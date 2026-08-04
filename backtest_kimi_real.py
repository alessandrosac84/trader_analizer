"""
backtest_kimi_real.py — Teste REAL das 12 estratégias imaginadas pela IA Kimi.

A Kimi gerou um motor com 12 setups (backtest_kimi.py), mas sem custos e sem
dados do MT5. Aqui adaptamos a MESMA lógica dela para a nossa régua:
  - Dados REAIS do MT5 (WIN$D, WDO$D da B3; BTCUSD, ETHUSD, XAUUSD da IC Markets)
  - CUSTOS reais (taxas B3 da nota Santander + spread; spread de CFD da IC)
  - Avaliação BAR-A-BAR (walk-forward), sem look-ahead
  - Exits da Kimi: SL / TP1(2R) → move stop p/ breakeven / TP2(3R)
  - Métricas por ativo × estratégia + consistência bimestral + critério de GO

Objetivo: ver se a Kimi pensou em algum setup DIFERENTE dos nossos que seja
lucrativo depois dos custos (foco nos conceitos novos: ICT sweep, Market
Profile, FVG, Liquidity Sweep, Order Flow por delta).

⚠️ Rodar com os DOIS MT5 abertos conforme o ativo:
   - WIN$D, WDO$D  → MT5 da XP (perfil b3, MT5_* do .env)
   - BTCUSD/ETH/XAU→ MT5 da IC Markets (MT5_CRYPTO_* do .env)
   Como não dá para ter os dois no mesmo processo, rode em duas passadas:
     python backtest_kimi_real.py --grupo b3
     python backtest_kimi_real.py --grupo crypto
   (ou --symbol WIN$D para um só)

Timeframe: M5 (como a Kimi projetou). Use --tf 15 para M15 se quiser comparar.
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

# ── Custos ──────────────────────────────────────────────────────────────────
# B3: taxas por contrato round-trip (nota Santander) em pontos + spread (ticks).
# Crypto CFD: spread por round-trip (medido ao vivo ou tabela IC Markets).
B3_FEES_BRL_RT = {"WIN": 0.50, "WDO": 1.15}
POINT_VALUE    = {"WIN": 0.20, "WDO": 10.0}
TICK_SIZE      = {"WIN": 5.0,  "WDO": 0.5,
                  "BTCUSD": 0.1, "ETHUSD": 0.01, "XAUUSD": 0.01,
                  "EURUSD": 0.00001, "GBPUSD": 0.00001}
CFD_SPREAD     = {"BTCUSD": 15.0, "ETHUSD": 2.9, "XAUUSD": 0.30,
                  "EURUSD": 0.00007, "GBPUSD": 0.00012}

RR_TP1 = 2.0   # da Kimi
RR_TP2 = 3.0


def asset_key(symbol):
    s = symbol.upper()
    for k in ("WIN", "WDO", "BTCUSD", "ETHUSD", "XAUUSD", "EURUSD", "GBPUSD"):
        if k in s:
            return k
    return s


def roundtrip_cost_pts(symbol, stopped=True):
    """Custo total (em pontos) de um round-trip de 1 unidade."""
    k = asset_key(symbol)
    ts = TICK_SIZE.get(k, 1.0)
    if k in ("WIN", "WDO"):
        fees_pts = B3_FEES_BRL_RT[k] / POINT_VALUE[k]
        cost = fees_pts + ts                      # entrada a mercado cruza 1 tick
        if stopped:
            cost += ts                            # saída no stop cruza 1 tick
        return cost
    # CFD: spread cheio no round-trip
    return CFD_SPREAD.get(k, ts)


# ════════════════════════════════════════════════════════════════════════════
# INDICADORES (vetorizados — mesma definição da Kimi, mas rápido)
# ════════════════════════════════════════════════════════════════════════════

def add_indicators(df):
    c, h, l, o, v = df.Close, df.High, df.Low, df.Open, df.Volume
    df["ema8"]  = c.ewm(span=8, adjust=False).mean()
    df["ema21"] = c.ewm(span=21, adjust=False).mean()
    df["ema50"] = c.ewm(span=50, adjust=False).mean()
    df["ema200"]= c.ewm(span=200, adjust=False).mean()
    df["ema5"]  = c.ewm(span=5, adjust=False).mean()
    df["ema10"] = c.ewm(span=10, adjust=False).mean()
    df["ema20"] = c.ewm(span=20, adjust=False).mean()
    # RSI Wilder
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
    df["rsi"] = 100 - 100/(1 + up/dn.replace(0, 1e-9))
    # ATR
    tr = pd.concat([(h-l), (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    df["atr"] = tr.ewm(alpha=1/14, adjust=False).mean()
    # MACD
    macd = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    df["macd"] = macd
    df["macd_sig"] = macd.ewm(span=9, adjust=False).mean()
    # Bollinger 20/2
    sma20 = c.rolling(20).mean(); std20 = c.rolling(20).std()
    df["bb_mid"] = sma20; df["bb_up"] = sma20 + 2*std20; df["bb_dn"] = sma20 - 2*std20
    # Stochastic 5/3/3
    ll = l.rolling(5).min(); hh = h.rolling(5).max()
    k_ = 100*(c-ll)/(hh-ll).replace(0, 1e-9)
    df["stk"] = k_.rolling(3).mean()
    df["stod"] = df["stk"].rolling(3).mean()   # %D (nome != 'std' p/ não colidir com pandas)
    # volume médio 10
    df["vol10"] = v.rolling(10).mean()
    df["vol5"]  = v.rolling(5).mean()
    df["vol20"] = v.rolling(20).mean()
    # VWAP diário
    day = pd.Series(df.index.date, index=df.index)
    tp = (h+l+c)/3
    df["vwap"] = (tp*v).groupby(day).cumsum() / v.groupby(day).cumsum().replace(0, np.nan)
    df["day"] = day.values
    # ── pré-computos p/ Order Flow (delta proxy) — vetorizado (velocidade) ──
    rng = (h-l).replace(0, np.nan)
    delta = ((c-l)/rng - 0.5) * 2 * v
    df["delta_sum10"] = delta.rolling(10).sum()
    df["vol_sum10"]   = v.rolling(10).sum()
    vwl = (c*v).rolling(20).sum() / v.rolling(20).sum().replace(0, np.nan)
    dev = ((v*(c-vwl)**2).rolling(20).sum() / v.rolling(20).sum().replace(0, np.nan))**0.5
    df["va_vwl"] = vwl; df["va_dev"] = dev
    return df


# ── candle helpers ──────────────────────────────────────────────────────────
def body(o, c): return abs(c-o)
def is_hammer(o,h,l,c):
    b=abs(c-o); si=min(o,c)-l; ss=h-max(o,c)
    return b>0 and c>o and b<=si*0.5 and si>=b*2 and ss<=b*0.5
def is_star(o,h,l,c):
    b=abs(c-o); si=min(o,c)-l; ss=h-max(o,c)
    return b>0 and c<o and b<=ss*0.5 and ss>=b*2 and si<=b*0.5
def is_doji(o,h,l,c):
    rt=h-l; return rt>0 and abs(c-o)/rt<=0.05
def rev_up(o,h,l,c, po,pc):
    return is_hammer(o,h,l,c) or (c>o and pc<po and o<=pc and c>=po) or is_doji(o,h,l,c)
def rev_dn(o,h,l,c, po,pc):
    return is_star(o,h,l,c) or (c<o and pc>po and o>=pc and c<=po) or is_doji(o,h,l,c)


# ════════════════════════════════════════════════════════════════════════════
# ESTRATÉGIAS — avaliadas no ÚLTIMO candle de um contexto (bar-a-bar)
# Cada função recebe (df, i, cfg) e devolve (dir, stop, tp1, tp2) ou None.
# Lógica fiel aos parâmetros da Kimi; adaptada p/ decisão no candle i.
# ════════════════════════════════════════════════════════════════════════════

def _row(df, i): return df.iloc[i]

def s_orb(df, i, ts):
    # rompimento do range dos primeiros 30min (6 candles M5) do dia
    r = df.iloc[i]
    dmask = df["day"].values == r["day"]
    didx = np.where(dmask)[0]
    if len(didx) < 8 or i - didx[0] < 6: return None
    rng = df.iloc[didx[0]:didx[0]+6]
    hi, lo = rng.High.max(), rng.Low.min()
    if (hi-lo) < (r.atr or 0): return None
    c, pc = r.Close, df.iloc[i-1].Close
    if pc <= hi and c > hi + 2*ts and r.Volume >= (r.vol5 or 0)*1.5 and 40 <= r.rsi <= 70:
        sl = lo - 2*ts; ds = c-sl
        if ds>0: return ("COMPRA", sl, c+2*ds, c+3*ds)
    if pc >= lo and c < lo - 2*ts and r.Volume >= (r.vol5 or 0)*1.5 and 30 <= r.rsi <= 60:
        sl = hi + 2*ts; ds = sl-c
        if ds>0: return ("VENDA", sl, c-2*ds, c-3*ds)
    return None

def s_vwap(df, i, ts):
    if i < 6: return None
    r = df.iloc[i]
    ct = df.iloc[i-5:i]
    up = (ct.Close > ct.vwap).all(); dn = (ct.Close < ct.vwap).all()
    po, pc = df.iloc[i-1].Open, df.iloc[i-1].Close
    if up and abs(r.Low-r.vwap)<=3*ts and r.Close>=r.vwap and rev_up(r.Open,r.High,r.Low,r.Close,po,pc) \
            and r.Volume>=(r.vol10 or 0) and 40<=r.rsi<=65:
        sl=r.Low-ts; ds=r.Close-sl
        if ds>0: return ("COMPRA", sl, r.Close+2*ds, r.Close+3*ds)
    if dn and abs(r.High-r.vwap)<=3*ts and r.Close<=r.vwap and rev_dn(r.Open,r.High,r.Low,r.Close,po,pc) \
            and r.Volume>=(r.vol10 or 0) and 35<=r.rsi<=60:
        sl=r.High+ts; ds=sl-r.Close
        if ds>0: return ("VENDA", sl, r.Close-2*ds, r.Close-3*ds)
    return None

def s_ma(df, i, ts):
    if i < 55: return None
    r, p = df.iloc[i], df.iloc[i-1]
    cross_up = p.ema8<=p.ema21 and r.ema8>r.ema21
    cross_dn = p.ema8>=p.ema21 and r.ema8<r.ema21
    if cross_up and r.macd>r.macd_sig and 50<=r.rsi<=70 and r.Volume>=(r.vol10 or 0) and r.Close>r.ema50:
        sl=df.iloc[i-3:i+1].Low.min()-ts; ds=r.Close-sl
        if ds>0: return ("COMPRA", sl, r.Close+2*ds, r.Close+3*ds)
    if cross_dn and r.macd<r.macd_sig and 30<=r.rsi<=50 and r.Volume>=(r.vol10 or 0) and r.Close<r.ema50:
        sl=df.iloc[i-3:i+1].High.max()+ts; ds=sl-r.Close
        if ds>0: return ("VENDA", sl, r.Close-2*ds, r.Close-3*ds)
    return None

def s_bb(df, i, ts):
    if i < 22 or pd.isna(df.iloc[i].bb_mid): return None
    r, p = df.iloc[i], df.iloc[i-1]
    if r.Low<=r.bb_dn and r.Close>r.bb_dn and r.rsi<30 and rev_up(r.Open,r.High,r.Low,r.Close,p.Open,p.Close) \
            and r.Volume>=(r.vol10 or 0)*1.2:
        sl=r.Low-ts; ds=r.Close-sl
        if ds>0 and (r.bb_mid-r.Close)>=2*ds: return ("COMPRA", sl, r.bb_mid, r.bb_up)
    if r.High>=r.bb_up and r.Close<r.bb_up and r.rsi>70 and rev_dn(r.Open,r.High,r.Low,r.Close,p.Open,p.Close) \
            and r.Volume>=(r.vol10 or 0)*1.2:
        sl=r.High+ts; ds=sl-r.Close
        if ds>0 and (r.Close-r.bb_mid)>=2*ds: return ("VENDA", sl, r.bb_mid, r.bb_dn)
    return None

def s_pivot(df, i, ts):
    r = df.iloc[i]
    dmask = df["day"].values == r["day"]; didx = np.where(dmask)[0]
    if didx[0] == 0: return None
    prev = df.iloc[didx[0]-1]  # aprox: candle antes do dia (não é OHLC diário exato, mas serve)
    # usa o dia anterior inteiro
    pday = df["day"].values[didx[0]-1]
    pmask = df["day"].values == pday
    ph, pl, pcl = df.High[pmask].max(), df.Low[pmask].min(), df.Close[pmask].iloc[-1] if pmask.any() else (r.High, r.Low, r.Close)
    pp=(ph+pl+pcl)/3; r1=2*pp-pl; s1=2*pp-ph; r2=pp+(ph-pl); s2=pp-(ph-pl)
    c, p = r.Close, df.iloc[i-1]
    for nivel, stopn in ((s1, s2),(s2, s2-(ph-pl))):
        if abs(c-nivel)<=5*ts and r.rsi<40 and rev_up(r.Open,r.High,r.Low,r.Close,p.Open,p.Close):
            sl=stopn-2*ts; ds=c-sl
            if ds>0 and (pp-c)>=2*ds: return ("COMPRA", sl, pp, r1)
    for nivel, stopn in ((r1, r2),(r2, r2+(ph-pl))):
        if abs(c-nivel)<=5*ts and r.rsi>60 and rev_dn(r.Open,r.High,r.Low,r.Close,p.Open,p.Close):
            sl=stopn+2*ts; ds=sl-c
            if ds>0 and (c-pp)>=2*ds: return ("VENDA", sl, pp, s1)
    return None

def s_trend(df, i, ts):
    if i < 205: return None
    r, p = df.iloc[i], df.iloc[i-1]
    up = r.ema50>r.ema200 and r.Close>r.ema50
    dn = r.ema50<r.ema200 and r.Close<r.ema50
    voldim = r.Volume < (r.vol5 or 0)*0.8
    if up and abs(r.Low-r.ema21)<=3*ts and 40<=r.rsi<=55 and rev_up(r.Open,r.High,r.Low,r.Close,p.Open,p.Close) and voldim:
        sl=df.iloc[i-2:i+1].Low.min()-ts; ds=r.Close-sl
        if ds>0: return ("COMPRA", sl, r.Close+2*ds, r.Close+3*ds)
    if dn and abs(r.High-r.ema21)<=3*ts and 45<=r.rsi<=60 and rev_dn(r.Open,r.High,r.Low,r.Close,p.Open,p.Close) and voldim:
        sl=df.iloc[i-2:i+1].High.max()+ts; ds=sl-r.Close
        if ds>0: return ("VENDA", sl, r.Close-2*ds, r.Close-3*ds)
    return None

def s_scalp(df, i, ts):
    if i < 25 or pd.isna(df.iloc[i].stk): return None
    r, p = df.iloc[i], df.iloc[i-1]
    if r.ema5>r.ema10>r.ema20 and p.stk<=p.stod and r.stk>r.stod and r.stk<80 and 45<=r.rsi<=65 and r.Volume>=(r.vol20 or 0):
        sl=r.Close-3*ts; return ("COMPRA", sl, r.Close+5*ts, None)   # alvo curto fixo
    if r.ema5<r.ema10<r.ema20 and p.stk>=p.stod and r.stk<r.stod and r.stk>20 and 30<=r.rsi<=55 and r.Volume>=(r.vol20 or 0):
        sl=r.Close+3*ts; return ("VENDA", sl, r.Close-5*ts, None)
    return None

def _swing_pools(df, i, look=20, tol_frac=0.0004, _H=None, _L=None):
    a = max(0, i-look)
    highs = (_H if _H is not None else df.High.values)[a:i+1]
    lows  = (_L if _L is not None else df.Low.values)[a:i+1]
    tol = (df.iloc[i].Close or 1)*tol_frac
    # "equal highs/lows": nível tocado >=2x dentro da tolerância (vetorizado)
    dh = np.abs(highs[:, None]-highs[None, :]) <= tol
    dl = np.abs(lows[:, None]-lows[None, :]) <= tol
    eqh = highs[dh.sum(axis=1) >= 2]
    eql = lows[dl.sum(axis=1) >= 2]
    return (eqh.max() if eqh.size else None), (eql.min() if eql.size else None)

def s_ict_sweep(df, i, ts):
    if i < 25: return None
    r, p = df.iloc[i], df.iloc[i-1]
    eqh, eql = _swing_pools(df, i, 20)
    if eqh and r.High>=eqh and r.Close<eqh and r.Close<r.Open and r.Close<p.Low:
        sl=max(r.High,eqh)+2*ts; ds=sl-r.Close
        if ds>0: return ("VENDA", sl, r.Close-2*ds, r.Close-3*ds)
    if eql and r.Low<=eql and r.Close>eql and r.Close>r.Open and r.Close>p.High:
        sl=min(r.Low,eql)-2*ts; ds=r.Close-sl
        if ds>0: return ("COMPRA", sl, r.Close+2*ds, r.Close+3*ds)
    return None

def _delta(o,h,l,c,v):
    rt=h-l
    return 0 if rt==0 else ((c-l)/rt - 0.5)*2*v

def s_orderflow(df, i, ts):
    if i < 20: return None
    r = df.iloc[i]
    vt = r.vol_sum10
    if not vt or vt <= 0 or pd.isna(vt): return None
    dn = r.delta_sum10 / vt
    vwl, dev = r.va_vwl, r.va_dev
    if pd.isna(vwl) or pd.isna(dev): return None
    vah, val = vwl+dev, vwl-dev
    if dn>0.3 and r.Close>vah and r.Close>r.Open and r.Volume>=(r.vol10 or 0)*1.5:
        sl=min(val, r.Low-2*ts); ds=r.Close-sl
        if ds>0: return ("COMPRA", sl, r.Close+2*ds, r.Close+3*ds)
    if dn<-0.3 and r.Close<val and r.Close<r.Open and r.Volume>=(r.vol10 or 0)*1.5:
        sl=max(vah, r.High+2*ts); ds=sl-r.Close
        if ds>0: return ("VENDA", sl, r.Close-2*ds, r.Close-3*ds)
    return None

def s_market_profile(df, i, ts, _H=None, _L=None, _V=None):
    if i < 30: return None
    a = i-30
    H = (_H if _H is not None else df.High.values)[a:i+1]
    L = (_L if _L is not None else df.Low.values)[a:i+1]
    V = (_V if _V is not None else df.Volume.values)[a:i+1]
    lo, hi = L.min(), H.max()
    if hi<=lo: return None
    NB=30; bins=np.linspace(lo,hi,NB); step=(hi-lo)/NB
    vol=np.zeros(NB)
    # distribui o volume de cada candle nos bins entre Low e High (vetorizado)
    b0=np.clip(((L-lo)/step).astype(int),0,NB-1)
    b1=np.clip(((H-lo)/step).astype(int),0,NB-1)
    for k in range(len(V)):
        span=b1[k]-b0[k]+1
        np.add.at(vol, np.arange(b0[k],b1[k]+1), V[k]/span)
    poc=bins[int(np.argmax(vol))]
    order=np.argsort(vol)[::-1]; tot=vol.sum(); acc=0; sel=[]
    for k in order:
        acc+=vol[k]; sel.append(bins[k])
        if acc>=0.7*tot: break
    vah, val = max(sel), min(sel)
    r = df.iloc[i]
    if r.Close>poc and r.Low<=val+3*ts and r.Close>val and r.Volume>=(r.vol10 or 0):
        sl=val-2*ts; ds=r.Close-sl
        if ds>0 and (poc-r.Close)>=2*ds: return ("COMPRA", sl, poc, vah)
    if r.Close<poc and r.High>=vah-3*ts and r.Close<vah and r.Volume>=(r.vol10 or 0):
        sl=vah+2*ts; ds=sl-r.Close
        if ds>0 and (r.Close-poc)>=2*ds: return ("VENDA", sl, poc, val)
    return None

def s_fvg(df, i, ts):
    if i < 3: return None
    r = df.iloc[i]; c2 = df.iloc[i-2]
    atr = r.atr or 0
    # bullish FVG: low[i] > high[i-2]
    if r.Low > c2.High and (r.Low-c2.High) >= 0.5*atr:
        bottom, top = c2.High, r.Low
        if bottom<=r.Close<=top and (r.ema50>=r.ema200):
            sl=bottom-2*ts; ds=r.Close-sl
            if ds>0: return ("COMPRA", sl, top, top+ds*1.5)
    if r.High < c2.Low and (c2.Low-r.High) >= 0.5*atr:
        top, bottom = c2.Low, r.High
        if bottom<=r.Close<=top and (r.ema50<=r.ema200):
            sl=top+2*ts; ds=sl-r.Close
            if ds>0: return ("VENDA", sl, bottom, bottom-ds*1.5)
    return None

def s_liq_sweep(df, i, ts):
    if i < 30: return None
    r, p = df.iloc[i], df.iloc[i-1]
    eqh, eql = _swing_pools(df, i, 30)
    volok = r.Volume >= (r.vol10 or 0)*1.2
    if eqh and r.High>=eqh and r.Close<eqh and r.Close<r.Open and volok and r.Close<p.Low:
        sl=max(r.High,eqh)+2*ts; ds=sl-r.Close
        if ds>0: return ("VENDA", sl, r.Close-2*ds, r.Close-3*ds)
    if eql and r.Low<=eql and r.Close>eql and r.Close>r.Open and volok and r.Close>p.High:
        sl=min(r.Low,eql)-2*ts; ds=r.Close-sl
        if ds>0: return ("COMPRA", sl, r.Close+2*ds, r.Close+3*ds)
    return None


STRATS = {
    "ORB": s_orb, "VWAP_PB": s_vwap, "MA_CROSS": s_ma, "BOLLINGER": s_bb,
    "PIVOT": s_pivot, "TREND_PB": s_trend, "SCALP": s_scalp,
    "ICT_SWEEP": s_ict_sweep, "ORDER_FLOW": s_orderflow,
    "MARKET_PROFILE": s_market_profile, "FVG": s_fvg, "LIQ_SWEEP": s_liq_sweep,
}
NOVAS = {"ICT_SWEEP", "ORDER_FLOW", "MARKET_PROFILE", "FVG", "LIQ_SWEEP"}


# ════════════════════════════════════════════════════════════════════════════
# SIMULAÇÃO (exit da Kimi: SL / TP1→breakeven / TP2) + custos, por estratégia
# ════════════════════════════════════════════════════════════════════════════

def simulate_strat(df, symbol, strat_fn, tf_min):
    ts = TICK_SIZE.get(asset_key(symbol), 1.0)
    is_b3 = asset_key(symbol) in ("WIN", "WDO")
    trades = []
    pos = None
    n = len(df)
    for i in range(210, n):
        if pos:
            row = df.iloc[i]; buy = pos["dir"] == "COMPRA"
            hit_sl = (row.Low <= pos["sl"]) if buy else (row.High >= pos["sl"])
            hit_tp1 = (row.High >= pos["tp1"]) if buy else (row.Low <= pos["tp1"])
            hit_tp2 = pos["tp2"] and ((row.High >= pos["tp2"]) if buy else (row.Low <= pos["tp2"]))
            gross = None; stopped = True
            if hit_sl:
                gross = ((pos["sl"]-pos["entry"])/pos["risk"]) if buy else ((pos["entry"]-pos["sl"])/pos["risk"])
            elif pos["tp2"] and hit_tp2:
                gross = RR_TP2; stopped = False
            elif pos["tp2"] and hit_tp1 and not pos["be"]:
                pos["be"] = True; pos["sl"] = pos["entry"]     # breakeven
            elif not pos["tp2"] and hit_tp1:
                gross = ((pos["tp1"]-pos["entry"])/pos["risk"]) if buy else ((pos["entry"]-pos["tp1"])/pos["risk"])
                stopped = False
            # limite de tempo (day trade: zera no fim do dia da B3)
            if gross is None and is_b3 and row["day"] != pos["day"]:
                px = df.iloc[i-1].Close
                gross = ((px-pos["entry"])/pos["risk"]) if buy else ((pos["entry"]-px)/pos["risk"])
            if gross is not None:
                cost_r = roundtrip_cost_pts(symbol, stopped) / pos["risk"]
                trades.append({"ts": pos["ts"], "dir": pos["dir"],
                               "gross_R": round(gross, 3), "cost_R": round(cost_r, 3),
                               "net_R": round(gross-cost_r, 3)})
                pos = None
            continue
        sig = strat_fn(df, i, ts)
        if not sig:
            continue
        d, sl, tp1, tp2 = sig
        entry = float(df.iloc[i].Close)
        risk = abs(entry-sl)
        atr_i = float(df.iloc[i].atr or 0)
        if risk <= 0 or risk < 0.15*max(atr_i, 1e-9):
            continue
        pos = {"ts": df.index[i], "dir": d, "entry": entry, "sl": sl,
               "tp1": tp1, "tp2": tp2, "risk": risk, "be": False,
               "day": df.iloc[i]["day"]}
    return pd.DataFrame(trades)


def stats(t):
    if t.empty: return {"n": 0}
    w = t[t.net_R > 0]; l = t[t.net_R <= 0]
    gw, gl = w.net_R.sum(), -l.net_R.sum()
    eq = t.net_R.cumsum(); dd = (eq-eq.cummax()).min()
    t2 = t.copy(); t2["p"] = pd.to_datetime(t2["ts"]).dt.to_period("2M").astype(str)
    per = t2.groupby("p")["net_R"].mean(); cons = round((per > 0).sum()/len(per)*100) if len(per) else 0
    return {"n": len(t), "win": round(len(w)/len(t)*100, 1),
            "gross": round(t.gross_R.mean(), 3), "cost": round(t.cost_R.mean(), 3),
            "net": round(t.net_R.mean(), 3), "PF": round(gw/gl, 2) if gl > 0 else 99.0,
            "tot": round(t.net_R.sum(), 1), "dd": round(dd, 1), "cons": cons}


# ════════════════════════════════════════════════════════════════════════════
# Dados MT5
# ════════════════════════════════════════════════════════════════════════════

def _env(k, d=""):
    if os.getenv("KIMI_GROUP") == "crypto":
        return os.getenv("MT5_CRYPTO_"+k) or os.getenv("MT5_"+k, d)
    return os.getenv("MT5_"+k, d)


def fetch(symbol, tf_min, bars=300000):
    import MetaTrader5 as mt5
    if mt5.terminal_info() is None:
        kw = {}
        path = _env("PATH")
        if path and os.path.exists(path): kw["path"] = path
        login, pw, srv = int(_env("LOGIN", "0") or 0), _env("PASSWORD"), _env("SERVER")
        if login and pw and srv and srv.lower() not in ("metaquotes-demo","metaquotes-demo2"):
            kw.update(login=login, password=pw, server=srv)
        if not mt5.initialize(**kw):
            print("MT5 não inicializou:", mt5.last_error()); return None
    tf = {5: mt5.TIMEFRAME_M5, 15: mt5.TIMEFRAME_M15,
          30: mt5.TIMEFRAME_M30, 60: mt5.TIMEFRAME_H1,
          240: mt5.TIMEFRAME_H4}.get(tf_min, mt5.TIMEFRAME_M5)
    # resolve variantes do contínuo (XP pode expor WIN$D, WIN$N, WIN$, WINQ26...)
    # tolera o PowerShell comer o "$D" (WIN$D -> WIN): se vier a base pura de
    # um futuro B3, adiciona as variantes do contínuo automaticamente.
    cands = [symbol]
    base = symbol.replace("$D", "").replace("$N", "").replace("$", "")
    if symbol.endswith("$D") or symbol.endswith("$") or base.upper() in ("WIN", "WDO", "IND", "DOL"):
        cands += [base+"$D", base+"$N", base+"$"]
        # último recurso: qualquer contrato do papel visível no MT5
        try:
            allsy = [s.name for s in (mt5.symbols_get(base+"*") or [])]
            cands += sorted(allsy)
        except Exception:
            pass
    sel = None
    for c in dict.fromkeys(cands):   # dedup mantendo ordem
        if mt5.symbol_select(c, True):
            r = mt5.copy_rates_from_pos(c, tf, 0, 10)
            if r is not None and len(r) > 0:
                sel = c; break
    if sel is None:
        print(f"  {symbol}: não disponível (tentei {cands[:4]})"); return None
    if sel != symbol:
        print(f"  {symbol} → usando {sel}")
    symbol = sel
    rates = None
    for cnt in (bars, 200000, 150000, 100000, 50000, 20000):
        if cnt > bars: continue
        rates = mt5.copy_rates_from_pos(symbol, tf, 0, cnt)
        if rates is not None and len(rates) > 0: break
    if rates is None or len(rates) == 0:
        print(f"  {symbol}: sem candles ({mt5.last_error()})"); return None
    df = pd.DataFrame(rates); df["time"] = pd.to_datetime(df["time"], unit="s")
    df = df.set_index("time")
    vol = df["real_volume"] if df["real_volume"].sum() > 0 else df["tick_volume"]
    out = pd.DataFrame({"Open": df["open"], "High": df["high"], "Low": df["low"],
                        "Close": df["close"], "Volume": vol}, index=df.index)
    return add_indicators(out)


# ════════════════════════════════════════════════════════════════════════════
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--grupo", choices=["b3", "crypto"], default="b3")
    ap.add_argument("--symbol", default=None)
    ap.add_argument("--tf", type=int, default=5)
    ap.add_argument("--bars", type=int, default=300000)
    args = ap.parse_args()
    os.environ["KIMI_GROUP"] = args.grupo

    if args.symbol:
        symbols = [args.symbol]
    elif args.grupo == "b3":
        symbols = ["WIN$D", "WDO$D"]
    else:
        symbols = ["BTCUSD", "ETHUSD", "XAUUSD"]

    os.makedirs("logs", exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    logf = open(f"logs/backtest_kimi_{args.grupo}_{stamp}.txt", "w", encoding="utf-8")
    def out(s):
        print(s); logf.write(s+"\n"); logf.flush()

    out(f"{'#'*72}\n# BACKTEST KIMI (real, com custos) — {args.grupo} · M{args.tf} · {datetime.now():%d/%m %H:%M}\n{'#'*72}")
    ranking = []
    for sym in symbols:
        t0 = time.time()
        df = fetch(sym, args.tf, args.bars)
        if df is None or len(df) < 5000:
            out(f"\n!! {sym}: sem histórico suficiente"); continue
        anos = len(df)/ (78 if args.tf==5 else 26) / 252 if asset_key(sym) in ("WIN","WDO") else len(df)/(288 if args.tf==5 else 96)/365
        out(f"\n{'='*72}\n  {sym}: {len(df)} candles M{args.tf} (~{anos:.1f} anos)  [{df.index[0].date()} → {df.index[-1].date()}]\n{'='*72}")
        out(f"  {'ESTRATÉGIA':<16}{'n':>6}{'win%':>7}{'bruta':>8}{'custo':>7}{'LÍQ_R':>8}{'PF':>7}{'total':>8}{'DD':>8}{'cons%':>7}  veredito")
        out("  " + "-"*94)
        for name, fn in STRATS.items():
            try:
                t = simulate_strat(df, sym, fn, args.tf)
            except Exception as exc:
                out(f"  {name:<16} ERRO: {exc}"); continue
            s = stats(t)
            if s["n"] == 0:
                out(f"  {name:<16}{'0':>6}  (sem trades)"); continue
            go = "🟢 GO" if (s["net"]>=0.10 and s["PF"]>=1.25 and s["cons"]>=55) else ("🟡" if s["net"]>0.05 else "🔴")
            tag = " ⭐NOVA" if name in NOVAS else ""
            out(f"  {name:<16}{s['n']:>6}{s['win']:>7}{s['gross']:>+8.3f}{s['cost']:>7.3f}"
                f"{s['net']:>+8.3f}{s['PF']:>7}{s['tot']:>+8.1f}{s['dd']:>8.1f}{s['cons']:>7}  {go}{tag}")
            if not t.empty:
                t.to_csv(f"logs/kimi_{asset_key(sym)}_{name}.csv", index=False)
            ranking.append({"sym": asset_key(sym), "strat": name, **s, "nova": name in NOVAS})
        out(f"  ({time.time()-t0:.0f}s)")

    out(f"\n{'#'*72}\n  RANKING GERAL (líquida, mín. 60 trades)\n{'#'*72}")
    rk = pd.DataFrame(ranking)
    if not rk.empty:
        rk = rk[rk.n >= 60].sort_values("net", ascending=False)
        for _, r in rk.iterrows():
            go = "🟢 GO" if (r.net>=0.10 and r.PF>=1.25 and r["cons"]>=55) else ("🟡" if r.net>0.05 else "🔴")
            out(f"  {r.sym:<8}{r.strat:<16}n={int(r.n):<6} net {r.net:+.3f} R  PF {r.PF:<6} "
                f"tot {r.tot:+8.1f}  DD {r.dd:>7}  cons {r['cons']}%  {go}{'  ⭐' if r.nova else ''}")
    out(f"\n# FIM — envie o arquivo de log para análise")
    logf.close()


if __name__ == "__main__":
    main()
