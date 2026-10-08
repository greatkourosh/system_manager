"""Read the host's network state by parsing procfs, with no subprocesses.

Why this exists: inside the container `ip` and `ss` report the *container's*
stack, not the host's, and the two obvious fixes do not work.

- `nsenter -t 1 -n` is blocked by the default seccomp profile. It fails as
  root too, so no capability helps.
- Binding /proc at /host/proc does not help either: procfs resolves the
  `net` symlink against the *reader's* pid namespace, so /host/proc/net/dev
  shows this container's 2 interfaces while the host has 52.

What does work is /proc/1/net/* under `pid: host`, which docker-compose.yml
already sets: there, 1 is the host's init, so its `net` directory is the
host's network namespace. MAC, MTU and operstate come from
/host/sys/class/net/<if>/, which procfs does not carry.

So HOST_ROOT set -> parse the host's files; unset -> this is already the
host, /proc/net/* is correct, and the same parsers read it.
"""
import ipaddress
import os
import re
import socket
import struct

__all__ = [
    "decode_ipv6_network",
    "host_root", "net_dir", "scoped_label", "interfaces", "routes", "dns_servers",
    "listening_ports", "conntrack", "decode_ipv4", "decode_ipv6", "socket_state_name",
]

# Socket states, indexed by the hex field in /proc/net/tcp*.
TCP_STATES = {
    "01": "ESTABLISHED", "02": "SYN_SENT", "03": "SYN_RECV", "04": "FIN_WAIT1",
    "05": "FIN_WAIT2", "06": "TIME_WAIT", "07": "CLOSE", "08": "CLOSE_WAIT",
    "09": "LAST_ACK", "0A": "LISTEN", "0B": "CLOSING",
}

_HEX4 = re.compile(r"^[0-9A-Fa-f]{8}$")
_HEX16 = re.compile(r"^[0-9A-Fa-f]{32}$")


def host_root():
    """The host prefix, or "" when already running on the host."""
    return os.environ.get("HOST_ROOT", "").rstrip("/")


def scoped_label():
    """What the numbers below describe: the host, or whatever we can see."""
    root = host_root()
    if not root:
        return "this host"
    if os.path.isdir(os.path.join(root, "proc/1/net")):
        return "the host"
    if os.path.isdir(os.path.join(root, "proc/net")):
        return "the container (no pid namespace)"
    return "nothing (host mounts missing)"


def net_dir():
    """Directory holding the authoritative /proc/net view, or None.

    With pid: host, /proc/1/net is the host's namespace while the bare
    /proc/net symlink still points at ours. Prefer the former; fall back for
    a direct-on-host run, where there is no other pid namespace to enter.
    """
    root = host_root()
    if not root:
        return "/proc/net" if os.path.isdir("/proc/net") else None
    for candidate in (os.path.join(root, "proc/1/net"), os.path.join(root, "proc/net")):
        if os.path.isdir(candidate):
            return candidate
    return None


def _read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError:
        return None


def _read_net(name):
    directory = net_dir()
    return _read(os.path.join(directory, name)) if directory else None


def _read_sys(iface, attribute):
    root = host_root()
    base = os.path.join(root, "sys/class/net", iface) if root else f"/sys/class/net/{iface}"
    return (_read(os.path.join(base, attribute)) or "").strip()


def _sys_ifaces():
    root = host_root()
    base = os.path.join(root, "sys/class/net") if root else "/sys/class/net"
    try:
        return sorted(os.listdir(base))
    except OSError:
        return []


def decode_ipv4(hex_digits):
    """/proc/net/tcp and /proc/net/route store IPv4 as little-endian hex."""
    if not _HEX4.match(hex_digits or ""):
        raise ValueError(f"not an IPv4 hex word: {hex_digits!r}")
    return socket.inet_ntop(socket.AF_INET, struct.pack("<I", int(hex_digits, 16)))


def decode_ipv6(hex_digits):
    """Decode IPv6 as procfs stores it in /proc/net/tcp6: four little-endian words.

    0000000000000000FFFF00000100007F -> ::ffff:127.0.0.1

    Not every procfs file agrees on byte order. /proc/net/if_inet6 and
    /proc/net/ipv6_route hold plain network-order bytes, so they use
    decode_ipv6_network() instead.
    """
    if not _HEX16.match(hex_digits or ""):
        raise ValueError(f"not an IPv6 hex string: {hex_digits!r}")
    packed = b"".join(struct.pack("<I", int(hex_digits[i:i + 8], 16)) for i in range(0, 32, 8))
    return socket.inet_ntop(socket.AF_INET6, packed)


