"""The proof of the privacy claim.

This project tells its users that no un-redacted content ever leaves their
machine. That is a promise nobody should have to take on trust, and reading the
whole redaction path to satisfy yourself is more than most people can do. This
file exists so the promise can be checked by running one command.

Each test isolates one link in the chain:

  1. The network function refuses anything that is not sealed.
  2. Sealed objects cannot be forged, mutated, or reconstructed.
  3. Sealing refuses while any detection is unreviewed.
  4. Sealing refuses if a confirmed name survives substitution.
  5. Only one module in the package can open a socket at all.

If every one of these passes, the claim holds. If any fails, it does not.
"""

from __future__ import annotations

import ast
import pickle
from pathlib import Path

import pytest

import jobmonger
from jobmonger import bridge, redaction
from jobmonger.intake import Document
from jobmonger.redaction import Review, Role, SealedText, SealError, detect, seal


def _reviewed(document: Document) -> Review:
    """A review with every person confirmed and everything else rejected."""
    review = Review(document, detect(document))
    for index, candidate in enumerate(review.detections):
        if candidate.kind is redaction.Kind.PERSON:
            review.confirm(index, Role.COWORKER)
        else:
            review.confirm(index, Role.CONTACT)
    return review


# -- 1. the network function refuses anything unsealed ----------------------


def test_send_refuses_a_raw_string():
    with pytest.raises(TypeError, match="sealed"):
        bridge.send("Sarah Chen was declined flexible hours.", "Summarise this.")


def test_stream_refuses_a_raw_string():
    with pytest.raises(TypeError, match="sealed"):
        # A generator body does not execute until iterated.
        next(bridge.stream("Sarah Chen was declined.", "Summarise this."))


def test_send_refuses_a_lookalike_object():
    """Duck typing must not be enough. The check is on the type, not the shape."""

    class NotSealed:
        text = "Sarah Chen was declined flexible hours."
        entity_count = 3
        source_name = "letter.txt"

        def restore(self, text):
            return text

    with pytest.raises(TypeError, match="sealed"):
        bridge.send(NotSealed(), "Summarise this.")


def test_send_refuses_a_subclass_shaped_impostor():
    """A subclass must not inherit its way past the gate.

    This test found a real hole. The gate originally used ``isinstance``, which
    accepts subclasses — and a subclass can override ``__init__`` to skip the
    mint entirely, producing an object that carries arbitrary un-redacted text
    and satisfies every check. The gate now compares the exact type.
    """

    class Sneaky(SealedText):
        def __init__(self):  # noqa: D401 - deliberately skips the mint
            object.__setattr__(self, "_text", "Sarah Chen was declined flexible hours.")
            object.__setattr__(self, "_token_map", {})
            object.__setattr__(self, "_entity_count", 0)
            object.__setattr__(self, "_source_name", "forged.txt")

    impostor = Sneaky()
    assert isinstance(impostor, SealedText), "the impostor really does pass isinstance"

    with pytest.raises(TypeError, match="sealed"):
        bridge.send(impostor, "Summarise this.")
    with pytest.raises(TypeError, match="sealed"):
        next(bridge.stream(impostor, "Summarise this."))


# -- 2. sealed objects cannot be forged, mutated, or reconstructed ----------


def test_sealed_text_cannot_be_constructed_directly():
    with pytest.raises(TypeError, match="cannot be constructed directly"):
        SealedText("anything at all", {}, "letter.txt")


def test_sealed_text_is_immutable(document, granted):
    sealed = seal(document, _reviewed(document))
    with pytest.raises(AttributeError):
        sealed._text = "Sarah Chen"
    with pytest.raises(AttributeError):
        del sealed._text


def test_sealed_text_cannot_be_pickled(document, granted):
    """Pickling would be a construction path that bypasses the mint."""
    sealed = seal(document, _reviewed(document))
    with pytest.raises(TypeError):
        pickle.dumps(sealed)


# -- 3. sealing refuses while review is incomplete --------------------------


