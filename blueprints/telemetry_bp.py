"""
blueprints/telemetry_bp.py
--------------------------
Blueprint de telemetria e Market Center para o Trade AI.

REGRA: nao executa trades, nao modifica estado,
nao altera nenhum modulo existente.
"""
import logging
from flask import Blueprint, jsonify, request, render_template

logger = logging.getLogger(__name__)

telemetry_bp = Blueprint("telemetry", __name__, url_prefix="")


def init_analytics_db():
    """Cria a tabela trade_opportunities se nao existir."""
    try:
        from services.opportunity_service import init_trade_opportunities
        init_trade_opportunities()
        logger.info("trade_opportunities table ready")
    except Exception as exc:
        logger.warning("init_analytics_db error: %s", exc)


@telemetry_bp.route("/api/telemetry")
def api_telemetry():
    tv_symbol = request.args.get("tv_symbol", "BMFBOVESPA:WIN1!")
    interval  = request.args.get("interval",  "15")
    try:
        from services.telemetry_service import get_telemetry_summary
        data = get_telemetry_summary(tv_symbol=tv_symbol, interval=interval)
        return jsonify({"ok": True, **data})
    except Exception as exc:
        logger.exception("api_telemetry error")
        return jsonify({"ok": False, "error": str(exc)}), 500


@telemetry_bp.route("/api/telemetry/full")
def api_telemetry_full():
    interval = request.args.get("interval", "15")
    try:
        from services.telemetry_service import get_telemetry_full
        data = get_telemetry_full(interval=interval)
        return jsonify({"ok": True, **data})
    except Exception as exc:
        logger.exception("api_telemetry_full error")
        return jsonify({"ok": False, "error": str(exc)}), 500


@telemetry_bp.route("/market-center")
def market_center():
    return render_template("market_center.html")


@telemetry_bp.route("/analytics")
def analytics_page():
    return render_template("analytics.html")


@telemetry_bp.route("/api/analytics/opportunity", methods=["POST"])
def api_log_opportunity():
    try:
        from services.opportunity_service import log_opportunity
        d = request.get_json(silent=True) or {}
        opp_id = log_opportunity(
            tv_symbol               = d.get("tv_symbol", ""),
            symbol_short            = d.get("symbol_short", ""),
            interval                = str(d.get("interval", "15")),
            action                  = d.get("action"),
            score                   = d.get("score"),
            score_raw               = d.get("score_raw"),
            signal_strength         = d.get("signal_strength"),
            risk_level              = d.get("risk_level"),
            market_regime           = d.get("market_regime"),
            score_breakdown         = d.get("score_breakdown"),
            confluences             = d.get("confluences"),
            entry_price             = d.get("entry_price"),
            sl                      = d.get("sl"),
            tp1                     = d.get("tp1"),
            atr                     = d.get("atr"),
            adx                     = d.get("adx"),
            rsi                     = d.get("rsi"),
            htf_trend               = d.get("htf_trend"),
            adx_filtered            = bool(d.get("adx_filtered", False)),
            directional_block       = d.get("directional_block"),
            ai_verdict              = d.get("ai_verdict"),
            ai_confidence           = d.get("ai_confidence"),
            volume_confirm          = d.get("volume_confirm"),
            volume_ratio            = d.get("volume_ratio"),
            ai_trader_confidence    = d.get("ai_trader_confidence"),
            ai_validator_confidence = d.get("ai_validator_confidence"),
        )
        return jsonify({"ok": True, "id": opp_id})
    except Exception as exc:
        logger.exception("api_log_opportunity error")
        return jsonify({"ok": False, "error": str(exc)}), 500


@telemetry_bp.route("/api/analytics/sync-outcomes")
def api_sync_outcomes():
    try:
        from services.opportunity_service import sync_outcomes
        result = sync_outcomes()
        return jsonify({"ok": True, **result})
    except Exception as exc:
        logger.exception("api_sync_outcomes error")
        return jsonify({"ok": False, "error": str(exc)}), 500


