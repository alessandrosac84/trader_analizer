"""
win_eod_runtime.py — WIN_EOD_REV ao vivo (paper trading em demo).

Setup APROVADO na bateria completa (22/07/2026):
  backtest 5 anos M15: +0,285 R/trade · PF 1,57 · consistência 72%
  walk-forward 6/6 janelas ✅ · Monte Carlo P(≤0)=0% ✅ · sensibilidade 4/5 ✅
  Lógica: fim de pregão do WIN (hora no quintil superior ≈ 16h+) + volume seco
  (percentil rolante baixo) + preço esticado abaixo da EMA50 → COMPRA (reversão
  para o fechamento). SL 1×ATR · TP 2×ATR · ZERAGEM às 18:12 (nunca overnight).

MÓDULO NOVO E ISOLADO: MAGIC próprio, thread própria na instância B3.
NÃO altera Monitor MT5 (v6) nem Monitor V7 — é o setup de TARDE que faltava
(o v7.4.1 opera só a abertura 09:15–10:45).

Controles: .env WIN_EOD_ENABLED=1|0 (default 1) · WIN_EOD_VOLUME (default 1)
Telegram: entrada/saída via telegram_notifier.send_async. Log: logs/win_eod_trades.csv
"""
import csv
import logging
import os
import threading
import time
from datetime import datetime

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

MAGIC = 20260722
COMMENT = "WinEodRev"
CHECK_SEC = 30
ENTRY_HHMM_MIN = (15, 45)      # começa a avaliar 15:45 (quintil sup. de hora ≈16h+ decide)
EOD_HHMM = (18, 12)            # zeragem — nunca carrega overnight
BARS = 3000                    # janela p/ features e limiares (≈5 meses M15)
Q = 0.80
MAX_TRADES_DAY = 3

_state = {"enabled": os.getenv("WIN_EOD_ENABLED", "1").strip() != "0",
          "trades_today": 0, "day": None, "last_bar": None}
_started = False


def _mt5():
    import MetaTrader5 as mt5
    if mt5.terminal_info() is None:
        kw = {}
        path = os.getenv("MT5_PATH", "")
        if path and os.path.exists(path):
            kw["path"] = path
        login = int(os.getenv("MT5_LOGIN", "0") or 0)
        pw, srv = os.getenv("MT5_PASSWORD", ""), os.getenv("MT5_SERVER", "")
        if login and pw and srv and srv.lower() not in ("metaquotes-demo", "metaquotes-demo2"):
            kw.update(login=login, password=pw, server=srv)
        mt5.initialize(**kw)
    return mt5


def _symbol():
    return os.getenv("WIN_MT5_SYMBOL", "WINQ26").strip()


def _notify(txt):
    try:
        from services.telegram_notifier import send_async
        send_async(txt)
    except Exception:
        pass


def _log_trade(row):
    try:
        os.makedirs("logs", exist_ok=True)
        path = os.path.join("logs", "win_eod_trades.csv")
        new = not os.path.exists(path)
        with open(path, "a", newline="", encoding="utf-8") as f:
            wcsv = csv.DictWriter(f, fieldnames=list(row.keys()))
            if new:
                wcsv.writeheader()
            wcsv.writerow(row)
    except Exception as exc:
        logger.warning("win_eod log: %s", exc)


