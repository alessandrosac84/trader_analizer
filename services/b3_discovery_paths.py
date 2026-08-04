"""
b3_discovery_paths.py — GOs feature-condition Edge Discovery B3 (harness GO).

Lê allowlist em data/b3_discovery_go_paths.json (thresholds IS congelados).
Mesma régua do crypto_discovery_paths / backtest_discovery_candidates_go.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

_SPEC_PATH = Path(__file__).resolve().parent.parent / "data" / "b3_discovery_go_paths.json"
_OOS_FRAC = 0.30
_MIN_BARS = 600

# asset WinGo (WIN/WDO) → símbolo no JSON (WIN$D / WDO$D)
_ASSET_JSON_SYM = {"WIN": "WIN$D", "WDO": "WDO$D"}

_cache: dict[str, Any] = {"mtime": None, "spec": None}


def _load_spec() -> dict:
    try:
        mtime = _SPEC_PATH.stat().st_mtime
    except OSError:
        return {"paths": []}
    if _cache["spec"] is not None and _cache["mtime"] == mtime:
        return _cache["spec"]
    try:
        raw = json.loads(_SPEC_PATH.read_text(encoding="utf-8"))
    except Exception:
        raw = {"paths": []}
    _cache["mtime"] = mtime
    _cache["spec"] = raw if isinstance(raw, dict) else {"paths": []}
    return _cache["spec"]


def _norm_sym(symbol: str) -> str:
    s = (symbol or "").upper().strip()
    if s in _ASSET_JSON_SYM:
        return _ASSET_JSON_SYM[s]
    if s.startswith("WIN"):
        return "WIN$D"
    if s.startswith("WDO"):
        return "WDO$D"
    return s


def discovery_go_paths(symbol: Optional[str] = None, group: Optional[str] = None) -> list:
    want = _norm_sym(symbol) if symbol else ""
    out = []
    for p in (_load_spec().get("paths") or []):
        if not p.get("enabled", True):
            continue
        if group and (p.get("asset_group") or "") != group:
            continue
        if want and _norm_sym(p.get("symbol") or "") != want:
            continue
        out.append(p)
    return out


def discovery_path_names(symbol: str) -> list:
    return [p.get("path") or p.get("name") for p in discovery_go_paths(symbol)
            if p.get("path") or p.get("name")]


def discovery_enabled(symbol: str) -> bool:
    return bool(discovery_go_paths(symbol))


def _fit_thresholds(F: pd.DataFrame, conds: list) -> dict:
    n = len(F)
    cut = max(int(n * (1 - _OOS_FRAC)), 300)
    thr = {}
    for item in conds:
        feat = item["feat"] if isinstance(item, dict) else item[0]
        bucket = item["bucket"] if isinstance(item, dict) else item[1]
        if bucket in ("0", "1"):
            thr[feat] = {"kind": "bin", "eq": float(bucket)}
            continue
        s = F[feat].iloc[:cut]
        qs = [float(x) for x in s.quantile([0.2, 0.4, 0.6, 0.8]).tolist()]
        q = int(bucket[1]) - 1
        lo_map = {-1: None, 0: qs[0], 1: qs[1], 2: qs[2], 3: qs[3]}
        lo_v = lo_map[q - 1]
        hi_v = qs[q] if q < 4 else None
        thr[feat] = {
            "kind": "q", "bucket": bucket, "qs": qs,
            "lo": None if lo_v is None else float(lo_v),
            "hi": None if hi_v is None else float(hi_v),
            "q_idx": q,
        }
    return thr


def _row_matches(row: pd.Series, thr: dict) -> bool:
    for feat, spec in thr.items():
        if feat not in row.index:
            return False
        val = row[feat]
        if not np.isfinite(val):
            return False
        if spec.get("kind") == "bin":
            if float(val) != float(spec["eq"]):
                return False
            continue
        lo, hi = spec.get("lo"), spec.get("hi")
        q_idx = int(spec.get("q_idx", 0))
        if q_idx == 0:
            if not (val <= float(spec["qs"][0])):
                return False
        else:
            ok_lo = True if lo is None else (val > float(lo))
            ok_hi = True if hi is None else (val <= float(hi))
            if not (ok_lo and ok_hi):
                return False
    return True


def _atr_pts(df: pd.DataFrame) -> float:
    h, l, c = df.High, df.Low, df.Close
    tr = pd.concat([(h - l), (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / 14, adjust=False).mean()
    a = float(atr.iloc[-1])
    return a if np.isfinite(a) and a > 0 else float("nan")


def eval_ohlc_df(df: pd.DataFrame, path: dict):
    """Avalia path em DataFrame OHLC já fechado. Retorna (acao, sl, tp) ou None."""
    if df is None or len(df) < _MIN_BARS:
        return None
    need = ["Open", "High", "Low", "Close", "Volume"]
    if any(c not in df.columns for c in need):
        return None
    work = df[need].copy()
    try:
        from edge_discovery.feature_engine import build_features
        F = build_features(work)
    except Exception:
        return None
    if F.empty or len(F) < _MIN_BARS:
        return None
    conds = path.get("conds") or []
    thr = path.get("thresholds_is")
    if not thr:
        thr = _fit_thresholds(F, conds)
    if not _row_matches(F.iloc[-1], thr):
        return None
    a = _atr_pts(work)
    if not np.isfinite(a) or a <= 0:
        try:
            a = float(F["atr_pct"].iloc[-1]) * float(work.Close.iloc[-1])
        except Exception:
            return None
    if not np.isfinite(a) or a <= 0:
        return None
    px = float(work.Close.iloc[-1])
    sl_m = float(path.get("sl_atr", 1.0))
    tp_m = float(path.get("tp_atr", 2.0))
    side = (path.get("side") or "COMPRA").upper()
    if side == "VENDA":
        return ("VENDA", px + sl_m * a, px - tp_m * a)
    return ("COMPRA", px - sl_m * a, px + tp_m * a)


def make_wingo_signal(path_name: str):
    """Factory: sinal WinGo (dict dir/sl/tp) para um path discovery nomeado."""
    def _signal(df: pd.DataFrame):
        paths = [p for p in discovery_go_paths()
                 if (p.get("path") or p.get("name")) == path_name]
        if not paths:
            return None
        sig = eval_ohlc_df(df, paths[0])
        if not sig:
            return None
        d, sl, tp = sig
        return {"dir": d, "entry": float(df.Close.iloc[-1]), "sl": float(sl),
                "tp": float(tp), "risk": abs(float(df.Close.iloc[-1]) - float(sl))}
    return _signal


def win_wdo_disc_paths() -> list:
    """Só paths WIN/WDO (live WinGo). VALE3 fica no inventário (acoes sem auto)."""
    return discovery_go_paths(group="win") + discovery_go_paths(group="wdo")
