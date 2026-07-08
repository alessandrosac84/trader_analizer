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


# ── Rejection Logger (P1 Scalper V2) ──────────────────────────────────────
try:
    from services.rejection_logger import (
        log_rejection as _log_rejection,
        get_rejection_stats as _get_rejection_stats,
        CODE_SCORE_BELOW_MINIMUM,
        CODE_LOW_TICK_CONSISTENCY,
        CODE_ENTRY_TOO_LATE,
        CODE_VWAP_DIRECTION_BLOCK,
        CODE_COOLDOWN_ACTIVE,
        CODE_MAX_DAILY_TRADES,
        CODE_LOW_AGGRESSION,
        CODE_CONSECUTIVE_LOSSES_PAUSE,
        CODE_POSITION_ALREADY_OPEN,
        CODE_OUTSIDE_TRADING_HOURS,
        CODE_HARD_BLOCK_DELTA,
        CODE_DIRECTIONAL_UPLIFT,
        CODE_AUTO_DISABLED,
        CODE_SIGNAL_NEUTRAL,
        CODE_LOW_VOLATILITY,
    )
    _REJECTION_LOG = True
except Exception as _rej_exc:
    logger.warning("rejection_logger não disponível: %s", _rej_exc)
    _REJECTION_LOG = False
    def _log_rejection(*a, **kw): pass
    def _get_rejection_stats(*a, **kw): return {"ok": False, "total": 0, "by_code": {}, "recent": []}


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

# ── Reconciliação de P&L (rede de segurança das "zeradas") ────────────────
# Se o trade foi gravado com profit≈0 mas o motivo era TP/SL, o deal do MT5
# provavelmente ainda não tinha aparecido no histórico. Aqui re-consultamos o
# histórico por até ~2min; ao achar o P&L real, corrigimos a linha do CSV, a
# sessão em memória e enviamos uma correção no Telegram.
import threading as _threading

_reconcile_lock = _threading.Lock()


def _reconcile_worker(ticket: int, symbol: str, row_id, reason: str):
    import time as _t
    try:
        # espera o deal aparecer (retry longo)
        profit, found = _fetch_realized_profit(ticket, tries=40, delay=3.0)
        if not found or profit is None or abs(profit) < 0.01:
            logger.info("Reconcile: ticket=%s sem P&L real (segue como BE).", ticket)
            return
        # corrige o CSV
        try:
            from services.trade_logger import patch_exit_by_id
            patch_exit_by_id(row_id, profit, exit_reason=reason)
        except Exception as exc:
            logger.warning("Reconcile patch CSV falhou: %s", exc)
        # ajusta a sessão em memória (remove do BE, soma no win/loss)
        with _reconcile_lock:
            if _session.get("breakevens", 0) > 0:
                _session["breakevens"] -= 1
            if profit > 0:
                _session["wins"] = _session.get("wins", 0) + 1
            else:
                _session["losses"] = _session.get("losses", 0) + 1
            _session["pnl"] = _session.get("pnl", 0.0) + profit
        # correção no Telegram
        try:
            from services.scalper_telegram import _send_async as _tg
            res = "✅ GAIN" if profit > 0 else "❌ LOSS"
            _tg(f"⚡ <b>SCALPER — correção de resultado</b>\n"
                f"O trade #{row_id} ({symbol}) foi registrado como zerado, mas o "
                f"resultado real foi <b>{res}</b>: {('R$%+.2f' % profit).replace('.', ',')}")
        except Exception as exc:
            logger.debug("Reconcile telegram: %s", exc)
        logger.info("Reconcile OK: ticket=%s id=%s profit=%.2f", ticket, row_id, profit)
    except Exception as exc:
        logger.warning("Reconcile worker error: %s", exc)


def _enqueue_reconcile(ticket, symbol, row_id, reason):
    if not ticket or row_id is None:
        return
    _threading.Thread(target=_reconcile_worker,
                      args=(int(ticket), symbol, row_id, reason),
                      daemon=True, name=f"reconcile-{ticket}").start()


