"""
services/crypto_service.py — Acesso MT5 do MONITOR CRYPTO (dados + execução).

⚠️ MÓDULO NOVO E INDEPENDENTE. Copia o padrão do Monitor MT5 (init via .env,
copy_rates, order_send) mas em arquivo próprio — NÃO importa nem altera o
Monitor MT5 / Scalper. Usa um MAGIC exclusivo para separar as posições.

A conta usada é a que estiver no .env (MT5_LOGIN/PASSWORD/SERVER/PATH). O usuário
troca para a conta de crypto à noite/fim de semana. Símbolos usados DIRETO.

Padrão: cada função faz initialize()+shutdown() (isolado e à prova de estado
ruim). ÚNICA exceção: get_tick() reaproveita a conexão (NÃO dá shutdown) para o
preço ao vivo ficar rápido e colado no MT5.
"""
import os
import logging

logger = logging.getLogger(__name__)

MAGIC_CRYPTO = 770077          # exclusivo do Monitor Crypto (separa do Monitor MT5)
BOT_COMMENT  = "MonitorCrypto"
# Magic do módulo Scalper — quando o Scalper opera BTCUSD na MESMA conta ICMarkets,
# seus trades NÃO são do Monitor Crypto e devem ser ignorados aqui (isolamento).
_SCALPER_MAGIC = 20260506

try:
    import MetaTrader5 as mt5
    MT5_OK = True
except Exception:
    MT5_OK = False

# ── Serialização do acesso ao MT5 ────────────────────────────────────────────
# A lib MetaTrader5 é um singleton por processo. Se duas threads chamam
# initialize()/shutdown() ao mesmo tempo (ex.: o reconciliador de background e o
# tick do frontend), uma quebra a conexão da outra. Este lock serializa TODO o
# acesso — cada função ainda faz seu init+shutdown, mas uma de cada vez.
import threading as _threading
import functools as _functools
_MT5_LOCK = _threading.Lock()

def _serialized(fn):
    @_functools.wraps(fn)
    def _w(*a, **k):
        with _MT5_LOCK:
            return fn(*a, **k)
    return _w


def _init_kwargs() -> dict:
    login  = int(os.getenv("MT5_LOGIN", "0") or 0)
    pw     = os.getenv("MT5_PASSWORD", "")
    server = os.getenv("MT5_SERVER", "")
    path   = os.getenv("MT5_PATH", "")
    kw = {}
    if path and os.path.exists(path):
        kw["path"] = path
    _mq = {"metaquotes-demo", "metaquotes-demo2"}
    if login and pw and server and server.lower() not in _mq:
        kw["login"], kw["password"], kw["server"] = login, pw, server
    return kw


def _tf(minutes: int):
    m = {1: mt5.TIMEFRAME_M1, 5: mt5.TIMEFRAME_M5, 15: mt5.TIMEFRAME_M15,
         30: mt5.TIMEFRAME_M30, 60: mt5.TIMEFRAME_H1, 240: mt5.TIMEFRAME_H4}
    return m.get(int(minutes), mt5.TIMEFRAME_M15)


# ── Dados ───────────────────────────────────────────────────────────────────

@_serialized
def get_candles(symbol: str, tf_minutes: int = 15, n: int = 200):
    """Retorna (list[dict candle], err). Candle: {time(epoch), open, high, low, close, volume}."""
    if not MT5_OK:
        return None, "MetaTrader5 não instalado."
    import time as _t
    last = "sem dados"
    for att in range(3):
        try:
            if att:
                _t.sleep(0.4)
            if not mt5.initialize(**_init_kwargs()):
                last = f"MT5 não inicializado: {mt5.last_error()} (terminal aberto e logado?)"
                try: mt5.shutdown()
                except Exception: pass
                continue
            mt5.symbol_select(symbol, True)
            rates = mt5.copy_rates_from_pos(symbol, _tf(tf_minutes), 0, n)
            mt5.shutdown()
            if rates is None or len(rates) == 0:
                last = (f"Sem dados p/ '{symbol}'. Está no Market Watch da conta atual? "
                        f"(lembre: à noite/fim de semana troque o .env p/ a conta crypto)")
                continue
            out = [{"time": int(r["time"]), "open": float(r["open"]), "high": float(r["high"]),
                    "low": float(r["low"]), "close": float(r["close"]),
                    "volume": float(r["tick_volume"])} for r in rates]
            return out, None
        except Exception as exc:
            last = str(exc)
            try: mt5.shutdown()
            except Exception: pass
    return None, last


