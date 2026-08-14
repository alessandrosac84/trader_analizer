"""
v7_engine_runtime.py — Motor v7.4.1 como SERVIÇO do app (Monitor V7).

O mesmo sistema validado no backtest de 6,6 anos (+0.182 R/trade, PF 1.44):
  GAP_FADE 09:15 (1 contrato, SL/TP fixos) + ORB 10:00-10:45
  (1 contrato: BE em +1R, trailing 1xATR até 2.5R, time-stop — sem parcial)

Roda numa thread daemon dentro do Flask e alimenta a página /v7:
  - SEMPRE avalia o mercado a cada candle 15m fechado (mostra regime/sinal na tela)
  - SÓ EXECUTA ordens (conta DEMO) quando o auto-trade da tela está ON
  - Gerencia posições existentes (magic 20260714) mesmo com auto OFF
  - Stop diário de -3R, zeragem 16:55, 1 posição por vez

Independente do Monitor MT5 (v6) — nada do fluxo antigo é alterado.
Log de trades: logs/v7_trader_trades.csv (mesmo do v7_trader.py CLI).
"""
import csv
import logging
import os
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

MAGIC      = 20260714
COMMENT    = "V7-DEMO"
_BRT       = timezone(timedelta(hours=-3))
_CSV       = Path(__file__).resolve().parent.parent / "logs" / "v7_trader_trades.csv"
_COLS      = ["id", "ts_open", "ts_close", "symbol", "setup", "dir", "volume",
              "entry", "stop", "tp", "risk_pts", "partial_done", "exit_reason",
              "profit_brl", "r_multiple", "flow_used", "flow_reason",
              # v7.5: parecer da IA (modo consultivo — logado, não bloqueia)
              "ai_veredito", "ai_conf", "ai_motivo"]

CHECK_SEC      = 10
VOLUME_GAP     = 1.0    # base — multiplicado por _vol_mult (painel)
VOLUME_ORB     = 1.0    # 1 contrato (antes 2.0 p/ parcial 50%; agora BE em +1R)
PARTIAL_AT_R   = 1.0
TP2_R          = 2.5
TRAIL_ATR      = 1.0
TIME_STOP_BARS = 4
DAILY_STOP_R   = -3.0
EOD_HHMM       = (16, 55)


