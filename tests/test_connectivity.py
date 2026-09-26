"""Tests for the opt-in connectivity diagnostics ported into Flask.

The probe layer is mocked: no sockets, TLS handshakes, or DNS lookups run
here. The store's config/locking and the API's auth + validation are the
parts under test.
"""
import os
import tempfile
import unittest
from unittest.mock import patch

from system_manager import create_app


def make_app(auth_enabled=True):
    directory = tempfile.mkdtemp(prefix="sm-connectivity-test-")
    return create_app({"DISABLE_AUTH": 0 if auth_enabled else 1,
                       "TESTING": True,
                       "AUDIT_PATH": os.path.join(directory, "actions.db")})


def fake_checks(config):
    """A successful scan, without touching the network."""
    destination = config["destination"]
    return {
        "gateway": {"state": "observed", "value": {"value": "192.168.1.1", "device": "eth0", "family": "IPv4"}},
        "gateway_reachable": {"state": "observed", "value": {"host": "192.168.1.1", "method": "udp_connect"}},
        "dns_configured": {"state": "observed", "value": ["1.1.1.1"]},
        "dns_reachable": {"state": "observed", "value": {"host": "1.1.1.1", "method": "udp_connect"}},
        "dns_resolution": {"state": "observed", "value": {"name": destination["host"], "addresses": ["93.184.216.34"]}},
        "https": {"state": "observed", "value": {"host": destination["host"], "port": destination["port"], "transport": "tls"}},
    }


class ConnectivityAuthTests(unittest.TestCase):
    def test_endpoints_require_session_when_auth_enabled(self):
        client = make_app().test_client()
        self.assertEqual(client.get("/api/connectivity").status_code, 401)
        self.assertEqual(client.post("/api/connectivity", json={"enabled": True}).status_code, 401)


class ConnectivityApiTests(unittest.TestCase):
    def setUp(self):
        self.app = make_app(auth_enabled=False)
        self.client = self.app.test_client()

    def test_defaults_to_disabled_and_untested(self):
        payload = self.client.get("/api/connectivity").get_json()
        self.assertFalse(payload["enabled"])
        self.assertEqual(payload["status"], "not tested")
        self.assertEqual(payload["checks"], {})

    def test_enable_records_endpoint_and_waits_for_first_scan(self):
        response = self.client.post("/api/connectivity", json={
            "enabled": True, "endpoints": ["https://example.com/"]})
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["enabled"])
        self.assertEqual(payload["destination"]["host"], "example.com")
        self.assertIsNone(payload["last_run"])

    def test_rejects_non_https_query_and_credential_endpoints(self):
        for url in ["http://example.com/", "https://example.com/?a=1", "https://u:p@example.com/"]:
            with self.subTest(url=url):
                response = self.client.post("/api/connectivity", json={
                    "enabled": True, "endpoints": [url]})
                self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.post("/api/connectivity", json={
            "enabled": True, "endpoints": []}).status_code, 400)
        self.assertEqual(self.client.post("/api/connectivity", json={
            "enabled": True,
            "endpoints": [f"https://h{i}.example.com/" for i in range(4)]}).status_code, 400)
        self.assertEqual(self.client.post("/api/connectivity", json={"enabled": "yes"}).status_code, 400)

    def test_disable_clears_checks_and_restores_off_explanation(self):
        self.client.post("/api/connectivity", json={"enabled": True, "endpoints": ["https://example.com/"]})
        with patch("system_manager.connectivity._checks", return_value=fake_checks(
                self.app.config["CONNECTIVITY"].config)):
            self.client.get("/api/status")
        self.assertTrue(self.client.get("/api/connectivity").get_json()["checks"])
        response = self.client.post("/api/connectivity", json={"enabled": False, "endpoints": ["https://example.com/"]})
        payload = response.get_json()
        self.assertFalse(payload["enabled"])
        self.assertEqual(payload["checks"], {})
        self.assertIn("off", payload["explanations"][0].lower())

    def test_disable_succeeds_with_a_missing_or_invalid_endpoint(self):
        self.client.post("/api/connectivity", json={"enabled": True, "endpoints": ["https://example.com/"]})
        for body in [{"enabled": False}, {"enabled": False, "endpoints": []},
                     {"enabled": False, "endpoints": ["http://not-allowed.example.com/"]}]:
            with self.subTest(body=body):
                response = self.client.post("/api/connectivity", json=body)
                self.assertEqual(response.status_code, 200)
                self.assertFalse(response.get_json()["enabled"])

    def test_enabled_must_be_a_boolean(self):
        for body in [{"endpoints": ["https://example.com/"]}, {"enabled": "yes"}]:
            with self.subTest(body=body):
                self.assertEqual(self.client.post("/api/connectivity", json=body).status_code, 400)

    def test_status_includes_connectivity_fields_after_scan(self):
        self.client.post("/api/connectivity", json={"enabled": True, "endpoints": ["https://example.com/"]})
        with patch("system_manager.connectivity._checks", return_value=fake_checks(
                self.app.config["CONNECTIVITY"].config)) as checks:
            payload = self.client.get("/api/status").get_json()
        checks.assert_called_once()
        self.assertEqual(payload["state"], "observed")
        self.assertTrue(payload["connectivity"]["enabled"])
        self.assertEqual(payload["checks"]["https"]["state"], "observed")
        self.assertEqual(payload["explanations"],
                         ["TLS connection to example.com succeeded. Internet access works for this endpoint."])

    def test_scan_is_reused_within_the_connect_interval(self):
        self.client.post("/api/connectivity", json={"enabled": True, "endpoints": ["https://example.com/"]})
        store = self.app.config["CONNECTIVITY"]
        with patch("system_manager.connectivity._checks", return_value=fake_checks(store.config)) as checks:
            self.client.get("/api/status")
            self.client.get("/api/status")
            self.assertEqual(checks.call_count, 1)
            # Once the interval has elapsed the scan runs again.
            store.clock = lambda: store.config["last_run"] + 60
            self.client.get("/api/status")
        self.assertEqual(checks.call_count, 2)

    def test_disabled_store_never_scans(self):
        with patch("system_manager.connectivity._checks") as checks:
            self.client.get("/api/status")
        checks.assert_not_called()


class StoreSettingsTests(unittest.TestCase):
    def test_last_run_records_the_clock_after_a_scan(self):
        app = make_app(auth_enabled=False)
        client = app.test_client()
        store = app.config["CONNECTIVITY"]
        client.post("/api/connectivity", json={"enabled": True, "endpoints": ["https://example.com/"]})
        store.clock = lambda: 1234.0
        with patch("system_manager.connectivity._checks", return_value=fake_checks(store.config)):
            client.get("/api/status")
        self.assertEqual(store.config["last_run"], 1234.0)
        self.assertEqual(store.config["status"], "reachable")


if __name__ == "__main__":
    unittest.main()
