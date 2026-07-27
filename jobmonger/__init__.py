"""A local-first advocate for the individual employee.

Nothing in this package sends data anywhere except through a single function —
``jobmonger.bridge.send`` — and that function accepts only a ``SealedText``
object, which only ``jobmonger.redaction.seal`` can construct. That is the whole
privacy claim, and ``tests/test_egress.py`` is its proof.

Read in this order to audit the loyalty claim:

1. ``constants.py``  — the disclaimers and guardrails, single-sourced
2. ``redaction.py``  — detection, confirmation, and the sealed chokepoint
3. ``bridge.py``     — the only code in the package that opens a socket
4. ``facts.py``      — why the dial cannot change the facts
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
