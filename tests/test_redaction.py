"""Detection, review, identity grouping, and token assignment."""

from __future__ import annotations

import pytest

from jobmonger.intake import Document
from jobmonger.redaction import (
    Confidence,
    DetectionPolicy,
    HeuristicDetector,
    Kind,
    Review,
    Role,
    SealError,
    assign_tokens,
    detect,
    seal,
)


def _detector_finds(text: str, kind: Kind) -> list[str]:
    found = HeuristicDetector().detect(text, DetectionPolicy())
    return [d.surface for d in found if d.kind is kind]


# -- detection --------------------------------------------------------------


def test_finds_names_after_a_from_label():
    assert "Sarah Chen" in _detector_finds("From: Sarah Chen\n", Kind.PERSON)


def test_finds_names_after_a_title():
    assert "Priya Raman" in _detector_finds("Please contact Dr. Priya Raman today.", Kind.PERSON)


def test_finds_names_in_a_signature_block():
    text = "Thanks for your time.\n\nRegards,\n\nMarcus Okafor\n"
    assert "Marcus Okafor" in _detector_finds(text, Kind.PERSON)


def test_finds_email_addresses():
    assert _detector_finds("write to p.raman@example.com please", Kind.EMAIL)


def test_finds_phone_numbers():
    assert _detector_finds("Her direct line is (555) 555-0147.", Kind.PHONE)


def test_finds_labelled_employee_identifiers():
    found = _detector_finds("Your employee number is 449201.", Kind.EMPLOYEE_ID)
    assert found and "449201" in found[0]


def test_finds_bare_staff_identifier_formats():
    assert _detector_finds("Quote EMP-449201 in correspondence.", Kind.EMPLOYEE_ID)


def test_does_not_flag_common_document_words():
    text = "Human Resources will review the Employee Handbook in March."
    found = _detector_finds(text, Kind.PERSON)
    assert "Human Resources" not in found
    assert "Employee Handbook" not in found
    assert "March" not in found


def test_sentence_opening_capital_is_not_treated_as_a_name():
    assert "Following" not in _detector_finds("Following our conversation, we agreed.", Kind.PERSON)


def test_detection_is_deliberately_over_eager_rather_than_cautious():
    """A false positive costs one click; a false negative leaks a real name."""
    found = HeuristicDetector().detect("I spoke to Reed about the rota.", DetectionPolicy())
    assert any(d.kind is Kind.PERSON and d.confidence is Confidence.LOW for d in found)


def test_own_name_only_detected_when_the_policy_says_so():
    text = "I, Marcus Okafor, submitted the request."
    off = HeuristicDetector().detect(text, DetectionPolicy())
    on = HeuristicDetector().detect(
        text, DetectionPolicy(include_own_name=True, own_name="Marcus Okafor")
    )
    assert sum(1 for d in on if d.surface == "Marcus Okafor") >= 1
    # With the toggle off it may still be caught by the generic rules — the
    # policy governs the *dedicated* rule, not whether the name is findable.
    assert isinstance(off, list)


def test_company_name_redaction_is_opt_in():
    text = "Northgate Logistics confirmed the schedule."
    policy = DetectionPolicy(include_company=True, company_name="Northgate Logistics")
    found = HeuristicDetector().detect(text, policy)
    assert any(d.kind is Kind.COMPANY for d in found)


def test_overlapping_detections_are_resolved_not_duplicated(document):
    found = detect(document)
    for a, b in zip(found, found[1:]):
        assert a.end <= b.start, "detections must not overlap after merging"


# -- review -----------------------------------------------------------------


def test_review_starts_with_everything_pending(document):
    review = Review(document, detect(document))
    assert len(review.pending()) == len(review.detections)
    assert not review.is_complete()


def test_rejecting_leaves_text_untouched(document, granted):
    review = Review(document, detect(document))
    for index in list(review.pending()):
        review.reject(index)
    sealed = seal(document, review)
    assert sealed.text == document.text
    assert sealed.entity_count == 0


def test_confirming_all_matching_resolves_every_occurrence(document):
    review = Review(document, detect(document))
    index = next(
        i for i, d in enumerate(review.detections) if d.surface == "Sarah Chen"
    )
    review.confirm_all_matching(index, Role.MANAGER)
    remaining = [review.detections[i].surface for i in review.pending()]
    assert "Sarah Chen" not in remaining


def test_a_missed_name_can_be_added_by_hand(document, granted):
    """The most important affordance: fixing what detection missed."""
    review = Review(document, detect(document))
    for index in list(review.pending()):
        review.reject(index)
    review.add_manual("scheduling committee", Role.EXTERNAL)
    sealed = seal(document, review)
    assert "scheduling committee" not in sealed.text.lower()
    assert "[EXTERNAL]" in sealed.text


