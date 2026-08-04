"""
backtest_edge_candidates.py — VEREDITO COM CUSTOS dos candidatos do Edge Discovery V2.

Os 9 candidatos mais robustos dos research reports de 22/07/2026, testados como
TRADES de verdade: entrada no fechamento quando as condições são verdadeiras,
SL = 1×ATR, TP = 2×ATR (idêntico ao label que os descobriu), custos reais,
zeragem EOD na B3 (mata o viés de overnight dos labels), time-stop nos 24h.

Rigor anti-vazamento: os limiares dos quintis são calculados SÓ no in-sample
(primeiros 70%) e congelados — diferente da V2, que usou a série inteira.

Uso (um comando, com as DUAS MT5 abertas):
    python backtest_edge_candidates.py --grupo tudo
  (ou --grupo b3 / --grupo crypto separadamente)
Saída: logs/edge_candidates_<grupo>_<stamp>.txt + ranking final
"""
import argparse
import os
import subprocess
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

import numpy as np
import pandas as pd

OOS_FRAC = 0.30
TIME_STOP_BARS = 60           # 24h: mesmo horizonte do label
STOCK_FEE_PCT = 0.00055
STOCK_TICK = 0.01
CFD_SPREAD = {"BTCUSD": 12.0, "ETHUSD": 2.9, "XAUUSD": 0.25}

# ── Candidatos (research reports b3/crypto 22/07/2026) ──────────────────────
# conds: lista de (feature, bucket) — bucket "q1".."q5" | "1"/"0" p/ binária
CANDIDATES = [
    dict(nome="XAU_US_DRIFT",   grupo="crypto", sym="XAUUSD", tf=15, side="COMPRA",
         conds=[("vol_day_cum_rel", "q5"), ("ema200_slope", "q5"), ("hour", "q5")]),
    dict(nome="ETH_SQZ_TREND",  grupo="crypto", sym="ETHUSD", tf=15, side="COMPRA",
         conds=[("bb_width", "q1"), ("ema50_slope", "q5")]),
    dict(nome="ETH_WEAK_DRIFT", grupo="crypto", sym="ETHUSD", tf=60, side="VENDA",
         conds=[("h4_rsi", "q1"), ("day_range_pos", "q1"), ("gap_pct", "q1")]),
    dict(nome="ETH_SQUEEZE_H1", grupo="crypto", sym="ETHUSD", tf=60, side="COMPRA",
         conds=[("bb_width_pctl", "q1"), ("macd_hist", "q3")]),
    dict(nome="BTC_SQZ_QUIET",  grupo="crypto", sym="BTCUSD", tf=15, side="COMPRA",
         conds=[("squeeze", "1"), ("vol_pctl", "q1"), ("ema200_dist", "q4")]),
    dict(nome="ABEV_ELASTICO",  grupo="b3", sym="ABEV3", tf=15, side="COMPRA",
         conds=[("ema200_dist", "q1"), ("vwap_dist", "q1"), ("stoch_k", "q1")]),
    dict(nome="WIN_CAPITULACAO", grupo="b3", sym="WIN$D", tf=60, side="COMPRA",
         conds=[("keltner_width", "q5"), ("bb_pos", "q1"), ("ema200_dist", "q1")]),
    dict(nome="VALE_SQZ_VOL",   grupo="b3", sym="VALE3", tf=15, side="COMPRA",
         conds=[("squeeze", "1"), ("vol_rel10", "q5")]),
    dict(nome="PETR_SQZ_VOL",   grupo="b3", sym="PETR4", tf=15, side="COMPRA",
         conds=[("squeeze", "1"), ("vol_rel10", "q5")]),
    dict(nome="WIN_EOD_REV",    grupo="b3", sym="WIN$D", tf=15, side="COMPRA",
         conds=[("vol_pctl", "q1"), ("ema50_dist", "q1"), ("hour", "q5")]),
]


def cost_R(sym, entry, risk, stopped):
    """Custo do round-trip em R (risco = 1×ATR em pontos)."""
    from backtest_kimi_real import asset_key, roundtrip_cost_pts
    k = asset_key(sym)
    if k in ("WIN", "WDO"):
        return roundtrip_cost_pts(sym, stopped=stopped) / risk
    if k in CFD_SPREAD:
        return CFD_SPREAD[k] / risk
    # ação
    c = entry*STOCK_FEE_PCT + STOCK_TICK + (STOCK_TICK if stopped else 0)
    return c / risk


