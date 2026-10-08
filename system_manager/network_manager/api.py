"""Network module API endpoints. Read-only host network inspection.

Every collector reads procfs through hostnet, so the numbers describe the
host even when this runs in the container. hostnet.scoped_label() says
which host, and /network/summary reports it, so the page never implies more
than it knows.
"""
import socket

from flask import Blueprint, jsonify, render_template

from . import hostnet

network_bp = Blueprint("network_api", __name__)


@network_bp.route("/summary")
def summary():
    return jsonify({
        "hostname": {"hostname": socket.gethostname(), "domain": ""},
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
    return jsonify({"servers": hostnet.dns_servers(), "source": hostnet.scoped_label()})


@network_bp.route("/ports")
def ports():
    return jsonify(hostnet.listening_ports())


@network_bp.route("/conntrack")
def conntrack():
    return jsonify(hostnet.conntrack())


@network_bp.route("/")
def index():
    """Network overview page."""
    return render_template("network/index.html")