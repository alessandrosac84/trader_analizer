"""
backtest_acoes_b3_v3_mega.py — MEGA bateria AÇÕES B3 blue chips.

Famílias (pregão 10–17 BRT, sem FDS):
  ORB · GAP fade/cont · PDH/HL · NR/Inside · Impulse · VWAP · Engolfo ·
  EMA reclaim · Squeeze · Outside · sessões AM/MID/PM/POWER · TFs M5/M15/M30/H1

Régua GO (não baixar):
  net ≥ +0,10 R · PF ≥ 1,25 · cons ≥ 55% · OOS+ · n ≥ 40

Custos: % B3 (FEE_PCT_RT) + tick — NÃO usa spread CFD de crypto.
Simulador: EOD no pregão (isola de WIN/WDO magics).

Uso:
  python backtest_acoes_b3_v3_mega.py
  python backtest_acoes_b3_v3_mega.py --workers 6 --bars 80000
  python backtest_acoes_b3_v3_mega.py --syms PETR4,VALE3 --workers 4
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
from backtest_setups_novos_v15_btc_eth import _vwap_fade_h, _vwap_cont_h, _cap_tp, _rr

# ── Universo ────────────────────────────────────────────────────────────────
SYMBOLS = ["PETR4", "VALE3", "ITUB4", "BBDC4", "BBAS3", "ABEV3", "WEGE3"]
TICK = 0.01
FEE_PCT_RT = 0.00055          # corretagem+emolumentos approx round-trip
GAP_MIN_PCT = 0.35

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


def stock_cost_pts(price, stopped=True):
    cost = float(price) * FEE_PCT_RT + TICK
    if stopped:
        cost += TICK
    return cost


def _b3_sess(fn, hh0=10.0, hh1=17.0):
    """Só pregão B3 (seg–sex implícito nos dados) + janela horária."""
    def _f(df, i, sym):
        r = df.iloc[i]
        if int(getattr(r, "dow", 0) or 0) >= 5:
            return None
        if not _hh_ok(r.hh, hh0, hh1):
            return None
        return fn(df, i, sym)
    return _f


def _gap_aligned(fn, min_pct=GAP_MIN_PCT, same_dir=True):
    """Exige gap mínimo; same_dir=True → só opera a favor do gap."""
    def _f(df, i, sym):
        r = df.iloc[i]
        g = float(r.gap_pct) if not pd.isna(r.gap_pct) else 0.0
        if abs(g) < min_pct:
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


# ── Setups específicos de ação ──────────────────────────────────────────────

def _orb30_h(vol=1.2, hh0=10.75, hh1=12.5, rr1=1.5, rr2=2.0, gap_dir=False):
    """Rompe ORB 10:00–10:45 com volume; opcional alinhado ao gap."""
    def _f(df, i, sym):
        if i < 5:
            return None
        r, p = df.iloc[i], df.iloc[i - 1]
        if not _hh_ok(r.hh, hh0, hh1):
            return None
        if pd.isna(r.orb_hi) or pd.isna(r.orb_lo) or float(r.atr or 0) <= 0:
            return None
        if (float(r.orb_hi) - float(r.orb_lo)) < 0.4 * float(r.atr):
            return None
        if float(r.Volume or 0) < vol * float(r.vol8 or 0):
            return None
        g = float(r.gap_pct) if not pd.isna(r.gap_pct) else 0.0
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


def _gap_fade_h(min_pct=0.40, hh0=10.0, hh1=11.5, vol=1.1):
    """Fade do gap na 1ª/2ª hora se rejeição + vol."""
    def _f(df, i, sym):
        if i < 3:
            return None
        r, p = df.iloc[i], df.iloc[i - 1]
        if not _hh_ok(r.hh, hh0, hh1):
            return None
        g = float(r.gap_pct) if not pd.isna(r.gap_pct) else 0.0
        if abs(g) < min_pct or float(r.atr or 0) <= 0:
            return None
        if float(r.Volume or 0) < vol * float(r.vol8 or 0):
            return None
        # gap up → fade venda se candle vermelho abaixo da máx
        if g >= min_pct and float(r.Close) < float(r.Open) and float(r.Close) < float(p.Close):
            d, sl = "VENDA", float(max(p.High, r.High, r.day_open))
        elif g <= -min_pct and float(r.Close) > float(r.Open) and float(r.Close) > float(p.Close):
            d, sl = "COMPRA", float(min(p.Low, r.Low, r.day_open))
        else:
            return None
        return _sig(d, float(r.Close), sl, 1.5, 2.0, True)
    return _f


def _gap_cont_h(min_pct=0.40, hh0=10.0, hh1=12.0, vol=1.2):
    """Continuação a favor do gap com rompimento da 1ª hora."""
    def _f(df, i, sym):
        if i < 3:
            return None
        r, p = df.iloc[i], df.iloc[i - 1]
        if not _hh_ok(r.hh, hh0, hh1):
            return None
        g = float(r.gap_pct) if not pd.isna(r.gap_pct) else 0.0
        if abs(g) < min_pct or float(r.atr or 0) <= 0:
            return None
        if float(r.Volume or 0) < vol * float(r.vol8 or 0):
            return None
        if g >= min_pct and float(r.Close) > float(p.High) and float(r.Close) > float(r.day_open):
            d, sl = "COMPRA", float(min(p.Low, r.day_open))
        elif g <= -min_pct and float(r.Close) < float(p.Low) and float(r.Close) < float(r.day_open):
            d, sl = "VENDA", float(max(p.High, r.day_open))
        else:
            return None
        if not _h4_ok(r, d):
            return None
        return _sig(d, float(r.Close), sl, 1.5, 2.5, True)
    return _f


def _pdh_stock_h(vol=1.2, hh0=10.0, hh1=17.0, rr1=1.5, rr2=2.0):
    """Rompe máxima/mínima do dia anterior (PDH/PDL reais de ação)."""
    def _f(df, i, sym):
        if i < 30:
            return None
        r, p = df.iloc[i], df.iloc[i - 1]
        if not _hh_ok(r.hh, hh0, hh1):
            return None
        if pd.isna(r.prev_day_hi) or pd.isna(r.prev_day_lo):
            return None
        if float(r.Volume or 0) < vol * float(r.vol8 or 0):
            return None
        pdh, pdl = float(r.prev_day_hi), float(r.prev_day_lo)
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
    """Reclaim VWAP intradía com viés do day_open."""
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
        # acima do open + reclaim VWAP de baixo → compra
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


# ── Registry builder ────────────────────────────────────────────────────────

# DAY completo + AM/PM leves → ~50–55 variantes/paper ≈ 350–400 total
SESSIONS_FULL = [("DAY", 10.0, 17.0)]
SESSIONS_LIGHT = [("AM", 10.0, 12.5), ("PM", 13.0, 16.5), ("POWER", 14.5, 17.0)]


def _build_registry(syms: list[str]):
    STRATS.clear()
    STRAT_SYM.clear()
    STRAT_TF.clear()
    ONE_PER_DAY.clear()

    for sx in syms:
        tag_sym = sx

        # ── DAY: cobertura completa das famílias ──
        for tag, h0, h1 in SESSIONS_FULL:
            for vol, vtag in ((1.2, ""), (1.4, "V14")):
                name = f"{tag_sym}_INS_{tag}{('_' + vtag) if vtag else ''}"
                _reg(name, _b3_sess(_inside_h(vol, h0, h1), h0, h1), sx)
            for n in (4, 5, 7):
                _reg(f"{tag_sym}_NR{n}_{tag}",
                     _b3_sess(_nr_h(n, 1.3, h0, h1), h0, h1), sx)
                _reg(f"{tag_sym}_NR{n}_{tag}_MT",
                     _b3_sess(_mt(_nr_h(n, 1.3, h0, h1)), h0, h1), sx)
            _reg(f"{tag_sym}_HL_{tag}",
                 _b3_sess(_hl_h(1.3, h0, h1), h0, h1), sx)
            _reg(f"{tag_sym}_HL_{tag}_MT",
                 _b3_sess(_mt(_hl_h(1.4, h0, h1)), h0, h1), sx)
            _reg(f"{tag_sym}_IMP_{tag}",
                 _b3_sess(_impulse_h(2.0, 1.5, h0, h1, 2.0), h0, h1), sx)
            _reg(f"{tag_sym}_IMP_{tag}_MT",
                 _b3_sess(_mt(_impulse_h(1.8, 1.6, h0, h1, 2.0)), h0, h1), sx)
            _reg(f"{tag_sym}_OUT_{tag}",
                 _b3_sess(_outside_h(1.3, h0, h1), h0, h1), sx)
            _reg(f"{tag_sym}_ENG_{tag}",
                 _b3_sess(_engulf_h(1.3, h0, h1), h0, h1), sx)
            _reg(f"{tag_sym}_EMA_{tag}",
                 _b3_sess(_ema_reclaim_h(h0, h1, 1.2), h0, h1), sx)
            _reg(f"{tag_sym}_SQZ_{tag}",
                 _b3_sess(_sqz_break_h(1.4, h0, h1), h0, h1), sx)
            _reg(f"{tag_sym}_PDH_{tag}",
                 _b3_sess(_pdh_stock_h(1.2, h0, h1), h0, h1), sx, one_per_day=True)
            _reg(f"{tag_sym}_PDH_{tag}_MT",
                 _b3_sess(_mt(_pdh_stock_h(1.3, h0, h1)), h0, h1), sx,
                 one_per_day=True)
            _reg(f"{tag_sym}_VWAPF_{tag}",
                 _b3_sess(_vwap_fade_h(1.2, 1.1, h0, h1), h0, h1), sx)
            _reg(f"{tag_sym}_VWAPC_{tag}",
                 _b3_sess(_vwap_cont_h(1.2, 0.3, h0, h1), h0, h1), sx)

        # ── AM / PM / POWER: subset (INS/NR5/HL/IMP/PDH/ENG) ──
        for tag, h0, h1 in SESSIONS_LIGHT:
            _reg(f"{tag_sym}_INS_{tag}",
                 _b3_sess(_inside_h(1.3, h0, h1), h0, h1), sx)
            _reg(f"{tag_sym}_NR5_{tag}",
                 _b3_sess(_nr_h(5, 1.3, h0, h1), h0, h1), sx)
            _reg(f"{tag_sym}_HL_{tag}",
                 _b3_sess(_hl_h(1.3, h0, h1), h0, h1), sx)
            _reg(f"{tag_sym}_IMP_{tag}",
                 _b3_sess(_impulse_h(2.0, 1.5, h0, h1, 2.0), h0, h1), sx)
            _reg(f"{tag_sym}_PDH_{tag}",
                 _b3_sess(_pdh_stock_h(1.2, h0, h1), h0, h1), sx, one_per_day=True)
            _reg(f"{tag_sym}_ENG_{tag}",
                 _b3_sess(_engulf_h(1.3, h0, h1), h0, h1), sx)
            _reg(f"{tag_sym}_VWAPC_{tag}",
                 _b3_sess(_vwap_cont_h(1.2, 0.3, h0, h1), h0, h1), sx)

        # ORB / GAP
        for vol, vtag in ((1.1, "V11"), (1.3, "V13"), (1.5, "V15")):
            _reg(f"{tag_sym}_ORB30_{vtag}",
                 _orb30_h(vol, 10.75, 12.5), sx, one_per_day=True)
            _reg(f"{tag_sym}_ORB30_GAP_{vtag}",
                 _orb30_h(vol, 10.75, 12.5, gap_dir=True), sx, one_per_day=True)

        for gp, gtag in ((0.30, "G30"), (0.45, "G45"), (0.60, "G60")):
            _reg(f"{tag_sym}_GAPF_{gtag}",
                 _gap_fade_h(gp, 10.0, 11.5), sx, one_per_day=True)
            _reg(f"{tag_sym}_GAPC_{gtag}",
                 _gap_cont_h(gp, 10.0, 12.0), sx, one_per_day=True)
            _reg(f"{tag_sym}_GAPC_{gtag}_MT",
                 _mt(_gap_cont_h(gp, 10.0, 12.5)), sx, one_per_day=True)

        _reg(f"{tag_sym}_VWAP_OPEN", _vwap_open_h(1.2, 10.5, 15.0), sx)
        _reg(f"{tag_sym}_VWAP_OPEN_MT", _mt(_vwap_open_h(1.3, 10.5, 15.0)), sx)
        _reg(f"{tag_sym}_DBL_INS_DAY",
             _b3_sess(_dbl_inside_h(1.3, 10.0, 17.0), 10, 17), sx)
        _reg(f"{tag_sym}_VOLSPIKE_AM",
             _b3_sess(_volspike_h(2.0, 1.5, 10.0, 12.5), 10, 12.5), sx)
        _reg(f"{tag_sym}_H4_PB_DAY",
             _b3_sess(_h4_pb_ema_h(10.0, 17.0), 10, 17), sx)
        _reg(f"{tag_sym}_H4_PB_AM",
             _b3_sess(_h4_pb_ema_h(10.0, 12.5), 10, 12.5), sx)

        for base_name, base_fn, opd in (
            (f"{tag_sym}_INS_DAY_TP20", _cap_tp(_inside_h(1.3, 10, 17), 2.0), False),
            (f"{tag_sym}_NR5_DAY_TP20", _cap_tp(_nr_h(5, 1.3, 10, 17), 2.0), False),
            (f"{tag_sym}_PDH_DAY_TP20", _cap_tp(_pdh_stock_h(1.2, 10, 17), 2.0), True),
            (f"{tag_sym}_HL_DAY_TP20", _cap_tp(_hl_h(1.3, 10, 17), 2.0), False),
            (f"{tag_sym}_IMP_DAY_TP25", _impulse_h(2.0, 1.5, 10, 17, 2.5), False),
            (f"{tag_sym}_ORB30_TP15", _orb30_h(1.3, 10.75, 12.5, 1.5, 1.5), True),
            (f"{tag_sym}_INS_DAY_RR20", _rr(_inside_h(1.3, 10, 17), 2.0, 3.0), False),
        ):
            _reg(base_name, _b3_sess(base_fn, 10, 17), sx, one_per_day=opd)

        _reg(f"{tag_sym}_INS_GAP_DAY",
             _gap_aligned(_b3_sess(_inside_h(1.3, 10, 17), 10, 17), 0.30, True), sx)
        _reg(f"{tag_sym}_NR5_GAP_DAY",
             _gap_aligned(_b3_sess(_nr_h(5, 1.3, 10, 17), 10, 17), 0.30, True), sx)
        _reg(f"{tag_sym}_HL_GAP_DAY",
             _gap_aligned(_b3_sess(_hl_h(1.3, 10, 17), 10, 17), 0.30, True), sx)

        # Multi-TF: M30 + H1 (core); M5 só INS/NR5/IMP
        for tf, tf_lab in ((30, "M30"), (60, "H1")):
            _reg(f"{tag_sym}_INS_DAY_{tf_lab}",
                 _b3_sess(_inside_h(1.3, 10, 17), 10, 17), sx, tf=tf)
            _reg(f"{tag_sym}_NR5_DAY_{tf_lab}",
                 _b3_sess(_nr_h(5, 1.3, 10, 17), 10, 17), sx, tf=tf)
            _reg(f"{tag_sym}_HL_DAY_{tf_lab}",
                 _b3_sess(_hl_h(1.3, 10, 17), 10, 17), sx, tf=tf)
            _reg(f"{tag_sym}_IMP_DAY_{tf_lab}",
                 _b3_sess(_impulse_h(2.0, 1.5, 10, 17, 2.0), 10, 17), sx, tf=tf)
            _reg(f"{tag_sym}_PDH_DAY_{tf_lab}",
                 _b3_sess(_pdh_stock_h(1.2, 10, 17), 10, 17), sx, tf=tf,
                 one_per_day=True)
            _reg(f"{tag_sym}_EMA_DAY_{tf_lab}",
                 _b3_sess(_ema_reclaim_h(10, 17, 1.2), 10, 17), sx, tf=tf)
            _reg(f"{tag_sym}_ORB30_{tf_lab}",
                 _orb30_h(1.3, 10.75, 13.0), sx, tf=tf, one_per_day=True)
            _reg(f"{tag_sym}_GAPC_G45_{tf_lab}",
                 _gap_cont_h(0.45, 10.0, 13.0), sx, tf=tf, one_per_day=True)

        for fam, fn in (
            ("INS", _inside_h(1.3, 10, 17)),
            ("NR5", _nr_h(5, 1.3, 10, 17)),
            ("IMP", _impulse_h(2.0, 1.5, 10, 17, 2.0)),
        ):
            _reg(f"{tag_sym}_{fam}_DAY_M5",
                 _b3_sess(fn, 10, 17), sx, tf=5)

    return len(STRATS)


# ── Simulador ações (EOD + custo %) ─────────────────────────────────────────

def _simulate_stock(df, symbol, strat_fn, name, tf=15):
    one_per_day = name in ONE_PER_DAY
    trades, pos = [], None
    last_day = None
    n = len(df)
    start = 400 if tf >= 30 else (500 if tf == 15 else 600)
    if n <= start + 20:
        start = max(80, n // 5)
    eod_hh = 16.9  # fecha perto do leilão

    for i in range(start, n):
        r = df.iloc[i]
        if pos:
            buy = pos["dir"] == "COMPRA"
            exit_R = None
            stopped = True
            if (r.Low <= pos["sl"]) if buy else (r.High >= pos["sl"]):
                exit_R = ((pos["sl"] - pos["entry"]) / pos["risk"]) if buy else (
                    (pos["entry"] - pos["sl"]) / pos["risk"])
            elif pos["tp2"] and ((r.High >= pos["tp2"]) if buy else (r.Low <= pos["tp2"])):
                exit_R = (pos["tp2"] - pos["entry"]) / pos["risk"] if buy else (
                    (pos["entry"] - pos["tp2"]) / pos["risk"])
                stopped = False
            elif (not pos["tp2"]) and ((r.High >= pos["tp1"]) if buy else (r.Low <= pos["tp1"])):
                exit_R = (pos["tp1"] - pos["entry"]) / pos["risk"] if buy else (
                    (pos["entry"] - pos["tp1"]) / pos["risk"])
                stopped = False
            elif pos["tp2"] and (not pos["tp1_done"]) and (
                    (r.High >= pos["tp1"]) if buy else (r.Low <= pos["tp1"])):
                pos["tp1_done"] = True
                pos["realized"] = 0.5 * (
                    (pos["tp1"] - pos["entry"]) / pos["risk"] if buy else (
                        (pos["entry"] - pos["tp1"]) / pos["risk"])) if pos["partial"] else 0.0
                pos["sl"] = pos["entry"]
            # EOD / virada de dia
            if exit_R is None and (float(r.hh) >= eod_hh or r["day"] != pos["day"]):
                px = float(r.Close) if float(r.hh) >= eod_hh else float(df.iloc[i - 1].Close)
                exit_R = ((px - pos["entry"]) / pos["risk"]) if buy else (
                    (pos["entry"] - px) / pos["risk"])
                stopped = False
            if exit_R is not None:
                rem = 0.5 if (pos["partial"] and pos["tp1_done"]) else 1.0
                gross = pos["realized"] + rem * exit_R
                cost = stock_cost_pts(pos["entry"], stopped=stopped) / pos["risk"]
                trades.append({
                    "ts": pos["ts"], "sym": symbol, "dir": pos["dir"],
                    "gross_R": round(gross, 3), "cost_R": round(cost, 3),
                    "net_R": round(gross - cost, 3),
                })
                pos = None
            continue

        if one_per_day and r["day"] == last_day:
            continue
        if int(getattr(r, "dow", 0) or 0) >= 5:
            continue
        sig = strat_fn(df, i, symbol)
        if not sig:
            continue
        entry = float(r.Close)
        risk = abs(entry - float(sig["sl"]))
        atr_i = float(r.atr or 0)
        min_risk = 0.15 * max(atr_i, 1e-9) if atr_i > 0 else 6 * TICK
        if risk <= 0 or risk < min_risk or risk < 6 * TICK:
            continue
        pos = {
            "ts": df.index[i], "day": r["day"], "dir": sig["dir"],
            "entry": entry, "sl": float(sig["sl"]),
            "tp1": float(sig["tp1"]), "tp2": sig.get("tp2"),
            "partial": bool(sig.get("partial")),
            "tp1_done": False, "realized": 0.0, "risk": risk,
        }
        last_day = r["day"]
    return pd.DataFrame(trades)


# ── Fetch MT5 ───────────────────────────────────────────────────────────────

def _fetch_stock(symbol: str, tf_min: int, bars: int = 100000):
    import MetaTrader5 as mt5
    if mt5.terminal_info() is None:
        kw = {}
        path = os.getenv("MT5_PATH", "")
        if path and os.path.exists(path):
            kw["path"] = path
        login = int(os.getenv("MT5_LOGIN", "0") or 0)
        pw, srv = os.getenv("MT5_PASSWORD", ""), os.getenv("MT5_SERVER", "")
        if login and pw and srv:
            kw.update(login=login, password=pw, server=srv)
        if not mt5.initialize(**kw):
            return None
    tf_map = {
        5: mt5.TIMEFRAME_M5, 15: mt5.TIMEFRAME_M15, 30: mt5.TIMEFRAME_M30,
        60: mt5.TIMEFRAME_H1, 240: mt5.TIMEFRAME_H4, 1440: mt5.TIMEFRAME_D1,
    }
    tf = tf_map.get(tf_min, mt5.TIMEFRAME_M15)
    if not mt5.symbol_select(symbol, True):
        return None
    rates = None
    for cnt in (bars, 80000, 50000, 20000, 8000):
        if cnt > bars:
            continue
        rates = mt5.copy_rates_from_pos(symbol, tf, 0, cnt)
        if rates is not None and len(rates) > 0:
            break
    if rates is None or len(rates) == 0:
        return None
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df = df.set_index("time")
    vol = df["real_volume"] if df["real_volume"].sum() > 0 else df["tick_volume"]
    return pd.DataFrame({
        "Open": df["open"], "High": df["high"], "Low": df["low"],
        "Close": df["close"], "Volume": vol,
    }, index=df.index)


# ── Workers ─────────────────────────────────────────────────────────────────

def _worker_init(dfs_by_key, want_syms):
    global _WORKER_DFS
    _WORKER_DFS = dfs_by_key
    # Windows spawn: registry precisa ser reconstruído no worker
    _build_registry(list(want_syms))


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
    ap.add_argument("--syms", default=",".join(SYMBOLS))
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--only", default="", help="csv de nomes (debug)")
    ap.add_argument("--list-only", action="store_true")
    args = ap.parse_args()

    want = [s.strip().upper() for s in args.syms.split(",") if s.strip()]
    n_reg = _build_registry(want)
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
    log_path = f"logs/backtest_acoes_v3_mega_{stamp}.txt"
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
    out(f"# BACKTEST AÇÕES B3 V3 MEGA — {len(names)} setups · "
        f"{datetime.now():%d/%m/%Y %H:%M}")
    out(f"# Papers: {want}")
    out(f"# Por ativo: {dict(Counter(STRAT_SYM[n] for n in names))}")
    out(f"# TFs: {dict(Counter(STRAT_TF.get(n, 15) for n in names))}")
    out(f"# GO: net≥+0.10R · PF≥1.25 · cons≥55% · OOS · n≥40")
    out(f"# Custos: FEE_PCT_RT={FEE_PCT_RT} + tick · EOD 16:54 · SEM FDS")
    out(f"# workers={args.workers} · NÃO altera live até GO")
    out(f"{'#' * 76}")

    # Load OHLC
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
        _worker_init(dfs, want)
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
                tr.to_csv(f"logs/acoes_v3_{row['sym']}_M{row['tf']}_{row['setup']}.csv",
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
                mark = "★" if "🟢" in str(row.get("veredito", "")) else " "
                out(f"  {mark}[{done}/{len(names)}] {row['setup']:<36} "
                    f"n={row.get('n', 0):<5} net={row.get('net', '—')} "
                    f"PF={row.get('PF', '—')} cons={row.get('cons%', '—')} "
                    f"OOS={row.get('oos_net', '—')}  {row.get('veredito', '')}")
                if tr is not None:
                    goes.append(row["setup"])
                    tr.to_csv(
                        f"logs/acoes_v3_{row['sym']}_M{row['tf']}_{row['setup']}.csv",
                        index=False)

    rk = pd.DataFrame(ranking)
    rk_path = f"logs/backtest_acoes_v3_mega_ranking_{stamp}.csv"
    if not rk.empty:
        rk2 = rk.copy()
        # drop heavy cols
        for c in ("err",):
            if c in rk2.columns:
                rk2 = rk2.drop(columns=[c])
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
        out(f"  🟢 {r['sym']:<7} M{r['tf']:<3} {r['setup']:<36} "
            f"n={r.get('n')} net {r.get('net'):+.3f} PF {r.get('PF')} "
            f"cons {r.get('cons%')}% OOS {r.get('oos_net')}")

    out(f"\n{'#' * 76}\n  TOP 🟡 / melhores net (n≥40)\n{'#' * 76}")
    top = [r for r in ranking if (r.get("n") or 0) >= 40]
    top = sorted(top, key=lambda x: -(x.get("net") or -9))[:40]
    for r in top:
        out(f"  {r.get('veredito', '?')[:12]:<12} {r['sym']:<7} M{r['tf']:<3} "
            f"{r['setup']:<36} n={r.get('n')} net {r.get('net')} "
            f"PF {r.get('PF')} cons {r.get('cons%')}% OOS {r.get('oos_net')}")

    # resumo por ativo
    out(f"\n{'#' * 76}\n  RESUMO POR ATIVO\n{'#' * 76}")
    for sx in want:
        sub = [r for r in ranking if r.get("sym") == sx]
        ng = sum(1 for r in sub if "🟢" in str(r.get("veredito", "")))
        ny = sum(1 for r in sub if "🟡" in str(r.get("veredito", ""))
                 or "PROMISSOR" in str(r.get("veredito", "")))
        out(f"  {sx}: testados={len(sub)}  🟢={ng}  🟡={ny}")

    out(f"\n# Veredito: {'🟢 HÁ GO → wire + tela' if go_rows else '🔴 ZERO GO — tela aguardando'}")
    out(f"# GOs: {goes}")
    out(f"# 🟡 count: {len(yel_rows)}")
    out(f"# Tempo total: {(time.time() - t_all) / 60:.1f} min")
    out(f"# Log: {log_path}")
    out(f"# Ranking: {rk_path}")
    out("# FIM")
    logf.close()
    print(f"\n→ {log_path}")
    print(f"→ {rk_path}")
    print(f"GOs: {len(go_rows)} | 🟡: {len(yel_rows)} | tested: {len(ranking)}")


if __name__ == "__main__":
    main()
