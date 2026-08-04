"""
trading_costs.py — Modelo de custos reais por trade (v7, Fase 1).

Fonte: nota de corretagem Santander de 01/06/2026 (WIN M26, day trade):
  corretagem R$0,00 + registro BM&F R$0,32 + emolumentos/f.gar R$0,18
  = R$0,50 por contrato por round-trip.
WDO: estimado pela tabela B3 de day trade (~R$1,15/contrato r/t) —
  CONFIRMAR com uma nota de WDO e ajustar FEES_BRL_RT abaixo.

Além das taxas, o custo dominante é CRUZAR O SPREAD:
  - entrada a MERCADO: paga ~1 tick;
  - saída no STOP: sempre a mercado, paga ~1 tick;
  - saída no TP (limite): não cruza spread.
  - entrada LIMITE (proposta v7): não cruza spread na entrada.

Uso:
  from services.trading_costs import roundtrip_cost_pts, cost_in_R
  custo = roundtrip_cost_pts("WIN", entry_limit=False, stopped=True)
  r_liq = gross_R - cost_in_R("WDO", risk_pts, entry_limit=True, stopped=False)

Este módulo é puro (sem MT5) e é usado pelo backtest_pro.py e pelo shadow.
NÃO altera nenhum módulo existente.
"""

# Parâmetros por prefixo de símbolo
COSTS = {
    "WIN": {
        "fees_brl_rt": 0.50,    # taxas B3 + corretagem, por contrato round-trip (nota Santander)
        "point_value": 0.20,    # R$ por ponto por contrato
        "tick_size":   5.0,     # pontos por tick
    },
    "WDO": {
        "fees_brl_rt": 1.15,    # ESTIMATIVA tabela B3 day trade — confirmar com nota WDO
        "point_value": 10.0,
        "tick_size":   0.5,
    },
}

# IR day trade: 20% sobre lucro líquido mensal (1% IRRF antecipado).
# Não entra no custo por trade (não muda o sinal da expectância), mas o
# backtest reporta o líquido pós-IR como referência.
DAYTRADE_TAX = 0.20


def _cfg(symbol: str) -> dict:
    s = (symbol or "").upper()
    for prefix, cfg in COSTS.items():
        if prefix in s:
            return cfg
    # fallback conservador: sem taxas conhecidas, só spread não é modelável
    return {"fees_brl_rt": 0.0, "point_value": 1.0, "tick_size": 1.0}


def fees_pts(symbol: str) -> float:
    """Taxas fixas (B3+corretagem) convertidas para PONTOS por contrato r/t."""
    c = _cfg(symbol)
    return c["fees_brl_rt"] / c["point_value"]


def roundtrip_cost_pts(symbol: str, entry_limit: bool = False,
                       stopped: bool = False,
                       extra_slippage_ticks: float = 0.0) -> float:
    """
    Custo TOTAL em pontos de um round-trip de 1 contrato.

    entry_limit  : True se a entrada foi por ordem limite (não cruza spread)
    stopped      : True se a saída foi pelo stop (a mercado, cruza spread)
    extra_slippage_ticks : slippage adicional além do spread (cenário pessimista)
    """
    c  = _cfg(symbol)
    ts = c["tick_size"]
    cost = fees_pts(symbol)
    if not entry_limit:
        cost += ts                      # cruza spread na entrada
    if stopped:
        cost += ts                      # stop é sempre a mercado
    cost += extra_slippage_ticks * ts
    return round(cost, 4)


def cost_in_R(symbol: str, risk_pts: float, entry_limit: bool = False,
              stopped: bool = False, extra_slippage_ticks: float = 0.0) -> float:
    """Custo do round-trip expresso em múltiplos de risco (R)."""
    if not risk_pts or risk_pts <= 0:
        return 0.0
    return roundtrip_cost_pts(symbol, entry_limit, stopped,
                              extra_slippage_ticks) / float(risk_pts)


def min_viable_stop_pts(symbol: str, max_cost_r: float = 0.10) -> float:
    """
    Menor stop (em pontos) para que o custo não passe de max_cost_r do risco.
    Piso anti-corrosão: no WDO, stops curtos são comidos pelo custo fixo.
    Cenário conservador: entrada a mercado + saída no stop.
    """
    worst = roundtrip_cost_pts(symbol, entry_limit=False, stopped=True)
    return round(worst / max_cost_r, 2)


if __name__ == "__main__":
    for sym in ("WIN", "WDO"):
        print(f"── {sym} ──")
        print("  taxas (pts):", round(fees_pts(sym), 3))
        print("  r/t mercado+stop :", roundtrip_cost_pts(sym, False, True), "pts")
        print("  r/t mercado+TP   :", roundtrip_cost_pts(sym, False, False), "pts")
        print("  r/t limite+TP    :", roundtrip_cost_pts(sym, True, False), "pts")
        print("  stop mínimo p/ custo<=0.10R:", min_viable_stop_pts(sym), "pts")
