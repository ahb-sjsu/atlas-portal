# I built a zero-config dashboard for my ML workstation because I was tired of SSHing in to run nvidia-smi

**TL;DR:** `pip install research-portal` then `research-portal`. Open browser. That's it.

---

I run ML experiments on an HP Z840 with dual Quadro GV100s. The workflow was always: SSH in, check `nvidia-smi`, check `htop`, open a few tmux sessions, try to remember which one has the 19-hour training run, check CPU temps with `sensors`, wonder which of my 48 cores is actually doing something.

So I wrote a web dashboard that figures all of this out automatically. No config files. No YAML. No Docker. No Prometheus/Grafana stack.

```bash
pip install research-portal
research-portal
```

It reads `/proc`, `nvidia-smi`, `sensors`, and the process table to build a live picture of your machine:

**Dashboard** -- CPU/GPU temps, memory, disk, load, active tmux sessions, plus a dynamically generated "Platform Guide" showing your exact hardware (it reads `/proc/cpuinfo`, detects your GPUs, etc.)

**Resource Map** -- per-core CPU utilization grid color-coded by load, with the name of whatever script is running on each core. Per-GPU utilization bars.

**Pipeline Flow** -- this is the part I'm most happy with. It auto-discovers every running Python/bash pipeline from the process table. It reads `CUDA_VISIBLE_DEVICES` from `/proc/pid/environ` to figure out which GPU each job is on. It parses your log files to extract dataset names and fold progress. When a job finishes, it remembers it as "completed" with elapsed time. If you have `result_*.json` files, it picks those up too and shows F1 scores.

**Zero config means zero config.** I tested it on three different machines (dual-Xeon workstation, single-GPU dev box, headless server with no GPU). Same command, different dashboard. It adapts to whatever hardware is present.

**What it's NOT:**
- Not a Grafana replacement for production monitoring
- Not a cluster manager (it's for one machine)
- Not a job scheduler

It's the equivalent of taping `nvidia-smi -l`, `htop`, and your tmux session list to a browser tab with auto-refresh.

**Best practices for visibility** (discovered the hard way):
- Name your scripts descriptively (`train_resnet.py` not `run.py`)
- One process per job, not a loop inside one script
- `tee` your output to `/tmp/DatasetName-experiment.log`
- Print `=== DatasetName: N=14980 d=14 ===` at the start
- Print `fold 25/100: accuracy=0.87` periodically

The portal recognizes these patterns and shows them in the UI.

**Security:** HTTP Basic auth, security headers, optional HTTPS with self-signed certs or explicit `--cert`/`--key`. Multi-user support with read-only guest accounts.

**Stack:** Flask (single dependency), vanilla JS, inline templates. No npm, no build step, no React.

MIT licensed: https://github.com/ahb-sjsu/atlas-portal

PyPI: https://pypi.org/project/research-portal/

Happy to answer questions. Built this over a weekend while waiting for benchmark results to finish (ironic, since the dashboard now shows me the benchmark results).
