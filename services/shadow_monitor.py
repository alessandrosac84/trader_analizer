"""
shadow_monitor.py — Motor v7 em MODO SOMBRA (Fase 4).

Roda o pipeline completo do v7 (regime → contexto/nível → gatilho de fluxo)
em paralelo ao Monitor MT5 de produção, SEM ENVIAR NENHUMA ORDEM.
Registra o que o v7 TERIA feito em logs/shadow_v7_trades.csv e acompanha o
resultado simulado até o fechamento (TP/SL/time-stop/EOD).

É o mecanismo de comparação champion (v6 em produção) vs challenger (v7):
depois de 2-3 semanas, comparar o CSV do shadow com os auto_trades reais.

Uso (standalone — NÃO precisa alterar o app.py):
  python -m services.shadow_monitor                # WIN e WDO do .env, loop
  python -m services.shadow_monitor --once         # uma avaliação e sai
  set SHADOW_USE_AI=1                              # liga o veredito de IA (opcional)

Zero alterações em módulos existentes; zero ordens ao MT5.
"""
import argparse
import csv
import logging
import os
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

_CSV = Path(__file__).resolve().parent.parent / "logs" / "shadow_v7_trades.csv"
_COLS = ["id", "ts_open", "ts_close", "symbol", "dir", "setup", "regime", "janela",
         "score", "entry_mode", "nivel", "entry", "stop", "tp1", "risk_pts",
         "flow_fired", "flow_reason", "ai_veredito", "status", "exit_price",
         "gross_R", "net_R", "exit_reason"]

_BRT = timezone(timedelta(hours=-3))
CHECK_EVERY_SEC = 30
LIMIT_TTL_MIN   = 45          # ordem limite sombra expira em 45 min
TIME_STOP_MIN   = 60          # sem 0.5R em 60 min -> time-stop
EOD_HHMM        = (16, 55)


def _ensure_csv():
    _CSV.parent.mkdir(exist_ok=True)
    if not _CSV.exists():
        with open(_CSV, "w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=_COLS).writeheader()


def _append(row: dict):
    _ensure_csv()
    with open(_CSV, "a", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=_COLS, extrasaction="ignore").writerow(row)


def _update_row(row_id, **patch):
    _ensure_csv()
    rows = list(csv.DictReader(open(_CSV, encoding="utf-8")))
    for r in rows:
        if r["id"] == str(row_id):
            r.update({k: v for k, v in patch.items()})
    with open(_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=_COLS, extrasaction="ignore")
        w.writeheader(); w.writerows(rows)


def _fetch_df15(mt5, symbol, bars=600):
    import pandas as pd
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M15, 0, bars)
    if rates is None or len(rates) == 0:
        return None
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df = df.set_index("time")
    vol = df["real_volume"] if df["real_volume"].sum() > 0 else df["tick_volume"]
    return pd.DataFrame({"Open": df["open"], "High": df["high"], "Low": df["low"],
                         "Close": df["close"], "Volume": vol}, index=df.index)


def _ai_verdict(sig: dict, symbol: str) -> str:
    """Veredito de IA opcional (SHADOW_USE_AI=1). Nunca bloqueia o shadow."""
    if os.getenv("SHADOW_USE_AI", "0") != "1":
        return ""
    try:
        from services.signal_validator_ai import validate_signal_with_ai
        r = validate_signal_with_ai(sig, f"SHADOW:{symbol}", interval="15",
                                    score_threshold=5)
        return r.get("veredito", "")
    except Exception as exc:
        logger.debug("shadow AI: %s", exc)
        return "ERRO"


class ShadowState:
    def __init__(self):
        self.pending: dict[str, dict] = {}   # symbol -> ordem limite sombra
        self.open:    dict[str, dict] = {}   # symbol -> posição sombra
        self.next_id = int(time.time()) % 10_000_000
        self.last_candle: dict[str, object] = {}

    def nid(self):
        self.next_id += 1
        return self.next_id


