#!/usr/bin/env python3
"""Read real season numbers off disk, for the video library's serial cards.

A card's `season`/`seasons` fields are parsed from the folder *name* ("S01 of 03+"),
which records what the name claims, not what exists. On the real library that
claim is wrong for 46 of 124 serials — e.g. "The Expanse S01 of 06" ships only S01.
This module walks a card's directory instead, so the badge reflects the disk.

Deliberately separate from video_catalog.py: that file is pure string ops and
runs wherever the JSON does, including the container, which has no media mount.
"""
import os
import re

# A season named by a directory. Anchored on non-alphanumeric edges so "SCI-FI"
# or a title word can't supply a season number, but a title prefix and a space
# after S are allowed ("Luther S 01", "Bates Motel Season 1").
_RE_DIR = re.compile(r"(?:^|[^A-Za-z0-9])(?:S\s*0?(\d{1,2})|Season\s*0?(\d{1,2}))(?![0-9])", re.I)

# Season stated explicitly by a filename: S01E02 or 01x02.
_RE_SE = re.compile(r"(?:^|[^A-Za-z0-9])(?:S(\d{1,2})E\d{1,3}|(\d{1,2})x\d{1,3})(?![0-9])", re.I)
_RE_SEASON_EPISODE = re.compile(r"Season\s*(\d{1,2})\s+Episode", re.I)

# No season token anywhere, only an episode number -> season 1. Each alternative
# requires a separator so resolutions and release tags (720p, 1080p, x264) and
# bare season-less numbers can never be read as an episode.
_RE_EP_WORD = re.compile(r"(?:^|[^A-Za-z0-9])E(\d{1,3})(?![0-9])", re.I)
_RE_EP_DASH = re.compile(r"\s-\s(\d{1,2})(?:\s*[\.\[]|\s|$)", re.I)
_RE_EP_BARE = re.compile(r"(?:^|\]\s|\s-\s|\.\s)E?(\d{1,2})(?![\d0-9pPiIxXa-zA-Z])", re.I)

# Only video files prove a season exists. Reading subtitles instead invents
# one: Chernobyl's .srt set includes "Chernobyl - 08x03 - ...", which would
# otherwise report a season 8 the five .mkv episodes have no support for.
VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".mov", ".m4v", ".ts", ".wmv", ".flv",
               ".mpg", ".mpeg", ".m2ts", ".iso", ".webm", ".divx", ".rmvb"}

# Non-video entries that sit alongside the media.
_SKIP_DIRS = {"subs", "sub", "subtitles", "extras", "specials", "featurettes",
              "ova", "nc", "audio", "video"}


def seasons_in_dir(path):
    """Sorted season numbers present in `path`, or None if it cannot be read.

    An empty list means the directory was readable but held nothing that
    identifies a season (a book-named folder, a pile of unrelated shorts).
    """
    try:
        entries = os.listdir(path)
    except OSError:
        return None

    found = set()
    for entry in entries:
        if entry.startswith("."):
            continue
        full = os.path.join(path, entry)
        if os.path.isdir(full):
            if entry.lower() in _SKIP_DIRS:
                continue
            m = _RE_DIR.search(entry)
            if m:
                found.add(int(m.group(1) or m.group(2)))
            continue
        if os.path.splitext(entry)[1].lower() not in VIDEO_EXTS:
            continue
        m = _RE_SE.search(entry) or _RE_SEASON_EPISODE.search(entry)
        if m:
            found.add(int(next(g for g in m.groups() if g)))
            continue
        m = _RE_EP_WORD.search(entry) or _RE_EP_DASH.search(entry) or _RE_EP_BARE.search(entry)
        if m:
            found.add(1)
    return sorted(found)


def season_state(present, claimed_seasons=None):
    """Classify a serial card as complete / incomplete / unknown.

    `present` is seasons_in_dir() output; `claimed_seasons` is the count parsed
    from the folder name, used only to decide whether a gap is real. Returns a
    dict with `state`, `seasons_present` and `seasons_missing` (the last only
    when the name made a claim we can check against).
    """
    if present is None:
        return {"state": "unknown", "seasons_present": None, "seasons_missing": None}
    if not present:
        return {"state": "unknown", "seasons_present": [], "seasons_missing": None}

    try:
        claimed = int(claimed_seasons)
    except (TypeError, ValueError):
        claimed = 0
    # With no usable claim, only the "we saw more than one season" case is a
    # finding; a single season found is not evidence of completeness.
    if claimed <= 0:
        return {"state": "present" if len(present) > 1 else "unknown",
                "seasons_present": present, "seasons_missing": None}

    missing = [s for s in range(1, claimed + 1) if s not in present]
    return {"state": "incomplete" if missing else "complete",
            "seasons_present": present, "seasons_missing": missing}