def make_mask(F, conds, cut):
    """Máscara das condições com limiares congelados no in-sample [0:cut)."""
    m = pd.Series(True, index=F.index)
    for feat, bucket in conds:
        s = F[feat]
        if bucket in ("0", "1"):
            m &= (s == float(bucket))
            continue
        qs = s.iloc[:cut].quantile([0.2, 0.4, 0.6, 0.8]).values
        lo = {-1: -np.inf, 0: qs[0], 1: qs[1], 2: qs[2], 3: qs[3]}
        q = int(bucket[1]) - 1              # q1→0 ... q5→4
        lo_v = lo[q-1]; hi_v = qs[q] if q < 4 else np.inf
        m &= ((s > lo_v) & (s <= hi_v)) if q > 0 else (s <= qs[0])
    return m.fillna(False)


def simulate(df, F, sym, side, conds, is_b3):
    n = len(df)
    cut = int(n*(1-OOS_FRAC))
    mask = make_mask(F, conds, cut).values
    atr = F["atr_pct"].values * df.Close.values      # ATR em pontos
    day = pd.Series(df.index.date, index=df.index).values
    trades, pos = [], None
    buy = side == "COMPRA"
    for i in range(300, n):
        h, l, c = df.High.iloc[i], df.Low.iloc[i], df.Close.iloc[i]
        if pos:
            exit_R = None; stopped = True
            if (l <= pos["sl"]) if buy else (h >= pos["sl"]):
                exit_R = -1.0
            elif (h >= pos["tp"]) if buy else (l <= pos["tp"]):
                exit_R = 2.0; stopped = False
            elif is_b3 and day[i] != pos["day"]:
                px = df.Close.iloc[i-1]
                exit_R = ((px-pos["entry"])/pos["risk"]) if buy else ((pos["entry"]-px)/pos["risk"])
            elif (not is_b3) and (i - pos["i"]) >= TIME_STOP_BARS:
                exit_R = ((c-pos["entry"])/pos["risk"]) if buy else ((pos["entry"]-c)/pos["risk"])
            if exit_R is not None:
                cr = cost_R(sym, pos["entry"], pos["risk"], stopped)
                trades.append({"ts": df.index[i], "gross_R": round(exit_R, 3),
                               "cost_R": round(cr, 3), "net_R": round(exit_R-cr, 3),
                               "oos": pos["i"] >= cut})
                pos = None
            continue
        if not mask[i]:
            continue
        a = atr[i]
        if not np.isfinite(a) or a <= 0:
            continue
        entry = float(c); risk = a
        pos = {"i": i, "day": day[i], "entry": entry, "risk": risk,
               "sl": entry - risk if buy else entry + risk,
               "tp": entry + 2*risk if buy else entry - 2*risk}
    return pd.DataFrame(trades)


def stats(t):
    if t is None or t.empty:
        return {"n": 0}
    w = t[t.net_R > 0]; l = t[t.net_R <= 0]
    gw, gl = w.net_R.sum(), -l.net_R.sum()
    eq = t.net_R.cumsum(); dd = (eq - eq.cummax()).min()
    return {"n": len(t), "win": round(len(w)/len(t)*100, 1),
            "net": round(t.net_R.mean(), 3),
            "PF": round(gw/gl, 2) if gl > 0 else float("inf"),
            "tot": round(t.net_R.sum(), 1), "dd": round(dd, 1)}


def consistency(t):
    if t is None or t.empty:
        return 0
    t2 = t.copy(); t2["p"] = pd.to_datetime(t2["ts"]).dt.to_period("2M").astype(str)
    per = t2.groupby("p")["net_R"].mean()
    return round((per > 0).sum()/len(per)*100) if len(per) else 0


def _fetch(grupo, symbol, tf, bars=200000):
    if grupo == "b3":
        from backtest_kimi_real import fetch
        df = fetch(symbol, tf, bars)
        return None if df is None else df[["Open", "High", "Low", "Close", "Volume"]].copy()
    from backtest_crypto_pro import mt5_connect, fetch
    if not hasattr(_fetch, "_mt5"):
        _fetch._mt5 = mt5_connect()
    return fetch(_fetch._mt5, symbol, tf, bars)