def decode_ipv6_network(hex_digits):
    """Decode IPv6 as /proc/net/if_inet6 and /proc/net/ipv6_route store it: as written."""
    if not _HEX16.match(hex_digits or ""):
        raise ValueError(f"not an IPv6 hex string: {hex_digits!r}")
    return socket.inet_ntop(socket.AF_INET6, bytes.fromhex(hex_digits))


def socket_state_name(code):
    return TCP_STATES.get((code or "").upper(), (code or "").upper() or "UNKNOWN")


def _prefix_len(mask_hex):
    """/proc/net/route's mask is a little-endian word: 0000FFFF is /16.

    Reversing the bytes cancels the little-endian order, so the result is
    just a popcount over the mask as written.
    """
    try:
        return sum(bin(byte).count("1") for byte in bytes.fromhex(mask_hex)[::-1])
    except ValueError:
        return -1


def _ipv4_network(address_hex, mask_hex):
    """(address, prefix) pair from a route row, or None if it will not parse."""
    length = _prefix_len(mask_hex)
    if length < 0 or length > 32:
        return None
    try:
        return ipaddress.IPv4Address(decode_ipv4(address_hex)), length
    except (ValueError, ipaddress.AddressValueError):
        return None


def _route_v4_rows():
    """Raw /proc/net/route rows, decoded as far as the hex goes."""
    text = _read_net("route")
    rows = []
    for line in (text or "").splitlines()[1:]:
        fields = line.split()
        if len(fields) < 8:
            continue
        try:
            flags, metric = int(fields[3], 16), int(fields[6])
        except ValueError:
            continue
        rows.append({
            "ifname": fields[0], "dst_hex": fields[1], "gateway_hex": fields[2],
            "flags": flags, "metric": metric, "mask_hex": fields[7],
        })
    return rows


def _parse_dev():
    """{iface: counters} from /proc/net/dev."""
    text = _read_net("dev")
    counters = {}
    for line in (text or "").splitlines()[2:]:
        name, _, rest = line.partition(":")
        fields = rest.split()
        if not name.strip() or len(fields) < 16:
            continue
        try:
            counters[name.strip()] = {
                "rx_bytes": int(fields[0]), "rx_packets": int(fields[1]),
                "rx_errors": int(fields[2]), "rx_dropped": int(fields[3]),
                "tx_bytes": int(fields[8]), "tx_packets": int(fields[9]),
                "tx_errors": int(fields[10]), "tx_dropped": int(fields[11]),
            }
        except ValueError:
            continue
    return counters


def _local_v4_addresses():
    """Host-assigned IPv4 addresses from /proc/net/fib_trie.

    Only the /32 LOCAL entries are real interface addresses. fib_trie also
    marks the subnet address itself LOCAL under a /8 or /31, so 127.0.0.0
    shows up next to 127.0.0.1; keeping the /32s only drops those.

    fib_trie has no interface column, so this yields addresses alone; which
    interface owns each one comes from _v4_by_iface().
    """
    text = _read_net("fib_trie")
    addresses = set()
    pending = None
    for line in (text or "").splitlines():
        stripped = line.strip()
        node = re.match(r"^\|--\s+(\d+(?:\.\d+){3})$", stripped)
        if node:
            pending = node.group(1)
            continue
        # A LOCAL marker's prefix length sits on the marker line itself.
        local = re.match(r"^/(\d+)\s+host\s+LOCAL$", stripped)
        if local and pending:
            if int(local.group(1)) == 32:
                addresses.add(pending)
            pending = None
    return sorted(addresses)


