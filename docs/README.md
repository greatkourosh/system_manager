# System Manager

Local system management dashboard for Linux. Runs in a container with host access, provides real-time system observation, connectivity diagnostics, and approved actions with audit logging.

## Quick Start

```bash
# Container (recommended) — auth is on by default
docker compose up -d --build
# Open http://localhost:4000/  (gunicorn -> run.py -> Flask app)
# Unlock with the access code:
#   docker compose exec system-manager cat /data/access-code

# Local development (Flask)
python3 run.py
# Open http://127.0.0.1:8200/
```

## Features

| Category | Capabilities |
|----------|--------------|
| **System Observation** | CPU, memory, disk, uptime, kernel, hardware identity |
| **Network** | Interfaces, addresses, routes, DNS servers |
| **Connectivity** | Gateway reachability, DNS resolution, TLS endpoint test (opt-in) |
| **Actions** | Restart/start user services, activate NetworkManager profiles (VPN, Wi-Fi, ethernet) |
| **Safety** | Preview→approval flow, single-use tokens, NM checkpoint rollback, SQLite audit log |
| **Auth** | Rotating local access code, session cookies, CSRF/Origin/Host validation |

## Architecture

- **Flask app** (`system_manager/`) hosting feature modules via blueprints
  - `status` — live system snapshot (reuses `server.py` collectors)
  - `auth` — access-code auth + approved actions + SQLite audit
  - `connectivity` — opt-in gateway → DNS → HTTPS diagnostics (reuses `server.py` probes)
  - `organizer` — mounts the sibling folder_organizer app under `/organizer`
  - `inventory` — hardware inventory (SQLite store, CRUD + export)
- **Templates/static** under `templates/`, `static/` (Jinja, vanilla JS/CSS)
- **Standalone panel** (`server.py`, stdlib-only, `--port 8765/4000`) superseded by the Flask app; kept until its HTTP layer is retired
- **Containerized** with host mounts for `/proc`, `/sys`, `/etc`, dbus
- **59 unit/integration tests** (`tests/test_server.py`, `tests/test_flask_app.py`, `tests/test_connectivity.py`, `tests/test_inventory.py`, `tests/test_lock.py`)

## Documentation

- [Continuation & Future Work](CONTINUATION.md)
- [Architecture](ARCHITECTURE.md)
- [API Reference](API.md)
- [Deployment](DEPLOYMENT.md)

## Requirements

- Linux (Ubuntu 24.04+ tested)
- Docker + Compose (container) or Python 3.12+ with `pip install -r requirements.txt` (Flask, gunicorn)
- Host: `systemd`, `NetworkManager`, `iproute2`

## License

MIT