"""Where this tool keeps its own files on this machine — and nowhere else.

Three rules, enforced here so no other module has to remember them:

1. Everything lives under a directory the user owns and can delete.
2. Documents are **never** written here. They are read from wherever the user
   keeps them and held in memory only. See DECISIONS.md item X1.
3. Nothing here is ever transmitted.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_APP_DIR_NAME = "jobmonger"


def _base_dir(kind: str) -> Path:
    """Resolve the platform-appropriate directory for ``kind``.

    ``kind`` is "config" or "data". They differ on Linux/macOS and coincide on
    Windows; keeping them separate means a user can back up settings without
    also copying their audit log, which may reference sensitive matters.
    """
    if sys.platform == "win32":
        root = os.environ.get("APPDATA")
        base = Path(root) if root else Path.home() / "AppData" / "Roaming"
        return base / _APP_DIR_NAME

    if kind == "config":
        root = os.environ.get("XDG_CONFIG_HOME")
        base = Path(root) if root else Path.home() / ".config"
        return base / _APP_DIR_NAME

    root = os.environ.get("XDG_DATA_HOME")
    base = Path(root) if root else Path.home() / ".local" / "share"
    return base / _APP_DIR_NAME


def config_dir() -> Path:
    return _base_dir("config")


def data_dir() -> Path:
    return _base_dir("data")


def config_file() -> Path:
    """Settings, including the model key. Written with owner-only permissions."""
    return config_dir() / "config.json"


def consent_file() -> Path:
    """Record that the long disclaimer was shown and accepted."""
    return config_dir() / "consent.json"


def log_file() -> Path:
    """Append-only audit trail of settings and actions. The user's, not ours."""
    return data_dir() / "activity.log.jsonl"


def ensure_dirs() -> None:
    """Create both directories if absent. Safe to call repeatedly."""
    for directory in (config_dir(), data_dir()):
        directory.mkdir(parents=True, exist_ok=True)
        _restrict(directory)


def _restrict(path: Path) -> None:
    """Best-effort owner-only permissions.

    A no-op on Windows, where the default ACL on a per-user AppData directory
    already excludes other standard users. Failures are ignored deliberately:
    a restrictive umask or an exotic filesystem should not stop the tool from
    running, and the security claim this project actually makes is about
    egress, not local file modes.
    """
    if sys.platform == "win32":
        return
    try:
        path.chmod(0o700 if path.is_dir() else 0o600)
    except OSError:
        pass
