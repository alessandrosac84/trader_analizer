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

# Auto-trade que já sobe LIGADO no boot.
# CRYPTO_AUTO_ON: lista ("BTCUSD,ETHUSD"), ALL/* (todos CRYPTO_SYMBOLS) ou 0/OFF/NONE.
# Default = ALL — todos os ativos crypto sobem TRADE ON. Painel ainda liga/desliga em runtime.
import os as _os

def _crypto_auto_on_default() -> set:
    raw = (_os.getenv("CRYPTO_AUTO_ON", "ALL") or "").strip()
    up = raw.upper()
    if up in ("0", "OFF", "NONE", "FALSE"):
        return set()
    if not raw or up in ("ALL", "*", "1", "TRUE", "ON"):
        try:
            from services.crypto_config import get_symbols
            return {s.upper() for s in get_symbols()}
        except Exception:
            return {"BTCUSD", "EURUSD", "GBPUSD", "ETHUSD", "XAUUSD"}
    return {s.strip().upper() for s in raw.split(",") if s.strip()}

_AUTO_ON_DEFAULT = _crypto_auto_on_default()

# Estado em memória (por processo)
_auto = {}       # symbol -> {"enabled": bool, "last_ts": float}
# Pré-semeia os símbolos do CRYPTO_AUTO_ON JÁ como enabled=True, para que o GET
# /auto-state devolva o estado logo no boot (o painel lê isso ao carregar e liga o
# botão + o loop). Sem isso o _auto ficaria vazio e a tela subiria tudo OFF.
for _s in _AUTO_ON_DEFAULT:
    _auto[_s] = {"enabled": True, "last_ts": 0.0, "volume": None}
_pending = {}    # symbol -> dados da entrada (para registrar no fechamento)
_session = {"trades": 0, "wins": 0, "losses": 0, "breakevens": 0, "pnl": 0.0,
            "gerados": 0, "executados": 0, "bloqueados": 0}
# Última decisão da IA por ativo (para exibir motivo no painel)
_last_ai = {}    # symbol -> {"veredito","confianca","motivo","ts"}
_last_close = {} # symbol -> ts do último fechamento registrado (dedup)
# Motivo MANAGER (giveback/micro/reversão) — consumido ao gravar o exit no CSV
_close_intent = {}  # symbol -> {"code","detail","ts"}

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
_MM_TTL = 5
_mm_cache = {}   # symbol -> {"ts", "data", "prev_micro"}
_usdbrl_cache = {"v": 5.2}   # cotação USD/BRL (atualizada no /api/crypto/account)
_pos_peak = {}       # ticket -> pico MFE em preço (p/ armar 0,6R)
_pos_peak_pnl = {}   # ticket -> pico MFE na moeda da conta (giveback escalonado R$)
_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
_PEAKS_FILE = os.path.join(_DATA_DIR, "crypto_pos_peaks.json")
_peaks_seeded = set()  # tickets já tentaram seed MFE nesta sessão


def _load_peaks_disk():
    """Carrega picos persistidos (sobrevive restart)."""
    import json
    try:
        if not os.path.exists(_PEAKS_FILE):
            return
        with open(_PEAKS_FILE, encoding="utf-8") as f:
            raw = json.load(f) or {}
        for k, v in raw.items():
            try:
                tk = int(k)
            except Exception:
                continue
            if not isinstance(v, dict):
                continue
            pf = float(v.get("peak_favor") or 0)
            pp = float(v.get("peak_pnl") or 0)
            if pf > 0:
                _pos_peak[tk] = max(_pos_peak.get(tk, 0.0), pf)
            if pp > 0:
                _pos_peak_pnl[tk] = max(_pos_peak_pnl.get(tk, 0.0), pp)
    except Exception as exc:
        logger.debug("load peaks: %s", exc)


def _save_peaks_disk():
    import json
    try:
        os.makedirs(_DATA_DIR, exist_ok=True)
        payload = {}
        for tk, pf in _pos_peak.items():
            payload[str(tk)] = {
                "peak_favor": float(pf or 0),
                "peak_pnl": float(_pos_peak_pnl.get(tk, 0) or 0),
                "ts": time.time(),
            }
        with open(_PEAKS_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f)
    except Exception as exc:
        logger.debug("save peaks: %s", exc)


def _prune_peaks(open_tickets):
    """Remove tickets fechados da RAM e do disco (sem wipe total)."""
    try:
        alive = {int(t) for t in (open_tickets or []) if t is not None}
    except Exception:
        return
    dead = [tk for tk in list(_pos_peak.keys()) if tk not in alive]
    for tk in dead:
        _pos_peak.pop(tk, None)
        _pos_peak_pnl.pop(tk, None)
        _peaks_seeded.discard(tk)
    if dead:
        _save_peaks_disk()


def _seed_peak_from_mfe(p, symbol):
    """ reconstruí MFE via highs/lows M5 desde a abertura (MT5 não guarda pico)."""
    tk = p.get("ticket")
    if tk is None or tk in _peaks_seeded:
        return
    _peaks_seeded.add(tk)
    try:
        from services.crypto_service import get_candles
        t0 = int(p.get("time") or 0)
        entry = float(p.get("price_open") or 0)
        vol = float(p.get("volume") or 0) or 1.0
        if not t0 or entry <= 0:
            return
        candles, _e = get_candles(symbol, 5, 2000)
        if not candles:
            return
        after = [c for c in candles if int(c.get("time") or 0) >= t0]
        if not after:
            return
        is_buy = p.get("type") == "COMPRA"
        if is_buy:
            mfe = max(float(c.get("high") or entry) for c in after) - entry
        else:
            mfe = entry - min(float(c.get("low") or entry) for c in after)
        if mfe <= 0:
            return
        # $/pt ≈ profit/favor quando favor≠0; senão vol (BTC/ETH IC: contract≈1)
        favor_now = float(p.get("price_current") or entry) - entry
        if not is_buy:
            favor_now = -favor_now
        pnl_now = float(p.get("profit") or 0)
        if abs(favor_now) > 1e-9:
            usd_per_pt = abs(pnl_now / favor_now)
        else:
            usd_per_pt = vol
        mfe_pnl = mfe * usd_per_pt
        prev_f = _pos_peak.get(tk, 0.0)
        prev_p = _pos_peak_pnl.get(tk, 0.0)
        _pos_peak[tk] = max(prev_f, mfe)
        _pos_peak_pnl[tk] = max(prev_p, mfe_pnl)
        if mfe > prev_f + 1e-9 or mfe_pnl > prev_p + 1e-9:
            logger.info(
                "PEAK_SEED %s tk=%s mfe_pts=%.2f mfe_pnl=%.2f (was favor=%.2f pnl=%.2f)",
                symbol, tk, mfe, mfe_pnl, prev_f, prev_p,
            )
            _save_peaks_disk()
    except Exception as exc:
        logger.debug("seed peak MFE %s: %s", symbol, exc)


_load_peaks_disk()

# Trava de execução por ativo — impede ordens simultâneas (corrida do auto-check)
import threading as _threading
_exec_locks = {}
def _lock_for(sym):
    return _exec_locks.setdefault(sym.upper(), _threading.Lock())

# Runtime do servidor (CryptoAutoRuntime) espera o lock; UI/painel não bloqueia.
# Evita fome: aba aberta chama auto-check a cada ~6s e cyBgDrive a cada 12s —
# com acquire(False) o runtime perdia o ciclo no fechamento da barra M15.
_RUNTIME_LOCK_TIMEOUT_SEC = 25.0


def _now():
    return datetime.now(_BRT)


def _is_weekend():
    return _now().weekday() >= 5


def _autost(sym):
    su = sym.upper()
    return _auto.setdefault(su, {"enabled": su in _AUTO_ON_DEFAULT, "last_ts": 0.0, "volume": None})


# ── CSV log ──────────────────────────────────────────────────────────────────

def _next_id():
    if not os.path.exists(_CSV):
        return 1
    try:
        with open(_CSV, encoding="utf-8") as f:
            return sum(1 for _ in f)   # header conta como +0 na prática
    except Exception:
        return 1


def _set_close_intent(symbol: str, code: str, detail: str = ""):
    """Guarda o motivo do fechamento antecipado (válido ~10 min)."""
    if not symbol:
        return
    _close_intent[symbol.upper()] = {
        "code": (code or "MANUAL").strip().upper()[:32],
        "detail": (detail or "")[:180],
        "ts": time.time(),
    }


def _pop_close_intent(symbol: str, max_age: float = 600.0):
    """Consome o intent se ainda for recente."""
    d = _close_intent.pop((symbol or "").upper(), None)
    if not d:
        return None
    if time.time() - float(d.get("ts") or 0) > max_age:
        return None
    return d


def _format_exit_reason(fallback: str, intent):
    """Monta exit_reason legível p/ histórico (código + detalhe curto)."""
    if not intent:
        return fallback or "MT5"
    code = intent.get("code") or "MANAGER"
    detail = (intent.get("detail") or "").strip()
    # tira emojis pesados p/ CSV mais limpo, mantém texto
    for ch in ("🔒", "⚡", "⚠️", "✅", "🚀", "⏰"):
        detail = detail.replace(ch, "").strip()
    if detail:
        return f"{code} · {detail}"[:120]
    return code


def _infer_broker_exit_reason(profit, entry_price=None, exit_price=None,
                              tp=None, sl=None, direcao=None) -> str:
    """NÃO usa 'lucro>0 ⇒ TP'. Só marca TP/SL se o preço de saída chegou perto do alvo."""
    def _n(v):
        try:
            if v is None or v == "":
                return None
            return float(v)
        except Exception:
            return None

    e, x, tpp, sll = _n(entry_price), _n(exit_price), _n(tp), _n(sl)
    risk = None
    if e is not None and sll is not None and abs(e - sll) > 1e-9:
        risk = abs(e - sll)
    elif e is not None and tpp is not None:
        risk = abs(tpp - e)
    tol = max((risk or 0) * 0.12, 0.08)  # ~12% do risco ou mínimo absoluto

    if tpp is not None and x is not None and abs(x - tpp) <= tol:
        return "TP"
    if sll is not None and x is not None and abs(x - sll) <= tol:
        return "SL"
    # Lucro sem bater TP = saída antecipada (giveback/micro/manual/trailing), NÃO TP
    if profit is not None and float(profit) > 0.01:
        return "SAIDA_ANTECIPADA"
    if profit is not None and float(profit) < -0.01:
        return "SL" if sll is not None else "SAIDA"
    return "BE"


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
    # PRIORIDADE para a entrada FORNECIDA (reconciliação = casada por position_id do MT5,
    # autoritativa). Só usa a _pending quando não veio entrada explícita — assim NUNCA
    # mistura a entrada de uma posição AINDA ABERTA com o fechamento de OUTRA posição.
    if entry is not None:
        p = dict(entry)
    else:
        p = _pending.pop(symbol.upper(), None) or {
            "id": _next_id(), "datetime_brt": _now().strftime("%Y-%m-%d %H:%M:%S"),
            "symbol": symbol.upper(), "direcao": "?", "entry_price": "", "sl": "",
            "tp1": "", "volume": "", "score": "",
        }
    intent = _pop_close_intent(symbol)
    # Se o reconcile só classificou TP/SL/BE pelo sinal do P&L, preferir intent do MANAGER
    raw = reason or "MT5"
    if intent and str(raw).upper() in ("TP", "SL", "BE", "MT5", ""):
        reason = _format_exit_reason(raw, intent)
    elif intent and intent.get("code") and intent["code"] not in str(raw).upper():
        reason = _format_exit_reason(raw, intent)
    else:
        reason = raw
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
    # Telegram: avisa o fechamento (USD + BRL). Não bloqueia nem quebra o log se falhar.
    try:
        from services.crypto_telegram import notify_exit
        notify_exit(p.get("symbol", symbol), p.get("direcao"), p.get("entry_price"),
                    exit_price, profit, reason, usdbrl=_usdbrl_cache.get("v", 5.2))
    except Exception:
        pass


_reconcile_lock = _threading.Lock()


def _reconcile_closes():
    """Serializa a reconciliação: só UMA por vez. A thread de fundo (5s), o polling
    do painel (_maybe_reconcile) e o register-close podem disparar juntos — sem essa
    trava, dois rodavam ao mesmo tempo e gravavam o MESMO fechamento 2x (duplicata).
    Se outra já está rodando, sai na hora (o próximo ciclo pega o que faltar)."""
    if not _reconcile_lock.acquire(blocking=False):
        return 0
    try:
        return _reconcile_closes_inner()
    finally:
        _reconcile_lock.release()


