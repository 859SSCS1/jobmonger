"""Start the local view: ``python -m jobmonger``.

Deliberately minimal. This is not the CLI referred to in DECISIONS.md item P1 —
it only launches the view. A scriptable command-line interface over the core
library remains unbuilt and is a thin wrapper whenever it is wanted.
"""

from __future__ import annotations

import argparse
import sys

from .ui.view import serve


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="jobmonger",
        description="Open the local view. Everything runs on this computer.",
    )
    parser.add_argument("--port", type=int, default=0,
                        help="port to listen on (default: ask the OS for a free one)")
    parser.add_argument("--no-browser", action="store_true",
                        help="do not open a browser window automatically")
    args = parser.parse_args(argv)

    server = serve(open_browser=not args.no_browser, port=args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("  Stopped.\n")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
