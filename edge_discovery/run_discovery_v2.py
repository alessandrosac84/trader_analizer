"""
run_discovery_v2.py — Edge Discovery Engine V2 (comportamentos, não features).

Pipeline por ativo × TF:
  fetch → features → labels → expectância (MFE/MAE) →
  ESTADOS de mercado (clustering) → SEQUÊNCIAS (n-grams) →
  MOTOR DE HIPÓTESES (milhares de combinações + FDR + Monte Carlo) →
  ANÁLOGOS históricos (memória) →
  research_report.md + candidatos prontos p/ o harness com custos.
Ao final: UNIVERSAL SCORE — hipóteses aprovadas em 2+ ativos.

Uso:  python -m edge_discovery.run_discovery_v2 --grupo b3|crypto
      (ou o comando único: python rodar_discovery_tudo.py --v2)
"""
import argparse
import os
import sys
import time
import warnings
from collections import defaultdict
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

from edge_discovery.config import ASSETS, BARS_MAX
from edge_discovery.feature_engine import build_features
from edge_discovery.label_generator import build_labels
from edge_discovery.discovery_engine import bucketize
from edge_discovery.expectancy_engine import build_expectancy
from edge_discovery.market_state_engine import fit_states, state_expectancy
from edge_discovery.sequence_engine import mine_sequences
from edge_discovery.hypothesis_engine import run_hypotheses
from edge_discovery.similarity_engine import analog_stats, analog_predictiveness
from edge_discovery.setup_generator import save_candidates

TFS_V2 = [15, 60]                 # V2 foca nos TFs operáveis (M5 = muito ruído/custo)
LABELS_V2 = ["hit_2R_long", "hit_2R_short"]


