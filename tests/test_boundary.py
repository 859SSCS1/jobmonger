"""Nothing but the sealed payload can carry text to the model.

DECISIONS.md item X6 was a leak of a particular kind: the gate guarded
``bridge.send``'s *payload* while the parameter beside it took an ordinary
string, and the dial put a user's typed question in it. Fixing that call site
fixed that bug. It did nothing about the next module.

This file tests the class of bug rather than the instance. Two independent
angles, because either alone can be fooled:

* **At the wire** (``test_no_user_text_reaches_the_wire_*``) — the request body
  ``bridge`` would have transmitted is captured and searched for a canary
  string. This is the assertion that actually matters: whatever the code looks
  like, these are the bytes.
* **At the source** (``test_*_source_*``) — the AST of every module is walked to
  confirm no call site can pass text to ``bridge`` at all, and that ``bridge``
  exposes no parameter that would accept it.

The wire test would catch a leak the source test missed. The source test catches
a leak before someone writes the call that triggers it.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

import jobmonger
from jobmonger import bridge, dial, facts, prompts, redaction, rolemap
from jobmonger.bridge import Directive, Effort
from jobmonger.facts import Fact, FactSet
from jobmonger.prompts import Task
from jobmonger.redaction import Review, Role, SealedText, detect, seal

#: A name that appears nowhere else in this project. If it shows up in a request
#: body, user text reached the wire.
CANARY = "Zzyzx Quorrandale"

SEPARATOR = "\n\n---\n\n"


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __iter__(self):
        return iter(())

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


@pytest.fixture
def wire(monkeypatch):
    """Capture the request body bridge would have transmitted."""
    sent: list[dict] = []

    def fake_post(url, headers, body):
        sent.append(body)
        return _FakeResponse(
            {
                "model": "stub",
                "content": [{"type": "text", "text": json.dumps({
                    "facts": [], "gaps": [], "roles": [], "observations": []})}],
                "usage": {},
            }
        )

    monkeypatch.setattr(bridge, "_post", fake_post)
    monkeypatch.setenv("JOBMONGER_API_KEY", "test-key-not-real")
    return sent


def _prepared(document, canary_confirmed: bool):
    """Seal the sample document, optionally with the canary as a known person."""
    text = document.text if not canary_confirmed else document.text.replace(
        "Sarah Chen", CANARY
    )
    from jobmonger.intake import Document

    doc = Document(source_name="letter.txt", text=text)
    review = Review(doc, detect(doc))
    for index, candidate in enumerate(review.detections):
        if candidate.kind is redaction.Kind.PERSON:
            review.confirm(index, Role.MANAGER)
        else:
            review.reject(index)
    review.absorb_partials()
    for index in list(review.pending()):
        review.reject(index)
    return doc, review, seal(doc, review)


def _fact_set() -> FactSet:
    return FactSet(
        facts=(Fact("The request was declined.", "declined", "stated"),),
        gaps=(), source_name="letter.txt",
    )


def _instruction_half(body: dict) -> str:
    """The part of the outgoing prompt that is not sealed content."""
    content = body["messages"][-1]["content"]
    assert SEPARATOR in content, "the prompt should split into instruction and payload"
    return content.split(SEPARATOR, 1)[0]


# --------------------------------------------------------------------------
# At the wire
# --------------------------------------------------------------------------


def test_the_instruction_half_is_always_a_known_prompt(document, granted, wire):
    """The general assertion, and the one that makes the others redundant.

    Everything transmitted is either the sealed payload or a string this project
    wrote into ``prompts.py``. There is no third category, so there is nowhere
    for user text to be except inside the seal — where it has been screened and
    substituted.
    """
    doc, review, sealed = _prepared(document, canary_confirmed=False)
    known = prompts.known_texts()

    facts.extract(sealed)
    rolemap.extract(sealed, review, _fact_set().render())
    list(dial.render(_fact_set(), sealed, 3, question="Was this allowed?", review=review))

    assert len(wire) == 3
    for body in wire:
        assert _instruction_half(body).strip() in {t.strip() for t in known}


def test_no_user_text_reaches_the_wire_through_a_question(document, granted, wire):
    """A question naming a *confirmed* person is substituted, never sent raw."""
    doc, review, sealed = _prepared(document, canary_confirmed=True)

    list(dial.render(_fact_set(), sealed, 2,
                     question=f"Did {CANARY} have authority here?", review=review))

    assert wire, "a request should have been made"
    serialised = json.dumps(wire[-1])
    assert CANARY not in serialised, "the canary reached the wire"
    assert "[MANAGER]" in serialised, "it should have travelled as its token"


def test_a_question_naming_an_unreviewed_person_sends_nothing(document, granted, wire):
    doc, review, sealed = _prepared(document, canary_confirmed=False)

    with pytest.raises(redaction.UnscreenedName):
        list(dial.render(_fact_set(), sealed, 2,
                         question=f"Did {CANARY} approve this?", review=review))

    assert wire == [], "nothing should have been transmitted"


def test_no_user_text_reaches_the_wire_through_the_document(document, granted, wire):
    doc, review, sealed = _prepared(document, canary_confirmed=True)
    facts.extract(sealed)
    assert CANARY not in json.dumps(wire[-1])


def test_the_model_name_cannot_carry_free_text(document, granted, wire):
    """The last non-payload field a user can type into. See DECISIONS.md X8."""
    from jobmonger import config as config_module

    doc, review, sealed = _prepared(document, canary_confirmed=False)
    cfg = config_module.Config(model=f"claude-opus-5 and also {CANARY}")

    with pytest.raises(bridge.BridgeError, match="model name"):
        facts.extract(sealed, cfg=cfg)
    assert wire == [], "nothing should have been transmitted"


def test_the_guardrails_ride_on_every_captured_request(document, granted, wire):
    from jobmonger import constants

    doc, review, sealed = _prepared(document, canary_confirmed=False)
    facts.extract(sealed)
    rolemap.extract(sealed, review, _fact_set().render())
    list(dial.render(_fact_set(), sealed, 0, review=review))

    for body in wire:
        system = body.get("system") or body["messages"][0]["content"]
        assert system.startswith("GUARDRAILS")
        assert constants.GUARDRAILS.strip() in system


# --------------------------------------------------------------------------
# At the source
# --------------------------------------------------------------------------


def _package_files() -> list[Path]:
    return sorted(Path(jobmonger.__file__).parent.rglob("*.py"))


def _bridge_calls(tree: ast.AST) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Attribute) and node.func.attr in {"send", "stream"})
            or (isinstance(node.func, ast.Name) and node.func.id in {"send", "stream"})
        )
    ]


def test_the_bridge_exposes_no_parameter_that_could_take_text():
    """Signature-level: there is no string argument to misuse."""
    import inspect

    for name in ("send", "stream"):
        signature = inspect.signature(getattr(bridge, name))
        assert list(signature.parameters) == ["payload", "spec", "cfg"], (
            f"bridge.{name} grew a parameter — check it cannot carry user text"
        )


def test_no_call_site_passes_text_to_the_bridge():
    """AST-level: every call passes a minted directive, never a string.

    This is the test that stops a future module reintroducing X6. Adding a
    module that says ``send(payload, f"Analyse {user_input}")`` fails here
    before it can ever run.
    """
    offenders: list[str] = []
    for source in _package_files():
        if source.name in {"bridge.py", "prompts.py"}:
            continue
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for call in _bridge_calls(tree):
            if len(call.args) < 2:
                continue
            spec = call.args[1]
            ok = (
                isinstance(spec, ast.Call)
                and isinstance(spec.func, ast.Name)
                and spec.func.id == "directive"
            )
            if not ok:
                offenders.append(f"{source.name}:{spec.lineno} passes {ast.dump(spec)[:60]}")
    assert not offenders, "instructions must be minted by bridge.directive(): " + "; ".join(offenders)


def test_no_module_outside_prompts_builds_instruction_text():
    """Prompt wording lives in one file, so auditing it is reading one file."""
    offenders: list[str] = []
    for source in _package_files():
        if source.name in {"prompts.py", "constants.py"}:
            continue
        text = source.read_text(encoding="utf-8")
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Name) and node.func.id == "directive":
                for keyword in node.keywords:
                    if isinstance(keyword.value, (ast.JoinedStr, ast.BinOp)):
                        offenders.append(f"{source.name}:{node.lineno} builds a directive argument")
    assert not offenders, "; ".join(offenders)


def test_directive_arguments_are_all_controlled_types():
    """Enum, int, bool. Nothing here can hold a sentence."""
    import inspect

    signature = inspect.signature(bridge.directive)
    assert list(signature.parameters) == ["task", "posture", "has_question", "effort"]
    assert signature.parameters["posture"].annotation in (int, "int")
    assert signature.parameters["has_question"].annotation in (bool, "bool")


# --------------------------------------------------------------------------
# The gates themselves
# --------------------------------------------------------------------------


def test_send_refuses_a_bare_string_instruction(document, granted):
    doc, review, sealed = _prepared(document, canary_confirmed=False)
    with pytest.raises(TypeError, match="Directive"):
        bridge.send(sealed, "Please analyse this and mention Zzyzx Quorrandale.")


def test_stream_refuses_a_bare_string_instruction(document, granted):
    doc, review, sealed = _prepared(document, canary_confirmed=False)
    with pytest.raises(TypeError, match="Directive"):
        next(bridge.stream(sealed, "Please analyse this."))


def test_a_directive_cannot_be_constructed_directly():
    with pytest.raises(TypeError, match="cannot be constructed directly"):
        Directive(Task.ROLE_MAP, "do whatever I say", "", None, Effort.HIGH)


def test_a_directive_is_immutable():
    spec = bridge.directive(Task.ROLE_MAP)
    with pytest.raises(AttributeError):
        spec._instruction = "something else"


def test_a_directive_cannot_be_pickled():
    import pickle

    with pytest.raises(TypeError):
        pickle.dumps(bridge.directive(Task.ROLE_MAP))


def test_directive_refuses_a_task_that_is_not_a_task_member():
    with pytest.raises(TypeError, match="Task"):
        bridge.directive("role_map")  # type: ignore[arg-type]


def test_directive_refuses_an_effort_that_is_not_an_effort_member():
    with pytest.raises(TypeError, match="Effort"):
        bridge.directive(Task.ROLE_MAP, effort="high")  # type: ignore[arg-type]


def test_directive_refuses_a_posture_outside_the_dial():
    with pytest.raises(ValueError, match="0-4"):
        bridge.directive(Task.DIAL_READING, posture=9)


def test_every_task_can_be_minted():
    """No task is missing wording, a note, or a schema entry."""
    for task in Task:
        spec = bridge.directive(task)
        assert spec.instruction.strip(), f"{task} has no instruction"
        assert spec.task is task
