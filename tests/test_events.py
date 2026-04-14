"""Tests for research_portal.events and atlas_portal.events{_producer}."""

from __future__ import annotations

import threading
import time

import pytest

from atlas_portal import events_producer as producer
from atlas_portal.events import EventKind
from research_portal.events import (
    DashboardEvent,
    DashboardEventBuffer,
    get_default_buffer,
    set_default_buffer,
)


class TestDashboardEvent:
    def test_frozen(self):
        import dataclasses

        e = DashboardEvent(kind="x", trace_id="t", ts=1.0)
        with pytest.raises(dataclasses.FrozenInstanceError):
            e.kind = "y"  # type: ignore[misc]

    def test_to_dict(self):
        e = DashboardEvent(kind="x", trace_id="t", ts=1.0, payload={"a": 1}, seq=7)
        d = e.to_dict()
        assert d["kind"] == "x"
        assert d["trace_id"] == "t"
        assert d["seq"] == 7


class TestBuffer:
    def _fresh(self, **kwargs) -> DashboardEventBuffer:
        buf = DashboardEventBuffer(**kwargs)
        return buf

    def test_reject_bad_size(self):
        with pytest.raises(ValueError):
            DashboardEventBuffer(max_size=0)

    def test_seq_monotonic(self):
        buf = self._fresh()
        a = buf.publish(DashboardEvent(kind="x", trace_id="t", ts=1.0))
        b = buf.publish(DashboardEvent(kind="x", trace_id="t", ts=2.0))
        c = buf.publish(DashboardEvent(kind="x", trace_id="t", ts=3.0))
        assert a.seq < b.seq < c.seq

    def test_fifo_eviction(self):
        buf = self._fresh(max_size=3)
        for i in range(5):
            buf.publish(DashboardEvent(kind="x", trace_id=str(i), ts=i))
        kept = buf.snapshot()
        assert len(kept) == 3
        # Should have kept the last 3 (trace_ids 2, 3, 4)
        assert [e.trace_id for e in kept] == ["2", "3", "4"]

    def test_since_cursor(self):
        buf = self._fresh()
        published = [
            buf.publish(DashboardEvent(kind="x", trace_id=str(i), ts=i)) for i in range(5)
        ]
        third_seq = published[2].seq
        result = buf.since(third_seq)
        assert len(result) == 2
        assert all(e.seq > third_seq for e in result)

    def test_redaction_happens_on_publish(self):
        buf = self._fresh()
        buf.publish(DashboardEvent(kind="x", trace_id="t", ts=1.0, payload={"api_key": "shh"}))
        stored = buf.snapshot()[0]
        assert stored.payload["api_key"] == "<redacted>"

    def test_wait_for_next_returns_new_events(self):
        buf = self._fresh()
        seq_before = buf.latest_seq()

        def producer_thread():
            time.sleep(0.05)
            buf.publish(DashboardEvent(kind="x", trace_id="t", ts=0))

        t = threading.Thread(target=producer_thread)
        t.start()
        t0 = time.monotonic()
        events = buf.wait_for_next(seq_before, timeout=1.0)
        t.join()
        assert len(events) == 1
        # Should have woken quickly (not waited the full timeout)
        assert (time.monotonic() - t0) < 0.5

    def test_wait_for_next_timeout(self):
        buf = self._fresh()
        seq_before = buf.latest_seq()
        t0 = time.monotonic()
        events = buf.wait_for_next(seq_before, timeout=0.1)
        elapsed = time.monotonic() - t0
        assert events == []
        assert 0.08 < elapsed < 0.3  # honored the timeout

    def test_subscriber_fires(self):
        buf = self._fresh()
        received = []

        def cb(e):
            received.append(e)

        unsub = buf.subscribe(cb)
        buf.publish(DashboardEvent(kind="x", trace_id="t", ts=0))
        assert len(received) == 1
        unsub()
        buf.publish(DashboardEvent(kind="x", trace_id="t", ts=0))
        assert len(received) == 1  # unsubscribed

    def test_subscriber_exception_does_not_break_buffer(self):
        buf = self._fresh()

        def bad_cb(_e):
            raise RuntimeError("oops")

        buf.subscribe(bad_cb)
        # Should not raise
        buf.publish(DashboardEvent(kind="x", trace_id="t", ts=0))
        assert len(buf.snapshot()) == 1

    def test_per_trace_ordering_under_contention(self):
        buf = self._fresh()
        n_producers = 8
        n_events = 50

        def publisher(trace_id: str):
            for i in range(n_events):
                buf.publish(
                    DashboardEvent(
                        kind="x",
                        trace_id=trace_id,
                        ts=time.time(),
                        payload={"i": i},
                    )
                )

        threads = [threading.Thread(target=publisher, args=(f"t{j}",)) for j in range(n_producers)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Per trace_id, the 'i' values must appear in order.
        per_trace: dict[str, list[int]] = {}
        for e in buf.snapshot():
            per_trace.setdefault(e.trace_id, []).append(e.payload["i"])
        for tid, ivals in per_trace.items():
            assert ivals == sorted(ivals), f"trace {tid} out of order: {ivals}"

    def test_reset_clears_state(self):
        buf = self._fresh()
        buf.publish(DashboardEvent(kind="x", trace_id="t", ts=0))
        buf.reset()
        assert buf.snapshot() == []
        assert buf.latest_seq() == 0


class TestDefaultBuffer:
    def test_singleton(self):
        a = get_default_buffer()
        b = get_default_buffer()
        assert a is b

    def test_override(self):
        original = get_default_buffer()
        try:
            custom = DashboardEventBuffer(max_size=5)
            set_default_buffer(custom)
            assert get_default_buffer() is custom
        finally:
            set_default_buffer(original)


class TestProducers:
    def _buf(self) -> DashboardEventBuffer:
        return DashboardEventBuffer()

    def test_request_start(self):
        buf = self._buf()
        e = producer.request_start("t1", query_preview="hello", buffer=buf)
        assert e.kind == EventKind.REQUEST_START.value
        assert e.trace_id == "t1"
        assert e.payload["query_preview"] == "hello"

    def test_council_member(self):
        buf = self._buf()
        e = producer.council_member(
            "t1", member="judge", outcome="approve", latency_ms=42.0, buffer=buf
        )
        assert e.kind == EventKind.COUNCIL_MEMBER.value
        assert e.payload == {
            "member": "judge",
            "outcome": "approve",
            "latency_ms": 42.0,
            "score": 5.0,
        }

    def test_council_done(self):
        buf = self._buf()
        e = producer.council_done(
            "t1",
            consensus=True,
            degraded=False,
            ethical_veto=False,
            approval_count=6,
            challenge_count=1,
            abstain_count=0,
            latency_ms=1500.0,
            buffer=buf,
        )
        assert e.payload["consensus"] is True
        assert e.payload["approval_count"] == 6

    def test_producer_redacts_query_preview(self):
        buf = self._buf()
        e = producer.request_start(
            "t1",
            query_preview="email: alice@example.com",
            buffer=buf,
        )
        assert "<email>" in e.payload["query_preview"]

    def test_heartbeat(self):
        buf = self._buf()
        e = producer.heartbeat(buffer=buf)
        assert e.kind == EventKind.HEARTBEAT.value
        assert e.trace_id == "system"
