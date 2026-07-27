"""The fact-layer invariant, the consent gate, and the disclaimer placeholders.

These cover the promises that are not about egress: that the dial cannot change
the facts, that the tool will not run before its disclaimer is accepted, and
that placeholder legal text cannot ship silently.
"""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError

import pytest

from jobmonger import constants, consent, dial, facts, friction
from jobmonger.facts import Fact, FactSet
from jobmonger.intake import IntakeError, from_text
from jobmonger.redaction import Review, Role, detect, seal


def _facts() -> FactSet:
    return FactSet(
        facts=(
            Fact("The request was declined by the scheduling committee.", "declined by the scheduling committee", "stated"),
            Fact("An appeal window may apply.", "appeals must be lodged within ten working days", "implied"),
        ),
        gaps=("Whether the decision can be escalated beyond [HR_REP].",),
        source_name="letter.txt",
    )


# -- the fact layer cannot be changed by the dial ---------------------------


def test_a_fact_set_is_immutable():
    fact_set = _facts()
    with pytest.raises(FrozenInstanceError):
        fact_set.facts = ()  # type: ignore[misc]


def test_individual_facts_are_immutable():
    fact = _facts().facts[0]
    with pytest.raises(FrozenInstanceError):
        fact.statement = "something else"  # type: ignore[misc]


def test_every_dial_position_renders_identical_facts():
    """The core invariant. One rendering, reused verbatim at all five settings."""
    fact_set = _facts()
    renderings = {position: fact_set.render() for position, _ in dial.positions()}
    assert len(set(renderings.values())) == 1


def test_the_rendered_facts_carry_certainty_markers():
    """An 'implied' fact must not read as settled at any position."""
    rendered = _facts().render()
    assert "(implied)" in rendered


def test_gaps_are_rendered_alongside_facts():
    """What the document does not say is part of the fixed record, not framing."""
    assert "WHAT THE DOCUMENT DOES NOT ADDRESS" in _facts().render()


def test_the_dial_module_cannot_reach_document_text():
    """Structural, not prompted: dial.render() takes facts, never a Document."""
    import inspect

    signature = inspect.signature(dial.render)
    assert "fact_set" in signature.parameters
    assert "document" not in signature.parameters

    source = inspect.getsource(dial)
    assert ".text" not in source.replace("sealed.text", ""), (
        "dial.py must not read document text directly"
    )


def test_every_position_instruction_carries_the_invariant_rule():
    from jobmonger import bridge
    from jobmonger.prompts import Task

    for position, _ in dial.positions():
        for has_question in (True, False):
            instruction = bridge.directive(
                Task.DIAL_READING, posture=position, has_question=has_question
            ).instruction
            assert "The facts above are fixed" in instruction
            assert "Do not drop a fact that cuts against the reader" in instruction


def test_positions_are_clamped_rather_than_rejected():
    assert dial.clamp(-5) == dial.MIN_POSITION
    assert dial.clamp(99) == dial.MAX_POSITION


def test_a_reading_always_carries_its_position():
    """A framing detached from its setting is the one output never allowed."""
    reading = dial.Reading(position=4, text="…", fact_count=2)
    assert reading.position_label == dial.label(4)


# -- consent gate -----------------------------------------------------------


def test_the_tool_refuses_to_read_a_document_before_consent():
    with pytest.raises(consent.ConsentRequired):
        from_text("Some text", "pasted")


def test_consent_permits_reading(granted):
    document = from_text("Some text about a schedule.", "pasted")
    assert document.text


def test_consent_is_bound_to_the_exact_disclaimer_text(granted, monkeypatch):
    """Changed wording re-opens the gate. Consent to unread text is not consent."""
    assert consent.is_granted()
    monkeypatch.setattr(constants, "LONG_DISCLAIMER", "A materially different disclaimer.")
    assert not consent.is_granted()


def test_a_corrupt_consent_record_fails_closed(granted):
    from jobmonger import paths

    paths.consent_file().write_text("{ not json", encoding="utf-8")
    assert not consent.is_granted()
    with pytest.raises(consent.ConsentRequired):
        consent.require()


def test_revoking_consent_reopens_the_gate(granted):
    assert consent.is_granted()
    consent.revoke()
    assert not consent.is_granted()


def test_consent_records_whether_the_disclaimer_was_a_placeholder(granted):
    """Now False — the long disclaimer carries its final verbatim wording."""
    from jobmonger import paths

    stored = json.loads(paths.consent_file().read_text(encoding="utf-8"))
    assert stored["disclaimer_was_placeholder"] is False


# -- disclaimers ------------------------------------------------------------


def test_nothing_is_a_placeholder_any_more():
    """All three constants carry their final owner-supplied wording.

    This was the tripwire that stopped unfinished legal text shipping quietly.
    It now guards the other direction: if anything here reverts to placeholder
    text, or new provisional text is added, this fails.
    """
    assert constants.all_placeholders() == [], (
        "A constant has reverted to placeholder text, or new provisional text "
        "was added without settling it. See DECISIONS.md item B1."
    )


