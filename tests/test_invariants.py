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
    for position, _ in dial.positions():
        instruction = dial._instruction(position, "")
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


def test_placeholders_are_marked():
    """The tripwire that stops unfinished legal text shipping quietly.

    Both user-facing disclaimers now carry their final verbatim wording. Only
    GUARDRAILS — the model-facing boundary text — is still outstanding. When its
    wording arrives, clear the marker and change this to expect an empty list.
    See DECISIONS.md item B1.
    """
    assert constants.all_placeholders() == ["GUARDRAILS"], (
        "Placeholder state changed. If GUARDRAILS wording is now in place, clear "
        "the PLACEHOLDER_MARK and update this test and DECISIONS.md item B1."
    )


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


def _sha(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_the_short_disclaimer_is_verbatim():
    assert _sha(constants.SHORT_DISCLAIMER) == SHORT_DISCLAIMER_SHA256


def test_the_long_disclaimer_is_verbatim():
    assert _sha(constants.LONG_DISCLAIMER) == LONG_DISCLAIMER_SHA256


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
    text = constants.GUARDRAILS.lower()
    assert "legal verdict" in text
    assert "hiring" in text or "placement" in text
    assert "profile of any named individual" in text
    assert "fabricate" in text


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
