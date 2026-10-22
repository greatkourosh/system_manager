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
import subprocess
import tempfile
import unittest
from unittest import mock

from system_manager import create_app
from system_manager.network_manager import dns as netdns
from system_manager.network_manager import dnsmasq_helper as dnsmasq_helper
from system_manager.network_manager import hostnet
from system_manager.network_manager import topology


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

# Column-aligned, as the kernel writes it. The 0x0 row is an entry the host has
# started to resolve but not finished: it is dropped, because a neighbour with
# no hardware address cannot be acted on and is not what "neighbour" means.
ARP = """IP address       HW type     Flags       HW address            Mask     Device
192.168.1.1      0x1         0x2         aa:bb:cc:dd:ee:ff     *        eno1
192.168.1.130    0x1         0x2         11:22:33:44:55:66     *        wlan0
192.168.1.200    0x1         0x0         00:00:00:00:00:00     *        eno1
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

    def bridge(self, name, members=(), **kwargs):
        """A bridge, and the interfaces enslaved to it."""
        self.interface(name, **kwargs)
        base = os.path.join(self.root, "sys/class/net", name, "brif")
        os.makedirs(base, exist_ok=True)
        for member in members:
            os.makedirs(os.path.join(base, member), exist_ok=True)
            self.interface(member)
            link = os.path.join(self.root, "sys/class/net", member, "master")
            os.makedirs(os.path.dirname(link), exist_ok=True)
            os.symlink(os.path.join("..", name), link)

    def physical(self, name, **kwargs):
        """A real NIC, which sysfs marks with a `device` symlink."""
        self.interface(name, **kwargs)
        device = os.path.join(self.root, "sys/class/net", name, "device")
        os.makedirs(os.path.dirname(device), exist_ok=True)
        os.symlink(os.path.join("..", "..", "..", "0000:05:00.0"), device)

    def container(self, pid, container_id, comm="python", addresses=(), cmdline=""):
        """A container init: nested pid namespace, docker cgroup, addresses."""
        base = os.path.join(self.root, "proc", str(pid))
        os.makedirs(os.path.join(base, "net"), exist_ok=True)
        with open(os.path.join(base, "status"), "w") as handle:
            handle.write(f"Name:\t{comm}\nNSpid:\t{pid}\t1\n")
        with open(os.path.join(base, "cgroup"), "w") as handle:
            handle.write(f"0::/../docker-{container_id}.scope\n")
        with open(os.path.join(base, "comm"), "w") as handle:
            handle.write(comm + "\n")
        with open(os.path.join(base, "cmdline"), "wb") as handle:
            handle.write(cmdline.encode() + b"\0")
        if addresses:
            lines = ["Main:"]
            for address in addresses:
                lines.append(f"  +-- {address}/32 0 0 1")
                lines.append(f"     |-- {address}")
                lines.append("        /32 host LOCAL")
            with open(os.path.join(base, "net/fib_trie"), "w") as handle:
                handle.write("\n".join(lines) + "\n")
        with open(os.path.join(base, "net/dev"), "w") as handle:
            handle.write(
                "Inter-|   Receive                            "
                "  |  Transmit\n"
                " face |bytes    packets errs drop fifo frame "
                "compressed multicast|bytes    packets errs drop fifo "
                "colls carrier compressed\n"
                "    lo: 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0\n"
                "  eth0: 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0\n")

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
        self.assertEqual(hostnet.decode_ipv6(
            "0000000000000000FFFF00000100007F"), "::ffff:127.0.0.1")
        self.assertEqual(hostnet.decode_ipv6(
            "00000000000000000000000000000000"), "::")

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
            tcp = [row for row in hostnet.listening_ports() if row["proto"]
                   == "TCP"]
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
            row = [r for r in hostnet.listening_ports() if r["proto"]
                   == "TCP"][0]
            self.assertEqual(row["local_ip"], "::ffff:127.0.0.1")
            self.assertEqual(row["local_port"], 0x90A7)

    def test_udp_rows_have_no_state_field_so_all_are_included(self):
        with FakeHost(udp=UDP):
            udp = [row for row in hostnet.listening_ports() if row["proto"]
                   == "UDP"]
            self.assertEqual([row["local_port"] for row in udp], [0x14E9])

    def test_unknown_owner_is_none_not_a_guess(self):
        with FakeHost(tcp=TCP):
            self.assertIsNone(hostnet.listening_ports()[0]["pid"])

    def test_socket_is_attributed_to_the_process_holding_its_inode(self):
        # TCP's LISTEN row has inode 95532. fd directories are readable only for
        # our own processes, so attribution is partial by design -- but it is
        # partial, not absent, and this pins the part that does work.
        with FakeHost(tcp=TCP) as host:
            host.process(
                4321, fds=[(7, "socket:[95532]")], exe="/usr/bin/clash")
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
            found = {row["dev"]: row for row in hostnet.routes()
                     if row["family"] == "inet"}
        self.assertEqual(found["eno1"]["dst"], "0.0.0.0/0")
        self.assertEqual(found["vmnet8"]["dst"], "172.16.180.0/24")
        self.assertEqual(found["docker0"]["dst"], "172.17.0.0/16")

    def test_default_route_reports_its_gateway(self):
        with FakeHost(route=ROUTE):
            default = [r for r in hostnet.routes() if r["dst"] ==
                       "0.0.0.0/0"][0]
            self.assertEqual(default["gateway"], "192.168.1.1")
            self.assertEqual(default["dev"], "eno1")

    def test_connected_route_has_no_gateway(self):
        with FakeHost(route=ROUTE):
            connected = [r for r in hostnet.routes() if r["dst"] ==
                         "172.17.0.0/16"][0]
            self.assertEqual(connected["gateway"], "",
                             "RTF_UP without RTF_GATEWAY has none")

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
            docker0 = [i for i in hostnet.interfaces() if i["name"]
                       == "docker0"][0]
        self.assertEqual(docker0["ipv4"], ["172.17.0.1"])

    def test_subnet_addresses_are_not_reported_as_addresses(self):
        """fib_trie marks 127.0.0.0 LOCAL under a /8; only the /32 is real."""
        with FakeHost(route=ROUTE, fib_trie=FIB_TRIE) as host:
            host.interface("lo")
            loopback = [i for i in hostnet.interfaces() if i["name"]
                        == "lo"][0]
        self.assertNotIn("127.0.0.0", loopback["ipv4"])

    def test_ipv6_address_keeps_its_interface(self):
        with FakeHost(route=ROUTE, fib_trie=FIB_TRIE, if_inet6=IF_INET6) as host:
            host.interface("lo")
            host.interface("br-983782549ddc")
            by_name = {i["name"]: i for i in hostnet.interfaces()}
        self.assertEqual(by_name["lo"]["ipv6"], ["::1"])
        self.assertEqual(by_name["br-983782549ddc"]
                         ["ipv6"], ["fe80::6cab:bfff:fe14:856f"])

    def test_mac_mtu_and_state_come_from_sysfs(self):
        with FakeHost(route=ROUTE, fib_trie=FIB_TRIE) as host:
            host.interface("eno1", mac="10:7c:61:79:15:3a",
                           mtu="9000", state="up")
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
            self.assertTrue(hostnet.net_dir().endswith(
                os.path.join("proc", "1", "net")))
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
            self.assertEqual(hostnet.neighbors(), [])
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


class NeighborTests(unittest.TestCase):
    def test_complete_entries_become_rows(self):
        with FakeHost(arp=ARP):
            rows = hostnet.neighbors()
        self.assertEqual(
            [(r["ip"], r["mac"], r["device"], r["state"]) for r in rows],
            [("192.168.1.1", "aa:bb:cc:dd:ee:ff", "eno1", "COMPLETE"),
             ("192.168.1.130", "11:22:33:44:55:66", "wlan0", "COMPLETE")],
        )

    def test_an_unresolved_entry_is_dropped(self):
        """Flag 0x0 has no hardware address; listing it would imply a neighbour that isn't there."""
        with FakeHost(arp=ARP):
            rows = hostnet.neighbors()
        self.assertNotIn("192.168.1.200", [r["ip"] for r in rows])

    def test_rows_sort_numerically_not_as_text(self):
        with FakeHost(arp="IP address HW type Flags HW address Mask Device\n"
                          "192.168.1.9 0x1 0x2 aa:bb:cc:dd:ee:01 * eno1\n"
                          "192.168.1.10 0x1 0x2 aa:bb:cc:dd:ee:02 * eno1\n"):
            self.assertEqual([r["ip"] for r in hostnet.neighbors()],
                             ["192.168.1.9", "192.168.1.10"])

    def test_a_missing_arp_file_is_empty_not_an_error(self):
        """The container's netns may have no ARP file at all; [] keeps the page working."""
        with FakeHost():
            self.assertEqual(hostnet.neighbors(), [])

    def test_the_header_row_is_not_data(self):
        with FakeHost(arp=ARP):
            for row in hostnet.neighbors():
                self.assertNotIn("HW", row["mac"])


