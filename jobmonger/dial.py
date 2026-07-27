"""[ADVOCACY-DIAL] — framing laid over a fixed fact set. Emphasis only.

The dial changes how findings are put, never what they are. It receives the
frozen ``FactSet`` and has no access to the document, so it cannot re-read the
source to find a more convenient reading. See ``facts.py`` for why that
separation is structural rather than a matter of prompt discipline.

Positions are integers 0-4. The integers are load-bearing — they are what gets
stored, logged, and compared. The display labels are PROVISIONAL and live in
one dict below (DECISIONS.md item N3); renaming them touches nothing else.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

from . import log
from .bridge import Effort, directive, stream
from .config import Config
from .facts import FactSet, as_sealed
from .prompts import Task
from .redaction import Review, SealedText, reseal_derived, screen_user_text

MIN_POSITION = 0
MAX_POSITION = 4

#: PROVISIONAL display labels — DECISIONS.md item N3. Cosmetic only.
_PROVISIONAL_LABELS: dict[int, str] = {
    0: "Straight reading",
    1: "Mostly neutral",
    2: "Balanced",
    3: "On your side",
    4: "Fully on your side",
}

# The posture wording, the invariance rule, and the framing boundary live in
# prompts.py. Only the display labels above stay here — those are cosmetic
# and never reach the model. See DECISIONS.md item X8.


def label(position: int) -> str:
    """The display label for a position. PROVISIONAL — see DECISIONS.md N3."""
    return _PROVISIONAL_LABELS.get(clamp(position), _PROVISIONAL_LABELS[2])


def clamp(position: int) -> int:
    return max(MIN_POSITION, min(MAX_POSITION, int(position)))


def positions() -> list[tuple[int, str]]:
    """Every position with its label, for rendering the control."""
    return [(index, _PROVISIONAL_LABELS[index]) for index in range(MIN_POSITION, MAX_POSITION + 1)]


def is_consequential(position: int) -> bool:
    """Whether moving here should trigger the restate-and-confirm step.

    Only the top of the dial, for now. That is where a reader is most likely to
    over-trust a framing — precisely when they are angriest and least inclined
    to check. PROVISIONAL trigger set; see DECISIONS.md item P9.
    """
    return clamp(position) == MAX_POSITION


@dataclass(frozen=True)
class Reading:
    """A rendered framing, and the position it was rendered at.

    Carries its position so that a reading can never be displayed without the
    setting that produced it. A framing detached from its dial position is the
    one output this tool must never produce.
    """

    position: int
    text: str
    fact_count: int

    @property
    def position_label(self) -> str:
        return label(self.position)


def render(fact_set: FactSet, sealed: SealedText, position: int, *,
           question: str = "", review: Review | None = None,
           cfg: Config | None = None) -> Iterator[str]:
    """Stream a reading of the facts at ``position``.

    Note what is passed to the model: the frozen facts, not the document.
    ``sealed`` is used only to carry the redaction map forward so the reply can
    have real names restored locally afterwards. The framing step never sees
    the source text.

    A question, if there is one, is screened against ``review`` and then carried
    *inside the sealed payload* rather than in the instruction — so it passes
    the same residual scan the facts do. Asking a question with an unreviewed
    name in it raises ``UnscreenedName`` and sends nothing.
    """
    position = clamp(position)
    asked = question.strip()
    body = fact_set.render()

    if asked:
        if review is None:
            raise ValueError(
                "A question has to be screened before it can be sent, which needs "
                "the Review it belongs to. Pass review=..."
            )
        screened = screen_user_text(asked, review).require_clear()
        payload = reseal_derived(sealed, f"{body}\n\nTHE READER ASKED\n\n{screened}")
    else:
        payload = as_sealed(fact_set, sealed)

    log.record(
        "dial.render",
        source_name=fact_set.source_name,
        position=position,
        position_label=label(position),
        fact_count=len(fact_set),
        has_question=bool(asked),
    )

    yield from stream(
        payload,
        directive(Task.DIAL_READING, posture=position,
                  has_question=bool(asked), effort=Effort.HIGH),
        cfg=cfg,
    )


def render_text(fact_set: FactSet, sealed: SealedText, position: int, *,
                question: str = "", review: Review | None = None,
                cfg: Config | None = None) -> Reading:
    """Collect a full reading. Convenience wrapper over ``render``."""
    chunks = list(render(fact_set, sealed, position, question=question,
                         review=review, cfg=cfg))
    return Reading(
        position=clamp(position),
        text=sealed.restore("".join(chunks).strip()),
        fact_count=len(fact_set),
    )
