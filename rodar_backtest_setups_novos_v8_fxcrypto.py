"""
rodar_backtest_setups_novos_v8_fxcrypto.py — Rodada 8 ETH/BTC/EUR/GBP.

⚠️ MT5 IC (crypto) aberta. Não altera motores :5000/:5001.

Uso:
    python rodar_backtest_setups_novos_v8_fxcrypto.py
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
    ap.add_argument("--syms", default="ETHUSD,BTCUSD,EURUSD,GBPUSD")
    args = ap.parse_args()

    sys.path.insert(0, str(ROOT))
    from backtest_setups_novos_v8_fxcrypto import STRATS

    print(f"{'#' * 70}\n# BACKTEST SETUPS NOVOS V8 — FX/CRYPTO — {datetime.now():%d/%m %H:%M}"
          f"\n# {len(STRATS)} setups · {args.syms} · bars={args.bars}"
          f"\n# Foco: ETH (+GOs) · BTC/EUR/GBP (primeiro path GO)"
          f"\n# Motores live intactos\n{'#' * 70}\n", flush=True)

    t0 = time.time()
    r = subprocess.run(
        [sys.executable, "-u", str(ROOT / "backtest_setups_novos_v8_fxcrypto.py"),
         "--bars", str(args.bars), "--syms", args.syms],
        cwd=str(ROOT),
        env={**dict(**{k: v for k, v in __import__("os").environ.items()}),
             "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
    )
    dur = (time.time() - t0) / 60
    st = "OK" if r.returncode == 0 else f"ERRO ({r.returncode})"
    print(f"\n{'#' * 70}\n# RESUMO: {st} ({dur:.1f} min)"
          f"\n# Log: logs/backtest_setups_novos_v8_fxcrypto_*.txt\n{'#' * 70}")


if __name__ == "__main__":
    main()
