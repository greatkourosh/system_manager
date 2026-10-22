"""The shape of the host's network: what is attached to what.

Three sources, all read-only procfs and sysfs:

- `/host/sys/class/net/<if>/` carries the *structure*. A `brif` directory
  means the interface is a bridge, a `master` symlink means it is enslaved
  to one, and a `device` symlink means real hardware is behind it. procfs
  records none of this, which is why `interfaces()` reports type "unknown"
  for every interface.
- `/host/proc/<pid>/status` carries `NSpid`, the pid as each nested
  namespace sees it. A container's init reads `<host pid> 1`.
- `/host/proc/<pid>/cgroup` names the docker container a pid belongs to,
  and `/host/proc/<pid>/net/fib_trie` holds the addresses of that
  container's network namespace.

What cannot be recovered: the *name* a container was started with. Docker
keeps that in its own database, which is not mounted here, and a
container's own hostname is its id. So containers are labelled by the
process they run, with the container id beside them.

Also unrecoverable: which veth belongs to which container. The container
side of the pair hides its ifindex behind a permission the container
process owns, the host side's MAC is not the container's, and the bridge
forwarding database is not readable. Containers are therefore attached to
the bridge their address routes through, which is a fact the routing table
states, and not to a named veth.
"""
import ipaddress
import os
import re

from . import hostnet

__all__ = ["topology"]

# Docker's cgroup path, in either cgroup version:
#   0::/../docker-<64 hex>.scope   (cgroup v2)
#   12:memory:/docker/<64 hex>     (cgroup v1)
_DOCKER_CGROUP = re.compile(r"docker[-/]([0-9a-f]{64})")
_NSPID = re.compile(r"^NSpid:[ \t]+(\d+(?:[ \t]+\d+)*)$", re.M)
_FIB_NODE = re.compile(r"^\|--\s+(\d+(?:\.\d+){3})$")
_FIB_LOCAL = re.compile(r"^/(\d+)\s+host\s+LOCAL$")

# Interface kinds. `physical` is the only one that implies a cable; `port`
# is anything the kernel keeps enslaved to a bridge.
KIND_LOOPBACK = "loopback"
KIND_BRIDGE = "bridge"
KIND_PHYSICAL = "physical"
KIND_PORT = "port"
KIND_VIRTUAL = "virtual"


def _read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError:
        return ""


def _host_proc(*parts):
    root = hostnet.host_root()
    prefix = os.path.join(root, "proc") if root else "/proc"
    return os.path.join(prefix, *parts)


def _sys_net(iface, *parts):
    root = hostnet.host_root()
    base = os.path.join(root, "sys/class/net") if root else "/sys/class/net"
    return os.path.join(base, iface, *parts)


def _interface_kind(name):
    """How an interface is built, from the symlinks sysfs keeps for it.

    A physical NIC that is enslaved to a bridge is still physical hardware,
    so the `device` symlink is checked before the `master` one.
    """
    if name == "lo":
        return KIND_LOOPBACK
    if os.path.isdir(_sys_net(name, "brif")):
        return KIND_BRIDGE
    # islink, not exists: the `device` symlink is what marks a real NIC, and
    # it is the link itself that carries that fact, not where it points.
    if os.path.islink(_sys_net(name, "device")):
        return KIND_PHYSICAL
    if os.path.islink(_sys_net(name, "master")):
        return KIND_PORT
    return KIND_VIRTUAL


def _bridge_master(name):
    """The bridge an interface is enslaved to, or ""."""
    try:
        return os.path.basename(os.readlink(_sys_net(name, "master")))
    except OSError:
        return ""


def _bridge_members(name):
    """The interfaces enslaved to a bridge."""
    try:
        return sorted(os.listdir(_sys_net(name, "brif")))
    except OSError:
        return []


