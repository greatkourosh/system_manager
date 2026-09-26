import concurrent.futures
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import server


def fixture():
    return {
        "observed_at": time.time(),
        "system": {"os": server.observed("Fixture Linux"), "kernel": server.observed("test"),
                   "cpu_count": server.observed(4), "load": server.observed([1., 2., 3.]),
                   "uptime": server.observed(3600)},
        "hardware": {"Manufacturer": server.unavailable(), "Processor": server.observed("Test CPU")},
        "memory": {key: server.observed(value) for key, value in
                   {"MemTotal": 1000, "MemAvailable": 50, "SwapTotal": 0, "SwapFree": 0}.items()},
        "filesystem": server.observed({"total": 1000, "available": 50}),
        "interfaces": server.observed([{"ifname": "<img src=x onerror=alert(1)>",
                                         "operstate": "UNKNOWN", "addr_info": []}]),
        "routes": {"IPv4": server.observed([]), "IPv6": server.observed([])},
        "internet": {"state": "not tested"}, "suggestions": [],
    }


class CollectorTests(unittest.TestCase):
    def test_missing_and_malformed_memory_is_unavailable_not_zero(self):
        with patch("server.read_text", return_value="MemTotal: 100 kB\nMemAvailable: wrong\nSwapTotal: 0 kB\nSwapFree: -1 kB"):
            result = server.memory_reading()
        self.assertEqual(result["MemTotal"]["value"], 102400)
        self.assertEqual(result["MemAvailable"]["state"], "unavailable")
        self.assertEqual(result["SwapTotal"]["value"], 0)
        self.assertEqual(result["SwapFree"]["state"], "unavailable")

    def test_file_errors_and_bad_uptime(self):
        with patch("pathlib.Path.open", side_effect=PermissionError):
            self.assertIsNone(server.read_text("/not-allowed"))
        for value in (None, "", "no", "-1", "nan", "inf"):
            with self.subTest(value=value), patch("server.read_text", return_value=value):
                self.assertEqual(server.uptime_reading()["state"], "unavailable")
        with patch("server.os.statvfs", side_effect=PermissionError):
            self.assertEqual(server.filesystem_reading()["state"], "unavailable")

    def test_read_is_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "large"
            path.write_text("a" * 100000)
            self.assertEqual(len(server.read_text(path)), 65536)

    def test_command_contract(self):
        cases = [
            ("print('[{}]')", "observed"),
            ("print('bad')", "unavailable"),
            ("print('{}')", "unavailable"),
            ("print('[1]')", "unavailable"),
            ("raise SystemExit(2)", "unavailable"),
            ("print('x'*10000)", "unavailable"),
            ("import time; time.sleep(1)", "unavailable"),
        ]
        for code, expected in cases:
            with self.subTest(code=code), patch.dict(server.COMMANDS, {"test": (sys.executable, "-c", code)}):
                result = server.command("test", timeout=.2, limit=1000)
                self.assertEqual(result["state"], expected)
        with patch.dict(server.COMMANDS, {"test": ("/missing-command",)}):
            self.assertEqual(server.command("test")["state"], "unavailable")

    def test_warnings_do_not_treat_unknown_as_healthy_or_failed(self):
        data = fixture()
        self.assertEqual(len(server.suggestions(data)), 3)
        data["memory"]["MemAvailable"] = server.unavailable()
        data["filesystem"] = server.unavailable()
        data["routes"]["IPv6"] = server.unavailable()
        self.assertEqual(server.suggestions(data), [])

    def test_snapshot_only_invokes_fixed_local_commands(self):
        with patch("server.command", return_value=server.unavailable()) as command:
            result = server.snapshot()
        self.assertEqual([call.args[0] for call in command.call_args_list], ["interfaces", "routes4", "routes6"])
        self.assertEqual(result["internet"]["state"], "not tested")
        json.dumps(result, allow_nan=False)


class CacheTests(unittest.TestCase):
    def test_refresh_is_coalesced_across_threads_and_recovers(self):
        now = [0]
        calls = []
        def collect():
            calls.append(1)
            return fixture()
        cache = server.SnapshotCache(collect, lambda: now[0])
        with concurrent.futures.ThreadPoolExecutor(8) as executor:
            results = list(executor.map(lambda _: cache.get(), range(20)))
        self.assertEqual(len(calls), 1)
        self.assertTrue(all(result["state"] == "observed" for result in results))
        now[0] = 10
        cache.collector = lambda: (_ for _ in ()).throw(OSError())
        self.assertEqual(cache.get()["state"], "stale")
        now[0] = 11
        cache.collector = collect
        self.assertEqual(cache.get()["state"], "stale")
        now[0] = 20
        self.assertEqual(cache.get()["state"], "observed")
        self.assertEqual(len(calls), 2)

    def test_initial_error_and_old_snapshot(self):
        cache = server.SnapshotCache(lambda: (_ for _ in ()).throw(OSError()))
        self.assertEqual(cache.get()["state"], "unavailable")
        data = fixture()
        data["observed_at"] -= 60
        self.assertEqual(server.SnapshotCache(lambda: data).get()["state"], "stale")


