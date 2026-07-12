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
_DEFAULT = "BTCUSD,EURUSD,GBPUSD,USDJPY,ETHUSD,XAUUSD"


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
SYMBOL_CFG = {
    "_default": {"score_min": 7, "sl_atr": 1.5, "tp_rr": 1.5, "volume": 0.10, "adx_min": 20},
    "BTCUSD":   {"score_min": 7, "sl_atr": 1.6, "tp_rr": 1.5, "volume": 0.10, "adx_min": 20},
    "ETHUSD":   {"score_min": 7, "sl_atr": 1.6, "tp_rr": 1.5, "volume": 0.10, "adx_min": 20},
    "XAUUSD":   {"score_min": 7, "sl_atr": 1.5, "tp_rr": 1.5, "volume": 0.05, "adx_min": 20},
    "EURUSD":   {"score_min": 8, "sl_atr": 1.4, "tp_rr": 1.5, "volume": 0.10, "adx_min": 22},
    "GBPUSD":   {"score_min": 8, "sl_atr": 1.4, "tp_rr": 1.5, "volume": 0.10, "adx_min": 22},
    "USDJPY":   {"score_min": 8, "sl_atr": 1.4, "tp_rr": 1.5, "volume": 0.10, "adx_min": 22},
}


def cfg_for(symbol: str) -> dict:
    return dict(SYMBOL_CFG.get((symbol or "").upper().strip(), SYMBOL_CFG["_default"]))


# Timeframes suportados (TradingView-like → minutos)
TF_MINUTES = {"1": 1, "5": 5, "15": 15, "30": 30, "60": 60, "240": 240}


def is_forex(symbol: str) -> bool:
    """Pares forex fecham no fim de semana; cripto/metais operam 24/7."""
    s = (symbol or "").upper()
    cryptos = ("BTC", "ETH", "XRP", "LTC", "BCH", "SOL", "DOGE", "ADA")
    return not any(c in s for c in cryptos)
