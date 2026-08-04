"""
backtest_setups_novos_v17_eth_fx.py — mega bateria ETH / EUR / GBP (pós-v15/v16).

Foco (sem retestar 🟢 já wired):
  · ETH: NR/INS/IMP/engolfo/VWAP/EMA/H4-PB · Asia/Lon/NY/OVN/24h · refine 🟡 v15
  · EUR: HL/INS/IMP thin · refine 🟡 v16 (NR5/HL Lon/VWAP fade LN) · M30
  · GBP: NR/HL/IMP/engolfo/H4-PB (quase vazio) · refine insides Lon
  · FX: pregão weekday (sem FDS/WE); Asia/OVN só refine seletivo

Régua GO: net ≥ +0,10 R · PF ≥ 1,25 · cons ≥ 55% · OOS segura · n ≥ 40.
NÃO altera live até 🟢.

Uso:
  python backtest_setups_novos_v17_eth_fx.py
  python backtest_setups_novos_v17_eth_fx.py --workers 6 --bars 50000
  python backtest_setups_novos_v17_eth_fx.py --syms ETHUSD --workers 8
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
from backtest_setups_novos import stats, consistency, verdict, OOS_FRAC, _sig
from backtest_setups_novos_v2 import add_extra_v2, _simulate
from backtest_setups_novos_v5 import _h4_ok
from backtest_setups_novos_v9_refine import _mt
from backtest_setups_novos_v13_btc_24h import (
    _hh_ok, _inside_h, _nr_h, _impulse_h, _h4_pb_ema_h, _volspike_h,
    _outside_h, _hl_h, _dbl_inside_h, _load_ohlc,
)
from backtest_setups_novos_v14_btc_eth import (
    _pdh_h, _engulf_h, _sqz_break_h, _ema_reclaim_h,
)
from backtest_setups_novos_v15_btc_eth import (
    _rr, _cap_tp, _atr_regime, _vwap_fade_h, _vwap_cont_h,
)

STRATS = {}
STRAT_SYM = {}
ONE_PER_DAY = set()
STRAT_TF = {}
STRAT_META = {}

_WORKER_DFS = {}

FX_TP_CAP = 0.0030  # ~30 pips


def _reg(name, fn, sym="ETHUSD", one_per_day=False, tf=15, meta=None):
    STRATS[name] = fn
    STRAT_SYM[name] = sym
    STRAT_TF[name] = tf
    if one_per_day:
        ONE_PER_DAY.add(name)
    if meta:
        STRAT_META[name] = meta


def _pts_cap(fn, cap: float, rr1=1.5):
    def _f(df, i, sym):
        base = fn(df, i, sym)
        if not base:
            return None
        entry = float(df.iloc[i].Close)
        sl = float(base["sl"])
        risk = abs(entry - sl)
        if risk <= 0:
            return None
        tp_dist = min(rr1 * risk, float(cap))
        return _sig(base["dir"], entry, sl, tp_dist / risk, tp_dist / risk, False)
    return _f


def _weekday(fn):
    def _f(df, i, sym):
        if int(df.iloc[i].dow) not in (0, 1, 2, 3, 4):
            return None
        return fn(df, i, sym)
    return _f


# Tags já wired — NÃO registrar idênticos (sym, kind, n, vol, hh0, hh1, session, tp_r, tf)
SKIP = {
    # ETH v14/v15 GOs
    ("ETHUSD", "inside", None, 1.3, 0.0, 24.0, "mt", 1.5, 15),   # INS_24H_MT
    ("ETHUSD", "hl", None, 1.3, 0.0, 24.0, "mt", 1.5, 15),        # HL_24H_MT vol1.3
    ("ETHUSD", "hl", None, 1.4, 0.0, 24.0, "mt", 1.5, 15),        # HL_24H_MT_V14
    ("ETHUSD", "hl", None, 1.5, 0.0, 24.0, "mt", 1.5, 15),        # HL_24H_MT_V15
    ("ETHUSD", "hl", None, 1.4, 0.0, 24.0, "mt", 2.0, 15),        # HL_24H_MT_TP20
    ("ETHUSD", "hl", None, 1.4, 0.0, 8.0, "mt", 1.5, 15),         # HL_ASIA_MT
    ("ETHUSD", "hl", None, 1.4, 20.0, 10.0, "mt", 1.5, 15),       # HL_NIGHT_MT
    ("ETHUSD", "hl", None, 1.4, 13.0, 17.0, None, 1.5, 15),       # HL_NY_1317
    ("ETHUSD", "pdh", None, 1.3, 0.0, 8.0, None, 1.5, 15),        # PDH_ASIA_V13
    ("ETHUSD", "pdh", None, 1.3, 0.0, 8.0, None, 2.0, 15),        # PDH_ASIA_TP20
    ("ETHUSD", "pdh", None, 1.3, 20.0, 10.0, "mt", 1.5, 15),      # PDH_NIGHT_MT
    ("ETHUSD", "imp", None, 1.5, 0.0, 24.0, None, 2.0, 15),       # IMP_CONT_24H
    ("ETHUSD", "imp", None, 1.6, 0.0, 24.0, None, 2.0, 15),       # IMP_CONT_24H_V18
    ("ETHUSD", "imp", None, 1.5, 0.0, 24.0, None, 2.5, 15),       # IMP_TP25_24H
    ("ETHUSD", "inside", None, 1.3, 9.0, 18.0, None, 1.5, 15),    # INS_0918
    ("ETHUSD", "inside", None, 1.2, 10.0, 15.0, None, 1.5, 15),   # INSIDE_1015
    # EUR v16 + clássicos
    ("EURUSD", "pdh", None, 1.2, 10.0, 16.0, None, 1.5, 15),      # EUR_PDH_H4
    ("EURUSD", "pdh", None, 1.2, 10.0, 16.0, "mt", 1.5, 15),      # EUR_PDH_DAY_MT
    ("EURUSD", "pdh", None, 1.3, 10.0, 16.0, None, 1.5, 15),      # EUR_PDH_DAY_V13
    ("EURUSD", "pdh", None, 1.3, 8.0, 17.0, "mt", 1.5, 15),       # EUR_PDH_LN_MT
    ("EURUSD", "imp", None, 1.5, 0.0, 24.0, "mt", 2.0, 15),       # EUR_IMP_CONT_24H_MT
    ("EURUSD", "vwap_fade", None, 1.2, 13.0, 17.0, None, 1.5, 15),
    ("EURUSD", "nr", 4, 1.4, 7.0, 13.0, None, 1.5, 15),
    ("EURUSD", "nr", 4, 1.4, 8.0, 17.0, "mt", 1.5, 15),
    ("EURUSD", "h4_pb", None, 1.3, 13.0, 17.0, None, 2.0, 15),
    ("EURUSD", "h4_pb", None, 1.3, 13.0, 17.0, "mt", 2.0, 15),
    ("EURUSD", "engulf", None, 1.4, 8.0, 12.0, "mt", 1.5, 15),
    ("EURUSD", "engulf", None, 1.4, 8.0, 17.0, "mt", 1.5, 15),
    ("EURUSD", "engulf", None, 1.4, 8.0, 17.0, None, 1.5, 30),
    # GBP
    ("GBPUSD", "inside", None, 1.3, 9.0, 12.0, None, 1.5, 15),
    ("GBPUSD", "inside", None, 1.3, 10.0, 17.0, None, 1.5, 15),
    ("GBPUSD", "inside", None, 1.3, 10.0, 16.0, None, 1.5, 15),
    ("GBPUSD", "inside", None, 1.3, 8.0, 17.0, None, 1.5, 15),
    ("GBPUSD", "inside", None, 1.3, 8.0, 17.0, "mt", 1.5, 15),
}


def _skip_key(sym, kind, n, vol, hh0, hh1, session, tp_r, tf):
    return (sym, kind, n, round(vol, 2), round(hh0, 1), round(hh1, 1),
            session, round(tp_r, 2), tf)


def _add(name, fn, sym, kind, *, n=None, vol=1.3, hh0=0.0, hh1=24.0,
         session=None, tp_r=1.5, tf=15, dist=None, title="",
         pts_cap=None, atr_mult=None):
    key = _skip_key(sym, kind, n, vol, hh0, hh1, session, tp_r, tf)
    if key in SKIP:
        return
    if name in STRATS:
        return
    wrapped = fn
    if session == "mt":
        wrapped = _mt(wrapped)
    elif session == "wd":
        wrapped = _weekday(wrapped)
    if pts_cap is not None:
        wrapped = _pts_cap(wrapped, pts_cap, rr1=float(tp_r))
    elif tp_r != 1.5 and kind not in ("imp", "h4_pb"):
        if kind in ("hl", "pdh", "nr", "inside", "engulf", "ema_recl",
                    "vwap_cont", "vwap_fade", "out"):
            wrapped = _cap_tp(wrapped, float(tp_r))
    meta = {
        "name": name, "sym": sym, "tf": tf, "kind": kind,
        "n": n, "vol": vol, "hh0": hh0, "hh1": hh1,
        "session": session if session in ("mt",) else None,
        "tp_r": tp_r, "dist": dist, "title": title or name,
        "tp_cap": pts_cap, "atr_mult": atr_mult,
    }
    _reg(name, wrapped, sym=sym, tf=tf, meta=meta)


def _build_battery():
    """~280–380 variantes focadas em buracos ETH/EUR/GBP."""
    SESS_ETH = [
        ("LON", 8.0, 12.0),
        ("LON0713", 7.0, 13.0),
        ("LN", 8.0, 17.0),
        ("NY", 13.0, 17.0),
        ("NY1418", 14.0, 18.0),
        ("DAY", 10.0, 16.0),
        ("AM", 9.0, 12.0),
        ("1622", 16.0, 22.0),
        ("ASIA", 0.0, 8.0),
        ("NIGHT", 20.0, 10.0),
        ("OVN", 18.0, 8.0),
        ("24H", 0.0, 24.0),
    ]
    # FX: Lon/NY/Day prioritários; Asia/OVN só refine seletivo (histórico ruim)
    SESS_FX = [
        ("LON", 8.0, 12.0),
        ("LON0713", 7.0, 13.0),
        ("LN", 8.0, 17.0),
        ("NY", 13.0, 17.0),
        ("NY1418", 14.0, 18.0),
        ("DAY", 10.0, 16.0),
        ("AM", 9.0, 12.0),
        ("PM", 12.0, 16.0),
        ("24H", 0.0, 24.0),
    ]
    SESS_FX_CORE = [
        ("LON", 8.0, 12.0), ("LN", 8.0, 17.0), ("NY", 13.0, 17.0),
        ("DAY", 10.0, 16.0), ("LON0713", 7.0, 13.0),
    ]

    # ══════════════════════════════════════════════════════
    # ETH — buracos NR / INS / engolfo / VWAP / H4-PB / refine 🟡
    # ══════════════════════════════════════════════════════
    sx, pfx = "ETHUSD", "ETH"

    # NR4/NR5/NR7 por sessão (24h NR era buraco)
    for n in (4, 5, 7):
        for tag, h0, h1 in SESS_ETH:
            if n == 7 and tag not in ("24H", "LN", "ASIA", "NIGHT", "OVN"):
                continue
            _add(f"{pfx}_NR{n}_{tag}", _nr_h(n, 1.4, h0, h1), sx, "nr",
                 n=n, vol=1.4, hh0=h0, hh1=h1, title=f"NR{n} · {tag}")
            if tag in ("24H", "ASIA", "LN", "NY", "NIGHT", "OVN", "LON"):
                _add(f"{pfx}_NR{n}_{tag}_MT", _nr_h(n, 1.4, h0, h1), sx, "nr",
                     n=n, vol=1.4, hh0=h0, hh1=h1, session="mt",
                     title=f"NR{n} · {tag} · seg–qui")
        # vol refine 🟡 NR4 Asia
        if n == 4:
            _add(f"{pfx}_NR4_ASIA_V15", _nr_h(4, 1.5, 0, 8), sx, "nr",
                 n=4, vol=1.5, hh0=0, hh1=8, title="NR4 vol≥1.5× · Asia")
            _add(f"{pfx}_NR4_ASIA_MT_V15", _nr_h(4, 1.5, 0, 8), sx, "nr",
                 n=4, vol=1.5, hh0=0, hh1=8, session="mt",
                 title="NR4 vol≥1.5× · Asia · seg–qui")
            _add(f"{pfx}_NR4_24H_V16", _nr_h(4, 1.6, 0, 24), sx, "nr",
                 n=4, vol=1.6, hh0=0, hh1=24, title="NR4 vol≥1.6× · 24h")
            _add(f"{pfx}_NR4_24H_MT_TP20", _nr_h(4, 1.4, 0, 24), sx, "nr",
                 n=4, vol=1.4, hh0=0, hh1=24, session="mt", tp_r=2.0,
                 title="NR4 · 24h · TP 2R · seg–qui")

    # Inside — Lon/NY/Asia/Night (🟡 INS Asia/NY/Lon)
    for tag, h0, h1 in SESS_ETH:
        if tag in ("AM",):  # overlap INS_0918/1015
            continue
        _add(f"{pfx}_INS_{tag}", _inside_h(1.3, h0, h1), sx, "inside",
             vol=1.3, hh0=h0, hh1=h1, title=f"inside · {tag}")
        if tag in ("LON", "LN", "NY", "ASIA", "NIGHT", "OVN", "1622", "24H"):
            _add(f"{pfx}_INS_{tag}_MT", _inside_h(1.3, h0, h1), sx, "inside",
                 vol=1.3, hh0=h0, hh1=h1, session="mt",
                 title=f"inside · {tag} · seg–qui")
    _add(f"{pfx}_INS_ASIA_V14", _inside_h(1.4, 0, 8), sx, "inside",
         vol=1.4, hh0=0, hh1=8, title="inside vol≥1.4× · Asia")
    _add(f"{pfx}_INS_ASIA_MT_V14", _inside_h(1.4, 0, 8), sx, "inside",
         vol=1.4, hh0=0, hh1=8, session="mt", title="inside vol≥1.4× · Asia · MT")
    _add(f"{pfx}_INS_NY_TP20", _inside_h(1.3, 13, 17), sx, "inside",
         vol=1.3, hh0=13, hh1=17, tp_r=2.0, title="inside · NY · TP 2R")
    _add(f"{pfx}_INS_LON_TP20", _inside_h(1.3, 8, 12), sx, "inside",
         vol=1.3, hh0=8, hh1=12, tp_r=2.0, title="inside · Lon · TP 2R")
    _add(f"{pfx}_DBL_INS_ASIA", _dbl_inside_h(1.3, 0, 8), sx, "inside",
         vol=1.3, hh0=0, hh1=8, title="dbl-inside · Asia")
    _add(f"{pfx}_DBL_INS_LN", _dbl_inside_h(1.3, 8, 17), sx, "inside",
         vol=1.3, hh0=8, hh1=17, title="dbl-inside · Lon+NY")

    # HL — gaps Lon/OVN/1622 + refine NY MT
    for tag, h0, h1 in [("LON", 8, 12), ("LON0713", 7, 13), ("LN", 8, 17),
                        ("OVN", 18, 8), ("1622", 16, 22), ("DAY", 10, 16)]:
        _add(f"{pfx}_HL_{tag}", _hl_h(1.4, h0, h1), sx, "hl",
             vol=1.4, hh0=h0, hh1=h1, title=f"HL · {tag}")
        _add(f"{pfx}_HL_{tag}_MT", _hl_h(1.4, h0, h1), sx, "hl",
             vol=1.4, hh0=h0, hh1=h1, session="mt", title=f"HL · {tag} · seg–qui")
    _add(f"{pfx}_HL_NY_MT", _hl_h(1.4, 13, 17), sx, "hl",
         vol=1.4, hh0=13, hh1=17, session="mt", title="HL · NY · seg–qui")
    _add(f"{pfx}_HL_NY_V15", _hl_h(1.5, 13, 17), sx, "hl",
         vol=1.5, hh0=13, hh1=17, title="HL vol≥1.5× · NY")
    _add(f"{pfx}_HL_NY_TP20", _hl_h(1.4, 13, 17), sx, "hl",
         vol=1.4, hh0=13, hh1=17, tp_r=2.0, title="HL · NY · TP 2R")
    _add(f"{pfx}_HL_ASIA_V15", _hl_h(1.5, 0, 8), sx, "hl",
         vol=1.5, hh0=0, hh1=8, title="HL vol≥1.5× · Asia")
    _add(f"{pfx}_HL_24H_MT_V16", _hl_h(1.6, 0, 24), sx, "hl",
         vol=1.6, hh0=0, hh1=24, session="mt", title="HL vol≥1.6× · 24h · MT")

    # PDH — Lon/NY/OVN/24h (Asia+Night já GO)
    for tag, h0, h1 in [("LON", 8, 12), ("LON0713", 7, 13), ("LN", 8, 17),
                        ("NY", 13, 17), ("OVN", 18, 8), ("24H", 0, 24),
                        ("DAY", 10, 16), ("1622", 16, 22)]:
        _add(f"{pfx}_PDH_{tag}", _pdh_h(1.2, h0, h1), sx, "pdh",
             vol=1.2, hh0=h0, hh1=h1, title=f"PDH · {tag}")
        _add(f"{pfx}_PDH_{tag}_MT", _pdh_h(1.3, h0, h1), sx, "pdh",
             vol=1.3, hh0=h0, hh1=h1, session="mt", title=f"PDH · {tag} · MT")
    _add(f"{pfx}_PDH_ASIA_MT", _pdh_h(1.2, 0, 8), sx, "pdh",
         vol=1.2, hh0=0, hh1=8, session="mt", title="PDH Asia · MT")
    _add(f"{pfx}_PDH_ASIA_V14", _pdh_h(1.4, 0, 8), sx, "pdh",
         vol=1.4, hh0=0, hh1=8, title="PDH vol≥1.4× · Asia")
    _add(f"{pfx}_PDH_24H_MT_V15", _pdh_h(1.5, 0, 24), sx, "pdh",
         vol=1.5, hh0=0, hh1=24, session="mt", title="PDH vol≥1.5× · 24h · MT")
    _add(f"{pfx}_PDH_24H_TP20", _pdh_h(1.3, 0, 24), sx, "pdh",
         vol=1.3, hh0=0, hh1=24, tp_r=2.0, title="PDH · 24h · TP 2R")

    # Impulse — sessões (🟡 Asia/OVN/Night amostra) + refine V18/TP
    for tag, h0, h1 in [("LON", 8, 12), ("NY", 13, 17), ("LN", 8, 17),
                        ("ASIA", 0, 8), ("OVN", 18, 8), ("NIGHT", 20, 10),
                        ("1622", 16, 22), ("DAY", 10, 16)]:
        _add(f"{pfx}_IMP_{tag}", _impulse_h(2.0, 1.5, h0, h1, 2.0), sx, "imp",
             vol=1.5, hh0=h0, hh1=h1, tp_r=2.0, atr_mult=2.0,
             title=f"impulso · {tag}")
        _add(f"{pfx}_IMP_{tag}_MT", _impulse_h(2.0, 1.5, h0, h1, 2.0), sx, "imp",
             vol=1.5, hh0=h0, hh1=h1, session="mt", tp_r=2.0, atr_mult=2.0,
             title=f"impulso · {tag} · MT")
        _add(f"{pfx}_IMP_{tag}_V18", _impulse_h(1.8, 1.6, h0, h1, 2.0), sx, "imp",
             vol=1.6, hh0=h0, hh1=h1, tp_r=2.0, atr_mult=1.8,
             title=f"impulso V18 · {tag}")
    _add(f"{pfx}_IMP_CONT_24H_MT_V18", _impulse_h(1.8, 1.6, 0, 24, 2.0), sx, "imp",
         vol=1.6, hh0=0, hh1=24, session="mt", tp_r=2.0, atr_mult=1.8,
         title="impulso V18 · 24h · MT")
    _add(f"{pfx}_IMP_CONT_24H_V16", _impulse_h(2.0, 1.6, 0, 24, 2.0), sx, "imp",
         vol=1.6, hh0=0, hh1=24, tp_r=2.0, atr_mult=2.0,
         title="impulso vol≥1.6× · 24h")
    _add(f"{pfx}_IMP_CONT_24H_V20", _impulse_h(2.0, 2.0, 0, 24, 2.0), sx, "imp",
         vol=2.0, hh0=0, hh1=24, tp_r=2.0, atr_mult=2.0,
         title="impulso vol≥2.0× · 24h")
    _add(f"{pfx}_IMP_ASIA_TP25", _impulse_h(2.0, 1.5, 0, 8, 2.5), sx, "imp",
         vol=1.5, hh0=0, hh1=8, tp_r=2.5, atr_mult=2.0,
         title="impulso Asia · TP 2.5R")
    _add(f"{pfx}_IMP_TP30_24H", _impulse_h(2.0, 1.5, 0, 24, 3.0), sx, "imp",
         vol=1.5, hh0=0, hh1=24, tp_r=3.0, atr_mult=2.0,
         title="impulso · 24h · TP 3R")

    # Engolfo / Outside / EMA / VWAP / H4-PB (buracos)
    for tag, h0, h1 in [("LON", 8, 12), ("NY", 13, 17), ("LN", 8, 17),
                        ("ASIA", 0, 8), ("NIGHT", 20, 10), ("24H", 0, 24),
                        ("OVN", 18, 8)]:
        _add(f"{pfx}_ENGULF_{tag}", _engulf_h(1.4, h0, h1), sx, "engulf",
             vol=1.4, hh0=h0, hh1=h1, title=f"engulf · {tag}")
        _add(f"{pfx}_ENGULF_{tag}_MT", _engulf_h(1.4, h0, h1), sx, "engulf",
             vol=1.4, hh0=h0, hh1=h1, session="mt", title=f"engulf · {tag} · MT")
        _add(f"{pfx}_OUT_{tag}", _outside_h(1.4, h0, h1), sx, "out",
             vol=1.4, hh0=h0, hh1=h1, title=f"outside · {tag}")
        _add(f"{pfx}_EMA_RECL_{tag}", _ema_reclaim_h(h0, h1, 1.3), sx, "ema_recl",
             vol=1.3, hh0=h0, hh1=h1, title=f"EMA reclaim · {tag}")
        _add(f"{pfx}_VWAP_CONT_{tag}", _vwap_cont_h(1.3, 0.3, h0, h1), sx, "vwap_cont",
             vol=1.3, hh0=h0, hh1=h1, dist=0.3, title=f"VWAP cont · {tag}")
        _add(f"{pfx}_VWAP_FADE_{tag}", _vwap_fade_h(1.2, 1.2, h0, h1), sx, "vwap_fade",
             vol=1.2, hh0=h0, hh1=h1, dist=1.2, title=f"VWAP fade · {tag}")
        _add(f"{pfx}_H4_PB_{tag}", _h4_pb_ema_h(h0, h1), sx, "h4_pb",
             hh0=h0, hh1=h1, tp_r=2.0, title=f"H4 PB · {tag}")
        _add(f"{pfx}_H4_PB_{tag}_MT", _h4_pb_ema_h(h0, h1), sx, "h4_pb",
             hh0=h0, hh1=h1, session="mt", tp_r=2.0, title=f"H4 PB · {tag} · MT")

    _add(f"{pfx}_SQZ_24H_MT", _sqz_break_h(1.5, 0, 24), sx, "sqz",
         vol=1.5, hh0=0, hh1=24, session="mt", title="squeeze 24h · MT")
    _add(f"{pfx}_SQZ_ASIA_MT", _sqz_break_h(1.5, 0, 8), sx, "sqz",
         vol=1.5, hh0=0, hh1=8, session="mt", title="squeeze Asia · MT")
    _add(f"{pfx}_VOLSPIKE_ASIA", _volspike_h(2.0, 1.5, 0, 8), sx, "volspike",
         vol=2.0, hh0=0, hh1=8, title="volspike · Asia")
    _add(f"{pfx}_VOLSPIKE_NIGHT", _volspike_h(2.0, 1.5, 20, 10), sx, "volspike",
         vol=2.0, hh0=20, hh1=10, title="volspike · Night")

    # ETH M30
    for name_s, fn, kind, kw in [
        (f"{pfx}_INS_LON_M30", _inside_h(1.3, 8, 12), "inside",
         {"vol": 1.3, "hh0": 8, "hh1": 12}),
        (f"{pfx}_INS_LN_M30", _inside_h(1.3, 8, 17), "inside",
         {"vol": 1.3, "hh0": 8, "hh1": 17}),
        (f"{pfx}_INS_ASIA_M30", _inside_h(1.3, 0, 8), "inside",
         {"vol": 1.3, "hh0": 0, "hh1": 8}),
        (f"{pfx}_HL_LN_M30", _hl_h(1.4, 8, 17), "hl",
         {"vol": 1.4, "hh0": 8, "hh1": 17}),
        (f"{pfx}_HL_24H_MT_M30", None, "hl",
         {"vol": 1.4, "hh0": 0, "hh1": 24, "session": "mt"}),
        (f"{pfx}_NR4_24H_M30", _nr_h(4, 1.4, 0, 24), "nr",
         {"n": 4, "vol": 1.4, "hh0": 0, "hh1": 24}),
        (f"{pfx}_NR5_LN_M30", _nr_h(5, 1.4, 8, 17), "nr",
         {"n": 5, "vol": 1.4, "hh0": 8, "hh1": 17}),
        (f"{pfx}_PDH_ASIA_M30", _pdh_h(1.2, 0, 8), "pdh",
         {"vol": 1.2, "hh0": 0, "hh1": 8}),
        (f"{pfx}_PDH_LN_M30", _pdh_h(1.2, 8, 17), "pdh",
         {"vol": 1.2, "hh0": 8, "hh1": 17}),
        (f"{pfx}_ENGULF_24H_M30", _engulf_h(1.4, 0, 24), "engulf",
         {"vol": 1.4, "hh0": 0, "hh1": 24}),
        (f"{pfx}_ENGULF_ASIA_M30", _engulf_h(1.4, 0, 8), "engulf",
         {"vol": 1.4, "hh0": 0, "hh1": 8}),
        (f"{pfx}_IMP_ASIA_MT_M30", None, "imp",
         {"vol": 1.5, "hh0": 0, "hh1": 8, "session": "mt", "tp_r": 2.0}),
        (f"{pfx}_IMP_LN_M30", _impulse_h(2.0, 1.5, 8, 17, 2.0), "imp",
         {"vol": 1.5, "hh0": 8, "hh1": 17, "tp_r": 2.0}),
        (f"{pfx}_EMA_RECL_24H_M30", _ema_reclaim_h(0, 24, 1.3), "ema_recl",
         {"vol": 1.3, "hh0": 0, "hh1": 24}),
        (f"{pfx}_H4_PB_24H_M30", _h4_pb_ema_h(0, 24), "h4_pb",
         {"hh0": 0, "hh1": 24, "tp_r": 2.0}),
        (f"{pfx}_VWAP_FADE_24H_M30", _vwap_fade_h(1.2, 1.2, 0, 24), "vwap_fade",
         {"vol": 1.2, "hh0": 0, "hh1": 24, "dist": 1.2}),
    ]:
        sess = kw.get("session")
        if name_s.endswith("_MT_M30") and kind == "hl":
            fn2 = _mt(_hl_h(1.4, 0, 24))
            _add(name_s, fn2, sx, kind, tf=30, session=None,
                 title=f"{kind} · M30 · seg–qui",
                 **{k: v for k, v in kw.items() if k in ("n", "vol", "hh0", "hh1", "tp_r")})
            # force meta session=mt
            if name_s in STRAT_META:
                STRAT_META[name_s]["session"] = "mt"
        elif name_s.endswith("_MT_M30") and kind == "imp":
            fn2 = _mt(_impulse_h(2.0, 1.5, 0, 8, 2.0))
            _add(name_s, fn2, sx, kind, tf=30, session=None, atr_mult=2.0,
                 title=f"{kind} · Asia M30 · MT",
                 **{k: v for k, v in kw.items() if k in ("n", "vol", "hh0", "hh1", "tp_r")})
            if name_s in STRAT_META:
                STRAT_META[name_s]["session"] = "mt"
        else:
            _add(name_s, fn, sx, kind, tf=30, session=sess,
                 title=f"{kind} · M30",
                 **{k: v for k, v in kw.items()
                    if k in ("n", "vol", "hh0", "hh1", "tp_r", "dist")})

    # ══════════════════════════════════════════════════════
    # EUR / GBP
    # ══════════════════════════════════════════════════════
    for sx in ("EURUSD", "GBPUSD"):
        pfx = "EUR" if sx == "EURUSD" else "GBP"

        # Inside
        for tag, h0, h1 in SESS_FX:
            if sx == "GBPUSD" and tag in ("DAY", "AM"):
                continue  # wired DAY/AM
            if sx == "GBPUSD" and abs(h0 - 10) < 0.01 and abs(h1 - 17) < 0.01:
                continue
            _add(f"{pfx}_INS_{tag}", _inside_h(1.3, h0, h1), sx, "inside",
                 vol=1.3, hh0=h0, hh1=h1, title=f"inside · {tag}")
            if tag in ("LON", "NY", "LN", "24H", "LON0713", "PM"):
                _add(f"{pfx}_INS_{tag}_MT", _inside_h(1.3, h0, h1), sx, "inside",
                     vol=1.3, hh0=h0, hh1=h1, session="mt",
                     title=f"inside · {tag} · MT")
        # refine 🟡 GBP Lon insides
        if sx == "GBPUSD":
            _add("GBP_INS_LON_V14", _inside_h(1.4, 8, 12), sx, "inside",
                 vol=1.4, hh0=8, hh1=12, title="inside vol≥1.4× · Lon")
            _add("GBP_INS_LON_MT_V14", _inside_h(1.4, 8, 12), sx, "inside",
                 vol=1.4, hh0=8, hh1=12, session="mt",
                 title="inside vol≥1.4× · Lon · MT")
            _add("GBP_INS_LON_TP20", _inside_h(1.3, 8, 12), sx, "inside",
                 vol=1.3, hh0=8, hh1=12, tp_r=2.0, title="inside Lon · TP 2R")
            _add("GBP_INS_LON0713_MT", _inside_h(1.3, 7, 13), sx, "inside",
                 vol=1.3, hh0=7, hh1=13, session="mt",
                 title="inside · 07–13 · MT")
            _add("GBP_INS_LON0713_V14", _inside_h(1.4, 7, 13), sx, "inside",
                 vol=1.4, hh0=7, hh1=13, title="inside vol≥1.4× · 07–13")
            _add("GBP_DBL_INS_LN", _dbl_inside_h(1.3, 8, 17), sx, "inside",
                 vol=1.3, hh0=8, hh1=17, title="dbl-inside · Lon+NY")
        if sx == "EURUSD":
            _add("EUR_INS_AM", _inside_h(1.3, 9, 12), sx, "inside",
                 vol=1.3, hh0=9, hh1=12, title="inside · 09–12")
            _add("EUR_INS_1017", _inside_h(1.3, 10, 17), sx, "inside",
                 vol=1.3, hh0=10, hh1=17, title="inside · 10–17")
            _add("EUR_INS_NY_MT", _inside_h(1.3, 13, 17), sx, "inside",
                 vol=1.3, hh0=13, hh1=17, session="mt", title="inside NY · MT")
            _add("EUR_INS_NY_TP20", _inside_h(1.3, 13, 17), sx, "inside",
                 vol=1.3, hh0=13, hh1=17, tp_r=2.0, title="inside NY · TP 2R")
            _add("EUR_INS_NY_V14", _inside_h(1.4, 13, 17), sx, "inside",
                 vol=1.4, hh0=13, hh1=17, title="inside vol≥1.4× · NY")

        # NR4/NR5 — GBP buraco principal; EUR refine 🟡 NR5
        for n in (4, 5, 7):
            for tag, h0, h1 in SESS_FX:
                if n == 7 and tag not in ("LN", "DAY", "24H", "LON0713"):
                    continue
                _add(f"{pfx}_NR{n}_{tag}", _nr_h(n, 1.4, h0, h1), sx, "nr",
                     n=n, vol=1.4, hh0=h0, hh1=h1, title=f"NR{n} · {tag}")
                if tag in ("LON", "NY", "LN", "DAY", "24H", "LON0713"):
                    _add(f"{pfx}_NR{n}_{tag}_MT", _nr_h(n, 1.4, h0, h1), sx, "nr",
                         n=n, vol=1.4, hh0=h0, hh1=h1, session="mt",
                         title=f"NR{n} · {tag} · MT")
        # refine 🟡 EUR NR5 Lon0713 / LN
        if sx == "EURUSD":
            _add("EUR_NR5_LON0713_V15", _nr_h(5, 1.5, 7, 13), sx, "nr",
                 n=5, vol=1.5, hh0=7, hh1=13, title="NR5 vol≥1.5× · 07–13")
            _add("EUR_NR5_LON0713_MT", _nr_h(5, 1.4, 7, 13), sx, "nr",
                 n=5, vol=1.4, hh0=7, hh1=13, session="mt",
                 title="NR5 · 07–13 · MT")
            _add("EUR_NR5_LON0713_TP20", _nr_h(5, 1.4, 7, 13), sx, "nr",
                 n=5, vol=1.4, hh0=7, hh1=13, tp_r=2.0,
                 title="NR5 · 07–13 · TP 2R")
            _add("EUR_NR4_LON_V15", _nr_h(4, 1.5, 8, 12), sx, "nr",
                 n=4, vol=1.5, hh0=8, hh1=12, title="NR4 vol≥1.5× · Lon")
            _add("EUR_NR5_LN_V15", _nr_h(5, 1.5, 8, 17), sx, "nr",
                 n=5, vol=1.5, hh0=8, hh1=17, title="NR5 vol≥1.5× · Lon+NY")
        if sx == "GBPUSD":
            _add("GBP_NR5_DAY_V15", _nr_h(5, 1.5, 10, 16), sx, "nr",
                 n=5, vol=1.5, hh0=10, hh1=16, title="NR5 vol≥1.5× · Day")
            _add("GBP_NR5_DAY_TP20", _nr_h(5, 1.4, 10, 16), sx, "nr",
                 n=5, vol=1.4, hh0=10, hh1=16, tp_r=2.0,
                 title="NR5 Day · TP 2R")
            _add("GBP_NR4_DAY_V15", _nr_h(4, 1.5, 10, 16), sx, "nr",
                 n=4, vol=1.5, hh0=10, hh1=16, title="NR4 vol≥1.5× · Day")
            _add("GBP_NR5_NY_V15", _nr_h(5, 1.5, 13, 17), sx, "nr",
                 n=5, vol=1.5, hh0=13, hh1=17, title="NR5 vol≥1.5× · NY")
            _add("GBP_NR4_NY_V15", _nr_h(4, 1.5, 13, 17), sx, "nr",
                 n=4, vol=1.5, hh0=13, hh1=17, title="NR4 vol≥1.5× · NY")

        # HL — EUR thin + GBP buraco
        for tag, h0, h1 in SESS_FX:
            _add(f"{pfx}_HL_{tag}", _hl_h(1.4, h0, h1), sx, "hl",
                 vol=1.4, hh0=h0, hh1=h1, title=f"HL · {tag}")
            _add(f"{pfx}_HL_{tag}_MT", _hl_h(1.4, h0, h1), sx, "hl",
                 vol=1.4, hh0=h0, hh1=h1, session="mt", title=f"HL · {tag} · MT")
            if tag in ("LON", "NY", "LN", "DAY", "LON0713"):
                _add(f"{pfx}_HL_{tag}_TP20", _hl_h(1.4, h0, h1), sx, "hl",
                     vol=1.4, hh0=h0, hh1=h1, tp_r=2.0, title=f"HL TP 2R · {tag}")
                _add(f"{pfx}_HL_{tag}_V15", _hl_h(1.5, h0, h1), sx, "hl",
                     vol=1.5, hh0=h0, hh1=h1, title=f"HL vol≥1.5× · {tag}")
        # refine 🟡 EUR HL Lon/LN
        if sx == "EURUSD":
            _add("EUR_HL_LON0713_V15", _hl_h(1.5, 7, 13), sx, "hl",
                 vol=1.5, hh0=7, hh1=13, title="HL vol≥1.5× · 07–13")
            _add("EUR_HL_LN_V15_MT", _hl_h(1.5, 8, 17), sx, "hl",
                 vol=1.5, hh0=8, hh1=17, session="mt",
                 title="HL vol≥1.5× · Lon+NY · MT")
            _add("EUR_HL_NY_V15_MT", _hl_h(1.5, 13, 17), sx, "hl",
                 vol=1.5, hh0=13, hh1=17, session="mt",
                 title="HL vol≥1.5× · NY · MT")

        # PDH
        for tag, h0, h1 in SESS_FX:
            if sx == "EURUSD" and tag == "DAY":
                continue  # wired
            _add(f"{pfx}_PDH_{tag}", _pdh_h(1.2, h0, h1), sx, "pdh",
                 vol=1.2, hh0=h0, hh1=h1, title=f"PDH · {tag}")
            if tag in ("LON", "NY", "LN", "24H", "LON0713"):
                _add(f"{pfx}_PDH_{tag}_MT", _pdh_h(1.3, h0, h1), sx, "pdh",
                     vol=1.3, hh0=h0, hh1=h1, session="mt",
                     title=f"PDH · {tag} · MT")

        # Impulse
        for tag, h0, h1 in [("LON", 8, 12), ("NY", 13, 17), ("LN", 8, 17),
                            ("DAY", 10, 16), ("LON0713", 7, 13), ("24H", 0, 24),
                            ("OVN", 18, 8)]:
            if sx == "EURUSD" and tag == "24H":
                _add("EUR_IMP_24H", _impulse_h(2.0, 1.5, 0, 24, 2.0), sx, "imp",
                     vol=1.5, hh0=0, hh1=24, tp_r=2.0, atr_mult=2.0,
                     title="impulso · 24h")
                _add("EUR_IMP_24H_V18_MT", _impulse_h(1.8, 1.6, 0, 24, 2.0), sx, "imp",
                     vol=1.6, hh0=0, hh1=24, session="mt", tp_r=2.0, atr_mult=1.8,
                     title="impulso V18 · 24h · MT")
                continue
            _add(f"{pfx}_IMP_{tag}", _impulse_h(2.0, 1.5, h0, h1, 2.0), sx, "imp",
                 vol=1.5, hh0=h0, hh1=h1, tp_r=2.0, atr_mult=2.0,
                 title=f"impulso · {tag}")
            _add(f"{pfx}_IMP_{tag}_MT", _impulse_h(2.0, 1.5, h0, h1, 2.0), sx, "imp",
                 vol=1.5, hh0=h0, hh1=h1, session="mt", tp_r=2.0, atr_mult=2.0,
                 title=f"impulso · {tag} · MT")
            _add(f"{pfx}_IMP_{tag}_V18", _impulse_h(1.8, 1.6, h0, h1, 2.0), sx, "imp",
                 vol=1.6, hh0=h0, hh1=h1, tp_r=2.0, atr_mult=1.8,
                 title=f"impulso V18 · {tag}")
        # refine 🟡 EUR IMP Lon
        if sx == "EURUSD":
            _add("EUR_IMP_LON_TP25", _impulse_h(2.0, 1.5, 8, 12, 2.5), sx, "imp",
                 vol=1.5, hh0=8, hh1=12, tp_r=2.5, atr_mult=2.0,
                 title="impulso Lon · TP 2.5R")
            _add("EUR_IMP_LON_V18_MT", _impulse_h(1.8, 1.6, 8, 12, 2.0), sx, "imp",
                 vol=1.6, hh0=8, hh1=12, session="mt", tp_r=2.0, atr_mult=1.8,
                 title="impulso V18 Lon · MT")

        # Engulf / EMA / VWAP / H4-PB / Out
        for tag, h0, h1 in SESS_FX_CORE + [("24H", 0, 24), ("PM", 12, 16)]:
            _add(f"{pfx}_ENGULF_{tag}", _engulf_h(1.4, h0, h1), sx, "engulf",
                 vol=1.4, hh0=h0, hh1=h1, title=f"engulf · {tag}")
            _add(f"{pfx}_ENGULF_{tag}_MT", _engulf_h(1.4, h0, h1), sx, "engulf",
                 vol=1.4, hh0=h0, hh1=h1, session="mt",
                 title=f"engulf · {tag} · MT")
            _add(f"{pfx}_OUT_{tag}", _outside_h(1.4, h0, h1), sx, "out",
                 vol=1.4, hh0=h0, hh1=h1, title=f"outside · {tag}")
            _add(f"{pfx}_EMA_RECL_{tag}", _ema_reclaim_h(h0, h1, 1.3), sx, "ema_recl",
                 vol=1.3, hh0=h0, hh1=h1, title=f"EMA reclaim · {tag}")
            _add(f"{pfx}_VWAP_CONT_{tag}", _vwap_cont_h(1.3, 0.3, h0, h1), sx, "vwap_cont",
                 vol=1.3, hh0=h0, hh1=h1, dist=0.3, title=f"VWAP cont · {tag}")
            _add(f"{pfx}_VWAP_FADE_{tag}", _vwap_fade_h(1.2, 1.2, h0, h1), sx, "vwap_fade",
                 vol=1.2, hh0=h0, hh1=h1, dist=1.2, title=f"VWAP fade · {tag}")
            if not (sx == "EURUSD" and tag == "NY"):  # H4_PB_NY wired
                _add(f"{pfx}_H4_PB_{tag}", _h4_pb_ema_h(h0, h1), sx, "h4_pb",
                     hh0=h0, hh1=h1, tp_r=2.0, title=f"H4 PB · {tag}")
                _add(f"{pfx}_H4_PB_{tag}_MT", _h4_pb_ema_h(h0, h1), sx, "h4_pb",
                     hh0=h0, hh1=h1, session="mt", tp_r=2.0,
                     title=f"H4 PB · {tag} · MT")
        # refine 🟡 EUR VWAP fade LN
        if sx == "EURUSD":
            _add("EUR_VWAP_FADE_LN_V13", _vwap_fade_h(1.3, 1.2, 8, 17), sx, "vwap_fade",
                 vol=1.3, hh0=8, hh1=17, dist=1.2, title="VWAP fade vol≥1.3× · LN")
            _add("EUR_VWAP_FADE_LN_MT", _vwap_fade_h(1.2, 1.2, 8, 17), sx, "vwap_fade",
                 vol=1.2, hh0=8, hh1=17, dist=1.2, session="mt",
                 title="VWAP fade LN · MT")
            _add("EUR_VWAP_FADE_LN_D14", _vwap_fade_h(1.2, 1.4, 8, 17), sx, "vwap_fade",
                 vol=1.2, hh0=8, hh1=17, dist=1.4, title="VWAP fade dist≥1.4 · LN")
            _add("EUR_VWAP_FADE_DAY", _vwap_fade_h(1.2, 1.2, 10, 16), sx, "vwap_fade",
                 vol=1.2, hh0=10, hh1=16, dist=1.2, title="VWAP fade · Day")
        if sx == "GBPUSD":
            _add("GBP_H4_PB_NY_V15", _h4_pb_ema_h(13, 17), sx, "h4_pb",
                 hh0=13, hh1=17, tp_r=2.0, title="H4 PB NY refine")
            # already added via loop; extra TP
            _add("GBP_H4_PB_LN_TP25", _h4_pb_ema_h(8, 17), sx, "h4_pb",
                 hh0=8, hh1=17, tp_r=2.5, title="H4 PB Lon+NY · TP 2.5R")
            _add("GBP_ENGULF_LN_V15", _engulf_h(1.5, 8, 17), sx, "engulf",
                 vol=1.5, hh0=8, hh1=17, title="engulf vol≥1.5× · LN")

        # Cap 30 pips seletivo Lon/NY
        for tag, h0, h1 in [("LON", 8, 12), ("NY", 13, 17), ("LN", 8, 17)]:
            _add(f"{pfx}_HL_{tag}_CAP30", _hl_h(1.4, h0, h1), sx, "hl",
                 vol=1.4, hh0=h0, hh1=h1, pts_cap=FX_TP_CAP,
                 title=f"HL · {tag} · TP cap 30p")
            _add(f"{pfx}_NR4_{tag}_CAP30", _nr_h(4, 1.4, h0, h1), sx, "nr",
                 n=4, vol=1.4, hh0=h0, hh1=h1, pts_cap=FX_TP_CAP,
                 title=f"NR4 · {tag} · TP cap 30p")

        # M30
        for name_s, fn, kind, kw in [
            (f"{pfx}_HL_LN_M30", _hl_h(1.4, 8, 17), "hl",
             {"vol": 1.4, "hh0": 8, "hh1": 17}),
            (f"{pfx}_HL_DAY_M30", _hl_h(1.4, 10, 16), "hl",
             {"vol": 1.4, "hh0": 10, "hh1": 16}),
            (f"{pfx}_NR4_LN_M30", _nr_h(4, 1.4, 8, 17), "nr",
             {"n": 4, "vol": 1.4, "hh0": 8, "hh1": 17}),
            (f"{pfx}_NR5_LN_M30", _nr_h(5, 1.4, 8, 17), "nr",
             {"n": 5, "vol": 1.4, "hh0": 8, "hh1": 17}),
            (f"{pfx}_NR5_DAY_M30", _nr_h(5, 1.4, 10, 16), "nr",
             {"n": 5, "vol": 1.4, "hh0": 10, "hh1": 16}),
            (f"{pfx}_PDH_LN_M30", _pdh_h(1.2, 8, 17), "pdh",
             {"vol": 1.2, "hh0": 8, "hh1": 17}),
            (f"{pfx}_INS_LN_M30", _inside_h(1.3, 8, 17), "inside",
             {"vol": 1.3, "hh0": 8, "hh1": 17}),
            (f"{pfx}_INS_LON_M30", _inside_h(1.3, 8, 12), "inside",
             {"vol": 1.3, "hh0": 8, "hh1": 12}),
            (f"{pfx}_ENGULF_LN_M30", _engulf_h(1.4, 8, 17), "engulf",
             {"vol": 1.4, "hh0": 8, "hh1": 17}),
            (f"{pfx}_ENGULF_LON_M30", _engulf_h(1.4, 8, 12), "engulf",
             {"vol": 1.4, "hh0": 8, "hh1": 12}),
            (f"{pfx}_IMP_LN_M30", _impulse_h(2.0, 1.5, 8, 17, 2.0), "imp",
             {"vol": 1.5, "hh0": 8, "hh1": 17, "tp_r": 2.0}),
            (f"{pfx}_IMP_LON_M30", _impulse_h(2.0, 1.5, 8, 12, 2.0), "imp",
             {"vol": 1.5, "hh0": 8, "hh1": 12, "tp_r": 2.0}),
            (f"{pfx}_H4_PB_NY_M30", _h4_pb_ema_h(13, 17), "h4_pb",
             {"hh0": 13, "hh1": 17, "tp_r": 2.0}),
            (f"{pfx}_H4_PB_LN_M30", _h4_pb_ema_h(8, 17), "h4_pb",
             {"hh0": 8, "hh1": 17, "tp_r": 2.0}),
            (f"{pfx}_VWAP_FADE_NY_M30", _vwap_fade_h(1.2, 1.2, 13, 17), "vwap_fade",
             {"vol": 1.2, "hh0": 13, "hh1": 17, "dist": 1.2}),
            (f"{pfx}_EMA_RECL_LN_M30", _ema_reclaim_h(8, 17, 1.3), "ema_recl",
             {"vol": 1.3, "hh0": 8, "hh1": 17}),
        ]:
            if sx == "EURUSD" and name_s == "EUR_ENGULF_LN_M30":
                continue  # wired GO
            _add(name_s, fn, sx, kind, tf=30, title=f"{kind} · M30",
                 atr_mult=2.0 if kind == "imp" else None,
                 **{k: v for k, v in kw.items()
                    if k in ("n", "vol", "hh0", "hh1", "tp_r", "dist")})

    # Poda → alvo 280–380 (buracos + refine 🟡; sem explosão)
    DROP_TAGS = ("_NY1418", "_PM", "_CAP30", "_OUT_", "_SQZ_", "_VOLSPIKE_",
                 "_DBL_INS_", "_EMA_RECL_", "_VWAP_CONT_")
    KEEP_EMA_VWAP = {  # exceções de buraco
        "ETH_EMA_RECL_ASIA", "ETH_EMA_RECL_NY", "ETH_EMA_RECL_24H",
        "ETH_VWAP_CONT_ASIA", "ETH_VWAP_CONT_NY", "ETH_VWAP_CONT_24H",
        "ETH_VWAP_FADE_ASIA", "ETH_VWAP_FADE_NY", "ETH_VWAP_FADE_24H",
        "EUR_VWAP_FADE_LN", "EUR_VWAP_FADE_LN_V13", "EUR_VWAP_FADE_LN_MT",
        "EUR_VWAP_FADE_LN_D14", "EUR_VWAP_FADE_DAY", "EUR_VWAP_FADE_LON",
        "EUR_VWAP_FADE_NY",  # skip wired via SKIP key, name may still exist
        "GBP_VWAP_FADE_LN", "GBP_VWAP_FADE_NY", "GBP_VWAP_FADE_DAY",
        "ETH_EMA_RECL_24H_M30", "EUR_EMA_RECL_LN_M30", "GBP_EMA_RECL_LN_M30",
        "EUR_VWAP_FADE_NY_M30", "GBP_VWAP_FADE_NY_M30",
    }
    for n in list(STRATS):
        sym = STRAT_SYM[n]
        if sym in ("EURUSD", "GBPUSD") and ("_ASIA" in n or "_NIGHT" in n):
            STRATS.pop(n, None); STRAT_SYM.pop(n, None)
            STRAT_TF.pop(n, None); STRAT_META.pop(n, None)
            continue
        if sym in ("EURUSD", "GBPUSD") and "_OVN" in n and "IMP" not in n:
            STRATS.pop(n, None); STRAT_SYM.pop(n, None)
            STRAT_TF.pop(n, None); STRAT_META.pop(n, None)
            continue
        if any(t in n for t in DROP_TAGS) and n not in KEEP_EMA_VWAP:
            # OUT / SQZ / VOLSPIKE / CAP / NY1418 / PM / DBL / EMA genérico
            if "_EMA_RECL_" in n or "_VWAP_CONT_" in n:
                if n not in KEEP_EMA_VWAP:
                    STRATS.pop(n, None); STRAT_SYM.pop(n, None)
                    STRAT_TF.pop(n, None); STRAT_META.pop(n, None)
                continue
            if "_VWAP_FADE_" in n and n not in KEEP_EMA_VWAP:
                # keep only curated fades
                if not any(n.startswith(p) for p in (
                        "ETH_VWAP_FADE_", "EUR_VWAP_FADE_", "GBP_VWAP_FADE_")):
                    STRATS.pop(n, None); STRAT_SYM.pop(n, None)
                    STRAT_TF.pop(n, None); STRAT_META.pop(n, None)
                elif n not in KEEP_EMA_VWAP and "_MT" not in n and "M30" not in n:
                    # drop generic fades except curated list
                    base_ok = any(k in n for k in (
                        "ASIA", "NY", "24H", "LN", "LON", "DAY"))
                    if not base_ok or n.count("_") > 4:
                        STRATS.pop(n, None); STRAT_SYM.pop(n, None)
                        STRAT_TF.pop(n, None); STRAT_META.pop(n, None)
                continue
            STRATS.pop(n, None); STRAT_SYM.pop(n, None)
            STRAT_TF.pop(n, None); STRAT_META.pop(n, None)
            continue
        # ETH: corta NR7 em sessões finas + H4_PB OVN/1622 genéricos
        if sym == "ETHUSD":
            if "_NR7_" in n and any(x in n for x in ("_1622", "_DAY", "_AM", "_LON0713")):
                STRATS.pop(n, None); STRAT_SYM.pop(n, None)
                STRAT_TF.pop(n, None); STRAT_META.pop(n, None)
                continue
            if "_H4_PB_OVN" in n or "_H4_PB_1622" in n or "_ENGULF_OVN" in n:
                STRATS.pop(n, None); STRAT_SYM.pop(n, None)
                STRAT_TF.pop(n, None); STRAT_META.pop(n, None)
                continue
        # FX: corta H4_PB em LON0713/PM duplicados se já tem LN
        if sym in ("EURUSD", "GBPUSD") and "_H4_PB_LON0713" in n:
            STRATS.pop(n, None); STRAT_SYM.pop(n, None)
            STRAT_TF.pop(n, None); STRAT_META.pop(n, None)
            continue

    # 2ª poda: VWAP fade só curated; corta ruído residual
    FADE_KEEP = {
        "ETH_VWAP_FADE_ASIA", "ETH_VWAP_FADE_NY", "ETH_VWAP_FADE_24H",
        "ETH_VWAP_FADE_24H_M30",
        "EUR_VWAP_FADE_LN", "EUR_VWAP_FADE_LN_V13", "EUR_VWAP_FADE_LN_MT",
        "EUR_VWAP_FADE_LN_D14", "EUR_VWAP_FADE_DAY", "EUR_VWAP_FADE_LON",
        "EUR_VWAP_FADE_NY_M30",
        "GBP_VWAP_FADE_LN", "GBP_VWAP_FADE_NY", "GBP_VWAP_FADE_DAY",
        "GBP_VWAP_FADE_NY_M30",
    }
    for n in list(STRATS):
        sym = STRAT_SYM[n]
        if "VWAP_FADE" in n and n not in FADE_KEEP:
            STRATS.pop(n, None); STRAT_SYM.pop(n, None)
            STRAT_TF.pop(n, None); STRAT_META.pop(n, None)
            continue
        if sym in ("EURUSD", "GBPUSD") and "_V18" in n and "IMP" in n:
            if not any(x in n for x in ("_LON", "_NY", "_LN", "_DAY", "_24H")):
                STRATS.pop(n, None); STRAT_SYM.pop(n, None)
                STRAT_TF.pop(n, None); STRAT_META.pop(n, None)
                continue
        if sym == "ETHUSD" and n.endswith("_MT") and any(
                x in n for x in ("_ENGULF_LON", "_ENGULF_LN",
                                 "_H4_PB_LON", "_H4_PB_LN", "_H4_PB_NIGHT")):
            STRATS.pop(n, None); STRAT_SYM.pop(n, None)
            STRAT_TF.pop(n, None); STRAT_META.pop(n, None)
            continue
        if sym == "ETHUSD" and "_1622" in n and "IMP" not in n:
            STRATS.pop(n, None); STRAT_SYM.pop(n, None)
            STRAT_TF.pop(n, None); STRAT_META.pop(n, None)
            continue
        if sym in ("EURUSD", "GBPUSD"):
            if ("_ENGULF_LON0713" in n or "_ENGULF_24H" in n
                    or "_H4_PB_24H" in n or "_H4_PB_DAY" in n):
                STRATS.pop(n, None); STRAT_SYM.pop(n, None)
                STRAT_TF.pop(n, None); STRAT_META.pop(n, None)
                continue
            if "_INS_24H" in n or "_HL_24H" in n or "_PDH_24H" in n:
                STRATS.pop(n, None); STRAT_SYM.pop(n, None)
                STRAT_TF.pop(n, None); STRAT_META.pop(n, None)
                continue


_build_battery()


def _sim(df, sym, fn, name, tf=15):
    from backtest_setups_novos_v2 import ONE_PER_DAY as _v2_opd
    added = name in ONE_PER_DAY and name not in _v2_opd
    if added:
        _v2_opd.add(name)
    try:
        return _simulate(df, sym, fn, name, tf=tf)
    finally:
        if added:
            _v2_opd.discard(name)


def _worker_init(dfs_by_key):
    global _WORKER_DFS
    _WORKER_DFS = dfs_by_key


def _run_one(name: str) -> dict:
    try:
        sym = STRAT_SYM[name]
        tf = STRAT_TF.get(name, 15)
        key = f"{sym}_M{tf}"
        df = _WORKER_DFS.get(key)
        if df is None:
            return {"setup": name, "sym": sym, "tf": tf, "n": 0,
                    "veredito": "SEM DADOS", "err": f"missing {key}"}
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
            "setup": name, "sym": sym, "tf": tf, **s,
            "cons%": cons, "veredito": v,
            "oos_net": s_out.get("net"), "oos_pf": s_out.get("PF"),
            "oos_n": s_out.get("n", 0),
            "secs": round(time.time() - t0, 1),
        }
        if "🟢" in str(v) and "GO" in str(v) and not t.empty:
            row["_trades"] = t
            row["_meta"] = STRAT_META.get(name)
        return row
    except Exception as exc:
        return {"setup": name, "sym": STRAT_SYM.get(name, "?"),
                "tf": STRAT_TF.get(name, 15), "n": 0,
                "veredito": f"ERRO: {exc}", "err": traceback.format_exc()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bars", type=int, default=50000)
    ap.add_argument("--syms", default="ETHUSD,EURUSD,GBPUSD")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--only", default="", help="csv de nomes (debug)")
    args = ap.parse_args()

    want = {s.strip().upper() for s in args.syms.split(",") if s.strip()}
    os.environ["KIMI_GROUP"] = "crypto"
    os.makedirs("logs", exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    log_path = f"logs/backtest_setups_novos_v17_eth_fx_{stamp}.txt"
    logf = open(log_path, "w", encoding="utf-8")

    def out(s):
        try:
            print(s)
        except UnicodeEncodeError:
            print(s.encode("ascii", "replace").decode("ascii"))
        logf.write(s + "\n")
        logf.flush()

    only = {x.strip() for x in args.only.split(",") if x.strip()}
    strat_names = [n for n in STRATS
                   if STRAT_SYM[n] in want and (not only or n in only)]
    by_sym = {s: sum(1 for n in strat_names if STRAT_SYM[n] == s)
              for s in sorted(want)}
    out(f"{'#' * 74}\n# SETUPS NOVOS V17 — ETH/EUR/GBP EXPAND "
        f"({len(strat_names)} setups) · {datetime.now():%d/%m %H:%M}\n"
        f"# Por ativo: {by_sym} · workers={args.workers}\n"
        f"# SKIP: GOs já wired · SEM FDS em EUR/GBP\n"
        f"# Régua: net≥+0.10 · PF≥1.25 · cons≥55% · OOS+ · n≥40\n{'#' * 74}")

    from backtest_crypto_pro import mt5_connect
    mt5 = mt5_connect()

    need_keys = sorted({f"{STRAT_SYM[n]}_M{STRAT_TF.get(n, 15)}"
                        for n in strat_names})
    dfs = {}
    for key in need_keys:
        sym, _, tf_s = key.partition("_M")
        tf = int(tf_s)
        if sym not in want:
            continue
        t0 = time.time()
        raw = _load_ohlc(mt5, sym, tf, args.bars, out)
        if raw is None or len(raw) < 2000:
            out(f"\n!! {key}: sem histórico")
            continue
        df = add_extra_v2(add_indicators(raw), sym, ctx=None)
        dfs[key] = df
        anos = len(df) / (96 if tf == 15 else 48) / 365
        out(f"\n{'=' * 74}\n  {key}: {len(df)} candles (~{anos:.1f}a)  "
            f"[{df.index[0].date()} -> {df.index[-1].date()}] "
            f"({time.time() - t0:.0f}s)\n{'=' * 74}")

    ranking = []
    t_all = time.time()
    n_done = 0
    workers = max(1, int(args.workers))

    if workers == 1:
        _worker_init(dfs)
        for name in strat_names:
            row = _run_one(name)
            trades = row.pop("_trades", None)
            row.pop("_meta", None)
            ranking.append(row)
            n_done += 1
            out(f"\n  ▸ {row['setup']}  ({row.get('sym')} M{row.get('tf')})  "
                f"[{row.get('secs', '?')}s]")
            if row.get("n", 0):
                out(f"      n={int(row['n'])} net {row.get('net', 0):+.3f} R  "
                    f"PF {row.get('PF')} cons {row.get('cons%')}%  "
                    f"OOS {row.get('oos_net')}  {row.get('veredito')}")
            else:
                out(f"      {row.get('veredito', 'sem trades')}")
            if trades is not None:
                trades.to_csv(
                    f"logs/novos_v17_{row['sym']}_M{row['tf']}_{row['setup']}.csv",
                    index=False)
            if n_done % 25 == 0:
                out(f"  … progresso {n_done}/{len(strat_names)}")
    else:
        out(f"\n# paralelizando com {workers} workers…")
        with ProcessPoolExecutor(
            max_workers=workers,
            initializer=_worker_init,
            initargs=(dfs,),
        ) as ex:
            futs = {ex.submit(_run_one, n): n for n in strat_names}
            for fut in as_completed(futs):
                row = fut.result()
                trades = row.pop("_trades", None)
                row.pop("_meta", None)
                ranking.append(row)
                n_done += 1
                out(f"\n  ▸ {row['setup']}  ({row.get('sym')} M{row.get('tf')})  "
                    f"[{row.get('secs', '?')}s]")
                if row.get("n", 0):
                    out(f"      n={int(row['n'])} net {row.get('net', 0):+.3f} R  "
                        f"PF {row.get('PF')} cons {row.get('cons%')}%  "
                        f"OOS {row.get('oos_net')}  {row.get('veredito')}")
                else:
                    out(f"      {row.get('veredito', 'sem trades')}")
                if trades is not None:
                    trades.to_csv(
                        f"logs/novos_v17_{row['sym']}_M{row['tf']}_{row['setup']}.csv",
                        index=False)
                if n_done % 25 == 0:
                    out(f"  … progresso {n_done}/{len(strat_names)} "
                        f"({time.time() - t_all:.0f}s)")

    out(f"\n{'#' * 74}\n  RANKING V17 ETH/EUR/GBP  ({time.time() - t_all:.0f}s total)\n"
        f"{'#' * 74}")
    rk = pd.DataFrame(ranking)
    goes, yellows, reds = [], [], []
    goes_by = {s: [] for s in want}
    goes_meta = []
    if not rk.empty:
        rk["net"] = pd.to_numeric(rk.get("net"), errors="coerce")
        rk = rk.sort_values("net", ascending=False, na_position="last")
        for _, r in rk.iterrows():
            if r.get("n", 0) == 0:
                out(f"  {r.setup:<36} sem trades / {r.get('veredito')}")
                continue
            out(f"  {r.setup:<36} n={int(r.n):<5} net {r.net:+.3f} R  "
                f"PF {r.PF:<5} cons {r['cons%']}%  OOS {r.oos_net}  {r.veredito}")
            vs = str(r.veredito)
            if "🟢" in vs and "GO" in vs:
                goes.append(r.setup)
                goes_by.setdefault(r.sym, []).append(r.setup)
                m = STRAT_META.get(r.setup)
                if m:
                    goes_meta.append(m)
            elif "🟡" in vs:
                yellows.append(r.setup)
            else:
                reds.append(r.setup)
        save = rk.drop(columns=[c for c in ("err",) if c in rk.columns],
                       errors="ignore")
        save.to_csv(f"logs/backtest_setups_novos_v17_eth_fx_ranking_{stamp}.csv",
                    index=False)
        if goes_meta:
            import json
            with open(f"logs/v17_goes_meta_{stamp}.json", "w", encoding="utf-8") as f:
                json.dump(goes_meta, f, indent=2, ensure_ascii=False)

    out(f"\n# GO encontrados ({len(goes)}): {goes if goes else 'nenhum'}")
    for s in sorted(want):
        out(f"#   {s}: {len(goes_by.get(s, []))} → {goes_by.get(s, [])}")
    out(f"# amarelos ({len(yellows)}): {yellows[:60]}{'…' if len(yellows) > 60 else ''}")
    out(f"# vermelhos/fracos: {len(reds)}")
    out(f"# FIM — {log_path}")
    logf.close()
    print(f"\n-> {log_path}")
    if goes:
        print("GO:", ", ".join(goes))
    print(f"TOTAL setups: {len(strat_names)}")


if __name__ == "__main__":
    main()
