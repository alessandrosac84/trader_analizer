"""
crypto_auto_runtime.py — Auto-trade do Monitor Crypto RODANDO NO SERVIDOR.

PROBLEMA que resolve: o /api/crypto/auto-check era "dirigido pelo cliente" —
só a ABA ABERTA do painel disparava a avaliação, e só para o ativo selecionado.
Navegador fechado (ou em outro ativo) = motor cego. Resultado: semanas sem
trade mesmo com setups validados (ORDER_FLOW, LONDON_HANDOFF) no ar.

SOLUÇÃO: uma thread daemon que, a cada CHECK_SEC, chama o MESMO endpoint
/api/crypto/auto-check em processo (Flask test_client) para TODOS os símbolos
com auto ligado. Nada da lógica de trade é duplicado: cooldown, locks, dupla
checagem de posição, vetos, Telegram — tudo continua no crypto_bp. O painel
segue funcionando normalmente como visualização/controle (ligar/desligar).

Símbolos que sobem ligados: .env CRYPTO_AUTO_ON=ALL (default) ou lista "XAUUSD,ETHUSD".
"""
import logging
import os
import threading
import time

logger = logging.getLogger(__name__)

CHECK_SEC = 45          # intervalo entre varreduras (M5 fecha a cada 300s)
STALE_HOURS = 16        # time-stop: tenta fechar posição mais velha que isso
                        # (ORDER_FLOW 8h · US_DRIFT ~15h). Sem Telegram — só log.
_started = False
# ticket já notificado/tentado no time-stop (evita spam a cada ciclo se mercado fechado)
_stale_tried = set()


def _close_stale(sym):
    try:
        from services.crypto_service import get_positions, close_position
        pos, err = get_positions(sym)
        if err or not pos:
            return
        for p in pos:
            t0 = p.get("time") or 0
            if not t0 or (time.time() - t0) <= STALE_HOURS * 3600:
                continue
            ticket = p.get("ticket") or f"{sym}:{t0}"
            if ticket in _stale_tried:
                # Já tentamos nesta sessão — não reenvia Telegram nem martela close
                # a cada ciclo (mercado fechado = posição fica, loop antigo spamava).
                continue
            _stale_tried.add(ticket)
            if len(_stale_tried) > 50:
                _stale_tried.clear()
                _stale_tried.add(ticket)
            logger.info(
                "[AutoRuntime] %s TIME-STOP: aberta >%dh (ticket %s) — 1 tentativa de close; sem Telegram",
                sym, STALE_HOURS, ticket,
            )
            try:
                from blueprints.crypto_bp import _set_close_intent
                _set_close_intent(sym, "TIME_STOP", f"Posição aberta >{STALE_HOURS}h")
            except Exception:
                pass
            try:
                close_position(sym)
            except Exception as exc:
                logger.info("[AutoRuntime] %s TIME-STOP close falhou (mercado fechado?): %s",
                            sym, exc)
            break
    except Exception as exc:
        logger.debug("[AutoRuntime] close_stale %s: %s", sym, exc)


def _manage_open(client, sym):
    """Aplica MANAGER (BE/trailing/giveback/micro) no servidor — sem depender do painel."""
    try:
        r = client.get(
            f"/api/crypto/manage?symbol={sym}&interval=15&apply=1&close=1",
            headers={"X-Crypto-Runtime": "1"},
        )
        data = r.get_json(silent=True) or {}
        if data.get("closed"):
            logger.info("[AutoRuntime] %s MANAGER fechou: %s",
                        sym, data.get("close_code") or data.get("reason"))
        elif data.get("applied") is not None:
            logger.debug("[AutoRuntime] %s MANAGER SL→%s (%s)",
                         sym, data.get("applied"), data.get("recommendation"))
    except Exception as exc:
        logger.debug("[AutoRuntime] manage %s: %s", sym, exc)


def _symbols():
    try:
        from services.crypto_config import SYMBOLS
        return list(SYMBOLS)
    except Exception:
        return ["BTCUSD", "ETHUSD", "XAUUSD", "EURUSD", "GBPUSD"]


