"""Análise MFE/TP setups WinGo B3 + BT rápido WDO_HL_1014 (RR variants)."""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

from backtest_kimi_real import fetch, add_indicators
from backtest_setups_novos import stats, consistency, verdict, _sig, OOS_FRAC
from backtest_setups_novos_v2 import add_extra_v2, _simulate
from backtest_setups_novos_v13_btc_24h import _hl_h, _h4_ok  # noqa: F401
from services.b3_trade_hub import unified_trades
from services.win_go_setups import wdo_hl_1014_signal


def _f(x):
    try:
        if x in ("", None):
            return None
        return float(x)
    except Exception:
        return None


def live_table():
    rows = unified_trades(limit=500, rng="all")
    focus = []
    for r in rows:
        setup = r.get("setup") or ""
        if not any(k in setup for k in (
            "WDO", "WIND", "WIN_PDH", "WIN_IMP", "INSIDE", "NR", "HL_H4", "OUT_"
        )):
            continue
        entry, sl, tp, exit_px = _f(r.get("entry")), _f(r.get("stop")), _f(r.get("tp")), _f(r.get("exit"))
        if entry is None or sl is None:
            continue
        risk = abs(entry - sl)
        tp_dist = abs(tp - entry) if tp is not None else None
        rr = (tp_dist / risk) if risk > 0 and tp_dist is not None else None
        focus.append({
            "setup": setup,
            "symbol": r.get("symbol"),
            "dir": r.get("dir"),
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "exit": exit_px,
            "evento": r.get("evento"),
            "sl_pts": round(risk, 1),
            "tp_pts": round(tp_dist, 1) if tp_dist is not None else None,
            "rr": round(rr, 2) if rr is not None else None,
            "r_mult": _f(r.get("r_multiple")),
            "pnl": _f(r.get("profit_brl")),
            "ts": r.get("ts") or r.get("opened_at") or "",
        })
    return pd.DataFrame(focus)


def mfe_from_ohlc(df: pd.DataFrame, trades: pd.DataFrame) -> pd.DataFrame:
    """Estima MFE/MAE entre entrada e saída usando High/Low M15."""
    out = []
    for _, t in trades.iterrows():
        if t["entry"] is None or t["sl_pts"] is None or t["sl_pts"] <= 0:
            continue
        # janela: dia do trade ± 1d (sem ts preciso na saída unificada)
        # usa proximidade de preço de entrada no índice
        idx = df.index
        # busca barra com Close ~ entry (± tick)
        tol = 0.6 if str(t["symbol"]).upper().startswith("WDO") else 30.0
        candidates = df.index[(df.Close - t["entry"]).abs() <= tol]
        if len(candidates) == 0:
            out.append({**t.to_dict(), "mfe_r": None, "mae_r": None,
                        "hit_05": None, "hit_075": None, "hit_10": None, "hit_tp": None,
                        "dur_bars": None})
            continue
        # pega candidato mais próximo ao horário se ts disponível
        entry_i = df.index.get_loc(candidates[-1])
        if isinstance(entry_i, slice):
            entry_i = entry_i.start
        elif isinstance(entry_i, np.ndarray):
            entry_i = int(entry_i[-1])
        buy = str(t["dir"]).upper().startswith("COMP")
        risk = float(t["sl_pts"])
        tp_pts = float(t["tp_pts"] or risk * 1.5)
        # simula até SL/TP/EOD (~32 barras = 8h)
        mfe = 0.0
        mae = 0.0
        hit_05 = hit_075 = hit_10 = hit_tp = False
        end_i = min(entry_i + 40, len(df) - 1)
        exit_i = end_i
        for j in range(entry_i + 1, end_i + 1):
            r = df.iloc[j]
            if buy:
                fav = float(r.High) - float(t["entry"])
                adv = float(t["entry"]) - float(r.Low)
            else:
                fav = float(t["entry"]) - float(r.Low)
                adv = float(r.High) - float(t["entry"])
            mfe = max(mfe, fav)
            mae = max(mae, adv)
            if mfe >= 0.5 * risk:
                hit_05 = True
            if mfe >= 0.75 * risk:
                hit_075 = True
            if mfe >= 1.0 * risk:
                hit_10 = True
            if mfe >= tp_pts * 0.98:
                hit_tp = True
            # stop
            if adv >= risk * 0.98:
                exit_i = j
                break
            if hit_tp:
                exit_i = j
                break
        out.append({
            **t.to_dict(),
            "mfe_pts": round(mfe, 1),
            "mae_pts": round(mae, 1),
            "mfe_r": round(mfe / risk, 2),
            "mae_r": round(mae / risk, 2),
            "hit_05": hit_05,
            "hit_075": hit_075,
            "hit_10": hit_10,
            "hit_tp": hit_tp,
            "dur_bars": int(exit_i - entry_i),
        })
    return pd.DataFrame(out)


def atr_session_stats(df: pd.DataFrame, asset="WDO"):
    d = df.copy()
    d["hh"] = d.index.hour + d.index.minute / 60.0
    sess = d[(d.hh >= 10) & (d.hh < 14)]
    atr = sess["atr"].dropna()
    return {
        "atr_med_1014": float(atr.median()) if len(atr) else None,
        "atr_p25": float(atr.quantile(0.25)) if len(atr) else None,
        "atr_p75": float(atr.quantile(0.75)) if len(atr) else None,
        "n_bars": int(len(sess)),
    }


