#!/usr/bin/env python3
"""Universal folder-name analysis + rename proposals for ALL areas (music, videos, learning,
programs, pictures, documents). Pure string ops — safe in container and as host CLI.

Replaces music_folder_fix.py and name_fix.py. Proposals land in one file:
data/folder_fix_proposals.json — {date, count, proposals: [{id, area, schema, current,
proposed, problems, files, dir, status, ...extra}]}.

CLI:
  python folder_fix.py                    # all areas
  python folder_fix.py --area music
  python folder_fix.py --area learning
"""
import json
import os
import re
import sys
from datetime import date

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
BS = "\\"
PROP_FILE = os.path.join(DATA, "folder_fix_proposals.json")

with open(os.path.join(BASE, "naming_schemas.json"), encoding="utf-8") as _f:
    SCHEMAS = json.load(_f)

# ---------- universal cleaning primitives (battle-tested in music_folder_fix.py) ----------

SITE_TAG = re.compile(
    r"(_p30download\.com|_?yasdl\.com|_?softgozar|_zarfilm|_?downloadly|[_-]iranfilm"
    r"|[_-]digit98|[_-]bingmag|[_-]nex1music|[_-]radiojavan|[_-]minitoons"
    r"|[_-]freecoursesite\.com|[\[({]\s*www\.[^\s)\]}]*[\])}]|@\w+)", re.I)
BRACKET_TAG = re.compile(r"\s*[\[({]((?:4K|8K|2160p|1080p|720p|480p|HD|BluRay|BR-Rip|WEBRip|"
                         r"x265|x264|HEVC|AAC|5\.1|NF|10bit|EAC3|Atmos|Farsi(?:-\w+)?|FA\+FR|FA\+EN"
                         r"|FR|EN|Ita Eng|Sub Ita Eng)[^\]){}]*)[\])}]\s*", re.I)
DASH_CHARS = re.compile(r"[–—]")  # en/em dash
YEAR_RE = re.compile(r"\b((19|20)\d{2})\b")
PYEAR_RE = re.compile(r"^(1[3-4]\d{2})\s*-\s*(.+)$")  # Persian year 13xx/14xx
TRAILING_STOPWORD = re.compile(r"\b(of|and|the|by|feat|ft|with|in|a|an|to|for|vol|cd)$", re.I)
DOT_STYLE = re.compile(r"^\d{4}\.[A-Z]")


def strip_site_tags(name):
    prev = None
    while prev != name:
        prev = name
        name = SITE_TAG.sub("", name)
    return name.strip(" .-_ ")


def dots_to_spaces(name):
    name = re.sub(r"\.(?=[^\d])", " ", name)
    name = re.sub(r"(?<=[^\d])\.", " ", name)
    return re.sub(r"\s{2,}", " ", name).strip()


def strip_bracket_tags(name):
    return BRACKET_TAG.sub("", name)


def common_clean(name, rules):
    problems = []
    if "site_tag" in rules and SITE_TAG.search(name):
        problems.append("site tag")
        name = strip_site_tags(name)
    if "dots" in rules and (DOT_STYLE.match(name) or (re.match(r"^(19|20)\d{2}\.", name) and "." in name[5:])):
        problems.append("dots for spaces")
        name = dots_to_spaces(name)
    if "en_dash" in rules and DASH_CHARS.search(name):
        problems.append("unicode dash")
        name = DASH_CHARS.sub("-", name)
    if "bracket_tags" in rules and BRACKET_TAG.search(name):
        problems.append("bracket tags")
        name = strip_bracket_tags(name)
    if "underscore" in rules and re.search(r"_+", name):
        problems.append("underscores")
        name = re.sub(r"_+", " ", name)
    if "amp_spacing" in rules and re.search(r"\S&\S", name):
        problems.append("unspaced &")
        name = re.sub(r"\s*&\s*", " & ", name)
    if "double_space" in rules and re.search(r"\s{2,}", name):
        problems.append("double spaces")
    name = re.sub(r"\s{2,}", " ", name).strip(" .-_")
    return name, problems


