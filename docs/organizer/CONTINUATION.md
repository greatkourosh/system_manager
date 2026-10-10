# Continuation & Future Documents

## Completed milestones

**2026-09-01 — Phase 1: Full system scan + duplicate detection**
- Full computer scan (21 roots across C/E/F/G/H, Windows 11 + Ubuntu 24.04 via Docker).
- 420,849 files cataloged (4,276.9 GB); 4,217 duplicate groups (42.0 GB redundant).
- Outputs: `data/catalog_full.json`, `data/duplicates.json`, per-category catalogs, `data/scan_summary.json`, `data/scan_2026-09-01.md`.
- 2026-09-01 19:55: executed SAFE video-duplicate batch — 137 files deleted, 18.74 GB freed, 0 failures.

**2026-09-01 — Phase 2: Music tag audit + fixer**
- 31,111 tracks audited (mutagen): 8,600 fully tagged; 22,511 need fixing (genre/year/album/artist/title gaps).
- `/music/tags` workflow: auto-detect (`tag_detect.py` + `genre_rules.json`) → manual review → MusicBrainz merge (`mb_candidates.json`) → host apply (`apply_music_tags.py`) → re-audit idempotent.
- E2E verified: 91 Ebi files proposed → manual edit → dry-run → real apply on 2 files → mutagen read-back confirmed → re-run (2 SKIPPED).
- Plan state: `data/tag_plan.json`, `data/music_tag_audit.json`.

**2026-09-02 — Phase 3: Video poster library + subtitles**
- `/videos` poster-card library (782 cards → **446** after the 2026-09-19 regroup, one card per main item) from `video_catalog.py` → `data/video_library.json`.
- TMDB posters via proxy 192.168.1.13:10810 (`tmdb_client.py`); base64 copies in `data/posters_b64/`, state in `data/posters_state.json`.
- `fetch_subtitles.py` OpenSubtitles login flow live; 20 subtitles delivered & verified (English + Persian per scan-log user decision).
- Subtitle/quota state: `commands_to_run/subtitle_log.txt`; 20-download/day budget noted.

**2026-09-02 — Universal Folder Fix (supersedes Music Folders + Name Fix)**
- One engine (`folder_fix.py`) + one page (`/folders`) for all areas: music · videos · learning · programs · pictures · documents.
- Config: `naming_schemas.json`. ~1,720 proposals. Export → `commands_to_run/folder_fix_list.json` → `apply_folder_fix.py` (dry-run default, `--apply` renames).
- Statuses persist across re-scans in `data/folder_fix_proposals.json`; legacy music/name-fix statuses migrated.
- Parity verified: 387/389 identical to the old split flows, 2 improved.
- Superseded: `music_folder_fix.py`, `apply_music_folder_fix.py`, `name_fix.py`, `/namefix`, `/music/folders`.

**2026-09-02 — Web dedupe workflow (`/select`)**
- Interactive duplicate selector: per-copy red/green checkboxes, all/none buttons, review-state + skip-state + media-type + ext + risk + text filters, pagination, floating ↑/↓, night-mode toggle (cookie-persisted).
- Atomic state writes (tmp + `os.replace`) with retry-once reader; fixed torn-read race after test flakiness.
- `gid = sha256[:16]-category` stable group IDs (v2); selection in `data/selection_state.json`, skip rules in `data/skip_rules.json`.

**2026-09-02 — Test suite**
- `python tests/test_app.py` — 73 checks (pages, theme, APIs, selection, exports, skip rules, tag-plan workflow, universal folder-fix round-trip, UI markers). Green ×2 after the universal folder-fixer refactor.

**2026-09-14 — SVG favicon**
- Added `static/favicon.svg` (dark navy rounded square, teal folder + play triangle — "media folder" glyph); wired into `templates/base.html` (inherited by all 10 pages).
- Dockerfile now `COPY static/ static/`. Verified 2026-09-14 on :5001 → `/static/favicon.svg` returns 200 `image/svg+xml`.

