"""
services/ai_supervisor.py
--------------------------
Supervisor de decisoes da IA: rastreia e avalia cada veredicto
(EXECUTAR / AGUARDAR / BLOQUEAR) contra o resultado real do mercado.

Fonte de dados principal: auto_trades (Monitor MT5)
  - Contem trades realmente executados com ai_veredito, ai_confidence, pnl_pts, pnl_brl
  - Filtravel por closed_at para analise de periodos

Terminologia:
  Verdadeiro Positivo  (TP) — IA disse EXECUTAR  → trade ganhou   (pnl_pts > 0)
  Falso Positivo       (FP) — IA disse EXECUTAR  → trade perdeu   (pnl_pts <= 0)
  Falso Negativo       (FN) — estimado via trade_opportunities quando disponivel
                              (bloqueou mas score alto — provavelmente seria ganho)

REGRA: apenas LE dados existentes. Nunca modifica modulos existentes.
"""
import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

_BRT = timezone(timedelta(hours=-3))
_MIN_SAMPLES = 5

# Periodo padrao para analise historica
_DEFAULT_PERIOD = "month"


def _conn():
    from services.db import _conn as _db
    return _db()


def _period_since(period: str) -> str:
    """Converte nome de periodo em data YYYY-MM-DD para filtro SQL via substr().
    Retorna apenas a data para evitar o bug de normalizacao UTC do SQLite com DATE().
    """
    _BRT_now = datetime.now(_BRT)
    if period == "today":
        return _BRT_now.strftime("%Y-%m-%d")
    elif period == "week":
        start_of_week = _BRT_now - timedelta(days=_BRT_now.weekday())
        return start_of_week.strftime("%Y-%m-%d")
    elif period == "month":
        return _BRT_now.strftime("%Y-%m-01")
    return "2000-01-01"


# ---------------------------------------------------------------------------
# Analise de desempenho da IA por veredicto
# ---------------------------------------------------------------------------

