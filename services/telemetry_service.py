"""
services/telemetry_service.py
-----------------------------
Módulo de telemetria — coleta dados de todos os módulos existentes
do Trade AI via leitura pura (zero side-effects, zero alteração de estado).

Utilizado pelos endpoints:
  GET /api/telemetry       → resumo executivo (Fase 1)
  GET /api/telemetry/full  → payload completo  (Fase 2)

REGRA FUNDAMENTAL: este módulo apenas LÊ.
Nunca executa trades, nunca modifica estado, nunca altera configurações.
"""
import logging
import re as _re
import time as _time
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

_BRT = timezone(timedelta(hours=-3))

# ---------------------------------------------------------------------------
# Cache de resultado (TTL curto para sincronizar com Monitor MT5)
# ---------------------------------------------------------------------------
_CACHE_FULL:    dict = {}   # key -> {"ts": float, "data": dict}
_CACHE_SUMMARY: dict = {}
_CACHE_TTL_FULL    = 8     # segundos - full payload
_CACHE_TTL_SUMMARY = 8     # segundos - resumo


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------

def _now_brt() -> str:
    return datetime.now(_BRT).strftime("%Y-%m-%d %H:%M:%S")


def _safe(fn, default=None):
    """Executa fn() absorvendo qualquer exceção — nunca propaga erros."""
    try:
        return fn()
    except Exception as exc:
        logger.debug("telemetry _safe: %s", exc)
        return default


# ---------------------------------------------------------------------------
# Análise avançada: derivados do signal dict (Fase 1.5)
# ---------------------------------------------------------------------------

_SCORE_RE = _re.compile(r'\[([+-]\d+)\]')


def compute_score_breakdown(signal: dict) -> dict:
    """
    Deriva a contribuição individual de cada categoria de indicador ao score total.
    Faz parsing dos strings `sinais` retornados por generate_signal().
    Não toca em technical_analysis.py — lê apenas o dict já calculado.

    Retorna dict com contribuição por categoria (valor positivo = bullish, negativo = bearish).
    Categorias: ema_alignment, ema50, ema200, vwap, rsi, macd, bollinger, volume, candle, htf_1h.
    """
    breakdown = {
        "ema_alignment": 0,   # EMA9/21 posição + crossover + slope
        "ema50":         0,   # filtro médio prazo
        "ema200":        0,   # filtro longo prazo ±2
        "vwap":          0,   # referência institucional
        "rsi":           0,   # momentum e extremos
        "macd":          0,   # histograma + crossover
        "bollinger":     0,   # BB mid + extremos
        "volume":        0,   # confirmação de volume
        "candle":        0,   # padrões de candle
        "htf_1h":        0,   # confirmação multi-timeframe 1h
    }

    sinais = signal.get("sinais") or []
    for sinal in sinais:
        m = _SCORE_RE.search(sinal)
        if not m:
            continue
        val = int(m.group(1))
        sl = sinal.lower()

        if "1h:" in sl:
            breakdown["htf_1h"] += val
        elif "ema9" in sl or "cruzamento" in sl:
            breakdown["ema_alignment"] += val
        elif "ema50" in sl:
            breakdown["ema50"] += val
        elif "ema200" in sl:
            breakdown["ema200"] += val
        elif "vwap" in sl:
            breakdown["vwap"] += val
        elif "rsi" in sl or "sobrevenda" in sl or "sobrecompra" in sl:
            breakdown["rsi"] += val
        elif "macd" in sl:
            breakdown["macd"] += val
        elif "banda" in sl or "sma20" in sl or "bb" in sl:
            breakdown["bollinger"] += val
        elif "volume" in sl:
            breakdown["volume"] += val
        else:
            breakdown["candle"] += val  # hammer, engolfo, marubozu, etc.

    return breakdown


