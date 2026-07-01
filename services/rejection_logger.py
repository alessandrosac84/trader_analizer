"""
rejection_logger.py — Auditoria de rejeições do Scalper (P1 Scalper V2)

Registra cada sinal bloqueado no auto-check com código de rejeição,
score, e detalhes do snapshot para diagnóstico.
"""

import sqlite3
import logging
import os
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

# ── DB path ────────────────────────────────────────────────────────────────
_DB_DIR  = os.path.join(os.path.dirname(__file__), "..", "data")
_DB_PATH = os.path.join(_DB_DIR, "scalper_rejections.db")

# ── Códigos de rejeição ─────────────────────────────────────────────────────
CODE_SCORE_BELOW_MINIMUM      = "SCORE_BELOW_MINIMUM"
CODE_LOW_TICK_CONSISTENCY     = "LOW_TICK_CONSISTENCY"
CODE_ENTRY_TOO_LATE           = "ENTRY_TOO_LATE"
CODE_VWAP_DIRECTION_BLOCK     = "VWAP_DIRECTION_BLOCK"
CODE_COOLDOWN_ACTIVE          = "COOLDOWN_ACTIVE"
CODE_MAX_DAILY_TRADES         = "MAX_DAILY_TRADES"
CODE_LOW_AGGRESSION           = "LOW_AGGRESSION"
CODE_CONSECUTIVE_LOSSES_PAUSE = "CONSECUTIVE_LOSSES_PAUSE"
CODE_POSITION_ALREADY_OPEN    = "POSITION_ALREADY_OPEN"
CODE_OUTSIDE_TRADING_HOURS    = "OUTSIDE_TRADING_HOURS"
CODE_HARD_BLOCK_DELTA         = "HARD_BLOCK_DELTA"
CODE_DIRECTIONAL_UPLIFT       = "DIRECTIONAL_UPLIFT"
CODE_AUTO_DISABLED            = "AUTO_DISABLED"
CODE_SIGNAL_NEUTRAL           = "SIGNAL_NEUTRAL"
CODE_LOW_VOLATILITY           = "LOW_VOLATILITY"


def _get_conn() -> sqlite3.Connection:
    os.makedirs(_DB_DIR, exist_ok=True)
    conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_table() -> None:
    with _get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS scalper_rejections (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp        TEXT NOT NULL,
                symbol           TEXT NOT NULL,
                signal           TEXT NOT NULL DEFAULT 'NEUTRO',
                rejection_code   TEXT NOT NULL,
                rejection_reason TEXT NOT NULL,
                score            REAL,
                buy_pct          REAL,
                sell_pct         REAL,
                vwap_regime      TEXT,
                hard_blocked     INTEGER DEFAULT 0,
                details          TEXT
            )
        """)
        # índice para queries por data/símbolo
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_rej_ts_sym
            ON scalper_rejections(timestamp, symbol)
        """)
        conn.commit()


# ── Inicializa tabela na importação ─────────────────────────────────────────
try:
    _ensure_table()
except Exception as _e:
    logger.warning("rejection_logger: não foi possível criar tabela: %s", _e)


def _brt_now() -> str:
    brt = timezone(timedelta(hours=-3))
    return datetime.now(brt).isoformat(timespec="seconds")


def log_rejection(
    symbol: str,
    signal: str,
    rejection_code: str,
    rejection_reason: str,
    *,
    score: float | None = None,
    buy_pct: float | None = None,
    sell_pct: float | None = None,
    vwap_regime: str | None = None,
    hard_blocked: bool = False,
    details: str | None = None,
) -> None:
    """Registra uma rejeição de sinal na tabela SQLite."""
    try:
        with _get_conn() as conn:
            conn.execute("""
                INSERT INTO scalper_rejections
                    (timestamp, symbol, signal, rejection_code, rejection_reason,
                     score, buy_pct, sell_pct, vwap_regime, hard_blocked, details)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                _brt_now(), symbol, signal, rejection_code, rejection_reason,
                score, buy_pct, sell_pct, vwap_regime,
                1 if hard_blocked else 0,
                details,
            ))
            conn.commit()
    except Exception as exc:
        logger.warning("rejection_logger.log_rejection error: %s", exc)


def get_rejection_stats(
    symbol: str | None = None,
    hours: int = 24,
) -> dict:
    """
    Retorna estatísticas de rejeições das últimas `hours` horas.
    Se symbol for fornecido, filtra por símbolo.
    """
    try:
        brt = timezone(timedelta(hours=-3))
        since = (datetime.now(brt) - timedelta(hours=hours)).isoformat(timespec="seconds")

        query_args: list = [since]
        sym_filter = ""
        if symbol:
            sym_filter = " AND symbol = ?"
            query_args.append(symbol)

        with _get_conn() as conn:
            # Total de rejeições
            total = conn.execute(
                f"SELECT COUNT(*) FROM scalper_rejections WHERE timestamp >= ?{sym_filter}",
                query_args,
            ).fetchone()[0]

            # Por código
            rows = conn.execute(
                f"""
                SELECT rejection_code, COUNT(*) as cnt
                FROM scalper_rejections
                WHERE timestamp >= ?{sym_filter}
                GROUP BY rejection_code
                ORDER BY cnt DESC
                """,
                query_args,
            ).fetchall()
            by_code = {r["rejection_code"]: r["cnt"] for r in rows}

            # Últimas 10 rejeições
            recent = conn.execute(
                f"""
                SELECT timestamp, symbol, signal, rejection_code, rejection_reason,
                       score, buy_pct, sell_pct, vwap_regime, hard_blocked
                FROM scalper_rejections
                WHERE timestamp >= ?{sym_filter}
                ORDER BY timestamp DESC
                LIMIT 20
                """,
                query_args,
            ).fetchall()
            recent_list = [dict(r) for r in recent]

            # Score médio bloqueado vs score_min
            score_avg = conn.execute(
                f"""
                SELECT AVG(score) FROM scalper_rejections
                WHERE timestamp >= ?{sym_filter} AND score IS NOT NULL
                """,
                query_args,
            ).fetchone()[0]

        return {
            "ok":         True,
            "hours":      hours,
            "symbol":     symbol,
            "total":      total,
            "by_code":    by_code,
            "score_avg":  round(score_avg, 1) if score_avg is not None else None,
            "recent":     recent_list,
        }
    except Exception as exc:
        logger.warning("rejection_logger.get_rejection_stats error: %s", exc)
        return {"ok": False, "error": str(exc), "total": 0, "by_code": {}, "recent": []}


def cleanup_old_rejections(days: int = 7) -> int:
    """Remove rejeições mais antigas que `days` dias. Retorna número removido."""
    try:
        brt = timezone(timedelta(hours=-3))
        cutoff = (datetime.now(brt) - timedelta(days=days)).isoformat(timespec="seconds")
        with _get_conn() as conn:
            cur = conn.execute(
                "DELETE FROM scalper_rejections WHERE timestamp < ?", (cutoff,)
            )
            conn.commit()
            return cur.rowcount
    except Exception as exc:
        logger.warning("rejection_logger.cleanup error: %s", exc)
        return 0
