# Deployment Guide

## Container (Recommended)

### Prerequisites
- Docker Engine 24+
- Docker Compose v2+
- Linux host with systemd, NetworkManager, iproute2

### Quick Start
```bash
cd /media/kourosh/DEVNVME/projects/system_manager
docker compose up -d --build
# Open http://localhost:4000/ and unlock with the access code.
# The container runs as root, so the file is root-owned: read it with
docker compose exec system-manager cat /data/access-code
```

The access code **rotates on every successful login** — re-read the file
whenever you need to log in again.

> **The action features do not work on a stock `compose up`.** Service restart
> and NetworkManager profile activation return
> `{"state": "unavailable"}`; the dashboard's service and profile panels show
> "Could not list user services." / "Could not list NetworkManager profiles."
> Read-only observation (CPU, memory, disk, network, hardware, audit log,
> inventory) is unaffected. The cause is three separate bugs — a wrong
> D-Bus path in `docker-compose.yml`, AppArmor's default profile denying
> D-Bus, and `auth.py` calling `systemctl --user` as root. The first two are
> documented and fixable in this file; the third is an open code change. See
> CONTINUATION.md for the full write-up.

Authentication is **on by default**. A clone with no `.env` starts locked; the
access code is written to `./data/access-code` on the host (and regenerated on
each successful login, so a leaked file alone is not enough). Because the
container runs as root the file is mode `0600` and root-owned — read it with
`docker compose exec` or `sudo cat`, and keep it out of version control.

### Configuration
`.env` is optional and untracked — copy `.env.example` to create one. It is
only read for Compose variable interpolation, so it must live beside
`docker-compose.yml`:

```bash
# .env
DISABLE_AUTH=1          # Opt out of the access code (development only)
# SYSTEM_MANAGER_AUTH_TOKEN_PATH=/data/access-code   # Already set in docker-compose.yml
# HOST_ROOT=/host       # Already set in docker-compose.yml
```

`DISABLE_AUTH=1` is for local development only. The container runs on
`network_mode: host` with `SYS_ADMIN`, `NET_ADMIN` and a read-only `/etc`
mount, so with auth disabled every `/api/*` endpoint — including
`POST /api/execute`, which runs `systemctl` and NetworkManager commands — is
reachable by anything that can reach port 4000. Leave it off unless you are
running on an isolated network.

### Volumes
| Host Path | Container Path | Mode | Required |
|-----------|----------------|------|----------|
| `/proc` | `/host/proc` | ro | Yes |
| `/sys` | `/host/sys` | ro | Yes |
| `/etc` | `/host/etc` | ro | Yes |
| `/run/user` | `/run/user` | ro | Yes (user systemd) |
| `/var/run/dbus` | `/var/run/dbus` | ro | Yes (NM, systemd) — see the note below |
| `/sys/class/dmi` | `/sys/class/dmi` | ro | Yes (hardware IDs) |
| `./data` | `/data` | rw | Yes (access code, audit + inventory DBs) |
| `../folder_organizer` | `/folder_organizer` | rw | Only for the Folder Organizer module |

### Capabilities
| Capability | Purpose |
|------------|---------|
| `SYS_ADMIN` | `statvfs` on host root, `/proc` access |
| `SYS_RESOURCE` | `getloadavg()`, resource limits |
| `NET_ADMIN` | `nmcli` network operations |
| `CAP_DAC_READ_SEARCH` | Read root-owned files via mounts |

### AppArmor (required for the action features)

The host's AppArmor `docker-default` profile **denies D-Bus** traffic from
inside a container. Without an override, `nmcli` fails with:

```
GDBus.Error:org.freedesktop.DBus.Error.AccessDenied: An AppArmor policy
prevents this sender from sending this message to this recipient
```

The action features (NetworkManager profiles, service restart) need
`--security-opt apparmor=unconfined`. Read-only observation does **not** — it
reads files through the mounts, never the bus — so the dashboard works without
this. To enable the actions, add to `docker-compose.yml`:

```yaml
    security_opt:
      - apparmor=unconfined
```

