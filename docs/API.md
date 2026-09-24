# API Reference

Base URL: `http://localhost:4000/` (container, gunicorn) or `http://127.0.0.1:8200/` (local `run.py`).

## Authentication

All API endpoints except `/`, `/api/login`, `/api/logout` require a valid session cookie when auth is enabled.

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

### Access Code
- Written to the file at `SYSTEM_MANAGER_AUTH_TOKEN_PATH` (600 perms) when set, with the same behavior as the standalone panel
- Rotates after each successful login
- Never appears in URLs, logs, or responses

### Disable Auth (Development)
Set `DISABLE_AUTH=1` in `.env` or environment. All endpoints become public.

> **Note:** In the container the app runs with `DISABLE_AUTH=1` from `.env`; a session cookie is not required there. The login/audit endpoints still exist for when auth is enabled.

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

### `POST /api/connectivity`
**Only in the standalone `server.py` panel.** The Flask app (`system_manager/`) reserves this path but returns `501` — interactive connectivity checks are still standalone-only and not yet wired into the Flask dashboard. Endpoint validation rules (HTTPS only, no query/credentials, max 3) live in `server.py:valid_endpoint`.

### `GET /api/services`
**Auth required.** List user-level systemd services.

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
| `nm_activate` | `{name: "ProfileName"}` | Activate NM profile |

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