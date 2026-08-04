"""
backtest_setups_novos.py — SETUPS NOVOS (rodada Auto, 23/07/2026).

Régua IDÊNTICA aos backtests anteriores (kimi2 / kimi_real):
  - Dados REAIS do MT5 (máximo de histórico disponível)
  - CUSTOS reais (taxas B3 + spread CFD)
  - Avaliação barra a barra, sem look-ahead
  - Walk-forward: IN-SAMPLE 70% × OUT-OF-SAMPLE 30%
  - Consistência bimestral + critério GO

⚠️ NÃO altera motores ao vivo. Só pesquisa — se algum setup passar GO,
   aí sim discutimos implementação.

Setups B3 (WIN/WDO):
  FAILED_GAP_FADE_ORB · ORB_VOLUME_CONFIRM · MIDDAY_MEAN_REVERT
  FIRST_PULLBACK_ORB · OVERNIGHT_INVENTORY · OPEN_DRIVE_FAILURE
  STOP_RUN_THEN_GO · ATR_EXPANSION_CONT · INSIDE_DAY_BREAK
  POWER_HOUR_B3 · WDO_CORREL_FADE

Setups Crypto (XAU/ETH):
  NY_HANDOFF · OF_SESSION_FILTER · ASIA_COMPRESSION_BREAK
  DELTA_DIVERGENCE_FADE · VALUE_AREA_REJECT · ROUND_NUMBER_MAGNET
  POWER_HOUR_NY · ATR_EXPANSION_XAU · ETH_BTC_SPREAD · OF_SESSION_ETH

Uso (um grupo por vez — lib MT5 é singleton):
  python backtest_setups_novos.py --grupo b3
  python backtest_setups_novos.py --grupo crypto

Ou o runner único (duas MT5 abertas):
  python rodar_backtest_setups_novos.py
"""
import argparse
import os
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

import numpy as np
import pandas as pd

from backtest_kimi_real import fetch as _b3_fetch, add_indicators, asset_key, roundtrip_cost_pts

# ── Sessões (hora do servidor IC Markets p/ crypto; B3 = hora local) ────────
SESS_ASIA         = (1, 9)       # range asiático
SESS_LONDON_RANGE = (9, 15)      # range Londres (p/ NY handoff)
SESS_NY           = (15, 18)     # handoff Londres→NY
SESS_OF_AM        = (8, 11)      # ORDER_FLOW filtrado manhã
SESS_OF_PM        = (14, 17)     # ORDER_FLOW filtrado tarde
SESS_POWER_NY     = (20, 22)     # power hour NY (~fechamento US no servidor)
WIN_MIDDAY        = (12.0, 14.0)
WIN_POWER         = (16.0, 17.25)
WIN_ORB_BUILD     = (10.0, 10.75)  # 10:00–10:45
WIN_ORB_TRADE     = (10.75, 12.5)  # após range formado
WIN_OPEN_DRIVE    = (9.0, 9.5)     # 09:00–09:30
OOS_FRAC          = 0.30
_WIN_TICK         = 5.0


# ════════════════════════════════════════════════════════════════════════════
# INDICADORES EXTRA
# ════════════════════════════════════════════════════════════════════════════

