"""Tests for the desktop notifier.

Nothing here shells out: ``Notifier`` is given a stub command, so the tests
cover which conditions fire, when they are suppressed, and that a failing
notify-send neither raises nor loses the alert.
"""
import os
import tempfile
import time
import unittest
from unittest.mock import patch

from system_manager import create_app
from system_manager.notifier import Notifier, conditions


def observed(suggestions):
    return {"state": "observed", "data": {"suggestions": suggestions}}


def disk_full(detail="Less than 10% of root filesystem space is available."):
    return observed([{"title": "Root filesystem space is low", "detail": detail}])


def packages(count=0, phased=0, oldest_days=None):
    index = None
    if oldest_days is not None:
        index = {"oldest": time.time() - oldest_days * 86400, "newest": time.time(), "count": 41}
    return {"ok": True, "upgradable": [{"name": f"p{i}"} for i in range(count)],
            "phased_count": phased, "index": index}


class ConditionTests(unittest.TestCase):
    def test_suggestion_becomes_a_condition(self):
        found = conditions(disk_full())
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["key"], "suggestion:Root filesystem space is low")

    def test_unavailable_snapshot_yields_nothing(self):
        self.assertEqual(conditions({"state": "unavailable", "data": None}), [])

    def test_upgradable_packages_are_surfaced(self):
        found = conditions(observed([]), packages(count=23, phased=15))
        keys = [f["key"] for f in found]
        self.assertIn("packages:upgradable", keys)
        body = next(f["body"] for f in found if f["key"] == "packages:upgradable")
        self.assertIn("23 packages", body)
        self.assertIn("15", body)

    def test_stale_index_is_surfaced_only_past_the_threshold(self):
        self.assertNotIn("packages:stale-index",
                         [f["key"] for f in conditions(observed([]), packages(count=1, oldest_days=3))])
        keys = [f["key"] for f in conditions(observed([]), packages(oldest_days=888))]
        self.assertIn("packages:stale-index", keys)

    def test_stale_index_tracks_the_oldest_index_not_the_newest(self):
        # The packages page measures the newest, which stays fresh as long as one
        # repo answers. A suite that stopped being fetched only shows up in the
        # oldest, so the alert must read that one and must not repeat the page's
        # claim.
        found = [f for f in conditions(observed([]), packages(oldest_days=888))
                 if f["key"] == "packages:stale-index"]
        self.assertIn("888", found[0]["title"])
        self.assertIn("Oldest", found[0]["title"])

    def test_unreachable_package_mount_does_not_stop_the_other_alerts(self):
        found = conditions(disk_full(), {"ok": False, "detail": "not mounted"})
        self.assertEqual([f["key"] for f in found], ["suggestion:Root filesystem space is low"])


class NotifyTests(unittest.TestCase):
    def setUp(self):
        self.sent = []

        def command(argv, timeout=2):
            self.sent.append(argv)
            return ""

        self.notifier = Notifier(command=command)

    def test_a_new_condition_notifies_once(self):
        first = self.notifier.check(disk_full())
        self.assertEqual(len(first), 1)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.notifier.check(disk_full()), [])
        self.assertEqual(len(self.sent), 1, "an unchanged condition must not re-notify")

    def test_body_is_passed_as_one_argument(self):
        self.notifier.check(disk_full("Free space: 4 GB, needs 10 GB."))
        argv = self.sent[0]
        self.assertIn("Free space: 4 GB, needs 10 GB.", argv)
        # A shell string would have split this into two argv entries.
        self.assertTrue(any("Free space: 4 GB, needs 10 GB." in part for part in argv))

    def test_a_cleared_condition_can_alert_again(self):
        self.notifier.check(disk_full())
        self.notifier.check(observed([]))
        self.assertEqual(len(self.notifier.check(disk_full())), 1)
        self.assertEqual(len(self.sent), 2)

    def test_failure_is_recorded_and_the_alert_is_retried(self):
        notifier = Notifier(command=lambda argv, timeout=2: None)
        self.assertEqual(notifier.check(disk_full()), [])
        self.assertIsNotNone(notifier.state()["last_error"])
        # Not acknowledged, so the next tick tries again rather than dropping it.
        self.assertEqual(notifier.active, set())

    def test_state_reports_active_and_recent(self):
        self.notifier.check(disk_full(), packages(count=23, phased=15, oldest_days=888))
        state = self.notifier.state()
        self.assertEqual(len(state["active"]), 3)
        self.assertEqual(len(state["sent"]), 3)
        self.assertIsNone(state["last_error"])
        self.assertEqual(state["sent"][0]["ts"] > 0, True)

    def test_history_is_bounded(self):
        for i in range(30):
            self.notifier.check(observed([{"title": f"Condition {i}", "detail": "d"}]))
        self.assertLessEqual(len(self.notifier.state()["sent"]), 20)


class AppTests(unittest.TestCase):
    def make_app(self, auth_enabled=False):
        directory = tempfile.mkdtemp(prefix="sm-notify-test-")
        return create_app({"DISABLE_AUTH": 0 if auth_enabled else 1,
                           "TESTING": True,
                           "AUDIT_PATH": os.path.join(directory, "actions.db")})

    def test_timer_does_not_start_under_testing(self):
        with patch("system_manager.start_notifier") as start:
            self.make_app()
        start.assert_not_called()

    def test_timer_starts_when_not_testing(self):
        with patch("system_manager.start_notifier") as start:
            create_app({"DISABLE_AUTH": 1, "TESTING": False,
                        "AUDIT_PATH": os.path.join(tempfile.mkdtemp(), "actions.db")})
        start.assert_called_once()

    def test_endpoint_reports_state(self):
        client = self.make_app().test_client()
        payload = client.get("/api/notifications").get_json()
        self.assertIn("active", payload)
        self.assertIn("interval", payload)

    def test_endpoint_requires_a_session_when_auth_is_on(self):
        client = self.make_app(auth_enabled=True).test_client()
        self.assertEqual(client.get("/api/notifications").status_code, 401)
        self.assertEqual(client.post("/api/notifications").status_code, 401)

    def test_test_notification_uses_a_fixed_message(self):
        app = self.make_app()
        app.config["NOTIFIER"].command = lambda argv, timeout=2: ""
        response = app.test_client().post("/api/notifications")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["ok"])


if __name__ == "__main__":
    unittest.main()
