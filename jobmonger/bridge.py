"""[MODEL-BRIDGE] — the only code in this package that opens a socket.

If you are auditing the privacy claim, this file and ``redaction.py`` are the
two you need. Everything else is local by construction; this is the single
place where anything leaves the machine.

Two things can reach the model, and both are locked:

* the **payload**, which must be a ``SealedText`` — only ``redaction.seal()``
  can mint one, and only after a human has reviewed every detected name;
* the **directive**, which must be a ``Directive`` — only ``bridge.directive()``
  can mint one, and it assembles text exclusively from ``prompts.py`` using
  arguments that cannot carry text (an enum, an int, a bool).

There is no third thing, and there is no string parameter on either public
function. That is deliberate, and it is the fix for DECISIONS.md item X6: the
old signature took an ``instruction: str``, the dial interpolated the user's
typed question into it, and a question sailed past a gate that was guarding
only the payload beside it. Patching that call site fixed that bug and left the
next module free to repeat it. Removing the parameter fixes the class.

Written against ``urllib`` rather than a vendor SDK on purpose: the entire
egress surface stays readable in one file with no transitive dependencies for
an auditor to chase.
"""

from __future__ import annotations

import json
import re
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass
from enum import Enum
from typing import Iterator

from . import config as config_module
from . import constants, log, prompts
from .config import ANTHROPIC_VERSION, Config
from .prompts import Task
from .redaction import SealedText

CONNECT_TIMEOUT_SECONDS = 30
READ_TIMEOUT_SECONDS = 600

# max_tokens bounds thinking *plus* visible text on current models, so this is
# sized for both. Streaming is used for anything user-facing, which is what
# makes a ceiling this high safe to request.
DEFAULT_MAX_TOKENS = 16_000

# Opus 5 safety classifiers can decline a request outright. Employment disputes
# involve conduct allegations, threats, and harassment described in detail —
# benign, but adjacent to categories the classifiers watch. Server-side
# fallbacks re-run a declined request on another model inside the same call
# rather than handing the user a dead end at the worst possible moment.
FALLBACK_BETA = "server-side-fallback-2026-07-01"

# A model name is transmitted verbatim in the request body, and it comes from a
# text box in Settings. Constraining its shape closes the last non-payload route
# by which a user could put arbitrary words on the wire — see DECISIONS.md X8.
_MODEL_NAME_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


