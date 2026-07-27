"""[REDACTION-GATE] — the load-bearing component. Read this one closely.

The claim this project makes is that no un-redacted content ever leaves the
machine. That claim is enforced here, at one chokepoint, in one way:

    ``bridge.send()`` accepts only a ``SealedText``.
    ``SealedText`` can only be constructed by ``seal()``.
    ``seal()`` refuses unless every detection has been decided by a human,
    and refuses again if any confirmed surface form survives substitution.

There is no second path. A caller holding a raw ``str`` cannot reach the
network no matter what it does, because the type the network function requires
cannot be forged. That is the whole design, and ``tests/test_egress.py`` proves
each link of it independently.

The flow:

    detect()   find candidate names — deliberately over-eager
      ↓
    Review     a human confirms, rejects, corrects, or adds. Nothing is
               decided automatically; detection only proposes.
      ↓
    seal()     substitute role tokens, verify nothing survived, mint SealedText

Detection is imperfect by design. The human step is what makes this safe — not
the detector's accuracy. That is why an undecided detection is a hard error
rather than a default-to-redact: silently redacting an unreviewed span would
teach the user that review is optional, and the one time detection missed a
name they would never know to look.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Protocol

from . import log
from .intake import Document

# --------------------------------------------------------------------------
# Role vocabulary — PROVISIONAL (DECISIONS.md N2)
# --------------------------------------------------------------------------
# Defined in exactly one place so the whole vocabulary can be revised at once.


class Role(str, Enum):
    MANAGER = "MANAGER"
    HR_REP = "HR_REP"
    COWORKER = "COWORKER"
    EXECUTIVE = "EXECUTIVE"
    REPORT = "REPORT"
    EXTERNAL = "EXTERNAL"
    SELF = "SELF"
    EMPLOYER = "EMPLOYER"
    #: Contact details and identifiers, which get a role only incidentally.
    CONTACT = "CONTACT"
    IDENTIFIER = "IDENTIFIER"


ROLE_TOKENS: dict[Role, str] = {
    Role.MANAGER: "MANAGER",
    Role.HR_REP: "HR_REP",
    Role.COWORKER: "COWORKER",
    Role.EXECUTIVE: "EXECUTIVE",
    Role.REPORT: "REPORT",
    Role.EXTERNAL: "EXTERNAL",
    Role.SELF: "SELF",
    Role.EMPLOYER: "EMPLOYER",
    Role.CONTACT: "CONTACT",
    Role.IDENTIFIER: "ID",
}

#: Roles that describe a person, and so must never be merged with one another.
PERSON_ROLES = frozenset(
    {Role.MANAGER, Role.HR_REP, Role.COWORKER, Role.EXECUTIVE, Role.REPORT, Role.EXTERNAL, Role.SELF}
)

#: SETTLED by the owner 2026-07-27 (DECISIONS.md P4). Warn when a role sits on a
#: team this size or smaller — a role label plus a tiny team can re-identify a
#: person as surely as a name. Warn only; never block, never silently mask.
REID_TEAM_SIZE_THRESHOLD = 8


class Kind(str, Enum):
    """What the detector thinks it found. The user may disagree."""

    PERSON = "person"
    EMAIL = "email"
    PHONE = "phone"
    EMPLOYEE_ID = "employee_id"
    COMPANY = "company"


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


# --------------------------------------------------------------------------
# Detection policy — PROVISIONAL (DECISIONS.md P2)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class DetectionPolicy:
    """What counts as something to redact.

    People, emails, phone numbers, and employee identifiers are always
    detected. The company name and the user's own name are opt-in, because
    redacting them often makes a document harder for the model to reason about
    without buying much: the user's own name is not a third party's, and the
    employer's name is usually inferable from the document's subject matter
    anyway. Both remain one toggle away for anyone who wants them.
    """

    include_company: bool = False
    include_own_name: bool = False
    own_name: str = ""
    company_name: str = ""


@dataclass(frozen=True)
class Detection:
    """One candidate span. A proposal, never a decision."""

    start: int
    end: int
    surface: str
    kind: Kind
    confidence: Confidence
    #: Why the detector flagged this, in plain words, for the review screen.
    reason: str = ""
    #: Set when this candidate came from ``absorb_partials`` — the identity it
    #: was derived from. Carrying the provenance is more reliable than
    #: re-deriving the link later: "Marcus" and "Marcus Okafor" share no
    #: surname, so surname matching cannot connect them, and they would
    #: otherwise become two identities with two tokens for one person.
    suggested_entity_id: str | None = None

    def overlaps(self, other: "Detection") -> bool:
        return self.start < other.end and other.start < self.end


# --------------------------------------------------------------------------
# Detectors
# --------------------------------------------------------------------------


class Detector(Protocol):
    name: str

    def detect(self, text: str, policy: DetectionPolicy) -> list[Detection]: ...


_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")

# Deliberately loose. A missed phone number is worse than a rejected date.
# The trailing guard excludes word characters but NOT punctuation: a number at
# the end of a sentence is followed by a full stop, and an earlier version of
# this pattern silently matched nothing in exactly that case.
_PHONE_RE = re.compile(
    r"(?<![\w.])(?:\+?\d{1,3}[\s.-]?)?(?:\(\d{2,4}\)[\s.-]?|\d{2,4}[\s.-])\d{3,4}[\s.-]?\d{3,4}(?!\w)"
)

# A person's name, for use inside the context patterns below.
#
# The inner separators are [ \t] and never \s. A name pattern that allows \s
# between its words will happily run across a line break and swallow the first
# word of the next line — "Sarah Chen\nTo" — which then becomes the surface
# form, the token, and the thing the residual scan looks for. Everything
# downstream stays consistent, so nothing fails; the redaction is simply wrong
# in a way that is invisible until you read the sealed text.
_NAME = r"[A-Z][a-z'’-]+(?:[ \t]+[A-Z][a-z'’-]+){0,2}"

_EMPLOYEE_ID_RE = re.compile(
    r"\b(?:employee\s*(?:id|number|no\.?|#)|emp\s*(?:id|#)|staff\s*(?:id|number|#)|payroll\s*(?:id|number|#))"
    r"\s*[:#-]?\s*([A-Z]{0,4}[-\s]?\d{3,10})\b",
    re.IGNORECASE,
)
_BARE_ID_RE = re.compile(r"\b(?:EMP|EID|STF|PR)[-_]?\d{4,10}\b", re.IGNORECASE)

_TITLE_RE = re.compile(r"\b(?:Mr|Mrs|Ms|Miss|Mx|Dr|Prof|Sir|Dame|Rev)\.?[ \t]+(" + _NAME + r")")

# Capitalised word runs — the workhorse, and the noisiest.
_CAP_RUN_RE = re.compile(
    r"\b[A-Z][a-z'’-]{1,}(?:[ \t]+(?:van|von|de|del|da|di|la|le)[ \t]+[A-Z][a-z'’-]{1,})?"
    r"(?:[ \t]+[A-Z][a-z'’-]{1,}){0,2}\b"
)

_SIGNOFF_RE = re.compile(
    r"(?:^|\n)[ \t]*(?:regards|sincerely|best|thanks|thank you|cheers|yours(?:[ \t]+\w+)?)"
    r"[ \t]*,?[ \t]*\n+[ \t]*(" + _NAME + r")",
    re.IGNORECASE,
)

_FROM_RE = re.compile(
    r"(?:^|\n)[ \t]*(?:from|to|cc|attn|attention|prepared by|submitted by|reviewed by|manager|supervisor)"
    r"[ \t]*:[ \t]*(" + _NAME + r")",
    re.IGNORECASE,
)

# Capitalised words that are almost never a person's name in a workplace
# document. Kept tight on purpose: every word here is a chance to miss a real
# name, and someone really can be called April or Reed.
_NOT_NAMES = frozenset(
    """
    january february march april may june july august september october november december
    monday tuesday wednesday thursday friday saturday sunday
    human resources department company employee employer manager supervisor director
    policy handbook agreement contract section clause appendix schedule exhibit
    the this that these those there their they them then than when where what which
    dear regards sincerely thank thanks please note however therefore accordingly
    monday's employee's company's
    limited incorporated corporation llc ltd inc plc gmbh
    monday-friday full-time part-time
    """.split()
)

# Capitalised words that open a sentence but never begin a name. These cannot go
# in _NOT_NAMES: that list rejects an entire run if any word matches, so "Did
# Devika" would be discarded whole and the real name lost. They are *trimmed*
# from the front of a run instead.
#
# This surfaced with user-typed questions, where nearly every sentence starts
# with one: "Did Devika approve it?" was detected as the single name "Did
# Devika", which would have been shown for review as such and then substituted
# whole, leaving "[COWORKER] approve it?" behind.
_SENTENCE_OPENERS = frozenset(
    """
    a an and as at before but by can could did do does during each every for from
    had has have how i if in is it may might must my no not of on or our per please
    shall should since so that the their then there these they this those to was
    we were what when where whether which while who whom why will with would you your
    """.split()
)

_ROLE_HINTS: dict[Role, tuple[str, ...]] = {
    Role.MANAGER: ("manager", "supervisor", "line manager", "team lead", "reports to", "my boss"),
    Role.HR_REP: ("human resources", "hr ", "people team", "people partner", "hrbp"),
    Role.EXECUTIVE: ("ceo", "cfo", "coo", "cto", "president", "vice president", "vp ", "director"),
    Role.REPORT: ("my report", "reports to me", "direct report", "my team member"),
    Role.EXTERNAL: ("counsel", "attorney", "solicitor", "auditor", "consultant", "vendor", "client"),
}


class HeuristicDetector:
    """Dependency-free detection — always available. PROVISIONAL (DECISIONS P7).

    Tuned to over-detect. A false positive costs the user one click; a false
    negative sends a real person's name to a third-party API. Those are not
    comparable errors, and the thresholds here reflect that.
    """

    name = "built-in"

    def detect(self, text: str, policy: DetectionPolicy) -> list[Detection]:
        found: list[Detection] = []

        for match in _EMAIL_RE.finditer(text):
            found.append(
                Detection(
                    match.start(), match.end(), match.group(0), Kind.EMAIL,
                    Confidence.HIGH, "Looks like an email address.",
                )
            )

        for match in _PHONE_RE.finditer(text):
            digits = sum(ch.isdigit() for ch in match.group(0))
            if 7 <= digits <= 15:
                found.append(
                    Detection(
                        match.start(), match.end(), match.group(0).strip(), Kind.PHONE,
                        Confidence.MEDIUM, "Looks like a phone number.",
                    )
                )

        for match in _EMPLOYEE_ID_RE.finditer(text):
            found.append(
                Detection(
                    match.start(1), match.end(1), match.group(1), Kind.EMPLOYEE_ID,
                    Confidence.HIGH, "Labelled as an employee or payroll identifier.",
                )
            )
        for match in _BARE_ID_RE.finditer(text):
            found.append(
                Detection(
                    match.start(), match.end(), match.group(0), Kind.EMPLOYEE_ID,
                    Confidence.MEDIUM, "Matches a common staff-identifier format.",
                )
            )

        # Strong person signals first, so their confidence wins on overlap.
        for pattern, reason in (
            (_TITLE_RE, "Follows a title such as Mr, Ms, or Dr."),
            (_SIGNOFF_RE, "Appears where a signature usually goes."),
            (_FROM_RE, "Appears after a From/To/Manager-style label."),
        ):
            for match in pattern.finditer(text):
                surface = match.group(1)
                found.append(
                    Detection(
                        match.start(1), match.start(1) + len(surface), surface,
                        Kind.PERSON, Confidence.HIGH, reason,
                    )
                )

        for match in _CAP_RUN_RE.finditer(text):
            surface, offset = self._trim_opener(match.group(0).strip(), match.start())
            if not surface:
                continue
            if self._is_probably_not_a_name(surface, text, offset):
                continue
            words = surface.split()
            confidence = Confidence.MEDIUM if len(words) >= 2 else Confidence.LOW
            reason = (
                "Two or more capitalised words in a row."
                if len(words) >= 2
                else "A single capitalised word that may be a first name."
            )
            found.append(
                Detection(offset, offset + len(surface), surface,
                          Kind.PERSON, confidence, reason)
            )

        if policy.include_own_name and policy.own_name.strip():
            found.extend(self._literal(text, policy.own_name, Kind.PERSON,
                                       "You told us this is your name."))
        if policy.include_company and policy.company_name.strip():
            found.extend(self._literal(text, policy.company_name, Kind.COMPANY,
                                       "You told us this is the organisation's name."))

        return _dedupe(found)

    @staticmethod
    def _literal(text: str, needle: str, kind: Kind, reason: str) -> list[Detection]:
        out: list[Detection] = []
        pattern = re.compile(re.escape(needle.strip()), re.IGNORECASE)
        for match in pattern.finditer(text):
            out.append(
                Detection(match.start(), match.end(), match.group(0), kind,
                          Confidence.HIGH, reason)
            )
        return out

    @staticmethod
    def _trim_opener(surface: str, start: int) -> tuple[str, int]:
        """Drop leading sentence-openers from a capitalised run.

        Returns the trimmed surface and its corrected start offset, so the span
        still points at the right characters. Returns ("" , start) if nothing is
        left, which the caller skips.
        """
        words = surface.split()
        removed = 0
        while len(words) > 1 and words[0].lower().strip(".,'’-") in _SENTENCE_OPENERS:
            removed += len(words[0]) + 1
            words = words[1:]
        if not words:
            return "", start
        if words[0].lower().strip(".,'’-") in _SENTENCE_OPENERS:
            # A lone opener with nothing after it is not a name at all.
            return "", start
        return " ".join(words), start + removed

    @staticmethod
    def _is_probably_not_a_name(surface: str, text: str, start: int) -> bool:
        words = surface.split()
        if any(word.lower().strip(".,'’-") in _NOT_NAMES for word in words):
            return True
        if len(surface) < 3:
            return True
        if len(words) == 1:
            # A lone capitalised word opening a sentence is usually just a
            # sentence. Mid-sentence, it is far more suspicious.
            #
            # Note the deliberate absence of .rstrip() here: stripping the
            # preceding whitespace also strips the newline that is the very
            # thing being tested for, so a word starting a fresh line read as
            # mid-sentence and every paragraph opener became a candidate.
            before = text[:start]
            if not before.strip():
                return True
            if before.rstrip(" \t").endswith(("\n", ".", "!", "?", ":", ";")):
                return True
        return False


class SpacyDetector:
    """Optional higher-recall detection. Raises recall only; never decides.

    Used when spaCy and a model are installed. Its output is merged with the
    built-in detector's rather than replacing it, because the two miss
    different things and the union is what the user reviews.
    """

    name = "spaCy"

    def __init__(self, model: str = "en_core_web_sm") -> None:
        import spacy  # noqa: F401  (import error surfaces to the caller)

        self._nlp = spacy.load(model)

    def detect(self, text: str, policy: DetectionPolicy) -> list[Detection]:
        wanted = {"PERSON"}
        if policy.include_company:
            wanted.add("ORG")
        found: list[Detection] = []
        for ent in self._nlp(text).ents:
            if ent.label_ not in wanted:
                continue
            kind = Kind.PERSON if ent.label_ == "PERSON" else Kind.COMPANY
            found.append(
                Detection(ent.start_char, ent.end_char, ent.text, kind,
                          Confidence.HIGH, f"Recognised as a {ent.label_.lower()} by spaCy.")
            )
        return found


def build_detectors() -> list[Detector]:
    """The built-in detector, plus spaCy if it happens to be installed."""
    detectors: list[Detector] = [HeuristicDetector()]
    try:
        detectors.append(SpacyDetector())
    except Exception:
        # Absent, or installed without a model. Not an error — the built-in
        # detector is the supported default and the human review is the
        # safeguard either way.
        pass
    return detectors


def _dedupe(items: list[Detection]) -> list[Detection]:
    """Drop overlapping spans, keeping the most confident and then the longest."""
    rank = {Confidence.HIGH: 0, Confidence.MEDIUM: 1, Confidence.LOW: 2}
    ordered = sorted(items, key=lambda d: (rank[d.confidence], -(d.end - d.start), d.start))
    kept: list[Detection] = []
    for candidate in ordered:
        if not any(candidate.overlaps(existing) for existing in kept):
            kept.append(candidate)
    return sorted(kept, key=lambda d: d.start)


def detect(document: Document, policy: DetectionPolicy | None = None,
           detectors: Iterable[Detector] | None = None) -> list[Detection]:
    """Find every candidate span in ``document``. Proposals only."""
    policy = policy or DetectionPolicy()
    active = list(detectors) if detectors is not None else build_detectors()
    found: list[Detection] = []
    for detector in active:
        try:
            found.extend(detector.detect(document.text, policy))
        except Exception:
            # One detector failing must not take down review. The built-in one
            # has no dependencies and will still have produced its results.
            continue
    merged = _dedupe(found)
    log.record(
        "redaction.detected",
        source_name=document.source_name,
        detectors=[d.name for d in active],
        candidates=len(merged),
        people=sum(1 for d in merged if d.kind is Kind.PERSON),
    )
    return merged


# --------------------------------------------------------------------------
# Human review
# --------------------------------------------------------------------------


@dataclass
class Entity:
    """A distinct identity the user has confirmed, and the token standing for it.

    ``surfaces`` holds every spelling that refers to this same identity —
    "Sarah Chen", "Ms. Chen", "Sarah". Grouping them matters: if two spellings
    of one person get two tokens, the model reads one person's conduct as two
    people's, and the analysis it produces is wrong in a way the user cannot see.
    """

    entity_id: str
    role: Role
    surfaces: set[str] = field(default_factory=set)
    kind: Kind = Kind.PERSON
    #: Team size, when the user supplied one. Feeds the re-identification warning.
    team_size: int | None = None

    def add_surface(self, surface: str) -> None:
        cleaned = surface.strip()
        if cleaned:
            self.surfaces.add(cleaned)


class Review:
    """The human-in-the-loop step. Nothing is redacted until this is complete.

    Every detection must be resolved — confirmed onto an entity, or rejected.
    ``seal()`` refuses while any remain undecided, which is what makes review a
    step rather than a suggestion.
    """

    def __init__(self, document: Document, detections: list[Detection]) -> None:
        self.document = document
        self.detections = list(detections)
        self.entities: dict[str, Entity] = {}
        self._assignment: dict[int, str | None] = {}  # detection index -> entity id / None
        self._counter = 0

    # -- state -------------------------------------------------------------

    def pending(self) -> list[int]:
        """Indices of detections the user has not yet decided."""
        return [i for i in range(len(self.detections)) if i not in self._assignment]

    def is_complete(self) -> bool:
        return not self.pending()

    def confirmed_surfaces(self) -> set[str]:
        surfaces: set[str] = set()
        for entity in self.entities.values():
            surfaces |= entity.surfaces
        return surfaces

    # -- the user's decisions ---------------------------------------------

    def reject(self, index: int) -> None:
        """Not a name. Leave it in the text untouched."""
        self._require_index(index)
        self._assignment[index] = None

    def reject_all_matching(self, surface: str) -> int:
        """Reject every detection with the same surface form. Saves clicking."""
        target = _fold(surface)
        count = 0
        for i, detection in enumerate(self.detections):
            if i not in self._assignment and _fold(detection.surface) == target:
                self._assignment[i] = None
                count += 1
        return count

    def confirm(self, index: int, role: Role, entity_id: str | None = None,
                team_size: int | None = None) -> Entity:
        """Confirm a detection as a real identity and assign it a role."""
        self._require_index(index)
        detection = self.detections[index]

        if entity_id is None:
            # Provenance first: a candidate raised by absorb_partials already
            # knows which identity it belongs to, provided the user agrees with
            # that identity's role. If they picked a different role they are
            # telling us it is someone else, so fall through.
            suggested = detection.suggested_entity_id
            if suggested and suggested in self.entities and self.entities[suggested].role is role:
                entity_id = suggested
            else:
                entity_id = self._match_existing(detection.surface, role) or self._new_id(role)

        entity = self.entities.get(entity_id)
        if entity is None:
            entity = Entity(entity_id=entity_id, role=role, kind=detection.kind)
            self.entities[entity_id] = entity
        entity.add_surface(detection.surface)
        if team_size is not None:
            entity.team_size = team_size

        self._assignment[index] = entity_id
        return entity

    def confirm_all_matching(self, index: int, role: Role) -> Entity:
        """Confirm this detection and every other with the same surface form."""
        entity = self.confirm(index, role)
        target = _fold(self.detections[index].surface)
        for i, detection in enumerate(self.detections):
            if i not in self._assignment and _fold(detection.surface) == target:
                self.confirm(i, role, entity_id=entity.entity_id)
        return entity

    def add_manual(self, surface: str, role: Role, team_size: int | None = None) -> Entity:
        """Add a name the detector missed. The most important function here.

        Detection is imperfect; this is how the user fixes an omission. Every
        occurrence of ``surface`` in the document is covered by the entity, so
        adding it once is enough.
        """
        cleaned = surface.strip()
        if not cleaned:
            raise ValueError("Nothing to add.")
        entity_id = self._match_existing(cleaned, role) or self._new_id(role)
        entity = self.entities.get(entity_id)
        if entity is None:
            entity = Entity(entity_id=entity_id, role=role, kind=Kind.PERSON)
            self.entities[entity_id] = entity
        entity.add_surface(cleaned)
        if team_size is not None:
            entity.team_size = team_size

        # Any pending detection covered by this surface is now decided.
        folded = _fold(cleaned)
        for i, detection in enumerate(self.detections):
            if i not in self._assignment and _fold(detection.surface) == folded:
                self._assignment[i] = entity_id
        return entity

    def merge(self, keep_id: str, absorb_id: str) -> Entity:
        """Declare two entities to be the same person."""
        if keep_id == absorb_id:
            return self.entities[keep_id]
        keep, absorb = self.entities[keep_id], self.entities.pop(absorb_id)
        keep.surfaces |= absorb.surfaces
        if keep.team_size is None:
            keep.team_size = absorb.team_size
        for index, assigned in self._assignment.items():
            if assigned == absorb_id:
                self._assignment[index] = keep_id
        return keep

    def set_role(self, entity_id: str, role: Role) -> Entity:
        entity = self.entities[entity_id]
        entity.role = role
        return entity

    # -- partial names -----------------------------------------------------

    def absorb_partials(self) -> int:
        """Fold bare components of a confirmed full name into that same person.

        Found by a dry run rather than by reasoning: confirming "Marcus Okafor"
        left a later salutation "Marcus," untouched, and the residual scan could
        not catch it — "Marcus" alone was never a confirmed surface, so there was
        nothing for it to look for. In a letter addressed to the user that is
        merely untidy. Where the name belongs to a manager or a colleague, it is
        a leak: the full name is replaced everywhere it appears, the first name
        survives, and the redacted text reads as though it were complete.

        The scope of this is deliberately narrow, and the boundary is the point:

        * A word that is **a component of a name the user already confirmed**
          is folded into that same identity automatically, keeping one token for
          one person. The user has already made the judgement that matters —
          that this name belongs to this person in this role — and re-asking
          about "Marcus" after they confirmed "Marcus Okafor" is review theatre.
          It trains people to click through, which is how a review step dies.

        * A bare name that was **never part of any confirmed full name** is left
          pending. Nothing here decides those. An unfamiliar single name is
          genuinely ambiguous, and guessing on the user's behalf is exactly the
          kind of silent decision this tool does not get to make.

        Returns the number of occurrences folded in.
        """
        text = self.document.text
        absorbed = 0

        for entity in list(self.entities.values()):
            if entity.role not in PERSON_ROLES:
                continue
            for surface in list(entity.surfaces):
                parts = [p for p in re.split(r"[\s,]+", surface.strip()) if len(p) >= 3]
                if len(parts) < 2:
                    continue
                for part in parts:
                    if part.lower() in _NOT_NAMES:
                        continue
                    if any(_fold(part) == _fold(s) for s in entity.surfaces):
                        continue
                    pattern = re.compile(r"(?<!\w)" + re.escape(part) + r"(?:['’]s)?(?!\w)")
                    for match in pattern.finditer(text):
                        candidate = Detection(
                            start=match.start(),
                            end=match.end(),
                            surface=match.group(0),
                            kind=Kind.PERSON,
                            confidence=Confidence.HIGH,
                            reason=(
                                f"Part of \"{surface}\", which you confirmed as "
                                f"{entity.role.value.replace('_', ' ').lower()}."
                            ),
                            suggested_entity_id=entity.entity_id,
                        )

                        overlapping = [
                            i
                            for i, existing in enumerate(self.detections)
                            if candidate.overlaps(existing)
                        ]
                        if overlapping:
                            # The detector had already flagged this span. Absorb
                            # it only while it is still undecided — if the user
                            # has ruled on it, that ruling stands.
                            index = overlapping[0]
                            if index in self._assignment:
                                continue
                            self._assignment[index] = entity.entity_id
                            entity.add_surface(self.detections[index].surface)
                        else:
                            self.detections.append(candidate)
                            self._assignment[len(self.detections) - 1] = entity.entity_id
                            entity.add_surface(candidate.surface)
                        absorbed += 1

        if absorbed:
            log.record("redaction.partials_absorbed", absorbed=absorbed)
        return absorbed

    # -- warnings ----------------------------------------------------------

    def reidentification_warnings(self) -> list[str]:
        """Roles that a small team could make identifying anyway.

        A role token is not anonymity when only three people hold the role. The
        tool says so rather than pretending otherwise — and says so as a warning,
        not a block, because it is the user's document and their call.
        """
        warnings: list[str] = []
        for entity in self.entities.values():
            if entity.team_size is not None and entity.team_size <= REID_TEAM_SIZE_THRESHOLD:
                warnings.append(
                    f"{entity.entity_id} sits on a team of {entity.team_size}. "
                    f"A role label alone may still identify them."
                )
        return warnings

    # -- internals ---------------------------------------------------------

    def _require_index(self, index: int) -> None:
        if not 0 <= index < len(self.detections):
            raise IndexError(f"No detection at position {index}.")

    def _new_id(self, role: Role) -> str:
        self._counter += 1
        return f"{role.value.lower()}-{self._counter}"

    def _match_existing(self, surface: str, role: Role) -> str | None:
        """Guess whether ``surface`` is an already-known identity.

        Conservative on purpose: it only groups on an exact fold match or a
        shared surname. Wrongly merging two people is a silent correctness
        failure, so this suggests rather than insists — the user can always
        merge explicitly, and the review screen shows the grouping.
        """
        folded = _fold(surface)
        tokens = set(folded.split())
        for entity in self.entities.values():
            if entity.role is not role:
                continue
            for known in entity.surfaces:
                known_folded = _fold(known)
                if known_folded == folded:
                    return entity.entity_id
                known_tokens = set(known_folded.split())
                # Shared surname (last token), and one of the two is a subset —
                # "Sarah Chen" / "Chen", or "Sarah Chen" / "Ms Chen".
                if (
                    known_tokens
                    and tokens
                    and list(known_folded.split())[-1] == list(folded.split())[-1]
                    and (tokens <= known_tokens or known_tokens <= tokens)
                ):
                    return entity.entity_id
        return None


def _fold(text: str) -> str:
    """Normalise a surface form for comparison: accents, case, punctuation."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"\b(?:mr|mrs|ms|miss|mx|dr|prof|sir|dame|rev)\.?\s+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"[^\w\s]", "", text)
    return " ".join(text.lower().split())


