"""
rodar_backtest_setups_novos.py — Backtest dos setups NOVOS em UM comando.

Roda B3 e Crypto em sequência, cada um num subprocesso próprio (lib
MetaTrader5 = 1 conexão/processo). Não mexe nos motores ao vivo das portas
5000/5001 — só lê histórico do terminal MT5.

⚠️ Requisito: as DUAS MT5 abertas (XP + IC Markets), como nos outros runners.

Uso:
    python rodar_backtest_setups_novos.py
    python rodar_backtest_setups_novos.py --grupos b3
    python rodar_backtest_setups_novos.py --grupos crypto
    python rodar_backtest_setups_novos.py --bars 300000 --tf 15

Saída: logs/backtest_setups_novos_<grupo>_<stamp>.txt + CSVs em logs/novos_*.csv
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
    ap.add_argument("--grupos", default="b3,crypto",
                    help="b3,crypto (default) ou só um deles")
    ap.add_argument("--tf", type=int, default=15)
    ap.add_argument("--bars", type=int, default=300000,
                    help="teto de candles; o MT5 devolve o máximo disponível")
    args = ap.parse_args()

    grupos = [g.strip() for g in args.grupos.split(",") if g.strip()]
    print(f"{'#' * 70}\n# BACKTEST SETUPS NOVOS — {datetime.now():%d/%m %H:%M}"
          f"\n# Grupos: {grupos} (subprocesso por grupo)"
          f"\n# TF=M{args.tf}  bars={args.bars}"
          f"\n# Requisito: MT5 XP + IC Markets abertas"
          f"\n# App :5000/:5001 pode continuar rodando (não altera motores)\n{'#' * 70}\n")

    resultados = {}
    for g in grupos:
        t0 = time.time()
        print(f"\n{'█' * 70}\n█  GRUPO {g.upper()}\n{'█' * 70}", flush=True)
        r = subprocess.run(
            [sys.executable, str(ROOT / "backtest_setups_novos.py"),
             "--grupo", g, "--tf", str(args.tf), "--bars", str(args.bars)],
            cwd=str(ROOT),
        )
        resultados[g] = ("OK" if r.returncode == 0 else f"ERRO (código {r.returncode})",
                         f"{(time.time() - t0) / 60:.1f} min")

    print(f"\n{'#' * 70}\n# RESUMO\n{'#' * 70}")
    for g, (st, dur) in resultados.items():
        print(f"  {g:<8} {st}  ({dur})")
    print("\n# Relatórios: logs/backtest_setups_novos_<grupo>_*.txt"
          "\n# Envie os dois arquivos para análise do critério GO")


if __name__ == "__main__":
    main()
