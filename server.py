"""Read-only host collectors, connectivity probes and action primitives.

This is a library, not an application. ``system_manager/`` imports
``snapshot()``, the connectivity probes, ``valid_endpoint`` and the
action/audit types; the Flask app in ``system_manager/`` is the only way to
run the dashboard. The stdlib HTTP panel that used to live here was retired on
2026-09-26, so this module has no entry point by design.
"""
import hashlib
import json
import math
import os
from pathlib import Path
import secrets
import selectors
import sqlite3
import socket
import ssl
import subprocess
import sys
import threading
import time
import urllib.parse


if __name__ == "__main__":
    sys.exit("server.py is a collector library. Run 'python3 run.py' for the dashboard.")

INTERVAL = 10
CONNECT_INTERVAL = 60
COMMANDS = {
    "interfaces": ("/usr/sbin/ip", "-j", "address", "show"),
    "routes4": ("/usr/sbin/ip", "-j", "-4", "route", "show", "default"),
    "routes6": ("/usr/sbin/ip", "-j", "-6", "route", "show", "default"),
}


def default_gateway(routes4, routes6):
    for family, routes in (("IPv4", routes4), ("IPv6", routes6)):
        if routes["state"] == "observed":
            for route in routes["value"]:
                if route.get("gateway"):
                    return family, route["gateway"], route.get("dev")
    return None, None, None


def nameservers():
    content = read_text("/etc/resolv.conf")
    if not content:
        return unavailable("No /etc/resolv.conf or unreadable.")
    servers = []
    for line in content.splitlines():
        if line.startswith("nameserver"):
            parts = line.split()
            if len(parts) == 2:
                servers.append(parts[1])
    if not servers:
        return unavailable("No nameservers found in /etc/resolv.conf.")
    return observed(servers)


def ping_host(host, timeout=2, family=socket.AF_INET):
    try:
        sock = socket.socket(family, socket.SOCK_DGRAM)
        sock.settimeout(timeout)
        sock.connect((host, 0))
        sock.close()
        return observed({"host": host, "method": "udp_connect"})
    except (OSError, socket.timeout):
        return unavailable(f"No UDP route to {host} within {timeout}s.")


def resolve_host(name, timeout=3):
    try:
        socket.setdefaulttimeout(timeout)
        infos = socket.getaddrinfo(name, 443, type=socket.SOCK_STREAM)
    except (OSError, socket.timeout, UnicodeError) as failure:
        return unavailable(f"Could not resolve {name}: {type(failure).__name__}.")
    finally:
        socket.setdefaulttimeout(None)
    addresses = sorted({info[4][0] for info in infos})
    return observed({"name": name, "addresses": addresses}) if addresses else unavailable(f"No addresses for {name}.")


def endpoint_reachable(host, port, timeout=4, context=None):
    try:
        with socket.create_connection((host, port), timeout=timeout) as raw:
            if context is None:
                return observed({"host": host, "port": port, "transport": "tcp"})
            with context.wrap_socket(raw, server_hostname=host) as tls:
                return observed({"host": host, "port": port, "transport": "tls",
                                 "peer": tls.getpeercert().get("subjectAltName", [])})
    except (OSError, socket.timeout, ssl.SSLError):
        return unavailable(f"Could not complete a {'TLS' if context else 'TCP'} connection to {host}:{port}.")


def endpoint_addresses(host):
    try:
        return sorted({info[4][0] for info in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)})
    except (OSError, UnicodeError):
        return []


def connectivity_settings(config):
    return {"enabled": config["enabled"], "endpoints": config["endpoints"],
            "destination": config["destination"], "last_run": config["last_run"]}


def connectivity_checks(config):
    if not config["enabled"]:
        return {}
    target = config["destination"]
    gateway_family, gateway, device = default_gateway(config["routes"]["IPv4"], config["routes"]["IPv6"])
    checks = {
        "gateway": observed({"value": gateway, "device": device, "family": gateway_family})
                   if gateway else unavailable("No default gateway is configured for this computer."),
        "gateway_reachable": ping_host(gateway, family=socket.AF_INET6 if gateway_family == "IPv6" else socket.AF_INET)
                             if gateway else unavailable("No default gateway to test."),
        "dns_configured": config["nameservers"],
        "dns_reachable": unavailable("No configured DNS server to test."),
        "dns_resolution": resolve_host(target["host"]),
    }
    servers = config["nameservers"]["value"] if config["nameservers"]["state"] == "observed" else []
    if servers:
        checks["dns_reachable"] = ping_host(servers[0], family=socket.AF_INET6 if ":" in servers[0] else socket.AF_INET)
    context = ssl.create_default_context()
    checks["https"] = endpoint_reachable(target["host"], target["port"], context=context)
    return checks


