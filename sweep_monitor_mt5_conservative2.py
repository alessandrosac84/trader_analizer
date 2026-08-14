"""
Segunda varredura CONSERVADORA Monitor MT5 (score v6, sem IA).

Grade curada em torno do pacote GO atual + refinamentos leves.
Critérios de aceitação rígidos (n alto / OOS / não piorar exp).
Não altera regras — só reporta candidatos vs baseline GO.
"""
from __future__ import annotations

import itertools
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUNBUFFERED", "1")

import pandas as pd

from backtest_pro import fetch_mt5
from sweep_monitor_mt5_rules import (
    HOUR_KEYS,
    _metrics,
    _print_m,
    _resolve_cache_path,
    load_signal_cache,
    prepare_bars,
    replay,
)
from services.monitor_mt5_rules import MONITOR_RULES, WINDOW_A_B

# Pacotes GO atuais (referência — não piorar)
GO = {
    "WIN": {
        "min_score": 9, "max_score": 11, "hours": "A_B",
        "hours_arg": WINDOW_A_B, "adx_min": None, "vol_min": None,
        "htf_aligned": False, "block_dow": (0,), "target_rr": 2.0,
        "label": "GO_WIN sc>=9|A_B|blkMon|RR=2.0",
        # métricas documentadas
        "ref": {"n": 686, "WR": 45.0, "PF": 1.33, "exp": 0.170, "oos": 70.0},
    },
    "WDO": {
        "min_score": 10, "max_score": 11, "hours": "A_B",
        "hours_arg": WINDOW_A_B, "adx_min": 30.0, "vol_min": 1.3,
        "htf_aligned": False, "block_dow": None, "target_rr": 2.0,
        "label": "GO_WDO sc>=10|A_B|ADX30|vol1.3|RR=2.0",
        "ref": {"n": 159, "WR": 53.5, "PF": 1.72, "exp": 0.358, "oos": 66.0},
    },
}

# Aceitação
ACCEPT = {
    "WIN": {"min_n": 350, "min_oos": 65.0, "min_pf": 1.25,
            "min_exp": 0.170,  # não piorar vs GO
            "min_delta_exp": 0.02,  # ganho claro p/ trocar
            "min_delta_pf": 0.05},
    "WDO": {"min_n": 100, "min_oos": 65.0, "min_pf": 1.50,
            "min_exp": 0.358,
            "min_delta_exp": 0.03,
            "min_delta_pf": 0.08},
}


def win_candidates():
    """Prioridade WIN: score≥10, ADX/vol leves, janelas, RR 2.0–2.2."""
    hours = ["A_B", "no_late", "skip_open_lunch", "am", "core"]
    score_mins = [9, 10]
    adx_mins = [None, 22, 25, 28]
    vol_mins = [None, 1.0, 1.15, 1.3]
    block_dows = [(0,), None, (0, 4)]
    rrs = [2.0, 2.1, 2.2]
    # grade reduzida: só combinações próximas do GO
    for smin, hkey, adx, vol, bdow, rr in itertools.product(
        score_mins, hours, adx_mins, vol_mins, block_dows, rrs
    ):
        # descartar filtros pesados em score 9 (já testado amplo)
        if smin == 9 and adx is not None and adx >= 28 and vol and vol >= 1.3:
            continue
        # foco: ou é GO-like, ou score10, ou filtro leve
        is_go_like = (smin == 9 and hkey == "A_B" and bdow == (0,))
        is_sc10 = smin == 10
        is_light = (adx in (None, 22, 25)) and (vol in (None, 1.0, 1.15))
        if not (is_go_like or is_sc10 or (hkey in ("A_B", "no_late") and is_light)):
            continue
        yield {
            "min_score": smin, "max_score": 11, "hours": hkey,
            "hours_arg": HOUR_KEYS[hkey], "adx_min": adx, "vol_min": vol,
            "htf_aligned": False, "block_dow": bdow, "target_rr": rr,
            "label": (
                f"sc{smin}-11|h={hkey}|adx>={adx or 0}|vol>={vol or 0}"
                f"|blkDow={bdow or '-'}|RR={rr}"
            ),
        }


