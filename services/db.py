import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from services.config import Config


def _conn() -> sqlite3.Connection:
    Path(Config.DATABASE_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(Config.DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS analyses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                stored_filename TEXT NOT NULL,
                trader_json TEXT NOT NULL,
                validator_json TEXT NOT NULL,
                risk_json TEXT NOT NULL,
                decisao TEXT,
                score_final INTEGER,
                permitir_trade INTEGER
            )
            """
        )


def insert_analysis(
    stored_filename: str,
    trader_json: str,
    validator_json: str,
    risk_json: str,
    decisao: str | None,
    score_final: int | None,
    permitir_trade: bool | None,
) -> tuple[int, str]:
    now = datetime.now(timezone.utc).isoformat()
    pt = 1 if permitir_trade is True else 0 if permitir_trade is False else None
    with _conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO analyses (
                created_at, stored_filename, trader_json, validator_json, risk_json,
                decisao, score_final, permitir_trade
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now,
                stored_filename,
                trader_json,
                validator_json,
                risk_json,
                decisao,
                score_final,
                pt,
            ),
        )
        return int(cur.lastrowid), now


def get_analysis(analysis_id: int) -> dict[str, Any] | None:
    with _conn() as conn:
        row = conn.execute(
            """
            SELECT id, created_at, stored_filename, trader_json, validator_json, risk_json,
                   decisao, score_final, permitir_trade
            FROM analyses WHERE id = ?
            """,
            (analysis_id,),
        ).fetchone()
    return dict(row) if row else None


def list_analyses(limit: int = 100) -> list[dict[str, Any]]:
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT id, created_at, stored_filename, trader_json, validator_json, risk_json,
                   decisao, score_final, permitir_trade
            FROM analyses
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]
