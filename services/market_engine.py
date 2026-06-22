"""
services/market_engine.py
--------------------------
Motor de Exposicao: gerencia risco agregado, drawdown e prioridade
entre WIN, WDO e Scalper.

Funcionalidades:
  - Exposicao atual (posicoes abertas por instrumento)
  - P&L do dia por instrumento
  - Drawdown diario / semanal
  - Priorizacao dinamica (WIN vs WDO vs Scalper)
  - Limites de operacao (stop de perda diario)

REGRA: apenas LE dados. Nunca modifica modulos existentes.
"""
import csv
import os
import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

_BRT         = timezone(timedelta(hours=-3))
_LOG_DIR     = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
_SCALPER_CSV = os.path.join(_LOG_DIR, "scalper_trades.csv")

# Limites configurados (podem ser ajustados pelo operador)
_DAILY_STOP_BRL    = -500.0   # Perda maxima no dia antes de parar
_WEEKLY_STOP_BRL   = -1500.0  # Perda maxima na semana
_MAX_CONCURRENT    = 3        # Max de operacoes simultaneas entre todos os instrumentos
_MIN_WIN_RATE_PRIORITY = 55.0  # Win rate minimo para prioridade maxima


def _conn():
    from services.db import _conn as _db
    return _db()


# ---------------------------------------------------------------------------
# P&L e performance por instrumento — WIN e WDO (via trade_opportunities)
# ---------------------------------------------------------------------------

def _period_since(period: str) -> str:
    """Converte nome de periodo em timestamp ISO para filtro SQL."""
    _BRT_now = datetime.now(_BRT)
    if period == "today":
        return _BRT_now.strftime("%Y-%m-%d") + "T00:00:00"
    elif period == "week":
        start_of_week = _BRT_now - timedelta(days=_BRT_now.weekday())
        return start_of_week.strftime("%Y-%m-%d") + "T00:00:00"
    elif period == "month":
        return _BRT_now.strftime("%Y-%m-01") + "T00:00:00"
    return "2000-01-01T00:00:00"


def _get_single_instrument_stats(instrument_key: str, period: str) -> dict:
    """
    Calcula P&L e performance de UM instrumento especifico (WIN ou WDO)
    a partir de auto_trades (tabela do Monitor MT5), no periodo solicitado.

    instrument_key: 'WIN' ou 'WDO'
    period: 'today' | 'week' | 'month' | 'all'
    """
    since        = _period_since(period)
    sym_fragment = instrument_key.upper()   # 'WIN' ou 'WDO'

    try:
        with _conn() as conn:
            rows = conn.execute("""
                SELECT
                    COUNT(*)                                          AS total,
                    SUM(CASE WHEN pnl_pts > 0 THEN 1 ELSE 0 END)    AS wins,
                    ROUND(SUM(pnl_pts), 1)                           AS total_pts,
                    ROUND(SUM(pnl_brl), 2)                           AS total_brl,
                    ROUND(AVG(pnl_pts), 1)                           AS avg_pts,
                    MIN(pnl_pts)                                     AS worst_trade,
                    MAX(pnl_pts)                                     AS best_trade
                FROM auto_trades
                WHERE closed_at IS NOT NULL
                  AND pnl_brl IS NOT NULL
                  AND closed_at >= ?
                  AND UPPER(tv_symbol) LIKE ?
            """, (since, f"%{sym_fragment}%")).fetchone()

        total = rows[0] or 0
        wins  = rows[1] or 0

        return {
            "total":     total,
            "wins":      wins,
            "losses":    total - wins,
            "total_pts": rows[2] or 0.0,
            "total_brl": rows[3] or 0.0,
            "avg_pts":   rows[4] or 0.0,
            "worst":     rows[5],
            "best":      rows[6],
            "win_rate":  round(wins / total * 100) if total else 0,
            "period":    period,
        }

    except Exception as exc:
        logger.warning("market_engine._get_single_instrument_stats(%s, %s): %s",
                       instrument_key, period, exc)
        return {
            "total": 0, "wins": 0, "losses": 0, "total_pts": 0.0,
            "total_brl": 0.0, "avg_pts": 0.0, "worst": None, "best": None,
            "win_rate": 0, "period": period,
        }