def trailing_dates_fix(name):
    """'Udemy - X - 2024 2024-1' -> 'X (2024)'; collapse duplicated trailing years."""
    m = re.match(r"^(?P<base>.+?)\s*[-–]\s*(?P<y>(19|20)\d{2})\s*(?:[-–]\s*\d{1,2})?$", name)
    if m and (str(m.group("y")) in m.group("base")):
        base = re.sub(r"\s*[-–]\s*$", "", m.group("base"))
        base = re.sub(rf"\b{m.group('y')}\b\s*$", "", base).strip(" -")
        if base:
            return f"{base} ({m.group('y')})"
    return name


# ---------- per-area folder collectors ----------

def collect_music(cat):
    """Music/<Artist>/<album...> — reuse proven music logic."""
    out = {}
    for c in cat:
        parts = c["path"].split(BS)
        try:
            i = next(i for i, s in enumerate(parts) if s.lower() == "music")
        except StopIteration:
            continue
        if len(parts) < i + 3:
            continue
        folder_parts = parts[:-1]
        if len(folder_parts) - i < 2:
            continue  # the artist folder itself
        artist = parts[i + 1]
        key = BS.join(folder_parts)
        d = out.setdefault(key, {"artist": artist, "name": folder_parts[-1], "files": 0})
        d["files"] += 1
    return out


def collect_learning(cat):
    out = {}
    for c in cat:
        parts = c["path"].split(BS)
        try:
            i = next(i for i, s in enumerate(parts) if s.lower() == "learning")
        except StopIteration:
            continue
        folder_parts = parts[:-1]
        if len(folder_parts) - i < 2:
            continue  # topic-level folder (Android, DevOps...) — these are clean
        key = BS.join(folder_parts)
        d = out.setdefault(key, {"topic": parts[i + 1], "name": folder_parts[-1], "files": 0})
        d["files"] += 1
    return out


def collect_programs(cat):
    out = {}
    for c in cat:
        parts = c["path"].split(BS)
        try:
            i = next(i for i, s in enumerate(parts) if s.lower() == "programs")
        except StopIteration:
            continue
        folder_parts = parts[:-1]
        if len(folder_parts) - i < 2:
            continue  # category folders (Communication, ...) are clean
        key = BS.join(folder_parts)
        d = out.setdefault(key, {"category": parts[i + 1], "name": folder_parts[-1], "files": 0})
        d["files"] += 1
    return out


def collect_pictures(cat):
    out = {}
    for c in cat:
        parts = c["path"].split(BS)
        try:
            i = next(i for i, s in enumerate(parts) if s.lower() == "pictures")
        except StopIteration:
            continue
        folder_parts = parts[:-1]
        if len(folder_parts) - i < 1:
            continue
        key = BS.join(folder_parts)
        d = out.setdefault(key, {"group": parts[i + 1] if len(parts) > i + 1 else "",
                                 "name": folder_parts[-1], "files": 0})
        d["files"] += 1
    return out


def collect_documents(cat):
    """Personal areas: light cleanup, but EXCLUDE code/sketch folders where underscores are
    functionally required (Arduino sketches, software projects)."""
    out = {}
    markers = ("documents", "desktop", "downloads", "workplace")
    CODE_HINTS = ("sketch", ".ino", "src", "venv", "node_modules", "__pycache__")
    for c in cat:
        parts = c["path"].split(BS)
        idx = next((i for i, s in enumerate(parts) if s.lower() in markers), None)
        if idx is None:
            continue
        folder_parts = parts[:-1]
        if len(folder_parts) <= idx + 1:
            continue
        # skip dirs whose contents signal a code project (any .ino/.py/.h sibling)
        if any(part.lower().endswith((".ino", ".h", ".cpp", ".py")) for part in parts[idx:]):
            continue
        # skip folders under any Arduino/bootcamp-style code project path
        lowered = [s.lower() for s in parts[idx:]]
        if any("arduino" in s or s in ("sketch", "sketches") or "bootcamp" in s for s in lowered):
            continue
        key = BS.join(folder_parts)
        d = out.setdefault(key, {"name": folder_parts[-1], "files": 0})
        d["files"] += 1
    return out


