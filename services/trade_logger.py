"""
services/trade_logger.py
Log de operações do Scalper para análise de assertividade.

Grava CSV em: logs/scalper_trades.csv
Cada linha = 1 operação completa (entry + exit)
"""
import csv
import os
import logging
from datetime import datetime, timezone, timedelta
from threading import Lock

logger  = logging.getLogger(__name__)
_lock   = Lock()

LOG_DIR  = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
LOG_FILE = os.path.join(LOG_DIR, "scalper_trades.csv")

COLUMNS = [
    # Identificação
    "id", "datetime_brt", "symbol", "mode",
    # Entrada
    "direcao", "entry_price", "tp_price", "sl_price", "volume",
    "auto",
    # Score e contexto no momento da entrada
    "score", "score_delta", "score_acel", "score_vwap",
    "score_book", "score_vel", "score_tape", "score_horario", "score_absorcao",
    # Macro
    "vwap_price", "vwap_context", "atr_1m",
    "cum_delta", "cum_delta_bias",
    "session", "flow_signal",
    "book_imbalance_pct",
    # Fluxo
    "aggr_5s_dir", "aggr_30s_dir", "velocity_3s",
    # Saída
    "exit_price", "profit", "exit_reason", "duration_s",
    # Resultado
    "resultado",   # WIN / LOSS / BE
    "score_reasons",
]


def _ensure_file():
    """Cria o diretório e arquivo CSV com cabeçalho se não existir."""
    os.makedirs(LOG_DIR, exist_ok=True)
    if not os.path.exists(LOG_FILE):
        with open(LOG_FILE, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=COLUMNS)
            writer.writeheader()


def _next_id() -> int:
    """Retorna o próximo ID sequencial."""
    if not os.path.exists(LOG_FILE):
        return 1
    with open(LOG_FILE, "r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return len(rows) + 1


# ── Estado em memória (entry pendente até o close) ──────────────────────────
_pending: dict[str, dict] = {}   # symbol -> dados da entrada atual


def log_entry(
    symbol: str, direcao: str, entry_price: float,
    tp_price: float, sl_price: float, volume: float,
    mode: str, auto: bool,
    macro: dict = None,
    context: dict = None,
) -> None:
    """
    Registra abertura de posição. Os dados serão completados no log_exit.
    macro = campo 'macro' retornado por get_scalper_data
    context = campo 'context' retornado por get_scalper_data
    """
    try:
        _ensure_file()
        macro   = macro   or {}
        context = context or {}

        brt_now  = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=-3)))
        vwap_d   = macro.get("vwap",      {}) or {}
        cum_d    = macro.get("cum_delta",  {}) or {}
        time_d   = macro.get("time",       {}) or {}
        flow_d   = macro.get("flow",       {}) or {}
        book_d   = macro.get("book_imb",   {}) or {}
        vel_d    = (context.get("velocity") or {})
        a5_d     = (context.get("aggr_5s")  or {})
        a30_d    = macro.get("score_buy" if direcao == "COMPRA" else "score_sell", {}) or {}
        score_d  = (macro.get("score_buy") if direcao == "COMPRA" else macro.get("score_sell")) or {}
        breakdown = score_d.get("breakdown", {}) or {}

        aggr_key  = "buy_pct" if direcao == "COMPRA" else "sell_pct"
        a30_from_context = (context.get("aggr_5s") or {})  # fallback

        _pending[symbol] = {
            "id":                _next_id(),
            "datetime_brt":      brt_now.strftime("%Y-%m-%d %H:%M:%S"),
            "symbol":            symbol,
            "mode":              mode,
            "direcao":           direcao,
            "entry_price":       round(entry_price, 4),
            "tp_price":          round(tp_price, 4),
            "sl_price":          round(sl_price, 4),
            "volume":            volume,
            "auto":              1 if auto else 0,
            # Score
            "score":             round(score_d.get("score", 0), 1),
            "score_delta":       round(breakdown.get("delta_30s", 0), 1),
            "score_acel":        round(breakdown.get("aceleracao", 0), 1),
            "score_vwap":        round(breakdown.get("vwap", 0), 1),
            "score_book":        round(breakdown.get("book", 0), 1),
            "score_vel":         round(breakdown.get("velocidade", 0), 1),
            "score_tape":        round(breakdown.get("tape_trend", 0), 1),
            "score_horario":     round(breakdown.get("horario", 0), 1),
            "score_absorcao":    round(breakdown.get("absorcao", 0), 1),
            # Macro
            "vwap_price":        vwap_d.get("vwap", ""),
            "vwap_context":      vwap_d.get("context", "NEUTRO"),
            "atr_1m":            vwap_d.get("atr_1m", ""),
            "cum_delta":         cum_d.get("delta", 0),
            "cum_delta_bias":    cum_d.get("bias", "NEUTRO"),
            "session":           time_d.get("session", ""),
            "flow_signal":       flow_d.get("signal", "NEUTRO"),
            "book_imbalance_pct": book_d.get("imbalance_pct", 0),
            # Fluxo
            "aggr_5s_dir":       round((a5_d.get(aggr_key) or 50), 1),
            "aggr_30s_dir":      "",   # preenchido no contexto do auto-check
            "velocity_3s":       vel_d.get("trades_3s", 0),
            # Saída (preenchida no log_exit)
            "exit_price": "", "profit": "", "exit_reason": "",
            "duration_s": "", "resultado": "",
            "score_reasons": "; ".join(score_d.get("reasons", [])),
            "_entry_ts": brt_now.timestamp(),
        }
        logger.info("TradeLog ENTRY: %s %s @ %.4f  score=%.0f  vwap=%s",
                    direcao, symbol, entry_price,
                    score_d.get("score", 0), vwap_d.get("context", "?"))
    except Exception as exc:
        logger.warning("trade_logger.log_entry error: %s", exc)


