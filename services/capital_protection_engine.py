"""
services/capital_protection_engine.py
--------------------------------------
Capital Protection Engine (CPE) V1.1 — poder de veto sobre todos os modulos.
14 blocos de protecao + melhorias M1-M8.

REGRA: este modulo NUNCA altera modulos existentes (Monitor MT5 / Scalper).
"""
import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)
_BRT = timezone(timedelta(hours=-3))


def _now_brt() -> datetime:
    return datetime.now(_BRT)


def _today_brt() -> str:
    return _now_brt().strftime("%Y-%m-%d")


def _conn():
    from services.db import _conn as _db
    return _db()


# ---------------------------------------------------------------------------
# Queries de dados diarios
# ---------------------------------------------------------------------------

def get_daily_stats() -> dict:
    """Le auto_trades do dia atual (BRT) e retorna estatisticas.
    Inclui:
      - Trades FECHADOS hoje (por closed_at) — captura posicoes abertas ontem e fechadas hoje
      - Trades ABERTOS hoje ainda sem fechamento (por opened_at) — exposicao atual
    """
    today = _today_brt()
    _SKIP = ("BLOQUEADO_IA", "AGUARDADO_IA", "IA_BLOQUEOU", "AGUARDAR", "BLOQUEAR")
    try:
        with _conn() as conn:
            rows = conn.execute("""
                SELECT pnl_pts, pnl_brl, close_reason,
                       closed_at, opened_at
                FROM auto_trades
                WHERE (
                    (closed_at IS NOT NULL AND substr(closed_at, 1, 10) = ?)
                    OR
                    (closed_at IS NULL AND substr(opened_at, 1, 10) = ?)
                )
            """, (today, today)).fetchall()

        total = wins = losses = consec = max_consec_cur = 0
        pnl_brl_total = 0.0
        pnl_pts_total = 0.0

        for r in rows:
            pnl_pts  = r[0] or 0
            pnl_brl  = r[1] or 0.0
            reason   = (r[2] or "").upper()
            if reason in _SKIP:
                continue
            total += 1
            pnl_brl_total += pnl_brl
            pnl_pts_total += pnl_pts
            if pnl_pts > 0:
                wins += 1
                consec = 0
            else:
                losses += 1
                consec += 1
                max_consec_cur = max(max_consec_cur, consec)

        return {
            "total":              total,
            "wins":               wins,
            "losses":             losses,
            "consecutive_losses": max_consec_cur,
            "pnl_brl":            round(pnl_brl_total, 2),
            "pnl_pts":            round(pnl_pts_total, 1),
        }
    except Exception as exc:
        logger.warning("get_daily_stats error: %s", exc)
        return {"total": 0, "wins": 0, "losses": 0,
                "consecutive_losses": 0, "pnl_brl": 0.0, "pnl_pts": 0.0}


def get_last_trade_info() -> dict:
    """Retorna timestamp e resultado do ultimo trade do dia."""
    today = _today_brt()
    _SKIP = ("BLOQUEADO_IA", "AGUARDADO_IA", "IA_BLOQUEOU")
    try:
        with _conn() as conn:
            row = conn.execute("""
                SELECT closed_at, opened_at, pnl_pts, close_reason
                FROM auto_trades
                WHERE (
                    (closed_at IS NOT NULL AND substr(closed_at, 1, 10) = ?)
                    OR
                    (closed_at IS NULL AND substr(opened_at, 1, 10) = ?)
                )
                  AND close_reason NOT IN (?, ?, ?)
                ORDER BY id DESC LIMIT 1
            """, (today, today, *_SKIP)).fetchone()
        if not row:
            return {"time": None, "pnl_pts": None, "outcome": None}
        pnl = row[2] or 0
        return {"time": row[0] or row[1], "pnl_pts": pnl,
                "outcome": "win" if pnl > 0 else "loss"}
    except Exception as exc:
        logger.warning("get_last_trade_info: %s", exc)
        return {"time": None, "pnl_pts": None, "outcome": None}


# ---------------------------------------------------------------------------
# Estado diario
# ---------------------------------------------------------------------------

