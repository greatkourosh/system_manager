# Architecture

## Overview

The canonical app is a **Flask application** (`system_manager/`) that mounts feature modules as blueprints. It serves Jinja templates + a small vanilla-JS dashboard and runs unprivileged, reading host state via the collector functions from the sibling `server.py`.

```
┌─────────────────────────────────────────────────────────────┐
│                        Browser                                │
│  http://localhost:4000/  (gunicorn → run.py)                  │
└─────────────────────┬───────────────────────────────────────┘
                      │ HTTP/JSON
                      ▼
┌─────────────────────────────────────────────────────────────┐
│  system_manager/ (Flask app, via run.py)                    │
│  ├─ status.py      — live snapshot (imports server.snapshot)│
│  ├─ auth.py        — login/session, approve→execute→audit   │
│  │                  (services, NM profiles, Access code)     │
│  ├─ organizer.py   — mounts folder_organizer app at          │
│  │                  /organizer (URL rewriter)                │
│  ├─ inventory/     — hardware inventory blueprint + SQLite   │
│  └─ __init__.py    — create_app(), /, /api/status, /modules  │
│                                                    │         │
│   templates/ (Jinja) · static/ (vanilla JS/CSS)              │
└─────────────────────┬───────────────────────────────────────┘
                      │ subprocess / syscalls
        ┌─────────────┼─────────────┐
        ▼             ▼             ▼
   /proc/...    /sys/class/dmi   nmcli, systemctl, ip
   /etc/...     os.statvfs       (host binaries via mounts)
```

> **Standalone panel** — `server.py` is still a complete stdlib-only HTTP server (`ThreadingHTTPServer`) serving its own embedded `index.html`, but it is now fully superseded: connectivity diagnostics, access-code auth, actions, and audit all live in `system_manager/` (`connectivity.py`, `auth.py`), which imports `server.py` for its collectors. Dashboard is the Flask app; the standalone panel is kept only until its HTTP layer is retired.

## Core Modules

### `snapshot()` — Read-Only Collection
Pure functions, no side effects. Each collector returns a normalized dict with `{state: "observed"|"unavailable", value: ..., detail?: ...}`.

| Collector | Source | Key Metrics |
|-----------|--------|-------------|
| `system` | `os.uname()`, `/proc/uptime`, `/proc/loadavg`, `/etc/os-release`, `/proc/cpuinfo` | OS, kernel, CPU count, load, uptime |
| `hardware` | `/sys/class/dmi/id/{sys_vendor,product_name}`, `/proc/cpuinfo` | Vendor, model, CPU model |
| `memory` | `/proc/meminfo` | MemTotal, MemAvailable, SwapTotal, SwapFree |
| `filesystem` | `os.statvfs("/")` (or `/host/` in container) | Total, available bytes |
| `interfaces` | `ip -j address show` | Name, operstate, addresses |
| `routes` | `ip -j -4/-6 route show default` | Gateway, device per family |
| `nameservers` | `/etc/resolv.conf` | List of nameserver IPs |

### `connectivity_checks()` — Opt-In Active Probes
Only runs when `config.enabled == true`. Uses configured destination (validated HTTPS URL).

| Check | Method | Target |
|-------|--------|--------|
| `gateway` | Parse routes | Default gateway IP + device |
| `gateway_reachable` | UDP `connect()` | Gateway (IPv4/IPv6) |
| `dns_configured` | Parse `/etc/resolv.conf` | Nameserver list |
| `dns_reachable` | UDP `connect()` | First nameserver |
| `dns_resolution` | `socket.getaddrinfo()` | Destination hostname |
| `https` | TLS `create_connection` + `wrap_socket` | Destination host:443 |

### `SnapshotCache` — Coalesced Collection
- **Lock-protected** shared state
- **10s interval** for main snapshot (coalesces concurrent requests)
- **60s interval** for connectivity (independent)
- **Stale detection**: >30s old or collection error
- **Thread-safe** `get()` returns `{state, detail, data, connectivity, checks, explanations}`

> `SnapshotCache` is the standalone panel's store. Under Flask, the 60s connectivity cache lives in `system_manager/connectivity.py:ConnectivityStore` (same lock + staleness rules), while `status.collect()` stays a thin uncached collector; the two share `server.py`'s `connectivity_checks()`/`connectivity_diagnosis()`.

