"""
validar_orderflow.py — VALIDAÇÃO PROFUNDA do setup ORDER_FLOW (crypto/metais).

O ORDER_FLOW foi a única ideia NOVA da Kimi que se repetiu em duas amostras
(M5 0,5 ano e M15 1,4 ano) e passou/quase passou em XAU, BTC e ETH. Aqui ele
é submetido ao teste que a Kimi NÃO fez: separação IN-SAMPLE × OUT-OF-SAMPLE
(anti-overfitting), consistência por bimestre e sensibilidade de parâmetro.

Lógica (delta de fluxo): usa um proxy de delta de volume — quanto do volume
das últimas 10 barras foi comprador vs vendedor. Entra a FAVOR do delta quando:
  • delta normalizado > +0,30 (ou < -0,30)
  • preço fecha acima da VAH (área de valor por VWAP-desvio) — abaixo da VAL p/ venda
  • candle na direção e volume >= 1,5× a média
Saída GERIDA: SL estruturado, TP1 2R (move p/ breakeven), TP2 3R, time-stop.

⚠️ Rodar com o MT5 da ICMarkets aberto (usa MT5_CRYPTO_* do .env).
Uso:  python validar_orderflow.py                 (XAU, BTC, ETH)
      python validar_orderflow.py --symbol XAUUSD
      python validar_orderflow.py --tf 5           (M5, como a Kimi projetou)
Saída: relatório na tela + logs/validar_orderflow_*.txt/.csv
"""
import argparse
import os
import sys
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

from backtest_crypto_pro import mt5_connect, fetch, live_spread, SPREAD_FALLBACK, Tee

# ── Custos (spread cheio no round-trip do CFD) ──────────────────────────────
TICK_SIZE = {"BTCUSD": 0.1, "ETHUSD": 0.01, "XAUUSD": 0.01}

RR_TP1, RR_TP2 = 2.0, 3.0
DELTA_THR      = 0.30     # limiar padrão do delta normalizado
OOS_FRAC       = 0.30     # últimos 30% da série = out-of-sample


def asset_key(symbol):
    s = symbol.upper()
    for k in ("BTCUSD", "ETHUSD", "XAUUSD"):
        if k in s:
            return k
    return s


# ── Indicadores necessários ao ORDER_FLOW ───────────────────────────────────

