# System Manager — Continuation & Documentation

## Project Overview
Local system management dashboard for Linux (Ubuntu 24.04+), running in a container with host access. Provides read-only system observation, opt-in connectivity diagnostics, and approved actions with audit logging.

**App:** Flask app (`system_manager/`) hosting feature modules as blueprints, served by gunicorn in the container and `run.py` in dev.
**Collector library:** `server.py` (stdlib-only, 540 lines) is a library, not an app — connectivity, actions and audit all live in `system_manager/`, which imports its collectors. Its standalone HTTP panel was retired 2026-09-26.

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
  - `organizer/` — the media organizer blueprint under `/organizer`, 37 routes ported from the sibling app (was: an in-process proxy that rewrote its HTML)
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

Fixes 1–2 were container config and are **now applied**; fix 3 was resolved in
`auth.py`, but by a different cause than first diagnosed (see Milestone 8). All
three are verified against the live bus.

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
and host sensing still reports the real machine. 66 tests pass at that point.

**Diagnostic note:** "module link does nothing" is ambiguous between a missing
mount and an expired session, and the dashboard renders both the same way. A
per-module 503 carrying a `detail` field means the loader; a uniform 302/401
across all modules means auth. Check the session before editing a loader.

**Diagnostic note:** media paths in `video_library.json` are **Windows** (`G:\…`)
and will *always* fail `os.path.exists()` on this host — the drive is really at
`/media/kourosh/Multimedia`. That is not a missing library and not a broken
scan; 424/446 cards resolve once `G:` is stripped and `\` swapped for `/`. Do
not conclude the library is absent, and do not "fix" the scanner, before trying
the translation. (Details in the subtitle task under Next Steps.)

### 2026-09-26 — Action Features Fixed Against a Live Bus (Milestone 8)

Closes the three bugs Milestone 6 found, each re-probed inside the running
container before and after. The app is `COPY`'d into the image, so **a code
change needs `docker compose build` + `up -d`** — editing the file on the host
silently changes nothing, which is what made the first attempt look unfixed.

1. **D-Bus path pointed into a prefix that isn't mounted.** The socket is
   mounted at `/var/run/dbus`, and `/var/run` is a symlink to `/run` inside the
   image, so the real path is `/run/dbus/system_bus_socket`. The old
   `unix:path=/host/run/dbus/…` resolved to nothing — only `/host/{etc,proc,sys}`
   exist under that prefix, there is no `/host/run`.
2. **AppArmor's `docker-default` denies the system bus.** `nmcli` failed with
   `AccessDenied: An AppArmor policy prevents this sender…` even with a correct
   path. Now `security_opt: [apparmor=unconfined]`. This does **loosen a
   security boundary** — it was left for an explicit decision earlier and is
   now applied, so it needs a deliberate re-look: removing the line puts NM
   actions back to failing closed.
3. **`systemctl --user` failed — but not for the documented reason.** Milestone 6
   blamed running as root; the container has run as `${UID}:${GID}` since
   `838aa48`, so that was stale. The real cause was narrower: `command_output`
   passed `env={"PATH", "LC_ALL"}`, and the stripped environment removed
   `XDG_RUNTIME_DIR`, which is how `systemctl` locates the per-user bus. Now one
   `_subprocess_env()` helper builds the env for all five systemctl/nmcli call
   sites, adding `XDG_RUNTIME_DIR`, `DBUS_SESSION_BUS_ADDRESS` and
   `DBUS_SYSTEM_BUS_ADDRESS` only when the corresponding socket actually exists
   — a wrong address produces the same "broken tool" error as a missing one.

**Verified live** (container rebuilt and recreated, logged in, real endpoints):
`/api/services` returns `"state":"observed"` with **45 real user services**,
`/api/profiles` returns the host's actual NM connections, and the guardrail is
intact — `dbus`, `polkit`, `systemd-udevd` and `network-manager` are all still
refused as critical while `cups.service` issues an approval token. 72 tests pass
(+6 `SubprocessEnvTests`) — the count as of this milestone; the suite is 67
now, after the standalone panel's HTTP tests were retired.

**Still broken, and not fixable from here: NM checkpoints.** `nm_activate` refuses
to run without one, so profile activation has no rollback safety net. Two
independent causes, both verified on the host — this is not a container problem:
- Neither nmcli implements the `con checkpoint` verb. The container ships 1.52.1
  and the host has 1.46.0; `nmcli con --help` lists no `checkpoint` in either.
- The D-Bus `CheckpointCreate` call *is* available and *is* refused: polkit's
  `org.freedesktop.NetworkManager.checkpoint-rollback` defaults to
  `auth_admin_keep`, i.e. an interactive admin password prompt, and
  `/usr/share/polkit-1/rules.d/org.freedesktop.NetworkManager.rules` grants only
  `settings.modify.system`. Reproduced outside the container as a plain user.

Making this work means either a polkit rule granting checkpoint to an active
local `sudo` session, or dropping the checkpoint requirement and accepting
activation without rollback. Both are security-relevant decisions, so neither
was made here. Until then the endpoint fails closed, which is the safe side.


### 2026-09-27 — Package & Update Management (Milestone 10)

Closes Future Work #2, in its **read-only** half. The module lists what an apt
upgrade would change and stops there: it parses the host's
`/var/lib/dpkg/status` and `/var/lib/apt/lists` (bind-mounted `ro` at
`/host/…`, since the container's own dpkg database is the image's ~140 packages
rather than the host's ~2700) and renders the commands. **No apt call, no root
helper, so there is no action to approve** — `auth.Approval` is deliberately
unused, and the page's payload is text to copy.

**apt's own answer is not reachable.** `apt-get -s upgrade` needs the lock
files, the dpkg status database, and a config the container deliberately does
not have. The module reproduces its *installed → candidate* comparison for the
read-only case, with one deliberate difference: **apt applies Ubuntu's
phased-rollout percentage per host and that decision is in no file**, so phased
versions are reported *with* their percentage rather than resolved. On this host
15 of 23 are phased. Backports are real updates that `apt-get upgrade`
deliberately skips (target-release 500 vs backports 100), so they get their own
bucket rather than being dropped.

**The version comparator is the load-bearing part, and it was verified, not
read.** Debian version order is **not a total order**: `dpkg.py` mirrors dpkg's
own scan rather than sorting tokens, because the two scales dpkg uses are not
comparable with each other — digit runs compare as *numbers* (so `2 < 10`), and
everything else compares by `order()`, where `~` sorts below end-of-string and
a non-digit run sorts above it. A token list cannot express that. Differential
test against **libapt itself** (`apt_pkg.version_compare`): **924 120 ordered
pairs** drawn from this host's real version set — **0 mismatches**. Re-run that
sweep if `_cmp_part` is ever touched; a total-order model looks plausible and is
wrong only on edge cases.

**Two defects were found and fixed before committing.** `packages/__init__.py`
carried a `snapshot()` that duplicated `api._snapshot()`, took no `_paths()`
override (so tests could not reach it), reached into `host._suites`/`_arches`
privates, and had no callers — deleted. And `list.html` marked four command
blocks `data-copy` with **nothing in the codebase handling that attribute**, so
clicking did nothing; `app.js` now copies and `app.css` gives the hover and a
copied state.

**Not verified in a browser** — the preview classifier was unavailable, so the
copy interaction is confirmed only by `node --check` plus a rendered-page
assertion that the targets exist.

**Measured on the live host:** 23 upgradable, 1 backport, 15 phased, 6 suites,
**2.5 s** to parse 274 MB of indexes. That parse runs on *every* page request,
which is fine for a page you open deliberately and is the thing to cache if the
dashboard ever links this from a polling view. 96 tests + 40 subtests.

**Still deliberately absent: the upgrade itself.** "Approved batch upgrade with
snapshot/rollback (btrfs/zfs/timeshift)" is the other half of Future Work #2 and
is untouched — it needs root, a snapshot strategy, and a rollback path, and is
the same class of decision as the NM checkpoint question below.

### 2026-09-27 — Desktop Notifications (Milestone 11)

Closes the delivery half of Future Work #1. A background timer announces the
conditions the app already observes, so a problem found at 3am is announced
rather than waiting for someone to open a page.

**It announces, it does not repair.** The conditions are the ones already
computed: `server.py`'s `suggestions()` (memory <10%, root disk <10%, no
default route) plus the two packages-module facts that were previously only
visible on a page someone had to open — the upgradable count with its phased
rollouts, and the age of the apt indexes. Nothing runs privileged and no new
apt call is made; the packages snapshot is the same read-only parse the module
already does.

**Delivery was verified against the live stack, not assumed.** This host runs
a real notification daemon (pid 4286, `gjs`) owning
`org.freedesktop.Notifications` on the session bus, and a `Notify` round-trip
returns an id. Three findings shaped the implementation:

1. **AppArmor blocks it.** From a container, `notify-send` fails with
   `AccessDenied: An AppArmor policy prevents this sender…` — the same class
   as Milestone 8's D-Bus denial. It **succeeds** under
   `security_opt: [apparmor=unconfined]`, which the shipped compose already
   sets, so no new loosening was introduced. The Dockerfile gains
   `libnotify-bin` (0.8.6-1), which the image did not have.
2. **The bus address is already built.** `auth._subprocess_env()` resolves the
   session bus to `unix:path=/run/user/1000/bus`, so `notify-send` is launched
   through `auth.command_output` and inherits it. As argv, never a shell
   string: the body is host-derived text and must not be word-split.
3. **gunicorn does not export its worker count.** A `WEB_CONCURRENCY`-based
   multi-worker guard was written, measured, and found to be dead code
   (`WEB_CONCURRENCY=None` inside a real worker), so it was removed rather
   than shipped. Like `ConnectivityStore`, the state is per-process and the
   single-worker deployment is the documented one; `SYSTEM_MANAGER_NOTIFY=0`
   is the escape hatch, and `TESTING` suppresses the timer.

**A condition is announced once, when it newly appears.** The state is a set
of keys, not a timestamp, so a machine that has been disk-full for a week does
not re-notify every five minutes; the key is forgotten when the condition
clears and can alert again. A send that **fails** is recorded in `last_error`
and left unacknowledged, so the next tick retries rather than losing the alert
to one dead daemon. A failing daemon never raises into the dashboard.

**The stale-index alert reports the OLDEST index, the page reports the
newest** — and that is deliberate, not an oversight. The page's `age_days` is
`now - idx.newest`, which stays low as long as *one* repo answers, so on this
host it reads 1.2 days and never fires while the oldest index is **888 days**
old. A suite that stopped being fetched is exactly what `newest` misses. The
alert therefore says "Oldest apt index is 888 days old" and names a failing
repo rather than restating the page's claim. Fixing the page's measure is a
separate call and was left alone.

**Verified live** (image rebuilt, container recreated, timer left to run
unsupervised): the thread fired on its own and sent two real desktop
notifications — *Updates are waiting* and *Oldest apt index is 888 days old* —
and a second tick sent nothing, confirming dedup. `/api/notifications` is
auth-gated (401 on both methods with auth on), the dashboard and `/packages/`
still render, and a failed send is reported as 503 rather than a false 200.
117 tests + 40 subtests.

**Not built: the other two halves of Future Work #1.** Configurable
thresholds would mean a settings surface, persistence and per-threshold tests;
webhook/email means storing credentials and calling user-supplied URLs, which
is an SSRF surface and a security decision, not a feature. Neither is implied
by what shipped.

### 2026-09-27 — Milestone 12: the subtitle button's URL, and why the rewriter missed it

The ＋sub button added in `8057d7a` (folder_organizer) was **dead through the
proxy** while working standalone. Cause: `organizer.py`'s `_JS_CALL_RE` matched a
*fixed list of callee names* — `fetch|post|open` — and `videos.html` calls
`subPost('/api/subtitles/queue')`, a helper that wraps `fetch`. The name wasn't
in the list, so the literal was never prefixed and the POST hit the **host
app's** root, 404ing. Same class as Milestone 7 bug 2, one layer deeper: that fix
rewrote the known call sites, and a *new* helper written later fell straight
through the same hole.

**Fixed by matching any identifier called with a quoted absolute path**, rather
than by adding `subPost` to the list — the list is a whitelist against a codebase
that is explicitly never modified by contract, so every new helper is a
recurrence. Two lookaheads keep it safe: `(?=...)` skips protocol-relative
URLs, and an already-`/organizer` path is left alone. A path that is merely
*assigned* is not matched, only one handed to a call, which matters because card
titles and `dir` values are absolute **Windows** paths (`G:\…`) that must not be
mangled.

The widening is bounded and was measured rather than assumed. Diffing old vs new
regex hits across all 9 organizer templates: **exactly 2 changed**, both the
`subPost` call sites (`/api/subtitles/queue` ×2, `/api/subtitles/clear`), nothing
else. The claim that "every such literal is a route" is checkable — the only
Jinja inside any `<script>` block in the whole template set is `{{ total_groups }}`
in `select.html`, a number.

**Verified live**, not just by the rewriter's own unit tests: container rebuilt
and `--force-recreate`d, logged in, `/organizer/videos` served with all four
inline call sites prefixed (`fetch` and both `subPost`s), then an actual
`POST /organizer/api/subtitles/queue` for a real card → `{"ok":true,"pending":2,
"quota":20}` and two rows landed in `commands_to_run/subtitle_queue.json`. The
test enqueue was then cleared back to the empty state it was found in; enqueue
only writes a gitignored queue file and downloads nothing. 4 tests added
(`test_lock.py` 15 → 19) for the wrapper case, the bare-root case, the
protocol-relative case, and the not-a-call case. **117 tests + 40 subtests** —
unchanged from Milestone 11's headline count because those 4 were already
counted; `test_lock.py` had 15 tests at `HEAD` (113 total then).

### 2026-09-27 — Log Analysis & Journal (Milestone 13)

Future Work #3, in its read-only half. `/logs` queries the host's journal
through `journalctl --root` and renders the entries, filterable by priority,
unit, time and boot, with a JSON export of whatever is on screen. No vacuum, no
rotation, no writes — so `auth.Approval` is deliberately unused, for the same
reason the packages module leaves it unused.

**Reading the journal needs group `adm`, and that is a real widening.** The
journal files are `root:systemd-journal` with an ACL granting `adm`; the host
user is in `adm` and the container was not, so the mount alone was not enough —
`head` on a journal file inside the container returns `Permission denied` at
`1000:1000` and succeeds at `1000:4`. `docker-compose.yml` gains
`group_add: ["4"]` (gid 4 is adm on Debian/Ubuntu), which grants the container
exactly the read the invoking user already has. **This is the third deliberate
loosening in the compose file** after `apparmor=unconfined` (Milestone 8) and the
`pid: host` that predates it; removing it makes the module silently unreadable
rather than failing loudly, which is worth knowing before anyone trims it.

**`--root` takes a filesystem root, and passing it the journal directory is a
silent failure.** `journalctl --root /host/var/log/journal` makes it look for
`/host/var/log/journal/var/log/journal`, prints "No journal files were found"
and **exits 0**. The first live check returned `{"ok": true, "count": 0}` — the
exact ambiguity the packages module was built to avoid, reproduced by the module
written to prevent it. `--root /host` is correct; `RootTests` pins it.

**The ambiguity itself is the design constraint.** journalctl exits 0 for a
good read, an empty result, and an unreadable journal alike, so exit code cannot
distinguish them and the mount is checked *before* querying. A missing mount
answers with the compose line that fixes it, never an empty table.

**Filter values travel as paired argv, never a joined string.** `--priority err`
rather than `--priority=err` is what keeps a value from becoming its own
option; journalctl escapes unit names internally as well. `priority` is
checked against the fixed syslog set, `boot` against an index or `all`, and
`since` against a conservative charset — a newline in any of them would break
the command echo the page renders. A refused filter is **reported on the page**,
not dropped: a silently ignored `priority` would leave the user looking at a
query they never asked for.

**Two live findings that only the real journal showed.** A unit-scoped query
legitimately returns `Started <unit> - ...` rows filed under `init.scope`, so
displaying the unit verbatim showed `init.scope` for all of them and hid what
matched; the unit is now read out of the message for scope rows. And
`--list-boots --output json` returns a **JSON array**, not newline-delimited
objects, so it needs `json.loads` on the whole document.

**Verified live** (image rebuilt, container recreated): 4 real entries from
`docker.service` and `avahi-daemon.service` with correct local times;
`priority=err` returns err/crit/alert only; `unit=systemd-resolved.service`
returns that unit only; `priority=--output=short` is **refused and reported**,
and `boot=--disk-usage` likewise. The page renders 200 with rows, stats, the
filter form and the export link. The missing-mount branch was exercised by
pointing `JOURNAL_HOST_DIR` at a nonexistent path — it names the compose line
and explains the exit-0 trap. 145 tests + 40 subtests (28 new).

**Not verified in a browser** — the preview classifier was unavailable for the
whole session, so the layout, the priority colours and the filter controls are
confirmed only by rendered-HTML assertions (200, 5 rows, 4 stat cards, export
href) and the live JSON API, not by eye.

**Not built: journal maintenance.** `journalctl --vacuum-time/--vacuum-size`
and rotation are the natural next half, but they are *writes* to a 1.4 GB
journal and would need the approve → execute → audit flow that the two
read-only modules deliberately skip. That is the same class of decision as the
NM checkpoint question, so it is not implied by what shipped.

### 2026-09-28 — Configurable Alert Thresholds (Milestone 14)

The remaining half of Future Work #1. The 10% cutoffs in
`server.py:suggestions()` and the 7-day apt-index age in `notifier.py` are now
set at runtime from the dashboard, and stored in SQLite beside the action audit
so they survive a restart.

**A threshold of 0 is a real answer, and `or` would have eaten it.** "Never warn
me about memory" is a legitimate preference, so `thresholds.get(k) or
DEFAULT` is the wrong shape — a 0 would silently become 10. The lookup tests
`is None` instead, and a `True` is refused where an int is required
(`isinstance(True, int)` is `True` in Python, so a checkbox posting `true`
would otherwise be stored as 1). The rendering is `%g`, not a hardcoded "10%",
so a saved 25 reads "Less than 25% of memory is available" in the advisory
itself; `12.5` is expressible in the renderer even though the API refuses
non-integers.

**The one signature change is additive.** `suggestions(data, thresholds=None)`
and `snapshot(thresholds=None)` default to today's behaviour, so every existing
caller — including the 145 tests that were already green before this — sees no
change. The thresholds are threaded to the three places that decide whether to
fire: `status.collect()` for the dashboard's Advisories card, `ConnectivityStore.get()`
for `/api/status` (which collects its own snapshot, so passing them only at the
top would have left the card on the built-in 10%), and `Notifier.check()` for the
desktop alerts.

**The notifier reads them through a callable, not a copy.** `Notifier.thresholds`
is a zero-arg function re-invoked per tick, so a value saved while the timer is
running reaches the next tick without the thread being rebuilt. A read that
raises falls back to the shipped default rather than propagating: a settings
failure must not be the reason the alert loop dies. The `WEB_CONCURRENCY`
dead-code lesson from Milestone 11 applies — nothing here rebuilds the thread.

**Saving a threshold calls `Notifier.forget()`.** The dedup is a *set of keys*,
so a condition already seen stays silent forever. Tightening a cutoff can make
a condition newly true, and without clearing that memory the notifier would
decide it had already reported a condition it never saw. The cost is that an
unrelated still-active condition may announce once more — the honest behaviour
after the user changed its definition. Forgetting is deliberately *not* on the
rejected path: a bad value changes nothing, so nothing should be re-announced.

**A rejected value changes nothing at all.** `set_thresholds` validates every
key before writing any of them and returns the current values alongside the
error, so a form re-renders without a second round trip and a partial save is
impossible. Bounds are 1–100% for the two percentages and 1–3650 days for the
index age; a 0% cutoff is refused at the API even though the renderer supports
it, because "alert when the disk is completely full" is not a useful state to
wake someone for.

**Verified against a running server**, not just the unit tests: `GET` returns the
defaults, `PUT {30, 25, 14}` round-trips, `PUT {900}` returns 400 and leaves
`25`/`14` untouched, and — the claim worth making — setting
`memory_low_pct: 100` makes the live `/api/status` advisory read *"Less than
100% of memory is available"*, proving the saved number reaches the text rather
than a second hardcoded copy. **Persistence was checked across a real process
restart** against a fixed `AUDIT_PATH`: `33`/`21` were still there after the
server came back up. 169 tests + 40 subtests (24 new, `tests/test_thresholds.py`).

The wiring test was checked against itself: reverting the `/api/status` change
makes it fail, so it is load-bearing rather than decorative. A first draft of it
asserted against a hand-built dict and would have passed with the wiring
removed; that was rewritten to spy on the argument.

**Not verified in a browser** — the preview classifier timed out on both
attempts, the same gap Milestone 13 recorded. The form is confirmed by
`node --check`, by the eight card elements rendering in the served HTML, and by
the API round trip above; the layout, the number inputs and the Save button are
not confirmed by eye. The `ponytail` here is that the form reuses `.conn-form`
from the connectivity card rather than owning styles, so a threshold box and an
endpoint box are styled identically by construction.

**Still not built: webhook/email delivery.** Unchanged from Milestone 11's
position and not implied by this — it means storing credentials and calling
user-supplied URLs, which is an SSRF surface and a security decision rather than
a feature.

---

### 2026-09-30 — Milestone 15: the Folder Organizer is a real module

The organizer ran as a **proxy**: `system_manager/organizer.py` imported the
sibling checkout's `app.py` in-process, faked a WSGI environ for it, and
rewrote the absolute `/api/...` paths in its HTML with a regex on the way out.
Two Flask apps on one interpreter only work through a shim like that, and the
rewrite existed purely to make it work. The routes now live here as a blueprint
(`system_manager/organizer/`), so the paths in the templates are simply right.

**Scope — web only.** The scanner, `fetch_subtitles.py`, `season_scan.py`, the
runners and 166M of `data/` stay in `../folder_organizer`, reached over the
existing bind mount. That mount is now read-only in practice for everything but
`commands_to_run/`, which is still written: `subtitle_queue.py` points at the
checkout, not at this package, so the host-side `subtitle_runner.py` drains the
very same queue file. Pointing it at the package would have created a second,
undrained queue.

**URLs are `url_for` everywhere.** `url_for` cannot be used inside a JS string,
so `base.html` builds the API endpoints into a `U` map (`| tojson`) that pages
read as `U['api/video/poster']`. Every API path a page can call is a `url_for`
result, which has no blind spot the old rewriter had.

**Three bugs the rewriter's blind spots had been hiding**, all now covered by
tests in `test_lock.py`:

- `subPost('/api/subtitles/queue')` — the ＋sub button. The rewriter matched a
  fixed list of callee names; a helper that *takes* the URL as an argument was
  never in it. Same bug class again, now pinned by a test that matches **any**
  `\w+('/api/...')`.
- `U['api/video/poster?id=']` — the query string was glued onto the *key*, so
  the lookup was `undefined` and every card fetched the bare endpoint. The
  existing test hid this by stripping `?` off the used key before comparing; it
  now compares verbatim.
- The `U` map sat in a `<script>` *after* `{% block content %}`, so page scripts
  inside the block ran first and threw `U is not defined`. It is now its own
  script above the block.

The last two only showed up under a browser, not in the suite: both pages render
fine and return 200 with every URL correctly prefixed. Rendered HTML being
correct is not the same as the page working.

**Two pre-existing 500s, left alone:** `api/selection/decide` and
`api/selection/set-group` do an unguarded `body["gid"]`, so a POST without one
raises `KeyError`. Byte-for-byte the same as the old `app.py` — not introduced
here, and out of scope for a merge. They answer 200 with a well-formed body.

---

### 2026-10-03 — Milestone 16: the tag detector stops guessing

`organizer/tag_detect.py` wrote fields into a 31 111-track catalog in three
places where the path did not determine the answer. All three were fixed in
`90e68a5`; none was introduced by the merge.

**Genre needles matched as bare substrings.** `"ney"` matched inside
`ho|ney`, `"tar "` inside `Bishtar` and `Bish|tar`, `"setar"` inside
`Setare` — so English and unrelated Persian tracks were tagged
`Persian Traditional`, **745 rows** of them. The needle is now compiled with
`(?<!\w)…(?!\w)`; one that carries its own boundary (`\\Games\\`) is used
verbatim, since it is already a path fragment. This is the same
whole-word lesson as the `dpkg` comparator, one layer down: a shortcut that
looks safe is safe only on the inputs it was written for.

**The boundary also un-steals later rules, which is why 254 rows change
genre rather than merely losing one.** A path that matched `tar ` inside a
*word* used to stop the scan dead. Now it falls through to the rule that
actually describes the folder — `Money Talks` under `POP\` is
`International Pop`, under `Blues\` is `International Jazz`.

**`detect_genre` had a script-derived fallback**, so **23 074 of 31 111**
rows were tagged `International Unknown` and none of them could ever be
re-audited as missing: a placeholder turns "no data" into data. Unmatched is
now `None`, and `fallback` is gone from `genre_rules.json`. Nothing else
read that key.

**Artist and title were read off a layout that determined neither.**
`Music/vMusic/001) Artist - Title.mp3` has no artist folder, so the folder
became the artist — `"vMusic"` written into **875 tracks**. A proposal now
requires a track at least two levels below `Music`, so a flat folder
proposes nothing. A guessed tag is not re-audit-able; an absent one is.

**Export answered 200 with `count: 0`** when nothing was writable, which is
indistinguishable from a successful empty export. It now names which of the
two reasons applies and answers 400.

**Measured, not sampled.** The old and new detectors were run side by side
over all 31 111 catalog entries: **739 rows change** — 477 genre drops,
254 value changes, 8 additions. Every transition was inspected and every
one is a correction; the 8 additions are genuine whole-word matches the
trailing-space needle used to miss (`Taknavaziye |Tar.`, `Bezan |Tar.`).
One expectation of mine was wrong while checking this and the code was
right: `Nazi Naz Kon` matches no instrument needle, so `None` is correct
there. 180 tests + 318 subtests.

**Not verified against the running container** — the Docker socket is not
readable by this session, so the tag pages were not walked in a browser.
The 11 new tests are unit-level; the numbers above come from the real
catalog file, not from the app.

**Deliberately not done: deleting the sibling's old web app.** Task 6 of
the merge plan removes `folder_organizer/app.py`, its `templates/` and the
standalone `:5001` Dockerfile/compose. That is held until the blueprint is
confirmed in a browser, because the suite passing is not the evidence that
matters for a URL rewrite — see Milestone 15, where two of the three bugs
produced perfectly correct HTML and a 200. **The plan's list of tests to
keep is also wrong:** `test_seasons.py` and `test_tmdb_ratings.py` both do
`from app import …`, so only `test_video_catalog.py` and
`e2e_tags_check.py` survive the deletion.

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
│     ├── notifier.py      → Notifier, a 5-minute thread that announces each
│     │                     new condition once via notify-send on the session bus.
│     │                     Thresholds are read per tick from auth.Settings.
│     ├── organizer/       → blueprint at /organizer; owns the web routes and
│     │                     reads data/ + commands_to_run/ from the bind-mounted
│     │                     sibling checkout. The host-side toolbelt stays there.
│     ├── journal.py       → blueprint at /logs; journal_api.py queries the host
│     │                     journal via journalctl --root /host and renders
│     │                     entries. Read-only: never vacuums or rotates.
│     ├── packages/        → blueprint at /packages; dpkg.py parses the host's
│     │                     dpkg status + apt indexes (ro) and renders
│     │                     upgrade commands. Read-only: runs nothing.
│     └── inventory/       → blueprint at /inventory; store.py (SQLite)
│                            CRUD, filters, soft-delete, export
├── templates/  (base.html, index.html, modules.html)  Jinja
└── static/     (app.js, app.css, favicon.svg)         vanilla JS

server.py (collector library, no HTTP layer)
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
| GET | `/logs/` | Yes | Journal entries, filtered by `priority`, `unit`, `since`, `boot`, `limit`; `?export=json` downloads them |
| GET | `/api/notifications` | Yes | Currently active conditions, recently sent alerts, `last_error`, `interval` |
| POST | `/api/notifications` | Yes | Send a fixed test notification; `503` if `notify-send` failed |
| GET | `/api/notifications/thresholds` | Yes | Current alert thresholds (`memory_low_pct`, `disk_low_pct`, `stale_index_days`) |
| PUT | `/api/notifications/thresholds` | Yes | Replace thresholds; rejects unknown keys, non-integers and out-of-range values with the current values attached |
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

Packages module (mounted at `/packages`): `GET /packages/` — the upgradable set with the commands to run it, as HTML, or as JSON with `Accept: application/json`. Read-only, and it runs no apt. A missing mount answers `ok: false` with the `docker-compose.yml` line to add, **never an empty list** — a blank page and an unreachable database would otherwise be indistinguishable from an up-to-date host.
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
Retired. `server.py` has no entry point; use `python3 run.py` or the container.

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
# 180 tests + 318 subtests, ~5s
```

