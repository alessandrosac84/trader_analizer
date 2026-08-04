"""
crypto_v15_gos.py — 37 GOs BTC/ETH da bateria v15 (31/07/2026).

Régua: net≥+0,10 R · PF≥1,25 · cons≥55% · OOS segura · n≥40.
Ordenados por net (cascade: melhores primeiro).
Não altera paths v14/anteriores — só adiciona.
"""
from __future__ import annotations

from services.crypto_edge_setups import (
    _pdh_h4_core, _hl_h4_core, _nr_h4_core, _inside_h4_core,
    _engulf_h4_core, _ema_reclaim_core, _vwap_cont_core, _h4_pb_ema_core,
    _session_dow_ok,
)

# name -> params
# kind: inside|nr|hl|pdh|engulf|ema_recl|vwap_cont|h4_pb
# session: None|mt|we|fri_sun|fri_mon
# tf: 15|30
V15_GOS = [
    # BTC M15 — ranking net desc
    {"name": "BTC_INS_NY_1317", "sym": "BTCUSD", "tf": 15, "kind": "inside",
     "vol": 1.3, "hh0": 13.0, "hh1": 17.0, "session": None, "tp_r": 1.5,
     "title": "inside vol≥1.3× + H4 · 13–17h"},
    {"name": "BTC_HL_ASIA_MT", "sym": "BTCUSD", "tf": 15, "kind": "hl",
     "vol": 1.4, "hh0": 0.0, "hh1": 8.0, "session": "mt", "tp_r": 1.5,
     "title": "HL vol≥1.4× + H4 · 00–08h · seg–qui"},
    {"name": "BTC_FRI_MON_NR4", "sym": "BTCUSD", "tf": 15, "kind": "nr",
     "n": 4, "vol": 1.4, "hh0": 0.0, "hh1": 24.0, "session": "fri_mon", "tp_r": 1.5,
     "title": "NR4 vol≥1.4× + H4 · sex18–seg08"},
    {"name": "BTC_NR4_NY_1317", "sym": "BTCUSD", "tf": 15, "kind": "nr",
     "n": 4, "vol": 1.4, "hh0": 13.0, "hh1": 17.0, "session": None, "tp_r": 1.5,
     "title": "NR4 vol≥1.4× + H4 · 13–17h"},
    {"name": "BTC_FRI_SUN_NR4", "sym": "BTCUSD", "tf": 15, "kind": "nr",
     "n": 4, "vol": 1.4, "hh0": 0.0, "hh1": 24.0, "session": "fri_sun", "tp_r": 1.5,
     "title": "NR4 vol≥1.4× + H4 · sex18–dom"},
    {"name": "BTC_HL_ASIA_V15", "sym": "BTCUSD", "tf": 15, "kind": "hl",
     "vol": 1.5, "hh0": 0.0, "hh1": 8.0, "session": None, "tp_r": 1.5,
     "title": "HL vol≥1.5× + H4 · 00–08h"},
    {"name": "BTC_WE_NR5_V14", "sym": "BTCUSD", "tf": 15, "kind": "nr",
     "n": 5, "vol": 1.4, "hh0": 0.0, "hh1": 24.0, "session": "we", "tp_r": 1.5,
     "title": "NR5 vol≥1.4× + H4 · sáb/dom"},
    {"name": "BTC_HL_OVN_MT", "sym": "BTCUSD", "tf": 15, "kind": "hl",
     "vol": 1.4, "hh0": 18.0, "hh1": 8.0, "session": "mt", "tp_r": 1.5,
     "title": "HL vol≥1.4× + H4 · 18–08h · seg–qui"},
    {"name": "BTC_WE_NR4_V14", "sym": "BTCUSD", "tf": 15, "kind": "nr",
     "n": 4, "vol": 1.4, "hh0": 0.0, "hh1": 24.0, "session": "we", "tp_r": 1.5,
     "title": "NR4 vol≥1.4× + H4 · sáb/dom"},
    {"name": "BTC_HL_NIGHT_MT", "sym": "BTCUSD", "tf": 15, "kind": "hl",
     "vol": 1.4, "hh0": 20.0, "hh1": 10.0, "session": "mt", "tp_r": 1.5,
     "title": "HL vol≥1.4× + H4 · 20–10h · seg–qui"},
    {"name": "BTC_HL_NIGHT_V16", "sym": "BTCUSD", "tf": 15, "kind": "hl",
     "vol": 1.6, "hh0": 20.0, "hh1": 10.0, "session": None, "tp_r": 1.5,
     "title": "HL vol≥1.6× + H4 · 20–10h"},
    {"name": "BTC_NR5_LN_0817", "sym": "BTCUSD", "tf": 15, "kind": "nr",
     "n": 5, "vol": 1.4, "hh0": 8.0, "hh1": 17.0, "session": None, "tp_r": 1.5,
     "title": "NR5 vol≥1.4× + H4 · 08–17h"},
    {"name": "BTC_WE_NR4_TP20", "sym": "BTCUSD", "tf": 15, "kind": "nr",
     "n": 4, "vol": 1.4, "hh0": 0.0, "hh1": 24.0, "session": "we", "tp_r": 2.0,
     "title": "NR4 vol≥1.4× · TP 2R · sáb/dom"},
    {"name": "BTC_VWAP_CONT_NY", "sym": "BTCUSD", "tf": 15, "kind": "vwap_cont",
     "vol": 1.3, "dist": 0.3, "hh0": 13.0, "hh1": 17.0, "session": None, "tp_r": 1.5,
     "title": "VWAP cont + H4 · 13–17h"},
    {"name": "BTC_NR7_24H_V14", "sym": "BTCUSD", "tf": 15, "kind": "nr",
     "n": 7, "vol": 1.4, "hh0": 0.0, "hh1": 24.0, "session": None, "tp_r": 1.5,
     "title": "NR7 vol≥1.4× + H4 · 24h"},
    {"name": "BTC_PDH_LON_0713", "sym": "BTCUSD", "tf": 15, "kind": "pdh",
     "vol": 1.3, "hh0": 7.0, "hh1": 13.0, "session": None, "tp_r": 1.5,
     "title": "PDH/PDL vol≥1.3× + H4 · 07–13h"},
    {"name": "BTC_PDH_LN_0817", "sym": "BTCUSD", "tf": 15, "kind": "pdh",
     "vol": 1.2, "hh0": 8.0, "hh1": 17.0, "session": None, "tp_r": 1.5,
     "title": "PDH/PDL vol≥1.2× + H4 · 08–17h"},
    {"name": "BTC_PDH_LON_0812", "sym": "BTCUSD", "tf": 15, "kind": "pdh",
     "vol": 1.2, "hh0": 8.0, "hh1": 12.0, "session": None, "tp_r": 1.5,
     "title": "PDH/PDL vol≥1.2× + H4 · 08–12h"},
    {"name": "BTC_EMA_RECL_NY", "sym": "BTCUSD", "tf": 15, "kind": "ema_recl",
     "vol": 1.3, "hh0": 13.0, "hh1": 17.0, "session": None, "tp_r": 1.5,
     "title": "EMA21 reclaim + H4 · 13–17h"},
    {"name": "BTC_HL_NIGHT_V15", "sym": "BTCUSD", "tf": 15, "kind": "hl",
     "vol": 1.5, "hh0": 20.0, "hh1": 10.0, "session": None, "tp_r": 1.5,
     "title": "HL vol≥1.5× + H4 · 20–10h"},
    {"name": "BTC_VWAP_CONT_24H_MT", "sym": "BTCUSD", "tf": 15, "kind": "vwap_cont",
     "vol": 1.3, "dist": 0.3, "hh0": 0.0, "hh1": 24.0, "session": "mt", "tp_r": 1.5,
     "title": "VWAP cont + H4 · 24h · seg–qui"},
    {"name": "BTC_FRI_MON_INS", "sym": "BTCUSD", "tf": 15, "kind": "inside",
     "vol": 1.3, "hh0": 0.0, "hh1": 24.0, "session": "fri_mon", "tp_r": 1.5,
     "title": "inside vol≥1.3× + H4 · sex18–seg08"},
    {"name": "BTC_VWAP_CONT_LON", "sym": "BTCUSD", "tf": 15, "kind": "vwap_cont",
     "vol": 1.3, "dist": 0.3, "hh0": 8.0, "hh1": 12.0, "session": None, "tp_r": 1.5,
     "title": "VWAP cont + H4 · 08–12h"},
    {"name": "BTC_H4_PB_EMA_NY", "sym": "BTCUSD", "tf": 15, "kind": "h4_pb",
     "hh0": 13.0, "hh1": 17.0, "session": None, "tp_r": 2.0,
     "title": "pullback EMA + H4 · 13–17h"},
    # BTC M30
    {"name": "BTC_ENGULF_24H_M30", "sym": "BTCUSD", "tf": 30, "kind": "engulf",
     "vol": 1.4, "hh0": 0.0, "hh1": 24.0, "session": None, "tp_r": 1.5,
     "title": "engulf vol≥1.4× + H4 · 24h · M30"},
    {"name": "BTC_PDH_ASIA_M30", "sym": "BTCUSD", "tf": 30, "kind": "pdh",
     "vol": 1.2, "hh0": 0.0, "hh1": 8.0, "session": None, "tp_r": 1.5,
     "title": "PDH/PDL vol≥1.2× + H4 · 00–08h · M30"},
    {"name": "BTC_HL_24H_MT_M30", "sym": "BTCUSD", "tf": 30, "kind": "hl",
     "vol": 1.4, "hh0": 0.0, "hh1": 24.0, "session": "mt", "tp_r": 1.5,
     "title": "HL vol≥1.4× + H4 · 24h · seg–qui · M30"},
    {"name": "BTC_EMA_RECL_24H_M30", "sym": "BTCUSD", "tf": 30, "kind": "ema_recl",
     "vol": 1.3, "hh0": 0.0, "hh1": 24.0, "session": None, "tp_r": 1.5,
     "title": "EMA21 reclaim + H4 · 24h · M30"},
    # ETH M15
    {"name": "ETH_HL_ASIA_MT", "sym": "ETHUSD", "tf": 15, "kind": "hl",
     "vol": 1.4, "hh0": 0.0, "hh1": 8.0, "session": "mt", "tp_r": 1.5,
     "title": "HL vol≥1.4× + H4 · 00–08h · seg–qui"},
    {"name": "ETH_HL_NIGHT_MT", "sym": "ETHUSD", "tf": 15, "kind": "hl",
     "vol": 1.4, "hh0": 20.0, "hh1": 10.0, "session": "mt", "tp_r": 1.5,
     "title": "HL vol≥1.4× + H4 · 20–10h · seg–qui"},
    {"name": "ETH_PDH_ASIA_V13", "sym": "ETHUSD", "tf": 15, "kind": "pdh",
     "vol": 1.3, "hh0": 0.0, "hh1": 8.0, "session": None, "tp_r": 1.5,
     "title": "PDH/PDL vol≥1.3× + H4 · 00–08h"},
    {"name": "ETH_PDH_ASIA_TP20", "sym": "ETHUSD", "tf": 15, "kind": "pdh",
     "vol": 1.3, "hh0": 0.0, "hh1": 8.0, "session": None, "tp_r": 2.0,
     "title": "PDH/PDL vol≥1.3× · TP 2R · 00–08h"},
    {"name": "ETH_HL_24H_MT_V15", "sym": "ETHUSD", "tf": 15, "kind": "hl",
     "vol": 1.5, "hh0": 0.0, "hh1": 24.0, "session": "mt", "tp_r": 1.5,
     "title": "HL vol≥1.5× + H4 · 24h · seg–qui"},
    {"name": "ETH_PDH_NIGHT_MT", "sym": "ETHUSD", "tf": 15, "kind": "pdh",
     "vol": 1.3, "hh0": 20.0, "hh1": 10.0, "session": "mt", "tp_r": 1.5,
     "title": "PDH/PDL vol≥1.3× + H4 · 20–10h · seg–qui"},
    {"name": "ETH_HL_NY_1317", "sym": "ETHUSD", "tf": 15, "kind": "hl",
     "vol": 1.4, "hh0": 13.0, "hh1": 17.0, "session": None, "tp_r": 1.5,
     "title": "HL vol≥1.4× + H4 · 13–17h"},
    {"name": "ETH_HL_24H_MT_TP20", "sym": "ETHUSD", "tf": 15, "kind": "hl",
     "vol": 1.4, "hh0": 0.0, "hh1": 24.0, "session": "mt", "tp_r": 2.0,
     "title": "HL vol≥1.4× · TP 2R · 24h · seg–qui"},
    {"name": "ETH_HL_24H_MT_V14", "sym": "ETHUSD", "tf": 15, "kind": "hl",
     "vol": 1.4, "hh0": 0.0, "hh1": 24.0, "session": "mt", "tp_r": 1.5,
     "title": "HL vol≥1.4× + H4 · 24h · seg–qui"},
]

