"""
backtest_setups_novos_v7_wdo.py — RODADA 7: foco WDO (só 1 GO ao vivo hoje).

Régua idêntica (custos, OOS 70/30, bimestre, GO). NÃO altera motores ao vivo.

Contexto: WDO_NR5_H4 é o único GO WDO. Meta = mais caminhos 🟢 no mini dólar,
espelhando famílias que passaram no WIN + variantes de sessão/vol.

Uso:
  python rodar_backtest_setups_novos_v7_wdo.py
  python backtest_setups_novos_v7_wdo.py --bars 300000
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
    stats, consistency, verdict, _line, _sig, OOS_FRAC,
)
from backtest_setups_novos_v2 import add_extra_v2, _simulate
from backtest_setups_novos_v3 import (
    make_ovn_pdc, make_ovn_atr, make_gap_fade, make_ib_break,
    s_win_nr7_break, s_win_inside_bar_break,
)
from backtest_setups_novos_v4 import s_win_nr7_trend, s_win_inside_vol15
from backtest_setups_novos_v5 import (
    _nr_break, _h4_ok, _gap_ok,
    s_nr5_break, s_outside_rev, s_hl_break,
    s_inside_h4, s_inside_am, s_inside_vol18_h4, s_nr7_h4_gap,
    s_vwap_pb_trend, s_pdh_break_vol, s_bb_squeeze_brk,
)
from backtest_setups_novos_v6 import (
    s_nr5_h4, s_nr5_h4_mt, s_nr5_am, s_nr5_vol14, s_nr4_h4, s_nr7_1014_h4,
    s_inside_1015_h4, s_inside_vol13_h4, s_inside_vol15_h4,
    s_outside_h4, s_outside_vol15, s_outside_h4_mt,
    s_hl_h4, s_hl_h4_mt, s_bb_h4, s_vwap_pb_tight, s_pdh_h4_vol12,
)

STRATS = {}
STRAT_SYM = {}
STRAT_TF = {}
ONE_PER_DAY = set()


def _reg(name, fn, sym="WDO", tf=15, one_per_day=False):
    STRATS[name] = fn
    STRAT_SYM[name] = sym
    if tf != 15:
        STRAT_TF[name] = tf
    if one_per_day:
        ONE_PER_DAY.add(name)


# ── helpers WDO-only ─────────────────────────────────────────────────────────

def s_nr7_h4_mt(df, i, sym):
    r = df.iloc[i]
    if int(r.dow) not in (0, 1, 2, 3):
        return None
    return s_win_nr7_trend(df, i, sym)


def s_inside_v18_h4(df, i, sym):
    return s_inside_vol18_h4(df, i, sym)


def s_inside_h4_mt(df, i, sym):
    r = df.iloc[i]
    if int(r.dow) not in (0, 1, 2, 3):
        return None
    return s_inside_h4(df, i, sym)


def s_inside_am_h4(df, i, sym):
    """INSIDE_AM + bias H4 (mais seletivo que AM puro)."""
    base = s_inside_am(df, i, sym)
    if not base:
        return None
    r = df.iloc[i]
    if not _h4_ok(r, base["dir"]):
        return None
    return base


def s_nr5_1015_h4(df, i, sym):
    base = _nr_break(df, i, n=5, vol_mult=1.3, hh0=10.0, hh1=15.0)
    if not base:
        return None
    r = df.iloc[i]
    if not _h4_ok(r, base["dir"]):
        return None
    return base


def s_nr7_vol14_h4(df, i, sym):
    base = _nr_break(df, i, n=7, vol_mult=1.4, hh0=10.0, hh1=15.0)
    if not base:
        return None
    r = df.iloc[i]
    if not _h4_ok(r, base["dir"]):
        return None
    return base


def s_nr6_h4(df, i, sym):
    base = _nr_break(df, i, n=6, vol_mult=1.3, hh0=10.0, hh1=14.5)
    if not base:
        return None
    r = df.iloc[i]
    if not _h4_ok(r, base["dir"]):
        return None
    return base


def s_hl_1015_h4(df, i, sym):
    """HL break janela 10–15 + H4."""
    if i < 6:
        return None
    r = df.iloc[i]
    if not (10.0 <= r.hh < 15.0):
        return None
    base = s_hl_break(df, i, sym)
    if not base:
        return None
    if not _h4_ok(r, base["dir"]):
        return None
    return base


def s_ib_brk_h4(df, i, sym):
    base = make_ib_break(1.3, 10, 12.0, False)(df, i, sym)
    if not base:
        return None
    r = df.iloc[i]
    if not _h4_ok(r, base["dir"]):
        return None
    return base


def s_vwap_pb_h4(df, i, sym):
    base = s_vwap_pb_trend(df, i, sym)
    if not base:
        return None
    r = df.iloc[i]
    if not _h4_ok(r, base["dir"]):
        return None
    return base


def s_pdh_vol13_h4(df, i, sym):
    base = s_pdh_break_vol(df, i, sym)
    if not base:
        return None
    r = df.iloc[i]
    if not _h4_ok(r, base["dir"]):
        return None
    return base


def s_bb_squeeze_h4(df, i, sym):
    base = s_bb_squeeze_brk(df, i, sym)
    if not base:
        return None
    r = df.iloc[i]
    if not _h4_ok(r, base["dir"]):
        return None
    return base


def s_gap_fade_wdo(df, i, sym):
    return make_gap_fade(0.28, 1.0, 9.5, 1.5, 2.5)(df, i, sym)


def s_nr5_h4_gap(df, i, sym):
    base = s_nr5_h4(df, i, sym)
    if not base:
        return None
    r = df.iloc[i]
    if not _gap_ok(r, base["dir"], 0.08):
        return None
    return base


# ════════════════════════════════════════════════════════════════════════════
# REGISTRO — só WDO (baseline + espelhos WIN GO + variantes)
# ════════════════════════════════════════════════════════════════════════════

# Baseline (já ao vivo — controle)
_reg("WDO_NR5_H4", s_nr5_h4)  # já GO live

# Família NR (espelho WIN GOs + variantes)
_reg("WDO_NR5_H4_MT", s_nr5_h4_mt)
_reg("WDO_NR5_AM", s_nr5_am)
_reg("WDO_NR5_VOL14", s_nr5_vol14)
_reg("WDO_NR5_1015_H4", s_nr5_1015_h4)
_reg("WDO_NR5_H4_GAP", s_nr5_h4_gap)
_reg("WDO_NR4_H4", s_nr4_h4)
_reg("WDO_NR6_H4", s_nr6_h4)
_reg("WDO_NR7_BREAK", s_win_nr7_break)
_reg("WDO_NR7_TREND_H4", s_win_nr7_trend)
_reg("WDO_NR7_H4_MT", s_nr7_h4_mt)
_reg("WDO_NR7_1014_H4", s_nr7_1014_h4)
_reg("WDO_NR7_VOL14_H4", s_nr7_vol14_h4)
_reg("WDO_NR7_H4_GAP", s_nr7_h4_gap)

# Família INSIDE
_reg("WDO_INSIDE_BAR_BRK", s_win_inside_bar_break)
_reg("WDO_INSIDE_H4", s_inside_h4)
_reg("WDO_INSIDE_H4_MT", s_inside_h4_mt)
_reg("WDO_INSIDE_AM", s_inside_am)
_reg("WDO_INSIDE_AM_H4", s_inside_am_h4)
_reg("WDO_INSIDE_V13_H4", s_inside_vol13_h4)
_reg("WDO_INSIDE_VOL15_H4", s_inside_vol15_h4)
_reg("WDO_INSIDE_V18_H4", s_inside_v18_h4)
_reg("WDO_INSIDE_1015_H4", s_inside_1015_h4)

# HL / OUTSIDE / compressão
_reg("WDO_HL_H4", s_hl_h4)
_reg("WDO_HL_H4_MT", s_hl_h4_mt)
_reg("WDO_HL_1015_H4", s_hl_1015_h4)
_reg("WDO_OUTSIDE_H4", s_outside_h4)
_reg("WDO_OUTSIDE_VOL15", s_outside_vol15)
_reg("WDO_OUTSIDE_H4_MT", s_outside_h4_mt)
_reg("WDO_BB_H4", s_bb_h4)
_reg("WDO_BB_SQUEEZE_H4", s_bb_squeeze_h4)

# Sessão / níveis
_reg("WDO_OVN_PDC_034", make_ovn_pdc(0.34, 9.25, 9.85, True, 4), one_per_day=True)
_reg("WDO_OVN_PDC_030_MT", make_ovn_pdc(0.30, 9.25, 9.85, True, 3), one_per_day=True)
_reg("WDO_OVN_ATR_12", make_ovn_atr(1.2, 1.25, 9.25, 9.85, 3, True), one_per_day=True)
_reg("WDO_GAP_FADE", s_gap_fade_wdo, one_per_day=True)
_reg("WDO_IB_BRK_H4", s_ib_brk_h4, one_per_day=True)
_reg("WDO_IB_BRK_14", make_ib_break(1.4, 10, 12.0, True), one_per_day=True)
_reg("WDO_VWAP_PB_H4", s_vwap_pb_h4)
_reg("WDO_VWAP_PB_TIGHT", s_vwap_pb_tight)
_reg("WDO_PDH_H4", s_pdh_h4_vol12)
_reg("WDO_PDH_VOL13_H4", s_pdh_vol13_h4)


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
    ap.add_argument("--bars", type=int, default=300000)
    args = ap.parse_args()

    os.environ["KIMI_GROUP"] = "b3"
    os.makedirs("logs", exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    logf = open(f"logs/backtest_setups_novos_v7_wdo_{stamp}.txt", "w", encoding="utf-8")

    def out(s):
        try:
            print(s)
        except UnicodeEncodeError:
            print(s.encode("ascii", "replace").decode("ascii"))
        logf.write(s + "\n")
        logf.flush()

    out(f"{'#' * 74}\n# SETUPS NOVOS V7 — WDO ONLY ({len(STRATS)} setups) · "
        f"{datetime.now():%d/%m %H:%M}\n# NÃO altera motores · GO rigoroso\n{'#' * 74}")

    t0 = time.time()
    raw = _b3_fetch("WDO$D", 15, args.bars)
    if raw is None or len(raw) < 3000:
        out("!! WDO M15: sem histórico — abra MT5 XP com WDO$D")
        logf.close()
        return
    df = add_extra_v2(add_indicators(raw).copy(), "WDO$D", ctx=None)
    anos = len(df) / 26 / 252
    out(f"\n{'=' * 74}\n  WDO M15: {len(df)} candles (~{anos:.1f}a)  "
        f"[{df.index[0].date()} -> {df.index[-1].date()}] ({time.time() - t0:.0f}s)\n{'=' * 74}")

    ranking = []
    for name, fn in STRATS.items():
        t0 = time.time()
        t = _sim(df, "WDO$D", fn, name, tf=15)
        s = stats(t)
        cons = consistency(t)
        out(f"\n  ▸ {name}  (WDO M15)   [{time.time() - t0:.0f}s]")
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
            t.to_csv(f"logs/novos_v7_WDO_M15_{name}.csv", index=False)
        v = verdict(s, cons, s_out)
        out(f"      VEREDITO: {v}")
        ranking.append({"setup": name, "sym": "WDO", "tf": 15, **s, "cons%": cons, "veredito": v})

    out(f"\n{'#' * 74}\n  RANKING WDO\n{'#' * 74}")
    rk = pd.DataFrame(ranking)
    goes, yellows = [], []
    if not rk.empty:
        rk = rk.sort_values("net", ascending=False)
        for _, r in rk.iterrows():
            if r.get("n", 0) == 0:
                out(f"  WDO M15 {r.setup:<24} sem trades")
                continue
            out(f"  WDO M15 {r.setup:<24} n={int(r.n):<5} net {r.net:+.3f} R  "
                f"PF {r.PF:<5} cons {r['cons%']}%  {r.veredito}")
            vs = str(r.veredito)
            if "🟢" in vs and "GO" in vs:
                goes.append(r.setup)
            elif "🟡" in vs:
                yellows.append(r.setup)
        rk.to_csv(f"logs/backtest_setups_novos_v7_wdo_ranking_{stamp}.csv", index=False)
    out(f"\n# 🟢 GO encontrados: {goes if goes else 'nenhum'}")
    out(f"# 🟡 amarelos: {yellows if yellows else 'nenhum'}")
    out(f"# Critério: net≥+0,10 · PF≥1,25 · cons≥55% · OOS segura · n≥40")
    out(f"# FIM — logs/backtest_setups_novos_v7_wdo_{stamp}.txt")
    logf.close()


if __name__ == "__main__":
    main()
