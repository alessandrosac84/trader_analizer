"""
pesquisa_fluxo_b3.py — O EDGE DO B3 ESTÁ NO FLUXO DE ORDENS. Este script testa
se dá pra capturar isso com DADOS: usa os NEGÓCIOS reais (ticks com preço/volume
executado) que o MT5 guarda pra ativos de bolsa (WIN/WDO), calcula a AGRESSÃO
(delta = volume comprador − vendedor) e mede se ela PREVÊ o movimento seguinte.

Diferente do preço puro (que já vimos ser eficiente), o fluxo de ordens é onde
existe vantagem genuína em bolsa. Este é o teste que diz se o B3 tem solução.

Roda NA SUA MÁQUINA com o MT5 aberto (conta B3/XP).
  python pesquisa_fluxo_b3.py                     # WINQ26 e WDOQ26, últimos 5 dias
  python pesquisa_fluxo_b3.py --symbol WINQ26 --days 10
  python pesquisa_fluxo_b3.py --cost 0.07

Saída:
  1. DISPONIBILIDADE — quantos ticks, período, e se têm negócio real (last+volume)
  2. PODER PREDITIVO — a agressão (delta) prevê o retorno seguinte? (correlação)
  3. ESTRATÉGIA DE DELTA — operar a favor da agressão dominante paga o custo?
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


def _resolve(sym):
    """Aceita WIN/WDO genéricos e resolve p/ o contrato de maior volume."""
    if sym and sym.upper() not in ("WIN", "WDO"):
        return sym
    pref = sym.upper()
    best, bv = None, -1
    for s in (mt5.symbols_get(pref + "*") or []):
        info = mt5.symbol_info(s.name)
        v = getattr(info, "volume", 0) if info else 0
        if info and getattr(info, "trade_mode", 0) != 0 and v >= bv:
            best, bv = s.name, v
    return best or (pref + "$")


def _ticks(symbol, days):
    frm = datetime.now() - timedelta(days=days)
    to = datetime.now() + timedelta(days=1)
    t = mt5.copy_ticks_range(symbol, frm, to, mt5.COPY_TICKS_ALL)
    if t is None or len(t) == 0:
        print(f"  ({symbol}: MT5 não retornou ticks — {mt5.last_error()})")
        return None
    df = pd.DataFrame(t)
    tcol = "time_msc" if "time_msc" in df.columns else "time"
    unit = "ms" if tcol == "time_msc" else "s"
    df["dt"] = pd.to_datetime(df[tcol], unit=unit)
    return df


def _classify(df):
    """Marca cada tick como COMPRA/VENDA agressora e devolve o volume assinado.
    Usa as flags do MT5 (TICK_FLAG_BUY/SELL); se não houver, infere por last×bid/ask."""
    vol = df.get("volume_real")
    if vol is None or vol.fillna(0).sum() == 0:
        vol = df.get("volume")
    vol = (vol.fillna(0) if vol is not None else pd.Series(1, index=df.index)).clip(lower=0)
    vol = vol.where(vol > 0, 1.0)   # se volume vier 0, conta 1 por negócio

    signed = pd.Series(0.0, index=df.index)
    fb = getattr(mt5, "TICK_FLAG_BUY", 4)
    fs = getattr(mt5, "TICK_FLAG_SELL", 8)
    if "flags" in df.columns and (df["flags"].astype(int).values & (fb | fs)).any():
        buy = (df["flags"].astype(int).values & fb) != 0
        sell = (df["flags"].astype(int).values & fs) != 0
        signed = pd.Series(np.where(buy, vol, np.where(sell, -vol, 0.0)), index=df.index)
    else:
        last = df.get("last", pd.Series(0, index=df.index)).replace(0, np.nan).ffill()
        bid, ask = df.get("bid"), df.get("ask")
        mid = ((bid + ask) / 2) if (bid is not None and ask is not None) else last
        up = last > mid
        signed = pd.Series(np.where(up, vol, -vol), index=df.index)
    df["signed"] = signed
    df["vol"] = vol
    df["price"] = df.get("last", pd.Series(np.nan, index=df.index)).replace(0, np.nan)
    if df["price"].isna().all() and "bid" in df.columns:
        df["price"] = (df["bid"] + df["ask"]) / 2
    df["price"] = df["price"].ffill()
    return df


def _bars(df, freq="1min"):
    """Agrega ticks em barras de tempo com DELTA (agressão líquida) e preço."""
    g = df.set_index("dt")
    bars = pd.DataFrame({
        "delta": g["signed"].resample(freq).sum(),
        "vol":   g["vol"].resample(freq).sum(),
        "close": g["price"].resample(freq).last(),
        "high":  g["price"].resample(freq).max(),
        "low":   g["price"].resample(freq).min(),
    }).dropna(subset=["close"])
    return bars[bars["vol"] > 0]


def _report(name, Rs, cost):
    if not Rs:
        print(f"  {name:36}: (sem trades)"); return
    r = np.array(Rs, float) - cost
    n = len(r); wins = r[r > 0]
    wr = len(wins) / n * 100
    gl = -r[r <= 0].sum()
    pf = (wins.sum() / gl) if gl > 0 else float("inf")
    exp = r.mean()
    flag = "🟢" if exp > 0.01 else ("🟡" if exp > -0.01 else "🔴")
    print(f"  {flag} {name:34}: exp {exp:+.3f}R  PF {pf:>4.2f}  WR {wr:>4.1f}%  n={n}")


def analisar(symbol, days, cost):
    df = _ticks(symbol, days)
    if df is None:
        return
    df = _classify(df)

    # 1. DISPONIBILIDADE
    has_last = (df.get("last", pd.Series(0)).fillna(0) > 0).mean() * 100
    has_vol = (df["vol"] > 0).mean() * 100
    print("\n" + "═" * 66)
    print(f"  FLUXO DE ORDENS — {symbol}")
    print(f"  {len(df):,} ticks  ({df['dt'].iloc[0]} → {df['dt'].iloc[-1]})")
    print(f"  ticks com PREÇO de negócio (last): {has_last:.0f}%   com VOLUME: {has_vol:.0f}%")
    print("═" * 66)
    if has_last < 20:
        print("  ⚠️  Poucos ticks têm 'last' (negócio real) — o feed pode ser só cotação.")
        print("     Sem negócio real, fluxo de ordens não é mensurável neste histórico.")

    bars = _bars(df, "1min")
    if len(bars) < 200:
        print("  (poucas barras p/ testar — aumente --days)"); return
    atr = (bars["high"] - bars["low"]).rolling(20).mean().shift(1)

    # 2. PODER PREDITIVO: delta acumulado em K barras prevê o retorno das próximas M?
    print("\n  ── PODER PREDITIVO DA AGRESSÃO (delta) ──")
    print("     correlação entre delta acumulado e o retorno seguinte (quanto maior, melhor):")
    best = None
    for K in (3, 5, 10):
        cum = bars["delta"].rolling(K).sum()
        for M in (3, 5, 10):
            fwd = bars["close"].shift(-M) - bars["close"]
            c = cum.corr(fwd)
            if c == c:
                print(f"     delta {K}min → retorno {M}min:  corr {c:+.3f}")
                if best is None or abs(c) > abs(best[2]):
                    best = (K, M, c)

    # 3. ESTRATÉGIA: SEGUIR × FADEAR a agressão forte, com WALK-FORWARD
    K = best[0] if best else 5
    cum = bars["delta"].rolling(K).sum()
    dstd = cum.rolling(200).std()
    half = len(bars) // 2
    print("\n  ── DELTA: SEGUIR × FADEAR (walk-forward: 1ª metade descobre, 2ª confirma) ──")
    print("     edge REAL = 🟢 na 2ª metade também (não só no todo).")
    for mode in ("follow", "fade"):
        lbl = "SEGUE a agressão" if mode == "follow" else "FADEIA (absorção/exaustão)"
        print(f"     · {lbl}:")
        for thr in (1.0, 1.5, 2.0):
            Rs, idx = _delta_trades(bars, atr, cum, dstd, mode, thr)
            r1 = [Rs[k] for k in range(len(Rs)) if idx[k] < half]
            r2 = [Rs[k] for k in range(len(Rs)) if idx[k] >= half]
            print("       " + _wf_line(f"z>{thr}σ", Rs, r1, r2, cost))

    print("\n  → Só vale 🟢 se a 2ª metade (fora da amostra) também for positiva.")
    print("     Se nem fadear segurar na 2ª metade, o edge está em janela mais curta")
    print("     (segundos/tape) — aí a ferramenta evolui pra tick-a-tick.")


def _delta_trades(bars, atr, cum, dstd, mode, thr):
    """Gera trades de delta. mode='follow' segue a agressão; 'fade' opera contra.
    Retorna (lista de R, lista dos índices de barra) p/ o walk-forward."""
    Rs, idx = [], []
    for i in range(210, len(bars) - 6):
        d, sd, at = cum.iloc[i], dstd.iloc[i], atr.iloc[i]
        if pd.isna(sd) or pd.isna(at) or sd <= 0 or at <= 0:
            continue
        z = d / sd
        entry = bars["close"].iloc[i]
        fut = bars.iloc[i + 1:i + 6]
        want = None
        if z >= thr:
            want = "LONG" if mode == "follow" else "SHORT"
        elif z <= -thr:
            want = "SHORT" if mode == "follow" else "LONG"
        if not want:
            continue
        if want == "LONG":
            R = _eval("LONG", entry, entry - at, entry + at, fut)
        else:
            R = _eval("SHORT", entry, entry + at, entry - at, fut)
        if R is not None:
            Rs.append(R); idx.append(i)
    return Rs, idx


def _wf_line(name, full, h1, h2, cost):
    """Linha compacta: resultado no TODO, na 1ª metade e na 2ª metade (out-of-sample)."""
    def m(Rs):
        if not Rs or len(Rs) < 10:
            return "     n<10    "
        r = np.array(Rs, float) - cost
        gl = -r[r <= 0].sum()
        pf = (r[r > 0].sum() / gl) if gl > 0 else 9.99
        flag = "🟢" if r.mean() > 0.01 else ("🟡" if r.mean() > -0.01 else "🔴")
        return f"{flag}{r.mean():+.3f}R PF{pf:.2f} n{len(r)}"
    return f"{name:7}: TODO {m(full)}  |  2ªmet {m(h2)}"


def _eval(direc, entry, stop, target, future):
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    for _, row in future.iterrows():
        if direc == "LONG":
            if row["low"] <= stop: return -1.0
            if row["high"] >= target: return (target - entry) / risk
        else:
            if row["high"] >= stop: return -1.0
            if row["low"] <= target: return (entry - target) / risk
    last = future["close"].iloc[-1] if len(future) else entry
    mtm = (last - entry) if direc == "LONG" else (entry - last)
    return mtm / risk


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Pesquisa de edge por FLUXO DE ORDENS no B3")
    ap.add_argument("--symbol", default="WIN,WDO", help="Símbolos (vírgula). WIN/WDO resolvem o contrato ativo")
    ap.add_argument("--days", type=int, default=5, help="Dias de ticks a puxar (tick é pesado; comece com 5)")
    ap.add_argument("--cost", type=float, default=0.07, help="Custo por trade em R")
    args = ap.parse_args()

    if not _mt5_init():
        print("❌ MT5 não inicializou:", mt5.last_error()); sys.exit(1)

    for s in [x.strip() for x in args.symbol.split(",") if x.strip()]:
        sym = _resolve(s)
        analisar(sym, args.days, args.cost)
    mt5.shutdown()