V15_BY_NAME = {g["name"]: g for g in V15_GOS}


def v15_path_names(symbol: str) -> list:
    sym = (symbol or "").upper().strip()
    return [g["name"] for g in V15_GOS if g["sym"] == sym]


def v15_enabled(name: str, symbol: str) -> bool:
    g = V15_BY_NAME.get(name)
    if not g:
        return False
    return (symbol or "").upper().strip() == g["sym"]


def v15_signal(name: str, candles, symbol: str = ""):
    g = V15_BY_NAME.get(name)
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
    if kind == "ema_recl":
        return _ema_reclaim_core(candles, vol_mult=vol, hh0=hh0, hh1=hh1, tp_r=tp_r)
    if kind == "vwap_cont":
        return _vwap_cont_core(candles, vol_mult=vol, dist=float(g.get("dist", 0.3)),
                               hh0=hh0, hh1=hh1, tp_r=tp_r)
    if kind == "h4_pb":
        return _h4_pb_ema_core(candles, hh0=hh0, hh1=hh1, tp_r=tp_r)
    return None


def v15_cascade_items(symbol: str, tf: int | None = None):
    """Lista (enabled_fn, signal_fn, tag, motivo) para o cascade."""
    sym = (symbol or "").upper().strip()
    items = []
    for g in V15_GOS:
        if g["sym"] != sym:
            continue
        if tf is not None and int(g["tf"]) != int(tf):
            continue
        name = g["name"]
        motivo = f"[{name} M{g['tf']} GO v15] {g['title']}"

        def _make_en(n):
            return lambda s, _n=n: v15_enabled(_n, s)

        def _make_sig(n):
            return lambda c, s="", _n=n: v15_signal(_n, c, s)

        items.append((_make_en(name), _make_sig(name), name, motivo))
    return items


def v15_needs_m30(symbol: str) -> bool:
    sym = (symbol or "").upper().strip()
    return any(g["sym"] == sym and int(g["tf"]) == 30 for g in V15_GOS)