@_serialized
def symbol_spec(symbol: str) -> dict:
    """Especificações do símbolo p/ calcular o valor por ponto (na moeda da conta)."""
    if not MT5_OK:
        return {}
    try:
        if not mt5.initialize(**_init_kwargs()):
            return {}
        mt5.symbol_select(symbol, True)
        info = mt5.symbol_info(symbol)
        mt5.shutdown()
        if not info:
            return {}
        ts = float(info.trade_tick_size or info.point or 0)
        tv = float(info.trade_tick_value or 0)
        vpp = (tv / ts) if ts else 0.0    # valor de 1.0 de movimento, por 1 lote
        return {"tick_size": ts, "tick_value": tv, "point": float(info.point or 0),
                "digits": int(info.digits or 2),
                "contract_size": float(getattr(info, "trade_contract_size", 0) or 0),
                "value_per_point": round(vpp, 4)}
    except Exception as exc:
        logger.warning("crypto symbol_spec %s: %s", symbol, exc)
        try: mt5.shutdown()
        except Exception: pass
        return {}


# Cache USD/BRL (evita bater no MT5/HTTP a cada poll do dashboard).
_USDBRL_CACHE = {"v": None, "ts": 0.0, "src": "fallback"}
_USDBRL_TTL = 3600.0  # 1h


@_serialized
def _usdbrl_from_mt5() -> float | None:
    """Tenta cotação USDBRL no terminal MT5 da conta crypto."""
    if not MT5_OK:
        return None
    try:
        if not mt5.initialize(**_init_kwargs()):
            return None
        for s in ("USDBRL", "USDBRL.a", "USDBRLm", "USDBRL.i"):
            try:
                mt5.symbol_select(s, True)
                t = mt5.symbol_info_tick(s)
                if t and (t.bid or t.ask):
                    mt5.shutdown()
                    return round(float(t.bid or t.ask), 4)
            except Exception:
                continue
        mt5.shutdown()
    except Exception:
        try: mt5.shutdown()
        except Exception: pass
    return None


def _usdbrl_from_awesome() -> float | None:
    """Fallback HTTP: AwesomeAPI USD-BRL (economia.awesomeapi.com.br)."""
    try:
        import json
        import urllib.request
        url = "https://economia.awesomeapi.com.br/json/last/USD-BRL"
        req = urllib.request.Request(url, headers={"User-Agent": "trader_analizer/1.0"})
        with urllib.request.urlopen(req, timeout=4) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        bid = (data.get("USDBRL") or {}).get("bid")
        if bid:
            return round(float(bid), 4)
    except Exception as exc:
        logger.debug("usd_brl AwesomeAPI: %s", exc)
    return None


def usd_brl_rate() -> float:
    """Cotação USD/BRL. Ordem: env CRYPTO_USDBRL → MT5 → AwesomeAPI (cache 1h) → 5.20."""
    import time as _time
    env = os.getenv("CRYPTO_USDBRL")
    if env:
        try:
            v = float(env)
            _USDBRL_CACHE.update(v=v, ts=_time.time(), src="env")
            return v
        except Exception:
            pass
    now = _time.time()
    if _USDBRL_CACHE["v"] and (now - _USDBRL_CACHE["ts"]) < _USDBRL_TTL:
        return float(_USDBRL_CACHE["v"])
    rate = _usdbrl_from_mt5()
    if rate:
        _USDBRL_CACHE.update(v=rate, ts=now, src="mt5")
        return rate
    rate = _usdbrl_from_awesome()
    if rate:
        _USDBRL_CACHE.update(v=rate, ts=now, src="awesomeapi")
        return rate
    if _USDBRL_CACHE["v"]:
        return float(_USDBRL_CACHE["v"])
    _USDBRL_CACHE.update(v=5.20, ts=now, src="fallback")
    return 5.20


