# jobmonger
A zealous advocate for the individual employee — the way a sports agent works for his client. Local-first and open-source: bring your own documents and model, names are redacted before anything leaves your machine, and the loyalty is verifiable in the source. Understands your handbook, benefits, and the roles around you.

---

> **Early build.** The critical path works end to end and the legal text is
> final. The role map, tenure reasoning, and compliance companion are not built
> yet. Treat this as something to read and test rather than something to rely on.

## What it does

You open a document you already have — a handbook, a letter, a policy, an email
thread. Before anything is sent anywhere, every name in it is found and replaced
with a neutral role label, and **you** confirm each replacement. What gets sent
to the model is the version with `[MANAGER]` and `[HR_REP]` in it, never the
version with real names.

The model reads that redacted version and extracts the facts. Those facts are
then fixed. You can move a dial from a straight neutral reading to a full
argument for your side, and the framing changes — but the facts on screen
beside it do not, because they were established once and are reused unchanged.

## Running it

```bash
pip install -e .              # core, no dependencies
pip install -e ".[docs]"      # adds PDF and Word support
python -m jobmonger
```

That prints a `127.0.0.1` address with a one-time token and opens it. The
address works from this computer only.

You supply your own model key, either in Settings or as an environment variable:

```bash
export JOBMONGER_API_KEY=...        # or ANTHROPIC_API_KEY
```

## Checking the claim yourself

The claim is that no un-redacted content leaves your machine. You should not
have to take that on trust, and you should not have to read the whole codebase
to check it:

```bash
pip install -e ".[dev]" && pytest
```

`tests/test_egress.py` proves each link of the chain independently — that the
network function refuses anything unsealed, that sealed objects cannot be
forged or mutated, that sealing refuses while any detection is unreviewed, that
it refuses again if a confirmed name survives substitution, and that only one
module in the package can open a socket at all.

If you would rather read than run, two files carry the whole claim:

| File | What to look for |
|---|---|
| [`jobmonger/redaction.py`](jobmonger/redaction.py) | Detection, the human review step, and `seal()` — the one place content is cleared to leave |
| [`jobmonger/bridge.py`](jobmonger/bridge.py) | The only code here that opens a socket. It accepts nothing but a sealed object |
| [`jobmonger/constants.py`](jobmonger/constants.py) | The disclaimers you are shown, and the guardrails the model is given on every request |

## How the pieces fit

```
  your document ──▶ [DOC-INTAKE] ──▶ [REDACTION-GATE] ──▶ seal ──┐
   (never copied)                     you confirm every name     │
                                                                 ▼
                                                         [MODEL-BRIDGE]
                                                       the only egress point
                                                                 │
                                   ┌─────────────────────────────┘
                                   ▼
                            [FACT-LAYER]  extracted once, frozen
                                   │
                                   ▼
                           [ADVOCACY-DIAL]  framing only, never the facts
```

## What it is not

It is not a lawyer and gives no legal advice. It does not decide whether you
have a case. It does not place anyone in a job. It does not build files on
named individuals. It does not know anything about your workplace beyond what
you give it.

There is no server, no account, and no telemetry. Your settings and your own
activity log live on your machine and belong to you — which also means that
when this breaks for you, nobody upstream finds out.

## Where things stand

The critical path is built: scaffold, consent gate, config and key handling,
document intake, the redaction gate, the model bridge, the fact layer, the
advocacy dial, the local log, and the local view.

Also built: the role map — what each role around you is obliged to do, split
into duties owed to the organisation, owed to you, and working against you, with
a warning when a team is small enough that the role label is thin cover.

Not yet built: tenure reasoning and the handbook compliance companion.

Every naming and policy question raised while building is collected in
[DECISIONS.md](DECISIONS.md) rather than settled quietly in the code.

## Licence

MIT. See [LICENSE](LICENSE).
