"""
backtest_pro.py — Backtest v7 (Fase 1): CUSTOS REAIS + exits geridos + walk-forward.

Não altera o backtest_mt5.py nem qualquer módulo em produção.

O que ele responde:
  1. Expectância LÍQUIDA (após custos da nota Santander) do motor atual (v6).
  2. Motor v7 (regime + níveis + confluência) na mesma régua.
  3. Estabilidade por bimestre (walk-forward), por regime, setup e horário.

⚠️ HISTÓRICO: símbolo de contrato (WINQ26) só tem a vida do contrato (~2-5
meses). Para os 2,7 anos use o CONTRATO CONTÍNUO da corretora, se existir
(ex.: WIN$, WIN$N, WDO$). Rode `python backtest_pro.py --list` para ver os
símbolos WIN*/WDO* disponíveis no seu Market Watch e escolha o de maior
histórico: `python backtest_pro.py --symbol WIN$N`.

Uso:
  python backtest_pro.py                       # WIN e WDO do .env, v6 vs v7
  python backtest_pro.py --list                # lista símbolos WIN*/WDO*
  python backtest_pro.py --symbol WDOU26 --engine v7
  python backtest_pro.py --engine v6 --exits fixed   # produção exata + custos
  python backtest_pro.py --ticks               # v7 com gatilho de fluxo (lento)
  python backtest_pro.py --csv dados.csv --symbol WIN   # offline

Saída: relatório no console + CSV em logs/backtest_pro_<sym>_<engine>.csv
"""
import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

import numpy as np
import pandas as pd

from services.trading_costs import roundtrip_cost_pts, fees_pts, _cfg as _cost_cfg
from services.market_regime import build_daily_refs
from services.technical_analysis_v7 import generate_signal_v7
from services.technical_analysis import generate_signal, compute_indicators

LOOKBACK       = 80      # candles mínimos antes de gerar sinal
WINDOW         = 600     # janela deslizante p/ indicadores (O(n) em vez de O(n²))
LIMIT_TTL      = 3       # candles de validade da ordem limite
TIME_STOP_N    = 4       # candles sem 0.5R -> sai
PARTIAL_AT_R   = 1.0
TRAIL_ATR      = 1.0
TP2_R          = 2.5
EOD_MIN        = 16 * 60 + 55   # zeragem 16:55


# ── Dados ──────────────────────────────────────────────────────────────────

def _mt5_connect():
    import MetaTrader5 as mt5
    if mt5.terminal_info() is None:
        login = int(os.getenv("MT5_LOGIN", "0") or 0)
        kw = {}
        path = os.getenv("MT5_PATH", "")
        if path and os.path.exists(path): kw["path"] = path
        pw, srv = os.getenv("MT5_PASSWORD", ""), os.getenv("MT5_SERVER", "")
        if login and pw and srv and srv.lower() not in ("metaquotes-demo", "metaquotes-demo2"):
            kw.update(login=login, password=pw, server=srv)
        if not mt5.initialize(**kw):
            print("MT5 não inicializou:", mt5.last_error()); sys.exit(1)
    return mt5


def list_symbols():
    mt5 = _mt5_connect()
    print("Símbolos WIN*/WDO* disponíveis (nome | candles 15m | desde | descrição):")
    print("(baixando histórico de cada um — pode levar alguns segundos por símbolo)\n")
    for pref in ("WIN", "WDO"):
        for s in (mt5.symbols_get(pref + "*") or []):
            # FIX: sem symbol_select o MT5 devolve vazio para símbolo fora do
            # Market Watch — era por isso que tudo aparecia com candles15m=0.
            if not mt5.symbol_select(s.name, True):
                print(f"  {s.name:<12} (não selecionável)")
                continue
            info = mt5.symbol_info(s.name)
            # Pede em blocos decrescentes: o terminal limita por 'Max bars'.
            rates = None
            for count in (200000, 100000, 50000, 10000, 1000):
                rates = mt5.copy_rates_from_pos(s.name, mt5.TIMEFRAME_M15, 0, count)
                if rates is not None and len(rates) > 0:
                    break
            n = len(rates) if rates is not None else 0
            first = (pd.to_datetime(rates[0]["time"], unit="s") if n else "-")
            anos = f" (~{n/28/252:.1f} anos)" if n else ""
            print(f"  {s.name:<12} candles15m={n:<7}{anos} desde={first}  {getattr(info,'description','')}")
    print("\nDica: para o backtest longo use o contínuo com AJUSTE POR DIFERENÇA")
    print("  (WIN$D / WDO$D): preserva distâncias em pontos entre os vencimentos —")
    print("  o ideal para estratégias baseadas em pontos/ATR. O 'Sem Ajustes' ($N)")
    print("  tem os preços reais mas salta no rollover, distorcendo indicadores.")
    print("  Ex.: python backtest_pro.py --symbol WIN$D")


