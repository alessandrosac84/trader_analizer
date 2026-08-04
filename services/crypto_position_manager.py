"""
services/crypto_position_manager.py — Modo MANAGER do Monitor Crypto.

⚠️ MÓDULO NOVO E INDEPENDENTE. Espelha a gestão do Monitor MT5 (breakeven no 1R/TP1,
trailing por ATR, saída por reversão de sinal, stop por tempo) mas em arquivo próprio.
NÃO importa nem altera o position_manager da B3.

manage() é SÓ CÁLCULO (não envia ordem). Quem aplica é o crypto_bp (move stop /
fecha), para manter a lógica testável e isolada.

Giveback MFE (todos os símbolos crypto/FX/ouro): escalonado pelo pico em R$ —
  <R$400 → 50% · R$400–999 → 33% · ≥R$1000 → teto absoluto R$250 do pico.
  Conta USD: limiares via usdbrl (CRYPTO_USDBRL → MT5 → AwesomeAPI → 5.20).

Armamento: pico ≥0,6R **OU** peak_brl ≥ R$200 (evita SL gigante impedir o guard
mesmo com lucro grande em R$). Avalia mesmo com PnL atual negativo/underwater.
"""
import logging

logger = logging.getLogger(__name__)

REVERSAL_SCORE = 8       # |score| do sinal oposto que dispara saída por reversão
TIME_STOP_CANDLES = 40   # candles aberto sem atingir TP1 → sugere revisar
BREAKEVEN_RR = 1.0       # a partir de 1R de lucro, move o stop para a entrada
TRAIL_ATR = 1.2          # trailing = preço ∓ TRAIL_ATR × ATR
# Proteção de lucro (evita "zerar" no breakeven devolvendo tudo)
GIVEBACK_PEAK_MIN_R = 0.6  # arma se lucro em preço ≥0,6R
GIVEBACK_ARM_BRL = 200.0   # OU se pico em R$ ≥ isto (SL largo não bloqueia)
# Faixas de giveback pelo pico MFE em R$ (allowed_giveback)
GIVEBACK_BRL_TIER1 = 400.0    # < 400 → 50% do pico
GIVEBACK_BRL_TIER2 = 1000.0   # 400–999 → 33%; ≥1000 → teto R$250
GIVEBACK_BRL_CAP = 250.0
GIVEBACK_PCT_LOW = 0.50
GIVEBACK_PCT_MID = 0.33
MICRO_EXIT_MIN_R    = 0.5  # revERSão pelo MICRO só fecha com lucro ≥0,5R já garantido


def allowed_giveback(peak_pnl_ccy: float, usdbrl: float = 5.2) -> float:
    """Devolução máxima permitida na moeda da conta (mesma de peak_pnl_ccy).

    Fecha quando ``peak - current_pnl >= allowed_giveback(...)``.
    Faixa definida pelo **pico** (não pelo PnL atual), convertida a R$:
      peak_brl = peak_pnl_ccy * usdbrl
      < 400      → 50% do pico
      400 – 999  → 33% do pico
      ≥ 1000     → teto absoluto R$250 (= 250/usdbrl na conta USD)

    Conta já em BRL: passe ``usdbrl=1.0``.
    """
    rate = float(usdbrl) if usdbrl and float(usdbrl) > 0 else 5.2
    peak = float(peak_pnl_ccy or 0.0)
    if peak <= 0:
        return 0.0
    peak_brl = peak * rate
    if peak_brl < GIVEBACK_BRL_TIER1:
        return GIVEBACK_PCT_LOW * peak
    if peak_brl < GIVEBACK_BRL_TIER2:
        return GIVEBACK_PCT_MID * peak
    return GIVEBACK_BRL_CAP / rate


def giveback_band_info(peak_pnl_ccy: float, usdbrl: float = 5.2) -> dict:
    """Metadados da faixa ativa (p/ log / reason)."""
    rate = float(usdbrl) if usdbrl and float(usdbrl) > 0 else 5.2
    peak = float(peak_pnl_ccy or 0.0)
    peak_brl = peak * rate
    allowed = allowed_giveback(peak, rate)
    if peak_brl < GIVEBACK_BRL_TIER1:
        band, rule = "<R$400", f"{int(GIVEBACK_PCT_LOW * 100)}% do pico"
    elif peak_brl < GIVEBACK_BRL_TIER2:
        band, rule = "R$400-999", f"{int(GIVEBACK_PCT_MID * 100)}% do pico"
    else:
        band, rule = ">=R$1000", f"teto R${GIVEBACK_BRL_CAP:.0f}"
    return {
        "band": band,
        "rule": rule,
        "allowed_ccy": allowed,
        "allowed_brl": allowed * rate,
        "peak_brl": peak_brl,
        "usdbrl": rate,
    }


