"""
crypto_discovery_paths.py — GOs feature-condition do Edge Discovery (harness GO).

Lê allowlist em data/crypto_discovery_go_paths.json (thresholds IS congelados,
mesma régua do backtest_discovery_candidates_go.py: quintis 0.2/0.4/0.6/0.8
só no in-sample 70%). Avalia última barra FECHADA com build_features + SL 1×ATR
/ TP 2×ATR.

Não abaixa a barra GO: só paths com enabled=true no JSON aprovado.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

_SPEC_PATH = Path(__file__).resolve().parent.parent / "data" / "crypto_discovery_go_paths.json"
_OOS_FRAC = 0.30
_MIN_BARS = 600

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


def discovery_go_paths(symbol: Optional[str] = None) -> list:
    """Paths enabled (opcionalmente filtrados por símbolo), na ordem do JSON (= ranking)."""
    sym = (symbol or "").upper().strip()
    out = []
    for p in (_load_spec().get("paths") or []):
        if not p.get("enabled", True):
            continue
        if sym and (p.get("symbol") or "").upper() != sym:
            continue
        out.append(p)
    return out


def discovery_path_names(symbol: str) -> list:
    return [p.get("path") or p.get("name") for p in discovery_go_paths(symbol)
            if p.get("path") or p.get("name")]


def discovery_enabled(symbol: str) -> bool:
    return bool(discovery_go_paths(symbol))


def _candles_to_df(candles):
    if not candles or len(candles) < _MIN_BARS:
        return None
    df = pd.DataFrame(candles).rename(columns={
        "open": "Open", "high": "High", "low": "Low",
        "close": "Close", "volume": "Volume"})
    for col in ("Open", "High", "Low", "Close", "Volume"):
        if col not in df.columns:
            return None
    df["dt"] = pd.to_datetime(df["time"], unit="s")
    df = df.set_index("dt").iloc[:-1]  # descarta barra em formação
    if len(df) < _MIN_BARS:
        return None
    return df[["Open", "High", "Low", "Close", "Volume"]].copy()


def _fit_thresholds(F: pd.DataFrame, conds: list) -> dict:
    """Mesma lógica de make_mask (backtest_edge_candidates) — limiares IS."""
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


def eval_path(candles, path: dict, *, df=None, features=None):
    """
    Avalia um path discovery. Retorna (acao, sl, tp) ou None.
    Prefere thresholds_is do JSON; se ausentes, re-deriva IS nos candles.
    df/features opcionais: reusa build_features no mesmo ciclo (evita N× custo).
    """
    if df is None:
        df = _candles_to_df(candles)
    if df is None:
        return None
    F = features
    if F is None:
        try:
            from edge_discovery.feature_engine import build_features
            F = build_features(df)
        except Exception:
            return None
    if F is None or F.empty or len(F) < _MIN_BARS:
        return None

    conds = path.get("conds") or []
    thr = path.get("thresholds_is")
    if not thr:
        thr = _fit_thresholds(F, conds)
    row = F.iloc[-1]
    if not _row_matches(row, thr):
        return None

    a = _atr_pts(df)
    if not np.isfinite(a) or a <= 0:
        try:
            a = float(F["atr_pct"].iloc[-1]) * float(df.Close.iloc[-1])
        except Exception:
            return None
    if not np.isfinite(a) or a <= 0:
        return None

    px = float(df.Close.iloc[-1])
    sl_m = float(path.get("sl_atr", 1.0))
    tp_m = float(path.get("tp_atr", 2.0))
    side = (path.get("side") or "COMPRA").upper()
    if side == "VENDA":
        sig = ("VENDA", px + sl_m * a, px - tp_m * a)
    else:
        sig = ("COMPRA", px - sl_m * a, px + tp_m * a)
    # Live caps (ETH/BTC/EUR/GBP): ATR H1 / estrutural infla SL/TP — cap como XAU/WDO.
    sym = (path.get("symbol") or "").upper().strip()
    try:
        from services.crypto_edge_setups import cap_live_signal, live_caps_for
        if live_caps_for(sym):
            sig = cap_live_signal(sym, sig, px)
    except Exception:
        pass
    return sig


def prepare_features(candles):
    """Uma vez por ciclo/TF: (df, features) ou (None, None)."""
    df = _candles_to_df(candles)
    if df is None:
        return None, None
    try:
        from edge_discovery.feature_engine import build_features
        F = build_features(df)
    except Exception:
        return None, None
    if F is None or F.empty or len(F) < _MIN_BARS:
        return None, None
    return df, F


def discovery_signal(candles, symbol: str, path_name=None):
    """
    Testa paths do símbolo (ordem do JSON = ranking harness).
    Se path_name dado, só esse. Retorna (tag, acao, sl, tp, motivo) ou None.
    """
    sym = (symbol or "").upper().strip()
    paths = discovery_go_paths(sym)
    if path_name:
        paths = [p for p in paths if (p.get("path") or p.get("name")) == path_name]
    df, F = prepare_features(candles)
    if df is None:
        return None
    for p in paths:
        sig = eval_path(candles, p, df=df, features=F)
        if sig:
            tag = p.get("path") or p.get("name")
            motivo = p.get("comment") or f"[{tag} GO disc] feature-cond SL1 TP2"
            acao, sl, tp = sig
            return (tag, acao, sl, tp, motivo)
    return None


def paths_by_tf(symbol: str) -> dict:
    """Agrupa paths por timeframe (ex.: {15: [...], 60: [...]})."""
    out = {}
    for p in discovery_go_paths(symbol):
        tf = int(p.get("tf") or 15)
        out.setdefault(tf, []).append(p)
    return out