def fetch_mt5(symbol: str, tf_key: str, bars: int):
    mt5 = _mt5_connect()
    _TF = {"5": mt5.TIMEFRAME_M5, "15": mt5.TIMEFRAME_M15, "30": mt5.TIMEFRAME_M30,
           "60": mt5.TIMEFRAME_H1}
    # PowerShell engole o "$D" de WIN$D (vira variável vazia) e o símbolo chega
    # como "WIN"/"WDO". Auto-resolve para o contínuo com ajuste por diferença.
    if symbol.upper() in ("WIN", "WDO") and not mt5.symbol_select(symbol, True):
        auto = symbol.upper() + "$D"
        if mt5.symbol_select(auto, True):
            print(f"⚠️  '{symbol}' não existe — usando o contínuo '{auto}' "
                  f"(no PowerShell use aspas simples: --symbol '{symbol}$D')")
            symbol = auto
    if not mt5.symbol_select(symbol, True):
        print(f"símbolo {symbol} não disponível"); return None
    # O terminal rejeita pedidos acima do limite "Max bars" com Invalid params.
    # Pede em blocos decrescentes até obter dados (mesma técnica do --list).
    tf = _TF.get(tf_key, mt5.TIMEFRAME_M15)
    rates = None
    for count in (bars, 150000, 100000, 60000, 46000, 30000, 20000, 10000, 5000, 1000):
        if count > bars:
            continue
        rates = mt5.copy_rates_from_pos(symbol, tf, 0, count)
        if rates is not None and len(rates) > 0:
            break
    if rates is None or len(rates) == 0:
        print(f"sem candles para {symbol}: {mt5.last_error()}"); return None
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df = df.set_index("time")
    vol = df["real_volume"] if df["real_volume"].sum() > 0 else df["tick_volume"]
    return pd.DataFrame({"Open": df["open"], "High": df["high"], "Low": df["low"],
                         "Close": df["close"], "Volume": vol}, index=df.index)


def load_csv(path: str):
    df = pd.read_csv(path)
    tcol = next((c for c in df.columns if c.lower() in ("time", "datetime", "date")), df.columns[0])
    df[tcol] = pd.to_datetime(df[tcol])
    df = df.set_index(tcol)
    df = df.rename(columns={c: c.capitalize() for c in df.columns})
    return df[["Open", "High", "Low", "Close", "Volume"]]


def resample_h1(df15):
    return df15.resample("1h").agg({"Open": "first", "High": "max", "Low": "min",
                                    "Close": "last", "Volume": "sum"}).dropna()


# ── Gatilho de fluxo histórico (opcional, --ticks) ─────────────────────────

def tick_trigger_ok(symbol, ts, direction):
    try:
        import MetaTrader5 as mt5
        from services.flow_trigger import confirm_from_ticks
        end = ts.to_pydatetime().replace(tzinfo=timezone.utc)
        raw = mt5.copy_ticks_range(symbol, end - timedelta(seconds=60), end, mt5.COPY_TICKS_ALL)
        if raw is None or len(raw) == 0:
            return False, "sem ticks"
        ticks = [{k: r[k] for k in raw.dtype.names} for r in raw]
        d = confirm_from_ticks(ticks, direction, end.timestamp())
        return d["fire"], d["reason"]
    except Exception as exc:
        return False, f"tick err: {exc}"


# ── Simulação ──────────────────────────────────────────────────────────────

def _min_of_day(ts): return ts.hour * 60 + ts.minute


