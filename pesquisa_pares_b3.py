"""
pesquisa_pares_b3.py — ARBITRAGEM ESTATÍSTICA WIN × WDO (par / spread).

Índice e dólar futuro são movidos pelo mesmo macro do Brasil e mantêm uma relação
estável. Quando essa relação se ESTICA além do normal, tende a voltar à média. Esse
é um edge NEUTRO DE MERCADO (não aposta em direção, só na relação) e LENTO o
suficiente pra nossa latência de 1s executar — diferente do scalping de segundos.

Método (textbook de pairs trading):
  1. beta móvel (regressão WIN~WDO)  → hedge ratio
  2. spread = logWIN − beta·logWDO   → série que deve reverter à média
  3. z = (spread − média móvel) / desvio móvel
  4. |z| grande = esticado → FADEIA (aposta na volta ao z=0)
Com walk-forward (1ª metade descobre, 2ª confirma) e custo DOBRADO (2 pernas).

Roda NA SUA MÁQUINA com o MT5 aberto (conta B3).
  python pesquisa_pares_b3.py
  python pesquisa_pares_b3.py --tf 30 --cost 0.05
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

_TF = {"5": mt5.TIMEFRAME_M5, "15": mt5.TIMEFRAME_M15, "30": mt5.TIMEFRAME_M30, "60": mt5.TIMEFRAME_H1}


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


def _fetch(symbol, tf_key, bars=25000):
    tf = _TF.get(str(tf_key), mt5.TIMEFRAME_M15)
    if not mt5.symbol_select(symbol, True):
        return None
    rates = mt5.copy_rates_from_pos(symbol, tf, 0, bars)
    if rates is None or len(rates) == 0:
        frm = datetime.now() - timedelta(days=1000)
        to = datetime.now() + timedelta(days=1)
        rates = mt5.copy_rates_range(symbol, tf, frm, to)
    if rates is None or len(rates) == 0:
        return None
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    return df.set_index("time")["close"]


def _stats(Rs, cost):
    if not Rs or len(Rs) < 10:
        return None
    r = np.array(Rs, float) - cost
    gl = -r[r <= 0].sum()
    pf = (r[r > 0].sum() / gl) if gl > 0 else 9.99
    return {"exp": r.mean(), "pf": pf, "wr": (r > 0).mean() * 100, "n": len(r)}


def _fmt(st):
    if not st:
        return "n<10"
    flag = "🟢" if st["exp"] > 0.01 else ("🟡" if st["exp"] > -0.01 else "🔴")
    return f"{flag}{st['exp']:+.3f}R PF{st['pf']:.2f} WR{st['wr']:.0f}% n{st['n']}"


def _fmt_pct(rets, yrs):
    """Formata o P&L REAL: % por trade, PF, WR, n e retorno anualizado aproximado."""
    if not rets or len(rets) < 10:
        return "n<10"
    r = np.array(rets, float)
    exp = r.mean() * 100
    gl = -r[r <= 0].sum()
    pf = (r[r > 0].sum() / gl) if gl > 0 else 9.99
    wr = (r > 0).mean() * 100
    ann = (r.sum() * 100 / yrs) if yrs > 0 else 0.0
    flag = "🟢" if exp > 0 else "🔴"
    return f"{flag} {exp:+.3f}%/tr PF{pf:.2f} WR{wr:.0f}% n{len(r)} (~{ann:+.0f}%/ano)"


def pairs_trades(z, entry_thr, buffer, max_hold):
    """Fadeia o spread: entra quando |z|>entry_thr, alvo z=0, stop |z|>entry_thr+buffer.
    Retorna lista de (entry_i, exit_i, z0) — p/ medir P&L REAL das pernas depois."""
    trades = []
    i = 0
    n = len(z)
    while i < n - 2:
        zi = z.iloc[i]
        if pd.isna(zi) or abs(zi) < entry_thr:
            i += 1
            continue
        z0 = zi
        stop_z = z0 + buffer if z0 > 0 else z0 - buffer
        exit_i = min(i + max_hold, n - 1)
        for j in range(i + 1, min(i + 1 + max_hold, n)):
            zj = z.iloc[j]
            if pd.isna(zj):
                continue
            if (z0 > 0 and zj <= 0) or (z0 < 0 and zj >= 0):      # alvo (z=0)
                exit_i = j; break
            if (z0 > 0 and zj >= stop_z) or (z0 < 0 and zj <= stop_z):  # stop
                exit_i = j; break
        trades.append((i, exit_i, z0))
        i = exit_i + 1                # não sobrepõe posições
    return trades


def real_pnl(trades, win, wdo, beta, cost_pct):
    """P&L REAL do par em % do capital: short/long as duas pernas no tamanho do hedge.
    Retorna (lista de retornos % líquidos, lista de índices de entrada)."""
    rets, idx = [], []
    wv, dv = win.values, wdo.values
    bv = beta.values
    for (ei, xi, z0) in trades:
        b = bv[ei]
        if not np.isfinite(b) or abs(b) < 1e-6:   # aceita beta NEGATIVO (WIN×WDO é correl. negativa)
            continue
        win_ret = wv[xi] / wv[ei] - 1.0
        wdo_ret = dv[xi] / dv[ei] - 1.0
        # spread = logWIN − beta·logWDO. z0>0 (spread alto) → SHORT spread.
        # A fórmula abaixo já lida com beta de qualquer sinal.
        if z0 > 0:
            pair = -win_ret + b * wdo_ret
        else:                                 # LONG spread
            pair = win_ret - b * wdo_ret
        # custo total do par (4 execuções): peso 1 na perna WIN + |beta| na perna WDO
        rets.append(pair - cost_pct * (1.0 + abs(b)))
        idx.append(ei)
    return rets, idx


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Arbitragem estatística WIN×WDO")
    ap.add_argument("--win", default="WIN$", help="Símbolo do índice (contínuo)")
    ap.add_argument("--wdo", default="WDO$", help="Símbolo do dólar (contínuo)")
    ap.add_argument("--tf", default="15", help="Timeframe: 5,15,30,60")
    ap.add_argument("--bars", type=int, default=25000)
    ap.add_argument("--window", type=int, default=100, help="Janela do beta/z-score (velas)")
    ap.add_argument("--cost", type=float, default=0.10, help="(legado) custo em R do modo z")
    ap.add_argument("--cost-pct", type=float, default=0.0004,
                    help="Custo REAL por perna (fração, ida+volta). 0.0004 = 0.04%%. Total do par ≈ ×(1+beta)")
    args = ap.parse_args()

    if not _mt5_init():
        print("❌ MT5 não inicializou:", mt5.last_error()); sys.exit(1)

    w = _fetch(args.win, args.tf, args.bars)
    d = _fetch(args.wdo, args.tf, args.bars)
    if w is None or d is None:
        print("Sem dados de um dos símbolos (tente --win WINZ26 --wdo WDOU26)"); sys.exit(1)

    df = pd.concat([w.rename("WIN"), d.rename("WDO")], axis=1).dropna()
    if len(df) < 500:
        print("Poucos pontos alinhados entre WIN e WDO"); sys.exit(1)
    lw, ld = np.log(df["WIN"]), np.log(df["WDO"])

    print("═" * 66)
    print(f"  PAR WIN×WDO — tf={args.tf}m · {len(df)} velas alinhadas · custo {args.cost}R")
    print(f"  ({df.index[0]} → {df.index[-1]})")
    print("═" * 66)

    # correlação dos RETORNOS (a relação existe?)
    corr = lw.diff().corr(ld.diff())
    print(f"  Correlação dos retornos WIN×WDO: {corr:+.3f}  "
          + ("(fortemente relacionados — par faz sentido)" if abs(corr) > 0.3
             else "(fraca — arbitragem de par pode não funcionar)"))

    W = args.window
    beta = lw.rolling(W).cov(ld) / ld.rolling(W).var()
    spread = lw - beta * ld
    z = (spread - spread.rolling(W).mean()) / spread.rolling(W).std()

    half = len(df) // 2
    yrs = max(0.1, (df.index[-1] - df.index[0]).days / 365.25)
    print("\n  ── FADEIA O SPREAD — P&L REAL (%) das 2 pernas · walk-forward ──")
    print(f"     custo real: {args.cost_pct*100:.3f}% por perna (ida+volta) · exp = %/trade líquido")
    for thr in (1.5, 2.0, 2.5):
        trades = pairs_trades(z, thr, buffer=1.0, max_hold=int(W))
        rets, idx = real_pnl(trades, df["WIN"], df["WDO"], beta, args.cost_pct)
        r2 = [rets[k] for k in range(len(rets)) if idx[k] >= half]
        print(f"     |z|>{thr}σ : TODO {_fmt_pct(rets, yrs)}")
        print(f"              2ªmet {_fmt_pct(r2, yrs / 2)}")

    print("\n  → 🟢 nas duas linhas (TODO e 2ªmet), com %/ano relevante = edge REAL e tradeável.")
    print("     Aí desenhamos o motor de PARES (short um, long o outro) — o novo B3.")
    mt5.shutdown()