def add_extra(df, symbol, ctx=None):
    """ctx: dict opcional com dfs auxiliares {'WIN': df_win, 'BTCUSD': df_btc}."""
    h, l, c, o, v = df.High, df.Low, df.Close, df.Open, df.Volume
    hh = df.index.hour + df.index.minute / 60.0
    df["hh"] = hh
    df["vol8"] = v.rolling(8).mean()
    df["vol20b"] = v.rolling(20).mean()

    # Bollinger width + squeeze (percentil 10)
    bbw = (df["bb_up"] - df["bb_dn"]) / df["bb_mid"].replace(0, np.nan)
    df["bbw"] = bbw
    df["bbw_q10"] = bbw.rolling(100).quantile(0.10)
    df["atr_ma"] = df["atr"].rolling(440).mean()

    day = df["day"]
    df["day_open"] = df.groupby(day)["Open"].transform("first")
    df["day_hi"] = df.groupby(day)["High"].cummax()
    df["day_lo"] = df.groupby(day)["Low"].cummin()
    df["day_hi_prev"] = df.groupby(day)["High"].cummax().groupby(day).shift(1)
    df["day_lo_prev"] = df.groupby(day)["Low"].cummin().groupby(day).shift(1)

    day_close = df.groupby(day)["Close"].last()
    prev_close_map = day_close.shift(1)
    df["prev_close"] = day.map(prev_close_map)
    df["gap_pct"] = (df["day_open"] - df["prev_close"]) / df["prev_close"].replace(0, np.nan) * 100

    # Extremos dos últimos 3 pregões (overnight inventory)
    day_hi_map = df.groupby(day)["High"].max()
    day_lo_map = df.groupby(day)["Low"].min()
    hi3 = day_hi_map.rolling(3).max().shift(1)
    lo3 = day_lo_map.rolling(3).min().shift(1)
    df["hi3"] = day.map(hi3)
    df["lo3"] = day.map(lo3)

    # Inside day anterior
    prev_hi = day_hi_map.shift(1)
    prev_lo = day_lo_map.shift(1)
    prev2_hi = day_hi_map.shift(2)
    prev2_lo = day_lo_map.shift(2)
    inside = (prev_hi <= prev2_hi) & (prev_lo >= prev2_lo)
    df["prev_inside"] = day.map(inside.astype(float))
    df["prev_day_hi"] = day.map(prev_hi)
    df["prev_day_lo"] = day.map(prev_lo)

    # ORB range 10:00–10:45
    orb_mask = (hh >= WIN_ORB_BUILD[0]) & (hh <= WIN_ORB_BUILD[1])
    df["orb_hi"] = df.High.where(orb_mask).groupby(day).transform("max")
    df["orb_lo"] = df.Low.where(orb_mask).groupby(day).transform("min")
    df["orb_vol"] = df.Volume.where(orb_mask).groupby(day).transform("mean")

    # Fade do gap já falhou hoje? (preço do lado do gap e longe do PDC)
    # gap up: fade falha se Low nunca tocou prev_close e Close > day_open
    touched_pdc = ((df["Low"] <= df["prev_close"]) | (df["High"] >= df["prev_close"])).astype(int)
    df["touched_pdc"] = touched_pdc.groupby(day).cummax()

    # Open drive: extremos 09:00–09:30
    od_mask = (hh >= WIN_OPEN_DRIVE[0]) & (hh < WIN_OPEN_DRIVE[1])
    df["od_hi"] = df.High.where(od_mask).groupby(day).transform("max")
    df["od_lo"] = df.Low.where(od_mask).groupby(day).transform("min")
    df["od_first_dir"] = np.nan
    # direção da 1ª barra do dia
    first = df.groupby(day).cumcount() == 0
    df.loc[first, "od_first_dir"] = np.where(df.loc[first, "Close"] > df.loc[first, "Open"], 1, -1)
    df["od_first_dir"] = df.groupby(day)["od_first_dir"].transform("first")

    # Sessão asiática / Londres (crypto)
    asia_mask = (hh >= SESS_ASIA[0]) & (hh < SESS_ASIA[1])
    df["asia_hi"] = df.High.where(asia_mask).groupby(day).transform("max")
    df["asia_lo"] = df.Low.where(asia_mask).groupby(day).transform("min")
    lon_mask = (hh >= SESS_LONDON_RANGE[0]) & (hh < SESS_LONDON_RANGE[1])
    df["lon_hi"] = df.High.where(lon_mask).groupby(day).transform("max")
    df["lon_lo"] = df.Low.where(lon_mask).groupby(day).transform("min")

    # Delta rolling p/ divergência (soma 20)
    rng = (h - l).replace(0, np.nan)
    delta = ((c - l) / rng - 0.5) * 2 * v
    df["delta"] = delta
    df["delta_sum20"] = delta.rolling(20).sum()

    # Bias H4 (só direção — expansão usa ATR do próprio M15)
    h4 = df[["Close"]].resample("4h").last().dropna()
    h4["ema50"] = h4.Close.ewm(span=50, adjust=False).mean()
    df["h4_ema50"] = h4["ema50"].reindex(df.index, method="ffill")
    df["h4_close"] = h4.Close.reindex(df.index, method="ffill")

    # Contexto cruzado (WDO↔WIN / ETH↔BTC)
    if ctx:
        if "WIN" in ctx and ctx["WIN"] is not None and asset_key(symbol) == "WDO":
            w = ctx["WIN"][["Close", "atr"]].copy()
            w.columns = ["win_close", "win_atr"]
            df = df.join(w.reindex(df.index, method="ffill"), how="left")
            # z-score do spread WDO vs WIN normalizado por ATR
            spread = df["Close"] - df["win_close"]  # unidades diferentes — usa retorno relativo
            # melhor: retorno 20-barra de cada e diferença
            r_wdo = df["Close"].pct_change(20)
            r_win = df["win_close"].pct_change(20)
            diff = r_wdo - r_win
            df["corr_z"] = (diff - diff.rolling(100).mean()) / diff.rolling(100).std().replace(0, np.nan)
        if "BTCUSD" in ctx and ctx["BTCUSD"] is not None and asset_key(symbol) == "ETHUSD":
            b = ctx["BTCUSD"][["Close"]].copy()
            b.columns = ["btc_close"]
            df = df.join(b.reindex(df.index, method="ffill"), how="left")
            ratio = df["Close"] / df["btc_close"].replace(0, np.nan)
            df["spread_z"] = (ratio - ratio.rolling(100).mean()) / ratio.rolling(100).std().replace(0, np.nan)

    return df


def _parts(r):
    body = abs(r.Close - r.Open)
    rng = max(r.High - r.Low, 1e-9)
    up_wick = r.High - max(r.Close, r.Open)
    dn_wick = min(r.Close, r.Open) - r.Low
    return body, rng, up_wick, dn_wick


def reversal_up(df, i):
    r = df.iloc[i]
    p = df.iloc[i - 1]
    body, rng, uw, dw = _parts(r)
    hammer = dw >= 0.6 * rng and body <= 0.4 * rng
    engulf = (r.Close > r.Open) and (p.Close < p.Open) and (r.Close >= p.Open) and (r.Open <= p.Close)
    return hammer or engulf


def reversal_down(df, i):
    r = df.iloc[i]
    p = df.iloc[i - 1]
    body, rng, uw, dw = _parts(r)
    star = uw >= 0.6 * rng and body <= 0.4 * rng
    engulf = (r.Close < r.Open) and (p.Close > p.Open) and (r.Close <= p.Open) and (r.Open >= p.Close)
    return star or engulf


def _sig(d, entry, sl, rr1=1.5, rr2=2.5, partial=True):
    risk = abs(entry - sl)
    if risk <= 0:
        return None
    if d == "COMPRA":
        return {"dir": d, "sl": sl, "tp1": entry + rr1 * risk,
                "tp2": (entry + rr2 * risk) if partial else None, "partial": partial}
    return {"dir": d, "sl": sl, "tp1": entry - rr1 * risk,
            "tp2": (entry - rr2 * risk) if partial else None, "partial": partial}


# ════════════════════════════════════════════════════════════════════════════
# SETUPS B3
# ════════════════════════════════════════════════════════════════════════════

def s_failed_gap_fade_orb(df, i, sym):
    """ORB só no dia em que o fade do gap falhou (não tocou PDC até 10:45)."""
    r = df.iloc[i]
    if not (WIN_ORB_TRADE[0] < r.hh < WIN_ORB_TRADE[1]):
        return None
    if pd.isna(r.orb_hi) or pd.isna(r.orb_lo) or pd.isna(r.gap_pct) or abs(r.gap_pct) < 0.15:
        return None
    if int(r.touched_pdc or 0) == 1:  # fade ainda possível / já tocou → não é failed fade
        return None
    p = df.iloc[i - 1]
    # gap up sem tocar PDC → só compra no rompimento do ORB high
    if r.gap_pct > 0 and r.Close > r.orb_hi and p.Close <= r.orb_hi:
        return _sig("COMPRA", r.Close, r.orb_lo, 1.5, 3.0, True)
    if r.gap_pct < 0 and r.Close < r.orb_lo and p.Close >= r.orb_lo:
        return _sig("VENDA", r.Close, r.orb_hi, 1.5, 3.0, True)
    return None


