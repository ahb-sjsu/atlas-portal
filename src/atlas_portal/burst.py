"""NRP burst monitoring — NATS leaf bridge + Kubernetes Job status.

Queries:
- NATS monitoring endpoint (``http://localhost:8222/leafz``, ``/jsz``,
  ``/varz``) for leaf-node health and JetStream stats.
- ``kubectl`` for active/completed Jobs and Pods in the NRP namespace.

All functions degrade gracefully (return empty dicts/lists) if the
backing service is unavailable — the portal never crashes because NATS
is down or kubectl isn't on PATH.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import time
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

# Default NATS monitoring base URL — override via NATS_MONITOR_URL env var.
NATS_MONITOR_URL = "http://localhost:8222"

# Kubernetes namespace for NRP burst jobs.
BURST_NAMESPACE = "ssu-atlas-ai"

# How long kubectl / HTTP calls are allowed before we give up.
_TIMEOUT = 8


# ── NATS helpers ──────────────────────────────────────────────────


def _nats_get(path: str) -> dict:
    """GET a NATS monitoring endpoint, return parsed JSON or ``{}``."""
    try:
        req = urllib.request.Request(
            f"{NATS_MONITOR_URL}{path}",
            headers={"Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            return json.loads(resp.read())
    except Exception:
        return {}


def get_leaf_status() -> dict:
    """NATS leaf-node connections (``/leafz``).

    Returns a dict like::

        {
            "connected": 1,
            "leafs": [
                {"name": "...", "account": "$G", "rtt": "67ms",
                 "in_msgs": 123, "out_msgs": 456, "ip": "192.41.231.239",
                 "subscriptions": 5}
            ]
        }
    """
    data = _nats_get("/leafz")
    if not data:
        return {"connected": 0, "leafs": []}
    leafs = []
    for leaf in data.get("leafs", []):
        leafs.append(
            {
                "name": leaf.get("name", ""),
                "account": leaf.get("account", ""),
                "rtt": leaf.get("rtt", ""),
                "ip": leaf.get("ip", ""),
                "port": leaf.get("port", 0),
                "in_msgs": leaf.get("in_msgs", 0),
                "out_msgs": leaf.get("out_msgs", 0),
                "in_bytes": leaf.get("in_bytes", 0),
                "out_bytes": leaf.get("out_bytes", 0),
                "subscriptions": leaf.get("subscriptions", 0),
                "compression": leaf.get("compression", "off"),
            }
        )
    return {"connected": data.get("leafnodes", 0), "leafs": leafs}


def get_jetstream_status() -> dict:
    """JetStream overview (``/jsz``).

    Returns memory/storage usage, stream/consumer counts, message totals.
    """
    data = _nats_get("/jsz")
    if not data:
        return {}
    return {
        "memory_bytes": data.get("memory", 0),
        "storage_bytes": data.get("storage", 0),
        "streams": data.get("streams", 0),
        "consumers": data.get("consumers", 0),
        "messages": data.get("messages", 0),
        "accounts": data.get("accounts", 0),
    }


def get_nats_server_info() -> dict:
    """Top-level NATS server stats (``/varz``).

    Returns connection counts, in/out message rates, uptime.
    """
    data = _nats_get("/varz")
    if not data:
        return {}
    return {
        "version": data.get("version", ""),
        "uptime": data.get("uptime", ""),
        "connections": data.get("connections", 0),
        "total_connections": data.get("total_connections", 0),
        "in_msgs": data.get("in_msgs", 0),
        "out_msgs": data.get("out_msgs", 0),
        "in_bytes": data.get("in_bytes", 0),
        "out_bytes": data.get("out_bytes", 0),
        "subscriptions": data.get("subscriptions", 0),
        "leafnodes": data.get("leafnodes", 0),
    }


# ── Kubernetes helpers ────────────────────────────────────────────


def _kubectl_json(*args: str) -> dict:
    """Run ``kubectl`` with ``-o json`` and return parsed output, or ``{}``."""
    if not shutil.which("kubectl"):
        return {}
    try:
        cmd = ["kubectl", "-n", BURST_NAMESPACE, *args, "-o", "json"]
        out = subprocess.check_output(cmd, text=True, timeout=_TIMEOUT, stderr=subprocess.DEVNULL)
        return json.loads(out)
    except Exception:
        return {}


def get_burst_jobs() -> list[dict]:
    """Active and completed K8s Jobs in the burst namespace.

    Each entry contains: name, status (Active/Succeeded/Failed),
    completions, age, and the creation timestamp.
    """
    data = _kubectl_json("get", "jobs")
    items = data.get("items", [])
    jobs: list[dict] = []
    for item in items:
        meta = item.get("metadata", {})
        status = item.get("status", {})
        spec = item.get("spec", {})

        # Determine state
        active = status.get("active", 0)
        succeeded = status.get("succeeded", 0)
        failed = status.get("failed", 0)
        if active > 0:
            state = "Running"
        elif succeeded >= (spec.get("completions", 1) or 1):
            state = "Succeeded"
        elif failed > 0:
            state = "Failed"
        else:
            state = "Pending"

        created = meta.get("creationTimestamp", "")
        managed_by = meta.get("labels", {}).get("app.kubernetes.io/managed-by", "")

        jobs.append(
            {
                "name": meta.get("name", ""),
                "state": state,
                "active": active,
                "succeeded": succeeded,
                "failed": failed,
                "created": created,
                "managed_by": managed_by,
            }
        )
    # Sort: running first, then by creation time (newest first)
    jobs.sort(key=lambda j: (j["state"] != "Running", j["created"]), reverse=False)
    return jobs


def get_burst_pods() -> list[dict]:
    """Pods in the burst namespace (includes leaf pod + job pods)."""
    data = _kubectl_json("get", "pods")
    items = data.get("items", [])
    pods: list[dict] = []
    for item in items:
        meta = item.get("metadata", {})
        status = item.get("status", {})
        spec = item.get("spec", {})

        # Container resource summary
        containers = spec.get("containers", [])
        resources: dict[str, Any] = {}
        for c in containers:
            res = c.get("resources", {})
            req = res.get("requests", {})
            resources["cpu"] = req.get("cpu", "")
            resources["memory"] = req.get("memory", "")
            gpu_limit = res.get("limits", {}).get("nvidia.com/gpu", "")
            if gpu_limit:
                resources["gpu"] = gpu_limit

        phase = status.get("phase", "Unknown")
        node = spec.get("nodeName", "")

        pods.append(
            {
                "name": meta.get("name", ""),
                "phase": phase,
                "node": node,
                "resources": resources,
                "created": meta.get("creationTimestamp", ""),
            }
        )
    return pods


# ── Combined summary ──────────────────────────────────────────────


def get_burst_summary() -> dict:
    """Full burst monitoring snapshot for the ``/api/burst`` endpoint."""
    return {
        "nats": {
            "server": get_nats_server_info(),
            "leafs": get_leaf_status(),
            "jetstream": get_jetstream_status(),
        },
        "kubernetes": {
            "namespace": BURST_NAMESPACE,
            "jobs": get_burst_jobs(),
            "pods": get_burst_pods(),
        },
        "timestamp": time.time(),
    }
