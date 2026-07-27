"""[MODEL-BRIDGE] — the only code in this package that opens a socket.

If you are auditing the privacy claim, this file and ``redaction.py`` are the
two you need. Everything else is local by construction; this is the single
place where anything leaves the machine, and it accepts only a ``SealedText``.

Written against ``urllib`` rather than a vendor SDK on purpose. Two reasons:
the entire egress surface stays readable in one file with no transitive
dependencies for an auditor to chase, and a project whose central promise is
"nothing leaves except what you approved" should not ask anyone to take a
dependency tree on faith.

Provider defaults are PROVISIONAL — see DECISIONS.md item P5.
"""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Iterator

from . import config as config_module
from . import constants, log
from .config import ANTHROPIC_VERSION, Config
from .redaction import SealedText

# Generous: an Opus 5 request at high effort on a long handbook can think for a
# while before the first byte. Too short a timeout here reads to the user as
# "the tool is broken" when it is in fact working.
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


def _guarded_system(extra: str = "") -> str:
    """Every system prompt, with the guardrails attached. No exceptions.

    Structural enforcement does the real work (a model cannot leak a name it was
    never given), but the guardrails are also stated to the model every time,
    because the boundaries this project cares about — no verdict, no dossiers,
    no fabrication — are behavioural rather than structural.
    """
    parts = [constants.GUARDRAILS.strip()]
    if extra.strip():
        parts.append(extra.strip())
    return "\n\n".join(parts)


def _require_sealed(payload: object) -> SealedText:
    """The gate. Nothing reaches the network without passing through here.

    Note ``type(...) is`` rather than ``isinstance``. That is not a style
    preference — it closes a real hole. ``isinstance`` accepts subclasses, and a
    subclass can define its own ``__init__`` that never asks for the mint:

        class Sneaky(SealedText):
            def __init__(self): pass        # no mint, no redaction, no scan

    Under ``isinstance`` that object is waved straight through to the network
    carrying whatever text it likes. Under an exact type check it is refused.
    The suite proves both halves — see
    ``tests/test_egress.py::test_send_refuses_a_subclass_shaped_impostor``,
    which is what found this in the first place.
    """
    if type(payload) is not SealedText:
        raise TypeError(
            "Only sealed, redacted content can be sent. Got "
            f"{type(payload).__name__}. Run the document through "
            "jobmonger.redaction.seal() and send its result instead."
        )
    return payload


def _post(url: str, headers: dict[str, str], body: dict, *, stream: bool):
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


# --------------------------------------------------------------------------
# Anthropic
# --------------------------------------------------------------------------


def _anthropic_headers(key: str) -> dict[str, str]:
    return {
        "content-type": "application/json",
        "x-api-key": key,
        "anthropic-version": ANTHROPIC_VERSION,
        "anthropic-beta": FALLBACK_BETA,
    }


def _anthropic_body(cfg: Config, system: str, prompt: str, *, effort: str,
                    schema: dict | None, stream: bool) -> dict:
    output_config: dict = {"effort": effort}
    if schema is not None:
        output_config["format"] = {"type": "json_schema", "schema": schema}

    body: dict = {
        "model": cfg.model,
        "max_tokens": DEFAULT_MAX_TOKENS,
        "system": system,
        "messages": [{"role": "user", "content": prompt}],
        "output_config": output_config,
        # Route a policy decline to a capable substitute rather than failing.
        "fallbacks": "default",
    }
    if stream:
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


def _anthropic_stream(response) -> Iterator[str]:
    """Yield text deltas from an Anthropic SSE stream."""
    refused = False
    for raw in response:
        line = raw.decode("utf-8", "replace").strip()
        if not line or not line.startswith("data:"):
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
            message = event.get("error", {}).get("message", "the model service reported an error")
            raise BridgeError(message)

    if refused:
        raise RefusedError(
            "The model service declined partway through this request. "
            "Nothing was wrong with what you sent."
        )


# --------------------------------------------------------------------------
# OpenAI-compatible
# --------------------------------------------------------------------------


def _openai_headers(key: str) -> dict[str, str]:
    return {"content-type": "application/json", "authorization": f"Bearer {key}"}


def _openai_body(cfg: Config, system: str, prompt: str, *, schema: dict | None,
                 stream: bool) -> dict:
    body: dict = {
        "model": cfg.model,
        "max_tokens": DEFAULT_MAX_TOKENS,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
    }
    if schema is not None:
        body["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "facts", "strict": True, "schema": schema},
        }
    if stream:
        body["stream"] = True
    return body


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


# --------------------------------------------------------------------------
# The public surface — both functions take SealedText and nothing else
# --------------------------------------------------------------------------


