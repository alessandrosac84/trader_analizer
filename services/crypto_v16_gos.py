"""
crypto_v16_gos.py — 27 GOs XAU/EUR/GBP da bateria v16 (31/07/2026).

Régua: net≥+0,10 R · PF≥1,25 · cons≥55% · OOS segura · n≥40.
Ordenados por net (cascade: melhores primeiro).
Não altera paths v15/anteriores — só adiciona.
XAU: TP cap estrutural 30 pts nos estruturais/impulso.
EUR/GBP: pregão weekday (sem FDS).
"""
from __future__ import annotations

from services.crypto_edge_setups import (
    _pdh_h4_core, _hl_h4_core, _nr_h4_core, _inside_h4_core,
    _engulf_h4_core, _vwap_fade_core, _h4_pb_ema_core, _impulse_cont_core,
    _session_dow_ok, _XAU_STRUCT_TP_CAP,
)

V16_GOS = [
    # GBP M15
    {"name": "GBP_INS_DAY", "sym": "GBPUSD", "tf": 15, "kind": "inside",
     "vol": 1.3, "hh0": 10.0, "hh1": 16.0, "session": None, "tp_r": 1.5,
     "title": "inside vol≥1.3× + H4 · 10–16h"},
    # EUR M15
    {"name": "EUR_VWAP_FADE_NY", "sym": "EURUSD", "tf": 15, "kind": "vwap_fade",
     "vol": 1.2, "dist": 1.2, "hh0": 13.0, "hh1": 17.0, "session": None, "tp_r": 1.5,
     "title": "VWAP fade + H4 · 13–17h"},
    # XAU impulso (melhor net estrutural)
    {"name": "XAU_IMP_ASIA", "sym": "XAUUSD", "tf": 15, "kind": "imp",
     "vol": 1.5, "atr_mult": 2.0, "hh0": 0.0, "hh1": 8.0, "session": None, "tp_r": 2.0,
     "tp_cap": _XAU_STRUCT_TP_CAP, "title": "impulso ATR×2 · 00–08h · TP≤30"},
    {"name": "XAU_IMP_LN", "sym": "XAUUSD", "tf": 15, "kind": "imp",
     "vol": 1.5, "atr_mult": 2.0, "hh0": 8.0, "hh1": 17.0, "session": None, "tp_r": 2.0,
     "tp_cap": _XAU_STRUCT_TP_CAP, "title": "impulso ATR×2 · 08–17h · TP≤30"},
    {"name": "XAU_IMP_24H", "sym": "XAUUSD", "tf": 15, "kind": "imp",
     "vol": 1.5, "atr_mult": 2.0, "hh0": 0.0, "hh1": 24.0, "session": None, "tp_r": 2.0,
     "tp_cap": _XAU_STRUCT_TP_CAP, "title": "impulso ATR×2 · 24h · TP≤30"},
    {"name": "XAU_IMP_24H_MT", "sym": "XAUUSD", "tf": 15, "kind": "imp",
     "vol": 1.5, "atr_mult": 2.0, "hh0": 0.0, "hh1": 24.0, "session": "mt", "tp_r": 2.0,
     "tp_cap": _XAU_STRUCT_TP_CAP, "title": "impulso ATR×2 · 24h · seg–qui · TP≤30"},
    {"name": "XAU_IMP_OVN", "sym": "XAUUSD", "tf": 15, "kind": "imp",
     "vol": 1.5, "atr_mult": 2.0, "hh0": 18.0, "hh1": 8.0, "session": None, "tp_r": 2.0,
     "tp_cap": _XAU_STRUCT_TP_CAP, "title": "impulso ATR×2 · 18–08h · TP≤30"},
    # GBP insides
    {"name": "GBP_INS_LN_MT", "sym": "GBPUSD", "tf": 15, "kind": "inside",
     "vol": 1.3, "hh0": 8.0, "hh1": 17.0, "session": "mt", "tp_r": 1.5,
     "title": "inside vol≥1.3× + H4 · 08–17h · seg–qui"},
    # EUR PDH
    {"name": "EUR_PDH_DAY_MT", "sym": "EURUSD", "tf": 15, "kind": "pdh",
     "vol": 1.2, "hh0": 10.0, "hh1": 16.0, "session": "mt", "tp_r": 1.5,
     "title": "PDH/PDL vol≥1.2× + H4 · 10–16h · seg–qui"},
    {"name": "GBP_INS_LN", "sym": "GBPUSD", "tf": 15, "kind": "inside",
     "vol": 1.3, "hh0": 8.0, "hh1": 17.0, "session": None, "tp_r": 1.5,
     "title": "inside vol≥1.3× + H4 · 08–17h"},
    # XAU PDH / HL
    {"name": "XAU_PDH_24H", "sym": "XAUUSD", "tf": 15, "kind": "pdh",
     "vol": 1.2, "hh0": 0.0, "hh1": 24.0, "session": None, "tp_r": 1.5,
     "tp_cap": _XAU_STRUCT_TP_CAP, "title": "PDH/PDL vol≥1.2× + H4 · 24h · TP≤30"},
    {"name": "XAU_HL_24H_TP20", "sym": "XAUUSD", "tf": 15, "kind": "hl",
     "vol": 1.3, "hh0": 0.0, "hh1": 24.0, "session": None, "tp_r": 2.0,
     "tp_cap": _XAU_STRUCT_TP_CAP, "title": "HL vol≥1.3× · TP 2R/cap30 · 24h"},
    {"name": "XAU_PDH_DAY_V13", "sym": "XAUUSD", "tf": 15, "kind": "pdh",
     "vol": 1.3, "hh0": 10.0, "hh1": 16.0, "session": None, "tp_r": 1.5,
     "tp_cap": _XAU_STRUCT_TP_CAP, "title": "PDH/PDL vol≥1.3× + H4 · 10–16h · TP≤30"},
    {"name": "XAU_PDH_NIGHT", "sym": "XAUUSD", "tf": 15, "kind": "pdh",
     "vol": 1.2, "hh0": 20.0, "hh1": 10.0, "session": None, "tp_r": 1.5,
     "tp_cap": _XAU_STRUCT_TP_CAP, "title": "PDH/PDL vol≥1.2× + H4 · 20–10h · TP≤30"},
    {"name": "XAU_PDH_LN", "sym": "XAUUSD", "tf": 15, "kind": "pdh",
     "vol": 1.2, "hh0": 8.0, "hh1": 17.0, "session": None, "tp_r": 1.5,
     "tp_cap": _XAU_STRUCT_TP_CAP, "title": "PDH/PDL vol≥1.2× + H4 · 08–17h · TP≤30"},
    # EUR M30 + M15
    {"name": "EUR_ENGULF_LN_M30", "sym": "EURUSD", "tf": 30, "kind": "engulf",
     "vol": 1.4, "hh0": 8.0, "hh1": 17.0, "session": None, "tp_r": 1.5,
     "title": "engulf vol≥1.4× + H4 · 08–17h · M30"},
    {"name": "EUR_PDH_DAY_V13", "sym": "EURUSD", "tf": 15, "kind": "pdh",
     "vol": 1.3, "hh0": 10.0, "hh1": 16.0, "session": None, "tp_r": 1.5,
     "title": "PDH/PDL vol≥1.3× + H4 · 10–16h"},
    {"name": "EUR_NR4_LON0713", "sym": "EURUSD", "tf": 15, "kind": "nr",
     "n": 4, "vol": 1.4, "hh0": 7.0, "hh1": 13.0, "session": None, "tp_r": 1.5,
     "title": "NR4 vol≥1.4× + H4 · 07–13h"},
    {"name": "EUR_H4_PB_NY_MT", "sym": "EURUSD", "tf": 15, "kind": "h4_pb",
     "hh0": 13.0, "hh1": 17.0, "session": "mt", "tp_r": 2.0,
     "title": "pullback EMA + H4 · 13–17h · seg–qui"},
    {"name": "EUR_NR4_LN_MT", "sym": "EURUSD", "tf": 15, "kind": "nr",
     "n": 4, "vol": 1.4, "hh0": 8.0, "hh1": 17.0, "session": "mt", "tp_r": 1.5,
     "title": "NR4 vol≥1.4× + H4 · 08–17h · seg–qui"},
    {"name": "XAU_PDH_NY", "sym": "XAUUSD", "tf": 15, "kind": "pdh",
     "vol": 1.2, "hh0": 13.0, "hh1": 17.0, "session": None, "tp_r": 1.5,
     "tp_cap": _XAU_STRUCT_TP_CAP, "title": "PDH/PDL vol≥1.2× + H4 · 13–17h · TP≤30"},
    {"name": "XAU_IMP_LN_M30", "sym": "XAUUSD", "tf": 30, "kind": "imp",
     "vol": 1.5, "atr_mult": 2.0, "hh0": 8.0, "hh1": 17.0, "session": None, "tp_r": 2.0,
     "tp_cap": _XAU_STRUCT_TP_CAP, "title": "impulso ATR×2 · 08–17h · M30 · TP≤30"},
    {"name": "EUR_ENGULF_LON_MT", "sym": "EURUSD", "tf": 15, "kind": "engulf",
     "vol": 1.4, "hh0": 8.0, "hh1": 12.0, "session": "mt", "tp_r": 1.5,
     "title": "engulf vol≥1.4× + H4 · 08–12h · seg–qui"},
    {"name": "EUR_ENGULF_LN_MT", "sym": "EURUSD", "tf": 15, "kind": "engulf",
     "vol": 1.4, "hh0": 8.0, "hh1": 17.0, "session": "mt", "tp_r": 1.5,
     "title": "engulf vol≥1.4× + H4 · 08–17h · seg–qui"},
    {"name": "EUR_PDH_LN_MT", "sym": "EURUSD", "tf": 15, "kind": "pdh",
     "vol": 1.3, "hh0": 8.0, "hh1": 17.0, "session": "mt", "tp_r": 1.5,
     "title": "PDH/PDL vol≥1.3× + H4 · 08–17h · seg–qui"},
    {"name": "EUR_H4_PB_NY", "sym": "EURUSD", "tf": 15, "kind": "h4_pb",
     "hh0": 13.0, "hh1": 17.0, "session": None, "tp_r": 2.0,
     "title": "pullback EMA + H4 · 13–17h"},
    {"name": "XAU_PDH_ASIA", "sym": "XAUUSD", "tf": 15, "kind": "pdh",
     "vol": 1.2, "hh0": 0.0, "hh1": 8.0, "session": None, "tp_r": 1.5,
     "tp_cap": _XAU_STRUCT_TP_CAP, "title": "PDH/PDL vol≥1.2× + H4 · 00–08h · TP≤30"},
]

