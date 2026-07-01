"""
market_service.py - Dados de mercado via MetaTrader5 (primario) + Yahoo Finance (fallback).

Prioridade de fonte de dados:
  1. MetaTrader5 (XP) - WINQ26/WDOQ26 em tempo real, volume real, VWAP funcional
  2. Yahoo Finance    - fallback para acoes B3 e quando MT5 nao esta disponivel
  3. Finnhub         - noticias e sentimento (requer FINNHUB_API_KEY no .env)

Variaveis de ambiente (.env):
  WIN_MT5_SYMBOL=WINQ26   # atualizar ao rolar contrato (ex: WINV26 em out/2026)
  WDO_MT5_SYMBOL=WDOQ26
  FINNHUB_API_KEY=...
"""
import os
import logging
from datetime import datetime, timedelta

import pandas as pd
import requests

logger = logging.getLogger(__name__)

FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "")
FINNHUB_BASE = "https://finnhub.io/api/v1"

# -- MetaTrader5 -----------------------------------------------------------
MT5_AVAILABLE = False
try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
    logger.info("MetaTrader5 disponivel -- usando como fonte primaria para WIN/WDO.")
except ImportError:
    logger.warning("MetaTrader5 nao instalado -- usando Yahoo Finance como fallback.")

# Simbolos do contrato atual (atualizar ao rolar)
WIN_MT5_SYMBOL = os.getenv("WIN_MT5_SYMBOL", "WINQ26")
WDO_MT5_SYMBOL = os.getenv("WDO_MT5_SYMBOL", "WDOQ26")   # contrato atual (jul/2026)

# Mapeamento TradingView -> MT5 (apenas futuros B3 disponiveis na XP)
TV_TO_MT5: dict = {
    "BMFBOVESPA:WIN1!": WIN_MT5_SYMBOL,
    "BMFBOVESPA:WDO1!": WDO_MT5_SYMBOL,
    "BMFBOVESPA:PETR4":    "PETR4",
    "BMFBOVESPA:RADL3":    "RADL3",
    # Forex 24/5 -- disponivel na conta Clear/Rico (trocar conta ativa no MT5)
    "FX:EURUSD":           "EURUSD",
    "FX:GBPUSD":           "GBPUSD",
    "FX:XAUUSD":           "XAUUSD",   # Ouro
    "CRYPTO:BTCUSD":       os.getenv("BTC_MT5_SYMBOL", "BTCUSD"),
    "CRYPTO:BTCUSDETF":    "QBTC11",
}

# Mapeamento TradingView -> Yahoo Finance (fallback para todos os ativos)
TV_TO_YF: dict = {
    "BMFBOVESPA:WIN1!":  "^BVSP",
    "BMFBOVESPA:WDO1!":  "BRL=X",
    "BMFBOVESPA:IBOV":   "^BVSP",
    "BMFBOVESPA:PETR4":  "PETR4.SA",
    "BMFBOVESPA:VALE3":  "VALE3.SA",
    "BMFBOVESPA:ITUB4":  "ITUB4.SA",
    "BMFBOVESPA:BBDC4":  "BBDC4.SA",
    "BMFBOVESPA:ABEV3":  "ABEV3.SA",
    "BMFBOVESPA:WEGE3":  "WEGE3.SA",
    "BMFBOVESPA:BBAS3":  "BBAS3.SA",
    "^BVSP":  "^BVSP",
    "BRL=X":  "BRL=X",
    "CRYPTO:BTCUSD":    "BTC-USD",
    "CRYPTO:BTCUSDETF": "QBTC11.SA",
}

# Intervalo TradingView -> Yahoo Finance (period, interval)
TV_INTERVAL_TO_YF: dict = {
    "1":   ("1d",  "1m"),
    "5":   ("5d",  "5m"),
    "15":  ("5d",  "15m"),
    "30":  ("1mo", "30m"),
    "60":  ("1mo", "60m"),
    "D":   ("6mo", "1d"),
    "W":   ("2y",  "1wk"),
}

# Intervalo TradingView -> numero de candles a buscar no MT5
TV_INTERVAL_TO_CANDLES: dict = {
    "1":  500,
    "5":  300,
    "15": 200,
    "30": 200,
    "60": 200,
    "D":  300,
    "W":  200,
}


