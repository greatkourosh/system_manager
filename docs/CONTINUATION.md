# System Manager — Continuation & Documentation

## Project Overview
Local system management dashboard for Linux (Ubuntu 24.04+), running in a container with host access. Provides read-only system observation, opt-in connectivity diagnostics, and approved actions with audit logging.

**App:** Flask app (`system_manager/`) hosting feature modules as blueprints, served by gunicorn in the container and `run.py` in dev.
**Standalone panel:** `server.py` (stdlib-only, ~830 lines) is now fully superseded by the Flask app — connectivity, actions, and audit all live in `system_manager/`, which imports `server.py` for its collectors. It is kept only until its HTTP layer is retired.

---

## Completed Milestones

### 2026-09-17 — Read-Only Dashboard (Milestone 1) — `server.py`
Collectors (system, hardware, memory, filesystem, network), rotating access code + session cookies, Host/Origin/CSRF validation, 12 tests. Now imported by `system_manager/status.py` so both apps report identical readings.

### 2026-09-19 — Opt-In Connectivity Diagnostics (Milestone 2) — `server.py`
Gateway → DNS → HTTPS layered diagnosis with explicit consent UI. ***Ported to Flask*** in Milestone 5.

### 2026-09-19 — Approved Actions System (Milestone 3) — `server.py`
service restart/start plus NetworkManager profile activation (preview → single-use approval → execute → verify → SQLite audit, NM checkpoint rollback). ***Ported to Flask** — see Milestone 5.

### 2026-09-20 — Containerization (Milestone 4)
Python 3.14 slim + iproute2, net-tools, network-manager, systemd; host network + pid; mounts for `/proc`, `/sys`, `/etc`, `/run/user`, `/var/run/dbus`, `/sys/class/dmi`; HOST_ROOT=/host. ***Updated** in Milestone 5 to run Flask.

### 2026-09-24 — Flask App + Module System (Milestone 5)
- **New package** `system_manager/`:
  - `__init__.py` — `create_app()`, `/api/status`, `/api/series`, `/modules`
  - `status.py` — live snapshot via `server.py` collectors
  - `auth.py` — access-code auth + services/approve/execute/audit API (ported from Milestone 3), single-use approval tokens, SQLite audit
  - `connectivity.py` — opt-in connectivity diagnostics (ported from Milestone 2): `ConnectivityStore` + GET/POST `/api/connectivity`
  - `organizer.py` — mounts the sibling folder_organizer app under `/organizer` (in-process, without copying or modifying it)
  - `inventory/` — hardware inventory blueprint with a SQLite store: real CRUD, filters, soft-delete, CSV/JSON/YAML export (was hardcoded stubs)
- **Frontend:** Jinja templates (`templates/`) + `static/app.js` with unlock, actions (approve→execute), audit, and connectivity renderers; module cards with mount-aware links; Network card holds the connectivity consent form
- **Docker:** Dockerfile now installs Flask/gunicorn and copies the package + templates + static; compose runs `gunicorn run:app` on :4000
- **Wiring fix:** module URLs no longer leak `?subpath=`; `inject_common` provides `env.authenticated` so the dashboard hides the unlock banner when auth is off
- **Tests:** 57 passing — 24 `test_server.py` + `test_flask_app.py` (auth + approval lifecycle) + `test_inventory.py` (store + API) + `test_connectivity.py` (config, scan cadence, endpoint validation, API auth) + `test_lock.py` (module blueprint gating)

---

## Architecture