**2026-09-17 — Phase 4.5: Cross-install duplicate audit (read-only)**
- `audit_cross_install.py` classifies cleanup-proposal groups by Steam library root (`/steamapps/common` prefix); no filesystem access, no deletions.
- Path classification identifies `Games/Steam` and `Games/steam_ubuntu` as different Steam library roots. Copies across them require review; this pass did not verify manifests, file existence, content hashes, or inodes.
- Reclassified 178 `safe → review`; 181 total review (2.19 GiB); 18 retain original `safe` label (explicitly NOT deletion approval).
- Outputs: `data/cleanup_proposal_audited.json`, `data/cleanup_audit_2026-09-17.md` (git-ignored). Source `cleanup_proposal.json` and all `commands_to_run/` lists byte-identical (sha256-verified before/after).
- `steam_ubuntu` is out of organizing scope per user; no scanner exclusion implemented.

**2026-09-17 — Phase 5: Docker hardening (Linux-verified; Windows pending)**
- Dockerfile: pinned base image via `PYTHON_IMAGE` build arg (LAN registry digest, Docker Hub override documented), non-root `appuser` (UID/GID 10001), pre-owned `/app/data` `/app/logs` `/app/commands_to_run`, `PYTHONDONTWRITEBYTECODE`, single `HEALTHCHECK`.
- `docker-compose.yml`: `env_file: .env` (secrets were previously never injected), named volumes `media_data`/`media_commands`/`media_logs` (replaces `./data` bind mounts — **existing data must be manually migrated**; see `04_DEPLOYMENT.md`), localhost-only port binding (`127.0.0.1:${MEDIA_ORGANIZER_PORT:-5001}:5000`), `cap_drop: ALL`, `no-new-privileges`.
- `app.py`: added `/health` route.
- Verified on Linux: isolated Compose build/start, container `healthy`, `/health` HTTP 200, UID 10001 confirmed, all three fresh volumes writable, `docker compose config` valid. Test deployment removed afterwards.
- NOT done: existing `data/` etc. not migrated to named volumes (manual step, backups required); Windows 11 build/run not tested; browser preview skipped (permission service outage) — assertion-based checks used instead.

**2026-09-17 — Regression verification**
- `python tests/test_app.py`: 73 passed, 0 failed against the live service on port 5001; the folder-fix round-trip was skipped because no learning proposal was available.
- `python -B -m unittest discover -s tests -p 'test_hardening.py' -v`: 4 passed, covering `/health`, path parsing, classification/totals/input preservation/idempotence, and empty input.
- `tests/e2e_tags_check.py` is an ad-hoc mutating diagnostic, not an assertion suite; its tag workflow is covered by `test_app.py` and it was not run separately.
- Compose configuration validated. Windows execution and browser preview remain unverified.

**2026-09-19 — Video library: one card per main item (782 → 446 cards)**
- Problem: `/videos` listed a movie's `Featurettes/` tree and each `Sxx/` season of a serial as separate cards, so one title could appear a dozen times.
- Fix: `video_catalog.py` groups by the folder directly under Movies/Serials/Videos via `main_item()`; nested subtitles fold into the parent card; the drive stays in the key so same-named folders on different drives do not merge. Rebuild with `python video_catalog.py` (idempotent).
- Posters preserved across the regroup: each card carries `poster_id` (the pre-regroup id whose `data/posters_b64/` hash still matches) and `templates/videos.html` posts it to `/api/video/poster`. All 48 existing poster refs still resolve.
- Re-verified: `tests/test_app.py` 73 passed / 0 failed; `tests/test_hardening.py` 4 passed; new `tests/test_video_catalog.py` 5 passed (Windows + posix paths, season and Featurettes folding, root-level file, no-section marker). Live checks on :5001: all 446 rendered card ids match the catalog, no duplicate main folders, every advertised poster returns valid image data. Browser visual check was blocked by permission-classifier timeouts — HTML and poster API verified by request instead.

