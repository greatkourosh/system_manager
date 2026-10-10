#!/usr/bin/env python3
"""True total season counts from TMDB, keyed by card id.

A serial card says how many seasons it *has* (read off the disk by
season_scan.py) and how many its folder *name* claims ("S01 of 03+"). Neither
is the number of seasons the show ever had, which is what says whether the
library is actually complete. That is one field on the TV detail endpoint.

Reuses the tmdb_id already resolved by tmdb_ratings.py instead of re-searching,
so a card that already matched once is not matched again -- and a card that
matched to the wrong show stays wrong in the same way, rather than picking a
different wrong answer here.

Sidecar, not a field in video_library.json, for the reason in tmdb_ratings.py:
video_catalog.py build() regenerates that file from scratch. data/ is not in the
container and the page must not reach TMDB on render, so this runs on the host:

    python3 tmdb_seasons.py                # every serial with a tmdb_id
    python3 tmdb_seasons.py --limit 20
    python3 tmdb_seasons.py --refresh
"""
import argparse
import json
import os
import time
from datetime import date

import tmdb_client

BASE = os.path.dirname(os.path.abspath(__file__))
LIBRARY = os.path.join(BASE, "data", "video_library.json")
RATINGS = os.path.join(BASE, "data", "ratings_state.json")
STATE = os.path.join(BASE, "data", "seasons_state.json")

REQUEST_DELAY = 0.3

# TMDB counts a show as ending or returning with these; they are seasons a
# viewer counts, so they are included rather than filtered.
FINAL_STATUSES = {"Ended", "Canceled", "Returning Series"}


def load_state():
    try:
        with open(STATE, encoding="utf-8") as f:
            state = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"date": None, "cards": {}}
    state.setdefault("cards", {})
    return state


def save_state(state):
    state["date"] = date.today().isoformat()
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)


def tmdb_id_for(card, ratings):
    """The tmdb_id tmdb_ratings.py already resolved, or None."""
    entry = (ratings.get(card["id"]) or {})
    if entry.get("status") != "ok":
        return None, entry.get("status")
    tid = entry.get("tmdb_id")
    if not tid:
        return None, "no-id"
    return tid, None


def fetch_total_seasons(op, api_key, tmdb_id):
    """Season count for one show, or a status describing why there isn't one.

    season_count comes off the detail endpoint and counts numbered seasons
    only. A show still running has aired fewer than that, so the count is the
    ceiling, not a target -- the caller labels it as such.
    """
    import urllib.parse
    url = ("https://api.themoviedb.org/3/tv/"
           f"{tmdb_id}?api_key={urllib.parse.quote(api_key)}")
    try:
        detail = json.load(op.open(url, timeout=20))
    except Exception as exc:  # one failed lookup must not lose the batch
        return {"status": "error", "error": str(exc)[:120]}

    total = detail.get("number_of_seasons")
    if not isinstance(total, int) or total <= 0:
        # A show with only ever a movie or a special has no numbered season.
        return {"status": "no-seasons", "tmdb_title": detail.get("name")}

    aired = [s.get("season_number") for s in (detail.get("seasons") or [])
             if isinstance(s.get("season_number"), int)
             and s.get("season_number", 0) > 0
             and s.get("air_date") is not None]

    return {
        "status": "ok",
        "tmdb_title": detail.get("name"),
        "status_tmdb": detail.get("status"),
        "total_seasons": total,
        "aired_seasons": len(aired),
        "returning": detail.get("status") not in FINAL_STATUSES,
        "checked": date.today().isoformat(),
    }


def run(limit=None, refresh=False):
    key = tmdb_client.load_env().get("TMDB_API_KEY")
    if not key:
        raise SystemExit("TMDB_API_KEY missing in .env")

    with open(LIBRARY, encoding="utf-8") as f:
        cards = json.load(f)["cards"]
    try:
        with open(RATINGS, encoding="utf-8") as f:
            ratings = json.load(f).get("cards") or {}
    except (FileNotFoundError, json.JSONDecodeError):
        raise SystemExit("data/ratings_state.json missing -- run tmdb_ratings.py first")

    state = load_state()
    op = tmdb_client.opener()
    n_ok = n_new = n_skip = n_noid = 0

    for c in cards:
        if c.get("kind") != "serial":
            continue
        tmdb_id, why = tmdb_id_for(c, ratings)
        if tmdb_id is None:
            # Not an error: many serials have no usable TMDB match, and that is
            # already recorded in ratings_state.json.
            if c["id"] not in state["cards"]:
                state["cards"][c["id"]] = {"status": "no-match", "checked": date.today().isoformat()}
                n_noid += 1
            continue
        if not refresh and state["cards"].get(c["id"], {}).get("status") == "ok":
            n_skip += 1
            continue
        if limit and n_new >= limit:
            break

        entry = fetch_total_seasons(op, key, tmdb_id)
        state["cards"][c["id"]] = entry
        n_new += 1
        if entry["status"] == "ok":
            n_ok += 1
        time.sleep(REQUEST_DELAY)
        if n_new % 25 == 0:
            save_state(state)
            print(f"  ... {n_new} checked", flush=True)

    save_state(state)
    print(f"checked {n_new} ({n_ok} with a season count), {n_skip} already known, "
          f"{n_noid} without a TMDB match -> {os.path.relpath(STATE, BASE)}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int)
    ap.add_argument("--refresh", action="store_true", help="re-check shows already counted")
    a = ap.parse_args()
    run(limit=a.limit, refresh=a.refresh)
