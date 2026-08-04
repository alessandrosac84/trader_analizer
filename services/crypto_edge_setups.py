"""
crypto_edge_setups.py — setups descobertos / validados, ao vivo.

XAU_US_DRIFT (APROVADO 22/07/2026 na bateria completa):
  backtest 2,1 anos: +0,343 R/trade · PF 1,61 · consistência 77%
  walk-forward 4/5 janelas ✅ · Monte Carlo P(≤0)=0% ✅ · sensibilidade 4/5 ✅
  Lógica: fim da sessão americana (hora no quintil superior) + volume acumulado
  do dia no quintil superior + EMA200 subindo (quintil superior) → COMPRA.
  SL = 1×ATR · TP = 2×ATR · time-stop ~15h (via stale-closer do runtime).

RND_FADE_TIGHT (🟢 GO rodada v3 23/07/2026):
  XAU M15 · n=105 · +0,178 R · PF 1,32 · cons 58% · OOS +0,106 / PF 1,17
  Fade em número redondo (passo 50) com delta fraco (|dn|<0,08) e proximidade
  ≤0,15×ATR. SL = extremo ±0,3×ATR · TP = 1,5R.

RND_FADE_07 (🟢 GO rodada v4 23/07/2026):
  XAU M15 · n=86 · +0,273 R · PF 1,51 · cons 70% · OOS +0,379 / PF 1,73
  Variante mais seletiva: |dn|<0,07 · near ≤0,14×ATR.

LH_047_D18 (🟢 GO rodada v4 23/07/2026):
  XAU M15 · n=113 · +0,185 R · PF 1,45 · cons 68% · OOS +0,135 / PF 1,31
  London handoff com asia_max=0,47 · delta≥0,18 · TP=1,75×asia · 1/dia.

v5 (23/07/2026):
  RND_FADE_MT — RND_FADE_07 só seg–qui
  LH_049_D19 — handoff asia≤0,49 · delta≥0,19 · 1/dia
  XAU_INSIDE_H4 / ETH_INSIDE_H4 — inside bar + bias H4

v6 (23/07/2026):
  XAU_INS_AM — inside H4 só 09–12
  RND_FADE_065 — |dn|<0,065 · near 0,135
  RND_FADE_075_MT — |dn|<0,075 · near 0,145 · seg–qui
  LH_048_D19 — asia≤0,48 · delta≥0,19 · 1/dia

v8 (24/07/2026):
  INSIDE_1015 — inside + vol≥1,2× + H4 · 10–15h · ETH + BTC (🟢 GO)

v9 refine (24/07/2026):
  GBP_INS_AM_V13 — inside vol≥1,3× + H4 · 09–12h · GBPUSD (🟢 GO)

v10 near (24/07/2026):
  EUR_LH_047_TP165 — London handoff asia≤0,47 · δ≥0,18 · TP 1,65× · EUR (🟢 GO)
  BTC_INS_1015_V13 — inside 10–15 · vol≥1,3× + H4 · BTC (🟢 GO)

v14 BTC/ETH FDS (31/07/2026):
  BTC_PDH_24H / V13 / ASIA / NIGHT / MT — PDH/PDL + vol + H4 · 24h/Asia/noite
  BTC_NR4_24H_V14 / NR5_24H_V14 / NR5_1622 — NR + vol + H4 · 24h / 16–22h
  BTC_H4_PB_EMA_MT24 — pullback EMA + H4 · 24h · seg–qui
  ETH_INS_24H_MT / HL_24H_MT — inside/HL + H4 · 24h · seg–qui
  ETH_IMP_CONT_24H_V18 / IMP_TP25_24H — impulso continuação · 24h

v15 BTC/ETH expand (31/07/2026) — 37 GOs em services/crypto_v15_gos.py
  (HL Asia/noite/OVN, NR Lon/NY/FDS, PDH Lon/ETH Asia, VWAP cont, EMA reclaim, M30).

v16 XAU/EUR/GBP expand (31/07/2026) — 27 GOs em services/crypto_v16_gos.py
v17 ETH/EUR/GBP expand (01/08/2026) — 17 GOs em services/crypto_v17_gos.py
  (ETH INS/HL/NR; EUR NR/HL/PDH/VWAP fade/engulf M30; GBP 0 novos).

v20 BTC/ETH famílias novas (03/08/2026) — 3 GOs em services/crypto_v20_gos.py
  BTC_RSI_RECL_M60 · ETH_RSI_RECL_M60 · BTC_BBFADE_NY.

RND_FADE_T_RR18 ≡ RND_FADE_TIGHT — NÃO religado (duplicata).

Fidelidade ao backtest: avalia na última barra M15 FECHADA.
ETH_WEAK_DRIFT e BTC_SQZ_QUIET: reprovaram WF/MC — NÃO estão ligados.
"""
import numpy as np
import pandas as pd
from datetime import date

US_DRIFT_SYMBOLS = {"XAUUSD"}
RND_FADE_SYMBOLS = {"XAUUSD"}
LH_047_SYMBOLS = {"XAUUSD"}
LH_049_SYMBOLS = {"XAUUSD"}
LH_048_SYMBOLS = {"XAUUSD"}
INSIDE_H4_SYMBOLS = {"XAUUSD", "ETHUSD"}
INSIDE_1015_SYMBOLS = {"ETHUSD", "BTCUSD"}
INSIDE_1015_V13_SYMBOLS = {"BTCUSD"}
INS_0918_V13_SYMBOLS = {"BTCUSD", "ETHUSD"}      # GO v11 · inside vol1.3 · 09–18
BTC_NR5_1016_SYMBOLS = {"BTCUSD"}                 # GO v11 · NR5 + H4 · 10–16
BTC_INS_1017_V13_SYMBOLS = {"BTCUSD"}             # GO v11 · inside vol1.3 · 10–17
BTC_INS_1117_V13_SYMBOLS = {"BTCUSD"}             # GO v12 · inside vol1.3 · 11–17
BTC_INS_1218_V13_SYMBOLS = {"BTCUSD"}             # GO v12 · inside vol1.3 · 12–18
BTC_INS_V135_0916_SYMBOLS = {"BTCUSD"}            # GO v12 · inside vol1.35 · 09–16
BTC_INS_0816_V13_SYMBOLS = {"BTCUSD"}             # GO v12 · inside vol1.3 · 08–16
BTC_NR5_0915_SYMBOLS = {"BTCUSD"}                 # GO v12 · NR5 + H4 · 09–15
BTC_NR4_1016_SYMBOLS = {"BTCUSD"}                 # GO v12 · NR4 + H4 · 10–16
BTC_H4_PB_EMA_SYMBOLS = {"BTCUSD"}                # GO v12 · pullback EMA + bias H4
BTC_INS_24H_V13_SYMBOLS = {"BTCUSD"}              # GO v13 · inside vol1.3 + H4 · 24h
ETH_IMP_CONT_24H_SYMBOLS = {"ETHUSD"}             # GO v13 · impulso cont ATR×2 · 24h
# v14 BTC/ETH FDS (31/07/2026)
BTC_PDH_24H_SYMBOLS = {"BTCUSD"}
BTC_PDH_24H_V13_SYMBOLS = {"BTCUSD"}
BTC_PDH_ASIA_0008_SYMBOLS = {"BTCUSD"}
BTC_PDH_NIGHT_2010_SYMBOLS = {"BTCUSD"}
BTC_PDH_24H_MT_SYMBOLS = {"BTCUSD"}
BTC_NR4_24H_V14_SYMBOLS = {"BTCUSD"}
BTC_NR5_24H_V14_SYMBOLS = {"BTCUSD"}
BTC_NR5_1622_SYMBOLS = {"BTCUSD"}
BTC_H4_PB_EMA_MT24_SYMBOLS = {"BTCUSD"}
ETH_INS_24H_MT_SYMBOLS = {"ETHUSD"}
ETH_HL_24H_MT_SYMBOLS = {"ETHUSD"}
ETH_IMP_CONT_24H_V18_SYMBOLS = {"ETHUSD"}
ETH_IMP_TP25_24H_SYMBOLS = {"ETHUSD"}
GBP_INS_1017_V13_SYMBOLS = {"GBPUSD"}             # GO v11 · inside vol1.3 · 10–17
XAU_IMP_CONT_SYMBOLS = {"XAUUSD"}                 # GO v11 · impulso continuação ATR×2
GBP_INS_AM_V13_SYMBOLS = {"GBPUSD"}
EUR_LH_047_TP165_SYMBOLS = {"EURUSD"}
XAU_INS_AM_SYMBOLS = {"XAUUSD"}
# Mega-sweep gaps 26/07/2026
XAU_NR4_1016_H4_SYMBOLS = {"XAUUSD"}              # GO mega · NR4 + H4 · 10–16
XAU_PDH_H4_SYMBOLS = {"XAUUSD"}                   # GO mega · PDH/PDL + H4 · 10–16
XAU_PDH_1115_SYMBOLS = {"XAUUSD"}                 # GO mega · PDH/PDL + H4 · 11–15
XAU_HL_24H_SYMBOLS = {"XAUUSD"}                   # GO mega · HL chain + H4 · 24h
BTC_PDH_H4_SYMBOLS = {"BTCUSD"}                   # GO mega · PDH/PDL + H4 · 10–16
EUR_PDH_H4_SYMBOLS = {"EURUSD"}                   # GO mega · PDH/PDL + H4 · 10–16
# Mega-sweep yellows refine 27/07/2026
EUR_IMP_CONT_24H_MT_SYMBOLS = {"EURUSD"}          # GO yellow · impulso 24h · seg–qui
BARS_NEEDED = 2000
Q = 0.80                       # quintil superior

