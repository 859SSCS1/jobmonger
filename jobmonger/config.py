"""[CONFIG-KEY] — local settings and the user's own model key.

The key belongs to the user. It is read from the environment if present,
otherwise from a file on this machine, and it is used for exactly one purpose:
authenticating to the model endpoint the user chose. It is never logged, never
echoed back to the local view, and never included in any prompt.

Provider defaults are PROVISIONAL — see DECISIONS.md item P5.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from typing import Literal

from . import log, paths

Provider = Literal["anthropic", "openai_compatible"]

# PROVISIONAL (DECISIONS.md P5 / P6). Opus 5 is the settled build model and the
# sensible default runtime model: strongest available reasoning at half the cost
# of the tier above, with no data-retention floor and no elevated refusal risk
# on employment-dispute content.
DEFAULT_ANTHROPIC_MODEL = "claude-opus-5"
DEFAULT_OPENAI_MODEL = "gpt-4o"

ANTHROPIC_ENDPOINT = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"

# Environment variables are checked before the config file so a user can run
# without ever writing a key to disk.
_ENV_KEYS = {
    "anthropic": ("JOBMONGER_API_KEY", "ANTHROPIC_API_KEY"),
    "openai_compatible": ("JOBMONGER_API_KEY", "OPENAI_API_KEY"),
}


@dataclass(frozen=True)
class Config:
    provider: Provider = "anthropic"
    model: str = DEFAULT_ANTHROPIC_MODEL
    #: Only used when provider is "openai_compatible". Full chat-completions URL.
    base_url: str = ""
    #: Redact the employing organisation's name. PROVISIONAL: off (DECISIONS P2).
    redact_company_name: bool = False
    #: Redact the user's own name. PROVISIONAL: off (DECISIONS P2).
    redact_own_name: bool = False
    #: Names the user has told us about, so detection can be seeded.
    own_name: str = ""
    company_name: str = ""
    #: Advocacy dial position, 0-4. See dial.py. PROVISIONAL labels (DECISIONS N3).
    dial_position: int = 2

    # -- key handling ------------------------------------------------------
    # Deliberately NOT a field: the key is never part of the serialised config
    # object that gets passed around, logged, or rendered. It is fetched at the
    # moment of use, from `resolve_key()`, and held only for that call.

    def endpoint(self) -> str:
        if self.provider == "anthropic":
            return ANTHROPIC_ENDPOINT
        return self.base_url

    def is_ready(self) -> bool:
        """True if this config could actually reach a model."""
        if not self.endpoint():
            return False
        return bool(resolve_key(self.provider))


def resolve_key(provider: Provider) -> str:
    """Return the user's key for ``provider``, or "" if none is available.

    Environment first, then the config file. Never logged, never cached in a
    module-level variable — callers hold it for the duration of one request.
    """
    for name in _ENV_KEYS.get(provider, ()):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    stored = _read_raw().get("api_key", "")
    return stored.strip() if isinstance(stored, str) else ""


def key_source(provider: Provider) -> str:
    """Where the key came from — for showing the user, never the key itself."""
    for name in _ENV_KEYS.get(provider, ()):
        if os.environ.get(name, "").strip():
            return f"environment variable {name}"
    if _read_raw().get("api_key"):
        return f"config file ({paths.config_file()})"
    return "not set"


def _read_raw() -> dict:
    target = paths.config_file()
    if not target.exists():
        return {}
    try:
        with open(target, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def load() -> Config:
    """Read settings from disk, falling back to defaults for anything missing."""
    raw = _read_raw()
    defaults = Config()
    provider = raw.get("provider", defaults.provider)
    if provider not in ("anthropic", "openai_compatible"):
        provider = defaults.provider
    model = raw.get("model") or (
        DEFAULT_ANTHROPIC_MODEL if provider == "anthropic" else DEFAULT_OPENAI_MODEL
    )
    position = raw.get("dial_position", defaults.dial_position)
    if not isinstance(position, int) or not 0 <= position <= 4:
        position = defaults.dial_position
    return Config(
        provider=provider,  # type: ignore[arg-type]
        model=str(model),
        base_url=str(raw.get("base_url", defaults.base_url)),
        redact_company_name=bool(raw.get("redact_company_name", defaults.redact_company_name)),
        redact_own_name=bool(raw.get("redact_own_name", defaults.redact_own_name)),
        own_name=str(raw.get("own_name", defaults.own_name)),
        company_name=str(raw.get("company_name", defaults.company_name)),
        dial_position=position,
    )


def save(config: Config, *, api_key: str | None = None) -> None:
    """Persist settings. ``api_key`` is written only if explicitly supplied.

    Passing ``api_key=""`` clears a stored key; passing ``None`` (the default)
    leaves whatever is on disk untouched, so an ordinary settings change cannot
    accidentally erase a key the user entered earlier.
    """
    paths.ensure_dirs()
    existing = _read_raw()
    payload = {
        "provider": config.provider,
        "model": config.model,
        "base_url": config.base_url,
        "redact_company_name": config.redact_company_name,
        "redact_own_name": config.redact_own_name,
        "own_name": config.own_name,
        "company_name": config.company_name,
        "dial_position": config.dial_position,
    }
    if api_key is None:
        if existing.get("api_key"):
            payload["api_key"] = existing["api_key"]
    elif api_key.strip():
        payload["api_key"] = api_key.strip()

    target = paths.config_file()
    with open(target, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
    paths._restrict(target)

    # Note the absence of `api_key` and of the two name fields — log.py would
    # scrub the key regardless, but not sending it is the stronger habit.
    log.record(
        "config.saved",
        provider=config.provider,
        model=config.model,
        redact_company_name=config.redact_company_name,
        redact_own_name=config.redact_own_name,
        dial_position=config.dial_position,
        key_present=bool(payload.get("api_key")),
    )


def with_dial(config: Config, position: int) -> Config:
    """Return a copy at a new dial position, clamped to the valid range."""
    return replace(config, dial_position=max(0, min(4, position)))
