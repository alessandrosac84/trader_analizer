"""
Telemetria de near-miss dos GOs crypto (sem baixar régua).

Classifica paths armados: fora da janela horária vs na janela sem setup.
Usado no HB / auto-check para explicar SCAN sem fill.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

# Janelas (hora do servidor IC Markets, mesma base dos setups).
# (0, 24) = 24h. Discovery não lista aqui — tratado como 24h.
_PATH_WINDOW = {
    "ORDER_FLOW": (0, 24),
    "LONDON_HANDOFF": (9, 11),
    "LH_047_D18": (9, 11),
    "LH_049_D19": (9, 11),
    "LH_048_D19": (9, 11),
    "EUR_LH_047_TP165": (9, 11),
    "US_DRIFT": (0, 24),
    "RND_FADE_065": (0, 24),
    "RND_FADE_075_MT": (0, 24),
    "RND_FADE_MT": (0, 24),
    "RND_FADE_07": (0, 24),
    "RND_FADE_TIGHT": (0, 24),
    "XAU_IMP_CONT_20": (9, 18),
    "XAU_NR4_1016_H4": (10, 16),
    "XAU_PDH_H4": (10, 16),
    "XAU_PDH_1115": (11, 15),
    "XAU_HL_24H": (0, 24),
    "XAU_INS_AM": (9, 12),
    "INSIDE_H4": (9, 14),
    "BTC_NR5_0915": (9, 15),
    "BTC_NR5_1016": (10, 16),
    "BTC_NR4_1016": (10, 16),
    "BTC_PDH_H4": (10, 16),
    "INSIDE_1015_V13": (10, 15),
    "BTC_INS_1117_V13": (11, 17),
    "BTC_INS_1017_V13": (10, 17),
    "BTC_INS_1218_V13": (12, 18),
    "BTC_INS_V135_0916": (9, 16),
    "BTC_INS_0816_V13": (8, 16),
    "INS_0918_V13": (9, 18),
    "INSIDE_1015": (10, 15),
    "BTC_H4_PB_EMA": (10, 18),
    "BTC_INS_24H_V13": (0, 24),
    "ETH_IMP_CONT_24H": (0, 24),
    "GBP_INS_AM_V13": (9, 12),
    "GBP_INS_1017_V13": (10, 17),
    "EUR_PDH_H4": (10, 16),
    "EUR_IMP_CONT_24H_MT": (0, 24),
    "PULLBACK": (0, 24),
}


def broker_hour_from_candles(candles) -> Optional[float]:
    """Hora (float) da última barra FECHADA (timestamp MT5 = hora servidor)."""
    if not candles or len(candles) < 2:
        return None
    try:
        t = int(candles[-2]["time"])  # última fechada (última pode estar em formação)
        dt = datetime.utcfromtimestamp(t)
        return dt.hour + dt.minute / 60.0
    except Exception:
        return None


def _in_window(hh: float, lo: float, hi: float) -> bool:
    if hi >= 24.0 and lo <= 0.0:
        return True
    if lo <= hi:
        return lo <= hh < hi
    return hh >= lo or hh < hi


def path_window(name: str):
    n = (name or "").upper().strip()
    if n in _PATH_WINDOW:
        return _PATH_WINDOW[n]
    # Discovery feature-cond / tags desconhecidas → 24h
    if "_L_" in n or "_S_" in n or n.endswith("_DISC"):
        return (0, 24)
    return (0, 24)


def summarize_near_miss(symbol: str, candles_m15, fired_probe: Optional[dict] = None) -> dict:
    """
    Resumo para HB/UI. fired_probe opcional: {tag, acao} se algum GO disparou
    (ex.: execute falhou depois).
    """
    from services.crypto_config import active_trade_paths

    paths = active_trade_paths(symbol)
    hh = broker_hour_from_candles(candles_m15)
    in_win, out_win = [], []
    if hh is None:
        return {
            "broker_hour": None,
            "n_armed": len(paths),
            "n_in_window": 0,
            "n_out_window": len(paths),
            "in_window": [],
            "out_window": paths[:12],
            "fired": fired_probe,
            "hint": "sem candles M15 p/ hora broker",
        }
    for p in paths:
        lo, hi = path_window(p)
        if _in_window(hh, lo, hi):
            in_win.append(p)
        else:
            out_win.append(p)
    hint = None
    if fired_probe:
        hint = f"GO {fired_probe.get('tag')} disparou mas fill falhou"
    elif not in_win:
        hint = f"hora broker {hh:.1f}h — todos os GOs fora da janela"
    elif len(in_win) <= 3 and len(out_win) >= 5:
        hint = (f"hora broker {hh:.1f}h — só {len(in_win)} path(s) 24h/OF na janela; "
                f"{len(out_win)} diurnos dormindo")
    else:
        hint = (f"hora broker {hh:.1f}h — {len(in_win)} na janela sem setup "
                f"(inside/vol/H4/conds)")
    return {
        "broker_hour": round(hh, 2),
        "n_armed": len(paths),
        "n_in_window": len(in_win),
        "n_out_window": len(out_win),
        "in_window": in_win[:16],
        "out_window": out_win[:16],
        "fired": fired_probe,
        "hint": hint,
    }


def dry_probe_signals(symbol: str, candles_m15, candles_of=None, include_discovery=False) -> list:
    """
    Avalia GOs sem executar. Retorna lista de {tag, status, detail}.
    status: FIRE|MISS|SKIP_WINDOW|ERR
    Discovery é opcional (pesado) — use include_discovery=True só sob demanda.
    """
    from services.crypto_config import active_trade_paths
    from services.crypto_orderflow import signal as of_signal
    from services import crypto_edge_setups as es

    sym = (symbol or "").upper().strip()
    hh = broker_hour_from_candles(candles_m15)
    out: list[dict[str, Any]] = []
    armed = set(active_trade_paths(sym))

    def _add(tag, status, detail=""):
        out.append({"tag": tag, "status": status, "detail": detail})

    # ORDER_FLOW
    if "ORDER_FLOW" in armed and candles_of:
        try:
            sig = of_signal(candles_of, sym)
            if sig:
                _add("ORDER_FLOW", "FIRE", str(sig[0]))
            else:
                _add("ORDER_FLOW", "MISS", "delta/VA/vol")
        except Exception as exc:
            _add("ORDER_FLOW", "ERR", str(exc))

    # Edge setups com janela conhecida
    _PROBES = [
        ("BTC_INS_24H_V13", es.btc_ins_24h_v13_enabled, es.btc_ins_24h_v13_signal),
        ("ETH_IMP_CONT_24H", es.eth_imp_cont_24h_enabled, es.eth_imp_cont_24h_signal),
        ("XAU_HL_24H", es.xau_hl_24h_enabled, es.xau_hl_24h_signal),
        ("EUR_IMP_CONT_24H_MT", es.eur_imp_cont_24h_mt_enabled, es.eur_imp_cont_24h_mt_signal),
        ("INS_0918_V13", es.ins_0918_v13_enabled, es.ins_0918_v13_signal),
        ("BTC_INS_1218_V13", es.btc_ins_1218_v13_enabled, es.btc_ins_1218_v13_signal),
        ("INSIDE_1015", es.inside_1015_enabled, es.inside_1015_signal),
        ("INSIDE_H4", es.inside_h4_enabled, es.inside_h4_signal),
        ("XAU_NR4_1016_H4", es.xau_nr4_1016_h4_enabled, es.xau_nr4_1016_h4_signal),
        ("XAU_PDH_H4", es.xau_pdh_h4_enabled, es.xau_pdh_h4_signal),
        ("EUR_PDH_H4", es.eur_pdh_h4_enabled, es.eur_pdh_h4_signal),
        ("GBP_INS_1017_V13", es.gbp_ins_1017_v13_enabled, es.gbp_ins_1017_v13_signal),
        ("GBP_INS_AM_V13", es.gbp_ins_am_v13_enabled, es.gbp_ins_am_v13_signal),
    ]
    for tag, en, fn in _PROBES:
        if not en(sym):
            continue
        lo, hi = path_window(tag)
        if hh is not None and not _in_window(hh, lo, hi):
            _add(tag, "SKIP_WINDOW", f"hh={hh:.1f} precisa {lo}-{hi}")
            continue
        try:
            sig = fn(candles_m15, sym) if candles_m15 else None
            if sig:
                _add(tag, "FIRE", str(sig[0]))
            else:
                _add(tag, "MISS", "setup")
        except Exception as exc:
            _add(tag, "ERR", str(exc))

    if include_discovery:
        try:
            from services.crypto_discovery_paths import discovery_go_paths, eval_path
            for p in discovery_go_paths(sym)[:3]:  # top-3 ranking, não todos
                tag = p.get("path") or p.get("name")
                try:
                    sig = eval_path(candles_m15, p) if candles_m15 else None
                    if sig:
                        _add(tag, "FIRE", str(sig[0]))
                    else:
                        _add(tag, "MISS", "feature-cond")
                except Exception as exc:
                    _add(tag, "ERR", str(exc))
        except Exception as exc:
            _add("DISCOVERY", "ERR", str(exc))

    return out
