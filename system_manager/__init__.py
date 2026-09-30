"""System Manager — a Flask dashboard that hosts feature modules."""
import os
import tempfile
from pathlib import Path

from flask import Flask, current_app, jsonify, render_template, request

from . import auth, status
from .connectivity import ConnectivityStore, connectivity_blueprint
from .notifier import Notifier, start_notifier
from .organizer import ORGANIZER_PATH, is_available, organizer_blueprint
from .inventory import inventory_blueprint, is_available as inventory_available
from .journal import journal_blueprint, is_available as journal_available
from .packages import packages_blueprint, is_available as packages_available

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
    app.config["SETTINGS"] = auth.Settings(app.config["AUDIT_PATH"])
    app.config["CONNECTIVITY"] = ConnectivityStore()
    # The notifier reads the thresholds through a callable so a value saved
    # at runtime reaches the next tick without the thread being rebuilt.
    app.config["NOTIFIER"] = Notifier(
        logger=app.logger,
        thresholds=lambda: app.config["SETTINGS"].thresholds())
    # A live timer would outlive the test that built the app; the tests
    # exercise Notifier directly instead.
    if not app.config.get("TESTING"):
        start_notifier(app.config["NOTIFIER"])

    app.register_blueprint(auth.auth_blueprint())
    app.register_blueprint(connectivity_blueprint())
    app.register_blueprint(organizer_blueprint())
    app.register_blueprint(inventory_blueprint())
    app.register_blueprint(packages_blueprint())
    app.register_blueprint(journal_blueprint())

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
            snap=status.collect(current_app.config["SETTINGS"].thresholds()),
            series=status.history(),
        )

    @app.route("/api/status")
    def api_status():
        if auth.require_session() is None:
            return jsonify({"error": "Unlock this dashboard with the local access code."}), 401
        # The advisories are computed inside the snapshot, so the stored
        # thresholds have to be passed in or the card would keep reporting
        # the built-in 10% no matter what the user just saved.
        thresholds = current_app.config["SETTINGS"].thresholds()
        return jsonify(current_app.config["CONNECTIVITY"].get(thresholds))

    @app.route("/api/series")
    def api_series():
        if auth.require_session() is None:
            return jsonify({"error": "Unlock this dashboard with the local access code."}), 401
        return jsonify(status.history())

    @app.route("/modules")
    def modules_page():
        return render_template("modules.html", modules=modules())

    @app.route("/api/notifications", methods=["GET", "POST"])
    def api_notifications():
        """Active conditions, recent alerts, and a manual test alert."""
        if auth.require_session() is None:
            return auth._authorize()
        notifier = current_app.config["NOTIFIER"]
        if request.method == "POST":
            # A fixed message, never request input: nothing a caller sends
            # reaches notify-send's argv.
            delivered = notifier.notify({
                "key": "test", "title": "System Manager",
                "body": "Desktop notifications are working."})
            return jsonify({"ok": delivered, "state": notifier.state()}), (200 if delivered else 503)
        return jsonify(notifier.state())

    @app.route("/api/notifications/thresholds", methods=["GET", "PUT"])
    def api_notification_thresholds():
        """Read or replace the notification thresholds."""
        if auth.require_session() is None:
            return auth._authorize()
        settings = current_app.config["SETTINGS"]
        if request.method == "GET":
            return jsonify(settings.thresholds())
        body = request.get_json(silent=True) or {}
        if not isinstance(body, dict):
            return jsonify({"error": "Send a JSON object of thresholds."}), 400
        unknown = sorted(set(body) - set(auth.THRESHOLD_BOUNDS))
        if unknown:
            return jsonify({"error": f"Unknown threshold(s): {', '.join(unknown)}."}), 400
        stored, error = settings.set_thresholds(body)
        if error:
            return jsonify({"error": error, "thresholds": settings.thresholds()}), 400
        # A tightened threshold can make a condition newly true. The notifier
        # dedups on "has this key been seen", so a key that was already active
        # stays quiet; clearing the memory is what lets a lowered cutoff
        # announce itself on the next tick instead of silently waiting.
        current_app.config["NOTIFIER"].forget()
        return jsonify(stored)

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
        {
            "id": "packages",
            "name": "Packages & Updates",
            "summary": "See what an apt upgrade would change, before running it.",
            "path": "packages",
            "url": "packages.packages_api.list_packages",
            "available": packages_available(),
            "tags": ["apt", "updates", "security"],
        },
        {
            "id": "journal",
            "name": "Logs",
            "summary": "Search the host's journal by priority, unit and time.",
            "path": "logs",
            "url": "journal.journal_api.list_entries",
            "available": journal_available(),
            "tags": ["journal", "logs", "systemd"],
        },
    ]