def manage(pos: dict, signal: dict, atr: float = 0.0, candles_open: int = 0,
           micro_dir: str = "NEUTRO", peak_favor: float = 0.0,
           peak_pnl: float = 0.0, usdbrl: float = 5.2) -> dict:
    """
    pos    : {type:'COMPRA'|'VENDA', price_open, price_current, sl, tp, profit}
    signal : sinal atual (crypto_analysis.analyze) — usado p/ detectar reversão
    peak_favor : pico de lucro em unidades de preço (p/ armar 0,6R)
    peak_pnl   : pico de lucro na moeda da conta (USD crypto / BRL se aplicável)
    usdbrl     : cotação p/ converter limiares R$ ↔ conta USD
    Retorna: {mode:'MANAGE', recommendation, reason, new_sl, close, close_code}
    """
    try:
        is_buy = pos.get("type") == "COMPRA"
        entry  = float(pos.get("price_open") or 0)
        cur    = float(pos.get("price_current") or entry)
        sl     = float(pos.get("sl") or 0)
        tp     = float(pos.get("tp") or 0)
        risk   = abs(entry - sl) if sl else (atr * 1.5 if atr else 0)
        cur_pnl = float(pos.get("profit") or 0)
        rate = float(usdbrl) if usdbrl and float(usdbrl) > 0 else 5.2
        peak_money = max(float(peak_pnl or 0.0), cur_pnl)

        rec, reason, new_sl, close = "MANTER", "Trade dentro dos parâmetros — aguardar alvos.", None, False
        close_code = None

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
            close_code = "REVERSAO"

        # 4) Stop por tempo (sem atingir TP1)
        if candles_open >= TIME_STOP_CANDLES and rec == "MANTER" and not hit_tp:
            rec, reason = "REVISAR", f"⏰ Aberto há {candles_open} candles sem TP1 — considerar fechar."

        # 5) GUARD DE DEVOLUÇÃO (MFE)
        # Arma por 0,6R OU por pico em R$ (SL gigante não pode silenciar o guard).
        # Avalia mesmo underwater (cur_pnl/favor ≤ 0) — senão devolve tudo até o stop.
        info = giveback_band_info(peak_money, rate) if peak_money > 0 else {
            "band": "—", "rule": "—", "allowed_ccy": 0.0, "allowed_brl": 0.0,
            "peak_brl": 0.0, "usdbrl": rate,
        }
        armed_r = risk > 0 and peak_favor >= GIVEBACK_PEAK_MIN_R * risk
        armed_brl = info["peak_brl"] >= GIVEBACK_ARM_BRL
        armed = bool(peak_money > 0 and (armed_r or armed_brl))
        allowed = float(info["allowed_ccy"] or 0.0)
        given_back = peak_money - cur_pnl if peak_money > 0 else 0.0
        if (not close and armed and allowed > 0 and given_back >= allowed - 1e-9):
            peak_brl = info["peak_brl"]
            cur_brl = cur_pnl * rate
            devolveu_pct = int((given_back / max(peak_money, 1e-9)) * 100)
            reason = (
                f"🔒 GIVEBACK faixa {info['band']} ({info['rule']}) — "
                f"pico R${peak_brl:.0f} → saída R${cur_brl:.0f} "
                f"(devolveu {devolveu_pct}% / allowed R${info['allowed_brl']:.0f})."
            )
            rec, close = "FECHAR", True
            close_code = "GIVEBACK"
            logger.info(
                "GIVEBACK %s: faixa=%s rule=%s peak_brl=%.0f pnl_saida_brl=%.0f "
                "peak_ccy=%.2f pnl_ccy=%.2f allowed_ccy=%.2f usdbrl=%.4f armed_r=%s armed_brl=%s",
                pos.get("symbol") or "?", info["band"], info["rule"],
                peak_brl, cur_brl, peak_money, cur_pnl, allowed, rate,
                armed_r, armed_brl,
            )
        else:
            logger.info(
                "GIVEBACK_CYCLE %s: peak_brl=%.0f faixa=%s allowed_ccy=%.2f "
                "cur_ccy=%.2f given_back=%.2f armed=%s (r=%s brl=%s) peak_favor=%.2f risk=%.2f",
                pos.get("symbol") or "?", info["peak_brl"], info["band"],
                allowed, cur_pnl, given_back, armed, armed_r, armed_brl,
                float(peak_favor or 0.0), risk,
            )

        # 6) REVERSÃO PELO MICRO
        micro_against = (is_buy and micro_dir == "BAIXA") or (not is_buy and micro_dir == "ALTA")
        if (not close and micro_against and risk > 0 and favor >= MICRO_EXIT_MIN_R * risk):
            rec, reason, close = "FECHAR", f"⚡ MICRO virou contra ({micro_dir}) com lucro — fecha antes de devolver.", True
            close_code = "MICRO"

        return {"mode": "MANAGE", "recommendation": rec, "reason": reason,
                "new_sl": new_sl, "close": close, "close_code": close_code,
                "giveback": {
                    "peak_brl": info["peak_brl"], "band": info["band"],
                    "allowed_ccy": allowed, "allowed_brl": info["allowed_brl"],
                    "cur_ccy": cur_pnl, "given_back": given_back,
                    "armed": armed, "armed_r": armed_r, "armed_brl": armed_brl,
                    "peak_pnl": peak_money, "peak_favor": float(peak_favor or 0.0),
                    "usdbrl": rate,
                },
                "position": {"type": pos.get("type"), "entry": entry, "current": cur,
                             "sl": sl, "tp": tp, "profit": pos.get("profit")}}
    except Exception as exc:
        logger.warning("crypto manage erro: %s", exc)
        return {"mode": "MANAGE", "recommendation": "MANTER", "reason": "—",
                "new_sl": None, "close": False, "close_code": None}
