"""
services/daily_report.py
------------------------
Relatório diário consolidado (Monitor MT5 + Scalper) enviado ao Telegram no
fechamento do pregão (~18:05 BRT, seg-sex).

REGRA: aditivo e SOMENTE LEITURA sobre os módulos existentes. Não altera
nenhuma lógica de trade — apenas lê estatísticas (auto_trades_stats do Monitor
e o CSV do Scalper filtrado pelo dia) e envia um resumo.

Uso manual / teste:
  python -c "from services.daily_report import send_daily_report as s; print(s())"
"""
import csv
import logging
import os
import threading
import time as _time
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)
_BRT = timezone(timedelta(hours=-3))

# Horário de disparo (após o fechamento das 18:00 feito pelo market_close_scheduler)
REPORT_HOUR = 18
REPORT_MINUTE = 5
REPORT_WINDOW = 5   # janela de tentativa (min)

_CSV = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs", "scalper_trades.csv")

_running = False
_last_sent_date = None
_lock = threading.Lock()


def _now_brt() -> datetime:
    return datetime.now(_BRT)


def _fmt_brl(v) -> str:
    try:
        return f"R${float(v):+.2f}".replace(".", ",")
    except Exception:
        return "—"


def _scalper_symbol() -> str:
    return (os.getenv("SCALPER_TG_SYMBOL")
            or os.getenv("SCALPER_SUPERVISOR_SYMBOL")
            or "BITN26").upper().strip()


# ── Coleta de dados (somente leitura) ───────────────────────────────────────

def _monitor_stats() -> dict:
    """Estatísticas do Monitor MT5 (trades automáticos) do dia."""
    try:
        from services.trade_log import auto_trades_stats
        return auto_trades_stats(today_only=True) or {}
    except Exception as exc:
        logger.warning("daily_report: falha lendo stats do monitor: %s", exc)
        return {}


def _scalper_today(symbol: str | None = None) -> dict:
    """Lê o CSV do scalper e agrega SOMENTE os trades de hoje (por datetime_brt)."""
    today = _now_brt().strftime("%Y-%m-%d")
    sym = (symbol or _scalper_symbol()).upper()
    out = {"total": 0, "wins": 0, "losses": 0, "bes": 0, "pnl": 0.0,
           "win_rate": 0, "avg_score": None, "symbol": sym}
    if not os.path.exists(_CSV):
        return out
    try:
        with open(_CSV, encoding="utf-8") as f:
            rows = [r for r in csv.DictReader(f)
                    if (r.get("datetime_brt") or "").startswith(today)
                    and r.get("resultado")
                    and (not sym or (r.get("symbol") or "").upper() == sym)]
    except Exception as exc:
        logger.warning("daily_report: falha lendo CSV scalper: %s", exc)
        return out

    def _f(v):
        try:
            return float(v)
        except Exception:
            return 0.0

    total = len(rows)
    if not total:
        return out
    wins = sum(1 for r in rows if r["resultado"] == "WIN")
    losses = sum(1 for r in rows if r["resultado"] == "LOSS")
    bes = sum(1 for r in rows if r["resultado"] == "BE")
    pnl = round(sum(_f(r.get("profit")) for r in rows), 2)
    scores = [_f(r.get("score")) for r in rows if r.get("score")]
    dec = wins + losses
    out.update({
        "total": total, "wins": wins, "losses": losses, "bes": bes,
        "pnl": pnl,
        "win_rate": round(wins / dec * 100) if dec else 0,
        "avg_score": round(sum(scores) / len(scores), 1) if scores else None,
    })
    return out


def _week_start_brt() -> str:
    """Segunda-feira da semana corrente (YYYY-MM-DD, BRT)."""
    now = _now_brt()
    monday = now - timedelta(days=now.weekday())
    return monday.strftime("%Y-%m-%d")


