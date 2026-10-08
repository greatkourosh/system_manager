"""Network module API endpoints. Read-only host network inspection."""
import os
import re
import socket
import subprocess

from flask import Blueprint, jsonify, render_template, request

network_bp = Blueprint("network_api", __name__)

def _run(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        return None

def _ipaddr():
    out = _run(["ip", "-j", "addr"])
    if not out:
        return []
    import json
    try:
        data = json.loads(out)
    except Exception:
        return []
    result = []
    for entry in data:
        ifname = entry.get("ifname", "")
        addr_info = entry.get("addr_info", [])
        ipv4, ipv6 = [], []
        for addr in addr_info:
            local, family = addr.get("local", ""), addr.get("family", "")
            if family == "inet":
                ipv4.append(local)
            elif family == "inet6":
                ipv6.append(local)
        result.append({
            "name": ifname,
            "state": entry.get("operstate", "UNKNOWN"),
            "type": entry.get("link_type", "unknown"),
            "mac": entry.get("address", ""),
            "ipv4": ipv4,
            "ipv6": ipv6,
            "mtu": entry.get("mtu", 0),
            "tx_bytes": entry.get("stats", {}).get("tx_bytes", 0),
            "rx_bytes": entry.get("stats", {}).get("rx_bytes", 0),
        })
    return result

def _iproute():
    out = _run(["ip", "-j", "route"])
    if not out:
        return []
    import json
    try:
        data = json.loads(out)
    except Exception:
        return []
    return data

def _dns_servers():
    resolv = "/etc/resolv.conf"
    hosts_resolv = os.path.join(os.environ.get("NETWORK_HOST_DIR", "/host"), "etc/resolv.conf")
    if os.path.exists(hosts_resolv):
        resolv = hosts_resolv
    servers = []
    try:
        with open(resolv) as f:
            for line in f:
                line = line.strip()
                if line.startswith("nameserver"):
                    servers.append(line.split()[1])
    except Exception:
        pass
    return servers

def _split_addr_port(addr):
    """Split an `ss` Local Address:Port cell into (address, port int).

    ss renders an IPv6 socket as `[addr]%iface:port`, so a plain rpartition on
    the bracketed form leaves the port glued to the scope id and int() raises.
    """
    if addr.startswith("["):
        ip, _, port = addr.rpartition("]:")
        return ip[1:], int(port)
    ip, _, port = addr.rpartition(":")
    return ip, int(port)

def _listening_ports():
    out = _run(["ss", "-tulpn"])
    if not out:
        return []
    lines = out.splitlines()
    result, header = [], True
    for line in lines:
        if header and line.startswith("Netid"):
            header = False
            continue
        if header:
            continue
        parts = line.split()
        if len(parts) < 5:
            continue
        netid, local_addr, prog = parts[0], parts[4], parts[-1]
        try:
            ip, port = _split_addr_port(local_addr)
        except ValueError:
            # A row with no address:port at all (unix sockets) has nothing to
            # show in these columns.
            continue
        pid, exe = None, ""
        if "=" in prog:
            for token in prog.split(","):
                if token.startswith("pid="):
                    pid = int(token.split("=")[1])
                elif token.startswith("exe="):
                    exe = token.split("=")[1]
        result.append({"proto": netid, "local_ip": ip, "local_port": port, "pid": pid, "exe": exe})
    return result

def _conntrack():
    out = _run(["conntrack", "-L"])
    if not out:
        return []
    result = []
    for line in out.splitlines():
        match = re.search(r'(\w+)\s+(\d+)\s+(\d+)\s+(\w+)\s+src=([^\s]+)\s+dst=([^\s]+)\s+sport=(\d+)\s+dport=(\d+)', line)
        if match:
            result.append({
                "proto": match.group(1),
                "protocol_num": int(match.group(2)),
                "timeout": int(match.group(3)),
                "state": match.group(4),
                "src_ip": match.group(5),
                "dst_ip": match.group(6),
                "src_port": int(match.group(7)),
                "dst_port": int(match.group(8)),
            })
    return result

def _hostname():
    return {"hostname": socket.gethostname(), "domain": ""}

@network_bp.route("/summary")
def summary():
    return jsonify({
        "hostname": _hostname(),
        "interfaces": len(_ipaddr()),
        "routes": len(_iproute()),
        "dns_servers": _dns_servers(),
        "listening_ports": len(_listening_ports()),
    })

@network_bp.route("/interfaces")
def interfaces():
    return jsonify(_ipaddr())

@network_bp.route("/routes")
def routes():
    return jsonify(_iproute())

@network_bp.route("/dns")
def dns():
    return jsonify({"servers": _dns_servers()})

@network_bp.route("/ports")
def ports():
    return jsonify(_listening_ports())

@network_bp.route("/conntrack")
def conntrack():
    return jsonify(_conntrack())

@network_bp.route("/")
def index():
    """Network overview page."""
    return render_template("network/index.html")
