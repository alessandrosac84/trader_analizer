"""
telegram_commander.py — Recebe comandos do Telegram para controlar o auto-trade.

Comandos suportados:
  /stop        — pausa o auto-trade remotamente
  /ativar      — reativa o auto-trade
  /pausar_hoje — pausa pelo resto do dia e reativa amanhã automaticamente
  /status      — situação atual (ativo/inativo, P&L do dia, trades)
  /trade       — trade em andamento: P&L, tempo aberto, preço atual
  /fechar      — fecha o trade em andamento agora (fecha no MT5)
  /mover_stop  — move o stop para o preço de entrada (breakeven)
  /resumo      — força envio do resumo do dia agora
  /meta X      — define meta de gain do dia em R$; ao atingir, para o bot
  /help        — lista os comandos disponíveis

Funciona via long-polling (getUpdates), em thread daemon separada.
"""
import logging
import threading
import time
import datetime
import requests

logger = logging.getLogger(__name__)

# ── Estado global do auto-trade ────────────────────────────────────────────
_autotrade_enabled = True
_meta_diaria_brl   = None   # None = sem meta definida
_lock = threading.Lock()

# ── Símbolos monitorados (Monitor MT5 + Scalper) ───────────────────────────
_MONITORED_SYMBOLS = [
    "BMFBOVESPA:WIN1!",
    "BMFBOVESPA:WDO1!",
    "BMFBOVESPA:PETR4",
]

_SYM_LABEL = {
    "BMFBOVESPA:WIN1!":  "WIN",
    "BMFBOVESPA:WDO1!":  "WDO",
    "BMFBOVESPA:PETR4":  "PETR4",
}

_ALIAS_TO_SYM = {
    "WIN":   "BMFBOVESPA:WIN1!",
    "WDO":   "BMFBOVESPA:WDO1!",
    "PETR4": "BMFBOVESPA:PETR4",
    "PETR":  "BMFBOVESPA:PETR4",
}


def _sym_label(tv_symbol: str) -> str:
    """Retorna rótulo curto do símbolo (ex: WIN, WDO, PETR4)."""
    return _SYM_LABEL.get(tv_symbol.upper(), tv_symbol.split(":")[-1])


def _calc_pts(tv_symbol: str, profit: float, volume: float) -> str:
    """Converte P&L em BRL para pontos conforme o ativo."""
    sym = tv_symbol.upper()
    try:
        if "WIN" in sym:
            pts = round(profit / (0.20 * volume)) if volume else None
        elif "WDO" in sym:
            pts = round(profit / (10.0 * volume)) if volume else None
        else:
            return "—"
        if pts is None:
            return "—"
        return f"{'+' if pts >= 0 else ''}{int(pts)} pts"
    except Exception:
        return "—"


def _resolve_sym(arg: str) -> "str | None":
    """Resolve alias curto (WIN, WDO, PETR4) para tv_symbol completo."""
    return _ALIAS_TO_SYM.get((arg or "").upper().strip())


def is_autotrade_enabled() -> bool:
    with _lock:
        return _autotrade_enabled


def set_autotrade_enabled(value: bool) -> None:
    global _autotrade_enabled
    with _lock:
        _autotrade_enabled = value


def get_meta_diaria() -> "float | None":
    with _lock:
        return _meta_diaria_brl


def set_meta_diaria(value: "float | None") -> None:
    global _meta_diaria_brl
    with _lock:
        _meta_diaria_brl = value


