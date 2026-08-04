"""
v7_trader.py — EXECUTOR do motor v7.4.1 em conta DEMO (WIN).

Executa de verdade (ordens na conta demo do MT5) o sistema validado no
backtest de 6,6 anos (+0.182 R/trade líq., PF 1.44, consistência 65%):

  GAP_FADE  — 09:15: gap moderado contra → alvo no fechamento do gap
              (1 contrato, SL e TP fixos direto na ordem — MT5 gerencia)
  ORB       — 10:00-10:45: rompimento da 1ª hora com filtros de regime
              (2 contratos: fecha 1 em +1R e move SL p/ entrada; o restante
              segue com trailing de 1x ATR até 2.5R — a política validada)

INDEPENDENTE do Monitor MT5: magic number próprio (20260714), log próprio
(logs/v7_trader_trades.csv). O app pode rodar junto — só deixe o auto-trade
antigo (v6) DESLIGADO para não misturar resultados.

Proteções: 1 posição por vez, stop diário de -3R, zeragem 16:55,
não opera fora das janelas validadas.

Uso (MT5 aberto e logado na conta demo):
    python v7_trader.py                # símbolo do .env (WIN_MT5_SYMBOL)
    python v7_trader.py --symbol WINQ26
    set V7_USE_FLOW=1                  # opcional: exige confirmação de fluxo
                                       # (tape) antes de entrar — o backtest
                                       # NÃO usou; default desligado p/ fidelidade
"""
import argparse
import csv
import logging
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("v7_trader")

MAGIC       = 20260714
COMMENT     = "V7-DEMO"
_BRT        = timezone(timedelta(hours=-3))
_CSV        = Path(__file__).parent / "logs" / "v7_trader_trades.csv"
_COLS       = ["id", "ts_open", "ts_close", "symbol", "setup", "dir", "volume",
               "entry", "stop", "tp", "risk_pts", "partial_done", "exit_reason",
               "profit_brl", "r_multiple", "flow_used", "flow_reason"]

CHECK_SEC       = 15
VOLUME_GAP      = 1.0
VOLUME_ORB      = 2.0      # 2 contratos p/ permitir a parcial de 50% em +1R
PARTIAL_AT_R    = 1.0
TP2_R           = 2.5
TRAIL_ATR       = 1.0
TIME_STOP_BARS  = 4        # candles 15m sem +0.5R -> sai (só ORB)
DAILY_STOP_R    = -3.0
EOD_HHMM        = (16, 55)


# ── CSV ────────────────────────────────────────────────────────────────────

