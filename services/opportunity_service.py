"""
services/opportunity_service.py
---------------------------------
Registra cada oportunidade de sinal encontrada pelo Trade AI,
independente de ter sido operada ou nao.

Tabela: trade_opportunities (no mesmo SQLite data/trade_ai.db)

REGRA: Este modulo NUNCA altera modulos existentes.
"""
import json
import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

_BRT = timezone(timedelta(hours=-3))


def _conn():
    from services.db import _conn as _db_conn
    return _db_conn()


def _parse_dt(ts_str):
    """
    Converte string ISO (com ou sem timezone) para datetime aware em UTC.
    Necessario porque SQLite nao entende offsets de timezone nas comparacoes.
    """
    if not ts_str:
        return None
    try:
        dt = datetime.fromisoformat(str(ts_str))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_BRT)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Inicializacao da tabela
# ---------------------------------------------------------------------------

def init_trade_opportunities():
    """Cria a tabela trade_opportunities se nao existir. Chamado no startup."""
    with _conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trade_opportunities (
                id                       INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at               TEXT NOT NULL,
                tv_symbol                TEXT NOT NULL,
                symbol_short             TEXT,
                interval                 TEXT DEFAULT '15',
                action                   TEXT,
                score                    INTEGER,
                score_raw                INTEGER,
                signal_strength          INTEGER,
                risk_level               INTEGER,
                market_regime            TEXT,
                score_breakdown          TEXT,
                confluences              TEXT,
                entry_price              REAL,
                sl                       REAL,
                tp1                      REAL,
                atr                      REAL,
                adx                      REAL,
                rsi                      REAL,
                htf_trend                TEXT,
                adx_filtered             INTEGER DEFAULT 0,
                directional_block        TEXT,
                ai_verdict               TEXT,
                ai_confidence            INTEGER,
                linked_trade_id          INTEGER,
                was_traded               INTEGER DEFAULT 0,
                outcome_pnl_pts          REAL,
                outcome_pnl_brl          REAL,
                outcome_win              INTEGER,
                outcome_close_reason     TEXT,
                outcome_duration_candles INTEGER,
                resolved_at              TEXT,
                volume_confirm           INTEGER DEFAULT 0,
                volume_ratio             REAL,
                confluence_count         INTEGER,
                ai_trader_confidence     INTEGER,
                ai_validator_confidence  INTEGER
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_opp_symbol
            ON trade_opportunities(tv_symbol, created_at)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_opp_score
            ON trade_opportunities(score, outcome_win)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_opp_regime
            ON trade_opportunities(market_regime, outcome_win)
        """)
    _ensure_columns()


def _ensure_columns():
    """Adiciona colunas novas em bases existentes (migracao sem perda de dados)."""
    _NEW_COLS = [
        ("volume_confirm",          "INTEGER DEFAULT 0"),
        ("volume_ratio",            "REAL"),
        ("confluence_count",        "INTEGER"),
        ("ai_trader_confidence",    "INTEGER"),
        ("ai_validator_confidence", "INTEGER"),
    ]
    try:
        with _conn() as conn:
            existing = {row[1] for row in conn.execute(
                "PRAGMA table_info(trade_opportunities)"
            ).fetchall()}
            for col_name, col_type in _NEW_COLS:
                if col_name not in existing:
                    conn.execute(
                        f"ALTER TABLE trade_opportunities ADD COLUMN {col_name} {col_type}"
                    )
    except Exception as exc:
        logger.warning("_ensure_columns: %s", exc)


# ---------------------------------------------------------------------------
# Gravar oportunidade
# ---------------------------------------------------------------------------

def log_opportunity(
    tv_symbol, symbol_short, interval, action,
    score, score_raw, signal_strength, risk_level, market_regime,
    score_breakdown, confluences, entry_price, sl, tp1,
    atr, adx, rsi, htf_trend, adx_filtered, directional_block,
    ai_verdict=None, ai_confidence=None,
    volume_confirm=None, volume_ratio=None,
    ai_trader_confidence=None, ai_validator_confidence=None,
):
    """
    Insere uma nova oportunidade no banco.
    Retorna o ID inserido, ou None em caso de erro.
    Deduplicacao: mesmo simbolo + action + score nos ultimos 5 min.
    """
    try:
        now = datetime.now(_BRT).isoformat()

        # Deriva volume e contagem das confluencias se nao fornecidos
        confs_list = confluences or []
        if volume_confirm is None:
            vol_txt = " ".join(str(c).lower() for c in confs_list)
            volume_confirm = 1 if ("volume forte" in vol_txt and "confirmando" in vol_txt) else 0
        confluence_count_val = len(confs_list)

        with _conn() as conn:
            existing = conn.execute("""
                SELECT id FROM trade_opportunities
                WHERE tv_symbol = ?
                  AND action    = ?
                  AND score     = ?
                  AND created_at >= datetime(?, '-5 minutes')
                ORDER BY id DESC LIMIT 1
            """, (tv_symbol, action, score, now)).fetchone()

            if existing:
                return existing[0]

            cur = conn.execute("""
                INSERT INTO trade_opportunities (
                    created_at, tv_symbol, symbol_short, interval,
                    action, score, score_raw, signal_strength, risk_level,
                    market_regime, score_breakdown, confluences,
                    entry_price, sl, tp1, atr, adx, rsi,
                    htf_trend, adx_filtered, directional_block,
                    ai_verdict, ai_confidence,
                    volume_confirm, volume_ratio, confluence_count,
                    ai_trader_confidence, ai_validator_confidence
                ) VALUES (
                    ?, ?, ?, ?,
                    ?, ?, ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?, ?, ?, ?,
                    ?, ?, ?,
                    ?, ?,
                    ?, ?, ?,
                    ?, ?
                )
            """, (
                now, tv_symbol, symbol_short, interval,
                action, score, score_raw, signal_strength, risk_level,
                market_regime,
                json.dumps(score_breakdown or {}),
                json.dumps(confs_list),
                entry_price, sl, tp1, atr, adx, rsi,
                htf_trend, 1 if adx_filtered else 0, directional_block,
                ai_verdict, ai_confidence,
                volume_confirm, volume_ratio, confluence_count_val,
                ai_trader_confidence, ai_validator_confidence,
            ))
            return int(cur.lastrowid)

    except Exception as exc:
        logger.warning("log_opportunity error: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Sincronizar outcomes com auto_trades
# ---------------------------------------------------------------------------

def sync_outcomes():
    """
    Varre auto_trades e vincula ao trade_opportunity correspondente.

    Matching feito em Python para evitar problemas de timezone no SQLite:
      - Mesmo tv_symbol
      - Trade aberto ate 20 min APOS a oportunidade registrada
      - Prioridade: menor diferenca de tempo

    Trata dois cenarios:
      1. Trade fechado  -> preenche outcome completo
      2. Trade aberto   -> marca was_traded=1, outcome pendente
    """
    linked         = 0
    open_marked    = 0
    blocked_marked = 0
    skipped        = 0

    # Razoes que indicam que o trade NAO foi executado de fato.
    # Comparacao feita em lowercase para pegar todas as variantes.
    _SKIP_LOWER = {
        'bloqueado_ia', 'aguardado_ia',
        'ia bloqueou',  'ia_bloqueou',
        'aguardado',
        'dados_corrompidos', 'fantasma_removido', 'orfao_bug_cross_symbol',
    }

    # Janela para trades REAIS: 5 minutos e suficiente (trades automaticos abrem em segundos)
    # Janela para trades SKIP (bloqueados/aguardados): 5 minutos tambem
    REAL_WINDOW_SECONDS = 5 * 60   # 5 min — impede que oportunidades antigas roubem trades futuras
    SKIP_WINDOW_SECONDS = 5 * 60   # 5 min

    # Controle para evitar que uma trade real seja linkada a mais de uma oportunidade
    used_real_trade_ids = set()

    try:
        with _conn() as conn:
            pending = conn.execute("""
                SELECT id, tv_symbol, created_at, action
                FROM trade_opportunities
                WHERE linked_trade_id IS NULL
                  AND action IN ('COMPRA', 'VENDA')
                ORDER BY id
            """).fetchall()

            if not pending:
                return {"linked": 0, "open_marked": 0, "blocked_marked": 0, "skipped": 0}

            symbols = list({opp[1] for opp in pending})
            placeholders = ",".join("?" * len(symbols))
            # Busca TODOS os trades (inclusive bloqueados/aguardados)
            # para podermos classificar corretamente cada oportunidade.
            all_trades = conn.execute("""
                SELECT id, tv_symbol, acao, pnl_pts, pnl_brl,
                       close_reason, closed_at, opened_at, interval
                FROM auto_trades
                WHERE tv_symbol IN ({})
                ORDER BY opened_at
            """.format(placeholders), symbols).fetchall()

            trades_by_symbol = {}
            for t in all_trades:
                sym = t[1]
                if sym not in trades_by_symbol:
                    trades_by_symbol[sym] = []
                trades_by_symbol[sym].append(t)

            now_str = datetime.now(_BRT).isoformat()

            for opp in pending:
                opp_id     = opp[0]
                symbol     = opp[1]
                opp_ts_str = opp[2]

                opp_dt = _parse_dt(opp_ts_str)
                if opp_dt is None:
                    skipped += 1
                    continue

                candidates = trades_by_symbol.get(symbol, [])

                # Separar trades reais (executados) de bloqueados/aguardados.
                # Preferencia sempre para trade real; skip-trade so usado como
                # fallback para registrar o motivo de nao-execucao.
                best_real        = None
                best_real_delta  = None
                best_skip        = None
                best_skip_delta  = None

                for t in candidates:
                    t_id = t[0]
                    t_opened_dt = _parse_dt(t[7])
                    if t_opened_dt is None:
                        continue

                    delta = (t_opened_dt - opp_dt).total_seconds()
                    # Trade deve ter sido aberto APOS a oportunidade (tolerancia de 90s antes)
                    cr_lower = (t[5] or '').lower().strip()
                    is_skip = cr_lower in _SKIP_LOWER

                    if is_skip:
                        # Janela para trades bloqueados/aguardados
                        if delta < -90 or delta > SKIP_WINDOW_SECONDS:
                            continue
                        if best_skip_delta is None or abs(delta) < abs(best_skip_delta):
                            best_skip       = t
                            best_skip_delta = delta
                    else:
                        # Janela para trades reais; nao reusar trade ja vinculado
                        if delta < -90 or delta > REAL_WINDOW_SECONDS:
                            continue
                        if t_id in used_real_trade_ids:
                            continue  # esta trade ja foi linkada a outra oportunidade
                        if best_real_delta is None or abs(delta) < abs(best_real_delta):
                            best_real       = t
                            best_real_delta = delta

                if best_real is None and best_skip is None:
                    skipped += 1
                    continue

                # Sem trade real — registrar como bloqueado/aguardado
                if best_real is None:
                    cr_raw    = (best_skip[5] or '').strip()
                    cr_lower2 = cr_raw.lower()
                    if 'bloqueou' in cr_lower2 or 'bloqueado' in cr_lower2:
                        norm_reason = 'IA_BLOQUEOU'
                    elif 'aguardado' in cr_lower2 or 'aguardar' in cr_lower2:
                        norm_reason = 'AGUARDADO_IA'
                    else:
                        norm_reason = cr_raw.upper().replace(' ', '_')[:40]

                    conn.execute("""
                        UPDATE trade_opportunities SET
                            linked_trade_id      = ?,
                            was_traded           = 0,
                            outcome_close_reason = ?,
                            resolved_at          = ?
                        WHERE id = ?
                    """, (best_skip[0], norm_reason, now_str, opp_id))
                    blocked_marked += 1
                    continue

                best_trade = best_real

                trade_id     = best_trade[0]
                pnl_pts      = best_trade[3]
                pnl_brl      = best_trade[4]
                close_reason = best_trade[5]
                closed_at    = best_trade[6]
                opened_at    = best_trade[7]
                interval_str = best_trade[8]
                interval_min = int(interval_str) if str(interval_str).isdigit() else 15

                is_closed = closed_at is not None
                cr_check  = (close_reason or '').lower().strip()

                if is_closed and cr_check not in _SKIP_LOWER:
                    duration_candles = None
                    try:
                        t_open  = _parse_dt(opened_at)
                        t_close = _parse_dt(closed_at)
                        if t_open and t_close:
                            duration_min     = (t_close - t_open).total_seconds() / 60
                            duration_candles = max(1, round(duration_min / interval_min))
                    except Exception:
                        pass

                    win = None
                    if pnl_pts is not None:
                        win = 1 if pnl_pts > 0 else 0

                    conn.execute("""
                        UPDATE trade_opportunities SET
                            linked_trade_id          = ?,
                            was_traded               = 1,
                            outcome_pnl_pts          = ?,
                            outcome_pnl_brl          = ?,
                            outcome_win              = ?,
                            outcome_close_reason     = ?,
                            outcome_duration_candles = ?,
                            resolved_at              = ?
                        WHERE id = ?
                    """, (
                        trade_id, pnl_pts, pnl_brl, win,
                        close_reason, duration_candles, now_str, opp_id,
                    ))
                    used_real_trade_ids.add(trade_id)
                    linked += 1

                else:
                    # Trade ainda aberto - marca was_traded sem outcome
                    conn.execute("""
                        UPDATE trade_opportunities SET
                            linked_trade_id = ?,
                            was_traded      = 1
                        WHERE id = ?
                    """, (trade_id, opp_id))
                    used_real_trade_ids.add(trade_id)
                    open_marked += 1

    except Exception as exc:
        logger.warning("sync_outcomes error: %s", exc)

    return {
        "linked":         linked,
        "open_marked":    open_marked,
        "blocked_marked": blocked_marked,
        "skipped":        skipped,
    }


# ---------------------------------------------------------------------------
# Analytics Queries
# ---------------------------------------------------------------------------

def analytics_score_performance():
    """Win rate por score para oportunidades que foram operadas."""
    try:
        with _conn() as conn:
            rows = conn.execute("""
                SELECT
                    score,
                    COUNT(*)                                          AS total,
                    SUM(CASE WHEN outcome_win = 1 THEN 1 ELSE 0 END) AS wins,
                    SUM(CASE WHEN outcome_win = 0 THEN 1 ELSE 0 END) AS losses,
                    ROUND(AVG(CASE WHEN outcome_win IS NOT NULL
                              THEN outcome_pnl_pts END), 1)           AS avg_pnl_pts,
                    ROUND(AVG(CASE WHEN outcome_win IS NOT NULL
                              THEN outcome_pnl_brl END), 2)           AS avg_pnl_brl
                FROM trade_opportunities
                WHERE was_traded = 1
                  AND outcome_win IS NOT NULL
                  AND score IS NOT NULL
                  AND action IN ('COMPRA', 'VENDA')
                GROUP BY score
                ORDER BY score DESC
            """).fetchall()

        result = []
        for r in rows:
            total = r[1] or 0
            wins  = r[2] or 0
            result.append({
                "score":        r[0],
                "total":        total,
                "wins":         wins,
                "losses":       r[3] or 0,
                "win_rate":     round(wins / total * 100) if total else 0,
                "avg_pnl_pts":  r[4],
                "avg_pnl_brl":  r[5],
            })
        return result
    except Exception as exc:
        logger.warning("analytics_score_performance: %s", exc)
        return []


def analytics_regime_performance():
    """Win rate por market_regime."""
    try:
        with _conn() as conn:
            rows = conn.execute("""
                SELECT
                    market_regime,
                    COUNT(*)                                          AS total,
                    SUM(CASE WHEN outcome_win = 1 THEN 1 ELSE 0 END) AS wins,
                    SUM(CASE WHEN outcome_win = 0 THEN 1 ELSE 0 END) AS losses,
                    ROUND(AVG(CASE WHEN outcome_win IS NOT NULL
                              THEN outcome_pnl_pts END), 1)           AS avg_pnl_pts
                FROM trade_opportunities
                WHERE was_traded = 1
                  AND outcome_win IS NOT NULL
                  AND market_regime IS NOT NULL
                GROUP BY market_regime
                ORDER BY wins * 1.0 / (total + 0.001) DESC
            """).fetchall()

        result = []
        for r in rows:
            total = r[1] or 0
            wins  = r[2] or 0
            result.append({
                "regime":       r[0],
                "total":        total,
                "wins":         wins,
                "losses":       r[3] or 0,
                "win_rate":     round(wins / total * 100) if total else 0,
                "avg_pnl_pts":  r[4],
            })
        return result
    except Exception as exc:
        logger.warning("analytics_regime_performance: %s", exc)
        return []


def analytics_confluence_performance():
    """Win rate por confluencia individual (top 20, min 3 ocorrencias)."""
    try:
        with _conn() as conn:
            rows = conn.execute("""
                SELECT confluences, outcome_win, outcome_pnl_pts
                FROM trade_opportunities
                WHERE was_traded = 1
                  AND outcome_win IS NOT NULL
                  AND confluences IS NOT NULL
                  AND confluences != '[]'
            """).fetchall()

        counts = {}
        for r in rows:
            try:
                confs = json.loads(r[0]) if r[0] else []
            except Exception:
                confs = []
            win     = r[1]
            pnl_pts = r[2] or 0
            for c in confs:
                key = c[:60].strip() if c else ""
                if not key:
                    continue
                if key not in counts:
                    counts[key] = {"total": 0, "wins": 0, "pnl_sum": 0.0}
                counts[key]["total"]   += 1
                counts[key]["wins"]    += 1 if win == 1 else 0
                counts[key]["pnl_sum"] += pnl_pts

        result = []
        for conf, d in counts.items():
            total = d["total"]
            wins  = d["wins"]
            result.append({
                "confluence": conf,
                "total":      total,
                "wins":       wins,
                "losses":     total - wins,
                "win_rate":   round(wins / total * 100) if total else 0,
                "avg_pnl":    round(d["pnl_sum"] / total, 1) if total else 0,
            })

        result = [r for r in result if r["total"] >= 3]
        result.sort(key=lambda x: x["total"], reverse=True)
        return result[:20]

    except Exception as exc:
        logger.warning("analytics_confluence_performance: %s", exc)
        return []


def analytics_signal_strength_performance():
    """Win rate por faixa de signal_strength (buckets de 10)."""
    try:
        with _conn() as conn:
            rows = conn.execute("""
                SELECT
                    (signal_strength / 10) * 10  AS bucket,
                    COUNT(*)                     AS total,
                    SUM(CASE WHEN outcome_win = 1 THEN 1 ELSE 0 END) AS wins,
                    ROUND(AVG(outcome_pnl_pts), 1) AS avg_pnl_pts
                FROM trade_opportunities
                WHERE was_traded = 1
                  AND outcome_win IS NOT NULL
                  AND signal_strength IS NOT NULL
                GROUP BY bucket
                ORDER BY bucket DESC
            """).fetchall()

        result = []
        for r in rows:
            total  = r[1] or 0
            wins   = r[2] or 0
            bucket = r[0] or 0
            result.append({
                "range":       "{}-{}".format(bucket, bucket + 9),
                "bucket_min":  bucket,
                "total":       total,
                "wins":        wins,
                "losses":      total - wins,
                "win_rate":    round(wins / total * 100) if total else 0,
                "avg_pnl_pts": r[3],
            })
        return result
    except Exception as exc:
        logger.warning("analytics_signal_strength_performance: %s", exc)
        return []



def analytics_recent_opportunities(limit=50, date_filter=None):
    """
    Lista as oportunidades mais recentes com resultado se disponivel.
    date_filter: 'today' | 'week' | None (tudo)
    """
    try:
        # Filtro de data em BRT
        where_date = ""
        if date_filter == "today":
            # substr(created_at,1,10) pega a data BRT diretamente do isoformat
            # Evita que o SQLite normalize para UTC o offset -03:00
            # Ex: '2026-06-23T23:10:00-03:00' -> substr = '2026-06-23' (correto)
            #     date(..., '+0 hours') -> '2026-06-24' (errado — converte para UTC)
            where_date = "AND substr(created_at, 1, 10) = date('now', '-3 hours')"
        elif date_filter == "week":
            where_date = "AND substr(created_at, 1, 10) >= date('now', '-3 hours', '-7 days')"

        with _conn() as conn:
            rows = conn.execute(f"""
                SELECT
                    id, created_at, symbol_short, action, score,
                    signal_strength, risk_level, market_regime,
                    was_traded, outcome_win, outcome_pnl_pts, outcome_pnl_brl,
                    outcome_close_reason, ai_verdict, confluences,
                    resolved_at,
                    adx, rsi, volume_confirm, confluence_count,
                    ai_confidence, ai_trader_confidence, ai_validator_confidence
                FROM trade_opportunities
                WHERE action IN ('COMPRA', 'VENDA')
                {where_date}
                ORDER BY id DESC
                LIMIT ?
            """, (limit,)).fetchall()

        result = []
        for r in rows:
            try:
                confs = json.loads(r[14]) if r[14] else []
                confs_short = confs[:3]
            except Exception:
                confs_short = []

            result.append({
                "id":                      r[0],
                "created_at":              r[1],
                "symbol":                  r[2],
                "action":                  r[3],
                "score":                   r[4],
                "signal_strength":         r[5],
                "risk_level":              r[6],
                "market_regime":           r[7],
                "was_traded":              bool(r[8]),
                "outcome_win":             r[9],
                "pnl_pts":                 r[10],
                "pnl_brl":                 r[11],
                "close_reason":            r[12],
                "ai_verdict":              r[13],
                "confluences":             confs_short,
                "resolved":                r[15] is not None,
                "adx":                     r[16],
                "rsi":                     r[17],
                "volume_confirm":          bool(r[18]) if r[18] is not None else None,
                "confluence_count":        r[19],
                "ai_confidence":           r[20],
                "ai_trader_confidence":    r[21],
                "ai_validator_confidence": r[22],
            })
        return result
    except Exception as exc:
        logger.warning("analytics_recent_opportunities: %s", exc)
        return []


def analytics_summary(date_filter=None):
    """Resumo geral para o header do analytics dashboard.
    date_filter: 'today' | 'week' | None (tudo)
    """
    where_date = ""
    if date_filter == "today":
        where_date = "AND substr(created_at, 1, 10) = date('now', '-3 hours')"
    elif date_filter == "week":
        where_date = "AND substr(created_at, 1, 10) >= date('now', '-3 hours', '-7 days')"

    try:
        with _conn() as conn:
            row = conn.execute(f"""
                SELECT
                    COUNT(*)                                          AS total,
                    SUM(CASE WHEN was_traded = 1 THEN 1 ELSE 0 END)  AS traded,
                    SUM(CASE WHEN outcome_win = 1 THEN 1 ELSE 0 END) AS wins,
                    SUM(CASE WHEN outcome_win = 0 THEN 1 ELSE 0 END) AS losses,
                    ROUND(SUM(COALESCE(outcome_pnl_brl, 0)), 2)      AS total_pnl_brl,
                    ROUND(SUM(COALESCE(outcome_pnl_pts, 0)), 0)      AS total_pnl_pts,
                    COUNT(CASE WHEN resolved_at IS NULL AND was_traded = 0
                               AND outcome_close_reason IS NULL
                               THEN 1 END)                            AS pending,
                    SUM(CASE WHEN outcome_close_reason = 'IA_BLOQUEOU'
                               THEN 1 ELSE 0 END)                     AS blocked_ia,
                    SUM(CASE WHEN outcome_close_reason = 'AGUARDADO_IA'
                               THEN 1 ELSE 0 END)                     AS waiting_ia
                FROM trade_opportunities
                WHERE action IN ('COMPRA', 'VENDA')
                {where_date}
            """).fetchone()

        total  = row[0] or 0
        traded = row[1] or 0
        wins   = row[2] or 0
        losses = row[3] or 0
        closed = wins + losses

        return {
            "total_opportunities": total,
            "traded":              traded,
            "wins":                wins,
            "losses":              losses,
            "pending":             row[6] or 0,
            "blocked_ia":          row[7] or 0,
            "waiting_ia":          row[8] or 0,
            "total_pnl_brl":       round(row[4] or 0, 2),
            "total_pnl_pts":       int(row[5] or 0),
            "win_rate":            round(wins / closed * 100) if closed else None,
            "closed":              closed,
        }
    except Exception as exc:
        logger.warning("analytics_summary: %s", exc)
        return {}


def analytics_confluence_performance():
    """Win rate por numero de confluencias."""
    try:
        with _conn() as conn:
            rows = conn.execute("""
                SELECT
                    confluence_count,
                    COUNT(*)                                          AS total,
                    SUM(CASE WHEN outcome_win = 1 THEN 1 ELSE 0 END) AS wins,
                    ROUND(AVG(outcome_pnl_pts), 1)                   AS avg_pnl
                FROM trade_opportunities
                WHERE was_traded = 1
                  AND outcome_win IS NOT NULL
                  AND confluence_count IS NOT NULL
                  AND action IN ('COMPRA', 'VENDA')
                GROUP BY confluence_count
                ORDER BY confluence_count DESC
            """).fetchall()

        result = []
        for r in rows:
            total = r[1] or 0
            wins  = r[2] or 0
            result.append({
                "confluence_count": r[0],
                "total":            total,
                "wins":             wins,
                "losses":           total - wins,
                "win_rate":         round(wins / total * 100) if total else 0,
                "avg_pnl_pts":      r[3],
            })
        return result
    except Exception as exc:
        logger.warning("analytics_confluence_performance: %s", exc)
        return []


def analytics_signal_strength_performance():
    """Win rate por faixa de signal_strength (buckets de 10)."""
    try:
        with _conn() as conn:
            rows = conn.execute("""
                SELECT
                    (signal_strength / 10) * 10                        AS bucket,
                    COUNT(*)                                           AS total,
                    SUM(CASE WHEN outcome_win = 1 THEN 1 ELSE 0 END)  AS wins,
                    ROUND(AVG(outcome_pnl_pts), 1)                    AS avg_pnl
                FROM trade_opportunities
                WHERE was_traded = 1
                  AND outcome_win IS NOT NULL
                  AND signal_strength IS NOT NULL
                  AND action IN ('COMPRA', 'VENDA')
                GROUP BY bucket
                ORDER BY bucket DESC
            """).fetchall()

        result = []
        for r in rows:
            total  = r[1] or 0
            wins   = r[2] or 0
            bucket = r[0] or 0
            result.append({
                "range":    f"{bucket}-{bucket + 9}",
                "bucket":   bucket,
                "total":    total,
                "wins":     wins,
                "win_rate": round(wins / total * 100, 1) if total else 0,
            })

        return result
    except Exception:
        return []
