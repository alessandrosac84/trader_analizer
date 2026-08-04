"""
backtest_acoes_b3_v2.py — V2 setups ESPECÍFICOS de ações B3 (não port WIN/crypto).

Hipóteses (plan v2):
  1) ACOES_GAP_CONT_H1  — H1 · gap a favor do viés D1 + continuação 1ª/2ª hora
  2) ACOES_PB_D1        — D1 · pullback em tendência (swing multi-dia)
  3) ACOES_ORB30_DIR    — M15 · ORB 30min só no lado do gap/viés D1 · sai ≤12:30

Universo: PETR4, VALE3, ITUB4, BBDC4, ABEV3, WEGE3, BBAS3
Régua GO: net ≥ +0,10 R · PF ≥ 1,25 · cons ≥ 55% · OOS 70/30 · n ≥ 40
         (também ranking cross-symbol por setup se n por paper < 40)

Uso (MT5 XP aberto, papers no Market Watch):
    python backtest_acoes_b3_v2.py
    python backtest_acoes_b3_v2.py --symbol PETR4
"""
from __future__ import annotations

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

SYMBOLS = ["PETR4", "VALE3", "ITUB4", "BBDC4", "ABEV3", "WEGE3", "BBAS3"]
OOS_FRAC = 0.30
TICK = 0.01
FEE_PCT_RT = 0.00055
GAP_MIN_PCT = 0.40          # gap mínimo p/ GAP_CONT / viés de ORB


# ── Custos ──────────────────────────────────────────────────────────────────

def stock_cost_pts(price, stopped=True, entry_limit=False):
    cost = price * FEE_PCT_RT
    if not entry_limit:
        cost += TICK
    if stopped:
        cost += TICK
    return cost


def _env(k, d=""):
    return os.getenv("MT5_" + k, d)


def fetch(symbol: str, tf_min: int, bars: int = 200000):
    """fetch com D1 (1440) — o kimi só mapeia até H4."""
    import MetaTrader5 as mt5
    if mt5.terminal_info() is None:
        kw = {}
        path = _env("PATH")
        if path and os.path.exists(path):
            kw["path"] = path
        login = int(_env("LOGIN", "0") or 0)
        pw, srv = _env("PASSWORD"), _env("SERVER")
        if login and pw and srv and srv.lower() not in ("metaquotes-demo", "metaquotes-demo2"):
            kw.update(login=login, password=pw, server=srv)
        if not mt5.initialize(**kw):
            print("MT5 não inicializou:", mt5.last_error())
            return None
    tf_map = {
        5: mt5.TIMEFRAME_M5, 15: mt5.TIMEFRAME_M15, 30: mt5.TIMEFRAME_M30,
        60: mt5.TIMEFRAME_H1, 240: mt5.TIMEFRAME_H4, 1440: mt5.TIMEFRAME_D1,
    }
    tf = tf_map.get(tf_min, mt5.TIMEFRAME_M15)
    if not mt5.symbol_select(symbol, True):
        print(f"  {symbol}: não disponível")
        return None
    rates = None
    for cnt in (bars, 100000, 50000, 20000, 5000, 2000):
        if cnt > bars:
            continue
        rates = mt5.copy_rates_from_pos(symbol, tf, 0, cnt)
        if rates is not None and len(rates) > 0:
            break
    if rates is None or len(rates) == 0:
        print(f"  {symbol} M{tf_min}: sem candles ({mt5.last_error()})")
        return None
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df = df.set_index("time")
    vol = df["real_volume"] if df["real_volume"].sum() > 0 else df["tick_volume"]
    out = pd.DataFrame(
        {"Open": df["open"], "High": df["high"], "Low": df["low"],
         "Close": df["close"], "Volume": vol},
        index=df.index,
    )
    return add_indicators(out)


# ── Features de sessão / bias D1 ────────────────────────────────────────────

def bars_per_day(tf):
    return {5: 84, 15: 28, 60: 7, 1440: 1}.get(tf, 28)


def attach_d1_bias(df, d1: pd.DataFrame) -> pd.DataFrame:
    """Viés D1 sem look-ahead: usa EMA20/50 do dia ANTERIOR."""
    d = d1.copy()
    bias = np.where(d["ema20"] > d["ema50"], 1, np.where(d["ema20"] < d["ema50"], -1, 0))
    ser = pd.Series(bias, index=d.index).shift(1)  # ontem → hoje
    # mapear por data do pregão
    by_day = ser.copy()
    by_day.index = pd.to_datetime(by_day.index).normalize()
    day_idx = pd.to_datetime(df.index).normalize()
    df = df.copy()
    df["d1_bias"] = day_idx.map(by_day.to_dict()).astype(float)
    return df


