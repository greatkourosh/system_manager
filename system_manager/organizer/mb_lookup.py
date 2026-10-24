"""MusicBrainz release lookup, stdlib only.

Lives inside the package rather than at the repo root because the image copies
system_manager/ wholesale but only a fixed list of top-level scripts. The
root-level musicbrainz_lookup.py is that fixed list's host CLI; it is
standalone and keeps its own copy of the per-track lookup it needs to write
data/mb_candidates.json for the Music Tags page.
"""
import json
import time
import urllib.parse
import urllib.request

UA = "MediaOrganizer/1.0 (https://localhost; kourosh@local)"

# MusicBrainz asks a client for one request per second.
MB_INTERVAL = 1.1

# How many tracks may fall back to a per-track lookup when the album itself
# is unknown to MusicBrainz. Each one costs a request, and an artist whose
# albums are all unknown would turn the fallback into minutes of waiting for
# nothing. Past this many, the tracks are left undated.
TRACK_FALLBACK_MAX = 20

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

def norm(text):
    """Lowercase and drop punctuation, so "One Man's Dream" and
    "One Mans Dream" compare equal. Filenames and release titles spell the
    same track differently often enough to matter.

    An apostrophe is dropped rather than spaced out: a filename writes
    "Im So" where the release title writes "I'm So", so spacing the
    apostrophe apart would stop the two from matching.
    """
    cleaned = "".join(ch if (ch.isalnum() or ch.isspace()) else " "
                      for ch in (text or "").lower().replace("'", ""))
    return " ".join(cleaned.split())

def _search(kind, query, limit=10):
    qs = urllib.parse.quote(query)
    data = _get(f"https://musicbrainz.org/ws/2/{kind}/?query={qs}&fmt=json&limit={limit}")
    return (data or {}).get(kind + "s") or []

def _release_detail(release_id):
    """The release's own date and track list.

    The search result leaves the date empty for some releases, so the year
    has to come from the release itself.
    """
    data = _get(f"https://musicbrainz.org/ws/2/release/{release_id}"
                f"?inc=recordings&fmt=json")
    if not data:
        return None, {}
    date = data.get("date") or ""
    year = date[:4] if date[:4].isdigit() else None
    tracks = {}
    for medium in data.get("media") or []:
        for track in medium.get("tracks") or []:
            title = norm(track.get("title"))
            if title:
                tracks.setdefault(title, track.get("position"))
    return year, tracks

# An album's release date does not change, so the lookups are cached for the
# life of the process. One click asks for the same album twice -- once to
# preview and once to write -- and each lookup costs a request.
_album_cache = {}
_track_cache = {}
_recording_cache = {}

def lookup_albums(albums, limit=30):
    """albums: [{artist, album}] -> {(artist, album): {"year", "tracks", "title"}}.

    One query per album, not per track: an album's tracks share its release
    date, and asking MusicBrainz per track is both slow and wrong -- the first
    recording match for a track is often a live or compilation release, so
    the track's own year comes back as the year of the wrong album.

    Each album costs a request and MusicBrainz allows one a second, so the
    number of lookups is capped; an album past the cap is left undated.
    """
    out = {}
    asked = 0
    for entry in albums:
        artist = (entry.get("artist") or "").strip()
        album = (entry.get("album") or "").strip()
        if not album:
            continue
        key = (artist, album)
        if key in out:
            continue
        if key in _album_cache:
            out[key] = _album_cache[key]
            continue
        if asked >= limit:
            out[key] = None
            _album_cache[key] = None
            continue
        asked += 1
        query = f'release:"{album}"' + (f' AND artist:"{artist}"' if artist else "")
        releases = _search("release", query)
        if not releases:
            out[key] = None
            _album_cache[key] = None
            time.sleep(MB_INTERVAL)
            continue
        # A release whose title matches the album exactly beats a fuzzy one.
        want = norm(album)
        exact = [r for r in releases if norm(r.get("title")) == want]
        release = (exact or releases)[0]
        year, tracks = _release_detail(release["id"]) if release.get("id") else (None, {})
        out[key] = {"year": year, "title": release.get("title"), "tracks": tracks,
                    "score": int(release.get("score", 0))}
        _album_cache[key] = out[key]
        time.sleep(MB_INTERVAL)
    return out

