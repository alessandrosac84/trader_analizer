"""
blueprints/acoes_bp.py — Monitor Ações B3 (blue chips).

Página isolada /acoes. Paths 🟢 GO wired (v3+v4+v5+v6 mega); auto via ACOES_AUTO_ON.
Não importa Monitor Índices (WIN/WDO) nem Crypto magics.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

from flask import Blueprint, jsonify, render_template, request

logger = logging.getLogger(__name__)
acoes_bp = Blueprint("acoes", __name__)

_BRT = timezone(timedelta(hours=-3))
_LOG = Path(__file__).resolve().parents[1] / "logs"


@acoes_bp.route("/acoes")
def acoes_page():
    return render_template("acoes_monitor.html")


@acoes_bp.route("/api/acoes/symbols")
def api_symbols():
    from services.acoes_config import get_symbols, cfg_for
    from services.acoes_go_paths import go_for, GO_PATHS

    syms = get_symbols()
    auto_ready = len(GO_PATHS) > 0
    return jsonify({
        "ok": True,
        "symbols": syms,
        "lots": {s: cfg_for(s).get("volume", 100) for s in syms},
        "auto_ready": auto_ready,
        "go_count": len(GO_PATHS),
        "note": (
            f"{len(GO_PATHS)} path(s) 🟢 GO wired (magics 20260810–20260874) · "
            "scan ao vivo · fill se ACOES_AUTO_ON=1."
            if auto_ready else
            "Aguardando 🟢 GO. Sem auto."
        ),
        "go_by_sym": {s: [p["setup"] for p in go_for(s)] for s in syms},
    })


@acoes_bp.route("/api/acoes/status")
def api_status():
    from services.acoes_config import get_symbols, cfg_for
    from services.acoes_go_paths import go_for, active_path_names
    from services.acoes_runtime import get_runtime_snapshot

    sym = (request.args.get("symbol") or "").upper()
    if not sym:
        syms = get_symbols()
        sym = syms[0] if syms else ""
    paths = go_for(sym)
    # top por net (evita parede de 20 nomes no board)
    top_paths = sorted(paths, key=lambda p: -(p.get("net") or 0))[:5]
    rt = get_runtime_snapshot(sym)
    return jsonify({
        "ok": True,
        "symbol": sym,
        "enabled": bool(rt.get("auto_on")) if "auto_on" in rt else False,
        "status": "live" if paths else "awaiting_go",
        "message": rt.get("motivo") or (
            f"{sym}: {len(paths)} path(s) GO"
            if paths else f"{sym}: sem GO"
        ),
        "active_paths": active_path_names(sym),
        "paths_n": len(paths),
        "paths_top": [
            {"setup": p["setup"], "tf": p.get("tf"), "net": p.get("net"),
             "pf": p.get("pf"), "family": p.get("family")}
            for p in top_paths
        ],
        "cfg": cfg_for(sym),
        "scan": rt.get("scan") or "IDLE",
        "executed_today": rt.get("executed_sym") or 0,
        "positions": rt.get("positions_sym") or [],
        "pnl_day": rt.get("pnl_day_sym") or 0.0,
        "pnl_realized": rt.get("pnl_realized_sym") or 0.0,
        "pnl_floating": rt.get("pnl_floating_sym") or 0.0,
        "trades_today": rt.get("trades_sym") or [],
        "go_detail": paths,
        "day_stats": rt.get("day_stats") or {},
        "ts": datetime.now(_BRT).isoformat(timespec="seconds"),
        "runtime_ts": rt.get("ts"),
    })


@acoes_bp.route("/api/acoes/ranking")
def api_ranking():
    from services.acoes_go_paths import load_ranking
    limit = int(request.args.get("limit") or 80)
    return jsonify(load_ranking(limit=limit))


@acoes_bp.route("/api/acoes/overview")
def api_overview():
    from services.acoes_config import get_symbols
    from services.acoes_go_paths import go_for, GO_PATHS, load_ranking
    from services.acoes_runtime import get_runtime_snapshot, refresh_live_book

    # força book fresco (MT5+CSV) — board não fica zerado pós-restart / fora do pregão
    try:
        book = refresh_live_book()
    except Exception as exc:
        logger.debug("overview refresh: %s", exc)
        book = {}

    syms = get_symbols()
    rk = load_ranking(limit=40)
    rt = get_runtime_snapshot()
    if book:
        pnls = book.get("pnl_day") or {}
        pnl_r = book.get("pnl_realized") or {}
        pnl_f = book.get("pnl_floating") or {}
        poss_all = book.get("positions") or []
        exec_map = book.get("executed_today") or {}
        day_stats = book.get("day_stats") or {}
        trades = book.get("trades_today") or []
    else:
        pnls = rt.get("pnl_day") or {}
        pnl_r = rt.get("pnl_realized") or {}
        pnl_f = rt.get("pnl_floating") or {}
        poss_all = rt.get("positions") or []
        exec_map = rt.get("executed_today") or {}
        day_stats = rt.get("day_stats") or {}
        trades = rt.get("trades_today") or []

    cards = []
    for s in syms:
        paths = go_for(s)
        by = (rk.get("summary") or {}).get("by_sym", {}).get(s, {})
        scans = rt.get("scans") or {}
        poss = [p for p in poss_all if (p.get("symbol") or "").upper() == s]
        top = sorted(paths, key=lambda p: -(p.get("net") or 0))[:3]
        cards.append({
            "symbol": s,
            "go_paths": [p["setup"] for p in paths],
            "go_n": len(paths),
            "paths_top": [p["setup"] for p in top],
            "battery_n": by.get("n", 0),
            "battery_go": by.get("go", 0),
            "battery_yellow": by.get("yellow", 0),
            "status": "GO" if paths else "WAIT",
            "scan": scans.get(s, "IDLE"),
            "pnl_day": float(pnls.get(s, 0.0) or 0.0),
            "pnl_realized": float(pnl_r.get(s, 0.0) or 0.0),
            "pnl_floating": float(pnl_f.get(s, 0.0) or 0.0),
            "positions": len(poss),
            "positions_detail": poss,
            "executed_today": int(exec_map.get(s, 0) or 0),
            "motivo": (rt.get("motivos") or {}).get(s, ""),
        })
    return jsonify({
        "ok": True,
        "auto_ready": len(GO_PATHS) > 0,
        "go_total": len(GO_PATHS),
        "auto_on": rt.get("auto_on", False) if isinstance(rt, dict) else False,
        "cards": cards,
        "day_stats": day_stats,
        "trades_today": trades,
        "positions": poss_all,
        "ranking_file": rk.get("file"),
        "ranking_summary": rk.get("summary"),
        "top": rk.get("rows", [])[:15],
        "wired": [
            {"setup": p["setup"], "symbol": p["symbol"], "tf": p["tf"],
             "magic": p["magic"], "net": p["net"], "pf": p["pf"]}
            for p in GO_PATHS
        ],
        "ts": datetime.now(_BRT).isoformat(timespec="seconds"),
    })


@acoes_bp.route("/api/acoes/history")
def api_history():
    """Trades do dia (+ histórico CSV). Prefere book MT5+CSV; fallback CSV puro."""
    from services.acoes_runtime import refresh_live_book, get_runtime_snapshot

    sym = (request.args.get("symbol") or "").upper()
    day_only = (request.args.get("day") or "1").strip() != "0"
    rows = []
    source = "csv"

    book = {}
    try:
        book = refresh_live_book() or {}
        rows = list(book.get("trades_today") or [])
        source = "mt5+csv" if book.get("ok") else "csv"
        if not day_only:
            path = _LOG / "acoes_trades.csv"
            if path.exists():
                import csv
                today = datetime.now(_BRT).date().isoformat()
                with open(path, encoding="utf-8") as f:
                    for r in csv.DictReader(f):
                        dt = str(r.get("datetime_brt") or r.get("exit_brt") or "")
                        if dt.startswith(today):
                            continue
                        rows.append(r)
    except Exception as exc:
        logger.debug("history book: %s", exc)
        path = _LOG / "acoes_trades.csv"
        if path.exists():
            try:
                import csv
                with open(path, encoding="utf-8") as f:
                    rows = list(csv.DictReader(f))
            except Exception as e2:
                return jsonify({"ok": False, "error": str(e2), "rows": []})

    if sym:
        rows = [r for r in rows if (r.get("symbol") or "").upper() == sym]

    def _k(r):
        return str(r.get("exit_brt") or r.get("datetime_brt") or "")

    rows = sorted(rows, key=_k, reverse=True)[:120]
    return jsonify({
        "ok": True,
        "rows": rows,
        "day_stats": book.get("day_stats") or {},
        "source": source,
        "file": "acoes_trades.csv",
    })


@acoes_bp.route("/api/acoes/runtime-alive")
def api_runtime_alive():
    from services.acoes_runtime import get_runtime_snapshot
    rt = get_runtime_snapshot()
    ts = rt.get("ts")
    alive = bool(rt.get("running"))
    return jsonify({"ok": True, "alive": alive, "ts": ts,
                    "auto_on": rt.get("auto_on", False),
                    "scans": rt.get("scans") or {},
                    "day_stats": rt.get("day_stats") or {}})
