"""Tests for the council replay recorder + API + UI."""

from __future__ import annotations

import time

import pytest

from atlas_portal.app import build_app
from atlas_portal.replay import (
    Deliberation,
    DeliberationRecorder,
)
from research_portal.events import DashboardEvent, DashboardEventBuffer


def _publish_deliberation(
    buffer: DashboardEventBuffer,
    trace_id: str,
    *,
    consensus: bool = True,
    veto: bool = False,
    degraded: bool = False,
    members: list[tuple[str, str]] | None = None,
    query_preview: str = "draft a memo",
) -> None:
    """Helper: emit a realistic sequence of events for one deliberation."""
    t0 = time.time()
    seq = [
        ("request_start", {"query_preview": query_preview}),
        ("superego_start", {}),
        ("id_start", {}),
        ("superego_end", {"latency_ms": 1100}),
        ("id_end", {"latency_ms": 1400}),
        ("council_start", {"member_count": 7}),
    ]
    for kind, payload in seq:
        buffer.publish(DashboardEvent(kind=kind, trace_id=trace_id, ts=t0, payload=payload))
        t0 += 0.05

    members = members or [
        ("judge", "approve"),
        ("historian", "approve"),
        ("futurist", "approve"),
        ("advocate", "challenge"),
        ("pragmatist", "approve"),
        ("ethicist", "approve"),
        ("synthesizer", "approve"),
    ]
    for m, outcome in members:
        buffer.publish(
            DashboardEvent(
                kind="council_member",
                trace_id=trace_id,
                ts=t0,
                payload={
                    "member": m,
                    "outcome": outcome,
                    "latency_ms": 180,
                    "score": 7.5,
                },
            )
        )
        t0 += 0.05

    approval = sum(1 for _, o in members if o == "approve")
    challenge = sum(1 for _, o in members if o == "challenge")
    abstain = sum(1 for _, o in members if o == "abstain")
    buffer.publish(
        DashboardEvent(
            kind="council_done",
            trace_id=trace_id,
            ts=t0,
            payload={
                "consensus": consensus,
                "degraded": degraded,
                "ethical_veto": veto,
                "approval_count": approval,
                "challenge_count": challenge,
                "abstain_count": abstain,
                "latency_ms": (t0 - (time.time() - 2)) * 1000,
            },
        )
    )


class TestRecorder:
    def test_captures_complete_deliberation(self):
        buf = DashboardEventBuffer()
        rec = DeliberationRecorder(buf)
        rec.attach()
        try:
            _publish_deliberation(buf, "t-1")
            assert len(rec.recent()) == 1
            d = rec.get("t-1")
            assert d is not None
            assert d["trace_id"] == "t-1"
            assert d["approval_count"] == 6
            assert d["challenge_count"] == 1
            assert d["abstain_count"] == 0
            assert d["consensus"] is True
            assert len(d["members"]) == 7
            assert d["query_preview"] == "draft a memo"
        finally:
            rec.detach()

    def test_ignores_partial_deliberations(self):
        buf = DashboardEventBuffer()
        rec = DeliberationRecorder(buf)
        rec.attach()
        try:
            # council_done without a matching start: recorder won't store it.
            buf.publish(
                DashboardEvent(
                    kind="council_done",
                    trace_id="stray",
                    ts=time.time(),
                    payload={"consensus": True},
                )
            )
            assert rec.get("stray") is None
            assert rec.recent() == []
        finally:
            rec.detach()

    def test_most_recent_first(self):
        buf = DashboardEventBuffer()
        rec = DeliberationRecorder(buf)
        rec.attach()
        try:
            _publish_deliberation(buf, "a")
            _publish_deliberation(buf, "b")
            _publish_deliberation(buf, "c")
            ids = [d["trace_id"] for d in rec.recent()]
            assert ids == ["c", "b", "a"]
        finally:
            rec.detach()

    def test_bounded_storage(self):
        buf = DashboardEventBuffer(max_size=1000)
        rec = DeliberationRecorder(buf, max_stored=3)
        rec.attach()
        try:
            for i in range(5):
                _publish_deliberation(buf, f"tr-{i}")
            ids = [d["trace_id"] for d in rec.recent()]
            # Only the 3 most recent remain
            assert ids == ["tr-4", "tr-3", "tr-2"]
        finally:
            rec.detach()

    def test_veto_encoded_in_summary(self):
        buf = DashboardEventBuffer()
        rec = DeliberationRecorder(buf)
        rec.attach()
        try:
            _publish_deliberation(
                buf,
                "risky",
                consensus=False,
                veto=True,
                members=[
                    ("judge", "challenge"),
                    ("advocate", "challenge"),
                    ("ethicist", "challenge"),
                    ("synthesizer", "challenge"),
                    ("historian", "challenge"),
                    ("futurist", "challenge"),
                    ("pragmatist", "approve"),
                ],
            )
            d = rec.get("risky")
            assert d["ethical_veto"] is True
            assert d["consensus"] is False
            assert d["challenge_count"] == 6
            assert d["approval_count"] == 1
        finally:
            rec.detach()

    def test_phase_markers_populated(self):
        buf = DashboardEventBuffer()
        rec = DeliberationRecorder(buf)
        rec.attach()
        try:
            _publish_deliberation(buf, "phased")
            d = rec.get("phased")
            # Phase markers include request_start, superego_*, id_*, council_start
            for kind in ("request_start", "superego_start", "council_start"):
                assert kind in d["phase_markers"]
        finally:
            rec.detach()

    def test_detach_stops_recording(self):
        buf = DashboardEventBuffer()
        rec = DeliberationRecorder(buf)
        rec.attach()
        _publish_deliberation(buf, "captured")
        rec.detach()
        _publish_deliberation(buf, "post-detach")
        ids = [d["trace_id"] for d in rec.recent()]
        assert "post-detach" not in ids
        assert "captured" in ids


