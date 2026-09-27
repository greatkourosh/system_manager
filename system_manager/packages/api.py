"""Packages module API endpoints. Read-only by construction.

The module's whole job is to answer "what would an upgrade do?" without
becoming one. It parses files, renders commands, and stops there -- so
auth.Approval is deliberately unused: there is no action to approve, only
text for the user to copy.
"""
import os
import time

from flask import Blueprint, jsonify, render_template, request

from .dpkg import APT_LIST_DIR, DPKG_STATUS, HostApt, DpkgError, index_age

packages_bp = Blueprint("packages_api", __name__)

# An index older than this under-reports: apt has not been told about newer
# versions, so "nothing to upgrade" may just mean "we have not looked".
STALE_INDEX_DAYS = 7


def _paths():
    """Host paths, overridable so tests can point at fixtures."""
    base = os.environ.get("PACKAGES_HOST_DIR", "/host")
    return os.path.join(base, "var/lib/dpkg/status"), os.path.join(base, "var/lib/apt/lists")


def _snapshot():
    status_path, list_dir = _paths()
    try:
        host = HostApt(status_path=status_path, list_dir=list_dir).load()
    except DpkgError as exc:
        return {
            "ok": False,
            "detail": str(exc),
            "status_path": status_path,
            "list_dir": list_dir,
            "mounts": [
                f"/var/lib/dpkg -> {status_path}",
                f"/var/lib/apt/lists -> {list_dir}",
            ],
        }
    rows = host.upgradable()
    return {
        "ok": True,
        "upgradable": rows,
        "backports": host.backports(),
        "security_count": sum(1 for r in rows if r["security"]),
        "phased_count": sum(1 for r in rows if r["phased"]),
        "suites": host._suites,
        "arches": host._arches,
        "index": index_age(list_dir),
        "status_path": status_path,
        "list_dir": list_dir,
    }


def _install_command(names):
    """The command for a set of packages, and its dry-run twin."""
    joined = " ".join(names)
    return f"sudo apt-get install --only-upgrade {joined}", f"sudo apt-get -s install --only-upgrade {joined}"


@packages_bp.route("/")
def list_packages():
    """Upgradable packages, with the commands to run them."""
    snapshot = _snapshot()
    rows = snapshot["upgradable"] if snapshot["ok"] else []
    names = [r["name"] for r in rows]
    command, dry_run = _install_command(names) if names else ("", "")
    backports_command, backports_dry_run = _install_command([r["name"] for r in snapshot.get("backports", [])])

    payload = dict(snapshot)
    payload.update({"command": command, "dry_run": dry_run,
                    "backports_command": backports_command, "backports_dry_run": backports_dry_run})
    if request.headers.get("Accept", "").startswith("application/json"):
        return jsonify(payload)

    return render_template(
        "packages/list.html",
        snapshot=snapshot,
        rows=rows,
        backports=snapshot.get("backports", []),
        command=command,
        dry_run=dry_run,
        backports_command=backports_command,
        backports_dry_run=backports_dry_run,
        stale_days=STALE_INDEX_DAYS,
        now=time.time(),
    )