scalper_bp = Blueprint("scalper", __name__, url_prefix="")

# Estado da sessao (em memoria, por processo Flask)
# Inicializado com os trades de hoje do CSV para sobreviver reinicializacoes.
_session = {
    "trades":     0,
    "wins":       0,
    "losses":     0,
    "breakevens": 0,
    "pnl":        0.0,
    "history":    [],
}
try:
    from services.trade_logger import get_today_session_stats as _gts
    _s = _gts()
    _session["trades"]     = _s.get("trades",     0)
    _session["wins"]       = _s.get("wins",        0)
    _session["losses"]     = _s.get("losses",      0)
    _session["breakevens"] = _s.get("breakevens",  0)
    _session["pnl"]        = _s.get("pnl",         0.0)
    del _gts, _s
except Exception:
    pass  # em caso de erro, mantém zeros — nao quebra o Scalper

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

# ── Guard-rails de assertividade do Scalper (v6.4) ──────────────────────────
# Regras anti-overtrading e anti-exaustão adicionadas ao gate de auto-check.
# Objetivo: menos trades, melhores. NÃO afeta o Monitor MT5.
_ASSERT = {
    "min_cooldown_floor_sec": 45,        # piso de cooldown (mesmo se o front mandar menos)
    "max_trades_per_min":      2,        # teto de execuções em 60s (mata o "machine-gun")
    "good_sessions": {"PRIME", "BOM"},   # só opera em sessão definida e boa
    "block_undefined_session": True,     # bloqueia sessão "?"/vazia
    # Teto de score por símbolo: acima disso = zona de exaustão (dados: ~0% acerto)
    "score_ceiling": {"BITN26": 84, "_default": 88},
}
_recent_exec_ts: list = []   # timestamps das últimas execuções (teto por minuto)

