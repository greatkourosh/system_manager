# API Reference

Base URL: `http://localhost:4000/` (container, gunicorn) or `http://127.0.0.1:8200/` (local `run.py`).

## Authentication

All API endpoints except `/`, `/api/login`, `/api/logout` require a valid session cookie when auth is enabled.

The module blueprints (`/inventory/*`, `/organizer/*`, `/packages/*`, `/logs/*`) enforce the same rule through a shared `before_request` hook. A locked request is answered according to the caller's `Accept` header:

| Caller | Response |
|--------|----------|
| Browser navigation (`Accept: text/html`) | `302` redirect to `/`, which hosts the unlock form |
| Fetch/JSON (`Accept: application/json`) | `401` with `{"error": "Unlock this dashboard with the local access code."}` |

This covers the inventory CRUD, builds, topology, export/import, and qr routes, the `/packages` and `/logs` pages, as well as every organizer route, including its POST forms for file operations. The dashboard (`/`), `/health`, and static assets are never gated.

### Headers Required
```
Cookie: sm_session=<token>
Origin: http://localhost:4000    # Required for POST
Host: localhost:4000             # Required for all requests
```

### Session Lifecycle
1. **Login**: `POST /api/login` with `{code}` → returns session cookie, rotates access code
2. **Use**: Include cookie in subsequent requests
3. **Logout**: `POST /api/logout` → invalidates session, clears cookie

Sessions are additive: logging in from a second tab issues a new token and
leaves existing ones valid, so one browser can hold several live sessions at
once. Rotating the access code does not invalidate a session you already hold.

### Access Code
- Written to the file at `SYSTEM_MANAGER_AUTH_TOKEN_PATH` (600 perms) when set
- Rotates after each successful login
- Never appears in URLs, logs, or responses

### Disable Auth (Development)
Set `DISABLE_AUTH=1` in `.env` or environment. All endpoints become public.

> **Note:** In the container auth is **on by default** — `docker-compose.yml`
> passes `DISABLE_AUTH=${DISABLE_AUTH:-0}`, so a missing or untracked `.env`
> can never silently expose the dashboard. Get the access code with
> `docker compose exec system-manager cat /data/access-code`; it rotates on
> every successful login.

---

## Endpoints

### `GET /`
Returns the HTML dashboard. No auth required.

### `GET /api/status`
**Auth required.** Returns complete system snapshot.

**Response:**
```json
{
  "state": "observed|stale|unavailable",
  "detail": "error message if any",
  "data": {
    "observed_at": 1789847540.7041152,
    "system": {
      "os": {"state": "observed", "value": "Ubuntu 24.04.5 LTS"},
      "kernel": {"state": "observed", "value": "7.0.0-31-generic"},
      "cpu_count": {"state": "observed", "value": 16},
      "load": {"state": "observed", "value": [3.34, 4.04, 4.26]},
      "uptime": {"state": "observed", "value": 18205.73}
    },
    "hardware": {
      "Manufacturer": {"state": "observed", "value": "ASUS"},
      "Model": {"state": "observed", "value": "System Product Name"},
      "Processor": {"state": "observed", "value": "13th Gen Intel(R) Core(TM) i5-13400F"}
    },
    "memory": {
      "MemTotal": {"state": "observed", "value": 67198767104},
      "MemAvailable": {"state": "observed", "value": 38800785408},
      "SwapTotal": {"state": "observed", "value": 8589930496},
      "SwapFree": {"state": "observed", "value": 8588992512}
    },
    "filesystem": {"state": "observed", "value": {"total": 1000204886016, "available": 450123456789}},
    "interfaces": {"state": "observed", "value": [...]},
    "routes": {"IPv4": {...}, "IPv6": {...}},
    "internet": {"state": "not tested", "detail": "..."},
    "nameservers": {"state": "observed", "value": ["127.0.0.1"]},
    "suggestions": [],
    "connectivity": {...}
  },
  "connectivity": {...},
  "checks": {...},
  "explanations": [...],
  "auth_disabled": true
}
```

**Reading states:**
- `"observed"` — `value` contains the data
- `"unavailable"` — `value: null`, `detail` explains why
- `"stale"` — cached data >30s old or collection error

### `GET /api/connectivity`
**Auth required.** Current opt-in connectivity configuration and the last scan result.

