"""
services/crypto_orderflow.py — setup ORDER_FLOW ao vivo (Monitor Crypto).

Único setup NOVO validado OUT-OF-SAMPLE (validar_orderflow.py, 20/07/2026):
  • XAUUSD em M5  — completo +0,179 R (PF 1,31), OOS +0,104 R (PF 1,19), cons 67%
  • ETHUSD em M15 — completo +0,260 R (PF 1,51), OOS +0,294 R (PF 1,58), cons 67%
BTC não segurou o OOS → fora.

Lógica (delta de fluxo + área de valor por VWAP): entra A FAVOR do delta de
volume acumulado quando o preço rompe a VAH/VAL com confirmação de volume 1,5×.
Avaliado na ÚLTIMA barra FECHADA (anti-repaint). SL estruturado, TP1 2R, TP2 3R.

Roda como CAMINHO ADICIONAL no auto-check do Monitor Crypto, no timeframe
validado de cada ativo — independente do timeframe que o painel exibe. NÃO passa
pelo filtro macro/ADX (foi validado sem ele; a confirmação de fluxo é a própria
validação). Não altera score/impulso/pullback.
"""
import numpy as np
import pandas as pd

# Ativo → timeframe (minutos) em que o setup passou no out-of-sample
ORDERFLOW_TF = {"XAUUSD": 5, "ETHUSD": 15}

# LONDON_HANDOFF (rodada 2 do Kimi): rompimento do range asiático estreito com
# delta confirmando, na transição Ásia→Londres. Validado OOS no XAU (M15):
#   completo +0,208 R (PF 1,56, cons 62%) · OOS +0,329 R (PF 2,07, cons 82%).
# LONDON_HANDOFF legado (Kimi r2): OOS forte, mas parâmetros mais frouxos e SEM
# limite 1/dia. Os LH_* GO (v4–v6) cobrem o mesmo conceito com régua mais apertada.
# Mantido OFF para não roubar o slot dos GOs. Reative só se quiser o legado de volta.
LONDON_HANDOFF_SYMBOLS = set()
SESS_ASIA   = (1, 9)       # range asiático 01:00–09:00 (hora do servidor IC Markets)
SESS_LONDON = (9, 11)      # janela de handoff/rompimento 09:00–11:00 servidor
HANDOFF_DELTA_THR = 0.15
HANDOFF_TP_MULT   = 1.75   # TP = 1,75× o tamanho do range asiático
# Parâmetros do legado (referência): asia≤0,60 ATR · delta≥0,15 — ver funções abaixo.

DELTA_THR = 0.30           # limiar do delta normalizado (robusto p/ 0,25–0,35)
RR_TP1, RR_TP2 = 2.0, 3.0
TICK_SIZE = {"BTCUSD": 0.1, "ETHUSD": 0.01, "XAUUSD": 0.01}


def orderflow_tf(symbol: str):
    """Retorna o timeframe (min) do ORDER_FLOW p/ o ativo, ou None se não habilitado."""
    return ORDERFLOW_TF.get((symbol or "").upper().strip())


def london_handoff_enabled(symbol: str) -> bool:
    return (symbol or "").upper().strip() in LONDON_HANDOFF_SYMBOLS


