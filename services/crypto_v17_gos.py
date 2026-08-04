"""
crypto_v17_gos.py — 17 GOs ETH/EUR da bateria v17 (01/08/2026).

Régua: net≥+0,10 R · PF≥1,25 · cons≥55% · OOS segura · n≥40.
Ordenados por net (cascade: melhores primeiro).
Não altera paths v16/anteriores — só adiciona.
GBP: 0 GOs novos nesta bateria (buraco permanece — ver yellows).
"""
from __future__ import annotations

from services.crypto_edge_setups import (
    _pdh_h4_core, _hl_h4_core, _nr_h4_core, _inside_h4_core,
    _engulf_h4_core, _vwap_fade_core, _session_dow_ok,
)

V17_GOS = [
    # ETH M15 — ranking net desc
    {"name": "ETH_INS_DAY", "sym": "ETHUSD", "tf": 15, "kind": "inside",
     "vol": 1.3, "hh0": 10.0, "hh1": 16.0, "session": None, "tp_r": 1.5,
     "title": "inside vol≥1.3× + H4 · 10–16h"},
    {"name": "ETH_HL_24H_MT_V16", "sym": "ETHUSD", "tf": 15, "kind": "hl",
     "vol": 1.6, "hh0": 0.0, "hh1": 24.0, "session": "mt", "tp_r": 1.5,
     "title": "HL vol≥1.6× + H4 · 24h · seg–qui"},
    # EUR M15
    {"name": "EUR_NR4_LON0713_MT", "sym": "EURUSD", "tf": 15, "kind": "nr",
     "n": 4, "vol": 1.4, "hh0": 7.0, "hh1": 13.0, "session": "mt", "tp_r": 1.5,
     "title": "NR4 vol≥1.4× + H4 · 07–13h · seg–qui"},
    {"name": "EUR_NR4_AM", "sym": "EURUSD", "tf": 15, "kind": "nr",
     "n": 4, "vol": 1.4, "hh0": 9.0, "hh1": 12.0, "session": None, "tp_r": 1.5,
     "title": "NR4 vol≥1.4× + H4 · 09–12h"},
    # ETH insides / HL
    {"name": "ETH_INS_LN", "sym": "ETHUSD", "tf": 15, "kind": "inside",
     "vol": 1.3, "hh0": 8.0, "hh1": 17.0, "session": None, "tp_r": 1.5,
     "title": "inside vol≥1.3× + H4 · 08–17h"},
    {"name": "ETH_INS_LN_M30", "sym": "ETHUSD", "tf": 30, "kind": "inside",
     "vol": 1.3, "hh0": 8.0, "hh1": 17.0, "session": None, "tp_r": 1.5,
     "title": "inside vol≥1.3× + H4 · 08–17h · M30"},
    {"name": "ETH_INS_ASIA_MT", "sym": "ETHUSD", "tf": 15, "kind": "inside",
     "vol": 1.3, "hh0": 0.0, "hh1": 8.0, "session": "mt", "tp_r": 1.5,
     "title": "inside vol≥1.3× + H4 · 00–08h · seg–qui"},
    {"name": "ETH_HL_OVN_MT", "sym": "ETHUSD", "tf": 15, "kind": "hl",
     "vol": 1.4, "hh0": 18.0, "hh1": 8.0, "session": "mt", "tp_r": 1.5,
     "title": "HL vol≥1.4× + H4 · 18–08h · seg–qui"},
    {"name": "ETH_HL_NY_TP20", "sym": "ETHUSD", "tf": 15, "kind": "hl",
     "vol": 1.4, "hh0": 13.0, "hh1": 17.0, "session": None, "tp_r": 2.0,
     "title": "HL vol≥1.4× · TP 2R · 13–17h"},
    {"name": "EUR_HL_AM_MT", "sym": "EURUSD", "tf": 15, "kind": "hl",
     "vol": 1.4, "hh0": 9.0, "hh1": 12.0, "session": "mt", "tp_r": 1.5,
     "title": "HL vol≥1.4× + H4 · 09–12h · seg–qui"},
    {"name": "EUR_ENGULF_LON_M30", "sym": "EURUSD", "tf": 30, "kind": "engulf",
     "vol": 1.4, "hh0": 8.0, "hh1": 12.0, "session": None, "tp_r": 1.5,
     "title": "engulf vol≥1.4× + H4 · 08–12h · M30"},
    {"name": "EUR_VWAP_FADE_LN_MT", "sym": "EURUSD", "tf": 15, "kind": "vwap_fade",
     "vol": 1.2, "dist": 1.2, "hh0": 8.0, "hh1": 17.0, "session": "mt", "tp_r": 1.5,
     "title": "VWAP fade + H4 · 08–17h · seg–qui"},
    {"name": "EUR_PDH_LON0713_MT", "sym": "EURUSD", "tf": 15, "kind": "pdh",
     "vol": 1.3, "hh0": 7.0, "hh1": 13.0, "session": "mt", "tp_r": 1.5,
     "title": "PDH/PDL vol≥1.3× + H4 · 07–13h · seg–qui"},
    {"name": "EUR_VWAP_FADE_LN_D14", "sym": "EURUSD", "tf": 15, "kind": "vwap_fade",
     "vol": 1.2, "dist": 1.4, "hh0": 8.0, "hh1": 17.0, "session": None, "tp_r": 1.5,
     "title": "VWAP fade dist≥1.4 ATR + H4 · 08–17h"},
    {"name": "ETH_HL_NY_V15", "sym": "ETHUSD", "tf": 15, "kind": "hl",
     "vol": 1.5, "hh0": 13.0, "hh1": 17.0, "session": None, "tp_r": 1.5,
     "title": "HL vol≥1.5× + H4 · 13–17h"},
    {"name": "ETH_HL_ASIA_V15", "sym": "ETHUSD", "tf": 15, "kind": "hl",
     "vol": 1.5, "hh0": 0.0, "hh1": 8.0, "session": None, "tp_r": 1.5,
     "title": "HL vol≥1.5× + H4 · 00–08h"},
    {"name": "ETH_NR4_24H_V16", "sym": "ETHUSD", "tf": 15, "kind": "nr",
     "n": 4, "vol": 1.6, "hh0": 0.0, "hh1": 24.0, "session": None, "tp_r": 1.5,
     "title": "NR4 vol≥1.6× + H4 · 24h"},
]

