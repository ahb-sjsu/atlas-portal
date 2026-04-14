"""Council deliberation recorder and replay store.

Subscribes to the trinity event buffer and assembles completed
deliberations (bookended by ``council_start`` → ``council_done``) into
:class:`Deliberation` snapshots that can be replayed on the Council
Replay UI.

Design
------

The generic event buffer is a time-ordered ring of individual events;
to render a swim-lane timeline for one deliberation we need those
events grouped by ``trace_id`` with their per-member relative times
computed from the council_start anchor.

The recorder listens for ``council_done``; at that moment it walks the
buffer for every event matching the trace_id, assembles a
:class:`Deliberation`, and stores it in a bounded FIFO cache keyed by
trace_id (most recent first). The cache is bounded so memory stays
predictable regardless of traffic volume.

Thread-safety: the recorder takes the underlying buffer's snapshot
() which is already thread-safe; its own store is guarded by a lock
for list/get access from request handlers.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

from research_portal.events import DashboardEvent, DashboardEventBuffer

# Event kinds we care about when assembling a deliberation.
_COUNCIL_KINDS = frozenset(
    {
        "request_start",
        "superego_start",
        "superego_end",
        "id_start",
        "id_end",
        "council_start",
        "council_member",
        "council_done",
        "synthesis",
    }
)


@dataclass
class DeliberationMember:
    """One council member's contribution within a single deliberation."""

    member: str
    outcome: str  # approve / challenge / abstain
    latency_ms: float = 0.0
    score: float = 5.0
    # t_offset_ms: ms between the council_start anchor and this event.
    # Computed at assembly time; renderers use it for the swim-lane x-axis.
    t_offset_ms: float = 0.0


@dataclass
class Deliberation:
    """A council deliberation, bookended by start/done.

    This is the replay-ready snapshot. Frozen at assembly time;
    downstream renderers treat instances as read-only.
    """

    trace_id: str
    started_ts: float  # wall-clock seconds, first event in the trace
    completed_ts: float  # wall-clock seconds, council_done
    consensus: bool
    degraded: bool
    ethical_veto: bool
    approval_count: int
    challenge_count: int
    abstain_count: int
    total_latency_ms: float
    members: list[DeliberationMember] = field(default_factory=list)
    # Timeline events keyed by kind → t_offset_ms. Captures phase boundaries
    # (superego_start, id_end, etc.) so the renderer can draw non-member lanes.
    phase_markers: dict[str, float] = field(default_factory=dict)
    query_preview: str = ""

    def summary_dict(self) -> dict[str, Any]:
        """Compact form for the recent-deliberations list."""
        return {
            "trace_id": self.trace_id,
            "started_ts": self.started_ts,
            "completed_ts": self.completed_ts,
            "total_latency_ms": self.total_latency_ms,
            "consensus": self.consensus,
            "degraded": self.degraded,
            "ethical_veto": self.ethical_veto,
            "approval_count": self.approval_count,
            "challenge_count": self.challenge_count,
            "abstain_count": self.abstain_count,
            "member_count": len(self.members),
            "query_preview": self.query_preview,
        }

    def full_dict(self) -> dict[str, Any]:
        """Full replay payload — what the UI consumes to render the swim-lane."""
        d = self.summary_dict()
        d["members"] = [
            {
                "member": m.member,
                "outcome": m.outcome,
                "latency_ms": m.latency_ms,
                "score": m.score,
                "t_offset_ms": m.t_offset_ms,
            }
            for m in self.members
        ]
        d["phase_markers"] = dict(self.phase_markers)
        return d


