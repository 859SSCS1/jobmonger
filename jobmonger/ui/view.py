"""The local view's HTTP server. Loopback only, single-use token, no framework.

Three properties this file must hold, and which ``tests/test_ui.py`` checks:

* It binds ``127.0.0.1`` and never ``0.0.0.0``. A tool whose promise is that
  nothing leaves the machine must not open a port other machines can reach.
* Every request must carry the session token printed at startup. Loopback alone
  is not access control — anything else running as this user could otherwise
  drive the tool.
* It never sends anything outward. ``bridge`` does that, and only that.
"""

from __future__ import annotations

import json
import secrets
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from .. import config as config_module
from .. import consent, constants, dial, facts, friction, intake, log, redaction
from ..bridge import BridgeError, check_reachable
from ..intake import IntakeError
from ..redaction import Review, Role, SealError
from .page import PAGE

#: PROVISIONAL (DECISIONS.md N4) — the repository name, not a product name.
APP_TITLE = "jobmonger"

BIND_HOST = "127.0.0.1"


class Session:
    """Everything the current session holds. In memory only; nothing persists.

    One document at a time, deliberately. A queue of documents would need a
    place to keep them, and this tool does not write documents to disk.
    """

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.token = secrets.token_urlsafe(24)
        self.document: intake.Document | None = None
        self.review: Review | None = None
        self.sealed: redaction.SealedText | None = None
        self.fact_set: facts.FactSet | None = None
        self.last_error: str = ""

    def reset_document(self) -> None:
        self.document = None
        self.review = None
        self.sealed = None
        self.fact_set = None


SESSION = Session()


def _json_response(handler: BaseHTTPRequestHandler, payload: dict, status: int = 200) -> None:
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("content-type", "application/json; charset=utf-8")
    handler.send_header("content-length", str(len(body)))
    handler.send_header("cache-control", "no-store")
    # The page loads no external resources; say so in a header the browser enforces.
    handler.send_header(
        "content-security-policy",
        "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
        "connect-src 'self'; img-src 'none'; font-src 'none'; form-action 'none'; "
        "base-uri 'none'; frame-ancestors 'none'",
    )
    handler.end_headers()
    handler.wfile.write(body)


def _state() -> dict[str, Any]:
    cfg = config_module.load()
    session = SESSION
    detections = []
    if session.review is not None:
        pending = set(session.review.pending())
        for index, candidate in enumerate(session.review.detections):
            assigned = session.review._assignment.get(index)
            detections.append(
                {
                    "index": index,
                    "start": candidate.start,
                    "end": candidate.end,
                    "surface": candidate.surface,
                    "kind": candidate.kind.value,
                    "confidence": candidate.confidence.value,
                    "reason": candidate.reason,
                    "suggested_role": (
                        session.review.entities[candidate.suggested_entity_id].role.value
                        if candidate.suggested_entity_id
                        and candidate.suggested_entity_id in session.review.entities
                        else None
                    ),
                    "pending": index in pending,
                    "entity_id": assigned,
                    "role": (
                        session.review.entities[assigned].role.value
                        if assigned and assigned in session.review.entities
                        else None
                    ),
                }
            )

    entities = []
    if session.review is not None:
        tokens = redaction.assign_tokens(session.review.entities.values())
        for entity in session.review.entities.values():
            entities.append(
                {
                    "entity_id": entity.entity_id,
                    "role": entity.role.value,
                    "token": tokens.get(entity.entity_id, ""),
                    "surfaces": sorted(entity.surfaces),
                    "team_size": entity.team_size,
                }
            )

    fact_payload = None
    if session.fact_set is not None:
        fact_payload = {
            "facts": [
                {"statement": f.statement, "quote": f.quote, "certainty": f.certainty}
                for f in session.fact_set.facts
            ],
            "gaps": list(session.fact_set.gaps),
            "counts": session.fact_set.certainty_counts(),
            "model": session.fact_set.model,
        }

    return {
        "consent_granted": consent.is_granted(),
        "long_disclaimer": constants.LONG_DISCLAIMER,
        "short_disclaimer": constants.SHORT_DISCLAIMER,
        "placeholders": constants.all_placeholders(),
        "settings": {
            "provider": cfg.provider,
            "model": cfg.model,
            "base_url": cfg.base_url,
            "redact_company_name": cfg.redact_company_name,
            "redact_own_name": cfg.redact_own_name,
            "own_name": cfg.own_name,
            "company_name": cfg.company_name,
            "dial_position": cfg.dial_position,
            "key_source": config_module.key_source(cfg.provider),
            "key_present": bool(config_module.resolve_key(cfg.provider)),
        },
        "document": (
            {
                "source_name": session.document.source_name,
                "text": session.document.text,
                "words": session.document.word_count,
            }
            if session.document
            else None
        ),
        "detections": detections,
        "entities": entities,
        "pending_count": len(session.review.pending()) if session.review else 0,
        "reid_warnings": session.review.reidentification_warnings() if session.review else [],
        "sealed": (
            {
                "text": session.sealed.text,
                "entity_count": session.sealed.entity_count,
                "tokens": list(session.sealed.tokens),
            }
            if session.sealed
            else None
        ),
        "facts": fact_payload,
        "dial": {
            "positions": [{"value": v, "label": l} for v, l in dial.positions()],
            "current": cfg.dial_position,
        },
        "roles": [r.value for r in Role],
        "last_error": session.last_error,
    }


