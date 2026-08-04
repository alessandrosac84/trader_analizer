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
import json
import logging
import threading
import time as _time
from datetime import datetime, timezone, timedelta
from pathlib import Path

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
               "pullback": "Pullback macro/micro", "orderflow": "Order Flow (delta)",
               "london_handoff": "London Handoff (Ásia→Londres)",
               "us_drift": "US Drift (edge discovery)",
               "manual": "Manual"}.get(source, source)
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
        reason_txt = {
            "TP": "🎯 TP atingido", "SL": "🛑 Stop atingido",
            "manual": "✋ Fechado manual", "MANUAL": "✋ Fechado manual",
            "MT5": "🔄 Fechado no broker",
            "BE": "➖ Breakeven",
            "GIVEBACK": "🔒 Proteção de lucro (devolveu do pico)",
            "MICRO": "⚡ MICRO virou contra",
            "REVERSAO": "⚠️ Reversão de sinal",
            "SAIDA_ANTECIPADA": "🛡 Saída antecipada (não foi TP)",
            "TIME_STOP": "⏱ Time-stop",
            "MANAGER": "🛡 Manager",
        }.get(reason, None)
        if reason_txt is None:
            # reason pode ser "GIVEBACK · Devolveu 55%..."
            code = str(reason or "").split("·")[0].strip().upper()
            reason_txt = {
                "GIVEBACK": "🔒 Proteção de lucro (devolveu do pico)",
                "MICRO": "⚡ MICRO virou contra",
                "REVERSAO": "⚠️ Reversão de sinal",
                "SAIDA_ANTECIPADA": "🛡 Saída antecipada (não foi TP)",
                "TIME_STOP": "⏱ Time-stop",
                "MANUAL": "✋ Fechado manual",
            }.get(code, reason or "Fechado")
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


def _crypto_api_base() -> str:
    return (os.getenv("CRYPTO_API_BASE") or "http://127.0.0.1:5001").rstrip("/")


def _live_positions() -> list:
    """Posições abertas: API do runtime crypto (:5001), fallback heartbeat."""
    out = []
    try:
        import urllib.request
        url = _crypto_api_base() + "/api/crypto/positions"
        with urllib.request.urlopen(url, timeout=4) as r:
            d = json.loads(r.read().decode())
        if d.get("ok"):
            for p in d.get("items") or []:
                out.append({
                    "symbol": p.get("symbol") or "—",
                    "dir": p.get("type") or p.get("dir") or "?",
                    "price_open": p.get("price_open"),
                    "price_current": p.get("price_current"),
                    "profit": p.get("profit"),
                    "sl": p.get("sl"),
                    "tp": p.get("tp"),
                    "volume": p.get("volume"),
                    "ticket": p.get("ticket"),
                    "setup": p.get("comment") or "crypto",
                })
            return out
    except Exception as exc:
        logger.debug("crypto live positions api: %s", exc)
    try:
        hb = Path(__file__).resolve().parent.parent / "logs" / "hb_crypto_runtime.json"
        if hb.exists():
            d = json.loads(hb.read_text(encoding="utf-8"))
            for p in d.get("positions") or []:
                typ = p.get("type")
                if typ in (0, "0", "BUY", "buy"):
                    dir_ = "COMPRA"
                elif typ in (1, "1", "SELL", "sell"):
                    dir_ = "VENDA"
                else:
                    dir_ = p.get("dir") or (typ if isinstance(typ, str) else "?")
                out.append({
                    "symbol": p.get("symbol") or "—",
                    "dir": dir_,
                    "price_open": p.get("price_open") or p.get("entry"),
                    "price_current": p.get("price_current") or p.get("price"),
                    "profit": p.get("profit"),
                    "sl": p.get("sl"),
                    "tp": p.get("tp"),
                    "volume": p.get("volume"),
                    "setup": p.get("comment") or p.get("setup") or "crypto",
                })
    except Exception as exc:
        logger.debug("crypto live positions hb: %s", exc)
    return out