# ── Parâmetros de execução por símbolo ──────────────────────────────────────
# Cada ativo tem thresholds calibrados para sua liquidez.
# O frontend pode sobrescrever individualmente via body do auto-check.
_SYMBOL_TRADE_CFG: dict[str, dict] = {
    "_default": {
        "score_min":       60,     # score mínimo para entrar
        "threshold_pct":   65.0,   # agressão mínima (buy_pct ou sell_pct)
        "confirm_n":        3,     # confirmações consecutivas do sinal
        "cooldown_sec":    30,     # cooldown entre trades (segundos)
        "max_daily":       15,     # limite diário de trades
        "tp_ticks":         5,     # take profit em ticks
        "sl_ticks":         2,     # stop loss em ticks
        "use_atr_sizing":  True,   # dimensionar TP/SL pelo ATR
        "use_vwap_filter": True,   # filtro direcional VWAP
        # ── Time Exits (P3+P4 Scalper V2) ────────────────────────────────
        "max_position_time_sec": 180,   # P3: fechar com TIME_EXIT após 3 min
        "time_stop_seconds":      30,   # P4: verificar progresso após 30s
        "minimum_progress_r":    0.3,   # P4: mínimo 0.3R de lucro em 30s → TIME_STOP
        # ── Bloqueio de abertura ──────────────────────────────────────────
        "opening_block_minutes": 0,     # 0 = sem bloqueio de abertura
    },
    "WDON26": {
        # ── Bloqueio primeiros 15min do pregão (09:00-09:15) ─────────────
        # Ruído de abertura do Dólar Futuro causa stops frequentes.
        "opening_block_minutes": 15,
    },
    "WDOM26": {
        "opening_block_minutes": 15,    # mesmo bloqueio para contrato seguinte
    },
    "WINM26": {
        "score_min":       60,
        "threshold_pct":   65.0,
        "confirm_n":        3,
        "cooldown_sec":    30,
        "max_daily":       20,     # WIN tem mais liquidez, mais oportunidades
        "tp_ticks":        10,     # WIN tick = R$1, precisa mais ticks p/ cobrir custo
        "sl_ticks":         4,
        # ── Bloqueio abertura WIN (já existia via _get_time_weight, tornando explícito) ──
        "opening_block_minutes": 15,
    },
    # Bitcoin Futuro B3 — baixa liquidez, movimentos mais lentos e maiores
    "BITN26": {
        # ── v6 ASSERTIVIDADE ──────────────────────────────────────────────
        # Dados reais (scalper_trades.csv): score < 60 = ~0% de acerto;
        # faixa 60-79 e a unica com edge (~32-37%). Piso subiu 35 -> 60 para
        # so operar a faixa com historico positivo. threshold_pct e confirm_n
        # tambem subiram (menos trades, mais qualidade). cooldown e menor
        # frequencia diaria reduzem overtrading (era fonte de perdas em serie).
        "score_min":       65,     # v6/v6.1: era 35. Replay real: piso 65 = melhor P&L (-280 vs -430 em 60)
        "threshold_pct":   62.0,   # v6: era 58.0
        "confirm_n":        3,     # v6: era 2 (exige 3 confirmacoes consecutivas)
        "cooldown_sec":    90,     # v6: era 45 (menos overtrading)
        "max_daily":        6,     # v6: era 10 (limite diario menor)
        "tp_ticks":         4,     # BTC: 4 ticks = R$400 por contrato (4 × R$100)
        "sl_ticks":         2,
        "use_atr_sizing":  False,  # ATR do BITN26 pode ser instável com poucos dados
        # Time Exits BITN26 — movimentos mais lentos → janelas maiores
        "max_position_time_sec": 120,  # P3: 2 min (ticks chegam devagar)
        "time_stop_seconds":      30,  # P4: verificar em 30s
        "minimum_progress_r":    0.3,  # P4: 0.3R mínimo
    },
    # Ouro — liquidez média, movimentos suaves
    "XAUUSD": {
        "score_min":       40,
        "threshold_pct":   60.0,
        "confirm_n":        2,
        "cooldown_sec":    60,
        "max_daily":       12,
        "tp_ticks":         4,
        "sl_ticks":         2,
    },
    # EUR/USD — alta liquidez forex
    "EURUSD": {
        "score_min":       50,
        "threshold_pct":   63.0,
        "confirm_n":        3,
        "cooldown_sec":    45,
        "max_daily":       15,
        "tp_ticks":         5,
        "sl_ticks":         2,
    },
    # USD/BRL
    "USDBRL": {
        "score_min":       40,
        "threshold_pct":   60.0,
        "confirm_n":        2,
        "cooldown_sec":    60,
        "max_daily":       12,
        "tp_ticks":         4,
        "sl_ticks":         2,
    },
    # Ações B3 — liquidez moderada
    "PETR4": {
        "score_min":       45,
        "threshold_pct":   62.0,
        "confirm_n":        2,
        "cooldown_sec":    45,
        "max_daily":       12,
        "tp_ticks":         4,
        "sl_ticks":         2,
    },
    "VALE3": {
        "score_min":       45,
        "threshold_pct":   62.0,
        "confirm_n":        2,
        "cooldown_sec":    45,
        "max_daily":       12,
        "tp_ticks":         4,
        "sl_ticks":         2,
    },
    "ITUB4": {
        "score_min":       45,
        "threshold_pct":   62.0,
        "confirm_n":        2,
        "cooldown_sec":    45,
        "max_daily":       12,
        "tp_ticks":         4,
        "sl_ticks":         2,
    },
    "GOLD11": {
        "score_min":       40,
        "threshold_pct":   60.0,
        "confirm_n":        2,
        "cooldown_sec":    60,
        "max_daily":       12,
        "tp_ticks":         4,
        "sl_ticks":         2,
    },
}


