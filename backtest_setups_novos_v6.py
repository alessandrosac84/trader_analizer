"""
backtest_setups_novos_v6.py — RODADA 6: empurrar 🟡 + ganhar frequência com qualidade.

Régua idêntica (custos, OOS 70/30, bimestre, GO). NÃO altera motores ao vivo.

Contexto: poucos setups → semanas sem trade. Meta = mais caminhos 🟢 GO,
sem baixar a régua (net≥+0,10 · PF≥1,25 · cons≥55% · OOS · n≥40).

Foco:
  · Refinar 🟡 fortes da v5 (OVN_PDC_*, NR5, OUTSIDE_REV, HL, XAU_NR5, LH_*)
  · Variantes da família GO (NR7/INSIDE) que aumentam frequência e ainda passam
  · Crypto: NR5/INSIDE/RND/LH refinados

Uso:
  python rodar_backtest_setups_novos_v6.py
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

from backtest_kimi_real import fetch as _b3_fetch, add_indicators
from backtest_setups_novos import (
    stats, consistency, verdict, _line, _sig, _parts, OOS_FRAC, _WIN_TICK,
)
from backtest_setups_novos_v2 import add_extra_v2, _simulate, _of
from backtest_setups_novos_v3 import (
    make_ovn_pdc, make_ovn_atr, make_gap_fade, make_ib_break,
    make_of_window, make_asia_break, make_round_fade, make_london_handoff,
    s_win_nr7_break, s_win_inside_bar_break,
)
from backtest_setups_novos_v4 import s_win_nr7_trend, s_win_inside_vol15
from backtest_setups_novos_v5 import (
    _nr_break, _h4_ok, _gap_ok,
    s_nr5_break, s_outside_rev, s_hl_break, s_bb_squeeze_brk,
    s_vwap_pb_trend, s_pdh_break_vol, s_inside_h4, s_nr7_h4_gap,
    s_xau_nr5, s_xau_inside_h4, s_xau_wick_fade,
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
# B3 — refinos
# ════════════════════════════════════════════════════════════════════════════

def s_nr5_h4(df, i, sym):
    base = s_nr5_break(df, i, sym)
    if not base:
        return None
    r = df.iloc[i]
    if not _h4_ok(r, base["dir"]):
        return None
    return base


def s_nr5_h4_mt(df, i, sym):
    r = df.iloc[i]
    if int(r.dow) not in (0, 1, 2, 3):
        return None
    return s_nr5_h4(df, i, sym)


def s_nr5_am(df, i, sym):
    return _nr_break(df, i, n=5, vol_mult=1.3, hh0=10.0, hh1=12.5)


def s_nr5_vol14(df, i, sym):
    return _nr_break(df, i, n=5, vol_mult=1.4, hh0=10.0, hh1=14.0)


def s_nr4_h4(df, i, sym):
    base = _nr_break(df, i, n=4, vol_mult=1.3, hh0=10.0, hh1=14.0)
    if not base:
        return None
    r = df.iloc[i]
    if not _h4_ok(r, base["dir"]):
        return None
    return base


def s_nr7_1014_h4(df, i, sym):
    """NR7 janela 10–14 (um pouco mais largo que morning) + H4."""
    base = _nr_break(df, i, n=7, vol_mult=1.3, hh0=10.0, hh1=14.0)
    if not base:
        return None
    r = df.iloc[i]
    if not _h4_ok(r, base["dir"]):
        return None
    return base


def s_outside_h4(df, i, sym):
    base = s_outside_rev(df, i, sym)
    if not base:
        return None
    r = df.iloc[i]
    if not _h4_ok(r, base["dir"]):
        return None
    return base


def s_outside_vol15(df, i, sym):
    if i < 2:
        return None
    r, p = df.iloc[i], df.iloc[i - 1]
    if not (10.0 <= r.hh < 13.5):
        return None
    if not (r.High > p.High and r.Low < p.Low):
        return None
    if r.Volume < 1.5 * (r.vol8 or 0):
        return None
    body, rng, uw, dw = _parts(r)
    if pd.isna(r.gap_pct):
        return None
    if r.gap_pct > 0.1 and r.Close < r.Open and uw >= 0.35 * rng:
        return _sig("VENDA", r.Close, r.High + _WIN_TICK, 1.5, 2.5, True)
    if r.gap_pct < -0.1 and r.Close > r.Open and dw >= 0.35 * rng:
        return _sig("COMPRA", r.Close, r.Low - _WIN_TICK, 1.5, 2.5, True)
    return None


def s_outside_h4_mt(df, i, sym):
    r = df.iloc[i]
    if int(r.dow) not in (0, 1, 2, 3):
        return None
    return s_outside_h4(df, i, sym)


def s_hl_h4(df, i, sym):
    base = s_hl_break(df, i, sym)
    if not base:
        return None
    r = df.iloc[i]
    if not _h4_ok(r, base["dir"]):
        return None
    return base


def s_hl_h4_mt(df, i, sym):
    r = df.iloc[i]
    if int(r.dow) not in (0, 1, 2, 3):
        return None
    return s_hl_h4(df, i, sym)


def s_bb_h4(df, i, sym):
    base = s_bb_squeeze_brk(df, i, sym)
    if not base:
        return None
    r = df.iloc[i]
    if not _h4_ok(r, base["dir"]):
        return None
    return base


def s_vwap_pb_tight(df, i, sym):
    """VWAP PB mais seletivo: |dist|≤0.55 · vol≥1.25 · 10:30–12:30."""
    if i < 2:
        return None
    r, p = df.iloc[i], df.iloc[i - 1]
    if not (10.5 <= r.hh < 12.5):
        return None
    if pd.isna(r.vwap) or pd.isna(r.vwap_dist) or pd.isna(r.h4_close):
        return None
    if abs(r.vwap_dist) > 0.55:
        return None
    if r.Volume < 1.25 * (r.vol8 or 0):
        return None
    up = r.h4_close > r.h4_ema50
    if up and p.Low <= p.vwap and r.Close > r.vwap and r.Close > r.Open:
        return _sig("COMPRA", r.Close, min(p.Low, r.Low) - _WIN_TICK, 1.5, 2.5, True)
    if (not up) and p.High >= p.vwap and r.Close < r.vwap and r.Close < r.Open:
        return _sig("VENDA", r.Close, max(p.High, r.High) + _WIN_TICK, 1.5, 2.5, True)
    return None


def s_inside_h4_gap_mt(df, i, sym):
    r = df.iloc[i]
    if int(r.dow) not in (0, 1, 2, 3):
        return None
    base = s_inside_h4(df, i, sym)
    if not base:
        return None
    if not _gap_ok(r, base["dir"], 0.08):
        return None
    return base


def s_inside_1015_h4(df, i, sym):
    """Inside 10–15h + H4 (mais frequência que AM, ainda filtrado)."""
    if i < 3:
        return None
    r = df.iloc[i]
    p, pp = df.iloc[i - 1], df.iloc[i - 2]
    if not (10.0 <= r.hh < 15.0):
        return None
    if not (p.High <= pp.High and p.Low >= pp.Low):
        return None
    if r.Volume < 1.2 * (r.vol8 or 0):
        return None
    base = None
    if r.Close > p.High:
        base = _sig("COMPRA", r.Close, p.Low, 1.5, 2.5, True)
    elif r.Close < p.Low:
        base = _sig("VENDA", r.Close, p.High, 1.5, 2.5, True)
    if not base or not _h4_ok(r, base["dir"]):
        return None
    return base


def s_inside_vol13_h4(df, i, sym):
    """Entre INSIDE (1.2) e V18 (1.8): vol 1.3 + H4."""
    if i < 3:
        return None
    r = df.iloc[i]
    p, pp = df.iloc[i - 1], df.iloc[i - 2]
    if not (10.0 <= r.hh < 14.0):
        return None
    if not (p.High <= pp.High and p.Low >= pp.Low):
        return None
    if r.Volume < 1.3 * (r.vol8 or 0):
        return None
    base = None
    if r.Close > p.High:
        base = _sig("COMPRA", r.Close, p.Low, 1.5, 2.5, True)
    elif r.Close < p.Low:
        base = _sig("VENDA", r.Close, p.High, 1.5, 2.5, True)
    if not base or not _h4_ok(r, base["dir"]):
        return None
    return base


def s_pdh_h4_vol12(df, i, sym):
    """PDH break vol 1.2 (mais trades) + H4 · 11–15h."""
    if i < 2:
        return None
    r, p = df.iloc[i], df.iloc[i - 1]
    if not (11.0 <= r.hh < 15.0):
        return None
    if pd.isna(r.prev_day_hi) or pd.isna(r.prev_day_lo):
        return None
    if r.Volume < 1.2 * (r.vol8 or 0):
        return None
    base = None
    if r.Close > r.prev_day_hi and p.Close <= r.prev_day_hi:
        base = _sig("COMPRA", r.Close, r.prev_day_hi - (r.atr or 50) * 0.3, 1.5, 2.5, True)
    elif r.Close < r.prev_day_lo and p.Close >= r.prev_day_lo:
        base = _sig("VENDA", r.Close, r.prev_day_lo + (r.atr or 50) * 0.3, 1.5, 2.5, True)
    if not base or not _h4_ok(r, base["dir"]):
        return None
    return base


def s_ovn_pdc_vol(df, i, sym):
    """OVN PDC 0.34 + EXT + vol≥1.15 (tentar OOS com mais amostra)."""
    base = make_ovn_pdc(0.34, 9.25, 9.85, True, 4)(df, i, sym)
    if not base:
        return None
    r = df.iloc[i]
    if r.Volume < 1.15 * (r.vol8 or 0):
        return None
    return base


def s_inside_vol15_h4(df, i, sym):
    base = s_win_inside_vol15(df, i, sym)
    if not base:
        return None
    r = df.iloc[i]
    if not _h4_ok(r, base["dir"]):
        return None
    return base


def s_xau_inside_am(df, i, sym):
    r = df.iloc[i]
    if not (9 <= r.hh < 12):
        return None
    return s_xau_inside_h4(df, i, sym)


# ════════════════════════════════════════════════════════════════════════════
# CRYPTO
# ════════════════════════════════════════════════════════════════════════════

def s_xau_nr5_h4(df, i, sym):
    base = s_xau_nr5(df, i, sym)
    if not base:
        return None
    r = df.iloc[i]
    if not _h4_ok(r, base["dir"]):
        return None
    return base


def s_xau_nr5_mt(df, i, sym):
    r = df.iloc[i]
    if int(r.dow) not in (0, 1, 2, 3):
        return None
    return s_xau_nr5_h4(df, i, sym)


def s_xau_nr5_vol14(df, i, sym):
    return _nr_break(df, i, n=5, vol_mult=1.4, hh0=9.0, hh1=13.0)


def s_xau_inside_vol15_h4(df, i, sym):
    if i < 3:
        return None
    r = df.iloc[i]
    p, pp = df.iloc[i - 1], df.iloc[i - 2]
    if not (9.0 <= r.hh < 14.0):
        return None
    if not (p.High <= pp.High and p.Low >= pp.Low):
        return None
    if r.Volume < 1.5 * (r.vol8 or 0):
        return None
    base = None
    if r.Close > p.High:
        base = _sig("COMPRA", r.Close, p.Low, 1.5, 2.5, True)
    elif r.Close < p.Low:
        base = _sig("VENDA", r.Close, p.High, 1.5, 2.5, True)
    if not base or not _h4_ok(r, base["dir"]):
        return None
    return base


def s_xau_rnd_06_mt(df, i, sym):
    r = df.iloc[i]
    if int(r.dow) not in (0, 1, 2, 3):
        return None
    return make_round_fade(50, 0.06, 0.13)(df, i, sym)


def s_xau_of_lon_h4(df, i, sym):
    base = make_of_window(9, 11, 0.34, (0, 1, 2, 3))(df, i, sym)
    if not base:
        return None
    r = df.iloc[i]
    if not _h4_ok(r, base["dir"]):
        return None
    return base


# ════════════════════════════════════════════════════════════════════════════
# REGISTRO
# ════════════════════════════════════════════════════════════════════════════

# ── OVN / gap (amarelos com net alto, OOS curto) ──
_reg("b3", "OVN_PDC_034_EXT", make_ovn_pdc(0.34, 9.25, 9.85, True, 4), "WIN", one_per_day=True)
_reg("b3", "OVN_PDC_034_VOL", s_ovn_pdc_vol, "WIN", one_per_day=True)
_reg("b3", "OVN_PDC_030_EXT_MT", make_ovn_pdc(0.30, 9.25, 9.85, True, 3), "WIN", one_per_day=True)
_reg("b3", "OVN_PDC_037_0945", make_ovn_pdc(0.37, 9.25, 9.75, True, 4), "WIN", one_per_day=True)
_reg("b3", "OVN_PDC_035_NOEXT", make_ovn_pdc(0.35, 9.25, 9.85, False, 4), "WIN", one_per_day=True)
_reg("b3", "OVN_ATR_12_PDC_MT", make_ovn_atr(1.2, 1.25, 9.25, 9.85, 3, True), "WIN", one_per_day=True)
_reg("b3", "GAP_FADE_030_ATR", make_gap_fade(0.30, 1.0, 9.5, 1.5, 2.5), "WIN", one_per_day=True)

# ── Família NR (frequência + filtro) ──
_reg("b3", "NR5_H4", s_nr5_h4, "WIN")
_reg("b3", "NR5_H4_MT", s_nr5_h4_mt, "WIN")
_reg("b3", "NR5_AM", s_nr5_am, "WIN")
_reg("b3", "NR5_VOL14", s_nr5_vol14, "WIN")
_reg("b3", "NR4_H4", s_nr4_h4, "WIN")
_reg("b3", "NR7_1014_H4", s_nr7_1014_h4, "WIN")
_reg("b3", "NR7_H4_GAP_V2", s_nr7_h4_gap, "WIN")

# ── Família INSIDE ──
_reg("b3", "INSIDE_1015_H4", s_inside_1015_h4, "WIN")
_reg("b3", "INSIDE_V13_H4", s_inside_vol13_h4, "WIN")
_reg("b3", "INSIDE_H4_GAP_MT", s_inside_h4_gap_mt, "WIN")
_reg("b3", "INSIDE_VOL15_H4", s_inside_vol15_h4, "WIN")

# ── OUTSIDE / HL / BB / VWAP / PDH ──
_reg("b3", "OUTSIDE_H4", s_outside_h4, "WIN")
_reg("b3", "OUTSIDE_VOL15", s_outside_vol15, "WIN")
_reg("b3", "OUTSIDE_H4_MT", s_outside_h4_mt, "WIN")
_reg("b3", "HL_H4", s_hl_h4, "WIN")
_reg("b3", "HL_H4_MT", s_hl_h4_mt, "WIN")
_reg("b3", "BB_H4", s_bb_h4, "WIN")
_reg("b3", "VWAP_PB_TIGHT", s_vwap_pb_tight, "WIN")
_reg("b3", "PDH_H4_V12", s_pdh_h4_vol12, "WIN")
_reg("b3", "IB_BRK_14_GAP", make_ib_break(1.4, 10, 12.0, True), "WIN", one_per_day=True)

# ── WDO espelhos dos melhores moldes ──
_reg("b3", "WDO_NR5_H4", s_nr5_h4, "WDO")
_reg("b3", "WDO_INSIDE_V13", s_inside_vol13_h4, "WDO")
_reg("b3", "WDO_OUTSIDE_H4", s_outside_h4, "WDO")
_reg("b3", "WDO_OVN_034", make_ovn_pdc(0.34, 9.25, 9.85, True, 4), "WDO", one_per_day=True)

# ── Crypto XAU ──
_reg("crypto", "XAU_NR5_H4", s_xau_nr5_h4, "XAUUSD")
_reg("crypto", "XAU_NR5_MT", s_xau_nr5_mt, "XAUUSD")
_reg("crypto", "XAU_NR5_V14", s_xau_nr5_vol14, "XAUUSD")
_reg("crypto", "XAU_INS_V15_H4", s_xau_inside_vol15_h4, "XAUUSD")
_reg("crypto", "XAU_INS_AM", s_xau_inside_am, "XAUUSD")
_reg("crypto", "RND_FADE_065", make_round_fade(50, 0.065, 0.135), "XAUUSD")
_reg("crypto", "RND_FADE_06_MT", s_xau_rnd_06_mt, "XAUUSD")
_reg("crypto", "RND_FADE_075_MT", make_round_fade(50, 0.075, 0.145, None, None, (0, 1, 2, 3)), "XAUUSD")
_reg("crypto", "RND_FADE_LON_07", make_round_fade(50, 0.07, 0.14, 9, 13), "XAUUSD")
_reg("crypto", "LH_048_D19", make_london_handoff(0.48, 0.19, 1.75), "XAUUSD", one_per_day=True)
_reg("crypto", "LH_050_D19_MT", make_london_handoff(0.50, 0.19, 1.75, (0, 1, 2, 3)), "XAUUSD", one_per_day=True)
_reg("crypto", "LH_047_D19_MT", make_london_handoff(0.47, 0.19, 1.75, (0, 1, 2, 3)), "XAUUSD", one_per_day=True)
_reg("crypto", "LH_049_D18_MT", make_london_handoff(0.49, 0.18, 1.75, (0, 1, 2, 3)), "XAUUSD", one_per_day=True)
_reg("crypto", "ASIA_043_D18", make_asia_break(0.43, 0.55, 0.18, 1.75), "XAUUSD", one_per_day=True)
_reg("crypto", "OF_L_D34_H4", s_xau_of_lon_h4, "XAUUSD")
_reg("crypto", "OF_NY_D33_MT", make_of_window(15.5, 17.5, 0.33, (0, 1, 2, 3)), "XAUUSD")

# ── ETH ──
_reg("crypto", "ETH_NR5_H4", s_xau_nr5_h4, "ETHUSD")
_reg("crypto", "ETH_INS_V15_H4", s_xau_inside_vol15_h4, "ETHUSD")
_reg("crypto", "ETH_RND_MT", make_round_fade(100, 0.08, 0.15, None, None, (0, 1, 2, 3)), "ETHUSD")
_reg("crypto", "ETH_LH_049", make_london_handoff(0.49, 0.19, 1.75, (0, 1, 2, 3)), "ETHUSD", one_per_day=True)
_reg("crypto", "ETH_OF_L_D30", make_of_window(9, 11, 0.30, (0, 1, 2, 3)), "ETHUSD")

# ── BTC (só se passar — histórico curto) ──
_reg("crypto", "BTC_NR5_H4", s_xau_nr5_h4, "BTCUSD")
_reg("crypto", "BTC_INS_H4", s_xau_inside_h4, "BTCUSD")
_reg("crypto", "BTC_RND_MT", make_round_fade(500, 0.07, 0.14, None, None, (0, 1, 2, 3)), "BTCUSD")


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
    logf = open(f"logs/backtest_setups_novos_v6_{args.grupo}_{stamp}.txt", "w", encoding="utf-8")

    def out(s):
        print(s)
        logf.write(s + "\n")
        logf.flush()

    strat_map = STRATS[args.grupo]
    out(f"{'#' * 74}\n# SETUPS NOVOS V6 ({len(strat_map)} setups) — {args.grupo} · "
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
        from backtest_crypto_pro import mt5_connect
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
            t.to_csv(f"logs/novos_v6_{k}_M{tf}_{name}.csv", index=False)
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
    out(f"# FIM — logs/backtest_setups_novos_v6_{args.grupo}_{stamp}.txt")
    logf.close()


if __name__ == "__main__":
    main()
