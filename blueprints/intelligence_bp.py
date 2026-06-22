"""
blueprints/intelligence_bp.py
-------------------------------
Blueprint do Motor de Inteligencia.
Todas as rotas /intelligence e /api/intelligence/* ficam aqui.

REGRA: NAO modifica nenhum modulo existente.
"""
import logging
from flask import Blueprint, render_template, jsonify, request

logger = logging.getLogger(__name__)

intelligence_bp = Blueprint("intelligence_bp", __name__)


# ---------------------------------------------------------------------------
# Dashboard HTML
# ---------------------------------------------------------------------------

@intelligence_bp.route("/intelligence")
def intelligence_dashboard():
    return render_template("intelligence.html")


# ---------------------------------------------------------------------------
# API — Intelligence Engine (Evolucoes 1-4)
# ---------------------------------------------------------------------------

@intelligence_bp.route("/api/intelligence/recommendations")
def api_recommendations():
    """Recomendacoes consolidadas do motor."""
    try:
        from services.intelligence_engine import get_recommendations
        return jsonify(get_recommendations())
    except Exception as exc:
        logger.error("api_recommendations: %s", exc)
        return jsonify({"error": str(exc)}), 500


@intelligence_bp.route("/api/intelligence/score-performance")
def api_score_performance():
    """Win rate por valor de score."""
    try:
        from services.intelligence_engine import analyze_score_performance, suggest_min_score
        data     = analyze_score_performance()
        min_rec  = suggest_min_score()
        return jsonify({"rows": data, "recommendation": min_rec})
    except Exception as exc:
        logger.error("api_score_performance: %s", exc)
        return jsonify({"error": str(exc)}), 500


@intelligence_bp.route("/api/intelligence/weights")
def api_weights():
    """Pesos dinamicos por componente do score."""
    try:
        from services.intelligence_engine import analyze_component_weights
        return jsonify({"rows": analyze_component_weights()})
    except Exception as exc:
        logger.error("api_weights: %s", exc)
        return jsonify({"error": str(exc)}), 500


@intelligence_bp.route("/api/intelligence/regimes")
def api_regimes():
    """Viabilidade por regime de mercado."""
    try:
        from services.intelligence_engine import analyze_regime_viability, get_blocked_regimes
        rows    = analyze_regime_viability()
        blocked = get_blocked_regimes()
        return jsonify({"rows": rows, "blocked": blocked})
    except Exception as exc:
        logger.error("api_regimes: %s", exc)
        return jsonify({"error": str(exc)}), 500


@intelligence_bp.route("/api/intelligence/strength")
def api_strength():
    """Win rate por faixa de signal_strength."""
    try:
        from services.intelligence_engine import analyze_strength_ranges, suggest_min_strength
        rows = analyze_strength_ranges()
        rec  = suggest_min_strength()
        return jsonify({"rows": rows, "recommendation": rec})
    except Exception as exc:
        logger.error("api_strength: %s", exc)
        return jsonify({"error": str(exc)}), 500


@intelligence_bp.route("/api/intelligence/confluences")
def api_confluences():
    """Correlacao de confluencias com resultado."""
    try:
        from services.intelligence_engine import analyze_confluence_correlation
        return jsonify({"rows": analyze_confluence_correlation()})
    except Exception as exc:
        logger.error("api_confluences: %s", exc)
        return jsonify({"error": str(exc)}), 500


# ---------------------------------------------------------------------------
# API — AI Supervisor (Evolucao 4)
# ---------------------------------------------------------------------------

@intelligence_bp.route("/api/intelligence/ai-supervisor")
def api_ai_supervisor():
    """Desempenho da IA por veredicto + TP/FP/FN."""
    try:
        period = request.args.get("period", "month")   # padrao: ultimo mes
        from services.ai_supervisor import (
            analyze_ai_performance, get_fp_fn_summary,
            analyze_confidence_accuracy
        )
        return jsonify({
            "performance":   analyze_ai_performance(period),
            "fp_fn_summary": get_fp_fn_summary(period),
            "confidence":    analyze_confidence_accuracy(period),
            "period":        period,
        })
    except Exception as exc:
        logger.error("api_ai_supervisor: %s", exc)
        return jsonify({"error": str(exc)}), 500


@intelligence_bp.route("/api/intelligence/ai-decisions")
def api_ai_decisions():
    """Historico recente de decisoes da IA."""
    try:
        limit  = int(request.args.get("limit", 50))
        period = request.args.get("period", "month")   # padrao: ultimo mes
        from services.ai_supervisor import get_recent_decisions
        return jsonify({"rows": get_recent_decisions(limit, period)})
    except Exception as exc:
        logger.error("api_ai_decisions: %s", exc)
        return jsonify({"error": str(exc)}), 500


# ---------------------------------------------------------------------------
# API — Market Engine (Evolucao 5)
# ---------------------------------------------------------------------------

@intelligence_bp.route("/api/intelligence/market-engine")
def api_market_engine():
    """Estado do motor de mercado: exposicao, P&L, priorizacao."""
    try:
        period = request.args.get("period", None)   # None = defaults inteligentes por instrumento
        from services.market_engine import get_engine_state
        return jsonify(get_engine_state(period))
    except Exception as exc:
        logger.error("api_market_engine: %s", exc)
        return jsonify({"error": str(exc)}), 500


@intelligence_bp.route("/api/intelligence/drawdown-history")
def api_drawdown_history():
    """Historico de P&L semanal para curva de capital."""
    try:
        from services.market_engine import get_drawdown_history
        return jsonify({"weeks": get_drawdown_history()})
    except Exception as exc:
        logger.error("api_drawdown_history: %s", exc)
        return jsonify({"error": str(exc)}), 500


@intelligence_bp.route("/api/intelligence/trading-status")
def api_trading_status():
    """Verifica se trading esta permitido agora (drawdown / stop)."""
    try:
        instrument = request.args.get("instrument", None)
        from services.market_engine import check_trading_allowed
        return jsonify(check_trading_allowed(instrument))
    except Exception as exc:
        logger.error("api_trading_status: %s", exc)
        return jsonify({"error": str(exc)}), 500
