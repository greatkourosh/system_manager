"""Ad-hoc E2E check for the tag-plan workflow (kept out of the suite; suite has its own section)."""
import json
import os
import shutil
import urllib.error
import urllib.request

BS = chr(92)
BASE = "http://localhost:5001"


def call(method, path, body=None):
    req = urllib.request.Request(BASE + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    try:
        return json.load(urllib.request.urlopen(req))
    except urllib.error.HTTPError as e:
        return {"ok": False, "status": e.code, "body": e.read().decode()[:200]}


if os.path.exists("data/tag_plan.json"):
    shutil.copy("data/tag_plan.json", "tag_plan_backup.json")

needle = BS + "Ebi" + BS
audit = json.load(open("data/music_tag_audit.json", encoding="utf-8"))
ebi = [p["path"] for p in audit["problems"] if needle in p["path"]]
print(f"Ebi problem files in audit: {len(ebi)}")

j = call("POST", "/api/tags/propose", {"filters": {"q": needle, "state": "unplanned", "missing": "all"}})
print("propose:", j)

j2 = call("POST", "/api/tags/propose", {"filters": {"q": "", "state": "unplanned", "missing": "all"}})
print("cap enforced:", not j2.get("ok"), str(j2.get("body") or j2.get("error"))[:90])

print("stats:", call("GET", "/api/tags/stats"))

if ebi:
    target = ebi[0]
    print("\nmanual edit on:", target[:80])
    j = call("POST", "/api/tags/set-entry", {"path": target, "fields": {"album": {"value": "MANUAL TEST ALBUM"}}, "accepted": True})
    print("set-entry ok:", j.get("ok"), "album now:", j.get("entry", {}).get("fields", {}).get("album", {}).get("value"))

    j = call("POST", "/api/tags/bulk", {"mode": "accept", "page_paths": ebi[:3]})
    print("bulk accept:", j)

j = call("POST", "/api/tags/export", {})
print("\nexport:", {k: j.get(k) for k in ("ok", "count", "skipped_existing", "file")})
if os.path.exists("commands_to_run/tag_fix_list.json"):
    payload = json.load(open("commands_to_run/tag_fix_list.json", encoding="utf-8"))
    print("fix list entries:", payload["count"])
    if payload["entries"]:
        e = payload["entries"][0]
        print("sample entry:", e["path"][:70], "->", e["tags"])