# --------------------------------------------------------------------------
# The chokepoint
# --------------------------------------------------------------------------

# Held only by this module. ``SealedText`` refuses to construct without it, so
# ``seal()`` below is the sole way to produce one anywhere in the program.
_MINT = object()


class SealError(Exception):
    """Sealing failed. Nothing may be sent; the caller must resolve this."""


class SealedText:
    """Text that has passed the redaction gate. The only thing ``bridge`` accepts.

    Cannot be constructed directly — ``SealedText(...)`` raises. Cannot be
    mutated — attribute assignment raises. Cannot be produced by copying or
    unpickling, because both routes go through ``__init__`` without the mint.

    This is not paranoia about a malicious caller; it is about the ordinary way
    codebases decay. Someone in a hurry, six months from now, wanting to "just
    send the raw text for this one feature", should find that the type system
    will not let them — and should have to come here and read this docstring to
    understand why.
    """

    __slots__ = ("_text", "_token_map", "_entity_count", "_source_name")

    def __init__(self, text: str, token_map: dict[str, str], source_name: str,
                 *, _mint: object = None) -> None:
        if _mint is not _MINT:
            raise TypeError(
                "SealedText cannot be constructed directly. It exists only as the "
                "output of jobmonger.redaction.seal(), which is the single point "
                "at which content is cleared to leave this machine."
            )
        object.__setattr__(self, "_text", text)
        object.__setattr__(self, "_token_map", dict(token_map))
        object.__setattr__(self, "_entity_count", len(token_map))
        object.__setattr__(self, "_source_name", source_name)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("SealedText is immutable once sealed.")

    def __delattr__(self, name: str) -> None:
        raise AttributeError("SealedText is immutable once sealed.")

    def __reduce__(self):
        raise TypeError("SealedText cannot be pickled; re-seal from the source document.")

    @property
    def text(self) -> str:
        """The redacted text. Safe to transmit."""
        return self._text

    @property
    def entity_count(self) -> int:
        return self._entity_count

    @property
    def source_name(self) -> str:
        return self._source_name

    @property
    def tokens(self) -> tuple[str, ...]:
        """The role tokens present, for showing the user what was substituted."""
        return tuple(sorted(set(self._token_map.values())))

    def restore(self, text: str) -> str:
        """Put real names back into a model response, for local display only.

        The reverse map never leaves this object and is never transmitted. This
        is what lets the user read an answer about "your manager" as an answer
        about the person they actually work for, without that name having been
        sent anywhere.
        """
        reverse: dict[str, str] = {}
        for surface, token in self._token_map.items():
            # First surface wins — usually the fullest spelling of the name.
            reverse.setdefault(token, surface)
        for token, surface in sorted(reverse.items(), key=lambda kv: -len(kv[0])):
            text = text.replace(token, surface)
        return text

    def __repr__(self) -> str:
        return f"<SealedText source={self._source_name!r} entities={self._entity_count} chars={len(self._text)}>"


