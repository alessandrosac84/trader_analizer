"""Análise MFE/TP setups XAU estruturais + BT variantes de cap TP (análogo WDO_HL).

Régua GO: net≥+0.10R · PF≥1.25 · cons≥55% · OOS · n≥40.
Live crypto = TP único (sem partial) — variantes usam partial=False.
"""
from __future__ import annotations

import os
import sys
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
from backtest_setups_novos_v5 import _h4_ok
from backtest_setups_novos_v13_btc_24h import _hl_h, _hh_ok, _nr_h, _impulse_h, _inside_h
from backtest_mega_sweep_gaps import _pdh_h


def atr_summary(df: pd.DataFrame) -> dict:
    atr = df["atr"].dropna()
    sess = atr  # XAU 24h
    return {
        "atr_med": float(atr.median()),
        "atr_p25": float(atr.quantile(0.25)),
        "atr_p75": float(atr.quantile(0.75)),
        "atr_mean": float(atr.mean()),
        "n_bars": int(len(sess)),
    }


def collect_signals(df, fn, name, max_n=5000):
    """Coleta SL/TP pts + MFE aproximado até hit SL/TP ou 96 barras."""
    rows = []
    n = len(df)
    start = 460
    for i in range(start, n):
        sig = fn(df, i, "XAUUSD")
        if not sig:
            continue
        entry = float(df.iloc[i].Close)
        sl = float(sig["sl"])
        risk = abs(entry - sl)
        if risk <= 0:
            continue
        tp1 = float(sig["tp1"])
        tp_pts = abs(tp1 - entry)
        buy = sig["dir"] == "COMPRA"
        atr_i = float(df.iloc[i].atr or 0)
        mfe = mae = 0.0
        hit_05 = hit_075 = hit_10 = hit_tp = False
        end = min(i + 96, n - 1)
        outcome = "TIME"
        for j in range(i + 1, end + 1):
            r = df.iloc[j]
            if buy:
                fav = float(r.High) - entry
                adv = entry - float(r.Low)
            else:
                fav = entry - float(r.Low)
                adv = float(r.High) - entry
            mfe = max(mfe, fav)
            mae = max(mae, adv)
            if mfe >= 0.5 * risk:
                hit_05 = True
            if mfe >= 0.75 * risk:
                hit_075 = True
            if mfe >= 1.0 * risk:
                hit_10 = True
            if mfe >= tp_pts - 1e-9:
                hit_tp = True
            if (buy and float(r.Low) <= sl) or ((not buy) and float(r.High) >= sl):
                outcome = "SL"
                break
            if (buy and float(r.High) >= tp1) or ((not buy) and float(r.Low) <= tp1):
                outcome = "TP"
                break
        rows.append({
            "setup": name,
            "sl_pts": risk,
            "tp_pts": tp_pts,
            "rr": tp_pts / risk if risk else None,
            "atr": atr_i,
            "sl_atr": risk / atr_i if atr_i > 0 else None,
            "tp_atr": tp_pts / atr_i if atr_i > 0 else None,
            "mfe_r": mfe / risk if risk else None,
            "mae_r": mae / risk if risk else None,
            "hit_05": hit_05,
            "hit_075": hit_075,
            "hit_10": hit_10,
            "hit_tp": hit_tp,
            "outcome": outcome,
        })
        if len(rows) >= max_n:
            break
    return pd.DataFrame(rows)


def make_exit_variant(base_fn, rr1=1.5, tp_cap=None):
    """Live-like: TP único (partial=False). Cap limita |entry−tp| em pts."""
    def fn(df_, i, sym):
        sig = base_fn(df_, i, sym)
        if not sig:
            return None
        entry = float(df_.iloc[i].Close)
        sl = float(sig["sl"])
        risk = abs(entry - sl)
        if risk <= 0:
            return None
        if tp_cap is not None:
            tp_dist = min(rr1 * risk, tp_cap)
            return _sig(sig["dir"], entry, sl, tp_dist / risk, tp_dist / risk, False)
        return _sig(sig["dir"], entry, sl, rr1, rr1, False)
    return fn


def bt_variants(df, label, base_fn, caps=(20, 25, 30, 40)):
    variants = [
        ("live_single_1.5", make_exit_variant(base_fn, 1.5)),
        ("single_1.2", make_exit_variant(base_fn, 1.2)),
        ("single_1.0", make_exit_variant(base_fn, 1.0)),
        ("BT_partial_1.5_2.0", base_fn),  # como BT original (partial)
    ]
    for c in caps:
        variants.append((f"cap_tp_{c}_rr1.5", make_exit_variant(base_fn, 1.5, tp_cap=float(c))))
    variants.append(("cap_tp_30_rr1.2", make_exit_variant(base_fn, 1.2, tp_cap=30.0)))

    work = df  # already add_extra_v2
    rows = []
    for name, fn in variants:
        tr = _simulate(work, "XAUUSD", fn, f"{label}_{name}", tf=15)
        if tr is None or tr.empty:
            rows.append({"setup": label, "variant": name, "n": 0})
            continue
        st = stats(tr)
        cons = consistency(tr)
        cut = int(len(tr) * (1 - OOS_FRAC))
        oos = stats(tr.iloc[cut:]) if cut < len(tr) else {}
        rows.append({
            "setup": label,
            "variant": name,
            "n": int(st.get("n", len(tr))),
            "net_R": round(float(st.get("net", 0) or 0), 3),
            "PF": round(float(st.get("PF", 0) or 0), 2),
            "win": round(float(st.get("win", 0) or 0), 1),
            "cons": round(float(cons or 0), 1),
            "oos_net": round(float(oos.get("net", 0) or 0), 3),
            "oos_PF": round(float(oos.get("PF", 0) or 0), 2),
            "verdict": verdict(st, cons, oos),
        })
    return pd.DataFrame(rows)


