"""
blueprints/scalper_bp.py
Blueprint Flask para o modulo Scalper - totalmente isolado do app principal.

Rotas:
  GET  /scalper/               -> pagina standalone do scalper
  GET  /api/scalper/data       -> tick + book + agressao + tape
  GET  /api/scalper/position   -> posicao aberta atual
  POST /api/scalper/execute    -> abre posicao manualmente
  POST /api/scalper/close      -> fecha posicao manualmente
  POST /api/scalper/auto-state -> liga/desliga auto-trade
  POST /api/scalper/auto-check -> verifica sinal e executa se regras OK
  GET  /api/scalper/session    -> estatisticas da sessao
  POST /api/scalper/session/reset -> zera estatisticas
  GET/POST /api/scalper/sim-mode  -> liga/desliga modo simulacao
"""
import time
import logging
from datetime import datetime
from flask import Blueprint, render_template, jsonify, request

logger = logging.getLogger(__name__)


# ── Trade logger (opcional, nunca bloqueia o trading) ──────────────────────
try:
    from services.trade_logger import (
        log_entry as _log_entry,
        log_exit  as _log_exit,
        get_log_summary,
    )
    _TRADE_LOG = True
except Exception:
    _TRADE_LOG = False


def _scalper_sim_mode():
    try:
        from services.scalper_service import get_sim_mode
        return get_sim_mode()
    except Exception:
        return False


def _safe_log_entry(symbol, direcao, result, auto, macro_=None, context_=None):
    """Loga abertura de posição sem jamais propagar erros."""
    if not _TRADE_LOG:
        return
    try:
        _log_entry(
            symbol=symbol,
            direcao=direcao,
            entry_price=result.get("price", 0),
            tp_price=result.get("tp", 0),
            sl_price=result.get("sl", 0),
            volume=result.get("volume", 100),
            mode="SIM" if _scalper_sim_mode() else "REAL",
            auto=auto,
            macro=macro_,
            context=context_,
        )
    except Exception as exc:
        logger.warning("safe_log_entry: %s", exc)


def _safe_log_exit(symbol, exit_price, profit, reason):
    """Loga fechamento de posição sem jamais propagar erros."""
    if not _TRADE_LOG:
        return
    try:
        _log_exit(symbol=symbol, exit_price=exit_price, profit=profit, exit_reason=reason)
    except Exception as exc:
        logger.warning("safe_log_exit: %s", exc)


def _safe_notify_entry(symbol, direcao, result, auto, macro_=None):
    """Envia notificação Telegram de entrada sem jamais propagar erros."""
    try:
        from services.scalper_telegram import notify_entry as _ntfy_e
        macro_ = macro_ or {}
        vwap_d = macro_.get("vwap", {}) or {}
        time_d = macro_.get("time", {}) or {}
        sc_buy = macro_.get("score_buy", {}) or {}
        sc_sel = macro_.get("score_sell", {}) or {}
        score  = (sc_buy if direcao == "COMPRA" else sc_sel).get("score", 0)
        _ntfy_e(
            symbol=symbol, direcao=direcao,
            price=result.get("price", 0),
            tp=result.get("tp", 0),
            sl=result.get("sl", 0),
            score=score,
            vwap_context=vwap_d.get("context", "NEUTRO"),
            session=time_d.get("session", ""),
            auto=auto,
            mode="SIM" if _scalper_sim_mode() else "REAL",
        )
    except Exception as exc:
        logger.warning("safe_notify_entry: %s", exc)


def _safe_notify_exit(symbol, info, profit, reason):
    """Envia notificação Telegram de saída sem jamais propagar erros."""
    try:
        from services.scalper_telegram import notify_exit as _ntfy_x
        from services.trade_logger import _pending as _tp
        pending = _tp.get(symbol, {})
        _ntfy_x(
            symbol=symbol,
            direcao=pending.get("direcao", "?"),
            entry_price=pending.get("entry_price", 0),
            exit_price=(info or {}).get("price", 0),
            profit=profit,
            exit_reason=reason,
            duration_s=None,
        )
    except Exception as exc:
        logger.warning("safe_notify_exit: %s", exc)

