"""
lab_win_extra.py — Caça a NOVOS setups para o WIN (expandir o portfólio v7).

O WIN v7.4.1 já tem 2 edges provados (GAP_FADE + ORB). Um sistema robusto vive
de VÁRIOS edges pequenos somados. Aqui testamos 8 NOVAS hipóteses específicas
do índice na B3, na mesma régua dos outros labs (custos da nota Santander +
walk-forward por bimestre). O ranking decide — o que passar entra no portfólio.

Novos setups (todos day-trade, zeragem 16:55):
  VWAP_FADE    — dia de range: preço estica 2σ+ da VWAP com RSI extremo → volta à VWAP
  FAILED_ORB   — rompimento da 1ª hora que FALHA (volta pra dentro) → opera a reversão
  ORB_2H       — rompimento do range da 2ª hora (10:00-11:00)
  TREND_PB     — dia de tendência: pullback à EMA9 com candle de rejeição, a favor
  PDC_REACT    — reação no fechamento de ontem (nível-ímã do intraday)
  PM_BREAK     — rompimento de range da tarde (janela B, 14:00-16:00)
  GAP_GO       — gap GRANDE (>1.2 ATR) que segura → segue o gap (oposto do fade)
  LUNCH_REV    — reversão do extremo da manhã após a janela morta do almoço

⚠️ MT5 (XP/B3) aberto. Use o contínuo com aspas simples no PowerShell.
Uso:  python lab_win_extra.py                 # WIN$D
      python lab_win_extra.py --symbol 'WIN$D'
Saída: ranking + logs/lab_winx_*.csv
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

from backtest_pro import fetch_mt5, _stats
from services.trading_costs import fees_pts, _cfg as _cost_cfg

TARGET_RR = 1.5
EOD_MIN   = 16 * 60 + 55


def _mod(ts):
    return ts.hour * 60 + ts.minute


def build_refs(df15):
    dates = pd.Series(df15.index.date, index=df15.index)
    dh = df15["High"].groupby(dates).max()
    dl = df15["Low"].groupby(dates).min()
    dc = df15["Close"].groupby(dates).last()
    do = df15["Open"].groupby(dates).first()
    atr_d = (dh - dl).rolling(14, min_periods=5).mean().shift(1)
    return pd.DataFrame({"open_d": do, "y_high": dh.shift(1), "y_low": dl.shift(1),
                         "y_close": dc.shift(1), "atr_d": atr_d})


def day_of(df15, date):
    return df15[df15.index.date == date]


def _atr(day, n=14):
    h, l, c = day["High"], day["Low"], day["Close"]
    tr = pd.concat([(h - l), (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(n, min_periods=3).mean()


def sim(day, i_entry, direction, entry, stop, tp, symbol, entry_limit=False):
    buy = direction == "COMPRA"
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    gross = None; stopped = False; exit_r = "EOD"
    for j in range(i_entry, len(day)):
        h, l, c = float(day.iloc[j]["High"]), float(day.iloc[j]["Low"]), float(day.iloc[j]["Close"])
        m = _mod(day.index[j])
        if (l <= stop) if buy else (h >= stop):
            gross, stopped, exit_r = -1.0, True, "SL"; break
        if tp is not None and ((h >= tp) if buy else (l <= tp)):
            gross, exit_r = abs(tp - entry) / risk, "TP"; break
        if m >= EOD_MIN:
            gross = ((c - entry) / risk) if buy else ((entry - c) / risk)
            stopped, exit_r = True, "EOD"; break
    if gross is None:
        c = float(day.iloc[-1]["Close"])
        gross = ((c - entry) / risk) if buy else ((entry - c) / risk)
        stopped = True
    cfg = _cost_cfg(symbol); ts_ = cfg["tick_size"]
    cost = fees_pts(symbol) + (0 if entry_limit else ts_) + (ts_ if stopped else 0)
    return {"dir": direction, "risk_pts": round(risk, 1), "gross_R": round(gross, 3),
            "cost_R": round(cost / risk, 3), "net_R": round(gross - cost / risk, 3),
            "exit": exit_r}


def _vwap(day):
    tp = (day["High"] + day["Low"] + day["Close"]) / 3
    v = day["Volume"].clip(lower=1)
    vwap = (tp * v).cumsum() / v.cumsum()
    var = ((tp - vwap) ** 2 * v).cumsum() / v.cumsum()
    return vwap, np.sqrt(var)


def _rsi(close, n=14):
    d = close.diff()
    up = d.clip(lower=0).rolling(n).mean()
    dn = (-d.clip(upper=0)).rolling(n).mean()
    return 100 - 100 / (1 + up / dn.replace(0, 1e-9))


# ── Setups ──────────────────────────────────────────────────────────────────

def s_vwap_fade(day, r, symbol):
    if len(day) < 12: return None
    vwap, sig = _vwap(day); rsi = _rsi(day["Close"])
    atr = _atr(day)
    for j in range(6, len(day) - 1):
        m = _mod(day.index[j])
        if m < 10 * 60 or m >= 15 * 60: continue
        c = float(day.iloc[j]["Close"]); v = float(vwap.iloc[j]); s = float(sig.iloc[j])
        rv = float(rsi.iloc[j]) if not pd.isna(rsi.iloc[j]) else 50
        a = float(atr.iloc[j]) if not pd.isna(atr.iloc[j]) else 0
        if s <= 0 or a <= 0: continue
        # muito acima da VWAP + sobrecomprado → vende de volta à VWAP
        if c >= v + 2 * s and rv >= 70:
            e = float(day.iloc[j + 1]["Open"]); stp = e + 1.0 * a
            if (e - v) >= TARGET_RR * (stp - e):
                return sim(day, j + 1, "VENDA", e, stp, v, symbol)
        if c <= v - 2 * s and rv <= 30:
            e = float(day.iloc[j + 1]["Open"]); stp = e - 1.0 * a
            if (v - e) >= TARGET_RR * (e - stp):
                return sim(day, j + 1, "COMPRA", e, stp, v, symbol)
    return None


def s_failed_orb(day, r, symbol):
    if len(day) < 8 or pd.isna(r["atr_d"]): return None
    orh = float(day.iloc[:4]["High"].max()); orl = float(day.iloc[:4]["Low"].min())
    for j in range(4, len(day) - 2):
        m = _mod(day.index[j])
        if m >= 13 * 60: return None
        c = float(day.iloc[j]["Close"]); pc = float(day.iloc[j - 1]["Close"])
        # rompeu para cima e o candle seguinte fecha de volta pra dentro → vende
        if pc <= orh < c:
            c2 = float(day.iloc[j + 1]["Close"])
            if c2 < orh:
                e = float(day.iloc[j + 2]["Open"]); stp = float(day.iloc[j]["High"])
                if stp > e:
                    return sim(day, j + 2, "VENDA", e, stp, e - TARGET_RR * (stp - e), symbol)
            return None
        if pc >= orl > c:
            c2 = float(day.iloc[j + 1]["Close"])
            if c2 > orl:
                e = float(day.iloc[j + 2]["Open"]); stp = float(day.iloc[j]["Low"])
                if e > stp:
                    return sim(day, j + 2, "COMPRA", e, stp, e + TARGET_RR * (e - stp), symbol)
            return None
    return None


def s_orb_2h(day, r, symbol):
    # range da 2ª hora: candles das 10:00-11:00
    idx = [k for k in range(len(day)) if 10 * 60 <= _mod(day.index[k]) < 11 * 60]
    if len(idx) < 3 or len(day) < idx[-1] + 3: return None
    seg = day.iloc[idx]
    orh, orl = float(seg["High"].max()), float(seg["Low"].min()); mid = (orh + orl) / 2
    for j in range(idx[-1] + 1, len(day) - 1):
        if _mod(day.index[j]) >= 15 * 60: return None
        c = float(day.iloc[j]["Close"]); pc = float(day.iloc[j - 1]["Close"])
        if pc <= orh < c:
            e = float(day.iloc[j + 1]["Open"])
            if e > mid: return sim(day, j + 1, "COMPRA", e, mid, e + TARGET_RR * (e - mid), symbol)
        if pc >= orl > c:
            e = float(day.iloc[j + 1]["Open"])
            if mid > e: return sim(day, j + 1, "VENDA", e, mid, e - TARGET_RR * (mid - e), symbol)
    return None


def s_trend_pb(day, r, symbol):
    if len(day) < 12 or pd.isna(r["open_d"]): return None
    ema9 = day["Close"].ewm(span=9, adjust=False).mean()
    atr = _atr(day)
    up = float(day.iloc[3]["Close"]) > r["open_d"]   # viés do dia pela abertura
    for j in range(6, len(day) - 1):
        m = _mod(day.index[j])
        if m < 10 * 60 or m >= 15 * 60: continue
        e9 = float(ema9.iloc[j]); a = float(atr.iloc[j]) if not pd.isna(atr.iloc[j]) else 0
        lo, hi, c = float(day.iloc[j]["Low"]), float(day.iloc[j]["High"]), float(day.iloc[j]["Close"])
        if a <= 0: continue
        # compra: dia de alta, testou a EMA9 por baixo e fechou acima (rejeição)
        if up and lo <= e9 and c > e9:
            e = float(day.iloc[j + 1]["Open"]); stp = lo - 0.1 * a
            if e > stp: return sim(day, j + 1, "COMPRA", e, stp, e + TARGET_RR * (e - stp), symbol)
        if (not up) and hi >= e9 and c < e9:
            e = float(day.iloc[j + 1]["Open"]); stp = hi + 0.1 * a
            if stp > e: return sim(day, j + 1, "VENDA", e, stp, e - TARGET_RR * (stp - e), symbol)
    return None


def s_pdc_react(day, r, symbol):
    if pd.isna(r["y_close"]) or pd.isna(r["atr_d"]) or not r["atr_d"]: return None
    yc = float(r["y_close"]); atr = _atr(day)
    for j in range(4, len(day) - 1):
        m = _mod(day.index[j])
        if m < 10 * 60 or m >= 15 * 60: continue
        lo, hi, c, o = (float(day.iloc[j][k]) for k in ("Low", "High", "Close", "Open"))
        a = float(atr.iloc[j]) if not pd.isna(atr.iloc[j]) else 0
        if a <= 0: continue
        # testou o fechamento de ontem por baixo e reagiu pra cima (defesa) → compra
        if lo <= yc <= c and c > o and (o < yc or lo < yc):
            e = float(day.iloc[j + 1]["Open"]); stp = lo - 0.15 * a
            if e > stp: return sim(day, j + 1, "COMPRA", e, stp, e + TARGET_RR * (e - stp), symbol)
        if hi >= yc >= c and c < o and (o > yc or hi > yc):
            e = float(day.iloc[j + 1]["Open"]); stp = hi + 0.15 * a
            if stp > e: return sim(day, j + 1, "VENDA", e, stp, e - TARGET_RR * (stp - e), symbol)
    return None


def s_pm_break(day, r, symbol):
    # range da janela morta 12-14h; rompimento na tarde (14:00-16:00)
    idx = [k for k in range(len(day)) if 12 * 60 <= _mod(day.index[k]) < 14 * 60]
    if len(idx) < 4: return None
    seg = day.iloc[idx]
    orh, orl = float(seg["High"].max()), float(seg["Low"].min()); mid = (orh + orl) / 2
    start = idx[-1] + 1
    for j in range(start, len(day) - 1):
        m = _mod(day.index[j])
        if m < 14 * 60: continue
        if m >= 16 * 60: return None
        c = float(day.iloc[j]["Close"]); pc = float(day.iloc[j - 1]["Close"])
        if pc <= orh < c:
            e = float(day.iloc[j + 1]["Open"])
            if e > mid: return sim(day, j + 1, "COMPRA", e, mid, e + TARGET_RR * (e - mid), symbol)
        if pc >= orl > c:
            e = float(day.iloc[j + 1]["Open"])
            if mid > e: return sim(day, j + 1, "VENDA", e, mid, e - TARGET_RR * (mid - e), symbol)
    return None


def s_gap_go(day, r, symbol):
    if pd.isna(r["y_close"]) or pd.isna(r["atr_d"]) or not r["atr_d"] or len(day) < 4: return None
    gap = r["open_d"] - r["y_close"]; ag = abs(gap)
    if ag < 1.2 * r["atr_d"]: return None       # só gap GRANDE
    o0, c0 = float(day.iloc[0]["Open"]), float(day.iloc[0]["Close"])
    buy = gap > 0
    if buy and c0 <= o0: return None            # 1º candle tem de confirmar a direção
    if (not buy) and c0 >= o0: return None
    e = float(day.iloc[1]["Open"])
    stp = float(day.iloc[0]["Low"]) if buy else float(day.iloc[0]["High"])
    risk = abs(e - stp)
    if risk <= 0: return None
    tp = e + TARGET_RR * risk if buy else e - TARGET_RR * risk
    return sim(day, 1, "COMPRA" if buy else "VENDA", e, stp, tp, symbol)


def s_lunch_rev(day, r, symbol):
    # reversão do extremo da manhã: pega o extremo até 12h; se a tarde volta, opera
    morn = [k for k in range(len(day)) if _mod(day.index[k]) < 12 * 60]
    if len(morn) < 8 or pd.isna(r["atr_d"]) or not r["atr_d"]: return None
    seg = day.iloc[morn]
    hi_m, lo_m = float(seg["High"].max()), float(seg["Low"].min())
    open_d = float(day.iloc[0]["Open"])
    atr = _atr(day)
    for j in range(morn[-1] + 1, len(day) - 1):
        m = _mod(day.index[j])
        if m < 14 * 60: continue
        if m >= 16 * 60: return None
        c = float(day.iloc[j]["Close"]); pc = float(day.iloc[j - 1]["Close"])
        a = float(atr.iloc[j]) if not pd.isna(atr.iloc[j]) else 0
        if a <= 0: continue
        # manhã fez topo bem acima da abertura e a tarde perde a mínima do range → vende reversão
        if (hi_m - open_d) > 0.8 * r["atr_d"] and pc >= lo_m > c:
            e = float(day.iloc[j + 1]["Open"]); stp = hi_m
            if stp > e and (stp - e) < 2.5 * a:
                return sim(day, j + 1, "VENDA", e, stp, e - TARGET_RR * (stp - e), symbol)
        if (open_d - lo_m) > 0.8 * r["atr_d"] and pc <= hi_m < c:
            e = float(day.iloc[j + 1]["Open"]); stp = lo_m
            if e > stp and (e - stp) < 2.5 * a:
                return sim(day, j + 1, "COMPRA", e, stp, e + TARGET_RR * (e - stp), symbol)
    return None


SETUPS = {
    "VWAP_FADE":  s_vwap_fade,
    "FAILED_ORB": s_failed_orb,
    "ORB_2H":     s_orb_2h,
    "TREND_PB":   s_trend_pb,
    "PDC_REACT":  s_pdc_react,
    "PM_BREAK":   s_pm_break,
    "GAP_GO":     s_gap_go,
    "LUNCH_REV":  s_lunch_rev,
}


def consistency(t):
    t2 = t.copy(); t2["p"] = pd.to_datetime(t2["date"]).dt.to_period("2M").astype(str)
    per = t2.groupby("p")["net_R"].mean()
    return round((per > 0).sum() / len(per) * 100) if len(per) else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default=None)
    ap.add_argument("--bars", type=int, default=200000)
    args = ap.parse_args()

    sym = args.symbol or "WIN$D"
    if sym.upper() in ("WIN", "WDO"):
        sym = sym.upper() + "$D"
    os.makedirs("logs", exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")

    df = fetch_mt5(sym, "15", args.bars)
    if df is None:
        print("sem dados"); return
    print(f"\n{'='*70}\n  {sym}: {len(df)} candles ({df.index[0].date()} -> {df.index[-1].date()})\n{'='*70}")
    refs = build_refs(df)

    results = {k: [] for k in SETUPS}
    for date in refs.index:
        r = refs.loc[date]
        if any(pd.isna(r[k]) for k in ("open_d", "atr_d")):
            continue
        day = day_of(df, date)
        if len(day) < 10:
            continue
        rd = r.to_dict()
        for name, fn in SETUPS.items():
            try:
                t = fn(day, rd, sym)
                if t: t["date"] = date; results[name].append(t)
            except Exception:
                pass

    ranking = []
    for name, rows in sorted(results.items()):
        if not rows:
            print(f"  {name:<12} (sem trades)"); continue
        t = pd.DataFrame(rows)
        s = _stats(t); cons = consistency(t)
        print(f"  {name:<12} n={s['n']:<5} win {s['win%']:>5}% | bruta {s['exp_gross_R']:+.3f} | "
              f"custo {s['cost_R_avg']:.3f} | LÍQ {s['exp_net_R']:+.3f} R | PF {s['PF']:<5} | "
              f"tot {s['total_net_R']:+7.1f} | DD {s['maxDD_R']:>6} | cons {cons}%")
        t.to_csv(f"logs/lab_winx_{name}.csv", index=False)
        ranking.append({"setup": name, **s, "cons%": cons})

    print(f"\n{'#'*70}\n  RANKING (líquida, mín. 80 trades) — candidatos ao portfólio WIN\n{'#'*70}")
    rk = pd.DataFrame(ranking)
    if not rk.empty:
        rk = rk[rk.n >= 80].sort_values("exp_net_R", ascending=False)
        for _, row in rk.iterrows():
            go = "GO" if (row.exp_net_R >= 0.10 and row.PF >= 1.25 and row["cons%"] >= 55) else \
                 ("quase" if row.exp_net_R > 0.05 else "-")
            print(f"  {row.setup:<12} n={int(row.n):<5} net {row.exp_net_R:+.3f} R  PF {row.PF:<5} "
                  f"tot {row.total_net_R:+7.1f}  DD {row.maxDD_R:>6}  cons {row['cons%']}%  [{go}]")
    print(f"\n  Referência (v7 atual): GAP_FADE +0.172 R | ORB +0.189 R")
    print(f"  Detalhes por setup em logs/lab_winx_*.csv")


if __name__ == "__main__":
    main()