**Response:**
```json
{
  "enabled": true,
  "endpoints": ["https://example.com/"],
  "destination": {"url": "https://example.com/", "host": "example.com", "port": 443},
  "last_run": 1758600000.12,
  "status": "reachable",
  "checks": {
    "gateway": {"state": "observed", "value": {"value": "192.168.1.1", "device": "eno1", "family": "IPv4"}},
    "gateway_reachable": {"state": "observed", "value": {"host": "192.168.1.1", "method": "udp_connect"}},
    "dns_configured": {"state": "observed", "value": ["1.1.1.1"]},
    "dns_reachable": {"state": "observed", "value": {"host": "1.1.1.1", "method": "udp_connect"}},
    "dns_resolution": {"state": "observed", "value": {"name": "example.com", "addresses": ["93.184.216.34"]}},
    "https": {"state": "observed", "value": {"host": "example.com", "port": 443, "transport": "tls"}}
  },
  "explanations": ["TLS connection to example.com succeeded. Internet access works for this endpoint."]
}
```

### `POST /api/connectivity`
**Auth required.** Enable or disable outbound checks and set the approved endpoint(s).

**Request:** `{"enabled": true, "endpoints": ["https://example.com/"]}`

**Response:** the same shape as `GET /api/connectivity` (200). Validation rules (HTTPS only, no credentials/query/fragment, max 200 chars, max 3 endpoints) are enforced by `server.py:valid_endpoint`; a rejected request returns 400 with an `error` message. Probes run on `/api/status` when a result older than 60s is due.

`{"enabled": false}` always succeeds and needs no `endpoints`: the last approved endpoint is retained but nothing is contacted, so checks can always be turned off. `enabled` must be a boolean; anything else is a 400.

### `GET /api/services`
**Auth required.** List user-level systemd services.

> **Unavailable in the container (as of 2026-09-26).** This endpoint and
> `/api/profiles` return `{"state": "unavailable", "value": null, ...}` on a
> stock `docker compose up` — `auth.py` calls `systemctl --user` as root, and
> the user bus refuses root. Both work when the app runs natively as the host
> user. See DEPLOYMENT.md "NetworkManager actions fail" for the full triage
> (the `--machine=<user>@.host` proxy works as root over the system bus).

**Response:**
```json
{
  "state": "observed",
  "value": [
    {"name": "myapp.service", "load": "loaded", "active": "active", "sub": "running", "description": "My Application"}
  ]
}
```
Critical services (journald, logind, resolved, udevd, dbus, polkit, network-manager, ssh, timesyncd, user-sessions) are excluded.

### `GET /api/profiles`
**Auth required.** List NetworkManager connection profiles.

> **Available as of 2026-09-26.** Required `security_opt: [apparmor=unconfined]`
> and `DBUS_SYSTEM_BUS_ADDRESS=unix:path=/run/dbus/system_bus_socket`; both are
> now in `docker-compose.yml` and verified against a live container. See
> DEPLOYMENT.md.

**Response:**
```json
{
  "state": "observed",
  "value": [
    {"name": "HomeWifi", "type": "wireless", "device": "wlan0"},
    {"name": "WorkVPN", "type": "vpn", "device": null}
  ]
}
```
Types: wireless, vpn, ethernet, bridge, bond, team, vlan.

### `POST /api/approve`
**Auth required.** Request approval token for an action.

**Request:**
```json
{
  "action_type": "service_restart",
  "parameters": {"name": "myapp.service"}
}
```

**Action Types:**
| Type | Parameters | Description |
|------|------------|-------------|
| `service_restart` | `{name: "foo.service"}` | Restart user service |
| `service_start` | `{name: "foo.service"}` | Start user service |
| `nm_activate` | `{name: "ProfileName"}` | Activate NM profile — **currently always refused**, see note below |

**Response:**
```json
{
  "approval_token": "a1b2c3...",
  "preconditions": {"name": "myapp.service", "action": "restart"},
  "expires_in": 300
}
```
Token is single-use, bound to session + exact parameters, expires in 5 minutes.

### `POST /api/execute`
**Auth required.** Execute a previously approved action.

**Request:**
```json
{
  "approval_token": "a1b2c3..."
}
```

**Response:**
```json
{
  "outcome": "success|failure",
  "result": {"code": 0, "stdout": "...", "stderr": "..."},
  "verification": {"state": "observed", "value": {"active": true}}
}
```
Audit record written automatically. On `nm_activate` failure, NM rollback attempted.