This is a deliberate loosening of the container's security boundary. It is
defensible here only because the container already runs as root with
`SYS_ADMIN`, `SYS_RESOURCE`, `NET_ADMIN` and `host` networking; see the
limitations section in CONTINUATION.md before enabling it anywhere else.

### Network & PID
- `network_mode: host` — Direct access to host network stack
- `pid: host` — Access to host process namespace (for `--user` systemd)

### Restart Policy
```yaml
restart: unless-stopped
```

### Health Check
```bash
docker exec system-manager curl -sf http://localhost:4000/health | jq -e '.status == "ok"'
```

`/health` is the only unauthenticated JSON endpoint. Use `/api/status` instead
only when auth is disabled — with auth on it returns `401`, so probing it is
not a valid liveness check.

---

## Local Development

### Prerequisites
- Python 3.12+
- Ubuntu 24.04+ (or compatible systemd/NetworkManager system)
- `iproute2`, `network-manager`, `systemd` installed

### Run
```bash
pip install -r requirements.txt
python3 run.py            # Flask app on http://127.0.0.1:8200/
# Authorisation disabled by default; set DISABLE_AUTH=0 + a token path to enable.
```

The standalone stdlib panel (superseded by the Flask app; reference only) is still:
```bash
python3 server.py --port 8765
# Output:
# System Manager: http://127.0.0.1:8765
# Local access code file: /tmp/system-manager-XXXXXX/access-code
# Audit database: /tmp/system-manager-XXXXXX/actions.db
```

### Environment Variables
```bash
DISABLE_AUTH=1 python3 run.py                       # Skip login (Flask)
SYSTEM_MANAGER_AUTH_TOKEN_PATH=/data/access-code    # Write rotating code (Flask)
DISABLE_AUTH=0 python3 server.py --port 8765        # standalone panel, auth on
HOST_ROOT=/host python3 server.py                   # Container-style paths
```

---

## Reverse Proxy (Production)

### Nginx Example
```nginx
server {
    listen 80;
    server_name sm.example.com;

    location / {
        proxy_pass http://127.0.0.1:4000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # WebSocket not used, but keep for future
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";

        # Security headers (also set by app)
        add_header X-Content-Type-Options nosniff;
        add_header X-Frame-Options DENY;
        add_header Referrer-Policy no-referrer;
    }
}
```

### With Auth Enabled
- Keep `DISABLE_AUTH=0` (default)
- Access code file is in container temp dir
- Retrieve via `docker logs system-manager` or `docker exec`

---

## Systemd Service (Alternative to Docker)

### User Service (No Container)
```ini
# ~/.config/systemd/user/system-manager.service
[Unit]
Description=System Manager Dashboard
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 /opt/system-manager/server.py --port 8765
WorkingDirectory=/opt/system-manager
Restart=on-failure
RestartSec=5
Environment=DISABLE_AUTH=0
# For container-like paths if using host mounts:
# Environment=HOST_ROOT=/host

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now system-manager
# Access at http://127.0.0.1:8765/
# Access code in journal: journalctl --user -u system-manager -f
```

### System Service (Root, Not Recommended)
Requires `sudo` for `--user` systemd calls. Not supported by current action implementation.

---

## Backup & Restore

### Audit Database
```bash
# Backup
docker exec system-manager cp /tmp/system-manager-XXXXXX/actions.db /backup/actions-$(date +%F).db

# Or from host if using bind mount for audit:
# docker-compose.yml: add `- ./data:/data`
# Then: cp /data/actions.db /backup/
```

### Configuration
```bash
# Backup .env, docker-compose.yml
tar -czf backup-config-$(date +%F).tar.gz .env docker-compose.yml
```

---

## Updates

### Container
```bash
cd /media/kourosh/DEVNVME/projects/system_manager
git pull
docker compose up -d --build
```

### Local
```bash
cd /media/kourosh/DEVNVME/projects/system_manager
git pull
# Restart process
```

---

## Troubleshooting

### Container won't start
```bash
docker logs system-manager
# Check: missing mounts, capability errors, port conflicts
```