def simulate(df15, symbol, engine="v7", exits="managed", use_ticks=False,
             mt5_symbol=None, hours=None):
    """Retorna DataFrame de trades com resultado bruto e líquido em R.
    hours: (min_inicial, min_final) em minutos do dia — restringe NOVAS
    entradas à janela (ex.: (9*60+15, 11*60+30) = 09:15-11:30)."""
    h1 = resample_h1(df15)
    daily_refs = build_daily_refs(df15)
    atr_series = compute_indicators(df15)["atr"]

    trades = []
    pos = None
    pending = None

    for i in range(LOOKBACK, len(df15)):
        ts   = df15.index[i]
        o, h, l, c = [float(df15.iloc[i][k]) for k in ("Open", "High", "Low", "Close")]
        minute = _min_of_day(ts)

        # ── gestão de posição aberta ─────────────────────────────────────
        if pos:
            buy  = pos["dir"] == "COMPRA"
            risk = pos["risk"]
            hit_stop = (l <= pos["stop"]) if buy else (h >= pos["stop"])
            hit_tp1  = pos["tp1"] and ((h >= pos["tp1"]) if buy else (l <= pos["tp1"]))

            def _r_of(px):
                return ((px - pos["entry"]) / risk) if buy else ((pos["entry"] - px) / risk)

            closed = False
            # v7.4: setups com "exit_policy: FIXED" (GAP_FADE, ORB_RETEST) usam
            # saída fixa mesmo em modo managed — foi a config que os validou.
            if exits == "fixed" or pos.get("fixed_exit"):
                if hit_stop:
                    pos["legs"].append((1.0, -1.0, True)); closed = True
                elif hit_tp1:
                    pos["legs"].append((1.0, _r_of(pos["tp1"]), False)); closed = True
                elif minute >= EOD_MIN:
                    # FIX v2: zeragem EOD também no modo fixed — a produção fecha
                    # no fim do pregão (market_close_scheduler); sem isso a posição
                    # atravessava dias/gaps e inflava o resultado artificialmente.
                    pos["legs"].append((1.0, _r_of(c), True)); closed = True
            else:  # managed
                if hit_stop:
                    frac = 1.0 - pos["closed_frac"]
                    pos["legs"].append((frac, _r_of(pos["stop"]), True)); closed = True
                else:
                    if not pos["partial_done"]:
                        tgt = pos["entry"] + PARTIAL_AT_R * risk * (1 if buy else -1)
                        if (h >= tgt) if buy else (l <= tgt):
                            pos["legs"].append((0.5, PARTIAL_AT_R, False))
                            pos["closed_frac"] += 0.5
                            pos["partial_done"] = True
                            pos["stop"] = pos["entry"]          # breakeven
                    if pos["partial_done"] and not closed:
                        tp2 = pos["entry"] + TP2_R * risk * (1 if buy else -1)
                        if (h >= tp2) if buy else (l <= tp2):
                            pos["legs"].append((0.5, TP2_R, False)); closed = True
                        else:
                            trail = (c - TRAIL_ATR * pos["atr"]) if buy else (c + TRAIL_ATR * pos["atr"])
                            pos["stop"] = max(pos["stop"], trail) if buy else min(pos["stop"], trail)
                    if not pos["partial_done"] and not closed:
                        pos["bars"] += 1
                        if pos["bars"] >= TIME_STOP_N and _r_of(c) < 0.5:
                            pos["legs"].append((1.0, _r_of(c), True)); closed = True
                if not closed and minute >= EOD_MIN:
                    frac = 1.0 - pos["closed_frac"]
                    pos["legs"].append((frac, _r_of(c), True)); closed = True

            if closed:
                gross = sum(f * r for f, r, _ in pos["legs"])
                cfgc = _cost_cfg(symbol)
                cost_pts = fees_pts(symbol)
                if pos["entry_mode"] == "MERCADO":
                    cost_pts += cfgc["tick_size"]
                cost_pts += sum(f * cfgc["tick_size"] for f, _, is_mkt in pos["legs"] if is_mkt)
                net = gross - (cost_pts / risk)
                trades.append({
                    "open_ts": pos["ts"], "close_ts": ts, "dir": pos["dir"],
                    "setup": pos.get("setup"), "regime": pos.get("regime"),
                    "entry_mode": pos["entry_mode"], "hour": pos["ts"].hour,
                    "risk_pts": round(risk, 2), "gross_R": round(gross, 3),
                    "cost_R": round(cost_pts / risk, 3), "net_R": round(net, 3),
                })
                pos = None
            continue

        # ── ordem limite pendente ────────────────────────────────────────
        # FIX v2: ordem pendente NÃO atravessa o dia (produção não carrega
        # ordem overnight; fill em gap de abertura com níveis velhos = artefato)
        if pending and ts.date() != pending.get("day"):
            pending = None
        if pending:
            pending["ttl"] -= 1
            buy = pending["dir"] == "COMPRA"
            filled = (l <= pending["level"]) if buy else (h >= pending["level"])
            if filled:
                pos = {"ts": ts, "dir": pending["dir"], "entry": pending["level"],
                       "stop": pending["stop"], "tp1": pending["tp1"],
                       "risk": abs(pending["level"] - pending["stop"]),
                       "atr": pending["atr"], "entry_mode": "LIMITE",
                       "setup": pending.get("setup"), "regime": pending.get("regime"),
                       "fixed_exit": pending.get("fixed_exit", False),
                       "legs": [], "closed_frac": 0.0, "partial_done": False, "bars": 0}
                pending = None
                continue
            if pending["ttl"] <= 0 or minute >= EOD_MIN:
                pending = None

        # ── novo sinal (sobre candles FECHADOS até i-1) ──────────────────
        if minute >= EOD_MIN:
            continue
        # Janela horária opcional (teste de restrição de horário)
        if hours and not (hours[0] <= minute < hours[1]):
            continue
        # v7 só opera nas janelas A (09:15-11:30, incluindo GAP_FADE às 09:15)
        # e B (14:00-16:30)
        if engine == "v7" and not (9*60+15 <= minute < 11*60+30 or 14*60 <= minute < 16*60+30):
            continue
        # Janela deslizante: indicadores convergem e o custo cai p/ O(n)
        win = df15.iloc[max(0, i - WINDOW):i]
        hwin = h1[h1.index <= win.index[-1]].tail(300)
        hwin = hwin if len(hwin) >= 26 else None

        if engine == "v6":
            sig = generate_signal(win, htf_df=hwin)
            if sig and sig["acao"] in ("COMPRA", "VENDA") and sig.get("stop") and sig.get("tp1"):
                # FIX v2 — guards anti-artefato de gap:
                #   1. entrada só no MESMO dia do sinal (produção não carrega sinal overnight)
                #   2. abertura já além do TP1 = gap-through, fill irreal → descarta
                #   3. risco mínimo de 0.3×ATR (gap que colapsa o risco → "R" explosivo falso)
                _stop  = float(sig["stop"]); _tp1f = float(sig["tp1"])
                _buy   = sig["acao"] == "COMPRA"
                _atr_i = float(atr_series.iloc[i - 1] or 0)
                _risk  = abs(o - _stop)
                _same_day = ts.date() == win.index[-1].date()
                _gap_tp   = (_buy and o >= _tp1f) or ((not _buy) and o <= _tp1f)
                if _same_day and not _gap_tp and _risk >= 0.3 * max(_atr_i, 1e-9):
                    pos = {"ts": ts, "dir": sig["acao"], "entry": o,
                           "stop": _stop, "tp1": _tp1f,
                           "risk": _risk,
                           "atr": _atr_i or _risk,
                           "entry_mode": "MERCADO", "setup": "V6", "regime": None,
                           "legs": [], "closed_frac": 0.0, "partial_done": False, "bars": 0}
        else:  # v7
            sig = generate_signal_v7(win, htf_df=hwin, daily_refs=daily_refs,
                                     symbol=symbol)
            if sig and sig["acao"] in ("COMPRA", "VENDA"):
                if use_ticks and mt5_symbol:
                    ok, _why = tick_trigger_ok(mt5_symbol, win.index[-1], sig["acao"])
                    if not ok:
                        continue
                entry, stop = float(sig["entrada"]), float(sig["stop"])
                if abs(entry - stop) <= 0:
                    continue
                common = {"dir": sig["acao"], "stop": stop, "tp1": float(sig["tp1"]),
                          "atr": float(atr_series.iloc[i - 1] or 0) or abs(entry - stop),
                          "setup": sig.get("setup"), "regime": sig.get("regime"),
                          "fixed_exit": sig.get("exit_policy") == "FIXED"}
                if sig.get("modo_entrada") == "LIMITE":
                    pending = {**common, "level": entry, "ttl": LIMIT_TTL,
                               "day": ts.date()}
                else:
                    # FIX v2: mesmos guards anti-gap do v6 (mesmo dia + risco mínimo)
                    _atr_i = float(atr_series.iloc[i - 1] or 0)
                    _risk  = abs(o - stop)
                    _buy   = sig["acao"] == "COMPRA"
                    _gap_tp = (_buy and o >= float(sig["tp1"])) or \
                              ((not _buy) and o <= float(sig["tp1"]))
                    if (ts.date() == win.index[-1].date() and not _gap_tp
                            and _risk >= 0.3 * max(_atr_i, 1e-9)):
                        pos = {**common, "ts": ts, "entry": o, "risk": _risk,
                               "entry_mode": "MERCADO", "legs": [],
                               "closed_frac": 0.0, "partial_done": False, "bars": 0}

    return pd.DataFrame(trades)