def s_orb_volume_confirm(df, i, sym):
    """ORB clássico com volume da barra de rompimento ≥ 1,5× média do range ORB."""
    r = df.iloc[i]
    if not (WIN_ORB_TRADE[0] < r.hh < WIN_ORB_TRADE[1]):
        return None
    if pd.isna(r.orb_hi) or pd.isna(r.orb_lo) or pd.isna(r.orb_vol) or r.orb_vol <= 0:
        return None
    if r.Volume < 1.5 * r.orb_vol:
        return None
    p = df.iloc[i - 1]
    if r.Close > r.orb_hi and p.Close <= r.orb_hi:
        return _sig("COMPRA", r.Close, r.orb_lo, 1.5, 2.5, True)
    if r.Close < r.orb_lo and p.Close >= r.orb_lo:
        return _sig("VENDA", r.Close, r.orb_hi, 1.5, 2.5, True)
    return None


def s_midday_mean_revert(df, i, sym):
    """12:00–14:00: preço ≥ 1,5×ATR da VWAP + RSI extremo → retorno à VWAP."""
    r = df.iloc[i]
    if not (WIN_MIDDAY[0] <= r.hh < WIN_MIDDAY[1]):
        return None
    if pd.isna(r.vwap) or pd.isna(r.atr) or r.atr <= 0 or pd.isna(r.rsi):
        return None
    dist = abs(r.Close - r.vwap)
    if dist < 1.5 * r.atr:
        return None
    # short: acima da VWAP + RSI alto + rejeição
    if r.Close > r.vwap and r.rsi >= 65 and reversal_down(df, i):
        entry = r.Close
        sl = r.day_hi + _WIN_TICK
        risk = sl - entry
        if risk > 0:
            tp1 = r.vwap  # alvo = VWAP
            return {"dir": "VENDA", "sl": sl, "tp1": tp1, "tp2": None, "partial": False}
    if r.Close < r.vwap and r.rsi <= 35 and reversal_up(df, i):
        entry = r.Close
        sl = r.day_lo - _WIN_TICK
        risk = entry - sl
        if risk > 0:
            return {"dir": "COMPRA", "sl": sl, "tp1": r.vwap, "tp2": None, "partial": False}
    return None


def s_first_pullback_orb(df, i, sym):
    """Após ORB válido, pullback ≤ 50% do impulso + hold do nível ORB."""
    r = df.iloc[i]
    if not (11.0 <= r.hh < 14.0):
        return None
    if pd.isna(r.orb_hi) or pd.isna(r.orb_lo) or pd.isna(r.atr) or r.atr <= 0:
        return None
    orb_rng = r.orb_hi - r.orb_lo
    if orb_rng <= 0:
        return None
    # long: preço já rompeu orb_hi hoje e agora puxa de volta sem perder orb_hi
    # usa day_hi como proxy do impulso
    impulse_up = r.day_hi - r.orb_hi
    impulse_dn = r.orb_lo - r.day_lo
    if impulse_up >= 0.5 * orb_rng and r.Close >= r.orb_hi and r.Low <= r.orb_hi + 0.5 * impulse_up:
        if reversal_up(df, i) and r.Close > r.orb_hi:
            return _sig("COMPRA", r.Close, r.orb_lo, 1.5, 2.5, True)
    if impulse_dn >= 0.5 * orb_rng and r.Close <= r.orb_lo and r.High >= r.orb_lo - 0.5 * impulse_dn:
        if reversal_down(df, i) and r.Close < r.orb_lo:
            return _sig("VENDA", r.Close, r.orb_hi, 1.5, 2.5, True)
    return None


def s_overnight_inventory(df, i, sym):
    """Abertura vs extremos dos 3 pregões anteriores — fade de gap extremo."""
    r = df.iloc[i]
    # só na 2ª–3ª barra (09:15–09:45)
    if not (9.25 <= r.hh <= 9.75):
        return None
    if pd.isna(r.hi3) or pd.isna(r.lo3) or pd.isna(r.prev_close) or pd.isna(r.atr) or r.atr <= 0:
        return None
    # gap up acima do high dos 3 dias → fade short
    if r.day_open > r.hi3 and r.Close < r.Open and r.gap_pct > 0.2:
        entry = r.Close
        sl = r.day_hi + _WIN_TICK
        return _sig("VENDA", entry, sl, 1.5, 2.0, True)
    if r.day_open < r.lo3 and r.Close > r.Open and r.gap_pct < -0.2:
        entry = r.Close
        sl = r.day_lo - _WIN_TICK
        return _sig("COMPRA", entry, sl, 1.5, 2.0, True)
    return None


def s_open_drive_failure(df, i, sym):
    """Drive 09:00–09:30 falha em fazer novo extremo → fade para VWAP."""
    r = df.iloc[i]
    if abs(r.hh - 9.75) > 1e-6:  # barra 09:45
        return None
    if pd.isna(r.od_hi) or pd.isna(r.od_lo) or pd.isna(r.od_first_dir) or pd.isna(r.vwap):
        return None
    # drive de alta falhou: 1ª barra up mas 09:45 não faz new high vs od_hi e fecha < od_hi
    if r.od_first_dir > 0 and r.High < r.od_hi and r.Close < r.od_hi and r.Close < r.Open:
        entry = r.Close
        sl = r.od_hi + _WIN_TICK
        risk = sl - entry
        if risk > 0:
            return {"dir": "VENDA", "sl": sl, "tp1": r.vwap, "tp2": None, "partial": False}
    if r.od_first_dir < 0 and r.Low > r.od_lo and r.Close > r.od_lo and r.Close > r.Open:
        entry = r.Close
        sl = r.od_lo - _WIN_TICK
        risk = entry - sl
        if risk > 0:
            return {"dir": "COMPRA", "sl": sl, "tp1": r.vwap, "tp2": None, "partial": False}
    return None


