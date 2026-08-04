"""
flow_trigger.py — Camada 3 do Motor v7: GATILHO por order flow (WIN/WDO).

Papel: o motor v7 (camada 2) ARMA um sinal num nível; este módulo decide o
MOMENTO de disparar, lendo o fluxo real: agressão (tape), delta de curto
prazo e absorção. Diferente do orderflow_mt5.py (veto), aqui o fluxo é
CONDIÇÃO DE ENTRADA.

Duas interfaces com a MESMA regra:
  confirm_live(mt5_symbol, direction)      — tempo real via copy_ticks_from
  confirm_from_ticks(ticks, direction, ts) — histórico (backtest) sobre um
     array de ticks do MT5 (copy_ticks_range) — o gatilho é BACKTESTÁVEL.

Regra de disparo (calibrável):
  1. Delta 45s a favor: >= 58% da agressão na direção
  2. Delta 10s (imediato) não contra: >= 45%
  3. Book (só live, opcional): imbalance não fortemente contra (-25%)
Fail-safe: sem dados de tick → NÃO dispara (gatilho exige evidência;
diferente do veto antigo, que na dúvida liberava).

Módulo NOVO — nada do fluxo em produção foi alterado.
"""
import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

DELTA_LONG_WIN_SEC  = 45
DELTA_SHORT_WIN_SEC = 10
DELTA_LONG_MIN_PCT  = 58.0   # agressão na direção na janela longa
DELTA_SHORT_MIN_PCT = 45.0   # janela curta não pode estar forte contra
BOOK_BLOCK_PCT      = 25.0   # book fortemente contra bloqueia (live)


def _classify_side(last, bid, ask, prev_last):
    if ask and last >= ask:  return "BUY"
    if bid and last <= bid:  return "SELL"
    if prev_last is not None:
        return "BUY" if last >= prev_last else "SELL"
    return None


def flow_stats(ticks, now_epoch: float, window_sec: int) -> dict:
    """
    Estatística de agressão numa janela. `ticks` = iterável de dicts/rows com
    campos time(sec)|time_msc, last, bid, ask, volume|volume_real.
    """
    buy = sell = 0.0
    prev_last = None
    cutoff = now_epoch - window_sec
    for t in ticks:
        get = t.get if isinstance(t, dict) else (lambda k, _t=t: _t[k] if k in getattr(_t, "dtype", {}).names else None) if hasattr(t, "dtype") else t.get
        try:
            tt = get("time_msc")
            tt = (tt / 1000.0) if tt else float(get("time") or 0)
        except Exception:
            tt = float(get("time") or 0)
        if tt < cutoff:
            continue
        last = float(get("last") or 0)
        if last <= 0:
            continue
        bid  = float(get("bid") or 0)
        ask  = float(get("ask") or 0)
        vol  = float(get("volume_real") or get("volume") or 1) or 1
        side = _classify_side(last, bid, ask, prev_last)
        if side == "BUY":  buy += vol
        elif side == "SELL": sell += vol
        prev_last = last
    total = buy + sell
    return {
        "buy_vol": buy, "sell_vol": sell, "total": total,
        "buy_pct": round(buy / total * 100, 1) if total else 50.0,
        "n": int(total),
    }


def _decide(long_stats: dict, short_stats: dict, direction: str,
            book_imb_pct: "float | None" = None) -> dict:
    buy = direction == "COMPRA"
    if long_stats["total"] <= 0:
        return {"fire": False, "reason": "sem ticks na janela — gatilho exige evidência",
                "long_pct": None, "short_pct": None}

    long_pct  = long_stats["buy_pct"]  if buy else 100 - long_stats["buy_pct"]
    short_pct = short_stats["buy_pct"] if buy else 100 - short_stats["buy_pct"]

    if long_pct < DELTA_LONG_MIN_PCT:
        return {"fire": False, "long_pct": long_pct, "short_pct": short_pct,
                "reason": f"delta {DELTA_LONG_WIN_SEC}s {long_pct:.0f}% < {DELTA_LONG_MIN_PCT:.0f}%"}
    if short_pct < DELTA_SHORT_MIN_PCT:
        return {"fire": False, "long_pct": long_pct, "short_pct": short_pct,
                "reason": f"delta {DELTA_SHORT_WIN_SEC}s {short_pct:.0f}% forte contra"}
    if book_imb_pct is not None:
        against = -book_imb_pct if buy else book_imb_pct
        if against >= BOOK_BLOCK_PCT:
            return {"fire": False, "long_pct": long_pct, "short_pct": short_pct,
                    "reason": f"book {book_imb_pct:+.0f}% fortemente contra"}
    return {"fire": True, "long_pct": long_pct, "short_pct": short_pct,
            "reason": f"fluxo confirma {direction}: {DELTA_LONG_WIN_SEC}s {long_pct:.0f}% / "
                      f"{DELTA_SHORT_WIN_SEC}s {short_pct:.0f}%"}


def confirm_from_ticks(ticks, direction: str, now_epoch: float) -> dict:
    """Versão histórica (backtest): decide com ticks até now_epoch."""
    ls = flow_stats(ticks, now_epoch, DELTA_LONG_WIN_SEC)
    ss = flow_stats(ticks, now_epoch, DELTA_SHORT_WIN_SEC)
    return _decide(ls, ss, direction)


def confirm_live(mt5_symbol: str, direction: str, use_book: bool = True) -> dict:
    """Versão tempo real: lê ticks recentes (e book) do MT5 e decide."""
    neutral = {"fire": False, "reason": "MT5 indisponível", "long_pct": None, "short_pct": None}
    try:
        import MetaTrader5 as mt5
        if mt5.terminal_info() is None:
            from services.trade_executor import _mt5_init_kwargs
            if not mt5.initialize(**_mt5_init_kwargs()):
                return neutral
        if not mt5.symbol_select(mt5_symbol, True):
            return neutral
        now = datetime.now(timezone.utc)
        raw = mt5.copy_ticks_from(mt5_symbol, now - timedelta(seconds=DELTA_LONG_WIN_SEC + 5),
                                  4000, mt5.COPY_TICKS_ALL)
        if raw is None or len(raw) == 0:
            return {**neutral, "reason": "sem ticks recentes"}
        names = raw.dtype.names
        ticks = [{k: r[k] for k in names} for r in raw]
        book_imb = None
        if use_book:
            try:
                mt5.market_book_add(mt5_symbol)
                book = mt5.market_book_get(mt5_symbol)
                if book:
                    bidv = sum(r.volume for r in book if r.type == mt5.BOOK_TYPE_BUY)
                    askv = sum(r.volume for r in book if r.type == mt5.BOOK_TYPE_SELL)
                    tot  = bidv + askv
                    book_imb = round((bidv - askv) / tot * 100, 1) if tot else None
            except Exception:
                pass
            finally:
                try: mt5.market_book_release(mt5_symbol)
                except Exception: pass
        ls = flow_stats(ticks, now.timestamp(), DELTA_LONG_WIN_SEC)
        ss = flow_stats(ticks, now.timestamp(), DELTA_SHORT_WIN_SEC)
        out = _decide(ls, ss, direction, book_imb)
        out["book_imb_pct"] = book_imb
        return out
    except Exception as exc:
        logger.warning("flow_trigger live erro (%s): %s", mt5_symbol, exc)
        return neutral