def _reset_daily_state_if_needed() -> None:
    from services.risk_settings_service import get_state, set_state
    today = _today_brt()
    last_date = get_state("last_state_date", "")
    if last_date != today:
        set_state("last_state_date",                    today)
        set_state("daily_max_profit",                   0.0)
        set_state("soft_mode_active",                   False)
        set_state("day_blocked",                        False)
        set_state("day_block_reason",                   "")
        set_state("last_cooldown_end",                  None)
        set_state("last_trade_outcome",                 None)
        set_state("consecutive_loss_cooldown_end",      None)
        set_state("cooldown_trigger_consec",            0)
        set_state("homolog_override",                   False)
        logger.info("CPE: estado diario resetado para %s", today)


def _update_daily_max_profit(pnl_brl: float) -> float:
    from services.risk_settings_service import get_state, set_state
    current_max = get_state("daily_max_profit", 0.0) or 0.0
    if pnl_brl > current_max:
        set_state("daily_max_profit", pnl_brl)
        return pnl_brl
    return current_max


# ---------------------------------------------------------------------------
# M6 — Calculo do piso protegido (fixo ou percentual)
# ---------------------------------------------------------------------------

def _calc_protected_floor(daily_max: float, cfg: dict) -> float:
    """M6: calcula o piso protegido conforme modo (FIXO ou PERCENTUAL)."""
    if daily_max <= 0:
        return 0.0
    mode = cfg.get("daily_trailing_mode", "FIXO")
    if mode == "PERCENTUAL":
        pct = float(cfg.get("daily_trailing_percent", 20.0))
        return round(daily_max * (1.0 - pct / 100.0), 2)
    else:
        val = float(cfg.get("daily_trailing_value", 100.0))
        return round(daily_max - val, 2)


# ---------------------------------------------------------------------------
# Status constants
# ---------------------------------------------------------------------------

CPE_STATUS_NORMAL      = "NORMAL"
CPE_STATUS_PROTEGIDO   = "PROTEGIDO"
CPE_STATUS_COOLDOWN    = "COOLDOWN"
CPE_STATUS_BLOQUEADO   = "BLOQUEADO_DIA"
CPE_STATUS_CONSERVADOR = "MODO_CONSERVADOR"
CPE_STATUS_CIRCUIT     = "CIRCUIT_BREAKER"
CPE_STATUS_EQUITY      = "ACCOUNT_PROTECTION"


# ---------------------------------------------------------------------------
# Main evaluate
# ---------------------------------------------------------------------------