scalper_bp = Blueprint("scalper", __name__, url_prefix="")

# Estado da sessao (em memoria, por processo Flask)
_session = {
    "trades":  0,
    "wins":    0,
    "losses":  0,
    "breakevens": 0,
    "pnl":     0.0,
    "history": [],
}

# Estado do auto-trade
_auto = {
    "enabled":            False,
    "signal_count":       0,
    "last_signal":        "",
    "last_trade_ts":      0.0,
    # ── Proteção por sequência de stops ───────────────────────────────────
    "consecutive_losses": 0,      # stops seguidos (resetado no WIN ou BE)
    "paused_until":       0.0,    # timestamp Unix — auto bloqueado enquanto time() < paused_until
    # ── Proteção direcional ───────────────────────────────────────────────
    "last_trade_dir":     "",     # direção do último trade aberto
    "dir_loss_streak":    {"COMPRA": 0, "VENDA": 0},  # LOSSes consecutivos por direção
}

# ── Parâmetros da pausa automática ───────────────────────────────────────────
_pause_cfg = {
    "max_consecutive_losses": 3,
    "pause_duration_sec":     1800,
}


# ── Helper: atualiza contador de stops consecutivos ──────────────────────────
def _update_consecutive_losses(profit: float) -> None:
    direction = _auto.get("last_trade_dir", "")
    if profit >= -0.01:  # WIN ou BE
        _auto["consecutive_losses"] = 0
        if direction in _auto["dir_loss_streak"]:
            _auto["dir_loss_streak"][direction] = 0
        return
    # SL confirmado
    _auto["consecutive_losses"] += 1
    if direction in _auto["dir_loss_streak"]:
        _auto["dir_loss_streak"][direction] += 1
        logger.info("Scalper dir_loss_streak[%s] = %d",
                    direction, _auto["dir_loss_streak"][direction])
    n   = _pause_cfg["max_consecutive_losses"]
    dur = _pause_cfg["pause_duration_sec"]
    if _auto["consecutive_losses"] >= n:
        _auto["paused_until"] = time.time() + dur
        logger.warning("Scalper: %d stops consecutivos — pausado por %ds (ate %s)",
                       _auto["consecutive_losses"], dur,
                       datetime.fromtimestamp(_auto["paused_until"]).strftime("%H:%M"))
        try:
            from services.scalper_telegram import _send_async
            mins = dur // 60
            _send_async(
                "Scalper Pausa Automatica\n"
                f"{_auto['consecutive_losses']} stops seguidos.\n"
                f"Auto-trade pausado por {mins} minutos.\n"
                "Use /sc_on para reativar."
            )
        except Exception:
            pass


# Pagina
@scalper_bp.route("/scalper/")
@scalper_bp.route("/scalper")
def scalper_page():
    return render_template("scalper.html")


# Dados de mercado
@scalper_bp.route("/api/scalper/data")
def api_scalper_data():
    from services.scalper_service import get_scalper_data
    symbol       = request.args.get("symbol", "WDON26")
    aggr_seconds = int(request.args.get("aggr_seconds", "30"))
    data = get_scalper_data(symbol, aggr_seconds=aggr_seconds)
    return jsonify(data)


# Posicao aberta
@scalper_bp.route("/api/scalper/position")
def api_scalper_position():
    from services.scalper_service import get_scalper_position
    symbol = request.args.get("symbol", "WDON26")
    pos, err = get_scalper_position(symbol)
    return jsonify({"ok": err is None, "position": pos, "error": err})