def _monitor_week_stats() -> dict:
    """Monitor MT5 — agrega os trades da semana (seg→hoje) via consulta read-only.

    Não altera trade_log: apenas reutiliza a conexão para um SELECT com range
    de datas (auto_trades_stats só suporta hoje/total).
    """
    out = {"total": 0, "wins": 0, "losses": 0, "bloqueados_ia": 0,
           "win_rate_pct": 0, "pnl_total_brl": 0.0, "dias": {}}
    try:
        from services.trade_log import _conn
        monday = _week_start_brt()
        with _conn() as conn:
            rows = [dict(r) for r in conn.execute(
                "SELECT * FROM auto_trades WHERE date(opened_at) >= ?", (monday,)
            ).fetchall()]
    except Exception as exc:
        logger.warning("daily_report: falha lendo semana do monitor: %s", exc)
        return out

    _nao_exec = {"BLOQUEADO_IA", "AGUARDADO_IA"}
    bloq = [r for r in rows if r.get("close_reason") in _nao_exec]
    reais = [r for r in rows if r.get("close_reason") not in _nao_exec]
    fechados = [r for r in reais if r.get("closed_at")]
    gains = [r for r in reais if (r.get("pnl_pts") or 0) > 0]
    losses = [r for r in reais if (r.get("pnl_pts") or 0) < 0]
    pnl = round(sum((r.get("pnl_brl") or 0) for r in reais), 2)

    # P&L por dia (para a curva da semana)
    dias = {}
    for r in reais:
        d = (r.get("opened_at") or "")[:10]
        if d:
            dias[d] = round(dias.get(d, 0.0) + (r.get("pnl_brl") or 0), 2)

    out.update({
        "total": len(rows), "wins": len(gains), "losses": len(losses),
        "bloqueados_ia": len(bloq),
        "win_rate_pct": round(len(gains) / len(fechados) * 100) if fechados else 0,
        "pnl_total_brl": pnl, "dias": dias,
    })
    return out


def _scalper_week(symbol: str | None = None) -> dict:
    """Scalper — agrega os trades da semana (seg→hoje) a partir do CSV."""
    monday = _week_start_brt()
    today = _now_brt().strftime("%Y-%m-%d")
    sym = (symbol or _scalper_symbol()).upper()
    out = {"total": 0, "wins": 0, "losses": 0, "bes": 0, "pnl": 0.0,
           "win_rate": 0, "avg_score": None, "symbol": sym, "dias": {}}
    if not os.path.exists(_CSV):
        return out
    try:
        with open(_CSV, encoding="utf-8") as f:
            rows = [r for r in csv.DictReader(f)
                    if monday <= (r.get("datetime_brt") or "")[:10] <= today
                    and r.get("resultado")
                    and (not sym or (r.get("symbol") or "").upper() == sym)]
    except Exception as exc:
        logger.warning("daily_report: falha lendo semana do scalper: %s", exc)
        return out

    def _f(v):
        try:
            return float(v)
        except Exception:
            return 0.0

    total = len(rows)
    if not total:
        return out
    wins = sum(1 for r in rows if r["resultado"] == "WIN")
    losses = sum(1 for r in rows if r["resultado"] == "LOSS")
    bes = sum(1 for r in rows if r["resultado"] == "BE")
    pnl = round(sum(_f(r.get("profit")) for r in rows), 2)
    scores = [_f(r.get("score")) for r in rows if r.get("score")]
    dec = wins + losses
    dias = {}
    for r in rows:
        d = (r.get("datetime_brt") or "")[:10]
        if d:
            dias[d] = round(dias.get(d, 0.0) + _f(r.get("profit")), 2)
    out.update({
        "total": total, "wins": wins, "losses": losses, "bes": bes, "pnl": pnl,
        "win_rate": round(wins / dec * 100) if dec else 0,
        "avg_score": round(sum(scores) / len(scores), 1) if scores else None,
        "dias": dias,
    })
    return out


def _supervisor_hint() -> str:
    """Última recomendação do supervisor do scalper, se houver."""
    try:
        import json
        p = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data",
                         "scalper_supervisor.json")
        if not os.path.exists(p):
            return ""
        d = json.loads(open(p, encoding="utf-8").read())
        rec = d.get("recomendacao")
        if rec and rec != "CONTINUAR":
            return f"\n🧭 <b>Supervisor:</b> {rec} — {d.get('motivo', '')}"
        return ""
    except Exception:
        return ""


# ── Montagem da mensagem ────────────────────────────────────────────────────

