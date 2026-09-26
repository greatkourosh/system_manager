"""Tests for the app-wide lock on the module blueprints.

``/api/*`` endpoints have always checked the session themselves; these cover
the inventory and organizer blueprints, which are gated by a shared
``before_request`` hook so locked pages redirect to the dashboard and JSON
callers get a 401.
"""
import os
import tempfile
import unittest

from system_manager import create_app
from system_manager.organizer import is_available as organizer_available
from system_manager.organizer import _rewrite_absolute_urls


def make_app(auth_enabled=True):
    directory = tempfile.mkdtemp(prefix="sm-lock-test-")
    app = create_app({"DISABLE_AUTH": 0 if auth_enabled else 1,
                      "TESTING": True,
                      "AUDIT_PATH": os.path.join(directory, "actions.db"),
                      "INVENTORY_DATA_DIR": os.path.join(directory, "inv.db")})
    app.config["SECURITY"].credential = "code"
    return app


class LockedModuleTests(unittest.TestCase):
    def setUp(self):
        self.app = make_app()
        self.client = self.app.test_client()

    def test_inventory_pages_redirect_to_dashboard_when_locked(self):
        for path in ["/inventory/", "/inventory/items", "/inventory/builds",
                     "/inventory/topology", "/inventory/export"]:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 302)
                self.assertTrue(response.headers["Location"].endswith("/"))

    def test_inventory_writes_are_blocked_when_locked(self):
        for path in ["/inventory/items", "/inventory/builds"]:
            with self.subTest(path=path):
                self.assertEqual(self.client.post(path, json={"name": "x"}).status_code, 302)
        item = self.client.post("/inventory/items", json={"name": "x"})
        self.assertNotEqual(item.status_code, 201)
        listed = self.client.get("/inventory/items",
                                 headers={"Accept": "application/json"})
        self.assertEqual(listed.status_code, 401)
        self.assertIn("error", listed.get_json())

    def test_json_callers_get_401_not_a_redirect(self):
        response = self.client.get("/inventory/items",
                                   headers={"Accept": "application/json"})
        self.assertEqual(response.status_code, 401)
        self.assertIn("error", response.get_json())

    @unittest.skipUnless(organizer_available(), "folder_organizer checkout not present")
    def test_organizer_redirects_when_locked(self):
        self.assertEqual(self.client.get("/organizer/").status_code, 302)

    def test_locked_inventory_has_no_data_leak_after_redirect(self):
        # A locked create must not persist anything a later session could read.
        self.client.post("/inventory/items", json={"name": "should-not-exist"})
        self.client.post("/api/login", json={"code": "code"})
        listed = self.client.get("/inventory/items",
                                 headers={"Accept": "application/json"}).get_json()
        self.assertEqual(listed["total"], 0)


class UnlockedModuleTests(unittest.TestCase):
    def setUp(self):
        self.app = make_app()
        self.client = self.app.test_client()
        self.client.post("/api/login", json={"code": "code"})

    def test_inventory_is_reachable_after_login(self):
        created = self.client.post("/inventory/items", json={"name": "Monitor"})
        self.assertEqual(created.status_code, 201)
        self.assertEqual(self.client.get("/inventory/").status_code, 200)
        listed = self.client.get("/inventory/items",
                                 headers={"Accept": "application/json"})
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.get_json()["total"], 1)

    @unittest.skipUnless(organizer_available(), "folder_organizer checkout not present")
    def test_organizer_is_reachable_after_login(self):
        self.assertIn(self.client.get("/organizer/").status_code, (200, 303, 307))


class OrganizerUrlRewriteTests(unittest.TestCase):
    """The organizer's templates hardcode absolute paths, so the proxy rewrites
    them on the way out. Without this, its fetch() calls hit the host app's
    root and 404, and the page's buttons silently do nothing."""

    def test_href_is_prefixed(self):
        self.assertIn(b'href="/organizer/scan"',
                      _rewrite_absolute_urls(b'<a href="/scan">x</a>'))

    def test_fetch_call_is_prefixed(self):
        self.assertIn(b"fetch('/organizer/api/skip-rules'",
                      _rewrite_absolute_urls(b"fetch('/api/skip-rules', {method:'POST'})"))

    def test_local_post_helper_is_prefixed(self):
        self.assertIn(b"post('/organizer/api/tags/bulk'",
                      _rewrite_absolute_urls(b"post('/api/tags/bulk', {mode:'m'})"))

    def test_window_open_navigation_is_prefixed(self):
        self.assertIn(b"open('/organizer/cleanup'",
                      _rewrite_absolute_urls(b"window.open('/cleanup','_blank')"))

    def test_already_prefixed_url_is_not_doubled(self):
        self.assertEqual(_rewrite_absolute_urls(b"fetch('/organizer/api/x')"),
                         b"fetch('/organizer/api/x')")

    def test_absolute_external_url_is_left_alone(self):
        self.assertEqual(
            _rewrite_absolute_urls(b'<a href="https://cdn.example/x.css">x</a>'),
            b'<a href="https://cdn.example/x.css">x</a>')


class AuthDisabledTests(unittest.TestCase):
    def test_modules_stay_open_when_auth_is_disabled(self):
        app = make_app(auth_enabled=False)
        client = app.test_client()
        self.assertEqual(client.post("/inventory/items",
                                     json={"name": "Monitor"}).status_code, 201)
        self.assertEqual(client.get("/inventory/").status_code, 200)


class UnlockSurfaceTests(unittest.TestCase):
    def test_login_and_assets_are_never_gated(self):
        app = make_app()
        client = app.test_client()
        self.assertEqual(client.get("/").status_code, 200)
        self.assertEqual(client.get("/health").status_code, 200)
        self.assertEqual(client.get("/static/app.js").status_code, 200)
        self.assertEqual(client.post("/api/login", json={"code": "code"}).status_code, 200)


if __name__ == "__main__":
    unittest.main()
