"""Atlas Portal Flask application.

Atlas-specific dashboard built on top of the generic ``research_portal``
layer. Provides the Cortex / Council / Platform / Archive / Chat page
structure, the trinity event SSE stream, Basic-and-demo-token auth,
and Pitch Mode state.

Production scope today (Stage 2A): page shells, live data feeds, no
Trinity Live visualization yet. Stage 2B layers the viz on top of
these routes without requiring changes to the backend.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import threading
import time
from typing import Any

from flask import (
    Flask,
    Response,
    g,
    jsonify,
    make_response,
    redirect,
    render_template,
    request,
    url_for,
)

from atlas_portal import events_producer
from atlas_portal.replay import DeliberationRecorder
from atlas_portal.replay import register_routes as register_replay_routes
from research_portal.demo_tokens import validate_token
from research_portal.discovery import (
    get_cpu_temps,
    get_disk,
    get_gpu_info,
    get_load,
    get_memory,
    get_raid_status,
    get_system_info,
    get_tmux_sessions,
)
from research_portal.events import (
    DashboardEventBuffer,
    get_default_buffer,
)
from research_portal.sse import register_routes as register_sse_routes

_DEFAULT_ROLE_COOKIE = "atlas_role"
_PITCH_COOKIE = "atlas_pitch"


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def build_app(
    *,
    no_auth: bool = False,
    buffer: DashboardEventBuffer | None = None,
    start_heartbeat: bool = True,
) -> Flask:
    """Create the Atlas Portal Flask app.

    Parameters
    ----------
    no_auth:
        Skip authentication entirely. Useful for local development.
    buffer:
        Event buffer backing the SSE stream. Defaults to the process-
        wide singleton from :mod:`research_portal.events`.
    start_heartbeat:
        Start a background thread that emits a heartbeat event every
        15 s. Keeps the SSE stream "live" in development even when no
        real traffic is flowing. Disable in tests.
    """
    event_buffer = buffer or get_default_buffer()

    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config["SECRET_KEY"] = os.environ.get("PORTAL_SECRET", secrets.token_hex(32))

    recorder = DeliberationRecorder(event_buffer)
    recorder.attach()
    app.extensions = getattr(app, "extensions", {})  # type: ignore[attr-defined]
    app.extensions["atlas_replay_recorder"] = recorder

    _register_auth(app, no_auth=no_auth)
    _register_pitch_mode(app)
    _register_pages(app)
    _register_api(app, event_buffer)
    register_replay_routes(app, recorder)
    register_sse_routes(
        app,
        buffer=event_buffer,
        stream_path="/api/events/trinity",
        snapshot_path="/api/events/trinity/snapshot",
    )
    _register_security_headers(app)

    sysinfo = get_system_info()
    hostname = sysinfo["hostname"].split(".")[0].upper() or "ATLAS"

    @app.context_processor
    def inject_globals() -> dict[str, Any]:
        return {
            "hostname": hostname,
            "sysinfo": sysinfo,
            "pitch_mode": getattr(g, "pitch_mode", False),
            "role": getattr(g, "portal_role", "admin"),
        }

    if start_heartbeat:
        _start_heartbeat_thread(event_buffer)

    return app


# ---------------------------------------------------------------------------
# Auth (Basic + demo-token)
# ---------------------------------------------------------------------------


def _register_auth(app: Flask, *, no_auth: bool) -> None:
    """Wrap every protected route with Basic + demo-token auth.

    Demo tokens come in as ``?demo=<token>`` on any request. If present
    and valid, the role is taken from the token and the request is
    allowed through. Otherwise, fall back to HTTP Basic with the admin
    credentials from ``PORTAL_USER`` / ``PORTAL_PASS``.
    """
    admin_user = os.environ.get("PORTAL_USER", "atlas")
    admin_hash = hashlib.sha256(
        os.environ.get("PORTAL_PASS", "atlas2026!research").encode()
    ).hexdigest()

    def _check_basic(username: str, password: str) -> str | None:
        if username == admin_user and (
            hashlib.sha256(password.encode()).hexdigest() == admin_hash
        ):
            return "admin"
        return None

    @app.before_request
    def _auth_hook() -> Response | None:
        if no_auth:
            g.portal_role = "admin"
            return None

        # Static assets do not require auth; they never return sensitive data.
        if request.path.startswith("/static/"):
            return None

        # Demo token path
        token = request.args.get("demo") or request.cookies.get(_DEFAULT_ROLE_COOKIE)
        if token:
            result = validate_token(token)
            if result is not None:
                role, _expires = result
                g.portal_role = role
                return None

        # Basic auth path
        auth = request.authorization
        if auth:
            role = _check_basic(auth.username or "", auth.password or "")
            if role is not None:
                g.portal_role = role
                return None

        return Response(
            "Authentication required.",
            401,
            {"WWW-Authenticate": 'Basic realm="Atlas Portal"'},
        )


# ---------------------------------------------------------------------------
# Pitch Mode (first-class state)
# ---------------------------------------------------------------------------
#
# Per design: Pitch Mode is NOT a CSS toggle. It is a persistent request-
# scoped state that (a) is read by templates to change what's rendered,
# and (b) is settable via a dedicated route so it can be activated from
# the UI and survives page navigation via cookie.


def _register_pitch_mode(app: Flask) -> None:
    @app.before_request
    def _load_pitch() -> None:
        # URL arg overrides cookie, so ?pitch=1 works for ad-hoc toggles.
        val = request.args.get("pitch")
        if val is not None:
            g.pitch_mode = val in ("1", "true", "yes", "on")
        else:
            g.pitch_mode = request.cookies.get(_PITCH_COOKIE) == "1"

    @app.route("/mode/pitch/<state>", methods=["POST", "GET"])
    def set_pitch(state: str) -> Response:  # type: ignore[misc]
        desired = state in ("on", "1", "true", "enable")
        resp = make_response(redirect(request.args.get("next") or url_for("cortex")))
        resp.set_cookie(
            _PITCH_COOKIE,
            "1" if desired else "",
            max_age=60 * 60 * 8 if desired else 0,
            httponly=True,
            samesite="Lax",
        )
        return resp


# ---------------------------------------------------------------------------
# Page routes
# ---------------------------------------------------------------------------


def _register_pages(app: Flask) -> None:
    @app.route("/")
    def cortex() -> Any:  # type: ignore[misc]
        return render_template("cortex.html", page="cortex")

    @app.route("/council")
    def council() -> Any:  # type: ignore[misc]
        return render_template("council.html", page="council")

    @app.route("/platform")
    def platform() -> Any:  # type: ignore[misc]
        return render_template("platform.html", page="platform")

    @app.route("/archive")
    def archive() -> Any:  # type: ignore[misc]
        return render_template("archive.html", page="archive")

    @app.route("/chat")
    def chat() -> Any:  # type: ignore[misc]
        return render_template("chat.html", page="chat")

    @app.route("/healthz")
    def healthz() -> Any:  # type: ignore[misc]
        return jsonify({"status": "ok"})


# ---------------------------------------------------------------------------
# API routes (thin wrappers over research_portal.discovery)
# ---------------------------------------------------------------------------


def _register_api(app: Flask, buffer: DashboardEventBuffer) -> None:
    @app.route("/api/status")
    def api_status() -> Any:  # type: ignore[misc]
        return jsonify(
            {
                "cpu_temps": get_cpu_temps(),
                "gpu": get_gpu_info(),
                "memory": get_memory(),
                "load": get_load(),
                "disk": get_disk(),
                "raid": get_raid_status(),
                "sessions": get_tmux_sessions(),
                "latest_event_seq": buffer.latest_seq(),
            }
        )


# ---------------------------------------------------------------------------
# Security headers
# ---------------------------------------------------------------------------


def _register_security_headers(app: Flask) -> None:
    @app.after_request
    def _headers(response: Response) -> Response:
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        # CSP: allow inline styles + scripts because the templates use them,
        # and allow self for EventSource (SSE). Tighten later.
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:;",
        )
        return response


# ---------------------------------------------------------------------------
# Heartbeat thread (dev convenience)
# ---------------------------------------------------------------------------


def _start_heartbeat_thread(buffer: DashboardEventBuffer) -> None:
    """Emit a heartbeat event every 15 s on a daemon thread.

    Keeps SSE connections visibly alive during dev; production deployments
    should get real traffic.
    """

    def _loop() -> None:
        while True:
            try:
                events_producer.heartbeat(buffer=buffer)
            except Exception:  # pragma: no cover - defensive
                pass
            time.sleep(15.0)

    t = threading.Thread(target=_loop, name="atlas-heartbeat", daemon=True)
    t.start()


__all__ = ["build_app"]
