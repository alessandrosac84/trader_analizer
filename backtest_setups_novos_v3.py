"""
backtest_setups_novos_v3.py — RODADA 3: máximo de setups (refinos + criativos).

Régua idêntica (custos, OOS 70/30, bimestre, GO). NÃO altera motores ao vivo.
Só entra em produção o que passar 🟢 GO de verdade.

Foco:
  · Refinos dos quase-GO (OVN_TO_PDC, OVN_ATR, ROUND_FADE, LONDON_HANDOFF_V2)
  · Dezenas de variantes paramétricas + setups criativos WIN/WDO/XAU/ETH

Uso:
  python rodar_backtest_setups_novos_v3.py
"""
import argparse
import os
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

import numpy as np
import pandas as pd

from backtest_kimi_real import fetch as _b3_fetch, add_indicators, asset_key, roundtrip_cost_pts
from backtest_setups_novos import (
    stats, consistency, verdict, _line, _sig,
    reversal_up, reversal_down, _parts, _of_core,
    OOS_FRAC, _WIN_TICK,
)
from backtest_setups_novos_v2 import (
    add_extra_v2, _simulate, _of, _tick,
    SESS_LONDON_TRADE, SESS_NY, SESS_NY_EARLY,
)

# ════════════════════════════════════════════════════════════════════════════
# FACTORIES — geram dezenas de variantes a partir de um molde
# ════════════════════════════════════════════════════════════════════════════

def make_ovn_pdc(gap_min=0.25, hh0=9.25, hh1=9.75, need_ext=False, dow_max=4, name=""):
    """Fade de gap com alvo PDC (família do melhor amarelo n=37)."""
    def fn(df, i, sym):
        r = df.iloc[i]
        if not (hh0 <= r.hh <= hh1) or r.dow > dow_max:
            return None
        if pd.isna(r.gap_pct) or pd.isna(r.prev_close) or abs(r.gap_pct) < gap_min:
            return None
        if need_ext:
            if pd.isna(r.hi3) or pd.isna(r.lo3):
                return None
            if r.gap_pct > 0 and r.day_open < r.hi3:
                return None
            if r.gap_pct < 0 and r.day_open > r.lo3:
                return None
        if r.gap_pct > 0 and r.Close < r.Open and r.Close > r.prev_close:
            entry, sl, tp = r.Close, r.day_hi + _WIN_TICK, r.prev_close
            if sl > entry > tp:
                return {"dir": "VENDA", "sl": sl, "tp1": tp, "tp2": None, "partial": False}
        if r.gap_pct < 0 and r.Close > r.Open and r.Close < r.prev_close:
            entry, sl, tp = r.Close, r.day_lo - _WIN_TICK, r.prev_close
            if tp > entry > sl:
                return {"dir": "COMPRA", "sl": sl, "tp1": tp, "tp2": None, "partial": False}
        return None
    fn.__name__ = name or f"ovn_pdc_{gap_min}"
    return fn


def make_ovn_atr(gap_atr=1.2, vol_mult=1.2, hh0=9.25, hh1=9.75, dow_max=3, to_pdc=False):
    def fn(df, i, sym):
        r = df.iloc[i]
        if not (hh0 <= r.hh <= hh1) or r.dow > dow_max:
            return None
        if pd.isna(r.gap_atr) or abs(r.gap_atr) < gap_atr:
            return None
        if r.Volume < vol_mult * (r.vol8 or 0):
            return None
        if pd.isna(r.hi3) or pd.isna(r.lo3):
            return None
        if r.gap_atr > 0 and r.day_open > r.hi3 and r.Close < r.Open:
            if to_pdc and not pd.isna(r.prev_close):
                return {"dir": "VENDA", "sl": r.day_hi + _WIN_TICK, "tp1": r.prev_close,
                        "tp2": None, "partial": False}
            return _sig("VENDA", r.Close, r.day_hi + _WIN_TICK, 1.5, 2.0, True)
        if r.gap_atr < 0 and r.day_open < r.lo3 and r.Close > r.Open:
            if to_pdc and not pd.isna(r.prev_close):
                return {"dir": "COMPRA", "sl": r.day_lo - _WIN_TICK, "tp1": r.prev_close,
                        "tp2": None, "partial": False}
            return _sig("COMPRA", r.Close, r.day_lo - _WIN_TICK, 1.5, 2.0, True)
        return None
    return fn


def make_gap_fade(gap_min=0.20, atr_mult=0.8, hh=9.5, rr1=1.5, rr2=2.0):
    def fn(df, i, sym):
        r = df.iloc[i]
        if abs(r.hh - hh) > 1e-6:
            return None
        if pd.isna(r.prev_close) or pd.isna(r.atr) or r.atr <= 0 or pd.isna(r.gap_pct):
            return None
        if abs(r.gap_pct) < gap_min:
            return None
        if abs(r.Close - r.prev_close) < atr_mult * r.atr:
            return None
        if r.gap_pct > 0 and r.Close > r.prev_close and r.Close < r.Open:
            return _sig("VENDA", r.Close, r.day_hi + _WIN_TICK, rr1, rr2, True)
        if r.gap_pct < 0 and r.Close < r.prev_close and r.Close > r.Open:
            return _sig("COMPRA", r.Close, r.day_lo - _WIN_TICK, rr1, rr2, True)
        return None
    return fn


