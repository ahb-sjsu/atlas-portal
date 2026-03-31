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

## Screenshots

<!-- TODO: Add screenshots -->
*Dashboard, Resource Map, and Pipeline Flow views coming soon.*

## Features

- **Auto-detects hardware** -- hostname, CPU model, GPU models, RAM, disk (reads from `/proc`, `nvidia-smi`, etc.)
- **Live system monitoring** -- CPU temperatures, GPU utilization/memory/thermals, RAM, disk, load averages
- **Pipeline discovery** -- automatically finds running Python/bash pipelines from process list
- **Resource map** -- per-core CPU utilization grid with job labels, per-GPU visual bars
- **Pipeline flow** -- live view of all discovered pipelines with stage progress
- **Security** -- HTTP Basic auth, security headers (CSP, X-Frame-Options, etc.)
- **SSL support** -- auto-detects `cert.pem`/`key.pem` for HTTPS
- **Zero config** -- works on any Linux workstation out of the box

## Pages

| Route | Description |
|-------|-------------|
| `/` | Main dashboard -- temperatures, GPU status, memory, active sessions |
| `/map` | Resource map -- per-core CPU grid, per-GPU utilization visuals |
| `/flow` | Pipeline flow -- auto-discovered running/completed pipelines |

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /api/status` | System metrics (CPU temps, GPUs, memory, disk, load, sessions) |
| `GET /api/cores` | Per-core utilization with job labels |
| `GET /api/pipelines` | Auto-discovered pipeline list with stages |
| `GET /api/system-info` | Static hardware info (hostname, CPU, GPU models, RAM) |

## Configuration

### CLI Flags

```
research-portal [OPTIONS]

  --host HOST       Bind address (default: 0.0.0.0)
  --port PORT       Port (default: 8443 with SSL, 8080 without)
  --no-auth         Disable HTTP Basic authentication
  --user USER       Override auth username
  --password PASS   Override auth password
  --no-ssl          Force plain HTTP even if certs are present
  --version         Print version and exit
```

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `PORTAL_USER` | Auth username | `atlas` |
| `PORTAL_PASS` | Auth password | `atlas2026!research` |
| `PORTAL_SECRET` | Flask secret key | random |
| `PORTAL_RESULTS_DIR` | Directory for downloadable result files | `.` |

### SSL

Place `cert.pem` and `key.pem` in the working directory or package directory. The portal will auto-detect them and serve on HTTPS port 8443.

Generate self-signed certs for development:

```bash
openssl req -x509 -newkey rsa:4096 -keyout key.pem -out cert.pem -days 365 -nodes
```

## Development

```bash
pip install -e ".[dev]"
pytest -v
ruff check src/ tests/
ruff format src/ tests/
```

## License

MIT