---

## Configuration

| Env Var | App | Default | Description |
|---------|-----|---------|-------------|
| `DISABLE_AUTH` | both | `0` | Set `1` to disable access code (dev only) |
| `HOST_ROOT` | server.py | (empty) | Prefix for host paths in container (`/host`) |
| `SYSTEM_MANAGER_AUTH_TOKEN_PATH` | Flask | (empty) | Writable path for the rotating access code (auth on) |
| `INVENTORY_DB` | Flask | (tmpdir) | SQLite file for the inventory module |
| `PACKAGES_HOST_DIR` | Flask | `/host` | Prefix for the host's dpkg status and apt lists; tests point it at a fixture |
| `JOURNAL_HOST_DIR` | Flask | `/host` | Prefix for the host journal — `journalctl --root` is passed this, and `<dir>/var/log/journal` is what gets checked for existence |
| `SYSTEM_MANAGER_NOTIFY` | Flask | `1` | Set `0` to stop the notification timer starting (one alert source per process) |

---

## Future Work (Priority Order)

### 1. Notifications & Scheduling
Desktop notifications — **done**, see Milestone 11. A background timer
announces the conditions the app already observes (low memory, low disk, no
default route, waiting updates, stale apt indexes), once per condition as it
appears.
~~User-configurable thresholds~~ — **done 2026-09-28**, see Milestone 14. The
two percentage cutoffs and the apt-index age are set from the dashboard's
Advisories card and stored in SQLite beside the audit log.
**Still open:** webhook/email alerts — these mean storing credentials and
calling user-supplied URLs, so they are a security decision, not just a feature.

