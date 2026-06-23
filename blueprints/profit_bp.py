"""
blueprints/profit_bp.py
-----------------------
Blueprint do Profit Bridge — Sprint Profit 1.

Rotas:
  GET /api/profit/status  — health check da bridge
  GET /profit             — dashboard de monitoramento

A bridge e iniciada automaticamente quando este blueprint e registrado
no app (via @profit_bp.record_once), sem necessidade de tocar no app.py
alem das 3 linhas padrao (comment + import + register_blueprint).

REGRA: nenhum modulo existente e alterado.
"""
import logging
from flask import Blueprint, jsonify, render_template

profit_bp = Blueprint("profit", __name__)
logger    = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Inicia a bridge automaticamente ao registrar o blueprint
# ---------------------------------------------------------------------------

@profit_bp.record_once
def _auto_start_bridge(state):          # noqa: state nao usado
    """Dispara start_bridge() quando app.register_blueprint(profit_bp) e chamado."""
    try:
        from services.profit_bridge import start_bridge
        start_bridge()
        logger.info("profit_bp: Profit Bridge iniciada via record_once.")
    except Exception as exc:
        logger.error("profit_bp: falha ao iniciar Profit Bridge: %s", exc)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@profit_bp.route("/api/profit/status")
def api_profit_status():
    """
    Health check do Profit Bridge.

    Retorna:
      running      — bridge esta em execucao
      last_update  — ISO timestamp da ultima geracao do JSON
      json_exists  — arquivo JSON existe em disco
      json_size    — tamanho do JSON em bytes
      error        — ultima mensagem de erro (null se ok)
      updates_ok   — total de atualizacoes com sucesso
      updates_fail — total de falhas
      started_at   — quando a bridge foi iniciada
    """
    try:
        from services.profit_bridge import get_status
        s = get_status()
        return jsonify({
            "running":      s.get("running", False),
            "last_update":  s.get("last_update"),
            "json_exists":  s.get("json_exists", False),
            "json_size":    s.get("json_size", 0),
            "error":        s.get("error"),
            "updates_ok":   s.get("updates_ok", 0),
            "updates_fail": s.get("updates_fail", 0),
            "started_at":   s.get("started_at"),
        })
    except Exception as exc:
        logger.error("api_profit_status: %s", exc)
        return jsonify({"error": str(exc)}), 500


@profit_bp.route("/api/profit/json")
def api_profit_json():
    """
    Retorna o conteudo do trade_ai_profit.json gerado pela bridge.
    Permite que o dashboard visualize os dados sem ler o arquivo diretamente.
    """
    import json as _json
    try:
        from services.profit_bridge import get_json_path
        path = get_json_path()
        import os
        if not os.path.exists(path):
            return jsonify({"error": "JSON ainda nao gerado"}), 404
        with open(path, "r", encoding="utf-8") as f:
            data = _json.load(f)
        return jsonify(data)
    except Exception as exc:
        logger.error("api_profit_json: %s", exc)
        return jsonify({"error": str(exc)}), 500


@profit_bp.route("/profit")
def profit_dashboard():
    """Dashboard de monitoramento do Profit Bridge."""
    return render_template("profit.html")