def make_ib_break(vol_mult=1.4, hh0=10.0, hh1=12.0, only_gap_align=False):
    def fn(df, i, sym):
        r = df.iloc[i]
        if not (hh0 <= r.hh < hh1):
            return None
        if pd.isna(r.ib_hi) or pd.isna(r.ib_lo):
            return None
        if r.Volume < vol_mult * (r.vol8 or 0):
            return None
        p = df.iloc[i - 1]
        if only_gap_align and not pd.isna(r.gap_pct):
            if r.gap_pct > 0 and not (r.Close > r.ib_hi and p.Close <= r.ib_hi):
                return None
            if r.gap_pct < 0 and not (r.Close < r.ib_lo and p.Close >= r.ib_lo):
                return None
            if abs(r.gap_pct) < 0.15:
                return None
        if r.Close > r.ib_hi and p.Close <= r.ib_hi:
            if only_gap_align and (pd.isna(r.gap_pct) or r.gap_pct <= 0):
                return None
            return _sig("COMPRA", r.Close, r.ib_lo, 1.5, 2.5, True)
        if r.Close < r.ib_lo and p.Close >= r.ib_lo:
            if only_gap_align and (pd.isna(r.gap_pct) or r.gap_pct >= 0):
                return None
            return _sig("VENDA", r.Close, r.ib_hi, 1.5, 2.5, True)
        return None
    return fn


def make_of_window(h0, h1, thr=0.30, dows=None, atr_max=None):
    def fn(df, i, sym):
        r = df.iloc[i]
        if not (h0 <= r.hh < h1):
            return None
        if dows is not None and int(r.dow) not in dows:
            return None
        if atr_max is not None and (pd.isna(r.atr_ratio) or r.atr_ratio >= atr_max):
            return None
        return _of(df, i, sym, thr=thr)
    return fn


def make_asia_break(asia_max=0.45, body_min=0.55, delta_min=0.0, tp_mult=1.75, h0=9, h1=11):
    def fn(df, i, sym):
        r = df.iloc[i]
        if not (h0 <= r.hh < h1):
            return None
        if pd.isna(r.asia_atr_ratio) or r.asia_atr_ratio > asia_max:
            return None
        if pd.isna(r.asia_hi) or pd.isna(r.asia_lo):
            return None
        body, rng, uw, dw = _parts(r)
        if body < body_min * rng:
            return None
        dn = r.dn if not pd.isna(r.dn) else 0
        p = df.iloc[i - 1]
        asia_rng = r.asia_hi - r.asia_lo
        if asia_rng <= 0:
            return None
        if r.Close > r.asia_hi and p.Close <= r.asia_hi and r.Close > r.Open and dn >= delta_min:
            return {"dir": "COMPRA", "sl": r.asia_lo, "tp1": r.Close + tp_mult * asia_rng,
                    "tp2": None, "partial": False}
        if r.Close < r.asia_lo and p.Close >= r.asia_lo and r.Close < r.Open and dn <= -delta_min:
            return {"dir": "VENDA", "sl": r.asia_hi, "tp1": r.Close - tp_mult * asia_rng,
                    "tp2": None, "partial": False}
        return None
    return fn


def make_round_fade(step=50.0, delta_max=0.12, atr_near=0.20, h0=None, h1=None, dows=None):
    def fn(df, i, sym):
        r = df.iloc[i]
        if h0 is not None and not (h0 <= r.hh < h1):
            return None
        if dows is not None and int(r.dow) not in dows:
            return None
        k = asset_key(sym)
        st = step if k == "XAUUSD" else (100.0 if k == "ETHUSD" else 1000.0)
        lvl = round(r.Close / st) * st
        if lvl <= 0 or abs(r.Close - lvl) > atr_near * (r.atr or 1):
            return None
        dn = r.dn if not pd.isna(r.dn) else 0
        if abs(dn) >= delta_max:
            return None
        body, rng, uw, dw = _parts(r)
        if r.High >= lvl and uw >= 0.45 * rng and r.Close < lvl:
            return _sig("VENDA", r.Close, r.High + 0.3 * (r.atr or 1), 1.5, 2.0, True)
        if r.Low <= lvl and dw >= 0.45 * rng and r.Close > lvl:
            return _sig("COMPRA", r.Close, r.Low - 0.3 * (r.atr or 1), 1.5, 2.0, True)
        return None
    return fn


def make_london_handoff(asia_max=0.50, delta_min=0.20, tp_mult=1.75, dows=None):
    def fn(df, i, sym):
        r = df.iloc[i]
        if not (SESS_LONDON_TRADE[0] <= r.hh < SESS_LONDON_TRADE[1]):
            return None
        if dows is not None and int(r.dow) not in dows:
            return None
        if pd.isna(r.asia_atr_ratio) or r.asia_atr_ratio > asia_max:
            return None
        if pd.isna(r.dn) or abs(r.dn) < delta_min:
            return None
        p = df.iloc[i - 1]
        asia_rng = r.asia_hi - r.asia_lo
        if asia_rng <= 0:
            return None
        if r.Close > r.asia_hi and p.Close <= r.asia_hi and r.dn > delta_min:
            return {"dir": "COMPRA", "sl": r.asia_lo, "tp1": r.Close + tp_mult * asia_rng,
                    "tp2": None, "partial": False}
        if r.Close < r.asia_lo and p.Close >= r.asia_lo and r.dn < -delta_min:
            return {"dir": "VENDA", "sl": r.asia_hi, "tp1": r.Close - tp_mult * asia_rng,
                    "tp2": None, "partial": False}
        return None
    return fn


