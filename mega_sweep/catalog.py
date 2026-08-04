"""
Catálogo finito de famílias × ativos — modo GAPS (opção 2).

Célula = (family, symbol). Já 🟢 live ou 🔴 documentado → SKIP.
O runner gera variantes paramétricas só nas células abertas.
"""

# Famílias do projeto (núcleo; variantes = knobs no runner)
FAMILIES = [
    "INSIDE",       # inside-bar break + vol + H4
    "NR",           # NR4/5/7 break + H4
    "IMP_CONT",     # impulso continuação
    "H4_PB_EMA",    # pullback EMA + bias H4
    "LH",           # London handoff (asia range)
    "HL",           # higher-low / lower-high chain
    "PDH",          # prev day high/low break
    "OUTSIDE",      # outside bar
    "DBL_INS",      # double inside
    "RND_FADE",     # round-number fade (XAU-like)
]

# Ativos com dados M15 no repo / live
SYMBOLS_CRYPTO = ["BTCUSD", "ETHUSD", "XAUUSD", "EURUSD", "GBPUSD"]
SYMBOLS_B3 = ["WIN$D", "WDO$D"]  # ações: passagem leve separada (já 2× zero GO)

# ── Já live (não retestar a mesma célula família×ativo) ─────────────────────
# Chave: (FAMILY, SYMBOL). Variantes da mesma família no mesmo ativo = coberta.
LIVE_CELLS = {
    # BTC — inside/NR/PB/PDH cobertos (diurno + 24h + mega)
    ("INSIDE", "BTCUSD"), ("NR", "BTCUSD"), ("H4_PB_EMA", "BTCUSD"), ("PDH", "BTCUSD"),
    # ETH
    ("INSIDE", "ETHUSD"), ("IMP_CONT", "ETHUSD"),  # OF é família aparte (não neste catálogo)
    # XAU — muito coberto (+ NR/PDH/HL mega 26/07)
    ("INSIDE", "XAUUSD"), ("LH", "XAUUSD"), ("RND_FADE", "XAUUSD"), ("IMP_CONT", "XAUUSD"),
    ("NR", "XAUUSD"), ("PDH", "XAUUSD"), ("HL", "XAUUSD"),
    # EUR / GBP (finos)
    ("LH", "EURUSD"), ("PDH", "EURUSD"),
    ("INSIDE", "GBPUSD"),
    # WIN WinGo
    ("INSIDE", "WIN$D"), ("NR", "WIN$D"), ("HL", "WIN$D"),
    ("PDH", "WIN$D"), ("IMP_CONT", "WIN$D"),
    # WDO (+ v19: OUTSIDE / IMP_CONT wired)
    ("NR", "WDO$D"), ("PDH", "WDO$D"), ("HL", "WDO$D"),
    ("OUTSIDE", "WDO$D"), ("IMP_CONT", "WDO$D"),
}

# ── 🔴 documentado: não repetir (SETUPS_TESTADOS / labs) ────────────────────
SKIP_CELLS = {
    ("ORDER_FLOW", "BTCUSD"),  # OOS caiu — família OF tratada à parte
    # Lab 24h crypto inteiro 🔴 (ASIA_BREAK etc.) — não são estas famílias nomeadas
    # Ações: mega zero GO — runner acoes separado só “leve”
}

# Famílias que NÃO aplicamos em B3 futuros (sessão/pregão)
CRYPTO_ONLY = {"LH", "RND_FADE"}


def open_cells(symbols=None):
    """Lista (family, symbol) ainda sem live e sem skip."""
    syms = symbols or (SYMBOLS_CRYPTO + SYMBOLS_B3)
    out = []
    for sym in syms:
        for fam in FAMILIES:
            if fam in CRYPTO_ONLY and sym in SYMBOLS_B3:
                continue
            cell = (fam, sym)
            if cell in LIVE_CELLS or cell in SKIP_CELLS:
                continue
            out.append(cell)
    return out
