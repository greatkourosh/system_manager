"""The Network module's page, its JSON API, and the procfs parsers behind it.

The template bug these pin: the row ids live on the <tbody>, and the script
has to ask for the tbody itself. Asking for "#ifaces tbody" finds nothing, and
because the failure lands in a .catch() it renders as an error banner on an
otherwise valid page -- nothing in the HTML looks wrong.

The parser fixtures are copied verbatim out of this host's /proc/1/net, which
is where the module reads when it runs in the container. Two of them are easy
to get subtly wrong and are pinned byte-for-byte here:

- IPv6 is stored little-endian in tcp6 but in plain network order in
  if_inet6 and ipv6_route. Decoding both the same way yields addresses like
  "0:80fe::ffd9:7cbc:6190:bafe" instead of "fe80::bc7c:d9ff:feba:9061".
- fib_trie lists every address twice, marks subnet addresses LOCAL under a
  /8 or /31, and has no interface column at all, so an address is attributed
  to an interface by longest-prefix match against the route table.
"""
import json
import os
import re
import shutil
import socket
import tempfile
import unittest

from system_manager import create_app
from system_manager.network_manager import hostnet


def make_app():
    directory = tempfile.mkdtemp(prefix="sm-network-test-")
    return create_app({"DISABLE_AUTH": 1, "TESTING": True,
                       "AUDIT_PATH": os.path.join(directory, "actions.db")})


# --- Verbatim procfs fixtures, trimmed to what each test needs ---------------

DEV = """Inter-|   Receive                                                |  Transmit
 face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed
    lo: 21777322283 9123943    0    0    0     0          0         0 21777322283 9123943    0    0    0     0       0          0
  eth0:     240       2    0    0    0     0          0         0        42       1    0    0    0     0       0          0
"""

# eno1 is the default route; the two 172.x networks belong to other devices.
ROUTE = """Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT
eno1\t00000000\t0101A8C0\t0003\t0\t0\t100\t00000000\t0\t0\t0
vmnet8\t00B410AC\t00000000\t0001\t0\t0\t0\t00FFFFFF\t0\t0\t0
docker0\t000011AC\t00000000\t0001\t0\t0\t0\t0000FFFF\t0\t0\t0
"""

FIB_TRIE = """Main:
  +-- 0.0.0.0/0 3 0 4
     +-- 127.0.0.0/8 2 0 2
        +-- 127.0.0.0/31 1 0 0
           |-- 127.0.0.0
              /8 host LOCAL
           |-- 127.0.0.1
              /32 host LOCAL
     +-- 172.17.0.0/16 5 0 9
        |-- 172.17.0.1
           /32 host LOCAL
           |-- 172.17.0.1
              /32 host LOCAL
"""

IF_INET6 = """fe800000000000006cabbffffe14856f 0d 40 20 80 br-983782549ddc
00000000000000000000000000000001 01 80 10 80 lo
"""

IPV6_ROUTE = """fe800000000000000000000000000000 40 00000000000000000000000000000000 00 00000000000000000000000000000000 00000100 00000001 00000000 00000001 veth73e3547
"""

# 0A is LISTEN. The second row is 01, ESTABLISHED, and must not be reported.
TCP = """  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode
   0: 0100007F:9161 00000000:0000 0A 00000000:00000000 00:00000000 00000000  1000        0 95532 1 0000000000000000 100 0 0 10 0
   1: 0100007F:13B0 00000000:0000 01 00000000:00000000 00:00000000 00000000     0        0 4533260 1 0000000000000000 100 0 0 10 0
"""

TCP6 = """  sl  local_address                         remote_address                        st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode
   0: 0000000000000000FFFF00000100007F:90A7 00000000000000000000000000000000:0000 0A 00000000:00000000 00:00000000 00000000  1000        0 55743 1 0000000000000000 100 0 0 10 0
"""

UDP = """  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode ref pointer drops
  1234: 00000000:14E9 00000000:0000 07 00000000:00000000 00:00000000 00000000     0        0 24527 2 0000000000000000 0
"""

RESOLV_CONF = """# a comment
nameserver 127.0.0.1
nameserver 8.8.8.8
options edns0
search .
"""


