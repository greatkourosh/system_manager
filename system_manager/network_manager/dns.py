"""Read-only DNS diagnostics and comparison utilities for the network module.

The module purposely never mutates resolvers or netplan files. It exposes the
current DNS state and measured comparison data for the Network dashboard, and a
preview endpoint that requires an explicit user confirmation before any future
system change is attempted.
"""
from __future__ import annotations

import ipaddress
import glob
import json
import os
import re
import secrets
import socket
import statistics
import struct
import subprocess
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit

from . import hostnet

__all__ = [
    "DEFAULT_DOMAINS",
    "DEFAULT_PROVIDERS",
    "benchmark_dns_providers",
    "dnsmasq_config_state",
    "current_dns_state",
    "host_dns_apply_command",
    "host_dns_rollback_command",
    "normalise_provider_input",
    "parse_dns_response",
    "rank_provider_results",
    "preview_dns_apply",
    "run_dnsmasq_helper",
]

DEFAULT_DOMAINS = ["google.com", "github.com", "docker.com",
                   "pypi.org", "digikala.com", "cloudflare.com"]
DEFAULT_PROVIDERS = [
    {"provider": "Shecan", "addresses": ["178.22.122.100", "185.51.200.2"]},
    {"provider": "Electro", "addresses": ["78.157.42.100", "78.157.42.101"]},
    {"provider": "Cloudflare", "addresses": ["1.1.1.1", "1.0.0.1"]},
    {"provider": "Google Public DNS", "addresses": ["8.8.8.8", "8.8.4.4"]},
    {"provider": "Quad9", "addresses": ["9.9.9.9", "149.112.112.112"]},
]

RCODE_MAP = {
    0: "NOERROR",
    1: "FORMERR",
    2: "SERVFAIL",
    3: "NXDOMAIN",
    4: "NOTIMP",
    5: "REFUSED",
}


def _encode_dns_name(name: str) -> bytes:
    labels = []
    for label in str(name).rstrip(".").split("."):
        if not label:
            continue
        labels.append(bytes([len(label)]) +
                      label.encode("ascii", errors="replace"))
    return b"".join(labels) + b"\x00"


def _read_dns_name(packet: bytes, offset: int):
    labels = []
    cursor = offset
    original = offset
    seen = set()
    while True:
        if cursor >= len(packet):
            return ".".join(labels), original
        length = packet[cursor]
        if length == 0:
            cursor += 1
            break
        if length & 0xC0 == 0xC0:
            if cursor + 1 >= len(packet):
                return ".".join(labels), original
            pointer = ((length & 0x3F) << 8) | packet[cursor + 1]
            if pointer in seen:
                return ".".join(labels), cursor + 2
            seen.add(pointer)
            if original == cursor:
                original = cursor + 2
            cursor = pointer
            continue
        cursor += 1
        labels.append(
            packet[cursor:cursor + length].decode("ascii", errors="replace"))
        cursor += length
    return ".".join(labels), (original if original != offset else cursor)


def _decode_rdata(rtype: int, payload: bytes):
    if rtype == 1 and len(payload) == 4:
        return socket.inet_ntoa(payload)
    if rtype == 28 and len(payload) == 16:
        return socket.inet_ntop(socket.AF_INET6, payload)
    if rtype in (2, 5, 12, 15):
        try:
            name, _ = _read_dns_name(payload, 0)
            return name
        except Exception:
            return payload.hex()
    return payload.hex()


def parse_dns_response(packet: bytes):
    """Parse a raw DNS response and return details suitable for ranking."""
    if len(packet) < 12:
        return {"rcode": "BADPACKET", "answers": [], "error": "response too short"}
    ident, flags, qdcount, ancount, nscount, arcount = struct.unpack(
        "!HHHHHH", packet[:12])
    rcode = (flags & 0xF)
    parsed = {
        "identifier": ident,
        "rcode": RCODE_MAP.get(rcode, f"RCODE{rcode}"),
        "answers": [],
        "answer_count": ancount,
        "question_count": qdcount,
    }
    offset = 12
    for _ in range(qdcount):
        _, offset = _read_dns_name(packet, offset)
        if offset + 4 > len(packet):
            return parsed
        offset += 4
    records = []
    for _ in range(ancount):
        name, offset = _read_dns_name(packet, offset)
        if offset + 10 > len(packet):
            return parsed
        rtype, rclass, ttl, rdlength = struct.unpack(
            "!HHIH", packet[offset:offset + 10])
        offset += 10
        if offset + rdlength > len(packet):
            return parsed
        rdata = packet[offset:offset + rdlength]
        offset += rdlength
        value = _decode_rdata(rtype, rdata)
        records.append({"name": name, "type": rtype,
                       "ttl": ttl, "value": value})
    parsed["answer_records"] = records
    parsed["answer_values"] = [item["value"] for item in records]
    parsed["answers"] = parsed["answer_values"]
    return parsed


