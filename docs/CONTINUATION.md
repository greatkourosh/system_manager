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
- **Tests:** 59 passing — 24 `test_server.py` + 11 `test_connectivity.py` (config, scan cadence, endpoint validation, API auth) + 10 `test_flask_app.py` (auth + approval lifecycle) + 9 `test_lock.py` (module blueprint gating) + 5 `test_inventory.py` (store + API)

### 2026-09-26 — Live Verification Pass (Milestone 6, docs only)
Ran the full suite (59 pass) and exercised the running container end-to-end
against the real host — login, `/api/status`, `/api/connectivity`,
`/api/audit`, `/inventory/`, `/api/services`, `/api/profiles`.

**Verified working:** dashboard observation (returns real host data —
`ASUS`, `i5-13400F`, 649 GB free), network, connectivity, audit, inventory,
auth (401 before login, session cookie after, code rotates on login).

**Found broken — the action features (3 independent bugs, all reproduced):**
1. `docker-compose.yml` sets `DBUS_SYSTEM_BUS_ADDRESS=unix:path=/host/run/dbus/system_bus_socket`,
   but only `/host/{etc,proc,sys}` are mounted — the socket is at
   `/run/dbus/system_bus_socket`.
2. AppArmor's `docker-default` profile denies D-Bus, so `nmcli` fails with
   `AccessDenied: An AppArmor policy prevents this sender...` even with the
   path fixed. Needs `security_opt: [apparmor=unconfined]`.
3. `system_manager/auth.py` calls `systemctl --user` as root; the user bus
   refuses root (`Transport endpoint is not connected`). Same command works
   as uid 1000, or over systemd's `--machine=<user>@.host` proxy as root.

Fixes 1–2 are container config and are now documented (not yet applied — the
AppArmor change loosens a security boundary and was left for an explicit
decision). Fix 3 needs a code change in `auth.py`. **This is the top open
item**; see "Next Steps".

### 2026-09-26 — Module Loading & Session Fixes (Milestone 7)

Reported as "modules at the dashboard don't work" and "clicking on modules,
nothing happens". Four independent bugs, each verified against the live
container:

1. **Folder Organizer was never mounted** (`8f56cfc`). `organizer.py` imports
   `../folder_organizer/app.py` in-process from `/folder_organizer`, but the
   Dockerfile only `COPY`s `system_manager/`, `templates/` and `static/`, so
   the module was unavailable in the container — greyed out on the dashboard,
   503 on every route — while working on the host. Fixed by bind-mounting the
   sibling checkout. It is `rw` because the app writes proposals, tag plans and
   exports into its own `data/` and `commands_to_run/`; it only ever *reads*
   the media library, so the library itself needs no mount.
2. **The organizer's buttons did nothing** (`54d3f4e`). Its templates hardcode
   `fetch('/api/...')` and a local `post('/api/...')` helper in inline scripts.
   The proxy rewrote `href`/`action`/`src` but not JS string literals, so those
   calls hit the host app's root and 404'd — 24 call sites across 5 pages. The
   rewriter now prefixes them, since the module's checkout is never modified by
   contract. Six tests added; the rewriter previously had none.
3. **Root-owned files in the organizer** (`838aa48`). The container ran as root,
   so every tag plan and proposal it saved landed in that project owned by
   root. Now pinned to `${UID}:${GID}`. Host sensing is unaffected — it reads
   through the `/host/*` mounts rather than privileged syscalls.
4. **A second login killed the first session** (`19b1a3b`). `login()` assigned a
   fresh dict to `self.sessions`, so unlocking in a new tab logged out every
   other one, and each module link bounced back to the dashboard root — the same
   symptom as bug 1 but a different cause. Now inserts the token instead.

Verified after the fixes: all 12 pages return 200 while authenticated, a real
organizer write lands `kourosh`-owned, two concurrent sessions both stay live,
and host sensing still reports the real machine. 66 tests pass.

**Diagnostic note:** "module link does nothing" is ambiguous between a missing
mount and an expired session, and the dashboard renders both the same way. A
per-module 503 carrying a `detail` field means the loader; a uniform 302/401
across all modules means auth. Check the session before editing a loader.

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
docker compose up -d --build
# Open http://localhost:4000/ and unlock with the access code:
docker compose exec system-manager cat /data/access-code
# The code rotates on every successful login.
```

### Tests
```bash
python3 -m pytest -q
# 66 tests + 40 subtests, ~4s
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