def check_meta_atingida(token: str, chat_id: str) -> None:
    """Chama ao final de cada trade fechado para ver se a meta foi atingida."""
    meta = get_meta_diaria()
    if meta is None or not is_autotrade_enabled():
        return
    try:
        from services.trade_log import auto_trades_stats
        stats = auto_trades_stats(today_only=True)
        pnl_brl = stats.get("pnl_total_brl", 0.0)
        if pnl_brl >= meta:
            set_autotrade_enabled(False)
            _reply(token, chat_id,
                f"🎯 <b>META DO DIA ATINGIDA!</b>\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"💰 P&L atual: R${pnl_brl:+.2f}\n"
                f"🏆 Meta: R${meta:+.2f}\n\n"
                f"🛑 Auto-Trade <b>PAUSADO</b> automaticamente.\n"
                f"Use /ativar se quiser continuar operando."
            )
            logger.info("Meta diária R$%.2f atingida — auto-trade pausado.", meta)
    except Exception as exc:
        logger.warning("Erro ao verificar meta: %s", exc)


# ── Envio de resposta ──────────────────────────────────────────────────────

def _reply(token: str, chat_id: str, text: str, reply_markup: dict = None) -> None:
    try:
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json=payload,
            timeout=10,
            verify=False,
        )
    except Exception as exc:
        logger.warning("Erro ao responder comando Telegram: %s", exc)


# ── Botões / teclado persistente ───────────────────────────────────────────
# Teclado fixo abaixo da caixa de texto: tocar num botão envia o rótulo, que
# é traduzido para o comando correspondente em _resolve_button(). Assim o
# usuário não precisa digitar os comandos.

_BTN_CMD = {
    "📊 Status":            "/status",
    "📈 Trades":            "/trade",
    "📋 Resumo do dia":     "/relatorio",
    "📅 Resumo da semana":  "/relatorio_semana",
    "✋ Fechar tudo":        "/fechar",
    "⚖️ Breakeven":         "/be",
    "🛑 Parar bot":         "/stop",
    "✅ Ativar bot":        "/ativar",
    "⚡ Scalper status":    "/sc_status",
    "📜 Scalper log":       "/sc_log",
    "🟢 Scalper ON":        "/sc_on",
    "🔴 Scalper OFF":       "/sc_off",
    "⌨️ Menu":              "/menu",
}

# Layout do teclado (linhas de botões)
_KEYBOARD_ROWS = [
    ["📊 Status", "📈 Trades"],
    ["📋 Resumo do dia", "📅 Resumo da semana"],
    ["✋ Fechar tudo", "⚖️ Breakeven"],
    ["🛑 Parar bot", "✅ Ativar bot"],
    ["⚡ Scalper status", "📜 Scalper log"],
    ["🟢 Scalper ON", "🔴 Scalper OFF"],
]


def _menu_markup() -> dict:
    """ReplyKeyboardMarkup persistente com os principais comandos."""
    return {
        "keyboard": [[{"text": b} for b in row] for row in _KEYBOARD_ROWS],
        "resize_keyboard": True,
        "is_persistent": True,
        "input_field_placeholder": "Toque num botão ou digite /help",
    }


def _resolve_button(text: str) -> "str | None":
    """Traduz o rótulo de um botão para o comando correspondente."""
    return _BTN_CMD.get((text or "").strip())


# ── Processamento de comandos ──────────────────────────────────────────────

