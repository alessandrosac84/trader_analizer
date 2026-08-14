"""
services/profit_bridge.py
--------------------------
Profit Bridge — Sprint Profit 1

Lê dados da plataforma Trade AI e gera arquivo JSON padronizado para
consumo futuro pelos indicadores do Profit Chart.

Arquitetura:
  Trade AI (DB + servicos) → Profit Bridge → storage/profit/trade_ai_profit.json

REGRAS FUNDAMENTAIS:
  - Apenas LE dados. Nunca escreve no DB. Nunca executa ordens.
  - Nunca altera Monitor MT5, Scalper ou qualquer modulo existente.
  - Todo output vai para storage/profit/ e logs/profit_bridge.log.
"""
import json
import logging
import logging.handlers
import os
import threading
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

_FLASK_PORT = int(os.getenv("PORT", "5000"))
_FLASK_BASE = f"http://127.0.0.1:{_FLASK_PORT}"

_BRT          = timezone(timedelta(hours=-3))
_BASE_DIR     = os.path.dirname(os.path.dirname(__file__))
_STORAGE_DIR  = os.path.join(_BASE_DIR, "storage", "profit")
_JSON_PATH    = os.path.join(_STORAGE_DIR, "trade_ai_profit.json")
_LOG_PATH     = os.path.join(_BASE_DIR, "logs", "profit_bridge.log")

# Frequencia de atualizacao do JSON (segundos)
PROFIT_BRIDGE_INTERVAL = int(os.environ.get("PROFIT_BRIDGE_INTERVAL", "1"))

# Cache de sinal tecnico: evita chamar generate_signal() mais que 1x/5s
_SIGNAL_CACHE_TTL = 5   # segundos

# ---------------------------------------------------------------------------
# Status global — consumido pelo health check /api/profit/status
# ---------------------------------------------------------------------------
_status = {
    "running":      False,
    "last_update":  None,
    "json_exists":  False,
    "json_size":    0,
    "error":        None,
    "updates_ok":   0,
    "updates_fail": 0,
    "started_at":   None,
}
_status_lock   = threading.Lock()
_bridge_thread = None
_bridge_stop   = threading.Event()

# Cache de sinal por instrumento
_sig_cache = {}    # "WIN" | "WDO" -> {"ts": float, "data": dict}
_sig_lock  = threading.Lock()

# Mapa instrumento -> símbolo TradingView
_TV_SYMBOLS = {
    "WIN": "BMFBOVESPA:WIN1!",
    "WDO": "BMFBOVESPA:WDO1!",
}


# ---------------------------------------------------------------------------
# Logger dedicado
# ---------------------------------------------------------------------------

def _build_bridge_logger() -> logging.Logger:
    os.makedirs(os.path.dirname(_LOG_PATH), exist_ok=True)
    log = logging.getLogger("profit_bridge")
    if not log.handlers:
        h = logging.handlers.RotatingFileHandler(
            _LOG_PATH, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        h.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        ))
        log.addHandler(h)
        log.setLevel(logging.INFO)
        log.propagate = False   # nao polui o log principal do Flask
    return log


_blog = _build_bridge_logger()


# ---------------------------------------------------------------------------
# Leitura de dados — camada 1: DB direto (ultra-rapido)
# ---------------------------------------------------------------------------

def _db_conn():
    from services.db import _conn as _c
    return _c()


def _get_latest_mt5_signal(symbol_fragment: str) -> dict:
    """
    Lê a linha mais recente de mt5_signals para WIN ou WDO.
    Retorna dict com: acao, score, preco, entrada, stop, tp1, tp2, vwap, created_at
    """
    try:
        with _db_conn() as conn:
            row = conn.execute("""
                SELECT acao, score, preco, entrada, stop, tp1, tp2, vwap, created_at
                FROM mt5_signals
                WHERE UPPER(tv_symbol) LIKE ?
                ORDER BY id DESC
                LIMIT 1
            """, (f"%{symbol_fragment.upper()}%",)).fetchone()
        return dict(row) if row else {}
    except Exception as exc:
        _blog.debug("_get_latest_mt5_signal(%s): %s", symbol_fragment, exc)
        return {}


