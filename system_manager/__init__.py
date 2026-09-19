"""System Manager — a Flask dashboard that hosts feature modules."""
from flask import Flask, jsonify, render_template

from .organizer import ORGANIZER_PATH, is_available, organizer_blueprint

__all__ = ["create_app"]


def create_app(config=None):
    app = Flask(__name__, template_folder="../templates", static_folder="../static")
    if config:
        app.config.update(config)
    app.register_blueprint(organizer_blueprint())
    app.config["ORGANIZER_PATH"] = ORGANIZER_PATH

    @app.route("/health")
    def health():
        return jsonify({"status": "ok"})

    @app.route("/")
    def index():
        return render_template("index.html", organizer_ok=is_available())

    return app