def add_of(df):
    c, h, l, o, v = df.Close, df.High, df.Low, df.Open, df.Volume
    tr = pd.concat([(h - l), (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    df["atr"] = tr.ewm(alpha=1 / 14, adjust=False).mean()
    df["vol10"] = v.rolling(10).mean()
    rng = (h - l).replace(0, np.nan)
    delta = ((c - l) / rng - 0.5) * 2 * v          # proxy de delta comprador-vendedor
    df["delta_sum10"] = delta.rolling(10).sum()
    df["vol_sum10"] = v.rolling(10).sum()
    vwl = (c * v).rolling(20).sum() / v.rolling(20).sum().replace(0, np.nan)
    dev = ((v * (c - vwl) ** 2).rolling(20).sum() / v.rolling(20).sum().replace(0, np.nan)) ** 0.5
    df["va_vwl"], df["va_dev"] = vwl, dev
    return df


# ── Sinal ORDER_FLOW (mesma lógica do backtest_kimi_real, thr parametrizável) ─

def of_signal(df, i, ts, thr=DELTA_THR):
    if i < 20:
        return None
    r = df.iloc[i]
    vt = r.vol_sum10
    if not vt or vt <= 0 or pd.isna(vt):
        return None
    dn = r.delta_sum10 / vt
    vwl, dev = r.va_vwl, r.va_dev
    if pd.isna(vwl) or pd.isna(dev):
        return None
    vah, val = vwl + dev, vwl - dev
    volok = r.Volume >= (r.vol10 or 0) * 1.5
    if dn > thr and r.Close > vah and r.Close > r.Open and volok:
        sl = min(val, r.Low - 2 * ts); ds = r.Close - sl
        if ds > 0:
            return ("COMPRA", sl, r.Close + RR_TP1 * ds, r.Close + RR_TP2 * ds)
    if dn < -thr and r.Close < val and r.Close < r.Open and volok:
        sl = max(vah, r.High + 2 * ts); ds = sl - r.Close
        if ds > 0:
            return ("VENDA", sl, r.Close - RR_TP1 * ds, r.Close - RR_TP2 * ds)
    return None


# ── Simulador gerido (24h: sem zeragem de dia; time-stop em barras) ─────────

def simulate(df, symbol, spread, thr=DELTA_THR, max_bars=96):
    ts = TICK_SIZE.get(asset_key(symbol), 0.01)
    trades = []
    pos = None
    n = len(df)
    for i in range(210, n):
        if pos:
            row = df.iloc[i]; buy = pos["dir"] == "COMPRA"
            hit_sl = (row.Low <= pos["sl"]) if buy else (row.High >= pos["sl"])
            hit_tp1 = (row.High >= pos["tp1"]) if buy else (row.Low <= pos["tp1"])
            hit_tp2 = (row.High >= pos["tp2"]) if buy else (row.Low <= pos["tp2"])
            gross = None; stopped = True
            if hit_sl:
                gross = ((pos["sl"] - pos["entry"]) / pos["risk"]) if buy else ((pos["entry"] - pos["sl"]) / pos["risk"])
            elif hit_tp2:
                gross = RR_TP2; stopped = False
            elif hit_tp1 and not pos["be"]:
                pos["be"] = True; pos["sl"] = pos["entry"]        # move p/ breakeven
            if gross is None and (i - pos["i"]) >= max_bars:      # time-stop
                px = df.iloc[i].Close
                gross = ((px - pos["entry"]) / pos["risk"]) if buy else ((pos["entry"] - px) / pos["risk"])
                stopped = abs(gross) >= 0
            if gross is not None:
                cost_r = spread / pos["risk"]                     # spread cheio round-trip
                trades.append({"ts": pos["ts"], "dir": pos["dir"],
                               "gross_R": round(gross, 3), "cost_R": round(cost_r, 3),
                               "net_R": round(gross - cost_r, 3)})
                pos = None
            continue
        sig = of_signal(df, i, ts, thr)
        if not sig:
            continue
        d, sl, tp1, tp2 = sig
        entry = float(df.iloc[i].Close)
        risk = abs(entry - sl)
        atr_i = float(df.iloc[i].atr or 0)
        if risk <= 0 or risk < 2 * spread or risk < 0.15 * max(atr_i, 1e-9):
            continue
        pos = {"ts": df.index[i], "i": i, "dir": d, "entry": entry, "sl": sl,
               "tp1": tp1, "tp2": tp2, "risk": risk, "be": False}
    return pd.DataFrame(trades)


# ── Estatísticas ────────────────────────────────────────────────────────────

def stats(t):
    if t is None or t.empty:
        return {"n": 0}
    w = t[t.net_R > 0]; l = t[t.net_R <= 0]
    gw, gl = w.net_R.sum(), -l.net_R.sum()
    eq = t.net_R.cumsum(); dd = (eq - eq.cummax()).min()
    return {"n": len(t), "win": round(len(w) / len(t) * 100, 1),
            "gross": round(t.gross_R.mean(), 3), "cost": round(t.cost_R.mean(), 3),
            "net": round(t.net_R.mean(), 3),
            "PF": round(gw / gl, 2) if gl > 0 else float("inf"),
            "tot": round(t.net_R.sum(), 1), "dd": round(dd, 1)}


def consistency(t):
    if t is None or t.empty:
        return 0
    t2 = t.copy(); t2["p"] = pd.to_datetime(t2["ts"]).dt.to_period("2M").astype(str)
    per = t2.groupby("p")["net_R"].mean()
    return round((per > 0).sum() / len(per) * 100) if len(per) else 0


def _line(tag, s, cons=None):
    if s.get("n", 0) == 0:
        return f"  {tag:<22} (sem trades)"
    c = f" | cons {cons}%" if cons is not None else ""
    return (f"  {tag:<22} n={s['n']:<5} win {s['win']:>5}% | LÍQ {s['net']:+.3f} R | "
            f"PF {s['PF']:<5} | tot {s['tot']:+7.1f} | DD {s['dd']:>7}{c}")


def verdict(s, cons):
    if s.get("n", 0) < 40:
        return "AMOSTRA FRACA"
    if s["net"] >= 0.10 and s["PF"] >= 1.25 and cons >= 55:
        return "🟢 GO"
    if s["net"] > 0.05 and s["PF"] >= 1.15:
        return "🟡 PROMISSOR"
    return "🔴 REPROVADO"


def run_symbol(mt5, sym, tf, bars, ranking):
    """Roda a validação completa de 1 ativo num timeframe. Anexa ao ranking."""
    df = fetch(mt5, sym, tf, bars)
    if df is None or len(df) < 3000:
        print(f"\n!! {sym} M{tf}: sem histórico"); return
    df = add_of(df)
    spread = live_spread(mt5, sym) or SPREAD_FALLBACK.get(sym, 0.0)
    per_year = 96 if tf == 15 else 288
    max_bars = per_year                       # time-stop = 1 dia
    anos = len(df) / per_year / 365
    print(f"\n{'='*70}\n  {sym} · M{tf}: {len(df)} candles (~{anos:.1f} anos) | spread {spread}"
          f"\n  [{df.index[0].date()} → {df.index[-1].date()}]\n{'='*70}")

    t = simulate(df, sym, spread, thr=DELTA_THR, max_bars=max_bars)
    s = stats(t); cons = consistency(t)
    print(_line("PERÍODO COMPLETO", s, cons))

    # ── OUT-OF-SAMPLE: separa por tempo (treino 70% × teste 30%) ──
    if not t.empty:
        t = t.sort_values("ts").reset_index(drop=True)
        cut = int(len(t) * (1 - OOS_FRAC))
        t_in, t_out = t.iloc[:cut], t.iloc[cut:]
        s_in, s_out = stats(t_in), stats(t_out)
        c_in, c_out = consistency(t_in), consistency(t_out)
        print("  " + "-" * 66)
        print(_line("IN-SAMPLE  (70%)", s_in, c_in))
        print(_line("OUT-SAMPLE (30%)", s_out, c_out))
        if s_out.get("n", 0) >= 20:
            date_cut = pd.to_datetime(t_out["ts"].iloc[0]).date()
            sobrevive = s_out["net"] >= 0.08 and s_out["PF"] >= 1.15
            print(f"  → corte em {date_cut} · OUT-OF-SAMPLE "
                  f"{'SEGUROU ✅' if sobrevive else 'CAIU ❌'} "
                  f"(net {s_out['net']:+.3f}, PF {s_out['PF']})")

    # ── Sensibilidade de parâmetro (robustez do limiar de delta) ──
    print("  " + "-" * 66)
    print("  Sensibilidade ao limiar de delta:")
    for thr in (0.25, 0.30, 0.35):
        ts_ = simulate(df, sym, spread, thr=thr, max_bars=max_bars)
        ss = stats(ts_)
        print(f"    thr={thr:.2f}  " + (_line("", ss).strip() if ss.get("n", 0) else "(sem trades)"))

    v = verdict(s, cons)
    print(f"\n  VEREDITO {sym} M{tf}: {v}")
    if not t.empty:
        t.to_csv(f"logs/validar_orderflow_{sym}_M{tf}.csv", index=False)
    ranking.append({"tf": f"M{tf}", "symbol": sym, **s, "cons%": cons, "veredito": v})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default=None, help="um ativo só (padrão: XAU, BTC, ETH)")
    ap.add_argument("--tf", type=int, default=None,
                    help="15 ou 5. Sem este argumento, roda M15 E M5 (tudo num comando).")
    ap.add_argument("--bars", type=int, default=200000)
    args = ap.parse_args()

    os.makedirs("logs", exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    sys.stdout = Tee(f"logs/validar_orderflow_{stamp}.txt")
    print(f"\n{'#'*70}\n# VALIDAÇÃO ORDER_FLOW (out-of-sample) — {datetime.now():%d/%m/%Y %H:%M}\n{'#'*70}")

    mt5 = mt5_connect()
    symbols = [args.symbol.upper()] if args.symbol else ["XAUUSD", "BTCUSD", "ETHUSD"]
    tfs = [args.tf] if args.tf else [15, 5]    # sem --tf: roda os dois
    ranking = []

    for tf in tfs:
        print(f"\n\n{'█'*70}\n█  TIMEFRAME M{tf}\n{'█'*70}")
        for sym in symbols:
            try:
                run_symbol(mt5, sym, tf, args.bars, ranking)
            except Exception as exc:
                print(f"\n!! {sym} M{tf}: erro — {exc}")

    print(f"\n{'#'*70}\n  RESUMO GERAL (todos os timeframes)\n{'#'*70}")
    for r in sorted(ranking, key=lambda x: x.get("net", -9), reverse=True):
        if r.get("n", 0) == 0:
            print(f"  {r['tf']:<4} {r['symbol']:<8} sem trades"); continue
        print(f"  {r['tf']:<4} {r['symbol']:<8} n={int(r['n']):<5} net {r['net']:+.3f} R  "
              f"PF {r['PF']:<5} cons {r['cons%']}%  {r['veredito']}")
    print(f"\n# Critério GO: net ≥ +0,10 R · PF ≥ 1,25 · consistência ≥ 55% · OOS segura")
    print(f"# FIM — envie logs/validar_orderflow_{stamp}.txt para análise")


if __name__ == "__main__":
    main()