@telemetry_bp.route("/api/analytics/reset-links", methods=["POST"])
def api_reset_links():
    """
    Reseta linked_trade_id / was_traded / outcome de TODAS as oportunidades
    para permitir re-sincronizacao limpa.
    Usar apenas para correcao de dados incorretos.
    """
    try:
        from services.db import _conn as _db_conn
        with _db_conn() as conn:
            conn.execute("""
                UPDATE trade_opportunities SET
                    linked_trade_id      = NULL,
                    was_traded           = 0,
                    outcome_pnl_pts      = NULL,
                    outcome_pnl_brl      = NULL,
                    outcome_win          = NULL,
                    outcome_close_reason = NULL,
                    outcome_duration_candles = NULL,
                    resolved_at          = NULL
            """)
        return jsonify({"ok": True, "message": "Links resetados. Execute Sincronizar Outcomes."})
    except Exception as exc:
        logger.exception("api_reset_links error")
        return jsonify({"ok": False, "error": str(exc)}), 500


@telemetry_bp.route("/api/analytics/summary")
def api_analytics_summary():
    try:
        from services.opportunity_service import analytics_summary
        date_filter = request.args.get("date", None)  # 'today' | 'week' | None
        return jsonify({"ok": True, "data": analytics_summary(date_filter)})
    except Exception as exc:
        logger.exception("api_analytics_summary error")
        return jsonify({"ok": False, "error": str(exc)}), 500


@telemetry_bp.route("/api/analytics/score")
def api_analytics_score():
    try:
        from services.opportunity_service import analytics_score_performance
        return jsonify({"ok": True, "data": analytics_score_performance()})
    except Exception as exc:
        logger.exception("api_analytics_score error")
        return jsonify({"ok": False, "error": str(exc)}), 500


@telemetry_bp.route("/api/analytics/regime")
def api_analytics_regime():
    try:
        from services.opportunity_service import analytics_regime_performance
        return jsonify({"ok": True, "data": analytics_regime_performance()})
    except Exception as exc:
        logger.exception("api_analytics_regime error")
        return jsonify({"ok": False, "error": str(exc)}), 500


@telemetry_bp.route("/api/analytics/confluence")
def api_analytics_confluence():
    try:
        from services.opportunity_service import analytics_confluence_performance
        return jsonify({"ok": True, "data": analytics_confluence_performance()})
    except Exception as exc:
        logger.exception("api_analytics_confluence error")
        return jsonify({"ok": False, "error": str(exc)}), 500


@telemetry_bp.route("/api/analytics/strength")
def api_analytics_strength():
    try:
        from services.opportunity_service import analytics_signal_strength_performance
        return jsonify({"ok": True, "data": analytics_signal_strength_performance()})
    except Exception as exc:
        logger.exception("api_analytics_strength error")
        return jsonify({"ok": False, "error": str(exc)}), 500


@telemetry_bp.route("/api/analytics/recent")
def api_analytics_recent():
    try:
        from services.opportunity_service import analytics_recent_opportunities
        limit       = int(request.args.get("limit", 50))
        date_filter = request.args.get("date", None)   # 'today' | 'week' | None
        return jsonify({"ok": True, "data": analytics_recent_opportunities(limit, date_filter)})
    except Exception as exc:
        logger.exception("api_analytics_recent error")
        return jsonify({"ok": False, "error": str(exc)}), 500


@telemetry_bp.route("/api/analytics/opportunity-log", methods=["POST"])
def api_analytics_log_opportunity():
    try:
        from services.opportunity_service import log_opportunity
        body = request.get_json(silent=True) or {}
        log_opportunity(
            tv_symbol  = body.get("tv_symbol", ""),
            interval   = body.get("interval", "15"),
            acao       = body.get("acao", ""),
            score      = body.get("score"),
            entrada    = body.get("entrada"),
            stop       = body.get("stop"),
            tp1        = body.get("tp1"),
            signal     = body.get("signal") or {},
        )
        return jsonify({"ok": True})
    except Exception as exc:
        logger.exception("api_analytics_log_opportunity error")
        return jsonify({"ok": False, "error": str(exc)}), 500
