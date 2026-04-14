"""Typed producers for Atlas trinity events.

Producers are the *only* sanctioned way to put events onto the pipeline.
Each function corresponds to one :class:`EventKind` and enforces payload
shape. This keeps the schema honest and prevents callers from publishing
unredacted fields by accident.

Every producer accepts a buffer argument (default: process-wide
buffer) and a trace_id. All producer calls are safe to invoke from
any thread.
"""

from __future__ import annotations

import time
from typing import Any

from atlas_portal.events import EventKind
from research_portal.events import (
    DashboardEvent,
    DashboardEventBuffer,
    get_default_buffer,
)


def _publish(
    kind: EventKind,
    trace_id: str,
    payload: dict[str, Any] | None = None,
    buffer: DashboardEventBuffer | None = None,
) -> DashboardEvent:
    buf = buffer or get_default_buffer()
    return buf.publish(
        DashboardEvent(
            kind=kind.value,
            trace_id=trace_id,
            ts=time.time(),
            payload=payload or {},
        )
    )


# ---------------------------------------------------------------------------
# Request lifecycle
# ---------------------------------------------------------------------------


def request_start(
    trace_id: str,
    *,
    query_preview: str,
    buffer: DashboardEventBuffer | None = None,
) -> DashboardEvent:
    """A new user request enters the system."""
    return _publish(
        EventKind.REQUEST_START,
        trace_id,
        {"query_preview": query_preview},
        buffer,
    )


def request_end(
    trace_id: str,
    *,
    latency_ms: float,
    buffer: DashboardEventBuffer | None = None,
) -> DashboardEvent:
    return _publish(
        EventKind.REQUEST_END,
        trace_id,
        {"latency_ms": latency_ms},
        buffer,
    )


# ---------------------------------------------------------------------------
# Superego / Id
# ---------------------------------------------------------------------------


def superego_start(
    trace_id: str,
    *,
    model: str = "",
    buffer: DashboardEventBuffer | None = None,
) -> DashboardEvent:
    return _publish(EventKind.SUPEREGO_START, trace_id, {"model": model}, buffer)


def superego_end(
    trace_id: str,
    *,
    latency_ms: float,
    text_preview: str = "",
    buffer: DashboardEventBuffer | None = None,
) -> DashboardEvent:
    return _publish(
        EventKind.SUPEREGO_END,
        trace_id,
        {"latency_ms": latency_ms, "text_preview": text_preview},
        buffer,
    )


def id_start(
    trace_id: str,
    *,
    model: str = "",
    buffer: DashboardEventBuffer | None = None,
) -> DashboardEvent:
    return _publish(EventKind.ID_START, trace_id, {"model": model}, buffer)


def id_end(
    trace_id: str,
    *,
    latency_ms: float,
    text_preview: str = "",
    buffer: DashboardEventBuffer | None = None,
) -> DashboardEvent:
    return _publish(
        EventKind.ID_END,
        trace_id,
        {"latency_ms": latency_ms, "text_preview": text_preview},
        buffer,
    )


# ---------------------------------------------------------------------------
# Council
# ---------------------------------------------------------------------------


def council_start(
    trace_id: str,
    *,
    member_count: int = 7,
    buffer: DashboardEventBuffer | None = None,
) -> DashboardEvent:
    return _publish(
        EventKind.COUNCIL_START,
        trace_id,
        {"member_count": member_count},
        buffer,
    )


def council_member(
    trace_id: str,
    *,
    member: str,
    outcome: str,
    latency_ms: float,
    score: float = 5.0,
    buffer: DashboardEventBuffer | None = None,
) -> DashboardEvent:
    """One council member's outcome.

    ``outcome`` is one of ``approve``, ``challenge``, ``abstain``.
    """
    return _publish(
        EventKind.COUNCIL_MEMBER,
        trace_id,
        {
            "member": member,
            "outcome": outcome,
            "latency_ms": latency_ms,
            "score": score,
        },
        buffer,
    )


def council_done(
    trace_id: str,
    *,
    consensus: bool,
    degraded: bool,
    ethical_veto: bool,
    approval_count: int,
    challenge_count: int,
    abstain_count: int,
    latency_ms: float,
    buffer: DashboardEventBuffer | None = None,
) -> DashboardEvent:
    return _publish(
        EventKind.COUNCIL_DONE,
        trace_id,
        {
            "consensus": consensus,
            "degraded": degraded,
            "ethical_veto": ethical_veto,
            "approval_count": approval_count,
            "challenge_count": challenge_count,
            "abstain_count": abstain_count,
            "latency_ms": latency_ms,
        },
        buffer,
    )


def synthesis(
    trace_id: str,
    *,
    text_preview: str,
    buffer: DashboardEventBuffer | None = None,
) -> DashboardEvent:
    return _publish(
        EventKind.SYNTHESIS,
        trace_id,
        {"text_preview": text_preview},
        buffer,
    )


# ---------------------------------------------------------------------------
# System
# ---------------------------------------------------------------------------


def backend_health(
    trace_id: str = "",
    *,
    backend: str,
    healthy: bool,
    buffer: DashboardEventBuffer | None = None,
) -> DashboardEvent:
    return _publish(
        EventKind.BACKEND_HEALTH,
        trace_id or "system",
        {"backend": backend, "healthy": healthy},
        buffer,
    )


def fallback_activated(
    trace_id: str,
    *,
    from_backend: str,
    to_backend: str,
    reason: str = "",
    buffer: DashboardEventBuffer | None = None,
) -> DashboardEvent:
    return _publish(
        EventKind.FALLBACK_ACTIVATED,
        trace_id,
        {
            "from_backend": from_backend,
            "to_backend": to_backend,
            "reason": reason,
        },
        buffer,
    )


def heartbeat(
    buffer: DashboardEventBuffer | None = None,
) -> DashboardEvent:
    """Periodic heartbeat. SSE clients use this to detect liveness."""
    return _publish(EventKind.HEARTBEAT, "system", {}, buffer)


__all__ = [
    "backend_health",
    "council_done",
    "council_member",
    "council_start",
    "fallback_activated",
    "heartbeat",
    "id_end",
    "id_start",
    "request_end",
    "request_start",
    "superego_end",
    "superego_start",
    "synthesis",
]
