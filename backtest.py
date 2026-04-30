"""
backtest.py — Backtesting do motor de análise técnica (Trade AI v3)

Metodologia:
  1. Busca dados históricos via Yahoo Finance (mesmo proxy do monitor)
  2. Para cada candle, simula o sinal que o motor geraria naquele momento
     usando apenas os candles ANTERIORES (sem look-ahead bias)
  3. Verifica se o preço atingiu TP1 antes de bater o stop nas N velas seguintes
  4. Calcula win rate, profit factor, drawdown e distribuição de scores

Uso:
  python backtest.py
  python backtest.py --symbol "^BVSP" --interval 15m --period 60d
  python backtest.py --symbol "PETR4.SA" --interval 5m --period 30d --forward 10
"""

import argparse
import sys
from pathlib import Path

# Garante que o diretório raiz do projeto está no path
sys.path.insert(0, str(Path(__file__).parent))

try:
    import pandas as pd
    import numpy as np
except ImportError:
    print("❌  pandas/numpy não instalados. Execute: pip install pandas numpy")
    sys.exit(1)

try:
    import yfinance as yf
except ImportError:
    print("❌  yfinance não instalado. Execute: pip install yfinance")
    sys.exit(1)

from services.technical_analysis import generate_signal


# ---------------------------------------------------------------------------
# Configuração padrão
# ---------------------------------------------------------------------------

DEFAULT_SYMBOL   = "^BVSP"
DEFAULT_INTERVAL = "15m"
DEFAULT_PERIOD   = "60d"
DEFAULT_FORWARD  = 8      # quantas velas à frente verificar se bateu TP ou stop


# ---------------------------------------------------------------------------
# Fetch de dados
# ---------------------------------------------------------------------------

def fetch_data(symbol: str, period: str, interval: str) -> pd.DataFrame | None:
    print(f"📥  Buscando dados: {symbol}  period={period}  interval={interval}")
    try:
        df = yf.Ticker(symbol).history(period=period, interval=interval, auto_adjust=True)
        if df is None or df.empty:
            print(f"❌  Sem dados para {symbol}")
            return None
        print(f"✅  {len(df)} candles carregados  ({df.index[0]} → {df.index[-1]})")
        return df
    except Exception as e:
        print(f"❌  Erro ao buscar dados: {e}")
        return None


# ---------------------------------------------------------------------------
# Backtesting principal
# ---------------------------------------------------------------------------

def run_backtest(
    symbol: str   = DEFAULT_SYMBOL,
    interval: str = DEFAULT_INTERVAL,
    period: str   = DEFAULT_PERIOD,
    forward: int  = DEFAULT_FORWARD,
    min_score: int = 4,          # score mínimo absoluto para considerar o sinal
) -> dict:
    df = fetch_data(symbol, period, interval)
    if df is None:
        return {}

    # Busca também HTF (1h) para confirmação multi-timeframe
    htf_df = None
    if interval not in ("60m", "1h", "1d", "1wk"):
        htf_df = fetch_data(symbol, "90d", "1h")

    results = []
    min_lookback = 52   # mínimo de candles para calcular todos os indicadores

    print(f"\n🔄  Simulando {len(df) - min_lookback - forward} pontos de entrada...")

    for i in range(min_lookback, len(df) - forward):
        window    = df.iloc[:i]          # apenas histórico até o candle i (sem look-ahead)
        future    = df.iloc[i: i + forward]

        # Prepara HTF: só usa candles anteriores ao timestamp do candle i
        htf_window = None
        if htf_df is not None and len(htf_df) >= 26:
            ts_i = df.index[i]
            htf_window = htf_df[htf_df.index <= ts_i]
            if len(htf_window) < 26:
                htf_window = None

        signal = generate_signal(window, htf_df=htf_window)
        if signal is None:
            continue

        acao  = signal["acao"]
        score = signal.get("score", 0)

        # Só avalia sinais COMPRA ou VENDA com score suficiente
        if acao not in ("COMPRA", "VENDA"):
            continue
        if abs(score) < min_score:
            continue

        entrada = signal.get("entrada")
        stop    = signal.get("stop")
        tp1     = signal.get("tp1")

        if entrada is None or stop is None or tp1 is None:
            continue

        # Avalia resultado nas próximas N velas
        outcome = _evaluate_outcome(acao, entrada, stop, tp1, future)

        results.append({
            "timestamp": df.index[i],
            "acao":      acao,
            "score":     score,
            "entrada":   entrada,
            "stop":      stop,
            "tp1":       tp1,
            "outcome":   outcome,       # "WIN", "LOSS", "OPEN"
            "adx_filtered": signal.get("adx_filtered", False),
            "htf_trend":    signal.get("htf_trend"),
            "forca":        signal.get("forca"),
        })

    return _calculate_stats(results, symbol, interval, period, forward)


