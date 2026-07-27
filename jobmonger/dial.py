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
from .bridge import stream
from .config import Config
from .constants import FRAMING_BOUNDARY
from .facts import FactSet, as_sealed
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

#: What each position asks the model to do. The facts are identical at all five.
_POSTURE: dict[int, str] = {
    0: (
        "Present the facts plainly, as a neutral reader would. Give the reading "
        "most likely to be correct, whether or not it favours the reader. Where "
        "the facts cut against them, say so directly."
    ),
    1: (
        "Present the facts plainly, but where something genuinely helps the "
        "reader, make sure they notice it. Do not soften what cuts against them."
    ),
    2: (
        "Give a balanced reading. Set out what helps the reader and what does "
        "not, with roughly equal care, and say which is stronger."
    ),
    3: (
        "Read these facts the way an advocate for the reader would: lead with "
        "what supports their position and explain why it matters. Still state "
        "what cuts against them, clearly, so they are not surprised later."
    ),
    4: (
        "Argue the reader's position as strongly as the facts honestly allow. "
        "Lead with their strongest ground and develop it fully. You must still "
        "state what cuts against them — an advocate who hides the weak point "
        "from their own client is no use to them — but you may put it last and "
        "put it briefly."
    ),
}

# Attached at every position. The dial's whole legitimacy rests on this holding.
_INVARIANT_RULE = """
The facts above are fixed. They were established before this request and are
identical at every setting the reader could have chosen. You are adjusting
emphasis, order, and tone. You are not adjusting what is true.

Specifically, at every setting:
  - Do not add a fact that is not listed above.
  - Do not drop a fact that cuts against the reader.
  - Do not upgrade an "implied" or "unclear" fact into a settled one.
  - Do not restate a gap as though the document had answered it.
"""


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


def _instruction(position: int, has_question: bool) -> str:
    """Build the instruction. Takes a flag, never the user's words.

    The signature is deliberate. This function used to interpolate the question
    directly, which put user-typed text into ``bridge.send``'s ``instruction``
    argument — a parameter that is not sealed and never passes the residual
    scan. Taking a bool makes that mistake impossible to repeat here: there is
    no user string in scope to accidentally include.
    """
    parts = [_POSTURE[clamp(position)], _INVARIANT_RULE.strip(), FRAMING_BOUNDARY.strip()]
    if has_question:
        parts.append(
            "The reader's question appears at the end of the material below, under "
            "THE READER ASKED. Answer it from the facts above. If the facts do not "
            "answer it, say so plainly and say what would be needed to answer it."
        )
    else:
        parts.append(
            "The reader has not asked anything specific. Give them the reading of "
            "these facts that would be most useful to someone in their position."
        )
    parts.append(
        "Refer to people only by the role labels shown, such as [MANAGER]. Never "
        "speculate about who they are."
    )
    return "\n\n".join(parts)


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
        _instruction(position, bool(asked)),
        cfg=cfg,
        effort="high",
        system_extra=(
            "You are writing for one person about their own situation. They may be "
            "under real stress and may act on what you say. Be clear, be concrete, "
            "and do not pad. Never claim more certainty than the facts carry."
        ),
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