# Execucao manual
@scalper_bp.route("/api/scalper/execute", methods=["POST"])
def api_scalper_execute():
    from services.scalper_service import execute_scalper_trade, get_scalper_position
    body     = request.get_json(silent=True) or {}
    symbol   = body.get("symbol",   "WDON26")
    acao     = body.get("acao",     "")
    volume   = float(body.get("volume",   100))
    tp_ticks       = int(body.get("tp_ticks",      5))
    sl_ticks       = int(body.get("sl_ticks",      2))
    use_atr_sizing = bool(body.get("use_atr_sizing", True))

    pos, _ = get_scalper_position(symbol)
    if pos:
        return jsonify({"ok": False, "error": "Ja ha posicao aberta."})

    result, err = execute_scalper_trade(symbol, acao, volume, tp_ticks, sl_ticks, use_atr_sizing)
    if result and not err:
        _session["trades"] += 1
        _auto["last_trade_ts"] = time.time()
        _session["history"].append({
            "time":  datetime.now().strftime("%H:%M:%S"),
            "acao":  acao, "auto": False,
            "price": result.get("price"),
            "tp":    result.get("tp"),
            "sl":    result.get("sl"),
            "order": result.get("order"),
        })
        _session["history"] = _session["history"][-50:]
        # Log da entrada (manual)
        try:
            from services.scalper_service import get_scalper_data as _gsd
            snap = _gsd(symbol)
            _safe_log_entry(symbol, acao, result, False, snap.get("macro"), snap.get("context"))
            # Notificação Telegram
            _safe_notify_entry(symbol, acao, result, False, snap.get("macro"))
        except Exception as _le:
            logger.warning("log_entry manual: %s", _le)

    return jsonify({"ok": err is None, "result": result, "error": err})


# Fechar manualmente
@scalper_bp.route("/api/scalper/close", methods=["POST"])
def api_scalper_close():
    from services.scalper_service import close_scalper_position
    body   = request.get_json(silent=True) or {}
    symbol = body.get("symbol", "WDON26")

    info, err = close_scalper_position(symbol)
    if info and not err:
        profit = info.get("profit", 0)
        _session["pnl"] += profit
        if abs(profit) < 0.01:
            _session["breakevens"] += 1
        elif profit > 0:
            _session["wins"] += 1
        else:
            _session["losses"] += 1
        _update_consecutive_losses(profit)
        # Log + notificação da saída manual
        _safe_log_exit(symbol, info.get("price", 0), profit, "manual")
        _safe_notify_exit(symbol, info, profit, "manual")

    return jsonify({"ok": err is None, "info": info, "error": err})


# Auto-trade: liga/desliga
@scalper_bp.route("/api/scalper/auto-state", methods=["GET", "POST"])
def api_scalper_auto_state():
    if request.method == "POST":
        body = request.get_json(silent=True) or {}
        _auto["enabled"]      = bool(body.get("enabled", False))
        _auto["signal_count"] = 0
        _auto["last_signal"]  = ""
    now_ts = time.time()
    paused = _auto["paused_until"] > now_ts
    return jsonify({
        "ok":                 True,
        "enabled":            _auto["enabled"],
        "consecutive_losses": _auto["consecutive_losses"],
        "paused":             paused,
        "paused_until":       _auto["paused_until"],
        "paused_remaining_s": max(0, int(_auto["paused_until"] - now_ts)) if paused else 0,
        "dir_loss_streak":    _auto["dir_loss_streak"],
    })


