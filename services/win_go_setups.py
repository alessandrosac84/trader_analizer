"""
win_go_setups.py — sinais dos setups 🟢 GO (rodadas v3–v7 + mega B3 + v19).

v3: NR7_BREAK · INSIDE_BAR_BRK
v4: NR7_TREND_H4
v5: NR7_H4_MT · INSIDE_H4 · INSIDE_V18_H4 · INSIDE_AM
v6: NR5_H4 · NR5_H4_MT · INSIDE_1015_H4 · INSIDE_V13_H4 · INSIDE_VOL15_H4 · HL_H4
    (+ WDO_NR5_H4 no runtime)
v7 WDO: NR4_H4 · NR7_1014_H4 · PDH_H4 (+ NR5_H4 já live)
mega B3: WIN_PDH_1115 · WIN_PDH_H4 · WIN_IMP_CONT · WDO_HL_1014
yellow refine: WDO_OUT_1015_MT
v19 mega: WIN_NR5/NR4_1115 · INS_PM · VOLSPIKE_AM · IB_BRK_V15 · HL_MID
          WDO_OUT_GAP_DAY · OUT_POWER · IMP_1014_MT · ENG_1014 · GAPC_A60

    Réplica fiel do backtest (barra fechada, sem look-ahead).
"""
import numpy as np
import pandas as pd

_WIN_TICK = 5.0


def _sig(d, entry, sl, rr1=1.5):
    risk = abs(entry - sl)
    if risk <= 0:
        return None
    if d == "COMPRA":
        return {"dir": d, "entry": entry, "sl": float(sl), "tp": float(entry + rr1 * risk), "risk": risk}
    return {"dir": d, "entry": entry, "sl": float(sl), "tp": float(entry - rr1 * risk), "risk": risk}


def _vol8(df, i):
    return float(df.Volume.iloc[i - 8:i].mean()) if i >= 8 else float(df.Volume.iloc[:i].mean())


def _h4_bias_up(df):
    """True = H4 close > EMA50; None = indisponível."""
    if df is None or len(df) < 220:
        return None
    h4 = df[["Close"]].resample("4h").last().dropna()
    if len(h4) < 55:
        return None
    h4 = h4.copy()
    h4["ema50"] = h4.Close.ewm(span=50, adjust=False).mean()
    h4_close = h4.Close.reindex(df.index, method="ffill")
    h4_ema = h4["ema50"].reindex(df.index, method="ffill")
    hc = float(h4_close.iloc[-1]) if np.isfinite(h4_close.iloc[-1]) else None
    he = float(h4_ema.iloc[-1]) if np.isfinite(h4_ema.iloc[-1]) else None
    if hc is None or he is None:
        return None
    return hc > he


def _h4_ok(df, d):
    up = _h4_bias_up(df)
    if up is None:
        return False
    return (d == "COMPRA" and up) or (d == "VENDA" and not up)


def _nr_break(df, n=7, vol_mult=1.3, hh0=10.0, hh1=15.0):
    if df is None or len(df) < n + 2:
        return None
    i = len(df) - 1
    r = df.iloc[i]
    hh = r.name.hour + r.name.minute / 60.0
    if not (hh0 <= hh < hh1):
        return None
    prev_ranges = (df.High.iloc[i - n:i] - df.Low.iloc[i - n:i])
    if len(prev_ranges) < n or float(prev_ranges.min()) <= 0:
        return None
    if float(prev_ranges.iloc[-1]) > float(prev_ranges.min()) + 1e-12:
        return None
    p = df.iloc[i - 1]
    vol8 = _vol8(df, i)
    if vol8 <= 0 or float(r.Volume) < vol_mult * vol8:
        return None
    if float(r.Close) > float(p.High):
        return _sig("COMPRA", float(r.Close), float(p.Low), 1.5)
    if float(r.Close) < float(p.Low):
        return _sig("VENDA", float(r.Close), float(p.High), 1.5)
    return None


def nr7_break_signal(df: pd.DataFrame):
    return _nr_break(df, n=7, vol_mult=1.3, hh0=10.0, hh1=15.0)


def nr7_trend_h4_signal(df: pd.DataFrame):
    base = nr7_break_signal(df)
    if not base or not _h4_ok(df, base["dir"]):
        return None
    return base


