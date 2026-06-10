"""
telegram_notifier.py — Alertas via Telegram para o Monitor MT5.

Notificações enviadas:
  - Trade automático executado
  - IA bloqueou um trade
  - Trade fechado (TP1 / STOP / reversão / manual)
  - Resumo consolidado periódico (a cada 2h durante o pregão)

Configuração (.env):
  TELEGRAM_BOT_TOKEN=7123456789:AAFxxx...
  TELEGRAM_CHAT_ID=123456789
"""
import logging
import threading
import requests

logger = logging.getLogger(__name__)

# ── Envio base ─────────────────────────────────────────────────────────────

def _send(text: str, parse_mode: str = "HTML") -> bool:
    """Envia mensagem via Telegram Bot API. Retorna True se ok."""
    from services.config import Config
    if not Config.use_telegram():
        return False
    url = f"https://api.telegram.org/bot{Config.TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={
            "chat_id":    Config.TELEGRAM_CHAT_ID,
            "text":       text,
            "parse_mode": parse_mode,
        }, timeout=10, verify=False)
        if not r.ok:
            logger.warning("Telegram API error: %s %s", r.status_code, r.text[:200])
        return r.ok
    except Exception as exc:
        logger.warning("Telegram send error: %s", exc)
        return False


def send_async(text: str) -> None:
    """Dispara envio em thread separada para não bloquear a requisição Flask."""
    t = threading.Thread(target=_send, args=(text,), daemon=True)
    t.start()


# ── Formatadores ──────────────────────────────────────────────────────────

def _fmt_pts(pts) -> str:
    if pts is None:
        return "—"
    return ("+" if pts >= 0 else "") + str(pts) + " pts"


def _fmt_brl(brl) -> str:
    if brl is None:
        return "—"
    s = f"R${brl:+.2f}".replace(".", ",")
    return s


def _acao_icon(acao: str) -> str:
    return "📈" if acao == "COMPRA" else "📉"


# ── Mensagens específicas ─────────────────────────────────────────────────

def notify_trade_executed(
    tv_symbol: str,
    acao: str,
    score: int,
    entry_price,
    sl,
    tp1,
    order_id=None,
    ai_veredito: str = None,
    ai_confianca: int = None,
) -> None:
    """Notifica que um novo trade automático foi executado."""
    icon  = _acao_icon(acao)
    ativo = tv_symbol.split(":")[-1]
    ai_line = ""
    if ai_veredito:
        ai_line = f"\n🤖 <b>IA:</b> {ai_veredito}" + (f" ({ai_confianca}%)" if ai_confianca else "")

    text = (
        f"{icon} <b>TRADE EXECUTADO</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📌 <b>Ativo:</b> {ativo}\n"
        f"🔀 <b>Direção:</b> {acao}\n"
        f"📊 <b>Score:</b> |{abs(score) if score else '—'}|\n"
        f"💰 <b>Entrada:</b> {int(entry_price) if entry_price else '—'}\n"
        f"🛑 <b>Stop:</b> {int(sl) if sl else '—'}\n"
        f"🎯 <b>TP1:</b> {int(tp1) if tp1 else '—'}"
        + (f"\n🔖 <b>Ordem:</b> #{order_id}" if order_id else "")
        + ai_line
    )
    send_async(text)


def notify_ia_blocked(
    tv_symbol: str,
    acao: str,
    score: int,
    motivo: str,
) -> None:
    """Notifica que a IA bloqueou um trade."""
    ativo  = tv_symbol.split(":")[-1]
    motivo_short = (motivo or "")[:150]
    text = (
        f"🚫 <b>IA BLOQUEOU O TRADE</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📌 <b>Ativo:</b> {ativo}\n"
        f"🔀 <b>Direção:</b> {acao} (score |{abs(score) if score else '—'}|)\n"
        f"💬 <b>Motivo:</b> {motivo_short}"
    )
    send_async(text)


