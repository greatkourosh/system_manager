"""Inventory module API endpoints."""
from flask import Blueprint, jsonify, render_template, request

inventory_bp = Blueprint("inventory_api", __name__)


@inventory_bp.route("/")
def dashboard():
    """Inventory dashboard with stats and alerts."""
    stats = {
        "total_items": 0,
        "warranty_expiring": 0,
        "low_stock": 0,
        "recent_changes": 0,
    }
    return render_template("inventory/dashboard.html", stats=stats)


@inventory_bp.route("/items")
def items_list():
    """List items with pagination and filters (HTML + JSON)."""
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 50, type=int)
    category = request.args.get("category")
    status = request.args.get("status")

    items = []
    total = 0

    if request.headers.get("Accept", "").startswith("application/json"):
        return jsonify({
            "items": items,
            "page": page,
            "per_page": per_page,
            "total": total,
        })

    return render_template("inventory/items_list.html",
                           items=items, page=page, per_page=per_page, total=total)


@inventory_bp.route("/items", methods=["POST"])
def items_create():
    """Create a new inventory item."""
    data = request.get_json() or {}
    return jsonify({"id": 1, **data}), 201


@inventory_bp.route("/items/<int:item_id>")
def item_detail(item_id):
    """Get item detail."""
    item = {"id": item_id, "name": "Sample Item", "category": "pc_component"}
    if request.headers.get("Accept", "").startswith("application/json"):
        return jsonify(item)
    return render_template("inventory/item_detail.html", item=item)


@inventory_bp.route("/items/<int:item_id>", methods=["PATCH"])
def item_update(item_id):
    """Update an item (partial)."""
    data = request.get_json() or {}
    return jsonify({"id": item_id, **data})


@inventory_bp.route("/items/<int:item_id>", methods=["DELETE"])
def item_delete(item_id):
    """Soft-delete an item (set status=retired)."""
    return jsonify({"id": item_id, "status": "retired"})


@inventory_bp.route("/builds")
def builds_list():
    """List builds."""
    builds = []
    if request.headers.get("Accept", "").startswith("application/json"):
        return jsonify({"builds": builds})
    return render_template("inventory/builds_list.html", builds=builds)


@inventory_bp.route("/builds", methods=["POST"])
def builds_create():
    """Create a build."""
    data = request.get_json() or {}
    return jsonify({"id": 1, **data}), 201


@inventory_bp.route("/builds/<int:build_id>")
def build_detail(build_id):
    """Build detail with compatibility report."""
    build = {"id": build_id, "name": "Sample Build", "components": []}
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
    return jsonify({"message": f"Export as {fmt} - not implemented yet"}), 501


@inventory_bp.route("/import", methods=["POST"])
def import_data():
    """Import inventory from CSV/JSON."""
    return jsonify({"message": "Import - not implemented yet"}), 501


@inventory_bp.route("/qr/<int:item_id>")
def qr_code(item_id):
    """Generate QR code SVG for item labeling."""
    return jsonify({"message": f"QR code for item {item_id} - not implemented yet"}), 501