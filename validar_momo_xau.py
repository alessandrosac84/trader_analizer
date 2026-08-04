"""
validar_momo_xau.py — Validação PROFUNDA do DAILY_MOMO no XAUUSD via H1.

Problema: o servidor IC Markets só entrega ~1,4 ano de M15 (50k candles).
Solução: o DAILY_MOMO é um setup de estrutura DIÁRIA (dia anterior forte →
continuação no rompimento da máx/mín). Dá para simulá-lo em candles H1, que
têm 6-8 anos de histórico — o gatilho fica um pouco mais grosso (fecho de H1
em vez de M15), mas a estrutura é a mesma.

Este script roda DUAS simulações e compara:
  A) H1 no PERÍODO COMPLETO (todo o histórico disponível)  → o veredito
  B) H1 SÓ no período coberto pelo M15 (~1,4 ano)          → calibragem:
     se (B) der próximo do resultado M15 do lab (+0.162 R), a aproximação
     H1 é confiável e (A) pode ser levado a sério.

Uso (MT5 ICMarkets aberto):  python validar_momo_xau.py
                             python validar_momo_xau.py --symbol BTCUSD
"""
import argparse
import os
import sys
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

from backtest_crypto_pro import mt5_connect, fetch, live_spread, SPREAD_FALLBACK, Tee

TARGET_RR   = 1.5
RANGE_MIN   = 1.1     # dia anterior com range >= 1.1x ATR_d
CLOSE_POS   = 0.75    # fechamento no quartil extremo
STOP_ATRD   = 0.5     # stop = 0.5x ATR diário
ENTRY_UNTIL = 20.0    # sem entradas após 20h (servidor)
DAY_CLOSE   = 23.0    # zeragem H1: última barra do dia


def day_refs_h1(h1):
    dates = pd.Series(h1.index.date, index=h1.index)
    dh = h1["High"].groupby(dates).max()
    dl = h1["Low"].groupby(dates).min()
    dc = h1["Close"].groupby(dates).last()
    atr_d = (dh - dl).rolling(14, min_periods=5).mean().shift(1)
    return pd.DataFrame({"y_high": dh.shift(1), "y_low": dl.shift(1),
                         "y_close": dc.shift(1), "y_range": (dh - dl).shift(1),
                         "atr_d": atr_d})


def simulate_h1(h1, symbol, spread, date_from=None):
    refs = day_refs_h1(h1)
    trades = []
    dates = list(refs.index)
    for date in dates:
        if date_from and date < date_from:
            continue
        r = refs.loc[date]
        if any(pd.isna(r[k]) for k in ("y_high", "y_low", "y_close", "y_range", "atr_d")):
            continue
        if not r["atr_d"] or r["y_range"] < RANGE_MIN * r["atr_d"]:
            continue
        pos = (r["y_close"] - r["y_low"]) / max(r["y_range"], 1e-9)
        if pos >= CLOSE_POS:
            want, level = "COMPRA", r["y_high"]
        elif pos <= 1 - CLOSE_POS:
            want, level = "VENDA", r["y_low"]
        else:
            continue
        day = h1[h1.index.date == date]
        if len(day) < 5:
            continue
        i0 = h1.index.get_indexer([day.index[0]])[0]
        for k in range(1, len(day) - 1):
            if day.index[k].hour >= ENTRY_UNTIL:
                break
            c = float(day.iloc[k]["Close"]); pc = float(day.iloc[k - 1]["Close"])
            fired = (pc <= level < c) if want == "COMPRA" else (pc >= level > c)
            if not fired:
                continue
            e = float(h1.iloc[i0 + k + 1]["Open"])
            buy = want == "COMPRA"
            stp = e - STOP_ATRD * r["atr_d"] if buy else e + STOP_ATRD * r["atr_d"]
            tp = e + TARGET_RR * abs(e - stp) * (1 if buy else -1)
            risk = abs(e - stp)
            if risk <= 0 or risk < 2 * spread:
                break
            gross = None; exit_r = "DAYEND"
            for j in range(i0 + k + 1, min(i0 + k + 1 + 30, len(h1))):
                ts = h1.index[j]
                hh, ll, cc = float(h1.iloc[j]["High"]), float(h1.iloc[j]["Low"]), float(h1.iloc[j]["Close"])
                if (ll <= stp) if buy else (hh >= stp):
                    gross, exit_r = -1.0, "SL"; break
                if (hh >= tp) if buy else (ll <= tp):
                    gross, exit_r = TARGET_RR, "TP"; break
                if ts.date() != date or ts.hour >= DAY_CLOSE:
                    gross = ((cc - e) / risk) if buy else ((e - cc) / risk)
                    exit_r = "DAYEND"; break
            if gross is None:
                cc = float(h1.iloc[min(i0 + k + 30, len(h1) - 1)]["Close"])
                gross = ((cc - e) / risk) if buy else ((e - cc) / risk)
            cost_r = spread / risk
            trades.append({"ts": day.index[k], "dir": want,
                           "gross_R": round(gross, 3), "cost_R": round(cost_r, 3),
                           "net_R": round(gross - cost_r, 3), "exit": exit_r})
            break   # 1 trade por dia
    return pd.DataFrame(trades)


