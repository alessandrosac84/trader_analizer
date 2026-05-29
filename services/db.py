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
    new_cols = [
        ("entrada_price",      "ALTER TABLE analyses ADD COLUMN entrada_price REAL"),
        ("stop_price",         "ALTER TABLE analyses ADD COLUMN stop_price REAL"),
        ("tp1_price",          "ALTER TABLE analyses ADD COLUMN tp1_price REAL"),
        ("tp2_price",          "ALTER TABLE analyses ADD COLUMN tp2_price REAL"),
        ("tp3_price",          "ALTER TABLE analyses ADD COLUMN tp3_price REAL"),
        ("tp1_hit",            "ALTER TABLE analyses ADD COLUMN tp1_hit INTEGER NOT NULL DEFAULT 0"),
        ("tp2_hit",            "ALTER TABLE analyses ADD COLUMN tp2_hit INTEGER NOT NULL DEFAULT 0"),
        ("tp3_hit",            "ALTER TABLE analyses ADD COLUMN tp3_hit INTEGER NOT NULL DEFAULT 0"),
        ("stop_hit",           "ALTER TABLE analyses ADD COLUMN stop_hit INTEGER NOT NULL DEFAULT 0"),
        ("outcome_checked_at", "ALTER TABLE analyses ADD COLUMN outcome_checked_at TEXT"),
    ]
    for col, sql in new_cols:
        if col not in have:
            alters.append(sql)
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
    "exec_pnl, exec_logged_at, "
    "entrada_price, stop_price, tp1_price, tp2_price, tp3_price, "
    "tp1_hit, tp2_hit, tp3_hit, stop_hit, outcome_checked_at"
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
    from services.json_utils import parse_trade_levels
    lvl = parse_trade_levels(trader_json)
    now = datetime.now(timezone.utc).isoformat()
    pt = 1 if permitir_trade is True else 0 if permitir_trade is False else None
    with _conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO analyses (
                created_at, stored_filename, trader_json, validator_json, risk_json,
                decisao, score_final, permitir_trade,
                entrada_price, stop_price, tp1_price, tp2_price, tp3_price
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                lvl["entrada"],
                lvl["stop"],
                lvl["tp1"],
                lvl["tp2"],
                lvl["tp3"],
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


def update_outcome(
    analysis_id: int,
    *,
    tp1_hit: bool = False,
    tp2_hit: bool = False,
    tp3_hit: bool = False,
    stop_hit: bool = False,
    candles_checked: int = 0,
    error: "str | None" = None,
) -> bool:
    """Grava resultado da verificação de alvos/stop no banco."""
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as conn:
        row = conn.execute("SELECT id FROM analyses WHERE id = ?", (analysis_id,)).fetchone()
        if not row:
            return False
        conn.execute(
            """
            UPDATE analyses SET
                tp1_hit = ?,
                tp2_hit = ?,
                tp3_hit = ?,
                stop_hit = ?,
                outcome_checked_at = ?
            WHERE id = ?
            """,
            (
                1 if tp1_hit else 0,
                1 if tp2_hit else 0,
                1 if tp3_hit else 0,
                1 if stop_hit else 0,
                now,
                analysis_id,
            ),
        )
    return True


def outcome_summary() -> dict:
    """Contadores globais de TP/Stop para os cards de resumo."""
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT decisao, tp1_hit, tp2_hit, tp3_hit, stop_hit, outcome_checked_at,
                   entrada_price, stop_price, tp1_price, tp2_price, tp3_price
            FROM analyses
            WHERE decisao IS NOT NULL
            """
        ).fetchall()

    total = len(rows)
    verificados = sum(1 for r in rows if r["outcome_checked_at"])
    tp1 = sum(1 for r in rows if r["tp1_hit"])
    tp2 = sum(1 for r in rows if r["tp2_hit"])
    tp3 = sum(1 for r in rows if r["tp3_hit"])
    stop = sum(1 for r in rows if r["stop_hit"])
    com_niveis = sum(
        1 for r in rows if r["tp1_price"] is not None or r["stop_price"] is not None
    )

    return {
        "total_sinais": total,
        "com_niveis_identificados": com_niveis,
        "verificados": verificados,
        "tp1_hit": tp1,
        "tp2_hit": tp2,
        "tp3_hit": tp3,
        "stop_hit": stop,
        "tp1_pct": round(tp1 / verificados * 100) if verificados else 0,
        "tp2_pct": round(tp2 / verificados * 100) if verificados else 0,
        "stop_pct": round(stop / verificados * 100) if verificados else 0,
    }


# ---------------------------------------------------------------------------
# Sinais do Monitor MT5 — persistência e verificação de alvos
# ---------------------------------------------------------------------------

def init_mt5_signals() -> None:
    """Cria tabela mt5_signals (chamada em init_db)."""
    with _conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS mt5_signals (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at      TEXT NOT NULL,
                tv_symbol       TEXT NOT NULL,
                interval        TEXT NOT NULL,
                acao            TEXT NOT NULL,
                score           INTEGER,
                preco           REAL,
                entrada         REAL,
                stop            REAL,
                tp1             REAL,
                tp2             REAL,
                tp3             REAL,
                vwap            REAL,
                tp1_hit         INTEGER NOT NULL DEFAULT 0,
                tp2_hit         INTEGER NOT NULL DEFAULT 0,
                tp3_hit         INTEGER NOT NULL DEFAULT 0,
                stop_hit        INTEGER NOT NULL DEFAULT 0,
                outcome_checked_at TEXT
            )
        """)


