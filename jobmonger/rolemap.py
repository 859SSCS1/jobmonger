"""[ROLE-MAP] — what each role is obligated to do, and to whom.

Three columns per role: duties **to the company**, duties **for the user**, and
duties **against the user**. That third column is the one that earns its keep. A
manager's duty to document performance concerns is not malice; it is the job.
But it runs against the user's interest, and a person who does not know it is
coming is blindsided by it. Naming it as a duty rather than a betrayal is the
difference between a tool that makes someone paranoid and one that makes them
prepared.

Two boundaries hold this apart from building a dossier:

* It analyses **roles**, never people. The model sees only tokens, and the
  guardrails tell it to treat each token as a position. A duty belongs to
  whoever holds the role, not to the person who happens to hold it now.
* It reports **obligations**, never character. "This role is expected to
  escalate repeat absences" is in scope. "This person is out to get you" is not,
  and is the specific failure this module is shaped to avoid.

Like the fact layer, the map is extracted once and frozen. Duties are facts
about roles, and the dial does not get to soften a duty that cuts against the
user any more than it gets to soften a fact.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

from . import log
from .bridge import BridgeError, Effort, directive, send
from .config import Config
from .constants import with_short_disclaimer
from .facts import Certainty
from .prompts import Task
from .redaction import (
    REID_TEAM_SIZE_THRESHOLD,
    PERSON_ROLES,
    Review,
    SealedText,
    assign_tokens,
    reseal_derived,
)

Direction = Literal["to_company", "for_user", "against_user"]

_DIRECTION_LABELS: dict[Direction, str] = {
    "to_company": "Owes the organisation",
    "for_user": "Owes you",
    "against_user": "Works against you",
}

# The instruction, the system note, and the JSON schema live in prompts.py.
# See DECISIONS.md item X8: bridge takes no text from callers.


@dataclass(frozen=True)
class Duty:
    direction: Direction
    duty: str
    quote: str
    certainty: Certainty


@dataclass(frozen=True)
class RoleAnalysis:
    """One role's obligations, and whether the role alone could identify someone."""

    token: str
    duties: tuple[Duty, ...]
    team_size: int | None = None

    def in_direction(self, direction: Direction) -> tuple[Duty, ...]:
        return tuple(d for d in self.duties if d.direction == direction)

    @property
    def reidentifiable(self) -> bool:
        """True when the team is small enough that the label is thin cover.

        Threshold settled by the owner at 8 (DECISIONS.md item P4). A warning
        only — it never blocks the analysis and never masks anything further.
        The user's document is the user's; this says the quiet part and leaves
        the decision with them.
        """
        return self.team_size is not None and self.team_size <= REID_TEAM_SIZE_THRESHOLD

    @property
    def reidentification_note(self) -> str:
        if not self.reidentifiable:
            return ""
        return (
            f"{self.token} sits on a team of {self.team_size}. Describing this role's "
            "duties may point at one person as clearly as naming them would."
        )


@dataclass(frozen=True)
class RoleMap:
    """The frozen map. Duties are facts about roles; the dial cannot soften them."""

    roles: tuple[RoleAnalysis, ...]
    source_name: str
    model: str = ""

    def __len__(self) -> int:
        return len(self.roles)

    @property
    def reidentification_notes(self) -> tuple[str, ...]:
        return tuple(r.reidentification_note for r in self.roles if r.reidentifiable)

    def render(self) -> str:
        """The map as text. Used to carry it back to the model, and to display."""
        if not self.roles:
            return "ROLE MAP\n\n- (no roles were established from this document)"

        lines = ["ROLE MAP", ""]
        for role in self.roles:
            lines.append(role.token)
            for direction, heading in _DIRECTION_LABELS.items():
                duties = role.in_direction(direction)  # type: ignore[arg-type]
                if not duties:
                    continue
                lines.append(f"  {heading}:")
                for duty in duties:
                    mark = "" if duty.certainty == "stated" else f" ({duty.certainty})"
                    lines.append(f"    - {duty.duty}{mark}")
                    lines.append(f'      quoted: "{duty.quote}"')
            if role.reidentifiable:
                lines.append(f"  Note: {role.reidentification_note}")
            lines.append("")
        return "\n".join(lines).rstrip()

    def render_for_user(self) -> str:
        """What the user reads. Carries the shared short disclaimer."""
        return with_short_disclaimer(self.render())


def extract(sealed: SealedText, review: Review, facts_text: str, *,
            cfg: Config | None = None) -> RoleMap:
    """Build the role map.

    Routes through the existing chokepoint: the payload is ``reseal_derived``
    of content already sealed, so it passes the same residual scan, and it goes
    out through ``bridge.send`` — the one egress point — which attaches the
    settled guardrails to the system prompt like every other request.

    Team sizes come from the ``Review``, where the user supplied them. They are
    never sent to the model: the headcount is used locally to decide whether to
    warn, and a team size is itself a small identifying detail.
    """
    tokens = assign_tokens(review.entities.values())
    people = {
        tokens[entity.entity_id]: entity
        for entity in review.entities.values()
        if entity.role in PERSON_ROLES
    }
    if not people:
        log.record("rolemap.skipped", reason="no person roles confirmed")
        return RoleMap(roles=(), source_name=sealed.source_name)

    body = (
        f"{facts_text}\n\nROLE LABELS IN THIS DOCUMENT\n\n"
        + "\n".join(f"- {token}" for token in sorted(people))
    )
    payload = reseal_derived(sealed, body)

    reply = send(payload, directive(Task.ROLE_MAP, effort=Effort.HIGH), cfg=cfg)

    try:
        payload_json = json.loads(reply.text)
    except json.JSONDecodeError as exc:
        raise BridgeError(
            "The role map came back in a form that could not be read. Try again; "
            "if it keeps happening, the model may not support structured output."
        ) from exc

    analyses: list[RoleAnalysis] = []
    for item in payload_json.get("roles", []):
        token = str(item.get("token", "")).strip()
        if token not in people:
            # A label the model invented, or one it mangled. Dropped rather than
            # shown: a duty attached to a role the user never confirmed is not
            # something they can act on.
            continue
        duties = tuple(
            Duty(
                direction=d.get("direction", "to_company"),  # type: ignore[arg-type]
                duty=str(d.get("duty", "")).strip(),
                quote=str(d.get("quote", "")).strip(),
                certainty=d.get("certainty", "unclear"),  # type: ignore[arg-type]
            )
            for d in item.get("duties", [])
            if str(d.get("duty", "")).strip()
        )
        analyses.append(
            RoleAnalysis(token=token, duties=duties, team_size=people[token].team_size)
        )

    role_map = RoleMap(
        roles=tuple(sorted(analyses, key=lambda r: r.token)),
        source_name=sealed.source_name,
        model=reply.model,
    )
    log.record(
        "rolemap.extracted",
        source_name=sealed.source_name,
        model=reply.model,
        roles=len(role_map),
        duties=sum(len(r.duties) for r in role_map.roles),
        against_user=sum(len(r.in_direction("against_user")) for r in role_map.roles),
        reid_flagged=len(role_map.reidentification_notes),
    )
    return role_map