# Auto-trade: verifica sinal e executa se regras OK
@scalper_bp.route("/api/scalper/auto-check", methods=["POST"])
def api_scalper_auto_check():
    from services.scalper_service import execute_scalper_trade, get_scalper_position
    body          = request.get_json(silent=True) or {}
    symbol        = body.get("symbol",        "WDON26")
    signal        = body.get("signal",        "NEUTRO")
    buy_pct       = float(body.get("buy_pct",       50))
    sell_pct      = float(body.get("sell_pct",      50))
    volume        = float(body.get("volume",        100))
    tp_ticks       = int(body.get("tp_ticks",       5))
    sl_ticks       = int(body.get("sl_ticks",       2))
    use_atr_sizing = bool(body.get("use_atr_sizing", True))
    threshold_pct  = float(body.get("threshold_pct", 65))
    cooldown_sec  = int(body.get("cooldown_sec",  30))
    confirm_n     = int(body.get("confirm_n",      3))
    b3_open       = bool(body.get("b3_open",      True))
    max_daily     = int(body.get("max_daily",     15))

    def _deny(reason):
        return jsonify({"ok": True, "action": "NONE", "reason": reason,
                        "confirm": _auto["signal_count"]})

    if not _auto["enabled"]:
        return _deny("Auto-trade desabilitado.")
    if not b3_open:
        return _deny("Fora do horario B3.")

    # ── Pausa automática por stops consecutivos ───────────────────────────
    if _auto["paused_until"] > time.time():
        remaining = int(_auto["paused_until"] - time.time())
        mins, secs = remaining // 60, remaining % 60
        n = _pause_cfg["max_consecutive_losses"]
        return _deny(f"Pausa automatica apos {n} stops seguidos. Retorna em {mins}m{secs:02d}s")

    # Limite diário de trades automáticos
    if _session["trades"] >= max_daily:
        return _deny(f"🛑 Limite diário de {max_daily} trades atingido. Reinicie a sessão para continuar.")

    elapsed = time.time() - _auto["last_trade_ts"]
    if elapsed < cooldown_sec:
        return _deny(f"Cooldown: {int(cooldown_sec - elapsed)}s restantes.")

    if signal == "NEUTRO":
        _auto["signal_count"] = 0
        _auto["last_signal"]  = ""
        return _deny("Sinal NEUTRO.")

    agg_pct = buy_pct if signal == "COMPRA" else sell_pct
    if agg_pct < threshold_pct:
        _auto["signal_count"] = 0
        _auto["last_signal"]  = ""
        return _deny(f"Agressao {agg_pct:.1f}% < threshold {threshold_pct:.0f}%.")

    if signal == _auto["last_signal"]:
        _auto["signal_count"] += 1
    else:
        _auto["signal_count"] = 1
        _auto["last_signal"]  = signal

    if _auto["signal_count"] < confirm_n:
        return _deny(f"Confirmando sinal {_auto['signal_count']}/{confirm_n}...")

    pos, _ = get_scalper_position(symbol)
    if pos:
        _auto["signal_count"] = 0
        return _deny("Posicao ja aberta.")

    # ── Validação server-side: score + delta divergência ──────────────────────
    try:
        from services.scalper_service import get_scalper_data as _gsd_sv
        snap_sv    = _gsd_sv(symbol)
        macro_sv   = snap_sv.get("macro", {})
        # Score server-side
        score_key  = "score_buy" if signal == "COMPRA" else "score_sell"
        score_obj  = macro_sv.get(score_key, {})
        sv_score   = score_obj.get("score", 100)
        sv_blocked = score_obj.get("hard_blocked", False)
        sv_cum_bias= score_obj.get("cum_bias", "NEUTRO")
        sv_cum_pct = score_obj.get("cum_pct",  50.0)
        score_min_sv = int(body.get("score_min", 60))

        # Hard-block 1: score server-side abaixo do mínimo configurado
        if sv_score < score_min_sv:
            _auto["signal_count"] = 0
            return _deny(f"🚫 Score srv {sv_score:.0f} < mín {score_min_sv} — entrada bloqueada")

        # Hard-block 3: VWAP direction filter — só operar a favor da tendência
        # BEAR = preço abaixo da VWAP intraday → só VENDA permitida
        # BULL = preço acima da VWAP intraday → só COMPRA permitida
        # NEUTRO → permite ambas as direções
        vwap_ctx = macro_sv.get("vwap", {}).get("context", "NEUTRO")
        use_vwap_filter = bool(body.get("use_vwap_filter", True))
        if use_vwap_filter and vwap_ctx != "NEUTRO":
            if vwap_ctx == "BEAR" and signal == "COMPRA":
                _auto["signal_count"] = 0
                return _deny(f"🚫 VWAP BEAR — apenas VENDA (bloqueando COMPRA contra-tendência)")
            if vwap_ctx == "BULL" and signal == "VENDA":
                _auto["signal_count"] = 0
                return _deny(f"🚫 VWAP BULL — apenas COMPRA (bloqueando VENDA contra-tendência)")

        # Hard-block 2: divergência delta severa (buy_pct extremo contra direção)
        if sv_blocked:
            _auto["signal_count"] = 0
            return _deny(
                f"Hard-block: Delta {sv_cum_bias} ({sv_cum_pct:.0f}% C) "
                f"fluxo acumulado contra {signal}"
            )

        # ── Uplift direcional: após 2+ LOSSes na mesma direção ───────────
        dir_streak = _auto["dir_loss_streak"].get(signal, 0)
        if dir_streak >= 2:
            uplift   = 5
            required = score_min_sv + uplift
            if sv_score < required:
                _auto["signal_count"] = 0
                return _deny(
                    f"Uplift direcional: {dir_streak} LOSSes em {signal}. "
                    f"Score {sv_score:.0f} < exigido {required} (min {score_min_sv}+{uplift})"
                )
            logger.info("Uplift direcional APROVADO: %s streak=%d score=%.0f >= %d",
                        signal, dir_streak, sv_score, required)

    except Exception as _sv_exc:
        logger.warning("Validação server-side ignorada: %s", _sv_exc)

    result, err = execute_scalper_trade(symbol, signal, volume, tp_ticks, sl_ticks, use_atr_sizing)
    if result and not err:
        _session["trades"] += 1
        _auto["last_trade_ts"]  = time.time()
        _auto["last_trade_dir"] = signal
        _auto["signal_count"]   = 0
        _auto["last_signal"]    = ""
        _session["history"].append({
            "time":  datetime.now().strftime("%H:%M:%S"),
            "acao":  signal, "auto": True,
            "price": result.get("price"),
            "tp":    result.get("tp"),
            "sl":    result.get("sl"),
            "order": result.get("order"),
        })
        _session["history"] = _session["history"][-50:]
        # Log da entrada (auto)
        try:
            from services.scalper_service import get_scalper_data as _gsd2
            snap2 = _gsd2(symbol)
            _safe_log_entry(symbol, signal, result, True, snap2.get("macro"), snap2.get("context"))
            # Notificação Telegram
            _safe_notify_entry(symbol, signal, result, True, snap2.get("macro"))
        except Exception as _le2:
            logger.warning("log_entry auto: %s", _le2)
        return jsonify({"ok": True, "action": "EXECUTED", "acao": signal,
                        "result": result, "confirm": 0})

    _auto["signal_count"] = 0
    return jsonify({"ok": False, "action": "FAILED", "error": err, "confirm": 0})