def _fetch(grupo, symbol, tf, bars):
    if grupo == "b3":
        from backtest_kimi_real import fetch
        df = fetch(symbol, tf, bars)
        return None if df is None else df[["Open", "High", "Low", "Close", "Volume"]].copy()
    from backtest_crypto_pro import mt5_connect, fetch
    if not hasattr(_fetch, "_mt5"):
        _fetch._mt5 = mt5_connect()
    return fetch(_fetch._mt5, symbol, tf, bars)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--grupo", choices=["b3", "crypto"], required=True)
    ap.add_argument("--symbol", default=None)
    ap.add_argument("--tfs", default=None)
    ap.add_argument("--bars", type=int, default=BARS_MAX)
    args = ap.parse_args()

    symbols = [args.symbol.upper()] if args.symbol else ASSETS[args.grupo]
    tfs = [int(x) for x in args.tfs.split(",")] if args.tfs else TFS_V2

    os.makedirs("logs", exist_ok=True)
    root = Path(__file__).parent
    os.makedirs(root/"out", exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    rep_path = f"logs/research_report_{args.grupo}_{stamp}.md"
    rep = open(rep_path, "w", encoding="utf-8")
    def w(s=""):
        print(s); rep.write(s+"\n"); rep.flush()

    w(f"# RESEARCH REPORT V2 — grupo {args.grupo} · {datetime.now():%d/%m/%Y %H:%M}")
    w(f"Ativos: {', '.join(symbols)} · TFs: {tfs} · Labels: {LABELS_V2}")
    w("Bateria de validação: FDR Benjamini-Hochberg → OOS 70/30 → walk-forward/ano → Monte Carlo (200 réplicas)\n")

    universal = defaultdict(list)      # hipótese → [(asset, tf, label, lift_oos, score)]
    all_cands = []

    for sym in symbols:
        for tf in tfs:
            t0 = time.time()
            df = _fetch(args.grupo, sym, tf, args.bars)
            if df is None or len(df) < 5000:
                w(f"## {sym} M{tf} — sem histórico suficiente\n"); continue
            F = build_features(df)
            Y = build_labels(df)
            E = build_expectancy(df)
            B = bucketize(F)
            years = pd.Series(df.index.year, index=df.index)
            anos = (df.index[-1]-df.index[0]).days/365
            w(f"\n## {sym} · M{tf} — {len(df)} candles (~{anos:.1f} anos)")

            # ── 1. Estados de mercado ──
            try:
                states, cent = fit_states(F)
                st_tab = state_expectancy(states, Y, E)
                if not st_tab.empty:
                    w("\n### Estados de mercado (clustering) — expectância bruta por estado")
                    for r in st_tab.itertuples():
                        w(f"- **{r.estado}** ({r.pct_tempo}% do tempo, n={r.n}): "
                          f"long {r.exp_long_R:+.2f}R · short {r.exp_short_R:+.2f}R · "
                          f"MFE/MAE p50 {r.mfe_p50}/{r.mae_p50}"
                          + (f" · OOS p2R {r.p2R_long_oos}" if r.p2R_long_oos else ""))
                    F2 = F.copy(); F2["state"] = states   # estado vira feature p/ hipóteses
                    B = bucketize(F2)
            except Exception as exc:
                w(f"- estados: erro {exc}")

            # ── 2+4+6+10+13+14. Motor de hipóteses ──
            for label in LABELS_V2:
                y = Y[label]
                hyp, n_tested = run_hypotheses(B, F, y, years)
                w(f"\n### Hipóteses `{label}` — testadas {n_tested}, "
                  f"aprovadas {0 if hyp.empty else int(hyp.aprovada.sum())}")
                if not hyp.empty:
                    for r in hyp[hyp.aprovada].head(6).itertuples():
                        w(f"- **{r.hipotese}** → score {r.score} · {r.explicacao}")
                        universal[r.hipotese].append((sym, tf, label, r.lift_oos, r.score))
                        all_cands.append({"asset": sym, "tf": f"M{tf}", "label": label,
                                          "condicoes": r.hipotese, "n": r.n,
                                          "lift_oos": r.lift_oos, "exp_bruta_R": r.exp_bruta_R,
                                          "score": r.score, "anos_pos": r.anos_pos,
                                          "obs": "sem custos — validar no harness"})
                    rej = hyp[~hyp.aprovada].head(2)
                    for r in rej.itertuples():
                        w(f"- rejeitada: {r.hipotese} ({r.explicacao})")

                # ── 3. Sequências ──
                seq = mine_sequences(F, y)
                if not seq.empty and seq.oos_ok.any():
                    top = seq[seq.oos_ok].head(3)
                    w(f"- sequências (n-grams) com OOS ok: "
                      + "; ".join(f"`{r.seq}` (lift {r.lift_in}→{r.lift_oos}, n={r.n_in})"
                                  for r in top.itertuples()))

            # ── 5. Análogos históricos ──
            try:
                pred = analog_predictiveness(F, Y)
                if pred:
                    w(f"\n### Memória histórica (análogos): otimistas→{pred['p_real_quando_analogos_otimistas']} "
                      f"vs pessimistas→{pred['p_real_quando_analogos_pessimistas']} "
                      f"({'DISCRIMINA ✔' if pred['discrimina'] else 'não discrimina'})")
                now = analog_stats(F, Y, E)
                w(f"- momento ATUAL ({now['ts']}): {now['k']} análogos → "
                  f"long {now['exp_long_R']:+.2f}R · short {now['exp_short_R']:+.2f}R")
            except Exception as exc:
                w(f"- análogos: erro {exc}")
            w(f"\n*({time.time()-t0:.0f}s)*")

    # ── 11. Universal Edge ──
    w(f"\n\n---\n# UNIVERSAL EDGES (mesma hipótese aprovada em 2+ ativos)")
    multi = {h: v for h, v in universal.items() if len({a for a, *_ in v}) >= 2}
    if multi:
        for h, v in sorted(multi.items(), key=lambda kv: -len(kv[1]))[:15]:
            assets = ", ".join(f"{a} M{t} ({lab.split('_')[-1]}, lift {lf})" for a, t, lab, lf, _ in v)
            w(f"- **{h}** → {assets}")
    else:
        w("- nenhuma hipótese idêntica aprovada em 2+ ativos nesta rodada")

    cand_path = str(root/"out"/f"candidatos_v2_{args.grupo}_{stamp}.json")
    save_candidates(all_cands, cand_path)
    w(f"\n# CANDIDATOS: {len(all_cands)} → `{cand_path}`")
    w("Próximo passo: harness COM CUSTOS decide o GO.")
    w(f"\n*Relatório: {rep_path}*")
    rep.close()


if __name__ == "__main__":
    main()
