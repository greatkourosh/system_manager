"""MusicBrainz recording lookup, stdlib only.

Lives inside the package rather than at the repo root because the image copies
system_manager/ wholesale but only a fixed list of top-level scripts. The
root-level musicbrainz_lookup.py is that fixed list's host CLI; it loads this
module by path so there is a single implementation.
"""
import json
import time
import urllib.parse
import urllib.request

UA = "MediaOrganizer/1.0 (https://localhost; kourosh@local)"

def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.load(r)
        except Exception:
            if attempt == 2:
                return None
            time.sleep(2 ** (attempt + 1))

def lookup_recordings(entries, limit):
    """entries: [{path, tags: {artist, title}}] -> {path: {score, artist, album, year}}.

    Grouped by (artist, title) so a whole album of one artist is one query.
    MusicBrainz allows one request per second.
    """
    seen = {}
    for e in entries:
        a = (e.get("tags") or {}).get("artist", "")
        t = (e.get("tags") or {}).get("title", "")
        if not t:
            continue
        seen.setdefault((a.lower(), t.lower()), []).append(e)
    out = {}
    n = 0
    for (artist, title), group in seen.items():
        if n >= limit:
            break
        qs = urllib.parse.quote(f'artist:"{artist}" AND recording:"{title}"' if artist
                                else f'recording:"{title}"')
        data = _get(f"https://musicbrainz.org/ws/2/recording/?query={qs}&fmt=json&limit=3")
        n += 1
        time.sleep(1.1)
        if not data or not data.get("recordings"):
            continue
        rec = data["recordings"][0]
        releases = rec.get("releases") or []
        date = releases[0].get("date", "") if releases else ""
        year = date[:4] if date[:4].isdigit() else None
        for e in group:
            out[e["path"]] = {"score": int(rec.get("score", 0)),
                              "artist": (rec.get("artist-credit") or [{}])[0].get("name"),
                              "album": releases[0].get("title") if releases else None,
                              "year": year}
    return out