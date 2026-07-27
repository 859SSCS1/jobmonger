# Open Decisions

Every naming and policy question raised during the build, collected here rather
than interrupting it. Nothing in this list is settled; every corresponding
implementation carries a `PROVISIONAL` marker pointing back to the item number.

**All product, module, and feature naming is reserved to the owner.** Where the
build needed a name to function, it uses the neutral placeholder labels from the
Stage 1 scope doc (`[REDACTION-GATE]`, `[FACT-LAYER]`, …) or a numbered
identifier. No candidate names are proposed anywhere in this repository.

---

## Settled

### B1 · All legal text is final — **closed 2026-07-27**

| Constant | Audience | Status |
|---|---|---|
| `SHORT_DISCLAIMER` | User, every output | **Final**, verbatim |
| `LONG_DISCLAIMER` | User, first run | **Final**, verbatim |
| `GUARDRAILS` | Model, every request | **Final**, verbatim |

All three are reproduced character for character and locked by SHA-256 in
`tests/test_invariants.py`. The guardrails checksum covers the **original line
breaks** as well as the words, so re-wrapping the paragraphs fails the build
even though no word changed — deliberate, since a formatter would otherwise
re-flow prompt text without anyone noticing.

**A checksum failure is not a licence to update the checksum.** It means the
text changed. Restore it, or — if the owner genuinely supplied new wording —
replace text and checksum together in one commit.

Beyond the checksums, named assertions cover the characters most likely to be
"corrected" by an editor: the em dash in the short disclaimer, the en dash in
"attorney–client", the straight quotes around `"as is,"`, the eight em dashes
in the guardrails, the bracketed role tokens `[MANAGER]` / `[HR_REP]` /
`[COWORKER_1]`, and the straight apostrophes in "user's" and "role's".

`GUARDRAILS` is appended to the system prompt of **every** model request,
including the connectivity probe, which carries no user content. That path was
originally exempt; it no longer is, because "every request" is easier to verify
and to keep true than "every request except the one we judged harmless". A
static test walks `bridge.py` and fails if any request builder omits it.

The placeholder mechanism (`PLACEHOLDER_MARK`, `all_placeholders()`, the startup
notice, the first-run banner) is retained rather than deleted, so any text added
here later can be marked provisional and will surface until it is settled.
Nothing currently carries the mark, and the startup notice no longer fires.

### B2 · Legal review of the guardrail phrasing

Beyond the disclaimer wording: the guardrails (understands the user's own
information; renders no legal verdict; places no one in a job; builds no named
dossiers; fabricates nothing) are enforced structurally *and* restated in every
system prompt. The enforcement is built. The phrasing is the owner's.

---

## Naming — reserved to the owner

### N1 · Product, module, and feature names
Scope-doc placeholder labels are used throughout. The Python package is named
`jobmonger` after the existing repository, not as a proposed product name.

### N2 · Role tokens
Currently `[MANAGER]`, `[HR_REP]`, `[COWORKER_1]`, `[EXECUTIVE]`, `[REPORT_1]`,
`[EXTERNAL_1]`, `[SELF]`, `[EMPLOYER]`. Defined in one place —
`jobmonger/redaction.py::ROLE_TOKENS` — so the vocabulary can be revised
wholesale. See also P3.

### N3 · Advocacy dial position labels
Positions are integers 0–4 in the API and storage. Display labels live in a
single dict, `jobmonger/dial.py::_PROVISIONAL_LABELS`, and are provisional.
Numbers are load-bearing; labels are cosmetic and safe to rename.

### N4 · The local view's window title and heading
Currently the repository name. One constant, `jobmonger/ui/view.py::APP_TITLE`.

### N5 · Role-map direction headings
The three columns are keyed internally as `to_company` / `for_user` /
`against_user` — those are load-bearing and appear in the model's schema. Their
display headings are provisional and live in one dict,
`jobmonger/rolemap.py::_DIRECTION_LABELS`: currently "Owes the organisation",
"Owes you", "Works against you". The third is the one worth your eye — it has to
convey *duty that cuts against you* without reading as accusation.

