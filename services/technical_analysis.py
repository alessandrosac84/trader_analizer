"""
technical_analysis.py - Motor de analise tecnica v4.

Indicadores:
  - EMA 9 / 21 / 50 / 200  (tendencia + crossover + slope + filtro maior)
  - VWAP diario       (referencia institucional intraday)
  - RSI 14            (zona de extremo + direcao / momentum)
  - MACD 12/26/9      (histograma > 0 = momentum positivo + crossovers)
  - Bollinger Bands 20 (posicao vs SMA20 + extremos)
  - ATR 14            (stop e alvos)
  - ADX 14            (filtro de range - ADX < 20 bloqueia sinal)
  - Volume            (confirmacao: volume acima da media reforca o movimento)
  - Padroes de candle  (hammer, shooting star, engolfo, marubozu, doji)
  - Confirmacao multi-timeframe (1h confirma 15m)
  - Suporte/Resistencia automatico via swing highs/lows (50 candles)
    TP1 usa nivel S/R mais proximo quando disponivel

v4 vs v3:
  - EMA200 adicionada: ±2 pts quando preco acima/abaixo da media de 200 periodos
  - Volume: ±1 pt quando volume acima de 1.3x media (confirma direcao do movimento)
  - S/R automatico: swing highs/lows dos ultimos 50 candles
  - TP1 usa primeiro nivel de resistencia (COMPRA) ou suporte (VENDA) mais proximo
    se estiver entre 0.5x e 2.5x ATR - caso contrario mantem 1.5x ATR
  - suporte/resistencia retornado agora usa os niveis S/R detectados em vez de
    simples min/max dos ultimos 20 candles
"""
import logging
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Indicadores - funcoes puras
# ---------------------------------------------------------------------------

def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain  = delta.clip(lower=0).rolling(period, min_periods=period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period, min_periods=period).mean()
    rs    = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _adx(df: pd.DataFrame, period: int = 14):
    """Retorna (ADX, +DI, -DI) como Series."""
    high  = df["High"]
    low   = df["Low"]
    close = df["Close"]

    up   = high.diff()
    down = -low.diff()

    plus_dm  = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)

    tr = pd.concat(
        [high - low,
         (high - close.shift(1)).abs(),
         (low  - close.shift(1)).abs()],
        axis=1
    ).max(axis=1)

    alpha    = 1.0 / period
    atr14    = tr.ewm(alpha=alpha, adjust=False).mean()
    plus_di  = 100 * plus_dm.ewm(alpha=alpha, adjust=False).mean() / atr14
    minus_di = 100 * minus_dm.ewm(alpha=alpha, adjust=False).mean() / atr14

    denom = (plus_di + minus_di).replace(0, np.nan)
    dx    = 100 * (plus_di - minus_di).abs() / denom
    adx   = dx.ewm(alpha=alpha, adjust=False).mean()
    return adx, plus_di, minus_di


def _vwap(df: pd.DataFrame) -> pd.Series:
    """VWAP diario - reseta a cada dia. Retorna NaN se volume ausente."""
    tp  = (df["High"] + df["Low"] + df["Close"]) / 3
    vol = df["Volume"]

    if vol.sum() == 0:
        return pd.Series(np.nan, index=df.index)

    try:
        dates   = pd.Series(df.index.date, index=df.index)
        cum_tpv = (tp * vol).groupby(dates).cumsum()
        cum_vol = vol.groupby(dates).cumsum()
    except Exception:
        cum_tpv = (tp * vol).cumsum()
        cum_vol = vol.cumsum()

    return cum_tpv / cum_vol.replace(0, np.nan)