def _evaluate_outcome(
    acao: str,
    entrada: float,
    stop: float,
    tp1: float,
    future: pd.DataFrame,
) -> str:
    """
    Percorre as velas futuras e verifica qual foi atingido primeiro: TP1 ou stop.
    Retorna 'WIN', 'LOSS' ou 'OPEN' (nenhum dos dois foi atingido no período).
    """
    for _, row in future.iterrows():
        high = float(row["High"])
        low  = float(row["Low"])

        if acao == "COMPRA":
            if low <= stop:   return "LOSS"
            if high >= tp1:   return "WIN"
        else:  # VENDA
            if high >= stop:  return "LOSS"
            if low <= tp1:    return "WIN"

    return "OPEN"


def _calculate_stats(results: list, symbol: str, interval: str, period: str, forward: int) -> dict:
    if not results:
        print("⚠️  Nenhum sinal gerado com os parâmetros configurados.")
        return {}

    df_r = pd.DataFrame(results)

    total    = len(df_r)
    wins     = (df_r["outcome"] == "WIN").sum()
    losses   = (df_r["outcome"] == "LOSS").sum()
    open_    = (df_r["outcome"] == "OPEN").sum()
    decided  = wins + losses                   # excluindo OPEN
    win_rate = (wins / decided * 100) if decided > 0 else 0

    # Separado por tipo de sinal
    compras = df_r[df_r["acao"] == "COMPRA"]
    vendas  = df_r[df_r["acao"] == "VENDA"]

    def wr(subset):
        d = (subset["outcome"] == "WIN").sum() + (subset["outcome"] == "LOSS").sum()
        return (subset["outcome"] == "WIN").sum() / d * 100 if d > 0 else 0

    # Por faixa de score
    score_buckets = {}
    for s in [4, 5, 6, 7, 8]:
        bucket = df_r[df_r["score"].abs() >= s]
        if len(bucket) > 0:
            d = (bucket["outcome"] == "WIN").sum() + (bucket["outcome"] == "LOSS").sum()
            wr_s = (bucket["outcome"] == "WIN").sum() / d * 100 if d > 0 else 0
            score_buckets[f"|score| ≥ {s}"] = {
                "total": len(bucket),
                "win_rate": round(wr_s, 1),
            }

    # Por força do sinal (ADX / HTF)
    htf_confirmed = df_r[df_r["htf_trend"].notna() & (
        ((df_r["acao"] == "COMPRA") & (df_r["htf_trend"] == "alta")) |
        ((df_r["acao"] == "VENDA")  & (df_r["htf_trend"] == "baixa"))
    )]

    stats = {
        "symbol":        symbol,
        "interval":      interval,
        "period":        period,
        "forward_bars":  forward,
        "total_signals": total,
        "wins":          int(wins),
        "losses":        int(losses),
        "open":          int(open_),
        "decided":       int(decided),
        "win_rate":      round(win_rate, 1),
        "win_rate_compra": round(wr(compras), 1),
        "win_rate_venda":  round(wr(vendas), 1),
        "total_compra":  len(compras),
        "total_venda":   len(vendas),
        "score_buckets": score_buckets,
        "htf_confirmed_total":    len(htf_confirmed),
        "htf_confirmed_win_rate": round(wr(htf_confirmed), 1) if len(htf_confirmed) > 0 else None,
    }

    _print_report(stats)
    return stats