# RND_FADE_TIGHT (parâmetros do GO v3)
RND_STEP = 50.0
RND_DELTA_MAX = 0.08
RND_ATR_NEAR = 0.15
RND_WICK_MIN = 0.45
RND_SL_ATR = 0.30
RND_RR = 1.5

# Cap TP XAU estrutural (HL/PDH/NR/INS/IMP): SL barra ant. largo → TP 1.5R/2R
# pode chegar a ~10×ATR M15 (ex. live SL~45 → TP~69). Cap 30 pts (~p75–p90)
# preserva edge live HL (+0,127 R) e melhora PDH_1115 no BT; só encurta cauda.
_XAU_STRUCT_TP_CAP = 30.0

# Caps live SL/TP (pts do símbolo) — só no fill/execução; backtests/GOs intactos.
# ETH (02/08): disc M60 ATR H1 inflava SL~18/TP~36 no M15 → 12/20 (~2×ATR scalp).
# BTC (03/08): trade live SL~1191 / TP~1827 (~8–13×ATR M15≈143) — MFE real ~380 pts.
#   Cap SL 1000 / TP 1200 ≈ 7–8×ATR teto operacional (scalp M15; user 800–1200 / 1000–1500).
# EUR/GBP: 30/45 pips — evita TP estrutural absurdo em FX.
# XAU: TP estrutural via _XAU_STRUCT_TP_CAP nos GOs (30 pts); sem SL cap global aqui.
_ETH_SL_CAP = 12.0
_ETH_TP_CAP = 20.0
_BTC_SL_CAP = 1000.0
_BTC_TP_CAP = 1200.0
_FX_SL_CAP = 0.0030   # 30 pips
_FX_TP_CAP = 0.0045   # 45 pips

# symbol -> (sl_cap, tp_cap) | None
LIVE_SL_TP_CAPS = {
    "ETHUSD": (_ETH_SL_CAP, _ETH_TP_CAP),
    "BTCUSD": (_BTC_SL_CAP, _BTC_TP_CAP),
    "EURUSD": (_FX_SL_CAP, _FX_TP_CAP),
    "GBPUSD": (_FX_SL_CAP, _FX_TP_CAP),
}


def live_caps_for(symbol: str):
    """Retorna (sl_cap, tp_cap) ou None se o ativo não tem cap live."""
    return LIVE_SL_TP_CAPS.get((symbol or "").upper().strip())


def _apply_tp_cap(sig, entry: float, cap: float):
    """Limita |entry−tp| a `cap` pts (mantém SL/risco; só encurta alvo)."""
    if not sig or cap is None or cap <= 0:
        return sig
    d, sl, tp = sig
    entry = float(entry)
    tp = float(tp)
    if abs(tp - entry) <= cap + 1e-9:
        return sig
    new_tp = entry + cap if d == "COMPRA" else entry - cap
    return (d, float(sl), new_tp)


def apply_live_caps(symbol: str, direction: str, entry: float, sl: float, tp: float,
                    sl_cap: float = None, tp_cap: float = None):
    """Aperta SL e encurta TP no live (pts do símbolo). Retorna (sl, tp)."""
    if entry is None or sl is None or tp is None:
        return sl, tp
    caps = live_caps_for(symbol)
    if sl_cap is None and tp_cap is None and not caps:
        return sl, tp
    entry = float(entry)
    sl, tp = float(sl), float(tp)
    d = (direction or "").upper().strip()
    sc = float(sl_cap if sl_cap is not None else (caps[0] if caps else 0))
    tc = float(tp_cap if tp_cap is not None else (caps[1] if caps else 0))
    if sc > 0 and abs(sl - entry) > sc + 1e-9:
        sl = entry - sc if d == "COMPRA" else entry + sc
    if tc > 0 and abs(tp - entry) > tc + 1e-9:
        tp = entry + tc if d == "COMPRA" else entry - tc
    return sl, tp


def apply_eth_live_caps(direction: str, entry: float, sl: float, tp: float,
                        sl_cap: float = None, tp_cap: float = None):
    """Compat: caps ETH (= apply_live_caps ETHUSD)."""
    return apply_live_caps("ETHUSD", direction, entry, sl, tp,
                           sl_cap=sl_cap, tp_cap=tp_cap)


def cap_live_signal(symbol: str, sig, entry: float,
                    sl_cap: float = None, tp_cap: float = None):
    """Aplica caps live a um tuple (acao, sl, tp) se o símbolo tiver cap."""
    if not sig:
        return sig
    if sl_cap is None and tp_cap is None and not live_caps_for(symbol):
        return sig
    d, sl, tp = sig
    sl2, tp2 = apply_live_caps(symbol, d, entry, sl, tp, sl_cap=sl_cap, tp_cap=tp_cap)
    return (d, float(sl2), float(tp2))


def cap_eth_signal(sig, entry: float, sl_cap: float = None, tp_cap: float = None):
    """Compat: aplica caps ETH a um tuple (acao, sl, tp)."""
    return cap_live_signal("ETHUSD", sig, entry, sl_cap=sl_cap, tp_cap=tp_cap)


# RND_FADE_07 (GO v4) / RND_FADE_MT (GO v5 = 07 + seg–qui)
RND07_DELTA_MAX = 0.07
RND07_ATR_NEAR = 0.14

# RND_FADE_065 / RND_FADE_075_MT (GO v6)
RND065_DELTA_MAX = 0.065
RND065_ATR_NEAR = 0.135
RND075_DELTA_MAX = 0.075
RND075_ATR_NEAR = 0.145

# LH_047_D18 (GO v4)
SESS_ASIA = (1, 9)
SESS_LONDON = (9, 11)
LH047_ASIA_MAX = 0.47
LH047_DELTA_MIN = 0.18
LH047_TP_MULT = 1.75

# LH_049_D19 (GO v5)
LH049_ASIA_MAX = 0.49
LH049_DELTA_MIN = 0.19
LH049_TP_MULT = 1.75

# LH_048_D19 (GO v6)
LH048_ASIA_MAX = 0.48
LH048_DELTA_MIN = 0.19
LH048_TP_MULT = 1.75

_lh047_day = {}  # symbol -> date ISO (1 trade/dia)
_lh049_day = {}
_lh048_day = {}
_eur_lh_tp165_day = {}

def us_drift_enabled(symbol: str) -> bool:
    return (symbol or "").upper().strip() in US_DRIFT_SYMBOLS


def rnd_fade_enabled(symbol: str) -> bool:
    return (symbol or "").upper().strip() in RND_FADE_SYMBOLS


def lh_047_enabled(symbol: str) -> bool:
    return (symbol or "").upper().strip() in LH_047_SYMBOLS


def lh_049_enabled(symbol: str) -> bool:
    return (symbol or "").upper().strip() in LH_049_SYMBOLS


def lh_048_enabled(symbol: str) -> bool:
    return (symbol or "").upper().strip() in LH_048_SYMBOLS


def inside_h4_enabled(symbol: str) -> bool:
    return (symbol or "").upper().strip() in INSIDE_H4_SYMBOLS


def inside_1015_enabled(symbol: str) -> bool:
    return (symbol or "").upper().strip() in INSIDE_1015_SYMBOLS


def inside_1015_v13_enabled(symbol: str) -> bool:
    return (symbol or "").upper().strip() in INSIDE_1015_V13_SYMBOLS


def ins_0918_v13_enabled(symbol: str) -> bool:
    return (symbol or "").upper().strip() in INS_0918_V13_SYMBOLS


def btc_nr5_1016_enabled(symbol: str) -> bool:
    return (symbol or "").upper().strip() in BTC_NR5_1016_SYMBOLS


def btc_ins_1017_v13_enabled(symbol: str) -> bool:
    return (symbol or "").upper().strip() in BTC_INS_1017_V13_SYMBOLS


def btc_ins_1117_v13_enabled(symbol: str) -> bool:
    return (symbol or "").upper().strip() in BTC_INS_1117_V13_SYMBOLS


def btc_ins_1218_v13_enabled(symbol: str) -> bool:
    return (symbol or "").upper().strip() in BTC_INS_1218_V13_SYMBOLS


def btc_ins_v135_0916_enabled(symbol: str) -> bool:
    return (symbol or "").upper().strip() in BTC_INS_V135_0916_SYMBOLS


def btc_ins_0816_v13_enabled(symbol: str) -> bool:
    return (symbol or "").upper().strip() in BTC_INS_0816_V13_SYMBOLS


def btc_nr5_0915_enabled(symbol: str) -> bool:
    return (symbol or "").upper().strip() in BTC_NR5_0915_SYMBOLS


def btc_nr4_1016_enabled(symbol: str) -> bool:
    return (symbol or "").upper().strip() in BTC_NR4_1016_SYMBOLS


def btc_h4_pb_ema_enabled(symbol: str) -> bool:
    return (symbol or "").upper().strip() in BTC_H4_PB_EMA_SYMBOLS


def btc_ins_24h_v13_enabled(symbol: str) -> bool:
    return (symbol or "").upper().strip() in BTC_INS_24H_V13_SYMBOLS


def eth_imp_cont_24h_enabled(symbol: str) -> bool:
    return (symbol or "").upper().strip() in ETH_IMP_CONT_24H_SYMBOLS


def btc_pdh_24h_enabled(symbol: str) -> bool:
    return (symbol or "").upper().strip() in BTC_PDH_24H_SYMBOLS


def btc_pdh_24h_v13_enabled(symbol: str) -> bool:
    return (symbol or "").upper().strip() in BTC_PDH_24H_V13_SYMBOLS


def btc_pdh_asia_0008_enabled(symbol: str) -> bool:
    return (symbol or "").upper().strip() in BTC_PDH_ASIA_0008_SYMBOLS


def btc_pdh_night_2010_enabled(symbol: str) -> bool:
    return (symbol or "").upper().strip() in BTC_PDH_NIGHT_2010_SYMBOLS


