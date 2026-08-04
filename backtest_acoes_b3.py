"""
backtest_acoes_b3.py — MEGA-BACKTEST de AÇÕES B3: VALE3, PETR4, ITUB4, ABEV3.

Objetivo: decidir se essas ações entram nos nossos motores. Testa TUDO o que já
validamos/reprovamos nos outros ativos, na mesma régua:

  A) BATERIA DE SETUPS (M5, M15 e H1):
     - os 12 da Kimi rodada 1 (ORB, VWAP_PB, MA_CROSS, BOLLINGER, PIVOT,
       TREND_PB, SCALP, ICT_SWEEP, ORDER_FLOW, MARKET_PROFILE, FVG, LIQ_SWEEP)
     - setups de abertura adaptados a ações (pregão abre 10:00):
       GAP_FADE_AB (conceito do v7.4.1), ORB45, ORB_DIR, PRE_ORB, FADE_IMPULSO,
       SEGUNDA_TENT, SPIKE_FADE, REVERSAO_TARDE
     - genéricos: SQUEEZE_EXP (squeeze Bollinger), ROUND_LEVEL (nível redondo),
       H1_PULLBACK (pullback em tendência H1)
  B) MOTORES REAIS (M15): as regras EXATAS da produção —
     - V6_FIXED   = Monitor MT5 (generate_signal, saída fixa) + custos
     - V6_MANAGED = v6 com exits geridos
     - V7_MANAGED = motor v7 (generate_signal_v7)

Custos de AÇÕES (diferente de futuros): emolumentos+liquidação day trade
~0,055% do financeiro por round-trip (corretagem zero) + spread de 1 tick
(R$0,01) na entrada a mercado + 1 tick na saída por stop.

Validação: walk-forward, consistência por bimestre, IN×OUT-OF-SAMPLE (70/30)
e critério GO (líq ≥ +0,10 R · PF ≥ 1,25 · cons ≥ 55% · OOS segura).

⚠️ Rodar com o MT5 da XP aberto (as 4 ações no Market Watch).
Uso (um comando faz tudo — DEMORA HORAS, deixe rodando):
    python backtest_acoes_b3.py
Opções: --symbol VALE3 · --skip-engines (pula v6/v7, só a bateria, ~10x mais rápido)
Saída: logs/backtest_acoes_<stamp>.txt + CSVs por combinação
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

from backtest_kimi_real import fetch, add_indicators, STRATS as KIMI1

SYMBOLS = ["VALE3", "PETR4", "ITUB4", "ABEV3"]
TFS_BATTERY = [5, 15, 60]
OOS_FRAC = 0.30

# ── Custos de ações ─────────────────────────────────────────────────────────
TICK = 0.01
FEE_PCT_RT = 0.00055          # 0,055% do financeiro por round-trip (day trade, corretagem 0)


def stock_cost_pts(price, stopped=True, entry_limit=False):
    cost = price * FEE_PCT_RT
    if not entry_limit:
        cost += TICK
    if stopped:
        cost += TICK
    return cost


# ════════════════════════════════════════════════════════════════════════════
# COLUNAS EXTRAS (referências de pregão por barra-índice — robusto a horário)
# ════════════════════════════════════════════════════════════════════════════

def bars_per_day(tf):
    return {5: 84, 15: 28, 60: 7}.get(tf, 28)


def add_extra(df, tf):
    h, l, c, o, v = df.High, df.Low, df.Close, df.Open, df.Volume
    hh = df.index.hour + df.index.minute / 60.0
    df["hh"] = hh
    day = df["day"]
    df["bar_n"] = df.groupby(day).cumcount()            # índice da barra no dia (0 = abertura)
    df["day_open"] = df.groupby(day)["Open"].transform("first")
    df["day_hi"] = df.groupby(day)["High"].cummax()
    df["day_lo"] = df.groupby(day)["Low"].cummin()
    df["day_hi_prev"] = df.groupby(day)["High"].cummax().groupby(day).shift(1)
    df["day_lo_prev"] = df.groupby(day)["Low"].cummin().groupby(day).shift(1)
    day_close = df.groupby(day)["Close"].last()
    df["prev_close"] = day.map(day_close.shift(1))
    df["gap_pct"] = (df["day_open"] - df["prev_close"]) / df["prev_close"].replace(0, np.nan) * 100
    df["day_first_high"] = df.groupby(day)["High"].transform("first")
    df["day_first_low"] = df.groupby(day)["Low"].transform("first")
    df["faded_dn_once"] = (df["Close"] < df["day_open"]).astype(int).groupby(day).cummax()
    df["faded_up_once"] = (df["Close"] > df["day_open"]).astype(int).groupby(day).cummax()
    # range dos primeiros 45 min (ORB): nº de barras conforme o TF
    n45 = max(1, int(45 / tf))
    in_orb = df["bar_n"] < n45
    df["orb_hi"] = df.High.where(in_orb).groupby(day).transform("max")
    df["orb_lo"] = df.Low.where(in_orb).groupby(day).transform("min")
    df["n45"] = n45
    df["vol8"] = v.rolling(8).mean()
    df["vol20b"] = v.rolling(20).mean()
    bbw = (df["bb_up"] - df["bb_dn"]) / df["bb_mid"].replace(0, np.nan)
    df["bbw"] = bbw
    df["bbw_q10"] = bbw.rolling(100).quantile(0.10)
    df["atr_ma"] = df["atr"].rolling(bars_per_day(tf) * 10).mean()
    fb = df[df["bar_n"] == 0]
    fb_ma = pd.Series(fb.Volume.values, index=fb["day"].values).rolling(20).mean()
    df["first_bar_vol_ma20"] = day.map(fb_ma)
    # contexto H1 (para H1_PULLBACK quando tf < 60)
    if tf < 60:
        h1 = df[["Open", "High", "Low", "Close"]].resample("1h").agg(
            {"Open": "first", "High": "max", "Low": "min", "Close": "last"}).dropna()
        h1["ema20"] = h1.Close.ewm(span=20, adjust=False).mean()
        h1["ema50"] = h1.Close.ewm(span=50, adjust=False).mean()
        d_ = h1.Close.diff()
        up = d_.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
        dn = (-d_.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
        h1["rsi"] = 100 - 100/(1 + up/dn.replace(0, 1e-9))
        df["h1_ema20"] = h1["ema20"].reindex(df.index, method="ffill")
        df["h1_ema50"] = h1["ema50"].reindex(df.index, method="ffill")
        df["h1_rsi"] = h1["rsi"].reindex(df.index, method="ffill")
    else:
        df["h1_ema20"] = df["ema20"]; df["h1_ema50"] = df["ema50"]; df["h1_rsi"] = df["rsi"]
    return df


# ── helpers de candle ───────────────────────────────────────────────────────

def _parts(r):
    body = abs(r.Close - r.Open); rng = max(r.High - r.Low, 1e-9)
    return body, rng, r.High - max(r.Close, r.Open), min(r.Close, r.Open) - r.Low


def rev_up(df, i):
    r, p = df.iloc[i], df.iloc[i-1]
    b, rng, uw, dw = _parts(r)
    return (dw >= 0.6*rng and b <= 0.4*rng) or \
           (r.Close > r.Open and p.Close < p.Open and r.Close >= p.Open and r.Open <= p.Close)


def rev_dn(df, i):
    r, p = df.iloc[i], df.iloc[i-1]
    b, rng, uw, dw = _parts(r)
    return (uw >= 0.6*rng and b <= 0.4*rng) or \
           (r.Close < r.Open and p.Close > p.Open and r.Close <= p.Open and r.Open >= p.Close)


# ════════════════════════════════════════════════════════════════════════════
# SETUPS DE ABERTURA/INTRADAY adaptados a AÇÕES  → dict ou None
# ════════════════════════════════════════════════════════════════════════════

def s_gap_fade_ab(df, i, sym):        # conceito GAP_FADE do v7.4.1 (na 1ª barra do pregão)
    r = df.iloc[i]
    if int(r.bar_n) != 0 or pd.isna(r.gap_pct) or pd.isna(r.prev_close) or r.atr <= 0:
        return None
    if abs(r.gap_pct) < 0.3:
        return None
    if r.gap_pct > 0:                 # gap up → fade short em direção ao fech. anterior
        entry = r.Close; sl = entry + 1.5*r.atr; tp = r.prev_close
        if tp < entry:
            return {"dir": "VENDA", "sl": sl, "tp1": tp, "tp2": None, "partial": False}
    else:
        entry = r.Close; sl = entry - 1.5*r.atr; tp = r.prev_close
        if tp > entry:
            return {"dir": "COMPRA", "sl": sl, "tp1": tp, "tp2": None, "partial": False}
    return None


def s_orb45(df, i, sym):              # ORB clássico (rompe o range dos 1os 45 min)
    r = df.iloc[i]
    n45 = int(r.n45)
    if not (n45 <= int(r.bar_n) <= n45 + int(90/max(5, (df.index[1]-df.index[0]).seconds//60 or 15))):
        pass
    if int(r.bar_n) < n45 or int(r.bar_n) > n45 + 8:
        return None
    if pd.isna(r.orb_hi) or pd.isna(r.orb_lo):
        return None
    p = df.iloc[i-1]
    if r.Close > r.orb_hi and p.Close <= r.orb_hi:
        entry = r.Close; sl = r.orb_lo; risk = entry - sl
        if risk > 0:
            return {"dir": "COMPRA", "sl": sl, "tp1": entry+1.5*risk, "tp2": entry+3.0*risk, "partial": True}
    if r.Close < r.orb_lo and p.Close >= r.orb_lo:
        entry = r.Close; sl = r.orb_hi; risk = sl - entry
        if risk > 0:
            return {"dir": "VENDA", "sl": sl, "tp1": entry-1.5*risk, "tp2": entry-3.0*risk, "partial": True}
    return None


def s_orb_dir(df, i, sym):            # ORB só no lado do gap (rodada 3)
    r = df.iloc[i]
    sig = s_orb45(df, i, sym)
    if not sig or pd.isna(r.gap_pct) or abs(r.gap_pct) < 0.2:
        return None
    if sig["dir"] == "COMPRA" and r.gap_pct > 0 and r.orb_lo > r.day_open:
        return sig
    if sig["dir"] == "VENDA" and r.gap_pct < 0 and r.orb_hi < r.day_open:
        return sig
    return None


def s_pre_orb(df, i, sym):            # rompe o range das 2 primeiras barras na 3ª (gap pequeno)
    r = df.iloc[i]
    if int(r.bar_n) != 2 or i < 2:
        return None
    if pd.isna(r.gap_pct) or abs(r.gap_pct) >= 0.3:
        return None
    b1, b2 = df.iloc[i-1], df.iloc[i-2]
    if r.Volume < 1.3 * ((b1.Volume + b2.Volume) / 2):
        return None
    hi2, lo2 = max(b1.High, b2.High), min(b1.Low, b2.Low)
    if r.Close > hi2:
        entry = r.Close; sl = min(lo2, r.Low); risk = entry - sl
        if risk > 0:
            return {"dir": "COMPRA", "sl": sl, "tp1": entry+1.5*risk, "tp2": entry+2.5*risk, "partial": True}
    if r.Close < lo2:
        entry = r.Close; sl = max(hi2, r.High); risk = sl - entry
        if risk > 0:
            return {"dir": "VENDA", "sl": sl, "tp1": entry-1.5*risk, "tp2": entry-2.5*risk, "partial": True}
    return None


def s_fade_impulso(df, i, sym):       # fade da extensão do gap médio (2ª barra)
    r = df.iloc[i]
    if int(r.bar_n) != 1:
        return None
    g = r.gap_pct
    if pd.isna(g) or not (0.3 <= abs(g) <= 0.8) or pd.isna(r.prev_close):
        return None
    gap_px = abs(r.day_open - r.prev_close)
    if g > 0:
        impulse = r.day_hi - r.prev_close
        if impulse <= gap_px or r.Close >= r.Open:
            return None
        entry = r.Close; sl = r.day_hi + TICK; risk = sl - entry
        if risk > 0:
            return {"dir": "VENDA", "sl": sl, "tp1": entry-1.5*risk, "tp2": None, "partial": False}
    else:
        impulse = r.prev_close - r.day_lo
        if impulse <= gap_px or r.Close <= r.Open:
            return None
        entry = r.Close; sl = r.day_lo - TICK; risk = entry - sl
        if risk > 0:
            return {"dir": "COMPRA", "sl": sl, "tp1": entry+1.5*risk, "tp2": None, "partial": False}
    return None


def s_segunda_tent(df, i, sym):       # a favor do gap quando o fade falhou (barras 2-4)
    r = df.iloc[i]
    if not (1 <= int(r.bar_n) <= 3):
        return None
    g = r.gap_pct
    if pd.isna(g) or abs(g) < 0.3 or pd.isna(r.prev_close):
        return None
    if g > 0:
        if int(r.faded_dn_once) != 1 or r.day_lo <= r.prev_close:
            return None
        if r.Close <= r.day_first_high or r.Volume < (r.vol8 or 0):
            return None
        entry = r.Close; sl = r.day_lo; risk = entry - sl
        if risk > 0:
            return {"dir": "COMPRA", "sl": sl, "tp1": entry+2.0*risk, "tp2": None, "partial": False}
    else:
        if int(r.faded_up_once) != 1 or r.day_hi >= r.prev_close:
            return None
        if r.Close >= r.day_first_low or r.Volume < (r.vol8 or 0):
            return None
        entry = r.Close; sl = r.day_hi; risk = sl - entry
        if risk > 0:
            return {"dir": "VENDA", "sl": sl, "tp1": entry-2.0*risk, "tp2": None, "partial": False}
    return None


def s_spike_fade(df, i, sym):         # gap grande + volume anômalo → a favor do gap
    r = df.iloc[i]
    if int(r.bar_n) != 0 or pd.isna(r.gap_pct) or pd.isna(r.first_bar_vol_ma20):
        return None
    if abs(r.gap_pct) < 1.0 or r.first_bar_vol_ma20 <= 0 or r.Volume < 2.0*r.first_bar_vol_ma20:
        return None
    if r.gap_pct > 0 and r.Close > r.Open:
        entry = r.Close; sl = r.prev_close; risk = entry - sl
        if risk > 0:
            return {"dir": "COMPRA", "sl": sl, "tp1": entry+2*risk, "tp2": None, "partial": False}
    if r.gap_pct < 0 and r.Close < r.Open:
        entry = r.Close; sl = r.prev_close; risk = sl - entry
        if risk > 0:
            return {"dir": "VENDA", "sl": sl, "tp1": entry-2*risk, "tp2": None, "partial": False}
    return None


def s_rev_tarde(df, i, sym):          # reversão no extremo do dia (14:00–16:00)
    r = df.iloc[i]
    if not (14.0 <= r.hh < 16.0):
        return None
    if pd.isna(r.atr_ma) or r.atr <= 0 or (r.atr / r.atr_ma) >= 1.5:
        return None
    if r.Volume < 1.2 * (r.vol8 or 0):
        return None
    subiu = r.Close > r.day_open
    if subiu and not pd.isna(r.day_hi_prev) and r.High >= r.day_hi_prev and rev_dn(df, i):
        entry = r.Close; sl = entry + 1.5*r.atr; risk = sl - entry
        return {"dir": "VENDA", "sl": sl, "tp1": entry-1.5*risk, "tp2": entry-2.5*risk, "partial": True}
    if (not subiu) and not pd.isna(r.day_lo_prev) and r.Low <= r.day_lo_prev and rev_up(df, i):
        entry = r.Close; sl = entry - 1.5*r.atr; risk = entry - sl
        return {"dir": "COMPRA", "sl": sl, "tp1": entry+1.5*risk, "tp2": entry+2.5*risk, "partial": True}
    return None


def s_squeeze_exp(df, i, sym):        # squeeze Bollinger → expansão com bias VWAP
    r = df.iloc[i]
    if pd.isna(r.bbw) or pd.isna(r.bbw_q10) or pd.isna(r.vwap):
        return None
    prev = df.iloc[i-1]
    if not (prev.bbw <= prev.bbw_q10) or r.Volume < 1.5*(r.vol20b or 0):
        return None
    if r.Close > r.vwap and r.Close > r.bb_up:
        entry = r.Close; sl = r.bb_dn; risk = entry - sl
        if risk > 0:
            return {"dir": "COMPRA", "sl": sl, "tp1": entry+2.5*risk, "tp2": None, "partial": False}
    if r.Close < r.vwap and r.Close < r.bb_dn:
        entry = r.Close; sl = r.bb_up; risk = sl - entry
        if risk > 0:
            return {"dir": "VENDA", "sl": sl, "tp1": entry-2.5*risk, "tp2": None, "partial": False}
    return None


def _round_step(price):
    if price < 20: return 0.50
    if price < 100: return 1.00
    return 5.00


def s_round_level(df, i, sym):        # rejeição (wick) em nível redondo
    r, p = df.iloc[i], df.iloc[i-1]
    step = _round_step(float(r.Close))
    lvl = round(r.Close / step) * step
    if lvl <= 0 or abs(r.Close - lvl) / lvl > 0.003:
        return None
    b, rng, uw, dw = _parts(p)
    if dw >= 0.6*rng and r.Close > r.Open and r.Close > p.High and r.Close > r.ema20:
        entry = r.Close; sl = p.Low; risk = entry - sl
        if risk > 0:
            return {"dir": "COMPRA", "sl": sl, "tp1": entry+2*risk, "tp2": None, "partial": False}
    if uw >= 0.6*rng and r.Close < r.Open and r.Close < p.Low and r.Close < r.ema20:
        entry = r.Close; sl = p.High; risk = sl - entry
        if risk > 0:
            return {"dir": "VENDA", "sl": sl, "tp1": entry-2*risk, "tp2": None, "partial": False}
    return None


def s_h1_pullback(df, i, sym):        # pullback à EMA20 H1 em tendência H1
    r = df.iloc[i]
    if pd.isna(r.h1_ema20) or pd.isna(r.h1_ema50) or r.atr <= 0:
        return None
    up = r.h1_ema20 > r.h1_ema50; dn = r.h1_ema20 < r.h1_ema50
    near = abs(r.Close - r.h1_ema20) < 2*r.atr
    if up and near and 40 <= (r.h1_rsi or 0) <= 70 and rev_up(df, i):
        entry = r.Close; sl = min(df.iloc[i-1].Low, r.Low); risk = entry - sl
        if risk > 0:
            return {"dir": "COMPRA", "sl": sl, "tp1": entry+1.5*risk, "tp2": entry+2.0*risk, "partial": True}
    if dn and near and 30 <= (r.h1_rsi or 0) <= 60 and rev_dn(df, i):
        entry = r.Close; sl = max(df.iloc[i-1].High, r.High); risk = sl - entry
        if risk > 0:
            return {"dir": "VENDA", "sl": sl, "tp1": entry-1.5*risk, "tp2": entry-2.0*risk, "partial": True}
    return None


# adaptador: setups da Kimi rodada 1 têm assinatura (df, i, tick) → tupla
def _wrap_kimi1(fn):
    def _f(df, i, sym):
        sig = fn(df, i, TICK)
        if not sig:
            return None
        d, sl, tp1, tp2 = sig
        return {"dir": d, "sl": sl, "tp1": tp1, "tp2": tp2, "partial": bool(tp2)}
    return _f


# nome → (fn, TFs onde roda, 1-trade-por-dia?)
BATTERY = {}
for _n, _fn in KIMI1.items():
    BATTERY[_n] = (_wrap_kimi1(_fn), {5, 15, 60}, False)
BATTERY.update({
    "GAP_FADE_AB":  (s_gap_fade_ab,  {5, 15}, True),
    "ORB45":        (s_orb45,        {5, 15}, True),
    "ORB_DIR":      (s_orb_dir,      {5, 15}, True),
    "PRE_ORB":      (s_pre_orb,      {15},    True),
    "FADE_IMPULSO": (s_fade_impulso, {15},    True),
    "SEGUNDA_TENT": (s_segunda_tent, {15},    True),
    "SPIKE_FADE":   (s_spike_fade,   {15},    True),
    "REVERSAO_TARDE": (s_rev_tarde,  {15},    False),
    "SQUEEZE_EXP":  (s_squeeze_exp,  {15, 60}, False),
    "ROUND_LEVEL":  (s_round_level,  {15},    False),
    "H1_PULLBACK":  (s_h1_pullback,  {15},    False),
})


# ════════════════════════════════════════════════════════════════════════════
# SIMULADOR (ações: EOD sempre; custos percentuais; parcial/BE quando pedido)
# ════════════════════════════════════════════════════════════════════════════

def simulate(df, sym, fn, one_per_day):
    trades, pos = [], None
    last_day = None
    n = len(df)
    for i in range(260, n):
        if pos:
            r = df.iloc[i]; buy = pos["dir"] == "COMPRA"
            exit_R = None; stopped = True
            if (r.Low <= pos["sl"]) if buy else (r.High >= pos["sl"]):
                exit_R = ((pos["sl"]-pos["entry"]) / pos["risk"]) if buy else ((pos["entry"]-pos["sl"]) / pos["risk"])
            elif pos["tp2"] and ((r.High >= pos["tp2"]) if buy else (r.Low <= pos["tp2"])):
                exit_R = abs(pos["tp2"]-pos["entry"]) / pos["risk"]; stopped = False
            elif (not pos["tp2"]) and ((r.High >= pos["tp1"]) if buy else (r.Low <= pos["tp1"])):
                exit_R = abs(pos["tp1"]-pos["entry"]) / pos["risk"]; stopped = False
            elif pos["tp2"] and not pos["tp1_done"] and ((r.High >= pos["tp1"]) if buy else (r.Low <= pos["tp1"])):
                pos["tp1_done"] = True
                pos["realized"] = 0.5*abs(pos["tp1"]-pos["entry"])/pos["risk"] if pos["partial"] else 0.0
                pos["sl"] = pos["entry"]
            if exit_R is None and r["day"] != pos["day"]:       # zeragem EOD (day trade)
                px = df.iloc[i-1].Close
                exit_R = ((px-pos["entry"]) / pos["risk"]) if buy else ((pos["entry"]-px) / pos["risk"])
            if exit_R is not None:
                rem = 0.5 if (pos["partial"] and pos["tp1_done"]) else 1.0
                gross = pos["realized"] + rem*exit_R
                cost = stock_cost_pts(pos["entry"], stopped) / pos["risk"]
                trades.append({"ts": pos["ts"], "dir": pos["dir"], "gross_R": round(gross, 3),
                               "cost_R": round(cost, 3), "net_R": round(gross-cost, 3)})
                pos = None
            continue
        if one_per_day and df.iloc[i]["day"] == last_day:
            continue
        sig = fn(df, i, sym)
        if not sig:
            continue
        entry = float(df.iloc[i].Close)
        risk = abs(entry - sig["sl"])
        atr_i = float(df.iloc[i].atr or 0)
        # risco mínimo: 0,15×ATR e 6 ticks (senão o custo % engole o trade)
        if risk <= 0 or risk < 0.15*max(atr_i, 1e-9) or risk < 6*TICK:
            continue
        pos = {"ts": df.index[i], "day": df.iloc[i]["day"], "dir": sig["dir"], "entry": entry,
               "sl": sig["sl"], "tp1": sig["tp1"], "tp2": sig.get("tp2"),
               "partial": sig.get("partial", False), "tp1_done": False, "realized": 0.0, "risk": risk}
        last_day = df.iloc[i]["day"]
    return pd.DataFrame(trades)


# ── Estatística / veredito ──────────────────────────────────────────────────

def stats(t):
    if t is None or t.empty:
        return {"n": 0}
    w = t[t.net_R > 0]; l = t[t.net_R <= 0]
    gw, gl = w.net_R.sum(), -l.net_R.sum()
    eq = t.net_R.cumsum(); dd = (eq - eq.cummax()).min()
    return {"n": len(t), "win": round(len(w)/len(t)*100, 1),
            "gross": round(t.gross_R.mean(), 3), "cost": round(t.cost_R.mean(), 3),
            "net": round(t.net_R.mean(), 3),
            "PF": round(gw/gl, 2) if gl > 0 else float("inf"),
            "tot": round(t.net_R.sum(), 1), "dd": round(dd, 1)}


def consistency(t):
    if t is None or t.empty:
        return 0
    t2 = t.copy(); t2["p"] = pd.to_datetime(t2["ts"]).dt.to_period("2M").astype(str)
    per = t2.groupby("p")["net_R"].mean()
    return round((per > 0).sum()/len(per)*100) if len(per) else 0


def oos_split(t):
    if t is None or t.empty:
        return {"n": 0}, {"n": 0}
    t = t.sort_values("ts").reset_index(drop=True)
    cut = int(len(t)*(1-OOS_FRAC))
    return stats(t.iloc[:cut]), stats(t.iloc[cut:])


def verdict(s, cons, s_out):
    if s.get("n", 0) < 40:
        return "⚪ amostra fraca"
    oos_ok = s_out.get("n", 0) >= 15 and s_out["net"] >= 0.08 and s_out["PF"] >= 1.15
    if s["net"] >= 0.10 and s["PF"] >= 1.25 and cons >= 55 and oos_ok:
        return "🟢 GO"
    if s["net"] > 0.05 and s["PF"] >= 1.15:
        return "🟡 promissor" + ("" if oos_ok else " (OOS fraco)")
    return "🔴"


# ── Motores reais v6/v7 (importa o backtest_pro e injeta custos de ação) ───

def align_session(df15):
    """
    Desloca o relógio para a sessão do WIN (abertura → 09:00).

    O motor v7 tem janelas de relógio FIXAS do WIN (GAP_FADE 09:00–09:30,
    ORB até 10:45). Ações abrem 10:00, então nada dispara. Deslocando a série
    para abrir às 09:00, o "tempo desde a abertura" fica idêntico ao do WIN e
    o v7 pode ser testado de verdade — sem alterar uma linha da produção.
    Retorna (df_deslocado, delta_minutos).
    """
    firsts = pd.Series([g.index[0].hour*60 + g.index[0].minute
                        for _, g in df15.groupby(df15.index.date)])
    if firsts.empty:
        return df15, 0
    delta = int(9*60 - firsts.median())
    if delta == 0:
        return df15, 0
    df2 = df15.copy()
    df2.index = df2.index + pd.Timedelta(minutes=delta)
    return df2, delta


def run_engines(sym, df15, out):
    try:
        import backtest_pro as BP
        import services.trading_costs as TC
    except Exception as exc:
        out(f"    !! motores v6/v7 indisponíveis: {exc}")
        return []
    avg_px = float(df15.Close.mean())
    TC.COSTS[sym.upper()] = {"fees_brl_rt": avg_px*FEE_PCT_RT, "point_value": 1.0, "tick_size": TICK}
    base = df15[["Open", "High", "Low", "Close", "Volume"]].copy()
    shifted, delta = align_session(base)
    if delta:
        out(f"    (sessão deslocada {delta:+d} min p/ alinhar com a abertura do WIN — "
            f"necessário para o v7 disparar)")
    res = []
    for eng, exits, label in (("v6", "fixed", "V6_FIXED (Monitor MT5)"),
                              ("v6", "managed", "V6_MANAGED"),
                              ("v7", "managed", "V7_MANAGED (motor v7)")):
        t0 = time.time()
        src = shifted if eng == "v7" else base    # v6 não depende do relógio
        try:
            t = BP.simulate(src.copy(), sym, engine=eng, exits=exits)
            if t is not None and not t.empty and eng == "v7" and delta:
                for col in ("open_ts", "close_ts"):
                    if col in t.columns:
                        t[col] = pd.to_datetime(t[col]) - pd.Timedelta(minutes=delta)
        except Exception as exc:
            out(f"    {label:<24} ERRO: {exc}"); continue
        if t is not None and not t.empty:
            t = t.rename(columns={"open_ts": "ts"})
        s = stats(t); cons = consistency(t); s_in, s_out2 = oos_split(t)
        v = verdict(s, cons, s_out2)
        out(f"    {label:<24} n={s.get('n',0):<5} "
            + (f"net {s['net']:+.3f} R  PF {s['PF']:<5} cons {cons}%  "
               f"OOS {s_out2.get('net','—') if s_out2.get('n',0) else '—'}  {v}"
               if s.get('n', 0) else "(sem trades)")
            + f"  [{time.time()-t0:.0f}s]")
        if s.get("n", 0):
            t.to_csv(f"logs/acoes_{sym}_M15_{eng}_{exits}.csv", index=False)
            res.append({"sym": sym, "tf": "M15", "setup": label, **s, "cons%": cons,
                        "oos_net": s_out2.get("net"), "veredito": v})
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default=None)
    ap.add_argument("--skip-engines", action="store_true")
    ap.add_argument("--engines-only", action="store_true",
                    help="só os motores v6/v7 (pula a bateria de setups)")
    ap.add_argument("--tfs", default="5,15,60",
                    help="timeframes da bateria, ex.: --tfs 60 (só H1)")
    ap.add_argument("--bars", type=int, default=200000)
    args = ap.parse_args()
    tfs_battery = [int(x) for x in str(args.tfs).split(",") if x.strip()]

    os.makedirs("logs", exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    logf = open(f"logs/backtest_acoes_{stamp}.txt", "w", encoding="utf-8")
    def out(s):
        print(s); logf.write(s+"\n"); logf.flush()

    syms = [args.symbol.upper()] if args.symbol else SYMBOLS
    out(f"{'#'*76}\n# MEGA-BACKTEST AÇÕES B3 — {', '.join(syms)} · {datetime.now():%d/%m %H:%M}")
    out(f"# Bateria: {len(BATTERY)} setups × M5/M15/H1 · Motores reais v6/v7 em M15"
        + (" (PULADOS)" if args.skip_engines else "") + f"\n{'#'*76}")

    ranking = []
    for sym in syms:
        out(f"\n\n{'█'*76}\n█  {sym}\n{'█'*76}")
        dfs = {}
        need_tfs = set(tfs_battery) if not args.engines_only else set()
        if not args.skip_engines:
            need_tfs.add(15)                       # motores rodam em M15
        for tf in sorted(need_tfs):
            df = fetch(sym, tf, args.bars)
            if df is None or len(df) < 3000:
                out(f"  M{tf}: sem histórico suficiente"); continue
            df = add_extra(df, tf)
            dfs[tf] = df
            anos = len(df)/bars_per_day(tf)/252
            out(f"  M{tf}: {len(df)} candles (~{anos:.1f} anos) [{df.index[0].date()} → {df.index[-1].date()}]")
        for tf, df in dfs.items():
            if args.engines_only or tf not in tfs_battery:
                continue
            out(f"\n  ── {sym} · M{tf} — bateria de setups "
                f"({sum(1 for _, (f_, tfs_, _o) in BATTERY.items() if tf in tfs_)} aplicáveis) ──")
            for name, (fn, tfs_ok, opd) in BATTERY.items():
                if tf not in tfs_ok:
                    continue
                t0 = time.time()
                try:
                    t = simulate(df, sym, fn, opd)
                except Exception as exc:
                    out(f"    {name:<16} ERRO: {exc}"); continue
                s = stats(t); cons = consistency(t); s_in, s_out2 = oos_split(t)
                v = verdict(s, cons, s_out2)
                if s.get("n", 0) == 0:
                    out(f"    {name:<16} (sem trades)  [{time.time()-t0:.0f}s]"); continue
                out(f"    {name:<16} n={s['n']:<5} win {s['win']:>5}% net {s['net']:+.3f} R  "
                    f"PF {s['PF']:<5} tot {s['tot']:+7.1f} DD {s['dd']:>7} cons {cons}%  "
                    f"OOS {s_out2.get('net') if s_out2.get('n',0) else '—'}  {v}  [{time.time()-t0:.0f}s]")
                t.to_csv(f"logs/acoes_{sym}_M{tf}_{name}.csv", index=False)
                ranking.append({"sym": sym, "tf": f"M{tf}", "setup": name, **s,
                                "cons%": cons, "oos_net": s_out2.get("net"), "veredito": v})
        if not args.skip_engines and 15 in dfs:
            out(f"\n  ── {sym} · M15 — MOTORES REAIS (regras exatas da produção) ──")
            ranking += run_engines(sym, dfs[15], out)

    out(f"\n\n{'#'*76}\n  RANKING GERAL (líquida, mín. 40 trades)\n{'#'*76}")
    rk = pd.DataFrame(ranking)
    if not rk.empty:
        rk2 = rk[rk.n >= 40].sort_values("net", ascending=False)
        for _, r in rk2.head(40).iterrows():
            out(f"  {r.sym:<7} {r.tf:<4} {str(r.setup):<24} n={int(r.n):<5} net {r.net:+.3f} R  "
                f"PF {r.PF:<5} cons {r['cons%']}%  OOS {r.oos_net}  {r.veredito}")
        rk.to_csv(f"logs/backtest_acoes_ranking_{stamp}.csv", index=False)
    out(f"\n# Critério GO: net ≥ +0,10 R · PF ≥ 1,25 · cons ≥ 55% · OOS segura (net ≥ +0,08, PF ≥ 1,15)")
    out(f"# FIM — envie logs/backtest_acoes_{stamp}.txt para análise")
    logf.close()


if __name__ == "__main__":
    main()