def usd_brl_meta() -> dict:
    """Valor + fonte da cotação (p/ UI indicar discretamente)."""
    v = usd_brl_rate()
    return {"usdbrl": v, "source": _USDBRL_CACHE.get("src") or "fallback"}


@_serialized
def account_info() -> dict:
    """Moeda e saldo da conta atual do MT5 (para rotular o P&L corretamente)."""
    if not MT5_OK:
        return {}
    try:
        if not mt5.initialize(**_init_kwargs()):
            return {}
        a = mt5.account_info()
        mt5.shutdown()
        if not a:
            return {}
        return {"currency": a.currency, "balance": float(a.balance),
                "equity": float(a.equity), "login": a.login, "server": a.server}
    except Exception as exc:
        logger.warning("crypto account_info: %s", exc)
        try: mt5.shutdown()
        except Exception: pass
        return {}


@_serialized
def last_deal_profit(symbol: str):
    """P&L realizado do último deal de SAÍDA do Monitor Crypto no símbolo (MT5 history)."""
    if not MT5_OK:
        return None
    try:
        from datetime import datetime, timedelta
        if not mt5.initialize(**_init_kwargs()):
            return None
        # +1 dia à frente: o servidor do broker fica horas ADIANTE do horário local,
        # então um fechamento recente tem carimbo "no futuro" p/ nós. Sem essa folga,
        # os deals mais novos caem fora da janela e não são capturados (bug do fuso).
        to = datetime.now() + timedelta(days=1)
        frm = datetime.now() - timedelta(hours=12)
        deals = mt5.history_deals_get(frm, to) or []
        mt5.shutdown()
        outs = [d for d in deals
                if getattr(d, "magic", 0) == MAGIC_CRYPTO
                and getattr(d, "entry", None) == mt5.DEAL_ENTRY_OUT
                and (d.symbol or "").upper() == symbol.upper()]
        if not outs:
            return None
        outs.sort(key=lambda d: d.time)
        return round(float(outs[-1].profit), 2)
    except Exception as exc:
        logger.warning("crypto last_deal_profit %s: %s", symbol, exc)
        try: mt5.shutdown()
        except Exception: pass
        return None


@_serialized
def last_out_deal(symbol: str):
    """Último deal de SAÍDA REAL (DEAL_ENTRY_OUT) do Monitor Crypto no símbolo.
    Retorna {ticket, profit, price, time} ou None se NÃO houver fechamento real."""
    if not MT5_OK:
        return None
    try:
        from datetime import datetime, timedelta
        if not mt5.initialize(**_init_kwargs()):
            return None
        # +1 dia à frente: o servidor do broker fica horas ADIANTE do horário local,
        # então um fechamento recente tem carimbo "no futuro" p/ nós. Sem essa folga,
        # os deals mais novos caem fora da janela e não são capturados (bug do fuso).
        to = datetime.now() + timedelta(days=1)
        frm = datetime.now() - timedelta(hours=12)
        deals = mt5.history_deals_get(frm, to) or []
        mt5.shutdown()
        # por SÍMBOLO, tolerando sufixo do broker (XAUUSD.i → XAUUSD)
        _sb = symbol.upper().split(".")[0]
        outs = [d for d in deals
                if getattr(d, "entry", None) == mt5.DEAL_ENTRY_OUT
                and (d.symbol or "").upper().split(".")[0] == _sb
                and getattr(d, "magic", 0) != _SCALPER_MAGIC]   # ignora trades do Scalper
        if not outs:
            return None
        outs.sort(key=lambda d: d.time)
        d = outs[-1]
        return {"ticket": int(d.ticket), "profit": round(float(d.profit), 2),
                "price": float(getattr(d, "price", 0) or 0), "time": int(d.time)}
    except Exception as exc:
        logger.warning("crypto last_out_deal %s: %s", symbol, exc)
        try: mt5.shutdown()
        except Exception: pass
        return None


