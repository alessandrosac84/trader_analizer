"""
services/acoes_go_paths.py — paths 🟢 GO de ações B3 (v3 + v4 + v5 + v6 mega).

Módulo isolado: magics 20260810+ (NÃO colide com WIN/WDO 202607xx).
Régua: net ≥ +0,10 R · PF ≥ 1,25 · cons ≥ 55% · OOS+ · n ≥ 40.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

LOG = Path(__file__).resolve().parents[1] / "logs"

# Magics exclusivos ações (faixa 202608xx)
GO_PATHS: list[dict] = [
    # VALE3 — 9 GOs v3
    {"setup": "VALE3_ORB30_V15", "symbol": "VALE3", "tf": 15, "magic": 20260810,
     "net": 0.274, "pf": 2.69, "cons": 68, "n": 71, "oos_net": 0.263,
     "family": "ORB", "one_per_day": True, "auto": True},
    {"setup": "VALE3_ORB30_GAP_V13", "symbol": "VALE3", "tf": 15, "magic": 20260811,
     "net": 0.216, "pf": 2.0, "cons": 57, "n": 85, "oos_net": 0.21,
     "family": "ORB", "one_per_day": True, "auto": True},
    {"setup": "VALE3_ORB30_GAP_V11", "symbol": "VALE3", "tf": 15, "magic": 20260812,
     "net": 0.191, "pf": 1.9, "cons": 55, "n": 122, "oos_net": 0.216,
     "family": "ORB", "one_per_day": True, "auto": True},
    {"setup": "VALE3_IMP_DAY_M5", "symbol": "VALE3", "tf": 5, "magic": 20260813,
     "net": 0.180, "pf": 1.51, "cons": 59, "n": 102, "oos_net": 0.435,
     "family": "IMP", "one_per_day": False, "auto": True},
    {"setup": "VALE3_VWAPC_AM", "symbol": "VALE3", "tf": 15, "magic": 20260814,
     "net": 0.139, "pf": 1.32, "cons": 65, "n": 489, "oos_net": 0.246,
     "family": "VWAP", "one_per_day": False, "auto": True},
    {"setup": "VALE3_GAPC_G30", "symbol": "VALE3", "tf": 15, "magic": 20260815,
     "net": 0.133, "pf": 1.4, "cons": 67, "n": 280, "oos_net": 0.301,
     "family": "GAP", "one_per_day": True, "auto": True},
    {"setup": "VALE3_GAPC_G30_MT", "symbol": "VALE3", "tf": 15, "magic": 20260816,
     "net": 0.120, "pf": 1.35, "cons": 63, "n": 246, "oos_net": 0.256,
     "family": "GAP", "one_per_day": True, "auto": True},
    {"setup": "VALE3_GAPC_G45", "symbol": "VALE3", "tf": 15, "magic": 20260817,
     "net": 0.112, "pf": 1.33, "cons": 60, "n": 251, "oos_net": 0.245,
     "family": "GAP", "one_per_day": True, "auto": True},
    {"setup": "VALE3_GAPC_G60", "symbol": "VALE3", "tf": 15, "magic": 20260818,
     "net": 0.111, "pf": 1.33, "cons": 59, "n": 219, "oos_net": 0.263,
     "family": "GAP", "one_per_day": True, "auto": True},
    # BBAS3 — 2 GOs v3
    {"setup": "BBAS3_ORB30_V13", "symbol": "BBAS3", "tf": 15, "magic": 20260819,
     "net": 0.145, "pf": 1.73, "cons": 62, "n": 91, "oos_net": 0.087,
     "family": "ORB", "one_per_day": True, "auto": True},
    {"setup": "BBAS3_ORB30_TP15", "symbol": "BBAS3", "tf": 15, "magic": 20260820,
     "net": 0.141, "pf": 1.71, "cons": 62, "n": 91, "oos_net": 0.089,
     "family": "ORB", "one_per_day": True, "auto": True},
    # WEGE3 — 3 GOs v3
    {"setup": "WEGE3_INS_AM", "symbol": "WEGE3", "tf": 15, "magic": 20260821,
     "net": 0.178, "pf": 1.4, "cons": 58, "n": 90, "oos_net": 0.084,
     "family": "INS", "one_per_day": False, "auto": True},
    {"setup": "WEGE3_GAPC_G30_MT", "symbol": "WEGE3", "tf": 15, "magic": 20260822,
     "net": 0.144, "pf": 1.41, "cons": 57, "n": 121, "oos_net": 0.165,
     "family": "GAP", "one_per_day": True, "auto": True},
    {"setup": "WEGE3_GAPC_G30", "symbol": "WEGE3", "tf": 15, "magic": 20260823,
     "net": 0.112, "pf": 1.31, "cons": 59, "n": 115, "oos_net": 0.182,
     "family": "GAP", "one_per_day": True, "auto": True},
    # PETR4 — 2 GOs v4 (primeiro GO do paper)
    {"setup": "PETR4_OUT_DAY_H1", "symbol": "PETR4", "tf": 60, "magic": 20260824,
     "net": 0.170, "pf": 1.58, "cons": 55, "n": 67, "oos_net": 0.236,
     "family": "OUT", "one_per_day": False, "auto": True},
    {"setup": "PETR4_VWAP_OPEN_AM", "symbol": "PETR4", "tf": 15, "magic": 20260825,
     "net": 0.117, "pf": 1.25, "cons": 58, "n": 134, "oos_net": 0.107,
     "family": "VWAP", "one_per_day": False, "auto": True},
    # VALE3 — 20 GOs v4
    {"setup": "VALE3_ORB30_V15_TP20", "symbol": "VALE3", "tf": 15, "magic": 20260826,
     "net": 0.274, "pf": 2.69, "cons": 68, "n": 71, "oos_net": 0.263,
     "family": "ORB", "one_per_day": True, "auto": True},
    {"setup": "VALE3_IMP_AM_M5", "symbol": "VALE3", "tf": 5, "magic": 20260827,
     "net": 0.265, "pf": 1.83, "cons": 62, "n": 69, "oos_net": 0.447,
     "family": "IMP", "one_per_day": False, "auto": True},
    {"setup": "VALE3_OUT_AM", "symbol": "VALE3", "tf": 15, "magic": 20260828,
     "net": 0.245, "pf": 1.57, "cons": 56, "n": 75, "oos_net": 0.447,
     "family": "OUT", "one_per_day": False, "auto": True},
    {"setup": "VALE3_IMP_DAY_MT_M5", "symbol": "VALE3", "tf": 5, "magic": 20260829,
     "net": 0.237, "pf": 1.75, "cons": 65, "n": 102, "oos_net": 0.532,
     "family": "IMP", "one_per_day": False, "auto": True},
    {"setup": "VALE3_ORB45_V15", "symbol": "VALE3", "tf": 15, "magic": 20260830,
     "net": 0.234, "pf": 2.3, "cons": 64, "n": 81, "oos_net": 0.165,
     "family": "ORB", "one_per_day": True, "auto": True},
    {"setup": "VALE3_ORB30_GAP_TP20", "symbol": "VALE3", "tf": 15, "magic": 20260831,
     "net": 0.216, "pf": 2.0, "cons": 57, "n": 85, "oos_net": 0.21,
     "family": "ORB", "one_per_day": True, "auto": True},
    {"setup": "VALE3_ORB30_GAP_V13_TP15", "symbol": "VALE3", "tf": 15, "magic": 20260832,
     "net": 0.210, "pf": 1.97, "cons": 57, "n": 85, "oos_net": 0.199,
     "family": "ORB", "one_per_day": True, "auto": True},
    {"setup": "VALE3_VWAPC_AM_V14", "symbol": "VALE3", "tf": 15, "magic": 20260833,
     "net": 0.186, "pf": 1.46, "cons": 65, "n": 359, "oos_net": 0.367,
     "family": "VWAP", "one_per_day": False, "auto": True},
    {"setup": "VALE3_ORB30_GAP_V11_TP15", "symbol": "VALE3", "tf": 15, "magic": 20260834,
     "net": 0.181, "pf": 1.85, "cons": 55, "n": 122, "oos_net": 0.205,
     "family": "ORB", "one_per_day": True, "auto": True},
    {"setup": "VALE3_ORB45_GAP_V11", "symbol": "VALE3", "tf": 15, "magic": 20260835,
     "net": 0.166, "pf": 1.77, "cons": 57, "n": 136, "oos_net": 0.143,
     "family": "ORB", "one_per_day": True, "auto": True},
    {"setup": "VALE3_ORB45_V13", "symbol": "VALE3", "tf": 15, "magic": 20260836,
     "net": 0.142, "pf": 1.59, "cons": 55, "n": 128, "oos_net": 0.107,
     "family": "ORB", "one_per_day": True, "auto": True},
    {"setup": "VALE3_GAPC_G35", "symbol": "VALE3", "tf": 15, "magic": 20260837,
     "net": 0.131, "pf": 1.39, "cons": 65, "n": 272, "oos_net": 0.265,
     "family": "GAP", "one_per_day": True, "auto": True},
    {"setup": "VALE3_GAPC_G25", "symbol": "VALE3", "tf": 15, "magic": 20260838,
     "net": 0.127, "pf": 1.38, "cons": 63, "n": 291, "oos_net": 0.26,
     "family": "GAP", "one_per_day": True, "auto": True},
    {"setup": "VALE3_GAPC_G30_RR20", "symbol": "VALE3", "tf": 15, "magic": 20260839,
     "net": 0.119, "pf": 1.34, "cons": 58, "n": 280, "oos_net": 0.336,
     "family": "GAP", "one_per_day": True, "auto": True},
    {"setup": "VALE3_GAPC_G75", "symbol": "VALE3", "tf": 15, "magic": 20260840,
     "net": 0.118, "pf": 1.36, "cons": 61, "n": 184, "oos_net": 0.221,
     "family": "GAP", "one_per_day": True, "auto": True},
    {"setup": "VALE3_GAPC_G35_MT", "symbol": "VALE3", "tf": 15, "magic": 20260841,
     "net": 0.117, "pf": 1.34, "cons": 59, "n": 237, "oos_net": 0.238,
     "family": "GAP", "one_per_day": True, "auto": True},
    {"setup": "VALE3_GAPC_G30_TP20", "symbol": "VALE3", "tf": 15, "magic": 20260842,
     "net": 0.114, "pf": 1.32, "cons": 57, "n": 280, "oos_net": 0.349,
     "family": "GAP", "one_per_day": True, "auto": True},
    {"setup": "VALE3_GAPC_G25_MT", "symbol": "VALE3", "tf": 15, "magic": 20260843,
     "net": 0.107, "pf": 1.31, "cons": 58, "n": 255, "oos_net": 0.231,
     "family": "GAP", "one_per_day": True, "auto": True},
    {"setup": "VALE3_GAPC_G60_TP20", "symbol": "VALE3", "tf": 15, "magic": 20260844,
     "net": 0.104, "pf": 1.3, "cons": 56, "n": 219, "oos_net": 0.319,
     "family": "GAP", "one_per_day": True, "auto": True},
    {"setup": "VALE3_GAPC_G60_RR20", "symbol": "VALE3", "tf": 15, "magic": 20260845,
     "net": 0.100, "pf": 1.29, "cons": 56, "n": 219, "oos_net": 0.296,
     "family": "GAP", "one_per_day": True, "auto": True},
    # BBAS3 — 4 GOs v4
    {"setup": "BBAS3_ORB30_LATE", "symbol": "BBAS3", "tf": 15, "magic": 20260846,
     "net": 0.171, "pf": 1.93, "cons": 60, "n": 101, "oos_net": 0.203,
     "family": "ORB", "one_per_day": True, "auto": True},
    {"setup": "BBAS3_ORB30_V13_TP20", "symbol": "BBAS3", "tf": 15, "magic": 20260847,
     "net": 0.148, "pf": 1.75, "cons": 63, "n": 90, "oos_net": 0.087,
     "family": "ORB", "one_per_day": True, "auto": True},
    {"setup": "BBAS3_ORB45_V13", "symbol": "BBAS3", "tf": 15, "magic": 20260848,
     "net": 0.141, "pf": 1.7, "cons": 67, "n": 118, "oos_net": 0.146,
     "family": "ORB", "one_per_day": True, "auto": True},
    {"setup": "BBAS3_ORB30_RR20", "symbol": "BBAS3", "tf": 15, "magic": 20260849,
     "net": 0.132, "pf": 1.67, "cons": 61, "n": 90, "oos_net": 0.084,
     "family": "ORB", "one_per_day": True, "auto": True},
    # WEGE3 — 5 GOs v4
    {"setup": "WEGE3_NR5_AM_MT", "symbol": "WEGE3", "tf": 15, "magic": 20260850,
     "net": 0.151, "pf": 1.33, "cons": 65, "n": 80, "oos_net": 0.108,
     "family": "NR", "one_per_day": False, "auto": True},
    {"setup": "WEGE3_GAPC_G30_AM", "symbol": "WEGE3", "tf": 15, "magic": 20260851,
     "net": 0.149, "pf": 1.41, "cons": 56, "n": 94, "oos_net": 0.226,
     "family": "GAP", "one_per_day": True, "auto": True},
    {"setup": "WEGE3_HL_AM_MT", "symbol": "WEGE3", "tf": 15, "magic": 20260852,
     "net": 0.119, "pf": 1.28, "cons": 59, "n": 169, "oos_net": 0.216,
     "family": "HL", "one_per_day": False, "auto": True},
    {"setup": "WEGE3_GAPC_G25_MT", "symbol": "WEGE3", "tf": 15, "magic": 20260853,
     "net": 0.119, "pf": 1.33, "cons": 57, "n": 134, "oos_net": 0.135,
     "family": "GAP", "one_per_day": True, "auto": True},
    {"setup": "WEGE3_GAPC_G30_MT_H1", "symbol": "WEGE3", "tf": 60, "magic": 20260854,
     "net": 0.103, "pf": 1.44, "cons": 57, "n": 94, "oos_net": 0.128,
     "family": "GAP", "one_per_day": True, "auto": True},
    # ── v5 mega (fracos: PETR4 + BBDC4) — magics 20260856–20260872 ──
    {"setup": "PETR4_ORB15_GAP_V14_LATE", "symbol": "PETR4", "tf": 15, "magic": 20260856,
     "net": 0.283, "pf": 1.96, "cons": 65, "n": 77, "oos_net": 0.086,
     "family": "ORB", "one_per_day": True, "auto": True},
    {"setup": "PETR4_ORB15_GAP_V12_LATE", "symbol": "PETR4", "tf": 15, "magic": 20260857,
     "net": 0.231, "pf": 1.73, "cons": 65, "n": 106, "oos_net": 0.182,
     "family": "ORB", "one_per_day": True, "auto": True},
    {"setup": "PETR4_GAPC_G90_LATE_SOFT", "symbol": "PETR4", "tf": 15, "magic": 20260858,
     "net": 0.158, "pf": 1.38, "cons": 60, "n": 83, "oos_net": 0.268,
     "family": "GAP", "one_per_day": True, "auto": True},
    {"setup": "PETR4_ORB15_V12_LATE", "symbol": "PETR4", "tf": 15, "magic": 20260859,
     "net": 0.151, "pf": 1.44, "cons": 59, "n": 151, "oos_net": 0.195,
     "family": "ORB", "one_per_day": True, "auto": True},
    {"setup": "PETR4_OUT_DAY_V5_LOATR_H1", "symbol": "PETR4", "tf": 60, "magic": 20260860,
     "net": 0.144, "pf": 1.40, "cons": 63, "n": 65, "oos_net": 0.109,
     "family": "OUT", "one_per_day": False, "auto": True},
    {"setup": "PETR4_GAPC_G90_LATE_SOFT_RR18", "symbol": "PETR4", "tf": 15, "magic": 20260861,
     "net": 0.143, "pf": 1.32, "cons": 55, "n": 83, "oos_net": 0.181,
     "family": "GAP", "one_per_day": True, "auto": True},
    {"setup": "BBDC4_VSPIKE_T20_DAY_MT", "symbol": "BBDC4", "tf": 15, "magic": 20260862,
     "net": 0.142, "pf": 1.56, "cons": 56, "n": 81, "oos_net": 0.203,
     "family": "VSPIKE", "one_per_day": False, "auto": True},
    {"setup": "PETR4_OUT_AM_V5_TP15_H1", "symbol": "PETR4", "tf": 60, "magic": 20260863,
     "net": 0.141, "pf": 1.47, "cons": 59, "n": 65, "oos_net": 0.212,
     "family": "OUT", "one_per_day": False, "auto": True},
    {"setup": "BBDC4_VSPIKE_T22_DAY_MT", "symbol": "BBDC4", "tf": 15, "magic": 20260864,
     "net": 0.129, "pf": 1.52, "cons": 55, "n": 64, "oos_net": 0.198,
     "family": "VSPIKE", "one_per_day": False, "auto": True},
    {"setup": "PETR4_OUT_AM_V5_H1", "symbol": "PETR4", "tf": 60, "magic": 20260865,
     "net": 0.122, "pf": 1.40, "cons": 59, "n": 65, "oos_net": 0.141,
     "family": "OUT", "one_per_day": False, "auto": True},
    {"setup": "PETR4_GAPC_G60_SOFT_M5", "symbol": "PETR4", "tf": 5, "magic": 20260866,
     "net": 0.120, "pf": 1.26, "cons": 55, "n": 128, "oos_net": 0.080,
     "family": "GAP", "one_per_day": True, "auto": True},
    {"setup": "PETR4_GAPC_G40_SOFT", "symbol": "PETR4", "tf": 15, "magic": 20260867,
     "net": 0.119, "pf": 1.29, "cons": 58, "n": 239, "oos_net": 0.130,
     "family": "GAP", "one_per_day": True, "auto": True},
    {"setup": "PETR4_GAPC_G90_LATE_SOFT_TP15", "symbol": "PETR4", "tf": 15, "magic": 20260868,
     "net": 0.109, "pf": 1.26, "cons": 62, "n": 83, "oos_net": 0.211,
     "family": "GAP", "one_per_day": True, "auto": True},
    {"setup": "PETR4_GAPC_G40_SOFT_TP15", "symbol": "PETR4", "tf": 15, "magic": 20260869,
     "net": 0.105, "pf": 1.26, "cons": 58, "n": 240, "oos_net": 0.216,
     "family": "GAP", "one_per_day": True, "auto": True},
    {"setup": "PETR4_OUT_AM_V5_RR18_H1", "symbol": "PETR4", "tf": 60, "magic": 20260870,
     "net": 0.103, "pf": 1.34, "cons": 56, "n": 65, "oos_net": 0.129,
     "family": "OUT", "one_per_day": False, "auto": True},
    {"setup": "PETR4_GAPC_G30_AM_TP15", "symbol": "PETR4", "tf": 15, "magic": 20260871,
     "net": 0.101, "pf": 1.25, "cons": 55, "n": 209, "oos_net": 0.216,
     "family": "GAP", "one_per_day": True, "auto": True},
    {"setup": "PETR4_ORB15_V10_LATE", "symbol": "PETR4", "tf": 15, "magic": 20260872,
     "net": 0.100, "pf": 1.28, "cons": 58, "n": 209, "oos_net": 0.098,
     "family": "ORB", "one_per_day": True, "auto": True},
    # ── v6 mega (zeros: ITUB4/ABEV3 + BBDC) — magics 20260873–20260874 ──
    {"setup": "BBDC4_VSPIKE_T24_A12_DAY_V6_MT", "symbol": "BBDC4", "tf": 15, "magic": 20260873,
     "net": 0.119, "pf": 1.39, "cons": 56, "n": 81, "oos_net": 0.228,
     "family": "VSPIKE", "one_per_day": False, "auto": True},
    {"setup": "BBDC4_VSPIKE_T20_A14_DAY_V6_MT", "symbol": "BBDC4", "tf": 15, "magic": 20260874,
     "net": 0.108, "pf": 1.37, "cons": 57, "n": 90, "oos_net": 0.096,
     "family": "VSPIKE", "one_per_day": False, "auto": True},
]


def go_for(symbol: str | None = None) -> list[dict]:
    sym = (symbol or "").upper().strip()
    if not sym:
        return list(GO_PATHS)
    return [p for p in GO_PATHS if p.get("symbol", "").upper() == sym]


def active_path_names(symbol: str | None = None) -> list[str]:
    return [p["setup"] for p in go_for(symbol) if p.get("auto", True)]


def magics() -> set[int]:
    return {int(p["magic"]) for p in GO_PATHS}


def setup_by_magic(magic: int) -> str | None:
    m = int(magic or 0)
    for p in GO_PATHS:
        if int(p["magic"]) == m:
            return p.get("setup")
    return None


def path_by_magic(magic: int) -> dict | None:
    m = int(magic or 0)
    for p in GO_PATHS:
        if int(p["magic"]) == m:
            return p
    return None


def latest_ranking_csv() -> Path | None:
    cands = sorted(
        list(LOG.glob("backtest_acoes_v6_mega_ranking_*.csv"))
        + list(LOG.glob("backtest_acoes_v5_mega_ranking_*.csv"))
        + list(LOG.glob("backtest_acoes_v4_mega_ranking_*.csv"))
        + list(LOG.glob("backtest_acoes_v3_mega_ranking_*.csv"))
        + list(LOG.glob("backtest_acoes_v2_ranking_*.csv")),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return cands[0] if cands else None


def load_ranking(limit: int = 80) -> dict:
    path = latest_ranking_csv()
    if path is None or not path.exists():
        return {"ok": True, "file": None, "rows": [], "summary": {}, "go_paths": GO_PATHS}

    try:
        df = pd.read_csv(path)
    except Exception as exc:
        return {"ok": False, "file": str(path), "error": str(exc), "rows": [],
                "summary": {}, "go_paths": GO_PATHS}

    def _tf_val(x):
        if x is None or (isinstance(x, float) and pd.isna(x)):
            return 15
        if isinstance(x, (int, float)) and not isinstance(x, bool):
            return int(x)
        s = str(x).strip().upper()
        s2 = s.replace("M", "").replace("H", "")
        try:
            return int(float(s2))
        except Exception:
            return 15

    def _num(x):
        if x is None or (isinstance(x, float) and pd.isna(x)):
            return None
        try:
            return float(x)
        except Exception:
            return None

    rows = []
    for _, r in df.iterrows():
        v = str(r.get("veredito", ""))
        n_raw = r.get("n")
        try:
            n_val = int(n_raw) if pd.notna(n_raw) else 0
        except Exception:
            n_val = 0
        rows.append({
            "setup": r.get("setup"),
            "sym": r.get("sym"),
            "tf": _tf_val(r.get("tf")),
            "n": n_val,
            "net": _num(r.get("net")),
            "PF": _num(r.get("PF")),
            "cons": _num(r.get("cons%")),
            "oos_net": _num(r.get("oos_net")),
            "veredito": v,
        })

    def _key(x):
        v = x["veredito"] or ""
        tier = 0 if "🟢" in v else (1 if ("🟡" in v or "PROMISSOR" in v) else 2)
        return (tier, -(x["net"] or -99))

    rows.sort(key=_key)
    rows = rows[:limit]

    summary = {"n": len(df), "go": 0, "yellow": 0, "red": 0, "by_sym": {}}
    for _, r in df.iterrows():
        v = str(r.get("veredito", ""))
        sx = str(r.get("sym", "?"))
        summary["by_sym"].setdefault(sx, {"n": 0, "go": 0, "yellow": 0})
        summary["by_sym"][sx]["n"] += 1
        if "🟢" in v:
            summary["go"] += 1
            summary["by_sym"][sx]["go"] += 1
        elif "🟡" in v or "PROMISSOR" in v:
            summary["yellow"] += 1
            summary["by_sym"][sx]["yellow"] += 1
        else:
            summary["red"] += 1

    return {
        "ok": True,
        "file": path.name,
        "rows": rows,
        "summary": summary,
        "go_paths": GO_PATHS,
    }