class FakeHost:
    """A HOST_ROOT tree holding procfs fixtures, torn down afterwards."""

    def __init__(self, **files):
        self.root = tempfile.mkdtemp(prefix="sm-fakehost-")
        os.makedirs(os.path.join(self.root, "proc/1/net"))
        os.makedirs(os.path.join(self.root, "etc"))
        for name, content in files.items():
            with open(os.path.join(self.root, "proc/1/net", name), "w") as handle:
                handle.write(content)

    def resolv(self, text):
        with open(os.path.join(self.root, "etc/resolv.conf"), "w") as handle:
            handle.write(text)

    def interface(self, name, mac="00:11:22:33:44:55", mtu="1500", state="up"):
        base = os.path.join(self.root, "sys/class/net", name)
        os.makedirs(base, exist_ok=True)
        for attribute, value in (("address", mac), ("mtu", mtu), ("operstate", state)):
            with open(os.path.join(base, attribute), "w") as handle:
                handle.write(value + "\n")

    def process(self, pid, fds=(), exe=None):
        """A /proc/<pid> holding fds, each a (fd number, link target)."""
        base = os.path.join(self.root, "proc", str(pid), "fd")
        os.makedirs(base, exist_ok=True)
        for fd, target in fds:
            link = os.path.join(base, str(fd))
            os.symlink(target, link)
        if exe:
            os.symlink(exe, os.path.join(self.root, "proc", str(pid), "exe"))

    def __enter__(self):
        self.previous = os.environ.get("HOST_ROOT")
        os.environ["HOST_ROOT"] = self.root
        return self

    def __exit__(self, *exc):
        if self.previous is None:
            os.environ.pop("HOST_ROOT", None)
        else:
            os.environ["HOST_ROOT"] = self.previous
        shutil.rmtree(self.root, ignore_errors=True)


class HexDecodeTests(unittest.TestCase):
    def test_ipv4_is_little_endian(self):
        self.assertEqual(hostnet.decode_ipv4("0100007F"), "127.0.0.1")
        self.assertEqual(hostnet.decode_ipv4("00000000"), "0.0.0.0")
        self.assertEqual(hostnet.decode_ipv4("0101A8C0"), "192.168.1.1")

    def test_ipv6_sockets_are_little_endian_words(self):
        # systemd-resolved's stub, exactly as the kernel writes it.
        self.assertEqual(hostnet.decode_ipv6("0000000000000000FFFF00000100007F"), "::ffff:127.0.0.1")
        self.assertEqual(hostnet.decode_ipv6("00000000000000000000000000000000"), "::")

    def test_if_inet6_is_network_order_not_little_endian(self):
        """The two orderings disagree; using the wrong one corrupts every address."""
        self.assertEqual(
            hostnet.decode_ipv6_network("fe800000000000006cabbffffe14856f"),
            "fe80::6cab:bfff:fe14:856f")
        # Same input, the other decoder, would give something else entirely.
        self.assertNotEqual(hostnet.decode_ipv6("fe800000000000006cabbffffe14856f"),
                            hostnet.decode_ipv6_network("fe800000000000006cabbffffe14856f"))

    def test_malformed_hex_raises(self):
        for bad in ("", "xyz", "0100", "0" * 33):
            with self.assertRaises(ValueError):
                hostnet.decode_ipv4(bad)


class SocketStateTests(unittest.TestCase):
    def test_listen_and_established_differ(self):
        self.assertEqual(hostnet.socket_state_name("0A"), "LISTEN")
        self.assertEqual(hostnet.socket_state_name("01"), "ESTABLISHED")

    def test_unknown_state_passes_through(self):
        self.assertEqual(hostnet.socket_state_name("FF"), "FF")


