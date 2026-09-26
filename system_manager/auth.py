"""Access-code authentication and approved actions for the dashboard.

Ported from the standalone ``server.py`` handler so the Flask app exposes the
same single-use approval flow: services, NetworkManager profiles, preview ->
approve -> execute -> verify, backed by a SQLite audit log.

Auth is intentionally disabled by default for local development (same as
``server.py`` with ``DISABLE_AUTH=1``). Enable by setting ``AUTH_TOKEN_PATH``
to a writable file path; the server writes a fresh random access code there
and each login session lasts eight hours, mirroring the standalone panel.
"""
import hashlib
import json
import os
import secrets
import sqlite3
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request

__all__ = ["auth_blueprint", "Security"]


DISABLE_AUTH_ENV = "SYSTEM_MANAGER_DISABLE_AUTH"
TOKEN_PATH_ENV = "SYSTEM_MANAGER_AUTH_TOKEN_PATH"
CRITICAL_SERVICES = {
    "systemd-journald.service", "systemd-logind.service", "systemd-resolved.service",
    "systemd-udevd.service", "dbus.service", "polkit.service", "network-manager.service",
    "ssh.service", "sshd.service", "systemd-timesyncd.service", "systemd-user-sessions.service",
}
AUTH_TOKEN_NAME = "sm_session"
SESSION_TTL = 8 * 3600
LOGIN_BLOCK_AFTER = 5
LOGIN_BLOCK_SECONDS = 30


def auth_disabled():
    value = current_app.config.get("DISABLE_AUTH")
    # Accept bool True/False and integer 0/1; anything else (including ""/None) means enabled.
    return value is True or value == 1


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def command_output(argv, timeout=2, limit=2048):
    """Run a fixed-argv command and return its stdout, or None on failure."""
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                                env={"PATH": "/usr/bin", "LC_ALL": "C"})
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode:
        return None
    return result.stdout


def observed(value, **extra):
    return {"state": "observed", "value": value, **extra}


def unavailable(detail="This reading is not available on this system."):
    return {"state": "unavailable", "value": None, "detail": detail}


class ActionAudit:
    """SQLite append-only log of executed actions (parameterized queries only)."""

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
                db.execute(
                    "INSERT INTO actions (ts, token_hash, action_type, parameters, preconditions, result, verification) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (time.time(), token_hash, action_type,
                     json.dumps(parameters), json.dumps(preconditions),
                     json.dumps(result), json.dumps(verification)))

    def recent(self, limit=50):
        with self.lock:
            with sqlite3.connect(self.path) as db:
                db.row_factory = sqlite3.Row
                return [dict(row) for row in db.execute(
                    "SELECT * FROM actions ORDER BY ts DESC LIMIT ?", (limit,))]


class Approval:
    """In-memory single-use, expiring approval tokens bound to exact parameters."""

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
        self.approvals = {key: value for key, value in self.approvals.items()
                          if not value["used"] and value["expires"] > now}