# ════════════════════════════════════════════════════════════════════════════
# SETUPS CRIATIVOS AVULSOS
# ════════════════════════════════════════════════════════════════════════════

def s_win_nr7_break(df, i, sym):
    """NR7: menor range das últimas 7 barras → rompimento com volume."""
    if i < 10:
        return None
    r = df.iloc[i]
    if not (10.0 <= r.hh < 15.0):
        return None
    prev_ranges = (df.High.iloc[i - 7:i] - df.Low.iloc[i - 7:i])
    if len(prev_ranges) < 7 or prev_ranges.min() <= 0:
        return None
    if prev_ranges.iloc[-1] > prev_ranges.min() + 1e-12:
        return None
    p = df.iloc[i - 1]
    if r.Volume < 1.3 * (r.vol8 or 0):
        return None
    if r.Close > p.High:
        return _sig("COMPRA", r.Close, p.Low, 1.5, 2.5, True)
    if r.Close < p.Low:
        return _sig("VENDA", r.Close, p.High, 1.5, 2.5, True)
    return None


def s_win_engulf_trend(df, i, sym):
    """Engolfo na direção H4, 10–14h, volume ok."""
    r = df.iloc[i]
    p = df.iloc[i - 1]
    if not (10.0 <= r.hh < 14.0):
        return None
    if pd.isna(r.h4_close) or pd.isna(r.h4_ema50):
        return None
    if r.Volume < 1.2 * (r.vol8 or 0):
        return None
    up = r.h4_close > r.h4_ema50
    bull = r.Close > r.Open and p.Close < p.Open and r.Close >= p.Open and r.Open <= p.Close
    bear = r.Close < r.Open and p.Close > p.Open and r.Close <= p.Open and r.Open >= p.Close
    if up and bull:
        return _sig("COMPRA", r.Close, p.Low, 1.5, 2.5, True)
    if (not up) and bear:
        return _sig("VENDA", r.Close, p.High, 1.5, 2.5, True)
    return None


def s_win_ema_ribbon(df, i, sym):
    """EMA8>21>50 alinhadas + pullback à EMA8 com reversão."""
    r = df.iloc[i]
    if not (10.0 <= r.hh < 15.0):
        return None
    if pd.isna(r.ema8) or pd.isna(r.ema21) or pd.isna(r.ema50):
        return None
    bull = r.ema8 > r.ema21 > r.ema50
    bear = r.ema8 < r.ema21 < r.ema50
    near = abs(r.Close - r.ema8) < 0.5 * (r.atr or 1)
    if bull and near and reversal_up(df, i):
        return _sig("COMPRA", r.Close, r.Low - _WIN_TICK, 1.5, 2.5, True)
    if bear and near and reversal_down(df, i):
        return _sig("VENDA", r.Close, r.High + _WIN_TICK, 1.5, 2.5, True)
    return None


def s_win_range_day_fade(df, i, sym):
    """Dia com ATR comprimido + extremo do dia 14–15h → fade VWAP."""
    r = df.iloc[i]
    if not (14.0 <= r.hh < 15.5):
        return None
    if pd.isna(r.atr_ratio) or r.atr_ratio > 0.9 or pd.isna(r.vwap):
        return None
    if not pd.isna(r.day_hi_prev) and r.High >= r.day_hi_prev and reversal_down(df, i):
        return {"dir": "VENDA", "sl": r.day_hi + _WIN_TICK, "tp1": r.vwap, "tp2": None, "partial": False}
    if not pd.isna(r.day_lo_prev) and r.Low <= r.day_lo_prev and reversal_up(df, i):
        return {"dir": "COMPRA", "sl": r.day_lo - _WIN_TICK, "tp1": r.vwap, "tp2": None, "partial": False}
    return None


def s_win_open_drive_cont(df, i, sym):
    """Continuação do open drive: 09:45–10:15 a favor da 1ª barra se gap≥0.2%."""
    r = df.iloc[i]
    if not (9.75 <= r.hh <= 10.25):
        return None
    if pd.isna(r.od_first_dir) or pd.isna(r.gap_pct) or abs(r.gap_pct) < 0.2:
        return None
    if pd.isna(r.od_hi) or pd.isna(r.od_lo):
        return None
    p = df.iloc[i - 1]
    if r.od_first_dir > 0 and r.gap_pct > 0 and r.Close > r.od_hi and p.Close <= r.od_hi:
        return _sig("COMPRA", r.Close, r.od_lo, 1.5, 2.5, True)
    if r.od_first_dir < 0 and r.gap_pct < 0 and r.Close < r.od_lo and p.Close >= r.od_lo:
        return _sig("VENDA", r.Close, r.od_hi, 1.5, 2.5, True)
    return None