class DiagnosticLogicTests(unittest.TestCase):
    def test_dns_response_parser_reads_answer_and_rcode(self):
        reply = b"\x12\x34\x81\x80\x00\x01\x00\x01\x00\x00\x00\x00\x03www\x07example\x03com\x00\x00\x01\x00\x01\xc0\x0c\x00\x01\x00\x01\x00\x00\x00\x3c\x00\x04\x7f\x00\x00\x01"
        parsed = netdns.parse_dns_response(reply)
        self.assertEqual(parsed["rcode"], "NOERROR")
        self.assertEqual(parsed["answers"], ["127.0.0.1"])

    def test_dns_result_ranking_keeps_reliability_first(self):
        ranked = netdns.rank_provider_results([
            {"provider": "A", "success_rate": 0.8,
                "median_latency": 15.0, "timeouts": 2, "errors": []},
            {"provider": "B", "success_rate": 0.95,
                "median_latency": 12.0, "timeouts": 0, "errors": []},
        ])
        self.assertEqual(ranked[0]["provider"], "B")
        self.assertEqual(ranked[1]["provider"], "A")

    def test_recommended_pair_uses_primary_ips_from_two_working_providers(self):
        pair = netdns.select_recommended_pair([
            {"provider": "Current DHCP / TCI", "addresses": ["127.0.0.1"],
             "success_rate": 0, "timeouts": 6, "attempts": 6, "median_latency": 0},
            {"provider": "Electro", "addresses": ["78.157.42.100", "78.157.42.101"],
             "success_rate": 1, "timeouts": 0, "attempts": 6, "median_latency": 17},
            {"provider": "Shecan", "addresses": ["178.22.122.100", "185.51.200.2"],
             "success_rate": 1, "timeouts": 0, "attempts": 6, "median_latency": 18},
        ])
        self.assertEqual(pair, {
            "primary": {"provider": "Electro", "address": "78.157.42.100"},
            "secondary": {"provider": "Shecan", "address": "178.22.122.100"},
        })

    def test_recommended_pair_is_inconclusive_without_two_working_providers(self):
        self.assertIsNone(netdns.select_recommended_pair([
            {"provider": "Only", "addresses": ["1.1.1.1"],
             "success_rate": 1, "timeouts": 0, "attempts": 1, "median_latency": 10},
        ]))

    def test_provider_summary_ignores_invalid_inputs(self):
        self.assertEqual(netdns.normalise_provider_input(
            {"provider": "bad", "addresses": "not-a-list"}), [])