def compute_market_regime(signal: dict) -> str:
    """
    Classifica o regime de mercado atual com base nos indicadores do sinal.

    Retorna: "TRENDING" | "RANGING" | "VOLATILE" | "LOW_VOLUME"

    Lógica:
      - ADX < 20 (adx_filtered=True): RANGING
      - RSI em extremo (< 28 ou > 72) com ADX < 20: VOLATILE
      - Volume muito fraco: LOW_VOLUME
      - ADX >= 20: TRENDING
    """
    adx_filtered = signal.get("adx_filtered", False)
    adx   = signal.get("adx")
    rsi   = signal.get("rsi")
    sinais = " ".join(signal.get("sinais") or []).lower()

    # LOW_VOLUME: sinal explícito no texto dos sinais
    if "volume fraco" in sinais and "sem convicto" in sinais:
        if adx is not None and adx < 20:
            return "LOW_VOLUME"

    # RANGING: ADX muito fraco (< 20)
    if adx_filtered or (adx is not None and adx < 20):
        # Dentro do range, RSI extremo sinaliza possível breakout volátil
        if rsi is not None and (rsi < 28 or rsi > 72):
            return "VOLATILE"
        return "RANGING"

    # VOLATILE: ADX entre 20-25 + RSI nos extremos
    if adx is not None and adx < 25 and rsi is not None and (rsi < 32 or rsi > 68):
        return "VOLATILE"

    # TRENDING: ADX >= 20 e mercado direcional
    return "TRENDING"


def compute_signal_strength(signal: dict) -> int:
    """
    Converte o estado do sinal em uma escala 0-100 de força.
    Considera: magnitude do score, ADX, confirmação HTF, volume, confluências.

    Escala intuitiva:
      0-30  = sinal fraco / não operar
      31-55 = sinal moderado / cautela
      56-75 = sinal bom
      76-100 = sinal forte / alta probabilidade
    """
    score     = signal.get("score", 0) or 0
    score_raw = signal.get("score_raw", score) or score
    adx       = signal.get("adx")
    htf_trend = signal.get("htf_trend")
    acao      = signal.get("acao", "NEUTRO")
    sinais_str = " ".join(signal.get("sinais") or []).lower()
    adx_filtered = signal.get("adx_filtered", False)
    directional_block = signal.get("directional_block")

    if acao == "NEUTRO":
        return max(0, min(30, abs(score) * 5))

    # Base: score normalizado (max útil ~12) → contribui até 65 pts
    abs_score = min(abs(score), 12)
    base = round(abs_score / 12 * 65)

    # ADX (força da tendência) → até +20
    adx_bonus = 0
    if adx is not None:
        if adx >= 30:
            adx_bonus = 20
        elif adx >= 25:
            adx_bonus = 14
        elif adx >= 20:
            adx_bonus = 7
    if adx_filtered:
        adx_bonus = 0

    # Confirmação HTF 1h → até +10
    htf_bonus = 0
    if htf_trend is not None:
        if (acao == "COMPRA" and htf_trend == "alta") or \
           (acao == "VENDA"  and htf_trend == "baixa"):
            htf_bonus = 10
        elif directional_block:
            htf_bonus = -10   # contra a tendência macro

    # Volume forte → +5
    vol_bonus = 5 if ("volume forte" in sinais_str and "confirmando" in sinais_str) else 0

    strength = base + adx_bonus + htf_bonus + vol_bonus
    return max(0, min(100, strength))


