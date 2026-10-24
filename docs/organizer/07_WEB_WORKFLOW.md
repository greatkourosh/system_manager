# Web-Based Dedupe Workflow (2026-09-01)

## Dashboard
Flask app in Docker, http://localhost:5001 (5000 is taken by project-dashboard).

Pages:
- `/` Dashboard — totals per drive/category
- `/scan` — raw duplicates list. Per-copy **Skip** and per-set **Skip all** write folder+extension skip rules (same as `/select`); sets left with fewer than two unskipped copies are hidden.
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

- Detection (`tag_detect.py` + `genre_rules.json`): artist/album from folder segments after `Music`, title from cleaned filename (junk `[site]`/`{tag}`/track numbers stripped), year from folder pattern → mtime fallback, genre from keyword rules (Persian Traditional/Religious/Pop, International Rock/Classical/Jazz, Podcast, Game Soundtrack; script-aware prefixes). Non-Music roots get genre+title only. **Flat folders** (one level below Music, e.g. `Turkish/`, `vMusic/`) read artist and title from the filename when it follows `Artist - Title`; a bare track name gets title only.
- Plan state: `data/tag_plan.json` keyed by path; per-field `{value, source: auto|manual|musicbrainz, overwrite, confidence}`; fill-empty-by-default; manual overwrite per-field opt-in.
- Batch caps: max 5000 files per propose request.
- MusicBrainz (opt-in, host): `python musicbrainz_lookup.py [--limit N]` writes `data/mb_candidates.json`; "Merge MusicBrainz" button fills empty plan fields (score ≥ 70).
- Export writes `commands_to_run/tag_fix_list.json` + shows copyable dry-run/apply commands; apply re-checks each file's live tags and skips fields that became non-empty; log at `commands_to_run/tag_log.txt`.
- E2E verified 2026-09-01: 91 Ebi files proposed → manual edit → export (321 existing-tag fields correctly guarded) → dry-run → real apply on 2 files → mutagen read-back confirmed → idempotent re-run (2 SKIPPED).

## Music library (2026-10-13 — /music)
**/music is a card library** of the 31,111 music tracks in `data/music_catalog.json`, in the shape of the video page: filters, sort, paging, and a per-group track list.

- **Two grouping modes.** `?mode=artist` groups by the folder after `Music`; `?mode=folder` groups by the album directory. The same filters apply to both: search, drive, format, and sort (tracks / size / name). 48 groups per page.
- **Card images.** Artist mode lazy-loads a photo from `data/artist_images/` via `/api/music/artist-image`; folder mode serves the album's local `folder.jpg` (or `cover.jpg` / `front.jpg`) via `/api/music/folder-cover`. The cover endpoint only serves files under `/media/kourosh/Multimedia/Music` — a path outside that root 404s.
- **Artist photos** are fetched host-side by `artist_images.py`: MusicBrainz artist → Wikidata → P18 → a 300px Wikimedia Commons thumbnail, cached as base64 under `data/artist_images/`. Artists with no confident match are recorded in `data/artist_images_missing.json` so a rerun skips them.
- **The untagged badge is a button.** It proposes tags for that group's untagged tracks, auto-accepts the confident ones, and writes them — see "Fix untagged tracks" below.

### Fix untagged tracks (2026-10-13)
Clicking an untagged badge posts to `/api/music/fix-untagged`, which does the whole chain for one artist or folder:

1. Proposes tags from the path (`tag_detect.propose_for`). A flat folder's
   bare track name ("01 - Hayat Sana Guzel") gives title only; an
   "Artist - Title" one gives artist too.
2. Looks up a **MusicBrainz release year** for any track whose year is only an mtime guess. The lookup is **per album**: one release query gives the album's date and its track list, and a track is dated only when that release lists it. `mb_lookup.py` lives inside the package because the image copies `system_manager/` wholesale; the root `musicbrainz_lookup.py` CLI still writes `data/mb_candidates.json` for the Music Tags page.
3. Fills an empty artist/album/year from a **recording search** for any track
   whose filename gave a title but no artist. The credited artist wins
   over the junk tag a burner wrote into the file (`Bivafa…` on this
   library), but only when the proposal had no artist of its own. Capped at
   the same 20 per group as the track-year fallback.