### 2. Package & Update Management
~~List upgradable packages~~ — **done, read-only**, see Milestone 10. The
`/packages` module lists the host's upgradable set, phased updates and
backports, and renders the commands without running them.
**Still open:** changelogs / security advisories (the data is not in the apt
indexes — it needs `apt changelog` or an advisory feed), and **approved batch
upgrade with snapshot/rollback** (btrfs/zfs/timeshift), which needs root, a
snapshot strategy and a rollback path.

### 3. Log Analysis & Journal
~~`journalctl` queries with filters and export~~ — **done, read-only**, see
Milestone 13. `/logs` reads the host's journal with priority, unit, time and
boot filters, and exports what is on screen.
**Still open:** journal maintenance — `--vacuum-size`/`--vacuum-time` and
rotation are writes to a 1.4 GB journal and would need the approve → execute →
audit flow the two read-only modules skip.

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
- ~~Retirement: `server.py`'s HTTP layer~~ — done 2026-09-26. `LocalServer`, `Handler`, `main()`, the `--port` flag and the orphaned `index.html` are removed; the collectors, actions, `SnapshotCache` and `valid_endpoint` stay because `system_manager/` imports them. `test_server.py` went 24 → 19 tests.

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

Clean through `90e68a5` ("fix: stop the tag detector guessing"). Milestone 15
(the organizer as a real blueprint) is `4f5f00b`; Milestone 5 completed in
`d1ac423`. The 2026-09-26 verification pass (Milestone 6) was documentation-only.
A later pass on the same day fixed four container/host bugs — see the Milestone 7
entry, and Milestone 9 below. **180 tests + 318 subtests pass**
(re-verified 2026-10-03, ~7s).

