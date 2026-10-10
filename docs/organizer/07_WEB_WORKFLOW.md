# Web-Based Dedupe Workflow (2026-09-01)

## Dashboard
Flask app in Docker, http://localhost:5001 (5000 is taken by project-dashboard).

Pages:
- `/` Dashboard — totals per drive/category
- `/scan` — raw duplicates list
- `/cleanup` — read-only safe/review proposal view
- `/select` — **the main workflow**: interactive duplicate selector
- `/music`, `/music/tags` — catalog + tag audit
- `/skipped` — skip-rule manager
- `/videos`, `/programs` — category catalogs

## /select workflow
1. Filter: media type (video/music/...), extension (.mp4/...), risk (safe/review), review-state (reviewed/undecided), skip-state (has/no skipped copies), text
2. Per group: checkbox per copy (red=DEL, green=KEEP), "all"/"none" buttons, "reviewed" tick
3. Bulk buttons: **Mark page reviewed** (filter bar + pagination bar), filtered accept-suggested / mark-reviewed / clear-marks
4. Per-file 🚫 → skip dialog: **A** = folder+all types, **B** = folder+extension only
5. **Run cleanup (decided)** → exports decided groups' marks to `commands_to_run/delete_list_decided.txt` + shows copyable PowerShell command
6. Run the command manually; results in `commands_to_run/delete_log.txt`

UI: night-mode toggle (navbar, cookie-persisted); floating ↑/↓ jump buttons; all controls theme-safe.

State writes are atomic (`tmp` + `os.replace`) with a retry-once reader — concurrent requests during selection saves no longer risk torn reads (fixed 2026-09-01 after test flakiness exposed it).

Skip rules persist in `data/skip_rules.json`; selection in `data/selection_state.json`.

## Group IDs (v2)
`gid = sha256[:16]-category` — stable across proposal rewrites (the old positional `sha-index` scheme broke when groups were removed after an execution run; migrated 2026-09-01).

