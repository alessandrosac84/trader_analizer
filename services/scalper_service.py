"""
scalper_service.py
Serviço MT5 exclusivo para o módulo Scalper — totalmente isolado dos demais serviços.
Usa book de ordens, agressão por polling de tick e execução direta via MT5.

Magic number: 20260506  (distinto do bot principal 20260505)

Agressão: rastreada por polling — não depende de copy_ticks_from.
A cada chamada comparamos o tick atual com o anterior; se o 'last' mudou
classificamos como compra (last >= ask) ou venda (last <= bid).

SIM MODE: quando ativo, gera ticks sintéticos sem MT5 (para testes 24/7).
"""
import os
import time
import random
import logging
from collections import deque
from datetime import datetime, timezone, timedelta, time as dt_time

logger = logging.getLogger(__name__)

SCALPER_MAGIC   = 20260506
SCALPER_COMMENT = "ScalperAI"

# ── Símbolos suportados ────────────────────────────────────────────────────
SCALPER_SYMBOLS = {
    "WDON26": "WDON26",
    "WDOM26": "WDOM26",
    "WINM26": "WINM26",
    "WDO":    "WDON26",
    "WIN":    "WINM26",
    # Bitcoin Futuro B3 (mini contrato, 0.01 BTC, horário B3)
    "BITM26": "BITM26",
    "BIT":    "BITM26",
    # Forex / Metais (24h)
    "XAUUSD": "XAUUSD",
    "EURUSD": "EURUSD",
    "USDBRL": "USDBRL",
    "GOLD11": "GOLD11",
    "BOVA11": "BOVA11",
    "IVVB11": "IVVB11",
    "PETR4":  "PETR4",
    "VALE3":  "VALE3",
    "ITUB4":  "ITUB4",
}

TICK_SIZE_OVERRIDE = {
    "WDON26": 0.5,
    "WDOM26": 0.5,
    "WINM26": 5.0,
    # BITM26: cotado em BRL/BTC — variação mínima R$100 por BTC
    # Cada tick = 0.01 BTC × R$100 = R$1,00 por mini contrato
    "BITM26": 100.0,
    "XAUUSD": 0.01,
    "EURUSD": 0.00010,
    "USDBRL": 0.0010,
}

# Valor em R$ por tick por contrato (simulacao e exibicao de P&L)
# WDO:   1 tick (0.5 pts)   = R$5,00 por mini contrato
# WIN:   1 tick (5 pts)     = R$1,00 por mini contrato
# BITM:  1 tick (R$100 pts) = R$1,00 por mini contrato (0.01 BTC × R$100)
TICK_VALUE_BRL = {
    "WDON26": 5.0,
    "WDOM26": 5.0,
    "WINM26": 1.0,
    "BITM26": 1.0,
    "XAUUSD": 1.0,
    "EURUSD": 1.0,
    "USDBRL": 1.0,
    "GOLD11": 1.0,
    "BOVA11": 1.0,
    "PETR4":  1.0,
    "VALE3":  1.0,
}

# ── Configuração por símbolo — parâmetros de scoring e detecção ───────────
# Cada ativo tem seus próprios limiares calibrados para sua liquidez.
# _default é aplicado para símbolos sem config explícita.
SYMBOL_CONFIG: dict[str, dict] = {
    "_default": {
        # Burst velocity: ratio ticks-2s / média — tiers de pontuação
        "burst_ratio_strong":        3.0,   # >= 3.0 → 30 pts
        "burst_ratio_med":           2.0,   # >= 2.0 → 20 pts
        "burst_ratio_weak":          1.3,   # >= 1.3 → 10 pts
        "burst_min_ticks":           2,     # mínimo absoluto em 2s para 4 pts
        # Tick consistency — hard-block abaixo desses valores
        "consistency_hard_block":    55,    # % mínimo sem VWAP alignment
        "consistency_hb_aligned":    48,    # % mínimo com VWAP alignment
        # Entry quality — ticks de movimento que bloqueiam entrada
        "entry_hard_ticks":          4,     # > 4 ticks movidos → hard-block
        # Absorção/exaustão — volumes mínimos para detecção
        "absorption_min_vol":        50,    # vol mínimo p/ absorção
        "exhaustion_min_vol":        100,   # vol mínimo p/ exaustão
        # Book imbalance — limiares de pontuação
        "book_strong_pct":           30,    # imb% para 20 pts
        "book_weak_pct":             15,    # imb% para 12 pts
        "book_penalty_pct":          20,    # imb% contra para -8 pts
        # Volatility (20s)
        "volatility_min_ticks":      2,     # range mínimo para mercado "vivo"
        # Volatility 60s (P2 Scalper V2) — filtro de mercado morto
        "min_range_60s":             4,     # ticks mínimos em 60s → bloqueia com LOW_VOLATILITY
        "expansion_range_60s":       10,    # ticks > isso → bônus +10pts no score
    },

    # ── Mini Dólar / Dólar Futuro — alta liquidez ─────────────────────────
    "WDON26": {},
    "WDOM26": {},

    # ── Mini Índice Bovespa — alta liquidez ───────────────────────────────
    "WINM26": {
        "burst_ratio_strong":        3.0,
        "burst_ratio_med":           2.0,
        "burst_ratio_weak":          1.3,
        "burst_min_ticks":           2,
        "consistency_hard_block":    55,
        "consistency_hb_aligned":    48,
        "entry_hard_ticks":          4,
        "absorption_min_vol":        80,    # WIN tem contratos menores, mais vol
        "exhaustion_min_vol":        150,
        "book_strong_pct":           30,
        "book_weak_pct":             15,
        "book_penalty_pct":          20,
        "volatility_min_ticks":      2,
    },

    # ── Bitcoin Futuro B3 (BITM26) — liquidez baixa/média ─────────────────
    # Tick rate muito menor que WDO/WIN: bursts de 2-5 ticks já são significativos.
    "BITM26": {
        "burst_ratio_strong":        2.0,   # 2x já é burst forte p/ BTC
        "burst_ratio_med":           1.4,
        "burst_ratio_weak":          1.1,
        "burst_min_ticks":           1,     # 1 tick em 2s já conta
        "consistency_hard_block":    42,    # mais tolerante (menos ticks no buffer)
        "consistency_hb_aligned":    35,
        "entry_hard_ticks":          5,     # BTC move mais em pts, 5 ticks = tarde (P5)
        "absorption_min_vol":         3,    # vol é muito menor
        "exhaustion_min_vol":          6,
        "book_strong_pct":           20,    # book menos espesso
        "book_weak_pct":             10,
        "book_penalty_pct":          15,
        "volatility_min_ticks":       1,
        # Volatility 60s BITM26 — limiares menores (BTC tem menos ticks)
        "min_range_60s":              2,    # 2 ticks = R$200 de range mínimo em 60s
        "expansion_range_60s":        6,    # 6 ticks → bônus
    },

    # ── Ouro / XAUUSD — liquidez média ────────────────────────────────────
    "XAUUSD": {
        "burst_ratio_strong":        2.5,
        "burst_ratio_med":           1.7,
        "burst_ratio_weak":          1.2,
        "burst_min_ticks":           1,
        "consistency_hard_block":    48,
        "consistency_hb_aligned":    40,
        "entry_hard_ticks":          3,
        "absorption_min_vol":        10,
        "exhaustion_min_vol":        20,
        "book_strong_pct":           25,
        "book_weak_pct":             12,
        "book_penalty_pct":          18,
        "volatility_min_ticks":       1,
    },

    # ── EUR/USD — alta liquidez forex ────────────────────────────────────
    "EURUSD": {
        "burst_ratio_strong":        2.8,
        "burst_ratio_med":           1.8,
        "burst_ratio_weak":          1.2,
        "burst_min_ticks":           2,
        "consistency_hard_block":    50,
        "consistency_hb_aligned":    42,
        "entry_hard_ticks":          4,
        "absorption_min_vol":        15,
        "exhaustion_min_vol":        30,
        "book_strong_pct":           28,
        "book_weak_pct":             14,
        "book_penalty_pct":          20,
        "volatility_min_ticks":       2,
    },

    # ── USD/BRL ───────────────────────────────────────────────────────────
    "USDBRL": {
        "burst_ratio_strong":        2.5,
        "burst_ratio_med":           1.6,
        "burst_ratio_weak":          1.1,
        "burst_min_ticks":           1,
        "consistency_hard_block":    46,
        "consistency_hb_aligned":    38,
        "entry_hard_ticks":          3,
        "absorption_min_vol":         8,
        "exhaustion_min_vol":         15,
        "book_strong_pct":           22,
        "book_weak_pct":             11,
        "book_penalty_pct":          16,
        "volatility_min_ticks":       1,
    },

    # ── Ações B3 (PETR4, VALE3, ITUB4) — liquidez média ──────────────────
    "PETR4": {
        "burst_ratio_strong":        2.5,
        "burst_ratio_med":           1.6,
        "burst_ratio_weak":          1.2,
        "burst_min_ticks":           1,
        "consistency_hard_block":    48,
        "consistency_hb_aligned":    40,
        "entry_hard_ticks":          3,
        "absorption_min_vol":        20,
        "exhaustion_min_vol":        50,
        "book_strong_pct":           25,
        "book_weak_pct":             12,
        "book_penalty_pct":          18,
        "volatility_min_ticks":       1,
    },
    "VALE3": {
        "burst_ratio_strong":        2.5,
        "burst_ratio_med":           1.6,
        "burst_ratio_weak":          1.2,
        "burst_min_ticks":           1,
        "consistency_hard_block":    48,
        "consistency_hb_aligned":    40,
        "entry_hard_ticks":          3,
        "absorption_min_vol":        20,
        "exhaustion_min_vol":        50,
        "book_strong_pct":           25,
        "book_weak_pct":             12,
        "book_penalty_pct":          18,
        "volatility_min_ticks":       1,
    },
    "ITUB4": {
        "burst_ratio_strong":        2.5,
        "burst_ratio_med":           1.6,
        "burst_ratio_weak":          1.2,
        "burst_min_ticks":           1,
        "consistency_hard_block":    48,
        "consistency_hb_aligned":    40,
        "entry_hard_ticks":          3,
        "absorption_min_vol":        20,
        "exhaustion_min_vol":        50,
        "book_strong_pct":           25,
        "book_weak_pct":             12,
        "book_penalty_pct":          18,
        "volatility_min_ticks":       1,
    },
    "GOLD11": {
        "burst_ratio_strong":        2.5,
        "burst_ratio_med":           1.6,
        "burst_ratio_weak":          1.2,
        "burst_min_ticks":           1,
        "consistency_hard_block":    46,
        "consistency_hb_aligned":    38,
        "entry_hard_ticks":          3,
        "absorption_min_vol":        10,
        "exhaustion_min_vol":        20,
        "book_strong_pct":           22,
        "book_weak_pct":             11,
        "book_penalty_pct":          16,
        "volatility_min_ticks":       1,
    },
}


