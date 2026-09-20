# System Manager — Continuation & Documentation

## Project Overview
Local system management dashboard for Linux (Ubuntu 24.04+), running in a container with host access. Provides read-only system observation, opt-in connectivity diagnostics, and approved actions with audit logging.

**Stack:** Python 3.14, stdlib only (no framework deps), single-file `server.py` + `index.html`, Docker/Compose deployment.

**Current URL:** http://localhost:4000/ (auth disabled via `.env` `DISABLE_AUTH=1`)

---

## Completed Milestones

### 2026-09-17 — Read-Only Dashboard (Milestone 1)
- **Files:** `server.py`, `index.html`, `tests/test_server.py`
- **Features:**
  - System: OS, kernel, CPU count/load, uptime
  - Hardware: Manufacturer, model, processor (from `/sys/class/dmi`)
  - Memory: Total, available, swap total/free (from `/proc/meminfo`)
  - Filesystem: Total/available on host root (via `os.statvfs`)
  - Network: Interfaces + addresses (via `ip -j address show`), default routes IPv4/IPv6 (via `ip -j route show default`)
  - Auth: Rotating local access code, session cookies (HttpOnly, SameSite=Strict), Host/Origin/CSRF validation
  - UI: Refresh (10s coalesced), Pause, Lock, accessible layout, stale/unavailable states
- **Tests:** 12 passing (collectors, cache, HTTP auth/CSRF/origin)

### 2026-09-19 — Opt-In Connectivity Diagnostics (Milestone 2)
- **Features:**
  - Gateway reachability (UDP connect to default gateway)
  - DNS resolution + DNS server reachability
  - TLS connection to approved HTTPS endpoint (configurable, validated: HTTPS only, no query/credentials, max 3)
  - Layered diagnosis: gateway → DNS → HTTPS, with plain-language explanations
  - Consent UI: explicit enable/disable, shows exact destination, 60s interval, no subnet/port scanning
- **Tests:** 4 added (endpoint validation, nameserver parsing, gateway detection, config rejection without session)

### 2026-09-19 — Approved Actions System (Milestone 3)
- **Features:**
  - User service restart/start (`systemctl --user`), critical services blocked
  - NetworkManager profile activation (Wi-Fi, VPN, ethernet, bridge, bond, team, vlan)
  - Preview → Approval (single-use, 5 min TTL, bound to exact parameters) → Execute → Verify
  - SQLite audit log (`actions.db`): timestamp, token hash, action type, params, preconditions, result, verification
  - NetworkManager checkpoint + timed rollback on failure
  - API: `/api/services`, `/api/profiles`, `/api/approve`, `/api/execute`, `/api/audit`
- **Tests:** 8 added (critical services, filtering, preconditions, profiles parsing, audit, approval lifecycle)

### 2026-09-20 — Containerization (Milestone 4)
- **Files:** `Dockerfile`, `docker-compose.yml`, `.env`
- **Features:**
  - Python 3.14 slim + iproute2, net-tools, network-manager, systemd
  - Host network + pid, mounts: `/proc`, `/sys`, `/etc`, `/run/user`, `/var/run/dbus`, `/sys/class/dmi`
  - Capabilities: SYS_ADMIN, SYS_RESOURCE, NET_ADMIN, CAP_DAC_READ_SEARCH
  - `HOST_ROOT=/host` for file reads, `DISABLE_AUTH=1` via `.env`
- **Result:** All readings observed (interfaces, routes, filesystem, nameservers)

---

## Architecture

```
server.py (single file, ~800 lines)
├── Collectors: snapshot() → system, hardware, memory, filesystem, interfaces, routes, nameservers
├── Connectivity: connectivity_checks() → gateway, dns, https (opt-in)
├── Actions: user_services(), nm_profiles(), service_*, nm_* (with preconditions/verify)
├── Auth: rotating token file, session cookies, Host/Origin/CSRF checks
├── Cache: SnapshotCache (10s coalesced, 60s connectivity, stale detection)
├── Audit: ActionAudit (SQLite, parameterized queries)
├── Approval: Approval (TTL, single-use, bound to params)
├── HTTP: ThreadingHTTPServer, JSON API, static HTML
└── CLI: argparse --port (default 8765 local, 4000 container)

index.html (embedded in server.py via ROOT)
├── Vanilla JS (ES6), no build step
├── CSS custom properties, responsive, dark-mode ready
├── Sections: System, Hardware, Network, Connectivity, Actions, Audit, Diagnostics
└── Modal approval flow, HTMX-like fetch patterns
```

---

## API Reference

### Authentication
| Header | Required |
|--------|----------|
| `Cookie: sm_session=<token>` | Yes (unless `DISABLE_AUTH=1`) |
| `Origin: http://localhost:4000` | Yes (state-changing) |
| `Host: localhost:4000` | Yes |

### Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/` | No | HTML dashboard |
| GET | `/api/status` | Yes | Full snapshot + connectivity config/results |
| GET | `/api/connectivity` | Yes | Connectivity settings only |
| POST | `/api/connectivity` | Yes | Update enabled/endpoints |
| GET | `/api/services` | Yes | List user services (name, load, active, sub, description) |
| GET | `/api/profiles` | Yes | List NM profiles (name, type, device) |
| POST | `/api/approve` | Yes | Request approval token (action_type, parameters) |
| POST | `/api/execute` | Yes | Execute approved action (approval_token) |
| GET | `/api/audit` | Yes | Recent action log (50 entries) |
| POST | `/api/login` | No | Exchange access code for session |
| POST | `/api/logout` | Yes | Invalidate session, rotate code |

### Approval Flow
1. `POST /api/approve` with `{action_type, parameters}` → returns `approval_token`, `preconditions`, `expires_in`
2. Show preview to user (modal)
3. `POST /api/execute` with `{approval_token}` → returns `outcome`, `result`, `verification`
4. Audit record written automatically

### Action Types
| Type | Parameters | Preconditions | Verification |
|------|------------|---------------|--------------|
| `service_restart` | `{name: "foo.service"}` | Not critical | `systemctl --user is-active` |
| `service_start` | `{name: "foo.service"}` | Not critical | `systemctl --user is-active` |
| `nm_activate` | `{name: "ProfileName"}` | NM checkpoint available | `nmcli con show --active` |

---

## Running

### Local Development
```bash
python3 server.py --port 8765
# Read access code from printed file path
# Open http://127.0.0.1:8765/
```

### Container (Production)
```bash
# .env must contain DISABLE_AUTH=1 (or remove for auth)
docker compose up -d --build
# Open http://localhost:4000/
# Access code in: docker logs system-manager
```

### Tests
```bash
python3 -m unittest discover -s tests -v
# 24 tests, ~3s
```

---

## Configuration

| Env Var | Default | Description |
|---------|---------|-------------|
| `DISABLE_AUTH` | `0` | Set `1` to disable access code (dev only) |
| `HOST_ROOT` | (empty) | Prefix for host paths in container (`/host`) |

---

## Future Work (Priority Order)

### 1. Notifications & Scheduling
- Desktop notifications (libnotify) for critical diagnostics
- Background scheduler for periodic checks with configurable intervals
- Webhook/email alerts for warranty, disk, memory thresholds

### 2. Package & Update Management
- List upgradable packages (apt list --upgradable)
- Show changelogs, security advisories
- Approved batch upgrade with snapshot/rollback (btrfs/zfs/timeshift)

### 3. Log Analysis & Journal
- `journalctl` queries: errors, failed units, boot time
- Structured log viewer with filters
- Export to file

### 4. Backup & Restore
- Config backup (etckeeper-style) for `/etc`, NetworkManager, systemd
- Scheduled backup to external drive/NAS
- Restore wizard with diff preview

### 5. Remote Device Enrollment
- SSH-based agent enrollment (authorized keys)
- Central dashboard for multiple authorized machines
- Encrypted tunnel (WireGuard/Tailscale) optional

### 6. Hardware Health
- SMART disk monitoring (smartctl)
- CPU/GPU temps (lm-sensors, nvidia-smi)
- Battery health (upower)
- Fan speeds, power consumption

### 7. Network Topology & Scanning
- ARP/NDP neighbor table
- Passive service discovery (mDNS, SSDP)
- Network map visualization (D3/cytoscape)

### 8. Module System
- Blueprint/plugin loader for optional features
- Per-module config, data dir, static assets
- Enable/disable without restart

---

## Obsidian Vault Sync

**Vault path:** `~/Obsidian/System Manager/`

| File | Purpose |
|------|---------|
| `Project Overview.md` | This summary |
| `Architecture.md` | Module diagram, data flow |
| `API Reference.md` | Full endpoint specs |
| `Deployment.md` | Docker, systemd, reverse proxy |
| `Testing.md` | Test patterns, fixtures, CI |
| `Troubleshooting.md` | Common issues, logs, debug tips |

Run `./scripts/sync-to-obsidian.sh` (to be created) to export docs.

---

## Git Status

```bash
git status
# Modified: docker-compose.yml, server.py
# New: .env, index.html, static/app.js, system_manager/status.py, templates/modules.html
```

**Next commit:** Update CONTINUATION.md, add docs/README.md, add .env.example, commit all.

---

## Next Steps

Say **"Create docs/README.md"** for project landing page  
Say **"Create docs/ARCHITECTURE.md"** for technical deep-dive  
Say **"Create docs/API.md"** for full endpoint docs  
Say **"Commit & push"** to finalize