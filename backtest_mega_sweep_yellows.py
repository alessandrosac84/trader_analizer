"""
MEGA-SWEEP YELLOWS — refino dos 🟡 do mega-sweep até 🟢 GO ou 🔴.

Padrão v9: _mt · vol↑ (1.35/1.4) · janela mais estreita · one_per_day.
NÃO retesta o base amarelo sem mudança.
Régua GO idêntica. NÃO altera live até wire explícito.

Uso:
  python -u backtest_mega_sweep_yellows.py --group crypto --bars 300000
  python -u backtest_mega_sweep_yellows.py --group b3 --bars 300000
  python -u backtest_mega_sweep_yellows.py --group all
"""
from __future__ import annotations

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

from mega_sweep.catalog import SYMBOLS_CRYPTO, SYMBOLS_B3
from backtest_kimi_real import add_indicators
from backtest_setups_novos import stats, consistency, verdict, _line, OOS_FRAC
from backtest_setups_novos_v2 import add_extra_v2, _simulate
from backtest_setups_novos_v9_refine import _mt
from backtest_setups_novos_v3 import make_london_handoff
from backtest_setups_novos_v13_btc_24h import (
    _inside_h, _nr_h, _impulse_h, _h4_pb_ema_h, _outside_h, _hl_h,
)
from backtest_mega_sweep_gaps import _pdh_h, _load_ohlc

STRATS = {}
STRAT_SYM = {}
STRAT_TF = {}
ONE_PER_DAY = set()
BASE_YELLOW = {}  # nome_variante -> base amarelo


def _reg(name, fn, sym, base: str, tf=15, one_per_day=False):
    if name == base:
        raise ValueError(f"não retestar base sem mudança: {name}")
    STRATS[name] = fn
    STRAT_SYM[name] = sym
    BASE_YELLOW[name] = base
    if tf != 15:
        STRAT_TF[name] = tf
    if one_per_day:
        ONE_PER_DAY.add(name)


def _narrow(hh0, hh1, shrink=1.0):
    """Encolhe janela simétrica (em horas) sem inverter."""
    a, b = float(hh0), float(hh1)
    if b >= 24.0 and a <= 0.0:
        return 10.0, 16.0  # 24h → sessão diurna
    mid = (a + b) / 2.0
    half = max(1.0, (b - a) / 2.0 - shrink)
    return max(0.0, mid - half), min(24.0, mid + half)


# ── Yellow bases confirmados ──────────────────────────────────────────────
YELLOWS_CRYPTO = [
    "GBP_NR5_0915_H4", "XAU_HL_1014", "BTC_OUT_1015", "BTC_IMP_CONT_24H",
    "BTC_OUT_24H", "XAU_NR5_1016_H4", "GBP_NR5_1016_H4", "EUR_INS_0918_V13",
    "BTC_HL_24H", "BTC_PDH_1115", "GBP_HL_1014", "XAU_H4_PB_EMA_24H",
    "XAU_OUT_24H", "EUR_IMP_CONT_20", "ETH_LH_047", "GBP_NR4_1016_H4",
    "EUR_IMP_CONT_24H",
]
YELLOWS_B3 = ["WDO_OUT_1015", "WDO_IMP_CONT_20", "WDO_IMP_CONT_24H"]


