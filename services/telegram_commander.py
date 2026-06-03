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

def _reply(token: str, chat_id: str, text: str) -> None:
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=10,
            verify=False,
        )
    except Exception as exc:
        logger.warning("Erro ao responder comando Telegram: %s", exc)


# ── Processamento de comandos ──────────────────────────────────────────────

def _handle_command(token: str, chat_id: str, allowed_chat_id: str, text: str) -> None:
    """Processa um comando recebido."""
    # Segurança: só aceita comandos do chat autorizado
    if str(chat_id) != str(allowed_chat_id):
        logger.warning("Comando recebido de chat não autorizado: %s", chat_id)
        return

    cmd = (text or "").strip().lower().split()[0] if text else ""

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
            "O bot voltará a abrir trades automaticamente."
        )
        logger.info("Auto-trade ativado via Telegram.")

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
        # Mostra situação do trade em andamento
        TV_SYMBOL = "BMFBOVESPA:WIN1!"
        try:
            from services.trade_log import get_open_auto_trade
            from services.trade_executor import get_open_positions
            import datetime

            log = get_open_auto_trade(TV_SYMBOL)
            positions, _ = get_open_positions(TV_SYMBOL)

            if not log and not positions:
                _reply(token, chat_id, "📭 <b>Nenhum trade aberto no momento.</b>")
                return

            # Tempo em aberto
            tempo_txt = "—"
            if log and log.get("opened_at"):
                try:
                    opened = datetime.datetime.fromisoformat(log["opened_at"])
                    minutos = int((datetime.datetime.now() - opened).total_seconds() / 60)
                    if minutos >= 60:
                        tempo_txt = f"{minutos // 60}h {minutos % 60}min"
                    else:
                        tempo_txt = f"{minutos} min"
                except Exception:
                    pass

            # P&L em tempo real via MT5
            pnl_pts_rt = "—"
            pnl_brl_rt = "—"
            price_atual = "—"
            if positions:
                pos = positions[0]
                profit = pos.get("profit")
                price_atual = int(pos.get("price_current", 0)) or "—"
                if profit is not None:
                    pnl_brl_rt = f"R${profit:+.2f}"
                    # Calcula pts: 1 contrato WIN mini = R$0,20/ponto
                    volume = pos.get("volume", 1)
                    pts = round(profit / (0.20 * volume)) if volume else "—"
                    if isinstance(pts, (int, float)):
                        pnl_pts_rt = f"{'+' if pts >= 0 else ''}{int(pts)} pts"
                    pnl_icon = "📈" if (profit or 0) >= 0 else "📉"
                else:
                    pnl_icon = "📊"

            acao      = (log or {}).get("acao", positions[0].get("type_desc", "—") if positions else "—")
            entrada   = (log or {}).get("entry_price")
            sl        = (log or {}).get("sl_initial")
            tp1       = (log or {}).get("tp1_initial")
            acao_icon = "📈" if acao == "COMPRA" else "📉"

            _reply(token, chat_id,
                f"{acao_icon} <b>TRADE EM ANDAMENTO</b>\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"🔀 <b>Direção:</b> {acao}\n"
                f"💰 <b>Entrada:</b> {int(entrada) if entrada else '—'}\n"
                f"📍 <b>Preço atual:</b> {price_atual}\n"
                f"🛑 <b>Stop:</b> {int(sl) if sl else '—'}   "
                f"🎯 <b>TP1:</b> {int(tp1) if tp1 else '—'}\n"
                f"⏱ <b>Tempo aberto:</b> {tempo_txt}\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"{pnl_icon} <b>P&L atual:</b> {pnl_pts_rt} | {pnl_brl_rt}\n\n"
                f"Use /fechar para encerrar este trade."
            )
        except Exception as exc:
            logger.warning("Erro ao buscar trade: %s", exc)
            _reply(token, chat_id, f"❌ Erro ao buscar trade: {exc}")

    elif cmd in ("/fechar", "/close", "/sair"):
        TV_SYMBOL = "BMFBOVESPA:WIN1!"
        try:
            from services.trade_executor import get_open_positions, close_all_positions
            from services.trade_log import get_open_auto_trade, close_auto_trade
            import datetime

            positions, err = get_open_positions(TV_SYMBOL)
            if err or not positions:
                _reply(token, chat_id, "📭 <b>Nenhuma posição aberta para fechar.</b>")
                return

            _reply(token, chat_id, "⏳ Fechando posição no MT5...")

            results, err2 = close_all_positions(TV_SYMBOL)
            if err2:
                _reply(token, chat_id, f"❌ Erro ao fechar: {err2}")
                return

            # Registra no banco
            log = get_open_auto_trade(TV_SYMBOL)
            pos = positions[0]
            exit_price = pos.get("price_current") or pos.get("price_open")
            profit = pos.get("profit", 0)
            volume = pos.get("volume", 1)
            pts = round(profit / (0.20 * volume)) if volume else 0

            if log:
                close_auto_trade(
                    trade_id     = log["id"],
                    exit_price   = exit_price,
                    close_reason = "MANUAL",
                    pnl_pts      = pts,
                    pnl_brl      = profit,
                )
                # Notifica fechamento
                try:
                    from services.telegram_notifier import notify_trade_closed
                    notify_trade_closed(
                        tv_symbol    = TV_SYMBOL,
                        acao         = log.get("acao", "—"),
                        entry_price  = log.get("entry_price"),
                        exit_price   = exit_price,
                        close_reason = "MANUAL",
                        pnl_pts      = pts,
                        pnl_brl      = profit,
                    )
                except Exception:
                    pass

            resultado = "✅ GAIN" if profit >= 0 else "❌ STOP"
            pnl_brl_txt = f"R${profit:+.2f}"
            pnl_pts_txt = f"{'+' if pts >= 0 else ''}{int(pts)} pts"

            _reply(token, chat_id,
                f"✋ <b>TRADE FECHADO MANUALMENTE — {resultado}</b>\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"📍 <b>Saída:</b> {int(exit_price) if exit_price else '—'}\n"
                f"📈 <b>Resultado:</b> {pnl_pts_txt} | {pnl_brl_txt}"
            )
            logger.info("Trade fechado manualmente via Telegram.")

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
        TV_SYMBOL = "BMFBOVESPA:WIN1!"
        try:
            from services.trade_log import get_open_auto_trade
            from services.trade_executor import get_open_positions, modify_position_sl

            log = get_open_auto_trade(TV_SYMBOL)
            positions, _ = get_open_positions(TV_SYMBOL)

            if not positions:
                _reply(token, chat_id, "📭 <b>Nenhuma posição aberta para mover o stop.</b>")
                return

            entry = log.get("entry_price") if log else None
            if not entry:
                entry = positions[0].get("price_open")

            if not entry:
                _reply(token, chat_id, "❌ Não foi possível determinar o preço de entrada.")
                return

            pos = positions[0]
            profit = pos.get("profit", 0)
            if profit <= 0:
                _reply(token, chat_id,
                    f"⚠️ Trade está negativo (R${profit:+.2f}).\n"
                    f"Só é seguro mover para breakeven quando estiver positivo.\n"
                    f"Confirma mesmo assim? Envie /be_confirmar"
                )
                return

            ok, err = modify_position_sl(TV_SYMBOL, float(entry))
            if ok:
                _reply(token, chat_id,
                    f"✅ <b>Stop movido para BREAKEVEN</b>\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"📍 Novo stop: {int(entry)}\n"
                    f"🔒 Trade garantido no zero a zero."
                )
            else:
                _reply(token, chat_id, f"❌ Erro ao mover stop: {err}")

        except Exception as exc:
            logger.warning("Erro ao mover stop: %s", exc)
            _reply(token, chat_id, f"❌ Erro: {exc}")

    elif cmd == "/be_confirmar":
        TV_SYMBOL = "BMFBOVESPA:WIN1!"
        try:
            from services.trade_log import get_open_auto_trade
            from services.trade_executor import get_open_positions, modify_position_sl
            log = get_open_auto_trade(TV_SYMBOL)
            positions, _ = get_open_positions(TV_SYMBOL)
            if not positions:
                _reply(token, chat_id, "📭 Nenhuma posição aberta.")
                return
            entry = (log.get("entry_price") if log else None) or positions[0].get("price_open")
            ok, err = modify_position_sl(TV_SYMBOL, float(entry))
            if ok:
                _reply(token, chat_id, f"✅ Stop movido para {int(entry)} (breakeven confirmado).")
            else:
                _reply(token, chat_id, f"❌ Erro: {err}")
        except Exception as exc:
            _reply(token, chat_id, f"❌ Erro: {exc}")

    elif cmd == "/resumo":
        try:
            from services.trade_log import auto_trades_stats
            from services.telegram_notifier import notify_daily_summary
            stats = auto_trades_stats(today_only=True)
            notify_daily_summary(stats, tv_symbol="BMFBOVESPA:WIN1!")
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
            "📊 <b>Informação</b>\n"
            "/status — Bot ativo/pausado + P&L do dia\n"
            "/trade — Trade em andamento (P&L, tempo, preço)\n"
            "/resumo — Resumo completo do dia agora\n\n"
            "⚙️ <b>Controle do bot</b>\n"
            "/stop — Pausa novos trades\n"
            "/ativar — Reativa o auto-trade\n"
            "/pausar_hoje — Pausa hoje, reativa amanhã às 08:50\n"
            "/meta 500 — Para automaticamente ao ganhar R$500\n\n"
            "🔧 <b>Gestão de posição</b>\n"
            "/trade — Ver trade aberto + P&L atual\n"
            "/mover_stop — Move stop para breakeven (entrada)\n"
            "/fechar — Fecha o trade agora\n\n"
            "/help — Esta mensagem"
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
                if text.startswith("/"):
                    _handle_command(token, msg_chat_id, chat_id, text)

        except requests.exceptions.ReadTimeout:
            pass  # normal no long-polling
        except Exception as exc:
            logger.warning("Telegram polling error: %s", exc)
            time.sleep(10)


# ── Inicialização ──────────────────────────────────────────────────────────

_commander_started = False


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
    t = threading.Thread(
        target=_polling_loop,
        args=(Config.TELEGRAM_BOT_TOKEN, Config.TELEGRAM_CHAT_ID),
        daemon=True,
        name="TelegramCommander",
    )
    t.start()
    logger.info("Telegram Commander iniciado em background.")
