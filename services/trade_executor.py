"""
trade_executor.py
Execução automatizada de ordens via MetaTrader5.

⚠️  USO RESTRITO A CONTAS DE TESTE (DEMO / SEM SALDO).
     Nunca executar em conta PRD com capital real sem validação extensiva.

Magic number fixo: 20260505 — identifica todas as ordens deste bot.
"""
import logging
import os
from datetime import datetime, timezone, time as dt_time

logger = logging.getLogger(__name__)

MAGIC_NUMBER   = 20260505
BOT_COMMENT    = "TradeAI-DEMO"
MAX_POSITIONS  = 1          # máximo de posições abertas simultâneas
DEFAULT_VOLUME = 1.0        # 1 mini contrato
SCORE_MIN      = 4          # |score| mínimo para executar (igual ao COMPRA/VENDA_THRESHOLD)
B3_OPEN        = dt_time(9, 0)
B3_CLOSE       = dt_time(17, 30)

# Mapeamento TV_SYMBOL -> MT5_SYMBOL para order_send
_TV_TO_MT5_TRADE = {
    "BMFBOVESPA:WIN1!": os.getenv("WIN_MT5_SYMBOL", "WINQ26"),
    "BMFBOVESPA:WDO1!": os.getenv("WDO_MT5_SYMBOL", "WDOM26"),
    "BMFBOVESPA:PETR4": "PETR4",
    "BMFBOVESPA:RADL3": "RADL3",
    "FX:EURUSD":        "EURUSD",
    "FX:GBPUSD":        "GBPUSD",
    "FX:XAUUSD":        "XAUUSD",
    "CRYPTO:BTCUSD":    os.getenv("BTC_MT5_SYMBOL", "BTCUSD"),
}


def _is_b3_open() -> bool:
    """Verifica se o pregão B3 está aberto (09:00–17:30 BRT = UTC-3)."""
    from datetime import datetime, timezone, timedelta
    now_brt = datetime.now(timezone.utc).astimezone(
        timezone(timedelta(hours=-3))
    ).time()
    return B3_OPEN <= now_brt <= B3_CLOSE


def _mt5_symbol(tv_symbol: str) -> "str | None":
    return _TV_TO_MT5_TRADE.get(tv_symbol.upper().strip())


def _mt5_init_kwargs() -> dict:
    """
    Retorna kwargs para mt5.initialize().
    Para MetaQuotes-Demo nao passa credenciais (servidor nao aceita login via Python).
    Para demais servidores (XP, etc.) passa login/password/server do .env.
    """
    login    = int(os.getenv("MT5_LOGIN", "0") or 0)
    password = os.getenv("MT5_PASSWORD", "")
    server   = os.getenv("MT5_SERVER", "")
    path     = os.getenv("MT5_PATH", "")
    kwargs: dict = {}
    if path and os.path.exists(path):
        kwargs["path"] = path
    _mq = {"metaquotes-demo", "metaquotes-demo2"}
    if login and password and server and server.lower() not in _mq:
        kwargs["login"]    = login
        kwargs["password"] = password
        kwargs["server"]   = server
    return kwargs


def get_open_positions(tv_symbol: str = None) -> "tuple[list, str | None]":
    """Retorna posições abertas do bot (filtradas por magic e opcionalmente símbolo)."""
    try:
        import MetaTrader5 as mt5
        kwargs = _mt5_init_kwargs()
        if not mt5.initialize(**kwargs):
            return [], f"MT5 não inicializado: {mt5.last_error()}"

        if tv_symbol:
            sym = _mt5_symbol(tv_symbol)
            positions = mt5.positions_get(symbol=sym) if sym else mt5.positions_get()
        else:
            positions = mt5.positions_get()

        if positions is None:
            positions = []

        ours = [dict(p._asdict()) for p in positions if p.magic == MAGIC_NUMBER]

        # Fallback: se não achou por símbolo, busca TODAS as posições do bot
        # (cobre casos de nome de símbolo ligeiramente diferente no servidor)
        if not ours and tv_symbol:
            all_pos = mt5.positions_get()
            if all_pos:
                ours = [dict(p._asdict()) for p in all_pos if p.magic == MAGIC_NUMBER]

        mt5.shutdown()
        return ours, None
    except Exception as exc:
        logger.warning("get_open_positions erro: %s", exc)
        return [], str(exc)


