"""
blueprints/risk_bp.py  — CPE V1.1
-----------------------------------
Blueprint do Capital Protection Engine — rotas API + pagina Risk Management.
REGRA: nao modifica nenhum modulo existente.
"""
import io
import logging
from flask import Blueprint, jsonify, request, render_template, Response

logger   = logging.getLogger(__name__)
risk_bp  = Blueprint("risk", __name__, url_prefix="")


# ── Pagina ─────────────────────────────────────────────────────────────────

@risk_bp.route("/risk-management")
def risk_management_page():
    return render_template("risk_management.html")


# ── Status em tempo real ───────────────────────────────────────────────────

@risk_bp.route("/api/risk/status")
def api_risk_status():
    try:
        from services.capital_protection_engine import get_cpe_status
        return jsonify({"ok": True, "data": get_cpe_status()})
    except Exception as exc:
        logger.exception("api_risk_status error")
        return jsonify({"ok": False, "error": str(exc)}), 500


# ── Settings CRUD ──────────────────────────────────────────────────────────

@risk_bp.route("/api/risk/settings", methods=["GET"])
def api_get_settings():
    try:
        from services.risk_settings_service import get_settings
        return jsonify({"ok": True, "data": get_settings()})
    except Exception as exc:
        logger.exception("api_get_settings error")
        return jsonify({"ok": False, "error": str(exc)}), 500


@risk_bp.route("/api/risk/settings", methods=["POST"])
def api_save_settings():
    try:
        from services.risk_settings_service import save_settings
        data = request.get_json(silent=True) or {}
        ok = save_settings(data)
        return jsonify({"ok": ok})
    except Exception as exc:
        logger.exception("api_save_settings error")
        return jsonify({"ok": False, "error": str(exc)}), 500


# ── Eventos de auditoria ───────────────────────────────────────────────────

@risk_bp.route("/api/risk/events")
def api_risk_events():
    try:
        from services.risk_settings_service import get_recent_events
        limit = int(request.args.get("limit", 60))
        return jsonify({"ok": True, "data": get_recent_events(limit)})
    except Exception as exc:
        logger.exception("api_risk_events error")
        return jsonify({"ok": False, "error": str(exc)}), 500


# ── M7 — Analytics de Efetividade ─────────────────────────────────────────

@risk_bp.route("/api/risk/analytics")
def api_risk_analytics():
    try:
        from services.risk_settings_service import analytics_effectiveness
        return jsonify({"ok": True, "data": analytics_effectiveness()})
    except Exception as exc:
        logger.exception("api_risk_analytics error")
        return jsonify({"ok": False, "error": str(exc)}), 500


# ── M9 — Exportacao de Auditoria ──────────────────────────────────────────

@risk_bp.route("/api/risk/export")
def api_risk_export():
    fmt = request.args.get("fmt", "csv").lower()
    try:
        if fmt == "json":
            from services.risk_settings_service import export_events_json
            return jsonify(export_events_json())
        else:
            # CSV (default) e Excel (xlsx)
            from services.risk_settings_service import export_events_csv, get_recent_events
            if fmt == "xlsx":
                try:
                    import openpyxl, io as _io
                    events = get_recent_events(limit=10000)
                    wb = openpyxl.Workbook()
                    ws = wb.active
                    ws.title = "CPE Audit"
                    if events:
                        ws.append(list(events[0].keys()))
                        for e in events:
                            ws.append(list(e.values()))
                    buf = _io.BytesIO()
                    wb.save(buf)
                    buf.seek(0)
                    return Response(
                        buf.read(),
                        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": "attachment;filename=cpe_auditoria.xlsx"}
                    )
                except ImportError:
                    # fallback para CSV se openpyxl nao disponivel
                    pass
            csv_data = export_events_csv()
            return Response(
                csv_data,
                mimetype="text/csv",
                headers={"Content-Disposition": "attachment;filename=cpe_auditoria.csv"}
            )
    except Exception as exc:
        logger.exception("api_risk_export error")
        return jsonify({"ok": False, "error": str(exc)}), 500


# ── Acoes de controle ──────────────────────────────────────────────────────

@risk_bp.route("/api/risk/reset-circuit-breaker", methods=["POST"])
def api_reset_circuit_breaker():
    try:
        from services.capital_protection_engine import reset_circuit_breaker
        reset_circuit_breaker()
        return jsonify({"ok": True, "message": "Circuit Breaker resetado."})
    except Exception as exc:
        logger.exception("api_reset_circuit_breaker error")
        return jsonify({"ok": False, "error": str(exc)}), 500


@risk_bp.route("/api/risk/reset-day-block", methods=["POST"])
def api_reset_day_block():
    try:
        from services.capital_protection_engine import reset_day_block
        reset_day_block()
        return jsonify({"ok": True, "message": "Dia desbloqueado. Override ativo até meia-noite."})
    except Exception as exc:
        logger.exception("api_reset_day_block error")
        return jsonify({"ok": False, "error": str(exc)}), 500


@risk_bp.route("/api/risk/disable-homolog-override", methods=["POST"])
def api_disable_homolog_override():
    try:
        from services.capital_protection_engine import disable_homolog_override
        disable_homolog_override()
        return jsonify({"ok": True, "message": "Override desativado. CPE voltou ao modo normal."})
    except Exception as exc:
        logger.exception("api_disable_homolog_override error")
        return jsonify({"ok": False, "error": str(exc)}), 500


@risk_bp.route("/api/risk/evaluate", methods=["POST"])
def api_risk_evaluate():
    """Avalia um trade hipotetico sem executar nada."""
    try:
        from services.capital_protection_engine import cpe_evaluate
        d = request.get_json(silent=True) or {}
        result = cpe_evaluate(
            tv_symbol        = d.get("tv_symbol", ""),
            acao             = d.get("acao", ""),
            sl_pts           = d.get("sl_pts"),
            score            = d.get("score"),
            risk_level       = d.get("risk_level"),
            confluence_count = d.get("confluence_count"),
        )
        return jsonify({"ok": True, "data": result})
    except Exception as exc:
        logger.exception("api_risk_evaluate error")
        return jsonify({"ok": False, "error": str(exc)}), 500
