"""
profit_monitor_bp.py — Monitor PROFIT: página /profit-monitor + API.

Tela de análise (v6 score + fluxo do Profit). NÃO envia ordem. Isolada dos
demais módulos.
"""
import logging

from flask import Blueprint, jsonify, render_template

logger = logging.getLogger(__name__)

profit_monitor_bp = Blueprint("profit_monitor", __name__)


@profit_monitor_bp.route("/profit-monitor")
def profit_monitor_page():
    return render_template("profit_monitor.html")


@profit_monitor_bp.route("/api/profit-monitor/status")
def api_profit_monitor_status():
    try:
        from services.profit_monitor import snapshot
        return jsonify({"ok": True, **snapshot()})
    except Exception as exc:
        logger.exception("profit_monitor status")
        return jsonify({"ok": False, "error": str(exc)}), 500


@profit_monitor_bp.route("/api/profit-monitor/candles")
def api_profit_monitor_candles():
    from flask import request
    try:
        from services.profit_monitor import get_candles
        asset = request.args.get("asset", "WIN")
        tf = request.args.get("tf", 15)
        return jsonify(get_candles(asset, tf))
    except Exception as exc:
        logger.exception("profit_monitor candles")
        return jsonify({"ok": False, "error": str(exc)}), 500
