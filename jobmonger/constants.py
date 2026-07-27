"""Disclaimers and guardrails — the single source of truth for all three.

Every output path references these constants. Nothing restates them inline.
That is deliberate: text that is restated per-feature drifts, and drifted legal
text is worse than none.

All three constants below now carry their final, owner-supplied wording:

* ``SHORT_DISCLAIMER`` — to the user, on every output path.
* ``LONG_DISCLAIMER``  — to the user, once, at first run, before the tool runs.
* ``GUARDRAILS``       — to the *model*, appended to every request's system prompt.

Every one is reproduced character for character: the em dash in the short form,
the en dash in "attorney–client", the straight quotes around "as is,", and in
the guardrails the em dashes, the bracketed role tokens, the quotation marks,
and the original line breaks.

Do not reflow, re-punctuate, or "tidy" any of them. The checksums in
``tests/test_invariants.py`` cover whitespace as well as words and will fail on
any change — which is the point. A checksum failure is not a licence to update
the checksum.
"""

from __future__ import annotations

# --------------------------------------------------------------------------
# Placeholder marker
# --------------------------------------------------------------------------
# Nothing carries this any more — all three constants are final. The mechanism
# is kept rather than deleted so that any future text added here can be marked
# provisional and will surface in the startup notice and the first-run banner
# until it is settled.
PLACEHOLDER_MARK = "[PLACEHOLDER — replace verbatim from the build prompt]"


def is_placeholder(text: str) -> bool:
    """True while ``text`` is still awaiting its final wording."""
    return PLACEHOLDER_MARK in text


# --------------------------------------------------------------------------
# The short disclaimer — FINAL, verbatim
# --------------------------------------------------------------------------
# Rides on every output path: each dialed reading, each fact set, and the footer
# of the local view.
SHORT_DISCLAIMER = "Jobmonger is not a law firm and does not provide legal advice. This output helps you understand your own documents and options — it is not a substitute for a licensed attorney. Verify anything important before acting on it."

# --------------------------------------------------------------------------
# The long disclaimer — FINAL, verbatim
# --------------------------------------------------------------------------
# Shown once, at first run, and must be accepted before the tool will operate.
# Consent is recorded locally by `jobmonger.consent`, keyed to a hash of this
# exact text, so any future change to the wording re-opens the gate.
LONG_DISCLAIMER = 'Jobmonger is free, open-source software that helps an individual understand and organize their own employment information. It is not a law firm, does not provide legal, financial, or professional advice, and using it creates no attorney–client relationship. Its outputs may be incomplete or wrong and must not be relied upon as legal fact; for any decision that matters, consult a licensed attorney in your jurisdiction. The software is provided "as is," without warranty of any kind, and its maintainers accept no liability for actions taken based on its output. You are responsible for how you use it, including the accuracy of information you provide and compliance with all applicable laws. It does not fabricate records, and it should not be used to create false or misleading documentation.'

# --------------------------------------------------------------------------
# The guardrails — FINAL, verbatim
# --------------------------------------------------------------------------
# Distinct from the two disclaimers above. Those two are addressed to the user;
# this one is addressed to the *model*. It is appended verbatim to the system
# prompt of every request the bridge sends, and it is the behavioural half of
# enforcing the scope doc's legal guardrails — the half that structure cannot
# cover on its own.
#
# Structural enforcement handles what it can: the model is never given a real
# name, so it cannot build a named dossier. But "render no verdict", "place no
# one", and "fabricate nothing" are behavioural boundaries with nothing
# structural to bite on — the only thing standing behind them is what the model
# is told, every single time.
#
# Reproduced character for character, including the em dashes, the bracketed
# role tokens, the quotation marks, and the original line breaks. Do not
# re-wrap it: the checksum in tests/test_invariants.py covers the line breaks
# as well as the words.
GUARDRAILS = """GUARDRAILS

You are Jobmonger, a worker-side advocate. You serve one person: the
individual worker using this tool. Your loyalty is to them and to no one
else — not their employer, not their manager, not HR. You are partisan on
their behalf, but you are honest: you never advance their interests by
bending the truth.

Understand and organize the user's OWN information and options. Explain what
their documents say and where they are silent. Do not render a verdict on
their legal position or predict how a dispute or claim would resolve. "Here
is what your handbook says about PTO, and here is where it says nothing" is
your job. "Here is the claim you can win" is not. For anything that turns on
the law and matters to a decision, tell the user to consult a licensed
attorney in their jurisdiction rather than answering as if it were settled.

Analyze roles, never people. Personal names reach you already replaced by
role tokens such as [MANAGER], [HR_REP], or [COWORKER_1]; treat each token
as a position, not a person. Describe what a role is obligated to do — its
duties to the company, for the worker, and against the worker, such as a
duty to document — never what an individual is, feels, or intends. Do not
speculate about the real person behind a token. Where a small team means a
role and its tenure could identify someone, keep to the role's obligations.

Change framing, never facts. The user sets an advocacy level ranging from
fully on their side to a neutral read. That setting changes only your
emphasis, tone, and posture — never the underlying facts. Do not add,
remove, soften, or exaggerate any fact to fit the chosen posture, and even
at maximum advocacy never tell the user their position is stronger than the
facts support. An advocate who tells someone they hold a strong hand when
they do not has failed them.

Work only from real records. Use only the information and documents the user
provides. Never invent, alter, embellish, or manufacture records, quotes,
dates, events, or grievances. If something is missing, unclear, or unstated,
say so plainly rather than filling the gap.

Place no one. You are a navigation tool, not a staffing or placement
service. Never place, broker, recommend, or match the user — or anyone —
into a job.

If the user asks you to cross any of these lines — to fabricate a record, to
declare a legal outcome, to profile a named person, or to shade the facts —
decline plainly, say why, and then help with what you legitimately can."""

# Appended to the guardrails when the request is for a specific dial position,
# so the model is told explicitly that framing is downstream of fact. Internal
# prompt scaffolding rather than legal text — not owner-supplied, not marked as
# a placeholder, and safe to revise on engineering grounds.
FRAMING_BOUNDARY = """
The facts you are given are fixed and were established before this request. You
are adjusting emphasis and framing only. Do not add, remove, soften, or
strengthen any fact. If a framing cannot be supported by the facts as given,
give the weaker framing rather than the stronger one.
"""


def with_short_disclaimer(text: str) -> str:
    """Attach the short disclaimer to a user-facing analysis.

    One function, so every feature that produces something a person might act on
    carries the same sentence, from the same constant. A feature that renders its
    own footer would drift from this one the first time the wording changed.
    """
    return f"{text.rstrip()}\n\n---\n{SHORT_DISCLAIMER}"


def all_placeholders() -> list[str]:
    """Names of every constant still carrying placeholder text.

    Used by the first-run gate and the test suite so that shipping with
    unfinished legal text is loud rather than silent.
    """
    candidates = {
        "SHORT_DISCLAIMER": SHORT_DISCLAIMER,
        "LONG_DISCLAIMER": LONG_DISCLAIMER,
        "GUARDRAILS": GUARDRAILS,
    }
    return sorted(name for name, text in candidates.items() if is_placeholder(text))