def _v4_by_iface():
    """Match each LOCAL IPv4 address to the interface whose route contains it.

    Longest prefix wins, and that ordering is what makes the result correct:
    every address also falls inside the 0.0.0.0/0 default route on eno1, so
    first-match would file all of them there.
    """
    networks = []
    for row in _route_v4_rows():
        parsed = _ipv4_network(row["dst_hex"], row["mask_hex"])
        if parsed is None:
            continue
        networks.append((parsed[1], row["ifname"],
                         ipaddress.ip_network(f"{parsed[0]}/{parsed[1]}", strict=False)))
    ordered = sorted(networks, key=lambda row: row[0], reverse=True)

    by_iface = {}
    for address in _local_v4_addresses():
        try:
            parsed = ipaddress.IPv4Address(address)
        except ipaddress.AddressValueError:
            continue
        # The local route table has no entry for lo, so 127.0.0.0/8 would
        # otherwise match the default route and be filed under eno1.
        if parsed.is_loopback:
            by_iface.setdefault("lo", []).append(address)
            continue
        for _, ifname, network in ordered:
            if parsed in network:
                by_iface.setdefault(ifname, []).append(address)
                break
    return by_iface


def _v6_by_iface():
    """{iface: [ipv6, ...]} from /proc/net/if_inet6, which names the interface."""
    text = _read_net("if_inet6")
    by_iface = {}
    for line in (text or "").splitlines():
        fields = line.split()
        if len(fields) < 6:
            continue
        try:
            address = decode_ipv6_network(fields[0])
        except ValueError:
            continue
        by_iface.setdefault(fields[5], []).append(address)
    return by_iface


def interfaces():
    """Every interface with addresses, MAC, MTU, state and byte counters.

    The shape matches what the page already reads, so the template is
    unchanged.
    """
    if not net_dir():
        return []
    names = _sys_ifaces() or sorted(_parse_dev())
    counters = _parse_dev()
    v4 = _v4_by_iface()
    v6 = _v6_by_iface()

    rows = []
    for name in names:
        counter = counters.get(name, {})
        try:
            mtu = int(_read_sys(name, "mtu") or 0)
        except ValueError:
            mtu = 0
        rows.append({
            "name": name,
            "state": (_read_sys(name, "operstate") or "unknown").upper(),
            "type": "unknown",
            "mac": _read_sys(name, "address"),
            "ipv4": sorted(v4.get(name, [])),
            "ipv6": sorted(v6.get(name, [])),
            "mtu": mtu,
            "tx_bytes": counter.get("tx_bytes", 0),
            "rx_bytes": counter.get("rx_bytes", 0),
            "tx_packets": counter.get("tx_packets", 0),
            "rx_packets": counter.get("rx_packets", 0),
            "tx_errors": counter.get("tx_errors", 0),
            "rx_errors": counter.get("rx_errors", 0),
            "tx_dropped": counter.get("tx_dropped", 0),
            "rx_dropped": counter.get("rx_dropped", 0),
        })
    return rows


def routes():
    """Host IPv4 and IPv6 routes. CIDR strings, where procfs stores hex."""
    if not net_dir():
        return []
    result = []

    for row in _route_v4_rows():
        parsed = _ipv4_network(row["dst_hex"], row["mask_hex"])
        if parsed is None:
            continue
        gateway = ""
        if row["flags"] & 0x2:  # RTF_GATEWAY
            try:
                gateway = decode_ipv4(row["gateway_hex"])
            except ValueError:
                gateway = ""
        result.append({
            "dst": f"{parsed[0]}/{parsed[1]}",
            "gateway": gateway,
            "dev": row["ifname"],
            "protocol": "kernel",
            "metric": row["metric"],
            "scope": "global" if row["flags"] & 0x1 else "link",
            "family": "inet",
        })

    for line in (_read_net("ipv6_route") or "").splitlines():
        fields = line.split()
        if len(fields) < 10 or not _HEX16.match(fields[0]):
            continue
        try:
            prefixlen, flags, metric = int(fields[1], 16), int(fields[4], 16), int(fields[6], 16)
            prefix = decode_ipv6_network(fields[0])
            gateway = decode_ipv6_network(fields[2]) if flags & 0x2 else ""
        except ValueError:
            continue
        result.append({
            "dst": f"{prefix}/{prefixlen}",
            "gateway": gateway,
            "dev": fields[9],
            "protocol": "kernel",
            "metric": metric,
            "scope": "link",
            "family": "inet6",
        })
    return result


def dns_servers():
    """The host's nameservers, from the mounted /etc/resolv.conf."""
    root = host_root()
    text = _read(os.path.join(root, "etc/resolv.conf") if root else "/etc/resolv.conf")
    servers = []
    for line in (text or "").splitlines():
        if line.strip().startswith("nameserver"):
            parts = line.split()
            if len(parts) > 1:
                servers.append(parts[1])
    return servers