def _signal(df):
    """df: OHLCV M15 (barras fechadas). Replica o WIN_EOD_REV do harness."""
    c, h, l, v = df.Close, df.High, df.Low, df.Volume
    tr = pd.concat([(h-l), (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/14, adjust=False).mean()
    vol_pctl = v.rolling(500, min_periods=100).rank(pct=True)
    ema50 = c.ewm(span=50, adjust=False).mean()
    ema50_dist = (c - ema50) / (atr + 1e-12)
    hours = pd.Series(df.index.hour, index=df.index)
    thr_vol = vol_pctl.quantile(1-Q)               # quintil INFERIOR (volume seco)
    thr_dist = ema50_dist.quantile(1-Q)            # quintil INFERIOR (esticado p/ baixo)
    thr_hour = hours.quantile(Q)                   # quintil SUPERIOR (fim de pregão)
    r_vol, r_dist = float(vol_pctl.iloc[-1]), float(ema50_dist.iloc[-1])
    r_hour = int(df.index[-1].hour)
    a, px = float(atr.iloc[-1]), float(c.iloc[-1])
    if not np.isfinite(a) or a <= 0:
        return None
    if r_vol <= thr_vol and r_dist <= thr_dist and r_hour > thr_hour:
        return {"entry": px, "sl": px - a, "tp": px + 2*a, "atr": a,
                "ctx": f"vol_pctl={r_vol:.2f} ema50_dist={r_dist:.2f} hora={r_hour}"}
    return None


def _positions(mt5, sym):
    raw = mt5.positions_get(symbol=sym) or []
    return [p for p in raw if p.magic == MAGIC]


def _order(mt5, sym, action, volume, price, sl, tp, closing_ticket=None):
    # snap ao tick do símbolo (WIN = 5 pts) + distância mínima de stops
    info = mt5.symbol_info(sym)
    ts_ = float((info.trade_tick_size if info else 0) or (info.point if info else 0) or 5.0)
    snap = lambda x: round(round(float(x)/ts_)*ts_, 8)
    min_d = max(float(getattr(info, "trade_stops_level", 0) or 0)*float(info.point or ts_),
                2*ts_) if info else 2*ts_
    price = snap(price)
    buy = action == mt5.ORDER_TYPE_BUY
    if sl:
        sl = snap(sl)
        if buy and sl >= price - min_d:
            sl = snap(price - min_d - ts_)
        if (not buy) and sl <= price + min_d:
            sl = snap(price + min_d + ts_)
    if tp:
        tp = snap(tp)
        if buy and tp <= price + min_d:
            tp = snap(price + min_d + ts_)
        if (not buy) and tp >= price - min_d:
            tp = snap(price - min_d - ts_)
    req = {"action": mt5.TRADE_ACTION_DEAL, "symbol": sym, "volume": float(volume),
           "type": action, "price": float(price), "deviation": 30, "magic": MAGIC,
           "comment": COMMENT, "type_time": mt5.ORDER_TIME_GTC,
           "type_filling": mt5.ORDER_FILLING_RETURN}
    if sl:
        req["sl"] = float(sl)
    if tp:
        req["tp"] = float(tp)
    if closing_ticket:
        req["position"] = int(closing_ticket)
    for _ in range(3):
        r = mt5.order_send(req)
        if r is not None and r.retcode == mt5.TRADE_RETCODE_DONE:
            return r, None
        time.sleep(1)
    return None, f"retcode={getattr(r, 'retcode', '?')}" if r else "sem resposta"


_DEALS_SEEN = set()


def _reconcile(mt5, sym):
    """Registra no CSV as SAÍDAS executadas pela corretora (SL/TP) — dedup por ticket."""
    try:
        from datetime import timedelta
        deals = mt5.history_deals_get(datetime.now() - timedelta(days=3), datetime.now()) or []
        for d in deals:
            if d.magic != MAGIC or d.entry != 1 or d.ticket in _DEALS_SEEN:
                continue
            _DEALS_SEEN.add(d.ticket)
            reason = "TP" if "tp" in (d.comment or "").lower() else \
                     ("SL" if "sl" in (d.comment or "").lower() else "SAIDA")
            _log_trade({"ts": str(datetime.fromtimestamp(d.time)), "evento": reason,
                        "preco": d.price, "pnl_brl": d.profit})
            _notify(f"{'🎯' if reason == 'TP' else '🛑' if reason == 'SL' else '✋'} "
                    f"<b>WIN EOD-REV — {reason}</b>\nSaída {d.price:.0f} · "
                    f"P&L R$ {d.profit:+.2f}")
    except Exception as exc:
        logger.debug("win_eod reconcile: %s", exc)


def _loop():
    sym = _symbol()
    logger.info("WinEodRuntime iniciado (%s, magic %s) — janela %02d:%02d–%02d:%02d",
                sym, MAGIC, *ENTRY_HHMM_MIN, *EOD_HHMM)
    while True:
        try:
            now = datetime.now()
            today = now.date()
            if _state["day"] != today:
                _state["day"] = today
                _state["trades_today"] = 0
            hhmm = (now.hour, now.minute)
            in_window = ENTRY_HHMM_MIN <= hhmm < EOD_HHMM
            past_eod = hhmm >= EOD_HHMM

            try:
                from services.engine_heartbeat import beat
                beat("win_eod_rev", {
                    "motivos": {_symbol(): (
                        "desligado (WIN_EOD_ENABLED=0)" if not _state["enabled"]
                        else f"aguardando janela (opera {ENTRY_HHMM_MIN[0]:02d}:{ENTRY_HHMM_MIN[1]:02d}–{EOD_HHMM[0]:02d}:{EOD_HHMM[1]:02d})"
                        if not (in_window or past_eod)
                        else "zeragem/pós-EOD" if past_eod
                        else f"na janela — avaliando (trades hoje: {_state['trades_today']}/{MAX_TRADES_DAY})")}})
            except Exception:
                pass
            if not (_state["enabled"] and (in_window or past_eod)):
                time.sleep(CHECK_SEC); continue

            mt5 = _mt5()
            if mt5.terminal_info() is None:
                time.sleep(CHECK_SEC); continue
            _reconcile(mt5, sym)
            poss = _positions(mt5, sym)

            # ── zeragem EOD (nunca overnight) ──
            if past_eod and hhmm < (18, 25):
                for p in poss:
                    tick = mt5.symbol_info_tick(sym)
                    if not tick:
                        continue
                    px = tick.bid if p.type == 0 else tick.ask
                    otype = mt5.ORDER_TYPE_SELL if p.type == 0 else mt5.ORDER_TYPE_BUY
                    r, err = _order(mt5, sym, otype, p.volume, px, None, None, p.ticket)
                    if not err:
                        _notify(f"🌆 <b>WIN EOD-REV — ZERAGEM 18:12</b>\n"
                                f"Saída {px:.0f} · P&L R$ {p.profit:+.2f}")
                        _log_trade({"ts": str(now), "evento": "EOD_CLOSE",
                                    "preco": px, "pnl_brl": p.profit})
                time.sleep(CHECK_SEC); continue

            # ── entrada (só na janela; 1 avaliação por barra fechada) ──
            if poss or _state["trades_today"] >= MAX_TRADES_DAY:
                time.sleep(CHECK_SEC); continue
            rates = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M15, 0, BARS)
            if rates is None or len(rates) < 600:
                time.sleep(CHECK_SEC); continue
            df = pd.DataFrame(rates)
            df["dt"] = pd.to_datetime(df["time"], unit="s")
            df = df.set_index("dt").rename(columns={
                "open": "Open", "high": "High", "low": "Low", "close": "Close"})
            df["Volume"] = df["real_volume"].where(df["real_volume"] > 0, df["tick_volume"])
            df = df.iloc[:-1]                        # só barras fechadas
            last_bar = df.index[-1]
            if _state["last_bar"] == last_bar:
                time.sleep(CHECK_SEC); continue
            _state["last_bar"] = last_bar
            sig = _signal(df[["Open", "High", "Low", "Close", "Volume"]])
            if not sig:
                time.sleep(CHECK_SEC); continue
            tick = mt5.symbol_info_tick(sym)
            if not tick:
                time.sleep(CHECK_SEC); continue
            vol = float(os.getenv("WIN_EOD_VOLUME", "1") or 1)
            r, err = _order(mt5, sym, mt5.ORDER_TYPE_BUY, vol, tick.ask,
                            sig["sl"], sig["tp"])
            if err:
                logger.warning("WinEod ordem falhou: %s", err)
                _notify(f"⚠️ WIN EOD-REV: ordem falhou ({err})")
            else:
                _state["trades_today"] += 1
                _notify(f"🌇 <b>WIN EOD-REV — COMPRA</b> (edge discovery ✅)\n"
                        f"━━━━━━━━━━━━━━━━━━\n"
                        f"💰 Entrada ~{tick.ask:.0f} · 🛑 SL {sig['sl']:.0f} · 🎯 TP {sig['tp']:.0f}\n"
                        f"📊 {sig['ctx']}\n"
                        f"⏱ Zeragem automática 18:12")
                _log_trade({"ts": str(now), "evento": "ENTRADA", "preco": tick.ask,
                            "sl": sig["sl"], "tp": sig["tp"], "atr": sig["atr"],
                            "ctx": sig["ctx"], "vol": vol})
        except Exception as exc:
            logger.warning("WinEodRuntime loop: %s", exc)
        time.sleep(CHECK_SEC)


def start_win_eod_runtime() -> None:
    """Chamar no boot da instância B3. Idempotente."""
    global _started
    if _started:
        return
    if os.getenv("MT5_PROFILE", "b3").strip().lower() == "crypto":
        return
    _started = True
    t = threading.Thread(target=_loop, daemon=True, name="WinEodRuntime")
    t.start()