def add_session(df, tf_min: int) -> pd.DataFrame:
    df = df.copy()
    day = pd.Series(df.index.date, index=df.index)
    df["day"] = day.values
    df["hh"] = df.index.hour + df.index.minute / 60.0
    df["bar_n"] = df.groupby(day).cumcount()
    df["day_open"] = df.groupby(day)["Open"].transform("first")
    day_close = df.groupby(day)["Close"].last()
    df["prev_close"] = day.map(day_close.shift(1))
    df["gap_pct"] = (df["day_open"] - df["prev_close"]) / df["prev_close"].replace(0, np.nan) * 100
    n30 = max(1, int(30 / max(tf_min, 1))) if tf_min < 1440 else 1
    in_orb = df["bar_n"] < n30
    df["orb_hi"] = df.High.where(in_orb).groupby(day).transform("max")
    df["orb_lo"] = df.Low.where(in_orb).groupby(day).transform("min")
    df["n30"] = n30
    df["day_first_high"] = df.groupby(day)["High"].transform("first")
    df["day_first_low"] = df.groupby(day)["Low"].transform("first")
    return df


# ── Setups ──────────────────────────────────────────────────────────────────

def s_gap_cont_h1(df, i, sym):
    """Gap ≥ GAP_MIN_PCT a favor do D1 + continuação na 1ª/2ª H1."""
    r = df.iloc[i]
    bn = int(r.bar_n)
    if bn not in (0, 1):
        return None
    if pd.isna(r.gap_pct) or pd.isna(r.d1_bias) or r.d1_bias == 0 or r.atr <= 0:
        return None
    g = float(r.gap_pct)
    if abs(g) < GAP_MIN_PCT:
        return None
    # gap e bias alinhados
    if r.d1_bias > 0 and g < GAP_MIN_PCT:
        return None
    if r.d1_bias < 0 and g > -GAP_MIN_PCT:
        return None

    if r.d1_bias > 0:
        ok = False
        if bn == 0 and r.Close > r.Open and r.Close >= r.Low + 0.6 * max(r.High - r.Low, 1e-9):
            ok = True
        if bn == 1 and r.Close > float(r.day_first_high):
            ok = True
        if not ok:
            return None
        entry = float(r.Close)
        sl = min(float(r.day_open), float(r.day_first_low)) - 0.15 * float(r.atr)
        risk = entry - sl
        if risk <= 0:
            return None
        return {"dir": "COMPRA", "sl": sl, "tp1": entry + 2.0 * risk, "tp2": None,
                "partial": False, "eod_hh": 17.0}

    ok = False
    if bn == 0 and r.Close < r.Open and r.Close <= r.High - 0.6 * max(r.High - r.Low, 1e-9):
        ok = True
    if bn == 1 and r.Close < float(r.day_first_low):
        ok = True
    if not ok:
        return None
    entry = float(r.Close)
    sl = max(float(r.day_open), float(r.day_first_high)) + 0.15 * float(r.atr)
    risk = sl - entry
    if risk <= 0:
        return None
    return {"dir": "VENDA", "sl": sl, "tp1": entry - 2.0 * risk, "tp2": None,
            "partial": False, "eod_hh": 17.0}


def s_pb_d1(df, i, sym):
    """Tendência D1 + pullback na EMA20 + candle de retomada. Hold multi-dia."""
    if i < 60:
        return None
    r, p = df.iloc[i], df.iloc[i - 1]
    atr = float(r.atr or 0)
    if atr <= 0 or pd.isna(r.ema20) or pd.isna(r.ema50):
        return None
    # COMPRA
    if float(r.ema20) > float(r.ema50):
        near = float(p.Low) <= float(p.ema20) + 0.35 * atr
        was_pb = float(p.Close) <= float(p.ema20) + 0.5 * atr
        recover = (float(r.Close) > float(r.ema20) and float(r.Close) > float(r.Open)
                   and float(r.Close) > float(p.High))
        if near and was_pb and recover:
            entry = float(r.Close)
            sl = min(float(p.Low), float(r.Low)) - 0.2 * atr
            risk = entry - sl
            if risk < 0.3 * atr:
                return None
            return {"dir": "COMPRA", "sl": sl, "tp1": entry + 2.0 * risk, "tp2": None,
                    "partial": False, "hold_overnight": True}
    # VENDA
    if float(r.ema20) < float(r.ema50):
        near = float(p.High) >= float(p.ema20) - 0.35 * atr
        was_pb = float(p.Close) >= float(p.ema20) - 0.5 * atr
        recover = (float(r.Close) < float(r.ema20) and float(r.Close) < float(r.Open)
                   and float(r.Close) < float(p.Low))
        if near and was_pb and recover:
            entry = float(r.Close)
            sl = max(float(p.High), float(r.High)) + 0.2 * atr
            risk = sl - entry
            if risk < 0.3 * atr:
                return None
            return {"dir": "VENDA", "sl": sl, "tp1": entry - 2.0 * risk, "tp2": None,
                    "partial": False, "hold_overnight": True}
    return None


