"""
market_service.py — Dados de mercado via Yahoo Finance e Finnhub.

Yahoo Finance (yfinance): gratuito, sem chave de API.
Finnhub: notícias e sentimento — requer FINNHUB_API_KEY no .env.
"""
import os
import logging
from datetime import datetime, timedelta

import requests

logger = logging.getLogger(__name__)

FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "")
FINNHUB_BASE = "https://finnhub.io/api/v1"

# Mapeamento TradingView → Yahoo Finance symbol
# Útil para buscar dados de candles via yfinance
TV_TO_YF: dict[str, str] = {
    "BMFBOVESPA:WIN1!":  "^BVSP",      # Mini Índice (proxy: Ibovespa)
    "BMFBOVESPA:WDO1!":  "BRL=X",      # Mini Dólar (proxy: USD/BRL)
    "BMFBOVESPA:IBOV":   "^BVSP",      # Ibovespa
    "BMFBOVESPA:PETR4":  "PETR4.SA",
    "BMFBOVESPA:VALE3":  "VALE3.SA",
    "BMFBOVESPA:ITUB4":  "ITUB4.SA",
    "BMFBOVESPA:BBDC4":  "BBDC4.SA",
    "BMFBOVESPA:ABEV3":  "ABEV3.SA",
    "BMFBOVESPA:WEGE3":  "WEGE3.SA",
    "BMFBOVESPA:BBAS3":  "BBAS3.SA",
    # Passa-através (já é símbolo yfinance)
    "^BVSP":  "^BVSP",
    "BRL=X":  "BRL=X",
}

# Intervalo TradingView → Yahoo Finance
TV_INTERVAL_TO_YF: dict[str, tuple[str, str]] = {
    "1":   ("1d",  "1m"),
    "5":   ("5d",  "5m"),
    "15":  ("5d",  "15m"),
    "30":  ("1mo", "30m"),
    "60":  ("1mo", "60m"),
    "D":   ("6mo", "1d"),
    "W":   ("2y",  "1wk"),
}


def tv_to_yf_symbol(tv_symbol: str) -> str:
    """Converte símbolo TradingView para Yahoo Finance."""
    key = tv_symbol.upper().strip()
    return TV_TO_YF.get(key, key)


def get_candles(yf_symbol: str, period: str = "5d", interval: str = "15m"):
    """
    Busca dados OHLCV do Yahoo Finance.
    Retorna (DataFrame | None, error_msg | None).
    """
    try:
        import yfinance as yf  # import tardio para não quebrar se não instalado
        ticker = yf.Ticker(yf_symbol)
        df = ticker.history(period=period, interval=interval, auto_adjust=True)
        if df is None or df.empty:
            return None, f"Dados não disponíveis para '{yf_symbol}' no Yahoo Finance."
        # Garante colunas padrão
        df = df.rename(columns={"Open": "Open", "High": "High", "Low": "Low",
                                 "Close": "Close", "Volume": "Volume"})
        return df, None
    except Exception as exc:
        logger.warning("yfinance error for %s: %s", yf_symbol, exc)
        return None, str(exc)


def get_news(finnhub_symbol: str | None = None, category: str = "general", limit: int = 8):
    """
    Busca notícias do Finnhub.
    Retorna (lista | [], error_msg | None).
    """
    if not FINNHUB_API_KEY:
        return [], "FINNHUB_API_KEY não configurada. Adicione ao .env para ativar notícias."

    try:
        if finnhub_symbol:
            from_date = (datetime.utcnow() - timedelta(days=7)).strftime("%Y-%m-%d")
            to_date = datetime.utcnow().strftime("%Y-%m-%d")
            url = f"{FINNHUB_BASE}/company-news"
            params = {
                "symbol": finnhub_symbol,
                "from": from_date,
                "to": to_date,
                "token": FINNHUB_API_KEY,
            }
        else:
            url = f"{FINNHUB_BASE}/news"
            params = {"category": category, "token": FINNHUB_API_KEY}

        r = requests.get(url, params=params, timeout=10)
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list):
                return data[:limit], None
            return [], "Formato de resposta inesperado."
        return [], f"Finnhub retornou HTTP {r.status_code}."
    except Exception as exc:
        logger.warning("Finnhub news error: %s", exc)
        return [], str(exc)


def get_sentiment(finnhub_symbol: str):
    """
    Busca sentimento de notícias do Finnhub.
    Retorna (dict | None, error_msg | None).
    """
    if not FINNHUB_API_KEY:
        return None, "FINNHUB_API_KEY não configurada."
    try:
        url = f"{FINNHUB_BASE}/news-sentiment"
        params = {"symbol": finnhub_symbol, "token": FINNHUB_API_KEY}
        r = requests.get(url, params=params, timeout=10)
        if r.status_code == 200:
            return r.json(), None
        return None, f"HTTP {r.status_code}"
    except Exception as exc:
        logger.warning("Finnhub sentiment error: %s", exc)
        return None, str(exc)


def get_market_status(finnhub_exchange: str = "US"):
    """Verifica se o mercado está aberto (Finnhub)."""
    if not FINNHUB_API_KEY:
        return None, "FINNHUB_API_KEY não configurada."
    try:
        url = f"{FINNHUB_BASE}/stock/market-status"
        params = {"exchange": finnhub_exchange, "token": FINNHUB_API_KEY}
        r = requests.get(url, params=params, timeout=8)
        if r.status_code == 200:
            return r.json(), None
        return None, f"HTTP {r.status_code}"
    except Exception as exc:
        return None, str(exc)