@_serialized
def recent_closed_trades(hours: int = 48):
    """Trades FECHADOS do Monitor Crypto direto do histórico MT5 (FONTE DA VERDADE).

    Agrupa os deals por position_id e junta a ENTRADA (IN) com a SAÍDA (OUT).
    Usado pela reconciliação: registra fechamentos (SL/TP no broker) mesmo que o
    navegador tenha perdido o evento. Posições ainda abertas (sem OUT) são ignoradas."""
    if not MT5_OK:
        return []
    try:
        from datetime import datetime, timedelta
        if not mt5.initialize(**_init_kwargs()):
            return []
        # +1 dia à frente: o servidor do broker fica horas ADIANTE do horário local,
        # então um fechamento recente tem carimbo "no futuro" p/ nós. Sem essa folga,
        # os deals mais novos caem fora da janela e não são capturados (bug do fuso).
        to = datetime.now() + timedelta(days=1)
        frm = datetime.now() - timedelta(hours=hours)
        deals = mt5.history_deals_get(frm, to) or []
        mt5.shutdown()
        # Captura por SÍMBOLO de crypto (não só pelo magic do robô): a conta ICMarkets é
        # dedicada a crypto, então TODO trade nesses símbolos é do Monitor Crypto — assim
        # registramos até trades abertos/gerenciados fora do magic. Fallback: se não houver
        # símbolos configurados, cai no filtro por magic (comportamento antigo).
        try:
            from services.crypto_config import get_symbols
            _syms = set(s.upper() for s in (get_symbols() or []))
        except Exception:
            _syms = set()
        by_pos = {}
        for d in deals:
            _dsym = (getattr(d, "symbol", "") or "").upper()
            _dbase = _dsym.split(".")[0]          # XAUUSD.i → XAUUSD (sufixo de broker)
            _dmag = getattr(d, "magic", 0)
            # ISOLAMENTO: trade do módulo Scalper (magic próprio) nunca é do Monitor Crypto,
            # mesmo que o símbolo (BTCUSD) esteja na lista. Ignora aqui.
            if _dmag == _SCALPER_MAGIC:
                continue
            # Captura se: (a) é do ROBÔ (magic exclusivo) — pega qualquer símbolo, mesmo
            # com sufixo do broker; OU (b) o símbolo bate com a lista crypto (cobre trades
            # manuais no terminal, já que a conta ICMarkets é dedicada a crypto).
            _sym_ok = bool(_syms) and (_dsym in _syms or _dbase in _syms)
            if not (_dmag == MAGIC_CRYPTO or _sym_ok):
                continue
            by_pos.setdefault(getattr(d, "position_id", 0), []).append(d)
        out = []
        for pid, ds in by_pos.items():
            ins  = [d for d in ds if getattr(d, "entry", None) == mt5.DEAL_ENTRY_IN]
            outs = [d for d in ds if getattr(d, "entry", None) == mt5.DEAL_ENTRY_OUT]
            if not outs:
                continue   # posição ainda aberta
            outs.sort(key=lambda d: d.time)
            od = outs[-1]
            idl = ins[0] if ins else None
            if idl is not None:
                direcao = "COMPRA" if idl.type == mt5.DEAL_TYPE_BUY else "VENDA"
                entry_price = float(idl.price)
                volume = float(idl.volume)
            else:
                direcao = "VENDA" if od.type == mt5.DEAL_TYPE_BUY else "COMPRA"
                entry_price = None
                volume = float(od.volume)
            profit = (sum(float(d.profit) for d in outs)
                      + sum(float(getattr(d, "swap", 0) or 0) for d in ds)
                      + sum(float(getattr(d, "commission", 0) or 0) for d in ds))
            out.append({
                "out_ticket": int(od.ticket), "position_id": int(pid),
                "symbol": (od.symbol or "").upper().split(".")[0], "direcao": direcao,
                "entry_price": entry_price, "exit_price": float(od.price),
                "volume": volume, "profit": round(profit, 2), "close_epoch": int(od.time),
            })
        out.sort(key=lambda x: x["close_epoch"])
        return out
    except Exception as exc:
        logger.warning("crypto recent_closed_trades: %s", exc)
        try: mt5.shutdown()
        except Exception: pass
        return []