class Security:
    """Encapsulates sessions, access code issuing, and request gating.

    Session validation lives on a value object rather than a request-bound
    object so the Flask blueprint routes stay thin and testable.
    """

    AUTH_TOKEN = AUTH_TOKEN_NAME
    SESSION_TTL = SESSION_TTL
    LOGIN_BLOCK_AFTER = LOGIN_BLOCK_AFTER
    LOGIN_BLOCK_SECONDS = LOGIN_BLOCK_SECONDS

    def __init__(self):
        self.lock = threading.Lock()
        self.credential_path = None
        self.credential = None
        self.credential_error = None
        self.sessions = {}
        self.failed_logins = 0
        self.login_blocked_until = 0
        token_path = os.environ.get(TOKEN_PATH_ENV, "")
        if token_path:
            self.issue_credential(Path(token_path))

    def issue_credential(self, path):
        try:
            credential = secrets.token_urlsafe(32)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as output:
                output.write(credential + "\n")
            self.credential_path = path
            self.credential = credential
            self.credential_error = None
        except OSError as error:
            # Without a code no one can log in, so say why instead of failing
            # every attempt as "incorrect access code".
            self.credential = None
            self.credential_path = None
            self.credential_error = f"Could not write the access code to {path}: {error.strerror or error}."

    def token_from(self, request):
        return request.cookies.get(self.AUTH_TOKEN, "") or ""

    def session_valid(self, token):
        if auth_disabled():
            return True
        hashed = _hash(token)
        with self.lock:
            self.sessions = {key: expiry for key, expiry in self.sessions.items() if expiry > time.monotonic()}
            return self.sessions.get(hashed, 0) > time.monotonic()

    def login(self, code):
        if auth_disabled():
            return {"ok": True}
        if not isinstance(code, str) or not code.isascii():
            raise ValueError("Enter a valid local access code.")
        with self.lock:
            now = time.monotonic()
            if now < self.login_blocked_until:
                raise PermissionError("Too many attempts. Wait 30 seconds before trying again.")
            if self.credential is None or not secrets.compare_digest(code, self.credential):
                self.failed_logins += 1
                if self.failed_logins >= self.LOGIN_BLOCK_AFTER:
                    self.login_blocked_until = now + self.LOGIN_BLOCK_SECONDS
                    self.failed_logins = 0
                raise PermissionError("Incorrect access code. Use the current code from the local credential file.")
            self.failed_logins = 0
            token = secrets.token_urlsafe(32)
            self.sessions[_hash(token)] = now + self.SESSION_TTL
            if self.credential_path is not None:
                self.issue_credential(self.credential_path)
            else:
                self.credential = secrets.token_urlsafe(32)
            return {"ok": True, "session_token": token}

    def logout(self, token):
        with self.lock:
            self.sessions.pop(_hash(token), None)


def require_session():
    security = current_app.config["SECURITY"]
    if not security.session_valid(security.token_from(request)):
        return None
    return security


def _log(source, action_type, parameters, preconditions, result, verification):
    audit = current_app.config.get("AUDIT")
    if audit is None:
        return
    token = current_app.config["SECURITY"].token_from(request)
    audit.record(_hash(token), action_type, parameters, preconditions, result, verification)


_ACTIONS = {
    "service_restart": "restart",
    "service_start": "start",
}


def service_preconditions(name):
    if name in CRITICAL_SERVICES:
        return False, "This service is critical and cannot be managed here."
    return True, None


def service_execute(action, name):
    try:
        result = subprocess.run(["/usr/bin/systemctl", "--user", action, name],
                                capture_output=True, text=True, timeout=15,
                                env={"PATH": "/usr/bin", "LC_ALL": "C"})
        return {"code": result.returncode, "stdout": result.stdout[:2048], "stderr": result.stderr[:2048]}
    except (OSError, subprocess.TimeoutExpired):
        return {"code": -1, "stdout": "", "stderr": "Execution timed out or failed."}


def service_verify(name):
    try:
        result = subprocess.run(["/usr/bin/systemctl", "--user", "is-active", name],
                                capture_output=True, text=True, timeout=3,
                                env={"PATH": "/usr/bin", "LC_ALL": "C"})
        return observed({"active": result.returncode == 0})
    except (OSError, subprocess.TimeoutExpired):
        return unavailable("Could not verify service state.")


def user_services():
    services = command_output(["/usr/bin/systemctl", "--user", "list-units",
                               "--type=service", "--no-legend", "--plain"], timeout=5)
    if services is None:
        return unavailable("Could not list user services.")
    result = []
    for line in services.splitlines():
        parts = line.split(None, 4)
        if len(parts) < 5:
            continue
        name, load, active, sub, description = parts
        if name not in CRITICAL_SERVICES:
            result.append({"name": name, "load": load, "active": active, "sub": sub, "description": description})
    return observed(result)