def compute_risk_level(signal: dict) -> int:
    """
    Converte o estado do sinal em uma escala 0-100 de risco.
    Maior = operação mais arriscada / menos favorável.

    Escala intuitiva:
      0-25  = risco baixo / setup limpo
      26-50 = risco moderado / atenção
      51-75 = risco alto / evitar
      76-100 = risco muito alto / não operar
    """
    adx          = signal.get("adx")
    rsi          = signal.get("rsi")
    adx_filtered = signal.get("adx_filtered", False)
    directional_block = signal.get("directional_block")
    htf_trend    = signal.get("htf_trend")
    acao         = signal.get("acao", "NEUTRO")
    sinais_str   = " ".join(signal.get("sinais") or []).lower()
    score        = abs(signal.get("score", 0) or 0)

    risk = 0

    # ADX fraco: mercado sem tendência = risco alto
    if adx_filtered or (adx is not None and adx < 20):
        risk += 40
    elif adx is not None and adx < 25:
        risk += 20
    elif adx is not None and adx >= 30:
        risk -= 10   # tendência forte = menos risco

    # RSI em extremo (direção contrária ao trade)
    if rsi is not None:
        if acao == "COMPRA" and rsi > 70:
            risk += 25   # comprando em sobrecompra
        elif acao == "VENDA" and rsi < 30:
            risk += 25   # vendendo em sobrevenda
        elif acao == "COMPRA" and rsi > 65:
            risk += 12
        elif acao == "VENDA" and rsi < 35:
            risk += 12

    # Bloqueio direcional (macro contra)
    if directional_block:
        risk += 30

    # HTF não confirma
    if htf_trend is not None:
        if (acao == "COMPRA" and htf_trend == "baixa") or \
           (acao == "VENDA"  and htf_trend == "alta"):
            risk += 20
    elif htf_trend is None and acao != "NEUTRO":
        risk += 8   # sem confirmação HTF = incerteza leve

    # Volume fraco (confirmação ausente)
    if "volume fraco" in sinais_str and "sem convicto" in sinais_str:
        risk += 10

    # Score baixo para um sinal ativo = borda fraca
    if acao != "NEUTRO" and score < 5:
        risk += 10

    # Sinal misto (filtro elevado para 6)
    if "threshold elevado" in sinais_str:
        risk += 15

    return max(0, min(100, risk))


def build_clean_confluences(signal: dict) -> list:
    """
    Gera lista legível de confluências a partir dos `sinais` do signal dict.
    Remove anotações de score ([+1], [-2]) e filtra mensagens de filtro/bloqueio.
    Retorna apenas as confluências com impacto efetivo no score.
    """
    sinais = signal.get("sinais") or []
    acao   = signal.get("acao", "NEUTRO")

    # Tags de filtro não são confluências operacionais
    _SKIP_PATTERNS = (
        "filtro", "bloqueado", "bloqueada", "threshold",
        "tp1 ajustado", "nivel de mercado", "adx fraco",
        "mercado em range", "sinal misto", "aguardar confirmacao",
    )

    result = []
    for sinal in sinais:
        sl = sinal.lower()
        if any(p in sl for p in _SKIP_PATTERNS):
            continue

        m = _SCORE_RE.search(sinal)
        if not m:
            continue   # sem score = informativo apenas (ex: "RSI neutro [0]")

        val = int(m.group(1))
        if val == 0:
            continue

        # Para COMPRA mostra confluências positivas; para VENDA, negativas; NEUTRO, tudo
        if acao == "COMPRA" and val < 0:
            continue
        if acao == "VENDA"  and val > 0:
            continue

        # Limpa o texto: remove "[+N]" e "[-N]" da string
        clean = _SCORE_RE.sub("", sinal).strip(" -–—").strip()
        if clean:
            result.append(clean)

    return result


# ---------------------------------------------------------------------------
# Coleta: estado do auto-trade (Monitor MT5)
# ---------------------------------------------------------------------------

def get_autotrade_state() -> dict:
    """
    Retorna estado do auto-trade do Monitor MT5.
    Lê variáveis do telegram_commander sem alterá-las.
    """
    enabled = _safe(lambda: __import__(
        "services.telegram_commander", fromlist=["is_autotrade_enabled"]
    ).is_autotrade_enabled(), default=None)

    meta = _safe(lambda: __import__(
        "services.telegram_commander", fromlist=["get_meta_diaria"]
    ).get_meta_diaria(), default=None)

    return {
        "enabled":      enabled,
        "meta_diaria":  meta,
    }


