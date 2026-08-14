"""Valida MONITOR_RULES no cache de sinais (rápido) — espelha o pacote calibrado."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from backtest_pro import fetch_mt5
from services.monitor_mt5_rules import MONITOR_RULES
from sweep_monitor_mt5_rules import (
    HOUR_KEYS,
    _metrics,
    load_signal_cache,
    prepare_bars,
    replay,
)


def main():
    for asset, sym in (("WIN", "WIN$D"), ("WDO", "WDO$D")):
        cfg = MONITOR_RULES[asset]
        cands = sorted(
            Path("logs").glob(f"sweep_mt5_{asset}_sigs_*.csv"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not cands:
            print(f"{asset}: sem cache")
            continue
        print(f"\n=== {asset} cache={cands[0].name} ===", flush=True)
        print(f"regras: {cfg.get('note')}", flush=True)
        df = fetch_mt5(sym, "15", 200000)
        bars = prepare_bars(df)
        sigs = load_signal_cache(cands[0], bars)
        hkey = cfg.get("hours_label") or "all"
        replay_cfg = {
            "min_score": cfg["min_score"],
            "max_score": cfg.get("max_score", 11),
            "hours": hkey,
            "hours_arg": HOUR_KEYS.get(hkey),
            "adx_min": cfg.get("adx_min"),
            "vol_min": cfg.get("vol_min"),
            "htf_aligned": False,
            "block_dow": tuple(cfg["block_dow"]) if cfg.get("block_dow") else None,
        }
        # baseline
        base = {
            "min_score": 9 if asset == "WIN" else 7,
            "max_score": 11,
            "hours": "all",
            "hours_arg": None,
            "adx_min": None,
            "vol_min": None,
            "htf_aligned": False,
            "block_dow": None,
        }
        mb = _metrics(replay(bars, sym, sigs, base))
        mn = _metrics(replay(bars, sym, sigs, replay_cfg, target_rr=cfg.get("target_rr")))
        for tag, m in (("BASE", mb), ("NOVO", mn)):
            print(
                f"  {tag}: n={m['n']} WR={m['win%']:.1f}% PF={m['PF']} "
                f"exp={m['exp_net_R']:+.3f} net={m['total_net_R']:+.1f} "
                f"OOS={m['oos%']:.0f}% DD={m['maxDD_R']}",
                flush=True,
            )
        go = (
            mn["exp_net_R"] >= 0.10
            and mn["PF"] >= 1.25
            and mn["oos%"] >= 55
            and mn["n"] >= 40
        )
        print(f"  GO={go}", flush=True)


if __name__ == "__main__":
    main()
