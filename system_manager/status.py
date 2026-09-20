"""Live system status for the System Manager dashboard.

Reuses the read-only collectors from the sibling ``server.py`` so the
dashboard and the standalone panel report identical readings. Nothing here
writes to the system: every value is either observed from /proc, /sys, or a
whitelisted ``ip`` subcommand.
"""
import importlib.util
import os
import sys

__all__ = ["collect", "history"]

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SERVER = os.path.join(_BASE, "server.py")

_MODULE = None
_ERROR = None


def _load():
    """Import server.py once; its collectors are pure reads."""
    global _MODULE, _ERROR
    if _MODULE is not None or _ERROR is not None:
        return _MODULE, _ERROR
    if not os.path.isfile(_SERVER):
        _ERROR = f"collector not found: {_SERVER}"
        return None, _ERROR
    try:
        spec = importlib.util.spec_from_file_location("sm_collector", _SERVER)
        module = importlib.util.module_from_spec(spec)
        sys.modules["sm_collector"] = module
        spec.loader.exec_module(module)
        _MODULE = module
    except Exception as exc:
        _ERROR = f"collector import failed: {exc}"
    return _MODULE, _ERROR


def collect():
    """Return the current snapshot, or an unavailable marker."""
    module, error = _load()
    if module is None:
        return {"state": "unavailable", "detail": error, "data": None}
    try:
        data = module.snapshot()
    except Exception as exc:
        return {"state": "unavailable", "detail": f"collection failed: {exc}", "data": None}
    return {"state": "observed", "detail": None, "data": data}


def _value(section, key=None):
    """Unwrap a reading into a plain value or None."""
    node = section.get(key) if key else section
    if not isinstance(node, dict):
        return None
    return node.get("value") if node.get("state") == "observed" else None


def history():
    """Chart-ready series derived from the current snapshot.

    Only instantaneous readings exist today, so each series has a single
    point. The shape is what the sparkline component expects, which keeps
    the template unchanged when a rolling buffer is added later.
    """
    snap = collect()
    data = snap.get("data") or {}
    memory = data.get("memory", {})
    total = _value(memory, "MemTotal")
    available = _value(memory, "MemAvailable")
    used = (total - available) if (total is not None and available is not None) else None
    disk = _value(data, "filesystem")
    return {
        "memory_used": [used] if used is not None else [],
        "memory_total": total,
        "disk_used": [disk["total"] - disk["available"]] if disk else [],
        "disk_total": disk["total"] if disk else None,
        "load": _value(data.get("system", {}), "load") or [],
        "time": [data.get("observed_at")] if data.get("observed_at") else [],
    }
