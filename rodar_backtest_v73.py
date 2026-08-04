"""
rodar_backtest_v73.py — Valida o motor v7.3 (portfólio de setups estruturais).

v7.3 = filtros de regime/confluência do v7 + os vencedores do lab:
  WIN$D: ORB (mercado) + GAP_FADE
  WDO$D: ORB_RETEST (entrada LIMITE no reteste — corta o custo) + GAP_FADE

Grava tudo em logs/backtest_v73_<data>.txt (UTF-8, sem problema de encoding).

Uso:  python rodar_backtest_v73.py     (MT5 aberto; ~30 min)
"""
import os
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from backtest_pro import fetch_mt5, simulate, report

SYMBOLS = ["WIN$D", "WDO$D"]


class Tee:
    def __init__(self, path):
        self.file = open(path, "a", encoding="utf-8")
        self.stdout = sys.stdout

    def write(self, data):
        try:
            self.stdout.write(data)
        except UnicodeEncodeError:
            self.stdout.write(data.encode("ascii", "replace").decode())
        self.file.write(data)
        self.file.flush()

    def flush(self):
        self.stdout.flush()
        self.file.flush()


def main():
    os.makedirs("logs", exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    logpath = f"logs/backtest_v73_{stamp}.txt"
    sys.stdout = Tee(logpath)

    print(f"\n{'#'*70}")
    print(f"# BACKTEST v7.3 (portfolio estrutural) — {datetime.now():%d/%m/%Y %H:%M:%S}")
    print(f"# Log: {logpath}")
    print(f"{'#'*70}\n")

    t0 = time.time()
    for sym in SYMBOLS:
        try:
            df = fetch_mt5(sym, "15", 200000)
            if df is None or len(df) < 1000:
                print(f"!! {sym}: sem dados — pulando")
                continue
            print(f"{sym}: {len(df)} candles ({df.index[0]} -> {df.index[-1]})")
            t1 = time.time()
            trades = simulate(df, sym, engine="v7", exits="managed",
                              use_ticks=False, mt5_symbol=sym)
            report(trades, sym, "v7.3/managed")
            out = f"logs/backtest_v73_{sym.replace('$','_')}.csv"
            trades.to_csv(out, index=False)
            print(f"  detalhe: {out}  ({time.time()-t1:.0f}s)")
        except Exception:
            print(f"!! ERRO em {sym}:")
            print(traceback.format_exc())

    print(f"\n{'#'*70}")
    print(f"# FIM — {(time.time()-t0)/60:.0f} min. Envie {logpath} para análise.")
    print(f"{'#'*70}")


if __name__ == "__main__":
    main()