# --------------------------------------------------------------------------
# Actions
# --------------------------------------------------------------------------


def _act_consent(_: dict) -> dict:
    consent.grant()
    return {"ok": True}


def _act_settings(body: dict) -> dict:
    cfg = config_module.load()
    provider = body.get("provider", cfg.provider)
    updated = config_module.Config(
        provider=provider if provider in ("anthropic", "openai_compatible") else cfg.provider,
        model=str(body.get("model") or cfg.model),
        base_url=str(body.get("base_url", cfg.base_url)),
        redact_company_name=bool(body.get("redact_company_name", cfg.redact_company_name)),
        redact_own_name=bool(body.get("redact_own_name", cfg.redact_own_name)),
        own_name=str(body.get("own_name", cfg.own_name)),
        company_name=str(body.get("company_name", cfg.company_name)),
        dial_position=int(body.get("dial_position", cfg.dial_position)),
    )
    key = body.get("api_key")
    config_module.save(updated, api_key=key if isinstance(key, str) else None)
    return {"ok": True}


def _act_check(_: dict) -> dict:
    return {"ok": True, "model": check_reachable()}


def _act_open(body: dict) -> dict:
    session = SESSION
    session.reset_document()
    path = str(body.get("path", "")).strip()
    text = str(body.get("text", "")).strip()
    if path:
        document = intake.load(path)
    elif text:
        document = intake.from_text(text)
    else:
        raise IntakeError("Choose a file or paste some text first.")

    cfg = config_module.load()
    policy = redaction.DetectionPolicy(
        include_company=cfg.redact_company_name,
        include_own_name=cfg.redact_own_name,
        own_name=cfg.own_name,
        company_name=cfg.company_name,
    )
    session.document = document
    session.review = Review(document, redaction.detect(document, policy))
    return {"ok": True}


def _require_review() -> Review:
    if SESSION.review is None:
        raise IntakeError("Open a document first.")
    return SESSION.review


def _act_decide(body: dict) -> dict:
    review = _require_review()
    index = int(body["index"])
    decision = str(body.get("decision", ""))
    if decision == "reject":
        review.reject(index)
    elif decision == "reject_all":
        review.reject_all_matching(review.detections[index].surface)
    elif decision == "confirm":
        role = Role(body.get("role", Role.COWORKER.value))
        team = body.get("team_size")
        team_size = int(team) if isinstance(team, (int, str)) and str(team).strip().isdigit() else None
        if body.get("all_matching"):
            review.confirm_all_matching(index, role)
            if team_size is not None:
                entity_id = review._assignment.get(index)
                if entity_id:
                    review.entities[entity_id].team_size = team_size
        else:
            review.confirm(index, role, team_size=team_size)
    else:
        raise IntakeError("Unrecognised decision.")
    # Confirming a full name can leave bare first-name or surname mentions
    # elsewhere in the document. Components of a name the user just confirmed
    # fold into that same person; unfamiliar bare names stay pending.
    review.absorb_partials()
    return {"ok": True}


