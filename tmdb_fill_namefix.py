#!/usr/bin/env python3
"""Fill all pending name-fix proposals with TMDB data (movies + serials), rate-limited.
Idempotent: skips proposals already carrying a tmdb_id."""
import json
import os
import re
import sys
import time
from datetime import date

import tmdb_client
from video_catalog import parse_movie, parse_serial

BASE = os.path.dirname(os.path.abspath(__file__))
PROP = os.path.join(BASE, "data", "name_fix_proposals.json")


def norm(proposed):
    m = re.sub(r" (\d\.\d{2,}) ", lambda x: f" {round(float(x.group(1)), 1)} ", " " + proposed + " ")
    return m.strip()


def main(only_pending=True, cap=None):
    with open(PROP, encoding="utf-8") as f:
        nf = json.load(f)
    env = tmdb_client.load_env()
    op = tmdb_client.opener()
    key = env["TMDB_API_KEY"]

    done = filled = missed = 0
    with open(os.path.join(BASE, "data", "video_library.json"), encoding="utf-8") as f:
        lib = json.load(f)
    by_id = {c["id"]: c for c in lib["cards"]}

    for p in nf["proposals"]:
        if cap is not None and filled >= cap:
            break
        if only_pending and p.get("status") == "accepted":
            continue
        if p.get("tmdb_id"):
            done += 1
            continue
        card = by_id.get(p["id"])
        if not card:
            continue
        try:
            if card["kind"] == "movie" or p["kind"] == "movie-folder":
                res = tmdb_client.tmdb_search(op, key, card["title"], card.get("year"))
                if not res:
                    p["tmdb_miss"] = True
                    missed += 1
                    time.sleep(0.3)
                    continue
                det = json.load(op.open(
                    f"https://api.themoviedb.org/3/movie/{res['id']}?api_key={key}", timeout=15))
                kind = "movie-folder"
            else:
                res = tmdb_client.tmdb_search_tv(op, key, card["title"])
                if not res:
                    p["tmdb_miss"] = True
                    missed += 1
                    time.sleep(0.3)
                    continue
                det = json.load(op.open(
                    f"https://api.themoviedb.org/3/tv/{res['id']}?api_key={key}", timeout=15))
                kind = "serial-folder"
            genres = ", ".join(g["name"] for g in det.get("genres", []))
            rating = round(det.get("vote_average") or 0, 1)
            pop = min(100, round(det.get("popularity") or 0))
            first_air = (det.get("release_date") or det.get("first_air_date") or "")[:4]
            year = first_air or card.get("year") or ""
            title = card["title"]
            if kind == "serial-folder":
                seasons = det.get("number_of_seasons")
                new = f"{title} S01 of {seasons:02d}+ - {genres} {rating} {pop}" if seasons else f"{title} - {genres} {rating} {pop}"
            else:
                new = f"{year} - {title} - {genres} {rating} {pop}"
            p["proposed"] = norm(new)
            p["tmdb_id"] = res["id"]
            p["kind"] = kind
            filled += 1
            if filled % 10 == 0:
                print(f"  ... {filled} filled", flush=True)
                json.dump(nf, open(PROP, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            time.sleep(0.3)
        except Exception as e:
            p.setdefault("errors", []).append(str(e)[:80])
            time.sleep(1)

    nf["date"] = date.today().isoformat()
    with open(PROP, "w", encoding="utf-8") as f:
        json.dump(nf, f, ensure_ascii=False, indent=1)
    print(f"already-done={done} newly-filled={filled} tmdb-miss={missed}")


if __name__ == "__main__":
    cap = int(sys.argv[sys.argv.index("--cap") + 1]) if "--cap" in sys.argv else None
    main(cap=cap)
