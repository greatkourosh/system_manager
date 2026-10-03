#!/usr/bin/env python3
"""Tag auto-detection from path/filename patterns. Pure string ops — runs in container (no mutagen,
no drive access) and as host CLI for spot-checks."""
import json
import os
import re
import sys
from datetime import date
from functools import lru_cache

BASE = os.path.dirname(os.path.abspath(__file__))
FIELDS = ("artist", "title", "album", "year", "genre")

with open(os.path.join(BASE, "genre_rules.json"), encoding="utf-8") as _f:
    GENRE_RULES = json.load(_f)

JUNK_RE = re.compile(
    r"\[.*?(ir|info|com|net|org|ir)\]|\{[^}]*\}|www\.[^\s]+|@[^\s]+|\(\d{3,4}\s*k?bps?\)|"
    r"\(?(128|192|256|320)\s*k?bits?\)?|(Full Album|Official|Audio|Lyrics?)(?=[\s_]*$)", re.I)
TRACKNUM_RE = re.compile(r"^\d{1,3}[\s._\-]+")
PERSIAN_RE = re.compile(r"[؀-ۿ]")
YEAR_RE = re.compile(r"\b(19[5-9]\d|20[0-2]\d)\b")

STOP_FOLDERS = {"files", "music", "audio", "cd", "cds", "new", "old", "mp3", "songs", "album",
                "music old", "full album", "best", "best of", "single", "telegram", "whatsapp",
                "whatsapp audio", "telegram audio", "telegram documents", "video", "videos",
                "video_files", "documents", "download", "downloads", "misc", "others", "other"}


@lru_cache(maxsize=512)
def _needle_re(needle):
    """A genre needle that only matches on a word boundary.

    Plain substring matching finds "ney" inside "jour|ney" and "tar " inside
    "so|tar", which tagged English tracks as Persian Traditional. A needle
    that already carries its own boundary ("\\Games\\") is used as-is.
    """
    n = needle.strip()
    if not n:
        return None
    if re.match(r"^\\.*\\$", n) or n[0] in "،/" or n[-1] in "،/":
        return re.compile(re.escape(n), re.I)
    return re.compile(r"(?<!\w)" + re.escape(n) + r"(?!\w)", re.I)


def _has_persian(text):
    return bool(PERSIAN_RE.search(text or ""))


def clean_filename(name):
    stem = os.path.splitext(name)[0]
    stem = stem.replace("_", " ")
    stem = JUNK_RE.sub(" ", stem)
    stem = TRACKNUM_RE.sub("", stem.strip())
    return re.sub(r"\s{2,}", " ", stem).strip(" -.")


def detect_genre(path):
    """Return a genre only when a rule actually matched, else None.

    No fallback: a placeholder like "International Unknown" is worse than an
    empty tag, because it turns "no data" into data the library will never
    re-audit as missing.
    """
    pl = path.lower().replace("_", " ")
    for rule in GENRE_RULES["rules"]:
        needles = rule["match"] if isinstance(rule["match"], list) else [rule["match"]]
        hit = any((r := _needle_re(n)) and r.search(pl) for n in needles)
        if not hit:
            continue
        if rule.get("script_aware"):
            prefix = GENRE_RULES["script_prefixes"]["fa"] if _has_persian(path) else GENRE_RULES["script_prefixes"]["default"]
            base = rule.get("base") or (needles[0].capitalize())
            return prefix + base
        return rule["genre"]
    return None


def _segment_after_music(parts):
    try:
        i = next(i for i, p in enumerate(parts) if p.lower() == "music")
        if i + 1 < len(parts):
            return parts[i + 1:], True
    except StopIteration:
        pass
    return parts, False


def detect_from_path(path, mtime=None):
    """Return {field: {value, confidence}} for proposed fields only (no value -> absent).

    Only suggests fields the layout actually determines. A flat folder like
    `Music/vMusic/` holds files named "001) Artist - Title.mp3"; taking the
    folder as the artist or the whole filename as the title writes "vMusic"
    into 875 tracks, so those cases propose nothing instead.
    """
    norm = path.replace("/", "\\")
    parts = [p for p in norm.split("\\") if p]
    filename = parts[-1]
    dirs = parts[:-1]
    after_music, in_music = _segment_after_music(dirs)
    prop = {}

    # `Music/<artist>/<album>/track` — a track sits at least two levels below
    # Music, so there is a real artist folder to read.
    nested = in_music and len(after_music) >= 2

    if nested:
        title = clean_filename(filename)
        if title:
            prop["title"] = {"value": title, "confidence": "med"}

        artist = after_music[0]
        prop["artist"] = {"value": artist, "confidence": "high"}
        album = after_music[1]
        if album.lower().strip() not in STOP_FOLDERS and not YEAR_RE.fullmatch(album.strip()):
            prop["album"] = {"value": album, "confidence": "med"}
        elif album.lower().strip() in STOP_FOLDERS and len(after_music) >= 3:
            nxt = after_music[2]
            if nxt.lower().strip() not in STOP_FOLDERS:
                prop["album"] = {"value": nxt, "confidence": "low"}

        # year: scan music-relative folder names, then fall back to mtime
        year = None
        for seg in after_music:
            m = YEAR_RE.search(seg)
            if m:
                year = m.group(1)
                break
        if year:
            prop["year"] = {"value": year, "confidence": "med"}
        elif mtime:
            try:
                y = date.fromtimestamp(mtime).year
                if 1900 <= y <= 2026:
                    prop["year"] = {"value": str(y), "confidence": "low"}
            except (OSError, OverflowError, ValueError):
                pass

    genre = detect_genre(path)
    if genre:
        prop["genre"] = {"value": genre, "confidence": "med"}
    return prop


def propose_for(entries, audit_by_path=None):
    """entries: [{path, size_bytes?, ext?, mtime}] -> {path: {field: {value, source, overwrite, confidence}}}"""
    out = {}
    for e in entries:
        detected = detect_from_path(e["path"], e.get("mtime"))
        if not detected:
            continue
        fields = {}
        for f, d in detected.items():
            overwrite = False
            if audit_by_path:
                cur = audit_by_path.get(e["path"])
                # propose overwrite only when we're confident and the current tag is junk-looking
                if cur and cur.get(f) and d["confidence"] == "high" and f == "artist" and JUNK_RE.search(cur[f]):
                    overwrite = True
            fields[f] = {"value": d["value"], "source": "auto",
                         "overwrite": overwrite, "confidence": d["confidence"]}
        if fields:
            out[e["path"]] = {"fields": fields, "accepted": False, "applied": False}
    return out


def main():
    if len(sys.argv) < 2:
        print("usage: python tag_detect.py <path-or-substring> [catalog-filter]")
        sys.exit(1)
    needle = sys.argv[1].lower()
    cat_path = os.path.join(BASE, "data", "music_catalog.json")
    with open(cat_path, encoding="utf-8") as f:
        catalog = json.load(f)
    hits = [c for c in catalog if needle in c["path"].lower()][:20]
    for c in hits:
        d = detect_from_path(c["path"], c.get("mtime"))
        print(c["path"][:90])
        for f, v in d.items():
            print(f"   {f:7} = {v['value']!r:40} ({v['confidence']})")
        print()
    print(f"{len(hits)} shown")


if __name__ == "__main__":
    main()
