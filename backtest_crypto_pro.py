"""
backtest_crypto_pro.py — Backtest COMPLETO do Monitor Crypto (mesma régua do B3).

O que este backtest cobre que o backtest_crypto.py não cobria:
  1. Os TRÊS caminhos de entrada que rodam ao vivo — SCORE, IMPULSO e
     PULLBACK (macro/micro) — e não só o SCORE. Impulso e pullback executam
     em produção sem nunca terem sido validados historicamente.
  2. CUSTOS reais de CFD: spread por round-trip (medido ao vivo do terminal
     quando aberto; senão tabela típica IC Markets).
  3. As DUAS políticas de saída: FIXED (SL/TP da ordem) e MANAGED (réplica do
     crypto_position_manager: breakeven em +1R + guard de devolução do pico).
  4. Máximo de histórico disponível + walk-forward por bimestre + breakdown
     por caminho de entrada.

⚠️ Rodar com o MT5 da ICMarkets aberto (usa MT5_CRYPTO_* do .env).
    Feche/minimize o MT5 da XP para não conectar no terminal errado, ou
    defina MT5_CRYPTO_PATH com o caminho do terminal64.exe da ICMarkets.

Uso:
  python backtest_crypto_pro.py                      # BTCUSD, ETHUSD, XAUUSD
  python backtest_crypto_pro.py --symbol BTCUSD
  python backtest_crypto_pro.py --paths score,impulso   # só alguns caminhos

Saída: log em logs/backtest_crypto_pro_<data>.txt + CSVs por símbolo.
"""
import argparse
import os
import sys
import time
import traceback
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

from services.crypto_analysis import analyze
from services.crypto_momentum import detect as impulse_detect
from services.crypto_config import cfg_for

# Spread típico IC Markets (price units) — usado se o terminal não informar
SPREAD_FALLBACK = {"BTCUSD": 15.0, "ETHUSD": 1.2, "XAUUSD": 0.20,
                   "EURUSD": 0.00007, "GBPUSD": 0.00012}

WINDOW      = 300     # candles M15 por avaliação (≈ produção)
LOOKBACK    = 300
# Gestão (réplica crypto_position_manager)
BREAKEVEN_RR       = 1.0
GIVEBACK_PEAK_MIN  = 0.6
GIVEBACK_KEEP      = 0.5
# Pullback (réplica _mm_compute)
MM_MACRO_PCT_MIN   = 45
MM_DIR_THR         = 0.15
MM_PCT_SCALE       = 130


class Tee:
    def __init__(self, path):
        self.f = open(path, "a", encoding="utf-8"); self.out = sys.stdout
    def write(self, d):
        try: self.out.write(d)
        except UnicodeEncodeError: self.out.write(d.encode("ascii", "replace").decode())
        self.f.write(d); self.f.flush()
    def flush(self): self.out.flush(); self.f.flush()


# ── MT5 (perfil crypto) ────────────────────────────────────────────────────

def _env(k, d=""):
    return os.getenv("MT5_CRYPTO_" + k) or os.getenv("MT5_" + k, d)


def mt5_connect():
    import MetaTrader5 as mt5
    if mt5.terminal_info() is not None:
        return mt5
    kw = {}
    path = _env("PATH")
    if path and os.path.exists(path):
        kw["path"] = path
    login, pw, srv = int(_env("LOGIN", "0") or 0), _env("PASSWORD"), _env("SERVER")
    if login and pw and srv:
        kw.update(login=login, password=pw, server=srv)
    if not mt5.initialize(**kw):
        print("MT5 (crypto) não inicializou:", mt5.last_error()); sys.exit(1)
    acc = mt5.account_info()
    if acc:
        print(f"Conectado: {acc.login} @ {acc.server}")
    return mt5


def fetch(mt5, symbol, tf, bars):
    _TF = {5: mt5.TIMEFRAME_M5, 15: mt5.TIMEFRAME_M15, 30: mt5.TIMEFRAME_M30,
           60: mt5.TIMEFRAME_H1, 240: mt5.TIMEFRAME_H4}
    if not mt5.symbol_select(symbol, True):
        return None
    rates = None
    for count in (bars, 150000, 100000, 50000, 20000, 5000):
        if count > bars: continue
        rates = mt5.copy_rates_from_pos(symbol, _TF[tf], 0, count)
        if rates is not None and len(rates) > 0:
            break
    if rates is None or len(rates) == 0:
        return None
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df = df.set_index("time")
    vol = df["real_volume"] if df["real_volume"].sum() > 0 else df["tick_volume"]
    return pd.DataFrame({"Open": df["open"], "High": df["high"], "Low": df["low"],
                         "Close": df["close"], "Volume": vol}, index=df.index)


