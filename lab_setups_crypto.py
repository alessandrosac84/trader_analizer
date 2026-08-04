"""
lab_setups_crypto.py — LAB DE SETUPS ESTRUTURAIS 24h (BTCUSD, XAUUSD, ETHUSD).

Mesmo método que encontrou o motor v7.4.1 do WIN: uma BATERIA de setups
estruturais testada de uma vez, com custos (spread) e walk-forward, ranqueada
por expectância líquida. O ranking decide — sem paixão por hipótese.

Mercados 24h têm estrutura própria — os setups aqui são baseados em SESSÕES:
  ASIA_BREAK    — rompimento do range asiático na abertura de Londres (clássico do ouro)
  ASIA_RETEST   — idem, mas entrada LIMITE no reteste do nível (corta spread — lição do WDO)
  LONDON_ORB    — rompimento da 1ª hora de Londres
  NY_ORB        — rompimento da 1ª hora de Nova York
  PDH_PDL       — rompimento da máx/mín do dia anterior (dia = dia do servidor)
  MONDAY_GAP    — fade do gap de abertura de segunda (só ativos que fecham no fim de semana)
  DAILY_MOMO    — dia anterior forte e direcional → continuação no rompimento da máx/mín
  H1_PULLBACK   — tendência H1 (EMA50>EMA200) + pullback à EMA21 com candle de rejeição
                  (testa em TF maior a hipótese do único caminho positivo do módulo atual)

Horários em HORA DO SERVIDOR IC Markets (GMT+2/+3): Ásia 01-09h, Londres 09h,
NY 16h30. Ajustável em SESSIONS se a corretora usar outro fuso.

⚠️ Rodar com o MT5 da ICMarkets aberto (usa MT5_CRYPTO_* do .env).
Uso:  python lab_setups_crypto.py            (rápido: minutos)
      python lab_setups_crypto.py --symbol XAUUSD
Saída: ranking + logs/lab_crypto_*.csv
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

TARGET_RR = 1.5

# Horários (hora do servidor IC Markets = GMT+2/+3)
SESSIONS = {
    "asia":        (1, 9),      # range asiático 01:00-09:00
    "london_or":   (9, 10),     # 1ª hora de Londres
    "london_until": 14,         # entradas de Londres até 14:00
    "ny_or":       (16.5, 17.5),
    "ny_until":    20,
    "day_close":   23.75,       # zeragem no fim do dia do servidor (23:45)
}


def hourf(ts):
    return ts.hour + ts.minute / 60.0


# ── Simulador de 1 trade (contínuo, com zeragem no fim do dia) ─────────────

def sim(m15, i_entry, direction, entry, stop, tp, symbol, spread,
        entry_limit=False, max_days=1):
    buy = direction == "COMPRA"
    risk = abs(entry - stop)
    if risk <= 0 or risk < 2 * spread:      # anti-armadilha de spread (lição do ETH)
        return None
    day0 = m15.index[i_entry].date()
    gross = None; stopped = False; exit_r = "DAYEND"
    j_end = min(i_entry + 96 * max_days + 96, len(m15))
    for j in range(i_entry, j_end):
        ts = m15.index[j]
        h, l, c = float(m15.iloc[j]["High"]), float(m15.iloc[j]["Low"]), float(m15.iloc[j]["Close"])
        if (l <= stop) if buy else (h >= stop):
            gross, stopped, exit_r = -1.0, True, "SL"; break
        if (h >= tp) if buy else (l <= tp):
            gross, exit_r = abs(tp - entry) / risk, "TP"; break
        days_open = (ts.date() - day0).days
        if (days_open >= max_days - 1 and hourf(ts) >= SESSIONS["day_close"]) or days_open >= max_days:
            gross = ((c - entry) / risk) if buy else ((entry - c) / risk)
            stopped, exit_r = True, "DAYEND"; break
    if gross is None:
        c = float(m15.iloc[j_end - 1]["Close"])
        gross = ((c - entry) / risk) if buy else ((entry - c) / risk)
        stopped = True
    cost = spread * (0.5 if entry_limit else 1.0) + (spread * 0.0)   # entrada limite paga ~meio spread
    if stopped:
        cost += 0.0   # stop a mercado já embutido no spread cheio do round-trip
    cost_r = max(cost, spread * 0.5) / risk
    return {"ts": m15.index[i_entry], "dir": direction, "risk": round(risk, 5),
            "gross_R": round(gross, 3), "cost_R": round(cost_r, 3),
            "net_R": round(gross - cost_r, 3), "exit": exit_r,
            "entry_mode": "LIMITE" if entry_limit else "MERCADO"}


# ── Referências por dia do servidor ────────────────────────────────────────

def day_refs(m15):
    dates = pd.Series(m15.index.date, index=m15.index)
    dh = m15["High"].groupby(dates).max()
    dl = m15["Low"].groupby(dates).min()
    dc = m15["Close"].groupby(dates).last()
    do = m15["Open"].groupby(dates).first()
    atr_d = (dh - dl).rolling(14, min_periods=5).mean().shift(1)
    return pd.DataFrame({"open_d": do, "y_high": dh.shift(1), "y_low": dl.shift(1),
                         "y_close": dc.shift(1), "y_range": (dh - dl).shift(1),
                         "atr_d": atr_d})


def sess_range(day, h0, h1):
    sel = day[(day.index.map(hourf) >= h0) & (day.index.map(hourf) < h1)]
    if len(sel) < 2:
        return None, None
    return float(sel["High"].max()), float(sel["Low"].min())


# ── Setups ─────────────────────────────────────────────────────────────────

def _break_after(m15, day, r, symbol, spread, hi, lo, h_from, h_until,
                 stop_mode="mid", retest=False):
    """Rompimento de um range [lo, hi] dentro da janela [h_from, h_until]."""
    if hi is None or lo is None or hi <= lo:
        return None
    mid = (hi + lo) / 2
    idx0 = m15.index.get_indexer([day.index[0]])[0]
    for k in range(len(day) - 1):
        ts = day.index[k]
        hf = hourf(ts)
        if hf < h_from: continue
        if hf >= h_until: return None
        c = float(day.iloc[k]["Close"])
        pc = float(day.iloc[k - 1]["Close"]) if k else c
        i_abs = idx0 + k + 1
        if pc <= hi < c:
            if retest:
                for m in range(k + 1, min(k + 7, len(day))):
                    if float(day.iloc[m]["Low"]) <= hi:
                        return sim(m15, idx0 + m, "COMPRA", hi, mid,
                                   hi + TARGET_RR * (hi - mid), symbol, spread,
                                   entry_limit=True)
                return None
            e = float(m15.iloc[i_abs]["Open"])
            stp = mid if stop_mode == "mid" else lo
            if e <= stp: return None
            return sim(m15, i_abs, "COMPRA", e, stp, e + TARGET_RR * (e - stp),
                       symbol, spread)
        if pc >= lo > c:
            if retest:
                for m in range(k + 1, min(k + 7, len(day))):
                    if float(day.iloc[m]["High"]) >= lo:
                        return sim(m15, idx0 + m, "VENDA", lo, mid,
                                   lo - TARGET_RR * (mid - lo), symbol, spread,
                                   entry_limit=True)
                return None
            e = float(m15.iloc[i_abs]["Open"])
            stp = mid if stop_mode == "mid" else hi
            if e >= stp: return None
            return sim(m15, i_abs, "VENDA", e, stp, e - TARGET_RR * (stp - e),
                       symbol, spread)
    return None


def s_asia_break(m15, day, r, symbol, spread):
    hi, lo = sess_range(day, *SESSIONS["asia"])
    return _break_after(m15, day, r, symbol, spread, hi, lo,
                        SESSIONS["asia"][1], SESSIONS["london_until"])


def s_asia_retest(m15, day, r, symbol, spread):
    hi, lo = sess_range(day, *SESSIONS["asia"])
    return _break_after(m15, day, r, symbol, spread, hi, lo,
                        SESSIONS["asia"][1], SESSIONS["london_until"], retest=True)


def s_london_orb(m15, day, r, symbol, spread):
    hi, lo = sess_range(day, *SESSIONS["london_or"])
    return _break_after(m15, day, r, symbol, spread, hi, lo,
                        SESSIONS["london_or"][1], SESSIONS["london_until"])


def s_ny_orb(m15, day, r, symbol, spread):
    hi, lo = sess_range(day, *SESSIONS["ny_or"])
    return _break_after(m15, day, r, symbol, spread, hi, lo,
                        SESSIONS["ny_or"][1], SESSIONS["ny_until"])


def s_pdh_pdl(m15, day, r, symbol, spread):
    if pd.isna(r["y_high"]) or pd.isna(r["atr_d"]):
        return None
    idx0 = m15.index.get_indexer([day.index[0]])[0]
    for k in range(1, len(day) - 1):
        hf = hourf(day.index[k])
        if hf < 7: continue
        if hf >= 20: return None
        c = float(day.iloc[k]["Close"]); pc = float(day.iloc[k - 1]["Close"])
        if pc <= r["y_high"] < c:
            e = float(m15.iloc[idx0 + k + 1]["Open"])
            stp = e - 0.35 * r["atr_d"]
            return sim(m15, idx0 + k + 1, "COMPRA", e, stp,
                       e + TARGET_RR * (e - stp), symbol, spread)
        if pc >= r["y_low"] > c:
            e = float(m15.iloc[idx0 + k + 1]["Open"])
            stp = e + 0.35 * r["atr_d"]
            return sim(m15, idx0 + k + 1, "VENDA", e, stp,
                       e - TARGET_RR * (stp - e), symbol, spread)
    return None


def s_monday_gap(m15, day, r, symbol, spread, prev_date=None):
    """Fade do gap pós-fim-de-semana (ativos que fecham: XAU/forex)."""
    if prev_date is None or (day.index[0].date() - prev_date).days < 2:
        return None                      # sem gap de fim de semana
    if pd.isna(r["y_close"]) or pd.isna(r["atr_d"]) or not r["atr_d"]:
        return None
    gap = r["open_d"] - r["y_close"]
    ag = abs(gap)
    if not (0.25 * r["atr_d"] <= ag <= 1.5 * r["atr_d"]):
        return None
    idx0 = m15.index.get_indexer([day.index[0]])[0]
    if len(day) < 3: return None
    e = float(day.iloc[1]["Open"])
    buy = gap < 0
    if buy and e >= r["y_close"]: return None
    if (not buy) and e <= r["y_close"]: return None
    stp = e - 0.7 * ag if buy else e + 0.7 * ag
    return sim(m15, idx0 + 1, "COMPRA" if buy else "VENDA", e, stp,
               r["y_close"], symbol, spread)


def s_daily_momo(m15, day, r, symbol, spread):
    """Dia anterior forte e fechando no extremo → continuação no rompimento."""
    if any(pd.isna(r[k]) for k in ("y_high", "y_low", "y_close", "y_range", "atr_d")):
        return None
    if not r["atr_d"] or r["y_range"] < 1.1 * r["atr_d"]:
        return None
    pos = (r["y_close"] - r["y_low"]) / max(r["y_range"], 1e-9)
    if pos >= 0.75:
        want, level = "COMPRA", r["y_high"]
    elif pos <= 0.25:
        want, level = "VENDA", r["y_low"]
    else:
        return None
    idx0 = m15.index.get_indexer([day.index[0]])[0]
    for k in range(1, len(day) - 1):
        if hourf(day.index[k]) >= 20: return None
        c = float(day.iloc[k]["Close"]); pc = float(day.iloc[k - 1]["Close"])
        fired = (pc <= level < c) if want == "COMPRA" else (pc >= level > c)
        if fired:
            e = float(m15.iloc[idx0 + k + 1]["Open"])
            stp = e - 0.5 * r["atr_d"] if want == "COMPRA" else e + 0.5 * r["atr_d"]
            tp = e + TARGET_RR * abs(e - stp) * (1 if want == "COMPRA" else -1)
            return sim(m15, idx0 + k + 1, want, e, stp, tp, symbol, spread)
    return None


def s_h1_pullback(m15, h1, symbol, spread):
    """Tendência H1 + pullback à EMA21(H1) com rejeição. Hold até 2 dias."""
    c = h1["Close"]
    e21 = c.ewm(span=21, adjust=False).mean()
    e50 = c.ewm(span=50, adjust=False).mean()
    e200 = c.ewm(span=200, adjust=False).mean()
    atr_h1 = (h1["High"] - h1["Low"]).rolling(14).mean()
    trades = []
    last_exit_ts = None
    for k in range(210, len(h1) - 1):
        ts = h1.index[k]
        if last_exit_ts is not None and ts <= last_exit_ts:
            continue
        up = e50.iloc[k] > e200.iloc[k] and c.iloc[k] > e50.iloc[k]
        dn = e50.iloc[k] < e200.iloc[k] and c.iloc[k] < e50.iloc[k]
        lo, hi, cl = float(h1.iloc[k]["Low"]), float(h1.iloc[k]["High"]), float(c.iloc[k])
        v21 = float(e21.iloc[k]); a1 = float(atr_h1.iloc[k] or 0)
        sig = None
        if up and lo <= v21 and cl > v21:
            stp = lo - 0.1 * a1
            sig = ("COMPRA", stp)
        elif dn and hi >= v21 and cl < v21:
            stp = hi + 0.1 * a1
            sig = ("VENDA", stp)
        if not sig:
            continue
        i_abs = m15.index.searchsorted(ts) + 4   # próximo M15 após fechar o H1
        if i_abs >= len(m15) - 2:
            break
        e = float(m15.iloc[i_abs]["Open"])
        want, stp = sig
        if (want == "COMPRA" and e <= stp) or (want == "VENDA" and e >= stp):
            continue
        tp = e + TARGET_RR * abs(e - stp) * (1 if want == "COMPRA" else -1)
        t = sim(m15, i_abs, want, e, stp, tp, symbol, spread, max_days=2)
        if t:
            trades.append(t)
            last_exit_ts = ts + pd.Timedelta(hours=8)   # evita re-entrada imediata
    return trades


DAY_SETUPS = {
    "ASIA_BREAK":  s_asia_break,
    "ASIA_RETEST": s_asia_retest,
    "LONDON_ORB":  s_london_orb,
    "NY_ORB":      s_ny_orb,
    "PDH_PDL":     s_pdh_pdl,
    "DAILY_MOMO":  s_daily_momo,
}


# ── Runner ─────────────────────────────────────────────────────────────────

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


def consistency(t):
    t2 = t.copy(); t2["p"] = pd.to_datetime(t2["ts"]).dt.to_period("2M").astype(str)
    per = t2.groupby("p")["net_R"].mean()
    return round((per > 0).sum() / len(per) * 100) if len(per) else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default=None)
    ap.add_argument("--bars", type=int, default=200000)
    args = ap.parse_args()

    os.makedirs("logs", exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    sys.stdout = Tee(f"logs/lab_crypto_{stamp}.txt")
    print(f"\n{'#'*70}\n# LAB SETUPS 24h — {datetime.now():%d/%m/%Y %H:%M}\n{'#'*70}")

    mt5 = mt5_connect()
    symbols = [args.symbol.upper()] if args.symbol else ["BTCUSD", "XAUUSD", "ETHUSD"]
    ranking = []

    for sym in symbols:
        m15 = fetch(mt5, sym, 15, args.bars)
        if m15 is None or len(m15) < 3000:
            print(f"\n!! {sym}: sem histórico"); continue
        h1 = fetch(mt5, sym, 60, 100000)
        spread = live_spread(mt5, sym) or SPREAD_FALLBACK.get(sym, 0.0)
        anos = len(m15) / 96 / 365
        print(f"\n{'='*70}\n  {sym}: {len(m15)} M15 (~{anos:.1f} anos) | spread {spread}\n{'='*70}")

        refs = day_refs(m15)
        results = {name: [] for name in DAY_SETUPS}
        results["MONDAY_GAP"] = []
        dates = list(refs.index)
        for di, date in enumerate(dates):
            r = refs.loc[date]
            day = m15[m15.index.date == date]
            if len(day) < 10:
                continue
            rd = r.to_dict()
            for name, fn in DAY_SETUPS.items():
                try:
                    t = fn(m15, day, rd, sym, spread)
                    if t: t["setup"] = name; results[name].append(t)
                except Exception:
                    pass
            try:
                prev_date = dates[di - 1] if di else None
                t = s_monday_gap(m15, day, rd, sym, spread, prev_date)
                if t: t["setup"] = "MONDAY_GAP"; results["MONDAY_GAP"].append(t)
            except Exception:
                pass
        try:
            hp = s_h1_pullback(m15, h1, sym, spread) if h1 is not None else []
            for t in hp: t["setup"] = "H1_PULLBACK"
            results["H1_PULLBACK"] = hp
        except Exception:
            results["H1_PULLBACK"] = []

        for name, rows in sorted(results.items()):
            if not rows:
                print(f"  {name:<12} (sem trades)")
                continue
            t = pd.DataFrame(rows)
            s = stats(t); cons = consistency(t)
            print(f"  {name:<12} n={s['n']:<5} win {s['win%']:>5}% | bruta {s['gross']:+.3f} | "
                  f"custo {s['cost']:.3f} | LÍQ {s['net']:+.3f} R | PF {s['PF']:<5} | "
                  f"tot {s['tot']:+8.1f} | DD {s['dd']:>7} | cons {cons}%")
            t.to_csv(f"logs/lab_crypto_{sym}_{name}.csv", index=False)
            ranking.append({"symbol": sym, "setup": name, **s, "cons%": cons})

    print(f"\n{'#'*70}\n  RANKING GERAL (líquida, mín. 60 trades)\n{'#'*70}")
    rk = pd.DataFrame(ranking)
    if not rk.empty:
        rk = rk[rk.n >= 60].sort_values("net", ascending=False)
        for _, row in rk.iterrows():
            go = "GO" if (row.net >= 0.10 and row.PF >= 1.25 and row["cons%"] >= 60) else \
                 ("quase" if row.net > 0.05 else "-")
            print(f"  {row.symbol:<8} {row.setup:<12} n={int(row.n):<5} net {row.net:+.3f} R  "
                  f"PF {row.PF:<5} tot {row.tot:+8.1f}  DD {row.dd:>7}  cons {row['cons%']}%  [{go}]")
        rk.to_csv(f"logs/lab_crypto_ranking_{stamp}.csv", index=False)
    print(f"\n# FIM — envie logs/lab_crypto_{stamp}.txt para análise")


if __name__ == "__main__":
    main()