def _find_sr_levels(df: pd.DataFrame, n: int = 50) -> "tuple[list[float], list[float]]":
    """
    Detecta suportes e resistencias via swing highs/lows nos ultimos N candles.

    Returns
    -------
    (supports, resistances) - listas ordenadas de precos
    """
    recent = df.tail(n)
    highs  = recent["High"].values
    lows   = recent["Low"].values

    supports    : list[float] = []
    resistances : list[float] = []

    # Swing high: maximo local > 2 candles antes e 2 depois
    for i in range(2, len(highs) - 2):
        if (highs[i] > highs[i-1] and highs[i] > highs[i-2] and
                highs[i] > highs[i+1] and highs[i] > highs[i+2]):
            resistances.append(float(highs[i]))

    # Swing low: minimo local < 2 candles antes e 2 depois
    for i in range(2, len(lows) - 2):
        if (lows[i] < lows[i-1] and lows[i] < lows[i-2] and
                lows[i] < lows[i+1] and lows[i] < lows[i+2]):
            supports.append(float(lows[i]))

    # Remove niveis muito proximos (dentro de 0.1% do preco medio)
    def dedupe(lvls: list[float]) -> list[float]:
        if not lvls:
            return []
        lvls = sorted(lvls)
        result = [lvls[0]]
        for lv in lvls[1:]:
            if abs(lv - result[-1]) / max(abs(result[-1]), 1) > 0.001:
                result.append(lv)
        return result

    return dedupe(supports), dedupe(resistances)


