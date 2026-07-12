"""
services/crypto_analysis.py — Motor de score do MONITOR CRYPTO (24h).

⚠️ MÓDULO NOVO E INDEPENDENTE. Espelha a filosofia do Monitor MT5 (score por
confluência de indicadores, limiar ±score_min, filtro de ADX, alvo em R:R), mas
em arquivo próprio e SEM premissas da B3 (sem VWAP diário / horário de pregão).
Não importa nem altera o motor do Monitor MT5.

Entrada: lista de candles (dicts com open/high/low/close/volume).
Saída: dict de sinal compatível com o painel (acao, score, entrada, stop, tp1, ...).
"""
import logging

logger = logging.getLogger(__name__)

COMPRA_THRESHOLD = 7
EXHAUSTION_CEILING = 12   # |score| >= isso → NEUTRO (movimento esticado)


def _ema(series, n):
    return series.ewm(span=n, adjust=False).mean()


def _rsi(close, n=14):
    delta = close.diff()
    up = delta.clip(lower=0).rolling(n).mean()
    down = (-delta.clip(upper=0)).rolling(n).mean()
    rs = up / down.replace(0, 1e-9)
    return 100 - (100 / (1 + rs))


def _macd_hist(close):
    macd = _ema(close, 12) - _ema(close, 26)
    signal = _ema(macd, 9)
    return macd - signal


def _atr(df, n=14):
    import pandas as pd
    h, l, c = df["High"], df["Low"], df["Close"]
    tr = pd.concat([(h - l), (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()


def _adx(df, n=14):
    import pandas as pd
    h, l, c = df["High"], df["Low"], df["Close"]
    up = h.diff()
    dn = -l.diff()
    plus_dm = ((up > dn) & (up > 0)) * up
    minus_dm = ((dn > up) & (dn > 0)) * dn
    tr = pd.concat([(h - l), (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(n).mean().replace(0, 1e-9)
    plus_di = 100 * (plus_dm.rolling(n).mean() / atr)
    minus_di = 100 * (minus_dm.rolling(n).mean() / atr)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, 1e-9)
    return dx.rolling(n).mean()


def analyze(candles: list, cfg: dict) -> dict:
    """Calcula o sinal a partir dos candles. cfg = crypto_config.cfg_for(symbol)."""
    try:
        import pandas as pd
        if not candles or len(candles) < 60:
            return {"acao": "NEUTRO", "score": 0, "forca": "FRACA", "confluences": [],
                    "erro": "poucos candles"}
        df = pd.DataFrame(candles)
        df = df.rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close"})
        close = df["Close"]
        price = float(close.iloc[-1])

        ema9, ema21 = _ema(close, 9), _ema(close, 21)
        ema50, ema200 = _ema(close, 50), _ema(close, 200)
        rsi = _rsi(close)
        hist = _macd_hist(close)
        bb_mid = close.rolling(20).mean()
        bb_std = close.rolling(20).std()
        atr = _atr(df)
        adx = _adx(df)

        e9, e21 = float(ema9.iloc[-1]), float(ema21.iloc[-1])
        e50 = float(ema50.iloc[-1]); e200 = float(ema200.iloc[-1])
        rsi_v = float(rsi.iloc[-1]); rsi_prev = float(rsi.iloc[-2])
        hist_v = float(hist.iloc[-1])
        bbm = float(bb_mid.iloc[-1]) if not pd.isna(bb_mid.iloc[-1]) else price
        atr_v = float(atr.iloc[-1]) if not pd.isna(atr.iloc[-1]) else 0.0
        adx_v = float(adx.iloc[-1]) if not pd.isna(adx.iloc[-1]) else 0.0

        score = 0
        conf = []
        # EMA9 x EMA21 (tendência curta)
        if e9 > e21: score += 2; conf.append("EMA9 > EMA21 — tendência de alta")
        elif e9 < e21: score -= 2; conf.append("EMA9 < EMA21 — tendência de baixa")
        # EMA50 (contexto)
        if price > e50: score += 1; conf.append("Preço acima da EMA50")
        else: score -= 1; conf.append("Preço abaixo da EMA50")
        # EMA200 (longo prazo)
        if price > e200: score += 2; conf.append("Preço acima da EMA200")
        else: score -= 2; conf.append("Preço abaixo da EMA200")
        # RSI
        if rsi_v > 55 and rsi_v > rsi_prev: score += 1; conf.append(f"RSI {rsi_v:.0f} subindo")
        elif rsi_v < 45 and rsi_v < rsi_prev: score -= 1; conf.append(f"RSI {rsi_v:.0f} caindo")
        # MACD histograma
        if hist_v > 0: score += 1; conf.append("MACD histograma positivo")
        elif hist_v < 0: score -= 1; conf.append("MACD histograma negativo")
        # Bollinger (viés)
        if price > bbm: score += 1
        else: score -= 1

        htf = "alta" if e50 > e200 else ("baixa" if e50 < e200 else "lateral")

        eff = max(min(score, EXHAUSTION_CEILING - 1), -(EXHAUSTION_CEILING - 1))
        acao = "NEUTRO"
        forca = "FRACA"
        entrada = price
        stop = tp1 = None
        smin = int(cfg.get("score_min", 7))
        adx_min = float(cfg.get("adx_min", 20))
        sl_atr = float(cfg.get("sl_atr", 1.5))
        tp_rr = float(cfg.get("tp_rr", 1.5))

        if abs(score) >= EXHAUSTION_CEILING:
            conf.append(f"Score {abs(score)} ≥ {EXHAUSTION_CEILING} — possível exaustão (NEUTRO)")
        elif adx_v < adx_min:
            conf.append(f"ADX {adx_v:.0f} < {adx_min:.0f} — sem tendência (NEUTRO)")
        elif eff >= smin and htf == "alta":
            acao = "COMPRA"
            forca = "FORTE" if eff >= smin + 2 else "MODERADA"
            stop = price - sl_atr * atr_v if atr_v else None
            tp1 = price + tp_rr * (price - stop) if stop else None
        elif eff <= -smin and htf == "baixa":
            acao = "VENDA"
            forca = "FORTE" if eff <= -(smin + 2) else "MODERADA"
            stop = price + sl_atr * atr_v if atr_v else None
            tp1 = price - tp_rr * (stop - price) if stop else None
        else:
            conf.append("Sem confluência suficiente para o gatilho (NEUTRO)")

        rnd = 2 if price < 100 else (1 if price < 5000 else 0)
        return {
            "acao": acao, "score": eff, "raw_score": score, "forca": forca,
            "entrada": round(entrada, 5), "stop": round(stop, 5) if stop else None,
            "tp1": round(tp1, 5) if tp1 else None,
            "rsi": round(rsi_v, 1), "adx": round(adx_v, 1), "atr": round(atr_v, 5),
            "htf_trend": htf, "confluences": conf[:8],
        }
    except Exception as exc:
        logger.warning("crypto analyze erro: %s", exc)
        return {"acao": "NEUTRO", "score": 0, "forca": "FRACA", "confluences": [], "erro": str(exc)}