class DnsmasqHelperTests(unittest.TestCase):
    def test_upstream_replacement_preserves_split_dns_and_other_options(self):
        original = (
            "conf-dir=/etc/dnsmasq.d/,*.conf\n"
            "no-resolv\n"
            "server=192.168.1.1\n"
            "server=178.22.122.100\n"
            "server=/workers.dev/1.1.1.1\n"
            "listen-address=127.0.0.1\n"
        )
        updated = dnsmasq_helper.replace_global_upstreams(
            original, ["8.8.8.8", "8.8.4.4"])
        self.assertIn("server=8.8.8.8\nserver=8.8.4.4\n", updated)
        self.assertIn("server=/workers.dev/1.1.1.1\n", updated)
        self.assertIn("no-resolv\n", updated)
        self.assertIn("listen-address=127.0.0.1\n", updated)
        self.assertNotIn("server=192.168.1.1", updated)

    def test_upstream_replacement_adds_global_servers_when_missing(self):
        updated = dnsmasq_helper.replace_global_upstreams(
            "no-resolv\nserver=/example.com/1.1.1.1\n",
            ["9.9.9.9", "149.112.112.112"])
        self.assertTrue(updated.endswith(
            "server=9.9.9.9\nserver=149.112.112.112\n"))
        self.assertIn("server=/example.com/1.1.1.1", updated)

    def test_apply_accepts_only_two_distinct_public_dns_addresses(self):
        self.assertEqual(dnsmasq_helper._validate_addresses(
            ["8.8.8.8", "8.8.4.4"]), ["8.8.8.8", "8.8.4.4"])
        for addresses in (["127.0.0.1", "8.8.8.8"],
                          ["8.8.8.8", "8.8.8.8"], ["not-an-ip", "8.8.8.8"]):
            with self.assertRaises(ValueError):
                dnsmasq_helper._validate_addresses(addresses)


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
        for table_id in ("ifaces", "routes", "ports", "neighbors", "ct"):
            self.assertIn(f'<tbody id="{table_id}">', page)
        # "#<id> tbody" would silently resolve to nothing: the id *is* the tbody.
        self.assertNotIn(" tbody'", page)

    def test_script_derives_its_api_prefix_from_the_page_path(self):
        page = self.client.get("/network/").get_data(as_text=True)
        self.assertIn("location.pathname.replace", page)

    def test_page_has_dns_diagnostics_section(self):
        page = self.client.get("/network/").get_data(as_text=True)
        self.assertIn("DNS Diagnostics &amp; Optimizer", page)
        self.assertIn("dns-diagnostics", page)
        self.assertIn('id="dns-use-recommended-pair"', page)
        self.assertIn('id="dns-selected-pair"', page)
        self.assertIn('id="dns-command-preview"', page)
        self.assertIn('id="dns-apply-command"', page)
        self.assertIn('id="dns-rollback-command-template"', page)
        self.assertIn('id="dns-current-setup"', page)
        self.assertIn('Current host DNS', page)
        self.assertIn("sudo cp /etc/dnsmasq.conf", page)
        self.assertNotIn("system-manager-dns-helper apply", page)

    def test_page_says_where_the_data_came_from(self):
        """The summary is only trustworthy if it names the scope it read."""
        body = json.loads(self.client.get(
            "/network/summary").get_data(as_text=True))
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
        self.assertEqual(response.status_code, 200,
                         f"/network/{name} returned {response.status_code}")
        return json.loads(response.get_data(as_text=True))

    def test_summary_has_the_keys_the_page_reads(self):
        summary = self._get("summary")
        self.assertEqual(sorted(summary["hostname"]), ["domain", "hostname"])
        for key in ("interfaces", "routes", "listening_ports", "dns_servers"):
            self.assertIn(key, summary)

    def test_listening_ports_parse_every_row(self):
        for port in self._get("ports"):
            self.assertIsInstance(
                port["local_port"], int, f"bad port in {port}")
            self.assertNotIn("]", port["local_ip"], f"bracket left in {port}")
            self.assertNotIn("]:", port["local_ip"],
                             f"port glued to the address in {port}")

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
        response = self._get("dns")
        self.assertIsInstance(response["servers"], list)
        self.assertIsInstance(response["resolver_servers"], list)
        self.assertIn("source", response)

    def test_dns_endpoint_distinguishes_upstreams_from_local_resolver(self):
        state = {
            "servers": ["78.157.42.100", "178.22.122.100"],
            "source": "dnsmasq configuration: /etc/dnsmasq.conf",
        }
        with mock.patch(
            "system_manager.network_manager.api.dnsmasq_config_state",
            return_value=state,
        ), mock.patch.object(
            hostnet, "dns_servers", return_value=["127.0.0.1"]
        ):
            response = self._get("dns")
        self.assertEqual(response["servers"], [
                         "78.157.42.100", "178.22.122.100"])
        self.assertEqual(response["resolver_servers"], ["127.0.0.1"])

    def test_dnsmasq_config_reads_included_upstreams_and_skips_split_dns(self):
        with FakeHost():
            etc = os.path.join(os.environ["HOST_ROOT"], "etc")
            os.makedirs(os.path.join(etc, "dnsmasq.d"))
            with open(os.path.join(etc, "dnsmasq.conf"), "w") as handle:
                handle.write(
                    "conf-dir=/etc/dnsmasq.d/,*.conf\nno-resolv\n")
            upstream_path = os.path.join(etc, "dnsmasq.d", "upstream.conf")
            with open(upstream_path, "w") as handle:
                handle.write(
                    "server=78.157.42.100\n"
                    "server=178.22.122.100\n"
                    "server=/example.com/1.1.1.1\n"
                )
            state = netdns.dnsmasq_config_state()
        self.assertEqual(state["servers"], ["78.157.42.100", "178.22.122.100"])
        self.assertIn("/etc/dnsmasq.d/upstream.conf", state["source"])

    def test_dns_apply_is_disabled_when_authentication_is_off(self):
        response = self.client.post("/network/dns/apply", json={
            "provider": "Electro + Shecan",
            "addresses": ["78.157.42.100", "178.22.122.100"],
            "confirm": True,
        })
        self.assertEqual(response.status_code, 403)
        self.assertIn("authentication is off", response.get_json()["error"])

    def test_authenticated_dns_apply_and_rollback_use_the_fixed_helper(self):
        directory = tempfile.mkdtemp(prefix="sm-network-dns-action-")
        app = create_app({"DISABLE_AUTH": 0, "TESTING": True,
                          "AUDIT_PATH": os.path.join(directory, "actions.db")})
        security = app.config["SECURITY"]
        security.credential = "network-dns-test-credential"
        client = app.test_client()
        login = client.post("/api/login", json={"code": security.credential})
        self.assertEqual(login.status_code, 200)

        applied = {
            "status": "applied",
            "addresses": ["8.8.8.8", "8.8.4.4"],
            "backup_id": "dnsmasq-1792320000-a1b2c3d4.conf",
            "dnsmasq_state": "active",
            "verification": "config and service verified",
            "rollback_command": "docker exec system-manager sudo -n helper rollback dnsmasq-1792320000-a1b2c3d4.conf",
        }
        rolled_back = {"status": "rolled-back", "dnsmasq_state": "active"}
        with mock.patch(
                "system_manager.network_manager.api.run_dnsmasq_helper",
                side_effect=[applied, rolled_back]) as helper:
            missing_confirmation = client.post("/network/dns/apply", json={
                "provider": "Google", "addresses": ["8.8.8.8", "8.8.4.4"],
            })
            self.assertEqual(missing_confirmation.status_code, 400)
            helper.assert_not_called()

            apply_response = client.post("/network/dns/apply", json={
                "provider": "Google", "addresses": ["8.8.8.8", "8.8.4.4"],
                "confirm": True,
            })
            self.assertEqual(apply_response.status_code, 200)
            apply_body = apply_response.get_json()
            self.assertEqual(apply_body["status"], "applied")
            self.assertTrue(any("sudo cp \"$(ls -t /etc/dnsmasq.conf.bak.* | head -n1)\"" in command
                                for command in apply_body["rollback_commands"]))
            self.assertEqual(helper.call_args_list[0].args,
                             ("apply", ["8.8.8.8", "8.8.4.4"]))

            rollback_response = client.post("/network/dns/rollback", json={
                "backup_id": applied["backup_id"], "confirm": True,
            })
            self.assertEqual(rollback_response.status_code, 200)
            self.assertEqual(rollback_response.get_json()
                             ["status"], "rolled-back")
            self.assertEqual(helper.call_args_list[1].args,
                             ("rollback", applied["backup_id"]))

    def test_neighbors_have_the_keys_the_page_reads(self):
        for row in self._get("neighbors"):
            for key in ("ip", "mac", "device", "state"):
                self.assertIn(key, row)

    def test_api_requires_a_session_when_auth_is_on(self):
        directory = tempfile.mkdtemp(prefix="sm-network-auth-")
        app = create_app({"DISABLE_AUTH": 0, "TESTING": True,
                          "AUDIT_PATH": os.path.join(directory, "actions.db")})
        client = app.test_client()
        for name in ("summary", "interfaces", "routes", "dns", "ports", "conntrack",
                     "neighbors", ""):
            self.assertNotEqual(client.get(f"/network/{name}").status_code, 200,
                                f"/network/{name} served without a session")
        self.assertEqual(client.post("/network/dns/apply", headers={
            "Accept": "application/json",
        }, json={
            "provider": "Google", "addresses": ["8.8.8.8", "8.8.4.4"],
            "confirm": True,
        }).status_code, 401)
        self.assertEqual(client.post("/network/dns/rollback", headers={
            "Accept": "application/json",
        }, json={
            "backup_id": "dnsmasq-1792320000-a1b2c3d4.conf", "confirm": True,
        }).status_code, 401)


