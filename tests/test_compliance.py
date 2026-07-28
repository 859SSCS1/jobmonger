"""[COMPLIANCE] — the four constraints, and the no-verdict line held twice.

The line matters more here than anywhere else in the tool, so it is tested from
both sides: the instruction says not to cross it, and the output has nowhere to
put a crossing even if the model tried.
"""

from __future__ import annotations

import json

import pytest

from jobmonger import bridge, compliance, constants
from jobmonger.bridge import Directive
from jobmonger.compliance import ComplianceReading, Requirement, Silence
from jobmonger.prompts import Task
from jobmonger.redaction import (
    Review,
    Role,
    SealedText,
    UnscreenedName,
    detect,
    seal,
)

FACTS_TEXT = "ESTABLISHED FACTS\n\n- Appeals must be lodged within ten working days."

REPLY = {
    "requirements": [
        {"requirement": "Lodge an appeal in writing.", "applies_to": "you",
         "deadline": "within ten working days of the decision",
         "quote": "appeals must be lodged within ten working days", "certainty": "stated"},
        {"requirement": "Acknowledge a lodged appeal.", "applies_to": "organisation",
         "deadline": "", "quote": "any correspondence", "certainty": "implied"},
        {"requirement": "Keep a record of the exchange.", "applies_to": "both",
         "deadline": "", "quote": "Please quote it in any correspondence", "certainty": "implied"},
    ],
    "silences": [
        {"topic": "What happens if the deadline falls on a public holiday.",
         "why_it_matters": "A reader counting days needs to know whether these are business days."},
    ],
}


@pytest.fixture
def prepared(document, granted):
    review = Review(document, detect(document))
    roles = {"Sarah Chen": Role.MANAGER, "Priya Raman": Role.HR_REP}
    for index, candidate in enumerate(review.detections):
        if candidate.surface in roles:
            review.confirm_all_matching(index, roles[candidate.surface])
    review.absorb_partials()
    for index in list(review.pending()):
        review.reject(index)
    return seal(document, review), review


@pytest.fixture
def stub(monkeypatch):
    seen = {}

    class _Reply:
        def __init__(self, text): self.text, self.model = text, "stub"

    def fake_send(payload, spec, **kwargs):
        seen["payload"] = payload
        seen["spec"] = spec
        seen.update(kwargs)
        return _Reply(json.dumps(REPLY))

    monkeypatch.setattr(compliance, "send", fake_send)
    return seen


# -- the four constraints ---------------------------------------------------


def test_it_uses_the_sealed_payload(prepared, stub):
    sealed, review = prepared
    compliance.read(sealed, review, FACTS_TEXT)
    assert type(stub["payload"]) is SealedText


def test_it_opens_no_new_api_path():
    import inspect

    source = inspect.getsource(compliance)
    assert "from .bridge import" in source
    for forbidden in ("urlopen", "urllib", "http.client", "socket", "requests"):
        assert forbidden not in source, f"compliance.py must not reference {forbidden}"


def test_it_sends_a_minted_directive_not_text(prepared, stub):
    sealed, review = prepared
    compliance.read(sealed, review, FACTS_TEXT)
    assert type(stub["spec"]) is Directive
    assert stub["spec"].task is Task.COMPLIANCE


def test_the_user_facing_render_carries_the_shared_disclaimer(prepared, stub):
    sealed, review = prepared
    reading = compliance.read(sealed, review, FACTS_TEXT)
    assert reading.render_for_user().endswith(constants.SHORT_DISCLAIMER)


def test_the_disclaimer_is_not_restated_in_this_module():
    import inspect

    source = inspect.getsource(compliance)
    assert "with_short_disclaimer" in source
    assert "not a law firm" not in source


def test_the_payload_carries_no_real_names(prepared, stub):
    sealed, review = prepared
    compliance.read(sealed, review, FACTS_TEXT)
    for name in ("Sarah", "Chen", "Priya", "Raman", "Marcus", "Okafor"):
        assert name not in stub["payload"].text


# -- free-text questions go through the stricter screening -----------------


def test_a_question_naming_an_unreviewed_person_is_refused(prepared, stub):
    sealed, review = prepared
    with pytest.raises(UnscreenedName, match="Devika"):
        compliance.read(sealed, review, FACTS_TEXT, question="Devika says I missed the window.")
    assert "payload" not in stub, "nothing should have been sent"


def test_a_question_naming_a_confirmed_person_is_substituted(prepared, stub):
    sealed, review = prepared
    compliance.read(sealed, review, FACTS_TEXT,
                    question="What does Sarah Chen have to do about my appeal?")
    body = stub["payload"].text
    assert "Sarah Chen" not in body
    assert "[MANAGER]" in body
    assert "THE READER ASKED" in body


def test_a_question_travels_inside_the_payload_not_beside_it(prepared, stub):
    sealed, review = prepared
    compliance.read(sealed, review, FACTS_TEXT, question="How long do I have to appeal?")
    assert "How long do I have to appeal?" in stub["payload"].text
    assert "How long do I have to appeal?" not in stub["spec"].instruction


# -- the no-verdict line, held in the instruction ---------------------------


def test_the_instruction_forbids_judging_compliance(prepared, stub):
    instruction = " ".join(bridge.directive(Task.COMPLIANCE).instruction.split())
    assert "Never say whether anyone has met it" in instruction
    assert "breached, missed, satisfied, or complied with" in instruction


def test_the_instruction_forbids_assessing_the_legal_position(prepared, stub):
    instruction = " ".join(bridge.directive(Task.COMPLIANCE).instruction.split())
    assert "Do not assess the reader's legal position" in instruction
    assert "predict how any dispute would go" in instruction


