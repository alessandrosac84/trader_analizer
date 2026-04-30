"""
technical_analysis.py — Motor de análise técnica v3.

Indicadores:
  - EMA 9 / 21 / 50  (tendência + crossover + slope)
  - VWAP diário       (referência institucional intraday)
  - RSI 14            (zona de extremo + direção / momentum)
  - MACD 12/26/9      (histograma > 0 = momentum positivo + crossovers)
  - Bollinger Bands 20 (posição vs SMA20 + extremos)
  - ATR 14            (stop e alvos)
  - ADX 14            (filtro de range — ADX < 20 bloqueia sinal)
  - Padrões de candle  (hammer, shooting star, engolfo, marubozu, doji)
  - Confirmação multi-timeframe (1h confirma 15m)

Scoring:
  Cada indicador contribui pontos com anotação explícita no campo "sinais".
  COMPRA  : score ≥ +4
  VENDA   : score ≤ -4
  NEUTRO  : -3 a +3
  ADX < 20: mercado em range → sinal bloqueado (retorna NEUTRO forçado)

Bugfixes v3 vs v2:
  - MACD usa macd_hist > 0 (não macd > 0) — mais responsivo e consistente
  - MACD sinais sem redundância (crossover OU posição, não os dois)
  - HTF check usa `is not None` (não truthiness)
  - RSI: adiciona pontuação por direção (rising/falling, 5 candles)
  - Bollinger: pontuação por posição vs SMA20 (não só extremos)
  - EMA slope: pontuação extra se EMA9 acelerando na direção do sinal
  - Sinais anotados com contribuição de score [+N] / [-N] para transparência
"""
import logging
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Indicadores — funções puras
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
    """VWAP diário — reseta a cada dia. Retorna NaN se volume ausente."""
    tp  = (df["High"] + df["Low"] + df["Close"]) / 3
    vol = df["Volume"]

    # Índices (^BVSP etc.) costumam ter volume 0 — evitar divisão por zero
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


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Calcula todos os indicadores sobre um DataFrame OHLCV."""
    df = df.copy()
    c  = df["Close"]

    # EMAs
    df["ema9"]  = c.ewm(span=9,  adjust=False).mean()
    df["ema21"] = c.ewm(span=21, adjust=False).mean()
    df["ema50"] = c.ewm(span=50, adjust=False).mean()

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

    # Bollinger Bands (20, 2σ)
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
# Reconhecimento de padrões de candle
# ---------------------------------------------------------------------------

def detect_candle_patterns(df: pd.DataFrame) -> tuple[list[str], int]:
    """
    Analisa os últimos 2 candles e retorna (lista_de_padroes, score_parcial).
    Score: ±2 engolfo, ±1 hammer / shooting star / marubozu.
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

    # Doji — indecisão, sem pontuação (informativo)
    if body_ratio < 0.08:
        patterns.append("⚪ Doji — indecisão / aguardar confirmação")

    # Hammer (bullish) — sombra inferior ≥ 2× corpo, vela de alta
    if body_ratio < 0.4 and lower_wick >= 2 * body and upper_wick <= body * 0.5 and c > o:
        patterns.append("🟢 Hammer — potencial reversão de alta [+1]")
        score += 1

    # Shooting Star (bearish) — sombra superior ≥ 2× corpo, vela de baixa
    if body_ratio < 0.4 and upper_wick >= 2 * body and lower_wick <= body * 0.5 and c < o:
        patterns.append("🔴 Shooting Star — potencial reversão de baixa [-1]")
        score -= 1

    # Engolfo de Alta
    if (pc > po and c > o and o <= pc and c >= po and body > prev_body * 0.8):
        patterns.append("🟢 Engolfo de Alta — reversão bullish forte [+2]")
        score += 2

    # Engolfo de Baixa
    if (pc < po and c < o and o >= pc and c <= po and body > prev_body * 0.8):
        patterns.append("🔴 Engolfo de Baixa — reversão bearish forte [-2]")
        score -= 2

    # Marubozu de Alta
    if c > o and body_ratio >= 0.80:
        patterns.append("🟢 Marubozu de Alta — força compradora dominante [+1]")
        score += 1

    # Marubozu de Baixa
    if o > c and body_ratio >= 0.80:
        patterns.append("🔴 Marubozu de Baixa — força vendedora dominante [-1]")
        score -= 1

    return patterns, score


