"""
rodar_backtest_setups_novos_v9_refine.py — Refino 🟡 v8 (GBP→EUR→BTC).

⚠️ MT5 IC aberta. Não altera motores.

Uso:
    python rodar_backtest_setups_novos_v9_refine.py
"""
import argparse
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bars", type=int, default=300000)
    ap.add_argument("--syms", default="GBPUSD,EURUSD,BTCUSD")
    args = ap.parse_args()

    sys.path.insert(0, str(ROOT))
    from backtest_setups_novos_v9_refine import STRATS

    print(f"{'#' * 70}\n# BACKTEST V9 REFINE — {datetime.now():%d/%m %H:%M}"
          f"\n# {len(STRATS)} setups · {args.syms}"
          f"\n# Foco: converter 🟡→🟢 (GBP INS primeiro)"
          f"\n# Motores live intactos\n{'#' * 70}\n", flush=True)

    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    t0 = time.time()
    r = subprocess.run(
        [sys.executable, "-u", str(ROOT / "backtest_setups_novos_v9_refine.py"),
         "--bars", str(args.bars), "--syms", args.syms],
        cwd=str(ROOT), env=env,
    )
    dur = (time.time() - t0) / 60
    st = "OK" if r.returncode == 0 else f"ERRO ({r.returncode})"
    print(f"\n{'#' * 70}\n# RESUMO: {st} ({dur:.1f} min)"
          f"\n# Log: logs/backtest_setups_novos_v9_refine_*.txt\n{'#' * 70}")


if __name__ == "__main__":
    main()
