# I Built a Zero-Config Research Workstation Dashboard -- and Published It

If you run ML experiments on a beefy Linux workstation, you know the drill: SSH in, run `nvidia-smi`, check `htop`, wonder which of your 44 CPU cores is actually doing something, and try to remember which tmux session has the training run that's been going for 19 hours.

I got tired of it. So I built **research-portal** -- a drop-in web dashboard that auto-discovers everything about your machine and every pipeline running on it. No config files. No YAML. No agent to install.

```
pip install research-portal
research-portal
```

That's it. Open the URL in your browser.

## What It Does

**research-portal** reads directly from `/proc`, `nvidia-smi`, `sensors`, and the process table to build a live picture of your workstation:

- **Dashboard** -- CPU package temperatures, GPU utilization/memory/thermals, RAM, disk, load averages, active tmux sessions. A dynamically generated Platform Guide shows your exact hardware specs, discovered at startup.

- **Resource Map** -- a per-core CPU grid (color-coded idle/low/med/high) showing which script is pinned to which core, plus per-GPU visual utilization bars. Think Google Maps, but for your hardware.

- **Pipeline Flow** -- automatically discovers every running Python or bash pipeline from the process table. Shows CPU/memory per process, which GPU it's using (read from `CUDA_VISIBLE_DEVICES` in `/proc/pid/environ`), fold progress parsed from log files, and a pulsing animation for active stages. When a job finishes, it's remembered as "completed" with elapsed time. Drop `result_*.json` files in a directory and the portal picks them up with scores and metrics.

## The Design Philosophy

Most monitoring tools (Grafana, Netdata, Prometheus) are built for ops teams managing fleets. They're powerful but require setup, configuration, and maintenance. Research workstations are different -- you want to glance at your machine's state between experiments, not build a monitoring stack.

research-portal discovers facts about the system dynamically:
- CPU model and core count from `/proc/cpuinfo`
- GPU models from `nvidia-smi`, `rocm-smi`, or `lspci` (NVIDIA, AMD, Intel)
- RAM from `/proc/meminfo`
- Running pipelines from `ps` output with pattern matching
- GPU assignments from `/proc/pid/environ`
- Dataset names and fold progress from log file parsing

It adapts to whatever machine it lands on. Dual Xeons with two Quadro GV100s? Single Ryzen with an RTX 4090? A headless server with no GPU at all? Same command, different dashboard.

## Pipeline Discovery: The Part I'm Most Proud Of

The dashboard and resource map are nice, but the pipeline flow is where research-portal earns its keep.

It follows a few conventions to make your pipelines maximally visible:

**Name your scripts descriptively.** `python train_resnet.py` shows as "train_resnet" in the portal. `python run.py` shows as "run" -- not helpful.

**One process per job.** `python experiment.py EEG 0` shows as "experiment/EEG" with GPU 0 tagged. The first positional argument becomes the pipeline sub-name.

**Log to /tmp with tee.** The portal scans for log files and parses them for progress:

```python
# Portal recognizes these patterns:
log.info("=== %s: N=%d d=%d ===", dataset, N, d)  # dataset identification
log.info("fold %d/%d: accuracy=%.4f", fold, total, acc)  # progress
log.info("DONE %s: accuracy=%.4f", dataset, acc)  # completion
```

**Results files.** Drop `result_dataset.json` files and completed experiments show up in the flow with F1 scores and outcomes, even after the process exits.

## Security

Since research machines sometimes have public IPs or are on shared networks:
- HTTP Basic authentication (override user/pass via CLI or env vars)
- Multi-user support: admin (full access) and guest accounts (read-only)
- Auto-detects SSL certificates for HTTPS
- Security headers (CSP, X-Frame-Options, X-Content-Type-Options)
- No config files with credentials -- everything via environment

## The Stack

Deliberately minimal:
- **Flask** (single dependency)
- **Vanilla JS** (no React, no npm, no build step)
- Templates are inline -- the entire app is `pip install` and go
- Hatchling build, CI with ruff lint + pytest on Python 3.10/3.11/3.12

## Where It Came From

I built this while running a 19-dataset benchmark for a symbolic AI project (Theory Radar). I had two GPUs running different experiments, 14 CPU-heavy gradient boosting baselines, and I kept losing track of what was running where. The portal started as a quick Flask script and evolved into something I thought others might find useful.

The irony: I built a monitoring tool because I needed to monitor experiments, and now the monitoring tool is one of the experiments the portal monitors.

## Open Source

The code is on GitHub with CI badges, tests, and MIT license:

- **PyPI:** https://pypi.org/project/research-portal/
- **GitHub:** https://github.com/ahb-sjsu/atlas-portal

If you run ML workloads on Linux, give it a try. `pip install research-portal && research-portal` -- 30 seconds to a live dashboard.

---

#MachineLearning #Python #OpenSource #DevTools #GPU #MLOps #Research #Monitoring
