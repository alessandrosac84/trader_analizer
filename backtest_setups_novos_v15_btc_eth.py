"""
backtest_setups_novos_v15_btc_eth.py — bateria GRANDE BTC/ETH pós-v14.

Foco (sem retestar 🟢 já wired):
  · refine 🟡 v14 (HL/IMP/OUT/EMA/NR/WE/ETH PDH·NR)
  · Londres / NY / Lon+NY
  · vol filters · RR/cap TP · MT · fri–mon
  · engolfo · outside · VWAP fade · squeeze · multi-TF M30

Régua GO: net ≥ +0,10 R · PF ≥ 1,25 · cons ≥ 55% · OOS segura · n ≥ 40.
NÃO altera live até 🟢.

Uso:
  python backtest_setups_novos_v15_btc_eth.py
  python backtest_setups_novos_v15_btc_eth.py --workers 6 --bars 50000
  python backtest_setups_novos_v15_btc_eth.py --syms BTCUSD --workers 8
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
    _hh_ok, _inside_h, _nr_h, _impulse_h, _h4_pb_ema_h, _volspike_h,
    _outside_h, _hl_h, _dbl_inside_h, _load_ohlc,
)
from backtest_setups_novos_v14_btc_eth import (
    _pdh_h, _engulf_h, _sqz_break_h, _ema_reclaim_h, _we, _fri_sun,
)

STRATS = {}
STRAT_SYM = {}
ONE_PER_DAY = set()
STRAT_TF = {}  # default 15

# Shared by workers (set in initializer)
_WORKER_DFS = {}


def _reg(name, fn, sym="BTCUSD", one_per_day=False, tf=15):
    STRATS[name] = fn
    STRAT_SYM[name] = sym
    STRAT_TF[name] = tf
    if one_per_day:
        ONE_PER_DAY.add(name)


def _fri_mon(fn):
    """Sex 18h → Seg 08h (FDS estendido + abertura segunda)."""
    def _f(df, i, sym):
        r = df.iloc[i]
        d = int(r.dow)
        h = float(r.hh)
        if d == 4 and h < 18.0:
            return None
        if d == 0 and h >= 8.0:
            return None
        if d not in (4, 5, 6, 0):
            return None
        return fn(df, i, sym)
    return _f


def _rr(fn, rr1=1.5, rr2=2.0, partial=True):
    """Sobrescreve RR do sinal base."""
    def _f(df, i, sym):
        base = fn(df, i, sym)
        if not base:
            return None
        return _sig(base["dir"], float(df.iloc[i].Close), float(base["sl"]),
                    rr1, rr2, partial)
    return _f


def _cap_tp(fn, tp_r=2.0):
    """TP único (sem parcial) em tp_r."""
    def _f(df, i, sym):
        base = fn(df, i, sym)
        if not base:
            return None
        return _sig(base["dir"], float(df.iloc[i].Close), float(base["sl"]),
                    tp_r, tp_r, False)
    return _f


def _atr_regime(fn, lo=0.7, hi=2.5):
    """Filtra por atr_ratio (ATR vs média longa)."""
    def _f(df, i, sym):
        r = df.iloc[i]
        ar = getattr(r, "atr_ratio", np.nan)
        if pd.isna(ar) or not (lo <= float(ar) <= hi):
            return None
        return fn(df, i, sym)
    return _f


def _vwap_fade_h(vol=1.2, dist=1.2, hh0=0.0, hh1=24.0, rr1=1.5, rr2=2.0):
    """Fade VWAP: preço esticado (≥dist ATR) + rejeição + H4 contrário ao stretch."""
    def _f(df, i, sym):
        if i < 5:
            return None
        r, p = df.iloc[i], df.iloc[i - 1]
        if not _hh_ok(r.hh, hh0, hh1):
            return None
        if float(r.Volume or 0) < vol * float(r.vol8 or 0):
            return None
        vd = getattr(r, "vwap_dist", np.nan)
        if pd.isna(vd) or pd.isna(r.atr) or float(r.atr) <= 0:
            return None
        vd = float(vd)
        # stretch up → fade venda; stretch down → fade compra
        if vd >= dist and float(r.Close) < float(r.Open) and float(r.Close) < float(p.Close):
            d, sl = "VENDA", float(max(p.High, r.High))
        elif vd <= -dist and float(r.Close) > float(r.Open) and float(r.Close) > float(p.Close):
            d, sl = "COMPRA", float(min(p.Low, r.Low))
        else:
            return None
        if not _h4_ok(r, d):
            return None
        return _sig(d, float(r.Close), sl, rr1, rr2, True)
    return _f


def _vwap_cont_h(vol=1.2, dist=0.3, hh0=0.0, hh1=24.0):
    """Continuaçao VWAP: preço do lado certo do VWAP + rompe + H4."""
    def _f(df, i, sym):
        if i < 5:
            return None
        r, p = df.iloc[i], df.iloc[i - 1]
        if not _hh_ok(r.hh, hh0, hh1):
            return None
        if float(r.Volume or 0) < vol * float(r.vol8 or 0):
            return None
        vd = getattr(r, "vwap_dist", np.nan)
        if pd.isna(vd):
            return None
        vd = float(vd)
        if vd > dist and float(r.Close) > float(p.High):
            d, sl = "COMPRA", float(p.Low)
        elif vd < -dist and float(r.Close) < float(p.Low):
            d, sl = "VENDA", float(p.High)
        else:
            return None
        if not _h4_ok(r, d):
            return None
        return _sig(d, float(r.Close), sl, 1.5, 2.0, True)
    return _f


def _hl_rr(vol=1.3, hh0=0.0, hh1=24.0, rr1=1.5, rr2=2.0, partial=True):
    """HL com RR configurável (base _hl_h usa 1.5/2.0)."""
    base_fn = _hl_h(vol, hh0, hh1)
    return _rr(base_fn, rr1, rr2, partial)


# ═══════════════════════════════════════════════════════════
# BTC — refine 🟡 HL / IMP / OUT / EMA / NR noite / WE
# ═══════════════════════════════════════════════════════════
# HL — OOS era o gargalo (MT net ok, OOS fraco)
_reg("BTC_HL_24H_MT_V14", _mt(_hl_h(1.4, 0.0, 24.0)))
_reg("BTC_HL_24H_MT_V15", _mt(_hl_h(1.5, 0.0, 24.0)))
_reg("BTC_HL_24H_MT_V16", _mt(_hl_h(1.6, 0.0, 24.0)))
_reg("BTC_HL_24H_MT_RR20", _mt(_hl_rr(1.4, 0.0, 24.0, 1.5, 2.0)))
_reg("BTC_HL_24H_MT_TP20", _mt(_cap_tp(_hl_h(1.4, 0.0, 24.0), 2.0)))
_reg("BTC_HL_24H_MT_TP25", _mt(_cap_tp(_hl_h(1.4, 0.0, 24.0), 2.5)))
_reg("BTC_HL_24H_MT_TP15", _mt(_cap_tp(_hl_h(1.5, 0.0, 24.0), 1.5)))
_reg("BTC_HL_NIGHT_MT", _mt(_hl_h(1.4, 20.0, 10.0)))
_reg("BTC_HL_NIGHT_V15", _hl_h(1.5, 20.0, 10.0))
_reg("BTC_HL_NIGHT_V16", _hl_h(1.6, 20.0, 10.0))
_reg("BTC_HL_ASIA_MT", _mt(_hl_h(1.4, 0.0, 8.0)))
_reg("BTC_HL_ASIA_V15", _hl_h(1.5, 0.0, 8.0))
_reg("BTC_HL_OVN_MT", _mt(_hl_h(1.4, 18.0, 8.0)))
_reg("BTC_HL_LON_0812", _hl_h(1.4, 8.0, 12.0))
_reg("BTC_HL_LON_0812_MT", _mt(_hl_h(1.4, 8.0, 12.0)))
_reg("BTC_HL_LON_0713", _hl_h(1.4, 7.0, 13.0))
_reg("BTC_HL_NY_1317", _hl_h(1.4, 13.0, 17.0))
_reg("BTC_HL_NY_1317_MT", _mt(_hl_h(1.4, 13.0, 17.0)))
_reg("BTC_HL_NY_1418", _hl_h(1.4, 14.0, 18.0))
_reg("BTC_HL_LN_0817", _hl_h(1.4, 8.0, 17.0))
_reg("BTC_HL_LN_0817_MT", _mt(_hl_h(1.4, 8.0, 17.0)))
_reg("BTC_HL_1622_MT", _mt(_hl_h(1.4, 16.0, 22.0)))
_reg("BTC_HL_24H_ATR", _atr_regime(_hl_h(1.4, 0.0, 24.0), 0.8, 2.0))
_reg("BTC_HL_24H_MT_ATR", _mt(_atr_regime(_hl_h(1.4, 0.0, 24.0), 0.8, 2.0)))

# IMP — Asia/Night fortes mas n/OOS; 24h V18 OOS negativo
_reg("BTC_IMP_NIGHT_MT", _mt(_impulse_h(2.0, 1.5, 20.0, 10.0, 2.0)))
_reg("BTC_IMP_NIGHT_V18", _impulse_h(1.8, 1.6, 20.0, 10.0, 2.0))
_reg("BTC_IMP_NIGHT_TP25", _impulse_h(2.0, 1.5, 20.0, 10.0, 2.5))
_reg("BTC_IMP_NIGHT_TP15", _impulse_h(2.0, 1.5, 20.0, 10.0, 1.5))
_reg("BTC_IMP_NIGHT_V22", _impulse_h(2.2, 1.6, 20.0, 10.0, 2.0))
_reg("BTC_IMP_ASIA_V18", _impulse_h(1.8, 1.6, 0.0, 8.0, 2.0))
_reg("BTC_IMP_ASIA_TP25", _impulse_h(1.8, 1.4, 0.0, 8.0, 2.5))
_reg("BTC_IMP_ASIA_MT_V18", _mt(_impulse_h(1.8, 1.6, 0.0, 8.0, 2.0)))
_reg("BTC_IMP_OVN_V18", _impulse_h(1.8, 1.6, 18.0, 8.0, 2.0))
_reg("BTC_IMP_OVN_MT_V18", _mt(_impulse_h(1.8, 1.6, 18.0, 8.0, 2.0)))
_reg("BTC_IMP_CONT_24H_MT_V18", _mt(_impulse_h(1.8, 1.6, 0.0, 24.0, 2.0)))
_reg("BTC_IMP_CONT_24H_MT_TP25", _mt(_impulse_h(2.0, 1.5, 0.0, 24.0, 2.5)))
_reg("BTC_IMP_CONT_24H_V16", _impulse_h(1.6, 1.7, 0.0, 24.0, 2.0))
_reg("BTC_IMP_CONT_24H_V20", _impulse_h(2.0, 1.7, 0.0, 24.0, 2.0))
_reg("BTC_IMP_CONT_24H_TP30", _impulse_h(2.0, 1.5, 0.0, 24.0, 3.0))
_reg("BTC_IMP_LON_0812", _impulse_h(2.0, 1.5, 8.0, 12.0, 2.0))
_reg("BTC_IMP_LON_0812_MT", _mt(_impulse_h(2.0, 1.5, 8.0, 12.0, 2.0)))
_reg("BTC_IMP_NY_1317", _impulse_h(2.0, 1.5, 13.0, 17.0, 2.0))
_reg("BTC_IMP_NY_1317_MT", _mt(_impulse_h(2.0, 1.5, 13.0, 17.0, 2.0)))
_reg("BTC_IMP_LN_0817", _impulse_h(2.0, 1.5, 8.0, 17.0, 2.0))
_reg("BTC_IMP_1622_MT", _mt(_impulse_h(2.0, 1.5, 16.0, 22.0, 2.0)))
_reg("BTC_IMP_1622_TP25", _impulse_h(2.0, 1.5, 16.0, 22.0, 2.5))
_reg("BTC_IMP_24H_ATR", _atr_regime(_impulse_h(2.0, 1.5, 0.0, 24.0, 2.0), 0.9, 2.2))

# OUT / ENGULF / SQZ / EMA reclaim
_reg("BTC_OUT_OVN_MT", _mt(_outside_h(1.4, 18.0, 8.0)))
_reg("BTC_OUT_OVN_V14", _outside_h(1.4, 18.0, 8.0))
_reg("BTC_OUT_NIGHT_MT", _mt(_outside_h(1.4, 20.0, 10.0)))
_reg("BTC_OUT_ASIA_MT", _mt(_outside_h(1.4, 0.0, 8.0)))
_reg("BTC_OUT_24H_MT", _mt(_outside_h(1.4, 0.0, 24.0)))
_reg("BTC_OUT_LON_0812", _outside_h(1.4, 8.0, 12.0))
_reg("BTC_OUT_NY_1317", _outside_h(1.4, 13.0, 17.0))
_reg("BTC_OUT_24H_TP20", _cap_tp(_outside_h(1.4, 0.0, 24.0), 2.0))
_reg("BTC_ENGULF_24H_MT", _mt(_engulf_h(1.4, 0.0, 24.0)))
_reg("BTC_ENGULF_24H_V14", _engulf_h(1.4, 0.0, 24.0))
_reg("BTC_ENGULF_24H_V15", _engulf_h(1.5, 0.0, 24.0))
_reg("BTC_ENGULF_ASIA_MT", _mt(_engulf_h(1.4, 0.0, 8.0)))
_reg("BTC_ENGULF_NIGHT", _engulf_h(1.4, 20.0, 10.0))
_reg("BTC_ENGULF_LON_0812", _engulf_h(1.4, 8.0, 12.0))
_reg("BTC_ENGULF_NY_1317", _engulf_h(1.4, 13.0, 17.0))
_reg("BTC_ENGULF_24H_TP20", _cap_tp(_engulf_h(1.4, 0.0, 24.0), 2.0))
_reg("BTC_SQZ_24H_MT", _mt(_sqz_break_h(1.5, 0.0, 24.0)))
_reg("BTC_SQZ_24H_V15", _sqz_break_h(1.5, 0.0, 24.0))
_reg("BTC_SQZ_ASIA_MT", _mt(_sqz_break_h(1.5, 0.0, 8.0)))
_reg("BTC_SQZ_NIGHT", _sqz_break_h(1.5, 20.0, 10.0))
_reg("BTC_SQZ_LON_0812", _sqz_break_h(1.5, 8.0, 12.0))
_reg("BTC_EMA_RECL_24H_MT", _mt(_ema_reclaim_h(0.0, 24.0, 1.3)))
_reg("BTC_EMA_RECL_24H_V13", _ema_reclaim_h(0.0, 24.0, 1.3))
_reg("BTC_EMA_RECL_24H_V14", _ema_reclaim_h(0.0, 24.0, 1.4))
_reg("BTC_EMA_RECL_24H_V15", _ema_reclaim_h(0.0, 24.0, 1.5))
_reg("BTC_EMA_RECL_OVN_MT", _mt(_ema_reclaim_h(18.0, 8.0, 1.3)))
_reg("BTC_EMA_RECL_NIGHT", _ema_reclaim_h(20.0, 10.0, 1.3))
_reg("BTC_EMA_RECL_LON", _ema_reclaim_h(8.0, 12.0, 1.3))
_reg("BTC_EMA_RECL_NY", _ema_reclaim_h(13.0, 17.0, 1.3))
_reg("BTC_EMA_RECL_24H_TP20", _cap_tp(_ema_reclaim_h(0.0, 24.0, 1.4), 2.0))

# NR refine (noite/WE/Lon/NY — 24h V14 já GO)
_reg("BTC_NR5_NIGHT_MT", _mt(_nr_h(5, 1.4, 20.0, 10.0)))
_reg("BTC_NR5_NIGHT_V14", _nr_h(5, 1.4, 20.0, 10.0))
_reg("BTC_NR5_NIGHT_V15", _nr_h(5, 1.5, 20.0, 10.0))
_reg("BTC_NR4_NIGHT_2010", _nr_h(4, 1.4, 20.0, 10.0))
_reg("BTC_NR4_NIGHT_MT", _mt(_nr_h(4, 1.4, 20.0, 10.0)))
_reg("BTC_NR5_OVN_MT", _mt(_nr_h(5, 1.4, 18.0, 8.0)))
_reg("BTC_NR5_ASIA_MT", _mt(_nr_h(5, 1.4, 0.0, 8.0)))
_reg("BTC_NR5_LON_0812", _nr_h(5, 1.4, 8.0, 12.0))
_reg("BTC_NR5_LON_0812_MT", _mt(_nr_h(5, 1.4, 8.0, 12.0)))
_reg("BTC_NR4_LON_0812", _nr_h(4, 1.4, 8.0, 12.0))
_reg("BTC_NR5_NY_1317", _nr_h(5, 1.4, 13.0, 17.0))
_reg("BTC_NR5_NY_1317_MT", _mt(_nr_h(5, 1.4, 13.0, 17.0)))
_reg("BTC_NR4_NY_1317", _nr_h(4, 1.4, 13.0, 17.0))
_reg("BTC_NR5_LN_0817", _nr_h(5, 1.4, 8.0, 17.0))
_reg("BTC_NR7_24H_V14", _nr_h(7, 1.4, 0.0, 24.0))
_reg("BTC_NR7_24H_MT", _mt(_nr_h(7, 1.4, 0.0, 24.0)))
_reg("BTC_NR5_1622_MT", _mt(_nr_h(5, 1.3, 16.0, 22.0)))
_reg("BTC_NR5_1622_V14", _nr_h(5, 1.4, 16.0, 22.0))
_reg("BTC_NR4_1622", _nr_h(4, 1.4, 16.0, 22.0))

# Inside evening / Lon / NY (24h já GO)
_reg("BTC_INS_1622_MT", _mt(_inside_h(1.3, 16.0, 22.0)))
_reg("BTC_INS_1622_V14", _inside_h(1.4, 16.0, 22.0))
_reg("BTC_INS_1822_MT", _mt(_inside_h(1.3, 18.0, 22.0)))
_reg("BTC_INS_LON_0812", _inside_h(1.3, 8.0, 12.0))
_reg("BTC_INS_LON_0812_MT", _mt(_inside_h(1.3, 8.0, 12.0)))
_reg("BTC_INS_NY_1317", _inside_h(1.3, 13.0, 17.0))
_reg("BTC_INS_NY_1317_MT", _mt(_inside_h(1.3, 13.0, 17.0)))
_reg("BTC_INS_NIGHT_MT", _mt(_inside_h(1.4, 20.0, 10.0)))
_reg("BTC_INS_ASIA_MT", _mt(_inside_h(1.4, 0.0, 8.0)))
_reg("BTC_DBL_INS_24H_MT", _mt(_dbl_inside_h(1.3, 0.0, 24.0)))
_reg("BTC_DBL_INS_NIGHT", _dbl_inside_h(1.3, 20.0, 10.0))
_reg("BTC_DBL_INS_LON", _dbl_inside_h(1.3, 8.0, 12.0))

# PDH Lon/NY (24h/Asia/Night já GO — não retestar idênticos)
_reg("BTC_PDH_LON_0812", _pdh_h(1.2, 8.0, 12.0))
_reg("BTC_PDH_LON_0812_MT", _mt(_pdh_h(1.2, 8.0, 12.0)))
_reg("BTC_PDH_LON_0713", _pdh_h(1.3, 7.0, 13.0))
_reg("BTC_PDH_NY_1317", _pdh_h(1.2, 13.0, 17.0))
_reg("BTC_PDH_NY_1317_MT", _mt(_pdh_h(1.2, 13.0, 17.0)))
_reg("BTC_PDH_NY_1418", _pdh_h(1.2, 14.0, 18.0))
_reg("BTC_PDH_LN_0817", _pdh_h(1.2, 8.0, 17.0))
_reg("BTC_PDH_OVN_MT", _mt(_pdh_h(1.3, 18.0, 8.0)))
_reg("BTC_PDH_OVN_V14", _pdh_h(1.4, 18.0, 8.0))
_reg("BTC_PDH_1622", _pdh_h(1.2, 16.0, 22.0))
_reg("BTC_PDH_1622_MT", _mt(_pdh_h(1.2, 16.0, 22.0)))

# H4 PB EMA sessões (MT24 já GO)
_reg("BTC_H4_PB_EMA_ASIA_MT", _mt(_h4_pb_ema_h(0.0, 8.0)))
_reg("BTC_H4_PB_EMA_OVN_MT", _mt(_h4_pb_ema_h(18.0, 8.0)))
_reg("BTC_H4_PB_EMA_NIGHT_MT", _mt(_h4_pb_ema_h(20.0, 10.0)))
_reg("BTC_H4_PB_EMA_LON", _h4_pb_ema_h(8.0, 12.0))
_reg("BTC_H4_PB_EMA_NY", _h4_pb_ema_h(13.0, 17.0))
_reg("BTC_H4_PB_EMA_1622", _h4_pb_ema_h(16.0, 22.0))

# Weekend / fri-mon
_reg("BTC_WE_NR4_V14", _we(_nr_h(4, 1.4, 0.0, 24.0)))
_reg("BTC_WE_NR4_V15", _we(_nr_h(4, 1.5, 0.0, 24.0)))
_reg("BTC_WE_NR4_TP20", _we(_cap_tp(_nr_h(4, 1.4, 0.0, 24.0), 2.0)))
_reg("BTC_WE_NR5_V14", _we(_nr_h(5, 1.4, 0.0, 24.0)))
_reg("BTC_WE_PDH_V13", _we(_pdh_h(1.3, 0.0, 24.0)))
_reg("BTC_WE_HL_V14", _we(_hl_h(1.4, 0.0, 24.0)))
_reg("BTC_WE_IMP_V18", _we(_impulse_h(1.8, 1.6, 0.0, 24.0, 2.0)))
_reg("BTC_WE_OUT", _we(_outside_h(1.4, 0.0, 24.0)))
_reg("BTC_WE_ENGULF", _we(_engulf_h(1.4, 0.0, 24.0)))
_reg("BTC_FRI_SUN_NR4", _fri_sun(_nr_h(4, 1.4, 0.0, 24.0)))
_reg("BTC_FRI_SUN_NR5", _fri_sun(_nr_h(5, 1.4, 0.0, 24.0)))
_reg("BTC_FRI_SUN_PDH", _fri_sun(_pdh_h(1.2, 0.0, 24.0)))
_reg("BTC_FRI_SUN_HL", _fri_sun(_hl_h(1.4, 0.0, 24.0)))
_reg("BTC_FRI_SUN_OUT", _fri_sun(_outside_h(1.4, 0.0, 24.0)))
_reg("BTC_FRI_MON_INS", _fri_mon(_inside_h(1.3, 0.0, 24.0)))
_reg("BTC_FRI_MON_NR4", _fri_mon(_nr_h(4, 1.4, 0.0, 24.0)))
_reg("BTC_FRI_MON_NR5", _fri_mon(_nr_h(5, 1.4, 0.0, 24.0)))
_reg("BTC_FRI_MON_PDH", _fri_mon(_pdh_h(1.2, 0.0, 24.0)))
_reg("BTC_FRI_MON_HL", _fri_mon(_hl_h(1.4, 0.0, 24.0)))
_reg("BTC_FRI_MON_IMP", _fri_mon(_impulse_h(2.0, 1.5, 0.0, 24.0, 2.0)))
_reg("BTC_FRI_MON_OUT", _fri_mon(_outside_h(1.4, 0.0, 24.0)))

# VWAP fade / cont (família nova)
_reg("BTC_VWAP_FADE_24H", _vwap_fade_h(1.2, 1.2, 0.0, 24.0))
_reg("BTC_VWAP_FADE_24H_V13", _vwap_fade_h(1.3, 1.3, 0.0, 24.0))
_reg("BTC_VWAP_FADE_24H_MT", _mt(_vwap_fade_h(1.3, 1.2, 0.0, 24.0)))
_reg("BTC_VWAP_FADE_ASIA", _vwap_fade_h(1.2, 1.2, 0.0, 8.0))
_reg("BTC_VWAP_FADE_NIGHT", _vwap_fade_h(1.2, 1.2, 20.0, 10.0))
_reg("BTC_VWAP_FADE_LON", _vwap_fade_h(1.2, 1.2, 8.0, 12.0))
_reg("BTC_VWAP_FADE_NY", _vwap_fade_h(1.2, 1.2, 13.0, 17.0))
_reg("BTC_VWAP_FADE_DIST15", _vwap_fade_h(1.2, 1.5, 0.0, 24.0))
_reg("BTC_VWAP_FADE_TP20", _cap_tp(_vwap_fade_h(1.3, 1.2, 0.0, 24.0), 2.0))
_reg("BTC_VWAP_CONT_24H", _vwap_cont_h(1.3, 0.3, 0.0, 24.0))
_reg("BTC_VWAP_CONT_24H_MT", _mt(_vwap_cont_h(1.3, 0.3, 0.0, 24.0)))
_reg("BTC_VWAP_CONT_LON", _vwap_cont_h(1.3, 0.3, 8.0, 12.0))
_reg("BTC_VWAP_CONT_NY", _vwap_cont_h(1.3, 0.3, 13.0, 17.0))
_reg("BTC_VWAP_CONT_NIGHT", _vwap_cont_h(1.3, 0.3, 20.0, 10.0))

# Volspike refine
_reg("BTC_VOLSPIKE_24H_MT", _mt(_volspike_h(2.0, 1.5, 0.0, 24.0)))
_reg("BTC_VOLSPIKE_NIGHT", _volspike_h(2.0, 1.5, 20.0, 10.0))
_reg("BTC_VOLSPIKE_LON", _volspike_h(2.0, 1.5, 8.0, 12.0))
_reg("BTC_VOLSPIKE_NY", _volspike_h(2.0, 1.5, 13.0, 17.0))
_reg("BTC_VOLSPIKE_ASIA_MT", _mt(_volspike_h(2.2, 1.5, 0.0, 8.0)))

# ═══════════════════════════════════════════════════════════
# ETH — buracos PDH/NR/OUT + refine IMP/HL amarelos
# ═══════════════════════════════════════════════════════════
_reg("ETH_PDH_24H_V14", _pdh_h(1.4, 0.0, 24.0), "ETHUSD")
_reg("ETH_PDH_24H_V15", _pdh_h(1.5, 0.0, 24.0), "ETHUSD")
_reg("ETH_PDH_24H_MT_V13", _mt(_pdh_h(1.3, 0.0, 24.0)), "ETHUSD")
_reg("ETH_PDH_24H_MT_V14", _mt(_pdh_h(1.4, 0.0, 24.0)), "ETHUSD")
_reg("ETH_PDH_ASIA_MT", _mt(_pdh_h(1.3, 0.0, 8.0)), "ETHUSD")
_reg("ETH_PDH_ASIA_V13", _pdh_h(1.3, 0.0, 8.0), "ETHUSD")
_reg("ETH_PDH_ASIA_V14", _pdh_h(1.4, 0.0, 8.0), "ETHUSD")
_reg("ETH_PDH_NIGHT_2010", _pdh_h(1.2, 20.0, 10.0), "ETHUSD")
_reg("ETH_PDH_NIGHT_MT", _mt(_pdh_h(1.3, 20.0, 10.0)), "ETHUSD")
_reg("ETH_PDH_OVN_MT", _mt(_pdh_h(1.3, 18.0, 8.0)), "ETHUSD")
_reg("ETH_PDH_LON_0812", _pdh_h(1.2, 8.0, 12.0), "ETHUSD")
_reg("ETH_PDH_LON_0812_MT", _mt(_pdh_h(1.2, 8.0, 12.0)), "ETHUSD")
_reg("ETH_PDH_NY_1317", _pdh_h(1.2, 13.0, 17.0), "ETHUSD")
_reg("ETH_PDH_NY_1317_MT", _mt(_pdh_h(1.2, 13.0, 17.0)), "ETHUSD")
_reg("ETH_PDH_LN_0817", _pdh_h(1.2, 8.0, 17.0), "ETHUSD")
_reg("ETH_PDH_1622", _pdh_h(1.2, 16.0, 22.0), "ETHUSD")
_reg("ETH_PDH_24H_TP20", _cap_tp(_pdh_h(1.4, 0.0, 24.0), 2.0), "ETHUSD")
_reg("ETH_PDH_ASIA_TP20", _cap_tp(_pdh_h(1.3, 0.0, 8.0), 2.0), "ETHUSD")

_reg("ETH_NR5_24H_V15", _nr_h(5, 1.5, 0.0, 24.0), "ETHUSD")
_reg("ETH_NR5_24H_MT_V14", _mt(_nr_h(5, 1.4, 0.0, 24.0)), "ETHUSD")
_reg("ETH_NR5_24H_MT_V15", _mt(_nr_h(5, 1.5, 0.0, 24.0)), "ETHUSD")
_reg("ETH_NR4_24H_MT", _mt(_nr_h(4, 1.4, 0.0, 24.0)), "ETHUSD")
_reg("ETH_NR4_24H_V15", _nr_h(4, 1.5, 0.0, 24.0), "ETHUSD")
_reg("ETH_NR5_NIGHT", _nr_h(5, 1.4, 20.0, 10.0), "ETHUSD")
_reg("ETH_NR5_NIGHT_MT", _mt(_nr_h(5, 1.4, 20.0, 10.0)), "ETHUSD")
_reg("ETH_NR5_LON_0812", _nr_h(5, 1.4, 8.0, 12.0), "ETHUSD")
_reg("ETH_NR5_LON_MT", _mt(_nr_h(5, 1.4, 8.0, 12.0)), "ETHUSD")
_reg("ETH_NR5_NY_1317", _nr_h(5, 1.4, 13.0, 17.0), "ETHUSD")
_reg("ETH_NR5_NY_MT", _mt(_nr_h(5, 1.4, 13.0, 17.0)), "ETHUSD")
_reg("ETH_NR5_1622", _nr_h(5, 1.3, 16.0, 22.0), "ETHUSD")
_reg("ETH_NR5_1622_MT", _mt(_nr_h(5, 1.3, 16.0, 22.0)), "ETHUSD")
_reg("ETH_NR4_ASIA_MT", _mt(_nr_h(4, 1.4, 0.0, 8.0)), "ETHUSD")
_reg("ETH_NR7_24H", _nr_h(7, 1.4, 0.0, 24.0), "ETHUSD")
_reg("ETH_NR7_24H_MT", _mt(_nr_h(7, 1.4, 0.0, 24.0)), "ETHUSD")

_reg("ETH_HL_24H_MT_V14", _mt(_hl_h(1.4, 0.0, 24.0)), "ETHUSD")
_reg("ETH_HL_24H_MT_V15", _mt(_hl_h(1.5, 0.0, 24.0)), "ETHUSD")
_reg("ETH_HL_24H_MT_TP20", _mt(_cap_tp(_hl_h(1.4, 0.0, 24.0), 2.0)), "ETHUSD")
_reg("ETH_HL_NIGHT", _hl_h(1.4, 20.0, 10.0), "ETHUSD")
_reg("ETH_HL_NIGHT_MT", _mt(_hl_h(1.4, 20.0, 10.0)), "ETHUSD")
_reg("ETH_HL_LON_0812", _hl_h(1.4, 8.0, 12.0), "ETHUSD")
_reg("ETH_HL_LON_MT", _mt(_hl_h(1.4, 8.0, 12.0)), "ETHUSD")
_reg("ETH_HL_NY_1317", _hl_h(1.4, 13.0, 17.0), "ETHUSD")
_reg("ETH_HL_NY_MT", _mt(_hl_h(1.4, 13.0, 17.0)), "ETHUSD")
_reg("ETH_HL_ASIA_MT", _mt(_hl_h(1.4, 0.0, 8.0)), "ETHUSD")
_reg("ETH_HL_1622", _hl_h(1.4, 16.0, 22.0), "ETHUSD")

_reg("ETH_IMP_CONT_24H_MT_V18", _mt(_impulse_h(1.8, 1.6, 0.0, 24.0, 2.0)), "ETHUSD")
_reg("ETH_IMP_CONT_24H_MT_TP25", _mt(_impulse_h(2.0, 1.5, 0.0, 24.0, 2.5)), "ETHUSD")
_reg("ETH_IMP_CONT_24H_V16", _impulse_h(1.6, 1.7, 0.0, 24.0, 2.0), "ETHUSD")
_reg("ETH_IMP_CONT_24H_V20", _impulse_h(2.0, 1.7, 0.0, 24.0, 2.0), "ETHUSD")
_reg("ETH_IMP_ASIA_MT_V18", _mt(_impulse_h(1.8, 1.6, 0.0, 8.0, 2.0)), "ETHUSD")
_reg("ETH_IMP_ASIA_TP25", _impulse_h(1.8, 1.4, 0.0, 8.0, 2.5), "ETHUSD")
_reg("ETH_IMP_ASIA_V18", _impulse_h(1.8, 1.6, 0.0, 8.0, 2.0), "ETHUSD")
_reg("ETH_IMP_NIGHT_MT", _mt(_impulse_h(2.0, 1.5, 20.0, 10.0, 2.0)), "ETHUSD")
_reg("ETH_IMP_NIGHT_V18", _impulse_h(1.8, 1.6, 20.0, 10.0, 2.0), "ETHUSD")
_reg("ETH_IMP_NIGHT_TP25", _impulse_h(2.0, 1.5, 20.0, 10.0, 2.5), "ETHUSD")
_reg("ETH_IMP_OVN_MT", _mt(_impulse_h(2.0, 1.5, 18.0, 8.0, 2.0)), "ETHUSD")
_reg("ETH_IMP_LON_0812", _impulse_h(2.0, 1.5, 8.0, 12.0, 2.0), "ETHUSD")
_reg("ETH_IMP_LON_MT", _mt(_impulse_h(2.0, 1.5, 8.0, 12.0, 2.0)), "ETHUSD")
_reg("ETH_IMP_NY_1317", _impulse_h(2.0, 1.5, 13.0, 17.0, 2.0), "ETHUSD")
_reg("ETH_IMP_NY_MT", _mt(_impulse_h(2.0, 1.5, 13.0, 17.0, 2.0)), "ETHUSD")
_reg("ETH_IMP_1622", _impulse_h(2.0, 1.5, 16.0, 22.0, 2.0), "ETHUSD")
_reg("ETH_IMP_1622_MT", _mt(_impulse_h(2.0, 1.5, 16.0, 22.0, 2.0)), "ETHUSD")
_reg("ETH_IMP_TP30_24H", _impulse_h(2.0, 1.5, 0.0, 24.0, 3.0), "ETHUSD")

_reg("ETH_OUT_ASIA_MT", _mt(_outside_h(1.4, 0.0, 8.0)), "ETHUSD")
_reg("ETH_OUT_ASIA_V14", _outside_h(1.4, 0.0, 8.0), "ETHUSD")
_reg("ETH_OUT_24H_MT", _mt(_outside_h(1.4, 0.0, 24.0)), "ETHUSD")
_reg("ETH_OUT_NIGHT", _outside_h(1.4, 20.0, 10.0), "ETHUSD")
_reg("ETH_OUT_NIGHT_MT", _mt(_outside_h(1.4, 20.0, 10.0)), "ETHUSD")
_reg("ETH_OUT_LON", _outside_h(1.4, 8.0, 12.0), "ETHUSD")
_reg("ETH_OUT_NY", _outside_h(1.4, 13.0, 17.0), "ETHUSD")
_reg("ETH_OUT_24H_TP20", _cap_tp(_outside_h(1.4, 0.0, 24.0), 2.0), "ETHUSD")

_reg("ETH_ENGULF_24H_MT", _mt(_engulf_h(1.4, 0.0, 24.0)), "ETHUSD")
_reg("ETH_ENGULF_24H_V14", _engulf_h(1.4, 0.0, 24.0), "ETHUSD")
_reg("ETH_ENGULF_ASIA", _engulf_h(1.4, 0.0, 8.0), "ETHUSD")
_reg("ETH_ENGULF_NIGHT", _engulf_h(1.4, 20.0, 10.0), "ETHUSD")
_reg("ETH_ENGULF_LON", _engulf_h(1.4, 8.0, 12.0), "ETHUSD")
_reg("ETH_ENGULF_NY", _engulf_h(1.4, 13.0, 17.0), "ETHUSD")

_reg("ETH_SQZ_24H_MT", _mt(_sqz_break_h(1.5, 0.0, 24.0)), "ETHUSD")
_reg("ETH_SQZ_24H_V15", _sqz_break_h(1.5, 0.0, 24.0), "ETHUSD")
_reg("ETH_SQZ_ASIA_MT", _mt(_sqz_break_h(1.5, 0.0, 8.0)), "ETHUSD")
_reg("ETH_SQZ_NIGHT", _sqz_break_h(1.5, 20.0, 10.0), "ETHUSD")
_reg("ETH_SQZ_LON", _sqz_break_h(1.5, 8.0, 12.0), "ETHUSD")

_reg("ETH_EMA_RECL_24H_MT", _mt(_ema_reclaim_h(0.0, 24.0, 1.3)), "ETHUSD")
_reg("ETH_EMA_RECL_24H_V14", _ema_reclaim_h(0.0, 24.0, 1.4), "ETHUSD")
_reg("ETH_EMA_RECL_NIGHT", _ema_reclaim_h(20.0, 10.0, 1.3), "ETHUSD")
_reg("ETH_EMA_RECL_LON", _ema_reclaim_h(8.0, 12.0, 1.3), "ETHUSD")
_reg("ETH_EMA_RECL_NY", _ema_reclaim_h(13.0, 17.0, 1.3), "ETHUSD")

_reg("ETH_INS_NIGHT_MT", _mt(_inside_h(1.4, 20.0, 10.0)), "ETHUSD")
_reg("ETH_INS_ASIA_MT", _mt(_inside_h(1.4, 0.0, 8.0)), "ETHUSD")
_reg("ETH_INS_LON_0812", _inside_h(1.3, 8.0, 12.0), "ETHUSD")
_reg("ETH_INS_LON_MT", _mt(_inside_h(1.3, 8.0, 12.0)), "ETHUSD")
_reg("ETH_INS_NY_1317", _inside_h(1.3, 13.0, 17.0), "ETHUSD")
_reg("ETH_INS_NY_MT", _mt(_inside_h(1.3, 13.0, 17.0)), "ETHUSD")
_reg("ETH_INS_1622_MT", _mt(_inside_h(1.3, 16.0, 22.0)), "ETHUSD")
_reg("ETH_INS_1622_V14", _inside_h(1.4, 16.0, 22.0), "ETHUSD")
_reg("ETH_DBL_INS_24H_MT", _mt(_dbl_inside_h(1.3, 0.0, 24.0)), "ETHUSD")
_reg("ETH_DBL_INS_ASIA", _dbl_inside_h(1.3, 0.0, 8.0), "ETHUSD")

_reg("ETH_H4_PB_EMA_24H_MT", _mt(_h4_pb_ema_h(0.0, 24.0)), "ETHUSD")
_reg("ETH_H4_PB_EMA_LON", _h4_pb_ema_h(8.0, 12.0), "ETHUSD")
_reg("ETH_H4_PB_EMA_NY", _h4_pb_ema_h(13.0, 17.0), "ETHUSD")
_reg("ETH_H4_PB_EMA_1622", _h4_pb_ema_h(16.0, 22.0), "ETHUSD")

_reg("ETH_VWAP_FADE_24H", _vwap_fade_h(1.2, 1.2, 0.0, 24.0), "ETHUSD")
_reg("ETH_VWAP_FADE_24H_MT", _mt(_vwap_fade_h(1.3, 1.2, 0.0, 24.0)), "ETHUSD")
_reg("ETH_VWAP_FADE_ASIA", _vwap_fade_h(1.2, 1.2, 0.0, 8.0), "ETHUSD")
_reg("ETH_VWAP_FADE_NIGHT", _vwap_fade_h(1.2, 1.2, 20.0, 10.0), "ETHUSD")
_reg("ETH_VWAP_FADE_LON", _vwap_fade_h(1.2, 1.2, 8.0, 12.0), "ETHUSD")
_reg("ETH_VWAP_FADE_NY", _vwap_fade_h(1.2, 1.2, 13.0, 17.0), "ETHUSD")
_reg("ETH_VWAP_CONT_24H", _vwap_cont_h(1.3, 0.3, 0.0, 24.0), "ETHUSD")
_reg("ETH_VWAP_CONT_24H_MT", _mt(_vwap_cont_h(1.3, 0.3, 0.0, 24.0)), "ETHUSD")
_reg("ETH_VWAP_CONT_LON", _vwap_cont_h(1.3, 0.3, 8.0, 12.0), "ETHUSD")
_reg("ETH_VWAP_CONT_NY", _vwap_cont_h(1.3, 0.3, 13.0, 17.0), "ETHUSD")

_reg("ETH_VOLSPIKE_24H_MT", _mt(_volspike_h(2.0, 1.5, 0.0, 24.0)), "ETHUSD")
_reg("ETH_VOLSPIKE_NIGHT", _volspike_h(2.0, 1.5, 20.0, 10.0), "ETHUSD")
_reg("ETH_VOLSPIKE_LON", _volspike_h(2.0, 1.5, 8.0, 12.0), "ETHUSD")
_reg("ETH_VOLSPIKE_NY", _volspike_h(2.0, 1.5, 13.0, 17.0), "ETHUSD")
_reg("ETH_VOLSPIKE_ASIA_MT", _mt(_volspike_h(2.2, 1.5, 0.0, 8.0)), "ETHUSD")

_reg("ETH_FRI_SUN_NR4", _fri_sun(_nr_h(4, 1.4, 0.0, 24.0)), "ETHUSD")
_reg("ETH_FRI_SUN_NR5", _fri_sun(_nr_h(5, 1.4, 0.0, 24.0)), "ETHUSD")
_reg("ETH_FRI_SUN_PDH", _fri_sun(_pdh_h(1.2, 0.0, 24.0)), "ETHUSD")
_reg("ETH_FRI_SUN_HL", _fri_sun(_hl_h(1.4, 0.0, 24.0)), "ETHUSD")
_reg("ETH_FRI_SUN_OUT", _fri_sun(_outside_h(1.4, 0.0, 24.0)), "ETHUSD")
_reg("ETH_FRI_MON_INS", _fri_mon(_inside_h(1.3, 0.0, 24.0)), "ETHUSD")
_reg("ETH_FRI_MON_NR5", _fri_mon(_nr_h(5, 1.4, 0.0, 24.0)), "ETHUSD")
_reg("ETH_FRI_MON_PDH", _fri_mon(_pdh_h(1.2, 0.0, 24.0)), "ETHUSD")
_reg("ETH_FRI_MON_HL", _fri_mon(_hl_h(1.4, 0.0, 24.0)), "ETHUSD")
_reg("ETH_FRI_MON_IMP", _fri_mon(_impulse_h(2.0, 1.5, 0.0, 24.0, 2.0)), "ETHUSD")
_reg("ETH_WE_NR4", _we(_nr_h(4, 1.4, 0.0, 24.0)), "ETHUSD")
_reg("ETH_WE_PDH_V13", _we(_pdh_h(1.3, 0.0, 24.0)), "ETHUSD")
_reg("ETH_WE_OUT", _we(_outside_h(1.4, 0.0, 24.0)), "ETHUSD")
_reg("ETH_WE_ENGULF", _we(_engulf_h(1.4, 0.0, 24.0)), "ETHUSD")

# ═══════════════════════════════════════════════════════════
# Multi-TF M30 — famílias promissoras (não duplicar GOs M15)
# ═══════════════════════════════════════════════════════════
_reg("BTC_HL_24H_MT_M30", _mt(_hl_h(1.4, 0.0, 24.0)), tf=30)
_reg("BTC_HL_NIGHT_M30", _hl_h(1.4, 20.0, 10.0), tf=30)
_reg("BTC_IMP_NIGHT_M30", _impulse_h(2.0, 1.5, 20.0, 10.0, 2.0), tf=30)
_reg("BTC_IMP_CONT_24H_M30", _impulse_h(1.8, 1.6, 0.0, 24.0, 2.0), tf=30)
_reg("BTC_NR5_NIGHT_M30", _nr_h(5, 1.4, 20.0, 10.0), tf=30)
_reg("BTC_NR4_24H_M30", _nr_h(4, 1.4, 0.0, 24.0), tf=30)
_reg("BTC_NR5_24H_M30", _nr_h(5, 1.4, 0.0, 24.0), tf=30)
_reg("BTC_PDH_24H_M30", _pdh_h(1.2, 0.0, 24.0), tf=30)
_reg("BTC_PDH_ASIA_M30", _pdh_h(1.2, 0.0, 8.0), tf=30)
_reg("BTC_OUT_OVN_M30", _outside_h(1.4, 18.0, 8.0), tf=30)
_reg("BTC_ENGULF_24H_M30", _engulf_h(1.4, 0.0, 24.0), tf=30)
_reg("BTC_EMA_RECL_24H_M30", _ema_reclaim_h(0.0, 24.0, 1.3), tf=30)
_reg("BTC_VWAP_FADE_24H_M30", _vwap_fade_h(1.3, 1.2, 0.0, 24.0), tf=30)
_reg("BTC_INS_NIGHT_M30", _inside_h(1.4, 20.0, 10.0), tf=30)
_reg("BTC_H4_PB_EMA_NIGHT_M30", _h4_pb_ema_h(20.0, 10.0), tf=30)

_reg("ETH_PDH_ASIA_M30", _pdh_h(1.3, 0.0, 8.0), "ETHUSD", tf=30)
_reg("ETH_PDH_24H_M30", _pdh_h(1.4, 0.0, 24.0), "ETHUSD", tf=30)
_reg("ETH_NR5_24H_MT_M30", _mt(_nr_h(5, 1.4, 0.0, 24.0)), "ETHUSD", tf=30)
_reg("ETH_IMP_ASIA_MT_M30", _mt(_impulse_h(1.8, 1.4, 0.0, 8.0, 2.0)), "ETHUSD", tf=30)
_reg("ETH_IMP_CONT_24H_M30", _impulse_h(1.8, 1.6, 0.0, 24.0, 2.0), "ETHUSD", tf=30)
_reg("ETH_HL_24H_MT_M30", _mt(_hl_h(1.4, 0.0, 24.0)), "ETHUSD", tf=30)
_reg("ETH_OUT_ASIA_M30", _outside_h(1.4, 0.0, 8.0), "ETHUSD", tf=30)
_reg("ETH_VWAP_FADE_24H_M30", _vwap_fade_h(1.3, 1.2, 0.0, 24.0), "ETHUSD", tf=30)
_reg("ETH_INS_LON_M30", _inside_h(1.3, 8.0, 12.0), "ETHUSD", tf=30)
_reg("ETH_NR5_LON_M30", _nr_h(5, 1.4, 8.0, 12.0), "ETHUSD", tf=30)


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
    """Worker: roda 1 setup; retorna dict ranking (+ trades se GO)."""
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
    ap.add_argument("--only", default="", help="csv de nomes (debug)")
    args = ap.parse_args()

    want = {s.strip().upper() for s in args.syms.split(",") if s.strip()}
    os.environ["KIMI_GROUP"] = "crypto"
    os.makedirs("logs", exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    log_path = f"logs/backtest_setups_novos_v15_btc_eth_{stamp}.txt"
    logf = open(log_path, "w", encoding="utf-8")

    def out(s):
        try:
            print(s)
        except UnicodeEncodeError:
            print(s.encode("ascii", "replace").decode("ascii"))
        logf.write(s + "\n")
        logf.flush()

    only = {x.strip() for x in args.only.split(",") if x.strip()}
    strat_names = [n for n, fn in STRATS.items()
                   if STRAT_SYM[n] in want and (not only or n in only)]
    out(f"{'#' * 74}\n# SETUPS NOVOS V15 — BTC/ETH EXPAND "
        f"({len(strat_names)} setups) · {datetime.now():%d/%m %H:%M}\n"
        f"# Ativos: {sorted(want)} · workers={args.workers}\n"
        f"# SKIP: GOs v14 já wired · ORDER_FLOW BTC\n"
        f"# NÃO altera live até GO · régua rigorosa\n{'#' * 74}")

    from backtest_crypto_pro import mt5_connect
    mt5 = mt5_connect()

    # Carrega M15 + M30 conforme necessidade
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
                    f"logs/novos_v15_{row['sym']}_M{row['tf']}_{row['setup']}.csv",
                    index=False)
            if n_done % 20 == 0:
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
                        f"logs/novos_v15_{row['sym']}_M{row['tf']}_{row['setup']}.csv",
                        index=False)
                if n_done % 20 == 0:
                    out(f"  … progresso {n_done}/{len(strat_names)} "
                        f"({time.time() - t_all:.0f}s)")

    out(f"\n{'#' * 74}\n  RANKING V15 BTC/ETH  ({time.time() - t_all:.0f}s total)\n"
        f"{'#' * 74}")
    rk = pd.DataFrame(ranking)
    goes, yellows, reds = [], [], []
    if not rk.empty:
        # ordenar por net (NaN por último)
        rk["net"] = pd.to_numeric(rk.get("net"), errors="coerce")
        rk = rk.sort_values("net", ascending=False, na_position="last")
        for _, r in rk.iterrows():
            if r.get("n", 0) == 0:
                out(f"  {r.setup:<32} sem trades / {r.get('veredito')}")
                continue
            out(f"  {r.setup:<32} n={int(r.n):<5} net {r.net:+.3f} R  "
                f"PF {r.PF:<5} cons {r['cons%']}%  OOS {r.oos_net}  {r.veredito}")
            vs = str(r.veredito)
            if "🟢" in vs and "GO" in vs:
                goes.append(r.setup)
            elif "🟡" in vs:
                yellows.append(r.setup)
            else:
                reds.append(r.setup)
        # drop helper cols before save
        save = rk.drop(columns=[c for c in ("err",) if c in rk.columns], errors="ignore")
        save.to_csv(f"logs/backtest_setups_novos_v15_btc_eth_ranking_{stamp}.csv",
                    index=False)

    out(f"\n# GO encontrados ({len(goes)}): {goes if goes else 'nenhum'}")
    out(f"# amarelos ({len(yellows)}): {yellows[:40]}{'…' if len(yellows) > 40 else ''}")
    out(f"# vermelhos/fracos: {len(reds)}")
    out(f"# FIM — {log_path}")
    logf.close()
    print(f"\n-> {log_path}")
    if goes:
        print("GO:", ", ".join(goes))


if __name__ == "__main__":
    main()
