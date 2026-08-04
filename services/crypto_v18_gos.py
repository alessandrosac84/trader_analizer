"""
crypto_v18_gos.py — 3 GOs ETH da bateria v18 EMA/VWAP (01/08/2026).

Régua: net≥+0,10 R · PF≥1,25 · cons≥55% · OOS segura · n≥40.
Ordenados por net (cascade: melhores primeiro).
Não altera paths v17/anteriores — só adiciona.
Famílias: EMA reclaim + VWAP cont (engolfo/H4-PB zeraram GO).
"""
from __future__ import annotations

from services.crypto_edge_setups import (
    _ema_reclaim_core, _vwap_cont_core, _session_dow_ok,
)

V18_GOS = [
    # ETH — ranking net desc (bateria v18_eth_ema_vwap)
    {"name": "ETH_EMA_RECL_NY_V14_MT_M30", "sym": "ETHUSD", "tf": 30, "kind": "ema_recl",
     "vol": 1.4, "hh0": 13.0, "hh1": 17.0, "session": "mt", "tp_r": 1.5,
     "title": "EMA21 reclaim vol≥1.4× + H4 · 13–17h · seg–qui · M30"},
    {"name": "ETH_VWAP_CONT_ASIA_MT_M30", "sym": "ETHUSD", "tf": 30, "kind": "vwap_cont",
     "vol": 1.3, "dist": 0.3, "hh0": 0.0, "hh1": 8.0, "session": "mt", "tp_r": 1.5,
     "title": "VWAP cont + H4 · 00–08h · seg–qui · M30"},
    {"name": "ETH_EMA_RECL_DAY_V15", "sym": "ETHUSD", "tf": 15, "kind": "ema_recl",
     "vol": 1.5, "hh0": 10.0, "hh1": 16.0, "session": None, "tp_r": 1.5,
     "title": "EMA21 reclaim vol≥1.5× + H4 · 10–16h"},
]

V18_BY_NAME = {g["name"]: g for g in V18_GOS}


def v18_path_names(symbol: str) -> list:
    sym = (symbol or "").upper().strip()
    return [g["name"] for g in V18_GOS if g["sym"] == sym]


def v18_enabled(name: str, symbol: str) -> bool:
    g = V18_BY_NAME.get(name)
    if not g:
        return False
    return (symbol or "").upper().strip() == g["sym"]


def v18_signal(name: str, candles, symbol: str = ""):
    g = V18_BY_NAME.get(name)
    if not g:
        return None
    if symbol and (symbol or "").upper().strip() != g["sym"]:
        return None
    if not _session_dow_ok(candles, g.get("session")):
        return None
    kind = g["kind"]
    tp_r = float(g.get("tp_r", 1.5))
    hh0, hh1 = float(g.get("hh0", 0.0)), float(g.get("hh1", 24.0))
    vol = float(g.get("vol", 1.3))
    if kind == "ema_recl":
        return _ema_reclaim_core(candles, vol_mult=vol, hh0=hh0, hh1=hh1, tp_r=tp_r)
    if kind == "vwap_cont":
        return _vwap_cont_core(candles, vol_mult=vol, dist=float(g.get("dist", 0.3)),
                               hh0=hh0, hh1=hh1, tp_r=tp_r)
    return None


def v18_cascade_items(symbol: str, tf: int | None = None):
    """Lista (enabled_fn, signal_fn, tag, motivo) para o cascade."""
    sym = (symbol or "").upper().strip()
    items = []
    for g in V18_GOS:
        if g["sym"] != sym:
            continue
        if tf is not None and int(g["tf"]) != int(tf):
            continue
        name = g["name"]
        motivo = f"[{name} M{g['tf']} GO v18] {g['title']}"

        def _make_en(n):
            return lambda s, _n=n: v18_enabled(_n, s)

        def _make_sig(n):
            return lambda c, s="", _n=n: v18_signal(_n, c, s)

        items.append((_make_en(name), _make_sig(name), name, motivo))
    return items


def v18_needs_m30(symbol: str) -> bool:
    sym = (symbol or "").upper().strip()
    return any(g["sym"] == sym and int(g["tf"]) == 30 for g in V18_GOS)
