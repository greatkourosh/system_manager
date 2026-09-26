"""Hardware & Components Inventory module for System Manager.

Mounts at /inventory with its own blueprint, templates, and static assets.
"""
from flask import Blueprint
import os

from .. import auth
from .api import inventory_bp

__all__ = ["inventory_bp", "PREFIX"]

PREFIX = "/inventory"
_BASE = os.path.dirname(os.path.abspath(__file__))
_TEMPLATE_FOLDER = os.path.join(_BASE, "templates")


def inventory_blueprint():
    """Return the inventory blueprint."""
    bp = Blueprint("inventory", __name__, url_prefix=PREFIX, template_folder=_TEMPLATE_FOLDER)

    @bp.before_request
    def _require_login():
        return auth.requires_session_view()

    bp.register_blueprint(inventory_bp)
    return bp


def is_available():
    """Quick probe for dashboard."""
    return True