def wdo_candidates():
    """WDO: só refinamentos leves do GO; se duvidar, manter."""
    hours = ["A_B", "no_late"]
    score_mins = [10]
    adx_mins = [28, 30, 32]
    vol_mins = [1.15, 1.3, 1.4]
    block_dows = [None, (4,), (0,)]
    rrs = [2.0, 2.1, 2.2]
    for smin, hkey, adx, vol, bdow, rr in itertools.product(
        score_mins, hours, adx_mins, vol_mins, block_dows, rrs
    ):
        yield {
            "min_score": smin, "max_score": 11, "hours": hkey,
            "hours_arg": HOUR_KEYS[hkey], "adx_min": adx, "vol_min": vol,
            "htf_aligned": False, "block_dow": bdow, "target_rr": rr,
            "label": (
                f"sc{smin}-11|h={hkey}|adx>={adx or 0}|vol>={vol or 0}"
                f"|blkDow={bdow or '-'}|RR={rr}"
            ),
        }


def beats_go(asset: str, m: dict, go_m: dict) -> tuple[bool, str]:
    acc = ACCEPT[asset]
    n = int(m.get("n") or 0)
    oos = float(m.get("oos%") or 0)
    pf = float(m.get("PF") or 0)
    exp = float(m.get("exp_net_R") or 0)
    go_exp = float(go_m.get("exp_net_R") or 0)
    go_pf = float(go_m.get("PF") or 0)
    go_n = int(go_m.get("n") or 0)
    go_dd = float(go_m.get("maxDD_R") or 0)
    dd = float(m.get("maxDD_R") or 0)

    if n < acc["min_n"]:
        return False, f"n={n}<{acc['min_n']}"
    if oos < acc["min_oos"]:
        return False, f"oos={oos:.0f}<{acc['min_oos']}"
    if pf < acc["min_pf"]:
        return False, f"PF={pf}<{acc['min_pf']}"
    if exp < go_exp - 1e-9:
        return False, f"exp={exp:+.3f}<GO {go_exp:+.3f}"
    # exigir ganho claro em exp OU (PF+DD sem perder n demais)
    delta_exp = exp - go_exp
    delta_pf = pf - go_pf
    n_ok = n >= 0.7 * go_n  # não matar amostra
    if not n_ok:
        return False, f"n caiu demais ({n} vs GO {go_n})"
    clear = (delta_exp >= acc["min_delta_exp"] and delta_pf >= 0) or (
        delta_pf >= acc["min_delta_pf"] and delta_exp >= 0 and dd >= go_dd - 1.0
    )
    if not clear:
        return False, (
            f"ganho insuficiente Δexp={delta_exp:+.3f} ΔPF={delta_pf:+.2f}"
        )
    # descartar "bonito demais" / n suspeito
    if asset == "WIN" and n < 300:
        return False, "n baixo / cheiro overfit"
    if asset == "WDO" and n < 100:
        return False, "n baixo / cheiro overfit"
    if pf >= 2.5 and n < 200:
        return False, "PF alto + n baixo (overfit)"
    return True, "ACEITA"