def btc_pdh_24h_mt_enabled(symbol: str) -> bool:
    return (symbol or "").upper().strip() in BTC_PDH_24H_MT_SYMBOLS


def btc_nr4_24h_v14_enabled(symbol: str) -> bool:
    return (symbol or "").upper().strip() in BTC_NR4_24H_V14_SYMBOLS


def btc_nr5_24h_v14_enabled(symbol: str) -> bool:
    return (symbol or "").upper().strip() in BTC_NR5_24H_V14_SYMBOLS


def btc_nr5_1622_enabled(symbol: str) -> bool:
    return (symbol or "").upper().strip() in BTC_NR5_1622_SYMBOLS


def btc_h4_pb_ema_mt24_enabled(symbol: str) -> bool:
    return (symbol or "").upper().strip() in BTC_H4_PB_EMA_MT24_SYMBOLS


def eth_ins_24h_mt_enabled(symbol: str) -> bool:
    return (symbol or "").upper().strip() in ETH_INS_24H_MT_SYMBOLS


def eth_hl_24h_mt_enabled(symbol: str) -> bool:
    return (symbol or "").upper().strip() in ETH_HL_24H_MT_SYMBOLS


def eth_imp_cont_24h_v18_enabled(symbol: str) -> bool:
    return (symbol or "").upper().strip() in ETH_IMP_CONT_24H_V18_SYMBOLS


def eth_imp_tp25_24h_enabled(symbol: str) -> bool:
    return (symbol or "").upper().strip() in ETH_IMP_TP25_24H_SYMBOLS


def gbp_ins_1017_v13_enabled(symbol: str) -> bool:
    return (symbol or "").upper().strip() in GBP_INS_1017_V13_SYMBOLS


def xau_imp_cont_enabled(symbol: str) -> bool:
    return (symbol or "").upper().strip() in XAU_IMP_CONT_SYMBOLS


def gbp_ins_am_v13_enabled(symbol: str) -> bool:
    return (symbol or "").upper().strip() in GBP_INS_AM_V13_SYMBOLS


def eur_lh_047_tp165_enabled(symbol: str) -> bool:
    return (symbol or "").upper().strip() in EUR_LH_047_TP165_SYMBOLS


def xau_ins_am_enabled(symbol: str) -> bool:
    return (symbol or "").upper().strip() in XAU_INS_AM_SYMBOLS


def xau_nr4_1016_h4_enabled(symbol: str) -> bool:
    return (symbol or "").upper().strip() in XAU_NR4_1016_H4_SYMBOLS


def xau_pdh_h4_enabled(symbol: str) -> bool:
    return (symbol or "").upper().strip() in XAU_PDH_H4_SYMBOLS


def xau_pdh_1115_enabled(symbol: str) -> bool:
    return (symbol or "").upper().strip() in XAU_PDH_1115_SYMBOLS


def xau_hl_24h_enabled(symbol: str) -> bool:
    return (symbol or "").upper().strip() in XAU_HL_24H_SYMBOLS


def btc_pdh_h4_enabled(symbol: str) -> bool:
    return (symbol or "").upper().strip() in BTC_PDH_H4_SYMBOLS


def eur_pdh_h4_enabled(symbol: str) -> bool:
    return (symbol or "").upper().strip() in EUR_PDH_H4_SYMBOLS


def eur_imp_cont_24h_mt_enabled(symbol: str) -> bool:
    return (symbol or "").upper().strip() in EUR_IMP_CONT_24H_MT_SYMBOLS


def lh_047_available_today(symbol: str) -> bool:
    """LH_047_D18 = one_per_day no backtest."""
    sym = (symbol or "").upper().strip()
    return _lh047_day.get(sym) != date.today().isoformat()


def lh_047_mark_today(symbol: str) -> None:
    _lh047_day[(symbol or "").upper().strip()] = date.today().isoformat()


def lh_049_available_today(symbol: str) -> bool:
    sym = (symbol or "").upper().strip()
    return _lh049_day.get(sym) != date.today().isoformat()


def lh_049_mark_today(symbol: str) -> None:
    _lh049_day[(symbol or "").upper().strip()] = date.today().isoformat()


def lh_048_available_today(symbol: str) -> bool:
    sym = (symbol or "").upper().strip()
    return _lh048_day.get(sym) != date.today().isoformat()


def lh_048_mark_today(symbol: str) -> None:
    _lh048_day[(symbol or "").upper().strip()] = date.today().isoformat()


def eur_lh_047_tp165_available_today(symbol: str) -> bool:
    sym = (symbol or "").upper().strip()
    return _eur_lh_tp165_day.get(sym) != date.today().isoformat()


def eur_lh_047_tp165_mark_today(symbol: str) -> None:
    _eur_lh_tp165_day[(symbol or "").upper().strip()] = date.today().isoformat()


