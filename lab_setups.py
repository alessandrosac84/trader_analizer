"""
lab_setups.py — LABORATÓRIO DE SETUPS ESTRUTURAIS (WIN/WDO, 6,6 anos, custos reais).

Por que este laboratório existe: o backtest v2 provou que score de indicadores
não tem edge nesses ativos, e que o único sobrevivente (ORB) é um setup de
ESTRUTURA de mercado. Este script testa uma BATERIA de setups estruturais
clássicos da B3 de uma vez, na mesma régua (custos da nota + walk-forward),
e ranqueia o que sobrevive. Sem paixão por hipótese: o ranking decide.

Setups testados (todos day-trade, zeragem 16:55):
  GAP_FADE     — gap moderado contra: opera o fechamento do gap (alvo = fech. de ontem)
  GAP_GO       — gap forte com abertura fora do range de ontem: segue o gap
  ORB30        — rompimento do range dos primeiros 30min
  ORB60        — rompimento do range da 1ª hora (o atual campeão, como referência)
  ORB60_RETEST — rompe a 1ª hora e ENTRA NO RETESTE com ordem LIMITE (corta custo — chave p/ WDO)
  PDH_PDL      — rompimento da máxima/mínima do dia anterior
  FH_MOMENTUM  — 1ª hora forte e direcional → entra e SEGURA até o fechamento (dia de tendência)
  VWAP_REJECT  — tendência + pullback à VWAP com candle de REJEIÇÃO (não compra a queda; espera a defesa do nível)
  LATE_BREAK   — rompimento do range do dia após 15h em dia comprimido (momentum de fechamento)

Uso (MT5 aberto):  python lab_setups.py            # roda tudo (rápido: minutos)
                   python lab_setups.py --symbol 'WIN$D'
Saída: ranking geral + detalhe por setup + CSVs em logs/lab_*.csv
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

from services.trading_costs import fees_pts, _cfg as _cost_cfg
from backtest_pro import fetch_mt5, _stats

EOD_MIN = 16 * 60 + 55
TARGET_RR = 1.5


def _mod(ts):
    return ts.hour * 60 + ts.minute


# ── Referências diárias ────────────────────────────────────────────────────

def build_refs(df15):
    dates = pd.Series(df15.index.date, index=df15.index)
    dh = df15["High"].groupby(dates).max()
    dl = df15["Low"].groupby(dates).min()
    dc = df15["Close"].groupby(dates).last()
    do = df15["Open"].groupby(dates).first()
    atr_d = (dh - dl).rolling(14, min_periods=5).mean().shift(1)
    return pd.DataFrame({"open_d": do, "y_high": dh.shift(1), "y_low": dl.shift(1),
                         "y_close": dc.shift(1), "atr_d": atr_d})


def day_slice(df15, date):
    return df15[df15.index.date == date]


# ── Motor de simulação intradiária de 1 trade ──────────────────────────────

def sim_trade(day, i_entry, direction, entry, stop, tp, exit_policy, symbol,
              entry_limit=False):
    """Simula 1 trade a partir do candle i_entry (inclusive) até fechar.
    exit_policy: 'tp_fixed' (tp + stop + EOD) | 'hold_eod' (só stop + EOD).
    Retorna dict do trade ou None se risco inválido."""
    buy = direction == "COMPRA"
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    gross = None
    stopped = False
    exit_reason = "EOD"
    for j in range(i_entry, len(day)):
        h, l, c = float(day.iloc[j]["High"]), float(day.iloc[j]["Low"]), float(day.iloc[j]["Close"])
        m = _mod(day.index[j])
        hit_stop = (l <= stop) if buy else (h >= stop)
        hit_tp = tp is not None and ((h >= tp) if buy else (l <= tp))
        if hit_stop:
            gross, stopped, exit_reason = -1.0, True, "SL"
            break
        if exit_policy == "tp_fixed" and hit_tp:
            gross = (abs(tp - entry)) / risk
            exit_reason = "TP"
            break
        if m >= EOD_MIN:
            gross = ((c - entry) / risk) if buy else ((entry - c) / risk)
            stopped, exit_reason = True, "EOD"
            break
    if gross is None:  # dia acabou sem tocar nada: fecha no último candle
        c = float(day.iloc[-1]["Close"])
        gross = ((c - entry) / risk) if buy else ((entry - c) / risk)
        stopped = True
    cfg = _cost_cfg(symbol)
    cost_pts = fees_pts(symbol)
    if not entry_limit:
        cost_pts += cfg["tick_size"]
    if stopped:
        cost_pts += cfg["tick_size"]
    return {"dir": direction, "gross_R": round(gross, 3),
            "cost_R": round(cost_pts / risk, 3),
            "net_R": round(gross - cost_pts / risk, 3),
            "risk_pts": round(risk, 2), "exit": exit_reason,
            "entry_mode": "LIMITE" if entry_limit else "MERCADO"}


# ── Setups (cada um recebe o dia + refs e retorna no máx. 1 trade) ─────────

def s_gap_fade(day, r, symbol):
    gap = r["open_d"] - r["y_close"]
    ag, atr = abs(gap), r["atr_d"]
    if not (0.4 * atr <= ag <= 1.2 * atr) or len(day) < 3:
        return None
    # entra no open do 2º candle (09:15) se o gap ainda estiver aberto
    e = float(day.iloc[1]["Open"])
    buy = gap < 0            # gap de baixa → compra p/ fechar o gap
    if buy and e >= r["y_close"]: return None
    if (not buy) and e <= r["y_close"]: return None
    stop = e - 0.7 * ag if buy else e + 0.7 * ag
    return sim_trade(day, 1, "COMPRA" if buy else "VENDA", e, stop,
                     r["y_close"], "tp_fixed", symbol)


def s_gap_go(day, r, symbol):
    gap = r["open_d"] - r["y_close"]
    ag, atr = abs(gap), r["atr_d"]
    outside = r["open_d"] > r["y_high"] or r["open_d"] < r["y_low"]
    if ag < 0.6 * atr or not outside or len(day) < 3:
        return None
    o0, c0 = float(day.iloc[0]["Open"]), float(day.iloc[0]["Close"])
    buy = gap > 0
    if buy and c0 <= o0: return None          # 1º candle precisa confirmar a direção
    if (not buy) and c0 >= o0: return None
    e = float(day.iloc[1]["Open"])
    stop = float(day.iloc[0]["Low"]) if buy else float(day.iloc[0]["High"])
    risk = abs(e - stop)
    tp = e + TARGET_RR * risk if buy else e - TARGET_RR * risk
    return sim_trade(day, 1, "COMPRA" if buy else "VENDA", e, stop, tp, "tp_fixed", symbol)


def _orb(day, r, symbol, n_or, last_entry_min):
    if len(day) < n_or + 2:
        return None
    orh = float(day.iloc[:n_or]["High"].max())
    orl = float(day.iloc[:n_or]["Low"].min())
    mid = (orh + orl) / 2
    for j in range(n_or, len(day) - 1):
        m = _mod(day.index[j])
        if m >= last_entry_min:
            return None
        c = float(day.iloc[j]["Close"])
        pc = float(day.iloc[j - 1]["Close"])
        if pc <= orh < c:
            e = float(day.iloc[j + 1]["Open"])
            risk = e - mid
            if risk <= 0: return None
            return sim_trade(day, j + 1, "COMPRA", e, mid, e + TARGET_RR * risk,
                             "tp_fixed", symbol)
        if pc >= orl > c:
            e = float(day.iloc[j + 1]["Open"])
            risk = mid - e
            if risk <= 0: return None
            return sim_trade(day, j + 1, "VENDA", e, mid, e - TARGET_RR * risk,
                             "tp_fixed", symbol)
    return None


def s_orb30(day, r, symbol):
    return _orb(day, r, symbol, 2, 11 * 60 + 30)


def s_orb60(day, r, symbol):
    return _orb(day, r, symbol, 4, 11 * 60 + 30)


def s_orb60_retest(day, r, symbol):
    """Rompe a 1ª hora, espera o RETESTE do nível e entra LIMITE (custo ~zero)."""
    n_or = 4
    if len(day) < n_or + 3:
        return None
    orh = float(day.iloc[:n_or]["High"].max())
    orl = float(day.iloc[:n_or]["Low"].min())
    mid = (orh + orl) / 2
    for j in range(n_or, len(day) - 2):
        if _mod(day.index[j]) >= 11 * 60 + 30:
            return None
        c = float(day.iloc[j]["Close"])
        pc = float(day.iloc[j - 1]["Close"])
        if pc <= orh < c:      # rompeu p/ cima; arma limite no nível por 4 candles
            for k in range(j + 1, min(j + 5, len(day))):
                if float(day.iloc[k]["Low"]) <= orh:      # reteste → fill
                    risk = orh - mid
                    if risk <= 0: return None
                    return sim_trade(day, k, "COMPRA", orh, mid,
                                     orh + TARGET_RR * risk, "tp_fixed", symbol,
                                     entry_limit=True)
            return None
        if pc >= orl > c:
            for k in range(j + 1, min(j + 5, len(day))):
                if float(day.iloc[k]["High"]) >= orl:
                    risk = mid - orl
                    if risk <= 0: return None
                    return sim_trade(day, k, "VENDA", orl, mid,
                                     orl - TARGET_RR * risk, "tp_fixed", symbol,
                                     entry_limit=True)
            return None
    return None


def s_pdh_pdl(day, r, symbol):
    for j in range(2, len(day) - 1):
        m = _mod(day.index[j])
        if m < 9 * 60 + 30: continue
        if m >= 15 * 60: return None
        c = float(day.iloc[j]["Close"]); pc = float(day.iloc[j - 1]["Close"])
        if pc <= r["y_high"] < c:
            e = float(day.iloc[j + 1]["Open"])
            stop = e - 0.25 * r["atr_d"]
            return sim_trade(day, j + 1, "COMPRA", e, stop,
                             e + TARGET_RR * (e - stop), "tp_fixed", symbol)
        if pc >= r["y_low"] > c:
            e = float(day.iloc[j + 1]["Open"])
            stop = e + 0.25 * r["atr_d"]
            return sim_trade(day, j + 1, "VENDA", e, stop,
                             e - TARGET_RR * (stop - e), "tp_fixed", symbol)
    return None


def s_fh_momentum(day, r, symbol):
    """1ª hora ampla e direcional → segura na direção até o fechamento."""
    n_or = 4
    if len(day) < n_or + 2:
        return None
    orh = float(day.iloc[:n_or]["High"].max())
    orl = float(day.iloc[:n_or]["Low"].min())
    rng = orh - orl
    if rng < 0.45 * r["atr_d"]:
        return None
    c = float(day.iloc[n_or - 1]["Close"])
    pos = (c - orl) / rng
    mid = (orh + orl) / 2
    e = float(day.iloc[n_or]["Open"])
    if pos >= 0.75:
        return sim_trade(day, n_or, "COMPRA", e, mid, None, "hold_eod", symbol)
    if pos <= 0.25:
        return sim_trade(day, n_or, "VENDA", e, mid, None, "hold_eod", symbol)
    return None


def s_vwap_reject(day, r, symbol):
    """Tendência intraday + pullback à VWAP com candle de REJEIÇÃO."""
    if len(day) < 8:
        return None
    tp_ = (day["High"] + day["Low"] + day["Close"]) / 3
    vol = day["Volume"].clip(lower=1)
    vwap = (tp_ * vol).cumsum() / vol.cumsum()
    for j in range(5, len(day) - 1):
        m = _mod(day.index[j])
        if m < 10 * 60: continue
        if m >= 15 * 60: return None
        v = float(vwap.iloc[j])
        above = all(float(day.iloc[k]["Close"]) > float(vwap.iloc[k]) for k in range(j - 4, j))
        below = all(float(day.iloc[k]["Close"]) < float(vwap.iloc[k]) for k in range(j - 4, j))
        lo, hi, c = float(day.iloc[j]["Low"]), float(day.iloc[j]["High"]), float(day.iloc[j]["Close"])
        if above and lo <= v and c > v:                 # testou a VWAP e foi defendida
            e = float(day.iloc[j + 1]["Open"])
            stop = lo
            if e - stop <= 0: return None
            return sim_trade(day, j + 1, "COMPRA", e, stop,
                             e + TARGET_RR * (e - stop), "tp_fixed", symbol)
        if below and hi >= v and c < v:
            e = float(day.iloc[j + 1]["Open"])
            stop = hi
            if stop - e <= 0: return None
            return sim_trade(day, j + 1, "VENDA", e, stop,
                             e - TARGET_RR * (stop - e), "tp_fixed", symbol)
    return None


def s_late_break(day, r, symbol):
    """Após 15h, rompimento do range do dia em dia comprimido → momentum de fechamento."""
    for j in range(2, len(day) - 1):
        m = _mod(day.index[j])
        if m < 15 * 60: continue
        if m >= 16 * 60 + 15: return None
        dh = float(day.iloc[:j]["High"].max())
        dl = float(day.iloc[:j]["Low"].min())
        if (dh - dl) > 0.8 * r["atr_d"]:
            return None                      # só em dia comprimido
        c = float(day.iloc[j]["Close"])
        e = float(day.iloc[j + 1]["Open"])
        if c > dh:
            stop = e - 0.2 * r["atr_d"]
            return sim_trade(day, j + 1, "COMPRA", e, stop, None, "hold_eod", symbol)
        if c < dl:
            stop = e + 0.2 * r["atr_d"]
            return sim_trade(day, j + 1, "VENDA", e, stop, None, "hold_eod", symbol)
    return None


SETUPS = {
    "GAP_FADE":     s_gap_fade,
    "GAP_GO":       s_gap_go,
    "ORB30":        s_orb30,
    "ORB60":        s_orb60,
    "ORB60_RETEST": s_orb60_retest,
    "PDH_PDL":      s_pdh_pdl,
    "FH_MOMENTUM":  s_fh_momentum,
    "VWAP_REJECT":  s_vwap_reject,
    "LATE_BREAK":   s_late_break,
}


# ── Runner ─────────────────────────────────────────────────────────────────

def run_symbol(symbol, df15):
    refs = build_refs(df15)
    results = {name: [] for name in SETUPS}
    for date, r in refs.iterrows():
        if any(pd.isna(v) for v in r.values):
            continue
        day = day_slice(df15, date)
        if len(day) < 6:
            continue
        rd = r.to_dict()
        for name, fn in SETUPS.items():
            try:
                t = fn(day, rd, symbol)
                if t:
                    t["date"] = date
                    results[name].append(t)
            except Exception:
                pass
    return {name: pd.DataFrame(rows) for name, rows in results.items() if rows}


def consistency(t):
    t2 = t.copy()
    t2["p"] = pd.to_datetime(t2["date"]).dt.to_period("2M").astype(str)
    per = t2.groupby("p")["net_R"].mean()
    return round((per > 0).sum() / len(per) * 100) if len(per) else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default=None)
    args = ap.parse_args()

    symbols = [args.symbol] if args.symbol else ["WIN$D", "WDO$D"]
    os.makedirs("logs", exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")

    ranking = []
    for sym in symbols:
        df = fetch_mt5(sym, "15", 200000)
        if df is None:
            continue
        print(f"\n{'='*70}\n  {sym}: {len(df)} candles ({df.index[0].date()} → {df.index[-1].date()})\n{'='*70}")
        res = run_symbol(sym, df)
        for name, t in sorted(res.items()):
            t["hour"] = 0
            s = _stats(t.rename(columns={}))
            cons = consistency(t)
            wr_exit = (t["exit"] == "TP").mean() * 100 if "exit" in t else 0
            print(f"  {name:<14} n={s['n']:<5} win {s['win%']:>5}% | bruta {s['exp_gross_R']:+.3f} | "
                  f"custo {s['cost_R_avg']:.3f} | LÍQUIDA {s['exp_net_R']:+.3f} R | "
                  f"PF {s['PF']:<5} | total {s['total_net_R']:+8.1f} R | DD {s['maxDD_R']:>6} | cons {cons}%")
            t.to_csv(f"logs/lab_{sym.replace('$','_')}_{name}.csv", index=False)
            ranking.append({"symbol": sym, "setup": name, "n": s["n"],
                            "exp_net": s["exp_net_R"], "PF": s["PF"],
                            "total": s["total_net_R"], "maxDD": s["maxDD_R"],
                            "cons%": cons})

    print(f"\n{'#'*70}\n  RANKING GERAL (exp líquida, mín. 100 trades)\n{'#'*70}")
    rk = pd.DataFrame(ranking)
    rk = rk[rk.n >= 100].sort_values("exp_net", ascending=False)
    for _, row in rk.iterrows():
        go = "🟢 GO" if (row.exp_net >= 0.10 and row.PF >= 1.25 and row["cons%"] >= 60) else \
             ("🟡 quase" if row.exp_net > 0.05 else "🔴")
        print(f"  {row.symbol:<8} {row.setup:<14} n={row.n:<5} exp_net {row.exp_net:+.3f} R  "
              f"PF {row.PF:<5} total {row.total:+8.1f} R  DD {row.maxDD:>7}  cons {row['cons%']}%  {go}")
    rk.to_csv(f"logs/lab_ranking_{stamp}.csv", index=False)
    print(f"\n  ranking salvo: logs/lab_ranking_{stamp}.csv")


if __name__ == "__main__":
    main()