def test_seal_refuses_with_unreviewed_detections(document, granted):
    review = Review(document, detect(document))
    assert review.pending(), "the sample document should produce detections"
    with pytest.raises(SealError, match="reviewed"):
        seal(document, review)


def test_seal_refuses_when_only_some_are_reviewed(document, granted):
    review = Review(document, detect(document))
    review.reject(0)
    with pytest.raises(SealError, match="reviewed"):
        seal(document, review)


# -- 4. sealing refuses if a confirmed name survives ------------------------


def test_confirmed_names_do_not_survive_sealing(document, granted):
    review = _reviewed(document)
    confirmed = review.confirmed_surfaces()
    assert confirmed, "the sample document should yield confirmed surfaces"

    sealed = seal(document, review)

    lowered = sealed.text.lower()
    for surface in confirmed:
        assert surface.lower() not in lowered, f"{surface!r} survived redaction"


def test_seal_detects_incomplete_substitution(document, granted, monkeypatch):
    """If substitution silently failed, sealing must fail too — not proceed.

    The residual scan is the backstop for a substitution bug. A backstop nobody
    has watched fire is not known to work, so this breaks substitution on
    purpose and asserts the scan catches it.
    """
    review = _reviewed(document)
    monkeypatch.setattr(redaction, "_apply_substitutions", lambda text, _replacements: text)
    with pytest.raises(SealError, match="did not fully apply"):
        seal(document, review)


def test_partial_substitution_is_caught_too(document, granted, monkeypatch):
    """Not just total failure — one missed name must fail the seal as well."""
    review = _reviewed(document)
    real = redaction._apply_substitutions

    def drop_one(text, replacements):
        return real(text, replacements[1:]) if len(replacements) > 1 else text

    monkeypatch.setattr(redaction, "_apply_substitutions", drop_one)
    with pytest.raises(SealError, match="did not fully apply"):
        seal(document, review)


def test_reseal_derived_refuses_content_carrying_a_confirmed_name(document, granted):
    sealed = seal(document, _reviewed(document))
    leaked = "The summary mentions Sarah Chen by name."
    with pytest.raises(SealError):
        redaction.reseal_derived(sealed, leaked)


def test_reseal_derived_accepts_clean_derived_content(document, granted):
    sealed = seal(document, _reviewed(document))
    clean = "ESTABLISHED FACTS\n\n- [COWORKER_1] declined a request from [COWORKER_2]."
    derived = redaction.reseal_derived(sealed, clean)
    assert isinstance(derived, SealedText)
    assert derived.source_name == sealed.source_name


# -- 5. only one module can open a socket ----------------------------------

_NETWORK_MODULES = {
    "urllib",
    "urllib.request",
    "http.client",
    "socket",
    "requests",
    "httpx",
    "aiohttp",
    "ssl",
}

#: bridge.py is the sanctioned egress point. ui/view.py binds a listening socket
#: on the loopback interface to serve the local view — it never dials out, and
#: its binding is asserted separately in test_ui.py.
_ALLOWED = {"bridge.py", "view.py"}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_only_the_bridge_can_reach_the_network():
    """A structural check that survives refactoring.

    The point is not that today's code is correct — the tests above establish
    that. It is that a future change which adds an HTTP call somewhere new
    fails here immediately, rather than quietly creating a second egress path
    that nobody thinks to audit.
    """
    package = Path(jobmonger.__file__).parent
    offenders: list[str] = []
    for source in package.rglob("*.py"):
        if source.name in _ALLOWED:
            continue
        found = _imports(source) & _NETWORK_MODULES
        if found:
            offenders.append(f"{source.relative_to(package)} imports {sorted(found)}")
    assert not offenders, (
        "Only bridge.py may reach the network. Found: " + "; ".join(offenders)
    )


def test_the_bridge_is_the_only_caller_of_urlopen():
    package = Path(jobmonger.__file__).parent
    offenders: list[str] = []
    for source in package.rglob("*.py"):
        if source.name == "bridge.py":
            continue
        if "urlopen" in source.read_text(encoding="utf-8"):
            offenders.append(str(source.relative_to(package)))
    assert not offenders, f"urlopen() called outside bridge.py: {offenders}"
