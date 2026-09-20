"""System Manager — a Flask dashboard that hosts feature modules."""
from flask import Flask, jsonify, render_template

from . import status
from .organizer import ORGANIZER_PATH, is_available, organizer_blueprint
from .inventory import inventory_blueprint, is_available as inventory_available

__all__ = ["create_app"]


def create_app(config=None):
    app = Flask(__name__, template_folder="../templates", static_folder="../static")
    if config:
        app.config.update(config)
    app.register_blueprint(organizer_blueprint())
    app.register_blueprint(inventory_blueprint())
    app.config["ORGANIZER_PATH"] = ORGANIZER_PATH

    @app.route("/health")
    def health():
        return jsonify({"status": "ok"})

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
        return jsonify(status.collect())

    @app.route("/api/series")
    def api_series():
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
