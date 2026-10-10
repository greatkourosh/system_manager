#!/usr/bin/env python3
"""MusicBrainz candidate lookup (host-side, opt-in). Reads commands_to_run/tag_fix_list.json,
queries MusicBrainz ws/2 JSON at 1 req/sec for artist/album/year candidates, writes
data/mb_candidates.json keyed by file path. The dashboard's "Merge MusicBrainz" button
then fills plan fields whose value is still empty. Stdlib only.

Usage: python musicbrainz_lookup.py [--limit 200]
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
FIX_LIST = os.path.join(BASE, "commands_to_run", "tag_fix_list.json")
OUT = os.path.join(BASE, "data", "mb_candidates.json")
UA = "MediaOrganizer/1.0 (https://localhost; kourosh@local)"


def mb_get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.load(r)
        except Exception:
            if attempt == 2:
                return None
            time.sleep(2 ** (attempt + 1))


def lookup(entries, limit):
    # group by (artist, title) to avoid duplicate queries
    seen = {}
    for e in entries:
        a = (e.get("tags") or {}).get("artist", "")
        t = (e.get("tags") or {}).get("title", "")
        if not t:
            continue
        seen.setdefault((a.lower(), t.lower()), []).append(e)
    print(f"{len(seen)} unique lookups (limit {limit})")
    out = {}
    n = 0
    for (artist, title), group in seen.items():
        if n >= limit:
            print(f"limit reached — {len(seen) - n} lookups not attempted")
            break
        qs = urllib.parse.quote(f'artist:"{artist}" AND recording:"{title}"' if artist else f'recording:"{title}"')
        url = (f"https://musicbrainz.org/ws/2/recording/?query={qs}&fmt=json&limit=3")
        data = mb_get(url)
        n += 1
        time.sleep(1.1)  # MusicBrainz rate limit
        if not data or not data.get("recordings"):
            continue
        rec = data["recordings"][0]
        score = int(rec.get("score", 0))
        r_artist = (rec.get("artist-credit") or [{}])[0].get("name")
        releases = rec.get("releases") or []
        r_album = releases[0].get("title") if releases else None
        r_date = releases[0].get("date", "") if releases else ""
        r_year = r_date[:4] if r_date and r_date[:4].isdigit() else None
        for e in group:
            out[e["path"]] = {"score": score, "artist": r_artist, "album": r_album, "year": r_year}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"wrote {len(out)} candidates -> data/mb_candidates.json")


if __name__ == "__main__":
    if not os.path.exists(FIX_LIST):
        print("No commands_to_run/tag_fix_list.json — export a tag plan first.")
        sys.exit(1)
    with open(FIX_LIST, encoding="utf-8") as f:
        entries = json.load(f)["entries"]
    limit = 200
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])
    lookup(entries, limit)