def nm_profiles():
    profiles = command_output(["/usr/bin/nmcli", "-t", "-f", "NAME,TYPE,DEVICE",
                               "con", "show"], timeout=5)
    if profiles is None:
        return unavailable("Could not list NetworkManager profiles.")
    result = []
    for line in profiles.strip().splitlines():
        parts = line.split(":")
        if len(parts) >= 3 and parts[1] in {"wireless", "vpn", "ethernet", "bridge", "bond", "team", "vlan"}:
            result.append({"name": parts[0], "type": parts[1], "device": parts[2] or None})
    return observed(result)


def nm_checkpoint():
    checkpoint = command_output(["/usr/bin/nmcli", "con", "checkpoint"], timeout=5)
    if checkpoint is not None and checkpoint.strip():
        return observed(checkpoint.strip())
    return unavailable("Checkpoint not supported or failed.")


def nm_rollback(checkpoint):
    try:
        result = subprocess.run(["/usr/bin/nmcli", "con", "rollback", checkpoint],
                                capture_output=True, text=True, timeout=10,
                                env={"PATH": "/usr/bin", "LC_ALL": "C"})
        return observed({"rollback": result.returncode == 0})
    except (OSError, subprocess.TimeoutExpired):
        return unavailable("Rollback failed.")


def nm_activate(profile):
    try:
        result = subprocess.run(["/usr/bin/nmcli", "con", "up", profile],
                                capture_output=True, text=True, timeout=20,
                                env={"PATH": "/usr/bin", "LC_ALL": "C"})
        return {"code": result.returncode, "stdout": result.stdout[:2048], "stderr": result.stderr[:2048]}
    except (OSError, subprocess.TimeoutExpired):
        return {"code": -1, "stdout": "", "stderr": "Activation timed out or failed."}


def nm_verify(profile):
    data = command_output(["/usr/bin/nmcli", "-t", "-f", "NAME,DEVICE",
                           "con", "show", "--active"], timeout=3)
    if data is None:
        return unavailable("Could not verify connection state.")
    return observed({"active": profile in data})


def nm_preconditions(profile):
    checkpoint = nm_checkpoint()
    if checkpoint["state"] != "observed":
        return False, "NetworkManager checkpoint not available; safe rollback cannot be guaranteed."
    return True, checkpoint["value"]


def _authorize():
    return jsonify({"error": "Unlock this dashboard with the local access code."}), 401


def requires_session_view():
    """Gate a module blueprint when auth is on.

    Browser navigations are redirected to the dashboard, which hosts the unlock
    form; fetch/JSON callers get a 401 so the client can report the error.
    Scope this to a blueprint rather than the app so the dashboard, login, and
    static assets stay reachable and the unlock form is never gated itself.
    """
    if auth_disabled():
        return None
    if require_session() is not None:
        return None
    if "application/json" in (request.headers.get("Accept") or ""):
        return _authorize()
    from flask import redirect, url_for
    return redirect(url_for("index"))


AUTH_PREFIX = "/api"


