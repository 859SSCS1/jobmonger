"""The local view's three security properties, and that it actually serves.

``view.py`` is the only module besides ``bridge.py`` permitted to touch a
socket, so the egress suite exempts it by name. That exemption is only safe if
what it does with a socket is checked, which is what this file is for.
"""

from __future__ import annotations

import ast
import json
import re
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from jobmonger.ui import view
from jobmonger.ui.page import PAGE


@pytest.fixture
def running_server():
    server = view.ThreadingHTTPServer((view.BIND_HOST, 0), view.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    yield f"http://{host}:{port}", view.SESSION.token
    server.shutdown()
    server.server_close()


def _get(url: str, token: str | None):
    request = urllib.request.Request(url)
    if token:
        request.add_header("x-session-token", token)
    return urllib.request.urlopen(request, timeout=5)


# -- binding ----------------------------------------------------------------


def _code_strings(path: Path) -> set[str]:
    """Every string literal in a module except docstrings.

    Checking raw source text would match the prose in a docstring that *warns*
    about a value — which is how the first version of this test failed. Parsing
    means the check is about what the code does, not what it says.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = {
        node.body[0].value
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }
    return {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node not in docstrings
    }


def test_binds_loopback_only():
    """A tool promising nothing leaves must not open a port others can reach."""
    assert view.BIND_HOST == "127.0.0.1"
    literals = _code_strings(Path(view.__file__))
    for wildcard in ("0.0.0.0", "::"):
        assert wildcard not in literals, f"view.py must not bind {wildcard!r}"


def test_the_bind_address_is_the_constant_not_a_literal():
    """Every server construction must go through BIND_HOST.

    An empty host string binds every interface just as surely as "0.0.0.0" —
    but "" is far too common a default elsewhere to blacklist wholesale, so
    the check is on the call site instead of the literal.
    """
    tree = ast.parse(Path(view.__file__).read_text(encoding="utf-8"))
    constructions = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "ThreadingHTTPServer"
    ]
    assert constructions, "expected the view to construct a server"
    for call in constructions:
        address = call.args[0]
        assert isinstance(address, ast.Tuple), "bind address must be a (host, port) tuple"
        host = address.elts[0]
        assert isinstance(host, ast.Name) and host.id == "BIND_HOST", (
            "the server must bind BIND_HOST, not an inline literal"
        )


def test_the_view_never_dials_out():
    """It listens. It does not connect. Outbound belongs to bridge.py alone."""
    source = Path(view.__file__).read_text(encoding="utf-8")
    for forbidden in ("urlopen", "urlretrieve", "http.client", "requests.", "socket.create_connection"):
        assert forbidden not in source, f"view.py must not call {forbidden}"


def test_the_server_actually_binds_and_serves(running_server):
    base, token = running_server
    with _get(f"{base}/?token={token}", None) as response:
        assert response.status == 200
        assert "text/html" in response.headers["content-type"]


# -- token ------------------------------------------------------------------


def test_a_request_without_a_token_is_refused(running_server):
    base, _ = running_server
    with pytest.raises(urllib.error.HTTPError) as caught:
        _get(f"{base}/api/state", None)
    assert caught.value.code == 403


def test_a_request_with_a_wrong_token_is_refused(running_server):
    base, _ = running_server
    with pytest.raises(urllib.error.HTTPError) as caught:
        _get(f"{base}/api/state", "not-the-right-token")
    assert caught.value.code == 403


def test_a_request_with_the_token_is_served(running_server):
    base, token = running_server
    with _get(f"{base}/api/state", token) as response:
        payload = json.loads(response.read())
    assert payload["ok"] is True
    assert "consent_granted" in payload["state"]


def test_the_token_is_long_enough_to_be_unguessable():
    assert len(view.SESSION.token) >= 30


def test_token_comparison_is_constant_time():
    """Timing-safe comparison, not ==, so the token cannot be guessed byte-wise."""
    source = Path(view.__file__).read_text(encoding="utf-8")
    assert "compare_digest" in source


# -- the page ---------------------------------------------------------------


def test_the_page_loads_nothing_from_the_internet():
    """No CDN fonts, no remote scripts. It must work with the network unplugged.

    Checks for constructs that *fetch* a remote resource, rather than for the
    substring "https://" — which legitimately appears in the placeholder text
    of the endpoint-URL field, where it is something the user types, not
    something the page loads.
    """
    for marker in ('src="http', "src='http", 'href="http', "href='http",
                   "url(http", "@import", "<img", "<iframe", "<script src"):
        assert marker not in PAGE, f"page.py must not fetch a remote resource ({marker})"


def test_the_page_talks_only_to_its_own_origin():
    """Every fetch is a same-origin relative path."""
    for call in re.findall(r"""(?:fetch|EventSource)\(\s*["'`]([^"'`]*)""", PAGE):
        assert call.startswith("/"), f"non-relative request target: {call!r}"


def test_the_page_declares_a_restrictive_policy(running_server):
    base, token = running_server
    with _get(f"{base}/?token={token}", None) as response:
        policy = response.headers["content-security-policy"]
    assert "default-src 'none'" in policy
    assert "connect-src 'self'" in policy
    assert "form-action 'none'" in policy


def test_the_page_carries_the_session_token_placeholder():
    assert "__TOKEN__" in PAGE
    assert "__TITLE__" in PAGE


def test_responses_are_not_cached(running_server):
    """The page reflects a live session; a cached copy would show stale state."""
    base, token = running_server
    with _get(f"{base}/api/state", token) as response:
        assert response.headers["cache-control"] == "no-store"


# -- state shape ------------------------------------------------------------


def test_state_never_includes_the_key(running_server, granted):
    """The view reports whether a key exists and where from — never its value."""
    from jobmonger import config

    config.save(config.Config(), api_key="CANARY-not-a-real-key-must-not-appear")
    base, token = running_server
    with _get(f"{base}/api/state", token) as response:
        raw = response.read().decode("utf-8")
    assert "CANARY-not-a-real-key-must-not-appear" not in raw
    payload = json.loads(raw)
    assert payload["state"]["settings"]["key_present"] is True
