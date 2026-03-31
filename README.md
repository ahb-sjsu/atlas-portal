# research-portal

[![PyPI version](https://img.shields.io/pypi/v/research-portal)](https://pypi.org/project/research-portal/)
[![CI](https://github.com/ahb-sjsu/atlas-portal/actions/workflows/ci.yml/badge.svg)](https://github.com/ahb-sjsu/atlas-portal/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](https://pypi.org/project/research-portal/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

Zero-config research workstation dashboard with automatic pipeline discovery.

Drop it on any Linux research machine and instantly get live GPU/CPU monitoring, running pipeline detection, and a resource map -- no configuration needed.

## Installation

```bash
pip install research-portal
```

Or install from source:

```bash
git clone https://github.com/ahb-sjsu/atlas-portal.git
cd atlas-portal
pip install -e ".[dev]"
```

## Quick Start

```bash
research-portal
```

That's it. Open the URL printed in the terminal (default: `http://0.0.0.0:8080`).

Default credentials: `atlas` / `atlas2026!research` (override with env vars or CLI flags).

## Features

- **Auto-detects hardware** -- hostname, CPU model, GPU models (NVIDIA/AMD/Intel), RAM, disk
- **Live system monitoring** -- CPU temperatures, GPU utilization/memory/thermals, RAM, disk, load averages
- **Pipeline discovery** -- automatically finds running Python/bash pipelines from process list
- **Log-based enrichment** -- reads pipeline log files to extract dataset names, fold progress, and metrics
- **Completed pipeline history** -- remembers finished pipelines; seeds from `result_*.json` files on disk
- **Dynamic Platform Guide** -- auto-generated hardware summary on the dashboard
- **Resource map** -- per-core CPU utilization grid with job labels, per-GPU visual bars
- **Pipeline flow** -- live view of all discovered pipelines with stage progress
- **Security** -- HTTP Basic auth, security headers (CSP, X-Frame-Options, etc.)
- **SSL support** -- explicit `--cert`/`--key` flags or auto-detects `cert.pem`/`key.pem`
- **Zero config** -- works on any Linux workstation with any number of CPUs and GPUs

## Pages

| Route | Description |
|-------|-------------|
| `/` | Main dashboard -- temperatures, GPU status, memory, Platform Guide, active sessions |
| `/map` | Resource map -- per-core CPU grid, per-GPU utilization visuals |
| `/flow` | Pipeline flow -- auto-discovered running/completed pipelines with metrics |

## How Pipeline Discovery Works

The portal finds your pipelines automatically. No config files, no agents, no registration. Here's how:

### 1. Process scanning

Every 5 seconds, the portal reads the system process table (`ps`) and identifies pipeline-like processes: Python scripts, bash scripts, LLM inference servers, and web servers. Any process using >1% CPU with a recognizable command line is picked up.

### 2. GPU detection

For each discovered process, the portal reads `/proc/<pid>/environ` to find `CUDA_VISIBLE_DEVICES`, so it knows which GPU each pipeline is using. This works for any number of GPUs.

### 3. Log-based enrichment

The portal looks for log files associated with each process. It checks:
- `/proc/<pid>/fd` for open `.log` files
- The parent process's file descriptors (catches `| tee` patterns)
- `/tmp/*.log` files whose names match the script name

When a log is found, the portal reads the head and tail to extract:
- **Dataset name** from lines like `=== EEG: N=14980 d=14 ===`
- **Fold progress** from lines like `fold 25/100: deep=0.670 rf=0.829`
- **Depth progress** from lines like `d7: best=0.8998`
- **Final results** from lines like `DONE EEG: deep=0.670 rf=0.829 sigma=-255.1`

### 4. Completed pipeline history

When a process disappears, the portal remembers it as "completed" with elapsed time. On startup, it also scans `PORTAL_RESULTS_DIR` for `result_*.json` files and shows them as completed pipelines with F1 scores and outcomes.

## Best Practices for Pipeline Visibility

To get the most out of the portal's auto-discovery, follow these conventions:

### Name your scripts descriptively

```bash
# Good -- portal shows "train_resnet" as the pipeline name
python train_resnet.py --epochs 100

# Bad -- portal shows "run" which tells you nothing
python run.py
```

### Use one process per job

```bash
# Good -- each dataset is a separate process, each visible in the portal
python run_experiment.py EEG 0      # GPU 0
python run_experiment.py Spambase 1  # GPU 1

# Bad -- one process loops over datasets, portal can't see which is running
python run_all.py  # invisible internal loop
```

### Log to /tmp with descriptive names

```bash
# Good -- portal finds the log and extracts "EEG" and fold progress
python run_experiment.py EEG 0 2>&1 | tee /tmp/EEG-depth7.log

# Bad -- portal can't match this log to the process
python run_experiment.py EEG 0 > output.txt
```

### Print structured progress lines

The portal recognizes these patterns in log output:

```python
# Dataset identification (parsed from head of log)
log.info("=== %s: N=%d d=%d ===", dataset_name, N, d)

# Fold progress (parsed from tail of log)
log.info("fold %d/%d: accuracy=%.4f", fold, total_folds, acc)

# Depth/stage progress
log.info("d%d: best=%.4f K=%d", depth, best_f1, n_candidates)

# Completion
log.info("DONE %s: accuracy=%.4f baseline=%.4f", dataset_name, acc, baseline)
```

### Set CUDA_VISIBLE_DEVICES for GPU labeling

```python
import os
os.environ["CUDA_VISIBLE_DEVICES"] = "1"  # portal reads this from /proc
```

Or from the shell:

```bash
CUDA_VISIBLE_DEVICES=0 python train.py  # portal shows "GPU 0"
CUDA_VISIBLE_DEVICES=1 python eval.py   # portal shows "GPU 1"
```

### Save results as result_*.json

Place `result_<name>.json` files in `PORTAL_RESULTS_DIR` and the portal will show them as completed pipelines on the Flow page:

```python
import json
result = {
    "name": "EEG",
    "test_f1": 0.670,
    "formula": "max(v13 - v5, v6)",
    "baselines": {"GB": {"mean": 0.829, "sigma": -255.1, "dir": "GB>"}},
}
with open("result_eeg.json", "w") as f:
    json.dump(result, f, indent=2)
```

### Use tmux for long-running jobs

```bash
# Good -- descriptive session name, tee'd log, portal sees everything
tmux new-session -d -s "EEG-depth7" \
    "python run_experiment.py EEG 0 2>&1 | tee /tmp/EEG-depth7.log"

# Launch a queue of jobs per GPU
tmux new-session -d -s "gpu0-queue" "\
    for ds in EEG Magic HIGGS; do \
        python run_experiment.py \$ds 0 2>&1 | tee /tmp/\${ds}-experiment.log; \
    done"
```

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /api/status` | System metrics (CPU temps, GPUs, memory, disk, load, sessions) |
| `GET /api/cores` | Per-core utilization with job labels |
| `GET /api/pipelines` | Auto-discovered pipeline list with stages, progress, and metrics |
| `GET /api/system-info` | Static hardware info (hostname, CPU, GPU models, RAM) |
| `GET /api/download/<name>` | Download a result JSON file |

## Configuration

### CLI Flags

```
research-portal [OPTIONS]

  --host HOST       Bind address (default: 0.0.0.0)
  --port PORT       Port (default: 8443 with SSL, 8080 without)
  --no-auth         Disable HTTP Basic authentication
  --user USER       Override auth username
  --password PASS   Override auth password
  --cert PATH       Path to SSL certificate
  --key PATH        Path to SSL private key
  --no-ssl          Force plain HTTP even if certs are present
  --version         Print version and exit
```

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `PORTAL_USER` | Auth username | `atlas` |
| `PORTAL_PASS` | Auth password | `atlas2026!research` |
| `PORTAL_SECRET` | Flask secret key | random |
| `PORTAL_RESULTS_DIR` | Directory for result JSON files and downloads | `.` |

### SSL

Place `cert.pem` and `key.pem` in the working directory, or pass them explicitly:

```bash
research-portal --cert /path/to/cert.pem --key /path/to/key.pem
```

Generate self-signed certs for development:

```bash
openssl req -x509 -newkey rsa:4096 -keyout key.pem -out cert.pem -days 365 -nodes
```

## Hardware Support

The portal dynamically detects any hardware configuration:

- **GPUs**: NVIDIA (`nvidia-smi`), AMD (`rocm-smi`), Intel (`lspci`) -- any number
- **CPUs**: Reads `/proc/cpuinfo` for model, physical cores, threads -- multi-socket supported
- **RAM**: From `/proc/meminfo`
- **Disk**: Root filesystem via `statvfs`
- **Temperatures**: CPU package temps via `sensors`, GPU temps via driver tools

## Development

```bash
pip install -e ".[dev]"
pytest -v
ruff check src/ tests/
ruff format src/ tests/
```

## License

MIT
