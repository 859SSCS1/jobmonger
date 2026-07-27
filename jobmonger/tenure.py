"""[TENURE] — what length of service tends to confer on a position.

Time in a place buys things a job title does not: knowing which rules get
enforced and which are quietly ignored, knowing who to ask, being believed by
default, holding relationships that predate the current org chart. A person
working out what to do next is better off knowing that the role across the table
has fifteen years of that and they have eight months.

Three things constrain this module, and all three are load-bearing:

*It is inference, and it says so.* Everything here is "likely" and "often",
because that is what it is. A confident-sounding guess about someone's standing
is worse than useless to a person deciding whether to escalate — it is the kind
of thing they would act on. Observations carry a certainty marker like facts do,
and almost all of them will read "implied".

*Tenure is sent as a band, never a figure.* "Over ten years" reasons just as
well as "fourteen years" and identifies far less. Exact service length plus a
role plus a small team is a name in all but spelling — which is the case the
owner-settled guardrails already tell the model to back away from, and there is
no reason to hand it the sharpest version of the detail. See DECISIONS.md P10.

*No cross-user learning, ever.* Nothing here is stored, aggregated, or compared
against anything outside the document in front of the user. There is no corpus.
The scope doc is explicit that a single organisation is far too little data to
learn from, and that any such capability belongs to a later stage behind consent
gates that do not exist yet.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from . import log
from .bridge import BridgeError, Effort, directive, send
from .config import Config
from .constants import with_short_disclaimer
from .facts import Certainty
from .prompts import Task
from .redaction import (
    PERSON_ROLES,
    REID_TEAM_SIZE_THRESHOLD,
    Review,
    SealedText,
    assign_tokens,
    reseal_derived,
    screen_user_text,
)

#: PROVISIONAL (DECISIONS.md item P10). Upper bound of each band, in years, and
#: the words used for it. Deliberately coarse at the top: the difference between
#: eleven years and twenty-two rarely changes what a reader should do, while the
#: exact figure is highly identifying.
BANDS: tuple[tuple[float, str], ...] = (
    (1, "under a year"),
    (3, "one to two years"),
    (6, "three to five years"),
    (11, "six to ten years"),
)
LONGEST_BAND = "over ten years"


def band_for(years: float | None) -> str:
    """The band a service length falls in. This, and never the figure, is sent."""
    if years is None:
        return ""
    if years < 0:
        raise ValueError("Length of service cannot be negative.")
    for upper, name in BANDS:
        if years < upper:
            return name
    return LONGEST_BAND


@dataclass(frozen=True)
class TenureInput:
    """What the user told us about one role's length of service.

    ``note`` is free text and is screened before it can travel — the module does
    that itself rather than trusting the caller, because a caller who forgets is
    exactly how DECISIONS.md item X6 happened.
    """

    token: str
    years: float | None = None
    note: str = ""


@dataclass(frozen=True)
class Observation:
    token: str
    observation: str
    basis: str
    certainty: Certainty

    def render(self) -> str:
        mark = "" if self.certainty == "stated" else f" ({self.certainty})"
        return f"- {self.observation}{mark}\n  based on: {self.basis}"


@dataclass(frozen=True)
class TenureMap:
    """Frozen, like the facts and the role map. The dial cannot soften it."""

    observations: tuple[Observation, ...]
    bands: tuple[tuple[str, str], ...]
    source_name: str
    model: str = ""
    #: Roles where a tenure band and a small team together could identify a person.
    reidentification_notes: tuple[str, ...] = ()

    def __len__(self) -> int:
        return len(self.observations)

    def for_token(self, token: str) -> tuple[Observation, ...]:
        return tuple(o for o in self.observations if o.token == token)

    def render(self) -> str:
        if not self.observations:
            return "STANDING AND INFLUENCE\n\n- (nothing could be inferred from what you supplied)"
        lines = ["STANDING AND INFLUENCE", "",
                 "Everything below is inference from length of service, not fact.", ""]
        for token, band in self.bands:
            observations = self.for_token(token)
            if not observations:
                continue
            lines.append(f"{token} — {band}")
            lines.extend(o.render() for o in observations)
            lines.append("")
        return "\n".join(lines).rstrip()

    def render_for_user(self) -> str:
        """What the user reads. Carries the shared short disclaimer."""
        body = self.render()
        if self.reidentification_notes:
            body += "\n\n" + "\n".join(f"Note: {n}" for n in self.reidentification_notes)
        return with_short_disclaimer(body)


def observe(sealed: SealedText, review: Review, facts_text: str,
            inputs: list[TenureInput], *, cfg: Config | None = None) -> TenureMap:
    """Infer what the supplied service lengths mean for the reader's situation.

    Routes exactly as the role map does: the payload is ``reseal_derived`` of
    content already sealed, so it passes the same residual scan, and it leaves
    through ``bridge.send`` with a minted directive — the settled guardrails ride
    on the system prompt, and no text from this module can reach the model
    except through ``prompts.py``.

    Free-text notes go through ``screen_user_text`` first. A note naming someone
    who has not been reviewed raises ``UnscreenedName`` and nothing is sent.
    """
    tokens = assign_tokens(review.entities.values())
    people = {
        tokens[entity.entity_id]: entity
        for entity in review.entities.values()
        if entity.role in PERSON_ROLES
    }

    supplied = [
        item for item in inputs
        if item.token in people and (item.years is not None or item.note.strip())
    ]
    if not supplied:
        log.record("tenure.skipped", reason="no tenure supplied for any confirmed role")
        return TenureMap(observations=(), bands=(), source_name=sealed.source_name)

    lines: list[str] = []
    bands: list[tuple[str, str]] = []
    for item in supplied:
        band = band_for(item.years)
        if band:
            bands.append((item.token, band))
        # The note is the user's own words, so it is screened like a question.
        note = screen_user_text(item.note, review).require_clear().strip() if item.note.strip() else ""
        descriptor = band or "length of service not given"
        lines.append(f"- {item.token}: {descriptor}" + (f". The reader adds: {note}" if note else ""))

    body = f"{facts_text}\n\nLENGTH OF SERVICE\n\n" + "\n".join(lines)
    payload = reseal_derived(sealed, body)

    reply = send(payload, directive(Task.TENURE, effort=Effort.HIGH), cfg=cfg)

    try:
        parsed = json.loads(reply.text)
    except json.JSONDecodeError as exc:
        raise BridgeError(
            "The tenure reading came back in a form that could not be read. Try "
            "again; if it keeps happening, the model may not support structured output."
        ) from exc

    observations = tuple(
        Observation(
            token=str(item.get("token", "")).strip(),
            observation=str(item.get("observation", "")).strip(),
            basis=str(item.get("basis", "")).strip(),
            certainty=item.get("certainty", "unclear"),  # type: ignore[arg-type]
        )
        for item in parsed.get("observations", [])
        # Same rule as the role map: an observation about a role the user never
        # confirmed is not something they can act on.
        if str(item.get("token", "")).strip() in people
        and str(item.get("observation", "")).strip()
    )

    notes = tuple(
        f"{token} has a length of service on a team of "
        f"{people[token].team_size}. Together those may point at one person as "
        "clearly as a name would."
        for token, _band in bands
        if people[token].team_size is not None
        and people[token].team_size <= REID_TEAM_SIZE_THRESHOLD
    )

    tenure_map = TenureMap(
        observations=observations,
        bands=tuple(bands),
        source_name=sealed.source_name,
        model=reply.model,
        reidentification_notes=notes,
    )
    log.record(
        "tenure.observed",
        source_name=sealed.source_name,
        model=reply.model,
        roles_with_tenure=len(bands),
        observations=len(observations),
        reid_flagged=len(notes),
        # Bands, never figures — the log is subject to the same rule as the wire.
        bands=[band for _token, band in bands],
    )
    return tenure_map
