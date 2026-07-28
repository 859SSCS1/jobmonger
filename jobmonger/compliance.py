"""[COMPLIANCE] — the defensive companion. What your handbook requires, and where it says nothing.

The other modules read a situation. This one reads a rulebook, and it exists to
answer a narrower question than it looks like: *what does this document require
of me, and by when?* A person working out whether to appeal, how long they have,
or what they are supposed to have filed is served by a plain restatement of
their own rules and by an honest account of the gaps.

**This is the module where the no-verdict line is easiest to cross**, which is
why it is drawn twice — once in the instruction and once in the shape of the
output. There is no field here for whether anyone complied. Not for the user,
not for their employer. The schema offers `requirement`, `applies_to`,
`deadline`, `quote`, `certainty` — and nothing that could hold "and they didn't".
A model inclined to drift has nowhere to put the drift.

The slide this guards against is short and natural: *the policy requires notice
within five days* → *you gave notice on day eight* → *you are in breach* → *they
would win*. Each step feels like the obvious next sentence. The first is this
tool's job; the second is a fact the user already has; the third and fourth are
a legal conclusion this tool is not permitted to reach and is not competent to.

Two design choices carry weight:

*Silences are first-class.* A rule that does not exist is something the reader
needs to know does not exist, and it is exactly where a language model will
helpfully supply what such documents "usually" say. Asking for silences
explicitly gives that instinct somewhere legitimate to go.

*Deadlines are their own field.* Timing is usually the most actionable thing a
handbook contains and the easiest thing to lose inside a paragraph. A ten-day
appeal window buried in prose is a ten-day appeal window missed.
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
from .redaction import Review, SealedText, reseal_derived, screen_user_text

AppliesTo = Literal["you", "organisation", "both"]

#: PROVISIONAL display labels (DECISIONS.md item N6). Cosmetic; the keys are
#: load-bearing and appear in the model's schema.
_APPLIES_LABELS: dict[AppliesTo, str] = {
    "you": "You have to",
    "organisation": "They have to",
    "both": "Both of you have to",
}


@dataclass(frozen=True)
class Requirement:
    requirement: str
    applies_to: AppliesTo
    deadline: str
    quote: str
    certainty: Certainty

    def render(self) -> str:
        mark = "" if self.certainty == "stated" else f" ({self.certainty})"
        lines = [f"- {self.requirement}{mark}"]
        if self.deadline:
            lines.append(f"  time limit: {self.deadline}")
        lines.append(f'  quoted: "{self.quote}"')
        return "\n".join(lines)


@dataclass(frozen=True)
class Silence:
    topic: str
    why_it_matters: str

    def render(self) -> str:
        return f"- {self.topic}\n  {self.why_it_matters}"


@dataclass(frozen=True)
class ComplianceReading:
    """Frozen, like every other analysis here. The dial cannot soften a rule."""

    requirements: tuple[Requirement, ...]
    silences: tuple[Silence, ...]
    source_name: str
    asked: str = ""
    model: str = ""

    def __len__(self) -> int:
        return len(self.requirements)

    def applying_to(self, who: AppliesTo) -> tuple[Requirement, ...]:
        return tuple(r for r in self.requirements if r.applies_to == who)

    @property
    def deadlines(self) -> tuple[Requirement, ...]:
        """Requirements with a time limit. Usually the most actionable output."""
        return tuple(r for r in self.requirements if r.deadline)

    def render(self) -> str:
        if not self.requirements and not self.silences:
            return "WHAT YOUR DOCUMENT REQUIRES\n\n- (nothing could be established from this document)"

        lines = ["WHAT YOUR DOCUMENT REQUIRES", ""]
        for who, heading in _APPLIES_LABELS.items():
            group = self.applying_to(who)  # type: ignore[arg-type]
            if not group:
                continue
            lines.append(f"{heading}:")
            lines.extend(r.render() for r in group)
            lines.append("")
        if self.silences:
            lines.extend(["WHAT IT DOES NOT SAY", ""])
            lines.extend(s.render() for s in self.silences)
        return "\n".join(lines).rstrip()

    def render_for_user(self) -> str:
        """What the user reads. Carries the shared short disclaimer."""
        return with_short_disclaimer(self.render())


def read(sealed: SealedText, review: Review, facts_text: str, *,
         question: str = "", cfg: Config | None = None) -> ComplianceReading:
    """Read the handbook for what it requires and where it is silent.

    Same four constraints as the other additive modules: the payload is
    ``reseal_derived`` of already-sealed content so it passes the residual scan;
    it leaves through ``bridge.send`` with a minted directive, so no text from
    this module can reach the model except via ``prompts.py``; the owner-settled
    guardrails ride on the system prompt; and the user-facing render carries the
    shared short disclaimer.

    A question is screened under the stricter note policy and travels *inside*
    the payload. A question naming someone unreviewed raises ``UnscreenedName``
    and nothing is sent.
    """
    asked = question.strip()
    body = facts_text
    if asked:
        screened = screen_user_text(asked, review).require_clear()
        body = f"{body}\n\nTHE READER ASKED\n\n{screened}"

    payload = reseal_derived(sealed, body)
    reply = send(payload, directive(Task.COMPLIANCE, effort=Effort.HIGH), cfg=cfg)

    try:
        parsed = json.loads(reply.text)
    except json.JSONDecodeError as exc:
        raise BridgeError(
            "The compliance reading came back in a form that could not be read. "
            "Try again; if it keeps happening, the model may not support "
            "structured output."
        ) from exc

    requirements = tuple(
        Requirement(
            requirement=str(item.get("requirement", "")).strip(),
            applies_to=(
                item.get("applies_to")
                if item.get("applies_to") in _APPLIES_LABELS
                else "both"
            ),  # type: ignore[arg-type]
            deadline=str(item.get("deadline", "")).strip(),
            quote=str(item.get("quote", "")).strip(),
            certainty=item.get("certainty", "unclear"),  # type: ignore[arg-type]
        )
        for item in parsed.get("requirements", [])
        # A requirement with nothing quotable behind it is the failure mode this
        # module most needs to avoid — a rule that sounds right and is not there.
        if str(item.get("requirement", "")).strip() and str(item.get("quote", "")).strip()
    )

    silences = tuple(
        Silence(
            topic=str(item.get("topic", "")).strip(),
            why_it_matters=str(item.get("why_it_matters", "")).strip(),
        )
        for item in parsed.get("silences", [])
        if str(item.get("topic", "")).strip()
    )

    reading = ComplianceReading(
        requirements=requirements,
        silences=silences,
        source_name=sealed.source_name,
        asked=asked,
        model=reply.model,
    )
    log.record(
        "compliance.read",
        source_name=sealed.source_name,
        model=reply.model,
        requirements=len(requirements),
        on_you=len(reading.applying_to("you")),
        on_organisation=len(reading.applying_to("organisation")),
        with_deadlines=len(reading.deadlines),
        silences=len(silences),
        had_question=bool(asked),
    )
    return reading