def tv_to_yf_symbol(tv_symbol: str) -> str:
    """Converte simbolo TradingView para Yahoo Finance (usado como fallback)."""
    key = tv_symbol.upper().strip()
    return TV_TO_YF.get(key, key)


def _tv_to_mt5_timeframe(tv_interval: str):
    """Converte intervalo TradingView para constante de timeframe do MT5."""
    if not MT5_AVAILABLE:
        return None
    mapping = {
        "1":  mt5.TIMEFRAME_M1,
        "5":  mt5.TIMEFRAME_M5,
        "15": mt5.TIMEFRAME_M15,
        "30": mt5.TIMEFRAME_M30,
        "60": mt5.TIMEFRAME_H1,
        "D":  mt5.TIMEFRAME_D1,
        "W":  mt5.TIMEFRAME_W1,
    }
    return mapping.get(str(tv_interval), mt5.TIMEFRAME_M15)


def _get_candles_mt5(mt5_symbol: str, tv_interval: str = "15") -> tuple:
    """
    Busca candles do MetaTrader5 com retry automatico (ate 3 tentativas).
    Requer que o terminal MT5 esteja aberto e logado.
    Retorna (DataFrame | None, error_msg | None).
    """
    import time as _time

    if not MT5_AVAILABLE:
        return None, "MetaTrader5 nao instalado."

    _login    = int(os.getenv("MT5_LOGIN", "0") or 0)
    _password = os.getenv("MT5_PASSWORD", "")
    _server   = os.getenv("MT5_SERVER", "")
    _path     = os.getenv("MT5_PATH", "")

    kwargs = {}
    if _path and os.path.exists(_path):
        kwargs["path"] = _path
    # Passa credenciais somente para servidores XP/B3.
    # MetaQuotes-Demo nao aceita login via Python — usa terminal ja aberto.
    _mq_servers = {"metaquotes-demo", "metaquotes-demo2"}
    if _login and _password and _server and _server.lower() not in _mq_servers:
        kwargs["login"]    = _login
        kwargs["password"] = _password
        kwargs["server"]   = _server

    timeframe = _tv_to_mt5_timeframe(tv_interval)
    n_candles = TV_INTERVAL_TO_CANDLES.get(str(tv_interval), 200)

    last_error = "Sem dados."
    for attempt in range(3):
        try:
            if attempt > 0:
                _time.sleep(0.5)   # aguarda antes de tentar novamente

            ok = mt5.initialize(**kwargs)
            if not ok:
                last_error = "MT5 nao inicializado: {}. Terminal aberto e logado?".format(mt5.last_error())
                try: mt5.shutdown()
                except Exception: pass
                continue

            rates = mt5.copy_rates_from_pos(mt5_symbol, timeframe, 0, n_candles)
            mt5.shutdown()

            if rates is None or len(rates) == 0:
                last_error = (
                    "Sem dados MT5 para '{}' ({}m). "
                    "Verifique se o simbolo esta no Market Watch e o terminal esta ativo."
                ).format(mt5_symbol, tv_interval)
                continue  # tenta novamente

            df = pd.DataFrame(rates)
            df["time"] = pd.to_datetime(df["time"], unit="s")
            df = df.set_index("time")
            df = df.rename(columns={
                "open":        "Open",
                "high":        "High",
                "low":         "Low",
                "close":       "Close",
                "tick_volume": "Volume",
            })
            df = df[["Open", "High", "Low", "Close", "Volume"]]
            logger.info(
                "MT5: %d candles de %s (%sm) tentativa=%d preco=%.0f",
                len(df), mt5_symbol, tv_interval, attempt + 1, df["Close"].iloc[-1]
            )
            return df, None

        except Exception as exc:
            last_error = str(exc)
            logger.warning("MT5 tentativa %d erro para %s: %s", attempt + 1, mt5_symbol, exc)
            try: mt5.shutdown()
            except Exception: pass

    return None, last_error


