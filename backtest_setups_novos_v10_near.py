"""
backtest_setups_novos_v10_near.py — RODADA 10: empurrar quase-GOs.

EUR zero paths (LH_047 OOS +0,076 vs +0,08). GBP/BTC/ETH poucos paths.
Régua intacta. NÃO altera live até 🟢.

Uso:
  python backtest_setups_novos_v10_near.py
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
from backtest_setups_novos_v5 import _nr_break, _h4_ok
from backtest_setups_novos_v6 import (
    s_xau_nr5_h4, s_xau_nr5_mt, s_nr4_h4, s_inside_1015_h4, s_inside_vol13_h4,
    s_pdh_h4_vol12, s_xau_inside_vol15_h4,
)
from backtest_setups_novos_v8_fxcrypto import s_nr5_lon_h4, s_nr5_ny_h4
from backtest_setups_novos_v9_refine import _mt, _inside, _nr5

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


def _of_h4(h0, h1, dmin, dows=None):
    base_fn = make_of_window(h0, h1, dmin, dows)

    def _f(df, i, sym):
        b = base_fn(df, i, sym)
        if not b or not _h4_ok(df.iloc[i], b["dir"]):
            return None
        return b
    return _f


# ── EUR (prioridade: zero GOs; LH_047 quase) ───────────────────────────────
_reg("EUR_LH_047", make_london_handoff(0.47, 0.18, 1.75, (0, 1, 2, 3)),
     "EURUSD", one_per_day=True)
_reg("EUR_LH_047_TP165", make_london_handoff(0.47, 0.18, 1.65, (0, 1, 2, 3)),
     "EURUSD", one_per_day=True)
_reg("EUR_LH_047_TP185", make_london_handoff(0.47, 0.18, 1.85, (0, 1, 2, 3)),
     "EURUSD", one_per_day=True)
_reg("EUR_LH_046_D18", make_london_handoff(0.46, 0.18, 1.75, (0, 1, 2, 3)),
     "EURUSD", one_per_day=True)
_reg("EUR_LH_046_D19", make_london_handoff(0.46, 0.19, 1.75, (0, 1, 2, 3)),
     "EURUSD", one_per_day=True)
_reg("EUR_LH_048_D18", make_london_handoff(0.48, 0.18, 1.75, (0, 1, 2, 3)),
     "EURUSD", one_per_day=True)
_reg("EUR_LH_047_D185", make_london_handoff(0.47, 0.185, 1.75, (0, 1, 2, 3)),
     "EURUSD", one_per_day=True)
_reg("EUR_LH_044_D18", make_london_handoff(0.44, 0.18, 1.80, (0, 1, 2, 3)),
     "EURUSD", one_per_day=True)
_reg("EUR_LH_047_ALL", make_london_handoff(0.47, 0.18, 1.75, None),
     "EURUSD", one_per_day=True)  # incl. sexta

_reg("EUR_NR5_H4", s_xau_nr5_h4, "EURUSD")
_reg("EUR_NR5_MT", s_xau_nr5_mt, "EURUSD")
_reg("EUR_NR5_V135_MT", _mt(_nr5(1.35, 10.0, 14.0)), "EURUSD")
_reg("EUR_NR5_1014_MT", _mt(_nr5(1.3, 10.0, 14.0)), "EURUSD")
_reg("EUR_NR5_0913_MT", _mt(_nr5(1.3, 9.0, 13.0)), "EURUSD")
_reg("EUR_NR4_V13_MT", _mt(lambda df, i, s: (
    (lambda b: b if b and _h4_ok(df.iloc[i], b["dir"]) else None)(
        _nr_break(df, i, n=4, vol_mult=1.3, hh0=10.0, hh1=14.0)))), "EURUSD")

_reg("EUR_OF_L_D30", make_of_window(9, 11, 0.30, (0, 1, 2, 3)), "EURUSD")
_reg("EUR_OF_L_D31_H4", _of_h4(9, 11, 0.31, (0, 1, 2, 3)), "EURUSD")
_reg("EUR_OF_L_D32_H4", _of_h4(9, 11, 0.32, (0, 1, 2, 3)), "EURUSD")
_reg("EUR_OF_L_D33_H4_MT", _mt(_of_h4(9, 11, 0.33)), "EURUSD")
_reg("EUR_OF_L_D28_H4", _of_h4(9, 11, 0.28, (0, 1, 2, 3)), "EURUSD")
_reg("EUR_OF_NY_D30_H4", _of_h4(14, 17, 0.30, (0, 1, 2, 3)), "EURUSD")

_reg("EUR_INS_V15", s_xau_inside_vol15_h4, "EURUSD")
_reg("EUR_INS_AM_V13", _inside(1.3, 9.0, 12.0), "EURUSD")  # espelho do GBP GO
_reg("EUR_INS_AM_V13_MT", _mt(_inside(1.3, 9.0, 12.0)), "EURUSD")
_reg("EUR_INS_V13_MT", _mt(s_inside_vol13_h4), "EURUSD")

# ── GBP (expandir além de AM_V13) ──────────────────────────────────────────
_reg("GBP_INS_AM_V13", _inside(1.3, 9.0, 12.0), "GBPUSD")  # baseline GO
_reg("GBP_INS_V13", s_inside_vol13_h4, "GBPUSD")
_reg("GBP_INS_V13_MT", _mt(s_inside_vol13_h4), "GBPUSD")
_reg("GBP_INS_V135_1014", _inside(1.35, 10.0, 14.0), "GBPUSD")
_reg("GBP_INS_V135_MT", _mt(_inside(1.35, 10.0, 14.0)), "GBPUSD")
_reg("GBP_INS_1015_V13", _inside(1.3, 10.0, 15.0), "GBPUSD")
_reg("GBP_INS_1015_V135", _inside(1.35, 10.0, 15.0), "GBPUSD")
_reg("GBP_INS_1015_V14_MT", _mt(_inside(1.4, 10.0, 15.0)), "GBPUSD")
_reg("GBP_INS_V12_1013_MT", _mt(_inside(1.2, 10.0, 13.0)), "GBPUSD")
_reg("GBP_INS_AM_V14", _inside(1.4, 9.0, 12.0), "GBPUSD")
_reg("GBP_INS_AM_V125", _inside(1.25, 9.0, 12.0), "GBPUSD")
_reg("GBP_NR5_V14_0914_MT", _mt(_nr5(1.4, 9.0, 14.0)), "GBPUSD")
_reg("GBP_OF_L_D33_H4", _of_h4(9, 11, 0.33, (0, 1, 2, 3)), "GBPUSD")
_reg("GBP_LH_047", make_london_handoff(0.47, 0.18, 1.75, (0, 1, 2, 3)),
     "GBPUSD", one_per_day=True)
_reg("GBP_LH_046_D19", make_london_handoff(0.46, 0.19, 1.75, (0, 1, 2, 3)),
     "GBPUSD", one_per_day=True)

# ── BTC (além de INS_1015) ─────────────────────────────────────────────────
_reg("BTC_INS_1015", s_inside_1015_h4, "BTCUSD")
_reg("BTC_NR5_LON_H4", s_nr5_lon_h4, "BTCUSD")
_reg("BTC_NR5_LON_V14", _nr5(1.4, 8.0, 12.0), "BTCUSD")
_reg("BTC_NR5_LON_V14_MT", _mt(_nr5(1.4, 8.0, 12.0)), "BTCUSD")
_reg("BTC_NR5_NY_H4", s_nr5_ny_h4, "BTCUSD")
_reg("BTC_NR5_NY_V14", _nr5(1.4, 14.0, 17.0), "BTCUSD")
_reg("BTC_NR5_H4", s_xau_nr5_h4, "BTCUSD")
_reg("BTC_NR5_V135", _nr5(1.35, 9.0, 14.0), "BTCUSD")
_reg("BTC_NR4_H4", s_nr4_h4, "BTCUSD")
_reg("BTC_NR4_V13", lambda df, i, s: (
    (lambda b: b if b and _h4_ok(df.iloc[i], b["dir"]) else None)(
        _nr_break(df, i, n=4, vol_mult=1.3, hh0=10.0, hh1=14.0))), "BTCUSD")
_reg("BTC_PDH_H4", s_pdh_h4_vol12, "BTCUSD")
_reg("BTC_PDH_V13", lambda df, i, s: _pdh(df, i, 1.3), "BTCUSD")
_reg("BTC_INS_AM_V13", _inside(1.3, 9.0, 12.0), "BTCUSD")
_reg("BTC_INS_V13_1014", _inside(1.3, 10.0, 14.0), "BTCUSD")
_reg("BTC_INS_1015_V13", _inside(1.3, 10.0, 15.0), "BTCUSD")

# ── ETH (poucos paths — só variantes que não duplicam live) ────────────────
_reg("ETH_INS_AM_V13", _inside(1.3, 9.0, 12.0), "ETHUSD")
_reg("ETH_INS_V13_MT", _mt(s_inside_vol13_h4), "ETHUSD")
_reg("ETH_INS_1015_V13", _inside(1.3, 10.0, 15.0), "ETHUSD")
_reg("ETH_NR5_NY_V14", _nr5(1.4, 14.0, 17.0), "ETHUSD")
_reg("ETH_LH_047", make_london_handoff(0.47, 0.18, 1.75, (0, 1, 2, 3)),
     "ETHUSD", one_per_day=True)
_reg("ETH_OF_L_D32_H4", _of_h4(9, 11, 0.32, (0, 1, 2, 3)), "ETHUSD")


def _pdh(df, i, vol=1.3):
    if i < 2:
        return None
    r, p = df.iloc[i], df.iloc[i - 1]
    if not (11.0 <= r.hh < 15.0):
        return None
    if pd.isna(r.prev_day_hi) or pd.isna(r.prev_day_lo):
        return None
    if r.Volume < vol * (r.vol8 or 0):
        return None
    base = None
    if r.Close > r.prev_day_hi and p.Close <= r.prev_day_hi:
        base = _sig("COMPRA", r.Close, r.prev_day_hi - (r.atr or 50) * 0.3, 1.5, 2.5, True)
    elif r.Close < r.prev_day_lo and p.Close >= r.prev_day_lo:
        base = _sig("VENDA", r.Close, r.prev_day_lo + (r.atr or 50) * 0.3, 1.5, 2.5, True)
    if not base or not _h4_ok(r, base["dir"]):
        return None
    return base


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
    ap.add_argument("--syms", default="EURUSD,GBPUSD,BTCUSD,ETHUSD")
    args = ap.parse_args()

    want = {s.strip().upper() for s in args.syms.split(",") if s.strip()}
    os.environ["KIMI_GROUP"] = "crypto"
    os.makedirs("logs", exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    logf = open(f"logs/backtest_setups_novos_v10_near_{stamp}.txt", "w", encoding="utf-8")

    def out(s):
        try:
            print(s)
        except UnicodeEncodeError:
            print(s.encode("ascii", "replace").decode("ascii"))
        logf.write(s + "\n")
        logf.flush()

    strat_map = {n: fn for n, fn in STRATS.items() if STRAT_SYM[n] in want}
    out(f"{'#' * 74}\n# SETUPS NOVOS V10 — NEAR-GO ({len(strat_map)} setups) · "
        f"{datetime.now():%d/%m %H:%M}\n# Ativos: {sorted(want)}\n"
        f"# NÃO altera motores · GO rigoroso\n{'#' * 74}")

    from backtest_crypto_pro import mt5_connect, fetch as _cp_fetch
    mt5 = mt5_connect()

    needs = {}
    for name in strat_map:
        needs.setdefault((STRAT_SYM[name], 15), []).append(name)

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
        key = (k, 15)
        if key not in dfs:
            out(f"\n  ▸ {name} ({k}): sem dados")
            continue
        df = dfs[key]
        t0 = time.time()
        t = _sim(df, k, fn, name, tf=15)
        s = stats(t)
        cons = consistency(t)
        out(f"\n  ▸ {name}  ({k} M15)   [{time.time() - t0:.0f}s]")
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
            t.to_csv(f"logs/novos_v10_{k}_M15_{name}.csv", index=False)
        v = verdict(s, cons, s_out)
        out(f"      VEREDITO: {v}")
        ranking.append({"setup": name, "sym": k, "tf": 15, **s, "cons%": cons, "veredito": v})

    out(f"\n{'#' * 74}\n  RANKING V10\n{'#' * 74}")
    rk = pd.DataFrame(ranking)
    goes, yellows = [], []
    if not rk.empty:
        rk = rk.sort_values(["sym", "net"], ascending=[True, False])
        for _, r in rk.iterrows():
            if r.get("n", 0) == 0:
                out(f"  {r.sym:<7} {r.setup:<24} sem trades")
                continue
            out(f"  {r.sym:<7} {r.setup:<24} n={int(r.n):<5} net {r.net:+.3f} R  "
                f"PF {r.PF:<5} cons {r['cons%']}%  {r.veredito}")
            vs = str(r.veredito)
            tag = f"{r.sym}:{r.setup}"
            if "🟢" in vs and "GO" in vs:
                goes.append(tag)
            elif "🟡" in vs:
                yellows.append(tag)
        rk.to_csv(f"logs/backtest_setups_novos_v10_near_ranking_{stamp}.csv", index=False)

    out(f"\n# GO encontrados: {goes if goes else 'nenhum'}")
    out(f"# amarelos: {yellows if yellows else 'nenhum'}")
    out(f"# FIM — logs/backtest_setups_novos_v10_near_{stamp}.txt")
    logf.close()


if __name__ == "__main__":
    main()
