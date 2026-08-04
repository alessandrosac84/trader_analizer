"""
backtest_setups_novos_v20_btc_eth.py — BTC/ETH famílias NOVAS (pós v14–v18).

SKIP (já cobertos/wired): INS/NR/HL/PDH/IMP/VWAP-cont/EMA-recl/engulf clássicos
  das baterias v13–v18.

Famílias novas:
  · 3-bar momentum + vol + H4
  · Donchian-20 break + H4
  · BB fade (mean-reversion stretch) + H4
  · RSI reclaim (oversold/overbought) + H4
  · False-break / spring (rompe e fecha de volta)
  · Pin-bar rejection + H4
  · M60: NR / HL / inside / momo (ETH já tem disc M60)
  · Live-cap aware: TP clipado a caps live BTC/ETH no BT

Régua GO: net ≥ +0,10 R · PF ≥ 1,25 · cons ≥ 55% · OOS segura · n ≥ 40.
NÃO altera live até 🟢.

Uso:
  python backtest_setups_novos_v20_btc_eth.py
  python backtest_setups_novos_v20_btc_eth.py --workers 6 --bars 50000
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
from backtest_setups_novos import stats, consistency, verdict, _line, OOS_FRAC, _sig
from backtest_setups_novos_v2 import add_extra_v2, _simulate
from backtest_setups_novos_v5 import _h4_ok
from backtest_setups_novos_v9_refine import _mt
from backtest_setups_novos_v13_btc_24h import (
    _hh_ok, _inside_h, _nr_h, _hl_h, _load_ohlc,
)
from backtest_setups_novos_v14_btc_eth import _we, _fri_sun
from backtest_setups_novos_v15_btc_eth import _cap_tp, _fri_mon, _atr_regime

STRATS = {}
STRAT_SYM = {}
ONE_PER_DAY = set()
STRAT_TF = {}
_WORKER_DFS = {}

# Caps live (espelho crypto_edge_setups.LIVE_SL_TP_CAPS) — pts
_LIVE_TP = {"BTCUSD": 1200.0, "ETHUSD": 20.0}


def _reg(name, fn, sym="BTCUSD", one_per_day=False, tf=15):
    STRATS[name] = fn
    STRAT_SYM[name] = sym
    STRAT_TF[name] = tf
    if one_per_day:
        ONE_PER_DAY.add(name)


def _live_cap(fn, sym):
    """Clipa |entry−tp| ao cap live do ativo (fidelidade ao fill)."""
    cap = _LIVE_TP.get(sym)
    if not cap:
        return fn

    def _f(df, i, s):
        base = fn(df, i, s)
        if not base:
            return None
        entry = float(df.iloc[i].Close)
        d, sl = base["dir"], float(base["sl"])
        risk = abs(entry - sl)
        if risk <= 0:
            return None
        # reconstrói TP a partir do RR do base (usa tp1 se parcial)
        tp = float(base.get("tp1") or base.get("tp") or 0)
        if tp == 0:
            rr = 1.5
            tp = entry + rr * risk if d == "COMPRA" else entry - rr * risk
        if abs(tp - entry) > cap:
            tp = entry + cap if d == "COMPRA" else entry - cap
        # _sig precisa rr; recalcula rr efetivo após clip
        new_risk_tp = abs(tp - entry)
        rr = new_risk_tp / risk if risk > 0 else 1.5
        return _sig(d, entry, sl, rr, rr, False)
    return _f


def _momo3_h(vol=1.3, hh0=0.0, hh1=24.0, n=3, tp_r=2.0):
    """n closes consecutivos mesma direção + vol + rompe extremo + H4."""
    def _f(df, i, sym):
        if i < n + 1:
            return None
        r = df.iloc[i]
        if not _hh_ok(r.hh, hh0, hh1):
            return None
        if float(r.Volume or 0) < vol * float(r.vol8 or 0):
            return None
        window = df.iloc[i - n:i]
        ups = all(float(x.Close) > float(x.Open) for _, x in window.iterrows())
        dns = all(float(x.Close) < float(x.Open) for _, x in window.iterrows())
        if not (ups or dns):
            return None
        hi = float(window.High.max())
        lo = float(window.Low.min())
        if ups and float(r.Close) > hi:
            d, sl = "COMPRA", lo
        elif dns and float(r.Close) < lo:
            d, sl = "VENDA", hi
        else:
            return None
        if not _h4_ok(r, d):
            return None
        return _sig(d, float(r.Close), sl, 1.0, tp_r, True)
    return _f


def _donch_h(n=20, vol=1.3, hh0=0.0, hh1=24.0, tp_r=2.0):
    """Rompimento Donchian n + vol + H4."""
    def _f(df, i, sym):
        if i < n + 1:
            return None
        r = df.iloc[i]
        if not _hh_ok(r.hh, hh0, hh1):
            return None
        if float(r.Volume or 0) < vol * float(r.vol8 or 0):
            return None
        win = df.iloc[i - n:i]
        hi, lo = float(win.High.max()), float(win.Low.min())
        p = df.iloc[i - 1]
        if float(r.Close) > hi and float(p.Close) <= hi:
            d, sl = "COMPRA", float(p.Low)
        elif float(r.Close) < lo and float(p.Close) >= lo:
            d, sl = "VENDA", float(p.High)
        else:
            return None
        if not _h4_ok(r, d):
            return None
        return _sig(d, float(r.Close), sl, 1.5, tp_r, True)
    return _f


def _bb_fade_h(vol=1.2, hh0=0.0, hh1=24.0, tp_r=1.5):
    """Fecha fora BB + rejeição (close volta) + H4 a favor do fade."""
    def _f(df, i, sym):
        if i < 5:
            return None
        r, p = df.iloc[i], df.iloc[i - 1]
        if not _hh_ok(r.hh, hh0, hh1):
            return None
        if float(r.Volume or 0) < vol * float(r.vol8 or 0):
            return None
        bu = getattr(r, "bb_up", np.nan)
        bd = getattr(r, "bb_dn", np.nan)
        if pd.isna(bu) or pd.isna(bd):
            return None
        # poke above then close back inside → venda; poke below → compra
        if float(p.High) > float(bu) and float(r.Close) < float(bu) and float(r.Close) < float(r.Open):
            d, sl = "VENDA", float(max(p.High, r.High))
        elif float(p.Low) < float(bd) and float(r.Close) > float(bd) and float(r.Close) > float(r.Open):
            d, sl = "COMPRA", float(min(p.Low, r.Low))
        else:
            return None
        if not _h4_ok(r, d):
            return None
        return _sig(d, float(r.Close), sl, 1.5, tp_r, True)
    return _f


def _rsi_recl_h(lo=30, hi=70, vol=1.2, hh0=0.0, hh1=24.0, tp_r=1.5):
    """RSI extrema na barra ant. + reclaim na atual + H4."""
    def _f(df, i, sym):
        if i < 3:
            return None
        r, p = df.iloc[i], df.iloc[i - 1]
        if not _hh_ok(r.hh, hh0, hh1):
            return None
        if float(r.Volume or 0) < vol * float(r.vol8 or 0):
            return None
        rsi_p = getattr(p, "rsi", np.nan)
        rsi_r = getattr(r, "rsi", np.nan)
        if pd.isna(rsi_p) or pd.isna(rsi_r):
            return None
        if float(rsi_p) < lo and float(rsi_r) >= lo and float(r.Close) > float(r.Open):
            d, sl = "COMPRA", float(min(p.Low, r.Low))
        elif float(rsi_p) > hi and float(rsi_r) <= hi and float(r.Close) < float(r.Open):
            d, sl = "VENDA", float(max(p.High, r.High))
        else:
            return None
        if not _h4_ok(r, d):
            return None
        return _sig(d, float(r.Close), sl, 1.5, tp_r, True)
    return _f


def _false_brk_h(look=10, vol=1.3, hh0=0.0, hh1=24.0, tp_r=1.5):
    """False break: rompe high/low lookback e fecha de volta dentro + H4 fade."""
    def _f(df, i, sym):
        if i < look + 1:
            return None
        r = df.iloc[i]
        if not _hh_ok(r.hh, hh0, hh1):
            return None
        if float(r.Volume or 0) < vol * float(r.vol8 or 0):
            return None
        win = df.iloc[i - look:i]
        hi, lo = float(win.High.max()), float(win.Low.min())
        # wick above hi but close back below → venda
        if float(r.High) > hi and float(r.Close) < hi and float(r.Close) < float(r.Open):
            d, sl = "VENDA", float(r.High)
        elif float(r.Low) < lo and float(r.Close) > lo and float(r.Close) > float(r.Open):
            d, sl = "COMPRA", float(r.Low)
        else:
            return None
        if not _h4_ok(r, d):
            return None
        return _sig(d, float(r.Close), sl, 1.5, tp_r, True)
    return _f


def _pin_h(wick=0.6, vol=1.3, hh0=0.0, hh1=24.0, tp_r=1.5):
    """Pin bar: wick ≥ wick×range, corpo pequeno, a favor H4."""
    def _f(df, i, sym):
        if i < 2:
            return None
        r = df.iloc[i]
        if not _hh_ok(r.hh, hh0, hh1):
            return None
        if float(r.Volume or 0) < vol * float(r.vol8 or 0):
            return None
        rng = float(r.High - r.Low)
        if rng <= 0:
            return None
        body = abs(float(r.Close) - float(r.Open))
        up_w = float(r.High) - max(float(r.Close), float(r.Open))
        dn_w = min(float(r.Close), float(r.Open)) - float(r.Low)
        if dn_w >= wick * rng and body <= 0.35 * rng:
            d, sl = "COMPRA", float(r.Low)
        elif up_w >= wick * rng and body <= 0.35 * rng:
            d, sl = "VENDA", float(r.High)
        else:
            return None
        if not _h4_ok(r, d):
            return None
        return _sig(d, float(r.Close), sl, 1.5, tp_r, True)
    return _f


# ═══════════════════════════════════════════════════════════
# BTC — famílias novas
# ═══════════════════════════════════════════════════════════
for _tag, _fn in [
    ("BTC_MOMO3_24H", _momo3_h(1.3, 0.0, 24.0, 3, 2.0)),
    ("BTC_MOMO3_24H_V14", _momo3_h(1.4, 0.0, 24.0, 3, 2.0)),
    ("BTC_MOMO3_24H_MT", _mt(_momo3_h(1.3, 0.0, 24.0, 3, 2.0))),
    ("BTC_MOMO3_ASIA", _momo3_h(1.3, 0.0, 8.0, 3, 2.0)),
    ("BTC_MOMO3_NIGHT", _momo3_h(1.3, 20.0, 10.0, 3, 2.0)),
    ("BTC_MOMO3_NY", _momo3_h(1.3, 13.0, 17.0, 3, 2.0)),
    ("BTC_MOMO3_LON", _momo3_h(1.3, 8.0, 12.0, 3, 2.0)),
    ("BTC_MOMO3_WE", _we(_momo3_h(1.3, 0.0, 24.0, 3, 2.0))),
    ("BTC_MOMO4_24H", _momo3_h(1.3, 0.0, 24.0, 4, 2.0)),
    ("BTC_MOMO3_TP25", _momo3_h(1.3, 0.0, 24.0, 3, 2.5)),
    ("BTC_MOMO3_CAP", _live_cap(_momo3_h(1.3, 0.0, 24.0, 3, 2.0), "BTCUSD")),
]:
    _reg(_tag, _fn)

for _tag, _fn in [
    ("BTC_DON20_24H", _donch_h(20, 1.3, 0.0, 24.0, 2.0)),
    ("BTC_DON20_24H_MT", _mt(_donch_h(20, 1.3, 0.0, 24.0, 2.0))),
    ("BTC_DON20_ASIA", _donch_h(20, 1.3, 0.0, 8.0, 2.0)),
    ("BTC_DON20_NIGHT", _donch_h(20, 1.3, 20.0, 10.0, 2.0)),
    ("BTC_DON20_NY", _donch_h(20, 1.3, 13.0, 17.0, 2.0)),
    ("BTC_DON20_LON", _donch_h(20, 1.3, 8.0, 12.0, 2.0)),
    ("BTC_DON20_WE", _we(_donch_h(20, 1.3, 0.0, 24.0, 2.0))),
    ("BTC_DON15_24H", _donch_h(15, 1.3, 0.0, 24.0, 2.0)),
    ("BTC_DON20_V14", _donch_h(20, 1.4, 0.0, 24.0, 2.0)),
    ("BTC_DON20_ATR", _atr_regime(_donch_h(20, 1.3, 0.0, 24.0, 2.0), 0.8, 2.0)),
    ("BTC_DON20_CAP", _live_cap(_donch_h(20, 1.3, 0.0, 24.0, 2.0), "BTCUSD")),
]:
    _reg(_tag, _fn)

for _tag, _fn in [
    ("BTC_BBFADE_24H", _bb_fade_h(1.2, 0.0, 24.0, 1.5)),
    ("BTC_BBFADE_24H_MT", _mt(_bb_fade_h(1.3, 0.0, 24.0, 1.5))),
    ("BTC_BBFADE_ASIA", _bb_fade_h(1.2, 0.0, 8.0, 1.5)),
    ("BTC_BBFADE_NIGHT", _bb_fade_h(1.2, 20.0, 10.0, 1.5)),
    ("BTC_BBFADE_NY", _bb_fade_h(1.2, 13.0, 17.0, 1.5)),
    ("BTC_BBFADE_LON", _bb_fade_h(1.2, 8.0, 12.0, 1.5)),
    ("BTC_BBFADE_WE", _we(_bb_fade_h(1.3, 0.0, 24.0, 1.5))),
    ("BTC_BBFADE_TP20", _cap_tp(_bb_fade_h(1.3, 0.0, 24.0, 1.5), 2.0)),
]:
    _reg(_tag, _fn)

for _tag, _fn in [
    ("BTC_RSI_RECL_24H", _rsi_recl_h(30, 70, 1.2, 0.0, 24.0)),
    ("BTC_RSI_RECL_24H_MT", _mt(_rsi_recl_h(30, 70, 1.3, 0.0, 24.0))),
    ("BTC_RSI_RECL_ASIA", _rsi_recl_h(30, 70, 1.2, 0.0, 8.0)),
    ("BTC_RSI_RECL_NIGHT", _rsi_recl_h(30, 70, 1.2, 20.0, 10.0)),
    ("BTC_RSI_RECL_NY", _rsi_recl_h(30, 70, 1.2, 13.0, 17.0)),
    ("BTC_RSI_RECL_25_75", _rsi_recl_h(25, 75, 1.2, 0.0, 24.0)),
    ("BTC_RSI_RECL_WE", _we(_rsi_recl_h(30, 70, 1.3, 0.0, 24.0))),
]:
    _reg(_tag, _fn)

for _tag, _fn in [
    ("BTC_FALSE_24H", _false_brk_h(10, 1.3, 0.0, 24.0)),
    ("BTC_FALSE_24H_MT", _mt(_false_brk_h(10, 1.3, 0.0, 24.0))),
    ("BTC_FALSE_ASIA", _false_brk_h(10, 1.3, 0.0, 8.0)),
    ("BTC_FALSE_NIGHT", _false_brk_h(10, 1.3, 20.0, 10.0)),
    ("BTC_FALSE_NY", _false_brk_h(10, 1.3, 13.0, 17.0)),
    ("BTC_FALSE_15", _false_brk_h(15, 1.3, 0.0, 24.0)),
    ("BTC_FALSE_WE", _we(_false_brk_h(10, 1.3, 0.0, 24.0))),
]:
    _reg(_tag, _fn)

for _tag, _fn in [
    ("BTC_PIN_24H", _pin_h(0.6, 1.3, 0.0, 24.0)),
    ("BTC_PIN_24H_MT", _mt(_pin_h(0.6, 1.3, 0.0, 24.0))),
    ("BTC_PIN_ASIA", _pin_h(0.6, 1.3, 0.0, 8.0)),
    ("BTC_PIN_NIGHT", _pin_h(0.6, 1.3, 20.0, 10.0)),
    ("BTC_PIN_NY", _pin_h(0.6, 1.3, 13.0, 17.0)),
    ("BTC_PIN_V65", _pin_h(0.65, 1.3, 0.0, 24.0)),
    ("BTC_PIN_WE", _we(_pin_h(0.6, 1.3, 0.0, 24.0))),
]:
    _reg(_tag, _fn)

# M60 BTC
_reg("BTC_MOMO3_24H_M60", _momo3_h(1.3, 0.0, 24.0, 3, 2.0), tf=60)
_reg("BTC_DON20_24H_M60", _donch_h(20, 1.3, 0.0, 24.0, 2.0), tf=60)
_reg("BTC_NR5_24H_M60", _nr_h(5, 1.3, 0.0, 24.0), tf=60)
_reg("BTC_HL_24H_M60", _hl_h(1.3, 0.0, 24.0), tf=60)
_reg("BTC_INS_24H_M60", _inside_h(1.3, 0.0, 24.0), tf=60)
_reg("BTC_BBFADE_24H_M60", _bb_fade_h(1.2, 0.0, 24.0), tf=60)
_reg("BTC_RSI_RECL_M60", _rsi_recl_h(30, 70, 1.2, 0.0, 24.0), tf=60)
_reg("BTC_FALSE_M60", _false_brk_h(10, 1.3, 0.0, 24.0), tf=60)
_reg("BTC_PIN_M60", _pin_h(0.6, 1.3, 0.0, 24.0), tf=60)
_reg("BTC_MOMO3_ASIA_M60", _momo3_h(1.3, 0.0, 8.0, 3, 2.0), tf=60)

# ═══════════════════════════════════════════════════════════
# ETH — mesmas famílias (buracos restantes)
# ═══════════════════════════════════════════════════════════
for _tag, _fn in [
    ("ETH_MOMO3_24H", _momo3_h(1.3, 0.0, 24.0, 3, 2.0)),
    ("ETH_MOMO3_24H_MT", _mt(_momo3_h(1.3, 0.0, 24.0, 3, 2.0))),
    ("ETH_MOMO3_ASIA", _momo3_h(1.3, 0.0, 8.0, 3, 2.0)),
    ("ETH_MOMO3_NIGHT", _momo3_h(1.3, 20.0, 10.0, 3, 2.0)),
    ("ETH_MOMO3_NY", _momo3_h(1.3, 13.0, 17.0, 3, 2.0)),
    ("ETH_MOMO3_LON", _momo3_h(1.3, 8.0, 12.0, 3, 2.0)),
    ("ETH_MOMO3_WE", _we(_momo3_h(1.3, 0.0, 24.0, 3, 2.0))),
    ("ETH_MOMO3_V14", _momo3_h(1.4, 0.0, 24.0, 3, 2.0)),
    ("ETH_MOMO3_CAP", _live_cap(_momo3_h(1.3, 0.0, 24.0, 3, 2.0), "ETHUSD")),
]:
    _reg(_tag, _fn, "ETHUSD")

for _tag, _fn in [
    ("ETH_DON20_24H", _donch_h(20, 1.3, 0.0, 24.0, 2.0)),
    ("ETH_DON20_24H_MT", _mt(_donch_h(20, 1.3, 0.0, 24.0, 2.0))),
    ("ETH_DON20_ASIA", _donch_h(20, 1.3, 0.0, 8.0, 2.0)),
    ("ETH_DON20_NIGHT", _donch_h(20, 1.3, 20.0, 10.0, 2.0)),
    ("ETH_DON20_NY", _donch_h(20, 1.3, 13.0, 17.0, 2.0)),
    ("ETH_DON20_WE", _we(_donch_h(20, 1.3, 0.0, 24.0, 2.0))),
    ("ETH_DON15_24H", _donch_h(15, 1.3, 0.0, 24.0, 2.0)),
    ("ETH_DON20_CAP", _live_cap(_donch_h(20, 1.3, 0.0, 24.0, 2.0), "ETHUSD")),
]:
    _reg(_tag, _fn, "ETHUSD")

for _tag, _fn in [
    ("ETH_BBFADE_24H", _bb_fade_h(1.2, 0.0, 24.0, 1.5)),
    ("ETH_BBFADE_24H_MT", _mt(_bb_fade_h(1.3, 0.0, 24.0, 1.5))),
    ("ETH_BBFADE_ASIA", _bb_fade_h(1.2, 0.0, 8.0, 1.5)),
    ("ETH_BBFADE_NIGHT", _bb_fade_h(1.2, 20.0, 10.0, 1.5)),
    ("ETH_BBFADE_NY", _bb_fade_h(1.2, 13.0, 17.0, 1.5)),
    ("ETH_BBFADE_WE", _we(_bb_fade_h(1.3, 0.0, 24.0, 1.5))),
    ("ETH_BBFADE_TP20", _cap_tp(_bb_fade_h(1.3, 0.0, 24.0, 1.5), 2.0)),
]:
    _reg(_tag, _fn, "ETHUSD")

for _tag, _fn in [
    ("ETH_RSI_RECL_24H", _rsi_recl_h(30, 70, 1.2, 0.0, 24.0)),
    ("ETH_RSI_RECL_24H_MT", _mt(_rsi_recl_h(30, 70, 1.3, 0.0, 24.0))),
    ("ETH_RSI_RECL_ASIA", _rsi_recl_h(30, 70, 1.2, 0.0, 8.0)),
    ("ETH_RSI_RECL_NIGHT", _rsi_recl_h(30, 70, 1.2, 20.0, 10.0)),
    ("ETH_RSI_RECL_NY", _rsi_recl_h(30, 70, 1.2, 13.0, 17.0)),
    ("ETH_RSI_RECL_25_75", _rsi_recl_h(25, 75, 1.2, 0.0, 24.0)),
    ("ETH_RSI_RECL_WE", _we(_rsi_recl_h(30, 70, 1.3, 0.0, 24.0))),
]:
    _reg(_tag, _fn, "ETHUSD")

for _tag, _fn in [
    ("ETH_FALSE_24H", _false_brk_h(10, 1.3, 0.0, 24.0)),
    ("ETH_FALSE_24H_MT", _mt(_false_brk_h(10, 1.3, 0.0, 24.0))),
    ("ETH_FALSE_ASIA", _false_brk_h(10, 1.3, 0.0, 8.0)),
    ("ETH_FALSE_NIGHT", _false_brk_h(10, 1.3, 20.0, 10.0)),
    ("ETH_FALSE_NY", _false_brk_h(10, 1.3, 13.0, 17.0)),
    ("ETH_FALSE_WE", _we(_false_brk_h(10, 1.3, 0.0, 24.0))),
]:
    _reg(_tag, _fn, "ETHUSD")

for _tag, _fn in [
    ("ETH_PIN_24H", _pin_h(0.6, 1.3, 0.0, 24.0)),
    ("ETH_PIN_24H_MT", _mt(_pin_h(0.6, 1.3, 0.0, 24.0))),
    ("ETH_PIN_ASIA", _pin_h(0.6, 1.3, 0.0, 8.0)),
    ("ETH_PIN_NIGHT", _pin_h(0.6, 1.3, 20.0, 10.0)),
    ("ETH_PIN_NY", _pin_h(0.6, 1.3, 13.0, 17.0)),
    ("ETH_PIN_WE", _we(_pin_h(0.6, 1.3, 0.0, 24.0))),
]:
    _reg(_tag, _fn, "ETHUSD")

# M60 ETH
_reg("ETH_MOMO3_24H_M60", _momo3_h(1.3, 0.0, 24.0, 3, 2.0), "ETHUSD", tf=60)
_reg("ETH_DON20_24H_M60", _donch_h(20, 1.3, 0.0, 24.0, 2.0), "ETHUSD", tf=60)
_reg("ETH_NR5_24H_M60", _nr_h(5, 1.3, 0.0, 24.0), "ETHUSD", tf=60)
_reg("ETH_HL_24H_M60", _hl_h(1.3, 0.0, 24.0), "ETHUSD", tf=60)
_reg("ETH_INS_24H_M60", _inside_h(1.3, 0.0, 24.0), "ETHUSD", tf=60)
_reg("ETH_BBFADE_24H_M60", _bb_fade_h(1.2, 0.0, 24.0), "ETHUSD", tf=60)
_reg("ETH_RSI_RECL_M60", _rsi_recl_h(30, 70, 1.2, 0.0, 24.0), "ETHUSD", tf=60)
_reg("ETH_FALSE_M60", _false_brk_h(10, 1.3, 0.0, 24.0), "ETHUSD", tf=60)
_reg("ETH_PIN_M60", _pin_h(0.6, 1.3, 0.0, 24.0), "ETHUSD", tf=60)
_reg("ETH_FRI_MON_MOMO", _fri_mon(_momo3_h(1.3, 0.0, 24.0, 3, 2.0)), "ETHUSD")
_reg("ETH_FRI_SUN_DON", _fri_sun(_donch_h(20, 1.3, 0.0, 24.0, 2.0)), "ETHUSD")


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
        return row
    except Exception as exc:
        return {"setup": name, "sym": STRAT_SYM.get(name, "?"),
                "tf": STRAT_TF.get(name, 15), "n": 0,
                "veredito": f"ERRO: {exc}", "err": traceback.format_exc()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bars", type=int, default=50000)
    ap.add_argument("--syms", default="BTCUSD,ETHUSD")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--only", default="")
    args = ap.parse_args()

    want = {s.strip().upper() for s in args.syms.split(",") if s.strip()}
    os.environ["KIMI_GROUP"] = "crypto"
    os.makedirs("logs", exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    log_path = f"logs/backtest_setups_novos_v20_btc_eth_{stamp}.txt"
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
    out(f"{'#' * 74}\n# SETUPS NOVOS V20 — BTC/ETH FAMÍLIAS NOVAS "
        f"({len(strat_names)} setups) · {datetime.now():%d/%m %H:%M}\n"
        f"# Ativos: {sorted(want)} · workers={args.workers}\n"
        f"# SKIP: GOs v13–v18 já wired · ORDER_FLOW BTC\n"
        f"# Famílias: momo3 · donch · bbfade · rsi · false · pin · M60\n"
        f"# NÃO altera live até GO · régua rigorosa\n{'#' * 74}")

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
        if raw is None or len(raw) < 1500:
            out(f"\n!! {key}: sem histórico")
            continue
        df = add_extra_v2(add_indicators(raw), sym, ctx=None)
        dfs[key] = df
        bars_day = {15: 96, 30: 48, 60: 24}.get(tf, 96)
        anos = len(df) / bars_day / 365
        out(f"\n{'=' * 74}\n  {key}: {len(df)} candles (~{anos:.1f}a)  "
            f"[{df.index[0].date()} -> {df.index[-1].date()}] "
            f"({time.time() - t0:.0f}s)\n{'=' * 74}")

    ranking = []
    t_all = time.time()
    with ProcessPoolExecutor(max_workers=args.workers,
                             initializer=_worker_init,
                             initargs=(dfs,)) as ex:
        futs = {ex.submit(_run_one, n): n for n in strat_names}
        done = 0
        for fut in as_completed(futs):
            done += 1
            row = fut.result()
            ranking.append(row)
            name = row.get("setup", "?")
            v = row.get("veredito", "?")
            n = int(row.get("n") or 0)
            net = row.get("net")
            net_s = f"{net:+.3f}" if isinstance(net, (int, float)) else "—"
            out(f"  [{done}/{len(strat_names)}] {name:<28} n={n:<5} "
                f"net {net_s}  {v}  ({row.get('secs', '?')}s)")
            if "_trades" in row:
                try:
                    row["_trades"].to_csv(
                        f"logs/novos_v20_{row['sym']}_M{row['tf']}_{name}.csv",
                        index=False)
                except Exception:
                    pass
                del row["_trades"]

    out(f"\n{'#' * 74}\n  RANKING V20 BTC/ETH\n{'#' * 74}")
    rk = pd.DataFrame([{k: v for k, v in r.items() if k != "_trades"}
                       for r in ranking])
    goes, yellows = [], []
    if not rk.empty:
        rk = rk.sort_values("net", ascending=False, na_position="last")
        for _, r in rk.iterrows():
            if r.get("n", 0) == 0:
                out(f"  {r.setup:<28} sem trades / {r.veredito}")
                continue
            out(f"  {r.setup:<28} n={int(r.n):<5} net {r.net:+.3f} R  "
                f"PF {r.PF:<5} cons {r['cons%']}%  OOS {r.oos_net}  {r.veredito}")
            vs = str(r.veredito)
            if "🟢" in vs and "GO" in vs:
                goes.append(r.setup)
            elif "🟡" in vs:
                yellows.append(r.setup)
        rk.to_csv(f"logs/backtest_setups_novos_v20_btc_eth_ranking_{stamp}.csv",
                  index=False)

    out(f"\n# GO encontrados: {goes if goes else 'nenhum'}")
    out(f"# amarelos: {yellows if yellows else 'nenhum'}")
    out(f"# tempo total: {time.time() - t_all:.0f}s")
    out(f"# FIM — {log_path}")
    logf.close()
    print(f"\n-> {log_path}")


if __name__ == "__main__":
    main()