4. Accepts a track when at least one field is high/med confidence, empty in the audit, and would not overwrite. A low-confidence field is dropped from the write, not used to veto the track.
5. Writes the group's entries to its own `commands_to_run/tag_fix_group.json` — never the shared `tag_fix_list.json` — and runs `apply_music_tags.apply()`, which now takes the list path as an argument.
6. **Dry run first, then a confirm, then the real write.** The button shows the dry-run summary and asks before touching any file.
7. Folds what was written back into `data/music_tag_audit.json`, so the card's untagged count drops instead of offering the same tracks again.
8. **A group larger than 500 tracks is done in chunks.** Reading a tag costs about 27 ms, so one request cannot cover thousands of files — the browser would give up long before the work was done. The response carries `next_offset`, and the button offers the next chunk until the group is finished.

The container can write tags itself: it has the Music mount and `mutagen`. The lookups cost one MusicBrainz request per album (and one per flat-track recording) at one request per second, and the results are cached for the life of the process — so a click costs one request per album, not one per track.

> The year comes from the album, not from the track. Looking a track up on its own dates it to whichever release matched its title first, which is often a live or compilation release: "Nostalgia" came back as 2020 from "Live at the Acropolis" instead of 2010 from "The Inspiring Journey", and a whole album's tracks were each dated to a different album. A track whose folder has no release of its own — a compilation — falls back to the year of the studio album it came from, but only for a handful of tracks: each fallback is another request, and an artist whose albums are all unknown to MusicBrainz (Fery, for one) would otherwise cost minutes of waiting for nothing. A track that only appears on live and compilation releases stays undated rather than being dated to a concert.

> A group whose files already carry the proposed tags is reported as "nothing to write" rather than queued. That normally means `data/music_tag_audit.json` is stale — re-run `python3 music_tag_audit.py` (host) and try again.

> When nothing in a group can be proposed, the failure carries a link to the Music Tags page, filtered to that group: the folder for a folder group, the artist's name in the search box for an artist, and the missing field the group was missing most. The button offers to open it.

### Tag audit fix (2026-10-14)
`music_tag_audit.py` read WMA tags with `v[0].strip()`, but mutagen returns `ASFUnicodeAttribute` for that format, so every WMA file raised and was recorded as an error with no tags — 18 files looked untagged when they were fully tagged. The reader now converts with `str()` first. Re-running the audit dropped the reader errors from 21 to 3 and the "needs fix" count from 13,125 to 13,107.

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
`data/scan_summary.json` · `catalog_full.json` · `{music,video,programs,disk_images}_catalog.json` · `duplicates.json` · `cleanup_proposal.json` · `selection_state.json` · `skip_rules.json` · `music_tag_audit.json` · `tag_plan.json` · `artist_images/` (cached artist photos) · `artist_images_missing.json` · `mb_candidates.json` (written on demand by `musicbrainz_lookup.py`)

### Subtitle fetcher — live (2026-09-02)
- Login flow verified: /login (username+password from .env) → Bearer token → /download → link on www.opensubtitles.com needs BROWSER-like headers (Cloudflare error 1010 otherwise).
- Account: level "Sub leecher", 20 downloads/day, quota renews 23:59 UTC.
- Usage: `python fetch_subtitles.py --apply --budget 18 --root serials --langs fa` (dry-run without --apply; skips existing; resumable daily).
- 20 subtitles delivered and verified 2026-09-02 (Gone with the Wind, Animal Farm, Bates Motel featurettes…).
- Note: serial card walk includes Featurettes/Deleted Scenes folders — subtitle hits there are low-value; consider excluding extras dirs in a future pass.
