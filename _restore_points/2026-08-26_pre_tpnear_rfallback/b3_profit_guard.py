"""
services/b3_profit_guard.py — Proteção de LUCRO para WIN/WDO (Monitor MT5 e V7).

Porta os mecanismos do crypto (giveback MFE + proteção de quase-alvo) para o B3,
em termos de R (múltiplos do risco) — uniforme p/ WIN e WDO.

SEGURANÇA (invariante): este módulo SÓ protege lucro. Ele NUNCA alarga o stop e
NUNCA fecha um trade no prejuízo. No pior caso, encerra um trade vencedor um
pouco antes. É cálculo puro (não envia ordem) — quem aplica é o chamador.

Regras:
  1. QUASE-ALVO: se o pico chegou a ≥80% do caminho até o TP, trava o stop em
     +50% do pico e fecha se devolver ≥1/3 do que andou.
  2. GIVEBACK: se o pico ≥1R, fecha se devolver ≥40% do pico.
"""
from __future__ import annotations

ENABLED = True
NEAR_TP_ARM_FRAC = 0.80      # pico atingiu ≥80% do caminho até o TP → arma
NEAR_TP_GIVEBACK = 0.34      # devolveu ≥1/3 do pico → fecha
NEAR_TP_LOCK = 0.50          # trava stop em +50% do pico enquanto não devolve
GIVEBACK_ARM_R = 1.0         # pico ≥1R → arma giveback
GIVEBACK_FRAC = 0.40         # devolveu ≥40% do pico → fecha


def check(entry: float, price: float, sl: float, tp: float,
          peak_favor: float, risk: float, buy: bool) -> dict:
    """Retorna {close, code, reason, new_sl}. new_sl só é sugerido se for MAIS
    apertado (trava lucro) — nunca alarga."""
    out = {"close": False, "code": None, "reason": "", "new_sl": None}
    if not ENABLED or risk <= 0 or entry <= 0 or price <= 0:
        return out
    favor = (price - entry) if buy else (entry - price)
    pk = max(float(peak_favor or 0.0), favor)
    if pk <= 0:                       # nunca esteve no lucro → nada a proteger
        return out
    r_now = favor / risk
    given_back = pk - favor           # quanto devolveu do pico (em pontos)

    # 1) QUASE-ALVO — arma pela % do caminho até o TP
    if tp:
        tp_dist = abs(entry - float(tp))
        if tp_dist > 0:
            prog = pk / tp_dist
            if prog >= NEAR_TP_ARM_FRAC:
                if given_back >= NEAR_TP_GIVEBACK * pk:
                    out.update(close=True, code="TP_NEAR",
                               reason=f"🎯 QUASE-ALVO: pico a {prog:.0%} do TP e devolveu "
                                      f"{given_back / pk:.0%} — trava o lucro.")
                    return out
                lock = NEAR_TP_LOCK * pk
                lock_sl = (entry + lock) if buy else (entry - lock)
                better = (buy and (not sl or lock_sl > sl)) or ((not buy) and (not sl or lock_sl < sl))
                if better:
                    out.update(new_sl=round(lock_sl, 1),
                               reason=f"🎯 QUASE-ALVO: pico a {prog:.0%} do TP — "
                                      f"stop travado em +{NEAR_TP_LOCK:.0%} do pico.")

    # 2) GIVEBACK (MFE) — pico ≥1R
    if not out["close"] and (pk / risk) >= GIVEBACK_ARM_R:
        if given_back >= GIVEBACK_FRAC * pk:
            out.update(close=True, code="GIVEBACK",
                       reason=f"🔒 GIVEBACK: pico {pk / risk:.1f}R, devolveu "
                              f"{given_back / pk:.0%} — protege o lucro.")
    return out