def _sym_cfg(mt5_sym: str) -> dict:
    """Retorna config mesclada: _default + override do símbolo específico."""
    base     = dict(SYMBOL_CONFIG["_default"])
    override = SYMBOL_CONFIG.get(mt5_sym, {})
    base.update(override)
    return base


# ── Buffer de ticks por símbolo (rastreamento de agressão) ─────────────────
_MAX_TAPE   = 200
_AGGR_WIN   = 30
_tick_buf:  dict[str, deque] = {}
_last_tick: dict[str, tuple] = {}

# Símbolos que operam fora do horário B3 (forex, metais)
_SYMBOLS_24H = {"XAUUSD", "EURUSD", "USDBRL", "GBPUSD", "BTCUSD"}

# ── Modo Simulação ─────────────────────────────────────────────────────────
_sim_mode   = False

_SIM_DEFAULT_PRICES = {
    "WDON26": 5850.0,  "WDOM26": 5850.0,  "WINM26": 132000.0,
    "BITM26": 620000.0,  # BTC aprox. R$620.000 (BTC ~$107k × BRL ~5,80)
    "XAUUSD": 3250.0,  "EURUSD": 1.0850,  "USDBRL": 5.75,
    "GOLD11": 385.0,   "BOVA11": 132.0,   "IVVB11": 320.0,
    "PETR4":  36.0,    "VALE3":  58.0,    "ITUB4":  35.0,
}

_sim_prices:          dict[str, float] = {}   # preço atual simulado
_sim_trends:          dict[str, int]   = {}   # -1 | 0 | +1 (viés de tendência)
_sim_position:        dict[str, dict]  = {}   # symbol -> posição sim ou None
_sim_closed_profits:  dict[int, float] = {}   # ticket -> profit final
_sim_ticket_counter   = 90_000_000
_pos_open_times:      dict[int, float] = {}   # ticket -> timestamp abertura (modo real)

# ── Novos acumuladores (VWAP, Delta Cumulativo, cache) ────────────────────
_cum_delta_data: dict[str, dict] = {}   # sym -> {buy, sell, date}
_vwap_cache:     dict[str, dict] = {}   # sym -> {vwap, atr_1m, context, ts}


def set_sim_mode(enabled: bool) -> None:
    global _sim_mode
    _sim_mode = bool(enabled)
    logger.info("Scalper SIM MODE: %s", "ON" if _sim_mode else "OFF")


def get_sim_mode() -> bool:
    return _sim_mode


def get_sim_closed_profit(ticket: int):
    """Retorna o lucro de uma posição simulada já fechada (para /api/scalper/last-deal)."""
    return _sim_closed_profits.get(ticket)


# ── Helpers de mercado ─────────────────────────────────────────────────────
def _is_b3_open() -> bool:
    brt = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=-3))).time()
    return dt_time(9, 0) <= brt <= dt_time(17, 30)


def _is_market_open(symbol: str) -> bool:
    if symbol in _SYMBOLS_24H:
        now     = datetime.now(timezone.utc)
        weekday = now.weekday()
        hour    = now.hour
        if weekday == 5:               return False
        if weekday == 6 and hour < 22: return False
        if weekday == 4 and hour >= 22:return False
        return True
    return _is_b3_open()


def _resolve_symbol(symbol: str) -> str:
    s = symbol.upper().strip()
    return SCALPER_SYMBOLS.get(s, s)


def _mt5_init() -> bool:
    try:
        import MetaTrader5 as mt5
        if mt5.terminal_info() is not None:
            return True
        login    = int(os.getenv("MT5_LOGIN", "0") or 0)
        password = os.getenv("MT5_PASSWORD", "")
        server   = os.getenv("MT5_SERVER", "")
        mt5_path = os.getenv("MT5_PATH", "")
        _mq      = {"metaquotes-demo", "metaquotes-demo2"}
        kwargs: dict = {}
        if mt5_path and os.path.exists(mt5_path):
            kwargs["path"] = mt5_path
        if login and password and server and server.lower() not in _mq:
            kwargs["login"]    = login
            kwargs["password"] = password
            kwargs["server"]   = server
        return mt5.initialize(**kwargs)
    except Exception as exc:
        logger.warning("Scalper MT5 init error: %s", exc)
        return False


# ── Simulação: geração de ticks sintéticos ────────────────────────────────
def _sim_next_price(symbol: str) -> float:
    """Random walk simples — preco segue a fase atual do ciclo."""
    ts      = TICK_SIZE_OVERRIDE.get(symbol, 0.01)
    price   = _sim_prices.get(symbol, _SIM_DEFAULT_PRICES.get(symbol, 100.0))
    phase   = _sim_phase(symbol)          # "BUY" | "SELL" | "NEUTRO"

    bias = {"BUY": 0.72, "SELL": 0.28, "NEUTRO": 0.50}[phase]
    direction = 1 if random.random() < bias else -1
    price = round(price + random.randint(0, 1) * ts * direction, 6)

    default = _SIM_DEFAULT_PRICES.get(symbol, 100.0)
    price   = max(default * 0.95, min(default * 1.05, price))
    _sim_prices[symbol] = price
    return price


def _sim_phase(symbol: str) -> str:
    """
    Ciclo deterministico por tempo — independe de estado aleatorio:
      0-15s  : BUY  (agressao compra ~72%)
      15-25s : NEUTRO
      25-40s : SELL (agressao venda ~72%)
      40-50s : NEUTRO
      50-65s : BUY
      ... repete a cada 65s
    Cada simbolo tem um offset diferente para nao disparar ao mesmo tempo.
    """
    offset  = abs(hash(symbol)) % 65
    t       = (time.time() + offset) % 65
    if   t < 15:  return "BUY"
    elif t < 25:  return "NEUTRO"
    elif t < 40:  return "SELL"
    elif t < 50:  return "NEUTRO"
    else:         return "BUY"


def _sim_fill_buf(symbol: str, price: float, ts_now: float) -> None:
    """Gera negocios sinteticos com vies direto da fase do ciclo."""
    if symbol not in _tick_buf:
        _tick_buf[symbol] = deque(maxlen=_MAX_TAPE)

    phase     = _sim_phase(symbol)
    bias      = {"BUY": 0.78, "SELL": 0.22, "NEUTRO": 0.50}[phase]
    tick_size = TICK_SIZE_OVERRIDE.get(symbol, 0.01)
    n_trades  = random.randint(3, 8)
    bid       = round(price - tick_size, 6)
    ask       = round(price + tick_size, 6)

    for i in range(n_trades):
        side        = "BUY" if random.random() < bias else "SELL"
        trade_price = ask if side == "BUY" else bid
        vol         = random.randint(2, 20) * (1 if phase == "NEUTRO" else 2)
        _tick_buf[symbol].append({
            "ts":   ts_now + i * 0.05,
            "last": trade_price,
            "bid":  bid,
            "ask":  ask,
            "vol":  float(vol),
            "side": side,
        })