def main():
    os.environ.setdefault("KIMI_GROUP", "crypto")
    print("=" * 72)
    print("XAU M15 — ATR + MFE/TP + variantes cap")
    print("=" * 72)

    raw = fetch("XAUUSD", 15, bars=300000)
    if raw is None or len(raw) < 1000:
        # fallback gzip discovery
        p = Path("edge_discovery/db/XAUUSD_M15.csv.gz")
        if p.exists():
            raw = pd.read_csv(p, compression="gzip")
            if "time" in raw.columns:
                raw["dt"] = pd.to_datetime(raw["time"], unit="s")
                raw = raw.set_index("dt")
            raw = raw.rename(columns={c: c.capitalize() if c.islower() else c
                                      for c in raw.columns})
        else:
            print("ERRO: sem dados XAUUSD M15")
            return
    df = add_indicators(raw.copy())
    df = add_extra_v2(df, "XAUUSD")

    atr = atr_summary(df)
    print("\n--- ATR M15 XAU ---")
    for k, v in atr.items():
        print(f"  {k}: {v:.3f}" if isinstance(v, float) else f"  {k}: {v}")

    setups = {
        "XAU_HL_24H": _hl_h(1.3, 0.0, 24.0),
        "XAU_PDH_H4": _pdh_h(1.2, 10.0, 16.0),
        "XAU_PDH_1115": _pdh_h(1.2, 11.0, 15.0),
        "XAU_NR4_1016_H4": _nr_h(4, 1.3, 10.0, 16.0),
        "XAU_INS_AM": _inside_h(1.2, 9.0, 12.0),
        "XAU_IMP_CONT_20": _impulse_h(2.0, 1.5, 9.0, 18.0, 2.0),
    }

    # Live-like single 1.5 for MFE (IMP uses rr=2 in live)
    print("\n--- Distâncias SL/TP (sinal live-like) + hit rates MFE ---")
    mfe_rows = []
    for name, base in setups.items():
        rr = 2.0 if "IMP" in name else 1.5
        live_fn = make_exit_variant(base, rr)
        sigs = collect_signals(df, live_fn, name)
        if sigs.empty:
            print(f"  {name}: 0 sinais")
            continue
        mfe_rows.append({
            "setup": name,
            "n": len(sigs),
            "sl_med": round(sigs.sl_pts.median(), 1),
            "tp_med": round(sigs.tp_pts.median(), 1),
            "sl_p75": round(sigs.sl_pts.quantile(0.75), 1),
            "tp_p75": round(sigs.tp_pts.quantile(0.75), 1),
            "sl_atr_med": round(sigs.sl_atr.median(), 2),
            "tp_atr_med": round(sigs.tp_atr.median(), 2),
            "mfe_med_R": round(sigs.mfe_r.median(), 2),
            "hit_05%": round(100 * sigs.hit_05.mean(), 1),
            "hit_075%": round(100 * sigs.hit_075.mean(), 1),
            "hit_10%": round(100 * sigs.hit_10.mean(), 1),
            "hit_tp%": round(100 * sigs.hit_tp.mean(), 1),
            "tp_out%": round(100 * (sigs.outcome == "TP").mean(), 1),
        })
        print(f"  {name}: n={len(sigs)} sl_med={sigs.sl_pts.median():.1f} "
              f"tp_med={sigs.tp_pts.median():.1f} "
              f"hit05={100*sigs.hit_05.mean():.0f}% "
              f"hit075={100*sigs.hit_075.mean():.0f}% "
              f"hit1R={100*sigs.hit_10.mean():.0f}% "
              f"hitTP={100*sigs.hit_tp.mean():.0f}%")

    mfe_df = pd.DataFrame(mfe_rows)
    out_dir = Path("logs")
    out_dir.mkdir(exist_ok=True)
    mfe_df.to_csv(out_dir / "xau_tp_mfe_summary.csv", index=False)

    print("\n--- BT variantes (live single + caps) ---")
    all_bt = []
    # foco principal: HL (trade live) + PDH/NR (mesmo padrão estrutural)
    focus = ["XAU_HL_24H", "XAU_PDH_H4", "XAU_PDH_1115", "XAU_NR4_1016_H4",
             "XAU_INS_AM", "XAU_IMP_CONT_20"]
    for name in focus:
        print(f"\n## {name}")
        bt = bt_variants(df, name, setups[name])
        all_bt.append(bt)
        # print compact
        show = bt[["variant", "n", "net_R", "PF", "cons", "oos_net", "verdict"]].copy()
        show["verdict"] = show["verdict"].astype(str).str.replace(
            r"[^\x00-\x7F]+", "", regex=True).str.strip()
        print(show.to_string(index=False))
        sys.stdout.flush()

    bt_df = pd.concat(all_bt, ignore_index=True)
    bt_df.to_csv(out_dir / "xau_tp_cap_variants.csv", index=False)
    print(f"\nSalvo: logs/xau_tp_mfe_summary.csv + logs/xau_tp_cap_variants.csv")


if __name__ == "__main__":
    main()
