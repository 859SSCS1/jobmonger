"""Every word this tool ever says to a model, in one file.

This exists because of DECISIONS.md item X6. The seal protects ``bridge.send``'s
*payload* parameter. Every other argument was an ordinary string, so the dial
could — and did — interpolate a user's typed question into the instruction and
sail past the residual scan. Fixing that call site fixed that bug; it did
nothing to stop the next module doing the same thing.

So the strings are gone. ``bridge`` no longer accepts instruction text from
anyone. A module names a ``Task`` and passes controlled values — an enum, a
bool, an integer in range — and the text is assembled *here*, from constants
defined in this file. A module that wants to say something new to the model has
to add it here, in a file whose entire purpose is to be read.

Two consequences worth stating plainly:

* Auditing what this tool tells a model is reading one file, not grepping a
  package for prompt fragments.
* A future module physically cannot reintroduce X6 by adding an argument,
  because ``bridge`` has no parameter that would carry it.

The owner-supplied guardrails are **not** here — they live in ``constants.py``
with the disclaimers, and ``bridge`` attaches them to every request. This file
holds only the working instructions.
"""

from __future__ import annotations

from enum import Enum

from .constants import FRAMING_BOUNDARY


class Task(str, Enum):
    """The closed set of things this tool asks a model to do."""

    FACT_EXTRACTION = "fact_extraction"
    DIAL_READING = "dial_reading"
    ROLE_MAP = "role_map"
    TENURE = "tenure"
    COMPLIANCE = "compliance"
    CONNECTIVITY_PROBE = "connectivity_probe"


# --------------------------------------------------------------------------
# [FACT-LAYER]
# --------------------------------------------------------------------------

FACT_EXTRACTION = """
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

FACT_EXTRACTION_NOTE = (
    "You are extracting a neutral, complete record of what a document "
    "establishes. This record will be reused unchanged at every framing "
    "setting the reader chooses, so it must not lean in any direction."
)

FACT_SCHEMA = {
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


# --------------------------------------------------------------------------
# [ADVOCACY-DIAL]
# --------------------------------------------------------------------------

DIAL_POSTURES: dict[int, str] = {
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

DIAL_INVARIANT = """
The facts above are fixed. They were established before this request and are
identical at every setting the reader could have chosen. You are adjusting
emphasis, order, and tone. You are not adjusting what is true.

Specifically, at every setting:
  - Do not add a fact that is not listed above.
  - Do not drop a fact that cuts against the reader.
  - Do not upgrade an "implied" or "unclear" fact into a settled one.
  - Do not restate a gap as though the document had answered it.
"""

DIAL_WITH_QUESTION = (
    "The reader's question appears at the end of the material below, under "
    "THE READER ASKED. Answer it from the facts above. If the facts do not "
    "answer it, say so plainly and say what would be needed to answer it."
)

DIAL_WITHOUT_QUESTION = (
    "The reader has not asked anything specific. Give them the reading of "
    "these facts that would be most useful to someone in their position."
)

DIAL_ROLE_LABELS = (
    "Refer to people only by the role labels shown, such as [MANAGER]. Never "
    "speculate about who they are."
)

DIAL_NOTE = (
    "You are writing for one person about their own situation. They may be "
    "under real stress and may act on what you say. Be clear, be concrete, "
    "and do not pad. Never claim more certainty than the facts carry."
)


# --------------------------------------------------------------------------
# [ROLE-MAP]
# --------------------------------------------------------------------------

ROLE_MAP = """
Below are the facts established from a document, and a list of the role labels
that appear in it. For each role label, set out what the role is obligated to
do — not what the person holding it is like.

For each duty give:
  - `direction`: "to_company" if the duty runs to the organisation,
    "for_user" if it runs to the reader's benefit, "against_user" if
    performing it properly works against the reader's interest.
  - `duty`: the obligation, in one plain sentence.
  - `quote`: the shortest exact span from the material that supports it, copied
    verbatim. If nothing supports it, do not include the duty.
  - `certainty`: "stated" if the material says it outright, "implied" if it
    follows from what is said, "unclear" if it is gestured at but unsettled.

The "against_user" direction is the important one and the easiest to get wrong.
It does not mean hostility. A duty to document concerns, to enforce a policy
consistently, or to escalate a repeated issue may be entirely proper and still
cut against this reader. Name those plainly. Do not soften them, and do not
dress them up as malice either.

Rules:
  - Describe the role, never the individual. You are seeing labels such as
    [MANAGER] or [HR_REP]; treat each as a position that anyone could hold.
  - Do not speculate about the character, feelings, motives, or intentions of
    whoever holds a role.
  - Do not invent duties that the material does not support. A role with
    nothing established about it should come back with no duties rather than
    plausible-sounding ones.
  - Do not state or imply that any duty was breached. What a role is obligated
    to do is in scope; whether someone failed to do it is not.