@_serialized
def get_tick(symbol: str):
    """Preço atual do símbolo. Padrão original init+shutdown (estável sob concorrência)."""
    if not MT5_OK:
        return None
    try:
        if not mt5.initialize(**_init_kwargs()):
            return None
        mt5.symbol_select(symbol, True)
        t = mt5.symbol_info_tick(symbol)
        mt5.shutdown()
        if not t:
            return None
        return {"bid": float(t.bid), "ask": float(t.ask), "last": float(t.last or t.bid),
                "time": int(getattr(t, "time", 0) or 0)}
    except Exception as exc:
        logger.warning("crypto get_tick %s: %s", symbol, exc)
        try: mt5.shutdown()
        except Exception: pass
        return None


@_serialized
def get_positions(symbol: str = None):
    """Posições abertas do Monitor Crypto (filtra pelo MAGIC exclusivo)."""
    if not MT5_OK:
        return [], "MetaTrader5 não instalado."
    try:
        if not mt5.initialize(**_init_kwargs()):
            return [], f"MT5 não inicializado: {mt5.last_error()}"
        raw = mt5.positions_get(symbol=symbol) if symbol else mt5.positions_get()
        mt5.shutdown()
        raw = raw or []
        out = []
        for p in raw:
            if p.magic != MAGIC_CRYPTO:
                continue
            out.append({
                "ticket": p.ticket, "symbol": p.symbol,
                "type": "COMPRA" if p.type == 0 else "VENDA",
                "volume": float(p.volume), "price_open": float(p.price_open),
                "price_current": float(p.price_current), "sl": float(p.sl), "tp": float(p.tp),
                "profit": float(p.profit),
                "time": int(getattr(p, "time", 0) or 0),   # epoch de abertura (p/ time-stop)
            })
        return out, None
    except Exception as exc:
        try: mt5.shutdown()
        except Exception: pass
        return [], str(exc)


# ── Execução ────────────────────────────────────────────────────────────────

_READY_CACHE = {"ts": 0.0, "ok": True, "detail": "ok"}
_READY_TTL = 20.0


@_serialized
def mt5_execution_ready():
    """
    Pré-checagem operacional antes do cascade GO.
    Retorna (ok: bool, detail: str).
    Cobre o caso clássico retcode 10027 (AutoTrading desligado no terminal).
    Cache curto: o runtime checa 5 símbolos/ciclo — evita 5× initialize.
    """
    import time as _t
    now = _t.time()
    if now - float(_READY_CACHE["ts"]) < _READY_TTL:
        return bool(_READY_CACHE["ok"]), str(_READY_CACHE["detail"])
    if not MT5_OK:
        return False, "MetaTrader5 não instalado"
    try:
        if not mt5.initialize(**_init_kwargs()):
            detail = f"MT5 não inicializado: {mt5.last_error()}"
            _READY_CACHE.update(ts=now, ok=False, detail=detail)
            return False, detail
        ti = mt5.terminal_info()
        ai = mt5.account_info()
        mt5.shutdown()
        if ti is None:
            detail = "MT5 terminal_info indisponível (terminal fechado?)"
            _READY_CACHE.update(ts=now, ok=False, detail=detail)
            return False, detail
        if not getattr(ti, "connected", True):
            detail = "MT5 terminal desconectado"
            _READY_CACHE.update(ts=now, ok=False, detail=detail)
            return False, detail
        if not getattr(ti, "trade_allowed", True):
            detail = ("MT5 AutoTrading DESLIGADO no terminal "
                      "(botão Algo Trading / AutoTrading) — GOs disparam mas fill=10027")
            _READY_CACHE.update(ts=now, ok=False, detail=detail)
            return False, detail
        if ai is not None and hasattr(ai, "trade_expert") and not ai.trade_expert:
            detail = "Conta MT5 com trade por Expert Advisors desabilitado"
            _READY_CACHE.update(ts=now, ok=False, detail=detail)
            return False, detail
        if ai is not None and hasattr(ai, "trade_allowed") and not ai.trade_allowed:
            detail = "Conta MT5 sem permissão de trade"
            _READY_CACHE.update(ts=now, ok=False, detail=detail)
            return False, detail
        _READY_CACHE.update(ts=now, ok=True, detail="ok")
        return True, "ok"
    except Exception as exc:
        try:
            mt5.shutdown()
        except Exception:
            pass
        detail = f"checagem MT5 falhou: {exc}"
        _READY_CACHE.update(ts=now, ok=False, detail=detail)
        return False, detail


