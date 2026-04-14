"""Tests for research_portal.sse."""

from __future__ import annotations

import json
import threading
import time

from research_portal.events import DashboardEvent, DashboardEventBuffer
from research_portal.sse import (
    format_keepalive,
    format_sse,
    parse_last_event_id,
    sse_stream,
)


class TestFormatSSE:
    def test_frame_shape(self):
        e = DashboardEvent(
            kind="council_member",
            trace_id="abc",
            ts=1.5,
            payload={"member": "judge"},
            seq=7,
        )
        frame = format_sse(e)
        lines = frame.rstrip("\n").split("\n")
        assert lines[0] == "id: 7"
        assert lines[1] == "event: council_member"
        assert lines[2].startswith("data: ")
        assert frame.endswith("\n\n")

    def test_data_is_parseable_json(self):
        e = DashboardEvent(kind="x", trace_id="abc", ts=1.5, payload={"k": "v"}, seq=1)
        frame = format_sse(e)
        data_line = [line for line in frame.split("\n") if line.startswith("data:")][0]
        json.loads(data_line[6:])  # just "data: " prefix


class TestFormatKeepalive:
    def test_is_comment(self):
        line = format_keepalive()
        assert line.startswith(": keepalive")
        assert line.endswith("\n\n")


class TestParseLastEventId:
    def test_missing_header(self):
        assert parse_last_event_id(None) == 0
        assert parse_last_event_id("") == 0

    def test_valid_int(self):
        assert parse_last_event_id("42") == 42

    def test_invalid_returns_zero(self):
        assert parse_last_event_id("not-a-number") == 0

    def test_negative_clamped_to_zero(self):
        assert parse_last_event_id("-5") == 0


class TestSSEStream:
    def test_sends_backlog_then_keepalives(self):
        buf = DashboardEventBuffer()
        buf.publish(DashboardEvent(kind="x", trace_id="t", ts=0))
        buf.publish(DashboardEvent(kind="y", trace_id="t", ts=0))
        gen = sse_stream(buffer=buf, last_event_id=0, wait_window=0.05, stop_after=0.15)
        chunks = list(gen)
        # We should see at least the 2 backlog events
        event_frames = [c for c in chunks if c.startswith("id:")]
        assert len(event_frames) >= 2

    def test_resume_cursor(self):
        buf = DashboardEventBuffer()
        e1 = buf.publish(DashboardEvent(kind="x", trace_id="t", ts=0))
        e2 = buf.publish(DashboardEvent(kind="y", trace_id="t", ts=0))
        gen = sse_stream(buffer=buf, last_event_id=e1.seq, wait_window=0.05, stop_after=0.15)
        chunks = list(gen)
        event_frames = [c for c in chunks if c.startswith("id:")]
        # Should get e2 but not e1
        assert len(event_frames) == 1
        assert str(e2.seq) in event_frames[0]

    def test_streams_new_events_live(self):
        buf = DashboardEventBuffer()
        received: list[str] = []

        def consumer():
            gen = sse_stream(buffer=buf, last_event_id=0, wait_window=0.05, stop_after=0.4)
            for chunk in gen:
                if chunk.startswith("id:"):
                    received.append(chunk)

        t = threading.Thread(target=consumer)
        t.start()
        time.sleep(0.05)
        buf.publish(DashboardEvent(kind="x", trace_id="t", ts=0))
        buf.publish(DashboardEvent(kind="y", trace_id="t", ts=0))
        t.join()
        assert len(received) >= 2

    def test_stop_after_terminates(self):
        buf = DashboardEventBuffer()
        gen = sse_stream(buffer=buf, wait_window=0.05, stop_after=0.15)
        t0 = time.monotonic()
        list(gen)
        elapsed = time.monotonic() - t0
        assert elapsed < 0.5  # terminated within reasonable time