def cpe_evaluate(
    tv_symbol: str = "",
    acao: str = "",
    sl_pts: float = None,
    score: int = None,
    risk_level: int = None,
    confluence_count: int = None,
) -> dict:
    """
    Avalia todos os 14 blocos do CPE V1.1.
    Retorna dict com allowed, status, reason, block_code, overrides, metricas.
    """
    from services.risk_settings_service import (
        get_settings, get_state, set_state, log_risk_event
    )

    _reset_daily_state_if_needed()

    cfg   = get_settings()
    stats = get_daily_stats()
    pnl   = stats["pnl_brl"]

    daily_max       = _update_daily_max_profit(pnl)
    protected_floor = _calc_protected_floor(daily_max, cfg)

    # M1 — Risk Budget visivel
    activation   = float(cfg.get("profit_protection_activation", 250))
    risk_budget  = round(pnl - protected_floor, 2) if (daily_max >= activation and protected_floor > 0) else None

    day_blocked      = get_state("day_blocked", False)
    day_block_reason = get_state("day_block_reason", "")
    circuit_active   = get_state("circuit_breaker_active", False)
    soft_mode        = get_state("soft_mode_active", False)

    def _log_block(code, reason, status, **extra):
        """M8: log detalhado ao bloquear."""
        log_risk_event(
            event_type       = code,
            event_reason     = reason,
            tv_symbol        = tv_symbol or None,
            acao             = acao or None,
            score            = score,
            risk_budget      = risk_budget,
            daily_pnl        = pnl,
            daily_max_profit = daily_max,
            protected_floor  = protected_floor,
            status_before    = CPE_STATUS_NORMAL,
            status_after     = status,
            **extra
        )

    def _block(code: str, reason: str, status: str) -> dict:
        set_state("day_blocked", True)
        set_state("day_block_reason", code)
        # M2 — Registra ultimo bloqueio
        set_state("last_block_code",   code)
        set_state("last_block_reason", reason)
        set_state("last_block_ts",     _now_brt().isoformat())
        _log_block(code, reason, status)
        return _result(False, status, reason, code)

    def _result(allowed, status, reason="", code="", score_adj=0, risk_adj=0,
                position_factor=1.0):
        return {
            "allowed":              allowed,
            "status":               status,
            "reason":               reason,
            "block_code":           code,
            "score_min_override":   score_adj,
            "risk_max_override":    risk_adj,
            "position_factor":      position_factor,  # M5
            "risk_budget":          risk_budget,       # M1
            "daily_pnl":            pnl,
            "daily_max_profit":     daily_max,
            "protected_floor":      protected_floor,
            "stats":                stats,
        }

    # ── Homolog override — desbloqueio manual (apenas durante homologação) ────
    homolog_override = get_state("homolog_override", False)
    if homolog_override:
        return _result(True, CPE_STATUS_NORMAL,
                       "Override manual ativo — homologacao", "")

    # ── Dia ja bloqueado ───────────────────────────────────────────────────
    if day_blocked:
        return _result(False, CPE_STATUS_BLOQUEADO,
                       f"Dia bloqueado pelo CPE ({day_block_reason})", day_block_reason)

    if circuit_active:
        return _result(False, CPE_STATUS_CIRCUIT,
                       "Circuit Breaker ativo — reativacao manual necessaria", "CIRCUIT_BREAKER")

    # ══════════════════════════════════════════════════════
    # BLOCO 12 — CIRCUIT BREAKER
    # ══════════════════════════════════════════════════════
    if cfg.get("circuit_breaker_enabled"):
        cb_days = get_state("circuit_breaker_consecutive_loss_days", 0) or 0
        max_seq = int(cfg.get("max_daily_stops_sequence", 3))
        if cb_days >= max_seq:
            set_state("circuit_breaker_active", True)
            log_risk_event("CIRCUIT_BREAKER_TRIGGERED",
                           f"{cb_days} dias consecutivos com daily loss",
                           status_after=CPE_STATUS_CIRCUIT)
            return _result(False, CPE_STATUS_CIRCUIT,
                           f"Circuit Breaker: {cb_days} dias no daily loss consecutivos. Reativacao manual obrigatoria.",
                           "CIRCUIT_BREAKER")

    # ══════════════════════════════════════════════════════
    # BLOCO 1 — DAILY LOSS
    # ══════════════════════════════════════════════════════
    if cfg.get("daily_loss_enabled"):
        loss_limit = -abs(float(cfg.get("daily_loss_value", 300)))
        if pnl <= loss_limit:
            cb_days = (get_state("circuit_breaker_consecutive_loss_days", 0) or 0) + 1
            set_state("circuit_breaker_consecutive_loss_days", cb_days)
            return _block("DAILY_LOSS_TRIGGERED",
                          f"Daily Loss atingido: R$ {pnl:.0f} (limite: R$ {loss_limit:.0f})",
                          CPE_STATUS_BLOQUEADO)

    # ══════════════════════════════════════════════════════
    # BLOCO 3 — DAILY TRAILING PROFIT (M6: fixo ou percentual)
    # ══════════════════════════════════════════════════════
    if cfg.get("daily_trailing_enabled") and daily_max >= activation:
        if pnl <= protected_floor and daily_max > 0:
            mode_label = cfg.get("daily_trailing_mode", "FIXO")
            if mode_label == "PERCENTUAL":
                pct = cfg.get("daily_trailing_percent", 20)
                detail = f"piso {pct}% do maximo"
            else:
                detail = f"trailing fixo R$ {cfg.get('daily_trailing_value', 100):.0f}"
            return _block("TRAILING_PROFIT_TRIGGERED",
                          f"Trailing Profit ({detail}): lucro R$ {pnl:.0f} caiu abaixo do piso R$ {protected_floor:.0f} (max: R$ {daily_max:.0f})",
                          CPE_STATUS_BLOQUEADO)

    # ══════════════════════════════════════════════════════
    # BLOCO 4 — HARD DAILY TARGET
    # ══════════════════════════════════════════════════════
    if cfg.get("hard_daily_target_enabled"):
        hard_target = float(cfg.get("hard_daily_target", 800))
        if pnl >= hard_target:
            return _block("HARD_TARGET_REACHED",
                          f"Meta diaria maxima atingida: R$ {pnl:.0f} (target: R$ {hard_target:.0f})",
                          CPE_STATUS_BLOQUEADO)

    # ══════════════════════════════════════════════════════
    # BLOCO 7 — CONSECUTIVE LOSSES
    # ══════════════════════════════════════════════════════
    if cfg.get("loss_pause_enabled"):
        consec   = stats["consecutive_losses"]
        stop_at  = int(cfg.get("stop_after_losses", 3))
        pause_at = int(cfg.get("pause_after_losses", 2))
        pause_min = int(cfg.get("pause_minutes", 30))

        if consec >= stop_at:
            return _block("CONSECUTIVE_LOSSES_STOP",
                          f"{consec} losses consecutivos: operacoes encerradas pelo dia",
                          CPE_STATUS_BLOQUEADO)

        if consec >= pause_at:
            cooldown_end    = get_state("consecutive_loss_cooldown_end")
            trigger_consec  = get_state("cooldown_trigger_consec", 0) or 0
            now_iso         = _now_brt().isoformat()
            # Só inicia novo cooldown se os losses aumentaram desde o último cooldown
            # Isso evita o loop eterno onde o cooldown se re-aplica infinitamente
            if consec > trigger_consec:
                end_iso = (_now_brt() + timedelta(minutes=pause_min)).isoformat()
                set_state("consecutive_loss_cooldown_end", end_iso)
                set_state("cooldown_trigger_consec",       consec)
                log_risk_event("CONSECUTIVE_LOSS_PAUSE",
                               f"{consec} losses consecutivos — pausa de {pause_min} min",
                               tv_symbol=tv_symbol or None,
                               daily_pnl=pnl, status_after=CPE_STATUS_COOLDOWN)
                # M2
                set_state("last_block_code",   "CONSECUTIVE_LOSS_PAUSE")
                set_state("last_block_reason", f"{consec} losses consecutivos")
                set_state("last_block_ts",     now_iso)
            cooldown_end = get_state("consecutive_loss_cooldown_end")
            now_iso      = _now_brt().isoformat()
            if cooldown_end and now_iso < cooldown_end:
                remaining = _minutes_remaining(cooldown_end)
                return _result(False, CPE_STATUS_COOLDOWN,
                               f"Pausa por {consec} losses consecutivos — {remaining} min restantes",
                               "CONSECUTIVE_LOSS_PAUSE")

    # ══════════════════════════════════════════════════════
    # BLOCO 8 — COOLDOWN ENTRE TRADES
    # ══════════════════════════════════════════════════════
    if cfg.get("cooldown_enabled"):
        cooldown_end = get_state("trade_cooldown_end")
        now_iso = _now_brt().isoformat()
        if cooldown_end and now_iso < cooldown_end:
            remaining = _minutes_remaining(cooldown_end)
            return _result(False, CPE_STATUS_COOLDOWN,
                           f"Cooldown ativo — {remaining} min restantes", "COOLDOWN")

    # ══════════════════════════════════════════════════════
    # BLOCO 6 — MAX TRADES PER DAY
    # ══════════════════════════════════════════════════════
    if cfg.get("max_trades_enabled"):
        max_t = int(cfg.get("max_trades_per_day", 8))
        if stats["total"] >= max_t:
            return _block("MAX_TRADES_REACHED",
                          f"Limite diario de trades atingido: {stats['total']}/{max_t}",
                          CPE_STATUS_BLOQUEADO)

    # ══════════════════════════════════════════════════════
    # BLOCO 13 — EQUITY DRAWDOWN
    # ══════════════════════════════════════════════════════
    if cfg.get("equity_protection_enabled"):
        max_dd_pct   = float(cfg.get("max_equity_drawdown_percent", 3.0))
        equity_start = get_state("equity_start_of_day")
        if equity_start and equity_start > 0:
            drawdown_pct = abs(min(pnl, 0)) / equity_start * 100
            if drawdown_pct >= max_dd_pct:
                return _block("EQUITY_DRAWDOWN_TRIGGERED",
                              f"Drawdown de equity: {drawdown_pct:.1f}% (limite: {max_dd_pct}%)",
                              CPE_STATUS_EQUITY)

    # ══════════════════════════════════════════════════════
    # BLOCO 11 — RISK BUDGET (M1: agora com valor visivel)
    # ══════════════════════════════════════════════════════
    if cfg.get("risk_budget_enabled") and sl_pts is not None and risk_budget is not None:
        sl_brl = sl_pts * (10 if "WDO" in str(tv_symbol).upper() else 1)
        if sl_brl > risk_budget > 0:
            log_risk_event("TRADE_BLOCKED_RISK_BUDGET",
                           f"Stop ({sl_brl:.0f}) excede orcamento disponivel ({risk_budget:.0f})",
                           tv_symbol=tv_symbol or None, acao=acao or None, score=score,
                           risk_budget=risk_budget, daily_pnl=pnl,
                           protected_floor=protected_floor)
            # M2
            set_state("last_block_code",   "TRADE_BLOCKED_RISK_BUDGET")
            set_state("last_block_reason", f"Stop R$ {sl_brl:.0f} > Budget R$ {risk_budget:.0f}")
            set_state("last_block_ts",     _now_brt().isoformat())
            return _result(False, CPE_STATUS_PROTEGIDO,
                           f"Risk Budget: stop R$ {sl_brl:.0f} excede orcamento disponivel R$ {risk_budget:.0f}",
                           "RISK_BUDGET_EXCEEDED")

    # ══════════════════════════════════════════════════════
    # Blocos informativos (nao bloqueiam)
    # ══════════════════════════════════════════════════════

    # Bloco 2 — Profit Protection
    if cfg.get("profit_protection_enabled") and daily_max >= activation:
        status = CPE_STATUS_PROTEGIDO
    else:
        status = CPE_STATUS_NORMAL

    # Bloco 5 — Soft Daily Target (M5: expandido)
    score_adj = risk_adj = 0
    position_factor = 1.0
    if cfg.get("soft_target_enabled"):
        soft_target = float(cfg.get("soft_target_value", 500))
        if pnl >= soft_target:
            if not soft_mode:
                set_state("soft_mode_active", True)
                log_risk_event("SOFT_TARGET_ACTIVATED",
                               f"Lucro R$ {pnl:.0f} atingiu soft target R$ {soft_target:.0f}",
                               daily_pnl=pnl, status_after=CPE_STATUS_CONSERVADOR)
            score_adj       = int(cfg.get("soft_score_penalty", 2))
            risk_adj        = int(cfg.get("soft_risk_reduction", 10))
            position_factor = float(cfg.get("soft_max_position_factor", 1.0))
            status          = CPE_STATUS_CONSERVADOR

    return _result(True, status,
                   f"Trade permitido — status: {status}",
                   score_adj=score_adj, risk_adj=risk_adj,
                   position_factor=position_factor)


