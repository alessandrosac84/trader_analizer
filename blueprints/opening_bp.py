"""
opening_bp.py — Opening Engine (abertura do WIN): página /opening + API.

Tela de DECISÃO (análise, não envia ordem). Mostra o score 0–100 de compra/venda
da abertura do WIN, fator a fator, com sinal LONG/SHORT/WAIT. Isolado dos demais
módulos.
"""
import logging

from flask import Blueprint, jsonify, render_template, request

logger = logging.getLogger(__name__)

opening_bp = Blueprint("opening", __name__)


@opening_bp.route("/opening")
def opening_page():
    try:
        from services.opening_engine import start_opening_engine
        start_opening_engine()
    except Exception as exc:
        logger.warning("opening start: %s", exc)
    return render_template("opening_engine.html")


@opening_bp.route("/api/opening/status")
def api_opening_status():
    try:
        from services.opening_engine import snapshot, start_opening_engine
        start_opening_engine()
        return jsonify({"ok": True, **snapshot()})
    except Exception as exc:
        logger.exception("opening status")
        return jsonify({"ok": False, "error": str(exc)}), 500


@opening_bp.route("/api/opening/toggle", methods=["POST"])
def api_opening_toggle():
    try:
        from services.opening_engine import set_enabled
        body = request.get_json(silent=True) or {}
        return jsonify({"ok": True, "enabled": set_enabled(bool(body.get("enabled")))})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@opening_bp.route("/api/opening/probe")
def api_opening_probe():
    """Diagnóstico: mostra quais dados o MT5 do usuário entrega (ticks, book, etc.)."""
    try:
        from services.opening_engine import probe
        return jsonify({"ok": True, **probe()})
    except Exception as exc:
        logger.exception("opening probe")
        return jsonify({"ok": False, "error": str(exc)}), 500