def s_orb30_dir(df, i, sym):
    """ORB 30min só no lado do gap/viés D1; entradas 10:00–11:00."""
    r = df.iloc[i]
    n30 = int(r.n30)
    if int(r.bar_n) < n30:
        return None
    if not (10.0 <= float(r.hh) < 11.0):
        return None
    if pd.isna(r.orb_hi) or pd.isna(r.orb_lo) or r.atr <= 0:
        return None
    # direção: gap alinhado OU (gap fraco + bias D1)
    g = float(r.gap_pct) if not pd.isna(r.gap_pct) else 0.0
    bias = float(r.d1_bias) if not pd.isna(r.d1_bias) else 0.0
    want = None
    if g >= GAP_MIN_PCT or (g > 0.15 and bias > 0) or (abs(g) < 0.15 and bias > 0):
        want = "COMPRA"
    elif g <= -GAP_MIN_PCT or (g < -0.15 and bias < 0) or (abs(g) < 0.15 and bias < 0):
        want = "VENDA"
    if want is None:
        return None
    # range mínimo
    if (float(r.orb_hi) - float(r.orb_lo)) < 0.5 * float(r.atr):
        return None
    p = df.iloc[i - 1]
    entry = float(r.Close)
    if want == "COMPRA":
        if not (float(p.Close) <= float(r.orb_hi) and entry > float(r.orb_hi)):
            return None
        sl = float(r.orb_lo)
        risk = entry - sl
        if risk <= 0:
            return None
        return {"dir": "COMPRA", "sl": sl, "tp1": entry + 1.5 * risk, "tp2": None,
                "partial": False, "eod_hh": 12.5}
    if not (float(p.Close) >= float(r.orb_lo) and entry < float(r.orb_lo)):
        return None
    sl = float(r.orb_hi)
    risk = sl - entry
    if risk <= 0:
        return None
    return {"dir": "VENDA", "sl": sl, "tp1": entry - 1.5 * risk, "tp2": None,
            "partial": False, "eod_hh": 12.5}


SETUPS = {
    # name -> (fn, tf_min, one_per_day)
    "ACOES_GAP_CONT_H1": (s_gap_cont_h1, 60, True),
    "ACOES_PB_D1":       (s_pb_d1, 1440, False),
    "ACOES_ORB30_DIR":   (s_orb30_dir, 15, True),
}


# ── Simulador ───────────────────────────────────────────────────────────────

