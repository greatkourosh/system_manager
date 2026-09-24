"""Inventory module API endpoints."""
import csv
import io
import os
import json
from pathlib import Path

from flask import Blueprint, current_app, g, jsonify, render_template, request

from .store import Store

inventory_bp = Blueprint("inventory_api", __name__)


def _store():
    if "inventory_store" not in g:
        data_dir = current_app.config.get("INVENTORY_DATA_DIR") or os.environ.get(
            "INVENTORY_DB")
        if not data_dir:
            base = current_app.config.get("TEMP_DIR") or "."
            data_dir = str(Path(base) / "inventory.db")
        g.inventory_store = Store(data_dir)
    return g.inventory_store


@inventory_bp.route("/")
def dashboard():
    """Inventory dashboard with stats and alerts."""
    stats = _store().stats()
    return render_template("inventory/dashboard.html", stats=stats)


@inventory_bp.route("/items")
def items_list():
    """List items with pagination and filters (HTML + JSON)."""
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 50, type=int)
    category = request.args.get("category") or None
    status = request.args.get("status") or None

    result = _store().list_items(page=page, per_page=per_page,
                                 category=category, status=status)

    if request.headers.get("Accept", "").startswith("application/json"):
        return jsonify({"items": result["items"], "page": page,
                        "per_page": per_page, "total": result["total"]})

    return render_template("inventory/items_list.html",
                           items=result["items"], page=page,
                           per_page=per_page, total=result["total"])


@inventory_bp.route("/items", methods=["POST"])
def items_create():
    """Create a new inventory item."""
    data = request.get_json(silent=True) or {}
    if not data.get("name"):
        return jsonify({"error": "name is required"}), 400
    item = _store().create_item(data)
    return jsonify(item), 201


@inventory_bp.route("/items/<int:item_id>")
def item_detail(item_id):
    """Get item detail."""
    item = _store().get_item(item_id)
    if item is None:
        return jsonify({"error": "not found"}), 404
    if request.headers.get("Accept", "").startswith("application/json"):
        return jsonify(item)
    return render_template("inventory/item_detail.html", item=item)


@inventory_bp.route("/items/<int:item_id>", methods=["PATCH"])
def item_update(item_id):
    """Update an item (partial)."""
    data = request.get_json(silent=True) or {}
    item = _store().update_item(item_id, data)
    if item is None:
        return jsonify({"error": "not found"}), 404
    return jsonify(item)


@inventory_bp.route("/items/<int:item_id>", methods=["DELETE"])
def item_delete(item_id):
    """Soft-delete an item (set status=retired)."""
    if not _store().delete_item(item_id):
        return jsonify({"error": "not found"}), 404
    return jsonify({"id": item_id, "status": "retired"})


@inventory_bp.route("/builds")
def builds_list():
    """List builds."""
    builds = _store().list_builds()
    if request.headers.get("Accept", "").startswith("application/json"):
        return jsonify({"builds": builds})
    return render_template("inventory/builds_list.html", builds=builds)


@inventory_bp.route("/builds", methods=["POST"])
def builds_create():
    """Create a build."""
    data = request.get_json(silent=True) or {}
    build = _store().create_build(data)
    return jsonify(build), 201


@inventory_bp.route("/builds/<int:build_id>")
def build_detail(build_id):
    """Build detail with compatibility report."""
    build = _store().get_build(build_id)
    if build is None:
        return jsonify({"error": "not found"}), 404
    build["components"] = []
    if request.headers.get("Accept", "").startswith("application/json"):
        return jsonify(build)
    return render_template("inventory/build_detail.html", build=build)


@inventory_bp.route("/topology")
def topology():
    """Network topology view."""
    if request.headers.get("Accept", "").startswith("application/json"):
        return jsonify({"nodes": [], "edges": []})
    return render_template("inventory/topology.html")


@inventory_bp.route("/export")
def export_data():
    """Export inventory as CSV/JSON/YAML."""
    fmt = request.args.get("format", "json")
    items = _store().list_items().get("items", [])
    if fmt == "csv":
        buffer = io.StringIO()
        if items:
            writer = csv.DictWriter(buffer, fieldnames=list(items[0].keys()))
            writer.writeheader()
            writer.writerows(items)
        from flask import Response
        return Response(buffer.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition": "attachment; filename=inventory.csv"})
    if fmt == "yaml":
        # Keep zero-dependency: emit a minimal YAML subset.
        lines = ["items:"]
        for item in items:
            lines.append(f"  - id: {item.get('id')}")
            for key, value in item.items():
                if key == "id":
                    continue
                lines.append(f"    {key}: {json.dumps(value)}")
        from flask import Response
        return Response("\n".join(lines), mimetype="text/plain",
                        headers={"Content-Disposition": "attachment; filename=inventory.yaml"})
    return jsonify({"items": items})


@inventory_bp.route("/import", methods=["POST"])
def import_data():
    """Import inventory from CSV/JSON."""
    return jsonify({"message": "Import - not implemented yet"}), 501


@inventory_bp.route("/qr/<int:item_id>")
def qr_code(item_id):
    """Generate QR code SVG for item labeling."""
    return jsonify({"message": f"QR code for item {item_id} - not implemented yet"}), 501