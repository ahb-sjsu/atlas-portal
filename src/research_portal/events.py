"""Generic dashboard event pipeline: schema, ring buffer, broadcaster.

This is the generic, research-workstation-agnostic event infrastructure.
Any dashboard wanting a live-events panel can use this directly.
Domain-specific extensions (e.g., atlas_portal's trinity event kinds
and typed producers) build on top.

Invariants
----------

1. **Ordering.** Events within a single ``trace_id`` must appear in
   the order they were produced. Cross-trace order is wall-clock
   best-effort.
2. **Loss-bounded.** If producers outpace consumers, old events drop
   (ring buffer); we never block a producer on a slow consumer.
3. **Monotonic seq.** Every published event gets a monotonic ``seq``
   assigned by the buffer; consumers use this as their resume cursor
   (``Last-Event-ID`` in SSE).
4. **Redacted by default.** Payload fields are passed through
   :mod:`research_portal.redaction` before publication.
5. **No mutation after publish.** :class:`DashboardEvent` is frozen.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any

from research_portal.redaction import redact_payload

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DashboardEvent:
    """One event on a dashboard event pipeline.

    ``kind`` is a free-form string — domain packages (like atlas_portal)
    define their own kind enumerations and pass the string value here.

    ``seq`` is assigned by the buffer at publish time; producers leave
    it at 0. Ordering guarantees are on ``seq``, not on ``ts``.
    """

    kind: str
    trace_id: str
    ts: float  # wall-clock seconds since epoch
    payload: dict[str, Any] = field(default_factory=dict)
    seq: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class DashboardEventBuffer:
    """Bounded in-memory event buffer with monotonic ``seq`` assignment.

    Design decisions:

    - **Bounded:** size cap prevents unbounded memory growth. FIFO eviction.
    - **Seq per buffer instance:** starts at 1, increments on every
      publish. Consumers use seq as resume cursor.
    - **Thread-safety:** a single lock protects writes. Snapshot reads
      copy under the lock and iterate without holding it.
    - **Subscribers:** in-process callbacks fire outside the lock so
      a slow subscriber cannot block producers.
    - **Condition variable:** SSE handlers block efficiently via
      :meth:`wait_for_next`.
    """

    def __init__(self, max_size: int = 10_000) -> None:
        if max_size <= 0:
            raise ValueError("max_size must be > 0")
        self._max_size = max_size
        self._events: list[DashboardEvent] = []
        self._next_seq = 1
        self._lock = threading.Lock()
        self._subscribers: list[Callable[[DashboardEvent], None]] = []
        self._cond = threading.Condition(self._lock)

    # ---- write --------------------------------------------------------

    def publish(self, event: DashboardEvent) -> DashboardEvent:
        """Insert an event, assigning a monotonic seq.

        Returns the stored event (with seq set). Payload is redacted
        before storage.
        """
        redacted_payload = redact_payload(event.payload)
        with self._cond:
            seq = self._next_seq
            self._next_seq += 1
            stored = DashboardEvent(
                kind=event.kind,
                trace_id=event.trace_id,
                ts=event.ts or time.time(),
                payload=redacted_payload,
                seq=seq,
            )
            self._events.append(stored)
            if len(self._events) > self._max_size:
                self._events = self._events[-self._max_size :]
            self._cond.notify_all()
        for cb in list(self._subscribers):
            try:
                cb(stored)
            except Exception:  # pragma: no cover - defensive
                logger.exception("dashboard event subscriber raised")
        return stored

    # ---- read ---------------------------------------------------------

    def since(self, last_seq: int = 0) -> list[DashboardEvent]:
        """Return all buffered events with ``seq > last_seq``."""
        with self._lock:
            return [e for e in self._events if e.seq > last_seq]

    def wait_for_next(self, last_seq: int, timeout: float = 15.0) -> list[DashboardEvent]:
        """Block until new events arrive, or timeout."""
        deadline = time.monotonic() + timeout
        with self._cond:
            while True:
                current = [e for e in self._events if e.seq > last_seq]
                if current:
                    return current
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return []
                self._cond.wait(timeout=remaining)

    def latest_seq(self) -> int:
        with self._lock:
            return self._next_seq - 1

    def snapshot(self) -> list[DashboardEvent]:
        with self._lock:
            return list(self._events)

    # ---- subscriptions ------------------------------------------------

    def subscribe(self, callback: Callable[[DashboardEvent], None]) -> Callable[[], None]:
        """Register an in-process subscriber. Returns an unsubscribe fn."""
        self._subscribers.append(callback)

        def unsubscribe() -> None:
            try:
                self._subscribers.remove(callback)
            except ValueError:
                pass

        return unsubscribe

    # ---- maintenance --------------------------------------------------

    def reset(self) -> None:
        """Drop all events and reset seq. Tests only."""
        with self._cond:
            self._events = []
            self._next_seq = 1
            self._cond.notify_all()


# ---------------------------------------------------------------------------
# Process-wide default
# ---------------------------------------------------------------------------


_default_buffer: DashboardEventBuffer | None = None
_default_lock = threading.Lock()


def get_default_buffer() -> DashboardEventBuffer:
    """Return the process-wide default buffer, creating it lazily."""
    global _default_buffer
    with _default_lock:
        if _default_buffer is None:
            _default_buffer = DashboardEventBuffer()
        return _default_buffer


def set_default_buffer(buffer: DashboardEventBuffer) -> None:
    """Override the default buffer (for tests or advanced config)."""
    global _default_buffer
    with _default_lock:
        _default_buffer = buffer


__all__ = [
    "DashboardEvent",
    "DashboardEventBuffer",
    "get_default_buffer",
    "set_default_buffer",
]