**2026-09-27 — Posters: the flag, not the mount (fixed for good)**
- Symptom: every poster on `/videos` was a letter placeholder, and it had been "fixed" twice before and came back both times.
- Cause 1: `video_library.json` carried `poster: null` on all 446 cards while all 455 `.b64` files sat in `data/posters_b64/`. The template's lazy-load is gated on that flag, so no image was ever requested. The flag is a one-shot carry-over in `video_catalog.py:build()` that copies `poster` forward from the *previous* library — run it twice after a poster fill and the field is permanently null.
- Cause 2, and the reason it kept returning: `system_manager/organizer.py` imports `folder_organizer/app.py` once at process start and caches it in `sys.modules`. The bind mount is live, so the file was new while the port-4000 route kept serving the old module. Rebuilding or restarting the *organizer* container changes nothing for the dashboard; `docker compose restart` in **system_manager** is required.
- Fix: `videos_page` derives `poster` from `os.path.exists(poster_path(...))` per request, so the durable `.b64` file is the source of truth and no rebuild can wipe it. Cards are copied rather than mutated in place, since the loaded library must stay as read.
- Verified: 118 tests pass (new `test_poster_flag_comes_from_disk_not_the_stored_field` fails without the fix); all 432 posters fetched through the port-4000 proxy, 0 failures, valid JPEG headers.

**2026-09-27 — Video card cleanup: less chrome, real season totals, one genre per entry**
- Removed from the card, per user: the "Queued work runs on the host…" help paragraph, the `{{ video_count }} vid` badge, the file-path line, and the `min_eps` "Any length" filter (template, `applyFilter`, the `app.py` filter branch, and its two tests).
- The `—` on a serial card was the **year** placeholder, not a missing field: serial folder names never carry a year, so `{{ c.year or '—' }}` printed a bare dash saying nothing. The year is now omitted when absent and the rating leads the line; the dash survives only for a card with neither year nor rating.
- Genres were scraped out of folder names and had drifted into 31 spellings for 24 genres. `genre_tokens()` splits on a case boundary, then on spaces, then folds case, with a small table of multi-word genres (`sci-fi`, `black comedy`, `sci-fi fantasy`) that must survive intact. "Sci-fi"/"Sci-Fi"/"Sci-fiAdventure" now resolve to one entry. **ponytail:** the phrase table is a fixed list — a new compound genre (`rom-com`) needs an entry or it splits wrongly.
- New `tmdb_seasons.py` writes `data/seasons_state.json` with TMDB's `number_of_seasons` per serial, reusing the `tmdb_id` `tmdb_ratings.py` already resolved so no show is re-matched. Run host-side: `python3 tmdb_seasons.py [--limit N] [--refresh]`. First run: 114 of 124 serials counted, 5 already known, 5 with no usable TMDB match.
- The card now shows `on disk / name claims / total ever had` (e.g. `1 / 3 / 1`), with `+` when the show is still returning so the total is read as a ceiling. `on disk` counts *distinct* seasons present, not the highest number — Family Guy holds S6 and S15, and "15" would claim nine seasons we lack. Movies get no badge.
- Already pays for itself: "3 Body Problem S01 of 03+" claims 3 seasons in its name, but TMDB counts 1, so the badge contradicts the folder rather than repeating it.
- Verified: 132 tests pass (new `GenreTests` and `SeasonCountTests`, plus `test_removed_chrome_stays_removed` and `test_yearless_serial_has_no_leading_dash`). Live check through the proxy: 123 serial badges rendered, 0 on movies, 24 genre entries.

## Tasks (not yet built)

