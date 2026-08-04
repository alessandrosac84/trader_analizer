"""
rodar_discovery_tudo.py — Edge Discovery COMPLETO em UM comando.

Roda os dois grupos em sequência, cada um num subprocesso próprio (a lib
MetaTrader5 é singleton por processo — em subprocessos separados, o grupo b3
conecta na MT5 da XP via MT5_* e o grupo crypto na MT5 da IC via MT5_CRYPTO_*,
sem conflito).

⚠️ ÚNICO REQUISITO: as DUAS MT5 abertas ao mesmo tempo (XP e IC Markets).

Uso:
    python rodar_discovery_tudo.py                 # tudo (todos ativos, todos TFs)
    python rodar_discovery_tudo.py --tfs 15,60     # só M15 e H1 (mais rápido)
    python rodar_discovery_tudo.py --labels hit_2R_long,hit_2R_short
Saída: um relatório por grupo em logs/edge_discovery_<grupo>_*.md
       + candidatos em edge_discovery/out/
"""
import argparse
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tfs", default=None)
    ap.add_argument("--labels", default=None)
    ap.add_argument("--bars", type=int, default=None)
    ap.add_argument("--grupos", default="b3,crypto")
    ap.add_argument("--v2", action="store_true",
                    help="roda o Edge Discovery V2 (estados, sequências, hipóteses, Monte Carlo)")
    args = ap.parse_args()

    extra = []
    if args.tfs:
        extra += ["--tfs", args.tfs]
    if args.labels:
        extra += ["--labels", args.labels]
    if args.bars:
        extra += ["--bars", str(args.bars)]

    grupos = [g.strip() for g in args.grupos.split(",") if g.strip()]
    print(f"{'#'*70}\n# EDGE DISCOVERY COMPLETO — {datetime.now():%d/%m %H:%M}"
          f"\n# Grupos: {grupos} (um subprocesso por grupo)"
          f"\n# Requisito: MT5 da XP E da IC Markets abertas ao mesmo tempo\n{'#'*70}\n")

    resultados = {}
    for g in grupos:
        t0 = time.time()
        print(f"\n{'='*70}\n#  GRUPO {g.upper()}\n{'='*70}", flush=True)
        mod = "edge_discovery.run_discovery_v2" if args.v2 else "edge_discovery.run_discovery"
        ex = [e for e in extra if not (args.v2 and e in ("--labels",))]  # v2 não usa --labels
        if args.v2 and "--labels" in extra:
            i = extra.index("--labels"); ex = extra[:i] + extra[i+2:]
        r = subprocess.run(
            [sys.executable, "-m", mod, "--grupo", g] + ex,
            cwd=str(ROOT))
        resultados[g] = ("OK" if r.returncode == 0 else f"ERRO (código {r.returncode})",
                         f"{(time.time()-t0)/60:.0f} min")

    print(f"\n{'#'*70}\n# RESUMO\n{'#'*70}")
    for g, (st, dur) in resultados.items():
        print(f"  {g:<8} {st}  ({dur})")
    print("\n# Relatórios: logs/edge_discovery_<grupo>_*.md — envie os dois para análise")


if __name__ == "__main__":
    main()
