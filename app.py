#!/usr/bin/env python3
"""Atlas Research Portal — Web dashboard for the Atlas workstation.

Provides:
- Live system monitoring (CPU/GPU temps, utilization, memory)
- Running experiment status
- Results dashboard with charts
- Platform guide and resource links
- Quick actions (launch/kill experiments)

Usage:
    python app.py  # serves on http://atlas:8080
"""

import json
import os
import subprocess
import time
import glob
import secrets
import hashlib
import functools
from datetime import datetime
from flask import Flask, render_template_string, jsonify, request, Response

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("PORTAL_SECRET", secrets.token_hex(32))

# ─── Security ──────────────────────────────────────────────────────

# Auth credentials (set via env vars or defaults for first run)
PORTAL_USER = os.environ.get("PORTAL_USER", "atlas")
PORTAL_PASS_HASH = hashlib.sha256(
    os.environ.get("PORTAL_PASS", "atlas2026!research").encode()
).hexdigest()


def check_auth(username, password):
    return (
        username == PORTAL_USER
        and hashlib.sha256(password.encode()).hexdigest() == PORTAL_PASS_HASH
    )


def auth_required(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth or not check_auth(auth.username, auth.password):
            return Response(
                "Authentication required.",
                401,
                {"WWW-Authenticate": 'Basic realm="Atlas Portal"'},
            )
        return f(*args, **kwargs)
    return decorated


# Security headers on every response
@app.after_request
def security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    # CSP: allow self + netdata iframe
    response.headers["Content-Security-Policy"] = (
        "default-src 'self' 'unsafe-inline'; "
        "connect-src 'self'; "
        "frame-src http://100.68.134.21:19999 https://100.68.134.21:19999; "
        "img-src 'self' data: https://img.shields.io;"
    )
    return response

# ─── System monitoring ─────────────────────────────────────────────

def get_cpu_temps():
    try:
        out = subprocess.check_output(["sensors"], text=True, timeout=5)
        temps = {}
        for line in out.splitlines():
            if "Package" in line:
                parts = line.split("+")
                if len(parts) > 1:
                    temp = float(parts[1].split("°")[0])
                    pkg = line.split(":")[0].strip()
                    temps[pkg] = temp
        return temps
    except Exception:
        return {}

def get_gpu_info():
    try:
        out = subprocess.check_output([
            "nvidia-smi",
            "--query-gpu=index,name,utilization.gpu,memory.used,memory.total,temperature.gpu",
            "--format=csv,noheader,nounits"
        ], text=True, timeout=5)
        gpus = []
        for line in out.strip().split("\n"):
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 6:
                gpus.append({
                    "index": int(parts[0]),
                    "name": parts[1],
                    "util": int(parts[2]),
                    "mem_used": int(parts[3]),
                    "mem_total": int(parts[4]),
                    "temp": int(parts[5]),
                })
        return gpus
    except Exception:
        return []

def get_memory():
    try:
        with open("/proc/meminfo") as f:
            info = {}
            for line in f:
                parts = line.split()
                info[parts[0].rstrip(":")] = int(parts[1])
        total = info.get("MemTotal", 0) // 1024
        avail = info.get("MemAvailable", 0) // 1024
        return {"total_mb": total, "available_mb": avail, "used_mb": total - avail}
    except Exception:
        return {}

def get_load():
    try:
        load1, load5, load15 = os.getloadavg()
        return {"1min": round(load1, 1), "5min": round(load5, 1), "15min": round(load15, 1)}
    except Exception:
        return {}

def get_disk():
    try:
        st = os.statvfs("/")
        total = st.f_blocks * st.f_frsize // (1024**3)
        free = st.f_bavail * st.f_frsize // (1024**3)
        return {"total_gb": total, "free_gb": free, "used_gb": total - free}
    except Exception:
        return {}

def get_tmux_sessions():
    try:
        out = subprocess.check_output(
            ["tmux", "list-sessions", "-F", "#{session_name}:#{session_created}"],
            text=True, timeout=5
        )
        sessions = []
        for line in out.strip().split("\n"):
            if ":" in line:
                name, created = line.split(":", 1)
                sessions.append({"name": name, "created": created})
        return sessions
    except Exception:
        return []

def get_results():
    results = []
    for jf in sorted(glob.glob("/home/claude/tensor-3body/result_*.json")):
        try:
            with open(jf) as f:
                r = json.load(f)
            bl = r.get("baselines", {})
            results.append({
                "name": r.get("name", "?"),
                "N": r.get("N", 0),
                "d": r.get("d", 0),
                "test_f1": r.get("test_f1", 0),
                "train_f1": r.get("train_f1", 0),
                "formula": r.get("formula", "?")[:40],
                "vs_gb": f"{bl.get('GB', {}).get('sigma', 0):.1f} {bl.get('GB', {}).get('dir', '?')}",
                "vs_rf": f"{bl.get('RF', {}).get('sigma', 0):.1f} {bl.get('RF', {}).get('dir', '?')}",
                "vs_lr": f"{bl.get('LR', {}).get('sigma', 0):.1f} {bl.get('LR', {}).get('dir', '?')}",
                "beats_gb": bl.get("GB", {}).get("dir") == "A*>",
            })
        except Exception:
            pass
    return results


# ─── API endpoints ─────────────────────────────────────────────────

def get_per_core():
    """Get per-core utilization and which process is on each core."""
    cores = {}
    try:
        # Per-core utilization from /proc/stat
        with open("/proc/stat") as f:
            for line in f:
                if line.startswith("cpu") and line[3] != " ":
                    parts = line.split()
                    core_id = int(parts[0][3:])
                    user, nice, system, idle = int(parts[1]), int(parts[2]), int(parts[3]), int(parts[4])
                    total = user + nice + system + idle
                    busy = user + nice + system
                    cores[core_id] = {"util": round(100 * busy / max(total, 1)), "job": None}
    except Exception:
        pass

    # Map processes to cores via taskset/affinity
    try:
        out = subprocess.check_output(
            ["ps", "-eo", "pid,psr,comm,args", "--no-headers"],
            text=True, timeout=5
        )
        for line in out.strip().split("\n"):
            parts = line.split(None, 3)
            if len(parts) >= 4:
                pid, psr, comm, args = parts[0], int(parts[1]), parts[2], parts[3]
                if "run_one.py" in args or "run_qwen" in args or "finetune" in args:
                    # Extract dataset name
                    name = comm
                    if "run_one.py" in args:
                        idx = args.find("run_one.py")
                        rest = args[idx+11:].strip().split()
                        if rest:
                            name = rest[0]
                    elif "run_qwen" in args:
                        name = "Qwen-eval"
                    elif "finetune" in args:
                        name = "Qwen-train"
                    if psr in cores:
                        cores[psr]["job"] = name
    except Exception:
        pass

    return cores


@app.route("/api/cores")
@auth_required
def api_cores():
    return jsonify(get_per_core())


@app.route("/api/status")
@auth_required
def api_status():
    return jsonify({
        "timestamp": datetime.now().isoformat(),
        "cpu_temps": get_cpu_temps(),
        "gpus": get_gpu_info(),
        "memory": get_memory(),
        "load": get_load(),
        "disk": get_disk(),
        "sessions": get_tmux_sessions(),
    })

@app.route("/api/results")
@auth_required
def api_results():
    return jsonify(get_results())


# ─── Main page ─────────────────────────────────────────────────────

TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Atlas Research Portal</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: 'Segoe UI', system-ui, sans-serif; background: #0f1117; color: #e2e8f0; }
  .header { background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%); padding: 24px 32px; border-bottom: 1px solid #2d3748; }
  .header h1 { font-size: 28px; font-weight: 300; letter-spacing: 2px; }
  .header h1 span { color: #63b3ed; font-weight: 600; }
  .header .subtitle { color: #718096; font-size: 14px; margin-top: 4px; }
  .header-links { margin-top: 8px; }
  .header-links a { color: #63b3ed; text-decoration: none; font-size: 13px; margin-right: 20px; padding: 4px 12px; border: 1px solid #4a5568; border-radius: 6px; transition: background 0.2s; }
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
  .results-table { width: 100%; border-collapse: collapse; font-size: 13px; }
  .results-table th { text-align: left; padding: 10px 8px; color: #718096; border-bottom: 2px solid #2d3748; font-size: 11px; text-transform: uppercase; letter-spacing: 1px; }
  .results-table td { padding: 8px; border-bottom: 1px solid #2d3748; }
  .results-table tr:hover { background: #2d3748; }
  .badge-win { background: #276749; color: #9ae6b4; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; }
  .badge-loss { background: #742a2a; color: #feb2b2; padding: 2px 8px; border-radius: 4px; font-size: 11px; }
  .session { display: inline-block; background: #2d3748; padding: 4px 10px; border-radius: 6px; margin: 2px; font-size: 12px; color: #a0aec0; }
  .session.active { border-left: 3px solid #48bb78; }
  .resources { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; }
  .resource-link { display: block; padding: 12px 16px; background: #2d3748; border-radius: 8px; color: #63b3ed; text-decoration: none; font-size: 13px; transition: background 0.2s; }
  .resource-link:hover { background: #4a5568; }
  .resource-link .desc { color: #718096; font-size: 11px; margin-top: 4px; }
  .formula { font-family: 'Consolas', monospace; font-size: 12px; color: #a0aec0; }
  .refresh-note { text-align: center; color: #4a5568; font-size: 11px; padding: 8px; }
</style>
</head>
<body>

<div class="header">
  <h1><span>ATLAS</span> Research Portal</h1>
  <div class="subtitle">HP Z840 — 2x Xeon E5-2690 v3 · 2x Quadro GV100 32GB · Theory Radar · ARC-AGI-2</div>
  <div class="header-links">
    <a href="/map">Resource Map</a>
    <a href="/flow">Pipeline Flow</a>
    <a href="http://100.68.134.21:19999" target="_blank">Netdata</a>
    <a href="https://github.com/ahb-sjsu/theory-radar" target="_blank">Theory Radar</a>
    <a href="https://github.com/ahb-sjsu/atlas-portal" target="_blank">Source</a>
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
    <h2>Active Experiments</h2>
    <div id="sessions">Loading...</div>
  </div>

  <!-- Results -->
  <div class="card" style="margin-bottom: 24px;">
    <h2>Theory Radar Results — Formula vs Ensemble</h2>
    <div style="overflow-x: auto;">
      <table class="results-table" id="results-table">
        <thead>
          <tr>
            <th>Dataset</th><th>N</th><th>d</th><th>Test F1</th>
            <th>Formula</th><th>vs GB</th><th>vs RF</th><th>vs LR</th>
          </tr>
        </thead>
        <tbody id="results-body">
          <tr><td colspan="8">Loading...</td></tr>
        </tbody>
      </table>
    </div>
  </div>

  <!-- Resources -->
  <div class="card">
    <h2>Resources</h2>
    <div class="resources">
      <a class="resource-link" href="https://github.com/ahb-sjsu/theory-radar">
        Theory Radar <div class="desc">Symbolic formula search — GitHub</div>
      </a>
      <a class="resource-link" href="https://pypi.org/project/theory-radar/">
        theory-radar <div class="desc">PyPI package</div>
      </a>
      <a class="resource-link" href="https://pypi.org/project/batch-probe/">
        batch-probe <div class="desc">GPU batch size + thermal management</div>
      </a>
      <a class="resource-link" href="https://github.com/ahb-sjsu/erisml-lib">
        ErisML <div class="desc">Governed agent modeling language</div>
      </a>
      <a class="resource-link" href="https://www.kaggle.com/competitions/kaggle-measuring-agi">
        Measuring AGI <div class="desc">Kaggle competition — 5 tracks</div>
      </a>
      <a class="resource-link" href="https://www.kaggle.com/competitions/arc-prize-2025">
        ARC-AGI-2 <div class="desc">Abstract reasoning challenge</div>
      </a>
    </div>
  </div>

  <!-- Platform Guide -->
  <div class="card" style="margin-top: 24px;">
    <h2>Platform Guide</h2>
    <div style="font-size: 13px; color: #a0aec0; line-height: 1.8;">
      <strong>Hardware:</strong> 2x Xeon E5-2690 v3 (48 threads), 128GB RAM (upgrading to 320-384GB), 2x Quadro GV100 32GB (NVLink pending)<br>
      <strong>Venvs:</strong> <code>/home/claude/env</code> (Theory Radar: cupy, sklearn) · <code>/home/claude/env-infer</code> (LLM inference: torch, transformers, peft)<br>
      <strong>GLM-5:</strong> <code>/home/claude/models/GLM-5-REAP-50-Q3_K_M/</code> (182GB GGUF) · llama.cpp at <code>/home/claude/llama.cpp/</code><br>
      <strong>Qwen v7:</strong> LoRA adapter at <code>/home/claude/arc-agi-2/models/qwen2.5-7b-arc-code-lora/</code><br>
      <strong>Thermal limit:</strong> Package 0 throttles at 82°C, critical at 100°C. Use batch-probe ThermalJobManager.<br>
      <strong>Access:</strong> Tailscale IP 100.68.134.21 · SSH user claude · RDP via xrdp :3389
    </div>
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
    // System status
    const status = await (await fetch('/api/status')).json();

    // CPU temps
    let cpuHtml = '';
    for (const [pkg, temp] of Object.entries(status.cpu_temps)) {
      cpuHtml += `<div class="metric"><span class="label">${pkg}</span><span class="value ${tempClass(temp)}">${temp}°C</span></div>`;
    }
    const load = status.load;
    cpuHtml += `<div class="metric"><span class="label">Load</span><span class="value">${load['1min']} / ${load['5min']} / ${load['15min']}</span></div>`;
    document.getElementById('cpu-temps').innerHTML = cpuHtml;

    // GPUs
    let gpuHtml = '';
    for (const gpu of status.gpus) {
      const memPct = Math.round(100 * gpu.mem_used / gpu.mem_total);
      gpuHtml += `<div style="margin-bottom: 12px;">
        <div class="metric"><span class="label">GPU ${gpu.index}: ${gpu.name}</span><span class="value ${tempClass(gpu.temp)}">${gpu.temp}°C</span></div>
        <div class="metric"><span class="label">Utilization</span><span class="value">${gpu.util}%</span></div>
        <div class="gpu-bar"><div class="gpu-bar-fill ${gpuBarClass(gpu.util)}" style="width:${gpu.util}%"></div></div>
        <div class="metric"><span class="label">Memory</span><span class="value">${gpu.mem_used}/${gpu.mem_total} MB (${memPct}%)</span></div>
      </div>`;
    }
    document.getElementById('gpu-status').innerHTML = gpuHtml;

    // System
    const mem = status.memory;
    const disk = status.disk;
    document.getElementById('system-info').innerHTML = `
      <div class="metric"><span class="label">RAM</span><span class="value">${Math.round(mem.used_mb/1024)}/${Math.round(mem.total_mb/1024)} GB</span></div>
      <div class="metric"><span class="label">Disk</span><span class="value">${disk.used_gb}/${disk.total_gb} GB</span></div>
      <div class="metric"><span class="label">Time</span><span class="value">${new Date(status.timestamp).toLocaleTimeString()}</span></div>
    `;

    // Sessions
    let sessHtml = '';
    for (const s of status.sessions) {
      sessHtml += `<span class="session active">${s.name}</span>`;
    }
    document.getElementById('sessions').innerHTML = sessHtml || '<span style="color:#4a5568">No active experiments</span>';

    // Results
    const results = await (await fetch('/api/results')).json();
    let tbody = '';
    let wins = 0, total = results.length;
    for (const r of results.sort((a,b) => a.name.localeCompare(b.name))) {
      if (r.beats_gb) wins++;
      const badge = r.beats_gb ? '<span class="badge-win">A*&gt;</span>' : '<span class="badge-loss">GB&gt;</span>';
      tbody += `<tr>
        <td><strong>${r.name}</strong></td>
        <td>${r.N}</td><td>${r.d}</td>
        <td>${r.test_f1.toFixed(4)}</td>
        <td class="formula">${r.formula}</td>
        <td>${badge} ${r.vs_gb}</td>
        <td>${r.vs_rf}</td>
        <td>${r.vs_lr}</td>
      </tr>`;
    }
    tbody += `<tr style="font-weight:600; border-top:2px solid #4a5568">
      <td colspan="5">Formula beats GB: ${wins}/${total}</td>
      <td colspan="3"></td></tr>`;
    document.getElementById('results-body').innerHTML = tbody;

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

MAP_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Atlas — Resource Map</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: 'Segoe UI', system-ui, sans-serif; background: #0f1117; color: #e2e8f0; }
  .header { background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%); padding: 16px 24px; border-bottom: 1px solid #2d3748; display: flex; justify-content: space-between; align-items: center; }
  .header h1 { font-size: 22px; font-weight: 300; letter-spacing: 2px; }
  .header h1 span { color: #63b3ed; font-weight: 600; }
  .header a { color: #63b3ed; text-decoration: none; font-size: 13px; padding: 4px 12px; border: 1px solid #4a5568; border-radius: 6px; }
  .container { max-width: 1200px; margin: 20px auto; padding: 0 20px; }
  .sockets { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; margin-bottom: 24px; }
  .socket { background: #1a202c; border-radius: 12px; padding: 16px; border: 1px solid #2d3748; }
  .socket h3 { font-size: 12px; text-transform: uppercase; color: #718096; letter-spacing: 1px; margin-bottom: 12px; }
  .socket .temp { float: right; font-size: 14px; font-weight: 600; }
  .core-grid { display: grid; grid-template-columns: repeat(6, 1fr); gap: 4px; }
  .core { aspect-ratio: 1; border-radius: 6px; display: flex; flex-direction: column; align-items: center; justify-content: center; font-size: 9px; transition: all 0.3s; cursor: default; position: relative; border: 1px solid transparent; }
  .core .id { font-weight: 600; font-size: 10px; }
  .core .job { font-size: 7px; color: #e2e8f0; text-align: center; margin-top: 2px; max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .core.idle { background: #1a202c; border-color: #2d3748; }
  .core.low { background: #1c3a2a; border-color: #276749; }
  .core.med { background: #3a3320; border-color: #975a16; }
  .core.high { background: #3a1c1c; border-color: #c53030; }
  .core.has-job { border-color: #63b3ed; box-shadow: 0 0 8px rgba(99,179,237,0.3); }
  .gpus { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; margin-bottom: 24px; }
  .gpu-card { background: #1a202c; border-radius: 12px; padding: 16px; border: 1px solid #2d3748; }
  .gpu-card h3 { font-size: 12px; text-transform: uppercase; color: #718096; letter-spacing: 1px; margin-bottom: 8px; }
  .gpu-visual { height: 80px; background: #0f1117; border-radius: 8px; position: relative; overflow: hidden; margin: 8px 0; }
  .gpu-fill { height: 100%; transition: width 0.5s; position: absolute; left: 0; top: 0; }
  .gpu-fill.util { background: linear-gradient(90deg, #2563eb44, #2563eb88); }
  .gpu-fill.mem { background: linear-gradient(90deg, #48bb7844, #48bb7888); top: 50%; height: 50%; }
  .gpu-label { position: absolute; padding: 4px 8px; font-size: 11px; color: #e2e8f0; z-index: 1; }
  .gpu-label.top { top: 8px; left: 8px; }
  .gpu-label.bot { bottom: 8px; left: 8px; }
  .gpu-temp { position: absolute; top: 8px; right: 8px; font-size: 18px; font-weight: 600; z-index: 1; }
  .gpu-jobs { margin-top: 8px; font-size: 11px; color: #a0aec0; }
  .gpu-jobs span { background: #2d3748; padding: 2px 8px; border-radius: 4px; margin-right: 4px; }
  .legend { display: flex; gap: 16px; justify-content: center; margin: 16px 0; font-size: 11px; color: #718096; }
  .legend-item { display: flex; align-items: center; gap: 4px; }
  .legend-dot { width: 12px; height: 12px; border-radius: 3px; }
  .refresh-bar { text-align: center; color: #4a5568; font-size: 10px; padding: 4px; }
</style>
</head>
<body>

<div class="header">
  <h1><span>ATLAS</span> Resource Map</h1>
  <div>
    <a href="/">Dashboard</a>
    <a href="http://100.68.134.21:19999" target="_blank">Netdata</a>
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

  <div class="sockets">
    <div class="socket">
      <h3>Socket 0 — Xeon E5-2690 v3 <span class="temp" id="temp0">--°C</span></h3>
      <div class="core-grid" id="socket0"></div>
    </div>
    <div class="socket">
      <h3>Socket 1 — Xeon E5-2690 v3 <span class="temp" id="temp1">--°C</span></h3>
      <div class="core-grid" id="socket1"></div>
    </div>
  </div>

  <div class="gpus">
    <div class="gpu-card">
      <h3>GPU 0 — Quadro GV100 32GB</h3>
      <div class="gpu-visual" id="gpu0-visual">
        <div class="gpu-fill util" id="gpu0-util"></div>
        <div class="gpu-fill mem" id="gpu0-mem"></div>
        <span class="gpu-label top" id="gpu0-util-label">--% util</span>
        <span class="gpu-label bot" id="gpu0-mem-label">-- MB</span>
        <span class="gpu-temp" id="gpu0-temp">--°C</span>
      </div>
      <div class="gpu-jobs" id="gpu0-jobs"></div>
    </div>
    <div class="gpu-card">
      <h3>GPU 1 — Quadro GV100 32GB</h3>
      <div class="gpu-visual" id="gpu1-visual">
        <div class="gpu-fill util" id="gpu1-util"></div>
        <div class="gpu-fill mem" id="gpu1-mem"></div>
        <span class="gpu-label top" id="gpu1-util-label">--% util</span>
        <span class="gpu-label bot" id="gpu1-mem-label">-- MB</span>
        <span class="gpu-temp" id="gpu1-temp">--°C</span>
      </div>
      <div class="gpu-jobs" id="gpu1-jobs"></div>
    </div>
  </div>

  <div class="refresh-bar">Live — refreshes every 5 seconds</div>
</div>

<script>
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
      el.textContent = temp + '°C';
      el.style.color = tempColor(temp);
    }

    // Cores: 0-11 = socket 0, 12-23 = socket 1 (physical)
    // 24-35 = socket 0 HT, 36-47 = socket 1 HT
    for (let socket = 0; socket < 2; socket++) {
      const el = document.getElementById('socket' + socket);
      let html = '';
      for (let i = 0; i < 12; i++) {
        const coreId = socket * 12 + i;
        const c = coreData[coreId] || {util: 0, job: null};
        const htId = coreId + 24;
        const ht = coreData[htId] || {util: 0, job: null};
        const util = Math.max(c.util, ht.util);
        const job = c.job || ht.job;
        html += '<div class="' + coreClass(util, job) + '" title="Core ' + coreId + (job ? ' — ' + job : '') + '">';
        html += '<span class="id">' + coreId + '</span>';
        if (job) html += '<span class="job">' + job + '</span>';
        html += '</div>';
      }
      el.innerHTML = html;
    }

    // GPUs
    for (const gpu of statusData.gpus) {
      const i = gpu.index;
      document.getElementById('gpu'+i+'-util').style.width = gpu.util + '%';
      document.getElementById('gpu'+i+'-mem').style.width = Math.round(100*gpu.mem_used/gpu.mem_total) + '%';
      document.getElementById('gpu'+i+'-util-label').textContent = gpu.util + '% utilization';
      document.getElementById('gpu'+i+'-mem-label').textContent = gpu.mem_used + '/' + gpu.mem_total + ' MB';
      const tempEl = document.getElementById('gpu'+i+'-temp');
      tempEl.textContent = gpu.temp + '°C';
      tempEl.style.color = tempColor(gpu.temp);
    }

    // GPU jobs (from sessions)
    const sessions = statusData.sessions || [];
    // Simple heuristic: even sessions -> GPU 0, odd -> GPU 1
    const gpu0jobs = [], gpu1jobs = [];
    sessions.forEach((s, idx) => {
      if (idx % 2 === 0) gpu0jobs.push(s.name);
      else gpu1jobs.push(s.name);
    });

  } catch(e) {
    console.error(e);
  }
}

refresh();
setInterval(refresh, 5000);
</script>
</body>
</html>
"""

def discover_pipelines():
    """Auto-discover running pipelines by scanning processes, files, and logs.

    Returns a list of pipeline objects, each with stages and edges.
    Works for ANY pipeline — not hardcoded to Theory Radar.
    """
    pipelines = []

    # 1. Discover tmux sessions as pipeline containers
    try:
        out = subprocess.check_output(
            ["tmux", "list-sessions", "-F", "#{session_name}:#{session_activity}"],
            text=True, timeout=5
        )
        sessions = {}
        for line in out.strip().split("\n"):
            if ":" in line:
                name, activity = line.split(":", 1)
                sessions[name] = {"activity": activity}
    except Exception:
        sessions = {}

    # 2. Scan processes — build parent-child tree and extract metadata
    processes = {}
    try:
        out = subprocess.check_output(
            ["ps", "-eo", "pid,ppid,psr,%cpu,%mem,etimes,comm,args", "--no-headers"],
            text=True, timeout=5
        )
        for line in out.strip().split("\n"):
            parts = line.split(None, 7)
            if len(parts) >= 8:
                pid = int(parts[0])
                processes[pid] = {
                    "pid": pid,
                    "ppid": int(parts[1]),
                    "core": int(parts[2]),
                    "cpu": float(parts[3]),
                    "mem": float(parts[4]),
                    "elapsed": int(parts[5]),
                    "comm": parts[6],
                    "args": parts[7],
                }
    except Exception:
        pass

    # 3. Identify pipeline processes (python, llama-cli, etc.)
    pipeline_procs = {}
    for pid, p in processes.items():
        if p["cpu"] < 1:
            continue
        args = p["args"]

        # Detect stage from command line
        stage = None
        pipeline_name = None

        # Detect GPU from CUDA_VISIBLE_DEVICES in /proc/pid/environ
        gpu_id = None
        try:
            with open(f"/proc/{pid}/environ", "rb") as ef:
                env = ef.read().decode("utf-8", errors="ignore")
                for part in env.split("\0"):
                    if part.startswith("CUDA_VISIBLE_DEVICES="):
                        gpu_id = part.split("=")[1]
                        break
        except Exception:
            pass

        if "run_one.py" in args:
            parts = args.split("run_one.py", 1)
            if len(parts) > 1:
                ds = parts[1].strip().split()[0] if parts[1].strip() else "?"
                pipeline_name = f"theory-radar/{ds}"
                stage = _detect_stage(ds.lower())
            else:
                pipeline_name = "theory-radar"
                stage = "running"

        elif "run_qwen" in args or "qwen" in args.lower():
            pipeline_name = "arc-agi-2/qwen"
            stage = _detect_qwen_stage()

        elif "finetune" in args:
            pipeline_name = "arc-agi-2/training"
            stage = "training"

        elif "llama-cli" in args or "llama" in p["comm"]:
            pipeline_name = "glm-5/inference"
            stage = "inference"

        elif "flask" in args or "app.py" in args:
            pipeline_name = "atlas-portal"
            stage = "serving"

        elif "netdata" in p["comm"]:
            pipeline_name = "monitoring/netdata"
            stage = "collecting"

        if pipeline_name:
            if pipeline_name not in pipeline_procs:
                pipeline_procs[pipeline_name] = []
            pipeline_procs[pipeline_name].append({
                "pid": pid,
                "stage": stage,
                "cpu": p["cpu"],
                "mem": p["mem"],
                "core": p["core"],
                "elapsed": p["elapsed"],
                "gpu": gpu_id,
            })

    # 4. Build pipeline objects with stages
    for name, procs in pipeline_procs.items():
        stages = []
        for p in procs:
            stage_str = p["stage"] or "running|0"
            if "|" in stage_str:
                label, pct = stage_str.rsplit("|", 1)
                try:
                    pct = int(pct)
                except ValueError:
                    pct = 0
            else:
                label, pct = stage_str, 0

            stages.append({
                "id": f"pid-{p['pid']}",
                "label": label,
                "status": "running",
                "progress": pct,
                "cpu": p["cpu"],
                "mem": p["mem"],
                "core": p["core"],
                "elapsed": p["elapsed"],
                "gpu": p.get("gpu"),
            })

        # Add completed results as "done" stages
        if name.startswith("theory-radar/"):
            ds = name.split("/")[1].lower()
            result_file = f"/home/claude/tensor-3body/result_{ds}.json"
            if os.path.exists(result_file):
                try:
                    with open(result_file) as f:
                        r = json.load(f)
                    stages.append({
                        "id": f"result-{ds}",
                        "label": f"F1={r.get('test_f1', 0):.3f}",
                        "status": "complete",
                        "formula": r.get("formula", "?")[:30],
                    })
                except Exception:
                    pass

        pipelines.append({
            "name": name,
            "stages": stages,
            "process_count": len(procs),
        })

    # 5. Add completed-only pipelines (no running process)
    for jf in glob.glob("/home/claude/tensor-3body/result_*.json"):
        ds = jf.split("result_")[1].split(".json")[0]
        pname = f"theory-radar/{ds}"
        if pname not in pipeline_procs:
            try:
                with open(jf) as f:
                    r = json.load(f)
                bl = r.get("baselines", {})
                pipelines.append({
                    "name": pname,
                    "stages": [{
                        "id": f"result-{ds}",
                        "label": f"F1={r.get('test_f1', 0):.3f}",
                        "status": "complete",
                        "formula": r.get("formula", "?")[:40],
                        "test_f1": r.get("test_f1", 0),
                        "train_f1": r.get("train_f1", 0),
                        "N": r.get("N", 0),
                        "d": r.get("d", 0),
                        "vs_gb": bl.get("GB", {}).get("dir", "?"),
                        "gb_sigma": bl.get("GB", {}).get("sigma", 0),
                        "vs_rf": bl.get("RF", {}).get("dir", "?"),
                        "rf_sigma": bl.get("RF", {}).get("sigma", 0),
                        "download": ds,
                    }],
                    "process_count": 0,
                })
            except Exception:
                pass

    return sorted(pipelines, key=lambda p: p["name"])


def _detect_stage(ds_name):
    """Detect current stage and progress of a Theory Radar run from its log."""
    import re
    log_path = f"/home/claude/tensor-3body/result_{ds_name}.log"
    try:
        with open(log_path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 500))
            tail = f.read().decode("utf-8", errors="ignore")

        if "Done:" in tail:
            return "complete|100"
        if "WINNER" in tail and "train=" in tail:
            m = re.search(r"(\d+)/(\d+)\s+train", tail)
            if m:
                pct = int(100 * int(m.group(1)) / int(m.group(2)))
                return f"CV {m.group(1)}/{m.group(2)}|{pct}"
            return "cv-eval|50"
        if "autotune" in tail.lower() or "configs" in tail:
            # Try to detect autotune progress
            m = re.search(r"\[([^\]]+)\].*F1=", tail)
            if m:
                return f"autotune ({m.group(1)})|25"
            return "autotune|15"
        if "train=" in tail and "test=" in tail:
            m = re.search(r"(\d+)/(\d+)\s+train", tail)
            if m:
                pct = int(100 * int(m.group(1)) / int(m.group(2)))
                return f"CV {m.group(1)}/{m.group(2)}|{pct}"
            return "cv-eval|50"
        if "FULL PIPELINE" in tail:
            return "starting|5"
        return "running|0"
    except Exception:
        return "unknown|0"


def _detect_qwen_stage():
    """Detect Qwen eval progress from its log."""
    import re
    try:
        with open("/home/claude/arc-agi-2/qwen_eval.log", "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 500))
            tail = f.read().decode("utf-8", errors="ignore")

        if "RESULTS:" in tail:
            m = re.search(r"(\d+)/(\d+) correct", tail)
            if m:
                return f"done: {m.group(1)}/{m.group(2)} correct|100"
            return "complete|100"

        m = re.search(r"Progress: (\d+)/(\d+) correct=(\d+)", tail)
        if m:
            pct = int(100 * int(m.group(1)) / int(m.group(2)))
            return f"eval {m.group(1)}/{m.group(2)} ({m.group(3)} correct)|{pct}"

        if "Model loaded" in tail:
            return "model loaded|5"

        return "running|0"
    except Exception:
        return "unknown|0"


@app.route("/api/pipelines")
@auth_required
def api_pipelines():
    return jsonify(discover_pipelines())


FLOW_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Atlas — Live Pipeline Flow</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: 'Segoe UI', system-ui, sans-serif; background: #0f1117; color: #e2e8f0; }
  .header { background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%); padding: 16px 24px; border-bottom: 1px solid #2d3748; display: flex; justify-content: space-between; align-items: center; }
  .header h1 { font-size: 22px; font-weight: 300; letter-spacing: 2px; }
  .header h1 span { color: #63b3ed; font-weight: 600; }
  .header-nav a { color: #63b3ed; text-decoration: none; font-size: 13px; padding: 4px 12px; border: 1px solid #4a5568; border-radius: 6px; margin-left: 8px; }
  .container { max-width: 1400px; margin: 10px auto; padding: 0 16px; }
  .pipeline-row { border-radius: 6px; padding: 6px 10px; margin-bottom: 4px; display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
  .pipeline-row.active { background: #1a2035; border: 1px solid #2b6cb0; border-left: 3px solid #63b3ed; }
  .pipeline-row.completed { background: #1a2c1a; border: 1px solid #276749; border-left: 3px solid #48bb78; }
  .pipeline-row.failed { background: #2c1a1a; border: 1px solid #c53030; border-left: 3px solid #fc8181; }
  .pipeline-name { font-size: 12px; font-weight: 600; min-width: 140px; }
  .pipeline-row.active .pipeline-name { color: #63b3ed; }
  .pipeline-row.completed .pipeline-name { color: #48bb78; }
  .pipeline-row.failed .pipeline-name { color: #fc8181; }
  .pipeline-name .count { color: #718096; font-weight: 400; font-size: 10px; margin-left: 4px; }
  .result-bar { display: flex; align-items: center; gap: 8px; font-size: 11px; }
  .result-bar .f1 { font-size: 14px; font-weight: 700; }
  .result-bar .f1.good { color: #48bb78; }
  .result-bar .f1.ok { color: #ecc94b; }
  .result-bar .f1.poor { color: #fc8181; }
  .result-bar .formula { font-family: 'Consolas', monospace; color: #718096; font-size: 10px; max-width: 200px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .result-bar .badge { padding: 1px 6px; border-radius: 3px; font-size: 9px; font-weight: 600; }
  .result-bar .badge.win { background: #276749; color: #9ae6b4; }
  .result-bar .badge.loss { background: #742a2a; color: #feb2b2; }
  .dl-link { color: #63b3ed; text-decoration: none; font-size: 10px; padding: 1px 5px; border: 1px solid #4a5568; border-radius: 3px; }
  .dl-link:hover { background: #2d3748; }
  .stages { display: flex; align-items: center; gap: 3px; flex-wrap: nowrap; }
  .stage { padding: 3px 8px; border-radius: 4px; font-size: 10px; display: inline-flex; flex-direction: column; align-items: center; min-width: 60px; }
  .stage .label { font-weight: 600; font-size: 9px; }
  .stage .detail { font-size: 8px; color: #a0aec0; margin-top: 1px; }
  .stage.running { background: #1c3a5e; border: 1px solid #2b6cb0; animation: pulse 2s infinite; position: relative; overflow: hidden; }
  .stage.complete { background: #1c3a2a; border: 1px solid #276749; }
  .stage.autotune { background: #3a2e1c; border: 1px solid #975a16; animation: pulse 3s infinite; position: relative; overflow: hidden; }
  .stage.unknown { background: #2d3748; border: 1px solid #4a5568; }
  .progress-fill { position: absolute; bottom: 0; left: 0; height: 3px; background: #63b3ed; transition: width 1s; border-radius: 0 0 8px 8px; }
  .gpu-tag { display: inline-block; font-size: 9px; padding: 1px 5px; border-radius: 3px; margin-left: 4px; }
  .gpu-tag.g0 { background: #2b6cb0; color: #bee3f8; }
  .gpu-tag.g1 { background: #975a16; color: #fefcbf; }
  .arrow { color: #4a5568; font-size: 12px; }
  .formula { font-family: 'Consolas', monospace; font-size: 10px; color: #a0aec0; }
  @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.7; } }
  .section-title { font-size: 12px; text-transform: uppercase; color: #718096; letter-spacing: 1px; margin: 20px 0 10px; }
  .summary { display: flex; gap: 12px; margin-bottom: 12px; }
  .summary-card { background: #1a202c; border-radius: 6px; padding: 6px 14px; border: 1px solid #2d3748; text-align: center; }
  .summary-card .num { font-size: 20px; font-weight: 600; color: #63b3ed; }
  .summary-card .lbl { font-size: 10px; color: #718096; }
  .section-title { font-size: 10px; text-transform: uppercase; color: #718096; letter-spacing: 1px; margin: 10px 0 4px; }
  .refresh-note { text-align: center; color: #4a5568; font-size: 9px; padding: 4px; }
</style>
</head>
<body>

<div class="header">
  <h1><span>ATLAS</span> Pipeline Flow</h1>
  <div class="header-nav">
    <a href="/">Dashboard</a>
    <a href="/map">Resource Map</a>
    <a href="/flow">Pipeline Flow</a>
    <a href="http://100.68.134.21:19999" target="_blank">Netdata</a>
  </div>
</div>

<div class="container">
  <div class="summary" id="summary"></div>
  <div class="section-title">Active Pipelines</div>
  <div id="active-pipelines"></div>
  <div class="section-title" style="margin-top: 24px;">Completed</div>
  <div id="completed-pipelines"></div>
  <div class="refresh-note">Auto-discovers pipelines from running processes — refreshes every 5s</div>
</div>

<script>
function stageClass(stage) {
  if (!stage) return 'unknown';
  const s = stage.label || '';
  if (s === 'complete' || stage.status === 'complete') return 'complete';
  if (s.includes('autotune') || s === 'autotune') return 'autotune';
  if (stage.status === 'running') return 'running';
  return 'unknown';
}

function renderPipeline(p) {
  const isActive = p.process_count > 0;
  const result = p.stages.find(s => s.status === 'complete' && s.test_f1);
  const rowClass = isActive ? 'active' : (result ? 'completed' : 'completed');

  let html = '<div class="pipeline-row ' + rowClass + '">';
  html += '<div class="pipeline-name">' + p.name;
  if (isActive) html += '<span class="count">' + p.process_count + ' processes</span>';
  html += '</div>';
  html += '<div class="stages">';

  // Show pipeline template stages based on name
  if (p.name.startsWith('theory-radar/')) {
    const stages = ['autotune', 'project', 'search', 'evaluate'];
    const currentStage = p.stages.find(s => s.status === 'running');
    const result = p.stages.find(s => s.status === 'complete');

    stages.forEach((template, i) => {
      let cls = 'stage unknown';
      let label = template;
      let detail = '';

      if (currentStage) {
        const cl = currentStage.label || '';
        if (template === 'autotune' && (cl.includes('autotune') || cl === 'starting')) {
          cls = 'stage autotune';
          detail = 'searching configs...';
        } else if (template === 'search' && cl.includes('CV')) {
          cls = 'stage running';
          label = cl;
          detail = 'core ' + currentStage.core + (currentStage.gpu != null ? ' · GPU ' + currentStage.gpu : '') + ' · ' + currentStage.cpu + '% CPU';
        } else if (template === 'evaluate' && result) {
          cls = 'stage complete';
          label = result.label;
          detail = result.formula || '';
        }
        // Mark earlier stages as done if we're past them
        const stageOrder = {'autotune':0, 'project':1, 'search':2, 'evaluate':3};
        const currentOrder = cl.includes('CV') ? 2 : cl.includes('autotune') ? 0 : cl === 'complete' ? 3 : 1;
        if (stageOrder[template] < currentOrder) {
          cls = 'stage complete';
        }
      } else if (result) {
        cls = 'stage complete';
        if (template === 'evaluate') {
          label = result.label;
          detail = result.formula || '';
        }
      }

      html += '<div class="' + cls + '">';
      html += '<span class="label">' + label + '</span>';
      if (detail) html += '<span class="detail">' + detail + '</span>';
      html += '</div>';
      if (i < stages.length - 1) html += '<span class="arrow">→</span>';
    });

  } else {
    // Generic pipeline: show stages with GPU + progress
    p.stages.forEach((s, i) => {
      const cls = 'stage ' + stageClass(s);
      html += '<div class="' + cls + '">';
      html += '<span class="label">' + (s.label || 'running') + '</span>';
      if (s.gpu != null) html += '<span class="gpu-tag g' + s.gpu + '">GPU ' + s.gpu + '</span>';
      if (s.cpu) html += '<span class="detail">core ' + (s.core||'?') + ' · ' + s.cpu + '% CPU</span>';
      if (s.progress > 0) html += '<div class="progress-fill" style="width:' + s.progress + '%"></div>';
      if (s.formula) html += '<span class="detail formula">' + s.formula + '</span>';
      html += '</div>';
      if (i < p.stages.length - 1) html += '<span class="arrow">→</span>';
    });
  }

  html += '</div>';

  // Result details for completed pipelines
  if (result && result.test_f1) {
    const f1 = result.test_f1;
    const f1Class = f1 > 0.9 ? 'good' : f1 > 0.7 ? 'ok' : 'poor';
    const beatsGB = result.vs_gb === 'A*>';
    const badge = beatsGB
      ? '<span class="badge win">Beats GB ' + result.gb_sigma.toFixed(1) + 'σ</span>'
      : '<span class="badge loss">GB wins ' + result.gb_sigma.toFixed(1) + 'σ</span>';

    html += '<div class="result-bar">';
    html += '<span class="f1 ' + f1Class + '">' + f1.toFixed(4) + '</span>';
    html += badge;
    html += '<span class="formula">' + (result.formula || '') + '</span>';
    if (result.N) html += '<span style="color:#718096;font-size:10px">N=' + result.N + ' d=' + result.d + '</span>';
    if (result.download) html += '<a class="dl-link" href="/api/download/' + result.download + '">↓ JSON</a>';
    html += '</div>';
  }

  html += '</div>';
  return html;
}

async function refresh() {
  try {
    const pipelines = await (await fetch('/api/pipelines')).json();

    const active = pipelines.filter(p => p.process_count > 0);
    const completed = pipelines.filter(p => p.process_count === 0);

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

@app.route("/api/download/<dataset>")
@auth_required
def download_result(dataset):
    """Download a result JSON file."""
    import re
    dataset = re.sub(r'[^a-zA-Z0-9_-]', '', dataset)  # sanitize
    path = f"/home/claude/tensor-3body/result_{dataset}.json"
    if os.path.exists(path):
        with open(path) as f:
            return Response(f.read(), mimetype="application/json",
                          headers={"Content-Disposition": f"attachment; filename=result_{dataset}.json"})
    return "Not found", 404


@app.route("/flow")
@auth_required
def flow():
    return render_template_string(FLOW_TEMPLATE)


@app.route("/map")
@auth_required
def resource_map():
    return render_template_string(MAP_TEMPLATE)


@app.route("/")
@auth_required
def index():
    return render_template_string(TEMPLATE)


if __name__ == "__main__":
    import ssl
    script_dir = os.path.dirname(os.path.abspath(__file__))
    cert_path = os.path.join(script_dir, "cert.pem")
    key_path = os.path.join(script_dir, "key.pem")

    if os.path.exists(cert_path) and os.path.exists(key_path):
        # HTTPS with self-signed cert
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert_path, key_path)
        print("Starting with HTTPS on port 8443")
        app.run(host="0.0.0.0", port=8443, ssl_context=context, debug=False)
    else:
        # Fallback to HTTP
        print("No certs found, starting HTTP on port 8080")
        app.run(host="0.0.0.0", port=8080, debug=False)