def _sim_build_book(symbol: str, price: float, depth: int = 10) -> dict:
    """Gera book sintético com depth níveis em cada lado."""
    ts   = TICK_SIZE_OVERRIDE.get(symbol, 0.01)
    asks, bids = [], []
    max_vol = 500

    for i in range(1, depth + 1):
        vol_a = random.randint(50, 500)
        vol_b = random.randint(50, 500)
        asks.append({
            "price":   round(price + i * ts, 6),
            "vol":     vol_a,
            "bar_pct": round(vol_a / max_vol * 100, 1),
        })
        bids.append({
            "price":   round(price - i * ts, 6),
            "vol":     vol_b,
            "bar_pct": round(vol_b / max_vol * 100, 1),
        })

    spread = round(2 * ts, 6)
    return {
        "asks":   sorted(asks, key=lambda x: x["price"], reverse=True),
        "bids":   sorted(bids, key=lambda x: x["price"], reverse=True),
        "spread": spread,
    }


# ── Buffer de agressão (modo real) ─────────────────────────────────────────
def _update_tick_buffer(mt5_sym: str, tick) -> None:
    if mt5_sym not in _tick_buf:
        _tick_buf[mt5_sym] = deque(maxlen=_MAX_TAPE)

    prev = _last_tick.get(mt5_sym)
    cur_last = float(tick.last)  if tick.last  else 0.0
    cur_bid  = float(tick.bid)   if tick.bid   else 0.0
    cur_ask  = float(tick.ask)   if tick.ask   else 0.0
    cur_vol  = (float(tick.volume_real)
                if hasattr(tick, "volume_real") and tick.volume_real
                else float(tick.volume) if tick.volume else 0.0)
    cur_tmsc = int(tick.time_msc) if hasattr(tick, "time_msc") else int(tick.time * 1000)

    if prev is None:
        _last_tick[mt5_sym] = (cur_last, cur_bid, cur_ask, cur_vol, cur_tmsc)
        return

    prev_last, prev_bid, prev_ask, prev_vol, prev_tmsc = prev

    if cur_last > 0 and (cur_last != prev_last or cur_tmsc != prev_tmsc):
        if cur_last >= cur_ask and cur_ask > 0:
            side = "BUY"
        elif cur_last <= cur_bid and cur_bid > 0:
            side = "SELL"
        else:
            side = "BUY" if cur_last >= prev_last else "SELL"

        vol = cur_vol if cur_vol > 0 else 1.0
        _tick_buf[mt5_sym].append({
            "ts":   time.time(),
            "last": cur_last,
            "bid":  cur_bid,
            "ask":  cur_ask,
            "vol":  vol,
            "side": side,
        })

    _last_tick[mt5_sym] = (cur_last, cur_bid, cur_ask, cur_vol, cur_tmsc)

    # Acumular delta cumulativo diário
    if cur_last > 0 and (cur_last != (prev[0] if prev else -1)):
        _accumulate_delta(mt5_sym, {"vol": vol, "side": side})


def _accumulate_delta(sym: str, entry: dict) -> None:
    """Atualiza delta cumulativo do dia (chamado a cada novo tick classificado)."""
    today = datetime.now(timezone.utc).astimezone(
        timezone(timedelta(hours=-3))).date().isoformat()
    if sym not in _cum_delta_data or _cum_delta_data[sym].get("date") != today:
        _cum_delta_data[sym] = {"buy": 0.0, "sell": 0.0, "date": today}
    if entry["side"] == "BUY":
        _cum_delta_data[sym]["buy"] += entry["vol"]
    else:
        _cum_delta_data[sym]["sell"] += entry["vol"]


def _get_cumulative_delta(sym: str) -> dict:
    """Delta acumulado desde abertura do dia."""
    d     = _cum_delta_data.get(sym, {"buy": 0.0, "sell": 0.0})
    buy   = d["buy"];  sell = d["sell"]
    delta = buy - sell
    total = buy + sell
    pct   = round(buy / total * 100, 1) if total > 0 else 50.0
    if   pct >= 57: bias = "BULLISH"  # comprador domina (era 60)
    elif pct <= 43: bias = "BEARISH"  # vendedor domina (era 40)
    else:           bias = "NEUTRO"
    return {
        "delta":    round(delta, 0),
        "buy_vol":  round(buy,   0),
        "sell_vol": round(sell,  0),
        "buy_pct":  pct,
        "bias":     bias,
    }


def _calc_vwap_atr(mt5_sym: str) -> dict:
    """VWAP intraday e ATR(14) de 1min via candles MT5. Cache 30s."""
    now_ts = time.time()
    cached = _vwap_cache.get(mt5_sym, {})
    if cached.get("ts", 0) > now_ts - 30:
        return cached

    try:
        import MetaTrader5 as mt5
        brt_now       = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=-3)))
        session_start = brt_now.replace(hour=9, minute=0, second=0, microsecond=0)
        utc_start     = session_start.astimezone(timezone.utc)

        rates = mt5.copy_rates_from(mt5_sym, mt5.TIMEFRAME_M1, utc_start, 500)
        if rates is None or len(rates) == 0:
            result = {"vwap": None, "atr_1m": None, "context": "NEUTRO",
                      "price": None,
                      "day_open": None, "day_high": None, "day_low": None,
                      "day_amplitude": None, "day_dist_open": None, "day_dist_min": None,
                      "ts": now_ts}
            _vwap_cache[mt5_sym] = result
            return result

        # VWAP: Σ(típico × volume) / Σvolume
        pv_sum = sum((r["high"] + r["low"] + r["close"]) / 3.0 * max(r["tick_volume"], 1)
                     for r in rates)
        v_sum  = sum(max(r["tick_volume"], 1) for r in rates)
        vwap   = pv_sum / v_sum if v_sum > 0 else None

        # ATR(14) de 1 minuto
        atr = None
        if len(rates) >= 3:
            trs = []
            for i in range(1, len(rates)):
                h, l, pc = rates[i]["high"], rates[i]["low"], rates[i-1]["close"]
                trs.append(max(h - l, abs(h - pc), abs(l - pc)))
            window = trs[-14:] if len(trs) >= 14 else trs
            atr    = sum(window) / len(window) if window else None

        last_price  = float(rates[-1]["close"])
        open_price  = float(rates[0]["open"])
        high_price  = max(float(r["high"]) for r in rates)
        low_price   = min(float(r["low"])  for r in rates)
        amplitude   = round(high_price - low_price, 2)
        dist_open   = round(last_price - open_price, 2)
        dist_min    = round(last_price - low_price,  2)

        ts_val     = TICK_SIZE_OVERRIDE.get(mt5_sym, 0.5)
        context    = "NEUTRO"
        if vwap:
            if   last_price > vwap + ts_val: context = "BULL"
            elif last_price < vwap - ts_val: context = "BEAR"

        result = {
            "vwap":         round(vwap,       2) if vwap  else None,
            "atr_1m":       round(atr / ts_val, 1) if atr and ts_val > 0 else None,  # em ticks
            "context":      context,
            "price":        round(last_price, 2),
            # ── Valores do dia (novos) ──────────────────────────────────────
            "day_open":     round(open_price, 2),
            "day_high":     round(high_price, 2),
            "day_low":      round(low_price,  2),
            "day_amplitude": amplitude,
            "day_dist_open": dist_open,
            "day_dist_min":  dist_min,
            "ts":           now_ts,
        }
        _vwap_cache[mt5_sym] = result
        return result

    except Exception as exc:
        logger.warning("_calc_vwap_atr: %s", exc)
        result = {"vwap": None, "atr_1m": None, "context": "NEUTRO", "price": None,
                  "day_open": None, "day_high": None, "day_low": None,
                  "day_amplitude": None, "day_dist_open": None, "day_dist_min": None,
                  "ts": now_ts}
        _vwap_cache[mt5_sym] = result
        return result


def _get_time_weight() -> dict:
    """Peso e classificação do horário de trading B3."""
    brt = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=-3))).time()
    t   = brt.hour * 60 + brt.minute   # minutos desde 00:00

    if   540 <= t <  555: return {"weight":   0, "session": "FECHADO",    "label": "Aguardando abertura (09:00-09:15)"}  # primeiros 15min: ruído de abertura
    elif 555 <= t <  630: return {"weight": 100, "session": "PRIME",      "label": "Prime (09:15-10:30)"}
    elif 630 <= t <  720: return {"weight":  80, "session": "BOM",        "label": "Bom (10:30-12:00)"}
    elif 720 <= t <  840: return {"weight":  25, "session": "PERIGOSO",   "label": "Perigoso (12-14h)"}
    elif 840 <= t <  990: return {"weight":  80, "session": "BOM",        "label": "Bom (14:00-16:30)"}
    elif 990 <= t < 1020: return {"weight":  50, "session": "FECHAMENTO", "label": "Fechamento (16:30-17h)"}
    else:                 return {"weight":   0, "session": "FECHADO",    "label": "Fora do horario"}