# Sessao: stats + historico
@scalper_bp.route("/api/scalper/session")
def api_scalper_session():
    return jsonify({"ok": True, "session": _session})


@scalper_bp.route("/api/scalper/session/reset", methods=["POST"])
def api_scalper_session_reset():
    _session["trades"]     = 0
    _session["wins"]      = 0
    _session["losses"]    = 0
    _session["breakevens"] = 0
    _session["pnl"]       = 0.0
    _session["history"]   = []
    _auto["signal_count"]       = 0
    _auto["last_signal"]        = ""
    _auto["consecutive_losses"] = 0
    _auto["paused_until"]       = 0.0
    _auto["dir_loss_streak"]    = {"COMPRA": 0, "VENDA": 0}
    return jsonify({"ok": True})


# Pausa: reset manual
@scalper_bp.route("/api/scalper/reset-pause", methods=["POST"])
def api_scalper_reset_pause():
    _auto["paused_until"]       = 0.0
    _auto["consecutive_losses"] = 0
    _auto["dir_loss_streak"]    = {"COMPRA": 0, "VENDA": 0}
    logger.info("Scalper: pausa automatica removida manualmente.")
    return jsonify({"ok": True, "message": "Pausa removida. Auto-trade liberado."})


# Pausa: configuração
@scalper_bp.route("/api/scalper/pause-config", methods=["GET", "POST"])
def api_scalper_pause_config():
    if request.method == "POST":
        body = request.get_json(silent=True) or {}
        if "max_consecutive_losses" in body:
            v = int(body["max_consecutive_losses"])
            _pause_cfg["max_consecutive_losses"] = max(1, min(10, v))
        if "pause_duration_sec" in body:
            v = int(body["pause_duration_sec"])
            _pause_cfg["pause_duration_sec"] = max(60, min(7200, v))
    now_ts = time.time()
    return jsonify({
        "ok":                     True,
        "max_consecutive_losses": _pause_cfg["max_consecutive_losses"],
        "pause_duration_sec":     _pause_cfg["pause_duration_sec"],
        "pause_duration_min":     _pause_cfg["pause_duration_sec"] // 60,
        "paused_until":           _auto["paused_until"],
        "paused_remaining_s":     max(0, int(_auto["paused_until"] - now_ts)),
    })


