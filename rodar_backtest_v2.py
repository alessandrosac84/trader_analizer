"""
rodar_backtest_v2.py — 2ª rodada noturna, com o SIMULADOR CORRIGIDO.

Correções aplicadas ao backtest_pro (FIX v2):
  - Zeragem EOD no modo fixed (a produção fecha no fim do pregão — sem isso a
    posição atravessava dias/gaps e inflava o resultado)
  - Entrada só no MESMO dia do sinal (sinal de 16h45 virava entrada na abertura
    seguinte com stop/alvo velhos → "R" explosivo falso nos gaps)
  - Descarte de gap-through do TP e risco mínimo de 0.3×ATR
  - FADE_2SIGMA desligado no v7 (negativo nos 2 ativos em 6,6 anos)

O que esta rodada responde:
  1. Qual é a expectância REAL da produção (v6/fixed) sem os artefatos
  2. Se restringir horário (só manhã) melhora consistência — candidato a
     mudança mínima de produção
  3. Se o ORB (v7.2) sobrevive à correção

Uso:  python rodar_backtest_v2.py   (MT5 aberto; ~1,5-2h; pode deixar rodando)
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

H_MANHA_TODA  = (9 * 60 + 15, 11 * 60 + 30)   # 09:15-11:30
H_SO_ABERTURA = (9 * 60 + 15, 10 * 60 + 30)   # 09:15-10:30

RUNS = [
    ("v6", "fixed", None,          "v6/fixed CORRIGIDO — dia inteiro (baseline real da produção)"),
    ("v6", "fixed", H_MANHA_TODA,  "v6/fixed CORRIGIDO — só 09:15-11:30"),
    ("v6", "fixed", H_SO_ABERTURA, "v6/fixed CORRIGIDO — só 09:15-10:30"),
    ("v7", "managed", None,        "v7.2 (ORB apenas; FADE off) CORRIGIDO"),
]


class Tee:
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
    logpath = f"logs/backtest_v2_{stamp}.txt"
    sys.stdout = Tee(logpath)

    print(f"\n{'#'*70}")
    print(f"# BACKTEST v2 (simulador corrigido) — início {datetime.now():%d/%m/%Y %H:%M:%S}")
    print(f"# Log: {logpath}")
    print(f"{'#'*70}\n")

    t0 = time.time()
    data = {}
    for sym in SYMBOLS:
        try:
            df = fetch_mt5(sym, "15", 200000)
            if df is None or len(df) < 1000:
                print(f"!! {sym}: sem dados — pulando")
                continue
            print(f"{sym}: {len(df)} candles ({df.index[0]} → {df.index[-1]})")
            data[sym] = df
        except Exception:
            print(f"!! {sym}: erro:\n{traceback.format_exc()}")

    for sym, df in data.items():
        for engine, exits, hours, label in RUNS:
            print(f"\n>>> [{datetime.now():%H:%M:%S}] {sym} — {label}")
            try:
                t1 = time.time()
                trades = simulate(df, sym, engine=engine, exits=exits,
                                  use_ticks=False, mt5_symbol=sym, hours=hours)
                htag = "" if not hours else f"_h{hours[0]//60}{hours[0]%60:02d}-{hours[1]//60}{hours[1]%60:02d}"
                report(trades, sym, f"{engine}/{exits}{htag}")
                out = f"logs/backtest_v2_{sym.replace('$','_')}_{engine}{htag}.csv"
                trades.to_csv(out, index=False)
                print(f"  detalhe: {out}  ({time.time()-t1:.0f}s)")
            except Exception:
                print(f"!! ERRO em {sym} {engine}{hours} — seguindo:")
                print(traceback.format_exc())

    print(f"\n{'#'*70}")
    print(f"# FIM — {datetime.now():%d/%m/%Y %H:%M:%S} (total {(time.time()-t0)/60:.0f} min)")
    print(f"# Envie o arquivo {logpath} para análise.")
    print(f"{'#'*70}")


if __name__ == "__main__":
    main()
