"""
backtest_setups_novos_v14_btc_eth.py — BTC/ETH FDS · noite · Asia · refine 🟡.

Foco: cobertura fora do pregão FX (noite/FDS) + buracos ETH 24h.
NÃO retesta GOs diurnos 08–18 já live. Refina near-miss v13 com MT/vol/RR.
ORDER_FLOW BTC: pulado (🔴 OOS histórico).

Régua GO: net ≥ +0,10 R · PF ≥ 1,25 · cons ≥ 55% · OOS segura · n ≥ 40.
NÃO altera live até 🟢.

Uso:
  python backtest_setups_novos_v14_btc_eth.py
  python backtest_setups_novos_v14_btc_eth.py --syms BTCUSD
  python backtest_setups_novos_v14_btc_eth.py --bars 200000
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

import pandas as pd

from backtest_kimi_real import add_indicators
from backtest_setups_novos import stats, consistency, verdict, _line, OOS_FRAC, _sig
from backtest_setups_novos_v2 import add_extra_v2, _simulate
from backtest_setups_novos_v5 import _h4_ok
from backtest_setups_novos_v9_refine import _mt
from backtest_setups_novos_v13_btc_24h import (
    _hh_ok, _inside_h, _nr_h, _impulse_h, _h4_pb_ema_h, _volspike_h,
    _outside_h, _hl_h, _dbl_inside_h, _load_ohlc,
)

STRATS = {}
STRAT_SYM = {}
ONE_PER_DAY = set()


def _reg(name, fn, sym="BTCUSD", one_per_day=False):
    STRATS[name] = fn
    STRAT_SYM[name] = sym
    if one_per_day:
        ONE_PER_DAY.add(name)


def _we(fn):
    """Só sábado/domingo (dow 5,6) — cobertura FDS pura."""
    def _f(df, i, sym):
        if int(df.iloc[i].dow) not in (5, 6):
            return None
        return fn(df, i, sym)
    return _f


def _fri_sun(fn):
    """Sex 18h → Dom 24h (proxy FDS estendido via dow)."""
    def _f(df, i, sym):
        r = df.iloc[i]
        d = int(r.dow)
        if d == 4 and float(r.hh) < 18.0:
            return None
        if d not in (4, 5, 6):
            return None
        return fn(df, i, sym)
    return _f


def _pdh_h(vol=1.2, hh0=0.0, hh1=24.0, rr1=1.5, rr2=2.0):
    def _f(df, i, sym):
        if i < 30:
            return None
        r = df.iloc[i]
        if not _hh_ok(r.hh, hh0, hh1):
            return None
        if float(r.Volume or 0) < vol * float(r.vol8 or 0):
            return None
        win = df.iloc[max(0, i - 96):i]
        if len(win) < 20:
            return None
        pdh, pdl = float(win.High.max()), float(win.Low.min())
        p = df.iloc[i - 1]
        if r.Close > pdh and p.Close <= pdh:
            base = _sig("COMPRA", r.Close, p.Low, rr1, rr2, True)
        elif r.Close < pdl and p.Close >= pdl:
            base = _sig("VENDA", r.Close, p.High, rr1, rr2, True)
        else:
            return None
        if not base or not _h4_ok(r, base["dir"]):
            return None
        return base
    return _f


def _engulf_h(vol=1.3, hh0=0.0, hh1=24.0):
    def _f(df, i, sym):
        if i < 3:
            return None
        r, p = df.iloc[i], df.iloc[i - 1]
        if not _hh_ok(r.hh, hh0, hh1):
            return None
        if float(r.Volume or 0) < vol * float(r.vol8 or 0):
            return None
        bull = (float(r.Close) > float(r.Open)
                and float(p.Close) < float(p.Open)
                and float(r.Close) >= float(p.Open)
                and float(r.Open) <= float(p.Close)
                and float(r.Close) > float(p.High))
        bear = (float(r.Close) < float(r.Open)
                and float(p.Close) > float(p.Open)
                and float(r.Close) <= float(p.Open)
                and float(r.Open) >= float(p.Close)
                and float(r.Close) < float(p.Low))
        if bull:
            d, sl = "COMPRA", float(min(p.Low, r.Low))
        elif bear:
            d, sl = "VENDA", float(max(p.High, r.High))
        else:
            return None
        if not _h4_ok(r, d):
            return None
        return _sig(d, float(r.Close), sl, 1.5, 2.0, True)
    return _f


def _sqz_break_h(vol=1.4, hh0=0.0, hh1=24.0):
    """Compressão BB (largura no mínimo 20) + rompe + vol + H4."""
    def _f(df, i, sym):
        if i < 25:
            return None
        r = df.iloc[i]
        if not _hh_ok(r.hh, hh0, hh1):
            return None
        if float(r.Volume or 0) < vol * float(r.vol8 or 0):
            return None
        # bb_width se existir; senão proxy ATR vs range médio
        widths = []
        for j in range(i - 20, i):
            row = df.iloc[j]
            atr = float(getattr(row, "atr", 0) or 0)
            rng = float(row.High - row.Low)
            if atr > 0:
                widths.append(rng / atr)
        if len(widths) < 15:
            return None
        if widths[-1] > min(widths) + 1e-12:
            return None  # não é a barra mais comprimida
        p = df.iloc[i - 1]
        if r.Close > p.High:
            base = _sig("COMPRA", r.Close, p.Low, 1.5, 2.5, True)
        elif r.Close < p.Low:
            base = _sig("VENDA", r.Close, p.High, 1.5, 2.5, True)
        else:
            return None
        if not base or not _h4_ok(r, base["dir"]):
            return None
        return base
    return _f


def _ema_reclaim_h(hh0=0.0, hh1=24.0, vol=1.2):
    """Fecha de volta acima/abaixo EMA21 após poke + H4."""
    def _f(df, i, sym):
        if i < 5:
            return None
        r, p = df.iloc[i], df.iloc[i - 1]
        if not _hh_ok(r.hh, hh0, hh1):
            return None
        e20 = r.ema21
        if pd.isna(e20) or pd.isna(r.atr) or r.atr <= 0:
            return None
        if float(r.Volume or 0) < vol * float(r.vol8 or 0):
            return None
        e = float(e20)
        # poke below then reclaim
        if float(p.Low) < e and float(r.Close) > e and float(r.Close) > float(r.Open):
            d, sl = "COMPRA", float(min(p.Low, r.Low))
        elif float(p.High) > e and float(r.Close) < e and float(r.Close) < float(r.Open):
            d, sl = "VENDA", float(max(p.High, r.High))
        else:
            return None
        if not _h4_ok(r, d):
            return None
        return _sig(d, float(r.Close), sl, 1.5, 2.0, True)
    return _f


# ═══════════════════════════════════════════════════════════
# BTC — FDS / noite / Asia / refine 🟡 v13
# ═══════════════════════════════════════════════════════════
# Weekend-only (sáb/dom)
_reg("BTC_WE_INS_V13", _we(_inside_h(1.3, 0.0, 24.0)))
_reg("BTC_WE_INS_V14", _we(_inside_h(1.4, 0.0, 24.0)))
_reg("BTC_WE_NR5", _we(_nr_h(5, 1.3, 0.0, 24.0)))
_reg("BTC_WE_NR4", _we(_nr_h(4, 1.3, 0.0, 24.0)))
_reg("BTC_WE_IMP", _we(_impulse_h(2.0, 1.5, 0.0, 24.0, 2.0)))
_reg("BTC_WE_HL", _we(_hl_h(1.3, 0.0, 24.0)))
_reg("BTC_WE_PDH", _we(_pdh_h(1.2, 0.0, 24.0)))
_reg("BTC_FRI_SUN_INS", _fri_sun(_inside_h(1.3, 0.0, 24.0)))
_reg("BTC_FRI_SUN_IMP", _fri_sun(_impulse_h(2.0, 1.5, 0.0, 24.0, 2.0)))

# PDH 24h / Asia / OVN (diurno 10–16 já GO)
_reg("BTC_PDH_24H", _pdh_h(1.2, 0.0, 24.0))
_reg("BTC_PDH_24H_V13", _pdh_h(1.3, 0.0, 24.0))
_reg("BTC_PDH_ASIA_0008", _pdh_h(1.2, 0.0, 8.0))
_reg("BTC_PDH_OVN_1808", _pdh_h(1.2, 18.0, 8.0))
_reg("BTC_PDH_NIGHT_2010", _pdh_h(1.2, 20.0, 10.0))
_reg("BTC_PDH_24H_MT", _mt(_pdh_h(1.2, 0.0, 24.0)))

# HL refine (v13 amarelo)
_reg("BTC_HL_24H_V14", _hl_h(1.4, 0.0, 24.0))
_reg("BTC_HL_24H_V15", _hl_h(1.5, 0.0, 24.0))
_reg("BTC_HL_24H_MT", _mt(_hl_h(1.3, 0.0, 24.0)))
_reg("BTC_HL_ASIA_0008", _hl_h(1.3, 0.0, 8.0))
_reg("BTC_HL_OVN_1808", _hl_h(1.3, 18.0, 8.0))
_reg("BTC_HL_NIGHT_2010", _hl_h(1.35, 20.0, 10.0))

# NR refine PF near-miss
_reg("BTC_NR4_24H_V14", _nr_h(4, 1.4, 0.0, 24.0))
_reg("BTC_NR4_24H_MT", _mt(_nr_h(4, 1.3, 0.0, 24.0)))
_reg("BTC_NR5_24H_V14", _nr_h(5, 1.4, 0.0, 24.0))
_reg("BTC_NR5_24H_MT", _mt(_nr_h(5, 1.3, 0.0, 24.0)))
_reg("BTC_NR7_ASIA_0008", _nr_h(7, 1.35, 0.0, 8.0))
_reg("BTC_NR5_NIGHT_2010", _nr_h(5, 1.35, 20.0, 10.0))
_reg("BTC_NR4_OVN_1808", _nr_h(4, 1.35, 18.0, 8.0))

# Impulse refine OOS (ASIA/OVN eram 🟡)
_reg("BTC_IMP_ASIA_MT", _mt(_impulse_h(1.8, 1.4, 0.0, 8.0, 2.0)))
_reg("BTC_IMP_OVN_MT", _mt(_impulse_h(2.0, 1.5, 18.0, 8.0, 2.0)))
_reg("BTC_IMP_CONT_24H_MT", _mt(_impulse_h(2.0, 1.5, 0.0, 24.0, 2.0)))
_reg("BTC_IMP_CONT_24H_V18", _impulse_h(1.8, 1.6, 0.0, 24.0, 2.0))
_reg("BTC_IMP_NIGHT_2010", _impulse_h(2.0, 1.5, 20.0, 10.0, 2.0))
_reg("BTC_IMP_TP25_24H", _impulse_h(2.0, 1.5, 0.0, 24.0, 2.5))
_reg("BTC_VOLSPIKE_ASIA", _volspike_h(2.0, 1.5, 0.0, 8.0))
_reg("BTC_VOLSPIKE_OVN", _volspike_h(2.0, 1.5, 18.0, 8.0))

# Estrutura / reclaim / squeeze
_reg("BTC_OUT_ASIA_0008", _outside_h(1.3, 0.0, 8.0))
_reg("BTC_OUT_OVN_1808", _outside_h(1.3, 18.0, 8.0))
_reg("BTC_OUT_NIGHT_2010", _outside_h(1.35, 20.0, 10.0))
_reg("BTC_DBL_INS_ASIA", _dbl_inside_h(1.3, 0.0, 8.0))
_reg("BTC_DBL_INS_OVN", _dbl_inside_h(1.3, 18.0, 8.0))
_reg("BTC_ENGULF_24H", _engulf_h(1.3, 0.0, 24.0))
_reg("BTC_ENGULF_ASIA", _engulf_h(1.3, 0.0, 8.0))
_reg("BTC_SQZ_24H", _sqz_break_h(1.4, 0.0, 24.0))
_reg("BTC_SQZ_ASIA", _sqz_break_h(1.4, 0.0, 8.0))
_reg("BTC_EMA_RECL_24H", _ema_reclaim_h(0.0, 24.0, 1.2))
_reg("BTC_EMA_RECL_OVN", _ema_reclaim_h(18.0, 8.0, 1.2))
_reg("BTC_H4_PB_EMA_NIGHT", _h4_pb_ema_h(20.0, 10.0))
_reg("BTC_H4_PB_EMA_MT24", _mt(_h4_pb_ema_h(0.0, 24.0)))

# Evening crypto (fora FX late) 16–22 / 18–22
_reg("BTC_INS_1622_V13", _inside_h(1.3, 16.0, 22.0))
_reg("BTC_INS_1822_V13", _inside_h(1.3, 18.0, 22.0))
_reg("BTC_NR5_1622", _nr_h(5, 1.3, 16.0, 22.0))
_reg("BTC_IMP_1622", _impulse_h(2.0, 1.5, 16.0, 22.0, 2.0))

# ═══════════════════════════════════════════════════════════
# ETH — buraco grande em 24h (só IMP_CONT live)
# ═══════════════════════════════════════════════════════════
_reg("ETH_INS_24H_V13", _inside_h(1.3, 0.0, 24.0), "ETHUSD")  # control v13
_reg("ETH_INS_24H_V14", _inside_h(1.4, 0.0, 24.0), "ETHUSD")
_reg("ETH_INS_24H_V135", _inside_h(1.35, 0.0, 24.0), "ETHUSD")
_reg("ETH_INS_24H_MT", _mt(_inside_h(1.3, 0.0, 24.0)), "ETHUSD")
_reg("ETH_INS_ASIA_0008", _inside_h(1.3, 0.0, 8.0), "ETHUSD")
_reg("ETH_INS_ASIA_2206", _inside_h(1.3, 22.0, 6.0), "ETHUSD")
_reg("ETH_INS_OVN_1808", _inside_h(1.3, 18.0, 8.0), "ETHUSD")
_reg("ETH_INS_NIGHT_2010", _inside_h(1.3, 20.0, 10.0), "ETHUSD")
_reg("ETH_INS_1622_V13", _inside_h(1.3, 16.0, 22.0), "ETHUSD")
_reg("ETH_WE_INS_V13", _we(_inside_h(1.3, 0.0, 24.0)), "ETHUSD")
_reg("ETH_FRI_SUN_INS", _fri_sun(_inside_h(1.3, 0.0, 24.0)), "ETHUSD")

_reg("ETH_NR5_24H", _nr_h(5, 1.3, 0.0, 24.0), "ETHUSD")
_reg("ETH_NR5_24H_V14", _nr_h(5, 1.4, 0.0, 24.0), "ETHUSD")
_reg("ETH_NR4_24H", _nr_h(4, 1.3, 0.0, 24.0), "ETHUSD")
_reg("ETH_NR4_24H_V14", _nr_h(4, 1.4, 0.0, 24.0), "ETHUSD")
_reg("ETH_NR5_ASIA_0008", _nr_h(5, 1.3, 0.0, 8.0), "ETHUSD")
_reg("ETH_NR5_OVN_1808", _nr_h(5, 1.3, 18.0, 8.0), "ETHUSD")
_reg("ETH_NR4_ASIA_0008", _nr_h(4, 1.35, 0.0, 8.0), "ETHUSD")
_reg("ETH_NR5_24H_MT", _mt(_nr_h(5, 1.3, 0.0, 24.0)), "ETHUSD")
_reg("ETH_WE_NR5", _we(_nr_h(5, 1.3, 0.0, 24.0)), "ETHUSD")

_reg("ETH_HL_24H", _hl_h(1.3, 0.0, 24.0), "ETHUSD")
_reg("ETH_HL_24H_V14", _hl_h(1.4, 0.0, 24.0), "ETHUSD")
_reg("ETH_HL_24H_MT", _mt(_hl_h(1.3, 0.0, 24.0)), "ETHUSD")
_reg("ETH_HL_ASIA_0008", _hl_h(1.3, 0.0, 8.0), "ETHUSD")
_reg("ETH_HL_OVN_1808", _hl_h(1.3, 18.0, 8.0), "ETHUSD")
_reg("ETH_WE_HL", _we(_hl_h(1.3, 0.0, 24.0)), "ETHUSD")

_reg("ETH_PDH_24H", _pdh_h(1.2, 0.0, 24.0), "ETHUSD")
_reg("ETH_PDH_24H_V13", _pdh_h(1.3, 0.0, 24.0), "ETHUSD")
_reg("ETH_PDH_1016", _pdh_h(1.2, 10.0, 16.0), "ETHUSD")
_reg("ETH_PDH_ASIA_0008", _pdh_h(1.2, 0.0, 8.0), "ETHUSD")
_reg("ETH_PDH_OVN_1808", _pdh_h(1.2, 18.0, 8.0), "ETHUSD")
_reg("ETH_PDH_24H_MT", _mt(_pdh_h(1.2, 0.0, 24.0)), "ETHUSD")
_reg("ETH_WE_PDH", _we(_pdh_h(1.2, 0.0, 24.0)), "ETHUSD")

_reg("ETH_IMP_CONT_24H_MT", _mt(_impulse_h(2.0, 1.5, 0.0, 24.0, 2.0)), "ETHUSD")
_reg("ETH_IMP_CONT_24H_22", _impulse_h(2.2, 1.5, 0.0, 24.0, 2.0), "ETHUSD")
_reg("ETH_IMP_CONT_24H_V18", _impulse_h(1.8, 1.6, 0.0, 24.0, 2.0), "ETHUSD")
_reg("ETH_IMP_ASIA_0008", _impulse_h(1.8, 1.4, 0.0, 8.0, 2.0), "ETHUSD")
_reg("ETH_IMP_OVN_1808", _impulse_h(2.0, 1.5, 18.0, 8.0, 2.0), "ETHUSD")
_reg("ETH_IMP_NIGHT_2010", _impulse_h(2.0, 1.5, 20.0, 10.0, 2.0), "ETHUSD")
_reg("ETH_IMP_ASIA_MT", _mt(_impulse_h(1.8, 1.4, 0.0, 8.0, 2.0)), "ETHUSD")
_reg("ETH_IMP_TP25_24H", _impulse_h(2.0, 1.5, 0.0, 24.0, 2.5), "ETHUSD")
_reg("ETH_WE_IMP", _we(_impulse_h(2.0, 1.5, 0.0, 24.0, 2.0)), "ETHUSD")
_reg("ETH_FRI_SUN_IMP", _fri_sun(_impulse_h(2.0, 1.5, 0.0, 24.0, 2.0)), "ETHUSD")
_reg("ETH_VOLSPIKE_24H", _volspike_h(2.0, 1.5, 0.0, 24.0), "ETHUSD")
_reg("ETH_VOLSPIKE_ASIA", _volspike_h(2.0, 1.5, 0.0, 8.0), "ETHUSD")

_reg("ETH_H4_PB_EMA_24H", _h4_pb_ema_h(0.0, 24.0), "ETHUSD")
_reg("ETH_H4_PB_EMA_ASIA", _h4_pb_ema_h(0.0, 8.0), "ETHUSD")
_reg("ETH_H4_PB_EMA_OVN", _h4_pb_ema_h(18.0, 8.0), "ETHUSD")
_reg("ETH_H4_PB_EMA_NIGHT", _h4_pb_ema_h(20.0, 10.0), "ETHUSD")
_reg("ETH_H4_PB_EMA_1018", _h4_pb_ema_h(10.0, 18.0), "ETHUSD")

_reg("ETH_OUTSIDE_24H", _outside_h(1.3, 0.0, 24.0), "ETHUSD")
_reg("ETH_OUT_ASIA", _outside_h(1.3, 0.0, 8.0), "ETHUSD")
_reg("ETH_DBL_INS_24H", _dbl_inside_h(1.3, 0.0, 24.0), "ETHUSD")
_reg("ETH_ENGULF_24H", _engulf_h(1.3, 0.0, 24.0), "ETHUSD")
_reg("ETH_SQZ_24H", _sqz_break_h(1.4, 0.0, 24.0), "ETHUSD")
_reg("ETH_EMA_RECL_24H", _ema_reclaim_h(0.0, 24.0, 1.2), "ETHUSD")
_reg("ETH_EMA_RECL_OVN", _ema_reclaim_h(18.0, 8.0, 1.2), "ETHUSD")


def _sim(df, sym, fn, name, tf=15):
    from backtest_setups_novos_v2 import ONE_PER_DAY as _v2_opd
    added = name in ONE_PER_DAY and name not in _v2_opd
    if added:
        _v2_opd.add(name)
    try:
        return _simulate(df, sym, fn, name, tf=tf)
    finally:
        if added:
            _v2_opd.discard(name)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bars", type=int, default=300000)
    ap.add_argument("--syms", default="BTCUSD,ETHUSD")
    args = ap.parse_args()

    want = {s.strip().upper() for s in args.syms.split(",") if s.strip()}
    os.environ["KIMI_GROUP"] = "crypto"
    os.makedirs("logs", exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    log_path = f"logs/backtest_setups_novos_v14_btc_eth_{stamp}.txt"
    logf = open(log_path, "w", encoding="utf-8")

    def out(s):
        try:
            print(s)
        except UnicodeEncodeError:
            print(s.encode("ascii", "replace").decode("ascii"))
        logf.write(s + "\n")
        logf.flush()

    strat_map = {n: fn for n, fn in STRATS.items() if STRAT_SYM[n] in want}
    out(f"{'#' * 74}\n# SETUPS NOVOS V14 — BTC/ETH FDS·NOITE·ASIA·REFINE "
        f"({len(strat_map)} setups) · {datetime.now():%d/%m %H:%M}\n"
        f"# Ativos: {sorted(want)}\n"
        f"# SKIP: ORDER_FLOW BTC · GOs diurnos 08–18 já live\n"
        f"# NÃO altera live até GO · régua rigorosa\n{'#' * 74}")

    from backtest_crypto_pro import mt5_connect
    mt5 = mt5_connect()

    needs = sorted({STRAT_SYM[n] for n in strat_map})
    raw_dfs = {}
    for sym in needs:
        t0 = time.time()
        raw = _load_ohlc(mt5, sym, 15, args.bars, out)
        if raw is None or len(raw) < 2000:
            out(f"\n!! {sym} M15: sem histórico")
            continue
        raw_dfs[sym] = add_indicators(raw)
        anos = len(raw_dfs[sym]) / 96 / 365
        out(f"\n{'=' * 74}\n  {sym} M15: {len(raw_dfs[sym])} candles (~{anos:.1f}a)  "
            f"[{raw_dfs[sym].index[0].date()} -> {raw_dfs[sym].index[-1].date()}] "
            f"({time.time() - t0:.0f}s)\n{'=' * 74}")

    dfs = {sym: add_extra_v2(df.copy(), sym, ctx=None) for sym, df in raw_dfs.items()}
    ranking = []
    for name, fn in strat_map.items():
        sym = STRAT_SYM[name]
        if sym not in dfs:
            out(f"\n  ▸ {name} ({sym}): sem dados")
            continue
        df = dfs[sym]
        t0 = time.time()
        try:
            t = _sim(df, sym, fn, name, tf=15)
        except Exception as exc:
            out(f"\n  ▸ {name}  ERRO: {exc}")
            continue
        s = stats(t)
        cons = consistency(t)
        out(f"\n  ▸ {name}  ({sym} M15)   [{time.time() - t0:.0f}s]")
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
                out(f"      -> OUT-OF-SAMPLE {'SEGUROU OK' if seg else 'CAIU'} "
                    f"(net {s_out['net']:+.3f}, PF {s_out['PF']})")
            t.to_csv(f"logs/novos_v14_{sym}_M15_{name}.csv", index=False)
        v = verdict(s, cons, s_out)
        out(f"      VEREDITO: {v}")
        ranking.append({"setup": name, "sym": sym, "tf": 15, **s,
                        "cons%": cons, "veredito": v, "oos_net": s_out.get("net")})

    out(f"\n{'#' * 74}\n  RANKING V14 BTC/ETH FDS\n{'#' * 74}")
    rk = pd.DataFrame(ranking)
    goes, yellows, reds = [], [], []
    if not rk.empty:
        rk = rk.sort_values("net", ascending=False)
        for _, r in rk.iterrows():
            if r.get("n", 0) == 0:
                out(f"  {r.setup:<28} sem trades")
                continue
            out(f"  {r.setup:<28} n={int(r.n):<5} net {r.net:+.3f} R  "
                f"PF {r.PF:<5} cons {r['cons%']}%  OOS {r.oos_net}  {r.veredito}")
            vs = str(r.veredito)
            if "🟢" in vs and "GO" in vs:
                goes.append(r.setup)
            elif "🟡" in vs:
                yellows.append(r.setup)
            elif "🔴" in vs or "REPROVADO" in vs or "AMOSTRA" in vs:
                reds.append(r.setup)
        rk.to_csv(f"logs/backtest_setups_novos_v14_btc_eth_ranking_{stamp}.csv",
                  index=False)

    out(f"\n# GO encontrados: {goes if goes else 'nenhum'}")
    out(f"# amarelos: {yellows if yellows else 'nenhum'}")
    out(f"# vermelhos/fracos: {len(reds)}")
    out(f"# FIM — {log_path}")
    logf.close()
    print(f"\n-> {log_path}")


if __name__ == "__main__":
    main()