def execute(symbol: str, acao: str, volume: float, sl: float = None, tp: float = None):
    """Abre ordem a mercado. Retorna (result_dict, err).

    Aplica caps live SL/TP e clamp de risco/lote (MAX_RISK_USD / MAX_VOLUME) —
    único choke point p/ GO fire, discovery, manual e auto-check.
    """
    if not MT5_OK:
        return None, "MetaTrader5 não instalado."
    try:
        if not mt5.initialize(**_init_kwargs()):
            return None, f"MT5 não inicializado: {mt5.last_error()}"
        # Fail-fast operacional (evita cascade inteiro achar que "não há gatilho")
        ti = mt5.terminal_info()
        if ti is not None and not getattr(ti, "trade_allowed", True):
            mt5.shutdown()
            _READY_CACHE.update(ts=0.0, ok=False,
                                detail="MT5 AutoTrading DESLIGADO no terminal")
            return None, ("MT5 retcode 10027: AutoTrading disabled by client "
                          "(ligue Algo Trading no terminal IC Markets)")
        if not mt5.symbol_select(symbol, True):
            mt5.shutdown()
            return None, f"Símbolo '{symbol}' não disponível no Market Watch."
        tick = mt5.symbol_info_tick(symbol)
        info = mt5.symbol_info(symbol)
        if tick is None or info is None:
            mt5.shutdown()
            return None, f"Preço/símbolo indisponível para '{symbol}'."
        is_buy = acao == "COMPRA"
        otype  = mt5.ORDER_TYPE_BUY if is_buy else mt5.ORDER_TYPE_SELL
        price  = tick.ask if is_buy else tick.bid
        tick_size = float(info.trade_tick_size or info.point or 0.00001)
        stops_lvl = int(getattr(info, "trade_stops_level", 0) or 0)
        point     = float(info.point or tick_size)
        spread    = abs(float(tick.ask) - float(tick.bid))

        # Caps live SL/TP (ETH/BTC/XAU/EUR/GBP) — evita ATR H1 discovery / estrutural gigante.
        # Posição já aberta: crypto_bp/manage encurta TP via modify_sl_tp.
        if sl is not None and tp is not None:
            try:
                from services.crypto_edge_setups import apply_live_caps, live_caps_for
                if live_caps_for(symbol):
                    sl, tp = apply_live_caps(symbol, acao, price, sl, tp)
            except Exception:
                pass

        # Preço cruzou o stop do sinal → 10016 se mandarmos assim; não “empurrar”
        # o SL para o outro lado (abriria trade já inválido).
        if sl is not None:
            sl_f = float(sl)
            if is_buy and sl_f >= price:
                mt5.shutdown()
                return None, (f"SL do lado errado p/ COMPRA (sl={sl_f} >= price={price}) "
                              "— preço já cruzou o stop do GO")
            if (not is_buy) and sl_f <= price:
                mt5.shutdown()
                return None, (f"SL do lado errado p/ VENDA (sl={sl_f} <= price={price}) "
                              "— preço já cruzou o stop do GO")
        if tp is not None:
            tp_f = float(tp)
            if is_buy and tp_f <= price:
                mt5.shutdown()
                return None, (f"TP do lado errado p/ COMPRA (tp={tp_f} <= price={price})")
            if (not is_buy) and tp_f >= price:
                mt5.shutdown()
                return None, (f"TP do lado errado p/ VENDA (tp={tp_f} >= price={price})")

        def snap(p):
            if p is None or tick_size == 0:
                return p
            return round(round(float(p) / tick_size) * tick_size, 10)

        def min_dist(p, side):
            """Garante distância mínima. BTC/ETH IC frequentemente reportam
            trade_stops_level=0 mas rejeitam SL/TP dentro do spread (10016)."""
            if p is None:
                return None
            p = float(p)
            d = max(stops_lvl * point, spread, tick_size * 2)
            if side == "sl":
                if is_buy and p > price - d:
                    p = price - d
                elif (not is_buy) and p < price + d:
                    p = price + d
            else:
                if is_buy and p < price + d:
                    p = price + d
                elif (not is_buy) and p > price - d:
                    p = price - d
            return snap(p)

        sl_a = min_dist(snap(sl), "sl") if sl is not None else None
        tp_a = min_dist(snap(tp), "tp") if tp is not None else None

        # Cap risco USD + teto de lote (painel às vezes força 1.0 em BTC/ETH).
        vol_a = float(volume or 0)
        try:
            from services.crypto_config import clamp_live_volume
            ts = float(info.trade_tick_size or info.point or 0)
            tv = float(info.trade_tick_value or 0)
            vpp = (tv / ts) if ts else 0.0
            sl_pts = abs(float(price) - float(sl_a)) if sl_a is not None else 0.0
            vmin = float(getattr(info, "volume_min", 0) or 0.01)
            vstep = float(getattr(info, "volume_step", 0) or 0.01)
            vol_a, vnote = clamp_live_volume(
                symbol, vol_a, sl_pts, vpp, volume_min=vmin, volume_step=vstep)
            if vnote:
                logger.warning("crypto execute %s volume clamp: %s", symbol, vnote)
        except Exception as exc:
            logger.warning("crypto execute volume clamp falhou %s: %s", symbol, exc)

        _IOC = getattr(mt5, "SYMBOL_FILLING_IOC", 2)
        _FOK = getattr(mt5, "SYMBOL_FILLING_FOK", 1)
        filling = mt5.ORDER_FILLING_RETURN
        try:
            if info.filling_mode & _IOC: filling = mt5.ORDER_FILLING_IOC
            elif info.filling_mode & _FOK: filling = mt5.ORDER_FILLING_FOK
        except Exception:
            pass

        req = {
            "action": mt5.TRADE_ACTION_DEAL, "symbol": symbol, "volume": float(vol_a),
            "type": otype, "price": price, "deviation": 30, "magic": MAGIC_CRYPTO,
            "comment": BOT_COMMENT, "type_time": mt5.ORDER_TIME_GTC, "type_filling": filling,
        }
        if sl_a is not None: req["sl"] = float(sl_a)
        if tp_a is not None: req["tp"] = float(tp_a)

        res = mt5.order_send(req)
        mt5.shutdown()
        if res is None:
            return None, "order_send retornou None."
        if res.retcode != mt5.TRADE_RETCODE_DONE:
            return None, f"MT5 retcode {res.retcode}: {res.comment}"
        return {"order": res.order, "price": float(res.price), "volume": float(res.volume),
                "sl": sl_a, "tp": tp_a}, None
    except Exception as exc:
        logger.exception("crypto execute erro")
        try: mt5.shutdown()
        except Exception: pass
        return None, str(exc)