def connectivity_diagnosis(checks, config):
    if not config["enabled"]:
        return "not tested", ["Outbound checks are off. Enable them to test the gateway, DNS, and an approved endpoint."]
    if checks["gateway"]["state"] == "unavailable" and checks["dns_resolution"]["state"] == "unavailable":
        return "no local connectivity", ["No default gateway or DNS resolution was observed. Check the network connection and router."]
    reachable = [name for name in ("gateway_reachable", "dns_reachable", "https") if checks[name]["state"] == "observed"]
    if checks["dns_resolution"]["state"] == "unavailable":
        return "name resolution failing", ["The endpoint name did not resolve. Check the configured DNS server and the endpoint spelling."]
    if checks["https"]["state"] == "observed":
        return "reachable", [f"TLS connection to {config['destination']['host']} succeeded. Internet access works for this endpoint."]
    if checks["gateway_reachable"]["state"] == "observed":
        return "endpoint unreachable", [f"The local gateway answered but the TLS connection to {config['destination']['host']} failed. The site may be down or blocked."]
    if reachable:
        return "partial connectivity", ["Some checks succeeded while others failed. Review each line below."]
    return "not verified", ["No check succeeded. This may be a local link, router, or DNS problem."]


def read_text(path):
    host_root = os.environ.get("HOST_ROOT", "")
    path_str = str(path)
    if host_root and (path_str.startswith("/proc/") or path_str.startswith("/sys/") or path_str.startswith("/etc/")):
        path_str = os.path.join(host_root, path_str.lstrip("/"))
    try:
        with Path(path_str).open(encoding="utf-8") as source:
            return source.read(65536).strip()
    except (OSError, UnicodeError):
        return None


def observed(value):
    return {"state": "observed", "value": value}


def unavailable(detail="This reading is not available on this system."):
    return {"state": "unavailable", "value": None, "detail": detail}


def command(name, timeout=2, limit=131072):
    process = None
    try:
        process = subprocess.Popen(COMMANDS[name], stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                                   env={"PATH": "/usr/bin:/usr/sbin", "LC_ALL": "C"})
        deadline = time.monotonic() + timeout
        output = bytearray()
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    return unavailable("The local command timed out.")
                chunk = os.read(process.stdout.fileno(), min(8192, limit + 1 - len(output)))
                if not chunk:
                    break
                output.extend(chunk)
                if len(output) > limit:
                    return unavailable("The local command exceeded its output limit.")
        if process.wait(timeout=max(0.001, deadline - time.monotonic())):
            return unavailable("The local command did not succeed.")
        value = json.loads(output)
        if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
            return unavailable("The local command returned an unexpected format.")
        return observed(value)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return unavailable("The local command is missing or returned an invalid reading.")
    finally:
        if process is not None:
            if process.poll() is None:
                process.kill()
            process.wait()
            process.stdout.close()


def memory_reading():
    memory = {}
    for line in (read_text("/proc/meminfo") or "").splitlines():
        key, separator, value = line.partition(":")
        if separator and key in {"MemTotal", "MemAvailable", "SwapTotal", "SwapFree"}:
            try:
                parts = value.split()
                if len(parts) == 2 and parts[1] == "kB" and int(parts[0]) >= 0:
                    memory[key] = int(parts[0]) * 1024
            except ValueError:
                pass
    return {key: observed(memory[key]) if key in memory else unavailable()
            for key in ("MemTotal", "MemAvailable", "SwapTotal", "SwapFree")}


def uptime_reading():
    host_root = os.environ.get("HOST_ROOT", "")
    path = os.path.join(host_root, "proc/uptime") if host_root else "/proc/uptime"
    try:
        value = float((read_text(path) or "").split()[0])
        return observed(value) if math.isfinite(value) and value >= 0 else unavailable()
    except (ValueError, IndexError):
        return unavailable()