def _sym_trade_cfg(symbol: str) -> dict:
    """Retorna config de trade mesclada: _default + override do símbolo."""
    from services.scalper_service import SCALPER_SYMBOLS
    mt5_sym  = SCALPER_SYMBOLS.get(symbol.upper().strip(), symbol.upper().strip())
    base     = dict(_SYMBOL_TRADE_CFG["_default"])
    override = _SYMBOL_TRADE_CFG.get(mt5_sym, {})
    base.update(override)
    return base


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
    body   = request.get_json(silent=True) or {}
    symbol = body.get("symbol", "WDON26")
    signal = body.get("signal", "NEUTRO")

    # Parâmetros com defaults por símbolo — frontend pode sobrescrever individualmente
    scfg           = _sym_trade_cfg(symbol)
    buy_pct        = float(body.get("buy_pct",        50))
    sell_pct       = float(body.get("sell_pct",       50))
    volume         = float(body.get("volume",         100))
    tp_ticks       = int(  body.get("tp_ticks",       scfg["tp_ticks"]))
    sl_ticks       = int(  body.get("sl_ticks",       scfg["sl_ticks"]))
    use_atr_sizing = bool( body.get("use_atr_sizing",  scfg["use_atr_sizing"]))
    threshold_pct  = float(body.get("threshold_pct",  scfg["threshold_pct"]))
    cooldown_sec   = int(  body.get("cooldown_sec",    scfg["cooldown_sec"]))
    confirm_n      = int(  body.get("confirm_n",       scfg["confirm_n"]))
    b3_open        = bool( body.get("b3_open",         True))
    max_daily      = int(  body.get("max_daily",       scfg["max_daily"]))

    # Estado do sinal para rejection logger
    _rej_score: list = [None]
    _rej_vwap:  list = [None]

    def _deny(reason, code=None, *, hard=False):
        """Rejeita sinal e registra auditoria."""
        agg_pct = buy_pct if signal == "COMPRA" else sell_pct
        if code:
            _log_rejection(
                symbol=symbol, signal=signal,
                rejection_code=code, rejection_reason=reason,
                score=_rej_score[0],
                buy_pct=buy_pct, sell_pct=sell_pct,
                vwap_regime=_rej_vwap[0],
                hard_blocked=hard,
            )
        return jsonify({"ok": True, "action": "NONE", "reason": reason,
                        "confirm": _auto["signal_count"]})

    if not _auto["enabled"]:
        return _deny("Auto-trade desabilitado.", CODE_AUTO_DISABLED)
    if not b3_open:
        return _deny("Fora do horario B3.", CODE_OUTSIDE_TRADING_HOURS)

    # ── Bloqueio de abertura do pregão (server-side, por símbolo) ─────────
    # opening_block_minutes > 0 → bloqueia as N primeiros minutos após 09:00.
    # Impede operar no ruído de abertura mesmo que o frontend envie b3_open=True.
    _ob_min = scfg.get("opening_block_minutes", 0)
    if _ob_min > 0:
        from datetime import datetime as _dt, timezone as _tz, timedelta as _td
        _brt  = _dt.now(_tz.utc).astimezone(_tz(_td(hours=-3)))
        _mins = _brt.hour * 60 + _brt.minute   # minutos desde 00:00 BRT
        _open = 9 * 60                          # 09:00 = 540 min
        if _open <= _mins < _open + _ob_min:
            _remaining = (_open + _ob_min) - _mins
            return _deny(
                f"🕘 Bloqueio de abertura: aguardando {_remaining}min para operar "
                f"{symbol} (libera às 09:{_ob_min:02d})",
                CODE_OUTSIDE_TRADING_HOURS,
            )

    # ── Pausa automática por stops consecutivos ───────────────────────────
    if _auto["paused_until"] > time.time():
        remaining = int(_auto["paused_until"] - time.time())
        mins, secs = remaining // 60, remaining % 60
        n = _pause_cfg["max_consecutive_losses"]
        return _deny(
            f"Pausa automatica apos {n} stops seguidos. Retorna em {mins}m{secs:02d}s",
            CODE_CONSECUTIVE_LOSSES_PAUSE,
        )

    # Limite diário de trades automáticos
    if _session["trades"] >= max_daily:
        return _deny(
            f"🛑 Limite diário de {max_daily} trades atingido. Reinicie a sessão para continuar.",
            CODE_MAX_DAILY_TRADES,
        )

    eff_cooldown = max(cooldown_sec, _ASSERT["min_cooldown_floor_sec"])
    elapsed = time.time() - _auto["last_trade_ts"]
    if elapsed < eff_cooldown:
        return _deny(
            f"Cooldown: {int(eff_cooldown - elapsed)}s restantes.",
            CODE_COOLDOWN_ACTIVE,
        )
    # Teto de execuções por minuto (anti-machine-gun)
    _now_ts = time.time()
    _recent_exec_ts[:] = [t for t in _recent_exec_ts if _now_ts - t < 60]
    if len(_recent_exec_ts) >= _ASSERT["max_trades_per_min"]:
        return _deny(
            f"🚫 Teto de {_ASSERT['max_trades_per_min']} trades/min — evitando overtrading.",
            CODE_COOLDOWN_ACTIVE,
        )

    if signal == "NEUTRO":
        _auto["signal_count"] = 0
        _auto["last_signal"]  = ""
        return _deny("Sinal NEUTRO.", CODE_SIGNAL_NEUTRAL)

    agg_pct = buy_pct if signal == "COMPRA" else sell_pct
    if agg_pct < threshold_pct:
        _auto["signal_count"] = 0
        _auto["last_signal"]  = ""
        return _deny(
            f"Agressao {agg_pct:.1f}% < threshold {threshold_pct:.0f}%.",
            CODE_LOW_AGGRESSION,
        )

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
        return _deny("Posicao ja aberta.", CODE_POSITION_ALREADY_OPEN)

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
        score_min_sv = int(body.get("score_min", scfg["score_min"]))

        # Propaga score para o rejection logger
        _rej_score[0] = sv_score

        # Hard-block 1: score server-side abaixo do mínimo configurado
        if sv_score < score_min_sv:
            _auto["signal_count"] = 0
            return _deny(
                f"🚫 Score srv {sv_score:.0f} < mín {score_min_sv} — entrada bloqueada",
                CODE_SCORE_BELOW_MINIMUM, hard=True,
            )

        # Hard-block 1b: ANTI-EXAUSTÃO — score alto demais = perseguindo o impulso.
        # Dados históricos: faixa alta teve ~0% de acerto. Bloqueia a zona de exaustão.
        _ceil = _ASSERT["score_ceiling"].get(symbol, _ASSERT["score_ceiling"]["_default"])
        if sv_score > _ceil:
            _auto["signal_count"] = 0
            return _deny(
                f"🚫 Score {sv_score:.0f} > teto {_ceil} — zona de exaustão (anti-perseguição)",
                CODE_SCORE_BELOW_MINIMUM, hard=True,
            )

        # Hard-block 1c: DISCIPLINA DE SESSÃO — só operar em sessão definida e boa.
        _sess = ((macro_sv.get("time", {}) or {}).get("session", "") or "").upper()
        if _ASSERT["block_undefined_session"] and _sess not in _ASSERT["good_sessions"]:
            _auto["signal_count"] = 0
            return _deny(
                f"🚫 Sessão '{_sess or '?'}' fora de PRIME/BOM — sem contexto p/ operar",
                CODE_OUTSIDE_TRADING_HOURS, hard=True,
            )

        # Hard-block Volatilidade 60s (P2) — mercado morto, sem range suficiente
        score_obj_v = macro_sv.get(score_key, {})
        if not score_obj_v.get("vol60_ok", True):
            r60 = score_obj_v.get("range_60s", 0)
            _auto["signal_count"] = 0
            return _deny(
                f"🚫 Volatilidade 60s insuficiente: {r60:.0f} ticks — mercado parado",
                CODE_LOW_VOLATILITY, hard=True,
            )

        # Hard-block 3: VWAP direction filter — só operar a favor da tendência
        # BEAR = preço abaixo da VWAP intraday → só VENDA permitida
        # BULL = preço acima da VWAP intraday → só COMPRA permitida
        # NEUTRO → permite ambas as direções
        vwap_ctx = macro_sv.get("vwap", {}).get("context", "NEUTRO")
        _rej_vwap[0] = vwap_ctx
        use_vwap_filter = bool(body.get("use_vwap_filter", scfg["use_vwap_filter"]))
        if use_vwap_filter and vwap_ctx != "NEUTRO":
            if vwap_ctx == "BEAR" and signal == "COMPRA":
                _auto["signal_count"] = 0
                return _deny(
                    f"🚫 VWAP BEAR — apenas VENDA (bloqueando COMPRA contra-tendência)",
                    CODE_VWAP_DIRECTION_BLOCK, hard=True,
                )
            if vwap_ctx == "BULL" and signal == "VENDA":
                _auto["signal_count"] = 0
                return _deny(
                    f"🚫 VWAP BULL — apenas COMPRA (bloqueando VENDA contra-tendência)",
                    CODE_VWAP_DIRECTION_BLOCK, hard=True,
                )

        # Hard-block 2: divergência delta severa (buy_pct extremo contra direção)
        if sv_blocked:
            _auto["signal_count"] = 0
            return _deny(
                f"Hard-block: Delta {sv_cum_bias} ({sv_cum_pct:.0f}% C) "
                f"fluxo acumulado contra {signal}",
                CODE_HARD_BLOCK_DELTA, hard=True,
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
                    f"Score {sv_score:.0f} < exigido {required} (min {score_min_sv}+{uplift})",
                    CODE_DIRECTIONAL_UPLIFT,
                )
            logger.info("Uplift direcional APROVADO: %s streak=%d score=%.0f >= %d",
                        signal, dir_streak, sv_score, required)

    except Exception as _sv_exc:
        logger.warning("Validação server-side ignorada: %s", _sv_exc)

    result, err = execute_scalper_trade(symbol, signal, volume, tp_ticks, sl_ticks, use_atr_sizing)
    if result and not err:
        _session["trades"] += 1
        _auto["last_trade_ts"]  = time.time()
        _recent_exec_ts.append(time.time())   # p/ teto de trades por minuto
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
# Config por símbolo — retorna defaults usados pelo auto-check para um símbolo
@scalper_bp.route("/api/scalper/symbol-config/<symbol>")
def api_scalper_symbol_config(symbol):
    try:
        from services.scalper_service import SYMBOL_CONFIG, _sym_cfg, SCALPER_SYMBOLS
        mt5_sym   = SCALPER_SYMBOLS.get(symbol.upper().strip(), symbol.upper().strip())
        scoring   = _sym_cfg(mt5_sym)
        trade     = _sym_trade_cfg(symbol)
        has_override = mt5_sym in SYMBOL_CONFIG and bool(SYMBOL_CONFIG[mt5_sym])
        return jsonify({
            "ok":           True,
            "symbol":       mt5_sym,
            "has_override": has_override,
            "scoring":      scoring,
            "trade":        trade,
        })
    except Exception as exc:
        logger.exception("api_scalper_symbol_config error")
        return jsonify({"ok": False, "error": str(exc)}), 500


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
                "paused_remaining_s":     max(0, int(_auto["paused_until"] - now_ts)),
    })