def s_stop_run_then_go(df, i, sym):
    """Sweep além do ORB e fecha de volta dentro → entrada na direção do close."""
    r = df.iloc[i]
    if not (WIN_ORB_TRADE[0] < r.hh < 13.0):
        return None
    if pd.isna(r.orb_hi) or pd.isna(r.orb_lo):
        return None
    body, rng, uw, dw = _parts(r)
    # sweep low: Low < orb_lo mas Close > orb_lo (reentra) → COMPRA
    if r.Low < r.orb_lo and r.Close > r.orb_lo and dw >= 0.4 * rng:
        return _sig("COMPRA", r.Close, r.Low - _WIN_TICK, 1.5, 2.5, True)
    if r.High > r.orb_hi and r.Close < r.orb_hi and uw >= 0.4 * rng:
        return _sig("VENDA", r.Close, r.High + _WIN_TICK, 1.5, 2.5, True)
    return None


def s_atr_expansion_cont(df, i, sym):
    """Compressão ATR M15 → 1ª barra de expansão na direção do bias H4."""
    r = df.iloc[i]
    p = df.iloc[i - 1]
    if pd.isna(r.atr) or pd.isna(r.atr_ma) or pd.isna(r.h4_ema50) or pd.isna(r.h4_close):
        return None
    if p.atr_ma <= 0 or (p.atr / p.atr_ma) > 0.75:   # ainda não estava comprimido
        return None
    if r.atr_ma <= 0 or (r.atr / r.atr_ma) < 1.10:    # ainda não expandiu
        return None
    bias_up = r.h4_close > r.h4_ema50
    if bias_up and r.Close > r.Open and r.Close > r.ema20:
        sl = min(r.Low, p.Low)
        return _sig("COMPRA", r.Close, sl, 1.5, 2.5, True)
    if (not bias_up) and r.Close < r.Open and r.Close < r.ema20:
        sl = max(r.High, p.High)
        return _sig("VENDA", r.Close, sl, 1.5, 2.5, True)
    return None


def s_inside_day_break(df, i, sym):
    """Dia anterior inside day → rompimento do high/low na 1ª–2ª hora."""
    r = df.iloc[i]
    if not (9.25 <= r.hh <= 11.0):
        return None
    if pd.isna(r.prev_inside) or r.prev_inside < 0.5:
        return None
    if pd.isna(r.prev_day_hi) or pd.isna(r.prev_day_lo):
        return None
    p = df.iloc[i - 1]
    if r.Close > r.prev_day_hi and p.Close <= r.prev_day_hi:
        return _sig("COMPRA", r.Close, r.prev_day_lo, 1.5, 2.5, True)
    if r.Close < r.prev_day_lo and p.Close >= r.prev_day_lo:
        return _sig("VENDA", r.Close, r.prev_day_hi, 1.5, 2.5, True)
    return None


def s_power_hour_b3(df, i, sym):
    """16:00–17:15: rompe range das 2h anteriores com volume crescente."""
    r = df.iloc[i]
    if not (WIN_POWER[0] <= r.hh < WIN_POWER[1]):
        return None
    if i < 10:
        return None
    # range das ~2h anteriores (8 barras M15)
    win = df.iloc[i - 8:i]
    hi, lo = win.High.max(), win.Low.min()
    if hi <= lo:
        return None
    if r.Volume < 1.2 * (r.vol8 or 0):
        return None
    p = df.iloc[i - 1]
    if r.Close > hi and p.Close <= hi:
        return _sig("COMPRA", r.Close, lo, 1.5, 2.0, True)
    if r.Close < lo and p.Close >= lo:
        return _sig("VENDA", r.Close, hi, 1.5, 2.0, True)
    return None


def s_wdo_correl_fade(df, i, sym):
    """WDO estica ≥ 1,5σ vs WIN → fade com confirmação de delta."""
    r = df.iloc[i]
    if "corr_z" not in df.columns or pd.isna(r.corr_z) or pd.isna(r.atr) or r.atr <= 0:
        return None
    if not (10.0 <= r.hh < 16.0):
        return None
    vt = r.vol_sum10
    dn = (r.delta_sum10 / vt) if (vt and vt > 0) else 0
    if r.corr_z >= 1.5 and dn < -0.05 and reversal_down(df, i):
        return _sig("VENDA", r.Close, r.Close + 1.5 * r.atr, 1.5, 2.0, True)
    if r.corr_z <= -1.5 and dn > 0.05 and reversal_up(df, i):
        return _sig("COMPRA", r.Close, r.Close - 1.5 * r.atr, 1.5, 2.0, True)
    return None


# ════════════════════════════════════════════════════════════════════════════
# SETUPS CRYPTO
# ════════════════════════════════════════════════════════════════════════════

def s_ny_handoff(df, i, sym):
    """Espelho do LONDON_HANDOFF: range Londres estreito → rompe na abertura NY + delta."""
    r = df.iloc[i]
    if not (SESS_NY[0] <= r.hh < SESS_NY[1]):
        return None
    if pd.isna(r.lon_hi) or pd.isna(r.lon_lo) or pd.isna(r.atr) or r.atr <= 0:
        return None
    lon_rng = r.lon_hi - r.lon_lo
    atr_d = r.atr * 16
    if lon_rng <= 0 or lon_rng > 0.60 * atr_d:
        return None
    vt = r.vol_sum10
    dn = (r.delta_sum10 / vt) if (vt and vt > 0) else 0
    p = df.iloc[i - 1]
    if r.Close > r.lon_hi and p.Close <= r.lon_hi and dn > 0.15:
        entry = r.Close
        sl = r.lon_lo
        risk = entry - sl
        if risk > 0:
            return {"dir": "COMPRA", "sl": sl, "tp1": entry + 1.75 * lon_rng, "tp2": None, "partial": False}
    if r.Close < r.lon_lo and p.Close >= r.lon_lo and dn < -0.15:
        entry = r.Close
        sl = r.lon_hi
        risk = sl - entry
        if risk > 0:
            return {"dir": "VENDA", "sl": sl, "tp1": entry - 1.75 * lon_rng, "tp2": None, "partial": False}
    return None