class Effort(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"
    MAX = "max"


class BridgeError(Exception):
    """Something went wrong reaching the model. Message is shown to the user."""


class RefusedError(BridgeError):
    """The provider's safety classifiers declined the request."""


@dataclass(frozen=True)
class Reply:
    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0


# --------------------------------------------------------------------------
# Directive — the second sealed thing
# --------------------------------------------------------------------------

_DIRECTIVE_MINT = object()


class Directive:
    """What the model is being asked to do. Cannot be constructed directly.

    Same shape as ``SealedText`` and for the same reason: the value is only
    trustworthy because of the checks performed on the way to creating it, so
    the constructor is closed and ``directive()`` is the only door.

    A caller cannot put words in one. ``directive()`` takes a ``Task``, an int,
    a bool, and an ``Effort`` — none of which can carry text — and looks the
    wording up in ``prompts.py``.
    """

    __slots__ = ("_task", "_instruction", "_note", "_schema", "_effort")

    def __init__(self, task: Task, instruction: str, note: str,
                 schema: dict | None, effort: Effort, *, _mint: object = None) -> None:
        if _mint is not _DIRECTIVE_MINT:
            raise TypeError(
                "Directive cannot be constructed directly. Use bridge.directive(), "
                "which assembles wording from prompts.py — there is no supported "
                "way to send the model text of your own."
            )
        object.__setattr__(self, "_task", task)
        object.__setattr__(self, "_instruction", instruction)
        object.__setattr__(self, "_note", note)
        object.__setattr__(self, "_schema", schema)
        object.__setattr__(self, "_effort", effort)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("Directive is immutable.")

    def __delattr__(self, name: str) -> None:
        raise AttributeError("Directive is immutable.")

    def __reduce__(self):
        raise TypeError("Directive cannot be pickled; rebuild it with bridge.directive().")

    @property
    def task(self) -> Task:
        return self._task

    @property
    def instruction(self) -> str:
        return self._instruction

    @property
    def note(self) -> str:
        return self._note

    @property
    def schema(self) -> dict | None:
        return self._schema

    @property
    def effort(self) -> Effort:
        return self._effort

    def __repr__(self) -> str:
        return f"<Directive task={self._task.value} effort={self._effort.value}>"


def directive(task: Task, *, posture: int = 0, has_question: bool = False,
              effort: Effort = Effort.HIGH) -> Directive:
    """Mint a directive. The only way to give the model an instruction.

    Every argument is a controlled value. There is no parameter here — and none
    on ``send`` or ``stream`` — through which a caller could pass a string of
    their own, which is what makes X6 structurally unrepeatable rather than a
    rule someone has to remember.
    """
    if not isinstance(task, Task):
        raise TypeError(f"Expected a prompts.Task member, got {type(task).__name__}.")
    if not isinstance(effort, Effort):
        raise TypeError(f"Expected a bridge.Effort member, got {type(effort).__name__}.")

    instruction, note, schema = prompts.build(
        task, posture=posture, has_question=has_question
    )
    return Directive(task, instruction, note, schema, effort, _mint=_DIRECTIVE_MINT)


# --------------------------------------------------------------------------
# Gates
# --------------------------------------------------------------------------


def _require_sealed(payload: object) -> SealedText:
    """The payload gate. Nothing reaches the network without passing here.

    Note ``type(...) is`` rather than ``isinstance``. That is not a style
    preference — it closes a real hole. ``isinstance`` accepts subclasses, and a
    subclass can define its own ``__init__`` that never asks for the mint,
    producing an object that carries arbitrary un-redacted text and satisfies
    every other check. See ``tests/test_egress.py``.
    """
    if type(payload) is not SealedText:
        raise TypeError(
            "Only sealed, redacted content can be sent. Got "
            f"{type(payload).__name__}. Run the document through "
            "jobmonger.redaction.seal() and send its result instead."
        )
    return payload


def _require_directive(value: object) -> Directive:
    """The instruction gate. A bare string is refused, however well-intentioned."""
    if type(value) is not Directive:
        raise TypeError(
            "Instructions must be a Directive from bridge.directive(), not "
            f"{type(value).__name__}. Wording lives in prompts.py; if you need "
            "the model to be told something new, add it there."
        )
    return value


def _require_model_name(model: str) -> str:
    if not _MODEL_NAME_RE.match(model or ""):
        raise BridgeError(
            "That does not look like a model name. Model names are short "
            "identifiers such as claude-opus-5, with no spaces."
        )
    return model


def _guarded_system(note: str = "") -> str:
    """Every system prompt, with the owner-supplied guardrails attached.

    Structural enforcement does the real work — a model cannot leak a name it
    was never given — but the guardrails are also stated every time, because
    "render no verdict", "place no one", and "fabricate nothing" are behavioural
    boundaries with nothing structural to bite on.
    """
    parts = [constants.GUARDRAILS.strip()]
    if note.strip():
        parts.append(note.strip())
    return "\n\n".join(parts)


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------


def _post(url: str, headers: dict[str, str], body: dict):
    """One HTTP POST. Certificate verification is never disabled."""
    data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    context = ssl.create_default_context()
    try:
        return urllib.request.urlopen(request, timeout=READ_TIMEOUT_SECONDS, context=context)
    except urllib.error.HTTPError as exc:
        raise _http_error(exc) from exc
    except urllib.error.URLError as exc:
        raise BridgeError(
            f"Could not reach the model service ({exc.reason}). "
            "Check your internet connection and try again."
        ) from exc
    except TimeoutError as exc:
        raise BridgeError("The model service took too long to respond.") from exc


def _http_error(exc: urllib.error.HTTPError) -> BridgeError:
    try:
        detail = json.loads(exc.read().decode("utf-8", "replace"))
        message = detail.get("error", {}).get("message", "")
    except Exception:
        message = ""

    if exc.code == 401:
        return BridgeError(
            "The model service rejected your key. Check it in Settings, or set "
            "JOBMONGER_API_KEY in your environment."
        )
    if exc.code == 403:
        return BridgeError("Your key does not have access to this model.")
    if exc.code == 404:
        return BridgeError(
            f"The model name was not recognised{': ' + message if message else ''}. "
            "Check the model setting."
        )
    if exc.code == 429:
        return BridgeError(
            "The model service is rate-limiting your key. Wait a moment and try again."
        )
    if exc.code >= 500:
        return BridgeError("The model service is having trouble. Try again shortly.")
    return BridgeError(f"The model service returned an error{': ' + message if message else ''}.")


def _anthropic_headers(key: str) -> dict[str, str]:
    return {
        "content-type": "application/json",
        "x-api-key": key,
        "anthropic-version": ANTHROPIC_VERSION,
        "anthropic-beta": FALLBACK_BETA,
    }


def _openai_headers(key: str) -> dict[str, str]:
    return {"content-type": "application/json", "authorization": f"Bearer {key}"}


def _build_body(cfg: Config, spec: Directive, sealed: SealedText, *, streaming: bool) -> dict:
    """Assemble the request. Every field is either config, a constant, or sealed."""
    system = _guarded_system(spec.note)
    prompt = f"{spec.instruction.strip()}\n\n---\n\n{sealed.text}"
    model = _require_model_name(cfg.model)

    if cfg.provider == "anthropic":
        output_config: dict = {"effort": spec.effort.value}
        if spec.schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": spec.schema}
        body: dict = {
            "model": model,
            "max_tokens": DEFAULT_MAX_TOKENS,
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
            "output_config": output_config,
            # Route a policy decline to a capable substitute rather than failing.
            "fallbacks": "default",
        }
    else:
        body = {
            "model": model,
            "max_tokens": DEFAULT_MAX_TOKENS,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        }
        if spec.schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "result", "strict": True, "schema": spec.schema},
            }

    if streaming:
        body["stream"] = True
    return body