### `ActionAudit` — SQLite Logging
```sql
CREATE TABLE actions (
  id INTEGER PRIMARY KEY,
  ts REAL NOT NULL,           -- epoch
  token_hash TEXT NOT NULL,   -- SHA256(session_token)
  action_type TEXT NOT NULL,  -- service_restart, service_start, nm_activate
  parameters TEXT NOT NULL,   -- JSON
  preconditions TEXT NOT NULL,-- JSON
  result TEXT NOT NULL,       -- JSON (exit code, stdout, stderr)
  verification TEXT           -- JSON (post-exec state)
);
```
Indexes on `ts` and `token_hash`. Parameterized queries only.

### `Approval` — Single-Use Tokens
- `issue(token_hash, action_type, parameters, preconditions, ttl=300)` → `approval_token` (hex)
- `consume(approval_token)` → approval dict or `None` (expired/used/invalid)
- In-memory dict with periodic cleanup
- Bound to exact action type + parameters + session

### Actions
| Action | Executable | Preconditions | Verification |
|--------|------------|---------------|--------------|
| `service_restart` | `systemctl --user restart <name>` | Not in `CRITICAL_SERVICES` | `systemctl --user is-active` |
| `service_start` | `systemctl --user start <name>` | Not in `CRITICAL_SERVICES` | `systemctl --user is-active` |
| `nm_activate` | `nmcli con up <name>` | `nmcli con checkpoint` succeeds | `nmcli con show --active` |
| `nm_activate` rollback | `nmcli con rollback <checkpoint>` | On execute failure | N/A |

## Container Runtime

### Mounts
| Host Path | Container Path | Purpose |
|-----------|----------------|---------|
| `/proc` | `/host/proc` | Process/kernel info |
| `/sys` | `/host/sys` | Hardware, DMI |
| `/etc` | `/host/etc` | OS release, resolv.conf |
| `/run/user` | `/run/user` | User systemd bus |
| `/var/run/dbus` | `/var/run/dbus` | System dbus (NM, systemd) |
| `/sys/class/dmi` | `/sys/class/dmi` | Hardware IDs |

> **Known limitation — actions are unreachable in the container.** Read-only
> observation works because it reads files through the mounts above, but the
> action paths (`/api/services`, `/api/profiles`, and therefore
> `service_restart` / `service_start` / `nm_activate`) all fail with
> `{"state": "unavailable"}` on a stock `docker compose up`. Three
> independent causes, each verified against a running container on
> 2026-09-26:
>
> 1. **Wrong D-Bus path.** `docker-compose.yml` sets
>    `DBUS_SYSTEM_BUS_ADDRESS=unix:path=/host/run/dbus/system_bus_socket`, but
>    there is no `/host/run` — only `/host/etc`, `/host/proc`, and `/host/sys`
>    are mounted. The socket is at `/run/dbus/system_bus_socket`.
> 2. **AppArmor's `docker-default` profile denies D-Bus.** With the path
>    corrected but AppArmor left at its default, `nmcli` fails with
>    `GDBus.Error:org.freedesktop.DBus.Error.AccessDenied: An AppArmor policy
>    prevents this sender from sending this message`. The container needs
>    `--security-opt apparmor=unconfined`; with that, `nmcli con show` lists
>    the host's real connections.
> 3. **The user bus rejects root.** `systemctl --user` needs
>    `XDG_RUNTIME_DIR` / `DBUS_SESSION_BUS_ADDRESS`; even with those set,
>    connecting as root gives `Transport endpoint is not connected` (as uid
>    1000 the same call succeeds). `auth.py` invokes `systemctl --user`
>    directly, so it needs either to run as the host uid or to go through
>    systemd's `--machine=<user>@.host` proxy, which works as root over the
>    system bus.
>
> Causes 1 and 2 are container-config issues; cause 3 is in
> `system_manager/auth.py`. See CONTINUATION.md for the open item.

### Capabilities
| Capability | Used For |
|------------|----------|
| `SYS_ADMIN` | `statvfs` on host root, some `/proc` reads |
| `SYS_RESOURCE` | Resource limits, `getloadavg` |
| `NET_ADMIN` | `nmcli` operations |
| `CAP_DAC_READ_SEARCH` | Read root-owned files via mounts |

### Network
- Bridge networking, port 4000 published (`ports: ["4000:4000"]`) — was
  `network_mode: host` until switched from the project dashboard. Under host
  mode `localhost` was the host; under bridge it is the container, so anything
  reaching a host-local socket must go via the `/host` mounts instead.
- `pid: host` — Access to host process namespace (for `--user` systemd)

## Security Model

