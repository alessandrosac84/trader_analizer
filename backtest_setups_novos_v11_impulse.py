"""
backtest_setups_novos_v11_impulse.py — RODADA 11: impulso / queda M15.

Famílias inspiradas em quedas violentas (continuação + NR/inside + engulf).
Régua GO intacta. NÃO altera live até 🟢.

Uso:
  python backtest_setups_novos_v11_impulse.py
  python backtest_setups_novos_v11_impulse.py --syms BTCUSD,ETHUSD
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
from backtest_setups_novos_v5 import _nr_break, _h4_ok
from backtest_setups_novos_v9_refine import _mt, _inside

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


def _impulse_cont(atr_mult=1.8, vol_mult=1.4, hh0=9.0, hh1=18.0, need_h4=True, tp_r=2.0):
    """Continuação após barra de impulso (range ≥ atr_mult×ATR + vol)."""
    def _f(df, i, sym):
        if i < 3:
            return None
        r, p = df.iloc[i], df.iloc[i - 1]
        if not (hh0 <= float(r.hh) < hh1):
            return None
        atr = float(p.atr or 0)
        if atr <= 0:
            return None
        rng = float(p.High - p.Low)
        if rng < atr_mult * atr:
            return None
        if float(p.Volume or 0) < vol_mult * float(p.vol8 or 0):
            return None
        down = float(p.Close) < float(p.Open)
        up = float(p.Close) > float(p.Open)
        if not (down or up):
            return None
        d = "VENDA" if down else "COMPRA"
        if need_h4 and not _h4_ok(r, d):
            return None
        # confirmação: rompe extremo da barra de impulso
        if d == "VENDA" and float(r.Close) >= float(p.Low):
            return None
        if d == "COMPRA" and float(r.Close) <= float(p.High):
            return None
        if float(r.Volume or 0) < 1.1 * float(r.vol8 or 0):
            return None
        sl = float(p.High) if d == "VENDA" else float(p.Low)
        return _sig(d, float(r.Close), sl, 1.0, tp_r, True)
    return _f


def _engulf_after_impulse(atr_mult=1.6, vol_mult=1.3, hh0=9.0, hh1=18.0, need_h4=True):
    """Engolfo contrário após impulso (fade de exaustão)."""
    def _f(df, i, sym):
        if i < 3:
            return None
        r, p = df.iloc[i], df.iloc[i - 1]
        if not (hh0 <= float(r.hh) < hh1):
            return None
        atr = float(p.atr or 0)
        if atr <= 0:
            return None
        if float(p.High - p.Low) < atr_mult * atr:
            return None
        if float(p.Volume or 0) < vol_mult * float(p.vol8 or 0):
            return None
        # engulf: candle atual engole o impulso
        if not (float(r.High) >= float(p.High) and float(r.Low) <= float(p.Low)):
            return None
        if float(r.Volume or 0) < 1.2 * float(r.vol8 or 0):
            return None
        down_imp = float(p.Close) < float(p.Open)
        # fade: contra o impulso
        d = "COMPRA" if down_imp else "VENDA"
        if need_h4 and not _h4_ok(r, d):
            return None
        if d == "COMPRA" and float(r.Close) <= float(r.Open):
            return None
        if d == "VENDA" and float(r.Close) >= float(r.Open):
            return None
        sl = float(p.Low) if d == "COMPRA" else float(p.High)
        return _sig(d, float(r.Close), sl, 1.0, 1.75, True)
    return _f


def _nr_h4(n=5, vol=1.3, hh0=10.0, hh1=16.0):
    def _f(df, i, sym):
        b = _nr_break(df, i, n=n, vol_mult=vol, hh0=hh0, hh1=hh1)
        if not b or not _h4_ok(df.iloc[i], b["dir"]):
            return None
        return b
    return _f


# ── BTC (prioridade: quedas M15) ───────────────────────────────────────────
_reg("BTC_IMP_CONT_18", _impulse_cont(1.8, 1.4, 9, 18, True, 2.0), "BTCUSD")
_reg("BTC_IMP_CONT_20", _impulse_cont(2.0, 1.5, 9, 18, True, 2.0), "BTCUSD")
_reg("BTC_IMP_CONT_22", _impulse_cont(2.2, 1.5, 10, 17, True, 2.0), "BTCUSD")
_reg("BTC_IMP_CONT_MT", _mt(_impulse_cont(1.8, 1.4, 9, 18, True, 2.0)), "BTCUSD")
_reg("BTC_IMP_TP25", _impulse_cont(1.8, 1.4, 9, 18, True, 2.5), "BTCUSD")
_reg("BTC_ENG_IMP", _engulf_after_impulse(1.6, 1.3, 9, 18, True), "BTCUSD")
_reg("BTC_ENG_IMP_MT", _mt(_engulf_after_impulse(1.6, 1.3, 9, 18, True)), "BTCUSD")
_reg("BTC_NR5_1016_H4", _nr_h4(5, 1.3, 10, 16), "BTCUSD")
_reg("BTC_NR7_1016_H4", _nr_h4(7, 1.3, 10, 16), "BTCUSD")
_reg("BTC_INS_0918_V13", _inside(1.3, 9, 18), "BTCUSD")
_reg("BTC_INS_1017_V13", _inside(1.3, 10, 17), "BTCUSD")
_reg("BTC_INS_AM_V15", _inside(1.5, 9, 12), "BTCUSD")

# ── ETH ────────────────────────────────────────────────────────────────────
_reg("ETH_IMP_CONT_18", _impulse_cont(1.8, 1.4, 9, 18, True, 2.0), "ETHUSD")
_reg("ETH_IMP_CONT_20", _impulse_cont(2.0, 1.5, 9, 18, True, 2.0), "ETHUSD")
_reg("ETH_IMP_CONT_MT", _mt(_impulse_cont(1.8, 1.4, 9, 18, True, 2.0)), "ETHUSD")
_reg("ETH_ENG_IMP", _engulf_after_impulse(1.6, 1.3, 9, 18, True), "ETHUSD")
_reg("ETH_NR5_1016_H4", _nr_h4(5, 1.3, 10, 16), "ETHUSD")
_reg("ETH_INS_0918_V13", _inside(1.3, 9, 18), "ETHUSD")

# ── XAU ────────────────────────────────────────────────────────────────────
_reg("XAU_IMP_CONT_18", _impulse_cont(1.8, 1.4, 9, 18, True, 2.0), "XAUUSD")
_reg("XAU_IMP_CONT_20", _impulse_cont(2.0, 1.5, 9, 18, True, 2.0), "XAUUSD")
_reg("XAU_ENG_IMP", _engulf_after_impulse(1.6, 1.3, 9, 18, True), "XAUUSD")
_reg("XAU_NR5_1016_H4", _nr_h4(5, 1.3, 10, 16), "XAUUSD")

# ── EUR / GBP ──────────────────────────────────────────────────────────────
_reg("EUR_IMP_CONT_18", _impulse_cont(1.8, 1.4, 9, 17, True, 2.0), "EURUSD")
_reg("EUR_NR5_1016_H4", _nr_h4(5, 1.3, 10, 16), "EURUSD")
_reg("EUR_INS_0918_V13", _inside(1.3, 9, 18), "EURUSD")
_reg("GBP_IMP_CONT_18", _impulse_cont(1.8, 1.4, 9, 17, True, 2.0), "GBPUSD")
_reg("GBP_NR5_1016_H4", _nr_h4(5, 1.3, 10, 16), "GBPUSD")
_reg("GBP_INS_1017_V13", _inside(1.3, 10, 17), "GBPUSD")


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bars", type=int, default=300000)
    ap.add_argument("--syms", default="BTCUSD,ETHUSD,XAUUSD,EURUSD,GBPUSD")
    args = ap.parse_args()

    want = {s.strip().upper() for s in args.syms.split(",") if s.strip()}
    os.environ["KIMI_GROUP"] = "crypto"
    os.makedirs("logs", exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    logf = open(f"logs/backtest_setups_novos_v11_impulse_{stamp}.txt", "w", encoding="utf-8")

    def out(s):
        try:
            print(s)
        except UnicodeEncodeError:
            print(s.encode("ascii", "replace").decode("ascii"))
        logf.write(s + "\n")
        logf.flush()

    strat_map = {n: fn for n, fn in STRATS.items() if STRAT_SYM[n] in want}
    out(f"{'#' * 74}\n# SETUPS NOVOS V11 — IMPULSE/DROP ({len(strat_map)} setups) · "
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
            t.to_csv(f"logs/novos_v11_{k}_M15_{name}.csv", index=False)
        v = verdict(s, cons, s_out)
        out(f"      VEREDITO: {v}")
        ranking.append({"setup": name, "sym": k, "tf": 15, **s, "cons%": cons, "veredito": v})

    out(f"\n{'#' * 74}\n  RANKING V11\n{'#' * 74}")
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
        rk.to_csv(f"logs/backtest_setups_novos_v11_impulse_ranking_{stamp}.csv", index=False)

    out(f"\n# GO encontrados: {goes if goes else 'nenhum'}")
    out(f"# amarelos: {yellows if yellows else 'nenhum'}")
    out(f"# FIM — logs/backtest_setups_novos_v11_impulse_{stamp}.txt")
    logf.close()


if __name__ == "__main__":
    main()
