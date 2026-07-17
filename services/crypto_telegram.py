"""
services/crypto_telegram.py — Alertas Telegram do MONITOR CRYPTO.

⚠️ MÓDULO NOVO E INDEPENDENTE. Reusa o envio base (send_async) do telegram_notifier,
mas com mensagens próprias de crypto (valores em USD + conversão BRL). Só ENVIA
mensagens (sendMessage) — NÃO escuta comandos (getUpdates), então roda na instância
crypto sem conflitar com o bot/commander do B3.

Notifica: ordem aberta, trade fechado (com resultado) e um resumo diário opcional.
"""
import os
import csv
import logging
import threading
import time as _time
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

_BRT = timezone(timedelta(hours=-3))
_CSV = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "logs", "crypto_trades.csv")


def _usdbrl(fallback: float = 5.2) -> float:
    v = os.getenv("CRYPTO_USDBRL")
    if v:
        try:
            return float(v)
        except Exception:
            pass
    return fallback


def _p(v):
    try:
        f = float(v)
        return f"{f:,.2f}" if f < 5000 else f"{f:,.0f}"
    except Exception:
        return "—"


def _money(usd, usdbrl=5.2):
    u = float(usd or 0)
    brl = u * (usdbrl or 5.2)
    return f"$ {u:+.2f} · R$ {brl:+.2f}"


def _icon(acao):
    return "📈" if acao == "COMPRA" else "📉"


def notify_entry(symbol, acao, entry, sl, tp, volume, source="auto", ai_motivo=None):
    """Ordem de crypto aberta."""
    try:
        from services.telegram_notifier import send_async
        src = {"score": "Score técnico", "impulso": "Impulso (rompimento)",
               "pullback": "Pullback macro/micro", "manual": "Manual"}.get(source, source)
        txt = (f"{_icon(acao)} <b>CRYPTO — ORDEM ABERTA</b>\n"
               f"━━━━━━━━━━━━━━━━━━\n"
               f"🪙 <b>Ativo:</b> {symbol}\n"
               f"🔀 <b>Direção:</b> {acao}\n"
               f"💰 <b>Entrada:</b> {_p(entry)}\n"
               f"🛑 <b>Stop:</b> {_p(sl)}  |  🎯 <b>TP1:</b> {_p(tp)}\n"
               f"📦 <b>Lote:</b> {volume}\n"
               f"🎯 <b>Gatilho:</b> {src}"
               + (f"\n🤖 {str(ai_motivo)[:150]}" if ai_motivo else ""))
        send_async(txt)
    except Exception as exc:
        logger.warning("crypto telegram entry: %s", exc)


def notify_exit(symbol, acao, entry_price, exit_price, profit_usd, reason, usdbrl=None):
    """Trade de crypto fechado, com resultado em USD + BRL."""
    try:
        from services.telegram_notifier import send_async
        rate = usdbrl or _usdbrl()
        p = float(profit_usd or 0)
        res = "✅ GAIN" if p > 0.01 else ("🛑 LOSS" if p < -0.01 else "➖ BE")
        reason_txt = {"TP": "🎯 TP atingido", "SL": "🛑 Stop atingido",
                      "manual": "✋ Fechado manual", "MT5": "🔄 Fechado no broker",
                      "BE": "➖ Breakeven"}.get(reason, reason or "Fechado")
        txt = (f"{_icon(acao)} <b>CRYPTO — TRADE FECHADO · {res}</b>\n"
               f"━━━━━━━━━━━━━━━━━━\n"
               f"🪙 <b>Ativo:</b> {symbol}  |  {acao or '—'}\n"
               f"📋 <b>Motivo:</b> {reason_txt}\n"
               f"💰 <b>Entrada:</b> {_p(entry_price)} → <b>Saída:</b> {_p(exit_price)}\n"
               f"📈 <b>Resultado:</b> {_money(p, rate)}")
        send_async(txt)
    except Exception as exc:
        logger.warning("crypto telegram exit: %s", exc)


def _today_rows():
    if not os.path.exists(_CSV):
        return []
    today = datetime.now(_BRT).strftime("%Y-%m-%d")
    try:
        with open(_CSV, encoding="utf-8") as f:
            return [r for r in csv.DictReader(f)
                    if r.get("resultado") and (r.get("datetime_brt") or "")[:10] == today]
    except Exception:
        return []


def notify_daily_summary():
    """Resumo do dia (crypto). Enviado 1x/dia pelo scheduler."""
    try:
        from services.telegram_notifier import send_async
        rows = _today_rows()
        if not rows:
            send_async("🪙 <b>CRYPTO — Resumo do dia</b>\nNenhum trade hoje.")
            return
        def _f(v):
            try: return float(v)
            except Exception: return 0.0
        wins = sum(1 for r in rows if _f(r.get("profit")) > 0)
        losses = sum(1 for r in rows if _f(r.get("profit")) < 0)
        be = sum(1 for r in rows if abs(_f(r.get("profit"))) < 1e-9)
        pnl = round(sum(_f(r.get("profit")) for r in rows), 2)
        rate = _usdbrl()
        wr = round(wins / (wins + losses) * 100) if (wins + losses) else 0
        emoji = "✅" if pnl > 0 else ("🛑" if pnl < 0 else "➖")
        txt = (f"🪙 <b>CRYPTO — RESUMO DO DIA</b> {emoji}\n"
               f"━━━━━━━━━━━━━━━━━━\n"
               f"📊 <b>Trades:</b> {len(rows)}  ·  ✅ {wins}  🛑 {losses}  ➖ {be}\n"
               f"🎯 <b>Win rate:</b> {wr}%\n"
               f"💰 <b>Resultado:</b> {_money(pnl, rate)}")
        send_async(txt)
    except Exception as exc:
        logger.warning("crypto telegram summary: %s", exc)