# ---------------------------------------------------------------------------
# Leitura de dados — camada 2: sinal tecnico completo (com cache 5s)
# ---------------------------------------------------------------------------

def _get_signal_from_flask(instrument: str) -> dict:
    """
    Chama GET /api/monitor/signals no Flask local.
    Retorna os mesmos dados que o Monitor MT5 exibe (MT5 real ou Yahoo Finance fallback).
    Timeout de 2s para nao travar a bridge.
    """
    tv_sym = _TV_SYMBOLS.get(instrument, f"BMFBOVESPA:{instrument}1!")
    url = f"{_FLASK_BASE}/api/monitor/signals?tv_symbol={tv_sym}&interval=15"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=2) as resp:
            data = json.loads(resp.read().decode())
        if data.get("ok"):
            return {"ok": True, **data}
        return {"ok": False}
    except Exception as exc:
        _blog.debug("_get_signal_from_flask(%s): %s", instrument, exc)
        return {"ok": False}


def _get_signal_cached(instrument: str) -> dict:
    """
    Retorna sinal tecnico completo para WIN ou WDO.
    Prioridade: Flask local (Monitor MT5, dados MT5 reais) > get_current_signal (Yahoo Finance fallback).
    Cache de 5 segundos.
    """
    now = time.monotonic()
    with _sig_lock:
        entry = _sig_cache.get(instrument)
        if entry and (now - entry["ts"]) < _SIGNAL_CACHE_TTL:
            return entry["data"]

    # 1a tentativa: Flask local (mesmos dados do Monitor MT5)
    sig = _get_signal_from_flask(instrument)

    # Fallback: computa direto se Flask estiver fora do ar
    if not sig.get("ok"):
        tv_sym = _TV_SYMBOLS.get(instrument, f"BMFBOVESPA:{instrument}1!")
        try:
            from services.telemetry_service import get_current_signal
            sig = get_current_signal(tv_sym, "15")
        except Exception as exc:
            _blog.debug("get_current_signal(%s): %s", instrument, exc)
            sig = {"ok": False}

    with _sig_lock:
        _sig_cache[instrument] = {"ts": now, "data": sig}

    return sig


# ---------------------------------------------------------------------------
# Montagem dos blocos do JSON
# ---------------------------------------------------------------------------