def evaluate_symbol(mt5, symbol: str, state: ShadowState):
    import pandas as pd
    from services.technical_analysis_v7 import generate_signal_v7
    from services.flow_trigger import confirm_live

    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        return
    price = float(tick.last or (tick.bid + tick.ask) / 2)
    now = datetime.now(_BRT)

    # ── posição sombra aberta: gerenciar ──────────────────────────────────
    if symbol in state.open:
        p = state.open[symbol]
        buy = p["dir"] == "COMPRA"
        risk = p["risk_pts"]
        r_now = ((price - p["entry"]) / risk) if buy else ((p["entry"] - price) / risk)
        hit_stop = (price <= p["stop"]) if buy else (price >= p["stop"])
        hit_tp   = (price >= p["tp1"]) if buy else (price <= p["tp1"])
        age_min  = (time.time() - p["t0"]) / 60
        eod = (now.hour, now.minute) >= EOD_HHMM
        reason = None
        if hit_stop: reason = "SL"
        elif hit_tp: reason = "TP1"
        # v7.4: setups de alvo fixo (GAP_FADE/ORB_RETEST) não sofrem time-stop —
        # foi a configuração validada no backtest de 6,6 anos.
        elif age_min >= TIME_STOP_MIN and r_now < 0.5 and not p.get("fixed"): reason = "TIME"
        elif eod: reason = "EOD"
        if reason:
            from services.trading_costs import roundtrip_cost_pts
            stopped = reason in ("SL", "TIME", "EOD")
            cost = roundtrip_cost_pts(symbol, entry_limit=(p["entry_mode"] == "LIMITE"),
                                      stopped=stopped)
            gross = (1.0 * ((p["tp1"] - p["entry"]) / risk if buy else (p["entry"] - p["tp1"]) / risk)
                     if reason == "TP1" else r_now if reason != "SL" else -1.0)
            net = gross - cost / risk
            _update_row(p["id"], ts_close=now.isoformat(), status="CLOSED",
                        exit_price=round(price, 2), gross_R=round(gross, 3),
                        net_R=round(net, 3), exit_reason=reason)
            logger.info("SHADOW %s fechou %s: %s %.3f R líq", symbol, p["dir"], reason, net)
            del state.open[symbol]
        return

    # ── ordem limite sombra: checar fill/expiração ────────────────────────
    if symbol in state.pending:
        o = state.pending[symbol]
        buy = o["dir"] == "COMPRA"
        filled = (price <= o["entry"]) if buy else (price >= o["entry"])
        if filled:
            o["t0"] = time.time()
            state.open[symbol] = o
            _update_row(o["id"], status="OPEN")
            logger.info("SHADOW %s: limite preenchida %s @ %.2f", symbol, o["dir"], o["entry"])
            del state.pending[symbol]
        elif (time.time() - o["t_created"]) / 60 >= LIMIT_TTL_MIN:
            _update_row(o["id"], status="EXPIRED", exit_reason="TTL")
            del state.pending[symbol]
        return

    # ── novo sinal: só quando fecha candle 15m novo ───────────────────────
    df15 = _fetch_df15(mt5, symbol)
    if df15 is None or len(df15) < 100:
        return
    last_closed = df15.index[-2]     # último candle FECHADO
    if state.last_candle.get(symbol) == last_closed:
        return
    state.last_candle[symbol] = last_closed

    h1 = df15.resample("1h").agg({"Open": "first", "High": "max", "Low": "min",
                                  "Close": "last", "Volume": "sum"}).dropna()
    sig = generate_signal_v7(df15.iloc[:-1], htf_df=h1, symbol=symbol)
    if not sig or sig["acao"] not in ("COMPRA", "VENDA"):
        return

    flow = confirm_live(symbol, sig["acao"])
    ai = _ai_verdict(sig, symbol) if flow["fire"] else ""
    row_id = state.nid()
    risk = abs(float(sig["entrada"]) - float(sig["stop"]))
    row = {
        "id": row_id, "ts_open": datetime.now(_BRT).isoformat(), "ts_close": "",
        "symbol": symbol, "dir": sig["acao"], "setup": sig.get("setup"),
        "regime": sig.get("regime"), "janela": sig.get("janela"),
        "score": sig.get("score"), "entry_mode": sig.get("modo_entrada"),
        "nivel": sig.get("nivel_ref"), "entry": sig.get("entrada"),
        "stop": sig.get("stop"), "tp1": sig.get("tp1"),
        "risk_pts": round(risk, 2), "flow_fired": flow["fire"],
        "flow_reason": flow["reason"], "ai_veredito": ai,
        "status": "", "exit_price": "", "gross_R": "", "net_R": "", "exit_reason": "",
    }
    if not flow["fire"]:
        row["status"] = "NOT_TRIGGERED"
        _append(row)
        logger.info("SHADOW %s: sinal %s %s SEM gatilho (%s)", symbol, sig["acao"],
                    sig.get("setup"), flow["reason"])
        return
    if ai == "BLOQUEAR":
        row["status"] = "AI_BLOCKED"
        _append(row)
        return

    entry_mode = sig.get("modo_entrada") or "MERCADO"
    o = {"id": row_id, "dir": sig["acao"], "entry": float(sig["entrada"]),
         "stop": float(sig["stop"]), "tp1": float(sig["tp1"]),
         "risk_pts": risk, "entry_mode": entry_mode, "t_created": time.time(),
         "fixed": sig.get("exit_policy") == "FIXED"}
    if entry_mode == "LIMITE":
        row["status"] = "PENDING"
        state.pending[symbol] = o
    else:
        o["t0"] = time.time()
        o["entry"] = price
        row["status"] = "OPEN"; row["entry"] = round(price, 2)
        state.open[symbol] = o
    _append(row)
    logger.info("SHADOW %s: %s %s %s @ %s (score %s, %s)", symbol, sig["acao"],
                sig.get("setup"), entry_mode, row["entry"], sig.get("score"),
                flow["reason"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default=None,
                    help="ex. WINQ26,WDOU26 (default: .env WIN_MT5_SYMBOL/WDO_MT5_SYMBOL)")
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()

    try:
        from dotenv import load_dotenv; load_dotenv()
    except Exception:
        pass
    import MetaTrader5 as mt5
    if mt5.terminal_info() is None and not mt5.initialize():
        print("MT5 não inicializou:", mt5.last_error()); return

    symbols = (args.symbols.split(",") if args.symbols else
               [os.getenv("WIN_MT5_SYMBOL", "WINQ26"), os.getenv("WDO_MT5_SYMBOL", "WDOU26")])
    symbols = [s.strip() for s in symbols if s.strip()]
    for s in symbols:
        mt5.symbol_select(s, True)

    state = ShadowState()
    logger.info("Shadow v7 ativo (SEM ordens): %s", symbols)
    while True:
        for s in symbols:
            try:
                evaluate_symbol(mt5, s, state)
            except Exception as exc:
                logger.warning("shadow %s: %s", s, exc)
        if args.once:
            break
        time.sleep(CHECK_EVERY_SEC)


if __name__ == "__main__":
    main()