def _rows_period(period: str):
    """Lê os trades fechados do CSV filtrados por período (day/week/month)."""
    if not os.path.exists(_CSV):
        return []
    now = datetime.now(_BRT)
    today = now.strftime("%Y-%m-%d")
    if period == "week":
        start = (now - timedelta(days=now.weekday())).strftime("%Y-%m-%d")
        ok = lambda d: start <= d[:10] <= today
    elif period == "month":
        pref = today[:7]
        ok = lambda d: d[:7] == pref
    else:
        ok = lambda d: d[:10] == today
    try:
        with open(_CSV, encoding="utf-8") as f:
            return [r for r in csv.DictReader(f)
                    if r.get("resultado") and ok(r.get("datetime_brt") or "")]
    except Exception:
        return []


def _summary_text(rows, titulo):
    def _f(v):
        try: return float(v)
        except Exception: return 0.0
    if not rows:
        return f"🪙 <b>CRYPTO — {titulo}</b>\nNenhum trade no período."
    wins = sum(1 for r in rows if _f(r.get("profit")) > 0)
    losses = sum(1 for r in rows if _f(r.get("profit")) < 0)
    be = sum(1 for r in rows if abs(_f(r.get("profit"))) < 1e-9)
    pnl = round(sum(_f(r.get("profit")) for r in rows), 2)
    rate = _usdbrl()
    wr = round(wins / (wins + losses) * 100) if (wins + losses) else 0
    emoji = "✅" if pnl > 0 else ("🛑" if pnl < 0 else "➖")
    # últimos trades
    ult = ""
    for r in rows[-5:][::-1]:
        p = _f(r.get("profit"))
        ic = "✅" if p > 0 else ("🛑" if p < 0 else "➖")
        hora = (r.get("datetime_brt") or "")[11:16]
        ult += f"\n{ic} {hora} {r.get('direcao','?')} {r.get('symbol','')} · {_money(p, rate)}"
    return (f"🪙 <b>CRYPTO — {titulo}</b> {emoji}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>Trades:</b> {len(rows)}  ·  ✅ {wins}  🛑 {losses}  ➖ {be}\n"
            f"🎯 <b>Win rate:</b> {wr}%\n"
            f"💰 <b>Resultado:</b> {_money(pnl, rate)}"
            + (f"\n\n<b>Últimos:</b>{ult}" if ult else ""))


def handle_cy_command(token: str, chat_id: str, text: str) -> None:
    """Comandos de crypto (/cy*) chamados pelo commander do B3. Só leem o CSV do
    crypto (dados de hoje/semana/mês) — sem posição ao vivo (isso fica no bot dedicado)."""
    try:
        from services.telegram_notifier import send_async
        cmd = (text or "").strip().lower().split()[0] if text else ""
        if cmd in ("/cy_semana", "/cy_week"):
            send_async(_summary_text(_rows_period("week"), "SEMANA"))
        elif cmd in ("/cy_mes", "/cy_mês", "/cy_month"):
            send_async(_summary_text(_rows_period("month"), "MÊS"))
        elif cmd in ("/cy_status", "/cy", "/cy_hoje", "/crypto"):
            send_async(_summary_text(_rows_period("day"), "HOJE"))
        else:
            send_async("🪙 <b>Crypto</b>\nComandos: /cy_status (hoje), /cy_semana, /cy_mes")
    except Exception as exc:
        logger.warning("crypto handle_cy_command: %s", exc)


def start_daily_summary_scheduler(hhmm: str = None):
    """Sobe uma thread que envia o resumo diário de crypto 1x/dia (padrão 21:00 BRT).
    Configurável via env CRYPTO_SUMMARY_TIME=HH:MM. Só deve ser chamado na instância crypto."""
    target = (hhmm or os.getenv("CRYPTO_SUMMARY_TIME", "21:00")).strip()
    try:
        th, tm = [int(x) for x in target.split(":")[:2]]
    except Exception:
        th, tm = 21, 0

    def _loop():
        sent_day = None
        while True:
            try:
                now = datetime.now(_BRT)
                if now.hour == th and now.minute == tm and sent_day != now.strftime("%Y-%m-%d"):
                    notify_daily_summary()
                    sent_day = now.strftime("%Y-%m-%d")
            except Exception as exc:
                logger.warning("crypto summary loop: %s", exc)
            _time.sleep(30)

    t = threading.Thread(target=_loop, name="crypto-daily-summary", daemon=True)
    t.start()
    logger.info("Crypto daily summary scheduler ativo (%02d:%02d BRT).", th, tm)