### 2026-09-26 — Milestone 9: stale-claim sweep, video library, panel retirement

Three pieces of work, all verified against the running stack rather than
asserted.

**Docs were wrong in six places.** Test counts said 66 then 72 against an
actual 72 (now 67); `DEPLOYMENT.md`'s health check used `curl` and `jq`, which
the image does not install, so the documented command could not run; and the
"verify the action features against a live container" step was still listed as
open after it had been done. Corrected in `823da3b` and `e0b8eab`.

**Video library filters and badges shipped** in the `folder_organizer`
checkout — decade, rating, kind, episode-count and size filters, five sorts, and
a "Recommended" badge. The two hard-won traps are written up under the
Requested-filters section below: Jinja resolves `c.pop` to the dict's built-in
`pop` method rather than the `pop` field, and a blank flag carried inside a
sort key gets inverted by `reverse=True`, floating unknown years to the top.
Committed in that checkout as two commits, `5926396` (this work) and `038b2fa`
(the programs pagination and CSV export whose diff was mixed into the same
`app.py`) — see the Requested-filters section below.

**The standalone panel is gone.** `server.py` is a library now — 827 → 540
lines, with `LocalServer`, `Handler`, `main()`, `--port` and the orphaned
`index.html` removed. All 24 symbols `system_manager/` imports still resolve,
verified by introspection rather than by reading. A `__main__` guard was added,
because a stripped module with no entry point exits 0 and silently does nothing
when run as a script — that reads as success rather than as "this is a library".
The test count dropping 72 → 67 is the five HTTP-layer tests that went with it.

