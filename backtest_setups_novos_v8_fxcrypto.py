"""
backtest_setups_novos_v8_fxcrypto.py — RODADA 8: ETH + BTC + EUR + GBP.

Régua idêntica (custos, OOS 70/30, bimestre, GO). NÃO altera motores ao vivo.

Contexto:
  · WDO acabou de ganhar 3 GOs → pausa pesquisa B3
  · ETH só OF + INSIDE_H4 ao vivo
  · BTC/EUR/GBP sem path GO automático
  · XAU já coberto — fora desta rodada

Uso:
  python rodar_backtest_setups_novos_v8_fxcrypto.py
  python backtest_setups_novos_v8_fxcrypto.py --bars 300000
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
from backtest_setups_novos_v3 import (
    make_of_window, make_asia_break, make_round_fade, make_london_handoff,
)
from backtest_kimi_real import asset_key, TICK_SIZE
from backtest_setups_novos_v5 import (
    _nr_break, _h4_ok, s_xau_nr5, s_xau_inside_h4,
)
from backtest_setups_novos_v6 import (
    s_xau_nr5_h4, s_xau_nr5_mt, s_xau_inside_vol15_h4, s_xau_inside_am,
    s_nr4_h4, s_nr7_1014_h4, s_pdh_h4_vol12, s_inside_1015_h4,
    s_inside_vol13_h4,
)

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


# ── helpers extras (janelas crypto/FX) ───────────────────────────────────────

def _sym_tick(sym):
    return TICK_SIZE.get(asset_key(sym), 0.01)


def s_nr5_lon_h4(df, i, sym):
    """NR5 08–12 (Londres) + H4."""
    base = _nr_break(df, i, n=5, vol_mult=1.3, hh0=8.0, hh1=12.0)
    if not base:
        return None
    if not _h4_ok(df.iloc[i], base["dir"]):
        return None
    return base


def s_nr5_ny_h4(df, i, sym):
    """NR5 14–17 (NY) + H4."""
    base = _nr_break(df, i, n=5, vol_mult=1.3, hh0=14.0, hh1=17.0)
    if not base:
        return None
    if not _h4_ok(df.iloc[i], base["dir"]):
        return None
    return base


def s_inside_vol12_h4(df, i, sym):
    """Inside vol 1.2 + H4 · 9–14 (mais frequência que V15)."""
    if i < 3:
        return None
    r = df.iloc[i]
    p, pp = df.iloc[i - 1], df.iloc[i - 2]
    if not (9.0 <= r.hh < 14.0):
        return None
    if not (p.High <= pp.High and p.Low >= pp.Low):
        return None
    if r.Volume < 1.2 * (r.vol8 or 0):
        return None
    base = None
    if r.Close > p.High:
        base = _sig("COMPRA", r.Close, p.Low, 1.5, 2.5, True)
    elif r.Close < p.Low:
        base = _sig("VENDA", r.Close, p.High, 1.5, 2.5, True)
    if not base or not _h4_ok(r, base["dir"]):
        return None
    return base


def s_hl_h4(df, i, sym):
    """HL + H4 com tick do ativo (não o tick do WIN)."""
    if i < 5:
        return None
    r = df.iloc[i]
    if not (10.5 <= r.hh < 14.0):
        return None
    import numpy as np
    lows = df.Low.iloc[i - 4:i].values
    highs = df.High.iloc[i - 4:i].values
    if r.Volume < 1.25 * (r.vol8 or 0):
        return None
    tick = _sym_tick(sym)
    base = None
    if lows[1] > lows[0] and lows[2] > lows[1] and lows[3] > lows[2]:
        level = float(np.max(highs))
        if r.Close > level and df.iloc[i - 1].Close <= level:
            base = _sig("COMPRA", r.Close, float(lows[-1]) - tick, 1.5, 2.5, True)
    if highs[1] < highs[0] and highs[2] < highs[1] and highs[3] < highs[2]:
        level = float(np.min(lows))
        if r.Close < level and df.iloc[i - 1].Close >= level:
            base = _sig("VENDA", r.Close, float(highs[-1]) + tick, 1.5, 2.5, True)
    if not base or not _h4_ok(r, base["dir"]):
        return None
    return base


def s_hl_lon_h4(df, i, sym):
    if i < 5:
        return None
    r = df.iloc[i]
    if not (8.0 <= r.hh < 12.0):
        return None
    # reusa lógica HL sem janela 10:30–14
    import numpy as np
    lows = df.Low.iloc[i - 4:i].values
    highs = df.High.iloc[i - 4:i].values
    if r.Volume < 1.25 * (r.vol8 or 0):
        return None
    tick = _sym_tick(sym)
    base = None
    if lows[1] > lows[0] and lows[2] > lows[1] and lows[3] > lows[2]:
        level = float(np.max(highs))
        if r.Close > level and df.iloc[i - 1].Close <= level:
            base = _sig("COMPRA", r.Close, float(lows[-1]) - tick, 1.5, 2.5, True)
    if highs[1] < highs[0] and highs[2] < highs[1] and highs[3] < highs[2]:
        level = float(np.min(lows))
        if r.Close < level and df.iloc[i - 1].Close >= level:
            base = _sig("VENDA", r.Close, float(highs[-1]) + tick, 1.5, 2.5, True)
    if not base or not _h4_ok(r, base["dir"]):
        return None
    return base


def s_of_lon_h4(df, i, sym, dmin=0.30):
    base = make_of_window(9, 11, dmin, (0, 1, 2, 3))(df, i, sym)
    if not base:
        return None
    if not _h4_ok(df.iloc[i], base["dir"]):
        return None
    return base


def _pack_common(prefix, sym, rnd_step, rnd_d=0.07, rnd_near=0.14):
    """Família comum portável (NR/INSIDE/HL/PDH/LH/OF/RND)."""
    _reg(f"{prefix}_NR5_H4", s_xau_nr5_h4, sym)
    _reg(f"{prefix}_NR5_MT", s_xau_nr5_mt, sym)
    _reg(f"{prefix}_NR5_LON_H4", s_nr5_lon_h4, sym)
    _reg(f"{prefix}_NR5_NY_H4", s_nr5_ny_h4, sym)
    _reg(f"{prefix}_NR4_H4", s_nr4_h4, sym)
    _reg(f"{prefix}_NR7_1014_H4", s_nr7_1014_h4, sym)
    _reg(f"{prefix}_INS_H4", s_xau_inside_h4, sym)
    _reg(f"{prefix}_INS_V12_H4", s_inside_vol12_h4, sym)
    _reg(f"{prefix}_INS_V15_H4", s_xau_inside_vol15_h4, sym)
    _reg(f"{prefix}_INS_AM", s_xau_inside_am, sym)
    _reg(f"{prefix}_INS_1015", s_inside_1015_h4, sym)
    _reg(f"{prefix}_INS_V13", s_inside_vol13_h4, sym)
    _reg(f"{prefix}_HL_H4", s_hl_h4, sym)
    _reg(f"{prefix}_HL_LON_H4", s_hl_lon_h4, sym)
    _reg(f"{prefix}_PDH_H4", s_pdh_h4_vol12, sym)
    _reg(f"{prefix}_LH_049", make_london_handoff(0.49, 0.19, 1.75, (0, 1, 2, 3)),
         sym, one_per_day=True)
    _reg(f"{prefix}_LH_047", make_london_handoff(0.47, 0.18, 1.75, (0, 1, 2, 3)),
         sym, one_per_day=True)
    _reg(f"{prefix}_ASIA_043", make_asia_break(0.43, 0.55, 0.18, 1.75),
         sym, one_per_day=True)
    _reg(f"{prefix}_OF_L_D30", make_of_window(9, 11, 0.30, (0, 1, 2, 3)), sym)
    _reg(f"{prefix}_OF_L_D32_H4",
         lambda df, i, s, d=0.32: s_of_lon_h4(df, i, s, d), sym)
    _reg(f"{prefix}_RND_MT",
         make_round_fade(rnd_step, rnd_d, rnd_near, None, None, (0, 1, 2, 3)), sym)
    _reg(f"{prefix}_RND_LON",
         make_round_fade(rnd_step, rnd_d, rnd_near, 8, 12), sym)


# ── ETH (expandir além de OF + INSIDE) ──
_pack_common("ETH", "ETHUSD", rnd_step=100, rnd_d=0.07, rnd_near=0.14)
_reg("ETH_RND_50_MT", make_round_fade(50, 0.08, 0.15, None, None, (0, 1, 2, 3)), "ETHUSD")
_reg("ETH_OF_NY_D30", make_of_window(15.5, 17.5, 0.30, (0, 1, 2, 3)), "ETHUSD")

# ── BTC (histórico curto — GO se n≥40 e OOS) ──
_pack_common("BTC", "BTCUSD", rnd_step=500, rnd_d=0.07, rnd_near=0.14)
_reg("BTC_RND_1K_MT", make_round_fade(1000, 0.08, 0.15, None, None, (0, 1, 2, 3)), "BTCUSD")

# ── EURUSD (primeira bateria séria) ──
_pack_common("EUR", "EURUSD", rnd_step=0.0050, rnd_d=0.08, rnd_near=0.15)
_reg("EUR_RND_100_MT", make_round_fade(0.0100, 0.07, 0.14, None, None, (0, 1, 2, 3)), "EURUSD")
_reg("EUR_OF_NY_D28", make_of_window(14.0, 17.0, 0.28, (0, 1, 2, 3)), "EURUSD")

# ── GBPUSD ──
_pack_common("GBP", "GBPUSD", rnd_step=0.0050, rnd_d=0.08, rnd_near=0.15)
_reg("GBP_RND_100_MT", make_round_fade(0.0100, 0.07, 0.14, None, None, (0, 1, 2, 3)), "GBPUSD")
_reg("GBP_OF_NY_D28", make_of_window(14.0, 17.0, 0.28, (0, 1, 2, 3)), "GBPUSD")


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
    ap.add_argument("--syms", default="ETHUSD,BTCUSD,EURUSD,GBPUSD",
                    help="filtro opcional de símbolos")
    args = ap.parse_args()

    want = {s.strip().upper() for s in args.syms.split(",") if s.strip()}
    os.environ["KIMI_GROUP"] = "crypto"
    os.makedirs("logs", exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    logf = open(f"logs/backtest_setups_novos_v8_fxcrypto_{stamp}.txt", "w", encoding="utf-8")

    def out(s):
        try:
            print(s)
        except UnicodeEncodeError:
            print(s.encode("ascii", "replace").decode("ascii"))
        logf.write(s + "\n")
        logf.flush()

    strat_map = {n: fn for n, fn in STRATS.items() if STRAT_SYM[n] in want}
    out(f"{'#' * 74}\n# SETUPS NOVOS V8 — FX/CRYPTO ({len(strat_map)} setups) · "
        f"{datetime.now():%d/%m %H:%M}\n# Ativos: {sorted(want)}\n"
        f"# NÃO altera motores · GO rigoroso\n{'#' * 74}")

    from backtest_crypto_pro import mt5_connect, fetch as _cp_fetch
    mt5 = mt5_connect()

    needs = {}
    for name in strat_map:
        k = STRAT_SYM[name]
        tf = STRAT_TF.get(name, 15)
        needs.setdefault((k, tf), []).append(name)

    raw_dfs = {}
    for (k, tf) in sorted(needs.keys()):
        t0 = time.time()
        raw = _cp_fetch(mt5, k, tf, args.bars)
        if raw is None or len(raw) < 2000:
            out(f"\n!! {k} M{tf}: sem histórico")
            continue
        raw_dfs[(k, tf)] = add_indicators(raw)
        bars_day = 288 if tf == 5 else 96
        anos = len(raw_dfs[(k, tf)]) / bars_day / 365
        out(f"\n{'=' * 74}\n  {k} M{tf}: {len(raw_dfs[(k, tf)])} candles (~{anos:.1f}a)  "
            f"[{raw_dfs[(k, tf)].index[0].date()} -> {raw_dfs[(k, tf)].index[-1].date()}] "
            f"({time.time() - t0:.0f}s)\n{'=' * 74}")

    dfs = {}
    for key, df in raw_dfs.items():
        k, tf = key
        dfs[key] = add_extra_v2(df.copy(), k, ctx=None)

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
            t.to_csv(f"logs/novos_v8_{k}_M{tf}_{name}.csv", index=False)
        v = verdict(s, cons, s_out)
        out(f"      VEREDITO: {v}")
        ranking.append({"setup": name, "sym": k, "tf": tf, **s, "cons%": cons, "veredito": v})

    out(f"\n{'#' * 74}\n  RANKING V8 FX/CRYPTO\n{'#' * 74}")
    rk = pd.DataFrame(ranking)
    goes, yellows = [], []
    if not rk.empty:
        rk = rk.sort_values(["sym", "net"], ascending=[True, False])
        for _, r in rk.iterrows():
            if r.get("n", 0) == 0:
                out(f"  {r.sym:<7} M{int(r.tf)} {r.setup:<22} sem trades")
                continue
            out(f"  {r.sym:<7} M{int(r.tf)} {r.setup:<22} n={int(r.n):<5} net {r.net:+.3f} R  "
                f"PF {r.PF:<5} cons {r['cons%']}%  {r.veredito}")
            vs = str(r.veredito)
            tag = f"{r.sym}:{r.setup}"
            if "🟢" in vs and "GO" in vs:
                goes.append(tag)
            elif "🟡" in vs:
                yellows.append(tag)
        rk.to_csv(f"logs/backtest_setups_novos_v8_fxcrypto_ranking_{stamp}.csv", index=False)

    out(f"\n# GO encontrados: {goes if goes else 'nenhum'}")
    out(f"# amarelos: {yellows if yellows else 'nenhum'}")
    out(f"# Criterio: net>=+0,10 · PF>=1,25 · cons>=55% · OOS segura · n>=40")
    out(f"# FIM — logs/backtest_setups_novos_v8_fxcrypto_{stamp}.txt")
    logf.close()


if __name__ == "__main__":
    main()
