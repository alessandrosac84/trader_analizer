"""
services/crypto_position_manager.py — Modo MANAGER do Monitor Crypto.

⚠️ MÓDULO NOVO E INDEPENDENTE. Espelha a gestão do Monitor MT5 (breakeven no 1R/TP1,
trailing por ATR, saída por reversão de sinal, stop por tempo) mas em arquivo próprio.
NÃO importa nem altera o position_manager da B3.

manage() é SÓ CÁLCULO (não envia ordem). Quem aplica é o crypto_bp (move stop /
fecha), para manter a lógica testável e isolada.
"""
import logging

logger = logging.getLogger(__name__)

REVERSAL_SCORE = 8       # |score| do sinal oposto que dispara saída por reversão
TIME_STOP_CANDLES = 40   # candles aberto sem atingir TP1 → sugere revisar
BREAKEVEN_RR = 1.0       # a partir de 1R de lucro, move o stop para a entrada
TRAIL_ATR = 1.2          # trailing = preço ∓ TRAIL_ATR × ATR


def manage(pos: dict, signal: dict, atr: float = 0.0, candles_open: int = 0) -> dict:
    """
    pos    : {type:'COMPRA'|'VENDA', price_open, price_current, sl, tp, profit}
    signal : sinal atual (crypto_analysis.analyze) — usado p/ detectar reversão
    Retorna: {mode:'MANAGE', recommendation, reason, new_sl(None se nada), close(bool)}
    """
    try:
        is_buy = pos.get("type") == "COMPRA"
        entry  = float(pos.get("price_open") or 0)
        cur    = float(pos.get("price_current") or entry)
        sl     = float(pos.get("sl") or 0)
        tp     = float(pos.get("tp") or 0)
        risk   = abs(entry - sl) if sl else (atr * 1.5 if atr else 0)

        rec, reason, new_sl, close = "MANTER", "Trade dentro dos parâmetros — aguardar alvos.", None, False

        # progresso a favor (em price units)
        favor = (cur - entry) if is_buy else (entry - cur)

        # 1) TP1 atingido → breakeven (move stop para a entrada)
        hit_tp = tp and ((is_buy and cur >= tp) or (not is_buy and cur <= tp))
        at_1r  = risk > 0 and favor >= BREAKEVEN_RR * risk
        be_target = entry
        already_be = (is_buy and sl >= entry - 1e-9) or (not is_buy and sl <= entry + 1e-9)
        if (hit_tp or at_1r) and not already_be:
            rec, reason, new_sl = "BREAKEVEN", "✅ Lucro ≥ 1R / TP1 — stop movido para a entrada (risco zero).", be_target

        # 2) Além do TP1 → trailing por ATR (aperta o stop atrás do preço)
        if hit_tp and atr and atr > 0:
            trail = (cur - TRAIL_ATR * atr) if is_buy else (cur + TRAIL_ATR * atr)
            better = (is_buy and trail > (new_sl or sl)) or (not is_buy and trail < (new_sl or sl or 1e18))
            if better:
                rec, reason, new_sl = "TRAILING", f"🚀 Além do TP1 — trailing stop por {TRAIL_ATR}×ATR.", round(trail, 5)

        # 3) Reversão de sinal forte → fechar para proteger
        sig_acao = signal.get("acao") if signal else "NEUTRO"
        sig_score = abs(int(signal.get("score", 0))) if signal else 0
        reversed_ = (is_buy and sig_acao == "VENDA") or (not is_buy and sig_acao == "COMPRA")
        if reversed_ and sig_score >= REVERSAL_SCORE and rec == "MANTER":
            rec, reason, close = "FECHAR", f"⚠️ Sinal reverteu para {sig_acao} (score {sig_score}) — fechar para proteger.", True

        # 4) Stop por tempo (sem atingir TP1)
        if candles_open >= TIME_STOP_CANDLES and rec == "MANTER" and not hit_tp:
            rec, reason = "REVISAR", f"⏰ Aberto há {candles_open} candles sem TP1 — considerar fechar."

        return {"mode": "MANAGE", "recommendation": rec, "reason": reason,
                "new_sl": new_sl, "close": close,
                "position": {"type": pos.get("type"), "entry": entry, "current": cur,
                             "sl": sl, "tp": tp, "profit": pos.get("profit")}}
    except Exception as exc:
        logger.warning("crypto manage erro: %s", exc)
        return {"mode": "MANAGE", "recommendation": "MANTER", "reason": "—",
                "new_sl": None, "close": False}
