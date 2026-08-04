"""
MEGA-SWEEP GAPS (opção 2) — preenche células (família × ativo) ainda sem GO live.

Não retesta células já 🟢 live nem 🔴 documentados.
Régua GO idêntica: net≥+0,10R · PF≥1,25 · cons≥55% · OOS · n≥40.
NÃO altera live até wire explícito dos 🟢.

Uso:
  python backtest_mega_sweep_gaps.py
  python backtest_mega_sweep_gaps.py --syms EURUSD,GBPUSD,ETHUSD
  python backtest_mega_sweep_gaps.py --syms BTCUSD,ETHUSD,XAUUSD,EURUSD,GBPUSD --bars 300000
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

from mega_sweep.catalog import open_cells, SYMBOLS_CRYPTO, SYMBOLS_B3
from backtest_kimi_real import add_indicators
from backtest_setups_novos import stats, consistency, verdict, _line, OOS_FRAC, _sig
from backtest_setups_novos_v2 import add_extra_v2, _simulate
from backtest_setups_novos_v5 import _h4_ok
from backtest_setups_novos_v9_refine import _mt
from backtest_setups_novos_v3 import make_london_handoff, make_round_fade
from backtest_setups_novos_v13_btc_24h import (
    _inside_h, _nr_h, _impulse_h, _h4_pb_ema_h, _outside_h, _hl_h, _dbl_inside_h, _hh_ok,
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


def _pdh_h(vol=1.2, hh0=10.0, hh1=16.0):
    def _f(df, i, sym):
        if i < 30:
            return None
        r = df.iloc[i]
        if not _hh_ok(r.hh, hh0, hh1):
            return None
        if float(r.Volume or 0) < vol * float(r.vol8 or 0):
            return None
        # prev day hi/lo se existirem; senão rolling 96 barras (~1d M15)
        pdh = getattr(r, "pdh", None)
        pdl = getattr(r, "pdl", None)
        if pdh is None or pdl is None or pd.isna(pdh) or pd.isna(pdl):
            win = df.iloc[max(0, i - 96):i]
            if len(win) < 20:
                return None
            pdh, pdl = float(win.High.max()), float(win.Low.min())
        else:
            pdh, pdl = float(pdh), float(pdl)
        p = df.iloc[i - 1]
        if r.Close > pdh and p.Close <= pdh:
            base = _sig("COMPRA", r.Close, p.Low, 1.5, 2.0, True)
        elif r.Close < pdl and p.Close >= pdl:
            base = _sig("VENDA", r.Close, p.High, 1.5, 2.0, True)
        else:
            return None
        if not base or not _h4_ok(r, base["dir"]):
            return None
        return base
    return _f


def _build_variants(family: str, sym: str):
    """Gera poucas variantes fortes por célula aberta (não explode o grid)."""
    tag = sym.replace("$", "").replace("USD", "")[:3]
    if sym.startswith("WIN"):
        tag = "WIN"
    elif sym.startswith("WDO"):
        tag = "WDO"
    elif sym == "BTCUSD":
        tag = "BTC"
    elif sym == "ETHUSD":
        tag = "ETH"
    elif sym == "XAUUSD":
        tag = "XAU"
    elif sym == "EURUSD":
        tag = "EUR"
    elif sym == "GBPUSD":
        tag = "GBP"

    is_fx_crypto = sym in SYMBOLS_CRYPTO
    specs = []

    if family == "INSIDE":
        specs += [
            (f"{tag}_INS_1015_V13", _inside_h(1.3, 10.0, 15.0)),
            (f"{tag}_INS_0918_V13", _inside_h(1.3, 9.0, 18.0)),
            (f"{tag}_INS_AM_V13", _inside_h(1.3, 9.0, 12.0)),
            (f"{tag}_INS_24H_V13", _inside_h(1.3, 0.0, 24.0)),
        ]
        if is_fx_crypto:
            specs.append((f"{tag}_INS_24H_MT", _mt(_inside_h(1.3, 0.0, 24.0))))

    elif family == "NR":
        specs += [
            (f"{tag}_NR5_1016_H4", _nr_h(5, 1.3, 10.0, 16.0)),
            (f"{tag}_NR5_0915_H4", _nr_h(5, 1.3, 9.0, 15.0)),
            (f"{tag}_NR4_1016_H4", _nr_h(4, 1.3, 10.0, 16.0)),
            (f"{tag}_NR7_1014_H4", _nr_h(7, 1.3, 10.0, 14.0)),
        ]
        if is_fx_crypto:
            specs.append((f"{tag}_NR5_24H", _nr_h(5, 1.3, 0.0, 24.0)))

    elif family == "IMP_CONT":
        specs += [
            (f"{tag}_IMP_CONT_20", _impulse_h(2.0, 1.5, 9.0, 18.0, 2.0)),
            (f"{tag}_IMP_CONT_24H", _impulse_h(2.0, 1.5, 0.0, 24.0, 2.0)),
        ]

    elif family == "H4_PB_EMA":
        specs += [
            (f"{tag}_H4_PB_EMA", _h4_pb_ema_h(10.0, 18.0)),
            (f"{tag}_H4_PB_EMA_24H", _h4_pb_ema_h(0.0, 24.0)),
        ]

    elif family == "LH" and is_fx_crypto:
        specs += [
            (f"{tag}_LH_047", make_london_handoff(0.47, 0.18, 1.65), True),
            (f"{tag}_LH_050", make_london_handoff(0.50, 0.20, 1.75), True),
        ]

    elif family == "HL":
        specs += [
            (f"{tag}_HL_1014", _hl_h(1.3, 10.0, 14.0)),
            (f"{tag}_HL_24H", _hl_h(1.3, 0.0, 24.0)) if is_fx_crypto else None,
        ]

    elif family == "PDH":
        specs += [
            (f"{tag}_PDH_H4", _pdh_h(1.2, 10.0, 16.0)),
            (f"{tag}_PDH_1115", _pdh_h(1.2, 11.0, 15.0)),
        ]

    elif family == "OUTSIDE":
        specs += [
            (f"{tag}_OUT_1015", _outside_h(1.3, 10.0, 15.0)),
            (f"{tag}_OUT_24H", _outside_h(1.3, 0.0, 24.0)) if is_fx_crypto else None,
        ]

    elif family == "DBL_INS":
        specs += [
            (f"{tag}_DBL_INS_1015", _dbl_inside_h(1.3, 10.0, 15.0)),
            (f"{tag}_DBL_INS_24H", _dbl_inside_h(1.3, 0.0, 24.0)) if is_fx_crypto else None,
        ]

    elif family == "RND_FADE" and sym == "XAUUSD":
        # XAU já tem RND live — célula skipped. Outros metais não. Skip.
        pass
    elif family == "RND_FADE" and is_fx_crypto and sym in ("EURUSD", "GBPUSD", "BTCUSD", "ETHUSD"):
        # steps em preço (FX pips-ish / BTC dollars)
        step = {"EURUSD": 0.005, "GBPUSD": 0.005, "BTCUSD": 500.0, "ETHUSD": 50.0}[sym]
        specs += [
            (f"{tag}_RND_FADE", make_round_fade(step=step, delta_max=0.12, atr_near=0.25)),
        ]

    for item in specs:
        if item is None:
            continue
        if len(item) == 3:
            name, fn, opd = item
            _reg(name, fn, sym, one_per_day=opd)
        else:
            name, fn = item
            _reg(name, fn, sym)


def build_registry(want_syms):
    STRATS.clear()
    STRAT_SYM.clear()
    STRAT_TF.clear()
    ONE_PER_DAY.clear()
    cells = open_cells(want_syms)
    for fam, sym in cells:
        _build_variants(fam, sym)
    return cells


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


def _load_ohlc(mt5, sym, tf, bars, out):
    if sym in SYMBOLS_B3:
        from backtest_kimi_real import fetch as _b3_fetch
        raw = _b3_fetch(sym, tf, bars)
        if raw is not None and len(raw) >= 2000:
            return raw
        return None
    from backtest_crypto_pro import fetch as _cp_fetch
    raw = _cp_fetch(mt5, sym, tf, bars) if mt5 is not None else None
    if raw is not None and len(raw) >= 2000:
        return raw
    # fallback edge_discovery featureless? use db OHLC if present as plain ohlc
    for cand in (
        Path(f"edge_discovery/db/{sym}_M{tf}.csv.gz"),
        Path(f"data/{sym}_M{tf}.csv.gz"),
        Path(f"data/{sym}_M{tf}.csv"),
    ):
        if not cand.exists():
            continue
        df = pd.read_csv(cand)
        cols = {c.lower(): c for c in df.columns}
        if not all(k in cols for k in ("open", "high", "low", "close")):
            out(f"  skip {cand.name}: sem OHLC puro")
            continue
        tcol = cols.get("time") or cols.get("datetime") or cols.get("date")
        if tcol:
            df["time"] = pd.to_datetime(df[tcol])
            df = df.set_index("time")
        volc = cols.get("volume") or cols.get("tick_volume") or cols.get("real_volume")
        out(f"  fallback: {cand}")
        return pd.DataFrame({
            "Open": df[cols["open"]], "High": df[cols["high"]],
            "Low": df[cols["low"]], "Close": df[cols["close"]],
            "Volume": df[volc] if volc else 1.0,
        }, index=df.index)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bars", type=int, default=300000)
    ap.add_argument(
        "--syms",
        default="EURUSD,GBPUSD,ETHUSD,BTCUSD,XAUUSD",
        help="Ativos a varrer (gaps). B3: WIN$D,WDO$D",
    )
    ap.add_argument("--list-only", action="store_true", help="Só lista células/variantes")
    args = ap.parse_args()

    want = []
    for s in args.syms.split(","):
        s = s.strip().upper()
        if not s:
            continue
        if s in ("WIN", "WIN$D"):
            want.append("WIN$D")
        elif s in ("WDO", "WDO$D"):
            want.append("WDO$D")
        else:
            want.append(s)

    cells = build_registry(want)
    if args.list_only:
        print(f"Células abertas ({len(cells)}):")
        for fam, sym in cells:
            print(f"  {sym:<10} {fam}")
        print(f"Variantes geradas: {len(STRATS)}")
        for n, sym in sorted(STRAT_SYM.items(), key=lambda x: (x[1], x[0])):
            print(f"  {sym:<10} {n}")
        return

    os.environ["KIMI_GROUP"] = "crypto"
    os.makedirs("logs", exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    log_path = f"logs/backtest_mega_sweep_gaps_{stamp}.txt"
    logf = open(log_path, "w", encoding="utf-8")

    def out(s=""):
        try:
            print(s)
        except UnicodeEncodeError:
            print(s.encode("ascii", "replace").decode("ascii"))
        logf.write(s + "\n")
        logf.flush()

    out(f"{'#' * 74}\n# MEGA-SWEEP GAPS — {len(STRATS)} variantes · {len(cells)} células "
        f"· {datetime.now():%d/%m %H:%M}\n"
        f"# Ativos: {want}\n"
        f"# Modo: só buracos (não retesta live/🔴)\n"
        f"# NÃO altera live até GO\n{'#' * 74}")
    for fam, sym in cells:
        out(f"  cell open: {sym} · {fam}")

    mt5 = None
    need_crypto = [s for s in want if s in SYMBOLS_CRYPTO]
    if need_crypto:
        try:
            from backtest_crypto_pro import mt5_connect
            mt5 = mt5_connect()
        except Exception as exc:
            out(f"!! MT5 crypto: {exc} — tentando fallback CSV")

    needs = sorted({STRAT_SYM[n] for n in STRATS})
    raw_dfs = {}
    for sym in needs:
        t0 = time.time()
        # B3 precisa grupo b3
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
        out(f"\n  ▸ {name}  ({sym} M15)   [{time.time() - t0:.0f}s]")
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
            t.to_csv(f"logs/mega_gaps_{sym}_M15_{name}.csv", index=False)
        v = verdict(s, cons, s_out)
        out(f"      VEREDITO: {v}")
        ranking.append({"setup": name, "sym": sym, "tf": 15, **s,
                        "cons%": cons, "veredito": v, "oos_net": s_out.get("net")})

    out(f"\n{'#' * 74}\n  RANKING MEGA-SWEEP GAPS\n{'#' * 74}")
    rk = pd.DataFrame(ranking)
    goes, yellows = [], []
    if not rk.empty:
        rk = rk.sort_values("net", ascending=False)
        for _, r in rk.iterrows():
            if r.get("n", 0) == 0:
                out(f"  {r.setup:<28} sem trades")
                continue
            out(f"  {r.setup:<28} n={int(r.n):<5} net {r.net:+.3f} R  "
                f"PF {r.PF:<5} cons {r['cons%']}%  OOS {r.oos_net}  {r.veredito}")
            vs = str(r.veredito)
            if "🟢" in vs and "GO" in vs:
                goes.append(r.setup)
            elif "🟡" in vs:
                yellows.append(r.setup)
        rk.to_csv(f"logs/backtest_mega_sweep_gaps_ranking_{stamp}.csv", index=False)

    out(f"\n# GO encontrados: {goes if goes else 'nenhum'}")
    out(f"# amarelos: {yellows if yellows else 'nenhum'}")
    out(f"# Próximo: wire automático de TODOS os GO no live + inventário")
    out(f"# FIM — {log_path}")
    logf.close()
    print(f"\n-> {log_path}")


if __name__ == "__main__":
    main()