def s_win_twin_peaks(df, i, sym):
    """Dois toques no extremo do dia sem rompimento + delta contrário."""
    r = df.iloc[i]
    if not (11.0 <= r.hh < 15.0) or i < 5:
        return None
    dn = r.dn if not pd.isna(r.dn) else 0
    # segundo toque no day_hi
    if not pd.isna(r.day_hi_prev) and abs(r.High - r.day_hi) < 0.15 * (r.atr or 1):
        touches = 0
        for j in range(max(0, i - 20), i):
            if abs(df.iloc[j].High - r.day_hi) < 0.2 * (r.atr or 1):
                touches += 1
        if touches >= 2 and dn < -0.05 and reversal_down(df, i):
            return _sig("VENDA", r.Close, r.High + _WIN_TICK, 1.5, 2.0, True)
    if not pd.isna(r.day_lo_prev) and abs(r.Low - r.day_lo) < 0.15 * (r.atr or 1):
        touches = 0
        for j in range(max(0, i - 20), i):
            if abs(df.iloc[j].Low - r.day_lo) < 0.2 * (r.atr or 1):
                touches += 1
        if touches >= 2 and dn > 0.05 and reversal_up(df, i):
            return _sig("COMPRA", r.Close, r.Low - _WIN_TICK, 1.5, 2.0, True)
    return None


def s_wdo_gap_fade(df, i, sym):
    """Gap fade no WDO (mesmo molde WIN 09:30)."""
    return make_gap_fade(0.15, 0.7, 9.5)(df, i, sym)


def s_wdo_ovn_pdc(df, i, sym):
    return make_ovn_pdc(0.20, 9.25, 9.75, False, 4)(df, i, sym)


def s_xau_orb_london(df, i, sym):
    """ORB da 1ª hora de Londres (09–10) rompido 10–12."""
    r = df.iloc[i]
    if not (10.0 <= r.hh < 12.0):
        return None
    day = r.day
    # range 09–10 já está em... usamos lon parcial: recalcula via ib se hh servidor
    # usa barras do mesmo dia com hh 9-10
    # proxy: asia_hi/lo não serve; usa rolling do dia
    # Simplificação: high/low entre 9 e 10 já formados
    if pd.isna(r.atr):
        return None
    # constrói via group — campos ib no crypto = 09-10 B3 clock, no servidor IC 9-10 = Londres early
    if "ib_hi" not in df.columns or pd.isna(r.ib_hi):
        return None
    p = df.iloc[i - 1]
    dn = r.dn if not pd.isna(r.dn) else 0
    if r.Close > r.ib_hi and p.Close <= r.ib_hi and dn > 0.10:
        return _sig("COMPRA", r.Close, r.ib_lo, 1.5, 2.5, True)
    if r.Close < r.ib_lo and p.Close >= r.ib_lo and dn < -0.10:
        return _sig("VENDA", r.Close, r.ib_hi, 1.5, 2.5, True)
    return None


def s_xau_vwap_reject(df, i, sym):
    """Rejeição da VWAP na Londres com pavio + delta."""
    r = df.iloc[i]
    if not (9 <= r.hh < 12):
        return None
    if pd.isna(r.vwap) or pd.isna(r.atr):
        return None
    dn = r.dn if not pd.isna(r.dn) else 0
    body, rng, uw, dw = _parts(r)
    if r.Low <= r.vwap <= r.High:
        if r.Close > r.vwap and dw >= 0.4 * rng and dn > 0.10:
            return _sig("COMPRA", r.Close, r.Low - 0.3 * r.atr, 1.5, 2.0, True)
        if r.Close < r.vwap and uw >= 0.4 * rng and dn < -0.10:
            return _sig("VENDA", r.Close, r.High + 0.3 * r.atr, 1.5, 2.0, True)
    return None


def s_xau_ema_cross_session(df, i, sym):
    """Cruzamento EMA8/21 só na janela Londres, com volume."""
    r = df.iloc[i]
    p = df.iloc[i - 1]
    if not (9 <= r.hh < 11):
        return None
    if pd.isna(r.ema8) or pd.isna(r.ema21):
        return None
    if r.Volume < 1.3 * (r.vol8 or 0):
        return None
    if p.ema8 <= p.ema21 and r.ema8 > r.ema21:
        return _sig("COMPRA", r.Close, r.Low - 0.5 * (r.atr or 1), 1.5, 2.5, True)
    if p.ema8 >= p.ema21 and r.ema8 < r.ema21:
        return _sig("VENDA", r.Close, r.High + 0.5 * (r.atr or 1), 1.5, 2.5, True)
    return None


def s_xau_failed_ny_break(df, i, sym):
    """Falso rompimento do range Londres na NY → fade."""
    r = df.iloc[i]
    if not (15 <= r.hh < 18):
        return None
    if pd.isna(r.lon_hi) or pd.isna(r.lon_lo):
        return None
    body, rng, uw, dw = _parts(r)
    if r.High > r.lon_hi and r.Close < r.lon_hi and uw >= 0.4 * rng:
        return _sig("VENDA", r.Close, r.High + 0.3 * (r.atr or 1), 1.5, 2.0, True)
    if r.Low < r.lon_lo and r.Close > r.lon_lo and dw >= 0.4 * rng:
        return _sig("COMPRA", r.Close, r.Low - 0.3 * (r.atr or 1), 1.5, 2.0, True)
    return None


def s_eth_asia_comp(df, i, sym):
    return make_asia_break(0.50, 0.55, 0.10, 1.75)(df, i, sym)


def s_eth_round_fade(df, i, sym):
    return make_round_fade(100.0, 0.12, 0.25, 9, 17)(df, i, sym)


def s_btc_asia_comp(df, i, sym):
    return make_asia_break(0.50, 0.55, 0.10, 1.75)(df, i, sym)


def s_btc_of_london(df, i, sym):
    return make_of_window(9, 11, 0.30)(df, i, sym)