def _get_candles_yf(yf_symbol: str, period: str = "5d", interval: str = "15m") -> tuple:
    """
    Busca candles do Yahoo Finance (fallback).
    Retorna (DataFrame | None, error_msg | None).
    """
    try:
        import yfinance as yf
        ticker = yf.Ticker(yf_symbol)
        df = ticker.history(period=period, interval=interval, auto_adjust=True)
        if df is None or df.empty:
            return None, "Dados nao disponiveis para '{}' no Yahoo Finance.".format(yf_symbol)
        df = df.rename(columns={
            "Open": "Open", "High": "High", "Low": "Low",
            "Close": "Close", "Volume": "Volume",
        })
        logger.info("YFinance: %d candles de %s (%s/%s)", len(df), yf_symbol, period, interval)
        return df, None
    except Exception as exc:
        logger.warning("yfinance erro para %s: %s", yf_symbol, exc)
        return None, str(exc)


def get_candles(
    symbol: str,
    period: str = "5d",
    interval: str = "15m",
    tv_interval=None,
) -> tuple:
    """
    Busca dados OHLCV via MetaTrader5 (tempo real, direto do terminal MT5 aberto).
    Requer MT5 instalado, terminal aberto e logado.

    Parametros
    ----------
    symbol      : Simbolo TradingView (ex: "BMFBOVESPA:WIN1!", "CRYPTO:BTCUSD")
    period      : Ignorado (mantido por compatibilidade)
    interval    : Ignorado (mantido por compatibilidade)
    tv_interval : Intervalo TradingView (ex: "15", "60")

    Retorna (DataFrame | None, error_msg | None).
    """
    symbol_key = symbol.upper().strip()

    if not MT5_AVAILABLE:
        return None, "MetaTrader5 nao instalado. Execute: pip install MetaTrader5"

    if tv_interval is None:
        return None, "tv_interval obrigatorio."

    if symbol_key not in TV_TO_MT5:
        return None, "Simbolo '{}' nao mapeado para MT5.".format(symbol)

    mt5_symbol = TV_TO_MT5[symbol_key]
    df, error = _get_candles_mt5(mt5_symbol, tv_interval=str(tv_interval))

    if df is None:
        return None, "MT5 sem dados para '{}': {}".format(mt5_symbol, error)

    return df, None


# -- Finnhub - Noticias e Sentimento ---------------------------------------

def get_news(finnhub_symbol=None, category: str = "general", limit: int = 8):
    """
    Busca noticias do Finnhub.
    Retorna (lista | [], error_msg | None).
    """
    if not FINNHUB_API_KEY:
        return [], "FINNHUB_API_KEY nao configurada. Adicione ao .env para ativar noticias."
    try:
        if finnhub_symbol:
            from_date = (datetime.utcnow() - timedelta(days=7)).strftime("%Y-%m-%d")
            to_date   = datetime.utcnow().strftime("%Y-%m-%d")
            url    = "{}/company-news".format(FINNHUB_BASE)
            params = {"symbol": finnhub_symbol, "from": from_date, "to": to_date, "token": FINNHUB_API_KEY}
        else:
            url    = "{}/news".format(FINNHUB_BASE)
            params = {"category": category, "token": FINNHUB_API_KEY}

        r = requests.get(url, params=params, timeout=10)
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list):
                return data[:limit], None
            return [], "Formato de resposta inesperado."
        return [], "Finnhub retornou HTTP {}.".format(r.status_code)
    except Exception as exc:
        logger.warning("Finnhub news error: %s", exc)
        return [], str(exc)


def get_sentiment(finnhub_symbol: str):
    """Busca sentimento de noticias do Finnhub."""
    if not FINNHUB_API_KEY:
        return None, "FINNHUB_API_KEY nao configurada."
    try:
        url    = "{}/news-sentiment".format(FINNHUB_BASE)
        params = {"symbol": finnhub_symbol, "token": FINNHUB_API_KEY}
        r = requests.get(url, params=params, timeout=10)
        if r.status_code == 200:
            return r.json(), None
        return None, "HTTP {}".format(r.status_code)
    except Exception as exc:
        return None, str(exc)


def get_market_status(finnhub_exchange: str = "US"):
    """Verifica se o mercado esta aberto (Finnhub)."""
    if not FINNHUB_API_KEY:
        return None, "FINNHUB_API_KEY nao configurada."
    try:
        url    = "{}/stock/market-status".format(FINNHUB_BASE)
        params = {"exchange": finnhub_exchange, "token": FINNHUB_API_KEY}
        r = requests.get(url, params=params, timeout=8)
        if r.status_code == 200:
            return r.json(), None
        return None, "HTTP {}".format(r.status_code)
    except Exception as exc:
        return None, str(exc)