**Full suite, 2026-09-26:** system_manager 67 passed + 40 subtests;
folder_organizer 35 passed + 6 subtests offline, and 73 passed / 0 failed
against its live container. Every dashboard route answers 200 through the
port-4000 proxy.

> **The three container bugs from Milestone 6 are fixed and verified against a live bus** — see Milestone 8. `docker-compose.yml` corrects `DBUS_SYSTEM_BUS_ADDRESS` and adds `security_opt: [apparmor=unconfined]`; `auth.py` has a per-call `_subprocess_env()`. `/api/services` and `/api/profiles` both return `observed` with real host data. Two caveats worth keeping: the AppArmor line **is** a deliberate security-boundary loosening, and the `systemctl --user` fix turned out to be the stripped subprocess env, not the uid. **NM checkpoint rollback remains unavailable** (upstream nmcli + polkit limits, reproducible on the host), so `nm_activate` still fails closed.

> `core.filemode` is set to `false` on this clone, so the older repo-wide `100644 → 100755` mode flips no longer appear in diffs.

---

## Next Steps

- ~~Review + commit the Flask port~~ — done in `ac47c92`
- ~~Wire the connectivity diagnostics into the Flask dashboard~~ — done, see Milestone 5
- ~~Enforce login redirect / lock the whole app when auth is on~~ — done; see `tests/test_lock.py`
- ~~Commit the Milestone 5 remainder~~ (connectivity port + lock) — done in `d1ac423`
- **Fix the action features in the container** — highest priority, and all three
  causes are fixed and now verified against the live stack.
  1. ~~`docker-compose.yml`: `DBUS_SYSTEM_BUS_ADDRESS` → `unix:path=/run/dbus/system_bus_socket`~~ — done. `_subprocess_env()` also sets both addresses per call, so the variable is belt-and-braces rather than load-bearing.
  2. ~~`docker-compose.yml`: add `security_opt: [apparmor=unconfined]`~~ — done. Still a deliberate security-boundary loosening to confirm explicitly before shipping.
  3. ~~Verify against a running container.~~ — done 2026-09-26. `SubprocessEnvTests`
     stubs `os.path.exists` / `os.path.isdir`, so it only proves the env is *built*
     correctly; the live check was separate. Logged in and read `/api/services`
     and `/api/profiles`, both returning `{"state":"observed", ...}` with real host
     data, plus `/health` → `{"status":"ok"}`. `nm_activate` is the one action
     still refused — see the checkpoint note above; that is a polkit decision,
     not an environment problem.
