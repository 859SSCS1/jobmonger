"""[TENURE] — banding, the four routing constraints, and the inference boundary."""

from __future__ import annotations

import json

import pytest

from jobmonger import constants, tenure
from jobmonger.bridge import Directive
from jobmonger.redaction import (
    REID_TEAM_SIZE_THRESHOLD,
    Review,
    Role,
    SealedText,
    UnscreenedName,
    detect,
    seal,
)
from jobmonger.tenure import LONGEST_BAND, Observation, TenureInput, TenureMap, band_for

FACTS_TEXT = "ESTABLISHED FACTS\n\n- [MANAGER] declined the request."

REPLY = {
    "observations": [
        {"token": "[MANAGER]", "observation": "A role held this long likely knows which exceptions get approved.",
         "basis": "the tenure band", "certainty": "implied"},
        {"token": "[HR_REP]", "observation": "May be newer to the current escalation process.",
         "basis": "the tenure band", "certainty": "unclear"},
    ]
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

    monkeypatch.setattr(tenure, "send", fake_send)
    return seen


# -- banding: figures never leave ------------------------------------------


@pytest.mark.parametrize("years,expected", [
    (0, "under a year"), (0.5, "under a year"),
    (1, "one to two years"), (2.9, "one to two years"),
    (3, "three to five years"), (5.5, "three to five years"),
    (6, "six to ten years"), (10.9, "six to ten years"),
    (11, LONGEST_BAND), (30, LONGEST_BAND),
])
def test_years_map_to_bands(years, expected):
    assert band_for(years) == expected


def test_an_unknown_length_of_service_has_no_band():
    assert band_for(None) == ""


def test_a_negative_length_of_service_is_rejected():
    with pytest.raises(ValueError):
        band_for(-1)


def test_the_exact_figure_is_never_sent(prepared, stub):
    """DECISIONS.md P10 — a band reasons as well and identifies far less."""
    sealed, review = prepared
    tenure.observe(sealed, review, FACTS_TEXT, [TenureInput("[MANAGER]", years=14)])
    body = stub["payload"].text
    assert "14" not in body
    assert LONGEST_BAND in body


def test_the_exact_figure_is_never_logged(prepared, stub):
    from jobmonger import paths

    sealed, review = prepared
    tenure.observe(sealed, review, FACTS_TEXT, [TenureInput("[MANAGER]", years=14)])
    written = paths.log_file().read_text(encoding="utf-8")
    assert '"14"' not in written and ": 14" not in written
    assert LONGEST_BAND in written


def test_your_own_tenure_is_banded_like_everyone_elses(document, granted, stub):
    """Owner-settled: [SELF] gets the same treatment, for posture consistency.

    Falls out of PERSON_ROLES including SELF, which makes it easy to break by
    accident later — hence an explicit test rather than leaving it incidental.
    """
    review = Review(document, detect(document))
    for index, candidate in enumerate(review.detections):
        if candidate.surface == "Marcus Okafor":
            review.confirm_all_matching(index, Role.SELF)
    review.absorb_partials()
    for index in list(review.pending()):
        review.reject(index)
    sealed = seal(document, review)

    tenure.observe(sealed, review, FACTS_TEXT, [TenureInput("[SELF]", years=14)])
    body = stub["payload"].text
    assert "14" not in body, "your own service length is banded too"
    assert LONGEST_BAND in body


# -- the four routing constraints ------------------------------------------


def test_it_uses_the_sealed_payload(prepared, stub):
    sealed, review = prepared
    tenure.observe(sealed, review, FACTS_TEXT, [TenureInput("[MANAGER]", years=14)])
    assert type(stub["payload"]) is SealedText


def test_it_opens_no_new_api_path():
    import inspect

    source = inspect.getsource(tenure)
    assert "from .bridge import" in source
    for forbidden in ("urlopen", "urllib", "http.client", "socket", "requests"):
        assert forbidden not in source, f"tenure.py must not reference {forbidden}"


def test_it_sends_a_minted_directive_not_text(prepared, stub):
    sealed, review = prepared
    tenure.observe(sealed, review, FACTS_TEXT, [TenureInput("[MANAGER]", years=14)])
    assert type(stub["spec"]) is Directive
    assert stub["spec"].task.value == "tenure"


def test_the_user_facing_render_carries_the_shared_disclaimer(prepared, stub):
    sealed, review = prepared
    result = tenure.observe(sealed, review, FACTS_TEXT, [TenureInput("[MANAGER]", years=14)])
    assert result.render_for_user().endswith(constants.SHORT_DISCLAIMER)


def test_the_disclaimer_is_not_restated_in_this_module():
    import inspect

    source = inspect.getsource(tenure)
    assert "with_short_disclaimer" in source
    assert "not a law firm" not in source


def test_the_payload_carries_no_real_names(prepared, stub):
    sealed, review = prepared
    tenure.observe(sealed, review, FACTS_TEXT, [TenureInput("[MANAGER]", years=14)])
    for name in ("Sarah", "Chen", "Priya", "Raman", "Marcus", "Okafor"):
        assert name not in stub["payload"].text


# -- free-text notes go through screening ----------------------------------


def test_a_note_naming_an_unreviewed_person_is_refused(prepared, stub):
    sealed, review = prepared
    with pytest.raises(UnscreenedName, match="Devika"):
        tenure.observe(sealed, review, FACTS_TEXT,
                       [TenureInput("[MANAGER]", years=14, note="Devika said so.")])
    assert "payload" not in stub, "nothing should have been sent"


def test_a_note_naming_a_confirmed_person_is_substituted(prepared, stub):
    sealed, review = prepared
    tenure.observe(sealed, review, FACTS_TEXT,
                   [TenureInput("[MANAGER]", years=14, note="Sarah Chen has been here longest.")])
    body = stub["payload"].text
    assert "Sarah Chen" not in body
    assert "[MANAGER]" in body


# -- boundaries -------------------------------------------------------------


def test_nothing_is_sent_when_no_tenure_is_supplied(prepared, stub):
    sealed, review = prepared
    result = tenure.observe(sealed, review, FACTS_TEXT, [])
    assert len(result) == 0
    assert "payload" not in stub


def test_tenure_for_an_unconfirmed_role_is_ignored(prepared, stub):
    sealed, review = prepared
    result = tenure.observe(sealed, review, FACTS_TEXT,
                            [TenureInput("[EXECUTIVE]", years=20)])
    assert len(result) == 0
    assert "payload" not in stub


def test_an_observation_about_an_unconfirmed_role_is_dropped(prepared, monkeypatch):
    sealed, review = prepared
    invented = {"observations": REPLY["observations"] + [
        {"token": "[EXECUTIVE]", "observation": "Untouchable.", "basis": "x", "certainty": "implied"}]}

    class _Reply:
        text, model = json.dumps(invented), "stub"

    monkeypatch.setattr(tenure, "send", lambda *a, **k: _Reply())
    result = tenure.observe(sealed, review, FACTS_TEXT, [TenureInput("[MANAGER]", years=14)])
    assert "[EXECUTIVE]" not in {o.token for o in result.observations}


def test_the_instruction_marks_this_as_inference(prepared, stub):
    from jobmonger import bridge
    from jobmonger.prompts import Task

    instruction = " ".join(bridge.directive(Task.TENURE).instruction.split())
    assert "This is inference, not fact" in instruction
    assert 'Say "likely", "often", "may" — and mean it' in instruction


def test_the_instruction_forbids_reasoning_about_the_individual(prepared, stub):
    from jobmonger import bridge
    from jobmonger.prompts import Task

    instruction = " ".join(bridge.directive(Task.TENURE).instruction.split())
    assert "never about the individual" in instruction
    assert "Do not speculate about character, motives, loyalty, or intentions" in instruction


def test_the_instruction_forbids_reassuring_or_alarming(prepared, stub):
    from jobmonger import bridge
    from jobmonger.prompts import Task

    instruction = " ".join(bridge.directive(Task.TENURE).instruction.split())
    assert "Do not reassure and do not alarm" in instruction


def test_the_rendering_says_it_is_inference(prepared, stub):
    sealed, review = prepared
    result = tenure.observe(sealed, review, FACTS_TEXT, [TenureInput("[MANAGER]", years=14)])
    assert "inference from length of service, not fact" in result.render()


def test_certainty_markers_survive_into_the_rendering(prepared, stub):
    sealed, review = prepared
    result = tenure.observe(sealed, review, FACTS_TEXT, [TenureInput("[MANAGER]", years=14)])
    assert "(implied)" in result.render()


# -- re-identification: tenure sharpens the small-team case ----------------


def test_tenure_on_a_small_team_raises_the_reidentification_note(prepared, stub):
    sealed, review = prepared
    for entity in review.entities.values():
        entity.team_size = 3
    result = tenure.observe(sealed, review, FACTS_TEXT, [TenureInput("[MANAGER]", years=14)])
    assert result.reidentification_notes
    assert "team of 3" in result.reidentification_notes[0]
    assert "Note:" in result.render_for_user()


def test_a_large_team_raises_no_note(prepared, stub):
    sealed, review = prepared
    for entity in review.entities.values():
        entity.team_size = REID_TEAM_SIZE_THRESHOLD + 1
    result = tenure.observe(sealed, review, FACTS_TEXT, [TenureInput("[MANAGER]", years=14)])
    assert result.reidentification_notes == ()


def test_the_note_warns_and_never_blocks(prepared, stub):
    sealed, review = prepared
    for entity in review.entities.values():
        entity.team_size = 2
    result = tenure.observe(sealed, review, FACTS_TEXT, [TenureInput("[MANAGER]", years=14)])
    assert result.reidentification_notes
    assert len(result) == 2, "the analysis still ran in full"


# -- no cross-user learning -------------------------------------------------


def test_nothing_is_persisted_between_runs(prepared, stub):
    """There is no corpus. Each run sees only the document in front of it."""
    import inspect

    source = inspect.getsource(tenure)
    for forbidden in ("open(", "Path(", "pickle", "sqlite3", "shelve"):
        assert forbidden not in source, f"tenure.py must not persist anything ({forbidden})"


def test_a_tenure_map_is_immutable():
    from dataclasses import FrozenInstanceError

    result = TenureMap(observations=(), bands=(), source_name="x")
    with pytest.raises(FrozenInstanceError):
        result.observations = ()  # type: ignore[misc]


def test_an_observation_is_immutable():
    observation = Observation(token="[MANAGER]", observation="x", basis="y", certainty="implied")
    from dataclasses import FrozenInstanceError

    with pytest.raises(FrozenInstanceError):
        observation.observation = "z"  # type: ignore[misc]
