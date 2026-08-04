"""
backtest_setups_novos_v9_refine.py — RODADA 9: refino dos 🟡 v8.

Régua idêntica. NÃO altera motores ao vivo até 🟢.

Ordem: GBP INS → EUR NR5/LH/OF → BTC NR5.
Filtros típicos que salvam OOS: MT (seg–qui), vol↑, janela, gap.

Uso:
  python rodar_backtest_setups_novos_v9_refine.py
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

import pandas as pd

from backtest_kimi_real import add_indicators
from backtest_setups_novos import stats, consistency, verdict, _line, OOS_FRAC, _sig
from backtest_setups_novos_v2 import add_extra_v2, _simulate
from backtest_setups_novos_v3 import make_of_window, make_london_handoff
from backtest_setups_novos_v5 import _nr_break, _h4_ok, _gap_ok
from backtest_setups_novos_v6 import (
    s_xau_nr5_h4, s_xau_nr5_mt, s_xau_inside_h4, s_xau_inside_am,
    s_xau_inside_vol15_h4, s_nr4_h4, s_inside_1015_h4, s_inside_vol13_h4,
    s_pdh_h4_vol12,
)
from backtest_setups_novos_v8_fxcrypto import s_inside_vol12_h4, s_nr5_lon_h4, s_nr5_ny_h4

STRATS = {}
STRAT_SYM = {}
STRAT_TF = {}
ONE_PER_DAY = set()


def _reg(name, fn, sym, tf=15, one_per_day=False):
    STRATS[name] = fn
    STRAT_SYM[name] = sym
    if tf != 15:
        STRAT_TF[name] = tf
    if one_per_day:
        ONE_PER_DAY.add(name)


def _mt(fn):
    def _f(df, i, sym):
        if int(df.iloc[i].dow) not in (0, 1, 2, 3):
            return None
        return fn(df, i, sym)
    return _f


def _gap(fn, min_abs=0.08):
    def _f(df, i, sym):
        base = fn(df, i, sym)
        if not base:
            return None
        if not _gap_ok(df.iloc[i], base["dir"], min_abs):
            return None
        return base
    return _f


def _inside(vol=1.2, hh0=10.0, hh1=14.0):
    def _f(df, i, sym):
        if i < 3:
            return None
        r = df.iloc[i]
        p, pp = df.iloc[i - 1], df.iloc[i - 2]
        if not (hh0 <= r.hh < hh1):
            return None
        if not (p.High <= pp.High and p.Low >= pp.Low):
            return None
        if r.Volume < vol * (r.vol8 or 0):
            return None
        base = None
        if r.Close > p.High:
            base = _sig("COMPRA", r.Close, p.Low, 1.5, 2.5, True)
        elif r.Close < p.Low:
            base = _sig("VENDA", r.Close, p.High, 1.5, 2.5, True)
        if not base or not _h4_ok(r, base["dir"]):
            return None
        return base
    return _f


def _nr5(vol=1.3, hh0=10.0, hh1=14.5):
    def _f(df, i, sym):
        base = _nr_break(df, i, n=5, vol_mult=vol, hh0=hh0, hh1=hh1)
        if not base or not _h4_ok(df.iloc[i], base["dir"]):
            return None
        return base
    return _f


# ════════════════════════════════════════════════════════════════════════════
# GBP — prioridade 1 (INS 🟡 fortes)
# ════════════════════════════════════════════════════════════════════════════
_reg("GBP_INS_H4", s_xau_inside_h4, "GBPUSD")  # baseline 🟡
_reg("GBP_INS_H4_MT", _mt(s_xau_inside_h4), "GBPUSD")
_reg("GBP_INS_H4_GAP", _gap(s_xau_inside_h4, 0.06), "GBPUSD")
_reg("GBP_INS_H4_MT_GAP", _mt(_gap(s_xau_inside_h4, 0.06)), "GBPUSD")
_reg("GBP_INS_V13", s_inside_vol13_h4, "GBPUSD")
_reg("GBP_INS_V13_MT", _mt(s_inside_vol13_h4), "GBPUSD")
_reg("GBP_INS_V14_1014", _inside(1.4, 10.0, 14.0), "GBPUSD")
_reg("GBP_INS_V14_MT", _mt(_inside(1.4, 10.0, 14.0)), "GBPUSD")
_reg("GBP_INS_V15_H4", s_xau_inside_vol15_h4, "GBPUSD")
_reg("GBP_INS_V15_MT", _mt(s_xau_inside_vol15_h4), "GBPUSD")
_reg("GBP_INS_1015", s_inside_1015_h4, "GBPUSD")
_reg("GBP_INS_1015_MT", _mt(s_inside_1015_h4), "GBPUSD")
_reg("GBP_INS_1015_V13", _inside(1.3, 10.0, 15.0), "GBPUSD")
_reg("GBP_INS_1015_V13_MT", _mt(_inside(1.3, 10.0, 15.0)), "GBPUSD")
_reg("GBP_INS_AM", s_xau_inside_am, "GBPUSD")
_reg("GBP_INS_AM_MT", _mt(s_xau_inside_am), "GBPUSD")
_reg("GBP_INS_AM_V13", _inside(1.3, 9.0, 12.0), "GBPUSD")
_reg("GBP_INS_LON_V13", _inside(1.3, 8.0, 12.0), "GBPUSD")
_reg("GBP_INS_LON_V13_MT", _mt(_inside(1.3, 8.0, 12.0)), "GBPUSD")
_reg("GBP_INS_V12_1014_MT", _mt(_inside(1.2, 10.0, 14.0)), "GBPUSD")
_reg("GBP_NR5_H4", s_xau_nr5_h4, "GBPUSD")
_reg("GBP_NR5_H4_MT", s_xau_nr5_mt, "GBPUSD")
_reg("GBP_NR5_V14_MT", _mt(_nr5(1.4, 10.0, 14.0)), "GBPUSD")
_reg("GBP_NR5_LON_MT", _mt(s_nr5_lon_h4), "GBPUSD")
_reg("GBP_OF_L_D32_MT", make_of_window(9, 11, 0.32, (0, 1, 2, 3)), "GBPUSD")
_reg("GBP_OF_L_D34_H4_MT", _mt(lambda df, i, s: (
    (lambda b: b if b and _h4_ok(df.iloc[i], b["dir"]) else None)(
        make_of_window(9, 11, 0.34, (0, 1, 2, 3))(df, i, s)))), "GBPUSD")

# ════════════════════════════════════════════════════════════════════════════
# EUR — prioridade 2
# ════════════════════════════════════════════════════════════════════════════
_reg("EUR_NR5_H4", s_xau_nr5_h4, "EURUSD")
_reg("EUR_NR5_MT", s_xau_nr5_mt, "EURUSD")
_reg("EUR_NR5_V14_MT", _mt(_nr5(1.4, 10.0, 14.0)), "EURUSD")
_reg("EUR_NR5_V15_MT", _mt(_nr5(1.5, 10.0, 14.0)), "EURUSD")
_reg("EUR_NR5_GAP_MT", _mt(_gap(s_xau_nr5_h4, 0.06)), "EURUSD")
_reg("EUR_NR5_LON_MT", _mt(s_nr5_lon_h4), "EURUSD")
_reg("EUR_NR4_MT", _mt(s_nr4_h4), "EURUSD")
_reg("EUR_LH_047", make_london_handoff(0.47, 0.18, 1.75, (0, 1, 2, 3)),
     "EURUSD", one_per_day=True)
_reg("EUR_LH_047_D20", make_london_handoff(0.47, 0.20, 1.75, (0, 1, 2, 3)),
     "EURUSD", one_per_day=True)
_reg("EUR_LH_045_D19", make_london_handoff(0.45, 0.19, 1.75, (0, 1, 2, 3)),
     "EURUSD", one_per_day=True)
_reg("EUR_LH_049_D20", make_london_handoff(0.49, 0.20, 1.75, (0, 1, 2, 3)),
     "EURUSD", one_per_day=True)
_reg("EUR_OF_L_D30", make_of_window(9, 11, 0.30, (0, 1, 2, 3)), "EURUSD")
_reg("EUR_OF_L_D32_H4", _mt(lambda df, i, s: (
    (lambda b: b if b and _h4_ok(df.iloc[i], b["dir"]) else None)(
        make_of_window(9, 11, 0.32, (0, 1, 2, 3))(df, i, s)))), "EURUSD")
_reg("EUR_OF_L_D34_H4_MT", _mt(lambda df, i, s: (
    (lambda b: b if b and _h4_ok(df.iloc[i], b["dir"]) else None)(
        make_of_window(9, 11, 0.34, (0, 1, 2, 3))(df, i, s)))), "EURUSD")
_reg("EUR_INS_V15_MT", _mt(s_xau_inside_vol15_h4), "EURUSD")
_reg("EUR_INS_1015_MT", _mt(s_inside_1015_h4), "EURUSD")

# ════════════════════════════════════════════════════════════════════════════
# BTC — prioridade 3 (após INS_1015 live)
# ════════════════════════════════════════════════════════════════════════════
_reg("BTC_NR5_H4", s_xau_nr5_h4, "BTCUSD")
_reg("BTC_NR5_MT", s_xau_nr5_mt, "BTCUSD")
_reg("BTC_NR5_LON_H4", s_nr5_lon_h4, "BTCUSD")
_reg("BTC_NR5_LON_MT", _mt(s_nr5_lon_h4), "BTCUSD")
_reg("BTC_NR5_NY_H4", s_nr5_ny_h4, "BTCUSD")
_reg("BTC_NR5_NY_MT", _mt(s_nr5_ny_h4), "BTCUSD")
_reg("BTC_NR5_V14_MT", _mt(_nr5(1.4, 9.0, 14.0)), "BTCUSD")
_reg("BTC_NR5_GAP_MT", _mt(_gap(s_xau_nr5_h4, 0.08)), "BTCUSD")
_reg("BTC_NR4_H4", s_nr4_h4, "BTCUSD")
_reg("BTC_NR4_MT", _mt(s_nr4_h4), "BTCUSD")
_reg("BTC_PDH_H4", s_pdh_h4_vol12, "BTCUSD")
_reg("BTC_PDH_MT", _mt(s_pdh_h4_vol12), "BTCUSD")
_reg("BTC_INS_V13", s_inside_vol13_h4, "BTCUSD")
_reg("BTC_INS_V13_MT", _mt(s_inside_vol13_h4), "BTCUSD")
_reg("BTC_INS_V15_MT", _mt(s_xau_inside_vol15_h4), "BTCUSD")
_reg("BTC_INS_1015", s_inside_1015_h4, "BTCUSD")  # baseline GO live
_reg("BTC_INS_1015_MT", _mt(s_inside_1015_h4), "BTCUSD")


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
    ap.add_argument("--bars", type=int, default=300000)
    ap.add_argument("--syms", default="GBPUSD,EURUSD,BTCUSD")
    args = ap.parse_args()

    want = {s.strip().upper() for s in args.syms.split(",") if s.strip()}
    os.environ["KIMI_GROUP"] = "crypto"
    os.makedirs("logs", exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    logf = open(f"logs/backtest_setups_novos_v9_refine_{stamp}.txt", "w", encoding="utf-8")

    def out(s):
        try:
            print(s)
        except UnicodeEncodeError:
            print(s.encode("ascii", "replace").decode("ascii"))
        logf.write(s + "\n")
        logf.flush()

    strat_map = {n: fn for n, fn in STRATS.items() if STRAT_SYM[n] in want}
    out(f"{'#' * 74}\n# SETUPS NOVOS V9 — REFINO 🟡 ({len(strat_map)} setups) · "
        f"{datetime.now():%d/%m %H:%M}\n# Ativos: {sorted(want)}\n"
        f"# NÃO altera motores · GO rigoroso\n{'#' * 74}")

    from backtest_crypto_pro import mt5_connect, fetch as _cp_fetch
    mt5 = mt5_connect()

    needs = {}
    for name in strat_map:
        needs.setdefault((STRAT_SYM[name], STRAT_TF.get(name, 15)), []).append(name)

    raw_dfs = {}
    for (k, tf) in sorted(needs.keys()):
        t0 = time.time()
        raw = _cp_fetch(mt5, k, tf, args.bars)
        if raw is None or len(raw) < 2000:
            out(f"\n!! {k} M{tf}: sem histórico")
            continue
        raw_dfs[(k, tf)] = add_indicators(raw)
        anos = len(raw_dfs[(k, tf)]) / 96 / 365
        out(f"\n{'=' * 74}\n  {k} M{tf}: {len(raw_dfs[(k, tf)])} candles (~{anos:.1f}a)  "
            f"[{raw_dfs[(k, tf)].index[0].date()} -> {raw_dfs[(k, tf)].index[-1].date()}] "
            f"({time.time() - t0:.0f}s)\n{'=' * 74}")

    dfs = {key: add_extra_v2(df.copy(), key[0], ctx=None) for key, df in raw_dfs.items()}

    ranking = []
    for name, fn in strat_map.items():
        k = STRAT_SYM[name]
        tf = STRAT_TF.get(name, 15)
        key = (k, tf)
        if key not in dfs:
            out(f"\n  ▸ {name} ({k} M{tf}): sem dados")
            continue
        df = dfs[key]
        t0 = time.time()
        t = _sim(df, k, fn, name, tf=tf)
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
                out(f"      -> OUT-OF-SAMPLE {'SEGUROU OK' if seg else 'CAIU'} "
                    f"(net {s_out['net']:+.3f}, PF {s_out['PF']})")
            t.to_csv(f"logs/novos_v9_{k}_M{tf}_{name}.csv", index=False)
        v = verdict(s, cons, s_out)
        out(f"      VEREDITO: {v}")
        ranking.append({"setup": name, "sym": k, "tf": tf, **s, "cons%": cons, "veredito": v})

    out(f"\n{'#' * 74}\n  RANKING V9 REFINE\n{'#' * 74}")
    rk = pd.DataFrame(ranking)
    goes, yellows = [], []
    if not rk.empty:
        rk = rk.sort_values(["sym", "net"], ascending=[True, False])
        for _, r in rk.iterrows():
            if r.get("n", 0) == 0:
                out(f"  {r.sym:<7} M{int(r.tf)} {r.setup:<24} sem trades")
                continue
            out(f"  {r.sym:<7} M{int(r.tf)} {r.setup:<24} n={int(r.n):<5} net {r.net:+.3f} R  "
                f"PF {r.PF:<5} cons {r['cons%']}%  {r.veredito}")
            vs = str(r.veredito)
            tag = f"{r.sym}:{r.setup}"
            if "🟢" in vs and "GO" in vs:
                goes.append(tag)
            elif "🟡" in vs:
                yellows.append(tag)
        rk.to_csv(f"logs/backtest_setups_novos_v9_refine_ranking_{stamp}.csv", index=False)

    out(f"\n# GO encontrados: {goes if goes else 'nenhum'}")
    out(f"# amarelos: {yellows if yellows else 'nenhum'}")
    out(f"# Criterio: net>=+0,10 · PF>=1,25 · cons>=55% · OOS segura · n>=40")
    out(f"# FIM — logs/backtest_setups_novos_v9_refine_{stamp}.txt")
    logf.close()


if __name__ == "__main__":
    main()
