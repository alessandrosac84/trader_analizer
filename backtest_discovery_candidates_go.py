"""
backtest_discovery_candidates_go.py — candidatos Edge Discovery V2 × régua GO COM CUSTOS.

Lê JSON de candidatos (ex.: edge_discovery/out/candidatos_v2_crypto_*.json),
traduz condicoes "feat=qN & …" + label hit_2R_long/short em trades reais
(SL 1×ATR · TP 2×ATR · custos · thresholds só in-sample) e aplica a régua GO.

Uso:
  python -u backtest_discovery_candidates_go.py \\
    --json edge_discovery/out/candidatos_v2_crypto_20260726_2104.json --top 40
"""
from __future__ import annotations

import argparse
import json
import os
import re
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

from backtest_edge_candidates import (
    OOS_FRAC, TIME_STOP_BARS, cost_R, make_mask, simulate, stats, consistency, _fetch,
)

_TF_RE = re.compile(r"^M?(\d+)$", re.I)


def _parse_tf(tf) -> int:
    if isinstance(tf, int):
        return tf
    m = _TF_RE.match(str(tf).strip())
    return int(m.group(1)) if m else 15


def _parse_conds(s: str):
    out = []
    for part in str(s or "").split("&"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        feat, bucket = part.split("=", 1)
        out.append((feat.strip(), bucket.strip()))
    return out


def _side_from_label(label: str) -> str:
    lab = (label or "").lower()
    if "short" in lab or "sell" in lab or "venda" in lab:
        return "VENDA"
    return "COMPRA"


def _cand_nome(c, idx: int) -> str:
    asset = str(c.get("asset", "SYM")).replace("$", "")
    side = "L" if _side_from_label(c.get("label", "")) == "COMPRA" else "S"
    return f"{asset}_{side}_{idx:02d}"


def load_candidates(path: str, top: int):
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise SystemExit(f"JSON inválido (esperado lista): {path}")
    ranked = sorted(raw, key=lambda x: float(x.get("score") or 0), reverse=True)
    cands = []
    for i, c in enumerate(ranked[:top], 1):
        conds = _parse_conds(c.get("condicoes") or c.get("conds") or "")
        if not conds:
            continue
        sym = str(c.get("asset") or c.get("sym") or "").upper().strip()
        if not sym:
            continue
        cands.append(dict(
            nome=_cand_nome(c, i),
            sym=sym,
            tf=_parse_tf(c.get("tf", 15)),
            side=_side_from_label(c.get("label", "hit_2R_long")),
            conds=conds,
            score=float(c.get("score") or 0),
            lift_oos=c.get("lift_oos"),
            exp_bruta_R=c.get("exp_bruta_R"),
        ))
    return cands


def run(json_path: str, top: int, bars: int):
    cands = load_candidates(json_path, top)
    if not cands:
        raise SystemExit("Nenhum candidato com condições parseáveis.")
    # crypto vs b3 pelo símbolo
    crypto_syms = {"BTCUSD", "ETHUSD", "XAUUSD", "EURUSD", "GBPUSD"}
    os.makedirs("logs", exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    logf = open(f"logs/discovery_candidates_go_{stamp}.txt", "w", encoding="utf-8")

    def out(s=""):
        print(s)
        logf.write(s + "\n")
        logf.flush()

    out("#" * 74)
    out(f"# DISCOVERY CANDIDATES × GO + CUSTOS · {datetime.now():%d/%m %H:%M}")
    out(f"# json={json_path} · top={top} · n_ok={len(cands)}")
    out(f"# SL 1×ATR · TP 2×ATR · time-stop {TIME_STOP_BARS} · thresholds IS-only")
    out("#" * 74)

    ranking, cache = [], {}
    goes, yellows = [], []

    for cand in cands:
        grupo = "crypto" if cand["sym"] in crypto_syms else "b3"
        key = (cand["sym"], cand["tf"], grupo)
        if key not in cache:
            df = _fetch(grupo, cand["sym"], cand["tf"], bars=bars)
            if df is None or len(df) < 3000:
                out(f"\n!! {cand['sym']} M{cand['tf']}: sem histórico")
                cache[key] = None
                continue
            from edge_discovery.feature_engine import build_features
            cache[key] = (df, build_features(df), grupo)
        if cache[key] is None:
            continue
        df, F, g = cache[key]
        t0 = time.time()
        t = simulate(df, F, cand["sym"], cand["side"], cand["conds"], g == "b3")
        s = stats(t)
        cons = consistency(t)
        anos = (df.index[-1] - df.index[0]).days / 365
        out(f"\n▸ {cand['nome']}  ({cand['sym']} M{cand['tf']} {cand['side']}, "
            f"score={cand['score']:.1f}, ~{anos:.1f}a)  [{time.time()-t0:.0f}s]")
        out(f"  conds: {' & '.join(f'{f}={b}' for f, b in cand['conds'])}")
        if s["n"] == 0:
            out("  (sem trades)")
            continue
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
              and s_oos.get("n", 0) >= 15 and s_oos.get("net", -9) >= 0.08
              and s_oos.get("PF", 0) >= 1.15)
        quase = (not go) and s["net"] > 0.05 and s["PF"] >= 1.15
        verd = "🟢 GO" if go else ("🟡 quase" if quase else "🔴")
        out(f"  VEREDITO: {verd}")
        row = {"nome": cand["nome"], "sym": cand["sym"], **s, "cons": cons,
               "oos_net": s_oos.get("net"), "verd": verd}
        ranking.append(row)
        if go:
            goes.append(cand["nome"])
        elif quase:
            yellows.append(cand["nome"])
        try:
            t.to_csv(f"logs/disc_cand_{cand['nome']}.csv", index=False)
        except Exception:
            pass

    out(f"\n{'#' * 74}\n  RANKING\n{'#' * 74}")
    for r in sorted(ranking, key=lambda x: -(x.get("net") or -9)):
        out(f"  {r['nome']:<22} n={r['n']:<5} net {r['net']:+.3f} R  PF {r['PF']:<5} "
            f"cons {r['cons']}%  OOS {r['oos_net']}  {r['verd']}")
    out(f"\n# GO: {goes}")
    out(f"# amarelos: {yellows}")
    out(f"# FIM — logs/discovery_candidates_go_{stamp}.txt")
    logf.close()
    return stamp


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", required=True, help="caminho do candidatos_v2_*.json")
    ap.add_argument("--top", type=int, default=40, help="top N por score")
    ap.add_argument("--bars", type=int, default=200000)
    args = ap.parse_args()
    run(args.json, args.top, args.bars)


if __name__ == "__main__":
    main()