def _of_core(df, i, ts):
    """Núcleo ORDER_FLOW (mesma lógica kimi_real) — retorna tuple ou None."""
    if i < 20:
        return None
    r = df.iloc[i]
    vt = r.vol_sum10
    if not vt or vt <= 0 or pd.isna(vt):
        return None
    dn = r.delta_sum10 / vt
    vwl, dev = r.va_vwl, r.va_dev
    if pd.isna(vwl) or pd.isna(dev):
        return None
    vah, val = vwl + dev, vwl - dev
    if dn > 0.3 and r.Close > vah and r.Close > r.Open and r.Volume >= (r.vol10 or 0) * 1.5:
        sl = min(val, r.Low - 2 * ts)
        ds = r.Close - sl
        if ds > 0:
            return ("COMPRA", sl, r.Close + 2 * ds, r.Close + 3 * ds)
    if dn < -0.3 and r.Close < val and r.Close < r.Open and r.Volume >= (r.vol10 or 0) * 1.5:
        sl = max(vah, r.High + 2 * ts)
        ds = sl - r.Close
        if ds > 0:
            return ("VENDA", sl, r.Close - 2 * ds, r.Close - 3 * ds)
    return None


def s_of_session_filter(df, i, sym):
    """ORDER_FLOW só nas janelas 08–11 e 14–17 (servidor)."""
    r = df.iloc[i]
    am = SESS_OF_AM[0] <= r.hh < SESS_OF_AM[1]
    pm = SESS_OF_PM[0] <= r.hh < SESS_OF_PM[1]
    if not (am or pm):
        return None
    ts = 0.01 if asset_key(sym) == "XAUUSD" else 0.01
    if asset_key(sym) == "ETHUSD":
        ts = 0.01
    elif asset_key(sym) == "BTCUSD":
        ts = 0.1
    sig = _of_core(df, i, ts)
    if not sig:
        return None
    d, sl, tp1, tp2 = sig
    return {"dir": d, "sl": sl, "tp1": tp1, "tp2": tp2, "partial": True}


def s_asia_compression_break(df, i, sym):
    """Range Ásia no percentil baixo de ATR + rompe com corpo ≥ 60% do range."""
    r = df.iloc[i]
    if not (SESS_LONDON_RANGE[0] <= r.hh < SESS_LONDON_RANGE[0] + 2):  # 09–11
        return None
    if pd.isna(r.asia_hi) or pd.isna(r.asia_lo) or pd.isna(r.atr) or r.atr <= 0:
        return None
    asia_rng = r.asia_hi - r.asia_lo
    atr_d = r.atr * 16
    if asia_rng <= 0 or asia_rng > 0.45 * atr_d:  # mais apertado que handoff
        return None
    body, rng, uw, dw = _parts(r)
    if body < 0.60 * rng:
        return None
    p = df.iloc[i - 1]
    if r.Close > r.asia_hi and p.Close <= r.asia_hi and r.Close > r.Open:
        entry = r.Close
        sl = r.asia_lo
        risk = entry - sl
        if risk > 0:
            return {"dir": "COMPRA", "sl": sl, "tp1": entry + 1.75 * asia_rng, "tp2": None, "partial": False}
    if r.Close < r.asia_lo and p.Close >= r.asia_lo and r.Close < r.Open:
        entry = r.Close
        sl = r.asia_hi
        risk = sl - entry
        if risk > 0:
            return {"dir": "VENDA", "sl": sl, "tp1": entry - 1.75 * asia_rng, "tp2": None, "partial": False}
    return None


def s_delta_divergence_fade(df, i, sym):
    """Preço faz HH/LL e delta diverge → fade."""
    r = df.iloc[i]
    if pd.isna(r.delta_sum20) or pd.isna(r.atr) or r.atr <= 0:
        return None
    if i < 40:
        return None
    # HH de preço sem HH de delta → short
    prev_hi = df.iloc[i - 20:i]["High"].max()
    prev_delta_hi = df.iloc[i - 20:i]["delta_sum20"].max()
    prev_lo = df.iloc[i - 20:i]["Low"].min()
    prev_delta_lo = df.iloc[i - 20:i]["delta_sum20"].min()
    if r.High >= prev_hi and r.delta_sum20 < prev_delta_hi and reversal_down(df, i):
        return _sig("VENDA", r.Close, r.High + 0.5 * r.atr, 1.5, 2.0, True)
    if r.Low <= prev_lo and r.delta_sum20 > prev_delta_lo and reversal_up(df, i):
        return _sig("COMPRA", r.Close, r.Low - 0.5 * r.atr, 1.5, 2.0, True)
    return None


def s_value_area_reject(df, i, sym):
    """Toque VAH/VAL (va_vwl±dev) + rejeição (pavio) + delta contrário."""
    r = df.iloc[i]
    if pd.isna(r.va_vwl) or pd.isna(r.va_dev) or pd.isna(r.atr) or r.atr <= 0:
        return None
    vah, val = r.va_vwl + r.va_dev, r.va_vwl - r.va_dev
    vt = r.vol_sum10
    dn = (r.delta_sum10 / vt) if (vt and vt > 0) else 0
    body, rng, uw, dw = _parts(r)
    # toque VAH + pavio superior + delta negativo
    if r.High >= vah and r.Close < vah and uw >= 0.5 * rng and dn < -0.10:
        return _sig("VENDA", r.Close, r.High + 0.3 * r.atr, 1.5, 2.0, True)
    if r.Low <= val and r.Close > val and dw >= 0.5 * rng and dn > 0.10:
        return _sig("COMPRA", r.Close, r.Low - 0.3 * r.atr, 1.5, 2.0, True)
    return None