def _build_instrument_block(instrument: str) -> dict:
    """
    Monta o bloco WIN ou WDO do payload JSON.
    Combina dados rapidos do DB com enriquecimento do sinal tecnico.
    """
    # --- Dados basicos do DB (rapido) ---
    db = _get_latest_mt5_signal(instrument)

    # --- Sinal tecnico completo (cache 5s) ---
    sig  = _get_signal_cached(instrument)
    ok   = sig.get("ok", False)

    # --- Enriquecimento via telemetry helpers ---
    breakdown       = {}
    confluences     = []
    market_regime   = ""
    signal_strength = 0
    risk_level      = 0

    if ok:
        try:
            from services.telemetry_service import (
                compute_score_breakdown,
                compute_market_regime,
                compute_signal_strength,
                compute_risk_level,
                build_clean_confluences,
            )
            breakdown       = compute_score_breakdown(sig)
            market_regime   = compute_market_regime(sig)
            signal_strength = compute_signal_strength(sig)
            risk_level      = compute_risk_level(sig)
            confluences     = build_clean_confluences(sig)
        except Exception as exc:
            _blog.debug("telemetry helpers(%s): %s", instrument, exc)

    # --- Posicao aberta ---
    position_open = False
    position_dir  = None
    position_pnl  = 0.0
    try:
        from services.telemetry_service import get_open_position
        pos = get_open_position(_TV_SYMBOLS.get(instrument, ""))
        if pos.get("found"):
            position_open = True
            position_dir  = pos.get("acao")
            position_pnl  = pos.get("pnl_brl") or 0.0
    except Exception as exc:
        _blog.debug("get_open_position(%s): %s", instrument, exc)

    # --- Campos derivados ---
    # Prioridade: sinal real-time (sig, ok=True) > registro DB (db, pode estar desatualizado)
    if ok:
        action = sig.get("acao") or db.get("acao") or "NEUTRO"
        score  = sig.get("score") if sig.get("score") is not None else (db.get("score") or 0)
    else:
        action = db.get("acao") or "NEUTRO"
        score  = db.get("score") or 0
    htf    = sig.get("htf_trend")
    trend  = htf or ("alta" if action == "COMPRA" else "baixa" if action == "VENDA" else "neutro")

    vwap_price = db.get("vwap") or sig.get("vwap")
    price      = db.get("preco") or sig.get("preco")
    vwap_bull  = bool(price > vwap_price) if (price and vwap_price) else None

    ema_alignment = (breakdown.get("ema_alignment", 0) > 0) if breakdown else None
    macd_positive = (breakdown.get("macd", 0) > 0)          if breakdown else None

    # Macro (HTF) / Micro (momentum curto no TF do gráfico)
    macro = (htf or "neutro").lower() if htf else "neutro"
    if macro not in ("alta", "baixa"):
        macro = "neutro"
    rsi_v = sig.get("rsi") if ok else None
    ema9 = sig.get("ema9")
    ema21 = sig.get("ema21")
    micro = "neutro"
    if ok and ema9 is not None and ema21 is not None and price is not None:
        try:
            e9, e21, px = float(ema9), float(ema21), float(price)
            if px > e9 > e21:
                micro = "alta"
            elif px < e9 < e21:
                micro = "baixa"
            elif rsi_v is not None:
                # desempate IFR
                if float(rsi_v) >= 55:
                    micro = "alta"
                elif float(rsi_v) <= 45:
                    micro = "baixa"
        except (TypeError, ValueError):
            micro = "neutro"
    elif ok and rsi_v is not None:
        try:
            if float(rsi_v) >= 55:
                micro = "alta"
            elif float(rsi_v) <= 45:
                micro = "baixa"
        except (TypeError, ValueError):
            pass
    mm_aligned = macro != "neutro" and micro != "neutro" and macro == micro
    mm_with_signal = False
    if action == "COMPRA":
        mm_with_signal = macro == "alta" and micro == "alta"
    elif action == "VENDA":
        mm_with_signal = macro == "baixa" and micro == "baixa"

    # Gate calibrado Monitor MT5 (mesmas regras do robô)
    gate_ok, gate_reasons = True, []
    try:
        from services.monitor_mt5_rules import (
            session_allows, score_allowed, filters_allowed, rules_for,
        )
        cfg = rules_for(instrument)
        ok_sess, why_s = session_allows(instrument)
        if not ok_sess:
            gate_ok = False
            gate_reasons.append(why_s or "fora da janela")
        ok_sc, why_sc = score_allowed(instrument, score)
        if not ok_sc:
            gate_ok = False
            gate_reasons.append(why_sc or "score")
        ok_f, why_f = filters_allowed(
            instrument,
            adx=float(sig["adx"]) if ok and sig.get("adx") is not None else None,
            vol_ratio=float(sig["vol_ratio"]) if ok and sig.get("vol_ratio") is not None else None,
        )
        if not ok_f:
            gate_ok = False
            gate_reasons.append(why_f or "filtros")
        if action not in ("COMPRA", "VENDA"):
            gate_ok = False
            if "NEUTRO" not in " ".join(gate_reasons):
                gate_reasons.append("sem direção COMPRA/VENDA")
        min_sc = int(cfg.get("min_score") or 0)
    except Exception as exc:
        _blog.debug("monitor gate(%s): %s", instrument, exc)
        gate_ok, gate_reasons, min_sc = False, ["gate indisponível"], 0

    # Veredito HUD: COMPRA / VENDA / AGUARDAR / BLOQUEADO
    if action in ("COMPRA", "VENDA") and gate_ok and mm_with_signal:
        verdict = action
        verdict_label = f"{action} · ALINHADO"
    elif action in ("COMPRA", "VENDA") and gate_ok:
        verdict = action
        verdict_label = f"{action} · GATE OK (macro/micro parcial)"
    elif action in ("COMPRA", "VENDA"):
        verdict = "BLOQUEADO"
        verdict_label = " · ".join(gate_reasons[:2]) or "filtros"
    else:
        verdict = "AGUARDAR"
        verdict_label = "sem setup"

    return {
        # Campos minimos da spec
        "action":           action,
        "score":            score,
        "signal_strength":  signal_strength,
        "risk_level":       risk_level,
        "market_regime":    market_regime,
        "entry_price":      db.get("entrada") or sig.get("entrada"),
        "sl":               db.get("stop")    or sig.get("stop"),
        "tp1":              db.get("tp1")     or sig.get("tp1"),
        "tp2":              db.get("tp2")     or sig.get("tp2"),
        "trend":            trend,
        "confluence_count": len(confluences),
        # Enriquecimento
        "score_breakdown":  breakdown,
        "confluences":      confluences,
        "vwap_bull":        vwap_bull,
        "ema_alignment":    ema_alignment,
        "macd_positive":    macd_positive,
        "rsi":              sig.get("rsi")   if ok else None,
        "adx":              sig.get("adx")   if ok else None,
        "atr":              sig.get("atr")   if ok else None,
        "vol_ratio":        sig.get("vol_ratio") if ok else None,
        "ema9":             sig.get("ema9") if ok else None,
        "ema21":            sig.get("ema21") if ok else None,
        "price":            price,
        "position_open":    position_open,
        "position_dir":     position_dir,
        "position_pnl":     position_pnl,
        "last_signal_at":   db.get("created_at"),
        # HUD v2
        "macro":            macro,
        "micro":            micro,
        "mm_aligned":       mm_aligned,
        "mm_with_signal":   mm_with_signal,
        "gate_ok":          gate_ok,
        "gate_reasons":     gate_reasons,
        "min_score":        min_sc,
        "verdict":          verdict,
        "verdict_label":    verdict_label,
    }