# ---------------------------------------------------------------------------
# Helpers numéricos
# ---------------------------------------------------------------------------

def _f(v) -> float | None:
    try:
        fv = float(v)
        return None if fv != fv else fv  # NaN check
    except Exception:
        return None


def _r(v, decimals: int = 3) -> float | None:
    fv = _f(v)
    return round(fv, decimals) if fv is not None else None


def _s(points: int) -> str:
    """Formata contribuição de score para exibição: [+2] ou [-1]."""
    return f"[{'+' if points >= 0 else ''}{points}]"


# ---------------------------------------------------------------------------
# Gerador de sinal principal
# ---------------------------------------------------------------------------

COMPRA_THRESHOLD = 4
VENDA_THRESHOLD  = -4
ADX_MIN_TREND    = 20   # abaixo disso → range → bloqueia sinal


def generate_signal(
    df: pd.DataFrame,
    htf_df: pd.DataFrame | None = None,
) -> dict | None:
    """
    Gera sinal de trading completo.

    Parâmetros
    ----------
    df     : DataFrame OHLCV do timeframe operacional (ex.: 15m).
    htf_df : DataFrame OHLCV do timeframe maior (ex.: 1h) — confirmação opcional.

    Retorna dict com acao, score, forca, entrada, stop, tp1/2/3,
    indicadores, padrões e sinais anotados. None se dados insuficientes.
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

    # ── Valores dos indicadores ───────────────────────────────────────────
    rsi       = _f(last["rsi"])
    atr       = _f(last["atr"]) or (close * 0.005)
    ema9      = _f(last["ema9"])
    ema21     = _f(last["ema21"])
    ema50     = _f(last["ema50"])
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

    prev_ema9     = _f(prev["ema9"])
    prev_ema21    = _f(prev["ema21"])
    prev_macd     = _f(prev["macd"])
    prev_macd_sig = _f(prev["macd_signal"])
    prev_macd_hist = _f(prev["macd_hist"])

    # RSI 5 candles atrás (para direção)
    rsi_5_ago = _f(df.iloc[-6]["rsi"]) if len(df) >= 6 else None

    # EMA9 5 candles atrás (para slope)
    ema9_5_ago = _f(df.iloc[-6]["ema9"]) if len(df) >= 6 else None

    score:  int       = 0
    sinais: list[str] = []

    # ── 1. EMA 9/21 — Tendência de curto prazo ───────────────────────────
    if ema9 is not None and ema21 is not None:
        if ema9 > ema21:
            score += 1
            sinais.append(f"EMA9 > EMA21 — tendência de alta {_s(+1)}")
        else:
            score -= 1
            sinais.append(f"EMA9 < EMA21 — tendência de baixa {_s(-1)}")

        # Crossover (evento pontual — bônus)
        if prev_ema9 is not None and prev_ema21 is not None:
            if ema9 > ema21 and prev_ema9 <= prev_ema21:
                score += 2
                sinais.append(f"🟢 Cruzamento de alta: EMA9 cruzou acima da EMA21 {_s(+2)}")
            elif ema9 < ema21 and prev_ema9 >= prev_ema21:
                score -= 2
                sinais.append(f"🔴 Cruzamento de baixa: EMA9 cruzou abaixo da EMA21 {_s(-2)}")

        # EMA9 slope — está acelerando na direção?
        if ema9_5_ago is not None:
            ema9_rising = ema9 > ema9_5_ago
            if ema9_rising:
                score += 1
                sinais.append(f"EMA9 ascendente (slope positivo) {_s(+1)}")
            else:
                score -= 1
                sinais.append(f"EMA9 descendente (slope negativo) {_s(-1)}")

    # ── 2. EMA 50 — Filtro de tendência médio prazo ──────────────────────
    if ema50 is not None:
        if close > ema50:
            score += 1
            sinais.append(f"Preço acima da EMA50 — contexto de alta {_s(+1)}")
        else:
            score -= 1
            sinais.append(f"Preço abaixo da EMA50 — contexto de baixa {_s(-1)}")

    # ── 3. VWAP — Referência institucional intraday ──────────────────────
    if vwap is not None:
        if close > vwap:
            score += 1
            sinais.append(f"Preço acima do VWAP ({_r(vwap)}) — pressão compradora {_s(+1)}")
        else:
            score -= 1
            sinais.append(f"Preço abaixo do VWAP ({_r(vwap)}) — pressão vendedora {_s(-1)}")

    # ── 4. RSI 14 — Momentum e extremos ──────────────────────────────────
    if rsi is not None:
        # Zona de extremo
        if rsi < 30:
            score += 2
            sinais.append(f"🟢 RSI em sobrevenda ({rsi:.1f}) — reversão de alta provável {_s(+2)}")
        elif rsi < 40:
            score += 1
            sinais.append(f"RSI em zona fraca ({rsi:.1f}) {_s(+1)}")
        elif rsi > 70:
            score -= 2
            sinais.append(f"🔴 RSI em sobrecompra ({rsi:.1f}) — reversão de baixa provável {_s(-2)}")
        elif rsi > 60:
            score -= 1
            sinais.append(f"RSI em zona forte ({rsi:.1f}) {_s(-1)}")
        else:
            sinais.append(f"RSI neutro ({rsi:.1f}) [0]")

        # Direção do RSI (momentum): rising = bullish, falling = bearish
        if rsi_5_ago is not None:
            diff = rsi - rsi_5_ago
            if diff > 3:           # subiu mais de 3 pontos nos últimos 5 candles
                score += 1
                sinais.append(f"RSI em alta ({rsi:.1f} ↑ vs {rsi_5_ago:.1f}) — momentum positivo {_s(+1)}")
            elif diff < -3:        # caiu mais de 3 pontos
                score -= 1
                sinais.append(f"RSI em queda ({rsi:.1f} ↓ vs {rsi_5_ago:.1f}) — momentum negativo {_s(-1)}")

    # ── 5. MACD 12/26/9 — Momentum ───────────────────────────────────────
    # Usa histograma (MACD - sinal): > 0 = momentum positivo, < 0 = negativo
    # FIX v3: era `macd > 0` (linha do zero) — agora usa histograma que é mais
    # responsivo e consistente com o que o label mostrava.
    if macd_hist is not None:
        crossover_happened = False

        # Crossover (evento pontual — bônus)
        if prev_macd is not None and prev_macd_sig is not None and prev_macd_hist is not None:
            if macd_hist > 0 and prev_macd_hist <= 0:
                score += 2
                sinais.append(f"🟢 MACD cruzou acima da linha de sinal — reversão bullish {_s(+2)}")
                crossover_happened = True
            elif macd_hist < 0 and prev_macd_hist >= 0:
                score -= 2
                sinais.append(f"🔴 MACD cruzou abaixo da linha de sinal — reversão bearish {_s(-2)}")
                crossover_happened = True

        # Posição do histograma (só exibe se não houve crossover já reportado)
        if macd_hist > 0:
            score += 1
            if not crossover_happened:
                sinais.append(f"MACD histograma positivo — momentum de alta {_s(+1)}")
        else:
            score -= 1
            if not crossover_happened:
                sinais.append(f"MACD histograma negativo — momentum de baixa {_s(-1)}")

    # ── 6. Bollinger Bands ────────────────────────────────────────────────
    # FIX v3: adiciona posição vs SMA20 (banda do meio) como sinal de tendência.
    # Antes só pontuava nos extremos (acima/abaixo das bandas).
    if bb_upper is not None and bb_lower is not None and bb_mid is not None:
        # Posição vs banda do meio (tendência)
        if close > bb_mid:
            score += 1
            sinais.append(f"Preço acima da SMA20 (BB mid) — viés comprador {_s(+1)}")
        else:
            score -= 1
            sinais.append(f"Preço abaixo da SMA20 (BB mid) — viés vendedor {_s(-1)}")

        # Extremos (possível reversão ou continuação em breakout)
        if close < bb_lower:
            score += 1
            sinais.append(f"Preço abaixo da Banda Inferior — possível reversão ou oversold {_s(+1)}")
        elif close > bb_upper:
            score -= 1
            sinais.append(f"Preço acima da Banda Superior — possível correção ou overbought {_s(-1)}")

    # ── 7. ADX — Força da tendência (filtro de range) ────────────────────
    adx_filtered = False
    if adx is not None:
        if adx < ADX_MIN_TREND:
            sinais.append(
                f"⚠️ ADX fraco ({adx:.1f} < {ADX_MIN_TREND}) — mercado em range, sinal bloqueado [filtro]"
            )
            adx_filtered = True
        else:
            di_dir = ""
            if plus_di is not None and minus_di is not None:
                di_dir = " — +DI domina (alta)" if plus_di > minus_di else " — -DI domina (baixa)"
            sinais.append(f"ADX {adx:.1f} — tendência presente{di_dir} [filtro]")

    # ── 8. Padrões de candle ──────────────────────────────────────────────
    candle_patterns, candle_score = detect_candle_patterns(df)
    score += candle_score
    sinais.extend(candle_patterns)

    # ── 9. Multi-timeframe — confirmação 1h ──────────────────────────────
    htf_trend = None
    if htf_df is not None and len(htf_df) >= 26:
        try:
            htf_df2  = compute_indicators(htf_df)
            htf_last = htf_df2.iloc[-1]
            h_ema9   = _f(htf_last["ema9"])
            h_ema21  = _f(htf_last["ema21"])
            # FIX v3: usa `is not None` em vez de truthiness
            if h_ema9 is not None and h_ema21 is not None:
                if h_ema9 > h_ema21:
                    score    += 1
                    htf_trend = "alta"
                    sinais.append(f"✅ 1h: EMA9 > EMA21 — tendência maior confirma alta {_s(+1)}")
                else:
                    score    -= 1
                    htf_trend = "baixa"
                    sinais.append(f"⚠️ 1h: EMA9 < EMA21 — tendência maior aponta baixa {_s(-1)}")
        except Exception as exc:
            logger.debug("HTF compute error: %s", exc)

    # ── Suporte / Resistência recente (20 candles) ────────────────────────
    recent      = df.tail(20)
    suporte     = _r(recent["Low"].min())
    resistencia = _r(recent["High"].max())

    # ── Decisão final ─────────────────────────────────────────────────────
    effective_score = score
    if adx_filtered:
        # Range: limita score para não atingir threshold → NEUTRO forçado
        effective_score = max(min(score, COMPRA_THRESHOLD - 1), VENDA_THRESHOLD + 1)

    if effective_score >= COMPRA_THRESHOLD:
        acao  = "COMPRA"
        forca = "FORTE" if effective_score >= COMPRA_THRESHOLD + 2 else "MODERADA"
        entrada = _r(close)
        stop    = _r(close - 2 * atr)
        tp1     = _r(close + 2 * atr)
        tp2     = _r(close + 4 * atr)
        tp3     = _r(close + 6 * atr)
    elif effective_score <= VENDA_THRESHOLD:
        acao  = "VENDA"
        forca = "FORTE" if effective_score <= VENDA_THRESHOLD - 2 else "MODERADA"
        entrada = _r(close)
        stop    = _r(close + 2 * atr)
        tp1     = _r(close - 2 * atr)
        tp2     = _r(close - 4 * atr)
        tp3     = _r(close - 6 * atr)
    else:
        acao  = "NEUTRO"
        forca = "FRACA"
        entrada = _r(close)
        stop = tp1 = tp2 = tp3 = None

    # Timestamp do último candle
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
        "macd":            _r(macd, 4) if macd is not None else None,
        "macd_hist":       _r(macd_hist, 4) if macd_hist is not None else None,
        "bb_upper":        _r(bb_upper),
        "bb_lower":        _r(bb_lower),
        "suporte":         suporte,
        "resistencia":     resistencia,
        "candle_patterns": candle_patterns,
        "sinais":          sinais,
        "timestamp":       timestamp,
        "candles":         len(df),
    }
