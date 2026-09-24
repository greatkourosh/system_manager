"""Tests for the System Manager Flask app (system_manager package).

Covers the auth-enabled API surface and the approved-actions flow that were
ported from the standalone ``server.py`` panel. The pure ``server.py``
collectors/actions still live in ``test_server.py``.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from system_manager import auth, create_app


def make_app(auth_enabled=True):
    directory = tempfile.mkdtemp(prefix="sm-flask-test-")
    app = create_app({"DISABLE_AUTH": 0 if auth_enabled else 1,
                      "TESTING": True,
                      "AUDIT_PATH": os.path.join(directory, "actions.db")})
    security = app.config["SECURITY"]
    return app, security, directory


class AuthApiTests(unittest.TestCase):
    def setUp(self):
        self.app, self.security, self.dir = make_app()
        self.client = self.app.test_client()
        self.security.credential = "correct-code"

    def test_status_requires_session_when_auth_enabled(self):
        response = self.client.get("/api/status")
        self.assertEqual(response.status_code, 401)

    def test_login_wrong_code_rejected(self):
        response = self.client.post("/api/login", json={"code": "wrong"})
        self.assertEqual(response.status_code, 401)

    def test_login_success_grants_session_and_locks_out_old_code(self):
        login = self.client.post("/api/login", json={"code": "correct-code"})
        self.assertEqual(login.status_code, 200)
        self.assertIn("sm_session", login.headers.get("Set-Cookie", ""))
        self.assertIn("HttpOnly", login.headers.get("Set-Cookie", ""))
        self.assertEqual(self.client.get("/api/status").status_code, 200)
        # code rotated after login
        self.assertNotEqual(self.security.credential, "correct-code")

    def test_auth_disabled_allows_status(self):
        app, security, _ = make_app(auth_enabled=False)
        client = app.test_client()
        self.assertEqual(client.get("/api/status").status_code, 200)
        self.assertTrue(app.config["DISABLE_AUTH"] is not None and app.config["DISABLE_AUTH"])


class ApprovalFlowTests(unittest.TestCase):
    def setUp(self):
        self.app, self.security, self.dir = make_app()
        self.client = self.app.test_client()
        self.security.credential = "code"
        self.client.post("/api/login", json={"code": "code"})

    def test_approve_blocks_critical_service(self):
        response = self.client.post("/api/approve", json={
            "action_type": "service_restart",
            "parameters": {"name": "systemd-journald.service"},
        })
        self.assertEqual(response.status_code, 400)
        self.assertIn("critical", response.get_json()["error"].lower())

    def test_approve_then_execute_writes_audit(self):
        approve = self.client.post("/api/approve", json={
            "action_type": "service_start",
            "parameters": {"name": "test.service"},
        })
        self.assertEqual(approve.status_code, 200)
        token = approve.get_json()["approval_token"]
        with patch("system_manager.auth.service_execute",
                   return_value={"code": 0, "stdout": "ok", "stderr": ""}), \
             patch("system_manager.auth.service_verify",
                   return_value=auth.observed({"active": True})):
            execute = self.client.post("/api/execute", json={"approval_token": token})
        self.assertEqual(execute.status_code, 200)
        self.assertEqual(execute.get_json()["outcome"], "success")
        audit = self.client.get("/api/audit").get_json()["actions"]
        self.assertEqual(len(audit), 1)
        self.assertEqual(json.loads(audit[0]["parameters"])["name"], "test.service")

    def test_execute_rejects_reused_token(self):
        approve = self.client.post("/api/approve", json={
            "action_type": "service_start",
            "parameters": {"name": "test.service"},
        })
        token = approve.get_json()["approval_token"]
        with patch("system_manager.auth.service_execute",
                   return_value={"code": 0, "stdout": "", "stderr": ""}):
            first = self.client.post("/api/execute", json={"approval_token": token})
            second = self.client.post("/api/execute", json={"approval_token": token})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 400)

    def test_services_endpoint_unavailable_without_user_bus(self):
        # No --user systemd bus on test/CI hosts; the endpoint must not crash.
        response = self.client.get("/api/services")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertIn(payload["state"], ("observed", "unavailable"))


if __name__ == "__main__":
    unittest.main()