class ListeningPortTests(unittest.TestCase):
    def test_only_listen_rows_are_reported(self):
        with FakeHost(tcp=TCP, udp=UDP) as host:
            tcp = [row for row in hostnet.listening_ports() if row["proto"] == "TCP"]
            self.assertEqual([row["local_port"] for row in tcp], [0x9161],
                             "the ESTABLISHED row must not be listed")

    def test_addresses_and_ports_decode(self):
        with FakeHost(tcp=TCP):
            row = hostnet.listening_ports()[0]
            self.assertEqual(row["local_ip"], "127.0.0.1")
            self.assertEqual(row["local_port"], 0x9161)
            self.assertIsInstance(row["local_port"], int)

    def test_ipv6_socket_keeps_no_brackets_or_merged_port(self):
        with FakeHost(tcp6=TCP6):
            row = [r for r in hostnet.listening_ports() if r["proto"] == "TCP"][0]
            self.assertEqual(row["local_ip"], "::ffff:127.0.0.1")
            self.assertEqual(row["local_port"], 0x90A7)

    def test_udp_rows_have_no_state_field_so_all_are_included(self):
        with FakeHost(udp=UDP):
            udp = [row for row in hostnet.listening_ports() if row["proto"] == "UDP"]
            self.assertEqual([row["local_port"] for row in udp], [0x14E9])

    def test_unknown_owner_is_none_not_a_guess(self):
        with FakeHost(tcp=TCP):
            self.assertIsNone(hostnet.listening_ports()[0]["pid"])

    def test_socket_is_attributed_to_the_process_holding_its_inode(self):
        # TCP's LISTEN row has inode 95532. fd directories are readable only for
        # our own processes, so attribution is partial by design -- but it is
        # partial, not absent, and this pins the part that does work.
        with FakeHost(tcp=TCP) as host:
            host.process(4321, fds=[(7, "socket:[95532]")], exe="/usr/bin/clash")
            row = hostnet.listening_ports()[0]
        self.assertEqual(row["pid"], 4321)
        self.assertEqual(row["exe"], "/usr/bin/clash")

    def test_a_socket_no_fd_points_at_stays_unknown(self):
        with FakeHost(tcp=TCP) as host:
            host.process(4321, fds=[(7, "socket:[999999]")])
            row = hostnet.listening_ports()[0]
        self.assertIsNone(row["pid"])
        self.assertEqual(row["exe"], "")

    def test_exe_is_empty_when_the_process_is_gone_or_unreadable(self):
        # Attributing the pid does not imply reading its exe: the two are
        # separate permission checks, and a pid can exit between them.
        with FakeHost(tcp=TCP) as host:
            host.process(4321, fds=[(7, "socket:[95532]")])
            row = hostnet.listening_ports()[0]
        self.assertEqual(row["pid"], 4321)
        self.assertEqual(row["exe"], "")


class RouteTests(unittest.TestCase):
    def test_mask_becomes_a_prefix_length(self):
        # 00000000 -> /0, 0000FFFF -> /16, 00FFFFFF -> /24. The mask is a
        # little-endian word, so this is where a byte-order slip shows up.
        with FakeHost(route=ROUTE, fib_trie=FIB_TRIE):
            found = {row["dev"]: row for row in hostnet.routes() if row["family"] == "inet"}
        self.assertEqual(found["eno1"]["dst"], "0.0.0.0/0")
        self.assertEqual(found["vmnet8"]["dst"], "172.16.180.0/24")
        self.assertEqual(found["docker0"]["dst"], "172.17.0.0/16")

    def test_default_route_reports_its_gateway(self):
        with FakeHost(route=ROUTE):
            default = [r for r in hostnet.routes() if r["dst"] == "0.0.0.0/0"][0]
            self.assertEqual(default["gateway"], "192.168.1.1")
            self.assertEqual(default["dev"], "eno1")

    def test_connected_route_has_no_gateway(self):
        with FakeHost(route=ROUTE):
            connected = [r for r in hostnet.routes() if r["dst"] == "172.17.0.0/16"][0]
            self.assertEqual(connected["gateway"], "", "RTF_UP without RTF_GATEWAY has none")

    def test_ipv6_route_prefix_length_is_hex(self):
        with FakeHost(ipv6_route=IPV6_ROUTE):
            row = [r for r in hostnet.routes() if r["family"] == "inet6"][0]
            self.assertEqual(row["dst"], "fe80::/64")
            self.assertEqual(row["dev"], "veth73e3547")


