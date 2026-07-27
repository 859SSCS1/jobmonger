"""[FACT-LAYER] — the facts, extracted once, identical at every dial position.

This is the second-hardest part of the tool after redaction, and the failure
mode is subtle: if each dial position re-reads the document, the model will
quietly find *different facts* at different settings. Maximum advocacy will
surface the helpful clause and skim the unhelpful one. The user would see a
dial that appears to change only tone, while it was actually changing what they
were told was true. That is the exact betrayal this product exists to not
commit.

So invariance here is structural, not prompted:

    facts are extracted ONCE, from the document, at a fixed neutral setting
      ↓
    the result is frozen
      ↓
    every dial position receives the frozen facts and NEVER the document

``FactSet`` is immutable and ``dial.py`` has no access to the source text. A
dial position physically cannot reach the document to re-read it. The prompt
also says not to change the facts — but the prompt is the second line of
defence, not the first.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

from . import log
from .bridge import BridgeError, Reply, send
from .config import Config
from .redaction import SealedText, reseal_derived

Certainty = Literal["stated", "implied", "unclear"]

# Extraction runs at a fixed neutral instruction regardless of where the user
# has the dial. The dial is applied afterwards, to these results.
_EXTRACTION_INSTRUCTION = """
Read the document below and extract what it actually establishes.

For each fact:
  - `statement`: what the document establishes, in one plain sentence.
  - `quote`: the shortest exact span from the document that supports it. Copy it
    verbatim. If you cannot quote it, do not include the fact.
  - `certainty`: "stated" if the document says it outright, "implied" if it
    follows from what is said, "unclear" if the document gestures at it without
    settling it.

Then list `gaps`: things a reader would reasonably expect this document to
address that it does not. Gaps matter as much as facts here — a person deciding
what to do next needs to know what their document leaves unanswered.

Be neutral and complete. Do not interpret, advise, or take a side. Include facts
that are unhelpful to the reader exactly as readily as ones that help them; a
later step will handle framing, and it can only be honest if this step was.

Names have already been replaced with role labels such as [MANAGER] or
[HR_REP]. Use those labels as they appear. Do not speculate about who they are.
"""

_FACT_SCHEMA = {
    "type": "object",
    "properties": {
        "facts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "statement": {"type": "string"},
                    "quote": {"type": "string"},
                    "certainty": {"type": "string", "enum": ["stated", "implied", "unclear"]},
                },
                "required": ["statement", "quote", "certainty"],
                "additionalProperties": False,
            },
        },
        "gaps": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["facts", "gaps"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class Fact:
    statement: str
    quote: str
    certainty: Certainty

    def render(self) -> str:
        mark = {"stated": "", "implied": " (implied)", "unclear": " (unclear)"}[self.certainty]
        return f"- {self.statement}{mark}\n  quoted: \"{self.quote}\""


@dataclass(frozen=True)
class FactSet:
    """The frozen fact set. Every dial position sees exactly this and no more.

    Frozen, with tuple fields rather than lists, so a dial position cannot
    mutate it in place. The immutability is the guarantee; the type checker and
    the runtime both enforce it.
    """

    facts: tuple[Fact, ...]
    gaps: tuple[str, ...]
    source_name: str
    model: str = ""

    def __len__(self) -> int:
        return len(self.facts)

    def render(self) -> str:
        """The exact text handed to every dial position. One rendering, always."""
        lines = ["ESTABLISHED FACTS", ""]
        if self.facts:
            lines.extend(fact.render() for fact in self.facts)
        else:
            lines.append("- (the document established nothing that could be quoted)")
        if self.gaps:
            lines.extend(["", "WHAT THE DOCUMENT DOES NOT ADDRESS", ""])
            lines.extend(f"- {gap}" for gap in self.gaps)
        return "\n".join(lines)

    def certainty_counts(self) -> dict[str, int]:
        counts = {"stated": 0, "implied": 0, "unclear": 0}
        for fact in self.facts:
            counts[fact.certainty] += 1
        return counts


def extract(sealed: SealedText, *, cfg: Config | None = None) -> FactSet:
    """Extract the fact set. Called once per document, before any dial position.

    Not streamed, deliberately: the fact set must be complete before any
    framing renders against it. Streaming would let a dial position begin
    against a partial fact set, which breaks the invariant in precisely the way
    that matters. See DECISIONS.md item X2.
    """
    reply: Reply = send(
        sealed,
        _EXTRACTION_INSTRUCTION,
        cfg=cfg,
        effort="high",
        schema=_FACT_SCHEMA,
        system_extra=(
            "You are extracting a neutral, complete record of what a document "
            "establishes. This record will be reused unchanged at every framing "
            "setting the reader chooses, so it must not lean in any direction."
        ),
    )

    try:
        payload = json.loads(reply.text)
    except json.JSONDecodeError as exc:
        raise BridgeError(
            "The model's fact extraction could not be read. Try again; if it keeps "
            "happening, the model may not support structured output."
        ) from exc

    facts = tuple(
        Fact(
            statement=str(item.get("statement", "")).strip(),
            quote=str(item.get("quote", "")).strip(),
            certainty=item.get("certainty", "unclear"),  # type: ignore[arg-type]
        )
        for item in payload.get("facts", [])
        if str(item.get("statement", "")).strip()
    )
    gaps = tuple(str(gap).strip() for gap in payload.get("gaps", []) if str(gap).strip())

    fact_set = FactSet(
        facts=facts, gaps=gaps, source_name=sealed.source_name, model=reply.model
    )
    log.record(
        "facts.extracted",
        source_name=sealed.source_name,
        model=reply.model,
        fact_count=len(facts),
        gap_count=len(gaps),
        **fact_set.certainty_counts(),
    )
    return fact_set


def as_sealed(fact_set: FactSet, parent: SealedText) -> SealedText:
    """Package the frozen facts for sending, in place of the document.

    This is what enforces the invariant at the boundary. The dial calls
    ``bridge`` with *this* object — facts only — so there is no document in the
    request for a framing step to reinterpret. The re-scan inside
    ``reseal_derived`` also confirms the model did not somehow return a real
    name it was never shown.
    """
    return reseal_derived(parent, fact_set.render())