def _ensure_csv():
    _CSV.parent.mkdir(exist_ok=True)
    # v7.5: se o CSV existente tem cabeçalho antigo (sem colunas de IA),
    # rotaciona para *_v74.csv e começa um novo — evita linhas desalinhadas.
    if _CSV.exists():
        try:
            with open(_CSV, "r", encoding="utf-8") as f:
                header = (f.readline() or "").strip().split(",")
            if header and header != _COLS:
                legacy = str(_CSV).replace(".csv",
                          f"_v74_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv")
                os.rename(_CSV, legacy)
                logger.warning("v7 CSV: cabeçalho antigo — rotacionado p/ %s", legacy)
        except Exception as exc:
            logger.warning("v7 CSV header check: %s", exc)
    if not _CSV.exists():
        with open(_CSV, "w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=_COLS).writeheader()


def _append_row(row):
    _ensure_csv()
    with open(_CSV, "a", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=_COLS, extrasaction="ignore").writerow(row)


def _update_row(row_id, **patch):
    _ensure_csv()
    rows = list(csv.DictReader(open(_CSV, encoding="utf-8")))
    for r in rows:
        if r["id"] == str(row_id):
            r.update({k: str(v) for k, v in patch.items()})
    with open(_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=_COLS, extrasaction="ignore")
        w.writeheader(); w.writerows(rows)


def read_trades(limit=50):
    if not _CSV.exists():
        return []
    rows = list(csv.DictReader(open(_CSV, encoding="utf-8")))
    return rows[-limit:][::-1]


def ai_history(limit=40):
    """Histórico dos pareceres da IA cruzado com o resultado real do trade —
    é o que valida se a IA acerta (BLOQUEAR num trade que perdeu = acertou)."""
    if not _CSV.exists():
        return []
    out = []
    for r in csv.DictReader(open(_CSV, encoding="utf-8")):
        ver = r.get("ai_veredito")
        if not ver or ver in ("N/D", "OFF", ""):
            continue
        try:
            rr = float(r.get("r_multiple") or 0)
        except Exception:
            rr = None
        out.append({
            "ts": (r.get("ts_open") or "")[5:16].replace("T", " "),
            "setup": r.get("setup"), "dir": r.get("dir"),
            "veredito": ver, "conf": r.get("ai_conf"),
            "motivo": r.get("ai_motivo"),
            "resultado_r": rr,
            "fechado": bool(r.get("ts_close")),
        })
    return out[-limit:][::-1]


def ai_scorecard():
    """Placar da IA: dos trades que a IA marcou 'AGUARDAR'/'BLOQUEAR', quantos
    de fato deram prejuízo? (a IA 'acerta' quando manda evitar um perdedor)."""
    hist = [h for h in ai_history(500) if h["fechado"] and h["resultado_r"] is not None]
    def bucket(vers):
        sub = [h for h in hist if h["veredito"] in vers]
        if not sub:
            return {"n": 0}
        neg = sum(1 for h in sub if h["resultado_r"] <= 0)
        avg = round(sum(h["resultado_r"] for h in sub) / len(sub), 3)
        return {"n": len(sub), "pct_perdedores": round(neg / len(sub) * 100),
                "media_r": avg}
    return {"executar": bucket(["EXECUTAR"]),
            "cautela": bucket(["AGUARDAR", "BLOQUEAR"])}


def today_realized_r():
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
    return round(tot, 2)


def today_stats():
    if not _CSV.exists():
        return {"trades": 0, "pnl_brl": 0.0, "pnl_r": 0.0, "wins": 0, "losses": 0}
    today = datetime.now(_BRT).strftime("%Y-%m-%d")
    n = wins = losses = 0
    pnl = pr = 0.0
    for r in csv.DictReader(open(_CSV, encoding="utf-8")):
        if (r.get("ts_close") or "").startswith(today):
            n += 1
            try:
                p = float(r.get("profit_brl") or 0); pnl += p
                pr += float(r.get("r_multiple") or 0)
                if p > 0: wins += 1
                elif p < 0: losses += 1
            except Exception:
                pass
    return {"trades": n, "pnl_brl": round(pnl, 2), "pnl_r": round(pr, 2),
            "wins": wins, "losses": losses}


def period_stats(rng="day"):
    """Estatísticas agregadas por período: 'day' | 'week' | 'month' | 'all'.
    Usa trades FECHADOS (ts_close). Retorna resumo + série de equity em R."""
    empty = {"range": rng, "trades": 0, "wins": 0, "losses": 0, "be": 0,
             "win_rate": 0.0, "pnl_brl": 0.0, "pnl_r": 0.0, "pf": 0.0,
             "max_dd_r": 0.0, "by_setup": {}, "equity": [], "days": []}
    if not _CSV.exists():
        return empty
    now = datetime.now(_BRT)
    if rng == "day":
        since = now.strftime("%Y-%m-%d")
    elif rng == "week":
        since = (now - timedelta(days=now.weekday())).strftime("%Y-%m-%d")
    elif rng == "month":
        since = now.strftime("%Y-%m-01")
    else:
        since = "0000-00-00"
    rows = []
    for r in csv.DictReader(open(_CSV, encoding="utf-8")):
        cl = r.get("ts_close") or ""
        if cl and cl[:10] >= since:
            rows.append(r)
    if not rows:
        return empty
    n = wins = losses = be = 0
    pnl = pr = 0.0
    gw = gl = 0.0
    by_setup = {}
    equity = []; cum = 0.0; peak = 0.0; dd = 0.0
    by_day = {}
    for r in sorted(rows, key=lambda x: x.get("ts_close", "")):
        try:
            p = float(r.get("profit_brl") or 0)
            rr = float(r.get("r_multiple") or 0)
        except Exception:
            continue
        n += 1; pnl += p; pr += rr
        if p > 0: wins += 1; gw += rr
        elif p < 0: losses += 1; gl += -rr
        else: be += 1
        s = r.get("setup", "?")
        d = by_setup.setdefault(s, {"n": 0, "pnl_r": 0.0, "wins": 0})
        d["n"] += 1; d["pnl_r"] += rr; d["wins"] += 1 if p > 0 else 0
        cum += rr; peak = max(peak, cum); dd = min(dd, cum - peak)
        equity.append(round(cum, 2))
        day = (r.get("ts_close") or "")[:10]
        dd_ = by_day.setdefault(day, {"pnl_brl": 0.0, "pnl_r": 0.0, "n": 0})
        dd_["pnl_brl"] += p; dd_["pnl_r"] += rr; dd_["n"] += 1
    for d in by_setup.values():
        d["pnl_r"] = round(d["pnl_r"], 2)
    days = [{"day": k, "pnl_brl": round(v["pnl_brl"], 2),
             "pnl_r": round(v["pnl_r"], 2), "n": v["n"]}
            for k, v in sorted(by_day.items())]
    dec = wins + losses
    return {"range": rng, "trades": n, "wins": wins, "losses": losses, "be": be,
            "win_rate": round(wins / dec * 100, 1) if dec else 0.0,
            "pnl_brl": round(pnl, 2), "pnl_r": round(pr, 2),
            "pf": round(gw / gl, 2) if gl > 0 else (99.0 if gw > 0 else 0.0),
            "max_dd_r": round(dd, 2), "by_setup": by_setup,
            "equity": equity, "days": days}


class V7Runtime:
    def __init__(self):
        self._lock = threading.Lock()
        self._thread = None
        # Auto-trade ON no boot (V7_AUTO_ENABLED=0 para subir desligado)
        self.enabled = os.getenv("V7_AUTO_ENABLED", "1").strip() != "0"
        self.symbol = os.getenv("WIN_MT5_SYMBOL", "WINV26")
        self.status = "parado"
        self.account = None
        self.is_demo = None
        self.last_eval = None         # dict do último generate_signal_v7
        self.last_eval_ts = None
        self.last_exec_msg = ""
        self.position = None          # snapshot da posição gerida
        self._state = {}              # ticket -> estado ORB
        self._last_candle = None
        self._next_id = int(time.time()) % 10_000_000
        self._candles_cache = {}      # tf -> (lista, ts)
        # ── config do painel (v7.5) ────────────────────────────────────────
        self.vol_mult = 1.0           # multiplicador de contratos (1 = base: gap 1 / orb 1)
        self.chart_tf = 15            # timeframe do GRÁFICO (não muda o motor: sempre 15m)
        self.ai_mode = "advisory"     # 'advisory' (loga, não bloqueia) | 'gate' | 'off'
        self.last_ai = None           # último parecer da IA
        self._daily_stop_notified = False
        self._notify_day = None       # data do último reset dos alertas diários

    # ── API pública (blueprint) ────────────────────────────────────────────
    def ensure_started(self, symbol=None):
        with self._lock:
            if symbol:
                self.symbol = symbol
            if self._thread and self._thread.is_alive():
                return
            self._thread = threading.Thread(target=self._loop, daemon=True,
                                            name="v7-runtime")
            self._thread.start()
            logger.info("V7Runtime iniciado (%s)", self.symbol)

    def set_enabled(self, on: bool):
        with self._lock:
            self.enabled = bool(on)
            self.last_exec_msg = ("Auto-trade LIGADO" if on else
                                  "Auto-trade desligado (segue avaliando)")
        logger.info("V7 auto-trade: %s", "ON" if on else "OFF")
        if on:
            self._notify(f"🟢 <b>AUTO LIGADO</b> — operando {self.symbol}\n"
                         f"Setups: GAP_FADE 09:15 + ORB 10:00-10:45 · "
                         f"contratos base×{self.vol_mult} · IA {self.ai_mode}\n"
                         f"Stop diário {DAILY_STOP_R}R · zeragem 16:55")
        else:
            self._notify("🔴 <b>AUTO DESLIGADO</b> — não abrirá novos trades "
                         "(posição aberta, se houver, segue sendo gerida).")

    def set_config(self, vol_mult=None, chart_tf=None, ai_mode=None):
        with self._lock:
            if vol_mult is not None:
                self.vol_mult = max(0.0, min(50.0, float(vol_mult)))
            if chart_tf is not None and int(chart_tf) in (1, 5, 15, 30, 60):
                self.chart_tf = int(chart_tf)
                self._candles_cache.pop(self.chart_tf, None)
            if ai_mode in ("advisory", "gate", "off"):
                self.ai_mode = ai_mode
        return {"vol_mult": self.vol_mult, "chart_tf": self.chart_tf, "ai_mode": self.ai_mode}

    def snapshot(self):
        with self._lock:
            sig = self.last_eval or {}
            return {
                "ok": True,
                "engine": "v7.4.1",
                "symbol": self.symbol,
                "status": self.status,
                "enabled": self.enabled,
                "is_demo": self.is_demo,
                "account": self.account,
                "last_eval_ts": self.last_eval_ts,
                "last_exec_msg": self.last_exec_msg,
                "vol_mult": self.vol_mult,
                "chart_tf": self.chart_tf,
                "ai_mode": self.ai_mode,
                "last_ai": self.last_ai,
                "vol_gap": VOLUME_GAP * self.vol_mult,
                "vol_orb": VOLUME_ORB * self.vol_mult,
                "signal": {k: sig.get(k) for k in
                           ("acao", "setup", "score", "regime", "janela",
                            "entrada", "stop", "tp1", "tp2", "modo_entrada",
                            "htf_trend", "sinais")} if sig else None,
                "levels": {k: sig.get(k) for k in
                           ("or_high", "or_low", "y_high", "y_low", "vwap")} if sig else {},
                "position": self.position,
                "today": today_stats(),
                "daily_stop_r": DAILY_STOP_R,
                "trades": read_trades(25),
            }

    def get_tick(self, symbol=None):
        """Preço atual (leve) p/ atualizar o último candle ao vivo — igual /api/crypto/tick."""
        sym = (symbol or self.symbol or "").strip()
        try:
            import MetaTrader5 as mt5
            if not self._mt5_ok(mt5):
                return None
            try:
                mt5.symbol_select(sym, True)
            except Exception:
                pass
            t = mt5.symbol_info_tick(sym)
            if t is None:
                return None
            return {
                "price": float(t.last or t.bid or 0),
                "bid": float(t.bid or 0),
                "ask": float(t.ask or 0),
                "time": int(t.time or 0),
            }
        except Exception as exc:
            logger.debug("V7 get_tick: %s", exc)
            return None

    def get_candles(self, n=220, tf=None, symbol=None):
        """Candles do gráfico. symbol opcional (WIN/WDO contrato) — só afeta o gráfico."""
        tf = int(tf or self.chart_tf)
        sym = (symbol or self.symbol or "").strip()
        cache_key = (sym, tf)
        if not hasattr(self, "_candles_cache_sym"):
            self._candles_cache_sym = {}
        cached, ts = self._candles_cache_sym.get(cache_key, ([], 0.0))
        if cached and time.time() - ts < 5:
            return cached, None
        # mantém cache legado do WIN do motor
        if sym == self.symbol:
            cached_old, ts_old = self._candles_cache.get(tf, ([], 0.0))
            if cached_old and time.time() - ts_old < 5:
                return cached_old, None
        try:
            import MetaTrader5 as mt5
            df = self._df_tf(mt5, tf, bars=n + 5, symbol=sym)
            if df is None:
                err = f"MT5 sem candles ({sym})"
                return cached, err
            out = [{"time": int(ix.timestamp()), "open": float(r["Open"]),
                    "high": float(r["High"]), "low": float(r["Low"]),
                    "close": float(r["Close"])} for ix, r in df.tail(n).iterrows()]
            self._candles_cache_sym[cache_key] = (out, time.time())
            if sym == self.symbol:
                self._candles_cache[tf] = (out, time.time())
            return out, None
        except Exception as exc:
            logger.warning("V7 get_candles erro: %s", exc)
            return cached, str(exc)

    def close_position_manual(self):
        try:
            import MetaTrader5 as mt5
            pos = self._our_position(mt5)
            if not pos:
                return False, "sem posição"
            ok = self._close(mt5, pos)
            return ok, None if ok else "falha ao fechar"
        except Exception as exc:
            return False, str(exc)

    # ── MT5 helpers ────────────────────────────────────────────────────────
    def _mt5_ok(self, mt5):
        """Garante MT5 inicializado (com as credenciais do .env, igual ao
        Monitor MT5) e o símbolo selecionado no Market Watch."""
        if mt5.terminal_info() is None:
            try:
                from services.trade_executor import _mt5_init_kwargs
                kwargs = _mt5_init_kwargs()
            except Exception:
                kwargs = {}
            if not mt5.initialize(**kwargs):
                return False
        try:
            mt5.symbol_select(self.symbol, True)
        except Exception:
            pass
        if self.account is None:
            acc = mt5.account_info()
            if acc:
                self.account = acc.login
                self.is_demo = (acc.trade_mode == 0)
        return True

    def _df_tf(self, mt5, tf_min, bars=700, symbol=None):
        """Candles de um timeframe qualquer (para o gráfico)."""
        import pandas as pd
        sym = (symbol or self.symbol or "").strip()
        if not self._mt5_ok(mt5):
            return None
        try:
            mt5.symbol_select(sym, True)
        except Exception:
            pass
        _TF = {1: mt5.TIMEFRAME_M1, 5: mt5.TIMEFRAME_M5, 15: mt5.TIMEFRAME_M15,
               30: mt5.TIMEFRAME_M30, 60: mt5.TIMEFRAME_H1}
        tf = _TF.get(int(tf_min), mt5.TIMEFRAME_M15)
        rates = None
        for count in (bars, 400, 200, 100):
            if count > bars:
                continue
            rates = mt5.copy_rates_from_pos(sym, tf, 0, count)
            if rates is not None and len(rates) > 0:
                break
        if rates is None or len(rates) == 0:
            logger.debug("V7 _df_tf vazio p/ %s tf=%s: %s", sym, tf_min, mt5.last_error())
            return None
        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s")
        df = df.set_index("time")
        vol = df["real_volume"] if df["real_volume"].sum() > 0 else df["tick_volume"]
        return pd.DataFrame({"Open": df["open"], "High": df["high"],
                             "Low": df["low"], "Close": df["close"],
                             "Volume": vol}, index=df.index)

    def _df15(self, mt5, bars=700):
        """O MOTOR sempre usa 15m (timeframe validado no backtest)."""
        return self._df_tf(mt5, 15, bars)

    def _our_position(self, mt5):
        for p in (mt5.positions_get(symbol=self.symbol) or []):
            if p.magic == MAGIC:
                return p
        return None

    def _send(self, mt5, direction, volume, sl=None, tp=None):
        # Blindagem de concorrência: outro módulo (v6/scalper/crypto) pode ter
        # dado mt5.shutdown() logo antes. Re-garante a conexão + o símbolo, e
        # tenta pegar o tick algumas vezes antes de desistir.
        tick = info = None
        for _try in range(5):
            self._mt5_ok(mt5)
            try:
                mt5.symbol_select(self.symbol, True)
            except Exception:
                pass
            tick = mt5.symbol_info_tick(self.symbol)
            info = mt5.symbol_info(self.symbol)
            if tick is not None and info is not None \
                    and (getattr(tick, "ask", 0) or getattr(tick, "bid", 0)):
                break
            time.sleep(0.4)
        if tick is None or info is None:
            return None, "sem tick (MT5 indisponível no instante da ordem)"
        # preço a mercado: usa ask/bid; se algum vier 0, cai para o last
        _ask = tick.ask or tick.last or 0
        _bid = tick.bid or tick.last or 0
        if not _ask or not _bid:
            return None, "sem cotação (bid/ask/last zerados)"
        buy = direction == "COMPRA"
        # ── FIX 22/07 (retcode 10016 "Invalid stops" no rally do ORB) ─────────
        # 1. SL/TP arredondados ao tick do símbolo (WIN = 5 pts; fração = inválido)
        # 2. respeita trade_stops_level (distância mínima da corretora)
        # 3. anti-chase: se o preço já correu até/além do alvo, ABORTA com motivo
        #    claro (perseguir rompimento esticado destrói o R:R do setup)
        ts_ = float(info.trade_tick_size or info.point or 5.0)
        def _snap(x):
            return round(round(float(x)/ts_)*ts_, 8)
        min_d = max(float(getattr(info, "trade_stops_level", 0) or 0)*float(info.point or ts_),
                    2*ts_)
        px_ref = _ask if buy else _bid
        if sl:
            sl = _snap(sl)
            if buy and sl >= px_ref - min_d:
                sl = _snap(px_ref - min_d - ts_)
            if (not buy) and sl <= px_ref + min_d:
                sl = _snap(px_ref + min_d + ts_)
        if tp:
            tp = _snap(tp)
            if buy and tp <= px_ref + min_d:
                return None, (f"preço já correu além do alvo (ask {px_ref:.0f} vs TP {tp:.0f}) "
                              f"— entrada abortada (anti-chase)")
            if (not buy) and tp >= px_ref - min_d:
                return None, (f"preço já correu além do alvo (bid {px_ref:.0f} vs TP {tp:.0f}) "
                              f"— entrada abortada (anti-chase)")
        filling = mt5.ORDER_FILLING_IOC
        if info.filling_mode & mt5.ORDER_FILLING_FOK:
            filling = mt5.ORDER_FILLING_FOK
        req = {"action": mt5.TRADE_ACTION_DEAL, "symbol": self.symbol,
               "volume": float(volume),
               "type": mt5.ORDER_TYPE_BUY if buy else mt5.ORDER_TYPE_SELL,
               "price": px_ref, "deviation": 30,
               "magic": MAGIC, "comment": COMMENT,
               "type_time": mt5.ORDER_TIME_GTC, "type_filling": filling}
        if sl: req["sl"] = float(sl)
        if tp: req["tp"] = float(tp)
        _RETC = {10004: "requote (preço mudou)", 10016: "stops inválidos (SL/TP)",
                 10018: "mercado fechado", 10019: "sem margem/saldo",
                 10021: "preço fora do book", 10030: "filling não suportado",
                 10027: "autotrading desabilitado no terminal"}
        r = mt5.order_send(req)
        if r and r.retcode == mt5.TRADE_RETCODE_DONE:
            return r, None
        # retry único em requote/preço fora (mercado rápido): re-cota e re-envia
        if r and r.retcode in (10004, 10021):
            time.sleep(0.3)
            t2 = mt5.symbol_info_tick(self.symbol)
            if t2:
                req["price"] = (t2.ask if buy else t2.bid) or req["price"]
                r = mt5.order_send(req)
                if r and r.retcode == mt5.TRADE_RETCODE_DONE:
                    return r, None
        rc = r.retcode if r else "?"
        return None, f"retcode {rc} — {_RETC.get(rc, 'erro não mapeado')}"

    def _close(self, mt5, pos, volume=None):
        tick = mt5.symbol_info_tick(pos.symbol)
        if tick is None:
            return False
        buy_to_close = pos.type == 1
        req = {"action": mt5.TRADE_ACTION_DEAL, "symbol": pos.symbol,
               "volume": float(volume or pos.volume), "position": pos.ticket,
               "type": mt5.ORDER_TYPE_BUY if buy_to_close else mt5.ORDER_TYPE_SELL,
               "price": tick.ask if buy_to_close else tick.bid, "deviation": 30,
               "magic": MAGIC, "comment": COMMENT + "-close",
               "type_time": mt5.ORDER_TIME_GTC,
               "type_filling": mt5.ORDER_FILLING_IOC}
        r = mt5.order_send(req)
        return bool(r and r.retcode == mt5.TRADE_RETCODE_DONE)

    def _modify_sl(self, mt5, pos, new_sl):
        req = {"action": mt5.TRADE_ACTION_SLTP, "symbol": pos.symbol,
               "position": pos.ticket, "sl": float(new_sl),
               "tp": float(pos.tp or 0)}
        r = mt5.order_send(req)
        return bool(r and r.retcode == mt5.TRADE_RETCODE_DONE)

    def _realized(self, mt5, ticket, tries=6):
        for _ in range(tries):
            deals = mt5.history_deals_get(datetime.now() - timedelta(days=2),
                                          datetime.now(), position=ticket) or []
            outs = [d.profit for d in deals if d.entry == 1]
            if outs:
                return sum(outs)
            time.sleep(0.5)
        return None

    # ── Loop principal ─────────────────────────────────────────────────────
    def _loop(self):
        from services.technical_analysis_v7 import generate_signal_v7
        while True:
            try:
                import MetaTrader5 as mt5
                if not self._mt5_ok(mt5):
                    self.status = "MT5 indisponível"
                    time.sleep(30); continue
                if self.is_demo is False:
                    self.status = "BLOQUEADO: conta não é demo"
                    time.sleep(60); continue

                now = datetime.now(_BRT)
                # reset dos flags de alerta diário na virada do dia
                if self._notify_day != now.date():
                    self._notify_day = now.date()
                    self._daily_stop_notified = False
                eod = (now.hour, now.minute) >= EOD_HHMM
                pos = self._our_position(mt5)
                self._sync_position_view(mt5, pos)

                if pos:
                    self._manage(mt5, pos, eod)
                    time.sleep(CHECK_SEC); continue

                # posição acabou de fechar? registra resultado
                for tk, st in list(self._state.items()):
                    profit = self._realized(mt5, tk)
                    if profit is not None:
                        rm = round(profit / st["risk_brl"], 3) if st["risk_brl"] else ""
                        _update_row(st["id"], ts_close=now.isoformat(),
                                    profit_brl=round(profit, 2), r_multiple=rm,
                                    exit_reason="MT5")
                        self.last_exec_msg = f"Trade fechado: R$ {profit:+.2f} ({rm} R)"
                        logger.info("V7 trade fechado: R$ %.2f (%s R)", profit, rm)
                        _ico = "✅" if profit > 0 else ("🔻" if profit < 0 else "➖")
                        self._notify(f"{_ico} <b>FECHADO — {st.get('setup','?')}</b>\n"
                                     f"Resultado: R$ {profit:+.2f} ({rm} R)\n"
                                     f"{self._day_summary_line()}")
                        # Alerta de stop diário atingido (uma vez)
                        if today_realized_r() <= DAILY_STOP_R and not self._daily_stop_notified:
                            self._daily_stop_notified = True
                            self._notify(f"🛑 <b>STOP DIÁRIO ({DAILY_STOP_R}R) ATINGIDO</b>\n"
                                         f"Não abrirei mais trades hoje. {self._day_summary_line()}")
                    del self._state[tk]

                if now.weekday() >= 5 or eod:
                    self.status = "fora do pregão"
                    time.sleep(60); continue

                # avaliação por candle fechado (sempre — mesmo com auto OFF)
                df15 = self._df15(mt5)
                if df15 is None or len(df15) < 120:
                    self.status = "sem dados"
                    time.sleep(CHECK_SEC); continue
                lc = df15.index[-2]
                if lc != self._last_candle:
                    self._last_candle = lc
                    h1 = df15.resample("1h").agg(
                        {"Open": "first", "High": "max", "Low": "min",
                         "Close": "last", "Volume": "sum"}).dropna()
                    sig = generate_signal_v7(df15.iloc[:-1], htf_df=h1,
                                             symbol=self.symbol)
                    with self._lock:
                        self.last_eval = sig
                        self.last_eval_ts = now.strftime("%H:%M:%S")
                    if sig and sig["acao"] in ("COMPRA", "VENDA") \
                            and sig.get("setup") in ("GAP_FADE", "ORB"):
                        # Parecer da IA (consultivo): calcula e mostra na tela
                        # SEMPRE que um setup dispara — mesmo com auto OFF.
                        ai = self._ai_opinion(sig)
                        with self._lock:
                            self.last_ai = ai
                        self._maybe_execute(mt5, sig, now, ai)

                dr = today_realized_r()
                self.status = (f"stop diário atingido ({dr}R)" if dr <= DAILY_STOP_R
                               else ("operando" if self.enabled else "monitorando (auto OFF)"))
            except Exception as exc:
                logger.warning("V7 loop: %s", exc)
                self.status = f"erro: {exc}"
            time.sleep(CHECK_SEC)

    def _ai_opinion(self, sig):
        """Parecer consultivo da IA. Nunca lança exceção."""
        if self.ai_mode == "off":
            return {"veredito": "OFF", "confianca": None, "motivo": "IA desligada", "ia_usada": False}
        try:
            from services.v7_ai_advisor import advise
            ai = advise(sig, self.symbol)
            ai["ts"] = datetime.now(_BRT).strftime("%H:%M:%S")
            ai["setup"] = sig.get("setup"); ai["acao"] = sig.get("acao")
            return ai
        except Exception as exc:
            logger.warning("V7 _ai_opinion: %s", exc)
            return {"veredito": "N/D", "confianca": None, "motivo": str(exc), "ia_usada": False}

    def _maybe_execute(self, mt5, sig, now, ai=None):
        ai = ai or {"veredito": "N/D", "confianca": None, "motivo": ""}
        setup = sig["setup"]
        if not self.enabled:
            self.last_exec_msg = (f"Sinal {setup} {sig['acao']} — auto OFF "
                                  f"(IA: {ai.get('veredito')})")
            return
        if today_realized_r() <= DAILY_STOP_R:
            self.last_exec_msg = "Sinal ignorado: stop diário -3R atingido"
            return
        # ── IA em modo GATE: só aqui a IA pode bloquear (padrão é advisory) ──
        if self.ai_mode == "gate" and ai.get("veredito") == "BLOQUEAR":
            self.last_exec_msg = f"IA BLOQUEOU (gate): {ai.get('motivo')}"
            self._log_blocked(sig, now, ai, "AI_GATE")
            return
        entry_ref, stop = float(sig["entrada"]), float(sig["stop"])
        risk_pts = abs(entry_ref - stop)
        if risk_pts <= 0:
            return
        base = VOLUME_GAP if setup == "GAP_FADE" else VOLUME_ORB
        vol = round(base * self.vol_mult, 2)
        if vol <= 0:
            self.last_exec_msg = "Volume configurado = 0 — não executado"
            return
        tp = float(sig["tp1"]) if setup == "GAP_FADE" else None
        # ORB: TP não vai na ordem MT5 (gestão software: BE +1R, final +2.5R).
        # Calculamos os alvos aqui p/ UI/Telegram/CSV — senão o painel só mostra SL.
        buy = sig["acao"] == "COMPRA"
        orb_tp1 = orb_tp2 = None
        if setup == "ORB":
            sign = 1 if buy else -1
            orb_tp1 = entry_ref + sign * PARTIAL_AT_R * risk_pts
            orb_tp2 = entry_ref + sign * TP2_R * risk_pts
        r, err = self._send(mt5, sig["acao"], vol, sl=stop, tp=tp)
        if err:
            self.last_exec_msg = f"Ordem falhou: {err}"
            logger.warning("V7 ordem falhou: %s", err)
            self._notify(f"⚠️ <b>SETUP PERDIDO — {setup}</b>\n"
                         f"{sig['acao']} {self.symbol} @ {entry_ref:.0f} tentou entrar mas a "
                         f"ORDEM FALHOU: {err}")
            return
        self._next_id += 1
        pt_val = 0.20 if "WIN" in self.symbol.upper() else 10.0
        risk_brl = risk_pts * pt_val * vol
        fill = float(r.price)
        # Recalcula alvos ORB no preço real de fill (pode divergir do sinal)
        if setup == "ORB":
            sign = 1 if buy else -1
            risk_fill = abs(fill - stop) or risk_pts
            orb_tp1 = fill + sign * PARTIAL_AT_R * risk_fill
            orb_tp2 = fill + sign * TP2_R * risk_fill
            risk_pts = risk_fill
            risk_brl = risk_pts * pt_val * vol
        pos2 = self._our_position(mt5)
        if pos2:
            self._state[pos2.ticket] = {
                "id": self._next_id, "setup": setup, "entry": fill,
                "risk": risk_pts, "risk_brl": risk_brl,
                "tp1": orb_tp1, "tp2": orb_tp2,
                "partial_done": False, "t0": time.time()}
        log_tp = tp if tp is not None else orb_tp2
        _append_row({"id": self._next_id, "ts_open": now.isoformat(),
                     "ts_close": "", "symbol": self.symbol, "setup": setup,
                     "dir": sig["acao"], "volume": vol,
                     "entry": round(fill, 1), "stop": round(stop, 1),
                     "tp": round(log_tp, 1) if log_tp else "",
                     "risk_pts": round(risk_pts, 1), "partial_done": 0,
                     "exit_reason": "", "profit_brl": "", "r_multiple": "",
                     "flow_used": 0, "flow_reason": "",
                     "ai_veredito": ai.get("veredito"), "ai_conf": ai.get("confianca"),
                     "ai_motivo": ai.get("motivo", "")})
        _flag = "" if ai.get("veredito") in ("EXECUTAR", "N/D", "OFF", None) \
                else f" [IA disse {ai.get('veredito')}]"
        self.last_exec_msg = (f"🟢 {setup} {sig['acao']} @ {fill:.0f} | "
                              f"SL {stop:.0f} | {vol}c · risco R$ {risk_brl:.2f}{_flag}")
        logger.info("V7 EXECUTADO %s", self.last_exec_msg)
        if setup == "ORB" and orb_tp1 and orb_tp2:
            if vol >= 2:
                _tptxt = (f" · Parcial +{PARTIAL_AT_R:.0f}R @ {orb_tp1:.0f}"
                          f" · Alvo final +{TP2_R}R @ {orb_tp2:.0f}")
            else:
                _tptxt = (f" · BE +{PARTIAL_AT_R:.0f}R @ {orb_tp1:.0f}"
                          f" · Alvo +{TP2_R}R @ {orb_tp2:.0f}")
        else:
            _tptxt = f" · Alvo {tp:.0f}" if tp else ""
        self._notify(
            f"🟢 <b>ENTRADA — {setup}</b>\n"
            f"{sig['acao']} {self.symbol} · {vol} contrato(s)\n"
            f"Entrada {fill:.0f} · Stop {stop:.0f}{_tptxt}\n"
            f"Risco: R$ {risk_brl:.2f} ({risk_pts:.0f} pts)\n"
            f"IA: {ai.get('veredito')}"
            + (f" ({ai.get('motivo')})" if ai.get('motivo') and ai.get('veredito') not in ('N/D','OFF') else ""))

    def _log_blocked(self, sig, now, ai, reason):
        """Registra no CSV um sinal que a IA bloqueou (modo gate) — para auditoria."""
        self._next_id += 1
        _append_row({"id": self._next_id, "ts_open": now.isoformat(),
                     "ts_close": now.isoformat(), "symbol": self.symbol,
                     "setup": sig.get("setup"), "dir": sig.get("acao"), "volume": 0,
                     "entry": sig.get("entrada"), "stop": sig.get("stop"),
                     "tp": sig.get("tp1"), "risk_pts": "", "partial_done": 0,
                     "exit_reason": reason, "profit_brl": 0, "r_multiple": 0,
                     "flow_used": 0, "flow_reason": "",
                     "ai_veredito": ai.get("veredito"), "ai_conf": ai.get("confianca"),
                     "ai_motivo": ai.get("motivo", "")})

    def _manage(self, mt5, pos, eod):
        st = self._state.get(pos.ticket)
        tick = mt5.symbol_info_tick(self.symbol)
        price = (tick.last or (tick.bid + tick.ask) / 2) if tick else None
        if eod:
            self._close(mt5, pos)
            self.last_exec_msg = "Zeragem EOD (16:55)"
            self._notify("🕐 <b>ZERAGEM 16:55</b> — posição encerrada no fim do pregão.")
            return
        if not st or st["setup"] != "ORB" or not price:
            return                            # GAP_FADE: MT5 gerencia SL/TP
        buy = pos.type == 0
        risk = st["risk"]
        r_now = ((price - st["entry"]) / risk) if buy else ((st["entry"] - price) / risk)
        if not st["partial_done"] and r_now >= PARTIAL_AT_R:
            if pos.volume >= 2:
                # Volume alto (legado / vol_mult): parcial 50% + BE
                if self._close(mt5, pos, volume=pos.volume / 2):
                    st["partial_done"] = True
                    p2 = self._our_position(mt5)
                    if p2:
                        self._modify_sl(mt5, p2, st["entry"])
                    _update_row(st["id"], partial_done=1)
                    self.last_exec_msg = "Parcial +1R feita; SL no breakeven"
                    logger.info("V7 parcial +1R; SL -> BE")
                    self._notify("📈 <b>+1R — PARCIAL</b>\nMetade fechada no lucro, "
                                 "stop movido para a entrada (risco zero no restante).")
            else:
                # 1 contrato: sem parcial — só BE e segue trail/TP2
                if self._modify_sl(mt5, pos, st["entry"]):
                    st["partial_done"] = True
                    _update_row(st["id"], partial_done=1)
                    self.last_exec_msg = "ORB +1R: SL no breakeven (1 contrato)"
                    logger.info("V7 ORB +1R; SL -> BE (sem parcial, vol=%.1f)", pos.volume)
                    self._notify("📈 <b>+1R — BREAKEVEN</b>\n"
                                 "Stop na entrada (1 contrato, sem parcial). "
                                 "Trailing até o alvo final.")
        elif st["partial_done"]:
            if r_now >= TP2_R:
                self._close(mt5, pos)
                self.last_exec_msg = f"TP2 {TP2_R}R atingido"
                self._notify(f"🎯 <b>ALVO FINAL (TP2 {TP2_R}R)</b> — restante fechado no lucro.")
            else:
                df = self._df15(mt5, 30)
                if df is not None and len(df) > 15:
                    atr = float((df["High"] - df["Low"]).tail(14).mean())
                    c = float(df["Close"].iloc[-2])
                    trail = c - TRAIL_ATR * atr if buy else c + TRAIL_ATR * atr
                    cur = pos.sl or 0
                    if (buy and trail > cur) or ((not buy) and (cur == 0 or trail < cur)):
                        self._modify_sl(mt5, pos, trail)
        else:
            bars = int((time.time() - st["t0"]) // (15 * 60))
            if bars >= TIME_STOP_BARS and r_now < 0.5:
                self._close(mt5, pos)
                self.last_exec_msg = f"Time-stop: {bars} candles sem 0.5R"
                self._notify(f"⏱️ <b>TIME-STOP</b> — {bars} candles sem andar 0,5R, "
                             "saída para liberar o capital.")

    def _sync_position_view(self, mt5, pos):
        if not pos:
            with self._lock:
                self.position = None
            return
        st = self._state.get(pos.ticket, {})
        buy = pos.type == 0
        entry = float(pos.price_open or 0)
        sl = float(pos.sl or 0) if pos.sl else None
        risk = st.get("risk") or (abs(entry - sl) if sl else 0) or 1
        # ORB: alvos geridos em software — deriva se o estado sumiu (restart)
        tp1 = st.get("tp1")
        tp2 = st.get("tp2")
        if st.get("setup") == "ORB" or (not pos.tp and sl and risk > 0):
            sign = 1 if buy else -1
            if not tp1:
                tp1 = entry + sign * PARTIAL_AT_R * risk
            if not tp2:
                tp2 = entry + sign * TP2_R * risk
        partial = bool(st.get("partial_done", False))
        # Próximo alvo visível: após parcial → TP2; senão → TP1 (parcial)
        next_tp = None
        if pos.tp:
            next_tp = float(pos.tp)
        elif tp2 and partial:
            next_tp = float(tp2)
        elif tp1:
            next_tp = float(tp1)
        r_now = ((pos.price_current - entry) / risk) if buy else \
                ((entry - pos.price_current) / risk)
        with self._lock:
            self.position = {
                "ticket": pos.ticket, "dir": "COMPRA" if buy else "VENDA",
                "setup": st.get("setup") or ("ORB" if (tp1 and not pos.tp) else "?"),
                "volume": pos.volume,
                "entry": round(entry, 1),
                "price": round(pos.price_current, 1),
                "sl": round(sl, 1) if sl else None,
                "tp": round(next_tp, 1) if next_tp else None,
                "tp1": round(float(tp1), 1) if tp1 else None,
                "tp2": round(float(tp2), 1) if tp2 else None,
                "tp_label": (
                    ("TP2 +%.1fR" % TP2_R) if partial
                    else (("+%.0fR BE" % PARTIAL_AT_R) if float(pos.volume or 0) < 2
                          else ("TP1 +%.0fR parcial" % PARTIAL_AT_R))
                ),
                "profit_brl": round(pos.profit, 2),
                "r_now": round(r_now, 2),
                "partial_done": partial,
                "symbol": self.symbol,
            }

    def _notify(self, msg):
        try:
            from services.telegram_notifier import _send
            hh = datetime.now(_BRT).strftime("%H:%M")
            _send(f"⚡ <b>Monitor V7</b> · {hh}\n{msg}")
        except Exception:
            pass

    def _day_summary_line(self):
        """Resumo curto do dia para anexar aos alertas de fechamento."""
        t = today_stats()
        return (f"Dia: {t['trades']} trade(s) · {t['wins']}✅/{t['losses']}❌ · "
                f"R$ {t['pnl_brl']:+.2f} ({t['pnl_r']:+.2f} R)")


runtime = V7Runtime()