def _build_engine_block() -> dict:
    """
    Monta o bloco engine com estado do motor de mercado.
    """
    try:
        from services.market_engine import get_engine_state
        state = get_engine_state()

        # Instrumento recomendado: primeiro da lista com prioridade alta/media e dados
        rec = ""
        for p in state.get("priority_ranking", []):
            if p.get("priority") in ("alta", "media") and p.get("total_trades", 0) >= 3:
                rec = p.get("instrument", "")
                break

        return {
            "can_trade":              state.get("trading_allowed", True),
            "daily_stop_hit":         state.get("drawdown_status") == "STOP_DIARIO",
            "current_pnl":            state.get("today_pnl_brl", 0.0),
            "recommended_instrument": rec,
            "drawdown_status":        state.get("drawdown_status", "NORMAL"),
            "daily_stop_brl":         state.get("daily_stop_brl", -500.0),
        }
    except Exception as exc:
        _blog.debug("_build_engine_block: %s", exc)
        return {
            "can_trade":              True,
            "daily_stop_hit":         False,
            "current_pnl":            0.0,
            "recommended_instrument": "",
            "drawdown_status":        "NORMAL",
            "daily_stop_brl":        -500.0,
        }


def _build_recent_trades_block(limit: int = 5) -> list:
    """
    Retorna os ultimos trades registrados em auto_trades.
    Apenas LEITURA — nunca escreve nem altera a tabela.
    """
    try:
        with _db_conn() as conn:
            rows = conn.execute("""
                SELECT
                    id, opened_at, tv_symbol, acao,
                    entry_price, sl_initial, tp1_initial,
                    score, ai_veredito,
                    closed_at, exit_price, close_reason,
                    pnl_pts, pnl_brl, volume
                FROM auto_trades
                ORDER BY id DESC
                LIMIT ?
            """, (limit,)).fetchall()

        result = []
        for row in rows:
            (tid, opened_at, tv_symbol, acao,
             entry_price, sl_initial, tp1_initial,
             score, ai_veredito,
             closed_at, exit_price, close_reason,
             pnl_pts, pnl_brl, volume) = row

            # Instrumento legivel (WIN / WDO)
            sym = str(tv_symbol).upper()
            if "WIN" in sym:
                instrument = "WIN"
            elif "WDO" in sym:
                instrument = "WDO"
            else:
                instrument = sym[:6]

            # Status aberto ou fechado
            is_open = closed_at is None

            # R/R ratio
            rr = None
            try:
                if entry_price and sl_initial and tp1_initial:
                    risk   = abs(entry_price - sl_initial)
                    reward = abs(tp1_initial  - entry_price)
                    rr = round(reward / risk, 2) if risk > 0 else None
            except Exception:
                pass

            result.append({
                "id":           tid,
                "opened_at":    opened_at,
                "instrument":   instrument,
                "acao":         str(acao).upper(),
                "entry_price":  entry_price,
                "sl":           sl_initial,
                "tp1":          tp1_initial,
                "volume":       volume,
                "score":        score,
                "ai_veredito":  ai_veredito,
                "is_open":      is_open,
                "closed_at":    closed_at,
                "exit_price":   exit_price,
                "close_reason": close_reason,
                "pnl_pts":      pnl_pts,
                "pnl_brl":      pnl_brl,
                "rr_ratio":     rr,
            })

        return result

    except Exception as exc:
        _blog.debug("_build_recent_trades_block: %s", exc)
        return []


