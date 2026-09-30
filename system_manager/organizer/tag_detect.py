#!/usr/bin/env python3
"""Tag auto-detection from path/filename patterns. Pure string ops — runs in container (no mutagen,
no drive access) and as host CLI for spot-checks."""
import json
import os
import re
import sys
from datetime import date

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


def _has_persian(text):
    return bool(PERSIAN_RE.search(text or ""))


def clean_filename(name):
    stem = os.path.splitext(name)[0]
    stem = stem.replace("_", " ")
    stem = JUNK_RE.sub(" ", stem)
    stem = TRACKNUM_RE.sub("", stem.strip())
    return re.sub(r"\s{2,}", " ", stem).strip(" -.")


def detect_genre(path):
    pl = path.lower().replace("_", " ")
    for rule in GENRE_RULES["rules"]:
        needles = rule["match"] if isinstance(rule["match"], list) else [rule["match"]]
        hit = any(n.lower() in pl for n in needles)
        if not hit:
            continue
        if rule.get("script_aware"):
            prefix = GENRE_RULES["script_prefixes"]["fa"] if _has_persian(path) else GENRE_RULES["script_prefixes"]["default"]
            base = rule.get("base") or (needles[0].capitalize())
            return prefix + base
        return rule["genre"]
    # fallback by script on the artist-ish part
    prefix = GENRE_RULES["script_prefixes"]["fa"] if _has_persian(path) else GENRE_RULES["script_prefixes"]["default"]
    return prefix + GENRE_RULES["fallback"]


def _segment_after_music(parts):
    try:
        i = next(i for i, p in enumerate(parts) if p.lower() == "music")
        if i + 1 < len(parts):
            return parts[i + 1:], True
    except StopIteration:
        pass
    return parts, False


def detect_from_path(path, mtime=None):
    """Return {field: {value, confidence}} for proposed fields only (no value -> absent)."""
    norm = path.replace("/", "\\")
    parts = [p for p in norm.split("\\") if p]
    filename = parts[-1]
    dirs = parts[:-1]
    after_music, in_music = _segment_after_music(dirs)
    prop = {}

    title = clean_filename(filename)
    if title:
        prop["title"] = {"value": title, "confidence": "med" if in_music else "low"}

    genre = detect_genre(path)
    if genre:
        prop["genre"] = {"value": genre, "confidence": "med"}

    if in_music and after_music:
        artist = after_music[0]
        if artist and len(after_music) >= 2:
            prop["artist"] = {"value": artist, "confidence": "high"}
            album = after_music[1]
            if album.lower().strip() not in STOP_FOLDERS and not YEAR_RE.fullmatch(album.strip()):
                prop["album"] = {"value": album, "confidence": "med"}
            elif album.lower().strip() in STOP_FOLDERS and len(after_music) >= 3:
                nxt = after_music[2]
                if nxt.lower().strip() not in STOP_FOLDERS:
                    prop["album"] = {"value": nxt, "confidence": "low"}
        elif artist:
            prop["artist"] = {"value": artist, "confidence": "med"}

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
