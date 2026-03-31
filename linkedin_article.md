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

- **Dashboard** -- CPU package temperatures, GPU utilization/memory/thermals, RAM, disk, load averages, active tmux sessions. Auto-refreshes every 10 seconds.

- **Resource Map** -- a per-core CPU grid (color-coded idle/low/med/high) showing which script is pinned to which core, plus per-GPU visual utilization bars. Think Google Maps, but for your hardware.

- **Pipeline Flow** -- automatically discovers every running Python or bash pipeline from the process table. Shows CPU/memory per process, which GPU it's using (read from `CUDA_VISIBLE_DEVICES` in `/proc/pid/environ`), and a pulsing animation for active stages.

[SCREENSHOT: Dashboard view showing GPU temps, CPU temps, system metrics]

[SCREENSHOT: Resource Map showing per-core grid with job labels and GPU bars]

[SCREENSHOT: Pipeline Flow showing multiple active ML pipelines]

## Why Zero-Config Matters

Most monitoring tools (Grafana, Netdata, Prometheus) are built for ops teams managing fleets. They're powerful but require setup, configuration, and maintenance. Research workstations are different -- you want to glance at your machine's state between experiments, not build a monitoring stack.

research-portal discovers facts about the system dynamically:
- CPU model and core count from `/proc/cpuinfo`
- GPU models from `nvidia-smi`, `rocm-smi`, or `lspci` (NVIDIA, AMD, and Intel)
- RAM from `/proc/meminfo`
- Running pipelines from `ps` output with pattern matching
- GPU assignments from `/proc/pid/environ`

It adapts to whatever machine it lands on. Dual Xeons with two Quadro GV100s? Single Ryzen with an RTX 4090? A headless server with no GPU at all? Same command, different dashboard.

## Security

Since research machines often have public IPs or are on shared networks, research-portal ships with:
- HTTP Basic authentication (override user/pass via CLI or env vars)
- Auto-detects SSL certificates for HTTPS
- Security headers (CSP, X-Frame-Options, X-Content-Type-Options)
- No config files with credentials -- everything via environment

## The Stack

Deliberately minimal:
- **Flask** (single dependency)
- **Vanilla JS** (no React, no npm, no build step)
- Templates are inline -- the entire app is `pip install` and go
- Hatchling build system, CI with ruff lint + pytest on Python 3.10/3.11/3.12

## Open Source

The code is on GitHub with CI badges, tests, and MIT license:

- PyPI: https://pypi.org/project/research-portal/
- GitHub: https://github.com/ahb-sjsu/atlas-portal

If you run ML workloads on Linux, give it a try. Feedback and PRs welcome.

---

#MachineLearning #Python #OpenSource #DevTools #GPU #MLOps #Research
