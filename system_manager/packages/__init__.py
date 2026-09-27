"""Package & Update Management module for System Manager.

Mounts at /packages. Read-only: it parses the host's dpkg database and apt
indexes and renders the commands to upgrade. It never runs them, and there is
no root helper, so there is no action needing an approval token.
"""
from flask import Blueprint
import os

from .. import auth
from .api import packages_bp
from .dpkg import DPKG_STATUS

__all__ = ["packages_blueprint", "PREFIX", "is_available"]

PREFIX = "/packages"
_BASE = os.path.dirname(os.path.abspath(__file__))
_TEMPLATE_FOLDER = os.path.join(_BASE, "templates")


def packages_blueprint():
    """Return the packages blueprint."""
    bp = Blueprint("packages", __name__, url_prefix=PREFIX, template_folder=_TEMPLATE_FOLDER)

    @bp.before_request
    def _require_login():
        return auth.requires_session_view()

    bp.register_blueprint(packages_bp)
    return bp


def is_available():
    """Quick probe for the dashboard nav."""
    return os.path.isfile(DPKG_STATUS)