def _namespace_init_pids():
    """Pids that are the init of a nested namespace.

    `NSpid` lists the pid once per namespace it is visible in, innermost
    last, so a container's init ends in 1. A process that only ever existed
    in one namespace reads a single number and is not an init.
    """
    base = hostnet.host_root()
    prefix = os.path.join(base, "proc") if base else "/proc"
    try:
        pids = sorted(entry for entry in os.listdir(prefix) if entry.isdigit())
    except OSError:
        return []
    found = []
    for pid in pids:
        match = _NSPID.search(_read(os.path.join(prefix, pid, "status")))
        if not match:
            continue
        levels = match.group(1).split()
        if len(levels) > 1 and levels[-1] == "1":
            found.append(pid)
    return found


def _docker_id(pid):
    """The docker container id a pid belongs to, or ""."""
    match = _DOCKER_CGROUP.search(_read(_host_proc(pid, "cgroup")))
    return match.group(1) if match else ""


def _pid_addresses(pid):
    """The IPv4 addresses of a pid's own network namespace."""
    text = _read(_host_proc(pid, "net", "fib_trie"))
    addresses = []
    pending = None
    for line in text.splitlines():
        stripped = line.strip()
        node = _FIB_NODE.match(stripped)
        if node:
            pending = node.group(1)
            continue
        local = _FIB_LOCAL.match(stripped)
        if local and pending:
            # fib_trie also marks a subnet's own address LOCAL under a /8 or
            # a /31, so only the /32s are real interface addresses.
            if int(local.group(1)) == 32:
                addresses.append(pending)
            pending = None
    return sorted(set(addresses))


def _host_addresses():
    """The IPv4 addresses of the host's own network namespace."""
    return set(_pid_addresses("1"))


def _containers():
    """The docker containers this host is running, as the kernel sees them.

    A container started with the host's network namespace is in it too, so
    its addresses are the host's. It is flagged rather than drawn on a
    bridge, because it is not behind one.
    """
    host_addresses = _host_addresses()
    found = {}
    for pid in _namespace_init_pids():
        container_id = _docker_id(pid)
        if not container_id:
            continue
        entry = found.setdefault(container_id, {
            "id": container_id,
            "pid": int(pid),
            "name": _read(_host_proc(pid, "comm")).strip(),
            "command": _read(_host_proc(pid, "cmdline")).replace("\0", " ").strip(),
            "addresses": [],
        })
        for address in _pid_addresses(pid):
            if address.startswith("127.") or address in entry["addresses"]:
                continue
            entry["addresses"].append(address)
    for entry in found.values():
        # Every address it holds is one the host also holds, so it is in the
        # host's namespace rather than on a bridge of the host's.
        entry["host_network"] = bool(
            entry["addresses"] and set(entry["addresses"]) <= host_addresses)
    return sorted(found.values(), key=lambda row: row["id"])


def _device_for(address, routes):
    """The interface a specific subnet routes an address through.

    The default route is skipped: it matches every address, so including it
    would file every container on the uplink. A container is only placed
    behind an interface when a real subnet route says so; one with no such
    route has nowhere to go and is reported as such.
    """
    best = None
    for route in routes:
        if route.get("family") != "inet":
            continue
        if route.get("dst") == "0.0.0.0/0":
            continue
        try:
            network = ipaddress.ip_network(route.get("dst") or "", strict=False)
        except ValueError:
            continue
        if network.version != 4:
            continue
        try:
            if ipaddress.IPv4Address(address) not in network:
                continue
        except ValueError:
            continue
        if best is None or network.prefixlen > best[0].prefixlen:
            best = (network, route.get("dev") or "")
    return best[1] if best else ""


def _default_route(routes):
    """The route that leaves the host, as (device, gateway)."""
    best = None
    for route in routes:
        if route.get("family") != "inet" or route.get("dst") != "0.0.0.0/0":
            continue
        try:
            metric = int(route.get("metric") or 0)
        except (TypeError, ValueError):
            metric = 0
        if best is None or metric < best[0]:
            best = (metric, route.get("dev") or "", route.get("gateway") or "")
    return (best[1], best[2]) if best else ("", "")


