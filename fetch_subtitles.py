#!/usr/bin/env python3
"""Fetch EN+FA subtitles from OpenSubtitles for videos missing them (host-side).
Reads data/video_library.json, queries api.opensubtitles.com, saves <videostem>.<lang>.srt
next to each video. Requires OPENSUBTITLES_API_KEY, OPENSUBTITLES_USERNAME and
OPENSUBTITLES_PASSWORD in .env. Dry-run by default; --apply downloads.
Logs to commands_to_run/subtitle_log.txt. Free tier: ~20 downloads/24h."""
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime

from media_path import to_host_path
from season_scan import VIDEO_EXTS

BASE = os.path.dirname(os.path.abspath(__file__))

# Cloudflare on www.opensubtitles.com rejects the API User-Agent (error 1010) when fetching the
# actual subtitle file, so the download link needs browser-like headers.
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def load_env():
    env = {}
    with open(os.path.join(BASE, ".env"), encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"')
    return env


def api(op, key, url, token=None):
    headers = {"Api-Key": key, "User-Agent": "MediaOrganizer v1", "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    return json.load(op.open(req, timeout=25))


def post_api(op, key, url, payload, token=None):
    headers = {"Api-Key": key, "User-Agent": "MediaOrganizer v1",
               "Content-Type": "application/json", "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers, method="POST")
    return json.load(op.open(req, timeout=30))


def login(op, env):
    body = {"username": env["OPENSUBTITLES_USERNAME"], "password": env["OPENSUBTITLES_PASSWORD"]}
    d = post_api(op, env["OPENSUBTITLES_API_KEY"], "https://api.opensubtitles.com/api/v1/login", body)
    token = d.get("token")
    if not token:
        raise RuntimeError(f"login failed: {json.dumps(d)[:120]}")
    user = d.get("user", {})
    print(f"logged in as {env['OPENSUBTITLES_USERNAME']} (level={user.get('level')}, allowed/day={user.get('allowed_downloads')})")
    return token


EP_PAT = re.compile(r"[sS](\d{1,2})[\.\- ]?[eE](\d{1,3})")

# low-value targets that burn quota: extras, featurettes, samples
EXTRA_DIRS = ("featurettes", "deleted scenes", "extras", "samples", "sample", "behind",
              "interview", "trailer", "trailers", "bonus", "specials", "featurette")

# Game installs sit under the same G: tree as the library and get scanned with it:
# 535 of the unreachable paths are Dota 2 assets (heroes, events, portraits,
# healthbar_deaths...) that no subtitle service will ever match. Excluded by rule
# rather than by path failure, or each is a permanent "no results found".
GAME_DIRS = ("dota", "steamapps", "steam library")

# Matched as whole path *components*, not bare substrings: a plain "behind"
# reaches "Behind Her Eyes" and "sample" reaches an episode named for one.
_EXCLUDE_DIRS = EXTRA_DIRS + GAME_DIRS


def _excluded(path):
    parts = re.split(r"[\\/]", path.lower())
    return any(p in _EXCLUDE_DIRS for p in parts)


def run(apply=False, limit=None, root_filter=None, lang_filter=None, budget=None, card_ids=None):
    if isinstance(lang_filter, str):
        lang_filter = [lang_filter]
    env = load_env()
    key = env["OPENSUBTITLES_API_KEY"]
    op = urllib.request.build_opener()
    token = login(op, env)
    user = api(op, key, "https://api.opensubtitles.com/api/v1/utilities/user_info", token) if False else {}

    with open(os.path.join(BASE, "data", "video_library.json"), encoding="utf-8") as f:
        lib = json.load(f)

    plan = []
    for c in lib["cards"]:
        if root_filter and c["root"] != root_filter:
            continue
        if card_ids is not None and c["id"] not in card_ids:
            continue
        missing = lang_filter or [l for l in ("en", "fa") if not c[f"has_{l}_sub"]]
        missing = [l for l in missing if not c[f"has_{l}_sub"]] if not lang_filter else missing
        if not missing:
            continue
        videos = c.get("videos") or [{"path": c["sample_video"]}]
        # skip extras/featurettes unless every video in the card lives in one
        core = [v for v in videos if not _excluded(v["path"])]
        if not core:
            continue  # every video is an extra — don't burn quota on it
        videos = core
        plan.append((c, missing, videos))
    print(f"{len(plan)} cards missing subtitles" + (f" (limit {limit})" if limit else "") +
          (f" (budget {budget})" if budget else ""))

    log = open(os.path.join(BASE, "commands_to_run", "subtitle_log.txt"), "a", encoding="utf-8")
    fetched = failed = checked = 0
    unreachable = 0
    stop = False
    try:
        for c, missing, videos in plan:
            if stop:
                break
            if limit and fetched >= limit:
                break
            if budget and fetched >= budget:
                print(f"budget {budget} reached — stopping (resumable tomorrow)")
                break
            for v in videos:
                if stop:
                    break
                # v["path"] is the Windows path from the library; every existence
                # check and every write has to go through the local translation.
                local = to_host_path(v["path"])
                if not os.path.exists(local):
                    log.write(f"UNREACHABLE\t-\t{v['path']}\n")
                    unreachable += 1
                    continue
                stem = os.path.splitext(local)[0]
                for lang in missing:
                    if budget and fetched >= budget:
                        stop = True
                        break
                    dest = f"{stem}.{lang}.srt"
                    if os.path.exists(dest):
                        continue
                    checked += 1
                    try:
                        q = urllib.parse.quote(os.path.basename(v["path"]))
                        d = api(op, key, f"https://api.opensubtitles.com/api/v1/subtitles?query={q}&languages={lang}", token)
                        items = d.get("data") or []
                        if not items:
                            log.write(f"NO-RESULT\t{lang}\t{v['path']}\n")
                            continue
                        files = items[0]["attributes"].get("files") or []
                        if not files:
                            log.write(f"NO-FILE\t{lang}\t{v['path']}\n")
                            continue
                        fid = files[0]["file_id"]
                        if not apply:
                            log.write(f"WOULD\t{lang}\t{v['path']}\tfile_id={fid}\t->\t{dest}\n")
                            fetched += 1
                            continue
                        dl = post_api(op, key, "https://api.opensubtitles.com/api/v1/download",
                                      {"file_id": fid}, token)
                        link = dl.get("link")
                        if not link:
                            log.write(f"FAIL\t{lang}\t{v['path']}\t{json.dumps(dl)[:120]}\n")
                            failed += 1
                            continue
                        blob = op.open(urllib.request.Request(link, headers=BROWSER_HEADERS), timeout=40).read()
                        with open(dest, "wb") as f2:
                            f2.write(blob)
                        log.write(f"FETCHED\t{lang}\t{v['path']}\t{len(blob)} bytes\n")
                        fetched += 1
                        time.sleep(1.1)  # search+download rate limiting
                    except Exception as e:
                        msg = str(e)[:140]
                        log.write(f"FAIL\t{lang}\t{v['path']}\t{msg}\n")
                        failed += 1
                        if "406" in msg or "quota" in msg.lower():
                            print("download quota exhausted — resumable tomorrow")
                            stop = True
                            break
                        time.sleep(2)
    finally:
        summary = f"===== {datetime.now():%Y-%m-%d %H:%M} mode={'APPLY' if apply else 'DRY-RUN'}: fetched/would={fetched} failed={failed} checked={checked} unreachable={unreachable} ====="
        log.write(summary + "\n")
        log.close()
    print(summary)
    print("Log: commands_to_run/subtitle_log.txt")


if __name__ == "__main__":
    apply = "--apply" in sys.argv
    limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else None
    root_filter = sys.argv[sys.argv.index("--root") + 1] if "--root" in sys.argv else None
    lang_filter = sys.argv[sys.argv.index("--langs") + 1].split(",") if "--langs" in sys.argv else None
    budget = int(sys.argv[sys.argv.index("--budget") + 1]) if "--budget" in sys.argv else None
    # Interactive callers pass a bare "en"; normalise here so run() always sees
    # a list. Left as-is it iterates the string and searches for "e" and "n".
    if lang_filter and isinstance(lang_filter, str):
        lang_filter = [lang_filter]
    card_ids = set(sys.argv[sys.argv.index("--card-ids") + 1].split("|")) if "--card-ids" in sys.argv else None
    run(apply=apply, limit=limit, root_filter=root_filter, lang_filter=lang_filter,
        budget=budget, card_ids=card_ids)