def _anthropic_text(payload: dict) -> Reply:
    if payload.get("stop_reason") == "refusal":
        raise RefusedError(
            "The model service declined to process this request. This sometimes "
            "happens with documents describing serious conduct allegations. "
            "Nothing was wrong with what you sent."
        )
    chunks = [
        block.get("text", "")
        for block in payload.get("content", [])
        if block.get("type") == "text"
    ]
    usage = payload.get("usage", {}) or {}
    return Reply(
        text="".join(chunks).strip(),
        model=payload.get("model", ""),
        input_tokens=int(usage.get("input_tokens", 0) or 0),
        output_tokens=int(usage.get("output_tokens", 0) or 0),
    )


def _openai_text(payload: dict) -> Reply:
    choices = payload.get("choices") or []
    if not choices:
        raise BridgeError("The model service returned an empty response.")
    first = choices[0]
    if first.get("finish_reason") == "content_filter":
        raise RefusedError(
            "The model service's content filter declined this request. "
            "Nothing was wrong with what you sent."
        )
    usage = payload.get("usage", {}) or {}
    return Reply(
        text=(first.get("message", {}).get("content") or "").strip(),
        model=payload.get("model", ""),
        input_tokens=int(usage.get("prompt_tokens", 0) or 0),
        output_tokens=int(usage.get("completion_tokens", 0) or 0),
    )


def _anthropic_stream(response) -> Iterator[str]:
    refused = False
    for raw in response:
        line = raw.decode("utf-8", "replace").strip()
        if not line.startswith("data:"):
            continue
        chunk = line[5:].strip()
        if not chunk or chunk == "[DONE]":
            continue
        try:
            event = json.loads(chunk)
        except json.JSONDecodeError:
            continue

        kind = event.get("type")
        if kind == "content_block_delta":
            delta = event.get("delta", {})
            if delta.get("type") == "text_delta":
                yield delta.get("text", "")
        elif kind == "message_delta":
            if event.get("delta", {}).get("stop_reason") == "refusal":
                refused = True
        elif kind == "error":
            raise BridgeError(
                event.get("error", {}).get("message", "the model service reported an error")
            )

    if refused:
        raise RefusedError(
            "The model service declined partway through this request. "
            "Nothing was wrong with what you sent."
        )


def _openai_stream(response) -> Iterator[str]:
    for raw in response:
        line = raw.decode("utf-8", "replace").strip()
        if not line.startswith("data:"):
            continue
        chunk = line[5:].strip()
        if not chunk or chunk == "[DONE]":
            continue
        try:
            event = json.loads(chunk)
        except json.JSONDecodeError:
            continue
        for choice in event.get("choices", []):
            piece = choice.get("delta", {}).get("content")
            if piece:
                yield piece


