"""Tests for the configurable notification thresholds.

The cutoffs are the 10% hardcoded in ``server.suggestions`` and the 7-day apt
index age. What matters here is that a saved value actually reaches the three
places that decide whether to fire: the advisories on the dashboard, the
notifier's own condition list, and the advice text itself -- and that a
rejected value changes nothing.
"""
import os
import tempfile
import unittest

from system_manager import create_app, status
from system_manager.auth import Settings
from system_manager.notifier import Notifier, conditions


def snapshot(available_pct, total=1000):
    """A minimal server.py snapshot with a known amount of memory free."""
    return {
        "state": "observed",
        "data": {
            "memory": {"MemTotal": {"state": "observed", "value": total},
                       "MemAvailable": {"state": "observed", "value": total * available_pct / 100}},
            "filesystem": {"state": "observed", "value": {"total": total, "available": total}},
            "routes": {"IPv4": {"state": "observed", "value": ["default via 10.0.0.1"]}},
        },
    }


def observed_suggestions(thresholds=None, available_pct=5):
    """Ask the real server.py for its advisories at a given memory level."""
    import server
    snap = snapshot(available_pct)
    return server.suggestions(snap["data"], thresholds)


class SuggestionThresholdTests(unittest.TestCase):
    """server.suggestions: the default is unchanged, a threshold is honoured."""

    def titles(self, suggestions):
        return [item["title"] for item in suggestions]

    def test_default_is_unchanged_with_no_thresholds(self):
        # The 10% behaviour must survive a caller that passes nothing, which
        # is every caller that has not opted in.
        self.assertIn("Memory pressure", self.titles(observed_suggestions(available_pct=5)))
        self.assertNotIn("Memory pressure", self.titles(observed_suggestions(available_pct=50)))

    def test_a_wider_threshold_fires_earlier(self):
        thresholds = {"memory_low_pct": 80}
        self.assertIn("Memory pressure", self.titles(observed_suggestions(thresholds, available_pct=50)))
        self.assertNotIn("Memory pressure", self.titles(observed_suggestions(thresholds, available_pct=90)))

    def test_the_advice_text_states_the_configured_cutoff(self):
        [item] = observed_suggestions({"memory_low_pct": 25}, available_pct=5)
        self.assertIn("25%", item["detail"])
        self.assertNotIn("10%", item["detail"])

    def test_a_fractional_threshold_does_not_render_as_a_false_zero(self):
        [item] = observed_suggestions({"memory_low_pct": 12.5}, available_pct=5)
        self.assertIn("12.5%", item["detail"])

    def test_a_zero_threshold_is_honoured_not_treated_as_unset(self):
        # 0 is a real answer: "never warn me about memory". A `or`-style
        # default would silently turn it back into 10%.
        self.assertEqual(self.titles(observed_suggestions({"memory_low_pct": 0}, available_pct=5)), [])

    def test_a_missing_key_keeps_the_default_for_that_key_only(self):
        thresholds = {"disk_low_pct": 90}
        self.assertIn("Memory pressure", self.titles(observed_suggestions(thresholds, available_pct=5)))


class SettingsStoreTests(unittest.TestCase):
    """The key/value table beside the audit log."""

    def make(self):
        return Settings(os.path.join(tempfile.mkdtemp(prefix="sm-settings-"), "actions.db"))

    def test_defaults_are_returned_before_anything_is_saved(self):
        self.assertEqual(self.make().thresholds(),
                         {"disk_low_pct": 10, "memory_low_pct": 10, "stale_index_days": 7})

    def test_a_saved_value_survives_a_new_store_on_the_same_file(self):
        path = os.path.join(tempfile.mkdtemp(prefix="sm-settings-"), "actions.db")
        Settings(path).set_thresholds({"memory_low_pct": 25})
        self.assertEqual(Settings(path).thresholds()["memory_low_pct"], 25)

    def test_valid_values_are_stored(self):
        stored, error = self.make().set_thresholds({"memory_low_pct": 25, "stale_index_days": 30})
        self.assertIsNone(error)
        self.assertEqual(stored["memory_low_pct"], 25)
        self.assertEqual(stored["stale_index_days"], 30)

    def test_a_rejected_value_leaves_the_stored_thresholds_untouched(self):
        settings = self.make()
        settings.set_thresholds({"memory_low_pct": 25})
        stored, error = settings.set_thresholds({"memory_low_pct": 5000})
        self.assertIsNone(stored)
        self.assertIn("memory_low_pct", error)
        self.assertEqual(settings.thresholds()["memory_low_pct"], 25)

    def test_every_bad_value_is_reported_at_once(self):
        stored, error = self.make().set_thresholds({"memory_low_pct": 0, "stale_index_days": "soon"})
        self.assertIsNone(stored)
        self.assertIn("memory_low_pct", error)
        self.assertIn("stale_index_days", error)

    def test_a_bool_is_refused_where_an_int_is_required(self):
        # isinstance(True, int) is True in Python; a checkbox posting true
        # must not be stored as 1.
        stored, error = self.make().set_thresholds({"memory_low_pct": True})
        self.assertIsNone(stored)
        self.assertIn("whole number", error)

    def test_a_float_is_refused_rather_than_truncated(self):
        stored, error = self.make().set_thresholds({"memory_low_pct": 12.5})
        self.assertIsNone(stored)
        self.assertIn("whole number", error)