def filesystem_reading():
    host_root = os.environ.get("HOST_ROOT", "")
    path = os.path.join(host_root, "/") if host_root else "/"
    try:
        disk = os.statvfs(path)
        return observed({"total": disk.f_blocks * disk.f_frsize,
                         "available": disk.f_bavail * disk.f_frsize})
    except OSError:
        return unavailable()


def suggestions(data):
    result = []
    total = data["memory"]["MemTotal"]["value"]
    available = data["memory"]["MemAvailable"]["value"]
    if total and available is not None and available / total < 0.1:
        result.append({"title": "Memory pressure", "detail": "Less than 10% of memory is available. Review running applications before starting more work."})
    disk = data["filesystem"]["value"]
    if disk and disk["total"] and disk["available"] / disk["total"] < 0.1:
        result.append({"title": "Root filesystem space is low", "detail": "Less than 10% of root filesystem space is available. Review disk usage; nothing will be deleted automatically."})
    routes = data["routes"]
    if all(route["state"] == "observed" and not route["value"] for route in routes.values()):
        result.append({"title": "No default route observed", "detail": "No default route was found in the main IPv4 or IPv6 tables. Policy routing may still provide connectivity. Review Network settings."})
    return result


def snapshot():
    try:
        load = observed(list(os.getloadavg()))
    except OSError:
        load = unavailable()
    os_name = None
    for line in (read_text("/etc/os-release") or "").splitlines():
        if line.startswith("PRETTY_NAME="):
            os_name = line.partition("=")[2].strip('"')
    cpu_model = None
    for line in (read_text("/proc/cpuinfo") or "").splitlines():
        if line.startswith("model name"):
            cpu_model = line.partition(":")[2].strip()
            break
    hardware = {}
    host_root = os.environ.get("HOST_ROOT", "")
    for name, path in {"Manufacturer": "/sys/class/dmi/id/sys_vendor",
                       "Model": "/sys/class/dmi/id/product_name"}.items():
        full_path = os.path.join(host_root, path.lstrip("/")) if host_root else path
        value = read_text(full_path)
        hardware[name] = observed(value) if value else unavailable()
    hardware["Processor"] = observed(cpu_model) if cpu_model else unavailable()
    data = {
        "observed_at": time.time(),
        "system": {"os": observed(os_name) if os_name else unavailable(),
                   "kernel": observed(os.uname().release),
                   "cpu_count": observed(os.cpu_count()) if os.cpu_count() else unavailable(),
                   "load": load, "uptime": uptime_reading()},
        "hardware": hardware,
        "memory": memory_reading(),
        "filesystem": filesystem_reading(),
        "interfaces": command("interfaces"),
        "routes": {"IPv4": command("routes4"), "IPv6": command("routes6")},
        "internet": {"state": "not tested", "detail": "No outbound probes are enabled. Local link state does not establish internet reachability."},
    }
    data["nameservers"] = nameservers()
    data["suggestions"] = suggestions(data)
    return data


class ActionAudit:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.Lock()
        with self.lock:
            with sqlite3.connect(self.path) as db:
                db.execute("""CREATE TABLE IF NOT EXISTS actions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    token_hash TEXT NOT NULL,
                    action_type TEXT NOT NULL,
                    parameters TEXT NOT NULL,
                    preconditions TEXT NOT NULL,
                    result TEXT NOT NULL,
                    verification TEXT
                )""")
                db.execute("CREATE INDEX IF NOT EXISTS idx_actions_ts ON actions(ts)")
                db.execute("CREATE INDEX IF NOT EXISTS idx_actions_token ON actions(token_hash)")

    def record(self, token_hash, action_type, parameters, preconditions, result, verification=None):
        with self.lock:
            with sqlite3.connect(self.path) as db:
                db.execute("INSERT INTO actions (ts, token_hash, action_type, parameters, preconditions, result, verification) VALUES (?, ?, ?, ?, ?, ?, ?)",
                           (time.time(), token_hash, action_type, json.dumps(parameters), json.dumps(preconditions), json.dumps(result), json.dumps(verification)))

    def recent(self, limit=50):
        with self.lock:
            with sqlite3.connect(self.path) as db:
                db.row_factory = sqlite3.Row
                return [dict(row) for row in db.execute("SELECT * FROM actions ORDER BY ts DESC LIMIT ?", (limit,))]


