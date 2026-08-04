"""
backtest_setups_kimi2.py — TESTE DOS 6 SETUPS NOVOS DO KIMI (rodada 2).

Mesmo rigor dos anteriores: barra a barra, custos reais, walk-forward por
bimestre, separação IN-SAMPLE × OUT-OF-SAMPLE (anti-overfit) e critério GO.

Setups (ver RESULTADOS_BACKTESTS_PARA_KIMI.md para o contexto):
  1 REVERSAO_TARDE   WIN     14:00–16:00  reversão no extremo do dia (contra o varejo)
  6 SPIKE_FADE       WIN     09:15        gap de CONTINUAÇÃO (exceção ao gap fade)
  2 LONDON_HANDOFF   XAUUSD  handoff Ásia→Londres  ORDER_FLOW filtrado por sessão
  5 XAU_PULLBACK     XAUUSD  24h          pullback à EMA20 H1 em tendência H1
  3 ETH_FRONTRUN     ETHUSD  24h          absorção (wick) em nível psicológico redondo
  4 BTC_EXPANSION    BTCUSD  24h          Bollinger squeeze → expansão com bias VWAP

Timeframe base M15 (alcança os ~6-7 anos no WIN; ~1,4-2 no crypto). Setup 5 usa
contexto H1 (resample). Horários: WIN em hora local B3; XAU em hora do servidor
IC Markets (~GMT+2/+3) — ajustável em SESS_* abaixo.

⚠️ Rodar em DOIS passes (não dá p/ ter as 2 MT5 no mesmo processo):
   python backtest_setups_kimi2.py --grupo b3       (MT5 da XP aberta)
   python backtest_setups_kimi2.py --grupo crypto   (MT5 da IC Markets aberta)
Saída: ranking na tela + logs/backtest_setups_kimi2_<grupo>_<stamp>.txt/.csv
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

# Reaproveita indicadores base, custos e chave de ativo do backtest_kimi_real.
# B3 usa o fetch da XP (backtest_kimi_real.fetch, já resolve WIN$D/WDO$D).
# Crypto usa o fetch da IC Markets (backtest_crypto_pro, credenciais MT5_CRYPTO_*)
# — MESMO caminho de conexão do validar_orderflow, que funciona.
from backtest_kimi_real import fetch as _b3_fetch, add_indicators, asset_key, roundtrip_cost_pts

# ── Janelas de sessão (hora do servidor IC Markets p/ XAU; ver lab_setups_crypto) ──
SESS_ASIA   = (1, 9)      # range asiático 01:00–09:00 servidor
SESS_LONDON = (9, 11)     # janela de handoff/rompimento 09:00–11:00 servidor
# WIN (hora local B3)
WIN_TARDE   = (14, 16)    # reversão de tarde
WIN_GAP_HHMM = (9, 15)    # 1ª barra M15 (09:15)

ROUND_STEP = {"ETHUSD": 50.0}   # nível psicológico "redondo" por ativo
OOS_FRAC   = 0.30


# ════════════════════════════════════════════════════════════════════════════
# INDICADORES EXTRA (por cima do fetch/add_indicators do backtest_kimi_real)
# ════════════════════════════════════════════════════════════════════════════

def add_extra(df, symbol):
    h, l, c, o, v = df.High, df.Low, df.Close, df.Open, df.Volume
    hh = df.index.hour + df.index.minute / 60.0
    df["hh"] = hh
    df["vol8"]  = v.rolling(8).mean()
    df["vol20b"] = v.rolling(20).mean()
    # Bollinger width + squeeze (percentil 10 nas últimas 100 barras)
    bbw = (df["bb_up"] - df["bb_dn"]) / df["bb_mid"].replace(0, np.nan)
    df["bbw"] = bbw
    df["bbw_q10"] = bbw.rolling(100).quantile(0.10)
    # ATR "do dia" e média 10d (regime da tarde do WIN)
    df["atr_ma"] = df["atr"].rolling(440).mean()      # ~10 pregões M15 B3
    # Referências intradiárias por dia
    day = df["day"]
    df["day_open"] = df.groupby(day)["Open"].transform("first")
    df["day_hi"] = df.groupby(day)["High"].cummax()
    df["day_lo"] = df.groupby(day)["Low"].cummin()
    df["day_hi_prev"] = df.groupby(day)["High"].cummax().groupby(day).shift(1)
    df["day_lo_prev"] = df.groupby(day)["Low"].cummin().groupby(day).shift(1)
    # Fechamento do dia anterior + gap de abertura
    day_close = df.groupby(day)["Close"].last()
    prev_close_map = day_close.shift(1)                 # índice = data
    df["prev_close"] = day.map(prev_close_map)
    df["gap_pct"] = (df["day_open"] - df["prev_close"]) / df["prev_close"].replace(0, np.nan) * 100
    # 1ª barra do dia (barra de abertura) + média 20d do volume dela
    first_mask = (df.groupby(day).cumcount() == 0)
    df["is_first_bar"] = first_mask.astype(int)
    fb_series = pd.Series(v[first_mask].values, index=day[first_mask].values)   # data -> vol 1ª barra
    fb_ma = fb_series.rolling(20).mean()
    df["first_bar_vol_ma20"] = day.map(fb_ma)
    # ── WIN abertura (setups A–D do Kimi, rodada 3) ──
    df["day_first_high"] = df.groupby(day)["High"].transform("first")
    df["day_first_low"]  = df.groupby(day)["Low"].transform("first")
    df["faded_dn_once"] = (df["Close"] < df["day_open"]).astype(int).groupby(day).cummax()  # já fechou < abertura hoje
    df["faded_up_once"] = (df["Close"] > df["day_open"]).astype(int).groupby(day).cummax()
    orb_mask = (hh >= 10.0) & (hh <= 10.75)      # range ORB 10:00–10:45
    df["orb_hi"] = df.High.where(orb_mask).groupby(day).transform("max")
    df["orb_lo"] = df.Low.where(orb_mask).groupby(day).transform("min")
    # Sessão asiática (range por dia) — para XAU
    asia_mask = (hh >= SESS_ASIA[0]) & (hh < SESS_ASIA[1])
    ah = df.High.where(asia_mask).groupby(day).transform("max")
    al = df.Low.where(asia_mask).groupby(day).transform("min")
    df["asia_hi"], df["asia_lo"] = ah, al
    df["asia_done_once"] = 0   # marca 1 trade/dia no handoff (preenchido no simulador)
    # Contexto H1 (para XAU pullback): EMA20/50 e RSI do H1, reindexado no M15
    h1 = df[["Open", "High", "Low", "Close"]].resample("1h").agg(
        {"Open": "first", "High": "max", "Low": "min", "Close": "last"}).dropna()
    h1["ema20"] = h1.Close.ewm(span=20, adjust=False).mean()
    h1["ema50"] = h1.Close.ewm(span=50, adjust=False).mean()
    d = h1.Close.diff()
    up = d.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
    h1["rsi"] = 100 - 100/(1 + up/dn.replace(0, 1e-9))
    df["h1_ema20"] = h1["ema20"].reindex(df.index, method="ffill")
    df["h1_ema50"] = h1["ema50"].reindex(df.index, method="ffill")
    df["h1_rsi"]   = h1["rsi"].reindex(df.index, method="ffill")
    return df


# ── Helpers de candle ────────────────────────────────────────────────────────

def _parts(r):
    body = abs(r.Close - r.Open); rng = max(r.High - r.Low, 1e-9)
    up_wick = r.High - max(r.Close, r.Open)
    dn_wick = min(r.Close, r.Open) - r.Low
    return body, rng, up_wick, dn_wick


def reversal_up(df, i):
    """Candle de reversão de baixo (fundo): hammer OU engolfo de alta."""
    r = df.iloc[i]; p = df.iloc[i-1]
    body, rng, uw, dw = _parts(r)
    hammer = dw >= 0.6 * rng and body <= 0.4 * rng
    engulf = (r.Close > r.Open) and (p.Close < p.Open) and (r.Close >= p.Open) and (r.Open <= p.Close)
    return hammer or engulf


def reversal_down(df, i):
    """Candle de reversão de cima (topo): inverted hammer/estrela OU engolfo de baixa."""
    r = df.iloc[i]; p = df.iloc[i-1]
    body, rng, uw, dw = _parts(r)
    star = uw >= 0.6 * rng and body <= 0.4 * rng
    engulf = (r.Close < r.Open) and (p.Close > p.Open) and (r.Close <= p.Open) and (r.Open >= p.Close)
    return star or engulf


# ════════════════════════════════════════════════════════════════════════════
# SETUPS  →  retornam dict(dir, sl, tp1, tp2, partial) ou None
# ════════════════════════════════════════════════════════════════════════════

def s_reversao_tarde(df, i, sym):                       # SETUP 1 — WIN
    r = df.iloc[i]
    if not (WIN_TARDE[0] <= r.hh < WIN_TARDE[1]):
        return None
    if pd.isna(r.atr_ma) or r.atr <= 0 or (r.atr / r.atr_ma) >= 1.5:   # regime range/tend. fraca
        return None
    if r.Volume < 1.2 * (r.vol8 or 0):
        return None
    subiu = r.Close > r.day_open
    # short no topo do dia (dia subiu) — novo extremo + reversão de topo
    if subiu and not pd.isna(r.day_hi_prev) and r.High >= r.day_hi_prev and reversal_down(df, i):
        entry = r.Close; sl = entry + 1.5 * r.atr; risk = sl - entry
        return {"dir": "VENDA", "sl": sl, "tp1": entry - 1.5*risk, "tp2": entry - 2.5*risk, "partial": True}
    # long no fundo do dia (dia caiu)
    if (not subiu) and not pd.isna(r.day_lo_prev) and r.Low <= r.day_lo_prev and reversal_up(df, i):
        entry = r.Close; sl = entry - 1.5 * r.atr; risk = entry - sl
        return {"dir": "COMPRA", "sl": sl, "tp1": entry + 1.5*risk, "tp2": entry + 2.5*risk, "partial": True}
    return None


def s_spike_fade(df, i, sym):                           # SETUP 6 — WIN (gap continuação)
    r = df.iloc[i]
    if int(r.is_first_bar) != 1:                        # só na barra de abertura do dia
        return None
    if pd.isna(r.gap_pct) or pd.isna(r.first_bar_vol_ma20) or r.first_bar_vol_ma20 <= 0:
        return None
    if abs(r.gap_pct) < 1.0:
        return None
    if r.Volume < 2.0 * r.first_bar_vol_ma20:            # volume anômalo = gap real
        return None
    gap_up = r.gap_pct > 0
    if gap_up and r.Close > r.Open:                     # 1ª barra confirma o gap
        entry = r.Close; sl = r.prev_close; risk = entry - sl
        if risk > 0:
            return {"dir": "COMPRA", "sl": sl, "tp1": entry + 2*risk, "tp2": None, "partial": False}
    if (not gap_up) and r.Close < r.Open:
        entry = r.Close; sl = r.prev_close; risk = sl - entry
        if risk > 0:
            return {"dir": "VENDA", "sl": sl, "tp1": entry - 2*risk, "tp2": None, "partial": False}
    return None


def s_london_handoff(df, i, sym):                       # SETUP 2 — XAU
    r = df.iloc[i]
    if not (SESS_LONDON[0] <= r.hh < SESS_LONDON[1]):
        return None
    if pd.isna(r.asia_hi) or pd.isna(r.asia_lo) or pd.isna(r.atr) or r.atr <= 0:
        return None
    asia_rng = r.asia_hi - r.asia_lo
    atr_d = r.atr * 16                                  # ~ATR diário aprox. (16 barras M15/dia útil de sessão)
    if asia_rng <= 0 or asia_rng > 0.60 * atr_d:        # range asiático estreito = acumulação
        return None
    vt = r.vol_sum10
    dn = (r.delta_sum10 / vt) if (vt and vt > 0) else 0
    p = df.iloc[i-1]
    # rompimento do high asiático com delta positivo (long) / low com delta negativo (short)
    if r.Close > r.asia_hi and p.Close <= r.asia_hi and dn > 0.15:
        entry = r.Close; sl = r.asia_lo; risk = entry - sl
        if risk > 0:
            return {"dir": "COMPRA", "sl": sl, "tp1": entry + 1.75*asia_rng, "tp2": None, "partial": False}
    if r.Close < r.asia_lo and p.Close >= r.asia_lo and dn < -0.15:
        entry = r.Close; sl = r.asia_hi; risk = sl - entry
        if risk > 0:
            return {"dir": "VENDA", "sl": sl, "tp1": entry - 1.75*asia_rng, "tp2": None, "partial": False}
    return None


def s_xau_pullback(df, i, sym):                         # SETUP 5 — XAU (pullback H1)
    r = df.iloc[i]
    if pd.isna(r.h1_ema20) or pd.isna(r.h1_ema50) or pd.isna(r.atr) or r.atr <= 0:
        return None
    up = r.h1_ema20 > r.h1_ema50
    dn = r.h1_ema20 < r.h1_ema50
    near = abs(r.Close - r.h1_ema20) < 2 * r.atr        # pullback até a EMA20 H1
    if up and near and 40 <= (r.h1_rsi or 0) <= 70 and reversal_up(df, i):
        entry = r.Close; sl = df.iloc[i-1].Low if df.iloc[i-1].Low < r.Low else r.Low
        sl = min(sl, r.Low); risk = entry - sl
        if risk > 0:
            return {"dir": "COMPRA", "sl": sl, "tp1": entry + 1.5*risk, "tp2": entry + 2.0*risk, "partial": True}
    if dn and near and 30 <= (r.h1_rsi or 0) <= 60 and reversal_down(df, i):
        entry = r.Close; sl = max(df.iloc[i-1].High, r.High); risk = sl - entry
        if risk > 0:
            return {"dir": "VENDA", "sl": sl, "tp1": entry - 1.5*risk, "tp2": entry - 2.0*risk, "partial": True}
    return None


def s_eth_frontrun(df, i, sym):                         # SETUP 3 — ETH (nível redondo)
    r = df.iloc[i]; p = df.iloc[i-1]
    step = ROUND_STEP.get(asset_key(sym), 50.0)
    lvl = round(r.Close / step) * step
    if lvl <= 0 or abs(r.Close - lvl) / lvl > 0.003:    # a < 0,3% de um nível redondo
        return None
    body_p, rng_p, uw_p, dw_p = _parts(p)
    # rejeição por baixo (wick inferior longo) na barra anterior + fechamento p/ cima → long
    if dw_p >= 0.6 * rng_p and r.Close > r.Open and r.Close > p.High and r.Close > r.ema20:
        entry = r.Close; sl = p.Low; risk = entry - sl
        if risk > 0:
            return {"dir": "COMPRA", "sl": sl, "tp1": entry + 2*risk, "tp2": None, "partial": False}
    # rejeição por cima (wick superior longo) + fechamento p/ baixo → short
    if uw_p >= 0.6 * rng_p and r.Close < r.Open and r.Close < p.Low and r.Close < r.ema20:
        entry = r.Close; sl = p.High; risk = sl - entry
        if risk > 0:
            return {"dir": "VENDA", "sl": sl, "tp1": entry - 2*risk, "tp2": None, "partial": False}
    return None


def s_btc_expansion(df, i, sym):                        # SETUP 4 — BTC (squeeze)
    r = df.iloc[i]
    if pd.isna(r.bbw) or pd.isna(r.bbw_q10) or pd.isna(r.vwap):
        return None
    prev = df.iloc[i-1]
    squeeze = (prev.bbw <= prev.bbw_q10)                # squeeze na barra anterior
    if not squeeze or r.Volume < 1.5 * (r.vol20b or 0):
        return None
    if r.Close > r.vwap and r.Close > r.bb_up:          # bias de alta + rompimento real
        entry = r.Close; sl = r.bb_dn; risk = entry - sl
        if risk > 0:
            return {"dir": "COMPRA", "sl": sl, "tp1": entry + 2.5*risk, "tp2": None, "partial": False}
    if r.Close < r.vwap and r.Close < r.bb_dn:
        entry = r.Close; sl = r.bb_up; risk = sl - entry
        if risk > 0:
            return {"dir": "VENDA", "sl": sl, "tp1": entry - 2.5*risk, "tp2": None, "partial": False}
    return None


_WIN_TICK = 5.0


def s_orb_dir(df, i, sym):                              # SETUP C — ORB direcional (gap-aligned)
    r = df.iloc[i]
    if not (10.75 < r.hh < 12.0):                       # após o range 10:00–10:45 formar
        return None
    if pd.isna(r.orb_hi) or pd.isna(r.orb_lo) or pd.isna(r.gap_pct) or abs(r.gap_pct) < 0.2:
        return None
    p = df.iloc[i-1]
    orb_above = r.orb_lo > r.day_open                   # range inteiro acima da abertura
    orb_below = r.orb_hi < r.day_open
    if r.gap_pct > 0 and orb_above and r.Close > r.orb_hi and p.Close <= r.orb_hi:
        entry = r.Close; sl = r.orb_lo; risk = entry - sl
        if risk > 0:
            return {"dir": "COMPRA", "sl": sl, "tp1": entry+1.5*risk, "tp2": entry+3.0*risk, "partial": True}
    if r.gap_pct < 0 and orb_below and r.Close < r.orb_lo and p.Close >= r.orb_lo:
        entry = r.Close; sl = r.orb_hi; risk = sl - entry
        if risk > 0:
            return {"dir": "VENDA", "sl": sl, "tp1": entry-1.5*risk, "tp2": entry-3.0*risk, "partial": True}
    return None


def s_pre_orb(df, i, sym):                              # SETUP A — pré-ORB (gap pequeno)
    r = df.iloc[i]
    if abs(r.hh - 9.75) > 1e-6 or i < 2:                # barra das 09:45
        return None
    if pd.isna(r.gap_pct) or abs(r.gap_pct) >= 0.3:     # só gap pequeno/inexistente
        return None
    b1, b2 = df.iloc[i-1], df.iloc[i-2]                 # 09:30, 09:15
    if abs(b1.hh - 9.5) > 1e-6 or abs(b2.hh - 9.25) > 1e-6:
        return None
    if r.Volume < 1.3 * ((b1.Volume + b2.Volume) / 2):
        return None
    hi2, lo2 = max(b1.High, b2.High), min(b1.Low, b2.Low)
    rng_lo, rng_hi = min(lo2, r.Low), max(hi2, r.High)
    if r.Close > hi2:                                   # rompe high das 2 barras anteriores
        entry = r.Close; sl = rng_lo; risk = entry - sl
        if risk > 0:
            return {"dir": "COMPRA", "sl": sl, "tp1": entry+1.5*risk, "tp2": entry+2.5*risk, "partial": True}
    if r.Close < lo2:
        entry = r.Close; sl = rng_hi; risk = sl - entry
        if risk > 0:
            return {"dir": "VENDA", "sl": sl, "tp1": entry-1.5*risk, "tp2": entry-2.5*risk, "partial": True}
    return None


def s_fade_impulso(df, i, sym):                         # SETUP B — fade da extensão do gap médio
    r = df.iloc[i]
    if abs(r.hh - 9.5) > 1e-6:                          # barra das 09:30
        return None
    g = r.gap_pct
    if pd.isna(g) or not (0.3 <= abs(g) <= 0.8):
        return None
    gap_px = abs(r.day_open - r.prev_close)
    if g > 0:                                           # gap up → fade short da extensão
        impulse = r.day_hi - r.prev_close
        if impulse <= 1.0 * gap_px or r.Close >= r.Open:
            return None
        entry = r.Close; sl = r.day_hi + _WIN_TICK; risk = sl - entry
        if risk > 0:
            return {"dir": "VENDA", "sl": sl, "tp1": entry-1.5*risk, "tp2": None, "partial": False}
    else:                                               # gap down → fade long
        impulse = r.prev_close - r.day_lo
        if impulse <= 1.0 * gap_px or r.Close <= r.Open:
            return None
        entry = r.Close; sl = r.day_lo - _WIN_TICK; risk = entry - sl
        if risk > 0:
            return {"dir": "COMPRA", "sl": sl, "tp1": entry+1.5*risk, "tp2": None, "partial": False}
    return None


def s_segunda_tent(df, i, sym):                         # SETUP D — 2ª tentativa (a favor do gap)
    r = df.iloc[i]
    if not (9.5 <= r.hh <= 10.0):
        return None
    g = r.gap_pct
    if pd.isna(g) or abs(g) < 0.3:
        return None
    if g > 0:                                           # gap up: fade falhou → recompra
        if int(r.faded_dn_once) != 1 or r.day_lo <= r.prev_close:   # fadeou mas incompleto
            return None
        if r.Close <= r.day_first_high or r.Volume < (r.vol8 or 0):  # recupera acima da máx. de abertura
            return None
        entry = r.Close; sl = r.day_lo; risk = entry - sl
        if risk > 0:
            return {"dir": "COMPRA", "sl": sl, "tp1": entry+2.0*risk, "tp2": None, "partial": False}
    else:                                               # gap down: fade falhou → revende
        if int(r.faded_up_once) != 1 or r.day_hi >= r.prev_close:
            return None
        if r.Close >= r.day_first_low or r.Volume < (r.vol8 or 0):
            return None
        entry = r.Close; sl = r.day_hi; risk = sl - entry
        if risk > 0:
            return {"dir": "VENDA", "sl": sl, "tp1": entry-2.0*risk, "tp2": None, "partial": False}
    return None


STRATS = {
    "b3": {"REVERSAO_TARDE": s_reversao_tarde, "SPIKE_FADE": s_spike_fade,
           "ORB_DIR": s_orb_dir, "PRE_ORB": s_pre_orb,
           "FADE_IMPULSO": s_fade_impulso, "SEGUNDA_TENT": s_segunda_tent},
    "crypto": {"LONDON_HANDOFF": s_london_handoff, "XAU_PULLBACK": s_xau_pullback,
               "ETH_FRONTRUN": s_eth_frontrun, "BTC_EXPANSION": s_btc_expansion},
}
# em que ativo cada setup deve rodar (evita rodar tudo em tudo)
STRAT_SYM = {
    "REVERSAO_TARDE": "WIN", "SPIKE_FADE": "WIN",
    "ORB_DIR": "WIN", "PRE_ORB": "WIN", "FADE_IMPULSO": "WIN", "SEGUNDA_TENT": "WIN",
    "LONDON_HANDOFF": "XAUUSD", "XAU_PULLBACK": "XAUUSD",
    "ETH_FRONTRUN": "ETHUSD", "BTC_EXPANSION": "BTCUSD",
}
ONE_PER_DAY = {"LONDON_HANDOFF", "SPIKE_FADE", "ETH_FRONTRUN",
               "ORB_DIR", "SEGUNDA_TENT"}


# ════════════════════════════════════════════════════════════════════════════
# SIMULADOR (gerido, com parcial opcional; EOD na B3, time-stop no crypto)
# ════════════════════════════════════════════════════════════════════════════

def simulate(df, symbol, strat_fn, name):
    is_b3 = asset_key(symbol) in ("WIN", "WDO")
    max_bars = 96                                       # time-stop crypto = 1 dia M15
    one_per_day = name in ONE_PER_DAY
    trades, pos = [], None
    last_day_traded = None
    n = len(df)
    for i in range(460, n):
        if pos:
            r = df.iloc[i]; buy = pos["dir"] == "COMPRA"
            exit_R = None; stopped = True
            # 1) stop
            if (r.Low <= pos["sl"]) if buy else (r.High >= pos["sl"]):
                exit_R = ((pos["sl"]-pos["entry"])/pos["risk"]) if buy else ((pos["entry"]-pos["sl"])/pos["risk"])
            # 2) alvo final (tp2 se existe, senão tp1)
            elif pos["tp2"] and ((r.High >= pos["tp2"]) if buy else (r.Low <= pos["tp2"])):
                exit_R = (pos["tp2"]-pos["entry"])/pos["risk"] if buy else (pos["entry"]-pos["tp2"])/pos["risk"]
                stopped = False
            elif (not pos["tp2"]) and ((r.High >= pos["tp1"]) if buy else (r.Low <= pos["tp1"])):
                exit_R = (pos["tp1"]-pos["entry"])/pos["risk"] if buy else (pos["entry"]-pos["tp1"])/pos["risk"]
                stopped = False
            # TP1 intermediário (parcial/BE) quando há tp2
            elif pos["tp2"] and (not pos["tp1_done"]) and ((r.High >= pos["tp1"]) if buy else (r.Low <= pos["tp1"])):
                pos["tp1_done"] = True
                pos["realized"] = 0.5 * ((pos["tp1"]-pos["entry"])/pos["risk"] if buy else (pos["entry"]-pos["tp1"])/pos["risk"]) if pos["partial"] else 0.0
                pos["sl"] = pos["entry"]                # breakeven no restante
            # EOD B3 / time-stop crypto
            if exit_R is None and is_b3 and r["day"] != pos["day"]:
                px = df.iloc[i-1].Close
                exit_R = ((px-pos["entry"])/pos["risk"]) if buy else ((pos["entry"]-px)/pos["risk"])
            if exit_R is None and (not is_b3) and (i - pos["i"]) >= max_bars:
                px = r.Close
                exit_R = ((px-pos["entry"])/pos["risk"]) if buy else ((pos["entry"]-px)/pos["risk"])
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
        entry = float(df.iloc[i].Close); risk = abs(entry - sig["sl"])
        atr_i = float(df.iloc[i].atr or 0)
        if risk <= 0 or risk < 0.10 * max(atr_i, 1e-9):
            continue
        pos = {"ts": df.index[i], "i": i, "day": df.iloc[i]["day"], "dir": sig["dir"],
               "entry": entry, "sl": sig["sl"], "tp1": sig["tp1"], "tp2": sig["tp2"],
               "partial": sig["partial"], "tp1_done": False, "realized": 0.0, "risk": risk}
        last_day_traded = df.iloc[i]["day"]
    return pd.DataFrame(trades)


# ── Estatísticas / consistência / OOS ────────────────────────────────────────

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
    ap.add_argument("--bars", type=int, default=200000)
    args = ap.parse_args()

    os.makedirs("logs", exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    logf = open(f"logs/backtest_setups_kimi2_{args.grupo}_{stamp}.txt", "w", encoding="utf-8")
    def out(s):
        print(s); logf.write(s + "\n"); logf.flush()

    out(f"{'#'*74}\n# 6 SETUPS NOVOS DO KIMI — {args.grupo} · M{args.tf} · {datetime.now():%d/%m %H:%M}\n{'#'*74}")
    strat_map = STRATS[args.grupo]
    # símbolos necessários neste grupo
    syms = sorted({STRAT_SYM[n] for n in strat_map})
    sym_full = {"WIN": "WIN$D", "WDO": "WDO$D", "XAUUSD": "XAUUSD", "ETHUSD": "ETHUSD", "BTCUSD": "BTCUSD"}
    ranking = []

    # ── Conexão/fetch por grupo ──────────────────────────────────────────────
    _mt5c = None
    if args.grupo == "crypto":
        from backtest_crypto_pro import mt5_connect, fetch as _cp_fetch
        _mt5c = mt5_connect()

    def _get(k):
        """Retorna df com indicadores (base + extra) para o ativo k, ou None."""
        if args.grupo == "b3":
            df = _b3_fetch(sym_full[k], args.tf, args.bars)      # já vem com add_indicators
        else:
            raw = _cp_fetch(_mt5c, sym_full[k], args.tf, args.bars)  # OHLCV cru
            df = add_indicators(raw) if raw is not None else None
        return df

    dfs = {}
    for k in syms:
        t0 = time.time()
        df = _get(k)
        if df is None or len(df) < 5000:
            out(f"\n!! {k}: sem histórico suficiente"); continue
        df = add_extra(df, sym_full[k])
        dfs[k] = df
        anos = len(df)/(26 if k in ("WIN", "WDO") else 96)/(252 if k in ("WIN", "WDO") else 365)
        out(f"\n{'='*74}\n  {k}: {len(df)} candles M{args.tf} (~{anos:.1f} anos)  "
            f"[{df.index[0].date()} → {df.index[-1].date()}]  ({time.time()-t0:.0f}s fetch)\n{'='*74}")

    for name, fn in strat_map.items():
        k = STRAT_SYM[name]
        if k not in dfs:
            out(f"\n  ▸ {name} ({k}): sem dados"); continue
        df = dfs[k]; symf = sym_full[k]
        t0 = time.time()
        t = simulate(df, symf, fn, name)
        s = stats(t); cons = consistency(t)
        out(f"\n  ▸ {name}  ({k})   [{time.time()-t0:.0f}s]")
        out(_line("PERÍODO COMPLETO", s, cons))
        s_out = {"n": 0}
        if not t.empty:
            t = t.sort_values("ts").reset_index(drop=True)
            cut = int(len(t)*(1-OOS_FRAC))
            s_in, s_out = stats(t.iloc[:cut]), stats(t.iloc[cut:])
            out(_line("IN-SAMPLE  (70%)", s_in, consistency(t.iloc[:cut])))
            out(_line("OUT-SAMPLE (30%)", s_out, consistency(t.iloc[cut:])))
            if s_out.get("n", 0) >= 20:
                seg = s_out["net"] >= 0.08 and s_out["PF"] >= 1.15
                out(f"      → OUT-OF-SAMPLE {'SEGUROU ✅' if seg else 'CAIU ❌'} "
                    f"(net {s_out['net']:+.3f}, PF {s_out['PF']})")
            t.to_csv(f"logs/kimi2_{k}_{name}.csv", index=False)
        v = verdict(s, cons, s_out)
        out(f"      VEREDITO: {v}")
        ranking.append({"setup": name, "sym": k, **s, "cons%": cons, "veredito": v})

    out(f"\n{'#'*74}\n  RANKING (líquida)\n{'#'*74}")
    rk = pd.DataFrame(ranking)
    if not rk.empty:
        rk = rk.sort_values("net", ascending=False)
        for _, r in rk.iterrows():
            if r.get("n", 0) == 0:
                out(f"  {r.sym:<7} {r.setup:<16} sem trades"); continue
            out(f"  {r.sym:<7} {r.setup:<16} n={int(r.n):<5} net {r.net:+.3f} R  PF {r.PF:<5} "
                f"cons {r['cons%']}%  {r.veredito}")
    out(f"\n# Critério GO: net ≥ +0,10 R · PF ≥ 1,25 · consist. ≥ 55% · OUT-OF-SAMPLE segura")
    out(f"# FIM — envie logs/backtest_setups_kimi2_{args.grupo}_{stamp}.txt para análise")
    logf.close()


if __name__ == "__main__":
    main()