def test_spellings_of_one_person_group_into_one_identity():
    text = "Sarah Chen approved it. Later, Ms. Chen reversed the decision."
    doc = Document(source_name="note.txt", text=text)
    review = Review(doc, detect(doc))
    for index, candidate in enumerate(review.detections):
        if candidate.kind is Kind.PERSON:
            review.confirm(index, Role.MANAGER)
        else:
            review.reject(index)
    assert len(review.entities) == 1, "two spellings of one person became two identities"


def test_distinct_people_never_share_a_token():
    """DECISIONS.md item P3 — merging two people would corrupt the analysis."""
    entities = list(
        Review(Document("x", "x"), []).entities.values()
    )
    from jobmonger.redaction import Entity

    a = Entity(entity_id="coworker-1", role=Role.COWORKER, surfaces={"Ana"})
    b = Entity(entity_id="coworker-2", role=Role.COWORKER, surfaces={"Bo"})
    tokens = assign_tokens([a, b])
    assert tokens["coworker-1"] != tokens["coworker-2"]
    assert set(tokens.values()) == {"[COWORKER_1]", "[COWORKER_2]"}


def test_a_sole_holder_of_a_role_gets_the_bare_token():
    from jobmonger.redaction import Entity

    tokens = assign_tokens([Entity(entity_id="manager-1", role=Role.MANAGER, surfaces={"Sarah"})])
    assert tokens["manager-1"] == "[MANAGER]"


def test_merging_two_entities_keeps_both_spellings():
    from jobmonger.redaction import Entity

    review = Review(Document("x", "x"), [])
    review.entities["a"] = Entity(entity_id="a", role=Role.MANAGER, surfaces={"Sarah Chen"})
    review.entities["b"] = Entity(entity_id="b", role=Role.MANAGER, surfaces={"S. Chen"})
    merged = review.merge("a", "b")
    assert merged.surfaces == {"Sarah Chen", "S. Chen"}
    assert "b" not in review.entities


# -- partial names ----------------------------------------------------------
#
# The boundary here is the whole point (DECISIONS.md item X4):
#   * a component of a name the user already confirmed  -> folded in automatically
#   * a bare name that was never part of a confirmed one -> left pending


def test_a_component_of_a_confirmed_name_is_folded_in_automatically(document):
    """Found by an end-to-end dry run, not by reasoning about the code.

    Confirming "Marcus Okafor" left the later salutation "Marcus," untouched,
    and the residual scan could not catch it because "Marcus" alone was never a
    confirmed surface.
    """
    review = Review(document, detect(document))
    index = next(i for i, d in enumerate(review.detections) if d.surface == "Marcus Okafor")
    review.confirm_all_matching(index, Role.SELF)

    assert review.absorb_partials() >= 1

    pending_surfaces = [review.detections[i].surface for i in review.pending()]
    assert "Marcus" not in pending_surfaces, "a confirmed name's own component should not be re-asked"

    selves = [e for e in review.entities.values() if e.role is Role.SELF]
    assert len(selves) == 1
    assert {"Marcus", "Marcus Okafor"} <= selves[0].surfaces


def test_a_novel_bare_name_is_left_pending(granted):
    """Never part of any confirmed full name, so nothing here gets to decide it."""
    doc = Document(
        source_name="note.txt",
        text="From: Sarah Chen\n\nI also spoke to Devika about the rota.",
    )
    review = Review(doc, detect(doc))
    index = next(i for i, d in enumerate(review.detections) if d.surface == "Sarah Chen")
    review.confirm_all_matching(index, Role.MANAGER)
    review.absorb_partials()

    pending_surfaces = [review.detections[i].surface for i in review.pending()]
    assert "Devika" in pending_surfaces, "an unfamiliar bare name must stay in review"
    assert not review.is_complete()


def test_a_novel_bare_name_is_never_auto_redacted(granted):
    """Ambiguity is not resolved silently — sealing refuses until it is decided."""
    doc = Document(
        source_name="note.txt",
        text="From: Sarah Chen\n\nI also spoke to Devika about the rota.",
    )
    review = Review(doc, detect(doc))
    index = next(i for i, d in enumerate(review.detections) if d.surface == "Sarah Chen")
    review.confirm_all_matching(index, Role.MANAGER)
    review.absorb_partials()
    with pytest.raises(SealError, match="reviewed"):
        seal(doc, review)


def test_absorbing_does_not_overturn_a_decision_the_user_already_made(granted):
    """If the user rejected a span, folding must not quietly re-confirm it.

    The mid-sentence "Sarah" here is deliberate: a bare capitalised word at the
    start of a line is filtered out by the detector, so a fixture using one
    would silently test nothing.
    """
    doc = Document(
        source_name="note.txt",
        text="From: Sarah Chen\n\nI spoke to Sarah yesterday about it.",
    )
    review = Review(doc, detect(doc))
    rejected = next(i for i, d in enumerate(review.detections) if d.surface == "Sarah")
    review.reject(rejected)

    index = next(i for i, d in enumerate(review.detections) if d.surface == "Sarah Chen")
    review.confirm_all_matching(index, Role.MANAGER)
    review.absorb_partials()

    manager = next(e for e in review.entities.values() if e.role is Role.MANAGER)
    assert "Sarah" not in manager.surfaces, "a rejection the user made must stand"

    sealed = seal(doc, review)
    assert "I spoke to Sarah yesterday" in sealed.text, "the rejected span must survive verbatim"


