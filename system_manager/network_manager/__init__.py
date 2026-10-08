"""Network module for System Manager.

Mounts at /network. Read-only host network inspection: interfaces,
routes, DNS, listening ports, conntrack. No changes, no approval token.
"""
from flask import Blueprint
import os
import shutil

from .. import auth
from .api import network_bp

__all__ = ["network_blueprint", "PREFIX", "is_available"]

PREFIX = "/network"
_BASE = os.path.dirname(os.path.abspath(__file__))
_TEMPLATE_FOLDER = os.path.join(_BASE, "templates")


def network_blueprint():
    """Return the network blueprint."""
    bp = Blueprint("network", __name__, url_prefix=PREFIX, template_folder=_TEMPLATE_FOLDER)

    @bp.before_request
    def _require_login():
        return auth.requires_session_view()

    bp.register_blueprint(network_bp)
    return bp


def is_available():
    """Quick probe for the dashboard nav.

    Degrades gracefully: without iproute2 the API still serves empty
    lists, so the module stays reachable wherever /proc/net exists.
    """
    return bool(shutil.which("ip") or shutil.which("ss") or os.path.exists("/proc/net"))