---

## Policy — provisional defaults in use

### P1 · Interface shape *(scope doc decision #2)*
**In use:** UI-agnostic core library + a local view on `127.0.0.1`. A CLI is
*not* built; the core is structured so one is a thin wrapper whenever wanted.

Rationale: the redaction confirm step requires seeing a flagged name inside its
sentence and clicking to correct it, which a terminal cannot do well; and the
audit story is carried by the library plus `tests/test_egress.py`, not by a CLI.

### P2 · What counts as a "name" *(scope doc decision #3)*
**In use:** people, emails, phone numbers, and employee IDs are redacted by
default. Company name and the user's own name are **optional toggles**, default
off. `jobmonger/redaction.py::DetectionPolicy`.

### P3 · Role-token granularity
Whether a second manager becomes `[MANAGER_2]` or collapses into `[MANAGER]`.
**In use:** distinct individuals always get distinct numbered tokens, because
collapsing them silently merges two people's conduct into one narrative. This is
a correctness choice more than a naming one, but the owner may disagree.

### P4 · Low-headcount re-identification threshold — **settled 2026-07-27 at 8**
**Owner-supplied:** warn when a role sits on a team of **8 or fewer**. Warn only
— never block, never silently mask further. One constant,
`jobmonger/redaction.py::REID_TEAM_SIZE_THRESHOLD`, consumed by `[ROLE-MAP]`.

A related choice made while wiring it: **team sizes are never sent to the
model.** The headcount is used locally to decide whether to warn. A team size is
itself a small identifying detail, and there is no reason the model needs it to
describe what a role is obligated to do.

### P5 · Model providers at MVP *(scope doc decision #5)*
**In use:** Anthropic (default, `claude-opus-5`) and any OpenAI-compatible
endpoint. Both are hand-rolled over `urllib` — no vendor SDKs — so that the
entire egress surface is a single readable file with no transitive dependencies.

### P6 · Build model *(scope doc decision #7)* — **settled**
Opus 5. Not a provisional default; confirmed by the owner 2026-07-27.

### P7 · Name detection without a heavyweight dependency — **ratified 2026-07-27**
The scope doc's provisional default was a Presidio-style layer over spaCy.
**In use, and confirmed by the owner:** a dependency-free detector (regex +
capitalisation/title heuristics) as the always-available default, with spaCy
supported as an **optional** install (`pip install jobmonger[ner]`) for stronger
recall. The core package declares zero required dependencies; spaCy is marked
optional in `pyproject.toml` and its absence is not an error, so the default
install stays dependency-light.

Rationale: the doc's own reasoning is that "detection is imperfect by design;
the human-in-the-loop confirmation step is what makes it safe, not the model's
accuracy." If confirmation is what carries safety, then a ~500 MB model download
as a hard install requirement buys less than it costs — both in install friction
for a stressed non-technical user and in audit surface. Reversible: the detector
is an interface with two implementations.

### P8 · Where local data lives
**In use:** `%APPDATA%\jobmonger` on Windows, `~/.config/jobmonger` and
`~/.local/share/jobmonger` elsewhere. Config, consent record, and the audit log
are separate files. Documents are **never** copied into this directory — they
are read from wherever the user keeps them and held in memory only.

### P9 · Decision-friction v1 seed *(scope doc decision #8)*
The scope doc defers `[DECISION-FRICTION]` but notes the v1 restate-and-confirm
seed "could ride along cheaply." **In use:** it does — `jobmonger/friction.py`
restates the single key fact before a consequential action. Currently wired to
exactly one trigger: moving the dial to position 4 (maximum advocacy). Timer and
active-recall check are not built, per the doc.

---

## Raised during the build — not in the scope doc

