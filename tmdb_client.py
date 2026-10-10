#!/usr/bin/env python3
"""TMDB client + poster fetcher (host-side; needs proxy per user network). Reads .env for keys.
Writes posters into each movie folder as poster.jpg; records results in data/posters_state.json."""
import base64
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import date

BASE = os.path.dirname(os.path.abspath(__file__))
BS = "\\"
PROXY = "http://192.168.1.13:10810"


def load_env():
    env = {}
    p = os.path.join(BASE, ".env")
    if not os.path.exists(p):
        return env
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"')
    return env


def opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler({"https": PROXY, "http": PROXY}))


def tmdb_search(op, api_key, title, year):
    q = urllib.parse.quote(title)
    url = f"https://api.themoviedb.org/3/search/movie?api_key={api_key}&query={q}"
    if year:
        url += f"&year={year}"
    d = json.load(op.open(url, timeout=15))
    return (d.get("results") or [None])[0]


def tmdb_search_tv(op, api_key, title):
    q = urllib.parse.quote(title)
    url = f"https://api.themoviedb.org/3/search/tv?api_key={api_key}&query={q}"
    d = json.load(op.open(url, timeout=15))
    return (d.get("results") or [None])[0]


def fetch_poster(op, path, dest):
    img = op.open(f"https://image.tmdb.org/t/p/w500{path}", timeout=20)
    data = img.read()
    with open(dest, "wb") as f:
        f.write(data)
    return len(data)


def save_b64(card_id, data):
    """Store base64 thumbnail per card, hash-named (Windows-safe)."""
    import base64
    import hashlib
    bdir = os.path.join(BASE, "data", "posters_b64")
    os.makedirs(bdir, exist_ok=True)
    name = hashlib.md5(card_id.encode("utf-8")).hexdigest() + ".b64"
    with open(os.path.join(bdir, name), "w", encoding="utf-8") as f:
        f.write(base64.b64encode(data).decode())


def run(limit=None, root_filter=None):
    env = load_env()
    key = env.get("TMDB_API_KEY")
    if not key:
        print("TMDB_API_KEY missing in .env")
        sys.exit(1)
    with open(os.path.join(BASE, "data", "video_library.json"), encoding="utf-8") as f:
        lib = json.load(f)
    state_path = os.path.join(BASE, "data", "posters_state.json")
    state = {"date": date.today().isoformat(), "cards": {}}
    if os.path.exists(state_path):
        with open(state_path, encoding="utf-8") as f:
            state = json.load(f)
    op = opener()
    n_ok = n_miss = n_skip = 0
    for i, c in enumerate(lib["cards"]):
        if limit and n_ok + n_miss >= limit:
            break
        if root_filter and c["root"] != root_filter:
            continue
        prev = state["cards"].get(c["id"], {})
        if prev.get("status") == "ok" and prev.get("poster_file"):
            n_skip += 1
            continue
        if not c.get("title"):
            n_miss += 1
            continue
        try:
            if c["kind"] == "movie":
                res = tmdb_search(op, key, c["title"], c.get("year"))
            else:
                res = tmdb_search_tv(op, key, c["title"])
            pp = res.get("poster_path") if res else None
            if not pp:
                state["cards"][c["id"]] = {"status": "no-result", "checked": date.today().isoformat()}
                n_miss += 1
                continue
            dest = os.path.join(c["dir"], "poster.jpg")
            size = fetch_poster(op, pp, dest)
            c["poster"] = "poster.jpg"
            save_b64(c["id"], size and open(dest, "rb").read() or b"")
            state["cards"][c["id"]] = {"status": "ok", "poster_file": "poster.jpg",
                                       "tmdb_id": res.get("id"), "bytes": size,
                                       "checked": date.today().isoformat()}
            n_ok += 1
            if n_ok % 10 == 0:
                print(f"  ... {n_ok} posters fetched", flush=True)
                with open(state_path, "w", encoding="utf-8") as f:
                    json.dump(state, f, ensure_ascii=False, indent=1)
        except Exception as e:
            state["cards"][c["id"]] = {"status": "error", "error": str(e)[:120]}
            n_miss += 1
        # be polite: ~4 req/s max
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
    # persist poster refs back into library
    with open(os.path.join(BASE, "data", "video_library.json"), "w", encoding="utf-8") as f:
        json.dump(lib, f, ensure_ascii=False, indent=1)
    print(f"posters: ok={n_ok} miss/err={n_miss} skipped(existing)={n_skip}; state -> data/posters_state.json")


if __name__ == "__main__":
    limit = None
    root_filter = None
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])
    if "--root" in sys.argv:
        root_filter = sys.argv[sys.argv.index("--root") + 1]
    run(limit=limit, root_filter=root_filter)
