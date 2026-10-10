#!/usr/bin/env python3
"""Build data/video_library.json: parsed movie/serial cards from folder names + subtitle presence.
Pure string ops — runs in container and host CLI alike."""
import json
import os
import re
from datetime import date
from pathlib import PurePosixPath, PureWindowsPath

from media_path import to_host_path
from season_scan import season_state, seasons_in_dir

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
BS = "\\"

MOVIE_PAT = re.compile(r"^(?P<year>(19|20)\d{2})\s*-\s*(?P<rest>.+)$")
YEAR_TOKEN = re.compile(r"\b((19|20)\d{2})\b")
SERIAL_PAT = re.compile(r"^(?P<title>.+?)(?:\s+S(?P<season>\d{2})\s+of\s+S?(?P<seasons>\d+)\+?)?(?:\s*-\s*(?P<kind>Limited))?(?:\s*-\s*(?P<genres_tail>[^0-9]+?))?(?:\s+(?P<rating>\d\.\d))?(?:\s+(?P<pop>\d{1,3}))?$")

SUB_EXTS = (".srt", ".sub", ".ass", ".ssa", ".vtt", ".idx")
FA_HINTS = ("persian", "farsi", "fa.", ".fa", "parsi", "فارسی")
EN_HINTS = ("english", ".en.", "eng.", "en.srt", ".en")

STOP_FOLDERS = {"s01", "s02", "s03", "s04", "s05", "s06", "s07", "s08", "s09", "s10",
                "season 1", "extras", "specials"}


def _strip_trailing_nums(rest):
    """Strip up to 3 trailing numeric tokens; classify as rating (d.d) or popularity (int<=100)."""
    rating = pop = None
    for _ in range(3):
        m = re.search(r"\s+(\d+(?:\.\d+)?)$", rest)
        if not m:
            break
        tok = m.group(1)
        if "." in tok and rating is None:
            rating = tok
        elif "." not in tok and pop is None and 0 < int(tok) <= 100:
            pop = tok
        elif "." not in tok and 0 < int(tok) <= 100:
            pass  # extra popularity-like token: drop from title, ignore
        else:
            break
        rest = rest[:m.start()].strip()
    return rest, rating, pop


def parse_movie(name):
    m = MOVIE_PAT.match(name)
    if not m:
        y = YEAR_TOKEN.search(name)
        return {"kind": "movie", "title": YEAR_TOKEN.sub("", name).strip(" -"),
                "year": y.group(1) if y else None, "genres": None, "rating": None, "pop": None}
    year = m["year"]
    rest = m["rest"].strip()
    year2 = None
    m2 = re.search(r"\s+((19|20)\d{2})$", rest)
    if m2:
        year2 = m2.group(1)
        rest = rest[:m2.start()].strip()
    rest, rating, pop = _strip_trailing_nums(rest)
    genres = None
    title = rest
    parts = rest.split(" - ")
    if len(parts) >= 2 and re.fullmatch(r"[A-Za-z, \-½]+", parts[-1]):
        genres = parts[-1].strip()
        title = " - ".join(parts[:-1]).strip()
    return {"kind": "movie", "title": title, "year": year2 or year,
            "genres": genres, "rating": rating, "pop": pop}


def parse_serial(name):
    m = SERIAL_PAT.match(name)
    if m:
        d = m.groupdict()
        return {"kind": "serial", "title": (d["title"] or "").strip(), "season": d["season"],
                "seasons": d["seasons"], "limited": d["kind"] == "Limited",
                "genres": d["genres_tail"], "rating": d["rating"], "pop": d["pop"]}
    y = YEAR_TOKEN.search(name)
    return {"kind": "serial", "title": YEAR_TOKEN.sub("", name).strip(" -"), "season": None,
            "seasons": None, "limited": None, "genres": None, "rating": None, "pop": None}


def classify_subs(subnames):
    has_fa = any(any(h in n for h in FA_HINTS) for n in subnames)
    has_en = any(any(h in n for h in EN_HINTS) for n in subnames)
    if subnames and not has_fa and not has_en:
        has_en = True  # unmarked subs — assume EN (dominant convention in library)
    return has_fa, has_en


def main_item(path):
    path = PureWindowsPath(path) if BS in path or re.match(r"^[A-Za-z]:", path) else PurePosixPath(path)
    for i, part in enumerate(path.parts[:-2]):
        if part.lower() in ("movies", "serials", "videos"):
            return part.lower(), path.parts[i + 1], str(type(path)(*path.parts[:i + 2]))
    return None


def build():
    with open(os.path.join(DATA, "video_catalog.json"), encoding="utf-8") as f:
        vc = json.load(f)
    with open(os.path.join(DATA, "catalog_full.json"), encoding="utf-8") as f:
        full = json.load(f)

    sub_by_dir = {}
    for c in full:
        if c["ext"] in SUB_EXTS:
            item = main_item(c["path"])
            if item:
                sub_by_dir.setdefault(item[2], []).append(c["path"].replace("/", BS).split(BS)[-1].lower())

    library_path = os.path.join(DATA, "video_library.json")
    old_posters = {}
    if os.path.exists(library_path):
        with open(library_path, encoding="utf-8") as f:
            for card in json.load(f)["cards"]:
                if not card.get("poster"):
                    continue
                item = main_item(card["sample_video"])
                if item and item not in old_posters:
                    old_posters[item] = card

    groups = {}
    for v in vc:
        p = v["path"]
        item = main_item(p)
        if not item:
            continue
        root, folder, main_dir = item
        key = f"{root}|{folder}|{main_dir}"
        g = groups.setdefault(key, {"root": root, "folder": folder, "dir": main_dir,
                                    "videos": [], "total_bytes": 0})
        g["videos"].append({"path": p, "size_bytes": v["size_bytes"], "ext": v["ext"]})
        g["total_bytes"] += v["size_bytes"]

    cards = []
    for key, g in groups.items():
        dirn = g["dir"]
        subnames = sub_by_dir.get(dirn, [])
        has_fa, has_en = classify_subs(subnames)
        info = parse_serial(g["folder"]) if g["root"] == "serials" else parse_movie(g["folder"])
        poster = old_posters.get((g["root"], g["folder"], dirn), {})
        card = {
            "id": key,
            "root": g["root"], "folder": g["folder"], "dir": dirn,
            "video_count": len(g["videos"]), "total_bytes": g["total_bytes"],
            # Every episode, not just the sample. Without this the subtitle
            # fetcher falls back to sample_video and a 24-episode serial gets
            # one subtitle for one episode.
            "videos": g["videos"],
            "sample_video": g["videos"][0]["path"],
            "exts": sorted({v["ext"] for v in g["videos"]}),
            "has_fa_sub": has_fa, "has_en_sub": has_en,
            "sub_count": len(subnames),
            **info,
            "poster": poster.get("poster"),
            "poster_id": poster.get("poster_id", poster.get("id", key)),
        }
        if g["root"] == "serials":
            # Read the seasons off disk rather than trusting the folder name.
            # Unreadable here (no media mount) leaves the card without the keys,
            # which the template treats the same as "state unknown".
            present = seasons_in_dir(to_host_path(dirn))
            if present is not None:
                card["season_state"] = season_state(present, info.get("seasons"))
        cards.append(card)
    cards.sort(key=lambda c: (c["root"], c["folder"]))
    out = {"date": date.today().isoformat(), "count": len(cards), "cards": cards}
    with open(os.path.join(DATA, "video_library.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    return out


if __name__ == "__main__":
    res = build()
    print(f"built video_library.json: {res['count']} cards")
