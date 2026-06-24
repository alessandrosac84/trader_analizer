"""
services/market_close_scheduler.py
------------------------------------
Fechamento automatico de todas as posicoes abertas ao final do pregao.

Horario padrao: 18:00 BRT (segunda a sexta).
Loop daemon roda a cada 20s verificando se chegou a hora.

REGRA: nao altera modulos existentes (Monitor MT5 / Scalper).
"""
import logging
import threading
import time as _time
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)
_BRT = timezone(timedelta(hours=-3))

# ── Configuracao ───────────────────────────────────────────────────────────────
CLOSE_HOUR   = 18        # hora de fechamento em BRT
CLOSE_MINUTE = 0         # minuto
CLOSE_WINDOW = 3         # janela de tentativas (minutos apos CLOSE_MINUTE)

# Todos os simbolos monitorados pelo bot
_SYMBOLS = [
    "BMFBOVESPA:WIN1!",
    "BMFBOVESPA:WDO1!",
]

# ── Estado interno ─────────────────────────────────────────────────────────────
_last_close_date = None
_lock            = threading.Lock()
_running         = False
_last_result     = {"status": "idle", "ts": None, "detail": None}


def _now_brt() -> datetime:
    return datetime.now(_BRT)


def _should_close() -> bool:
    """True se estamos na janela de fechamento e ainda nao fechamos hoje."""
    now = _now_brt()

    # Apenas segunda (0) a sexta (4)
    if now.weekday() >= 5:
        return False

    # Janela: CLOSE_HOUR:CLOSE_MINUTE ate CLOSE_HOUR:(CLOSE_MINUTE+CLOSE_WINDOW)
    in_window = (
        now.hour == CLOSE_HOUR and
        CLOSE_MINUTE <= now.minute < CLOSE_MINUTE + CLOSE_WINDOW
    )
    if not in_window:
        return False

    with _lock:
        if _last_close_date == now.date():
            return False   # ja fechou hoje
    return True


def _do_close():
    """Fecha todas as posicoes de todos os simbolos monitorados."""
    global _last_close_date, _last_result
    now = _now_brt()

    with _lock:
        _last_close_date = now.date()

    ts_str = now.strftime("%H:%M:%S BRT %d/%m/%Y")
    logger.warning("MARKET CLOSE SCHEDULER: %s — iniciando fechamento de todas as posicoes", ts_str)

    all_results = []
    all_errors  = []

    for symbol in _SYMBOLS:
        try:
            from services.trade_executor import close_all_positions
            results, error = close_all_positions(symbol)
            if error:
                all_errors.append(f"{symbol}: {error}")
                logger.error("MARKET CLOSE [%s] erro: %s", symbol, error)
            elif results:
                all_results.extend(results)
                logger.info("MARKET CLOSE [%s]: %d posicao(oes) fechada(s)", symbol, len(results))
            else:
                logger.info("MARKET CLOSE [%s]: sem posicoes abertas", symbol)
        except Exception as exc:
            all_errors.append(f"{symbol}: {exc}")
            logger.error("MARKET CLOSE [%s] excecao: %s", symbol, exc)

    # Registra no log do CPE
    try:
        from services.risk_settings_service import log_risk_event
        detail = f"{len(all_results)} posicao(oes) fechada(s)"
        if all_errors:
            detail += f" | Erros: {'; '.join(all_errors)}"
        log_risk_event(
            event_type   = "MARKET_CLOSE_AUTO",
            event_reason = f"Fechamento automatico {ts_str} — {detail}",
        )
    except Exception:
        pass

    with _lock:
        _last_result = {
            "status":  "error" if all_errors else "ok",
            "ts":      ts_str,
            "closed":  len(all_results),
            "errors":  all_errors,
        }

    logger.warning("MARKET CLOSE SCHEDULER: concluido — %d fechadas, %d erros",
                   len(all_results), len(all_errors))


def _scheduler_loop():
    """Loop principal — verifica a cada 20 segundos."""
    logger.info(
        "Market Close Scheduler ativo: fechamento automatico as %02d:%02d BRT (seg-sex)",
        CLOSE_HOUR, CLOSE_MINUTE
    )
    while True:
        try:
            if _should_close():
                _do_close()
        except Exception as exc:
            logger.error("market_close_scheduler loop error: %s", exc)
        _time.sleep(20)


def start_scheduler():
    """Inicia a thread daemon do scheduler. Idempotente."""
    global _running
    if _running:
        return
    _running = True
    t = threading.Thread(
        target=_scheduler_loop,
        daemon=True,
        name="market-close-scheduler"
    )
    t.start()
    logger.info("market_close_scheduler: thread iniciada (fecha as %02d:%02d BRT)", CLOSE_HOUR, CLOSE_MINUTE)


def get_status() -> dict:
    """Retorna status atual para diagnostico."""
    now = _now_brt()
    with _lock:
        last_result = dict(_last_result)
        last_close  = str(_last_close_date) if _last_close_date else None
    return {
        "running":          _running,
        "close_time":       f"{CLOSE_HOUR:02d}:{CLOSE_MINUTE:02d} BRT",
        "current_time_brt": now.strftime("%H:%M:%S"),
        "weekday":          now.strftime("%A"),
        "last_close_date":  last_close,
        "last_result":      last_result,
    }