"""

ROLE_MAP_NOTE = (
    "You are mapping obligations attached to positions, not judging people. "
    "Every label you see stands for a role that anyone could hold."
)

ROLE_MAP_SCHEMA = {
    "type": "object",
    "properties": {
        "roles": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "token": {"type": "string"},
                    "duties": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "direction": {
                                    "type": "string",
                                    "enum": ["to_company", "for_user", "against_user"],
                                },
                                "duty": {"type": "string"},
                                "quote": {"type": "string"},
                                "certainty": {
                                    "type": "string",
                                    "enum": ["stated", "implied", "unclear"],
                                },
                            },
                            "required": ["direction", "duty", "quote", "certainty"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["token", "duties"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["roles"],
    "additionalProperties": False,
}


# --------------------------------------------------------------------------
# [TENURE]
# --------------------------------------------------------------------------

TENURE = """
Below are the facts established from a document, and — for some role labels —
roughly how long that role has been in place, given as a band rather than an
exact figure.

Length of service tends to confer things a job title does not: knowing which
rules get enforced and which are ignored, knowing who to ask, being believed by
default, having relationships that predate the current structure. Set out, for
each role with a tenure band, what that band plausibly means for the reader's
situation.

For each observation give:
  - `observation`: what the tenure plausibly means here, in one plain sentence.
  - `basis`: what you are inferring it from — the tenure band, something in the
    facts, or both.
  - `certainty`: this will almost always be "implied" or "unclear". Use
    "stated" only if the material says it outright.

This is inference, not fact, and the reader must be able to tell the difference.
Say "likely", "often", "may" — and mean it. A confident-sounding guess about
someone's standing is worse than useless to a person deciding what to do.

Rules:
  - Reason about what tenure confers on a *role*, never about the individual.
    "A role held this long usually knows which exceptions get approved" is in
    scope. "This person thinks they are untouchable" is not.
  - Do not speculate about character, motives, loyalty, or intentions.
  - Do not infer anything about the reader's own standing that the facts do not
    support, in either direction. Do not reassure and do not alarm.
  - Where a tenure band and a role together could point at one identifiable
    person, keep to what the role's position implies and go no further.
  - If a tenure band supports nothing useful, return no observation for it
    rather than a plausible-sounding one.
"""

TENURE_NOTE = (
    "You are reasoning about what length of service tends to confer on a "
    "position. Everything you produce here is inference and must read as such."
)

TENURE_SCHEMA = {
    "type": "object",
    "properties": {
        "observations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "token": {"type": "string"},
                    "observation": {"type": "string"},
                    "basis": {"type": "string"},
                    "certainty": {"type": "string", "enum": ["stated", "implied", "unclear"]},
                },
                "required": ["token", "observation", "basis", "certainty"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["observations"],
    "additionalProperties": False,
}


# --------------------------------------------------------------------------
# [COMPLIANCE]
# --------------------------------------------------------------------------

COMPLIANCE = """
Below are the facts established from a document — the reader's own handbook,
policy, or agreement — and, if they asked something specific, their question.

Set out what the document *requires*, and where it is *silent*. The reader is
trying to understand and keep to their own rules. That is the whole job.

For each requirement give:
  - `requirement`: what the document requires, in one plain sentence.
  - `applies_to`: "you" if it is an obligation on the reader, "organisation" if
    it is an obligation on their employer, "both" if it runs both ways.
  - `deadline`: any time limit attached to it, quoted or paraphrased tightly —
    "within ten working days of the decision". Empty string if there is none.
    Timing is often the most actionable thing a handbook contains, so do not
    bury it inside the requirement text.
  - `quote`: the shortest exact span from the material that supports it, copied
    verbatim. If you cannot quote it, do not include the requirement.
  - `certainty`: "stated" if the document says it outright, "implied" if it
    follows from what is said, "unclear" if it is gestured at but unsettled.

For each silence give:
  - `topic`: what the document does not address.
  - `why_it_matters`: one sentence on why a reader in this situation would have
    expected it to. Silences are not filler here — a rule that does not exist is
    something the reader needs to know does not exist.

