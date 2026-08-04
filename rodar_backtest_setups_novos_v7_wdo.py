"""
rodar_backtest_setups_novos_v7_wdo.py — Rodada 7 WDO em UM comando.

⚠️ MT5 XP aberta (B3 · WDO$D). Não altera motores :5000/:5001.

Uso:
    python rodar_backtest_setups_novos_v7_wdo.py
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
    ap.add_argument("--bars", type=int, default=300000)
    args = ap.parse_args()

    sys.path.insert(0, str(ROOT))
    from backtest_setups_novos_v7_wdo import STRATS

    print(f"{'#' * 70}\n# BACKTEST SETUPS NOVOS V7 — WDO ONLY — {datetime.now():%d/%m %H:%M}"
          f"\n# {len(STRATS)} setups · bars={args.bars}"
          f"\n# Foco: mais GOs no mini dólar (hoje só WDO_NR5_H4)"
          f"\n# Motores live intactos\n{'#' * 70}\n", flush=True)

    t0 = time.time()
    r = subprocess.run(
        [sys.executable, str(ROOT / "backtest_setups_novos_v7_wdo.py"),
         "--bars", str(args.bars)],
        cwd=str(ROOT),
    )
    dur = (time.time() - t0) / 60
    st = "OK" if r.returncode == 0 else f"ERRO ({r.returncode})"
    print(f"\n{'#' * 70}\n# RESUMO: {st} ({dur:.1f} min)"
          f"\n# Log: logs/backtest_setups_novos_v7_wdo_*.txt"
          f"\n# Procure '# 🟢 GO encontrados:' no final\n{'#' * 70}")


if __name__ == "__main__":
    main()
