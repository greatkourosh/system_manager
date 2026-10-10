# Phase 2 Kickoff — Duplicates + Music Tags (2026-09-01)

## Cleanup proposal (data/cleanup_proposal.json + .md)
- **SAFE: 3,770 groups / 30.7 GB** — identical filenames, byte-identical content. Machine-decidable; delete list ready for one confirmation.
- **REVIEW: 447 groups / 11.3 GB** — differing filenames (renamed copies, media with wrong titles). Need user eyes.
- Categories (safe): music 2,812 · image 671 · video 146 · program 90 · archive 32 · document 19.
- Keep-copy chosen via path heuristics (prefer originals over "(2)" copies, organized roots, shallower tmp).
- Dashboard: /cleanup page with Safe/Review/All tabs.

## Music tag audit (data/music_tag_audit.json)
- 31,111 files audited with mutagen.
- **8,600 fully tagged · 22,504 incomplete · 7 read errors** → 22,511 need fixing (72%).
- Missing fields: genre 18,207 · year 17,488 · album 14,498 · artist 13,262 · title 13,157.
- Dashboard: /music/tags page with worst-folder breakdown.
- Largest problem area: G:/Music/Fery (17,231 tracks) — tags sparse there.

## Next actions (awaiting user confirmation)
1. Execute SAFE deletions (30.7 GB) — one "yes" triggers; everything logged to /logs/actions.
2. Auto-tag pass: fill missing artist/title/album from folder+filename patterns (per user: genres auto-detected); year from file mtime fallback.
3. Review list presented group-by-group in chat for the 447 REVIEW groups (11.3 GB).
