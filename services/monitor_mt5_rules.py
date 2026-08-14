"""
monitor_mt5_rules.py — Regras calibradas do Monitor MT5 (score v6, sem IA).

Varredura ~6,6a (sweep_monitor_mt5_rules.py + tools_pick_monitor_pkg.py).
Pacote WIN (GO): janelas A/B + bloqueia segunda + piso RR 2.0.
WDO: preenchido após varredura honesta (não usar top n≈40 overfit).

Usado por: trade_executor, app autotrade, backtest_monitor_mt5_7y.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any

# Janelas em minutos do dia (BRT).
WINDOW_A_B = [(9 * 60 + 15, 11 * 60 + 30), (14 * 60, 16 * 60 + 30)]
WINDOW_NO_LATE = (9 * 60, 15 * 60 + 30)
WINDOW_SKIP_OPEN_LUNCH = [(9 * 60 + 30, 12 * 60), (13 * 60, 17 * 60 + 30)]

# Regras por ativo (chaves = prefixo WIN/WDO ou TV symbol).
# target_rr: piso do TP1 em R (motor/execução esticam o alvo se S/R for curto).
MONITOR_RULES: dict[str, dict[str, Any]] = {
    "WIN": {
        "min_score": 9,
        "max_score": 11,          # teto alinhado ao EXHAUSTION do motor
        "hours": WINDOW_A_B,      # 09:15–11:30 ∪ 14:00–16:30
        "hours_label": "A_B",
        "block_dow": (0,),        # segunda-feira
        "target_rr": 2.0,
        "adx_min": None,
        "vol_min": None,
        # BT oficial ~6,6a: n=686 PF=1.33 exp=+0.170 net=+116.7 OOS=70%
        "note": "GO: A_B + blk segunda + RR>=2.0 (vs base +0.070/1.14 → +0.170/1.33)",
    },
    "WDO": {
        "min_score": 10,          # 7 e 9 destroem edge; 8 ajuda pouco vs 10 filtrado
        "max_score": 11,
        "hours": WINDOW_A_B,
        "hours_label": "A_B",
        "block_dow": None,        # sem blk sexta: mais n/net sem perder GO
        "target_rr": 2.0,
        "adx_min": 30.0,
        "vol_min": 1.3,
        # BT oficial ~6,6a: n=159 PF=1.72 exp=+0.358 net=+56.9 OOS=66%
        "note": "GO: sc>=10 + A_B + ADX>=30 + vol>=1.3 + RR>=2.0 (vs base -0.016/0.97)",
    },
}


def asset_key(symbol: str) -> str:
    s = (symbol or "").upper().replace("$", "").replace("@", "").replace("_", "")
    if "WIN" in s or s.endswith("IND") or ":IND" in s:
        return "WIN"
    if "WDO" in s or "DOL" in s:
        return "WDO"
    if s.startswith("WIN"):
        return "WIN"
    if s.startswith("WDO"):
        return "WDO"
    return s[:3]


def rules_for(symbol: str) -> dict[str, Any]:
    return dict(MONITOR_RULES.get(asset_key(symbol), {}))


def _minutes_now_brt(now: datetime | None = None) -> tuple[int, int]:
    if now is None:
        now = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=-3)))
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone(timedelta(hours=-3)))
    else:
        now = now.astimezone(timezone(timedelta(hours=-3)))
    return now.weekday(), now.hour * 60 + now.minute


def hours_ok(minute: int, hours) -> bool:
    if not hours:
        return True
    if isinstance(hours, (list, tuple)) and hours and isinstance(hours[0], (list, tuple)):
        return any(a <= minute < b for a, b in hours)
    return hours[0] <= minute < hours[1]


def session_allows(symbol: str, now: datetime | None = None) -> tuple[bool, str]:
    """Gate de sessão/DOW do Monitor. Retorna (ok, motivo)."""
    cfg = rules_for(symbol)
    if not cfg:
        return True, ""
    dow, minute = _minutes_now_brt(now)
    bdow = cfg.get("block_dow") or ()
    if dow in bdow:
        return False, f"Monitor: {asset_key(symbol)} não opera neste dia da semana (dow={dow})"
    if not hours_ok(minute, cfg.get("hours")):
        return False, (
            f"Monitor: fora da janela {cfg.get('hours_label', '?')} "
            f"({asset_key(symbol)})"
        )
    return True, ""


def apply_tp_floor(acao: str, entrada: float, stop: float, tp1: float | None,
                   target_rr: float) -> float | None:
    """Garante TP1 com RR >= target_rr (nunca encurta um alvo já mais longo)."""
    if tp1 is None or entrada is None or stop is None or target_rr is None:
        return tp1
    risk = abs(float(entrada) - float(stop))
    if risk <= 0:
        return tp1
    if acao == "COMPRA":
        floor = float(entrada) + float(target_rr) * risk
        return max(float(tp1), floor)
    if acao == "VENDA":
        floor = float(entrada) - float(target_rr) * risk
        return min(float(tp1), floor)
    return tp1


def score_allowed(symbol: str, score: float | int | None) -> tuple[bool, str]:
    cfg = rules_for(symbol)
    if not cfg:
        return True, ""
    sc = abs(float(score or 0))
    mn = float(cfg.get("min_score") or 0)
    mx = float(cfg.get("max_score") or 99)
    if sc < mn:
        return False, f"Score {score} < min {mn} ({asset_key(symbol)})"
    if sc > mx:
        return False, f"Score {score} > max {mx} ({asset_key(symbol)})"
    return True, ""


def filters_allowed(
    symbol: str,
    *,
    adx: float | None = None,
    vol_ratio: float | None = None,
    require_data: bool = False,
) -> tuple[bool, str]:
    """Gates ADX / volume do Monitor.

    Sem dado: fail-open (não quebra ordem manual). Com require_data=True
    (autotrade / BT), ausência de dado bloqueia.
    """
    cfg = rules_for(symbol)
    if not cfg:
        return True, ""
    adx_m = cfg.get("adx_min")
    if adx_m is not None:
        if adx is None:
            if require_data:
                return False, f"Monitor: ADX ausente (exige >={adx_m}, {asset_key(symbol)})"
        elif float(adx) < float(adx_m):
            return False, (
                f"Monitor: ADX {adx} < {adx_m} ({asset_key(symbol)})"
            )
    vol_m = cfg.get("vol_min")
    if vol_m is not None:
        if vol_ratio is None:
            if require_data:
                return False, f"Monitor: vol_ratio ausente (exige >={vol_m}, {asset_key(symbol)})"
        elif float(vol_ratio) < float(vol_m):
            return False, (
                f"Monitor: vol_ratio {vol_ratio} < {vol_m} ({asset_key(symbol)})"
            )
    return True, ""