- ~~Decide: retire `server.py`'s standalone panel~~ — done, see the retirement note above
- ~~Per-card subtitle fetch~~ — **done 2026-09-27**, `8057d7a` + `c5e40d5` in
  `folder_organizer`. The ＋sub button only *enqueues* into
  `commands_to_run/subtitle_queue.json`; `subtitle_runner.py` drains it on the
  host, because the container has no media mount and no OpenSubtitles
  credentials. Dry-run unless `--apply`, capped at the 20/day free tier.
- ~~Rating source decision~~ — **decided and shipped 2026-09-27**, `8eafab2`.
  TMDB, not Rotten Tomatoes: `TMDB_API_KEY` was already in `.env` and
  `tmdb_client.py` had already matched 455/455 cards, so the provider choice
  was never really open — only the scope was. See the rating section below.
- **Package & update management (#2)** — the read-only half shipped
  2026-09-27 (`888abfe`), see Milestone 10. What remains is the *upgrade*
  itself — changelogs/advisories, and an approved batch upgrade with
  snapshot/rollback. That needs root, a snapshot strategy and a rollback path,
  so it is a decision, not a feature: the same class as the NM checkpoint
  question below.
- **Notifications & scheduling (#1)** — the desktop half shipped
  2026-09-27, see Milestone 11, and configurable thresholds landed 2026-09-28
  (Milestone 14). What remains is webhook/email, which is a security decision
  rather than a feature, so it is listed as blocked rather than pending.
- **Finish the organizer merge: delete the sibling's old web app** — the
  remaining item of Milestone 15. Remove `folder_organizer/app.py`, its
  `templates/`, the standalone `:5001` Dockerfile/compose, and the tests that
  `from app import …`: `test_app.py`, `test_hardening.py`, `test_programs.py`,
  `test_run_cmd.py`, `test_videos.py`, `test_subtitle_queue.py`, **and also
  `test_seasons.py` and `test_tmdb_ratings.py`** — the plan said to keep those
  two, but both import the app. Only `test_video_catalog.py` and
  `e2e_tags_check.py` genuinely survive.

  **Do this only after a browser walk, not on a green suite.** Rebuild with
  `docker compose build && docker compose up -d --force-recreate`, then visit
  all ten pages and click the POST flows on `music/tags` and `select`; a bare
  `/api/...` 404 in the console is the signature of a missed prefix. Milestone
  15 had two bugs that rendered perfect HTML and returned 200 while failing in
  the browser, so "the tests pass" is not the evidence that matters here.

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
| `rating` | 388/446 (87%) | **str** | Range 4.8–9.3, every value matches `\d\.\d` — folder-name scrape, superseded by TMDB |
| `pop` | 385/446 (86%) | **str** | Range 30–100 |
| `genres` | 396/446 (89%) | str | Comma-separated, multi-valued |
| `title` | 446/446 | str | |

**Ratings now live in a sidecar, not in these cards.** `data/ratings_state.json`
is a `{card_id: {status, rating, votes, exact, tmdb_title, checked}}` map
written by `tmdb_ratings.py`; `app.py` reads it via `load_ratings()` and prefers
it over the scrape above. 413/446 scored as of 2026-09-27. It is deliberately
*not* merged into `video_library.json`, which `video_catalog.py build()`
rewrites from scratch.

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
   (e.g. ≥9, ≥8, ≥7, ≥6). **Shipped 2026-09-27**; the filter and both rating
   sorts now read TMDB's score first and fall back to this scrape.
3. **Kind** — `movie` (322) vs `serial` (124), currently only reachable indirectly
   via `root`.
4. **Size / episode count** — `total_bytes` and `video_count` are numeric-typed
   and fully populated; a cheap "5+ episodes" or "multi-GB" filter for serials.
5. **Sort** — by year, rating, title, size, episode count, ascending and
   descending. Default to the current file order so the page looks unchanged
   until a sort is picked.

**Status 2026-09-26: shipped and committed** as `5926396` in the
`folder_organizer` checkout, with `tests/test_videos.py` (16 tests). Verified
through the live container on `/organizer/videos`: all five sorts, every filter,
and a paging sweep that returns 446 unique cards with no card repeated or
dropped.

Two things the implementation had to get right, both worth not re-breaking:

- **Jinja `c.pop` is not the `pop` field.** Dot access on a dict resolves
  built-ins first, so `{{ c.pop }}` rendered
  `<built-in method pop of dict object at 0x…>` into every badge. It has to be
  `{{ c['pop'] }}`. `test_recommended_badge_shows_numbers` asserts the string
  `built-in method` never appears in the page.
- **Blanks must be partitioned out, not carried in the sort key.** `year`,
  `rating` and `pop` are strings and are `''`/`None` on unparsed cards, so a
  `(is_blank, value)` key tuple under `reverse=True` inverts the blank flag
  too and floats unknowns back to the top. `_sort_cards` splits known from
  blank first, then sorts.

**Decade filtering hides every serial.** All 124 serials have `year: None` —
the scanner only extracts a year from the `YYYY - Title` form that movie
folders use, and serial names never carry one. So `?decade=2010&kind=serial`
is legitimately 0 results. The unknown-year option is labelled "No year in
name (all serials)" and the note under the filter bar says so, rather than
leaving a user to conclude the filter is broken.

**Rating source: decided, TMDB, shipped 2026-09-27** (`8eafab2` in
`folder_organizer`). The note below is what the question *was*; kept because
the reasoning is what settled it.

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

**What settled it.** Checking the repo answered the question before any code
was written: `TMDB_API_KEY` was already in `.env`, `tmdb_client.py` already
existed, and it had already matched **455/455** cards for posters. A 30-card
sample then put the folder scrape and TMDB within **0.8** of each other (mean
−0.04, median −0.06), which is rounding, not disagreement. So the scrape was
never a substitute for a provider — it was TMDB's number, lossy. The real cost
was zero and the only open question was scope.

**What it changed.** `tmdb_ratings.py` (host-side; the container has no key and
must not call out on render) caches to `data/ratings_state.json`, not into
`video_library.json`, because `video_catalog.py build()` regenerates that file
from scratch. Across all 446 cards: **413 scored, 33 no-result, 0 errored** — the
proxy flaked twice mid-run, and because `error` is not cached as an answer
those cards simply came back on the next pass. 114 of 124 serials gained a rating they
never had — serial folder names carry no year, so the scrape had nothing to
read. `error` is cached as "not an answer" and retried; `ok`/`no-result`/
`no-title`/`no-score` are final and only re-queried under `--refresh`.

**Two things the numbers did not predict.** TMDB runs slightly *below* the
scrape, so `rating>=8` now reaches **72** cards, not 106 — the filter got
stricter, which is worth knowing before someone reads the drop as a bug. And
wrong matches exist: the Persian-titled "سارا" matches *Terminator: The Sarah
Connor Chronicles* on 1038 votes. Among 18 fuzzy matches the 4 wrong ones had
under 35 votes and every correct one over 200, but that separation does not
hold generally, so the card carries a `?` flag for a non-exact title rather
than leaning on the vote count.

**Refreshing.** `python3 tmdb_ratings.py [--limit N] [--root serials]
[--refresh]` in the `folder_organizer` checkout. It needs the proxy at
`192.168.1.13:10810` — a full pass is ~446 requests at 0.3s, roughly four
minutes — and takes no arguments to cover the whole library.

**Committed 2026-09-26, split by concern.** The work above is `5926396` in the
`folder_organizer` checkout. `app.py` also carried *unrelated* in-progress
programs work (pagination plus a CSV export endpoint) that predated this work,
so its diff mixed both. They are now two commits: `5926396` (video filters and
sorts, `tests/test_videos.py` — 16 tests) and `038b2fa` (programs pagination and
`/programs/export.csv`, `tests/test_programs.py` — 10 tests). Each commit's
staged content was byte-compared against an isolated single-concern tree and
tested on its own before committing. Suite is 35 passed + 6 subtests
(`python3 -m pytest tests/ -q --ignore=tests/test_app.py`, which needs no
running server), plus 73 passed / 0 failed for the live
`python3 tests/test_app.py` against the container on :5001.

**Two container notes, both of which cost time on 2026-09-26:**

- `docker compose up -d` after a build does **not** recreate the container, so
  it keeps serving the old image and an edit silently appears to do nothing.
  Use `--force-recreate`.
- The organizer container's `media_data` **named volume was empty**, so
  standalone `localhost:5001/videos` rendered "0 files" and `tests/test_app.py`
  failed `summary has totals`, `duplicates json` and the `/api/select` `groups`
  call. **Fixed 2026-09-26** — the three named volumes are replaced with bind
  mounts to `./data`, `./commands_to_run` and `./logs`, and the service gained
  `user: "${UID:-1000}:${GID:-1000}"`. `tests/test_app.py` is now 73 passed /
  0 failed, and standalone `:5001` matches the proxy. The `user:` line is
  load-bearing, not cosmetic: the image's `appuser` is uid 10001 and gets
  EACCES writing a 1000-owned `data/`, which would break the
  selection-state, tag-plan and proposal writes. The proxy was never affected —
  it imports `folder_organizer/app.py` in-process and reads the real host data.

### Requested: auto-download subtitles for series/movies missing them

**Status 2026-09-27: unblocked, not started.**
The `G:` blocker below was stale — the volume is mounted and the translation is
mechanical. It now exists as `folder_organizer/media_path.py`
(`to_host_path`, used by the season scan), so the remaining work is only the
per-card button. The counters and caveats below still stand.

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

**The blocker, now solved: the library IS on this host — the paths just need
translating.** `data/video_library.json` stores **Windows** paths, e.g.
`G:\Movies\1939 - Gone with the Wind …\Gone with the Wind.avi`, which is why a
naive `os.path.exists()` returns `False` and the library *looks* absent. The
`G:` drive is mounted at **`/media/kourosh/Multimedia`** (`/dev/sda2`, **ntfs3**,
`rw`, `uid=1000`); its children match `config.json`'s `roots_windows` exactly
(`G:/Movies`, `G:/Serials`, `G:/Videos` → `Movies`, `Serials`, `Videos`). Strip
the `G:` prefix and swap `\` for `/` and **424 of 446 cards resolve**.

So the fix is a `G:\…` → `/media/kourosh/Multimedia/…` prefix rewrite, applied
wherever a library path becomes a real path. It must be applied to `dir` and
`sample_video` alike, and it belongs in `fetch_subtitles.py` / `app.py` — not
here. **Done 2026-09-27** as `folder_organizer/media_path.py` (`to_host_path`);
the season scan is its first caller.

**22 cards still don't resolve, and none of them are the feature's fault:**

| Cause | Count | What they are |
|---|---|---|
| `F:` and `E:` drives not mounted | 19 | 17 are Dota 2 `.webm` game assets (`heroes`, `events`, `portraits`…) — no subtitles exist for these at all; 2 are `E:` |
| Folder deleted since the 2026-09-17 scan | 3 | e.g. `1997 - Gattaca` → now `Gattaca.1997.1080p.Farsi.Dubbed.mkv` (the `- Copy` was removed) |

Filtering to cards that are actually present and actually wantable subtitles
leaves the real target set:

| | Count |
|---|---|
| Cards needing FA **or** EN | 410 |
| — unresolvable (F:/E:/deleted) | 22 |
| — **actually fetchable** | **388** (268 movies, 108 serials, 12 videos) |

The 17 Dota 2 asset folders should be excluded by rule, not by path failure —
they'd otherwise be 17 permanent "no subtitles found" failures per language.

**Quota makes "fetch all" a trap.** OpenSubtitles free tier is **~20
downloads/24h**. The real set needs:

| Strategy | Downloads | Wall-clock at free tier |
|---|---|---|
| **1 file per card** (what the script does today) | **589** | ~30 days |
| **1 file per video** (correct for serials) | **2 221** | ~4 months |

1204 actual video files sit behind those 388 cards, and **0 of 446 cards carry
a `videos[]` key** — the fetcher falls back to `c["sample_video"]`, so a
24-episode serial would get one subtitle file and 23 episodes with none. Fixing
the scanner to emit `videos[]` (it already groups by main item — commit
`bfc1938`, 782→446 cards) is the real fix; re-globbing `dir` at fetch time is a
one-line unblocker.

Given those numbers, the page must **default to dry-run and show a per-run cap**
— the existing script already takes `--budget` and `--limit` for exactly this.
A one-click "fetch all" would surface as ~2 200 failures a month apart.

**Already filtered for you.** The page's `sub` param (`app.py:110-128`) already
does `missing-fa` / `missing-en` / `missing-both`, so a "fetch what's missing"
action can be scoped to the current filter with no new query param. The card
badges at `videos.html:59-61` already show FA/EN state per card, so the button
can sit right there and only appear when a badge is missing.

### Requested: series-state badges + a "Recommended" badge

**Split outcome, Task A shipped 2026-09-27 (`8a9e21a` in `folder_organizer`):**
- **Task B (Recommended badge) — shipped.** Implemented with the filter work
  above: `rating >= 8.0 and pop >= 85`, 91 of 446 cards, labelled in the UI as a
  folder-name heuristic rather than a curated score.
- **Task A (season-state badges) — shipped.** The `G:` blocker below was stale:
  the volume is mounted and the translation is mechanical. Both are now done —
  `media_path.py` rewrites `G:\…` to `/media/kourosh/Multimedia/…`, and
  `season_scan.py` reads the real directory listing. Cards carry a new
  `season_state` key, rendered as a badge; the old `S1/6` name-derived text is
  gone. 43 incomplete / 27 complete / 53 unknown, 0 over-matches.

Same placement as the task above — a change to the **`folder_organizer`**
checkout (`templates/videos.html` for the markup, `app.py:/videos` if a helper
is needed), not to this repo. Pure presentation: no new query params, no new
`fetch`/`post` call sites, so the `_JS_CALL_RE` rewrite needs no edit.

The card badge row is already there — `videos.html:59-61` renders
`FA` / `EN` / `{{ c.video_count }} vid` as Bootstrap `badge`s. New badges slot
in there; the "x of y seasons" text wants its own line under the title, not a
badge, since it is a sentence rather than a state.

**Task A — "Season N of M" on serials. The `season`/`seasons` fields cannot
answer this; the folder contents can, and they do not agree.**

`season` and `seasons` are parsed from the `S01 of 03+` token in the **folder
name** (`video_catalog.py:16`, `SERIAL_PAT`) and are 2-char strings, not ints.
They record what the name *claims*, not what is on disk. I walked all 124 serial
folders under `/media/kourosh/Multimedia/Serials` (1.7 TB of real media; see the
path-rewrite note at the end of this section for how to reach them) and
compared. **The name-derived field is wrong or misleading for 43 of the 124
serials.** Ground truth by actual contents:

| On-disk state | Count | Example |
|---------------|-------|---------|
| Every claimed season present | 27 | `Bates Motel S05 of 05` → S01–S05 all on disk |
| **Seasons claimed but absent** | **43** | `Sense8 S02 of 02` → only `S02` on disk, S01 missing |
| Limited series, no count in name | 45 | `Chernobyl - Limited` |
| No count, not limited-flagged | 9 | `Family Guy`, `Sherlock` |

The gaps are real, not scanner artefacts — hand-verified, e.g.:

- `The Expanse S01 of 06` → only `S01` on disk. **5 of 6 seasons missing.**
- `The Man in the High Castle S01 of 04` → only `S01E01…E08` files. **3 missing.**
- `Person of Interest S05 of 05` → `Season 1, 2, 4, 5` subfolders. **S03 missing.**
- `Sense8 S02 of 02` and `The Exorcist S02 of 02` → only `S02`; **S01 missing.**

So "display how many seasons we have out of how many" **is** answerable, but
only by scanning the directory, and the answer is a *set*, not a count. Render
the missing seasons explicitly, e.g. **"S1–S5 · missing S2–S6"** — the 43
incomplete series are the interesting ones and a plain "1 of 6" hides which ones.
The three folders with no season-numbered evidence at all (`Drifters`,
`Fullmetal Alchemist Brotherhood`, `The Promised Neverland`) hold bare
`E01…E14`-style filenames, which means season 1 only. `My Daddy Long Legs` uses
`01 - …mkv` the same way.

To make this work, `video_catalog.py:build()` has to emit a `seasons_present`
list per serial card instead of inferring it from the name. Match, in order:
`S01`/`S 01`/`Season 1`/`Season 01` directories, then `S01E02` filenames, then
`01x02`, then a bare `E01`/`01.` fallback (→ season 1). Beware that the season
regex must handle the space in `Luther S 01`, and that some shows bury seasons
in subfolders — `Bates Motel` holds `S01`, `S05` *and* `Season 1`…`Season 4`
(duplicated under two conventions), and `Sherlock` holds four differently-named
season folders. `Family Guy` has `Season 01–06` then jumps to `Season 15–17`.

One caveat on the scan itself: some seasons ship as **torrent-pack directories**
that carry the season in the folder name rather than as `S01` — `The Boys` has
`The.Boys.S03.COMPLETE.720p.AMZN.WEBRip.x264-GalaxyTV[TGx]` beside its
`S01`/`S02`. A matcher that only accepts `S01`-style names will undercount
these, so add a rule for `S03`-inside-a-directory-name and expect the gap list to
shrink slightly on a second pass. Eleven folders have no season-named entry at
all; eight are bare `E01…`/`01.` episode sets (season 1), and the other three —
`P&B`, `سارا`, and the `Cowboy Bebop`/`Moawiya`/`Dying for Sex` limited series —
are single-season or non-series content where "season 1" is right anyway.

**Task B — series-state badges.** Three states, all derivable per card once
Task A's `seasons_present` exists:

- **Limited series** — `limited is True`. 49 cards, all serials, 45 with no
  season count, so on those the badge is the *only* signal. Trustworthy: it is
  read from an explicit `- Limited` token in the name.
- **Complete** — every season in `1..seasons` is in `seasons_present`. 27 by
  the disk check, **not** the 31 the name suggests. `int()`-coerce both sides or
  `'10' <= '09'` sorts wrong lexicographically.
- **Incomplete** — 43 cards, with the missing seasons listed. This is the
  category the name-only heuristic gets most wrong, and the one most worth
  surfacing.

`limited` is tri-state (`True`/`False`/`None`); `None` means "a movie, or a
serial whose folder name did not parse". Movies have no `season`/`seasons` at
all, so none of these three badges should ever apply to them.

**The `+` marker is the one trap.** `S01 of 03+` means "more seasons are
planned" and is captured by the `\d+\+?` in `SERIAL_PAT`, but the trailing `+`
is **discarded** — it is not stored on the card. 36 folders carry it. Of those,
30 are `season < seasons` and **6 are `season >= seasons`**, i.e. the `+`
disagrees with the counter. Note that with the disk scan, some `+` folders are
genuinely complete (`Foundation S02 of 03+` has S01–S03 on disk) and some are
not (`The Boys S04 of 04+` has S01–S03 only). Re-derive the marker with a regex
over `folder` if the planned-seasons state is wanted, and treat the 6
name/disk disagreements as a data-quality finding to report rather than silently
preferring one field.

**Path reachability — already solved above, reuse it.** The task above already
establishes that the media is on this host at `/media/kourosh/Multimedia/{Movies,
Serials,Videos}` (the `G:` drive, ntfs3) and that a `G:\…` →
`/media/kourosh/Multimedia/…` prefix rewrite resolves 424 of 446 cards. Task A
depends on that same rewrite — the directory scan has no path to the files
without it, so do the rewrite first and let Task A consume it.

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