def _minutes_remaining(end_iso: str) -> int:
    try:
        end_dt = datetime.fromisoformat(end_iso)
        if end_dt.tzinfo is None:
            end_dt = end_dt.replace(tzinfo=_BRT)
        diff = (end_dt - _now_brt()).total_seconds()
        return max(0, int(diff / 60))
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Post-trade
# ---------------------------------------------------------------------------

def cpe_post_trade(outcome: str, tv_symbol: str = "") -> None:
    """Chamado APOS um trade. Registra cooldown."""
    from services.risk_settings_service import get_settings, set_state, log_risk_event
    cfg = get_settings()
    if not cfg.get("cooldown_enabled"):
        return
    now = _now_brt()
    mins = int(cfg.get("cooldown_minutes_after_loss", 15) if outcome == "loss"
               else cfg.get("cooldown_minutes_after_win", 5))
    end_iso = (now + timedelta(minutes=mins)).isoformat()
    set_state("trade_cooldown_end", end_iso)
    set_state("last_trade_outcome", outcome)
    log_risk_event("COOLDOWN_STARTED",
                   f"Cooldown de {mins} min apos {outcome}",
                   tv_symbol=tv_symbol or None,
                   status_after=CPE_STATUS_COOLDOWN)


# ---------------------------------------------------------------------------
# Dashboard status — M1/M2/M3/M4 enriquecido
# ---------------------------------------------------------------------------

