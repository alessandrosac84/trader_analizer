"""
services/scalper_telegram.py
Integração Telegram exclusiva do módulo Scalper.

Notificações (outbound):
  - Trade aberto  (direção, preço, TP, SL, score, VWAP, sessão)
  - Trade fechado (P&L, motivo, duração)
  - Auto-trade ligado/desligado

Comandos recebidos via telegram_commander.py (prefixo /sc):
  /sc  ou  /sc_status   — posição aberta + auto-trade + stats da sessão
  /sc_on                — liga auto-trade do scalper
  /sc_off               — desliga auto-trade do scalper
  /sc_fechar            — fecha posição aberta agora
  /sc_be                — move SL para breakeven
  /sc_log               — últimos 10 trades + win rate

NÃO modifica nenhum outro módulo além de:
  - services/scalper_telegram.py  (novo)
  - blueprints/scalper_bp.py      (adiciona chamadas de notificação)
  - services/telegram_commander.py (3 linhas: elif /sc -> dispatch)
"""
import logging
import os
import threading
import requests

logger = logging.getLogger(__name__)


def _scalper_symbol() -> str:
    """Símbolo ativo do scalper para os comandos do Telegram.

    Fonte de verdade: env SCALPER_TG_SYMBOL (fallback SCALPER_SUPERVISOR_SYMBOL),
    default BITN26 — o contrato atualmente operado. Antes estava 'WDON26'
    fixo no código, o que fazia /sc_status, /sc_fechar e /sc_be agirem no
    contrato errado.
    """
    return (os.getenv("SCALPER_TG_SYMBOL")
            or os.getenv("SCALPER_SUPERVISOR_SYMBOL")
            or "BITN26").upper().strip()


# ── Envio base (reutiliza config sem depender do notifier original) ────────

def _send(text: str) -> bool:
    from services.config import Config
    if not Config.use_telegram():
        return False
    url = f"https://api.telegram.org/bot{Config.TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={
            "chat_id":    Config.TELEGRAM_CHAT_ID,
            "text":       text,
            "parse_mode": "HTML",
        }, timeout=10, verify=False)
        return r.ok
    except Exception as exc:
        logger.warning("scalper_telegram._send error: %s", exc)
        return False


def _send_async(text: str) -> None:
    threading.Thread(target=_send, args=(text,), daemon=True).start()


def _fmt_brl(v) -> str:
    try:
        return f"R${float(v):+.2f}".replace(".", ",")
    except Exception:
        return "—"


# ── Notificações outbound ─────────────────────────────────────────────────

def notify_entry(
    symbol: str, direcao: str, price: float,
    tp: float, sl: float, score: float,
    vwap_context: str, session: str,
    auto: bool, mode: str,
) -> None:
    """Envia notificação de abertura de posição."""
    icon    = "📈" if direcao == "COMPRA" else "📉"
    dir_txt = "COMPRA" if direcao == "COMPRA" else "VENDA&nbsp; "
    mode_txt = "🎮 SIM" if mode == "SIM" else "💰 REAL"
    auto_txt = "🤖 Auto" if auto else "✋ Manual"

    vwap_icon = {"BULL": "🟢", "BEAR": "🔴", "NEUTRO": "🟡"}.get(vwap_context, "⚪")
    sess_icon = {"PRIME": "⚡", "BOM": "✅", "PERIGOSO": "⚠️", "FECHAMENTO": "🕐"}.get(session, "")

    score_bar = "▓" * min(10, int(score / 10)) + "░" * max(0, 10 - int(score / 10))

    _send_async(
        f"⚡ <b>SCALPER — ENTRADA {dir_txt}</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{icon} <b>Ativo:</b> {symbol}   {mode_txt}   {auto_txt}\n"
        f"💰 <b>Entrada:</b> {price:.2f}   "
        f"🎯 <b>TP:</b> {tp:.2f}   "
        f"🛑 <b>SL:</b> {sl:.2f}\n"
        f"📊 <b>Score:</b> {score:.0f}/100  <code>{score_bar}</code>\n"
        f"{vwap_icon} <b>VWAP:</b> {vwap_context}   "
        f"{sess_icon} <b>Sessão:</b> {session}\n"
        f"\n📟 Use /sc_status para acompanhar"
    )


