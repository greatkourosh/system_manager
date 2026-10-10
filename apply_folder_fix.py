#!/usr/bin/env python3
"""Apply accepted folder renames from data/folder_fix_proposals.json (host-side).
Dry-run by default; --apply renames. Logged to commands_to_run/folder_fix_log.txt."""
import json
import os
import sys
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
PROP = os.path.join(BASE, "data", "folder_fix_proposals.json")
LOG = os.path.join(BASE, "commands_to_run", "folder_fix_log.txt")


def run(apply, area=None):
    with open(PROP, encoding="utf-8") as f:
        data = json.load(f)
    todo = [p for p in data["proposals"]
            if p.get("status") == "accepted" and (not area or p.get("area") == area)]
    log = open(LOG, "a", encoding="utf-8")
    ok = fail = skip = 0
    for p in todo:
        old, new = p["dir"], os.path.join(os.path.dirname(p["dir"]), p["proposed"])
        if not os.path.exists(old):
            log.write(f"MISSING\t{p['area']}\t{old}\n")
            skip += 1
            continue
        if os.path.exists(new):
            log.write(f"EXISTS\t{p['area']}\t{new}\n")
            skip += 1
            continue
        try:
            if apply:
                os.rename(old, new)
            log.write(f"{'RENAMED' if apply else 'WOULD'}\t{p['area']}\t{old}\n\t-> {new}\n")
            ok += 1
        except OSError as e:
            log.write(f"FAIL\t{p['area']}\t{old}\t{str(e)[:120]}\n")
            fail += 1
    summary = (f"===== {datetime.now():%Y-%m-%d %H:%M} mode={'APPLY' if apply else 'DRY-RUN'}"
               f"{f' area={area}' if area else ''}: ok={ok} fail={fail} skipped={skip} =====")
    log.write(summary + "\n")
    log.close()
    print(summary)


if __name__ == "__main__":
    if not os.path.exists(PROP):
        print("no folder_fix_proposals.json — run folder_fix.py first")
        sys.exit(1)
    area = sys.argv[sys.argv.index("--area") + 1] if "--area" in sys.argv else None
    run("--apply" in sys.argv, area=area)