def nr7_h4_mt_signal(df: pd.DataFrame):
    if df is None or len(df) < 20:
        return None
    if int(df.index[-1].dayofweek) not in (0, 1, 2, 3):
        return None
    return nr7_trend_h4_signal(df)


def nr5_h4_signal(df: pd.DataFrame):
    """NR5_H4 — GO v6."""
    base = _nr_break(df, n=5, vol_mult=1.3, hh0=10.0, hh1=14.5)
    if not base or not _h4_ok(df, base["dir"]):
        return None
    return base


def nr5_h4_mt_signal(df: pd.DataFrame):
    """NR5_H4_MT — GO v6."""
    if df is None or len(df) < 20:
        return None
    if int(df.index[-1].dayofweek) not in (0, 1, 2, 3):
        return None
    return nr5_h4_signal(df)


def nr4_h4_signal(df: pd.DataFrame):
    """NR4_H4 — GO v7 WDO (também testado no WIN v6)."""
    base = _nr_break(df, n=4, vol_mult=1.3, hh0=10.0, hh1=14.0)
    if not base or not _h4_ok(df, base["dir"]):
        return None
    return base


def nr7_1014_h4_signal(df: pd.DataFrame):
    """NR7_1014_H4 — NR7 janela 10–14 + H4 · GO v7 WDO."""
    base = _nr_break(df, n=7, vol_mult=1.3, hh0=10.0, hh1=14.0)
    if not base or not _h4_ok(df, base["dir"]):
        return None
    return base


def _prev_day_hl(df: pd.DataFrame):
    if df is None or len(df) < 30:
        return None, None
    days = df.index.normalize()
    today = days[-1]
    prev = df.loc[days < today]
    if prev.empty:
        return None, None
    last_day = prev.index.normalize()[-1]
    day_bars = prev.loc[prev.index.normalize() == last_day]
    if day_bars.empty:
        return None, None
    return float(day_bars.High.max()), float(day_bars.Low.min())