def test_the_instruction_forbids_strategy_advice(prepared, stub):
    instruction = " ".join(bridge.directive(Task.COMPLIANCE).instruction.split())
    assert "Do not advise on strategy" in instruction


def test_the_instruction_forbids_filling_a_silence(prepared, stub):
    """The most damaging failure available to this module."""
    instruction = " ".join(bridge.directive(Task.COMPLIANCE).instruction.split())
    assert "Do not fill the gap with what such documents usually say" in instruction
    assert "a rule that does not exist is the most damaging thing" in instruction


# -- the no-verdict line, held in the shape of the output -------------------


def test_the_output_has_nowhere_to_record_a_breach():
    """Structural half of the guard: a drifting model has no field to drift into."""
    fields = set(Requirement.__dataclass_fields__)
    assert fields == {"requirement", "applies_to", "deadline", "quote", "certainty"}
    for forbidden in ("complied", "breached", "met", "violation", "assessment", "verdict"):
        assert not any(forbidden in f for f in fields)


def test_the_schema_offers_no_compliance_verdict_field():
    schema = bridge.directive(Task.COMPLIANCE).schema
    properties = schema["properties"]["requirements"]["items"]["properties"]
    assert set(properties) == {"requirement", "applies_to", "deadline", "quote", "certainty"}
    assert schema["properties"]["requirements"]["items"]["additionalProperties"] is False


def test_applies_to_is_a_closed_vocabulary():
    schema = bridge.directive(Task.COMPLIANCE).schema
    enum = schema["properties"]["requirements"]["items"]["properties"]["applies_to"]["enum"]
    assert enum == ["you", "organisation", "both"]


def test_an_unrecognised_applies_to_falls_back_rather_than_being_trusted(prepared, monkeypatch):
    odd = {"requirements": [{"requirement": "x", "applies_to": "the tribunal",
                             "deadline": "", "quote": "q", "certainty": "stated"}],
           "silences": []}

    class _Reply:
        text, model = json.dumps(odd), "stub"

    monkeypatch.setattr(compliance, "send", lambda *a, **k: _Reply())
    sealed, review = prepared
    reading = compliance.read(sealed, review, FACTS_TEXT)
    assert reading.requirements[0].applies_to == "both"


# -- requirements, silences, deadlines --------------------------------------


def test_requirements_are_split_by_who_they_bind(prepared, stub):
    sealed, review = prepared
    reading = compliance.read(sealed, review, FACTS_TEXT)
    assert len(reading.applying_to("you")) == 1
    assert len(reading.applying_to("organisation")) == 1
    assert len(reading.applying_to("both")) == 1


def test_deadlines_are_surfaced_separately(prepared, stub):
    """Timing is the most actionable thing a handbook contains."""
    sealed, review = prepared
    reading = compliance.read(sealed, review, FACTS_TEXT)
    assert len(reading.deadlines) == 1
    assert "ten working days" in reading.deadlines[0].deadline
    assert "time limit:" in reading.render()


def test_silences_are_first_class(prepared, stub):
    """A rule that does not exist is something the reader needs to know."""
    sealed, review = prepared
    reading = compliance.read(sealed, review, FACTS_TEXT)
    assert len(reading.silences) == 1
    assert "WHAT IT DOES NOT SAY" in reading.render()
    assert "public holiday" in reading.render()


def test_a_requirement_without_a_quote_is_dropped(prepared, monkeypatch):
    """An unquotable rule is the thing this module most needs not to invent."""
    unbacked = {"requirements": [
        {"requirement": "You must give three months' notice.", "applies_to": "you",
         "deadline": "", "quote": "", "certainty": "implied"}],
        "silences": []}

    class _Reply:
        text, model = json.dumps(unbacked), "stub"

    monkeypatch.setattr(compliance, "send", lambda *a, **k: _Reply())
    sealed, review = prepared
    reading = compliance.read(sealed, review, FACTS_TEXT)
    assert len(reading) == 0


def test_certainty_markers_survive_into_the_rendering(prepared, stub):
    sealed, review = prepared
    reading = compliance.read(sealed, review, FACTS_TEXT)
    assert "(implied)" in reading.render()


def test_an_empty_document_renders_without_inventing_anything(prepared, monkeypatch):
    class _Reply:
        text, model = json.dumps({"requirements": [], "silences": []}), "stub"

    monkeypatch.setattr(compliance, "send", lambda *a, **k: _Reply())
    sealed, review = prepared
    reading = compliance.read(sealed, review, FACTS_TEXT)
    assert "nothing could be established" in reading.render()


# -- immutability -----------------------------------------------------------


def test_a_reading_is_immutable():
    from dataclasses import FrozenInstanceError

    reading = ComplianceReading(requirements=(), silences=(), source_name="x")
    with pytest.raises(FrozenInstanceError):
        reading.requirements = ()  # type: ignore[misc]


def test_a_requirement_is_immutable():
    from dataclasses import FrozenInstanceError

    requirement = Requirement(requirement="x", applies_to="you", deadline="",
                              quote="q", certainty="stated")
    with pytest.raises(FrozenInstanceError):
        requirement.requirement = "y"  # type: ignore[misc]


def test_a_silence_is_immutable():
    from dataclasses import FrozenInstanceError

    silence = Silence(topic="x", why_it_matters="y")
    with pytest.raises(FrozenInstanceError):
        silence.topic = "z"  # type: ignore[misc]
