"""Atlas-specific trinity event kinds on top of ``research_portal.events``.

The generic event pipeline (ring buffer, broadcaster, redaction, SSE)
lives in :mod:`research_portal.events`. This module adds only the
Atlas-specific taxonomy: the :class:`EventKind` enum covering the
Superego/Id/Council architecture, plus convenience helpers.

Typed producers live in :mod:`atlas_portal.events_producer`.
"""

from __future__ import annotations

from enum import Enum

# Re-export generic pieces so consumers can write::
#
#     from atlas_portal.events import DashboardEvent, EventKind, publish
#
# without caring whether the buffer is generic or Atlas-specific.
from research_portal.events import (  # noqa: F401
    DashboardEvent as TrinityEvent,
)
from research_portal.events import (  # noqa: F401
    DashboardEventBuffer as TrinityEventBuffer,
)
from research_portal.events import (
    get_default_buffer,
    set_default_buffer,
)


class EventKind(str, Enum):
    """Enumerated trinity event kinds. Adding new kinds is an API bump.

    Each kind's string value is what flows on the SSE wire.
    """

    # Request-wide
    REQUEST_START = "request_start"
    REQUEST_END = "request_end"

    # Superego / Id
    SUPEREGO_START = "superego_start"
    SUPEREGO_END = "superego_end"
    ID_START = "id_start"
    ID_END = "id_end"

    # Council
    COUNCIL_START = "council_start"
    COUNCIL_MEMBER = "council_member"
    COUNCIL_DONE = "council_done"

    # Synthesis
    SYNTHESIS = "synthesis"

    # System
    BACKEND_HEALTH = "backend_health"
    FALLBACK_ACTIVATED = "fallback_activated"

    # Meta — used by consumers to detect liveness
    HEARTBEAT = "heartbeat"


__all__ = [
    "EventKind",
    "TrinityEvent",
    "TrinityEventBuffer",
    "get_default_buffer",
    "set_default_buffer",
]
