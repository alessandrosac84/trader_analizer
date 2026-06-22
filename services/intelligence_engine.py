"""
services/intelligence_engine.py
---------------------------------
Motor de Inteligencia: aprende com os resultados registrados em
trade_opportunities e gera recomendacoes acionaveis.

Evoluções implementadas:
  1. Score Performance Analysis   — min score recomendado
  2. Dynamic Score Weighting      — pesos por componente vs resultado
  3. Regime Manager               — regimes lucrativos vs bloqueados
  4. Signal Strength Optimization — faixa ideal de forca do sinal

REGRA: apenas LE dados. Nunca modifica modulos existentes.
"""
import json
import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

_BRT = timezone(timedelta(hours=-3))
_MIN_SAMPLES = 5   # minimo de amostras para uma recomendacao ser confiavel


def _conn():
    from services.db import _conn as _db
    return _db()


# ---------------------------------------------------------------------------
# 1. Score Performance Analysis
# ---------------------------------------------------------------------------

def analyze_score_performance() -> list:
    """
    Win rate e P&L medio por valor de score.
    Retorna lista ordenada por score desc.
    """
    try:
        with _conn() as conn:
            rows = conn.execute("""
                SELECT
                    score,
                    COUNT(*)                                           AS total,
                    SUM(CASE WHEN outcome_win = 1 THEN 1 ELSE 0 END)  AS wins,
                    ROUND(AVG(CASE WHEN outcome_win IS NOT NULL
                              THEN outcome_pnl_pts END), 1)            AS avg_pnl,
                    ROUND(AVG(CASE WHEN outcome_win IS NOT NULL
                              THEN outcome_pnl_brl END), 2)            AS avg_brl
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
            wr    = round(wins / total * 100) if total else 0
            result.append({
                "score":    r[0],
                "total":    total,
                "wins":     wins,
                "losses":   total - wins,
                "win_rate": wr,
                "avg_pnl":  r[3],
                "avg_brl":  r[4],
            })
        return result
    except Exception as exc:
        logger.warning("analyze_score_performance: %s", exc)
        return []


def suggest_min_score(min_wr: float = 55.0, min_samples: int = _MIN_SAMPLES) -> dict:
    """
    Sugere o menor score que ainda atinge win_rate >= min_wr
    com pelo menos min_samples amostras.
    """
    rows = analyze_score_performance()
    qualifying = [r for r in rows if r["total"] >= min_samples and r["win_rate"] >= min_wr]
    if not qualifying:
        return {"min_score": None, "confidence": "baixa", "reason": "amostras insuficientes"}

    min_s   = min(r["score"] for r in qualifying)
    max_wr  = max(r["win_rate"] for r in qualifying)
    samples = sum(r["total"] for r in qualifying)
    conf    = "alta" if samples >= 30 else "media" if samples >= 15 else "baixa"

    return {
        "min_score":    min_s,
        "max_win_rate": max_wr,
        "qualifying":   qualifying,
        "confidence":   conf,
        "total_samples": samples,
    }


# ---------------------------------------------------------------------------
# 2. Dynamic Score Weighting
# ---------------------------------------------------------------------------

def analyze_component_weights() -> list:
    """
    Para cada componente do score_breakdown (ema_alignment, macd, vwap, etc.),
    calcula a correlacao com resultado positivo:

      correlation = (win_rate_quando_componente_positivo) -
                    (win_rate_quando_componente_neutro_ou_negativo)

    Componentes com alta correlacao sao candidatos a peso maior.
    Componentes com correlacao baixa podem ter peso reduzido.
    """
    try:
        with _conn() as conn:
            rows = conn.execute("""
                SELECT score_breakdown, outcome_win
                FROM trade_opportunities
                WHERE was_traded = 1
                  AND outcome_win IS NOT NULL
                  AND score_breakdown IS NOT NULL
                  AND score_breakdown != '{}'
            """).fetchall()

        if not rows:
            return []

        # Acumula: comp -> {pos_wins, pos_total, neg_wins, neg_total}
        stats = {}
        for r in rows:
            try:
                bd = json.loads(r[0]) if r[0] else {}
            except Exception:
                continue
            ow = r[1]  # 0 ou 1

            for comp, val in bd.items():
                if comp not in stats:
                    stats[comp] = {"pos_wins": 0, "pos_total": 0,
                                   "neg_wins": 0, "neg_total": 0}
                if val > 0:
                    stats[comp]["pos_total"] += 1
                    if ow == 1:
                        stats[comp]["pos_wins"] += 1
                else:
                    stats[comp]["neg_total"] += 1
                    if ow == 1:
                        stats[comp]["neg_wins"] += 1

        result = []
        for comp, s in stats.items():
            pt = s["pos_total"]
            nt = s["neg_total"]
            pw = round(s["pos_wins"] / pt * 100, 1) if pt else None
            nw = round(s["neg_wins"] / nt * 100, 1) if nt else None
            corr = round((pw or 0) - (nw or 0), 1) if pw is not None else 0.0

            total = pt + nt
            if total < _MIN_SAMPLES:
                continue

            # Sugestao de ajuste de peso
            if corr >= 20:
                suggestion = "aumentar"
            elif corr <= -10:
                suggestion = "reduzir"
            else:
                suggestion = "manter"

            result.append({
                "component":       comp,
                "pos_total":       pt,
                "pos_win_rate":    pw,
                "neg_total":       nt,
                "neg_win_rate":    nw,
                "correlation":     corr,
                "suggestion":      suggestion,
                "total_samples":   total,
            })

        result.sort(key=lambda x: x["correlation"], reverse=True)
        return result

    except Exception as exc:
        logger.warning("analyze_component_weights: %s", exc)
        return []


# ---------------------------------------------------------------------------
# 3. Regime Manager
# ---------------------------------------------------------------------------

def analyze_regime_viability() -> list:
    """
    Win rate por market_regime.
    Regimes com win_rate < 45% e >= min_samples recebem status 'bloquear'.
    Regimes com win_rate >= 60% recebem status 'priorizar'.
    """
    try:
        with _conn() as conn:
            rows = conn.execute("""
                SELECT
                    market_regime,
                    COUNT(*)                                           AS total,
                    SUM(CASE WHEN outcome_win = 1 THEN 1 ELSE 0 END)  AS wins,
                    ROUND(AVG(outcome_pnl_pts), 1)                    AS avg_pnl
                FROM trade_opportunities
                WHERE was_traded = 1
                  AND outcome_win IS NOT NULL
                  AND market_regime IS NOT NULL
                  AND action IN ('COMPRA', 'VENDA')
                GROUP BY market_regime
                ORDER BY wins * 1.0 / (total + 0.001) DESC
            """).fetchall()

        result = []
        for r in rows:
            total = r[1] or 0
            wins  = r[2] or 0
            wr    = round(wins / total * 100) if total else 0
            conf  = "alta" if total >= 30 else "media" if total >= 10 else "baixa"

            if total < _MIN_SAMPLES:
                status = "aguardando_dados"
            elif wr >= 60:
                status = "priorizar"
            elif wr >= 45:
                status = "neutro"
            else:
                status = "bloquear"

            result.append({
                "regime":     r[0],
                "total":      total,
                "wins":       wins,
                "losses":     total - wins,
                "win_rate":   wr,
                "avg_pnl":    r[3],
                "status":     status,
                "confidence": conf,
            })
        return result
    except Exception as exc:
        logger.warning("analyze_regime_viability: %s", exc)
        return []


def get_blocked_regimes() -> list:
    """Retorna lista de regimes que o motor recomenda bloquear."""
    return [r["regime"] for r in analyze_regime_viability()
            if r["status"] == "bloquear" and r["total"] >= _MIN_SAMPLES]


# ---------------------------------------------------------------------------
# 4. Signal Strength Optimization
# ---------------------------------------------------------------------------

def analyze_strength_ranges() -> list:
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
            wr     = round(wins / total * 100) if total else 0

            if total < _MIN_SAMPLES:
                status = "aguardando_dados"
            elif wr >= 60:
                status = "priorizar"
            elif wr >= 45:
                status = "neutro"
            else:
                status = "evitar"

            result.append({
                "range":   f"{bucket}-{bucket + 9}",
                "bucket":  bucket,
                "total":   total,
                "wins":    wins,
                "losses":  total - wins,
                "win_rate": wr,
                "avg_pnl": r[3],
                "status":  status,
            })
        return result
    except Exception as exc:
        logger.warning("analyze_strength_ranges: %s", exc)
        return []


def suggest_min_strength(min_wr: float = 55.0) -> dict:
    """Sugere a forca minima do sinal com base nos resultados."""
    rows = analyze_strength_ranges()
    qualifying = [r for r in rows if r["total"] >= _MIN_SAMPLES and r["win_rate"] >= min_wr]
    if not qualifying:
        return {"min_strength": None, "confidence": "baixa"}

    min_b = min(r["bucket"] for r in qualifying)
    conf  = "alta" if sum(r["total"] for r in qualifying) >= 30 else "media"
    return {"min_strength": min_b, "confidence": conf, "qualifying": qualifying}


# ---------------------------------------------------------------------------
# 5. Master Recommendations
# ---------------------------------------------------------------------------

def get_recommendations() -> dict:
    """
    Consolida todas as analises em recomendacoes acionaveis.
    Retorna um dicionario pronto para exibir no dashboard.
    """
    score_rec    = suggest_min_score()
    strength_rec = suggest_min_strength()
    regimes      = analyze_regime_viability()
    weights      = analyze_component_weights()

    blocked = [r["regime"] for r in regimes if r["status"] == "bloquear"]
    prioritize_regimes = [r["regime"] for r in regimes if r["status"] == "priorizar"]

    top_components = [w for w in weights if w["suggestion"] == "aumentar"][:3]
    drop_components = [w for w in weights if w["suggestion"] == "reduzir"][:3]

    # Nivel de confianca geral (baseado em volume de dados)
    try:
        with _conn() as conn:
            total_resolved = conn.execute("""
                SELECT COUNT(*) FROM trade_opportunities
                WHERE was_traded = 1 AND outcome_win IS NOT NULL
            """).fetchone()[0] or 0
    except Exception:
        total_resolved = 0

    if total_resolved >= 200:
        overall_conf = "alta"
    elif total_resolved >= 50:
        overall_conf = "media"
    elif total_resolved >= _MIN_SAMPLES:
        overall_conf = "baixa"
    else:
        overall_conf = "insuficiente"

    return {
        "overall_confidence":    overall_conf,
        "total_resolved_trades": total_resolved,
        "min_score":             score_rec.get("min_score"),
        "score_confidence":      score_rec.get("confidence"),
        "min_strength":          strength_rec.get("min_strength"),
        "strength_confidence":   strength_rec.get("confidence"),
        "blocked_regimes":       blocked,
        "prioritize_regimes":    prioritize_regimes,
        "top_components":        top_components,
        "drop_components":       drop_components,
        "generated_at":          datetime.now(_BRT).isoformat(),
    }


# ---------------------------------------------------------------------------
# 6. Confluence Correlation
# ---------------------------------------------------------------------------

def analyze_confluence_correlation() -> list:
    """
    Para cada confluencia (texto), calcula win rate quando presente vs ausente.
    Minimo de _MIN_SAMPLES para ser exibida.
    """
    try:
        with _conn() as conn:
            rows = conn.execute("""
                SELECT confluences, outcome_win
                FROM trade_opportunities
                WHERE was_traded = 1
                  AND outcome_win IS NOT NULL
                  AND confluences IS NOT NULL
            """).fetchall()

        if not rows:
            return []

        stats = {}
        for r in rows:
            try:
                confs = json.loads(r[0]) if r[0] else []
            except Exception:
                continue
            ow = r[1]
            seen = set()
            for c in confs:
                key = c.strip()[:80]
                if key in seen:
                    continue
                seen.add(key)
                if key not in stats:
                    stats[key] = {"wins": 0, "total": 0}
                stats[key]["total"] += 1
                if ow == 1:
                    stats[key]["wins"] += 1

        result = []
        for conf, s in stats.items():
            t = s["total"]
            w = s["wins"]
            if t < _MIN_SAMPLES:
                continue
            wr = round(w / t * 100) if t else 0
            result.append({
                "confluence": conf,
                "total":      t,
                "wins":       w,
                "win_rate":   wr,
            })

        result.sort(key=lambda x: x["win_rate"], reverse=True)
        return result[:30]

    except Exception as exc:
        logger.warning("analyze_confluence_correlation: %s", exc)
        return []