def bt_hl_variants(df):
    """Compara exits: BT original (partial 1.5/2.0), live-like single RR, caps."""
    results = []

    def make_hl(rr1, rr2=None, partial=False, tp_cap=None):
        base = _hl_h(1.3, 10.0, 14.0)

        def fn(df_, i, sym):
            sig = base(df_, i, sym)
            if not sig:
                return None
            entry = float(df_.iloc[i].Close)
            sl = float(sig["sl"])
            risk = abs(entry - sl)
            if risk <= 0:
                return None
            # reconstrói com RR desejado
            if tp_cap is not None:
                tp_dist = min(rr1 * risk, tp_cap)
                # TP fixo em pts → rr efetivo = tp_dist/risk; sem runner
                return _sig(sig["dir"], entry, sl, tp_dist / risk, tp_dist / risk, False)
            if partial and rr2 is not None:
                return _sig(sig["dir"], entry, sl, rr1, rr2, True)
            return _sig(sig["dir"], entry, sl, rr1, rr1, False)
        return fn

    variants = [
        ("BT_partial_1.5_2.0", make_hl(1.5, 2.0, True)),
        ("live_single_1.5", make_hl(1.5, partial=False)),
        ("single_1.2", make_hl(1.2, partial=False)),
        ("single_1.0", make_hl(1.0, partial=False)),
        ("cap_tp_20pts_rr1.5", make_hl(1.5, partial=False, tp_cap=20.0)),
        ("cap_tp_15pts_rr1.5", make_hl(1.5, partial=False, tp_cap=15.0)),
        ("single_1.2_cap20", make_hl(1.2, partial=False, tp_cap=20.0)),
    ]

    work = add_extra_v2(df.copy(), "WDO$D")
    for name, fn in variants:
        tr = _simulate(work, "WDO$D", fn, name, tf=15)
        if tr is None or tr.empty:
            results.append({"variant": name, "n": 0})
            continue
        st = stats(tr)
        cons = consistency(tr)
        cut = int(len(tr) * (1 - OOS_FRAC))
        oos = stats(tr.iloc[cut:]) if cut < len(tr) else {}
        results.append({
            "variant": name,
            "n": int(st.get("n", len(tr))),
            "net_R": round(float(st.get("net", 0) or 0), 3),
            "PF": round(float(st.get("PF", 0) or 0), 2),
            "win": round(float(st.get("win", 0) or 0), 1),
            "cons": round(float(cons or 0), 1),
            "oos_net": round(float(oos.get("net", 0) or 0), 3),
            "verdict": verdict(st, cons, oos),
        })
    return pd.DataFrame(results)


def main():
    print("=" * 72)
    print("LIVE SL/TP distances")
    print("=" * 72)
    live = live_table()
    if live.empty:
        print("sem trades live")
    else:
        g = live.groupby("setup").agg(
            n=("setup", "count"),
            sl_med=("sl_pts", "median"),
            tp_med=("tp_pts", "median"),
            rr_med=("rr", "median"),
            tp_hit=("evento", lambda s: round(100 * (s == "TP").mean(), 1)),
            sl_hit=("evento", lambda s: round(100 * (s == "SL").mean(), 1)),
            eod=("evento", lambda s: round(100 * s.isin(["EOD_CLOSE", "SAIDA"]).mean(), 1)),
            r_med=("r_mult", "median"),
        ).reset_index()
        print(g.to_string(index=False))
        print("\n--- WDO_HL_1014 detalhe ---")
        w = live[live.setup == "WDO_HL_1014"].copy()
        print(w[["ts", "dir", "entry", "sl_pts", "tp_pts", "rr", "evento", "r_mult", "pnl"]].to_string(index=False))

    print("\n" + "=" * 72)
    print("Fetch WDO$D M15 (MT5) para ATR + MFE + BT")
    print("=" * 72)
    os.environ["KIMI_GROUP"] = "b3"
    raw = fetch("WDO$D", 15, 80000)
    if raw is None or len(raw) < 2000:
        print("!! sem OHLC WDO — abort BT/MFE")
        return
    print(f"bars={len(raw)}  {raw.index[0]} -> {raw.index[-1]}")
    atr = atr_session_stats(raw)
    print(f"ATR M15 sessao 10-14h: med={atr['atr_med_1014']:.2f}  "
          f"p25={atr['atr_p25']:.2f}  p75={atr['atr_p75']:.2f}")

    if not live.empty:
        w = live[live.setup == "WDO_HL_1014"].copy()
        if not w.empty:
            # filtra OHLC recente (últimos 10 dias) para MFE
            recent = raw[raw.index >= (raw.index[-1] - pd.Timedelta(days=14))]
            mfe = mfe_from_ohlc(recent if len(recent) > 50 else raw, w)
            print("\n--- MFE WDO_HL_1014 (aprox M15) ---")
            cols = ["dir", "sl_pts", "tp_pts", "evento", "mfe_pts", "mfe_r", "mae_r",
                    "hit_05", "hit_075", "hit_10", "hit_tp", "dur_bars"]
            print(mfe[[c for c in cols if c in mfe.columns]].to_string(index=False))
            if mfe["mfe_r"].notna().any():
                print(f"\nMFE mediano={mfe.mfe_r.median():.2f}R  "
                      f"hit0.5R={100*mfe.hit_05.mean():.0f}%  "
                      f"hit0.75R={100*mfe.hit_075.mean():.0f}%  "
                      f"hit1.0R={100*mfe.hit_10.mean():.0f}%  "
                      f"hitTP={100*mfe.hit_tp.mean():.0f}%")

    print("\n" + "=" * 72)
    print("BT WDO_HL_1014 - variantes de saida")
    print("=" * 72)
    bt = bt_hl_variants(raw)
    print(bt.to_string(index=False))
    bt.to_csv("logs/_tp_rr_wdo_hl_1014_variants.csv", index=False)
    print("\nsalvo logs/_tp_rr_wdo_hl_1014_variants.csv")


if __name__ == "__main__":
    main()