@_serialized
def close_position(symbol: str):
    """Fecha a posição do Monitor Crypto no símbolo. Retorna (info, err)."""
    if not MT5_OK:
        return None, "MetaTrader5 não instalado."
    try:
        if not mt5.initialize(**_init_kwargs()):
            return None, f"MT5 não inicializado: {mt5.last_error()}"
        poss = [p for p in (mt5.positions_get(symbol=symbol) or []) if p.magic == MAGIC_CRYPTO]
        if not poss:
            mt5.shutdown()
            return None, "Sem posição do Monitor Crypto para fechar."
        p = poss[0]
        tick = mt5.symbol_info_tick(symbol)
        info = mt5.symbol_info(symbol)
        is_buy = p.type == 0
        otype = mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY
        price = tick.bid if is_buy else tick.ask
        _IOC = getattr(mt5, "SYMBOL_FILLING_IOC", 2)
        filling = mt5.ORDER_FILLING_IOC if (info and info.filling_mode & _IOC) else mt5.ORDER_FILLING_RETURN
        req = {"action": mt5.TRADE_ACTION_DEAL, "symbol": symbol, "volume": float(p.volume),
               "type": otype, "position": p.ticket, "price": price, "deviation": 30,
               "magic": MAGIC_CRYPTO, "comment": "MonitorCrypto close",
               "type_time": mt5.ORDER_TIME_GTC, "type_filling": filling}
        res = mt5.order_send(req)
        mt5.shutdown()
        if res is None or res.retcode != mt5.TRADE_RETCODE_DONE:
            return None, f"Falha ao fechar: {res.comment if res else 'None'}"
        return {"price": float(res.price), "profit": float(p.profit)}, None
    except Exception as exc:
        try: mt5.shutdown()
        except Exception: pass
        return None, str(exc)