def s_round_number_magnet(df, i, sym):
    """Número redondo: delta fraco → fade; delta forte → breakout."""
    r = df.iloc[i]
    k = asset_key(sym)
    step = 50.0 if k == "XAUUSD" else (100.0 if k == "ETHUSD" else 1000.0)
    lvl = round(r.Close / step) * step
    if lvl <= 0 or abs(r.Close - lvl) > 0.15 * (r.atr or 1):
        return None
    vt = r.vol_sum10
    dn = (r.delta_sum10 / vt) if (vt and vt > 0) else 0
    body, rng, uw, dw = _parts(r)
    near_from_below = r.Low <= lvl <= r.High and r.Close > lvl
    near_from_above = r.Low <= lvl <= r.High and r.Close < lvl
    # breakout forte
    if near_from_below and dn > 0.25 and r.Close > r.Open:
        return _sig("COMPRA", r.Close, lvl - (r.atr or 1), 1.5, 2.5, True)
    if near_from_above and dn < -0.25 and r.Close < r.Open:
        return _sig("VENDA", r.Close, lvl + (r.atr or 1), 1.5, 2.5, True)
    # fade fraco
    if r.High >= lvl and abs(dn) < 0.10 and uw >= 0.45 * rng:
        return _sig("VENDA", r.Close, r.High + 0.3 * (r.atr or 1), 1.5, 2.0, True)
    if r.Low <= lvl and abs(dn) < 0.10 and dw >= 0.45 * rng:
        return _sig("COMPRA", r.Close, r.Low - 0.3 * (r.atr or 1), 1.5, 2.0, True)
    return None


def s_power_hour_ny(df, i, sym):
    """Última hora NY: rompe range das 2h anteriores."""
    r = df.iloc[i]
    if not (SESS_POWER_NY[0] <= r.hh < SESS_POWER_NY[1]):
        return None
    if i < 10:
        return None
    win = df.iloc[i - 8:i]
    hi, lo = win.High.max(), win.Low.min()
    if hi <= lo or r.Volume < 1.2 * (r.vol8 or 0):
        return None
    p = df.iloc[i - 1]
    if r.Close > hi and p.Close <= hi:
        return _sig("COMPRA", r.Close, lo, 1.5, 2.0, True)
    if r.Close < lo and p.Close >= lo:
        return _sig("VENDA", r.Close, hi, 1.5, 2.0, True)
    return None


def s_atr_expansion_xau(df, i, sym):
    return s_atr_expansion_cont(df, i, sym)


def s_eth_btc_spread(df, i, sym):
    """Z-score do ratio ETH/BTC reverte de ±2σ."""
    r = df.iloc[i]
    if "spread_z" not in df.columns or pd.isna(r.spread_z) or pd.isna(r.atr) or r.atr <= 0:
        return None
    if r.spread_z >= 2.0 and reversal_down(df, i):
        return _sig("VENDA", r.Close, r.Close + 1.5 * r.atr, 1.5, 2.0, True)
    if r.spread_z <= -2.0 and reversal_up(df, i):
        return _sig("COMPRA", r.Close, r.Close - 1.5 * r.atr, 1.5, 2.0, True)
    return None


# ════════════════════════════════════════════════════════════════════════════
# REGISTRO
# ════════════════════════════════════════════════════════════════════════════

STRATS = {
    "b3": {
        "FAILED_GAP_FADE_ORB": s_failed_gap_fade_orb,
        "ORB_VOLUME_CONFIRM": s_orb_volume_confirm,
        "MIDDAY_MEAN_REVERT": s_midday_mean_revert,
        "FIRST_PULLBACK_ORB": s_first_pullback_orb,
        "OVERNIGHT_INVENTORY": s_overnight_inventory,
        "OPEN_DRIVE_FAILURE": s_open_drive_failure,
        "STOP_RUN_THEN_GO": s_stop_run_then_go,
        "ATR_EXPANSION_CONT": s_atr_expansion_cont,
        "INSIDE_DAY_BREAK": s_inside_day_break,
        "POWER_HOUR_B3": s_power_hour_b3,
        "WDO_CORREL_FADE": s_wdo_correl_fade,
    },
    "crypto": {
        "NY_HANDOFF": s_ny_handoff,
        "OF_SESSION_FILTER": s_of_session_filter,
        "ASIA_COMPRESSION_BREAK": s_asia_compression_break,
        "DELTA_DIVERGENCE_FADE": s_delta_divergence_fade,
        "VALUE_AREA_REJECT": s_value_area_reject,
        "ROUND_NUMBER_MAGNET": s_round_number_magnet,
        "POWER_HOUR_NY": s_power_hour_ny,
        "ATR_EXPANSION_XAU": s_atr_expansion_xau,
        "ETH_BTC_SPREAD": s_eth_btc_spread,
        "OF_SESSION_ETH": s_of_session_filter,
    },
}

STRAT_SYM = {
    "FAILED_GAP_FADE_ORB": "WIN", "ORB_VOLUME_CONFIRM": "WIN",
    "MIDDAY_MEAN_REVERT": "WIN", "FIRST_PULLBACK_ORB": "WIN",
    "OVERNIGHT_INVENTORY": "WIN", "OPEN_DRIVE_FAILURE": "WIN",
    "STOP_RUN_THEN_GO": "WIN", "ATR_EXPANSION_CONT": "WIN",
    "INSIDE_DAY_BREAK": "WIN", "POWER_HOUR_B3": "WIN",
    "WDO_CORREL_FADE": "WDO",
    "NY_HANDOFF": "XAUUSD", "OF_SESSION_FILTER": "XAUUSD",
    "ASIA_COMPRESSION_BREAK": "XAUUSD", "DELTA_DIVERGENCE_FADE": "XAUUSD",
    "VALUE_AREA_REJECT": "XAUUSD", "ROUND_NUMBER_MAGNET": "XAUUSD",
    "POWER_HOUR_NY": "XAUUSD", "ATR_EXPANSION_XAU": "XAUUSD",
    "ETH_BTC_SPREAD": "ETHUSD", "OF_SESSION_ETH": "ETHUSD",
}

