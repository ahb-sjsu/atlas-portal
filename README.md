# Atlas Research Portal

Web dashboard for the Atlas research workstation (HP Z840).

<p align="center">
  <em>Live monitoring · Experiment tracking · Results visualization</em>
</p>

## Features

- **Live system monitoring** — CPU/GPU temperatures, utilization, memory, load averages (10s refresh)
- **GPU status** — Per-GPU utilization bars, memory usage, thermal state for both GV100s
- **Experiment tracking** — Active tmux sessions with status
- **Results dashboard** — Theory Radar benchmark results table with formula-vs-ensemble comparisons
- **Platform guide** — Hardware specs, venv locations, model paths, thermal limits
- **Resource links** — Quick access to repos, PyPI packages, Kaggle competitions

## Quick Start

```bash
pip install flask
python app.py
# Open http://atlas:8080
```

## Screenshot

The dashboard shows:
- CPU Package temperatures with color-coded warnings (green < 75°C, yellow < 85°C, red > 85°C)
- GPU utilization bars with memory breakdown
- Active experiment sessions
- Theory Radar results: formula F1, discovered formula, sigma vs GB/RF/LR with win/loss badges

## Hardware

| Component | Spec |
|-----------|------|
| CPU | 2x Xeon E5-2690 v3 (48 threads, 2.60GHz) |
| RAM | 128GB → upgrading to 320-384GB |
| GPU 0 | Quadro GV100 32GB (Volta) |
| GPU 1 | Quadro GV100 32GB (Volta) |
| NVLink | Pending installation |
| Disk | 1.8TB total |

## Architecture

Single-file Flask app with:
- `/` — Main dashboard (HTML + CSS + JavaScript, no build step)
- `/api/status` — JSON system metrics (CPU temps, GPU info, memory, disk, load, tmux sessions)
- `/api/results` — JSON Theory Radar experiment results

Auto-refreshes every 10 seconds via `fetch()`. No database, no npm, no webpack — just Python + HTML.

## License

MIT