def _act_add(body: dict) -> dict:
    review = _require_review()
    surface = str(body.get("surface", ""))
    role = Role(body.get("role", Role.COWORKER.value))
    team = body.get("team_size")
    team_size = int(team) if isinstance(team, (int, str)) and str(team).strip().isdigit() else None
    review.add_manual(surface, role, team_size=team_size)
    review.absorb_partials()
    return {"ok": True}


def _act_reject_all_pending(_: dict) -> dict:
    """Reject everything still pending. For a document with no real names in it."""
    review = _require_review()
    count = 0
    for index in list(review.pending()):
        review.reject(index)
        count += 1
    return {"ok": True, "rejected": count}


def _act_seal(_: dict) -> dict:
    session = SESSION
    review = _require_review()
    assert session.document is not None
    session.sealed = redaction.seal(session.document, review)
    session.fact_set = facts.extract(session.sealed)
    return {"ok": True}


def _act_friction(body: dict) -> dict:
    if SESSION.fact_set is None:
        raise IntakeError("Nothing to confirm against yet.")
    restatement = friction.for_max_advocacy(SESSION.fact_set)
    if "accepted" in body:
        return {"ok": True, "proceed": friction.confirm(restatement, bool(body["accepted"]))}
    return {
        "ok": True,
        "headline": restatement.headline,
        "detail": restatement.detail,
        "anchor": restatement.anchor,
    }


def _act_log(_: dict) -> dict:
    return {"ok": True, "entries": log.read_all(limit=200)}


ACTIONS: dict[str, Callable[[dict], dict]] = {
    "consent": _act_consent,
    "settings": _act_settings,
    "check": _act_check,
    "open": _act_open,
    "decide": _act_decide,
    "add": _act_add,
    "reject_all_pending": _act_reject_all_pending,
    "seal": _act_seal,
    "friction": _act_friction,
    "log": _act_log,
}


