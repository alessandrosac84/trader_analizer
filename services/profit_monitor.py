"""
services/profit_monitor.py — MONITOR PROFIT (v6 score + FLUXO do Profit).

Evolução do Monitor MT5: mantém a ESSÊNCIA (score v6 + regras calibradas) e
sobrepõe o FLUXO real do Profit (agressão comprador × vendedor, e no futuro o
book), gerando um veredito "CONFIRMADO PELO FLUXO" — mais parrudo e assertivo
que o v6 puro.

ISOLADO: não importa nem altera V7, WinGo, Crypto ou o Monitor MT5 clássico.
Reaproveita só funções de LEITURA (generate_signal, monitor_mt5_rules) sem tocá-las.

EXECUÇÃO: por ora NÃO opera. A trava de simulador (bloqueio triplo) já está
codificada e DESLIGADA — nunca roteia em conta real.
"""
from __future__ import annotations

import json
import logging
import os
import time

logger = logging.getLogger(__name__)

# Confirmação de fluxo: |ratio| acima disso confirma/diverge do sinal v6.
FLOW_CONFIRM = 0.15
FLOW_STRONG = 0.35
FLOW_MAX_AGE = 15.0     # s — dado de agressão mais velho que isso é ignorado


def _win_symbol():
    return os.getenv("WIN_MT5_SYMBOL", "WINV26").strip()


def _wdo_symbol():
    return os.getenv("WDO_MT5_SYMBOL", "WDOU26").strip()


# ── FLUXO do Profit (lido do profit_flow.py, sem carregar a DLL aqui) ───────
def get_flow():
    """Saldo de agressão gravado pelo profit_flow.py. None se ausente/velho."""
    try:
        path = os.path.join("data", "profit_aggression.json")
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        if time.time() - float(d.get("ts", 0)) > FLOW_MAX_AGE:
            return {**d, "stale": True}
        tot = float(d.get("buy", 0)) + float(d.get("sell", 0))
        d["ratio"] = (float(d.get("buy", 0)) - float(d.get("sell", 0))) / tot if tot > 0 else 0.0
        d["stale"] = False
        return d
    except Exception as exc:
        logger.debug("get_flow: %s", exc)
        return None


# ── MT5 candles (mesma conta B3 das outras instâncias) ─────────────────────
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


def _candles(mt5, symbol, tf=None, n=400):
    tf = tf or mt5.TIMEFRAME_M15
    r = mt5.copy_rates_from_pos(symbol, tf, 0, n)
    if r is None or len(r) == 0:
        return None
    import pandas as pd
    df = pd.DataFrame(r)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df = df.set_index("time")
    vol = df["real_volume"].where(df["real_volume"] > 0, df["tick_volume"])
    return pd.DataFrame({"Open": df["open"], "High": df["high"], "Low": df["low"],
                         "Close": df["close"], "Volume": vol}, index=df.index)


def get_candles(asset="WIN", tf=15, n=200):
    """Candles p/ o gráfico (formato LightweightCharts: time em segundos + OHLC)."""
    try:
        mt5 = _mt5()
        if mt5.terminal_info() is None:
            return {"ok": False, "error": "MT5 não conectado"}
        sym = _win_symbol() if asset.upper() != "WDO" else _wdo_symbol()
        tfmap = {1: mt5.TIMEFRAME_M1, 5: mt5.TIMEFRAME_M5, 15: mt5.TIMEFRAME_M15,
                 30: mt5.TIMEFRAME_M30, 60: mt5.TIMEFRAME_H1}
        r = mt5.copy_rates_from_pos(sym, tfmap.get(int(tf), mt5.TIMEFRAME_M15), 0, int(n))
        if r is None or len(r) == 0:
            return {"ok": False, "error": "sem candles"}
        candles = [{"time": int(x["time"]), "open": float(x["open"]), "high": float(x["high"]),
                    "low": float(x["low"]), "close": float(x["close"])} for x in r]
        return {"ok": True, "asset": asset.upper(), "symbol": sym, "tf": int(tf), "candles": candles}
    except Exception as exc:
        logger.warning("get_candles: %s", exc)
        return {"ok": False, "error": str(exc)}


def _v6_signal(mt5, symbol):
    """Sinal v6 (score) + gate calibrado do Monitor — só LEITURA das funções existentes."""
    try:
        from services.technical_analysis import generate_signal
        from services.monitor_mt5_rules import (score_allowed, session_allows,
                                                 filters_allowed, rules_for)
        df = _candles(mt5, symbol)
        if df is None or len(df) < 60:
            return {"ok": False, "motivo": "sem candles"}
        h1 = df.resample("1h").agg({"Open": "first", "High": "max", "Low": "min",
                                    "Close": "last", "Volume": "sum"}).dropna()
        sig = generate_signal(df, htf_df=(h1 if len(h1) >= 26 else None)) or {}
        acao = sig.get("acao", "NEUTRO")
        score = sig.get("score", 0)
        # gate calibrado (mesmo do trade_executor / monitor_mt5_rules)
        gates = []
        ok_sc, m1 = score_allowed(symbol, score); gates.append(("score", ok_sc, m1))
        ok_se, m2 = session_allows(symbol); gates.append(("sessao", ok_se, m2))
        rules = rules_for(symbol)
        passa = ok_sc and ok_se and acao in ("COMPRA", "VENDA")
        return {"ok": True, "acao": acao, "score": score,
                "entrada": sig.get("entrada"), "stop": sig.get("stop"),
                "tp1": sig.get("tp1"), "forca": sig.get("forca"),
                "gate_passa": passa, "gates": gates, "regras": rules,
                "sinais": sig.get("sinais", [])[:6]}
    except Exception as exc:
        logger.warning("v6_signal %s: %s", symbol, exc)
        return {"ok": False, "motivo": str(exc)}


