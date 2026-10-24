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
# The container runs as your host user, so this reads it as you:
docker compose exec system-manager cat /data/access-code
```

The access code **rotates on every successful login** — re-read the file
whenever you need to log in again.

> **The action features work on a stock `compose up` as of 2026-09-26.** Service
> restart and NetworkManager profile listing return `{"state": "observed"}` with
> real host data. Two prerequisites ship in `docker-compose.yml` already — the
> correct D-Bus socket path and `security_opt: [apparmor=unconfined]`; see the
> AppArmor section below, since removing that line breaks profile listing again.
> One thing still does **not** work: NM checkpoint rollback, which needs a
> newer nmcli or a polkit rule, so `nm_activate` fails closed. See
> CONTINUATION.md for the full write-up.

Authentication is **on by default**. A clone with no `.env` starts locked; the
access code is written to `./data/access-code` on the host (and regenerated on
each successful login, so a leaked file alone is not enough). Because the
container runs as `${UID}` the file is mode `0600` and owned by you — read it
with `docker compose exec` or `cat`, and keep it out of version control.

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

`DISABLE_AUTH=1` is for local development only. The container runs with
`SYS_ADMIN`, `NET_ADMIN` and a read-only `/etc` mount, so with auth disabled
every `/api/*` endpoint — including
`POST /api/execute`, which runs `systemctl` and NetworkManager commands — is
reachable by anything that can reach port 4000. Leave it off unless you are
running on an isolated network.

### Volumes

| Host Path           | Container Path         | Mode | Required                                                                        |
| ------------------- | ---------------------- | ---- | ------------------------------------------------------------------------------- |
| `/proc`             | `/host/proc`           | ro   | Yes                                                                             |
| `/sys`              | `/host/sys`            | ro   | Yes                                                                             |
| `/etc`              | `/host/etc`            | ro   | Yes                                                                             |
| `/run/user`         | `/run/user`            | ro   | Yes (user systemd)                                                              |
| `/var/run/dbus`     | `/var/run/dbus`        | ro   | Yes (NM, systemd) — see the note below                                          |
| `/sys/class/dmi`    | `/sys/class/dmi`       | ro   | Yes (hardware IDs)                                                              |
| `./data`            | `/data`                | rw   | Yes (access code, audit + inventory DBs)                                        |
| `./commands_to_run` | `/app/commands_to_run` | rw   | Folder Organizer proposals, written by the app and applied by host-side scripts |
| `/media/kourosh/Multimedia/Music` | `/media/kourosh/Multimedia/Music` | rw | Music files — the app writes tags to them and serves album covers |

The Music mount is the same path inside and out, so `media_path.py`'s rewrite
needs no change. It is what lets the app write tags itself instead of handing
back a script for the user to run; without it the Music page still lists and
proposes, but every write fails with `missing`.

### Host-side organizer tools

These run on the host, not in the container:

```bash
python3 music_tag_audit.py          # re-audit every music file (slow; ~10 min)
python3 artist_images.py --limit 500  # fetch artist photos into data/artist_images/
python3 musicbrainz_lookup.py --limit 200  # MusicBrainz candidates for the tags page
```

`music_tag_audit.py` should be re-run before trusting the untagged badges:
the audit is a snapshot, and a file tagged after the last run still shows as
untagged until the audit is refreshed.

### Capabilities

| Capability            | Purpose                                |
| --------------------- | -------------------------------------- |
| `SYS_ADMIN`           | `statvfs` on host root, `/proc` access |
| `SYS_RESOURCE`        | `getloadavg()`, resource limits        |
| `NET_ADMIN`           | `nmcli` network operations             |
| `CAP_DAC_READ_SEARCH` | Read root-owned files via mounts       |

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
defensible here only because the container already holds `SYS_ADMIN`,
`SYS_RESOURCE`, `NET_ADMIN` and `CAP_DAC_READ_SEARCH`; see the
limitations section in CONTINUATION.md before enabling it anywhere else.

### Container User

The container runs as `${UID}:${GID}` (your host user, 1000 by default) rather
than root. The Folder Organizer checkout is mounted `rw` because the app writes
tag plans, proposals and exports into it, so without this every saved file would
land in that project owned by root and become awkward to edit or delete from the
host. Host sensing still works: it reads through the `/host/*` bind mounts, not
privileged syscalls, and `CAP_DAC_READ_SEARCH` covers the few root-owned files.

### Docker socket (read-only, for container names and compose projects)

The network topology page shows the compose project each container belongs to.
That label lives only in Docker's own database, so the socket is mounted
read-only and the host's docker GID is added so the container uid can read it:

```yaml
group_add:
  - "4"
  - "${DOCKER_GID:-984}"          # docker GID on the host — check `getent group docker`
volumes:
  - /var/run/docker.sock:/var/run/docker.sock:ro
```

The module issues a single `GET /containers/json` and never writes or starts
anything. If the host's docker GID is not 984, set `DOCKER_GID` in `.env`.
Without the socket, the topology still renders — containers just lose their
names and project labels.

Two consequences worth knowing:

- The access code at `./data/access-code` is written mode `0600` by the app, so
  it stays readable only by this uid. If you switch back to root, `chown` that
  file to root or login will fail with "Could not write the access code".
- Files created by the _action_ features may still land root-owned, since some
  of those paths talk to host services.

### Network & PID

- Bridge networking with `ports: ["4000:4000"]` — port published to the host.
  Previously `network_mode: host`; switched via the project dashboard
  (`/ports` → Network → _Switch to bridge_), which added the port mapping.
  Port 4000 is unchanged, so every URL above still works.
- `pid: host` — Access to host process namespace (for `--user` systemd)

If you switch back to host networking, drop the `ports:` block. Note that under
host mode `localhost` inside the container is the host, which any code reaching
a host-local socket depends on — check `HOST_ROOT`/dbus paths still resolve.

### DNS Resolution Paths

Keep the resolver address used by applications separate from the upstream
servers configured in dnsmasq:

- Host applications using the system resolver read `/etc/resolv.conf`. On the
  deployment host documented here, it contains `nameserver 127.0.0.1`, and
  dnsmasq listens on that loopback address.
- dnsmasq's global upstreams come from `server=` entries in
  `/etc/dnsmasq.conf` and its included configuration files. `server=/domain/...`
  entries are per-domain overrides, not global upstreams. At the time this
  host was checked (2026-10-18), its global upstreams were
  `78.157.42.100` and `178.22.122.100`; these are host settings, not project
  defaults.
- Network Settings can still show DNS servers received by a link from DHCP.
  That list describes link configuration; it does not by itself establish which
  resolver a particular application uses. On this host, systemd-resolved's
  global `~.` route points at `127.0.0.1`, while the `eno1` DHCP list remains
  visible. Check both `resolvectl status` and `/etc/resolv.conf` when diagnosing.
- Bridge-network containers normally use Docker's embedded resolver at
  `127.0.0.11`. Docker answers container/service names itself and forwards
  external DNS queries to the host resolver it learned from the host's
  `/etc/resolv.conf`. On this host that resolver is `127.0.0.1`, so external
  container DNS follows the host dnsmasq path. A container using `network_mode:
host` instead sees the host resolver directly.
- Applications or containers with their own proxy, VPN, or DNS-over-HTTPS
  settings can use a different path. V2RayN's own DNS rules are independent of
  dnsmasq; changing host dnsmasq does not rewrite those rules.

Read-only checks:

```bash
cat /etc/resolv.conf
resolvectl status
grep -E '^[[:space:]]*(no-resolv|server=|conf-dir=|conf-file=)' /etc/dnsmasq.conf
docker exec system-manager cat /etc/resolv.conf
dig @127.0.0.1 example.com
```

### Restart Policy

```yaml
restart: unless-stopped
```

### Health Check

The image is `python:3.14-slim` plus iproute2/net-tools/network-manager/systemd,
so **`curl` and `jq` are not installed inside the container**. Check health with
Python, which is present:

> `iproute2` is still required, by `server.py`'s connectivity probes, which shell
> out to `ip -j`. The `/network` module does **not** use it — it parses procfs,
> because `ip` inside a container reports the container's own stack, not the
> host's.

```bash
docker exec system-manager python3 -c "import urllib.request,sys,json; d=json.load(urllib.request.urlopen('http://localhost:4000/health')); sys.exit(0 if d['status']=='ok' else 1)"
```

It exits 0 when healthy, 1 otherwise.

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
# Authorisation is on by default; set DISABLE_AUTH=1 to skip the access code.
```

There is no standalone panel any more. `server.py` is a library of read-only
collectors, the connectivity probes and the action/audit primitives, imported
by `system_manager/`; it has no `__main__` and no HTTP server, so the only way
to run the dashboard is `run.py` or the container.

### Environment Variables

```bash
DISABLE_AUTH=1 python3 run.py                       # Skip login (Flask)
SYSTEM_MANAGER_AUTH_TOKEN_PATH=/data/access-code    # Write rotating code (Flask)
HOST_ROOT=/host python3 run.py                      # Container-style paths
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
ExecStart=/usr/bin/python3 /opt/system-manager/run.py
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
# Access at http://127.0.0.1:4000/
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
docker exec system-manager /usr/bin/nmcli -t -f NAME,TYPE,DEVICE con show
docker exec system-manager /usr/bin/systemctl --user list-units --type=service
# The network module parses procfs, so verify the host's view, not `ip`.
# `ip` inside the container reports the container's own 2 interfaces; the
# /network page reports the host's ~53.
docker exec system-manager ls /host/proc/1/net
docker exec system-manager head -3 /host/proc/1/net/dev
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

Profile _listing_ and service listing both work on a current `compose up`. The
three causes below were the reasons they did not, and are kept because they
recur whenever a host is set up differently, or when a `security_opt` line gets
dropped. Each one masks the next, so diagnose in order.

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

**3. `systemctl --user` cannot find the user bus.** Affects services, not
NetworkManager. The symptom names the fix but not the real cause:

```bash
docker exec system-manager systemctl --user list-units --type=service
# Failed to connect to user scope bus via local transport:
# $DBUS_SESSION_BUS_ADDRESS and $XDG_RUNTIME_DIR not defined
```

It is **not** a uid problem — the container runs as your host uid already (see
`user:` in `docker-compose.yml`). `systemctl` locates the per-user bus through
`XDG_RUNTIME_DIR`, and `auth.py` was passing a minimal `env={"PATH", "LC_ALL"}`
to every subprocess, which stripped that variable out. `auth.py:_subprocess_env()`
now builds the environment per call and adds `XDG_RUNTIME_DIR` and
`DBUS_SESSION_BUS_ADDRESS` when `/run/user/<uid>/bus` exists. Verify:

```bash
curl -s -b "$COOKIES" http://localhost:4000/api/services | head -c 120
# {"state":"observed","value":[{"active":"active","description":"...
```

> If a fix in `system_manager/` appears to do nothing, remember the app is
> `COPY`'d into the image rather than bind-mounted — run
> `docker compose build && docker compose up -d` before testing.

### NetworkManager activation refused

```bash
# "NetworkManager checkpoint not available; safe rollback cannot be guaranteed."
```

Not fixable from the container, and not an AppArmor or D-Bus problem. Neither
nmcli implements `con checkpoint` (container ships 1.52.1, host has 1.46.0), and
polkit refuses the `CheckpointCreate` D-Bus call because
`org.freedesktop.NetworkManager.checkpoint-rollback` defaults to
`auth_admin_keep` while the shipped NM rules grant only
`settings.modify.system`. Reproduce on the host to confirm it is not a container
issue. Unblocking needs a polkit rule or dropping the checkpoint requirement —
both are security decisions, so `nm_activate` fails closed meanwhile.

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
