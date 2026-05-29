"""
outcome_checker.py
Verifica via MT5 se os alvos (TP1/TP2/TP3) e stop de um sinal foram atingidos.

Lógica candle a candle (1m):
  - Stop tem PRIORIDADE: se Low <= stop (COMPRA) ou High >= stop (VENDA) → para tudo.
  - TPs são acumulativos: marca cada um independentemente enquanto stop não acionar.
  - Lookback padrão: 12 horas após o sinal (uma sessão de mercado).
"""
import logging
from datetime import datetime, timezone, timedelta

import pandas as pd

logger = logging.getLogger(__name__)


def _infer_mt5_symbol(ativo: str) -> "str | None":
    """Mapeia o campo 'ativo' do trader JSON para um símbolo MT5."""
    import os
    if not ativo:
        return None
    a = str(ativo).upper().strip()
    # Futuros B3
    if "WIN" in a:
        return os.getenv("WIN_MT5_SYMBOL", "WINM26")
    if "WDO" in a:
        return os.getenv("WDO_MT5_SYMBOL", "WDOM26")
    # Ações e outros — usa direto
    for sym in ("PETR4", "RADL3", "VALE3", "ITUB4", "BBDC4", "ABEV3", "WEGE3", "BBAS3",
                "EURUSD", "GBPUSD", "XAUUSD", "BTCUSD"):
        if sym in a:
            return sym
    # Tenta usar o valor diretamente se parecer um ticker limpo
    clean = a.split()[0]
    if clean.isalnum() and 3 <= len(clean) <= 10:
        return clean
    return None


def check_outcome(
    ativo: str,
    acao: str,
    created_at_iso: str,
    entrada: "float | None",
    stop: "float | None",
    tp1: "float | None",
    tp2: "float | None",
    tp3: "float | None",
    lookback_hours: int = 12,
) -> dict:
    """
    Retorna:
      tp1_hit, tp2_hit, tp3_hit, stop_hit  (bool)
      candles_checked                        (int)
      error                                  (str | None)
    """
    result = {
        "tp1_hit": False, "tp2_hit": False, "tp3_hit": False,
        "stop_hit": False, "candles_checked": 0, "error": None,
    }

    try:
        import MetaTrader5 as mt5
    except ImportError:
        result["error"] = "MetaTrader5 não instalado."
        return result

    if not acao or acao not in ("COMPRA", "VENDA"):
        result["error"] = "Ação inválida (NÃO OPERAR ou ausente) — nada a verificar."
        return result

    if entrada is None and stop is None and tp1 is None:
        result["error"] = "Nenhum nível de preço identificado no sinal."
        return result

    mt5_symbol = _infer_mt5_symbol(ativo)
    if not mt5_symbol:
        result["error"] = f"Símbolo MT5 não mapeado para '{ativo}'."
        return result

    try:
        from_dt = datetime.fromisoformat(created_at_iso.replace("Z", "+00:00"))
        to_dt = min(from_dt + timedelta(hours=lookback_hours), datetime.now(timezone.utc))

        if not mt5.initialize():
            err = mt5.last_error()
            result["error"] = f"MT5 não inicializado: {err}"
            return result

        # MT5 copy_rates_range usa datetime sem fuso (UTC implícito)
        from_naive = from_dt.replace(tzinfo=None)
        to_naive   = to_dt.replace(tzinfo=None)

        rates = mt5.copy_rates_range(mt5_symbol, mt5.TIMEFRAME_M1, from_naive, to_naive)
        mt5.shutdown()

        if rates is None or len(rates) == 0:
            result["error"] = (
                f"Sem candles MT5 para '{mt5_symbol}' de {from_naive} a {to_naive}. "
                "Terminal ativo e símbolo no Market Watch?"
            )
            return result

        df = pd.DataFrame(rates)
        result["candles_checked"] = len(df)
        is_compra = (acao == "COMPRA")

        for _, row in df.iterrows():
            high = float(row["high"])
            low  = float(row["low"])

            # Stop tem prioridade — verifica primeiro
            if stop is not None:
                stop_triggered = (low <= stop) if is_compra else (high >= stop)
                if stop_triggered:
                    result["stop_hit"] = True
                    break  # stop acionado: TPs deste ponto em diante não contam

            # TPs acumulativos
            if tp1 is not None and not result["tp1_hit"]:
                result["tp1_hit"] = (high >= tp1) if is_compra else (low <= tp1)
            if tp2 is not None and not result["tp2_hit"]:
                result["tp2_hit"] = (high >= tp2) if is_compra else (low <= tp2)
            if tp3 is not None and not result["tp3_hit"]:
                result["tp3_hit"] = (high >= tp3) if is_compra else (low <= tp3)

        return result

    except Exception as exc:
        logger.warning("Erro ao verificar outcome: %s", exc)
        try:
            import MetaTrader5 as mt5
            mt5.shutdown()
        except Exception:
            pass
        result["error"] = str(exc)
        return result
