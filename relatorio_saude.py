"""
relatorio_saude.py — SAÚDE DO PORTFÓLIO: realizado × backtest, por estratégia.

O módulo de acompanhamento da fase de paper trading. Lê os logs reais de todos
os motores e compara cada estratégia com o SEU baseline de backtest:
  · nº de trades, win rate, PF, R médio, P&L
  · frequência realizada vs esperada (trades/semana)
  · DEGRADAÇÃO (critérios objetivos, sem achismo):
      - com ≥20 trades: PF < 60% do backtest OU win% < backtest−10pp → 🚨
      - com <20 trades: "amostra insuficiente" (sem julgamento)
  · execução: entradas que falharam (SETUP PERDIDO) se registradas

Fontes:  logs/v7_trader_trades.csv       (Monitor V7 — GAP_FADE/ORB)
         logs/crypto_trades.csv          (Monitor Crypto — caminho em ai_veredito)
         logs/win_eod_trades.csv         (WIN_EOD_REV)

Uso:  python relatorio_saude.py           (tela + logs/saude_<data>.md)
      python relatorio_saude.py --dias 30
"""
import argparse
import os
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

# ── Baselines dos backtests que validaram cada estratégia ───────────────────
BASELINES = {
    # estratégia: (win%, PF, R/trade, trades/semana esperados)
    "V7:GAP_FADE":      (55.0, 1.38, 0.172, 0.4),
    "V7:ORB":           (55.0, 1.48, 0.189, 0.6),
    "WIN_EOD_REV":      (44.7, 1.57, 0.285, 1.2),
    "WIN:NR7_BREAK":    (51.4, 1.52, 0.262, 0.5),
    "WIN:INSIDE_BAR":   (45.5, 1.29, 0.159, 0.9),
    "WIN:NR7_TREND_H4": (54.9, 1.81, 0.374, 0.3),
    "XAU:ORDER_FLOW":   (32.0, 1.31, 0.179, 4.0),
    "XAU:LONDON_HANDOFF": (52.1, 1.56, 0.208, 1.3),
    "XAU:LH_047_D18":   (51.3, 1.45, 0.185, 0.9),
    "XAU:US_DRIFT":     (46.2, 1.61, 0.343, 2.0),
    "XAU:RND_FADE_TIGHT": (47.6, 1.32, 0.178, 1.4),
    "XAU:RND_FADE_07":  (50.0, 1.51, 0.273, 0.7),
    "XAU:PULLBACK":     (None, 1.64, 0.208, 0.3),
    "ETH:ORDER_FLOW":   (44.7, 1.51, 0.260, 1.3),
}

LOG = Path(__file__).parent / "logs"


def _r_est(direcao, entry, sl, exit_px):
    """R realizado estimado por preços (quando o log não traz R)."""
    try:
        entry, sl, exit_px = float(entry), float(sl), float(exit_px)
        risk = abs(entry - sl)
        if risk <= 0:
            return None
        mult = 1 if str(direcao).upper() == "COMPRA" else -1
        return mult*(exit_px - entry)/risk
    except Exception:
        return None