def assign_tokens(entities: Iterable[Entity]) -> dict[str, str]:
    """Map each entity id to its role token.

    A role held by exactly one person gets the bare token (``[MANAGER]``); a
    role held by several gets numbered tokens (``[COWORKER_1]``,
    ``[COWORKER_2]``). Numbering is by entity id, so it is stable across runs.

    Distinct people never share a token — see DECISIONS.md item P3. Collapsing
    two coworkers into one label would merge their conduct into a single
    narrative, and the user would have no way to see that it had happened.
    """
    by_role: dict[Role, list[Entity]] = {}
    for entity in entities:
        by_role.setdefault(entity.role, []).append(entity)

    tokens: dict[str, str] = {}
    for role, members in by_role.items():
        base = ROLE_TOKENS[role]
        members.sort(key=lambda e: e.entity_id)
        if len(members) == 1:
            tokens[members[0].entity_id] = f"[{base}]"
        else:
            for position, entity in enumerate(members, start=1):
                tokens[entity.entity_id] = f"[{base}_{position}]"
    return tokens


def seal(document: Document, review: Review) -> SealedText:
    """Substitute role tokens and mint the only object ``bridge`` will accept.

    Raises ``SealError`` unless:

    * every detection has been decided by the user, and
    * no confirmed surface form survives anywhere in the output.

    The second check is the one that matters. Substitution is done by regex over
    known surfaces, and regexes have edge cases — hyphenation, possessives, a
    name spanning a line break. Rather than trusting the substitution to be
    complete, the output is re-scanned for every confirmed surface, and sealing
    fails if any remains. A failed seal is recoverable; a leaked name is not.
    """
    pending = review.pending()
    if pending:
        raise SealError(
            f"{len(pending)} detected name(s) have not been reviewed yet. "
            "Every one has to be confirmed or rejected before anything can be sent."
        )

    tokens = assign_tokens(review.entities.values())

    # Longest surfaces first, so "Sarah Chen" is replaced before "Chen" and the
    # substitution does not leave a stranded fragment behind.
    replacements: list[tuple[str, str]] = []
    surface_to_token: dict[str, str] = {}
    for entity in review.entities.values():
        token = tokens[entity.entity_id]
        for surface in entity.surfaces:
            replacements.append((surface, token))
            surface_to_token[surface] = token
    replacements.sort(key=lambda pair: -len(pair[0]))

    text = _apply_substitutions(document.text, replacements)

    residual = _find_residual(text, surface_to_token.keys())
    if residual:
        shown = ", ".join(sorted(residual)[:3])
        raise SealError(
            "Redaction did not fully apply — "
            f"{len(residual)} confirmed name(s) still appear in the text ({shown}). "
            "Nothing has been sent. This is a bug; please report it with the document type."
        )

    log.record(
        "redaction.sealed",
        source_name=document.source_name,
        entities=len(review.entities),
        substitutions=len(replacements),
        tokens=sorted(set(tokens.values())),
        reid_warnings=len(review.reidentification_warnings()),
    )
    return SealedText(text, surface_to_token, document.source_name, _mint=_MINT)