# ── Relatório ──────────────────────────────────────────────────────────────

def _stats(t: pd.DataFrame) -> dict:
    if t.empty:
        return {"n": 0}
    wins = t[t.net_R > 0]; losses = t[t.net_R <= 0]
    gw = wins.net_R.sum(); gl = -losses.net_R.sum()
    eq = t.net_R.cumsum()
    dd = (eq - eq.cummax()).min()
    return {"n": len(t), "win%": round(len(wins) / len(t) * 100, 1),
            "exp_gross_R": round(t.gross_R.mean(), 3),
            "exp_net_R": round(t.net_R.mean(), 3),
            "cost_R_avg": round(t.cost_R.mean(), 3),
            "PF": round(gw / gl, 2) if gl > 0 else float("inf"),
            "total_net_R": round(t.net_R.sum(), 1),
            "maxDD_R": round(dd, 1)}


def report(t: pd.DataFrame, symbol: str, engine: str):
    print(f"\n{'='*66}\n  {symbol} — engine {engine.upper()}\n{'='*66}")
    if t.empty:
        print("  (nenhum trade)"); return
    s = _stats(t)
    print(f"  Trades {s['n']} | win {s['win%']}% | exp bruta {s['exp_gross_R']:+.3f} R"
          f" | custo médio {s['cost_R_avg']:.3f} R | exp LÍQUIDA {s['exp_net_R']:+.3f} R")
    print(f"  PF {s['PF']} | total {s['total_net_R']:+.1f} R | maxDD {s['maxDD_R']} R")

    def _bd(col, label):
        print(f"  ── por {label} ──")
        for k, g in t.groupby(col):
            gs = _stats(g)
            print(f"    {str(k):<14} n={gs['n']:<4} exp_net {gs['exp_net_R']:+.3f} R  PF {gs['PF']}")
    for col, lbl in (("setup", "setup"), ("regime", "regime"), ("hour", "hora"),
                     ("entry_mode", "entrada")):
        if col in t.columns and t[col].notna().any():
            _bd(col, lbl)

    t2 = t.copy()
    t2["periodo"] = pd.to_datetime(t2["open_ts"]).dt.to_period("2M").astype(str)
    print("  ── walk-forward (estabilidade por bimestre) ──")
    pos_p = tot_p = 0
    for k, g in t2.groupby("periodo"):
        gs = _stats(g)
        pos_p += gs["exp_net_R"] > 0; tot_p += 1
        print(f"    {k}  n={gs['n']:<4} exp_net {gs['exp_net_R']:+.3f} R  "
              f"[{'+' if gs['exp_net_R'] > 0 else '-'}]")
    if tot_p:
        print(f"  Consistência: {pos_p}/{tot_p} períodos positivos ({pos_p/tot_p*100:.0f}%)")
    print("  Critério de GO: exp_net >= +0.10 R e PF >= 1.25 e consistência >= 60%")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default=None)
    ap.add_argument("--tf", default="15")
    ap.add_argument("--bars", type=int, default=200000, help="máx disponível do símbolo")
    ap.add_argument("--engine", default="both", choices=["v6", "v7", "both"])
    ap.add_argument("--exits", default="managed", choices=["managed", "fixed"])
    ap.add_argument("--ticks", action="store_true")
    ap.add_argument("--csv", default=None)
    ap.add_argument("--list", action="store_true", help="lista símbolos WIN*/WDO* e sai")
    args = ap.parse_args()

    if args.list:
        list_symbols(); return

    if args.csv:
        symbols = [(args.symbol or "WIN", None)]
        data = {symbols[0][0]: load_csv(args.csv)}
    else:
        syms = [args.symbol] if args.symbol else \
               [os.getenv("WIN_MT5_SYMBOL", "WINQ26"), os.getenv("WDO_MT5_SYMBOL", "WDOU26")]
        symbols = [(s, s) for s in syms]
        data = {}
        for s, _ in symbols:
            df = fetch_mt5(s, args.tf, args.bars)
            if df is not None:
                # "->" em vez de seta unicode: o console do Windows (cp1252)
                # quebra com caracteres especiais quando a saída é redirecionada
                print(f"{s}: {len(df)} candles ({df.index[0]} -> {df.index[-1]})")
                data[s] = df

    os.makedirs("logs", exist_ok=True)
    engines = ["v6", "v7"] if args.engine == "both" else [args.engine]
    for sym, mt5_sym in symbols:
        if sym not in data:
            continue
        for eng in engines:
            exits = "fixed" if (eng == "v6" and args.exits == "fixed") else args.exits
            t = simulate(data[sym], sym, engine=eng, exits=exits,
                         use_ticks=args.ticks and eng == "v7", mt5_symbol=mt5_sym)
            report(t, sym, f"{eng}/{exits}")
            out = f"logs/backtest_pro_{sym}_{eng}.csv"
            t.to_csv(out, index=False)
            print(f"  detalhe: {out}")


if __name__ == "__main__":
    main()
