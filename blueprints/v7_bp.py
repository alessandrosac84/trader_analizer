"""
v7_bp.py — Hub B3 / Monitor V7 (página /v7 + API).

Tela única de acompanhamento WIN/WDO: V7 (GAP_FADE+ORB) + WinGo (NR7/INSIDE)
+ WIN_EOD e futuros setups B3. Execução continua isolada por magic; a UI
agrega status, posições e histórico etiquetado por SETUP.
Crypto fica no Monitor Crypto — não entra aqui.
"""
import logging

from flask import Blueprint, jsonify, render_template, request

logger = logging.getLogger(__name__)

v7_bp = Blueprint("v7", __name__)


def _rt():
    from services.v7_engine_runtime import runtime
    runtime.ensure_started()
    return runtime


@v7_bp.route("/v7")
def v7_page():
    _rt()
    return render_template("v7_monitor.html")


@v7_bp.route("/api/v7/status")
def api_v7_status():
    try:
        rt = _rt()
        snap = rt.snapshot()
        try:
            from services.v7_engine_runtime import ai_scorecard
            snap["ai_scorecard"] = ai_scorecard()
        except Exception as exc:
            logger.warning("v7 ai_scorecard: %s", exc)
            snap["ai_scorecard"] = None
        try:
            from services.b3_trade_hub import engines_status, unified_trades
            snap["engines"] = engines_status()
            snap["trades"] = unified_trades(80)
        except Exception as exc:
            logger.exception("v7 hub")
            snap["engines"] = []
            snap["trades"] = snap.get("trades") or []
            snap["hub_error"] = str(exc)
        return jsonify(snap)
    except Exception as exc:
        logger.exception("v7 status")
        return jsonify({"ok": False, "error": str(exc)}), 500


@v7_bp.route("/api/v7/hub")
def api_v7_hub():
    """Status dos motores B3 + trades unificados (sem snapshot completo do V7)."""
    try:
        from services.b3_trade_hub import engines_status, unified_trades
        return jsonify({
            "ok": True,
            "engines": engines_status(),
            "trades": unified_trades(60),
        })
    except Exception as exc:
        logger.exception("v7 hub")
        return jsonify({"ok": False, "error": str(exc)}), 500


@v7_bp.route("/api/v7/toggle", methods=["POST"])
def api_v7_toggle():
    body = request.get_json(silent=True) or {}
    rt = _rt()
    rt.set_enabled(bool(body.get("enabled")))
    return jsonify({"ok": True, "enabled": rt.enabled})


@v7_bp.route("/api/v7/engine-toggle", methods=["POST"])
def api_v7_engine_toggle():
    """Liga/desliga um motor do hub (v7|nr7|inside|eod|wingo)."""
    body = request.get_json(silent=True) or {}
    eid = body.get("id") or body.get("engine") or ""
    enabled = bool(body.get("enabled"))
    try:
        from services.b3_trade_hub import set_engine_enabled
        ok, which = set_engine_enabled(eid, enabled)
        if not ok:
            return jsonify({"ok": False, "error": which}), 400
        return jsonify({"ok": True, "id": which, "enabled": enabled})
    except Exception as exc:
        logger.exception("v7 engine-toggle")
        return jsonify({"ok": False, "error": str(exc)}), 500


@v7_bp.route("/api/v7/config", methods=["GET", "POST"])
def api_v7_config():
    rt = _rt()
    if request.method == "POST":
        b = request.get_json(silent=True) or {}
        cfg = rt.set_config(vol_mult=b.get("vol_mult"),
                            chart_tf=b.get("chart_tf"),
                            ai_mode=b.get("ai_mode"))
        return jsonify({"ok": True, **cfg})
    return jsonify({"ok": True, "vol_mult": rt.vol_mult,
                    "chart_tf": rt.chart_tf, "ai_mode": rt.ai_mode})