def insert_mt5_signal(
    tv_symbol: str, interval: str, acao: str,
    score: "int | None" = None, preco: "float | None" = None,
    entrada: "float | None" = None, stop: "float | None" = None,
    tp1: "float | None" = None, tp2: "float | None" = None,
    tp3: "float | None" = None, vwap: "float | None" = None,
) -> "tuple[int, str]":
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as conn:
        cur = conn.execute("""
            INSERT INTO mt5_signals
              (created_at, tv_symbol, interval, acao, score, preco,
               entrada, stop, tp1, tp2, tp3, vwap)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (now, tv_symbol, interval, acao, score, preco,
              entrada, stop, tp1, tp2, tp3, vwap))
        return int(cur.lastrowid), now


def list_mt5_signals(limit: int = 100) -> "list[dict]":
    with _conn() as conn:
        rows = conn.execute("""
            SELECT * FROM mt5_signals ORDER BY id DESC LIMIT ?
        """, (limit,)).fetchall()
    return [dict(r) for r in rows]


def get_mt5_signal(signal_id: int) -> "dict | None":
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM mt5_signals WHERE id = ?", (signal_id,)
        ).fetchone()
    return dict(row) if row else None


def update_mt5_outcome(
    signal_id: int, *, tp1_hit=False, tp2_hit=False,
    tp3_hit=False, stop_hit=False, **_kw
) -> bool:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as conn:
        if not conn.execute("SELECT id FROM mt5_signals WHERE id=?", (signal_id,)).fetchone():
            return False
        conn.execute("""
            UPDATE mt5_signals SET
                tp1_hit=?, tp2_hit=?, tp3_hit=?, stop_hit=?,
                outcome_checked_at=?
            WHERE id=?
        """, (int(tp1_hit), int(tp2_hit), int(tp3_hit), int(stop_hit), now, signal_id))
    return True


def mt5_signals_stats() -> dict:
    with _conn() as conn:
        rows = conn.execute("SELECT * FROM mt5_signals WHERE acao IN ('COMPRA','VENDA')").fetchall()
    total = len(rows)
    verif = sum(1 for r in rows if r["outcome_checked_at"])
    tp1   = sum(1 for r in rows if r["tp1_hit"])
    tp2   = sum(1 for r in rows if r["tp2_hit"])
    tp3   = sum(1 for r in rows if r["tp3_hit"])
    stop  = sum(1 for r in rows if r["stop_hit"])
    return {
        "total": total, "verificados": verif,
        "tp1_hit": tp1, "tp2_hit": tp2, "tp3_hit": tp3, "stop_hit": stop,
        "tp1_pct":  round(tp1  / verif * 100) if verif else 0,
        "tp2_pct":  round(tp2  / verif * 100) if verif else 0,
        "tp3_pct":  round(tp3  / verif * 100) if verif else 0,
        "stop_pct": round(stop / verif * 100) if verif else 0,
    }


# ---------------------------------------------------------------------------
# Auto-Trades — historico de trades executados pelo Monitor MT5 automaticamente
# ---------------------------------------------------------------------------

def init_auto_trades() -> None:
    """Cria tabela auto_trades (chamada no startup do app)."""
    with _conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS auto_trades (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                opened_at       TEXT NOT NULL,
                tv_symbol       TEXT NOT NULL,
                interval        TEXT NOT NULL DEFAULT '15',
                acao            TEXT NOT NULL,
                entry_price     REAL,
                volume          REAL DEFAULT 1.0,
                sl_initial      REAL,
                tp1_initial     REAL,
                score           INTEGER,
                ai_validated    INTEGER NOT NULL DEFAULT 0,
                ai_confidence   INTEGER,
                ai_veredito     TEXT,
                ai_motivo       TEXT,
                mt5_ticket      INTEGER,
                mt5_deal        INTEGER,
                closed_at       TEXT,
                exit_price      REAL,
                close_reason    TEXT,
                pnl_pts         REAL,
                pnl_brl         REAL
            )
        """)
        rows = conn.execute("PRAGMA table_info(auto_trades)").fetchall()
        have = {r[1] for r in rows}
        for col, sql in [
            ("ai_veredito", "ALTER TABLE auto_trades ADD COLUMN ai_veredito TEXT"),
            ("ai_motivo",   "ALTER TABLE auto_trades ADD COLUMN ai_motivo TEXT"),
            ("mt5_deal",    "ALTER TABLE auto_trades ADD COLUMN mt5_deal INTEGER"),
        ]:
            if col not in have:
                try:
                    conn.execute(sql)
                except Exception:
                    pass


