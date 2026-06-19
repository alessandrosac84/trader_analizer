"""
partial_close_manager.py — Estratégia de fechamento parcial (3 contratos).

Módulo ISOLADO — não altera o comportamento padrão de 1 contrato.
Quando desabilitado (_enabled=False), o sistema opera exatamente como antes.

ESTRATÉGIA:
  Entrada: 3 contratos
  TPs calculados INTERNAMENTE pelo R/R sobre o stop loss — nunca do sinal:
    TP1 = entrada ± TP1_RR × |entrada - sl|   → safety net no MT5 + monitorado
    TP2 = entrada ± TP2_RR × |entrada - sl|   → alvo do contrato restante

  TP1 atingido (monitor 2s detecta) → fecha 2 contratos
                                     + SL → entrada (breakeven)
                                     + MT5 TP → TP2 no contrato restante
  TP2 atingido → MT5 fecha o último contrato automaticamente
  Fallback: se MT5 fechar todos em TP1 → lucro assegurado em TP1

POR QUÊ IGNORAR O TP DO SINAL?
  O sinal TradingView pode gerar TPs agressivos (ex: TP1=1800 pts quando o
  SL é de 300 pts → 6:1 R/R irreal). O correto é calcular os TPs a partir
  do risco real assumido no SL — isso garante alvos sempre alcançáveis e
  proporcionais ao risco de cada trade.

COMO ATIVAR:
  POST /api/autotrade/partial-config  {"enabled": true}
  GET  /api/autotrade/partial-config  → status atual
"""
import logging
from threading import Lock

logger = logging.getLogger(__name__)

_lock    = Lock()
_enabled = False      # desabilitado por padrão
_state: dict = {}     # tv_symbol → state dict

# ── Configuração da estratégia ─────────────────────────────────────────────────
ENTRY_VOLUME         = 3     # contratos na entrada
PARTIAL_CLOSE_VOLUME = 2     # contratos a fechar quando TP1 for atingido

# R/R para cálculo dos TPs — baseados na distância do stop loss
# TP1 = entrada ± TP1_RR × stop_distance   (ex: SL=300 pts → TP1=300 pts, 1:1 R/R)
# TP2 = entrada ± TP2_RR × stop_distance   (ex: SL=300 pts → TP2=450 pts, 1.5:1 R/R)
TP1_RR = 1.0   # R/R para o primeiro alvo parcial (1:1)
TP2_RR = 1.5   # R/R para o alvo final do contrato restante (1.5:1)


# ── API de controle ────────────────────────────────────────────────────────────

def is_enabled() -> bool:
    """Retorna True se o modo 3-contratos está ativo."""
    return _enabled


def set_enabled(flag: bool) -> None:
    global _enabled
    with _lock:
        _enabled = bool(flag)
    logger.info("Partial-close 3-contratos: %s", "ATIVADO ✅" if _enabled else "DESATIVADO")


# ── Cálculo dos TPs pelo R/R ──────────────────────────────────────────────────

def calc_tp_from_rr(
    entrada: float,
    sl: float,
    acao: str,
) -> "tuple[float, float]":
    """
    Calcula TP1 e TP2 com base no risco real do trade (distância entrada→SL).

    TP1 = entrada ± TP1_RR × stop_distance   (1:1 R/R padrão)
    TP2 = entrada ± TP2_RR × stop_distance   (1.5:1 R/R padrão)

    Exemplos com TP1_RR=1.0 e TP2_RR=1.5:
      SL=200 pts → TP1=200 pts, TP2=300 pts
      SL=350 pts → TP1=350 pts, TP2=525 pts
      SL=500 pts → TP1=500 pts, TP2=750 pts

    Retorna (tp1, tp2).
    """
    entrada_f   = float(entrada)
    sl_f        = float(sl)
    stop_dist   = abs(entrada_f - sl_f)

    if stop_dist == 0:
        logger.warning("calc_tp_from_rr: stop_distance=0 (SL=entrada?). TP1=TP2=entrada.")
        return entrada_f, entrada_f

    if acao == "COMPRA":
        tp1 = round(entrada_f + TP1_RR * stop_dist, 2)
        tp2 = round(entrada_f + TP2_RR * stop_dist, 2)
    else:
        tp1 = round(entrada_f - TP1_RR * stop_dist, 2)
        tp2 = round(entrada_f - TP2_RR * stop_dist, 2)

    logger.info(
        "R/R calc [%s]: entry=%.0f  sl=%.0f  stop_dist=%.0f  "
        "→ TP1=%.0f (%.1f:1)  TP2=%.0f (%.1f:1)",
        acao, entrada_f, sl_f, stop_dist,
        tp1, TP1_RR, tp2, TP2_RR,
    )
    return tp1, tp2


# ── Ciclo de vida do trade ─────────────────────────────────────────────────────

def register_trade(
    tv_symbol: str,
    acao: str,
    entry_price: float,
    tp1: float,
    tp2: float,
) -> None:
    """
    Registra novo trade em modo parcial.
    Chamado após execute_trade() bem-sucedido quando o modo está ativo.
    """
    with _lock:
        _state[tv_symbol] = {
            "phase":       "FULL",       # FULL → PARTIAL_DONE
            "acao":        acao,
            "entry_price": entry_price,
            "tp1":         tp1,
            "tp2":         tp2,
        }
    logger.info(
        "Partial-trade registrado: %s %s | entry=%.0f  tp1=%.0f  tp2=%.0f",
        acao, tv_symbol, entry_price, tp1, tp2,
    )


def get_state(tv_symbol: str) -> dict:
    """Retorna cópia do estado atual para um símbolo (vazio se não existe)."""
    return dict(_state.get(tv_symbol, {}))


def clear_state(tv_symbol: str) -> None:
    """Limpa o estado após fechamento total da posição."""
    with _lock:
        removed = _state.pop(tv_symbol, None)
    if removed:
        logger.info("Partial-state limpo para %s", tv_symbol)


def should_partial_close(tv_symbol: str, current_price: float) -> bool:
    """
    Retorna True quando TODAS as condições abaixo forem verdadeiras:
      1. Estado do símbolo existe e fase é FULL (ainda não foi fechado parcialmente)
      2. Preço atual atingiu ou ultrapassou o TP1
    """
    s = _state.get(tv_symbol)
    if not s or s.get("phase") != "FULL":
        return False
    tp1  = float(s["tp1"])
    acao = s.get("acao", "")
    if acao == "COMPRA":
        return current_price >= tp1
    elif acao == "VENDA":
        return current_price <= tp1
    return False


def mark_partial_done(tv_symbol: str) -> None:
    """Marca que os 2 contratos foram fechados em TP1. Restam 1 contrato até TP2."""
    with _lock:
        if tv_symbol in _state:
            _state[tv_symbol]["phase"] = "PARTIAL_DONE"
    logger.info(
        "Partial-close concluído para %s → fase PARTIAL_DONE (1 contrato restante até TP2)",
        tv_symbol,
    )