def s_win_inside_bar_break(df, i, sym):
    """Inside bar (barra dentro da anterior) → rompimento."""
    r = df.iloc[i]
    p = df.iloc[i - 1]
    pp = df.iloc[i - 2]
    if not (10.0 <= r.hh < 14.0):
        return None
    inside = p.High <= pp.High and p.Low >= pp.Low
    if not inside:
        return None
    if r.Close > p.High and r.Volume >= 1.2 * (r.vol8 or 0):
        return _sig("COMPRA", r.Close, p.Low, 1.5, 2.5, True)
    if r.Close < p.Low and r.Volume >= 1.2 * (r.vol8 or 0):
        return _sig("VENDA", r.Close, p.High, 1.5, 2.5, True)
    return None


def s_win_rth_mid_reversion(df, i, sym):
    """11:30–12:30: retorno à VWAP se dist≥1.2 ATR e RSI extremo."""
    r = df.iloc[i]
    if not (11.5 <= r.hh < 12.5):
        return None
    if pd.isna(r.vwap_dist) or abs(r.vwap_dist) < 1.2 or pd.isna(r.rsi):
        return None
    if r.vwap_dist > 0 and r.rsi >= 60 and reversal_down(df, i):
        return {"dir": "VENDA", "sl": r.day_hi + _WIN_TICK, "tp1": r.vwap, "tp2": None, "partial": False}
    if r.vwap_dist < 0 and r.rsi <= 40 and reversal_up(df, i):
        return {"dir": "COMPRA", "sl": r.day_lo - _WIN_TICK, "tp1": r.vwap, "tp2": None, "partial": False}
    return None


def s_xau_delta_burst(df, i, sym):
    """Burst de delta (|dn|≥0.45) com candle de corpo forte, Londres/NY."""
    r = df.iloc[i]
    ok = (9 <= r.hh < 11) or (15.5 <= r.hh < 17.5)
    if not ok or pd.isna(r.dn):
        return None
    body, rng, uw, dw = _parts(r)
    if body < 0.6 * rng:
        return None
    if r.dn >= 0.45 and r.Close > r.Open:
        return _sig("COMPRA", r.Close, r.Low - 0.3 * (r.atr or 1), 1.5, 2.5, True)
    if r.dn <= -0.45 and r.Close < r.Open:
        return _sig("VENDA", r.Close, r.High + 0.3 * (r.atr or 1), 1.5, 2.5, True)
    return None


def s_xau_lon_comp_ny(df, i, sym):
    r = df.iloc[i]
    if not (SESS_NY_EARLY[0] <= r.hh < SESS_NY_EARLY[1]):
        return None
    if pd.isna(r.lon_atr_ratio) or r.lon_atr_ratio > 0.50:
        return None
    p = df.iloc[i - 1]
    lon_rng = r.lon_hi - r.lon_lo
    if lon_rng <= 0:
        return None
    dn = r.dn if not pd.isna(r.dn) else 0
    if r.Close > r.lon_hi and p.Close <= r.lon_hi and dn > 0.15:
        return {"dir": "COMPRA", "sl": r.lon_lo, "tp1": r.Close + 1.75 * lon_rng,
                "tp2": None, "partial": False}
    if r.Close < r.lon_lo and p.Close >= r.lon_lo and dn < -0.15:
        return {"dir": "VENDA", "sl": r.lon_hi, "tp1": r.Close - 1.75 * lon_rng,
                "tp2": None, "partial": False}
    return None


# ════════════════════════════════════════════════════════════════════════════
# REGISTRO EM MASSA
# ════════════════════════════════════════════════════════════════════════════

STRATS = {"b3": {}, "crypto": {}}
STRAT_SYM = {}
STRAT_TF = {}
ONE_PER_DAY = set()


def _reg(grupo, name, fn, sym, tf=15, one_per_day=False):
    STRATS[grupo][name] = fn
    STRAT_SYM[name] = sym
    if tf != 15:
        STRAT_TF[name] = tf
    if one_per_day:
        ONE_PER_DAY.add(name)


# ── B3: família overnight / gap (refinos do quase-GO) ───────────────────────
_reg("b3", "OVN_PDC_025", make_ovn_pdc(0.25, 9.25, 9.75, False, 4), "WIN", one_per_day=True)
_reg("b3", "OVN_PDC_030", make_ovn_pdc(0.30, 9.25, 9.75, False, 4), "WIN", one_per_day=True)
_reg("b3", "OVN_PDC_020", make_ovn_pdc(0.20, 9.25, 9.75, False, 4), "WIN", one_per_day=True)
_reg("b3", "OVN_PDC_035_EXT", make_ovn_pdc(0.35, 9.25, 9.75, True, 4), "WIN", one_per_day=True)
_reg("b3", "OVN_PDC_025_MT", make_ovn_pdc(0.25, 9.25, 9.75, False, 3), "WIN", one_per_day=True)
_reg("b3", "OVN_PDC_025_EARLY", make_ovn_pdc(0.25, 9.25, 9.5, False, 4), "WIN", one_per_day=True)
_reg("b3", "OVN_ATR_12", make_ovn_atr(1.2, 1.2, 9.25, 9.75, 3), "WIN", one_per_day=True)
_reg("b3", "OVN_ATR_15", make_ovn_atr(1.5, 1.2, 9.25, 9.75, 4), "WIN", one_per_day=True)
_reg("b3", "OVN_ATR_12_PDC", make_ovn_atr(1.2, 1.2, 9.25, 9.75, 3, True), "WIN", one_per_day=True)
_reg("b3", "OVN_ATR_10_VOL", make_ovn_atr(1.0, 1.4, 9.25, 9.75, 3), "WIN", one_per_day=True)
_reg("b3", "GAP_FADE_020", make_gap_fade(0.20, 0.8, 9.5), "WIN", one_per_day=True)
_reg("b3", "GAP_FADE_025", make_gap_fade(0.25, 1.0, 9.5), "WIN", one_per_day=True)
_reg("b3", "GAP_FADE_0945", make_gap_fade(0.20, 0.8, 9.75), "WIN", one_per_day=True)
_reg("b3", "GAP_FADE_RR2", make_gap_fade(0.25, 0.9, 9.5, 2.0, 3.0), "WIN", one_per_day=True)