def collect_videos(cat):
    """Only the direct child folder of Movies/Serials — not Featurettes/SUB/season subdirs."""
    out = {}
    for c in cat:
        parts = c["path"].split(BS)
        marker = next((i for i, s in enumerate(parts) if s.lower() in ("movies", "serials")), None)
        if marker is None or len(parts) < marker + 3:
            continue
        root = parts[marker].lower()
        title_dir = BS.join(parts[:marker + 2])
        d = out.setdefault(title_dir, {"root": root, "name": parts[marker + 1], "files": 0})
        d["files"] += 1
    return out


# ---------- validators per area ----------

def clean_music_folder(d):
    """Inlined from the retired music_folder_fix.clean_album — proven rules."""
    name = d["name"]
    artist = d.get("artist", "")
    problems = []
    cleaned, probs = common_clean(name, ["site_tag", "dots", "underscore", "double_space"])
    problems.extend(probs)
    # embedded artist name — only when what remains still reads like a title
    if artist and len(artist) >= 3:
        pat = re.compile(r"[\s.\-_]+" + re.escape(artist) + r"[\s.\-_]*$", re.I)
        if pat.search(cleaned):
            candidate = pat.sub("", cleaned).strip(" .-_")
            words = candidate.split()
            keeps_meaning = (len(candidate) >= 4 and words
                             and not TRAILING_STOPWORD.match(words[-1])
                             and not re.fullmatch(r"[(\[]?\d{4}[)\]]?", candidate))
            if keeps_meaning:
                problems.append("embedded artist name")
                cleaned = candidate
    # normalize an existing leading year into "YYYY - Title" (never invent a year)
    m = re.match(r"^[(\[]?((19|20)\d{2})[)\]]?[\s._-]+(.+)$", cleaned)
    if m and m.group(3).strip():
        normalized = f"{m.group(1)} - {m.group(3).strip()}"
        if normalized != cleaned:
            problems.append("year separator")
            cleaned = normalized
    if not cleaned or cleaned == name:
        return name, []
    return cleaned, problems


def clean_videos_folder(d):
    from video_catalog import parse_movie, parse_serial
    name = d["name"]
    problems = []
    pre, probs = common_clean(name, ["site_tag", "bracket_tags", "en_dash", "underscore", "double_space"])
    if probs:
        problems.extend(probs)
    p = parse_serial(pre) if d["root"] == "serials" else parse_movie(pre)
    if p.get("kind") == "movie":
        if not p["genres"]:
            problems.append("missing Genres segment")
            if p["year"]:
                pre = f"{p['year']} - {p['title']} - ?"
            else:
                pre = f"{p['title']} - ?"
        elif not p["rating"] or not p["pop"]:
            problems.append("missing rating/pop")
    else:
        if not p["seasons"] and not p["limited"]:
            problems.append("missing 'Sxx of yy' or 'Limited'")
        if not p["genres"] or not p["rating"]:
            problems.append("missing genres/rating")
    cleaned = re.sub(r"\s{2,}", " ", pre).strip(" .-_")
    return cleaned, problems


def clean_learning_folder(d):
    name = d["name"]
    # numbered lesson folders (01_Retrofit, 02_Retrofit) are ordinal structure, not mess — skip
    if re.match(r"^\d{1,3}[\s._-]*\D", name) and "_" in name and len(name) < 40:
        # still clean double spaces but don't strip the ordinal underscore pattern
        cleaned = re.sub(r"\s{2,}", " ", name)
        return cleaned, (["double spaces"] if cleaned != name else [])
    cleaned, problems = common_clean(name, ["site_tag", "bracket_tags", "en_dash", "double_space"])
    fixed = trailing_dates_fix(cleaned)
    if fixed != cleaned:
        problems.append("trailing date fragments")
        cleaned = fixed
    return cleaned, problems