@v7_bp.route("/api/v7/candles")
def api_v7_candles():
    try:
        import os
        rt = _rt()
        tf = request.args.get("tf")
        asset = (request.args.get("asset") or "WIN").upper().strip()
        if asset == "WDO":
            sym = os.getenv("WDO_MT5_SYMBOL", "WDOU26").strip()
        else:
            asset = "WIN"
            sym = os.getenv("WIN_MT5_SYMBOL", rt.symbol or "WINV26").strip()
        candles, err = rt.get_candles(tf=tf, symbol=sym)
        return jsonify({"ok": True, "asset": asset, "symbol": sym,
                        "tf": int(tf) if tf else rt.chart_tf,
                        "candles": candles, "error": err})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@v7_bp.route("/api/v7/tick")
def api_v7_tick():
    """Preço atual (leve) p/ atualizar o último candle ao vivo — espelho do crypto.
    ?asset=WIN|WDO (default WIN). Sem asset = resposta única WIN (compat).
    ?asset=ALL = {WIN:{...}, WDO:{...}} num request."""
    try:
        import os
        rt = _rt()
        asset = (request.args.get("asset") or "WIN").upper().strip()

        def _one(a):
            if a == "WDO":
                sym = os.getenv("WDO_MT5_SYMBOL", "WDOU26").strip()
            else:
                a = "WIN"
                sym = os.getenv("WIN_MT5_SYMBOL", rt.symbol or "WINV26").strip()
            t = rt.get_tick(symbol=sym)
            if not t or not t.get("price"):
                return None
            return {"asset": a, "symbol": sym, "price": t["price"],
                    "bid": t.get("bid"), "ask": t.get("ask"), "time": t.get("time", 0)}

        if asset == "ALL":
            out = {}
            for a in ("WIN", "WDO"):
                one = _one(a)
                if one:
                    out[a] = one
            return jsonify({"ok": bool(out), "ticks": out})

        one = _one(asset)
        if not one:
            return jsonify({"ok": False})
        return jsonify({"ok": True, **one})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@v7_bp.route("/api/v7/period")
def api_v7_period():
    """KPIs + by_setup agregando TODOS os motores B3 do hub.

    Query: range=day|week|month · trades=1 inclui trade_list unificada (Dashboard/Relatório HUD).
    """
    try:
        from datetime import datetime
        from services.b3_trade_hub import unified_period, unified_trades, _since
        rng = request.args.get("range", "day")
        out = {"ok": True, **unified_period(rng)}
        since = _since(rng)
        out["date_from"] = since if since != "0000-00-00" else ""
        out["date_to"] = datetime.now().strftime("%Y-%m-%d")
        if str(request.args.get("trades") or "").strip().lower() in ("1", "true", "yes"):
            # Mesmo teto do unified_period — lista e KPIs não divergem no Relatório
            out["trade_list"] = unified_trades(limit=5000, rng=rng)
        return jsonify(out)
    except Exception as exc:
        logger.exception("v7 period")
        return jsonify({"ok": False, "error": str(exc)}), 500


@v7_bp.route("/api/v7/ai-history")
def api_v7_ai_history():
    try:
        from services.v7_engine_runtime import ai_history, ai_scorecard
        return jsonify({"ok": True, "history": ai_history(60),
                        "scorecard": ai_scorecard()})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@v7_bp.route("/api/v7/close", methods=["POST"])
def api_v7_close():
    """Fecha posição: sem magic = V7; com magic = qualquer motor do hub."""
    body = request.get_json(silent=True) or {}
    magic = body.get("magic")
    if magic is not None and str(magic).strip() != "":
        try:
            from services.b3_trade_hub import close_by_magic
            asset = body.get("asset") or body.get("symbol")
            ok, err = close_by_magic(int(magic), asset=asset)
            return jsonify({"ok": ok, "error": err})
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 500
    ok, err = _rt().close_position_manual()
    return jsonify({"ok": ok, "error": err})
