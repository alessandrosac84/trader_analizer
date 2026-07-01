"""
orderflow_mt5.py — Confirmacao por ORDER FLOW (book/DOM + agressao + volume real)
para o Monitor MT5 (WIN/WDO).

Motivacao (v6.1): o Monitor gera sinais a partir de candles (15m/5m), mas o MT5
expoe dados de microestrutura que o Monitor ignorava — book de ofertas (DOM),
agressao (tape) e volume real. Este modulo faz uma leitura PONTUAL desses dados
no instante do sinal e diz se o fluxo CONFIRMA ou CONTRADIZ a direcao proposta.

Uso: chamado no auto-trade, DEPOIS do validador de IA e ANTES de executar.
Filosofia "menos trades, mais qualidade": so BLOQUEIA quando o fluxo contradiz
FORTEMENTE a direcao. Se o book/tape nao estiver disponivel, NAO bloqueia
(fail-safe) — nunca deixa o modulo derrubar um trade por falta de dado.

Este modulo e independente do scalper_service (que mantem buffers proprios);
aqui cada chamada faz uma leitura fresca via API do MT5.
"""
import logging

logger = logging.getLogger(__name__)

# Limiares de confirmacao (calibraveis). Positivo = comprador; negativo = vendedor.
_IMB_STRONG   = 25.0   # |imbalance%| do book acima disso = pressao clara
_AGGR_STRONG  = 62.0   # % de agressao (buy_pct/sell_pct) acima disso = fluxo claro
_CONTRA_BLOCK = True   # bloquear quando fluxo contradiz forte a direcao

# TV_SYMBOL -> MT5_SYMBOL (mesmo mapeamento do executor, importado dinamicamente)
def _mt5_symbol(tv_symbol: str) -> "str | None":
    try:
        from services.trade_executor import _mt5_symbol as _m
        return _m(tv_symbol)
    except Exception:
        return None


def _read_book_imbalance(mt5, mt5_sym: str, levels: int = 10) -> "dict | None":
    """Le o book (DOM) atual e calcula desequilibrio bid/ask."""
    try:
        mt5.market_book_add(mt5_sym)
        book = mt5.market_book_get(mt5_sym)
        if not book:
            return None
        bids = [r for r in book if r.type == mt5.BOOK_TYPE_BUY][:levels]
        asks = [r for r in book if r.type == mt5.BOOK_TYPE_SELL][:levels]
        bid_vol = sum(r.volume for r in bids)
        ask_vol = sum(r.volume for r in asks)
        total = bid_vol + ask_vol
        if total <= 0:
            return None
        imb = round((bid_vol - ask_vol) / total * 100, 1)  # + = mais compra
        return {"imbalance_pct": imb, "bid_vol": int(bid_vol), "ask_vol": int(ask_vol)}
    except Exception as exc:
        logger.debug("book read falhou (%s): %s", mt5_sym, exc)
        return None
    finally:
        try:
            mt5.market_book_release(mt5_sym)
        except Exception:
            pass


def _read_aggression(mt5, mt5_sym: str, seconds: int = 30) -> "dict | None":
    """Le os ticks recentes e classifica agressao (compra vs venda) pelo tape."""
    try:
        import time as _t
        from datetime import datetime, timezone, timedelta
        utc_from = datetime.now(timezone.utc) - timedelta(seconds=seconds)
        ticks = mt5.copy_ticks_from(mt5_sym, utc_from, 2000, mt5.COPY_TICKS_ALL)
        if ticks is None or len(ticks) == 0:
            return None
        buy_vol = sell_vol = 0.0
        prev_last = None
        for tk in ticks:
            last = float(tk["last"]) if tk["last"] else 0.0
            bid  = float(tk["bid"])  if tk["bid"]  else 0.0
            ask  = float(tk["ask"])  if tk["ask"]  else 0.0
            vol  = float(tk["volume_real"]) if ("volume_real" in ticks.dtype.names and tk["volume_real"]) else float(tk["volume"] or 1)
            if last <= 0:
                continue
            if ask > 0 and last >= ask:
                side = "BUY"
            elif bid > 0 and last <= bid:
                side = "SELL"
            elif prev_last is not None:
                side = "BUY" if last >= prev_last else "SELL"
            else:
                side = None
            if side == "BUY":
                buy_vol += vol
            elif side == "SELL":
                sell_vol += vol
            prev_last = last
        total = buy_vol + sell_vol
        if total <= 0:
            return None
        buy_pct = round(buy_vol / total * 100, 1)
        return {"buy_pct": buy_pct, "sell_pct": round(100 - buy_pct, 1),
                "buy_vol": int(buy_vol), "sell_vol": int(sell_vol), "n_ticks": len(ticks)}
    except Exception as exc:
        logger.debug("aggression read falhou (%s): %s", mt5_sym, exc)
        return None