def simulate(df, sym, fn, one_per_day):
    trades, pos = [], None
    last_day = None
    n = len(df)
    warmup = 60 if len(df) < 400 else 260
    if n <= warmup + 5:
        warmup = max(30, n // 5)
    for i in range(warmup, n):
        r = df.iloc[i]
        if pos:
            buy = pos["dir"] == "COMPRA"
            exit_R = None
            stopped = True
            if (r.Low <= pos["sl"]) if buy else (r.High >= pos["sl"]):
                exit_R = ((pos["sl"] - pos["entry"]) / pos["risk"]) if buy else (
                    (pos["entry"] - pos["sl"]) / pos["risk"])
            elif (r.High >= pos["tp1"]) if buy else (r.Low <= pos["tp1"]):
                exit_R = abs(pos["tp1"] - pos["entry"]) / pos["risk"]
                stopped = False
            else:
                # saída por horário (ORB / gap day-trade)
                eod_hh = pos.get("eod_hh")
                if eod_hh is not None and float(r.hh) >= float(eod_hh):
                    px = float(r.Close)
                    exit_R = ((px - pos["entry"]) / pos["risk"]) if buy else (
                        (pos["entry"] - px) / pos["risk"])
                    stopped = False
                # EOD clássico (não hold overnight)
                elif (not pos.get("hold_overnight")) and r["day"] != pos["day"]:
                    px = float(df.iloc[i - 1].Close)
                    exit_R = ((px - pos["entry"]) / pos["risk"]) if buy else (
                        (pos["entry"] - px) / pos["risk"])
                    stopped = False
            if exit_R is not None:
                cost = stock_cost_pts(pos["entry"], stopped=stopped) / pos["risk"]
                trades.append({
                    "ts": pos["ts"], "sym": sym, "dir": pos["dir"],
                    "gross_R": round(exit_R, 3),
                    "cost_R": round(cost, 3),
                    "net_R": round(exit_R - cost, 3),
                })
                pos = None
            continue

        if one_per_day and r["day"] == last_day:
            continue
        sig = fn(df, i, sym)
        if not sig:
            continue
        entry = float(r.Close)
        risk = abs(entry - float(sig["sl"]))
        atr_i = float(r.atr or 0)
        min_risk = 0.15 * max(atr_i, 1e-9) if atr_i > 0 else 6 * TICK
        if risk <= 0 or risk < min_risk or risk < 6 * TICK:
            continue
        pos = {
            "ts": df.index[i], "day": r["day"], "dir": sig["dir"], "entry": entry,
            "sl": float(sig["sl"]), "tp1": float(sig["tp1"]),
            "risk": risk,
            "hold_overnight": bool(sig.get("hold_overnight")),
            "eod_hh": sig.get("eod_hh"),
        }
        last_day = r["day"]
    return pd.DataFrame(trades)


def stats(t):
    if t is None or t.empty:
        return {"n": 0}
    w = t[t.net_R > 0]
    l = t[t.net_R <= 0]
    gw, gl = w.net_R.sum(), -l.net_R.sum()
    eq = t.net_R.cumsum()
    dd = (eq - eq.cummax()).min()
    return {
        "n": len(t), "win": round(len(w) / len(t) * 100, 1),
        "gross": round(t.gross_R.mean(), 3), "cost": round(t.cost_R.mean(), 3),
        "net": round(t.net_R.mean(), 3),
        "PF": round(gw / gl, 2) if gl > 0 else float("inf"),
        "tot": round(t.net_R.sum(), 1), "dd": round(dd, 1),
    }


def consistency(t):
    if t is None or t.empty:
        return 0
    t2 = t.copy()
    t2["p"] = pd.to_datetime(t2["ts"]).dt.to_period("2M").astype(str)
    per = t2.groupby("p")["net_R"].mean()
    return round((per > 0).sum() / len(per) * 100) if len(per) else 0


def oos_split(t):
    if t is None or t.empty:
        return {"n": 0}, {"n": 0}
    t = t.sort_values("ts").reset_index(drop=True)
    cut = int(len(t) * (1 - OOS_FRAC))
    return stats(t.iloc[:cut]), stats(t.iloc[cut:])


def verdict(s, cons, s_out):
    if s.get("n", 0) < 40:
        return "⚪ amostra fraca"
    oos_ok = s_out.get("n", 0) >= 15 and s_out.get("net", -9) >= 0.08 and s_out.get("PF", 0) >= 1.15
    if s["net"] >= 0.10 and s["PF"] >= 1.25 and cons >= 55 and oos_ok:
        return "🟢 GO"
    if s["net"] > 0.05 and s["PF"] >= 1.15:
        return "🟡 promissor" + ("" if oos_ok else " (OOS fraco)")
    return "🔴"


def _line(out, sym, setup, tf_label, s, cons, s_out, v, dt):
    if s.get("n", 0) == 0:
        out(f"    {setup:<22} (sem trades)  [{dt:.0f}s]")
        return
    oos = s_out.get("net") if s_out.get("n", 0) else "—"
    out(f"    {setup:<22} n={s['n']:<5} win {s['win']:>5}% net {s['net']:+.3f} R  "
        f"PF {s['PF']:<5} tot {s['tot']:+7.1f} DD {s['dd']:>7} cons {cons}%  "
        f"OOS {oos}  {v}  [{dt:.0f}s]")


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default=None)
    ap.add_argument("--bars", type=int, default=200000)
    args = ap.parse_args()

    os.makedirs("logs", exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    log_path = f"logs/backtest_acoes_v2_{stamp}.txt"
    logf = open(log_path, "w", encoding="utf-8")

    def out(s):
        print(s)
        logf.write(s + "\n")
        logf.flush()

    syms = [args.symbol.upper()] if args.symbol else SYMBOLS
    out(f"{'#' * 76}")
    out(f"# BACKTEST AÇÕES B3 V2 — setups específicos · {datetime.now():%d/%m/%Y %H:%M}")
    out(f"# Papers: {', '.join(syms)}")
    out(f"# Setups: {', '.join(SETUPS)}")
    out(f"# GO: net≥+0.10R · PF≥1.25 · cons≥55% · OOS · n≥40")
    out(f"{'#' * 76}")

    ranking = []
    pool = {name: [] for name in SETUPS}  # trades cross-symbol

    for sym in syms:
        out(f"\n\n{'█' * 76}\n█  {sym}\n{'█' * 76}")
        # D1 sempre (bias + setup PB)
        d1 = fetch(sym, 1440, min(args.bars, 3000))
        if d1 is None or len(d1) < 120:
            out(f"  D1: histórico insuficiente — pulando {sym}")
            continue
        d1 = add_session(d1, 1440)
        anos_d1 = len(d1) / 252
        out(f"  D1: {len(d1)} candles (~{anos_d1:.1f} anos) "
            f"[{d1.index[0].date()} → {d1.index[-1].date()}]")

        cache = {1440: d1}
        for name, (fn, tf, opd) in SETUPS.items():
            if tf not in cache:
                raw = fetch(sym, tf, args.bars if tf < 1440 else min(args.bars, 3000))
                if raw is None or len(raw) < 200:
                    out(f"  M{tf}: sem histórico — {name} pulado")
                    continue
                df = add_session(raw, tf)
                if tf in (15, 60):
                    df = attach_d1_bias(df, d1)
                cache[tf] = df
                anos = len(df) / bars_per_day(tf) / 252
                out(f"  M{tf}: {len(df)} candles (~{anos:.1f} anos) "
                    f"[{df.index[0].date()} → {df.index[-1].date()}]")

            df = cache.get(tf)
            if df is None:
                continue
            t0 = time.time()
            try:
                t = simulate(df, sym, fn, opd)
            except Exception as exc:
                out(f"    {name:<22} ERRO: {exc}")
                continue
            s = stats(t)
            cons = consistency(t)
            _, s_out = oos_split(t)
            v = verdict(s, cons, s_out)
            tf_label = "D1" if tf == 1440 else f"M{tf}"
            _line(out, sym, name, tf_label, s, cons, s_out, v, time.time() - t0)
            if s.get("n", 0):
                t.to_csv(f"logs/acoes_v2_{sym}_{tf_label}_{name}.csv", index=False)
                ranking.append({
                    "sym": sym, "tf": tf_label, "setup": name, **s,
                    "cons%": cons, "oos_net": s_out.get("net"), "veredito": v,
                })
                pool[name].append(t.assign(setup=name, tf=tf_label))

    # Ranking por paper
    out(f"\n\n{'#' * 76}\n  RANKING POR PAPER (n≥40)\n{'#' * 76}")
    rk = pd.DataFrame(ranking)
    if not rk.empty:
        rk2 = rk[rk.n >= 40].sort_values("net", ascending=False)
        if rk2.empty:
            out("  (nenhum com n≥40 por paper)")
        for _, r in rk2.iterrows():
            out(f"  {r.sym:<7} {r.tf:<4} {str(r.setup):<22} n={int(r.n):<5} "
                f"net {r.net:+.3f} R  PF {r.PF:<5} cons {r['cons%']}%  "
                f"OOS {r.oos_net}  {r.veredito}")
        rk.to_csv(f"logs/backtest_acoes_v2_ranking_{stamp}.csv", index=False)

    # Pool cross-symbol (plan: n≥40 no pool)
    out(f"\n{'#' * 76}\n  POOL CROSS-SYMBOL (mesmo setup em todos os papers)\n{'#' * 76}")
    cross_rows = []
    for name, parts in pool.items():
        if not parts:
            out(f"  {name:<22} (sem trades)")
            continue
        t = pd.concat(parts, ignore_index=True).sort_values("ts")
        s = stats(t)
        cons = consistency(t)
        _, s_out = oos_split(t)
        v = verdict(s, cons, s_out)
        oos = s_out.get("net") if s_out.get("n", 0) else "—"
        out(f"  {name:<22} n={s.get('n', 0):<5} "
            + (f"net {s['net']:+.3f} R  PF {s['PF']:<5} cons {cons}%  OOS {oos}  {v}"
               if s.get("n") else "(sem trades)"))
        cross_rows.append({
            "sym": "POOL", "tf": SETUPS[name][1], "setup": name, **s,
            "cons%": cons, "oos_net": s_out.get("net"), "veredito": v,
        })
    if cross_rows:
        pd.DataFrame(cross_rows).to_csv(
            f"logs/backtest_acoes_v2_pool_{stamp}.csv", index=False)

    goes = [r for r in (cross_rows + ranking) if str(r.get("veredito", "")).startswith("🟢")]
    out(f"\n# Veredito campanha: {'🟢 HÁ GO' if goes else '🔴 ZERO GO — monitor permanece scaffold'}")
    out(f"# Log: {log_path}")
    out(f"# FIM")
    logf.close()
    print(f"\n→ {log_path}")


if __name__ == "__main__":
    main()