ONE_PER_DAY = {
    "FAILED_GAP_FADE_ORB", "ORB_VOLUME_CONFIRM", "OVERNIGHT_INVENTORY",
    "OPEN_DRIVE_FAILURE", "INSIDE_DAY_BREAK", "POWER_HOUR_B3",
    "NY_HANDOFF", "ASIA_COMPRESSION_BREAK", "POWER_HOUR_NY",
}


# ════════════════════════════════════════════════════════════════════════════
# SIMULADOR (mesmo do kimi2)
# ════════════════════════════════════════════════════════════════════════════

def simulate(df, symbol, strat_fn, name):
    is_b3 = asset_key(symbol) in ("WIN", "WDO")
    max_bars = 96
    one_per_day = name in ONE_PER_DAY
    trades, pos = [], None
    last_day_traded = None
    n = len(df)
    for i in range(460, n):
        if pos:
            r = df.iloc[i]
            buy = pos["dir"] == "COMPRA"
            exit_R = None
            stopped = True
            if (r.Low <= pos["sl"]) if buy else (r.High >= pos["sl"]):
                exit_R = ((pos["sl"] - pos["entry"]) / pos["risk"]) if buy else (
                    (pos["entry"] - pos["sl"]) / pos["risk"])
            elif pos["tp2"] and ((r.High >= pos["tp2"]) if buy else (r.Low <= pos["tp2"])):
                exit_R = (pos["tp2"] - pos["entry"]) / pos["risk"] if buy else (
                    (pos["entry"] - pos["tp2"]) / pos["risk"])
                stopped = False
            elif (not pos["tp2"]) and ((r.High >= pos["tp1"]) if buy else (r.Low <= pos["tp1"])):
                exit_R = (pos["tp1"] - pos["entry"]) / pos["risk"] if buy else (
                    (pos["entry"] - pos["tp1"]) / pos["risk"])
                stopped = False
            elif pos["tp2"] and (not pos["tp1_done"]) and (
                    (r.High >= pos["tp1"]) if buy else (r.Low <= pos["tp1"])):
                pos["tp1_done"] = True
                pos["realized"] = 0.5 * (
                    (pos["tp1"] - pos["entry"]) / pos["risk"] if buy else (
                        (pos["entry"] - pos["tp1"]) / pos["risk"])) if pos["partial"] else 0.0
                pos["sl"] = pos["entry"]
            if exit_R is None and is_b3 and r["day"] != pos["day"]:
                px = df.iloc[i - 1].Close
                exit_R = ((px - pos["entry"]) / pos["risk"]) if buy else (
                    (pos["entry"] - px) / pos["risk"])
            if exit_R is None and (not is_b3) and (i - pos["i"]) >= max_bars:
                px = r.Close
                exit_R = ((px - pos["entry"]) / pos["risk"]) if buy else (
                    (pos["entry"] - px) / pos["risk"])
            if exit_R is not None:
                rem = 0.5 if (pos["partial"] and pos["tp1_done"]) else 1.0
                gross = pos["realized"] + rem * exit_R
                cost = roundtrip_cost_pts(symbol, stopped) / pos["risk"]
                trades.append({"ts": pos["ts"], "dir": pos["dir"], "gross_R": round(gross, 3),
                               "cost_R": round(cost, 3), "net_R": round(gross - cost, 3)})
                pos = None
            continue
        if one_per_day and df.iloc[i]["day"] == last_day_traded:
            continue
        sig = strat_fn(df, i, symbol)
        if not sig:
            continue
        entry = float(df.iloc[i].Close)
        risk = abs(entry - sig["sl"])
        atr_i = float(df.iloc[i].atr or 0)
        if risk <= 0 or risk < 0.10 * max(atr_i, 1e-9):
            continue
        pos = {"ts": df.index[i], "i": i, "day": df.iloc[i]["day"], "dir": sig["dir"],
               "entry": entry, "sl": sig["sl"], "tp1": sig["tp1"], "tp2": sig["tp2"],
               "partial": sig["partial"], "tp1_done": False, "realized": 0.0, "risk": risk}
        last_day_traded = df.iloc[i]["day"]
    return pd.DataFrame(trades)


def stats(t):
    if t is None or t.empty:
        return {"n": 0}
    w = t[t.net_R > 0]
    l = t[t.net_R <= 0]
    gw, gl = w.net_R.sum(), -l.net_R.sum()
    eq = t.net_R.cumsum()
    dd = (eq - eq.cummax()).min()
    return {"n": len(t), "win": round(len(w) / len(t) * 100, 1),
            "gross": round(t.gross_R.mean(), 3), "cost": round(t.cost_R.mean(), 3),
            "net": round(t.net_R.mean(), 3),
            "PF": round(gw / gl, 2) if gl > 0 else float("inf"),
            "tot": round(t.net_R.sum(), 1), "dd": round(dd, 1)}


def consistency(t):
    if t is None or t.empty:
        return 0
    t2 = t.copy()
    t2["p"] = pd.to_datetime(t2["ts"]).dt.to_period("2M").astype(str)
    per = t2.groupby("p")["net_R"].mean()
    return round((per > 0).sum() / len(per) * 100) if len(per) else 0


def verdict(s, cons, s_out):
    if s.get("n", 0) < 40:
        return "AMOSTRA FRACA"
    oos_ok = s_out.get("n", 0) >= 20 and s_out["net"] >= 0.08 and s_out["PF"] >= 1.15
    if s["net"] >= 0.10 and s["PF"] >= 1.25 and cons >= 55 and oos_ok:
        return "🟢 GO"
    if s["net"] > 0.05 and s["PF"] >= 1.15:
        return "🟡 PROMISSOR" + ("" if oos_ok else " (OOS fraco)")
    return "🔴 REPROVADO"