def notify_exit(
    symbol: str, direcao: str,
    entry_price: float, exit_price: float,
    profit: float, exit_reason: str, duration_s,
) -> None:
    """Envia notificação de fechamento de posição."""
    icon    = "📈" if direcao == "COMPRA" else "📉"
    resultado = "✅ GAIN" if profit > 0.01 else ("❌ LOSS" if profit < -0.01 else "➖ BE")
    reason_txt = {
        "TP":         "🎯 TP atingido",
        "SL":         "🛑 Stop Loss",
        "manual":     "✋ Fechado manualmente",
        "time_exit":  "⏱ Saída por tempo",
        "breakeven":  "⚖️ Breakeven",
    }.get(exit_reason, exit_reason or "Fechado")

    dur_txt = ""
    if duration_s:
        try:
            s = int(float(duration_s))
            dur_txt = f"   ⏱ {s//60}m{s%60:02d}s" if s >= 60 else f"   ⏱ {s}s"
        except Exception:
            pass

    _send_async(
        f"⚡ <b>SCALPER — FECHADO {resultado}</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{icon} {symbol} | {direcao}\n"
        f"💰 {entry_price:.2f} → {exit_price:.2f}{dur_txt}\n"
        f"📋 <b>Motivo:</b> {reason_txt}\n"
        f"📈 <b>Resultado:</b> {_fmt_brl(profit)}"
    )


def notify_auto_changed(enabled: bool, reason: str = "") -> None:
    """Notifica mudança no estado do auto-trade."""
    if enabled:
        _send_async(f"⚡ <b>SCALPER</b> — 🤖 Auto-Trade <b>ATIVADO</b>{' — ' + reason if reason else ''}")
    else:
        _send_async(f"⚡ <b>SCALPER</b> — 🛑 Auto-Trade <b>DESATIVADO</b>{' — ' + reason if reason else ''}")


# ── Handler de comandos /sc_* (chamado por telegram_commander) ────────────

