# Open Decisions

Every naming and policy question raised during the build, collected here rather
than interrupting it. Nothing in this list is settled; every corresponding
implementation carries a `PROVISIONAL` marker pointing back to the item number.

**All product, module, and feature naming is reserved to the owner.** Where the
build needed a name to function, it uses the neutral placeholder labels from the
Stage 1 scope doc (`[REDACTION-GATE]`, `[FACT-LAYER]`, …) or a numbered
identifier. No candidate names are proposed anywhere in this repository.

---

## Blocking — needed before first public release

### B1 · Guardrail wording outstanding — both disclaimers are now final

| Constant | Status |
|---|---|
| `SHORT_DISCLAIMER` | **Final**, verbatim from the build spec (2026-07-27) |
| `LONG_DISCLAIMER` | **Final**, verbatim from the build spec (2026-07-27) |
| `GUARDRAILS` | **Placeholder** — wording still needed |

Both user-facing disclaimers are in place character for character, including
the em dash in the short form, the en dash in "attorney–client", and the
straight quotes around `"as is,"`. They are locked by SHA-256 checksums in
`tests/test_invariants.py`, so a smart-quote substitution or an accidental
reflow fails the build rather than shipping. A checksum failure is not a licence
to update the checksum — it means the text changed and should be restored.

**What `GUARDRAILS` is for, since it is a different kind of text.** The two
disclaimers are addressed to the *user*. `GUARDRAILS` is addressed to the
*model*: it is appended verbatim to the system prompt of every request the
bridge sends, and it is the behavioural half of enforcing the scope doc's legal
guardrails.

Structure covers what it can — the model is never given a real name, so it
cannot build a named dossier no matter what it is told. But **renders no legal
verdict**, **places no one in a job**, and **fabricates nothing** are
behavioural boundaries with nothing structural to bite on. The only thing
standing behind those three is what the model is instructed, on every request.

A working placeholder is in the file so the mechanism runs end to end. It is not
owner-approved wording. If you would like to supply it, what is needed is a
short block of instructions written *to the model*, covering at minimum those
three boundaries plus "describe roles by duties, never by the character or
motives of the person holding them".

Until it is supplied, the startup warning still fires and the first-run banner
still shows — both now name `GUARDRAILS` specifically and state that the
disclaimers the user is shown are final, so the notice cannot be misread as the
legal text being unfinished.

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

### P4 · Low-headcount re-identification threshold *(scope doc decision #4)*
**In use:** warn when a role description sits on a team of 5 or fewer. Warn only
— never block, never silently mask further. One constant,
`jobmonger/redaction.py::REID_TEAM_SIZE_THRESHOLD`. The `[ROLE-MAP]` module that
consumes it is deferred past the critical path; the constant and the warning
path exist now so the threshold has one home.

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

### X3 · No telemetry means no crash reports
Stated for completeness: when the tool breaks for a user, the owner will not
know. That is the correct trade for this posture, but it is a real operational
cost worth accepting deliberately rather than discovering later.