def auth_blueprint():
    bp = Blueprint("auth", __name__, url_prefix=AUTH_PREFIX)

    @bp.post("/login")
    def login():
        if auth_disabled():
            return jsonify({"ok": True})
        security = current_app.config["SECURITY"]
        body = request.get_json(silent=True) or {}
        if security.credential_error:
            return jsonify({"error": security.credential_error}), 500
        try:
            result = security.login(body.get("code"))
        except PermissionError as error:
            return jsonify({"error": str(error)}), 401
        except ValueError as error:
            return jsonify({"error": str(error)}), 400
        from flask import make_response
        response = make_response(jsonify({"ok": True}))
        response.set_cookie(AUTH_TOKEN_NAME, result["session_token"],
                            httponly=True, samesite="Strict", max_age=SESSION_TTL)
        return response

    @bp.post("/logout")
    def logout():
        security = current_app.config["SECURITY"]
        security.logout(security.token_from(request))
        return jsonify({"ok": True})

    @bp.get("/services")
    def services():
        if require_session() is None:
            return _authorize()
        return jsonify(user_services())

    @bp.get("/profiles")
    def profiles():
        if require_session() is None:
            return _authorize()
        return jsonify(nm_profiles())

    @bp.get("/audit")
    def audit():
        if require_session() is None:
            return _authorize()
        current = current_app.config.get("AUDIT")
        return jsonify({"actions": current.recent() if current is not None else []})

    @bp.post("/approve")
    def approve():
        if require_session() is None:
            return _authorize()
        body = request.get_json(silent=True) or {}
        action_type = body.get("action_type")
        parameters = body.get("parameters", {})
        if action_type not in set(_ACTIONS) | {"nm_activate"} or not isinstance(parameters, dict):
            return jsonify({"error": "Unknown action type."}), 400
        security = current_app.config["SECURITY"]
        if action_type in _ACTIONS:
            name = parameters.get("name")
            if not isinstance(name, str) or not name.endswith(".service"):
                return jsonify({"error": "Invalid service name."}), 400
            ok, reason = service_preconditions(name)
            preconditions = {"name": name, "action": _ACTIONS[action_type]}
            if not ok:
                return jsonify({"error": reason, "preconditions": preconditions}), 400
            approval_token = security.approval.issue(
                _hash(security.token_from(request)), action_type, parameters, preconditions)
            return jsonify({"approval_token": approval_token,
                            "preconditions": preconditions, "expires_in": 300}), 200
        name = parameters.get("name")
        if not isinstance(name, str):
            return jsonify({"error": "Invalid profile name."}), 400
        ok, checkpoint = nm_preconditions(name)
        preconditions = {"name": name, "checkpoint": checkpoint}
        if not ok:
            return jsonify({"error": checkpoint, "preconditions": preconditions}), 400
        approval_token = security.approval.issue(
            _hash(security.token_from(request)), action_type, parameters, preconditions)
        return jsonify({"approval_token": approval_token,
                        "preconditions": preconditions, "expires_in": 300}), 200

    @bp.post("/execute")
    def execute():
        if require_session() is None:
            return _authorize()
        security = current_app.config["SECURITY"]
        body = request.get_json(silent=True) or {}
        approval_token = body.get("approval_token")
        if not isinstance(approval_token, str):
            return jsonify({"error": "Approval token required."}), 400
        approval = security.approval.consume(approval_token)
        if not approval:
            return jsonify({"error": "Invalid or expired approval token."}), 400
        action_type = approval["action_type"]
        parameters = approval["parameters"]
        preconditions = approval["preconditions"]
        token_hash = approval["token_hash"]
        if action_type in _ACTIONS:
            name = parameters["name"]
            ok, _ = service_preconditions(name)
            if not ok:
                return jsonify({"error": "Preconditions no longer met."}), 400
            result = service_execute(_ACTIONS[action_type], name)
            verification = service_verify(name)
            outcome = "success" if result["code"] == 0 else "failure"
            _log(auth_blueprint, action_type, parameters, preconditions, result, verification)
            return jsonify({"outcome": outcome, "result": result, "verification": verification}), 200
        name = parameters["name"]
        ok, checkpoint = nm_preconditions(name)
        if not ok:
            return jsonify({"error": "Preconditions no longer met: " + checkpoint}), 400
        if checkpoint != preconditions.get("checkpoint"):
            return jsonify({"error": "Network state changed; new approval required."}), 400
        result = nm_activate(name)
        verification = nm_verify(name)
        outcome = "success" if result["code"] == 0 else "failure"
        _log(auth_blueprint, action_type, parameters, preconditions, result, verification)
        if result["code"] != 0 and checkpoint:
            nm_rollback(checkpoint)
        return jsonify({"outcome": outcome, "result": result, "verification": verification}), 200

    return bp