class Approval:
    def __init__(self):
        self.lock = threading.Lock()
        self.approvals = {}

    def issue(self, token_hash, action_type, parameters, preconditions, ttl=300):
        with self.lock:
            self._cleanup()
            token = hashlib.sha256(secrets.token_bytes(16)).hexdigest()
            self.approvals[token] = {
                "token_hash": token_hash,
                "action_type": action_type,
                "parameters": parameters,
                "preconditions": preconditions,
                "expires": time.monotonic() + ttl,
                "used": False,
            }
            return token

    def consume(self, token):
        with self.lock:
            approval = self.approvals.get(token)
            if not approval or approval["used"] or time.monotonic() > approval["expires"]:
                return None
            approval["used"] = True
            self._cleanup()
            return approval

    def _cleanup(self):
        now = time.monotonic()
        self.approvals = {key: value for key, value in self.approvals.items() if not value["used"] and value["expires"] > now}


CRITICAL_SERVICES = {
    "systemd-journald.service", "systemd-logind.service", "systemd-resolved.service",
    "systemd-udevd.service", "dbus.service", "polkit.service", "network-manager.service",
    "ssh.service", "sshd.service", "systemd-timesyncd.service", "systemd-user-sessions.service",
}


def user_services():
    host_root = os.environ.get("HOST_ROOT", "")
    systemctl = os.path.join(host_root, "usr/bin/systemctl") if host_root else "/usr/bin/systemctl"
    try:
        result = subprocess.run([systemctl, "--user", "list-units", "--type=service", "--no-legend", "--plain"],
                                capture_output=True, text=True, timeout=5, env={"PATH": "/usr/bin", "LC_ALL": "C"})
        if result.returncode:
            return observed([])
        services = []
        for line in result.stdout.splitlines():
            parts = line.split(None, 4)
            if len(parts) >= 5:
                name, load, active, sub, description = parts
                if name not in CRITICAL_SERVICES:
                    services.append({"name": name, "load": load, "active": active, "sub": sub, "description": description})
        return observed(services)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return unavailable("Could not list user services.")


def service_preconditions(name):
    if name in CRITICAL_SERVICES:
        return False, "This service is critical and cannot be managed here."
    return True, None


def service_execute(action, name):
    try:
        result = subprocess.run(["/usr/bin/systemctl", "--user", action, name],
                                capture_output=True, text=True, timeout=15, env={"PATH": "/usr/bin", "LC_ALL": "C"})
        return {"code": result.returncode, "stdout": result.stdout[:2048], "stderr": result.stderr[:2048]}
    except (OSError, subprocess.TimeoutExpired):
        return {"code": -1, "stdout": "", "stderr": "Execution timed out or failed."}


def service_verify(name):
    try:
        result = subprocess.run(["/usr/bin/systemctl", "--user", "is-active", name],
                                capture_output=True, text=True, timeout=3, env={"PATH": "/usr/bin", "LC_ALL": "C"})
        return observed({"active": result.returncode == 0})
    except (OSError, subprocess.TimeoutExpired):
        return unavailable("Could not verify service state.")


def nm_profiles():
    try:
        result = subprocess.run(["/usr/bin/nmcli", "-t", "-f", "NAME,TYPE,DEVICE", "con", "show"],
                                capture_output=True, text=True, timeout=5, env={"PATH": "/usr/bin", "LC_ALL": "C"})
        if result.returncode:
            return observed([])
        profiles = []
        for line in result.stdout.strip().splitlines():
            parts = line.split(":")
            if len(parts) >= 3:
                name, ptype, device = parts[0], parts[1], parts[2]
                if ptype in {"wireless", "vpn", "ethernet", "bridge", "bond", "team", "vlan"}:
                    profiles.append({"name": name, "type": ptype, "device": device or None})
        return observed(profiles)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return unavailable("Could not list NetworkManager profiles.")


def nm_checkpoint():
    try:
        result = subprocess.run(["/usr/bin/nmcli", "con", "checkpoint"],
                                capture_output=True, text=True, timeout=5, env={"PATH": "/usr/bin", "LC_ALL": "C"})
        if result.returncode == 0 and result.stdout.strip():
            return observed(result.stdout.strip())
        return unavailable("Checkpoint not supported or failed.")
    except (OSError, subprocess.TimeoutExpired):
        return unavailable("Checkpoint command failed.")