# Registro de fechamento via TP/SL
@scalper_bp.route("/api/scalper/register-close", methods=["POST"])
def api_scalper_register_close():
    body   = request.get_json(silent=True) or {}
    profit = float(body.get("profit", 0))
    reason = body.get("reason", "MT5")
    symbol = body.get("symbol", "")
    ticket = body.get("ticket")

    # Servidor autoritativo: havendo ticket, o P&L realizado do MT5 sempre vale
    # mais que o profit flutuante do cliente. Busca com retry antes de gravar —
    # evita a "zerada" (deal ainda não settado no instante do fechamento).
    if ticket and reason not in ("manual", "breakeven", "BE"):
        try:
            real, found = _fetch_realized_profit(int(ticket))
            if found and real is not None:
                profit = real
        except Exception as exc:
            logger.debug("register-close fetch real profit: %s", exc)

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
        # id da linha que será gravada (para eventual reconciliação)
        row_id = None
        try:
            from services.trade_logger import peek_pending_id
            row_id = peek_pending_id(symbol)
        except Exception:
            pass
        _safe_log_exit(symbol, 0.0, profit, reason)
        _safe_notify_exit(symbol, {}, profit, reason)
        # Rede de segurança: gravou zerado mas era TP/SL → reconcilia em background
        if ticket and abs(profit) < 0.01 and reason not in ("manual", "breakeven", "BE"):
            _enqueue_reconcile(ticket, symbol, row_id, reason)
    return jsonify({"ok": True})