### Readings show "unavailable"
```bash
# Test mounts
docker exec system-manager ls /host/proc /host/sys /host/etc
# Test commands
docker exec system-manager /usr/sbin/ip -j address show
docker exec system-manager /usr/bin/nmcli -t -f NAME,TYPE,DEVICE con show
docker exec system-manager /usr/bin/systemctl --user list-units --type=service
```

### Auth not working
```bash
# Check .env
cat .env
# Should have DISABLE_AUTH=1 for no auth, or unset/0 for auth
# Restart after change: docker compose restart
```

### NetworkManager actions fail
```bash
# Check NM running
docker exec system-manager systemctl is-active NetworkManager
# Check checkpoint support
docker exec system-manager /usr/bin/nmcli con checkpoint
```

This is the single most common failure on a stock deployment, and it has
three independent causes. Diagnose them in order — each one masks the next.

**1. D-Bus path points at a directory that does not exist.**
```bash
# Wrong path — the mount is /var/run/dbus, not /host/run/dbus
docker exec system-manager printenv DBUS_SYSTEM_BUS_ADDRESS
# Correct: unix:path=/run/dbus/system_bus_socket
docker exec system-manager ls -la /run/dbus/system_bus_socket
```
Fix in `docker-compose.yml`:
```yaml
- DBUS_SYSTEM_BUS_ADDRESS=unix:path=/run/dbus/system_bus_socket
```

**2. AppArmor denies D-Bus.** Symptom after fixing (1) — the socket exists but
`nmcli` still refuses:
```bash
docker exec system-manager nmcli -t -f NAME,TYPE,DEVICE con show
# GDBus.Error:...AccessDenied: An AppArmor policy prevents this sender...
```
Fix with `security_opt: [apparmor=unconfined]` (see the AppArmor section above).
Verify — this should list your real connections:
```bash
netplan-eno1:802-3-ethernet:eno1
br-11bc256a9deb:bridge:br-11bc256a9deb
```

**3. The user bus rejects root.** Affects services, not NetworkManager. Even
with the environment set, connecting as root fails while the same call as the
host uid succeeds:
```bash
# As root: "Transport endpoint is not connected"
docker exec system-manager busctl --address=unix:path=/run/user/1000/bus list
# The same container started with --user 1000:1000 works.
```
`system_manager/auth.py` calls `systemctl --user` directly, so user services
stay unavailable until it either runs as the host uid or switches to systemd's
`--machine=<user>@.host` proxy over the system bus (verified working as root):
```bash
docker exec system-manager \
  systemctl --machine=<user>@.host --user list-units --type=service
```

### Filesystem reading unavailable
```bash
# Check statvfs path
docker exec system-manager python3 -c "import os; print(os.statvfs('/host/'))"
```

---

## Security Hardening

### Production Checklist
- [ ] `DISABLE_AUTH=0` in `.env`
- [ ] Reverse proxy with TLS (HTTPS)
- [ ] Firewall: only allow proxy → container port 4000
- [ ] Regular audit log review
- [ ] Rotate access code file periodically (auto on login)
- [ ] Monitor `docker logs` for errors

### Capability Reduction (Advanced)
Remove unused capabilities if not needed:
```yaml
# If no NM actions:
# cap_drop: [NET_ADMIN]
# If no filesystem statvfs on host root:
# cap_drop: [SYS_ADMIN, SYS_RESOURCE]
```

---

## Monitoring

### Prometheus Metrics (Not Yet Implemented)
Planned: `/metrics` endpoint with:
- `system_manager_snapshot_duration_seconds`
- `system_manager_connectivity_check_duration_seconds`
- `system_manager_actions_total{action_type,outcome}`
- `system_manager_cache_stale_total`

### Log Aggregation
```bash
# JSON logs not yet implemented; currently plain text
docker logs system-manager --since 1h | grep -E "(ERROR|WARNING|action)"
```

---

## Uninstall

```bash
docker compose down -v
# Removes container, network, volumes
# Audit DB in /tmp is ephemeral; backup first if needed
```