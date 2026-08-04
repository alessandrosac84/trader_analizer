"""
crypto_v20_gos.py — 3 GOs BTC/ETH da bateria v20 (03/08/2026).

Famílias novas (não cobertas em v13–v18):
  · RSI reclaim M60 (BTC + ETH)
  · BB fade NY M15 (BTC)

Régua: net≥+0,10 R · PF≥1,25 · cons≥55% · OOS segura · n≥40.
Não altera paths anteriores — só adiciona.
"""
from __future__ import annotations

from services.crypto_edge_setups import (
    _rsi_recl_core, _bb_fade_core, _session_dow_ok,
)

V20_GOS = [
    {"name": "BTC_RSI_RECL_M60", "sym": "BTCUSD", "tf": 60, "kind": "rsi_recl",
     "vol": 1.2, "lo": 30.0, "hi": 70.0, "hh0": 0.0, "hh1": 24.0,
     "session": None, "tp_r": 1.5,
     "title": "RSI reclaim 30/70 + vol≥1.2× + H4 · 24h · M60",
     "net": 0.200, "pf": 1.38, "cons": 56, "oos_net": 0.353, "n": 115},
    {"name": "ETH_RSI_RECL_M60", "sym": "ETHUSD", "tf": 60, "kind": "rsi_recl",
     "vol": 1.2, "lo": 30.0, "hi": 70.0, "hh0": 0.0, "hh1": 24.0,
     "session": None, "tp_r": 1.5,
     "title": "RSI reclaim 30/70 + vol≥1.2× + H4 · 24h · M60",
     "net": 0.191, "pf": 1.37, "cons": 65, "oos_net": 0.174, "n": 125},
    {"name": "BTC_BBFADE_NY", "sym": "BTCUSD", "tf": 15, "kind": "bb_fade",
     "vol": 1.2, "hh0": 13.0, "hh1": 17.0, "session": None, "tp_r": 1.5,
     "title": "BB fade (poke+rejeição) + H4 · 13–17h",
     "net": 0.175, "pf": 1.34, "cons": 65, "oos_net": 0.152, "n": 168},
]

V20_BY_NAME = {g["name"]: g for g in V20_GOS}


def v20_path_names(symbol: str) -> list:
    sym = (symbol or "").upper().strip()
    return [g["name"] for g in V20_GOS if g["sym"] == sym]


def v20_enabled(name: str, symbol: str) -> bool:
    g = V20_BY_NAME.get(name)
    if not g:
        return False
    return (symbol or "").upper().strip() == g["sym"]


def v20_signal(name: str, candles, symbol: str = ""):
    g = V20_BY_NAME.get(name)
    if not g:
        return None
    if symbol and (symbol or "").upper().strip() != g["sym"]:
        return None
    if not _session_dow_ok(candles, g.get("session")):
        return None
    kind = g["kind"]
    tp_r = float(g.get("tp_r", 1.5))
    hh0, hh1 = float(g.get("hh0", 0.0)), float(g.get("hh1", 24.0))
    vol = float(g.get("vol", 1.2))
    if kind == "rsi_recl":
        return _rsi_recl_core(
            candles, lo=float(g.get("lo", 30)), hi=float(g.get("hi", 70)),
            vol_mult=vol, hh0=hh0, hh1=hh1, tp_r=tp_r)
    if kind == "bb_fade":
        return _bb_fade_core(candles, vol_mult=vol, hh0=hh0, hh1=hh1, tp_r=tp_r)
    return None


def v20_cascade_items(symbol: str, tf: int | None = None):
    sym = (symbol or "").upper().strip()
    items = []
    for g in V20_GOS:
        if g["sym"] != sym:
            continue
        if tf is not None and int(g["tf"]) != int(tf):
            continue
        name = g["name"]
        motivo = f"[{name} M{g['tf']} GO v20] {g['title']}"

        def _make_en(n):
            return lambda s, _n=n: v20_enabled(_n, s)

        def _make_sig(n):
            return lambda c, s="", _n=n: v20_signal(_n, c, s)

        items.append((_make_en(name), _make_sig(name), name, motivo))
    return items


def v20_needs_m60(symbol: str) -> bool:
    sym = (symbol or "").upper().strip()
    return any(g["sym"] == sym and int(g["tf"]) == 60 for g in V20_GOS)