# Registro de fechamento via TP/SL
@scalper_bp.route("/api/scalper/register-close", methods=["POST"])
def api_scalper_register_close():
    body   = request.get_json(silent=True) or {}
    profit = float(body.get("profit", 0))
    reason = body.get("reason", "MT5")
    symbol = body.get("symbol", "")

    _session["pnl"] += profit
    if abs(profit) < 0.01:
        _session["breakevens"] += 1
    elif profit > 0:
        _session["wins"] += 1
    else:
        _session["losses"] += 1
    _update_consecutive_losses(profit)

    logger.info("Scalper fechamento registrado: reason=%s profit=%.2f symbol=%s", reason, profit, symbol)
    if symbol:
        _safe_log_exit(symbol, 0.0, profit, reason)
        _safe_notify_exit(symbol, {}, profit, reason)
    return jsonify({"ok": True})


# Busca P&L real do historico MT5 pelo ticket
@scalper_bp.route("/api/scalper/last-deal")
def api_scalper_last_deal():
    from services.scalper_service import _mt5_init, get_sim_mode, get_sim_closed_profit
    ticket = request.args.get("ticket", type=int)
    if not ticket:
        return jsonify({"ok": False, "error": "ticket obrigatorio"})

    if get_sim_mode():
        profit = get_sim_closed_profit(ticket)
        if profit is not None:
            return jsonify({"ok": True, "found": True, "profit": round(float(profit), 2)})
        return jsonify({"ok": True, "found": False, "profit": None})

    try:
        import MetaTrader5 as mt5
        if not _mt5_init():
            return jsonify({"ok": False, "profit": None})
        deals = mt5.history_deals_get(position=ticket)
        if deals is None or len(deals) == 0:
            return jsonify({"ok": True, "found": False, "profit": None})
        profit = sum(
            d.profit for d in deals
            if hasattr(d, "entry") and d.entry == mt5.DEAL_ENTRY_OUT
        )
        if profit == 0:
            profit = sum(d.profit for d in deals)
        return jsonify({"ok": True, "found": True, "profit": round(profit, 2)})
    except Exception as exc:
        logger.warning("last-deal error: %s", exc)
        return jsonify({"ok": False, "profit": None, "error": str(exc)})


# Modo Simulacao: liga/desliga
@scalper_bp.route("/api/scalper/sim-mode", methods=["GET", "POST"])
def api_scalper_sim_mode():
    from services.scalper_service import set_sim_mode, get_sim_mode
    if request.method == "POST":
        body    = request.get_json(silent=True) or {}
        enabled = bool(body.get("enabled", False))
        set_sim_mode(enabled)
        if enabled:
            _auto["enabled"]      = False
            _auto["signal_count"] = 0
            _auto["last_signal"]  = ""
    return jsonify({"ok": True, "sim_mode": get_sim_mode()})



# Trade Log: resumo estatístico para análise de assertividade
@scalper_bp.route("/api/scalper/trade-log")
def api_scalper_trade_log():
    try:
        from services.trade_logger import get_log_summary
        return jsonify({"ok": True, "data": get_log_summary()})
    except Exception as exc:
        logger.warning("trade-log error: %s", exc)
        return jsonify({"ok": False, "error": str(exc)})
# Break-even: move SL para entry
@scalper_bp.route("/api/scalper/move-sl", methods=["POST"])
def api_scalper_move_sl():
    from services.scalper_service import move_sl_to_breakeven
    body   = request.get_json(silent=True) or {}
    symbol = body.get("symbol", "WDON26")
    result, err = move_sl_to_breakeven(symbol)
    return jsonify({"ok": err is None, "result": result, "error": err})
