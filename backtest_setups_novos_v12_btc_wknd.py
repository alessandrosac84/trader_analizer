"""
backtest_setups_novos_v12_btc_wknd.py — MEGA BTC-only (fim de semana / 24h).

Máximo de hipóteses novas na família que já deu GO (inside+vol+H4, NR+H4),
mais pushes dos 🟡 v10/v11. NÃO altera live até 🟢.

Já live (não retestar como "novo"): INS_1015, INS_1015_V13, INS_0918_V13, NR5_1016.
Já GO v11 pendente de wire: BTC_INS_1017_V13 (incluído como confirmação).

Uso:
  python backtest_setups_novos_v12_btc_wknd.py
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

from backtest_kimi_real import add_indicators
from backtest_setups_novos import stats, consistency, verdict, _line, OOS_FRAC, _sig
from backtest_setups_novos_v2 import add_extra_v2, _simulate
from backtest_setups_novos_v5 import _nr_break, _h4_ok, _gap_ok
from backtest_setups_novos_v9_refine import _mt, _inside, _gap
from backtest_setups_novos_v6 import s_pdh_h4_vol12, s_outside_h4, s_hl_h4
from backtest_setups_novos_v11_impulse import _impulse_cont, _engulf_after_impulse, _nr_h4

STRATS = {}
STRAT_SYM = {}
ONE_PER_DAY = set()


def _reg(name, fn, one_per_day=False):
    STRATS[name] = fn
    STRAT_SYM[name] = "BTCUSD"
    if one_per_day:
        ONE_PER_DAY.add(name)


def _dbl_inside(vol=1.3, hh0=10.0, hh1=16.0):
    """Dois insides seguidos + rompimento + H4."""
    def _f(df, i, sym):
        if i < 4:
            return None
        r = df.iloc[i]
        a, b, c = df.iloc[i - 1], df.iloc[i - 2], df.iloc[i - 3]
        if not (hh0 <= float(r.hh) < hh1):
            return None
        # b inside c, a inside b
        if not (b.High <= c.High and b.Low >= c.Low):
            return None
        if not (a.High <= b.High and a.Low >= b.Low):
            return None
        if float(r.Volume or 0) < vol * float(r.vol8 or 0):
            return None
        if r.Close > a.High:
            base = _sig("COMPRA", r.Close, a.Low, 1.5, 2.5, True)
        elif r.Close < a.Low:
            base = _sig("VENDA", r.Close, a.High, 1.5, 2.5, True)
        else:
            return None
        if not base or not _h4_ok(r, base["dir"]):
            return None
        return base
    return _f


def _outside_vol(vol=1.3, hh0=10.0, hh1=16.0):
    def _f(df, i, sym):
        if i < 3:
            return None
        r, p = df.iloc[i], df.iloc[i - 1]
        if not (hh0 <= float(r.hh) < hh1):
            return None
        if not (float(r.High) > float(p.High) and float(r.Low) < float(p.Low)):
            return None
        if float(r.Volume or 0) < vol * float(r.vol8 or 0):
            return None
        if r.Close > r.Open and r.Close > p.High:
            d, sl = "COMPRA", float(r.Low)
        elif r.Close < r.Open and r.Close < p.Low:
            d, sl = "VENDA", float(r.High)
        else:
            return None
        if not _h4_ok(r, d):
            return None
        return _sig(d, float(r.Close), sl, 1.5, 2.0, True)
    return _f


def _hl_vol(vol=1.3, hh0=10.0, hh1=16.0):
    def _f(df, i, sym):
        if i < 4:
            return None
        r = df.iloc[i]
        if not (hh0 <= float(r.hh) < hh1):
            return None
        if float(r.Volume or 0) < vol * float(r.vol8 or 0):
            return None
        p1, p2 = df.iloc[i - 1], df.iloc[i - 2]
        # higher-low break up / lower-high break down
        if float(p1.Low) > float(p2.Low) and float(r.Close) > float(p1.High):
            d, sl = "COMPRA", float(p1.Low)
        elif float(p1.High) < float(p2.High) and float(r.Close) < float(p1.Low):
            d, sl = "VENDA", float(p1.High)
        else:
            return None
        if not _h4_ok(r, d):
            return None
        return _sig(d, float(r.Close), sl, 1.5, 2.0, True)
    return _f


def _pdh_vol(vol=1.35, hh0=10.0, hh1=16.0):
    def _f(df, i, sym):
        r = df.iloc[i]
        if not (hh0 <= float(r.hh) < hh1):
            return None
        if pd.isna(r.get("prev_day_hi")) or pd.isna(r.get("prev_day_lo")):
            return None
        if float(r.Volume or 0) < vol * float(r.vol8 or 0):
            return None
        p = df.iloc[i - 1]
        pdh, pdl = float(r.prev_day_hi), float(r.prev_day_lo)
        if float(p.Close) <= pdh < float(r.Close):
            d, sl = "COMPRA", pdl
        elif float(p.Close) >= pdl > float(r.Close):
            d, sl = "VENDA", pdh
        else:
            return None
        if not _h4_ok(r, d):
            return None
        risk = abs(float(r.Close) - sl)
        if risk <= 0:
            return None
        return _sig(d, float(r.Close), sl, 1.5, 2.0, True)
    return _f


def _wick_rej(vol=1.3, hh0=10.0, hh1=16.0):
    def _f(df, i, sym):
        r = df.iloc[i]
        if not (hh0 <= float(r.hh) < hh1):
            return None
        if float(r.Volume or 0) < vol * float(r.vol8 or 0):
            return None
        rng = max(float(r.High - r.Low), 1e-9)
        body = abs(float(r.Close - r.Open))
        uw = float(r.High - max(r.Close, r.Open))
        dw = float(min(r.Close, r.Open) - r.Low)
        if dw >= 2 * body and dw >= 0.55 * rng and r.Close > r.Open:
            d, sl = "COMPRA", float(r.Low)
        elif uw >= 2 * body and uw >= 0.55 * rng and r.Close < r.Open:
            d, sl = "VENDA", float(r.High)
        else:
            return None
        if not _h4_ok(r, d):
            return None
        return _sig(d, float(r.Close), sl, 1.5, 1.75, True)
    return _f


def _asia_comp(max_asia=0.38, hh0=10.0, hh1=14.0):
    """Range Ásia estreito → rompimento na manhã (proxy: range 00–08 vs ATR)."""
    def _f(df, i, sym):
        r = df.iloc[i]
        if not (hh0 <= float(r.hh) < hh1):
            return None
        day = r.day
        asia = df[(df.day == day) & (df.hh >= 0) & (df.hh < 8)]
        if len(asia) < 8:
            return None
        ar = float(asia.High.max() - asia.Low.min())
        atr = float(r.atr or 0)
        if atr <= 0 or ar > max_asia * atr * 8:  # relative tight
            # use pct of price
            mid = float(asia.Close.iloc[-1])
            if mid <= 0 or (ar / mid * 100) > max_asia:
                return None
        ah, al = float(asia.High.max()), float(asia.Low.min())
        p = df.iloc[i - 1]
        if float(p.Close) <= ah < float(r.Close) and float(r.Volume or 0) >= 1.2 * float(r.vol8 or 0):
            d, sl = "COMPRA", al
        elif float(p.Close) >= al > float(r.Close) and float(r.Volume or 0) >= 1.2 * float(r.vol8 or 0):
            d, sl = "VENDA", ah
        else:
            return None
        if not _h4_ok(r, d):
            return None
        return _sig(d, float(r.Close), sl, 1.5, 2.0, True)
    return _f


def _volspike_cont(vol=2.0, atr_mult=1.5, hh0=10.0, hh1=18.0):
    def _f(df, i, sym):
        if i < 2:
            return None
        r, p = df.iloc[i], df.iloc[i - 1]
        if not (hh0 <= float(r.hh) < hh1):
            return None
        if float(p.Volume or 0) < vol * float(p.vol8 or 0):
            return None
        atr = float(p.atr or 0)
        if atr <= 0 or float(p.High - p.Low) < atr_mult * atr:
            return None
        d = "COMPRA" if p.Close > p.Open else "VENDA"
        if d == "COMPRA" and float(r.Close) <= float(p.High):
            return None
        if d == "VENDA" and float(r.Close) >= float(p.Low):
            return None
        if not _h4_ok(r, d):
            return None
        sl = float(p.Low) if d == "COMPRA" else float(p.High)
        return _sig(d, float(r.Close), sl, 1.0, 2.0, True)
    return _f


def _us_open_imp(atr_mult=1.8, vol=1.4):
    return _impulse_cont(atr_mult, vol, 14.0, 17.0, True, 2.0)


def _h4_pb_ema():
    def _f(df, i, sym):
        if i < 5:
            return None
        r = df.iloc[i]
        if not (10.0 <= float(r.hh) < 18.0):
            return None
        # bias H4 via close vs ema50 H4 (coluna padrão add_extra)
        h4c, h4e = r.get("h4_close"), r.get("h4_ema50")
        e20, e50 = r.ema21, r.ema50
        if pd.isna(e20) or pd.isna(e50) or pd.isna(r.atr) or r.atr <= 0:
            return None
        p = df.iloc[i - 1]
        bull = float(e20) > float(e50)
        if not pd.isna(h4c) and not pd.isna(h4e):
            bull = float(h4c) > float(h4e)
        if bull:
            if float(p.Low) > float(e20) + 0.3 * float(r.atr):
                return None
            if not (float(r.Close) > float(e20) and float(r.Close) > float(r.Open)
                    and float(r.Close) > float(p.High)):
                return None
            d, sl = "COMPRA", min(float(p.Low), float(r.Low))
        else:
            if float(p.High) < float(e20) - 0.3 * float(r.atr):
                return None
            if not (float(r.Close) < float(e20) and float(r.Close) < float(r.Open)
                    and float(r.Close) < float(p.Low)):
                return None
            d, sl = "VENDA", max(float(p.High), float(r.High))
        if not _h4_ok(r, d):
            return None
        return _sig(d, float(r.Close), sl, 1.5, 2.0, True)
    return _f


# ═══ Confirmação GO pendente ═══
_reg("BTC_INS_1017_V13", _inside(1.3, 10.0, 17.0))  # já 🟢 v11 — reconfirma

# ═══ Inside variants ═══
_reg("BTC_INS_1016_V14", _inside(1.4, 10.0, 16.0))
_reg("BTC_INS_V135_0916", _inside(1.35, 9.0, 16.0))
_reg("BTC_INS_1117_V13", _inside(1.3, 11.0, 17.0))
_reg("BTC_INS_1015_MT", _mt(_inside(1.2, 10.0, 15.0)))
_reg("BTC_INS_V14_1015", _inside(1.4, 10.0, 15.0))
_reg("BTC_INS_V15_1016", _inside(1.5, 10.0, 16.0))
_reg("BTC_INS_0816_V13", _inside(1.3, 8.0, 16.0))
_reg("BTC_INS_1218_V13", _inside(1.3, 12.0, 18.0))
_reg("BTC_DBL_INS_V13", _dbl_inside(1.3, 10.0, 16.0))
_reg("BTC_INS_GAP_1016", _gap(_inside(1.3, 10.0, 16.0), 0.08))

# ═══ NR variants ═══
_reg("BTC_NR7_1016_V14", _nr_h4(7, 1.4, 10.0, 16.0))
_reg("BTC_NR5_1014_V13", _nr_h4(5, 1.3, 10.0, 14.0))
_reg("BTC_NR5_1116_V135", _nr_h4(5, 1.35, 11.0, 16.0))
_reg("BTC_NR5_LON_V135", _nr_h4(5, 1.35, 8.0, 12.0))
_reg("BTC_NR5_NY_V135", _nr_h4(5, 1.35, 13.0, 17.0))
_reg("BTC_NR4_1016_H4", _nr_h4(4, 1.3, 10.0, 16.0))
_reg("BTC_NR5_GAP_V13", _gap(_nr_h4(5, 1.3, 10.0, 16.0), 0.08))
_reg("BTC_NR7_1016_H4", _nr_h4(7, 1.3, 10.0, 16.0))  # 🟡 v11 push
_reg("BTC_NR5_0915_V13", _nr_h4(5, 1.3, 9.0, 15.0))

# ═══ PDH / HL / Outside / Wick ═══
_reg("BTC_PDH_1016_V135", _pdh_vol(1.35, 10.0, 16.0))
_reg("BTC_PDH_H4", s_pdh_h4_vol12)
_reg("BTC_HL_V13_1016", _hl_vol(1.3, 10.0, 16.0))
_reg("BTC_OUTSIDE_V13_1016", _outside_vol(1.3, 10.0, 16.0))
_reg("BTC_WICK_REJ_H4_V13", _wick_rej(1.3, 10.0, 16.0))

# ═══ Impulso / vol spike / US open ═══
_reg("BTC_IMP_CONT_24_V15", _impulse_cont(2.4, 1.5, 9.0, 18.0, True, 2.0))
_reg("BTC_IMP_CONT_22", _impulse_cont(2.2, 1.5, 10.0, 17.0, True, 2.0))
_reg("BTC_IMP_TP25", _impulse_cont(1.8, 1.4, 9.0, 18.0, True, 2.5))
_reg("BTC_IMP_FADE_V14", _engulf_after_impulse(1.6, 1.3, 9.0, 18.0, True))
_reg("BTC_US_OPEN_IMP_1417", _us_open_imp(1.8, 1.4))
_reg("BTC_VOLSPIKE_CONT_H4", _volspike_cont(2.0, 1.5, 10.0, 18.0))

# ═══ Asia / PB ═══
_reg("BTC_ASIA_COMP_038", _asia_comp(0.38, 10.0, 14.0), one_per_day=True)
_reg("BTC_H4_PB_EMA", _h4_pb_ema())


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
    args = ap.parse_args()

    os.environ["KIMI_GROUP"] = "crypto"
    os.makedirs("logs", exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    log_path = f"logs/backtest_setups_novos_v12_btc_wknd_{stamp}.txt"
    logf = open(log_path, "w", encoding="utf-8")

    def out(s):
        try:
            print(s)
        except UnicodeEncodeError:
            print(s.encode("ascii", "replace").decode("ascii"))
        logf.write(s + "\n")
        logf.flush()

    out(f"{'#' * 74}\n# SETUPS NOVOS V12 — BTC WEEKEND MEGA ({len(STRATS)} setups) · "
        f"{datetime.now():%d/%m %H:%M}\n# Ativo: BTCUSD M15 · GO rigoroso · NÃO altera live\n"
        f"{'#' * 74}")

    from backtest_crypto_pro import mt5_connect, fetch as _cp_fetch
    mt5 = mt5_connect()
    t0 = time.time()
    raw = _cp_fetch(mt5, "BTCUSD", 15, args.bars)
    if raw is None or len(raw) < 2000:
        out("!! BTCUSD sem histórico"); logf.close(); return
    df0 = add_indicators(raw)
    df = add_extra_v2(df0.copy(), "BTCUSD", ctx=None)
    anos = len(df) / 96 / 365
    out(f"\nBTCUSD M15: {len(df)} candles (~{anos:.1f}a) "
        f"[{df.index[0].date()} -> {df.index[-1].date()}] ({time.time()-t0:.0f}s)\n")

    ranking = []
    for name, fn in STRATS.items():
        t0 = time.time()
        try:
            t = _sim(df, "BTCUSD", fn, name, tf=15)
        except Exception as exc:
            out(f"\n  ▸ {name}  ERRO: {exc}")
            continue
        s = stats(t)
        cons = consistency(t)
        out(f"\n  ▸ {name}   [{time.time()-t0:.0f}s]")
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
            t.to_csv(f"logs/novos_v12_BTCUSD_M15_{name}.csv", index=False)
        v = verdict(s, cons, s_out)
        out(f"      VEREDITO: {v}")
        ranking.append({"setup": name, "sym": "BTCUSD", "tf": 15, **s,
                        "cons%": cons, "veredito": v, "oos_net": s_out.get("net")})

    out(f"\n{'#' * 74}\n  RANKING V12 BTC\n{'#' * 74}")
    rk = pd.DataFrame(ranking)
    goes, yellows = [], []
    if not rk.empty:
        rk = rk.sort_values("net", ascending=False)
        for _, r in rk.iterrows():
            if r.get("n", 0) == 0:
                out(f"  {r.setup:<26} sem trades")
                continue
            out(f"  {r.setup:<26} n={int(r.n):<5} net {r.net:+.3f} R  "
                f"PF {r.PF:<5} cons {r['cons%']}%  OOS {r.oos_net}  {r.veredito}")
            vs = str(r.veredito)
            if "🟢" in vs and "GO" in vs:
                goes.append(r.setup)
            elif "🟡" in vs:
                yellows.append(r.setup)
        rk.to_csv(f"logs/backtest_setups_novos_v12_btc_wknd_ranking_{stamp}.csv", index=False)

    out(f"\n# GO encontrados: {goes if goes else 'nenhum'}")
    out(f"# amarelos: {yellows if yellows else 'nenhum'}")
    out(f"# FIM — {log_path}")
    logf.close()
    print(f"\n→ {log_path}")


if __name__ == "__main__":
    main()