def run_group(grupo):
    from edge_discovery.feature_engine import build_features
    os.makedirs("logs", exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    logf = open(f"logs/edge_candidates_{grupo}_{stamp}.txt", "w", encoding="utf-8")
    def out(s):
        print(s); logf.write(s+"\n"); logf.flush()
    out(f"{'#'*74}\n# EDGE CANDIDATES × CUSTOS — {grupo} · {datetime.now():%d/%m %H:%M}\n"
        f"# entrada=close · SL 1×ATR · TP 2×ATR · {'EOD B3' if grupo=='b3' else 'time-stop 60 barras'}"
        f" · thresholds só do in-sample\n{'#'*74}")
    ranking = []
    cache = {}
    for cand in [c for c in CANDIDATES if c["grupo"] == grupo]:
        key = (cand["sym"], cand["tf"])
        if key not in cache:
            df = _fetch(grupo, cand["sym"], cand["tf"])
            if df is None or len(df) < 5000:
                out(f"\n!! {cand['sym']} M{cand['tf']}: sem histórico"); cache[key] = None; continue
            cache[key] = (df, build_features(df))
        if cache[key] is None:
            continue
        df, F = cache[key]
        t0 = time.time()
        t = simulate(df, F, cand["sym"], cand["side"], cand["conds"], grupo == "b3")
        s = stats(t); cons = consistency(t)
        anos = (df.index[-1]-df.index[0]).days/365
        out(f"\n▸ {cand['nome']}  ({cand['sym']} M{cand['tf']} {cand['side']}, ~{anos:.1f} anos)  "
            f"[{time.time()-t0:.0f}s]")
        out(f"  conds: {' & '.join(f'{f}={b}' for f, b in cand['conds'])}")
        if s["n"] == 0:
            out("  (sem trades)"); continue
        t_in, t_oos = t[~t.oos], t[t.oos]
        s_in, s_oos = stats(t_in), stats(t_oos)
        out(f"  COMPLETO  n={s['n']:<5} win {s['win']}%  net {s['net']:+.3f} R  PF {s['PF']}  "
            f"tot {s['tot']:+.1f}  DD {s['dd']}  cons {cons}%")
        if s_in.get("n"):
            out(f"  IN        n={s_in['n']:<5} net {s_in['net']:+.3f} R  PF {s_in['PF']}")
        if s_oos.get("n"):
            seg = s_oos["net"] >= 0.08 and s_oos["PF"] >= 1.15
            out(f"  OOS       n={s_oos['n']:<5} net {s_oos['net']:+.3f} R  PF {s_oos['PF']}  "
                f"→ {'SEGUROU ✅' if seg else 'CAIU ❌'}")
        go = (s["net"] >= 0.10 and s["PF"] >= 1.25 and cons >= 55
              and s_oos.get("n", 0) >= 15 and s_oos["net"] >= 0.08 and s_oos["PF"] >= 1.15)
        quase = s["net"] > 0.05 and s["PF"] >= 1.15
        verd = "🟢 GO" if go else ("🟡 quase" if quase else "🔴")
        out(f"  VEREDITO: {verd}")
        t.to_csv(f"logs/edge_cand_{cand['nome']}.csv", index=False)
        ranking.append({"nome": cand["nome"], "sym": cand["sym"], **s, "cons": cons,
                        "oos_net": s_oos.get("net"), "verd": verd})
    out(f"\n{'#'*74}\n  RANKING {grupo}\n{'#'*74}")
    for r in sorted(ranking, key=lambda x: -(x.get("net") or -9)):
        out(f"  {r['nome']:<16} n={r['n']:<5} net {r['net']:+.3f} R  PF {r['PF']:<5} "
            f"cons {r['cons']}%  OOS {r['oos_net']}  {r['verd']}")
    out("\n# FIM — envie o log para análise")
    logf.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--grupo", choices=["b3", "crypto", "tudo"], default="tudo")
    args = ap.parse_args()
    if args.grupo == "tudo":
        for g in ("b3", "crypto"):
            print(f"\n{'█'*74}\n█  GRUPO {g.upper()}\n{'█'*74}")
            subprocess.run([sys.executable, __file__, "--grupo", g],
                           cwd=str(Path(__file__).parent))
        return
    run_group(args.grupo)


if __name__ == "__main__":
    main()