def _inode_owners():
    """{socket inode: pid} by walking /proc/*/fd, for the pids we may read.

    fd directories are readable only for processes we own, so this resolves
    most of the invoking user's sockets and none of root's -- CAP_DAC_READ_SEARCH
    does not extend it to /host/proc/*/fd. A pid of None therefore means
    "owned by a process we cannot inspect", which is a real answer, not a
    missing one.
    """
    root = host_root()
    base = os.path.join(root, "proc") if root else "/proc"
    try:
        pids = [entry for entry in os.listdir(base) if entry.isdigit()]
    except OSError:
        return {}
    owners = {}
    for pid in pids:
        fd_dir = os.path.join(base, pid, "fd")
        try:
            names = os.listdir(fd_dir)
        except OSError:
            continue
        for fd in names:
            try:
                target = os.readlink(os.path.join(fd_dir, fd))
            except OSError:
                continue
            if target.startswith("socket:["):
                owners[target[len("socket:["):-1]] = int(pid)
    return owners


def _process_exe(pid):
    """The executable behind a pid, or "" when it is unreadable or gone."""
    if not pid:
        return ""
    root = host_root()
    link = os.path.join(root, "proc", str(pid), "exe") if root else f"/proc/{pid}/exe"
    try:
        return os.readlink(link)
    except OSError:
        return ""


def listening_ports():
    """Listening sockets, from /proc/net/{tcp,tcp6,udp,udp6}.

    tcp rows carry a state field and only LISTEN is reported, matching the
    `ss -tulpn` this replaces. udp has no state field, and an unconnected UDP
    socket is its server-side equivalent, so all of them are included.
    """
    if not net_dir():
        return []
    owners = _inode_owners()
    result = []
    for name, decoder, needs_listen in (("tcp", decode_ipv4, True), ("tcp6", decode_ipv6, True),
                                        ("udp", decode_ipv4, False), ("udp6", decode_ipv6, False)):
        text = _read_net(name)
        for line in (text or "").splitlines()[1:]:
            fields = line.split()
            if len(fields) < 10:
                continue
            address_hex, _, port_hex = fields[1].partition(":")
            try:
                address, port = decoder(address_hex), int(port_hex, 16)
            except (ValueError, ipaddress.AddressValueError):
                continue
            if needs_listen and socket_state_name(fields[3]) != "LISTEN":
                continue
            result.append({
                "proto": name.upper().rstrip("6"),
                "local_ip": address,
                "local_port": port,
                "pid": owners.get(fields[9]),
                "exe": _process_exe(owners.get(fields[9])),
            })
    result.sort(key=lambda row: (row["proto"], row["local_port"]))
    return result


def conntrack():
    """Host conntrack rows, or [] when the table is not exposed.

    A line looks like:
        ipv4 2 tcp 6 431996 ESTABLISHED src=192.168.1.13 dst=93.184.216.34 \
sport=51000 dport=443 [ASSURED] mark=0
    so the tuple header is positional and only the trailing attributes are
    key=value. /proc/net/nf_conntrack exists only when nf_conntrack is loaded,
    and this host exposes neither it nor the conntrack binary, so [] is the
    normal answer rather than a fabricated list.
    """
    text = _read_net("nf_conntrack")
    rows = []
    for line in (text or "").splitlines():
        fields = line.split()
        if not fields:
            continue
        entry = {"proto": "", "state": "", "src_ip": "", "src_port": 0,
                 "dst_ip": "", "dst_port": 0}
        # ipproto is the second column, the state the last positional one
        # before the attributes begin.
        entry["proto"] = fields[2] if len(fields) > 2 else ""
        for field in fields:
            key, sep, value = field.partition("=")
            if not sep:
                # The only bare token that is a state; the rest are numbers or
                # bracketed flags.
                if field.isalpha() and field.upper() in TCP_STATES.values():
                    entry["state"] = field.upper()
                continue
            if key == "sport" or key == "dport":
                try:
                    entry["src_port" if key == "sport" else "dst_port"] = int(value, 16)
                except ValueError:
                    pass
            elif key in ("src", "dst"):
                entry["src_ip" if key == "src" else "dst_ip"] = value
        rows.append(entry)
    return rows