def _detect_absorption_exhaustion(mt5_sym: str) -> dict:
    """Detecta absorção (preço não move com volume alto) e exaustão (volume alto, movimento pequeno)."""
    buf = _tick_buf.get(mt5_sym)
    _neutral = {
        "absorption_buy": False, "absorption_sell": False,
        "exhaustion_buy": False, "exhaustion_sell": False,
        "signal": "NEUTRO", "buy_vol_10s": 0, "sell_vol_10s": 0, "range_ticks": 0,
    }
    if not buf or len(buf) < 10:
        return _neutral

    now_ts    = time.time()
    ticks_10s = [e for e in buf if now_ts - e["ts"] <= 10]
    if len(ticks_10s) < 5:
        return _neutral

    ts_val    = TICK_SIZE_OVERRIDE.get(mt5_sym, 0.5)
    prices    = [e["last"] for e in ticks_10s if e["last"] > 0]
    p_range   = (max(prices) - min(prices)) if prices else 0
    r_ticks   = round(p_range / ts_val, 1) if ts_val > 0 else 0

    buy_vol   = sum(e["vol"] for e in ticks_10s if e["side"] == "BUY")
    sell_vol  = sum(e["vol"] for e in ticks_10s if e["side"] == "SELL")
    total_vol = buy_vol + sell_vol

    # Absorção: muito volume de um lado mas preço NÃO se move (≤ 1 tick)
    # Limiares por símbolo (BITM26 e ações têm vol muito menor que WDO/WIN)
    cfg      = _sym_cfg(mt5_sym)
    abs_min  = cfg["absorption_min_vol"]
    exh_min  = cfg["exhaustion_min_vol"]
    # Absorção compradora = muito sell, preço não cai → compradores institucionais absorvendo
    abs_buy  = sell_vol > abs_min and r_ticks <= 1.0 and total_vol > 0 and sell_vol / max(total_vol, 1) > 0.65
    # Absorção vendedora = muito buy, preço não sobe → vendedores absorvendo
    abs_sell = buy_vol  > abs_min and r_ticks <= 1.0 and total_vol > 0 and buy_vol  / max(total_vol, 1) > 0.65

    # Exaustão: muito volume de um lado mas preço move POUCO (< 1 tick) — compradores/vendedores sem força
    exh_buy  = buy_vol  > exh_min and buy_vol  > sell_vol * 2 and r_ticks < 1.0
    exh_sell = sell_vol > exh_min and sell_vol > buy_vol  * 2 and r_ticks < 1.0

    signal = "NEUTRO"
    if   abs_buy:  signal = "COMPRA"   # compradores absorvendo pressão vendedora
    elif abs_sell: signal = "VENDA"    # vendedores absorvendo pressão compradora

    return {
        "absorption_buy":  abs_buy,
        "absorption_sell": abs_sell,
        "exhaustion_buy":  exh_buy,
        "exhaustion_sell": exh_sell,
        "signal":          signal,
        "buy_vol_10s":     int(buy_vol),
        "sell_vol_10s":    int(sell_vol),
        "range_ticks":     r_ticks,
    }


def _calc_book_imbalance(book_data: dict, levels: int = 10) -> dict:
    """Calcula desequilíbrio do book usando até N níveis."""
    bids = book_data.get("bids", [])[:levels]
    asks = book_data.get("asks", [])[:levels]
    bid_vol = sum(r.get("vol", 0) for r in bids)
    ask_vol = sum(r.get("vol", 0) for r in asks)
    total   = bid_vol + ask_vol
    if total == 0:
        return {"bid_vol": 0, "ask_vol": 0, "imbalance_pct": 0, "bias": "NEUTRO", "levels": 0}
    imb = round((bid_vol - ask_vol) / total * 100, 1)  # positivo = mais bid (compra)
    if   imb >  20: bias = "BID_HEAVY"    # compradores dominando
    elif imb < -20: bias = "ASK_HEAVY"    # vendedores dominando
    else:           bias = "NEUTRO"
    return {
        "bid_vol":      int(bid_vol),
        "ask_vol":      int(ask_vol),
        "imbalance_pct": imb,
        "bias":          bias,
        "levels":        max(len(bids), len(asks)),
    }



def _get_delta_shift(mt5_sym: str) -> dict:
    """Detecta REVERSÃO no delta de curto prazo — o sinal mais preditivo.
    Compara delta recente (últimos 8s) vs anterior (8-30s).
    Uma reversão delta = começo de movimento → melhor momento de entrada."""
    buf = _tick_buf.get(mt5_sym)
    if not buf:
        return {"recent_pct": 50.0, "older_pct": 50.0, "shift": 0.0, "direction": "NEUTRO"}

    now = time.time()
    recent_ticks = [e for e in buf if e["ts"] >= now - 8]
    older_ticks  = [e for e in buf if now - 30 <= e["ts"] < now - 8]

    def _wpct(ticks):
        bv = sum(e["vol"] for e in ticks if e["side"] == "BUY")
        tv = sum(e["vol"] for e in ticks)
        return round(bv / tv * 100, 1) if tv > 0 else 50.0

    rp = _wpct(recent_ticks)
    op = _wpct(older_ticks)
    shift = round(rp - op, 1)  # >0 = virou comprador; <0 = virou vendedor

    if   shift >= 15 and rp >= 55: direction = "VIROU_COMPRA"  # reversão para compra
    elif shift <= -15 and rp <= 45: direction = "VIROU_VENDA"   # reversão para venda
    elif rp >= 60:                  direction = "COMPRA"
    elif rp <= 40:                  direction = "VENDA"
    else:                           direction = "NEUTRO"

    return {"recent_pct": rp, "older_pct": op, "shift": shift, "direction": direction}


def _get_price_position(mt5_sym: str, window_sec: int = 45) -> dict:
    """Posição do preço atual no range dos últimos window_sec segundos.
    0% = mínima do período (ideal para COMPRA), 100% = máxima (ideal para VENDA).
    Evita entrar comprando no topo ou vendendo no fundo de um move."""
    buf = _tick_buf.get(mt5_sym)
    if not buf:
        return {"position": 50.0, "range_ticks": 0, "at_extreme": False}

    now    = time.time()
    recent = [e for e in buf if e["ts"] >= now - window_sec and e["last"] > 0]
    if len(recent) < 5:
        return {"position": 50.0, "range_ticks": 0, "at_extreme": False}

    prices    = [e["last"] for e in recent]
    hi, lo    = max(prices), min(prices)
    curr      = prices[-1]
    ts_val    = TICK_SIZE_OVERRIDE.get(mt5_sym, 0.5)
    p_range   = hi - lo
    r_ticks   = round(p_range / ts_val, 1) if ts_val > 0 else 0
    position  = round((curr - lo) / p_range * 100, 1) if p_range > 0 else 50.0

    return {
        "position":   position,   # 0=at_low, 50=mid, 100=at_high
        "range_ticks": r_ticks,
        "hi": hi, "lo": lo, "curr": curr,
        "at_extreme": position <= 15 or position >= 85,
    }