def _print_report(s: dict):
    sep = "─" * 58
    print(f"\n{'═'*58}")
    print(f"  RESULTADO DO BACKTESTING — Trade AI Motor v3")
    print(f"{'═'*58}")
    print(f"  Instrumento : {s['symbol']}  ({s['interval']}  ·  {s['period']})")
    print(f"  Janela futura: {s['forward_bars']} candles para confirmar TP/Stop")
    print(sep)
    print(f"  Sinais gerados  : {s['total_signals']:>5}  (COMPRA: {s['total_compra']}  VENDA: {s['total_venda']})")
    print(f"  Decididos       : {s['decided']:>5}  (WIN: {s['wins']}  LOSS: {s['losses']}  OPEN: {s['open']})")
    print(sep)
    print(f"  📊 WIN RATE GERAL   : {s['win_rate']:>5.1f}%")
    print(f"     └─ COMPRA        : {s['win_rate_compra']:>5.1f}%  ({s['total_compra']} sinais)")
    print(f"     └─ VENDA         : {s['win_rate_venda']:>5.1f}%  ({s['total_venda']} sinais)")
    print(sep)
    print("  Win rate por faixa de score (|score| ≥ N):")
    for k, v in s["score_buckets"].items():
        bar = "█" * int(v["win_rate"] / 5)
        print(f"     {k:<14}: {v['win_rate']:>5.1f}%  ({v['total']:>3} sinais)  {bar}")
    print(sep)
    if s["htf_confirmed_total"] > 0:
        print(f"  ✅ Multi-TF confirmado : {s['htf_confirmed_total']} sinais → win rate {s['htf_confirmed_win_rate']}%")
    print(f"{'═'*58}\n")

    # Interpretação
    wr = s["win_rate"]
    if wr >= 60:
        grade = "🟢 BOM  — motor assertivo para as condições testadas"
    elif wr >= 50:
        grade = "🟡 REGULAR — melhor que aleatório, mas precisa afinar"
    else:
        grade = "🔴 FRACO — abaixo de 50%, revisar parâmetros ou dados"

    print(f"  Avaliação: {grade}")
    print(f"\n  ⚠️  Lembrete: dados do Yahoo Finance têm delay de 15 min")
    print(f"      e usam proxy (^BVSP / BRL=X) em vez dos futuros reais.")
    print(f"      Win rate real com dados live tende a ser diferente.\n")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backtesting do motor Trade AI")
    parser.add_argument("--symbol",   default=DEFAULT_SYMBOL,   help="Símbolo Yahoo Finance (ex: ^BVSP, PETR4.SA)")
    parser.add_argument("--interval", default=DEFAULT_INTERVAL, help="Intervalo (1m,5m,15m,30m,60m,1d)")
    parser.add_argument("--period",   default=DEFAULT_PERIOD,   help="Período histórico (7d,30d,60d,6mo,1y)")
    parser.add_argument("--forward",  default=DEFAULT_FORWARD,  type=int, help="Velas futuras para checar TP/stop")
    parser.add_argument("--min-score",default=4,                type=int, help="Score mínimo para considerar sinal")
    parser.add_argument("--all",      action="store_true",       help="Roda em todos os instrumentos principais")
    args = parser.parse_args()

    if args.all:
        instruments = [
            ("^BVSP",    "15m", "60d"),
            ("PETR4.SA", "15m", "60d"),
            ("VALE3.SA", "15m", "60d"),
            ("ITUB4.SA", "15m", "60d"),
            ("BRL=X",    "15m", "60d"),
        ]
        all_stats = []
        for sym, ivl, per in instruments:
            st = run_backtest(sym, ivl, per, args.forward, args.min_score)
            if st:
                all_stats.append(st)

        # Resumo consolidado
        if all_stats:
            print("\n" + "═"*58)
            print("  RESUMO CONSOLIDADO")
            print("═"*58)
            for st in all_stats:
                print(f"  {st['symbol']:<12} {st['interval']}  →  "
                      f"win rate {st['win_rate']:>5.1f}%  "
                      f"({st['decided']} sinais decididos)")
            print("═"*58 + "\n")
    else:
        run_backtest(args.symbol, args.interval, args.period, args.forward, args.min_score)
