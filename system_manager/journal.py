"""Log Analysis & Journal module for System Manager.

Mounts at /logs. Read-only: it queries the host's journal through
``journalctl --root`` and renders the entries. It never vacuums, rotates or
writes, so there is no action needing an approval token -- the same reasoning
that keeps ``auth.Approval`` out of the packages module.
"""
from flask import Blueprint
import os

from . import auth
from .journal_api import journal_bp

__all__ = ["journal_blueprint", "PREFIX", "is_available"]

PREFIX = "/logs"
_BASE = os.path.dirname(os.path.abspath(__file__))
_TEMPLATE_FOLDER = os.path.join(_BASE, "templates")


def _journal_dir():
    return os.path.join(os.environ.get("JOURNAL_HOST_DIR", "/host"), "var/log/journal")


def journal_blueprint():
    """Return the journal blueprint."""
    bp = Blueprint("journal", __name__, url_prefix=PREFIX, template_folder=_TEMPLATE_FOLDER)

    @bp.before_request
    def _require_login():
        return auth.requires_session_view()

    bp.register_blueprint(journal_bp)
    return bp


def is_available():
    """Quick probe for the dashboard nav."""
    return os.path.isdir(_journal_dir())