def _reconcile_closes_inner():
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
            # tolerância absorve diferença de comissão/swap entre o registro do cliente
            # e o cálculo do histórico (evita duplicar o MESMO fechamento).
            _tol = max(0.10, 0.02 * abs(t["profit"]))
            if ((r.get("symbol", "") or "").upper() == t["symbol"]
                    and abs(_f(r.get("profit")) - t["profit"]) < _tol):
                matched = r
                break
        if matched is not None:
            matched["_claimed"] = True
            _mark_deal_logged(tk)     # já estava no CSV → só marca como conhecido
            continue
        # fechamento NOVO/perdido → registra agora, com os dados reais do histórico
        pend = _pending.get(t["symbol"]) or {}
        entry = {
            "id": _next_id(), "datetime_brt": cdt.strftime("%Y-%m-%d %H:%M:%S"),
            "symbol": t["symbol"], "direcao": t.get("direcao") or pend.get("direcao") or "?",
            "entry_price": t.get("entry_price") if t.get("entry_price") is not None else pend.get("entry_price", ""),
            "sl": pend.get("sl", ""), "tp1": pend.get("tp1", ""),
            "volume": t.get("volume") or pend.get("volume", ""),
            "score": pend.get("score", ""),
            "ai_veredito": pend.get("ai_veredito", ""),
            "ai_confidence": pend.get("ai_confidence", ""),
            "ai_motivo": pend.get("ai_motivo", ""),
        }
        reason = _infer_broker_exit_reason(
            t["profit"], entry.get("entry_price"), t.get("exit_price"),
            entry.get("tp1"), entry.get("sl"), entry.get("direcao"))
        _log_exit(t["symbol"], t.get("exit_price", 0), t["profit"], reason, entry=entry)
        _mark_deal_logged(tk)
        logged += 1
        logger.info("crypto reconcile: registrou %s %+.2f (deal %s)", t["symbol"], t["profit"], tk)
    return logged


# ── Endpoints ────────────────────────────────────────────────────────────────

@crypto_bp.route("/api/crypto/symbols")
def api_symbols():
    from services.crypto_config import get_symbols
    return jsonify({"ok": True, "symbols": get_symbols()})


@crypto_bp.route("/api/crypto/account")
def api_account():
    """Moeda/saldo da conta MT5 + cotação USD/BRL do dia."""
    from services.crypto_service import account_info, usd_brl_meta
    meta = usd_brl_meta()
    rate = meta.get("usdbrl") or 5.2
    try:
        _usdbrl_cache["v"] = float(rate) or 5.2
    except Exception:
        pass
    return jsonify({
        "ok": True,
        "account": account_info(),
        "usdbrl": rate,
        "usdbrl_source": meta.get("source") or "fallback",
    })


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
    from services.crypto_config import (
        cfg_for, TF_MINUTES, active_trade_paths, score_panel_armed, auto_blocked,
    )
    symbol = (request.args.get("symbol", "BTCUSD") or "BTCUSD").upper()
    interval = str(request.args.get("interval", "15"))
    candles, err = get_candles(symbol, TF_MINUTES.get(interval, 15))
    if err:
        return jsonify({"ok": False, "error": err})
    sig = analyze(candles, cfg_for(symbol))
    paths = active_trade_paths(symbol)
    armed = score_panel_armed(symbol)
    return jsonify({
        "ok": True, "fonte": "mt5-crypto", "symbol": symbol,
        "candles": candles, "signal": sig, "tick": get_tick(symbol),
        "last_ai": _last_ai.get(symbol),
        "active_paths": paths,
        "score_armed": armed,
        "auto_blocked": auto_blocked(symbol),
    })


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
    from services.crypto_service import get_positions, get_candles, modify_sl, modify_sl_tp
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
    # rastreia o PICO de lucro (MFE) — preço (0,6R) + moeda da conta (giveback R$)
    is_buy = p.get("type") == "COMPRA"
    entry = float(p.get("price_open") or 0); cur = float(p.get("price_current") or entry)
    favor = (cur - entry) if is_buy else (entry - cur)
    pnl = float(p.get("profit") or 0)
    tk = p.get("ticket")
    # Seed MFE histórico (candles) se pico em RAM/disco ainda baixo / pós-restart
    _seed_peak_from_mfe(p, symbol)
    peak = max(_pos_peak.get(tk, 0.0), favor); _pos_peak[tk] = peak
    peak_pnl = max(_pos_peak_pnl.get(tk, 0.0), pnl); _pos_peak_pnl[tk] = peak_pnl
    _save_peaks_disk()
    # Soft cap: se inflar demais, mantém os 30 maiores peak_pnl (+ tickets abertos aqui)
    if len(_pos_peak) > 40:
        open_here = {pp.get("ticket") for pp in pos if pp.get("ticket") is not None}
        keep = sorted(_pos_peak_pnl.items(), key=lambda kv: kv[1], reverse=True)[:30]
        keep_ids = {k for k, _ in keep} | open_here
        for old_tk in list(_pos_peak.keys()):
            if old_tk not in keep_ids:
                _pos_peak.pop(old_tk, None)
                _pos_peak_pnl.pop(old_tk, None)
        _save_peaks_disk()
    # USD/BRL: cache do dashboard; se frio, mesma fonte de usd_brl_rate()
    try:
        from services.crypto_service import usd_brl_rate
        rate = float(_usdbrl_cache.get("v") or 0) or float(usd_brl_rate())
        _usdbrl_cache["v"] = rate
    except Exception:
        rate = float(_usdbrl_cache.get("v") or 5.2)
    # direção do MICRO (M5) para a reversão antecipada
    try:
        micro_dir = (_mm_compute(symbol).get("micro") or {}).get("dir", "NEUTRO")
    except Exception:
        micro_dir = "NEUTRO"
    p = {**p, "symbol": symbol}  # p/ log do giveback
    m = manage(p, sig, float(sig.get("atr") or 0.0), micro_dir=micro_dir,
               peak_favor=peak, peak_pnl=peak_pnl, usdbrl=rate)
    # Se o MANAGER pediu fechar, grava o motivo ANTES do close (painel ou runtime)
    if m.get("close"):
        code = m.get("close_code") or "MANAGER"
        _set_close_intent(symbol, code, m.get("reason") or "")
    applied = None
    tp_capped = None
    # Caps live em posição aberta: encurta TP absurdo (BTC/ETH/FX) sem esperar fill novo
    if do_apply and not m.get("close"):
        try:
            from services.crypto_edge_setups import live_caps_for, apply_live_caps
            caps = live_caps_for(symbol)
            if caps:
                sl_cap, tp_cap = caps
                cur_sl = float(p.get("sl") or 0)
                cur_tp = float(p.get("tp") or 0)
                new_sl, new_tp = apply_live_caps(
                    symbol, p.get("type"), entry, cur_sl, cur_tp)
                # Só aperta TP (não alarga SL de posição viva — risco já definido)
                if cur_tp and abs(cur_tp - entry) > tp_cap + 1e-9 and abs(new_tp - cur_tp) > 1e-6:
                    ok_tp, err_tp = modify_sl_tp(symbol, cur_sl, new_tp)
                    if ok_tp:
                        tp_capped = new_tp
                        logger.info(
                            "TP_CAP live %s: %.5f → %.5f (cap=%.5f)",
                            symbol, cur_tp, new_tp, tp_cap,
                        )
                    else:
                        logger.warning("TP_CAP %s falhou: %s", symbol, err_tp)
        except Exception as exc:
            logger.debug("tp cap open pos: %s", exc)
    if do_apply and m.get("new_sl") is not None:
        ok, _err = modify_sl(symbol, m["new_sl"])
        if ok:
            applied = m["new_sl"]
    # apply=close: runtime/painel pedem fechar de fato
    closed = False
    if do_apply and m.get("close") and request.args.get("close", "0") == "1":
        try:
            from services.crypto_service import close_position
            info, err = close_position(symbol)
            closed = bool(info and not err)
            if closed and tk is not None:
                _pos_peak.pop(tk, None)
                _pos_peak_pnl.pop(tk, None)
                _peaks_seeded.discard(tk)
                _save_peaks_disk()
        except Exception as exc:
            logger.warning("crypto manage close: %s", exc)
    return jsonify({"ok": True, **m, "applied": applied, "tp_capped": tp_capped,
                    "closed": closed, "peak_favor": peak, "peak_pnl": peak_pnl})


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
    macro = tf_momentum(h1 or [], window=16, ema_f=9, ema_s=21)   # ~16h de contexto
    micro = tf_momentum(m5 or [], window=8, ema_f=5, ema_s=13)    # ~40min (pega a virada recente)
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


def _macro_h1_dir(symbol):
    """Direção do MACRO (H1) por EMA50×EMA200 — MESMA definição validada no backtest.
    Retorna 'alta' / 'baixa' / 'lateral', ou None se não der pra determinar (erro de
    infra → NÃO bloqueia, para não travar tudo por uma falha transitória de leitura)."""
    try:
        from services.crypto_service import get_candles
        from services.crypto_config import MACRO_TF
        h1, err = get_candles(symbol, MACRO_TF, 260)
        if err or not h1 or len(h1) < 205:
            return None
        import pandas as pd
        c = pd.Series([float(x["close"]) for x in h1])
        e50 = c.ewm(span=50, adjust=False).mean().iloc[-1]
        e200 = c.ewm(span=200, adjust=False).mean().iloc[-1]
        if e50 > e200:
            return "alta"
        if e50 < e200:
            return "baixa"
        return "lateral"
    except Exception as exc:
        logger.warning("crypto macro_h1 %s: %s", symbol, exc)
        return None


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


@crypto_bp.route("/api/crypto/runtime-alive")
def api_runtime_alive():
    """UI usa isso para NÃO competir com o auto do servidor (evita lock starvation)."""
    try:
        import json
        from pathlib import Path
        p = Path(__file__).resolve().parent.parent / "logs" / "hb_crypto_runtime.json"
        if not p.exists():
            return jsonify({"ok": True, "alive": False, "idade_s": None})
        d = json.loads(p.read_text(encoding="utf-8"))
        age = round(time.time() - float(d.get("ts") or 0), 1)
        return jsonify({
            "ok": True,
            "alive": age <= 90,
            "idade_s": age,
            "n_positions": len(d.get("positions") or []),
        })
    except Exception as exc:
        return jsonify({"ok": True, "alive": False, "error": str(exc)})


