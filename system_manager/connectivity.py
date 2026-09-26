"""Opt-in connectivity diagnostics for the dashboard.

Ported from the standalone ``server.py`` panel: the user chooses an approved
HTTPS endpoint, then gateway -> DNS -> HTTPS probes run on a fixed cadence.
The probe and diagnosis logic lives in ``server.py`` and is reused through the
same shared import as ``status.py``; this module owns the configuration and
locking. Nothing here writes to the system and outbound probes run only
against the approved endpoint, the default gateway, and the configured DNS
server -- after the user explicitly enables them.
"""
import threading
import time

from flask import Blueprint, current_app, jsonify, request

from . import auth, status

__all__ = ["ConnectivityStore", "connectivity_blueprint"]

CONNECT_INTERVAL = 60


def _collector():
    """The shared ``server.py`` module (cached by ``status._load``)."""
    module, _ = status._load()
    return module


def _settings(config):
    return _collector().connectivity_settings(config)


def _checks(config):
    return _collector().connectivity_checks(config)


def _diagnosis(checks, config):
    return _collector().connectivity_diagnosis(checks, config)


def _valid_endpoint(url):
    return _collector().valid_endpoint(url)


class ConnectivityStore:
    """Owns the connectivity configuration and runs scans when due.

    Mirrors the standalone ``server.SnapshotCache``: scans run inline under a
    lock whenever the enabled config has no result newer than ``CONNECT_INTERVAL``.
    No background thread; under gunicorn each worker holds its own store and
    scans independently, which matches the standalone panel.
    """

    def __init__(self, clock=time.monotonic):
        self.lock = threading.Lock()
        self.clock = clock
        self.config = {
            "enabled": False,
            "endpoints": ["https://example.com/"],
            "destination": {"host": "example.com", "port": 443},
            "last_run": None,
            "checks": {},
        }
        self.config["status"], self.config["explanations"] = _diagnosis({}, self.config)

    def set_config(self, enabled, endpoints, destination):
        with self.lock:
            self.config.update({
                "enabled": enabled,
                "endpoints": endpoints,
                "destination": destination,
                "last_run": None,
                "checks": {},
            })
            if not enabled:
                self.config["status"], self.config["explanations"] = _diagnosis({}, self.config)
            else:
                self.config["status"] = "not tested"
                self.config["explanations"] = [
                    "Outbound checks are enabled. The first gateway, DNS, and endpoint results appear on the next refresh."]

    def settings(self):
        with self.lock:
            return {**_settings(self.config),
                    "status": self.config["status"],
                    "checks": self.config["checks"],
                    "explanations": self.config["explanations"]}

    def get(self):
        """A status snapshot with connectivity fields attached; scans when due."""
        result = status.collect()
        data = result.get("data")
        with self.lock:
            due = self.config["enabled"] and (
                self.config["last_run"] is None
                or self.clock() - self.config["last_run"] >= CONNECT_INTERVAL)
            if due and data is not None:
                config = {**self.config,
                          "routes": data["routes"], "nameservers": data["nameservers"]}
                self.config["checks"] = _checks(config)
                self.config["status"], self.config["explanations"] = _diagnosis(
                    self.config["checks"], config)
                self.config["last_run"] = self.clock()
            result["connectivity"] = _settings(self.config)
            result["checks"] = self.config["checks"]
            result["explanations"] = self.config["explanations"]
        return result


def connectivity_blueprint():
    bp = Blueprint("connectivity", __name__, url_prefix="/api")

    @bp.get("/connectivity")
    def get_connectivity():
        if auth.require_session() is None:
            return auth._authorize()
        return jsonify(current_app.config["CONNECTIVITY"].settings())

    @bp.post("/connectivity")
    def set_connectivity():
        if auth.require_session() is None:
            return auth._authorize()
        body = request.get_json(silent=True) or {}
        enabled = body.get("enabled")
        if not isinstance(enabled, bool):
            return jsonify({"error": "Set enabled to true or false."}), 400
        store = current_app.config["CONNECTIVITY"]
        if not enabled:
            # Turning checks off must always succeed, whatever the box holds:
            # the last approved endpoint is kept, so nothing is contacted again.
            store.set_config(False, store.config["endpoints"], store.config["destination"])
            return jsonify(store.settings())
        endpoints = body.get("endpoints", ["https://example.com/"])
        targets = ([_valid_endpoint(url) for url in endpoints]
                   if isinstance(endpoints, list) and 0 < len(endpoints) <= 3 else [])
        if not targets or any(target is None for target in targets):
            return jsonify({
                "error": "Provide up to 3 HTTPS addresses such as https://example.com/ with no query or credentials.",
            }), 400
        store.set_config(True, endpoints, targets[0])
        return jsonify(store.settings())

    return bp