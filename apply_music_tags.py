#!/usr/bin/env python3
"""Apply tag fixes from commands_to_run/tag_fix_list.json to real music files (host-side, mutagen).

Default is DRY RUN (no writes). Pass --apply to write tags.
Every action is logged to commands_to_run/tag_log.txt. Safe to re-run (idempotent).
"""
import json
import os
import sys
from datetime import datetime

from mutagen import File as MutagenFile

from media_path import to_host_path

BASE = os.path.dirname(os.path.abspath(__file__))
FIX_LIST = os.path.join(BASE, "commands_to_run", "tag_fix_list.json")
LOG = os.path.join(BASE, "commands_to_run", "tag_log.txt")


def apply(apply_changes):
    with open(FIX_LIST, encoding="utf-8") as f:
        payload = json.load(f)
    entries = payload.get("entries", [])
    mode = "APPLY" if apply_changes else "DRY-RUN"
    with open(LOG, "a", encoding="utf-8") as log:
        log.write(f"===== RUN START {datetime.now():%Y-%m-%d %H:%M:%S} mode={mode} entries={len(entries)} =====\n")
        applied = skipped = failed = missing = 0
        for e in entries:
            path = to_host_path(e["path"])
            tags = e.get("tags", {})
            overwrites = set(e.get("overwrites", []))
            try:
                if not os.path.exists(path):
                    log.write(f"MISSING\t{path}\n")
                    missing += 1
                    continue
                audio = MutagenFile(path, easy=True)
                if audio is None:
                    log.write(f"FAIL\t{path}\tunreadable format\n")
                    failed += 1
                    continue
                written = {}
                blocked = []
                for field, value in tags.items():
                    key = "date" if field == "year" else field
                    cur = audio.get(key)
                    has = bool(cur and str(cur[0]).strip())
                    if has and field not in overwrites:
                        blocked.append(field)
                        continue
                    audio[key] = value
                    written[field] = value
                if not written:
                    log.write(f"SKIPPED\t{path}\tblocked:{','.join(blocked) or 'nothing to write'}\n")
                    skipped += 1
                    continue
                if apply_changes:
                    audio.save()
                log.write(f"{'APPLIED' if apply_changes else 'WOULD'}\t{path}\t{json.dumps(written, ensure_ascii=False)}\n")
                applied += 1
                if blocked:
                    log.write(f"  kept-existing\t{','.join(blocked)}\n")
            except Exception as ex:
                log.write(f"FAIL\t{path}\t{str(ex)[:140]}\n")
                failed += 1
        summary = f"===== RUN END: {'applied' if apply_changes else 'would-apply'}={applied} skipped={skipped} missing={missing} failed={failed} ====="
        log.write(summary + "\n")
    print(summary)
    print(f"Log: {LOG}")


if __name__ == "__main__":
    if not os.path.exists(FIX_LIST):
        print("No commands_to_run/tag_fix_list.json — export a tag plan from the dashboard first.")
        sys.exit(1)
    apply("--apply" in sys.argv)
