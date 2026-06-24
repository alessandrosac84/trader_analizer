"""
services/risk_settings_service.py
-----------------------------------
Gerencia configuracoes do Capital Protection Engine (CPE) V1.1.
Persiste em SQLite: tabelas risk_settings, risk_state, risk_events.

REGRA: este modulo NUNCA altera modulos existentes.
"""
import csv
import io
import json
import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)
_BRT = timezone(timedelta(hours=-3))

# ---------------------------------------------------------------------------
# Defaults — todos os 14 blocos + melhorias V1.1
# ---------------------------------------------------------------------------
DEFAULTS = {
    # Bloco 1 — Daily Loss
    "daily_loss_enabled":               True,
    "daily_loss_value":                 300.0,
    # Bloco 2 — Profit Protection
    "profit_protection_enabled":        True,
    "profit_protection_activation":     250.0,
    # Bloco 3 — Daily Trailing (M6: modo fixo ou percentual)
    "daily_trailing_enabled":           True,
    "daily_trailing_mode":              "FIXO",   # "FIXO" ou "PERCENTUAL"
    "daily_trailing_value":             100.0,    # usado no modo FIXO
    "daily_trailing_percent":           20.0,     # usado no modo PERCENTUAL
    # Bloco 4 — Hard Daily Target
    "hard_daily_target_enabled":        True,
    "hard_daily_target":                800.0,
    # Bloco 5 — Soft Daily Target (M5: expandido)
    "soft_target_enabled":              True,
    "soft_target_value":                500.0,
    "soft_score_penalty":               2,
    "soft_confluences_penalty":         2,
    "soft_risk_reduction":              10,
    "soft_max_position_factor":         1.0,   # M5: fator de reducao de tamanho (0.5 = 50%)
    # Bloco 6 — Max Trades
    "max_trades_enabled":               True,
    "max_trades_per_day":               8,
    # Bloco 7 — Consecutive Losses
    "loss_pause_enabled":               True,
    "pause_after_losses":               2,
    "pause_minutes":                    30,
    "stop_after_losses":                3,
    # Bloco 8 — Cooldown
    "cooldown_enabled":                 True,
    "cooldown_minutes_after_loss":      15,
    "cooldown_minutes_after_win":       5,
    # Bloco 9 — Break Even
    "breakeven_enabled":                True,
    "breakeven_trigger_rr":             1.0,
    "breakeven_offset_points":          10,
    # Bloco 10 — Trade Trailing
    "trade_trailing_enabled":           True,
    "trade_trailing_mode":              "ATR",
    "atr_multiplier":                   1.0,
    # Bloco 11 — Risk Budget
    "risk_budget_enabled":              True,
    # Bloco 12 — Circuit Breaker
    "circuit_breaker_enabled":          True,
    "max_daily_stops_sequence":         3,
    # Bloco 13 — Equity Drawdown
    "equity_protection_enabled":        True,
    "max_equity_drawdown_percent":      3.0,
}


def _conn():
    from services.db import _conn as _db
    return _db()