def _flow_verdict(acao, flow):
    """Cruza o sinal v6 com o fluxo de agressão do Profit."""
    if not flow or flow.get("stale"):
        return {"estado": "SEM_FLUXO", "nota": "fluxo do Profit indisponível (ligue o ProfitFlow)",
                "boost": 0}
    ratio = float(flow.get("ratio", 0))
    lado_fluxo = "COMPRA" if ratio > 0 else ("VENDA" if ratio < 0 else "NEUTRO")
    if acao not in ("COMPRA", "VENDA"):
        return {"estado": "NEUTRO", "nota": f"fluxo {ratio:+.0%} ({lado_fluxo})", "boost": 0}
    alinhado = (acao == lado_fluxo)
    forte = abs(ratio) >= FLOW_STRONG
    confirma = abs(ratio) >= FLOW_CONFIRM
    if alinhado and forte:
        return {"estado": "CONFIRMADO_FORTE", "nota": f"fluxo {ratio:+.0%} a favor (forte)", "boost": +2}
    if alinhado and confirma:
        return {"estado": "CONFIRMADO", "nota": f"fluxo {ratio:+.0%} a favor", "boost": +1}
    if (not alinhado) and forte:
        return {"estado": "DIVERGENTE_FORTE", "nota": f"fluxo {ratio:+.0%} CONTRA — cautela/veto", "boost": -2}
    if not alinhado and confirma:
        return {"estado": "DIVERGENTE", "nota": f"fluxo {ratio:+.0%} contra", "boost": -1}
    return {"estado": "NEUTRO", "nota": f"fluxo {ratio:+.0%} (indefinido)", "boost": 0}


# ── Trava de SIMULADOR — BLOQUEIO TRIPLO (execução ainda DESLIGADA) ─────────
def simulator_gate():
    """Bloqueio triplo para roteamento no SIMULADOR. Enquanto a execução não é
    ligada, sempre retorna allowed=False. Nunca permite conta real.

    Regras (todas obrigatórias):
      1. PROFIT_TRADING_ENABLED=1  (flag mestre; default 0)
      2. PROFIT_SIM_ACCOUNT preenchido E nome/tipo reconhecido como SIMULADOR
      3. PROFIT_SIM_CONFIRM=EU_CONFIRMO_SIMULADOR  (confirmação explícita)
    """
    reasons = []
    enabled = os.getenv("PROFIT_TRADING_ENABLED", "0").strip() == "1"
    if not enabled:
        reasons.append("trading desligado (PROFIT_TRADING_ENABLED != 1)")
    sim_acc = os.getenv("PROFIT_SIM_ACCOUNT", "").strip()
    if not sim_acc:
        reasons.append("conta simulador não definida (PROFIT_SIM_ACCOUNT vazio)")
    # heurística anti-conta-real: exige marca de simulador no identificador
    if sim_acc and not any(k in sim_acc.upper() for k in ("SIM", "SIMUL", "DEMO", "PAPER")):
        reasons.append("PROFIT_SIM_ACCOUNT não parece simulador (sem SIM/DEMO no nome)")
    confirm = os.getenv("PROFIT_SIM_CONFIRM", "").strip()
    if confirm != "EU_CONFIRMO_SIMULADOR":
        reasons.append("confirmação ausente (PROFIT_SIM_CONFIRM)")
    allowed = len(reasons) == 0
    return {"allowed": allowed, "conta": sim_acc or "—",
            "motivos_bloqueio": reasons,
            "estado": "PRONTO (SIMULADOR)" if allowed else "DESLIGADO / BLOQUEADO"}


# ── Snapshot para a tela ────────────────────────────────────────────────────
def snapshot():
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone(timedelta(hours=-3)))
    out = {"ts": now.strftime("%Y-%m-%d %H:%M:%S"), "sim": simulator_gate(),
           "flow": get_flow(), "ativos": {}}
    try:
        mt5 = _mt5()
        if mt5.terminal_info() is None:
            out["status"] = "SEM_MT5"
            out["erro"] = "MT5 não conectado (abra o terminal da XP)."
            return out
        out["status"] = "OK"
        flow = out["flow"]
        for label, sym in (("WIN", _win_symbol()), ("WDO", _wdo_symbol())):
            v6 = _v6_signal(mt5, sym)
            verd = _flow_verdict(v6.get("acao", "NEUTRO"), flow) if v6.get("ok") else {"estado": "—", "nota": "—", "boost": 0}
            out["ativos"][label] = {"symbol": sym, "v6": v6, "fluxo": verd}
    except Exception as exc:
        logger.warning("profit_monitor snapshot: %s", exc)
        out["status"] = "ERRO"; out["erro"] = str(exc)
    return out
