"""System Manager — a Flask dashboard that hosts feature modules."""
import os
import tempfile
from pathlib import Path

from flask import Flask, current_app, jsonify, render_template

from . import auth, status
from .connectivity import ConnectivityStore, connectivity_blueprint
from .organizer import ORGANIZER_PATH, is_available, organizer_blueprint
from .inventory import inventory_blueprint, is_available as inventory_available

__all__ = ["create_app"]


def create_app(config=None):
    app = Flask(__name__, template_folder="../templates", static_folder="../static")
    app.config.update(
        ORGANIZER_PATH=ORGANIZER_PATH,
        DISABLE_AUTH=os.environ.get("DISABLE_AUTH", "0") == "1",
    )
    if config:
        # config overrides env-derived defaults (tests use this to force auth on/off)
        app.config.update({key: value for key, value in config.items()
                           if not key.startswith("SECURITY") and key != "AUDIT"})
    if app.config.get("AUDIT_PATH"):
        app.config["AUDIT_PATH"] = app.config["AUDIT_PATH"]
    else:
        audit_dir = Path(tempfile.mkdtemp(prefix="system-manager-"))
        app.config["AUDIT_PATH"] = str(audit_dir / "actions.db")
        app.config["TEMP_DIR"] = str(audit_dir)

    security = auth.Security()
    security.approval = auth.Approval()
    app.config["SECURITY"] = security
    app.config["AUDIT"] = auth.ActionAudit(app.config["AUDIT_PATH"])
    app.config["CONNECTIVITY"] = ConnectivityStore()

    app.register_blueprint(auth.auth_blueprint())
    app.register_blueprint(connectivity_blueprint())
    app.register_blueprint(organizer_blueprint())
    app.register_blueprint(inventory_blueprint())

    @app.context_processor
    def inject_modules():
        return {"modules": modules()}

    @app.route("/health")
    def health():
        return jsonify({"status": "ok"})

    def _env_authenticated():
        if auth.auth_disabled():
            return True
        return auth.require_session() is not None

    @app.context_processor
    def inject_common():
        return {"env": {"authenticated": _env_authenticated()}}

    @app.route("/")
    def index():
        return render_template(
            "index.html",
            modules=modules(),
            snap=status.collect(),
            series=status.history(),
        )

    @app.route("/api/status")
    def api_status():
        if auth.require_session() is None:
            return jsonify({"error": "Unlock this dashboard with the local access code."}), 401
        return jsonify(current_app.config["CONNECTIVITY"].get())

    @app.route("/api/series")
    def api_series():
        if auth.require_session() is None:
            return jsonify({"error": "Unlock this dashboard with the local access code."}), 401
        return jsonify(status.history())

    @app.route("/modules")
    def modules_page():
        return render_template("modules.html", modules=modules())

    return app


def modules():
    """Every module the dashboard knows about, with its mount state."""
    return [
        {
            "id": "organizer",
            "name": "Folder Organizer",
            "summary": "Scan, deduplicate, tag and rename the media library.",
            "path": "folder_organizer",
            "url": "organizer.proxy",
            "available": is_available(),
            "tags": ["media", "dedupe", "tags"],
        },
        {
            "id": "inventory",
            "name": "Hardware Inventory",
            "summary": "Track PC components, network devices, and personal hardware.",
            "path": "inventory",
            "url": "inventory.inventory_api.dashboard",
            "available": inventory_available(),
            "tags": ["hardware", "network", "assets"],
        },
    ]
