"""
market_regime.py — Camada 1 do Motor v7: classificação do DIA (WIN/WDO).

Classifica cada momento do pregão em um regime operacional usando apenas
dados disponíveis ATÉ aquele momento (sem look-ahead — backtestável):

  FORMING     — antes de o Opening Range (1ª hora) completar: não opera
  TREND_UP    — dia de tendência compradora: só compras, pullback/rompimento
  TREND_DOWN  — dia de tendência vendedora: só vendas
  RANGE       — dia de range: fade nos extremos (bandas 2σ / máx-mín)
  NO_TRADE    — volatilidade anômala sem direção, ou fora de janela

Referências calculadas por dia: abertura, máx/mín de ontem, fechamento de
ontem, Opening Range (09:00-10:00), ATR diário (14 dias).

Módulo puro (pandas) — usado pelo technical_analysis_v7, backtest_pro e
shadow_monitor. NÃO altera nenhum módulo existente.
"""
import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Janelas BRT (horário do candle = horário do servidor da corretora B3)
OR_START_MIN   = 9 * 60          # 09:00
OR_END_MIN     = 10 * 60         # 10:00  (Opening Range = 1ª hora)
JANELA_A       = (9 * 60 + 30, 11 * 60 + 30)   # 09:30-11:30
JANELA_B       = (14 * 60,      16 * 60 + 30)  # 14:00-16:30
LAST_ENTRY_MIN = 16 * 60 + 30    # sem entradas novas após 16:30

GAP_TREND_RATIO   = 0.30   # |gap| >= 30% do ATR diário sugere tendência
EXPANSION_RATIO   = 1.00   # range do dia >= 1.0x ATR_d = dia expandido
CHAOS_RATIO       = 2.20   # range do dia >= 2.2x ATR_d sem direção = NO_TRADE


def _minute_of_day(ts) -> int:
    return ts.hour * 60 + ts.minute


def build_daily_refs(df15: pd.DataFrame) -> pd.DataFrame:
    """
    A partir do M15 completo, monta a tabela de referências POR DIA.
    Cada linha do dia D usa apenas dados de dias < D (mais o próprio OR,
    que é usado somente após as 10:00 do dia D).

    Retorna DataFrame indexado por date com colunas:
      y_high, y_low, y_close, atr_d, open_d, or_high, or_low
    """
    df = df15.copy()
    dates = pd.Series(df.index.date, index=df.index)

    day_high  = df["High"].groupby(dates).max()
    day_low   = df["Low"].groupby(dates).min()
    day_close = df["Close"].groupby(dates).last()
    day_open  = df["Open"].groupby(dates).first()

    day_range = day_high - day_low
    atr_d     = day_range.rolling(14, min_periods=5).mean().shift(1)  # ATR de ontem p/ trás

    # Opening Range: 09:00-10:00 de cada dia
    mins   = pd.Series([_minute_of_day(t) for t in df.index], index=df.index)
    inor   = (mins >= OR_START_MIN) & (mins < OR_END_MIN)
    orh    = df.loc[inor, "High"].groupby(dates[inor]).max()
    orl    = df.loc[inor, "Low"].groupby(dates[inor]).min()

    refs = pd.DataFrame({
        "open_d":  day_open,
        "y_high":  day_high.shift(1),
        "y_low":   day_low.shift(1),
        "y_close": day_close.shift(1),
        "atr_d":   atr_d,
        "or_high": orh,
        "or_low":  orl,
    })
    return refs


def classify(ts, price: float, day_high: float, day_low: float,
             refs_row: "pd.Series | dict") -> dict:
    """
    Classifica o regime NO instante ts (candle atual), usando refs do dia.

    Parâmetros
    ----------
    ts        : timestamp do candle atual
    price     : close atual
    day_high/day_low : máx/mín do dia ATÉ este candle (sem look-ahead)
    refs_row  : linha de build_daily_refs para a data de ts

    Retorna {regime, janela, dir_score, gap_ratio, expansion, refs...}
    """
    g = lambda k: refs_row.get(k) if isinstance(refs_row, dict) else refs_row.get(k, np.nan)
    open_d, y_close = g("open_d"), g("y_close")
    y_high, y_low   = g("y_high"), g("y_low")
    atr_d           = g("atr_d")
    or_high, or_low = g("or_high"), g("or_low")

    minute = _minute_of_day(ts)
    if JANELA_A[0] <= minute < JANELA_A[1]:
        janela = "A"
    elif JANELA_B[0] <= minute < JANELA_B[1]:
        janela = "B"
    else:
        janela = "FECHADA"

    out = {"regime": "NO_TRADE", "janela": janela, "dir_score": 0,
           "gap_ratio": None, "expansion": None,
           "open_d": open_d, "y_high": y_high, "y_low": y_low,
           "y_close": y_close, "atr_d": atr_d,
           "or_high": or_high, "or_low": or_low}

    if any(pd.isna(v) for v in (open_d, y_close, atr_d)) or not atr_d:
        return out

    gap        = open_d - y_close
    gap_ratio  = gap / atr_d
    expansion  = (day_high - day_low) / atr_d if day_high and day_low else 0.0
    out["gap_ratio"], out["expansion"] = round(gap_ratio, 2), round(expansion, 2)

    # Antes do OR completo: dia ainda se formando
    if minute < OR_END_MIN or pd.isna(or_high) or pd.isna(or_low):
        out["regime"] = "FORMING"
        return out

    # Placar direcional do dia (cada item = evidência independente de direção)
    up = down = 0
    if gap_ratio >= GAP_TREND_RATIO:   up += 1
    if gap_ratio <= -GAP_TREND_RATIO:  down += 1
    if price > or_high:                up += 1
    if price < or_low:                 down += 1
    if not pd.isna(y_high) and price > y_high: up += 1
    if not pd.isna(y_low)  and price < y_low:  down += 1
    if price > open_d:                 up += 1
    if price < open_d:                 down += 1
    dir_score = up - down
    out["dir_score"] = dir_score

    # Caos: range gigante sem direção definida → não opera
    if expansion >= CHAOS_RATIO and abs(dir_score) < 2:
        out["regime"] = "NO_TRADE"
        return out

    if dir_score >= 2 and expansion >= 0.5:
        out["regime"] = "TREND_UP"
    elif dir_score <= -2 and expansion >= 0.5:
        out["regime"] = "TREND_DOWN"
    else:
        out["regime"] = "RANGE"
    return out
