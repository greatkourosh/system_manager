# Docker deployment — Windows and Linux

## Requirements

- Docker Engine with Compose on Linux, or Docker Desktop using Linux containers on Windows 11.
- A checkout of this repository and a local `.env` file with your API settings. Keep `.env` out of Git and image layers.
- Access to the LAN registry `192.168.1.40:5001`, or use the public image override below.

The image runs Gunicorn as UID/GID 10001. Compose publishes only to localhost, defaults to port 5001, drops capabilities, and disables privilege escalation. `/health` is a process-liveness check, not a storage or external-API readiness check.

## Existing installations: migrate before starting

The previous relative bind mounts already persisted data on both platforms. Named volumes are an explicit storage change, not a prerequisite for Windows support. A fresh named volume is empty; existing `./data`, `./commands_to_run`, and `./logs` directories are NOT imported automatically.

Do not run the start command below over an existing installation until you have backed up these directories and planned migration. Stop the old writer first. Keep the same Compose project name for subsequent runs.

For each directory, copy the backed-up contents into its corresponding named volume using a one-off migration container with the source bind-mounted read-only. Set the copied files' ownership to 10001:10001, verify counts/content, and retain the host originals. Do not copy secrets into the volumes. This session has not migrated any existing data.

Exported command files now live in `media_commands`, not the host `commands_to_run` directory. Copy individual exports back to the host for review and execution with the host-side apply scripts. Never execute deletion lists merely because the original proposal calls them safe.

## Start a new or migrated installation

Run from the repository root in PowerShell or a Linux shell:

```bash
docker compose up --build -d
```

Open http://localhost:5001. Check status:

```bash
docker compose ps
```

Set `MEDIA_ORGANIZER_PORT` in the shell or `.env` to select another host port. Both hosts run independently; no state is synchronized between them. Keep the localhost binding unless you deliberately secure remote access; the dashboard has no authentication.

## Base-image override

The default LAN Python image is pinned by digest. There is no automatic registry fallback. To use Docker Hub, set this non-secret build setting in `.env` and rebuild:

```text
PYTHON_IMAGE=python:3.12-slim@sha256:2fe5997d249a808b8eeea52c58a1dbffbba28754dc11699ef5c029f2d818ce79
```

The digest was obtained from the locally available image's repository metadata. Availability from Docker Hub and a Windows-host build still require verification.

## Persistence and host operations

- `media_data` → `/app/data`: catalogs and review state.
- `media_commands` → `/app/commands_to_run`: exported plans.
- `media_logs` → `/app/logs`: logs.

Compose prefixes volume names with the project name. Use the same project name after moving the checkout. Rebuilding or recreating a container preserves these volumes; `docker compose down --volumes` removes them and must not be used for routine updates. Back up volumes separately.

Fresh volumes inherit the image directory ownership. Existing root-owned volumes require a deliberate ownership migration; the app never starts as root to repair them automatically.

Source code, templates, and schema configuration are baked into the image; rebuild after changing them. No host media drives are mounted. Run scanners, taggers, subtitle downloaders, and apply scripts on the host with their own dependencies. A Linux container on Windows still reports Linux; setting a `PLATFORM` variable does not change scanner behavior. `steam_ubuntu` is outside organizing scope per the user's direction; this Docker change does not implement scanner exclusions.

## Verification status — 2026-09-17

- Isolated Linux Compose build/start and Docker healthcheck passed.
- Non-root UID 10001, environment injection (without printing values), and writes to all three fresh named volumes passed.
- Existing data was not migrated or replaced.
- Windows 11 validation and production migration remain pending; dual-host sign-off is not claimed.
