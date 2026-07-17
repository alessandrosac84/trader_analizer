"""
services/b3_close_watcher.py — Detecta e registra o FECHAMENTO dos trades do Monitor
MT5 (B3) em BACKGROUND, no servidor — independente do navegador estar aberto/ativo.

Motivo: a detecção de fecho estava presa no polling do frontend. Quando a aba fica em
2º plano, o navegador estrangula os timers e o fecho não era registrado (trade ficava
"aberto" e o P&L zerado). Este watcher roda numa thread própria e resolve isso.

⚠️ Só CAPTURA o resultado (marca o trade como fechado + P&L REAL do histórico do MT5).
NÃO toma nenhuma decisão de trade, não abre nada, não mexe em SL/TP. Roda só na
instância do B3 (XP).
"""
import logging
import threading
import time

logger = logging.getLogger(__name__)

SYMBOLS = ["BMFBOVESPA:WIN1!", "BMFBOVESPA:WDO1!"]
CONFIRM = 3          # leituras sem posição p/ confirmar o fecho (evita fecho falso)
_no_pos = {}
_started = {"on": False}


def _realized(ticket):
    """P&L realizado + preço de saída REAIS do histórico do MT5 p/ o ticket da posição."""
    try:
        import MetaTrader5 as mt5
        if not ticket:
            return None
        if not mt5.initialize():
            return None
        deals = mt5.history_deals_get(position=int(ticket)) or []
        mt5.shutdown()
        outs = [d for d in deals if getattr(d, "entry", None) == mt5.DEAL_ENTRY_OUT]
        if not outs:
            return None
        outs.sort(key=lambda d: d.time)
        profit = sum(float(d.profit) + float(getattr(d, "swap", 0) or 0)
                     + float(getattr(d, "commission", 0) or 0) for d in outs)
        price = float(getattr(outs[-1], "price", 0) or 0)
        return (price or None), round(profit, 2)
    except Exception:
        try:
            import MetaTrader5 as mt5
            mt5.shutdown()
        except Exception:
            pass
        return None


def _reason_and_pts(exit_price, log):
    sl = log.get("sl_initial"); tp1 = log.get("tp1_initial")
    entry = log.get("entry_price"); acao = log.get("acao", "")
    sym = (log.get("tv_symbol") or "").upper()
    tol = 50 if "WIN" in sym else (5 if "WDO" in sym else (float(entry) * 0.005 if entry else 0.5))
    pts = None
    if entry:
        diff = (exit_price - float(entry)) if acao == "COMPRA" else (float(entry) - exit_price)
        pts = round(diff) if ("WIN" in sym or "WDO" in sym) else None
    reason = "FECHADO_MT5"
    if entry and abs(exit_price - float(entry)) <= tol:
        reason = "BREAKEVEN"
    elif sl and abs(exit_price - float(sl)) <= tol:
        reason = "STOP"
    elif tp1 and abs(exit_price - float(tp1)) <= tol:
        reason = "TP1"
    elif entry:
        lucro = (exit_price > float(entry)) if acao == "COMPRA" else (exit_price < float(entry))
        reason = "FECHADO_MT5_GAIN" if lucro else "FECHADO_MT5_STOP"
    return reason, pts


def _check(sym):
    from services.trade_log import get_open_auto_trade, close_auto_trade
    from services.trade_executor import get_open_positions
    log = get_open_auto_trade(sym)
    if not log:
        _no_pos[sym] = 0
        return
    positions, err = get_open_positions(sym)
    if err:
        return   # erro de MT5 → não fecha no escuro
    if positions:
        _no_pos[sym] = 0
        return
    _no_pos[sym] = _no_pos.get(sym, 0) + 1
    if _no_pos[sym] < CONFIRM:
        return
    _no_pos[sym] = 0
    real = _realized(log.get("mt5_ticket"))
    if not real or not real[0]:
        return   # P&L ainda não disponível no histórico → tenta no próximo ciclo
    exit_price, pnl_brl = real
    reason, pts = _reason_and_pts(exit_price, log)
    if not get_open_auto_trade(sym):   # re-checa (evita corrida com o endpoint manage)
        return
    close_auto_trade(log["id"], exit_price, reason, pts, pnl_brl)
    logger.info("B3 watcher: trade id=%d fechado (%s) exit=%.1f pnl=%s", log["id"], reason, exit_price, pnl_brl)
    try:
        from services.daily_report import _export_records
        _export_records()
    except Exception:
        pass


def start_watcher():
    if _started["on"]:
        return
    _started["on"] = True

    def _loop():
        while True:
            for sym in SYMBOLS:
                try:
                    _check(sym)
                except Exception as exc:
                    logger.warning("B3 close watcher %s: %s", sym, exc)
            # backfill dos que fecharam antes sem P&L capturado
            try:
                from services.trade_log import backfill_missing_pnl
                backfill_missing_pnl()
            except Exception:
                pass
            time.sleep(5)

    threading.Thread(target=_loop, name="b3-close-watcher", daemon=True).start()
    logger.info("B3 close watcher em background ativo (5s).")