> **`nm_activate` fails closed (2026-09-26).** It requires an NM checkpoint so a
> failed activation can be rolled back, and no checkpoint can be made here: the
> `con checkpoint` nmcli verb exists in neither the container's nmcli 1.52 nor the
> host's 1.46, and the D-Bus `CheckpointCreate` call is refused by polkit
> (`checkpoint-rollback` defaults to `auth_admin_keep`, and the shipped NM rules
> grant only `settings.modify.system`). Reproduced on the host, so it is not a
> container issue. The endpoint returns
> `{"error": "NetworkManager checkpoint not available; safe rollback cannot be guaranteed."}`
> rather than activating a profile with no way back. Unblocking it needs a polkit
> rule or dropping the checkpoint requirement — a security decision, not a bug fix.

### `GET /api/audit`
**Auth required.** Recent action log (max 50).

**Response:**
```json
{
  "actions": [
    {
      "id": 1,
      "ts": 1789847540.7041152,
      "token_hash": "sha256...",
      "action_type": "service_restart",
      "parameters": "{\"name\": \"myapp.service\"}",
      "preconditions": "{\"name\": \"myapp.service\", \"action\": \"restart\"}",
      "result": "{\"code\": 0, \"stdout\": \"\", \"stderr\": \"\"}",
      "verification": "{\"state\": \"observed\", \"value\": {\"active\": true}}"
    }
  ]
}
```

### `GET /api/notifications`
**Auth required.** The conditions currently true, the alerts recently sent,
and whether the last delivery worked.

**Response:**
```json
{
  "active": ["packages:stale-index", "packages:upgradable"],
  "sent": [
    {"key": "packages:stale-index", "title": "Oldest apt index is 888 days old",
     "body": "...", "ts": 1789847540.7}
  ],
  "last_error": null,
  "interval": 300
}
```

A condition is announced once, when it newly appears; it is forgotten when it
clears, so it can alert again. `active` is empty when nothing is wrong.

### `POST /api/notifications`
**Auth required.** Send a fixed test notification. The body is a constant, so
nothing a caller sends reaches `notify-send`'s argv.

**Response:** `200 {"ok": true, "state": {...}}`, or `503` with `ok: false` and
`last_error` set when `notify-send` failed — a dead desktop daemon must not
read as a delivered alert.

### `POST /api/login`
**No auth.** Exchange access code for session.

**Request:**
```json
{"code": "ZlELdYeyAYFyMPSTdIWqsdniwBx3baIAvtHU32_q49M"}
```

**Response:** 200 + `Set-Cookie: sm_session=...; HttpOnly; SameSite=Strict; Max-Age=28800`

### `POST /api/logout`
**Auth required.** Invalidate session.

**Response:** 200 + `Set-Cookie: sm_session=; Max-Age=0`

### `GET /packages/`
**Auth required.** The host's upgradable package set, as HTML, or as JSON
with `Accept: application/json`.

**No apt is run and nothing is approved.** The module parses the host's
`/var/lib/dpkg/status` and `/var/lib/apt/lists` (bind-mounted `ro` at
`/host/…`) and renders the commands for the user to copy — `apt-get -s
install --only-upgrade` (dry run) and `apt-get install --only-upgrade` (the
real one), plus an equivalent pair for the backports bucket.

**Response:**
```json
{
  "ok": true,
  "upgradable": [
    {"name": "apparmor", "installed": "4.0.1…-7", "candidate": "4.0.1…-8",
     "suite": "noble-updates", "security": false, "arch": "amd64",
     "phased": null, "backports": false}
  ],
  "backports": [],
  "security_count": 0,
  "phased_count": 15,
  "suites": ["noble", "noble-backports", "noble-security", "noble-updates"],
  "arches": ["amd64"],
  "index": {"newest": 1789766400.0, "oldest": 1789257600.0, "count": 12},
  "command": "sudo apt-get install --only-upgrade apparmor",
  "dry_run": "sudo apt-get -s install --only-upgrade apparmor"
}
```

`upgradable` is sorted by name; `command`/`dry_run` name every package in it,
and `backports_command`/`backports_dry_run` do the same for the backports
bucket (empty strings when there are none). `status_path` and `list_dir` echo
where the module actually looked.

`phased` is Ubuntu's rollout percentage, or `null`. **apt decides per host
whether to offer a phased version and that decision is in no file**, so this
module reports the percentage rather than resolving it — the same index can
yield a different answer on a different machine.

**When the mounts are missing, `ok` is `false`, not an empty list:**
```json
{
  "ok": false,
  "detail": "dpkg status not readable at /host/var/lib/dpkg/status: …",
  "status_path": "/host/var/lib/dpkg/status",
  "list_dir": "/host/var/lib/apt/lists",
  "mounts": ["/var/lib/dpkg -> …", "/var/lib/apt/lists -> …"]
}
```
A blank page would be indistinguishable from an up-to-date host, and a missing
mount is the single likeliest cause, so the failure names itself and its fix.

