import json
import re
from typing import Any


def extract_json_object(text: str) -> dict[str, Any] | None:
    if not text or not text.strip():
        return None
    s = text.strip()
    if "```" in s:
        m = re.search(r"```(?:json)?\s*([\s\S]*?)```", s)
        if m:
            s = m.group(1).strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    start = s.find("{")
    end = s.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(s[start : end + 1])
        except json.JSONDecodeError:
            return None
    return None


def risk_summary(risk_parsed: dict[str, Any] | None) -> tuple[str | None, int | None, bool | None]:
    if not risk_parsed:
        return None, None, None
    decisao = risk_parsed.get("decisao")
    if isinstance(decisao, str):
        decisao_norm = decisao.strip().upper().replace(" ", "_")
    else:
        decisao_norm = None
    score = risk_parsed.get("score_final")
    try:
        score_int = int(score) if score is not None else None
    except (TypeError, ValueError):
        score_int = None
    permitir = risk_parsed.get("permitir_trade")
    if isinstance(permitir, bool):
        permitir_b = permitir
    elif isinstance(permitir, str):
        permitir_b = permitir.lower() in ("true", "1", "sim", "yes")
    else:
        permitir_b = None
    return decisao_norm, score_int, permitir_b


def trader_ativo_label(trader_raw: Any) -> str:
    """Ticker/ativo lido do JSON do trader (campo `ativo`)."""
    if trader_raw is None:
        return "—"
    if isinstance(trader_raw, dict):
        d: dict[str, Any] = trader_raw
    elif isinstance(trader_raw, str):
        d = extract_json_object(trader_raw) or {}
    else:
        return "—"
    if not isinstance(d, dict):
        return "—"
    a = d.get("ativo")
    s = str(a or "").strip()
    return s if s else "—"


def trader_ativo_hint(trader_raw: Any) -> str:
    """Texto curto para title/tooltip (`ativo_como_detectado`)."""
    if trader_raw is None:
        return ""
    if isinstance(trader_raw, dict):
        d = trader_raw
    elif isinstance(trader_raw, str):
        d = extract_json_object(trader_raw) or {}
    else:
        return ""
    if not isinstance(d, dict):
        return ""
    h = d.get("ativo_como_detectado")
    s = str(h or "").strip()
    return s