```
run.py ── create_app() ── Flask
├── system_manager/__init__.py
│     ├── /, /api/status, /api/series, /modules
│     ├── status.py        → importlib imports server.snapshot()
│     ├── auth.py          → login/session, /api/services /api/profiles
│     │                     /api/approve /api/execute /api/audit
│     │                     Security, Approval, ActionAudit (SQLite)
│     ├── connectivity.py  → ConnectivityStore, GET/POST /api/connectivity
│     │                     gateway → DNS → HTTPS probes every 60s when enabled
│     ├── organizer.py     → proxy to folder_organizer app at /organizer
│     └── inventory/       → blueprint at /inventory; store.py (SQLite)
│                            CRUD, filters, soft-delete, export
├── templates/  (base.html, index.html, modules.html)  Jinja
└── static/     (app.js, app.css, favicon.svg)         vanilla JS

server.py (standalone stdlib panel, kept)
├── Collectors: snapshot() → system, hardware, memory, filesystem,
│               interfaces, routes, nameservers   (shared with Flask)
├── Connectivity: connectivity_checks() → gateway, dns, https (opt-in)
├── Actions: user_services(), nm_profiles(), service_*, nm_*
├── Auth: rotating token file, session cookies, Host/Origin/CSRF
├── Audit: ActionAudit (SQLite) · Approval (single-use)
└── HTTP: ThreadingHTTPServer, JSON API, static HTML
```

---

## API Reference (Flask app)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/` | No | HTML dashboard |
| GET | `/api/status` | Yes | Full snapshot (`{state, data: {...}}`) |
| GET | `/api/series` | Yes | Chart-ready series (`memory_used`, `disk_used`, `load`, `time`) |
| GET | `/modules` | No | Module catalog |
| GET | `/api/services` | Yes | User services (critical excluded) |
| GET | `/api/profiles` | Yes | NM profiles |
| POST | `/api/approve` | Yes | Issue single-use approval token |
| POST | `/api/execute` | Yes | Execute approved action |
| GET | `/api/audit` | Yes | Recent action log |
| POST | `/api/login` | No | Exchange access code for session |
| POST | `/api/logout` | Yes | Invalidate session |
| GET | `/api/connectivity` | Yes | Connectivity settings + last scan (`enabled`, `endpoints`, `destination`, `last_run`, `status`, `checks`, `explanations`) |
| POST | `/api/connectivity` | Yes | Set `{enabled, endpoints:[https://…]}`; validates each URL (https only, no credentials/query/fragment, max 200 chars) |

### Connectivity Flow
1. The dashboard's Network card shows `Internet: not tested.` while outbound checks are off.
2. Enter an approved HTTPS endpoint → **Enable outbound checks** → `POST /api/connectivity {enabled: true, endpoints: [...]}`.
3. On each `/api/status` poll the store runs gateway → DNS → HTTPS probes when a result older than 60s is due; `/api/status` then carries `connectivity`, `checks`, and `explanations`.
4. **Disable** clears the checks and returns the panel to its off state.

Probes contact only the approved endpoint, the default gateway, and the first configured nameserver. No scanning, and nothing runs until explicitly enabled. Each gunicorn worker keeps its own store, so with multiple workers each would scan on its own interval (the shipped Dockerfile uses `--workers 1`).

Inventory module (mounted at `/inventory`): `GET/POST /inventory/items`, `GET/PATCH/DELETE /inventory/items/<id>`, `GET/POST /inventory/builds`, `GET /inventory/export?format={json,csv,yaml}`.
Full endpoint specs in API.md.

### Approval Flow
1. `POST /api/approve` `{action_type, parameters}` → `approval_token`, `preconditions`, `expires_in`
2. Show preview (modal)
3. `POST /api/execute` `{approval_token}` → `outcome`, `result`, `verification`
4. Audit record written automatically; NM rollback attempted on activation failure

### Action Types
| Type | Parameters | Preconditions | Verification |
|------|------------|---------------|--------------|
| `service_restart` | `{name: "foo.service"}` | Not critical | `systemctl --user is-active` |
| `service_start` | `{name: "foo.service"}` | Not critical | `systemctl --user is-active` |
| `nm_activate` | `{name: "ProfileName"}` | NM checkpoint available | `nmcli con show --active` |

---

## Running

