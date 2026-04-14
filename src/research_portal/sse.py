"""Generic Server-Sent Events endpoint and helpers.

Works on top of :mod:`research_portal.events`, so any dashboard using
the ``DashboardEventBuffer`` gets an SSE stream for free.

Why SSE and not WebSockets
--------------------------

- One-way server → client flow is all we need.
- SSE reconnects automatically with ``Last-Event-ID`` resumption.
- Works through common reverse proxies without special config.
- Simpler server-side — a generator function, not a new protocol.

Wire format
-----------

Each event on the stream is::

    id: 42
    event: <kind>
    data: {"trace_id": "...", "ts": ..., "payload": {...}}

``id`` is the monotonic seq from the buffer. On reconnect the browser
sends ``Last-Event-ID: 42`` and we resume from seq > 42.

Keepalive comments are sent whenever no events arrive in the wait
window so proxies don't close idle connections.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterator
from typing import Any

from research_portal.events import (
    DashboardEvent,
    DashboardEventBuffer,
    get_default_buffer,
)

logger = logging.getLogger(__name__)


# Each call to wait_for_next blocks up to this long before returning
# (possibly empty) to emit a keepalive.
_WAIT_WINDOW_SECONDS = 15.0

# Maximum events sent in a single flush. Protects against a burst of
# buffered events blowing out the connection.
_MAX_FLUSH = 200


def format_sse(event: DashboardEvent) -> str:
    """Format one :class:`DashboardEvent` as an SSE frame."""
    payload_json = json.dumps(
        {
            "trace_id": event.trace_id,
            "ts": event.ts,
            "payload": event.payload,
        },
        separators=(",", ":"),
    )
    return f"id: {event.seq}\nevent: {event.kind}\ndata: {payload_json}\n\n"


def format_keepalive() -> str:
    """Comment-only SSE line to keep connections alive through proxies."""
    return f": keepalive {int(time.time())}\n\n"


def sse_stream(
    buffer: DashboardEventBuffer | None = None,
    last_event_id: int = 0,
    *,
    wait_window: float = _WAIT_WINDOW_SECONDS,
    max_flush: int = _MAX_FLUSH,
    stop_after: float | None = None,
) -> Iterator[str]:
    """Generator producing SSE frames.

    Parameters
    ----------
    buffer:
        Event buffer to stream from. Defaults to the process-wide one.
    last_event_id:
        Resume cursor. Events with ``seq > last_event_id`` will be
        sent; older events are considered already delivered.
    wait_window:
        Maximum seconds to block on the buffer for new events before
        emitting a keepalive.
    max_flush:
        Maximum events per flush batch.
    stop_after:
        If set, the generator ends after this many seconds. Used by
        tests; in production generators run until the client drops.
    """
    buf = buffer or get_default_buffer()
    last_seq = max(0, int(last_event_id))

    # Initial catch-up: send anything buffered since the resume cursor.
    backlog = buf.since(last_seq)[:max_flush]
    for ev in backlog:
        yield format_sse(ev)
        last_seq = ev.seq

    start = time.monotonic()
    while True:
        if stop_after is not None and (time.monotonic() - start) >= stop_after:
            return

        events = buf.wait_for_next(last_seq, timeout=wait_window)
        if not events:
            yield format_keepalive()
            continue

        for ev in events[:max_flush]:
            yield format_sse(ev)
            last_seq = ev.seq


def parse_last_event_id(header_value: str | None) -> int:
    """Parse an incoming ``Last-Event-ID`` header.

    Returns 0 (stream from beginning) if the header is missing or
    malformed.
    """
    if not header_value:
        return 0
    try:
        return max(0, int(header_value))
    except (TypeError, ValueError):
        return 0


# ---------------------------------------------------------------------------
# Flask integration
# ---------------------------------------------------------------------------


def register_routes(
    app: Any,
    buffer: DashboardEventBuffer | None = None,
    *,
    stream_path: str = "/api/events/stream",
    snapshot_path: str = "/api/events/snapshot",
) -> None:
    """Register generic SSE routes on a Flask app.

    ``stream_path`` and ``snapshot_path`` can be overridden so a single
    Flask app can host multiple independent event streams on different
    URLs (e.g., trinity events vs alerts).
    """
    from flask import Response, request

    def trinity_events_stream() -> Response:  # type: ignore[misc]
        last_id = parse_last_event_id(request.headers.get("Last-Event-ID"))
        if "last_id" in request.args:
            try:
                last_id = max(last_id, int(request.args["last_id"]))
            except (TypeError, ValueError):
                pass

        def gen() -> Iterator[bytes]:
            try:
                for chunk in sse_stream(buffer=buffer, last_event_id=last_id):
                    yield chunk.encode("utf-8")
            except GeneratorExit:  # pragma: no cover - client disconnect
                logger.debug("SSE client disconnected (last_id=%s)", last_id)
                raise

        return Response(
            gen(),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-store, no-transform",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    def trinity_events_snapshot() -> Any:  # type: ignore[misc]
        from flask import jsonify

        buf = buffer or get_default_buffer()
        events = buf.snapshot()
        return jsonify(
            {
                "latest_seq": buf.latest_seq(),
                "events": [e.to_dict() for e in events],
            }
        )

    # Give each route a unique endpoint name so register_routes can be
    # called more than once (for multiple buffers on one app).
    stream_endpoint = f"sse_stream_{stream_path.replace('/', '_')}"
    snapshot_endpoint = f"sse_snapshot_{snapshot_path.replace('/', '_')}"
    app.add_url_rule(stream_path, endpoint=stream_endpoint, view_func=trinity_events_stream)
    app.add_url_rule(snapshot_path, endpoint=snapshot_endpoint, view_func=trinity_events_snapshot)


__all__ = [
    "format_keepalive",
    "format_sse",
    "parse_last_event_id",
    "register_routes",
    "sse_stream",
]
