"""
services/crypto_config.py — Configuração do módulo MONITOR CRYPTO.

⚠️ MÓDULO NOVO E INDEPENDENTE. Não importa nem altera nada do Monitor MT5 / Scalper.

Opera via MetaTrader 5 (mesma biblioteca), na conta de crypto/forex que o usuário
carrega no .env (à noite/fim de semana). Os símbolos são usados DIRETO (BTCUSD,
EURUSD, ...) — sem o mapeamento da B3.

Ativos padrão: BTCUSD + principais pares forex (ajustável).
Timeframe 24/7 — sem trava de horário de pregão.
"""
import os

# Lista de símbolos (podem vir do .env: CRYPTO_SYMBOLS="BTCUSD,ETHUSD,EURUSD")
# USDJPY removido: no backtest perde mesmo com filtro macro+ADX (-0.27R, PF 0.62).
_DEFAULT = "BTCUSD,EURUSD,GBPUSD,ETHUSD,XAUUSD"

# ── Filtro MACRO (adicionado após backtest 05-07/2026) ──────────────────────
# Só opera (caminho do score) quando a tendência do timeframe MACRO concorda com
# a direção do sinal E o ADX (M15) está acima do corte do ativo. Isso virou o
# resultado do crypto de negativo p/ neutro/positivo (ex.: XAU -0.075R → +0.131R).
MACRO_TF = 60  # H1 = tendência maior de referência (EMA50×EMA200)


def get_symbols() -> list:
    raw = os.getenv("CRYPTO_SYMBOLS", _DEFAULT)
    return [s.strip().upper() for s in raw.split(",") if s.strip()]


# Parâmetros de execução por símbolo. Defaults conservadores; o painel pode
# sobrescrever por símbolo. Valores em "pontos do símbolo" (price units).
#   score_min  : |score| mínimo para operar
#   sl_atr     : stop = sl_atr × ATR(14)
#   tp_rr      : TP1 = tp_rr × risco (R:R do primeiro alvo)
#   volume     : lotes por ordem
#   adx_min    : ADX mínimo para considerar tendência
# macro_adx: corte de ADX (M15) do FILTRO macro, calibrado por ativo no backtest.
SYMBOL_CFG = {
    "_default": {"score_min": 7, "sl_atr": 1.5, "tp_rr": 1.5, "volume": 0.10, "adx_min": 20, "macro_adx": 25},
    # Live caps: crypto_edge_setups.LIVE_SL_TP_CAPS — BTC 1000/1200, ETH 12/20,
    # EUR/GBP 30/45 pips (só execução; não altera régua de backtest).
    "BTCUSD":   {"score_min": 7, "sl_atr": 1.6, "tp_rr": 1.5, "volume": 0.10, "adx_min": 20, "macro_adx": 30},
    "ETHUSD":   {"score_min": 7, "sl_atr": 1.6, "tp_rr": 1.5, "volume": 0.10, "adx_min": 20, "macro_adx": 18},
    "XAUUSD":   {"score_min": 7, "sl_atr": 1.5, "tp_rr": 1.5, "volume": 0.05, "adx_min": 20, "macro_adx": 30},
    "EURUSD":   {"score_min": 8, "sl_atr": 1.4, "tp_rr": 1.5, "volume": 0.10, "adx_min": 22, "macro_adx": 28},
    "GBPUSD":   {"score_min": 8, "sl_atr": 1.4, "tp_rr": 1.5, "volume": 0.10, "adx_min": 22, "macro_adx": 22},
    "USDJPY":   {"score_min": 8, "sl_atr": 1.4, "tp_rr": 1.5, "volume": 0.10, "adx_min": 22, "macro_adx": 99},
}


def cfg_for(symbol: str) -> dict:
    return dict(SYMBOL_CFG.get((symbol or "").upper().strip(), SYMBOL_CFG["_default"]))