# ── B3: IB / estrutura ──────────────────────────────────────────────────────
_reg("b3", "IB_BRK_14", make_ib_break(1.4, 10, 12), "WIN", one_per_day=True)
_reg("b3", "IB_BRK_16", make_ib_break(1.6, 10, 11.5), "WIN", one_per_day=True)
_reg("b3", "IB_BRK_GAP", make_ib_break(1.3, 10, 12, True), "WIN", one_per_day=True)
_reg("b3", "IB_BRK_LATE", make_ib_break(1.4, 10.5, 13), "WIN", one_per_day=True)

# ── B3: criativos ───────────────────────────────────────────────────────────
_reg("b3", "NR7_BREAK", s_win_nr7_break, "WIN")
_reg("b3", "ENGULF_H4", s_win_engulf_trend, "WIN")
_reg("b3", "EMA_RIBBON", s_win_ema_ribbon, "WIN")
_reg("b3", "RANGE_DAY_FADE", s_win_range_day_fade, "WIN", one_per_day=True)
_reg("b3", "OPEN_DRIVE_CONT", s_win_open_drive_cont, "WIN", one_per_day=True)
_reg("b3", "TWIN_PEAKS", s_win_twin_peaks, "WIN")
_reg("b3", "INSIDE_BAR_BRK", s_win_inside_bar_break, "WIN")
_reg("b3", "MID_RTH_REV", s_win_rth_mid_reversion, "WIN", one_per_day=True)

# ── WDO ─────────────────────────────────────────────────────────────────────
_reg("b3", "WDO_GAP_FADE", s_wdo_gap_fade, "WDO", one_per_day=True)
_reg("b3", "WDO_OVN_PDC", s_wdo_ovn_pdc, "WDO", one_per_day=True)
_reg("b3", "WDO_IB_BRK", make_ib_break(1.4, 10, 12), "WDO", one_per_day=True)
_reg("b3", "WDO_NR7", s_win_nr7_break, "WDO")
_reg("b3", "WDO_EMA_RIBBON", s_win_ema_ribbon, "WDO")

# ── Crypto XAU: OF windows ──────────────────────────────────────────────────
_reg("crypto", "OF_L9_11", make_of_window(9, 11, 0.30), "XAUUSD")
_reg("crypto", "OF_L9_11_D35", make_of_window(9, 11, 0.35), "XAUUSD")
_reg("crypto", "OF_L9_11_D40", make_of_window(9, 11, 0.40), "XAUUSD")
_reg("crypto", "OF_L9_11_MT", make_of_window(9, 11, 0.30, (0, 1, 2, 3)), "XAUUSD")
_reg("crypto", "OF_L9_11_TT", make_of_window(9, 11, 0.30, (1, 2, 3)), "XAUUSD")
_reg("crypto", "OF_L9_11_LV", make_of_window(9, 11, 0.30, None, 1.05), "XAUUSD")
_reg("crypto", "OF_NY15_17", make_of_window(15.5, 17.5, 0.30), "XAUUSD")
_reg("crypto", "OF_NY15_17_D35", make_of_window(15.5, 17.5, 0.35), "XAUUSD")
_reg("crypto", "OF_NY15_17_TT", make_of_window(15.5, 17.5, 0.30, (1, 2, 3)), "XAUUSD")
_reg("crypto", "OF_AM8_11_LV", make_of_window(8, 11, 0.30, None, 1.0), "XAUUSD")
_reg("crypto", "OF_PM14_17_LV", make_of_window(14, 17, 0.30, None, 1.0), "XAUUSD")
_reg("crypto", "OF_FULL_TT_LV", make_of_window(8, 17, 0.35, (1, 2, 3), 1.05), "XAUUSD")

# ── Crypto XAU: Asia / handoff ──────────────────────────────────────────────
_reg("crypto", "ASIA_045_D15", make_asia_break(0.45, 0.55, 0.15), "XAUUSD", one_per_day=True)
_reg("crypto", "ASIA_040_D15", make_asia_break(0.40, 0.60, 0.15, 2.0), "XAUUSD", one_per_day=True)
_reg("crypto", "ASIA_050_D20", make_asia_break(0.50, 0.55, 0.20), "XAUUSD", one_per_day=True)
_reg("crypto", "ASIA_035_D10", make_asia_break(0.35, 0.65, 0.10, 2.0), "XAUUSD", one_per_day=True)
_reg("crypto", "LH_050_D20", make_london_handoff(0.50, 0.20), "XAUUSD", one_per_day=True)
_reg("crypto", "LH_045_D20", make_london_handoff(0.45, 0.20), "XAUUSD", one_per_day=True)
_reg("crypto", "LH_050_D15_MT", make_london_handoff(0.50, 0.15, 1.75, (0, 1, 2, 3)), "XAUUSD", one_per_day=True)
_reg("crypto", "LH_040_D25", make_london_handoff(0.40, 0.25, 2.0), "XAUUSD", one_per_day=True)
_reg("crypto", "LH_055_D20_TT", make_london_handoff(0.55, 0.20, 1.75, (1, 2, 3)), "XAUUSD", one_per_day=True)