# ---------------------------------------------------------------------------
# Coleta: posição aberta (Monitor MT5)
# ---------------------------------------------------------------------------

def get_open_position(tv_symbol: str = "BMFBOVESPA:WIN1!") -> dict:
    """
    Retorna dados da posição aberta do Monitor MT5 para o símbolo.
    Combina registro do DB com dados ao vivo do MT5.
    """
    result = {
        "found":        False,
        "tv_symbol":    tv_symbol,
        "trade_log":    None,
        "mt5_position": None,
        "pnl_brl":      None,
        "pnl_pts":      None,
        "entry_price":  None,
        "current_price": None,
        "volume":       None,
        "acao":         None,
        "opened_at":    None,
    }

    # 1) DB — registro do trade aberto
    trade_log = _safe(lambda: __import__(
        "services.trade_log", fromlist=["get_open_auto_trade"]
    ).get_open_auto_trade(tv_symbol))

    if trade_log:
        result["found"]       = True
        result["trade_log"]   = trade_log
        result["entry_price"] = trade_log.get("entry_price")
        result["volume"]      = trade_log.get("volume")
        result["acao"]        = trade_log.get("acao")
        result["opened_at"]   = trade_log.get("opened_at")

    # 2) MT5 — posição ao vivo
    mt5_positions = _safe(lambda: __import__(
        "services.trade_executor", fromlist=["get_open_positions"]
    ).get_open_positions(tv_symbol))

    if mt5_positions and not isinstance(mt5_positions, tuple):
        pass
    elif mt5_positions and isinstance(mt5_positions, tuple):
        positions, err = mt5_positions
        if not err and positions:
            pos = positions[0]
            result["found"]         = True
            result["mt5_position"]  = pos
            result["pnl_brl"]       = round(pos.get("profit", 0), 2)
            result["current_price"] = pos.get("price_current")
            if not result["entry_price"]:
                result["entry_price"] = pos.get("price_open")
            if not result["volume"]:
                result["volume"] = pos.get("volume")

    return result


# ---------------------------------------------------------------------------
# Coleta: sinal técnico atual (Monitor MT5)
# ---------------------------------------------------------------------------

