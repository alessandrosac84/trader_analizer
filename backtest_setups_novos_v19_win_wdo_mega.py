"""
backtest_setups_novos_v19_win_wdo_mega.py — MEGA bateria WIN + WDO.

Famílias novas / refinamentos (não rewire de GOs já live):
  OUTSIDE · DBL_INS · H4_PB_EMA · IMP_CONT (WDO) · ORB · GAP fade/cont ·
  Engolfo · VWAP fade/cont · EMA reclaim · Squeeze · NR/Inside/HL/PDH
  em sessões AM/MID/PM/POWER/1014/1115 · TFs M5/M15/M30/H1

Régua GO (não baixar):
  net ≥ +0,10 R · PF ≥ 1,25 · cons ≥ 55% · OOS+ · n ≥ 40

Uso:
  python backtest_setups_novos_v19_win_wdo_mega.py
  python backtest_setups_novos_v19_win_wdo_mega.py --workers 6 --bars 300000
  python backtest_setups_novos_v19_win_wdo_mega.py --syms WIN --list-only
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
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

from backtest_kimi_real import add_indicators
from backtest_setups_novos import stats, consistency, verdict, OOS_FRAC, _sig, _line
from backtest_setups_novos_v2 import add_extra_v2, _simulate
from backtest_setups_novos_v5 import _h4_ok
from backtest_setups_novos_v9_refine import _mt
from backtest_setups_novos_v13_btc_24h import (
    _hh_ok, _inside_h, _nr_h, _impulse_h, _h4_pb_ema_h, _volspike_h,
    _outside_h, _hl_h, _dbl_inside_h,
)
from backtest_setups_novos_v14_btc_eth import (
    _engulf_h, _sqz_break_h, _ema_reclaim_h,
)
from backtest_setups_novos_v15_btc_eth import _vwap_fade_h, _vwap_cont_h, _cap_tp, _rr

SYMBOLS = ["WIN$D", "WDO$D"]

STRATS: dict = {}
STRAT_SYM: dict = {}
STRAT_TF: dict = {}
ONE_PER_DAY: set = set()
_WORKER_DFS: dict = {}


def _tag(sym: str) -> str:
    if sym.startswith("WIN"):
        return "WIN"
    if sym.startswith("WDO"):
        return "WDO"
    return sym.replace("$", "")[:6]


def _reg(name, fn, sym, tf=15, one_per_day=False):
    STRATS[name] = fn
    STRAT_SYM[name] = sym
    STRAT_TF[name] = tf
    if one_per_day:
        ONE_PER_DAY.add(name)


def _b3_sess(fn, hh0=10.0, hh1=16.5):
    """Pregão B3 (seg–sex) + janela horária."""
    def _f(df, i, sym):
        r = df.iloc[i]
        if int(getattr(r, "dow", 0) or 0) >= 5:
            return None
        if not _hh_ok(r.hh, hh0, hh1):
            return None
        return fn(df, i, sym)
    return _f


def _gap_atr_aligned(fn, min_atr=0.25, same_dir=True):
    def _f(df, i, sym):
        r = df.iloc[i]
        g = float(r.gap_atr) if not pd.isna(getattr(r, "gap_atr", np.nan)) else 0.0
        if abs(g) < min_atr:
            return None
        base = fn(df, i, sym)
        if not base:
            return None
        if same_dir:
            if g > 0 and base["dir"] != "COMPRA":
                return None
            if g < 0 and base["dir"] != "VENDA":
                return None
        else:
            if g > 0 and base["dir"] != "VENDA":
                return None
            if g < 0 and base["dir"] != "COMPRA":
                return None
        return base
    return _f


# ── Setups específicos B3 futuros ───────────────────────────────────────────

def _orb30_h(vol=1.2, hh0=10.75, hh1=12.5, rr1=1.5, rr2=2.0, gap_dir=False):
    def _f(df, i, sym):
        if i < 5:
            return None
        r, p = df.iloc[i], df.iloc[i - 1]
        if not _hh_ok(r.hh, hh0, hh1):
            return None
        if pd.isna(r.orb_hi) or pd.isna(r.orb_lo) or float(r.atr or 0) <= 0:
            return None
        if (float(r.orb_hi) - float(r.orb_lo)) < 0.35 * float(r.atr):
            return None
        if float(r.Volume or 0) < vol * float(r.vol8 or 0):
            return None
        g = float(r.gap_atr) if not pd.isna(getattr(r, "gap_atr", np.nan)) else 0.0
        if r.Close > r.orb_hi and p.Close <= r.orb_hi:
            if gap_dir and g < -0.1:
                return None
            d, sl = "COMPRA", float(r.orb_lo)
        elif r.Close < r.orb_lo and p.Close >= r.orb_lo:
            if gap_dir and g > 0.1:
                return None
            d, sl = "VENDA", float(r.orb_hi)
        else:
            return None
        if not _h4_ok(r, d):
            return None
        return _sig(d, float(r.Close), sl, rr1, rr2, True)
    return _f


def _failed_orb_h(vol=1.2, hh0=10.75, hh1=13.0):
    """Rompimento ORB que falha (fecha de volta) → reversão."""
    def _f(df, i, sym):
        if i < 4:
            return None
        r, p = df.iloc[i], df.iloc[i - 1]
        if not _hh_ok(r.hh, hh0, hh1):
            return None
        if pd.isna(r.orb_hi) or pd.isna(r.orb_lo) or float(r.atr or 0) <= 0:
            return None
        if float(r.Volume or 0) < vol * float(r.vol8 or 0):
            return None
        # rompeu cima e voltou
        if float(p.Close) > float(r.orb_hi) and float(r.Close) < float(r.orb_hi) and float(r.Close) < float(r.Open):
            d, sl = "VENDA", float(max(p.High, r.High))
        elif float(p.Close) < float(r.orb_lo) and float(r.Close) > float(r.orb_lo) and float(r.Close) > float(r.Open):
            d, sl = "COMPRA", float(min(p.Low, r.Low))
        else:
            return None
        if not _h4_ok(r, d):
            return None
        return _sig(d, float(r.Close), sl, 1.5, 2.0, True)
    return _f


def _gap_fade_atr(min_atr=0.35, hh0=9.25, hh1=10.5, vol=1.1):
    def _f(df, i, sym):
        if i < 3:
            return None
        r, p = df.iloc[i], df.iloc[i - 1]
        if not _hh_ok(r.hh, hh0, hh1):
            return None
        g = float(r.gap_atr) if not pd.isna(getattr(r, "gap_atr", np.nan)) else 0.0
        if abs(g) < min_atr or float(r.atr or 0) <= 0:
            return None
        if float(r.Volume or 0) < vol * float(r.vol8 or 0):
            return None
        if g >= min_atr and float(r.Close) < float(r.Open) and float(r.Close) < float(p.Close):
            d, sl = "VENDA", float(max(p.High, r.High, r.day_open))
        elif g <= -min_atr and float(r.Close) > float(r.Open) and float(r.Close) > float(p.Close):
            d, sl = "COMPRA", float(min(p.Low, r.Low, r.day_open))
        else:
            return None
        return _sig(d, float(r.Close), sl, 1.5, 2.0, True)
    return _f


def _gap_cont_atr(min_atr=0.35, hh0=10.0, hh1=12.0, vol=1.2):
    def _f(df, i, sym):
        if i < 3:
            return None
        r, p = df.iloc[i], df.iloc[i - 1]
        if not _hh_ok(r.hh, hh0, hh1):
            return None
        g = float(r.gap_atr) if not pd.isna(getattr(r, "gap_atr", np.nan)) else 0.0
        if abs(g) < min_atr or float(r.atr or 0) <= 0:
            return None
        if float(r.Volume or 0) < vol * float(r.vol8 or 0):
            return None
        if g >= min_atr and float(r.Close) > float(p.High) and float(r.Close) > float(r.day_open):
            d, sl = "COMPRA", float(min(p.Low, r.day_open))
        elif g <= -min_atr and float(r.Close) < float(p.Low) and float(r.Close) < float(r.day_open):
            d, sl = "VENDA", float(max(p.High, r.day_open))
        else:
            return None
        if not _h4_ok(r, d):
            return None
        return _sig(d, float(r.Close), sl, 1.5, 2.5, True)
    return _f


def _pdh_h(vol=1.2, hh0=10.0, hh1=16.0, rr1=1.5, rr2=2.0):
    def _f(df, i, sym):
        if i < 30:
            return None
        r, p = df.iloc[i], df.iloc[i - 1]
        if not _hh_ok(r.hh, hh0, hh1):
            return None
        if float(r.Volume or 0) < vol * float(r.vol8 or 0):
            return None
        pdh = getattr(r, "prev_day_hi", None)
        pdl = getattr(r, "prev_day_lo", None)
        if pdh is None or pdl is None or pd.isna(pdh) or pd.isna(pdl):
            return None
        pdh, pdl = float(pdh), float(pdl)
        if r.Close > pdh and p.Close <= pdh:
            d, sl = "COMPRA", float(p.Low)
        elif r.Close < pdl and p.Close >= pdl:
            d, sl = "VENDA", float(p.High)
        else:
            return None
        if not _h4_ok(r, d):
            return None
        return _sig(d, float(r.Close), sl, rr1, rr2, True)
    return _f


def _vwap_open_h(vol=1.2, hh0=10.5, hh1=15.0):
    def _f(df, i, sym):
        if i < 5:
            return None
        r, p = df.iloc[i], df.iloc[i - 1]
        if not _hh_ok(r.hh, hh0, hh1):
            return None
        if pd.isna(r.vwap) or float(r.atr or 0) <= 0:
            return None
        if float(r.Volume or 0) < vol * float(r.vol8 or 0):
            return None
        if (float(r.Close) > float(r.day_open)
                and float(p.Close) < float(p.vwap)
                and float(r.Close) > float(r.vwap)
                and float(r.Close) > float(r.Open)):
            d, sl = "COMPRA", float(min(p.Low, r.Low))
        elif (float(r.Close) < float(r.day_open)
              and float(p.Close) > float(p.vwap)
              and float(r.Close) < float(r.vwap)
              and float(r.Close) < float(r.Open)):
            d, sl = "VENDA", float(max(p.High, r.High))
        else:
            return None
        if not _h4_ok(r, d):
            return None
        return _sig(d, float(r.Close), sl, 1.5, 2.0, True)
    return _f


def _ib_break_h(vol=1.3, hh0=10.0, hh1=12.0):
    def _f(df, i, sym):
        if i < 5:
            return None
        r, p = df.iloc[i], df.iloc[i - 1]
        if not _hh_ok(r.hh, hh0, hh1):
            return None
        if pd.isna(getattr(r, "ib_hi", np.nan)) or pd.isna(getattr(r, "ib_lo", np.nan)):
            return None
        if float(r.Volume or 0) < vol * float(r.vol8 or 0):
            return None
        if r.Close > r.ib_hi and p.Close <= r.ib_hi:
            d, sl = "COMPRA", float(r.ib_lo)
        elif r.Close < r.ib_lo and p.Close >= r.ib_lo:
            d, sl = "VENDA", float(r.ib_hi)
        else:
            return None
        if not _h4_ok(r, d):
            return None
        return _sig(d, float(r.Close), sl, 1.5, 2.0, True)
    return _f


def _lunch_rev_h(vol=1.2, hh0=13.0, hh1=15.0):
    """Reversão do extremo da manhã após almoço."""
    def _f(df, i, sym):
        if i < 10:
            return None
        r, p = df.iloc[i], df.iloc[i - 1]
        if not _hh_ok(r.hh, hh0, hh1):
            return None
        if float(r.Volume or 0) < vol * float(r.vol8 or 0):
            return None
        if float(r.atr or 0) <= 0:
            return None
        # manhã = 10–12.5
        day = r.day
        am = df[(df["day"] == day) & (df["hh"] >= 10.0) & (df["hh"] < 12.5)]
        if len(am) < 4:
            return None
        am_hi, am_lo = float(am.High.max()), float(am.Low.min())
        # tarde rejeita máx manhã → venda
        if float(r.High) >= am_hi and float(r.Close) < am_hi and float(r.Close) < float(r.Open):
            d, sl = "VENDA", float(max(p.High, r.High))
        elif float(r.Low) <= am_lo and float(r.Close) > am_lo and float(r.Close) > float(r.Open):
            d, sl = "COMPRA", float(min(p.Low, r.Low))
        else:
            return None
        if not _h4_ok(r, d):
            return None
        return _sig(d, float(r.Close), sl, 1.5, 2.0, True)
    return _f


# Sessões pregão B3
SESSIONS_FULL = [
    ("DAY", 10.0, 16.5),
    ("1015", 10.0, 15.0),
]
SESSIONS_LIGHT = [
    ("AM", 10.0, 12.5),
    ("MID", 11.0, 14.0),
    ("PM", 13.0, 16.0),
    ("POWER", 14.5, 16.5),
    ("1014", 10.0, 14.0),
    ("1115", 11.0, 15.0),
]


# TFs ativos no build (setado em main / --tfs)
_ACTIVE_TFS = {5, 15, 30, 60}


def _build_registry(syms: list[str], tfs=None):
    STRATS.clear()
    STRAT_SYM.clear()
    STRAT_TF.clear()
    ONE_PER_DAY.clear()
    active = set(tfs) if tfs is not None else set(_ACTIVE_TFS)

    for sx in syms:
        tag = _tag(sx)

        # ── DAY / 1015: cobertura completa ──
        for stag, h0, h1 in SESSIONS_FULL:
            for vol, vtag in ((1.2, ""), (1.35, "V135"), (1.5, "V15")):
                suf = f"_{vtag}" if vtag else ""
                _reg(f"{tag}_INS_{stag}{suf}",
                     _b3_sess(_inside_h(vol, h0, h1), h0, h1), sx)
            for n in (4, 5, 6, 7):
                _reg(f"{tag}_NR{n}_{stag}",
                     _b3_sess(_nr_h(n, 1.3, h0, h1), h0, h1), sx)
                _reg(f"{tag}_NR{n}_{stag}_MT",
                     _b3_sess(_mt(_nr_h(n, 1.3, h0, h1)), h0, h1), sx)
                _reg(f"{tag}_NR{n}_{stag}_V14",
                     _b3_sess(_nr_h(n, 1.4, h0, h1), h0, h1), sx)
            _reg(f"{tag}_HL_{stag}",
                 _b3_sess(_hl_h(1.3, h0, h1), h0, h1), sx)
            _reg(f"{tag}_HL_{stag}_MT",
                 _b3_sess(_mt(_hl_h(1.4, h0, h1)), h0, h1), sx)
            _reg(f"{tag}_HL_{stag}_V15",
                 _b3_sess(_hl_h(1.5, h0, h1), h0, h1), sx)
            _reg(f"{tag}_IMP_{stag}",
                 _b3_sess(_impulse_h(2.0, 1.5, h0, h1, 2.0), h0, h1), sx)
            _reg(f"{tag}_IMP_{stag}_MT",
                 _b3_sess(_mt(_impulse_h(1.8, 1.6, h0, h1, 2.0)), h0, h1), sx)
            _reg(f"{tag}_IMP_{stag}_A22",
                 _b3_sess(_impulse_h(2.2, 1.5, h0, h1, 2.0), h0, h1), sx)
            _reg(f"{tag}_OUT_{stag}",
                 _b3_sess(_outside_h(1.3, h0, h1), h0, h1), sx)
            _reg(f"{tag}_OUT_{stag}_MT",
                 _b3_sess(_mt(_outside_h(1.3, h0, h1)), h0, h1), sx)
            _reg(f"{tag}_OUT_{stag}_V15",
                 _b3_sess(_outside_h(1.5, h0, h1), h0, h1), sx)
            _reg(f"{tag}_DBL_{stag}",
                 _b3_sess(_dbl_inside_h(1.3, h0, h1), h0, h1), sx)
            _reg(f"{tag}_DBL_{stag}_MT",
                 _b3_sess(_mt(_dbl_inside_h(1.3, h0, h1)), h0, h1), sx)
            _reg(f"{tag}_ENG_{stag}",
                 _b3_sess(_engulf_h(1.3, h0, h1), h0, h1), sx)
            _reg(f"{tag}_ENG_{stag}_MT",
                 _b3_sess(_mt(_engulf_h(1.4, h0, h1)), h0, h1), sx)
            _reg(f"{tag}_EMA_{stag}",
                 _b3_sess(_ema_reclaim_h(h0, h1, 1.2), h0, h1), sx)
            _reg(f"{tag}_EMA_{stag}_MT",
                 _b3_sess(_mt(_ema_reclaim_h(h0, h1, 1.3)), h0, h1), sx)
            _reg(f"{tag}_SQZ_{stag}",
                 _b3_sess(_sqz_break_h(1.4, h0, h1), h0, h1), sx)
            _reg(f"{tag}_H4PB_{stag}",
                 _b3_sess(_h4_pb_ema_h(h0, h1), h0, h1), sx)
            _reg(f"{tag}_H4PB_{stag}_MT",
                 _b3_sess(_mt(_h4_pb_ema_h(h0, h1)), h0, h1), sx)
            _reg(f"{tag}_PDH_{stag}",
                 _b3_sess(_pdh_h(1.2, h0, h1), h0, h1), sx, one_per_day=True)
            _reg(f"{tag}_PDH_{stag}_MT",
                 _b3_sess(_mt(_pdh_h(1.3, h0, h1)), h0, h1), sx, one_per_day=True)
            _reg(f"{tag}_PDH_{stag}_V14",
                 _b3_sess(_pdh_h(1.4, h0, h1), h0, h1), sx, one_per_day=True)
            _reg(f"{tag}_VWAPF_{stag}",
                 _b3_sess(_vwap_fade_h(1.2, 1.1, h0, h1), h0, h1), sx)
            _reg(f"{tag}_VWAPC_{stag}",
                 _b3_sess(_vwap_cont_h(1.2, 0.3, h0, h1), h0, h1), sx)
            _reg(f"{tag}_VWAPC_{stag}_MT",
                 _b3_sess(_mt(_vwap_cont_h(1.3, 0.25, h0, h1)), h0, h1), sx)

        # ── AM / MID / PM / POWER / 1014 / 1115: subset ──
        for stag, h0, h1 in SESSIONS_LIGHT:
            _reg(f"{tag}_INS_{stag}",
                 _b3_sess(_inside_h(1.3, h0, h1), h0, h1), sx)
            _reg(f"{tag}_INS_{stag}_MT",
                 _b3_sess(_mt(_inside_h(1.35, h0, h1)), h0, h1), sx)
            _reg(f"{tag}_NR5_{stag}",
                 _b3_sess(_nr_h(5, 1.3, h0, h1), h0, h1), sx)
            _reg(f"{tag}_NR5_{stag}_MT",
                 _b3_sess(_mt(_nr_h(5, 1.35, h0, h1)), h0, h1), sx)
            _reg(f"{tag}_NR4_{stag}",
                 _b3_sess(_nr_h(4, 1.3, h0, h1), h0, h1), sx)
            _reg(f"{tag}_NR7_{stag}",
                 _b3_sess(_nr_h(7, 1.3, h0, h1), h0, h1), sx)
            _reg(f"{tag}_HL_{stag}",
                 _b3_sess(_hl_h(1.3, h0, h1), h0, h1), sx)
            _reg(f"{tag}_HL_{stag}_MT",
                 _b3_sess(_mt(_hl_h(1.4, h0, h1)), h0, h1), sx)
            _reg(f"{tag}_IMP_{stag}",
                 _b3_sess(_impulse_h(2.0, 1.5, h0, h1, 2.0), h0, h1), sx)
            _reg(f"{tag}_IMP_{stag}_MT",
                 _b3_sess(_mt(_impulse_h(1.8, 1.5, h0, h1, 2.0)), h0, h1), sx)
            _reg(f"{tag}_OUT_{stag}",
                 _b3_sess(_outside_h(1.3, h0, h1), h0, h1), sx)
            _reg(f"{tag}_OUT_{stag}_MT",
                 _b3_sess(_mt(_outside_h(1.35, h0, h1)), h0, h1), sx)
            _reg(f"{tag}_DBL_{stag}",
                 _b3_sess(_dbl_inside_h(1.3, h0, h1), h0, h1), sx)
            _reg(f"{tag}_ENG_{stag}",
                 _b3_sess(_engulf_h(1.3, h0, h1), h0, h1), sx)
            _reg(f"{tag}_PDH_{stag}",
                 _b3_sess(_pdh_h(1.2, h0, h1), h0, h1), sx, one_per_day=True)
            _reg(f"{tag}_H4PB_{stag}",
                 _b3_sess(_h4_pb_ema_h(h0, h1), h0, h1), sx)
            _reg(f"{tag}_VWAPC_{stag}",
                 _b3_sess(_vwap_cont_h(1.2, 0.3, h0, h1), h0, h1), sx)
            _reg(f"{tag}_EMA_{stag}",
                 _b3_sess(_ema_reclaim_h(h0, h1, 1.2), h0, h1), sx)

        # ORB / Failed ORB / IB
        for vol, vtag in ((1.1, "V11"), (1.3, "V13"), (1.5, "V15")):
            _reg(f"{tag}_ORB30_{vtag}",
                 _orb30_h(vol, 10.75, 12.5), sx, one_per_day=True)
            _reg(f"{tag}_ORB30_GAP_{vtag}",
                 _orb30_h(vol, 10.75, 12.5, gap_dir=True), sx, one_per_day=True)
            _reg(f"{tag}_ORB30_{vtag}_MT",
                 _mt(_orb30_h(vol, 10.75, 12.5)), sx, one_per_day=True)
        _reg(f"{tag}_ORB30_1114",
             _orb30_h(1.3, 11.0, 14.0), sx, one_per_day=True)
        _reg(f"{tag}_FORB_V12",
             _failed_orb_h(1.2, 10.75, 13.0), sx, one_per_day=True)
        _reg(f"{tag}_FORB_V14",
             _failed_orb_h(1.4, 10.75, 13.5), sx, one_per_day=True)
        _reg(f"{tag}_FORB_MT",
             _mt(_failed_orb_h(1.3, 10.75, 13.0)), sx, one_per_day=True)
        _reg(f"{tag}_IB_BRK",
             _ib_break_h(1.3, 10.0, 12.0), sx, one_per_day=True)
        _reg(f"{tag}_IB_BRK_MT",
             _mt(_ib_break_h(1.4, 10.0, 12.5)), sx, one_per_day=True)
        _reg(f"{tag}_IB_BRK_V15",
             _ib_break_h(1.5, 10.0, 13.0), sx, one_per_day=True)

        # GAP fade / cont (ATR)
        for ga, gtag in ((0.30, "A30"), (0.45, "A45"), (0.60, "A60"), (0.80, "A80")):
            _reg(f"{tag}_GAPF_{gtag}",
                 _gap_fade_atr(ga, 9.25, 10.5), sx, one_per_day=True)
            _reg(f"{tag}_GAPF_{gtag}_MT",
                 _mt(_gap_fade_atr(ga, 9.25, 10.75)), sx, one_per_day=True)
            _reg(f"{tag}_GAPC_{gtag}",
                 _gap_cont_atr(ga, 10.0, 12.0), sx, one_per_day=True)
            _reg(f"{tag}_GAPC_{gtag}_MT",
                 _mt(_gap_cont_atr(ga, 10.0, 12.5)), sx, one_per_day=True)

        # VWAP open / lunch / volspike
        _reg(f"{tag}_VWAP_OPEN", _vwap_open_h(1.2, 10.5, 15.0), sx)
        _reg(f"{tag}_VWAP_OPEN_MT", _mt(_vwap_open_h(1.3, 10.5, 15.0)), sx)
        _reg(f"{tag}_LUNCH_REV", _lunch_rev_h(1.2, 13.0, 15.0), sx, one_per_day=True)
        _reg(f"{tag}_LUNCH_REV_MT", _mt(_lunch_rev_h(1.3, 13.0, 15.5)), sx,
             one_per_day=True)
        _reg(f"{tag}_VOLSPIKE_AM",
             _b3_sess(_volspike_h(2.0, 1.5, 10.0, 12.5), 10, 12.5), sx)
        _reg(f"{tag}_VOLSPIKE_PM",
             _b3_sess(_volspike_h(2.0, 1.5, 13.0, 16.0), 13, 16), sx)

        # TP / RR variants (DAY)
        for base_name, base_fn, opd in (
            (f"{tag}_INS_DAY_TP20", _cap_tp(_inside_h(1.3, 10, 16.5), 2.0), False),
            (f"{tag}_NR5_DAY_TP20", _cap_tp(_nr_h(5, 1.3, 10, 16.5), 2.0), False),
            (f"{tag}_OUT_DAY_TP20", _cap_tp(_outside_h(1.3, 10, 16.5), 2.0), False),
            (f"{tag}_DBL_DAY_TP20", _cap_tp(_dbl_inside_h(1.3, 10, 16.5), 2.0), False),
            (f"{tag}_HL_DAY_TP20", _cap_tp(_hl_h(1.3, 10, 16.5), 2.0), False),
            (f"{tag}_IMP_DAY_TP25", _impulse_h(2.0, 1.5, 10, 16.5, 2.5), False),
            (f"{tag}_H4PB_DAY_TP20", _cap_tp(_h4_pb_ema_h(10, 16.5), 2.0), False),
            (f"{tag}_ENG_DAY_TP20", _cap_tp(_engulf_h(1.3, 10, 16.5), 2.0), False),
            (f"{tag}_ORB30_TP15", _orb30_h(1.3, 10.75, 12.5, 1.5, 1.5), True),
            (f"{tag}_INS_DAY_RR20", _rr(_inside_h(1.3, 10, 16.5), 2.0, 3.0), False),
            (f"{tag}_OUT_DAY_RR20", _rr(_outside_h(1.3, 10, 16.5), 2.0, 3.0), False),
            (f"{tag}_DBL_DAY_RR20", _rr(_dbl_inside_h(1.3, 10, 16.5), 2.0, 3.0), False),
        ):
            _reg(base_name, _b3_sess(base_fn, 10, 16.5), sx, one_per_day=opd)

        # Gap-aligned structure
        _reg(f"{tag}_INS_GAP_DAY",
             _gap_atr_aligned(_b3_sess(_inside_h(1.3, 10, 16.5), 10, 16.5), 0.25, True), sx)
        _reg(f"{tag}_NR5_GAP_DAY",
             _gap_atr_aligned(_b3_sess(_nr_h(5, 1.3, 10, 16.5), 10, 16.5), 0.25, True), sx)
        _reg(f"{tag}_OUT_GAP_DAY",
             _gap_atr_aligned(_b3_sess(_outside_h(1.3, 10, 16.5), 10, 16.5), 0.25, True), sx)
        _reg(f"{tag}_HL_GAP_DAY",
             _gap_atr_aligned(_b3_sess(_hl_h(1.3, 10, 16.5), 10, 16.5), 0.25, True), sx)
        _reg(f"{tag}_IMP_GAP_DAY",
             _gap_atr_aligned(_b3_sess(_impulse_h(2.0, 1.5, 10, 16.5, 2.0), 10, 16.5),
                             0.25, True), sx)
        _reg(f"{tag}_INS_FADEGAP_DAY",
             _gap_atr_aligned(_b3_sess(_inside_h(1.3, 10, 16.5), 10, 16.5), 0.30, False), sx)

        # Multi-TF: M30 + H1 (core) · M5 subset — respeita --tfs
        if 30 in active or 60 in active:
            for tf, tf_lab in ((30, "M30"), (60, "H1")):
                if tf not in active:
                    continue
                _reg(f"{tag}_INS_DAY_{tf_lab}",
                     _b3_sess(_inside_h(1.3, 10, 16.5), 10, 16.5), sx, tf=tf)
                _reg(f"{tag}_NR5_DAY_{tf_lab}",
                     _b3_sess(_nr_h(5, 1.3, 10, 16.5), 10, 16.5), sx, tf=tf)
                _reg(f"{tag}_NR4_DAY_{tf_lab}",
                     _b3_sess(_nr_h(4, 1.3, 10, 16.5), 10, 16.5), sx, tf=tf)
                _reg(f"{tag}_HL_DAY_{tf_lab}",
                     _b3_sess(_hl_h(1.3, 10, 16.5), 10, 16.5), sx, tf=tf)
                _reg(f"{tag}_IMP_DAY_{tf_lab}",
                     _b3_sess(_impulse_h(2.0, 1.5, 10, 16.5, 2.0), 10, 16.5), sx, tf=tf)
                _reg(f"{tag}_OUT_DAY_{tf_lab}",
                     _b3_sess(_outside_h(1.3, 10, 16.5), 10, 16.5), sx, tf=tf)
                _reg(f"{tag}_DBL_DAY_{tf_lab}",
                     _b3_sess(_dbl_inside_h(1.3, 10, 16.5), 10, 16.5), sx, tf=tf)
                _reg(f"{tag}_PDH_DAY_{tf_lab}",
                     _b3_sess(_pdh_h(1.2, 10, 16.5), 10, 16.5), sx, tf=tf, one_per_day=True)
                _reg(f"{tag}_H4PB_DAY_{tf_lab}",
                     _b3_sess(_h4_pb_ema_h(10, 16.5), 10, 16.5), sx, tf=tf)
                _reg(f"{tag}_ENG_DAY_{tf_lab}",
                     _b3_sess(_engulf_h(1.3, 10, 16.5), 10, 16.5), sx, tf=tf)
                _reg(f"{tag}_EMA_DAY_{tf_lab}",
                     _b3_sess(_ema_reclaim_h(10, 16.5, 1.2), 10, 16.5), sx, tf=tf)
                _reg(f"{tag}_ORB30_{tf_lab}",
                     _orb30_h(1.3, 10.75, 13.0), sx, tf=tf, one_per_day=True)
                _reg(f"{tag}_GAPC_A45_{tf_lab}",
                     _gap_cont_atr(0.45, 10.0, 13.0), sx, tf=tf, one_per_day=True)
                _reg(f"{tag}_VWAPC_DAY_{tf_lab}",
                     _b3_sess(_vwap_cont_h(1.2, 0.3, 10, 16.5), 10, 16.5), sx, tf=tf)

        if 5 in active:
            for fam, fn in (
                ("INS", _inside_h(1.3, 10, 16.5)),
                ("NR5", _nr_h(5, 1.3, 10, 16.5)),
                ("IMP", _impulse_h(2.0, 1.5, 10, 16.5, 2.0)),
                ("OUT", _outside_h(1.3, 10, 16.5)),
                ("DBL", _dbl_inside_h(1.3, 10, 16.5)),
                ("HL", _hl_h(1.3, 10, 16.5)),
                ("ENG", _engulf_h(1.3, 10, 16.5)),
                ("H4PB", _h4_pb_ema_h(10, 16.5)),
            ):
                _reg(f"{tag}_{fam}_DAY_M5",
                     _b3_sess(fn, 10, 16.5), sx, tf=5)
                _reg(f"{tag}_{fam}_AM_M5",
                     _b3_sess(fn if fam != "H4PB" else _h4_pb_ema_h(10, 12.5),
                              10, 12.5), sx, tf=5)

    return len(STRATS)


def _sim(df, sym, fn, name, tf=15):
    # ONE_PER_DAY já pré-registrado no v2 em _worker_init (thread-safe)
    return _simulate(df, sym, fn, name, tf=tf)


def _load_ohlc(sym: str, tf: int, bars: int, out):
    """MT5 primeiro; fallback edge_discovery/db/{WIN|WDO}_D_M*.csv.gz."""
    os.environ["KIMI_GROUP"] = "b3"
    try:
        from backtest_kimi_real import fetch as _b3_fetch
        raw = _b3_fetch(sym, tf, bars)
        if raw is not None and len(raw) >= 1500:
            return raw
    except Exception as exc:
        out(f"  MT5 {sym} M{tf}: {exc}")

    base = "WIN_D" if sym.startswith("WIN") else "WDO_D"
    for cand in (
        Path(f"edge_discovery/db/{base}_M{tf}.csv.gz"),
        Path(f"data/{base}_M{tf}.csv.gz"),
    ):
        if not cand.exists():
            continue
        df = pd.read_csv(cand)
        cols = {c.lower(): c for c in df.columns}
        if not all(k in cols for k in ("open", "high", "low", "close")):
            continue
        tcol = cols.get("time") or cols.get("datetime") or cols.get("date")
        if tcol:
            df["time"] = pd.to_datetime(df[tcol])
            df = df.set_index("time")
        volc = cols.get("volume") or cols.get("tick_volume") or cols.get("real_volume")
        out(f"  fallback CSV: {cand}")
        return pd.DataFrame({
            "Open": df[cols["open"]], "High": df[cols["high"]],
            "Low": df[cols["low"]], "Close": df[cols["close"]],
            "Volume": df[volc] if volc else 1.0,
        }, index=df.index)
    return None


def _worker_init(dfs_by_key, want_syms, tfs=None):
    global _WORKER_DFS
    _WORKER_DFS = dfs_by_key
    _build_registry(list(want_syms), tfs=tfs)
    from backtest_setups_novos_v2 import ONE_PER_DAY as _v2_opd
    _v2_opd.update(ONE_PER_DAY)


def _run_one(name: str) -> dict:
    try:
        if name not in STRATS:
            return {"setup": name, "sym": "?", "tf": 15, "n": 0,
                    "veredito": "SEM REGISTRO"}
        sym = STRAT_SYM[name]
        tf = STRAT_TF.get(name, 15)
        key = f"{_tag(sym)}_M{tf}"
        df = _WORKER_DFS.get(key)
        if df is None:
            return {"setup": name, "sym": _tag(sym), "tf": tf, "n": 0,
                    "veredito": "SEM DADOS"}
        fn = STRATS[name]
        t0 = time.time()
        t = _sim(df, sym, fn, name, tf=tf)
        s = stats(t)
        cons = consistency(t)
        s_out = {"n": 0, "net": None, "PF": None}
        if not t.empty:
            t = t.sort_values("ts").reset_index(drop=True)
            cut = int(len(t) * (1 - OOS_FRAC))
            s_out = stats(t.iloc[cut:])
        v = verdict(s, cons, s_out)
        row = {
            "setup": name, "sym": _tag(sym), "tf": tf, **s,
            "cons%": cons, "veredito": v,
            "oos_net": s_out.get("net"), "oos_pf": s_out.get("PF"),
            "oos_n": s_out.get("n", 0),
            "secs": round(time.time() - t0, 1),
        }
        if "🟢" in str(v) and "GO" in str(v) and not t.empty:
            row["_trades"] = t
        return row
    except Exception as exc:
        return {"setup": name, "sym": _tag(STRAT_SYM.get(name, "?")),
                "tf": STRAT_TF.get(name, 15), "n": 0,
                "veredito": f"ERRO: {exc}", "err": traceback.format_exc()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bars", type=int, default=300000)
    ap.add_argument("--syms", default="WIN,WDO")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--tfs", default="15,30,60",
                    help="TFs a incluir (ex: 15 ou 5,15,30,60)")
    ap.add_argument("--only", default="", help="csv de nomes (debug)")
    ap.add_argument("--list-only", action="store_true")
    args = ap.parse_args()

    want = []
    for s in args.syms.split(","):
        s = s.strip().upper()
        if not s:
            continue
        if s in ("WIN", "WIN$D"):
            want.append("WIN$D")
        elif s in ("WDO", "WDO$D"):
            want.append("WDO$D")
        else:
            want.append(s)

    tfs = [int(x.strip()) for x in args.tfs.split(",") if x.strip()]
    global _ACTIVE_TFS
    _ACTIVE_TFS = set(tfs)
    n_reg = _build_registry(want, tfs=tfs)
    only = {x.strip() for x in args.only.split(",") if x.strip()}
    names = [n for n in STRATS if STRAT_SYM[n] in want and (not only or n in only)]
    # M15 é default das famílias DAY — só filtra se 15 não estiver nos tfs
    if 15 not in tfs:
        names = [n for n in names if STRAT_TF.get(n, 15) in tfs]
    else:
        names = [n for n in names if STRAT_TF.get(n, 15) in tfs]

    if args.list_only:
        from collections import Counter
        print(f"TOTAL {len(names)}")
        print(dict(Counter(_tag(STRAT_SYM[n]) for n in names)))
        print(dict(Counter(STRAT_TF.get(n, 15) for n in names)))
        return

    os.environ["KIMI_GROUP"] = "b3"
    os.makedirs("logs", exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    log_path = f"logs/backtest_setups_novos_v19_win_wdo_mega_{stamp}.txt"
    logf = open(log_path, "w", encoding="utf-8")

    def out(s=""):
        try:
            print(s, flush=True)
        except UnicodeEncodeError:
            print(s.encode("ascii", "replace").decode("ascii"), flush=True)
        logf.write(s + "\n")
        logf.flush()

    from collections import Counter
    out(f"{'#' * 76}")
    out(f"# BACKTEST WIN/WDO V19 MEGA — {len(names)} setups · "
        f"{datetime.now():%d/%m/%Y %H:%M}")
    out(f"# Ativos: {want}")
    out(f"# Por ativo: {dict(Counter(_tag(STRAT_SYM[n]) for n in names))}")
    out(f"# TFs: {dict(Counter(STRAT_TF.get(n, 15) for n in names))}")
    out(f"# GO: net≥+0.10R · PF≥1.25 · cons≥55% · OOS · n≥40")
    out(f"# Custos B3 reais · EOD virada de dia · workers={args.workers}")
    out(f"# NÃO altera live até GO · não corta paths wired")
    out(f"{'#' * 76}")

    need_keys = sorted({f"{_tag(STRAT_SYM[n])}_M{STRAT_TF.get(n, 15)}" for n in names})
    dfs = {}
    for key in need_keys:
        tag, _, tf_s = key.partition("_M")
        tf = int(tf_s)
        sym = "WIN$D" if tag == "WIN" else "WDO$D"
        t0 = time.time()
        raw = _load_ohlc(sym, tf, args.bars, out)
        if raw is None or len(raw) < 1500:
            out(f"\n!! {key}: sem histórico")
            continue
        df = add_extra_v2(add_indicators(raw), sym, ctx=None)
        dfs[key] = df
        bpd = {5: 84, 15: 28, 30: 14, 60: 7}.get(tf, 28)
        anos = len(df) / bpd / 252
        out(f"\n{'=' * 76}\n  {key}: {len(df)} candles (~{anos:.1f}a)  "
            f"[{df.index[0].date()} → {df.index[-1].date()}] "
            f"({time.time() - t0:.0f}s)\n{'=' * 76}")

    if not dfs:
        out("SEM DADOS — abort")
        logf.close()
        return

    ranking = []
    goes = []
    t_all = time.time()
    workers = max(1, int(args.workers))

    if workers <= 1:
        _worker_init(dfs, want, tfs)
        for i, name in enumerate(names, 1):
            row = _run_one(name)
            tr = row.pop("_trades", None)
            ranking.append(row)
            mark = "★" if "🟢" in str(row.get("veredito", "")) else " "
            out(f"  {mark}[{i}/{len(names)}] {row['setup']:<36} "
                f"n={row.get('n', 0):<5} net={row.get('net', '—')} "
                f"PF={row.get('PF', '—')} cons={row.get('cons%', '—')} "
                f"OOS={row.get('oos_net', '—')}  {row.get('veredito', '')}")
            if tr is not None:
                goes.append(row["setup"])
                tr.to_csv(
                    f"logs/v19_{row['sym']}_M{row['tf']}_{row['setup']}.csv",
                    index=False)
    else:
        # ProcessPool: 1 cópia dos dfs por worker (sem GIL)
        with ProcessPoolExecutor(
            max_workers=workers,
            initializer=_worker_init,
            initargs=(dfs, want, tfs),
        ) as ex:
            futs = {ex.submit(_run_one, n): n for n in names}
            done = 0
            for fut in as_completed(futs):
                done += 1
                row = fut.result()
                tr = row.pop("_trades", None)
                ranking.append(row)
                mark = "★" if "🟢" in str(row.get("veredito", "")) else " "
                out(f"  {mark}[{done}/{len(names)}] {row['setup']:<36} "
                    f"n={row.get('n', 0):<5} net={row.get('net', '—')} "
                    f"PF={row.get('PF', '—')} cons={row.get('cons%', '—')} "
                    f"OOS={row.get('oos_net', '—')}  {row.get('veredito', '')}")
                if tr is not None:
                    goes.append(row["setup"])
                    tr.to_csv(
                        f"logs/v19_{row['sym']}_M{row['tf']}_{row['setup']}.csv",
                        index=False)

    rk = pd.DataFrame(ranking)
    rk_path = f"logs/backtest_setups_novos_v19_win_wdo_mega_ranking_{stamp}.csv"
    if not rk.empty:
        rk2 = rk.copy()
        if "err" in rk2.columns:
            rk2 = rk2.drop(columns=["err"])
        rk2.sort_values(
            ["veredito", "net"], ascending=[True, False], kind="mergesort"
        ).to_csv(rk_path, index=False)

    out(f"\n\n{'#' * 76}\n  RANKING 🟢 GO\n{'#' * 76}")
    go_rows = [r for r in ranking if "🟢" in str(r.get("veredito", ""))]
    yel_rows = [r for r in ranking
                if "🟡" in str(r.get("veredito", "")) or "PROMISSOR" in str(r.get("veredito", ""))]
    if not go_rows:
        out("  (nenhum GO)")
    for r in sorted(go_rows, key=lambda x: -(x.get("net") or -9)):
        out(f"  🟢 {r['sym']:<5} M{r['tf']:<3} {r['setup']:<36} "
            f"n={r.get('n')} net {r.get('net'):+.3f} PF {r.get('PF')} "
            f"cons {r.get('cons%')}% OOS {r.get('oos_net')}")

    out(f"\n{'#' * 76}\n  TOP 🟡 / melhores net (n≥40)\n{'#' * 76}")
    top = [r for r in ranking if (r.get("n") or 0) >= 40]
    top = sorted(top, key=lambda x: -(x.get("net") or -9))[:50]
    for r in top:
        out(f"  {str(r.get('veredito', '?'))[:14]:<14} {r['sym']:<5} M{r['tf']:<3} "
            f"{r['setup']:<36} n={r.get('n')} net {r.get('net')} "
            f"PF {r.get('PF')} cons {r.get('cons%')}% OOS {r.get('oos_net')}")

    out(f"\n{'#' * 76}\n  RESUMO POR ATIVO\n{'#' * 76}")
    for sx in want:
        tag = _tag(sx)
        sub = [r for r in ranking if r.get("sym") == tag]
        ng = sum(1 for r in sub if "🟢" in str(r.get("veredito", "")))
        ny = sum(1 for r in sub if "🟡" in str(r.get("veredito", ""))
                 or "PROMISSOR" in str(r.get("veredito", "")))
        out(f"  {tag}: testados={len(sub)}  🟢={ng}  🟡={ny}")

    out(f"\n# Veredito: {'🟢 HÁ GO → wire WinGo' if go_rows else '🔴 ZERO GO'}")
    out(f"# GOs: {goes}")
    out(f"# 🟡 count: {len(yel_rows)}")
    out(f"# Tempo total: {(time.time() - t_all) / 60:.1f} min")
    out(f"# Log: {log_path}")
    out(f"# Ranking: {rk_path}")
    out("# FIM")
    logf.close()
    print(f"\n-> {log_path}")
    print(f"-> {rk_path}")
    print(f"GOs: {len(go_rows)} | yellow: {len(yel_rows)} | tested: {len(ranking)}")


if __name__ == "__main__":
    main()
