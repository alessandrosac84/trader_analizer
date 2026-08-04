"""
services/acoes_config.py — Monitor Ações B3 (blue chips).

Módulo isolado: NÃO mistura com Monitor Índices B3 (WIN/WDO) nem Crypto.
Sem auto-trade até setups 🟢 GO (mesma régua: net/PF/cons/OOS/n).
"""
import os

# Universo inicial (paper): blue chips líquidas B3 via MT5
_DEFAULT = "PETR4,VALE3,ITUB4,BBDC4,BBAS3,ABEV3,WEGE3"


def get_symbols() -> list:
    raw = os.getenv("ACOES_SYMBOLS", _DEFAULT)
    return [s.strip().upper() for s in raw.split(",") if s.strip()]


SYMBOL_CFG = {
    "_default": {"volume": 100.0, "score_min": 7},  # volume = ações (lote 100 típico)
    "PETR4": {"volume": 100.0},
    "VALE3": {"volume": 100.0},
    "ITUB4": {"volume": 100.0},
    "BBDC4": {"volume": 100.0},
    "BBAS3": {"volume": 100.0},
    "ABEV3": {"volume": 100.0},
    "WEGE3": {"volume": 100.0},
}


def cfg_for(symbol: str) -> dict:
    base = dict(SYMBOL_CFG.get("_default", {}))
    base.update(SYMBOL_CFG.get((symbol or "").upper(), {}))
    return base
