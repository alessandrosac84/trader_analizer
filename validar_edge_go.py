"""
validar_edge_go.py — BATERIA FINAL dos 4 GOs do edge discovery (pré-produção).

Aplica nos 4 aprovados (WIN_EOD_REV, XAU_US_DRIFT, ETH_WEAK_DRIFT, BTC_SQZ_QUIET)
os três testes que faltavam antes do paper trading:

1. WALK-FORWARD ROLANTE — treina ~2 anos (thresholds dos quintis SÓ da janela
   de treino), testa os 6 meses seguintes, anda 6 meses, repete. Aprova se a
   maioria das janelas de teste for positiva.
2. MONTE CARLO — bootstrap (5.000 reamostragens dos trades) para IC do retorno
   e P(total ≤ 0); embaralhamento da ordem (2.000×) para a distribuição de
   drawdown máximo esperado.
3. SENSIBILIDADE — perturba cada condição (quintil ±1) e a saída (TP 1,5R/2,5R):
   se o edge só existe na combinação exata, é overfit.

Uso (um comando, DUAS MT5 abertas):
    python validar_edge_go.py --grupo tudo
Saída: logs/validar_edge_go_<grupo>_<stamp>.txt
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

from backtest_edge_candidates import (CANDIDATES, _fetch, make_mask, cost_R,
                                      stats, TIME_STOP_BARS)

GOS = {"WIN_EOD_REV", "XAU_US_DRIFT", "ETH_WEAK_DRIFT", "BTC_SQZ_QUIET"}
TRAIN_YEARS = 2.0
TEST_MONTHS = 6
BOOT_N = 5000
SHUF_N = 2000


def simulate_window(df, F, sym, side, conds, is_b3, thr_end, t_start, t_end,
                    sl_mult=1.0, tp_mult=2.0):
    """Simula trades ENTRADOS em [t_start, t_end) com thresholds de [0, thr_end)."""
    mask = make_mask(F, conds, thr_end).values
    atr = F["atr_pct"].values * df.Close.values
    day = pd.Series(df.index.date, index=df.index).values
    idx = df.index
    trades, pos = [], None
    buy = side == "COMPRA"
    n = len(df)
    for i in range(300, n):
        h, l, c = df.High.iloc[i], df.Low.iloc[i], df.Close.iloc[i]
        if pos:
            exit_R = None; stopped = True
            if (l <= pos["sl"]) if buy else (h >= pos["sl"]):
                exit_R = -sl_mult
            elif (h >= pos["tp"]) if buy else (l <= pos["tp"]):
                exit_R = tp_mult; stopped = False
            elif is_b3 and day[i] != pos["day"]:
                px = df.Close.iloc[i-1]
                exit_R = ((px-pos["entry"])/pos["risk"]) if buy else ((pos["entry"]-px)/pos["risk"])
            elif (not is_b3) and (i - pos["i"]) >= TIME_STOP_BARS:
                exit_R = ((c-pos["entry"])/pos["risk"]) if buy else ((pos["entry"]-c)/pos["risk"])
            if exit_R is not None:
                cr = cost_R(sym, pos["entry"], pos["risk"], stopped)
                trades.append({"ts": idx[pos["i"]], "net_R": round(exit_R-cr, 3)})
                pos = None
            continue
        if not mask[i] or not (t_start <= idx[i] < t_end):
            continue
        a = atr[i]
        if not np.isfinite(a) or a <= 0:
            continue
        entry = float(c); risk = a*sl_mult
        pos = {"i": i, "day": day[i], "entry": entry, "risk": a,
               "sl": entry - a*sl_mult if buy else entry + a*sl_mult,
               "tp": entry + a*tp_mult if buy else entry - a*tp_mult}
    return pd.DataFrame(trades)


def walk_forward(df, F, cand, is_b3, out):
    t0, t1 = df.index[0], df.index[-1]
    span_y = (t1 - t0).days/365
    # adaptativo: histórico curto (crypto ~1,4-2 anos) usa treino/teste menores
    train_y = TRAIN_YEARS if span_y >= 4 else max(0.8, span_y*0.4)
    test_m = TEST_MONTHS if span_y >= 4 else (3 if span_y >= 2 else 2)
    train = pd.Timedelta(days=int(365*train_y))
    step = pd.Timedelta(days=int(30.4*test_m))
    folds = []
    start = t0 + train
    while start + step <= t1 + pd.Timedelta(days=1):
        thr_end = int(np.searchsorted(df.index.values, np.datetime64(start)))
        t = simulate_window(df, F, cand["sym"], cand["side"], cand["conds"],
                            is_b3, thr_end, start, start + step)
        if len(t) >= 5:
            folds.append({"janela": f"{start.date()}→{(start+step).date()}",
                          "n": len(t), "net": round(t.net_R.mean(), 3),
                          "tot": round(t.net_R.sum(), 1)})
        start += step
    pos = sum(1 for f in folds if f["net"] > 0)
    out(f"  WALK-FORWARD rolante ({len(folds)} janelas de {test_m}m, treino {train_y:.1f} anos):")
    for f in folds:
        out(f"    {f['janela']}  n={f['n']:<4} net {f['net']:+.3f} R  tot {f['tot']:+.1f}  "
            f"{'[+]' if f['net'] > 0 else '[-]'}")
    ok = len(folds) >= 3 and pos/len(folds) >= 0.6
    out(f"    → {pos}/{len(folds)} janelas positivas  {'✅' if ok else '❌'}")
    return ok, folds


def monte_carlo(t, out, seed=3):
    rng = np.random.RandomState(seed)
    r = t.net_R.values
    n = len(r)
    boots = np.array([r[rng.randint(0, n, n)].sum() for _ in range(BOOT_N)])
    p_neg = float((boots <= 0).mean())
    ci = np.percentile(boots, [5, 95])
    dds = np.empty(SHUF_N)
    for k in range(SHUF_N):
        eq = np.cumsum(rng.permutation(r))
        dds[k] = (eq - np.maximum.accumulate(eq)).min()
    out(f"  MONTE CARLO ({BOOT_N} bootstraps · {SHUF_N} shuffles):")
    out(f"    total real {r.sum():+.1f} R · IC90% [{ci[0]:+.1f}, {ci[1]:+.1f}] · P(total≤0) = {p_neg:.1%}")
    out(f"    drawdown esperado: mediana {np.percentile(dds,50):.1f} R · pior 5% {np.percentile(dds,5):.1f} R")
    ok = p_neg <= 0.05
    out(f"    → {'✅' if ok else '❌'} (exigido P(total≤0) ≤ 5%)")
    return ok


def _shift_bucket(b, d):
    if b in ("0", "1"):
        return None
    q = int(b[1]) + d
    return f"q{q}" if 1 <= q <= 5 else None


def sensitivity(df, F, cand, is_b3, base_net, out):
    variants = []
    for j, (feat, b) in enumerate(cand["conds"]):
        for d in (-1, 1):
            nb = _shift_bucket(b, d)
            if nb is None:
                continue
            conds = list(cand["conds"]); conds[j] = (feat, nb)
            variants.append((f"{feat}:{b}→{nb}", conds, 1.0, 2.0))
    variants.append(("TP 1.5R", cand["conds"], 1.0, 1.5))
    variants.append(("TP 2.5R", cand["conds"], 1.0, 2.5))
    cut = int(len(df)*0.7)
    pos = tot = 0
    out("  SENSIBILIDADE (condição ±1 quintil, saída alternativa):")
    for nome, conds, slm, tpm in variants:
        t = simulate_window(df, F, cand["sym"], cand["side"], conds, is_b3,
                            cut, df.index[0], df.index[-1], slm, tpm)
        if len(t) < 20:
            out(f"    {nome:<22} (n<20)"); continue
        net = t.net_R.mean(); tot += 1; pos += net > 0
        out(f"    {nome:<22} n={len(t):<5} net {net:+.3f} R  {'[+]' if net > 0 else '[-]'}")
    ok = tot >= 3 and pos/tot >= 0.6
    out(f"    → {pos}/{tot} variantes positivas  {'✅' if ok else '❌'} "
        f"(edge robusto não pode viver de um único quintil)")
    return ok


def run_group(grupo):
    from edge_discovery.feature_engine import build_features
    os.makedirs("logs", exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    logf = open(f"logs/validar_edge_go_{grupo}_{stamp}.txt", "w", encoding="utf-8")
    def out(s):
        print(s); logf.write(s+"\n"); logf.flush()
    out(f"{'#'*74}\n# BATERIA FINAL (WF rolante + Monte Carlo + sensibilidade) — {grupo} · "
        f"{datetime.now():%d/%m %H:%M}\n{'#'*74}")
    resumo = []
    for cand in [c for c in CANDIDATES if c["grupo"] == grupo and c["nome"] in GOS]:
        df = _fetch(grupo, cand["sym"], cand["tf"])
        if df is None or len(df) < 5000:
            out(f"\n!! {cand['sym']}: sem histórico"); continue
        F = build_features(df)
        is_b3 = grupo == "b3"
        out(f"\n{'='*74}\n▸ {cand['nome']}  ({cand['sym']} M{cand['tf']} {cand['side']})\n{'='*74}")
        t0 = time.time()
        base = simulate_window(df, F, cand["sym"], cand["side"], cand["conds"],
                               is_b3, int(len(df)*0.7), df.index[0], df.index[-1])
        s = stats(base.assign(gross_R=base.net_R, cost_R=0)) if not base.empty else {"n": 0}
        out(f"  BASE: n={s.get('n',0)} net {s.get('net',0):+.3f} R")
        ok_wf, _ = walk_forward(df, F, cand, is_b3, out)
        ok_mc = monte_carlo(base, out) if len(base) >= 30 else False
        ok_sn = sensitivity(df, F, cand, is_b3, s.get("net", 0), out)
        aprovado = ok_wf and ok_mc and ok_sn
        out(f"\n  ★ VEREDITO FINAL {cand['nome']}: "
            f"{'🟢 APROVADO P/ PAPER TRADING' if aprovado else '🟡 REPROVOU EM ' + ', '.join(x for x, o in [('WF', ok_wf), ('MC', ok_mc), ('SENS', ok_sn)] if not o)}"
            f"  [{time.time()-t0:.0f}s]")
        resumo.append((cand["nome"], aprovado, ok_wf, ok_mc, ok_sn))
    out(f"\n{'#'*74}\n  RESUMO {grupo}\n{'#'*74}")
    for nome, ap, wf, mc, sn in resumo:
        out(f"  {nome:<16} WF {'✅' if wf else '❌'}  MC {'✅' if mc else '❌'}  "
            f"SENS {'✅' if sn else '❌'}  → {'🟢 PAPER TRADING' if ap else '🟡 aguarda'}")
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
