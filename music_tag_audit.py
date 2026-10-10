#!/usr/bin/env python3
"""Audit music files for missing/incorrect tags using mutagen. Output: data/music_tag_audit.json"""
import json
import os
import shutil
import sys
from collections import Counter, defaultdict
from datetime import date

from mutagen import File as MutagenFile
from mutagen.flac import FLAC
from mutagen.id3 import ID3
from mutagen.mp4 import MP4
from mutagen.wave import WAVE

from media_path import to_host_path

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")

with open(os.path.join(DATA, "music_catalog.json"), encoding="utf-8") as f:
    catalog = json.load(f)

today = date.today().isoformat()


def read_tags(path):
    """Return dict of normalized tags or raise."""
    audio = MutagenFile(path, easy=True)
    if audio is None:
        return None
    def g(k):
        v = audio.get(k)
        return v[0].strip() if v and v[0].strip() else None
    return {
        "artist": g("artist"),
        "title": g("title"),
        "album": g("album"),
        "year": g("date"),
        "genre": g("genre"),
    }


results = []
err_count = 0
gone_count = 0
for i, f in enumerate(catalog):
    path = f["path"]
    entry = {"path": path, "size_bytes": f["size_bytes"], "ext": f["ext"]}
    host = to_host_path(path)
    if not os.path.exists(host):
        # The catalog was scanned once; folders get renamed or deleted since.
        # That is drift, not a broken file, so it must not look like work to do.
        # Checked before the read because mutagen wraps a missing file in its
        # own MutagenError, which is indistinguishable from a real parse failure.
        entry.update(status="gone", **{k: None for k in ("artist", "title", "album", "year", "genre")})
        gone_count += 1
        results.append(entry)
        if i % 5000 == 0:
            print(f"  ... {i}/{len(catalog)}", flush=True)
        continue
    try:
        tags = read_tags(host)
        if tags is None:
            entry.update(status="unreadable", **{k: None for k in ("artist", "title", "album", "year", "genre")})
        else:
            missing = [k for k, v in tags.items() if not v]
            entry.update(status="ok" if not missing else "incomplete", **tags, missing=missing)
    except Exception as e:
        entry.update(status="error", error=str(e)[:120], **{k: None for k in ("artist", "title", "album", "year", "genre")})
        err_count += 1
    results.append(entry)
    if i % 5000 == 0:
        print(f"  ... {i}/{len(catalog)}", flush=True)

# aggregate
by_status = Counter(r["status"] for r in results)
missing_field = Counter()
for r in results:
    for m in r.get("missing", []):
        missing_field[m] += 1

# per artist-folder aggregation (2nd path segment under a 'Music' dir)
per_folder = defaultdict(lambda: {"files": 0, "no_tags": 0, "bytes": 0})
for r in results:
    parts = r["path"].replace("\\", "/").split("/")
    try:
        i = next(i for i, p in enumerate(parts) if p.lower() == "music")
        folder = parts[i + 1] if i + 1 < len(parts) else "(root)"
    except StopIteration:
        folder = "(outside Music)"
    per_folder[folder]["files"] += 1
    per_folder[folder]["bytes"] += r["size_bytes"]
    if r["status"] in ("incomplete", "error", "unreadable"):
        per_folder[folder]["no_tags"] += 1

# `gone` is catalog drift: a re-scan of scanner.py is what clears it, not a tag edit.
NEEDS_FIX = ("incomplete", "error", "unreadable")
out = {
    "date": today,
    "total_files": len(results),
    "status_counts": dict(by_status),
    "missing_field_counts": dict(missing_field),
    "files_needing_fix": sum(1 for r in results if r["status"] in NEEDS_FIX),
    "files_gone": gone_count,
    "per_folder": {k: v for k, v in sorted(per_folder.items(), key=lambda kv: -kv[1]["files"])},
    "problems": [r for r in results if r["status"] in NEEDS_FIX],
    "gone": [r["path"] for r in results if r["status"] == "gone"],
}
AUDIT_FILE = os.path.join(DATA, "music_tag_audit.json")
if os.path.exists(AUDIT_FILE):
    # data/ is gitignored and this file is derived, so a bad run is unrecoverable
    # without a full re-scan. Cheap insurance; the copy is overwritten each run.
    shutil.copy2(AUDIT_FILE, AUDIT_FILE + ".bak")
with open(AUDIT_FILE, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)

print(json.dumps({k: out[k] for k in ("total_files", "status_counts", "missing_field_counts", "files_needing_fix")}, indent=2))