def build_daily_report() -> str:
    now = _now_brt()
    mon = _monitor_stats()
    sca = _scalper_today()

    # Hub B3 (V7+WinGo+EOD) + Crypto CSV
    hub = {}
    try:
        from services.b3_trade_hub import unified_period
        hub = unified_period("day") or {}
    except Exception as exc:
        logger.debug("daily_report hub: %s", exc)
    cy_rows = []
    cy_pnl_usd = 0.0
    cy_rate = 5.2
    try:
        from services.crypto_telegram import _rows_period, _usdbrl
        cy_rows = _rows_period("day")
        cy_rate = _usdbrl()
        cy_pnl_usd = round(sum(float(r.get("profit") or 0) for r in cy_rows), 2)
    except Exception as exc:
        logger.debug("daily_report crypto: %s", exc)

    mon_pnl = mon.get("pnl_total_brl", 0.0) or 0.0
    sca_pnl = sca.get("pnl", 0.0) or 0.0
    hub_pnl = float(hub.get("pnl_brl") or 0.0)
    cy_pnl_brl = round(cy_pnl_usd * cy_rate, 2)
    combined = round(mon_pnl + sca_pnl + hub_pnl + cy_pnl_brl, 2)
    comb_icon = "🟢" if combined > 0 else ("🔴" if combined < 0 else "⚪")

    m_total = mon.get("total", 0)
    m_wins = mon.get("wins", 0)
    m_loss = mon.get("losses", 0)
    m_wr = mon.get("win_rate_pct", 0)
    m_bloq = mon.get("bloqueados_ia", 0)
    mon_icon = "📈" if mon_pnl >= 0 else "📉"

    s_total = sca.get("total", 0)
    s_wins = sca.get("wins", 0)
    s_loss = sca.get("losses", 0)
    s_be = sca.get("bes", 0)
    s_wr = sca.get("win_rate", 0)
    s_avg = sca.get("avg_score")
    sca_icon = "📈" if sca_pnl >= 0 else "📉"

    lines = [
        f"📊 <b>RELATÓRIO DO DIA — {now.strftime('%d/%m/%Y')}</b>",
        "━━━━━━━━━━━━━━━━━━",
        "",
        "🖥 <b>MONITOR MT5</b> (WIN/WDO)",
    ]
    if m_total:
        lines.append(f"   Trades: {m_total}   ✅{m_wins}  ❌{m_loss}   WR: {m_wr}%")
        if m_bloq:
            lines.append(f"   🚫 IA bloqueou: {m_bloq}")
        lines.append(f"   {mon_icon} P&L: {_fmt_brl(mon_pnl)}")
    else:
        lines.append("   Sem trades hoje.")
    lines += [
        "",
        f"⚡ <b>SCALPER</b> ({sca.get('symbol', '—')})",
    ]
    if s_total:
        avg_txt = f"   Score médio: {s_avg}" if s_avg is not None else ""
        lines += [
            f"   Trades: {s_total}   ✅{s_wins}  ❌{s_loss}  ➖{s_be}   WR: {s_wr}%{avg_txt}",
            f"   {sca_icon} P&L: {_fmt_brl(sca_pnl)}",
        ]
    else:
        lines.append("   Sem trades hoje.")

    # Hub B3
    h_n = int(hub.get("trades") or 0)
    h_icon = "📈" if hub_pnl >= 0 else "📉"
    lines += ["", "🏗 <b>HUB B3</b> (V7 + WinGo + EOD)"]
    if h_n:
        lines.append(
            f"   Trades: {h_n}   ✅{hub.get('wins', 0)}  ❌{hub.get('losses', 0)}   "
            f"WR: {hub.get('win_rate', 0)}%"
        )
        lines.append(f"   {h_icon} P&L: {_fmt_brl(hub_pnl)}")
        by = hub.get("by_setup") or {}
        if by:
            top = sorted(by.items(), key=lambda kv: abs(kv[1].get("pnl_brl") or 0), reverse=True)[:5]
            for name, d in top:
                lines.append(f"   · {name}: {d.get('n', 0)} · {_fmt_brl(d.get('pnl_brl'))}")
    else:
        lines.append("   Sem trades hoje.")

    # Crypto
    cy_icon = "📈" if cy_pnl_usd >= 0 else "📉"
    lines += ["", "🪙 <b>CRYPTO</b>"]
    if cy_rows:
        cy_w = sum(1 for r in cy_rows if float(r.get("profit") or 0) > 0)
        cy_l = sum(1 for r in cy_rows if float(r.get("profit") or 0) < 0)
        lines.append(f"   Trades: {len(cy_rows)}   ✅{cy_w}  ❌{cy_l}")
        lines.append(f"   {cy_icon} P&L: $ {cy_pnl_usd:+.2f} · {_fmt_brl(cy_pnl_brl)}")
    else:
        lines.append("   Sem trades hoje.")

    lines += [
        "",
        "━━━━━━━━━━━━━━━━━━",
        f"{comb_icon} <b>RESULTADO CONSOLIDADO:</b> {_fmt_brl(combined)}",
    ]
    hint = _supervisor_hint()
    if hint:
        lines.append(hint.lstrip("\n"))

    return "\n".join(l for l in lines if l is not None)


