"""Flask application for the Research Portal dashboard.

Can be used stand-alone::

    python -m research_portal          # uses cli.main()
    python -m research_portal.app      # direct Flask fallback

Or as a library::

    from research_portal import create_app
    app = create_app()
"""

from __future__ import annotations

import functools
import hashlib
import json
import logging
import os
import re
import secrets
import urllib.request
from datetime import datetime

from flask import Flask, Response, jsonify, render_template_string, request

from research_portal.discovery import (
    discover_pipelines_with_history,
    get_cpu_temps,
    get_disk,
    get_gpu_info,
    get_io_stats,
    get_load,
    get_memory,
    get_net_stats,
    get_per_core,
    get_raid_status,
    get_system_info,
    get_tmux_sessions,
    search_documents,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

# Module-level system_info cache (computed once at startup).
_system_info: dict | None = None


def _get_system_info() -> dict:
    global _system_info
    if _system_info is None:
        _system_info = get_system_info()
    return _system_info


def build_app(*, no_auth: bool = False) -> Flask:
    """Create and configure the Flask application."""
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.environ.get("PORTAL_SECRET", secrets.token_hex(32))

    sysinfo = _get_system_info()
    hostname = sysinfo["hostname"].split(".")[0].upper() or "PORTAL"

    # -- Auth ---------------------------------------------------------------
    # Multi-user: admin (full access) + optional guest accounts (read-only)
    _users: dict[str, tuple[str, str]] = {}  # username -> (pass_hash, role)

    # Admin user
    admin_user = os.environ.get("PORTAL_USER", "atlas")
    admin_hash = hashlib.sha256(
        os.environ.get("PORTAL_PASS", "atlas2026!research").encode()
    ).hexdigest()
    _users[admin_user] = (admin_hash, "admin")

    # Guest users from PORTAL_GUESTS env var: "user1:pass1,user2:pass2"
    for entry in os.environ.get("PORTAL_GUESTS", "").split(","):
        entry = entry.strip()
        if ":" in entry:
            gu, gp = entry.split(":", 1)
            _users[gu.strip()] = (
                hashlib.sha256(gp.strip().encode()).hexdigest(),
                "guest",
            )

    def check_auth(username: str, password: str) -> str | None:
        """Return role ('admin'/'guest') if valid, None otherwise."""
        if username in _users:
            pass_hash, role = _users[username]
            if hashlib.sha256(password.encode()).hexdigest() == pass_hash:
                return role
        return None

    def auth_required(f):  # type: ignore[no-untyped-def]
        @functools.wraps(f)
        def decorated(*args, **kwargs):  # type: ignore[no-untyped-def]
            if no_auth:
                return f(*args, **kwargs)
            auth = request.authorization
            if not auth:
                return Response(
                    "Authentication required.",
                    401,
                    {"WWW-Authenticate": f'Basic realm="{hostname} Portal"'},
                )
            role = check_auth(auth.username, auth.password)
            if not role:
                return Response(
                    "Authentication required.",
                    401,
                    {"WWW-Authenticate": f'Basic realm="{hostname} Portal"'},
                )
            request.environ["portal_role"] = role
            return f(*args, **kwargs)

        return decorated

    # -- Security headers ---------------------------------------------------
    @app.after_request
    def security_headers(response):  # type: ignore[no-untyped-def]
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self' 'unsafe-inline'; "
            "connect-src 'self'; "
            "frame-src *; "
            "img-src 'self' data: https://img.shields.io;"
        )
        return response

    # -- API endpoints ------------------------------------------------------

    @app.route("/api/status")
    @auth_required
    def api_status():  # type: ignore[no-untyped-def]
        return jsonify(
            {
                "timestamp": datetime.now().isoformat(),
                "cpu_temps": get_cpu_temps(),
                "gpus": get_gpu_info(),
                "memory": get_memory(),
                "load": get_load(),
                "disk": get_disk(),
                "sessions": get_tmux_sessions(),
                "raid": get_raid_status(),
                "io": get_io_stats(),
                "net": get_net_stats(),
                "system_info": sysinfo,
            }
        )

    @app.route("/api/cores")
    @auth_required
    def api_cores():  # type: ignore[no-untyped-def]
        return jsonify(get_per_core())

    @app.route("/api/pipelines")
    @auth_required
    def api_pipelines():  # type: ignore[no-untyped-def]
        return jsonify(discover_pipelines_with_history())

    @app.route("/api/system-info")
    @auth_required
    def api_system_info():  # type: ignore[no-untyped-def]
        return jsonify(sysinfo)

    # -- Pages --------------------------------------------------------------

    @app.route("/")
    @auth_required
    def index():  # type: ignore[no-untyped-def]
        return render_template_string(_TEMPLATE, hostname=hostname, sysinfo=sysinfo)

    @app.route("/map")
    @auth_required
    def resource_map():  # type: ignore[no-untyped-def]
        return render_template_string(_MAP_TEMPLATE, hostname=hostname, sysinfo=sysinfo)

    @app.route("/flow")
    @auth_required
    def flow():  # type: ignore[no-untyped-def]
        return render_template_string(_FLOW_TEMPLATE, hostname=hostname, sysinfo=sysinfo)

    @app.route("/api/download/<dataset>")
    @auth_required
    def download_result(dataset):  # type: ignore[no-untyped-def]
        if request.environ.get("portal_role") != "admin":
            return Response("Admin access required.", 403)
        dataset = re.sub(r"[^a-zA-Z0-9_-]", "", dataset)
        results_dir = os.environ.get("PORTAL_RESULTS_DIR", ".")
        path = os.path.join(results_dir, f"result_{dataset}.json")
        if os.path.exists(path):
            with open(path) as f:
                return Response(
                    f.read(),
                    mimetype="application/json",
                    headers={"Content-Disposition": f"attachment; filename=result_{dataset}.json"},
                )
        return "Not found", 404

    # -- LLM Control -------------------------------------------------------

    @app.route("/api/start-llm", methods=["POST"])
    @auth_required
    def api_start_llm():  # type: ignore[no-untyped-def]
        if request.environ.get("portal_role") != "admin":
            return jsonify({"status": "Admin access required"}), 403
        import subprocess

        # Check if already running
        try:
            urllib.request.urlopen("http://localhost:8080/health", timeout=2)
            return jsonify({"status": "running", "msg": "GLM-5 already running"})
        except Exception:
            pass
        # Check GPU memory — need ~50 GB free across GPUs
        gpus = get_gpu_info()
        free_mb = sum(g.get("mem_total", 0) - g.get("mem_used", 0) for g in gpus)
        if free_mb < 50000:
            return jsonify(
                {
                    "status": "busy",
                    "msg": f"GPU busy ({free_mb // 1024} GB free, need ~50 GB)",
                }
            )
        # Start GLM-5 server in background
        subprocess.Popen(["tmux", "new-session", "-d", "-s", "glm5", "bash /tmp/glm5_spec.sh"])
        return jsonify({"status": "starting", "msg": "Loading 170 GB model (3-5 min)"})

    # -- RAG Chat -----------------------------------------------------------

    @app.route("/api/chat", methods=["POST"])
    @auth_required
    def api_chat():  # type: ignore[no-untyped-def]
        body = request.get_json(silent=True) or {}
        question = (body.get("question") or "").strip()
        if not question:
            return jsonify({"error": "question is required"}), 400

        # Retrieve relevant context via TF-IDF
        hits = search_documents(question, top_k=5)
        sources = list({h["source"] for h in hits})

        # Build prompt with context
        context_parts: list[str] = []
        for h in hits:
            context_parts.append(f"[{h['source']}]\n{h['text']}")
        context_block = "\n---\n".join(context_parts)

        if context_block:
            prompt = (
                f"You are a research assistant. Use the following context "
                f"from indexed documents to answer the question. If the context "
                f"does not contain enough information, say so.\n\n"
                f"Context:\n{context_block}\n\n"
                f"Question: {question}\n\nAnswer:"
            )
        else:
            prompt = (
                f"You are a research assistant. No indexed documents were "
                f"found for this query. Answer as best you can.\n\n"
                f"Question: {question}\n\nAnswer:"
            )

        # Call local llama.cpp server
        llm_url = os.environ.get("LLM_URL", "http://localhost:8080/completion")
        payload = json.dumps(
            {
                "prompt": prompt,
                "n_predict": 500,
                "temperature": 0.3,
            }
        ).encode("utf-8")

        try:
            req = urllib.request.Request(
                llm_url,
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                llm_resp = json.loads(resp.read().decode("utf-8"))
            answer = llm_resp.get("content", "").strip()
        except Exception as exc:
            logger.warning("LLM request failed: %s", exc)
            answer = f"LLM server unavailable ({exc}). Retrieved {len(hits)} document chunks."

        return jsonify({"answer": answer, "sources": sources})

    @app.route("/chat")
    @auth_required
    def chat_page():  # type: ignore[no-untyped-def]
        return render_template_string(_CHAT_TEMPLATE, hostname=hostname, sysinfo=sysinfo)

    return app


# ---------------------------------------------------------------------------
# Templates -- all use {{ hostname }} and {{ sysinfo }} instead of hardcoded
# ---------------------------------------------------------------------------

_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{{ hostname }} Research Portal</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: 'Consolas', 'Fira Code', 'Monaco', monospace; background: #0f1117; color: #e2e8f0; font-size: 14px; }
  .header { background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%); padding: 24px 32px; border-bottom: 1px solid #2d3748; display: flex; align-items: center; flex-wrap: wrap; gap: 16px; }
  .header-logo { width: 44px; height: 44px; flex-shrink: 0; }
  .header h1 { font-size: 28px; font-weight: 300; letter-spacing: 2px; }
  .header h1 span { color: #63b3ed; font-weight: 600; }
  .header .subtitle { color: #718096; font-size: 14px; margin-top: 4px; }
  .header-links { margin-top: 8px; }
  .header-links a { color: #63b3ed; text-decoration: none; font-size: 14px; margin-right: 20px; padding: 4px 12px; border: 1px solid #4a5568; border-radius: 6px; transition: background 0.2s; }
  .header-links a:hover { background: #2d3748; }
  .container { max-width: 1400px; margin: 0 auto; padding: 24px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 20px; margin-bottom: 24px; }
  .card { background: #1a202c; border-radius: 12px; padding: 20px; border: 1px solid #2d3748; }
  .card h2 { font-size: 14px; text-transform: uppercase; letter-spacing: 1px; color: #718096; margin-bottom: 16px; }
  .metric { display: flex; justify-content: space-between; align-items: center; padding: 8px 0; border-bottom: 1px solid #2d3748; }
  .metric:last-child { border-bottom: none; }
  .metric .label { color: #a0aec0; font-size: 14px; }
  .metric .value { font-size: 18px; font-weight: 600; }
  .temp-safe { color: #48bb78; }
  .temp-warn { color: #ecc94b; }
  .temp-crit { color: #fc8181; }
  .gpu-bar { height: 8px; background: #2d3748; border-radius: 4px; margin-top: 4px; }
  .gpu-bar-fill { height: 100%; border-radius: 4px; transition: width 0.5s; }
  .gpu-bar-fill.high { background: linear-gradient(90deg, #48bb78, #38a169); }
  .gpu-bar-fill.med { background: linear-gradient(90deg, #ecc94b, #d69e2e); }
  .gpu-bar-fill.low { background: linear-gradient(90deg, #4299e1, #3182ce); }
  .session { display: inline-block; background: #2d3748; padding: 4px 10px; border-radius: 6px; margin: 2px; font-size: 12px; color: #a0aec0; }
  .session.active { border-left: 3px solid #48bb78; }
  .refresh-note { text-align: center; color: #4a5568; font-size: 11px; padding: 8px; }
</style>
</head>
<body>

<div class="header">
  <svg class="header-logo" viewBox="0 0 512 512"><defs><linearGradient id="lo" x1="0" y1="0" x2="1" y2="1"><stop offset="0%" stop-color="#63b3ed"/><stop offset="100%" stop-color="#2b6cb0"/></linearGradient></defs><circle cx="256" cy="256" r="56" fill="url(#lo)" opacity=".9"/><ellipse cx="256" cy="256" rx="180" ry="60" fill="none" stroke="#63b3ed" stroke-width="1.5" transform="rotate(-20,256,256)" opacity=".35"/><ellipse cx="256" cy="256" rx="180" ry="60" fill="none" stroke="#63b3ed" stroke-width="1.5" transform="rotate(40,256,256)" opacity=".25"/><circle cx="148" cy="198" r="8" fill="#48bb78"/><circle cx="370" cy="210" r="8" fill="#ecc94b"/><circle cx="190" cy="340" r="6" fill="#63b3ed" opacity=".6"/><circle cx="330" cy="320" r="6" fill="#63b3ed" opacity=".5"/><text x="256" y="252" fill="#fff" font-size="32" font-weight="700" text-anchor="middle" font-family="sans-serif">R</text><text x="256" y="278" fill="#e2e8f0" font-size="15" font-weight="600" text-anchor="middle" font-family="sans-serif" letter-spacing="7">PORTAL</text></svg>
  <div>
    <h1><span>{{ hostname }}</span> Research Portal</h1>
    <div class="subtitle" id="subtitle">{{ sysinfo.cpu_model }} &middot; {{ sysinfo.gpu_models | join(', ') or 'No GPU detected' }} &middot; {{ sysinfo.total_ram_gb }} GB RAM</div>
    <div class="header-links">
      <a href="/map">Resource Map</a>
      <a href="/flow">Pipeline Flow</a>
      <a href="/chat">Chat</a>
    </div>
  </div>
</div>

<div class="container">

  <!-- System Status -->
  <div class="grid" id="system-grid">
    <div class="card">
      <h2>CPU Temperature</h2>
      <div id="cpu-temps">Loading...</div>
    </div>
    <div class="card">
      <h2>GPU Status</h2>
      <div id="gpu-status">Loading...</div>
    </div>
    <div class="card">
      <h2>System</h2>
      <div id="system-info">Loading...</div>
    </div>
  </div>

  <!-- Active Experiments -->
  <div class="card" style="margin-bottom: 24px;">
    <h2>Active Sessions</h2>
    <div id="sessions">Loading...</div>
  </div>

  <!-- RAID Storage -->
  <div class="card" style="margin-bottom: 24px;">
    <h2>Storage Arrays</h2>
    <div id="raid-status">Loading...</div>
  </div>

  <!-- Dynamic Platform Guide -->
  <div class="card" style="margin-bottom: 24px;">
    <h2>Platform Guide</h2>
    <div id="platform-guide">Loading...</div>
  </div>

  <div class="refresh-note">Auto-refreshes every 10 seconds</div>
</div>

<script>
function tempClass(t) {
  if (t < 75) return 'temp-safe';
  if (t < 85) return 'temp-warn';
  return 'temp-crit';
}

function gpuBarClass(u) {
  if (u > 80) return 'high';
  if (u > 30) return 'med';
  return 'low';
}

async function refresh() {
  try {
    const status = await (await fetch('/api/status')).json();

    // CPU temps
    let cpuHtml = '';
    for (const [pkg, temp] of Object.entries(status.cpu_temps)) {
      cpuHtml += '<div class="metric"><span class="label">' + pkg + '</span><span class="value ' + tempClass(temp) + '">' + temp + '\u00b0C</span></div>';
    }
    const load = status.load;
    if (load) {
      cpuHtml += '<div class="metric"><span class="label">Load</span><span class="value">' + load['1min'] + ' / ' + load['5min'] + ' / ' + load['15min'] + '</span></div>';
    }
    document.getElementById('cpu-temps').innerHTML = cpuHtml || '<span style="color:#4a5568">No sensor data</span>';

    // GPUs
    let gpuHtml = '';
    for (const gpu of status.gpus) {
      const memPct = Math.round(100 * gpu.mem_used / gpu.mem_total);
      gpuHtml += '<div style="margin-bottom: 12px;">';
      gpuHtml += '<div class="metric"><span class="label">GPU ' + gpu.index + ': ' + gpu.name + '</span><span class="value ' + tempClass(gpu.temp) + '">' + gpu.temp + '\u00b0C</span></div>';
      gpuHtml += '<div class="metric"><span class="label">Utilization</span><span class="value">' + gpu.util + '%</span></div>';
      gpuHtml += '<div class="gpu-bar"><div class="gpu-bar-fill ' + gpuBarClass(gpu.util) + '" style="width:' + gpu.util + '%"></div></div>';
      gpuHtml += '<div class="metric"><span class="label">Memory</span><span class="value">' + gpu.mem_used + '/' + gpu.mem_total + ' MB (' + memPct + '%)</span></div>';
      gpuHtml += '</div>';
    }
    document.getElementById('gpu-status').innerHTML = gpuHtml || '<span style="color:#4a5568">No GPU detected</span>';

    // System
    const mem = status.memory;
    const disk = status.disk;
    let sysHtml = '';
    if (mem && mem.total_mb) {
      sysHtml += '<div class="metric"><span class="label">RAM</span><span class="value">' + Math.round(mem.used_mb/1024) + '/' + Math.round(mem.total_mb/1024) + ' GB</span></div>';
    }
    if (disk && disk.total_gb) {
      sysHtml += '<div class="metric"><span class="label">Disk</span><span class="value">' + disk.used_gb + '/' + disk.total_gb + ' GB</span></div>';
    }
    sysHtml += '<div class="metric"><span class="label">Time</span><span class="value">' + new Date(status.timestamp).toLocaleTimeString() + '</span></div>';
    document.getElementById('system-info').innerHTML = sysHtml;

    // Sessions
    let sessHtml = '';
    for (const s of status.sessions) {
      sessHtml += '<span class="session active">' + s.name + '</span>';
    }
    document.getElementById('sessions').innerHTML = sessHtml || '<span style="color:#4a5568">No active sessions</span>';

    // Platform Guide (dynamic)
    const si = status.system_info;
    let guideHtml = '';
    guideHtml += '<div class="metric"><span class="label">Hostname</span><span class="value">' + si.hostname + '</span></div>';
    guideHtml += '<div class="metric"><span class="label">OS</span><span class="value">' + si.os + '</span></div>';
    guideHtml += '<div class="metric"><span class="label">Kernel</span><span class="value">' + si.kernel + '</span></div>';
    guideHtml += '<div class="metric"><span class="label">CPU</span><span class="value">' + si.cpu_model + '</span></div>';
    guideHtml += '<div class="metric"><span class="label">Cores / Threads</span><span class="value">' + si.cpu_cores + ' / ' + si.cpu_threads + '</span></div>';
    guideHtml += '<div class="metric"><span class="label">RAM</span><span class="value">' + si.total_ram_gb + ' GB</span></div>';
    if (si.gpu_models && si.gpu_models.length > 0) {
      for (let i = 0; i < si.gpu_models.length; i++) {
        guideHtml += '<div class="metric"><span class="label">GPU ' + i + '</span><span class="value">' + si.gpu_models[i] + '</span></div>';
      }
    }
    if (mem && mem.total_mb) {
      guideHtml += '<div class="metric"><span class="label">RAM Used</span><span class="value">' + Math.round(mem.used_mb/1024) + ' / ' + Math.round(mem.total_mb/1024) + ' GB</span></div>';
    }
    if (disk && disk.total_gb) {
      guideHtml += '<div class="metric"><span class="label">Disk</span><span class="value">' + disk.used_gb + ' / ' + disk.total_gb + ' GB</span></div>';
    }
    document.getElementById('platform-guide').innerHTML = guideHtml;

    // RAID / Storage Arrays
    let raidHtml = '';
    const tiers = {'raid1': 'WARM (mirror)', 'raid5': 'COLD (archive)', 'raid0': 'CACHE'};
    if (status.raid && status.raid.length > 0) {
      for (const r of status.raid) {
        const tier = r.level === 'raid1' ? 'WARM' : r.level === 'raid5' ? 'COLD' : r.level;
        const stateColor = r.disk_state && r.disk_state.includes('_') ? '#ecc94b' : '#48bb78';
        raidHtml += '<div class="metric"><span class="label">' + r.name + ' (' + r.level + ') ' + (r.mount || '') + '</span>';
        raidHtml += '<span class="value" style="color:' + stateColor + '">[' + (r.disk_state || '?') + ']';
        if (r.size_gb) raidHtml += ' ' + r.size_gb + ' GB';
        raidHtml += '</span></div>';
        if (r.sync_pct != null) {
          raidHtml += '<div class="metric"><span class="label">Sync</span><span class="value" style="color:#ecc94b">' + r.sync_pct.toFixed(1) + '% ' + (r.sync_speed || '') + '</span></div>';
          raidHtml += '<div class="gpu-bar"><div class="gpu-bar-fill med" style="width:' + r.sync_pct + '%"></div></div>';
        }
      }
    }
    // Storage tiers with capacity
    if (disk && disk.total_gb) {
      raidHtml += '<div class="metric" style="margin-top:8px"><span class="label">HOT (NVMe /)</span><span class="value">' + disk.used_gb + ' / ' + disk.total_gb + ' GB</span></div>';
      raidHtml += '<div class="gpu-bar"><div class="gpu-bar-fill ' + (disk.used_gb/disk.total_gb > 0.8 ? 'high' : 'low') + '" style="width:' + Math.round(100*disk.used_gb/disk.total_gb) + '%"></div></div>';
    }
    document.getElementById('raid-status').innerHTML = raidHtml || '<span style="color:#4a5568">No arrays detected</span>';

    // I/O stats
    let ioHtml = '';
    if (status.io) {
      for (const [dev, s] of Object.entries(status.io)) {
        if (dev.startsWith('md') || dev.startsWith('nvme')) {
          ioHtml += '<span class="session active" style="margin:2px">' + dev + ' R:' + s.read_mb + 'MB W:' + s.write_mb + 'MB</span>';
        }
      }
    }

    // Network stats
    let netHtml = '';
    if (status.net) {
      for (const iface of status.net) {
        netHtml += '<span class="session active" style="margin:2px">' + iface.name + ' RX:' + iface.rx_mb + 'MB TX:' + iface.tx_mb + 'MB</span>';
      }
    }
    if (ioHtml || netHtml) {
      const ioNetEl = document.getElementById('raid-status');
      ioNetEl.innerHTML += '<div style="margin-top:8px"><span style="color:#718096;font-size:11px">I/O: </span>' + (ioHtml || 'none') + '</div>';
      ioNetEl.innerHTML += '<div style="margin-top:4px"><span style="color:#718096;font-size:11px">Net: </span>' + (netHtml || 'none') + '</div>';
    }

  } catch(e) {
    console.error('Refresh failed:', e);
  }
}

refresh();
setInterval(refresh, 10000);
</script>
</body>
</html>
"""

_MAP_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{{ hostname }} -- Resource Map</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: 'Consolas', 'Fira Code', 'Monaco', monospace; background: #0f1117; color: #e2e8f0; font-size: 14px; }
  .header { background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%); padding: 16px 24px; border-bottom: 1px solid #2d3748; display: flex; justify-content: space-between; align-items: center; }
  .header h1 { font-size: 24px; font-weight: 300; letter-spacing: 2px; }
  .header h1 span { color: #63b3ed; font-weight: 600; }
  .header a { color: #63b3ed; text-decoration: none; font-size: 14px; padding: 4px 12px; border: 1px solid #4a5568; border-radius: 6px; }
  .container { max-width: 1400px; margin: 20px auto; padding: 0 20px; }
  .sockets { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; margin-bottom: 24px; }
  .socket { background: #1a202c; border-radius: 12px; padding: 16px; border: 1px solid #2d3748; }
  .socket h3 { font-size: 14px; text-transform: uppercase; color: #718096; letter-spacing: 1px; margin-bottom: 12px; }
  .socket .temp { float: right; font-size: 16px; font-weight: 600; }
  .core-grid { display: grid; grid-template-columns: repeat(6, 1fr); gap: 6px; }
  .core { aspect-ratio: 1; border-radius: 6px; display: flex; flex-direction: column; align-items: center; justify-content: center; font-size: 12px; transition: all 0.3s; cursor: default; position: relative; border: 1px solid transparent; }
  .core .id { font-weight: 600; font-size: 14px; }
  .core .job { font-size: 10px; color: #e2e8f0; text-align: center; margin-top: 2px; max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .core.idle { background: #1a202c; border-color: #2d3748; }
  .core.low { background: #1c3a2a; border-color: #276749; }
  .core.med { background: #3a3320; border-color: #975a16; }
  .core.high { background: #3a1c1c; border-color: #c53030; }
  .core.has-job { border-color: #63b3ed; box-shadow: 0 0 8px rgba(99,179,237,0.3); }
  .gpus { display: grid; grid-template-columns: repeat(auto-fit, minmax(350px, 1fr)); gap: 24px; margin-bottom: 24px; }
  .gpu-card { background: #1a202c; border-radius: 12px; padding: 16px; border: 1px solid #2d3748; }
  .gpu-card h3 { font-size: 14px; text-transform: uppercase; color: #718096; letter-spacing: 1px; margin-bottom: 8px; }
  .gpu-visual { height: 100px; background: #0f1117; border-radius: 8px; position: relative; overflow: hidden; margin: 8px 0; }
  .gpu-fill { height: 100%; transition: width 0.5s; position: absolute; left: 0; top: 0; }
  .gpu-fill.util { background: linear-gradient(90deg, #2563eb44, #2563eb88); }
  .gpu-fill.mem { background: linear-gradient(90deg, #48bb7844, #48bb7888); top: 50%; height: 50%; }
  .gpu-label { position: absolute; padding: 4px 8px; font-size: 14px; color: #e2e8f0; z-index: 1; }
  .gpu-label.top { top: 8px; left: 8px; }
  .gpu-label.bot { bottom: 8px; left: 8px; }
  .gpu-temp { position: absolute; top: 8px; right: 8px; font-size: 20px; font-weight: 600; z-index: 1; }
  .legend { display: flex; gap: 16px; justify-content: center; margin: 16px 0; font-size: 13px; color: #718096; }
  .legend-item { display: flex; align-items: center; gap: 4px; }
  .legend-dot { width: 14px; height: 14px; border-radius: 3px; }
  .refresh-bar { text-align: center; color: #4a5568; font-size: 12px; padding: 4px; }
</style>
</head>
<body>

<div class="header">
  <h1><span>{{ hostname }}</span> Resource Map</h1>
  <div>
    <a href="/">Dashboard</a>
  </div>
</div>

<div class="container">
  <div class="legend">
    <div class="legend-item"><div class="legend-dot" style="background:#1a202c;border:1px solid #2d3748"></div> Idle</div>
    <div class="legend-item"><div class="legend-dot" style="background:#1c3a2a;border:1px solid #276749"></div> Low (&lt;30%)</div>
    <div class="legend-item"><div class="legend-dot" style="background:#3a3320;border:1px solid #975a16"></div> Med (30-70%)</div>
    <div class="legend-item"><div class="legend-dot" style="background:#3a1c1c;border:1px solid #c53030"></div> High (&gt;70%)</div>
    <div class="legend-item"><div class="legend-dot" style="background:#1a202c;border:2px solid #63b3ed"></div> Has Job</div>
  </div>

  <div class="sockets" id="sockets-container">
    <!-- Dynamically populated based on detected CPU sockets -->
  </div>

  <div class="gpus" id="gpus-container">
    <!-- Dynamically populated based on detected GPUs -->
  </div>

  <div class="refresh-bar">Live -- refreshes every 5 seconds</div>
</div>

<script>
var systemInfo = null;

function coreClass(util, job) {
  let cls = 'core';
  if (util < 5) cls += ' idle';
  else if (util < 30) cls += ' low';
  else if (util < 70) cls += ' med';
  else cls += ' high';
  if (job) cls += ' has-job';
  return cls;
}

function tempColor(t) {
  if (t < 75) return '#48bb78';
  if (t < 85) return '#ecc94b';
  return '#fc8181';
}

async function init() {
  try {
    systemInfo = await (await fetch('/api/system-info')).json();
  } catch(e) {
    systemInfo = {cpu_model: 'Unknown CPU', cpu_cores: 0, gpu_models: []};
  }

  // Build socket containers dynamically
  const socketsEl = document.getElementById('sockets-container');
  const coresPerSocket = Math.max(1, Math.ceil((systemInfo.cpu_cores || 1) / 2));
  for (let s = 0; s < 2; s++) {
    socketsEl.innerHTML += '<div class="socket">' +
      '<h3>Socket ' + s + ' -- ' + systemInfo.cpu_model + ' <span class="temp" id="temp' + s + '">--\u00b0C</span></h3>' +
      '<div class="core-grid" id="socket' + s + '"></div></div>';
  }

  // Build GPU containers dynamically
  const gpusEl = document.getElementById('gpus-container');
  const gpuModels = systemInfo.gpu_models || [];
  if (gpuModels.length === 0) {
    gpusEl.innerHTML = '<div style="color:#4a5568;padding:12px">No GPUs detected</div>';
  } else {
    for (let i = 0; i < gpuModels.length; i++) {
      gpusEl.innerHTML += '<div class="gpu-card">' +
        '<h3>GPU ' + i + ' -- ' + gpuModels[i] + '</h3>' +
        '<div class="gpu-visual" id="gpu' + i + '-visual">' +
          '<div class="gpu-fill util" id="gpu' + i + '-util"></div>' +
          '<div class="gpu-fill mem" id="gpu' + i + '-mem"></div>' +
          '<span class="gpu-label top" id="gpu' + i + '-util-label">--% util</span>' +
          '<span class="gpu-label bot" id="gpu' + i + '-mem-label">-- MB</span>' +
          '<span class="gpu-temp" id="gpu' + i + '-temp">--\u00b0C</span>' +
        '</div></div>';
    }
  }

  refresh();
  setInterval(refresh, 5000);
}

async function refresh() {
  try {
    const [coreData, statusData] = await Promise.all([
      (await fetch('/api/cores')).json(),
      (await fetch('/api/status')).json(),
    ]);

    // CPU temps
    const temps = statusData.cpu_temps;
    for (const [pkg, temp] of Object.entries(temps)) {
      const id = pkg.includes('0') ? 'temp0' : 'temp1';
      const el = document.getElementById(id);
      if (el) {
        el.textContent = temp + '\u00b0C';
        el.style.color = tempColor(temp);
      }
    }

    // Cores -- distribute evenly across 2 sockets
    const totalCores = systemInfo ? systemInfo.cpu_cores : 24;
    const coresPerSocket = Math.ceil(totalCores / 2);
    for (let socket = 0; socket < 2; socket++) {
      const el = document.getElementById('socket' + socket);
      if (!el) continue;
      let html = '';
      for (let i = 0; i < coresPerSocket; i++) {
        const coreId = socket * coresPerSocket + i;
        const c = coreData[coreId] || {util: 0, job: null};
        html += '<div class="' + coreClass(c.util, c.job) + '" title="Core ' + coreId + (c.job ? ' -- ' + c.job : '') + '">';
        html += '<span class="id">' + coreId + '</span>';
        if (c.job) html += '<span class="job">' + c.job + '</span>';
        html += '</div>';
      }
      el.innerHTML = html;
    }

    // GPUs
    for (const gpu of statusData.gpus) {
      const i = gpu.index;
      const utilEl = document.getElementById('gpu'+i+'-util');
      if (!utilEl) continue;
      utilEl.style.width = gpu.util + '%';
      document.getElementById('gpu'+i+'-mem').style.width = Math.round(100*gpu.mem_used/gpu.mem_total) + '%';
      document.getElementById('gpu'+i+'-util-label').textContent = gpu.util + '% utilization';
      document.getElementById('gpu'+i+'-mem-label').textContent = gpu.mem_used + '/' + gpu.mem_total + ' MB';
      const tempEl = document.getElementById('gpu'+i+'-temp');
      tempEl.textContent = gpu.temp + '\u00b0C';
      tempEl.style.color = tempColor(gpu.temp);
    }

  } catch(e) {
    console.error(e);
  }
}

init();
</script>
</body>
</html>
"""

_FLOW_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{{ hostname }} -- Live Pipeline Flow</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: 'Consolas', 'Fira Code', 'Monaco', monospace; background: #0f1117; color: #e2e8f0; font-size: 13px; }
  .header { background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%); padding: 12px 24px; border-bottom: 1px solid #2d3748; display: flex; justify-content: space-between; align-items: center; }
  .header h1 { font-size: 20px; font-weight: 300; letter-spacing: 2px; }
  .header h1 span { color: #63b3ed; font-weight: 600; }
  .header-nav a { color: #63b3ed; text-decoration: none; font-size: 12px; padding: 4px 12px; border: 1px solid #4a5568; border-radius: 6px; margin-left: 8px; }
  .container { max-width: 1400px; margin: 10px auto; padding: 0 16px; }
  .pipeline-row { border-radius: 6px; padding: 6px 10px; margin-bottom: 4px; display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
  .pipeline-row.active { background: #1a2035; border: 1px solid #2b6cb0; border-left: 3px solid #63b3ed; }
  .pipeline-row.completed { background: #1a2c1a; border: 1px solid #276749; border-left: 3px solid #48bb78; }
  .pipeline-name { font-size: 13px; font-weight: 600; min-width: 160px; }
  .pipeline-row.active .pipeline-name { color: #63b3ed; }
  .pipeline-row.completed .pipeline-name { color: #48bb78; }
  .pipeline-name .count { color: #718096; font-weight: 400; font-size: 10px; margin-left: 4px; }
  .stages { display: flex; align-items: center; gap: 3px; flex-wrap: nowrap; }
  .stage { padding: 4px 10px; border-radius: 4px; font-size: 12px; display: inline-flex; flex-direction: column; align-items: center; min-width: 70px; position: relative; overflow: hidden; }
  .stage .label { font-weight: 600; font-size: 11px; }
  .stage .detail { font-size: 10px; color: #a0aec0; margin-top: 1px; }
  .stage.running { background: #1c3a5e; border: 1px solid #2b6cb0; animation: pulse 2s infinite; }
  .stage.complete { background: #1c3a2a; border: 1px solid #276749; }
  .stage.unknown { background: #2d3748; border: 1px solid #4a5568; }
  .progress-fill { position: absolute; bottom: 0; left: 0; height: 3px; background: #63b3ed; transition: width 1s; border-radius: 0 0 8px 8px; }
  .gpu-tag { display: inline-block; font-size: 9px; padding: 1px 5px; border-radius: 3px; margin-left: 4px; }
  .gpu-tag.g0 { background: #2b6cb0; color: #bee3f8; }
  .gpu-tag.g1 { background: #975a16; color: #fefcbf; }
  .arrow { color: #4a5568; font-size: 12px; }
  .summary { display: flex; gap: 12px; margin-bottom: 12px; }
  .summary-card { background: #1a202c; border-radius: 6px; padding: 6px 14px; border: 1px solid #2d3748; text-align: center; }
  .summary-card .num { font-size: 22px; font-weight: 600; color: #63b3ed; }
  .summary-card .lbl { font-size: 11px; color: #718096; }
  .section-title { font-size: 11px; text-transform: uppercase; color: #718096; letter-spacing: 1px; margin: 10px 0 4px; }
  .refresh-note { text-align: center; color: #4a5568; font-size: 10px; padding: 4px; }
  @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.7; } }
</style>
</head>
<body>

<div class="header">
  <h1><span>{{ hostname }}</span> Pipeline Flow</h1>
  <div class="header-nav">
    <a href="/">Dashboard</a>
    <a href="/map">Resource Map</a>
    <a href="/flow">Pipeline Flow</a>
  </div>
</div>

<div class="container">
  <div class="summary" id="summary"></div>
  <div class="section-title">Active Pipelines</div>
  <div id="active-pipelines"></div>
  <div class="section-title" style="margin-top: 24px;">Completed</div>
  <div id="completed-pipelines"></div>
  <div class="refresh-note">Auto-discovers pipelines from running processes -- refreshes every 5s</div>
</div>

<script>
function stageClass(stage) {
  if (!stage) return 'unknown';
  if (stage.status === 'complete') return 'complete';
  if (stage.status === 'running') return 'running';
  return 'unknown';
}

function fmtElapsed(s) {
  if (!s && s !== 0) return '';
  if (s < 60) return s + 's ago';
  if (s < 3600) return Math.round(s/60) + 'm ago';
  if (s < 86400) return Math.round(s/3600) + 'h ago';
  return Math.round(s/86400) + 'd ago';
}

function renderPipeline(p) {
  const isActive = p.process_count > 0;
  const rowClass = isActive ? 'active' : 'completed';

  let html = '<div class="pipeline-row ' + rowClass + '">';
  html += '<div class="pipeline-name">' + p.name;
  if (isActive) html += '<span class="count">' + p.process_count + ' processes</span>';
  if (!isActive && p.completed_ago_s != null) html += '<span class="count">' + fmtElapsed(p.completed_ago_s) + '</span>';
  html += '</div>';
  html += '<div class="stages">';

  p.stages.forEach(function(s, i) {
    const cls = 'stage ' + stageClass(s);
    html += '<div class="' + cls + '">';
    html += '<span class="label">' + (s.label || 'running') + '</span>';
    if (s.gpu != null) html += '<span class="gpu-tag g' + s.gpu + '">GPU ' + s.gpu + '</span>';
    if (s.detail) html += '<span class="detail">' + s.detail + '</span>';
    else if (s.cpu) html += '<span class="detail">core ' + (s.core||'?') + ' \u00b7 ' + s.cpu + '% CPU</span>';
    if (s.progress > 0) html += '<div class="progress-fill" style="width:' + s.progress + '%"></div>';
    html += '</div>';
    if (i < p.stages.length - 1) html += '<span class="arrow">\u2192</span>';
  });

  html += '</div></div>';
  return html;
}

async function refresh() {
  try {
    const pipelines = await (await fetch('/api/pipelines')).json();

    const active = pipelines.filter(function(p) { return p.process_count > 0; });
    const completed = pipelines.filter(function(p) { return p.process_count === 0; });

    document.getElementById('active-pipelines').innerHTML =
      active.length ? active.map(renderPipeline).join('') : '<div style="color:#4a5568;padding:12px">No active pipelines</div>';
    document.getElementById('completed-pipelines').innerHTML =
      completed.length ? completed.map(renderPipeline).join('') : '<div style="color:#4a5568;padding:12px">None yet</div>';

    document.getElementById('summary').innerHTML =
      '<div class="summary-card"><div class="num">' + active.length + '</div><div class="lbl">Active</div></div>' +
      '<div class="summary-card"><div class="num">' + completed.length + '</div><div class="lbl">Completed</div></div>' +
      '<div class="summary-card"><div class="num">' + pipelines.length + '</div><div class="lbl">Total</div></div>';

  } catch(e) { console.error(e); }
}

refresh();
setInterval(refresh, 5000);
</script>
</body>
</html>
"""


_CHAT_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{{ hostname }} -- Research Chat</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: 'Consolas', 'Fira Code', 'Monaco', monospace; background: #0f1117; color: #e2e8f0; font-size: 14px; display: flex; flex-direction: column; height: 100vh; }
  .header { background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%); padding: 12px 24px; border-bottom: 1px solid #2d3748; display: flex; justify-content: space-between; align-items: center; flex-shrink: 0; }
  .header h1 { font-size: 20px; font-weight: 300; letter-spacing: 2px; }
  .header h1 span { color: #63b3ed; font-weight: 600; }
  .header-nav a { color: #63b3ed; text-decoration: none; font-size: 12px; padding: 4px 12px; border: 1px solid #4a5568; border-radius: 6px; margin-left: 8px; }
  .header-nav a:hover { background: #2d3748; }
  .chat-container { flex: 1; max-width: 900px; width: 100%; margin: 0 auto; padding: 20px; display: flex; flex-direction: column; overflow: hidden; }
  .messages { flex: 1; overflow-y: auto; padding: 12px 0; }
  .message { margin-bottom: 16px; }
  .message.user .bubble { background: #2b6cb0; border-radius: 12px 12px 4px 12px; padding: 10px 14px; display: inline-block; max-width: 80%; float: right; clear: both; }
  .message.assistant .bubble { background: #1a202c; border: 1px solid #2d3748; border-radius: 12px 12px 12px 4px; padding: 10px 14px; display: inline-block; max-width: 90%; clear: both; }
  .message .bubble { white-space: pre-wrap; word-wrap: break-word; line-height: 1.5; }
  .sources { clear: both; margin-top: 8px; }
  .sources-label { font-size: 11px; color: #718096; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 4px; }
  .source-tag { display: inline-block; font-size: 11px; background: #2d3748; color: #a0aec0; padding: 2px 8px; border-radius: 4px; margin: 2px 4px 2px 0; }
  .input-row { display: flex; gap: 8px; padding-top: 12px; border-top: 1px solid #2d3748; flex-shrink: 0; }
  .input-row input { flex: 1; background: #1a202c; border: 1px solid #2d3748; border-radius: 8px; padding: 10px 14px; color: #e2e8f0; font-family: inherit; font-size: 14px; outline: none; }
  .input-row input:focus { border-color: #63b3ed; }
  .input-row input::placeholder { color: #4a5568; }
  .input-row button { background: #2b6cb0; color: #e2e8f0; border: none; border-radius: 8px; padding: 10px 20px; font-family: inherit; font-size: 14px; cursor: pointer; transition: background 0.2s; }
  .input-row button:hover { background: #3182ce; }
  .input-row button:disabled { background: #2d3748; color: #4a5568; cursor: not-allowed; }
  .loading { display: inline-block; }
  .loading::after { content: ''; display: inline-block; width: 12px; height: 12px; border: 2px solid #4a5568; border-top-color: #63b3ed; border-radius: 50%; animation: spin 0.8s linear infinite; margin-left: 8px; vertical-align: middle; }
  .empty-state { text-align: center; color: #4a5568; padding: 60px 20px; }
  .empty-state .icon { font-size: 48px; margin-bottom: 12px; }
  .empty-state p { margin-top: 8px; font-size: 13px; }
  @keyframes spin { to { transform: rotate(360deg); } }
</style>
</head>
<body>

<div class="header">
  <h1><span>{{ hostname }}</span> Research Chat</h1>
  <div class="header-nav">
    <a href="/">Dashboard</a>
    <a href="/map">Resource Map</a>
    <a href="/flow">Pipeline Flow</a>
    <a href="/chat">Chat</a>
  </div>
</div>

<div class="chat-container">
  <div class="messages" id="messages">
    <div class="empty-state" id="empty-state">
      <div class="icon">?</div>
      <div>Ask questions about your research results and documents.</div>
      <p>Uses TF-IDF retrieval over indexed files + local LLM for answers.</p>
    </div>
    <div id="llm-status" style="display:none; background:#3a1c1c; border:1px solid #c53030; border-radius:6px; padding:8px 14px; margin:8px; color:#fc8181; font-size:12px;"></div>
  </div>

  <div class="input-row">
    <input type="text" id="question-input" placeholder="Ask about your research..." autocomplete="off" />
    <button id="send-btn" onclick="sendQuestion()">Send</button>
  </div>
</div>

<script>
var messagesEl = document.getElementById('messages');
var inputEl = document.getElementById('question-input');
var sendBtn = document.getElementById('send-btn');
var emptyState = document.getElementById('empty-state');
var llmStatus = document.getElementById('llm-status');

// Check LLM availability on load and every 30s
async function checkLLM() {
  try {
    var r = await fetch('/api/chat', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({question:'ping'})});
    var d = await r.json();
    if (d.answer && d.answer.includes('unavailable')) {
      var sr = await fetch('/api/status');
      var st = await sr.json();
      var gpus = (st.gpus||[]).map(function(g){return 'GPU'+g.index+': '+g.mem_used+'/'+g.mem_total+'MB '+g.util+'%'}).join(' | ');
      var free = (st.gpus||[]).reduce(function(s,g){return s+(g.mem_total-g.mem_used)},0);
      var canStart = free > 50000;
      var btn = canStart
        ? '<button onclick="startLLM()" style="margin-left:8px;background:#2b6cb0;color:#fff;border:none;padding:4px 12px;border-radius:4px;cursor:pointer;font-size:12px">Start GLM-5</button>'
        : '<button disabled style="margin-left:8px;background:#4a5568;color:#718096;border:none;padding:4px 12px;border-radius:4px;font-size:12px;cursor:not-allowed">GPUs Busy</button>';
      llmStatus.innerHTML = 'GLM-5 offline | ' + gpus + ' ' + btn;
      llmStatus.style.display='block'; llmStatus.style.background='#3a1c1c'; llmStatus.style.borderColor='#c53030'; llmStatus.style.color='#fc8181';
    } else {
      llmStatus.innerHTML = 'GLM-5 online';
      llmStatus.style.display='block'; llmStatus.style.background='#1a2c1a'; llmStatus.style.borderColor='#276749'; llmStatus.style.color='#48bb78';
    }
  } catch(e) {
    llmStatus.innerHTML = 'GLM-5 offline';
    llmStatus.style.display='block'; llmStatus.style.background='#3a1c1c'; llmStatus.style.borderColor='#c53030'; llmStatus.style.color='#fc8181';
  }
}
checkLLM();
setInterval(checkLLM, 30000);

async function startLLM() {
  try {
    var r = await fetch('/api/start-llm', {method:'POST'});
    var d = await r.json();
    if (d.status === 'busy') {
      llmStatus.innerHTML = d.msg + ' <button disabled style="margin-left:8px;background:#4a5568;color:#718096;border:none;padding:4px 12px;border-radius:4px;font-size:12px;cursor:not-allowed">GPUs Busy</button>';
    } else if (d.status === 'starting') {
      llmStatus.innerHTML = d.msg;
      llmStatus.style.background = '#1a2c1a';
      llmStatus.style.borderColor = '#276749';
      llmStatus.style.color = '#48bb78';
      setTimeout(checkLLM, 120000);
    } else {
      llmStatus.innerHTML = d.msg || 'OK';
    }
  } catch(e) {
    llmStatus.innerHTML = 'Failed: ' + e;
  }
}

inputEl.addEventListener('keydown', function(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendQuestion();
  }
});

function scrollToBottom() {
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function addMessage(role, text, sources) {
  if (emptyState) {
    emptyState.remove();
    emptyState = null;
  }
  var div = document.createElement('div');
  div.className = 'message ' + role;
  var bubble = document.createElement('div');
  bubble.className = 'bubble';
  bubble.textContent = text;
  div.appendChild(bubble);

  if (sources && sources.length > 0) {
    var srcDiv = document.createElement('div');
    srcDiv.className = 'sources';
    srcDiv.innerHTML = '<div class="sources-label">Sources</div>';
    sources.forEach(function(s) {
      var tag = document.createElement('span');
      tag.className = 'source-tag';
      tag.textContent = s;
      srcDiv.appendChild(tag);
    });
    div.appendChild(srcDiv);
  }

  messagesEl.appendChild(div);
  scrollToBottom();
  return div;
}

function addLoading() {
  if (emptyState) {
    emptyState.remove();
    emptyState = null;
  }
  var div = document.createElement('div');
  div.className = 'message assistant';
  div.id = 'loading-msg';
  var bubble = document.createElement('div');
  bubble.className = 'bubble';
  bubble.innerHTML = 'Thinking<span class="loading"></span>';
  div.appendChild(bubble);
  messagesEl.appendChild(div);
  scrollToBottom();
}

function removeLoading() {
  var el = document.getElementById('loading-msg');
  if (el) el.remove();
}

async function sendQuestion() {
  var question = inputEl.value.trim();
  if (!question) return;

  inputEl.value = '';
  sendBtn.disabled = true;
  addMessage('user', question);
  addLoading();

  try {
    var resp = await fetch('/api/chat', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({question: question})
    });
    var data = await resp.json();
    removeLoading();
    if (data.error) {
      addMessage('assistant', 'Error: ' + data.error);
    } else {
      addMessage('assistant', data.answer, data.sources);
    }
  } catch(e) {
    removeLoading();
    addMessage('assistant', 'Request failed: ' + e.message);
  }

  sendBtn.disabled = false;
  inputEl.focus();
}

inputEl.focus();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Direct execution fallback
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from research_portal.cli import main

    main()
