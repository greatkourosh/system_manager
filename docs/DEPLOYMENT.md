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
# Open http://localhost:4000/
```

### Configuration
Create `.env` (optional):
```bash
# .env
DISABLE_AUTH=1          # Set to 0 to enable access code (production)
# HOST_ROOT=/host       # Already set in docker-compose.yml
# SYSTEM_MANAGER_AUTH_TOKEN_PATH=/data/access-code   # when auth enabled
```

### Volumes
| Host Path | Container Path | Mode | Required |
|-----------|----------------|------|----------|
| `/proc` | `/host/proc` | ro | Yes |
| `/sys` | `/host/sys` | ro | Yes |
| `/etc` | `/host/etc` | ro | Yes |
| `/run/user` | `/run/user` | ro | Yes (user systemd) |
| `/var/run/dbus` | `/var/run/dbus` | ro | Yes (NM, systemd) |
| `/sys/class/dmi` | `/sys/class/dmi` | ro | Yes (hardware IDs) |

### Capabilities
| Capability | Purpose |
|------------|---------|
| `SYS_ADMIN` | `statvfs` on host root, `/proc` access |
| `SYS_RESOURCE` | `getloadavg()`, resource limits |
| `NET_ADMIN` | `nmcli` network operations |
| `CAP_DAC_READ_SEARCH` | Read root-owned files via mounts |

### Network & PID
- `network_mode: host` — Direct access to host network stack
- `pid: host` — Access to host process namespace (for `--user` systemd)

### Restart Policy
```yaml
restart: unless-stopped
```

### Health Check
```bash
docker exec system-manager curl -sf http://localhost:4000/api/status | jq -e '.state == "observed"'
```

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

The standalone stdlib panel (connectivity/actions reference) is still:
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