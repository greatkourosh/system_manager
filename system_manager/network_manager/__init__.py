"""Network module for System Manager.

Mounts at /network. Read-only host network inspection: interfaces, routes,
DNS, listening ports, conntrack. No changes, no approval token.

The data comes from procfs rather than `ip`/`ss`, because inside a container
those report the container's own stack. See hostnet for why neither
nsenter nor a plain /host/proc bind mount can fix that.
"""
import os

from flask import Blueprint

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

    True whenever there is a procfs net directory to parse, which is every
    Linux kernel, container or not.
    """
    from . import hostnet
    return hostnet.net_dir() is not None