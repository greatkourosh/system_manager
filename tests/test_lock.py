"""Tests for the app-wide lock on the module blueprints.

``/api/*`` endpoints have always checked the session themselves; these cover
the inventory and organizer blueprints, which are gated by a shared
``before_request`` hook so locked pages redirect to the dashboard and JSON
callers get a 401.
"""
import os
import re
import tempfile
import unittest

from system_manager import create_app
from system_manager.organizer import is_available as organizer_available


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


class OrganizerUrlTests(unittest.TestCase):
    """The organizer's pages are mounted under /organizer, so every link and
    fetch() has to carry the prefix. It used to be added by a rewriter in the
    proxy; the routes are a blueprint now, so a missed prefix is a 404 and a
    button that silently does nothing. These assert on the rendered output."""

    def setUp(self):
        self.app = make_app()
        self.client = self.app.test_client()
        self.client.post("/api/login", json={"code": "code"})

    def _html(self, path):
        return self.client.get(path).get_data(as_text=True)

    def test_nav_links_are_prefixed(self):
        html = self._html("/organizer/")
        self.assertIn('href="/organizer/scan"', html)
        self.assertIn('href="/organizer/videos"', html)

    def test_api_map_is_prefixed(self):
        html = self._html("/organizer/")
        urls = dict(re.findall(r"'(api/[\w/-]+)': \"([^\"]+)\"", html))
        self.assertTrue(urls, "base.html should render the U map of API endpoints")
        for endpoint, url in urls.items():
            with self.subTest(endpoint=endpoint):
                self.assertEqual(url, "/organizer/" + endpoint)

    def test_no_page_links_to_the_host_app_root(self):
        """The exact failure the old rewriter existed to prevent: an unprefixed
        fetch('/api/...') that hits the dashboard root and 404s."""
        allowed = re.compile(r"^/(organizer/|static/|api/|\?|#)")
        for page in ("/organizer/", "/organizer/music/tags", "/organizer/select",
                     "/organizer/videos", "/organizer/folders", "/organizer/skipped",
                     "/organizer/cleanup"):
            for attr, url in re.findall(r'\b(href|action|src)="(/[^"]*)"', self._html(page)):
                with self.subTest(page=page, attr=attr, url=url):
                    self.assertRegex(url, allowed)

    def test_every_page_renders(self):
        for page in ("", "scan", "music", "music/tags", "folders", "select",
                     "skipped", "cleanup", "programs", "videos"):
            with self.subTest(page=page):
                self.assertEqual(self.client.get(f"/organizer/{page}").status_code, 200)

    def test_api_map_is_defined_before_any_page_uses_it(self):
        """base.html's own <script> sits after {% block content %}, so a page's
        script inside that block would run first and see `U` undefined -- a
        ReferenceError that leaves every button on the page dead while the page
        itself still returns 200."""
        for page in ("music/tags", "folders", "select", "skipped", "videos"):
            html = self._html(f"/organizer/{page}")
            defined = html.find("const U = {")
            uses = [i for i in (html.find("fetch(U["), html.find("post(U["),
                                 html.find("subPost(U[")) if i != -1]
            with self.subTest(page=page):
                self.assertNotEqual(defined, -1, "base.html should render the U map")
                self.assertTrue(uses, f"/organizer/{page} should call the API")
                self.assertLess(defined, min(uses),
                                "U must be defined before any page script runs")

    def test_every_anchor_is_well_formed(self):
        """A rewrite pass once emitted href"url_for('x'") with no '=', which
        renders as a dead link and still returns 200."""
        for page in ("", "scan", "music", "music/tags", "folders", "select",
                     "skipped", "cleanup", "programs", "videos"):
            for attr, url in re.findall(r'\b(href|action|src)=(?:"([^"]*)")', self._html(f"/organizer/{page}")):
                with self.subTest(page=page, url=url):
                    self.assertNotIn("url_for", url, "unrendered url_for left in output")
                    self.assertTrue(url.startswith(("/", "http", "#")), f"bad {attr}: {url!r}")

    def test_api_calls_are_syntactically_valid(self):
        """fetch(U['x'], {...}) not fetch(U['x', {...}]) or U['x']] -- both parse
        as an array or a syntax error and break every button on the page."""
        for page in ("music/tags", "folders", "select", "skipped", "videos"):
            html = self._html(f"/organizer/{page}")
            with self.subTest(page=page):
                self.assertNotRegex(html, r"\b(?:fetch|post)\(U\['[^']*'(?=[,;])")
                self.assertNotRegex(html, r"U\['[^']*'\]\]")

    def test_every_api_key_used_is_defined(self):
        """A U['x'] typo reads as undefined and posts to 'undefined'.

        The key is compared verbatim, query string included. Stripping a '?'
        off the used key used to hide a real bug: the page looked up
        U['api/video/poster?id='] and got undefined, so every card fetched the
        bare endpoint and every poster 404'd."""
        for page in ("music/tags", "folders", "select", "skipped", "videos"):
            html = self._html(f"/organizer/{page}")
            defined = set(re.findall(r"'(api/[\w/?=-]+)':", html))
            used = set(re.findall(r"\b\w+\(U\['([^']+)'\]", html))
            with self.subTest(page=page):
                self.assertTrue(defined)
                self.assertEqual(used - defined, set())

    def test_no_api_literal_reaches_any_call(self):
        """Matches any identifier called with a bare '/api/...', not just
        fetch() and post(). subPost() was one: it takes the URL as an argument,
        so a rewrite pass keyed on a fixed callee list misses it and the ＋sub
        button posts to the dashboard root and 404s."""
        for page in ("music/tags", "folders", "select", "skipped", "videos"):
            for callee, path in re.findall(r"\b(\w+)\('(/api/[^']*)'", self._html(f"/organizer/{page}")):
                with self.subTest(page=page, callee=callee):
                    self.fail(f"unprefixed {callee}('{path}') — use U['{path.lstrip('/')}'] instead")

    def test_unused_rewriter_helpers_are_gone(self):
        """The wrapper case: a helper wrapping fetch() used to be missed by a
        fixed list of callee names, leaving the ＋sub button posting to the host
        app's root and 404ing. The U map has no such blind spot -- every API
        path a page can call is a url_for result, so this just pins the
        ＋sub button specifically."""
        html = self._html("/organizer/videos")
        self.assertIn("'api/subtitles/queue': \"/organizer/api/subtitles/queue\"", html)
        self.assertNotIn("fetch('/api/", html)


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