def _depths(nodes, links):
    """How far each node sits from the host, by breadth-first walk.

    The graph is a tree rooted at the host, so the first path to a node is
    the shortest one and the depth is simply the number of hops.
    """
    outgoing = {}
    for link in links:
        outgoing.setdefault(link["source"], []).append(link["target"])
    depth = {"host": 0}
    queue = ["host"]
    while queue:
        current = queue.pop(0)
        for target in outgoing.get(current, []):
            if target in depth:
                continue
            depth[target] = depth[current] + 1
            queue.append(target)
    for node in nodes:
        node["depth"] = depth.get(node["id"], 1)


def topology():
    """The host's network, as a graph of nodes and links.

    Nodes are the host, its interfaces, the containers reachable through
    them, and whatever the host routes to by default. Links say which
    interface hangs off the host, which port hangs off which bridge, and
    which container sits behind which bridge.
    """
    if not hostnet.net_dir():
        return {"host": hostnet.hostname(), "scope": hostnet.scoped_label(),
                "nodes": [], "links": [], "containers": 0}

    interfaces = hostnet.interfaces()
    routes = hostnet.routes()
    names = {row["name"] for row in interfaces}
    uplink_device, gateway = _default_route(routes)

    nodes = [{
        "id": "host",
        "kind": "host",
        "label": hostnet.hostname(),
        "detail": hostnet.scoped_label(),
    }]
    links = []

    for row in interfaces:
        name = row["name"]
        kind = _interface_kind(name)
        master = _bridge_master(name)
        node = {
            "id": f"iface:{name}",
            "kind": "interface",
            "subtype": kind,
            "label": name,
            "state": row.get("state") or "unknown",
            "addresses": list(row.get("ipv4") or []),
            "mtu": row.get("mtu") or 0,
            "master": master,
        }
        if kind == KIND_BRIDGE:
            members = _bridge_members(name)
            node["members"] = members
            node["member_count"] = len(members)
        # An enslaved port hangs off its bridge, not off the host; the
        # bridge is the thing that is actually plugged in.
        links.append({
            "source": f"iface:{master}" if master in names else "host",
            "target": node["id"],
            "kind": "attach",
        })
        nodes.append(node)

    # Containers hang off the bridge their address routes through.
    containers = _containers()
    for container in containers:
        node = {
            "id": f"container:{container['id'][:12]}",
            "kind": "container",
            "label": container["name"] or container["id"][:12],
            "container_id": container["id"][:12],
            "pid": container["pid"],
            "addresses": container["addresses"],
            "command": container["command"],
            "device": "",
        }
        if container.get("host_network"):
            # It runs in the host's own namespace, so it is beside the host
            # rather than behind one of its interfaces.
            node["addresses"] = []
            node["host_network"] = True
            node["detail"] = "shares the host's network"
            links.append({"source": "host", "target": node["id"],
                          "kind": "container"})
            nodes.append(node)
            continue
        address = container["addresses"][0] if container["addresses"] else ""
        device = _device_for(address, routes) if address else ""
        node["device"] = device
        if not device:
            # Its address routes nowhere the host knows, so it cannot be
            # placed behind an interface. Draw it beside the host rather
            # than dropping it: a container the user can see running but
            # not see on the graph reads as a bug.
            node["detail"] = "no route to it"
            links.append({"source": "host", "target": node["id"],
                          "kind": "container"})
            nodes.append(node)
            continue
        links.append({
            "source": f"iface:{device}" if device in names else "host",
            "target": node["id"],
            "kind": "container",
        })
        nodes.append(node)

    # What the host talks to by default.
    if uplink_device and uplink_device in names:
        nodes.append({
            "id": "upstream",
            "kind": "upstream",
            "label": "Internet" if gateway else "Default route",
            "gateway": gateway,
            "device": uplink_device,
            "detail": f"via {gateway} on {uplink_device}" if gateway
                      else f"via {uplink_device}",
        })
        links.append({"source": f"iface:{uplink_device}", "target": "upstream",
                      "kind": "upstream"})

    _depths(nodes, links)

    return {
        "host": hostnet.hostname(),
        "scope": hostnet.scoped_label(),
        "nodes": nodes,
        "links": links,
        "containers": len(containers),
    }
