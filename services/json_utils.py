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


# ---------------------------------------------------------------------------
# Parse de níveis de trade (entrada, stop, TP1/TP2/TP3)
# ---------------------------------------------------------------------------

def _extract_price_from_text(text: str) -> "float | None":
    """Extrai primeiro preço numérico de um texto (ex: '195.500', '196.100 (115 pts)')."""
    if not text:
        return None
    import re
    s = str(text).strip()
    # Preços tipo 195.500, 1.11921, 30.50 — 1-6 dígitos + ponto/vírgula + 1-3 dígitos
    matches = re.findall(r'\b(\d{1,6}[.,]\d{1,3})\b', s)
    for m in matches:
        try:
            v = float(m.replace(',', '.'))
            if v > 0:
                return v
        except ValueError:
            continue
    return None


def _extract_pts_from_text(text: str) -> "float | None":
    """Extrai valor em pontos de texto como '600 pts', '115 pontos'."""
    if not text:
        return None
    import re
    m = re.search(r'(\d+(?:[.,]\d+)?)\s*(?:pts?|pontos?)', str(text), re.I)
    if m:
        try:
            return float(m.group(1).replace(',', '.'))
        except ValueError:
            pass
    return None


def parse_trade_levels(trader_raw: Any) -> dict:
    """
    Extrai entrada, stop, TP1/TP2/TP3 do JSON do trader.
    Retorna dict: {acao, entrada, stop, tp1, tp2, tp3} — float ou None.
    """
    result: dict = {
        "acao": None, "entrada": None, "stop": None,
        "tp1": None, "tp2": None, "tp3": None,
    }

    if trader_raw is None:
        return result
    if isinstance(trader_raw, str):
        d = extract_json_object(trader_raw) or {}
    elif isinstance(trader_raw, dict):
        d = trader_raw
    else:
        return result

    # Ação
    acao = str(d.get("acao", "") or "").strip().upper()
    if "COMPRA" in acao:
        result["acao"] = "COMPRA"
    elif "VENDA" in acao:
        result["acao"] = "VENDA"

    is_compra = result["acao"] == "COMPRA"

    # Entrada
    entrada_price = _extract_price_from_text(str(d.get("entrada", "") or ""))
    result["entrada"] = entrada_price

    # Stop
    stop_text = str(d.get("stop", "") or "")
    stop_price = _extract_price_from_text(stop_text)
    if stop_price is None and entrada_price is not None:
        pts = _extract_pts_from_text(stop_text)
        if pts is not None:
            stop_price = entrada_price - pts if is_compra else entrada_price + pts
    result["stop"] = stop_price

    # TPs — campo alvos[]
    alvos = d.get("alvos") or []
    tp_keys = ["tp1", "tp2", "tp3"]
    idx = 0
    for alvo in alvos:
        if idx >= 3:
            break
        if not isinstance(alvo, dict):
            continue
        dist_text = str(alvo.get("distancia", "") or "")
        tp_price = _extract_price_from_text(dist_text)
        if tp_price is None and entrada_price is not None:
            pts = _extract_pts_from_text(dist_text)
            if pts is not None:
                tp_price = entrada_price + pts if is_compra else entrada_price - pts
        result[tp_keys[idx]] = tp_price
        idx += 1

    return result