def _nearest_sr_tp(
    price: float,
    levels: list[float],
    direction: str,
    atr: float,
) -> "float | None":
    """
    Retorna o nivel S/R mais proximo na direcao do trade para usar como TP1.

    direction: "above" (COMPRA) ou "below" (VENDA)
    Aceita niveis entre 0.5x e 2.5x ATR de distancia do preco atual.
    """
    if not levels or atr <= 0:
        return None

    min_dist = atr * 0.5
    max_dist = atr * 2.5

    if direction == "above":
        candidates = [lv for lv in levels
                      if min_dist < (lv - price) <= max_dist]
        return min(candidates) if candidates else None
    else:
        candidates = [lv for lv in levels
                      if min_dist < (price - lv) <= max_dist]
        return max(candidates) if candidates else None


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Calcula todos os indicadores sobre um DataFrame OHLCV."""
    df = df.copy()
    c  = df["Close"]

    # EMAs
    df["ema9"]   = c.ewm(span=9,   adjust=False).mean()
    df["ema21"]  = c.ewm(span=21,  adjust=False).mean()
    df["ema50"]  = c.ewm(span=50,  adjust=False).mean()
    df["ema200"] = c.ewm(span=200, adjust=False).mean()

    # VWAP
    df["vwap"] = _vwap(df)

    # RSI
    df["rsi"] = _rsi(c, 14)

    # MACD
    ema12             = c.ewm(span=12, adjust=False).mean()
    ema26             = c.ewm(span=26, adjust=False).mean()
    df["macd"]        = ema12 - ema26
    df["macd_signal"] = df["macd"].ewm(span=9, adjust=False).mean()
    df["macd_hist"]   = df["macd"] - df["macd_signal"]

    # Bollinger Bands (20, 2 sigma)
    sma20          = c.rolling(20).mean()
    std20          = c.rolling(20).std()
    df["bb_upper"] = sma20 + 2 * std20
    df["bb_lower"] = sma20 - 2 * std20
    df["bb_mid"]   = sma20

    # ATR (14)
    hl        = df["High"] - df["Low"]
    hc        = (df["High"] - df["Close"].shift(1)).abs()
    lc        = (df["Low"]  - df["Close"].shift(1)).abs()
    df["tr"]  = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    df["atr"] = df["tr"].rolling(14, min_periods=1).mean()

    # ADX (14)
    df["adx"], df["plus_di"], df["minus_di"] = _adx(df, 14)

    return df


# ---------------------------------------------------------------------------
# Reconhecimento de padroes de candle
# ---------------------------------------------------------------------------

def detect_candle_patterns(df: pd.DataFrame) -> "tuple[list[str], int]":
    """
    Analisa os ultimos 2 candles e retorna (lista_de_padroes, score_parcial).
    Score: +-2 engolfo, +-1 hammer / shooting star / marubozu.
    """
    if len(df) < 2:
        return [], 0

    patterns: list[str] = []
    score = 0

    last = df.iloc[-1]
    prev = df.iloc[-2]

    o, h, l, c     = float(last["Open"]), float(last["High"]), float(last["Low"]), float(last["Close"])
    po, ph, pl, pc  = float(prev["Open"]), float(prev["High"]), float(prev["Low"]), float(prev["Close"])

    body        = abs(c - o)
    range_      = h - l
    upper_wick  = h - max(o, c)
    lower_wick  = min(o, c) - l
    prev_body   = abs(pc - po)

    if range_ < 1e-9:
        return patterns, score

    body_ratio = body / range_

    # Doji - indecisao, sem pontuacao (informativo)
    if body_ratio < 0.08:
        patterns.append("Doji - indecisao / aguardar confirmacao")

    # Hammer (bullish)
    if body_ratio < 0.4 and lower_wick >= 2 * body and upper_wick <= body * 0.5 and c > o:
        patterns.append("Hammer - potencial reversao de alta [+1]")
        score += 1

    # Shooting Star (bearish)
    if body_ratio < 0.4 and upper_wick >= 2 * body and lower_wick <= body * 0.5 and c < o:
        patterns.append("Shooting Star - potencial reversao de baixa [-1]")
        score -= 1

    # Engolfo de Alta
    if (pc > po and c > o and o <= pc and c >= po and body > prev_body * 0.8):
        patterns.append("Engolfo de Alta - reversao bullish forte [+2]")
        score += 2

    # Engolfo de Baixa
    if (pc < po and c < o and o >= pc and c <= po and body > prev_body * 0.8):
        patterns.append("Engolfo de Baixa - reversao bearish forte [-2]")
        score -= 2

    # Marubozu de Alta
    if c > o and body_ratio >= 0.80:
        patterns.append("Marubozu de Alta - forca compradora dominante [+1]")
        score += 1

    # Marubozu de Baixa
    if o > c and body_ratio >= 0.80:
        patterns.append("Marubozu de Baixa - forca vendedora dominante [-1]")
        score -= 1

    return patterns, score


# ---------------------------------------------------------------------------
# Helpers numericos
# ---------------------------------------------------------------------------

def _f(v) -> "float | None":
    try:
        fv = float(v)
        return None if fv != fv else fv  # NaN check
    except Exception:
        return None


def _r(v, decimals: int = 3) -> "float | None":
    fv = _f(v)
    return round(fv, decimals) if fv is not None else None


def _s(points: int) -> str:
    """Formata contribuicao de score para exibicao: [+2] ou [-1]."""
    return f"[{'+' if points >= 0 else ''}{points}]"


# ---------------------------------------------------------------------------
# Gerador de sinal principal
# ---------------------------------------------------------------------------

COMPRA_THRESHOLD = 4
VENDA_THRESHOLD  = -4
ADX_MIN_TREND    = 20   # abaixo disso -> range -> bloqueia sinal


def generate_signal(
    df: pd.DataFrame,
    htf_df: "pd.DataFrame | None" = None,
) -> "dict | None":
    """
    Gera sinal de trading completo (v4).

    Parametros
    ----------
    df     : DataFrame OHLCV do timeframe operacional (ex.: 15m).
    htf_df : DataFrame OHLCV do timeframe maior (ex.: 1h) - confirmacao opcional.

    Retorna dict com acao, score, forca, entrada, stop, tp1/2/3,
    indicadores, padroes e sinais anotados. None se dados insuficientes.
    """
    if df is None or len(df) < 30:
        return None

    df = compute_indicators(df)
    if len(df) < 5:
        return None

    last = df.iloc[-1]
    prev = df.iloc[-2]

    close = _f(last["Close"])
    if close is None:
        return None

    # Valores dos indicadores
    rsi       = _f(last["rsi"])
    atr       = _f(last["atr"]) or (close * 0.005)
    ema9      = _f(last["ema9"])
    ema21     = _f(last["ema21"])
    ema50     = _f(last["ema50"])
    ema200    = _f(last["ema200"])
    vwap      = _f(last["vwap"])
    macd      = _f(last["macd"])
    macd_sig  = _f(last["macd_signal"])
    macd_hist = _f(last["macd_hist"])
    bb_upper  = _f(last["bb_upper"])
    bb_lower  = _f(last["bb_lower"])
    bb_mid    = _f(last["bb_mid"])
    adx       = _f(last["adx"])
    plus_di   = _f(last["plus_di"])
    minus_di  = _f(last["minus_di"])

    prev_ema9      = _f(prev["ema9"])
    prev_ema21     = _f(prev["ema21"])
    prev_macd_hist = _f(prev["macd_hist"])

    # RSI e EMA9 5 candles atras (para slope/direcao)
    rsi_5_ago  = _f(df.iloc[-6]["rsi"])  if len(df) >= 6 else None
    ema9_5_ago = _f(df.iloc[-6]["ema9"]) if len(df) >= 6 else None

    # Volume atual vs media 20 candles
    vol_series = df["Volume"]
    last_vol   = _f(vol_series.iloc[-1])
    avg_vol    = _f(vol_series.tail(20).mean())
    last_close = _f(df["Close"].iloc[-1])
    prev_close = _f(df["Close"].iloc[-2]) if len(df) >= 2 else None

    score:  int       = 0
    sinais: list[str] = []

    # 1. EMA 9/21 - Tendencia de curto prazo
    if ema9 is not None and ema21 is not None:
        if ema9 > ema21:
            score += 1
            sinais.append(f"EMA9 > EMA21 - tendencia de alta {_s(+1)}")
        else:
            score -= 1
            sinais.append(f"EMA9 < EMA21 - tendencia de baixa {_s(-1)}")

        if prev_ema9 is not None and prev_ema21 is not None:
            if ema9 > ema21 and prev_ema9 <= prev_ema21:
                score += 2
                sinais.append(f"Cruzamento de alta: EMA9 cruzou acima da EMA21 {_s(+2)}")
            elif ema9 < ema21 and prev_ema9 >= prev_ema21:
                score -= 2
                sinais.append(f"Cruzamento de baixa: EMA9 cruzou abaixo da EMA21 {_s(-2)}")

        if ema9_5_ago is not None:
            if ema9 > ema9_5_ago:
                score += 1
                sinais.append(f"EMA9 ascendente (slope positivo) {_s(+1)}")
            else:
                score -= 1
                sinais.append(f"EMA9 descendente (slope negativo) {_s(-1)}")

    # 2. EMA 50 - Filtro de tendencia medio prazo
    if ema50 is not None:
        if close > ema50:
            score += 1
            sinais.append(f"Preco acima da EMA50 - contexto de alta {_s(+1)}")
        else:
            score -= 1
            sinais.append(f"Preco abaixo da EMA50 - contexto de baixa {_s(-1)}")

    # 3. EMA 200 - Filtro de tendencia longa (v4 novo)
    if ema200 is not None:
        if close > ema200:
            score += 2
            sinais.append(f"Preco acima da EMA200 ({_r(ema200, 0)}) - tendencia de longo prazo de alta {_s(+2)}")
        else:
            score -= 2
            sinais.append(f"Preco abaixo da EMA200 ({_r(ema200, 0)}) - tendencia de longo prazo de baixa {_s(-2)}")

    # 4. VWAP - Referencia institucional intraday
    if vwap is not None:
        if close > vwap:
            score += 1
            sinais.append(f"Preco acima do VWAP ({_r(vwap)}) - pressao compradora {_s(+1)}")
        else:
            score -= 1
            sinais.append(f"Preco abaixo do VWAP ({_r(vwap)}) - pressao vendedora {_s(-1)}")

    # 5. RSI 14 - Momentum e extremos
    if rsi is not None:
        if rsi < 30:
            score += 2
            sinais.append(f"RSI em sobrevenda ({rsi:.1f}) - reversao de alta provavel {_s(+2)}")
        elif rsi < 40:
            score += 1
            sinais.append(f"RSI em zona fraca ({rsi:.1f}) {_s(+1)}")
        elif rsi > 70:
            score -= 2
            sinais.append(f"RSI em sobrecompra ({rsi:.1f}) - reversao de baixa provavel {_s(-2)}")
        elif rsi > 60:
            score -= 1
            sinais.append(f"RSI em zona forte ({rsi:.1f}) {_s(-1)}")
        else:
            sinais.append(f"RSI neutro ({rsi:.1f}) [0]")

        if rsi_5_ago is not None:
            diff = rsi - rsi_5_ago
            if diff > 3:
                score += 1
                sinais.append(f"RSI em alta ({rsi:.1f} vs {rsi_5_ago:.1f}) - momentum positivo {_s(+1)}")
            elif diff < -3:
                score -= 1
                sinais.append(f"RSI em queda ({rsi:.1f} vs {rsi_5_ago:.1f}) - momentum negativo {_s(-1)}")

    # 6. MACD 12/26/9
    if macd_hist is not None:
        crossover_happened = False
        if prev_macd_hist is not None:
            if macd_hist > 0 and prev_macd_hist <= 0:
                score += 2
                sinais.append(f"MACD cruzou acima da linha de sinal - reversao bullish {_s(+2)}")
                crossover_happened = True
            elif macd_hist < 0 and prev_macd_hist >= 0:
                score -= 2
                sinais.append(f"MACD cruzou abaixo da linha de sinal - reversao bearish {_s(-2)}")
                crossover_happened = True

        if macd_hist > 0:
            score += 1
            if not crossover_happened:
                sinais.append(f"MACD histograma positivo - momentum de alta {_s(+1)}")
        else:
            score -= 1
            if not crossover_happened:
                sinais.append(f"MACD histograma negativo - momentum de baixa {_s(-1)}")

    # 7. Bollinger Bands
    if bb_upper is not None and bb_lower is not None and bb_mid is not None:
        if close > bb_mid:
            score += 1
            sinais.append(f"Preco acima da SMA20 (BB mid) - vies comprador {_s(+1)}")
        else:
            score -= 1
            sinais.append(f"Preco abaixo da SMA20 (BB mid) - vies vendedor {_s(-1)}")

        if close < bb_lower:
            score += 1
            sinais.append(f"Preco abaixo da Banda Inferior - possivel reversao/oversold {_s(+1)}")
        elif close > bb_upper:
            score -= 1
            sinais.append(f"Preco acima da Banda Superior - possivel correcao/overbought {_s(-1)}")

    # 8. Volume (v4 novo) - confirma direcao do movimento
    if (last_vol is not None and avg_vol is not None and avg_vol > 0
            and last_close is not None and prev_close is not None):
        if last_vol > avg_vol * 1.3:
            price_rising = last_close > prev_close
            if price_rising:
                score += 1
                sinais.append(
                    f"Volume forte ({last_vol:.0f} vs media {avg_vol:.0f}) confirmando alta {_s(+1)}"
                )
            else:
                score -= 1
                sinais.append(
                    f"Volume forte ({last_vol:.0f} vs media {avg_vol:.0f}) confirmando baixa {_s(-1)}"
                )
        elif last_vol < avg_vol * 0.5:
            sinais.append(
                f"Volume fraco ({last_vol:.0f} vs media {avg_vol:.0f}) - movimento sem convicto [0]"
            )

    # 9. ADX - Forca da tendencia (filtro de range)
    adx_filtered = False
    if adx is not None:
        if adx < ADX_MIN_TREND:
            sinais.append(
                f"ADX fraco ({adx:.1f} < {ADX_MIN_TREND}) - mercado em range, sinal bloqueado [filtro]"
            )
            adx_filtered = True
        else:
            di_dir = ""
            if plus_di is not None and minus_di is not None:
                di_dir = " - +DI domina (alta)" if plus_di > minus_di else " - -DI domina (baixa)"
            sinais.append(f"ADX {adx:.1f} - tendencia presente{di_dir} [filtro]")

    # 10. Padroes de candle
    candle_patterns, candle_score = detect_candle_patterns(df)
    score += candle_score
    sinais.extend(candle_patterns)

    # 11. Multi-timeframe - confirmacao 1h
    htf_trend = None
    if htf_df is not None and len(htf_df) >= 26:
        try:
            htf_df2  = compute_indicators(htf_df)
            htf_last = htf_df2.iloc[-1]
            h_ema9   = _f(htf_last["ema9"])
            h_ema21  = _f(htf_last["ema21"])
            if h_ema9 is not None and h_ema21 is not None:
                if h_ema9 > h_ema21:
                    score    += 1
                    htf_trend = "alta"
                    sinais.append(f"1h: EMA9 > EMA21 - tendencia maior confirma alta {_s(+1)}")
                else:
                    score    -= 1
                    htf_trend = "baixa"
                    sinais.append(f"1h: EMA9 < EMA21 - tendencia maior aponta baixa {_s(-1)}")
        except Exception as exc:
            logger.debug("HTF compute error: %s", exc)

    # Suporte / Resistencia via S/R automatico (v4)
    sr_supports, sr_resistances = _find_sr_levels(df, n=50)

    # Fallback: min/max dos ultimos 20 candles
    recent = df.tail(20)
    suporte_raw     = _r(recent["Low"].min())
    resistencia_raw = _r(recent["High"].max())

    # Usa o nivel S/R mais representativo como suporte/resistencia exibido
    suporte     = _r(max(sr_supports))     if sr_supports     else suporte_raw
    resistencia = _r(min(sr_resistances))  if sr_resistances  else resistencia_raw

    # Decisao final
    effective_score = score
    if adx_filtered:
        effective_score = max(min(score, COMPRA_THRESHOLD - 1), VENDA_THRESHOLD + 1)

    if effective_score >= COMPRA_THRESHOLD:
        acao  = "COMPRA"
        forca = "FORTE" if effective_score >= COMPRA_THRESHOLD + 2 else "MODERADA"
        entrada = _r(close)
        stop    = _r(close - 1.5 * atr)

        # TP1: usa nivel de resistencia S/R mais proximo se disponivel
        sr_tp1 = _nearest_sr_tp(close, sr_resistances, "above", atr)
        if sr_tp1 is not None:
            tp1 = _r(sr_tp1)
            sinais.append(f"TP1 ajustado para resistencia S/R detectada em {tp1} [nivel de mercado]")
        else:
            tp1 = _r(close + 1.5 * atr)

        tp2 = _r(close + 3.0 * atr)
        tp3 = _r(close + 5.0 * atr)

    elif effective_score <= VENDA_THRESHOLD:
        acao  = "VENDA"
        forca = "FORTE" if effective_score <= VENDA_THRESHOLD - 2 else "MODERADA"
        entrada = _r(close)
        stop    = _r(close + 1.5 * atr)

        # TP1: usa nivel de suporte S/R mais proximo se disponivel
        sr_tp1 = _nearest_sr_tp(close, sr_supports, "below", atr)
        if sr_tp1 is not None:
            tp1 = _r(sr_tp1)
            sinais.append(f"TP1 ajustado para suporte S/R detectado em {tp1} [nivel de mercado]")
        else:
            tp1 = _r(close - 1.5 * atr)

        tp2 = _r(close - 3.0 * atr)
        tp3 = _r(close - 5.0 * atr)

    else:
        acao  = "NEUTRO"
        forca = "FRACA"
        entrada = _r(close)
        stop = tp1 = tp2 = tp3 = None

    # Timestamp do ultimo candle
    try:
        ts = df.index[-1]
        timestamp = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)
    except Exception:
        timestamp = None

    return {
        "acao":            acao,
        "score":           effective_score,
        "score_raw":       score,
        "forca":           forca,
        "adx_filtered":    adx_filtered,
        "htf_trend":       htf_trend,
        "entrada":         entrada,
        "stop":            stop,
        "tp1":             tp1,
        "tp2":             tp2,
        "tp3":             tp3,
        "rr":              2.0,
        "preco_atual":     _r(close),
        "rsi":             _r(rsi, 1) if rsi is not None else None,
        "atr":             _r(atr),
        "adx":             _r(adx, 1) if adx is not None else None,
        "plus_di":         _r(plus_di, 1) if plus_di is not None else None,
        "minus_di":        _r(minus_di, 1) if minus_di is not None else None,
        "vwap":            _r(vwap),
        "ema9":            _r(ema9),
        "ema21":           _r(ema21),
        "ema50":           _r(ema50),
        "ema200":          _r(ema200),
        "macd":            _r(macd, 4) if macd is not None else None,
        "macd_hist":       _r(macd_hist, 4) if macd_hist is not None else None,
        "bb_upper":        _r(bb_upper),
        "bb_lower":        _r(bb_lower),
        "suporte":         suporte,
        "resistencia":     resistencia,
        "sr_supports":     [_r(s) for s in sr_supports[-5:]],
        "sr_resistances":  [_r(r) for r in sr_resistances[:5]],
        "candle_patterns": candle_patterns,
        "sinais":          sinais,
        "timestamp":       timestamp,
        "candles":         len(df),
    }