def _get_monitor_stats(period: str = "today") -> dict:
    """
    Calcula P&L e performance de WIN e WDO juntos a partir de auto_trades.
    Usado internamente para o historico de drawdown.
    period: 'today' | 'week' | 'month' | 'all'
    """
    since = _period_since(period)

    try:
        with _conn() as conn:
            rows = conn.execute("""
                SELECT
                    tv_symbol,
                    COUNT(*)                                          AS total,
                    SUM(CASE WHEN pnl_pts > 0 THEN 1 ELSE 0 END)    AS wins,
                    ROUND(SUM(pnl_pts), 1)                           AS total_pts,
                    ROUND(SUM(pnl_brl), 2)                           AS total_brl,
                    ROUND(AVG(pnl_pts), 1)                           AS avg_pts,
                    MIN(pnl_pts)                                     AS worst_trade
                FROM auto_trades
                WHERE closed_at IS NOT NULL
                  AND pnl_brl IS NOT NULL
                  AND closed_at >= ?
                GROUP BY tv_symbol
            """, (since,)).fetchall()

        result = {}
        for r in rows:
            sym   = r[0] or "?"
            total = r[1] or 0
            wins  = r[2] or 0

            key = "WIN" if "WIN" in sym.upper() else ("WDO" if "WDO" in sym.upper() else sym.upper())

            if key not in result:
                result[key] = {
                    "total": 0, "wins": 0, "total_pts": 0.0,
                    "total_brl": 0.0, "avg_pts": 0.0, "worst": None
                }

            result[key]["total"]     += total
            result[key]["wins"]      += wins
            result[key]["total_pts"] = round(result[key]["total_pts"] + (r[3] or 0), 1)
            result[key]["total_brl"] = round(result[key]["total_brl"] + (r[4] or 0), 2)
            w = r[6]
            if w is not None:
                if result[key]["worst"] is None or w < result[key]["worst"]:
                    result[key]["worst"] = w

        for key, d in result.items():
            t = d["total"]
            d["win_rate"] = round(d["wins"] / t * 100) if t else 0
            d["avg_pts"]  = round(d["total_pts"] / t, 1) if t else 0.0

        return result

    except Exception as exc:
        logger.warning("market_engine._get_monitor_stats: %s", exc)
        return {}


# ---------------------------------------------------------------------------
# P&L do Scalper (via CSV)
# ---------------------------------------------------------------------------

def _get_scalper_stats(period: str = "today") -> dict:
    """
    Le o CSV de trades do Scalper e retorna estatisticas do periodo.
    """
    try:
        if not os.path.exists(_SCALPER_CSV):
            return {"total": 0, "wins": 0, "total_brl": 0.0, "win_rate": 0}

        _BRT_now = datetime.now(_BRT)

        if period == "today":
            prefix = _BRT_now.strftime("%Y-%m-%d")
        elif period == "week":
            start_of_week = _BRT_now - timedelta(days=_BRT_now.weekday())
            prefix = start_of_week.strftime("%Y-%m-%d")
        elif period == "month":
            prefix = _BRT_now.strftime("%Y-%m")
        else:
            prefix = ""

        with open(_SCALPER_CSV, "r", encoding="utf-8") as f:
            rows = [r for r in csv.DictReader(f) if r.get("resultado")]

        if prefix:
            if period == "today":
                rows = [r for r in rows if str(r.get("datetime_brt", "")).startswith(prefix)]
            elif period == "week":
                # Filtra da segunda-feira em diante
                rows = [r for r in rows
                        if str(r.get("datetime_brt", ""))[:10] >= prefix]
            elif period == "month":
                rows = [r for r in rows
                        if str(r.get("datetime_brt", "")).startswith(prefix)]

        def _f(v):
            try: return float(v)
            except: return 0.0

        total = len(rows)
        wins  = sum(1 for r in rows if r.get("resultado") == "WIN")
        brl   = round(sum(_f(r.get("profit", 0)) for r in rows), 2)
        wr    = round(wins / total * 100) if total else 0

        return {
            "total":     total,
            "wins":      wins,
            "losses":    total - wins,
            "total_brl": brl,
            "win_rate":  wr,
            "avg_brl":   round(brl / total, 2) if total else 0.0,
        }

    except Exception as exc:
        logger.warning("market_engine._get_scalper_stats: %s", exc)
        return {"total": 0, "wins": 0, "total_brl": 0.0, "win_rate": 0, "error": str(exc)}