def _handle_command(token: str, chat_id: str, allowed_chat_id: str, text: str) -> None:
    """Processa um comando recebido."""
    # Segurança: só aceita comandos do chat autorizado
    if str(chat_id) != str(allowed_chat_id):
        logger.warning("Comando recebido de chat não autorizado: %s", chat_id)
        return

    # Se veio de um botão do teclado, traduz o rótulo para o comando
    _btn = _resolve_button(text)
    if _btn:
        text = _btn

    cmd = (text or "").strip().lower().split()[0] if text else ""

    # ── Despacha comandos do Scalper (/sc*) sem interferir no Monitor ─────
    if cmd.startswith("/sc"):
        try:
            from services.scalper_telegram import handle_sc_command
            handle_sc_command(token, chat_id, text)
        except Exception as _sc_exc:
            logger.warning("Scalper command dispatch error: %s", _sc_exc)
        return

    if cmd in ("/stop", "/desativar", "/pausar"):
        set_autotrade_enabled(False)
        _reply(token, chat_id,
            "🛑 <b>Auto-Trade DESATIVADO</b>\n\n"
            "Nenhum novo trade automático será aberto.\n"
            "Use /ativar para reativar quando quiser."
        )
        logger.info("Auto-trade desativado via Telegram.")

    elif cmd in ("/ativar", "/start", "/on"):
        set_autotrade_enabled(True)
        _reply(token, chat_id,
            "✅ <b>Auto-Trade ATIVADO</b>\n\n"
            "O bot voltará a abrir trades automaticamente.\n"
            "⌨️ Use os botões abaixo para os comandos principais.",
            reply_markup=_menu_markup(),
        )
        logger.info("Auto-trade ativado via Telegram.")

    elif cmd in ("/menu", "/teclado", "/botoes", "/botões"):
        _reply(token, chat_id,
            "⌨️ <b>Menu de comandos ativado</b>\n\n"
            "Toque nos botões abaixo da caixa de texto para executar os "
            "comandos sem precisar digitar.",
            reply_markup=_menu_markup(),
        )

    elif cmd in ("/relatorio", "/relatório", "/relatorio_dia"):
        try:
            from services.daily_report import send_daily_report
            ok = send_daily_report()
            if not ok:
                _reply(token, chat_id, "⚠️ Não foi possível enviar o relatório (Telegram configurado?).")
        except Exception as exc:
            _reply(token, chat_id, f"❌ Erro ao gerar relatório do dia: {exc}")

    elif cmd in ("/relatorio_semana", "/resumo_semana", "/semana"):
        try:
            from services.daily_report import send_weekly_report
            ok = send_weekly_report()
            if not ok:
                _reply(token, chat_id, "⚠️ Não foi possível enviar o resumo semanal.")
        except Exception as exc:
            _reply(token, chat_id, f"❌ Erro ao gerar resumo da semana: {exc}")

    elif cmd == "/status":
        enabled = is_autotrade_enabled()
        status_icon = "✅ ATIVO" if enabled else "🛑 PAUSADO"
        try:
            from services.trade_log import auto_trades_stats
            stats = auto_trades_stats(today_only=True)
            total    = stats.get("total", 0)
            wins     = stats.get("wins", 0)
            losses   = stats.get("losses", 0)
            bloq     = stats.get("bloqueados_ia", 0)
            win_rate = stats.get("win_rate_pct", 0)
            pnl_pts  = stats.get("pnl_total_pts", 0)
            pnl_brl  = stats.get("pnl_total_brl", 0.0)
            pnl_icon = "📈" if pnl_pts >= 0 else "📉"
            stats_txt = (
                f"\n━━━━━━━━━━━━━━━━━━\n"
                f"🔢 Trades hoje: {total}\n"
                f"✅ Gains: {wins}   ❌ Stops: {losses}\n"
                f"🎯 Win Rate: {win_rate}%\n"
                + (f"🚫 IA bloqueou: {bloq}\n" if bloq else "")
                + f"{pnl_icon} P&L: {'+' if pnl_pts >= 0 else ''}{pnl_pts} pts | R${pnl_brl:+.2f}"
            )
        except Exception:
            stats_txt = "\n(Erro ao carregar estatísticas)"

        _reply(token, chat_id,
            f"📊 <b>STATUS DO BOT</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🤖 Auto-Trade: <b>{status_icon}</b>"
            + stats_txt
        )

    elif cmd in ("/trade", "/posicao", "/pos"):
        # Mostra situação de todos os trades abertos (multi-símbolo)
        try:
            from services.trade_log import get_open_auto_trade
            from services.trade_executor import get_open_positions
            import datetime

            blocos = []
            for sym in _MONITORED_SYMBOLS:
                log       = get_open_auto_trade(sym)
                positions, _ = get_open_positions(sym)
                if not log and not positions:
                    continue

                label = _sym_label(sym)

                # Tempo em aberto
                tempo_txt = "—"
                if log and log.get("opened_at"):
                    try:
                        opened  = datetime.datetime.fromisoformat(log["opened_at"])
                        minutos = int((datetime.datetime.now() - opened).total_seconds() / 60)
                        tempo_txt = f"{minutos // 60}h {minutos % 60}min" if minutos >= 60 else f"{minutos} min"
                    except Exception:
                        pass

                # P&L em tempo real via MT5
                pnl_pts_rt = "—"
                pnl_brl_rt = "—"
                price_atual = "—"
                pnl_icon   = "📊"
                if positions:
                    pos    = positions[0]
                    profit = pos.get("profit")
                    price_atual = pos.get("price_current") or "—"
                    try:
                        price_atual = int(price_atual) if price_atual != "—" else "—"
                    except Exception:
                        pass
                    if profit is not None:
                        pnl_brl_rt = f"R${profit:+.2f}"
                        volume     = pos.get("volume", 1)
                        pnl_pts_rt = _calc_pts(sym, profit, volume)
                        pnl_icon   = "📈" if profit >= 0 else "📉"

                acao      = (log or {}).get("acao") or (positions[0].get("type_desc", "—") if positions else "—")
                entrada   = (log or {}).get("entry_price")
                sl        = (log or {}).get("sl_initial")
                tp1       = (log or {}).get("tp1_initial")
                acao_icon = "📈" if acao == "COMPRA" else "📉"

                pts_str = f" | {pnl_pts_rt}" if pnl_pts_rt != "—" else ""
                blocos.append(
                    f"{acao_icon} <b>[{label}] TRADE EM ANDAMENTO</b>\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"🔀 <b>Direção:</b> {acao}\n"
                    f"💰 <b>Entrada:</b> {int(entrada) if entrada else '—'}\n"
                    f"📍 <b>Preço atual:</b> {price_atual}\n"
                    f"🛑 <b>Stop:</b> {int(sl) if sl else '—'}   "
                    f"🎯 <b>TP1:</b> {int(tp1) if tp1 else '—'}\n"
                    f"⏱ <b>Tempo aberto:</b> {tempo_txt}\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"{pnl_icon} <b>P&L atual:</b> {pnl_brl_rt}{pts_str}"
                )

            if not blocos:
                _reply(token, chat_id, "📭 <b>Nenhum trade aberto no momento.</b>")
            else:
                msg = "\n\n".join(blocos)
                if len(blocos) > 1:
                    msg += "\n\nUse /fechar WIN | /fechar WDO | /fechar PETR4 para fechar individualmente."
                else:
                    msg += "\n\nUse /fechar para encerrar este trade."
                _reply(token, chat_id, msg)

        except Exception as exc:
            logger.warning("Erro ao buscar trade: %s", exc)
            _reply(token, chat_id, f"❌ Erro ao buscar trade: {exc}")

    elif cmd in ("/fechar", "/close", "/sair"):
        # /fechar         → fecha TODAS as posições abertas
        # /fechar WIN     → fecha só WIN
        # /fechar WDO     → fecha só WDO
        # /fechar PETR4   → fecha só PETR4
        try:
            from services.trade_executor import get_open_positions, close_all_positions
            from services.trade_log import get_open_auto_trade, close_auto_trade

            parts = (text or "").strip().split()
            arg   = parts[1] if len(parts) > 1 else None

            if arg:
                sym_resolved = _resolve_sym(arg)
                if not sym_resolved:
                    _reply(token, chat_id, f"❌ Símbolo não reconhecido: <code>{arg}</code>\nUse: WIN, WDO ou PETR4")
                    return
                alvos = [sym_resolved]
            else:
                alvos = _MONITORED_SYMBOLS

            resultados = []
            alguma_posicao = False

            for sym in alvos:
                label = _sym_label(sym)
                positions, err = get_open_positions(sym)
                if err or not positions:
                    continue

                alguma_posicao = True
                _reply(token, chat_id, f"⏳ Fechando posição <b>{label}</b> no MT5...")

                results, err2 = close_all_positions(sym)
                if err2:
                    resultados.append(f"❌ <b>{label}</b>: Erro ao fechar — {err2}")
                    continue

                log        = get_open_auto_trade(sym)
                pos        = positions[0]
                exit_price = pos.get("price_current") or pos.get("price_open")
                profit     = pos.get("profit", 0)
                volume     = pos.get("volume", 1)
                pts_str    = _calc_pts(sym, profit, volume)

                if log:
                    pnl_pts_val = None
                    try:
                        sym_u = sym.upper()
                        if "WIN" in sym_u:
                            pnl_pts_val = round(profit / (0.20 * volume)) if volume else None
                        elif "WDO" in sym_u:
                            pnl_pts_val = round(profit / (10.0 * volume)) if volume else None
                    except Exception:
                        pass
                    close_auto_trade(
                        trade_id     = log["id"],
                        exit_price   = exit_price,
                        close_reason = "MANUAL",
                        pnl_pts      = pnl_pts_val,
                        pnl_brl      = profit,
                    )
                    try:
                        from services.telegram_notifier import notify_trade_closed
                        notify_trade_closed(
                            tv_symbol    = sym,
                            acao         = log.get("acao", "—"),
                            entry_price  = log.get("entry_price"),
                            exit_price   = exit_price,
                            close_reason = "MANUAL",
                            pnl_pts      = pnl_pts_val,
                            pnl_brl      = profit,
                        )
                    except Exception:
                        pass

                resultado   = "✅ GAIN" if profit >= 0 else "❌ STOP"
                exit_str    = int(exit_price) if exit_price else "—"
                pts_display = f" | {pts_str}" if pts_str != "—" else ""
                resultados.append(
                    f"✋ <b>[{label}] {resultado}</b>\n"
                    f"📍 Saída: {exit_str}   💰 R${profit:+.2f}{pts_display}"
                )
                logger.info("Trade %s fechado manualmente via Telegram.", label)

            if not alguma_posicao:
                _reply(token, chat_id, "📭 <b>Nenhuma posição aberta para fechar.</b>")
            elif resultados:
                _reply(token, chat_id, "\n\n".join(resultados))

        except Exception as exc:
            logger.warning("Erro ao fechar trade via Telegram: %s", exc)
            _reply(token, chat_id, f"❌ Erro ao fechar: {exc}")

    elif cmd in ("/pausar_hoje", "/pausarhoje"):
        set_autotrade_enabled(False)
        # Agenda reativação à meia-noite
        def _reativar_amanha():
            now = datetime.datetime.now()
            amanha = (now + datetime.timedelta(days=1)).replace(
                hour=8, minute=50, second=0, microsecond=0
            )
            segundos = (amanha - now).total_seconds()
            time.sleep(max(segundos, 0))
            set_autotrade_enabled(True)
            set_meta_diaria(None)
            _reply(token, chat_id,
                "☀️ <b>Bom dia!</b> Auto-Trade <b>REATIVADO</b> automaticamente.\n"
                "Boa sessão de hoje!"
            )
            logger.info("Auto-trade reativado automaticamente (pausar_hoje).")

        t = threading.Thread(target=_reativar_amanha, daemon=True)
        t.start()

        amanha_str = (datetime.datetime.now() + datetime.timedelta(days=1)).strftime("%d/%m às 08:50")
        _reply(token, chat_id,
            f"🌙 <b>Auto-Trade PAUSADO pelo resto do dia</b>\n\n"
            f"Nenhum novo trade será aberto hoje.\n"
            f"⏰ Reativação automática amanhã em {amanha_str}.\n\n"
            f"Use /ativar se mudar de ideia."
        )
        logger.info("Auto-trade pausado pelo resto do dia via Telegram.")

    elif cmd in ("/mover_stop", "/breakeven", "/be"):
        # /be WIN | /be WDO | /be PETR4 — move stop para breakeven no ativo indicado
        # /be — se só houver 1 posição aberta, aplica a ela; senão pede especificação
        try:
            from services.trade_log import get_open_auto_trade
            from services.trade_executor import get_open_positions, modify_position_sl

            parts = (text or "").strip().split()
            arg   = parts[1] if len(parts) > 1 else None

            if arg:
                sym_resolved = _resolve_sym(arg)
                if not sym_resolved:
                    _reply(token, chat_id, f"❌ Símbolo não reconhecido: <code>{arg}</code>\nUse: /be WIN | /be WDO | /be PETR4")
                    return
                alvos = [sym_resolved]
            else:
                # Auto-detecta: busca todos com posição aberta
                alvos = []
                from services.trade_executor import get_open_positions as _gop
                for sym in _MONITORED_SYMBOLS:
                    pos, _ = _gop(sym)
                    if pos:
                        alvos.append(sym)
                if len(alvos) == 0:
                    _reply(token, chat_id, "📭 <b>Nenhuma posição aberta para mover o stop.</b>")
                    return
                if len(alvos) > 1:
                    labels = " | ".join(f"/be {_sym_label(s)}" for s in alvos)
                    _reply(token, chat_id, f"⚠️ Há múltiplas posições abertas. Especifique:\n{labels}")
                    return

            sym   = alvos[0]
            label = _sym_label(sym)
            log   = get_open_auto_trade(sym)
            positions, _ = get_open_positions(sym)

            if not positions:
                _reply(token, chat_id, f"📭 <b>Nenhuma posição aberta em {label}.</b>")
                return

            entry = (log.get("entry_price") if log else None) or positions[0].get("price_open")
            if not entry:
                _reply(token, chat_id, "❌ Não foi possível determinar o preço de entrada.")
                return

            pos    = positions[0]
            profit = pos.get("profit", 0)
            if profit <= 0:
                _reply(token, chat_id,
                    f"⚠️ <b>[{label}]</b> Trade está negativo (R${profit:+.2f}).\n"
                    f"Só é seguro mover para breakeven quando estiver positivo.\n"
                    f"Confirma mesmo assim? Envie /be_confirmar {_sym_label(sym)}"
                )
                return

            ok, err = modify_position_sl(sym, float(entry))
            if ok:
                _reply(token, chat_id,
                    f"✅ <b>[{label}] Stop movido para BREAKEVEN</b>\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"📍 Novo stop: {int(entry)}\n"
                    f"🔒 Trade garantido no zero a zero."
                )
            else:
                _reply(token, chat_id, f"❌ Erro ao mover stop em {label}: {err}")

        except Exception as exc:
            logger.warning("Erro ao mover stop: %s", exc)
            _reply(token, chat_id, f"❌ Erro: {exc}")

    elif cmd == "/be_confirmar":
        # /be_confirmar WIN | /be_confirmar WDO | /be_confirmar PETR4
        try:
            from services.trade_log import get_open_auto_trade
            from services.trade_executor import get_open_positions, modify_position_sl

            parts = (text or "").strip().split()
            arg   = parts[1] if len(parts) > 1 else None
            sym   = _resolve_sym(arg) if arg else "BMFBOVESPA:WIN1!"
            label = _sym_label(sym)

            log       = get_open_auto_trade(sym)
            positions, _ = get_open_positions(sym)
            if not positions:
                _reply(token, chat_id, f"📭 Nenhuma posição aberta em {label}.")
                return
            entry = (log.get("entry_price") if log else None) or positions[0].get("price_open")
            ok, err = modify_position_sl(sym, float(entry))
            if ok:
                _reply(token, chat_id, f"✅ <b>[{label}]</b> Stop movido para {int(entry)} (breakeven confirmado).")
            else:
                _reply(token, chat_id, f"❌ Erro em {label}: {err}")
        except Exception as exc:
            _reply(token, chat_id, f"❌ Erro: {exc}")

    elif cmd == "/resumo":
        try:
            from services.trade_log import auto_trades_stats
            from services.telegram_notifier import notify_daily_summary
            stats = auto_trades_stats(today_only=True)
            notify_daily_summary(stats, tv_symbol="WIN+WDO+PETR4")
            logger.info("Resumo forçado via Telegram.")
        except Exception as exc:
            _reply(token, chat_id, f"❌ Erro ao gerar resumo: {exc}")

    elif cmd == "/meta":
        parts = (text or "").strip().split()
        if len(parts) < 2:
            meta_atual = get_meta_diaria()
            if meta_atual:
                _reply(token, chat_id,
                    f"🎯 Meta atual: <b>R${meta_atual:.2f}</b>\n"
                    f"Envie /meta 0 para cancelar."
                )
            else:
                _reply(token, chat_id,
                    "❓ Use: /meta 500\n"
                    "Ao atingir R$500 de gain no dia, o bot para automaticamente."
                )
            return
        try:
            valor = float(parts[1].replace(",", "."))
            if valor <= 0:
                set_meta_diaria(None)
                _reply(token, chat_id, "✅ Meta diária <b>cancelada</b>.")
            else:
                set_meta_diaria(valor)
                _reply(token, chat_id,
                    f"🎯 <b>Meta diária definida: R${valor:.2f}</b>\n\n"
                    f"Quando o P&L do dia atingir esse valor,\n"
                    f"o auto-trade será pausado automaticamente."
                )
            logger.info("Meta diária definida: R$%.2f", valor)
        except ValueError:
            _reply(token, chat_id, "❌ Valor inválido. Use: /meta 500")

    elif cmd in ("/help", "/ajuda", "/comandos"):
        _reply(token, chat_id,
            "🤖 <b>Comandos disponíveis:</b>\n\n"
            "⌨️ <b>Dica:</b> use os botões abaixo da caixa de texto (/menu).\n\n"
            "📊 <b>Informação</b>\n"
            "/status — Bot ativo/pausado + P&L do dia\n"
            "/trade — Todos os trades abertos (WIN + WDO + PETR4)\n"
            "/relatorio — Relatório do dia (Monitor + Scalper)\n"
            "/relatorio_semana — Resumo da semana\n\n"
            "⚙️ <b>Controle do bot</b>\n"
            "/stop — Pausa novos trades\n"
            "/ativar — Reativa o auto-trade\n"
            "/pausar_hoje — Pausa hoje, reativa amanhã às 08:50\n"
            "/meta 500 — Para automaticamente ao ganhar R$500\n\n"
            "🔧 <b>Gestão de posição (multi-ativo)</b>\n"
            "/fechar — Fecha TODAS as posições abertas\n"
            "/fechar WIN — Fecha só o WIN\n"
            "/fechar WDO — Fecha só o WDO\n"
            "/fechar PETR4 — Fecha só o PETR4\n"
            "/be WIN — Move stop para breakeven no WIN\n"
            "/be WDO — Move stop para breakeven no WDO\n"
            "/be PETR4 — Move stop para breakeven no PETR4\n\n"
            "⚡ <b>Scalper</b>\n"
            "/sc_status /sc_log /sc_on /sc_off /sc_fechar /sc_be\n\n"
            "/menu — Mostra os botões · /help — Esta mensagem",
            reply_markup=_menu_markup(),
        )

    else:
        _reply(token, chat_id,
            f"❓ Comando não reconhecido: <code>{text[:50]}</code>\n"
            "Use /help para ver os comandos disponíveis."
        )


