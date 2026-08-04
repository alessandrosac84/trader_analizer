"""
backtest_setups_novos_v18_eth_ema_vwap.py — caça ETH equivalentes GO.

Foco (buracos v17 — 0 GOs nessas famílias):
  · EMA reclaim (espelho BTC_EMA_RECL_24H_M30 / NY)
  · VWAP cont / fade
  · Engolfo
  · H4-PB-EMA
  · Sweeps: M15/M30 · sessões · vol · MT · TP/RR

Régua GO: net ≥ +0,10 R · PF ≥ 1,25 · cons ≥ 55% · OOS segura · n ≥ 40.
NÃO altera live até 🟢. Não duplica tags HL/INS/IMP/PDH já wired.

Uso:
  python backtest_setups_novos_v18_eth_ema_vwap.py
  python backtest_setups_novos_v18_eth_ema_vwap.py --workers 6 --bars 50000
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

import pandas as pd

from backtest_kimi_real import add_indicators
from backtest_setups_novos import stats, consistency, verdict, OOS_FRAC, _sig
from backtest_setups_novos_v2 import add_extra_v2, _simulate
from backtest_setups_novos_v9_refine import _mt
from backtest_setups_novos_v13_btc_24h import _h4_pb_ema_h, _load_ohlc
from backtest_setups_novos_v14_btc_eth import _engulf_h, _ema_reclaim_h
from backtest_setups_novos_v15_btc_eth import _cap_tp, _vwap_fade_h, _vwap_cont_h

STRATS = {}
STRAT_SYM = {}
ONE_PER_DAY = set()
STRAT_TF = {}
STRAT_META = {}

_WORKER_DFS = {}


def _reg(name, fn, sym="ETHUSD", one_per_day=False, tf=15, meta=None):
    STRATS[name] = fn
    STRAT_SYM[name] = sym
    STRAT_TF[name] = tf
    if one_per_day:
        ONE_PER_DAY.add(name)
    if meta:
        STRAT_META[name] = meta


def _add(name, fn, *, kind, n=None, vol=1.3, hh0=0.0, hh1=24.0,
         session=None, tp_r=1.5, tf=15, dist=None, title=""):
    if name in STRATS:
        return
    wrapped = fn
    if session == "mt":
        wrapped = _mt(wrapped)
    if tp_r != 1.5 and kind not in ("h4_pb",):
        wrapped = _cap_tp(wrapped, float(tp_r))
    meta = {
        "name": name, "sym": "ETHUSD", "tf": tf, "kind": kind,
        "n": n, "vol": vol, "hh0": hh0, "hh1": hh1,
        "session": session if session in ("mt",) else None,
        "tp_r": tp_r, "dist": dist, "title": title or name,
    }
    _reg(name, wrapped, sym="ETHUSD", tf=tf, meta=meta)


def _build_battery():
    """~140–190 variantes ETH: EMA / VWAP / engolfo / H4-PB."""
    SESS = [
        ("ASIA", 0.0, 8.0),
        ("LON", 8.0, 12.0),
        ("LON0713", 7.0, 13.0),
        ("LN", 8.0, 17.0),
        ("NY", 13.0, 17.0),
        ("DAY", 10.0, 16.0),
        ("1622", 16.0, 22.0),
        ("NIGHT", 20.0, 10.0),
        ("OVN", 18.0, 8.0),
        ("24H", 0.0, 24.0),
    ]
    # Sessões prioritárias para sweeps densos (vol×MT×TP)
    CORE = ["ASIA", "LON", "LN", "NY", "NIGHT", "OVN", "24H"]
    M30_SESS = [
        ("ASIA", 0.0, 8.0),
        ("LON", 8.0, 12.0),
        ("LN", 8.0, 17.0),
        ("NY", 13.0, 17.0),
        ("NIGHT", 20.0, 10.0),
        ("OVN", 18.0, 8.0),
        ("24H", 0.0, 24.0),
        ("DAY", 10.0, 16.0),
    ]

    # ── EMA reclaim (espelho BTC GO + sweep vol/MT/TP/M30) ──
    for tag, h0, h1 in SESS:
        for vol in (1.2, 1.3, 1.4, 1.5):
            vtag = "" if abs(vol - 1.3) < 1e-9 else f"_V{int(vol * 10)}"
            _add(f"ETH_EMA_RECL_{tag}{vtag}", _ema_reclaim_h(h0, h1, vol),
                 kind="ema_recl", vol=vol, hh0=h0, hh1=h1,
                 title=f"EMA reclaim vol≥{vol}× · {tag}")
            if tag in CORE:
                _add(f"ETH_EMA_RECL_{tag}{vtag}_MT", _ema_reclaim_h(h0, h1, vol),
                     kind="ema_recl", vol=vol, hh0=h0, hh1=h1, session="mt",
                     title=f"EMA reclaim vol≥{vol}× · {tag} · seg–qui")
        # TP 2R nos vols base (1.3/1.4) sessões core
        if tag in CORE:
            for vol in (1.3, 1.4):
                vtag = "" if abs(vol - 1.3) < 1e-9 else f"_V{int(vol * 10)}"
                _add(f"ETH_EMA_RECL_{tag}{vtag}_TP20",
                     _ema_reclaim_h(h0, h1, vol),
                     kind="ema_recl", vol=vol, hh0=h0, hh1=h1, tp_r=2.0,
                     title=f"EMA reclaim · {tag} · TP 2R")

    # M30 EMA — espelho direto do BTC_EMA_RECL_24H_M30 + vizinhos
    for tag, h0, h1 in M30_SESS:
        for vol in (1.2, 1.3, 1.4, 1.5):
            vtag = "" if abs(vol - 1.3) < 1e-9 else f"_V{int(vol * 10)}"
            _add(f"ETH_EMA_RECL_{tag}{vtag}_M30",
                 _ema_reclaim_h(h0, h1, vol),
                 kind="ema_recl", vol=vol, hh0=h0, hh1=h1, tf=30,
                 title=f"EMA reclaim vol≥{vol}× · {tag} · M30")
            if tag in ("24H", "ASIA", "NY", "LN", "OVN", "NIGHT"):
                _add(f"ETH_EMA_RECL_{tag}{vtag}_MT_M30",
                     _ema_reclaim_h(h0, h1, vol),
                     kind="ema_recl", vol=vol, hh0=h0, hh1=h1, session="mt",
                     tf=30, title=f"EMA reclaim · {tag} · M30 · seg–qui")
        if tag in ("24H", "NY", "ASIA", "LN"):
            _add(f"ETH_EMA_RECL_{tag}_TP20_M30",
                 _ema_reclaim_h(h0, h1, 1.3),
                 kind="ema_recl", vol=1.3, hh0=h0, hh1=h1, tp_r=2.0, tf=30,
                 title=f"EMA reclaim · {tag} · TP 2R · M30")

    # ── VWAP cont ──
    for tag, h0, h1 in SESS:
        for dist in (0.2, 0.3, 0.5):
            dtag = "" if abs(dist - 0.3) < 1e-9 else f"_D{int(dist * 10)}"
            for vol in (1.2, 1.3):
                vtag = "" if abs(vol - 1.3) < 1e-9 else f"_V{int(vol * 10)}"
                if abs(vol - 1.2) < 1e-9 and tag not in CORE:
                    continue
                _add(f"ETH_VWAP_CONT_{tag}{vtag}{dtag}",
                     _vwap_cont_h(vol, dist, h0, h1),
                     kind="vwap_cont", vol=vol, hh0=h0, hh1=h1, dist=dist,
                     title=f"VWAP cont d≥{dist} · {tag}")
        if tag in CORE:
            _add(f"ETH_VWAP_CONT_{tag}_MT",
                 _vwap_cont_h(1.3, 0.3, h0, h1),
                 kind="vwap_cont", vol=1.3, hh0=h0, hh1=h1, dist=0.3,
                 session="mt", title=f"VWAP cont · {tag} · seg–qui")
            _add(f"ETH_VWAP_CONT_{tag}_TP20",
                 _vwap_cont_h(1.3, 0.3, h0, h1),
                 kind="vwap_cont", vol=1.3, hh0=h0, hh1=h1, dist=0.3, tp_r=2.0,
                 title=f"VWAP cont · {tag} · TP 2R")

    for tag, h0, h1 in M30_SESS:
        _add(f"ETH_VWAP_CONT_{tag}_M30",
             _vwap_cont_h(1.3, 0.3, h0, h1),
             kind="vwap_cont", vol=1.3, hh0=h0, hh1=h1, dist=0.3, tf=30,
             title=f"VWAP cont · {tag} · M30")
        if tag in ("24H", "NY", "LN", "ASIA"):
            _add(f"ETH_VWAP_CONT_{tag}_MT_M30",
                 _vwap_cont_h(1.3, 0.3, h0, h1),
                 kind="vwap_cont", vol=1.3, hh0=h0, hh1=h1, dist=0.3,
                 session="mt", tf=30, title=f"VWAP cont · {tag} · M30 · MT")

    # ── VWAP fade ──
    for tag, h0, h1 in SESS:
        for dist in (1.0, 1.2, 1.4):
            dtag = "" if abs(dist - 1.2) < 1e-9 else f"_D{int(dist * 10)}"
            _add(f"ETH_VWAP_FADE_{tag}{dtag}",
                 _vwap_fade_h(1.2, dist, h0, h1),
                 kind="vwap_fade", vol=1.2, hh0=h0, hh1=h1, dist=dist,
                 title=f"VWAP fade d≥{dist} · {tag}")
        if tag in CORE:
            _add(f"ETH_VWAP_FADE_{tag}_MT",
                 _vwap_fade_h(1.2, 1.2, h0, h1),
                 kind="vwap_fade", vol=1.2, hh0=h0, hh1=h1, dist=1.2,
                 session="mt", title=f"VWAP fade · {tag} · seg–qui")
            _add(f"ETH_VWAP_FADE_{tag}_V13",
                 _vwap_fade_h(1.3, 1.2, h0, h1),
                 kind="vwap_fade", vol=1.3, hh0=h0, hh1=h1, dist=1.2,
                 title=f"VWAP fade vol≥1.3× · {tag}")

    for tag, h0, h1 in M30_SESS:
        _add(f"ETH_VWAP_FADE_{tag}_M30",
             _vwap_fade_h(1.2, 1.2, h0, h1),
             kind="vwap_fade", vol=1.2, hh0=h0, hh1=h1, dist=1.2, tf=30,
             title=f"VWAP fade · {tag} · M30")

    # ── Engolfo ──
    for tag, h0, h1 in SESS:
        for vol in (1.3, 1.4, 1.5):
            vtag = "" if abs(vol - 1.4) < 1e-9 else f"_V{int(vol * 10)}"
            _add(f"ETH_ENGULF_{tag}{vtag}",
                 _engulf_h(vol, h0, h1),
                 kind="engulf", vol=vol, hh0=h0, hh1=h1,
                 title=f"engulf vol≥{vol}× · {tag}")
            if tag in CORE and abs(vol - 1.4) < 1e-9:
                _add(f"ETH_ENGULF_{tag}_MT",
                     _engulf_h(vol, h0, h1),
                     kind="engulf", vol=vol, hh0=h0, hh1=h1, session="mt",
                     title=f"engulf · {tag} · seg–qui")
        if tag in CORE:
            _add(f"ETH_ENGULF_{tag}_TP20",
                 _engulf_h(1.4, h0, h1),
                 kind="engulf", vol=1.4, hh0=h0, hh1=h1, tp_r=2.0,
                 title=f"engulf · {tag} · TP 2R")

    for tag, h0, h1 in M30_SESS:
        for vol in (1.3, 1.4, 1.5):
            vtag = "" if abs(vol - 1.4) < 1e-9 else f"_V{int(vol * 10)}"
            _add(f"ETH_ENGULF_{tag}{vtag}_M30",
                 _engulf_h(vol, h0, h1),
                 kind="engulf", vol=vol, hh0=h0, hh1=h1, tf=30,
                 title=f"engulf vol≥{vol}× · {tag} · M30")
        if tag in ("24H", "ASIA", "NY", "LN"):
            _add(f"ETH_ENGULF_{tag}_MT_M30",
                 _engulf_h(1.4, h0, h1),
                 kind="engulf", vol=1.4, hh0=h0, hh1=h1, session="mt", tf=30,
                 title=f"engulf · {tag} · M30 · seg–qui")

    # ── H4-PB-EMA ──
    for tag, h0, h1 in SESS:
        _add(f"ETH_H4_PB_{tag}",
             _h4_pb_ema_h(h0, h1),
             kind="h4_pb", hh0=h0, hh1=h1, tp_r=2.0,
             title=f"H4 PB EMA · {tag}")
        if tag in CORE:
            _add(f"ETH_H4_PB_{tag}_MT",
                 _h4_pb_ema_h(h0, h1),
                 kind="h4_pb", hh0=h0, hh1=h1, session="mt", tp_r=2.0,
                 title=f"H4 PB EMA · {tag} · seg–qui")
            # TP 1.5 via _cap_tp (h4_pb default 2.0)
            _add(f"ETH_H4_PB_{tag}_TP15",
                 _cap_tp(_h4_pb_ema_h(h0, h1), 1.5),
                 kind="h4_pb", hh0=h0, hh1=h1, tp_r=1.5,
                 title=f"H4 PB EMA · {tag} · TP 1.5R")

    for tag, h0, h1 in M30_SESS:
        _add(f"ETH_H4_PB_{tag}_M30",
             _h4_pb_ema_h(h0, h1),
             kind="h4_pb", hh0=h0, hh1=h1, tp_r=2.0, tf=30,
             title=f"H4 PB EMA · {tag} · M30")
        if tag in ("24H", "NY", "LN", "ASIA"):
            _add(f"ETH_H4_PB_{tag}_MT_M30",
                 _h4_pb_ema_h(h0, h1),
                 kind="h4_pb", hh0=h0, hh1=h1, session="mt", tp_r=2.0, tf=30,
                 title=f"H4 PB EMA · {tag} · M30 · seg–qui")


_build_battery()


def _sim(df, sym, fn, name, tf=15):
    from backtest_setups_novos_v2 import ONE_PER_DAY as _v2_opd
    added = name in ONE_PER_DAY and name not in _v2_opd
    if added:
        _v2_opd.add(name)
    try:
        return _simulate(df, sym, fn, name, tf=tf)
    finally:
        if added:
            _v2_opd.discard(name)


def _worker_init(dfs_by_key):
    global _WORKER_DFS
    _WORKER_DFS = dfs_by_key


def _run_one(name: str) -> dict:
    try:
        sym = STRAT_SYM[name]
        tf = STRAT_TF.get(name, 15)
        key = f"{sym}_M{tf}"
        df = _WORKER_DFS.get(key)
        if df is None:
            return {"setup": name, "sym": sym, "tf": tf, "n": 0,
                    "veredito": "SEM DADOS", "err": f"missing {key}"}
        fn = STRATS[name]
        t0 = time.time()
        t = _sim(df, sym, fn, name, tf=tf)
        s = stats(t)
        cons = consistency(t)
        s_out = {"n": 0, "net": None, "PF": None}
        if not t.empty:
            t = t.sort_values("ts").reset_index(drop=True)
            cut = int(len(t) * (1 - OOS_FRAC))
            s_out = stats(t.iloc[cut:])
        v = verdict(s, cons, s_out)
        row = {
            "setup": name, "sym": sym, "tf": tf, **s,
            "cons%": cons, "veredito": v,
            "oos_net": s_out.get("net"), "oos_pf": s_out.get("PF"),
            "oos_n": s_out.get("n", 0),
            "secs": round(time.time() - t0, 1),
        }
        if "🟢" in str(v) and "GO" in str(v) and not t.empty:
            row["_trades"] = t
            row["_meta"] = STRAT_META.get(name)
        return row
    except Exception as exc:
        return {"setup": name, "sym": STRAT_SYM.get(name, "?"),
                "tf": STRAT_TF.get(name, 15), "n": 0,
                "veredito": f"ERRO: {exc}", "err": traceback.format_exc()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bars", type=int, default=50000)
    ap.add_argument("--syms", default="ETHUSD")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--only", default="", help="csv de nomes (debug)")
    args = ap.parse_args()

    want = {s.strip().upper() for s in args.syms.split(",") if s.strip()}
    os.environ["KIMI_GROUP"] = "crypto"
    os.makedirs("logs", exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    log_path = f"logs/backtest_setups_novos_v18_eth_ema_vwap_{stamp}.txt"
    logf = open(log_path, "w", encoding="utf-8")

    def out(s):
        try:
            print(s)
        except UnicodeEncodeError:
            print(s.encode("ascii", "replace").decode("ascii"))
        logf.write(s + "\n")
        logf.flush()

    only = {x.strip() for x in args.only.split(",") if x.strip()}
    strat_names = [n for n in STRATS
                   if STRAT_SYM[n] in want and (not only or n in only)]
    by_kind = {}
    for n in strat_names:
        k = STRAT_META.get(n, {}).get("kind", "?")
        by_kind[k] = by_kind.get(k, 0) + 1
    out(f"{'#' * 74}\n# SETUPS NOVOS V18 — ETH EMA/VWAP/ENGULF/H4-PB "
        f"({len(strat_names)} setups) · {datetime.now():%d/%m %H:%M}\n"
        f"# Por kind: {by_kind} · workers={args.workers}\n"
        f"# Régua: net≥+0.10 · PF≥1.25 · cons≥55% · OOS+ · n≥40\n{'#' * 74}")

    from backtest_crypto_pro import mt5_connect
    mt5 = mt5_connect()

    need_keys = sorted({f"{STRAT_SYM[n]}_M{STRAT_TF.get(n, 15)}"
                        for n in strat_names})
    dfs = {}
    for key in need_keys:
        sym, _, tf_s = key.partition("_M")
        tf = int(tf_s)
        if sym not in want:
            continue
        t0 = time.time()
        raw = _load_ohlc(mt5, sym, tf, args.bars, out)
        if raw is None or len(raw) < 2000:
            out(f"\n!! {key}: sem histórico")
            continue
        df = add_extra_v2(add_indicators(raw), sym, ctx=None)
        dfs[key] = df
        anos = len(df) / (96 if tf == 15 else 48) / 365
        out(f"\n{'=' * 74}\n  {key}: {len(df)} candles (~{anos:.1f}a)  "
            f"[{df.index[0].date()} -> {df.index[-1].date()}] "
            f"({time.time() - t0:.0f}s)\n{'=' * 74}")

    ranking = []
    t_all = time.time()
    n_done = 0
    workers = max(1, int(args.workers))

    if workers == 1:
        _worker_init(dfs)
        for name in strat_names:
            row = _run_one(name)
            trades = row.pop("_trades", None)
            row.pop("_meta", None)
            ranking.append(row)
            n_done += 1
            out(f"\n  ▸ {row['setup']}  ({row.get('sym')} M{row.get('tf')})  "
                f"[{row.get('secs', '?')}s]")
            if row.get("n", 0):
                out(f"      n={int(row['n'])} net {row.get('net', 0):+.3f} R  "
                    f"PF {row.get('PF')} cons {row.get('cons%')}%  "
                    f"OOS {row.get('oos_net')}  {row.get('veredito')}")
            else:
                out(f"      {row.get('veredito', 'sem trades')}")
            if trades is not None:
                trades.to_csv(
                    f"logs/novos_v18_{row['sym']}_M{row['tf']}_{row['setup']}.csv",
                    index=False)
            if n_done % 25 == 0:
                out(f"  … progresso {n_done}/{len(strat_names)}")
    else:
        out(f"\n# paralelizando com {workers} workers…")
        with ProcessPoolExecutor(
            max_workers=workers,
            initializer=_worker_init,
            initargs=(dfs,),
        ) as ex:
            futs = {ex.submit(_run_one, n): n for n in strat_names}
            for fut in as_completed(futs):
                row = fut.result()
                trades = row.pop("_trades", None)
                row.pop("_meta", None)
                ranking.append(row)
                n_done += 1
                out(f"\n  ▸ {row['setup']}  ({row.get('sym')} M{row.get('tf')})  "
                    f"[{row.get('secs', '?')}s]")
                if row.get("n", 0):
                    out(f"      n={int(row['n'])} net {row.get('net', 0):+.3f} R  "
                        f"PF {row.get('PF')} cons {row.get('cons%')}%  "
                        f"OOS {row.get('oos_net')}  {row.get('veredito')}")
                else:
                    out(f"      {row.get('veredito', 'sem trades')}")
                if trades is not None:
                    trades.to_csv(
                        f"logs/novos_v18_{row['sym']}_M{row['tf']}_{row['setup']}.csv",
                        index=False)
                if n_done % 25 == 0:
                    out(f"  … progresso {n_done}/{len(strat_names)} "
                        f"({time.time() - t_all:.0f}s)")

    out(f"\n{'#' * 74}\n  RANKING V18 ETH EMA/VWAP  ({time.time() - t_all:.0f}s total)\n"
        f"{'#' * 74}")
    rk = pd.DataFrame(ranking)
    goes, yellows, reds = [], [], []
    goes_meta = []
    if not rk.empty:
        rk["net"] = pd.to_numeric(rk.get("net"), errors="coerce")
        rk = rk.sort_values("net", ascending=False, na_position="last")
        for _, r in rk.iterrows():
            if r.get("n", 0) == 0:
                out(f"  {r.setup:<40} sem trades / {r.get('veredito')}")
                continue
            out(f"  {r.setup:<40} n={int(r.n):<5} net {r.net:+.3f} R  "
                f"PF {r.PF:<5} cons {r['cons%']}%  OOS {r.oos_net}  {r.veredito}")
            vs = str(r.veredito)
            if "🟢" in vs and "GO" in vs:
                goes.append(r.setup)
                m = STRAT_META.get(r.setup)
                if m:
                    goes_meta.append({**m, "net": float(r.net), "PF": float(r.PF),
                                      "cons": float(r["cons%"]), "n": int(r.n),
                                      "oos_net": r.oos_net})
            elif "🟡" in vs:
                yellows.append(r.setup)
            else:
                reds.append(r.setup)
        save = rk.drop(columns=[c for c in ("err",) if c in rk.columns],
                       errors="ignore")
        save.to_csv(f"logs/backtest_setups_novos_v18_eth_ema_vwap_ranking_{stamp}.csv",
                    index=False)
        if goes_meta:
            import json
            with open(f"logs/v18_goes_meta_{stamp}.json", "w", encoding="utf-8") as f:
                json.dump(goes_meta, f, indent=2, ensure_ascii=False)

    out(f"\n# GO encontrados ({len(goes)}): {goes if goes else 'nenhum'}")
    out(f"# amarelos ({len(yellows)}): {yellows[:80]}{'…' if len(yellows) > 80 else ''}")
    out(f"# vermelhos/fracos: {len(reds)}")
    out(f"# FIM — {log_path}")
    logf.close()
    print(f"\n-> {log_path}")
    if goes:
        print("GO:", ", ".join(goes))
    print(f"TOTAL setups: {len(strat_names)}")


if __name__ == "__main__":
    main()
