"""
run_discovery.py — CLI do Edge Discovery Engine (um comando faz tudo).

Pipeline por ativo × timeframe: fetch MT5 → features → labels → banco →
descoberta univariada/bivariada → validação OOS + walk-forward por ano →
expectativa por hora/dia/sessão → setups candidatos → relatório markdown.

⚠️ Dois passes (mesma limitação de sempre — 1 MT5 por processo):
   python -m edge_discovery.run_discovery --grupo b3       (MT5 XP aberto)
   python -m edge_discovery.run_discovery --grupo crypto   (MT5 IC aberto)
Opções: --symbol XAUUSD · --tfs 15,60 · --label hit_2R_long
Saída:  edge_discovery/db/*.parquet (banco de features)
        logs/edge_discovery_<grupo>_<stamp>.md (relatório)
        edge_discovery/out/candidatos_<grupo>_<stamp>.json
"""
import argparse
import os
import sys
import time
import warnings
from datetime import datetime
from pathlib import Path

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

import numpy as np
import pandas as pd

from edge_discovery.config import ASSETS, TIMEFRAMES, BARS_MAX
from edge_discovery.feature_engine import build_features
from edge_discovery.label_generator import build_labels
from edge_discovery.discovery_engine import (bucketize, discover, by_dimension,
                                             sklearn_importance)
from edge_discovery.setup_generator import make_candidates, save_candidates

LABELS_DEFAULT = ["hit_2R_long", "hit_2R_short"]


def _fetch(grupo, symbol, tf, bars):
    if grupo == "b3":
        from backtest_kimi_real import fetch
        df = fetch(symbol, tf, bars)
        if df is None:
            return None
        return df[["Open", "High", "Low", "Close", "Volume"]].copy()
    from backtest_crypto_pro import mt5_connect, fetch
    if not hasattr(_fetch, "_mt5"):
        _fetch._mt5 = mt5_connect()
    return fetch(_fetch._mt5, symbol, tf, bars)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--grupo", choices=["b3", "crypto"], required=True)
    ap.add_argument("--symbol", default=None)
    ap.add_argument("--tfs", default=None, help="ex.: 15,60 (padrão: todos)")
    ap.add_argument("--labels", default=",".join(LABELS_DEFAULT))
    ap.add_argument("--bars", type=int, default=BARS_MAX)
    args = ap.parse_args()

    symbols = [args.symbol.upper()] if args.symbol else ASSETS[args.grupo]
    tfs = [int(x) for x in args.tfs.split(",")] if args.tfs else TIMEFRAMES
    labels = [x.strip() for x in args.labels.split(",") if x.strip()]

    root = Path(__file__).parent
    os.makedirs(root/"db", exist_ok=True)
    os.makedirs(root/"out", exist_ok=True)
    os.makedirs("logs", exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    rep_path = f"logs/edge_discovery_{args.grupo}_{stamp}.md"
    rep = open(rep_path, "w", encoding="utf-8")
    def w(s=""):
        print(s); rep.write(s+"\n"); rep.flush()

    w(f"# EDGE DISCOVERY — grupo {args.grupo} · {datetime.now():%d/%m/%Y %H:%M}")
    w(f"Ativos: {', '.join(symbols)} · TFs: {tfs} · Labels: {labels}\n")
    all_cands = []

    for sym in symbols:
        for tf in tfs:
            t0 = time.time()
            df = _fetch(args.grupo, sym, tf, args.bars)
            if df is None or len(df) < 5000:
                w(f"## {sym} M{tf} — sem histórico suficiente\n"); continue
            F = build_features(df)
            Y = build_labels(df)
            years = pd.Series(df.index.year, index=df.index)
            # banco unificado (Etapa 3)
            db = pd.concat([F, Y], axis=1)
            db.insert(0, "asset", sym); db.insert(1, "tf", f"M{tf}")
            try:
                db.to_parquet(root/"db"/f"{sym.replace('$','_')}_M{tf}.parquet")
            except Exception:
                db.to_csv(root/"db"/f"{sym.replace('$','_')}_M{tf}.csv.gz", compression="gzip")
            anos = (df.index[-1]-df.index[0]).days/365
            w(f"## {sym} · M{tf} — {len(df)} candles (~{anos:.1f} anos) "
              f"[{df.index[0].date()} → {df.index[-1].date()}]")

            B = bucketize(F)
            for label in labels:
                if label not in Y.columns:
                    continue
                y = Y[label]
                base, pat, uni = discover(B, y, years)
                w(f"\n### label `{label}` — taxa-base {base:.1%}")
                # Etapas 12-14: hora / dia / sessão
                for dim, nome in (("hour", "hora"), ("dow", "dia da semana")):
                    tb = by_dimension(F, y, dim)
                    if not tb.empty:
                        top = tb.head(3); bot = tb.tail(2)
                        w(f"- melhor {nome}: " + "; ".join(
                            f"{i} (lift {r.lift}, n={r.n})" for i, r in top.iterrows()))
                        w(f"- pior {nome}: " + "; ".join(
                            f"{i} (lift {r.lift})" for i, r in bot.iterrows()))
                if pat is None or pat.empty:
                    w("- nenhum padrão passou os filtros (suporte/lift/z)"); continue
                ok = pat[pat.oos_ok]
                w(f"- padrões candidatos: {len(pat)} · **sobreviveram ao OOS: {len(ok)}**")
                for r in ok.head(8).itertuples():
                    w(f"  - `{r.pattern}` → P={r.p_in:.1%} (lift {r.lift_in}, n={r.n_in}) | "
                      f"OOS P={r.p_oos:.1%} (lift {r.lift_oos}, n={r.n_oos}) | anos+ {r.anos_pos}")
                cands = make_candidates(sym, tf, label, base, pat)
                all_cands += cands
            imp = sklearn_importance(F, Y[labels[0]]) if labels else None
            if imp is not None:
                w(f"\n- feature importance (RandomForest, top 10): "
                  + ", ".join(f"{k}={v:.3f}" for k, v in imp.head(10).items()))
            w(f"- ({time.time()-t0:.0f}s)\n")

    cand_path = str(root/"out"/f"candidatos_{args.grupo}_{stamp}.json")
    save_candidates(all_cands, cand_path)
    w(f"\n---\n## SETUPS CANDIDATOS: {len(all_cands)} → `{cand_path}`")
    w("Próximo passo: codificar os melhores no harness COM CUSTOS "
      "(backtest_setups_kimi2 / backtest_acoes_b3) — só ele dá o veredito GO.")
    w(f"\n*Relatório: {rep_path}*")
    rep.close()


if __name__ == "__main__":
    main()