def _calc_multi_score(
    mt5_sym: str, signal_dir: str,
    aggr5: dict, aggr30: dict, velocity: dict,
    book_imb: dict, vwap_data: dict, flow: dict,
    time_w: dict, threshold_pct: float = 65.0,
) -> dict:
    """
    BURST DETECTION Score 0-100

    Filosofia: detectar o INICIO de uma explosao direcional de 3-10 segundos.
    Entrada cedo = sobrevive ao SL. Entrada tarde = pega o retorno.

    Componentes:
      burst_velocity   30pts  — aceleração de ticks nos últimos 2s vs média 30s
      tick_consistency 30pts  — % dos últimos 10 ticks na direção do sinal
      book_pressure    20pts  — desequilíbrio do book AGORA (leading indicator)
      entry_quality    20pts  — preço ainda não se moveu muito (entrando cedo)
      absorcao_bonus  +10pts  — bônus se absorção institucional confirmada
      cum_delta_pen   -15pts  — penalidade se delta acumulado muito contra

    Hard-blocks:
      tick_consistency < 55% na direção: sinal sem burst real — bloqueia
      entry_quality: preço já se moveu > 4 ticks nessa direção — tarde demais
    """
    if signal_dir not in ("COMPRA", "VENDA"):
        return {"score": 0, "breakdown": {}, "reasons": ["Sem sinal"],
                "hard_blocked": False, "cum_bias": "NEUTRO", "cum_pct": 50.0}

    is_buy       = signal_dir == "COMPRA"
    pts          = {}
    reasons      = []
    hard_blocked = False
    ts_val       = TICK_SIZE_OVERRIDE.get(mt5_sym, 0.5)
    buf          = _tick_buf.get(mt5_sym)
    now          = time.time()
    scfg         = _sym_cfg(mt5_sym)   # config específica do símbolo

    # Alinhamento VWAP: define se estamos operando A FAVOR da tendência
    vwap_context = vwap_data.get("context", "NEUTRO")
    vwap_aligned = (is_buy and vwap_context == "BULL") or (not is_buy and vwap_context == "BEAR")
    # Hard-block de consistência: por símbolo (BITM26 tem buffer menor → mais tolerante)
    consistency_hard_block = scfg["consistency_hb_aligned"] if vwap_aligned else scfg["consistency_hard_block"]

    # ── 1. BURST VELOCITY — 30 pts ──────────────────────────────────────────
    # Quantos ticks chegaram nos últimos 2s vs média dos últimos 30s?
    # Aceleração 3x = instituição entrando agressivamente AGORA.
    count_2s  = 0
    avg_per_2s = 1.0
    if buf:
        ticks_2s  = [e for e in buf if e["ts"] >= now - 2]
        ticks_30s = [e for e in buf if e["ts"] >= now - 30]
        count_2s  = len(ticks_2s)
        count_30s = len(ticks_30s)
        avg_per_2s = max(0.5, count_30s / 15.0)  # avg de ticks por janela de 2s

        ratio = count_2s / avg_per_2s
        br_s = scfg["burst_ratio_strong"]; br_m = scfg["burst_ratio_med"]
        br_w = scfg["burst_ratio_weak"];   br_min = scfg["burst_min_ticks"]
        if   ratio >= br_s:         pts["burst_vel"] = 30; reasons.append(f"🚀 Burst forte {count_2s}tk/2s ({avg_per_2s:.1f} media)")
        elif ratio >= br_m:         pts["burst_vel"] = 20; reasons.append(f"⚡ Burst moderado {count_2s}tk/2s")
        elif ratio >= br_w:         pts["burst_vel"] = 10
        elif count_2s >= br_min:    pts["burst_vel"] =  4
        else:                       pts["burst_vel"] =  0; reasons.append(f"Tape parado ({count_2s}tk/2s)")
    else:
        pts["burst_vel"] = 0
        reasons.append("Sem dados de tape")

    # ── 2. TICK CONSISTENCY — 30 pts ────────────────────────────────────────
    # Dos últimos 10 ticks, qual % está na direção do sinal?
    # 90%+ = burst limpo; <55% = ruído, bloqueia.
    consistency = 50.0
    if buf:
        last10 = list(buf)[-10:]
        n10    = len(last10)
        if n10 >= 3:
            buy10       = sum(1 for e in last10 if e["side"] == "BUY")
            buy_pct10   = buy10 / n10 * 100
            consistency = buy_pct10 if is_buy else (100.0 - buy_pct10)

            if   consistency >= 90: pts["tick_consistency"] = 30; reasons.append(f"✅ {consistency:.0f}% ticks → {'C' if is_buy else 'V'}")
            elif consistency >= 80: pts["tick_consistency"] = 22; reasons.append(f"✅ {consistency:.0f}% consistente")
            elif consistency >= 70: pts["tick_consistency"] = 12
            elif consistency >= 60: pts["tick_consistency"] =  5
            elif consistency >= 55: pts["tick_consistency"] =  0
            elif consistency >= consistency_hard_block: pts["tick_consistency"] = 0  # zona tolerada em tendência
            else:
                pts["tick_consistency"] =  0
                hard_blocked = True
                reasons.append(f"🚫 Ticks inconsistentes ({consistency:.0f}%) — sem burst real")
        else:
            pts["tick_consistency"] = 5  # poucos dados, neutro
    else:
        pts["tick_consistency"] = 0

    # ── 3. BOOK PRESSURE — 20 pts ───────────────────────────────────────────
    # Desequilíbrio do book: imbalance_pct > 0 = bid pesado (pressão compra).
    # Leading indicator: o book pesado ANTECEDE o movimento de preço.
    imb  = book_imb.get("imbalance_pct", 0)
    bs   = scfg["book_strong_pct"]; bw = scfg["book_weak_pct"]; bp = scfg["book_penalty_pct"]
    if is_buy:
        if   imb >=  bs:  pts["book_pressure"] = 20; reasons.append(f"✅ Book comprador ({imb:+.0f}%)")
        elif imb >=  bw:  pts["book_pressure"] = 12
        elif imb >=   0:  pts["book_pressure"] =  5
        elif imb >= -bw:  pts["book_pressure"] =  0
        else:             pts["book_pressure"] = -8; reasons.append(f"⚠ Book vendedor ({imb:+.0f}%)")
    else:
        if   imb <= -bs:  pts["book_pressure"] = 20; reasons.append(f"✅ Book vendedor ({imb:+.0f}%)")
        elif imb <= -bw:  pts["book_pressure"] = 12
        elif imb <=   0:  pts["book_pressure"] =  5
        elif imb <=  bw:  pts["book_pressure"] =  0
        else:             pts["book_pressure"] = -8; reasons.append(f"⚠ Book comprador ({imb:+.0f}%)")

    # ── 4. ENTRY QUALITY — 20 pts ───────────────────────────────────────────
    # Quanto o preço já se moveu na direção do sinal nos últimos 5s?
    # Entrar antes do move = ideal. Entrar depois de 4+ ticks = tarde demais.
    if buf:
        ticks_5s = [e for e in buf if e["ts"] >= now - 5 and e["last"] > 0]
        if len(ticks_5s) >= 2:
            price_start = ticks_5s[0]["last"]
            price_now   = ticks_5s[-1]["last"]
            move_pts    = price_now - price_start
            move_ticks  = abs(move_pts) / ts_val if ts_val > 0 else 0

            if is_buy:
                if   move_pts <= 0:         pts["entry_quality"] = 20; reasons.append("✅ Preço ainda não subiu")
                elif move_ticks <= 1:       pts["entry_quality"] = 15
                elif move_ticks <= 2:       pts["entry_quality"] =  8
                elif move_ticks <= scfg["entry_hard_ticks"]: pts["entry_quality"] = 0; reasons.append(f"Preço já subiu {move_ticks:.1f}tk")
                else:
                    pts["entry_quality"] = -10; hard_blocked = True
                    reasons.append(f"🚫 Tarde: preço subiu {move_ticks:.1f}tk")
            else:
                if   move_pts >= 0:         pts["entry_quality"] = 20; reasons.append("✅ Preço ainda não caiu")
                elif move_ticks <= 1:       pts["entry_quality"] = 15
                elif move_ticks <= 2:       pts["entry_quality"] =  8
                elif move_ticks <= scfg["entry_hard_ticks"]: pts["entry_quality"] = 0; reasons.append(f"Preço já caiu {move_ticks:.1f}tk")
                else:
                    pts["entry_quality"] = -10; hard_blocked = True
                    reasons.append(f"🚫 Tarde: preço caiu {move_ticks:.1f}tk")
        else:
            pts["entry_quality"] = 10
    else:
        pts["entry_quality"] = 10

    # ── 5. Bônus absorção institucional — +10 pts ────────────────────────────
    abs_ok = (is_buy and flow.get("absorption_buy")) or (not is_buy and flow.get("absorption_sell"))
    exh_ok = (is_buy and flow.get("exhaustion_sell")) or (not is_buy and flow.get("exhaustion_buy"))
    if   abs_ok: pts["absorcao_bonus"] = 10; reasons.append("✅ Absorção confirmada")
    elif exh_ok: pts["absorcao_bonus"] =  5; reasons.append("Exaustão oposta")
    else:        pts["absorcao_bonus"] =  0

    # ── 6. Penalidade delta cumulativo — máx -15 pts ─────────────────────────
    # Não é hard-block: um burst pode ir contra o delta cumulativo temporariamente.
    cum_d    = _get_cumulative_delta(mt5_sym)
    cum_bias = cum_d.get("bias",    "NEUTRO")
    cum_pct  = cum_d.get("buy_pct", 50.0)

    if is_buy and cum_bias == "BEARISH":
        pts["cum_delta_pen"] = -15
        reasons.append(f"⚠ Delta acum. BEARISH ({cum_pct:.0f}% C)")
    elif not is_buy and cum_bias == "BULLISH":
        pts["cum_delta_pen"] = -15
        reasons.append(f"⚠ Delta acum. BULLISH ({cum_pct:.0f}% C)")
    else:
        pts["cum_delta_pen"] = 0

    # ── 7. Bônus alinhamento VWAP — +12 pts ─────────────────────────────────
    # Opera a favor da tendência intraday = barreira menor, mais oportunidades.
    # Pullbacks em downtrend são entradas VENDA mesmo com ticks momentaneamente comprados.
    if vwap_aligned and vwap_context != "NEUTRO":
        pts["vwap_trend_bonus"] = 12
        reasons.append(f"✅ Tendência VWAP {vwap_context} alinhada")
    else:
        pts["vwap_trend_bonus"] = 0

    # ── 8. Penalidade exaustao de impulso ───────────────────────────────────
    # Dados historicos (133 trades): score 80-100 -> win rate 9%, loss rate 82%.
    # Causa: burst_vel maximo (ratio >=3x) + tick_consistency maximo (>=90%) simultaneos
    # indicam que o movimento JA ACONTECEU -- entramos no teto do impulso, nao no inicio.
    # Grau 1 (severo): burst maximo + consistencia >=90% -> -20pts
    # Grau 2 (moderado): burst maximo + consistencia >=80% -> -10pts
    _burst_max       = pts.get("burst_vel", 0) >= 30
    _consistency_pts = pts.get("tick_consistency", 0)

    # v6 ASSERTIVIDADE: dados reais (scalper_trades.csv, BITM26) mostram que
    # score ALTO = PIOR resultado: faixa 90-109 teve 0% de acerto; 80-89 ~33%;
    # 60-79 (melhor) ~32-37%. A combinacao burst-maximo + tape-maximo indica que
    # o movimento JA aconteceu — estamos entrando no TETO do impulso, nao no
    # inicio. Antes isso era so uma penalidade (-20); agora vira HARD-BLOCK.
    if _burst_max and _consistency_pts >= 30:       # burst >=3x + tape >=90% -> exaustao
        pts["exaustao_pen"] = -20
        hard_blocked = True
        reasons.append("🚫 Exaustao no teto: burst x3+ com tape 90%+ -> entrada tardia bloqueada")
    elif _burst_max and _consistency_pts >= 22:     # burst >=3x + tape >=80%
        pts["exaustao_pen"] = -18   # v6: era -10 (penalidade mais forte)
        reasons.append("AVISO Burst extremo com tape 80%+ -> atencao a exaustao")
    else:
        pts["exaustao_pen"] = 0

    # ── 9. Bônus expansão de volatilidade 60s (P2 Scalper V2) ─────────────────
    # Mercado em expansão de range → movimento mais definido → +10pts
    vol60 = _calc_volatility(mt5_sym)
    if vol60.get("expansion"):
        pts["expansion_bonus"] = 10
        reasons.append(f"📈 Expansão 60s: {vol60['range_60s']:.0f}tk > {vol60['expansion_range_60s']}tk")
    else:
        pts["expansion_bonus"] = 0

    # ── Score final ──────────────────────────────────────────────────────────
    total = round(sum(pts.values()), 1)
    return {
        "score":             min(100, max(0, total)),
        "breakdown":         pts,
        "reasons":           reasons,
        "hard_blocked":      hard_blocked,
        "cum_bias":          cum_bias,
        "cum_pct":           cum_pct,
        "tick_consistency":  round(consistency, 1),
        "burst_vel_2s":      count_2s,
        "vwap_aligned":      vwap_aligned,
        "range_60s":         vol60.get("range_60s", 0),
        "vol60_ok":          vol60.get("vol60_ok", True),
    }