def log_exit(
    symbol: str, exit_price: float, profit: float,
    exit_reason: str = "manual",
) -> None:
    """
    Completa o registro com dados da saída e grava no CSV.
    exit_reason: 'TP' | 'SL' | 'manual' | 'time_exit' | 'breakeven'
    """
    try:
        _ensure_file()
        pending = _pending.pop(symbol, None)
        if not pending:
            logger.warning("trade_logger.log_exit: nenhuma entrada pendente para %s", symbol)
            return

        entry_ts   = pending.pop("_entry_ts", None)
        duration_s = round((datetime.now(timezone.utc).timestamp() - entry_ts), 1) if entry_ts else ""

        if   profit > 0.01:  resultado = "WIN"
        elif profit < -0.01: resultado = "LOSS"
        else:                resultado = "BE"

        pending.update({
            "exit_price":  round(exit_price, 4),
            "profit":      round(profit, 2),
            "exit_reason": exit_reason,
            "duration_s":  duration_s,
            "resultado":   resultado,
        })

        with _lock:
            with open(LOG_FILE, "a", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
                writer.writerow(pending)

        logger.info("TradeLog EXIT: %s %s @ %.4f  profit=%.2f  reason=%s  resultado=%s",
                    pending["direcao"], symbol, exit_price, profit, exit_reason, resultado)
    except Exception as exc:
        logger.warning("trade_logger.log_exit error: %s", exc)


def get_log_summary() -> dict:
    """Retorna resumo estatístico do log para a API."""
    try:
        _ensure_file()
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            rows = [r for r in csv.DictReader(f) if r.get("resultado")]

        if not rows:
            return {"trades": 0}

        wins   = [r for r in rows if r["resultado"] == "WIN"]
        losses = [r for r in rows if r["resultado"] == "LOSS"]
        bes    = [r for r in rows if r["resultado"] == "BE"]
        total  = len(rows)

        def safe_float(v):
            try: return float(v)
            except: return 0.0

        profits    = [safe_float(r["profit"]) for r in rows]
        total_pnl  = round(sum(profits), 2)
        avg_score  = round(sum(safe_float(r["score"]) for r in rows) / total, 1) if total else 0

        # Win rate por contexto VWAP
        by_vwap = {}
        for r in rows:
            ctx = r.get("vwap_context", "NEUTRO")
            if ctx not in by_vwap:
                by_vwap[ctx] = {"wins": 0, "total": 0}
            by_vwap[ctx]["total"] += 1
            if r["resultado"] == "WIN":
                by_vwap[ctx]["wins"] += 1

        # Win rate por sessão
        by_session = {}
        for r in rows:
            s = r.get("session", "?")
            if s not in by_session:
                by_session[s] = {"wins": 0, "total": 0}
            by_session[s]["total"] += 1
            if r["resultado"] == "WIN":
                by_session[s]["wins"] += 1

        # Win rate por faixa de score
        score_ranges = {"<50": {"wins":0,"total":0}, "50-69": {"wins":0,"total":0}, ">=70": {"wins":0,"total":0}}
        for r in rows:
            sc = safe_float(r.get("score", 0))
            key = "<50" if sc < 50 else ("50-69" if sc < 70 else ">=70")
            score_ranges[key]["total"] += 1
            if r["resultado"] == "WIN":
                score_ranges[key]["wins"] += 1

        return {
            "trades":    total,
            "wins":      len(wins),
            "losses":    len(losses),
            "bes":       len(bes),
            "win_rate":  round(len(wins) / total * 100, 1) if total else 0,
            "total_pnl": total_pnl,
            "avg_score": avg_score,
            "by_vwap":   by_vwap,
            "by_session": by_session,
            "by_score":  score_ranges,
            "last_trades": [
                {k: r.get(k) for k in ["datetime_brt","direcao","resultado","profit","score","vwap_context","session","exit_reason"]}
                for r in rows
            ][::-1],
        }
    except Exception as exc:
        logger.warning("trade_logger.get_log_summary error: %s", exc)
        return {"error": str(exc)}
