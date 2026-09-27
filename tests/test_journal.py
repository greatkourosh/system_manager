"""Tests the journal module.

No fixture journal exists here, so what is pinned down is the part that is easy
to get wrong and expensive to debug live: journalctl exits 0 whether it read
the journal, found nothing, or could not read it at all, and every filter value
travels in argv where a leading dash would be a second option.
"""
import json
import os
import unittest
from unittest import mock

from system_manager import create_app
from system_manager import journal_api


def _record(**overrides):
    raw = {
        "__REALTIME_TIMESTAMP": "1790535294550406",
        "PRIORITY": "6",
        "MESSAGE": "hello",
        "_SYSTEMD_UNIT": "docker.service",
        "SYSLOG_IDENTIFIER": "dockerd",
        "_PID": "2218",
        "_HOSTNAME": "kourosh-pc",
        "_BOOT_ID": "0a79054e14984cd1bdce8dac4a134836",
    }
    raw.update(overrides)
    return json.dumps(raw)


class EntryTests(unittest.TestCase):
    def test_timestamp_is_microseconds_not_nanoseconds(self):
        """__REALTIME_TIMESTAMP is microseconds. Dividing by 1000 would put
        every entry in the year 57000 and still look like a number."""
        entry = journal_api._entry(_record())
        self.assertAlmostEqual(entry["timestamp"], 1790535294.550406, places=4)

    def test_missing_timestamp_becomes_a_dash_not_a_crash(self):
        entry = journal_api._entry(_record(__REALTIME_TIMESTAMP=None))
        self.assertIsNone(entry["timestamp"])
        self.assertEqual(entry["time_text"], "—")

    def test_priority_number_becomes_a_name(self):
        self.assertEqual(journal_api._entry(_record(PRIORITY="3"))["priority"], "err")
        self.assertEqual(journal_api._entry(_record(PRIORITY="0"))["priority"], "emerg")
        self.assertEqual(journal_api._entry(_record(PRIORITY="6"))["priority"], "info")

    def test_out_of_range_priority_does_not_index_off_the_end(self):
        self.assertEqual(journal_api._entry(_record(PRIORITY="99"))["priority"], "info")

    def test_byte_array_message_is_rendered_not_dropped(self):
        """Non-UTF8 payloads come back as a list of byte arrays. The entry is
        still worth showing, so it is decoded rather than discarded."""
        entry = journal_api._entry(_record(MESSAGE=[104, 105]))
        self.assertEqual(entry["message"], "hi")

    def test_unit_falls_back_to_syslog_identifier(self):
        entry = journal_api._entry(_record(_SYSTEMD_UNIT=None))
        self.assertEqual(entry["unit"], "dockerd")

    def test_scope_rows_show_the_unit_they_are_about(self):
        """A unit-scoped query returns "Started <unit> - ..." rows filed under
        init.scope. Reporting init.scope for all of them hides what matched."""
        entry = journal_api._entry(_record(
            _SYSTEMD_UNIT="init.scope",
            MESSAGE="Started systemd-resolved.service - Network Name Resolution."))
        self.assertEqual(entry["unit"], "systemd-resolved.service")

    def test_line_that_is_not_json_is_skipped(self):
        self.assertIsNone(journal_api._entry("-- No entries --"))