def _add_of(df: pd.DataFrame) -> pd.DataFrame:
    c, h, l, o, v = df.Close, df.High, df.Low, df.Open, df.Volume
    tr = pd.concat([(h - l), (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    df["atr"] = tr.ewm(alpha=1 / 14, adjust=False).mean()
    df["vol10"] = v.rolling(10).mean()
    rng = (h - l).replace(0, np.nan)
    delta = ((c - l) / rng - 0.5) * 2 * v
    df["delta_sum10"] = delta.rolling(10).sum()
    df["vol_sum10"] = v.rolling(10).sum()
    vwl = (c * v).rolling(20).sum() / v.rolling(20).sum().replace(0, np.nan)
    dev = ((v * (c - vwl) ** 2).rolling(20).sum() / v.rolling(20).sum().replace(0, np.nan)) ** 0.5
    df["va_vwl"], df["va_dev"] = vwl, dev
    return df


def _of_signal(df: pd.DataFrame, i: int, ts: float, thr: float = DELTA_THR):
    if i < 20:
        return None
    r = df.iloc[i]
    vt = r.vol_sum10
    if not vt or vt <= 0 or pd.isna(vt):
        return None
    dn = r.delta_sum10 / vt
    vwl, dev = r.va_vwl, r.va_dev
    if pd.isna(vwl) or pd.isna(dev):
        return None
    vah, val = vwl + dev, vwl - dev
    volok = r.Volume >= (r.vol10 or 0) * 1.5
    if dn > thr and r.Close > vah and r.Close > r.Open and volok:
        sl = min(val, r.Low - 2 * ts); ds = r.Close - sl
        if ds > 0:
            return ("COMPRA", float(sl), float(r.Close + RR_TP1 * ds), float(r.Close + RR_TP2 * ds))
    if dn < -thr and r.Close < val and r.Close < r.Open and volok:
        sl = max(vah, r.High + 2 * ts); ds = sl - r.Close
        if ds > 0:
            return ("VENDA", float(sl), float(r.Close - RR_TP1 * ds), float(r.Close - RR_TP2 * ds))
    return None


def signal(candles, symbol: str):
    """
    Avalia o ORDER_FLOW na última barra FECHADA.
    candles: list[dict] {time, open, high, low, close, volume} (do get_candles).
    Retorna (acao, sl, tp1, tp2) ou None.
    """
    if not candles or len(candles) < 60:
        return None
    df = pd.DataFrame(candles).rename(columns={
        "open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"})
    for col in ("Open", "High", "Low", "Close", "Volume"):
        if col not in df.columns:
            return None
    df = df.iloc[:-1]                       # descarta candle em formação (anti-repaint)
    if len(df) < 60:
        return None
    df = _add_of(df.reset_index(drop=True))
    ts = TICK_SIZE.get((symbol or "").upper().strip(), 0.01)
    return _of_signal(df, len(df) - 1, ts)


def london_handoff_signal(candles, symbol: str):
    """
    LONDON_HANDOFF (M15): rompimento do range asiático ESTREITO com delta
    confirmando, na janela 09:00–11:00 (hora do servidor). Avaliado na última
    barra FECHADA. Retorna (acao, sl, tp1, tp2=None) ou None.
    Horário vem do timestamp do candle (já em hora do servidor da corretora).
    """
    if not candles or len(candles) < 60:
        return None
    df = pd.DataFrame(candles).rename(columns={
        "open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"})
    if "time" not in df.columns:
        return None
    for col in ("Open", "High", "Low", "Close", "Volume"):
        if col not in df.columns:
            return None
    df["dt"] = pd.to_datetime(df["time"], unit="s")
    df = df.set_index("dt").iloc[:-1]           # descarta candle em formação
    if len(df) < 60:
        return None
    df = _add_of(df)
    r = df.iloc[-1]
    hh = df.index[-1].hour + df.index[-1].minute / 60.0
    if not (SESS_LONDON[0] <= hh < SESS_LONDON[1]):
        return None
    if pd.isna(r.atr) or r.atr <= 0:
        return None
    today = df.index[-1].date()
    hrs = df.index.hour + df.index.minute / 60.0
    is_today = np.array([d.date() == today for d in df.index])
    asia = df[is_today & (hrs >= SESS_ASIA[0]) & (hrs < SESS_ASIA[1])]
    if asia.empty:
        return None
    asia_hi, asia_lo = float(asia.High.max()), float(asia.Low.min())
    asia_rng = asia_hi - asia_lo
    if asia_rng <= 0 or asia_rng > 0.60 * (r.atr * 16):   # range estreito = acumulação
        return None
    vt = r.vol_sum10
    dn = (r.delta_sum10 / vt) if (vt and vt > 0) else 0.0
    prev = df.iloc[-2]
    if r.Close > asia_hi and prev.Close <= asia_hi and dn > HANDOFF_DELTA_THR:
        entry = float(r.Close)
        if entry > asia_lo:
            return ("COMPRA", asia_lo, entry + HANDOFF_TP_MULT * asia_rng, None)
    if r.Close < asia_lo and prev.Close >= asia_lo and dn < -HANDOFF_DELTA_THR:
        entry = float(r.Close)
        if asia_hi > entry:
            return ("VENDA", asia_hi, entry - HANDOFF_TP_MULT * asia_rng, None)
    return None
