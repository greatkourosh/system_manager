#!/usr/local/bin/python3
"""Narrow root helper for changing only dnsmasq global upstream resolvers.

This executable is granted through a scoped sudoers rule in the container.
It accepts only apply <primary-ip> <secondary-ip> and rollback <backup-id>.
It never invokes a shell or accepts paths/service names from the caller.
"""
import ipaddress
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import time
from pathlib import Path

CONFIG_PATH = Path("/host/etc/dnsmasq.conf")
BACKUP_DIR = Path("/data/dns-backups")
DNSMASQ = "/usr/sbin/dnsmasq"
BUSCTL = "/usr/bin/busctl"
SERVICE = "dnsmasq.service"
BACKUP_RE = re.compile(r"^dnsmasq-[0-9]{10}-[a-f0-9]{8}\.conf$")
SYSTEM_BUS = "unix:path=/var/run/dbus/system_bus_socket"


def _run(argv, timeout=8):
    return subprocess.run(
        argv,
        capture_output=True,
        text=True,
        timeout=timeout,
        env={"PATH": "/usr/bin:/usr/sbin", "LC_ALL": "C",
             "DBUS_SYSTEM_BUS_ADDRESS": SYSTEM_BUS},
        check=False,
    )


def _validate_addresses(values):
    if len(values) != 2 or values[0] == values[1]:
        raise ValueError(
            "Exactly two distinct DNS server addresses are required.")
    validated = []
    for value in values:
        address = ipaddress.ip_address(value)
        if not address.is_global:
            raise ValueError(
                "DNS server addresses must be public, routable IPs.")
        validated.append(str(address))
    return validated


def _global_server_line(line):
    stripped = line.lstrip()
    if not stripped.startswith("server="):
        return False
    value = stripped[len("server="):].strip()
    return not value.startswith("/")


def replace_global_upstreams(contents, addresses):
    """Replace global server directives while preserving per-domain overrides."""
    lines = contents.splitlines(keepends=True)
    indexes = [index for index, line in enumerate(lines)
               if _global_server_line(line)]
    newline = "\r\n" if any(line.endswith("\r\n") for line in lines) else "\n"
    new_lines = [f"server={address}{newline}" for address in addresses]
    if indexes:
        first = indexes[0]
        lines = [line for index, line in enumerate(
            lines) if index not in indexes]
        lines[first:first] = new_lines
        return "".join(lines)
    if lines and not lines[-1].endswith(("\n", "\r")):
        lines[-1] += newline
    return "".join(lines + new_lines)


def _backup_path(backup_id):
    if not BACKUP_RE.fullmatch(backup_id):
        raise ValueError("Invalid DNS backup identifier.")
    return BACKUP_DIR / backup_id


def _ensure_backup_dir():
    BACKUP_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    if BACKUP_DIR.is_symlink() or not BACKUP_DIR.is_dir():
        raise RuntimeError("DNS backup directory is not a safe directory.")
    os.chmod(BACKUP_DIR, 0o700)


def _host_active_state():
    result = _run([
        BUSCTL, "--system", "get-property", "org.freedesktop.systemd1",
        "/org/freedesktop/systemd1/unit/dnsmasq_2eservice",
        "org.freedesktop.systemd1.Unit", "ActiveState",
    ])
    if result.returncode != 0:
        raise RuntimeError("Could not inspect the host dnsmasq service.")
    match = re.fullmatch(r's "([a-z-]+)"\s*', result.stdout)
    if not match:
        raise RuntimeError(
            "Host dnsmasq returned an unexpected service state.")
    return match.group(1)


def _restart_dnsmasq():
    result = _run([
        BUSCTL, "--system", "call", "org.freedesktop.systemd1",
        "/org/freedesktop/systemd1", "org.freedesktop.systemd1.Manager",
        "RestartUnit", "ss", SERVICE, "replace",
    ], timeout=15)
    if result.returncode != 0:
        raise RuntimeError(
            "Host authorization denied or dnsmasq restart failed.")
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if _host_active_state() == "active":
            return
        time.sleep(0.2)
    raise RuntimeError("dnsmasq did not return to active state after restart.")