class UnscreenedName(SealError):
    """User-typed text contains a name that has not been through review."""

    def __init__(self, novel: tuple[Detection, ...]) -> None:
        self.novel = novel
        names = ", ".join(sorted({d.surface for d in novel}))
        super().__init__(
            f"What you typed contains {len(set(d.surface for d in novel))} name(s) "
            f"that have not been reviewed: {names}. Either take them out, or add "
            "them on the review screen so they get a role label like everything else."
        )


@dataclass(frozen=True)
class ScreenResult:
    """The outcome of screening something the user typed."""

    text: str
    #: Names found that are not covered by any confirmed identity. While this is
    #: non-empty the text must not be sent.
    novel: tuple[Detection, ...] = ()

    @property
    def is_clear(self) -> bool:
        return not self.novel

    def require_clear(self) -> str:
        if self.novel:
            raise UnscreenedName(self.novel)
        return self.text


def screen_user_text(text: str, review: Review,
                     policy: DetectionPolicy | None = None) -> ScreenResult:
    """Redact a string the *user* typed, before it can be sent anywhere.

    The document goes through ``seal()``. This is the other input — the question
    box, tenure notes, a compliance query — and it needs the same treatment for
    the same reason.

    It was originally missed. ``bridge.send()`` takes a sealed payload *and* an
    instruction string, and the dial interpolated the user's question into the
    instruction. The payload was airtight; the sentence next to it was not, so
    "Did Sarah have authority to deny this?" travelled verbatim. The gate was
    real, and the question walked around it.

    Two passes, in this order:

    1. Every confirmed surface is substituted with its token, so names the user
       has already reviewed carry their existing label — the same person keeps
       the same token whether they were named in the document or in the query.
    2. Detection runs over what remains. Anything still name-shaped is *novel*:
       never confirmed, so nothing here is entitled to decide it. The caller
       must refuse to send until the user reviews it.

    This is the same split settled in DECISIONS.md item X4, applied to a second
    input: known components fold in with provenance, unfamiliar names stay in
    review.
    """
    tokens = assign_tokens(review.entities.values())
    replacements: list[tuple[str, str]] = []
    for entity in review.entities.values():
        token = tokens[entity.entity_id]
        for surface in entity.surfaces:
            replacements.append((surface, token))
    replacements.sort(key=lambda pair: -len(pair[0]))

    screened = _apply_substitutions(text, replacements)

    policy = policy or DetectionPolicy()
    novel = [
        candidate
        for candidate in _dedupe(
            [d for detector in build_detectors() for d in detector.detect(screened, policy)]
        )
        # Contact details the user types about themselves are their own to share;
        # the concern here is third-party names they have not reviewed.
        if candidate.kind in (Kind.PERSON, Kind.COMPANY)
    ]

    if novel:
        log.record("redaction.user_text_blocked", novel_count=len(novel))
    return ScreenResult(text=screened, novel=tuple(novel))


