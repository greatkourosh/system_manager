#!/usr/bin/env python3
"""Drain the subtitle queue on the host, where the media and credentials live.

The web app enqueues; this runs. It is intentionally a separate process rather
than a Flask background thread because fetching needs /media/kourosh/Multimedia
and the OpenSubtitles credentials, neither of which the container has.

    python3 subtitle_runner.py            # dry-run: report what a run would do
    python3 subtitle_runner.py --apply    # actually download, up to the quota

Apply is capped at subtitle_queue.DAILY_QUOTA downloads per day and never
exceeds it silently: the cap is printed before the run and the queue is left
intact for the next day.
"""
import argparse
import json
import os
import sys

import fetch_subtitles
from media_path import to_host_path
from system_manager.organizer import subtitle_queue

BASE = os.path.dirname(os.path.abspath(__file__))
LIBRARY = os.path.join(BASE, "data", "video_library.json")


def cards_by_id():
    with open(LIBRARY, encoding="utf-8") as f:
        return {c["id"]: c for c in json.load(f)["cards"]}


def videos_for(card, lang):
    """The card's video paths that still lack `lang`, skipping unreachable ones."""
    host_dir = to_host_path(card["dir"])
    if not os.path.isdir(host_dir):
        return None
    out = []
    for name in sorted(os.listdir(host_dir)):
        if os.path.splitext(name)[1].lower() in fetch_subtitles.VIDEO_EXTS:
            out.append(os.path.join(card["dir"], name))
    return out


def pending_requests(cards, q):
    """Queue entries whose card exists, with the card's unmet languages."""
    seen = {}
    for req in q["requests"]:
        card = cards.get(req["card_id"])
        if card is None:
            continue
        key = (req["card_id"], req["lang"])
        if key in seen:
            continue
        videos = videos_for(card, req["lang"])
        if not videos:
            continue
        seen[key] = {"card": card, "lang": req["lang"], "videos": videos}
    return list(seen.values())


def already_have(card, lang):
    host_dir = to_host_path(card["dir"])
    if not os.path.isdir(host_dir):
        return False
    marker = f".{lang}.srt"
    for name in os.listdir(host_dir):
        if name.lower().endswith(marker):
            return True
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="download; default is a dry-run that writes nothing")
    ap.add_argument("--budget", type=int, default=None,
                    help="cap downloads for this run (never above the daily quota)")
    args = ap.parse_args()

    q = subtitle_queue.load_queue()
    if not q["requests"]:
        print("queue is empty — nothing to do")
        return 0

    cards = cards_by_id()
    items = pending_requests(cards, q)
    if not items:
        print("queued cards are unreachable or already satisfied; clearing queue")
        subtitle_queue.save_queue({"date": q["date"], "requests": []})
        return 0

    todo = [it for it in items if not already_have(it["card"], it["lang"])]
    already = len(items) - len(todo)
    budget = args.budget
    if budget is None:
        budget = subtitle_queue.DAILY_QUOTA

    print(f"{len(todo)} request(s) pending, {already} already satisfied, "
          f"{len(todo) - len(items)} queued card(s) not found on disk")
    print(f"mode={'APPLY' if args.apply else 'DRY-RUN'} budget={budget} "
          f"(daily quota {subtitle_queue.DAILY_QUOTA})")

    if not todo:
        subtitle_queue.save_queue({"date": q["date"], "requests": []})
        return 0

    # fetch_subtitles works per (root, langs); drive it once per language so the
    # per-language 'missing' logic inside it stays the single source of truth.
    by_lang = {}
    for it in todo:
        by_lang.setdefault(it["lang"], []).append(it)

    total_budget = budget
    for lang, group in by_lang.items():
        if total_budget <= 0:
            print("budget exhausted before this language — left queued for tomorrow")
            break
        share = total_budget if len(by_lang) == 1 else max(1, total_budget // len(by_lang))
        ids = {it["card"]["id"] for it in group}
        print(f"\n-- {lang}: {len(group)} card(s) --")
        fetch_subtitles.run(apply=args.apply, limit=len(ids),
                            lang_filter=lang, budget=share, card_ids=ids)
        total_budget -= share

    if args.apply:
        remaining = [r for r in q["requests"]
                     if not already_have(cards.get(r["card_id"], {}), r["lang"])]
        subtitle_queue.save_queue({"date": q["date"], "requests": remaining})
        print(f"\n{len(remaining)} request(s) left queued for the next run")
    else:
        print("\ndry-run: nothing written. Re-run with --apply to download.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