class InterfaceTests(unittest.TestCase):
    def test_address_lands_on_the_interface_whose_route_contains_it(self):
        """Longest-prefix match: 172.17.0.1 is inside 172.17.0.0/16 *and* 0.0.0.0/0."""
        with FakeHost(route=ROUTE, fib_trie=FIB_TRIE) as host:
            host.interface("lo")
            host.interface("eno1")
            host.interface("docker0")
            host.interface("vmnet8")
            by_name = {i["name"]: i for i in hostnet.interfaces()}
        self.assertEqual(by_name["docker0"]["ipv4"], ["172.17.0.1"])
        self.assertEqual(by_name["vmnet8"]["ipv4"], [])
        self.assertEqual(by_name["lo"]["ipv4"], ["127.0.0.1"])

    def test_loopback_never_falls_through_to_the_default_route(self):
        """The local route table has no lo entry, so 127.0.0.1 needs saying."""
        with FakeHost(route=ROUTE, fib_trie=FIB_TRIE) as host:
            for name in ("lo", "eno1"):
                host.interface(name)
            by_name = {i["name"]: i for i in hostnet.interfaces()}
        self.assertEqual(by_name["lo"]["ipv4"], ["127.0.0.1"])
        self.assertEqual(by_name["eno1"]["ipv4"], [])

    def test_duplicate_fib_entries_collapse(self):
        # The fixture lists 172.17.0.1 twice; it must appear once.
        with FakeHost(route=ROUTE, fib_trie=FIB_TRIE) as host:
            host.interface("docker0")
            docker0 = [i for i in hostnet.interfaces() if i["name"] == "docker0"][0]
        self.assertEqual(docker0["ipv4"], ["172.17.0.1"])

    def test_subnet_addresses_are_not_reported_as_addresses(self):
        """fib_trie marks 127.0.0.0 LOCAL under a /8; only the /32 is real."""
        with FakeHost(route=ROUTE, fib_trie=FIB_TRIE) as host:
            host.interface("lo")
            loopback = [i for i in hostnet.interfaces() if i["name"] == "lo"][0]
        self.assertNotIn("127.0.0.0", loopback["ipv4"])

    def test_ipv6_address_keeps_its_interface(self):
        with FakeHost(route=ROUTE, fib_trie=FIB_TRIE, if_inet6=IF_INET6) as host:
            host.interface("lo")
            host.interface("br-983782549ddc")
            by_name = {i["name"]: i for i in hostnet.interfaces()}
        self.assertEqual(by_name["lo"]["ipv6"], ["::1"])
        self.assertEqual(by_name["br-983782549ddc"]["ipv6"], ["fe80::6cab:bfff:fe14:856f"])

    def test_mac_mtu_and_state_come_from_sysfs(self):
        with FakeHost(route=ROUTE, fib_trie=FIB_TRIE) as host:
            host.interface("eno1", mac="10:7c:61:79:15:3a", mtu="9000", state="up")
            eno1 = [i for i in hostnet.interfaces() if i["name"] == "eno1"][0]
        self.assertEqual(eno1["mac"], "10:7c:61:79:15:3a")
        self.assertEqual(eno1["mtu"], 9000)
        self.assertEqual(eno1["state"], "UP")

    def test_counters_parse_from_dev(self):
        with FakeHost(dev=DEV, route=ROUTE, fib_trie=FIB_TRIE) as host:
            host.interface("lo")
            host.interface("eth0")
            by_name = {i["name"]: i for i in hostnet.interfaces()}
        self.assertEqual(by_name["lo"]["rx_bytes"], 21777322283)
        self.assertEqual(by_name["lo"]["rx_packets"], 9123943)
        self.assertEqual(by_name["eth0"]["tx_bytes"], 42)