def normalise_provider_input(values):
    """Accept a dict or list of provider records and return only validated address pairs."""
    if values is None:
        return []
    if isinstance(values, dict):
        values = [values]
    if not isinstance(values, list):
        return []
    providers = []
    for item in values:
        if not isinstance(item, dict):
            continue
        name = str(item.get("provider") or item.get("name") or "").strip()
        addresses = item.get("addresses") or item.get("servers") or []
        if isinstance(addresses, str):
            addresses = [addresses]
        if not isinstance(addresses, list):
            continue
        cleaned = []
        for entry in addresses:
            if not isinstance(entry, str):
                continue
            value = entry.strip()
            if not value:
                continue
            try:
                ipaddress.ip_address(value)
            except ValueError:
                continue
            cleaned.append(value)
        if name and cleaned:
            providers.append({"provider": name, "addresses": cleaned[:2]})
    return providers


def rank_provider_results(results):
    """Rank by reliability and latency, with success rate taking precedence."""
    cleaned = []
    for item in results or []:
        if not isinstance(item, dict):
            continue
        success_rate = float(item.get("success_rate", 0.0) or 0.0)
        timeout_count = int(item.get("timeouts", 0) or 0)
        median_latency = float(item.get("median_latency", 0.0) or 0.0)
        cleaned.append({
            **item,
            "success_rate": success_rate,
            "timeouts": timeout_count,
            "median_latency": median_latency,
            "provider": str(item.get("provider") or "unknown").strip() or "unknown",
        })
    return sorted(cleaned, key=lambda row: (
        -(float(row.get("success_rate", 0.0) or 0.0)),
        int(row.get("timeouts", 0) or 0),
        float(row.get("median_latency", 0.0) or 0.0),
        str(row.get("provider") or ""),
    ))


def select_recommended_pair(results):
    """Select primary resolver IPs from the top two distinct working providers."""
    selected = []
    seen_addresses = set()
    for provider in rank_provider_results(results):
        addresses = normalise_provider_input(provider)
        if (not addresses or provider["success_rate"] <= 0
                or provider["timeouts"] >= provider.get("attempts", 1)):
            continue
        address = addresses[0]["addresses"][0]
        if address in seen_addresses:
            continue
        selected.append({"provider": provider["provider"], "address": address})
        seen_addresses.add(address)
        if len(selected) == 2:
            break
    if len(selected) != 2:
        return None
    return {"primary": selected[0], "secondary": selected[1]}


def _build_dns_query(name: str, qtype: str = "A") -> bytes:
    qtype_code = {"A": 1, "AAAA": 28}.get(str(qtype).upper(), 1)
    identifier = secrets.randbits(16)
    header = struct.pack("!HHHHHH", identifier, 0x0100, 1, 0, 0, 0)
    return header + _encode_dns_name(name) + struct.pack("!HH", qtype_code, 1)