# ── Loop de polling ────────────────────────────────────────────────────────

def _polling_loop(token: str, chat_id: str) -> None:
    """Long-polling do Telegram getUpdates. Roda em thread daemon."""
    offset = 0
    base_url = f"https://api.telegram.org/bot{token}"
    logger.info("Telegram Commander iniciado — aguardando comandos...")

    while True:
        try:
            r = requests.get(
                f"{base_url}/getUpdates",
                params={"offset": offset, "timeout": 30, "allowed_updates": ["message"]},
                timeout=35,
                verify=False,
            )
            if not r.ok:
                time.sleep(5)
                continue

            updates = r.json().get("result", [])
            for upd in updates:
                offset = upd["update_id"] + 1
                msg = upd.get("message", {})
                msg_chat_id = str(msg.get("chat", {}).get("id", ""))
                text = msg.get("text", "")
                # Aceita comandos (/...) e toques nos botões do teclado (rótulos)
                if text.startswith("/") or _resolve_button(text):
                    _handle_command(token, msg_chat_id, chat_id, text)

        except requests.exceptions.ReadTimeout:
            pass  # normal no long-polling
        except Exception as exc:
            logger.warning("Telegram polling error: %s", exc)
            time.sleep(10)


# ── Inicialização ──────────────────────────────────────────────────────────