def _loop(app):
    from blueprints.crypto_bp import _autost
    from services.engine_heartbeat import beat
    client = app.test_client()
    logger.info("CryptoAutoRuntime iniciado — avaliando %s a cada %ss (independente do navegador)",
                _symbols(), CHECK_SEC)
    while True:
        motivos = {}
        positions = []
        try:
            # Publica posições abertas p/ /trade no Telegram do B3 (via hb JSON)
            try:
                from services.crypto_service import get_positions
                from services.crypto_config import SYMBOLS
                for sym in list(SYMBOLS):
                    poss, err = get_positions(sym)
                    if err or not poss:
                        continue
                    for p in poss:
                        typ = p.get("type")
                        if typ in (0, "0") or str(typ).upper() in ("COMPRA", "BUY"):
                            dir_ = "COMPRA"
                        elif typ in (1, "1") or str(typ).upper() in ("VENDA", "SELL"):
                            dir_ = "VENDA"
                        else:
                            dir_ = p.get("dir") or str(typ or "?")
                        positions.append({
                            "symbol": sym,
                            "dir": dir_,
                            "type": typ,
                            "price_open": p.get("price_open") or p.get("entry"),
                            "price_current": p.get("price_current") or p.get("price"),
                            "profit": p.get("profit"),
                            "sl": p.get("sl"),
                            "tp": p.get("tp"),
                            "comment": p.get("comment") or "crypto",
                            "volume": p.get("volume"),
                        })
            except Exception as exc:
                logger.debug("[AutoRuntime] positions hb: %s", exc)

            for sym in _symbols():
                try:
                    if not _autost(sym)["enabled"]:
                        motivos[sym] = "auto OFF (painel)"
                        continue
                    _close_stale(sym)
                    _manage_open(client, sym)
                    # source=runtime → crypto_bp faz acquire(blocking, timeout)
                    # em vez de non-blocking, para não perder o ciclo p/ a UI.
                    r = client.post(
                        "/api/crypto/auto-check",
                        json={"symbol": sym, "interval": "15", "source": "runtime"},
                        headers={"X-Crypto-Runtime": "1"},
                    )
                    data = r.get_json(silent=True) or {}
                    act = data.get("action")
                    if act == "EXECUTED":
                        motivos[sym] = "EXECUTOU " + str(data.get("source"))
                        logger.info("[AutoRuntime] %s EXECUTOU %s via %s",
                                    sym, data.get("acao"), data.get("source"))
                    elif act == "FAILED":
                        src = data.get("source") or "?"
                        err = data.get("error") or data.get("reason") or "execução falhou"
                        motivos[sym] = f"FAILED {src}: {err}"
                        logger.warning("[AutoRuntime] %s FAILED via %s: %s", sym, src, err)
                    elif act == "BLOCKED":
                        motivos[sym] = str(data.get("reason") or "BLOCKED")
                        logger.info("[AutoRuntime] %s bloqueado: %s", sym, data.get("reason"))
                    else:
                        motivos[sym] = str(data.get("reason") or act or "?")
                    # Telemetria near-miss no HB (compacta)
                    nm = data.get("near_miss")
                    if isinstance(nm, dict):
                        motivos[sym + "_nm"] = (
                            f"hh={nm.get('broker_hour')} in={nm.get('n_in_window')}/"
                            f"{nm.get('n_armed')} · {nm.get('hint') or ''}"
                        )[:180]
                except Exception as exc:
                    motivos[sym] = f"erro: {exc}"
                    logger.warning("[AutoRuntime] %s erro: %s", sym, exc)
        except Exception as exc:
            logger.warning("[AutoRuntime] loop erro: %s", exc)
        beat("crypto_runtime", {"motivos": motivos, "positions": positions})
        time.sleep(CHECK_SEC)


def start_crypto_auto_runtime(app) -> None:
    """Chamar uma vez no boot da instância CRYPTO. Idempotente."""
    global _started
    if _started:
        return
    if os.getenv("MT5_PROFILE", "b3").strip().lower() != "crypto":
        logger.info("CryptoAutoRuntime: perfil não é crypto — não iniciado.")
        return
    _started = True
    t = threading.Thread(target=_loop, args=(app,), daemon=True, name="CryptoAutoRuntime")
    t.start()