### X1 · The document is held in memory, never written to disk
A consequence of P8 worth stating explicitly because it constrains future
features: there is no document cache, so nothing resumes after the process
exits. A "recent documents" convenience feature would require reversing this.

### X2 · Streaming responses vs. the fact-layer invariant
The fact layer is extracted in a single non-streamed call so the frozen fact set
is complete before any dial position renders. Dialed framing *is* streamed. This
means the user waits once, up front, then sees fast dial changes. The alternative
(streaming facts too) would let a dial position render against a partial fact
set, which would break the invariant in exactly the way that matters.

### X4 · Bare partial names — **settled 2026-07-27, split by provenance**

Found by an end-to-end dry run rather than by reasoning about the code:
confirming "Marcus Okafor" left a later salutation "Marcus," untouched, and the
residual scan could not catch it because "Marcus" alone was never a confirmed
surface. In a letter addressed to the user that is untidy; where the name is a
manager's, it is a leak that reads as though redaction were complete.

**Settled as a split, on provenance** (`Review.absorb_partials`):

| Case | Behaviour |
|---|---|
| A component of a name the user **already confirmed** | Folded into that same identity automatically, keeping one token for one person |
| A bare name **never part of** any confirmed full name | Left pending. Sealing refuses until the user decides |

The reasoning for the first half: the user has already made the judgement that
matters — that this name belongs to this person in this role. Re-asking about
"Marcus" after they confirmed "Marcus Okafor" is review theatre, and review
theatre trains people to click through, which is how a review step dies.

The reasoning for the second: an unfamiliar single name is genuinely ambiguous,
and nothing here gets to resolve that on the user's behalf.

One refinement the tests forced: absorbing never overturns a decision the user
already made. If they rejected a span, folding leaves it rejected.

### X5 · Contact details are given a role like people are

Phone numbers, email addresses, and employee identifiers currently take a role
(`[CONTACT]`, `[ID]`) through the same control people do, so the review screen
asks "who are they to you?" about a phone number. It works, but it reads oddly.
The kind is already known at detection time and could set the role
automatically. Left as-is rather than expanded past the critical path.

### X6 · The question box was a second path around the seal — **fixed**

Found while working out how the additive modules should handle names, not by a
test. `bridge.send()` takes a sealed payload **and** an instruction string. The
document went through `seal()`; the dial interpolated the user's typed question
straight into the instruction, which is not sealed and never passes the residual
scan. A question like *"Did Sarah have authority to deny this?"* travelled
verbatim. The gate was real and the question walked around it.

**Fixed three ways, so it cannot recur:**

1. `redaction.screen_user_text()` applies confirmed substitutions to anything the
   user types, and refuses on names never reviewed — the same X4 split, applied
   to the second input.
2. The screened question now travels **inside the sealed payload**, so it passes
   the residual scan like everything else.
3. `dial._instruction()` takes a `bool`, not the question. There is no user
   string in scope for it to accidentally include.

The general lesson is worth keeping in view for the remaining modules: the seal
protects the *payload* parameter. Every other argument that reaches `bridge` is
unprotected by construction, so user input must be routed into the payload
rather than placed beside it.

### X7 · Sentence openers were being absorbed into names — **fixed**

Surfaced by the fix above, because questions are where it bites. *"Did Devika
approve it?"* was detected as the single name **"Did Devika"** — which would have
been shown for review as such, substituted whole, and left "[COWORKER] approve
it?" behind. A leading opener is now trimmed from a capitalised run, with the
span corrected so the highlight still points at the name (the first cut moved
the surface but not the offsets — caught by a span/surface alignment check).

Openers are trimmed only from the front of a longer run, never rejected alone,
so a real name like "May Fletcher" still detects.

### X3 · No telemetry means no crash reports
Stated for completeness: when the tool breaks for a user, the owner will not
know. That is the correct trade for this posture, but it is a real operational
cost worth accepting deliberately rather than discovering later.
