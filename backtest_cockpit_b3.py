"""
backtest_cockpit_b3.py — Mede o EDGE do sinal do Cockpit B3 sobre TICKS reais.

Replica a lógica de confluência do cockpit (agressão forte + delta alinhado →
entrada, com alvo/stop por ATR) e simula os trades sobre os negócios reais do MT5
(ticks com preço/volume), com WALK-FORWARD e custo. Dá a MÉTRICA: esses sinais
ganham de verdade?

⚠️ Só dá pra usar os ticks que a corretora guarda (dias a ~semanas), NÃO os 2,7
anos (candle não tem fluxo). E cobre delta+agressão (o book de 10 níveis não fica
no histórico de tick).

Roda NA SUA MÁQUINA com o MT5 (B3) aberto.
  python backtest_cockpit_b3.py                       # WINZ26 e WDOQ26, ~20 dias
  python backtest_cockpit_b3.py --symbol WINZ26 --days 20
  python backtest_cockpit_b3.py --cost 0.05 --bar 15
"""
import argparse
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass
try:
    import pandas as pd
    import numpy as np
except ImportError:
    print("Instale pandas/numpy"); sys.exit(1)
try:
    import MetaTrader5 as mt5
except ImportError:
    print("MetaTrader5 não instalado. Rode na máquina Windows com o MT5 aberto."); sys.exit(1)


def _mt5_init():
    if mt5.terminal_info() is not None:
        return True
    login = int(os.getenv("MT5_LOGIN", "0") or 0)
    kw = {}
    p = os.getenv("MT5_PATH", "")
    if p and os.path.exists(p):
        kw["path"] = p
    if login and os.getenv("MT5_PASSWORD") and os.getenv("MT5_SERVER"):
        kw.update(login=login, password=os.getenv("MT5_PASSWORD"), server=os.getenv("MT5_SERVER"))
    return mt5.initialize(**kw)


def _ticks(symbol, days):
    frm = datetime.now() - timedelta(days=days)
    to = datetime.now() + timedelta(days=1)
    t = mt5.copy_ticks_range(symbol, frm, to, mt5.COPY_TICKS_ALL)
    if t is None or len(t) == 0:
        print(f"  ({symbol}: sem ticks — {mt5.last_error()})")
        return None
    df = pd.DataFrame(t)
    tcol = "time_msc" if "time_msc" in df.columns else "time"
    df["dt"] = pd.to_datetime(df[tcol], unit="ms" if tcol == "time_msc" else "s")
    return df


def _classify(df):
    vol = df.get("volume_real")
    if vol is None or vol.fillna(0).sum() == 0:
        vol = df.get("volume")
    vol = (vol.fillna(0) if vol is not None else pd.Series(1.0, index=df.index)).clip(lower=0)
    vol = vol.where(vol > 0, 1.0)
    fb = getattr(mt5, "TICK_FLAG_BUY", 4)
    fs = getattr(mt5, "TICK_FLAG_SELL", 8)
    if "flags" in df.columns and (df["flags"].astype(int).values & (fb | fs)).any():
        fl = df["flags"].astype(int).values
        buy = (fl & fb) != 0
        signed = np.where(buy, vol, np.where((fl & fs) != 0, -vol, 0.0))
    else:
        last = df.get("last", pd.Series(0, index=df.index)).replace(0, np.nan).ffill()
        mid = ((df.get("bid") + df.get("ask")) / 2) if "bid" in df.columns else last
        signed = np.where(last > mid, vol, -vol)
    df["signed"] = signed
    df["vol"] = vol
    price = df.get("last", pd.Series(np.nan, index=df.index)).replace(0, np.nan)
    if price.isna().all() and "bid" in df.columns:
        price = (df["bid"] + df["ask"]) / 2
    df["price"] = price.ffill()
    return df


def _bars(df, secs):
    g = df.set_index("dt")
    buy = g["signed"].clip(lower=0)
    sell = (-g["signed"]).clip(lower=0)
    b = pd.DataFrame({
        "delta": g["signed"].resample(f"{secs}s").sum(),
        "buy":   buy.resample(f"{secs}s").sum(),
        "sell":  sell.resample(f"{secs}s").sum(),
        "close": g["price"].resample(f"{secs}s").last(),
        "high":  g["price"].resample(f"{secs}s").max(),
        "low":   g["price"].resample(f"{secs}s").min(),
    }).dropna(subset=["close"])
    return b[(b["buy"] + b["sell"]) > 0]


