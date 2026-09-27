"""Journal module API endpoints. Read-only by construction.

The module's whole job is to answer "what did the host log?" without becoming
able to change it. It runs ``journalctl`` queries and renders the result --
no vacuum, no rotate, no writes -- so ``auth.Approval`` is deliberately
unused, for the same reason the packages module leaves it unused.

Two properties of ``journalctl`` shape the code more than anything else:

* **It exits 0 whether it read the journal, found no matching entries, or
  could not read the journal at all.** Only stderr tells those apart. So a
  missing mount is checked up front and reported with the compose line that
  fixes it; a blank table is never allowed to mean "the database is
  unreachable", which is the exact ambiguity the packages module was built
  to avoid.
* **The container's own journal is empty and its machine-id is the image's**,
  so a bare ``journalctl`` reads the wrong thing. Every query passes
  ``--root`` at the host's journal directory.

Queries are built as a fixed argv and never as a shell string, so a filter
value can never become a second option. ``priority`` is checked against a
fixed set for the same reason: it is the one parameter that changes the
query's shape rather than its range.
"""
import json
import os
import re
import subprocess
import time
from datetime import datetime

from flask import Blueprint, jsonify, render_template, request

journal_bp = Blueprint("journal_api", __name__)

# journalctl's exit code is 0 whether it found entries, found nothing, or could
# not read the journal at all -- only stderr distinguishes them. So the mount
# has to be checked before querying, not inferred from the result.
#
# --root takes a *filesystem root* and looks for <root>/var/log/journal itself,
# so it is the /host prefix, not the journal directory. Passing the journal
# directory here makes journalctl look for /host/var/log/journal/var/log/journal,
# report nothing, and exit 0.
JOURNAL_DIR = "/host/var/log/journal"
JOURNAL_ROOT = "/host"

# syslog priorities. journalctl takes the same names, so a filter that is
# checked against this list cannot smuggle in an option.
PRIORITIES = ("emerg", "alert", "crit", "err", "warning", "notice", "info", "debug")

# A limit with no ceiling is how a page request turns into reading 1.4 GB.
DEFAULT_LIMIT = 200
MAX_LIMIT = 2000

# systemd's own time syntax is a language of its own; rather than reimplement
# it, allow the shapes that are unambiguous and reject the rest. A newline
# would break the command echo the page renders.
_SINCE_RE = re.compile(r"^[A-Za-z0-9:.+,\- ]{1,40}$")
_UNIT_RE = re.compile(r"^[^\r\n]{1,200}$")


def _paths():
    """(root for journalctl, journal dir to check), overridable for tests."""
    root = os.environ.get("JOURNAL_HOST_DIR", JOURNAL_ROOT)
    return root, os.path.join(root, "var/log/journal")


def _run(args, timeout=15):
    """Run journalctl with a fixed argv. Returns (stdout, stderr, ok)."""
    root, _journal = _paths()
    argv = ["journalctl", "--root", root, "--no-pager", *args]
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "", str(exc), False
    return result.stdout, result.stderr, result.returncode == 0


def _mount_error():
    """The explanation for an unreachable journal, with the fix in it."""
    _root, journal_dir = _paths()
    return {
        "ok": False,
        "mount_error": True,
        "detail": (
            "The host journal is not mounted, so there is nothing to read. "
            "This is a container configuration problem, not an empty log: "
            "journalctl exits 0 either way, so a blank page cannot be trusted. "
            "Add this line to docker-compose.yml and recreate the container:\n"
            "  - /var/log/journal:/host/var/log/journal:ro"
        ),
        "journal_dir": journal_dir,
    }


def _priority_name(value):
    try:
        return PRIORITIES[int(value)]
    except (TypeError, ValueError, IndexError):
        return "info"


def _time_text(stamp):
    """Local wall-clock time for an entry, or a dash when the field is absent."""
    if stamp is None:
        return "—"
    return datetime.fromtimestamp(stamp).strftime("%Y-%m-%d %H:%M:%S")


