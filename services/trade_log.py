"""
trade_log.py — Rastreamento completo de trades automáticos do Monitor MT5.

Ciclo de vida de um trade:
  1. save_auto_trade()   — chamado no momento da execução (POST /api/autotrade/execute)
  2. close_auto_trade()  — chamado quando a posicao e detectada como fechada
                           (em GET /api/autotrade/manage ao perceber que nao ha mais posicao)

A tabela auto_trades fica no mesmo SQLite do projeto (data/trade_ai.db).
"""
import logging
from datetime import datetime, timezone, timedelta

# Fuso horário BRT (UTC-3) — timestamps salvos no horário local de Brasília
_BRT = timezone(timedelta(hours=-3))

logger = logging.getLogger(__name__)


def _conn():
    from services.db import _conn as _db_conn
    return _db_conn()


# ---------------------------------------------------------------------------
# Inicializacao da tabela
# ---------------------------------------------------------------------------

def init_auto_trades() -> None:
    """Cria a tabela auto_trades se nao existir (chama no startup do app)."""
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
        # Migracoes para instancias existentes
        _migrate_auto_trades(conn)


def _migrate_auto_trades(conn) -> None:
    rows = conn.execute("PRAGMA table_info(auto_trades)").fetchall()
    have = {r[1] for r in rows}
    extras = [
        ("ai_veredito",   "ALTER TABLE auto_trades ADD COLUMN ai_veredito TEXT"),
        ("ai_motivo",     "ALTER TABLE auto_trades ADD COLUMN ai_motivo TEXT"),
        ("mt5_deal",      "ALTER TABLE auto_trades ADD COLUMN mt5_deal INTEGER"),
        ("volume",        "ALTER TABLE auto_trades ADD COLUMN volume REAL DEFAULT 1.0"),
        ("interval",      "ALTER TABLE auto_trades ADD COLUMN interval TEXT DEFAULT '15'"),
    ]
    for col, sql in extras:
        if col not in have:
            try:
                conn.execute(sql)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def save_auto_trade(
    tv_symbol: str,
    interval: str,
    acao: str,
    entry_price: "float | None" = None,
    volume: float = 1.0,
    sl_initial: "float | None" = None,
    tp1_initial: "float | None" = None,
    score: "int | None" = None,
    mt5_ticket: "int | None" = None,
    mt5_deal: "int | None" = None,
    ai_validated: bool = False,
    ai_confidence: "int | None" = None,
    ai_veredito: "str | None" = None,
    ai_motivo: "str | None" = None,
) -> int:
    """
    Salva um novo trade automatico.
    Retorna o ID do registro criado.
    """
    now = datetime.now(_BRT).isoformat()
    with _conn() as conn:
        cur = conn.execute("""
            INSERT INTO auto_trades
              (opened_at, tv_symbol, interval, acao, entry_price, volume,
               sl_initial, tp1_initial, score, mt5_ticket, mt5_deal,
               ai_validated, ai_confidence, ai_veredito, ai_motivo)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            now, tv_symbol, interval, acao, entry_price, volume,
            sl_initial, tp1_initial, score, mt5_ticket, mt5_deal,
            1 if ai_validated else 0, ai_confidence, ai_veredito, ai_motivo,
        ))
        trade_id = int(cur.lastrowid)
    logger.info("Trade salvo no log: id=%d %s %s @ %.0f", trade_id, acao, tv_symbol, entry_price or 0)
    return trade_id


def close_auto_trade(
    trade_id: int,
    exit_price: float,
    close_reason: str,
    pnl_pts: "float | None" = None,
    pnl_brl: "float | None" = None,
) -> bool:
    """
    Marca um trade como fechado com resultado.

    close_reason: 'TP1' | 'TP2' | 'TP3' | 'STOP' | 'MANUAL' | 'TRAILING' |
                  'REVERSAO' | 'TEMPO' | 'BREAKEVEN'
    """
    now = datetime.now(_BRT).isoformat()
    with _conn() as conn:
        row = conn.execute("SELECT id FROM auto_trades WHERE id=?", (trade_id,)).fetchone()
        if not row:
            return False
        conn.execute("""
            UPDATE auto_trades SET
                closed_at=?, exit_price=?, close_reason=?, pnl_pts=?, pnl_brl=?
            WHERE id=?
        """, (now, exit_price, close_reason, pnl_pts, pnl_brl, trade_id))
    logger.info(
        "Trade fechado: id=%d motivo=%s exit=%.0f pnl_pts=%s pnl_brl=%s",
        trade_id, close_reason, exit_price, pnl_pts, pnl_brl,
    )
    return True


def backfill_missing_pnl(max_rows: int = 30) -> int:
    """Preenche o P&L de trades JÁ FECHADOS que ficaram SEM resultado capturado
    (exit_price 0/nulo ou pnl_brl nulo), buscando o realizado no histórico do MT5.

    ⚠️ Só corrige a CAPTURA do resultado (exit_price/pnl) — NÃO altera closed_at,
    close_reason, nem qualquer decisão de trade. Roda na instância do B3 (XP)."""
    try:
        import MetaTrader5 as mt5
    except Exception:
        return 0
    with _conn() as conn:
        rows = conn.execute("""
            SELECT id, mt5_ticket, entry_price, acao, tv_symbol
            FROM auto_trades
            WHERE closed_at IS NOT NULL
              AND mt5_ticket IS NOT NULL
              AND (pnl_brl IS NULL OR exit_price IS NULL OR exit_price = 0)
            ORDER BY id DESC LIMIT ?
        """, (max_rows,)).fetchall()
        rows = [dict(r) for r in rows]
    if not rows:
        return 0
    if not mt5.initialize():
        return 0
    fixed = 0
    try:
        for r in rows:
            tk = r.get("mt5_ticket")
            if not tk:
                continue
            try:
                deals = mt5.history_deals_get(position=int(tk)) or []
            except Exception:
                deals = []
            outs = [d for d in deals if getattr(d, "entry", None) == mt5.DEAL_ENTRY_OUT]
            if not outs:
                continue
            outs.sort(key=lambda d: d.time)
            profit = sum(float(d.profit) + float(getattr(d, "swap", 0) or 0)
                         + float(getattr(d, "commission", 0) or 0) for d in outs)
            price = float(getattr(outs[-1], "price", 0) or 0)
            if not price:
                continue
            entry = r.get("entry_price"); acao = r.get("acao")
            pts = None
            if entry:
                diff = (price - float(entry)) if acao == "COMPRA" else (float(entry) - price)
                sym = (r.get("tv_symbol") or "").upper()
                pts = round(diff) if ("WIN" in sym or "WDO" in sym) else None
            with _conn() as conn:
                conn.execute(
                    "UPDATE auto_trades SET exit_price=?, pnl_pts=?, pnl_brl=? WHERE id=?",
                    (price, pts, round(profit, 2), r["id"]))
            fixed += 1
    finally:
        try: mt5.shutdown()
        except Exception: pass
    if fixed:
        logger.info("Backfill P&L do B3: %d trade(s) corrigidos do histórico MT5.", fixed)
    return fixed


def get_open_auto_trade(tv_symbol: str) -> "dict | None":
    """
    Retorna o trade automatico aberto mais recente para o simbolo.
    Retorna None se nao houver trade aberto.
    """
    with _conn() as conn:
        row = conn.execute("""
            SELECT * FROM auto_trades
            WHERE tv_symbol=? AND closed_at IS NULL
            ORDER BY id DESC LIMIT 1
        """, (tv_symbol,)).fetchone()
    return dict(row) if row else None


def list_auto_trades(limit: int = 50, today_only: bool = False) -> "list[dict]":
    """Lista trades automaticos mais recentes (abertos e fechados).

    today_only=True  -> retorna apenas trades do dia corrente (por created_at).
    today_only=False -> retorna os N mais recentes independente de data.
    """
    with _conn() as conn:
        if today_only:
            rows = conn.execute("""
                SELECT * FROM auto_trades
                WHERE date(opened_at) = date('now', 'localtime')
                ORDER BY id DESC LIMIT ?
            """, (limit,)).fetchall()
        else:
            rows = conn.execute("""
                SELECT * FROM auto_trades ORDER BY id DESC LIMIT ?
            """, (limit,)).fetchall()
    return [dict(r) for r in rows]


def get_auto_trade(trade_id: int) -> "dict | None":
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM auto_trades WHERE id=?", (trade_id,)
        ).fetchone()
    return dict(row) if row else None


def auto_trades_stats(today_only: bool = False) -> dict:
    """Resumo estatistico dos trades automaticos (filtrado por dia ou total)."""
    with _conn() as conn:
        if today_only:
            rows = conn.execute("""
                SELECT * FROM auto_trades
                WHERE date(opened_at) = date('now', 'localtime')
            """).fetchall()
        else:
            rows = conn.execute("SELECT * FROM auto_trades").fetchall()
    rows = [dict(r) for r in rows]

    # Separa sinais não-executados dos demais (bloqueados/aguardados não têm P&L real)
    _nao_executados = {"BLOQUEADO_IA", "AGUARDADO_IA"}
    bloqueados = [r for r in rows if r.get("close_reason") in _nao_executados]
    reais      = [r for r in rows if r.get("close_reason") not in _nao_executados]

    total    = len(rows)
    abertos  = sum(1 for r in reais if not r.get("closed_at"))
    fechados = len(reais) - abertos

    gains = [r for r in reais if r.get("pnl_pts") is not None and r["pnl_pts"] > 0]
    losses= [r for r in reais if r.get("pnl_pts") is not None and r["pnl_pts"] < 0]
    pnl_total_pts = sum(r["pnl_pts"] or 0 for r in reais)
    pnl_total_brl = sum(r["pnl_brl"] or 0 for r in reais)

    by_reason = {}
    for r in rows:
        reason = r.get("close_reason") or "aberto"
        by_reason[reason] = by_reason.get(reason, 0) + 1

    return {
        "total":         total,
        "abertos":       abertos,
        "fechados":      fechados,
        "bloqueados_ia": len(bloqueados),   # inclui BLOQUEADO_IA + AGUARDADO_IA
        "wins":          len(gains),
        "losses":        len(losses),
        "win_rate_pct":  round(len(gains) / fechados * 100) if fechados else 0,
        "pnl_total_pts": round(pnl_total_pts),
        "pnl_total_brl": round(pnl_total_brl, 2),
        "por_motivo":    by_reason,
    }
