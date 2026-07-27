"""[ROLE-MAP] — duties by direction, the re-identification flag, and the boundaries.

No test here reaches the network. ``rolemap.send`` is replaced with a stub that
records what it was handed, which is also how the routing assertions work: the
payload the stub receives is the payload the bridge would have received.
"""

from __future__ import annotations

import json

import pytest

from jobmonger import constants, rolemap
from jobmonger.redaction import (
    REID_TEAM_SIZE_THRESHOLD,
    Review,
    Role,
    SealedText,
    detect,
    seal,
)
from jobmonger.rolemap import Duty, RoleAnalysis, RoleMap

FACTS_TEXT = "ESTABLISHED FACTS\n\n- [MANAGER] declined the request."

REPLY = {
    "roles": [
        {
            "token": "[MANAGER]",
            "duties": [
                {"direction": "to_company", "duty": "Keep the schedule staffed.",
                 "quote": "the scheduling committee", "certainty": "stated"},
                {"direction": "for_user", "duty": "Give a reason for a decision.",
                 "quote": "I am confirming", "certainty": "implied"},
                {"direction": "against_user", "duty": "Document repeated requests.",
                 "quote": "any correspondence", "certainty": "implied"},
            ],
        },
        {
            "token": "[HR_REP]",
            "duties": [
                {"direction": "for_user", "duty": "Receive an appeal.",
                 "quote": "You may raise this", "certainty": "stated"},
            ],
        },
    ]
}


@pytest.fixture
def prepared(document, granted):
    """A sealed document with a manager and an HR contact confirmed."""
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
    """Replace the bridge call, recording everything it was handed."""
    seen = {}

    def fake_send(payload, instruction, **kwargs):
        seen["payload"] = payload
        seen["instruction"] = instruction
        seen.update(kwargs)
        return rolemap.send.__self__ if False else _Reply(json.dumps(REPLY))

    class _Reply:
        def __init__(self, text): self.text, self.model = text, "stub"

    monkeypatch.setattr(rolemap, "send", fake_send)
    return seen


# -- routing: no new path to the API ---------------------------------------


def test_the_payload_is_a_sealed_object(prepared, stub):
    sealed, review = prepared
    rolemap.extract(sealed, review, FACTS_TEXT)
    assert type(stub["payload"]) is SealedText


def test_the_payload_carries_no_real_names(prepared, stub):
    sealed, review = prepared
    rolemap.extract(sealed, review, FACTS_TEXT)
    text = stub["payload"].text
    for name in ("Sarah", "Chen", "Priya", "Raman", "Marcus", "Okafor"):
        assert name not in text, f"{name} reached the request"


def test_it_goes_through_the_one_egress_function(prepared, stub):
    """rolemap must call bridge.send, not build its own request."""
    import inspect

    source = inspect.getsource(rolemap)
    assert "from .bridge import" in source
    for forbidden in ("urlopen", "urllib", "http.client", "socket", "requests"):
        assert forbidden not in source, f"rolemap.py must not reference {forbidden}"


def test_team_sizes_are_never_sent(prepared, stub):
    """Headcount is used locally to warn. It is itself an identifying detail."""
    sealed, review = prepared
    for entity in review.entities.values():
        entity.team_size = 3
    rolemap.extract(sealed, review, FACTS_TEXT)
    assert "team" not in stub["payload"].text.lower()
    assert "3" not in stub["payload"].text


# -- the three directions ---------------------------------------------------


def test_duties_are_split_by_direction(prepared, stub):
    sealed, review = prepared
    result = rolemap.extract(sealed, review, FACTS_TEXT)
    manager = next(r for r in result.roles if r.token == "[MANAGER]")
    assert len(manager.in_direction("to_company")) == 1
    assert len(manager.in_direction("for_user")) == 1
    assert len(manager.in_direction("against_user")) == 1


def test_the_against_user_column_is_kept_not_softened(prepared, stub):
    """The column that earns its keep. A user blindsided by it is worse off."""
    sealed, review = prepared
    result = rolemap.extract(sealed, review, FACTS_TEXT)
    manager = next(r for r in result.roles if r.token == "[MANAGER]")
    against = manager.in_direction("against_user")
    assert against and against[0].duty == "Document repeated requests."
    assert "Works against you" in result.render()


def test_the_instruction_frames_against_user_as_duty_not_malice(prepared, stub):
    sealed, review = prepared
    rolemap.extract(sealed, review, FACTS_TEXT)
    # Whitespace normalised: the instruction is hard-wrapped, so several of
    # these phrases span a line break in the source.
    instruction = " ".join(stub["instruction"].split())
    assert "It does not mean hostility" in instruction
    assert "do not dress them up as malice either" in instruction


