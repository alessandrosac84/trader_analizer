"""
blueprints/crypto_bp.py — MONITOR CRYPTO (MT5 24h).

⚠️ MÓDULO NOVO E INDEPENDENTE. Endpoints /api/crypto/*. Não importa nem altera
o Monitor MT5 / Scalper. Usa serviços próprios (crypto_service / crypto_analysis
/ crypto_config) e MAGIC exclusivo.

Auto-trade dirigido pelo cliente (igual ao Monitor): o painel chama /auto-check
por ativo; o servidor aplica as travas e executa.
"""
import os
import csv
import time
import logging
from datetime import datetime, timezone, timedelta
from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)
crypto_bp = Blueprint("crypto", __name__)

_BRT = timezone(timedelta(hours=-3))
_LOGDIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
_CSV = os.path.join(_LOGDIR, "crypto_trades.csv")
# IMPORTANTE: campos de IA ficam no FIM. Assim, se o arquivo tiver um cabeçalho
# antigo (sem IA), as colunas centrais (profit/resultado) continuam alinhadas.
_COLS = ["id", "datetime_brt", "symbol", "direcao", "entry_price", "sl", "tp1",
         "volume", "score", "exit_price", "profit", "exit_reason", "resultado",
         "ai_veredito", "ai_confidence", "ai_motivo"]

COOLDOWN_SEC = 300   # 5 min entre trades por ativo (anti-overtrading)

# Estado em memória (por processo)
_auto = {}       # symbol -> {"enabled": bool, "last_ts": float}
_pending = {}    # symbol -> dados da entrada (para registrar no fechamento)
_session = {"trades": 0, "wins": 0, "losses": 0, "breakevens": 0, "pnl": 0.0,
            "gerados": 0, "executados": 0, "bloqueados": 0}
# Última decisão da IA por ativo (para exibir motivo no painel)
_last_ai = {}    # symbol -> {"veredito","confianca","motivo","ts"}
_last_close = {} # symbol -> ts do último fechamento registrado (dedup)

# Dedup AUTORITATIVO por ticket do deal de SAÍDA. Persistido em arquivo para
# sobreviver a restart (senão a reconciliação re-registraria trades antigos).
_DEALS_FILE = os.path.join(_LOGDIR, "crypto_logged_deals.txt")
# Marker de RESET: quando existe, a próxima reconciliação marca TODO o histórico
# atual como já-conhecido (sem logar) → começa a base limpa a partir de agora.
_SEED_FLAG = os.path.join(_LOGDIR, "crypto_seed_pending.flag")