def load_trades(days):
    """Unifica todos os logs em (estrategia, ts, resultado_win, pnl, r)."""
    cut = datetime.now() - timedelta(days=days)
    rows = []
    # ── Monitor V7 ──
    p = LOG/"v7_trader_trades.csv"
    if p.exists():
        df = pd.read_csv(p)
        df = df[df.get("ts_close", pd.Series(dtype=str)).astype(str).str.len() > 3]
        for r in df.itertuples():
            try:
                ts = pd.to_datetime(r.ts_open)
            except Exception:
                continue
            if ts < cut:
                continue
            rr = pd.to_numeric(getattr(r, "r_multiple", None), errors="coerce")
            pnl = pd.to_numeric(getattr(r, "profit_brl", None), errors="coerce")
            rows.append({"estrategia": f"V7:{r.setup}", "ts": ts,
                         "win": (pnl or 0) > 0, "pnl": pnl or 0.0,
                         "r": rr if rr == rr else None, "moeda": "BRL"})
    # ── Monitor Crypto ──
    p = LOG/"crypto_trades.csv"
    if p.exists():
        df = pd.read_csv(p)
        df = df[df.exit_price.notna()]
        path_map = {"ORDER_FLOW": "ORDER_FLOW", "LONDON_HANDOFF": "LONDON_HANDOFF",
                    "US_DRIFT": "US_DRIFT", "PULLBACK": "PULLBACK"}
        for r in df.itertuples():
            try:
                ts = pd.to_datetime(r.datetime_brt)
            except Exception:
                continue
            if ts < cut:
                continue
            sym = str(r.symbol)[:3]
            ver = str(getattr(r, "ai_veredito", "") or "").upper()
            cam = next((v for k, v in path_map.items() if k in ver), "CASCADE")
            pnl = pd.to_numeric(r.profit, errors="coerce")
            rows.append({"estrategia": f"{sym}:{cam}", "ts": ts,
                         "win": str(getattr(r, "resultado", "")) == "WIN",
                         "pnl": pnl if pnl == pnl else 0.0,
                         "r": _r_est(r.direcao, r.entry_price, r.sl, r.exit_price),
                         "moeda": "USD"})
    # ── WIN_EOD_REV (pareia ENTRADA com a saída seguinte) ──
    p = LOG/"win_eod_trades.csv"
    if p.exists():
        df = pd.read_csv(p)
        last_entry = None
        for r in df.itertuples():
            ev = str(r.evento)
            if ev == "ENTRADA":
                last_entry = r
            elif ev in ("TP", "SL", "SAIDA", "EOD_CLOSE") and last_entry is not None:
                try:
                    ts = pd.to_datetime(last_entry.ts)
                except Exception:
                    last_entry = None; continue
                pnl = pd.to_numeric(getattr(r, "pnl_brl", None), errors="coerce")
                rr = _r_est("COMPRA", last_entry.preco, last_entry.sl, r.preco)
                if ts >= cut:
                    rows.append({"estrategia": "WIN_EOD_REV", "ts": ts,
                                 "win": (pnl or 0) > 0, "pnl": pnl or 0.0,
                                 "r": rr, "moeda": "BRL"})
                last_entry = None
    return pd.DataFrame(rows)