# ── Captura do P&L realizado no MT5 (com retry) ───────────────────────────
# O deal de saida (DEAL_ENTRY_OUT) pode demorar 1-2s para aparecer no historico
# apos o TP/SL disparar. Sem retry, a consulta volta vazia e o trade e gravado
# com profit=0 -> vira BE ("zerada"). Aqui tentamos algumas vezes com pequeno
# atraso antes de desistir.
def _fetch_realized_profit(ticket: int, tries: int = 8, delay: float = 0.35):
    """Busca o P&L realizado de uma posicao fechada. Retorna (profit|None, found)."""
    from services.scalper_service import get_sim_mode, get_sim_closed_profit
    if get_sim_mode():
        profit = get_sim_closed_profit(ticket)
        return (round(float(profit), 2), True) if profit is not None else (None, False)
    try:
        import MetaTrader5 as mt5
        import time as _t
        from services.scalper_service import _mt5_init
        if not _mt5_init():
            return None, False
        for i in range(max(1, tries)):
            deals = mt5.history_deals_get(position=ticket)
            if deals:
                out = sum(d.profit for d in deals
                          if hasattr(d, "entry") and d.entry == mt5.DEAL_ENTRY_OUT)
                total = sum(d.profit for d in deals)
                profit = out if out != 0 else total
                # so aceita como "encontrado" quando ha deal de saida (evita 0 prematuro)
                has_out = any(getattr(d, "entry", None) == mt5.DEAL_ENTRY_OUT for d in deals)
                if has_out:
                    return round(profit, 2), True
            if i < tries - 1:
                _t.sleep(delay)
        return None, False
    except Exception as exc:
        logger.warning("_fetch_realized_profit error: %s", exc)
        return None, False