class ScopeTests(unittest.TestCase):
    def test_pid_1_net_is_preferred_over_proc_net(self):
        """Under pid: host, /proc/1/net is the host and /proc/net is ours."""
        with FakeHost(route=ROUTE):
            self.assertTrue(hostnet.net_dir().endswith(os.path.join("proc", "1", "net")))
            self.assertEqual(hostnet.scoped_label(), "the host")

    def test_missing_mounts_are_reported_not_guessed(self):
        root = tempfile.mkdtemp(prefix="sm-emptyhost-")
        try:
            previous = os.environ.get("HOST_ROOT")
            os.environ["HOST_ROOT"] = root
            self.assertIsNone(hostnet.net_dir())
            self.assertEqual(hostnet.interfaces(), [])
            self.assertEqual(hostnet.routes(), [])
            self.assertEqual(hostnet.listening_ports(), [])
            self.assertEqual(hostnet.dns_servers(), [])
        finally:
            if previous is None:
                os.environ.pop("HOST_ROOT", None)
            else:
                os.environ["HOST_ROOT"] = previous
            shutil.rmtree(root, ignore_errors=True)

    def test_hostname_is_the_hosts_not_the_containers(self):
        """socket.gethostname() reads the container's UTS namespace, so from
        inside the container it is the container ID, not the machine."""
        sentinel = "host-machine-" + "x" * 8
        self.assertNotEqual(sentinel, socket.gethostname())
        with FakeHost(route=ROUTE) as fake:
            with open(os.path.join(fake.root, "etc/hostname"), "w") as handle:
                handle.write(sentinel + "\n")
            self.assertEqual(hostnet.hostname(), sentinel)

    def test_hostname_falls_back_to_the_local_machine_with_no_host_mount(self):
        root = tempfile.mkdtemp(prefix="sm-emptyhost-")
        try:
            previous = os.environ.get("HOST_ROOT")
            os.environ["HOST_ROOT"] = root
            self.assertEqual(hostnet.hostname(), socket.gethostname())
        finally:
            if previous is None:
                os.environ.pop("HOST_ROOT", None)
            else:
                os.environ["HOST_ROOT"] = previous
            shutil.rmtree(root, ignore_errors=True)

    def test_conntrack_is_empty_when_the_table_is_absent(self):
        with FakeHost(tcp=TCP):
            self.assertEqual(hostnet.conntrack(), [])

    def test_conntrack_parses_when_present(self):
        # Ports in this table are hex: sport=51000 is 0x51000, dport=443 is 0x443.
        table = ("ipv4 2 tcp 6 431996 ESTABLISHED src=192.168.1.13 dst=93.184.216.34 "
                 "sport=51000 dport=443\n")
        with FakeHost(nf_conntrack=table):
            rows = hostnet.conntrack()
        self.assertEqual(rows[0]["proto"], "tcp")
        self.assertEqual(rows[0]["src_ip"], "192.168.1.13")
        self.assertEqual(rows[0]["dst_ip"], "93.184.216.34")
        self.assertEqual(rows[0]["src_port"], 0x51000)
        self.assertEqual(rows[0]["dst_port"], 0x443)
        self.assertEqual(rows[0]["state"], "ESTABLISHED")


class DnsTests(unittest.TestCase):
    def test_nameservers_are_read_and_comments_skipped(self):
        with FakeHost() as host:
            host.resolv(RESOLV_CONF)
            self.assertEqual(hostnet.dns_servers(), ["127.0.0.1", "8.8.8.8"])


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

    def test_page_says_where_the_data_came_from(self):
        """The summary is only trustworthy if it names the scope it read."""
        body = json.loads(self.client.get("/network/summary").get_data(as_text=True))
        self.assertIn(body["source"], ("the host", "this host",
                                       "the container (no pid namespace)",
                                       "nothing (host mounts missing)"))
        page = self.client.get("/network/").get_data(as_text=True)
        self.assertIn("sum.source", page)


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
        for port in self._get("ports"):
            self.assertIsInstance(port["local_port"], int, f"bad port in {port}")
            self.assertNotIn("]", port["local_ip"], f"bracket left in {port}")
            self.assertNotIn("]:", port["local_ip"], f"port glued to the address in {port}")

    def test_list_rows_have_the_keys_the_page_reads(self):
        for row in self._get("interfaces"):
            for key in ("name", "state", "mac", "ipv4", "ipv6", "mtu"):
                self.assertIn(key, row)
        # A connected route has no gateway and the default one has no dst, so
        # only these are guaranteed; the page already tolerates both absent.
        for row in self._get("routes"):
            for key in ("dev", "family"):
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