def clean_programs_folder(d):
    cleaned, problems = common_clean(d["name"], ["site_tag", "underscore", "double_space", "en_dash"])
    # dot-style program names: only flag, don't auto-convert (version dots are meaningful)
    if re.match(r"^[A-Z][A-Za-z0-9.]+v?\d", d["name"]) and "." in d["name"] and " " not in d["name"]:
        problems.append("dot-style name (review)")
    return cleaned, problems


def clean_pictures_folder(d):
    name = d["name"]
    cleaned, problems = common_clean(name, ["underscore", "double_space", "amp_spacing"])
    m = PYEAR_RE.match(cleaned)
    if m:
        normalized = f"{m.group(1)} - {m.group(2).strip()}"
        if normalized != cleaned:
            problems.append("year separator")
            cleaned = normalized
    return cleaned, problems


def clean_documents_folder(d):
    cleaned, problems = common_clean(d["name"], ["site_tag", "underscore", "double_space"])
    return cleaned, problems


CLEANERS = {
    "music": clean_music_folder,
    "videos": clean_videos_folder,
    "learning": clean_learning_folder,
    "programs": clean_programs_folder,
    "pictures": clean_pictures_folder,
    "documents": clean_documents_folder,
}

COLLECTORS = {
    "music": collect_music,
    "videos": collect_videos,
    "learning": collect_learning,
    "programs": collect_programs,
    "pictures": collect_pictures,
    "documents": collect_documents,
}


# ---------- migration from the two legacy proposal files ----------

def migrate_old_statuses(entries):
    """Carry accepted/pending statuses from legacy files into the unified entries dict."""
    carried = 0
    for legacy in ("music_folder_proposals.json", "name_fix_proposals.json"):
        path = os.path.join(DATA, legacy)
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            old = json.load(f)
        for p in old.get("proposals", []):
            if p.get("status") == "accepted" and p["id"] in entries:
                entries[p["id"]]["status"] = "accepted"
                carried += 1
    return carried


# ---------- main ----------

def propose(areas=None):
    if not areas:
        areas = list(COLLECTORS)
    with open(os.path.join(DATA, "catalog_full.json"), encoding="utf-8") as f:
        cat = json.load(f)

    existing = {}
    if os.path.exists(PROP_FILE):
        with open(PROP_FILE, encoding="utf-8") as f:
            existing = {p["id"]: p for p in json.load(f).get("proposals", [])}

    proposals = []
    for area in areas:
        dirs = COLLECTORS[area](cat)
        clean_fn = CLEANERS[area]
        rules = SCHEMAS.get(area, {}).get("clean", [])
        for dir_path, d in sorted(dirs.items()):
            cleaned, problems = clean_fn(d)
            if not problems or cleaned == d["name"]:
                continue
            old = existing.get(dir_path, {})
            proposals.append({
                "id": dir_path,
                "area": area,
                "current": d["name"],
                "proposed": cleaned,
                "problems": problems,
                "files": d["files"],
                "dir": dir_path,
                "status": old.get("status", "pending"),
                **({"artist": d["artist"]} if "artist" in d else {}),
                **({"topic": d["topic"]} if "topic" in d else {}),
                **({"root": d["root"]} if "root" in d else {}),
            })

    out = {"date": date.today().isoformat(), "count": len(proposals), "proposals": proposals}
    tmp = PROP_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    os.replace(tmp, PROP_FILE)
    return out


def main():
    areas = None
    if "--area" in sys.argv:
        areas = sys.argv[sys.argv.index("--area") + 1].split(",")
    res = propose(areas)
    from collections import Counter
    by_area = Counter(p["area"] for p in res["proposals"])
    print(f"folder_fix_proposals.json: {res['count']} proposals")
    for a, n in by_area.most_common():
        print(f"  {a}: {n}")


if __name__ == "__main__":
    main()