def _fmt_open_block(poss: list) -> str:
    if not poss:
        return "\n\n📭 Sem posição crypto aberta."
    rate = _usdbrl()
    lines = ["\n\n<b>Abertas agora:</b>"]
    for p in poss:
        dir_ = p.get("dir") or "?"
        ic = "📈" if str(dir_).upper().startswith("C") else "📉"
        pr = p.get("profit")
        lines.append(
            f"\n{ic} <b>{p.get('symbol')}</b> {dir_}"
            f"\n   Entrada {_p(p.get('price_open'))} → agora {_p(p.get('price_current'))}"
            f"\n   SL {_p(p.get('sl'))} · TP {_p(p.get('tp'))}"
            f"\n   Lote {p.get('volume') or '—'} · P&L {_money(pr, rate)}"
        )
    return "".join(lines)


def _summary_text(rows, titulo, open_poss=None):
    def _f(v):
        try: return float(v)
        except Exception: return 0.0
    rate = _usdbrl()
    open_poss = open_poss if open_poss is not None else []
    open_block = _fmt_open_block(open_poss)
    if not rows:
        base = (
            f"🪙 <b>CRYPTO — {titulo}</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>Fechados:</b> 0 no período"
        )
        if open_poss:
            float_pnl = round(sum(_f(p.get("profit")) for p in open_poss), 2)
            base += (
                f"\n🔴 <b>Abertas:</b> {len(open_poss)}"
                f"\n💰 <b>P&L flutuante:</b> {_money(float_pnl, rate)}"
            )
        return base + open_block
    wins = sum(1 for r in rows if _f(r.get("profit")) > 0)
    losses = sum(1 for r in rows if _f(r.get("profit")) < 0)
    be = sum(1 for r in rows if abs(_f(r.get("profit"))) < 1e-9)
    pnl = round(sum(_f(r.get("profit")) for r in rows), 2)
    wr = round(wins / (wins + losses) * 100) if (wins + losses) else 0
    emoji = "✅" if pnl > 0 else ("🛑" if pnl < 0 else "➖")
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
            + (f"\n\n<b>Últimos:</b>{ult}" if ult else "")
            + open_block)


def handle_cy_command(token: str, chat_id: str, text: str) -> None:
    """Comandos /cy* — CSV fechados + posições ao vivo via API :5001."""
    def reply(msg: str) -> None:
        try:
            import requests
            r = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": msg, "parse_mode": "HTML"},
                timeout=10, verify=False,
            )
            if not r.ok:
                logger.warning("crypto telegram reply: %s %s", r.status_code, r.text[:200])
                # fallback chat do .env
                from services.telegram_notifier import send_async
                send_async(msg)
        except Exception as exc:
            logger.warning("crypto telegram reply error: %s", exc)
            try:
                from services.telegram_notifier import send_async
                send_async(msg)
            except Exception:
                pass

    try:
        cmd = (text or "").strip().lower().split()[0] if text else ""
        open_poss = _live_positions()
        if cmd in ("/cy_semana", "/cy_week"):
            reply(_summary_text(_rows_period("week"), "SEMANA", open_poss))
        elif cmd in ("/cy_mes", "/cy_mês", "/cy_month"):
            reply(_summary_text(_rows_period("month"), "MÊS", open_poss))
        elif cmd in ("/cy_status", "/cy", "/cy_hoje", "/crypto", "/cy_trade", "/cy_pos"):
            reply(_summary_text(_rows_period("day"), "HOJE", open_poss))
        else:
            reply("🪙 <b>Crypto</b>\nComandos: /cy_status · /cy_semana · /cy_mes")
    except Exception as exc:
        logger.warning("crypto handle_cy_command: %s", exc)
        try:
            reply(f"🪙 Crypto erro: {exc}")
        except Exception:
            pass


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