def _apply_substitutions(text: str, replacements: list[tuple[str, str]]) -> str:
    """Replace each surface with its token. Kept separate so it can be broken.

    Extracted from ``seal()`` on purpose: the residual scan downstream is a
    backstop against this function being wrong, and a backstop nobody has seen
    fire is not known to work. ``tests/test_egress.py`` swaps this out for a
    no-op and asserts that sealing fails loudly rather than proceeding.
    """
    for surface, token in replacements:
        # Word-boundary-aware where the surface starts and ends with a word
        # character; plain replacement otherwise (emails, ids with punctuation).
        if surface[:1].isalnum() and surface[-1:].isalnum():
            pattern = re.compile(
                r"(?<!\w)" + re.escape(surface) + r"(?:['’]s)?(?!\w)", re.IGNORECASE
            )
        else:
            pattern = re.compile(re.escape(surface), re.IGNORECASE)
        text = pattern.sub(token, text)
    return text


def reseal_derived(parent: SealedText, text: str) -> SealedText:
    """Seal text derived from already-sealed content — a summary, a fact set.

    The fact layer needs to send its own output back to the model (that is how
    a dialed reading is produced from fixed facts rather than from the document
    again), and ``bridge`` accepts only ``SealedText``. This is that path.

    It is not a loophole. The derived text is scanned against the parent's
    confirmed surfaces exactly as ``seal()`` scans its own output, so the only
    way through is content that provably contains no confirmed name. The scan
    is redundant by construction — the model never received those names, so it
    cannot have returned them — and that is the point: a check that should
    never fire is a cheap way to find out if something upstream has changed.
    """
    if not isinstance(parent, SealedText):
        raise TypeError("reseal_derived() needs the SealedText the content came from.")

    residual = _find_residual(text, parent._token_map.keys())
    if residual:
        raise SealError(
            f"Derived content contains {len(residual)} confirmed name(s) that should "
            "not be present. Nothing has been sent. This indicates a bug upstream."
        )
    return SealedText(text, dict(parent._token_map), parent.source_name, _mint=_MINT)


def _find_residual(text: str, surfaces: Iterable[str]) -> set[str]:
    """Any confirmed surface still present after substitution.

    Compares on a whitespace- and punctuation-folded copy so that a name broken
    across a line break, or followed by a possessive, is still caught.
    """
    haystack = _fold(text)
    leaked: set[str] = set()
    for surface in surfaces:
        needle = _fold(surface)
        if not needle:
            continue
        if re.search(r"(?<!\w)" + re.escape(needle) + r"(?!\w)", haystack):
            leaked.add(surface)
    return leaked