def confirm_direction(tv_symbol: str, acao: str) -> dict:
    """
    Confirma (ou nao) a direcao {acao} pelo order flow atual do MT5.

    Retorna dict:
      { allow: bool, confirms: bool, contradicts: bool, reason: str,
        imbalance_pct, buy_pct, available: bool }

    - allow=False SOMENTE quando o fluxo contradiz FORTEMENTE a direcao.
    - Se dados indisponiveis -> available=False, allow=True (fail-safe).
    """
    neutral = {"allow": True, "confirms": False, "contradicts": False,
               "reason": "order flow indisponivel — sem veto (fail-safe)",
               "imbalance_pct": None, "buy_pct": None, "available": False}

    if acao not in ("COMPRA", "VENDA"):
        return {**neutral, "reason": "acao nao direcional"}

    mt5_sym = _mt5_symbol(tv_symbol)
    if not mt5_sym:
        return neutral

    try:
        import MetaTrader5 as mt5
    except Exception:
        return neutral

    try:
        if mt5.terminal_info() is None:
            from services.trade_executor import _mt5_init_kwargs
            if not mt5.initialize(**_mt5_init_kwargs()):
                return neutral
        if not mt5.symbol_select(mt5_sym, True):
            return neutral

        book = _read_book_imbalance(mt5, mt5_sym)
        aggr = _read_aggression(mt5, mt5_sym)

        if not book and not aggr:
            return neutral

        is_buy = acao == "COMPRA"
        imb = book["imbalance_pct"] if book else 0.0
        buy_pct = aggr["buy_pct"] if aggr else 50.0

        # Sinais a favor / contra
        book_for   = (imb >=  _IMB_STRONG) if is_buy else (imb <= -_IMB_STRONG)
        book_against = (imb <= -_IMB_STRONG) if is_buy else (imb >=  _IMB_STRONG)
        aggr_for   = (buy_pct >= _AGGR_STRONG) if is_buy else ((100 - buy_pct) >= _AGGR_STRONG)
        aggr_against = ((100 - buy_pct) >= _AGGR_STRONG) if is_buy else (buy_pct >= _AGGR_STRONG)

        confirms    = book_for or aggr_for
        # Contradicao FORTE = ambos os lados (book e tape) contra a direcao
        contradicts_strong = book_against and aggr_against

        if _CONTRA_BLOCK and contradicts_strong:
            return {"allow": False, "confirms": False, "contradicts": True,
                    "available": True, "imbalance_pct": imb, "buy_pct": buy_pct,
                    "reason": (f"Order flow contra {acao}: book {imb:+.0f}% e agressao "
                               f"{buy_pct:.0f}%C — fluxo forte na direcao oposta.")}

        reason = (f"Order flow {'confirma' if confirms else 'neutro para'} {acao} "
                  f"(book {imb:+.0f}%, agressao {buy_pct:.0f}%C).")
        return {"allow": True, "confirms": confirms, "contradicts": False,
                "available": True, "imbalance_pct": imb, "buy_pct": buy_pct, "reason": reason}

    except Exception as exc:
        logger.warning("confirm_direction erro (%s): %s", mt5_sym, exc)
        return neutral
