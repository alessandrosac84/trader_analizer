"""
analise_dia.py — Análise e EXPORTAÇÃO dos registros do dia (Monitor + Scalper).

Por que existe: o banco do Monitor é SQLite com WAL; quando a pasta está em
nuvem (OneDrive), a leitura externa pode vir "malformada". Este script roda NA
MÁQUINA (onde o banco está íntegro), imprime o resumo do dia e EXPORTA um CSV
limpo em logs/monitor_trades.csv — durável e legível de qualquer lugar, para
permitir análise confiável dos registros.

Uso:
    python analise_dia.py            # dia de hoje (BRT)
    python analise_dia.py 2026-07-09 # dia específico
"""
import csv
import os
import sys
from datetime import datetime, timezone, timedelta

_BRT = timezone(timedelta(hours=-3))
_BASE = os.path.dirname(os.path.abspath(__file__))
_LOGDIR = os.path.join(_BASE, "logs")
_MON_CSV = os.path.join(_LOGDIR, "monitor_trades.csv")
_SCALP_CSV = os.path.join(_LOGDIR, "scalper_trades.csv")

_MON_COLS = ["id", "opened_at", "closed_at", "tv_symbol", "interval", "acao",
             "entry_price", "volume", "sl_initial", "tp1_initial", "score",
             "ai_veredito", "ai_confidence", "ai_motivo", "close_reason",
             "exit_price", "pnl_pts", "pnl_brl"]


def _today():
    return datetime.now(_BRT).strftime("%Y-%m-%d")


def _f(v):
    try:
        return float(v)
    except Exception:
        return 0.0


# ── Exportação durável (append incremental por id) ──────────────────────────

def export_monitor_csv() -> int:
    """Lê o banco auto_trades e adiciona ao CSV as linhas ainda não exportadas.
    Retorna quantas linhas novas foram gravadas. Robusto a banco indisponível."""
    try:
        from services.trade_log import _conn
    except Exception as exc:
        print(f"[export] não consegui importar trade_log: {exc}")
        return 0
    os.makedirs(_LOGDIR, exist_ok=True)
    existing = set()
    if os.path.exists(_MON_CSV):
        try:
            with open(_MON_CSV, encoding="utf-8") as f:
                existing = {r.get("id") for r in csv.DictReader(f)}
        except Exception:
            existing = set()
    try:
        with _conn() as conn:
            rows = [dict(r) for r in conn.execute("SELECT * FROM auto_trades ORDER BY id").fetchall()]
    except Exception as exc:
        print(f"[export] falha lendo o banco: {exc}")
        return 0
    new = [r for r in rows if str(r.get("id")) not in existing]
    if not new:
        return 0
    write_header = not os.path.exists(_MON_CSV)
    with open(_MON_CSV, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=_MON_COLS, extrasaction="ignore")
        if write_header:
            w.writeheader()
        for r in new:
            w.writerow(r)
    return len(new)


# ── Resumo do dia ───────────────────────────────────────────────────────────

def _summ_monitor(date):
    try:
        from services.trade_log import _conn
        with _conn() as conn:
            rows = [dict(r) for r in conn.execute(
                "SELECT * FROM auto_trades WHERE date(opened_at)=? ORDER BY id", (date,)).fetchall()]
    except Exception as exc:
        print(f"  (banco indisponível: {exc} — usando o CSV exportado)")
        rows = []
        if os.path.exists(_MON_CSV):
            with open(_MON_CSV, encoding="utf-8") as f:
                rows = [r for r in csv.DictReader(f) if (r.get("opened_at") or "").startswith(date)]
    _naoexec = {"BLOQUEADO_IA", "AGUARDADO_IA"}
    bloq = [r for r in rows if r.get("close_reason") in _naoexec]
    reais = [r for r in rows if r.get("close_reason") not in _naoexec]
    wins = [r for r in reais if _f(r.get("pnl_brl")) > 0]
    loss = [r for r in reais if _f(r.get("pnl_brl")) < 0]
    pnl = round(sum(_f(r.get("pnl_brl")) for r in reais), 2)
    dec = len(wins) + len(loss)
    print("\n" + "=" * 60)
    print(f"MONITOR MT5 — {date}")
    print("=" * 60)
    print(f"  Sinais gerados: {len(rows)}   Executados: {len(reais)}   Bloqueados IA: {len(bloq)}")
    print(f"  Wins: {len(wins)}   Losses: {len(loss)}   "
          f"Win rate: {round(len(wins)/dec*100) if dec else 0}%")
    print(f"  P&L do dia: R$ {pnl:+.2f}")
    if reais:
        print("  Trades executados:")
        for r in reais:
            print(f"    {(r.get('opened_at') or '')[11:19]}  {(r.get('tv_symbol') or '').split(':')[-1]:8} "
                  f"{r.get('acao'):6} score {str(r.get('score')):>3}  "
                  f"{r.get('close_reason') or '—':16} R$ {_f(r.get('pnl_brl')):+.2f}")
    return pnl


def _summ_scalper(date):
    if not os.path.exists(_SCALP_CSV):
        print("\nSCALPER — sem CSV.")
        return 0.0
    with open(_SCALP_CSV, encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f)
                if (r.get("datetime_brt") or "").startswith(date) and r.get("resultado")]
    wins = sum(1 for r in rows if r["resultado"] == "WIN")
    loss = sum(1 for r in rows if r["resultado"] == "LOSS")
    be = sum(1 for r in rows if r["resultado"] == "BE")
    pnl = round(sum(_f(r.get("profit")) for r in rows), 2)
    dec = wins + loss
    print("\n" + "=" * 60)
    print(f"SCALPER — {date}")
    print("=" * 60)
    print(f"  Trades: {len(rows)}   Wins: {wins}  Losses: {loss}  BE: {be}   "
          f"Win rate: {round(wins/dec*100) if dec else 0}%")
    print(f"  P&L do dia: R$ {pnl:+.2f}")
    return pnl


def main():
    date = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else _today()
    n = export_monitor_csv()
    print(f"[export] {n} novo(s) trade(s) do Monitor adicionado(s) a logs/monitor_trades.csv")
    m = _summ_monitor(date)
    s = _summ_scalper(date)
    print("\n" + "=" * 60)
    print(f"CONSOLIDADO {date}:  Monitor R$ {m:+.2f}  +  Scalper R$ {s:+.2f}  =  R$ {round(m+s,2):+.2f}")
    print("=" * 60)


if __name__ == "__main__":
    main()