# Busca P&L real do historico MT5 pelo ticket (usado pelos frontends)
@scalper_bp.route("/api/scalper/last-deal")
def api_scalper_last_deal():
    ticket = request.args.get("ticket", type=int)
    if not ticket:
        return jsonify({"ok": False, "error": "ticket obrigatorio"})
    profit, found = _fetch_realized_profit(ticket)
    if found:
        return jsonify({"ok": True, "found": True, "profit": profit})
    return jsonify({"ok": True, "found": False, "profit": None})


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


# Trade Log: resumo estatistico para analise de assertividade
@scalper_bp.route("/api/scalper/trade-log")
def api_scalper_trade_log():
    try:
        from services.trade_logger import get_log_summary
        return jsonify({"ok": True, "data": get_log_summary()})
    except Exception as exc:
        logger.warning("trade-log error: %s", exc)
        return jsonify({"ok": False, "error": str(exc)})


# Rejection Audit (P1 Scalper V2) -- estatisticas de rejeicoes por simbolo
@scalper_bp.route("/api/scalper/rejections")
def api_scalper_rejections():
    symbol = request.args.get("symbol")          # opcional -- filtra por simbolo
    hours  = int(request.args.get("hours", 24))  # janela em horas (padrao 24h)
    return jsonify(_get_rejection_stats(symbol=symbol, hours=hours))


