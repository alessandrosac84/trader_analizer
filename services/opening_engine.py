"""
services/opening_engine.py — OPENING ENGINE (abertura do WIN).

Motor DETERMINÍSTICO (sem IA no cálculo) que lê o mercado a partir de ~08:50 e
durante os primeiros 15 min do pregão e produz um SCORE 0–100 de COMPRA e de
VENDA para o candle de abertura do WIN (M15), na linha da conversa de projeto:

  Contexto diário · Gap · Posição vs POC/VAH/VAL · Opening Range · Volume ·
  Agressão (Times&Trades) · Price Action · Contexto externo (WDO).

FILOSOFIA: o motor NÃO tenta adivinhar o candle. Ele calcula qual CENÁRIO está se
confirmando e devolve LONG / SHORT / WAIT. Nenhuma ordem é enviada — é uma tela de
DECISÃO (análise). A execução, se um dia existir, fica noutro módulo.

MÓDULO NOVO E ISOLADO: não importa nem altera V7, WinGo, Monitor, EOD ou Crypto.
Roda só na instância B3 (MT5_PROFILE != crypto). Loga cada pregão em
logs/opening_engine_YYYY-MM-DD.json p/ backtest posterior.

Pesos (somam 100) — ajustáveis em WEIGHTS:
  contexto 15 · gap 10 · value_area 15 · opening_range 15 · volume 10 ·
  agressao 15 · price_action 10 · externo 10
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

_BRT = timezone(timedelta(hours=-3))

# Janela de trabalho do motor (BRT)
WINDOW_START = (8, 50)     # começa a montar contexto
OPEN_HHMM = (9, 0)         # abertura do pregão
WINDOW_END = (9, 20)       # encerra a análise da abertura (candle 15m fechou 09:15)
OR_MINUTES = 5             # opening range = primeiros 5 min
CHECK_SEC = 5

WEIGHTS = {
    "contexto": 15, "gap": 10, "value_area": 15, "opening_range": 15,
    "volume": 10, "agressao": 15, "price_action": 10, "externo": 10,
}

# Classificação do score
def classify(score: float) -> str:
    if score >= 80: return "FORTE"
    if score >= 70: return "SETUP"
    if score >= 60: return "ATENCAO"
    if score >= 40: return "FRACO"
    return "NAO_OPERAR"


# ── MT5 (mesma conta B3 das outras instâncias) ─────────────────────────────
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


def _win_symbol() -> str:
    return os.getenv("WIN_MT5_SYMBOL", "WINV26").strip()


def _wdo_symbol() -> str:
    return os.getenv("WDO_MT5_SYMBOL", "WDOV26").strip()


def _now() -> datetime:
    return datetime.now(_BRT)


def _hhmm(now=None):
    now = now or _now()
    return (now.hour, now.minute)


# ── Coleta de dados ────────────────────────────────────────────────────────
def _rates_df(mt5, symbol, timeframe, count):
    r = mt5.copy_rates_from_pos(symbol, timeframe, 0, count)
    if r is None or len(r) == 0:
        return None
    return r


def _prev_session_refs(mt5, symbol):
    """Fechamento, máxima, mínima e Volume Profile (POC/VAH/VAL) do pregão ANTERIOR,
    montados a partir de candles M5 do dia anterior."""
    out = {"prev_close": None, "prev_high": None, "prev_low": None,
           "poc": None, "vah": None, "val": None, "ok": False}
    try:
        rates = _rates_df(mt5, symbol, mt5.TIMEFRAME_M5, 600)
        if rates is None:
            return out
        import pandas as pd
        df = pd.DataFrame(rates)
        df["dt"] = pd.to_datetime(df["time"], unit="s")
        df["day"] = df["dt"].dt.date
        days = sorted(df["day"].unique())
        if len(days) < 2:
            return out
        today = _now().date()
        prev_days = [d for d in days if d < today]
        if not prev_days:
            return out
        pday = prev_days[-1]
        d = df[df["day"] == pday]
        if d.empty:
            return out
        vol = d["real_volume"].where(d["real_volume"] > 0, d["tick_volume"])
        out["prev_close"] = float(d["close"].iloc[-1])
        out["prev_high"] = float(d["high"].max())
        out["prev_low"] = float(d["low"].min())
        # Volume profile por faixa de preço (bins do tamanho de 1 tick-agrupado)
        lo, hi = float(d["low"].min()), float(d["high"].max())
        if hi > lo:
            nbins = 40
            step = (hi - lo) / nbins
            prof = defaultdict(float)
            for (_, row), v in zip(d.iterrows(), vol):
                mid = (row["high"] + row["low"]) / 2.0
                b = int((mid - lo) / step) if step > 0 else 0
                prof[b] += float(v or 0)
            if prof:
                poc_bin = max(prof, key=prof.get)
                out["poc"] = round(lo + (poc_bin + 0.5) * step)
                # Value Area = 70% do volume ao redor do POC
                total = sum(prof.values())
                target = 0.70 * total
                acc = prof[poc_bin]
                lo_b = hi_b = poc_bin
                while acc < target and (lo_b > 0 or hi_b < nbins - 1):
                    down = prof.get(lo_b - 1, 0)
                    up = prof.get(hi_b + 1, 0)
                    if up >= down and hi_b < nbins - 1:
                        hi_b += 1; acc += up
                    elif lo_b > 0:
                        lo_b -= 1; acc += down
                    else:
                        hi_b += 1; acc += up
                out["val"] = round(lo + lo_b * step)
                out["vah"] = round(lo + (hi_b + 1) * step)
        out["ok"] = out["poc"] is not None
    except Exception as exc:
        logger.debug("prev_session_refs: %s", exc)
    return out


def _today_open_and_or(mt5, symbol):
    """Abertura de hoje + máxima/mínima do opening range (primeiros OR_MINUTES min)."""
    out = {"open": None, "or_high": None, "or_low": None, "last": None,
           "cur_high": None, "cur_low": None, "vol_now": None, "vol_avg": None}
    try:
        rates = _rates_df(mt5, symbol, mt5.TIMEFRAME_M1, 400)
        if rates is None:
            return out
        import pandas as pd
        df = pd.DataFrame(rates)
        df["dt"] = pd.to_datetime(df["time"], unit="s")
        df["day"] = df["dt"].dt.date
        today = _now().date()
        d = df[df["day"] == today]
        if d.empty:
            return out
        out["open"] = float(d["open"].iloc[0])
        out["last"] = float(d["close"].iloc[-1])
        out["cur_high"] = float(d["high"].max())
        out["cur_low"] = float(d["low"].min())
        d = d.copy()
        d["mod"] = d["dt"].dt.hour * 60 + d["dt"].dt.minute
        # Abertura REAL detectada pelo 1º candle de hoje (auto-adapta a 09:00 ou 10:00)
        open_min = int(d["mod"].iloc[0])
        out["open_min"] = open_min
        out["open_hhmm"] = f"{open_min // 60:02d}:{open_min % 60:02d}"
        orr = d[(d["mod"] >= open_min) & (d["mod"] < open_min + OR_MINUTES)]
        if not orr.empty:
            out["or_high"] = float(orr["high"].max())
            out["or_low"] = float(orr["low"].min())
        vol = d["real_volume"].where(d["real_volume"] > 0, d["tick_volume"])
        # "surto atual" = média das últimas 3 barras M1 vs mediana histórica das barras
        out["vol_now"] = float(vol.iloc[-3:].mean()) if len(vol) >= 1 else None
        base = df[df["day"] < today]
        if not base.empty:
            bv = base["real_volume"].where(base["real_volume"] > 0, base["tick_volume"])
            med = float(bv.median())
            out["vol_avg"] = med if med > 0 else None
    except Exception as exc:
        logger.debug("today_open_and_or: %s", exc)
    return out


def _aggression(mt5, symbol, seconds=120):
    """Saldo de agressão (Times&Trades) nos últimos `seconds`: volume comprador
    (tick a mercado no ask) vs vendedor (no bid), via flags do tick.

    FIX: usa o TEMPO DO SERVIDOR como referência (tick.time é epoch em hora do
    servidor, não UTC) — pedir por janela UTC devolvia 0 ticks. Faz symbol_select
    antes e tem fallback por contagem (copy_ticks_from)."""
    try:
        mt5.symbol_select(symbol, True)
        tk = mt5.symbol_info_tick(symbol)
        if tk is None:
            return None
        end_epoch = int(tk.time)  # referência no relógio do servidor
        start = datetime.utcfromtimestamp(end_epoch - seconds)
        end = datetime.utcfromtimestamp(end_epoch + 1)
        raw = mt5.copy_ticks_range(symbol, start, end, mt5.COPY_TICKS_ALL)
        if raw is None or len(raw) == 0:
            # fallback: pega os últimos N ticks a partir de um passado amplo
            raw = mt5.copy_ticks_from(
                symbol, datetime.utcfromtimestamp(end_epoch - seconds * 3),
                200000, mt5.COPY_TICKS_ALL)
        if raw is None or len(raw) == 0:
            return None
        FLAG_BUY = getattr(mt5, "TICK_FLAG_BUY", 32)
        FLAG_SELL = getattr(mt5, "TICK_FLAG_SELL", 64)
        has_vr = "volume_real" in raw.dtype.names
        cutoff = end_epoch - seconds
        buy = sell = 0.0
        for t in raw:
            if int(t["time"]) < cutoff:
                continue
            flags = int(t["flags"])
            v = float(t["volume_real"]) if has_vr and t["volume_real"] > 0 else float(t["volume"] or 1)
            if flags & FLAG_BUY:
                buy += v
            elif flags & FLAG_SELL:
                sell += v
        tot = buy + sell
        if tot <= 0:
            return None
        return {"buy": buy, "sell": sell, "ratio": (buy - sell) / tot}
    except Exception as exc:
        logger.debug("aggression: %s", exc)
        return None


def _aggression_from_profit(max_age=15.0):
    """Lê o saldo de agressão gravado pelo profit_flow.py (ProfitDLL). Só usa se
    o dado for recente (< max_age s). É a fonte PREFERIDA — o MT5 da XP não dá T&T."""
    try:
        path = os.path.join("data", "profit_aggression.json")
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        if time.time() - float(d.get("ts", 0)) > max_age:
            return None  # coletor parado / dado velho
        tot = float(d.get("buy", 0)) + float(d.get("sell", 0))
        if tot <= 0:
            return None
        return {"buy": d.get("buy"), "sell": d.get("sell"),
                "ratio": float(d.get("ratio", 0)), "fonte": "ProfitDLL"}
    except Exception:
        return None


def _wdo_dir(mt5):
    """Direção do WDO desde a abertura (contexto externo; dólar sobe → índice tende a cair)."""
    try:
        rates = _rates_df(mt5, _wdo_symbol(), mt5.TIMEFRAME_M1, 60)
        if rates is None:
            return None
        import pandas as pd
        df = pd.DataFrame(rates)
        df["dt"] = pd.to_datetime(df["time"], unit="s")
        today = _now().date()
        d = df[df["dt"].dt.date == today]
        if d.empty or len(d) < 2:
            return None
        chg = float(d["close"].iloc[-1]) - float(d["open"].iloc[0])
        return {"change": chg, "dir": "ALTA" if chg > 0 else ("BAIXA" if chg < 0 else "FLAT")}
    except Exception:
        return None


# ── Motor de score ─────────────────────────────────────────────────────────
def compute_score(refs: dict, today: dict, aggr, wdo, daily_bias: str) -> dict:
    """Score determinístico. Cada fator adiciona ao lado COMPRA ou VENDA.
    Fator sem dado é marcado 'sem_dado' e não pontua (score máximo cai)."""
    buy = 0.0
    sell = 0.0
    factors = {}
    W = WEIGHTS

    def add(name, side, pts, note, avail=True):
        nonlocal buy, sell
        factors[name] = {"side": side, "pts": round(pts, 1), "note": note, "avail": avail}
        if side == "COMPRA":
            buy += pts
        elif side == "VENDA":
            sell += pts

    o = today.get("open"); last = today.get("last")

    # 1) Contexto diário (tendência dos dias anteriores)
    if daily_bias in ("ALTA", "BAIXA"):
        add("contexto", "COMPRA" if daily_bias == "ALTA" else "VENDA", W["contexto"],
            f"tendência recente {daily_bias}")
    else:
        add("contexto", "NEUTRO", 0, "sem viés claro", avail=True)

    # 2) Gap de abertura vs fechamento anterior
    pc = refs.get("prev_close")
    if o and pc:
        gap = (o - pc) / pc * 100
        if abs(gap) >= 0.05:
            side = "COMPRA" if gap > 0 else "VENDA"
            mag = min(1.0, abs(gap) / 0.5)  # satura em 0,5%
            add("gap", side, W["gap"] * mag, f"gap {gap:+.2f}%")
        else:
            add("gap", "NEUTRO", 0, f"gap ~0 ({gap:+.2f}%)")
    else:
        add("gap", "NEUTRO", 0, "sem fechamento anterior", avail=False)

    # 3) Posição vs Value Area (POC/VAH/VAL)
    vah, val, poc = refs.get("vah"), refs.get("val"), refs.get("poc")
    price = last or o
    if price and vah and val:
        if price > vah:
            add("value_area", "COMPRA", W["value_area"], f"preço acima da VAH ({vah:.0f})")
        elif price < val:
            add("value_area", "VENDA", W["value_area"], f"preço abaixo da VAL ({val:.0f})")
        else:
            # dentro da VA: leve viés pela metade em relação ao POC
            if poc and price > poc:
                add("value_area", "COMPRA", W["value_area"] * 0.4, "dentro da VA, acima do POC")
            elif poc:
                add("value_area", "VENDA", W["value_area"] * 0.4, "dentro da VA, abaixo do POC")
            else:
                add("value_area", "NEUTRO", 0, "dentro da Value Area")
    else:
        add("value_area", "NEUTRO", 0, "Value Area indisponível", avail=False)

    # 4) Opening Range (rompimento dos primeiros 5 min)
    orh, orl = today.get("or_high"), today.get("or_low")
    if orh and orl and last:
        if last > orh:
            add("opening_range", "COMPRA", W["opening_range"], f"rompeu máxima do OR ({orh:.0f})")
        elif last < orl:
            add("opening_range", "VENDA", W["opening_range"], f"perdeu mínima do OR ({orl:.0f})")
        else:
            add("opening_range", "NEUTRO", 0, "dentro do opening range")
    else:
        add("opening_range", "NEUTRO", 0, "OR ainda não formado", avail=orh is not None)

    # 5) Volume
    vn, va = today.get("vol_now"), today.get("vol_avg")
    if vn and va and va > 0:
        ratio = vn / va
        # volume forte reforça o lado que já está ganhando (OR/gap)
        lead = "COMPRA" if buy >= sell else "VENDA"
        if ratio >= 1.2:
            add("volume", lead, W["volume"], f"volume {ratio:.1f}× a média")
        elif ratio <= 0.7:
            add("volume", "NEUTRO", 0, f"volume fraco {ratio:.1f}×")
        else:
            add("volume", lead, W["volume"] * 0.4, f"volume {ratio:.1f}×")
    else:
        add("volume", "NEUTRO", 0, "volume sem referência", avail=vn is not None)

    # 6) Agressão (Times & Trades)
    if aggr:
        r = aggr["ratio"]
        if abs(r) >= 0.1:
            side = "COMPRA" if r > 0 else "VENDA"
            add("agressao", side, W["agressao"] * min(1.0, abs(r) / 0.5), f"saldo agressão {r:+.0%}")
        else:
            add("agressao", "NEUTRO", 0, f"agressão equilibrada ({r:+.0%})")
    else:
        add("agressao", "NEUTRO", 0, "Times&Trades indisponível no feed", avail=False)

    # 7) Price action (continuação vs rejeição do movimento)
    if o and last and orh and orl:
        moved_up = last > o
        # rejeição: fez máxima acima do OR mas voltou pra dentro → contra
        rejected_up = today.get("cur_high", 0) > orh and last < orh
        rejected_dn = today.get("cur_low", 1e18) < orl and last > orl
        if rejected_up:
            add("price_action", "VENDA", W["price_action"], "falso rompimento de alta (rejeição)")
        elif rejected_dn:
            add("price_action", "COMPRA", W["price_action"], "falso rompimento de baixa (rejeição)")
        elif moved_up:
            add("price_action", "COMPRA", W["price_action"] * 0.6, "preço acima da abertura")
        else:
            add("price_action", "VENDA", W["price_action"] * 0.6, "preço abaixo da abertura")
    else:
        add("price_action", "NEUTRO", 0, "aguardando price action", avail=False)

    # 8) Contexto externo (WDO — correlação inversa com o índice)
    if wdo and wdo.get("dir") in ("ALTA", "BAIXA"):
        # dólar em alta pressiona o índice pra baixo (e vice-versa)
        side = "VENDA" if wdo["dir"] == "ALTA" else "COMPRA"
        add("externo", side, W["externo"] * 0.7, f"WDO {wdo['dir']} (inverso do índice)")
    else:
        add("externo", "NEUTRO", 0, "WDO sem direção clara", avail=bool(wdo))

    raw_buy = max(0.0, buy)
    raw_sell = max(0.0, sell)
    # NORMALIZAÇÃO: mede a força como % do peso DISPONÍVEL (exclui só os fatores
    # sem dado, ex.: agressão quando o feed não entrega). Assim o score não fica
    # eternamente travado por 15 pontos que nunca vêm — quando os fatores que
    # existem se alinham forte, chega perto de 100 normalmente.
    avail_weight = sum(WEIGHTS[k] for k, f in factors.items() if f["avail"])
    if avail_weight <= 0:
        avail_weight = 100.0
    scale = 100.0 / avail_weight
    buy = min(100.0, raw_buy * scale)
    sell = min(100.0, raw_sell * scale)
    dominant = "COMPRA" if buy > sell else ("VENDA" if sell > buy else "NEUTRO")
    strength = max(buy, sell)
    # sinal: precisa de dominância clara E força mínima de SETUP (70, já normalizado)
    if strength >= 70 and abs(buy - sell) >= 20:
        signal = "LONG" if dominant == "COMPRA" else "SHORT"
    else:
        signal = "WAIT"
    avail = sum(1 for f in factors.values() if f["avail"])
    return {
        "buy": round(buy, 1), "sell": round(sell, 1),
        "raw_buy": round(raw_buy, 1), "raw_sell": round(raw_sell, 1),
        "avail_weight": round(avail_weight),
        "dominant": dominant, "strength": round(strength, 1),
        "classe": classify(strength), "signal": signal,
        "factors": factors, "fatores_com_dado": avail, "fatores_total": len(factors),
    }


# ── Estado / snapshot / loop ───────────────────────────────────────────────
_state = {
    "enabled": os.getenv("OPENING_ENABLED", "1").strip() != "0",
    "last": None, "day": None, "status": "boot", "logged_today": False,
}
_started = False


def _daily_bias(mt5, symbol):
    """Viés dos últimos dias pelo fechamento vs EMA rápida (D1)."""
    try:
        rates = _rates_df(mt5, symbol, mt5.TIMEFRAME_D1, 15)
        if rates is None or len(rates) < 6:
            return "NEUTRO"
        import pandas as pd
        df = pd.DataFrame(rates)
        ema = df["close"].ewm(span=5, adjust=False).mean()
        last = float(df["close"].iloc[-1]); e = float(ema.iloc[-1])
        if last > e * 1.001: return "ALTA"
        if last < e * 0.999: return "BAIXA"
        return "NEUTRO"
    except Exception:
        return "NEUTRO"


def build_snapshot() -> dict:
    """Coleta tudo e devolve o snapshot atual do motor (usado pela API e pelo loop)."""
    sym = _win_symbol()
    now = _now()
    snap = {"ts": now.strftime("%Y-%m-%d %H:%M:%S"), "symbol": sym,
            "enabled": _state["enabled"], "window": None}
    nowmin = now.hour * 60 + now.minute
    try:
        mt5 = _mt5()
        if mt5.terminal_info() is None:
            snap["status"] = "SEM_MT5"
            snap["erro"] = "MT5 não conectado — abra o terminal da XP (conta B3)."
            return snap
        refs = _prev_session_refs(mt5, sym)
        today = _today_open_and_or(mt5, sym)
        # Agressão: PREFERE a ProfitDLL (profit_flow.py); cai pro MT5 se não houver.
        aggr = _aggression_from_profit() or _aggression(mt5, sym)
        wdo = _wdo_dir(mt5)
        bias = _daily_bias(mt5, sym)
        score = compute_score(refs, today, aggr, wdo, bias)
        snap.update({
            "refs": refs, "today": today, "aggr": aggr, "wdo": wdo,
            "daily_bias": bias, "score": score,
        })
        # Status pela ABERTURA REAL detectada (1º candle), não por relógio fixo.
        om = today.get("open_min")
        if om is None:
            snap["status"] = "AGUARDANDO"; snap["window"] = "antes da abertura (sem candle hoje)"
        elif nowmin < om + 20:
            snap["status"] = "ATIVO"
            snap["window"] = f"abertura {today.get('open_hhmm','?')} · janela de análise"
        else:
            snap["status"] = "ENCERRADO"
            snap["window"] = f"abertura {today.get('open_hhmm','?')} · candle de 15m fechado"
    except Exception as exc:
        logger.warning("opening build_snapshot: %s", exc)
        snap["status"] = "ERRO"; snap["erro"] = str(exc)
    return snap


def snapshot() -> dict:
    """Recalcula AO VIVO a cada chamada (a tela atualiza a cada 5s)."""
    _state["last"] = build_snapshot()
    return _state["last"]


def _log_day(snap):
    try:
        os.makedirs("logs", exist_ok=True)
        day = _now().strftime("%Y-%m-%d")
        path = os.path.join("logs", f"opening_engine_{day}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(snap, f, ensure_ascii=False, indent=2, default=str)
        _state["logged_today"] = True
        logger.info("opening_engine: snapshot do dia salvo em %s", path)
    except Exception as exc:
        logger.warning("opening log: %s", exc)


def _loop():
    logger.info("OpeningEngine iniciado (janela %02d:%02d–%02d:%02d, símbolo %s)",
                *WINDOW_START, *WINDOW_END, _win_symbol())
    while True:
        try:
            now = _now()
            if _state["day"] != now.date():
                _state["day"] = now.date()
                _state["logged_today"] = False
            nowmin = now.hour * 60 + now.minute
            # Loop ativo numa faixa ampla (cobre abertura 09:00 e 10:00). Fora dela,
            # dorme mais. A abertura REAL é detectada pelo 1º candle no snapshot.
            if not _state["enabled"] or nowmin < (8 * 60 + 30) or nowmin > (11 * 60 + 30):
                time.sleep(20)
                continue
            _state["last"] = build_snapshot()
            # ~15 min após a abertura detectada, grava o fechamento do candle p/ backtest
            om = (_state["last"].get("today") or {}).get("open_min")
            if (om is not None and nowmin >= om + 15
                    and not _state["logged_today"] and _state["last"].get("score")):
                _log_day(_state["last"])
        except Exception as exc:
            logger.warning("OpeningEngine loop: %s", exc)
        time.sleep(CHECK_SEC)


def set_enabled(on: bool):
    _state["enabled"] = bool(on)
    return _state["enabled"]


def start_opening_engine() -> None:
    """Chamar no boot da instância B3. Idempotente. Não roda no perfil crypto."""
    global _started
    if _started:
        return
    if os.getenv("MT5_PROFILE", "b3").strip().lower() == "crypto":
        return
    _started = True
    t = threading.Thread(target=_loop, daemon=True, name="OpeningEngine")
    t.start()


# ── Probe: descobre o que o MT5 do usuário realmente entrega ────────────────
def probe() -> dict:
    """Diagnóstico dos dados disponíveis no MT5 para WIN/WDO (rode 1x no pregão)."""
    out = {"ts": _now().strftime("%Y-%m-%d %H:%M:%S"), "win_symbol": _win_symbol(),
           "wdo_symbol": _wdo_symbol(), "checks": {}}
    try:
        mt5 = _mt5()
        if mt5.terminal_info() is None:
            out["erro"] = "MT5 não conectado"; return out
        sym = _win_symbol()
        c = out["checks"]
        r = _rates_df(mt5, sym, mt5.TIMEFRAME_M1, 10)
        c["candles_M1"] = {"ok": r is not None, "n": (len(r) if r is not None else 0)}
        tick = mt5.symbol_info_tick(sym)
        c["tick_bid_ask"] = {"ok": bool(tick), "bid": getattr(tick, "bid", None),
                             "ask": getattr(tick, "ask", None)}
        mt5.symbol_select(sym, True)
        _tk = mt5.symbol_info_tick(sym)
        _ep = int(_tk.time) if _tk else 0
        tk = None
        if _ep:
            tk = mt5.copy_ticks_range(
                sym, datetime.utcfromtimestamp(_ep - 120),
                datetime.utcfromtimestamp(_ep + 1), mt5.COPY_TICKS_ALL)
            if tk is None or len(tk) == 0:
                tk = mt5.copy_ticks_from(
                    sym, datetime.utcfromtimestamp(_ep - 360), 200000, mt5.COPY_TICKS_ALL)
        c["times_and_trades"] = {"ok": tk is not None and len(tk) > 0,
                                 "n_ticks": (len(tk) if tk is not None else 0),
                                 "server_time": _ep,
                                 "obs": "necessário p/ AGRESSÃO (fix hora servidor)"}
        # Market book (DOM)
        try:
            added = mt5.market_book_add(sym)
            book = mt5.market_book_get(sym) if added else None
            c["book_dom"] = {"ok": bool(book), "niveis": (len(book) if book else 0)}
            if added:
                mt5.market_book_release(sym)
        except Exception as be:
            c["book_dom"] = {"ok": False, "erro": str(be)}
        # WDO
        rw = _rates_df(mt5, _wdo_symbol(), mt5.TIMEFRAME_M1, 5)
        c["wdo_candles"] = {"ok": rw is not None, "n": (len(rw) if rw is not None else 0)}
    except Exception as exc:
        out["erro"] = str(exc)
    return out