# ── Crypto XAU: round fade / criativos ──────────────────────────────────────
_reg("crypto", "RND_FADE", make_round_fade(50, 0.12, 0.20), "XAUUSD")
_reg("crypto", "RND_FADE_TIGHT", make_round_fade(50, 0.08, 0.15), "XAUUSD")
_reg("crypto", "RND_FADE_LON", make_round_fade(50, 0.12, 0.20, 9, 12), "XAUUSD")
_reg("crypto", "RND_FADE_NY", make_round_fade(50, 0.12, 0.20, 15, 18), "XAUUSD")
_reg("crypto", "RND_FADE_TT", make_round_fade(50, 0.10, 0.18, None, None, (1, 2, 3)), "XAUUSD")
_reg("crypto", "RND_FADE_100", make_round_fade(100, 0.12, 0.25), "XAUUSD")
_reg("crypto", "XAU_ORB_LON", s_xau_orb_london, "XAUUSD", one_per_day=True)
_reg("crypto", "XAU_VWAP_REJ", s_xau_vwap_reject, "XAUUSD")
_reg("crypto", "XAU_EMA_X_LON", s_xau_ema_cross_session, "XAUUSD")
_reg("crypto", "XAU_FAIL_NY", s_xau_failed_ny_break, "XAUUSD")
_reg("crypto", "XAU_DELTA_BURST", s_xau_delta_burst, "XAUUSD")
_reg("crypto", "XAU_LON_NY", s_xau_lon_comp_ny, "XAUUSD", one_per_day=True)

# ── ETH / BTC ───────────────────────────────────────────────────────────────
_reg("crypto", "ETH_OF_L9_11", make_of_window(9, 11, 0.30), "ETHUSD")
_reg("crypto", "ETH_OF_L9_11_D35", make_of_window(9, 11, 0.35), "ETHUSD")
_reg("crypto", "ETH_OF_NY", make_of_window(15.5, 17.5, 0.30), "ETHUSD")
_reg("crypto", "ETH_ASIA", s_eth_asia_comp, "ETHUSD", one_per_day=True)
_reg("crypto", "ETH_RND", s_eth_round_fade, "ETHUSD")
_reg("crypto", "ETH_LH", make_london_handoff(0.50, 0.15), "ETHUSD", one_per_day=True)
_reg("crypto", "BTC_OF_L9_11", s_btc_of_london, "BTCUSD")
_reg("crypto", "BTC_ASIA", s_btc_asia_comp, "BTCUSD", one_per_day=True)
_reg("crypto", "BTC_LH", make_london_handoff(0.50, 0.15), "BTCUSD", one_per_day=True)
_reg("crypto", "BTC_RND", make_round_fade(1000, 0.12, 0.25, 9, 17), "BTCUSD")

# ── XAU M5 OF (histórico curto, mas vale varrer) ────────────────────────────
_reg("crypto", "M5_OF_L9_11", make_of_window(9, 11, 0.30), "XAUUSD", tf=5)
_reg("crypto", "M5_OF_NY", make_of_window(15.5, 17.5, 0.30), "XAUUSD", tf=5)
_reg("crypto", "M5_OF_NY_D35", make_of_window(15.5, 17.5, 0.35), "XAUUSD", tf=5)
_reg("crypto", "M5_OF_L_TT", make_of_window(9, 11, 0.30, (1, 2, 3)), "XAUUSD", tf=5)


