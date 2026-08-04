"""
backtest_v6_atoms.py — Dissecção do score legado (Monitor MT5 / generate_signal).

Testa cada GATILHO (evento) do score v6 isolado em WIN/WDO M15, com a mesma
régua de risco v6.2 e filtros de produção (ADX≥23 + HTF 1h alinhado).

Átomos: V6_EMA_CROSS, V6_MACD_CROSS, V6_RSI_EXT, V6_BB_FADE, V6_VOL_SPIKE,
        V6_CANDLE, V6_EMA200_RECLAIM, V6_VWAP_RECLAIM
Controle: V6_FULL_SCORE (|score|≥7, teto exaustão 11)

NÃO altera produção. Integração V7/WinGo só após 🟢 GO.

Uso (MT5 aberto):
    python backtest_v6_atoms.py
    python backtest_v6_atoms.py --symbol WIN
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

from services.trading_costs import roundtrip_cost_pts, min_viable_stop_pts
from services.technical_analysis import (
    compute_indicators, detect_candle_patterns,
    COMPRA_THRESHOLD, VENDA_THRESHOLD, EXHAUSTION_CEILING, ADX_MIN_TREND,
)

OOS_FRAC = 0.30
SL_ATR = 1.2
TP_R = 1.5
EOD_MIN = 16 * 60 + 55
LOOKBACK = 80
WINDOW = 600
ADX_MIN = ADX_MIN_TREND  # 23


# ── Dados ───────────────────────────────────────────────────────────────────

def _mt5_connect():
    import MetaTrader5 as mt5
    if mt5.terminal_info() is None:
        login = int(os.getenv("MT5_LOGIN", "0") or 0)
        kw = {}
        path = os.getenv("MT5_PATH", "")
        if path and os.path.exists(path):
            kw["path"] = path
        pw, srv = os.getenv("MT5_PASSWORD", ""), os.getenv("MT5_SERVER", "")
        if login and pw and srv and srv.lower() not in ("metaquotes-demo", "metaquotes-demo2"):
            kw.update(login=login, password=pw, server=srv)
        if not mt5.initialize(**kw):
            print("MT5 não inicializou:", mt5.last_error())
            sys.exit(1)
    return mt5


def fetch_m15(symbol: str, bars: int = 200000) -> "pd.DataFrame | None":
    mt5 = _mt5_connect()
    sym = symbol
    if sym.upper() in ("WIN", "WDO") and not mt5.symbol_select(sym, True):
        auto = sym.upper() + "$D"
        if mt5.symbol_select(auto, True):
            print(f"  '{sym}' → contínuo '{auto}'")
            sym = auto
    # PowerShell às vezes entrega WIN / WDO sem $D
    if not mt5.symbol_select(sym, True):
        for c in (sym + "$D", sym.replace("$", "") + "$D"):
            if mt5.symbol_select(c, True):
                print(f"  '{sym}' → '{c}'")
                sym = c
                break
    if not mt5.symbol_select(sym, True):
        print(f"símbolo {symbol} indisponível")
        return None
    rates = None
    for count in (bars, 150000, 100000, 60000, 46000, 30000, 20000, 10000):
        if count > bars:
            continue
        rates = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M15, 0, count)
        if rates is not None and len(rates) > 0:
            break
    if rates is None or len(rates) == 0:
        print(f"sem candles {sym}: {mt5.last_error()}")
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
    out.attrs["mt5_symbol"] = sym
    return out


def prepare(df15: pd.DataFrame) -> pd.DataFrame:
    df = compute_indicators(df15)
    # HTF 1h: EMA9×EMA21 (mesmo critério do v6) — shift 1h p/ não look-ahead
    h1 = df[["Open", "High", "Low", "Close", "Volume"]].resample("1h").agg(
        {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
    ).dropna()
    h1 = compute_indicators(h1)
    bias = np.where(h1["ema9"] > h1["ema21"], 1, np.where(h1["ema9"] < h1["ema21"], -1, 0))
    hbias = pd.Series(bias, index=h1.index).shift(1)
    df["htf_bias"] = hbias.reindex(df.index, method="ffill")
    df["vol20"] = df["Volume"].rolling(20).mean()
    df["hh"] = df.index.hour + df.index.minute / 60.0
    df["day"] = df.index.date
    df["mod_day"] = df.index.hour * 60 + df.index.minute
    return df


# ── Filtros + risco ─────────────────────────────────────────────────────────

def gates_ok(row, direction: str) -> bool:
    adx = row.get("adx")
    if adx is None or (isinstance(adx, float) and np.isnan(adx)) or float(adx) < ADX_MIN:
        return False
    hb = row.get("htf_bias")
    if hb is None or (isinstance(hb, float) and np.isnan(hb)):
        return False
    if direction == "COMPRA" and float(hb) <= 0:
        return False
    if direction == "VENDA" and float(hb) >= 0:
        return False
    return True


def levels(close, atr, direction):
    if atr is None or atr <= 0 or close is None:
        return None
    if direction == "COMPRA":
        sl = close - SL_ATR * atr
        risk = close - sl
        tp = close + TP_R * risk
    else:
        sl = close + SL_ATR * atr
        risk = sl - close
        tp = close - TP_R * risk
    if risk <= 0:
        return None
    return sl, tp, risk


# ── Átomos (eventos) ────────────────────────────────────────────────────────

def atom_ema_cross(df, i):
    r, p = df.iloc[i], df.iloc[i - 1]
    if any(pd.isna(x) for x in (r.ema9, r.ema21, p.ema9, p.ema21)):
        return None
    if r.ema9 > r.ema21 and p.ema9 <= p.ema21:
        return "COMPRA"
    if r.ema9 < r.ema21 and p.ema9 >= p.ema21:
        return "VENDA"
    return None


def atom_macd_cross(df, i):
    r, p = df.iloc[i], df.iloc[i - 1]
    if any(pd.isna(x) for x in (r.macd_hist, p.macd_hist)):
        return None
    if r.macd_hist > 0 and p.macd_hist <= 0:
        return "COMPRA"
    if r.macd_hist < 0 and p.macd_hist >= 0:
        return "VENDA"
    return None


def atom_rsi_ext(df, i):
    """RSI sai de extremo com candle a favor."""
    r, p = df.iloc[i], df.iloc[i - 1]
    if pd.isna(r.rsi) or pd.isna(p.rsi):
        return None
    # sai de sobrevenda
    if p.rsi < 30 and r.rsi >= 30 and r.Close > r.Open:
        return "COMPRA"
    # sai de sobrecompra
    if p.rsi > 70 and r.rsi <= 70 and r.Close < r.Open:
        return "VENDA"
    return None


def atom_bb_fade(df, i):
    """Close fora da banda no prev → fecha de volta para dentro."""
    r, p = df.iloc[i], df.iloc[i - 1]
    if any(pd.isna(x) for x in (r.bb_upper, r.bb_lower, p.bb_upper, p.bb_lower)):
        return None
    if p.Close < p.bb_lower and r.Close >= r.bb_lower and r.Close > r.Open:
        return "COMPRA"
    if p.Close > p.bb_upper and r.Close <= r.bb_upper and r.Close < r.Open:
        return "VENDA"
    return None


def atom_vol_spike(df, i):
    r, p = df.iloc[i], df.iloc[i - 1]
    if pd.isna(r.vol20) or r.vol20 <= 0:
        return None
    if r.Volume <= 1.3 * r.vol20:
        return None
    if r.Close > p.Close:
        return "COMPRA"
    if r.Close < p.Close:
        return "VENDA"
    return None


def atom_candle(df, i):
    win = df.iloc[max(0, i - 1): i + 1]
    if len(win) < 2:
        return None
    _pat, sc = detect_candle_patterns(win)
    if sc > 0:
        return "COMPRA"
    if sc < 0:
        return "VENDA"
    return None


def atom_ema200_reclaim(df, i):
    r, p = df.iloc[i], df.iloc[i - 1]
    if pd.isna(r.ema200) or pd.isna(p.ema200):
        return None
    if p.Close <= p.ema200 and r.Close > r.ema200:
        return "COMPRA"
    if p.Close >= p.ema200 and r.Close < r.ema200:
        return "VENDA"
    return None


def atom_vwap_reclaim(df, i):
    r, p = df.iloc[i], df.iloc[i - 1]
    if pd.isna(r.vwap) or pd.isna(p.vwap):
        return None
    if p.Close <= p.vwap and r.Close > r.vwap:
        return "COMPRA"
    if p.Close >= p.vwap and r.Close < r.vwap:
        return "VENDA"
    return None


ATOMS = {
    "V6_EMA_CROSS": atom_ema_cross,
    "V6_MACD_CROSS": atom_macd_cross,
    "V6_RSI_EXT": atom_rsi_ext,
    "V6_BB_FADE": atom_bb_fade,
    "V6_VOL_SPIKE": atom_vol_spike,
    "V6_CANDLE": atom_candle,
    "V6_EMA200_RECLAIM": atom_ema200_reclaim,
    "V6_VWAP_RECLAIM": atom_vwap_reclaim,
}


# ── Simulador átomos ────────────────────────────────────────────────────────

def simulate_atom(df, symbol, atom_fn):
    trades, pos = [], None
    n = len(df)
    min_stop = min_viable_stop_pts(symbol, 0.10)
    for i in range(max(LOOKBACK, 210), n):
        r = df.iloc[i]
        mod = int(r["mod_day"])

        if pos is not None:
            buy = pos["dir"] == "COMPRA"
            exit_R = None
            stopped = True
            if (r.Low <= pos["sl"]) if buy else (r.High >= pos["sl"]):
                exit_R = ((pos["sl"] - pos["entry"]) / pos["risk"]) if buy else (
                    (pos["entry"] - pos["sl"]) / pos["risk"])
            elif (r.High >= pos["tp"]) if buy else (r.Low <= pos["tp"]):
                exit_R = TP_R
                stopped = False
            elif mod >= EOD_MIN or r["day"] != pos["day"]:
                px = float(r.Close) if mod >= EOD_MIN else float(df.iloc[i - 1].Close)
                exit_R = ((px - pos["entry"]) / pos["risk"]) if buy else (
                    (pos["entry"] - px) / pos["risk"])
                stopped = False
            if exit_R is not None:
                cost = roundtrip_cost_pts(symbol, entry_limit=False, stopped=stopped) / pos["risk"]
                trades.append({
                    "ts": pos["ts"], "sym": symbol, "dir": pos["dir"],
                    "gross_R": round(exit_R, 3), "cost_R": round(cost, 3),
                    "net_R": round(exit_R - cost, 3),
                })
                pos = None
            continue

        if mod >= EOD_MIN - 15:  # não abre perto do EOD
            continue
        direction = atom_fn(df, i)
        if not direction:
            continue
        if not gates_ok(r, direction):
            continue
        atr = float(r.atr) if not pd.isna(r.atr) else 0.0
        close = float(r.Close)
        lv = levels(close, atr, direction)
        if not lv:
            continue
        sl, tp, risk = lv
        if risk < min_stop:
            continue
        pos = {
            "ts": df.index[i], "day": r["day"], "dir": direction,
            "entry": close, "sl": sl, "tp": tp, "risk": risk,
        }
    return pd.DataFrame(trades)


def score_at(df, i) -> "str | None":
    """Réplica enxuta do generate_signal (threshold/exaustão) sobre colunas pré-computadas."""
    r, p = df.iloc[i], df.iloc[i - 1]
    score = 0
    # EMA 9/21
    if not (pd.isna(r.ema9) or pd.isna(r.ema21)):
        score += 1 if r.ema9 > r.ema21 else -1
        if not (pd.isna(p.ema9) or pd.isna(p.ema21)):
            if r.ema9 > r.ema21 and p.ema9 <= p.ema21:
                score += 2
            elif r.ema9 < r.ema21 and p.ema9 >= p.ema21:
                score -= 2
        if i >= 5 and not pd.isna(df.iloc[i - 5].ema9):
            score += 1 if r.ema9 > df.iloc[i - 5].ema9 else -1
    if not pd.isna(r.ema50):
        score += 1 if r.Close > r.ema50 else -1
    if not pd.isna(r.ema200):
        score += 2 if r.Close > r.ema200 else -2
    if not pd.isna(r.vwap):
        score += 1 if r.Close > r.vwap else -1
    if not pd.isna(r.rsi):
        if r.rsi < 30:
            score += 2
        elif r.rsi < 40:
            score += 1
        elif r.rsi > 70:
            score -= 2
        elif r.rsi > 60:
            score -= 1
        if i >= 5 and not pd.isna(df.iloc[i - 5].rsi):
            diff = r.rsi - df.iloc[i - 5].rsi
            if diff > 3:
                score += 1
            elif diff < -3:
                score -= 1
    if not pd.isna(r.macd_hist):
        cross = False
        if not pd.isna(p.macd_hist):
            if r.macd_hist > 0 and p.macd_hist <= 0:
                score += 2
                cross = True
            elif r.macd_hist < 0 and p.macd_hist >= 0:
                score -= 2
                cross = True
        if r.macd_hist > 0:
            score += 1
        else:
            score -= 1
        # (cross já somou ±2; hist ±1 também no v6 — mantém fidelidade)
        if cross:
            pass
    if not (pd.isna(r.bb_mid) or pd.isna(r.bb_upper) or pd.isna(r.bb_lower)):
        score += 1 if r.Close > r.bb_mid else -1
        if r.Close < r.bb_lower:
            score += 1
        elif r.Close > r.bb_upper:
            score -= 1
    if not pd.isna(r.vol20) and r.vol20 > 0:
        if r.Volume > 1.3 * r.vol20:
            score += 1 if r.Close > p.Close else -1
    # candles
    _pat, csc = detect_candle_patterns(df.iloc[max(0, i - 1): i + 1])
    score += csc
    # HTF
    hb = r.htf_bias
    if not pd.isna(hb):
        score += 1 if hb > 0 else (-1 if hb < 0 else 0)

    # ADX filter (neutraliza threshold)
    adx_filtered = pd.isna(r.adx) or float(r.adx) < ADX_MIN
    effective = score
    if adx_filtered:
        effective = max(min(score, COMPRA_THRESHOLD - 1), VENDA_THRESHOLD + 1)

    # filtro HTF direcional v6
    if not pd.isna(hb):
        if hb < 0 and effective >= COMPRA_THRESHOLD:
            effective = COMPRA_THRESHOLD - 1
        elif hb > 0 and effective <= VENDA_THRESHOLD:
            effective = VENDA_THRESHOLD + 1

    if abs(effective) >= EXHAUSTION_CEILING:
        return None
    if effective >= COMPRA_THRESHOLD:
        return "COMPRA"
    if effective <= VENDA_THRESHOLD:
        return "VENDA"
    return None


def simulate_full_score(df15_raw, df, symbol):
    """Controle V6_FULL_SCORE — mesma gestão SL/TP do runner de átomos."""
    return simulate_atom(df, symbol, score_at)


# ── Stats / veredito ────────────────────────────────────────────────────────

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
        return "amostra fraca"
    oos_ok = (s_out.get("n", 0) >= 15 and s_out.get("net", -9) >= 0.08
              and s_out.get("PF", 0) >= 1.15)
    if s["net"] >= 0.10 and s["PF"] >= 1.25 and cons >= 55 and oos_ok:
        return "GO"
    if s["net"] > 0.05 and s["PF"] >= 1.15:
        return "promissor" + ("" if oos_ok else " (OOS fraco)")
    return "REPROVADO"


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default=None, help="WIN ou WDO (resolve p/ $D)")
    ap.add_argument("--bars", type=int, default=200000)
    ap.add_argument("--skip-full", action="store_true",
                    help="pula V6_FULL_SCORE (mais rápido)")
    args = ap.parse_args()

    if args.symbol:
        syms = [args.symbol.upper().replace("$D", "").replace("$", "")]
        # normaliza para base WIN/WDO
        if "WDO" in syms[0]:
            syms = ["WDO"]
        elif "WIN" in syms[0]:
            syms = ["WIN"]
    else:
        syms = ["WIN", "WDO"]

    os.makedirs("logs", exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    log_path = f"logs/backtest_v6_atoms_{stamp}.txt"
    logf = open(log_path, "w", encoding="utf-8")

    def out(s):
        print(s)
        logf.write(s + "\n")
        logf.flush()

    out(f"{'#' * 76}")
    out(f"# BACKTEST V6 ATOMS — dissecção do score Monitor MT5 · {datetime.now():%d/%m/%Y %H:%M}")
    out(f"# Ativos: {', '.join(syms)} · M15 · filtros ADX>={ADX_MIN} + HTF · SL {SL_ATR}ATR · TP {TP_R}R")
    out(f"# GO: net>=+0.10R · PF>=1.25 · cons>=55% · OOS · n>=40")
    out(f"{'#' * 76}")

    ranking = []
    for base in syms:
        out(f"\n\n{'█' * 76}\n█  {base}\n{'█' * 76}")
        raw = fetch_m15(base, args.bars)
        if raw is None or len(raw) < 5000:
            out(f"  histórico insuficiente")
            continue
        mt5_sym = raw.attrs.get("mt5_symbol", base)
        df = prepare(raw)
        anos = len(df) / 28 / 252
        out(f"  {mt5_sym}: {len(df)} M15 (~{anos:.1f} anos) "
            f"[{df.index[0].date()} → {df.index[-1].date()}]")

        setups = list(ATOMS.items())
        if not args.skip_full:
            setups.append(("V6_FULL_SCORE", None))

        for name, fn in setups:
            t0 = time.time()
            try:
                if name == "V6_FULL_SCORE":
                    t = simulate_full_score(raw, df, mt5_sym)
                else:
                    t = simulate_atom(df, mt5_sym, fn)
            except Exception as exc:
                out(f"    {name:<22} ERRO: {exc}")
                continue
            s = stats(t)
            cons = consistency(t)
            _, s_out = oos_split(t)
            v = verdict(s, cons, s_out)
            dt = time.time() - t0
            if s.get("n", 0) == 0:
                out(f"    {name:<22} (sem trades)  [{dt:.0f}s]")
                continue
            oos = s_out.get("net") if s_out.get("n", 0) else "—"
            out(f"    {name:<22} n={s['n']:<5} win {s['win']:>5}% net {s['net']:+.3f} R  "
                f"PF {s['PF']:<5} tot {s['tot']:+7.1f} DD {s['dd']:>7} cons {cons}%  "
                f"OOS {oos}  {v}  [{dt:.0f}s]")
            t.to_csv(f"logs/v6atoms_{base}_{name}.csv", index=False)
            ranking.append({
                "sym": base, "setup": name, **s, "cons%": cons,
                "oos_net": s_out.get("net"), "veredito": v,
            })

    out(f"\n\n{'#' * 76}\n  RANKING (n≥40)\n{'#' * 76}")
    rk = pd.DataFrame(ranking)
    if not rk.empty:
        rk2 = rk[rk.n >= 40].sort_values("net", ascending=False)
        if rk2.empty:
            out("  (nenhum com n≥40)")
        for _, r in rk2.iterrows():
            out(f"  {r.sym:<5} {str(r.setup):<22} n={int(r.n):<5} net {r.net:+.3f} R  "
                f"PF {r.PF:<5} cons {r['cons%']}%  OOS {r.oos_net}  {r.veredito}")
        rk.to_csv(f"logs/backtest_v6_atoms_ranking_{stamp}.csv", index=False)

    goes = [r for r in ranking if str(r.get("veredito", "")).startswith("GO")]
    out(f"\n# Veredito: {'HA GO — candidatos a path V7/WinGo' if goes else 'ZERO GO — sem integracao live'}")
    out(f"# Log: {log_path}")
    out("# FIM")
    logf.close()
    print(f"\n→ {log_path}")


if __name__ == "__main__":
    main()
