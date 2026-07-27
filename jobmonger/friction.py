"""[DECISION-FRICTION] v1 seed — restate the key fact, then confirm.

The scope doc defers the full feature and notes the v1 seed "could ride along
cheaply." It does. What is here is only the seed:

    before a consequential action, restate the one fact the decision rests on,
    and have the user confirm against it.

What is deliberately *not* here: a countdown timer, and an active-recall check.
Both belong to the later version (DECISIONS.md item P9). The reasoning from the
scope doc is worth keeping in view while implementing anything more: a bare
timer trains people to wait it out rather than read. Elapsed time does not
produce comprehension; making the person do something small with the content
does. So if a timer is added later, it is the garnish — the recall check is the
load-bearing part.

Currently wired to exactly one trigger: moving the dial to maximum advocacy.
Blanket friction is explicitly not the design; that is where the wait-it-out
reflex sets in fastest.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import log
from .facts import FactSet


@dataclass(frozen=True)
class Restatement:
    """What to show before a consequential action, and what it is guarding."""

    action: str
    headline: str
    detail: str
    #: The fact the user is being asked to hold in mind. May be absent.
    anchor: str = ""

    def render(self) -> str:
        parts = [self.headline, "", self.detail]
        if self.anchor:
            parts.extend(["", f"Worth holding in mind: {self.anchor}"])
        return "\n".join(parts)


def for_max_advocacy(fact_set: FactSet) -> Restatement:
    """The restatement shown before the dial moves to its strongest setting.

    Anchored on a real fact from this document rather than a generic warning.
    A caution the reader has seen ten times is wallpaper; a caution that quotes
    the unclear clause in *their* handbook is not.
    """
    weak = [f for f in fact_set.facts if f.certainty in ("implied", "unclear")]
    anchor = ""
    if weak:
        anchor = f"\"{weak[0].statement}\" is {weak[0].certainty}, not established."
    elif fact_set.gaps:
        anchor = f"Your document does not address: {fact_set.gaps[0]}"

    return Restatement(
        action="dial.max_advocacy",
        headline="This setting argues your side as hard as the facts allow.",
        detail=(
            "The facts do not change here — the same ones apply at every setting, "
            "including the ones that do not help you. What changes is how they are "
            "put. That is useful for understanding your strongest ground. It is a "
            "poor basis for deciding what to do next, because the other side will "
            "read the same facts the other way."
        ),
        anchor=anchor,
    )


def confirm(restatement: Restatement, accepted: bool) -> bool:
    """Record the user's answer. Returns whether to proceed."""
    log.record(
        "friction.confirmed" if accepted else "friction.declined",
        guarding=restatement.action,
        had_anchor=bool(restatement.anchor),
    )
    return accepted