def _entry(line):
    """One journal record as the page wants it, or None if it is not one."""
    try:
        raw = json.loads(line)
    except ValueError:
        return None
    message = raw.get("MESSAGE", "")
    if isinstance(message, list):
        # A non-UTF8 payload arrives as an array of byte values rather than
        # text. Decode it rather than dropping the entry, but say so instead
        # of pretending the bytes were the message.
        try:
            message = bytes(bytearray(message)).decode("utf-8", "replace")
        except (TypeError, ValueError):
            message = str(message)
    try:
        stamp = int(raw["__REALTIME_TIMESTAMP"]) / 1_000_000
    except (KeyError, TypeError, ValueError):
        stamp = None
    # A unit-scoped query legitimately returns rows filed under init.scope
    # ("Started systemd-resolved.service - ..."), so show the message's own
    # unit rather than the scope it happened to be started in.
    unit = raw.get("_SYSTEMD_UNIT") or raw.get("SYSLOG_IDENTIFIER") or "-"
    if unit.endswith(".scope"):
        for token in message.split():
            if token.endswith(".service"):
                unit = token.rstrip(",")
                break
    return {
        "timestamp": stamp,
        "time_text": _time_text(stamp),
        "priority": _priority_name(raw.get("PRIORITY")),
        "unit": unit,
        "identifier": raw.get("SYSLOG_IDENTIFIER") or "-",
        "pid": raw.get("_PID") or "-",
        "hostname": raw.get("_HOSTNAME") or "-",
        "boot_id": raw.get("_BOOT_ID") or "",
        "message": message,
    }


def _filters():
    """Validate the query string into argv arguments, rejecting anything odd."""
    args, applied, rejected = [], [], []

    priority = (request.args.get("priority") or "").strip()
    if priority:
        if priority in PRIORITIES:
            args += ["--priority", priority]
            applied.append(f"priority={priority}")
        else:
            rejected.append(f"priority={priority!r} is not a priority")

    unit = (request.args.get("unit") or "").strip()
    if unit:
        if _UNIT_RE.match(unit):
            args += ["--unit", unit]
            applied.append(f"unit={unit}")
        else:
            rejected.append("unit is not a usable unit name")

    since = (request.args.get("since") or "").strip()
    if since:
        if _SINCE_RE.match(since):
            args += ["--since", since]
            applied.append(f"since={since}")
        else:
            rejected.append("since is not a timestamp journalctl can read")

    boot = (request.args.get("boot") or "").strip()
    if boot:
        # An index, or the word "all". Not a raw boot id, so it cannot be a
        # flag.
        if boot == "all" or re.match(r"^-?\d{1,4}$", boot):
            args += ["--boot", boot]
            applied.append(f"boot={boot}")
        else:
            rejected.append("boot is not a boot index")

    try:
        limit = int(request.args.get("limit", DEFAULT_LIMIT))
    except ValueError:
        limit = DEFAULT_LIMIT
    limit = max(1, min(limit, MAX_LIMIT))
    applied.append(f"limit={limit}")
    return args, limit, applied, rejected


def query():
    """The host's log entries, plus what was asked for and what was refused."""
    _root, journal_dir = _paths()
    if not os.path.isdir(journal_dir):
        return _mount_error()

    args, limit, applied, rejected = _filters()

    out, err, ok = _run([*args, "--output", "json", "--lines", str(limit)])
    entries = [e for e in (_entry(line) for line in out.splitlines()) if e]
    entries.reverse()  # journalctl emits oldest first; a log reads newest first.

    return {
        "ok": ok,
        "entries": entries,
        "count": len(entries),
        "error_count": sum(1 for e in entries if e["priority"] in ("emerg", "alert", "crit", "err")),
        "applied": applied,
        "rejected": rejected,
        # journalctl exits 0 for a good read and for an unreadable journal
        # alike, so a quiet stderr is the only failure signal there is.
        "detail": err.strip() if not ok else "",
        "journal_dir": journal_dir,
        "priorities": list(PRIORITIES),
        "max_limit": MAX_LIMIT,
    }


def boots():
    """Boot history, newest first, so a boot can be scoped to."""
    _root, journal_dir = _paths()
    if not os.path.isdir(journal_dir):
        return []
    out, _err, ok = _run(["--list-boots", "--output", "json"])
    if not ok or not out.strip():
        return []
    try:
        raw = json.loads(out)
    except ValueError:
        return []
    return [
        {
            "index": b.get("index"),
            "boot_id": b.get("boot_id", "")[:8],
            "first_entry": b.get("first_entry"),
            "last_entry": b.get("last_entry"),
        }
        for b in reversed(raw)
    ]


def _export(data):
    """The same entries as a downloadable JSON document."""
    response = jsonify({
        "count": data["count"],
        "applied": data["applied"],
        "rejected": data["rejected"],
        "entries": data["entries"],
    })
    response.headers["Content-Disposition"] = "attachment; filename=journal.json"
    return response


@journal_bp.route("/")
def list_entries():
    """Journal entries, filtered and rendered."""
    data = query()
    rows = data.get("entries", [])
    if request.args.get("export") == "json":
        return _export(data)

    if request.headers.get("Accept", "").startswith("application/json"):
        return jsonify(data)

    return render_template(
        "journal/list.html",
        data=data,
        rows=rows,
        boots=boots(),
        now=time.time(),
    )
