"""
rodar_backtest_noturno.py — Roda TODOS os backtests de uma vez (deixar rodando).

Executa, para WIN$D e WDO$D (contínuos, 6,6 anos):
  1. v6 / exits FIXED    — a produção EXATA do Monitor MT5, com custos reais
  2. v6 / exits MANAGED  — mesma produção, só trocando a saída (parcial 1R +
                           breakeven + trailing + time-stop)
  3. v7.1 / MANAGED      — motor novo (regime + ORB + fade)

Tudo é gravado em logs/backtest_noturno_<data>.txt (além do console) e os
trades detalhados em logs/backtest_pro_*.csv.

Uso:  python rodar_backtest_noturno.py
(Deixe o MT5 aberto. Pode minimizar tudo e ir dormir.)
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
RUNS = [
    ("v6", "fixed",   "PRODUÇÃO ATUAL (v6, saída fixa) + custos"),
    ("v6", "managed", "v6 com EXITS GERIDOS (parcial+BE+trailing+time-stop)"),
    ("v7", "managed", "MOTOR v7.1 (regime + ORB + fade)"),
]


class Tee:
    """Duplica stdout para um arquivo de log."""
    def __init__(self, path):
        self.file = open(path, "a", encoding="utf-8")
        self.stdout = sys.stdout

    def write(self, data):
        self.stdout.write(data)
        self.file.write(data)
        self.file.flush()

    def flush(self):
        self.stdout.flush()
        self.file.flush()


def main():
    os.makedirs("logs", exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    logpath = f"logs/backtest_noturno_{stamp}.txt"
    sys.stdout = Tee(logpath)

    print(f"\n{'#'*70}")
    print(f"# BACKTEST NOTURNO — início {datetime.now():%d/%m/%Y %H:%M:%S}")
    print(f"# Log: {logpath}")
    print(f"{'#'*70}\n")

    t0 = time.time()
    data = {}
    for sym in SYMBOLS:
        try:
            df = fetch_mt5(sym, "15", 200000)
            if df is None or len(df) < 1000:
                print(f"!! {sym}: sem dados suficientes — pulando")
                continue
            print(f"{sym}: {len(df)} candles ({df.index[0]} → {df.index[-1]})")
            data[sym] = df
        except Exception:
            print(f"!! {sym}: erro ao buscar candles:\n{traceback.format_exc()}")

    for sym, df in data.items():
        for engine, exits, label in RUNS:
            print(f"\n>>> [{datetime.now():%H:%M:%S}] {sym} — {label}")
            try:
                t1 = time.time()
                trades = simulate(df, sym, engine=engine, exits=exits,
                                  use_ticks=False, mt5_symbol=sym)
                report(trades, sym, f"{engine}/{exits}")
                out = f"logs/backtest_pro_{sym.replace('$','_')}_{engine}_{exits}.csv"
                trades.to_csv(out, index=False)
                print(f"  detalhe: {out}  ({time.time()-t1:.0f}s)")
            except Exception:
                print(f"!! ERRO em {sym} {engine}/{exits} — seguindo para o próximo:")
                print(traceback.format_exc())

    print(f"\n{'#'*70}")
    print(f"# FIM — {datetime.now():%d/%m/%Y %H:%M:%S} (total {(time.time()-t0)/60:.0f} min)")
    print(f"# Amanhã: envie o arquivo {logpath} para o Claude analisar.")
    print(f"{'#'*70}")


if __name__ == "__main__":
    main()