def _calc_aggr(mt5_sym: str, window_sec: int) -> dict:
    buf = _tick_buf.get(mt5_sym)
    if not buf:
        return {"buy_vol": 0, "sell_vol": 0, "buy_pct": 50.0, "sell_pct": 50.0, "total": 0}

    cutoff   = time.time() - window_sec
    recent   = [e for e in buf if e["ts"] >= cutoff]
    buy_vol  = sum(e["vol"] for e in recent if e["side"] == "BUY")
    sell_vol = sum(e["vol"] for e in recent if e["side"] == "SELL")
    total    = buy_vol + sell_vol

    if total == 0:
        return {"buy_vol": 0, "sell_vol": 0, "buy_pct": 50.0, "sell_pct": 50.0, "total": len(recent)}

    return {
        "buy_vol":  round(buy_vol,  1),
        "sell_vol": round(sell_vol, 1),
        "buy_pct":  round(buy_vol  / total * 100, 1),
        "sell_pct": round(sell_vol / total * 100, 1),
        "total":    len(recent),
    }


def _build_tape(mt5_sym: str, tape_size: int) -> list:
    buf = _tick_buf.get(mt5_sym)
    if not buf:
        return []
    entries = list(buf)[-tape_size:]
    return [
        {
            "time":  int(e["ts"]),
            "price": round(e["last"], 4),
            "vol":   round(e["vol"],  1),
            "side":  e["side"],
        }
        for e in reversed(entries)
    ]



def _calc_volatility(mt5_sym: str, window_sec: int = 20) -> dict:
    """Calcula range de preço (em ticks) dos últimos window_sec segundos.
    Retorna range_ticks, range_60s e ok=True se mercado tem movimento suficiente.
    range_60s alimenta o filtro LOW_VOLATILITY (P2 Scalper V2)."""
    buf    = _tick_buf.get(mt5_sym)
    ts_val = TICK_SIZE_OVERRIDE.get(mt5_sym, 0.01)
    cfg_v  = _sym_cfg(mt5_sym)

    if not buf:
        return {
            "range_ticks": 0, "range_60s": 0,
            "ok": False, "vol60_ok": False, "expansion": False,
            "reason": "sem dados",
        }

    now    = time.time()
    cutoff = now - window_sec
    recent = [e["last"] for e in buf if e["ts"] >= cutoff and e["last"] > 0]

    if len(recent) < 3:
        return {
            "range_ticks": 0, "range_60s": 0,
            "ok": False, "vol60_ok": False, "expansion": False,
            "reason": "poucos ticks",
        }

    price_range = max(recent) - min(recent)
    range_ticks = round(price_range / ts_val, 1)

    # ── Range 60s (P2) ─────────────────────────────────────────────────────
    cutoff60  = now - 60
    recent60  = [e["last"] for e in buf if e["ts"] >= cutoff60 and e["last"] > 0]
    if len(recent60) >= 2:
        range_60s = round((max(recent60) - min(recent60)) / ts_val, 1)
    else:
        range_60s = range_ticks  # fallback para janela curta

    min_r60     = cfg_v.get("min_range_60s", 4)
    exp_r60     = cfg_v.get("expansion_range_60s", 10)
    vol60_ok    = range_60s >= min_r60
    expansion   = range_60s >= exp_r60

    return {
        "range_ticks": range_ticks,
        "range_60s":   range_60s,
        "ok":          range_ticks >= cfg_v["volatility_min_ticks"],
        "vol60_ok":    vol60_ok,
        "expansion":   expansion,
        "min_range_60s":       min_r60,
        "expansion_range_60s": exp_r60,
        "max":  round(max(recent), 4),
        "min":  round(min(recent), 4),
    }



def _calc_tape_velocity(mt5_sym: str) -> dict:
    """Conta negócios recentes e analisa direção para detectar fluxo institucional.
    Retorna trades nos últimos 3s/10s, volume, flag 'fast' e tendência do tape."""
    buf = _tick_buf.get(mt5_sym)
    if not buf:
        return {"trades_3s": 0, "trades_10s": 0, "vol_3s": 0.0,
                "fast": False, "tape_trend": "NEUTRO", "last5_buy_pct": 50.0}

    now        = time.time()
    window_3   = [e for e in buf if e["ts"] >= now - 3]
    window_10  = [e for e in buf if e["ts"] >= now - 10]

    trades_3s  = len(window_3)
    trades_10s = len(window_10)
    vol_3s     = round(sum(e["vol"] for e in window_3), 1)

    # Direção dos últimos 5 negócios (tape trend)
    last5      = list(buf)[-5:]
    buy5       = sum(1 for e in last5 if e["side"] == "BUY")
    sell5      = len(last5) - buy5
    last5_buy_pct = round(buy5 / max(len(last5), 1) * 100, 1)

    if   buy5 >= 4:  tape_trend = "BUY"
    elif sell5 >= 4: tape_trend = "SELL"
    else:            tape_trend = "NEUTRO"

    # Aceleração: comparar volume 3s com média dos 10s anteriores
    vol_10  = sum(e["vol"] for e in window_10)
    avg_10s = vol_10 / 10.0  # volume médio por segundo nos últimos 10s
    avg_3s  = vol_3s / 3.0   # volume médio por segundo nos últimos 3s
    accelerating = avg_3s > avg_10s * 1.5  # 50% mais rápido que a média

    return {
        "trades_3s":    trades_3s,
        "trades_10s":   trades_10s,
        "vol_3s":       vol_3s,
        "fast":         trades_3s >= 5,
        "tape_trend":   tape_trend,
        "last5_buy_pct": last5_buy_pct,
        "accelerating": accelerating,
    }