## Execution record
- 2026-09-01 19:55: video batch, 137 files, 18.74 GB freed, 0 failures.
- 33 SKIP-missing were stale `مثل\hehe\` paths (folder contents moved pre-scan); keep copies verified intact.

## Music tag fixer (Phase 2.5 — /music/tags)
Workflow: **Auto-detect proposals** → review/edit → Accept → **Export fix script** → host `python apply_music_tags.py` (dry-run default, `--apply` writes) → re-run `music_tag_audit.py` to refresh stats.

- Detection (`tag_detect.py` + `genre_rules.json`): artist/album from folder segments after `Music`, title from cleaned filename (junk `[site]`/`{tag}`/track numbers stripped), year from folder pattern → mtime fallback, genre from keyword rules (Persian Traditional/Religious/Pop, International Rock/Classical/Jazz, Podcast, Game Soundtrack; script-aware prefixes). Non-Music roots get genre+title only.
- Plan state: `data/tag_plan.json` keyed by path; per-field `{value, source: auto|manual|musicbrainz, overwrite, confidence}`; fill-empty-by-default; manual overwrite per-field opt-in.
- Batch caps: max 5000 files per propose request.
- MusicBrainz (opt-in, host): `python musicbrainz_lookup.py [--limit N]` writes `data/mb_candidates.json`; "Merge MusicBrainz" button fills empty plan fields (score ≥ 70).
- Export writes `commands_to_run/tag_fix_list.json` + shows copyable dry-run/apply commands; apply re-checks each file's live tags and skips fields that became non-empty; log at `commands_to_run/tag_log.txt`.
- E2E verified 2026-09-01: 91 Ebi files proposed → manual edit → export (321 existing-tag fields correctly guarded) → dry-run → real apply on 2 files → mutagen read-back confirmed → idempotent re-run (2 SKIPPED).

## Phase 3 — Video library + subtitles (2026-09-02)
**/videos is a poster-card library** (782 cards: 317 movies / 332 serials / 133 videos) parsed from folder names: `video_catalog.py` builds `data/video_library.json` (title/year/genres/rating/popularity, serial Sxx-of-yy, Limited flag). Subtitle badges per card: FA/EN present-or-missing (folder-listing based; unmarked subs assumed EN). Filters: section, genre, subtitle state (missing FA/EN/both), search; 36 cards/page; posters lazy-load from `/api/video/poster`.

**2026-09-17 — one card per main item (782 → 446 cards).** The old grouping key included each video's immediate directory, so a movie with a `Featurettes/` tree and a serial split across `S01/…S07/` produced many cards for one title. `video_catalog.py` now groups by the folder directly under the section root via `main_item()`; seasons, `Featurettes`, and extras fold into the parent card and nested subtitles count toward it. Same-named folders on different drives stay separate (the drive is part of the key). Existing TMDB posters survive the regroup: each card carries `poster_id` (the pre-regroup card id whose `data/posters_b64/` hash still matches) and the template posts that id to `/api/video/poster`. Rebuild with `python video_catalog.py`; it is idempotent and re-reads the current library to preserve poster refs.

- **Posters (host)**: `python tmdb_client.py [--limit N] [--root movies]` — TMDB search via proxy 192.168.1.13:10810 (TMDB direct is blocked), saves `poster.jpg` into each movie folder + base64 copy into `data/posters_b64/` for the dashboard. State: `data/posters_state.json` (idempotent, re-runs skip existing).
- **Subtitles (host)**: `python fetch_subtitles.py [--apply] [--limit N] [--budget N] [--langs en,fa]` — OpenSubtitles search+download per missing language, saves `<videostem>.<lang>.srt` beside the video, rate-limited, log `commands_to_run/subtitle_log.txt`. See "Subtitle fetcher — live" below.
- **Name fix**: 28/317 movie folders don't match the `Year - Title - Genres rating pop` convention (list visible as missing-genre cards); rename proposals pending.

Keys in `.env`: TMDB_API_KEY (works), OPENSUBTITLES_API_KEY + OPENSUBTITLES_USERNAME + OPENSUBTITLES_PASSWORD (login flow live), OMDB (dropped — 401, redundant with TMDB).

## Universal Folder Fix (2026-09-02 — replaces Music Folders + Name Fix)
**/folders** — one page, all areas: music · videos · learning · programs · pictures · documents.
Engine `folder_fix.py` + config `naming_schemas.json`. Conventions per area:
- **music** `Music/<Artist>/Year - Album` (site tags, dots-for-spaces, embedded artist, underscore runs, year separator — 406 proposals)
- **videos** Movies/Serials title folders only (existing parse_movie/parse_serial validators; Featurettes/SUB/season subdirs excluded — 9)
- **learning** `Learning/<Topic>/<course>`: bracket tags, unicode dashes, trailing date fragments, double spaces; ordinal lesson folders (`01_Retrofit`) and code dirs untouched (11)
- **programs** site tags + underscores; dot-style version names flagged for review, not auto-converted (706)
- **pictures** `YYYY - Event` Persian-year events, unspaced `&` (9)
- **documents** light-only (site tags/underscores/double spaces); Arduino sketch & code-project dirs excluded (579)

Total ~1,720 proposals. CLI: `python folder_fix.py [--area a,b]`. Export → `commands_to_run/folder_fix_list.json` → `python apply_folder_fix.py [--area a]` (dry-run default, `--apply` renames, logged to `commands_to_run/folder_fix_log.txt`). Statuses accepted in the UI persist across re-scans; legacy accepted statuses from music_folder_proposals.json / name_fix_proposals.json were migrated.
Superseded: music_folder_fix.py, apply_music_folder_fix.py, name_fix.py, apply_name_fix.py, /music/folders, /namefix (parity verified: 387/389 identical, 2 improved).

## Tests
`python tests/test_app.py` — 73 checks against the live server (pages, theme, APIs, selection, exports, skip rules, tag-plan workflow, universal folder-fix round-trip, UI markers). Restores selection/skip/plan/folder-fix state afterward. Latest: 2× green on 2026-09-02 after the universal folder-fixer refactor.

## Data files
`data/scan_summary.json` · `catalog_full.json` · `{music,video,programs,disk_images}_catalog.json` · `duplicates.json` · `cleanup_proposal.json` · `selection_state.json` · `skip_rules.json` · `music_tag_audit.json`

### Subtitle fetcher — live (2026-09-02)
- Login flow verified: /login (username+password from .env) → Bearer token → /download → link on www.opensubtitles.com needs BROWSER-like headers (Cloudflare error 1010 otherwise).
- Account: level "Sub leecher", 20 downloads/day, quota renews 23:59 UTC.
- Usage: `python fetch_subtitles.py --apply --budget 18 --root serials --langs fa` (dry-run without --apply; skips existing; resumable daily).
- 20 subtitles delivered and verified 2026-09-02 (Gone with the Wind, Animal Farm, Bates Motel featurettes…).
- Note: serial card walk includes Featurettes/Deleted Scenes folders — subtitle hits there are low-value; consider excluding extras dirs in a future pass.
