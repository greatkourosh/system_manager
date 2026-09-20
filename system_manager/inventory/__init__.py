"""Hardware & Components Inventory module for System Manager.

Mounts at /inventory with its own blueprint, templates, and static assets.
"""
from flask import Blueprint

from .api import inventory_bp

__all__ = ["inventory_bp", "PREFIX"]

PREFIX = "/inventory"


def inventory_blueprint():
    """Return the inventory blueprint."""
    bp = Blueprint("inventory", __name__, url_prefix=PREFIX)
    bp.register_blueprint(inventory_bp)
    return bp


def is_available():
    """Quick probe for dashboard."""
    return True