def _validate_config(contents):
    """Syntax-check a temporary copy, pointing includes at the mounted host dir."""
    contents = re.sub(
        r"(?m)^(\s*conf-dir=)/etc/dnsmasq\.d(/[^\r\n]*)?$",
        r"\1/host/etc/dnsmasq.d\2",
        contents,
    )
    handle = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", prefix="sm-dnsmasq-", suffix=".conf",
        dir="/run", delete=False,
    )
    try:
        with handle:
            handle.write(contents)
            handle.flush()
            os.fsync(handle.fileno())
        result = _run(
            [DNSMASQ, "--test", f"--conf-file={handle.name}"], timeout=8)
        if result.returncode != 0:
            raise RuntimeError("dnsmasq rejected the proposed configuration.")
    finally:
        try:
            os.unlink(handle.name)
        except OSError:
            pass


def _write_config(contents):
    if CONFIG_PATH.is_symlink() or not CONFIG_PATH.is_file():
        raise RuntimeError(
            "The host dnsmasq config is missing or is not a regular file.")
    with CONFIG_PATH.open("w", encoding="utf-8", newline="") as handle:
        handle.write(contents)
        handle.flush()
        os.fsync(handle.fileno())


def _restore(backup_id):
    backup = _backup_path(backup_id)
    if backup.is_symlink() or not backup.is_file():
        raise RuntimeError("The requested DNS backup does not exist.")
    original = backup.read_text(encoding="utf-8")
    _validate_config(original)
    _write_config(original)
    _restart_dnsmasq()
    return {"status": "rolled-back", "backup_id": backup_id,
            "dnsmasq_state": _host_active_state()}


def _rollback_command(backup_id):
    return ("docker exec system-manager sudo -n "
            f"/usr/local/sbin/system-manager-dns-helper rollback {backup_id}")


def apply_upstreams(values):
    if os.geteuid() != 0:
        raise PermissionError(
            "The DNS helper must run with its scoped root authorization.")
    addresses = _validate_addresses(values)
    if _host_active_state() != "active":
        raise RuntimeError(
            "Host dnsmasq is not active; no DNS change was made.")
    if not CONFIG_PATH.is_file() or CONFIG_PATH.is_symlink():
        raise RuntimeError(
            "The host dnsmasq configuration is unavailable or unsafe.")
    original = CONFIG_PATH.read_text(encoding="utf-8")
    if not re.search(r"(?m)^\s*no-resolv\s*$", original):
        raise RuntimeError(
            "This dnsmasq setup is not the expected no-resolv configuration.")
    proposed = replace_global_upstreams(original, addresses)
    _validate_config(proposed)
    _ensure_backup_dir()
    backup_id = f"dnsmasq-{int(time.time())}-{secrets.token_hex(4)}.conf"
    backup = _backup_path(backup_id)
    backup.write_text(original, encoding="utf-8")
    os.chmod(backup, 0o600)
    try:
        _write_config(proposed)
        _restart_dnsmasq()
    except Exception as error:
        try:
            _write_config(original)
            _restart_dnsmasq()
            rolled_back = True
        except Exception:
            rolled_back = False
        return {
            "status": "error",
            "error": f"DNS apply failed. Cause: {error}",
            "backup_id": backup_id,
            "automatic_rollback": rolled_back,
            "rollback_command": _rollback_command(backup_id),
        }
    return {
        "status": "applied",
        "addresses": addresses,
        "backup_id": backup_id,
        "dnsmasq_state": _host_active_state(),
        "verification": "dnsmasq config syntax passed and host service is active; "
                        "DNS queries cannot be directly verified from the bridged container.",
        "rollback_command": _rollback_command(backup_id),
    }


def main(argv):
    if os.geteuid() != 0:
        raise PermissionError("Root authorization is required.")
    if len(argv) == 3 and argv[0] == "apply":
        return apply_upstreams(argv[1:])
    if len(argv) == 2 and argv[0] == "rollback":
        return _restore(argv[1])
    raise ValueError(
        "Use apply <primary-ip> <secondary-ip> or rollback <backup-id>.")


if __name__ == "__main__":
    try:
        result = main(sys.argv[1:])
        print(json.dumps(result, separators=(",", ":")))
    except Exception as error:
        print(json.dumps(
            {"status": "error", "error": str(error)}, separators=(",", ":")))
        sys.exit(1)