# ---------------------------------------------------------------------------
# Engine State: visao consolidada do dia
# ---------------------------------------------------------------------------

def get_engine_state(period: str = None) -> dict:
    """
    Retorna o estado completo do motor para todos os instrumentos.

    Comportamento de periodo:
      - period=None ou 'default': usa defaults inteligentes por instrumento
          WIN     -> ultimo mes   (validacao de estrategia de medio prazo)
          WDO     -> ultima semana (ajuste rapido de condicoes do mercado)
          Scalper -> hoje          (performance operacional do dia)
      - period='today'|'week'|'month'|'all': aplica o mesmo periodo a todos
          (usado quando o usuario clica nos filtros do frontend)

    Drawdown status -> SEMPRE baseado em hoje (seguranca diaria, nunca altera)
    """
    _VALID = {"today", "week", "month", "all"}
    _override = period if period in _VALID else None

    # Periodos por instrumento
    WIN_PERIOD     = _override or "month"
    WDO_PERIOD     = _override or "week"
    SCALPER_PERIOD = _override or "today"

    win_stats  = _get_single_instrument_stats("WIN", WIN_PERIOD)
    wdo_stats  = _get_single_instrument_stats("WDO", WDO_PERIOD)
    scalper    = _get_scalper_stats(SCALPER_PERIOD)

    # Drawdown status: sempre baseado em hoje para seguranca
    today_win  = _get_single_instrument_stats("WIN", "today")
    today_wdo  = _get_single_instrument_stats("WDO", "today")
    today_brl  = round(
        today_win.get("total_brl", 0.0) +
        today_wdo.get("total_brl", 0.0) +
        scalper.get("total_brl", 0.0),
        2,
    )

    if today_brl <= _DAILY_STOP_BRL:
        drawdown_status = "STOP_DIARIO"
        trading_allowed = False
    elif today_brl <= _DAILY_STOP_BRL * 0.6:
        drawdown_status = "ALERTA"
        trading_allowed = True
    else:
        drawdown_status = "NORMAL"
        trading_allowed = True

    # P&L total exibido (soma dos periodos de cada instrumento)
    total_brl = round(
        win_stats.get("total_brl", 0.0) +
        wdo_stats.get("total_brl", 0.0) +
        scalper.get("total_brl", 0.0),
        2,
    )

    # Ranking de prioridade — usa os dados do periodo de cada instrumento
    all_instruments = {
        "WIN":     win_stats,
        "WDO":     wdo_stats,
        "SCALPER": scalper,
    }

    pnl_values = [d.get("total_brl", 0.0) for d in all_instruments.values()]
    pnl_max    = max(abs(v) for v in pnl_values) if pnl_values else 1.0
    pnl_max    = pnl_max if pnl_max > 0 else 1.0

    priority_list = []
    for name, d in all_instruments.items():
        wr  = d.get("win_rate", 0)
        brl = d.get("total_brl", 0.0)
        tot = d.get("total", 0)

        wr_score       = min(wr, 100) * 0.6
        pnl_score      = min((brl / pnl_max + 1) / 2 * 100, 100) * 0.4
        priority_score = round(wr_score + pnl_score, 1)

        if tot < 3:
            priority_label = "aguardando_dados"
        elif wr >= _MIN_WIN_RATE_PRIORITY and brl >= 0:
            priority_label = "alta"
        elif wr >= 45:
            priority_label = "media"
        else:
            priority_label = "baixa"

        priority_list.append({
            "instrument":     name,
            "win_rate":       wr,
            "total_trades":   tot,
            "total_brl":      brl,
            "priority_score": priority_score,
            "priority":       priority_label,
            "period":         d.get("period", "today"),
        })

    priority_list.sort(key=lambda x: x["priority_score"], reverse=True)

    # Labels legíveis para o frontend
    _period_labels = {
        "today": "Hoje",
        "week":  "Última Semana",
        "month": "Último Mês",
        "all":   "Todo o Período",
    }

    win_stats["period_label"]    = _period_labels.get(WIN_PERIOD,     WIN_PERIOD)
    wdo_stats["period_label"]    = _period_labels.get(WDO_PERIOD,     WDO_PERIOD)
    scalper["period_label"]      = _period_labels.get(SCALPER_PERIOD, SCALPER_PERIOD)

    return {
        "period":           period,   # mantido para compatibilidade
        "total_pnl_brl":    total_brl,
        "today_pnl_brl":    today_brl,
        "daily_stop_brl":   _DAILY_STOP_BRL,
        "drawdown_status":  drawdown_status,
        "trading_allowed":  trading_allowed,
        "instruments": {
            "WIN":     win_stats,
            "WDO":     wdo_stats,
            "SCALPER": scalper,
        },
        "priority_ranking": priority_list,
        "generated_at":     datetime.now(_BRT).isoformat(),
    }