@crypto_bp.route("/api/crypto/go-scan")
def api_go_scan():
    """Diagnóstico: avalia GOs sem executar + near-miss (hora broker / janelas)."""
    from services.crypto_service import get_candles, mt5_execution_ready
    from services.crypto_config import active_trade_paths
    from services.crypto_orderflow import orderflow_tf
    from services.crypto_go_telemetry import summarize_near_miss, dry_probe_signals

    symbol = (request.args.get("symbol") or "BTCUSD").upper().strip()
    do_disc = str(request.args.get("discovery") or "0").strip() in ("1", "true", "yes")
    ready, ready_detail = mt5_execution_ready()
    # 800 barras bastam p/ setups; evita travar o worker com 2000×feature_engine
    m15, err15 = get_candles(symbol, 15, 800)
    of_tf = orderflow_tf(symbol)
    of_c, of_err = (None, None)
    if of_tf:
        of_c, of_err = get_candles(symbol, of_tf, 260)
    near = summarize_near_miss(symbol, m15)
    probes = dry_probe_signals(symbol, m15, of_c if not of_err else None,
                               include_discovery=do_disc)
    fires = [p for p in probes if p.get("status") == "FIRE"]
    return jsonify({
        "ok": True,
        "symbol": symbol,
        "mt5_ready": ready,
        "mt5_detail": ready_detail,
        "active_paths": active_trade_paths(symbol),
        "near_miss": near,
        "probes": probes,
        "would_fire": fires,
        "m15_err": err15,
        "of_err": of_err,
    })


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
        try:
            from services.crypto_telegram import notify_entry
            notify_entry(symbol, acao, result.get("price"), result.get("sl"),
                         result.get("tp"), volume, "manual")
        except Exception:
            pass
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
    # ── Motor legado (score/impulso/pullback) ──────────────────────────────
    # BTC/ETH estão em AUTO_BLOCKED (spread inviável no score M15), MAS os
    # caminhos GO (INSIDE_1015, ORDER_FLOW, …) rodam normalmente abaixo.
    # NÃO retornar aqui — senão o BTC nunca chega nos GOs e a UI mente
    # "vetado" como se o ativo inteiro estivesse morto.
    from services.crypto_config import auto_blocked, path_enabled
    from services.crypto_orderflow import (orderflow_tf, signal as _of_signal_live,
                                           london_handoff_enabled, london_handoff_signal)
    _of_tf = orderflow_tf(symbol)   # XAU M5 / ETH M15 (validado OOS) ou None
    if is_forex(symbol) and _is_weekend():
        return jsonify({"ok": True, "action": "NONE", "reason": "forex fechado (fim de semana)"})
    # ── TRAVA DE EXECUÇÃO por ativo: só UM auto-check por vez (atômico) ──
    # Runtime (servidor): blocking+timeout — tem prioridade operacional (não
    # depende do navegador). UI/painel: non-blocking — se o runtime (ou outro
    # tick) está no meio, devolve "execução em andamento" e o próximo ciclo tenta.
    _from_runtime = (
        str(b.get("source") or "").strip().lower() == "runtime"
        or (request.headers.get("X-Crypto-Runtime") or "").strip() == "1"
    )
    lk = _lock_for(symbol)
    if _from_runtime:
        if not lk.acquire(blocking=True, timeout=_RUNTIME_LOCK_TIMEOUT_SEC):
            return jsonify({"ok": True, "action": "NONE",
                            "reason": "execução em andamento (runtime timeout)"})
    elif not lk.acquire(blocking=False):
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

        # Pré-checagem: AutoTrading off → GOs disparam mas fill sempre 10027.
        # Bloqueia cedo com motivo claro no HB (evita "FAILED" opaco / "sem gatilho").
        try:
            from services.crypto_service import mt5_execution_ready
            _ready, _rdy_detail = mt5_execution_ready()
            if not _ready:
                from services.crypto_config import active_trade_paths
                return jsonify({
                    "ok": True, "action": "BLOCKED",
                    "reason": _rdy_detail,
                    "active_paths": active_trade_paths(symbol),
                })
        except Exception as _exc_rdy:
            logger.debug("mt5_execution_ready: %s", _exc_rdy)

        # ── CAMINHO ORDER_FLOW (validado OUT-OF-SAMPLE) — XAU M5 / ETH M15 ──
        # Roda no SEU timeframe (não no do painel). Não passa pelo filtro macro/ADX.
        # Se não disparar: cascade segue (XAU → LH/RND/…; ETH → INSIDE_H4).
        if _of_tf:
            of_candles, of_err = get_candles(symbol, _of_tf, 260)
            of = _of_signal_live(of_candles, symbol) if (not of_err and of_candles) else None
            if of:
                acao_of, sl_of, tp1_of, _tp2_of = of
                pos2, perr2 = get_positions(symbol)   # 2ª checagem antes de abrir
                if perr2 or pos2:
                    return jsonify({"ok": True, "action": "NONE", "reason": "posição já aberta (2ª checagem)"})
                st["last_ts"] = time.time()
                cfg_of = cfg_for(symbol)
                vol_of = st.get("volume") or float(cfg_of.get("volume", 0.1))
                ai_of = {"veredito": "ORDER_FLOW", "confianca": None, "ia_usada": False,
                         "motivo": f"[ORDER_FLOW M{_of_tf}] delta de fluxo + área de valor"}
                result, exerr = execute(symbol, acao_of, float(vol_of), sl_of, tp1_of)
                if result and not exerr:
                    _session["gerados"] += 1; _session["executados"] += 1
                    _last_ai[symbol] = {"veredito": "ORDER_FLOW", "confianca": None,
                                        "motivo": ai_of["motivo"], "acao": acao_of,
                                        "score": 8 if acao_of == "COMPRA" else -8, "ts": time.time()}
                    _log_entry(symbol, acao_of, result, 8 if acao_of == "COMPRA" else -8, ai_of)
                    try:
                        from services.crypto_telegram import notify_entry
                        notify_entry(symbol, acao_of, result.get("price"), sl_of, tp1_of,
                                     vol_of, "orderflow", ai_of["motivo"])
                    except Exception:
                        pass
                    return jsonify({"ok": True, "action": "EXECUTED", "acao": acao_of,
                                    "source": "orderflow", "result": result, "ai": ai_of})
                st["last_ts"] = 0.0
                return jsonify({
                    "ok": False, "action": "FAILED", "error": exerr,
                    "source": "orderflow",
                    "reason": f"GO orderflow disparou ({acao_of}) mas fill falhou: {exerr}",
                })
            # ORDER_FLOW não disparou — NÃO corta o cascade aqui.
            # ETH também tem INSIDE_H4 (GO v5); o veto do motor antigo só
            # bloqueia score/impulso/pullback (mais abaixo).
            pass

        # ── CAMINHO LONDON_HANDOFF (validado OOS: PF 2,07) — XAU, janela 09-11 M15 ──
        # Roda junto do ORDER_FLOW; competem pelo slot único de posição (1ª a disparar
        # abre). Só rompe o range asiático estreito com delta confirmando. Sem macro/ADX.
        from services.crypto_edge_setups import (
            us_drift_enabled, xau_us_drift_signal,
            rnd_fade_enabled, xau_rnd_fade_tight_signal,
            xau_rnd_fade_07_signal, xau_rnd_fade_mt_signal,
            xau_rnd_fade_065_signal, xau_rnd_fade_075_mt_signal,
            lh_047_enabled, lh_047_available_today, lh_047_mark_today,
            xau_lh_047_d18_signal,
            lh_049_enabled, lh_049_available_today, lh_049_mark_today,
            xau_lh_049_d19_signal,
            lh_048_enabled, lh_048_available_today, lh_048_mark_today,
            xau_lh_048_d19_signal,
            inside_h4_enabled, inside_h4_signal,
            inside_1015_enabled, inside_1015_signal,
            inside_1015_v13_enabled, inside_1015_v13_signal,
            ins_0918_v13_enabled, ins_0918_v13_signal,
            btc_nr5_1016_enabled, btc_nr5_1016_signal,
            btc_ins_1017_v13_enabled, btc_ins_1017_v13_signal,
            btc_ins_1117_v13_enabled, btc_ins_1117_v13_signal,
            btc_ins_1218_v13_enabled, btc_ins_1218_v13_signal,
            btc_ins_v135_0916_enabled, btc_ins_v135_0916_signal,
            btc_ins_0816_v13_enabled, btc_ins_0816_v13_signal,
            btc_nr5_0915_enabled, btc_nr5_0915_signal,
            btc_nr4_1016_enabled, btc_nr4_1016_signal,
            btc_h4_pb_ema_enabled, btc_h4_pb_ema_signal,
            btc_ins_24h_v13_enabled, btc_ins_24h_v13_signal,
            eth_imp_cont_24h_enabled, eth_imp_cont_24h_signal,
            btc_pdh_24h_enabled, btc_pdh_24h_signal,
            btc_pdh_24h_v13_enabled, btc_pdh_24h_v13_signal,
            btc_pdh_asia_0008_enabled, btc_pdh_asia_0008_signal,
            btc_pdh_night_2010_enabled, btc_pdh_night_2010_signal,
            btc_pdh_24h_mt_enabled, btc_pdh_24h_mt_signal,
            btc_nr4_24h_v14_enabled, btc_nr4_24h_v14_signal,
            btc_nr5_24h_v14_enabled, btc_nr5_24h_v14_signal,
            btc_nr5_1622_enabled, btc_nr5_1622_signal,
            btc_h4_pb_ema_mt24_enabled, btc_h4_pb_ema_mt24_signal,
            eth_ins_24h_mt_enabled, eth_ins_24h_mt_signal,
            eth_hl_24h_mt_enabled, eth_hl_24h_mt_signal,
            eth_imp_cont_24h_v18_enabled, eth_imp_cont_24h_v18_signal,
            eth_imp_tp25_24h_enabled, eth_imp_tp25_24h_signal,
            gbp_ins_am_v13_enabled, gbp_ins_am_v13_signal,
            gbp_ins_1017_v13_enabled, gbp_ins_1017_v13_signal,
            eur_lh_047_tp165_enabled, eur_lh_047_tp165_available_today,
            eur_lh_047_tp165_mark_today, eur_lh_047_tp165_signal,
            xau_ins_am_enabled, xau_ins_am_signal,
            xau_imp_cont_enabled, xau_imp_cont_20_signal,
            xau_nr4_1016_h4_enabled, xau_nr4_1016_h4_signal,
            xau_pdh_h4_enabled, xau_pdh_h4_signal,
            xau_pdh_1115_enabled, xau_pdh_1115_signal,
            xau_hl_24h_enabled, xau_hl_24h_signal,
            btc_pdh_h4_enabled, btc_pdh_h4_signal,
            eur_pdh_h4_enabled, eur_pdh_h4_signal,
            eur_imp_cont_24h_mt_enabled, eur_imp_cont_24h_mt_signal,
        )
        # Cache único M15 por ciclo: o cascade (esp. XAU) fazia ~20× copy_rates
        # (até 2000 barras). UI + runtime a cada ~45s → lock "execução em andamento"
        # e o ativo passava fome de avaliação completa.
        _m15_all, _m15_err = get_candles(symbol, 15, 2000)
        _m30_all, _m30_err = None, None
        _m60_all, _m60_err = None, None
        try:
            from services.crypto_v15_gos import v15_needs_m30
            from services.crypto_v16_gos import v16_needs_m30
            from services.crypto_v17_gos import v17_needs_m30
            from services.crypto_v18_gos import v18_needs_m30
            if (v15_needs_m30(symbol) or v16_needs_m30(symbol)
                    or v17_needs_m30(symbol) or v18_needs_m30(symbol)):
                _m30_all, _m30_err = get_candles(symbol, 30, 2000)
        except Exception:
            pass
        try:
            from services.crypto_v20_gos import v20_needs_m60
            if v20_needs_m60(symbol):
                _m60_all, _m60_err = get_candles(symbol, 60, 2000)
        except Exception:
            pass

        def _m15(n: int = 800):
            if _m15_err:
                return None, _m15_err
            if not _m15_all:
                return None, "sem candles M15"
            return (_m15_all if len(_m15_all) <= n else _m15_all[-n:]), None

        def _m30(n: int = 800):
            if _m30_err:
                return None, _m30_err
            if not _m30_all:
                return None, "sem candles M30"
            return (_m30_all if len(_m30_all) <= n else _m30_all[-n:]), None

        def _m60(n: int = 800):
            if _m60_err:
                return None, _m60_err
            if not _m60_all:
                return None, "sem candles M60"
            return (_m60_all if len(_m60_all) <= n else _m60_all[-n:]), None

        def _fire_tuple_go(tag, sig, motivo):
            """Executa GO que retorna (acao, sl, tp). None = não disparou / bloqueado."""
            if not sig:
                return None
            acao, sl, tp = sig
            # Caps live no sinal (execute() também aplica) — telegram/UI veem valores finais.
            try:
                from services.crypto_edge_setups import cap_live_signal, live_caps_for
                if live_caps_for(symbol):
                    _ref = None
                    if _m15_all:
                        _ref = float(_m15_all[-1].get("close")
                                     or _m15_all[-1].get("Close") or 0) or None
                    if _ref:
                        acao, sl, tp = cap_live_signal(symbol, (acao, sl, tp), _ref)
            except Exception:
                pass
            pos2, perr2 = get_positions(symbol)
            if perr2 or pos2:
                return jsonify({"ok": True, "action": "NONE",
                                "reason": "posição já aberta (2ª checagem)"})
            st["last_ts"] = time.time()
            cfg_g = cfg_for(symbol)
            vol_g = st.get("volume") or float(cfg_g.get("volume", 0.1))
            ai_g = {"veredito": tag, "confianca": None, "ia_usada": False, "motivo": motivo}
            result, exerr = execute(symbol, acao, float(vol_g), sl, tp)
            if result and not exerr:
                _session["gerados"] += 1
                _session["executados"] += 1
                sc = 8 if acao == "COMPRA" else -8
                _last_ai[symbol] = {"veredito": tag, "confianca": None,
                                    "motivo": motivo, "acao": acao,
                                    "score": sc, "ts": time.time()}
                _log_entry(symbol, acao, result, sc, ai_g)
                try:
                    from services.crypto_telegram import notify_entry
                    notify_entry(symbol, acao, result.get("price"),
                                 result.get("sl", sl), result.get("tp", tp),
                                 vol_g, tag.lower(), motivo)
                except Exception:
                    pass
                return jsonify({"ok": True, "action": "EXECUTED", "acao": acao,
                                "source": tag.lower(), "result": result, "ai": ai_g})
            st["last_ts"] = 0.0
            return jsonify({
                "ok": False, "action": "FAILED", "error": exerr,
                "source": tag.lower(),
                "reason": f"GO {tag} disparou ({acao}) mas fill falhou: {exerr}",
            })

        # Hora broker (última M15 fechada) — fora do pregão diurno prioriza paths 24h
        # para não enterrar cobertura noturna atrás de dezenas de MISS diurnos.
        _broker_hh = None
        try:
            from services.crypto_go_telemetry import broker_hour_from_candles
            _broker_hh = broker_hour_from_candles(_m15_all)
        except Exception:
            pass
        _off_hours = (_broker_hh is not None) and not (8.0 <= float(_broker_hh) < 19.0)

        def _run_go_list(items, tf=15):
            for _en, _sigfn, _tag, _mot in items:
                if _en(symbol):
                    if tf == 60:
                        _c, _e = _m60(800)
                    elif tf == 30:
                        _c, _e = _m30(800)
                    else:
                        _c, _e = _m15(800)
                    _sig = _sigfn(_c, symbol) if (not _e and _c) else None
                    if _sig:
                        _resp = _fire_tuple_go(_tag, _sig, _mot)
                        if _resp is not None:
                            return _resp
            return None

        # BTC v12 GOs (após NR5_1016 legado abaixo; ordem: NR seletivo → insides → PB)
        _btc_v12_day = [
            (btc_nr5_0915_enabled, btc_nr5_0915_signal, "BTC_NR5_0915",
             "[BTC_NR5_0915 M15 GO v12] NR5 + vol≥1.3× + H4 · 09–15h"),
            (btc_nr4_1016_enabled, btc_nr4_1016_signal, "BTC_NR4_1016",
             "[BTC_NR4_1016 M15 GO v12] NR4 + vol≥1.3× + H4 · 10–16h"),
            (btc_ins_1117_v13_enabled, btc_ins_1117_v13_signal, "BTC_INS_1117_V13",
             "[BTC_INS_1117_V13 M15 GO v12] inside vol≥1.3× + H4 · 11–17h"),
            (btc_ins_1218_v13_enabled, btc_ins_1218_v13_signal, "BTC_INS_1218_V13",
             "[BTC_INS_1218_V13 M15 GO v12] inside vol≥1.3× + H4 · 12–18h"),
            (btc_ins_v135_0916_enabled, btc_ins_v135_0916_signal, "BTC_INS_V135_0916",
             "[BTC_INS_V135_0916 M15 GO v12] inside vol≥1.35× + H4 · 09–16h"),
            (btc_ins_0816_v13_enabled, btc_ins_0816_v13_signal, "BTC_INS_0816_V13",
             "[BTC_INS_0816_V13 M15 GO v12] inside vol≥1.3× + H4 · 08–16h"),
            (btc_h4_pb_ema_enabled, btc_h4_pb_ema_signal, "BTC_H4_PB_EMA",
             "[BTC_H4_PB_EMA M15 GO v12] pullback EMA + bias H4 · 10–18h"),
            (btc_pdh_h4_enabled, btc_pdh_h4_signal, "BTC_PDH_H4",
             "[BTC_PDH_H4 M15 GO mega] PDH/PDL + vol≥1.2× + H4 · 10–16h"),
        ]
        _btc_v12_24h = [
            (btc_pdh_24h_enabled, btc_pdh_24h_signal, "BTC_PDH_24H",
             "[BTC_PDH_24H M15 GO v14] PDH/PDL + vol≥1.2× + H4 · 24h"),
            (btc_pdh_24h_v13_enabled, btc_pdh_24h_v13_signal, "BTC_PDH_24H_V13",
             "[BTC_PDH_24H_V13 M15 GO v14] PDH/PDL + vol≥1.3× + H4 · 24h"),
            (btc_pdh_asia_0008_enabled, btc_pdh_asia_0008_signal, "BTC_PDH_ASIA_0008",
             "[BTC_PDH_ASIA_0008 M15 GO v14] PDH/PDL + vol≥1.2× + H4 · 00–08h"),
            (btc_pdh_night_2010_enabled, btc_pdh_night_2010_signal, "BTC_PDH_NIGHT_2010",
             "[BTC_PDH_NIGHT_2010 M15 GO v14] PDH/PDL + vol≥1.2× + H4 · 20–10h"),
            (btc_nr4_24h_v14_enabled, btc_nr4_24h_v14_signal, "BTC_NR4_24H_V14",
             "[BTC_NR4_24H_V14 M15 GO v14] NR4 + vol≥1.4× + H4 · 24h"),
            (btc_nr5_24h_v14_enabled, btc_nr5_24h_v14_signal, "BTC_NR5_24H_V14",
             "[BTC_NR5_24H_V14 M15 GO v14] NR5 + vol≥1.4× + H4 · 24h"),
            (btc_pdh_24h_mt_enabled, btc_pdh_24h_mt_signal, "BTC_PDH_24H_MT",
             "[BTC_PDH_24H_MT M15 GO v14] PDH/PDL · 24h · seg–qui"),
            (btc_h4_pb_ema_mt24_enabled, btc_h4_pb_ema_mt24_signal, "BTC_H4_PB_EMA_MT24",
             "[BTC_H4_PB_EMA_MT24 M15 GO v14] pullback EMA + H4 · 24h · seg–qui"),
            (btc_nr5_1622_enabled, btc_nr5_1622_signal, "BTC_NR5_1622",
             "[BTC_NR5_1622 M15 GO v14] NR5 + vol≥1.3× + H4 · 16–22h"),
            (btc_ins_24h_v13_enabled, btc_ins_24h_v13_signal, "BTC_INS_24H_V13",
             "[BTC_INS_24H_V13 M15 GO v13] inside vol≥1.3× + H4 · 24h"),
        ]
        _mega_gaps_day = [
            (xau_nr4_1016_h4_enabled, xau_nr4_1016_h4_signal, "XAU_NR4_1016_H4",
             "[XAU_NR4_1016_H4 M15 GO mega] NR4 + vol≥1.3× + H4 · 10–16h"),
            (xau_pdh_h4_enabled, xau_pdh_h4_signal, "XAU_PDH_H4",
             "[XAU_PDH_H4 M15 GO mega] PDH/PDL + vol≥1.2× + H4 · 10–16h"),
            (xau_pdh_1115_enabled, xau_pdh_1115_signal, "XAU_PDH_1115",
             "[XAU_PDH_1115 M15 GO mega] PDH/PDL + vol≥1.2× + H4 · 11–15h"),
            (eur_pdh_h4_enabled, eur_pdh_h4_signal, "EUR_PDH_H4",
             "[EUR_PDH_H4 M15 GO mega] PDH/PDL + vol≥1.2× + H4 · 10–16h"),
        ]
        _mega_gaps_24h = [
            (xau_hl_24h_enabled, xau_hl_24h_signal, "XAU_HL_24H",
             "[XAU_HL_24H M15 GO mega] higher-low/LH + vol≥1.3× + H4 · 24h"),
            (eur_imp_cont_24h_mt_enabled, eur_imp_cont_24h_mt_signal, "EUR_IMP_CONT_24H_MT",
             "[EUR_IMP_CONT_24H_MT M15 GO yellow] impulso ATR×2 + vol · 24h · seg–qui"),
        ]
        _eth_24h = [
            (eth_ins_24h_mt_enabled, eth_ins_24h_mt_signal, "ETH_INS_24H_MT",
             "[ETH_INS_24H_MT M15 GO v14] inside vol≥1.3× + H4 · 24h · seg–qui"),
            (eth_hl_24h_mt_enabled, eth_hl_24h_mt_signal, "ETH_HL_24H_MT",
             "[ETH_HL_24H_MT M15 GO v14] HL/LH + vol≥1.3× + H4 · 24h · seg–qui"),
            (eth_imp_cont_24h_v18_enabled, eth_imp_cont_24h_v18_signal, "ETH_IMP_CONT_24H_V18",
             "[ETH_IMP_CONT_24H_V18 M15 GO v14] impulso ATR×1.8 + vol · 24h"),
            (eth_imp_tp25_24h_enabled, eth_imp_tp25_24h_signal, "ETH_IMP_TP25_24H",
             "[ETH_IMP_TP25_24H M15 GO v14] impulso ATR×2 · TP 2.5R · 24h"),
            (eth_imp_cont_24h_enabled, eth_imp_cont_24h_signal, "ETH_IMP_CONT_24H",
             "[ETH_IMP_CONT_24H M15 GO v13] continuação impulso ATR×2 + vol · 24h"),
        ]

        # v15 GOs (37) — M15 por net; M30 separado (candles M30)
        try:
            from services.crypto_v15_gos import v15_cascade_items
            _v15_m15 = v15_cascade_items(symbol, tf=15)
            _v15_m30 = v15_cascade_items(symbol, tf=30)
        except Exception:
            _v15_m15, _v15_m30 = [], []
        # v16 GOs (27) — XAU/EUR/GBP expand
        try:
            from services.crypto_v16_gos import v16_cascade_items
            _v16_m15 = v16_cascade_items(symbol, tf=15)
            _v16_m30 = v16_cascade_items(symbol, tf=30)
        except Exception:
            _v16_m15, _v16_m30 = [], []
        # v17 GOs (17) — ETH/EUR expand (GBP sem novos)
        try:
            from services.crypto_v17_gos import v17_cascade_items
            _v17_m15 = v17_cascade_items(symbol, tf=15)
            _v17_m30 = v17_cascade_items(symbol, tf=30)
        except Exception:
            _v17_m15, _v17_m30 = [], []
        # v18 GOs (3) — ETH EMA reclaim / VWAP cont
        try:
            from services.crypto_v18_gos import v18_cascade_items
            _v18_m15 = v18_cascade_items(symbol, tf=15)
            _v18_m30 = v18_cascade_items(symbol, tf=30)
        except Exception:
            _v18_m15, _v18_m30 = [], []
        # v20 GOs (3) — RSI reclaim M60 + BB fade NY
        try:
            from services.crypto_v20_gos import v20_cascade_items
            _v20_m15 = v20_cascade_items(symbol, tf=15)
            _v20_m60 = v20_cascade_items(symbol, tf=60)
        except Exception:
            _v20_m15, _v20_m60 = [], []

        # Off-hours: 24h primeiro (cobertura noite/FDS). Diurno: day → 24h (prioridade sessão).
        # v20/v18/v17/v16/v15 entram cedo; M30/M60 depois dos M15 clássicos.
        if _off_hours:
            _ord = (_v20_m15, _v18_m15, _v17_m15, _v16_m15, _v15_m15, _btc_v12_24h, _eth_24h, _mega_gaps_24h,
                    _v20_m60, _v18_m30, _v17_m30, _v16_m30, _v15_m30, _btc_v12_day, _mega_gaps_day)
        else:
            _ord = (_btc_v12_day, _v20_m15, _v18_m15, _v17_m15, _v16_m15, _v15_m15, _btc_v12_24h, _eth_24h,
                    _mega_gaps_day, _mega_gaps_24h, _v20_m60, _v18_m30, _v17_m30, _v16_m30, _v15_m30)
        for _block in _ord:
            if not _block:
                continue
            # TF por bloco (M15 default; M30/M60 por registry)
            if _block is _v20_m60:
                _tf = 60
            elif (_block is _v15_m30 or _block is _v16_m30
                  or _block is _v17_m30 or _block is _v18_m30):
                _tf = 30
            else:
                _tf = 15
            _r = _run_go_list(_block, tf=_tf)
            if _r is not None:
                return _r

        if london_handoff_enabled(symbol):
            lh_candles, lh_err = _m15(260)
            lh = london_handoff_signal(lh_candles, symbol) if (not lh_err and lh_candles) else None
            if lh:
                acao_lh, sl_lh, tp1_lh, _tp2 = lh
                pos2, perr2 = get_positions(symbol)
                if perr2 or pos2:
                    return jsonify({"ok": True, "action": "NONE", "reason": "posição já aberta (2ª checagem)"})
                st["last_ts"] = time.time()
                cfg_lh = cfg_for(symbol)
                vol_lh = st.get("volume") or float(cfg_lh.get("volume", 0.1))
                ai_lh = {"veredito": "LONDON_HANDOFF", "confianca": None, "ia_usada": False,
                         "motivo": "[LONDON_HANDOFF M15] rompimento do range asiático + delta"}
                result, exerr = execute(symbol, acao_lh, float(vol_lh), sl_lh, tp1_lh)
                if result and not exerr:
                    _session["gerados"] += 1; _session["executados"] += 1
                    _last_ai[symbol] = {"veredito": "LONDON_HANDOFF", "confianca": None,
                                        "motivo": ai_lh["motivo"], "acao": acao_lh,
                                        "score": 8 if acao_lh == "COMPRA" else -8, "ts": time.time()}
                    _log_entry(symbol, acao_lh, result, 8 if acao_lh == "COMPRA" else -8, ai_lh)
                    try:
                        from services.crypto_telegram import notify_entry
                        notify_entry(symbol, acao_lh, result.get("price"), sl_lh, tp1_lh,
                                     vol_lh, "london_handoff", ai_lh["motivo"])
                    except Exception:
                        pass
                    return jsonify({"ok": True, "action": "EXECUTED", "acao": acao_lh,
                                    "source": "london_handoff", "result": result, "ai": ai_lh})
                st["last_ts"] = 0.0
                return jsonify({"ok": False, "action": "FAILED", "error": exerr})

        # ── CAMINHO EUR_LH_047_TP165 (🟢 GO v10) — EUR M15 · 1/dia · TP 1,65× ──
        if eur_lh_047_tp165_enabled(symbol) and eur_lh_047_tp165_available_today(symbol):
            elh_candles, elh_err = _m15(260)
            elh = eur_lh_047_tp165_signal(elh_candles, symbol) if (not elh_err and elh_candles) else None
            if elh:
                acao_elh, sl_elh, tp_elh = elh
                pos2, perr2 = get_positions(symbol)
                if perr2 or pos2:
                    return jsonify({"ok": True, "action": "NONE", "reason": "posição já aberta (2ª checagem)"})
                st["last_ts"] = time.time()
                cfg_elh = cfg_for(symbol)
                vol_elh = st.get("volume") or float(cfg_elh.get("volume", 0.1))
                tag = "EUR_LH_047_TP165"
                ai_elh = {"veredito": tag, "confianca": None, "ia_usada": False,
                          "motivo": "[EUR_LH_047_TP165 M15 GO v10] asia≤0.47 · δ≥0.18 · TP 1.65×"}
                result, exerr = execute(symbol, acao_elh, float(vol_elh), sl_elh, tp_elh)
                if result and not exerr:
                    eur_lh_047_tp165_mark_today(symbol)
                    _session["gerados"] += 1; _session["executados"] += 1
                    sc = 8 if acao_elh == "COMPRA" else -8
                    _last_ai[symbol] = {"veredito": tag, "confianca": None,
                                        "motivo": ai_elh["motivo"], "acao": acao_elh,
                                        "score": sc, "ts": time.time()}
                    _log_entry(symbol, acao_elh, result, sc, ai_elh)
                    try:
                        from services.crypto_telegram import notify_entry
                        notify_entry(symbol, acao_elh, result.get("price"), sl_elh, tp_elh,
                                     vol_elh, tag.lower(), ai_elh["motivo"])
                    except Exception:
                        pass
                    return jsonify({"ok": True, "action": "EXECUTED", "acao": acao_elh,
                                    "source": tag.lower(), "result": result, "ai": ai_elh})
                st["last_ts"] = 0.0
                return jsonify({"ok": False, "action": "FAILED", "error": exerr})

        # ── CAMINHO LH_047_D18 (🟢 GO v4 23/07) — XAU M15 · 1/dia ──
        if lh_047_enabled(symbol) and lh_047_available_today(symbol):
            lh4_candles, lh4_err = _m15(260)
            lh4 = xau_lh_047_d18_signal(lh4_candles, symbol) if (not lh4_err and lh4_candles) else None
            if lh4:
                acao_lh4, sl_lh4, tp_lh4 = lh4
                pos2, perr2 = get_positions(symbol)
                if perr2 or pos2:
                    return jsonify({"ok": True, "action": "NONE", "reason": "posição já aberta (2ª checagem)"})
                st["last_ts"] = time.time()
                cfg_lh4 = cfg_for(symbol)
                vol_lh4 = st.get("volume") or float(cfg_lh4.get("volume", 0.1))
                ai_lh4 = {"veredito": "LH_047_D18", "confianca": None, "ia_usada": False,
                          "motivo": "[LH_047_D18 M15 GO v4] asia≤0.47 · delta≥0.18 · TP 1.75×"}
                result, exerr = execute(symbol, acao_lh4, float(vol_lh4), sl_lh4, tp_lh4)
                if result and not exerr:
                    lh_047_mark_today(symbol)
                    _session["gerados"] += 1; _session["executados"] += 1
                    sc = 8 if acao_lh4 == "COMPRA" else -8
                    _last_ai[symbol] = {"veredito": "LH_047_D18", "confianca": None,
                                        "motivo": ai_lh4["motivo"], "acao": acao_lh4,
                                        "score": sc, "ts": time.time()}
                    _log_entry(symbol, acao_lh4, result, sc, ai_lh4)
                    try:
                        from services.crypto_telegram import notify_entry
                        notify_entry(symbol, acao_lh4, result.get("price"), sl_lh4, tp_lh4,
                                     vol_lh4, "lh_047_d18", ai_lh4["motivo"])
                    except Exception:
                        pass
                    return jsonify({"ok": True, "action": "EXECUTED", "acao": acao_lh4,
                                    "source": "lh_047_d18", "result": result, "ai": ai_lh4})
                st["last_ts"] = 0.0
                return jsonify({"ok": False, "action": "FAILED", "error": exerr})

        # ── CAMINHO LH_049_D19 (🟢 GO v5 23/07) — XAU M15 · 1/dia ──
        if lh_049_enabled(symbol) and lh_049_available_today(symbol):
            lh5_candles, lh5_err = _m15(260)
            lh5 = xau_lh_049_d19_signal(lh5_candles, symbol) if (not lh5_err and lh5_candles) else None
            if lh5:
                acao_lh5, sl_lh5, tp_lh5 = lh5
                pos2, perr2 = get_positions(symbol)
                if perr2 or pos2:
                    return jsonify({"ok": True, "action": "NONE", "reason": "posição já aberta (2ª checagem)"})
                st["last_ts"] = time.time()
                cfg_lh5 = cfg_for(symbol)
                vol_lh5 = st.get("volume") or float(cfg_lh5.get("volume", 0.1))
                ai_lh5 = {"veredito": "LH_049_D19", "confianca": None, "ia_usada": False,
                          "motivo": "[LH_049_D19 M15 GO v5] asia≤0.49 · delta≥0.19 · TP 1.75×"}
                result, exerr = execute(symbol, acao_lh5, float(vol_lh5), sl_lh5, tp_lh5)
                if result and not exerr:
                    lh_049_mark_today(symbol)
                    _session["gerados"] += 1; _session["executados"] += 1
                    sc = 8 if acao_lh5 == "COMPRA" else -8
                    _last_ai[symbol] = {"veredito": "LH_049_D19", "confianca": None,
                                        "motivo": ai_lh5["motivo"], "acao": acao_lh5,
                                        "score": sc, "ts": time.time()}
                    _log_entry(symbol, acao_lh5, result, sc, ai_lh5)
                    try:
                        from services.crypto_telegram import notify_entry
                        notify_entry(symbol, acao_lh5, result.get("price"), sl_lh5, tp_lh5,
                                     vol_lh5, "lh_049_d19", ai_lh5["motivo"])
                    except Exception:
                        pass
                    return jsonify({"ok": True, "action": "EXECUTED", "acao": acao_lh5,
                                    "source": "lh_049_d19", "result": result, "ai": ai_lh5})
                st["last_ts"] = 0.0
                return jsonify({"ok": False, "action": "FAILED", "error": exerr})

        # ── CAMINHO LH_048_D19 (🟢 GO v6 23/07) — XAU M15 · 1/dia ──
        if lh_048_enabled(symbol) and lh_048_available_today(symbol):
            lh6_candles, lh6_err = _m15(260)
            lh6 = xau_lh_048_d19_signal(lh6_candles, symbol) if (not lh6_err and lh6_candles) else None
            if lh6:
                acao_lh6, sl_lh6, tp_lh6 = lh6
                pos2, perr2 = get_positions(symbol)
                if perr2 or pos2:
                    return jsonify({"ok": True, "action": "NONE", "reason": "posição já aberta (2ª checagem)"})
                st["last_ts"] = time.time()
                cfg_lh6 = cfg_for(symbol)
                vol_lh6 = st.get("volume") or float(cfg_lh6.get("volume", 0.1))
                ai_lh6 = {"veredito": "LH_048_D19", "confianca": None, "ia_usada": False,
                          "motivo": "[LH_048_D19 M15 GO v6] asia≤0.48 · delta≥0.19 · TP 1.75×"}
                result, exerr = execute(symbol, acao_lh6, float(vol_lh6), sl_lh6, tp_lh6)
                if result and not exerr:
                    lh_048_mark_today(symbol)
                    _session["gerados"] += 1; _session["executados"] += 1
                    sc = 8 if acao_lh6 == "COMPRA" else -8
                    _last_ai[symbol] = {"veredito": "LH_048_D19", "confianca": None,
                                        "motivo": ai_lh6["motivo"], "acao": acao_lh6,
                                        "score": sc, "ts": time.time()}
                    _log_entry(symbol, acao_lh6, result, sc, ai_lh6)
                    try:
                        from services.crypto_telegram import notify_entry
                        notify_entry(symbol, acao_lh6, result.get("price"), sl_lh6, tp_lh6,
                                     vol_lh6, "lh_048_d19", ai_lh6["motivo"])
                    except Exception:
                        pass
                    return jsonify({"ok": True, "action": "EXECUTED", "acao": acao_lh6,
                                    "source": "lh_048_d19", "result": result, "ai": ai_lh6})
                st["last_ts"] = 0.0
                return jsonify({"ok": False, "action": "FAILED", "error": exerr})

        # ── CAMINHO XAU_IMP_CONT_20 (🟢 GO v11) — impulso ATR×2 + vol · 09–18 ──
        if xau_imp_cont_enabled(symbol):
            ic_candles, ic_err = _m15(800)
            ic = xau_imp_cont_20_signal(ic_candles, symbol) if (not ic_err and ic_candles) else None
            if ic:
                acao_ic, sl_ic, tp_ic = ic
                pos2, perr2 = get_positions(symbol)
                if perr2 or pos2:
                    return jsonify({"ok": True, "action": "NONE", "reason": "posição já aberta (2ª checagem)"})
                st["last_ts"] = time.time()
                cfg_ic = cfg_for(symbol)
                vol_ic = st.get("volume") or float(cfg_ic.get("volume", 0.1))
                tag = "XAU_IMP_CONT_20"
                ai_ic = {"veredito": tag, "confianca": None, "ia_usada": False,
                         "motivo": f"[{tag} M15 GO v11] continuação após impulso ATR×2 + vol"}
                result, exerr = execute(symbol, acao_ic, float(vol_ic), sl_ic, tp_ic)
                if result and not exerr:
                    _session["gerados"] += 1; _session["executados"] += 1
                    sc = 8 if acao_ic == "COMPRA" else -8
                    _last_ai[symbol] = {"veredito": tag, "confianca": None,
                                        "motivo": ai_ic["motivo"], "acao": acao_ic,
                                        "score": sc, "ts": time.time()}
                    _log_entry(symbol, acao_ic, result, sc, ai_ic)
                    try:
                        from services.crypto_telegram import notify_entry
                        notify_entry(symbol, acao_ic, result.get("price"), sl_ic, tp_ic,
                                     vol_ic, tag.lower(), ai_ic["motivo"])
                    except Exception:
                        pass
                    return jsonify({"ok": True, "action": "EXECUTED", "acao": acao_ic,
                                    "source": tag.lower(), "result": result, "ai": ai_ic})
                st["last_ts"] = 0.0
                return jsonify({"ok": False, "action": "FAILED", "error": exerr})

        # ── CAMINHO XAU_INS_AM (🟢 GO v6) — XAU M15 · 09–12 ──
        if xau_ins_am_enabled(symbol):
            am_candles, am_err = _m15(800)
            am = xau_ins_am_signal(am_candles, symbol) if (not am_err and am_candles) else None
            if am:
                acao_am, sl_am, tp_am = am
                pos2, perr2 = get_positions(symbol)
                if perr2 or pos2:
                    return jsonify({"ok": True, "action": "NONE", "reason": "posição já aberta (2ª checagem)"})
                st["last_ts"] = time.time()
                cfg_am = cfg_for(symbol)
                vol_am = st.get("volume") or float(cfg_am.get("volume", 0.1))
                ai_am = {"veredito": "XAU_INS_AM", "confianca": None, "ia_usada": False,
                         "motivo": "[XAU_INS_AM M15 GO v6] inside H4 · 09–12"}
                result, exerr = execute(symbol, acao_am, float(vol_am), sl_am, tp_am)
                if result and not exerr:
                    _session["gerados"] += 1; _session["executados"] += 1
                    sc = 8 if acao_am == "COMPRA" else -8
                    _last_ai[symbol] = {"veredito": "XAU_INS_AM", "confianca": None,
                                        "motivo": ai_am["motivo"], "acao": acao_am,
                                        "score": sc, "ts": time.time()}
                    _log_entry(symbol, acao_am, result, sc, ai_am)
                    try:
                        from services.crypto_telegram import notify_entry
                        notify_entry(symbol, acao_am, result.get("price"), sl_am, tp_am,
                                     vol_am, "xau_ins_am", ai_am["motivo"])
                    except Exception:
                        pass
                    return jsonify({"ok": True, "action": "EXECUTED", "acao": acao_am,
                                    "source": "xau_ins_am", "result": result, "ai": ai_am})
                st["last_ts"] = 0.0
                return jsonify({"ok": False, "action": "FAILED", "error": exerr})

        # ── CAMINHO INSIDE_H4 (🟢 GO v5) — XAU / ETH M15 ──
        if inside_h4_enabled(symbol):
            ih_candles, ih_err = _m15(800)
            ih = inside_h4_signal(ih_candles, symbol) if (not ih_err and ih_candles) else None
            if ih:
                acao_ih, sl_ih, tp_ih = ih
                pos2, perr2 = get_positions(symbol)
                if perr2 or pos2:
                    return jsonify({"ok": True, "action": "NONE", "reason": "posição já aberta (2ª checagem)"})
                st["last_ts"] = time.time()
                cfg_ih = cfg_for(symbol)
                vol_ih = st.get("volume") or float(cfg_ih.get("volume", 0.1))
                tag = f"{symbol.split('USD')[0]}_INSIDE_H4" if "USD" in symbol else "INSIDE_H4"
                if symbol.upper() == "XAUUSD":
                    tag = "XAU_INSIDE_H4"
                elif symbol.upper() == "ETHUSD":
                    tag = "ETH_INSIDE_H4"
                ai_ih = {"veredito": tag, "confianca": None, "ia_usada": False,
                         "motivo": f"[{tag} M15 GO v5] inside bar + bias H4"}
                result, exerr = execute(symbol, acao_ih, float(vol_ih), sl_ih, tp_ih)
                if result and not exerr:
                    _session["gerados"] += 1; _session["executados"] += 1
                    sc = 8 if acao_ih == "COMPRA" else -8
                    _last_ai[symbol] = {"veredito": tag, "confianca": None,
                                        "motivo": ai_ih["motivo"], "acao": acao_ih,
                                        "score": sc, "ts": time.time()}
                    _log_entry(symbol, acao_ih, result, sc, ai_ih)
                    try:
                        from services.crypto_telegram import notify_entry
                        notify_entry(symbol, acao_ih, result.get("price"), sl_ih, tp_ih,
                                     vol_ih, tag.lower(), ai_ih["motivo"])
                    except Exception:
                        pass
                    return jsonify({"ok": True, "action": "EXECUTED", "acao": acao_ih,
                                    "source": tag.lower(), "result": result, "ai": ai_ih})
                st["last_ts"] = 0.0
                return jsonify({"ok": False, "action": "FAILED", "error": exerr})

        # ── CAMINHO BTC_NR5_1016 (🟢 GO v11) — BTC M15 · NR5 + H4 · 10–16h ──
        if btc_nr5_1016_enabled(symbol):
            nr_candles, nr_err = _m15(800)
            nr = btc_nr5_1016_signal(nr_candles, symbol) if (not nr_err and nr_candles) else None
            if nr:
                acao_nr, sl_nr, tp_nr = nr
                pos2, perr2 = get_positions(symbol)
                if perr2 or pos2:
                    return jsonify({"ok": True, "action": "NONE", "reason": "posição já aberta (2ª checagem)"})
                st["last_ts"] = time.time()
                cfg_nr = cfg_for(symbol)
                vol_nr = st.get("volume") or float(cfg_nr.get("volume", 0.1))
                tag = "BTC_NR5_1016"
                ai_nr = {"veredito": tag, "confianca": None, "ia_usada": False,
                         "motivo": f"[{tag} M15 GO v11] NR5 + vol≥1.3× + H4 · 10–16h"}
                result, exerr = execute(symbol, acao_nr, float(vol_nr), sl_nr, tp_nr)
                if result and not exerr:
                    _session["gerados"] += 1; _session["executados"] += 1
                    sc = 8 if acao_nr == "COMPRA" else -8
                    _last_ai[symbol] = {"veredito": tag, "confianca": None,
                                        "motivo": ai_nr["motivo"], "acao": acao_nr,
                                        "score": sc, "ts": time.time()}
                    _log_entry(symbol, acao_nr, result, sc, ai_nr)
                    try:
                        from services.crypto_telegram import notify_entry
                        notify_entry(symbol, acao_nr, result.get("price"), sl_nr, tp_nr,
                                     vol_nr, tag.lower(), ai_nr["motivo"])
                    except Exception:
                        pass
                    return jsonify({"ok": True, "action": "EXECUTED", "acao": acao_nr,
                                    "source": tag.lower(), "result": result, "ai": ai_nr})
                st["last_ts"] = 0.0
                return jsonify({"ok": False, "action": "FAILED", "error": exerr})

        # ── CAMINHO INSIDE_1015_V13 (🟢 GO v10) — BTC M15 · vol 1,3× · 10–15h ──
        if inside_1015_v13_enabled(symbol):
            iv_candles, iv_err = _m15(800)
            iv = inside_1015_v13_signal(iv_candles, symbol) if (not iv_err and iv_candles) else None
            if iv:
                acao_iv, sl_iv, tp_iv = iv
                pos2, perr2 = get_positions(symbol)
                if perr2 or pos2:
                    return jsonify({"ok": True, "action": "NONE", "reason": "posição já aberta (2ª checagem)"})
                st["last_ts"] = time.time()
                cfg_iv = cfg_for(symbol)
                vol_iv = st.get("volume") or float(cfg_iv.get("volume", 0.1))
                tag = "BTC_INS_1015_V13"
                ai_iv = {"veredito": tag, "confianca": None, "ia_usada": False,
                         "motivo": f"[{tag} M15 GO v10] inside vol≥1.3× + H4 · 10–15h"}
                result, exerr = execute(symbol, acao_iv, float(vol_iv), sl_iv, tp_iv)
                if result and not exerr:
                    _session["gerados"] += 1; _session["executados"] += 1
                    sc = 8 if acao_iv == "COMPRA" else -8
                    _last_ai[symbol] = {"veredito": tag, "confianca": None,
                                        "motivo": ai_iv["motivo"], "acao": acao_iv,
                                        "score": sc, "ts": time.time()}
                    _log_entry(symbol, acao_iv, result, sc, ai_iv)
                    try:
                        from services.crypto_telegram import notify_entry
                        notify_entry(symbol, acao_iv, result.get("price"), sl_iv, tp_iv,
                                     vol_iv, tag.lower(), ai_iv["motivo"])
                    except Exception:
                        pass
                    return jsonify({"ok": True, "action": "EXECUTED", "acao": acao_iv,
                                    "source": tag.lower(), "result": result, "ai": ai_iv})
                st["last_ts"] = 0.0
                return jsonify({"ok": False, "action": "FAILED", "error": exerr})

        # ── CAMINHO INS_0918_V13 (🟢 GO v11) — BTC/ETH · inside vol1.3 · 09–18h ──
        if ins_0918_v13_enabled(symbol):
            i918_candles, i918_err = _m15(800)
            i918 = ins_0918_v13_signal(i918_candles, symbol) if (not i918_err and i918_candles) else None
            if i918:
                acao_i918, sl_i918, tp_i918 = i918
                pos2, perr2 = get_positions(symbol)
                if perr2 or pos2:
                    return jsonify({"ok": True, "action": "NONE", "reason": "posição já aberta (2ª checagem)"})
                st["last_ts"] = time.time()
                cfg_i918 = cfg_for(symbol)
                vol_i918 = st.get("volume") or float(cfg_i918.get("volume", 0.1))
                tag = "INS_0918_V13"
                ai_i918 = {"veredito": tag, "confianca": None, "ia_usada": False,
                           "motivo": f"[{tag} M15 GO v11] inside vol≥1.3× + H4 · 09–18h"}
                result, exerr = execute(symbol, acao_i918, float(vol_i918), sl_i918, tp_i918)
                if result and not exerr:
                    _session["gerados"] += 1; _session["executados"] += 1
                    sc = 8 if acao_i918 == "COMPRA" else -8
                    _last_ai[symbol] = {"veredito": tag, "confianca": None,
                                        "motivo": ai_i918["motivo"], "acao": acao_i918,
                                        "score": sc, "ts": time.time()}
                    _log_entry(symbol, acao_i918, result, sc, ai_i918)
                    try:
                        from services.crypto_telegram import notify_entry
                        notify_entry(symbol, acao_i918, result.get("price"), sl_i918, tp_i918,
                                     vol_i918, tag.lower(), ai_i918["motivo"])
                    except Exception:
                        pass
                    return jsonify({"ok": True, "action": "EXECUTED", "acao": acao_i918,
                                    "source": tag.lower(), "result": result, "ai": ai_i918})
                st["last_ts"] = 0.0
                return jsonify({"ok": False, "action": "FAILED", "error": exerr})

        # ── CAMINHO BTC_INS_1017_V13 (🟢 GO v11) — BTC · inside vol1.3 · 10–17h ──
        if btc_ins_1017_v13_enabled(symbol):
            i17_candles, i17_err = _m15(800)
            i17 = btc_ins_1017_v13_signal(i17_candles, symbol) if (not i17_err and i17_candles) else None
            if i17:
                acao_i17, sl_i17, tp_i17 = i17
                pos2, perr2 = get_positions(symbol)
                if perr2 or pos2:
                    return jsonify({"ok": True, "action": "NONE", "reason": "posição já aberta (2ª checagem)"})
                st["last_ts"] = time.time()
                cfg_i17 = cfg_for(symbol)
                vol_i17 = st.get("volume") or float(cfg_i17.get("volume", 0.1))
                tag = "BTC_INS_1017_V13"
                ai_i17 = {"veredito": tag, "confianca": None, "ia_usada": False,
                          "motivo": f"[{tag} M15 GO v11] inside vol≥1.3× + H4 · 10–17h"}
                result, exerr = execute(symbol, acao_i17, float(vol_i17), sl_i17, tp_i17)
                if result and not exerr:
                    _session["gerados"] += 1; _session["executados"] += 1
                    sc = 8 if acao_i17 == "COMPRA" else -8
                    _last_ai[symbol] = {"veredito": tag, "confianca": None,
                                        "motivo": ai_i17["motivo"], "acao": acao_i17,
                                        "score": sc, "ts": time.time()}
                    _log_entry(symbol, acao_i17, result, sc, ai_i17)
                    try:
                        from services.crypto_telegram import notify_entry
                        notify_entry(symbol, acao_i17, result.get("price"), sl_i17, tp_i17,
                                     vol_i17, tag.lower(), ai_i17["motivo"])
                    except Exception:
                        pass
                    return jsonify({"ok": True, "action": "EXECUTED", "acao": acao_i17,
                                    "source": tag.lower(), "result": result, "ai": ai_i17})
                st["last_ts"] = 0.0
                return jsonify({"ok": False, "action": "FAILED", "error": exerr})

        # ── CAMINHO INSIDE_1015 (🟢 GO v8) — ETH / BTC M15 · 10–15h ──
        if inside_1015_enabled(symbol):
            i15_candles, i15_err = _m15(800)
            i15 = inside_1015_signal(i15_candles, symbol) if (not i15_err and i15_candles) else None
            if i15:
                acao_i15, sl_i15, tp_i15 = i15
                pos2, perr2 = get_positions(symbol)
                if perr2 or pos2:
                    return jsonify({"ok": True, "action": "NONE", "reason": "posição já aberta (2ª checagem)"})
                st["last_ts"] = time.time()
                cfg_i15 = cfg_for(symbol)
                vol_i15 = st.get("volume") or float(cfg_i15.get("volume", 0.1))
                tag = "ETH_INS_1015" if symbol.upper() == "ETHUSD" else (
                    "BTC_INS_1015" if symbol.upper() == "BTCUSD" else "INSIDE_1015")
                ai_i15 = {"veredito": tag, "confianca": None, "ia_usada": False,
                          "motivo": f"[{tag} M15 GO v8] inside + H4 · 10–15h"}
                result, exerr = execute(symbol, acao_i15, float(vol_i15), sl_i15, tp_i15)
                if result and not exerr:
                    _session["gerados"] += 1; _session["executados"] += 1
                    sc = 8 if acao_i15 == "COMPRA" else -8
                    _last_ai[symbol] = {"veredito": tag, "confianca": None,
                                        "motivo": ai_i15["motivo"], "acao": acao_i15,
                                        "score": sc, "ts": time.time()}
                    _log_entry(symbol, acao_i15, result, sc, ai_i15)
                    try:
                        from services.crypto_telegram import notify_entry
                        notify_entry(symbol, acao_i15, result.get("price"), sl_i15, tp_i15,
                                     vol_i15, tag.lower(), ai_i15["motivo"])
                    except Exception:
                        pass
                    return jsonify({"ok": True, "action": "EXECUTED", "acao": acao_i15,
                                    "source": tag.lower(), "result": result, "ai": ai_i15})
                st["last_ts"] = 0.0
                return jsonify({"ok": False, "action": "FAILED", "error": exerr})

        # ── CAMINHO GBP_INS_AM_V13 (🟢 GO v9) — GBP M15 · 09–12 · vol 1,3× ──
        if gbp_ins_am_v13_enabled(symbol):
            gav_candles, gav_err = _m15(800)
            gav = gbp_ins_am_v13_signal(gav_candles, symbol) if (not gav_err and gav_candles) else None
            if gav:
                acao_gav, sl_gav, tp_gav = gav
                pos2, perr2 = get_positions(symbol)
                if perr2 or pos2:
                    return jsonify({"ok": True, "action": "NONE", "reason": "posição já aberta (2ª checagem)"})
                st["last_ts"] = time.time()
                cfg_gav = cfg_for(symbol)
                vol_gav = st.get("volume") or float(cfg_gav.get("volume", 0.1))
                tag = "GBP_INS_AM_V13"
                ai_gav = {"veredito": tag, "confianca": None, "ia_usada": False,
                          "motivo": f"[{tag} M15 GO v9] inside vol≥1.3× + H4 · 09–12h"}
                result, exerr = execute(symbol, acao_gav, float(vol_gav), sl_gav, tp_gav)
                if result and not exerr:
                    _session["gerados"] += 1; _session["executados"] += 1
                    sc = 8 if acao_gav == "COMPRA" else -8
                    _last_ai[symbol] = {"veredito": tag, "confianca": None,
                                        "motivo": ai_gav["motivo"], "acao": acao_gav,
                                        "score": sc, "ts": time.time()}
                    _log_entry(symbol, acao_gav, result, sc, ai_gav)
                    try:
                        from services.crypto_telegram import notify_entry
                        notify_entry(symbol, acao_gav, result.get("price"), sl_gav, tp_gav,
                                     vol_gav, tag.lower(), ai_gav["motivo"])
                    except Exception:
                        pass
                    return jsonify({"ok": True, "action": "EXECUTED", "acao": acao_gav,
                                    "source": tag.lower(), "result": result, "ai": ai_gav})
                st["last_ts"] = 0.0
                return jsonify({"ok": False, "action": "FAILED", "error": exerr})

        # ── CAMINHO GBP_INS_1017_V13 (🟢 GO v11) — GBP M15 · 10–17 · vol 1,3× ──
        if gbp_ins_1017_v13_enabled(symbol):
            g17_candles, g17_err = _m15(800)
            g17 = gbp_ins_1017_v13_signal(g17_candles, symbol) if (not g17_err and g17_candles) else None
            if g17:
                acao_g17, sl_g17, tp_g17 = g17
                pos2, perr2 = get_positions(symbol)
                if perr2 or pos2:
                    return jsonify({"ok": True, "action": "NONE", "reason": "posição já aberta (2ª checagem)"})
                st["last_ts"] = time.time()
                cfg_g17 = cfg_for(symbol)
                vol_g17 = st.get("volume") or float(cfg_g17.get("volume", 0.1))
                tag = "GBP_INS_1017_V13"
                ai_g17 = {"veredito": tag, "confianca": None, "ia_usada": False,
                          "motivo": f"[{tag} M15 GO v11] inside vol≥1.3× + H4 · 10–17h"}
                result, exerr = execute(symbol, acao_g17, float(vol_g17), sl_g17, tp_g17)
                if result and not exerr:
                    _session["gerados"] += 1; _session["executados"] += 1
                    sc = 8 if acao_g17 == "COMPRA" else -8
                    _last_ai[symbol] = {"veredito": tag, "confianca": None,
                                        "motivo": ai_g17["motivo"], "acao": acao_g17,
                                        "score": sc, "ts": time.time()}
                    _log_entry(symbol, acao_g17, result, sc, ai_g17)
                    try:
                        from services.crypto_telegram import notify_entry
                        notify_entry(symbol, acao_g17, result.get("price"), sl_g17, tp_g17,
                                     vol_g17, tag.lower(), ai_g17["motivo"])
                    except Exception:
                        pass
                    return jsonify({"ok": True, "action": "EXECUTED", "acao": acao_g17,
                                    "source": tag.lower(), "result": result, "ai": ai_g17})
                st["last_ts"] = 0.0
                return jsonify({"ok": False, "action": "FAILED", "error": exerr})

        # ── CAMINHO XAU_US_DRIFT (Edge Discovery V2, bateria completa ✅ 22/07) ──
        # Fim da sessão US + volume acumulado alto + EMA200 subindo → COMPRA.
        # SL 1×ATR / TP 2×ATR. Compete pelo slot único de posição do ativo.
        if us_drift_enabled(symbol):
            ud_candles, ud_err = _m15(2000)
            ud = xau_us_drift_signal(ud_candles, symbol) if (not ud_err and ud_candles) else None
            if ud:
                acao_ud, sl_ud, tp_ud = ud
                pos2, perr2 = get_positions(symbol)
                if perr2 or pos2:
                    return jsonify({"ok": True, "action": "NONE", "reason": "posição já aberta (2ª checagem)"})
                st["last_ts"] = time.time()
                cfg_ud = cfg_for(symbol)
                vol_ud = st.get("volume") or float(cfg_ud.get("volume", 0.1))
                ai_ud = {"veredito": "US_DRIFT", "confianca": None, "ia_usada": False,
                         "motivo": "[XAU_US_DRIFT M15] fim de sessão US + volume + EMA200 subindo"}
                result, exerr = execute(symbol, acao_ud, float(vol_ud), sl_ud, tp_ud)
                if result and not exerr:
                    _session["gerados"] += 1; _session["executados"] += 1
                    _last_ai[symbol] = {"veredito": "US_DRIFT", "confianca": None,
                                        "motivo": ai_ud["motivo"], "acao": acao_ud,
                                        "score": 8, "ts": time.time()}
                    _log_entry(symbol, acao_ud, result, 8, ai_ud)
                    try:
                        from services.crypto_telegram import notify_entry
                        notify_entry(symbol, acao_ud, result.get("price"), sl_ud, tp_ud,
                                     vol_ud, "us_drift", ai_ud["motivo"])
                    except Exception:
                        pass
                    return jsonify({"ok": True, "action": "EXECUTED", "acao": acao_ud,
                                    "source": "us_drift", "result": result, "ai": ai_ud})
                st["last_ts"] = 0.0
                return jsonify({"ok": False, "action": "FAILED", "error": exerr})

        # ── CAMINHO RND_FADE_065 (🟢 GO v6 23/07) — XAU M15 ──
        if rnd_fade_enabled(symbol):
            rf65_candles, rf65_err = _m15(260)
            rf65 = xau_rnd_fade_065_signal(rf65_candles, symbol) if (not rf65_err and rf65_candles) else None
            if rf65:
                acao_rf, sl_rf, tp_rf = rf65
                pos2, perr2 = get_positions(symbol)
                if perr2 or pos2:
                    return jsonify({"ok": True, "action": "NONE", "reason": "posição já aberta (2ª checagem)"})
                st["last_ts"] = time.time()
                cfg_rf = cfg_for(symbol)
                vol_rf = st.get("volume") or float(cfg_rf.get("volume", 0.1))
                ai_rf = {"veredito": "RND_FADE_065", "confianca": None, "ia_usada": False,
                         "motivo": "[RND_FADE_065 M15 GO v6] fade redondo · |dn|<0.065"}
                result, exerr = execute(symbol, acao_rf, float(vol_rf), sl_rf, tp_rf)
                if result and not exerr:
                    _session["gerados"] += 1; _session["executados"] += 1
                    sc = 8 if acao_rf == "COMPRA" else -8
                    _last_ai[symbol] = {"veredito": "RND_FADE_065", "confianca": None,
                                        "motivo": ai_rf["motivo"], "acao": acao_rf,
                                        "score": sc, "ts": time.time()}
                    _log_entry(symbol, acao_rf, result, sc, ai_rf)
                    try:
                        from services.crypto_telegram import notify_entry
                        notify_entry(symbol, acao_rf, result.get("price"), sl_rf, tp_rf,
                                     vol_rf, "rnd_fade_065", ai_rf["motivo"])
                    except Exception:
                        pass
                    return jsonify({"ok": True, "action": "EXECUTED", "acao": acao_rf,
                                    "source": "rnd_fade_065", "result": result, "ai": ai_rf})
                st["last_ts"] = 0.0
                return jsonify({"ok": False, "action": "FAILED", "error": exerr})

        # ── CAMINHO RND_FADE_075_MT (🟢 GO v6) — XAU M15 · seg–qui ──
        if rnd_fade_enabled(symbol):
            rf75_candles, rf75_err = _m15(260)
            rf75 = xau_rnd_fade_075_mt_signal(rf75_candles, symbol) if (not rf75_err and rf75_candles) else None
            if rf75:
                acao_rf, sl_rf, tp_rf = rf75
                pos2, perr2 = get_positions(symbol)
                if perr2 or pos2:
                    return jsonify({"ok": True, "action": "NONE", "reason": "posição já aberta (2ª checagem)"})
                st["last_ts"] = time.time()
                cfg_rf = cfg_for(symbol)
                vol_rf = st.get("volume") or float(cfg_rf.get("volume", 0.1))
                ai_rf = {"veredito": "RND_FADE_075_MT", "confianca": None, "ia_usada": False,
                         "motivo": "[RND_FADE_075_MT M15 GO v6] |dn|<0.075 · seg–qui"}
                result, exerr = execute(symbol, acao_rf, float(vol_rf), sl_rf, tp_rf)
                if result and not exerr:
                    _session["gerados"] += 1; _session["executados"] += 1
                    sc = 8 if acao_rf == "COMPRA" else -8
                    _last_ai[symbol] = {"veredito": "RND_FADE_075_MT", "confianca": None,
                                        "motivo": ai_rf["motivo"], "acao": acao_rf,
                                        "score": sc, "ts": time.time()}
                    _log_entry(symbol, acao_rf, result, sc, ai_rf)
                    try:
                        from services.crypto_telegram import notify_entry
                        notify_entry(symbol, acao_rf, result.get("price"), sl_rf, tp_rf,
                                     vol_rf, "rnd_fade_075_mt", ai_rf["motivo"])
                    except Exception:
                        pass
                    return jsonify({"ok": True, "action": "EXECUTED", "acao": acao_rf,
                                    "source": "rnd_fade_075_mt", "result": result, "ai": ai_rf})
                st["last_ts"] = 0.0
                return jsonify({"ok": False, "action": "FAILED", "error": exerr})

        # ── CAMINHO RND_FADE_MT (🟢 GO v5 23/07) — XAU M15 · seg–qui ──
        if rnd_fade_enabled(symbol):
            rfm_candles, rfm_err = _m15(260)
            rfm = xau_rnd_fade_mt_signal(rfm_candles, symbol) if (not rfm_err and rfm_candles) else None
            if rfm:
                acao_rf, sl_rf, tp_rf = rfm
                pos2, perr2 = get_positions(symbol)
                if perr2 or pos2:
                    return jsonify({"ok": True, "action": "NONE", "reason": "posição já aberta (2ª checagem)"})
                st["last_ts"] = time.time()
                cfg_rf = cfg_for(symbol)
                vol_rf = st.get("volume") or float(cfg_rf.get("volume", 0.1))
                ai_rf = {"veredito": "RND_FADE_MT", "confianca": None, "ia_usada": False,
                         "motivo": "[RND_FADE_MT M15 GO v5] fade redondo · |dn|<0.07 · seg–qui"}
                result, exerr = execute(symbol, acao_rf, float(vol_rf), sl_rf, tp_rf)
                if result and not exerr:
                    _session["gerados"] += 1; _session["executados"] += 1
                    sc = 8 if acao_rf == "COMPRA" else -8
                    _last_ai[symbol] = {"veredito": "RND_FADE_MT", "confianca": None,
                                        "motivo": ai_rf["motivo"], "acao": acao_rf,
                                        "score": sc, "ts": time.time()}
                    _log_entry(symbol, acao_rf, result, sc, ai_rf)
                    try:
                        from services.crypto_telegram import notify_entry
                        notify_entry(symbol, acao_rf, result.get("price"), sl_rf, tp_rf,
                                     vol_rf, "rnd_fade_mt", ai_rf["motivo"])
                    except Exception:
                        pass
                    return jsonify({"ok": True, "action": "EXECUTED", "acao": acao_rf,
                                    "source": "rnd_fade_mt", "result": result, "ai": ai_rf})
                st["last_ts"] = 0.0
                return jsonify({"ok": False, "action": "FAILED", "error": exerr})

        # ── CAMINHO RND_FADE_07 (🟢 GO v4 23/07) — XAU M15 (mais seletivo) ──
        if rnd_fade_enabled(symbol):
            rf7_candles, rf7_err = _m15(260)
            rf7 = xau_rnd_fade_07_signal(rf7_candles, symbol) if (not rf7_err and rf7_candles) else None
            if rf7:
                acao_rf, sl_rf, tp_rf = rf7
                pos2, perr2 = get_positions(symbol)
                if perr2 or pos2:
                    return jsonify({"ok": True, "action": "NONE", "reason": "posição já aberta (2ª checagem)"})
                st["last_ts"] = time.time()
                cfg_rf = cfg_for(symbol)
                vol_rf = st.get("volume") or float(cfg_rf.get("volume", 0.1))
                ai_rf = {"veredito": "RND_FADE_07", "confianca": None, "ia_usada": False,
                         "motivo": "[RND_FADE_07 M15 GO v4] fade redondo · |dn|<0.07"}
                result, exerr = execute(symbol, acao_rf, float(vol_rf), sl_rf, tp_rf)
                if result and not exerr:
                    _session["gerados"] += 1; _session["executados"] += 1
                    sc = 8 if acao_rf == "COMPRA" else -8
                    _last_ai[symbol] = {"veredito": "RND_FADE_07", "confianca": None,
                                        "motivo": ai_rf["motivo"], "acao": acao_rf,
                                        "score": sc, "ts": time.time()}
                    _log_entry(symbol, acao_rf, result, sc, ai_rf)
                    try:
                        from services.crypto_telegram import notify_entry
                        notify_entry(symbol, acao_rf, result.get("price"), sl_rf, tp_rf,
                                     vol_rf, "rnd_fade_07", ai_rf["motivo"])
                    except Exception:
                        pass
                    return jsonify({"ok": True, "action": "EXECUTED", "acao": acao_rf,
                                    "source": "rnd_fade_07", "result": result, "ai": ai_rf})
                st["last_ts"] = 0.0
                return jsonify({"ok": False, "action": "FAILED", "error": exerr})

        # ── CAMINHO RND_FADE_TIGHT (🟢 GO v3 23/07) — XAU M15 ──
        # Fade em número redondo com delta fraco. Compete pelo slot único.
        if rnd_fade_enabled(symbol):
            rf_candles, rf_err = _m15(260)
            rf = xau_rnd_fade_tight_signal(rf_candles, symbol) if (not rf_err and rf_candles) else None
            if rf:
                acao_rf, sl_rf, tp_rf = rf
                pos2, perr2 = get_positions(symbol)
                if perr2 or pos2:
                    return jsonify({"ok": True, "action": "NONE", "reason": "posição já aberta (2ª checagem)"})
                st["last_ts"] = time.time()
                cfg_rf = cfg_for(symbol)
                vol_rf = st.get("volume") or float(cfg_rf.get("volume", 0.1))
                ai_rf = {"veredito": "RND_FADE_TIGHT", "confianca": None, "ia_usada": False,
                         "motivo": "[RND_FADE_TIGHT M15] fade redondo + delta fraco"}
                result, exerr = execute(symbol, acao_rf, float(vol_rf), sl_rf, tp_rf)
                if result and not exerr:
                    _session["gerados"] += 1; _session["executados"] += 1
                    sc = 8 if acao_rf == "COMPRA" else -8
                    _last_ai[symbol] = {"veredito": "RND_FADE_TIGHT", "confianca": None,
                                        "motivo": ai_rf["motivo"], "acao": acao_rf,
                                        "score": sc, "ts": time.time()}
                    _log_entry(symbol, acao_rf, result, sc, ai_rf)
                    try:
                        from services.crypto_telegram import notify_entry
                        notify_entry(symbol, acao_rf, result.get("price"), sl_rf, tp_rf,
                                     vol_rf, "rnd_fade_tight", ai_rf["motivo"])
                    except Exception:
                        pass
                    return jsonify({"ok": True, "action": "EXECUTED", "acao": acao_rf,
                                    "source": "rnd_fade_tight", "result": result, "ai": ai_rf})
                st["last_ts"] = 0.0
                return jsonify({"ok": False, "action": "FAILED", "error": exerr})

        # ── Discovery feature-condition GOs (harness 26/07 · JSON allowlist) ──
        # Depois dos GOs clássicos; ordem = ranking net no JSON. ETH_S_36 usa M60.
        # CRÍTICO: build_features 1× por TF (não por path) — senão o lock do ativo
        # fica preso minutos e o runtime inteiro passa fome (timeout 25s).
        try:
            from services.crypto_discovery_paths import (
                discovery_enabled, paths_by_tf, eval_path, prepare_features,
            )
            if discovery_enabled(symbol):
                _by_tf = paths_by_tf(symbol)
                _tf_cache = {15: (_m15_all, _m15_err)}
                for _tf, _plist in sorted(_by_tf.items()):
                    if _tf not in _tf_cache:
                        # thresholds_is congelados → 900 barras bastam p/ features estáveis
                        _tf_cache[_tf] = get_candles(symbol, int(_tf), 900)
                    _c_all, _c_err = _tf_cache[_tf]
                    if _c_err or not _c_all:
                        continue
                    _c = _c_all if len(_c_all) <= 900 else _c_all[-900:]
                    _df, _F = prepare_features(_c)
                    if _df is None or _F is None:
                        continue
                    for _p in _plist:
                        _sig = eval_path(_c, _p, df=_df, features=_F)
                        if not _sig:
                            continue
                        _tag = _p.get("path") or _p.get("name")
                        _mot = _p.get("comment") or f"[{_tag} GO disc] feature-cond SL1 TP2"
                        _resp = _fire_tuple_go(_tag, _sig, _mot)
                        if _resp is not None:
                            return _resp
        except Exception:
            pass

        # Ativo com score legado OFF (ex.: BTC/ETH): só caminhos GO acima.
        # Evita cair em score/pullback e ficar spitando "NEUTRO" como se fosse gatilho.
        if auto_blocked(symbol):
            from services.crypto_config import active_trade_paths
            paths = active_trade_paths(symbol)
            near = {}
            try:
                from services.crypto_go_telemetry import summarize_near_miss
                near = summarize_near_miss(symbol, _m15_all)
            except Exception:
                pass
            reason = (f"{symbol}: SCAN · sem gatilho GO "
                      f"(score legado OFF — spread M15)")
            if near.get("hint"):
                reason = f"{reason} · {near['hint']}"
            return jsonify({
                "ok": True, "action": "NONE",
                "reason": reason,
                "active_paths": paths,
                "near_miss": near,
            })

        candles, err = get_candles(symbol, TF_MINUTES.get(interval, 15))
        if err:
            return jsonify({"ok": True, "action": "NONE", "reason": err})
        cfg = cfg_for(symbol)
        sig = analyze(candles, cfg)
        source = "score"
        score_ok = (sig["acao"] in ("COMPRA", "VENDA")
                    and abs(sig.get("score", 0)) >= int(cfg.get("score_min", 7)))
        # Chave por caminho (veredito do backtest_crypto_pro — ver crypto_config)
        if score_ok and not path_enabled("score"):
            score_ok = False

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
            imp = {}
            if path_enabled("impulso"):
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
                if not path_enabled("pullback"):
                    return jsonify({"ok": True, "action": "NONE",
                                    "reason": "caminhos score/impulso/pullback desativados p/ este ciclo"})
                from services.crypto_config import pullback_enabled, active_trade_paths
                paths = active_trade_paths(symbol)
                if not pullback_enabled(symbol):
                    # EUR/GBP/etc.: GOs já rodaram acima; mensagem antiga mentia
                    # "sem caminho GO" quando na verdade só faltou gatilho.
                    near = {}
                    try:
                        from services.crypto_go_telemetry import summarize_near_miss
                        near = summarize_near_miss(symbol, candles if candles else _m15_all)
                    except Exception:
                        pass
                    reason = f"{symbol}: SCAN · sem gatilho GO"
                    if near.get("hint"):
                        reason = f"{reason} · {near['hint']}"
                    return jsonify({"ok": True, "action": "NONE",
                                    "reason": reason,
                                    "active_paths": paths,
                                    "near_miss": near})
                mm = _mm_compute(symbol)
                if not mm.get("trigger"):
                    reading = mm.get("reading") or "sem gatilho pullback"
                    return jsonify({
                        "ok": True, "action": "NONE",
                        "reason": (f"{symbol}: SCAN · GOs sem disparo · "
                                   f"pullback: {reading}"),
                        "active_paths": paths,
                    })
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

        # ── FILTRO MACRO (backtest 05-07/2026) ───────────────────────────────
        # Nunca opera CONTRA a tendência do H1 (EMA50×EMA200). No caminho do SCORE
        # também exige ADX(M15) ≥ corte calibrado do ativo. Foi o que virou o crypto
        # de negativo p/ neutro/positivo (ex.: XAU -0.075R → +0.131R). Impulso e
        # pullback só passam pela trava de macro (têm confirmação própria de momentum).
        _macro = _macro_h1_dir(symbol)
        _acao = sig["acao"]
        _macro_ok = (_macro is None
                     or (_acao == "COMPRA" and _macro == "alta")
                     or (_acao == "VENDA" and _macro == "baixa"))
        if not _macro_ok:
            _session["bloqueados"] += 1
            return jsonify({"ok": True, "action": "BLOCKED",
                            "reason": f"macro H1 '{_macro}' contra {_acao} — filtro anti-lateral"})
        if source == "score":
            _adxv = abs(float(sig.get("adx", 0) or 0))
            _adxcut = float(cfg.get("macro_adx", 25))
            if _adxv < _adxcut:
                _session["bloqueados"] += 1
                return jsonify({"ok": True, "action": "BLOCKED",
                                "reason": f"ADX {_adxv:.0f} < corte {_adxcut:.0f} ({symbol}) — filtro anti-lateral"})

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
            try:
                from services.crypto_telegram import notify_entry
                notify_entry(symbol, sig["acao"], result.get("price"), sig.get("stop"),
                             sig.get("tp1"), vol, source, (ai or {}).get("motivo"))
            except Exception:
                pass
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
    reason = (b.get("reason") or b.get("exit_reason") or "").strip()
    detail = (b.get("detail") or b.get("motivo") or "").strip()
    if reason:
        _set_close_intent(symbol, reason, detail or reason)
    elif not _close_intent.get(symbol):
        _set_close_intent(symbol, "MANUAL", "Fechado pelo painel/API")
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
    # Registra QUALQUER fechamento real via RECONCILIAÇÃO (casada por position_id do
    # MT5): cada posição fechada vira uma linha com a SUA PRÓPRIA entrada/saída/lucro.
    # Nunca mistura a entrada de uma posição AINDA ABERTA com o fechamento de outra.
    try:
        _reconcile_closes()
    except Exception as exc:
        logger.warning("crypto register-close reconcile: %s", exc)
    # Confirma se a posição realmente sumiu (só p/ o cliente trocar de modo).
    from services.crypto_service import get_positions, last_out_deal
    pos, perr = get_positions(symbol)
    if perr:
        return jsonify({"ok": True, "closed": False, "reason": "checagem indisponível"})
    closed = not pos
    profit = None
    if closed:
        try:
            d = last_out_deal(symbol)     # só p/ o som de saída no cliente
            profit = d["profit"] if d else None
        except Exception:
            profit = None
    return jsonify({"ok": True, "closed": closed, "profit": profit})


