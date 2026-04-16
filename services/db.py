import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from services.config import Config


def _conn() -> sqlite3.Connection:
    Path(Config.DATABASE_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(Config.DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    rows = conn.execute("PRAGMA table_info(analyses)").fetchall()
    have = {r[1] for r in rows}
    alters: list[str] = []
    if "exec_recorded" not in have:
        alters.append(
            "ALTER TABLE analyses ADD COLUMN exec_recorded INTEGER NOT NULL DEFAULT 0"
        )
    if "exec_entry" not in have:
        alters.append("ALTER TABLE analyses ADD COLUMN exec_entry TEXT")
    if "exec_exit" not in have:
        alters.append("ALTER TABLE analyses ADD COLUMN exec_exit TEXT")
    if "exec_pnl" not in have:
        alters.append("ALTER TABLE analyses ADD COLUMN exec_pnl REAL")
    if "exec_logged_at" not in have:
        alters.append("ALTER TABLE analyses ADD COLUMN exec_logged_at TEXT")
    for sql in alters:
        conn.execute(sql)


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
        _migrate(conn)


_COLS = (
    "id, created_at, stored_filename, trader_json, validator_json, risk_json, "
    "decisao, score_final, permitir_trade, exec_recorded, exec_entry, exec_exit, "
    "exec_pnl, exec_logged_at"
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
            f"SELECT {_COLS} FROM analyses WHERE id = ?",
            (analysis_id,),
        ).fetchone()
    return dict(row) if row else None


def list_analyses(limit: int = 100) -> list[dict[str, Any]]:
    with _conn() as conn:
        rows = conn.execute(
            f"""
            SELECT {_COLS}
            FROM analyses
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def delete_all_analyses() -> int:
    """Remove todas as análises gravadas. Retorna a quantidade de registros removidos."""
    with _conn() as conn:
        n = int(conn.execute("SELECT COUNT(*) FROM analyses").fetchone()[0])
        conn.execute("DELETE FROM analyses")
    return n


def _acao_from_trader_json(trader_json: str | None) -> str:
    if not trader_json:
        return ""
    try:
        d = json.loads(trader_json)
        if not isinstance(d, dict):
            return ""
        a = d.get("acao")
        return str(a or "").strip().upper()
    except (json.JSONDecodeError, TypeError, ValueError):
        return ""


def _recommendation_counts(
    conn: sqlite3.Connection,
    period: str,
    ref_date: date,
    today: date,
) -> dict[str, int]:
    """Conta sinais COMPRA/VENDA no trader_json, filtrando por data UTC de `created_at`."""
    period = (period or "all").strip().lower()
    if period == "all":
        rows = conn.execute("SELECT trader_json FROM analyses").fetchall()
    elif period == "day":
        d = ref_date.isoformat()
        rows = conn.execute(
            """
            SELECT trader_json FROM analyses
            WHERE date(created_at) = date(?)
            """,
            (d,),
        ).fetchall()
    else:
        bounds = _month_bounds(ref_date, today)
        if bounds is None:
            return {"recomendacoes_compra": 0, "recomendacoes_venda": 0}
        mstart, mend = bounds
        i0, i1 = mstart.isoformat(), mend.isoformat()
        rows = conn.execute(
            """
            SELECT trader_json FROM analyses
            WHERE date(created_at) >= date(?) AND date(created_at) <= date(?)
            """,
            (i0, i1),
        ).fetchall()
    compra = venda = 0
    for r in rows:
        acao = _acao_from_trader_json(r["trader_json"])
        if "COMPRA" in acao:
            compra += 1
        if "VENDA" in acao:
            venda += 1
    return {"recomendacoes_compra": compra, "recomendacoes_venda": venda}


def parse_pnl_value(raw: Any) -> float | None:
    """Aceita número JSON ou string pt-BR (ex.: 1.234,56, -50, 36.009 como milhar)."""
    if raw is None or raw == "":
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    s = str(raw).strip().replace("\u00a0", " ").replace(" ", "")
    if not s:
        return None
    neg = s.startswith("-")
    s = s.replace("-", "").strip()
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    elif "." in s and s.count(".") >= 1:
        parts = s.split(".")
        if all(p.isdigit() for p in parts) and len(parts) >= 2:
            if parts[0] == "0" and len(parts) == 2:
                try:
                    v = float(s)
                    return -v if neg else v
                except ValueError:
                    return None
            if len(parts) >= 2 and all(len(p) == 3 for p in parts[1:]):
                try:
                    v = float("".join(parts))
                    return -v if neg else v
                except ValueError:
                    return None
    try:
        v = float(s)
        return -v if neg else v
    except ValueError:
        return None


def update_execution(
    analysis_id: int,
    *,
    recorded: bool,
    entry: str | None = None,
    exit_: str | None = None,
    pnl: float | None = None,
) -> dict[str, Any] | None:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as conn:
        row = conn.execute(
            "SELECT id FROM analyses WHERE id = ?",
            (analysis_id,),
        ).fetchone()
        if not row:
            return None
        if not recorded:
            conn.execute(
                """
                UPDATE analyses SET
                    exec_recorded = 0,
                    exec_entry = NULL,
                    exec_exit = NULL,
                    exec_pnl = NULL,
                    exec_logged_at = NULL
                WHERE id = ?
                """,
                (analysis_id,),
            )
        else:
            conn.execute(
                """
                UPDATE analyses SET
                    exec_recorded = 1,
                    exec_entry = ?,
                    exec_exit = ?,
                    exec_pnl = ?,
                    exec_logged_at = ?
                WHERE id = ?
                """,
                (
                    (entry or "").strip() or None,
                    (exit_ or "").strip() or None,
                    pnl,
                    now,
                    analysis_id,
                ),
            )
        out = conn.execute(
            f"SELECT {_COLS} FROM analyses WHERE id = ?",
            (analysis_id,),
        ).fetchone()
    return dict(out) if out else None


def _last_day_of_month(d: date) -> date:
    if d.month == 12:
        n = date(d.year + 1, 1, 1)
    else:
        n = date(d.year, d.month + 1, 1)
    return n - timedelta(days=1)


def _month_bounds(ref: date, today: date) -> tuple[date, date] | None:
    """Mês corrente: dia 1 até hoje. Mês passado: mês inteiro. Futuro: None."""
    mstart = ref.replace(day=1)
    if (ref.year, ref.month) > (today.year, today.month):
        return None
    last = _last_day_of_month(mstart)
    if ref.year == today.year and ref.month == today.month:
        mend = min(today, last)
    else:
        mend = last
    return mstart, mend


def _aggregate_exec_rows(rows: list) -> dict[str, Any]:
    total_reg = len(rows)
    with_pnl: list[float] = []
    for r in rows:
        p = r["exec_pnl"]
        if p is not None:
            try:
                with_pnl.append(float(p))
            except (TypeError, ValueError):
                pass
    wins = sum(1 for p in with_pnl if p > 0)
    losses = sum(1 for p in with_pnl if p < 0)
    breakeven = sum(1 for p in with_pnl if p == 0)
    pnl_sum = sum(with_pnl)
    return {
        "total_registrados": total_reg,
        "com_pnl_informado": len(with_pnl),
        "wins": wins,
        "losses": losses,
        "breakeven": breakeven,
        "pnl_total": round(pnl_sum, 2),
    }


def _snapshot_dia_utc(conn, dia: str) -> dict[str, Any]:
    """dia = YYYY-MM-DD (UTC)."""
    rows = conn.execute(
        """
        SELECT exec_pnl FROM analyses
        WHERE exec_recorded = 1 AND exec_logged_at IS NOT NULL
        AND date(exec_logged_at) = date(?)
        """,
        (dia,),
    ).fetchall()
    cnt = len(rows)
    pnls: list[float] = []
    for r in rows:
        p = r["exec_pnl"]
        if p is not None:
            try:
                pnls.append(float(p))
            except (TypeError, ValueError):
                pass
    pnl = sum(pnls)
    if not pnls:
        st = "neutro"
    elif pnl > 0:
        st = "lucro"
    elif pnl < 0:
        st = "prejuizo"
    else:
        st = "zero"
    return {
        "data": dia,
        "trades_registrados_hoje": cnt,
        "pnl": round(pnl, 2),
        "status": st,
    }


def journal_stats(period: str = "all", ref: str | None = None) -> dict[str, Any]:
    """Agrega trades com exec_recorded=1 e contagem de sinais COMPRA/VENDA no trader.

    P/L: filtro por data UTC de exec_logged_at. Recomendações: por data UTC de created_at.
    Períodos: all | day | month.
    """
    today = datetime.now(timezone.utc).date()
    ref_date = today
    if ref:
        try:
            ref_date = date.fromisoformat(ref.strip()[:10])
        except ValueError:
            ref_date = today

    period = (period or "all").strip().lower()
    if period not in ("all", "day", "month"):
        period = "all"

    with _conn() as conn:
        if period == "all":
            rows = conn.execute(
                """
                SELECT exec_recorded, exec_pnl, exec_logged_at
                FROM analyses
                WHERE exec_recorded = 1 AND exec_logged_at IS NOT NULL
                """
            ).fetchall()
            out = _aggregate_exec_rows(rows)
            today_iso = today.isoformat()
            out["period"] = "all"
            out["ref"] = today_iso
            out["intervalo"] = None
            out["contexto"] = "Visão geral — todos os trades registrados (datas em UTC)."
            out["hoje_utc"] = _snapshot_dia_utc(conn, today_iso)
            out.update(_recommendation_counts(conn, period, ref_date, today))
            return out

        if period == "day":
            d = ref_date.isoformat()
            rows = conn.execute(
                """
                SELECT exec_recorded, exec_pnl, exec_logged_at
                FROM analyses
                WHERE exec_recorded = 1 AND exec_logged_at IS NOT NULL
                AND date(exec_logged_at) = date(?)
                """,
                (d,),
            ).fetchall()
            out = _aggregate_exec_rows(rows)
            out["period"] = "day"
            out["ref"] = d
            out["intervalo"] = {"inicio": d, "fim": d}
            out["contexto"] = (
                f"Dia {ref_date.strftime('%d/%m/%Y')} (UTC) — apenas registros salvos nesse dia."
            )
            pnl = out["pnl_total"]
            st = (
                "neutro"
                if not rows
                else "lucro"
                if pnl > 0
                else "prejuizo"
                if pnl < 0
                else "zero"
            )
            out["hoje_utc"] = {
                "data": d,
                "trades_registrados_hoje": out["total_registrados"],
                "pnl": pnl,
                "status": st,
            }
            out.update(_recommendation_counts(conn, period, ref_date, today))
            return out

        # month
        bounds = _month_bounds(ref_date, today)
        if bounds is None:
            return {
                "period": "month",
                "ref": ref_date.isoformat(),
                "intervalo": None,
                "contexto": "Período futuro — sem dados.",
                "total_registrados": 0,
                "com_pnl_informado": 0,
                "wins": 0,
                "losses": 0,
                "breakeven": 0,
                "pnl_total": 0.0,
                "recomendacoes_compra": 0,
                "recomendacoes_venda": 0,
                "hoje_utc": {
                    "data": today.isoformat(),
                    "trades_registrados_hoje": 0,
                    "pnl": 0.0,
                    "status": "neutro",
                },
            }

        mstart, mend = bounds
        i0, i1 = mstart.isoformat(), mend.isoformat()
        rows = conn.execute(
            """
            SELECT exec_recorded, exec_pnl, exec_logged_at
            FROM analyses
            WHERE exec_recorded = 1 AND exec_logged_at IS NOT NULL
            AND date(exec_logged_at) >= date(?) AND date(exec_logged_at) <= date(?)
            """,
            (i0, i1),
        ).fetchall()
        out = _aggregate_exec_rows(rows)
        out["period"] = "month"
        out["ref"] = ref_date.isoformat()
        out["intervalo"] = {"inicio": i0, "fim": i1}
        if ref_date.year == today.year and ref_date.month == today.month:
            ctx = (
                f"Consolidado do mês (parcial): {mstart.strftime('%d/%m/%Y')} a "
                f"{mend.strftime('%d/%m/%Y')} (UTC), até hoje."
            )
        else:
            ctx = (
                f"Consolidado do mês: {mstart.strftime('%d/%m/%Y')} a "
                f"{mend.strftime('%d/%m/%Y')} (UTC)."
            )
        out["contexto"] = ctx
        pnl = out["pnl_total"]
        st = (
            "neutro"
            if not rows
            else "lucro"
            if pnl > 0
            else "prejuizo"
            if pnl < 0
            else "zero"
        )
        out["hoje_utc"] = {
            "data": i1,
            "trades_registrados_hoje": out["total_registrados"],
            "pnl": pnl,
            "status": st,
        }
        out.update(_recommendation_counts(conn, period, ref_date, today))
        return out
