"""
backtest_setups_novos_v13_btc_24h.py — BTC/ETH hipóteses 24h / overnight / Asia.

Foco: janelas que cobrem noite/FDS (não retestar GOs diurnos 08–18 já live).
ORDER_FLOW BTC: pulado (🔴 OOS caiu — SETUPS_TESTADOS §3; sem params/dados novos).

Régua GO: net ≥ +0,10 R · PF ≥ 1,25 · cons ≥ 55% · OOS segura · n ≥ 40.
NÃO altera live até 🟢.

Uso:
  python backtest_setups_novos_v13_btc_24h.py
  python backtest_setups_novos_v13_btc_24h.py --syms BTCUSD
  python backtest_setups_novos_v13_btc_24h.py --bars 200000
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
from backtest_setups_novos_v5 import _h4_ok
from backtest_setups_novos_v9_refine import _mt

STRATS = {}
STRAT_SYM = {}
ONE_PER_DAY = set()


def _reg(name, fn, sym="BTCUSD", one_per_day=False):
    STRATS[name] = fn
    STRAT_SYM[name] = sym
    if one_per_day:
        ONE_PER_DAY.add(name)


def _hh_ok(hh, hh0, hh1):
    """Janela horária; se hh0 > hh1, wrap (ex.: 22–06, 18–08). hh1=24 → full day."""
    h = float(hh)
    a, b = float(hh0), float(hh1)
    if b >= 24.0 and a <= 0.0:
        return True
    if a <= b:
        return a <= h < b
    return h >= a or h < b


def _inside_h(vol=1.3, hh0=0.0, hh1=24.0):
    def _f(df, i, sym):
        if i < 3:
            return None
        r = df.iloc[i]
        p, pp = df.iloc[i - 1], df.iloc[i - 2]
        if not _hh_ok(r.hh, hh0, hh1):
            return None
        if not (p.High <= pp.High and p.Low >= pp.Low):
            return None
        if float(r.Volume or 0) < vol * float(r.vol8 or 0):
            return None
        if r.Close > p.High:
            base = _sig("COMPRA", r.Close, p.Low, 1.5, 2.5, True)
        elif r.Close < p.Low:
            base = _sig("VENDA", r.Close, p.High, 1.5, 2.5, True)
        else:
            return None
        if not base or not _h4_ok(r, base["dir"]):
            return None
        return base
    return _f


def _nr_h(n=5, vol=1.3, hh0=0.0, hh1=24.0):
    def _f(df, i, sym):
        if i < n + 2:
            return None
        r = df.iloc[i]
        if not _hh_ok(r.hh, hh0, hh1):
            return None
        prev = (df.High.iloc[i - n:i] - df.Low.iloc[i - n:i])
        if len(prev) < n or float(prev.min()) <= 0:
            return None
        if float(prev.iloc[-1]) > float(prev.min()) + 1e-12:
            return None
        p = df.iloc[i - 1]
        if float(r.Volume or 0) < vol * float(r.vol8 or 0):
            return None
        if r.Close > p.High:
            base = _sig("COMPRA", r.Close, p.Low, 1.5, 2.5, True)
        elif r.Close < p.Low:
            base = _sig("VENDA", r.Close, p.High, 1.5, 2.5, True)
        else:
            return None
        if not _h4_ok(r, base["dir"]):
            return None
        return base
    return _f


def _impulse_h(atr_mult=2.0, vol_mult=1.5, hh0=0.0, hh1=24.0, tp_r=2.0):
    def _f(df, i, sym):
        if i < 3:
            return None
        r, p = df.iloc[i], df.iloc[i - 1]
        if not _hh_ok(r.hh, hh0, hh1):
            return None
        atr = float(p.atr or 0)
        if atr <= 0:
            return None
        if float(p.High - p.Low) < atr_mult * atr:
            return None
        if float(p.Volume or 0) < vol_mult * float(p.vol8 or 0):
            return None
        down = float(p.Close) < float(p.Open)
        up = float(p.Close) > float(p.Open)
        if not (down or up):
            return None
        d = "VENDA" if down else "COMPRA"
        if not _h4_ok(r, d):
            return None
        if d == "VENDA" and float(r.Close) >= float(p.Low):
            return None
        if d == "COMPRA" and float(r.Close) <= float(p.High):
            return None
        if float(r.Volume or 0) < 1.1 * float(r.vol8 or 0):
            return None
        sl = float(p.High) if d == "VENDA" else float(p.Low)
        return _sig(d, float(r.Close), sl, 1.0, tp_r, True)
    return _f


def _h4_pb_ema_h(hh0=0.0, hh1=24.0):
    """Pullback EMA21 + bias H4 — variante 24h do GO diurno BTC_H4_PB_EMA (10–18)."""
    def _f(df, i, sym):
        if i < 5:
            return None
        r = df.iloc[i]
        if not _hh_ok(r.hh, hh0, hh1):
            return None
        h4c, h4e = r.get("h4_close"), r.get("h4_ema50")
        e20, e50 = r.ema21, r.ema50
        if pd.isna(e20) or pd.isna(e50) or pd.isna(r.atr) or r.atr <= 0:
            return None
        p = df.iloc[i - 1]
        bull = float(e20) > float(e50)
        if not pd.isna(h4c) and not pd.isna(h4e):
            bull = float(h4c) > float(h4e)
        if bull:
            if float(p.Low) > float(e20) + 0.3 * float(r.atr):
                return None
            if not (float(r.Close) > float(e20) and float(r.Close) > float(r.Open)
                    and float(r.Close) > float(p.High)):
                return None
            d, sl = "COMPRA", min(float(p.Low), float(r.Low))
        else:
            if float(p.High) < float(e20) - 0.3 * float(r.atr):
                return None
            if not (float(r.Close) < float(e20) and float(r.Close) < float(r.Open)
                    and float(r.Close) < float(p.Low)):
                return None
            d, sl = "VENDA", max(float(p.High), float(r.High))
        if not _h4_ok(r, d):
            return None
        return _sig(d, float(r.Close), sl, 1.5, 2.0, True)
    return _f


def _volspike_h(vol=2.0, atr_mult=1.5, hh0=0.0, hh1=24.0):
    def _f(df, i, sym):
        if i < 2:
            return None
        r, p = df.iloc[i], df.iloc[i - 1]
        if not _hh_ok(r.hh, hh0, hh1):
            return None
        if float(p.Volume or 0) < vol * float(p.vol8 or 0):
            return None
        atr = float(p.atr or 0)
        if atr <= 0 or float(p.High - p.Low) < atr_mult * atr:
            return None
        d = "COMPRA" if p.Close > p.Open else "VENDA"
        if d == "COMPRA" and float(r.Close) <= float(p.High):
            return None
        if d == "VENDA" and float(r.Close) >= float(p.Low):
            return None
        if not _h4_ok(r, d):
            return None
        sl = float(p.Low) if d == "COMPRA" else float(p.High)
        return _sig(d, float(r.Close), sl, 1.0, 2.0, True)
    return _f


def _outside_h(vol=1.3, hh0=0.0, hh1=24.0):
    def _f(df, i, sym):
        if i < 3:
            return None
        r, p = df.iloc[i], df.iloc[i - 1]
        if not _hh_ok(r.hh, hh0, hh1):
            return None
        if not (float(r.High) > float(p.High) and float(r.Low) < float(p.Low)):
            return None
        if float(r.Volume or 0) < vol * float(r.vol8 or 0):
            return None
        if r.Close > r.Open and r.Close > p.High:
            d, sl = "COMPRA", float(r.Low)
        elif r.Close < r.Open and r.Close < p.Low:
            d, sl = "VENDA", float(r.High)
        else:
            return None
        if not _h4_ok(r, d):
            return None
        return _sig(d, float(r.Close), sl, 1.5, 2.0, True)
    return _f


def _hl_h(vol=1.3, hh0=0.0, hh1=24.0):
    def _f(df, i, sym):
        if i < 4:
            return None
        r = df.iloc[i]
        if not _hh_ok(r.hh, hh0, hh1):
            return None
        if float(r.Volume or 0) < vol * float(r.vol8 or 0):
            return None
        p1, p2 = df.iloc[i - 1], df.iloc[i - 2]
        if float(p1.Low) > float(p2.Low) and float(r.Close) > float(p1.High):
            d, sl = "COMPRA", float(p1.Low)
        elif float(p1.High) < float(p2.High) and float(r.Close) < float(p1.Low):
            d, sl = "VENDA", float(p1.High)
        else:
            return None
        if not _h4_ok(r, d):
            return None
        return _sig(d, float(r.Close), sl, 1.5, 2.0, True)
    return _f


def _dbl_inside_h(vol=1.3, hh0=0.0, hh1=24.0):
    def _f(df, i, sym):
        if i < 4:
            return None
        r = df.iloc[i]
        a, b, c = df.iloc[i - 1], df.iloc[i - 2], df.iloc[i - 3]
        if not _hh_ok(r.hh, hh0, hh1):
            return None
        if not (b.High <= c.High and b.Low >= c.Low):
            return None
        if not (a.High <= b.High and a.Low >= b.Low):
            return None
        if float(r.Volume or 0) < vol * float(r.vol8 or 0):
            return None
        if r.Close > a.High:
            base = _sig("COMPRA", r.Close, a.Low, 1.5, 2.5, True)
        elif r.Close < a.Low:
            base = _sig("VENDA", r.Close, a.High, 1.5, 2.5, True)
        else:
            return None
        if not base or not _h4_ok(r, base["dir"]):
            return None
        return base
    return _f


# ═══ BTC — full session / overnight / Asia ═══
# Inside 24h + Asia + overnight (não retestar 08–18 já GO)
_reg("BTC_INS_24H_V13", _inside_h(1.3, 0.0, 24.0))
_reg("BTC_INS_24H_V14", _inside_h(1.4, 0.0, 24.0))
_reg("BTC_INS_24H_V135", _inside_h(1.35, 0.0, 24.0))
_reg("BTC_INS_ASIA_0008", _inside_h(1.3, 0.0, 8.0))
_reg("BTC_INS_ASIA_2206", _inside_h(1.3, 22.0, 6.0))
_reg("BTC_INS_OVN_1808", _inside_h(1.3, 18.0, 8.0))
_reg("BTC_INS_NIGHT_2010", _inside_h(1.3, 20.0, 10.0))
_reg("BTC_INS_24H_MT", _mt(_inside_h(1.3, 0.0, 24.0)))

# NR break 24h / Asia
_reg("BTC_NR5_24H", _nr_h(5, 1.3, 0.0, 24.0))
_reg("BTC_NR4_24H", _nr_h(4, 1.3, 0.0, 24.0))
_reg("BTC_NR7_24H", _nr_h(7, 1.3, 0.0, 24.0))
_reg("BTC_NR5_ASIA_0008", _nr_h(5, 1.3, 0.0, 8.0))
_reg("BTC_NR5_ASIA_2206", _nr_h(5, 1.3, 22.0, 6.0))
_reg("BTC_NR5_OVN_1808", _nr_h(5, 1.3, 18.0, 8.0))
_reg("BTC_NR4_ASIA_0008", _nr_h(4, 1.35, 0.0, 8.0))

# Impulso / volspike continuação H4 24h
_reg("BTC_IMP_CONT_24H", _impulse_h(2.0, 1.5, 0.0, 24.0, 2.0))
_reg("BTC_IMP_CONT_24H_22", _impulse_h(2.2, 1.5, 0.0, 24.0, 2.0))
_reg("BTC_IMP_ASIA_0008", _impulse_h(1.8, 1.4, 0.0, 8.0, 2.0))
_reg("BTC_IMP_OVN_1808", _impulse_h(2.0, 1.5, 18.0, 8.0, 2.0))
_reg("BTC_VOLSPIKE_24H", _volspike_h(2.0, 1.5, 0.0, 24.0))

# H4 PB EMA 24h (live diurno é 10–18 — hipótese nova = sem filtro / Asia)
_reg("BTC_H4_PB_EMA_24H", _h4_pb_ema_h(0.0, 24.0))
_reg("BTC_H4_PB_EMA_ASIA", _h4_pb_ema_h(0.0, 8.0))
_reg("BTC_H4_PB_EMA_OVN", _h4_pb_ema_h(18.0, 8.0))

# Estrutura 24h
_reg("BTC_OUTSIDE_24H", _outside_h(1.3, 0.0, 24.0))
_reg("BTC_HL_24H", _hl_h(1.3, 0.0, 24.0))
_reg("BTC_DBL_INS_24H", _dbl_inside_h(1.3, 0.0, 24.0))

# ═══ ETH — subset 24h (mesmo script, sem explodir) ═══
_reg("ETH_INS_24H_V13", _inside_h(1.3, 0.0, 24.0), "ETHUSD")
_reg("ETH_NR5_24H", _nr_h(5, 1.3, 0.0, 24.0), "ETHUSD")
_reg("ETH_H4_PB_EMA_24H", _h4_pb_ema_h(0.0, 24.0), "ETHUSD")
_reg("ETH_IMP_CONT_24H", _impulse_h(2.0, 1.5, 0.0, 24.0, 2.0), "ETHUSD")
_reg("ETH_INS_ASIA_0008", _inside_h(1.3, 0.0, 8.0), "ETHUSD")
_reg("ETH_INS_OVN_1808", _inside_h(1.3, 18.0, 8.0), "ETHUSD")


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
    """MT5 primeiro; fallback edge_discovery só se existir OHLC (não features)."""
    from backtest_crypto_pro import fetch as _cp_fetch
    raw = _cp_fetch(mt5, sym, tf, bars)
    if raw is not None and len(raw) >= 2000:
        return raw
    # fallback: procurar CSV OHLC local (não usar feature-db do edge_discovery)
    for cand in (
        Path(f"data/{sym}_M{tf}.csv"),
        Path(f"data/{sym}_M{tf}.csv.gz"),
        Path(f"storage/{sym}_M{tf}.csv"),
    ):
        if cand.exists():
            df = pd.read_csv(cand)
            cols = {c.lower(): c for c in df.columns}
            need = ("open", "high", "low", "close")
            if not all(k in cols for k in need):
                continue
            tcol = cols.get("time") or cols.get("datetime") or cols.get("date")
            if tcol:
                df["time"] = pd.to_datetime(df[tcol])
                df = df.set_index("time")
            volc = cols.get("volume") or cols.get("tick_volume") or cols.get("real_volume")
            out(f"  fallback CSV: {cand}")
            return pd.DataFrame({
                "Open": df[cols["open"]], "High": df[cols["high"]],
                "Low": df[cols["low"]], "Close": df[cols["close"]],
                "Volume": df[volc] if volc else 1.0,
            }, index=df.index)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bars", type=int, default=300000)
    ap.add_argument("--syms", default="BTCUSD,ETHUSD")
    args = ap.parse_args()

    want = {s.strip().upper() for s in args.syms.split(",") if s.strip()}
    os.environ["KIMI_GROUP"] = "crypto"
    os.makedirs("logs", exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    log_path = f"logs/backtest_setups_novos_v13_btc_24h_{stamp}.txt"
    logf = open(log_path, "w", encoding="utf-8")

    def out(s):
        try:
            print(s)
        except UnicodeEncodeError:
            print(s.encode("ascii", "replace").decode("ascii"))
        logf.write(s + "\n")
        logf.flush()

    strat_map = {n: fn for n, fn in STRATS.items() if STRAT_SYM[n] in want}
    out(f"{'#' * 74}\n# SETUPS NOVOS V13 — BTC/ETH 24H / OVERNIGHT / ASIA "
        f"({len(strat_map)} setups) · {datetime.now():%d/%m %H:%M}\n"
        f"# Ativos: {sorted(want)}\n"
        f"# SKIP: ORDER_FLOW BTC (🔴 OOS histórico — sem params novos)\n"
        f"# NÃO altera live até GO · régua rigorosa\n{'#' * 74}")

    from backtest_crypto_pro import mt5_connect
    mt5 = mt5_connect()

    needs = sorted({STRAT_SYM[n] for n in strat_map})
    raw_dfs = {}
    for sym in needs:
        t0 = time.time()
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
    for name, fn in strat_map.items():
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
            t.to_csv(f"logs/novos_v13_{sym}_M15_{name}.csv", index=False)
        v = verdict(s, cons, s_out)
        out(f"      VEREDITO: {v}")
        ranking.append({"setup": name, "sym": sym, "tf": 15, **s,
                        "cons%": cons, "veredito": v, "oos_net": s_out.get("net")})

    out(f"\n{'#' * 74}\n  RANKING V13 24H\n{'#' * 74}")
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
        rk.to_csv(f"logs/backtest_setups_novos_v13_btc_24h_ranking_{stamp}.csv",
                  index=False)

    out(f"\n# GO encontrados: {goes if goes else 'nenhum'}")
    out(f"# amarelos: {yellows if yellows else 'nenhum'}")
    out(f"# FIM — {log_path}")
    logf.close()
    print(f"\n-> {log_path}")


if __name__ == "__main__":
    main()