**Vault path:** `/media/kourosh/DEVNVME/projects/kourosh_vault` (the active
vault; a separate git repo, not a submodule of this project).

| File | Purpose |
|------|---------|
| `docs/SYSTEM-MANAGER.md` | Vault-side note: what the project is, how to run it, and its known problems |

The vault follows a one-note-per-project convention (`NEXUS-MANAGER.md`,
`REVERSE-PROXY.md`, …) rather than mirroring the full `docs/` tree, so the
detail lives here and the vault note links back to it. The
`./scripts/sync-to-obsidian.sh` described in earlier revisions was never
written; `scripts/` does not exist in this project.

---

## Git Status

Clean through `19b1a3b` ("Keep existing sessions alive when a second one logs in"). Milestone 5 completed in `d1ac423`. The 2026-09-26 verification pass (Milestone 6) was documentation-only. A later pass on the same day fixed four container/host bugs — see the Milestone 7 entry. 66 tests pass.

> **Three known bugs are documented but unfixed** (see the Milestone 6 entry): the D-Bus path, the AppArmor denial, and the root/`systemctl --user` mismatch. Actions are unavailable in the container until all three are addressed. The container no longer runs as root, so cause 3 now needs the `--machine=<user>@.host` form rather than a plain `systemctl --user`.

> `core.filemode` is set to `false` on this clone, so the older repo-wide `100644 → 100755` mode flips no longer appear in diffs.

---

## Next Steps

- ~~Review + commit the Flask port~~ — done in `ac47c92`
- ~~Wire the connectivity diagnostics into the Flask dashboard~~ — done, see Milestone 5
- ~~Enforce login redirect / lock the whole app when auth is on~~ — done; see `tests/test_lock.py`
- ~~Commit the Milestone 5 remainder~~ (connectivity port + lock) — done in `d1ac423`
- **Fix the action features in the container** — highest priority. In order:
  1. `docker-compose.yml`: `DBUS_SYSTEM_BUS_ADDRESS` → `unix:path=/run/dbus/system_bus_socket`
  2. `docker-compose.yml`: add `security_opt: [apparmor=unconfined]` (a deliberate security-boundary loosening — decide explicitly before shipping)
  3. `system_manager/auth.py`: stop calling `systemctl --user` as root. **The
     symptom changed but is not fixed** by `838aa48`. As `${UID}` it now fails
     differently — `Failed to connect to user scope bus via local transport:
     $DBUS_SESSION_BUS_ADDRESS and $XDG_RUNTIME_DIR not defined` — because the
     container has no session bus. `pid: host` alone is not enough. Switch to
     `systemctl --machine=<user>@.host --user` over the system bus, which needs
     the `/run/user` and `/var/run/dbus` mounts already present plus cause 1
     fixed. Whichever is chosen should be covered by a test — the current suite
     is green *because* it never exercises a real bus.