def get_cpe_status() -> dict:
    """Retorna snapshot completo do estado CPE para o dashboard (V1.1)."""
    from services.risk_settings_service import get_settings, get_state
    _reset_daily_state_if_needed()

    cfg       = get_settings()
    stats     = get_daily_stats()
    pnl       = stats["pnl_brl"]
    daily_max = _update_daily_max_profit(pnl)
    protected_floor = _calc_protected_floor(daily_max, cfg)

    # M1 — Risk Budget
    activation  = float(cfg.get("profit_protection_activation", 250))
    risk_budget = round(pnl - protected_floor, 2) if (daily_max >= activation and protected_floor > 0) else None

    # Cooldowns
    cooldown_end  = get_state("trade_cooldown_end")
    cl_pause_end  = get_state("consecutive_loss_cooldown_end")
    now_iso       = _now_brt().isoformat()
    cooldown_rem  = _minutes_remaining(cooldown_end) if (cooldown_end  and now_iso < cooldown_end)  else 0
    cl_pause_rem  = _minutes_remaining(cl_pause_end) if (cl_pause_end  and now_iso < cl_pause_end)  else 0

    # M2 — Ultimo bloqueio
    last_block = {
        "code":   get_state("last_block_code"),
        "reason": get_state("last_block_reason"),
        "ts":     get_state("last_block_ts"),
    }

    # Status + overrides
    result = cpe_evaluate()

    # M3 — Percentuais de progresso para barras
    loss_limit   = abs(float(cfg.get("daily_loss_value", 300)))
    hard_target  = float(cfg.get("hard_daily_target", 800))
    max_trades   = int(cfg.get("max_trades_per_day", 8))
    soft_target  = float(cfg.get("soft_target_value", 500))

    loss_pct   = min(100, round(abs(min(pnl, 0)) / loss_limit * 100, 1))     if loss_limit  else 0
    target_pct = min(100, round(max(pnl, 0) / hard_target  * 100, 1))        if hard_target else 0
    trades_pct = min(100, round(stats["total"]  / max_trades * 100, 1))       if max_trades  else 0
    soft_pct   = min(100, round(max(pnl, 0) / soft_target   * 100, 1))        if soft_target else 0

    # M4 — Proximo limite
    next_limit = _calc_next_limit(pnl, daily_max, protected_floor, cfg, stats)

    return {
        "status":                result["status"],
        "allowed":               result["allowed"],
        "reason":                result["reason"],
        "block_code":            result["block_code"],
        # M1
        "daily_pnl":             pnl,
        "daily_max_profit":      daily_max,
        "protected_floor":       round(protected_floor, 2),
        "risk_budget":           risk_budget,
        # M2
        "last_block":            last_block,
        # M3 — barras de progresso
        "loss_progress_pct":     loss_pct,
        "target_progress_pct":   target_pct,
        "trades_progress_pct":   trades_pct,
        "soft_progress_pct":     soft_pct,
        "loss_limit":            loss_limit,
        "hard_target":           hard_target,
        "max_trades":            max_trades,
        "soft_target":           soft_target,
        # M4
        "next_limit":            next_limit,
        # Trades
        "trades_today":          stats["total"],
        "wins_today":            stats["wins"],
        "losses_today":          stats["losses"],
        "consecutive_losses":    stats["consecutive_losses"],
        # Cooldowns
        "cooldown_remaining":    cooldown_rem,
        "loss_pause_remaining":  cl_pause_rem,
        # Estado
        "circuit_breaker_active": get_state("circuit_breaker_active", False),
        "soft_mode_active":      get_state("soft_mode_active", False),
        "day_blocked":           get_state("day_blocked", False),
        "homolog_override":      get_state("homolog_override", False),
        "score_min_override":    result["score_min_override"],
        "risk_max_override":     result["risk_max_override"],
        "position_factor":       result["position_factor"],
        # M6 — info do modo trailing
        "trailing_mode":         cfg.get("daily_trailing_mode", "FIXO"),
        "trailing_value":        cfg.get("daily_trailing_value", 100),
        "trailing_percent":      cfg.get("daily_trailing_percent", 20),
    }