### Local Development (Flask)
```bash
pip install -r requirements.txt
python3 run.py            # http://127.0.0.1:8200/
```

### Standalone Panel
```bash
python3 server.py --port 8765
# Read access code from printed file path; Open http://127.0.0.1:8765/
```

### Container (Production)
```bash
# .env must contain DISABLE_AUTH=1 (or remove for auth)
docker compose up -d --build
# Open http://localhost:4000/
```

### Tests
```bash
python3 -m unittest discover -s tests -v
# 57 tests, ~4s
```

---

## Configuration

| Env Var | App | Default | Description |
|---------|-----|---------|-------------|
| `DISABLE_AUTH` | both | `0` | Set `1` to disable access code (dev only) |
| `HOST_ROOT` | server.py | (empty) | Prefix for host paths in container (`/host`) |
| `SYSTEM_MANAGER_AUTH_TOKEN_PATH` | Flask | (empty) | Writable path for the rotating access code (auth on) |
| `INVENTORY_DB` | Flask | (tmpdir) | SQLite file for the inventory module |

---

## Future Work (Priority Order)

### 1. Notifications & Scheduling
Desktop notifications (libnotify) for critical diagnostics; background scheduler with configurable intervals; webhook/email alerts for warranty, disk, memory thresholds.

### 2. Package & Update Management
List upgradable packages (`apt list --upgradable`); changelogs/security advisories; approved batch upgrade with snapshot/rollback (btrfs/zfs/timeshift).

### 3. Log Analysis & Journal
`journalctl` queries (errors, failed units, boot time); structured log viewer with filters; export.

### 4. Backup & Restore
Config backup (etckeeper-style) for `/etc`, NetworkManager, systemd; scheduled backup to external drive/NAS; restore wizard with diff preview.

### 5. Remote Device Enrollment
SSH-based agent enrollment (authorized keys); central dashboard for multiple authorized machines; encrypted tunnel (WireGuard/Tailscale) optional.

### 6. Hardware Health
SMART disk monitoring (smartctl); CPU/GPU temps (lm-sensors, nvidia-smi); battery health (upower); fan speeds, power consumption.

### 7. Network Topology & Scanning
ARP/NDP neighbor table; passive service discovery (mDNS, SSDP); network map visualization (D3/cytoscape).

### 8. Complete the Flask Port
- ~~Wire connectivity diagnostics into the Flask dashboard~~ — done, see Milestone 5
- ~~Enforce login redirect / lock the whole app when auth is on~~ — done: the inventory and organizer blueprints gate on the session (`auth.requires_session_view`), so locked pages redirect to the dashboard and JSON callers get 401
- Retirement: `server.py`'s HTTP layer is now redundant (collectors, actions, and connectivity are all reached through the Flask app). Drop it once the standalone panel is no longer needed, keeping its collectors + actions as an importable library.

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

Clean through `ac47c92` ("Port actions, approval, audit, and auth from server.py to Flask"). The remainder of Milestone 5 — the connectivity port (`system_manager/connectivity.py`, the Network card UI, API docs) and the app-wide lock (`auth.requires_session_view` on the inventory/organizer blueprints) — is the current uncommitted change: 3 new files plus edits to 9, with 57 tests passing.

> `core.filemode` is set to `false` on this clone, so the older repo-wide `100644 → 100755` mode flips no longer appear in diffs.

---

## Next Steps

- ~~Review + commit the Flask port~~ — done in `ac47c92`
- ~~Wire the connectivity diagnostics into the Flask dashboard~~ — done, see Milestone 5
- ~~Enforce login redirect / lock the whole app when auth is on~~ — done; see `tests/test_lock.py`
- **Commit the Milestone 5 remainder** (connectivity port + lock) — everything is green, only the commit is outstanding
- Decide: retire `server.py`'s standalone panel now that Flask covers all of it, or keep it as a thin wrapper over `system_manager`
- Package & update management (#2) is the highest-value next feature