def _export_records() -> None:
    """Exporta os registros do Monitor para logs/monitor_trades.csv (durável,
    sem WAL — legível mesmo com a pasta em nuvem). Best-effort, nunca quebra."""
    try:
        import importlib.util, os
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        spec = importlib.util.spec_from_file_location("analise_dia", os.path.join(base, "analise_dia.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        n = mod.export_monitor_csv()
        logger.info("daily_report: exportados %d trade(s) do Monitor para CSV.", n)
    except Exception as exc:
        logger.debug("daily_report: export CSV falhou: %s", exc)


def send_daily_report() -> bool:
    """Monta e envia o relatório diário via Telegram. Retorna True se enviado."""
    _export_records()   # sempre atualiza o CSV durável, mesmo sem Telegram
    try:
        from services.config import Config
        if not Config.use_telegram():
            logger.info("daily_report: Telegram não configurado — pulando.")
            return False
        from services.telegram_notifier import _send
        text = build_daily_report()
        ok = _send(text)
        logger.info("daily_report: relatório enviado (ok=%s)", ok)
        return ok
    except Exception as exc:
        logger.warning("daily_report: falha ao enviar: %s", exc)
        return False


# ── Relatório SEMANAL (sexta, fechamento) ───────────────────────────────────

_DIA_SEMANA = {0: "seg", 1: "ter", 2: "qua", 3: "qui", 4: "sex", 5: "sáb", 6: "dom"}


def _week_curve(dias: dict) -> str:
    """Linha compacta P&L por dia útil da semana (seg→sex)."""
    monday = datetime.strptime(_week_start_brt(), "%Y-%m-%d")
    parts = []
    for i in range(5):
        d = (monday + timedelta(days=i)).strftime("%Y-%m-%d")
        if d in dias:
            v = dias[d]
            ic = "🟢" if v > 0 else ("🔴" if v < 0 else "⚪")
            parts.append(f"{_DIA_SEMANA[i]} {ic}{_fmt_brl(v)}")
    return "   " + "\n   ".join(parts) if parts else "   —"


def build_weekly_report() -> str:
    now = _now_brt()
    monday = datetime.strptime(_week_start_brt(), "%Y-%m-%d")
    mon = _monitor_week_stats()
    sca = _scalper_week()

    mon_pnl = mon.get("pnl_total_brl", 0.0) or 0.0
    sca_pnl = sca.get("pnl", 0.0) or 0.0
    combined = round(mon_pnl + sca_pnl, 2)
    comb_icon = "🟢" if combined > 0 else ("🔴" if combined < 0 else "⚪")

    # junta P&L por dia dos dois módulos para a curva consolidada
    dias_all = {}
    for src in (mon.get("dias", {}), sca.get("dias", {})):
        for d, v in src.items():
            dias_all[d] = round(dias_all.get(d, 0.0) + v, 2)

    lines = [
        f"📅 <b>RESUMO DA SEMANA</b>",
        f"<i>{monday.strftime('%d/%m')} → {now.strftime('%d/%m/%Y')}</i>",
        "━━━━━━━━━━━━━━━━━━",
        "",
        "🖥 <b>MONITOR MT5</b> (WIN/WDO)",
    ]
    if mon.get("total"):
        lines.append(f"   Trades: {mon['total']}   ✅{mon['wins']}  ❌{mon['losses']}   WR: {mon['win_rate_pct']}%")
        if mon.get("bloqueados_ia"):
            lines.append(f"   🚫 IA bloqueou: {mon['bloqueados_ia']}")
        lines.append(f"   {'📈' if mon_pnl >= 0 else '📉'} P&L semana: {_fmt_brl(mon_pnl)}")
    else:
        lines.append("   Sem trades na semana.")

    lines += ["", f"⚡ <b>SCALPER</b> ({sca.get('symbol', '—')})"]
    if sca.get("total"):
        avg = f"   Score médio: {sca['avg_score']}" if sca.get("avg_score") is not None else ""
        lines.append(f"   Trades: {sca['total']}   ✅{sca['wins']}  ❌{sca['losses']}  ➖{sca['bes']}   WR: {sca['win_rate']}%{avg}")
        lines.append(f"   {'📈' if sca_pnl >= 0 else '📉'} P&L semana: {_fmt_brl(sca_pnl)}")
    else:
        lines.append("   Sem trades na semana.")

    lines += [
        "",
        "📈 <b>Curva da semana</b> (consolidada)",
        _week_curve(dias_all),
        "",
        "━━━━━━━━━━━━━━━━━━",
        f"{comb_icon} <b>RESULTADO DA SEMANA:</b> {_fmt_brl(combined)}",
    ]
    return "\n".join(l for l in lines if l is not None)


def send_weekly_report() -> bool:
    try:
        from services.config import Config
        if not Config.use_telegram():
            logger.info("weekly_report: Telegram não configurado — pulando.")
            return False
        from services.telegram_notifier import _send
        ok = _send(build_weekly_report())
        logger.info("weekly_report: relatório enviado (ok=%s)", ok)
        return ok
    except Exception as exc:
        logger.warning("weekly_report: falha ao enviar: %s", exc)
        return False


# ── Scheduler in-process (daemon thread) ────────────────────────────────────

_last_week_sent_date = None

# Sexta, logo após o diário (18:05) → 18:07
WEEKLY_HOUR = 18
WEEKLY_MINUTE = 7


def _should_send_weekly() -> bool:
    now = _now_brt()
    if now.weekday() != 4:   # somente sexta
        return False
    if not (now.hour == WEEKLY_HOUR and
            WEEKLY_MINUTE <= now.minute < WEEKLY_MINUTE + REPORT_WINDOW):
        return False
    with _lock:
        if _last_week_sent_date == now.date():
            return False
    return True


def _should_send() -> bool:
    now = _now_brt()
    if now.weekday() >= 5:   # seg-sex apenas
        return False
    if not (now.hour == REPORT_HOUR and
            REPORT_MINUTE <= now.minute < REPORT_MINUTE + REPORT_WINDOW):
        return False
    with _lock:
        if _last_sent_date == now.date():
            return False
    return True


def _loop():
    logger.info("Daily Report Scheduler ativo: diário %02d:%02d (seg-sex) + semanal %02d:%02d (sex) BRT",
                REPORT_HOUR, REPORT_MINUTE, WEEKLY_HOUR, WEEKLY_MINUTE)
    global _last_sent_date, _last_week_sent_date
    while True:
        try:
            if _should_send():
                with _lock:
                    _last_sent_date = _now_brt().date()
                send_daily_report()
            if _should_send_weekly():
                with _lock:
                    _last_week_sent_date = _now_brt().date()
                send_weekly_report()
        except Exception as exc:
            logger.error("daily_report loop error: %s", exc)
        _time.sleep(30)


def start_daily_report_scheduler():
    """Inicia a thread daemon do relatório diário. Idempotente."""
    global _running
    if _running:
        return
    _running = True
    t = threading.Thread(target=_loop, daemon=True, name="daily-report-scheduler")
    t.start()
    logger.info("daily_report: thread iniciada (%02d:%02d BRT)", REPORT_HOUR, REPORT_MINUTE)


if __name__ == "__main__":
    import sys
    if "--week" in sys.argv:
        print(build_weekly_report())
    else:
        print(build_daily_report())