# ---------------------------------------------------------------------------
# Auto-Trades — historico de trades executados pelo Monitor MT5 automaticamente
# ---------------------------------------------------------------------------

def init_auto_trades() -> None:
    """Cria tabela auto_trades (chamada no startup do app)."""
    with _conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS auto_trades (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                opened_at       TEXT NOT NULL,
                tv_symbol       TEXT NOT NULL,
                interval        TEXT NOT NULL DEFAULT '15',
                acao            TEXT NOT NULL,
                entry_price     REAL,
                volume          REAL DEFAULT 1.0,
                sl_initial      REAL,
                tp1_initial     REAL,
                score           INTEGER,
                ai_validated    INTEGER NOT NULL DEFAULT 0,
                ai_confidence   INTEGER,
                ai_veredito     TEXT,
                ai_motivo       TEXT,
                mt5_ticket      INTEGER,
                mt5_deal        INTEGER,
                closed_at       TEXT,
                exit_price      REAL,
                close_reason    TEXT,
                pnl_pts         REAL,
                pnl_brl         REAL
            )
        """)
        rows = conn.execute("PRAGMA table_info(auto_trades)").fetchall()
        have = {r[1] for r in rows}
        for col, sql in [
            ("ai_veredito", "ALTER TABLE auto_trades ADD COLUMN ai_veredito TEXT"),
            ("ai_motivo",   "ALTER TABLE auto_trades ADD COLUMN ai_motivo TEXT"),
            ("mt5_deal",    "ALTER TABLE auto_trades ADD COLUMN mt5_deal INTEGER"),
        ]:
            if col not in have:
                try:
                    conn.execute(sql)
                except Exception:
                    pass
