"""Network module API endpoints. Read-only host network inspection.

Every collector reads procfs through hostnet, so the numbers describe the
host even when this runs in the container. hostnet.scoped_label() says
which host, and /network/summary reports it, so the page never implies more
than it knows.
"""
from flask import Blueprint, current_app, jsonify, render_template, request

from .. import auth
from . import hostnet
from .dns import (
    DEFAULT_DOMAINS,
    benchmark_dns_providers,
    current_dns_state,
    dnsmasq_config_state,
    host_dns_rollback_command,
    preview_dns_apply,
    run_dnsmasq_helper,
)

network_bp = Blueprint("network_api", __name__)


@network_bp.route("/summary")
def summary():
    return jsonify({
        "hostname": {"hostname": hostnet.hostname(), "domain": ""},
        "interfaces": len(hostnet.interfaces()),
        "routes": len(hostnet.routes()),
        "dns_servers": hostnet.dns_servers(),
        "listening_ports": len(hostnet.listening_ports()),
        "source": hostnet.scoped_label(),
    })


@network_bp.route("/interfaces")
def interfaces():
    return jsonify(hostnet.interfaces())


@network_bp.route("/routes")
def routes():
    return jsonify(hostnet.routes())


@network_bp.route("/dns")
def dns():
    state = dnsmasq_config_state()
    return jsonify({
        "servers": state["servers"],
        "resolver_servers": hostnet.dns_servers(),
        "source": state["source"],
        "scope": hostnet.scoped_label(),
    })


@network_bp.route("/dns/status")
def dns_status():
    return jsonify(current_dns_state())


@network_bp.route("/dns/compare")
def dns_compare():
    domains_value = request.args.get("domains", "")
    if domains_value:
        domains = [field.strip()
                   for field in domains_value.split(",") if field.strip()]
    else:
        domains = list(DEFAULT_DOMAINS)
    quick = str(request.args.get("quick", "0")).lower() in {"1", "true", "yes"}
    return jsonify(benchmark_dns_providers(domains=domains, timeout=1.5, quick_test=quick))


@network_bp.route("/dns/apply", methods=["POST"])
def dns_apply():
    if auth.auth_disabled():
        return jsonify({
            "error": "DNS changes are disabled while dashboard authentication is off.",
        }), 403
    security = auth.require_session()
    if security is None:
        return auth._authorize()
    body = request.get_json(silent=True) or {}
    provider_name = str(body.get("provider") or body.get("name") or "").strip()
    addresses = body.get("addresses") or []
    if isinstance(addresses, str):
        addresses = [addresses]
    preview = preview_dns_apply(
        provider_name, addresses, hostnet.dns_servers())
    if preview.get("status") == "rejected":
        return jsonify(preview), 400
    if body.get("confirm") is not True:
        return jsonify({
            "error": "Explicit confirmation is required before applying DNS.",
            "preview": preview,
        }), 400
    before = hostnet.dns_servers()
    result = run_dnsmasq_helper("apply", preview["addresses"])
    outcome = result.get("status", "error")
    audit = current_app.config.get("AUDIT")
    if audit is not None:
        audit.record(
            auth._hash(security.token_from(request)), "dns_apply",
            {"provider": provider_name, "addresses": preview["addresses"]},
            {"manager": "host dnsmasq", "current_dns": before},
            result, {"status": outcome},
        )
    if outcome != "applied":
        failure = {
            "status": "failed",
            "error": result.get("error", "DNS change failed."),
            "detail": result.get("detail", ""),
            "automatic_rollback": result.get("automatic_rollback"),
        }
        backup_id = result.get("backup_id", "")
        if backup_id and result.get("rollback_command"):
            failure["backup_id"] = backup_id
            failure["rollback_commands"] = [host_dns_rollback_command()]
        return jsonify(failure), 503
    backup_id = result.get("backup_id", "")
    result["rollback_commands"] = [host_dns_rollback_command()]
    return jsonify({
        **result,
        "provider": provider_name,
        "previous_dns": before,
        "effective_dns": hostnet.dns_servers(),
        "message": "DNS upstreams updated and dnsmasq is active. Use the rollback button or command below to restore the backed-up configuration.",
    })


@network_bp.route("/dns/rollback", methods=["POST"])
def dns_rollback():
    if auth.auth_disabled():
        return jsonify({
            "error": "DNS rollback actions are disabled while dashboard authentication is off.",
        }), 403
    security = auth.require_session()
    if security is None:
        return auth._authorize()
    body = request.get_json(silent=True) or {}
    backup_id = body.get("backup_id")
    if not isinstance(backup_id, str) or body.get("confirm") is not True:
        return jsonify({
            "error": "A valid backup ID and explicit rollback confirmation are required.",
        }), 400
    before = hostnet.dns_servers()
    result = run_dnsmasq_helper("rollback", backup_id)
    outcome = result.get("status", "error")
    audit = current_app.config.get("AUDIT")
    if audit is not None:
        audit.record(
            auth._hash(security.token_from(request)), "dns_rollback",
            {"backup_id": backup_id}, {
                "manager": "host dnsmasq", "current_dns": before},
            result, {"status": outcome},
        )
    if outcome != "rolled-back":
        return jsonify({"status": "failed", **result}), 503
    return jsonify({**result, "effective_dns": hostnet.dns_servers()})


@network_bp.route("/ports")
def ports():
    return jsonify(hostnet.listening_ports())


@network_bp.route("/conntrack")
def conntrack():
    return jsonify(hostnet.conntrack())


@network_bp.route("/neighbors")
def neighbors():
    return jsonify(hostnet.neighbors())


@network_bp.route("/")
def index():
    """Network overview page."""
    return render_template("network/index.html")
