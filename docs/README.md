# System Manager

Local system management dashboard for Linux. Runs in a container with host access, provides real-time system observation, connectivity diagnostics, and approved actions with audit logging.

## Quick Start

```bash
# Container (recommended)
docker compose up -d --build
# Open http://localhost:4000/

# Local development
python3 server.py --port 8765
# Open http://127.0.0.1:8765/ (read access code from stdout)
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

- **Single-file backend** (`server.py`, ~800 lines, stdlib only)
- **Embedded frontend** (`index.html` with vanilla JS/CSS)
- **Containerized** with host mounts for `/proc`, `/sys`, `/etc`, dbus
- **24 unit/integration tests** (collectors, auth, actions, connectivity)

## Documentation

- [Continuation & Future Work](CONTINUATION.md)
- [Architecture](ARCHITECTURE.md)
- [API Reference](API.md)
- [Deployment](DEPLOYMENT.md)

## Requirements

- Linux (Ubuntu 24.04+ tested)
- Docker + Compose (container) or Python 3.12+ (local)
- Host: `systemd`, `NetworkManager`, `iproute2`

## License

MIT