def test_certainty_markers_survive_into_the_rendering(prepared, stub):
    sealed, review = prepared
    result = rolemap.extract(sealed, review, FACTS_TEXT)
    assert "(implied)" in result.render()


# -- boundaries -------------------------------------------------------------


def test_a_role_the_user_never_confirmed_is_dropped(prepared, stub, monkeypatch):
    """A duty attached to an invented label is not something anyone can act on."""
    sealed, review = prepared
    invented = {"roles": REPLY["roles"] + [{"token": "[EXECUTIVE]", "duties": [
        {"direction": "against_user", "duty": "Approve terminations.",
         "quote": "x", "certainty": "implied"}]}]}

    class _Reply:
        text, model = json.dumps(invented), "stub"

    monkeypatch.setattr(rolemap, "send", lambda *a, **k: _Reply())
    result = rolemap.extract(sealed, review, FACTS_TEXT)
    assert "[EXECUTIVE]" not in {r.token for r in result.roles}


def test_the_instruction_forbids_characterising_individuals(prepared, stub):
    sealed, review = prepared
    rolemap.extract(sealed, review, FACTS_TEXT)
    instruction = " ".join(stub["instruction"].split())
    assert "Describe the role, never the individual" in instruction
    assert "Do not speculate about the character, feelings, motives" in instruction


def test_the_instruction_forbids_declaring_a_breach(prepared, stub):
    """What a role must do is in scope. Whether someone failed to is a verdict."""
    sealed, review = prepared
    rolemap.extract(sealed, review, FACTS_TEXT)
    normalised = " ".join(stub["instruction"].split())
    assert "Do not state or imply that any duty was breached" in normalised


def test_nothing_is_requested_when_no_person_roles_are_confirmed(document, granted, stub):
    review = Review(document, detect(document))
    for index in list(review.pending()):
        review.reject(index)
    sealed = seal(document, review)
    result = rolemap.extract(sealed, review, FACTS_TEXT)
    assert len(result) == 0
    assert "payload" not in stub, "no request should have been made"


# -- re-identification flag (threshold settled at 8) ------------------------


def test_the_threshold_is_the_owner_supplied_value():
    assert REID_TEAM_SIZE_THRESHOLD == 8


@pytest.mark.parametrize("size,flagged", [(1, True), (5, True), (8, True), (9, False), (40, False)])
def test_the_flag_fires_at_or_below_the_threshold(size, flagged):
    role = RoleAnalysis(token="[MANAGER]", duties=(), team_size=size)
    assert role.reidentifiable is flagged


def test_an_unknown_team_size_does_not_flag():
    assert RoleAnalysis(token="[MANAGER]", duties=(), team_size=None).reidentifiable is False


def test_the_note_names_the_role_and_the_headcount():
    role = RoleAnalysis(token="[HR_REP]", duties=(), team_size=3)
    assert "[HR_REP]" in role.reidentification_note
    assert "team of 3" in role.reidentification_note


def test_the_flag_warns_and_never_blocks(prepared, stub):
    """Settled as warn-only in P4. The document is the user's."""
    sealed, review = prepared
    for entity in review.entities.values():
        entity.team_size = 2
    result = rolemap.extract(sealed, review, FACTS_TEXT)
    assert result.reidentification_notes
    assert len(result) == 2, "the analysis still ran in full"
    assert "Note:" in result.render()


# -- immutability and disclaimer -------------------------------------------


def test_a_role_map_is_immutable():
    from dataclasses import FrozenInstanceError

    result = RoleMap(roles=(), source_name="x")
    with pytest.raises(FrozenInstanceError):
        result.roles = ()  # type: ignore[misc]


def test_a_duty_is_immutable():
    from dataclasses import FrozenInstanceError

    duty = Duty(direction="for_user", duty="x", quote="y", certainty="stated")
    with pytest.raises(FrozenInstanceError):
        duty.duty = "z"  # type: ignore[misc]


def test_the_user_facing_render_carries_the_shared_disclaimer(prepared, stub):
    sealed, review = prepared
    result = rolemap.extract(sealed, review, FACTS_TEXT)
    rendered = result.render_for_user()
    assert constants.SHORT_DISCLAIMER in rendered
    assert rendered.endswith(constants.SHORT_DISCLAIMER)


def test_the_disclaimer_comes_from_the_shared_constant_not_a_copy():
    import inspect

    source = inspect.getsource(rolemap)
    assert "with_short_disclaimer" in source
    assert "not a law firm" not in source, "the disclaimer must not be restated here"