def live_spread(mt5, symbol):
    try:
        info = mt5.symbol_info(symbol)
        if info and info.spread and info.point:
            return float(info.spread) * float(info.point)
    except Exception:
        pass
    return None


# ── Séries auxiliares ──────────────────────────────────────────────────────

def h1_macro_series(h1):
    """EMA50×EMA200 do H1 → 'alta'/'baixa'/'lateral' por candle H1."""
    c = h1["Close"]
    e50 = c.ewm(span=50, adjust=False).mean()
    e200 = c.ewm(span=200, adjust=False).mean()
    return pd.Series(np.where(e50 > e200, "alta",
                     np.where(e50 < e200, "baixa", "lateral")), index=h1.index)


def _adx_series(df, n=14):
    h, l, c = df["High"], df["Low"], df["Close"]
    up, dn = h.diff(), -l.diff()
    plus_dm = ((up > dn) & (up > 0)) * up
    minus_dm = ((dn > up) & (dn > 0)) * dn
    tr = pd.concat([(h - l), (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(n).mean().replace(0, 1e-9)
    pdi = 100 * plus_dm.rolling(n).mean() / atr
    mdi = 100 * minus_dm.rolling(n).mean() / atr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, 1e-9)
    return dx.rolling(n).mean()


def tf_mom_series(df, window, ema_f, ema_s):
    """Réplica vetorizada de crypto_macro_micro.tf_momentum → dir por candle."""
    c, o = df["Close"], df["Open"]
    vol = df["Volume"].astype(float).clip(lower=0)
    press = ((c - o) * vol).rolling(window).sum()
    h, l = df["High"], df["Low"]
    tr = pd.concat([(h - l), (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean()
    base = atr * vol.rolling(window).mean() * window
    norm = press / base.replace(0, np.nan)
    ef = c.ewm(span=ema_f, adjust=False).mean()
    es = c.ewm(span=ema_s, adjust=False).mean()
    bias = np.where(ef > es, 1, np.where(ef < es, -1, 0))
    adx = _adx_series(df)
    d = np.where((norm > MM_DIR_THR) & (bias >= 0), "ALTA",
        np.where((norm < -MM_DIR_THR) & (bias <= 0), "BAIXA", "NEUTRO"))
    d = np.where((adx < 18) & (norm.abs() < 0.25), "NEUTRO", d)
    pct = (norm.abs() * MM_PCT_SCALE).clip(upper=100).fillna(0)
    return pd.Series(d, index=df.index), pct


# ── Simulação ──────────────────────────────────────────────────────────────

def simulate(symbol, m15, h1, m5, spread, paths, exits="managed"):
    cfg = cfg_for(symbol)
    macro = h1_macro_series(h1)
    trades = []
    pos = None

    # séries do pullback
    if "pullback" in paths and m5 is not None:
        m5_dir, _ = tf_mom_series(m5, 8, 5, 13)
        h1_dir, h1_pct = tf_mom_series(h1, 16, 9, 21)
    else:
        m5_dir = h1_dir = h1_pct = None
    prev_micro = None

    atr15 = (m15["High"] - m15["Low"]).rolling(14).mean()

    for i in range(LOOKBACK, len(m15)):
        ts = m15.index[i]
        o, h, l, c = [float(m15.iloc[i][k]) for k in ("Open", "High", "Low", "Close")]

        # ── gestão da posição ────────────────────────────────────────────
        if pos:
            buy = pos["dir"] == "COMPRA"
            risk = pos["risk"]
            def r_of(px): return ((px - pos["entry"]) / risk) if buy else ((pos["entry"] - px) / risk)
            hit_stop = (l <= pos["stop"]) if buy else (h >= pos["stop"])
            hit_tp = (h >= pos["tp"]) if buy else (l <= pos["tp"])
            closed = gross = None
            if hit_stop:
                gross = r_of(pos["stop"]); closed = "SL" if abs(pos["stop"]-pos["stop0"]) < 1e-9 else "BE/TRAIL"
            elif hit_tp:
                gross = r_of(pos["tp"]); closed = "TP"
            elif exits == "managed":
                fav = r_of(c)
                pos["peak"] = max(pos["peak"], r_of(h if buy else l))
                if fav < 0.0:
                    pass
                elif not pos["be_done"] and pos["peak"] >= BREAKEVEN_RR:
                    pos["stop"] = pos["entry"]; pos["be_done"] = True
                if (pos["peak"] >= GIVEBACK_PEAK_MIN and fav > 0
                        and fav <= GIVEBACK_KEEP * pos["peak"]):
                    gross = fav; closed = "GIVEBACK"
            if closed:
                cost_r = (spread + pos.get("extra_cost", 0.0)) / risk
                trades.append({"ts": pos["ts"], "close_ts": ts, "setup": pos["setup"],
                               "dir": pos["dir"], "risk": round(risk, 5),
                               "gross_R": round(gross, 3), "cost_R": round(cost_r, 3),
                               "net_R": round(gross - cost_r, 3), "exit": closed})
                pos = None
            continue

        # ── novos sinais (candle fechado i-1) ────────────────────────────
        win = m15.iloc[max(0, i - WINDOW):i]
        candles = [{"open": r_.Open, "high": r_.High, "low": r_.Low,
                    "close": r_.Close, "volume": r_.Volume}
                   for r_ in win.itertuples()]
        # macro H1 vigente
        midx = macro.index[macro.index <= win.index[-1]]
        mdir = macro.loc[midx[-1]] if len(midx) else "lateral"

        sig = None; setup = None
        if "score" in paths:
            s = analyze(candles, cfg)
            if s["acao"] in ("COMPRA", "VENDA") and abs(s.get("score", 0)) >= cfg.get("score_min", 7):
                if ((s["acao"] == "COMPRA" and mdir == "alta") or
                        (s["acao"] == "VENDA" and mdir == "baixa")):
                    if abs(float(s.get("adx", 0) or 0)) >= float(cfg.get("macro_adx", 25)):
                        sig, setup = s, "SCORE"
        if sig is None and "impulso" in paths:
            imp = impulse_detect(candles, cfg)
            if imp.get("impulso") and imp.get("acao") in ("COMPRA", "VENDA"):
                if ((imp["acao"] == "COMPRA" and mdir != "baixa") or
                        (imp["acao"] == "VENDA" and mdir != "alta")):
                    sig, setup = imp, "IMPULSO"
        if sig is None and "pullback" in paths and m5_dir is not None:
            m5idx = m5_dir.index[m5_dir.index <= win.index[-1]]
            h1idx = h1_dir.index[h1_dir.index <= win.index[-1]]
            if len(m5idx) > 6 and len(h1idx):
                micro_now = m5_dir.loc[m5idx[-1]]
                mac_now = h1_dir.loc[h1idx[-1]]
                mac_pct = float(h1_pct.loc[h1idx[-1]])
                want = "COMPRA" if mac_now == "ALTA" else ("VENDA" if mac_now == "BAIXA" else None)
                aligned = micro_now == mac_now and micro_now in ("ALTA", "BAIXA")
                flipped = prev_micro in (None, "NEUTRO",
                                         "BAIXA" if mac_now == "ALTA" else "ALTA")
                if (want and mac_pct >= MM_MACRO_PCT_MIN and aligned and flipped
                        and ((want == "COMPRA" and mdir == "alta")
                             or (want == "VENDA" and mdir == "baixa"))):
                    tail = m5.loc[m5.index <= win.index[-1]].tail(6)
                    px = float(tail["Close"].iloc[-1])
                    if want == "COMPRA":
                        stp = float(tail["Low"].min())
                        if px > stp:
                            sig = {"acao": want, "entrada": px, "stop": stp,
                                   "tp1": px + 1.5 * (px - stp)}
                            setup = "PULLBACK"
                    else:
                        stp = float(tail["High"].max())
                        if stp > px:
                            sig = {"acao": want, "entrada": px, "stop": stp,
                                   "tp1": px - 1.5 * (stp - px)}
                            setup = "PULLBACK"
                prev_micro = micro_now

        if not sig:
            continue
        entry, stop, tp = float(o), float(sig["stop"]), float(sig["tp1"])
        buy = sig["acao"] == "COMPRA"
        risk = abs(entry - stop)
        atr_i = float(atr15.iloc[i - 1] or 0)
        # guards anti-artefato (mesmos do B3): risco mínimo e gap-through
        if risk <= 0 or risk < 0.2 * max(atr_i, 1e-9):
            continue
        if (buy and entry >= tp) or ((not buy) and entry <= tp):
            continue
        pos = {"ts": ts, "dir": sig["acao"], "setup": setup, "entry": entry,
               "stop": stop, "stop0": stop, "tp": tp, "risk": risk,
               "peak": 0.0, "be_done": False}

    return pd.DataFrame(trades)


# ── Relatório ──────────────────────────────────────────────────────────────

def stats(t):
    if t.empty: return {"n": 0}
    w = t[t.net_R > 0]; l = t[t.net_R <= 0]
    gw, gl = w.net_R.sum(), -l.net_R.sum()
    eq = t.net_R.cumsum(); dd = (eq - eq.cummax()).min()
    return {"n": len(t), "win%": round(len(w) / len(t) * 100, 1),
            "gross": round(t.gross_R.mean(), 3), "cost": round(t.cost_R.mean(), 3),
            "net": round(t.net_R.mean(), 3),
            "PF": round(gw / gl, 2) if gl > 0 else float("inf"),
            "tot": round(t.net_R.sum(), 1), "dd": round(dd, 1)}


def report(t, symbol, label):
    print(f"\n{'='*66}\n  {symbol} — {label}\n{'='*66}")
    if t.empty:
        print("  (nenhum trade)"); return
    s = stats(t)
    print(f"  Trades {s['n']} | win {s['win%']}% | bruta {s['gross']:+.3f} | "
          f"custo {s['cost']:.3f} | LÍQUIDA {s['net']:+.3f} R | PF {s['PF']} | "
          f"total {s['tot']:+.1f} R | DD {s['dd']}")
    print("  ── por caminho de entrada ──")
    for k, g in t.groupby("setup"):
        gs = stats(g)
        print(f"    {k:<10} n={gs['n']:<5} net {gs['net']:+.3f} R  PF {gs['PF']:<5} "
              f"tot {gs['tot']:+8.1f}  DD {gs['dd']}")
    print("  ── por saída ──")
    for k, g in t.groupby("exit"):
        gs = stats(g)
        print(f"    {k:<10} n={gs['n']:<5} net {gs['net']:+.3f} R")
    t2 = t.copy(); t2["p"] = pd.to_datetime(t2["ts"]).dt.to_period("2M").astype(str)
    pos_p = tot_p = 0
    print("  ── walk-forward (bimestres) ──")
    for k, g in t2.groupby("p"):
        gs = stats(g); pos_p += gs["net"] > 0; tot_p += 1
        print(f"    {k}  n={gs['n']:<4} net {gs['net']:+.3f} R  [{'+' if gs['net']>0 else '-'}]")
    if tot_p:
        print(f"  Consistência: {pos_p}/{tot_p} ({pos_p/tot_p*100:.0f}%)")
    print("  GO: net >= +0.10 R | PF >= 1.25 | consistência >= 60%")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default=None)
    ap.add_argument("--paths", default="score,impulso,pullback")
    ap.add_argument("--exits", default="managed", choices=["managed", "fixed"])
    ap.add_argument("--bars", type=int, default=200000)
    args = ap.parse_args()

    os.makedirs("logs", exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    sys.stdout = Tee(f"logs/backtest_crypto_pro_{stamp}.txt")
    print(f"\n{'#'*70}\n# BACKTEST CRYPTO PRO — {datetime.now():%d/%m/%Y %H:%M}\n"
          f"# caminhos: {args.paths} | exits: {args.exits}\n{'#'*70}")

    mt5 = mt5_connect()
    symbols = [args.symbol.upper()] if args.symbol else ["BTCUSD", "ETHUSD", "XAUUSD"]
    paths = [p.strip() for p in args.paths.split(",")]

    for sym in symbols:
        try:
            t0 = time.time()
            m15 = fetch(mt5, sym, 15, args.bars)
            if m15 is None or len(m15) < 2000:
                print(f"\n!! {sym}: sem histórico M15 suficiente"); continue
            h1 = fetch(mt5, sym, 60, 100000)
            m5 = fetch(mt5, sym, 5, args.bars) if "pullback" in paths else None
            spread = live_spread(mt5, sym) or SPREAD_FALLBACK.get(sym, 0.0)
            anos = len(m15) / 96 / 365
            print(f"\n{sym}: {len(m15)} candles M15 (~{anos:.1f} anos) | "
                  f"spread usado: {spread} ({'medido' if live_spread(mt5, sym) else 'tabela'})")
            t = simulate(sym, m15, h1, m5, spread, paths, exits=args.exits)
            report(t, sym, f"{args.exits} / {'+'.join(paths)}")
            out = f"logs/backtest_crypto_pro_{sym}.csv"
            t.to_csv(out, index=False)
            print(f"  detalhe: {out}  ({time.time()-t0:.0f}s)")
        except Exception:
            print(f"!! ERRO {sym}:\n{traceback.format_exc()}")

    print(f"\n# FIM — envie logs/backtest_crypto_pro_{stamp}.txt para análise")


if __name__ == "__main__":
    main()