Hold this line, and hold it firmly. It is the easiest one in this tool to cross:

  - Say what the document requires. Never say whether anyone has met it. Not the
    reader, not their employer, not anyone.
  - Do not state or imply that a requirement was breached, missed, satisfied, or
    complied with. "The policy requires notice within five days" is your job.
    "You gave notice late" and "they failed to respond in time" are not, even if
    the material appears to show it.
  - Do not assess the reader's legal position, characterise anything as a claim,
    a grievance, a defence, or a violation, or predict how any dispute would go.
  - Do not advise on strategy. Explaining what a rule says is not the same as
    telling someone what to do about it, and only the first is yours.
  - Where the document is silent, say so plainly. Do not fill the gap with what
    such documents usually say, what the law generally requires, or what would
    be reasonable. A confident guess about a rule that does not exist is the
    most damaging thing you could produce here.
"""

COMPLIANCE_NOTE = (
    "You are helping one person understand their own handbook. You explain what "
    "it requires and where it says nothing. You do not judge whether anyone has "
    "followed it."
)

COMPLIANCE_SCHEMA = {
    "type": "object",
    "properties": {
        "requirements": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "requirement": {"type": "string"},
                    "applies_to": {"type": "string", "enum": ["you", "organisation", "both"]},
                    "deadline": {"type": "string"},
                    "quote": {"type": "string"},
                    "certainty": {"type": "string", "enum": ["stated", "implied", "unclear"]},
                },
                "required": ["requirement", "applies_to", "deadline", "quote", "certainty"],
                "additionalProperties": False,
            },
        },
        "silences": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string"},
                    "why_it_matters": {"type": "string"},
                },
                "required": ["topic", "why_it_matters"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["requirements", "silences"],
    "additionalProperties": False,
}


# --------------------------------------------------------------------------
# Connectivity probe
# --------------------------------------------------------------------------

CONNECTIVITY_PROBE = "Reply with the single word: ready"


# --------------------------------------------------------------------------
# Assembly
# --------------------------------------------------------------------------

_INSTRUCTIONS: dict[Task, str] = {
    Task.FACT_EXTRACTION: FACT_EXTRACTION,
    Task.ROLE_MAP: ROLE_MAP,
    Task.TENURE: TENURE,
    Task.COMPLIANCE: COMPLIANCE,
    Task.CONNECTIVITY_PROBE: CONNECTIVITY_PROBE,
}

_NOTES: dict[Task, str] = {
    Task.FACT_EXTRACTION: FACT_EXTRACTION_NOTE,
    Task.DIAL_READING: DIAL_NOTE,
    Task.ROLE_MAP: ROLE_MAP_NOTE,
    Task.TENURE: TENURE_NOTE,
    Task.COMPLIANCE: COMPLIANCE_NOTE,
    Task.CONNECTIVITY_PROBE: "",
}

_SCHEMAS: dict[Task, dict | None] = {
    Task.FACT_EXTRACTION: FACT_SCHEMA,
    Task.DIAL_READING: None,
    Task.ROLE_MAP: ROLE_MAP_SCHEMA,
    Task.TENURE: TENURE_SCHEMA,
    Task.COMPLIANCE: COMPLIANCE_SCHEMA,
    Task.CONNECTIVITY_PROBE: None,
}


def _dial_instruction(posture: int, has_question: bool) -> str:
    """Assemble a dial instruction from fixed fragments only.

    Both arguments are controlled: an integer the caller has already clamped to
    0-4, and a bool. Neither can carry text.
    """
    return "\n\n".join(
        [
            DIAL_POSTURES[posture],
            DIAL_INVARIANT.strip(),
            FRAMING_BOUNDARY.strip(),
            DIAL_WITH_QUESTION if has_question else DIAL_WITHOUT_QUESTION,
            DIAL_ROLE_LABELS,
        ]
    )


def build(task: Task, *, posture: int = 0, has_question: bool = False) -> tuple[str, str, dict | None]:
    """Return ``(instruction, system_note, schema)`` for ``task``.

    The only inputs are a ``Task`` member, an integer, and a bool. There is no
    parameter here that a caller could use to pass text of their own — which is
    the entire point of this module.
    """
    if not isinstance(task, Task):
        raise TypeError(f"Expected a prompts.Task member, got {type(task).__name__}.")

    if task is Task.DIAL_READING:
        if posture not in DIAL_POSTURES:
            raise ValueError(f"Dial posture must be 0-4, got {posture!r}.")
        instruction = _dial_instruction(posture, bool(has_question))
    else:
        instruction = _INSTRUCTIONS[task]

    return instruction, _NOTES[task], _SCHEMAS[task]


def known_texts() -> frozenset[str]:
    """Every instruction and note this module can produce.

    Used by the boundary test to assert that nothing else ever reaches the
    model. Dial instructions are enumerated across all ten valid combinations.
    """
    texts = set(_INSTRUCTIONS.values()) | set(_NOTES.values())
    for posture in DIAL_POSTURES:
        for has_question in (True, False):
            texts.add(_dial_instruction(posture, has_question))
    return frozenset(texts)
