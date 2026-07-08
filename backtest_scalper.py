"""
backtest_scalper.py — Backtest CONTRAFACTUAL do Scalper.

Mesma ideia do backtest do Monitor: lê os trades já registrados
(logs/scalper_trades.csv) e responde "se tivéssemos aplicado os novos
guard-rails, como ficaria a assertividade?".

NÃO é uma simulação tick-a-tick (não temos o replay do book/tape histórico);
é um recorte dos trades reais pelos filtros novos — exatamente o método que
usamos para calibrar o Monitor MT5.

Guard-rails testados (espelham o _ASSERT do scalper_bp):
  - Anti-exaustão: score <= teto (BITN26 84 / default 88)
  - Disciplina de sessão: só PRIME/BOM
  - Anti-overtrading: >=45s entre trades e no máx. 2/min

Uso:  python backtest_scalper.py [caminho_csv]
"""
import csv
import sys
from datetime import datetime

CSV = sys.argv[1] if len(sys.argv) > 1 else "logs/scalper_trades.csv"

# Tick size / valor por tick (BRL) por símbolo — para converter P&L em R
TICK = {"BITN26": (100.0, 1.0), "BITM26": (100.0, 1.0),
        "WDON26": (0.5, 5.0), "WINQ26": (5.0, 1.0)}
SCORE_CEIL = {"BITN26": 84, "BITM26": 84, "_default": 88}
GOOD_SESSIONS = {"PRIME", "BOM"}
MIN_GAP_S = 45          # piso de cooldown
MAX_PER_MIN = 2         # teto por minuto


def _f(v, d=0.0):
    try:
        return float(v)
    except Exception:
        return d


def _risk_brl(r):
    """Risco em R$ do trade (distância entrada→stop × valor do tick × volume)."""
    ts, tv = TICK.get((r.get("symbol") or "").upper(), (1.0, 1.0))
    dist = abs(_f(r.get("entry_price")) - _f(r.get("sl_price")))
    ticks = dist / ts if ts else 0
    vol = _f(r.get("volume"), 1.0)
    return ticks * tv * vol


def metrics(rows, label):
    n = len(rows)
    wins = [r for r in rows if r["resultado"] == "WIN"]
    loss = [r for r in rows if r["resultado"] == "LOSS"]
    bes  = [r for r in rows if r["resultado"] == "BE"]
    dec  = len(wins) + len(loss)
    wr   = (len(wins) / dec * 100) if dec else 0.0
    pnl  = sum(_f(r.get("profit")) for r in rows)
    gross_win = sum(_f(r.get("profit")) for r in wins)
    gross_los = abs(sum(_f(r.get("profit")) for r in loss))
    pf   = (gross_win / gross_los) if gross_los else float("inf")
    # expectância em R
    rs = []
    for r in rows:
        risk = _risk_brl(r)
        if risk > 0:
            rs.append(_f(r.get("profit")) / risk)
    exp_r = (sum(rs) / len(rs)) if rs else 0.0
    print(f"\n── {label} ──")
    print(f"  Trades: {n}   (WIN {len(wins)} · LOSS {len(loss)} · BE {len(bes)})")
    print(f"  Win rate (decisivo): {wr:.1f}%")
    print(f"  P&L total: R$ {pnl:+.2f}   |   por trade: R$ {(pnl/n if n else 0):+.2f}")
    print(f"  Profit factor: {pf:.2f}")
    print(f"  Expectância: {exp_r:+.3f} R/trade")
    return {"n": n, "wr": wr, "pnl": pnl, "pf": pf, "exp_r": exp_r}


def pass_exhaustion(r):
    ceil = SCORE_CEIL.get((r.get("symbol") or "").upper(), SCORE_CEIL["_default"])
    return _f(r.get("score")) <= ceil


def pass_session(r):
    return (r.get("session") or "").upper() in GOOD_SESSIONS


def apply_overtrading(rows):
    """Mantém só trades respeitando >=MIN_GAP_S entre si e <=MAX_PER_MIN por minuto."""
    def _dt(r):
        try:
            return datetime.strptime(r["datetime_brt"], "%Y-%m-%d %H:%M:%S")
        except Exception:
            return None
    ordered = sorted([r for r in rows if _dt(r)], key=_dt)
    kept, kept_ts = [], []
    for r in ordered:
        t = _dt(r)
        if kept_ts and (t - kept_ts[-1]).total_seconds() < MIN_GAP_S:
            continue
        recent = [x for x in kept_ts if (t - x).total_seconds() < 60]
        if len(recent) >= MAX_PER_MIN:
            continue
        kept.append(r); kept_ts.append(t)
    return kept


def main():
    rows = [r for r in csv.DictReader(open(CSV, encoding="utf-8")) if r.get("resultado")]
    print(f"Backtest contrafactual do Scalper — {len(rows)} trades registrados ({CSV})")

    base = metrics(rows, "BASELINE (todos os trades)")

    f1 = [r for r in rows if pass_exhaustion(r)]
    metrics(f1, "Só anti-exaustão (score <= teto)")

    f2 = [r for r in rows if pass_session(r)]
    metrics(f2, "Só disciplina de sessão (PRIME/BOM)")

    f3 = apply_overtrading(rows)
    metrics(f3, "Só anti-overtrading (>=45s / <=2 por min)")

    comb = apply_overtrading([r for r in rows if pass_exhaustion(r) and pass_session(r)])
    res = metrics(comb, "COMBINADO (todos os guard-rails)")

    print("\n" + "=" * 52)
    print("RESUMO baseline → combinado:")
    print(f"  Win rate:      {base['wr']:.1f}%  →  {res['wr']:.1f}%")
    print(f"  P&L total:     R$ {base['pnl']:+.2f}  →  R$ {res['pnl']:+.2f}")
    print(f"  Profit factor: {base['pf']:.2f}  →  {res['pf']:.2f}")
    print(f"  Expectância:   {base['exp_r']:+.3f}R  →  {res['exp_r']:+.3f}R")
    print(f"  Trades:        {base['n']}  →  {res['n']}")


if __name__ == "__main__":
    main()
