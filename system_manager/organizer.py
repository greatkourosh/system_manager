"""Load the folder_organizer project as a System Manager module.

The organizer code lives in its own checkout and is never copied or modified.
Its Flask app is imported and re-mounted under the ``/organizer`` prefix so
its routes, templates and data stay exactly where they already are.
"""
import importlib.util
import os
import re
import sys

from flask import Blueprint, Response

__all__ = ["organizer_blueprint", "ORGANIZER_PATH", "PREFIX", "is_available"]

PREFIX = "/organizer"

# Sibling checkout by default; override with ORGANIZER_PATH for other layouts.
ORGANIZER_PATH = os.environ.get(
    "ORGANIZER_PATH",
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                 "folder_organizer"),
)
ORGANIZER_ENTRY = "app.py"


def _load_module():
    """Import folder_organizer/app.py under a private module name."""
    source = os.path.join(ORGANIZER_PATH, ORGANIZER_ENTRY)
    if not os.path.isfile(source):
        return None, f"organizer entry not found: {source}"

    # app.py resolves its data dir relative to __file__, so keep both on sys.path.
    if ORGANIZER_PATH not in sys.path:
        sys.path.insert(0, ORGANIZER_PATH)

    name = "organizer_app"
    if name in sys.modules:
        return sys.modules[name], None

    spec = importlib.util.spec_from_file_location(name, source)
    if spec is None or spec.loader is None:
        return None, f"cannot load module from: {source}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # surface the real import failure to the caller
        del sys.modules[name]
        return None, f"organizer import failed: {exc}"
    return module, None


def organizer_blueprint():
    """Return a blueprint exposing the organizer app, or one that reports why not."""
    module, error = _load_module()
    bp = Blueprint("organizer", __name__, url_prefix=PREFIX)

    if module is None:
        @bp.route("/")
        @bp.route("/<path:subpath>")
        def unavailable(subpath=""):
            return ({"module": "folder_organizer", "status": "unavailable",
                     "detail": error, "expected_path": ORGANIZER_PATH}, 503)

        return bp

    inner = module.app

    @bp.route("/")
    @bp.route("/<path:subpath>", methods=["GET", "POST", "DELETE", "PUT"])
    def proxy(subpath=""):
        path = f"/{subpath}" if subpath else "/"
        with inner.request_context(_make_environ(path)):
            response = inner.full_dispatch_request()
        # Rewrite absolute URLs in HTML responses
        if response.mimetype and response.mimetype.startswith("text/html"):
            response.set_data(_rewrite_absolute_urls(response.get_data()))
        return response

    return bp


def _make_environ(path):
    """Build a WSGI environ for the inner app from the current request."""
    from flask import request

    environ = request.environ.copy()
    environ["PATH_INFO"] = path
    environ["SCRIPT_NAME"] = ""
    return environ


def is_available():
    """Quick probe without running the full import (used for dashboard)."""
    return os.path.isfile(os.path.join(ORGANIZER_PATH, ORGANIZER_ENTRY))


_HREF_RE = re.compile(rb'(href|action|src)="(/[^"]*)"', re.IGNORECASE)


def _rewrite_absolute_urls(html: bytes) -> bytes:
    """Prefix absolute URLs in HTML with /organizer so they work under the mount."""
    # Replace href="/foo" with href="/organizer/foo"
    def repl(match):
        prefix, path = match.group(1), match.group(2)
        return rb'%s="%s%s"' % (prefix, PREFIX.encode(), path)
    return _HREF_RE.sub(repl, html)
