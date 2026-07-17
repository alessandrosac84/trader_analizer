"""
pesquisa_b3.py — MINERAÇÃO DE EDGE no B3 sobre anos de histórico.

Em vez de assumir um sinal genérico (EMA/RSI/score), este script TESTA hipóteses
reais de edge sobre os dados e mostra quais têm vantagem que sobrevive aos custos.
É a "descoberta dirigida por dados": deixa o histórico dizer o que funciona.

Roda NA SUA MÁQUINA com o MT5 aberto (usa WIN$/WDO$ contínuos, ~2,7 anos).
  python pesquisa_b3.py                          # WIN$ e WDO$, M5, custo 0.07R
  python pesquisa_b3.py --symbol 'WIN$' --tf 5
  python pesquisa_b3.py --cost 0.05

Hipóteses testadas (todas com custo descontado, resultado em R):
  1. MAPA POR HORA      — em que horário o mercado tem viés direcional (drift)
  2. ORB                — rompimento da máxima/mínima dos primeiros X min do pregão
  3. REVERSÃO VWAP      — fadear quando o preço se estica N×ATR da VWAP da sessão
  4. MOMENTUM/PERSIST.  — um candle forte tende a continuar no próximo?
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
    print("Instale pandas/numpy: pip install pandas numpy"); sys.exit(1)
try:
    import MetaTrader5 as mt5
except ImportError:
    print("MetaTrader5 não instalado. Rode na máquina Windows com o MT5 aberto."); sys.exit(1)

_TF = {"1": mt5.TIMEFRAME_M1, "5": mt5.TIMEFRAME_M5, "15": mt5.TIMEFRAME_M15,
       "30": mt5.TIMEFRAME_M30, "60": mt5.TIMEFRAME_H1}


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


def _fetch(symbol, tf_key, days=1000):
    tf = _TF.get(str(tf_key), mt5.TIMEFRAME_M5)
    if not mt5.symbol_select(symbol, True):
        print(f"  ({symbol}: não consegui selecionar no Market Watch)")
        return None
    # 1) por posição (funciona bem até ~30k barras); 2) fallback por intervalo de datas
    rates = mt5.copy_rates_from_pos(symbol, tf, 0, 30000)
    if rates is None or len(rates) == 0:
        frm = datetime.now() - timedelta(days=days)
        to = datetime.now() + timedelta(days=1)
        rates = mt5.copy_rates_range(symbol, tf, frm, to)
    if rates is None or len(rates) == 0:
        print(f"  ({symbol} tf={tf_key}m: MT5 não retornou candles — {mt5.last_error()}. "
              f"Tente --tf 15, ou aumente 'Máx. barras' nas opções do MT5.)")
        return None
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df = df.set_index("time")
    vol = df["real_volume"] if df["real_volume"].sum() > 0 else df["tick_volume"]
    return pd.DataFrame({"Open": df["open"], "High": df["high"], "Low": df["low"],
                         "Close": df["close"], "Vol": vol}, index=df.index)


def _atr(df, n=14):
    h, l, c = df["High"], df["Low"], df["Close"]
    tr = pd.concat([(h - l), (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()


def _report(name, Rs, cost):
    """Rs = lista de R-multiplos (bruto). Aplica custo e imprime as métricas."""
    if not Rs:
        print(f"  {name:34}: (sem trades)")
        return None
    r = np.array(Rs, dtype=float) - cost
    n = len(r)
    wins = r[r > 0]; losses = r[r <= 0]
    wr = len(wins) / n * 100
    gw = wins.sum(); gl = -losses.sum()
    pf = (gw / gl) if gl > 0 else float("inf")
    exp = r.mean()
    flag = "🟢" if exp > 0.01 else ("🟡" if exp > -0.01 else "🔴")
    print(f"  {flag} {name:32}: exp {exp:+.3f}R  PF {pf:>4.2f}  WR {wr:>4.1f}%  n={n}  soma {r.sum():+.0f}R")
    return {"exp": exp, "pf": pf, "n": n}


# ── Utilidades intraday ─────────────────────────────────────────────────────
def _sessions(df):
    """Agrupa por dia de pregão (usa a data do índice)."""
    return df.groupby(df.index.date)


def _eval_dir(direc, entry, stop, target, future):
    """Retorna R (múltiplo do risco) de um trade, varrendo os candles à frente."""
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    for _, row in future.iterrows():
        hi, lo = row["High"], row["Low"]
        if direc == "LONG":
            if lo <= stop: return -1.0
            if hi >= target: return (target - entry) / risk
        else:
            if hi >= stop: return -1.0
            if lo <= target: return (entry - target) / risk
    last = future["Close"].iloc[-1] if len(future) else entry
    mtm = (last - entry) if direc == "LONG" else (entry - last)
    return mtm / risk


# ── 1. MAPA POR HORA — viés direcional por horário ──────────────────────────
def edge_by_hour(df, fwd=6):
    """Retorno médio (em ATR) mantendo COMPRADO por `fwd` candles, por hora do dia.
    Revela se existe drift (viés de alta/baixa) sistemático em certos horários."""
    atr = _atr(df).shift(1)
    fut_ret = (df["Close"].shift(-fwd) - df["Close"]) / atr
    tmp = pd.DataFrame({"hour": df.index.hour, "ret": fut_ret}).dropna()
    print("\n  ── 1. VIÉS DIRECIONAL POR HORA (retorno médio em ATR, comprado %d velas) ──" % fwd)
    print("     (positivo = tende a SUBIR nesse horário | negativo = tende a CAIR)")
    g = tmp.groupby("hour")["ret"].agg(["mean", "count"])
    for h, row in g.iterrows():
        if row["count"] >= 50:
            bar = "▲" if row["mean"] > 0.02 else ("▼" if row["mean"] < -0.02 else "·")
            print(f"     {int(h):02d}h: {row['mean']:+.3f} ATR  {bar}  (n={int(row['count'])})")


# ── 2. ORB — rompimento do range de abertura ────────────────────────────────
def orb(df, or_minutes=30, rr=1.0, cost=0.0, sess_start=9):
    """Define a máx/mín dos primeiros `or_minutes` do pregão; opera o rompimento.
    Stop no lado oposto do range, alvo em rr× o range. 1 trade por dia."""
    Rs = []
    for _, day in _sessions(df):
        day = day[day.index.hour >= sess_start]
        if len(day) < 10:
            continue
        t0 = day.index[0]
        or_win = day[day.index < t0 + pd.Timedelta(minutes=or_minutes)]
        rest = day[day.index >= t0 + pd.Timedelta(minutes=or_minutes)]
        if len(or_win) < 2 or len(rest) < 3:
            continue
        orh, orl = or_win["High"].max(), or_win["Low"].min()
        rng = orh - orl
        if rng <= 0:
            continue
        for i in range(len(rest)):
            row = rest.iloc[i]
            fut = rest.iloc[i + 1:]
            if len(fut) < 2:
                break
            if row["High"] >= orh:          # rompeu p/ cima → LONG
                R = _eval_dir("LONG", orh, orl, orh + rr * rng, fut); Rs.append(R); break
            if row["Low"] <= orl:           # rompeu p/ baixo → SHORT
                R = _eval_dir("SHORT", orl, orh, orl - rr * rng, fut); Rs.append(R); break
    return [x for x in Rs if x is not None]


# ── 3. REVERSÃO VWAP — fadear esticadas da VWAP da sessão ────────────────────
def vwap_reversion(df, dev=1.5, cost=0.0):
    """Quando o preço se estica `dev`×ATR da VWAP da sessão, fadeia de volta à VWAP.
    Stop = mesma distância adicional; alvo = VWAP. 1 tentativa por esticada."""
    Rs = []
    atr_full = _atr(df)
    for _, day in _sessions(df):
        if len(day) < 20:
            continue
        tp = (day["High"] + day["Low"] + day["Close"]) / 3
        cum_v = day["Vol"].cumsum().replace(0, np.nan)
        vwap = (tp * day["Vol"]).cumsum() / cum_v
        a = atr_full.reindex(day.index).ffill()
        armed = True
        for i in range(15, len(day) - 3):
            price = day["Close"].iloc[i]
            v = vwap.iloc[i]; at = a.iloc[i]
            if pd.isna(v) or pd.isna(at) or at <= 0:
                continue
            dist = (price - v) / at
            fut = day.iloc[i + 1:]
            if dist >= dev and armed:                 # esticou p/ cima → SHORT (volta à VWAP)
                R = _eval_dir("SHORT", price, price + dev * at, v, fut); Rs.append(R); armed = False
            elif dist <= -dev and armed:              # esticou p/ baixo → LONG
                R = _eval_dir("LONG", price, price - dev * at, v, fut); Rs.append(R); armed = False
            elif abs(dist) < 0.3:
                armed = True
    return [x for x in Rs if x is not None]


# ── 4. MOMENTUM / PERSISTÊNCIA — candle forte continua? ─────────────────────
def momentum_persist(df, body_atr=0.8, rr=1.0):
    """Se um candle fecha com corpo forte (>body_atr×ATR) na direção do movimento,
    entra a favor no próximo, stop = 1×ATR, alvo = rr×ATR. Testa se momentum segue."""
    atr = _atr(df)
    Rs = []
    for i in range(20, len(df) - 4):
        at = atr.iloc[i]
        if pd.isna(at) or at <= 0:
            continue
        body = df["Close"].iloc[i] - df["Open"].iloc[i]
        if abs(body) < body_atr * at:
            continue
        entry = df["Close"].iloc[i]
        fut = df.iloc[i + 1:i + 5]
        if len(fut) < 2:
            continue
        if body > 0:      # candle de alta forte → LONG
            Rs.append(_eval_dir("LONG", entry, entry - at, entry + rr * at, fut))
        else:             # candle de baixa forte → SHORT
            Rs.append(_eval_dir("SHORT", entry, entry + at, entry - rr * at, fut))
    return [x for x in Rs if x is not None]


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Mineração de edge no B3 (data-driven)")
    ap.add_argument("--symbol", default="WIN$,WDO$", help="Símbolos (vírgula). Padrão WIN$,WDO$")
    ap.add_argument("--tf", default="5", help="Timeframe: 5, 15 (intraday). Padrão 5m")
    ap.add_argument("--days", type=int, default=1000, help="Dias de histórico a puxar")
    ap.add_argument("--cost", type=float, default=0.07, help="Custo por trade em R. Padrão 0.07")
    args = ap.parse_args()

    if not _mt5_init():
        print("❌ MT5 não inicializou:", mt5.last_error()); sys.exit(1)

    for sym in [s.strip() for s in args.symbol.split(",") if s.strip()]:
        df = _fetch(sym, args.tf, args.days)
        if df is None or len(df) < 500:
            print(f"\n{sym}: sem dados suficientes"); continue
        print("\n" + "═" * 68)
        print(f"  PESQUISA DE EDGE — {sym}  ·  tf={args.tf}m  ·  custo {args.cost}R/trade")
        print(f"  {len(df)} candles  ({df.index[0]} → {df.index[-1]})")
        print("═" * 68)

        edge_by_hour(df)

        print("\n  ── 2. ORB (rompimento do range de abertura) ──")
        for orm in (15, 30, 60):
            for rr in (1.0, 1.5):
                _report(f"ORB {orm}min · alvo {rr}R", orb(df, orm, rr, args.cost), args.cost)

        print("\n  ── 3. REVERSÃO VWAP (fadear esticadas) ──")
        for dev in (1.0, 1.5, 2.0):
            _report(f"VWAP fade · {dev}×ATR", vwap_reversion(df, dev, args.cost), args.cost)

        print("\n  ── 4. MOMENTUM (candle forte continua?) ──")
        for rr in (1.0, 1.5):
            _report(f"Momentum · alvo {rr}R", momentum_persist(df, 0.8, rr), args.cost)

        print("\n  → Procure 🟢: expectância > 0 e PF > 1 DEPOIS do custo. Isso é edge real.")
    mt5.shutdown()