V16_BY_NAME = {g["name"]: g for g in V16_GOS}


def v16_path_names(symbol: str) -> list:
    sym = (symbol or "").upper().strip()
    return [g["name"] for g in V16_GOS if g["sym"] == sym]


def v16_enabled(name: str, symbol: str) -> bool:
    g = V16_BY_NAME.get(name)
    if not g:
        return False
    return (symbol or "").upper().strip() == g["sym"]


def v16_signal(name: str, candles, symbol: str = ""):
    g = V16_BY_NAME.get(name)
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
    tp_cap = g.get("tp_cap")
    if kind == "inside":
        return _inside_h4_core(candles, hh0=hh0, hh1=hh1, vol_mult=vol,
                               tp_r=tp_r, tp_cap=tp_cap)
    if kind == "nr":
        return _nr_h4_core(candles, n=int(g.get("n", 5)), vol_mult=vol,
                           hh0=hh0, hh1=hh1, tp_r=tp_r, tp_cap=tp_cap)
    if kind == "hl":
        return _hl_h4_core(candles, vol_mult=vol, hh0=hh0, hh1=hh1,
                           tp_r=tp_r, tp_cap=tp_cap)
    if kind == "pdh":
        return _pdh_h4_core(candles, vol_mult=vol, hh0=hh0, hh1=hh1,
                            tp_r=tp_r, tp_cap=tp_cap)
    if kind == "engulf":
        return _engulf_h4_core(candles, vol_mult=vol, hh0=hh0, hh1=hh1,
                               tp_r=tp_r, tp_cap=tp_cap)
    if kind == "vwap_fade":
        return _vwap_fade_core(candles, vol_mult=vol, dist=float(g.get("dist", 1.2)),
                               hh0=hh0, hh1=hh1, tp_r=tp_r, tp_cap=tp_cap)
    if kind == "h4_pb":
        return _h4_pb_ema_core(candles, hh0=hh0, hh1=hh1, tp_r=tp_r, tp_cap=tp_cap)
    if kind == "imp":
        return _impulse_cont_core(
            candles, atr_mult=float(g.get("atr_mult", 2.0)), vol_mult=vol,
            hh0=hh0, hh1=hh1, tp_r=tp_r, tp_cap=tp_cap)
    return None


def v16_cascade_items(symbol: str, tf: int | None = None):
    """Lista (enabled_fn, signal_fn, tag, motivo) para o cascade."""
    sym = (symbol or "").upper().strip()
    items = []
    for g in V16_GOS:
        if g["sym"] != sym:
            continue
        if tf is not None and int(g["tf"]) != int(tf):
            continue
        name = g["name"]
        motivo = f"[{name} M{g['tf']} GO v16] {g['title']}"

        def _make_en(n):
            return lambda s, _n=n: v16_enabled(_n, s)

        def _make_sig(n):
            return lambda c, s="", _n=n: v16_signal(_n, c, s)

        items.append((_make_en(name), _make_sig(name), name, motivo))
    return items


def v16_needs_m30(symbol: str) -> bool:
    sym = (symbol or "").upper().strip()
    return any(g["sym"] == sym and int(g["tf"]) == 30 for g in V16_GOS)