# ---------------------------------------------------------------------------
# Drawdown historico por semana
# ---------------------------------------------------------------------------

def get_drawdown_history() -> list:
    """
    Retorna P&L semanal dos ultimos 8 periodos para visualizacao de curva.
    Combina monitor + scalper.
    """
    try:
        _BRT_now = datetime.now(_BRT)
        result   = []

        for week_offset in range(7, -1, -1):
            week_start = _BRT_now - timedelta(days=_BRT_now.weekday() + week_offset * 7)
            week_end   = week_start + timedelta(days=6)
            ws = week_start.strftime("%Y-%m-%d")
            we = week_end.strftime("%Y-%m-%d")

            with _conn() as conn:
                mon_row = conn.execute("""
                    SELECT ROUND(SUM(pnl_brl), 2)
                    FROM auto_trades
                    WHERE closed_at IS NOT NULL
                      AND pnl_brl IS NOT NULL
                      AND DATE(closed_at) BETWEEN ? AND ?
                """, (ws, we)).fetchone()

            mon_brl = mon_row[0] or 0.0

            # Scalper CSV
            scalp_brl = 0.0
            if os.path.exists(_SCALPER_CSV):
                try:
                    with open(_SCALPER_CSV, "r", encoding="utf-8") as f:
                        for r in csv.DictReader(f):
                            dt = str(r.get("datetime_brt", ""))[:10]
                            if ws <= dt <= we and r.get("resultado"):
                                try:
                                    scalp_brl += float(r.get("profit", 0))
                                except Exception:
                                    pass
                    scalp_brl = round(scalp_brl, 2)
                except Exception:
                    pass

            result.append({
                "week":      ws,
                "label":     f"Sem {week_start.strftime('%d/%m')}",
                "monitor_brl": mon_brl,
                "scalper_brl": scalp_brl,
                "total_brl":   round(mon_brl + scalp_brl, 2),
            })

        return result

    except Exception as exc:
        logger.warning("market_engine.get_drawdown_history: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Verificacao se trading e permitido
# ---------------------------------------------------------------------------

def check_trading_allowed(instrument: str = None) -> dict:
    """
    Retorna se e seguro operar agora com base no P&L do dia.
    instrument: 'WIN' | 'WDO' | 'SCALPER' | None (verifica todos)
    """
    state = get_engine_state("today")

    base = {
        "allowed":  state["trading_allowed"],
        "reason":   state["drawdown_status"],
        "pnl_brl":  state["total_pnl_brl"],
        "daily_stop": _DAILY_STOP_BRL,
    }

    if instrument:
        instr_data = state["instruments"].get(instrument.upper(), {})
        instr_brl  = instr_data.get("total_brl", 0.0)
        base["instrument"]      = instrument.upper()
        base["instrument_pnl"]  = instr_brl

    return base
