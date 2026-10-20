#!/usr/bin/env python3
"""Fetch artist photos for the Music page (host-side, opt-in). Stdlib only.

For each artist in data/music_catalog.json: MusicBrainz artist -> Wikidata id
-> P18 image -> Wikimedia Commons thumbnail. Writes base64 JPEG to
data/artist_images/<md5(name)>.b64, which the app serves. Artists with no
result are recorded so reruns skip them.

Usage: python artist_images.py [--limit 100]
"""
import argparse
import base64
import hashlib
import json
import os
import time
import urllib.parse
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
OUT_DIR = os.path.join(DATA, "artist_images")
CATALOG = os.path.join(DATA, "music_catalog.json")
MISS = os.path.join(DATA, "artist_images_missing.json")
UA = "MediaOrganizer/1.0 (https://localhost; kourosh@local)"
THUMB = 300

def get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.load(r)
        except Exception:
            if attempt == 2:
                return None
            time.sleep(2)

def get_bytes(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read()

def wikidata_image(name):
    q = urllib.parse.quote(f'artist:"{name}"')
    data = get_json(f"https://musicbrainz.org/ws/2/artist/?query={q}&fmt=json&limit=1")
    time.sleep(1)
    if not data or not data.get("artists"):
        return None
    mb = data["artists"][0]
    if (mb.get("score") or 0) < 90:
        return None
    detail = get_json(f"https://musicbrainz.org/ws/2/artist/{mb['id']}?inc=url-rels&fmt=json")
    time.sleep(1)
    wd = None
    for rel in (detail or {}).get("relations", []):
        if rel.get("type") == "wikidata":
            wd = rel["url"]["resource"].rstrip("/").split("/")[-1]
            break
    if not wd:
        return None
    ent = get_json(f"https://www.wikidata.org/wiki/Special:EntityData/{wd}.json")
    claims = ((ent or {}).get("entities", {}).get(wd, {}).get("claims", {}))
    p18 = claims.get("P18")
    if not p18:
        return None
    fname = p18[0]["mainsnak"]["datavalue"]["value"]
    return ("https://commons.wikimedia.org/w/index.php?"
            + urllib.parse.urlencode({"title": "Special:FilePath/" + fname, "width": THUMB}))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=100)
    args = ap.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)
    with open(CATALOG, encoding="utf-8") as f:
        catalog = json.load(f)
    names = []
    for c in catalog:
        parts = c["path"].replace("\\", "/").split("/")
        try:
            i = next(i for i, p in enumerate(parts) if p.lower() == "music")
        except StopIteration:
            continue
        if i + 1 < len(parts) - 1 and parts[i + 1] not in names:
            names.append(parts[i + 1])
    missing = set(json.load(open(MISS))) if os.path.exists(MISS) else set()

    done = 0
    for name in names:
        if done >= args.limit:
            break
        out = os.path.join(OUT_DIR, hashlib.md5(name.encode("utf-8")).hexdigest() + ".b64")
        if os.path.exists(out) or name in missing:
            continue
        done += 1
        url = wikidata_image(name)
        if url:
            raw = get_bytes(url)
            tmp = out + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(base64.b64encode(raw).decode("ascii"))
            os.replace(tmp, out)
            print("saved", name)
        else:
            missing.add(name)
        with open(MISS, "w", encoding="utf-8") as f:
            json.dump(sorted(missing), f, ensure_ascii=False)

if __name__ == "__main__":
    main()