### Threat: Unauthorized Access
- **Mitigation**: Rotating access code file (600 perms), never in URLs/logs
- **Session**: HttpOnly, SameSite=Strict, 8h TTL, SHA256 stored
- **Host/Origin**: Must match `http://127.0.0.1:PORT` or `http://localhost:PORT`
- **CSRF**: `Sec-Fetch-Site: cross-site` rejected, Origin required for POST

### Threat: Command Injection
- **Mitigation**: Fixed argv arrays, no shell, `subprocess.Popen`/`run` with explicit args
- **Paths**: Absolute (`/usr/bin/nmcli`), no `$PATH` search
- **Env**: Minimal `{"PATH": "/usr/bin:/usr/sbin", "LC_ALL": "C"}`

### Threat: Privilege Escalation
- **Mitigation**: Runs as unprivileged user in container
- **Actions**: Only `--user` systemd (no sudo), NM profile activation (user-scoped or polkit)
- **Rollback**: NM checkpoint created before activation, auto-rollback on failure

### Threat: Data Leakage
- **Mitigation**: No telemetry, no external requests except approved connectivity checks
- **Audit**: Redacts secrets (tokens, passwords) — only stores parameter names/values
- **Logs**: Access code never written to stdout/stderr

## Frontend Architecture

```
index.html (served by /)
├── CSS: Custom properties, responsive grid, dark-mode ready
├── JS: Vanilla ES6, no dependencies
│   ├── State: paused, signedIn, current, lastResult, pendingApproval
│   ├── Renderers: render(), renderConnectivity(), renderServices(), renderProfiles(), renderAudit()
│   ├── API: fetch() with credentials: 'same-origin'
│   ├── Flow: refresh() → /api/status → render*() → updateState()
│   └── Approval: requestApproval() → modal → executeApproval() → /api/execute
└── Sections: System, Hardware, Network, Connectivity, Actions, Audit, Diagnostics
```

### State Machine
```
[Boot] → [Login] → [Dashboard] ↔ [Pause/Refresh]
                ↓
          [Request Approval] → [Preview Modal] → [Execute] → [Verify] → [Dashboard]
```

## Testing Strategy

| Layer | Tool | Coverage |
|-------|------|----------|
| Unit | `unittest` + `unittest.mock` | Collectors, cache, approval, audit, endpoint validation |
| Integration | `unittest` + `http.client` | Full HTTP stack: auth, CSRF, logout, session expiry |
| Contract | Fixtures + mocks | Malformed input, timeouts, permission errors, unavailable sensors |
| Smoke | Manual / browser | Full UI flow, container mounts, real system data |

Run: `python3 -m pytest -q` (59 tests + 40 subtests, ~4s)
Test modules: `test_server.py` (24 — collectors/cache/HTTP on the standalone panel), `test_connectivity.py` (11 — config, scan cadence, endpoint validation, API auth), `test_flask_app.py` (10 — Flask auth + approval flow), `test_lock.py` (9 — module blueprint gating), `test_inventory.py` (5 — store + API).

## Extensibility Points

1. **New Collectors** — Add to `snapshot()`, update `SnapshotCache._observe()`
2. **New Connectivity Checks** — Extend `connectivity_checks()`, `connectivity_diagnosis()`
3. **New Actions** — Add `action_*` functions, register in `/api/approve` and `/api/execute`
4. **New API Endpoints** — Add to `Handler.do_GET/do_POST`
5. **Frontend Sections** — Add HTML section + renderer + nav link
6. **Auth Policies** — Extend `session_valid()`, `allowed_request()`

## Performance Characteristics

| Metric | Value |
|--------|-------|
| Snapshot latency | ~50-100ms (local commands) |
| Connectivity latency | ~2-5s (sequential probes) |
| Memory footprint | ~15-25 MB (Python + SQLite) |
| CPU (idle) | <1% |
| Cache coalescing | 10s window, unlimited concurrent readers |
| Audit DB size | ~1 KB per action |

## Failure Modes

| Component | Failure | Behavior |
|-----------|---------|----------|
| `ip` command | Missing/timeout | `state: "unavailable"`, detail logged |
| `systemctl --user` | No user bus | `state: "unavailable"`, empty list |
| `nmcli` | No NM/timeout | `state: "unavailable"`, actions disabled |
| SQLite | Disk full/perm | Exception caught, audit skipped, action continues |
| Network probe | Timeout/refused | `state: "unavailable"`, diagnosis reflects |
| Container mount | Missing path | Reads return `unavailable`, no crash |

---

*Updated 2026-09-24 to reflect the Flask-first architecture, the 
`system_manager/auth.py` port, and the SQLite-backed inventory store.*