> `index` older than 7 days under-reports: apt has not been told about newer
> versions, so "nothing to upgrade" may just mean "we have not looked". The page
> warns when this is so. Parsing is ~2.5 s against 274 MB of indexes and runs on
> every request.

### `GET /logs/`
**Auth required.** The host's journal entries, as HTML, or as JSON with
`Accept: application/json`. `?export=json` returns the same entries as a
download.

**Query parameters:** `priority` (a syslog name: `emerg alert crit err warning
notice info debug`), `unit`, `since` (any timestamp `journalctl` accepts, e.g.
`-24h`, `today`, `07:00`), `boot` (an index from the boot dropdown, or `all`),
`limit` (1–2000, default 200).

**Nothing is written and nothing is approved.** The module only runs
`journalctl` queries — no vacuum, no rotation — so `auth.Approval` is
deliberately unused.

**Response:**
```json
{
  "ok": true,
  "entries": [
    {"timestamp": 1790535294.550406, "time_text": "2026-09-27 20:19:43",
     "priority": "info", "unit": "docker.service", "identifier": "dockerd",
     "pid": "2218", "hostname": "kourosh-pc", "message": "…"}
  ],
  "count": 200, "error_count": 3,
  "applied": ["priority=err", "unit=sshd.service", "limit=200"],
  "rejected": [], "detail": "",
  "journal_dir": "/host/var/log/journal",
  "priorities": ["emerg", "…"], "max_limit": 2000
}
```

Entries are newest first. `error_count` counts priorities 3 and above.

**A refused filter appears in `rejected` and on the page** rather than being
dropped — a silently ignored `priority` would leave the user reading a query
they never asked for. `priority` is checked against the syslog set, `boot`
against an index or `all`, and `since` against a conservative charset. Values
are passed as **paired argv** (`--priority err`, not `--priority=err`), which is
what keeps a value from becoming its own option.

**`--root` takes a filesystem root, not the journal directory.** It is passed
`/host`, and `<JOURNAL_HOST_DIR>/var/log/journal` is what gets checked for
existence. Passing the journal directory itself makes journalctl look one level
too deep, print "No journal files were found", and **exit 0**.

**When the mount is missing, `ok` is `false`, not an empty list:**
```json
{"ok": false, "mount_error": true,
 "detail": "The host journal is not mounted… - /var/log/journal:/host/var/log/journal:ro",
 "journal_dir": "/host/var/log/journal"}
```
`journalctl` exits 0 for a good read, an empty result, and an unreadable
journal alike, so the mount is checked *before* querying. A blank page would
otherwise be indistinguishable from a quiet host.

> Reading the journal also requires the container process to be in group `adm`
> (gid 4) — the files are `root:systemd-journal` with an ACL granting `adm`. The
> shipped `docker-compose.yml` sets `group_add: ["4"]`; without it every read is
> `Permission denied`, which surfaces as the mount error above rather than as
> an empty log.

---

## Error Responses

All errors return JSON:
```json
{"error": "Human-readable message"}
```

| Status | Cause |
|--------|-------|
| 400 | Invalid JSON, missing fields, invalid parameters |
| 401 | Missing/invalid session, wrong access code |
| 403 | Invalid Host/Origin, cross-site request |
| 404 | Unknown endpoint |
| 415 | Content-Type not application/json |
| 429 | Too many login attempts (5 in 30s) |
| 501 | Unsupported HTTP method |

---

## Reading Value Format

Every reading uses this structure:
```json
{
  "state": "observed|unavailable|stale",
  "value": <any> | null,
  "detail": "explanation if unavailable"
}
```

**Frontend helper:**
```javascript
const display = (reading, format = String) =>
  reading?.state === 'observed' ? format(reading.value) : 'Unavailable';
```

---

## Rate Limits & Timeouts

| Operation | Limit |
|-----------|-------|
| Snapshot refresh | Coalesced to 10s minimum interval |
| Connectivity checks | 60s minimum interval |
| Command timeout | 2s (ip, systemctl, nmcli list), 15-20s (execute) |
| Session TTL | 8 hours |
| Approval TTL | 5 minutes |
| Login attempts | 5 per 30 seconds |
| Request body | Max 1024 bytes |
| Command output | Max 131072 bytes |

---

## WebSocket / SSE

Not implemented. Frontend polls `/api/status` every 10s when visible and not paused.

---

## CORS

No CORS headers. Only same-origin requests accepted. `Access-Control-Allow-Origin` never set.