def _calc_next_limit(pnl, daily_max, protected_floor, cfg, stats) -> dict:
    """M4: calcula qual limite sera atingido a seguir."""
    candidates = []

    # Daily loss
    if cfg.get("daily_loss_enabled"):
        loss_lim = -abs(float(cfg.get("daily_loss_value", 300)))
        candidates.append(("Daily Loss", loss_lim, pnl - loss_lim))

    # Trailing (se ativo)
    if cfg.get("daily_trailing_enabled") and protected_floor > 0 and daily_max > 0:
        dist = pnl - protected_floor
        candidates.append(("Trailing Piso", protected_floor, dist))

    # Hard target
    if cfg.get("hard_daily_target_enabled"):
        ht = float(cfg.get("hard_daily_target", 800))
        dist = ht - pnl
        if dist > 0:
            candidates.append(("Hard Target", ht, dist))

    # Soft target
    if cfg.get("soft_target_enabled"):
        st = float(cfg.get("soft_target_value", 500))
        dist = st - pnl
        if dist > 0:
            candidates.append(("Soft Target", st, dist))

    # Max trades
    if cfg.get("max_trades_enabled"):
        max_t = int(cfg.get("max_trades_per_day", 8))
        remaining_t = max_t - stats["total"]
        if remaining_t > 0:
            candidates.append((f"Max Trades ({remaining_t} restantes)", max_t, remaining_t))

    if not candidates:
        return {"name": "—", "value": None, "distance": None}

    # Seleciona o mais proximo (menor distancia absoluta positiva)
    pos = [(n, v, d) for n, v, d in candidates if d is not None]
    if not pos:
        return {"name": "—", "value": None, "distance": None}
    closest = min(pos, key=lambda x: abs(x[2]))
    return {"name": closest[0], "value": closest[1], "distance": round(closest[2], 0)}


