"""
backtest_acoes_b3_v6_mega.py — bateria FOCO zeros ITUB4/ABEV3 (+ BBDC).

v5 (7276) deu 15 GO PETR + 2 BBDC; ITUB/ABEV ainda 0 — quase todos 🟡 por
OOS n<20 (amostra total <~67). Esta rodada amplia n (vol soft / janelas /
TFs) sem baixar a régua.

Régua GO (não baixar):
  net ≥ +0,10 R · PF ≥ 1,25 · cons ≥ 55% · OOS+ · n ≥ 40

Uso:
  python backtest_acoes_b3_v6_mega.py --syms ITUB4,ABEV3,BBDC4 --workers 8
  python backtest_acoes_b3_v6_mega.py --list-only
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
    _inside_h, _nr_h, _volspike_h, _hl_h, _outside_h,
)
from backtest_setups_novos_v14_btc_eth import _engulf_h, _ema_reclaim_h
from backtest_setups_novos_v15_btc_eth import _cap_tp, _rr, _atr_regime

import backtest_acoes_b3_v3_mega as v3
import backtest_acoes_b3_v4_mega as v4
import backtest_acoes_b3_v5_mega as v5
from backtest_acoes_b3_v3_mega import (
    FEE_PCT_RT, _b3_sess, _gap_cont_h, _simulate_stock, _fetch_stock,
)
from backtest_acoes_b3_v5_mega import (
    _enrich_v5, _trend_f, _orb_range_h, _pdm_h,
)

FOCUS_DEFAULT = ["ITUB4", "ABEV3", "BBDC4"]

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


def _known_all(syms: list[str]) -> set[str]:
    v3._build_registry(list(syms))
    known = set(v3.STRATS.keys())
    v4._build_registry(list(syms), skip_v3=True)
    known |= set(v4.STRATS.keys())
    v5._build_registry(list(syms), skip_known=True)
    known |= set(v5.STRATS.keys())
    return known


def _build_registry(syms: list[str], skip_known: bool = True):
    STRATS.clear()
    STRAT_SYM.clear()
    STRAT_TF.clear()
    ONE_PER_DAY.clear()

    known = _known_all(syms) if skip_known else set()

    def reg(name, fn, sym, tf=15, one_per_day=False):
        if name in known:
            return
        _reg(name, fn, sym, tf=tf, one_per_day=one_per_day)

    for sx in syms:
        # ── NR soft++ (ITUB NR4_AM_V13_M30: n=44 OOS n=14 → precisa n≥67) ──
        for n in (3, 4, 5):
            for vol, vtag in ((0.9, "V09"), (1.0, "V10"), (1.15, "V115")):
                for tag, h0, h1 in (
                    ("AM", 10.0, 12.5), ("AMW", 10.0, 13.5),
                    ("LATEAM", 11.0, 13.5), ("DAY", 10.0, 16.5),
                ):
                    for tf, tlab in ((15, "M15"), (30, "M30"), (60, "H1")):
                        base = _nr_h(n, vol, h0, h1)
                        reg(f"{sx}_NR{n}_{tag}_{vtag}_V6_{tlab}",
                            _b3_sess(base, h0, h1), sx, tf=tf)
                        reg(f"{sx}_NR{n}_{tag}_{vtag}_V6_MT_{tlab}",
                            _b3_sess(_mt(base), h0, h1), sx, tf=tf)
                        reg(f"{sx}_NR{n}_{tag}_{vtag}_V6_TP15_{tlab}",
                            _b3_sess(_cap_tp(base, 1.5), h0, h1), sx, tf=tf)
                        reg(f"{sx}_NR{n}_{tag}_{vtag}_V6_RR18_{tlab}",
                            _b3_sess(_rr(base, 1.8, 2.5), h0, h1), sx, tf=tf)
                        reg(f"{sx}_NR{n}_{tag}_{vtag}_V6_LOATR_{tlab}",
                            _atr_regime(_b3_sess(base, h0, h1), 0.5, 1.2),
                            sx, tf=tf)

        # ── ORB60 soft (ITUB ORB60_GAP_V14_LATE n=52 OOS n=16) ──
        for vol, vtag in ((0.9, "V09"), (1.0, "V10"), (1.15, "V115")):
            for hh0, hh1, wtag in (
                (11.0, 13.5, ""), (11.0, 14.5, "_WIDE"),
                (11.5, 14.0, "_LATE"), (10.75, 13.5, "_EARLY"),
            ):
                for gap in (False, True):
                    gtag = "_GAP" if gap else ""
                    fn = _orb_range_h(
                        "orb60_hi", "orb60_lo", vol, hh0, hh1,
                        gap_dir=gap, min_atr=0.25)
                    reg(f"{sx}_ORB60{gtag}_{vtag}{wtag}_V6",
                        fn, sx, one_per_day=True)
                    reg(f"{sx}_ORB60{gtag}_{vtag}{wtag}_V6_MT",
                        _mt(fn), sx, one_per_day=True)
                    reg(f"{sx}_ORB60{gtag}_{vtag}{wtag}_V6_TP15",
                        _cap_tp(fn, 1.5), sx, one_per_day=True)
                    reg(f"{sx}_ORB60{gtag}_{vtag}{wtag}_V6_RR18",
                        _rr(fn, 1.8, 2.5), sx, one_per_day=True)
                    reg(f"{sx}_ORB60{gtag}_{vtag}{wtag}_V6_M30",
                        fn, sx, tf=30, one_per_day=True)

        # ── PDM soft (ABEV PDM_V15_H1 n=59 OOS n=18) ──
        for vol, vtag in ((0.85, "V085"), (0.95, "V095"), (1.1, "V11")):
            for tag, h0, h1 in (
                ("AM", 10.0, 12.5), ("AMW", 10.0, 13.5),
                ("DAY", 10.0, 16.5), ("LATE", 11.0, 14.0),
            ):
                for tf, tlab in ((15, "M15"), (30, "M30"), (60, "H1")):
                    fn = _pdm_h(vol, h0, h1)
                    reg(f"{sx}_PDM_{vtag}_{tag}_V6_{tlab}",
                        fn, sx, tf=tf, one_per_day=True)
                    reg(f"{sx}_PDM_{vtag}_{tag}_V6_MT_{tlab}",
                        _mt(fn), sx, tf=tf, one_per_day=True)
                    reg(f"{sx}_PDM_{vtag}_{tag}_V6_TP15_{tlab}",
                        _cap_tp(fn, 1.5), sx, tf=tf, one_per_day=True)
                    reg(f"{sx}_PDM_{vtag}_{tag}_V6_RR18_{tlab}",
                        _rr(fn, 1.8, 2.5), sx, tf=tf, one_per_day=True)
                    reg(f"{sx}_PDM_{vtag}_{tag}_V6_LOATR_{tlab}",
                        _atr_regime(fn, 0.5, 1.2), sx, tf=tf,
                        one_per_day=True)

        # ── EMA soft (ABEV EMA_OPEN1H_V5_MT: cons54 / oos_net 0.087) ──
        for vol, vtag in ((0.9, "V09"), (1.0, "V10"), (1.1, "V11")):
            for tag, h0, h1 in (
                ("OPEN1H", 10.0, 11.0), ("OPEN90", 10.0, 11.5),
                ("AM", 10.0, 12.5), ("MID", 10.5, 12.0),
                ("DAY", 10.0, 16.5),
            ):
                for tf, tlab in ((15, "M15"), (30, "M30"), (60, "H1")):
                    fn = _ema_reclaim_h(h0, h1, vol)
                    sess = _b3_sess(fn, h0, h1)
                    reg(f"{sx}_EMA_{tag}_{vtag}_V6_{tlab}",
                        sess, sx, tf=tf)
                    reg(f"{sx}_EMA_{tag}_{vtag}_V6_MT_{tlab}",
                        _b3_sess(_mt(fn), h0, h1), sx, tf=tf)
                    reg(f"{sx}_EMA_{tag}_{vtag}_V6_TP15_{tlab}",
                        _b3_sess(_cap_tp(fn, 1.5), h0, h1), sx, tf=tf)
                    reg(f"{sx}_EMA_{tag}_{vtag}_V6_RR16_{tlab}",
                        _b3_sess(_rr(fn, 1.6, 2.2), h0, h1), sx, tf=tf)
                    reg(f"{sx}_EMA_{tag}_{vtag}_V6_LOATR_{tlab}",
                        _atr_regime(sess, 0.45, 1.15), sx, tf=tf)
                    reg(f"{sx}_EMA_{tag}_{vtag}_V6_TREND_{tlab}",
                        _trend_f(sess, "with"), sx, tf=tf)

        # ── INS soft (ITUB INS near) ──
        for vol, vtag in ((0.95, "V095"), (1.1, "V11"), (1.2, "V12")):
            for tag, h0, h1 in (
                ("AM", 10.0, 12.5), ("AMW", 10.0, 13.5),
                ("DAY", 10.0, 16.5),
            ):
                for tf, tlab in ((15, "M15"), (30, "M30"), (60, "H1")):
                    fn = _inside_h(vol, h0, h1)
                    sess = _b3_sess(fn, h0, h1)
                    reg(f"{sx}_INS_{tag}_{vtag}_V6_{tlab}",
                        sess, sx, tf=tf)
                    reg(f"{sx}_INS_{tag}_{vtag}_V6_MT_{tlab}",
                        _b3_sess(_mt(fn), h0, h1), sx, tf=tf)
                    reg(f"{sx}_INS_{tag}_{vtag}_V6_TP15_{tlab}",
                        _b3_sess(_cap_tp(fn, 1.5), h0, h1), sx, tf=tf)
                    reg(f"{sx}_INS_{tag}_{vtag}_V6_LOATR_{tlab}",
                        _atr_regime(sess, 0.5, 1.2), sx, tf=tf)
                    reg(f"{sx}_INS_{tag}_{vtag}_V6_TREND_{tlab}",
                        _trend_f(sess, "with"), sx, tf=tf)

        # ── VSPIKE soft (BBDC T25 OOS n=15) ──
        for thr, ttag in ((1.6, "T16"), (1.8, "T18"), (2.0, "T20"),
                          (2.3, "T23"), (2.4, "T24")):
            for atr_m, atag in ((1.2, "A12"), (1.4, "A14")):
                for tag, h0, h1 in (
                    ("AM", 10.0, 12.5), ("DAY", 10.0, 16.5),
                    ("LATEAM", 11.0, 13.5), ("OPEN1H", 10.0, 11.0),
                ):
                    fn = _volspike_h(thr, atr_m, h0, h1)
                    sess = _b3_sess(fn, h0, h1)
                    reg(f"{sx}_VSPIKE_{ttag}_{atag}_{tag}_V6",
                        sess, sx)
                    reg(f"{sx}_VSPIKE_{ttag}_{atag}_{tag}_V6_MT",
                        _b3_sess(_mt(fn), h0, h1), sx)
                    reg(f"{sx}_VSPIKE_{ttag}_{atag}_{tag}_V6_TP15",
                        _b3_sess(_cap_tp(fn, 1.5), h0, h1), sx)
                    reg(f"{sx}_VSPIKE_{ttag}_{atag}_{tag}_V6_RR18",
                        _b3_sess(_rr(fn, 1.8, 2.5), h0, h1), sx)
                    reg(f"{sx}_VSPIKE_{ttag}_{atag}_{tag}_V6_M30",
                        sess, sx, tf=30)

        # ── GAPC soft banks ──
        for gp, gtag in ((0.15, "G15"), (0.25, "G25"), (0.35, "G35")):
            for hh0, hh1, stag in (
                (10.0, 12.0, ""), (10.0, 13.0, "_WIDE"),
                (10.0, 11.5, "_AM"),
            ):
                for vol, vtag in ((0.9, "V09"), (1.0, "V10")):
                    fn = _gap_cont_h(gp, hh0, hh1, vol=vol)
                    reg(f"{sx}_GAPC_{gtag}{stag}_{vtag}_V6",
                        fn, sx, one_per_day=True)
                    reg(f"{sx}_GAPC_{gtag}{stag}_{vtag}_V6_MT",
                        _mt(fn), sx, one_per_day=True)
                    reg(f"{sx}_GAPC_{gtag}{stag}_{vtag}_V6_TP15",
                        _cap_tp(fn, 1.5), sx, one_per_day=True)
                    reg(f"{sx}_GAPC_{gtag}{stag}_{vtag}_V6_M30",
                        fn, sx, tf=30, one_per_day=True)

        # ── HL / OUT / ENG lean ──
        for fam, maker in (
            ("HL", lambda h0, h1, v: _hl_h(v, h0, h1)),
            ("OUT", lambda h0, h1, v: _outside_h(v, h0, h1)),
            ("ENG", lambda h0, h1, v: _engulf_h(v, h0, h1)),
        ):
            for vol, vtag in ((1.0, "V10"), (1.15, "V115")):
                for tag, h0, h1 in (
                    ("AM", 10.0, 12.5), ("DAY", 10.0, 16.5),
                ):
                    for tf, tlab in ((15, "M15"), (60, "H1")):
                        fn = maker(h0, h1, vol)
                        sess = _b3_sess(fn, h0, h1)
                        reg(f"{sx}_{fam}_{tag}_{vtag}_V6_{tlab}",
                            sess, sx, tf=tf)
                        reg(f"{sx}_{fam}_{tag}_{vtag}_V6_MT_{tlab}",
                            _b3_sess(_mt(fn), h0, h1), sx, tf=tf)
                        reg(f"{sx}_{fam}_{tag}_{vtag}_V6_TP15_{tlab}",
                            _b3_sess(_cap_tp(fn, 1.5), h0, h1), sx, tf=tf)

    return len(STRATS)


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
        print(f"TOTAL {len(names)} (registry build={n_reg})")
        print(dict(Counter(STRAT_SYM[n] for n in names)))
        print(dict(Counter(STRAT_TF.get(n, 15) for n in names)))
        return

    os.makedirs("logs", exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    log_path = f"logs/backtest_acoes_v6_mega_{stamp}.txt"
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
    out(f"# BACKTEST AÇÕES B3 V6 MEGA — {len(names)} setups NOVOS · "
        f"{datetime.now():%d/%m/%Y %H:%M}")
    out(f"# Foco zeros: ITUB4/ABEV3 (+BBDC) — 0 overlap v3+v4+v5")
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
            out(f"  {mark}[{i}/{len(names)}] {row['setup']:<48} "
                f"n={row.get('n', 0):<5} net={row.get('net', '—')} "
                f"PF={row.get('PF', '—')} cons={row.get('cons%', '—')} "
                f"OOS={row.get('oos_net', '—')}  {row.get('veredito', '')}")
            if tr is not None:
                goes.append(row["setup"])
                tr.to_csv(
                    f"logs/acoes_v6_{row['sym']}_M{row['tf']}_{row['setup']}.csv",
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
                out(f"  {mark}[{done}/{len(names)}] {row['setup']:<48} "
                    f"n={row.get('n', 0):<5} net={row.get('net', '—')} "
                    f"PF={row.get('PF', '—')} cons={row.get('cons%', '—')} "
                    f"OOS={row.get('oos_net', '—')}  {row.get('veredito', '')}")
                if tr is not None:
                    goes.append(row["setup"])
                    tr.to_csv(
                        f"logs/acoes_v6_{row['sym']}_M{row['tf']}_{row['setup']}.csv",
                        index=False)

    rk = pd.DataFrame(ranking)
    rk_path = f"logs/backtest_acoes_v6_mega_ranking_{stamp}.csv"
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
        out(f"  GO {r['sym']:<7} M{r['tf']:<3} {r['setup']:<48} "
            f"n={r.get('n')} net {r.get('net'):+.3f} PF {r.get('PF')} "
            f"cons {r.get('cons%')}% OOS {r.get('oos_net')} "
            f"oos_n={r.get('oos_n')}")

    out(f"\n{'#' * 76}\n  TOP melhores net (n>=40)\n{'#' * 76}")
    top = [r for r in ranking if (r.get("n") or 0) >= 40]
    top = sorted(top, key=lambda x: -(x.get("net") or -9))[:60]
    for r in top:
        out(f"  {str(r.get('veredito', '?'))[:14]:<14} {r['sym']:<7} M{r['tf']:<3} "
            f"{r['setup']:<48} n={r.get('n')} net {r.get('net')} "
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