# ── CAMINHOS DE ENTRADA (backtest_crypto_pro, 1,4 anos COM custos, 18/07/2026) ──
# Veredito por caminho:
#   IMPULSO : negativo nos 3 ativos (BTC -0.064 | ETH -0.159 | XAU -0.086 R)  → OFF
#   SCORE   : zero edge líquido (BTC +0.002 | XAU +0.004) e ETH -0.151 R      → OFF
#   PULLBACK: ÚNICO positivo — XAUUSD +0.208 R, PF 1.64, DD -7.9 (n=47)      → ON
# ETHUSD: spread (~$2,9) ≈ 0.15R do risco típico em M15 — estruturalmente
# inviável nesse timeframe; auto-trade não deve operá-lo.
# Para reativar um caminho, mude aqui (e SÓ depois de um backtest aprovar).
PATHS_ENABLED = {
    "score":    False,
    "impulso":  False,
    "pullback": True,   # só XAUUSD — ver PULLBACK_SYMBOLS
}
# Pullback só foi 🟡 positivo no XAU (n=47). BTC/EUR/GBP NÃO herdam.
PULLBACK_SYMBOLS = {"XAUUSD"}
# Símbolos vetados no motor legado score/impulso/pullback:
AUTO_BLOCKED_SYMBOLS = {"ETHUSD", "BTCUSD"}


def path_enabled(name: str) -> bool:
    return bool(PATHS_ENABLED.get((name or "").lower(), False))


def auto_blocked(symbol: str) -> bool:
    return (symbol or "").upper().strip() in AUTO_BLOCKED_SYMBOLS


def pullback_enabled(symbol: str) -> bool:
    if not path_enabled("pullback"):
        return False
    return (symbol or "").upper().strip() in PULLBACK_SYMBOLS


