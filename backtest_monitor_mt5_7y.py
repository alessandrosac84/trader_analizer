"""
backtest_monitor_mt5_7y.py — Backtest das regras do Monitor MT5 (legado v6).

NÃO é WinGo / V7 / Scalper / Ações / Crypto.
Reutiliza backtest_pro.simulate (exits fixed + custos reais + guards v2)
e aplica o gate de score da produção (trade_executor.MIN_SCORE):
  WIN >= 9 | WDO >= 7

Uso (MT5 aberto, Market Watch com contínuos):
  python backtest_monitor_mt5_7y.py
  python backtest_monitor_mt5_7y.py --no-min-score   # só threshold do generate_signal (±7)

Saída: logs/monitor_mt5_7y_*.csv + relatório no console / log txt.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from backtest_pro import fetch_mt5, simulate, report, _stats
import backtest_pro as bp
from services.technical_analysis import generate_signal as _generate_signal_raw
from services.monitor_mt5_rules import MONITOR_RULES, asset_key as _asset_key

SYMBOLS = ["WIN$D", "WDO$D"]

# Espelha trade_executor / monitor_mt5_rules.
MIN_SCORE = {
    k: int(v.get("min_score", 7)) for k, v in MONITOR_RULES.items()
}


def _patch_monitor_rules(asset: str, use_calibrated: bool = True):
    """Aplica gates do Monitor (score + DOW + target_rr). Horário via simulate(hours=)."""
    cfg = MONITOR_RULES.get(asset, {}) if use_calibrated else {}
    min_score = int(cfg.get("min_score") or MIN_SCORE.get(asset, 7))
    max_score = int(cfg.get("max_score") or 99)
    block_dow = tuple(cfg.get("block_dow") or ())
    target_rr = cfg.get("target_rr")
    adx_min = cfg.get("adx_min")
    vol_min = cfg.get("vol_min")

    def wrapped(df, htf_df=None):
        sig = _generate_signal_raw(df, htf_df=htf_df, target_rr=target_rr)
        if not sig:
            return sig
        if sig.get("acao") in ("COMPRA", "VENDA"):
            sc = abs(float(sig.get("score") or 0))
            if sc < min_score or sc > max_score:
                out = dict(sig)
                out["acao"] = "NEUTRO"
                out["motivo"] = (
                    f"score {sig.get('score')} fora [{min_score},{max_score}] (Monitor gate)"
                )
                return out
            if adx_min is not None:
                adx = sig.get("adx")
                if adx is None or float(adx) < float(adx_min):
                    out = dict(sig)
                    out["acao"] = "NEUTRO"
                    out["motivo"] = f"adx {adx} < {adx_min} (Monitor gate)"
                    return out
            if vol_min is not None:
                vr = sig.get("vol_ratio")
                if vr is None:
                    try:
                        vv = float(df["Volume"].iloc[-1] or 0)
                        va = float(df["Volume"].tail(20).mean() or 0)
                        vr = (vv / va) if va > 0 else None
                    except Exception:
                        vr = None
                if vr is None or float(vr) < float(vol_min):
                    out = dict(sig)
                    out["acao"] = "NEUTRO"
                    out["motivo"] = f"vol {vr} < {vol_min} (Monitor gate)"
                    return out
            if block_dow:
                try:
                    dow = int(df.index[-1].dayofweek)
                    if dow in block_dow:
                        out = dict(sig)
                        out["acao"] = "NEUTRO"
                        out["motivo"] = f"block_dow={dow} (Monitor gate)"
                        return out
                except Exception:
                    pass
        return sig

    bp.generate_signal = wrapped
    return cfg


def _restore_signal():
    bp.generate_signal = _generate_signal_raw


# compat: nome antigo usado em diffs/logs
def _patch_min_score(min_score: int):
    def wrapped(df, htf_df=None):
        sig = _generate_signal_raw(df, htf_df=htf_df)
        if not sig:
            return sig
        if sig.get("acao") in ("COMPRA", "VENDA"):
            sc = abs(float(sig.get("score") or 0))
            if sc < min_score:
                out = dict(sig)
                out["acao"] = "NEUTRO"
                out["motivo"] = f"score {sig.get('score')} < min {min_score} (Monitor gate)"
                return out
        return sig

    bp.generate_signal = wrapped


class Tee:
    def __init__(self, path):
        self.file = open(path, "a", encoding="utf-8")
        self.stdout = sys.stdout

    def write(self, data):
        try:
            self.stdout.write(data)
        except UnicodeEncodeError:
            enc = getattr(self.stdout, "encoding", None) or "utf-8"
            self.stdout.write(data.encode(enc, errors="replace").decode(enc, errors="replace"))
        self.file.write(data)
        self.file.flush()

    def flush(self):
        self.stdout.flush()
        self.file.flush()


def _safe_report(trades, sym, engine):
    """report() do backtest_pro usa box-drawing unicode — falha em cp1252."""
    try:
        report(trades, sym, engine)
    except UnicodeEncodeError:
        s = _stats(trades)
        print(f"\n{'=' * 66}\n  {sym} — engine {engine.upper()}\n{'=' * 66}")
        if not s or s.get("n", 0) == 0:
            print("  (nenhum trade)"); return
        print(f"  Trades {s['n']} | win {s['win%']}% | exp bruta {s['exp_gross_R']:+.3f} R"
              f" | custo medio {s['cost_R_avg']:.3f} R | exp LIQUIDA {s['exp_net_R']:+.3f} R")
        print(f"  PF {s['PF']} | total {s['total_net_R']:+.1f} R | maxDD {s['maxDD_R']} R")
        if not trades.empty and "setup" in trades.columns:
            for k, g in trades.groupby("setup"):
                gs = _stats(g)
                print(f"    setup {k}: n={gs['n']} exp_net {gs['exp_net_R']:+.3f} R  PF {gs['PF']}")
        t2 = trades.copy()
        t2["periodo"] = __import__("pandas").to_datetime(t2["open_ts"]).dt.to_period("2M").astype(str)
        print("  -- walk-forward (bimestre) --")
        pos_p = tot_p = 0
        for k, g in t2.groupby("periodo"):
            gs = _stats(g)
            pos_p += gs["exp_net_R"] > 0; tot_p += 1
            mark = "+" if gs["exp_net_R"] > 0 else "-"
            print(f"    {k}  n={gs['n']:<4} exp_net {gs['exp_net_R']:+.3f} R  [{mark}]")
        if tot_p:
            print(f"  Consistencia: {pos_p}/{tot_p} periodos positivos ({pos_p/tot_p*100:.0f}%)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bars", type=int, default=200000)
    ap.add_argument("--no-min-score", action="store_true",
                    help="não aplica gate MIN_SCORE da produção (só ±7 do motor)")
    ap.add_argument("--baseline", action="store_true",
                    help="só gate min_score legado (sem janela A/B, DOW, RR calibrado)")
    ap.add_argument("--symbol", default=None,
                    help="um símbolo só (ex. 'WIN$D'); default = WIN$D e WDO$D")
    args = ap.parse_args()

    # Evita UnicodeEncodeError no console Windows (cp1252) durante o report.
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    os.makedirs("logs", exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    logpath = f"logs/monitor_mt5_7y_{stamp}.txt"
    sys.stdout = Tee(logpath)

    symbols = [args.symbol] if args.symbol else list(SYMBOLS)

    print(f"\n{'#' * 70}")
    print(f"# BACKTEST Monitor MT5 (v6 / exits fixed / custos reais)")
    print(f"# início {datetime.now():%d/%m/%Y %H:%M:%S}")
    mode = (
        "OFF (motor ±7)" if args.no_min_score
        else ("BASELINE min_score" if args.baseline else "CALIBRADO monitor_mt5_rules")
    )
    print(f"# gate: {mode}")
    print(f"# Log: {logpath}")
    print(f"# Pedido: ~7 anos — usa máximo disponível no MT5 (WIN$D / WDO$D)")
    print(f"{'#' * 70}\n")

    t0 = time.time()
    data = {}
    for sym in symbols:
        try:
            df = fetch_mt5(sym, "15", args.bars)
            if df is None or len(df) < 1000:
                print(f"!! {sym}: sem dados — pulando")
                continue
            anos = len(df) / 28 / 252
            print(f"{sym}: {len(df)} candles M15 "
                  f"({df.index[0]} -> {df.index[-1]}) ~{anos:.1f} anos")
            if anos < 7.0:
                print(f"  AVISO: histórico < 7 anos (faltam ~{7.0 - anos:.1f} a). "
                      f"MT5/broker não entrega mais barras para este contínuo.")
            data[sym] = df
        except Exception:
            print(f"!! {sym}: erro:\n{traceback.format_exc()}")

    summary_rows = []
    for sym, df in data.items():
        key = _asset_key(sym)
        cfg = {} if (args.no_min_score or args.baseline) else MONITOR_RULES.get(key, {})
        min_sc = None if args.no_min_score else int(
            (cfg.get("min_score") if cfg else None) or MIN_SCORE.get(key, 7)
        )
        if args.no_min_score:
            tag = "v6_fixed"
        elif args.baseline:
            tag = f"v6_fixed_min{min_sc}"
        else:
            tag = f"v6_cal_{cfg.get('hours_label', 'all')}_rr{cfg.get('target_rr', 1.5)}"
        print(f"\n>>> [{datetime.now():%H:%M:%S}] {sym} — Monitor MT5 ({tag})")
        if cfg:
            print(f"  regras: {cfg.get('note', cfg)}")
        try:
            hours = None
            if args.no_min_score:
                _restore_signal()
            elif args.baseline:
                _patch_min_score(min_sc)
            else:
                cfg = _patch_monitor_rules(key, use_calibrated=True)
                hours = cfg.get("hours")
            t1 = time.time()
            trades = simulate(df, sym, engine="v6", exits="fixed",
                              use_ticks=False, mt5_symbol=sym, hours=hours)
            _restore_signal()
            # setup label explícito
            if not trades.empty:
                trades = trades.copy()
                trades["setup"] = "MONITOR_MT5_V6"
                trades["min_score_gate"] = min_sc if min_sc is not None else 0

            out = f"logs/monitor_mt5_7y_{sym.replace('$', '_')}_{tag}.csv"
            trades.to_csv(out, index=False)
            print(f"  detalhe: {out}  ({time.time() - t1:.0f}s)")
            _safe_report(trades, sym, f"MonitorMT5/{tag}")

            s = _stats(trades)
            periodo = "-"
            if not trades.empty:
                periodo = (f"{pd_to_date(trades['open_ts'].min())}"
                           f" -> {pd_to_date(trades['open_ts'].max())}")
            summary_rows.append((sym, tag, s, periodo, str(df.index[0]), str(df.index[-1])))
        except Exception:
            _restore_signal()
            print(f"!! ERRO em {sym}:\n{traceback.format_exc()}")

    print(f"\n{'=' * 70}")
    print("RESUMO Monitor MT5 (WIN vs WDO)")
    print(f"{'=' * 70}")
    print(f"{'Symbol':<10} {'tag':<18} {'n':>5} {'WR%':>6} {'PF':>6} "
          f"{'exp_net':>8} {'net_R':>8} {'maxDD':>7}  trades")
    for sym, tag, s, periodo, d0, d1 in summary_rows:
        if not s or s.get("n", 0) == 0:
            print(f"{sym:<10} {tag:<18} (sem trades)  data={d0}->{d1}")
            continue
        print(f"{sym:<10} {tag:<18} {s['n']:>5} {s['win%']:>6.1f} {s['PF']:>6} "
              f"{s['exp_net_R']:>+8.3f} {s['total_net_R']:>+8.1f} {s['maxDD_R']:>7.1f}  "
              f"{periodo}")
        print(f"           candles: {d0} -> {d1}")

    print(f"\n{'#' * 70}")
    print(f"# FIM — {datetime.now():%d/%m/%Y %H:%M:%S} "
          f"(total {(time.time() - t0) / 60:.1f} min)")
    print(f"# Log: {logpath}")
    print(f"{'#' * 70}")


def pd_to_date(x):
    import pandas as pd
    return pd.to_datetime(x).date()


if __name__ == "__main__":
    main()