def _maybe_reconcile():
    """Roda a reconciliação no máx. 1x a cada 3s (instância crypto é dedicada à ICMarkets,
    então pode ser rápido → lista de trades quase em tempo real)."""
    global _last_reconcile
    if time.time() - _last_reconcile < 3:
        return
    _last_reconcile = time.time()
    try:
        _reconcile_closes()
    except Exception as exc:
        logger.warning("crypto reconcile: %s", exc)


_bg_reconciler_started = {"on": False}

def start_bg_reconciler():
    """Reconciliação em BACKGROUND (thread própria), independente do navegador —
    registra os fechamentos mesmo com a aba do crypto em segundo plano/fechada.
    Só deve rodar na instância CRYPTO (dedicada à ICMarkets)."""
    if _bg_reconciler_started["on"]:
        return
    _bg_reconciler_started["on"] = True
    import threading

    def _loop():
        while True:
            try:
                _reconcile_closes()
            except Exception as exc:
                logger.warning("crypto bg reconcile: %s", exc)
            time.sleep(5)

    threading.Thread(target=_loop, name="crypto-bg-reconcile", daemon=True).start()
    logger.info("Crypto: reconciliador em background ativo (5s).")


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


def _rebuild_from_history(hours: int = 120) -> int:
    """Reconstrói o log do Monitor Crypto do ZERO a partir do histórico REAL da conta
    de CRYPTO (ICMarkets), casado por position_id — sem fantasmas nem duplicatas.
    ⚠️ Só mexe no crypto_trades.csv/sidecar. NÃO toca em NADA do Monitor MT5 (B3):
    conta/terminal/magic/arquivos são outros."""
    from services.crypto_service import recent_closed_trades
    trades = recent_closed_trades(hours)
    os.makedirs(_LOGDIR, exist_ok=True)
    # backup do CSV atual
    try:
        if os.path.exists(_CSV):
            import shutil
            shutil.copy2(_CSV, os.path.join(
                _LOGDIR, "crypto_trades_backup_rebuild_%s.csv" % _now().strftime("%Y%m%d_%H%M%S")))
    except Exception:
        pass
    # zera CSV (só cabeçalho) + dedup persistido
    with open(_CSV, "w", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=_COLS).writeheader()
    _logged_deals.clear()
    try:
        if os.path.exists(_DEALS_FILE):
            os.remove(_DEALS_FILE)
    except Exception:
        pass
    # zera contadores de sessão (serão recompostos pela releitura do CSV)
    for k in ("trades", "wins", "losses", "breakevens"):
        _session[k] = 0
    _session["pnl"] = 0.0
    _pending.clear()
    # reimporta cada posição fechada (na ordem de fechamento), casada por posição
    n = 0
    for t in trades:
        cdt = datetime.fromtimestamp(t["close_epoch"], _BRT)
        entry = {
            "id": _next_id(), "datetime_brt": cdt.strftime("%Y-%m-%d %H:%M:%S"),
            "symbol": t["symbol"], "direcao": t.get("direcao", "?"),
            "entry_price": t.get("entry_price") if t.get("entry_price") is not None else "",
            "sl": "", "tp1": "", "volume": t.get("volume"), "score": "",
        }
        reason = _infer_broker_exit_reason(
            t["profit"], entry.get("entry_price"), t.get("exit_price"),
            entry.get("tp1"), entry.get("sl"), entry.get("direcao"))
        _log_exit(t["symbol"], t.get("exit_price", 0), t["profit"], reason, entry=entry)
        _mark_deal_logged(t["out_ticket"])
        n += 1
    logger.info("crypto rebuild: %d posição(ões) reimportada(s) do histórico MT5.", n)
    return n


@crypto_bp.route("/api/crypto/rebuild", methods=["POST"])
def api_rebuild():
    """Reconstrói o histórico do Monitor Crypto pelo MT5 (ICMarkets). Não toca no B3."""
    try:
        hours = int(request.args.get("hours", 120))
    except Exception:
        hours = 120
    # Segura a MESMA trava da reconciliação: o rebuild zera e reescreve o CSV, então
    # não pode rodar junto com um _reconcile_closes (senão volta a duplicar).
    with _reconcile_lock:
        try:
            n = _rebuild_from_history(hours)
            return jsonify({"ok": True, "imported": n})
        except Exception as exc:
            logger.warning("crypto rebuild erro: %s", exc)
            return jsonify({"ok": False, "error": str(exc)})


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