def test_the_startup_notice_no_longer_fires():
    """The user-visible consequence of the line above."""
    assert not constants.all_placeholders()


# The two disclaimers are owner-supplied legal text, reproduced character for
# character. These checksums exist so that an accidental reflow, a smart-quote
# substitution by an editor, or an em dash swapped for an en dash fails the
# build instead of shipping.
#
# A failure here is NOT a licence to update the checksum. It means the text
# changed, and the only correct response is to restore it — or, if the owner
# genuinely supplied new wording, to replace the text and the checksum together
# in the same commit.
SHORT_DISCLAIMER_SHA256 = "18bc4f7e2859c7508554047783152e3bddaeff866f093f50b4c9b6a27d8b4ce4"
LONG_DISCLAIMER_SHA256 = "9737c6f9c134510e88d1a66d827dfa99c85f96826288a6603f3befe1fbf411c1"
# The guardrails checksum covers the original line breaks as well as the words,
# so re-wrapping the paragraphs fails this even though no word changed.
GUARDRAILS_SHA256 = "316f8277233ed9f224f94b2684ef0e7e5b1396731109775fb188b74a7f7c3ad4"


def _sha(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_the_short_disclaimer_is_verbatim():
    assert _sha(constants.SHORT_DISCLAIMER) == SHORT_DISCLAIMER_SHA256


def test_the_long_disclaimer_is_verbatim():
    assert _sha(constants.LONG_DISCLAIMER) == LONG_DISCLAIMER_SHA256


def test_the_guardrails_are_verbatim():
    assert _sha(constants.GUARDRAILS) == GUARDRAILS_SHA256


def test_the_guardrails_keep_their_original_line_breaks():
    """Explicit, because a formatter would re-wrap these without a second thought."""
    text = constants.GUARDRAILS
    assert text.startswith("GUARDRAILS\n\n")
    assert text.endswith("help with what you legitimately can.")
    assert text == text.strip(), "no leading or trailing whitespace"

    paragraphs = text.split("\n\n")
    assert len(paragraphs) == 8, f"expected 8 paragraphs, found {len(paragraphs)}"
    assert len(text.splitlines()) == 44, "the original hard wrapping was changed"

    # The PTO quotation spans a line break in the original. If someone re-wraps
    # the text, this exact span is the first thing that moves.
    assert '"Here\nis what your handbook says about PTO' in text


def test_the_guardrails_keep_their_punctuation_and_role_tokens():
    text = constants.GUARDRAILS

    assert text.count("—") == 8, "an em dash was lost or converted"
    for token in ("[MANAGER]", "[HR_REP]", "[COWORKER_1]"):
        assert token in text, f"role token {token} was altered"

    assert '"Here is the claim you can win" is not.' in text
    assert "the user's OWN information" in text
    assert "keep to the role's obligations" in text

    for curly in ("“", "”", "‘", "’"):
        assert curly not in text, f"a straight quote or apostrophe became {curly!r}"

    non_ascii = {ch for ch in text if ord(ch) > 127}
    assert non_ascii == {"—"}, f"unexpected non-ASCII characters: {sorted(non_ascii)}"


def test_the_specific_characters_the_owner_flagged_survived():
    """Named explicitly because these are exactly what editors silently 'fix'."""
    short, long = constants.SHORT_DISCLAIMER, constants.LONG_DISCLAIMER

    assert "options — it is not a substitute" in short, "em dash lost in the short form"
    assert "attorney–client relationship" in long, "en dash lost in attorney-client"
    assert "attorney—client" not in long, "en dash was replaced with an em dash"
    assert 'provided "as is," without warranty' in long, "straight quotes lost around as is"

    for curly in ("“", "”", "‘", "’"):
        assert curly not in short + long, f"a straight quote became {curly!r}"

    non_ascii = {ch for ch in short + long if ord(ch) > 127}
    assert non_ascii == {"–", "—"}, (
        f"unexpected non-ASCII characters: {sorted(non_ascii)}"
    )


def test_neither_disclaimer_carries_leading_or_trailing_whitespace():
    """A reflow would most likely show up here first."""
    for text in (constants.SHORT_DISCLAIMER, constants.LONG_DISCLAIMER):
        assert text == text.strip()
        assert "\n" not in text, "the verbatim text is a single paragraph; do not reflow it"


def test_the_disclaimers_are_single_sourced():
    """No output path may restate the text inline; drift is the failure mode."""
    import ast
    from pathlib import Path

    import jobmonger

    package = Path(jobmonger.__file__).parent
    fragments = (
        "not a substitute for a licensed attorney",
        "does not provide legal advice",
        "attorney–client relationship",
        'provided "as is,"',
    )
    offenders = [
        f"{source.name}: {fragment!r}"
        for source in package.rglob("*.py")
        if source.name != "constants.py"
        for fragment in fragments
        if fragment in source.read_text(encoding="utf-8")
    ]
    assert not offenders, f"disclaimer text restated outside constants.py: {offenders}"


def test_the_guardrails_state_every_boundary_from_the_scope():
    """Each of the scope doc's five legal guardrails is actually stated.

    Whitespace is normalised first: the guardrails keep their original hard
    wrapping, so several of these phrases span a line break in the source.
    """
    text = " ".join(constants.GUARDRAILS.split())

    assert "Understand and organize the user's OWN information" in text
    assert "Do not render a verdict on their legal position" in text
    assert "Never place, broker, recommend, or match" in text and "into a job" in text
    assert "Analyze roles, never people" in text
    assert "Do not speculate about the real person behind a token" in text
    assert "Never invent, alter, embellish, or manufacture records" in text


def test_the_guardrails_cover_the_dial_invariant_too():
    """The dial's honesty is stated to the model, not only enforced in code."""
    text = " ".join(constants.GUARDRAILS.split())
    assert "Change framing, never facts" in text
    assert "even at maximum advocacy never tell the user their position is stronger" in text


def test_the_guardrails_tell_the_model_what_to_do_when_asked_to_cross_a_line():
    text = " ".join(constants.GUARDRAILS.split())
    assert "decline plainly, say why, and then help with what you legitimately can" in text


# -- the guardrails reach every request -------------------------------------


def test_every_request_path_carries_the_guardrails():
    """Not "every path except the one we decided was harmless"."""
    from jobmonger import bridge

    assert bridge._guarded_system().startswith("GUARDRAILS")
    assert bridge._guarded_system("extra context").startswith("GUARDRAILS")
    assert "extra context" in bridge._guarded_system("extra context")


def test_no_request_builder_omits_the_system_prompt():
    """Static check across bridge.py, so a new request path cannot skip them."""
    import ast
    import inspect

    from jobmonger import bridge

    source = inspect.getsource(bridge)
    tree = ast.parse(source)

    builders = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and node.name in {"send", "stream", "check_reachable"}
    ]
    assert len(builders) == 3, "a request path was added or renamed — check it too"

    for node in builders:
        body = ast.unparse(node)
        # send/stream assemble via _build_body; check_reachable builds its own.
        assert "_guarded_system" in body or "_build_body" in body, (
            f"bridge.{node.name}() builds a request without the guardrails"
        )

    assert "_guarded_system" in ast.unparse(
        next(n for n in ast.walk(tree)
             if isinstance(n, ast.FunctionDef) and n.name == "_build_body")
    ), "_build_body must attach the guardrails"


