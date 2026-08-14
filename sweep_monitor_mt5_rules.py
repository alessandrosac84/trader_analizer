"""
sweep_monitor_mt5_rules.py — Varredura rápida de filtros no Monitor MT5 (v6, sem IA).

Estratégia:
  1) Uma passagem gera cache de sinais (generate_signal v6) + ATR.
  2) Replay O(sinais) aplica filtros (score/hora/ADX/vol/H1/DOW) + exits fixed/EOD
     com arrays numpy (sem df.iloc no loop).
  3) Variantes de RR recalculam TP1 a partir do risco (piso RR).

Uso (MT5 aberto):
  python -u sweep_monitor_mt5_rules.py --top 15
  python -u sweep_monitor_mt5_rules.py --cache-csv logs/sweep_mt5_WIN_sigs_20260807_2033.csv --save-cache
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUNBUFFERED", "1")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import numpy as np
import pandas as pd

from backtest_pro import (
    fetch_mt5, resample_h1, compute_indicators, _stats, _cost_cfg, fees_pts,
    LOOKBACK, WINDOW, EOD_MIN, _min_of_day, _hours_ok,
)
from services.technical_analysis import generate_signal
import services.technical_analysis as ta

SYMBOLS = ["WIN$D", "WDO$D"]
BASELINE_MIN = {"WIN": 9, "WDO": 7}
MIN_N = 40


def _asset_key(symbol: str) -> str:
    s = symbol.upper().replace("$", "").replace("@", "").replace("_", "")
    if s.startswith("WIN"):
        return "WIN"
    if s.startswith("WDO"):
        return "WDO"
    return s[:3]


def _oos_pct(trades: pd.DataFrame) -> float:
    if trades is None or trades.empty:
        return 0.0
    t2 = trades.copy()
    t2["periodo"] = pd.to_datetime(t2["open_ts"]).dt.to_period("2M").astype(str)
    pos = tot = 0
    for _, g in t2.groupby("periodo"):
        gs = _stats(g)
        if gs.get("n", 0) <= 0:
            continue
        tot += 1
        if gs["exp_net_R"] > 0:
            pos += 1
    return (pos / tot * 100.0) if tot else 0.0


def _metrics(trades: pd.DataFrame) -> dict:
    s = _stats(trades)
    if not s or s.get("n", 0) == 0:
        return {"n": 0, "win%": 0, "PF": 0, "exp_net_R": 0, "total_net_R": 0,
                "maxDD_R": 0, "oos%": 0}
    s = dict(s)
    s["oos%"] = round(_oos_pct(trades), 1)
    return s


def _metrics_fast(trades: pd.DataFrame) -> dict:
    s = _stats(trades)
    if not s or s.get("n", 0) == 0:
        return {"n": 0, "win%": 0, "PF": 0, "exp_net_R": 0, "total_net_R": 0,
                "maxDD_R": 0, "oos%": 0}
    s = dict(s)
    s["oos%"] = 0.0
    return s


def _score_row(m: dict, baseline_exp: float) -> float:
    n = m.get("n", 0)
    if n < MIN_N:
        return -999.0
    exp = float(m.get("exp_net_R") or 0)
    pf = float(m.get("PF") or 0)
    oos = float(m.get("oos%") or 0)
    net = float(m.get("total_net_R") or 0)
    go = (exp >= 0.10 and pf >= 1.25 and oos >= 55 and n >= MIN_N)
    base = exp * 10 + (pf - 1) * 2 + oos / 100 + min(n, 200) / 500
    if go:
        base += 5
    if exp > baseline_exp:
        base += (exp - baseline_exp) * 3
    if exp <= 0:
        base -= 2
    base += net / 500
    return base


HOUR_KEYS = {
    "all": None,
    "skip_open": (9 * 60 + 30, 17 * 60 + 30),
    "skip_open_lunch": [(9 * 60 + 30, 12 * 60), (13 * 60, 17 * 60 + 30)],
    "am": (9 * 60 + 15, 12 * 60),
    "pm": (13 * 60, 16 * 60 + 30),
    "core": (10 * 60, 15 * 60),
    "no_late": (9 * 60, 15 * 60 + 30),
    "A_B": [(9 * 60 + 15, 11 * 60 + 30), (14 * 60, 16 * 60 + 30)],
}


def prepare_bars(df15: pd.DataFrame) -> dict:
    """OHLC + minute em arrays numpy (uma vez por simbolo)."""
    idx = df15.index
    if not isinstance(idx, pd.DatetimeIndex):
        idx = pd.to_datetime(idx)
    minutes = (idx.hour.to_numpy(dtype=np.int32) * 60
               + idx.minute.to_numpy(dtype=np.int32))
    return {
        "open": df15["Open"].to_numpy(dtype=np.float64, copy=False),
        "high": df15["High"].to_numpy(dtype=np.float64, copy=False),
        "low": df15["Low"].to_numpy(dtype=np.float64, copy=False),
        "close": df15["Close"].to_numpy(dtype=np.float64, copy=False),
        "minute": minutes,
        "index": idx,
        "n": len(df15),
    }


def build_signal_cache(df15: pd.DataFrame, progress_every: int = 5000) -> list[dict]:
    """Uma passagem: sinais v6 brutos (ja com filtros internos do motor)."""
    h1 = resample_h1(df15)
    atr_series = compute_indicators(df15)["atr"]
    sigs = []
    n = len(df15)
    t0 = time.time()
    for i in range(LOOKBACK, n):
        if progress_every and (i - LOOKBACK) % progress_every == 0 and i > LOOKBACK:
            pct = (i - LOOKBACK) / max(n - LOOKBACK, 1) * 100
            print(f"    cache {pct:.0f}% ({i}/{n}) {(time.time()-t0):.0f}s", flush=True)
        ts = df15.index[i]
        minute = _min_of_day(ts)
        if minute >= EOD_MIN:
            continue
        win = df15.iloc[max(0, i - WINDOW):i]
        hwin = h1[h1.index <= win.index[-1]].tail(300)
        hwin = hwin if len(hwin) >= 26 else None
        sig = generate_signal(win, htf_df=hwin)
        if not sig or sig.get("acao") not in ("COMPRA", "VENDA"):
            continue
        if not sig.get("stop") or not sig.get("tp1"):
            continue
        o = float(df15.iloc[i]["Open"])
        stop = float(sig["stop"])
        tp1 = float(sig["tp1"])
        buy = sig["acao"] == "COMPRA"
        atr_i = float(atr_series.iloc[i - 1] or 0)
        risk = abs(o - stop)
        same_day = ts.date() == win.index[-1].date()
        gap_tp = (buy and o >= tp1) or ((not buy) and o <= tp1)
        if not same_day or gap_tp or risk < 0.3 * max(atr_i, 1e-9):
            continue
        try:
            vv = float(win["Volume"].iloc[-1] or 0)
            va = float(win["Volume"].tail(20).mean() or 0)
            vol_ratio = (vv / va) if va > 0 else None
        except Exception:
            vol_ratio = None
        sigs.append({
            "i": i,
            "ts": ts,
            "minute": minute,
            "hour": ts.hour,
            "dow": ts.dayofweek,
            "dir": sig["acao"],
            "entry": o,
            "stop": stop,
            "tp1": tp1,
            "risk": risk,
            "atr": atr_i or risk,
            "score": float(sig.get("score") or 0),
            "abs_score": abs(float(sig.get("score") or 0)),
            "adx": float(sig["adx"]) if sig.get("adx") is not None else None,
            "htf_trend": sig.get("htf_trend"),
            "vol_ratio": vol_ratio,
        })
    print(f"    cache OK: {len(sigs)} sinais em {(time.time()-t0)/60:.1f} min", flush=True)
    return sigs


def _resolve_cache_path(cache_csv: str | None, asset: str) -> Path | None:
    if not cache_csv:
        return None
    p = Path(cache_csv)
    if "{asset}" in cache_csv:
        cand = Path(cache_csv.format(asset=asset))
        return cand if cand.is_file() else None
    name = p.name
    for other in ("WIN", "WDO"):
        if other in name and other != asset:
            alt = p.with_name(name.replace(other, asset))
            if alt.is_file():
                return alt
    if p.is_file():
        if asset not in p.name and (("WIN" in p.name) or ("WDO" in p.name)):
            parent = p.parent
            matches = sorted(parent.glob(f"sweep_mt5_{asset}_sigs_*.csv"),
                             key=lambda x: x.stat().st_mtime, reverse=True)
            return matches[0] if matches else None
        return p
    if p.is_dir():
        matches = sorted(p.glob(f"sweep_mt5_{asset}_sigs_*.csv"),
                         key=lambda x: x.stat().st_mtime, reverse=True)
        return matches[0] if matches else None
    matches = sorted(Path("logs").glob(f"sweep_mt5_{asset}_sigs_*.csv"),
                     key=lambda x: x.stat().st_mtime, reverse=True)
    return matches[0] if matches else None


def load_signal_cache(path: Path, bars: dict | None = None) -> list[dict]:
    df = pd.read_csv(path)
    ts_to_i = None
    if bars is not None:
        idx = bars["index"]
        ts_to_i = {pd.Timestamp(t): i for i, t in enumerate(idx)}
    sigs = []
    skipped = 0
    for row in df.itertuples(index=False):
        ts = pd.Timestamp(row.ts)
        if ts_to_i is not None:
            i = ts_to_i.get(ts)
            if i is None:
                skipped += 1
                continue
        else:
            i = int(row.i)
        adx = getattr(row, "adx", None)
        vol = getattr(row, "vol_ratio", None)
        htf = getattr(row, "htf_trend", None)
        if isinstance(htf, float) and np.isnan(htf):
            htf = None
        if adx is not None and (isinstance(adx, float) and np.isnan(adx)):
            adx = None
        if vol is not None and (isinstance(vol, float) and np.isnan(vol)):
            vol = None
        score = float(row.score or 0)
        sigs.append({
            "i": int(i),
            "ts": ts,
            "minute": int(row.minute),
            "hour": int(row.hour),
            "dow": int(row.dow),
            "dir": str(row.dir),
            "entry": float(row.entry),
            "stop": float(row.stop),
            "tp1": float(row.tp1),
            "risk": float(row.risk),
            "atr": float(getattr(row, "atr", row.risk) or row.risk),
            "score": score,
            "abs_score": float(getattr(row, "abs_score", abs(score))),
            "adx": float(adx) if adx is not None else None,
            "htf_trend": htf,
            "vol_ratio": float(vol) if vol is not None else None,
        })
    if skipped:
        print(f"    cache: {skipped} sinais fora da serie atual", flush=True)
    return sigs


def _pass_filters(s: dict, cfg: dict) -> bool:
    sc = s["abs_score"]
    if sc < cfg["min_score"] or sc > cfg["max_score"]:
        return False
    if not _hours_ok(s["minute"], cfg.get("hours_arg")):
        return False
    adx_m = cfg.get("adx_min")
    if adx_m is not None:
        if s["adx"] is None or s["adx"] < adx_m:
            return False
    vol_m = cfg.get("vol_min")
    if vol_m is not None:
        if s["vol_ratio"] is None or s["vol_ratio"] < vol_m:
            return False
    if cfg.get("htf_aligned"):
        ht = s.get("htf_trend")
        if ht is not None:
            ok = (s["dir"] == "COMPRA" and ht == "alta") or (
                s["dir"] == "VENDA" and ht == "baixa"
            )
            if not ok:
                return False
    bdow = cfg.get("block_dow")
    if bdow and s["dow"] in bdow:
        return False
    return True


def replay(bars: dict, symbol: str, sigs: list[dict], cfg: dict,
           target_rr: float | None = None) -> pd.DataFrame:
    """Replay exits fixed + EOD: filtra sinais e varre barras com arrays (1 posicao)."""
    high = bars["high"]
    low = bars["low"]
    close = bars["close"]
    minute_arr = bars["minute"]
    index = bars["index"]
    n = bars["n"]

    filtered = [s for s in sigs if _pass_filters(s, cfg)]
    filtered.sort(key=lambda s: s["i"])

    trades = []
    cfgc = _cost_cfg(symbol)
    fee = fees_pts(symbol)
    tick = cfgc["tick_size"]
    next_free = 0

    for s in filtered:
        i = int(s["i"])
        if i < next_free:
            continue
        if i >= n or minute_arr[i] >= EOD_MIN:
            continue

        entry = float(s["entry"])
        stop = float(s["stop"])
        risk = float(s["risk"])
        if risk <= 0:
            continue
        buy = s["dir"] == "COMPRA"

        if target_rr is not None and target_rr > 0:
            tp1 = entry + float(target_rr) * risk if buy else entry - float(target_rr) * risk
        else:
            tp1 = float(s["tp1"])

        closed = False
        close_j = i
        legs = None
        for j in range(i + 1, n):
            h = high[j]
            l = low[j]
            hit_stop = (l <= stop) if buy else (h >= stop)
            hit_tp1 = (h >= tp1) if buy else (l <= tp1)

            if hit_stop:
                legs = [(1.0, -1.0, True)]
                closed = True
                close_j = j
                break
            if hit_tp1:
                r_tp = ((tp1 - entry) / risk) if buy else ((entry - tp1) / risk)
                legs = [(1.0, r_tp, False)]
                closed = True
                close_j = j
                break
            if minute_arr[j] >= EOD_MIN:
                px = close[j]
                r_eod = ((px - entry) / risk) if buy else ((entry - px) / risk)
                legs = [(1.0, r_eod, True)]
                closed = True
                close_j = j
                break

        if not closed or not legs:
            break

        gross = sum(f * r for f, r, _ in legs)
        cost_pts = fee + tick
        cost_pts += sum(f * tick for f, _, is_mkt in legs if is_mkt)
        net = gross - (cost_pts / risk)
        ts_open = s["ts"]
        trades.append({
            "open_ts": ts_open, "close_ts": index[close_j], "dir": s["dir"],
            "hour": ts_open.hour if hasattr(ts_open, "hour") else int(s["hour"]),
            "score": s.get("score"), "adx": s.get("adx"),
            "gross_R": round(gross, 3),
            "cost_R": round(cost_pts / risk, 3), "net_R": round(net, 3),
        })
        next_free = close_j + 1

    return pd.DataFrame(trades)


def _print_m(tag: str, m: dict):
    if m.get("n", 0) == 0:
        print(f"  {tag}: (sem trades)", flush=True)
        return
    print(
        f"  {tag}: n={m['n']} WR={m['win%']:.1f}% PF={m['PF']} "
        f"exp={m['exp_net_R']:+.3f}R net={m['total_net_R']:+.1f}R "
        f"DD={m['maxDD_R']} OOS={m['oos%']:.0f}%",
        flush=True,
    )


def sweep_cfgs(asset: str):
    # Mantem score/hora/ADX/vol/H1/DOW; grade completa nessas dimensoes.
    score_mins = [7, 8, 9, 10] if asset == "WDO" else [8, 9, 10]
    score_maxs = [10, 11]
    adx_mins = [None, 25, 28, 30]
    vol_mins = [None, 1.0, 1.3]
    require_htf = [False, True]
    block_dows = [None, (0,), (4,), (0, 4)]
    for smin, smax, hkey, adx_m, vol_m, htf_req, bdow in itertools.product(
        score_mins, score_maxs, HOUR_KEYS.keys(), adx_mins, vol_mins,
        require_htf, block_dows
    ):
        if smax < smin:
            continue
        yield {
            "min_score": smin,
            "max_score": smax,
            "hours": hkey,
            "hours_arg": HOUR_KEYS[hkey],
            "adx_min": adx_m,
            "vol_min": vol_m,
            "htf_aligned": htf_req,
            "block_dow": bdow,
            "label": (
                f"sc{smin}-{smax}|h={hkey}|adx>={adx_m or 0}|vol>={vol_m or 0}"
                f"|htf={int(htf_req)}|blkDow={bdow or '-'}"
            ),
        }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bars", type=int, default=200000)
    ap.add_argument("--symbol", default=None)
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument(
        "--cache-csv", default=None,
        help="CSV de sinais (aceita {asset} ou path WIN/WDO; procura irmao do outro ativo)",
    )
    ap.add_argument(
        "--save-cache", action="store_true",
        help="Persiste cache de sinais gerado (logs/sweep_mt5_{asset}_sigs_{stamp}.csv)",
    )
    args = ap.parse_args()

    os.makedirs("logs", exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    logpath = f"logs/sweep_monitor_mt5_{stamp}.txt"

    class Tee:
        def __init__(self, path):
            self.file = open(path, "a", encoding="utf-8")
            self.stdout = sys.__stdout__

        def write(self, data):
            try:
                self.stdout.write(data)
            except UnicodeEncodeError:
                enc = getattr(self.stdout, "encoding", None) or "utf-8"
                self.stdout.write(
                    data.encode(enc, errors="replace").decode(enc, errors="replace")
                )
            self.file.write(data)
            self.file.flush()

        def flush(self):
            self.stdout.flush()
            self.file.flush()

    sys.stdout = Tee(logpath)

    symbols = [args.symbol] if args.symbol else list(SYMBOLS)
    print(f"\n{'#' * 70}", flush=True)
    print("# SWEEP Monitor MT5 — so score v6 + SL/TP fixo + EOD (sem IA)", flush=True)
    print(f"# {datetime.now():%d/%m/%Y %H:%M:%S}", flush=True)
    print(f"# Log: {logpath}", flush=True)
    if args.cache_csv:
        print(f"# cache-csv: {args.cache_csv}", flush=True)
    print(f"# save-cache: {bool(args.save_cache)}", flush=True)
    print(f"{'#' * 70}\n", flush=True)

    t0 = time.time()
    data = {}
    for sym in symbols:
        try:
            df = fetch_mt5(sym, "15", args.bars)
            if df is None or len(df) < 1000:
                print(f"!! {sym}: sem dados", flush=True)
                continue
            anos = len(df) / 28 / 252
            print(f"{sym}: {len(df)} M15 ({df.index[0]} -> {df.index[-1]}) ~{anos:.1f}a",
                  flush=True)
            data[sym] = df
        except Exception:
            print(f"!! {sym}:\n{traceback.format_exc()}", flush=True)

    best_by_asset = {}

    for sym, df in data.items():
        asset = _asset_key(sym)
        print(f"\n{'=' * 70}\n>>> {sym} ({asset})\n{'=' * 70}", flush=True)

        bars = prepare_bars(df)
        print(f"Barras numpy: n={bars['n']}", flush=True)

        cache_path = _resolve_cache_path(args.cache_csv, asset)
        if cache_path is not None:
            print(f"Carregando cache de sinais: {cache_path}", flush=True)
            sigs = load_signal_cache(cache_path, bars)
            print(f"    cache load OK: {len(sigs)} sinais", flush=True)
        else:
            if args.cache_csv:
                print(f"Cache nao encontrado para {asset}; gerando do zero...", flush=True)
            print("Construindo cache de sinais (1 passagem)...", flush=True)
            sigs = build_signal_cache(df)
            if args.save_cache or not args.cache_csv:
                out_c = f"logs/sweep_mt5_{asset}_sigs_{stamp}.csv"
                pd.DataFrame(sigs).to_csv(out_c, index=False)
                print(f"    cache salvo: {out_c}", flush=True)

        base_cfg = {
            "min_score": BASELINE_MIN[asset], "max_score": 11,
            "hours": "all", "hours_arg": None,
            "adx_min": None, "vol_min": None,
            "htf_aligned": False, "block_dow": None,
            "label": f"BASELINE min{BASELINE_MIN[asset]}",
        }
        base_tr = replay(bars, sym, sigs, base_cfg)
        base_m = _metrics(base_tr)
        print("Baseline producao:", flush=True)
        _print_m("BASE", base_m)
        base_tr.to_csv(f"logs/sweep_mt5_{asset}_baseline_{stamp}.csv", index=False)

        dump_tr = replay(bars, sym, sigs, {
            "min_score": 7, "max_score": 11, "hours": "all", "hours_arg": None,
            "adx_min": None, "vol_min": None, "htf_aligned": False, "block_dow": None,
        })
        print("Dump motor >=7:", flush=True)
        _print_m("DUMP", _metrics(dump_tr))

        if not dump_tr.empty:
            d = dump_tr.copy()
            d["abs_score"] = pd.to_numeric(d["score"], errors="coerce").abs()
            print("\n  -- por |score| --", flush=True)
            for sc, g in d.groupby(d["abs_score"].round(0)):
                _print_m(f"score~{int(sc)}", _metrics(g))
            print("  -- por hora --", flush=True)
            for h, g in d.groupby("hour"):
                _print_m(f"h{int(h):02d}", _metrics(g))

        base_exp = float(base_m.get("exp_net_R") or 0)
        rows = []
        t1 = time.time()
        n_cfgs = 0
        for cfg in sweep_cfgs(asset):
            n_cfgs += 1
            tr = replay(bars, sym, sigs, cfg)
            m = _metrics_fast(tr)
            if m["n"] < MIN_N:
                continue
            rows.append({**cfg, **m, "target_rr": 1.5, "_tr": tr,
                         "rank": _score_row(m, base_exp)})
        rows.sort(key=lambda r: r["rank"], reverse=True)
        for r in rows[: max(args.top * 3, 40)]:
            m_full = _metrics(r["_tr"])
            r["oos%"] = m_full.get("oos%", 0)
            r["rank"] = _score_row(r, base_exp)
        rows.sort(key=lambda r: r["rank"], reverse=True)
        print(f"\n  Varredura offline: {len(rows)} pacotes n>={MIN_N} "
              f"({n_cfgs} cfgs) [{time.time()-t1:.1f}s]", flush=True)
        print(f"  TOP {args.top}:", flush=True)
        for i, r in enumerate(rows[: args.top], 1):
            print(
                f"  #{i:02d} rank={r['rank']:.2f} {r['label']}\n"
                f"       n={r['n']} WR={r['win%']:.1f}% PF={r['PF']} "
                f"exp={r['exp_net_R']:+.3f} OOS={r['oos%']:.0f}% "
                f"net={r['total_net_R']:+.1f}",
                flush=True,
            )

        extra = []
        for r in rows[:3]:
            for rr in (1.2, 1.8, 2.0):
                tr = replay(bars, sym, sigs, r, target_rr=rr)
                m = _metrics(tr)
                if m["n"] < MIN_N:
                    continue
                extra.append({
                    **{k: r[k] for k in (
                        "min_score", "max_score", "hours", "hours_arg",
                        "adx_min", "vol_min", "htf_aligned", "block_dow", "label"
                    )},
                    **m, "target_rr": rr,
                    "label": r["label"] + f"|RR={rr}",
                    "rank": _score_row(m, base_exp),
                })
                _print_m(f"RR {rr} on #{r['label'][:40]}", m)
        for r in rows:
            r.pop("_tr", None)
        all_res = rows + extra
        all_res.sort(key=lambda x: x["rank"], reverse=True)
        best = all_res[0] if all_res else None

        improved = [r for r in all_res
                    if float(r.get("exp_net_R") or 0) > base_exp
                    and float(r.get("PF") or 0) >= float(base_m.get("PF") or 0)]
        if improved:
            best = improved[0]

        best_by_asset[asset] = {"baseline": base_m, "best": best, "top": all_res[:8]}
        pd.DataFrame([
            {k: v for k, v in r.items() if k != "hours_arg"} for r in all_res[:200]
        ]).to_csv(f"logs/sweep_mt5_{asset}_rank_{stamp}.csv", index=False)

        print("\n  MELHOR pacote:", flush=True)
        if best:
            print(f"  {best.get('label')} RR={best.get('target_rr', 1.5)}", flush=True)
            _print_m("BEST", best)
        else:
            print("  (nenhum)", flush=True)

    print(f"\n{'#' * 70}\n# RESUMO FINAL\n{'#' * 70}", flush=True)
    out_json = {}
    for asset, pack in best_by_asset.items():
        print(f"\n[{asset}]", flush=True)
        _print_m("baseline", pack["baseline"])
        b = pack.get("best") or {}
        if b:
            print(f"  BEST: {b.get('label')} RR={b.get('target_rr', 1.5)}", flush=True)
            _print_m("melhorado", b)
        out_json[asset] = {
            "baseline": {k: pack["baseline"].get(k) for k in
                         ("n", "win%", "PF", "exp_net_R", "total_net_R", "oos%", "maxDD_R")},
            "best": {k: b.get(k) for k in (
                "label", "min_score", "max_score", "hours", "adx_min", "vol_min",
                "htf_aligned", "block_dow", "target_rr",
                "n", "win%", "PF", "exp_net_R", "total_net_R", "oos%", "maxDD_R"
            )},
        }

    jpath = f"logs/sweep_monitor_mt5_best_{stamp}.json"
    with open(jpath, "w", encoding="utf-8") as f:
        json.dump(out_json, f, ensure_ascii=False, indent=2, default=str)
    print(f"\nRegras: {jpath}", flush=True)
    print(f"Log: {logpath}", flush=True)
    print(f"TARGET_RR motor atual: {ta.TARGET_RR}", flush=True)
    print(f"Total {(time.time()-t0)/60:.1f} min", flush=True)


if __name__ == "__main__":
    main()