def health_data(days: int = 21) -> dict:
    """Dados estruturados p/ a tela /saude e p/ o relatório em markdown."""
    t = load_trades(days)
    semanas = max(days/7, 0.1)
    todas = sorted(set(list(BASELINES.keys()) + (list(t.estrategia.unique()) if not t.empty else [])))
    ests, alertas = [], []
    for est in todas:
        bt = BASELINES.get(est)
        sub = t[t.estrategia == est] if not t.empty else pd.DataFrame()
        n = len(sub)
        row = {"estrategia": est, "n": n,
               "freq": round(n/semanas, 1),
               "freq_esp": bt[3] if bt else None,
               "win": None, "win_esp": bt[0] if bt else None,
               "pf": None, "pf_esp": bt[1] if bt else None,
               "r_med": None, "r_esp": bt[2] if bt else None,
               "pnl": {}, "status": "sem trades"}
        if n == 0:
            if bt and days >= 7 and bt[3] and bt[3]*semanas >= 3:
                row["status"] = "silencio"
                alertas.append(f"{est}: 0 trades no período (esperados ~{round(bt[3]*semanas)}) — verificar motor")
            ests.append(row); continue
        wins = int(sub.win.sum())
        row["win"] = round(100*wins/n, 1)
        gw = float(sub.loc[sub.pnl > 0, "pnl"].sum())
        gl = float(-sub.loc[sub.pnl <= 0, "pnl"].sum())
        row["pf"] = round(gw/gl, 2) if gl > 0 else None
        rs = sub.r.dropna()
        row["r_med"] = round(float(rs.mean()), 3) if len(rs) else None
        row["pnl"] = {m: round(float(sub[sub.moeda == m].pnl.sum()), 2) for m in sub.moeda.unique()}
        if bt and n >= 20:
            degr = ((row["pf"] or 0) < 0.6*bt[1]) or (bt[0] is not None and row["win"] < bt[0]-10)
            row["status"] = "degradacao" if degr else "ok"
            if degr:
                alertas.append(f"{est}: PF {row['pf']} vs backtest {bt[1]} · win {row['win']}% vs {bt[0]}% — investigar/pausar")
        else:
            row["status"] = f"amostra {n}/20"
        ests.append(row)
    return {"dias": days, "total": int(len(t)), "estrategias": ests, "alertas": alertas,
            "gerado_em": datetime.now().strftime("%d/%m/%Y %H:%M")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dias", type=int, default=21)
    args = ap.parse_args()

    os.makedirs(LOG, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d")
    out_path = LOG/f"saude_{stamp}.md"
    lines = []
    def w(s=""):
        print(s); lines.append(s)

    t = load_trades(args.dias)
    semanas = max(args.dias/7, 0.1)
    w(f"# SAÚDE DO PORTFÓLIO — últimos {args.dias} dias · {datetime.now():%d/%m/%Y %H:%M}")
    w(f"Trades fechados no período: **{len(t)}**\n")
    w("| Estratégia | n | /sem (esp.) | win% (esp.) | PF (esp.) | R méd (esp.) | P&L | Status |")
    w("|---|---|---|---|---|---|---|---|")

    todas = sorted(set(list(BASELINES.keys()) + (list(t.estrategia.unique()) if not t.empty else [])))
    alertas = []
    for est in todas:
        bt = BASELINES.get(est)
        sub = t[t.estrategia == est] if not t.empty else pd.DataFrame()
        n = len(sub)
        exp_wk = bt[3] if bt else None
        if n == 0:
            freq_flag = ""
            if bt and args.dias >= 7 and exp_wk and exp_wk*semanas >= 3:
                freq_flag = " ⚠️ esperava ~%d" % round(exp_wk*semanas)
                alertas.append(f"{est}: 0 trades no período (esperados ~{round(exp_wk*semanas)}) — verificar se o motor está ativo")
            w(f"| {est} | 0 | 0 ({exp_wk if bt else '—'}) | — | — | — | — | sem trades{freq_flag} |")
            continue
        wins = int(sub.win.sum())
        winp = 100*wins/n
        gw = sub.loc[sub.pnl > 0, "pnl"].sum()
        gl = -sub.loc[sub.pnl <= 0, "pnl"].sum()
        pf = round(gw/gl, 2) if gl > 0 else float("inf")
        rs = sub.r.dropna()
        rmed = round(float(rs.mean()), 3) if len(rs) else None
        pnl_txt = " · ".join(f"{m} {sub[sub.moeda==m].pnl.sum():+.2f}"
                             for m in sub.moeda.unique())
        freq = round(n/semanas, 1)
        if bt:
            bwin, bpf, br, bwk = bt
            if n >= 20:
                degr = (pf < 0.6*bpf) or (bwin is not None and winp < bwin - 10)
                status = "🚨 DEGRADAÇÃO" if degr else "✅ dentro do esperado"
                if degr:
                    alertas.append(f"{est}: PF {pf} vs backtest {bpf} · win {winp:.0f}% vs {bwin}% — investigar/pausar")
            else:
                status = f"⏳ amostra ({n}/20)"
            w(f"| {est} | {n} | {freq} ({bwk}) | {winp:.0f}% ({bwin or '—'}) | {pf} ({bpf}) | "
              f"{rmed if rmed is not None else '—'} ({br}) | {pnl_txt} | {status} |")
        else:
            w(f"| {est} | {n} | {freq} (—) | {winp:.0f}% | {pf} | {rmed or '—'} | {pnl_txt} | sem baseline |")

    w("\n## Alertas")
    if alertas:
        for a in alertas:
            w(f"- 🚨 {a}")
    else:
        w("- nenhum alerta de degradação (ou amostra ainda insuficiente)")
    w("\n*Critérios: 🚨 com n≥20 se PF < 60% do backtest OU win% < backtest−10pp. "
      "Com n<20, sem julgamento (variância normal).*")
    open(out_path, "w", encoding="utf-8").write("\n".join(lines))
    print(f"\n# salvo em {out_path}")


if __name__ == "__main__":
    main()
