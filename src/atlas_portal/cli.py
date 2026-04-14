"""Atlas Portal CLI entry point.

Usage::

    atlas-portal                      # run with defaults
    atlas-portal --host 0.0.0.0 --port 8080
    atlas-portal --no-auth            # disable auth for local dev
"""

from __future__ import annotations

import argparse
import logging
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Atlas Portal — dashboard server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--no-auth", action="store_true")
    parser.add_argument("--no-heartbeat", action="store_true")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # Imported after argparse so --help is fast and doesn't require Flask.
    from atlas_portal.app import build_app

    app = build_app(
        no_auth=args.no_auth,
        start_heartbeat=not args.no_heartbeat,
    )
    app.run(host=args.host, port=args.port, threaded=True, use_reloader=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