def _build_payload() -> dict:
    """Monta o payload JSON completo."""
    return {
        "generated_at": datetime.now(_BRT).isoformat(),
        "win":    _build_instrument_block("WIN"),
        "wdo":    _build_instrument_block("WDO"),
        "engine": _build_engine_block(),
        "trades": _build_recent_trades_block(5),
    }


# ---------------------------------------------------------------------------
# Escrita atomica do JSON
# ---------------------------------------------------------------------------

def _write_json(payload: dict) -> int:
    """
    Escreve o payload no arquivo JSON de forma atomica (tmp + replace).
    Retorna o tamanho em bytes.
    """
    os.makedirs(_STORAGE_DIR, exist_ok=True)
    content = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    tmp_path = _JSON_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(content)
    os.replace(tmp_path, _JSON_PATH)   # operacao atomica no mesmo filesystem
    return len(content.encode("utf-8"))

# ---------------------------------------------------------------------------
# Loop principal da bridge
# ---------------------------------------------------------------------------

def _bridge_loop() -> None:
    """Thread principal: gera payload, escreve JSON e atualiza status."""
    _blog.info(
        "=== Profit Bridge iniciado | interval=%ds | output=%s ===",
        PROFIT_BRIDGE_INTERVAL, _JSON_PATH
    )

    with _status_lock:
        _status["running"]    = True
        _status["started_at"] = datetime.now(_BRT).isoformat()

    while not _bridge_stop.is_set():
        t0 = time.monotonic()
        try:
            payload = _build_payload()
            size    = _write_json(payload)
            elapsed = round((time.monotonic() - t0) * 1000, 1)

            with _status_lock:
                _status["last_update"] = payload["generated_at"]
                _status["json_exists"] = True
                _status["json_size"]   = size
                _status["error"]       = None
                _status["updates_ok"] += 1

            _blog.debug("Atualizado: %d bytes em %.1f ms.",
                elapsed, size)

        except Exception as exc:
            elapsed = round((time.monotonic() - t0) * 1000, 1)
            _blog.error("Falha na atualizacao (%.1fms): %s", elapsed, exc)
            with _status_lock:
                _status["error"] = str(exc)

        _bridge_stop.wait(timeout=PROFIT_BRIDGE_INTERVAL)

    with _status_lock:
        _status["running"] = False
    _blog.info("Profit Bridge encerrado.")


# ---------------------------------------------------------------------------
# API publica — start / stop / status
# ---------------------------------------------------------------------------

_bridge_thread: "threading.Thread | None" = None

def start_bridge() -> None:
    """Inicia a thread da bridge (idempotente)."""
    global _bridge_thread
    if _bridge_thread and _bridge_thread.is_alive():
        return
    _bridge_stop.clear()
    _bridge_thread = threading.Thread(
        target=_bridge_loop, name="profit-bridge", daemon=True
    )
    _bridge_thread.start()
    _blog.info("Thread profit-bridge iniciada.")


def stop_bridge() -> None:
    """Sinaliza parada e aguarda ate 5s."""
    _bridge_stop.set()
    if _bridge_thread:
        _bridge_thread.join(timeout=5)


def get_status() -> dict:
    with _status_lock:
        return dict(_status)


def get_json_path() -> str:
    """Caminho absoluto do trade_ai_profit.json (API / dashboard)."""
    return _JSON_PATH


if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    print("Iniciando Profit Bridge standalone...")
    start_bridge()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nEncerrando...")
        stop_bridge()