def _sim(bars, win, aggr_thr, delta_min, rr, atr_mult_stop, max_hold, cost):
    """Confluência do cockpit: agressão forte + delta alinhado na janela `win` → entra.
    alvo = rr×ATR, stop = atr_mult_stop×ATR. Uma posição por vez. Retorna (Rs, idx)."""
    atr = (bars["high"] - bars["low"]).rolling(20).mean().shift(1)
    dsum = bars["delta"].rolling(win).sum()
    bsum = bars["buy"].rolling(win).sum()
    ssum = bars["sell"].rolling(win).sum()
    close = bars["close"].values
    hi = bars["high"].values; lo = bars["low"].values
    n = len(bars)
    Rs, idx = [], []
    i = 25
    while i < n - 2:
        at = atr.iloc[i]
        tot = (bsum.iloc[i] + ssum.iloc[i])
        if pd.isna(at) or at <= 0 or tot <= 0:
            i += 1; continue
        aggr = bsum.iloc[i] / tot * 100
        d = dsum.iloc[i]
        direc = None
        if aggr >= aggr_thr and d >= delta_min:
            direc = "LONG"
        elif aggr <= (100 - aggr_thr) and d <= -delta_min:
            direc = "SHORT"
        if not direc:
            i += 1; continue
        entry = close[i]
        stop = entry - atr_mult_stop * at if direc == "LONG" else entry + atr_mult_stop * at
        alvo = entry + rr * atr_mult_stop * at if direc == "LONG" else entry - rr * atr_mult_stop * at
        risk = abs(entry - stop)
        R = None
        for j in range(i + 1, min(i + 1 + max_hold, n)):
            if direc == "LONG":
                if lo[j] <= stop: R = -1.0; break
                if hi[j] >= alvo: R = rr; break
            else:
                if hi[j] >= stop: R = -1.0; break
                if lo[j] <= alvo: R = rr; break
        if R is None:
            last = close[min(i + max_hold, n - 1)]
            R = ((last - entry) if direc == "LONG" else (entry - last)) / risk
        Rs.append(R - cost); idx.append(i)
        i = j + 1
    return Rs, idx


def _fmt(Rs):
    if not Rs or len(Rs) < 10:
        return "n<10"
    r = np.array(Rs, float)
    gl = -r[r <= 0].sum()
    pf = (r[r > 0].sum() / gl) if gl > 0 else 9.99
    flag = "🟢" if r.mean() > 0.01 else ("🟡" if r.mean() > -0.01 else "🔴")
    return f"{flag} exp {r.mean():+.3f}R  PF {pf:.2f}  WR {(r>0).mean()*100:.0f}%  n={len(r)}"


def _resolve(sym):
    if sym.upper() not in ("WIN", "WDO"):
        return sym
    best, bv = None, -1
    for s in (mt5.symbols_get(sym.upper() + "*") or []):
        info = mt5.symbol_info(s.name)
        v = getattr(info, "volume", 0) if info else 0
        if v >= bv:
            best, bv = s.name, v
    return best or sym


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Backtest do sinal do Cockpit B3 sobre ticks")
    ap.add_argument("--symbol", default="WINZ26,WDOQ26", help="Contratos (vírgula). Use os ATIVOS")
    ap.add_argument("--days", type=int, default=20)
    ap.add_argument("--bar", type=int, default=15, help="Tamanho da barra em segundos")
    ap.add_argument("--win", type=int, default=4, help="Janela de confluência (nº de barras)")
    ap.add_argument("--rr", type=float, default=1.8, help="Alvo em múltiplo do stop (R:R)")
    ap.add_argument("--cost", type=float, default=0.07, help="Custo por trade em R")
    args = ap.parse_args()

    if not _mt5_init():
        print("❌ MT5 não inicializou:", mt5.last_error()); sys.exit(1)

    for raw in [s.strip() for s in args.symbol.split(",") if s.strip()]:
        sym = _resolve(raw)
        df = _ticks(sym, args.days)
        if df is None:
            continue
        df = _classify(df)
        bars = _bars(df, args.bar)
        print("\n" + "═" * 66)
        print(f"  COCKPIT B3 — SINAL DE FLUXO · {sym}  ·  barra {args.bar}s")
        print(f"  {len(df):,} ticks → {len(bars):,} barras  ({df['dt'].iloc[0]} → {df['dt'].iloc[-1]})")
        print(f"  alvo/stop por ATR (R:R {args.rr}) · custo {args.cost}R · walk-forward")
        print("═" * 66)
        if len(bars) < 400:
            print("  (poucas barras — aumente --days)"); continue
        half = len(bars) // 2
        print("  gatilho: agressão forte + delta alinhado na janela")
        for aggr in (62, 66, 70):
            for dmin in (0,):
                Rs, idx = _sim(bars, args.win, aggr, dmin, args.rr, 1.0, int(60 / args.bar * 5), args.cost)
                r2 = [Rs[k] for k in range(len(Rs)) if idx[k] >= half]
                print(f"     agressão≥{aggr}% : TODO {_fmt(Rs)}")
                print(f"                2ªmet {_fmt(r2)}")
        print("\n  → 🟢 no TODO E na 2ªmet = o sinal do cockpit tem edge mensurável.")
        print("     Aí a automação (com ou sem IA) passa a ter base. Se 🔴, fica como")
        print("     apoio à decisão manual (onde seu julgamento faz a diferença).")
    mt5.shutdown()