class TestAPI:
    @pytest.fixture
    def app(self):
        return build_app(no_auth=True, start_heartbeat=False)

    @pytest.fixture
    def client(self, app):
        return app.test_client()

    @pytest.fixture
    def recorder(self, app):
        return app.extensions["atlas_replay_recorder"]

    def test_list_empty_initially(self, client, recorder):
        recorder.clear()
        rv = client.get("/api/council/deliberations")
        assert rv.status_code == 200
        assert rv.get_json() == {"items": []}

    def test_list_after_deliberation(self, client, recorder):
        recorder.clear()
        _publish_deliberation(recorder._buffer, "live")
        rv = client.get("/api/council/deliberations")
        data = rv.get_json()
        assert len(data["items"]) == 1
        assert data["items"][0]["trace_id"] == "live"

    def test_get_existing(self, client, recorder):
        recorder.clear()
        _publish_deliberation(recorder._buffer, "live-2")
        rv = client.get("/api/council/deliberations/live-2")
        assert rv.status_code == 200
        assert rv.get_json()["trace_id"] == "live-2"

    def test_get_missing_404(self, client, recorder):
        recorder.clear()
        rv = client.get("/api/council/deliberations/nonexistent")
        assert rv.status_code == 404

    def test_limit_query_param(self, client, recorder):
        recorder.clear()
        for i in range(10):
            _publish_deliberation(recorder._buffer, f"many-{i}")
        rv = client.get("/api/council/deliberations?limit=3")
        assert len(rv.get_json()["items"]) == 3


class TestCouncilUI:
    @pytest.fixture
    def client(self):
        return build_app(no_auth=True, start_heartbeat=False).test_client()

    def test_council_page_has_canvas(self, client):
        html = client.get("/council").data.decode("utf-8")
        assert "replay-list" in html
        assert "swim-svg" in html
        assert "replay-play" in html
        assert "replay-scrub" in html

    def test_replay_css_served(self, client):
        rv = client.get("/static/css/replay.css")
        assert rv.status_code == 200
        body = rv.data.decode("utf-8")
        assert ".swim-lane-bg" in body
        assert "prefers-reduced-motion" in body

    def test_replay_js_served(self, client):
        rv = client.get("/static/js/replay.js")
        assert rv.status_code == 200
        body = rv.data.decode("utf-8")
        for kw in ("MEMBERS", "drawLanes", "/api/council/deliberations"):
            assert kw in body


class TestArchive:
    @pytest.fixture
    def client(self):
        return build_app(no_auth=True, start_heartbeat=False).test_client()

    def test_archive_has_volume_grid(self, client):
        html = client.get("/archive").data.decode("utf-8")
        # All 11 volume references
        for v in (f"Vol {i}" for i in range(1, 12)):
            assert v in html
        # Patent numbers present
        assert "63/941,563" in html
        assert "63/945,667" in html


class TestDeliberationDataclass:
    def test_summary_keys(self):
        d = Deliberation(
            trace_id="x",
            started_ts=1.0,
            completed_ts=2.0,
            consensus=True,
            degraded=False,
            ethical_veto=False,
            approval_count=5,
            challenge_count=1,
            abstain_count=1,
            total_latency_ms=1500.0,
        )
        s = d.summary_dict()
        for k in (
            "trace_id",
            "started_ts",
            "completed_ts",
            "consensus",
            "degraded",
            "ethical_veto",
            "approval_count",
            "challenge_count",
            "abstain_count",
            "member_count",
            "query_preview",
            "total_latency_ms",
        ):
            assert k in s
        # Full dict adds members + phase markers
        f = d.full_dict()
        assert "members" in f
        assert "phase_markers" in f
