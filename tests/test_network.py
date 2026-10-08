"""The Network module's page and its JSON API.

The template bug these pin: the row ids live on the <tbody>, and the script
has to ask for the tbody itself. Asking for "#ifaces tbody" finds nothing, and
because the failure lands in a .catch() it renders as an error banner on an
otherwise valid page -- nothing in the HTML looks wrong.
"""
import json
import os
import re
import tempfile
import unittest

from system_manager import create_app


def make_app():
    directory = tempfile.mkdtemp(prefix="sm-network-test-")
    return create_app({"DISABLE_AUTH": 1, "TESTING": True,
                       "AUDIT_PATH": os.path.join(directory, "actions.db")})


class NetworkPageTests(unittest.TestCase):
    def setUp(self):
        self.app = make_app()
        self.client = self.app.test_client()

    def test_dashboard_navigates_to_the_page_not_the_json(self):
        """modules() names the page endpoint; pointing it at /summary renders raw JSON."""
        body = self.client.get("/").get_data(as_text=True)
        self.assertIn('href="/network/"', body)
        self.assertNotIn('href="/network/summary"', body)

    def test_modules_page_opens_the_page(self):
        body = self.client.get("/modules").get_data(as_text=True)
        self.assertIn('href="/network/', body)
        self.assertNotIn('href="/network/summary', body)

    def test_page_renders_through_base_template(self):
        page = self.client.get("/network/").get_data(as_text=True)
        # Chrome, theme toggle and the nav all come from base.html.
        self.assertIn("themeToggle", page)
        self.assertIn("app.css", page)
        # The stylesheet the module used to name does not exist in static/.
        self.assertNotIn("style.css", page)

    def test_every_scripted_element_id_exists_in_the_markup(self):
        """getElementById targets must be present, or the page renders its catch()."""
        page = self.client.get("/network/").get_data(as_text=True)
        script = re.search(r"<script>([\s\S]*?)</script>", page).group(1)
        wanted = set(re.findall(r"getElementById\('([^']+)'\)", script))
        self.assertTrue(wanted, "no script found to check")
        for element_id in wanted:
            self.assertIn(f'id="{element_id}"', page,
                          f'script needs #{element_id}, markup does not define it')

    def test_row_ids_sit_on_the_tbody_the_script_writes_to(self):
        page = self.client.get("/network/").get_data(as_text=True)
        for table_id in ("ifaces", "routes", "ports", "ct"):
            self.assertIn(f'<tbody id="{table_id}">', page)
        # "#<id> tbody" would silently resolve to nothing: the id *is* the tbody.
        self.assertNotIn(" tbody'", page)

    def test_script_derives_its_api_prefix_from_the_page_path(self):
        page = self.client.get("/network/").get_data(as_text=True)
        self.assertIn("location.pathname.replace", page)


class NetworkApiTests(unittest.TestCase):
    """Only the shape is pinned, not this host's values."""

    def setUp(self):
        self.app = make_app()
        self.client = self.app.test_client()

    def _get(self, name):
        response = self.client.get(f"/network/{name}")
        self.assertEqual(response.status_code, 200, f"/network/{name} returned {response.status_code}")
        return json.loads(response.get_data(as_text=True))

    def test_summary_has_the_keys_the_page_reads(self):
        summary = self._get("summary")
        self.assertEqual(sorted(summary["hostname"]), ["domain", "hostname"])
        for key in ("interfaces", "routes", "listening_ports", "dns_servers"):
            self.assertIn(key, summary)

    def test_listening_ports_parse_every_row(self):
        """ss renders IPv6 as [addr]%iface:port, which is what broke the parse."""
        for port in self._get("ports"):
            self.assertIsInstance(port["local_port"], int, f'bad port in {port}')
            self.assertNotIn("]", port["local_ip"], f'bracket left in {port}')
            self.assertNotIn("]:", port["local_ip"], f'port glued to the address in {port}')

    def test_list_rows_have_the_keys_the_page_reads(self):
        for row in self._get("interfaces"):
            for key in ("name", "state", "mac", "ipv4", "ipv6", "mtu"):
                self.assertIn(key, row)
        # A connected route has no gateway and the default one has no dst, so
        # only these two are guaranteed; the page already tolerates both absent.
        for row in self._get("routes"):
            for key in ("dev", "protocol"):
                self.assertIn(key, row)

    def test_dns_is_an_object_with_servers(self):
        self.assertIsInstance(self._get("dns")["servers"], list)

    def test_api_requires_a_session_when_auth_is_on(self):
        directory = tempfile.mkdtemp(prefix="sm-network-auth-")
        app = create_app({"DISABLE_AUTH": 0, "TESTING": True,
                          "AUDIT_PATH": os.path.join(directory, "actions.db")})
        client = app.test_client()
        for name in ("summary", "interfaces", "routes", "dns", "ports", "conntrack", ""):
            self.assertNotEqual(client.get(f"/network/{name}").status_code, 200,
                                f"/network/{name} served without a session")


if __name__ == "__main__":
    unittest.main()