V17_BY_NAME = {g["name"]: g for g in V17_GOS}


def v17_path_names(symbol: str) -> list:
    sym = (symbol or "").upper().strip()
    return [g["name"] for g in V17_GOS if g["sym"] == sym]


def v17_enabled(name: str, symbol: str) -> bool:
    g = V17_BY_NAME.get(name)
    if not g:
        return False
    return (symbol or "").upper().strip() == g["sym"]


def v17_signal(name: str, candles, symbol: str = ""):
    g = V17_BY_NAME.get(name)
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
    if kind == "inside":
        return _inside_h4_core(candles, hh0=hh0, hh1=hh1, vol_mult=vol, tp_r=tp_r)
    if kind == "nr":
        return _nr_h4_core(candles, n=int(g.get("n", 5)), vol_mult=vol,
                           hh0=hh0, hh1=hh1, tp_r=tp_r)
    if kind == "hl":
        return _hl_h4_core(candles, vol_mult=vol, hh0=hh0, hh1=hh1, tp_r=tp_r)
    if kind == "pdh":
        return _pdh_h4_core(candles, vol_mult=vol, hh0=hh0, hh1=hh1, tp_r=tp_r)
    if kind == "engulf":
        return _engulf_h4_core(candles, vol_mult=vol, hh0=hh0, hh1=hh1, tp_r=tp_r)
    if kind == "vwap_fade":
        return _vwap_fade_core(candles, vol_mult=vol, dist=float(g.get("dist", 1.2)),
                               hh0=hh0, hh1=hh1, tp_r=tp_r)
    return None


def v17_cascade_items(symbol: str, tf: int | None = None):
    """Lista (enabled_fn, signal_fn, tag, motivo) para o cascade."""
    sym = (symbol or "").upper().strip()
    items = []
    for g in V17_GOS:
        if g["sym"] != sym:
            continue
        if tf is not None and int(g["tf"]) != int(tf):
            continue
        name = g["name"]
        motivo = f"[{name} M{g['tf']} GO v17] {g['title']}"

        def _make_en(n):
            return lambda s, _n=n: v17_enabled(_n, s)

        def _make_sig(n):
            return lambda c, s="", _n=n: v17_signal(_n, c, s)

        items.append((_make_en(name), _make_sig(name), name, motivo))
    return items


def v17_needs_m30(symbol: str) -> bool:
    sym = (symbol or "").upper().strip()
    return any(g["sym"] == sym and int(g["tf"]) == 30 for g in V17_GOS)