def _atr14(df: pd.DataFrame) -> float:
    if df is None or len(df) < 16:
        return 0.0
    hi, lo, cl = df.High, df.Low, df.Close
    prev_c = cl.shift(1)
    tr = pd.concat([(hi - lo), (hi - prev_c).abs(), (lo - prev_c).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / 14, adjust=False).mean()
    v = float(atr.iloc[-1])
    return v if np.isfinite(v) and v > 0 else 0.0


def pdh_h4_signal(df: pd.DataFrame):
    """PDH_H4 — rompe PDH/PDL vol≥1,2× + H4 · 11–15h · GO v7 WDO."""
    if df is None or len(df) < 30:
        return None
    i = len(df) - 1
    r, p = df.iloc[i], df.iloc[i - 1]
    hh = r.name.hour + r.name.minute / 60.0
    if not (11.0 <= hh < 15.0):
        return None
    pdh, pdl = _prev_day_hl(df)
    if pdh is None or pdl is None:
        return None
    vol8 = _vol8(df, i)
    if vol8 <= 0 or float(r.Volume) < 1.2 * vol8:
        return None
    atr = _atr14(df) or 1.0
    base = None
    if float(r.Close) > pdh and float(p.Close) <= pdh:
        base = _sig("COMPRA", float(r.Close), pdh - 0.3 * atr, 1.5)
    elif float(r.Close) < pdl and float(p.Close) >= pdl:
        base = _sig("VENDA", float(r.Close), pdl + 0.3 * atr, 1.5)
    if not base or not _h4_ok(df, base["dir"]):
        return None
    return base


def _pdh_mega(df: pd.DataFrame, vol_mult=1.2, hh0=10.0, hh1=16.0):
    """Espelho mega `_pdh_h`: PDH/PDL dia ant. (fallback 96 barras) · SL barra ant. · RR 1,5."""
    if df is None or len(df) < 30:
        return None
    i = len(df) - 1
    r, p = df.iloc[i], df.iloc[i - 1]
    hh = r.name.hour + r.name.minute / 60.0
    if not (hh0 <= hh < hh1):
        return None
    vol8 = _vol8(df, i)
    if vol8 <= 0 or float(r.Volume) < vol_mult * vol8:
        return None
    pdh, pdl = _prev_day_hl(df)
    if pdh is None or pdl is None:
        win = df.iloc[max(0, i - 96):i]
        if len(win) < 20:
            return None
        pdh, pdl = float(win.High.max()), float(win.Low.min())
    base = None
    if float(r.Close) > pdh and float(p.Close) <= pdh:
        base = _sig("COMPRA", float(r.Close), float(p.Low), 1.5)
    elif float(r.Close) < pdl and float(p.Close) >= pdl:
        base = _sig("VENDA", float(r.Close), float(p.High), 1.5)
    if not base or not _h4_ok(df, base["dir"]):
        return None
    return base


def win_pdh_1115_signal(df: pd.DataFrame):
    """WIN_PDH_1115 — mega GO · 11–15h · TP≤800 pts."""
    return _apply_tp_cap(_pdh_mega(df, 1.2, 11.0, 15.0), _WIN_PDH_TP_CAP)


def win_pdh_h4_signal(df: pd.DataFrame):
    """WIN_PDH_H4 — mega GO · 10–16h · TP≤800 pts."""
    return _apply_tp_cap(_pdh_mega(df, 1.2, 10.0, 16.0), _WIN_PDH_TP_CAP)


def win_imp_cont_signal(df: pd.DataFrame):
    """WIN_IMP_CONT — mega `_impulse_h(2.0, 1.5, 9, 18, tp=2)` · 24H era duplicata no pregão."""
    if df is None or len(df) < 40:
        return None
    i = len(df) - 1
    r, p = df.iloc[i], df.iloc[i - 1]
    hh = r.name.hour + r.name.minute / 60.0
    if not (9.0 <= hh < 18.0):
        return None
    hi, lo, cl = df.High, df.Low, df.Close
    tr = pd.concat([(hi - lo), (hi - cl.shift()).abs(), (lo - cl.shift()).abs()], axis=1).max(axis=1)
    atr = float(tr.ewm(alpha=1 / 14, adjust=False).mean().iloc[i - 1])
    if not np.isfinite(atr) or atr <= 0:
        return None
    if float(p.High - p.Low) < 2.0 * atr:
        return None
    vol8p = float(df.Volume.iloc[i - 9:i - 1].mean()) if i >= 9 else float(df.Volume.iloc[:i - 1].mean())
    if vol8p <= 0 or float(p.Volume) < 1.5 * vol8p:
        return None
    down = float(p.Close) < float(p.Open)
    up = float(p.Close) > float(p.Open)
    if not (down or up):
        return None
    d = "VENDA" if down else "COMPRA"
    if not _h4_ok(df, d):
        return None
    if d == "VENDA" and float(r.Close) >= float(p.Low):
        return None
    if d == "COMPRA" and float(r.Close) <= float(p.High):
        return None
    vol8 = _vol8(df, i)
    if vol8 <= 0 or float(r.Volume) < 1.1 * vol8:
        return None
    sl = float(p.High) if d == "VENDA" else float(p.Low)
    return _sig(d, float(r.Close), sl, 2.0)


# Caps live WDO (WinGo): SL estrutural (engolfo/HL/gap) → TP 1.5R pode
# ir a ~30–40 pts (ex. ENG live SL 28.5 → TP 39). Cap TP 20 = WDO_HL;
# SL≤25 corta stop absurdo. Só risco live (runtime + sinais WDO_*);
# régua GO / backtests v19 intactos.
_WDO_TP_CAP = 20.0
_WDO_SL_CAP = 25.0
_WDO_HL_TP_CAP = _WDO_TP_CAP  # alias compat

# Cap TP WIN_PDH_*: SL = low/high barra ant. pode ir a ~3–4×ATR M15
# (ex. live COMPRA 178770 SL 1170 → TP 1.5R ≈1920). Cap 800 ≈2,5×ATR med
# (~312) preserva GO PDH_H4 (+0,184R OOS+0,135) e corta cauda gorda.
_WIN_PDH_TP_CAP = 800.0

# Caps live WIN (WinGo): IMP_CONT usa TP=2R com SL = extremo da barra impulso
# (≥2×ATR) → TP pode ir a ~2000+ pts (ex. live COMPRA entry 177400 SL 840
# → TP 2R ≈1680–2100). Cap TP 800 = mesmo WIN_PDH (~2,5×ATR M15 scalp);
# SL≤500 corta stop estrutural absurdo sem apertar demais vs ATR~312.
# Só risco live (runtime); régua GO / backtests intactos.
_WIN_TP_CAP = 800.0
_WIN_SL_CAP = 400.0  # 07/08: IMP_CONT SL~370–855; teto 400 corta cauda (antes 500)


def _apply_tp_cap(sig: dict, cap: float) -> dict:
    """Limita |entry−tp| a `cap` pts (mantém SL/risco; só encurta alvo)."""
    if not sig or cap is None or cap <= 0:
        return sig
    entry = float(sig["entry"])
    tp = float(sig["tp"])
    if abs(tp - entry) <= cap + 1e-9:
        return sig
    out = dict(sig)
    out["tp"] = entry + cap if sig["dir"] == "COMPRA" else entry - cap
    return out


def apply_wdo_live_caps(sig: dict, entry: float = None) -> dict:
    """Caps live SL/TP WDO em pts. `entry` = preço fill (ask/bid) se já conhecido."""
    if not sig:
        return sig
    out = dict(sig)
    e = float(entry if entry is not None else out["entry"])
    d = out["dir"]
    sl, tp = float(out["sl"]), float(out["tp"])
    if _WDO_SL_CAP > 0 and abs(sl - e) > _WDO_SL_CAP + 1e-9:
        sl = e - _WDO_SL_CAP if d == "COMPRA" else e + _WDO_SL_CAP
    if _WDO_TP_CAP > 0 and abs(tp - e) > _WDO_TP_CAP + 1e-9:
        tp = e + _WDO_TP_CAP if d == "COMPRA" else e - _WDO_TP_CAP
    out["entry"] = e
    out["sl"] = sl
    out["tp"] = tp
    out["risk"] = abs(e - sl)
    return out


def apply_win_live_caps(sig: dict, entry: float = None) -> dict:
    """Caps live SL/TP WIN em pts. `entry` = preço fill (ask/bid) se já conhecido."""
    if not sig:
        return sig
    out = dict(sig)
    e = float(entry if entry is not None else out["entry"])
    d = out["dir"]
    sl, tp = float(out["sl"]), float(out["tp"])
    if _WIN_SL_CAP > 0 and abs(sl - e) > _WIN_SL_CAP + 1e-9:
        sl = e - _WIN_SL_CAP if d == "COMPRA" else e + _WIN_SL_CAP
    if _WIN_TP_CAP > 0 and abs(tp - e) > _WIN_TP_CAP + 1e-9:
        tp = e + _WIN_TP_CAP if d == "COMPRA" else e - _WIN_TP_CAP
    out["entry"] = e
    out["sl"] = sl
    out["tp"] = tp
    out["risk"] = abs(e - sl)
    return out


def wdo_hl_1014_signal(df: pd.DataFrame):
    """WDO_HL_1014 — mega `_hl_h(1.3, 10, 14)` · 1 HL/LH + rompe + H4 · TP≤20 pts."""
    if df is None or len(df) < 5:
        return None
    i = len(df) - 1
    if i < 2:
        return None
    r, p1, p2 = df.iloc[i], df.iloc[i - 1], df.iloc[i - 2]
    hh = r.name.hour + r.name.minute / 60.0
    if not (10.0 <= hh < 14.0):
        return None
    vol8 = _vol8(df, i)
    if vol8 <= 0 or float(r.Volume) < 1.3 * vol8:
        return None
    if float(p1.Low) > float(p2.Low) and float(r.Close) > float(p1.High):
        base = _sig("COMPRA", float(r.Close), float(p1.Low), 1.5)
    elif float(p1.High) < float(p2.High) and float(r.Close) < float(p1.Low):
        base = _sig("VENDA", float(r.Close), float(p1.High), 1.5)
    else:
        return None
    if not base or not _h4_ok(df, base["dir"]):
        return None
    return _apply_tp_cap(base, _WDO_TP_CAP)


def wdo_out_1015_mt_signal(df: pd.DataFrame):
    """WDO_OUT_1015_MT — yellow GO · outside bar vol≥1.3× + H4 · 10–15h · seg–qui."""
    if df is None or len(df) < 5:
        return None
    i = len(df) - 1
    if i < 1:
        return None
    r, p = df.iloc[i], df.iloc[i - 1]
    if int(r.name.dayofweek) not in (0, 1, 2, 3):
        return None
    hh = r.name.hour + r.name.minute / 60.0
    if not (10.0 <= hh < 15.0):
        return None
    if not (float(r.High) > float(p.High) and float(r.Low) < float(p.Low)):
        return None
    vol8 = _vol8(df, i)
    if vol8 <= 0 or float(r.Volume) < 1.3 * vol8:
        return None
    if float(r.Close) > float(r.Open) and float(r.Close) > float(p.High):
        base = _sig("COMPRA", float(r.Close), float(r.Low), 1.5)
    elif float(r.Close) < float(r.Open) and float(r.Close) < float(p.Low):
        base = _sig("VENDA", float(r.Close), float(r.High), 1.5)
    else:
        return None
    if not base or not _h4_ok(df, base["dir"]):
        return None
    return base


def inside_bar_break_signal(df: pd.DataFrame):
    """INSIDE_BAR_BRK — 10:00–14:00 · vol≥1,2×."""
    if df is None or len(df) < 5:
        return None
    i = len(df) - 1
    r, p, pp = df.iloc[i], df.iloc[i - 1], df.iloc[i - 2]
    hh = r.name.hour + r.name.minute / 60.0
    if not (10.0 <= hh < 14.0):
        return None
    if not (float(p.High) <= float(pp.High) and float(p.Low) >= float(pp.Low)):
        return None
    vol8 = _vol8(df, i)
    if vol8 <= 0:
        return None
    if float(r.Close) > float(p.High) and float(r.Volume) >= 1.2 * vol8:
        return _sig("COMPRA", float(r.Close), float(p.Low), 1.5)
    if float(r.Close) < float(p.Low) and float(r.Volume) >= 1.2 * vol8:
        return _sig("VENDA", float(r.Close), float(p.High), 1.5)
    return None


def _inside_vol_h4(df, vol_mult=1.2, hh0=10.0, hh1=14.0):
    if df is None or len(df) < 5:
        return None
    i = len(df) - 1
    r, p, pp = df.iloc[i], df.iloc[i - 1], df.iloc[i - 2]
    hh = r.name.hour + r.name.minute / 60.0
    if not (hh0 <= hh < hh1):
        return None
    if not (float(p.High) <= float(pp.High) and float(p.Low) >= float(pp.Low)):
        return None
    vol8 = _vol8(df, i)
    if vol8 <= 0 or float(r.Volume) < vol_mult * vol8:
        return None
    base = None
    if float(r.Close) > float(p.High):
        base = _sig("COMPRA", float(r.Close), float(p.Low), 1.5)
    elif float(r.Close) < float(p.Low):
        base = _sig("VENDA", float(r.Close), float(p.High), 1.5)
    if not base or not _h4_ok(df, base["dir"]):
        return None
    return base


def inside_h4_signal(df: pd.DataFrame):
    return _inside_vol_h4(df, 1.2, 10.0, 14.0)


def inside_v18_h4_signal(df: pd.DataFrame):
    return _inside_vol_h4(df, 1.8, 10.0, 14.0)


def inside_vol15_h4_signal(df: pd.DataFrame):
    """INSIDE_VOL15_H4 — GO v6."""
    return _inside_vol_h4(df, 1.5, 10.0, 14.0)


def inside_v13_h4_signal(df: pd.DataFrame):
    """INSIDE_V13_H4 — GO v6."""
    return _inside_vol_h4(df, 1.3, 10.0, 14.0)


def inside_1015_h4_signal(df: pd.DataFrame):
    """INSIDE_1015_H4 — 10–15h + H4 · GO v6."""
    return _inside_vol_h4(df, 1.2, 10.0, 15.0)


def inside_am_signal(df: pd.DataFrame):
    if df is None or len(df) < 5:
        return None
    hh = df.index[-1].hour + df.index[-1].minute / 60.0
    if not (10.0 <= hh < 12.0):
        return None
    return inside_bar_break_signal(df)


def hl_h4_signal(df: pd.DataFrame):
    """HL_H4 — 3 higher-lows (ou lower-highs) + rompe + H4 · GO v6."""
    if df is None or len(df) < 6:
        return None
    i = len(df) - 1
    r = df.iloc[i]
    hh = r.name.hour + r.name.minute / 60.0
    if not (10.5 <= hh < 14.0):
        return None
    lows = df.Low.iloc[i - 4:i].values
    highs = df.High.iloc[i - 4:i].values
    vol8 = _vol8(df, i)
    if vol8 <= 0 or float(r.Volume) < 1.25 * vol8:
        return None
    base = None
    if lows[1] > lows[0] and lows[2] > lows[1] and lows[3] > lows[2]:
        level = float(np.max(highs))
        if float(r.Close) > level and float(df.iloc[i - 1].Close) <= level:
            base = _sig("COMPRA", float(r.Close), float(lows[-1]) - _WIN_TICK, 1.5)
    if highs[1] < highs[0] and highs[2] < highs[1] and highs[3] < highs[2]:
        level = float(np.min(lows))
        if float(r.Close) < level and float(df.iloc[i - 1].Close) >= level:
            base = _sig("VENDA", float(r.Close), float(highs[-1]) + _WIN_TICK, 1.5)
    if not base or not _h4_ok(df, base["dir"]):
        return None
    return base


# ── v19 mega WIN/WDO (novos paths — magics 20260745–49 / 20260758+) ─────────

def _gap_atr_now(df: pd.DataFrame, i: int) -> float:
    """gap_atr = (day_open - prev_close) / ATR14 na barra i."""
    if i < 20:
        return 0.0
    days = df.index.normalize()
    today = days[i]
    day_bars = df.loc[days == today]
    if day_bars.empty:
        return 0.0
    day_open = float(day_bars.iloc[0].Open)
    prev = df.loc[days < today]
    if prev.empty:
        return 0.0
    prev_close = float(prev.iloc[-1].Close)
    atr = _atr14(df.iloc[: i + 1])
    if atr <= 0:
        return 0.0
    return (day_open - prev_close) / atr


def _ib_hl(df: pd.DataFrame, i: int):
    """Initial Balance 09:00–10:00 do dia da barra i."""
    days = df.index.normalize()
    today = days[i]
    day = df.loc[days == today]
    if day.empty:
        return None, None
    ib = day[(day.index.hour + day.index.minute / 60.0 >= 9.0)
             & (day.index.hour + day.index.minute / 60.0 < 10.0)]
    if len(ib) < 2:
        return None, None
    return float(ib.High.max()), float(ib.Low.min())


def win_nr5_1115_signal(df: pd.DataFrame):
    """WIN_NR5_1115 — NR5 vol≥1,3× + H4 · 11–15h · GO v19 (+0,428 R)."""
    base = _nr_break(df, n=5, vol_mult=1.3, hh0=11.0, hh1=15.0)
    if not base or not _h4_ok(df, base["dir"]):
        return None
    return base


def win_nr4_1115_signal(df: pd.DataFrame):
    """WIN_NR4_1115 — NR4 vol≥1,3× + H4 · 11–15h · GO v19 (+0,372 R)."""
    base = _nr_break(df, n=4, vol_mult=1.3, hh0=11.0, hh1=15.0)
    if not base or not _h4_ok(df, base["dir"]):
        return None
    return base


def win_ins_pm_signal(df: pd.DataFrame):
    """WIN_INS_PM — inside vol≥1,3× + H4 · 13–16h · GO v19 (+0,340 R)."""
    return _inside_vol_h4(df, 1.3, 13.0, 16.0)


def win_volspike_am_signal(df: pd.DataFrame):
    """WIN_VOLSPIKE_AM — volspike ≥2× + range≥1,5ATR + H4 · 10–12:30 · GO v19."""
    if df is None or len(df) < 20:
        return None
    i = len(df) - 1
    r, p = df.iloc[i], df.iloc[i - 1]
    hh = r.name.hour + r.name.minute / 60.0
    if not (10.0 <= hh < 12.5):
        return None
    vol8p = float(df.Volume.iloc[i - 9:i - 1].mean()) if i >= 9 else float(df.Volume.iloc[:i - 1].mean())
    if vol8p <= 0 or float(p.Volume) < 2.0 * vol8p:
        return None
    atr = _atr14(df.iloc[:i])
    if atr <= 0 or float(p.High - p.Low) < 1.5 * atr:
        return None
    d = "COMPRA" if float(p.Close) > float(p.Open) else "VENDA"
    if d == "COMPRA" and float(r.Close) <= float(p.High):
        return None
    if d == "VENDA" and float(r.Close) >= float(p.Low):
        return None
    if not _h4_ok(df, d):
        return None
    sl = float(p.Low) if d == "COMPRA" else float(p.High)
    return _sig(d, float(r.Close), sl, 2.0)


def win_ib_brk_v15_signal(df: pd.DataFrame):
    """WIN_IB_BRK_V15 — rompe IB 09–10 + vol≥1,5× + H4 · 10–13h · GO v19."""
    if df is None or len(df) < 30:
        return None
    i = len(df) - 1
    r, p = df.iloc[i], df.iloc[i - 1]
    hh = r.name.hour + r.name.minute / 60.0
    if not (10.0 <= hh < 13.0):
        return None
    ib_hi, ib_lo = _ib_hl(df, i)
    if ib_hi is None or ib_lo is None or ib_hi <= ib_lo:
        return None
    vol8 = _vol8(df, i)
    if vol8 <= 0 or float(r.Volume) < 1.5 * vol8:
        return None
    if float(r.Close) > ib_hi and float(p.Close) <= ib_hi:
        base = _sig("COMPRA", float(r.Close), ib_lo, 1.5)
    elif float(r.Close) < ib_lo and float(p.Close) >= ib_lo:
        base = _sig("VENDA", float(r.Close), ib_hi, 1.5)
    else:
        return None
    if not base or not _h4_ok(df, base["dir"]):
        return None
    return base


def win_hl_mid_signal(df: pd.DataFrame):
    """WIN_HL_MID — 1 HL/LH + rompe + vol≥1,3× + H4 · 11–14h · GO v19 (+0,286 R)."""
    if df is None or len(df) < 5:
        return None
    i = len(df) - 1
    r, p1, p2 = df.iloc[i], df.iloc[i - 1], df.iloc[i - 2]
    hh = r.name.hour + r.name.minute / 60.0
    if not (11.0 <= hh < 14.0):
        return None
    vol8 = _vol8(df, i)
    if vol8 <= 0 or float(r.Volume) < 1.3 * vol8:
        return None
    if float(p1.Low) > float(p2.Low) and float(r.Close) > float(p1.High):
        base = _sig("COMPRA", float(r.Close), float(p1.Low), 1.5)
    elif float(p1.High) < float(p2.High) and float(r.Close) < float(p1.Low):
        base = _sig("VENDA", float(r.Close), float(p1.High), 1.5)
    else:
        return None
    if not base or not _h4_ok(df, base["dir"]):
        return None
    return base


def _outside_h4(df, vol_mult=1.3, hh0=10.0, hh1=15.0, mt=False):
    if df is None or len(df) < 5:
        return None
    i = len(df) - 1
    r, p = df.iloc[i], df.iloc[i - 1]
    if mt and int(r.name.dayofweek) not in (0, 1, 2, 3):
        return None
    hh = r.name.hour + r.name.minute / 60.0
    if not (hh0 <= hh < hh1):
        return None
    if not (float(r.High) > float(p.High) and float(r.Low) < float(p.Low)):
        return None
    vol8 = _vol8(df, i)
    if vol8 <= 0 or float(r.Volume) < vol_mult * vol8:
        return None
    if float(r.Close) > float(r.Open) and float(r.Close) > float(p.High):
        base = _sig("COMPRA", float(r.Close), float(r.Low), 1.5)
    elif float(r.Close) < float(r.Open) and float(r.Close) < float(p.Low):
        base = _sig("VENDA", float(r.Close), float(r.High), 1.5)
    else:
        return None
    if not base or not _h4_ok(df, base["dir"]):
        return None
    return base


def wdo_out_gap_day_signal(df: pd.DataFrame):
    """WDO_OUT_GAP_DAY — outside + gap_atr≥0,25 a favor · 10–16:30 · GO v19 (+0,414 R)."""
    base = _outside_h4(df, 1.3, 10.0, 16.5, mt=False)
    if not base:
        return None
    i = len(df) - 1
    g = _gap_atr_now(df, i)
    if abs(g) < 0.25:
        return None
    if g > 0 and base["dir"] != "COMPRA":
        return None
    if g < 0 and base["dir"] != "VENDA":
        return None
    return base


def wdo_out_power_signal(df: pd.DataFrame):
    """WDO_OUT_POWER — outside vol≥1,3× + H4 · 14:30–16:30 · GO v19 (+0,343 R)."""
    return _outside_h4(df, 1.3, 14.5, 16.5, mt=False)


def wdo_imp_1014_mt_signal(df: pd.DataFrame):
    """WDO_IMP_1014_MT — impulso cont. 10–14 · seg–qui · GO v19 (célula IMP aberta)."""
    if df is None or len(df) < 40:
        return None
    i = len(df) - 1
    r, p = df.iloc[i], df.iloc[i - 1]
    if int(r.name.dayofweek) not in (0, 1, 2, 3):
        return None
    hh = r.name.hour + r.name.minute / 60.0
    if not (10.0 <= hh < 14.0):
        return None
    atr = _atr14(df.iloc[:i])
    if atr <= 0 or float(p.High - p.Low) < 1.8 * atr:
        return None
    vol8p = float(df.Volume.iloc[i - 9:i - 1].mean()) if i >= 9 else float(df.Volume.iloc[:i - 1].mean())
    if vol8p <= 0 or float(p.Volume) < 1.5 * vol8p:
        return None
    down = float(p.Close) < float(p.Open)
    up = float(p.Close) > float(p.Open)
    if not (down or up):
        return None
    d = "VENDA" if down else "COMPRA"
    if not _h4_ok(df, d):
        return None
    if d == "VENDA" and float(r.Close) >= float(p.Low):
        return None
    if d == "COMPRA" and float(r.Close) <= float(p.High):
        return None
    vol8 = _vol8(df, i)
    if vol8 <= 0 or float(r.Volume) < 1.1 * vol8:
        return None
    sl = float(p.High) if d == "VENDA" else float(p.Low)
    return _sig(d, float(r.Close), sl, 2.0)


def wdo_eng_1014_signal(df: pd.DataFrame):
    """WDO_ENG_1014 — engolfo + vol≥1,3× + H4 · 10–14h · GO v19 (+0,206 R) · TP≤20 live."""
    if df is None or len(df) < 5:
        return None
    i = len(df) - 1
    r, p = df.iloc[i], df.iloc[i - 1]
    hh = r.name.hour + r.name.minute / 60.0
    if not (10.0 <= hh < 14.0):
        return None
    vol8 = _vol8(df, i)
    if vol8 <= 0 or float(r.Volume) < 1.3 * vol8:
        return None
    bull = (float(r.Close) > float(r.Open) and float(p.Close) < float(p.Open)
            and float(r.Close) >= float(p.Open) and float(r.Open) <= float(p.Close)
            and float(r.Close) > float(p.High))
    bear = (float(r.Close) < float(r.Open) and float(p.Close) > float(p.Open)
            and float(r.Close) <= float(p.Open) and float(r.Open) >= float(p.Close)
            and float(r.Close) < float(p.Low))
    if bull:
        base = _sig("COMPRA", float(r.Close), float(min(p.Low, r.Low)), 1.5)
    elif bear:
        base = _sig("VENDA", float(r.Close), float(max(p.High, r.High)), 1.5)
    else:
        return None
    if not base or not _h4_ok(df, base["dir"]):
        return None
    # Live: TP≤20 (mesmo cap WDO_HL); SL estrutural mantido no sinal —
    # SL cap absurdo só no runtime (apply_wdo_live_caps).
    return _apply_tp_cap(base, _WDO_TP_CAP)


def wdo_gapc_a60_signal(df: pd.DataFrame):
    """WDO_GAPC_A60 — continuação gap ≥0,60 ATR · 10–12h + H4 · GO v19 (+0,212 R)."""
    if df is None or len(df) < 30:
        return None
    i = len(df) - 1
    r, p = df.iloc[i], df.iloc[i - 1]
    hh = r.name.hour + r.name.minute / 60.0
    if not (10.0 <= hh < 12.0):
        return None
    g = _gap_atr_now(df, i)
    if abs(g) < 0.60:
        return None
    vol8 = _vol8(df, i)
    if vol8 <= 0 or float(r.Volume) < 1.2 * vol8:
        return None
    days = df.index.normalize()
    today = days[i]
    day_open = float(df.loc[days == today].iloc[0].Open)
    if g >= 0.60 and float(r.Close) > float(p.High) and float(r.Close) > day_open:
        base = _sig("COMPRA", float(r.Close), float(min(p.Low, day_open)), 1.5)
    elif g <= -0.60 and float(r.Close) < float(p.Low) and float(r.Close) < day_open:
        base = _sig("VENDA", float(r.Close), float(max(p.High, day_open)), 1.5)
    else:
        return None
    if not base or not _h4_ok(df, base["dir"]):
        return None
    return base
