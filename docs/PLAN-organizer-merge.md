# Merge folder_organizer's web half into system_manager

> **Completed in Milestone 15** (see `CONTINUATION.md`). This plan is kept for
> history only — do not work through it. The web half landed as the
> `system_manager/organizer` blueprint, `folder_organizer/app.py` and its
> `templates/` were deleted from the sibling, and the old `organizer.py` proxy
> loader and its URL rewriter are gone. The tasks below describe the state
> *before* the merge and are written in the future tense on purpose.

## Where this actually stands

The merge is **already half-written and unwired**. `system_manager/organizer/`
exists (untracked) with `api.py` (1341 lines, all 38 routes ported 1:1 from
`folder_organizer/app.py`), `subtitle_queue.py`, `tag_detect.py` and 11
templates. It has **no `__init__.py`**, so Python resolves `system_manager.organizer`
to the old `organizer.py` proxy loader instead, and the new package is dead code.

So this is a finishing job, not a migration. The scope agreed:

- **Web half only** lands here. `folder_organizer` keeps its host-side toolbelt
  (scanner, fetch_subtitles, season_scan, runners) and its 166M of `data/`,
  reached over the existing `../folder_organizer:/folder_organizer` mount.
- **Delete** `folder_organizer/app.py`, `templates/` and its standalone
  Dockerfile/compose after the new module is verified.
- **`url_for` everywhere** in the ported templates, so the old regex URL
  rewriter can die with the proxy.

## Task 1 — Wire the module so the app imports

`system_manager/organizer/__init__.py`, following the `inventory`/`packages`
house style exactly:

- `PREFIX = "/organizer"`
- `organizer_blueprint()` — `Blueprint("organizer", __name__, url_prefix=PREFIX,
  template_folder=<pkg>/templates)`, a `before_request` calling
  `auth.requires_session_view()`, then `bp.register_blueprint(organizer_api)`.
- `is_available()` — keep the `os.path.isfile` probe, but against the **data
  dir**, not `app.py`, since `app.py` is being deleted. `is_available()` is the
  dashboard's greyed-out/unavailable switch, so it must not report "unavailable"
  just because the checkout moved.

Delete `system_manager/organizer.py` (the proxy). The package replaces it, which
is what frees the `from .organizer import ...` line in `__init__.py`.

**Gotcha:** the package and the old module share the name `organizer`, so until
`organizer.py` is deleted, `from .organizer import` still resolves to the proxy
and the new package is invisible. Delete first, then add.

## Task 2 — Fix the three things that break at runtime