def nm_rollback(checkpoint):
    try:
        result = subprocess.run(["/usr/bin/nmcli", "con", "rollback", checkpoint],
                                capture_output=True, text=True, timeout=10, env={"PATH": "/usr/bin", "LC_ALL": "C"})
        return observed({"rollback": result.returncode == 0})
    except (OSError, subprocess.TimeoutExpired):
        return unavailable("Rollback failed.")


def nm_activate(profile):
    try:
        result = subprocess.run(["/usr/bin/nmcli", "con", "up", profile],
                                capture_output=True, text=True, timeout=20, env={"PATH": "/usr/bin", "LC_ALL": "C"})
        return {"code": result.returncode, "stdout": result.stdout[:2048], "stderr": result.stderr[:2048]}
    except (OSError, subprocess.TimeoutExpired):
        return {"code": -1, "stdout": "", "stderr": "Activation timed out or failed."}


def nm_verify(profile):
    try:
        result = subprocess.run(["/usr/bin/nmcli", "-t", "-f", "NAME,DEVICE", "con", "show", "--active"],
                                capture_output=True, text=True, timeout=3, env={"PATH": "/usr/bin", "LC_ALL": "C"})
        active = profile in result.stdout
        return observed({"active": active})
    except (OSError, subprocess.TimeoutExpired):
        return unavailable("Could not verify connection state.")


def nm_preconditions(profile):
    checkpoint = nm_checkpoint()
    if checkpoint["state"] != "observed":
        return False, "NetworkManager checkpoint not available; safe rollback cannot be guaranteed."
    return True, checkpoint["value"]


class SnapshotCache:
    def __init__(self, collector=snapshot, clock=time.monotonic, connectivity=connectivity_checks,
                 interval=INTERVAL):
        self.collector = collector
        self.clock = clock
        self.connectivity = connectivity
        self.interval = interval
        self.lock = threading.Lock()
        self.value = None
        self.last_attempt = None
        self.error = None
        self.config = {"enabled": False, "endpoints": ["https://example.com/"],
                       "destination": {"host": "example.com", "port": 443}, "last_run": None,
                       "checks": {}, "status": "not tested", "explanations": []}

    def set_config(self, enabled, endpoints):
        with self.lock:
            self.config.update({"enabled": enabled, "endpoints": endpoints, "last_run": None})
            self.last_attempt = None

    def _observe(self):
        try:
            self.value = self.collector()
            self.error = None
        except Exception:
            self.error = "Collection failed. Previous readings may be stale; retry after 10 seconds."
        self.last_attempt = self.clock()

    def get(self):
        with self.lock:
            stale_due = self.last_attempt is None or self.clock() - self.last_attempt >= self.interval
            connectivity_due = self.config["enabled"] and (
                self.config["last_run"] is None or self.clock() - self.config["last_run"] >= CONNECT_INTERVAL)
            if stale_due:
                self._observe()
            if connectivity_due and self.value is not None:
                config = {**self.config, "routes": self.value["routes"], "nameservers": self.value["nameservers"]}
                self.config["checks"] = self.connectivity(config)
                self.config["status"], self.config["explanations"] = connectivity_diagnosis(self.config["checks"], config)
                self.config["last_run"] = self.clock()
            if self.value is None:
                return {"state": "unavailable", "detail": self.error, "data": None, "connectivity": connectivity_settings(self.config)}
            stale = self.error is not None or time.time() - self.value["observed_at"] > 30
            return {"state": "stale" if stale else "observed", "detail": self.error,
                    "data": {**self.value, "connectivity": connectivity_settings(self.config)},
                    "connectivity": connectivity_settings(self.config),
                    "checks": self.config["checks"], "explanations": self.config["explanations"]}


def valid_endpoint(url):
    if not isinstance(url, str) or len(url) > 200 or not url.isascii() or any(c.isspace() or ord(c) < 32 for c in url):
        return None
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.query or parsed.fragment:
        return None
    host, port = parsed.hostname, parsed.port or 443
    if not host or len(host) > 253 or not all(part and len(part) <= 63 for part in host.rstrip(".").split(".")):
        return None
    try:
        if not port.is_integer() or not 1 <= port <= 65535:
            return None
    except AttributeError:
        return None
    if not all(character.isalnum() or character in "-." for character in host):
        return None
    return {"url": url, "host": host, "port": port}