def _load_logged_deals():
    s = set()
    try:
        if os.path.exists(_DEALS_FILE):
            with open(_DEALS_FILE, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.isdigit():
                        s.add(int(line))
    except Exception:
        pass
    return s

_logged_deals = _load_logged_deals()

def _mark_deal_logged(ticket):
    try:
        t = int(ticket)
    except Exception:
        return
    if t in _logged_deals:
        return
    _logged_deals.add(t)
    try:
        os.makedirs(_LOGDIR, exist_ok=True)
        with open(_DEALS_FILE, "a", encoding="utf-8") as f:
            f.write(f"{t}\n")
    except Exception:
        pass

_last_reconcile = 0.0   # throttle da reconciliação (só na tela Crypto)

# Cache do monitor MACRO/MICRO (recalc no máx. a cada _MM_TTL s → não pesa o MT5).
_MM_TTL = 12
_mm_cache = {}   # symbol -> {"ts", "data", "prev_micro"}

# Trava de execução por ativo — impede ordens simultâneas (corrida do auto-check)
import threading as _threading
_exec_locks = {}
def _lock_for(sym):
    return _exec_locks.setdefault(sym.upper(), _threading.Lock())


def _now():
    return datetime.now(_BRT)


def _is_weekend():
    return _now().weekday() >= 5


def _autost(sym):
    return _auto.setdefault(sym.upper(), {"enabled": False, "last_ts": 0.0, "volume": None})


# ── CSV log ──────────────────────────────────────────────────────────────────

def _next_id():
    if not os.path.exists(_CSV):
        return 1
    try:
        with open(_CSV, encoding="utf-8") as f:
            return sum(1 for _ in f)   # header conta como +0 na prática
    except Exception:
        return 1


def _log_entry(symbol, direcao, result, score, ai=None):
    ai = ai or {}
    _pending[symbol.upper()] = {
        "id": _next_id(),
        "datetime_brt": _now().strftime("%Y-%m-%d %H:%M:%S"),
        "symbol": symbol.upper(), "direcao": direcao,
        "entry_price": result.get("price"), "sl": result.get("sl"),
        "tp1": result.get("tp"), "volume": result.get("volume"), "score": score,
        "ai_veredito": ai.get("veredito", ""), "ai_confidence": ai.get("confianca", ""),
        "ai_motivo": (ai.get("motivo", "") or "")[:200],
    }


def _log_exit(symbol, exit_price, profit, reason, entry=None):
    os.makedirs(_LOGDIR, exist_ok=True)
    # usa a entrada pendente (bot) OU a fornecida (reconciliação do histórico) OU um esqueleto
    p = _pending.pop(symbol.upper(), None) or entry or {
        "id": _next_id(), "datetime_brt": _now().strftime("%Y-%m-%d %H:%M:%S"),
        "symbol": symbol.upper(), "direcao": "?", "entry_price": "", "sl": "",
        "tp1": "", "volume": "", "score": "",
    }
    res = "WIN" if profit > 0.01 else ("LOSS" if profit < -0.01 else "BE")
    p.update({"exit_price": exit_price, "profit": round(profit, 2),
              "exit_reason": reason, "resultado": res})
    new = not os.path.exists(_CSV)
    with open(_CSV, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=_COLS, extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerow(p)
    # sessão
    _session["trades"] += 1
    _session["pnl"] += profit
    if res == "WIN": _session["wins"] += 1
    elif res == "LOSS": _session["losses"] += 1
    else: _session["breakevens"] += 1


def _reconcile_closes():
    """Backstop AUTORITATIVO: varre o histórico MT5 e registra QUALQUER fechamento
    (SL/TP no broker) que ainda não esteja no CSV — mesmo que o navegador tenha
    perdido o evento (recarregou a página, estava em outra tela, etc.).

    Dedup por ticket do deal de saída (persistido). Na 1ª passada, 'casa' os trades
    já registrados pelo cliente (por símbolo + profit + dia) para NÃO duplicá-los.
    Só registra deals com o MAGIC do Monitor Crypto — trades manuais no terminal
    (outro magic) são ignorados de propósito."""
    try:
        from services.crypto_service import recent_closed_trades
    except Exception:
        return 0
    trades = recent_closed_trades(48)
    if not trades:
        return 0
    # RESET pendente: marca TODO o histórico atual como já-conhecido (não loga nada)
    # e sai. A partir daí, só fechamentos NOVOS entram na base — base limpa de verdade.
    if os.path.exists(_SEED_FLAG):
        for t in trades:
            tk = t.get("out_ticket")
            if tk is not None:
                _mark_deal_logged(tk)
        try:
            os.remove(_SEED_FLAG)
        except Exception:
            pass
        logger.info("crypto reconcile: RESET — %d deals marcados como conhecidos", len(trades))
        return 0
    existing = _read_rows()          # linhas já logadas (com resultado)
    for r in existing:
        r["_claimed"] = False
    logged = 0
    for t in trades:
        tk = t.get("out_ticket")
        if tk is None or tk in _logged_deals:
            continue
        cdt = datetime.fromtimestamp(t["close_epoch"], _BRT)
        cday = cdt.strftime("%Y-%m-%d")
        # tenta casar com uma linha existente (mesmo símbolo + profit ~igual) → seed.
        # NÃO usamos a data no casamento (o horário do servidor MT5 tem fuso diferente
        # do BRT do CSV, o que quebraria o match e duplicaria trades).
        matched = None
        for r in existing:
            if r.get("_claimed"):
                continue
            if ((r.get("symbol", "") or "").upper() == t["symbol"]
                    and abs(_f(r.get("profit")) - t["profit"]) < 0.05):
                matched = r
                break
        if matched is not None:
            matched["_claimed"] = True
            _mark_deal_logged(tk)     # já estava no CSV → só marca como conhecido
            continue
        # fechamento NOVO/perdido → registra agora, com os dados reais do histórico
        entry = {
            "id": _next_id(), "datetime_brt": cdt.strftime("%Y-%m-%d %H:%M:%S"),
            "symbol": t["symbol"], "direcao": t.get("direcao", "?"),
            "entry_price": t.get("entry_price") if t.get("entry_price") is not None else "",
            "sl": "", "tp1": "", "volume": t.get("volume"), "score": "",
        }
        reason = "TP" if t["profit"] > 0.01 else ("SL" if t["profit"] < -0.01 else "BE")
        _log_exit(t["symbol"], t.get("exit_price", 0), t["profit"], reason, entry=entry)
        _mark_deal_logged(tk)
        logged += 1
    return logged


# ── Endpoints ────────────────────────────────────────────────────────────────

@crypto_bp.route("/api/crypto/symbols")
def api_symbols():
    from services.crypto_config import get_symbols
    return jsonify({"ok": True, "symbols": get_symbols()})


@crypto_bp.route("/api/crypto/account")
def api_account():
    """Moeda/saldo da conta MT5 + cotação USD/BRL do dia."""
    from services.crypto_service import account_info, usd_brl_rate
    return jsonify({"ok": True, "account": account_info(), "usdbrl": usd_brl_rate()})


@crypto_bp.route("/api/crypto/symbol-info")
def api_symbol_info():
    """Spec do símbolo (valor por ponto, tick, dígitos) para dimensionar a mão."""
    from services.crypto_service import symbol_spec
    symbol = (request.args.get("symbol", "BTCUSD") or "BTCUSD").upper()
    return jsonify({"ok": True, "spec": symbol_spec(symbol)})


@crypto_bp.route("/api/crypto/lot", methods=["GET", "POST"])
def api_lot():
    """Tamanho da mão (lote/volume) por ativo. POST {symbol, volume}."""
    from services.crypto_config import cfg_for
    if request.method == "POST":
        b = request.get_json(silent=True) or {}
        sym = (b.get("symbol", "") or "").upper()
        if sym:
            try:
                v = float(b.get("volume"))
                _autost(sym)["volume"] = max(0.0, v) or None
            except Exception:
                pass
    # devolve o lote efetivo por ativo (definido ou default da config)
    from services.crypto_config import get_symbols
    out = {}
    for s in get_symbols():
        st = _auto.get(s.upper(), {})
        out[s] = st.get("volume") or cfg_for(s).get("volume", 0.1)
    return jsonify({"ok": True, "lots": out})


@crypto_bp.route("/api/crypto/mt5")
def api_mt5():
    from services.crypto_service import get_candles, get_tick
    from services.crypto_analysis import analyze
    from services.crypto_config import cfg_for, TF_MINUTES
    symbol = (request.args.get("symbol", "BTCUSD") or "BTCUSD").upper()
    interval = str(request.args.get("interval", "15"))
    candles, err = get_candles(symbol, TF_MINUTES.get(interval, 15))
    if err:
        return jsonify({"ok": False, "error": err})
    sig = analyze(candles, cfg_for(symbol))
    return jsonify({"ok": True, "fonte": "mt5-crypto", "symbol": symbol,
                    "candles": candles, "signal": sig, "tick": get_tick(symbol),
                    "last_ai": _last_ai.get(symbol)})


@crypto_bp.route("/api/crypto/tick")
def api_tick():
    """Preço atual (leve) para atualizar o último candle ao vivo (1-2s)."""
    from services.crypto_service import get_tick
    symbol = (request.args.get("symbol", "BTCUSD") or "BTCUSD").upper()
    t = get_tick(symbol)
    if not t:
        return jsonify({"ok": False})
    return jsonify({"ok": True, "price": t.get("last") or t.get("bid"),
                    "bid": t.get("bid"), "ask": t.get("ask"), "time": t.get("time", 0)})


@crypto_bp.route("/api/crypto/position")
def api_position():
    from services.crypto_service import get_positions
    symbol = (request.args.get("symbol", "BTCUSD") or "BTCUSD").upper()
    pos, err = get_positions(symbol)
    return jsonify({"ok": err is None, "position": (pos[0] if pos else None), "error": err})


@crypto_bp.route("/api/crypto/positions")
def api_positions():
    """Todas as posições abertas do Monitor Crypto (para a lista de controle)."""
    from services.crypto_service import get_positions
    _maybe_reconcile()   # backstop extra: a lista de trades (cyHist) chama este endpoint
    pos, err = get_positions()
    return jsonify({"ok": err is None, "items": pos or [], "error": err})


@crypto_bp.route("/api/crypto/manage")
def api_manage():
    """Modo MANAGER: avalia a posição aberta e aplica breakeven/trailing (aperta o stop)."""
    from services.crypto_service import get_positions, get_candles, modify_sl
    from services.crypto_analysis import analyze
    from services.crypto_position_manager import manage
    from services.crypto_config import cfg_for, TF_MINUTES
    symbol = (request.args.get("symbol", "BTCUSD") or "BTCUSD").upper()
    interval = str(request.args.get("interval", "15"))
    do_apply = request.args.get("apply", "1") == "1"
    pos, _ = get_positions(symbol)
    if not pos:
        return jsonify({"ok": True, "mode": "SIGNAL"})   # sem posição → painel de sinal
    p = pos[0]
    candles, _e = get_candles(symbol, TF_MINUTES.get(interval, 15))
    sig = analyze(candles, cfg_for(symbol)) if candles else {}
    m = manage(p, sig, float(sig.get("atr") or 0.0))
    applied = None
    if do_apply and m.get("new_sl") is not None:
        ok, _err = modify_sl(symbol, m["new_sl"])
        if ok:
            applied = m["new_sl"]
    return jsonify({"ok": True, **m, "applied": applied})


# ── MONITOR MACRO/MICRO (momentum multi-timeframe) ──────────────────────────

def _mm_compute(symbol):
    """Calcula MACRO (H1) e MICRO (M5) com cache de _MM_TTL s. Detecta o gatilho de
    PULLBACK-RECUPERAÇÃO: macro forte e direcional + micro que ACABOU de realinhar
    com o macro (estava contra/neutro) → entrada a favor da tendência maior."""
    now = time.time()
    c = _mm_cache.get(symbol)
    if c and (now - c["ts"] < _MM_TTL):
        return c["data"]
    from services.crypto_service import get_candles
    from services.crypto_macro_micro import tf_momentum, read
    h1, _e1 = get_candles(symbol, 60, 200)   # MACRO = H1
    m5, _e2 = get_candles(symbol, 5, 200)    # MICRO = M5
    macro = tf_momentum(h1 or [], window=24, ema_f=9, ema_s=21)
    micro = tf_momentum(m5 or [], window=6, ema_f=5, ema_s=13)
    r = read(macro, micro)

    prev = (c or {}).get("prev_micro")
    want = "COMPRA" if macro["dir"] == "ALTA" else ("VENDA" if macro["dir"] == "BAIXA" else None)
    aligned = ((macro["dir"] == "ALTA" and micro["dir"] == "ALTA")
               or (macro["dir"] == "BAIXA" and micro["dir"] == "BAIXA"))
    counter = ("BAIXA" if macro["dir"] == "ALTA" else "ALTA")
    just_flipped = prev in (counter, "NEUTRO", None)   # micro estava contra e voltou
    entry = stop = tp = None
    trigger = None
    if want and macro["pct"] >= 45 and aligned and just_flipped and m5:
        px = float(m5[-1]["close"])
        win = m5[-6:]
        if want == "COMPRA":
            stop = min(x["low"] for x in win); entry = px
            tp = entry + 1.5 * (entry - stop) if entry > stop else None
        else:
            stop = max(x["high"] for x in win); entry = px
            tp = entry - 1.5 * (stop - entry) if stop > entry else None
        if tp:
            trigger = want

    data = {"ok": True, "symbol": symbol, "macro": macro, "micro": micro,
            "reading": r.get("reading"), "kind": r.get("kind"),
            "macro_dir": macro["dir"], "micro_dir": micro["dir"],
            "neutro_pct": r.get("neutro_pct", max(0, 100 - macro["pct"])),
            "trigger": trigger,
            "entry": round(entry, 5) if entry else None,
            "stop": round(stop, 5) if stop else None,
            "tp1": round(tp, 5) if tp else None}
    _mm_cache[symbol] = {"ts": now, "data": data, "prev_micro": micro["dir"]}
    return data


@crypto_bp.route("/api/crypto/macromicro")
def api_macromicro():
    symbol = (request.args.get("symbol", "BTCUSD") or "BTCUSD").upper()
    try:
        return jsonify(_mm_compute(symbol))
    except Exception as exc:
        logger.warning("crypto macromicro: %s", exc)
        return jsonify({"ok": False, "error": str(exc)})


@crypto_bp.route("/api/crypto/auto-state", methods=["GET", "POST"])
def api_auto_state():
    if request.method == "POST":
        b = request.get_json(silent=True) or {}
        sym = (b.get("symbol", "") or "").upper()
        if sym:
            st = _autost(sym)
            st["enabled"] = bool(b.get("enabled", False))
    return jsonify({"ok": True, "state": {k: v["enabled"] for k, v in _auto.items()}})


@crypto_bp.route("/api/crypto/execute", methods=["POST"])
def api_execute():
    from services.crypto_service import execute
    from services.crypto_config import is_forex
    b = request.get_json(silent=True) or {}
    symbol = (b.get("symbol", "") or "").upper()
    acao = b.get("acao", "")
    volume = float(b.get("volume", 0.1))
    sl = b.get("sl"); tp = b.get("tp1") or b.get("tp")
    score = b.get("score", 0)
    if not symbol or acao not in ("COMPRA", "VENDA"):
        return jsonify({"ok": False, "error": "símbolo/ação inválidos"})
    if is_forex(symbol) and _is_weekend():
        return jsonify({"ok": False, "error": f"{symbol}: forex fechado no fim de semana."})
    result, err = execute(symbol, acao, volume,
                          float(sl) if sl else None, float(tp) if tp else None)
    if result and not err:
        _autost(symbol)["last_ts"] = time.time()
        _log_entry(symbol, acao, result, score)
        return jsonify({"ok": True, "result": result})
    return jsonify({"ok": False, "error": err})


@crypto_bp.route("/api/crypto/auto-check", methods=["POST"])
def api_auto_check():
    """Motor de auto-trade (dirigido pelo cliente). Recalcula o sinal no servidor."""
    from services.crypto_service import get_candles, get_positions, execute
    from services.crypto_analysis import analyze
    from services.crypto_config import cfg_for, is_forex, TF_MINUTES
    b = request.get_json(silent=True) or {}
    symbol = (b.get("symbol", "") or "").upper()
    interval = str(b.get("interval", "15"))
    if not symbol:
        return jsonify({"ok": True, "action": "NONE", "reason": "sem símbolo"})

    st = _autost(symbol)
    if not st["enabled"]:
        return jsonify({"ok": True, "action": "NONE", "reason": "auto-trade OFF"})
    if is_forex(symbol) and _is_weekend():
        return jsonify({"ok": True, "action": "NONE", "reason": "forex fechado (fim de semana)"})
    # ── TRAVA DE EXECUÇÃO por ativo: só UM auto-check por vez (atômico) ──
    lk = _lock_for(symbol)
    if not lk.acquire(blocking=False):
        return jsonify({"ok": True, "action": "NONE", "reason": "execução em andamento"})
    try:
        # cooldown DENTRO do lock: assim que A abre e grava last_ts, B/C que entrarem
        # aqui já veem o last_ts novo e são barrados (evita ordens no mesmo segundo).
        elapsed = time.time() - st["last_ts"]
        if elapsed < COOLDOWN_SEC:
            return jsonify({"ok": True, "action": "NONE",
                            "reason": f"cooldown {int(COOLDOWN_SEC - elapsed)}s"})
        # ANTI-OVERTRADING: não abre se já há posição no MT5. Bloqueia TAMBÉM se a
        # checagem de posição falhar (não abre no escuro). Sem flag sticky — assim
        # nunca fica "preso em aberta" quando a posição na verdade já fechou.
        pos, perr = get_positions(symbol)
        if perr:
            return jsonify({"ok": True, "action": "NONE", "reason": "checagem de posição indisponível — não abre"})
        if pos:
            return jsonify({"ok": True, "action": "NONE", "reason": "posição já aberta"})

        candles, err = get_candles(symbol, TF_MINUTES.get(interval, 15))
        if err:
            return jsonify({"ok": True, "action": "NONE", "reason": err})
        cfg = cfg_for(symbol)
        sig = analyze(candles, cfg)
        source = "score"
        score_ok = (sig["acao"] in ("COMPRA", "VENDA")
                    and abs(sig.get("score", 0)) >= int(cfg.get("score_min", 7)))

        if score_ok:
            # ── Sinal do SCORE → validação da IA (fail-closed, exige alinhamento 1h) ──
            _session["gerados"] += 1
            from services.crypto_validator_ai import validate as _ai_validate
            ai = _ai_validate(sig, symbol, interval)
            _last_ai[symbol] = {"veredito": ai.get("veredito"), "confianca": ai.get("confianca"), "motivo": ai.get("motivo"), "acao": sig["acao"], "score": sig.get("score"), "ts": time.time()}
            if not ai.get("aprovado") or ai.get("veredito") != "EXECUTAR":
                _session["bloqueados"] += 1
                _mot = str(ai.get("motivo", ""))
                return jsonify({"ok": True, "action": "BLOCKED", "veredito": ai.get("veredito"), "reason": "IA " + str(ai.get("veredito")) + ": " + _mot, "ai": ai})
        else:
            # ── Score não disparou → 1) IMPULSO (rompimento); 2) PULLBACK macro/micro ──
            from services.crypto_momentum import detect as _impulse
            imp = _impulse(candles, cfg)
            if imp.get("impulso") and imp.get("acao") in ("COMPRA", "VENDA"):
                sig = dict(sig)
                sig.update({"acao": imp["acao"], "entrada": imp.get("entrada"),
                            "stop": imp.get("stop"), "tp1": imp.get("tp1"),
                            "forca": imp.get("forca", "MODERADA"),
                            "confluences": imp.get("confluences", []),
                            "score": 8 if imp["acao"] == "COMPRA" else -8})
                source = "impulso"
                ai = {"veredito": "EXECUTAR", "confianca": None, "ia_usada": False,
                      "motivo": "[IMPULSO] " + "; ".join(imp.get("confluences", [])[:3])}
            else:
                # PULLBACK macro/micro: entra a favor da tendência maior quando o micro
                # realinha após um recuo (macro forte + micro voltando pra direção do macro).
                mm = _mm_compute(symbol)
                if not mm.get("trigger"):
                    _why = (imp.get("confluences") or [mm.get("reading") or "sinal NEUTRO / sem gatilho"])[0]
                    return jsonify({"ok": True, "action": "NONE", "reason": _why})
                sig = dict(sig)
                sig.update({"acao": mm["trigger"], "entrada": mm.get("entry"),
                            "stop": mm.get("stop"), "tp1": mm.get("tp1"),
                            "forca": "MODERADA", "confluences": [mm.get("reading", "")],
                            "score": 8 if mm["trigger"] == "COMPRA" else -8})
                source = "pullback"
                ai = {"veredito": "EXECUTAR", "confianca": None, "ia_usada": False,
                      "motivo": "[PULLBACK] " + str(mm.get("reading", ""))}
            _session["gerados"] += 1
            # IMPULSO/PULLBACK operam COM o momentum → não passam pelo gate de tendência
            # da IA (a própria confirmação do detector é a validação).
            _last_ai[symbol] = {"veredito": source.upper(), "confianca": None,
                                "motivo": ai["motivo"], "acao": sig["acao"],
                                "score": sig.get("score"), "ts": time.time()}

        # 2ª CHECAGEM de posição logo antes de abrir (dupla confirmação): evita abrir
        # em cima de uma posição que surgiu durante o cálculo e reduz o falso-vazio
        # do get_positions. Precisa de DUAS leituras vazias para abrir.
        pos2, perr2 = get_positions(symbol)
        if perr2 or pos2:
            return jsonify({"ok": True, "action": "NONE", "reason": "posição já aberta (2ª checagem)"})
        # marca o cooldown ANTES de executar (protege contra reentrada na latência)
        st["last_ts"] = time.time()
        vol = st.get("volume") or float(cfg.get("volume", 0.1))   # lote definido no painel
        result, exerr = execute(symbol, sig["acao"], float(vol),
                                sig.get("stop"), sig.get("tp1"))
        if result and not exerr:
            _session["executados"] += 1
            _log_entry(symbol, sig["acao"], result, sig.get("score"), ai)
            return jsonify({"ok": True, "action": "EXECUTED", "acao": sig["acao"],
                            "source": source, "result": result, "ai": ai})
        st["last_ts"] = 0.0   # execução falhou → libera o cooldown
        return jsonify({"ok": False, "action": "FAILED", "error": exerr})
    finally:
        lk.release()


@crypto_bp.route("/api/crypto/close", methods=["POST"])
def api_close():
    from services.crypto_service import close_position
    b = request.get_json(silent=True) or {}
    symbol = (b.get("symbol", "") or "").upper()
    info, err = close_position(symbol)
    if info and not err:
        # NÃO loga aqui: o registro é feito de forma autoritativa por /register-close
        # (via deal de saída REAL do MT5), evitando dupla-contagem do mesmo fechamento.
        return jsonify({"ok": True, "result": info})
    return jsonify({"ok": False, "error": err})


@crypto_bp.route("/api/crypto/move-sl", methods=["POST"])
def api_move_sl():
    from services.crypto_service import get_positions, modify_sl
    b = request.get_json(silent=True) or {}
    symbol = (b.get("symbol", "") or "").upper()
    pos, _ = get_positions(symbol)
    if not pos:
        return jsonify({"ok": False, "error": "sem posição"})
    ok, err = modify_sl(symbol, pos[0]["price_open"])
    return jsonify({"ok": ok, "error": err})


@crypto_bp.route("/api/crypto/register-close", methods=["POST"])
def api_register_close():
    """Registra o fechamento de forma AUTORITATIVA pelo servidor.

    O cliente avisa 'posição sumiu' — mas isso pode ser só uma leitura transitória
    (positions_get vazio por contenção do MT5). Então SÓ registramos se o MT5
    confirmar um deal de SAÍDA REAL (DEAL_ENTRY_OUT). Sem deal real → NÃO grava
    nada (elimina o 'loss fantasma' com a posição ainda aberta).
    Dedup por TICKET do deal (nunca registra o mesmo fechamento 2x).
    Retorna {ok, closed:bool, profit?} — o cliente só troca de modo se closed=True.
    """
    b = request.get_json(silent=True) or {}
    symbol = (b.get("symbol", "") or "").upper()
    if not symbol:
        return jsonify({"ok": True, "closed": False})
    try:
        from services.crypto_service import last_out_deal
        deal = last_out_deal(symbol)
    except Exception as exc:
        logger.warning("crypto register-close last_out_deal: %s", exc)
        return jsonify({"ok": True, "closed": False, "reason": "history indisponível"})
    if not deal:
        # posição ainda aberta / nenhum fechamento real → não registra
        return jsonify({"ok": True, "closed": False, "reason": "sem deal de saída real"})
    if deal["ticket"] in _logged_deals:
        return jsonify({"ok": True, "closed": True, "dedup": True})
    profit = deal["profit"]
    reason = "TP" if profit > 0.01 else ("SL" if profit < -0.01 else "BE")
    _log_exit(symbol, deal.get("price", 0), profit, reason)
    _mark_deal_logged(deal["ticket"])   # persiste p/ a reconciliação não duplicar
    return jsonify({"ok": True, "closed": True, "profit": profit})


def _maybe_reconcile():
    """Roda a reconciliação no máx. 1x a cada 20s. Chamado só por endpoints da tela
    Crypto (session/report) → NÃO adiciona carga ao MT5 durante o pregão da B3."""
    global _last_reconcile
    if time.time() - _last_reconcile < 10:
        return
    _last_reconcile = time.time()
    try:
        _reconcile_closes()
    except Exception as exc:
        logger.warning("crypto reconcile: %s", exc)


@crypto_bp.route("/api/crypto/session")
def api_session():
    _maybe_reconcile()   # backstop: captura fechamentos que o navegador perdeu
    # Calcula do CSV (trades FECHADOS de hoje) → sobrevive a restart e reflete o log real
    rows = _read_rows()
    today = _now().strftime("%Y-%m-%d")
    td = [r for r in rows if (r.get("datetime_brt") or "")[:10] == today]
    wins = sum(1 for r in td if _f(r.get("profit")) > 0)
    losses = sum(1 for r in td if _f(r.get("profit")) < 0)
    be = sum(1 for r in td if abs(_f(r.get("profit"))) < 1e-9)
    pnl = round(sum(_f(r.get("profit")) for r in td), 2)
    return jsonify({"ok": True, "session": {
        "trades": len(td), "wins": wins, "losses": losses, "breakevens": be, "pnl": pnl,
        "gerados": _session.get("gerados", 0), "executados": _session.get("executados", 0),
        "bloqueados": _session.get("bloqueados", 0)}})


@crypto_bp.route("/api/crypto/trades")
def api_trades():
    _maybe_reconcile()   # garante que a lista reflita fechamentos recentes do MT5
    rows = []
    if os.path.exists(_CSV):
        try:
            with open(_CSV, encoding="utf-8") as f:
                rows = [r for r in csv.DictReader(f)]
        except Exception as exc:
            logger.warning("crypto trades read: %s", exc)
    return jsonify({"ok": True, "items": rows[-50:]})


# ── RELATÓRIO CRYPTO (dia/semana/mês) — independente da B3 ──────────────────

def _read_rows():
    if not os.path.exists(_CSV):
        return []
    try:
        with open(_CSV, encoding="utf-8") as f:
            return [r for r in csv.DictReader(f) if r.get("resultado")]
    except Exception:
        return []


def _f(v):
    try:
        return float(v)
    except Exception:
        return 0.0


def _agg(rows):
    wins = [r for r in rows if _f(r.get("profit")) > 0]
    loss = [r for r in rows if _f(r.get("profit")) < 0]
    be   = [r for r in rows if abs(_f(r.get("profit"))) < 1e-9 and r.get("resultado") == "BE"]
    dec  = len(wins) + len(loss)
    pnl  = round(sum(_f(r.get("profit")) for r in rows), 2)
    gw   = sum(_f(r.get("profit")) for r in wins)
    gl   = abs(sum(_f(r.get("profit")) for r in loss))
    pf   = round(gw / gl, 2) if gl else (99.0 if gw else 0.0)
    exp  = round(pnl / len(rows), 2) if rows else 0.0
    scores = [_f(r.get("score")) for r in rows if r.get("score") not in (None, "")]
    return {"total": len(rows), "wins": len(wins), "losses": len(loss), "be": len(be),
            "win_rate": round(len(wins) / dec * 100) if dec else 0,
            "pnl": pnl, "profit_factor": pf, "expectativa": exp,
            "avg_score": round(sum(scores) / len(scores), 1) if scores else None}


def _daily(rows):
    by = {}
    for r in rows:
        d = (r.get("datetime_brt") or "")[:10]
        if not d:
            continue
        g = by.setdefault(d, {"date": d, "trades": 0, "wins": 0, "losses": 0, "pnl": 0.0})
        g["trades"] += 1
        p = _f(r.get("profit"))
        g["pnl"] = round(g["pnl"] + p, 2)
        if p > 0: g["wins"] += 1
        elif p < 0: g["losses"] += 1
    return [by[k] for k in sorted(by)]


@crypto_bp.route("/api/crypto/report")
def api_report():
    from datetime import timedelta
    _maybe_reconcile()   # garante que o relatório reflita fechamentos recentes
    period = request.args.get("period", "day")
    rows = _read_rows()
    now = _now()
    today = now.strftime("%Y-%m-%d")
    if period == "month":
        pref = today[:7]
        sel = [r for r in rows if (r.get("datetime_brt") or "")[:7] == pref]
        dfrom, dto = pref + "-01", today
    elif period == "week":
        monday = (now - timedelta(days=now.weekday())).strftime("%Y-%m-%d")
        sel = [r for r in rows if monday <= (r.get("datetime_brt") or "")[:10] <= today]
        dfrom, dto = monday, today
    else:
        sel = [r for r in rows if (r.get("datetime_brt") or "")[:10] == today]
        dfrom, dto = today, today
    summary = _agg(sel)
    daily = _daily(sel)
    if period != "day" and daily:
        summary["best_day"] = max(daily, key=lambda d: d["pnl"])
        summary["worst_day"] = min(daily, key=lambda d: d["pnl"])
    return jsonify({"ok": True, "summary": summary, "daily": daily,
                    "trades": sel[-80:], "date_from": dfrom, "date_to": dto, "period": period})
