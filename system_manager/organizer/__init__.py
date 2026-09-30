"""Folder Organizer module for System Manager.

Mounts at /organizer. These routes used to be served by importing a sibling
checkout's app.py in-process and rewriting the absolute paths in its HTML; a
second Flask app on one interpreter has to be reached through a proxy, and the
rewrite was the only way to make that work. The routes now live here as a
blueprint, so the paths in the templates are simply right.

The host-side half of the organizer -- the scanner, the subtitle fetcher, the
command runners -- stays in the folder_organizer checkout, which is bind-mounted
for its data/ and commands_to_run/ directories. This module only ever reads the
scan JSON the host-side tools write, except for the tag and folder-fix proposals
it hands back to be applied on the host.
"""
from flask import Blueprint
import os

from .. import auth
from .api import DATA, bp as organizer_bp

__all__ = ["organizer_blueprint", "PREFIX", "is_available"]

PREFIX = "/organizer"
_BASE = os.path.dirname(os.path.abspath(__file__))
_TEMPLATE_FOLDER = os.path.join(_BASE, "templates")


def organizer_blueprint():
    """Return the organizer blueprint."""
    bp = Blueprint("organizer", __name__, url_prefix=PREFIX, template_folder=_TEMPLATE_FOLDER)

    @bp.before_request
    def _require_login():
        return auth.requires_session_view()

    bp.register_blueprint(organizer_bp)
    return bp


def is_available():
    """Quick probe for the dashboard nav.

    Probes the data directory rather than the organizer's old entry point: the
    routes now live in this package, and the only thing that can still be
    missing is the host-side scan data they render.
    """
    return os.path.isdir(DATA)