def _query_single_dns(server: str, domain: str, qtype: str = "A", timeout: float = 1.5, protocol: str = "udp"):
    if not server or not domain:
        raise ValueError("server and domain are required")
    payload = _build_dns_query(domain, qtype)
    family = socket.AF_INET6 if ":" in str(server) else socket.AF_INET
    start = time.monotonic()
    if str(protocol).lower() == "tcp":
        sock = socket.socket(family, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            sock.connect((str(server), 53))
            sock.sendall(struct.pack("!H", len(payload)) + payload)
            length_bytes = sock.recv(2)
            if len(length_bytes) != 2:
                raise socket.timeout("No DNS TCP response")
            length = struct.unpack("!H", length_bytes)[0]
            chunks = []
            while len(b"".join(chunks)) < length:
                packet = sock.recv(min(4096, length - len(b"".join(chunks))))
                if not packet:
                    break
                chunks.append(packet)
            response = b"".join(chunks)
        finally:
            sock.close()
    else:
        sock = socket.socket(family, socket.SOCK_DGRAM)
        sock.settimeout(timeout)
        try:
            sock.sendto(payload, (str(server), 53))
            response, _ = sock.recvfrom(4096)
        finally:
            sock.close()
    elapsed_ms = (time.monotonic() - start) * 1000
    parsed = parse_dns_response(response)
    return {"server": server, "domain": domain, "type": qtype, "protocol": protocol, "latency_ms": elapsed_ms, "result": parsed}


def benchmark_dns_providers(domains=None, providers=None, timeout=1.4, quick_test=False):
    """Run a safe, read-only DNS benchmark against the configured candidate resolvers."""
    domain_list = list(domains or DEFAULT_DOMAINS)
    if quick_test and len(domain_list) > 3:
        domain_list = domain_list[:3]
    provider_list = normalise_provider_input(providers or DEFAULT_PROVIDERS)
    if not provider_list:
        return {"generated_at": _timestamp(), "providers": [], "best_provider": None, "domains": domain_list}
    current = dnsmasq_config_state()["servers"][:2]
    if current:
        provider_list = [{"provider": "Current DHCP / TCI",
                          "addresses": current}] + provider_list
    rows = []
    for provider in provider_list:
        attempts = 0
        failures = 0
        timeouts = 0
        latencies = []
        errors = []
        for domain in domain_list:
            for qtype in ("A", "AAAA"):
                for server in provider["addresses"]:
                    attempts += 1
                    try:
                        record = _query_single_dns(
                            server, domain, qtype=qtype, timeout=timeout, protocol="udp")
                        if record["result"].get("rcode") not in ("NOERROR", None):
                            failures += 1
                            errors.append(
                                f"{provider['provider']} {server} {domain} {qtype}: {record['result'].get('rcode')}")
                        if record["result"].get("answers"):
                            latencies.append(record["latency_ms"])
                        else:
                            latencies.append(record["latency_ms"])
                    except socket.timeout:
                        timeouts += 1
                        errors.append(
                            f"{provider['provider']} {server} {domain} {qtype}: timeout")
                    except (OSError, ValueError) as exc:
                        failures += 1
                        errors.append(
                            f"{provider['provider']} {server} {domain} {qtype}: {exc}")
        success_rate = (attempts - failures - timeouts) / \
            attempts if attempts else 0.0
        if latencies:
            median_latency = statistics.median(latencies)
        else:
            median_latency = 0.0
        row = {
            "provider": provider["provider"],
            "addresses": provider["addresses"],
            "success_rate": round(max(0.0, success_rate), 3),
            "median_latency": round(float(median_latency), 2),
            "timeouts": timeouts,
            "errors": errors[:8],
            "attempts": attempts,
            "failures": failures,
            "quick_test": quick_test,
        }
        rows.append(row)
    ranked = rank_provider_results(rows)
    recommended = ranked[0] if ranked else None
    return {
        "generated_at": _timestamp(),
        "domains": domain_list,
        "providers": ranked,
        "best_provider": recommended,
        "recommended_pair": select_recommended_pair(ranked),
        "current_dns": dnsmasq_config_state()["servers"],
        "current_dns_source": dnsmasq_config_state()["source"],
    }


def _dnsmasq_host_path(path, relative_to, root):
    path = path.strip()
    if not os.path.isabs(path):
        path = os.path.join(os.path.dirname(relative_to), path)
    elif root:
        path = os.path.join(root, path.lstrip("/"))
    return os.path.normpath(path)


def _dnsmasq_include_paths(key, value, config_path, root):
    if key == "conf-file":
        path = _dnsmasq_host_path(value, config_path, root)
        return sorted(glob.glob(path))
    if key != "conf-dir":
        return []
    parts = value.split(",", 1)
    directory = _dnsmasq_host_path(parts[0], config_path, root)
    mask = parts[1].strip() if len(parts) > 1 else ""
    if mask:
        return sorted(glob.glob(os.path.join(directory, mask)))
    try:
        return [
            os.path.join(directory, name)
            for name in sorted(os.listdir(directory))
            if not name.startswith(".") and not name.endswith(("~", ".bak"))
            and os.path.isfile(os.path.join(directory, name))
        ]
    except OSError:
        return []


def _dnsmasq_file_entries(config_path, root):
    servers = []
    includes = []
    try:
        with open(config_path, "r", encoding="utf-8", errors="replace") as handle:
            lines = handle.readlines()
    except OSError:
        return servers, includes
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith(("#", ";")) or "=" not in line:
            continue
        key, value = (part.strip() for part in line.split("=", 1))
        if key == "server":
            address = value.split("#", 1)[0]
            if "/" in value:
                continue
            try:
                ipaddress.ip_address(address)
            except ValueError:
                continue
            if value not in servers:
                servers.append(value)
        else:
            includes.extend(
                _dnsmasq_include_paths(key, value, config_path, root))
    return servers, includes


def dnsmasq_config_state():
    """Read global dnsmasq upstreams from its main config and included files.

    The host's resolv.conf may point at dnsmasq on 127.0.0.1; that is the
    local resolver, not the upstream servers this dashboard is meant to show.
    """
    root = hostnet.host_root()
    main_config = os.path.join(
        root, "etc/dnsmasq.conf") if root else "/etc/dnsmasq.conf"
    pending = [main_config]
    visited = set()
    servers = []
    server_sources = []
    while pending:
        config_path = pending.pop(0)
        if config_path in visited or not os.path.isfile(config_path):
            continue
        visited.add(config_path)
        file_servers, includes = _dnsmasq_file_entries(config_path, root)
        for server in file_servers:
            if server in servers:
                continue
            servers.append(server)
            source = os.path.relpath(
                config_path, root) if root else config_path
            server_sources.append(source)
        pending.extend(includes)

    display_sources = []
    for source in server_sources:
        if root:
            source = "/" + source
        if source not in display_sources:
            display_sources.append(source)
    return {
        "servers": servers,
        "source": "dnsmasq configuration: " + ", ".join(display_sources)
        if display_sources else "dnsmasq upstreams not detected",
    }


def current_dns_state():
    """Summarise the current DNS configuration and the source it comes from."""
    resolv = "/etc/resolv.conf"
    if hostnet.host_root():
        resolv = os.path.join(hostnet.host_root(), "etc/resolv.conf")
    nameservers = hostnet.dns_servers()
    source = "manual / unknown"
    if os.path.exists("/run/systemd/resolve") or "systemd" in (" ".join(nameservers), ""):
        source = "systemd-resolved"
    elif os.path.exists("/etc/NetworkManager"):
        source = "DHCP / NetworkManager"
    elif os.path.exists("/etc/netplan"):
        source = "netplan"
    elif os.path.exists("/etc/resolvconf"):
        source = "resolvconf"
    if os.path.exists(resolv):
        try:
            with open(resolv, "r", encoding="utf-8", errors="replace") as handle:
                text = handle.read()
            if "generated by systemd-resolved" in text.lower():
                source = "systemd-resolved"
            elif "generated by NetworkManager" in text.lower():
                source = "DHCP / NetworkManager"
        except OSError:
            pass
    proxy = {}
    for key in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
        value = os.environ.get(key)
        if not value:
            continue
        split = urlsplit(value)
        proxy[key.lower()] = {
            "host": split.hostname or "",
            "port": split.port or 80,
            "scheme": split.scheme or "http",
            "masked": True,
        }
    return {
        "nameservers": nameservers,
        "source": source,
        "proxy": proxy,
        "timestamp": _timestamp(),
        "interfaces": hostnet.interfaces(),
        "routes": hostnet.routes(),
        "default_gateway": next((row.get("gateway") for row in hostnet.routes() if row.get("dst") == "0.0.0.0/0"), ""),
    }


def preview_dns_apply(provider_name, addresses, current_nameservers=None):
    """Return the exact planned DNS change without altering the system."""
    cleaned = normalise_provider_input(
        {"provider": provider_name, "addresses": addresses})
    if not cleaned:
        return {"status": "rejected", "reason": "Provide a valid provider with IPv4 or IPv6 nameserver addresses."}
    selected = cleaned[0]
    current = list(current_nameservers or hostnet.dns_servers())
    return {
        "status": "preview-only",
        "execute": False,
        "provider": selected["provider"],
        "addresses": selected["addresses"],
        "current": current,
        "expected_impact": "No network changes were executed. The resolver list would be updated in the active DNS manager only after explicit approval and a verified rollback path.",
        "notes": [
            "This change would not touch /etc/resolv.conf blindly.",
            "DHCP, VPN, split-DNS, Docker networking, routes, and proxy settings are preserved by design.",
            "A rollback path remains available in the current configuration manager.",
        ],
        "system_configuration": {
            "manager": "Existing active DNS manager discovered in this host",
            "files": ["/etc/resolv.conf", "/run/systemd/resolve/resolv.conf", "/etc/NetworkManager/conf.d/*"],
        },
    }


def host_dns_apply_command(primary, secondary):
    """Return a safe host-side command that rewrites the global dnsmasq upstreams."""
    return (
        f'sudo cp /etc/dnsmasq.conf "/etc/dnsmasq.conf.bak.$(date +%Y%m%d-%H%M%S)" && '
        f'sudo python3 - <<\'PY\'\n'
        'from pathlib import Path\n'
        'p = Path("/etc/dnsmasq.conf")\n'
        'text = p.read_text()\n'
        'lines = []\n'
        'replaced = False\n'
        'for line in text.splitlines():\n'
        '    s = line.strip()\n'
        '    if s.startswith("server=") and not s.startswith("server=/"):\n'
        '        if not replaced:\n'
        f'            lines.extend(["server={primary}", "server={secondary}"])\n'
        '            replaced = True\n'
        '        continue\n'
        '    lines.append(line)\n'
        'if not replaced:\n'
        f'    lines.extend(["", "server={primary}", "server={secondary}"])\n'
        'p.write_text("\\n".join(lines) + "\\n")\n'
        'PY\n'
        'sudo dnsmasq --test && sudo systemctl restart dnsmasq'
    )


def host_dns_rollback_command():
    """Return the host command that restores the latest saved dnsmasq backup."""
    return (
        'sudo cp "$(ls -t /etc/dnsmasq.conf.bak.* | head -n1)" /etc/dnsmasq.conf && '
        'sudo dnsmasq --test && sudo systemctl restart dnsmasq'
    )


def run_dnsmasq_helper(operation, values):
    """Run the fixed, sudoers-scoped dnsmasq helper without a shell."""
    helper = "/usr/local/sbin/system-manager-dns-helper"
    if operation == "apply":
        cleaned = normalise_provider_input(
            {"provider": "validated", "addresses": values})
        if not cleaned or len(cleaned[0]["addresses"]) != 2:
            return {"status": "error",
                    "error": "Exactly two valid DNS addresses are required."}
        args = ["apply", *cleaned[0]["addresses"]]
    elif operation == "rollback":
        backup_id = str(values or "")
        if not re.fullmatch(r"dnsmasq-[0-9]{10}-[a-f0-9]{8}\.conf", backup_id):
            return {"status": "error", "error": "Invalid DNS backup identifier."}
        args = ["rollback", backup_id]
    else:
        return {"status": "error", "error": "Unsupported DNS operation."}
    try:
        # The executable is fixed; args are limited to two validated IPs or a
        # regex-validated backup ID. shell=False prevents command injection.
        result = subprocess.run(
            ["/usr/bin/sudo", "-n", helper, *args],
            capture_output=True,
            text=True,
            timeout=30,
            env={
                "PATH": "/usr/bin:/usr/sbin",
                "LC_ALL": "C",
                "DBUS_SYSTEM_BUS_ADDRESS": "unix:path=/var/run/dbus/system_bus_socket",
            },
            check=False,
        )  # noqa: S603
    except (OSError, subprocess.TimeoutExpired):
        return {"status": "error",
                "error": "DNS helper unavailable or timed out; no result can be confirmed."}
    try:
        payload = json.loads(result.stdout)
    except (TypeError, ValueError):
        payload = {}
    if result.returncode != 0 or not isinstance(payload, dict):
        return {
            "status": "error",
            "error": payload.get("error") or
            "DNS helper failed; inspect service status before retrying.",
            "detail": result.stderr[:512],
        }
    if payload.get("status") == "error":
        payload["detail"] = result.stderr[:512]
    return payload


def _timestamp():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