def main():
    os.makedirs("logs", exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    logpath = f"logs/sweep_monitor_mt5_cons2_{stamp}.txt"

    class Tee:
        def __init__(self, path):
            self.file = open(path, "a", encoding="utf-8")
            self.stdout = sys.__stdout__

        def write(self, data):
            try:
                self.stdout.write(data)
            except UnicodeEncodeError:
                enc = getattr(self.stdout, "encoding", None) or "utf-8"
                self.stdout.write(
                    data.encode(enc, errors="replace").decode(enc, errors="replace")
                )
            self.file.write(data)
            self.file.flush()

        def flush(self):
            self.stdout.flush()
            self.file.flush()

    sys.stdout = Tee(logpath)
    print(f"\n{'#' * 70}")
    print("# SWEEP CONSERVADOR #2 — Monitor MT5 (v6, sem IA)")
    print(f"# {datetime.now():%d/%m/%Y %H:%M:%S}")
    print(f"# Log: {logpath}")
    print(f"{'#' * 70}\n", flush=True)

    cache_hint = "logs/sweep_mt5_WIN_sigs_20260807_2033.csv"
    t0 = time.time()
    results = {}

    for sym, asset, cand_fn in (
        ("WIN$D", "WIN", win_candidates),
        ("WDO$D", "WDO", wdo_candidates),
    ):
        print(f"\n{'=' * 70}\n>>> {sym} ({asset})\n{'=' * 70}", flush=True)
        df = fetch_mt5(sym, "15", 200000)
        if df is None or len(df) < 1000:
            print(f"!! sem dados {sym}", flush=True)
            continue
        print(f"{sym}: {len(df)} M15 ({df.index[0]} -> {df.index[-1]})", flush=True)
        bars = prepare_bars(df)
        cpath = _resolve_cache_path(cache_hint, asset)
        if cpath is None:
            # fallback WDO cache 0716
            cpath = _resolve_cache_path(
                f"logs/sweep_mt5_{asset}_sigs_20260808_0716.csv", asset
            )
        if cpath is None:
            print("!! cache de sinais nao encontrado", flush=True)
            continue
        print(f"Cache: {cpath}", flush=True)
        sigs = load_signal_cache(cpath, bars)
        print(f"Sinais: {len(sigs)}", flush=True)

        go_cfg = {k: GO[asset][k] for k in (
            "min_score", "max_score", "hours", "hours_arg", "adx_min",
            "vol_min", "htf_aligned", "block_dow", "label",
        )}
        go_rr = GO[asset]["target_rr"]
        go_tr = replay(bars, sym, sigs, go_cfg, target_rr=go_rr)
        go_m = _metrics(go_tr)
        print("Pacote GO atual (replay):", flush=True)
        _print_m("GO", go_m)
        print(f"  (ref doc: {GO[asset]['ref']})", flush=True)

        cands = list(cand_fn())
        print(f"Avaliando {len(cands)} candidatos...", flush=True)
        rows = []
        accepted = []
        t1 = time.time()
        for i, cfg in enumerate(cands, 1):
            rr = cfg["target_rr"]
            tr = replay(bars, sym, sigs, cfg, target_rr=rr)
            m = _metrics(tr)
            if m["n"] < 40:
                continue
            ok, why = beats_go(asset, m, go_m)
            row = {**cfg, **m, "accept": ok, "why": why}
            rows.append(row)
            if ok:
                accepted.append(row)
            if i % 50 == 0:
                print(f"  ... {i}/{len(cands)}", flush=True)
        print(f"  feito em {time.time()-t1:.1f}s | validos n>=40: {len(rows)}",
              flush=True)

        # Top por exp entre robustos (mesmo sem aceitar)
        acc_cfg = ACCEPT[asset]
        robust = [
            r for r in rows
            if r["n"] >= acc_cfg["min_n"]
            and r["oos%"] >= acc_cfg["min_oos"]
            and r["PF"] >= acc_cfg["min_pf"]
            and r["exp_net_R"] >= go_m.get("exp_net_R", 0) - 0.01
        ]
        robust.sort(key=lambda r: (r["exp_net_R"], r["PF"], r["n"]), reverse=True)
        rows.sort(key=lambda r: (r["exp_net_R"], r["PF"], r["n"]), reverse=True)

        print(f"\n  Robustos (n>={acc_cfg['min_n']} OOS>={acc_cfg['min_oos']} "
              f"PF>={acc_cfg['min_pf']} exp≈GO): {len(robust)}", flush=True)
        for i, r in enumerate(robust[:12], 1):
            print(
                f"  R#{i:02d} {r['label']}\n"
                f"       n={r['n']} WR={r['win%']:.1f}% PF={r['PF']} "
                f"exp={r['exp_net_R']:+.3f} OOS={r['oos%']:.0f}% "
                f"net={r['total_net_R']:+.1f} DD={r['maxDD_R']} "
                f"| {r['why']}",
                flush=True,
            )

        print(f"\n  ACEITOS p/ troca: {len(accepted)}", flush=True)
        for r in accepted[:8]:
            _print_m(f"OK {r['label'][:50]}", r)

        # Top absoluto com n alto (diagnóstico)
        hi_n = [r for r in rows if r["n"] >= acc_cfg["min_n"]]
        hi_n.sort(key=lambda r: r["exp_net_R"], reverse=True)
        print(f"\n  Top exp com n>={acc_cfg['min_n']} (mesmo abaixo do GO):",
              flush=True)
        for i, r in enumerate(hi_n[:8], 1):
            print(
                f"  #{i} n={r['n']} PF={r['PF']} exp={r['exp_net_R']:+.3f} "
                f"OOS={r['oos%']:.0f}% | {r['label']}",
                flush=True,
            )

        out_csv = f"logs/sweep_mt5_{asset}_cons2_{stamp}.csv"
        pd.DataFrame([
            {k: v for k, v in r.items() if k != "hours_arg"} for r in rows
        ]).to_csv(out_csv, index=False)
        print(f"  CSV: {out_csv}", flush=True)

        best_ok = accepted[0] if accepted else None
        if best_ok:
            accepted.sort(
                key=lambda r: (r["exp_net_R"], r["PF"], r["n"]), reverse=True
            )
            best_ok = accepted[0]
        results[asset] = {
            "go_replay": {k: go_m.get(k) for k in
                          ("n", "win%", "PF", "exp_net_R", "total_net_R",
                           "oos%", "maxDD_R")},
            "go_ref": GO[asset]["ref"],
            "n_candidates": len(cands),
            "n_robust": len(robust),
            "n_accepted": len(accepted),
            "best_accepted": (
                {k: best_ok.get(k) for k in (
                    "label", "min_score", "max_score", "hours", "adx_min",
                    "vol_min", "block_dow", "target_rr", "n", "win%", "PF",
                    "exp_net_R", "total_net_R", "oos%", "maxDD_R", "why"
                )} if best_ok else None
            ),
            "top_robust": [
                {k: r.get(k) for k in (
                    "label", "n", "win%", "PF", "exp_net_R", "oos%",
                    "maxDD_R", "total_net_R", "why"
                )} for r in robust[:5]
            ],
            "decision": (
                "UPDATE" if best_ok else "KEEP_GO (otimo local / sem ganho robusto)"
            ),
        }

    jpath = f"logs/sweep_monitor_mt5_cons2_best_{stamp}.json"
    with open(jpath, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)

    print(f"\n{'#' * 70}\n# DECISAO\n{'#' * 70}", flush=True)
    for asset, pack in results.items():
        print(f"\n[{asset}] {pack['decision']}", flush=True)
        _print_m("GO_replay", pack["go_replay"])
        if pack["best_accepted"]:
            b = pack["best_accepted"]
            print(f"  BEST_OK: {b.get('label')}", flush=True)
            _print_m("candidate", b)
        else:
            print("  nenhum candidato passou nos criterios conservadores", flush=True)

    print(f"\nJSON: {jpath}", flush=True)
    print(f"Log: {logpath}", flush=True)
    print(f"Total {(time.time()-t0)/60:.1f} min", flush=True)
    print(f"MONITOR_RULES live: { {k: MONITOR_RULES[k].get('note') for k in MONITOR_RULES} }",
          flush=True)


if __name__ == "__main__":
    main()