class DeliberationRecorder:
    """Listens to a :class:`DashboardEventBuffer`, stores completed deliberations.

    Call :meth:`attach` once at startup to subscribe; :meth:`detach` for
    shutdown/tests. :meth:`recent` returns the most-recent N as
    summary dicts; :meth:`get` returns a single deliberation by
    trace_id or ``None``.
    """

    def __init__(
        self,
        buffer: DashboardEventBuffer,
        *,
        max_stored: int = 1000,
    ) -> None:
        if max_stored <= 0:
            raise ValueError("max_stored must be > 0")
        self._buffer = buffer
        self._max_stored = max_stored
        # OrderedDict keeps insertion order for predictable "most recent" queries.
        self._store: OrderedDict[str, Deliberation] = OrderedDict()
        self._lock = threading.Lock()
        self._unsubscribe: Any | None = None

    # ---- lifecycle ----

    def attach(self) -> None:
        """Start listening. Idempotent."""
        if self._unsubscribe is None:
            self._unsubscribe = self._buffer.subscribe(self._on_event)

    def detach(self) -> None:
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None

    # ---- public API ----

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return summaries of the ``limit`` most recent deliberations."""
        if limit <= 0:
            return []
        with self._lock:
            # OrderedDict is oldest-first; reverse + truncate.
            items = list(self._store.values())
        items.reverse()
        return [d.summary_dict() for d in items[:limit]]

    def get(self, trace_id: str) -> dict[str, Any] | None:
        with self._lock:
            d = self._store.get(trace_id)
        if d is None:
            return None
        return d.full_dict()

    def clear(self) -> None:
        """Drop all stored deliberations. Tests only."""
        with self._lock:
            self._store.clear()

    # ---- subscriber callback ----

    def _on_event(self, event: DashboardEvent) -> None:
        # Only `council_done` is the assembly trigger. Other events are
        # harvested from the buffer at assembly time so we don't have to
        # buffer partial deliberations in the recorder.
        if event.kind != "council_done":
            return
        trace_id = event.trace_id
        if not trace_id:
            return
        deliberation = self._assemble(trace_id)
        if deliberation is None:
            return
        with self._lock:
            # Overwrite if duplicate trace_id (shouldn't happen in normal
            # use, but defensive).
            if trace_id in self._store:
                self._store.pop(trace_id)
            self._store[trace_id] = deliberation
            # Evict oldest until we're under the cap.
            while len(self._store) > self._max_stored:
                self._store.popitem(last=False)

    # ---- assembly ----

    def _assemble(self, trace_id: str) -> Deliberation | None:
        events = [
            e
            for e in self._buffer.snapshot()
            if e.trace_id == trace_id and e.kind in _COUNCIL_KINDS
        ]
        if not events:
            return None

        # Must have both start + done to form a useful deliberation.
        start_event = next((e for e in events if e.kind == "council_start"), None)
        done_event = next((e for e in events if e.kind == "council_done"), None)
        if start_event is None or done_event is None:
            return None

        anchor_ts = start_event.ts
        completed_ts = done_event.ts

        members: list[DeliberationMember] = []
        phase_markers: dict[str, float] = {}
        query_preview = ""
        started_ts = events[0].ts  # earliest event (could be request_start)

        for e in events:
            t_offset_ms = max(0.0, (e.ts - anchor_ts) * 1000.0)
            p = e.payload or {}
            if e.kind == "council_member":
                members.append(
                    DeliberationMember(
                        member=str(p.get("member", "")),
                        outcome=str(p.get("outcome", "abstain")),
                        latency_ms=float(p.get("latency_ms", 0.0)),
                        score=float(p.get("score", 5.0)),
                        t_offset_ms=t_offset_ms,
                    )
                )
            else:
                # Use the latest timestamp we see for each phase kind
                # (covers the case where duplicate events slip in).
                phase_markers[e.kind] = t_offset_ms
            if e.kind == "request_start":
                qp = p.get("query_preview")
                if isinstance(qp, str):
                    query_preview = qp
            if e.ts < started_ts:
                started_ts = e.ts

        done_payload = done_event.payload or {}
        total_latency_ms = float(
            done_payload.get("latency_ms", (completed_ts - anchor_ts) * 1000.0)
        )

        return Deliberation(
            trace_id=trace_id,
            started_ts=started_ts,
            completed_ts=completed_ts,
            consensus=bool(done_payload.get("consensus", False)),
            degraded=bool(done_payload.get("degraded", False)),
            ethical_veto=bool(done_payload.get("ethical_veto", False)),
            approval_count=int(done_payload.get("approval_count", 0)),
            challenge_count=int(done_payload.get("challenge_count", 0)),
            abstain_count=int(done_payload.get("abstain_count", 0)),
            total_latency_ms=total_latency_ms,
            members=members,
            phase_markers=phase_markers,
            query_preview=query_preview,
        )


# ---------------------------------------------------------------------------
# Flask integration
# ---------------------------------------------------------------------------


def register_routes(app: Any, recorder: DeliberationRecorder) -> None:
    """Register /api/council/* routes on a Flask app."""
    from flask import jsonify, request

    @app.route("/api/council/deliberations")
    def list_deliberations() -> Any:  # type: ignore[misc]
        try:
            limit = int(request.args.get("limit", "50"))
        except (TypeError, ValueError):
            limit = 50
        return jsonify({"items": recorder.recent(limit=max(1, min(limit, 500)))})

    @app.route("/api/council/deliberations/<trace_id>")
    def get_deliberation(trace_id: str) -> Any:  # type: ignore[misc]
        d = recorder.get(trace_id)
        if d is None:
            return jsonify({"error": "not found", "trace_id": trace_id}), 404
        return jsonify(d)


__all__ = [
    "Deliberation",
    "DeliberationMember",
    "DeliberationRecorder",
    "register_routes",
]