# -- decision friction ------------------------------------------------------


def test_only_the_top_of_the_dial_triggers_friction():
    """Blanket friction is explicitly not the design — see DECISIONS.md P9."""
    assert dial.is_consequential(4)
    for position in (0, 1, 2, 3):
        assert not dial.is_consequential(position)


def test_the_restatement_anchors_on_a_real_fact_from_this_document():
    """A generic caution is wallpaper; one quoting their own document is not."""
    restatement = friction.for_max_advocacy(_facts())
    assert "An appeal window may apply." in restatement.anchor
    assert "implied" in restatement.anchor


def test_the_restatement_falls_back_to_a_gap_when_every_fact_is_settled():
    settled = FactSet(
        facts=(Fact("The request was declined.", "declined", "stated"),),
        gaps=("Whether it can be escalated.",),
        source_name="letter.txt",
    )
    assert "Whether it can be escalated." in friction.for_max_advocacy(settled).anchor


def test_declining_the_restatement_stops_the_action():
    restatement = friction.for_max_advocacy(_facts())
    assert friction.confirm(restatement, accepted=False) is False
    assert friction.confirm(restatement, accepted=True) is True


# -- the log ----------------------------------------------------------------


def test_the_log_never_records_document_content(granted, document):
    from jobmonger import log, paths

    review = Review(document, detect(document))
    for index, candidate in enumerate(review.detections):
        if candidate.kind is not None:
            review.confirm(index, Role.COWORKER)
    seal(document, review)

    written = paths.log_file().read_text(encoding="utf-8")
    assert "Sarah Chen" not in written
    assert "Priya Raman" not in written
    assert "p.raman@northgate-logistics.example" not in written


def test_the_log_never_records_a_key(granted):
    from jobmonger import config, paths

    config.save(config.Config(), api_key="CANARY-not-a-real-key-must-not-be-logged")
    written = paths.log_file().read_text(encoding="utf-8")
    assert "CANARY-not-a-real-key-must-not-be-logged" not in written


def test_the_log_scrubs_forbidden_keys_whatever_the_caller_passes(granted):
    from jobmonger import log, paths

    log.record("test.entry", text="Sarah Chen said so", api_key="sk-leak", count=3)
    written = paths.log_file().read_text(encoding="utf-8")
    assert "Sarah Chen" not in written
    assert "sk-leak" not in written
    assert '"count": 3' in written