def handle_sc_command(token: str, chat_id: str, text: str) -> None:
    """
    Processa comandos do Scalper recebidos pelo commander do Monitor.
    Chamado de telegram_commander._handle_command quando cmd.startswith('/sc').
    """
    def reply(msg):
        try:
            requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": msg, "parse_mode": "HTML"},
                timeout=10, verify=False,
            )
        except Exception as exc:
            logger.warning("scalper_telegram.reply error: %s", exc)

    cmd = (text or "").strip().lower().split()[0]

    # ── /sc ou /sc_status ─────────────────────────────────────────────────
    if cmd in ("/sc", "/sc_status"):
        try:
            from blueprints.scalper_bp import _session, _auto
            from services.scalper_service import get_scalper_position, get_sim_mode
            symbol = _scalper_symbol()
            pos, _ = get_scalper_position(symbol)
            auto_on  = _auto.get("enabled", False)
            sim_on   = get_sim_mode()
            trades   = _session.get("trades", 0)
            wins     = _session.get("wins", 0)
            losses   = _session.get("losses", 0)
            pnl      = _session.get("pnl", 0.0)
            wr       = round(wins / trades * 100) if trades > 0 else 0

            pos_txt = "📭 Sem posição aberta"
            if pos:
                tipo   = "📈 COMPRA" if pos.get("type") == 0 else "📉 VENDA"
                preco  = pos.get("price_open", 0)
                lucro  = pos.get("profit", 0)
                pos_txt = (
                    f"🔴 <b>Posição aberta:</b> {tipo}\n"
                    f"   Entrada: {preco:.2f}   P&L: {_fmt_brl(lucro)}"
                )

            pnl_icon = "📈" if pnl >= 0 else "📉"
            reply(
                f"⚡ <b>STATUS SCALPER</b>\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"🤖 Auto-Trade: <b>{'✅ ON' if auto_on else '🛑 OFF'}</b>"
                + (f"   🎮 <i>SIMULAÇÃO</i>" if sim_on else "") + "\n\n"
                f"{pos_txt}\n\n"
                f"📊 <b>Sessão hoje</b>\n"
                f"   Trades: {trades}   ✅{wins}  ❌{losses}   WR: {wr}%\n"
                f"   {pnl_icon} P&L: {_fmt_brl(pnl)}\n\n"
                f"Comandos: /sc_on  /sc_off  /sc_fechar  /sc_be  /sc_log"
            )
        except Exception as exc:
            reply(f"❌ Erro ao buscar status: {exc}")

    # ── /sc_on ────────────────────────────────────────────────────────────
    elif cmd == "/sc_on":
        try:
            from blueprints.scalper_bp import _auto
            _auto["enabled"]            = True
            _auto["signal_count"]       = 0
            _auto["paused_until"]       = 0.0   # limpa pausa automática
            _auto["consecutive_losses"] = 0     # reseta contador
            notify_auto_changed(True, "via Telegram")
            reply("⚡ <b>SCALPER</b> — 🤖 Auto-Trade <b>ATIVADO</b>\n✅ Pausa automática removida (contador zerado)")
        except Exception as exc:
            reply(f"❌ Erro: {exc}")

    # ── /sc_off ───────────────────────────────────────────────────────────
    elif cmd == "/sc_off":
        try:
            from blueprints.scalper_bp import _auto
            _auto["enabled"] = False
            _auto["signal_count"] = 0
            notify_auto_changed(False, "via Telegram")
            reply("⚡ <b>SCALPER</b> — 🛑 Auto-Trade <b>DESATIVADO</b>")
        except Exception as exc:
            reply(f"❌ Erro: {exc}")

    # ── /sc_fechar ────────────────────────────────────────────────────────
    elif cmd == "/sc_fechar":
        try:
            from services.scalper_service import close_scalper_position
            info, err = close_scalper_position(_scalper_symbol())
            if err:
                reply(f"❌ Erro ao fechar: {err}")
                return
            profit  = info.get("profit", 0)
            price   = info.get("price", 0)
            res_txt = "✅ GAIN" if profit > 0.01 else ("❌ LOSS" if profit < -0.01 else "➖ BE")
            reply(
                f"⚡ <b>SCALPER — FECHADO {res_txt}</b>\n"
                f"📍 Saída: {price:.2f}\n"
                f"📈 Resultado: {_fmt_brl(profit)}"
            )
        except Exception as exc:
            reply(f"❌ Erro: {exc}")

    # ── /sc_be ────────────────────────────────────────────────────────────
    elif cmd in ("/sc_be", "/sc_breakeven"):
        try:
            from services.scalper_service import move_sl_to_breakeven
            result, err = move_sl_to_breakeven(_scalper_symbol())
            if err:
                reply(f"❌ Erro: {err}")
            else:
                new_sl = (result or {}).get("new_sl", "")
                reply(f"⚡ <b>SCALPER</b> — ⚖️ Stop movido para entrada{(': ' + str(new_sl)) if new_sl else ''}")
        except Exception as exc:
            reply(f"❌ Erro: {exc}")

    # ── /sc_log ───────────────────────────────────────────────────────────
    elif cmd == "/sc_log":
        try:
            from services.trade_logger import get_log_summary
            d = get_log_summary()
            if not d.get("trades"):
                reply("⚡ <b>SCALPER Log</b> — Nenhum trade registrado ainda.")
                return

            wr       = d.get("win_rate", 0)
            wr_icon  = "🟢" if wr >= 55 else ("🟡" if wr >= 45 else "🔴")
            pnl      = d.get("total_pnl", 0)
            pnl_icon = "📈" if pnl >= 0 else "📉"

            # Últimos 5 trades
            last = (d.get("last_trades") or [])[:5]
            trades_txt = ""
            for t in last:
                res   = t.get("resultado", "?")
                res_i = "✅" if res == "WIN" else ("❌" if res == "LOSS" else "➖")
                hora  = (t.get("datetime_brt") or "")[-8:-3]  # HH:MM
                dir_  = "▲" if t.get("direcao") == "COMPRA" else "▼"
                sc    = t.get("score", "?")
                pnl_t = _fmt_brl(float(t.get("profit") or 0))
                trades_txt += f"  {res_i} {hora} {dir_} sc:{sc} {pnl_t}\n"

            reply(
                f"⚡ <b>SCALPER — Log de Trades</b>\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"🔢 Total: {d['trades']}   "
                f"✅{d.get('wins',0)}  ❌{d.get('losses',0)}  ➖{d.get('bes',0)}\n"
                f"{wr_icon} <b>Win Rate: {wr:.1f}%</b>   "
                f"Score médio: {d.get('avg_score',0):.0f}\n"
                f"{pnl_icon} <b>P&L Total: {_fmt_brl(pnl)}</b>\n\n"
                f"<b>Últimos trades:</b>\n{trades_txt.rstrip()}"
            )
        except Exception as exc:
            reply(f"❌ Erro: {exc}")

    # ── /sc_help ──────────────────────────────────────────────────────────
    else:
        reply(
            "⚡ <b>SCALPER — Comandos:</b>\n\n"
            "/sc ou /sc_status — Status atual\n"
            "/sc_on  — Liga auto-trade\n"
            "/sc_off — Desliga auto-trade\n"
            "/sc_fechar — Fecha posição agora\n"
            "/sc_be — Move stop para breakeven\n"
            "/sc_log — Últimos trades + win rate"
        )