# ── API pública ────────────────────────────────────────────────────────────
def get_scalper_data(symbol: str, aggr_seconds: int = 30,
                     book_depth: int = 10, tape_size: int = 25) -> dict:
    mt5_sym = _resolve_symbol(symbol)

    # ── MODO SIMULAÇÃO ──────────────────────────────────────────────────────
    if _sim_mode:
        ts_now = time.time()
        price  = _sim_next_price(mt5_sym)
        ts_val = TICK_SIZE_OVERRIDE.get(mt5_sym, 0.01)
        _sim_fill_buf(mt5_sym, price, ts_now)

        tick_data = {
            "last":      round(price, 4),
            "bid":       round(price - ts_val, 4),
            "ask":       round(price + ts_val, 4),
            "spread":    round(2 * ts_val, 6),
            "tick_size": ts_val,
            "time":      int(ts_now),
        }
        sim_book  = _sim_build_book(mt5_sym, price, book_depth)
        aggr5_sim = _calc_aggr(mt5_sym, 5)
        aggr30_sim= _calc_aggr(mt5_sym, aggr_seconds)
        vel_sim   = _calc_tape_velocity(mt5_sym)
        book_imb_sim = _calc_book_imbalance(sim_book)
        flow_sim  = _detect_absorption_exhaustion(mt5_sym)
        time_w    = _get_time_weight()
        vwap_sim  = {"vwap": None, "atr_1m": None, "context": "NEUTRO", "price": round(price, 2)}
        cum_sim   = _get_cumulative_delta(mt5_sym)
        score_buy  = _calc_multi_score(mt5_sym, "COMPRA",  aggr5_sim, aggr30_sim, vel_sim, book_imb_sim, vwap_sim, flow_sim, time_w)
        score_sell = _calc_multi_score(mt5_sym, "VENDA",   aggr5_sim, aggr30_sim, vel_sim, book_imb_sim, vwap_sim, flow_sim, time_w)
        return {
            "ok":      True,
            "symbol":  mt5_sym,
            "b3_open": True,
            "sim":     True,
            "tick":    tick_data,
            "book":    sim_book,
            "aggr":    aggr30_sim,
            "tape":       _build_tape(mt5_sym, tape_size),
            "volatility": _calc_volatility(mt5_sym),
            "context": {
                "velocity": vel_sim,
                "aggr_5s":  aggr5_sim,
                "aggr_10s": _calc_aggr(mt5_sym, 10),
            },
            "macro": {
                "vwap":      vwap_sim,
                "cum_delta": cum_sim,
                "time":      time_w,
                "flow":      flow_sim,
                "book_imb":  book_imb_sim,
                "score_buy":  score_buy,
                "score_sell": score_sell,
            },
        }

    # ── MODO REAL (MT5) ────────────────────────────────────────────────────
    try:
        import MetaTrader5 as mt5
        if not _mt5_init():
            return {"ok": False, "error": f"MT5 não inicializado: {mt5.last_error()}"}

        tick = mt5.symbol_info_tick(mt5_sym)
        info = mt5.symbol_info(mt5_sym)
        if tick is None:
            return {"ok": False, "error": f"Sem tick para '{mt5_sym}'. Símbolo no Market Watch?"}

        tick_size = TICK_SIZE_OVERRIDE.get(mt5_sym) or (
            float(info.trade_tick_size) if info and info.trade_tick_size else 0.01
        )
        tick_data = {
            "last":      round(tick.last or ((tick.bid + tick.ask) / 2), 2),
            "bid":       round(tick.bid,  2),
            "ask":       round(tick.ask,  2),
            "spread":    round(tick.ask - tick.bid, 4),
            "tick_size": tick_size,
            "time":      tick.time,
        }

        _update_tick_buffer(mt5_sym, tick)

        book_data = {"asks": [], "bids": [], "spread": tick_data["spread"]}
        try:
            mt5.market_book_add(mt5_sym)
            book_raw = mt5.market_book_get(mt5_sym)
            if book_raw:
                asks    = [r for r in book_raw if r.type == mt5.BOOK_TYPE_SELL][:book_depth]
                bids    = [r for r in book_raw if r.type == mt5.BOOK_TYPE_BUY][:book_depth]
                max_vol = max((r.volume for r in list(asks) + list(bids)), default=1) or 1
                book_data["asks"] = [
                    {"price": round(r.price, 2), "vol": int(r.volume),
                     "bar_pct": round(r.volume / max_vol * 100, 1)}
                    for r in sorted(asks, key=lambda x: x.price, reverse=True)
                ]
                book_data["bids"] = [
                    {"price": round(r.price, 2), "vol": int(r.volume),
                     "bar_pct": round(r.volume / max_vol * 100, 1)}
                    for r in sorted(bids, key=lambda x: x.price, reverse=True)
                ]
        except Exception:
            pass

        aggr5_r  = _calc_aggr(mt5_sym, 5)
        aggr30_r = _calc_aggr(mt5_sym, aggr_seconds)
        vel_r    = _calc_tape_velocity(mt5_sym)
        book_imb_r = _calc_book_imbalance(book_data)
        flow_r   = _detect_absorption_exhaustion(mt5_sym)
        time_w_r = _get_time_weight()
        vwap_r   = _calc_vwap_atr(mt5_sym)
        cum_r    = _get_cumulative_delta(mt5_sym)
        score_buy_r  = _calc_multi_score(mt5_sym, "COMPRA", aggr5_r, aggr30_r, vel_r, book_imb_r, vwap_r, flow_r, time_w_r)
        score_sell_r = _calc_multi_score(mt5_sym, "VENDA",  aggr5_r, aggr30_r, vel_r, book_imb_r, vwap_r, flow_r, time_w_r)
        return {
            "ok":      True,
            "symbol":  mt5_sym,
            "b3_open": _is_market_open(mt5_sym),
            "sim":     False,
            "tick":    tick_data,
            "book":    book_data,
            "aggr":    aggr30_r,
            "tape":       _build_tape(mt5_sym, tape_size),
            "volatility": _calc_volatility(mt5_sym),
            "context": {
                "velocity": vel_r,
                "aggr_5s":  aggr5_r,
                "aggr_10s": _calc_aggr(mt5_sym, 10),
            },
            "macro": {
                "vwap":      vwap_r,
                "cum_delta": cum_r,
                "time":      time_w_r,
                "flow":      flow_r,
                "book_imb":  book_imb_r,
                "score_buy":  score_buy_r,
                "score_sell": score_sell_r,
            },
        }

    except Exception as exc:
        logger.error("get_scalper_data error: %s", exc)
        return {"ok": False, "error": str(exc)}


def get_scalper_position(symbol: str) -> tuple:
    mt5_sym = _resolve_symbol(symbol)

    # ── MODO SIMULAÇÃO ──────────────────────────────────────────────────────
    if _sim_mode:
        pos = _sim_position.get(mt5_sym)
        if not pos:
            return None, None

        # Preço atual simulado
        cur_price = _sim_prices.get(mt5_sym, pos["price_open"])
        ts_val    = TICK_SIZE_OVERRIDE.get(mt5_sym, 0.01)

        # Calcular P&L (em pontos × valor_por_tick — simplificado para testes)
        if pos["type"] == "BUY":
            tick_val = TICK_VALUE_BRL.get(mt5_sym, 1.0)
            profit   = round((cur_price - pos["price_open"]) / ts_val * tick_val * pos["volume"], 2)
        else:
            tick_val = TICK_VALUE_BRL.get(mt5_sym, 1.0)
            profit   = round((pos["price_open"] - cur_price) / ts_val * tick_val * pos["volume"], 2)

        pos["price_cur"] = round(cur_price, 4)
        pos["profit"]    = profit

        # Verificar se atingiu TP ou SL
        hit_tp = (pos["type"] == "BUY"  and cur_price >= pos["tp"]) or                  (pos["type"] == "SELL" and cur_price <= pos["tp"])
        hit_sl = (pos["type"] == "BUY"  and cur_price <= pos["sl"]) or                  (pos["type"] == "SELL" and cur_price >= pos["sl"])

        if hit_tp or hit_sl:
            final_profit = profit
            _sim_closed_profits[pos["ticket"]] = final_profit
            _sim_position[mt5_sym] = None
            logger.info("SIM: posição fechada por %s | profit=%.2f", "TP" if hit_tp else "SL", final_profit)
            return None, None

        return pos, None

    # ── MODO REAL ──────────────────────────────────────────────────────────
    try:
        import MetaTrader5 as mt5
        if not _mt5_init():
            return None, "MT5 não inicializado"

        positions   = mt5.positions_get(symbol=mt5_sym) or []
        scalper_pos = [p for p in positions if p.magic == SCALPER_MAGIC] or list(positions)

        if not scalper_pos:
            return None, None

        p = scalper_pos[0]
        # Preservar open_time entre polls (armazenado em _pos_open_times)
        if p.ticket not in _pos_open_times:
            _pos_open_times[p.ticket] = time.time()
        return {
            "ticket":     p.ticket,
            "type":       "BUY" if p.type == 0 else "SELL",
            "volume":     p.volume,
            "price_open": round(p.price_open,    2),
            "price_cur":  round(p.price_current, 2),
            "profit":     round(p.profit,        2),
            "tp":         round(p.tp, 2) if p.tp else None,
            "sl":         round(p.sl, 2) if p.sl else None,
            "magic":      p.magic,
            "symbol":     p.symbol,
            "comment":    p.comment,
            "open_time":  _pos_open_times.get(p.ticket, time.time()),
        }, None

    except Exception as exc:
        logger.error("get_scalper_position error: %s", exc)
        return None, str(exc)