def active_trade_paths(symbol: str) -> list:
    """Caminhos que o auto-trade realmente pode usar neste ativo (UI + mensagem)."""
    sym = (symbol or "").upper().strip()
    out = []
    try:
        from services.crypto_orderflow import orderflow_tf, LONDON_HANDOFF_SYMBOLS
        if orderflow_tf(sym):
            out.append("ORDER_FLOW")
        if sym in LONDON_HANDOFF_SYMBOLS:
            out.append("LONDON_HANDOFF")
    except Exception:
        pass
    try:
        from services.crypto_edge_setups import (
            US_DRIFT_SYMBOLS, RND_FADE_SYMBOLS, LH_047_SYMBOLS, LH_049_SYMBOLS,
            LH_048_SYMBOLS, INSIDE_H4_SYMBOLS, INSIDE_1015_SYMBOLS,
            INSIDE_1015_V13_SYMBOLS, GBP_INS_AM_V13_SYMBOLS,
            EUR_LH_047_TP165_SYMBOLS, XAU_INS_AM_SYMBOLS,
            INS_0918_V13_SYMBOLS, BTC_NR5_1016_SYMBOLS,
            GBP_INS_1017_V13_SYMBOLS, XAU_IMP_CONT_SYMBOLS,
            BTC_INS_1017_V13_SYMBOLS,
            BTC_INS_1117_V13_SYMBOLS, BTC_INS_1218_V13_SYMBOLS,
            BTC_INS_V135_0916_SYMBOLS, BTC_INS_0816_V13_SYMBOLS,
            BTC_NR5_0915_SYMBOLS, BTC_NR4_1016_SYMBOLS, BTC_H4_PB_EMA_SYMBOLS,
            BTC_INS_24H_V13_SYMBOLS, ETH_IMP_CONT_24H_SYMBOLS,
            BTC_PDH_24H_SYMBOLS, BTC_PDH_24H_V13_SYMBOLS, BTC_PDH_ASIA_0008_SYMBOLS,
            BTC_PDH_NIGHT_2010_SYMBOLS, BTC_PDH_24H_MT_SYMBOLS,
            BTC_NR4_24H_V14_SYMBOLS, BTC_NR5_24H_V14_SYMBOLS, BTC_NR5_1622_SYMBOLS,
            BTC_H4_PB_EMA_MT24_SYMBOLS,
            ETH_INS_24H_MT_SYMBOLS, ETH_HL_24H_MT_SYMBOLS,
            ETH_IMP_CONT_24H_V18_SYMBOLS, ETH_IMP_TP25_24H_SYMBOLS,
            XAU_NR4_1016_H4_SYMBOLS, XAU_PDH_H4_SYMBOLS, XAU_PDH_1115_SYMBOLS,
            XAU_HL_24H_SYMBOLS, BTC_PDH_H4_SYMBOLS, EUR_PDH_H4_SYMBOLS,
            EUR_IMP_CONT_24H_MT_SYMBOLS,
        )
        if sym in LH_047_SYMBOLS:
            out.append("LH_047_D18")
        if sym in LH_049_SYMBOLS:
            out.append("LH_049_D19")
        if sym in LH_048_SYMBOLS:
            out.append("LH_048_D19")
        if sym in EUR_LH_047_TP165_SYMBOLS:
            out.append("EUR_LH_047_TP165")
        if sym in US_DRIFT_SYMBOLS:
            out.append("US_DRIFT")
        if sym in RND_FADE_SYMBOLS:
            out.extend(["RND_FADE_065", "RND_FADE_075_MT", "RND_FADE_MT",
                        "RND_FADE_07", "RND_FADE_TIGHT"])
        if sym in XAU_IMP_CONT_SYMBOLS:
            out.append("XAU_IMP_CONT_20")
        if sym in XAU_NR4_1016_H4_SYMBOLS:
            out.append("XAU_NR4_1016_H4")
        if sym in XAU_PDH_H4_SYMBOLS:
            out.append("XAU_PDH_H4")
        if sym in XAU_PDH_1115_SYMBOLS:
            out.append("XAU_PDH_1115")
        if sym in XAU_HL_24H_SYMBOLS:
            out.append("XAU_HL_24H")
        if sym in XAU_INS_AM_SYMBOLS:
            out.append("XAU_INS_AM")
        if sym in INSIDE_H4_SYMBOLS:
            out.append("INSIDE_H4")
        if sym in BTC_NR5_0915_SYMBOLS:
            out.append("BTC_NR5_0915")
        if sym in BTC_NR5_1016_SYMBOLS:
            out.append("BTC_NR5_1016")
        if sym in BTC_NR4_1016_SYMBOLS:
            out.append("BTC_NR4_1016")
        if sym in BTC_PDH_H4_SYMBOLS:
            out.append("BTC_PDH_H4")
        if sym in INSIDE_1015_V13_SYMBOLS:
            out.append("INSIDE_1015_V13")
        if sym in BTC_INS_1117_V13_SYMBOLS:
            out.append("BTC_INS_1117_V13")
        if sym in BTC_INS_1017_V13_SYMBOLS:
            out.append("BTC_INS_1017_V13")
        if sym in BTC_INS_1218_V13_SYMBOLS:
            out.append("BTC_INS_1218_V13")
        if sym in BTC_INS_V135_0916_SYMBOLS:
            out.append("BTC_INS_V135_0916")
        if sym in BTC_INS_0816_V13_SYMBOLS:
            out.append("BTC_INS_0816_V13")
        if sym in INS_0918_V13_SYMBOLS:
            out.append("INS_0918_V13")
        if sym in INSIDE_1015_SYMBOLS:
            out.append("INSIDE_1015")
        if sym in BTC_H4_PB_EMA_SYMBOLS:
            out.append("BTC_H4_PB_EMA")
        if sym in BTC_INS_24H_V13_SYMBOLS:
            out.append("BTC_INS_24H_V13")
        if sym in ETH_IMP_CONT_24H_SYMBOLS:
            out.append("ETH_IMP_CONT_24H")
        if sym in BTC_PDH_24H_SYMBOLS:
            out.append("BTC_PDH_24H")
        if sym in BTC_PDH_24H_V13_SYMBOLS:
            out.append("BTC_PDH_24H_V13")
        if sym in BTC_PDH_ASIA_0008_SYMBOLS:
            out.append("BTC_PDH_ASIA_0008")
        if sym in BTC_PDH_NIGHT_2010_SYMBOLS:
            out.append("BTC_PDH_NIGHT_2010")
        if sym in BTC_PDH_24H_MT_SYMBOLS:
            out.append("BTC_PDH_24H_MT")
        if sym in BTC_NR4_24H_V14_SYMBOLS:
            out.append("BTC_NR4_24H_V14")
        if sym in BTC_NR5_24H_V14_SYMBOLS:
            out.append("BTC_NR5_24H_V14")
        if sym in BTC_NR5_1622_SYMBOLS:
            out.append("BTC_NR5_1622")
        if sym in BTC_H4_PB_EMA_MT24_SYMBOLS:
            out.append("BTC_H4_PB_EMA_MT24")
        if sym in ETH_INS_24H_MT_SYMBOLS:
            out.append("ETH_INS_24H_MT")
        if sym in ETH_HL_24H_MT_SYMBOLS:
            out.append("ETH_HL_24H_MT")
        if sym in ETH_IMP_CONT_24H_V18_SYMBOLS:
            out.append("ETH_IMP_CONT_24H_V18")
        if sym in ETH_IMP_TP25_24H_SYMBOLS:
            out.append("ETH_IMP_TP25_24H")
        try:
            from services.crypto_v15_gos import v15_path_names
            out.extend(v15_path_names(sym))
        except Exception:
            pass
        try:
            from services.crypto_v16_gos import v16_path_names
            out.extend(v16_path_names(sym))
        except Exception:
            pass
        try:
            from services.crypto_v17_gos import v17_path_names
            out.extend(v17_path_names(sym))
        except Exception:
            pass
        try:
            from services.crypto_v18_gos import v18_path_names
            out.extend(v18_path_names(sym))
        except Exception:
            pass
        try:
            from services.crypto_v20_gos import v20_path_names
            out.extend(v20_path_names(sym))
        except Exception:
            pass
        if sym in GBP_INS_AM_V13_SYMBOLS:
            out.append("GBP_INS_AM_V13")
        if sym in GBP_INS_1017_V13_SYMBOLS:
            out.append("GBP_INS_1017_V13")
        if sym in EUR_PDH_H4_SYMBOLS:
            out.append("EUR_PDH_H4")
        if sym in EUR_IMP_CONT_24H_MT_SYMBOLS:
            out.append("EUR_IMP_CONT_24H_MT")
    except Exception:
        pass
    try:
        from services.crypto_discovery_paths import discovery_path_names
        out.extend(discovery_path_names(sym))
    except Exception:
        pass
    # Score / impulso / pullback só se o ativo NÃO estiver vetado
    if not auto_blocked(sym):
        if path_enabled("score"):
            out.append("SCORE")
        if path_enabled("impulso"):
            out.append("IMPULSO")
        if pullback_enabled(sym):
            out.append("PULLBACK")
    return out


def score_panel_armed(symbol: str) -> bool:
    """Se False, o painel de score NÃO deve parecer gatilho de entrada."""
    if auto_blocked(symbol):
        return False
    if path_enabled("score") or path_enabled("impulso"):
        return True
    return pullback_enabled(symbol)


# Timeframes suportados (TradingView-like → minutos)
TF_MINUTES = {"1": 1, "5": 5, "15": 15, "30": 30, "60": 60, "240": 240}


def is_forex(symbol: str) -> bool:
    """Pares forex fecham no fim de semana; cripto/metais operam 24/7."""
    s = (symbol or "").upper()
    cryptos = ("BTC", "ETH", "XRP", "LTC", "BCH", "SOL", "DOGE", "ADA")
    return not any(c in s for c in cryptos)