class TopologyTests(unittest.TestCase):
    """The shape of the host's network, as a graph.

    The structure comes from sysfs, which procfs does not record: a bridge
    has a `brif` directory, an enslaved port a `master` symlink, and a real
    NIC a `device` symlink.
    """

    def test_a_bridge_is_told_apart_from_a_nic_and_a_port(self):
        with FakeHost(route=ROUTE, fib_trie=FIB_TRIE) as host:
            host.interface("lo")
            host.bridge("br-test", members=["veth-a"])
            host.physical("eno1")
            kinds = {node["label"]: node.get("subtype")
                     for node in topology.topology()["nodes"]
                     if node["kind"] == "interface"}
        self.assertEqual(kinds["br-test"], "bridge")
        self.assertEqual(kinds["eno1"], "physical")
        self.assertEqual(kinds["veth-a"], "port")
        self.assertEqual(kinds["lo"], "loopback")

    def test_a_port_hangs_off_its_bridge_not_off_the_host(self):
        with FakeHost(route=ROUTE, fib_trie=FIB_TRIE) as host:
            host.bridge("br-test", members=["veth-a"])
            graph = topology.topology()
        links = {(link["source"], link["target"]) for link in graph["links"]}
        self.assertIn(("iface:br-test", "iface:veth-a"), links)
        self.assertNotIn(("host", "iface:veth-a"), links)

    def test_depth_counts_hops_from_the_host(self):
        with FakeHost(route=ROUTE, fib_trie=FIB_TRIE) as host:
            host.bridge("br-test", members=["veth-a"])
            graph = topology.topology()
        depths = {node["id"]: node["depth"] for node in graph["nodes"]}
        self.assertEqual(depths["host"], 0)
        self.assertEqual(depths["iface:br-test"], 1)
        self.assertEqual(depths["iface:veth-a"], 2)

    def test_a_container_is_found_by_its_nested_pid_namespace(self):
        with FakeHost(route=ROUTE, fib_trie=FIB_TRIE) as host:
            host.container(4242, "a" * 64, comm="ollama",
                           addresses=["172.22.0.2"], cmdline="/bin/ollama serve")
            graph = topology.topology()
        containers = [node for node in graph["nodes"] if node["kind"] == "container"]
        self.assertEqual(len(containers), 1)
        self.assertEqual(containers[0]["label"], "ollama")
        self.assertEqual(containers[0]["container_id"], "a" * 12)
        self.assertEqual(containers[0]["addresses"], ["172.22.0.2"])
        self.assertEqual(graph["containers"], 1)

    def test_a_process_outside_a_container_is_not_a_container(self):
        with FakeHost(route=ROUTE, fib_trie=FIB_TRIE) as host:
            host.interface("lo")
            base = os.path.join(host.root, "proc", "77")
            os.makedirs(base, exist_ok=True)
            with open(os.path.join(base, "status"), "w") as handle:
                handle.write("Name:\tsystemd\nNSpid:\t77\n")
            graph = topology.topology()
        self.assertEqual(graph["containers"], 0)

    def test_a_container_is_attached_to_the_bridge_its_address_routes_through(self):
        with FakeHost(route=ROUTE, fib_trie=FIB_TRIE) as host:
            # The route fixture puts 172.17.0.0/16 on docker0.
            host.bridge("docker0")
            host.container(4242, "b" * 64, addresses=["172.17.0.2"])
            graph = topology.topology()
        links = {(link["source"], link["target"]) for link in graph["links"]}
        self.assertIn(("iface:docker0", "container:" + "b" * 12), links)

    def test_the_default_route_becomes_the_upstream_node(self):
        with FakeHost(route=ROUTE, fib_trie=FIB_TRIE) as host:
            host.interface("lo")
            host.physical("eno1")
            graph = topology.topology()
        upstream = [node for node in graph["nodes"] if node["kind"] == "upstream"]
        self.assertEqual(len(upstream), 1)
        self.assertEqual(upstream[0]["gateway"], "192.168.1.1")
        self.assertEqual(upstream[0]["device"], "eno1")

    def test_no_host_mounts_is_an_empty_graph_not_an_error(self):
        root = tempfile.mkdtemp(prefix="sm-emptyhost-")
        previous = os.environ.get("HOST_ROOT")
        try:
            os.environ["HOST_ROOT"] = root
            graph = topology.topology()
        finally:
            if previous is None:
                os.environ.pop("HOST_ROOT", None)
            else:
                os.environ["HOST_ROOT"] = previous
            shutil.rmtree(root, ignore_errors=True)
        self.assertEqual(graph["nodes"], [])
        self.assertEqual(graph["links"], [])

    def test_every_link_points_at_a_node_that_exists(self):
        with FakeHost(route=ROUTE, fib_trie=FIB_TRIE) as host:
            host.bridge("br-test", members=["veth-a"])
            host.container(4242, "c" * 64, addresses=["172.22.0.2"])
            graph = topology.topology()
        known = {node["id"] for node in graph["nodes"]}
        for link in graph["links"]:
            self.assertIn(link["source"], known, link)
            self.assertIn(link["target"], known, link)

    def test_a_container_with_no_route_is_drawn_not_dropped(self):
        """A container running but unreachable would read as a bug if the
        graph simply left it out."""
        with FakeHost(route=ROUTE, fib_trie=FIB_TRIE) as host:
            # 10.9.9.9 is in no route the fixture has.
            host.container(4242, "d" * 64, addresses=["10.9.9.9"])
            graph = topology.topology()
        containers = [node for node in graph["nodes"] if node["kind"] == "container"]
        self.assertEqual(len(containers), 1)
        self.assertEqual(containers[0]["detail"], "no route to it")
        links = {(link["source"], link["target"]) for link in graph["links"]}
        self.assertIn(("host", "container:" + "d" * 12), links)

    def test_a_container_sharing_the_host_network_is_not_put_on_a_bridge(self):
        with FakeHost(route=ROUTE, fib_trie=FIB_TRIE) as host:
            host.bridge("docker0")
            # 172.17.0.1 is the host's own address in the fixture, so this
            # container is in the host's network namespace.
            host.container(4242, "e" * 64, addresses=["172.17.0.1"])
            graph = topology.topology()
        containers = [node for node in graph["nodes"] if node["kind"] == "container"]
        self.assertEqual(len(containers), 1)
        self.assertTrue(containers[0]["host_network"])
        self.assertEqual(containers[0]["device"], "")
        links = {(link["source"], link["target"]) for link in graph["links"]}
        self.assertIn(("host", "container:" + "e" * 12), links)
        self.assertNotIn(("iface:docker0", "container:" + "e" * 12), links)

    def test_the_endpoint_serves_the_graph(self):
        app = make_app()
        client = app.test_client()
        response = client.get("/network/topology")
        self.assertEqual(response.status_code, 200)
        body = json.loads(response.get_data(as_text=True))
        for key in ("host", "scope", "nodes", "links", "containers"):
            self.assertIn(key, body)


