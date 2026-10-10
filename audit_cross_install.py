#!/usr/bin/env python3
"""Classify proposal paths without accessing or modifying library files."""
import copy
import json
import re
from collections import Counter
from datetime import date
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data"


def steam_root(path):
    norm = path.replace("\\", "/")
    match = re.search(r"/steamapps/common/[^/]+(?:/|$)", norm, re.IGNORECASE)
    return norm[:match.start()] if match else None


def audit(proposal):
    result = copy.deepcopy(proposal)
    changed = []
    cross_install = []
    for group in result["groups"]:
        roots = sorted({root for path in [group["keep"], *group["delete"]]
                        if (root := steam_root(path)) is not None})
        if len(roots) > 1:
            cross_install.append(group)
            if group["risk"] == "safe":
                changed.append(group)
            group["risk"] = "review"
            group["review_reason"] = "Paths span different Steam library roots; installation dependencies require review."

    result["total_groups"] = len(result["groups"])
    for risk in ("safe", "review"):
        groups = [g for g in result["groups"] if g["risk"] == risk]
        result[f"{risk}_groups"] = len(groups)
        result[f"{risk}_redundant_bytes"] = sum(g["redundant_bytes"] for g in groups)
    result["audit"] = {
        "date": date.today().isoformat(),
        "source": "cleanup_proposal.json",
        "method": "Path-only classification; no filesystem or content verification",
        "cross_install_groups": len(cross_install),
        "reclassified_groups": len(changed),
        "organizing_scope_note": "User says steam_ubuntu does not need organizing. No deletion authorized.",
    }
    return result, changed, cross_install


def report(result, changed, cross_install):
    gib = lambda value: f"{value / 1024**3:.3f} GiB"
    lines = [
        f"# Phase 4.5 Audit — {result['audit']['date']}", "",
        "## Scope", "",
        "Path-only classification of the existing proposal. No library files were read, renamed, moved, or deleted.",
        "The source proposal and deletion selections are unchanged; results are in `data/cleanup_proposal_audited.json`.",
        "`steam_ubuntu` does not need organizing, per the user. This audit does not configure a global scanner exclusion or authorize deletion.", "",
        "## Summary", "",
        f"- Input groups: **{result['total_groups']}**",
        f"- Cross-install groups: **{len(cross_install)}**",
        f"- Reclassified `safe` to `review`: **{len(changed)}**",
        f"- Review after audit: **{result['review_groups']}** ({gib(result['review_redundant_bytes'])})",
        f"- Original `safe` labels retained: **{result['safe_groups']}** ({gib(result['safe_redundant_bytes'])})",
        "",
        "## Interpretation and limitations", "",
        "Different prefixes before `/steamapps/common/<game>/` identify different Steam library roots.",
        "Groups spanning multiple roots are marked `review`, including groups already under review.",
        "All other labels are preserved, not certified safe: identical content or filenames do not prove a path is dispensable, even within one installation or when hardlinked.",
        "Counts and sizes come from the existing proposal, not a fresh scan. File existence, hashes, inodes, manifests, and actual reclaimable space were not verified.",
        "The active dashboard still reads the original proposal. Do not treat that proposal's `safe` labels as deletion approval.", "",
        "## Per-category classification", "",
        "| Category | Total | Retained safe | Review |",
        "|---|---:|---:|---:|",
    ]
    total = Counter(g["category"] for g in result["groups"])
    safe = Counter(g["category"] for g in result["groups"] if g["risk"] == "safe")
    review = Counter(g["category"] for g in result["groups"] if g["risk"] == "review")
    for category in sorted(total):
        lines.append(f"| {category} | {total[category]} | {safe[category]} | {review[category]} |")
    lines.extend(["", "## Cross-install library roots", ""])
    roots = sorted({steam_root(p) for g in cross_install for p in [g["keep"], *g["delete"]] if steam_root(p)})
    lines.extend(f"- `{root}`" for root in roots)
    lines.extend(["", "## Reclassified groups", ""])
    for group in changed:
        lines.append(f"- `{group['sha256']}` — {group['category']}, {gib(group['redundant_bytes'])}")
        lines.append(f"  - Existing keep: `{group['keep']}`")
        lines.extend(f"  - Review only: `{path}`" for path in group["delete"])
    lines.extend(["", "## Retained original safe labels (not deletion approval)", ""])
    for group in result["groups"]:
        if group["risk"] == "safe":
            lines.append(f"- `{group['sha256']}` — {group['category']}, {gib(group['redundant_bytes'])} — `{group['keep']}`")
    return "\n".join(lines) + "\n"


def main():
    proposal = json.loads((DATA / "cleanup_proposal.json").read_text(encoding="utf-8"))
    result, changed, cross_install = audit(proposal)
    text = report(result, changed, cross_install)
    (DATA / "cleanup_proposal_audited.json").write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    report_path = DATA / f"cleanup_audit_{result['audit']['date']}.md"
    report_path.write_text(text, encoding="utf-8")
    print(f"Cross-install: {len(cross_install)}; reclassified: {len(changed)}; review: {result['review_groups']}; retained safe: {result['safe_groups']}")
    print(f"Report: {report_path}")


if __name__ == "__main__":
    main()