# Patch ONE_PER_DAY into simulate via local wrapper
def _sim(df, symbol, strat_fn, name, tf=15):
    # _simulate da v2 usa ONE_PER_DAY do módulo v2 — sobrescrevemos checando aqui
    # Reusa o mesmo loop mas com nosso set: importa e chama com name;
    # a v2 só marca one_per_day se name ∈ v2.ONE_PER_DAY. Então copiamos lógica:
    from backtest_setups_novos_v2 import ONE_PER_DAY as _v2_opd
    # temporariamente adiciona
    added = name in ONE_PER_DAY and name not in _v2_opd
    if added:
        _v2_opd.add(name)
    try:
        return _simulate(df, symbol, strat_fn, name, tf=tf)
    finally:
        if added:
            _v2_opd.discard(name)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--grupo", choices=["b3", "crypto"], default="b3")
    ap.add_argument("--bars", type=int, default=300000)
    args = ap.parse_args()

    os.environ["KIMI_GROUP"] = args.grupo
    os.makedirs("logs", exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    logf = open(f"logs/backtest_setups_novos_v3_{args.grupo}_{stamp}.txt", "w", encoding="utf-8")

    def out(s):
        print(s)
        logf.write(s + "\n")
        logf.flush()

    strat_map = STRATS[args.grupo]
    out(f"{'#' * 74}\n# SETUPS NOVOS V3 ({len(strat_map)} setups) — {args.grupo} · "
        f"{datetime.now():%d/%m %H:%M}\n# NÃO altera motores · GO rigoroso\n{'#' * 74}")

    needs = {}
    for name in strat_map:
        k = STRAT_SYM[name]
        tf = STRAT_TF.get(name, 15)
        needs.setdefault((k, tf), []).append(name)
    if args.grupo == "b3":
        needs.setdefault(("WIN", 15), [])
    if args.grupo == "crypto":
        needs.setdefault(("BTCUSD", 15), [])

    sym_full = {"WIN": "WIN$D", "WDO": "WDO$D", "XAUUSD": "XAUUSD",
                "ETHUSD": "ETHUSD", "BTCUSD": "BTCUSD"}
    ranking = []

    _mt5c = None
    if args.grupo == "crypto":
        from backtest_crypto_pro import mt5_connect, fetch as _cp_fetch
        _mt5c = mt5_connect()

    def _get(k, tf):
        if args.grupo == "b3":
            return _b3_fetch(sym_full[k], tf, args.bars)
        from backtest_crypto_pro import fetch as _cp_fetch
        raw = _cp_fetch(_mt5c, sym_full[k], tf, args.bars)
        return add_indicators(raw) if raw is not None else None

    raw_dfs = {}
    for (k, tf) in sorted(needs.keys()):
        t0 = time.time()
        df = _get(k, tf)
        if df is None or len(df) < 3000:
            out(f"\n!! {k} M{tf}: sem histórico")
            continue
        raw_dfs[(k, tf)] = df
        bars_day = 26 if k in ("WIN", "WDO") else (288 if tf == 5 else 96)
        years_den = 252 if k in ("WIN", "WDO") else 365
        anos = len(df) / bars_day / years_den
        out(f"\n{'=' * 74}\n  {k} M{tf}: {len(df)} candles (~{anos:.1f}a)  "
            f"[{df.index[0].date()} → {df.index[-1].date()}] ({time.time() - t0:.0f}s)\n{'=' * 74}")

    ctx15 = {}
    if ("WIN", 15) in raw_dfs:
        ctx15["WIN"] = raw_dfs[("WIN", 15)]
    if ("BTCUSD", 15) in raw_dfs:
        ctx15["BTCUSD"] = raw_dfs[("BTCUSD", 15)]

    dfs = {}
    for key, df in raw_dfs.items():
        k, tf = key
        dfs[key] = add_extra_v2(df.copy(), sym_full[k], ctx=(ctx15 if tf == 15 else None))

    for name, fn in strat_map.items():
        k = STRAT_SYM[name]
        tf = STRAT_TF.get(name, 15)
        key = (k, tf)
        if key not in dfs:
            out(f"\n  ▸ {name} ({k} M{tf}): sem dados")
            continue
        df = dfs[key]
        t0 = time.time()
        t = _sim(df, sym_full[k], fn, name, tf=tf)
        s = stats(t)
        cons = consistency(t)
        out(f"\n  ▸ {name}  ({k} M{tf})   [{time.time() - t0:.0f}s]")
        out(_line("PERÍODO COMPLETO", s, cons))
        s_out = {"n": 0}
        if not t.empty:
            t = t.sort_values("ts").reset_index(drop=True)
            cut = int(len(t) * (1 - OOS_FRAC))
            s_in, s_out = stats(t.iloc[:cut]), stats(t.iloc[cut:])
            out(_line("IN-SAMPLE  (70%)", s_in, consistency(t.iloc[:cut])))
            out(_line("OUT-SAMPLE (30%)", s_out, consistency(t.iloc[cut:])))
            if s_out.get("n", 0) >= 20:
                seg = s_out["net"] >= 0.08 and s_out["PF"] >= 1.15
                out(f"      → OUT-OF-SAMPLE {'SEGUROU ✅' if seg else 'CAIU ❌'} "
                    f"(net {s_out['net']:+.3f}, PF {s_out['PF']})")
            t.to_csv(f"logs/novos_v3_{k}_M{tf}_{name}.csv", index=False)
        v = verdict(s, cons, s_out)
        out(f"      VEREDITO: {v}")
        ranking.append({"setup": name, "sym": k, "tf": tf, **s, "cons%": cons, "veredito": v})

    out(f"\n{'#' * 74}\n  RANKING\n{'#' * 74}")
    rk = pd.DataFrame(ranking)
    goes = []
    if not rk.empty:
        rk = rk.sort_values("net", ascending=False)
        for _, r in rk.iterrows():
            if r.get("n", 0) == 0:
                out(f"  {r.sym:<7} M{int(r.tf)} {r.setup:<22} sem trades")
                continue
            out(f"  {r.sym:<7} M{int(r.tf)} {r.setup:<22} n={int(r.n):<5} net {r.net:+.3f} R  "
                f"PF {r.PF:<5} cons {r['cons%']}%  {r.veredito}")
            if "GO" in str(r.veredito) and "🟢" in str(r.veredito):
                goes.append(r.setup)
    out(f"\n# 🟢 GO encontrados: {goes if goes else 'nenhum'}")
    out(f"# Critério: net≥+0,10 · PF≥1,25 · cons≥55% · OOS segura · n≥40")
    out(f"# FIM — logs/backtest_setups_novos_v3_{args.grupo}_{stamp}.txt")
    logf.close()


if __name__ == "__main__":
    main()