def notify_trade_closed(
    tv_symbol: str,
    acao: str,
    entry_price,
    exit_price,
    close_reason: str,
    pnl_pts,
    pnl_brl,
) -> None:
    """Notifica fechamento de um trade com resultado."""
    ativo  = tv_symbol.split(":")[-1]
    icon   = _acao_icon(acao)

    reason_icons = {
        "TP1":              "🎯 TP1 atingido!",
        "TP2":              "🎯🎯 TP2 atingido!",
        "STOP":             "🛑 Stop Loss atingido",
        "REVERSAO":         "⚡ Reversão de sinal",
        "MANUAL":           "✋ Fechado manualmente (Telegram)",
        "FECHADO_MT5":      "🔄 Fechado pelo MT5 (motivo desconhecido)",
        "FECHADO_MT5_GAIN": "🔄 Fechado pelo MT5 com lucro",
        "FECHADO_MT5_STOP": "🔄 Fechado pelo MT5 com perda",
        "TRAILING":         "📐 Trailing stop",
    }
    reason_txt = reason_icons.get(close_reason, close_reason or "Fechado")

    pts_str = _fmt_pts(pnl_pts)
    brl_str = _fmt_brl(pnl_brl)
    resultado = "✅ GAIN" if (pnl_pts or 0) > 0 else ("❌ STOP" if (pnl_pts or 0) < 0 else "➖ Neutro")

    text = (
        f"{icon} <b>TRADE FECHADO — {resultado}</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📌 <b>Ativo:</b> {ativo} | {acao}\n"
        f"📋 <b>Motivo:</b> {reason_txt}\n"
        f"💰 <b>Entrada:</b> {int(entry_price) if entry_price else '—'} → "
        f"<b>Saída:</b> {int(exit_price) if exit_price else '—'}\n"
        f"📈 <b>Resultado:</b> {pts_str} | {brl_str}"
    )
    send_async(text)


def notify_daily_summary(
    stats: dict,
    tv_symbol: str = "WIN",
) -> None:
    """Envia resumo consolidado dos trades do dia."""
    ativo       = tv_symbol.split(":")[-1]
    total       = stats.get("total", 0)
    wins        = stats.get("wins", 0)
    losses      = stats.get("losses", 0)
    bloq        = stats.get("bloqueados_ia", 0)
    win_rate    = stats.get("win_rate_pct", 0)
    pnl_pts     = stats.get("pnl_total_pts", 0)
    pnl_brl     = stats.get("pnl_total_brl", 0.0)

    pnl_icon = "📈" if pnl_pts >= 0 else "📉"
    text = (
        f"📊 <b>RESUMO — {ativo}</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🔢 <b>Trades hoje:</b> {total}\n"
        f"✅ <b>Gains:</b> {wins}   ❌ <b>Stops:</b> {losses}\n"
        f"🎯 <b>Win Rate:</b> {win_rate}%\n"
        + (f"🚫 <b>IA bloqueou:</b> {bloq}\n" if bloq else "")
        + f"{pnl_icon} <b>P&L Total:</b> {_fmt_pts(pnl_pts)} | {_fmt_brl(pnl_brl)}"
    )
    send_async(text)


# ── Scheduler de resumo a cada 2h ─────────────────────────────────────────

_summary_timer = None


def _schedule_next_summary(tv_symbol: str) -> None:
    """Agenda o próximo resumo em 2h se ainda estiver dentro do horário de pregão."""
    import datetime, threading
    now = datetime.datetime.now()
    # Pregão: 9h–18h (horário local)
    if now.hour < 9 or now.hour >= 18:
        return   # fora do pregão, não agenda

    global _summary_timer
    if _summary_timer:
        _summary_timer.cancel()

    def _run():
        try:
            from services.trade_log import auto_trades_stats
            stats = auto_trades_stats(today_only=True)
            notify_daily_summary(stats, tv_symbol)
        except Exception as exc:
            logger.warning("Erro ao enviar resumo periódico: %s", exc)
        _schedule_next_summary(tv_symbol)   # re-agenda

    _summary_timer = threading.Timer(2 * 3600, _run)
    _summary_timer.daemon = True
    _summary_timer.start()
    logger.info("Próximo resumo Telegram agendado em 2h")


def start_periodic_summary(tv_symbol: str = "BMFBOVESPA:WIN1!") -> None:
    """Inicia o ciclo de resumos periódicos (chamar no startup do app)."""
    from services.config import Config
    if not Config.use_telegram():
        logger.info("Telegram não configurado — resumo periódico desativado.")
        return
    _schedule_next_summary(tv_symbol)
    logger.info("Resumo periódico Telegram iniciado para %s", tv_symbol)