def execute_trade(
    tv_symbol: str,
    acao: str,
    score: int,
    entrada: "float | None" = None,
    sl: "float | None" = None,
    tp1: "float | None" = None,
    volume: float = DEFAULT_VOLUME,
    check_market_hours: bool = True,
) -> "tuple[dict | None, str | None]":
    """
    Abre uma posição a mercado via MT5.
    Retorna (result_dict | None, error_msg | None).

    Proteções embutidas:
      - Bloqueia se |score| < SCORE_MIN
      - Bloqueia se MAX_POSITIONS já atingido
      - Bloqueia fora do horário de pregão (para ativos B3)
      - Bloqueia se símbolo não mapeado
    """
    # --- Validações de segurança ---
    if acao not in ("COMPRA", "VENDA"):
        return None, "Ação inválida — apenas COMPRA ou VENDA."

    if abs(score or 0) < SCORE_MIN:
        return None, f"Score {score} abaixo do mínimo ({SCORE_MIN}) — trade bloqueado."

    mt5_symbol = _mt5_symbol(tv_symbol)
    if not mt5_symbol:
        return None, f"Símbolo '{tv_symbol}' não mapeado para execução."

    is_b3 = "BMFBOVESPA" in tv_symbol.upper()
    if check_market_hours and is_b3 and not _is_b3_open():
        return None, "Fora do horário de pregão B3 (09:00–17:30 BRT) — trade bloqueado."

    # --- Checa posições abertas ---
    open_pos, err = get_open_positions(tv_symbol)
    if err:
        return None, f"Erro ao verificar posições: {err}"
    if len(open_pos) >= MAX_POSITIONS:
        return None, f"Já existe {len(open_pos)} posição aberta — máximo {MAX_POSITIONS}."

    try:
        import MetaTrader5 as mt5

        if not mt5.initialize(**_mt5_init_kwargs()):
            return None, f"MT5 não inicializado: {mt5.last_error()}"

        # Garante símbolo visível no Market Watch
        if not mt5.symbol_select(mt5_symbol, True):
            mt5.shutdown()
            return None, f"Não foi possível selecionar '{mt5_symbol}' no Market Watch."

        tick = mt5.symbol_info_tick(mt5_symbol)
        if tick is None:
            mt5.shutdown()
            return None, f"Preço não disponível para '{mt5_symbol}'."

        order_type = mt5.ORDER_TYPE_BUY if acao == "COMPRA" else mt5.ORDER_TYPE_SELL
        price      = tick.ask              if acao == "COMPRA" else tick.bid

        # Info do símbolo: tick_size, stops_level e filling mode
        sym_info   = mt5.symbol_info(mt5_symbol)
        tick_size  = float(sym_info.trade_tick_size) if sym_info and sym_info.trade_tick_size else 1.0
        stops_lvl  = int(sym_info.trade_stops_level)  if sym_info else 0   # distância mínima em pontos
        point      = float(sym_info.point)             if sym_info else 1.0

        def snap(p):
            """Arredonda para o tick_size mais próximo (evita retcode 10016)."""
            if p is None or tick_size == 0:
                return p
            return round(round(float(p) / tick_size) * tick_size, 10)

        def ensure_min_distance(p, side):
            """Garante distância mínima de stops_level pontos em relação ao preço atual."""
            if p is None or stops_lvl == 0:
                return p
            min_dist = stops_lvl * point
            if side == "sl":
                if acao == "COMPRA" and p >= price - min_dist:
                    p = price - min_dist
                elif acao == "VENDA" and p <= price + min_dist:
                    p = price + min_dist
            elif side == "tp":
                if acao == "COMPRA" and p <= price + min_dist:
                    p = price + min_dist
                elif acao == "VENDA" and p >= price - min_dist:
                    p = price - min_dist
            return snap(p)

        sl_adj  = ensure_min_distance(snap(sl),  "sl")  if sl  is not None else None
        tp1_adj = ensure_min_distance(snap(tp1), "tp")  if tp1 is not None else None

        logger.info(
            "SL bruto=%.2f → ajustado=%.2f | TP bruto=%.2f → ajustado=%.2f | tick=%.2f stops_lvl=%d",
            sl  or 0, sl_adj  or 0,
            tp1 or 0, tp1_adj or 0,
            tick_size, stops_lvl,
        )

        # Filling mode
        _FILL_IOC = getattr(mt5, "SYMBOL_FILLING_IOC", 2)
        _FILL_FOK = getattr(mt5, "SYMBOL_FILLING_FOK", 1)
        filling   = mt5.ORDER_FILLING_RETURN
        try:
            if sym_info:
                if sym_info.filling_mode & _FILL_IOC:
                    filling = mt5.ORDER_FILLING_IOC
                elif sym_info.filling_mode & _FILL_FOK:
                    filling = mt5.ORDER_FILLING_FOK
        except Exception:
            pass

        request = {
            "action":       mt5.TRADE_ACTION_DEAL,
            "symbol":       mt5_symbol,
            "volume":       float(volume),
            "type":         order_type,
            "price":        price,
            "deviation":    30,
            "magic":        MAGIC_NUMBER,
            "comment":      BOT_COMMENT,
            "type_time":    mt5.ORDER_TIME_GTC,
            "type_filling": filling,
        }
        if sl_adj  is not None: request["sl"] = float(sl_adj)
        if tp1_adj is not None: request["tp"] = float(tp1_adj)

        logger.info(
            "AUTO-TRADE %s %s vol=%.1f price=%.0f SL=%.0f TP=%.0f",
            acao, mt5_symbol, volume, price,
            sl_adj or 0, tp1_adj or 0,
        )

        result = mt5.order_send(request)
        mt5.shutdown()

        if result is None:
            return None, "order_send retornou None."
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            return None, f"MT5 retcode {result.retcode}: {result.comment}"

        out = {
            "order":   result.order,
            "deal":    result.deal,
            "volume":  result.volume,
            "price":   result.price,
            "retcode": result.retcode,
            "comment": result.comment,
        }
        logger.info("AUTO-TRADE executado: order=%s deal=%s price=%.3f", result.order, result.deal, result.price)
        return out, None

    except Exception as exc:
        logger.exception("execute_trade erro: %s", exc)
        try:
            import MetaTrader5 as mt5; mt5.shutdown()
        except Exception:
            pass
        return None, str(exc)


