"""
backtest_acoes_b3_v4_mega.py — complemento + expansão do grid AÇÕES B3.

V3 cobriu 91 variantes/paper (=637). Este script NÃO re-testa esses nomes.
Cobre o restante do plano (~1200+) + famílias promissoras (ORB/GAP/VWAP/IMP/INS AM)
priorizando refinamentos para PETR4/ITUB4/BBDC4/ABEV3 (zero GO na v3).

Régua GO (não baixar):
  net ≥ +0,10 R · PF ≥ 1,25 · cons ≥ 55% · OOS+ · n ≥ 40

Uso:
  python backtest_acoes_b3_v4_mega.py
  python backtest_acoes_b3_v4_mega.py --workers 6 --bars 80000
  python backtest_acoes_b3_v4_mega.py --syms PETR4,ITUB4,BBDC4,ABEV3 --workers 4
  python backtest_acoes_b3_v4_mega.py --list-only
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

import pandas as pd

from backtest_kimi_real import add_indicators
from backtest_setups_novos import stats, consistency, verdict, OOS_FRAC
from backtest_setups_novos_v2 import add_extra_v2
from backtest_setups_novos_v9_refine import _mt
from backtest_setups_novos_v13_btc_24h import (
    _inside_h, _nr_h, _impulse_h, _h4_pb_ema_h, _volspike_h,
    _outside_h, _hl_h, _dbl_inside_h,
)
from backtest_setups_novos_v14_btc_eth import (
    _engulf_h, _sqz_break_h, _ema_reclaim_h,
)
from backtest_setups_novos_v15_btc_eth import _vwap_fade_h, _vwap_cont_h, _cap_tp, _rr

import backtest_acoes_b3_v3_mega as v3
from backtest_acoes_b3_v3_mega import (
    SYMBOLS, FEE_PCT_RT, _b3_sess, _gap_aligned, _orb30_h, _gap_fade_h,
    _gap_cont_h, _pdh_stock_h, _vwap_open_h, _simulate_stock, _fetch_stock,
    stock_cost_pts,
)

STRATS: dict = {}
STRAT_SYM: dict = {}
STRAT_TF: dict = {}
ONE_PER_DAY: set = set()
_WORKER_DFS: dict = {}

# Setups já 🟢 na v3 — não priorizar (e não entram no registry v4 de qualquer forma)
_V3_GO = {
    "VALE3_ORB30_V15", "VALE3_ORB30_GAP_V13", "VALE3_ORB30_GAP_V11",
    "VALE3_IMP_DAY_M5", "VALE3_VWAPC_AM", "VALE3_GAPC_G30", "VALE3_GAPC_G30_MT",
    "VALE3_GAPC_G45", "VALE3_GAPC_G60", "BBAS3_ORB30_V13", "BBAS3_ORB30_TP15",
    "WEGE3_INS_AM", "WEGE3_GAPC_G30_MT", "WEGE3_GAPC_G30",
}


def _reg(name, fn, sym, tf=15, one_per_day=False):
    if name in _V3_GO:
        return
    STRATS[name] = fn
    STRAT_SYM[name] = sym
    STRAT_TF[name] = tf
    if one_per_day:
        ONE_PER_DAY.add(name)


def _orb45_h(vol=1.2, hh0=11.0, hh1=13.0, rr1=1.5, rr2=2.0, gap_dir=False):
    """ORB 10:00–10:45 quebrado mais tarde (45–90 min após ORB)."""
    return _orb30_h(vol=vol, hh0=hh0, hh1=hh1, rr1=rr1, rr2=rr2, gap_dir=gap_dir)


def _v3_names(syms: list[str]) -> set[str]:
    v3._build_registry(list(syms))
    return set(v3.STRATS.keys())


# ── Registry v4: só o que NÃO está na v3 ────────────────────────────────────

SESSIONS_EXPAND = [
    ("AM", 10.0, 12.5),
    ("MID", 11.5, 14.0),
    ("PM", 13.0, 16.5),
    ("POWER", 14.5, 17.0),
]


def _build_registry(syms: list[str], skip_v3: bool = True):
    STRATS.clear()
    STRAT_SYM.clear()
    STRAT_TF.clear()
    ONE_PER_DAY.clear()

    known = _v3_names(syms) if skip_v3 else set()

    def reg(name, fn, sym, tf=15, one_per_day=False):
        if name in known or name in _V3_GO:
            return
        _reg(name, fn, sym, tf=tf, one_per_day=one_per_day)

    for sx in syms:
        # ── Completar AM/MID/PM/POWER com famílias que só estavam no DAY ──
        for tag, h0, h1 in SESSIONS_EXPAND:
            # INS variants
            reg(f"{sx}_INS_{tag}_V14",
                _b3_sess(_inside_h(1.4, h0, h1), h0, h1), sx)
            reg(f"{sx}_INS_{tag}_MT",
                _b3_sess(_mt(_inside_h(1.3, h0, h1)), h0, h1), sx)
            reg(f"{sx}_INS_{tag}_TP20",
                _b3_sess(_cap_tp(_inside_h(1.3, h0, h1), 2.0), h0, h1), sx)
            # NR4 / NR7 / NR5_MT
            for n in (4, 5, 7):
                if tag == "AM" and n == 5:
                    # NR5_AM já na v3 — só MT/TP
                    reg(f"{sx}_NR{n}_{tag}_MT",
                        _b3_sess(_mt(_nr_h(n, 1.3, h0, h1)), h0, h1), sx)
                    reg(f"{sx}_NR{n}_{tag}_TP20",
                        _b3_sess(_cap_tp(_nr_h(n, 1.3, h0, h1), 2.0), h0, h1), sx)
                else:
                    reg(f"{sx}_NR{n}_{tag}",
                        _b3_sess(_nr_h(n, 1.3, h0, h1), h0, h1), sx)
                    reg(f"{sx}_NR{n}_{tag}_MT",
                        _b3_sess(_mt(_nr_h(n, 1.3, h0, h1)), h0, h1), sx)
            # HL / IMP MT + TP
            reg(f"{sx}_HL_{tag}_MT",
                _b3_sess(_mt(_hl_h(1.4, h0, h1)), h0, h1), sx)
            reg(f"{sx}_IMP_{tag}_MT",
                _b3_sess(_mt(_impulse_h(1.8, 1.6, h0, h1, 2.0)), h0, h1), sx)
            reg(f"{sx}_IMP_{tag}_TP25",
                _b3_sess(_impulse_h(2.0, 1.5, h0, h1, 2.5), h0, h1), sx)
            # Famílias DAY-only → sessões
            if tag != "AM" or True:  # MID sempre novo; AM/PM/POWER: só se nome novo
                reg(f"{sx}_OUT_{tag}",
                    _b3_sess(_outside_h(1.3, h0, h1), h0, h1), sx)
                reg(f"{sx}_EMA_{tag}",
                    _b3_sess(_ema_reclaim_h(h0, h1, 1.2), h0, h1), sx)
                reg(f"{sx}_SQZ_{tag}",
                    _b3_sess(_sqz_break_h(1.4, h0, h1), h0, h1), sx)
                reg(f"{sx}_VWAPF_{tag}",
                    _b3_sess(_vwap_fade_h(1.2, 1.1, h0, h1), h0, h1), sx)
                reg(f"{sx}_PDH_{tag}_MT",
                    _b3_sess(_mt(_pdh_stock_h(1.3, h0, h1)), h0, h1), sx,
                    one_per_day=True)
                # ENG/VWAPC/PDH/HL/IMP/INS/NR5 já existem em AM/PM/POWER na v3
                # MID precisa do subset completo
                if tag == "MID":
                    reg(f"{sx}_INS_{tag}",
                        _b3_sess(_inside_h(1.3, h0, h1), h0, h1), sx)
                    reg(f"{sx}_HL_{tag}",
                        _b3_sess(_hl_h(1.3, h0, h1), h0, h1), sx)
                    reg(f"{sx}_IMP_{tag}",
                        _b3_sess(_impulse_h(2.0, 1.5, h0, h1, 2.0), h0, h1), sx)
                    reg(f"{sx}_PDH_{tag}",
                        _b3_sess(_pdh_stock_h(1.2, h0, h1), h0, h1), sx,
                        one_per_day=True)
                    reg(f"{sx}_ENG_{tag}",
                        _b3_sess(_engulf_h(1.3, h0, h1), h0, h1), sx)
                    reg(f"{sx}_VWAPC_{tag}",
                        _b3_sess(_vwap_cont_h(1.2, 0.3, h0, h1), h0, h1), sx)

        # ── ORB / GAP refinamentos (promissores na v3) ──
        for vol, vtag in ((1.1, "V11"), (1.3, "V13"), (1.5, "V15")):
            reg(f"{sx}_ORB45_{vtag}",
                _orb45_h(vol, 11.0, 13.0), sx, one_per_day=True)
            reg(f"{sx}_ORB45_GAP_{vtag}",
                _orb45_h(vol, 11.0, 13.0, gap_dir=True), sx, one_per_day=True)
            reg(f"{sx}_ORB30_{vtag}_TP20",
                _orb30_h(vol, 10.75, 12.5, 1.5, 2.0), sx, one_per_day=True)
            reg(f"{sx}_ORB30_GAP_{vtag}_TP15",
                _orb30_h(vol, 10.75, 12.5, 1.5, 1.5, gap_dir=True), sx,
                one_per_day=True)

        reg(f"{sx}_ORB30_EARLY",
            _orb30_h(1.3, 10.5, 11.5), sx, one_per_day=True)
        reg(f"{sx}_ORB30_LATE",
            _orb30_h(1.3, 11.5, 13.5), sx, one_per_day=True)
        reg(f"{sx}_ORB30_RR20",
            _rr(_orb30_h(1.3, 10.75, 12.5), 2.0, 3.0), sx, one_per_day=True)

        for gp, gtag in ((0.25, "G25"), (0.35, "G35"), (0.75, "G75")):
            reg(f"{sx}_GAPC_{gtag}",
                _gap_cont_h(gp, 10.0, 12.0), sx, one_per_day=True)
            reg(f"{sx}_GAPC_{gtag}_MT",
                _mt(_gap_cont_h(gp, 10.0, 12.5)), sx, one_per_day=True)
            reg(f"{sx}_GAPF_{gtag}",
                _gap_fade_h(gp, 10.0, 11.5), sx, one_per_day=True)

        for gp, gtag in ((0.30, "G30"), (0.45, "G45"), (0.60, "G60")):
            reg(f"{sx}_GAPF_{gtag}_MT",
                _mt(_gap_fade_h(gp, 10.0, 11.5)), sx, one_per_day=True)
            reg(f"{sx}_GAPC_{gtag}_AM",
                _gap_cont_h(gp, 10.0, 11.5), sx, one_per_day=True)
            reg(f"{sx}_GAPC_{gtag}_TP20",
                _cap_tp(_gap_cont_h(gp, 10.0, 12.0), 2.0), sx, one_per_day=True)
            reg(f"{sx}_GAPC_{gtag}_RR20",
                _rr(_gap_cont_h(gp, 10.0, 12.0), 2.0, 3.0), sx, one_per_day=True)

        # VWAP refinamentos
        for tag, h0, h1 in (("AM", 10.0, 12.5), ("MID", 11.5, 14.0),
                            ("PM", 13.0, 16.5), ("POWER", 14.5, 17.0)):
            reg(f"{sx}_VWAPC_{tag}_MT",
                _b3_sess(_mt(_vwap_cont_h(1.2, 0.3, h0, h1)), h0, h1), sx)
            reg(f"{sx}_VWAPC_{tag}_V14",
                _b3_sess(_vwap_cont_h(1.4, 0.25, h0, h1), h0, h1), sx)
        reg(f"{sx}_VWAPC_DAY_MT",
            _b3_sess(_mt(_vwap_cont_h(1.2, 0.3, 10, 17)), 10, 17), sx)
        reg(f"{sx}_VWAPC_DAY_TP20",
            _b3_sess(_cap_tp(_vwap_cont_h(1.2, 0.3, 10, 17), 2.0), 10, 17), sx)
        reg(f"{sx}_VWAP_OPEN_AM", _vwap_open_h(1.2, 10.5, 12.5), sx)
        reg(f"{sx}_VWAP_OPEN_RR20",
            _rr(_vwap_open_h(1.3, 10.5, 15.0), 2.0, 3.0), sx)

        # Gap-aligned extras
        for fam, fn in (
            ("IMP", _impulse_h(2.0, 1.5, 10, 17, 2.0)),
            ("ENG", _engulf_h(1.3, 10, 17)),
            ("EMA", _ema_reclaim_h(10, 17, 1.2)),
            ("VWAPC", _vwap_cont_h(1.2, 0.3, 10, 17)),
            ("PDH", _pdh_stock_h(1.2, 10, 17)),
        ):
            reg(f"{sx}_{fam}_GAP_DAY",
                _gap_aligned(_b3_sess(fn, 10, 17), 0.30, True), sx,
                one_per_day=(fam == "PDH"))

        # DBL / VOLSPIKE / H4 extras
        reg(f"{sx}_DBL_INS_AM",
            _b3_sess(_dbl_inside_h(1.3, 10.0, 12.5), 10, 12.5), sx)
        reg(f"{sx}_VOLSPIKE_DAY",
            _b3_sess(_volspike_h(2.0, 1.5, 10.0, 17.0), 10, 17), sx)
        reg(f"{sx}_VOLSPIKE_AM_MT",
            _b3_sess(_mt(_volspike_h(2.0, 1.5, 10.0, 12.5)), 10, 12.5), sx)
        reg(f"{sx}_H4_PB_PM",
            _b3_sess(_h4_pb_ema_h(13.0, 16.5), 13, 16.5), sx)
        reg(f"{sx}_H4_PB_DAY_MT",
            _b3_sess(_mt(_h4_pb_ema_h(10.0, 17.0)), 10, 17), sx)

        # DAY extras (TP/RR em famílias fortes)
        for base, fn, opd in (
            (f"{sx}_ENG_DAY_TP20", _cap_tp(_engulf_h(1.3, 10, 17), 2.0), False),
            (f"{sx}_EMA_DAY_TP20", _cap_tp(_ema_reclaim_h(10, 17, 1.2), 2.0), False),
            (f"{sx}_SQZ_DAY_TP20", _cap_tp(_sqz_break_h(1.4, 10, 17), 2.0), False),
            (f"{sx}_OUT_DAY_TP20", _cap_tp(_outside_h(1.3, 10, 17), 2.0), False),
            (f"{sx}_IMP_DAY_RR20", _rr(_impulse_h(2.0, 1.5, 10, 17, 2.0), 2.0, 3.0), False),
            (f"{sx}_HL_DAY_RR20", _rr(_hl_h(1.3, 10, 17), 2.0, 3.0), False),
            (f"{sx}_NR4_DAY_TP20", _cap_tp(_nr_h(4, 1.3, 10, 17), 2.0), False),
            (f"{sx}_NR7_DAY_TP20", _cap_tp(_nr_h(7, 1.3, 10, 17), 2.0), False),
            (f"{sx}_ORB30_GAP_TP20",
             _orb30_h(1.3, 10.75, 12.5, 1.5, 2.0, gap_dir=True), True),
        ):
            reg(base, _b3_sess(fn, 10, 17) if "ORB" not in base else fn, sx,
                one_per_day=opd)

        # ── Multi-TF expansão M30 / H1 ──
        for tf, tf_lab in ((30, "M30"), (60, "H1")):
            for fam, fn, opd in (
                ("ENG", _engulf_h(1.3, 10, 17), False),
                ("SQZ", _sqz_break_h(1.4, 10, 17), False),
                ("OUT", _outside_h(1.3, 10, 17), False),
                ("VWAPC", _vwap_cont_h(1.2, 0.3, 10, 17), False),
                ("VWAPF", _vwap_fade_h(1.2, 1.1, 10, 17), False),
                ("NR4", _nr_h(4, 1.3, 10, 17), False),
                ("NR7", _nr_h(7, 1.3, 10, 17), False),
                ("HL_MT", _mt(_hl_h(1.4, 10, 17)), False),
                ("IMP_MT", _mt(_impulse_h(1.8, 1.6, 10, 17, 2.0)), False),
                ("PDH_MT", _mt(_pdh_stock_h(1.3, 10, 17)), True),
                ("INS_MT", _mt(_inside_h(1.3, 10, 17)), False),
                ("INS_AM", _inside_h(1.3, 10, 12.5), False),
                ("NR5_AM", _nr_h(5, 1.3, 10, 12.5), False),
            ):
                reg(f"{sx}_{fam}_DAY_{tf_lab}" if not fam.endswith("_AM")
                    else f"{sx}_{fam}_{tf_lab}",
                    _b3_sess(fn, 10, 17) if "AM" not in fam
                    else _b3_sess(fn, 10, 12.5),
                    sx, tf=tf, one_per_day=opd)
            reg(f"{sx}_ORB30_GAP_{tf_lab}",
                _orb30_h(1.3, 10.75, 13.0, gap_dir=True), sx, tf=tf,
                one_per_day=True)
            reg(f"{sx}_ORB30_V15_{tf_lab}",
                _orb30_h(1.5, 10.75, 13.0), sx, tf=tf, one_per_day=True)
            for gp, gtag in ((0.30, "G30"), (0.60, "G60")):
                reg(f"{sx}_GAPC_{gtag}_{tf_lab}",
                    _gap_cont_h(gp, 10.0, 13.0), sx, tf=tf, one_per_day=True)
                reg(f"{sx}_GAPC_{gtag}_MT_{tf_lab}",
                    _mt(_gap_cont_h(gp, 10.0, 13.0)), sx, tf=tf, one_per_day=True)

        # ── M5 expansão (além de INS/NR5/IMP DAY) ──
        for fam, fn, opd in (
            ("ORB30", _orb30_h(1.3, 10.75, 12.5), True),
            ("ORB30_GAP", _orb30_h(1.3, 10.75, 12.5, gap_dir=True), True),
            ("ORB30_V15", _orb30_h(1.5, 10.75, 12.5), True),
            ("GAPC_G30", _gap_cont_h(0.30, 10.0, 12.0), True),
            ("GAPC_G45", _gap_cont_h(0.45, 10.0, 12.0), True),
            ("GAPC_G30_MT", _mt(_gap_cont_h(0.30, 10.0, 12.5)), True),
            ("VWAPC_AM", _vwap_cont_h(1.2, 0.3, 10.0, 12.5), False),
            ("VWAPC_DAY", _vwap_cont_h(1.2, 0.3, 10.0, 17.0), False),
            ("HL_DAY", _hl_h(1.3, 10, 17), False),
            ("PDH_DAY", _pdh_stock_h(1.2, 10, 17), True),
            ("ENG_DAY", _engulf_h(1.3, 10, 17), False),
            ("EMA_DAY", _ema_reclaim_h(10, 17, 1.2), False),
            ("NR4_DAY", _nr_h(4, 1.3, 10, 17), False),
            ("OUT_DAY", _outside_h(1.3, 10, 17), False),
            ("IMP_DAY_MT", _mt(_impulse_h(1.8, 1.6, 10, 17, 2.0)), False),
            ("INS_AM", _inside_h(1.3, 10, 12.5), False),
            ("NR5_AM", _nr_h(5, 1.3, 10, 12.5), False),
            ("IMP_AM", _impulse_h(2.0, 1.5, 10, 12.5, 2.0), False),
            ("VOLSPIKE_AM", _volspike_h(2.0, 1.5, 10.0, 12.5), False),
            ("HL_AM", _hl_h(1.3, 10, 12.5), False),
        ):
            # nomes no padrão SYM_FAM_M5
            if fam.endswith("_AM") or fam.endswith("_DAY") or fam.endswith("_DAY_MT"):
                name = f"{sx}_{fam}_M5"
            else:
                name = f"{sx}_{fam}_M5"
            if "ORB" in fam or "GAPC" in fam or "VWAPC" in fam or "VOLSPIKE" in fam \
                    or "PDH" in fam:
                # funções ORB/GAP já embutem janela
                wrapped = fn if ("ORB" in fam or "GAPC" in fam) else _b3_sess(
                    fn, 10, 12.5 if "AM" in fam else 17)
            else:
                h1 = 12.5 if "AM" in fam else 17.0
                wrapped = _b3_sess(fn, 10, h1)
            if "ORB" in fam or fam.startswith("GAPC"):
                wrapped = fn
            reg(name, wrapped, sx, tf=5, one_per_day=opd)

    return len(STRATS)


# ── Workers ─────────────────────────────────────────────────────────────────

def _worker_init(dfs_by_key, want_syms):
    global _WORKER_DFS
    _WORKER_DFS = dfs_by_key
    _build_registry(list(want_syms))
    # espelha ONE_PER_DAY no módulo v3 usado pelo simulador
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
        # simulador usa ONE_PER_DAY do v3
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
    ap.add_argument("--syms", default=",".join(SYMBOLS))
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--only", default="", help="csv de nomes (debug)")
    ap.add_argument("--list-only", action="store_true")
    ap.add_argument("--include-v3", action="store_true",
                    help="não pular nomes já testados na v3")
    args = ap.parse_args()

    want = [s.strip().upper() for s in args.syms.split(",") if s.strip()]
    n_reg = _build_registry(want, skip_v3=not args.include_v3)
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
    log_path = f"logs/backtest_acoes_v4_mega_{stamp}.txt"
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
    out(f"# BACKTEST AÇÕES B3 V4 MEGA — {len(names)} setups NOVOS · "
        f"{datetime.now():%d/%m/%Y %H:%M}")
    out(f"# Complemento da v3 (637 já testados — NÃO retestados)")
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
            mark = "*" if "🟢" in str(row.get("veredito", "")) else " "
            out(f"  {mark}[{i}/{len(names)}] {row['setup']:<40} "
                f"n={row.get('n', 0):<5} net={row.get('net', '—')} "
                f"PF={row.get('PF', '—')} cons={row.get('cons%', '—')} "
                f"OOS={row.get('oos_net', '—')}  {row.get('veredito', '')}")
            if tr is not None:
                goes.append(row["setup"])
                tr.to_csv(f"logs/acoes_v4_{row['sym']}_M{row['tf']}_{row['setup']}.csv",
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
                out(f"  {mark}[{done}/{len(names)}] {row['setup']:<40} "
                    f"n={row.get('n', 0):<5} net={row.get('net', '—')} "
                    f"PF={row.get('PF', '—')} cons={row.get('cons%', '—')} "
                    f"OOS={row.get('oos_net', '—')}  {row.get('veredito', '')}")
                if tr is not None:
                    goes.append(row["setup"])
                    tr.to_csv(
                        f"logs/acoes_v4_{row['sym']}_M{row['tf']}_{row['setup']}.csv",
                        index=False)

    rk = pd.DataFrame(ranking)
    rk_path = f"logs/backtest_acoes_v4_mega_ranking_{stamp}.csv"
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
        out(f"  GO {r['sym']:<7} M{r['tf']:<3} {r['setup']:<40} "
            f"n={r.get('n')} net {r.get('net'):+.3f} PF {r.get('PF')} "
            f"cons {r.get('cons%')}% OOS {r.get('oos_net')}")

    out(f"\n{'#' * 76}\n  TOP melhores net (n>=40)\n{'#' * 76}")
    top = [r for r in ranking if (r.get("n") or 0) >= 40]
    top = sorted(top, key=lambda x: -(x.get("net") or -9))[:50]
    for r in top:
        out(f"  {str(r.get('veredito', '?'))[:14]:<14} {r['sym']:<7} M{r['tf']:<3} "
            f"{r['setup']:<40} n={r.get('n')} net {r.get('net')} "
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
    out(f"# Cumulativo com v3: ~{637 + len(ranking)} setups")
    out("# FIM")
    logf.close()
    print(f"\n-> {log_path}")
    print(f"-> {rk_path}")
    print(f"GOs: {len(go_rows)} | yellow: {len(yel_rows)} | tested: {len(ranking)}")


if __name__ == "__main__":
    main()