class NetworkPageFilterTests(unittest.TestCase):
    """The three panels that start collapsed, and what filters they carry."""

    def setUp(self):
        self.app = make_app()
        self.client = self.app.test_client()
        self.page = self.client.get("/network/").get_data(as_text=True)
    def test_the_three_panels_start_minimized(self):
        for panel in ("interfaces-panel", "routes-panel", "ports-panel"):
            self.assertIn(f'id="{panel}"', self.page)
            # An open <details> carries the attribute; a minimized one does not.
            block = self._block(panel)
            self.assertNotIn(" open>", block,
                             f"{panel} starts expanded")

    def test_the_topology_panel_is_open_and_present(self):
        self.assertIn('id="topology-panel"', self.page)
        self.assertIn(" open>", self._block("topology-panel"))
        self.assertIn('id="topology"', self.page)

    def test_each_panel_offers_recommended_filters(self):
        for group, presets in (
            ("iface-filters", ("all", "up", "ipv4", "ipv6", "traffic")),
            ("route-filters", ("all", "default", "ipv4", "ipv6", "gateway")),
            ("port-filters", ("all", "tcp", "udp", "external", "loopback",
                              "privileged", "known")),
        ):
            block = self._block(group)
            for preset in presets:
                self.assertIn(f'data-preset="{preset}"', block,
                              f"{group} is missing the {preset} filter")
            self.assertIn('data-preset="all" aria-pressed="true"', block)

    def test_the_page_fetches_the_topology(self):
        self.assertIn("get('/topology')", self.page)

    def _block(self, element_id):
        """The markup from an element's opening tag to its closing one."""
        start = self.page.find(f'id="{element_id}"')
        self.assertNotEqual(start, -1, f"no #{element_id} on the page")
        end = self.page.find("</details>", start)
        return self.page[start:end] if end != -1 else self.page[start:]


class NetworkPageScriptTests(unittest.TestCase):
    """The page's own script, run.

    A server-side assertion reads the template. The failure this guards
    against lives in what the JavaScript computes at runtime, so the script
    from the *rendered* page is executed against a DOM stub and the result
    is asserted on.
    """

    def setUp(self):
        self.app = make_app()
        self.client = self.app.test_client()
        self.page = self.client.get("/network/").get_data(as_text=True)

    def test_the_rendered_page_script_filters_and_draws(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not installed")
        directory = tempfile.mkdtemp(prefix="sm-network-js-")
        try:
            page_file = os.path.join(directory, "page.html")
            with open(page_file, "w", encoding="utf-8") as handle:
                handle.write(self.page)
            script = os.path.join(os.path.dirname(__file__), "page_dom_test.js")
            result = subprocess.run(
                [node, script, page_file],
                capture_output=True, text=True, timeout=120)
            self.assertEqual(
                result.returncode, 0,
                "the page's script failed its own assertions:\n"
                f"{result.stdout}\n{result.stderr}")
            self.assertIn("ALL PAGE ASSERTIONS PASSED", result.stdout)
        finally:
            shutil.rmtree(directory, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