# Break-even: move SL para entry
@scalper_bp.route("/api/scalper/move-sl", methods=["POST"])
def api_scalper_move_sl():
    from services.scalper_service import move_sl_to_breakeven
    body   = request.get_json(silent=True) or {}
    symbol = body.get("symbol", "WDON26")
    result, err = move_sl_to_breakeven(symbol)
    return jsonify({"ok": err is None, "result": result, "error": err})


# Time Exit (P3+P4 Scalper V2) -- verifica posicao aberta e fecha se atingiu limite de tempo
@scalper_bp.route("/api/scalper/check-time-exit", methods=["POST"])
def api_scalper_check_time_exit():
    from services.scalper_service import get_scalper_position, close_scalper_position
    body    = request.get_json(silent=True) or {}
    symbol  = body.get("symbol", "WDON26")
    pnl_now = float(body.get("pnl_now", 0.0))

    scfg = _sym_trade_cfg(symbol)
    max_pos_time   = scfg.get("max_position_time_sec", 180)
    time_stop_sec  = scfg.get("time_stop_seconds", 30)
    min_progress_r = scfg.get("minimum_progress_r", 0.3)

    pos, _ = get_scalper_position(symbol)
    if not pos:
        return jsonify({"ok": True, "action": "NONE", "reason": "sem posicao"})

    open_time = pos.get("time", 0)
    if not open_time:
        return jsonify({"ok": True, "action": "NONE", "reason": "sem timestamp"})

    elapsed = time.time() - open_time

    try:
        from services.scalper_service import TICK_VALUE_BRL, _resolve_symbol
        mt5_sym  = _resolve_symbol(symbol)
        sl_ticks = scfg.get("sl_ticks", 2)
        tick_val = TICK_VALUE_BRL.get(mt5_sym, 1.0)
        min_pnl  = min_progress_r * sl_ticks * tick_val
    except Exception:
        min_pnl = 0.0

    # P3: TIME_EXIT -- posicao aberta muito tempo
    if elapsed >= max_pos_time:
        result, err = close_scalper_position(symbol)
        if not err:
            reason_msg = f"TIME_EXIT: posicao aberta {elapsed:.0f}s >= {max_pos_time}s"
            return jsonify({"ok": True, "action": "CLOSE", "reason": reason_msg, "elapsed": elapsed})
        else:
            return jsonify({"ok": False, "action": "NONE", "reason": f"TIME_EXIT falhou: {err}", "elapsed": elapsed})

    # P4: TIME_STOP -- posicao sem progresso minimo
    if elapsed >= time_stop_sec:
        pnl = pos.get("profit", 0.0)
        if pnl < min_pnl:
            result, err = close_scalper_position(symbol)
            if not err:
                reason_msg = f"TIME_STOP: {elapsed:.0f}s sem progresso minimo (pnl={pnl:.2f} < {min_pnl:.2f})"
                return jsonify({"ok": True, "action": "CLOSE", "reason": reason_msg, "elapsed": elapsed})
            else:
                return jsonify({"ok": False, "action": "NONE", "reason": f"TIME_STOP falhou: {err}", "elapsed": elapsed})

    return jsonify({"ok": True, "action": "NONE", "reason": "posicao dentro dos limites de tempo", "elapsed": elapsed})
