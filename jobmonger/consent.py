"""[CONSENT-GATE] — the long disclaimer must be accepted before the tool runs.

Two things are deliberate here:

*Consent is versioned.* It records a hash of the exact disclaimer text that was
accepted. If the wording changes, prior consent no longer applies and the gate
re-opens. Consent to text nobody has read is not consent, and this project's
whole posture makes that hypocrisy expensive.

*The gate is checked in one place* — ``require()`` — which every entry point
calls before doing anything else.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone

from . import constants, log, paths


class ConsentRequired(Exception):
    """Raised when the tool is asked to operate before consent is recorded."""


@dataclass(frozen=True)
class ConsentRecord:
    accepted_at: str
    disclaimer_sha256: str
    disclaimer_was_placeholder: bool

    def matches_current(self) -> bool:
        return self.disclaimer_sha256 == current_disclaimer_hash()


def current_disclaimer_hash() -> str:
    """SHA-256 of the long disclaimer exactly as it will be displayed."""
    return hashlib.sha256(constants.LONG_DISCLAIMER.encode("utf-8")).hexdigest()


def load() -> ConsentRecord | None:
    """Read the stored consent record, or None if absent or unreadable."""
    target = paths.consent_file()
    if not target.exists():
        return None
    try:
        with open(target, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
        return ConsentRecord(
            accepted_at=str(raw["accepted_at"]),
            disclaimer_sha256=str(raw["disclaimer_sha256"]),
            disclaimer_was_placeholder=bool(raw.get("disclaimer_was_placeholder", False)),
        )
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        # Corrupt record is treated as no record: the gate re-opens. Failing
        # closed is the only safe direction for a consent check.
        return None


def is_granted() -> bool:
    """True only if consent exists *and* covers the disclaimer as it reads now."""
    record = load()
    return record is not None and record.matches_current()


def grant() -> ConsentRecord:
    """Record acceptance of the long disclaimer as it currently reads."""
    record = ConsentRecord(
        accepted_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        disclaimer_sha256=current_disclaimer_hash(),
        disclaimer_was_placeholder=constants.is_placeholder(constants.LONG_DISCLAIMER),
    )
    paths.ensure_dirs()
    target = paths.consent_file()
    with open(target, "w", encoding="utf-8") as handle:
        json.dump(
            {
                "accepted_at": record.accepted_at,
                "disclaimer_sha256": record.disclaimer_sha256,
                "disclaimer_was_placeholder": record.disclaimer_was_placeholder,
            },
            handle,
            indent=2,
        )
    paths._restrict(target)
    log.record(
        "consent.granted",
        disclaimer_sha256=record.disclaimer_sha256[:12],
        disclaimer_was_placeholder=record.disclaimer_was_placeholder,
    )
    return record


def revoke() -> None:
    """Delete the consent record. The gate re-opens on next run."""
    target = paths.consent_file()
    try:
        target.unlink()
    except FileNotFoundError:
        return
    except OSError:
        return
    log.record("consent.revoked")


def require() -> None:
    """Raise unless consent has been granted for the current disclaimer text.

    Called at the top of every operation that reads a document or contacts a
    model. Cheap enough to call freely; the point is that no path can skip it.
    """
    if not is_granted():
        raise ConsentRequired(
            "The disclaimer has not been accepted on this machine, "
            "or its wording has changed since it was accepted."
        )
