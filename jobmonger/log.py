"""[LOG] — a local, append-only record of settings and actions.

This is the user's log, not ours. It never leaves the machine, and there is no
code anywhere in this package that reads it back except to show it to the user.

What it records: what was done, when, and with which settings. What it does not
record: document content, detected names, model responses, or the model key.
The log has to be safe to hand to someone; that constrains what goes in it.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import paths

_lock = threading.Lock()

# Keys whose values are never written, regardless of caller. A defensive
# measure: the call sites are all careful, but this is the kind of care that
# decays as a codebase grows, so it is enforced in one place instead.
_NEVER_LOG = frozenset(
    {
        "api_key",
        "key",
        "text",
        "content",
        "document",
        "response",
        "surface",
        "surfaces",
        "original",
        "prompt",
    }
)


def _scrub(details: dict[str, Any]) -> dict[str, Any]:
    """Drop forbidden keys and coerce the rest to something JSON-safe."""
    clean: dict[str, Any] = {}
    for key, value in details.items():
        if key.lower() in _NEVER_LOG:
            clean[key] = "<omitted>"
        elif isinstance(value, (str, int, float, bool)) or value is None:
            clean[key] = value
        elif isinstance(value, (list, tuple)):
            clean[key] = [v if isinstance(v, (str, int, float, bool)) else str(v) for v in value]
        elif isinstance(value, dict):
            clean[key] = _scrub(value)
        else:
            clean[key] = str(value)
    return clean


def record(action: str, **details: Any) -> None:
    """Append one entry. Never raises — logging must not break the tool."""
    entry = {
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "action": action,
        **_scrub(details),
    }
    try:
        paths.ensure_dirs()
        target = paths.log_file()
        with _lock:
            with open(target, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        if os.name != "nt":
            try:
                target.chmod(0o600)
            except OSError:
                pass
    except OSError:
        # A full disk or a read-only home should not prevent someone from
        # reading their own handbook.
        pass


def read_all(limit: int | None = None) -> list[dict[str, Any]]:
    """Return log entries, newest last. For showing the user their own record."""
    target: Path = paths.log_file()
    if not target.exists():
        return []
    entries: list[dict[str, Any]] = []
    try:
        with open(target, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    # A partially-written final line after a crash. Skip it
                    # rather than refusing to show the user the rest.
                    continue
    except OSError:
        return []
    return entries[-limit:] if limit else entries
