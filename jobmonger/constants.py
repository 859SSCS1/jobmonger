"""Disclaimers and guardrails — the single source of truth for all three.

Every output path references these constants. Nothing restates them inline.
That is deliberate: text that is restated per-feature drifts, and drifted legal
text is worse than none.

Status:

* ``SHORT_DISCLAIMER`` — final, verbatim from the build spec.
* ``LONG_DISCLAIMER``  — final, verbatim from the build spec.
* ``GUARDRAILS``       — still PLACEHOLDER, awaiting wording. See DECISIONS.md B1.

The two disclaimers below are reproduced character for character, including the
em dash in the short form, the en dash in "attorney–client", and the straight
quotes around "as is,". Do not reflow, re-punctuate, or "tidy" them — the
checksums in ``tests/test_invariants.py`` will fail if any character changes,
which is the point.
"""

from __future__ import annotations

# --------------------------------------------------------------------------
# Placeholder marker
# --------------------------------------------------------------------------
# Present in any string below still awaiting its final wording.
# `is_placeholder()` checks for it, the first-run gate surfaces it, and the test
# suite asserts on it. Delete the marker from a string only when inserting the
# final wording.
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
# The guardrails — PLACEHOLDER, awaiting wording (DECISIONS.md item B1)
# --------------------------------------------------------------------------
# Distinct from the two disclaimers above. Those two are addressed to the user;
# this one is addressed to the *model*. It is appended verbatim to the system
# prompt of every request the bridge sends, and it is the behavioural half of
# enforcing the scope doc's legal guardrails — the half that structure cannot
# cover on its own.
#
# Structural enforcement handles what it can: the model is never given a real
# name, so it cannot build a named dossier. But "render no legal verdict",
# "place no one in a job", and "fabricate nothing" are behavioural boundaries
# with nothing structural to bite on — the only thing standing behind them is
# what the model is told, every single time.
#
# The text below is a working placeholder so the mechanism runs end to end. It
# is not owner-approved wording and must be replaced.
GUARDRAILS = f"""{PLACEHOLDER_MARK}

You are helping one person understand information they already possess. Hold to
these boundaries in every response:

  - Explain what the person's own documents say. Do not speculate about facts
    not present in them.
  - Do not render a legal verdict. You may explain what a document says and
    what it appears to require; you may not conclude that a law or policy was
    broken, that a claim would succeed, or that someone is liable.
  - Do not evaluate anyone for hiring, placement, promotion, or termination.
  - Do not build a profile of any named individual. You will only ever see
    neutral role labels; do not attempt to infer, reconstruct, or ask for the
    identity behind one.
  - Describe roles by their duties, never by the character or motives of the
    person holding them.
  - Do not fabricate. If the documents do not answer a question, say that
    plainly. An honest gap is more useful to this person than a confident
    guess, because they may act on what you tell them.
"""

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