# ---------------------------------------------------------------------------
# Resets manuais
# ---------------------------------------------------------------------------

def reset_circuit_breaker() -> None:
    from services.risk_settings_service import set_state, log_risk_event
    set_state("circuit_breaker_active", False)
    set_state("circuit_breaker_consecutive_loss_days", 0)
    log_risk_event("CIRCUIT_BREAKER_RESET", "Reativado manualmente",
                   status_before=CPE_STATUS_CIRCUIT, status_after=CPE_STATUS_NORMAL)


def reset_day_block() -> None:
    from services.risk_settings_service import set_state, log_risk_event
    set_state("day_blocked",                   False)
    set_state("day_block_reason",              "")
    set_state("consecutive_loss_cooldown_end", None)
    set_state("homolog_override",              True)
    log_risk_event("DAY_BLOCK_RESET", "Desbloqueio manual do dia (homologacao)",
                   status_before=CPE_STATUS_BLOQUEADO, status_after=CPE_STATUS_NORMAL)


def disable_homolog_override() -> None:
    """Desativa o override manual e restaura comportamento normal do CPE."""
    from services.risk_settings_service import set_state, log_risk_event
    set_state("homolog_override", False)
    log_risk_event("HOMOLOG_OVERRIDE_OFF", "Override manual desativado",
                   status_before=CPE_STATUS_NORMAL, status_after=CPE_STATUS_NORMAL)