- **User accounts, seen marks, ratings and recommendations** (user request 2026-09-27, deliberately not started — it is its own milestone, not a card edit). Wanted: people log in, tick off what they have watched, rate it, and get recommendations for what to watch next.
  - **Open decisions to settle before any code:** where the users and their per-user state live (this app has no database; `data/` is flat JSON written host-side, and the container is `cap_drop: ALL` with no media mount); whether "seen" is per-title or per-episode for serials; whether a personal rating overrides or sits beside the existing TMDB score, which is already shown with `*`/`?` confidence markers that a personal rating would have to coexist with; and whether recommendations are content-based (genre/rating similarity, and TMDB's recommendation endpoints — both need a key that is already present in `.env`) or collaborative (needs enough users to be worth anything, which is unlikely on a single-family library).
  - **Note the constraint that shaped this entry:** the organizer container has no `/media` mount and no OpenSubtitles credentials, and `data/` is the only writable bind mount, so any feature that reads the media tree must run host-side and persist into the JSON — the same split `season_scan.py`, `subtitle_runner.py`, `tmdb_ratings.py` and `tmdb_seasons.py` already use. The page must not call out to a provider on render.
  - **Watch out:** the proxy caches the imported module at process start. After any change here, `docker compose build && docker compose up -d --force-recreate` in folder_organizer **and** `docker compose restart` in system_manager, or the dashboard silently serves the old app.

## What remains (per docs/03_IMPLEMENTATION_PLAN.md roadmap)

- **Phase 4 — Programs cleanup**: re-scanned program/game roots (2026-09-16) → 196 SAFE duplicate groups, 2.43 GB reclaimable. The original 90-group proposal was stale (filesystem reorganized since the 9/1 scan; V2RayN tree restructured). `data/cleanup_proposal.json` refreshed; `commands_to_run/delete_list_decided.txt` regenerated with all 196 keeps verified intact. **Pending user execution confirmation.** NOTE 2026-09-17: Phase 4.5 audit reclassified 178 of these as cross-install `review` — see the audit entry above; do not execute the old list.
- **Phase 4.5 — Cross-category duplicate audit (2026-09-17: done, read-only)**: audit complete; 181 groups now sit in `review` pending a user decision. Next: regenerate `delete_list_decided.txt` from the audited proposal only after the user decides on cross-install groups.
- **Phase 5 — Docker cross-platform hardening (2026-09-17)**: pinned configurable base image, non-root UID 10001, `.env` injection, named persistent volumes, localhost binding, dropped capabilities, and `/health` implemented. Isolated Linux Compose build/start, health HTTP 200, environment injection, and fresh-volume writes passed. Test deployment removed; existing data not migrated. Browser preview was blocked by the permission service; Windows validation and production storage migration remain pending. See `04_DEPLOYMENT.md`; dual-host sign-off is not claimed.
- **Phase 6 — Auto-improvement**: Claude updates its own instructions — deferred.
- **Phase 7 — Dashboard enhancements (2026-09-17: Programs subset implemented)**: `/programs` now combines case-insensitive path search with extension filtering, paginates all matches at 300 rows/page, preserves filters in navigation, and shows matching counts, visible ranges, and an empty state. Invalid/negative/excessive page values are handled. A `Download CSV` button exports **all** matching rows (not just the visible page) to a dated file (`programs_YYYY-MM-DD.csv`), carrying the current `q`/`ext` filters. `tests/test_programs.py`: 10 isolated tests passed (pagination, clamping, search, extension, combined filters, escapes, CSV export + filtered/empty CSV); existing video-catalog (5) and hardening (4) tests also passed. Live verification: fresh dev server on :5003 returned 200 for `/programs`, and the filtered CSV download returned correct rows/headers (`programs_2026-09-17.csv`). The live :5001 service was still running pre-change code (no auto-reload), so after user approval it was stopped (old PID 1802787) and relaunched with the same invocation — `/programs` now shows the download button and `/programs/export.csv` returns 200 (full export 194,697 bytes / 2,195 rows; filtered exports verified). An `Exception on /programs` line appeared once in tool output at 16:14:40 but was never found in `logs/app_local.log` on re-check and is unexplained — the log currently contains zero ERROR entries and 40 consecutive requests return 200. Read-only checks confirmed all 2,195 catalog records reachable across 8 pages, correct extension-filter counts, and 200 for `/`, `/music`, `/videos`, `/cleanup`, `/health`; the Programs catalog hash stayed unchanged. The mutating live suite was deliberately not rerun against shared data. Browser preview verification remains blocked by permission-service timeouts (curl-based verification used instead). Export polish and enhancements to other catalog pages remain open. Other project sessions were checked for overlap; pre-existing video/poster/music-tag work was left untouched.
- **Phase 8 — New features**: "find similar movies", "auto-organize downloads" — not started. The recommendation half of this is now specified in the **Tasks (not yet built)** section above (user accounts, seen marks, ratings, recommendations), with its open decisions listed.

## Future docs (create on demand)
- `05_VIDEO_SUBTITLE_AUTOMATION.md`
- `06_DUPLICATE_DETECTION_ALGORITHM.md`
- `07_WEB_DASHBOARD_UI.md`
- `08_SECURITY_AND_PRIVACY.md`
- `09_AUTO-UPDATE_CLAUDE_INSTRUCTIONS.md`

Next: say "Create CONTINUATION_01.md" or "Add new feature: X" to spawn the next milestone doc.
