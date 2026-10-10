#!/usr/bin/env python3
"""Real ratings and vote counts from TMDB, keyed by card id.

The `rating` a card already carries is scraped out of its folder name and
rounded to one decimal. On a 30-card sample it agreed with TMDB to within 0.8
(mean -0.04), so this is a precision and confidence upgrade rather than a
different score: it adds the second decimal and, more usefully, `vote_count`,
which says how many people that number is actually worth.

Kept out of app.py the way season_scan.py is, and cached in a sidecar rather
than written into video_library.json, because that file is regenerated from
scratch by video_catalog.py build() and would take the ratings with it. data/
is not in the container, and the page must not reach TMDB on render, so this
runs on the host:

    python3 tmdb_ratings.py               # all cards
    python3 tmdb_ratings.py --limit 20    # sample first
    python3 tmdb_ratings.py --root serials
"""
import argparse
import json
import os
import re
import time
from datetime import date

import tmdb_client

BASE = os.path.dirname(os.path.abspath(__file__))
LIBRARY = os.path.join(BASE, "data", "video_library.json")
STATE = os.path.join(BASE, "data", "ratings_state.json")

# Below this many votes a score is shown but marked thin, so a 9.3 from twelve
# people is not read as a 9.3 from twelve thousand.
LOW_VOTES = 100

# TMDB allows a burst, but the free key is per-IP and the polite rate is fine.
REQUEST_DELAY = 0.3


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _norm(s):
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


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


def match(card, search):
    """State entry for one card, or a record of why there isn't a rating.

    `search(kind, title, year)` returns TMDB's ranked results. TMDB filters
    movies by year but a serial card carries none -- serial folder names never
    hold one -- so a serial match rests on the title alone and can land on a
    different show entirely. An exactly-normalised title therefore wins over
    TMDB's own first pick, and the matched title is recorded either way so a
    bad match shows up in the state file instead of silently on a card.
    """
    title = (card.get("title") or "").strip()
    if not title:
        return {"status": "no-title", "checked": date.today().isoformat()}

    try:
        results = search(card.get("kind"), title, card.get("year")) or []
    except Exception as exc:  # one failed lookup must not lose the batch
        return {"status": "error", "error": str(exc)[:120], "checked": date.today().isoformat()}

    if not results:
        return {"status": "no-result", "checked": date.today().isoformat()}

    want = _norm(title)
    hit = next((r for r in results if _norm(r.get("title") or r.get("name")) == want), results[0])

    score = _num(hit.get("vote_average"))
    if score is None:
        return {"status": "no-score", "tmdb_id": hit.get("id"),
                "checked": date.today().isoformat()}

    return {
        "status": "ok",
        "tmdb_id": hit.get("id"),
        "tmdb_title": hit.get("title") or hit.get("name"),
        "tmdb_year": (hit.get("release_date") or hit.get("first_air_date") or "")[:4] or None,
        "rating": score,
        "votes": int(hit.get("vote_count") or 0),
        "exact": _norm(hit.get("title") or hit.get("name")) == want,
        "checked": date.today().isoformat(),
    }


def build_search(key, op):
    def search(kind, title, year=None):
        import urllib.parse
        if kind == "movie":
            url = f"https://api.themoviedb.org/3/search/movie?api_key={key}&query={urllib.parse.quote(title)}"
            if year:
                url += f"&year={urllib.parse.quote(str(year))}"
        else:
            url = f"https://api.themoviedb.org/3/search/tv?api_key={key}&query={urllib.parse.quote(title)}"
        return json.load(op.open(url, timeout=20)).get("results") or []
    return search


def run(limit=None, root_filter=None, refresh=False):
    key = tmdb_client.load_env().get("TMDB_API_KEY")
    if not key:
        raise SystemExit("TMDB_API_KEY missing in .env")

    with open(LIBRARY, encoding="utf-8") as f:
        cards = json.load(f)["cards"]

    state = load_state()
    search = build_search(key, tmdb_client.opener())
    n_ok = n_new = n_skip = n_bad = 0

    for c in cards:
        if root_filter and c.get("root") != root_filter:
            continue
        prev = state["cards"].get(c["id"], {})
        # `error` is deliberately absent: a failed lookup is not an answer, so
        # the next run retries it. The rest are final and only re-queried on
        # --refresh, or they would re-ask TMDB the same thing forever.
        if not refresh and prev.get("status") in ("ok", "no-result", "no-title", "no-score"):
            n_skip += 1
            continue
        if limit and n_new >= limit:
            break

        state["cards"][c["id"]] = match(c, search)
        n_new += 1
        if state["cards"][c["id"]]["status"] == "ok":
            n_ok += 1
            if not state["cards"][c["id"]]["exact"]:
                n_bad += 1
        time.sleep(REQUEST_DELAY)
        if n_new % 25 == 0:
            save_state(state)
            print(f"  ... {n_new} checked", flush=True)

    save_state(state)
    print(f"checked {n_new} ({n_ok} with a score, {n_bad} title mismatch), "
          f"{n_skip} already known -> {os.path.relpath(STATE, BASE)}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int)
    ap.add_argument("--root")
    ap.add_argument("--refresh", action="store_true", help="re-check cards already answered")
    a = ap.parse_args()
    run(limit=a.limit, root_filter=a.root, refresh=a.refresh)