def stats(t):
    if t.empty: return {"n": 0}
    w = t[t.net_R > 0]; l = t[t.net_R <= 0]
    gw, gl = w.net_R.sum(), -l.net_R.sum()
    eq = t.net_R.cumsum(); dd = (eq - eq.cummax()).min()
    return {"n": len(t), "win%": round(len(w) / len(t) * 100, 1),
            "net": round(t.net_R.mean(), 3),
            "PF": round(gw / gl, 2) if gl > 0 else float("inf"),
            "tot": round(t.net_R.sum(), 1), "dd": round(dd, 1)}


def report(t, label):
    print(f"\n{'='*64}\n  DAILY_MOMO — {label}\n{'='*64}")
    if t.empty:
        print("  (nenhum trade)"); return
    s = stats(t)
    print(f"  Trades {s['n']} | win {s['win%']}% | LÍQUIDA {s['net']:+.3f} R | "
          f"PF {s['PF']} | total {s['tot']:+.1f} R | DD {s['dd']}")
    t2 = t.copy()
    t2["p"] = pd.to_datetime(t2["ts"]).dt.to_period("Q").astype(str)
    pos_p = tot_p = 0
    print("  ── por trimestre ──")
    for k, g in t2.groupby("p"):
        gs = stats(g); pos_p += gs["net"] > 0; tot_p += 1
        print(f"    {k}  n={gs['n']:<4} net {gs['net']:+.3f} R  [{'+' if gs['net']>0 else '-'}]")
    if tot_p:
        print(f"  Consistência trimestral: {pos_p}/{tot_p} ({pos_p/tot_p*100:.0f}%)")
    for k, g in t.groupby("exit"):
        print(f"    saída {k:<8} n={len(g):<4} net {g.net_R.mean():+.3f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="XAUUSD")
    args = ap.parse_args()
    sym = args.symbol.upper()

    os.makedirs("logs", exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    sys.stdout = Tee(f"logs/validar_momo_{sym}_{stamp}.txt")
    print(f"\n{'#'*64}\n# VALIDAÇÃO PROFUNDA DAILY_MOMO {sym} — {datetime.now():%d/%m %H:%M}\n{'#'*64}")

    mt5 = mt5_connect()
    h1 = fetch(mt5, sym, 60, 200000)
    m15 = fetch(mt5, sym, 15, 200000)
    if h1 is None or len(h1) < 3000:
        print("sem histórico H1"); return
    spread = live_spread(mt5, sym) or SPREAD_FALLBACK.get(sym, 0.0)
    anos_h1 = len(h1) / 24 / 365
    print(f"H1: {len(h1)} candles (~{anos_h1:.1f} anos: {h1.index[0].date()} -> {h1.index[-1].date()})")
    print(f"spread usado: {spread}")

    # (A) período completo
    tA = simulate_h1(h1, sym, spread)
    report(tA, f"H1 período COMPLETO (~{anos_h1:.1f} anos)")
    tA.to_csv(f"logs/validar_momo_{sym}_full.csv", index=False)

    # (B) só o período coberto pelo M15 (para calibrar a aproximação H1 vs M15)
    if m15 is not None and len(m15) > 1000:
        d0 = m15.index[0].date()
        tB = simulate_h1(h1, sym, spread, date_from=d0)
        report(tB, f"H1 restrito ao período do M15 (desde {d0}) — comparar com +0.162 R do lab M15")
        print("\n  Leitura: se (B) ficou na mesma direção/ordem de grandeza do lab M15,")
        print("  a aproximação H1 é confiável e o resultado (A) é o veredito de longo prazo.")

    print(f"\n# FIM — envie logs/validar_momo_{sym}_{stamp}.txt")


if __name__ == "__main__":
    main()
