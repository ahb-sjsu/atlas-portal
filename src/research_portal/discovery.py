"""System discovery -- dynamic hardware detection and pipeline scanning.

Every function works without hardcoded specs.  Information is read from
``/proc``, ``nvidia-smi``, ``sensors``, or equivalent system calls so the
portal adapts to whatever machine it runs on.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import threading
import time

# ---------------------------------------------------------------------------
# Hardware / OS discovery
# ---------------------------------------------------------------------------


def get_system_info() -> dict:
    """Auto-discover hostname, OS, CPU model, GPU models, cores, and RAM.

    Returns a dict with the following keys (all best-effort):
      hostname, os, kernel, cpu_model, cpu_cores, cpu_threads,
      gpu_models (list[str]), total_ram_gb
    """
    info: dict = {
        "hostname": platform.node() or "unknown",
        "os": f"{platform.system()} {platform.release()}",
        "kernel": platform.version(),
        "cpu_model": _detect_cpu_model(),
        "cpu_cores": os.cpu_count() or 0,
        "cpu_threads": os.cpu_count() or 0,
        "gpu_models": _detect_gpu_models(),
        "total_ram_gb": _detect_total_ram_gb(),
    }
    # Try to get physical core count separately on Linux.
    phys = _detect_physical_cores()
    if phys:
        info["cpu_cores"] = phys
    return info


def _detect_cpu_model() -> str:
    """Read CPU model string from /proc/cpuinfo (Linux) or platform."""
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or "unknown"


def _detect_physical_cores() -> int | None:
    """Return the number of *physical* cores via /proc/cpuinfo."""
    try:
        with open("/proc/cpuinfo") as f:
            seen: set[tuple[str, str]] = set()
            phys_id = core_id = ""
            for line in f:
                if line.startswith("physical id"):
                    phys_id = line.split(":", 1)[1].strip()
                elif line.startswith("core id"):
                    core_id = line.split(":", 1)[1].strip()
                    seen.add((phys_id, core_id))
            return len(seen) if seen else None
    except OSError:
        return None


def _detect_gpu_models() -> list[str]:
    """Detect GPU model names via nvidia-smi, or AMD/Intel fallbacks."""
    # NVIDIA ---
    if shutil.which("nvidia-smi"):
        try:
            out = subprocess.check_output(
                ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                text=True,
                timeout=5,
            )
            models = [line.strip() for line in out.strip().splitlines() if line.strip()]
            if models:
                return models
        except Exception:
            pass

    # AMD (rocm-smi) ---
    if shutil.which("rocm-smi"):
        try:
            out = subprocess.check_output(["rocm-smi", "--showproductname"], text=True, timeout=5)
            models = []
            for line in out.splitlines():
                if "GPU" in line and ":" in line:
                    models.append(line.split(":", 1)[1].strip())
            if models:
                return models
        except Exception:
            pass

    # Intel (lspci fallback) ---
    if shutil.which("lspci"):
        try:
            out = subprocess.check_output(["lspci"], text=True, timeout=5)
            models = []
            for line in out.splitlines():
                low = line.lower()
                if "vga" in low or "3d controller" in low or "display" in low:
                    models.append(line.split(":", 2)[-1].strip())
            if models:
                return models
        except Exception:
            pass

    return []


def _detect_total_ram_gb() -> float:
    """Total system RAM in GiB."""
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal"):
                    kb = int(line.split()[1])
                    return round(kb / 1048576, 1)
    except OSError:
        pass
    return 0.0


# ---------------------------------------------------------------------------
# Live metrics
# ---------------------------------------------------------------------------


def get_cpu_temps() -> dict[str, float]:
    """Read CPU package temperatures from ``sensors``."""
    try:
        out = subprocess.check_output(["sensors"], text=True, timeout=5)
        temps: dict[str, float] = {}
        for line in out.splitlines():
            if "Package" in line:
                parts = line.split("+")
                if len(parts) > 1:
                    temp = float(parts[1].split("\u00b0")[0])
                    pkg = line.split(":")[0].strip()
                    temps[pkg] = temp
        return temps
    except Exception:
        return {}


def get_gpu_info() -> list[dict]:
    """Query nvidia-smi (or equivalent) for live GPU metrics."""
    # NVIDIA path
    if shutil.which("nvidia-smi"):
        try:
            out = subprocess.check_output(
                [
                    "nvidia-smi",
                    "--query-gpu=index,name,utilization.gpu,memory.used,memory.total,temperature.gpu",
                    "--format=csv,noheader,nounits",
                ],
                text=True,
                timeout=5,
            )
            gpus: list[dict] = []
            for line in out.strip().splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 6:
                    gpus.append(
                        {
                            "index": int(parts[0]),
                            "name": parts[1],
                            "util": int(parts[2]),
                            "mem_used": int(parts[3]),
                            "mem_total": int(parts[4]),
                            "temp": int(parts[5]),
                        }
                    )
            return gpus
        except Exception:
            pass

    # AMD fallback (rocm-smi --json)
    if shutil.which("rocm-smi"):
        try:
            out = subprocess.check_output(
                ["rocm-smi", "--showuse", "--showmemuse", "--showtemp", "--json"],
                text=True,
                timeout=5,
            )
            data = json.loads(out)
            gpus = []
            for key, val in data.items():
                if key.startswith("card"):
                    gpus.append(
                        {
                            "index": len(gpus),
                            "name": key,
                            "util": int(val.get("GPU use (%)", 0)),
                            "mem_used": int(val.get("GPU memory use (%)", 0)),
                            "mem_total": 0,
                            "temp": int(float(val.get("Temperature (Sensor edge) (C)", 0))),
                        }
                    )
            return gpus
        except Exception:
            pass

    return []


def get_memory() -> dict:
    """System memory usage from ``/proc/meminfo``."""
    try:
        with open("/proc/meminfo") as f:
            info: dict[str, int] = {}
            for line in f:
                parts = line.split()
                info[parts[0].rstrip(":")] = int(parts[1])
        total = info.get("MemTotal", 0) // 1024
        avail = info.get("MemAvailable", 0) // 1024
        return {"total_mb": total, "available_mb": avail, "used_mb": total - avail}
    except Exception:
        return {}


def get_load() -> dict:
    """1/5/15 minute load averages."""
    try:
        load1, load5, load15 = os.getloadavg()
        return {"1min": round(load1, 1), "5min": round(load5, 1), "15min": round(load15, 1)}
    except Exception:
        return {}


def get_disk() -> dict:
    """Root filesystem usage."""
    try:
        st = os.statvfs("/")
        total = st.f_blocks * st.f_frsize // (1024**3)
        free = st.f_bavail * st.f_frsize // (1024**3)
        return {"total_gb": total, "free_gb": free, "used_gb": total - free}
    except Exception:
        return {}


def get_tmux_sessions() -> list[dict]:
    """List active tmux sessions."""
    try:
        out = subprocess.check_output(
            ["tmux", "list-sessions", "-F", "#{session_name}:#{session_created}"],
            text=True,
            timeout=5,
        )
        sessions: list[dict] = []
        for line in out.strip().splitlines():
            if ":" in line:
                name, created = line.split(":", 1)
                sessions.append({"name": name, "created": created})
        return sessions
    except Exception:
        return []


def get_per_core() -> dict:
    """Per-core utilisation and which process is running on each core."""
    cores: dict = {}
    try:
        with open("/proc/stat") as f:
            for line in f:
                if line.startswith("cpu") and line[3] != " ":
                    parts = line.split()
                    core_id = int(parts[0][3:])
                    user, nice, system, idle = (
                        int(parts[1]),
                        int(parts[2]),
                        int(parts[3]),
                        int(parts[4]),
                    )
                    total = user + nice + system + idle
                    busy = user + nice + system
                    cores[core_id] = {"util": round(100 * busy / max(total, 1)), "job": None}
    except Exception:
        pass

    # Map processes to cores
    try:
        out = subprocess.check_output(
            ["ps", "-eo", "pid,psr,comm,args", "--no-headers"],
            text=True,
            timeout=5,
        )
        for line in out.strip().splitlines():
            parts = line.split(None, 3)
            if len(parts) >= 4:
                _pid, psr_s, _comm, args = parts[0], parts[1], parts[2], parts[3]
                psr = int(psr_s)
                # Detect pipeline-like processes generically
                name = _detect_process_label(args)
                if name and psr in cores:
                    cores[psr]["job"] = name
    except Exception:
        pass

    return cores


def _read_process_log(pid: int) -> dict | None:
    """Read a process's log output to learn what it's doing.

    Discovers log files via /proc/pid/fd (finds open .log files or
    stdout redirections), then parses the tail for dataset names,
    fold progress, and current results.
    """
    # Strategy 1: check /proc/pid/fd for open .log files
    log_path = None
    try:
        fd_dir = f"/proc/{pid}/fd"
        for fd in os.listdir(fd_dir):
            try:
                link = os.readlink(os.path.join(fd_dir, fd))
                if link.endswith(".log") and "/tmp/" in link:
                    log_path = link
                    break
            except OSError:
                continue
    except OSError:
        pass

    # Strategy 2: check parent/sibling processes (tee piping)
    if not log_path:
        try:
            with open(f"/proc/{pid}/stat") as f:
                ppid = int(f.read().split()[3])
            # Check siblings: children of our parent
            for sibling_fd_dir in [f"/proc/{ppid}/fd"]:
                try:
                    for fd in os.listdir(sibling_fd_dir):
                        try:
                            link = os.readlink(os.path.join(sibling_fd_dir, fd))
                            if link.endswith(".log") and "/tmp/" in link:
                                log_path = link
                                break
                        except OSError:
                            continue
                except OSError:
                    pass
        except (OSError, ValueError, IndexError):
            pass

    # Strategy 3: match script name to /tmp/*.log convention
    if not log_path:
        try:
            cmdline = open(f"/proc/{pid}/cmdline", "rb").read().decode("utf-8", "ignore")
            # Extract script name from cmdline
            for part in cmdline.split("\0"):
                if part.endswith(".py"):
                    base = os.path.basename(part).removesuffix(".py")
                    # Check common log locations
                    for candidate in [
                        f"/tmp/{base}.log",
                        f"/tmp/{base}_gpu1.log",
                        "/tmp/pilot_d7.log",
                        "/tmp/pilot_d7_gpu1.log",
                    ]:
                        if os.path.exists(candidate):
                            log_path = candidate
                            break
                    break
        except OSError:
            pass

    if not log_path:
        return None

    # Read the tail of the log
    try:
        with open(log_path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 2000))
            tail = f.read().decode("utf-8", errors="ignore")
    except OSError:
        return None

    result: dict = {}

    # Parse dataset name: "=== DatasetName: N=... ==="
    dataset_matches = re.findall(r"===\s+(\w[\w\-]+):\s+N=(\d+)\s+d=(\d+)", tail)
    if dataset_matches:
        last = dataset_matches[-1]
        result["dataset"] = last[0]
        result["N"] = int(last[1])
        result["d"] = int(last[2])

    # Parse fold progress: "fold 25: deep=0.670 rf=0.829"
    fold_matches = re.findall(r"fold\s+(\d+):\s+(.*)", tail)
    if fold_matches:
        last_fold = fold_matches[-1]
        fold_num = int(last_fold[0])
        # Assume 100 folds (20x5 CV)
        total_folds = 100
        pct = min(99, int(100 * fold_num / total_folds))
        metrics = last_fold[1].strip()
        result["stage_label"] = f"fold {fold_num}/{total_folds}|{pct}"
        result["detail"] = metrics

    # Parse depth progress: "d7: best=0.8998 K=1968 beam=3"
    depth_matches = re.findall(r"d(\d+):\s+best=([\d.]+)", tail)
    if depth_matches and not fold_matches:
        last_depth = depth_matches[-1]
        result["detail"] = f"depth {last_depth[0]} best={last_depth[1]}"

    # Parse DONE lines: "DONE Spambase: deep(d7)=0.8700 rf=0.9100 sigma=-42.2"
    done_matches = re.findall(r"DONE\s+(\w+):\s+(.*)", tail)
    if done_matches:
        last_done = done_matches[-1]
        result["detail"] = last_done[1].strip()

    return result if result else None


def _detect_process_label(args: str) -> str | None:
    """Return a short human label for a pipeline-like process, or None."""
    # Python scripts
    m = re.search(r"python[23]?\s+\S*?([^/\\]+\.py)", args)
    if m:
        return m.group(1)
    # Bash scripts
    m = re.search(r"bash\s+\S*?([^/\\]+\.sh)", args)
    if m:
        return m.group(1)
    # llama.cpp / LLM inference
    if "llama-cli" in args or "llama-server" in args:
        return "llama"
    return None


# ---------------------------------------------------------------------------
# Pipeline discovery
# ---------------------------------------------------------------------------


def discover_pipelines() -> list[dict]:
    """Auto-discover running pipelines by scanning processes.

    Returns a list of pipeline dicts, each with ``name``, ``stages``, and
    ``process_count``.  Works for ANY pipeline -- detection is pattern-based
    on process command lines, not hardcoded to specific projects.
    """
    pipelines: list[dict] = []

    # 1. Discover tmux sessions as pipeline containers
    try:
        out = subprocess.check_output(
            ["tmux", "list-sessions", "-F", "#{session_name}:#{session_activity}"],
            text=True,
            timeout=5,
        )
        sessions: dict[str, dict] = {}
        for line in out.strip().splitlines():
            if ":" in line:
                name, activity = line.split(":", 1)
                sessions[name] = {"activity": activity}
    except Exception:
        sessions = {}

    # 2. Scan processes -- build metadata
    processes: dict[int, dict] = {}
    try:
        out = subprocess.check_output(
            ["ps", "-eo", "pid,ppid,psr,%cpu,%mem,etimes,comm,args", "--no-headers"],
            text=True,
            timeout=5,
        )
        for line in out.strip().splitlines():
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

    # 3. Identify pipeline processes
    pipeline_procs: dict[str, list[dict]] = {}
    for pid, p in processes.items():
        if p["cpu"] < 1:
            continue
        args = p["args"]
        stage: str | None = None
        pipeline_name: str | None = None

        # Detect GPU from CUDA_VISIBLE_DEVICES in /proc/pid/environ
        gpu_id: str | None = None
        try:
            with open(f"/proc/{pid}/environ", "rb") as ef:
                env = ef.read().decode("utf-8", errors="ignore")
                for part in env.split("\0"):
                    if part.startswith("CUDA_VISIBLE_DEVICES="):
                        gpu_id = part.split("=")[1]
                        break
        except Exception:
            pass

        # Generic Python script detection
        py_match = re.search(r"python[23]?\s+\S*?([^/\\]+\.py)", args)
        if py_match:
            script = py_match.group(1)
            pipeline_name = script.removesuffix(".py")
            stage = "running|0"

        # Generic bash script detection
        if not pipeline_name:
            sh_match = re.search(r"bash\s+\S*?([^/\\]+\.sh)", args)
            if sh_match:
                script = sh_match.group(1)
                pipeline_name = script.removesuffix(".sh")
                stage = "running|0"

        # LLM inference
        if not pipeline_name and ("llama-cli" in args or "llama" in p["comm"]):
            pipeline_name = "llm/inference"
            stage = "inference|0"

        # Flask / web servers
        if not pipeline_name and ("flask" in args or "gunicorn" in args or "uvicorn" in args):
            pipeline_name = "web/server"
            stage = "serving|0"

        # Monitoring daemons
        if not pipeline_name and "netdata" in p["comm"]:
            pipeline_name = "monitoring/netdata"
            stage = "collecting|0"

        if pipeline_name:
            # Enrich from log output: find what the process is actually doing
            detail = _read_process_log(pid)
            if detail:
                if detail.get("dataset"):
                    pipeline_name = f"{pipeline_name}/{detail['dataset']}"
                if detail.get("stage_label"):
                    stage = detail["stage_label"]

            pipeline_procs.setdefault(pipeline_name, []).append(
                {
                    "pid": pid,
                    "stage": stage,
                    "cpu": p["cpu"],
                    "mem": p["mem"],
                    "core": p["core"],
                    "elapsed": p["elapsed"],
                    "gpu": gpu_id,
                    "detail": detail.get("detail") if detail else None,
                }
            )

    # 4. Build pipeline objects with stages
    for name, procs in pipeline_procs.items():
        stages: list[dict] = []
        for p in procs:
            stage_str = p["stage"] or "running|0"
            if "|" in stage_str:
                label, pct_s = stage_str.rsplit("|", 1)
                try:
                    pct = int(pct_s)
                except ValueError:
                    pct = 0
            else:
                label, pct = stage_str, 0

            stages.append(
                {
                    "id": f"pid-{p['pid']}",
                    "label": label,
                    "status": "running",
                    "progress": pct,
                    "cpu": p["cpu"],
                    "mem": p["mem"],
                    "core": p["core"],
                    "elapsed": p["elapsed"],
                    "gpu": p.get("gpu"),
                }
            )

        pipelines.append(
            {
                "name": name,
                "stages": stages,
                "process_count": len(procs),
            }
        )

    return sorted(pipelines, key=lambda p: p["name"])


# ---------------------------------------------------------------------------
# Stage detection helpers (generic)
# ---------------------------------------------------------------------------


def _detect_stage_from_log(log_path: str) -> str:
    """Best-effort stage detection by reading the tail of a log file."""
    try:
        with open(log_path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 500))
            tail = f.read().decode("utf-8", errors="ignore")

        if "Done:" in tail or "Finished" in tail.lower():
            return "complete|100"

        m = re.search(r"(\d+)/(\d+)", tail)
        if m:
            pct = int(100 * int(m.group(1)) / max(int(m.group(2)), 1))
            return f"progress {m.group(1)}/{m.group(2)}|{pct}"

        return "running|0"
    except Exception:
        return "unknown|0"


# ---------------------------------------------------------------------------
# Pipeline history -- remembers completed pipelines across polls
# ---------------------------------------------------------------------------

_pipeline_lock = threading.Lock()
_pipeline_history: dict[str, dict] = {}  # name -> completed pipeline record
_MAX_HISTORY = 50


def discover_pipelines_with_history() -> list[dict]:
    """Like discover_pipelines() but remembers pipelines that have finished.

    Active pipelines are returned with ``process_count > 0``.
    Completed pipelines (previously seen, now gone) are returned with
    ``process_count = 0`` and ``status = "completed"``.

    Also scans ``PORTAL_RESULTS_DIR`` for ``result_*.json`` files to seed
    the completed list with jobs that finished before the portal started.
    """
    active = discover_pipelines()
    active_names = {p["name"] for p in active}
    now = time.time()

    with _pipeline_lock:
        # Seed from result files on first call (or when new files appear)
        _seed_from_result_files(now)

        # Mark previously-active pipelines that are no longer running
        for name in list(_pipeline_history):
            rec = _pipeline_history[name]
            if name in active_names and rec.get("status") == "completed":
                del _pipeline_history[name]

        # Track currently active ones so we know when they disappear
        for p in active:
            _pipeline_history[p["name"]] = {
                "last_seen": now,
                "status": "active",
                "pipeline": p,
            }

        # Detect newly completed: was active last poll, gone now
        for name, rec in list(_pipeline_history.items()):
            if rec["status"] == "active" and name not in active_names:
                rec["status"] = "completed"
                rec["completed_at"] = now
                for stage in rec["pipeline"]["stages"]:
                    stage["status"] = "complete"
                    stage["label"] = "complete"
                rec["pipeline"]["process_count"] = 0

        # Build completed list (most recent first), cap at _MAX_HISTORY
        completed = []
        for _name, rec in sorted(
            _pipeline_history.items(),
            key=lambda kv: kv[1].get("completed_at", 0),
            reverse=True,
        ):
            if rec["status"] == "completed":
                elapsed = int(now - rec.get("completed_at", now))
                p = rec["pipeline"].copy()
                p["completed_ago_s"] = elapsed
                completed.append(p)

        completed = completed[:_MAX_HISTORY]

    return active + completed


_seeded_files: set[str] = set()


def _seed_from_result_files(now: float) -> None:
    """Scan PORTAL_RESULTS_DIR for result_*.json and seed pipeline history."""
    results_dir = os.environ.get("PORTAL_RESULTS_DIR", ".")
    try:
        entries = os.listdir(results_dir)
    except OSError:
        return

    for fname in entries:
        if not fname.startswith("result_") or not fname.endswith(".json"):
            continue
        fpath = os.path.join(results_dir, fname)
        if fpath in _seeded_files:
            continue
        _seeded_files.add(fpath)

        try:
            mtime = os.path.getmtime(fpath)
            with open(fpath) as f:
                data = json.load(f)
        except Exception:
            continue

        dataset = data.get("name", fname.removeprefix("result_").removesuffix(".json"))
        pipeline_name = f"theory-radar/{dataset}"

        # Don't overwrite actively-tracked pipelines
        if pipeline_name in _pipeline_history:
            continue

        # Build a rich stage from the result data
        stage_label = "complete"
        detail_parts = []
        if "test_f1" in data:
            detail_parts.append(f"F1={data['test_f1']:.3f}")
        if "formula" in data:
            detail_parts.append(data["formula"])

        # Check if it beat gradient boosting
        baselines = data.get("baselines", {})
        gb = baselines.get("GB", {})
        if gb:
            sigma = gb.get("sigma", 0)
            direction = gb.get("dir", "")
            if "A*>" in direction:
                detail_parts.append(f"WINS vs GB ({sigma:.1f}σ)")
            else:
                detail_parts.append(f"loses to GB ({sigma:.1f}σ)")

        stages = [
            {
                "id": f"result-{dataset}",
                "label": stage_label,
                "status": "complete",
                "progress": 100,
                "cpu": 0,
                "mem": 0,
                "core": None,
                "elapsed": int(now - mtime),
                "gpu": None,
                "detail": " | ".join(detail_parts) if detail_parts else None,
            }
        ]

        _pipeline_history[pipeline_name] = {
            "last_seen": mtime,
            "status": "completed",
            "completed_at": mtime,
            "pipeline": {
                "name": pipeline_name,
                "stages": stages,
                "process_count": 0,
            },
        }