1. **`DATA` points at the wrong place** ([api.py:25](system_manager/organizer/api.py#L25)).
   It walks up three levels from `system_manager/organizer/api.py` → the repo
   root → `folder_organizer/data`, i.e. `system_manager/folder_organizer/data`,
   which does not exist. In the container the checkout is at `/folder_organizer`.
   Default it to `/folder_organizer/data` with the env override kept. Same for
   `CMD_DIR` (`commands_to_run`), which is derived from `DATA` and is written to.

2. **`import tag_detect` is bare** ([api.py:1143](system_manager/organizer/api.py#L1143)).
   Inside the package this only resolves if the module dir is on `sys.path`.
   Make it `from . import tag_detect`. And `tag_detect.py` reads
   `genre_rules.json` off disk **at import time** relative to its own location —
   that file is not in the package, so copy it in, or point the read at the
   checkout. Copying is simpler and it is 1.5K.

3. **Leftover `if __name__ == "__main__": app.run(...)`** ([api.py:1341](system_manager/organizer/api.py#L1341))
   references a `Flask` app that no longer exists in a blueprint module. Delete.

Also check `subtitle_queue.py`: it resolves `commands_to_run/` relative to
`__file__`, which is now the package dir. It must point at the mounted checkout
so the host-side runner drains the same file the web half writes.

## Task 3 — Convert the templates to `url_for`

The 11 templates hardcode paths written for a root-mounted app. The old proxy
papered over this with `_HREF_RE`/`_JS_CALL_RE`; a blueprint cannot.

- **HTML attributes** (`href="/scan"`, `action="/api/..."` in `base.html`,
  `cleanup.html`, `folders.html`, `music_tags.html`, `select.html`,
  `skipped.html`, `videos.html`) → `url_for('organizer.organizer_api.<endpoint>')`.
  This is the house style already used by `inventory`'s templates.
- **`url_for('programs_page')` / `url_for('programs_export_csv')`** in
  `programs.html` are bare and will raise `BuildError` once the blueprint is
  nested — they need the `organizer.organizer_api.` prefix.
- **JS `fetch('/api/...')` and `post('/api/...')` literals** cannot use
  `url_for` inside a JS string. Add a `{{ url_for('organizer.organizer_api', _external=False) }}`-derived
  prefix from an app context processor and use it in the script blocks, or inject
  it as a `data-` attribute. 10 call sites across `select.html`, `videos.html`,
  `skipped.html`, `music_tags.html`, `folders.html`.
- `url_for('static', 'favicon.svg')` in `base.html` is **already correct** — it
  resolves to the host app's static folder. Leave it.
- Per the house rule: do not interpolate values into inline JS strings; read
  from `data-*` attributes.

## Task 4 — Update the dashboard wiring

- `modules()` in [__init__.py:155](system_manager/__init__.py#L155) points at
  `url: "organizer.proxy"` — that endpoint disappears with the proxy. Point it at
  the new module's index endpoint.
- `templates/base.html:27` and `templates/index.html:56` call
  `url_for('organizer.proxy', subpath='')` — same fix, in two places.
- `from .organizer import ORGANIZER_PATH, ...` — `ORGANIZER_PATH` was the old
  loader's env knob. If anything still reads it, keep exporting it.

## Task 5 — Tests

- `tests/test_lock.py` covers "the organizer's URL rewriter" (per
  `docs/ARCHITECTURE.md:233`) — that test is about code being deleted. Rewrite it
  against the new blueprint, or drop the rewriter cases.
- `tests/test_flask_app.py:59` asserts `GET /organizer/scan` is 200 — keep it,
  it is the load-bearing end-to-end check. It will pass for the right reason
  once the module is real.
- Add coverage for what the port has no test for: the blueprint mounts, the login
  gate, and the template filter/context-processor registration (`gb`, `fmt_size`,
  `theme`, `today` — all four are consumed by the ported templates, so if
  `add_app_template_filter` on a nested blueprint does not reach the app-level
  Jinja env, every one of those pages breaks).
- Run the whole `tests/` suite.

## Task 6 — Delete the old web app, then verify in the browser

Only after the new module is green:

- Remove `folder_organizer/app.py` and `folder_organizer/templates/`.
- Remove the 5 test files that import `from app import app` / use `test_client`:
  `test_app.py`, `test_hardening.py`, `test_programs.py`, `test_run_cmd.py`,
  `test_videos.py`, `test_subtitle_queue.py`. Keep the 4 that test pure
  functions and do not import the app (`test_seasons.py`, `test_tmdb_ratings.py`,
  `test_video_catalog.py`, `e2e_tags_check.py`).
- Its `Dockerfile` + `docker-compose.yml` (the standalone :5001 server) go too.

**Verify in the browser, not just via tests** — this is the whole point of the
change, and a green suite does not prove the URL rewrite landed:

```bash
docker compose build && docker compose up -d --force-recreate
```

Then log into `http://localhost:4000` and walk **every** page: index, scan,
music, music/tags, folders, select, skipped, cleanup, programs, videos. Click
through the POST actions on at least `music/tags` (propose → bulk) and `select`
(bulk → export) — those are the `post('/api/...')` sites that break silently.
Check the console for 404s on `/api/...`, which is the signature of a missed
prefix.

Known trap from memory: a bare `up -d` after `build` does **not** recreate the
container and silently serves the old image. `--force-recreate` is required.

## Task 7 — Docs and memory

- `docs/ARCHITECTURE.md:19-20` describes the proxy loader and the URL rewriter —
  now wrong. Update the tree entry and the module list.
- `docs/ARCHITECTURE.md:233` — the test-count line and the `test_lock.py`
  description.
- `docs/CONTINUATION.md` — add the milestone entry.
- Memory: `project_folder_organizer_sibling.md` says the video features are
  edited in the sibling repo. That is now half false — the web templates move
  here, only the host-side scripts stay. Update it, and
  `project_module_hosting.md` (folder_organizer is no longer mounted *as code*,
  only for its data/commands_to_run).

## Commit strategy

`system_manager/organizer/` is **untracked and was written by someone else
(this or a prior session), not by me this session.** Per the house rule about
uncommitted changes, I will not fold it into an unrelated commit silently. Land
it as its own commit ("feat: the folder organizer as a real module") so the diff
is reviewable, and I will not commit anything in `folder_organizer` without
saying so first.

## Out of scope

- The host-side toolbelt stays in `folder_organizer` and is reached over the
  existing bind mount.
- `data/` (166M) is runtime state, not code. It stays where it is.
- The `host` -> `organizer` media path rewrite (`media_path.py`) is unchanged.