def modify_position_sl(tv_symbol: str, new_sl: float) -> "tuple[bool, str | None]":
    """Move o stop loss de uma posição aberta para new_sl."""
    open_pos, err = get_open_positions(tv_symbol)
    if err:
        return False, err
    if not open_pos:
        return False, "Nenhuma posição aberta."

    try:
        import MetaTrader5 as mt5
        if not mt5.initialize(**_mt5_init_kwargs()):
            return False, f"MT5 não inicializado: {mt5.last_error()}"

        mt5_symbol = _mt5_symbol(tv_symbol) or tv_symbol
        pos = open_pos[0]
        req = {
            "action":   mt5.TRADE_ACTION_SLTP,
            "symbol":   mt5_symbol,
            "position": pos["ticket"],
            "sl":       float(new_sl),
            "tp":       float(pos.get("tp", 0) or 0),
        }
        r = mt5.order_send(req)
        mt5.shutdown()
        if r and r.retcode == mt5.TRADE_RETCODE_DONE:
            return True, None
        return False, f"MT5 retcode {r.retcode if r else 'None'}: {r.comment if r else ''}"
    except Exception as exc:
        return False, str(exc)


def modify_position_sltp(
    tv_symbol: str,
    new_sl: float,
    new_tp: float,
) -> "tuple[bool, str | None]":
    """
    Modifica SL e TP de uma posição aberta simultaneamente.
    Usado pelo partial_close_monitor após fechar 2 contratos:
      - new_sl = entry_price (breakeven)
      - new_tp = tp2         (alvo final para o contrato restante)
    """
    open_pos, err = get_open_positions(tv_symbol)
    if err:
        return False, err
    if not open_pos:
        return False, "Nenhuma posição aberta."

    try:
        import MetaTrader5 as mt5
        if not mt5.initialize(**_mt5_init_kwargs()):
            return False, f"MT5 não inicializado: {mt5.last_error()}"

        mt5_symbol = _mt5_symbol(tv_symbol) or tv_symbol
        pos = open_pos[0]
        req = {
            "action":   mt5.TRADE_ACTION_SLTP,
            "symbol":   mt5_symbol,
            "position": pos["ticket"],
            "sl":       float(new_sl),
            "tp":       float(new_tp),
        }
        r = mt5.order_send(req)
        mt5.shutdown()
        if r and r.retcode == mt5.TRADE_RETCODE_DONE:
            logger.info(
                "modify_position_sltp: %s SL=%.0f TP=%.0f OK",
                tv_symbol, new_sl, new_tp,
            )
            return True, None
        return False, f"MT5 retcode {r.retcode if r else 'None'}: {r.comment if r else ''}"
    except Exception as exc:
        return False, str(exc)