def init_cpe_tables():
    """Cria/migra tabelas do CPE. Chamado no startup."""
    with _conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS risk_settings (
                key        TEXT PRIMARY KEY,
                value      TEXT NOT NULL,
                updated_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS risk_state (
                key        TEXT PRIMARY KEY,
                value      TEXT,
                updated_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS risk_events (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp        TEXT NOT NULL,
                event_type       TEXT NOT NULL,
                event_reason     TEXT,
                trade_id         INTEGER,
                tv_symbol        TEXT,
                acao             TEXT,
                score            INTEGER,
                risk_budget      REAL,
                daily_pnl        REAL,
                daily_max_profit REAL,
                protected_floor  REAL,
                status_before    TEXT,
                status_after     TEXT
            )
        """)
        # Migracoes: adicionar colunas novas se a tabela ja existe (M8)
        existing_cols = {r[1] for r in conn.execute("PRAGMA table_info(risk_events)").fetchall()}
        for col, typedef in [
            ("tv_symbol",   "TEXT"),
            ("acao",        "TEXT"),
            ("score",       "INTEGER"),
            ("risk_budget", "REAL"),
        ]:
            if col not in existing_cols:
                try:
                    conn.execute(f"ALTER TABLE risk_events ADD COLUMN {col} {typedef}")
                except Exception:
                    pass
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_risk_events_ts
            ON risk_events(timestamp)
        """)
    _seed_defaults()


def _seed_defaults():
    """Insere defaults se a chave ainda nao existir."""
    with _conn() as conn:
        existing = {r[0] for r in conn.execute("SELECT key FROM risk_settings").fetchall()}
        now = datetime.now(_BRT).isoformat()
        for key, val in DEFAULTS.items():
            if key not in existing:
                conn.execute(
                    "INSERT INTO risk_settings (key, value, updated_at) VALUES (?, ?, ?)",
                    (key, json.dumps(val), now)
                )


def get_settings() -> dict:
    """Retorna todas as configuracoes com valores tipados."""
    try:
        with _conn() as conn:
            rows = conn.execute("SELECT key, value FROM risk_settings").fetchall()
        result = dict(DEFAULTS)
        for key, val_str in rows:
            try:
                result[key] = json.loads(val_str)
            except Exception:
                result[key] = val_str
        return result
    except Exception as exc:
        logger.warning("get_settings error: %s", exc)
        return dict(DEFAULTS)


def save_settings(updates: dict) -> bool:
    """Salva um dict de {key: value} no banco."""
    try:
        now = datetime.now(_BRT).isoformat()
        with _conn() as conn:
            for key, val in updates.items():
                conn.execute("""
                    INSERT INTO risk_settings (key, value, updated_at) VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """, (key, json.dumps(val), now))
        return True
    except Exception as exc:
        logger.warning("save_settings error: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Risk State
# ---------------------------------------------------------------------------

def get_state(key: str, default=None):
    try:
        with _conn() as conn:
            row = conn.execute(
                "SELECT value FROM risk_state WHERE key = ?", (key,)
            ).fetchone()
        if row is None:
            return default
        return json.loads(row[0])
    except Exception:
        return default


def set_state(key: str, value) -> None:
    try:
        now = datetime.now(_BRT).isoformat()
        with _conn() as conn:
            conn.execute("""
                INSERT INTO risk_state (key, value, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """, (key, json.dumps(value), now))
    except Exception as exc:
        logger.warning("set_state %s error: %s", key, exc)


# ---------------------------------------------------------------------------
# Risk Events — M8: log detalhado
# ---------------------------------------------------------------------------

def log_risk_event(
    event_type: str,
    event_reason: str = "",
    trade_id: int = None,
    tv_symbol: str = None,
    acao: str = None,
    score: int = None,
    risk_budget: float = None,
    daily_pnl: float = None,
    daily_max_profit: float = None,
    protected_floor: float = None,
    status_before: str = None,
    status_after: str = None,
) -> None:
    try:
        now = datetime.now(_BRT).isoformat()
        with _conn() as conn:
            conn.execute("""
                INSERT INTO risk_events (
                    timestamp, event_type, event_reason, trade_id,
                    tv_symbol, acao, score, risk_budget,
                    daily_pnl, daily_max_profit, protected_floor,
                    status_before, status_after
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                now, event_type, event_reason, trade_id,
                tv_symbol, acao, score, risk_budget,
                daily_pnl, daily_max_profit, protected_floor,
                status_before, status_after,
            ))
    except Exception as exc:
        logger.warning("log_risk_event error: %s", exc)


def get_recent_events(limit: int = 50) -> list:
    try:
        with _conn() as conn:
            rows = conn.execute("""
                SELECT id, timestamp, event_type, event_reason,
                       tv_symbol, acao, score, risk_budget,
                       daily_pnl, daily_max_profit, protected_floor,
                       status_before, status_after
                FROM risk_events
                ORDER BY id DESC LIMIT ?
            """, (limit,)).fetchall()
        return [
            {
                "id": r[0], "timestamp": r[1], "event_type": r[2],
                "event_reason": r[3], "tv_symbol": r[4], "acao": r[5],
                "score": r[6], "risk_budget": r[7],
                "daily_pnl": r[8], "daily_max_profit": r[9],
                "protected_floor": r[10], "status_before": r[11], "status_after": r[12],
            }
            for r in rows
        ]
    except Exception as exc:
        logger.warning("get_recent_events error: %s", exc)
        return []


# ---------------------------------------------------------------------------
# M7 — Analytics de Efetividade
# ---------------------------------------------------------------------------

def analytics_effectiveness() -> dict:
    """Calcula estatisticas de efetividade de cada regra CPE."""
    try:
        with _conn() as conn:
            rows = conn.execute("""
                SELECT event_type, COUNT(*) as cnt,
                       SUM(ABS(daily_pnl)) as pnl_sum
                FROM risk_events
                GROUP BY event_type
                ORDER BY cnt DESC
            """).fetchall()

            # Dias bloqueados (distinct dates com day_blocked)
            days_blocked = conn.execute("""
                SELECT COUNT(DISTINCT date(timestamp)) FROM risk_events
                WHERE event_type IN ('DAILY_LOSS_TRIGGERED','TRAILING_PROFIT_TRIGGERED',
                                     'HARD_TARGET_REACHED','CONSECUTIVE_LOSSES_STOP',
                                     'EQUITY_DRAWDOWN_TRIGGERED','CIRCUIT_BREAKER_TRIGGERED')
            """).fetchone()[0] or 0

            # Total de trades bloqueados
            trades_blocked = conn.execute("""
                SELECT COUNT(*) FROM risk_events
                WHERE event_type IN ('DAILY_LOSS_TRIGGERED','TRAILING_PROFIT_TRIGGERED',
                                     'HARD_TARGET_REACHED','MAX_TRADES_REACHED',
                                     'CIRCUIT_BREAKER_TRIGGERED','TRADE_BLOCKED_RISK_BUDGET',
                                     'CONSECUTIVE_LOSSES_STOP','EQUITY_DRAWDOWN_TRIGGERED',
                                     'COOLDOWN','CONSECUTIVE_LOSS_PAUSE')
            """).fetchone()[0] or 0

            # Circuit breakers acionados
            circuit_count = conn.execute("""
                SELECT COUNT(*) FROM risk_events
                WHERE event_type = 'CIRCUIT_BREAKER_TRIGGERED'
            """).fetchone()[0] or 0

            # Cooldowns
            cooldown_count = conn.execute("""
                SELECT COUNT(*) FROM risk_events
                WHERE event_type IN ('COOLDOWN_STARTED','CONSECUTIVE_LOSS_PAUSE')
            """).fetchone()[0] or 0

        by_rule = {}
        for rule, cnt, pnl_sum in rows:
            by_rule[rule] = {"count": cnt, "pnl_sum": round(pnl_sum or 0, 2)}

        return {
            "trades_blocked":   trades_blocked,
            "days_protected":   days_blocked,
            "circuit_breakers": circuit_count,
            "cooldowns":        cooldown_count,
            "by_rule":          by_rule,
        }
    except Exception as exc:
        logger.warning("analytics_effectiveness error: %s", exc)
        return {"trades_blocked": 0, "days_protected": 0, "circuit_breakers": 0,
                "cooldowns": 0, "by_rule": {}}


# ---------------------------------------------------------------------------
# M9 — Exportacao de Auditoria
# ---------------------------------------------------------------------------

def export_events_csv() -> str:
    """Retorna todos os eventos como string CSV."""
    try:
        events = get_recent_events(limit=10000)
        if not events:
            return "id,timestamp,event_type,event_reason,tv_symbol,acao,score,risk_budget,daily_pnl,daily_max_profit,protected_floor,status_before,status_after\n"
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=list(events[0].keys()))
        writer.writeheader()
        writer.writerows(events)
        return output.getvalue()
    except Exception as exc:
        logger.warning("export_events_csv error: %s", exc)
        return ""


def export_events_json() -> list:
    """Retorna todos os eventos como lista JSON."""
    return get_recent_events(limit=10000)