class ConnectivityTests(unittest.TestCase):
    def test_valid_endpoint_accepts_https_and_rejects_invalid(self):
        good = ["https://example.com/", "https://api.example.org:8443/path", "https://host.sub.domain.tld/"]
        for url in good:
            with self.subTest(url=url):
                self.assertIsNotNone(server.valid_endpoint(url))
        bad = ["http://example.com/", "https://example.com?query", "https://user:pass@example.com/",
               "https://", "https://toolong" + "x" * 200, "https://bad/name\n",
               "https://example.com#frag", "https://ex ample.com/", "ftp://example.com/"]
        for url in bad:
            with self.subTest(url=url):
                self.assertIsNone(server.valid_endpoint(url))

    def test_nameservers_from_resolv_conf(self):
        with patch("server.read_text", return_value="nameserver 1.1.1.1\nnameserver 8.8.8.8\n"):
            result = server.nameservers()
        self.assertEqual(result["state"], "observed")
        self.assertEqual(result["value"], ["1.1.1.1", "8.8.8.8"])
        with patch("server.read_text", return_value=""):
            self.assertEqual(server.nameservers()["state"], "unavailable")

    def test_gateway_detection(self):
        routes = {"IPv4": server.observed([{"gateway": "192.168.1.1", "dev": "eth0"}]), "IPv6": server.observed([])}
        self.assertEqual(server.default_gateway(routes["IPv4"], routes["IPv6"]), ("IPv4", "192.168.1.1", "eth0"))
        routes = {"IPv4": server.observed([]), "IPv6": server.observed([{"gateway": "::1", "dev": "lo"}])}
        self.assertEqual(server.default_gateway(routes["IPv4"], routes["IPv6"]), ("IPv6", "::1", "lo"))


class ActionTests(unittest.TestCase):
    def test_critical_services_excluded(self):
        self.assertIn("systemd-journald.service", server.CRITICAL_SERVICES)
        self.assertIn("network-manager.service", server.CRITICAL_SERVICES)

    def test_user_services_filtering(self):
        mock_output = "test.service loaded active running Test Service\nsystemd-journald.service loaded active running System Journal"
        with patch("subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = mock_output
            run.return_value.stderr = ""
            result = server.user_services()
        self.assertEqual(result["state"], "observed")
        self.assertEqual(len(result["value"]), 1)
        self.assertEqual(result["value"][0]["name"], "test.service")

    def test_service_preconditions_blocks_critical(self):
        ok, reason = server.service_preconditions("systemd-journald.service")
        self.assertFalse(ok)
        self.assertIn("critical", reason.lower())

    def test_nm_profiles_parsing(self):
        mock_output = "HomeWifi:wireless:wlan0\nWorkVPN:vpn:\nWired:ethernet:eth0\nBridge:bridge:br0"
        with patch("subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = mock_output
            run.return_value.stderr = ""
            result = server.nm_profiles()
        self.assertEqual(result["state"], "observed")
        self.assertEqual(len(result["value"]), 4)

    def test_action_audit_records_and_retrieves(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            audit = server.ActionAudit(Path(directory) / "test.db")
            token_hash = "test_hash"
            audit.record(token_hash, "service_restart", {"name": "test.service"}, {"name": "test.service"}, {"code": 0})
            audit.record(token_hash, "nm_activate", {"name": "VPN"}, {"name": "VPN", "checkpoint": "cp1"}, {"code": 1})
            actions = audit.recent(10)
        self.assertEqual(len(actions), 2)
        self.assertEqual(actions[0]["action_type"], "nm_activate")
        self.assertEqual(json.loads(actions[0]["parameters"])["name"], "VPN")

    def test_approval_issue_and_consume(self):
        approval = server.Approval()
        token = approval.issue("hash", "service_restart", {"name": "test.service"}, {"name": "test.service"})
        consumed = approval.consume(token)
        self.assertEqual(consumed["action_type"], "service_restart")
        self.assertEqual(consumed["parameters"]["name"], "test.service")
        self.assertIsNone(approval.consume(token))
        self.assertIsNone(approval.consume("invalid"))

    def test_approval_expiry(self):
        approval = server.Approval()
        token = approval.issue("hash", "service_restart", {"name": "test.service"}, {"name": "test.service"}, ttl=-1)
        self.assertIsNone(approval.consume(token))

    def test_approval_single_use(self):
        approval = server.Approval()
        token = approval.issue("hash", "service_restart", {"name": "test.service"}, {"name": "test.service"})
        self.assertIsNotNone(approval.consume(token))
        self.assertIsNone(approval.consume(token))


if __name__ == "__main__":
    unittest.main()