def send(payload: object, instruction: str, *, cfg: Config | None = None,
         system_extra: str = "", effort: str = "high",
         schema: dict | None = None) -> Reply:
    """Send sealed content and wait for the whole reply.

    ``payload`` is annotated ``object`` deliberately: the type check is a
    runtime gate, not a hint a caller can silence with a cast or an ignore
    comment. Passing anything but a ``SealedText`` raises.
    """
    sealed = _require_sealed(payload)
    cfg = cfg or config_module.load()
    key = config_module.resolve_key(cfg.provider)
    if not key:
        raise BridgeError(
            "No model key is set. Add one in Settings, or set JOBMONGER_API_KEY "
            "in your environment."
        )
    endpoint = cfg.endpoint()
    if not endpoint:
        raise BridgeError("No model endpoint is configured. Check Settings.")

    system = _guarded_system(system_extra)
    prompt = f"{instruction.strip()}\n\n---\n\n{sealed.text}"

    if cfg.provider == "anthropic":
        headers = _anthropic_headers(key)
        body = _anthropic_body(cfg, system, prompt, effort=effort, schema=schema, stream=False)
    else:
        headers = _openai_headers(key)
        body = _openai_body(cfg, system, prompt, schema=schema, stream=False)

    log.record(
        "bridge.request",
        provider=cfg.provider,
        model=cfg.model,
        source_name=sealed.source_name,
        entities_redacted=sealed.entity_count,
        streamed=False,
        effort=effort,
        structured=schema is not None,
    )

    with _post(endpoint, headers, body, stream=False) as response:
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
        input_tokens=reply.input_tokens,
        output_tokens=reply.output_tokens,
    )
    return reply


def stream(payload: object, instruction: str, *, cfg: Config | None = None,
           system_extra: str = "", effort: str = "high") -> Iterator[str]:
    """Send sealed content and yield text as it arrives.

    Used for anything the user is waiting on — a dialed reading, chiefly. The
    fact layer deliberately does not stream; see DECISIONS.md item X2.
    """
    sealed = _require_sealed(payload)
    cfg = cfg or config_module.load()
    key = config_module.resolve_key(cfg.provider)
    if not key:
        raise BridgeError(
            "No model key is set. Add one in Settings, or set JOBMONGER_API_KEY "
            "in your environment."
        )
    endpoint = cfg.endpoint()
    if not endpoint:
        raise BridgeError("No model endpoint is configured. Check Settings.")

    system = _guarded_system(system_extra)
    prompt = f"{instruction.strip()}\n\n---\n\n{sealed.text}"

    if cfg.provider == "anthropic":
        headers = _anthropic_headers(key)
        body = _anthropic_body(cfg, system, prompt, effort=effort, schema=None, stream=True)
        reader = _anthropic_stream
    else:
        headers = _openai_headers(key)
        body = _openai_body(cfg, system, prompt, schema=None, stream=True)
        reader = _openai_stream

    log.record(
        "bridge.request",
        provider=cfg.provider,
        model=cfg.model,
        source_name=sealed.source_name,
        entities_redacted=sealed.entity_count,
        streamed=True,
        effort=effort,
    )

    with _post(endpoint, headers, body, stream=True) as response:
        yield from reader(response)

    log.record("bridge.reply", provider=cfg.provider, model=cfg.model, streamed=True)


def check_reachable(cfg: Config | None = None) -> str:
    """A cheap round-trip to confirm the key and model work. Sends no document.

    Deliberately not routed through ``send()``: there is no document, so there
    is nothing to seal, and inventing a fake ``SealedText`` to satisfy the gate
    would be exactly the kind of shortcut that erodes it. This builds its own
    request from a constant string that contains nothing of the user's.
    """
    cfg = cfg or config_module.load()
    key = config_module.resolve_key(cfg.provider)
    if not key:
        raise BridgeError("No model key is set.")
    endpoint = cfg.endpoint()
    if not endpoint:
        raise BridgeError("No model endpoint is configured.")

    probe = "Reply with the single word: ready"
    if cfg.provider == "anthropic":
        headers = _anthropic_headers(key)
        body = {
            "model": cfg.model,
            "max_tokens": 2048,
            "messages": [{"role": "user", "content": probe}],
            "output_config": {"effort": "low"},
        }
    else:
        headers = _openai_headers(key)
        body = {
            "model": cfg.model,
            "max_tokens": 16,
            "messages": [{"role": "user", "content": probe}],
        }

    with _post(endpoint, headers, body, stream=False) as response:
        payload = json.loads(response.read().decode("utf-8", "replace"))
    reply = _anthropic_text(payload) if cfg.provider == "anthropic" else _openai_text(payload)
    log.record("bridge.check", provider=cfg.provider, model=reply.model or cfg.model, ok=True)
    return reply.model or cfg.model