- Decide: retire `server.py`'s standalone panel now that Flask covers all of it, or keep it as a thin wrapper over `system_manager`
- Package & update management (#2) is the highest-value next feature

### Requested: richer filters + sorting on the Video Library page

**Where the code lives:** this is a change to the **`folder_organizer` checkout**,
not to this repo. `data/video_library.json`, `app.py` (`/videos`), and
`templates/videos.html` are all in `../folder_organizer`, which is bind-mounted
into the container. `system_manager/organizer.py` only proxies it, so the host
app needs no change (the filters are plain query params, so no new `fetch`/`post`
call sites and no edit to the `_JS_CALL_RE` rewrite).

**Today's filters** (`app.py:99-135`): `q` (title substring), `root`
(all/movies/serials/videos), `genre` (exact match against a comma-split list),
`sub` (all/missing-fa/missing-en/missing-both), `page`. 446 cards,
`VIDEO_PAGE_SIZE = 36`. There is **no sort and no range filter at all** — the
card list is emitted in raw file order, and paging is index-based off that order,
so sorting has to be applied before the page slice to stay stable across pages.

**Data available today** (from `data/video_library.json`, built 2026-09-17):

| Field | Populated | Type | Notes |
|-------|-----------|------|-------|
| `year` | 286/446 (64%) | **str** | Range 1939–2025 |
| `rating` | 388/446 (87%) | **str** | Range 4.8–9.3, every value matches `\d\.\d` |
| `pop` | 385/446 (86%) | **str** | Range 30–100 |
| `genres` | 396/446 (89%) | str | Comma-separated, multi-valued |
| `title` | 446/446 | str | |

Card keys are exactly: `dir, exts, folder, genres, has_en_sub, has_fa_sub, id,
kind, limited, pop, poster, poster_id, rating, root, sample_video, season,
seasons, sub_count, title, total_bytes, video_count, year`.

**Gotchas to design around**

- `year`, `rating` and `pop` are **strings, not numbers** — every comparison and
  range boundary needs an explicit float()/int() coercion, and blanks are `''`
  or `None`, not `0`. A naive `float(c['year'])` raises on the 160 blank cards.
- The 160 cards with no year are **all 124 serials + 34/35 `videos`** + 2 movies.
  Any "unknown year" bucket therefore skews heavily toward serials, and sorting
  by year descending would bury every serial at the bottom unless blanks are
  explicitly placed rather than merely defaulting.
- `pop` is scraped from the folder name, not from a provider — it collides with
  the name-collision detection above it in `video_catalog.py:_strip_trailing_nums`
  (`\s+(?P<rating>\d\.\d))?(?:\s+(?P<pop>\d{1,3}))?`), so treat it as low-trust.
- `genres` is a comma-joined string; the existing filter already splits on `,`
  and trims. Reuse that rather than adding a second genre matcher.

**Suggested filters** (each needs a control in `videos.html` *and* a branch in
`applyFilter()`, which rebuilds the query string from the four existing
`getElementById` lookups and would otherwise silently drop the new params):

1. **Year** — decade buckets (data is 171× 2020s, 69× 2010s, 25× 2000s, 12×
   1990s, 5× 1980s, 1 each 1930s–1970s, 160 unknown), or a min/max year range
   with an explicit "unknown" opt-in. Decades suit the distribution; a
   year-to-year list does not.
2. **Rating (IMDb-style)** — the folder name carries a `\d\.\d` token, and the
   scanner labels it `rating`. It is *not* sourced from IMDb — nothing in
   `folder_organizer` references imdb. Minimum-rating slider or bucketed select
   (e.g. ≥9, ≥8, ≥7, ≥6).
3. **Kind** — `movie` (322) vs `serial` (124), currently only reachable indirectly
   via `root`.
4. **Size / episode count** — `total_bytes` and `video_count` are numeric-typed
   and fully populated; a cheap "5+ episodes" or "multi-GB" filter for serials.
5. **Sort** — by year, rating, title, size, episode count, ascending and
   descending. Default to the current file order so the page looks unchanged
   until a sort is picked.

**Rotten Tomatoes is not available.** There is no RT field in
`video_library.json` and nothing in the codebase fetches one; the `tomato` grep
hits in `data/*.json` are folder names ("Tomatons"). `tmdb_client.py` already
calls TMDB for posters, and TMDB supplies `vote_average` — that is a different
number from both IMDb and Rotten Tomatoes, and it would be a *new* upstream
field, not a filter over existing data. Treat RT (and a true IMDb rating) as a
separate data-acquisition task: decide the provider and its API key/rate limit
before promising a score, and write it back into `video_library.json` via the
existing poster-cache pattern (`tmdb_client.py` → `data/posters_state.json`)
rather than calling out to the network on page render. If neither is wanted,
ship 1–5 and drop the RT idea — the page is still much more useful.

### Requested: auto-download subtitles for series/movies missing them

For every card on `/organizer/videos` that lacks a subtitle, add an option to
auto-fetch it. **A fetcher already exists and is not wired to the page** —
`folder_organizer/fetch_subtitles.py` targets exactly this set, and has run
successfully: `commands_to_run/subtitle_log.txt` records **45 `FETCHED` lines**
against 59 `FAIL`/`NO-RESULT` (last run 2026-09-02, `mode=APPLY: fetched=25
failed=1 checked=26`). The work is therefore to expose it as a UI action, not
to write a downloader.

**Where code lives:** the **`folder_organizer` checkout**, not this repo.
`app.py` (new `POST` route), `templates/videos.html` (per-card control),
`fetch_subtitles.py` (reused as a library, or shelled out to). Because a new
`post('/api/...')` call site is involved, `system_manager/organizer.py`'s
`_JS_CALL_RE` already covers it — the regex prefixes any `/…` literal that is not
already under `/organizer`, so a new endpoint is rewritten for free. No change
to this repo needed.

**The blocker to resolve first — the media library is not reachable from this
host.** `data/video_library.json` paths are **Windows**, e.g.
`G:\Movies\1939 - Gone with the Wind …\Gone with the Wind.avi`, and
`os.path.exists()` on that string returns `False` here. `config.json` lists
Windows roots (`roots_windows`) and Linux roots (`roots_linux`) side by side;
the library was scanned on Windows and copied over. There is no `/mnt/g`, no
`/media/G`, and no `G:` entry in `/etc/fstab`. The 45 past downloads were
written on the Windows machine, not here.

So a fetch button served from the container cannot write next to the video
files — there is no media mount, and `docker-compose.yml` deliberately does not
add one (it only ever *reads* the library, and `system_manager`'s own contract is
that the library needs no mount). Decide before building:

1. **Run the fetch host-side, UI only queues it.** The container writes a job
   to `commands_to_run/`; something on the host with `G:` access runs
   `fetch_subtitles.py --apply`. Needs a runner, but matches how the organizer
   already treats its media.
2. **Mount the library into the container and translate the paths.** Only viable
   if `G:` is reachable *somewhere* to mount. It currently is not — nothing to
   mount. Would also need `G:\…` → mount-point prefix rewriting, since the
   stored paths are Windows regardless.
3. **Drop the write, keep the lookup.** The page can search OpenSubtitles and
   show candidates/links for manual download. No quota, no path problem, but the
   user still downloads by hand.

Options 1 and 2 both burn the **free-tier quota of ~20 downloads/24h** — the
existing script's own docstring, and why it has `--budget`. Any UI must expose
that as a per-run cap and default to **dry-run**, not silently fire 410 cards.

**Scale of the target set** (from `data/video_library.json`, 2026-09-17):

| | Count |
|---|---|
| Cards missing FA **or** EN | **410 / 446** |
| — `movies` | 270 of 287 |
| — `serials` | 108 of 124 |
| — `videos` | 32 of 35 |

410 at ~1.1 s/rate-limited request and a 20/24h quota is **~20 days** of manual
runs; the page should say so rather than offering a one-click "fetch all".

**Where the data falls short for this task.** The fetcher resolves per-video
paths as `c.get("videos") or [{"path": c["sample_video"]}]` — but **0 of 446
cards carry a `videos` key** in the current build. Every card therefore falls
back to `sample_video`, so one download per card: a 24-episode serial gets one
subtitle, not 24. Fetching all episodes needs either a re-scan that emits
`videos[]` (the scanner already groups by main item — see commit `bfc1938`,
782→446 cards) or a re-glob of `dir` at fetch time. The former is the right
fix; the latter is a one-liner and unblocks the feature now.

**Already filtered for you.** The page's `sub` param (`app.py:110-128`) already
does `missing-fa` / `missing-en` / `missing-both`, so a "fetch what's missing"
action can be scoped to the current filter with no new query param. The card
badges at `videos.html:59-61` already show FA/EN state per card, so the button
can sit right there and only appear when a badge is missing.

### Requested: series-state badges + a "Recommended" badge

Same placement as the task above — a change to the **`folder_organizer`**
checkout (`templates/videos.html` for the markup, `app.py:/videos` if a helper
is needed), not to this repo. Pure presentation: no new query params, no new
`fetch`/`post` call sites, so the `_JS_CALL_RE` rewrite needs no edit.

The card badge row is already there — `videos.html:59-61` renders
`FA` / `EN` / `{{ c.video_count }} vid` as Bootstrap `badge`s. New badges slot
in there; the "x of y seasons" text wants its own line under the title, not a
badge, since it is a sentence rather than a state.

**Task A — "Season N of M" on serials.** `season` and `seasons` come from the
`S01 of 03+` token in the folder name (`video_catalog.py:16`, `SERIAL_PAT`), and
are **2-char strings, not ints** — `'01'`, `'02'`… `'10'`, or `None`. There is
no missing-season list, so the honest rendering is "we hold season 3 of 5",
**not** "seasons 4 and 5 are missing". Coverage of the 124 serials:

| Case | Count | `limited` | Render as |
|------|-------|-----------|-----------|
| `season` == `seasons` | 31 | `False` | "Complete" badge, no counter |
| `season` < `seasons` | 39 | `False` | "3 of 5 seasons" + "In progress" |
| `seasons is None`, `- Limited` in name | 45 | `True` | "Limited series" badge |
| `seasons is None`, neither | 9 | `False` | no badge — see below |

So 70/124 serials can show a real "N of M" and 45 more are cleanly identifiable
as limited; the remaining 9 are genuinely ambiguous. Three of those are
**malformed folder names** that the regex cannot parse — `Fallout  S01 of`,
`Taboo S01 of`, `The Day Of The Jackal S01 of` (note the trailing space, and no
count after `of`). These are multi-season serials that fall through to the
`YEAR_TOKEN` branch in `parse_serial`, which sets `limited: None`… in practice
`False`, because the fallback hardcodes `d["kind"] == "Limited"` against a `None`
group. So `limited=False` on a serial with no season data is **"unparseable
name", not "open-ended show"** — do not render a "Complete" badge off it. The
other six are single-season shows (`Family Guy`, `Sherlock`, `The Great`, …)
where no badge is the right answer.

**Task B — series-state badges.** Three states, all derivable per card with no
extra data:

- **Limited series** — `limited is True`. 49 cards, all serials, 45 of them with
  no season count (so the badge is the *only* signal on those).
- **Complete** — `limited is False` and `seasons` is not `None` and
  `int(season) >= int(seasons)`. 31 cards. Both fields must be `int()`-coerced
  or `'10' <= '09'` sorts wrong lexicographically.
- **In progress** — `limited is False` and `int(season) < int(seasons)`. 39 cards.

`limited` is tri-state (`True`/`False`/`None`); `None` means "a movie, or a
serial whose folder name did not parse". Movies have no `season`/`seasons` at
all, so none of these three badges should ever apply to them.

**The `+` marker is the one trap.** `S01 of 03+` means "more seasons are
planned" and is captured by the `\d+\+?` in `SERIAL_PAT`, but the trailing `+`
is **discarded** — it is not stored on the card. 36 folders carry it. Of those,
30 are `season < seasons` and **6 are `season >= seasons`**, i.e. the `+`
disagrees with the counter. Re-derive it with a regex over `folder` if the
planned-seasons state is wanted, and treat the 6 disagreements as a data-quality
finding to report rather than silently prefer one field.

**Task C — "Recommended" for every kind (movies, serials, videos).** There is
no recommendation field; this has to be derived, and "recommended" needs a
definition before it is written. `rating` and `pop` are the only ranking
signals, both **strings** needing `float()`, both flagged as low-trust above
(`pop` is scraped off the folder name by a regex that can misattribute). Their
distribution is too skewed for an absolute threshold to be interesting — a
"rating ≥ 9" badge would hit 5 cards, all serials:

| Threshold | Cards | Breakdown |
|-----------|-------|-----------|
| `rating >= 9.0` | 5 | 5 serials, 0 movies — useless |
| `rating >= 8.5` | 30 | 4 movies, 26 serials |
| `rating >= 8.0` | 106 | 41 movies, 65 serials |
| `pop >= 85` | 245 | 166 movies, 79 serials |
| `rating >= 8.0 and pop >= 85` | 91 | ~20% of the library — the useful band |

**Recommended:** one rule, `rating >= 8.0 and pop >= 85`, ≈91 badges across all
three roots, so it reads as a genuine shortlist rather than decoration. Because
both inputs are low-trust, show the numbers next to the badge rather than a
bare "Recommended" — the page already has the space under the title. State
plainly in the UI that this is a heuristic over folder-name metadata, not a
curated or provider-sourced score, or it will be read as an editorial claim.

**Gotchas shared by all three tasks**

- `kind` is only ever `movie` (322) or `serial` (124) — there is no third
  value. "Other" is the `root=videos` section (35 cards), and all 35 of them
  are `kind=movie`. So Task C's "all types of videos" means movies, serials and
  the `videos` root, which is a `root` distinction, not a `kind` one.
- `season`/`seasons`/`rating`/`pop` are all strings or `None`; every comparison
  needs an explicit coercion, and there is no numeric zero to fall back on.
- No serial title repeats across cards (0/124 duplicates), so a per-card badge
  is safe — no need to aggregate across a series first.
- Badges are Bootstrap `.badge` + `bg-*` in the existing row, so reuse those
  classes; add a couple of new colour keywords rather than inventing a palette.