class FilterTests(unittest.TestCase):
    """Every filter has to survive a value that starts with a dash."""

    def setUp(self):
        self.app = create_app({"TESTING": True, "DISABLE_AUTH": True})

    def _args(self, query):
        with self.app.test_request_context(query):
            args, limit, applied, rejected = journal_api._filters()
        return args, limit, applied, rejected

    def test_valid_filters_become_paired_argv(self):
        args, _limit, applied, rejected = self._args("/?priority=err&unit=sshd.service&since=-24h&boot=-1")
        self.assertEqual(rejected, [])
        self.assertIn("--priority", args)
        # Paired, not joined: a value can never become its own option.
        self.assertEqual(args[args.index("--priority") + 1], "err")
        self.assertEqual(args[args.index("--unit") + 1], "sshd.service")
        self.assertEqual(args[args.index("--since") + 1], "-24h")
        self.assertEqual(args[args.index("--boot") + 1], "-1")

    def test_priority_is_checked_against_the_known_set(self):
        args, _l, _a, rejected = self._args("/?priority=--output=short")
        self.assertNotIn("--priority", args)
        self.assertEqual(len(rejected), 1)
        self.assertIn("not a priority", rejected[0])

    def test_rejected_filter_is_reported_rather_than_silently_dropped(self):
        """A refused filter that vanished would leave the page showing a query
        the user never asked for."""
        _args, _l, _a, rejected = self._args("/?since=<script>")
        self.assertEqual(len(rejected), 1)

    def test_newline_in_a_unit_is_refused(self):
        # A newline in a filter would break the command echo the page renders.
        _args, _l, _a, rejected = self._args("/?unit=foo%0abar")
        self.assertEqual(len(rejected), 1)

    def test_boot_accepts_an_index_or_all_but_not_a_flag(self):
        self.assertIn("--boot", self._args("/?boot=-2")[0])
        self.assertIn("--boot", self._args("/?boot=all")[0])
        args, _l, _a, rejected = self._args("/?boot=--disk-usage")
        self.assertNotIn("--boot", args)
        self.assertEqual(len(rejected), 1)

    def test_limit_is_capped(self):
        self.assertEqual(self._args("/?limit=999999")[1], journal_api.MAX_LIMIT)
        self.assertEqual(self._args("/?limit=0")[1], 1)
        self.assertEqual(self._args("/?limit=abc")[1], journal_api.DEFAULT_LIMIT)


class RootTests(unittest.TestCase):
    """--root takes a filesystem root, and passing the journal directory to it
    makes journalctl silently find nothing. This is worth pinning because the
    result is a clean exit 0 and an empty page, not an error."""

    def setUp(self):
        self.app = create_app({"TESTING": True, "DISABLE_AUTH": True})

    def test_root_is_the_host_prefix_not_the_journal_directory(self):
        root, journal_dir = journal_api._paths()
        self.assertEqual(root, "/host")
        self.assertNotEqual(root, journal_dir)
        self.assertTrue(journal_dir.startswith(root))

    def test_journalctl_is_asked_for_the_root(self):
        with mock.patch.object(journal_api.subprocess, "run") as run:
            run.return_value = mock.Mock(stdout="", stderr="", returncode=0)
            journal_api._run(["--lines", "1"])
        argv = run.call_args[0][0]
        self.assertEqual(argv[argv.index("--root") + 1], "/host")


class QueryTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app({"TESTING": True, "DISABLE_AUTH": True})

    def _query(self, query="/"):
        with self.app.test_request_context(query):
            return journal_api.query()

    def test_missing_mount_is_reported_with_the_fix_not_as_an_empty_log(self):
        """journalctl exits 0 for an unreadable journal, so a blank page could
        mean either. The mount is checked first."""
        with mock.patch.object(journal_api.os.path, "isdir", return_value=False):
            data = self._query()
        self.assertFalse(data["ok"])
        self.assertTrue(data["mount_error"])
        self.assertIn("docker-compose.yml", data["detail"])
        self.assertNotIn("entries", data)

    def test_entries_are_newest_first(self):
        out = "\n".join([
            _record(MESSAGE="old", __REALTIME_TIMESTAMP="1000000"),
            _record(MESSAGE="new", __REALTIME_TIMESTAMP="9000000"),
        ])
        with mock.patch.object(journal_api.os.path, "isdir", return_value=True), \
             mock.patch.object(journal_api, "_run", return_value=(out, "", True)):
            entries = self._query()["entries"]
        self.assertEqual([e["message"] for e in entries], ["new", "old"])

    def test_error_count_only_counts_priority_three_and_above(self):
        out = "\n".join([
            _record(PRIORITY="3"), _record(PRIORITY="4"),
            _record(PRIORITY="0"), _record(PRIORITY="6"),
        ])
        with mock.patch.object(journal_api.os.path, "isdir", return_value=True), \
             mock.patch.object(journal_api, "_run", return_value=(out, "", True)):
            data = self._query()
        self.assertEqual(data["count"], 4)
        self.assertEqual(data["error_count"], 2)

    def test_journalctl_failure_is_surfaced_even_though_its_exit_code_is_zero(self):
        with mock.patch.object(journal_api.os.path, "isdir", return_value=True), \
             mock.patch.object(journal_api, "_run", return_value=("", "", False)):
            data = self._query()
        self.assertFalse(data["ok"])


class BootsTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app({"TESTING": True, "DISABLE_AUTH": True})

    def test_boots_are_newest_first(self):
        out = json.dumps([
            {"index": -1, "boot_id": "aaaabbbbcccc", "first_entry": 1, "last_entry": 2},
            {"index": 0, "boot_id": "ddddeeeeffff", "first_entry": 3, "last_entry": 4},
        ])
        with mock.patch.object(journal_api.os.path, "isdir", return_value=True), \
             mock.patch.object(journal_api, "_run", return_value=(out, "", True)):
            with self.app.test_request_context("/"):
                boots = journal_api.boots()
        self.assertEqual([b["index"] for b in boots], [0, -1])
        self.assertEqual(boots[0]["boot_id"], "ddddeeee")

    def test_malformed_boots_output_is_empty_not_an_exception(self):
        with mock.patch.object(journal_api.os.path, "isdir", return_value=True), \
             mock.patch.object(journal_api, "_run", return_value=("not json", "", True)):
            with self.app.test_request_context("/"):
                self.assertEqual(journal_api.boots(), [])


class JournalPageTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app({"TESTING": True, "DISABLE_AUTH": True})
        self.client = self.app.test_client()

    def test_page_renders_without_the_host_mount(self):
        with mock.patch.object(journal_api.os.path, "isdir", return_value=False):
            response = self.client.get("/logs/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("not mounted", response.get_data(as_text=True))

    def test_json_api_reports_failure_rather_than_an_empty_list(self):
        with mock.patch.object(journal_api.os.path, "isdir", return_value=False):
            response = self.client.get("/logs/", headers={"Accept": "application/json"})
        payload = response.get_json()
        self.assertFalse(payload["ok"])
        self.assertTrue(payload["mount_error"])

    def _fake_run(self, entries_out, boots_out="[]"):
        """journalctl is called twice per page render -- once for entries, once
        for boots -- so a single fixed return value would feed the wrong output
        to one of them."""
        def run(args, timeout=15):
            return (boots_out, "", True) if "--list-boots" in args else (entries_out, "", True)
        return run

    def test_rows_render_with_real_entries(self):
        out = _record(MESSAGE="something happened", _SYSTEMD_UNIT="sshd.service", PRIORITY="3")
        with mock.patch.object(journal_api.os.path, "isdir", return_value=True), \
             mock.patch.object(journal_api, "_run", self._fake_run(out)):
            response = self.client.get("/logs/?priority=err")
        body = response.get_data(as_text=True)
        self.assertIn("something happened", body)
        self.assertIn("sshd.service", body)

    def test_export_is_a_download_with_the_same_entries(self):
        out = _record(MESSAGE="exported line")
        with mock.patch.object(journal_api.os.path, "isdir", return_value=True), \
             mock.patch.object(journal_api, "_run", self._fake_run(out)):
            response = self.client.get("/logs/?export=json")
        self.assertIn("attachment", response.headers["Content-Disposition"])
        self.assertEqual(response.get_json()["entries"][0]["message"], "exported line")


class JournalAuthTests(unittest.TestCase):
    def test_json_callers_get_401_when_auth_is_on(self):
        app = create_app({"TESTING": True, "DISABLE_AUTH": False})
        response = app.test_client().get("/logs/", headers={"Accept": "application/json"})
        self.assertEqual(response.status_code, 401)

    def test_browser_navigation_is_redirected_to_the_dashboard(self):
        app = create_app({"TESTING": True, "DISABLE_AUTH": False})
        response = app.test_client().get("/logs/")
        self.assertEqual(response.status_code, 302)


if __name__ == "__main__":
    unittest.main()
