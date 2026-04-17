"""Atlas Portal CLI entry point.

Usage::

    atlas-portal                      # run with defaults
    atlas-portal --host 0.0.0.0 --port 8443 --cert cert.pem --key key.pem
    atlas-portal --no-auth            # disable auth for local dev
    atlas-portal --user admin --password s3cret
"""

from __future__ import annotations

import argparse
import logging
import os
import ssl
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Atlas Portal — dashboard server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--no-auth", action="store_true")
    parser.add_argument("--no-heartbeat", action="store_true")
    parser.add_argument("--user", default=None, help="Override the Basic-auth username")
    parser.add_argument("--password", default=None, help="Override the Basic-auth password")
    parser.add_argument("--cert", default=None, help="TLS certificate file (PEM)")
    parser.add_argument("--key", default=None, help="TLS private key file (PEM)")
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

    # Push auth overrides into env vars before the app reads them.
    if args.user:
        os.environ["PORTAL_USER"] = args.user
    if args.password:
        os.environ["PORTAL_PASS"] = args.password

    # Imported after argparse so --help is fast and doesn't require Flask.
    from atlas_portal.app import build_app

    app = build_app(
        no_auth=args.no_auth,
        start_heartbeat=not args.no_heartbeat,
    )

    ssl_ctx = None
    if args.cert and args.key:
        ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ssl_ctx.load_cert_chain(args.cert, args.key)

    app.run(
        host=args.host,
        port=args.port,
        threaded=True,
        use_reloader=False,
        ssl_context=ssl_ctx,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
