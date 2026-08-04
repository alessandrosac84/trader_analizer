"""
backtest_setups_novos_v16_xau_fx.py — mega bateria XAU / EUR / GBP (pós-v15 BTC/ETH).

Foco (sem retestar 🟢 já wired):
  · XAU: Lon/NY/Asia/OVN/night · NR/INS/HL/PDH/IMP/engolfo/VWAP/EMA · TP cap 30 pts
  · EUR/GBP: pregão weekday (Lon/NY/Asia/OVN) — SEM FDS/WE/fri_sun
  · refine MT / vol / RR / M30

Régua GO: net ≥ +0,10 R · PF ≥ 1,25 · cons ≥ 55% · OOS segura · n ≥ 40.
NÃO altera live até 🟢.

Uso:
  python backtest_setups_novos_v16_xau_fx.py
  python backtest_setups_novos_v16_xau_fx.py --workers 6 --bars 50000
  python backtest_setups_novos_v16_xau_fx.py --syms XAUUSD --workers 8
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
STRAT_META = {}  # name -> dict for registry export

_WORKER_DFS = {}

# Cap estrutural XAU (live): |entry−tp| ≤ 30 pts
XAU_TP_CAP = 30.0
# Cap sensato FX (30 pips) — só em variantes *_CAP
FX_TP_CAP = 0.0030


def _reg(name, fn, sym="XAUUSD", one_per_day=False, tf=15, meta=None):
    STRATS[name] = fn
    STRAT_SYM[name] = sym
    STRAT_TF[name] = tf
    if one_per_day:
        ONE_PER_DAY.add(name)
    if meta:
        STRAT_META[name] = meta


def _pts_cap(fn, cap: float, rr1=1.5):
    """Live-like: TP único com |entry−tp| ≤ cap pts (mantém SL)."""
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


def _xau(fn, rr1=1.5):
    """Aplica cap 30 pts no XAU (espelha live)."""
    return _pts_cap(fn, XAU_TP_CAP, rr1=rr1)


def _fxcap(fn, rr1=1.5):
    return _pts_cap(fn, FX_TP_CAP, rr1=rr1)


def _weekday(fn):
    """Seg–sex (dow 0–4) — pregão FX/XAU sem FDS."""
    def _f(df, i, sym):
        if int(df.iloc[i].dow) not in (0, 1, 2, 3, 4):
            return None
        return fn(df, i, sym)
    return _f


# Sessões alvo (label, hh0, hh1)
SESS_CORE = [
    ("LON", 8.0, 12.0),
    ("LON0713", 7.0, 13.0),
    ("LN", 8.0, 17.0),
    ("NY", 13.0, 17.0),
    ("NY1418", 14.0, 18.0),
    ("DAY", 10.0, 16.0),
    ("AM", 9.0, 12.0),
    ("PM", 12.0, 16.0),
    ("1622", 16.0, 22.0),
    ("ASIA", 0.0, 8.0),
    ("NIGHT", 20.0, 10.0),
    ("OVN", 18.0, 8.0),
    ("24H", 0.0, 24.0),
]

# Tags já wired — NÃO registrar idênticos
SKIP = {
    # XAU
    ("XAUUSD", "nr", 4, 1.3, 10.0, 16.0, None, 1.5, 15),
    ("XAUUSD", "pdh", None, 1.2, 10.0, 16.0, None, 1.5, 15),
    ("XAUUSD", "pdh", None, 1.2, 11.0, 15.0, None, 1.5, 15),
    ("XAUUSD", "hl", None, 1.3, 0.0, 24.0, None, 1.5, 15),
    ("XAUUSD", "inside", None, 1.2, 9.0, 12.0, None, 1.5, 15),  # INS_AM approx
    ("XAUUSD", "imp", None, 1.5, 9.0, 18.0, None, 2.0, 15),
    # EUR
    ("EURUSD", "pdh", None, 1.2, 10.0, 16.0, None, 1.5, 15),
    ("EURUSD", "imp", None, 1.5, 0.0, 24.0, "mt", 2.0, 15),
    # GBP
    ("GBPUSD", "inside", None, 1.3, 9.0, 12.0, None, 1.5, 15),
    ("GBPUSD", "inside", None, 1.3, 10.0, 17.0, None, 1.5, 15),
}


def _skip_key(sym, kind, n, vol, hh0, hh1, session, tp_r, tf):
    return (sym, kind, n, round(vol, 2), round(hh0, 1), round(hh1, 1), session, round(tp_r, 2), tf)


def _wrap(fn, sym, session=None, pts_cap=None, rr_cap=None):
    out = fn
    if session == "mt":
        out = _mt(out)
    elif session == "wd":
        out = _weekday(out)
    if pts_cap is not None:
        out = _pts_cap(out, pts_cap, rr1=float(rr_cap or 1.5))
    elif sym == "XAUUSD":
        # default live-like cap on structural families
        out = _xau(out, rr1=float(rr_cap or 1.5))
    return out


def _add(name, fn, sym, kind, *, n=None, vol=1.3, hh0=0.0, hh1=24.0,
         session=None, tp_r=1.5, tf=15, dist=None, title="",
         pts_cap=None, apply_xau_cap=True):
    key = _skip_key(sym, kind, n, vol, hh0, hh1, session, tp_r, tf)
    if key in SKIP:
        return
    # avoid duplicate names
    if name in STRATS:
        return
    use_cap = pts_cap
    if apply_xau_cap and sym == "XAUUSD" and pts_cap is None and kind in (
            "inside", "nr", "hl", "pdh", "imp", "engulf", "out", "ema_recl",
            "vwap_cont", "vwap_fade", "h4_pb"):
        use_cap = XAU_TP_CAP
    wrapped = fn
    if session == "mt":
        wrapped = _mt(wrapped)
    elif session == "wd":
        wrapped = _weekday(wrapped)
    if use_cap is not None:
        wrapped = _pts_cap(wrapped, use_cap, rr1=float(tp_r))
    elif tp_r != 1.5 and kind not in ("imp",):
        # single TP when custom rr without pts cap
        if kind in ("hl", "pdh", "nr", "inside", "engulf"):
            wrapped = _cap_tp(wrapped, float(tp_r))
    meta = {
        "name": name, "sym": sym, "tf": tf, "kind": kind,
        "n": n, "vol": vol, "hh0": hh0, "hh1": hh1,
        "session": session if session in ("mt",) else None,
        "tp_r": tp_r, "dist": dist, "title": title or name,
        "tp_cap": use_cap,
    }
    # weekday filter for EUR/GBP is implicit in session windows (no WE regs)
    _reg(name, wrapped, sym=sym, tf=tf, meta=meta)


def _build_battery():
    """~280–330 variantes focadas em buracos (sem explosão combinatorial)."""
    # Sessões prioritárias (pregão FX + overnight weekday)
    SESS = [
        ("LON", 8.0, 12.0),
        ("LON0713", 7.0, 13.0),
        ("LN", 8.0, 17.0),
        ("NY", 13.0, 17.0),
        ("DAY", 10.0, 16.0),
        ("ASIA", 0.0, 8.0),
        ("OVN", 18.0, 8.0),
        ("NIGHT", 20.0, 10.0),
        ("24H", 0.0, 24.0),
    ]
    SESS_FX = [s for s in SESS if s[0] != "ASIA"] + [("ASIA", 0.0, 8.0)]  # Asia ok weekday open

    # ── XAU (~110) ───────────────────────────────────────
    sx = "XAUUSD"
    # HL — buracos Lon/NY/Asia/OVN (24h base já GO)
    for tag, h0, h1 in SESS:
        if tag == "24H":
            _add("XAU_HL_24H_MT", _hl_h(1.3, 0, 24), sx, "hl",
                 vol=1.3, hh0=0, hh1=24, session="mt", title="HL 24h · seg–qui")
            _add("XAU_HL_24H_V14", _hl_h(1.4, 0, 24), sx, "hl",
                 vol=1.4, hh0=0, hh1=24, title="HL vol≥1.4× · 24h")
            _add("XAU_HL_24H_TP20", _hl_h(1.3, 0, 24), sx, "hl",
                 vol=1.3, hh0=0, hh1=24, tp_r=2.0, title="HL TP 2R · 24h")
            continue
        _add(f"XAU_HL_{tag}", _hl_h(1.4, h0, h1), sx, "hl",
             vol=1.4, hh0=h0, hh1=h1, title=f"HL vol≥1.4× · {tag}")
        _add(f"XAU_HL_{tag}_MT", _hl_h(1.4, h0, h1), sx, "hl",
             vol=1.4, hh0=h0, hh1=h1, session="mt", title=f"HL · {tag} · seg–qui")
        if tag in ("LON", "NY", "LN", "OVN"):
            _add(f"XAU_HL_{tag}_V15", _hl_h(1.5, h0, h1), sx, "hl",
                 vol=1.5, hh0=h0, hh1=h1, title=f"HL vol≥1.5× · {tag}")

    # NR4/NR5 (skip NR4 DAY = wired)
    for n in (4, 5):
        for tag, h0, h1 in SESS:
            if n == 4 and tag == "DAY":
                continue
            _add(f"XAU_NR{n}_{tag}", _nr_h(n, 1.4, h0, h1), sx, "nr",
                 n=n, vol=1.4, hh0=h0, hh1=h1, title=f"NR{n} vol≥1.4× · {tag}")
            if tag in ("LON", "NY", "LN", "OVN", "24H", "ASIA"):
                _add(f"XAU_NR{n}_{tag}_MT", _nr_h(n, 1.4, h0, h1), sx, "nr",
                     n=n, vol=1.4, hh0=h0, hh1=h1, session="mt",
                     title=f"NR{n} · {tag} · seg–qui")
    _add("XAU_NR7_LN", _nr_h(7, 1.4, 8, 17), sx, "nr",
         n=7, vol=1.4, hh0=8, hh1=17, title="NR7 · Lon+NY")
    _add("XAU_NR7_24H", _nr_h(7, 1.4, 0, 24), sx, "nr",
         n=7, vol=1.4, hh0=0, hh1=24, title="NR7 · 24h")

    # Inside (skip AM)
    for tag, h0, h1 in SESS:
        if tag == "AM":
            continue
        _add(f"XAU_INS_{tag}", _inside_h(1.3, h0, h1), sx, "inside",
             vol=1.3, hh0=h0, hh1=h1, title=f"inside vol≥1.3× · {tag}")
        if tag in ("LON", "NY", "LN", "DAY", "OVN"):
            _add(f"XAU_INS_{tag}_MT", _inside_h(1.3, h0, h1), sx, "inside",
                 vol=1.3, hh0=h0, hh1=h1, session="mt",
                 title=f"inside · {tag} · seg–qui")
    _add("XAU_DBL_INS_LN", _dbl_inside_h(1.3, 8, 17), sx, "inside",
         vol=1.3, hh0=8, hh1=17, title="dbl-inside · Lon+NY")

    # PDH (skip DAY 1.2 e 11–15)
    for tag, h0, h1 in SESS:
        if tag == "DAY":
            _add("XAU_PDH_DAY_V13", _pdh_h(1.3, 10, 16), sx, "pdh",
                 vol=1.3, hh0=10, hh1=16, title="PDH vol≥1.3× · 10–16")
            _add("XAU_PDH_DAY_MT", _pdh_h(1.2, 10, 16), sx, "pdh",
                 vol=1.2, hh0=10, hh1=16, session="mt", title="PDH 10–16 · seg–qui")
            continue
        _add(f"XAU_PDH_{tag}", _pdh_h(1.2, h0, h1), sx, "pdh",
             vol=1.2, hh0=h0, hh1=h1, title=f"PDH · {tag}")
        if tag in ("LON", "NY", "LN", "OVN", "24H", "ASIA"):
            _add(f"XAU_PDH_{tag}_MT", _pdh_h(1.3, h0, h1), sx, "pdh",
                 vol=1.3, hh0=h0, hh1=h1, session="mt", title=f"PDH · {tag} · seg–qui")

    # Impulse sessões
    for tag, h0, h1 in [("LON", 8, 12), ("NY", 13, 17), ("LN", 8, 17),
                        ("ASIA", 0, 8), ("OVN", 18, 8), ("24H", 0, 24)]:
        _add(f"XAU_IMP_{tag}", _impulse_h(2.0, 1.5, h0, h1, 2.0), sx, "imp",
             vol=1.5, hh0=h0, hh1=h1, tp_r=2.0, title=f"impulso · {tag}")
        _add(f"XAU_IMP_{tag}_MT", _impulse_h(2.0, 1.5, h0, h1, 2.0), sx, "imp",
             vol=1.5, hh0=h0, hh1=h1, session="mt", tp_r=2.0,
             title=f"impulso · {tag} · seg–qui")
        _add(f"XAU_IMP_{tag}_V18", _impulse_h(1.8, 1.6, h0, h1, 2.0), sx, "imp",
             vol=1.6, hh0=h0, hh1=h1, tp_r=2.0, title=f"impulso V18 · {tag}")

    # Famílias novas por sessão chave
    for tag, h0, h1 in [("LON", 8, 12), ("NY", 13, 17), ("LN", 8, 17),
                        ("OVN", 18, 8), ("24H", 0, 24)]:
        _add(f"XAU_ENGULF_{tag}", _engulf_h(1.4, h0, h1), sx, "engulf",
             vol=1.4, hh0=h0, hh1=h1, title=f"engulf · {tag}")
        _add(f"XAU_OUT_{tag}", _outside_h(1.4, h0, h1), sx, "out",
             vol=1.4, hh0=h0, hh1=h1, title=f"outside · {tag}")
        _add(f"XAU_EMA_RECL_{tag}", _ema_reclaim_h(h0, h1, 1.3), sx, "ema_recl",
             vol=1.3, hh0=h0, hh1=h1, title=f"EMA reclaim · {tag}")
        _add(f"XAU_VWAP_CONT_{tag}", _vwap_cont_h(1.3, 0.3, h0, h1), sx, "vwap_cont",
             vol=1.3, hh0=h0, hh1=h1, dist=0.3, title=f"VWAP cont · {tag}")
        _add(f"XAU_VWAP_FADE_{tag}", _vwap_fade_h(1.2, 1.2, h0, h1), sx, "vwap_fade",
             vol=1.2, hh0=h0, hh1=h1, dist=1.2, title=f"VWAP fade · {tag}")
        _add(f"XAU_H4_PB_{tag}", _h4_pb_ema_h(h0, h1), sx, "h4_pb",
             hh0=h0, hh1=h1, tp_r=2.0, title=f"H4 PB EMA · {tag}")
    _add("XAU_SQZ_LN", _sqz_break_h(1.5, 8, 17), sx, "sqz",
         vol=1.5, hh0=8, hh1=17, title="squeeze · Lon+NY")
    _add("XAU_SQZ_24H_MT", _sqz_break_h(1.5, 0, 24), sx, "sqz",
         vol=1.5, hh0=0, hh1=24, session="mt", title="squeeze 24h · seg–qui")
    _add("XAU_VOLSPIKE_LN", _volspike_h(2.0, 1.5, 8, 17), sx, "volspike",
         vol=2.0, hh0=8, hh1=17, title="volspike · Lon+NY")

    # M30 XAU
    for name, fn, kind, kw in [
        ("XAU_HL_LN_M30", _hl_h(1.4, 8, 17), "hl", {"vol": 1.4, "hh0": 8, "hh1": 17}),
        ("XAU_HL_24H_MT_M30", _mt(_hl_h(1.4, 0, 24)), "hl",
         {"vol": 1.4, "hh0": 0, "hh1": 24, "session": "mt"}),
        ("XAU_NR5_LN_M30", _nr_h(5, 1.4, 8, 17), "nr",
         {"n": 5, "vol": 1.4, "hh0": 8, "hh1": 17}),
        ("XAU_PDH_LN_M30", _pdh_h(1.2, 8, 17), "pdh",
         {"vol": 1.2, "hh0": 8, "hh1": 17}),
        ("XAU_INS_LN_M30", _inside_h(1.3, 8, 17), "inside",
         {"vol": 1.3, "hh0": 8, "hh1": 17}),
        ("XAU_ENGULF_24H_M30", _engulf_h(1.4, 0, 24), "engulf",
         {"vol": 1.4, "hh0": 0, "hh1": 24}),
        ("XAU_IMP_LN_M30", _impulse_h(2.0, 1.5, 8, 17, 2.0), "imp",
         {"vol": 1.5, "hh0": 8, "hh1": 17, "tp_r": 2.0}),
        ("XAU_EMA_RECL_24H_M30", _ema_reclaim_h(0, 24, 1.3), "ema_recl",
         {"vol": 1.3, "hh0": 0, "hh1": 24}),
    ]:
        # _mt already applied in fn for MT_M30 — avoid double wrap
        sess = kw.pop("session", None) if "session" in kw else None
        if name.endswith("_MT_M30"):
            _reg(name, _xau(fn), sym=sx, tf=30, meta={
                "name": name, "sym": sx, "tf": 30, "kind": kind,
                "vol": kw.get("vol", 1.3), "hh0": kw.get("hh0", 0), "hh1": kw.get("hh1", 24),
                "session": "mt", "tp_r": kw.get("tp_r", 1.5), "n": kw.get("n"),
                "title": f"{kind} · M30 · seg–qui", "tp_cap": XAU_TP_CAP,
            })
        else:
            _add(name, fn, sx, kind, tf=30, session=sess, title=f"{kind} · M30",
                 **{k: v for k, v in kw.items() if k in ("n", "vol", "hh0", "hh1", "tp_r")})

    # ── EUR / GBP (~100 each) ────────────────────────────
    for sx in ("EURUSD", "GBPUSD"):
        pfx = "EUR" if sx == "EURUSD" else "GBP"

        # Inside
        for tag, h0, h1 in SESS:
            if sx == "GBPUSD" and tag in ("DAY",):  # overlap 10-17 wired via separate
                pass
            if sx == "GBPUSD" and abs(h0 - 9) < 0.01 and abs(h1 - 12) < 0.01:
                continue
            _add(f"{pfx}_INS_{tag}", _inside_h(1.3, h0, h1), sx, "inside",
                 vol=1.3, hh0=h0, hh1=h1, title=f"inside · {tag}")
            if tag in ("LON", "NY", "LN", "24H", "OVN"):
                _add(f"{pfx}_INS_{tag}_MT", _inside_h(1.3, h0, h1), sx, "inside",
                     vol=1.3, hh0=h0, hh1=h1, session="mt",
                     title=f"inside · {tag} · seg–qui")
        if sx == "EURUSD":
            _add("EUR_INS_AM", _inside_h(1.3, 9, 12), sx, "inside",
                 vol=1.3, hh0=9, hh1=12, title="inside · 09–12")
            _add("EUR_INS_1017", _inside_h(1.3, 10, 17), sx, "inside",
                 vol=1.3, hh0=10, hh1=17, title="inside · 10–17")
        # GBP 10-17 wired — skip exact; LN 08-17 is different

        # NR4/NR5
        for n in (4, 5):
            for tag, h0, h1 in SESS:
                _add(f"{pfx}_NR{n}_{tag}", _nr_h(n, 1.4, h0, h1), sx, "nr",
                     n=n, vol=1.4, hh0=h0, hh1=h1, title=f"NR{n} · {tag}")
                if tag in ("LON", "NY", "LN", "DAY", "24H"):
                    _add(f"{pfx}_NR{n}_{tag}_MT", _nr_h(n, 1.4, h0, h1), sx, "nr",
                         n=n, vol=1.4, hh0=h0, hh1=h1, session="mt",
                         title=f"NR{n} · {tag} · seg–qui")

        # HL
        for tag, h0, h1 in SESS:
            _add(f"{pfx}_HL_{tag}", _hl_h(1.4, h0, h1), sx, "hl",
                 vol=1.4, hh0=h0, hh1=h1, title=f"HL · {tag}")
            _add(f"{pfx}_HL_{tag}_MT", _hl_h(1.4, h0, h1), sx, "hl",
                 vol=1.4, hh0=h0, hh1=h1, session="mt", title=f"HL · {tag} · seg–qui")
            if tag in ("LON", "NY", "LN", "24H"):
                _add(f"{pfx}_HL_{tag}_TP20", _hl_h(1.4, h0, h1), sx, "hl",
                     vol=1.4, hh0=h0, hh1=h1, tp_r=2.0, title=f"HL TP 2R · {tag}")

        # PDH
        for tag, h0, h1 in SESS:
            if sx == "EURUSD" and tag == "DAY":
                _add("EUR_PDH_DAY_V13", _pdh_h(1.3, 10, 16), sx, "pdh",
                     vol=1.3, hh0=10, hh1=16, title="PDH vol≥1.3× · 10–16")
                _add("EUR_PDH_DAY_MT", _pdh_h(1.2, 10, 16), sx, "pdh",
                     vol=1.2, hh0=10, hh1=16, session="mt", title="PDH 10–16 · seg–qui")
                continue
            _add(f"{pfx}_PDH_{tag}", _pdh_h(1.2, h0, h1), sx, "pdh",
                 vol=1.2, hh0=h0, hh1=h1, title=f"PDH · {tag}")
            if tag in ("LON", "NY", "LN", "24H", "OVN"):
                _add(f"{pfx}_PDH_{tag}_MT", _pdh_h(1.3, h0, h1), sx, "pdh",
                     vol=1.3, hh0=h0, hh1=h1, session="mt",
                     title=f"PDH · {tag} · seg–qui")

        # Impulse
        for tag, h0, h1 in [("LON", 8, 12), ("NY", 13, 17), ("LN", 8, 17),
                            ("DAY", 10, 16), ("OVN", 18, 8), ("24H", 0, 24)]:
            if sx == "EURUSD" and tag == "24H":
                _add("EUR_IMP_24H", _impulse_h(2.0, 1.5, 0, 24, 2.0), sx, "imp",
                     vol=1.5, hh0=0, hh1=24, tp_r=2.0, title="impulso · 24h")
                _add("EUR_IMP_24H_V18_MT", _impulse_h(1.8, 1.6, 0, 24, 2.0), sx, "imp",
                     vol=1.6, hh0=0, hh1=24, session="mt", tp_r=2.0,
                     title="impulso V18 · 24h · seg–qui")
                continue
            _add(f"{pfx}_IMP_{tag}", _impulse_h(2.0, 1.5, h0, h1, 2.0), sx, "imp",
                 vol=1.5, hh0=h0, hh1=h1, tp_r=2.0, title=f"impulso · {tag}")
            _add(f"{pfx}_IMP_{tag}_MT", _impulse_h(2.0, 1.5, h0, h1, 2.0), sx, "imp",
                 vol=1.5, hh0=h0, hh1=h1, session="mt", tp_r=2.0,
                 title=f"impulso · {tag} · seg–qui")

        # Engulf / EMA / VWAP / H4 PB / Out (sessões chave)
        for tag, h0, h1 in [("LON", 8, 12), ("NY", 13, 17), ("LN", 8, 17), ("24H", 0, 24)]:
            _add(f"{pfx}_ENGULF_{tag}", _engulf_h(1.4, h0, h1), sx, "engulf",
                 vol=1.4, hh0=h0, hh1=h1, title=f"engulf · {tag}")
            _add(f"{pfx}_ENGULF_{tag}_MT", _engulf_h(1.4, h0, h1), sx, "engulf",
                 vol=1.4, hh0=h0, hh1=h1, session="mt", title=f"engulf · {tag} · seg–qui")
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
                 hh0=h0, hh1=h1, session="mt", tp_r=2.0, title=f"H4 PB · {tag} · seg–qui")

        # Cap 30 pips (estrutural)
        for tag, h0, h1 in [("LON", 8, 12), ("NY", 13, 17), ("LN", 8, 17)]:
            _add(f"{pfx}_HL_{tag}_CAP30", _hl_h(1.4, h0, h1), sx, "hl",
                 vol=1.4, hh0=h0, hh1=h1, pts_cap=FX_TP_CAP,
                 title=f"HL · {tag} · TP cap 30pips")
            _add(f"{pfx}_PDH_{tag}_CAP30", _pdh_h(1.2, h0, h1), sx, "pdh",
                 vol=1.2, hh0=h0, hh1=h1, pts_cap=FX_TP_CAP,
                 title=f"PDH · {tag} · TP cap 30pips")

        # M30
        for name_s, fn, kind, kw in [
            (f"{pfx}_HL_LN_M30", _hl_h(1.4, 8, 17), "hl", {"vol": 1.4, "hh0": 8, "hh1": 17}),
            (f"{pfx}_NR5_LN_M30", _nr_h(5, 1.4, 8, 17), "nr",
             {"n": 5, "vol": 1.4, "hh0": 8, "hh1": 17}),
            (f"{pfx}_PDH_LN_M30", _pdh_h(1.2, 8, 17), "pdh",
             {"vol": 1.2, "hh0": 8, "hh1": 17}),
            (f"{pfx}_INS_LN_M30", _inside_h(1.3, 8, 17), "inside",
             {"vol": 1.3, "hh0": 8, "hh1": 17}),
            (f"{pfx}_ENGULF_LN_M30", _engulf_h(1.4, 8, 17), "engulf",
             {"vol": 1.4, "hh0": 8, "hh1": 17}),
            (f"{pfx}_IMP_LN_M30", _impulse_h(2.0, 1.5, 8, 17, 2.0), "imp",
             {"vol": 1.5, "hh0": 8, "hh1": 17, "tp_r": 2.0}),
        ]:
            _add(name_s, fn, sx, kind, tf=30, title=f"{kind} · Lon+NY · M30",
                 **{k: v for k, v in kw.items() if k in ("n", "vol", "hh0", "hh1", "tp_r")})

    # Poda leve → alvo ~320–360 (remove Asia/Night FX finos + CAP + LON0713 XAU)
    for n in list(STRATS):
        sym = STRAT_SYM[n]
        if sym in ("EURUSD", "GBPUSD") and ("_ASIA" in n or "_NIGHT" in n):
            STRATS.pop(n, None); STRAT_SYM.pop(n, None); STRAT_TF.pop(n, None); STRAT_META.pop(n, None)
        elif "_CAP30" in n:
            STRATS.pop(n, None); STRAT_SYM.pop(n, None); STRAT_TF.pop(n, None); STRAT_META.pop(n, None)
        elif sym == "XAUUSD" and ("_LON0713" in n or n.endswith("_V15")):
            STRATS.pop(n, None); STRAT_SYM.pop(n, None); STRAT_TF.pop(n, None); STRAT_META.pop(n, None)


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
    ap.add_argument("--syms", default="XAUUSD,EURUSD,GBPUSD")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--only", default="", help="csv de nomes (debug)")
    args = ap.parse_args()

    want = {s.strip().upper() for s in args.syms.split(",") if s.strip()}
    os.environ["KIMI_GROUP"] = "crypto"
    os.makedirs("logs", exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    log_path = f"logs/backtest_setups_novos_v16_xau_fx_{stamp}.txt"
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
    by_sym = {s: sum(1 for n in strat_names if STRAT_SYM[n] == s) for s in sorted(want)}
    out(f"{'#' * 74}\n# SETUPS NOVOS V16 — XAU/EUR/GBP EXPAND "
        f"({len(strat_names)} setups) · {datetime.now():%d/%m %H:%M}\n"
        f"# Por ativo: {by_sym} · workers={args.workers}\n"
        f"# SKIP: GOs já wired · SEM FDS em EUR/GBP\n"
        f"# XAU TP cap 30 pts · régua rigorosa\n{'#' * 74}")

    from backtest_crypto_pro import mt5_connect
    mt5 = mt5_connect()

    need_keys = sorted({f"{STRAT_SYM[n]}_M{STRAT_TF.get(n, 15)}" for n in strat_names})
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
                    f"logs/novos_v16_{row['sym']}_M{row['tf']}_{row['setup']}.csv",
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
                        f"logs/novos_v16_{row['sym']}_M{row['tf']}_{row['setup']}.csv",
                        index=False)
                if n_done % 25 == 0:
                    out(f"  … progresso {n_done}/{len(strat_names)} "
                        f"({time.time() - t_all:.0f}s)")

    out(f"\n{'#' * 74}\n  RANKING V16 XAU/EUR/GBP  ({time.time() - t_all:.0f}s total)\n"
        f"{'#' * 74}")
    rk = pd.DataFrame(ranking)
    goes, yellows, reds = [], [], []
    goes_by = {s: [] for s in want}
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
            elif "🟡" in vs:
                yellows.append(r.setup)
            else:
                reds.append(r.setup)
        save = rk.drop(columns=[c for c in ("err",) if c in rk.columns], errors="ignore")
        save.to_csv(f"logs/backtest_setups_novos_v16_xau_fx_ranking_{stamp}.csv",
                    index=False)

    out(f"\n# GO encontrados ({len(goes)}): {goes if goes else 'nenhum'}")
    for s in sorted(want):
        out(f"#   {s}: {len(goes_by.get(s, []))} → {goes_by.get(s, [])}")
    out(f"# amarelos ({len(yellows)}): {yellows[:50]}{'…' if len(yellows) > 50 else ''}")
    out(f"# vermelhos/fracos: {len(reds)}")
    out(f"# FIM — {log_path}")
    logf.close()
    print(f"\n-> {log_path}")
    if goes:
        print("GO:", ", ".join(goes))
    print(f"TOTAL setups: {len(strat_names)}")


if __name__ == "__main__":
    main()
