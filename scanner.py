#!/usr/bin/env python3
"""Full system scan: catalog, stats, duplicate detection. Outputs data/ JSON + Markdown."""
import hashlib
import json
import os
import re
import sys
from collections import defaultdict
from datetime import date

BASE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(BASE, "config.json"), encoding="utf-8") as f:
    CFG = json.load(f)

MUSIC_EXT = {".mp3", ".flac", ".m4a", ".ogg", ".wav", ".wma", ".aac", ".opus", ".ape", ".m4b"}
VIDEO_EXT = {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v", ".mpg", ".mpeg", ".ts", ".m2ts"}
DOC_EXT = {".pdf", ".doc", ".docx", ".odt", ".rtf", ".txt", ".md", ".epub", ".mobi", ".xlsx", ".xls", ".pptx", ".ppt", ".csv"}
PROG_EXT = {".exe", ".msi", ".apk", ".deb", ".rpm", ".appimage", ".bat", ".ps1", ".sh"}
ARCHIVE_EXT = {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".iso_disc"}
DISK_EXT = {".iso", ".img", ".vmdk", ".vdi", ".qcow2", ".ova", ".ovf", ".raw", ".vhd", ".vhdx"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tiff", ".raw", ".heic"}

IS_WINDOWS = sys.platform == "win32"
ROOTS = CFG["roots_windows"] if IS_WINDOWS else CFG["roots_linux"]
EXCL_NAMES = set(CFG["exclude_dir_names"])
EXCL_PREFIXES = tuple(p.replace("/", os.sep) + os.sep for p in CFG["exclude_path_prefixes"])
ISO_MARKERS = set(CFG["iso_folder_markers"])


def category_of(ext, path):
    e = ext.lower()
    parts = path.lower().split(os.sep)
    if e == ".iso" or any(m in parts for m in ISO_MARKERS) and e in DISK_EXT | ARCHIVE_EXT:
        return "disk_image"
    if e in MUSIC_EXT:
        return "music"
    if e in VIDEO_EXT:
        return "video"
    if e in PROG_EXT:
        return "program"
    if e in DISK_EXT:
        return "disk_image"
    if e in ARCHIVE_EXT:
        return "archive"
    if e in DOC_EXT:
        return "document"
    if e in IMAGE_EXT:
        return "image"
    return "other"


def iter_files(root):
    root = os.path.abspath(root)
    if not os.path.isdir(root):
        yield None
        return
    for dirpath, dirnames, filenames in os.walk(root, topdown=True, onerror=lambda e: None):
        dirnames[:] = [d for d in dirnames if d not in EXCL_NAMES]
        if dirpath.startswith(EXCL_PREFIXES):
            continue
        for name in filenames:
            yield os.path.join(dirpath, name)


def hash_file(path, chunk):
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fp:
            while True:
                b = fp.read(chunk)
                if not b:
                    break
                h.update(b)
    except OSError:
        return None
    return h.hexdigest()


def main():
    today = date.today().isoformat()
    catalog = []
    errors = []
    per_drive = defaultdict(lambda: {"files": 0, "bytes": 0})
    per_cat = defaultdict(lambda: {"files": 0, "bytes": 0})
    drive_folders = defaultdict(set)

    for root in ROOTS:
        if not os.path.isdir(root):
            continue
        drive = root.split(":")[0] if IS_WINDOWS and ":" in root else root.split(os.sep)[1]
        for i, path in enumerate(iter_files(root)):
            if path is None:
                continue
            try:
                st = os.stat(path)
            except OSError as e:
                errors.append({"path": path, "error": str(e)})
                continue
            if not os.path.isfile(path):
                continue
            ext = os.path.splitext(path)[1]
            cat = category_of(ext, path)
            size = st.st_size
            rel_parent = os.path.dirname(path)
            drive_folders[drive].add(rel_parent)
            catalog.append({
                "path": path,
                "drive": drive,
                "category": cat,
                "ext": ext.lower(),
                "size_bytes": size,
                "mtime": int(st.st_mtime),
            })
            per_drive[drive]["files"] += 1
            per_drive[drive]["bytes"] += size
            per_cat[cat]["files"] += 1
            per_cat[cat]["bytes"] += size
            if i % 20000 == 0:
                print(f"  ... {i} files scanned in {root}", flush=True)

    # ---- duplicate detection: group by (size, category), hash in groups > 1 ----
    min_size = CFG["duplicate_min_size_bytes"]
    size_groups = defaultdict(list)
    for f in catalog:
        if f["size_bytes"] >= min_size and f["category"] in CFG["duplicate_categories"]:
            size_groups[(f["size_bytes"], f["category"])].append(f)

    dup_groups = []
    dup_bytes_wasted = 0
    to_hash = [f for g in size_groups.values() if len(g) > 1 for f in g]
    print(f"Hashing {len(to_hash)} candidate files for duplicate detection...", flush=True)
    hash_map = {}
    for i, f in enumerate(to_hash):
        digest = hash_file(f["path"], CFG["hash_chunk_bytes"])
        if digest:
            hash_map[f["path"]] = digest
        if i % 200 == 0:
            print(f"  ... hashed {i}/{len(to_hash)}", flush=True)

    content_groups = defaultdict(list)
    for f in to_hash:
        d = hash_map.get(f["path"])
        if d:
            content_groups[(d, f["category"])].append(f)

    for (digest, cat), members in content_groups.items():
        if len(members) > 1:
            members_sorted = sorted(members, key=lambda x: (x["mtime"], x["path"]))
            wasted = sum(m["size_bytes"] for m in members_sorted[1:])
            dup_bytes_wasted += wasted
            dup_groups.append({
                "category": cat,
                "sha256": digest[:16],
                "size_bytes": members_sorted[0]["size_bytes"],
                "copies": [m["path"] for m in members_sorted],
                "keep_suggestion": members_sorted[0]["path"],
                "redundant_bytes": wasted,
            })
    dup_groups.sort(key=lambda g: -g["redundant_bytes"])

    # ---- videos / music / programs sub-catalogs ----
    video_catalog = [f for f in catalog if f["category"] == "video"]
    music_catalog = [f for f in catalog if f["category"] == "music"]
    program_catalog = [f for f in catalog if f["category"] == "program"]
    disk_catalog = [f for f in catalog if f["category"] == "disk_image"]

    os.makedirs(os.path.join(BASE, CFG["data_dir"]), exist_ok=True)
    os.makedirs(os.path.join(BASE, CFG["logs_dir"]), exist_ok=True)

    def dump(name, obj):
        with open(os.path.join(BASE, CFG["data_dir"], name), "w", encoding="utf-8") as fp:
            json.dump(obj, fp, ensure_ascii=False, indent=1)

    dump("catalog_full.json", catalog)
    dump("video_catalog.json", video_catalog)
    dump("music_catalog.json", music_catalog)
    dump("programs_catalog.json", program_catalog)
    dump("disk_images_catalog.json", disk_catalog)
    dump("duplicates.json", {
        "scan_date": today,
        "total_groups": len(dup_groups),
        "total_redundant_bytes": dup_bytes_wasted,
        "groups": dup_groups,
    })
    summary = {
        "scan_date": today,
        "platform": sys.platform,
        "roots_scanned": ROOTS,
        "totals": {
            "files": len(catalog),
            "bytes": sum(f["size_bytes"] for f in catalog),
        },
        "per_drive": {k: dict(v) for k, v in per_drive.items()},
        "per_category": {k: dict(v) for k, v in per_cat.items()},
        "duplicate_summary": {
            "groups": len(dup_groups),
            "redundant_bytes": dup_bytes_wasted,
        },
        "folder_counts": {d: len(v) for d, v in drive_folders.items()},
        "errors": len(errors),
    }
    dump("scan_summary.json", summary)
    with open(os.path.join(BASE, CFG["logs_dir"], f"scan_errors_{today}.log"), "w", encoding="utf-8") as fp:
        fp.write("\n".join(e["path"] + " :: " + e["error"] for e in errors))

    # markdown report
    def gb(b):
        return f"{b / 1024**3:.1f} GB"

    lines = [
        f"# Full System Scan — {today}", "",
        f"- Platform: {'Windows 11' if IS_WINDOWS else 'Linux'} ({sys.platform})",
        f"- Files cataloged: {len(catalog):,} ({gb(summary['totals']['bytes'])})",
        f"- Duplicate groups: {len(dup_groups)} (redundant: {gb(dup_bytes_wasted)})",
        f"- Stat/read errors: {len(errors)} (see logs/)", "",
        "## Per drive", "",
        "| Drive | Files | Size |", "|---|---|---|",
    ]
    for d, v in sorted(per_drive.items()):
        lines.append(f"| {d}: | {v['files']:,} | {gb(v['bytes'])} |")
    lines += ["", "## Per category", "", "| Category | Files | Size |", "|---|---|---|"]
    for c, v in sorted(per_cat.items(), key=lambda kv: -kv[1]["bytes"]):
        lines.append(f"| {c} | {v['files']:,} | {gb(v['bytes'])} |")
    lines += ["", "## Top 20 duplicate groups (by wasted space)", ""]
    for g in dup_groups[:20]:
        lines.append(f"- **{gb(g['redundant_bytes'])}** wasted — {g['category']} — {g['copies'][0]}")
        for p in g["copies"][1:]:
            lines.append(f"    - dup: {p}")
    with open(os.path.join(BASE, CFG["data_dir"], f"scan_{today}.md"), "w", encoding="utf-8") as fp:
        fp.write("\n".join(lines))

    print(json.dumps(summary["totals"], indent=2))
    print(f"dup groups: {len(dup_groups)}, redundant: {gb(dup_bytes_wasted)}")


if __name__ == "__main__":
    main()