class NotifierThresholdTests(unittest.TestCase):
    """The notifier reads its stale-index cutoff through the callable."""

    def test_the_stale_index_cutoff_is_taken_from_the_callable(self):
        snap = {"state": "observed", "data": {"suggestions": []}}
        now = __import__("time").time()

        def packages(oldest_days):
            return {"ok": True, "upgradable": [],
                    "index": {"oldest": now - oldest_days * 86400, "newest": now, "count": 1}}

        notifier = Notifier(command=lambda argv, timeout=2: "",
                            thresholds=lambda: {"stale_index_days": 30})
        notifier.check(snap, packages(10))
        self.assertEqual(notifier.state()["active"], [])
        notifier.check(snap, packages(40))
        self.assertIn("packages:stale-index", notifier.state()["active"])

    def test_a_broken_settings_read_falls_back_instead_of_raising(self):
        # The alert loop must survive a settings failure; losing a threshold
        # to a stale default is better than the loop dying on every tick.
        notifier = Notifier(command=lambda argv, timeout=2: "",
                            thresholds=lambda: {"stale_index_days": None})
        self.assertEqual(notifier._stale_days(), 7)

    def test_forget_allows_a_condition_to_announce_again(self):
        notifier = Notifier(command=lambda argv, timeout=2: "")
        snap = {"state": "observed", "data": {"suggestions": [{"title": "T", "detail": "d"}]}}
        self.assertEqual(len(notifier.check(snap)), 1)
        self.assertEqual(len(notifier.check(snap)), 0)
        notifier.forget()
        self.assertEqual(len(notifier.check(snap)), 1)


class ThresholdApiTests(unittest.TestCase):
    def make_app(self, auth_enabled=False):
        directory = tempfile.mkdtemp(prefix="sm-thresholds-")
        return create_app({"DISABLE_AUTH": 0 if auth_enabled else 1,
                           "TESTING": True,
                           "AUDIT_PATH": os.path.join(directory, "actions.db")})

    def test_get_returns_the_defaults(self):
        response = self.make_app().test_client().get("/api/notifications/thresholds")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["memory_low_pct"], 10)

    def test_put_stores_and_get_reads_back(self):
        client = self.make_app().test_client()
        response = client.put("/api/notifications/thresholds", json={"memory_low_pct": 30})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(client.get("/api/notifications/thresholds").get_json()["memory_low_pct"], 30)

    def test_put_rejects_an_out_of_range_value(self):
        client = self.make_app().test_client()
        response = client.put("/api/notifications/thresholds", json={"memory_low_pct": 900})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(client.get("/api/notifications/thresholds").get_json()["memory_low_pct"], 10)

    def test_put_rejects_an_unknown_key(self):
        response = self.make_app().test_client().put(
            "/api/notifications/thresholds", json={"cpu_pct": 50})
        self.assertEqual(response.status_code, 400)
        self.assertIn("cpu_pct", response.get_json()["error"])

    def test_a_rejected_body_returns_the_thresholds_unchanged(self):
        # The error carries the current values so a form can re-render
        # itself without a second round trip.
        client = self.make_app().test_client()
        response = client.put("/api/notifications/thresholds", json={"memory_low_pct": 0})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["thresholds"]["memory_low_pct"], 10)

    def test_the_endpoint_requires_a_session_when_auth_is_on(self):
        client = self.make_app(auth_enabled=True).test_client()
        self.assertEqual(client.get("/api/notifications/thresholds").status_code, 401)
        self.assertEqual(client.put("/api/notifications/thresholds",
                                    json={"memory_low_pct": 20}).status_code, 401)

    def test_a_saved_threshold_reaches_the_status_advisories(self):
        # The end-to-end claim: what the user saves is what the dashboard
        # then computes, not a second hardcoded copy of the default. The
        # argument is captured rather than read off a real host snapshot,
        # which would otherwise make this test depend on the machine's
        # actual free memory.
        app = self.make_app()
        client = app.test_client()
        seen = []
        original = status.collect

        def spy(thresholds=None):
            seen.append(thresholds)
            return {"state": "unavailable", "detail": "stubbed", "data": None}

        status.collect = spy
        try:
            client.get("/api/status")
            client.put("/api/notifications/thresholds", json={"memory_low_pct": 42})
            client.get("/api/status")
        finally:
            status.collect = original
        self.assertEqual(seen[0]["memory_low_pct"], 10)
        self.assertEqual(seen[1]["memory_low_pct"], 42)

    def test_forgetting_runs_on_a_successful_save(self):
        app = self.make_app()
        notifier = app.config["NOTIFIER"]
        notifier.active.add("packages:upgradable")
        app.test_client().put("/api/notifications/thresholds", json={"memory_low_pct": 30})
        self.assertEqual(notifier.active, set())


if __name__ == "__main__":
    unittest.main()
