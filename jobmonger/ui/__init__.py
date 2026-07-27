"""[UI] — the local view.

Served from this machine to this machine, on the loopback interface only, by
the standard library. No web framework, no bundler, no external assets: the
page has to work with the network unplugged, and a tool that promises nothing
leaves should not be quietly fetching a font from a CDN.
"""

from .view import serve

__all__ = ["serve"]
