#!/usr/bin/env python3
"""Queue subtitle fetches from the web UI for a host-side runner to drain.

The web app cannot fetch subtitles itself. The container has no
/media/kourosh/Multimedia mount, so it cannot even see the media files, and
running the fetcher in-process would block the request for the length of a
rate-limited HTTP call. So the UI appends a request to a queue file in the
bind-mounted commands_to_run/, and `subtitle_runner.py` drains it on the host.

Requests are coalesced by (card, language) and always dry-run first: the
OpenSubtitles free tier allows ~20 downloads/day, and a careless "fetch all"
would spend the whole day's quota on the first 20 videos. An apply run has to
be an explicit, separate action.
"""
import json
import os
from datetime import date, datetime

# The queue must be the file subtitle_runner.py drains on the host, so it lives
# at the repo root. Mirrors DATA in api.py.
BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
QUEUE = os.path.join(BASE, "commands_to_run", "subtitle_queue.json")
LOG = os.path.join(BASE, "commands_to_run", "subtitle_queue_log.jsonl")

# Free-tier daily download limit. The UI shows it so the cap is never a surprise.
DAILY_QUOTA = 20

# Languages the fetcher knows how to request.
LANGS = ("en", "fa")


def load_queue():
    try:
        with open(QUEUE, encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"date": date.today().isoformat(), "requests": []}
    # A queue from yesterday would silently spend today's quota against a
    # result that is no longer wanted, so start clean on a new day.
    if data.get("date") != date.today().isoformat():
        return {"date": date.today().isoformat(), "requests": []}
    data.setdefault("requests", [])
    return data


def save_queue(q):
    os.makedirs(os.path.dirname(QUEUE), exist_ok=True)
    tmp = QUEUE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(q, f, ensure_ascii=False, indent=1)
    os.replace(tmp, QUEUE)


def enqueue(card_ids, langs, apply=False):
    """Add cards/langs to the queue, coalescing with what is already there.

    Returns the resulting request count. Re-queuing a card that is already
    pending is a no-op, so a user can click repeatedly without filling the
    queue with duplicates.
    """
    if apply:
        raise ValueError("apply must go through run_subtitles(), not enqueue()")
    for lang in langs:
        if lang not in LANGS:
            raise ValueError(f"unsupported language: {lang}")
    q = load_queue()
    have = {(r["card_id"], r["lang"]) for r in q["requests"]}
    added = 0
    for card_id in card_ids:
        for lang in langs:
            if (card_id, lang) not in have:
                q["requests"].append({"card_id": card_id, "lang": lang,
                                      "queued_at": datetime.now().isoformat(timespec="seconds")})
                have.add((card_id, lang))
                added += 1
    save_queue(q)
    return len(q["requests"])


def remaining_quota(used):
    return max(0, DAILY_QUOTA - used)


def append_log(entry):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