def _build_yellow_variants(group: str):
    STRATS.clear()
    STRAT_SYM.clear()
    STRAT_TF.clear()
    ONE_PER_DAY.clear()
    BASE_YELLOW.clear()

    def _nr_vars(base, sym, n, vol, hh0, hh1):
        n0, n1 = _narrow(hh0, hh1, 1.0)
        n2, n3 = _narrow(hh0, hh1, 1.5)
        specs = [
            (f"{base}_MT", _mt(_nr_h(n, vol, hh0, hh1))),
            (f"{base}_V135", _nr_h(n, 1.35, hh0, hh1)),
            (f"{base}_V14", _nr_h(n, 1.4, hh0, hh1)),
            (f"{base}_V135_MT", _mt(_nr_h(n, 1.35, hh0, hh1))),
            (f"{base}_NAR", _nr_h(n, vol, n0, n1)),
            (f"{base}_NAR_MT", _mt(_nr_h(n, vol, n0, n1))),
            (f"{base}_NAR2_V14", _nr_h(n, 1.4, n2, n3)),
        ]
        for name, fn in specs:
            _reg(name, fn, sym, base)

    def _hl_vars(base, sym, vol, hh0, hh1):
        n0, n1 = _narrow(hh0, hh1, 1.0)
        specs = [
            (f"{base}_MT", _mt(_hl_h(vol, hh0, hh1))),
            (f"{base}_V135", _hl_h(1.35, hh0, hh1)),
            (f"{base}_V14", _hl_h(1.4, hh0, hh1)),
            (f"{base}_V14_MT", _mt(_hl_h(1.4, hh0, hh1))),
            (f"{base}_NAR", _hl_h(vol, n0, n1)),
            (f"{base}_NAR_MT", _mt(_hl_h(vol, n0, n1))),
        ]
        if hh0 <= 0 and hh1 >= 24:
            specs += [
                (f"{base}_1016", _hl_h(vol, 10.0, 16.0)),
                (f"{base}_1016_MT", _mt(_hl_h(vol, 10.0, 16.0))),
                (f"{base}_1014_V14", _hl_h(1.4, 10.0, 14.0)),
            ]
        for name, fn in specs:
            _reg(name, fn, sym, base)

    def _out_vars(base, sym, vol, hh0, hh1):
        n0, n1 = _narrow(hh0, hh1, 1.0)
        specs = [
            (f"{base}_MT", _mt(_outside_h(vol, hh0, hh1))),
            (f"{base}_V135", _outside_h(1.35, hh0, hh1)),
            (f"{base}_V14", _outside_h(1.4, hh0, hh1)),
            (f"{base}_V14_MT", _mt(_outside_h(1.4, hh0, hh1))),
            (f"{base}_NAR", _outside_h(vol, n0, n1)),
            (f"{base}_NAR_MT", _mt(_outside_h(vol, n0, n1))),
        ]
        if hh0 <= 0 and hh1 >= 24:
            specs += [
                (f"{base}_1015_V14", _outside_h(1.4, 10.0, 15.0)),
                (f"{base}_1115_MT", _mt(_outside_h(vol, 11.0, 15.0))),
            ]
        for name, fn in specs:
            _reg(name, fn, sym, base)

    def _imp_vars(base, sym, atr_m, vol_m, hh0, hh1, tp=2.0):
        n0, n1 = _narrow(hh0, hh1, 1.5)
        specs = [
            (f"{base}_MT", _mt(_impulse_h(atr_m, vol_m, hh0, hh1, tp))),
            (f"{base}_V16", _impulse_h(atr_m, 1.6, hh0, hh1, tp)),
            (f"{base}_V17", _impulse_h(atr_m, 1.7, hh0, hh1, tp)),
            (f"{base}_V16_MT", _mt(_impulse_h(atr_m, 1.6, hh0, hh1, tp))),
            (f"{base}_NAR", _impulse_h(atr_m, vol_m, n0, n1, tp)),
            (f"{base}_NAR_MT", _mt(_impulse_h(atr_m, vol_m, n0, n1, tp))),
            (f"{base}_ATR22_V16", _impulse_h(2.2, 1.6, hh0, hh1, tp)),
        ]
        if hh0 <= 0 and hh1 >= 24:
            specs += [
                (f"{base}_0918_V16", _impulse_h(atr_m, 1.6, 9.0, 18.0, tp)),
                (f"{base}_1016_MT", _mt(_impulse_h(atr_m, vol_m, 10.0, 16.0, tp))),
            ]
        for name, fn in specs:
            _reg(name, fn, sym, base)

    def _ins_vars(base, sym, vol, hh0, hh1):
        n0, n1 = _narrow(hh0, hh1, 2.0)
        specs = [
            (f"{base}_MT", _mt(_inside_h(vol, hh0, hh1))),
            (f"{base}_V135", _inside_h(1.35, hh0, hh1)),
            (f"{base}_V14", _inside_h(1.4, hh0, hh1)),
            (f"{base}_V14_MT", _mt(_inside_h(1.4, hh0, hh1))),
            (f"{base}_NAR", _inside_h(vol, n0, n1)),
            (f"{base}_NAR_MT", _mt(_inside_h(vol, n0, n1))),
            (f"{base}_1015_V14", _inside_h(1.4, 10.0, 15.0)),
            (f"{base}_AM_V14", _inside_h(1.4, 9.0, 12.0)),
        ]
        for name, fn in specs:
            _reg(name, fn, sym, base)

    def _pdh_vars(base, sym, vol, hh0, hh1):
        n0, n1 = _narrow(hh0, hh1, 0.5)
        specs = [
            (f"{base}_MT", _mt(_pdh_h(vol, hh0, hh1))),
            (f"{base}_V135", _pdh_h(1.35, hh0, hh1)),
            (f"{base}_V14", _pdh_h(1.4, hh0, hh1)),
            (f"{base}_V14_MT", _mt(_pdh_h(1.4, hh0, hh1))),
            (f"{base}_NAR", _pdh_h(vol, n0, n1)),
            (f"{base}_NAR_V14", _pdh_h(1.4, n0, n1)),
        ]
        for name, fn in specs:
            _reg(name, fn, sym, base)

    def _pb_vars(base, sym, hh0, hh1):
        specs = [
            (f"{base}_MT", _mt(_h4_pb_ema_h(hh0, hh1))),
            (f"{base}_1018", _h4_pb_ema_h(10.0, 18.0)),
            (f"{base}_1018_MT", _mt(_h4_pb_ema_h(10.0, 18.0))),
            (f"{base}_1016", _h4_pb_ema_h(10.0, 16.0)),
            (f"{base}_1016_MT", _mt(_h4_pb_ema_h(10.0, 16.0))),
            (f"{base}_1115", _h4_pb_ema_h(11.0, 15.0)),
        ]
        for name, fn in specs:
            _reg(name, fn, sym, base)

    if group in ("crypto", "all"):
        # NR5 / NR4
        _nr_vars("GBP_NR5_0915_H4", "GBPUSD", 5, 1.3, 9.0, 15.0)
        _nr_vars("XAU_NR5_1016_H4", "XAUUSD", 5, 1.3, 10.0, 16.0)
        _nr_vars("GBP_NR5_1016_H4", "GBPUSD", 5, 1.3, 10.0, 16.0)
        _nr_vars("GBP_NR4_1016_H4", "GBPUSD", 4, 1.3, 10.0, 16.0)
        # HL
        _hl_vars("XAU_HL_1014", "XAUUSD", 1.3, 10.0, 14.0)
        _hl_vars("GBP_HL_1014", "GBPUSD", 1.3, 10.0, 14.0)
        _hl_vars("BTC_HL_24H", "BTCUSD", 1.3, 0.0, 24.0)
        # OUT
        _out_vars("BTC_OUT_1015", "BTCUSD", 1.3, 10.0, 15.0)
        _out_vars("BTC_OUT_24H", "BTCUSD", 1.3, 0.0, 24.0)
        _out_vars("XAU_OUT_24H", "XAUUSD", 1.3, 0.0, 24.0)
        # IMP
        _imp_vars("BTC_IMP_CONT_24H", "BTCUSD", 2.0, 1.5, 0.0, 24.0)
        _imp_vars("EUR_IMP_CONT_20", "EURUSD", 2.0, 1.5, 9.0, 18.0)
        _imp_vars("EUR_IMP_CONT_24H", "EURUSD", 2.0, 1.5, 0.0, 24.0)
        # INS
        _ins_vars("EUR_INS_0918_V13", "EURUSD", 1.3, 9.0, 18.0)
        # PDH
        _pdh_vars("BTC_PDH_1115", "BTCUSD", 1.2, 11.0, 15.0)
        # PB EMA
        _pb_vars("XAU_H4_PB_EMA_24H", "XAUUSD", 0.0, 24.0)
        # LH ETH — já one_per_day; variar thresholds / MT dias
        _reg("ETH_LH_047_D20", make_london_handoff(0.47, 0.20, 1.65),
             "ETHUSD", "ETH_LH_047", one_per_day=True)
        _reg("ETH_LH_045_D18", make_london_handoff(0.45, 0.18, 1.65),
             "ETHUSD", "ETH_LH_047", one_per_day=True)
        _reg("ETH_LH_050_D18", make_london_handoff(0.50, 0.18, 1.65),
             "ETHUSD", "ETH_LH_047", one_per_day=True)
        _reg("ETH_LH_047_TP175", make_london_handoff(0.47, 0.18, 1.75),
             "ETHUSD", "ETH_LH_047", one_per_day=True)
        _reg("ETH_LH_047_MT", make_london_handoff(0.47, 0.18, 1.65, (0, 1, 2, 3)),
             "ETHUSD", "ETH_LH_047", one_per_day=True)
        _reg("ETH_LH_045_D20_MT", make_london_handoff(0.45, 0.20, 1.65, (0, 1, 2, 3)),
             "ETHUSD", "ETH_LH_047", one_per_day=True)

    if group in ("b3", "all"):
        _out_vars("WDO_OUT_1015", "WDO$D", 1.3, 10.0, 15.0)
        _imp_vars("WDO_IMP_CONT_20", "WDO$D", 2.0, 1.5, 9.0, 18.0)
        _imp_vars("WDO_IMP_CONT_24H", "WDO$D", 2.0, 1.5, 0.0, 24.0)
        # extras B3: one_per_day no OUT e IMP
        _reg("WDO_OUT_1015_1D", _outside_h(1.3, 10.0, 15.0),
             "WDO$D", "WDO_OUT_1015", one_per_day=True)
        _reg("WDO_OUT_1015_V14_1D", _outside_h(1.4, 10.0, 15.0),
             "WDO$D", "WDO_OUT_1015", one_per_day=True)
        _reg("WDO_IMP_CONT_20_1D", _impulse_h(2.0, 1.5, 9.0, 18.0, 2.0),
             "WDO$D", "WDO_IMP_CONT_20", one_per_day=True)
        _reg("WDO_IMP_CONT_20_V16_1D", _impulse_h(2.0, 1.6, 9.0, 18.0, 2.0),
             "WDO$D", "WDO_IMP_CONT_20", one_per_day=True)


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
    ap.add_argument("--group", choices=["crypto", "b3", "all"], default="all")
    ap.add_argument("--list-only", action="store_true")
    args = ap.parse_args()

    _build_yellow_variants(args.group)
    if args.list_only:
        print(f"Variantes yellow ({len(STRATS)}):")
        for n, sym in sorted(STRAT_SYM.items(), key=lambda x: (x[1], BASE_YELLOW[x[0]], x[0])):
            print(f"  {sym:<10} {BASE_YELLOW[n]:<22} -> {n}")
        return

    os.makedirs("logs", exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    log_path = f"logs/backtest_mega_sweep_yellows_{args.group}_{stamp}.txt"
    logf = open(log_path, "w", encoding="utf-8")

    def out(s=""):
        try:
            print(s)
        except UnicodeEncodeError:
            print(s.encode("ascii", "replace").decode("ascii"))
        logf.write(s + "\n")
        logf.flush()

    bases = sorted(set(BASE_YELLOW.values()))
    out(f"{'#' * 74}\n# MEGA-SWEEP YELLOWS — {len(STRATS)} variantes · "
        f"{len(bases)} bases 🟡 · group={args.group} · {datetime.now():%d/%m %H:%M}\n"
        f"# Bases: {bases}\n"
        f"# Filtros: _mt · vol↑ · janela estreita · 1/dia (B3)\n"
        f"# NÃO retesta base sem mudança · NÃO altera live\n{'#' * 74}")

    mt5 = None
    needs = sorted({STRAT_SYM[n] for n in STRATS})
    need_crypto = [s for s in needs if s in SYMBOLS_CRYPTO]
    if need_crypto:
        try:
            from backtest_crypto_pro import mt5_connect
            mt5 = mt5_connect()
        except Exception as exc:
            out(f"!! MT5 crypto: {exc} — tentando fallback CSV")

    raw_dfs = {}
    for sym in needs:
        t0 = time.time()
        if sym in SYMBOLS_B3:
            os.environ["KIMI_GROUP"] = "b3"
        else:
            os.environ["KIMI_GROUP"] = "crypto"
        raw = _load_ohlc(mt5, sym, 15, args.bars, out)
        if raw is None or len(raw) < 2000:
            out(f"\n!! {sym} M15: sem histórico")
            continue
        raw_dfs[sym] = add_indicators(raw)
        anos = len(raw_dfs[sym]) / 96 / 365
        out(f"\n{'=' * 74}\n  {sym} M15: {len(raw_dfs[sym])} candles (~{anos:.1f}a)  "
            f"[{raw_dfs[sym].index[0].date()} -> {raw_dfs[sym].index[-1].date()}] "
            f"({time.time() - t0:.0f}s)\n{'=' * 74}")

    dfs = {sym: add_extra_v2(df.copy(), sym, ctx=None) for sym, df in raw_dfs.items()}
    ranking = []
    for name, fn in STRATS.items():
        sym = STRAT_SYM[name]
        if sym not in dfs:
            out(f"\n  ▸ {name} ({sym}): sem dados")
            continue
        df = dfs[sym]
        t0 = time.time()
        try:
            t = _sim(df, sym, fn, name, tf=15)
        except Exception as exc:
            out(f"\n  ▸ {name}  ERRO: {exc}")
            continue
        s = stats(t)
        cons = consistency(t)
        out(f"\n  ▸ {name}  (base {BASE_YELLOW[name]} · {sym} M15)   [{time.time() - t0:.0f}s]")
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
            t.to_csv(f"logs/mega_yellows_{sym.replace('$', '')}_M15_{name}.csv", index=False)
        v = verdict(s, cons, s_out)
        out(f"      VEREDITO: {v}")
        ranking.append({
            "setup": name, "base": BASE_YELLOW[name], "sym": sym, "tf": 15,
            **s, "cons%": cons, "veredito": v, "oos_net": s_out.get("net"),
        })

    out(f"\n{'#' * 74}\n  RANKING MEGA-SWEEP YELLOWS ({args.group})\n{'#' * 74}")
    rk = pd.DataFrame(ranking)
    goes, still_y, reds = [], [], []
    if not rk.empty:
        rk = rk.sort_values("net", ascending=False)
        for _, r in rk.iterrows():
            if r.get("n", 0) == 0:
                out(f"  {r.setup:<36} sem trades")
                continue
            out(f"  {r.setup:<36} n={int(r.n):<5} net {r.net:+.3f} R  "
                f"PF {r.PF:<5} cons {r['cons%']}%  OOS {r.oos_net}  {r.veredito}")
            vs = str(r.veredito)
            if "🟢" in vs and "GO" in vs:
                goes.append(r.setup)
            elif "🟡" in vs:
                still_y.append(r.setup)
            else:
                reds.append(r.setup)
        rk.to_csv(f"logs/backtest_mega_sweep_yellows_{args.group}_ranking_{stamp}.csv",
                  index=False)

    # resumo por base
    out(f"\n# Resumo por base 🟡:")
    for base in bases:
        sub = [r for r in ranking if r["base"] == base]
        g = [r["setup"] for r in sub if "🟢" in str(r["veredito"]) and "GO" in str(r["veredito"])]
        y = [r["setup"] for r in sub if "🟡" in str(r["veredito"])]
        best = "🔴 todas"
        if g:
            best = f"🟢 {g[0]}"
        elif y:
            best = f"🟡 ainda ({y[0]})"
        out(f"  {base:<22} → {best}  (tested {len(sub)})")

    out(f"\n# GO encontrados: {goes if goes else 'nenhum'}")
    out(f"# ainda amarelos: {still_y if still_y else 'nenhum'}")
    out(f"# vermelhos/amostra: {len(reds)}")
    out(f"# testados: {len(ranking)}  |  GO: {len(goes)}  |  🟡: {len(still_y)}  |  🔴+: {len(reds)}")
    out(f"# FIM — {log_path}")
    logf.close()
    print(f"\n-> {log_path}")


if __name__ == "__main__":
    main()
