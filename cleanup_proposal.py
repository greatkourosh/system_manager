#!/usr/bin/env python3
"""Turn duplicates.json into an actionable cleanup proposal with keep/delete recommendations."""
import json
import os
import re
from collections import Counter
from datetime import date

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")

with open(os.path.join(DATA, "duplicates.json"), encoding="utf-8") as f:
    dups = json.load(f)

today = date.today().isoformat()

# Path-quality score: lower = keep. Prefer organized roots, non-copy suffixes, deeper=more specific.
COPY_PAT = re.compile(r"[\s(_\-]\(?\d\)?\)?\.(?=[^.]*$)|[- _]copy\d*\.|[- _]new\.", re.I)
GOOD_ROOTS = ("G:", "E:/Programs", "E:/Learning")


def path_score(p):
    s = 0
    norm = p.replace("\\", "/")
    if COPY_PAT.search(p):
        s += 50
    if re.search(r"\((2|\d{2,})\)\.", p):
        s += 40
    if norm.startswith(GOOD_ROOTS):
        s -= 5
    if "/tmp/" in norm.lower() or "\\tmp\\" in norm.lower():
        s += 30
    depth = norm.count("/")
    s += min(depth, 12)
    return s


groups = dups["groups"]
proposal = []
for g in groups:
    copies = g["copies"]
    scored = sorted(copies, key=path_score)
    keep = scored[0]
    deletes = scored[1:]
    # risk: same-basename pairs in different folders are usually real dups; differing basenames need review
    base = lambda p: os.path.splitext(os.path.basename(p))[0].lower()
    same_name = len({base(c) for c in copies}) == 1
    risk = "safe" if same_name else "review"
    if g["category"] in ("music", "video") and not same_name:
        risk = "review"  # mislabeled media needs human eyes
    proposal.append({
        "category": g["category"],
        "sha256": g["sha256"],
        "size_bytes": g["size_bytes"],
        "redundant_bytes": g["redundant_bytes"],
        "risk": risk,
        "keep": keep,
        "delete": deletes,
    })

proposal.sort(key=lambda x: -x["redundant_bytes"])

out = {
    "date": today,
    "total_groups": len(proposal),
    "safe_groups": sum(1 for p in proposal if p["risk"] == "safe"),
    "review_groups": sum(1 for p in proposal if p["risk"] == "review"),
    "safe_redundant_bytes": sum(p["redundant_bytes"] for p in proposal if p["risk"] == "safe"),
    "review_redundant_bytes": sum(p["redundant_bytes"] for p in proposal if p["risk"] == "review"),
    "groups": proposal,
}
with open(os.path.join(DATA, "cleanup_proposal.json"), "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)

# markdown summary
gb = lambda b: f"{b / 1024**3:.1f} GB"
by_cat = Counter()
by_cat_safe = Counter()
for p in proposal:
    by_cat[p["category"]] += p["redundant_bytes"]
    if p["risk"] == "safe":
        by_cat_safe[p["category"]] += p["redundant_bytes"]

lines = [
    f"# Cleanup Proposal — {today}", "",
    f"- {out['safe_groups']} SAFE groups ({gb(out['safe_redundant_bytes'])}) — identical filenames, machine-decidable",
    f"- {out['review_groups']} REVIEW groups ({gb(out['review_redundant_bytes'])}) — differing filenames, need your eyes",
    f"- **Total reclaimable: {gb(out['safe_redundant_bytes'] + out['review_redundant_bytes'])}**",
    "", "## Redundant by category", "",
    "| Category | Safe | Review | Total |", "|---|---|---|---|",
]
for cat, tot in by_cat.most_common():
    lines.append(f"| {cat} | {gb(by_cat_safe.get(cat, 0))} | {gb(tot - by_cat_safe.get(cat, 0))} | {gb(tot)} |")

lines += ["", "## Top 15 SAFE deletions", ""]
n = 0
for p in proposal:
    if p["risk"] != "safe":
        continue
    lines.append(f"- {gb(p['redundant_bytes'])} — keep `{p['keep']}`")
    for d in p["delete"]:
        lines.append(f"  - ~~delete~~ `{d}`")
    n += 1
    if n >= 15:
        break

lines += ["", "## Top 10 REVIEW groups", ""]
n = 0
for p in proposal:
    if p["risk"] != "review":
        continue
    lines.append(f"- {gb(p['redundant_bytes'])} — {p['category']} — keep `{p['keep']}`")
    for d in p["delete"]:
        lines.append(f"  - delete? `{d}`")
    n += 1
    if n >= 10:
        break

with open(os.path.join(DATA, f"cleanup_proposal_{today}.md"), "w", encoding="utf-8") as f:
    f.write("\n".join(lines))

print(f"safe: {out['safe_groups']} ({gb(out['safe_redundant_bytes'])})  review: {out['review_groups']} ({gb(out['review_redundant_bytes'])})")