def analyze_ai_performance(period: str = _DEFAULT_PERIOD) -> dict:
    """
    Analisa como cada veredicto da IA se saiu nos resultados reais.

    Fonte: auto_trades — trades realmente executados pelo Monitor MT5.
    Para o FN estimado, tenta tambem trade_opportunities (pode estar vazia).

    period: 'today' | 'week' | 'month' | 'all'
    """
    since = _period_since(period)

    try:
        with _conn() as conn:
            # 1. Todos os trades fechados com AI verdict no periodo
            exec_rows = conn.execute("""
                SELECT
                    ai_veredito,
                    ai_confidence,
                    CASE WHEN pnl_pts > 0 THEN 1 ELSE 0 END  AS outcome_win,
                    pnl_pts,
                    pnl_brl,
                    score,
                    tv_symbol,
                    closed_at
                FROM auto_trades
                WHERE closed_at IS NOT NULL
                  AND pnl_brl IS NOT NULL
                  AND ai_veredito IS NOT NULL
                  AND substr(closed_at, 1, 10) >= ?
                ORDER BY closed_at DESC
            """, (since,)).fetchall()

            # 2. FN estimado via trade_opportunities (quando disponivel)
            try:
                block_rows = conn.execute("""
                    SELECT
                        ai_verdict,
                        ai_confidence,
                        score,
                        market_regime,
                        created_at
                    FROM trade_opportunities
                    WHERE was_traded = 0
                      AND ai_verdict IS NOT NULL
                      AND ai_verdict IN ('BLOQUEAR', 'AGUARDAR', 'AGUARDAR_IA', 'BLOQUEAR_IA')
                      AND created_at >= ?
                    ORDER BY created_at DESC
                """, (since,)).fetchall()
            except Exception:
                block_rows = []

        # --- Analise de EXECUTAR ---
        exec_stats = {
            "total":    0,
            "wins":     0,
            "losses":   0,
            "win_rate": 0,
            "avg_pnl":  0.0,
            "avg_brl":  0.0,
            "by_confidence": {},
            "period":   period,
        }

        pnl_sum = 0.0
        brl_sum = 0.0

        for r in exec_rows:
            exec_stats["total"] += 1
            ow  = r[2]   # 1 se ganhou, 0 se perdeu
            pnl = r[3] or 0.0
            brl = r[4] or 0.0

            if ow == 1:
                exec_stats["wins"] += 1
            else:
                exec_stats["losses"] += 1

            pnl_sum += pnl
            brl_sum += brl

            # Por faixa de confianca
            conf = r[1]
            if conf is not None:
                bucket = int(conf // 10) * 10
                key = f"{bucket}-{bucket+9}%"
            else:
                key = "desconhecida"

            if key not in exec_stats["by_confidence"]:
                exec_stats["by_confidence"][key] = {"wins": 0, "total": 0}
            exec_stats["by_confidence"][key]["total"] += 1
            if ow == 1:
                exec_stats["by_confidence"][key]["wins"] += 1

        t = exec_stats["total"]
        if t:
            exec_stats["win_rate"] = round(exec_stats["wins"] / t * 100, 1)
            exec_stats["avg_pnl"]  = round(pnl_sum / t, 1)
            exec_stats["avg_brl"]  = round(brl_sum / t, 2)

        # --- Analise de BLOQUEAR / AGUARDAR (via trade_opportunities se disponivel) ---
        block_stats = {
            "total":         len(block_rows),
            "estimated_fn":  0,
            "estimated_tn":  0,
            "by_verdict":    {},
            "source":        "trade_opportunities" if block_rows else "indisponivel",
        }

        for r in block_rows:
            verdict = (r[0] or "").upper()
            score   = r[2] or 0
            regime  = (r[3] or "").upper()

            if score >= 7 and "RANGING" not in regime and "LATERAL" not in regime:
                block_stats["estimated_fn"] += 1
            else:
                block_stats["estimated_tn"] += 1

            if verdict not in block_stats["by_verdict"]:
                block_stats["by_verdict"][verdict] = {"total": 0, "estimated_fn": 0}
            block_stats["by_verdict"][verdict]["total"] += 1
            if score >= 7:
                block_stats["by_verdict"][verdict]["estimated_fn"] += 1

        # --- Sumario por veredicto ---
        verdict_summary = {}

        total_exec = exec_stats["total"]
        wins_exec  = exec_stats["wins"]
        verdict_summary["EXECUTAR"] = {
            "total":    total_exec,
            "wins":     wins_exec,
            "losses":   total_exec - wins_exec,
            "win_rate": exec_stats["win_rate"],
            "avg_pnl":  exec_stats["avg_pnl"],
            "quality":  _rate_quality(exec_stats["win_rate"], total_exec),
        }

        for v, d in block_stats["by_verdict"].items():
            fn_rate = round(d["estimated_fn"] / d["total"] * 100) if d["total"] else 0
            verdict_summary[v] = {
                "total":            d["total"],
                "estimated_fn":     d["estimated_fn"],
                "fn_rate":          fn_rate,
                "quality":          _rate_block_quality(fn_rate, d["total"]),
            }

        return {
            "executar_stats":  exec_stats,
            "block_stats":     block_stats,
            "verdict_summary": verdict_summary,
            "period":          period,
            "generated_at":    datetime.now(_BRT).isoformat(),
        }

    except Exception as exc:
        logger.warning("ai_supervisor.analyze_ai_performance: %s", exc)
        return {"error": str(exc)}


def _rate_quality(win_rate: float, samples: int) -> str:
    if samples < _MIN_SAMPLES:
        return "aguardando_dados"
    if win_rate >= 65:
        return "excelente"
    if win_rate >= 55:
        return "bom"
    if win_rate >= 45:
        return "regular"
    return "ruim"


def _rate_block_quality(fn_rate: float, samples: int) -> str:
    """
    fn_rate = % de bloqueios que estimamos teriam sido ganhos.
    Se fn_rate < 30% -> a IA esta bloqueando coisas certas (TN alto)
    Se fn_rate > 50% -> a IA esta bloqueando muitas oportunidades boas
    """
    if samples < _MIN_SAMPLES:
        return "aguardando_dados"
    if fn_rate <= 20:
        return "excelente"
    if fn_rate <= 35:
        return "bom"
    if fn_rate <= 50:
        return "regular"
    return "muito_restritivo"


# ---------------------------------------------------------------------------
# Historico de decisoes recentes
# ---------------------------------------------------------------------------

def get_recent_decisions(limit: int = 50, period: str = _DEFAULT_PERIOD) -> list:
    """
    Retorna as ultimas `limit` decisoes da IA com contexto e resultado.
    Fonte: auto_trades (trades executados).
    """
    since = _period_since(period)

    try:
        with _conn() as conn:
            rows = conn.execute("""
                SELECT
                    id,
                    opened_at,
                    tv_symbol,
                    acao,
                    ai_veredito,
                    ai_confidence,
                    score,
                    closed_at,
                    pnl_pts,
                    pnl_brl,
                    close_reason
                FROM auto_trades
                WHERE ai_veredito IS NOT NULL
                  AND substr(closed_at, 1, 10) >= ?
                ORDER BY closed_at DESC
                LIMIT ?
            """, (since, limit)).fetchall()

        result = []
        for r in rows:
            pnl_pts = r[8]
            closed  = r[7]

            if closed and pnl_pts is not None:
                outcome_label = "WIN" if pnl_pts > 0 else "LOSS"
                outcome_win   = 1 if pnl_pts > 0 else 0
            else:
                outcome_label = "PENDENTE"
                outcome_win   = None

            result.append({
                "id":            r[0],
                "created_at":    r[1],
                "symbol":        r[2],
                "action":        r[3],
                "ai_verdict":    r[4],
                "ai_confidence": r[5],
                "score":         r[6],
                "market_regime": None,   # nao disponivel em auto_trades
                "was_traded":    1,      # sempre 1 — auto_trades so tem executados
                "outcome_win":   outcome_win,
                "outcome_pnl":   pnl_pts,
                "outcome_label": outcome_label,
            })
        return result

    except Exception as exc:
        logger.warning("ai_supervisor.get_recent_decisions: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Estatisticas de falso positivo / falso negativo por periodo
# ---------------------------------------------------------------------------

def get_fp_fn_summary(period: str = _DEFAULT_PERIOD) -> dict:
    """
    Resumo de Falsos Positivos e Falsos Negativos estimados.

    FP = IA disse EXECUTAR, trade perdeu          (auto_trades)
    TP = IA disse EXECUTAR, trade ganhou          (auto_trades)
    FN = IA disse BLOQUEAR/AGUARDAR, score >= 7  (trade_opportunities se disponivel)
    """
    since = _period_since(period)

    try:
        with _conn() as conn:
            # TP e FP de auto_trades
            tp = conn.execute("""
                SELECT COUNT(*) FROM auto_trades
                WHERE closed_at IS NOT NULL AND pnl_brl IS NOT NULL
                  AND pnl_pts > 0
                  AND ai_veredito IS NOT NULL
                  AND substr(closed_at, 1, 10) >= ?
            """, (since,)).fetchone()[0] or 0

            fp = conn.execute("""
                SELECT COUNT(*) FROM auto_trades
                WHERE closed_at IS NOT NULL AND pnl_brl IS NOT NULL
                  AND pnl_pts <= 0
                  AND ai_veredito IS NOT NULL
                  AND substr(closed_at, 1, 10) >= ?
            """, (since,)).fetchone()[0] or 0

            # FN estimado de trade_opportunities (pode estar vazio)
            fn_est = 0
            total_blocked = 0
            try:
                fn_est = conn.execute("""
                    SELECT COUNT(*) FROM trade_opportunities
                    WHERE was_traded = 0
                      AND ai_verdict IN ('BLOQUEAR', 'AGUARDAR', 'AGUARDAR_IA', 'BLOQUEAR_IA')
                      AND score >= 7
                      AND (market_regime NOT LIKE '%RANGING%'
                           AND market_regime NOT LIKE '%LATERAL%')
                      AND created_at >= ?
                """, (since,)).fetchone()[0] or 0

                total_blocked = conn.execute("""
                    SELECT COUNT(*) FROM trade_opportunities
                    WHERE was_traded = 0
                      AND ai_verdict IN ('BLOQUEAR', 'AGUARDAR', 'AGUARDAR_IA', 'BLOQUEAR_IA')
                      AND created_at >= ?
                """, (since,)).fetchone()[0] or 0
            except Exception:
                pass

        total_exec = tp + fp
        precision  = round(tp / total_exec * 100, 1) if total_exec else 0
        fn_rate    = round(fn_est / total_blocked * 100, 1) if total_blocked else 0

        return {
            "true_positives":              tp,
            "false_positives":             fp,
            "false_negatives_estimated":   fn_est,
            "total_executed":              total_exec,
            "total_blocked":               total_blocked,
            "precision":                   precision,
            "fn_rate":                     fn_rate,
            "period":                      period,
            "generated_at":               datetime.now(_BRT).isoformat(),
        }

    except Exception as exc:
        logger.warning("ai_supervisor.get_fp_fn_summary: %s", exc)
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# Win rate por nivel de confianca da IA
# ---------------------------------------------------------------------------

def analyze_confidence_accuracy(period: str = _DEFAULT_PERIOD) -> list:
    """
    Win rate dos trades executados agrupados por faixa de ai_confidence.
    Ajuda a calibrar o limiar de confianca minimo.
    Fonte: auto_trades.
    """
    since = _period_since(period)

    try:
        with _conn() as conn:
            rows = conn.execute("""
                SELECT
                    (ai_confidence / 10) * 10                             AS bucket,
                    COUNT(*)                                              AS total,
                    SUM(CASE WHEN pnl_pts > 0 THEN 1 ELSE 0 END)         AS wins,
                    ROUND(AVG(pnl_pts), 1)                               AS avg_pnl
                FROM auto_trades
                WHERE closed_at IS NOT NULL
                  AND pnl_brl IS NOT NULL
                  AND ai_confidence IS NOT NULL
                  AND substr(closed_at, 1, 10) >= ?
                GROUP BY bucket
                ORDER BY bucket DESC
            """, (since,)).fetchall()

        result = []
        for r in rows:
            bucket = r[0] or 0
            total  = r[1] or 0
            wins   = r[2] or 0
            wr     = round(wins / total * 100) if total else 0
            result.append({
                "range":    f"{bucket}-{bucket+9}%",
                "bucket":   bucket,
                "total":    total,
                "wins":     wins,
                "win_rate": wr,
                "avg_pnl":  r[3],
            })
        return result

    except Exception as exc:
        logger.warning("ai_supervisor.analyze_confidence_accuracy: %s", exc)
        return []
