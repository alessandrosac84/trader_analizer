"""
backtest_setups_novos_v2.py — RODADA 2: variações dos amarelos + setups criativos.

Baseado no que quase passou na v1:
  · OVERNIGHT_INVENTORY (WIN) — +0,14 R mas OOS fraco / cons 49%
  · OF_SESSION_FILTER (XAU)   — +0,21 R / cons 73% mas OOS caiu
  · ASIA_COMPRESSION_BREAK    — +0,11 R mas OOS fraco
  · OF_SESSION_ETH            — +0,24 R / amostra fina
  · WDO_CORREL OOS positivo   — IS ruim → apertar filtros

Régua idêntica (custos, OOS 70/30, bimestre, GO). NÃO altera motores ao vivo.

Uso:
  python backtest_setups_novos_v2.py --grupo b3
  python backtest_setups_novos_v2.py --grupo crypto
  python rodar_backtest_setups_novos_v2.py   # um comando, dois grupos
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
from backtest_setups_novos import (
    add_extra as _add_extra_v1,
    stats, consistency, verdict, _line, _sig,
    reversal_up, reversal_down, _parts, _of_core,
    OOS_FRAC, _WIN_TICK,
    SESS_ASIA, SESS_LONDON_RANGE, SESS_NY,
)

# ── Janelas adicionais ──────────────────────────────────────────────────────
SESS_LONDON_TRADE = (9, 11)      # handoff Ásia→Londres (igual produção)
SESS_NY_EARLY     = (15.5, 17.5)
WIN_IB            = (9.0, 10.0)  # Initial Balance
WIN_GAP_FADE      = (9.25, 9.75) # 09:15–09:45


def add_extra_v2(df, symbol, ctx=None):
    df = _add_extra_v1(df, symbol, ctx=ctx)
    day = df["day"]
    hh = df["hh"]
    df["dow"] = pd.to_datetime(df.index).dayofweek  # 0=seg … 4=sex
    # Initial Balance 09:00–10:00
    ib = (hh >= WIN_IB[0]) & (hh < WIN_IB[1])
    df["ib_hi"] = df.High.where(ib).groupby(day).transform("max")
    df["ib_lo"] = df.Low.where(ib).groupby(day).transform("min")
    # gap em ATR (não %)
    df["gap_atr"] = (df["day_open"] - df["prev_close"]) / df["atr"].replace(0, np.nan)
    # toque PDC só até 10:00 (failed fade mais cedo)
    pre10 = hh < 10.0
    touch = ((df["Low"] <= df["prev_close"]) | (df["High"] >= df["prev_close"])) & pre10
    df["touched_pdc_10"] = touch.astype(int).groupby(day).cummax()
    # range Ásia em ATR diário approx
    asia_rng = (df["asia_hi"] - df["asia_lo"]).replace(0, np.nan)
    df["asia_atr_ratio"] = asia_rng / (df["atr"] * 16).replace(0, np.nan)
    lon_rng = (df["lon_hi"] - df["lon_lo"]).replace(0, np.nan)
    df["lon_atr_ratio"] = lon_rng / (df["atr"] * 16).replace(0, np.nan)
    # delta normalizado pronto
    vt = df["vol_sum10"].replace(0, np.nan)
    df["dn"] = df["delta_sum10"] / vt
    # regime vol: ATR vs média
    df["atr_ratio"] = df["atr"] / df["atr_ma"].replace(0, np.nan)
    # distância VWAP em ATR
    df["vwap_dist"] = (df["Close"] - df["vwap"]) / df["atr"].replace(0, np.nan)
    return df


def _tick(sym):
    k = asset_key(sym)
    return {"WIN": 5.0, "WDO": 0.5, "XAUUSD": 0.01, "ETHUSD": 0.01,
            "BTCUSD": 0.1, "EURUSD": 0.00001, "GBPUSD": 0.00001}.get(k, 0.01)


def _of(df, i, sym, thr=0.30):
    """ORDER_FLOW com limiar de delta configurável → dict do simulador."""
    sig = _of_core(df, i, _tick(sym))
    if not sig:
        return None
    # reavalia com thr custom: se thr != 0.30, filtra pelo dn
    r = df.iloc[i]
    vt = r.vol_sum10
    if not vt or vt <= 0:
        return None
    dn = r.delta_sum10 / vt
    d, sl, tp1, tp2 = sig
    if d == "COMPRA" and dn < thr:
        return None
    if d == "VENDA" and dn > -thr:
        return None
    return {"dir": d, "sl": sl, "tp1": tp1, "tp2": tp2, "partial": True}


# ════════════════════════════════════════════════════════════════════════════
# B3 — variações OVERNIGHT + criativos WIN/WDO
# ════════════════════════════════════════════════════════════════════════════

def s_ovn_atr(df, i, sym):
    """OVERNIGHT por gap≥1.2 ATR (não %), fade com volume, seg–qui."""
    r = df.iloc[i]
    if not (9.25 <= r.hh <= 9.75) or r.dow >= 4:
        return None
    if pd.isna(r.gap_atr) or pd.isna(r.hi3) or abs(r.gap_atr) < 1.2:
        return None
    if r.Volume < 1.2 * (r.vol8 or 0):
        return None
    if r.gap_atr > 0 and r.day_open > r.hi3 and r.Close < r.Open:
        return _sig("VENDA", r.Close, r.day_hi + _WIN_TICK, 1.5, 2.0, True)
    if r.gap_atr < 0 and r.day_open < r.lo3 and r.Close > r.Open:
        return _sig("COMPRA", r.Close, r.day_lo - _WIN_TICK, 1.5, 2.0, True)
    return None


def s_ovn_to_pdc(df, i, sym):
    """OVERNIGHT extremo → alvo = PDC (RR dinâmico), só gap≥0.35%."""
    r = df.iloc[i]
    if not (9.25 <= r.hh <= 9.5) or r.dow >= 4:
        return None
    if pd.isna(r.gap_pct) or pd.isna(r.prev_close) or abs(r.gap_pct) < 0.35:
        return None
    if pd.isna(r.hi3) or pd.isna(r.lo3):
        return None
    if r.gap_pct > 0 and r.day_open >= r.hi3 and r.Close < r.Open:
        entry, sl, tp = r.Close, r.day_hi + _WIN_TICK, r.prev_close
        risk = sl - entry
        if risk > 0 and entry > tp:
            return {"dir": "VENDA", "sl": sl, "tp1": tp, "tp2": None, "partial": False}
    if r.gap_pct < 0 and r.day_open <= r.lo3 and r.Close > r.Open:
        entry, sl, tp = r.Close, r.day_lo - _WIN_TICK, r.prev_close
        risk = entry - sl
        if risk > 0 and entry < tp:
            return {"dir": "COMPRA", "sl": sl, "tp1": tp, "tp2": None, "partial": False}
    return None


def s_ovn_quiet_reg(df, i, sym):
    """OVERNIGHT só em regime ATR comprimido (atr_ratio < 1.0) — mean-reversion."""
    r = df.iloc[i]
    if not (9.25 <= r.hh <= 9.75):
        return None
    if pd.isna(r.atr_ratio) or r.atr_ratio >= 1.0:
        return None
    if pd.isna(r.gap_pct) or abs(r.gap_pct) < 0.25 or pd.isna(r.hi3):
        return None
    if r.gap_pct > 0 and r.day_open > r.hi3 and reversal_down(df, i):
        return _sig("VENDA", r.Close, r.day_hi + _WIN_TICK, 1.5, 2.0, True)
    if r.gap_pct < 0 and r.day_open < r.lo3 and reversal_up(df, i):
        return _sig("COMPRA", r.Close, r.day_lo - _WIN_TICK, 1.5, 2.0, True)
    return None


def s_gap_fade_0930(df, i, sym):
    """Fade clássico do gap às 09:30 se ainda estendido ≥0.8 ATR do PDC."""
    r = df.iloc[i]
    if abs(r.hh - 9.5) > 1e-6:
        return None
    if pd.isna(r.prev_close) or pd.isna(r.atr) or r.atr <= 0 or pd.isna(r.gap_pct):
        return None
    if abs(r.gap_pct) < 0.20:
        return None
    dist = abs(r.Close - r.prev_close)
    if dist < 0.8 * r.atr:
        return None
    if r.gap_pct > 0 and r.Close > r.prev_close and r.Close < r.Open:
        return _sig("VENDA", r.Close, r.day_hi + _WIN_TICK, 1.5, 2.0, True)
    if r.gap_pct < 0 and r.Close < r.prev_close and r.Close > r.Open:
        return _sig("COMPRA", r.Close, r.day_lo - _WIN_TICK, 1.5, 2.0, True)
    return None


def s_ib_break_vol(df, i, sym):
    """Rompimento do Initial Balance (09–10) após 10:00 com volume 1,4×."""
    r = df.iloc[i]
    if not (10.0 <= r.hh < 12.0):
        return None
    if pd.isna(r.ib_hi) or pd.isna(r.ib_lo):
        return None
    if r.Volume < 1.4 * (r.vol8 or 0):
        return None
    p = df.iloc[i - 1]
    if r.Close > r.ib_hi and p.Close <= r.ib_hi:
        return _sig("COMPRA", r.Close, r.ib_lo, 1.5, 2.5, True)
    if r.Close < r.ib_lo and p.Close >= r.ib_lo:
        return _sig("VENDA", r.Close, r.ib_hi, 1.5, 2.5, True)
    return None


def s_ib_fail_fade(df, i, sym):
    """Falso rompimento do IB: sweep e fecha de volta → fade."""
    r = df.iloc[i]
    if not (10.0 <= r.hh < 13.0):
        return None
    if pd.isna(r.ib_hi) or pd.isna(r.ib_lo):
        return None
    body, rng, uw, dw = _parts(r)
    if r.High > r.ib_hi and r.Close < r.ib_hi and uw >= 0.4 * rng:
        return _sig("VENDA", r.Close, r.High + _WIN_TICK, 1.5, 2.0, True)
    if r.Low < r.ib_lo and r.Close > r.ib_lo and dw >= 0.4 * rng:
        return _sig("COMPRA", r.Close, r.Low - _WIN_TICK, 1.5, 2.0, True)
    return None


def s_failed_fade_orb_v2(df, i, sym):
    """FAILED_GAP_FADE_ORB afrouxado: não tocou PDC até 10:00 + gap≥0.15%."""
    r = df.iloc[i]
    if not (10.75 < r.hh < 12.5):
        return None
    if pd.isna(r.orb_hi) or pd.isna(r.gap_pct) or abs(r.gap_pct) < 0.15:
        return None
    if int(r.touched_pdc_10 or 0) == 1:
        return None
    p = df.iloc[i - 1]
    if r.gap_pct > 0 and r.Close > r.orb_hi and p.Close <= r.orb_hi:
        return _sig("COMPRA", r.Close, r.orb_lo, 1.5, 3.0, True)
    if r.gap_pct < 0 and r.Close < r.orb_lo and p.Close >= r.orb_lo:
        return _sig("VENDA", r.Close, r.orb_hi, 1.5, 3.0, True)
    return None


def s_vwap_reclaim(df, i, sym):
    """Manhã: dump/rally além de 1 ATR da VWAP e reclaim com volume."""
    r = df.iloc[i]
    if not (10.0 <= r.hh < 13.0):
        return None
    if pd.isna(r.vwap_dist) or pd.isna(r.vwap) or abs(r.vwap_dist) < 1.0:
        return None
    p = df.iloc[i - 1]
    if r.Volume < 1.3 * (r.vol8 or 0):
        return None
    # reclaim de baixo: estava abaixo e fecha acima da VWAP
    if p.Close < p.vwap and r.Close > r.vwap and r.Close > r.Open:
        return _sig("COMPRA", r.Close, min(r.Low, p.Low) - _WIN_TICK, 1.5, 2.5, True)
    if p.Close > p.vwap and r.Close < r.vwap and r.Close < r.Open:
        return _sig("VENDA", r.Close, max(r.High, p.High) + _WIN_TICK, 1.5, 2.5, True)
    return None


def s_stop_run_filtered(df, i, sym):
    """STOP_RUN só com volume ≥1,5× e ATR não expandido (evita tendência forte)."""
    r = df.iloc[i]
    if not (10.75 < r.hh < 13.0):
        return None
    if pd.isna(r.orb_hi) or pd.isna(r.atr_ratio) or r.atr_ratio > 1.3:
        return None
    if r.Volume < 1.5 * (r.vol8 or 0):
        return None
    body, rng, uw, dw = _parts(r)
    if r.Low < r.orb_lo and r.Close > r.orb_lo and dw >= 0.45 * rng:
        return _sig("COMPRA", r.Close, r.Low - _WIN_TICK, 1.5, 2.5, True)
    if r.High > r.orb_hi and r.Close < r.orb_hi and uw >= 0.45 * rng:
        return _sig("VENDA", r.Close, r.High + _WIN_TICK, 1.5, 2.5, True)
    return None


def s_wdo_correl_v2(df, i, sym):
    """WDO correl z≥2.0, só 10–13h, delta confirmando (OOS da v1 era bom)."""
    r = df.iloc[i]
    if "corr_z" not in df.columns or pd.isna(r.corr_z):
        return None
    if not (10.0 <= r.hh < 13.0) or r.dow >= 5:
        return None
    dn = r.dn if not pd.isna(r.dn) else 0
    if r.corr_z >= 2.0 and dn < -0.10 and reversal_down(df, i):
        return _sig("VENDA", r.Close, r.Close + 1.25 * r.atr, 1.5, 2.0, True)
    if r.corr_z <= -2.0 and dn > 0.10 and reversal_up(df, i):
        return _sig("COMPRA", r.Close, r.Close - 1.25 * r.atr, 1.5, 2.0, True)
    return None


def s_win_trend_pb_h4(df, i, sym):
    """Pullback à EMA20 em tendência H4, só 10–15h, RSI neutro."""
    r = df.iloc[i]
    if not (10.0 <= r.hh < 15.0):
        return None
    if pd.isna(r.h4_ema50) or pd.isna(r.h4_close) or pd.isna(r.ema20) or pd.isna(r.rsi):
        return None
    up = r.h4_close > r.h4_ema50
    near = abs(r.Close - r.ema20) < 0.6 * (r.atr or 1)
    if up and near and 40 <= r.rsi <= 60 and reversal_up(df, i):
        return _sig("COMPRA", r.Close, r.Low - _WIN_TICK, 1.5, 2.5, True)
    if (not up) and near and 40 <= r.rsi <= 60 and reversal_down(df, i):
        return _sig("VENDA", r.Close, r.High + _WIN_TICK, 1.5, 2.5, True)
    return None


def s_orb_dir_vol(df, i, sym):
    """ORB alinhado ao gap + volume 1,5× média ORB."""
    r = df.iloc[i]
    if not (10.75 < r.hh < 12.0):
        return None
    if pd.isna(r.orb_hi) or pd.isna(r.gap_pct) or abs(r.gap_pct) < 0.2:
        return None
    if pd.isna(r.orb_vol) or r.orb_vol <= 0 or r.Volume < 1.5 * r.orb_vol:
        return None
    p = df.iloc[i - 1]
    orb_above = r.orb_lo > r.day_open
    orb_below = r.orb_hi < r.day_open
    if r.gap_pct > 0 and orb_above and r.Close > r.orb_hi and p.Close <= r.orb_hi:
        return _sig("COMPRA", r.Close, r.orb_lo, 1.5, 3.0, True)
    if r.gap_pct < 0 and orb_below and r.Close < r.orb_lo and p.Close >= r.orb_lo:
        return _sig("VENDA", r.Close, r.orb_hi, 1.5, 3.0, True)
    return None


def s_afternoon_ext_fade(df, i, sym):
    """14–15h: dia estendido ≥2 ATR da abertura + reversão → fade p/ VWAP."""
    r = df.iloc[i]
    if not (14.0 <= r.hh < 15.5):
        return None
    if pd.isna(r.atr) or r.atr <= 0 or pd.isna(r.vwap) or pd.isna(r.day_open):
        return None
    ext = abs(r.Close - r.day_open)
    if ext < 2.0 * r.atr:
        return None
    if r.Close > r.day_open and reversal_down(df, i):
        risk = (r.day_hi + _WIN_TICK) - r.Close
        if risk > 0:
            return {"dir": "VENDA", "sl": r.day_hi + _WIN_TICK, "tp1": r.vwap, "tp2": None, "partial": False}
    if r.Close < r.day_open and reversal_up(df, i):
        risk = r.Close - (r.day_lo - _WIN_TICK)
        if risk > 0:
            return {"dir": "COMPRA", "sl": r.day_lo - _WIN_TICK, "tp1": r.vwap, "tp2": None, "partial": False}
    return None


# ════════════════════════════════════════════════════════════════════════════
# CRYPTO — variações OF / ASIA / handoff + criativos
# ════════════════════════════════════════════════════════════════════════════

def s_of_london_only(df, i, sym):
    """OF só 09–11 (janela do LONDON_HANDOFF que já passou GO)."""
    r = df.iloc[i]
    if not (SESS_LONDON_TRADE[0] <= r.hh < SESS_LONDON_TRADE[1]):
        return None
    return _of(df, i, sym, thr=0.30)


def s_of_london_strict(df, i, sym):
    """OF 09–11 + delta ≥0.40 (mais seletivo — combate overfit OOS)."""
    r = df.iloc[i]
    if not (SESS_LONDON_TRADE[0] <= r.hh < SESS_LONDON_TRADE[1]):
        return None
    return _of(df, i, sym, thr=0.40)


def s_of_ny_only(df, i, sym):
    """OF só na abertura NY 15:30–17:30."""
    r = df.iloc[i]
    if not (SESS_NY_EARLY[0] <= r.hh < SESS_NY_EARLY[1]):
        return None
    return _of(df, i, sym, thr=0.30)


def s_of_tue_thu(df, i, sym):
    """OF manhã+tarde (08–11 / 14–17) só ter–qui."""
    r = df.iloc[i]
    if r.dow not in (1, 2, 3):
        return None
    am = 8 <= r.hh < 11
    pm = 14 <= r.hh < 17
    if not (am or pm):
        return None
    return _of(df, i, sym, thr=0.30)


def s_of_low_vol(df, i, sym):
    """OF só com atr_ratio < 1.1 (evita caos de notícia)."""
    r = df.iloc[i]
    am = 8 <= r.hh < 11
    pm = 14 <= r.hh < 17
    if not (am or pm):
        return None
    if pd.isna(r.atr_ratio) or r.atr_ratio >= 1.1:
        return None
    return _of(df, i, sym, thr=0.30)


def s_asia_comp_delta(df, i, sym):
    """ASIA_COMPRESSION + confirmação de delta (merge dos dois amarelos)."""
    r = df.iloc[i]
    if not (SESS_LONDON_TRADE[0] <= r.hh < SESS_LONDON_TRADE[1]):
        return None
    if pd.isna(r.asia_atr_ratio) or r.asia_atr_ratio > 0.45:
        return None
    if pd.isna(r.asia_hi) or pd.isna(r.dn):
        return None
    body, rng, uw, dw = _parts(r)
    if body < 0.55 * rng:
        return None
    p = df.iloc[i - 1]
    asia_rng = r.asia_hi - r.asia_lo
    if asia_rng <= 0:
        return None
    if r.Close > r.asia_hi and p.Close <= r.asia_hi and r.dn > 0.15 and r.Close > r.Open:
        entry = r.Close
        return {"dir": "COMPRA", "sl": r.asia_lo, "tp1": entry + 1.75 * asia_rng,
                "tp2": None, "partial": False}
    if r.Close < r.asia_lo and p.Close >= r.asia_lo and r.dn < -0.15 and r.Close < r.Open:
        entry = r.Close
        return {"dir": "VENDA", "sl": r.asia_hi, "tp1": entry - 1.75 * asia_rng,
                "tp2": None, "partial": False}
    return None


def s_asia_comp_tight(df, i, sym):
    """Compressão Ásia ≤0.35 ATR + corpo 65%."""
    r = df.iloc[i]
    if not (SESS_LONDON_TRADE[0] <= r.hh < SESS_LONDON_TRADE[1]):
        return None
    if pd.isna(r.asia_atr_ratio) or r.asia_atr_ratio > 0.35:
        return None
    body, rng, uw, dw = _parts(r)
    if body < 0.65 * rng:
        return None
    p = df.iloc[i - 1]
    asia_rng = r.asia_hi - r.asia_lo
    if asia_rng <= 0:
        return None
    if r.Close > r.asia_hi and p.Close <= r.asia_hi and r.Close > r.Open:
        return {"dir": "COMPRA", "sl": r.asia_lo, "tp1": r.Close + 2.0 * asia_rng,
                "tp2": None, "partial": False}
    if r.Close < r.asia_lo and p.Close >= r.asia_lo and r.Close < r.Open:
        return {"dir": "VENDA", "sl": r.asia_hi, "tp1": r.Close - 2.0 * asia_rng,
                "tp2": None, "partial": False}
    return None


def s_london_handoff_v2(df, i, sym):
    """LONDON_HANDOFF mais apertado: asia≤0.50 ATR, delta≥0.20, 1 trade/dia."""
    r = df.iloc[i]
    if not (SESS_LONDON_TRADE[0] <= r.hh < SESS_LONDON_TRADE[1]):
        return None
    if pd.isna(r.asia_atr_ratio) or r.asia_atr_ratio > 0.50:
        return None
    if pd.isna(r.dn) or abs(r.dn) < 0.20:
        return None
    p = df.iloc[i - 1]
    asia_rng = r.asia_hi - r.asia_lo
    if asia_rng <= 0:
        return None
    if r.Close > r.asia_hi and p.Close <= r.asia_hi and r.dn > 0.20:
        return {"dir": "COMPRA", "sl": r.asia_lo, "tp1": r.Close + 1.75 * asia_rng,
                "tp2": None, "partial": False}
    if r.Close < r.asia_lo and p.Close >= r.asia_lo and r.dn < -0.20:
        return {"dir": "VENDA", "sl": r.asia_hi, "tp1": r.Close - 1.75 * asia_rng,
                "tp2": None, "partial": False}
    return None


def s_ny_handoff_tight(df, i, sym):
    """NY handoff com Londres ≤0.45 ATR + delta 0.20."""
    r = df.iloc[i]
    if not (SESS_NY[0] <= r.hh < SESS_NY[1]):
        return None
    if pd.isna(r.lon_atr_ratio) or r.lon_atr_ratio > 0.45:
        return None
    if pd.isna(r.dn):
        return None
    p = df.iloc[i - 1]
    lon_rng = r.lon_hi - r.lon_lo
    if lon_rng <= 0:
        return None
    if r.Close > r.lon_hi and p.Close <= r.lon_hi and r.dn > 0.20:
        return {"dir": "COMPRA", "sl": r.lon_lo, "tp1": r.Close + 1.75 * lon_rng,
                "tp2": None, "partial": False}
    if r.Close < r.lon_lo and p.Close >= r.lon_lo and r.dn < -0.20:
        return {"dir": "VENDA", "sl": r.lon_hi, "tp1": r.Close - 1.75 * lon_rng,
                "tp2": None, "partial": False}
    return None


def s_va_reject_london(df, i, sym):
    """VALUE_AREA_REJECT só na janela Londres 09–11."""
    r = df.iloc[i]
    if not (SESS_LONDON_TRADE[0] <= r.hh < SESS_LONDON_TRADE[1]):
        return None
    if pd.isna(r.va_vwl) or pd.isna(r.va_dev) or pd.isna(r.atr):
        return None
    vah, val = r.va_vwl + r.va_dev, r.va_vwl - r.va_dev
    dn = r.dn if not pd.isna(r.dn) else 0
    body, rng, uw, dw = _parts(r)
    if r.High >= vah and r.Close < vah and uw >= 0.5 * rng and dn < -0.10:
        return _sig("VENDA", r.Close, r.High + 0.3 * r.atr, 1.5, 2.0, True)
    if r.Low <= val and r.Close > val and dw >= 0.5 * rng and dn > 0.10:
        return _sig("COMPRA", r.Close, r.Low - 0.3 * r.atr, 1.5, 2.0, True)
    return None


def s_round_fade_only(df, i, sym):
    """Só FADE em número redondo (delta fraco) — removeu o lado breakout que sangrou."""
    r = df.iloc[i]
    k = asset_key(sym)
    step = 50.0 if k == "XAUUSD" else (100.0 if k == "ETHUSD" else 1000.0)
    lvl = round(r.Close / step) * step
    if lvl <= 0 or abs(r.Close - lvl) > 0.20 * (r.atr or 1):
        return None
    dn = r.dn if not pd.isna(r.dn) else 0
    if abs(dn) >= 0.12:  # só quando fluxo está fraco
        return None
    body, rng, uw, dw = _parts(r)
    if r.High >= lvl and uw >= 0.45 * rng and r.Close < lvl:
        return _sig("VENDA", r.Close, r.High + 0.3 * (r.atr or 1), 1.5, 2.0, True)
    if r.Low <= lvl and dw >= 0.45 * rng and r.Close > lvl:
        return _sig("COMPRA", r.Close, r.Low - 0.3 * (r.atr or 1), 1.5, 2.0, True)
    return None


def s_squeeze_london(df, i, sym):
    """Bollinger squeeze → expansão na janela Londres, bias VWAP."""
    r = df.iloc[i]
    if not (SESS_LONDON_TRADE[0] <= r.hh < SESS_LONDON_TRADE[1]):
        return None
    p = df.iloc[i - 1]
    if pd.isna(p.bbw) or pd.isna(p.bbw_q10) or p.bbw > p.bbw_q10:
        return None
    if r.Volume < 1.4 * (r.vol20b or 0):
        return None
    if r.Close > r.vwap and r.Close > r.bb_up:
        return _sig("COMPRA", r.Close, r.bb_dn, 2.0, 3.0, True)
    if r.Close < r.vwap and r.Close < r.bb_dn:
        return _sig("VENDA", r.Close, r.bb_up, 2.0, 3.0, True)
    return None


def s_pdh_pdl_delta(df, i, sym):
    """Toque PDH/PDL do dia anterior + delta contrário + rejeição (09–12 ou 15–18)."""
    r = df.iloc[i]
    ok = (9 <= r.hh < 12) or (15 <= r.hh < 18)
    if not ok:
        return None
    if pd.isna(r.prev_day_hi) or pd.isna(r.prev_day_lo) or pd.isna(r.dn):
        return None
    body, rng, uw, dw = _parts(r)
    if r.High >= r.prev_day_hi and r.Close < r.prev_day_hi and uw >= 0.45 * rng and r.dn < -0.10:
        return _sig("VENDA", r.Close, r.High + 0.3 * (r.atr or 1), 1.5, 2.0, True)
    if r.Low <= r.prev_day_lo and r.Close > r.prev_day_lo and dw >= 0.45 * rng and r.dn > 0.10:
        return _sig("COMPRA", r.Close, r.Low - 0.3 * (r.atr or 1), 1.5, 2.0, True)
    return None


def s_eth_of_london(df, i, sym):
    return s_of_london_only(df, i, sym)


def s_eth_of_london_strict(df, i, sym):
    return s_of_london_strict(df, i, sym)


def s_xau_of_m5_london(df, i, sym):
    """Mesma lógica OF Londres — pensado para rodar em M5 (tf do live)."""
    return s_of_london_only(df, i, sym)


def s_xau_of_m5_london_strict(df, i, sym):
    return s_of_london_strict(df, i, sym)


def s_xau_of_m5_ny(df, i, sym):
    return s_of_ny_only(df, i, sym)


# ════════════════════════════════════════════════════════════════════════════
# REGISTRO
# ════════════════════════════════════════════════════════════════════════════

STRATS = {
    "b3": {
        "OVN_ATR": s_ovn_atr,
        "OVN_TO_PDC": s_ovn_to_pdc,
        "OVN_QUIET_REG": s_ovn_quiet_reg,
        "GAP_FADE_0930": s_gap_fade_0930,
        "IB_BREAK_VOL": s_ib_break_vol,
        "IB_FAIL_FADE": s_ib_fail_fade,
        "FAILED_FADE_ORB_V2": s_failed_fade_orb_v2,
        "VWAP_RECLAIM": s_vwap_reclaim,
        "STOP_RUN_FILTERED": s_stop_run_filtered,
        "WDO_CORREL_V2": s_wdo_correl_v2,
        "WIN_TREND_PB_H4": s_win_trend_pb_h4,
        "ORB_DIR_VOL": s_orb_dir_vol,
        "AFTERNOON_EXT_FADE": s_afternoon_ext_fade,
    },
    "crypto": {
        "OF_LONDON_ONLY": s_of_london_only,
        "OF_LONDON_STRICT": s_of_london_strict,
        "OF_NY_ONLY": s_of_ny_only,
        "OF_TUE_THU": s_of_tue_thu,
        "OF_LOW_VOL": s_of_low_vol,
        "ASIA_COMP_DELTA": s_asia_comp_delta,
        "ASIA_COMP_TIGHT": s_asia_comp_tight,
        "LONDON_HANDOFF_V2": s_london_handoff_v2,
        "NY_HANDOFF_TIGHT": s_ny_handoff_tight,
        "VA_REJECT_LONDON": s_va_reject_london,
        "ROUND_FADE_ONLY": s_round_fade_only,
        "SQUEEZE_LONDON": s_squeeze_london,
        "PDH_PDL_DELTA": s_pdh_pdl_delta,
        "ETH_OF_LONDON": s_eth_of_london,
        "ETH_OF_LONDON_STRICT": s_eth_of_london_strict,
        # M5 XAU (tf do ORDER_FLOW ao vivo)
        "XAU_OF_M5_LONDON": s_xau_of_m5_london,
        "XAU_OF_M5_LONDON_STRICT": s_xau_of_m5_london_strict,
        "XAU_OF_M5_NY": s_xau_of_m5_ny,
    },
}

STRAT_SYM = {
    "OVN_ATR": "WIN", "OVN_TO_PDC": "WIN", "OVN_QUIET_REG": "WIN",
    "GAP_FADE_0930": "WIN", "IB_BREAK_VOL": "WIN", "IB_FAIL_FADE": "WIN",
    "FAILED_FADE_ORB_V2": "WIN", "VWAP_RECLAIM": "WIN", "STOP_RUN_FILTERED": "WIN",
    "WDO_CORREL_V2": "WDO", "WIN_TREND_PB_H4": "WIN", "ORB_DIR_VOL": "WIN",
    "AFTERNOON_EXT_FADE": "WIN",
    "OF_LONDON_ONLY": "XAUUSD", "OF_LONDON_STRICT": "XAUUSD",
    "OF_NY_ONLY": "XAUUSD", "OF_TUE_THU": "XAUUSD", "OF_LOW_VOL": "XAUUSD",
    "ASIA_COMP_DELTA": "XAUUSD", "ASIA_COMP_TIGHT": "XAUUSD",
    "LONDON_HANDOFF_V2": "XAUUSD", "NY_HANDOFF_TIGHT": "XAUUSD",
    "VA_REJECT_LONDON": "XAUUSD", "ROUND_FADE_ONLY": "XAUUSD",
    "SQUEEZE_LONDON": "XAUUSD", "PDH_PDL_DELTA": "XAUUSD",
    "ETH_OF_LONDON": "ETHUSD", "ETH_OF_LONDON_STRICT": "ETHUSD",
    "XAU_OF_M5_LONDON": "XAUUSD", "XAU_OF_M5_LONDON_STRICT": "XAUUSD",
    "XAU_OF_M5_NY": "XAUUSD",
}

# timeframe por setup (default 15)
STRAT_TF = {
    "XAU_OF_M5_LONDON": 5,
    "XAU_OF_M5_LONDON_STRICT": 5,
    "XAU_OF_M5_NY": 5,
}

ONE_PER_DAY = {
    "OVN_ATR", "OVN_TO_PDC", "OVN_QUIET_REG", "GAP_FADE_0930",
    "IB_BREAK_VOL", "FAILED_FADE_ORB_V2", "ORB_DIR_VOL", "AFTERNOON_EXT_FADE",
    "ASIA_COMP_DELTA", "ASIA_COMP_TIGHT", "LONDON_HANDOFF_V2", "NY_HANDOFF_TIGHT",
    "SQUEEZE_LONDON",
}


def _simulate(df, symbol, strat_fn, name, tf=15):
    is_b3 = asset_key(symbol) in ("WIN", "WDO")
    max_bars = 288 if tf == 5 else 96
    one_per_day = name in ONE_PER_DAY
    trades, pos = [], None
    last_day_traded = None
    n = len(df)
    start = 600 if tf == 5 else 460
    for i in range(start, n):
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--grupo", choices=["b3", "crypto"], default="b3")
    ap.add_argument("--bars", type=int, default=300000)
    args = ap.parse_args()

    os.environ["KIMI_GROUP"] = args.grupo
    os.makedirs("logs", exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    logf = open(f"logs/backtest_setups_novos_v2_{args.grupo}_{stamp}.txt", "w", encoding="utf-8")

    def out(s):
        print(s)
        logf.write(s + "\n")
        logf.flush()

    out(f"{'#' * 74}\n# SETUPS NOVOS V2 (variações) — {args.grupo} · "
        f"{datetime.now():%d/%m %H:%M}\n# NÃO altera motores ao vivo\n{'#' * 74}")

    strat_map = STRATS[args.grupo]
    # coleta (sym, tf) necessários
    needs = {}
    for name in strat_map:
        k = STRAT_SYM[name]
        tf = STRAT_TF.get(name, 15)
        needs.setdefault((k, tf), []).append(name)

    if args.grupo == "b3":
        needs.setdefault(("WIN", 15), [])
    if args.grupo == "crypto":
        needs.setdefault(("BTCUSD", 15), [])

    sym_full = {"WIN": "WIN$D", "WDO": "WDO$D", "XAUUSD": "XAUUSD",
                "ETHUSD": "ETHUSD", "BTCUSD": "BTCUSD"}
    ranking = []

    _mt5c = None
    _cp_fetch = None
    if args.grupo == "crypto":
        from backtest_crypto_pro import mt5_connect, fetch as _cp_fetch
        _mt5c = mt5_connect()

    def _get(k, tf):
        if args.grupo == "b3":
            return _b3_fetch(sym_full[k], tf, args.bars)
        raw = _cp_fetch(_mt5c, sym_full[k], tf, args.bars)
        return add_indicators(raw) if raw is not None else None

    raw_dfs = {}
    for (k, tf) in sorted(needs.keys()):
        t0 = time.time()
        df = _get(k, tf)
        if df is None or len(df) < 3000:
            out(f"\n!! {k} M{tf}: sem histórico suficiente")
            continue
        raw_dfs[(k, tf)] = df
        bars_day = 26 if k in ("WIN", "WDO") else (288 if tf == 5 else 96)
        years_den = 252 if k in ("WIN", "WDO") else 365
        anos = len(df) / bars_day / years_den
        out(f"\n{'=' * 74}\n  {k} M{tf}: {len(df)} candles (~{anos:.1f} anos)  "
            f"[{df.index[0].date()} → {df.index[-1].date()}]  ({time.time() - t0:.0f}s)\n{'=' * 74}")

    ctx15 = {}
    if ("WIN", 15) in raw_dfs:
        ctx15["WIN"] = raw_dfs[("WIN", 15)]
    if ("BTCUSD", 15) in raw_dfs:
        ctx15["BTCUSD"] = raw_dfs[("BTCUSD", 15)]

    dfs = {}
    for key, df in raw_dfs.items():
        k, tf = key
        # contexto cruzado só faz sentido no mesmo TF
        ctx = ctx15 if tf == 15 else None
        dfs[key] = add_extra_v2(df.copy(), sym_full[k], ctx=ctx)

    for name, fn in strat_map.items():
        k = STRAT_SYM[name]
        tf = STRAT_TF.get(name, 15)
        key = (k, tf)
        if key not in dfs:
            out(f"\n  ▸ {name} ({k} M{tf}): sem dados")
            continue
        df = dfs[key]
        symf = sym_full[k]
        t0 = time.time()
        t = _simulate(df, symf, fn, name, tf=tf)
        s = stats(t)
        cons = consistency(t)
        out(f"\n  ▸ {name}  ({k} M{tf})   [{time.time() - t0:.0f}s]")
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
            t.to_csv(f"logs/novos_v2_{k}_M{tf}_{name}.csv", index=False)
        v = verdict(s, cons, s_out)
        out(f"      VEREDITO: {v}")
        ranking.append({"setup": name, "sym": k, "tf": tf, **s, "cons%": cons, "veredito": v})

    out(f"\n{'#' * 74}\n  RANKING (líquida)\n{'#' * 74}")
    rk = pd.DataFrame(ranking)
    if not rk.empty:
        rk = rk.sort_values("net", ascending=False)
        for _, r in rk.iterrows():
            if r.get("n", 0) == 0:
                out(f"  {r.sym:<7} M{int(r.tf)} {r.setup:<24} sem trades")
                continue
            out(f"  {r.sym:<7} M{int(r.tf)} {r.setup:<24} n={int(r.n):<5} net {r.net:+.3f} R  "
                f"PF {r.PF:<5} cons {r['cons%']}%  {r.veredito}")
    out(f"\n# Critério GO: net ≥ +0,10 R · PF ≥ 1,25 · consist. ≥ 55% · OOS segura")
    out(f"# FIM — logs/backtest_setups_novos_v2_{args.grupo}_{stamp}.txt")
    logf.close()


if __name__ == "__main__":
    main()
