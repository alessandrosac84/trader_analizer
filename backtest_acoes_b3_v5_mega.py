"""
backtest_acoes_b3_v5_mega.py — bateria FOCO fracos AÇÕES B3.

Prioridade: PETR4 (só 2 GO) · ITUB4/ABEV3 (zero) · BBDC4 (zero/fraco).
NÃO re-testa nomes v3+v4. Famílias novas + refinamentos de near-miss.

Régua GO (não baixar):
  net ≥ +0,10 R · PF ≥ 1,25 · cons ≥ 55% · OOS+ · n ≥ 40

Uso:
  python backtest_acoes_b3_v5_mega.py
  python backtest_acoes_b3_v5_mega.py --syms PETR4,ITUB4,ABEV3,BBDC4 --workers 8
  python backtest_acoes_b3_v5_mega.py --list-only
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
from backtest_setups_novos_v2 import add_extra_v2
from backtest_setups_novos_v5 import _h4_ok
from backtest_setups_novos_v9_refine import _mt
from backtest_setups_novos_v13_btc_24h import (
    _hh_ok, _inside_h, _nr_h, _impulse_h, _h4_pb_ema_h, _volspike_h,
    _outside_h, _hl_h, _dbl_inside_h,
)
from backtest_setups_novos_v14_btc_eth import (
    _engulf_h, _sqz_break_h, _ema_reclaim_h,
)
from backtest_setups_novos_v15_btc_eth import (
    _vwap_fade_h, _vwap_cont_h, _cap_tp, _rr, _atr_regime,
)

import backtest_acoes_b3_v3_mega as v3
import backtest_acoes_b3_v4_mega as v4
from backtest_acoes_b3_v3_mega import (
    SYMBOLS, FEE_PCT_RT, _b3_sess, _gap_aligned, _orb30_h, _gap_fade_h,
    _gap_cont_h, _pdh_stock_h, _vwap_open_h, _simulate_stock, _fetch_stock,
    stock_cost_pts,
)

FOCUS_DEFAULT = ["PETR4", "ITUB4", "ABEV3", "BBDC4"]

STRATS: dict = {}
STRAT_SYM: dict = {}
STRAT_TF: dict = {}
ONE_PER_DAY: set = set()
_WORKER_DFS: dict = {}


def _reg(name, fn, sym, tf=15, one_per_day=False):
    STRATS[name] = fn
    STRAT_SYM[name] = sym
    STRAT_TF[name] = tf
    if one_per_day:
        ONE_PER_DAY.add(name)


# ── Enrich + novos helpers ──────────────────────────────────────────────────

def _enrich_v5(df: pd.DataFrame) -> pd.DataFrame:
    """ORB15 / ORB60 (IB) / prev_day_mid — sem look-ahead (groupby day)."""
    if "day" not in df.columns:
        df = df.copy()
        df["day"] = df.index.date
    day = df["day"]
    hh = df["hh"]
    for end, tag in ((10.25, "orb15"), (11.0, "orb60")):
        mask = (hh >= 10.0) & (hh < end)
        df[f"{tag}_hi"] = df["High"].where(mask).groupby(day).transform("max")
        df[f"{tag}_lo"] = df["Low"].where(mask).groupby(day).transform("min")
    if "prev_day_hi" in df.columns and "prev_day_lo" in df.columns:
        df["prev_day_mid"] = 0.5 * (df["prev_day_hi"] + df["prev_day_lo"])
    return df


def _trend_f(fn, mode="with"):
    """Filtra pela tendência EMA50/EMA200. mode=with|against."""
    def _f(df, i, sym):
        r = df.iloc[i]
        base = fn(df, i, sym)
        if not base:
            return None
        e50 = getattr(r, "ema50", np.nan)
        e200 = getattr(r, "ema200", np.nan)
        if pd.isna(e50) or pd.isna(e200):
            return None
        bull = float(e50) > float(e200) and float(r.Close) > float(e50)
        bear = float(e50) < float(e200) and float(r.Close) < float(e50)
        d = base["dir"]
        if mode == "with":
            if d == "COMPRA" and not bull:
                return None
            if d == "VENDA" and not bear:
                return None
        else:
            if d == "COMPRA" and not bear:
                return None
            if d == "VENDA" and not bull:
                return None
        return base
    return _f


def _orb_range_h(hi_col, lo_col, vol=1.2, hh0=11.0, hh1=13.0,
                 rr1=1.5, rr2=2.0, gap_dir=False, min_atr=0.35):
    """Rompe range custom (orb15/orb60) com volume + H4."""
    def _f(df, i, sym):
        if i < 5:
            return None
        r, p = df.iloc[i], df.iloc[i - 1]
        if not _hh_ok(r.hh, hh0, hh1):
            return None
        hi = getattr(r, hi_col, np.nan)
        lo = getattr(r, lo_col, np.nan)
        if pd.isna(hi) or pd.isna(lo) or float(r.atr or 0) <= 0:
            return None
        if (float(hi) - float(lo)) < min_atr * float(r.atr):
            return None
        if float(r.Volume or 0) < vol * float(r.vol8 or 0):
            return None
        g = float(r.gap_pct) if not pd.isna(r.gap_pct) else 0.0
        if r.Close > hi and p.Close <= hi:
            if gap_dir and g < -0.1:
                return None
            d, sl = "COMPRA", float(lo)
        elif r.Close < lo and p.Close >= lo:
            if gap_dir and g > 0.1:
                return None
            d, sl = "VENDA", float(hi)
        else:
            return None
        if not _h4_ok(r, d):
            return None
        return _sig(d, float(r.Close), sl, rr1, rr2, True)
    return _f


def _orb_retest_h(vol=1.2, hh0=11.0, hh1=14.0):
    """Break ORB + reteste que segura → continua."""
    def _f(df, i, sym):
        if i < 6:
            return None
        r, p, p2 = df.iloc[i], df.iloc[i - 1], df.iloc[i - 2]
        if not _hh_ok(r.hh, hh0, hh1):
            return None
        if pd.isna(r.orb_hi) or pd.isna(r.orb_lo) or float(r.atr or 0) <= 0:
            return None
        if float(r.Volume or 0) < vol * float(r.vol8 or 0):
            return None
        # já tinha rompido (p2), retestou (p tocou), agora segue
        if (float(p2.Close) > float(r.orb_hi)
                and float(p.Low) <= float(r.orb_hi) * 1.002
                and float(r.Close) > float(r.orb_hi)
                and float(r.Close) > float(r.Open)):
            d, sl = "COMPRA", float(min(p.Low, r.orb_hi))
        elif (float(p2.Close) < float(r.orb_lo)
              and float(p.High) >= float(r.orb_lo) * 0.998
              and float(r.Close) < float(r.orb_lo)
              and float(r.Close) < float(r.Open)):
            d, sl = "VENDA", float(max(p.High, r.orb_lo))
        else:
            return None
        if not _h4_ok(r, d):
            return None
        return _sig(d, float(r.Close), sl, 1.5, 2.0, True)
    return _f


def _failed_orb_h(vol=1.2, hh0=10.75, hh1=13.0):
    """Rompimento ORB que falha → reversão."""
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
        if (float(p.Close) > float(r.orb_hi)
                and float(r.Close) < float(r.orb_hi)
                and float(r.Close) < float(r.Open)):
            d, sl = "VENDA", float(max(p.High, r.High))
        elif (float(p.Close) < float(r.orb_lo)
              and float(r.Close) > float(r.orb_lo)
              and float(r.Close) > float(r.Open)):
            d, sl = "COMPRA", float(min(p.Low, r.Low))
        else:
            return None
        if not _h4_ok(r, d):
            return None
        return _sig(d, float(r.Close), sl, 1.5, 2.0, True)
    return _f


def _open_drive_h(vol=1.15, hh0=10.0, hh1=11.0, rr1=1.5, rr2=2.0):
    """Continuação da 1ª hora a favor do open vs prev_close."""
    def _f(df, i, sym):
        if i < 3:
            return None
        r, p = df.iloc[i], df.iloc[i - 1]
        if not _hh_ok(r.hh, hh0, hh1):
            return None
        if float(r.atr or 0) <= 0 or pd.isna(r.day_open):
            return None
        if float(r.Volume or 0) < vol * float(r.vol8 or 0):
            return None
        g = float(r.gap_pct) if not pd.isna(r.gap_pct) else 0.0
        # drive up: close > day_open e rompe high anterior
        if g >= 0.15 and float(r.Close) > float(r.day_open) and float(r.Close) > float(p.High):
            d, sl = "COMPRA", float(min(p.Low, r.day_open))
        elif g <= -0.15 and float(r.Close) < float(r.day_open) and float(r.Close) < float(p.Low):
            d, sl = "VENDA", float(max(p.High, r.day_open))
        else:
            return None
        if not _h4_ok(r, d):
            return None
        return _sig(d, float(r.Close), sl, rr1, rr2, True)
    return _f


def _pdm_h(vol=1.2, hh0=10.0, hh1=15.0):
    """Rompe midpoint do dia anterior."""
    def _f(df, i, sym):
        if i < 30:
            return None
        r, p = df.iloc[i], df.iloc[i - 1]
        if not _hh_ok(r.hh, hh0, hh1):
            return None
        mid = getattr(r, "prev_day_mid", np.nan)
        if pd.isna(mid) or float(r.atr or 0) <= 0:
            return None
        if float(r.Volume or 0) < vol * float(r.vol8 or 0):
            return None
        mid = float(mid)
        if r.Close > mid and p.Close <= mid and float(r.Close) > float(r.Open):
            d, sl = "COMPRA", float(p.Low)
        elif r.Close < mid and p.Close >= mid and float(r.Close) < float(r.Open):
            d, sl = "VENDA", float(p.High)
        else:
            return None
        if not _h4_ok(r, d):
            return None
        return _sig(d, float(r.Close), sl, 1.5, 2.0, True)
    return _f


def _lunch_fade_h(vol=1.1, hh0=12.0, hh1=14.0, dist=0.8):
    """Fade de extensão vs VWAP no almoço."""
    def _f(df, i, sym):
        if i < 5:
            return None
        r, p = df.iloc[i], df.iloc[i - 1]
        if not _hh_ok(r.hh, hh0, hh1):
            return None
        if float(r.Volume or 0) < vol * float(r.vol8 or 0):
            return None
        vd = getattr(r, "vwap_dist", np.nan)
        if pd.isna(vd) or float(r.atr or 0) <= 0:
            return None
        vd = float(vd)
        if vd >= dist and float(r.Close) < float(r.Open) and float(r.Close) < float(p.Close):
            d, sl = "VENDA", float(max(p.High, r.High))
        elif vd <= -dist and float(r.Close) > float(r.Open) and float(r.Close) > float(p.Close):
            d, sl = "COMPRA", float(min(p.Low, r.Low))
        else:
            return None
        return _sig(d, float(r.Close), sl, 1.4, 1.8, True)
    return _f


def _first_pb_orb_h(vol=1.15, hh0=11.0, hh1=13.5):
    """Após ORB break, 1º pullback que segura EMA20."""
    def _f(df, i, sym):
        if i < 8:
            return None
        r, p = df.iloc[i], df.iloc[i - 1]
        if not _hh_ok(r.hh, hh0, hh1):
            return None
        if pd.isna(r.orb_hi) or pd.isna(r.ema20) or float(r.atr or 0) <= 0:
            return None
        if float(r.Volume or 0) < vol * float(r.vol8 or 0):
            return None
        # dia já acima do ORB (close day open side) + PB na ema20
        if (float(r.Close) > float(r.orb_hi)
                and float(p.Low) <= float(p.ema20)
                and float(r.Close) > float(r.ema20)
                and float(r.Close) > float(r.Open)):
            d, sl = "COMPRA", float(min(p.Low, r.Low))
        elif (float(r.Close) < float(r.orb_lo)
              and float(p.High) >= float(p.ema20)
              and float(r.Close) < float(r.ema20)
              and float(r.Close) < float(r.Open)):
            d, sl = "VENDA", float(max(p.High, r.High))
        else:
            return None
        if not _h4_ok(r, d):
            return None
        return _sig(d, float(r.Close), sl, 1.5, 2.2, True)
    return _f


def _gap_cont_soft(min_pct=0.40, hh0=10.0, hh1=12.0, vol=1.0, rr1=1.5, rr2=2.5):
    """GAP cont com vol mais frouxo (bancos/ABEV geram pouco trade)."""
    return _gap_cont_h(min_pct, hh0, hh1, vol=vol)  # type: ignore[call-arg]


# monkey-patch: v3 _gap_cont_h aceita vol — confirmar assinatura
# (já tem vol=1.2 default)


def _known_v3_v4(syms: list[str]) -> set[str]:
    v3._build_registry(list(syms))
    known = set(v3.STRATS.keys())
    v4._build_registry(list(syms), skip_v3=True)
    known |= set(v4.STRATS.keys())
    return known


# ── Registry v5 ─────────────────────────────────────────────────────────────

def _build_registry(syms: list[str], skip_known: bool = True):
    STRATS.clear()
    STRAT_SYM.clear()
    STRAT_TF.clear()
    ONE_PER_DAY.clear()

    known = _known_v3_v4(syms) if skip_known else set()

    def reg(name, fn, sym, tf=15, one_per_day=False):
        if name in known:
            return
        _reg(name, fn, sym, tf=tf, one_per_day=one_per_day)

    # Sessões novas (não cobertas como blocos completos)
    NEW_SESS = [
        ("OPEN1H", 10.0, 11.0),
        ("LATEAM", 11.0, 13.0),
        ("LUNCH", 12.0, 14.0),
        ("AFT", 14.0, 16.0),
        ("FULL", 10.0, 16.5),
    ]

    for sx in syms:
        # ── GAPC near-miss PETR: soft vol + novos thresholds + trend/ATR ──
        for gp, gtag in (
            (0.20, "G20"), (0.40, "G40"), (0.50, "G50"),
            (0.55, "G55"), (0.70, "G70"), (0.90, "G90"),
        ):
            for hh0, hh1, stag in (
                (10.0, 12.0, ""),
                (10.0, 11.5, "_AM"),
                (10.0, 13.0, "_WIDE"),
                (10.5, 12.5, "_LATE"),
            ):
                base = _gap_cont_h(gp, hh0, hh1, vol=1.0)
                reg(f"{sx}_GAPC_{gtag}{stag}_SOFT", base, sx, one_per_day=True)
                reg(f"{sx}_GAPC_{gtag}{stag}_SOFT_MT",
                    _mt(_gap_cont_h(gp, hh0, hh1 + 0.5, vol=1.0)), sx,
                    one_per_day=True)
                reg(f"{sx}_GAPC_{gtag}{stag}_SOFT_TP15",
                    _cap_tp(_gap_cont_h(gp, hh0, hh1, vol=1.0), 1.5), sx,
                    one_per_day=True)
                reg(f"{sx}_GAPC_{gtag}{stag}_SOFT_TP25",
                    _cap_tp(_gap_cont_h(gp, hh0, hh1, vol=1.0), 2.5), sx,
                    one_per_day=True)
                reg(f"{sx}_GAPC_{gtag}{stag}_SOFT_RR18",
                    _rr(_gap_cont_h(gp, hh0, hh1, vol=1.0), 1.8, 2.5), sx,
                    one_per_day=True)
                reg(f"{sx}_GAPC_{gtag}{stag}_TREND",
                    _trend_f(_gap_cont_h(gp, hh0, hh1, vol=1.1), "with"), sx,
                    one_per_day=True)
                reg(f"{sx}_GAPC_{gtag}{stag}_LOATR",
                    _atr_regime(_gap_cont_h(gp, hh0, hh1, vol=1.1), 0.5, 1.15),
                    sx, one_per_day=True)
                reg(f"{sx}_GAPC_{gtag}{stag}_HIATR",
                    _atr_regime(_gap_cont_h(gp, hh0, hh1, vol=1.1), 1.0, 2.5),
                    sx, one_per_day=True)

        # GAPC clássicos com filtros novos (nomes distintos)
        for gp, gtag in ((0.30, "G30"), (0.45, "G45"), (0.60, "G60"),
                         (0.25, "G25"), (0.35, "G35")):
            reg(f"{sx}_GAPC_{gtag}_TREND",
                _trend_f(_gap_cont_h(gp, 10.0, 12.0), "with"), sx,
                one_per_day=True)
            reg(f"{sx}_GAPC_{gtag}_LOATR",
                _atr_regime(_gap_cont_h(gp, 10.0, 12.0), 0.5, 1.15), sx,
                one_per_day=True)
            reg(f"{sx}_GAPC_{gtag}_AM_TREND",
                _trend_f(_gap_cont_h(gp, 10.0, 11.5), "with"), sx,
                one_per_day=True)
            reg(f"{sx}_GAPC_{gtag}_AM_TP15",
                _cap_tp(_gap_cont_h(gp, 10.0, 11.5), 1.5), sx,
                one_per_day=True)
            reg(f"{sx}_GAPC_{gtag}_WIDE_MT",
                _mt(_gap_cont_h(gp, 10.0, 13.5)), sx, one_per_day=True)
            reg(f"{sx}_GAPC_{gtag}_SOFT_M5",
                _gap_cont_h(gp, 10.0, 12.0, vol=1.0), sx, tf=5,
                one_per_day=True)
            reg(f"{sx}_GAPC_{gtag}_AM_M5",
                _gap_cont_h(gp, 10.0, 11.5, vol=1.1), sx, tf=5,
                one_per_day=True)
            reg(f"{sx}_GAPC_{gtag}_TREND_H1",
                _trend_f(_gap_cont_h(gp, 10.0, 13.0), "with"), sx, tf=60,
                one_per_day=True)
            reg(f"{sx}_GAPC_{gtag}_SOFT_H1",
                _gap_cont_h(gp, 10.0, 13.0, vol=1.0), sx, tf=60,
                one_per_day=True)
            reg(f"{sx}_GAPC_{gtag}_SOFT_M30",
                _gap_cont_h(gp, 10.0, 13.0, vol=1.0), sx, tf=30,
                one_per_day=True)
            # GAP fade soft novos
            reg(f"{sx}_GAPF_{gtag}_SOFT",
                _gap_fade_h(gp, 10.0, 11.5, vol=1.0), sx, one_per_day=True)
            reg(f"{sx}_GAPF_{gtag}_TREND",
                _trend_f(_gap_fade_h(gp, 10.0, 11.5), "against"), sx,
                one_per_day=True)

        # ── ORB15 / ORB60 (IB) — família nova ──
        for vol, vtag in ((1.0, "V10"), (1.2, "V12"), (1.4, "V14"), (1.6, "V16")):
            for hh0, hh1, wtag in (
                (10.25, 12.0, ""), (10.25, 13.0, "_WIDE"),
                (10.5, 12.5, "_MID"), (11.0, 13.5, "_LATE"),
            ):
                reg(f"{sx}_ORB15_{vtag}{wtag}",
                    _orb_range_h("orb15_hi", "orb15_lo", vol, hh0, hh1),
                    sx, one_per_day=True)
                reg(f"{sx}_ORB15_GAP_{vtag}{wtag}",
                    _orb_range_h("orb15_hi", "orb15_lo", vol, hh0, hh1,
                                 gap_dir=True),
                    sx, one_per_day=True)
            for hh0, hh1, wtag in (
                (11.0, 13.0, ""), (11.0, 14.0, "_WIDE"),
                (11.5, 13.5, "_LATE"), (11.0, 12.5, "_EARLY"),
            ):
                reg(f"{sx}_ORB60_{vtag}{wtag}",
                    _orb_range_h("orb60_hi", "orb60_lo", vol, hh0, hh1),
                    sx, one_per_day=True)
                reg(f"{sx}_ORB60_GAP_{vtag}{wtag}",
                    _orb_range_h("orb60_hi", "orb60_lo", vol, hh0, hh1,
                                 gap_dir=True),
                    sx, one_per_day=True)

        # ORB30 com filtros/trend/ATR/TP novos
        for vol, vtag in ((1.0, "V10"), (1.2, "V12"), (1.4, "V14")):
            reg(f"{sx}_ORB30_{vtag}_TREND",
                _trend_f(_orb30_h(vol, 10.75, 13.0), "with"), sx,
                one_per_day=True)
            reg(f"{sx}_ORB30_{vtag}_LOATR",
                _atr_regime(_orb30_h(vol, 10.75, 13.0), 0.5, 1.2), sx,
                one_per_day=True)
            reg(f"{sx}_ORB30_{vtag}_WIDE",
                _orb30_h(vol, 10.75, 14.0), sx, one_per_day=True)
            reg(f"{sx}_ORB30_GAP_{vtag}_TREND",
                _trend_f(_orb30_h(vol, 10.75, 13.0, gap_dir=True), "with"),
                sx, one_per_day=True)
            reg(f"{sx}_ORB30_{vtag}_TP25",
                _orb30_h(vol, 10.75, 12.5, 1.5, 2.5), sx, one_per_day=True)
            reg(f"{sx}_ORB30_{vtag}_RR18",
                _rr(_orb30_h(vol, 10.75, 12.5), 1.8, 2.5), sx,
                one_per_day=True)

        # Retest / failed ORB / first PB / open drive
        for vol, vtag in ((1.1, "V11"), (1.3, "V13"), (1.5, "V15")):
            reg(f"{sx}_ORB_RT_{vtag}", _orb_retest_h(vol), sx, one_per_day=True)
            reg(f"{sx}_ORB_FAIL_{vtag}", _failed_orb_h(vol), sx, one_per_day=True)
            reg(f"{sx}_ORB_PB_{vtag}", _first_pb_orb_h(vol), sx, one_per_day=True)
            reg(f"{sx}_ORB_RT_{vtag}_MT", _mt(_orb_retest_h(vol)), sx,
                one_per_day=True)
            reg(f"{sx}_ORB_FAIL_{vtag}_MT", _mt(_failed_orb_h(vol)), sx,
                one_per_day=True)
        for vol, vtag in ((1.0, "V10"), (1.2, "V12"), (1.4, "V14")):
            reg(f"{sx}_OPENDRV_{vtag}", _open_drive_h(vol), sx, one_per_day=True)
            reg(f"{sx}_OPENDRV_{vtag}_MT", _mt(_open_drive_h(vol)), sx,
                one_per_day=True)
            reg(f"{sx}_OPENDRV_{vtag}_TP15",
                _cap_tp(_open_drive_h(vol), 1.5), sx, one_per_day=True)
            reg(f"{sx}_OPENDRV_{vtag}_TREND",
                _trend_f(_open_drive_h(vol), "with"), sx, one_per_day=True)

        # ── VWAP_OPEN expansões (PETR GO nesta família) ──
        for vol, vtag in ((1.0, "V10"), (1.1, "V11"), (1.4, "V14"), (1.6, "V16")):
            for hh0, hh1, stag in (
                (10.25, 12.0, "_EARLY"), (10.5, 14.0, "_MID"),
                (11.0, 15.5, "_LATE"), (10.5, 16.0, "_DAY"),
            ):
                reg(f"{sx}_VWAP_OPEN_{vtag}{stag}",
                    _vwap_open_h(vol, hh0, hh1), sx)
                reg(f"{sx}_VWAP_OPEN_{vtag}{stag}_MT",
                    _mt(_vwap_open_h(vol, hh0, hh1)), sx)
                reg(f"{sx}_VWAP_OPEN_{vtag}{stag}_TP15",
                    _cap_tp(_vwap_open_h(vol, hh0, hh1), 1.5), sx)
                reg(f"{sx}_VWAP_OPEN_{vtag}{stag}_TP25",
                    _cap_tp(_vwap_open_h(vol, hh0, hh1), 2.5), sx)
                reg(f"{sx}_VWAP_OPEN_{vtag}{stag}_TREND",
                    _trend_f(_vwap_open_h(vol, hh0, hh1), "with"), sx)
                reg(f"{sx}_VWAP_OPEN_{vtag}{stag}_LOATR",
                    _atr_regime(_vwap_open_h(vol, hh0, hh1), 0.5, 1.2), sx)
        reg(f"{sx}_VWAP_OPEN_AM_H1", _vwap_open_h(1.2, 10.5, 13.0), sx, tf=60)
        reg(f"{sx}_VWAP_OPEN_AM_M30", _vwap_open_h(1.2, 10.5, 13.0), sx, tf=30)
        reg(f"{sx}_VWAP_OPEN_AM_M5", _vwap_open_h(1.2, 10.5, 12.5), sx, tf=5)
        reg(f"{sx}_VWAP_OPEN_TREND_H1",
            _trend_f(_vwap_open_h(1.2, 10.5, 15.0), "with"), sx, tf=60)
        reg(f"{sx}_VWAP_OPEN_AM_RR18",
            _rr(_vwap_open_h(1.3, 10.5, 12.5), 1.8, 2.5), sx)

        # ── OUT expansões (PETR GO em H1) ──
        for tag, h0, h1 in (
            ("AM", 10.0, 12.5), ("MID", 11.5, 14.0), ("PM", 13.0, 16.5),
            ("DAY", 10.0, 17.0), ("LATEAM", 11.0, 13.0),
        ):
            for tf, tlab in ((60, "H1"), (30, "M30"), (15, "")):
                suf = f"_{tlab}" if tlab else ""
                name = f"{sx}_OUT_{tag}{suf}_V5"
                # evita colisão com OUT_AM / OUT_DAY_H1 já existentes
                if name.replace("_V5", "") in known or name in known:
                    name = f"{sx}_OUT_{tag}_V5{suf}"
                fn = _b3_sess(_outside_h(1.2, h0, h1), h0, h1)
                reg(f"{sx}_OUT_{tag}_V5{suf}", fn, sx, tf=tf if tf != 15 else 15)
                reg(f"{sx}_OUT_{tag}_V5_MT{suf}",
                    _b3_sess(_mt(_outside_h(1.2, h0, h1)), h0, h1), sx,
                    tf=tf if tf != 15 else 15)
                reg(f"{sx}_OUT_{tag}_V5_TP15{suf}",
                    _b3_sess(_cap_tp(_outside_h(1.2, h0, h1), 1.5), h0, h1),
                    sx, tf=tf if tf != 15 else 15)
                reg(f"{sx}_OUT_{tag}_V5_TP25{suf}",
                    _b3_sess(_cap_tp(_outside_h(1.2, h0, h1), 2.5), h0, h1),
                    sx, tf=tf if tf != 15 else 15)
                reg(f"{sx}_OUT_{tag}_V5_TREND{suf}",
                    _trend_f(_b3_sess(_outside_h(1.2, h0, h1), h0, h1), "with"),
                    sx, tf=tf if tf != 15 else 15)
                reg(f"{sx}_OUT_{tag}_V5_LOATR{suf}",
                    _atr_regime(
                        _b3_sess(_outside_h(1.2, h0, h1), h0, h1), 0.5, 1.2),
                    sx, tf=tf if tf != 15 else 15)
                reg(f"{sx}_OUT_{tag}_V5_RR18{suf}",
                    _b3_sess(_rr(_outside_h(1.2, h0, h1), 1.8, 2.5), h0, h1),
                    sx, tf=tf if tf != 15 else 15)
        reg(f"{sx}_OUT_GAP_H1_V5",
            _gap_aligned(_b3_sess(_outside_h(1.2, 10, 17), 10, 17), 0.25, True),
            sx, tf=60)
        reg(f"{sx}_OUT_GAP_DAY_V5",
            _gap_aligned(_b3_sess(_outside_h(1.2, 10, 17), 10, 17), 0.25, True),
            sx)

        # ── INS cons-boost (ITUB near) ──
        for vol, vtag in ((1.1, "V11"), (1.2, "V12"), (1.5, "V15")):
            for tag, h0, h1 in (
                ("AM", 10.0, 12.5), ("DAY", 10.0, 17.0),
                ("LATEAM", 11.0, 13.0), ("FULL", 10.0, 16.5),
            ):
                for tf, tlab in ((60, "H1"), (30, "M30"), (15, "M15")):
                    reg(f"{sx}_INS_{tag}_{vtag}_{tlab}",
                        _b3_sess(_inside_h(vol, h0, h1), h0, h1), sx, tf=tf)
                    reg(f"{sx}_INS_{tag}_{vtag}_MT_{tlab}",
                        _b3_sess(_mt(_inside_h(vol, h0, h1)), h0, h1), sx, tf=tf)
                    reg(f"{sx}_INS_{tag}_{vtag}_TP15_{tlab}",
                        _b3_sess(_cap_tp(_inside_h(vol, h0, h1), 1.5), h0, h1),
                        sx, tf=tf)
                    reg(f"{sx}_INS_{tag}_{vtag}_TREND_{tlab}",
                        _trend_f(
                            _b3_sess(_inside_h(vol, h0, h1), h0, h1), "with"),
                        sx, tf=tf)
                    reg(f"{sx}_INS_{tag}_{vtag}_LOATR_{tlab}",
                        _atr_regime(
                            _b3_sess(_inside_h(vol, h0, h1), h0, h1), 0.5, 1.15),
                        sx, tf=tf)
                    reg(f"{sx}_INS_{tag}_{vtag}_RR18_{tlab}",
                        _b3_sess(_rr(_inside_h(vol, h0, h1), 1.8, 2.5), h0, h1),
                        sx, tf=tf)

        # ── NR soft (ITUB NR5_AM_H1 amostra) ──
        for n in (3, 4, 5, 6, 7):
            for vol in (1.1, 1.3):
                vtag = f"V{int(vol * 10)}"
                for tag, h0, h1 in (
                    ("AM", 10.0, 12.5), ("DAY", 10.0, 17.0),
                    ("LATEAM", 11.0, 13.0),
                ):
                    for tf, tlab in ((60, "H1"), (30, "M30"), (15, "M15")):
                        reg(f"{sx}_NR{n}_{tag}_{vtag}_{tlab}",
                            _b3_sess(_nr_h(n, vol, h0, h1), h0, h1), sx, tf=tf)
                        reg(f"{sx}_NR{n}_{tag}_{vtag}_MT_{tlab}",
                            _b3_sess(_mt(_nr_h(n, vol, h0, h1)), h0, h1),
                            sx, tf=tf)
                        reg(f"{sx}_NR{n}_{tag}_{vtag}_TP15_{tlab}",
                            _b3_sess(_cap_tp(_nr_h(n, vol, h0, h1), 1.5), h0, h1),
                            sx, tf=tf)
                        reg(f"{sx}_NR{n}_{tag}_{vtag}_TREND_{tlab}",
                            _trend_f(
                                _b3_sess(_nr_h(n, vol, h0, h1), h0, h1), "with"),
                            sx, tf=tf)

        # ── VOLSPIKE (BBDC near) ──
        for thr, ttag in ((1.6, "T16"), (1.8, "T18"), (2.0, "T20"),
                          (2.2, "T22"), (2.5, "T25")):
            for tag, h0, h1 in (
                ("AM", 10.0, 12.5), ("OPEN1H", 10.0, 11.0),
                ("LATEAM", 11.0, 13.0), ("DAY", 10.0, 17.0), ("PM", 13.0, 16.5),
            ):
                fn = _volspike_h(thr, 1.5, h0, h1)
                reg(f"{sx}_VSPIKE_{ttag}_{tag}",
                    _b3_sess(fn, h0, h1), sx)
                reg(f"{sx}_VSPIKE_{ttag}_{tag}_MT",
                    _b3_sess(_mt(fn), h0, h1), sx)
                reg(f"{sx}_VSPIKE_{ttag}_{tag}_TP15",
                    _b3_sess(_cap_tp(fn, 1.5), h0, h1), sx)
                reg(f"{sx}_VSPIKE_{ttag}_{tag}_TP25",
                    _b3_sess(_cap_tp(fn, 2.5), h0, h1), sx)
                reg(f"{sx}_VSPIKE_{ttag}_{tag}_TREND",
                    _trend_f(_b3_sess(fn, h0, h1), "with"), sx)
                reg(f"{sx}_VSPIKE_{ttag}_{tag}_LOATR",
                    _atr_regime(_b3_sess(fn, h0, h1), 0.5, 1.2), sx)
                reg(f"{sx}_VSPIKE_{ttag}_{tag}_RR18",
                    _b3_sess(_rr(fn, 1.8, 2.5), h0, h1), sx)
                reg(f"{sx}_VSPIKE_{ttag}_{tag}_M5",
                    _b3_sess(fn, h0, h1), sx, tf=5)

        # ── IMP soft (ABEV near amostra) ──
        for body, btag in ((1.4, "B14"), (1.6, "B16"), (1.8, "B18"), (2.2, "B22")):
            for tag, h0, h1 in (
                ("AM", 10.0, 12.5), ("DAY", 10.0, 17.0),
                ("LATEAM", 11.0, 13.0), ("PM", 13.0, 16.5),
            ):
                fn = _impulse_h(body, 1.4, h0, h1, 2.0)
                reg(f"{sx}_IMP_{tag}_{btag}",
                    _b3_sess(fn, h0, h1), sx)
                reg(f"{sx}_IMP_{tag}_{btag}_MT",
                    _b3_sess(_mt(_impulse_h(body, 1.4, h0, h1, 2.0)), h0, h1),
                    sx)
                reg(f"{sx}_IMP_{tag}_{btag}_TP15",
                    _b3_sess(_cap_tp(fn, 1.5), h0, h1), sx)
                reg(f"{sx}_IMP_{tag}_{btag}_TP20",
                    _b3_sess(_cap_tp(fn, 2.0), h0, h1), sx)
                reg(f"{sx}_IMP_{tag}_{btag}_TREND",
                    _trend_f(_b3_sess(fn, h0, h1), "with"), sx)
                reg(f"{sx}_IMP_{tag}_{btag}_LOATR",
                    _atr_regime(_b3_sess(fn, h0, h1), 0.5, 1.2), sx)
                reg(f"{sx}_IMP_{tag}_{btag}_H1",
                    _b3_sess(fn, h0, h1), sx, tf=60)
                reg(f"{sx}_IMP_{tag}_{btag}_M5",
                    _b3_sess(fn, h0, h1), sx, tf=5)
                reg(f"{sx}_IMP_{tag}_{btag}_M30",
                    _b3_sess(fn, h0, h1), sx, tf=30)

        # ── Sessões novas: INS/HL/ENG/EMA/SQZ/PDH/VWAP ──
        for tag, h0, h1 in NEW_SESS:
            for fam, fn0 in (
                ("INS", _inside_h(1.25, h0, h1)),
                ("HL", _hl_h(1.25, h0, h1)),
                ("ENG", _engulf_h(1.2, h0, h1)),
                ("EMA", _ema_reclaim_h(h0, h1, 1.15)),
                ("SQZ", _sqz_break_h(1.3, h0, h1)),
                ("PDH", _pdh_stock_h(1.15, h0, h1)),
                ("VWAPC", _vwap_cont_h(1.15, 0.25, h0, h1)),
                ("VWAPF", _vwap_fade_h(1.15, 1.0, h0, h1)),
                ("DBL", _dbl_inside_h(1.2, h0, h1)),
            ):
                opd = fam == "PDH"
                reg(f"{sx}_{fam}_{tag}_V5",
                    _b3_sess(fn0, h0, h1), sx, one_per_day=opd)
                reg(f"{sx}_{fam}_{tag}_V5_MT",
                    _b3_sess(_mt(fn0), h0, h1), sx, one_per_day=opd)
                reg(f"{sx}_{fam}_{tag}_V5_TP15",
                    _b3_sess(_cap_tp(fn0, 1.5), h0, h1), sx, one_per_day=opd)
                reg(f"{sx}_{fam}_{tag}_V5_TREND",
                    _trend_f(_b3_sess(fn0, h0, h1), "with"), sx,
                    one_per_day=opd)
                reg(f"{sx}_{fam}_{tag}_V5_H1",
                    _b3_sess(fn0, h0, h1), sx, tf=60, one_per_day=opd)

        # PDM / Lunch fade / H4 PB filtrado
        for vol, vtag in ((1.1, "V11"), (1.3, "V13"), (1.5, "V15")):
            reg(f"{sx}_PDM_{vtag}", _pdm_h(vol), sx, one_per_day=True)
            reg(f"{sx}_PDM_{vtag}_MT", _mt(_pdm_h(vol)), sx, one_per_day=True)
            reg(f"{sx}_PDM_{vtag}_AM", _pdm_h(vol, 10.0, 12.5), sx,
                one_per_day=True)
            reg(f"{sx}_PDM_{vtag}_TREND",
                _trend_f(_pdm_h(vol), "with"), sx, one_per_day=True)
            reg(f"{sx}_PDM_{vtag}_H1", _pdm_h(vol), sx, tf=60, one_per_day=True)
        for dist, dtag in ((0.6, "D06"), (0.8, "D08"), (1.0, "D10"),
                           (1.2, "D12")):
            reg(f"{sx}_LUNCH_{dtag}", _lunch_fade_h(1.1, 12.0, 14.0, dist), sx)
            reg(f"{sx}_LUNCH_{dtag}_MT",
                _mt(_lunch_fade_h(1.1, 12.0, 14.0, dist)), sx)
            reg(f"{sx}_LUNCH_{dtag}_TP15",
                _cap_tp(_lunch_fade_h(1.1, 12.0, 14.0, dist), 1.5), sx)

        # ENG / EMA H1 refinamentos (PETR ENG_DAY_H1 near)
        for tf, tlab in ((60, "H1"), (30, "M30")):
            for fam, fn0 in (
                ("ENG", _engulf_h(1.15, 10, 17)),
                ("EMA", _ema_reclaim_h(10, 17, 1.1)),
                ("HL", _hl_h(1.2, 10, 17)),
                ("SQZ", _sqz_break_h(1.25, 10, 17)),
            ):
                reg(f"{sx}_{fam}_DAY_V5_{tlab}",
                    _b3_sess(fn0, 10, 17), sx, tf=tf)
                reg(f"{sx}_{fam}_DAY_V5_MT_{tlab}",
                    _b3_sess(_mt(fn0), 10, 17), sx, tf=tf)
                reg(f"{sx}_{fam}_DAY_V5_TP15_{tlab}",
                    _b3_sess(_cap_tp(fn0, 1.5), 10, 17), sx, tf=tf)
                reg(f"{sx}_{fam}_DAY_V5_TREND_{tlab}",
                    _trend_f(_b3_sess(fn0, 10, 17), "with"), sx, tf=tf)
                reg(f"{sx}_{fam}_DAY_V5_LOATR_{tlab}",
                    _atr_regime(_b3_sess(fn0, 10, 17), 0.5, 1.15), sx, tf=tf)
                reg(f"{sx}_{fam}_AM_V5_{tlab}",
                    _b3_sess(
                        type(fn0) if False else (
                            _engulf_h(1.15, 10, 12.5) if fam == "ENG"
                            else _ema_reclaim_h(10, 12.5, 1.1) if fam == "EMA"
                            else _hl_h(1.2, 10, 12.5) if fam == "HL"
                            else _sqz_break_h(1.25, 10, 12.5)
                        ), 10, 12.5),
                    sx, tf=tf)

        # Gap-aligned novas combinações
        for fam, fn0 in (
            ("OUT", _outside_h(1.2, 10, 17)),
            ("ENG", _engulf_h(1.2, 10, 17)),
            ("IMP", _impulse_h(1.6, 1.4, 10, 17, 2.0)),
            ("VSPIKE", _volspike_h(1.8, 1.5, 10, 17)),
            ("ORB_RT", _orb_retest_h(1.2)),
            ("OPENDRV", _open_drive_h(1.15)),
        ):
            reg(f"{sx}_{fam}_GAP_V5",
                _gap_aligned(_b3_sess(fn0, 10, 17) if fam not in (
                    "ORB_RT", "OPENDRV") else fn0, 0.25, True),
                sx, one_per_day=True)
            reg(f"{sx}_{fam}_GAP_V5_H1",
                _gap_aligned(_b3_sess(fn0, 10, 17) if fam not in (
                    "ORB_RT", "OPENDRV") else fn0, 0.25, True),
                sx, tf=60, one_per_day=True)

        # H4 PB filtrado
        reg(f"{sx}_H4_PB_TREND",
            _trend_f(_b3_sess(_h4_pb_ema_h(10.0, 17.0), 10, 17), "with"), sx)
        reg(f"{sx}_H4_PB_LOATR",
            _atr_regime(_b3_sess(_h4_pb_ema_h(10.0, 17.0), 10, 17), 0.5, 1.15),
            sx)
        reg(f"{sx}_H4_PB_AM_TP15",
            _b3_sess(_cap_tp(_h4_pb_ema_h(10.0, 12.5), 1.5), 10, 12.5), sx)

        # ORB multi-TF novos
        for tf, tlab in ((5, "M5"), (30, "M30"), (60, "H1")):
            reg(f"{sx}_ORB15_V12_{tlab}",
                _orb_range_h("orb15_hi", "orb15_lo", 1.2, 10.25, 13.0),
                sx, tf=tf, one_per_day=True)
            reg(f"{sx}_ORB60_V12_{tlab}",
                _orb_range_h("orb60_hi", "orb60_lo", 1.2, 11.0, 14.0),
                sx, tf=tf, one_per_day=True)
            reg(f"{sx}_ORB_RT_V13_{tlab}",
                _orb_retest_h(1.3), sx, tf=tf, one_per_day=True)
            reg(f"{sx}_ORB_FAIL_V13_{tlab}",
                _failed_orb_h(1.3), sx, tf=tf, one_per_day=True)
            reg(f"{sx}_OPENDRV_V12_{tlab}",
                _open_drive_h(1.2), sx, tf=tf, one_per_day=True)

    return len(STRATS)


# ── Workers ─────────────────────────────────────────────────────────────────

def _worker_init(dfs_by_key, want_syms):
    global _WORKER_DFS
    _WORKER_DFS = dfs_by_key
    _build_registry(list(want_syms))
    v3.ONE_PER_DAY.clear()
    v3.ONE_PER_DAY.update(ONE_PER_DAY)
    v3.STRATS.clear()
    v3.STRATS.update(STRATS)
    v3.STRAT_SYM.clear()
    v3.STRAT_SYM.update(STRAT_SYM)
    v3.STRAT_TF.clear()
    v3.STRAT_TF.update(STRAT_TF)


def _run_one(name: str) -> dict:
    try:
        if name not in STRATS:
            return {"setup": name, "sym": "?", "tf": 15, "n": 0,
                    "veredito": "SEM REGISTRO"}
        sym = STRAT_SYM[name]
        tf = STRAT_TF.get(name, 15)
        key = f"{sym}_M{tf}"
        df = _WORKER_DFS.get(key)
        if df is None:
            return {"setup": name, "sym": sym, "tf": tf, "n": 0,
                    "veredito": "SEM DADOS"}
        fn = STRATS[name]
        v3.ONE_PER_DAY.clear()
        v3.ONE_PER_DAY.update(ONE_PER_DAY)
        t0 = time.time()
        t = _simulate_stock(df, sym, fn, name, tf=tf)
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
    ap.add_argument("--bars", type=int, default=100000)
    ap.add_argument("--syms", default=",".join(FOCUS_DEFAULT))
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--only", default="")
    ap.add_argument("--list-only", action="store_true")
    ap.add_argument("--include-known", action="store_true")
    args = ap.parse_args()

    want = [s.strip().upper() for s in args.syms.split(",") if s.strip()]
    n_reg = _build_registry(want, skip_known=not args.include_known)
    only = {x.strip() for x in args.only.split(",") if x.strip()}
    names = [n for n in STRATS if STRAT_SYM[n] in want and (not only or n in only)]

    if args.list_only:
        from collections import Counter
        print(f"TOTAL {len(names)}")
        print(dict(Counter(STRAT_SYM[n] for n in names)))
        print(dict(Counter(STRAT_TF.get(n, 15) for n in names)))
        return

    os.makedirs("logs", exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    log_path = f"logs/backtest_acoes_v5_mega_{stamp}.txt"
    logf = open(log_path, "w", encoding="utf-8")

    def out(s):
        try:
            print(s)
        except UnicodeEncodeError:
            print(s.encode("ascii", "replace").decode("ascii"))
        logf.write(s + "\n")
        logf.flush()

    from collections import Counter
    out(f"{'#' * 76}")
    out(f"# BACKTEST AÇÕES B3 V5 MEGA — {len(names)} setups NOVOS · "
        f"{datetime.now():%d/%m/%Y %H:%M}")
    out(f"# Foco fracos: PETR4/ITUB4/ABEV3/BBDC4 — 0 overlap v3+v4")
    out(f"# Papers: {want}")
    out(f"# Por ativo: {dict(Counter(STRAT_SYM[n] for n in names))}")
    out(f"# TFs: {dict(Counter(STRAT_TF.get(n, 15) for n in names))}")
    out(f"# GO: net≥+0.10R · PF≥1.25 · cons≥55% · OOS · n≥40")
    out(f"# Custos: FEE_PCT_RT={FEE_PCT_RT} + tick · EOD 16:54 · SEM FDS")
    out(f"# workers={args.workers} · NÃO altera live até GO")
    out(f"{'#' * 76}")

    need_keys = sorted({f"{STRAT_SYM[n]}_M{STRAT_TF.get(n, 15)}" for n in names})
    dfs = {}
    for key in need_keys:
        sym, _, tf_s = key.partition("_M")
        tf = int(tf_s)
        t0 = time.time()
        raw = _fetch_stock(sym, tf, args.bars)
        if raw is None or len(raw) < 800:
            out(f"\n!! {key}: sem histórico")
            continue
        df = _enrich_v5(add_extra_v2(add_indicators(raw), sym, ctx=None))
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
        _worker_init(dfs, want)
        for i, name in enumerate(names, 1):
            row = _run_one(name)
            tr = row.pop("_trades", None)
            ranking.append(row)
            mark = "*" if "🟢" in str(row.get("veredito", "")) else " "
            out(f"  {mark}[{i}/{len(names)}] {row['setup']:<44} "
                f"n={row.get('n', 0):<5} net={row.get('net', '—')} "
                f"PF={row.get('PF', '—')} cons={row.get('cons%', '—')} "
                f"OOS={row.get('oos_net', '—')}  {row.get('veredito', '')}")
            if tr is not None:
                goes.append(row["setup"])
                tr.to_csv(
                    f"logs/acoes_v5_{row['sym']}_M{row['tf']}_{row['setup']}.csv",
                    index=False)
    else:
        with ProcessPoolExecutor(
            max_workers=workers, initializer=_worker_init, initargs=(dfs, want),
        ) as ex:
            futs = {ex.submit(_run_one, n): n for n in names}
            done = 0
            for fut in as_completed(futs):
                done += 1
                row = fut.result()
                tr = row.pop("_trades", None)
                ranking.append(row)
                mark = "*" if "🟢" in str(row.get("veredito", "")) else " "
                out(f"  {mark}[{done}/{len(names)}] {row['setup']:<44} "
                    f"n={row.get('n', 0):<5} net={row.get('net', '—')} "
                    f"PF={row.get('PF', '—')} cons={row.get('cons%', '—')} "
                    f"OOS={row.get('oos_net', '—')}  {row.get('veredito', '')}")
                if tr is not None:
                    goes.append(row["setup"])
                    tr.to_csv(
                        f"logs/acoes_v5_{row['sym']}_M{row['tf']}_{row['setup']}.csv",
                        index=False)

    rk = pd.DataFrame(ranking)
    rk_path = f"logs/backtest_acoes_v5_mega_ranking_{stamp}.csv"
    if not rk.empty:
        rk2 = rk.copy()
        if "err" in rk2.columns:
            rk2 = rk2.drop(columns=["err"])
        rk2.sort_values(
            ["veredito", "net"], ascending=[True, False], kind="mergesort"
        ).to_csv(rk_path, index=False)

    out(f"\n\n{'#' * 76}\n  RANKING GO\n{'#' * 76}")
    go_rows = [r for r in ranking if "🟢" in str(r.get("veredito", ""))]
    yel_rows = [r for r in ranking
                if "🟡" in str(r.get("veredito", ""))
                or "PROMISSOR" in str(r.get("veredito", ""))]
    if not go_rows:
        out("  (nenhum GO)")
    for r in sorted(go_rows, key=lambda x: -(x.get("net") or -9)):
        out(f"  GO {r['sym']:<7} M{r['tf']:<3} {r['setup']:<44} "
            f"n={r.get('n')} net {r.get('net'):+.3f} PF {r.get('PF')} "
            f"cons {r.get('cons%')}% OOS {r.get('oos_net')}")

    out(f"\n{'#' * 76}\n  TOP melhores net (n>=40)\n{'#' * 76}")
    top = [r for r in ranking if (r.get("n") or 0) >= 40]
    top = sorted(top, key=lambda x: -(x.get("net") or -9))[:60]
    for r in top:
        out(f"  {str(r.get('veredito', '?'))[:14]:<14} {r['sym']:<7} M{r['tf']:<3} "
            f"{r['setup']:<44} n={r.get('n')} net {r.get('net')} "
            f"PF {r.get('PF')} cons {r.get('cons%')}% OOS {r.get('oos_net')}")

    out(f"\n{'#' * 76}\n  RESUMO POR ATIVO\n{'#' * 76}")
    for sx in want:
        sub = [r for r in ranking if r.get("sym") == sx]
        ng = sum(1 for r in sub if "🟢" in str(r.get("veredito", "")))
        ny = sum(1 for r in sub if "🟡" in str(r.get("veredito", ""))
                 or "PROMISSOR" in str(r.get("veredito", "")))
        out(f"  {sx}: testados={len(sub)}  GO={ng}  yellow={ny}")

    out(f"\n# Veredito: {'HA GO -> wire + tela' if go_rows else 'ZERO GO nesta rodada'}")
    out(f"# GOs: {goes}")
    out(f"# yellow count: {len(yel_rows)}")
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
