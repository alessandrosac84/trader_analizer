"""
backtest_setups_novos_v4.py — RODADA 4: variações dos 🟡 + setups novos.

Régua idêntica (custos, OOS 70/30, bimestre, GO). NÃO altera motores ao vivo.

Foco:
  · Empurrar amarelos da v3 (OVN_*, LH_*, RND_FADE, DELTA_BURST, ETH_LH…)
  · Novos conceitos (ainda não testados nas v1–v3)

Uso:
  python rodar_backtest_setups_novos_v4.py
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
    stats, consistency, verdict, _line, _sig,
    reversal_up, reversal_down, _parts, OOS_FRAC, _WIN_TICK,
)
from backtest_setups_novos_v2 import (
    add_extra_v2, _simulate, _of,
    SESS_LONDON_TRADE, SESS_NY_EARLY,
)
from backtest_setups_novos_v3 import (
    make_ovn_pdc, make_ovn_atr, make_gap_fade, make_ib_break,
    make_of_window, make_asia_break, make_round_fade, make_london_handoff,
    s_win_nr7_break, s_win_inside_bar_break,  # já GO — não re-registrar
)

STRATS = {"b3": {}, "crypto": {}}
STRAT_SYM = {}
STRAT_TF = {}
ONE_PER_DAY = set()


def _reg(grupo, name, fn, sym, tf=15, one_per_day=False):
    STRATS[grupo][name] = fn
    STRAT_SYM[name] = sym
    if tf != 15:
        STRAT_TF[name] = tf
    if one_per_day:
        ONE_PER_DAY.add(name)


# ════════════════════════════════════════════════════════════════════════════
# NOVOS CRIATIVOS
# ════════════════════════════════════════════════════════════════════════════

def s_win_three_bar_rev(df, i, sym):
    """3 barras: duas no sentido + engolfo contrario, 10–13h, vol ok."""
    if i < 3:
        return None
    r, p, pp = df.iloc[i], df.iloc[i - 1], df.iloc[i - 2]
    if not (10.0 <= r.hh < 13.0):
        return None
    if r.Volume < 1.2 * (r.vol8 or 0):
        return None
    # duas baixas + engolfo de alta
    if pp.Close < pp.Open and p.Close < p.Open and r.Close > r.Open and r.Close >= p.Open and r.Open <= p.Close:
        return _sig("COMPRA", r.Close, min(p.Low, r.Low) - _WIN_TICK, 1.5, 2.5, True)
    if pp.Close > pp.Open and p.Close > p.Open and r.Close < r.Open and r.Close <= p.Open and r.Open >= p.Close:
        return _sig("VENDA", r.Close, max(p.High, r.High) + _WIN_TICK, 1.5, 2.5, True)
    return None


def s_win_orb_nr7(df, i, sym):
    """NR7 que ocorre DENTRO do range ORB 10:00–10:45 → rompe ORB com vol."""
    r = df.iloc[i]
    if not (10.75 < r.hh < 12.5):
        return None
    if pd.isna(r.orb_hi) or pd.isna(r.orb_lo):
        return None
    if i < 10:
        return None
    prev_ranges = (df.High.iloc[i - 7:i] - df.Low.iloc[i - 7:i])
    if len(prev_ranges) < 7 or prev_ranges.iloc[-1] > prev_ranges.min() + 1e-12:
        return None
    p = df.iloc[i - 1]
    if r.Volume < 1.3 * (r.vol8 or 0):
        return None
    if r.Close > r.orb_hi and p.Close <= r.orb_hi:
        return _sig("COMPRA", r.Close, r.orb_lo, 1.5, 2.5, True)
    if r.Close < r.orb_lo and p.Close >= r.orb_lo:
        return _sig("VENDA", r.Close, r.orb_hi, 1.5, 2.5, True)
    return None


def s_win_inside_orb(df, i, sym):
    """Inside bar após ORB formado, rompe na direção do gap."""
    r = df.iloc[i]
    p, pp = df.iloc[i - 1], df.iloc[i - 2]
    if not (10.75 < r.hh < 13.0):
        return None
    if pd.isna(r.orb_hi) or pd.isna(r.gap_pct):
        return None
    inside = p.High <= pp.High and p.Low >= pp.Low
    if not inside or r.Volume < 1.2 * (r.vol8 or 0):
        return None
    if r.gap_pct > 0.1 and r.Close > p.High and r.Close > r.orb_hi:
        return _sig("COMPRA", r.Close, p.Low, 1.5, 2.5, True)
    if r.gap_pct < -0.1 and r.Close < p.Low and r.Close < r.orb_lo:
        return _sig("VENDA", r.Close, p.High, 1.5, 2.5, True)
    return None


def s_win_nr7_trend(df, i, sym):
    """NR7 só a favor do bias H4."""
    base = s_win_nr7_break(df, i, sym)
    if not base:
        return None
    r = df.iloc[i]
    if pd.isna(r.h4_close) or pd.isna(r.h4_ema50):
        return None
    up = r.h4_close > r.h4_ema50
    if base["dir"] == "COMPRA" and not up:
        return None
    if base["dir"] == "VENDA" and up:
        return None
    return base


def s_win_inside_vol15(df, i, sym):
    """Inside bar com volume ≥1,5× (mais seletivo que o GO)."""
    r = df.iloc[i]
    p, pp = df.iloc[i - 1], df.iloc[i - 2]
    if not (10.0 <= r.hh < 14.0):
        return None
    if not (p.High <= pp.High and p.Low >= pp.Low):
        return None
    if r.Volume < 1.5 * (r.vol8 or 0):
        return None
    if r.Close > p.High:
        return _sig("COMPRA", r.Close, p.Low, 1.5, 2.5, True)
    if r.Close < p.Low:
        return _sig("VENDA", r.Close, p.High, 1.5, 2.5, True)
    return None


def s_win_nr7_morning(df, i, sym):
    """NR7 só 10–12h (melhor liquidez)."""
    r = df.iloc[i]
    if not (10.0 <= r.hh < 12.0):
        return None
    # reusa lógica sem checar hh de novo — chama e filtra
    if i < 10:
        return None
    prev_ranges = (df.High.iloc[i - 7:i] - df.Low.iloc[i - 7:i])
    if len(prev_ranges) < 7 or prev_ranges.min() <= 0:
        return None
    if prev_ranges.iloc[-1] > prev_ranges.min() + 1e-12:
        return None
    p = df.iloc[i - 1]
    if r.Volume < 1.3 * (r.vol8 or 0):
        return None
    if r.Close > p.High:
        return _sig("COMPRA", r.Close, p.Low, 1.5, 2.5, True)
    if r.Close < p.Low:
        return _sig("VENDA", r.Close, p.High, 1.5, 2.5, True)
    return None


def s_win_double_inside(df, i, sym):
    """Duas inside bars consecutivas → rompimento."""
    if i < 4:
        return None
    r = df.iloc[i]
    a, b, c = df.iloc[i - 1], df.iloc[i - 2], df.iloc[i - 3]
    if not (10.0 <= r.hh < 14.0):
        return None
    ins1 = a.High <= b.High and a.Low >= b.Low
    ins2 = b.High <= c.High and b.Low >= c.Low
    if not (ins1 and ins2):
        return None
    if r.Volume < 1.2 * (r.vol8 or 0):
        return None
    if r.Close > a.High:
        return _sig("COMPRA", r.Close, a.Low, 1.5, 2.5, True)
    if r.Close < a.Low:
        return _sig("VENDA", r.Close, a.High, 1.5, 2.5, True)
    return None


def s_win_kickoff(df, i, sym):
    """Marubozu (corpo ≥80% range) 10–11h na direção do gap ≥0.2%."""
    r = df.iloc[i]
    if not (10.0 <= r.hh < 11.0):
        return None
    if pd.isna(r.gap_pct) or abs(r.gap_pct) < 0.2:
        return None
    body, rng, uw, dw = _parts(r)
    if body < 0.80 * rng or r.Volume < 1.3 * (r.vol8 or 0):
        return None
    if r.gap_pct > 0 and r.Close > r.Open:
        return _sig("COMPRA", r.Close, r.Low - _WIN_TICK, 1.5, 2.5, True)
    if r.gap_pct < 0 and r.Close < r.Open:
        return _sig("VENDA", r.Close, r.High + _WIN_TICK, 1.5, 2.5, True)
    return None


def s_xau_delta_burst_v2(df, i, sym):
    """DELTA_BURST mais seletivo: |dn|≥0.50, só Londres, corpo 65%."""
    r = df.iloc[i]
    if not (9 <= r.hh < 11) or pd.isna(r.dn):
        return None
    body, rng, uw, dw = _parts(r)
    if body < 0.65 * rng:
        return None
    if r.dn >= 0.50 and r.Close > r.Open:
        return _sig("COMPRA", r.Close, r.Low - 0.3 * (r.atr or 1), 1.5, 2.5, True)
    if r.dn <= -0.50 and r.Close < r.Open:
        return _sig("VENDA", r.Close, r.High + 0.3 * (r.atr or 1), 1.5, 2.5, True)
    return None


def s_xau_rnd_fade_goish(df, i, sym):
    """Entre RND_FADE e TIGHT: delta_max 0.10, atr_near 0.18."""
    return make_round_fade(50, 0.10, 0.18)(df, i, sym)


def s_xau_rnd_fade_rr2(df, i, sym):
    """RND_FADE_TIGHT com RR 2.0 (via _sig manual)."""
    r = df.iloc[i]
    lvl = round(r.Close / 50.0) * 50.0
    if lvl <= 0 or abs(r.Close - lvl) > 0.15 * (r.atr or 1):
        return None
    dn = r.dn if not pd.isna(r.dn) else 0
    if abs(dn) >= 0.08:
        return None
    body, rng, uw, dw = _parts(r)
    if r.High >= lvl and uw >= 0.45 * rng and r.Close < lvl:
        return _sig("VENDA", r.Close, r.High + 0.3 * (r.atr or 1), 2.0, 3.0, True)
    if r.Low <= lvl and dw >= 0.45 * rng and r.Close > lvl:
        return _sig("COMPRA", r.Close, r.Low - 0.3 * (r.atr or 1), 2.0, 3.0, True)
    return None


def s_xau_nr7(df, i, sym):
    """NR7 no ouro, janela Londres 09–12."""
    r = df.iloc[i]
    if not (9 <= r.hh < 12) or i < 10:
        return None
    prev_ranges = (df.High.iloc[i - 7:i] - df.Low.iloc[i - 7:i])
    if len(prev_ranges) < 7 or prev_ranges.min() <= 0:
        return None
    if prev_ranges.iloc[-1] > prev_ranges.min() + 1e-12:
        return None
    p = df.iloc[i - 1]
    if r.Volume < 1.3 * (r.vol8 or 0):
        return None
    if r.Close > p.High:
        return _sig("COMPRA", r.Close, p.Low, 1.5, 2.5, True)
    if r.Close < p.Low:
        return _sig("VENDA", r.Close, p.High, 1.5, 2.5, True)
    return None


def s_xau_inside(df, i, sym):
    """Inside bar break XAU 09–14 servidor."""
    r = df.iloc[i]
    p, pp = df.iloc[i - 1], df.iloc[i - 2]
    if not (9 <= r.hh < 14):
        return None
    if not (p.High <= pp.High and p.Low >= pp.Low):
        return None
    if r.Volume < 1.2 * (r.vol8 or 0):
        return None
    if r.Close > p.High:
        return _sig("COMPRA", r.Close, p.Low, 1.5, 2.5, True)
    if r.Close < p.Low:
        return _sig("VENDA", r.Close, p.High, 1.5, 2.5, True)
    return None


def s_xau_lh_cons(df, i, sym):
    """LH asia≤0.48 delta≥0.18 só seg–qui (empurrar cons do LH_050_D20)."""
    return make_london_handoff(0.48, 0.18, 1.75, (0, 1, 2, 3))(df, i, sym)


def s_eth_inside(df, i, sym):
    return s_xau_inside(df, i, sym)


def s_eth_nr7(df, i, sym):
    return s_xau_nr7(df, i, sym)


def s_eth_rnd_tight(df, i, sym):
    return make_round_fade(100, 0.08, 0.15)(df, i, sym)


def s_btc_nr7(df, i, sym):
    return s_xau_nr7(df, i, sym)


def s_wdo_nr7_morning(df, i, sym):
    return s_win_nr7_morning(df, i, sym)


def s_wdo_inside_vol15(df, i, sym):
    return s_win_inside_vol15(df, i, sym)


def s_wdo_three_bar(df, i, sym):
    return s_win_three_bar_rev(df, i, sym)


# ════════════════════════════════════════════════════════════════════════════
# REGISTRO — variações amarelos + novos
# ════════════════════════════════════════════════════════════════════════════

# ── OVN / gap (amarelos v3) ──
_reg("b3", "OVN_PDC_035", make_ovn_pdc(0.35, 9.25, 9.75, False, 4), "WIN", one_per_day=True)
_reg("b3", "OVN_PDC_040_EXT", make_ovn_pdc(0.40, 9.25, 9.75, True, 4), "WIN", one_per_day=True)
_reg("b3", "OVN_PDC_035_EXT_MT", make_ovn_pdc(0.35, 9.25, 9.75, True, 3), "WIN", one_per_day=True)
_reg("b3", "OVN_PDC_032_EXT", make_ovn_pdc(0.32, 9.25, 9.75, True, 4), "WIN", one_per_day=True)
_reg("b3", "OVN_PDC_035_0930", make_ovn_pdc(0.35, 9.25, 9.5, True, 4), "WIN", one_per_day=True)
_reg("b3", "OVN_ATR_13_PDC", make_ovn_atr(1.3, 1.2, 9.25, 9.75, 3, True), "WIN", one_per_day=True)
_reg("b3", "OVN_ATR_12_PDC_MT", make_ovn_atr(1.2, 1.3, 9.25, 9.75, 2, True), "WIN", one_per_day=True)
_reg("b3", "OVN_ATR_14", make_ovn_atr(1.4, 1.25, 9.25, 9.75, 3), "WIN", one_per_day=True)
_reg("b3", "OVN_ATR_11_PDC", make_ovn_atr(1.1, 1.3, 9.25, 9.75, 3, True), "WIN", one_per_day=True)
_reg("b3", "GAP_FADE_030_ATR1", make_gap_fade(0.30, 1.0, 9.5), "WIN", one_per_day=True)
_reg("b3", "GAP_FADE_028_RR2", make_gap_fade(0.28, 0.9, 9.5, 2.0, 3.0), "WIN", one_per_day=True)

# ── Família do GO (variantes — podem melhorar ou piorar) ──
_reg("b3", "NR7_MORNING", s_win_nr7_morning, "WIN")
_reg("b3", "NR7_TREND_H4", s_win_nr7_trend, "WIN")
_reg("b3", "NR7_ORB", s_win_orb_nr7, "WIN", one_per_day=True)
_reg("b3", "INSIDE_VOL15", s_win_inside_vol15, "WIN")
_reg("b3", "INSIDE_ORB_GAP", s_win_inside_orb, "WIN", one_per_day=True)
_reg("b3", "DOUBLE_INSIDE", s_win_double_inside, "WIN")
_reg("b3", "THREE_BAR_REV", s_win_three_bar_rev, "WIN")
_reg("b3", "KICKOFF_GAP", s_win_kickoff, "WIN", one_per_day=True)
_reg("b3", "IB_BRK_15_GAP", make_ib_break(1.5, 10, 11.5, True), "WIN", one_per_day=True)

# ── WDO espelhos do que funcionou no WIN ──
_reg("b3", "WDO_NR7_AM", s_wdo_nr7_morning, "WDO")
_reg("b3", "WDO_INSIDE_V15", s_wdo_inside_vol15, "WDO")
_reg("b3", "WDO_THREE_BAR", s_wdo_three_bar, "WDO")
_reg("b3", "WDO_OVN_035_EXT", make_ovn_pdc(0.35, 9.25, 9.75, True, 4), "WDO", one_per_day=True)
_reg("b3", "WDO_KICKOFF", s_win_kickoff, "WDO", one_per_day=True)

# ── Crypto: LH amarelos ──
_reg("crypto", "LH_048_D18_MT", s_xau_lh_cons, "XAUUSD", one_per_day=True)
_reg("crypto", "LH_047_D18", make_london_handoff(0.47, 0.18, 1.75), "XAUUSD", one_per_day=True)
_reg("crypto", "LH_050_D18_MT", make_london_handoff(0.50, 0.18, 1.75, (0, 1, 2, 3)), "XAUUSD", one_per_day=True)
_reg("crypto", "LH_052_D20_MT", make_london_handoff(0.52, 0.20, 1.75, (0, 1, 2, 3)), "XAUUSD", one_per_day=True)
_reg("crypto", "LH_045_D18_TT", make_london_handoff(0.45, 0.18, 1.75, (1, 2, 3)), "XAUUSD", one_per_day=True)
_reg("crypto", "LH_050_D22", make_london_handoff(0.50, 0.22, 1.75), "XAUUSD", one_per_day=True)
_reg("crypto", "LH_048_D20_TP2", make_london_handoff(0.48, 0.20, 2.0, (0, 1, 2, 3)), "XAUUSD", one_per_day=True)

# ── Crypto: RND fade amarelos ──
_reg("crypto", "RND_FADE_010", s_xau_rnd_fade_goish, "XAUUSD")
_reg("crypto", "RND_FADE_T_RR2", s_xau_rnd_fade_rr2, "XAUUSD")
_reg("crypto", "RND_FADE_T_LON", make_round_fade(50, 0.08, 0.15, 9, 14), "XAUUSD")
_reg("crypto", "RND_FADE_T_MT", make_round_fade(50, 0.08, 0.15, None, None, (0, 1, 2, 3)), "XAUUSD")
_reg("crypto", "RND_FADE_09", make_round_fade(50, 0.09, 0.16), "XAUUSD")
_reg("crypto", "RND_FADE_07", make_round_fade(50, 0.07, 0.14), "XAUUSD")
_reg("crypto", "RND_FADE_T_NY", make_round_fade(50, 0.08, 0.15, 15, 18), "XAUUSD")

# ── Crypto: Asia / OF / burst ──
_reg("crypto", "ASIA_042_D18", make_asia_break(0.42, 0.58, 0.18, 1.75), "XAUUSD", one_per_day=True)
_reg("crypto", "ASIA_045_D18_MT", make_asia_break(0.45, 0.55, 0.18, 1.75), "XAUUSD", one_per_day=True)
_reg("crypto", "OF_NY_D38_TT", make_of_window(15.5, 17.5, 0.38, (1, 2, 3)), "XAUUSD")
_reg("crypto", "OF_NY_D32_LV", make_of_window(15.5, 17.5, 0.32, None, 1.05), "XAUUSD")
_reg("crypto", "OF_L_D32_MT", make_of_window(9, 11, 0.32, (0, 1, 2, 3), 1.05), "XAUUSD")
_reg("crypto", "DELTA_BURST_V2", s_xau_delta_burst_v2, "XAUUSD")

# ── Crypto: portar NR7/INSIDE (GO no WIN) ──
_reg("crypto", "XAU_NR7", s_xau_nr7, "XAUUSD")
_reg("crypto", "XAU_INSIDE", s_xau_inside, "XAUUSD")
_reg("crypto", "ETH_NR7", s_eth_nr7, "ETHUSD")
_reg("crypto", "ETH_INSIDE", s_eth_inside, "ETHUSD")
_reg("crypto", "ETH_RND_TIGHT", s_eth_rnd_tight, "ETHUSD")
_reg("crypto", "ETH_LH_048", make_london_handoff(0.48, 0.18, 1.75, (0, 1, 2, 3)), "ETHUSD", one_per_day=True)
_reg("crypto", "BTC_NR7", s_btc_nr7, "BTCUSD")
_reg("crypto", "BTC_INSIDE", s_xau_inside, "BTCUSD")
_reg("crypto", "BTC_LH_048", make_london_handoff(0.48, 0.18, 1.75), "BTCUSD", one_per_day=True)


def _sim(df, symbol, strat_fn, name, tf=15):
    from backtest_setups_novos_v2 import ONE_PER_DAY as _v2_opd
    added = name in ONE_PER_DAY and name not in _v2_opd
    if added:
        _v2_opd.add(name)
    try:
        return _simulate(df, symbol, strat_fn, name, tf=tf)
    finally:
        if added:
            _v2_opd.discard(name)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--grupo", choices=["b3", "crypto"], default="b3")
    ap.add_argument("--bars", type=int, default=300000)
    args = ap.parse_args()

    os.environ["KIMI_GROUP"] = args.grupo
    os.makedirs("logs", exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    logf = open(f"logs/backtest_setups_novos_v4_{args.grupo}_{stamp}.txt", "w", encoding="utf-8")

    def out(s):
        print(s)
        logf.write(s + "\n")
        logf.flush()

    strat_map = STRATS[args.grupo]
    out(f"{'#' * 74}\n# SETUPS NOVOS V4 ({len(strat_map)} setups) — {args.grupo} · "
        f"{datetime.now():%d/%m %H:%M}\n# NÃO altera motores · GO rigoroso\n{'#' * 74}")

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
    if args.grupo == "crypto":
        from backtest_crypto_pro import mt5_connect, fetch as _cp_fetch
        _mt5c = mt5_connect()

    def _get(k, tf):
        if args.grupo == "b3":
            return _b3_fetch(sym_full[k], tf, args.bars)
        from backtest_crypto_pro import fetch as _cp_fetch
        raw = _cp_fetch(_mt5c, sym_full[k], tf, args.bars)
        return add_indicators(raw) if raw is not None else None

    raw_dfs = {}
    for (k, tf) in sorted(needs.keys()):
        t0 = time.time()
        df = _get(k, tf)
        if df is None or len(df) < 3000:
            out(f"\n!! {k} M{tf}: sem histórico")
            continue
        raw_dfs[(k, tf)] = df
        bars_day = 26 if k in ("WIN", "WDO") else (288 if tf == 5 else 96)
        years_den = 252 if k in ("WIN", "WDO") else 365
        anos = len(df) / bars_day / years_den
        out(f"\n{'=' * 74}\n  {k} M{tf}: {len(df)} candles (~{anos:.1f}a)  "
            f"[{df.index[0].date()} → {df.index[-1].date()}] ({time.time() - t0:.0f}s)\n{'=' * 74}")

    ctx15 = {}
    if ("WIN", 15) in raw_dfs:
        ctx15["WIN"] = raw_dfs[("WIN", 15)]
    if ("BTCUSD", 15) in raw_dfs:
        ctx15["BTCUSD"] = raw_dfs[("BTCUSD", 15)]

    dfs = {}
    for key, df in raw_dfs.items():
        k, tf = key
        dfs[key] = add_extra_v2(df.copy(), sym_full[k], ctx=(ctx15 if tf == 15 else None))

    for name, fn in strat_map.items():
        k = STRAT_SYM[name]
        tf = STRAT_TF.get(name, 15)
        key = (k, tf)
        if key not in dfs:
            out(f"\n  ▸ {name} ({k} M{tf}): sem dados")
            continue
        df = dfs[key]
        t0 = time.time()
        t = _sim(df, sym_full[k], fn, name, tf=tf)
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
            t.to_csv(f"logs/novos_v4_{k}_M{tf}_{name}.csv", index=False)
        v = verdict(s, cons, s_out)
        out(f"      VEREDITO: {v}")
        ranking.append({"setup": name, "sym": k, "tf": tf, **s, "cons%": cons, "veredito": v})

    out(f"\n{'#' * 74}\n  RANKING\n{'#' * 74}")
    rk = pd.DataFrame(ranking)
    goes = []
    if not rk.empty:
        rk = rk.sort_values("net", ascending=False)
        for _, r in rk.iterrows():
            if r.get("n", 0) == 0:
                out(f"  {r.sym:<7} M{int(r.tf)} {r.setup:<22} sem trades")
                continue
            out(f"  {r.sym:<7} M{int(r.tf)} {r.setup:<22} n={int(r.n):<5} net {r.net:+.3f} R  "
                f"PF {r.PF:<5} cons {r['cons%']}%  {r.veredito}")
            if "🟢" in str(r.veredito) and "GO" in str(r.veredito):
                goes.append(r.setup)
    out(f"\n# 🟢 GO encontrados: {goes if goes else 'nenhum'}")
    out(f"# Critério: net≥+0,10 · PF≥1,25 · cons≥55% · OOS segura · n≥40")
    out(f"# FIM — logs/backtest_setups_novos_v4_{args.grupo}_{stamp}.txt")
    logf.close()


if __name__ == "__main__":
    main()