_commander_started = False


def _setup_bot_ui(token: str, chat_id: str) -> None:
    """Registra a lista de comandos (menu '/') e mostra o teclado persistente."""
    # Lista que aparece ao digitar '/' no Telegram
    commands = [
        {"command": "menu",             "description": "⌨️ Mostrar os botões de comando"},
        {"command": "status",           "description": "📊 Bot ativo/pausado + P&L do dia"},
        {"command": "trade",            "description": "📈 Trades abertos (WIN/WDO/PETR4)"},
        {"command": "relatorio",        "description": "📋 Relatório do dia (Monitor+Scalper)"},
        {"command": "relatorio_semana", "description": "📅 Resumo da semana"},
        {"command": "fechar",           "description": "✋ Fecha todas as posições"},
        {"command": "be",               "description": "⚖️ Move stop para breakeven"},
        {"command": "stop",             "description": "🛑 Pausa novos trades"},
        {"command": "ativar",           "description": "✅ Reativa o auto-trade"},
        {"command": "sc_status",        "description": "⚡ Status do Scalper"},
        {"command": "sc_log",           "description": "📜 Log do Scalper"},
        {"command": "help",             "description": "❓ Ajuda"},
    ]
    try:
        requests.post(f"https://api.telegram.org/bot{token}/setMyCommands",
                      json={"commands": commands}, timeout=10, verify=False)
    except Exception as exc:
        logger.debug("setMyCommands falhou: %s", exc)
    # Faz o teclado persistente aparecer imediatamente
    try:
        _reply(token, chat_id,
               "🤖 <b>Bot online.</b> Use os botões abaixo para os comandos principais.",
               reply_markup=_menu_markup())
    except Exception as exc:
        logger.debug("startup menu falhou: %s", exc)


def start_commander() -> None:
    """Inicia o listener de comandos Telegram em background. Chamar no startup do app."""
    global _commander_started
    if _commander_started:
        return

    from services.config import Config
    if not Config.use_telegram():
        logger.info("Telegram não configurado — commander desativado.")
        return

    _commander_started = True
    try:
        _setup_bot_ui(Config.TELEGRAM_BOT_TOKEN, Config.TELEGRAM_CHAT_ID)
    except Exception as exc:
        logger.debug("_setup_bot_ui erro: %s", exc)
    t = threading.Thread(
        target=_polling_loop,
        args=(Config.TELEGRAM_BOT_TOKEN, Config.TELEGRAM_CHAT_ID),
        daemon=True,
        name="TelegramCommander",
    )
    t.start()
    logger.info("Telegram Commander iniciado em background.")
