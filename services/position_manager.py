"""
position_manager.py — Gestão de trades ativos (modo MANAGE).

Quando há uma posição aberta pelo bot, analisa a cada ciclo de 30s:
  1. P&L atual em pontos e R$
  2. Distância até stop / TP1
  3. Breakeven: se preço atingiu TP1 → sugere mover stop para entrada (risco zero)
  4. Trailing stop: se preço passou do TP1 → trail com 1×ATR de folga
  5. Saída antecipada: sinal técnico reverteu fortemente (score oposto >= threshold)
  6. Stop por tempo: N candles sem atingir TP1 → alerta para revisar
"""
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Candles sem TP1 antes de alertar (16 × 15min = 4h — tempo razoável para WIN)
TIME_STOP_CANDLES = 16

# Score de reversão mínimo para SUGERIR saída (nunca executa automaticamente)
# Aumentado para evitar falsos alertas por ruído de mercado
REVERSAL_SCORE_THRESHOLD = 8


def _pts(val):
    """Arredonda para inteiro (pontos do WIN)."""
    try:
        return int(round(float(val)))
    except Exception:
        return None


def analyze_position(
    position: dict,
    current_signal: "dict | None",
    atr: "float | None",
    interval_min: int = 15,
) -> dict:
    """
    Analisa uma posição aberta e retorna recomendação de gestão.

    Parameters
    ----------
    position       : dict MT5 (type, price_open, price_current, sl, tp, profit, time, volume)
    current_signal : output de generate_signal() — sinal técnico atual
    atr            : ATR atual (em pontos)
    interval_min   : timeframe em minutos (para cálculo de candles abertos)

    Returns
    -------
    dict com: recommendation, reason, alert_level, pnl_points, pnl_brl,
              dist_stop_pts, dist_tp1_pts, tp1_reached, breakeven_suggested,
              new_sl_suggestion, candles_open, e mais campos informativos.
    """
    is_buy  = position.get("type") == 0          # 0 = BUY, 1 = SELL
    entry   = float(position.get("price_open")   or 0)
    current = float(position.get("price_current") or 0)
    profit  = float(position.get("profit")       or 0)
    open_ts = position.get("time")               # unix timestamp de abertura

    sl_raw = position.get("sl")
    tp_raw = position.get("tp")
    sl = float(sl_raw) if sl_raw else None
    tp = float(tp_raw) if tp_raw else None

    # ── P&L em pontos ──────────────────────────────────────────────────────
    pnl_pts = _pts(current - entry) if is_buy else _pts(entry - current)

    # ── Distância até stop/TP (positivo = ainda não atingiu) ──────────────
    dist_stop = None
    if sl is not None:
        dist_stop = _pts(current - sl) if is_buy else _pts(sl - current)

    dist_tp1 = None
    if tp is not None:
        dist_tp1 = _pts(tp - current) if is_buy else _pts(current - tp)

    tp1_reached = dist_tp1 is not None and dist_tp1 <= 0

    # ── Candles abertos ────────────────────────────────────────────────────
    candles_open = None
    if open_ts:
        try:
            open_dt  = datetime.fromtimestamp(int(open_ts), tz=timezone.utc)
            elapsed_min = (datetime.now(timezone.utc) - open_dt).total_seconds() / 60
            candles_open = max(0, int(elapsed_min / interval_min))
        except Exception:
            pass

    # ── Variáveis de saída ─────────────────────────────────────────────────
    recommendation      = "MANTER"
    reason              = "Trade dentro dos parâmetros — aguardar alvos."
    alert_level         = "ok"
    breakeven_suggested = False
    new_sl_suggestion   = None

    # ── 1. TP1 atingido → breakeven ────────────────────────────────────────
    if tp1_reached:
        breakeven_suggested = True
        new_sl_suggestion   = _pts(entry)
        recommendation      = "BREAKEVEN"
        reason              = "✅ TP1 atingido! Mova o stop para a entrada — trade sem risco."
        alert_level         = "success"

    # ── 2. Preço além do TP1 → trailing stop (1×ATR de folga) ─────────────
    elif tp is not None and atr and not tp1_reached:
        beyond = (current - tp) if is_buy else (tp - current)
        if beyond > 0 and atr > 0:
            trail_sl = _pts((current - atr) if is_buy else (current + atr))
            new_sl_suggestion   = trail_sl
            breakeven_suggested = True
            recommendation      = "TRAILING"
            reason              = (
                f"🚀 Preço além do TP1! Trailing stop sugerido em {trail_sl:,} pts"
                f" ({_pts(atr)} pts de folga)."
            )
            alert_level = "success"

    # ── 3. Saída automática por reversão de sinal ─────────────────────────
    # Se o sinal técnico inverteu com score forte, fecha o trade automaticamente
    # para proteger capital (válido quando operador não está monitorando)
    if current_signal and recommendation == "MANTER":
        sig_acao  = current_signal.get("acao", "NEUTRO")
        sig_score = int(current_signal.get("score") or 0)
        reversed_ = (is_buy and sig_acao == "VENDA") or (not is_buy and sig_acao == "COMPRA")
        if reversed_ and abs(sig_score) >= REVERSAL_SCORE_THRESHOLD:
            recommendation = "FECHAR"
            reason = (
                f"⚠️ Sinal reverteu para {sig_acao} (score {sig_score:+d}) — "
                "fechando trade automaticamente para proteger capital."
            )
            alert_level = "danger"

    # ── 4. Stop por tempo ─────────────────────────────────────────────────
    if (
        candles_open is not None
        and candles_open >= TIME_STOP_CANDLES
        and recommendation == "MANTER"
    ):
        recommendation = "REVISAR"
        reason = (
            f"⏰ Trade aberto há {candles_open} candles ({candles_open * interval_min} min) "
            "sem atingir TP1. Considere fechar e aguardar nova oportunidade."
        )
        alert_level = "warning"

    # ── 5. Stop atingido ou iminente ───────────────────────────────────────
    if dist_stop is not None and dist_stop <= 0 and recommendation not in ("BREAKEVEN", "TRAILING"):
        recommendation = "STOP"
        reason         = "🛑 Stop atingido ou preço abaixo do stop!"
        alert_level    = "danger"

    return {
        "mode":               "MANAGE",
        "position_side":      "COMPRA" if is_buy else "VENDA",
        "entry_price":        _pts(entry),
        "current_price":      _pts(current),
        "pnl_points":         pnl_pts,
        "pnl_brl":            round(profit, 2),
        "sl_current":         _pts(sl) if sl else None,
        "tp1_current":        _pts(tp) if tp else None,
        "dist_stop_pts":      dist_stop,
        "dist_tp1_pts":       dist_tp1,
        "tp1_reached":        tp1_reached,
        "breakeven_suggested": breakeven_suggested,
        "new_sl_suggestion":  new_sl_suggestion,
        "candles_open":       candles_open,
        "recommendation":     recommendation,
        "reason":             reason,
        "alert_level":        alert_level,
        "signal_acao":        current_signal.get("acao")  if current_signal else None,
        "signal_score":       current_signal.get("score") if current_signal else None,
    }
