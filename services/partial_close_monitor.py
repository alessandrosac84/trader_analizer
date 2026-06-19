"""
partial_close_monitor.py — Monitor de background para fechamento parcial (3 contratos).

Módulo ISOLADO — executa em thread daemon separada.
Não interfere com o comportamento normal do sistema de 1 contrato.

RESPONSABILIDADE:
  Verificar o preço do MT5 a cada POLL_INTERVAL segundos.
  Quando TP1 for atingido ANTES do MT5 auto-fechar:
    1. Fecha 2 contratos no preço de mercado atual
    2. Move SL do contrato restante para entrada (breakeven)
    3. Atualiza TP do contrato restante para TP2 (alvo final)
    4. Marca estado como PARTIAL_DONE no partial_close_manager

FALLBACK DE SEGURANÇA:
  Se o MT5 fechar todos os 3 contratos no TP1 antes deste monitor disparar,
  o lucro em TP1 já está assegurado (não há perda).
  O monitor simplesmente detecta a ausência de posição e limpa o estado.

INICIALIZAÇÃO:
  Chame start_monitor() no startup do Flask app (uma única vez).
"""
import logging
import threading
import time

logger = logging.getLogger(__name__)

# ── Configuração ───────────────────────────────────────────────────────────────
POLL_INTERVAL   = 2.0   # segundos entre cada verificação de preço
_ENABLED_SYMB   = "WIN" # prefixo — só monitora símbolos WIN (mini-índice)

# ── Estado interno ─────────────────────────────────────────────────────────────
_started   = False
_start_lock = threading.Lock()


# ── Núcleo do monitor ──────────────────────────────────────────────────────────

def _get_current_price(mt5_symbol: str) -> "float | None":
    """Retorna o último preço negociado para o símbolo via MT5."""
    try:
        import MetaTrader5 as mt5
        # Inicializa com parâmetros do trade_executor (já definidos em .env)
        from services.trade_executor import _mt5_init_kwargs
        if not mt5.initialize(**_mt5_init_kwargs()):
            return None
        tick = mt5.symbol_info_tick(mt5_symbol)
        price = None
        if tick:
            price = tick.last or (tick.bid + tick.ask) / 2
        mt5.shutdown()
        return float(price) if price else None
    except Exception as exc:
        logger.debug("Monitor: erro ao buscar preço de %s: %s", mt5_symbol, exc)
        try:
            import MetaTrader5 as mt5
            mt5.shutdown()
        except Exception:
            pass
        return None


def _get_mt5_symbol_for(tv_symbol: str) -> "str | None":
    """Resolve tv_symbol → símbolo MT5 (ex: 'WIN' → 'WINQ26')."""
    try:
        from services.trade_executor import _mt5_symbol
        return _mt5_symbol(tv_symbol) or tv_symbol
    except Exception:
        return None


def _run_monitor_loop() -> None:
    """Loop principal do monitor. Roda na thread daemon."""
    logger.info("partial_close_monitor: thread iniciada (poll=%.1fs)", POLL_INTERVAL)

    while True:
        try:
            from services import partial_close_manager as pcm

            # Se não há modo parcial habilitado ou nenhum trade ativo, dorme e continua
            if not pcm.is_enabled():
                time.sleep(POLL_INTERVAL)
                continue

            # Copia a lista de símbolos rastreados (evita modificar dict durante iteração)
            symbols = list(pcm._state.keys())
            if not symbols:
                time.sleep(POLL_INTERVAL)
                continue

            for tv_symbol in symbols:
                state = pcm.get_state(tv_symbol)
                if not state or state.get("phase") != "FULL":
                    continue  # já fez partial close ou não está rastreando

                # Resolve símbolo MT5
                mt5_symbol = _get_mt5_symbol_for(tv_symbol)
                if not mt5_symbol:
                    continue

                # Busca preço atual
                current_price = _get_current_price(mt5_symbol)
                if current_price is None:
                    continue

                # Verifica se TP1 foi atingido
                if not pcm.should_partial_close(tv_symbol, current_price):
                    continue

                # ── TP1 atingido! Executa partial close ──────────────────────
                logger.info(
                    "MONITOR: TP1 atingido! %s  price=%.0f  tp1=%.0f",
                    tv_symbol, current_price, state["tp1"],
                )

                # 1) Fecha 2 contratos no mercado
                from services.trade_executor import close_partial_position, modify_position_sltp
                res_pc, err_pc = close_partial_position(tv_symbol, pcm.PARTIAL_CLOSE_VOLUME)

                if err_pc:
                    logger.warning(
                        "MONITOR: close_partial_position falhou para %s: %s",
                        tv_symbol, err_pc,
                    )
                    continue

                logger.info(
                    "MONITOR: 2 contratos fechados em %.0f (order=%s)",
                    res_pc.get("price", 0), res_pc.get("order"),
                )

                # 2) Move SL para entrada (breakeven) + TP para TP2
                entry_price = float(state.get("entry_price") or 0)
                tp2         = float(state.get("tp2") or 0)

                if entry_price and tp2:
                    ok_sltp, err_sltp = modify_position_sltp(tv_symbol, entry_price, tp2)
                    if ok_sltp:
                        logger.info(
                            "MONITOR: SL movido para entrada (%.0f), TP atualizado para TP2 (%.0f)",
                            entry_price, tp2,
                        )
                    else:
                        logger.warning("MONITOR: modify_position_sltp falhou: %s", err_sltp)

                # 3) Marca como PARTIAL_DONE
                pcm.mark_partial_done(tv_symbol)

        except Exception as loop_exc:
            logger.warning("partial_close_monitor: erro no loop: %s", loop_exc)

        time.sleep(POLL_INTERVAL)


# ── API pública ────────────────────────────────────────────────────────────────

def start_monitor() -> None:
    """
    Inicia a thread de monitoramento de background (idempotente — seguro chamar múltiplas vezes).
    Chame esta função no startup do Flask app.
    """
    global _started
    with _start_lock:
        if _started:
            logger.debug("partial_close_monitor: já iniciado, ignorando chamada duplicada.")
            return
        _started = True

    t = threading.Thread(
        target=_run_monitor_loop,
        name="partial-close-monitor",
        daemon=True,   # morre com o processo principal — não bloqueia shutdown
    )
    t.start()
    logger.info("partial_close_monitor: thread daemon iniciada com sucesso.")


def is_running() -> bool:
    """Retorna True se a thread foi iniciada (não garante que está ativa)."""
    return _started