@_serialized
def modify_sl(symbol: str, new_sl: float):
    """Move o stop da posição do Monitor Crypto. Retorna (ok, err)."""
    if not MT5_OK:
        return False, "MetaTrader5 não instalado."
    try:
        if not mt5.initialize(**_init_kwargs()):
            return False, f"MT5 não inicializado: {mt5.last_error()}"
        poss = [p for p in (mt5.positions_get(symbol=symbol) or []) if p.magic == MAGIC_CRYPTO]
        if not poss:
            mt5.shutdown()
            return False, "Sem posição do Monitor Crypto."
        p = poss[0]
        req = {"action": mt5.TRADE_ACTION_SLTP, "symbol": symbol, "position": p.ticket,
               "sl": float(new_sl), "tp": float(p.tp)}
        res = mt5.order_send(req)
        mt5.shutdown()
        if res is None or res.retcode != mt5.TRADE_RETCODE_DONE:
            return False, f"Falha ao mover stop: {res.comment if res else 'None'}"
        return True, None
    except Exception as exc:
        try: mt5.shutdown()
        except Exception: pass
        return False, str(exc)


@_serialized
def modify_sl_tp(symbol: str, new_sl: float = None, new_tp: float = None):
    """Altera SL e/ou TP da posição do Monitor Crypto. Retorna (ok, err)."""
    if not MT5_OK:
        return False, "MetaTrader5 não instalado."
    try:
        if not mt5.initialize(**_init_kwargs()):
            return False, f"MT5 não inicializado: {mt5.last_error()}"
        poss = [p for p in (mt5.positions_get(symbol=symbol) or []) if p.magic == MAGIC_CRYPTO]
        if not poss:
            mt5.shutdown()
            return False, "Sem posição do Monitor Crypto."
        p = poss[0]
        sl = float(new_sl) if new_sl is not None else float(p.sl)
        tp = float(new_tp) if new_tp is not None else float(p.tp)
        req = {"action": mt5.TRADE_ACTION_SLTP, "symbol": symbol, "position": p.ticket,
               "sl": sl, "tp": tp}
        res = mt5.order_send(req)
        mt5.shutdown()
        if res is None or res.retcode != mt5.TRADE_RETCODE_DONE:
            return False, f"Falha ao alterar SL/TP: {res.comment if res else 'None'}"
        return True, None
    except Exception as exc:
        try: mt5.shutdown()
        except Exception: pass
        return False, str(exc)