def close_partial_position(
    tv_symbol: str,
    volume_to_close: float,
) -> "tuple[dict | None, str | None]":
    """
    Fecha PARCIALMENTE uma posição aberta, reduzindo `volume_to_close` contratos.

    Usado na estratégia 3-contratos: fecha 2 no TP1, mantém 1 até TP2.
    Não altera nem prejudica o comportamento das posições de 1 contrato.

    Retorna (result_dict | None, error_msg | None).
    """
    open_pos, err = get_open_positions(tv_symbol)
    if err:
        return None, err
    if not open_pos:
        return None, "Nenhuma posição aberta para fechamento parcial."

    try:
        import MetaTrader5 as mt5
        if not mt5.initialize(**_mt5_init_kwargs()):
            return None, f"MT5 não inicializado: {mt5.last_error()}"

        mt5_symbol   = _mt5_symbol(tv_symbol) or tv_symbol
        pos          = open_pos[0]
        actual_vol   = float(pos.get("volume", 1.0))
        vol_close    = min(float(volume_to_close), actual_vol)

        tick = mt5.symbol_info_tick(mt5_symbol)
        if tick is None:
            mt5.shutdown()
            return None, f"Tick não disponível para '{mt5_symbol}'."

        close_type = mt5.ORDER_TYPE_SELL if pos["type"] == 0 else mt5.ORDER_TYPE_BUY
        price      = tick.bid             if pos["type"] == 0 else tick.ask

        # Filling mode
        _FILL_IOC = getattr(mt5, "SYMBOL_FILLING_IOC", 2)
        _FILL_FOK = getattr(mt5, "SYMBOL_FILLING_FOK", 1)
        filling   = mt5.ORDER_FILLING_RETURN
        try:
            sym_info = mt5.symbol_info(mt5_symbol)
            if sym_info:
                if sym_info.filling_mode & _FILL_IOC:
                    filling = mt5.ORDER_FILLING_IOC
                elif sym_info.filling_mode & _FILL_FOK:
                    filling = mt5.ORDER_FILLING_FOK
        except Exception:
            pass

        req = {
            "action":       mt5.TRADE_ACTION_DEAL,
            "symbol":       mt5_symbol,
            "volume":       vol_close,
            "type":         close_type,
            "position":     pos["ticket"],
            "price":        price,
            "deviation":    30,
            "magic":        MAGIC_NUMBER,
            "comment":      BOT_COMMENT + "-partial",
            "type_time":    mt5.ORDER_TIME_GTC,
            "type_filling": filling,
        }

        r = mt5.order_send(req)
        mt5.shutdown()

        if r is None:
            return None, "order_send retornou None."
        if r.retcode != mt5.TRADE_RETCODE_DONE:
            return None, f"MT5 retcode {r.retcode}: {r.comment}"

        out = {
            "order":          r.order,
            "deal":           r.deal,
            "volume_closed":  vol_close,
            "price":          r.price,
            "retcode":        r.retcode,
        }
        logger.info(
            "PARTIAL-CLOSE: ticket=%s vol=%.1f price=%.0f order=%s",
            pos["ticket"], vol_close, r.price, r.order,
        )
        return out, None

    except Exception as exc:
        logger.exception("close_partial_position erro: %s", exc)
        try:
            import MetaTrader5 as mt5; mt5.shutdown()
        except Exception:
            pass
        return None, str(exc)


def close_all_positions(tv_symbol: str) -> "tuple[list, str | None]":
    """Fecha todas as posições abertas pelo bot para o símbolo dado."""
    open_pos, err = get_open_positions(tv_symbol)
    if err:
        return [], err
    if not open_pos:
        return [], None

    try:
        import MetaTrader5 as mt5
        if not mt5.initialize(**_mt5_init_kwargs()):
            return [], f"MT5 não inicializado: {mt5.last_error()}"

        mt5_symbol = _mt5_symbol(tv_symbol) or tv_symbol
        results = []
        for pos in open_pos:
            tick = mt5.symbol_info_tick(mt5_symbol)
            if tick is None:
                continue
            close_type = mt5.ORDER_TYPE_SELL if pos["type"] == 0 else mt5.ORDER_TYPE_BUY
            price      = tick.bid             if pos["type"] == 0 else tick.ask

            _FILL_IOC = getattr(mt5, "SYMBOL_FILLING_IOC", 2)
            _FILL_FOK = getattr(mt5, "SYMBOL_FILLING_FOK", 1)
            filling  = mt5.ORDER_FILLING_RETURN
            try:
                sym_info = mt5.symbol_info(mt5_symbol)
                if sym_info:
                    if sym_info.filling_mode & _FILL_IOC:
                        filling = mt5.ORDER_FILLING_IOC
                    elif sym_info.filling_mode & _FILL_FOK:
                        filling = mt5.ORDER_FILLING_FOK
            except Exception:
                pass

            req = {
                "action":       mt5.TRADE_ACTION_DEAL,
                "symbol":       mt5_symbol,
                "volume":       pos["volume"],
                "type":         close_type,
                "position":     pos["ticket"],
                "price":        price,
                "deviation":    30,
                "magic":        MAGIC_NUMBER,
                "comment":      BOT_COMMENT + "-close",
                "type_time":    mt5.ORDER_TIME_GTC,
                "type_filling": filling,
            }
            r = mt5.order_send(req)
            results.append({
                "ticket":  pos["ticket"],
                "retcode": r.retcode if r else None,
                "comment": r.comment if r else "None",
            })
        mt5.shutdown()
        return results, None

    except Exception as exc:
        logger.exception("close_all_positions erro: %s", exc)
        try:
            import MetaTrader5 as mt5; mt5.shutdown()
        except Exception:
            pass
        return [], str(exc)
