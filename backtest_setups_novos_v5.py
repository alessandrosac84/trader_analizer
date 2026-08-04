"""
backtest_setups_novos_v5.py — RODADA 5: máximo de candidatos lucrativos.

Régua idêntica (custos, OOS 70/30, bimestre, GO). NÃO altera motores ao vivo.

Foco:
  · Refinos da família GO (NR7 / INSIDE) com filtros H4, gap, horário, vol
  · Empurrar 🟡 da v4 (OVN_PDC, INSIDE_VOL, RND_FADE_T_*, LH_*)
  · Conceitos novos ainda não batidos nas v1–v4

Uso:
  python rodar_backtest_setups_novos_v5.py
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
    _parts, OOS_FRAC, _WIN_TICK,
)
from backtest_setups_novos_v2 import (
    add_extra_v2, _simulate, _of,
)
from backtest_setups_novos_v3 import (
    make_ovn_pdc, make_ovn_atr, make_gap_fade, make_ib_break,
    make_of_window, make_asia_break, make_round_fade, make_london_handoff,
    s_win_nr7_break, s_win_inside_bar_break,
)
from backtest_setups_novos_v4 import (
    s_win_nr7_trend, s_win_inside_vol15, s_win_nr7_morning,
    s_win_double_inside, s_xau_nr7, s_xau_inside,
)

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


def _nr_break(df, i, n=7, vol_mult=1.3, hh0=10.0, hh1=15.0):
    if i < n + 2:
        return None
    r = df.iloc[i]
    if not (hh0 <= r.hh < hh1):
        return None
    prev = (df.High.iloc[i - n:i] - df.Low.iloc[i - n:i])
    if len(prev) < n or prev.min() <= 0:
        return None
    if prev.iloc[-1] > prev.min() + 1e-12:
        return None
    p = df.iloc[i - 1]
    if r.Volume < vol_mult * (r.vol8 or 0):
        return None
    if r.Close > p.High:
        return _sig("COMPRA", r.Close, p.Low, 1.5, 2.5, True)
    if r.Close < p.Low:
        return _sig("VENDA", r.Close, p.High, 1.5, 2.5, True)
    return None


def _h4_ok(r, d):
    if pd.isna(r.h4_close) or pd.isna(r.h4_ema50):
        return False
    up = r.h4_close > r.h4_ema50
    return (d == "COMPRA" and up) or (d == "VENDA" and not up)


def _gap_ok(r, d, min_abs=0.10):
    if pd.isna(r.gap_pct) or abs(r.gap_pct) < min_abs:
        return False
    return (d == "COMPRA" and r.gap_pct > 0) or (d == "VENDA" and r.gap_pct < 0)


# ════════════════════════════════════════════════════════════════════════════
# B3 — família NR / INSIDE (variações do que já GO)
# ════════════════════════════════════════════════════════════════════════════

def s_nr4_break(df, i, sym):
    return _nr_break(df, i, n=4, vol_mult=1.25, hh0=10.0, hh1=14.5)


def s_nr5_break(df, i, sym):
    return _nr_break(df, i, n=5, vol_mult=1.3, hh0=10.0, hh1=14.5)


def s_nr7_h4_gap(df, i, sym):
    base = s_win_nr7_break(df, i, sym)
    if not base:
        return None
    r = df.iloc[i]
    if not _h4_ok(r, base["dir"]) or not _gap_ok(r, base["dir"], 0.08):
        return None
    return base


def s_nr7_h4_am(df, i, sym):
    r = df.iloc[i]
    if not (10.0 <= r.hh < 12.5):
        return None
    base = s_win_nr7_trend(df, i, sym)
    return base


def s_nr7_vol16(df, i, sym):
    return _nr_break(df, i, n=7, vol_mult=1.6, hh0=10.0, hh1=14.0)


def s_nr7_quiet_atr(df, i, sym):
    base = s_win_nr7_break(df, i, sym)
    if not base:
        return None
    r = df.iloc[i]
    if pd.isna(r.atr_ratio) or r.atr_ratio > 1.15:
        return None
    return base


def s_nr7_dow_mt(df, i, sym):
    r = df.iloc[i]
    if int(r.dow) not in (0, 1, 2, 3):
        return None
    return s_win_nr7_trend(df, i, sym)


def s_inside_h4(df, i, sym):
    base = s_win_inside_bar_break(df, i, sym)
    if not base:
        return None
    r = df.iloc[i]
    if not _h4_ok(r, base["dir"]):
        return None
    return base


def s_inside_h4_gap(df, i, sym):
    base = s_inside_h4(df, i, sym)
    if not base:
        return None
    r = df.iloc[i]
    if not _gap_ok(r, base["dir"], 0.08):
        return None
    return base


def s_inside_am(df, i, sym):
    r = df.iloc[i]
    if not (10.0 <= r.hh < 12.0):
        return None
    return s_win_inside_bar_break(df, i, sym)


def s_inside_vol18_h4(df, i, sym):
    base = s_win_inside_vol15(df, i, sym)
    if not base:
        return None
    r = df.iloc[i]
    if r.Volume < 1.8 * (r.vol8 or 0):
        return None
    if not _h4_ok(r, base["dir"]):
        return None
    return base


def s_double_inside_h4(df, i, sym):
    base = s_win_double_inside(df, i, sym)
    if not base:
        return None
    r = df.iloc[i]
    if not _h4_ok(r, base["dir"]):
        return None
    return base


def s_outside_rev(df, i, sym):
    """Outside bar que engole a anterior e fecha no extremo contrário ao gap."""
    if i < 2:
        return None
    r, p = df.iloc[i], df.iloc[i - 1]
    if not (10.0 <= r.hh < 13.5):
        return None
    if not (r.High > p.High and r.Low < p.Low):
        return None
    if r.Volume < 1.25 * (r.vol8 or 0):
        return None
    body, rng, uw, dw = _parts(r)
    if pd.isna(r.gap_pct):
        return None
    if r.gap_pct > 0.1 and r.Close < r.Open and uw >= 0.35 * rng:
        return _sig("VENDA", r.Close, r.High + _WIN_TICK, 1.5, 2.5, True)
    if r.gap_pct < -0.1 and r.Close > r.Open and dw >= 0.35 * rng:
        return _sig("COMPRA", r.Close, r.Low - _WIN_TICK, 1.5, 2.5, True)
    return None


def s_pdh_break_vol(df, i, sym):
    """Rompimento do high/low do dia anterior com volume, 11–14h."""
    if i < 2:
        return None
    r, p = df.iloc[i], df.iloc[i - 1]
    if not (11.0 <= r.hh < 14.0):
        return None
    if pd.isna(r.prev_day_hi) or pd.isna(r.prev_day_lo):
        return None
    if r.Volume < 1.4 * (r.vol8 or 0):
        return None
    if r.Close > r.prev_day_hi and p.Close <= r.prev_day_hi:
        return _sig("COMPRA", r.Close, r.prev_day_hi - (r.atr or 50) * 0.3, 1.5, 2.5, True)
    if r.Close < r.prev_day_lo and p.Close >= r.prev_day_lo:
        return _sig("VENDA", r.Close, r.prev_day_lo + (r.atr or 50) * 0.3, 1.5, 2.5, True)
    return None


def s_pdh_break_h4(df, i, sym):
    base = s_pdh_break_vol(df, i, sym)
    if not base:
        return None
    r = df.iloc[i]
    if not _h4_ok(r, base["dir"]):
        return None
    return base


def s_vwap_pb_trend(df, i, sym):
    """Pullback ao VWAP a favor do H4, 10:30–13h, corpo retomada."""
    if i < 2:
        return None
    r, p = df.iloc[i], df.iloc[i - 1]
    if not (10.5 <= r.hh < 13.0):
        return None
    if pd.isna(r.vwap) or pd.isna(r.vwap_dist) or pd.isna(r.h4_close):
        return None
    if abs(r.vwap_dist) > 0.85:
        return None
    up = r.h4_close > r.h4_ema50
    if up and p.Low <= p.vwap and r.Close > r.vwap and r.Close > r.Open:
        if r.Volume < 1.15 * (r.vol8 or 0):
            return None
        return _sig("COMPRA", r.Close, min(p.Low, r.Low) - _WIN_TICK, 1.5, 2.5, True)
    if (not up) and p.High >= p.vwap and r.Close < r.vwap and r.Close < r.Open:
        if r.Volume < 1.15 * (r.vol8 or 0):
            return None
        return _sig("VENDA", r.Close, max(p.High, r.High) + _WIN_TICK, 1.5, 2.5, True)
    return None


def s_orb_mid_reclaim(df, i, sym):
    """Preço cruza o meio do ORB a favor do gap, 10:45–12:30."""
    if i < 2:
        return None
    r, p = df.iloc[i], df.iloc[i - 1]
    if not (10.75 < r.hh < 12.5):
        return None
    if pd.isna(r.orb_hi) or pd.isna(r.orb_lo) or pd.isna(r.gap_pct):
        return None
    mid = 0.5 * (r.orb_hi + r.orb_lo)
    if r.orb_hi - r.orb_lo <= 0:
        return None
    if r.Volume < 1.2 * (r.vol8 or 0):
        return None
    if r.gap_pct > 0.12 and p.Close <= mid and r.Close > mid and r.Close > r.Open:
        return _sig("COMPRA", r.Close, r.orb_lo, 1.5, 2.5, True)
    if r.gap_pct < -0.12 and p.Close >= mid and r.Close < mid and r.Close < r.Open:
        return _sig("VENDA", r.Close, r.orb_hi, 1.5, 2.5, True)
    return None


def s_hl_break(df, i, sym):
    """3 higher-lows → rompe high da sequência (ou espelho)."""
    if i < 5:
        return None
    r = df.iloc[i]
    if not (10.5 <= r.hh < 14.0):
        return None
    lows = df.Low.iloc[i - 4:i].values
    highs = df.High.iloc[i - 4:i].values
    if r.Volume < 1.25 * (r.vol8 or 0):
        return None
    if lows[1] > lows[0] and lows[2] > lows[1] and lows[3] > lows[2]:
        level = float(np.max(highs))
        if r.Close > level and df.iloc[i - 1].Close <= level:
            return _sig("COMPRA", r.Close, float(lows[-1]) - _WIN_TICK, 1.5, 2.5, True)
    if highs[1] < highs[0] and highs[2] < highs[1] and highs[3] < highs[2]:
        level = float(np.min(lows))
        if r.Close < level and df.iloc[i - 1].Close >= level:
            return _sig("VENDA", r.Close, float(highs[-1]) + _WIN_TICK, 1.5, 2.5, True)
    return None


def s_bb_squeeze_brk(df, i, sym):
    """BB width no mínimo de 20 barras → rompimento com volume."""
    if i < 25:
        return None
    r = df.iloc[i]
    if not (10.0 <= r.hh < 14.0):
        return None
    if "bb_up" not in df.columns or pd.isna(r.bb_up) or pd.isna(r.bb_dn) or pd.isna(r.bb_mid):
        return None
    mid = df["bb_mid"].iloc[i - 20:i].replace(0, np.nan)
    width = (df["bb_up"].iloc[i - 20:i] - df["bb_dn"].iloc[i - 20:i]) / mid
    if width.isna().all() or width.iloc[-1] > width.min() + 1e-12:
        return None
    p = df.iloc[i - 1]
    if r.Volume < 1.35 * (r.vol8 or 0):
        return None
    if r.Close > r.bb_up and p.Close <= p.bb_up:
        return _sig("COMPRA", r.Close, r.bb_mid, 1.5, 2.5, True)
    if r.Close < r.bb_dn and p.Close >= p.bb_dn:
        return _sig("VENDA", r.Close, r.bb_mid, 1.5, 2.5, True)
    return None


def s_lunch_fade(df, i, sym):
    """12–13:30: fade extremo do dia (≥0.7×ATR do open) com rejeição."""
    r = df.iloc[i]
    if not (12.0 <= r.hh < 13.5):
        return None
    if pd.isna(r.day_open) or pd.isna(r.atr) or r.atr <= 0:
        return None
    body, rng, uw, dw = _parts(r)
    dist = (r.Close - r.day_open) / r.atr
    if dist >= 0.7 and uw >= 0.40 * rng and r.Close < r.Open:
        return _sig("VENDA", r.Close, r.High + _WIN_TICK, 1.5, 2.0, True)
    if dist <= -0.7 and dw >= 0.40 * rng and r.Close > r.Open:
        return _sig("COMPRA", r.Close, r.Low - _WIN_TICK, 1.5, 2.0, True)
    return None


def s_gap_cont_after_fill(df, i, sym):
    """Gap ≥0.25%: toca PDC até 10h e depois continua na direção do gap."""
    if i < 2:
        return None
    r, p = df.iloc[i], df.iloc[i - 1]
    if not (10.25 <= r.hh < 12.0):
        return None
    if pd.isna(r.gap_pct) or abs(r.gap_pct) < 0.25:
        return None
    if int(getattr(r, "touched_pdc_10", 0) or 0) < 1:
        return None
    if r.Volume < 1.25 * (r.vol8 or 0):
        return None
    if r.gap_pct > 0 and r.Close > r.day_open and r.Close > p.High and r.Close > r.Open:
        return _sig("COMPRA", r.Close, r.prev_close, 1.5, 2.5, True)
    if r.gap_pct < 0 and r.Close < r.day_open and r.Close < p.Low and r.Close < r.Open:
        return _sig("VENDA", r.Close, r.prev_close, 1.5, 2.5, True)
    return None


def s_ema_ribbon_pb(df, i, sym):
    """Pullback em EMA20 com H4 alinhado, 10–13h."""
    if i < 3:
        return None
    r, p = df.iloc[i], df.iloc[i - 1]
    if not (10.0 <= r.hh < 13.0):
        return None
    if pd.isna(r.ema20) or pd.isna(r.h4_close) or pd.isna(r.h4_ema50):
        return None
    up = r.h4_close > r.h4_ema50
    if up and p.Low <= p.ema20 and r.Close > r.ema20 and r.Close > r.Open:
        if r.Volume < 1.1 * (r.vol8 or 0):
            return None
        return _sig("COMPRA", r.Close, min(p.Low, r.Low) - _WIN_TICK, 1.5, 2.5, True)
    if (not up) and p.High >= p.ema20 and r.Close < r.ema20 and r.Close < r.Open:
        if r.Volume < 1.1 * (r.vol8 or 0):
            return None
        return _sig("VENDA", r.Close, max(p.High, r.High) + _WIN_TICK, 1.5, 2.5, True)
    return None


# ════════════════════════════════════════════════════════════════════════════
# CRYPTO — refinos + novos
# ════════════════════════════════════════════════════════════════════════════

def s_xau_nr7_h4(df, i, sym):
    base = s_xau_nr7(df, i, sym)
    if not base:
        return None
    r = df.iloc[i]
    if not _h4_ok(r, base["dir"]):
        return None
    return base


def s_xau_inside_h4(df, i, sym):
    base = s_xau_inside(df, i, sym)
    if not base:
        return None
    r = df.iloc[i]
    if not _h4_ok(r, base["dir"]):
        return None
    return base


def s_xau_wick_fade(df, i, sym):
    """Pavio ≥55% do range + |dn| fraco → fade (Londres/NY early)."""
    r = df.iloc[i]
    if not ((9 <= r.hh < 12) or (14.5 <= r.hh < 17)):
        return None
    body, rng, uw, dw = _parts(r)
    if rng <= 0:
        return None
    dn = r.dn if not pd.isna(r.dn) else 0
    if abs(dn) >= 0.22:
        return None
    atr = r.atr or 1
    if uw >= 0.55 * rng and r.Close < r.Open:
        return _sig("VENDA", r.Close, r.High + 0.25 * atr, 1.5, 2.5, True)
    if dw >= 0.55 * rng and r.Close > r.Open:
        return _sig("COMPRA", r.Close, r.Low - 0.25 * atr, 1.5, 2.5, True)
    return None


def s_xau_lh_body(df, i, sym):
    """LH 0.48/0.18 com corpo ≥60% e seg–qui."""
    base = make_london_handoff(0.48, 0.18, 1.75, (0, 1, 2, 3))(df, i, sym)
    if not base:
        return None
    r = df.iloc[i]
    body, rng, uw, dw = _parts(r)
    if rng <= 0 or body < 0.60 * rng:
        return None
    return base


def s_xau_of_vol(df, i, sym):
    """OF Londres com volume ≥1.3× vol8."""
    r = df.iloc[i]
    if not (9 <= r.hh < 11):
        return None
    if r.Volume < 1.3 * (r.vol8 or 0):
        return None
    return _of(df, i, sym, thr=0.34)


def s_xau_ny_sweep_fade(df, i, sym):
    """15–17h: sweep do high/low do dia + fechamento de volta → fade."""
    if i < 2:
        return None
    r = df.iloc[i]
    if not (15.0 <= r.hh < 17.0):
        return None
    if pd.isna(r.day_hi) or pd.isna(r.day_lo) or pd.isna(r.atr):
        return None
    body, rng, uw, dw = _parts(r)
    if rng <= 0:
        return None
    if r.High >= r.day_hi and r.Close < r.day_hi - 0.15 * r.atr and uw >= 0.40 * rng:
        return _sig("VENDA", r.Close, r.High + 0.2 * r.atr, 1.5, 2.5, True)
    if r.Low <= r.day_lo and r.Close > r.day_lo + 0.15 * r.atr and dw >= 0.40 * rng:
        return _sig("COMPRA", r.Close, r.Low - 0.2 * r.atr, 1.5, 2.5, True)
    return None


def s_xau_asia_h4(df, i, sym):
    base = make_asia_break(0.42, 0.58, 0.18, 1.75)(df, i, sym)
    if not base:
        return None
    r = df.iloc[i]
    if not _h4_ok(r, base["dir"]):
        return None
    return base


def s_eth_nr7_h4(df, i, sym):
    return s_xau_nr7_h4(df, i, sym)


def s_eth_rnd_07(df, i, sym):
    return make_round_fade(100, 0.07, 0.14)(df, i, sym)


def s_btc_rnd_tight(df, i, sym):
    return make_round_fade(500, 0.08, 0.15)(df, i, sym)


def s_xau_nr5(df, i, sym):
    return _nr_break(df, i, n=5, vol_mult=1.25, hh0=9.0, hh1=12.5)


# ════════════════════════════════════════════════════════════════════════════
# REGISTRO
# ════════════════════════════════════════════════════════════════════════════

# ── B3: família NR/INSIDE ──
_reg("b3", "NR4_BREAK", s_nr4_break, "WIN")
_reg("b3", "NR5_BREAK", s_nr5_break, "WIN")
_reg("b3", "NR7_H4_GAP", s_nr7_h4_gap, "WIN")
_reg("b3", "NR7_H4_AM", s_nr7_h4_am, "WIN")
_reg("b3", "NR7_VOL16", s_nr7_vol16, "WIN")
_reg("b3", "NR7_QUIET", s_nr7_quiet_atr, "WIN")
_reg("b3", "NR7_H4_MT", s_nr7_dow_mt, "WIN")
_reg("b3", "NR7_MORNING", s_win_nr7_morning, "WIN")
_reg("b3", "INSIDE_H4", s_inside_h4, "WIN")
_reg("b3", "INSIDE_H4_GAP", s_inside_h4_gap, "WIN")
_reg("b3", "INSIDE_AM", s_inside_am, "WIN")
_reg("b3", "INSIDE_V18_H4", s_inside_vol18_h4, "WIN")
_reg("b3", "DBL_INSIDE_H4", s_double_inside_h4, "WIN")

# ── B3: OVN / gap amarelos ──
_reg("b3", "OVN_PDC_038_EXT", make_ovn_pdc(0.38, 9.25, 9.75, True, 4), "WIN", one_per_day=True)
_reg("b3", "OVN_PDC_033_EXT_MT", make_ovn_pdc(0.33, 9.25, 9.75, True, 3), "WIN", one_per_day=True)
_reg("b3", "OVN_PDC_036_0935", make_ovn_pdc(0.36, 9.25, 9.6, True, 4), "WIN", one_per_day=True)
_reg("b3", "OVN_ATR_125_PDC", make_ovn_atr(1.25, 1.25, 9.25, 9.75, 3, True), "WIN", one_per_day=True)
_reg("b3", "OVN_ATR_15_MT", make_ovn_atr(1.5, 1.3, 9.25, 9.75, 2, False), "WIN", one_per_day=True)
_reg("b3", "GAP_FADE_032_RR2", make_gap_fade(0.32, 0.95, 9.5, 2.0, 3.0), "WIN", one_per_day=True)
_reg("b3", "GAP_CONT_FILL", s_gap_cont_after_fill, "WIN", one_per_day=True)
_reg("b3", "IB_BRK_16_GAP", make_ib_break(1.6, 10, 11.5, True), "WIN", one_per_day=True)

# ── B3: criativos novos ──
_reg("b3", "OUTSIDE_REV", s_outside_rev, "WIN")
_reg("b3", "PDH_BRK_VOL", s_pdh_break_vol, "WIN")
_reg("b3", "PDH_BRK_H4", s_pdh_break_h4, "WIN")
_reg("b3", "VWAP_PB_H4", s_vwap_pb_trend, "WIN")
_reg("b3", "ORB_MID_REC", s_orb_mid_reclaim, "WIN", one_per_day=True)
_reg("b3", "HL_BREAK", s_hl_break, "WIN")
_reg("b3", "BB_SQUEEZE", s_bb_squeeze_brk, "WIN")
_reg("b3", "LUNCH_FADE", s_lunch_fade, "WIN")
_reg("b3", "EMA_PB_H4", s_ema_ribbon_pb, "WIN")

# ── WDO espelhos ──
_reg("b3", "WDO_NR7_H4", s_win_nr7_trend, "WDO")
_reg("b3", "WDO_NR5", s_nr5_break, "WDO")
_reg("b3", "WDO_INSIDE_H4", s_inside_h4, "WDO")
_reg("b3", "WDO_VWAP_PB", s_vwap_pb_trend, "WDO")
_reg("b3", "WDO_PDH_H4", s_pdh_break_h4, "WDO")
_reg("b3", "WDO_OVN_036", make_ovn_pdc(0.36, 9.25, 9.75, True, 4), "WDO", one_per_day=True)

# ── Crypto XAU ──
_reg("crypto", "RND_FADE_06", make_round_fade(50, 0.06, 0.13), "XAUUSD")
_reg("crypto", "RND_FADE_08_LON", make_round_fade(50, 0.08, 0.15, 9, 13), "XAUUSD")
_reg("crypto", "RND_FADE_25", make_round_fade(25, 0.08, 0.14), "XAUUSD")
_reg("crypto", "RND_FADE_T_RR18", make_round_fade(50, 0.08, 0.15), "XAUUSD")  # baseline tight window check
_reg("crypto", "RND_FADE_MT", make_round_fade(50, 0.07, 0.14, None, None, (0, 1, 2, 3)), "XAUUSD")
_reg("crypto", "LH_046_D17_MT", make_london_handoff(0.46, 0.17, 1.75, (0, 1, 2, 3)), "XAUUSD", one_per_day=True)
_reg("crypto", "LH_049_D19", make_london_handoff(0.49, 0.19, 1.75), "XAUUSD", one_per_day=True)
_reg("crypto", "LH_048_BODY", s_xau_lh_body, "XAUUSD", one_per_day=True)
_reg("crypto", "LH_051_D18_TP2", make_london_handoff(0.51, 0.18, 2.0, (0, 1, 2, 3)), "XAUUSD", one_per_day=True)
_reg("crypto", "ASIA_040_D20", make_asia_break(0.40, 0.60, 0.20, 1.75), "XAUUSD", one_per_day=True)
_reg("crypto", "ASIA_H4", s_xau_asia_h4, "XAUUSD", one_per_day=True)
_reg("crypto", "OF_L_D36_VOL", s_xau_of_vol, "XAUUSD")
_reg("crypto", "OF_NY_D35_MT", make_of_window(15.5, 17.5, 0.35, (0, 1, 2, 3)), "XAUUSD")
_reg("crypto", "OF_L_D30_LV", make_of_window(9, 11, 0.30, None, 1.10), "XAUUSD")
_reg("crypto", "WICK_FADE", s_xau_wick_fade, "XAUUSD")
_reg("crypto", "NY_SWEEP_FADE", s_xau_ny_sweep_fade, "XAUUSD")
_reg("crypto", "XAU_NR7_H4", s_xau_nr7_h4, "XAUUSD")
_reg("crypto", "XAU_NR5", s_xau_nr5, "XAUUSD")
_reg("crypto", "XAU_INSIDE_H4", s_xau_inside_h4, "XAUUSD")

# ── ETH / BTC ──
_reg("crypto", "ETH_NR7_H4", s_eth_nr7_h4, "ETHUSD")
_reg("crypto", "ETH_INSIDE_H4", s_xau_inside_h4, "ETHUSD")
_reg("crypto", "ETH_RND_07", s_eth_rnd_07, "ETHUSD")
_reg("crypto", "ETH_LH_047", make_london_handoff(0.47, 0.18, 1.75, (0, 1, 2, 3)), "ETHUSD", one_per_day=True)
_reg("crypto", "ETH_OF_L_D32", make_of_window(9, 11, 0.32, (0, 1, 2, 3)), "ETHUSD")
_reg("crypto", "BTC_NR7_H4", s_xau_nr7_h4, "BTCUSD")
_reg("crypto", "BTC_RND_T", s_btc_rnd_tight, "BTCUSD")
_reg("crypto", "BTC_WICK_FADE", s_xau_wick_fade, "BTCUSD")
_reg("crypto", "BTC_LH_048", make_london_handoff(0.48, 0.18, 1.75), "BTCUSD", one_per_day=True)


def _sim(df, symbol, strat_fn, name, tf=15):
    from backtest_setups_novos_v2 import ONE_PER_DAY as _v2_opd
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
    logf = open(f"logs/backtest_setups_novos_v5_{args.grupo}_{stamp}.txt", "w", encoding="utf-8")

    def out(s):
        print(s)
        logf.write(s + "\n")
        logf.flush()

    strat_map = STRATS[args.grupo]
    out(f"{'#' * 74}\n# SETUPS NOVOS V5 ({len(strat_map)} setups) — {args.grupo} · "
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
        from backtest_crypto_pro import mt5_connect
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
            t.to_csv(f"logs/novos_v5_{k}_M{tf}_{name}.csv", index=False)
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
            if "🟢" in str(r.veredito) and "GO" in str(r.veredito):
                goes.append(r.setup)
    out(f"\n# 🟢 GO encontrados: {goes if goes else 'nenhum'}")
    out(f"# Critério: net≥+0,10 · PF≥1,25 · cons≥55% · OOS segura · n≥40")
    out(f"# FIM — logs/backtest_setups_novos_v5_{args.grupo}_{stamp}.txt")
    logf.close()


if __name__ == "__main__":
    main()