def _line(tag, s, cons=None):
    if s.get("n", 0) == 0:
        return f"    {tag:<20} (sem trades)"
    c = f" | cons {cons}%" if cons is not None else ""
    return (f"    {tag:<20} n={s['n']:<5} win {s['win']:>5}% | LÍQ {s['net']:+.3f} R | "
            f"PF {s['PF']:<5} | tot {s['tot']:+7.1f} | DD {s['dd']:>7}{c}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--grupo", choices=["b3", "crypto"], default="b3")
    ap.add_argument("--tf", type=int, default=15)
    ap.add_argument("--bars", type=int, default=300000)
    args = ap.parse_args()

    os.environ["KIMI_GROUP"] = args.grupo
    os.makedirs("logs", exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    logf = open(f"logs/backtest_setups_novos_{args.grupo}_{stamp}.txt", "w", encoding="utf-8")

    def out(s):
        print(s)
        logf.write(s + "\n")
        logf.flush()

    out(f"{'#' * 74}\n# SETUPS NOVOS (Auto) — {args.grupo} · M{args.tf} · "
        f"{datetime.now():%d/%m %H:%M}\n# NÃO altera motores ao vivo\n{'#' * 74}")
    strat_map = STRATS[args.grupo]
    syms = sorted({STRAT_SYM[n] for n in strat_map})
    # contexto cruzado
    if args.grupo == "b3":
        syms = sorted(set(syms) | {"WIN"})  # WIN sempre p/ WDO_CORREL
    if args.grupo == "crypto":
        syms = sorted(set(syms) | {"BTCUSD"})  # BTC p/ ETH spread
    sym_full = {"WIN": "WIN$D", "WDO": "WDO$D", "XAUUSD": "XAUUSD",
                "ETHUSD": "ETHUSD", "BTCUSD": "BTCUSD"}
    ranking = []

    _mt5c = None
    if args.grupo == "crypto":
        from backtest_crypto_pro import mt5_connect, fetch as _cp_fetch
        _mt5c = mt5_connect()

    def _get(k):
        if args.grupo == "b3":
            return _b3_fetch(sym_full[k], args.tf, args.bars)
        raw = _cp_fetch(_mt5c, sym_full[k], args.tf, args.bars)
        return add_indicators(raw) if raw is not None else None

    raw_dfs = {}
    for k in syms:
        t0 = time.time()
        df = _get(k)
        if df is None or len(df) < 5000:
            out(f"\n!! {k}: sem histórico suficiente")
            continue
        raw_dfs[k] = df
        anos = len(df) / (26 if k in ("WIN", "WDO") else 96) / (252 if k in ("WIN", "WDO") else 365)
        out(f"\n{'=' * 74}\n  {k}: {len(df)} candles M{args.tf} (~{anos:.1f} anos)  "
            f"[{df.index[0].date()} → {df.index[-1].date()}]  ({time.time() - t0:.0f}s fetch)\n{'=' * 74}")

    # add_extra com contexto
    ctx_base = {}
    if "WIN" in raw_dfs:
        ctx_base["WIN"] = raw_dfs["WIN"]
    if "BTCUSD" in raw_dfs:
        ctx_base["BTCUSD"] = raw_dfs["BTCUSD"]

    dfs = {}
    for k, df in raw_dfs.items():
        dfs[k] = add_extra(df.copy(), sym_full[k], ctx=ctx_base)

    for name, fn in strat_map.items():
        k = STRAT_SYM[name]
        if k not in dfs:
            out(f"\n  ▸ {name} ({k}): sem dados")
            continue
        df = dfs[k]
        symf = sym_full[k]
        t0 = time.time()
        t = simulate(df, symf, fn, name)
        s = stats(t)
        cons = consistency(t)
        out(f"\n  ▸ {name}  ({k})   [{time.time() - t0:.0f}s]")
        out(_line("PERÍODO COMPLETO", s, cons))
        s_out = {"n": 0}
        if not t.empty:
            t = t.sort_values("ts").reset_index(drop=True)
            cut = int(len(t) * (1 - OOS_FRAC))
            s_in, s_out = stats(t.iloc[:cut]), stats(t.iloc[cut:])
            out(_line("IN-SAMPLE  (70%)", s_in, consistency(t.iloc[:cut])))
            out(_line("OUT-SAMPLE (30%)", s_out, consistency(t.iloc[cut:])))
            if s_out.get("n", 0) >= 20:
                seg = s_out["net"] >= 0.08 and s_out["PF"] >= 1.15
                out(f"      → OUT-OF-SAMPLE {'SEGUROU ✅' if seg else 'CAIU ❌'} "
                    f"(net {s_out['net']:+.3f}, PF {s_out['PF']})")
            t.to_csv(f"logs/novos_{k}_{name}.csv", index=False)
        v = verdict(s, cons, s_out)
        out(f"      VEREDITO: {v}")
        ranking.append({"setup": name, "sym": k, **s, "cons%": cons, "veredito": v})

    out(f"\n{'#' * 74}\n  RANKING (líquida)\n{'#' * 74}")
    rk = pd.DataFrame(ranking)
    if not rk.empty:
        rk = rk.sort_values("net", ascending=False)
        for _, r in rk.iterrows():
            if r.get("n", 0) == 0:
                out(f"  {r.sym:<7} {r.setup:<22} sem trades")
                continue
            out(f"  {r.sym:<7} {r.setup:<22} n={int(r.n):<5} net {r.net:+.3f} R  PF {r.PF:<5} "
                f"cons {r['cons%']}%  {r.veredito}")
    out(f"\n# Critério GO: net ≥ +0,10 R · PF ≥ 1,25 · consist. ≥ 55% · OUT-OF-SAMPLE segura")
    out(f"# FIM — logs/backtest_setups_novos_{args.grupo}_{stamp}.txt")
    logf.close()


if __name__ == "__main__":
    main()