def xau_us_drift_signal(candles, symbol: str = "XAUUSD"):
    """
    candles: list[dict] {time, open, high, low, close, volume} (get_candles, M15).
    Retorna ("COMPRA", sl, tp) ou None. Avalia a última barra FECHADA.
    """
    if not candles or len(candles) < 600:
        return None
    df = pd.DataFrame(candles).rename(columns={
        "open": "Open", "high": "High", "low": "Low",
        "close": "Close", "volume": "Volume"})
    for col in ("Open", "High", "Low", "Close", "Volume"):
        if col not in df.columns:
            return None
    df["dt"] = pd.to_datetime(df["time"], unit="s")
    df = df.set_index("dt").iloc[:-1]              # descarta barra em formação
    if len(df) < 600:
        return None

    c, h, l, v = df.Close, df.High, df.Low, df.Volume
    tr = pd.concat([(h - l), (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / 14, adjust=False).mean()
    day = pd.Series(df.index.date, index=df.index)
    vol_day_cum_rel = v.groupby(day).cumsum() / (v.rolling(500, min_periods=50).mean() + 1e-12)
    ema200 = c.ewm(span=200, adjust=False).mean()
    ema200_slope = (ema200 - ema200.shift(5)) / (ema200.shift(5).abs() + 1e-12)
    hours = pd.Series(df.index.hour, index=df.index)

    thr_vol = vol_day_cum_rel.quantile(Q)
    thr_slope = ema200_slope.quantile(Q)
    thr_hour = hours.quantile(Q)

    r_vol = float(vol_day_cum_rel.iloc[-1])
    r_slope = float(ema200_slope.iloc[-1])
    r_hour = int(df.index[-1].hour)
    a = float(atr.iloc[-1])
    px = float(c.iloc[-1])
    if not np.isfinite(a) or a <= 0:
        return None
    if r_vol > thr_vol and r_slope > thr_slope and r_hour > thr_hour:
        return ("COMPRA", px - a, px + 2 * a)
    return None


def xau_rnd_fade_tight_signal(candles, symbol: str = "XAUUSD"):
    """
    Fade em redondo com delta fraco (RND_FADE_TIGHT).
    Retorna ("COMPRA"|"VENDA", sl, tp) ou None. Última barra M15 FECHADA.
    """
    if not candles or len(candles) < 40:
        return None
    df = pd.DataFrame(candles).rename(columns={
        "open": "Open", "high": "High", "low": "Low",
        "close": "Close", "volume": "Volume"})
    for col in ("Open", "High", "Low", "Close", "Volume"):
        if col not in df.columns:
            return None
    df["dt"] = pd.to_datetime(df["time"], unit="s")
    df = df.set_index("dt").iloc[:-1]
    if len(df) < 30:
        return None

    c, h, l, o, v = df.Close, df.High, df.Low, df.Open, df.Volume
    tr = pd.concat([(h - l), (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / 14, adjust=False).mean()
    rng = (h - l).replace(0, np.nan)
    delta = ((c - l) / rng - 0.5) * 2 * v
    delta_sum10 = delta.rolling(10).sum()
    vol_sum10 = v.rolling(10).sum()
    dn = delta_sum10 / vol_sum10.replace(0, np.nan)

    r_c = float(c.iloc[-1])
    r_h = float(h.iloc[-1])
    r_l = float(l.iloc[-1])
    r_o = float(o.iloc[-1])
    a = float(atr.iloc[-1])
    dnv = float(dn.iloc[-1]) if np.isfinite(dn.iloc[-1]) else 0.0
    if not np.isfinite(a) or a <= 0:
        return None

    lvl = round(r_c / RND_STEP) * RND_STEP
    if lvl <= 0 or abs(r_c - lvl) > RND_ATR_NEAR * a:
        return None
    if abs(dnv) >= RND_DELTA_MAX:
        return None

    bar_rng = max(r_h - r_l, 1e-9)
    up_wick = r_h - max(r_c, r_o)
    dn_wick = min(r_c, r_o) - r_l

    if r_h >= lvl and up_wick >= RND_WICK_MIN * bar_rng and r_c < lvl:
        sl = r_h + RND_SL_ATR * a
        risk = sl - r_c
        if risk > 0:
            return ("VENDA", sl, r_c - RND_RR * risk)
    if r_l <= lvl and dn_wick >= RND_WICK_MIN * bar_rng and r_c > lvl:
        sl = r_l - RND_SL_ATR * a
        risk = r_c - sl
        if risk > 0:
            return ("COMPRA", sl, r_c + RND_RR * risk)
    return None


def _rnd_fade_core(candles, delta_max: float, atr_near: float):
    """Núcleo compartilhado RND_FADE_* (última barra M15 FECHADA)."""
    if not candles or len(candles) < 40:
        return None
    df = pd.DataFrame(candles).rename(columns={
        "open": "Open", "high": "High", "low": "Low",
        "close": "Close", "volume": "Volume"})
    for col in ("Open", "High", "Low", "Close", "Volume"):
        if col not in df.columns:
            return None
    df["dt"] = pd.to_datetime(df["time"], unit="s")
    df = df.set_index("dt").iloc[:-1]
    if len(df) < 30:
        return None

    c, h, l, o, v = df.Close, df.High, df.Low, df.Open, df.Volume
    tr = pd.concat([(h - l), (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / 14, adjust=False).mean()
    rng = (h - l).replace(0, np.nan)
    delta = ((c - l) / rng - 0.5) * 2 * v
    delta_sum10 = delta.rolling(10).sum()
    vol_sum10 = v.rolling(10).sum()
    dn = delta_sum10 / vol_sum10.replace(0, np.nan)

    r_c = float(c.iloc[-1])
    r_h = float(h.iloc[-1])
    r_l = float(l.iloc[-1])
    r_o = float(o.iloc[-1])
    a = float(atr.iloc[-1])
    dnv = float(dn.iloc[-1]) if np.isfinite(dn.iloc[-1]) else 0.0
    if not np.isfinite(a) or a <= 0:
        return None

    lvl = round(r_c / RND_STEP) * RND_STEP
    if lvl <= 0 or abs(r_c - lvl) > atr_near * a:
        return None
    if abs(dnv) >= delta_max:
        return None

    bar_rng = max(r_h - r_l, 1e-9)
    up_wick = r_h - max(r_c, r_o)
    dn_wick = min(r_c, r_o) - r_l

    if r_h >= lvl and up_wick >= RND_WICK_MIN * bar_rng and r_c < lvl:
        sl = r_h + RND_SL_ATR * a
        risk = sl - r_c
        if risk > 0:
            return ("VENDA", sl, r_c - RND_RR * risk)
    if r_l <= lvl and dn_wick >= RND_WICK_MIN * bar_rng and r_c > lvl:
        sl = r_l - RND_SL_ATR * a
        risk = r_c - sl
        if risk > 0:
            return ("COMPRA", sl, r_c + RND_RR * risk)
    return None


def xau_rnd_fade_07_signal(candles, symbol: str = "XAUUSD"):
    """RND_FADE_07 — GO v4 (delta_max 0,07 · atr_near 0,14)."""
    return _rnd_fade_core(candles, RND07_DELTA_MAX, RND07_ATR_NEAR)


def xau_rnd_fade_mt_signal(candles, symbol: str = "XAUUSD"):
    """RND_FADE_MT — GO v5 (= RND_FADE_07 só seg–qui)."""
    if not candles or len(candles) < 40:
        return None
    try:
        df = pd.DataFrame(candles)
        df["dt"] = pd.to_datetime(df["time"], unit="s")
        closed = df.iloc[:-1]
        if closed.empty:
            return None
        if int(closed["dt"].iloc[-1].dayofweek) not in (0, 1, 2, 3):
            return None
    except Exception:
        return None
    return _rnd_fade_core(candles, RND07_DELTA_MAX, RND07_ATR_NEAR)


def xau_rnd_fade_065_signal(candles, symbol: str = "XAUUSD"):
    """RND_FADE_065 — GO v6."""
    return _rnd_fade_core(candles, RND065_DELTA_MAX, RND065_ATR_NEAR)


def xau_rnd_fade_075_mt_signal(candles, symbol: str = "XAUUSD"):
    """RND_FADE_075_MT — GO v6."""
    if not candles or len(candles) < 40:
        return None
    try:
        df = pd.DataFrame(candles)
        df["dt"] = pd.to_datetime(df["time"], unit="s")
        closed = df.iloc[:-1]
        if closed.empty:
            return None
        if int(closed["dt"].iloc[-1].dayofweek) not in (0, 1, 2, 3):
            return None
    except Exception:
        return None
    return _rnd_fade_core(candles, RND075_DELTA_MAX, RND075_ATR_NEAR)


def _london_handoff_core(candles, asia_max: float, delta_min: float, tp_mult: float):
    """Núcleo LH_* (última barra M15 FECHADA)."""
    if not candles or len(candles) < 60:
        return None
    df = pd.DataFrame(candles).rename(columns={
        "open": "Open", "high": "High", "low": "Low",
        "close": "Close", "volume": "Volume"})
    if "time" not in df.columns:
        return None
    for col in ("Open", "High", "Low", "Close", "Volume"):
        if col not in df.columns:
            return None
    df["dt"] = pd.to_datetime(df["time"], unit="s")
    df = df.set_index("dt").iloc[:-1]
    if len(df) < 60:
        return None

    c, h, l, v = df.Close, df.High, df.Low, df.Volume
    tr = pd.concat([(h - l), (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / 14, adjust=False).mean()
    rng = (h - l).replace(0, np.nan)
    delta = ((c - l) / rng - 0.5) * 2 * v
    delta_sum10 = delta.rolling(10).sum()
    vol_sum10 = v.rolling(10).sum()

    r = df.iloc[-1]
    a = float(atr.iloc[-1])
    if not np.isfinite(a) or a <= 0:
        return None
    hh = df.index[-1].hour + df.index[-1].minute / 60.0
    if not (SESS_LONDON[0] <= hh < SESS_LONDON[1]):
        return None

    today = df.index[-1].date()
    hrs = df.index.hour + df.index.minute / 60.0
    is_today = np.array([d.date() == today for d in df.index])
    asia = df[is_today & (hrs >= SESS_ASIA[0]) & (hrs < SESS_ASIA[1])]
    if asia.empty:
        return None
    asia_hi, asia_lo = float(asia.High.max()), float(asia.Low.min())
    asia_rng = asia_hi - asia_lo
    if asia_rng <= 0:
        return None
    asia_atr_ratio = asia_rng / (a * 16)
    if asia_atr_ratio > asia_max:
        return None

    vt = float(vol_sum10.iloc[-1]) if np.isfinite(vol_sum10.iloc[-1]) else 0.0
    dn = float(delta_sum10.iloc[-1] / vt) if vt > 0 else 0.0
    if abs(dn) < delta_min:
        return None

    prev = df.iloc[-2]
    entry = float(r.Close)
    if r.Close > asia_hi and prev.Close <= asia_hi and dn > delta_min:
        return ("COMPRA", asia_lo, entry + tp_mult * asia_rng)
    if r.Close < asia_lo and prev.Close >= asia_lo and dn < -delta_min:
        return ("VENDA", asia_hi, entry - tp_mult * asia_rng)
    return None


def xau_lh_047_d18_signal(candles, symbol: str = "XAUUSD"):
    """LH_047_D18 — London handoff GO v4."""
    return _london_handoff_core(candles, LH047_ASIA_MAX, LH047_DELTA_MIN, LH047_TP_MULT)


def xau_lh_049_d19_signal(candles, symbol: str = "XAUUSD"):
    """LH_049_D19 — London handoff GO v5 (asia≤0,49 · delta≥0,19)."""
    return _london_handoff_core(candles, LH049_ASIA_MAX, LH049_DELTA_MIN, LH049_TP_MULT)


def xau_lh_048_d19_signal(candles, symbol: str = "XAUUSD"):
    """LH_048_D19 — London handoff GO v6 (asia≤0,48 · delta≥0,19)."""
    return _london_handoff_core(candles, LH048_ASIA_MAX, LH048_DELTA_MIN, LH048_TP_MULT)


def inside_h4_signal(candles, symbol: str = "XAUUSD"):
    """
    XAU_INSIDE_H4 / ETH_INSIDE_H4 — GO v5.
    Inside bar 09–14 servidor + volume ≥1,2× + rompimento a favor do H4.
    XAU: TP cap estrutural (ver _XAU_STRUCT_TP_CAP).
    """
    cap = _XAU_STRUCT_TP_CAP if (symbol or "").upper().strip() == "XAUUSD" else None
    return _inside_h4_core(candles, hh0=9.0, hh1=14.0, tp_cap=cap)


def inside_1015_signal(candles, symbol: str = "ETHUSD"):
    """
    ETH_INS_1015 / BTC_INS_1015 — GO v8.
    Inside + vol≥1,2× + H4 · janela 10–15h.
    """
    return _inside_h4_core(candles, hh0=10.0, hh1=15.0, vol_mult=1.2)


def inside_1015_v13_signal(candles, symbol: str = "BTCUSD"):
    """
    BTC_INS_1015_V13 — GO v10.
    Inside + vol≥1,3× + H4 · 10–15h.
    """
    return _inside_h4_core(candles, hh0=10.0, hh1=15.0, vol_mult=1.3)


def ins_0918_v13_signal(candles, symbol: str = "BTCUSD"):
    """INS_0918_V13 — GO v11 · inside vol≥1,3× + H4 · 09–18h (BTC/ETH)."""
    return _inside_h4_core(candles, hh0=9.0, hh1=18.0, vol_mult=1.3)


def btc_ins_1017_v13_signal(candles, symbol: str = "BTCUSD"):
    """BTC_INS_1017_V13 — GO v11 · inside vol≥1,3× + H4 · 10–17h."""
    return _inside_h4_core(candles, hh0=10.0, hh1=17.0, vol_mult=1.3)


def gbp_ins_1017_v13_signal(candles, symbol: str = "GBPUSD"):
    """GBP_INS_1017_V13 — GO v11 · inside vol≥1,3× + H4 · 10–17h."""
    return _inside_h4_core(candles, hh0=10.0, hh1=17.0, vol_mult=1.3)


def gbp_ins_am_v13_signal(candles, symbol: str = "GBPUSD"):
    """
    GBP_INS_AM_V13 — GO v9 refine.
    Inside + vol≥1,3× + H4 · 09–12h.
    """
    return _inside_h4_core(candles, hh0=9.0, hh1=12.0, vol_mult=1.3)


def eur_lh_047_tp165_signal(candles, symbol: str = "EURUSD"):
    """
    EUR_LH_047_TP165 — GO v10.
    London handoff asia≤0,47 · delta≥0,18 · TP 1,65×asia · 1/dia.
    """
    return _london_handoff_core(candles, 0.47, 0.18, 1.65)


def xau_ins_am_signal(candles, symbol: str = "XAUUSD"):
    """XAU_INS_AM — GO v6 · inside H4 só 09–12 · TP≤30 pts."""
    return _inside_h4_core(candles, hh0=9.0, hh1=12.0, vol_mult=1.2,
                           tp_cap=_XAU_STRUCT_TP_CAP)


def btc_nr5_1016_signal(candles, symbol: str = "BTCUSD"):
    """BTC_NR5_1016_H4 — GO v11 · NR5 + vol≥1,3× + H4 · 10–16h."""
    return _nr_h4_core(candles, n=5, vol_mult=1.3, hh0=10.0, hh1=16.0)


def btc_nr5_0915_signal(candles, symbol: str = "BTCUSD"):
    """BTC_NR5_0915_V13 — GO v12 · NR5 + vol≥1,3× + H4 · 09–15h."""
    return _nr_h4_core(candles, n=5, vol_mult=1.3, hh0=9.0, hh1=15.0)


def btc_nr4_1016_signal(candles, symbol: str = "BTCUSD"):
    """BTC_NR4_1016_H4 — GO v12 · NR4 + vol≥1,3× + H4 · 10–16h."""
    return _nr_h4_core(candles, n=4, vol_mult=1.3, hh0=10.0, hh1=16.0)


def btc_ins_1117_v13_signal(candles, symbol: str = "BTCUSD"):
    return _inside_h4_core(candles, hh0=11.0, hh1=17.0, vol_mult=1.3)


def btc_ins_1218_v13_signal(candles, symbol: str = "BTCUSD"):
    return _inside_h4_core(candles, hh0=12.0, hh1=18.0, vol_mult=1.3)


def btc_ins_v135_0916_signal(candles, symbol: str = "BTCUSD"):
    return _inside_h4_core(candles, hh0=9.0, hh1=16.0, vol_mult=1.35)


def btc_ins_0816_v13_signal(candles, symbol: str = "BTCUSD"):
    return _inside_h4_core(candles, hh0=8.0, hh1=16.0, vol_mult=1.3)


def btc_ins_24h_v13_signal(candles, symbol: str = "BTCUSD"):
    """BTC_INS_24H_V13 — GO v13 · inside vol≥1,3× + H4 · 24h (noite/FDS)."""
    return _inside_h4_core(candles, hh0=0.0, hh1=24.0, vol_mult=1.3)


def eur_imp_cont_24h_mt_signal(candles, symbol: str = "EURUSD"):
    """EUR_IMP_CONT_24H_MT — GO yellow refine · impulso ATR×2 · 24h · seg–qui."""
    return _impulse_cont_core(candles, atr_mult=2.0, vol_mult=1.5,
                              hh0=0.0, hh1=24.0, tp_r=2.0, mt_only=True)


def eth_imp_cont_24h_signal(candles, symbol: str = "ETHUSD"):
    """ETH_IMP_CONT_24H — GO v13 · continuação após impulso ATR×2 + vol · 24h."""
    return _impulse_cont_core(candles, atr_mult=2.0, vol_mult=1.5,
                              hh0=0.0, hh1=24.0, tp_r=2.0)


def btc_pdh_24h_signal(candles, symbol: str = "BTCUSD"):
    """BTC_PDH_24H — GO v14 · PDH/PDL + vol≥1,2× + H4 · 24h."""
    return _pdh_h4_core(candles, vol_mult=1.2, hh0=0.0, hh1=24.0)


def btc_pdh_24h_v13_signal(candles, symbol: str = "BTCUSD"):
    """BTC_PDH_24H_V13 — GO v14 · PDH/PDL + vol≥1,3× + H4 · 24h."""
    return _pdh_h4_core(candles, vol_mult=1.3, hh0=0.0, hh1=24.0)


def btc_pdh_asia_0008_signal(candles, symbol: str = "BTCUSD"):
    """BTC_PDH_ASIA_0008 — GO v14 · PDH/PDL + vol≥1,2× + H4 · 00–08h."""
    return _pdh_h4_core(candles, vol_mult=1.2, hh0=0.0, hh1=8.0)


def btc_pdh_night_2010_signal(candles, symbol: str = "BTCUSD"):
    """BTC_PDH_NIGHT_2010 — GO v14 · PDH/PDL + vol≥1,2× + H4 · 20–10h."""
    return _pdh_h4_core(candles, vol_mult=1.2, hh0=20.0, hh1=10.0)


def btc_pdh_24h_mt_signal(candles, symbol: str = "BTCUSD"):
    """BTC_PDH_24H_MT — GO v14 · PDH/PDL · 24h · seg–qui."""
    df = _candles_df(candles, 100)
    if df is None or int(df.index[-1].dayofweek) not in (0, 1, 2, 3):
        return None
    return _pdh_h4_core(candles, vol_mult=1.2, hh0=0.0, hh1=24.0)


def btc_nr4_24h_v14_signal(candles, symbol: str = "BTCUSD"):
    """BTC_NR4_24H_V14 — GO v14 · NR4 + vol≥1,4× + H4 · 24h."""
    return _nr_h4_core(candles, n=4, vol_mult=1.4, hh0=0.0, hh1=24.0)


def btc_nr5_24h_v14_signal(candles, symbol: str = "BTCUSD"):
    """BTC_NR5_24H_V14 — GO v14 · NR5 + vol≥1,4× + H4 · 24h."""
    return _nr_h4_core(candles, n=5, vol_mult=1.4, hh0=0.0, hh1=24.0)


def btc_nr5_1622_signal(candles, symbol: str = "BTCUSD"):
    """BTC_NR5_1622 — GO v14 · NR5 + vol≥1,3× + H4 · 16–22h."""
    return _nr_h4_core(candles, n=5, vol_mult=1.3, hh0=16.0, hh1=22.0)


def eth_ins_24h_mt_signal(candles, symbol: str = "ETHUSD"):
    """ETH_INS_24H_MT — GO v14 · inside vol≥1,3× + H4 · 24h · seg–qui."""
    df = _candles_df(candles, 220)
    if df is None or int(df.index[-1].dayofweek) not in (0, 1, 2, 3):
        return None
    return _inside_h4_core(candles, hh0=0.0, hh1=24.0, vol_mult=1.3)


def eth_hl_24h_mt_signal(candles, symbol: str = "ETHUSD"):
    """ETH_HL_24H_MT — GO v14 · HL/LH + vol≥1,3× + H4 · 24h · seg–qui."""
    df = _candles_df(candles, 30)
    if df is None or int(df.index[-1].dayofweek) not in (0, 1, 2, 3):
        return None
    return _hl_h4_core(candles, vol_mult=1.3, hh0=0.0, hh1=24.0)


def eth_imp_cont_24h_v18_signal(candles, symbol: str = "ETHUSD"):
    """ETH_IMP_CONT_24H_V18 — GO v14 · impulso ATR×1,8 + vol≥1,6× · 24h."""
    return _impulse_cont_core(candles, atr_mult=1.8, vol_mult=1.6,
                              hh0=0.0, hh1=24.0, tp_r=2.0)


def eth_imp_tp25_24h_signal(candles, symbol: str = "ETHUSD"):
    """ETH_IMP_TP25_24H — GO v14 · impulso ATR×2 · TP 2,5R · 24h."""
    return _impulse_cont_core(candles, atr_mult=2.0, vol_mult=1.5,
                              hh0=0.0, hh1=24.0, tp_r=2.5)


def btc_h4_pb_ema_mt24_signal(candles, symbol: str = "BTCUSD"):
    """BTC_H4_PB_EMA_MT24 — GO v14 · pullback EMA21 + bias H4 · 24h · seg–qui."""
    if not candles or len(candles) < 80:
        return None
    df = pd.DataFrame(candles).rename(columns={
        "open": "Open", "high": "High", "low": "Low",
        "close": "Close", "volume": "Volume"})
    for col in ("Open", "High", "Low", "Close", "Volume"):
        if col not in df.columns:
            return None
    df["dt"] = pd.to_datetime(df["time"], unit="s")
    df = df.set_index("dt").iloc[:-1]
    if len(df) < 60:
        return None
    if int(df.index[-1].dayofweek) not in (0, 1, 2, 3):
        return None
    i = len(df) - 1
    r, p = df.iloc[i], df.iloc[i - 1]
    c = df.Close
    ema21 = c.ewm(span=21, adjust=False).mean()
    ema50 = c.ewm(span=50, adjust=False).mean()
    tr = pd.concat([(df.High - df.Low),
                    (df.High - c.shift()).abs(),
                    (df.Low - c.shift()).abs()], axis=1).max(axis=1)
    atr = float(tr.ewm(alpha=1 / 14, adjust=False).mean().iloc[i])
    if not np.isfinite(atr) or atr <= 0:
        return None
    e20, e50 = float(ema21.iloc[i]), float(ema50.iloc[i])
    if e20 > e50:
        if float(p.Low) > e20 + 0.3 * atr:
            return None
        if not (float(r.Close) > e20 and float(r.Close) > float(r.Open)
                and float(r.Close) > float(p.High)):
            return None
        d, sl = "COMPRA", min(float(p.Low), float(r.Low))
    else:
        if float(p.High) < e20 - 0.3 * atr:
            return None
        if not (float(r.Close) < e20 and float(r.Close) < float(r.Open)
                and float(r.Close) < float(p.Low)):
            return None
        d, sl = "VENDA", max(float(p.High), float(r.High))
    if not _h4_bias_ok(df, d):
        return None
    entry = float(r.Close)
    risk = abs(entry - sl)
    if risk <= 0:
        return None
    tp = entry + 2.0 * risk if d == "COMPRA" else entry - 2.0 * risk
    return (d, sl, tp)


def btc_h4_pb_ema_signal(candles, symbol: str = "BTCUSD"):
    """BTC_H4_PB_EMA — GO v12 · pullback EMA21 + bias H4 · 10–18h."""
    if not candles or len(candles) < 80:
        return None
    df = pd.DataFrame(candles).rename(columns={
        "open": "Open", "high": "High", "low": "Low",
        "close": "Close", "volume": "Volume"})
    for col in ("Open", "High", "Low", "Close", "Volume"):
        if col not in df.columns:
            return None
    df["dt"] = pd.to_datetime(df["time"], unit="s")
    df = df.set_index("dt").iloc[:-1]
    if len(df) < 60:
        return None
    i = len(df) - 1
    r, p = df.iloc[i], df.iloc[i - 1]
    hh = df.index[-1].hour + df.index[-1].minute / 60.0
    if not (10.0 <= hh < 18.0):
        return None
    c = df.Close
    ema21 = c.ewm(span=21, adjust=False).mean()
    ema50 = c.ewm(span=50, adjust=False).mean()
    tr = pd.concat([(df.High - df.Low),
                    (df.High - c.shift()).abs(),
                    (df.Low - c.shift()).abs()], axis=1).max(axis=1)
    atr = float(tr.ewm(alpha=1 / 14, adjust=False).mean().iloc[i])
    if not np.isfinite(atr) or atr <= 0:
        return None
    e20, e50 = float(ema21.iloc[i]), float(ema50.iloc[i])
    if not _h4_bias_ok(df, "COMPRA" if e20 > e50 else "VENDA"):
        # still require H4 ok for chosen direction below
        pass
    if e20 > e50:
        if float(p.Low) > e20 + 0.3 * atr:
            return None
        if not (float(r.Close) > e20 and float(r.Close) > float(r.Open)
                and float(r.Close) > float(p.High)):
            return None
        d, sl = "COMPRA", min(float(p.Low), float(r.Low))
    else:
        if float(p.High) < e20 - 0.3 * atr:
            return None
        if not (float(r.Close) < e20 and float(r.Close) < float(r.Open)
                and float(r.Close) < float(p.Low)):
            return None
        d, sl = "VENDA", max(float(p.High), float(r.High))
    if not _h4_bias_ok(df, d):
        return None
    entry = float(r.Close)
    risk = abs(entry - sl)
    if risk <= 0:
        return None
    tp = entry + 2.0 * risk if d == "COMPRA" else entry - 2.0 * risk
    return (d, sl, tp)


def xau_imp_cont_20_signal(candles, symbol: str = "XAUUSD"):
    """XAU_IMP_CONT_20 — GO v11 · impulso ATR×2 + vol · TP≤30 pts."""
    return _impulse_cont_core(candles, atr_mult=2.0, vol_mult=1.5,
                              hh0=9.0, hh1=18.0, tp_r=2.0,
                              tp_cap=_XAU_STRUCT_TP_CAP)


def xau_nr4_1016_h4_signal(candles, symbol: str = "XAUUSD"):
    """XAU_NR4_1016_H4 — GO mega-sweep · NR4 + vol≥1,3× + H4 · 10–16h · TP≤30."""
    return _nr_h4_core(candles, n=4, vol_mult=1.3, hh0=10.0, hh1=16.0,
                       tp_cap=_XAU_STRUCT_TP_CAP)


def xau_pdh_h4_signal(candles, symbol: str = "XAUUSD"):
    """XAU_PDH_H4 — GO mega-sweep · PDH/PDL + vol≥1,2× + H4 · 10–16h · TP≤30."""
    return _pdh_h4_core(candles, vol_mult=1.2, hh0=10.0, hh1=16.0,
                        tp_cap=_XAU_STRUCT_TP_CAP)


def xau_pdh_1115_signal(candles, symbol: str = "XAUUSD"):
    """XAU_PDH_1115 — GO mega-sweep · PDH/PDL + H4 · 11–15h · TP≤30."""
    return _pdh_h4_core(candles, vol_mult=1.2, hh0=11.0, hh1=15.0,
                        tp_cap=_XAU_STRUCT_TP_CAP)


def xau_hl_24h_signal(candles, symbol: str = "XAUUSD"):
    """XAU_HL_24H — GO mega-sweep · HL/LH + vol≥1,3× + H4 · 24h · TP≤30 pts."""
    return _hl_h4_core(candles, vol_mult=1.3, hh0=0.0, hh1=24.0,
                       tp_cap=_XAU_STRUCT_TP_CAP)


def btc_pdh_h4_signal(candles, symbol: str = "BTCUSD"):
    """BTC_PDH_H4 — GO mega-sweep · PDH/PDL + vol≥1,2× + H4 · 10–16h."""
    return _pdh_h4_core(candles, vol_mult=1.2, hh0=10.0, hh1=16.0)


def eur_pdh_h4_signal(candles, symbol: str = "EURUSD"):
    """EUR_PDH_H4 — GO mega-sweep · PDH/PDL + vol≥1,2× + H4 · 10–16h."""
    return _pdh_h4_core(candles, vol_mult=1.2, hh0=10.0, hh1=16.0)


def _hh_in(hh, hh0, hh1):
    """Janela horária; hh1≥24 e hh0≤0 → 24h."""
    h, a, b = float(hh), float(hh0), float(hh1)
    if b >= 24.0 and a <= 0.0:
        return True
    if a <= b:
        return a <= h < b
    return h >= a or h < b


def _candles_df(candles, min_n=30):
    if not candles or len(candles) < min_n:
        return None
    df = pd.DataFrame(candles).rename(columns={
        "open": "Open", "high": "High", "low": "Low",
        "close": "Close", "volume": "Volume"})
    for col in ("Open", "High", "Low", "Close", "Volume"):
        if col not in df.columns:
            return None
    df["dt"] = pd.to_datetime(df["time"], unit="s")
    df = df.set_index("dt").iloc[:-1]
    if len(df) < min_n:
        return None
    return df


def _pdh_h4_core(candles, vol_mult=1.2, hh0=10.0, hh1=16.0, tp_cap=None, tp_r=1.5):
    """Espelho de mega-sweep `_pdh_h`: range 96 barras (~1d M15) + SL barra ant. · RR tp_r."""
    df = _candles_df(candles, 100)
    if df is None:
        return None
    i = len(df) - 1
    r, p = df.iloc[i], df.iloc[i - 1]
    hh = df.index[-1].hour + df.index[-1].minute / 60.0
    if not _hh_in(hh, hh0, hh1):
        return None
    vol8 = float(df.Volume.iloc[i - 8:i].mean()) if i >= 8 else float(df.Volume.iloc[:i].mean())
    if vol8 <= 0 or float(r.Volume) < vol_mult * vol8:
        return None
    win = df.iloc[max(0, i - 96):i]
    if len(win) < 20:
        return None
    pdh, pdl = float(win.High.max()), float(win.Low.min())
    if float(r.Close) > pdh and float(p.Close) <= pdh:
        d, sl = "COMPRA", float(p.Low)
    elif float(r.Close) < pdl and float(p.Close) >= pdl:
        d, sl = "VENDA", float(p.High)
    else:
        return None
    if not _h4_bias_ok(df, d):
        return None
    entry = float(r.Close)
    risk = abs(entry - sl)
    if risk <= 0:
        return None
    tp = entry + tp_r * risk if d == "COMPRA" else entry - tp_r * risk
    return _apply_tp_cap((d, sl, tp), entry, tp_cap)


def _hl_h4_core(candles, vol_mult=1.3, hh0=0.0, hh1=24.0, tp_cap=None, tp_r=1.5):
    """Espelho de mega-sweep `_hl_h`: 1 HL/LH + rompe + H4 · RR tp_r."""
    df = _candles_df(candles, 30)
    if df is None:
        return None
    i = len(df) - 1
    if i < 2:
        return None
    r, p1, p2 = df.iloc[i], df.iloc[i - 1], df.iloc[i - 2]
    hh = df.index[-1].hour + df.index[-1].minute / 60.0
    if not _hh_in(hh, hh0, hh1):
        return None
    vol8 = float(df.Volume.iloc[i - 8:i].mean()) if i >= 8 else float(df.Volume.iloc[:i].mean())
    if vol8 <= 0 or float(r.Volume) < vol_mult * vol8:
        return None
    if float(p1.Low) > float(p2.Low) and float(r.Close) > float(p1.High):
        d, sl = "COMPRA", float(p1.Low)
    elif float(p1.High) < float(p2.High) and float(r.Close) < float(p1.Low):
        d, sl = "VENDA", float(p1.High)
    else:
        return None
    if not _h4_bias_ok(df, d):
        return None
    entry = float(r.Close)
    risk = abs(entry - sl)
    if risk <= 0:
        return None
    tp = entry + tp_r * risk if d == "COMPRA" else entry - tp_r * risk
    return _apply_tp_cap((d, sl, tp), entry, tp_cap)


def _nr_h4_core(candles, n=5, vol_mult=1.3, hh0=10.0, hh1=16.0, tp_cap=None, tp_r=1.5):
    if not candles or len(candles) < max(220, n + 10):
        return None
    df = pd.DataFrame(candles).rename(columns={
        "open": "Open", "high": "High", "low": "Low",
        "close": "Close", "volume": "Volume"})
    for col in ("Open", "High", "Low", "Close", "Volume"):
        if col not in df.columns:
            return None
    df["dt"] = pd.to_datetime(df["time"], unit="s")
    df = df.set_index("dt").iloc[:-1]
    if len(df) < n + 10:
        return None
    i = len(df) - 1
    r = df.iloc[i]
    hh = df.index[-1].hour + df.index[-1].minute / 60.0
    if not _hh_in(hh, hh0, hh1):
        return None
    prev_rng = (df.High.iloc[i - n:i] - df.Low.iloc[i - n:i])
    if len(prev_rng) < n or float(prev_rng.min()) <= 0:
        return None
    if float(prev_rng.iloc[-1]) > float(prev_rng.min()) + 1e-12:
        return None
    p = df.iloc[i - 1]
    vol8 = float(df.Volume.iloc[i - 8:i].mean()) if i >= 8 else float(df.Volume.iloc[:i].mean())
    if vol8 <= 0 or float(r.Volume) < vol_mult * vol8:
        return None
    d = sl = None
    if float(r.Close) > float(p.High):
        d, sl = "COMPRA", float(p.Low)
    elif float(r.Close) < float(p.Low):
        d, sl = "VENDA", float(p.High)
    else:
        return None
    if not _h4_bias_ok(df, d):
        return None
    entry = float(r.Close)
    risk = abs(entry - sl)
    if risk <= 0:
        return None
    tp = entry + tp_r * risk if d == "COMPRA" else entry - tp_r * risk
    return _apply_tp_cap((d, sl, tp), entry, tp_cap)


def _impulse_cont_core(candles, atr_mult=2.0, vol_mult=1.5, hh0=9.0, hh1=18.0, tp_r=2.0,
                       mt_only=False, tp_cap=None):
    if not candles or len(candles) < 40:
        return None
    df = pd.DataFrame(candles).rename(columns={
        "open": "Open", "high": "High", "low": "Low",
        "close": "Close", "volume": "Volume"})
    for col in ("Open", "High", "Low", "Close", "Volume"):
        if col not in df.columns:
            return None
    df["dt"] = pd.to_datetime(df["time"], unit="s")
    df = df.set_index("dt").iloc[:-1]
    if len(df) < 40:
        return None
    i = len(df) - 1
    r, p = df.iloc[i], df.iloc[i - 1]
    if mt_only and int(df.index[-1].dayofweek) not in (0, 1, 2, 3):
        return None
    hh = df.index[-1].hour + df.index[-1].minute / 60.0
    if not _hh_in(hh, hh0, hh1):
        return None
    c, h, l = df.Close, df.High, df.Low
    tr = pd.concat([(h - l), (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = float(tr.ewm(alpha=1 / 14, adjust=False).mean().iloc[i - 1])
    if not np.isfinite(atr) or atr <= 0:
        return None
    rng = float(p.High - p.Low)
    if rng < atr_mult * atr:
        return None
    vol8p = float(df.Volume.iloc[i - 9:i - 1].mean()) if i >= 9 else float(df.Volume.iloc[:i - 1].mean())
    if vol8p <= 0 or float(p.Volume) < vol_mult * vol8p:
        return None
    down = float(p.Close) < float(p.Open)
    up = float(p.Close) > float(p.Open)
    if not (down or up):
        return None
    d = "VENDA" if down else "COMPRA"
    if not _h4_bias_ok(df, d):
        return None
    if d == "VENDA" and float(r.Close) >= float(p.Low):
        return None
    if d == "COMPRA" and float(r.Close) <= float(p.High):
        return None
    vol8 = float(df.Volume.iloc[i - 8:i].mean()) if i >= 8 else float(df.Volume.iloc[:i].mean())
    if vol8 <= 0 or float(r.Volume) < 1.1 * vol8:
        return None
    sl = float(p.High) if d == "VENDA" else float(p.Low)
    entry = float(r.Close)
    risk = abs(entry - sl)
    if risk <= 0:
        return None
    tp = entry + tp_r * risk if d == "COMPRA" else entry - tp_r * risk
    return _apply_tp_cap((d, sl, tp), entry, tp_cap)


def _h4_bias_ok(df, d: str) -> bool:
    h4 = df[["Close"]].resample("4h").last().dropna()
    if len(h4) < 55:
        return False
    h4 = h4.copy()
    h4["ema50"] = h4.Close.ewm(span=50, adjust=False).mean()
    h4_close = h4.Close.reindex(df.index, method="ffill")
    h4_ema = h4["ema50"].reindex(df.index, method="ffill")
    hc = float(h4_close.iloc[-1]) if np.isfinite(h4_close.iloc[-1]) else None
    he = float(h4_ema.iloc[-1]) if np.isfinite(h4_ema.iloc[-1]) else None
    if hc is None or he is None:
        return False
    up = hc > he
    if d == "COMPRA" and not up:
        return False
    if d == "VENDA" and up:
        return False
    return True


def _inside_h4_core(candles, hh0=9.0, hh1=14.0, vol_mult=1.2, tp_cap=None, tp_r=1.5):
    if not candles or len(candles) < 220:
        return None
    df = pd.DataFrame(candles).rename(columns={
        "open": "Open", "high": "High", "low": "Low",
        "close": "Close", "volume": "Volume"})
    for col in ("Open", "High", "Low", "Close", "Volume"):
        if col not in df.columns:
            return None
    df["dt"] = pd.to_datetime(df["time"], unit="s")
    df = df.set_index("dt").iloc[:-1]
    if len(df) < 220:
        return None

    i = len(df) - 1
    r, p, pp = df.iloc[i], df.iloc[i - 1], df.iloc[i - 2]
    hh = df.index[-1].hour + df.index[-1].minute / 60.0
    if not _hh_in(hh, hh0, hh1):
        return None
    if not (float(p.High) <= float(pp.High) and float(p.Low) >= float(pp.Low)):
        return None
    vol8 = float(df.Volume.iloc[i - 8:i].mean()) if i >= 8 else float(df.Volume.iloc[:i].mean())
    if vol8 <= 0 or float(r.Volume) < vol_mult * vol8:
        return None

    d = None
    sl = None
    if float(r.Close) > float(p.High):
        d, sl = "COMPRA", float(p.Low)
    elif float(r.Close) < float(p.Low):
        d, sl = "VENDA", float(p.High)
    else:
        return None

    if not _h4_bias_ok(df, d):
        return None

    entry = float(r.Close)
    risk = abs(entry - sl)
    if risk <= 0:
        return None
    tp = entry + tp_r * risk if d == "COMPRA" else entry - tp_r * risk
    return _apply_tp_cap((d, sl, tp), entry, tp_cap)


def _engulf_h4_core(candles, vol_mult=1.3, hh0=0.0, hh1=24.0, tp_r=1.5, tp_cap=None):
    df = _candles_df(candles, 30)
    if df is None or len(df) < 3:
        return None
    i = len(df) - 1
    r, p = df.iloc[i], df.iloc[i - 1]
    hh = df.index[-1].hour + df.index[-1].minute / 60.0
    if not _hh_in(hh, hh0, hh1):
        return None
    vol8 = float(df.Volume.iloc[i - 8:i].mean()) if i >= 8 else float(df.Volume.iloc[:i].mean())
    if vol8 <= 0 or float(r.Volume) < vol_mult * vol8:
        return None
    bull = (float(r.Close) > float(r.Open) and float(p.Close) < float(p.Open)
            and float(r.Close) >= float(p.Open) and float(r.Open) <= float(p.Close)
            and float(r.Close) > float(p.High))
    bear = (float(r.Close) < float(r.Open) and float(p.Close) > float(p.Open)
            and float(r.Close) <= float(p.Open) and float(r.Open) >= float(p.Close)
            and float(r.Close) < float(p.Low))
    if bull:
        d, sl = "COMPRA", float(min(p.Low, r.Low))
    elif bear:
        d, sl = "VENDA", float(max(p.High, r.High))
    else:
        return None
    if not _h4_bias_ok(df, d):
        return None
    entry = float(r.Close)
    risk = abs(entry - sl)
    if risk <= 0:
        return None
    tp = entry + tp_r * risk if d == "COMPRA" else entry - tp_r * risk
    return _apply_tp_cap((d, sl, tp), entry, tp_cap)


def _ema_reclaim_core(candles, vol_mult=1.2, hh0=0.0, hh1=24.0, tp_r=1.5):
    df = _candles_df(candles, 60)
    if df is None or len(df) < 30:
        return None
    i = len(df) - 1
    r, p = df.iloc[i], df.iloc[i - 1]
    hh = df.index[-1].hour + df.index[-1].minute / 60.0
    if not _hh_in(hh, hh0, hh1):
        return None
    vol8 = float(df.Volume.iloc[i - 8:i].mean()) if i >= 8 else float(df.Volume.iloc[:i].mean())
    if vol8 <= 0 or float(r.Volume) < vol_mult * vol8:
        return None
    ema21 = df.Close.ewm(span=21, adjust=False).mean()
    e = float(ema21.iloc[i])
    if not np.isfinite(e):
        return None
    if float(p.Low) < e and float(r.Close) > e and float(r.Close) > float(r.Open):
        d, sl = "COMPRA", float(min(p.Low, r.Low))
    elif float(p.High) > e and float(r.Close) < e and float(r.Close) < float(r.Open):
        d, sl = "VENDA", float(max(p.High, r.High))
    else:
        return None
    if not _h4_bias_ok(df, d):
        return None
    entry = float(r.Close)
    risk = abs(entry - sl)
    if risk <= 0:
        return None
    tp = entry + tp_r * risk if d == "COMPRA" else entry - tp_r * risk
    return (d, sl, tp)


def _session_vwap_atr(df, i):
    """VWAP do dia da barra i + ATR(14). Retorna (vwap, atr) ou (None, None)."""
    day = df.index.date
    mask = np.array([d == df.index[i].date() for d in day])
    sess = df.loc[mask]
    if len(sess) < 3:
        return None, None
    typ = (sess.High + sess.Low + sess.Close) / 3.0
    vol = sess.Volume.replace(0, np.nan).fillna(1.0)
    vwap = float((typ * vol).cumsum().iloc[-1] / vol.cumsum().iloc[-1])
    tr = pd.concat([(df.High - df.Low),
                    (df.High - df.Close.shift()).abs(),
                    (df.Low - df.Close.shift()).abs()], axis=1).max(axis=1)
    atr = float(tr.ewm(alpha=1 / 14, adjust=False).mean().iloc[i])
    if not np.isfinite(atr) or atr <= 0:
        return None, None
    return vwap, atr


def _vwap_cont_core(candles, vol_mult=1.3, dist=0.3, hh0=0.0, hh1=24.0, tp_r=1.5,
                    tp_cap=None):
    """Continuaçao do lado do VWAP session · H4."""
    df = _candles_df(candles, 80)
    if df is None or len(df) < 40:
        return None
    i = len(df) - 1
    r, p = df.iloc[i], df.iloc[i - 1]
    hh = df.index[-1].hour + df.index[-1].minute / 60.0
    if not _hh_in(hh, hh0, hh1):
        return None
    vol8 = float(df.Volume.iloc[i - 8:i].mean()) if i >= 8 else float(df.Volume.iloc[:i].mean())
    if vol8 <= 0 or float(r.Volume) < vol_mult * vol8:
        return None
    vwap, atr = _session_vwap_atr(df, i)
    if vwap is None:
        return None
    vd = (float(r.Close) - vwap) / atr
    if vd > dist and float(r.Close) > float(p.High):
        d, sl = "COMPRA", float(p.Low)
    elif vd < -dist and float(r.Close) < float(p.Low):
        d, sl = "VENDA", float(p.High)
    else:
        return None
    if not _h4_bias_ok(df, d):
        return None
    entry = float(r.Close)
    risk = abs(entry - sl)
    if risk <= 0:
        return None
    tp = entry + tp_r * risk if d == "COMPRA" else entry - tp_r * risk
    return _apply_tp_cap((d, sl, tp), entry, tp_cap)


def _vwap_fade_core(candles, vol_mult=1.2, dist=1.2, hh0=0.0, hh1=24.0, tp_r=1.5,
                    tp_cap=None):
    """Fade VWAP: esticado ≥dist ATR + rejeição + H4 contrário ao stretch."""
    df = _candles_df(candles, 80)
    if df is None or len(df) < 40:
        return None
    i = len(df) - 1
    r, p = df.iloc[i], df.iloc[i - 1]
    hh = df.index[-1].hour + df.index[-1].minute / 60.0
    if not _hh_in(hh, hh0, hh1):
        return None
    vol8 = float(df.Volume.iloc[i - 8:i].mean()) if i >= 8 else float(df.Volume.iloc[:i].mean())
    if vol8 <= 0 or float(r.Volume) < vol_mult * vol8:
        return None
    vwap, atr = _session_vwap_atr(df, i)
    if vwap is None:
        return None
    vd = (float(r.Close) - vwap) / atr
    if vd >= dist and float(r.Close) < float(r.Open) and float(r.Close) < float(p.Close):
        d, sl = "VENDA", float(max(p.High, r.High))
    elif vd <= -dist and float(r.Close) > float(r.Open) and float(r.Close) > float(p.Close):
        d, sl = "COMPRA", float(min(p.Low, r.Low))
    else:
        return None
    if not _h4_bias_ok(df, d):
        return None
    entry = float(r.Close)
    risk = abs(entry - sl)
    if risk <= 0:
        return None
    tp = entry + tp_r * risk if d == "COMPRA" else entry - tp_r * risk
    return _apply_tp_cap((d, sl, tp), entry, tp_cap)


def _h4_pb_ema_core(candles, hh0=10.0, hh1=18.0, tp_r=2.0, mt_only=False, tp_cap=None):
    df = _candles_df(candles, 80)
    if df is None or len(df) < 60:
        return None
    if mt_only and int(df.index[-1].dayofweek) not in (0, 1, 2, 3):
        return None
    i = len(df) - 1
    r, p = df.iloc[i], df.iloc[i - 1]
    hh = df.index[-1].hour + df.index[-1].minute / 60.0
    if not _hh_in(hh, hh0, hh1):
        return None
    c = df.Close
    ema21 = c.ewm(span=21, adjust=False).mean()
    ema50 = c.ewm(span=50, adjust=False).mean()
    tr = pd.concat([(df.High - df.Low),
                    (df.High - c.shift()).abs(),
                    (df.Low - c.shift()).abs()], axis=1).max(axis=1)
    atr = float(tr.ewm(alpha=1 / 14, adjust=False).mean().iloc[i])
    if not np.isfinite(atr) or atr <= 0:
        return None
    e20, e50 = float(ema21.iloc[i]), float(ema50.iloc[i])
    if e20 > e50:
        if float(p.Low) > e20 + 0.3 * atr:
            return None
        if not (float(r.Close) > e20 and float(r.Close) > float(r.Open)
                and float(r.Close) > float(p.High)):
            return None
        d, sl = "COMPRA", min(float(p.Low), float(r.Low))
    else:
        if float(p.High) < e20 - 0.3 * atr:
            return None
        if not (float(r.Close) < e20 and float(r.Close) < float(r.Open)
                and float(r.Close) < float(p.Low)):
            return None
        d, sl = "VENDA", max(float(p.High), float(r.High))
    if not _h4_bias_ok(df, d):
        return None
    entry = float(r.Close)
    risk = abs(entry - sl)
    if risk <= 0:
        return None
    tp = entry + tp_r * risk if d == "COMPRA" else entry - tp_r * risk
    return _apply_tp_cap((d, sl, tp), entry, tp_cap)


def _rsi_series(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    up = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, 1e-9))


def _rsi_recl_core(candles, lo=30.0, hi=70.0, vol_mult=1.2, hh0=0.0, hh1=24.0,
                   tp_r=1.5, tp_cap=None):
    """RSI extrema na barra ant. + reclaim na atual + H4 (espelho BT v20)."""
    df = _candles_df(candles, 40)
    if df is None or len(df) < 30:
        return None
    i = len(df) - 1
    if i < 2:
        return None
    r, p = df.iloc[i], df.iloc[i - 1]
    hh = df.index[-1].hour + df.index[-1].minute / 60.0
    if not _hh_in(hh, hh0, hh1):
        return None
    vol8 = float(df.Volume.iloc[i - 8:i].mean()) if i >= 8 else float(df.Volume.iloc[:i].mean())
    if vol8 <= 0 or float(r.Volume) < vol_mult * vol8:
        return None
    rsi = _rsi_series(df.Close)
    rsi_p, rsi_r = float(rsi.iloc[i - 1]), float(rsi.iloc[i])
    if not (np.isfinite(rsi_p) and np.isfinite(rsi_r)):
        return None
    if rsi_p < lo and rsi_r >= lo and float(r.Close) > float(r.Open):
        d, sl = "COMPRA", float(min(p.Low, r.Low))
    elif rsi_p > hi and rsi_r <= hi and float(r.Close) < float(r.Open):
        d, sl = "VENDA", float(max(p.High, r.High))
    else:
        return None
    if not _h4_bias_ok(df, d):
        return None
    entry = float(r.Close)
    risk = abs(entry - sl)
    if risk <= 0:
        return None
    tp = entry + tp_r * risk if d == "COMPRA" else entry - tp_r * risk
    return _apply_tp_cap((d, sl, tp), entry, tp_cap)


def _bb_fade_core(candles, vol_mult=1.2, hh0=0.0, hh1=24.0, tp_r=1.5, tp_cap=None):
    """Fecha fora BB + rejeição (close volta) + H4 a favor do fade (BT v20)."""
    df = _candles_df(candles, 40)
    if df is None or len(df) < 25:
        return None
    i = len(df) - 1
    if i < 2:
        return None
    r, p = df.iloc[i], df.iloc[i - 1]
    hh = df.index[-1].hour + df.index[-1].minute / 60.0
    if not _hh_in(hh, hh0, hh1):
        return None
    vol8 = float(df.Volume.iloc[i - 8:i].mean()) if i >= 8 else float(df.Volume.iloc[:i].mean())
    if vol8 <= 0 or float(r.Volume) < vol_mult * vol8:
        return None
    mid = df.Close.rolling(20).mean()
    std = df.Close.rolling(20).std()
    bu = float(mid.iloc[i] + 2 * std.iloc[i])
    bd = float(mid.iloc[i] - 2 * std.iloc[i])
    if not (np.isfinite(bu) and np.isfinite(bd)):
        return None
    if float(p.High) > bu and float(r.Close) < bu and float(r.Close) < float(r.Open):
        d, sl = "VENDA", float(max(p.High, r.High))
    elif float(p.Low) < bd and float(r.Close) > bd and float(r.Close) > float(r.Open):
        d, sl = "COMPRA", float(min(p.Low, r.Low))
    else:
        return None
    if not _h4_bias_ok(df, d):
        return None
    entry = float(r.Close)
    risk = abs(entry - sl)
    if risk <= 0:
        return None
    tp = entry + tp_r * risk if d == "COMPRA" else entry - tp_r * risk
    return _apply_tp_cap((d, sl, tp), entry, tp_cap)


def _session_dow_ok(candles, mode=None):
    """mode: None | mt | we | fri_sun | fri_mon."""
    if not mode:
        return True
    df = _candles_df(candles, 5)
    if df is None:
        return False
    ts = df.index[-1]
    d = int(ts.dayofweek)
    h = ts.hour + ts.minute / 60.0
    if mode == "mt":
        return d in (0, 1, 2, 3)
    if mode == "we":
        return d in (5, 6)
    if mode == "fri_sun":
        if d == 4 and h < 18.0:
            return False
        return d in (4, 5, 6)
    if mode == "fri_mon":
        if d == 4 and h < 18.0:
            return False
        if d == 0 and h >= 8.0:
            return False
        return d in (4, 5, 6, 0)
    return True
