"""
rodar_backtest_setups_novos_v2.py — Rodada 2 (variações) em UM comando.

⚠️ Duas MT5 abertas (XP + IC). Não altera motores :5000/:5001.

Uso:
    python rodar_backtest_setups_novos_v2.py
    python rodar_backtest_setups_novos_v2.py --grupos b3
    python rodar_backtest_setups_novos_v2.py --grupos crypto
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
    ap.add_argument("--grupos", default="b3,crypto")
    ap.add_argument("--bars", type=int, default=300000)
    args = ap.parse_args()

    grupos = [g.strip() for g in args.grupos.split(",") if g.strip()]
    print(f"{'#' * 70}\n# BACKTEST SETUPS NOVOS V2 — {datetime.now():%d/%m %H:%M}"
          f"\n# Grupos: {grupos}"
          f"\n# bars={args.bars}  |  motores live intactos\n{'#' * 70}\n")

    resultados = {}
    for g in grupos:
        t0 = time.time()
        print(f"\n{'█' * 70}\n█  GRUPO {g.upper()}\n{'█' * 70}", flush=True)
        r = subprocess.run(
            [sys.executable, str(ROOT / "backtest_setups_novos_v2.py"),
             "--grupo", g, "--bars", str(args.bars)],
            cwd=str(ROOT),
        )
        resultados[g] = ("OK" if r.returncode == 0 else f"ERRO ({r.returncode})",
                         f"{(time.time() - t0) / 60:.1f} min")

    print(f"\n{'#' * 70}\n# RESUMO\n{'#' * 70}")
    for g, (st, dur) in resultados.items():
        print(f"  {g:<8} {st}  ({dur})")
    print("\n# Logs: logs/backtest_setups_novos_v2_<grupo>_*.txt — envie para análise")


if __name__ == "__main__":
    main()
