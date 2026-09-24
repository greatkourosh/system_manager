# System Manager

Local system management dashboard for Linux. Runs in a container with host access, provides real-time system observation, connectivity diagnostics, and approved actions with audit logging.

## Quick Start

```bash
# Container (recommended)
docker compose up -d --build
# Open http://localhost:4000/  (gunicorn -> run.py -> Flask app)

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
  - `organizer` — mounts the sibling folder_organizer app under `/organizer`
  - `inventory` — hardware inventory (SQLite store, CRUD + export)
- **Templates/static** under `templates/`, `static/` (Jinja, vanilla JS/CSS)
- **Standalone panel** (`server.py`, stdlib-only, `--port 8765/4000`) kept for the interactive connectivity diagnostics not yet wired into Flask
- **Containerized** with host mounts for `/proc`, `/sys`, `/etc`, dbus
- **37 unit/integration tests** (`tests/test_server.py`, `tests/test_flask_app.py`, `tests/test_inventory.py`)

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