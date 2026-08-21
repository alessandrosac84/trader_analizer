"""
services/monitor_position_guard.py — Proteção de LUCRO automática do Monitor MT5.

O Monitor MT5 (magic 20260505) não tinha aplicador de gestão: as sugestões de
breakeven/trailing só apareciam no painel, nunca eram aplicadas. Este daemon
monitora as posições do Monitor a cada POLL segundos e aplica a proteção de lucro
(giveback + quase-alvo) do b3_profit_guard — travando o stop ou fechando no lucro.

ISOLADO: thread daemon própria. Age SÓ em posições com magic 20260505. Não toca em
V7 (714), WinGo, Crypto, EOD ou qualquer outro motor. Só protege lucro (nunca
alarga stop nem fecha no prejuízo). Liga/desliga: MONITOR_GUARD_ENABLED=0.
"""
import logging
import os
import threading
import time

logger = logging.getLogger(__name__)

MAGIC = 20260505
POLL = 5
_state = {}          # ticket -> {"peak": float, "risk0": float}
_started = False


def _mt5():
    import MetaTrader5 as mt5
    if mt5.terminal_info() is None:
        try:
            from services.trade_executor import _mt5_init_kwargs
            mt5.initialize(**_mt5_init_kwargs())
        except Exception:
            mt5.initialize()
    return mt5


def _price(mt5, pos):
    tick = mt5.symbol_info_tick(pos.symbol)
    if not tick:
        return None
    return (tick.bid if pos.type == 0 else tick.ask) or tick.last or ((tick.bid + tick.ask) / 2)


def _modify_sl(mt5, pos, new_sl):
    req = {"action": mt5.TRADE_ACTION_SLTP, "symbol": pos.symbol,
           "position": pos.ticket, "sl": float(new_sl), "tp": float(pos.tp or 0)}
    r = mt5.order_send(req)
    return bool(r and r.retcode == mt5.TRADE_RETCODE_DONE)


def _close(mt5, pos):
    tick = mt5.symbol_info_tick(pos.symbol)
    if not tick:
        return False
    otype = mt5.ORDER_TYPE_SELL if pos.type == 0 else mt5.ORDER_TYPE_BUY
    px = tick.bid if pos.type == 0 else tick.ask
    req = {"action": mt5.TRADE_ACTION_DEAL, "symbol": pos.symbol, "volume": float(pos.volume),
           "type": otype, "position": pos.ticket, "price": float(px), "deviation": 30,
           "magic": MAGIC, "type_filling": mt5.ORDER_FILLING_RETURN}
    for _ in range(3):
        r = mt5.order_send(req)
        if r and r.retcode == mt5.TRADE_RETCODE_DONE:
            return True
        time.sleep(0.5)
    return False


def _notify(txt):
    try:
        from services.telegram_notifier import send_async
        send_async(txt)
    except Exception:
        pass


def _loop():
    from services.b3_profit_guard import check
    logger.info("MonitorPositionGuard iniciado (magic %s, poll %ss)", MAGIC, POLL)
    while True:
        try:
            if os.getenv("MONITOR_GUARD_ENABLED", "1").strip() == "0":
                time.sleep(POLL); continue
            mt5 = _mt5()
            if mt5.terminal_info() is None:
                time.sleep(POLL); continue
            positions = [p for p in (mt5.positions_get() or []) if p.magic == MAGIC]
            live = {p.ticket for p in positions}
            for tk in list(_state):
                if tk not in live:
                    _state.pop(tk, None)
            for pos in positions:
                price = _price(mt5, pos)
                if not price:
                    continue
                entry = float(pos.price_open or 0)
                sl = float(pos.sl or 0)
                tp = float(pos.tp or 0)
                buy = pos.type == 0
                stt = _state.setdefault(pos.ticket, {"peak": 0.0, "risk0": 0.0})
                if stt["risk0"] <= 0:
                    stt["risk0"] = abs(entry - sl) if sl else 0.0
                risk = stt["risk0"] or (abs(entry - sl) if sl else 0.0)
                favor = (price - entry) if buy else (entry - price)
                stt["peak"] = max(stt["peak"], favor)
                g = check(entry, price, sl, tp, stt["peak"], risk, buy)
                if g["close"]:
                    if _close(mt5, pos):
                        logger.info("MonitorGuard %s: %s (%s)", pos.symbol, g["code"], g["reason"])
                        _notify(f"🛡 <b>MONITOR — PROTEÇÃO {g['code']}</b>\n{pos.symbol}: {g['reason']}")
                        _state.pop(pos.ticket, None)
                elif g["new_sl"]:
                    if _modify_sl(mt5, pos, g["new_sl"]):
                        logger.info("MonitorGuard %s: stop travado em %.1f", pos.symbol, g["new_sl"])
        except Exception as exc:
            logger.debug("MonitorPositionGuard loop: %s", exc)
        time.sleep(POLL)


def start_monitor_position_guard():
    """Chamar no boot da instância B3. Idempotente. Não roda no perfil crypto."""
    global _started
    if _started:
        return
    if os.getenv("MT5_PROFILE", "b3").strip().lower() == "crypto":
        return
    _started = True
    threading.Thread(target=_loop, daemon=True, name="MonitorPositionGuard").start()