def _ensure_csv():
    _CSV.parent.mkdir(exist_ok=True)
    if not _CSV.exists():
        with open(_CSV, "w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=_COLS).writeheader()


def _append(row):
    _ensure_csv()
    with open(_CSV, "a", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=_COLS, extrasaction="ignore").writerow(row)


def _update(row_id, **patch):
    _ensure_csv()
    rows = list(csv.DictReader(open(_CSV, encoding="utf-8")))
    for r in rows:
        if r["id"] == str(row_id):
            r.update({k: str(v) for k, v in patch.items()})
    with open(_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=_COLS, extrasaction="ignore")
        w.writeheader(); w.writerows(rows)


def _today_realized_r():
    """Soma o R realizado HOJE (para o stop diário de -3R)."""
    if not _CSV.exists():
        return 0.0
    today = datetime.now(_BRT).strftime("%Y-%m-%d")
    tot = 0.0
    for r in csv.DictReader(open(_CSV, encoding="utf-8")):
        if (r.get("ts_close") or "").startswith(today):
            try:
                tot += float(r.get("r_multiple") or 0)
            except Exception:
                pass
    return tot


# ── MT5 helpers ────────────────────────────────────────────────────────────

def mt5_connect():
    import MetaTrader5 as mt5
    if mt5.terminal_info() is None and not mt5.initialize():
        print("MT5 não inicializou:", mt5.last_error()); sys.exit(1)
    acc = mt5.account_info()
    if acc:
        mode = "DEMO" if acc.trade_mode == 0 else "REAL/OUTRO"
        print(f"Conta: {acc.login} ({mode}) | saldo {acc.balance:.2f}")
        if acc.trade_mode != 0:
            print("⚠️  ATENÇÃO: a conta NÃO parece ser demo. Abortando por segurança.")
            sys.exit(1)
    return mt5


def get_df15(mt5, symbol, bars=700):
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


def our_position(mt5, symbol):
    for p in (mt5.positions_get(symbol=symbol) or []):
        if p.magic == MAGIC:
            return p
    return None


def send_order(mt5, symbol, direction, volume, sl=None, tp=None):
    tick = mt5.symbol_info_tick(symbol)
    info = mt5.symbol_info(symbol)
    if tick is None or info is None:
        return None, "sem tick/info"
    buy = direction == "COMPRA"
    price = tick.ask if buy else tick.bid
    filling = mt5.ORDER_FILLING_IOC
    if info.filling_mode & mt5.ORDER_FILLING_FOK:
        filling = mt5.ORDER_FILLING_FOK
    req = {"action": mt5.TRADE_ACTION_DEAL, "symbol": symbol,
           "volume": float(volume),
           "type": mt5.ORDER_TYPE_BUY if buy else mt5.ORDER_TYPE_SELL,
           "price": price, "deviation": 30, "magic": MAGIC, "comment": COMMENT,
           "type_time": mt5.ORDER_TIME_GTC, "type_filling": filling}
    if sl: req["sl"] = float(sl)
    if tp: req["tp"] = float(tp)
    r = mt5.order_send(req)
    if r and r.retcode == mt5.TRADE_RETCODE_DONE:
        return r, None
    return None, f"retcode {r.retcode if r else '?'}: {r.comment if r else ''}"


def close_position(mt5, pos, volume=None):
    tick = mt5.symbol_info_tick(pos.symbol)
    if tick is None:
        return False
    buy_to_close = pos.type == 1     # short fecha comprando
    req = {"action": mt5.TRADE_ACTION_DEAL, "symbol": pos.symbol,
           "volume": float(volume or pos.volume), "position": pos.ticket,
           "type": mt5.ORDER_TYPE_BUY if buy_to_close else mt5.ORDER_TYPE_SELL,
           "price": tick.ask if buy_to_close else tick.bid,
           "deviation": 30, "magic": MAGIC, "comment": COMMENT + "-close",
           "type_time": mt5.ORDER_TIME_GTC, "type_filling": mt5.ORDER_FILLING_IOC}
    r = mt5.order_send(req)
    return bool(r and r.retcode == mt5.TRADE_RETCODE_DONE)


def modify_sl(mt5, pos, new_sl, tp=None):
    req = {"action": mt5.TRADE_ACTION_SLTP, "symbol": pos.symbol,
           "position": pos.ticket, "sl": float(new_sl),
           "tp": float(tp if tp is not None else (pos.tp or 0))}
    r = mt5.order_send(req)
    return bool(r and r.retcode == mt5.TRADE_RETCODE_DONE)


def realized_profit(mt5, ticket, wait=6):
    """P&L realizado de uma posição fechada (busca nos deals, com retry)."""
    from datetime import datetime as dt
    for _ in range(wait):
        deals = mt5.history_deals_get(dt.now() - timedelta(days=2), dt.now(),
                                      position=ticket) or []
        outs = [d.profit for d in deals if d.entry == 1]   # DEAL_ENTRY_OUT
        if outs:
            return sum(outs)
        time.sleep(0.5)
    return None


# ── Loop principal ─────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default=None)
    args = ap.parse_args()

    from services.technical_analysis_v7 import generate_signal_v7
    use_flow = os.getenv("V7_USE_FLOW", "0") == "1"

    mt5 = mt5_connect()
    symbol = args.symbol or os.getenv("WIN_MT5_SYMBOL", "WINQ26")
    mt5.symbol_select(symbol, True)
    print(f"\n=== V7 TRADER (demo) — {symbol} | magic {MAGIC} | "
          f"flow={'ON' if use_flow else 'OFF'} ===")
    print("Setups: GAP_FADE 09:15 (fixo) + ORB 10:00-10:45 (parcial 1R + trailing)")
    print(f"Proteções: 1 posição, stop diário {DAILY_STOP_R}R, zeragem 16:55\n")

    state = {}            # estado da posição gerida (setup ORB)
    last_candle = None
    next_id = int(time.time()) % 10_000_000

    while True:
        try:
            now = datetime.now(_BRT)
            eod = (now.hour, now.minute) >= EOD_HHMM
            pos = our_position(mt5, symbol)

            # ── posição aberta: gerenciar ─────────────────────────────────
            if pos:
                st = state.get(pos.ticket)
                tick = mt5.symbol_info_tick(symbol)
                price = tick.last or (tick.bid + tick.ask) / 2 if tick else None

                if eod:
                    close_position(mt5, pos)
                    logger.info("EOD: posição fechada")
                elif st and st["setup"] == "ORB" and price:
                    buy = pos.type == 0
                    risk = st["risk"]
                    r_now = ((price - st["entry"]) / risk) if buy else ((st["entry"] - price) / risk)
                    # parcial de 50% em +1R + breakeven
                    if not st["partial_done"] and r_now >= PARTIAL_AT_R and pos.volume >= 2:
                        if close_position(mt5, pos, volume=pos.volume / 2):
                            st["partial_done"] = True
                            p2 = our_position(mt5, symbol)
                            if p2: modify_sl(mt5, p2, st["entry"])
                            _update(st["id"], partial_done=1)
                            logger.info("PARCIAL +1R executada; SL -> breakeven")
                    elif st["partial_done"]:
                        # TP2 em 2.5R
                        if r_now >= TP2_R:
                            close_position(mt5, pos)
                            logger.info("TP2 %.1fR atingido — fechado", TP2_R)
                        else:
                            # trailing 1x ATR sobre o fechamento do último candle
                            df = get_df15(mt5, symbol, 30)
                            if df is not None and len(df) > 15:
                                atr = (df["High"] - df["Low"]).tail(14).mean()
                                c = float(df["Close"].iloc[-2])
                                trail = c - TRAIL_ATR * atr if buy else c + TRAIL_ATR * atr
                                cur_sl = pos.sl or 0
                                better = (trail > cur_sl) if buy else (trail < cur_sl or cur_sl == 0)
                                if better:
                                    modify_sl(mt5, pos, trail)
                    else:
                        # time-stop: 4 candles sem +0.5R
                        bars_open = int((time.time() - st["t0"]) // (15 * 60))
                        if bars_open >= TIME_STOP_BARS and r_now < 0.5:
                            close_position(mt5, pos)
                            logger.info("TIME-STOP: %d candles sem 0.5R — fechado", bars_open)
                time.sleep(CHECK_SEC)
                continue

            # ── sem posição: registrar fechamento pendente ────────────────
            for tk, st in list(state.items()):
                profit = realized_profit(mt5, tk)
                if profit is not None:
                    rmult = round(profit / st["risk_brl"], 3) if st["risk_brl"] else ""
                    _update(st["id"], ts_close=now.isoformat(),
                            profit_brl=round(profit, 2), r_multiple=rmult,
                            exit_reason="MT5")
                    logger.info("Trade %s fechado: R$ %.2f (%s R)", st["id"], profit, rmult)
                del state[tk]

            # ── novo sinal? ───────────────────────────────────────────────
            if eod or now.weekday() >= 5:
                time.sleep(60); continue
            if _today_realized_r() <= DAILY_STOP_R:
                time.sleep(300); continue

            df15 = get_df15(mt5, symbol)
            if df15 is None or len(df15) < 120:
                time.sleep(CHECK_SEC); continue
            lc = df15.index[-2]
            if lc == last_candle:
                time.sleep(CHECK_SEC); continue
            last_candle = lc

            h1 = df15.resample("1h").agg({"Open": "first", "High": "max",
                                          "Low": "min", "Close": "last",
                                          "Volume": "sum"}).dropna()
            sig = generate_signal_v7(df15.iloc[:-1], htf_df=h1, symbol=symbol)
            if not sig or sig["acao"] not in ("COMPRA", "VENDA"):
                time.sleep(CHECK_SEC); continue
            if sig.get("setup") not in ("GAP_FADE", "ORB"):
                time.sleep(CHECK_SEC); continue

            flow_reason = ""
            if use_flow:
                from services.flow_trigger import confirm_live
                fl = confirm_live(symbol, sig["acao"])
                flow_reason = fl.get("reason", "")
                if not fl["fire"]:
                    logger.info("Sinal %s %s SEM fluxo: %s", sig["setup"],
                                sig["acao"], flow_reason)
                    time.sleep(CHECK_SEC); continue

            setup = sig["setup"]
            entry_ref = float(sig["entrada"])
            stop = float(sig["stop"])
            risk_pts = abs(entry_ref - stop)
            vol = VOLUME_GAP if setup == "GAP_FADE" else VOLUME_ORB
            tp = float(sig["tp1"]) if setup == "GAP_FADE" else None

            r, err = send_order(mt5, symbol, sig["acao"], vol, sl=stop, tp=tp)
            if err:
                logger.warning("Ordem falhou: %s", err)
                time.sleep(CHECK_SEC); continue

            next_id += 1
            info = mt5.symbol_info(symbol)
            pt_val = 0.20 if "WIN" in symbol.upper() else 10.0
            risk_brl = risk_pts * pt_val * vol
            pos2 = our_position(mt5, symbol)
            if pos2:
                state[pos2.ticket] = {"id": next_id, "setup": setup,
                                      "entry": float(r.price), "risk": risk_pts,
                                      "risk_brl": risk_brl, "partial_done": False,
                                      "t0": time.time()}
            _append({"id": next_id, "ts_open": now.isoformat(), "ts_close": "",
                     "symbol": symbol, "setup": setup, "dir": sig["acao"],
                     "volume": vol, "entry": round(float(r.price), 1),
                     "stop": round(stop, 1), "tp": round(tp, 1) if tp else "",
                     "risk_pts": round(risk_pts, 1), "partial_done": 0,
                     "exit_reason": "", "profit_brl": "", "r_multiple": "",
                     "flow_used": int(use_flow), "flow_reason": flow_reason})
            logger.info("🟢 EXECUTADO %s %s %s @ %.1f | SL %.1f%s | risco %.1f pts (R$ %.2f)",
                        setup, sig["acao"], symbol, r.price, stop,
                        f" | TP {tp:.1f}" if tp else "", risk_pts, risk_brl)

        except KeyboardInterrupt:
            print("\nEncerrado pelo usuário."); break
        except Exception as exc:
            logger.warning("loop: %s", exc)
        time.sleep(CHECK_SEC)


if __name__ == "__main__":
    main()