def get_current_signal(
    tv_symbol: str = "BMFBOVESPA:WIN1!",
    interval: str  = "15",
) -> dict:
    """
    Gera sinal técnico atual para o símbolo/intervalo informado.
    Reutiliza exatamente o mesmo pipeline do Monitor MT5 sem nenhuma alteração.
    Retorna None em qualquer falha (sem propagar exceções).
    """
    try:
        from services.market_service import get_candles, TV_INTERVAL_TO_YF
        from services.technical_analysis import generate_signal

        period_yf, interval_yf = TV_INTERVAL_TO_YF.get(interval, ("5d", "15m"))

        df, error = get_candles(
            tv_symbol,
            period=period_yf,
            interval=interval_yf,
            tv_interval=interval,
        )
        if error or df is None:
            return {"ok": False, "error": error or "Sem dados"}

        htf_df = None
        if interval_yf not in ("60m", "1h", "1d", "1wk"):
            htf_df, _ = get_candles(
                tv_symbol, period="1mo", interval="1h", tv_interval="60"
            )

        signal = generate_signal(df, htf_df=htf_df)
        if signal is None:
            return {"ok": False, "error": "Dados insuficientes"}

        return {"ok": True, **signal}

    except Exception as exc:
        logger.warning("telemetry get_current_signal: %s", exc)
        return {"ok": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Coleta: análise da posição (Position Manager)
# ---------------------------------------------------------------------------

def get_position_analysis(
    tv_symbol: str = "BMFBOVESPA:WIN1!",
    interval:  str = "15",
) -> "dict | None":
    """
    Retorna recomendação de gestão do Position Manager para a posição ativa.
    Retorna None se não houver posição ou em caso de erro.
    """
    try:
        from services.trade_log import get_open_auto_trade
        from services.trade_executor import get_open_positions
        from services.position_manager import analyze_position
        from services.market_service import get_candles, TV_INTERVAL_TO_YF
        from services.technical_analysis import generate_signal

        open_log = get_open_auto_trade(tv_symbol)
        if not open_log:
            return None

        positions, err = get_open_positions(tv_symbol)
        if err or not positions:
            return None

        pos = positions[0]
        period_yf, interval_yf = TV_INTERVAL_TO_YF.get(interval, ("5d", "15m"))
        df, error = get_candles(tv_symbol, period=period_yf, interval=interval_yf, tv_interval=interval)
        if error or df is None:
            return None

        signal = generate_signal(df)
        if signal is None:
            return None

        interval_min = int(interval) if str(interval).isdigit() else 15
        analysis = analyze_position(pos, open_log, signal, interval_min)
        return analysis

    except Exception as exc:
        logger.warning("telemetry get_position_analysis: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Coleta: estatísticas do dia (Monitor MT5)
# ---------------------------------------------------------------------------

def get_daily_stats() -> dict:
    """Resumo de P&L e trades do dia corrente."""
    return _safe(
        lambda: __import__(
            "services.trade_log", fromlist=["auto_trades_stats"]
        ).auto_trades_stats(today_only=True),
        default={
            "total": 0, "abertos": 0, "fechados": 0,
            "wins": 0, "losses": 0, "win_rate_pct": 0,
            "pnl_total_pts": 0, "pnl_total_brl": 0.0,
        },
    )


def get_weekly_stats() -> dict:
    """Resumo de P&L e trades da semana (últimos 7 dias)."""
    try:
        from services.trade_log import _conn

        brt_now = datetime.now(_BRT)
        week_start = (brt_now - timedelta(days=brt_now.weekday())).strftime("%Y-%m-%d")

        with _conn() as conn:
            rows = conn.execute(
                """
                SELECT pnl_brl, pnl_pts, close_reason, closed_at
                FROM auto_trades
                WHERE date(opened_at) >= ?
                  AND close_reason NOT IN (
                      'BLOQUEADO_IA','AGUARDADO_IA','DADOS_CORROMPIDOS',
                      'FANTASMA_REMOVIDO','ORFAO_BUG_CROSS_SYMBOL'
                  )
                """,
                (week_start,),
            ).fetchall()

        rows = [dict(r) for r in rows]
        fechados = [r for r in rows if r.get("closed_at")]
        wins  = [r for r in fechados if (r.get("pnl_pts") or 0) > 0]
        losses= [r for r in fechados if (r.get("pnl_pts") or 0) < 0]

        return {
            "total_semana":    len(rows),
            "fechados":        len(fechados),
            "wins":            len(wins),
            "losses":          len(losses),
            "pnl_semana_pts":  round(sum(r.get("pnl_pts") or 0 for r in fechados)),
            "pnl_semana_brl":  round(sum(r.get("pnl_brl") or 0 for r in fechados), 2),
        }
    except Exception as exc:
        logger.warning("telemetry get_weekly_stats: %s", exc)
        return {
            "total_semana": 0, "fechados": 0, "wins": 0, "losses": 0,
            "pnl_semana_pts": 0, "pnl_semana_brl": 0.0,
        }


# ---------------------------------------------------------------------------
# Coleta: estado do Scalper
# ---------------------------------------------------------------------------

def get_scalper_state() -> dict:
    """
    Retorna estado atual do Scalper: score, posição, auto-trade, pausa, sessão.
    Lê diretamente dos módulos sem alterá-los.
    """
    result = {
        "auto_enabled":        None,
        "paused":              False,
        "paused_until":        None,
        "consecutive_losses":  0,
        "position_open":       False,
        "position":            None,
        "session_pnl":         0.0,
        "session_trades":      0,
        "session_wins":        0,
        "session_losses":      0,
        "sim_mode":            False,
        "score_compra":        None,
        "score_venda":         None,
        "vwap_context":        None,
    }

    # Estado do auto (variável _auto no scalper_bp)
    try:
        from blueprints.scalper_bp import _auto, _session, _pause_cfg
        result["auto_enabled"]       = _auto.get("enabled", False)
        result["consecutive_losses"] = _auto.get("consecutive_losses", 0)
        paused_until = _auto.get("paused_until", 0.0)
        result["paused"]             = _time.time() < paused_until
        result["paused_until"]       = (
            datetime.fromtimestamp(paused_until, _BRT).strftime("%H:%M")
            if result["paused"] else None
        )
        result["session_pnl"]    = _session.get("pnl", 0.0)
        result["session_trades"] = _session.get("trades", 0)
        result["session_wins"]   = _session.get("wins", 0)
        result["session_losses"] = _session.get("losses", 0)
    except Exception as exc:
        logger.debug("telemetry scalper _auto: %s", exc)

    # Posição aberta
    try:
        from services.scalper_service import get_scalper_position, get_sim_mode
        symbol = "WDON26"
        pos, err = get_scalper_position(symbol)
        if not err and pos:
            result["position_open"] = True
            result["position"]      = pos
        result["sim_mode"] = get_sim_mode()
    except Exception as exc:
        logger.debug("telemetry scalper position: %s", exc)

    # Score / contexto macro do scalper
    try:
        from services.scalper_service import get_scalper_data
        data = get_scalper_data("WDON26", aggr_seconds=30)
        macro = data.get("macro", {})
        result["score_compra"] = macro.get("score_compra")
        result["score_venda"]  = macro.get("score_venda")
        result["vwap_context"] = macro.get("vwap_context")
    except Exception as exc:
        logger.debug("telemetry scalper data: %s", exc)

    return result


# ---------------------------------------------------------------------------
# Telemetria resumida (Fase 1)
# ---------------------------------------------------------------------------

def get_telemetry_summary(
    tv_symbol: str = "BMFBOVESPA:WIN1!",
    interval:  str = "15",
) -> dict:
    """
    Payload resumido para consumo externo (Profit, dashboards, webhooks).
    Campos principais: simbolo, sinal, score, veredito IA, posicao, P&L.
    Agora inclui signal_strength, risk_level, market_regime, score_breakdown (Fase 1.5).
    """
    _cache_key = (tv_symbol, interval)
    _cached    = _CACHE_SUMMARY.get(_cache_key)
    if _cached and (_time.time() - _cached["ts"]) < _CACHE_TTL_SUMMARY:
        return _cached["data"]

    signal      = get_current_signal(tv_symbol, interval)
    position    = get_open_position(tv_symbol)
    at_state    = get_autotrade_state()
    daily_stats = get_daily_stats()

    sym_short = tv_symbol.split(":")[-1].replace("1!", "").replace("!", "")
    ok = signal.get("ok", False)
    trade_log = position.get("trade_log") or {}

    _result_summary = {
        "timestamp":       _now_brt(),
        "symbol":          sym_short,
        "tv_symbol":       tv_symbol,
        "interval":        interval,
        # Sinal tecnico
        "action":          signal.get("acao")      if ok else None,
        "score":           signal.get("score")     if ok else None,
        "score_raw":       signal.get("score_raw") if ok else None,
        "signal_ok":       ok,
        # Fase 1.5 — campos institucionais
        "signal_strength": compute_signal_strength(signal) if ok else 0,
        "risk_level":      compute_risk_level(signal)      if ok else 50,
        "market_regime":   compute_market_regime(signal)   if ok else None,
        "score_breakdown": compute_score_breakdown(signal) if ok else {},
        # Estado de posicao
        "position_open":   position["found"],
        "entry_price":     position["entry_price"],
        "current_price":   position["current_price"],
        "current_pnl":     position["pnl_brl"],
        "trade_direction": position["acao"],
        # IA
        "ai_verdict":      trade_log.get("ai_veredito"),
        "confidence":      trade_log.get("ai_confidence"),
        # Auto-trade
        "autotrade_on":    at_state["enabled"],
        "meta_diaria":     at_state["meta_diaria"],
        # P&L do dia
        "pnl_day_brl":     daily_stats.get("pnl_total_brl", 0.0),
        "pnl_day_pts":     daily_stats.get("pnl_total_pts", 0),
        "trades_today":    daily_stats.get("total", 0),
        "wins_today":      daily_stats.get("wins", 0),
        "losses_today":    daily_stats.get("losses", 0),
    }
    _CACHE_SUMMARY[_cache_key] = {"ts": _time.time(), "data": _result_summary}
    return _result_summary


# ---------------------------------------------------------------------------
# Telemetria completa (Fase 2)
# ---------------------------------------------------------------------------

def get_telemetry_full(
    tv_symbol_win: str = "BMFBOVESPA:WIN1!",
    tv_symbol_wdo: str = "BMFBOVESPA:WDO1!",
    interval:      str = "15",
) -> dict:
    """
    Payload completo para o Market Center e integracoes avancadas.
    Inclui indicadores detalhados, confluencias limpas, gestao de posicao, Scalper.
    """
    # Cache lookup
    _cache_key_full = (tv_symbol_win, tv_symbol_wdo, interval)
    _cached_full = _CACHE_FULL.get(_cache_key_full)
    if _cached_full and (_time.time() - _cached_full["ts"]) < _CACHE_TTL_FULL:
        return _cached_full["data"]

    # Sinais individuais
    win_signal = get_current_signal(tv_symbol_win, interval)
    wdo_signal = get_current_signal(tv_symbol_wdo, interval)

    # Posicoes abertas
    win_position = get_open_position(tv_symbol_win)
    wdo_position = get_open_position(tv_symbol_wdo)

    # Analise de gestao (Position Manager)
    win_analysis = get_position_analysis(tv_symbol_win, interval)
    wdo_analysis = get_position_analysis(tv_symbol_wdo, interval)

    # Estado global
    at_state     = get_autotrade_state()
    daily_stats  = get_daily_stats()
    weekly_stats = get_weekly_stats()
    scalper      = get_scalper_state()

    def _build_instrument(symbol, signal, position, analysis):
        s   = signal or {}
        ok  = s.get("ok", False)
        sym_short = symbol.split(":")[-1].replace("1!", "").replace("!", "")

        # Indicadores lidos diretamente do signal flat (nao existe chave "indicadores")
        ema9   = s.get("ema9")
        ema21  = s.get("ema21")
        ema200 = s.get("ema200")
        vwap   = s.get("vwap")
        price  = s.get("preco_atual") or s.get("entrada")

        ema_alignment  = (ema9 is not None and ema21 is not None and ema9 > ema21)       if ok else None
        vwap_bull      = (price is not None and vwap is not None and price > vwap)        if ok else None
        ema200_bull    = (price is not None and ema200 is not None and price > ema200)    if ok else None
        macd_positive  = (s.get("macd_hist") is not None and s.get("macd_hist") > 0)     if ok else None

        sinais_str     = " ".join(s.get("sinais") or []).lower()
        volume_confirm = ("volume forte" in sinais_str and "confirmando" in sinais_str)   if ok else None

        # Novos campos Fase 1.5
        confluences     = build_clean_confluences(s)  if ok else []
        score_breakdown = compute_score_breakdown(s)  if ok else {}
        market_regime   = compute_market_regime(s)    if ok else None
        signal_strength = compute_signal_strength(s)  if ok else 0
        risk_level      = compute_risk_level(s)       if ok else 50

        # Tendencia derivada de EMA9/21 + EMA200
        if ok:
            if ema_alignment and ema200_bull:
                tendencia = "BULLISH"
            elif (not ema_alignment) and (not ema200_bull):
                tendencia = "BEARISH"
            else:
                tendencia = "NEUTRO"
        else:
            tendencia = None

        tlog = position.get("trade_log") or {}

        _result_full = {
            "symbol":           sym_short,
            "tv_symbol":        symbol,
            # Sinal
            "action":           s.get("acao")      if ok else None,
            "score":            s.get("score")     if ok else None,
            "score_raw":        s.get("score_raw") if ok else None,
            "trend":            tendencia,
            "signal_ok":        ok,
            # Fase 1.5
            "signal_strength":   signal_strength,
            "risk_level":        risk_level,
            "market_regime":     market_regime,
            "score_breakdown":   score_breakdown,
            "confluences":       confluences,
            "confluence_count":  len(confluences),
            # Indicadores flat
            "ema_alignment":     ema_alignment,
            "vwap_bull":         vwap_bull,
            "rsi":               s.get("rsi"),
            "adx":               s.get("adx"),
            "macd_positive":     macd_positive,
            "volume_confirm":    volume_confirm,
            "ema200_bull":       ema200_bull,
            "htf_trend":         s.get("htf_trend"),
            "adx_filtered":      s.get("adx_filtered"),
            "directional_block": s.get("directional_block"),
            # Precos tecnicos
            "entry_price":  s.get("entrada") if ok else None,
            "sl":           s.get("stop")    if ok else None,
            "tp1":          s.get("tp1")     if ok else None,
            "tp2":          s.get("tp2")     if ok else None,
            "atr":          s.get("atr"),
            "suporte":      s.get("suporte"),
            "resistencia":  s.get("resistencia"),
            # Posicao aberta
            "position_open":    position["found"],
            "position_entry":   position["entry_price"],
            "position_price":   position["current_price"],
            "position_pnl":     position["pnl_brl"],
            "position_dir":     position["acao"],
            "ai_verdict":       tlog.get("ai_veredito"),
            "ai_confidence":    tlog.get("ai_confidence"),
            # Gestao (Position Manager)
            "recommendation":      (analysis or {}).get("recommendation"),
            "breakeven_suggested": (analysis or {}).get("breakeven_suggested"),
            "tp1_reached":         (analysis or {}).get("tp1_reached"),
            "candles_open":        (analysis or {}).get("candles_in_trade"),
        }
        return _result_full

    win_data = _build_instrument(tv_symbol_win, win_signal, win_position, win_analysis)
    wdo_data = _build_instrument(tv_symbol_wdo, wdo_signal, wdo_position, wdo_analysis)

    _result_full = {
        "timestamp":   _now_brt(),
        "interval":    interval,
        # Instrumentos
        "win":     win_data,
        "wdo":     wdo_data,
        # Scalper
        "scalper": {
            "auto_enabled":       scalper["auto_enabled"],
            "paused":             scalper["paused"],
            "paused_until":       scalper["paused_until"],
            "consecutive_losses": scalper["consecutive_losses"],
            "position_open":      scalper["position_open"],
            "position":           scalper["position"],
            "score_compra":       scalper["score_compra"],
            "score_venda":        scalper["score_venda"],
            "vwap_context":       scalper["vwap_context"],
            "sim_mode":           scalper["sim_mode"],
            "session": {
                "pnl":    scalper["session_pnl"],
                "trades": scalper["session_trades"],
                "wins":   scalper["session_wins"],
                "losses": scalper["session_losses"],
            },
        },
        # Auto-Trade Monitor
        "autotrade": {
            "enabled":    at_state["enabled"],
            "meta_diaria": at_state["meta_diaria"],
        },
        # Estatisticas
        "stats": {
            "day":  daily_stats,
            "week": weekly_stats,
        },
    }
    _CACHE_FULL[_cache_key_full] = {"ts": _time.time(), "data": _result_full}
    return _result_full
