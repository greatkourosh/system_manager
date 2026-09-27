"""Desktop notifications for conditions the dashboard already observes.

Read-only in the same sense the collectors are: nothing here repairs anything,
it only announces what a poll has already found. The conditions come from
``server.py``'s ``suggestions()`` (low memory, low disk, no default route)
plus the two facts the packages module already computes and only shows when a
page happens to be open -- how many updates carry a phased-rollout percentage,
and how stale the host's apt indexes are.

A condition is announced once, when it newly appears. A machine that has been
disk-full for a week does not re-notify every interval; the condition is
forgotten when it clears, so it can alert again. That is why the state is a
set of keys rather than a timestamp.

Delivery is ``notify-send`` over the host's session bus, launched through
``auth.command_output`` so it inherits the bus address ``_subprocess_env``
builds. A daemon that is missing or refusing is recorded and swallowed: the
dashboard must not depend on a desktop being present.
"""
import os
import threading
import time

from . import auth

__all__ = ["Notifier", "conditions", "start_notifier"]

CHECK_INTERVAL = 300
NOTIFY_SEND = "/usr/bin/notify-send"
STALE_INDEX_DAYS = 7
SENT_HISTORY = 20


def conditions(snapshot, packages=None):
    """Return [{key, title, body}] for every condition currently true.

    ``snapshot`` is a ``server.py`` snapshot; ``packages`` is the packages
    module's snapshot, or None to skip the package-derived conditions.
    """
    result = []
    if snapshot.get("state") != "observed":
        return result
    for item in snapshot["data"].get("suggestions", []):
        result.append({"key": f"suggestion:{item['title']}",
                       "title": item["title"], "body": item["detail"]})

    if not packages or not packages.get("ok"):
        return result

    count = len(packages.get("upgradable", []))
    if count:
        body = f"{count} package{'s' if count != 1 else ''} have a newer version in the host's apt indexes."
        phased = packages.get("phased_count", 0)
        if phased:
            body += (f" {phased} are rolled out to a percentage of hosts, so apt will not"
                     " offer all of them on every machine.")
        result.append({"key": "packages:upgradable", "title": "Updates are waiting", "body": body})

    index = packages.get("index")
    if index:
        # The OLDEST index, not the newest. The packages page measures the
        # newest (when apt last refreshed anything), which stays low as long as
        # one repo answers -- so a suite that stopped being fetched is exactly
        # what the page misses. Naming the oldest makes this a different claim
        # from the page's rather than a contradictory one.
        age_days = int((time.time() - index["oldest"]) / 86400)
        if age_days > STALE_INDEX_DAYS:
            result.append({
                "key": "packages:stale-index",
                "title": f"Oldest apt index is {age_days} days old",
                "body": ("At least one suite has not been refreshed since then, so its"
                         " updates are missing from the list. Run sudo apt-get update on"
                         " the host and check for a repository that failed to fetch."),
            })
    return result


class Notifier:
    """Fires a desktop notification the first time each condition appears.

    Like ``ConnectivityStore``, this holds its state in the process. The
    shipped gunicorn runs a single worker, so one process owns the state and
    one timer notifies; raising the worker count would give each worker its own
    set, and every condition would be announced once per worker.
    """

    def __init__(self, clock=time.time, command=auth.command_output, logger=None):
        self.lock = threading.Lock()
        self.clock = clock
        self.command = command
        self.logger = logger
        self.active = set()
        self.sent = []  # newest first
        self.last_error = None

    def notify(self, item):
        """Send one notification. Returns False instead of raising on failure."""
        # argv, never a shell string: the body is host-derived text and must
        # not be word-split or glob-expanded.
        output = self.command([NOTIFY_SEND, "-a", "System Manager", "-t", "10000",
                               item["title"], item["body"]], timeout=5)
        if output is None:
            self.last_error = "notify-send failed; no desktop notification was shown."
            return False
        self.last_error = None
        self.sent.insert(0, {**item, "ts": self.clock()})
        del self.sent[SENT_HISTORY:]
        return True

    def check(self, snapshot, packages=None):
        """Announce conditions that newly appeared; return what was notified.

        A condition whose send failed stays unacknowledged, so the next tick
        tries again rather than losing the alert to one dead notification
        daemon.
        """
        fired = []
        with self.lock:
            current = {item["key"]: item for item in conditions(snapshot, packages)}
            still = {key for key in self.active if key in current}
            for key, item in current.items():
                if key in self.active:
                    continue
                if self.notify(item):
                    still.add(key)
                    fired.append(item)
            self.active = still
        return fired

    def state(self):
        with self.lock:
            return {"active": sorted(self.active), "sent": list(self.sent),
                    "last_error": self.last_error, "interval": CHECK_INTERVAL}


def _tick(notifier):
    """Collect and check, forever; a failure sleeps and tries again."""
    from .packages.api import snapshot as packages_snapshot
    from .status import collect
    while True:
        try:
            notifier.check(collect(), packages_snapshot())
        except Exception as exc:
            if notifier.logger:
                notifier.logger.warning("notification check failed: %s", exc)
        time.sleep(CHECK_INTERVAL)


def start_notifier(notifier):
    """Start the background timer, unless tests or an opt-out asked us not to.

    gunicorn does not export its worker count to the worker process, so a
    multi-worker misconfiguration cannot be detected from in here; the
    single-worker deployment is the documented one and
    ``SYSTEM_MANAGER_NOTIFY=0`` is the escape hatch.
    """
    if os.environ.get("SYSTEM_MANAGER_NOTIFY", "1") != "1":
        return None
    thread = threading.Thread(target=_tick, args=(notifier,),
                              name="sm-notifier", daemon=True)
    thread.start()
    return thread