def lookup_recordings(records, limit=20):
    """records: [{artist?, title}] -> {(artist_or_empty, title): {"artist", "album", "year"}}.

    For a track whose own tags say nothing, the recording search finds who
    actually recorded it: the best match's credited artist, its first
    release's album and date. The filename that carried the title is not
    proof of the artist -- a burner often wrote the same junk into every
    file of one compilation -- so the credited artist wins only when the
    search agrees a recording exists at all.

    Keyed by the *input* artist (possibly empty), not the credited one, so
    the caller can find its answer. Caching follows that same key: the same
    filename asked twice costs one request.
    """
    out = {}
    asked = 0
    for entry in records:
        artist = (entry.get("artist") or "").strip()
        title = (entry.get("title") or "").strip()
        if not title:
            continue
        key = (artist, title)
        if key in out:
            continue
        if key in _recording_cache:
            out[key] = _recording_cache[key]
            continue
        if asked >= limit:
            out[key] = None
            _recording_cache[key] = None
            continue
        asked += 1
        query = f'recording:"{title}"' + (f' AND artist:"{artist}"' if artist else "")
        qs = urllib.parse.quote(query)
        data = _get(f"https://musicbrainz.org/ws/2/recording/?query={qs}&fmt=json&limit=3")
        time.sleep(MB_INTERVAL)
        recordings = (data or {}).get("recordings") or []
        if not recordings:
            out[key] = None
            _recording_cache[key] = None
            continue
        rec = recordings[0]
        releases = rec.get("releases") or []
        date = (releases[0].get("date") or "") if releases else ""
        best = {
            "artist": (rec.get("artist-credit") or [{}])[0].get("name"),
            "album": releases[0].get("title") if releases else None,
            "year": date[:4] if date[:4].isdigit() else None,
        }
        out[key] = best
        _recording_cache[key] = best
    return out

def lookup_track_years(tracks, limit=20):
    """tracks: [{artist, title}] -> {(artist, title): {"year", "album"}}.

    The year of a track that has no album of its own -- a track on a
    compilation -- is the year of the studio album it came from. The
    releases a recording appears on are scanned for one that is a studio
    album, and a live or compilation release is skipped: dating a track by
    the concert it was played in is worse than leaving it undated.

    Each track costs a request and MusicBrainz allows one a second, so the
    number of lookups is capped. A group that needs more than the cap gets
    its tracks looked up only up to the cap; the rest stay undated, which is
    the honest answer for a track that has no studio album anyway.
    """
    out = {}
    asked = 0
    for entry in tracks:
        artist = (entry.get("artist") or "").strip()
        title = (entry.get("title") or "").strip()
        if not title:
            continue
        key = (artist, title)
        if key in out:
            continue
        if key in _track_cache:
            out[key] = _track_cache[key]
            continue
        if asked >= limit:
            out[key] = None
            _track_cache[key] = None
            continue
        asked += 1
        query = f'recording:"{title}"' + (f' AND artist:"{artist}"' if artist else "")
        qs = urllib.parse.quote(query)
        data = _get(f"https://musicbrainz.org/ws/2/recording/?query={qs}&fmt=json&limit=1&inc=releases")
        time.sleep(MB_INTERVAL)
        recordings = (data or {}).get("recordings") or []
        if not recordings:
            out[key] = None
            _track_cache[key] = None
            continue
        best = None
        for release in recordings[0].get("releases") or []:
            group = release.get("release-group") or {}
            date = release.get("date") or ""
            if not date[:4].isdigit():
                continue
            if group.get("primary-type") != "Album" or group.get("secondary-types"):
                continue
            if release.get("status") not in (None, "Official"):
                continue
            best = {"year": date[:4], "album": release.get("title")}
            break
        out[key] = best
        _track_cache[key] = best
    return out