# --------------------------------------------------------------------------
# Handler
# --------------------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    server_version = "jobmonger-local"
    sys_version = ""

    def log_message(self, *_args) -> None:  # noqa: D102
        # Silence stdout request logging. The user's terminal should show them
        # the URL and nothing else; the audit trail is the log file.
        return

    # -- auth --------------------------------------------------------------

    def _authorised(self) -> bool:
        header = self.headers.get("x-session-token", "")
        if secrets.compare_digest(header, SESSION.token):
            return True
        query = parse_qs(urlparse(self.path).query)
        supplied = (query.get("token") or [""])[0]
        return secrets.compare_digest(supplied, SESSION.token)

    def _deny(self) -> None:
        _json_response(self, {"ok": False, "error": "Not authorised for this session."}, 403)

    # -- routes ------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802
        route = urlparse(self.path).path
        if not self._authorised():
            self._deny()
            return

        if route == "/":
            body = PAGE.replace("__TOKEN__", SESSION.token).replace("__TITLE__", APP_TITLE)
            encoded = body.encode("utf-8")
            self.send_response(200)
            self.send_header("content-type", "text/html; charset=utf-8")
            self.send_header("content-length", str(len(encoded)))
            self.send_header("cache-control", "no-store")
            self.send_header(
                "content-security-policy",
                "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
                "connect-src 'self'; img-src 'none'; font-src 'none'; form-action 'none'; "
                "base-uri 'none'; frame-ancestors 'none'",
            )
            self.end_headers()
            self.wfile.write(encoded)
            return

        if route == "/api/state":
            with SESSION.lock:
                _json_response(self, {"ok": True, "state": _state()})
            return

        if route == "/api/read":
            self._stream_reading()
            return

        _json_response(self, {"ok": False, "error": "Unknown route."}, 404)

    def do_POST(self) -> None:  # noqa: N802
        route = urlparse(self.path).path
        if not self._authorised():
            self._deny()
            return
        if not route.startswith("/api/"):
            _json_response(self, {"ok": False, "error": "Unknown route."}, 404)
            return

        action = route[len("/api/") :]
        handler = ACTIONS.get(action)
        if handler is None:
            _json_response(self, {"ok": False, "error": "Unknown action."}, 404)
            return

        try:
            length = int(self.headers.get("content-length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            body = json.loads(raw.decode("utf-8") or "{}")
        except (ValueError, json.JSONDecodeError):
            _json_response(self, {"ok": False, "error": "Malformed request."}, 400)
            return

        try:
            with SESSION.lock:
                result = handler(body)
                SESSION.last_error = ""
                result["state"] = _state()
            _json_response(self, result)
        except (IntakeError, SealError, BridgeError, consent.ConsentRequired, ValueError, KeyError) as exc:
            SESSION.last_error = str(exc)
            _json_response(self, {"ok": False, "error": str(exc)}, 400)
        except Exception as exc:  # noqa: BLE001
            SESSION.last_error = f"Unexpected problem: {exc}"
            _json_response(self, {"ok": False, "error": SESSION.last_error}, 500)

    # -- streaming ---------------------------------------------------------

    def _stream_reading(self) -> None:
        """Server-sent events carrying a dialed reading as it is produced."""
        query = parse_qs(urlparse(self.path).query)
        position = int((query.get("position") or ["2"])[0])
        question = (query.get("question") or [""])[0]

        session = SESSION
        if session.fact_set is None or session.sealed is None:
            _json_response(self, {"ok": False, "error": "Review and seal a document first."}, 400)
            return

        self.send_response(200)
        self.send_header("content-type", "text/event-stream; charset=utf-8")
        self.send_header("cache-control", "no-store")
        self.send_header("connection", "close")
        self.end_headers()

        def emit(event: str, data: dict) -> None:
            chunk = f"event: {event}\ndata: {json.dumps(data)}\n\n".encode("utf-8")
            self.wfile.write(chunk)
            self.wfile.flush()

        try:
            for piece in dial.render(session.fact_set, session.sealed, position, question=question):
                # Real names are put back here, on this machine, for display
                # only. They were never sent.
                emit("delta", {"text": session.sealed.restore(piece)})
            emit("done", {"position": dial.clamp(position), "label": dial.label(position)})
        except (BridgeError, SealError) as exc:
            emit("error", {"message": str(exc)})
        except Exception as exc:  # noqa: BLE001
            emit("error", {"message": f"Unexpected problem: {exc}"})


def serve(*, open_browser: bool = True, port: int = 0) -> ThreadingHTTPServer:
    """Start the local view. Returns the server; caller runs it.

    ``port=0`` asks the OS for an unused port, so nothing is squatting on a
    predictable one between runs.
    """
    server = ThreadingHTTPServer((BIND_HOST, port), Handler)
    host, bound_port = server.server_address[:2]
    url = f"http://{host}:{bound_port}/?token={SESSION.token}"

    log.record("ui.started", port=bound_port, host=str(host))
    print(f"\n  {APP_TITLE} is running on this machine only.")
    print(f"  {url}\n")
    print("  Nothing is published. This address works from this computer alone,")
    print("  and only with the token in the link above.")
    outstanding = constants.all_placeholders()
    if outstanding:
        # Names what is actually outstanding rather than saying "the disclaimers"
        # generically. Both user-facing disclaimers are final; what remains is
        # the model-facing guardrail text, which a reader of this warning would
        # otherwise reasonably assume meant the legal text was unfinished.
        print(f"\n  Note: awaiting final wording for {', '.join(outstanding)}.")
        print("  Both user-facing disclaimers are final. See DECISIONS.md item B1.")
    print("\n  Press Ctrl+C to stop.\n")

    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    return server
