"""Tests for atlas_portal.burst — NRP burst monitoring module."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from atlas_portal.burst import (
    get_burst_jobs,
    get_burst_pods,
    get_burst_summary,
    get_jetstream_status,
    get_leaf_status,
    get_nats_server_info,
)

# ── Fixtures ──────────────────────────────────────────────────────

LEAFZ_RESPONSE = json.dumps(
    {
        "server_id": "NTEST",
        "leafnodes": 1,
        "leafs": [
            {
                "name": "NLEAF",
                "account": "$G",
                "ip": "192.41.231.239",
                "port": 41668,
                "rtt": "67.2ms",
                "in_msgs": 100,
                "out_msgs": 200,
                "in_bytes": 4096,
                "out_bytes": 8192,
                "subscriptions": 5,
                "compression": "s2_better",
            }
        ],
    }
).encode()

JSZ_RESPONSE = json.dumps(
    {
        "memory": 0,
        "storage": 59885993,
        "streams": 1,
        "consumers": 0,
        "messages": 103515,
        "accounts": 1,
    }
).encode()

VARZ_RESPONSE = json.dumps(
    {
        "version": "2.10.24",
        "uptime": "7d2h",
        "connections": 3,
        "total_connections": 42,
        "in_msgs": 50000,
        "out_msgs": 48000,
        "in_bytes": 1048576,
        "out_bytes": 2097152,
        "subscriptions": 15,
        "leafnodes": 1,
    }
).encode()

JOBS_JSON = json.dumps(
    {
        "apiVersion": "batch/v1",
        "items": [
            {
                "metadata": {
                    "name": "burst-sweep-001",
                    "creationTimestamp": "2026-04-16T19:52:53Z",
                    "labels": {"app.kubernetes.io/managed-by": "nats-bursting"},
                },
                "spec": {"completions": 1},
                "status": {"active": 1, "succeeded": 0, "failed": 0},
            },
            {
                "metadata": {
                    "name": "burst-train-002",
                    "creationTimestamp": "2026-04-16T18:00:00Z",
                    "labels": {},
                },
                "spec": {"completions": 1},
                "status": {"active": 0, "succeeded": 1, "failed": 0},
            },
        ],
    }
)

PODS_JSON = json.dumps(
    {
        "items": [
            {
                "metadata": {
                    "name": "atlas-nats-leaf-abc",
                    "creationTimestamp": "2026-04-16T19:24:28Z",
                },
                "spec": {
                    "nodeName": "k8s-haosu-15.sdsc.optiputer.net",
                    "containers": [
                        {
                            "name": "nats",
                            "resources": {
                                "requests": {"cpu": "100m", "memory": "128Mi"},
                                "limits": {"cpu": "500m", "memory": "512Mi"},
                            },
                        }
                    ],
                },
                "status": {"phase": "Running"},
            }
        ]
    }
)


# ── NATS tests ────────────────────────────────────────────────────


def _mock_urlopen(response_bytes):
    """Create a mock urllib.request.urlopen that returns response_bytes."""
    mock_resp = MagicMock()
    mock_resp.read.return_value = response_bytes
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


class TestGetLeafStatus:
    def test_parses_leafz(self):
        with patch(
            "atlas_portal.burst.urllib.request.urlopen", return_value=_mock_urlopen(LEAFZ_RESPONSE)
        ):
            result = get_leaf_status()
        assert result["connected"] == 1
        assert len(result["leafs"]) == 1
        leaf = result["leafs"][0]
        assert leaf["rtt"] == "67.2ms"
        assert leaf["subscriptions"] == 5
        assert leaf["compression"] == "s2_better"
        assert leaf["ip"] == "192.41.231.239"

    def test_returns_empty_on_failure(self):
        with patch("atlas_portal.burst.urllib.request.urlopen", side_effect=Exception("refused")):
            result = get_leaf_status()
        assert result["connected"] == 0
        assert result["leafs"] == []


class TestGetJetStreamStatus:
    def test_parses_jsz(self):
        with patch(
            "atlas_portal.burst.urllib.request.urlopen", return_value=_mock_urlopen(JSZ_RESPONSE)
        ):
            result = get_jetstream_status()
        assert result["streams"] == 1
        assert result["messages"] == 103515
        assert result["storage_bytes"] == 59885993

    def test_returns_empty_on_failure(self):
        with patch("atlas_portal.burst.urllib.request.urlopen", side_effect=Exception):
            assert get_jetstream_status() == {}


class TestGetNatsServerInfo:
    def test_parses_varz(self):
        with patch(
            "atlas_portal.burst.urllib.request.urlopen", return_value=_mock_urlopen(VARZ_RESPONSE)
        ):
            result = get_nats_server_info()
        assert result["version"] == "2.10.24"
        assert result["uptime"] == "7d2h"
        assert result["connections"] == 3
        assert result["leafnodes"] == 1

    def test_returns_empty_on_failure(self):
        with patch("atlas_portal.burst.urllib.request.urlopen", side_effect=Exception):
            assert get_nats_server_info() == {}


# ── Kubernetes tests ──────────────────────────────────────────────


class TestGetBurstJobs:
    def test_parses_kubectl_jobs(self):
        with patch("atlas_portal.burst.shutil.which", return_value="/usr/bin/kubectl"):
            with patch("atlas_portal.burst.subprocess.check_output", return_value=JOBS_JSON):
                jobs = get_burst_jobs()
        assert len(jobs) == 2
        running = [j for j in jobs if j["state"] == "Running"]
        assert len(running) == 1
        assert running[0]["name"] == "burst-sweep-001"
        succeeded = [j for j in jobs if j["state"] == "Succeeded"]
        assert len(succeeded) == 1
        assert succeeded[0]["name"] == "burst-train-002"

    def test_returns_empty_when_kubectl_missing(self):
        with patch("atlas_portal.burst.shutil.which", return_value=None):
            assert get_burst_jobs() == []

    def test_returns_empty_on_kubectl_failure(self):
        with patch("atlas_portal.burst.shutil.which", return_value="/usr/bin/kubectl"):
            with patch(
                "atlas_portal.burst.subprocess.check_output",
                side_effect=subprocess.TimeoutExpired("kubectl", 8),
            ):
                jobs = get_burst_jobs()
        assert jobs == []


class TestGetBurstPods:
    def test_parses_kubectl_pods(self):
        with patch("atlas_portal.burst.shutil.which", return_value="/usr/bin/kubectl"):
            with patch("atlas_portal.burst.subprocess.check_output", return_value=PODS_JSON):
                pods = get_burst_pods()
        assert len(pods) == 1
        assert pods[0]["name"] == "atlas-nats-leaf-abc"
        assert pods[0]["phase"] == "Running"
        assert pods[0]["node"] == "k8s-haosu-15.sdsc.optiputer.net"
        assert pods[0]["resources"]["cpu"] == "100m"

    def test_returns_empty_when_kubectl_missing(self):
        with patch("atlas_portal.burst.shutil.which", return_value=None):
            assert get_burst_pods() == []


# ── Combined summary ──────────────────────────────────────────────


class TestGetBurstSummary:
    def test_has_all_top_level_keys(self):
        with patch("atlas_portal.burst.urllib.request.urlopen", side_effect=Exception):
            with patch("atlas_portal.burst.shutil.which", return_value=None):
                summary = get_burst_summary()
        assert "nats" in summary
        assert "kubernetes" in summary
        assert "timestamp" in summary
        assert isinstance(summary["timestamp"], float)
        assert "server" in summary["nats"]
        assert "leafs" in summary["nats"]
        assert "jetstream" in summary["nats"]
        assert "jobs" in summary["kubernetes"]
        assert "pods" in summary["kubernetes"]
        assert summary["kubernetes"]["namespace"] == "ssu-atlas-ai"


# ── App integration ───────────────────────────────────────────────


import subprocess  # noqa: E402 — needed for TimeoutExpired reference

from atlas_portal.app import build_app  # noqa: E402


@pytest.fixture
def client():
    app = build_app(no_auth=True, start_heartbeat=False)
    return app.test_client()


class TestBurstPage:
    def test_burst_renders(self, client):
        rv = client.get("/burst")
        assert rv.status_code == 200
        assert b"Burst" in rv.data
        assert b"ssu-atlas-ai" in rv.data

    def test_burst_api_returns_structure(self, client):
        with patch("atlas_portal.burst.urllib.request.urlopen", side_effect=Exception):
            with patch("atlas_portal.burst.shutil.which", return_value=None):
                rv = client.get("/api/burst")
        assert rv.status_code == 200
        data = rv.get_json()
        assert "nats" in data
        assert "kubernetes" in data
        assert "timestamp" in data