def _credentials(cfg: Config) -> tuple[str, str]:
    key = config_module.resolve_key(cfg.provider)
    if not key:
        raise BridgeError(
            "No model key is set. Add one in Settings, or set JOBMONGER_API_KEY "
            "in your environment."
        )
    endpoint = cfg.endpoint()
    if not endpoint:
        raise BridgeError("No model endpoint is configured. Check Settings.")
    return key, endpoint


# --------------------------------------------------------------------------
# The public surface — two sealed arguments, no strings
# --------------------------------------------------------------------------


def send(payload: object, spec: object, *, cfg: Config | None = None) -> Reply:
    """Send sealed content with a minted directive, and wait for the reply.

    Both arguments are annotated ``object`` deliberately: the type checks are
    runtime gates, not hints a caller can silence with a cast or an ignore
    comment.
    """
    sealed = _require_sealed(payload)
    spec_ = _require_directive(spec)
    cfg = cfg or config_module.load()
    key, endpoint = _credentials(cfg)

    headers = _anthropic_headers(key) if cfg.provider == "anthropic" else _openai_headers(key)
    body = _build_body(cfg, spec_, sealed, streaming=False)

    log.record(
        "bridge.request",
        provider=cfg.provider,
        model=cfg.model,
        task=spec_.task.value,
        source_name=sealed.source_name,
        entities_redacted=sealed.entity_count,
        streamed=False,
        effort=spec_.effort.value,
        structured=spec_.schema is not None,
    )

    with _post(endpoint, headers, body) as response:
        raw = response.read().decode("utf-8", "replace")
    try:
        payload_json = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise BridgeError("The model service returned a response that could not be read.") from exc

    reply = _anthropic_text(payload_json) if cfg.provider == "anthropic" else _openai_text(payload_json)
    log.record(
        "bridge.reply",
        provider=cfg.provider,
        model=reply.model or cfg.model,
        task=spec_.task.value,
        input_tokens=reply.input_tokens,
        output_tokens=reply.output_tokens,
    )
    return reply


def stream(payload: object, spec: object, *, cfg: Config | None = None) -> Iterator[str]:
    """Send sealed content with a minted directive, yielding text as it arrives."""
    sealed = _require_sealed(payload)
    spec_ = _require_directive(spec)
    cfg = cfg or config_module.load()
    key, endpoint = _credentials(cfg)

    if cfg.provider == "anthropic":
        headers, reader = _anthropic_headers(key), _anthropic_stream
    else:
        headers, reader = _openai_headers(key), _openai_stream
    body = _build_body(cfg, spec_, sealed, streaming=True)

    log.record(
        "bridge.request",
        provider=cfg.provider,
        model=cfg.model,
        task=spec_.task.value,
        source_name=sealed.source_name,
        entities_redacted=sealed.entity_count,
        streamed=True,
        effort=spec_.effort.value,
    )

    with _post(endpoint, headers, body) as response:
        yield from reader(response)

    log.record("bridge.reply", provider=cfg.provider, model=cfg.model,
               task=spec_.task.value, streamed=True)


def check_reachable(cfg: Config | None = None) -> str:
    """A cheap round-trip to confirm the key and model work. Sends no document.

    Deliberately not routed through ``send()``: there is no document, so there
    is nothing to seal, and inventing a fake ``SealedText`` to satisfy the gate
    would be exactly the kind of shortcut that erodes it. The request is built
    here from constants only — the probe text comes from ``prompts.py`` like
    every other word, and the guardrails ride along as on every request.
    """
    cfg = cfg or config_module.load()
    key, endpoint = _credentials(cfg)
    model = _require_model_name(cfg.model)

    spec = directive(Task.CONNECTIVITY_PROBE, effort=Effort.LOW)
    system = _guarded_system(spec.note)

    if cfg.provider == "anthropic":
        headers = _anthropic_headers(key)
        body = {
            "model": model,
            "max_tokens": 2048,
            "system": system,
            "messages": [{"role": "user", "content": spec.instruction}],
            "output_config": {"effort": spec.effort.value},
        }
    else:
        headers = _openai_headers(key)
        body = {
            "model": model,
            "max_tokens": 16,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": spec.instruction},
            ],
        }

    with _post(endpoint, headers, body) as response:
        payload = json.loads(response.read().decode("utf-8", "replace"))
    reply = _anthropic_text(payload) if cfg.provider == "anthropic" else _openai_text(payload)
    log.record("bridge.check", provider=cfg.provider, model=reply.model or cfg.model, ok=True)
    return reply.model or cfg.model