def execute_scalper_trade(symbol: str, acao: str, volume: float,
                          tp_ticks: int = 5, sl_ticks: int = 2,
                          use_atr_sizing: bool = True) -> tuple:
    """Executa trade scalper com TP/SL dinâmicos baseados no ATR atual.
    use_atr_sizing=True: SL = max(sl_ticks, ATR*0.55); TP = max(tp_ticks, ATR*1.15)
    atr_1m é em TICKS (já dividido por tick_size). Fatores calibrados para
    ATR típico de 8-13 ticks no WDON26 — SL sobrevive ao ruído, TP alcança o move real.
    """
    global _sim_ticket_counter
    mt5_sym = _resolve_symbol(symbol)

    # ── ATR-based dynamic sizing ──────────────────────────────────────────────
    if use_atr_sizing:
        try:
            vwap_d = _calc_vwap_atr(mt5_sym)
            atr_t  = vwap_d.get("atr_1m")          # ATR em ticks (1min)
            if atr_t and atr_t > 2:                 # só ajusta se ATR for significativo
                sl_orig   = sl_ticks
                tp_orig   = tp_ticks
                sl_atr    = max(sl_ticks, round(atr_t * 0.55))  # 55% do ATR — sobrevive ao ruído do WDON26
                tp_atr    = max(tp_ticks, round(atr_t * 1.15))  # 115% do ATR — captura o move real
                # Cap: no máximo 4× o valor configurado (evita SL absurdo em spike de volatilidade)
                sl_ticks  = min(sl_atr, sl_ticks * 4)
                tp_ticks  = min(tp_atr, tp_ticks * 4)
                logger.info(
                    "ATR sizing [%s]: ATR=%.1f tks → SL=%d (era %d) TP=%d (era %d)",
                    mt5_sym, atr_t, sl_ticks, sl_orig, tp_ticks, tp_orig
                )
        except Exception as _atr_exc:
            logger.debug("ATR sizing ignorado (%s): %s", mt5_sym, _atr_exc)

    # ── MODO SIMULAÇÃO ──────────────────────────────────────────────────────
    if _sim_mode:
        price    = _sim_prices.get(mt5_sym, _SIM_DEFAULT_PRICES.get(mt5_sym, 100.0))
        ts_val   = TICK_SIZE_OVERRIDE.get(mt5_sym, 0.01)
        acao_up  = acao.upper()

        if acao_up == "COMPRA":
            entry  = round(price, 6)                              # sim: usa mid price
            tp     = round(price + tp_ticks * ts_val, 6)
            sl     = round(price - sl_ticks * ts_val, 6)
            pos_type = "BUY"
        elif acao_up == "VENDA":
            entry  = round(price, 6)                              # sim: usa mid price
            tp     = round(price - tp_ticks * ts_val, 6)
            sl     = round(price + sl_ticks * ts_val, 6)
            pos_type = "SELL"
        else:
            return None, f"Ação inválida: '{acao}'"

        _sim_ticket_counter += 1
        ticket = _sim_ticket_counter

        _sim_position[mt5_sym] = {
            "ticket":     ticket,
            "type":       pos_type,
            "volume":     volume,
            "price_open": entry,
            "price_cur":  entry,
            "profit":     0.0,
            "tp":         tp,
            "sl":         sl,
            "magic":      SCALPER_MAGIC,
            "symbol":     mt5_sym,
            "comment":    "SIM",
            "open_time":  time.time(),
            "be_done":    False,     # break-even ja aplicado?
        }
        logger.info("SIM: abriu %s %s @ %.4f  TP=%.4f  SL=%.4f", pos_type, mt5_sym, entry, tp, sl)
        return {"order": ticket, "price": entry, "tp": tp, "sl": sl,
                "volume": volume, "acao": acao_up}, None

    # ── MODO REAL ──────────────────────────────────────────────────────────
    try:
        import MetaTrader5 as mt5
        if not _mt5_init():
            return None, "MT5 não inicializado"

        tick = mt5.symbol_info_tick(mt5_sym)
        info = mt5.symbol_info(mt5_sym)
        if tick is None or info is None:
            return None, f"Sem dados para '{mt5_sym}'"

        tick_size = TICK_SIZE_OVERRIDE.get(mt5_sym) or (
            float(info.trade_tick_size) if info and info.trade_tick_size else 0.01
        )
        acao_up = acao.upper()
        if acao_up == "COMPRA":
            order_type = mt5.ORDER_TYPE_BUY
            price      = tick.ask
            tp_price   = round(price + tp_ticks * tick_size, 2)
            sl_price   = round(price - sl_ticks * tick_size, 2)
        elif acao_up == "VENDA":
            order_type = mt5.ORDER_TYPE_SELL
            price      = tick.bid
            tp_price   = round(price - tp_ticks * tick_size, 2)
            sl_price   = round(price + sl_ticks * tick_size, 2)
        else:
            return None, f"Ação inválida: '{acao}'"

        filling = mt5.ORDER_FILLING_IOC
        if info.filling_mode & mt5.ORDER_FILLING_FOK:
            filling = mt5.ORDER_FILLING_FOK

        req = {
            "action":       mt5.TRADE_ACTION_DEAL,
            "symbol":       mt5_sym,
            "volume":       float(volume),
            "type":         order_type,
            "price":        price,
            "tp":           tp_price,
            "sl":           sl_price,
            "deviation":    10,
            "magic":        SCALPER_MAGIC,
            "comment":      SCALPER_COMMENT,
            "type_time":    mt5.ORDER_TIME_GTC,
            "type_filling": filling,
        }
        r = mt5.order_send(req)

        if r and r.retcode == mt5.TRADE_RETCODE_DONE:
            return {
                "order":  r.order,
                "price":  round(r.price, 2),
                "tp":     tp_price,
                "sl":     sl_price,
                "volume": volume,
                "acao":   acao_up,
            }, None

        code    = r.retcode if r else "?"
        comment = r.comment if r else "sem resposta"
        return None, f"MT5 retcode {code}: {comment}"

    except Exception as exc:
        logger.error("execute_scalper_trade error: %s", exc)
        return None, str(exc)


def close_scalper_position(symbol: str) -> tuple:
    mt5_sym = _resolve_symbol(symbol)

    # ── MODO SIMULAÇÃO ──────────────────────────────────────────────────────
    if _sim_mode:
        pos = _sim_position.get(mt5_sym)
        if not pos:
            return None, "Nenhuma posição simulada aberta."

        cur_price = _sim_prices.get(mt5_sym, pos["price_open"])
        ts_val    = TICK_SIZE_OVERRIDE.get(mt5_sym, 0.01)

        if pos["type"] == "BUY":
            tick_val = TICK_VALUE_BRL.get(mt5_sym, 1.0)
            profit   = round((cur_price - pos["price_open"]) / ts_val * tick_val * pos["volume"], 2)
        else:
            tick_val = TICK_VALUE_BRL.get(mt5_sym, 1.0)
            profit   = round((pos["price_open"] - cur_price) / ts_val * tick_val * pos["volume"], 2)

        ticket = pos["ticket"]
        _sim_closed_profits[ticket] = profit
        _sim_position[mt5_sym] = None
        logger.info("SIM: fechou manualmente %s | profit=%.2f", mt5_sym, profit)
        return {"order": ticket, "price": round(cur_price, 4), "profit": profit}, None

    # ── MODO REAL ──────────────────────────────────────────────────────────
    try:
        import MetaTrader5 as mt5
        if not _mt5_init():
            return None, "MT5 não inicializado"

        pos, err = get_scalper_position(symbol)
        if not pos:
            return None, err or "Nenhuma posição aberta."

        tick = mt5.symbol_info_tick(mt5_sym)
        if tick is None:
            return None, f"Sem tick para '{mt5_sym}'"

        info = mt5.symbol_info(mt5_sym)
        filling = mt5.ORDER_FILLING_IOC
        if info and (info.filling_mode & mt5.ORDER_FILLING_FOK):
            filling = mt5.ORDER_FILLING_FOK

        close_type  = mt5.ORDER_TYPE_SELL if pos["type"] == "BUY" else mt5.ORDER_TYPE_BUY
        close_price = tick.bid             if pos["type"] == "BUY" else tick.ask

        req = {
            "action":       mt5.TRADE_ACTION_DEAL,
            "symbol":       mt5_sym,
            "volume":       float(pos["volume"]),
            "type":         close_type,
            "price":        close_price,
            "position":     pos["ticket"],
            "deviation":    10,
            "magic":        SCALPER_MAGIC,
            "comment":      "ScalperClose",
            "type_time":    mt5.ORDER_TIME_GTC,
            "type_filling": filling,
        }
        r = mt5.order_send(req)

        if r and r.retcode == mt5.TRADE_RETCODE_DONE:
            return {"order": r.order, "price": round(r.price, 2),
                    "profit": pos["profit"]}, None

        code    = r.retcode if r else '?'
        comment = r.comment if r else 'sem resposta'
        return None, f'MT5 retcode {code}: {comment}'

    except Exception as exc:
        logger.error('close_scalper_position error: %s', exc)
        return None, str(exc)


def move_sl_to_breakeven(symbol: str) -> tuple:
    """Move o SL para o preco de entrada (break-even) quando o lucro justifica."""
    mt5_sym = _resolve_symbol(symbol)

    if _sim_mode:
        pos = _sim_position.get(mt5_sym)
        if not pos:
            return None, 'Sem posicao simulada'
        old_sl = pos.get('sl')
        pos['sl'] = pos['price_open']
        return {'old_sl': old_sl, 'new_sl': pos['sl']}, None

    try:
        import MetaTrader5 as mt5
        if not _mt5_init():
            return None, f'MT5 nao inicializado: {mt5.last_error()}'

        positions = mt5.positions_get(symbol=mt5_sym)
        if not positions:
            return None, 'Sem posicao aberta'
        pos = positions[0]

        new_sl = pos.price_open
        new_tp = pos.tp

        req = {
            'action':   mt5.TRADE_ACTION_SLTP,
            'symbol':   mt5_sym,
            'position': pos.ticket,
            'sl':       new_sl,
            'tp':       new_tp,
            'magic':    SCALPER_MAGIC,
            'comment':  'BE',
        }
        r = mt5.order_send(req)
        if r and r.retcode == mt5.TRADE_RETCODE_DONE:
            return {'old_sl': round(pos.sl, 2), 'new_sl': round(new_sl, 2)}, None
        code    = r.retcode if r else '?'
        comment = r.comment if r else 'sem resposta'
        return None, f'SLTP retcode {code}: {comment}'

    except Exception as exc:
        logger.error('move_sl_to_breakeven error: %s', exc)
        return None, str(exc)