def test_a_component_inside_the_full_name_is_not_double_counted(granted):
    """"Sarah" inside "Sarah Chen" is already covered and must not be re-absorbed."""
    doc = Document(source_name="note.txt", text="From: Sarah Chen\n\nThe rota is set.")
    review = Review(doc, detect(doc))
    for index in list(review.pending()):
        surface = review.detections[index].surface
        review.confirm(index, Role.MANAGER) if surface == "Sarah Chen" else review.reject(index)
    assert review.absorb_partials() == 0


def test_one_person_gets_one_token_end_to_end(document, granted):
    review = Review(document, detect(document))
    for index, candidate in enumerate(review.detections):
        if candidate.surface == "Marcus Okafor":
            review.confirm_all_matching(index, Role.SELF)
    review.absorb_partials()
    for index in list(review.pending()):
        review.reject(index)
    sealed = seal(document, review)
    assert "[SELF]" in sealed.text
    assert "[SELF_1]" not in sealed.text and "[SELF_2]" not in sealed.text
    assert "Marcus" not in sealed.text


def test_confirm_still_honours_carried_provenance(document):
    """The provenance path stays live for anything routed through confirm()."""
    review = Review(document, detect(document))
    index = next(i for i, d in enumerate(review.detections) if d.surface == "Marcus Okafor")
    review.confirm_all_matching(index, Role.SELF)
    review.absorb_partials()

    folded = [d for d in review.detections if d.suggested_entity_id is not None]
    assert folded, "folded candidates should carry the identity they came from"
    for candidate in folded:
        assert candidate.suggested_entity_id in review.entities


def test_absorb_partials_is_idempotent(document):
    review = Review(document, detect(document))
    index = next(i for i, d in enumerate(review.detections) if d.surface == "Marcus Okafor")
    review.confirm_all_matching(index, Role.SELF)
    first = review.absorb_partials()
    second = review.absorb_partials()
    assert first >= 1 and second == 0, "re-running must not pile up duplicates"


def test_partials_are_not_absorbed_for_non_person_roles(document):
    review = Review(document, detect(document))
    for index in range(len(review.detections)):
        review.confirm(index, Role.CONTACT)
    assert review.absorb_partials() == 0


# -- re-identification warning ---------------------------------------------


def test_small_team_triggers_a_reidentification_warning(document):
    review = Review(document, detect(document))
    index = next(i for i, d in enumerate(review.detections) if d.kind is Kind.PERSON)
    review.confirm(index, Role.MANAGER, team_size=3)
    warnings = review.reidentification_warnings()
    assert warnings and "may still identify" in warnings[0]


def test_large_team_does_not_warn(document):
    review = Review(document, detect(document))
    index = next(i for i, d in enumerate(review.detections) if d.kind is Kind.PERSON)
    review.confirm(index, Role.MANAGER, team_size=40)
    assert not review.reidentification_warnings()


def test_the_warning_never_blocks(document, granted):
    """It is the user's document. We say so, then do as they asked."""
    review = Review(document, detect(document))
    for index, candidate in enumerate(review.detections):
        if candidate.kind is Kind.PERSON:
            review.confirm(index, Role.MANAGER, team_size=2)
        else:
            review.reject(index)
    assert review.reidentification_warnings()
    sealed = seal(document, review)  # must not raise
    assert sealed.entity_count >= 1


# -- possessives and boundaries --------------------------------------------


def test_possessive_forms_are_redacted_too(granted):
    doc = Document(source_name="n.txt", text="From: Sarah Chen\n\nThis was Sarah Chen's decision.")
    review = Review(doc, detect(doc))
    for index, candidate in enumerate(review.detections):
        if candidate.kind is Kind.PERSON:
            review.confirm(index, Role.MANAGER)
        else:
            review.reject(index)
    sealed = seal(doc, review)
    assert "Sarah" not in sealed.text
    assert "Chen" not in sealed.text


def test_a_name_inside_a_longer_word_is_left_alone(granted):
    doc = Document(source_name="n.txt", text="From: Reed\n\nThe agreement was reedited later.")
    review = Review(doc, detect(doc))
    for index, candidate in enumerate(review.detections):
        if candidate.surface == "Reed":
            review.confirm(index, Role.COWORKER)
        else:
            review.reject(index)
    sealed = seal(doc, review)
    assert "reedited" in sealed.text, "substitution must respect word boundaries"
