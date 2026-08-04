"""
feature_engine.py — ETAPA 1: extrai ~110 features por candle (vetorizado).

REGRA DE OURO: nenhuma feature olha o futuro. Tudo é calculado com dados até o
fechamento do próprio candle. (Labels olham o futuro — mas ficam no
label_generator, nunca aqui.)

Grupos: OHLC/range/corpo, volume, ATR/volatilidade (Bollinger/Donchian/Keltner),
tendência (EMAs: distância/inclinação/cruzamento), VWAP, momentum (RSI, MACD,
ROC, CCI, Stoch), estrutura (swings HH/HL/LH/LL, BOS, inside/outside, sequências,
compressão/expansão), gap do dia, distâncias a referências, rompimentos,
padrões de candle, tempo (hora/dia/sessão) e contexto multi-TF (H1/H4 por resample).
"""
import numpy as np
import pandas as pd

from edge_discovery.config import SESSIONS_24H


def _ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def _slope(s, n=5):
    """Inclinação relativa: variação da média em n barras / preço."""
    return (s - s.shift(n)) / (s.shift(n).abs() + 1e-12)


def _rsi(c, n=14):
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1/n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False).mean()
    return 100 - 100/(1 + up/dn.replace(0, 1e-12))


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """df: OHLCV indexado por datetime. Retorna DataFrame de features (mesmo índice)."""
    o, h, l, c, v = df.Open, df.High, df.Low, df.Close, df.Volume
    F = pd.DataFrame(index=df.index)

    # ── range / corpo / pavios ──
    rng = (h - l).replace(0, np.nan)
    body = (c - o)
    F["ret_1"] = c.pct_change()
    F["ret_5"] = c.pct_change(5)
    F["ret_20"] = c.pct_change(20)
    F["body_pct"] = body.abs() / rng
    F["dir_up"] = (c > o).astype(int)
    F["wick_up_pct"] = (h - np.maximum(c, o)) / rng
    F["wick_dn_pct"] = (np.minimum(c, o) - l) / rng
    F["close_pos"] = (c - l) / rng                      # onde fechou dentro do range
    tr = pd.concat([(h-l), (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/14, adjust=False).mean()
    F["atr_pct"] = atr / c
    F["atr_pctl"] = atr.rolling(500, min_periods=100).rank(pct=True)
    F["rng_vs_atr"] = rng / (atr + 1e-12)
    F["tr_expansion"] = tr / (tr.rolling(10).mean() + 1e-12)

    # ── volume ──
    F["vol_rel10"] = v / (v.rolling(10).mean() + 1e-12)
    F["vol_rel50"] = v / (v.rolling(50).mean() + 1e-12)
    F["vol_pctl"] = v.rolling(500, min_periods=100).rank(pct=True)
    day = pd.Series(df.index.date, index=df.index)
    F["vol_day_cum_rel"] = v.groupby(day).cumsum() / (v.rolling(500, min_periods=50).mean() + 1e-12)

    # ── tendência (EMAs) ──
    for n in (9, 20, 50, 200):
        e = _ema(c, n)
        F[f"ema{n}_dist"] = (c - e) / (atr + 1e-12)      # distância em ATRs
        F[f"ema{n}_slope"] = _slope(e, 5)
    F["ema9_gt_21"] = (_ema(c, 9) > _ema(c, 21)).astype(int)
    F["ema20_gt_50"] = (_ema(c, 20) > _ema(c, 50)).astype(int)
    F["ema50_gt_200"] = (_ema(c, 50) > _ema(c, 200)).astype(int)
    cross = (_ema(c, 9) > _ema(c, 21)).astype(int).diff()
    F["ema_cross_up"] = (cross == 1).astype(int)
    F["ema_cross_dn"] = (cross == -1).astype(int)

    # ── VWAP diário ──
    tp = (h + l + c) / 3
    vwap = (tp*v).groupby(day).cumsum() / v.groupby(day).cumsum().replace(0, np.nan)
    F["vwap_dist"] = (c - vwap) / (atr + 1e-12)
    F["vwap_above"] = (c > vwap).astype(int)
    F["vwap_slope"] = _slope(vwap, 5)
    vw_cross = (c > vwap).astype(int).diff()
    F["vwap_cross_up"] = (vw_cross == 1).astype(int)
    F["vwap_cross_dn"] = (vw_cross == -1).astype(int)

    # ── momentum ──
    F["rsi"] = _rsi(c)
    F["rsi_slope"] = F["rsi"].diff(3)
    macd = _ema(c, 12) - _ema(c, 26)
    sig = _ema(macd, 9)
    F["macd_hist"] = (macd - sig) / (atr + 1e-12)
    F["macd_hist_up"] = ((macd - sig) > 0).astype(int)
    F["roc_10"] = c.pct_change(10)
    tp_cci = (h + l + c) / 3
    F["cci"] = (tp_cci - tp_cci.rolling(20).mean()) / (0.015*tp_cci.rolling(20).std() + 1e-12)
    ll5, hh5 = l.rolling(5).min(), h.rolling(5).max()
    F["stoch_k"] = 100*(c - ll5)/((hh5 - ll5).replace(0, np.nan))

    # ── volatilidade (larguras) ──
    sma20, std20 = c.rolling(20).mean(), c.rolling(20).std()
    F["bb_width"] = (4*std20) / (sma20 + 1e-12)
    F["bb_width_pctl"] = F["bb_width"].rolling(500, min_periods=100).rank(pct=True)
    F["bb_pos"] = (c - sma20) / (2*std20 + 1e-12)        # -1..+1 dentro das bandas
    F["donch_width"] = (h.rolling(20).max() - l.rolling(20).min()) / (atr + 1e-12)
    F["keltner_width"] = (4*atr) / (sma20 + 1e-12)
    F["squeeze"] = (F["bb_width_pctl"] < 0.10).astype(int)

    # ── estrutura ──
    hh20, ll20 = h.rolling(20).max(), l.rolling(20).min()
    F["break_hh20"] = (c > hh20.shift(1)).astype(int)
    F["break_ll20"] = (c < ll20.shift(1)).astype(int)
    F["dist_hh20"] = (hh20 - c) / (atr + 1e-12)
    F["dist_ll20"] = (c - ll20) / (atr + 1e-12)
    F["inside_bar"] = ((h < h.shift(1)) & (l > l.shift(1))).astype(int)
    F["outside_bar"] = ((h > h.shift(1)) & (l < l.shift(1))).astype(int)
    up_bar = (c > o).astype(int)
    grp = (up_bar != up_bar.shift()).cumsum()
    F["seq_dir"] = up_bar.groupby(grp).cumcount() + 1     # sequência de mesma cor
    F["seq_dir"] = np.where(up_bar == 1, F["seq_dir"], -F["seq_dir"])
    # swing points confirmados (pivô de 2 barras — usa shift p/ não vazar futuro)
    sw_hi = ((h.shift(2) > h.shift(3)) & (h.shift(2) > h.shift(1))).astype(int)
    sw_lo = ((l.shift(2) < l.shift(3)) & (l.shift(2) < l.shift(1))).astype(int)
    last_sw_hi = h.shift(2).where(sw_hi == 1).ffill()
    last_sw_lo = l.shift(2).where(sw_lo == 1).ffill()
    prev_sw_hi = h.shift(2).where(sw_hi == 1).shift(1).ffill()
    prev_sw_lo = l.shift(2).where(sw_lo == 1).shift(1).ffill()
    F["hh_struct"] = (last_sw_hi > prev_sw_hi).astype(int)     # higher-high
    F["ll_struct"] = (last_sw_lo < prev_sw_lo).astype(int)     # lower-low
    F["bos_up"] = ((c > last_sw_hi) & (c.shift(1) <= last_sw_hi)).astype(int)
    F["bos_dn"] = ((c < last_sw_lo) & (c.shift(1) >= last_sw_lo)).astype(int)
    F["compress"] = (rng.rolling(5).mean() / (rng.rolling(20).mean() + 1e-12))

    # ── gap do dia / posição no dia ──
    day_open = c.groupby(day).transform("first")
    prev_close = c.groupby(day).last().shift(1)
    F["gap_pct"] = (day_open - day.map(prev_close)) / (day.map(prev_close) + 1e-12) * 100
    F["gap_filled"] = (((F["gap_pct"] > 0) & (l <= day.map(prev_close))) |
                       ((F["gap_pct"] < 0) & (h >= day.map(prev_close)))).astype(int)
    day_hi = h.groupby(day).cummax(); day_lo = l.groupby(day).cummin()
    F["day_range_pos"] = (c - day_lo) / ((day_hi - day_lo).replace(0, np.nan))
    F["dist_day_hi"] = (day_hi - c) / (atr + 1e-12)
    F["dist_day_lo"] = (c - day_lo) / (atr + 1e-12)
    F["above_day_open"] = (c > day_open).astype(int)
    F["bar_of_day"] = df.groupby(day.values).cumcount()

    # ── padrões de candle ──
    F["hammer"] = ((F["wick_dn_pct"] >= 0.6) & (F["body_pct"] <= 0.35)).astype(int)
    F["star"] = ((F["wick_up_pct"] >= 0.6) & (F["body_pct"] <= 0.35)).astype(int)
    F["doji"] = (F["body_pct"] <= 0.1).astype(int)
    F["marubozu"] = (F["body_pct"] >= 0.85).astype(int)
    eng_up = (c > o) & (c.shift(1) < o.shift(1)) & (c >= o.shift(1)) & (o <= c.shift(1))
    eng_dn = (c < o) & (c.shift(1) > o.shift(1)) & (c <= o.shift(1)) & (o >= c.shift(1))
    F["engulf_up"] = eng_up.astype(int)
    F["engulf_dn"] = eng_dn.astype(int)

    # ── tempo / sessão ──
    F["hour"] = df.index.hour
    F["dow"] = df.index.dayofweek
    F["month"] = df.index.month
    for name, (h0, h1_) in SESSIONS_24H.items():
        F[f"sess_{name}"] = ((F["hour"] >= h0) & (F["hour"] < h1_)).astype(int)

    # ── contexto multi-TF (H1 e H4 por resample — só barras FECHADAS via shift) ──
    for tfn, tfr in (("h1", "1h"), ("h4", "4h")):
        r_ = df[["Close"]].resample(tfr).last().dropna()
        e20 = _ema(r_.Close, 20); e50 = _ema(r_.Close, 50)
        trend = (e20 > e50).astype(int).shift(1)          # shift: só TF fechado
        F[f"{tfn}_trend_up"] = trend.reindex(df.index, method="ffill")
        F[f"{tfn}_rsi"] = _rsi(r_.Close).shift(1).reindex(df.index, method="ffill")

    return F.replace([np.inf, -np.inf], np.nan)
