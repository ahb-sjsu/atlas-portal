"""CLI entry point for research-portal."""

from __future__ import annotations

import argparse
import os
import ssl
import sys


def main(argv: list[str] | None = None) -> None:
    """Parse arguments and start the Research Portal server."""
    parser = argparse.ArgumentParser(
        prog="research-portal",
        description="Zero-config research workstation dashboard with automatic pipeline discovery",
    )
    parser.add_argument("--host", default="0.0.0.0", help="Bind address (default: 0.0.0.0)")
    parser.add_argument(
        "--port", type=int, default=None, help="Port (default: 8443 with SSL, 8080 without)"
    )
    parser.add_argument("--no-auth", action="store_true", help="Disable HTTP Basic authentication")
    parser.add_argument("--user", default=None, help="Override PORTAL_USER env var")
    parser.add_argument("--password", default=None, help="Override PORTAL_PASS env var")
    parser.add_argument(
        "--no-ssl", action="store_true", help="Force plain HTTP even if certs are present"
    )
    parser.add_argument("--cert", default=None, help="Path to SSL certificate (cert.pem)")
    parser.add_argument("--key", default=None, help="Path to SSL private key (key.pem)")
    parser.add_argument("--version", action="store_true", help="Print version and exit")

    args = parser.parse_args(argv)

    if args.version:
        from research_portal import __version__

        print(f"research-portal {__version__}")
        sys.exit(0)

    # Propagate CLI overrides into env vars so the app picks them up.
    if args.user:
        os.environ["PORTAL_USER"] = args.user
    if args.password:
        os.environ["PORTAL_PASS"] = args.password

    from research_portal.app import build_app

    app = build_app(no_auth=args.no_auth)

    # SSL detection ----------------------------------------------------------
    ssl_context = None
    default_port = 8080

    if not args.no_ssl:
        # Explicit --cert/--key takes priority, then auto-detect.
        cert_path = args.cert
        key_path = args.key
        if cert_path and key_path:
            ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ssl_context.load_cert_chain(cert_path, key_path)
            default_port = 8443
        else:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            search_dirs = [os.getcwd(), script_dir, os.path.dirname(script_dir)]
            for d in search_dirs:
                cp = os.path.join(d, "cert.pem")
                kp = os.path.join(d, "key.pem")
                if os.path.exists(cp) and os.path.exists(kp):
                    ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                    ssl_context.load_cert_chain(cp, kp)
                    default_port = 8443
                    break

    port = args.port if args.port is not None else default_port
    scheme = "https" if ssl_context else "http"

    print(f"Research Portal v{_version()} starting")
    print(f"  URL: {scheme}://{args.host}:{port}")
    if args.no_auth:
        print("  Auth: DISABLED")
    else:
        print(f"  Auth: enabled (user={os.environ.get('PORTAL_USER', 'atlas')})")
    print()

    app.run(host=args.host, port=port, ssl_context=ssl_context, debug=False)


def _version() -> str:
    from research_portal import __version__

    return __version__


if __name__ == "__main__":
    main()
