import logging
import os
import re
from pathlib import Path
from uuid import uuid4

from flask import (
    Flask,
    jsonify,
    render_template,
    request,
    send_from_directory,
    url_for,
)
from werkzeug.utils import secure_filename

from agents.risk_manager import run_risk_manager
from agents.trader import run_trader
from agents.validator import run_validator
from services.config import Config
from services.db import (
    get_analysis,
    get_mt5_signal,
    init_auto_trades,
    init_db,
    init_mt5_signals,
    insert_analysis,
    insert_mt5_signal,
    journal_stats,
    list_analyses,
    list_mt5_signals,
    mt5_signals_stats,
    parse_pnl_value,
    update_execution,
    update_mt5_outcome,
)
from services.json_utils import extract_json_object, parse_trade_levels, risk_summary, trader_ativo_hint, trader_ativo_label
from services.market_service import (
    TV_TO_YF,
    TV_INTERVAL_TO_YF,
    tv_to_yf_symbol,
    get_candles,
    get_news,
    get_sentiment,
)
from services.technical_analysis import generate_signal

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

os.makedirs(Config.UPLOAD_FOLDER, exist_ok=True)

app = Flask(__name__, static_folder="static", template_folder="templates")
app.config["SECRET_KEY"] = Config.SECRET_KEY
app.config["MAX_CONTENT_LENGTH"] = Config.MAX_CONTENT_LENGTH

# ── Módulo Scalper (blueprint isolado) ────────────────────────────────────
from blueprints.scalper_bp import scalper_bp
app.register_blueprint(scalper_bp)

# ── Módulo Telemetria + Market Center (Fases 1-3 — somente leitura) ───────
from blueprints.telemetry_bp import telemetry_bp, init_analytics_db
app.register_blueprint(telemetry_bp)
init_analytics_db()

# ── Motor de Inteligência — Fases 1-5 (somente leitura, novos arquivos) ────
from blueprints.intelligence_bp import intelligence_bp
app.register_blueprint(intelligence_bp)

# ── Profit Bridge — Sprint Profit 1 (somente leitura, novos arquivos) ─────
from blueprints.profit_bp import profit_bp
app.register_blueprint(profit_bp)

# ── Capital Protection Engine (CPE) ───────────────────────────────────────
from blueprints.risk_bp import risk_bp
app.register_blueprint(risk_bp)

# ── Nova UI (STARK HUD) em paralelo — SOMENTE frontend, rota /newdashboard ──
# Aditivo: serve um novo template que consome os MESMOS /api/* existentes.
# Não altera nenhum módulo/endpoint. O /dashboard atual segue intacto.
from blueprints.newui_bp import newui_bp
app.register_blueprint(newui_bp)

# ── Inicialização única no primeiro request ────────────────────────────────
_app_initialized = False

# ── Debounce e cooldown de auto-trade no servidor ─────────────────────────
import time as _time
_last_autotrade_ts   = {}   # tv_symbol -> epoch da última execução de trade
_AUTOTRADE_COOLDOWN  = 15 * 60  # 15 min entre trades (segundos)

def _autotrade_in_cooldown(sym: str) -> bool:
    return (_time.time() - _last_autotrade_ts.get(sym, 0.0)) < _AUTOTRADE_COOLDOWN

def _register_autotrade_ts(sym: str):
    _last_autotrade_ts[sym] = _time.time()

def _cooldown_remaining_min(sym: str) -> int:
    rem = _AUTOTRADE_COOLDOWN - (_time.time() - _last_autotrade_ts.get(sym, 0.0))
    return max(0, int(rem / 60))

# ── Cooldown especial: proteção pós-abertura (09:00–09:30 BRT) ────────────────
_ABERTURA_COOLDOWN_SEC  = 20 * 60   # 20 min de proteção após trade na abertura
_last_abertura_trade_ts = {}        # tv_symbol -> epoch do último trade na janela

def _is_abertura_window() -> bool:
    """Retorna True se o horário BRT atual está na janela 09:00–09:30."""
    from datetime import datetime, timezone, timedelta
    brt = datetime.now(timezone(timedelta(hours=-3)))
    return brt.hour == 9 and brt.minute < 30

def _abertura_cooldown_active(sym: str) -> bool:
    """Retorna True se um trade foi feito na abertura e o cooldown ainda está ativo."""
    ts = _last_abertura_trade_ts.get(sym, 0.0)
    if ts == 0.0:
        return False
    return (_time.time() - ts) < _ABERTURA_COOLDOWN_SEC

def _abertura_cooldown_remaining_min(sym: str) -> int:
    rem = _ABERTURA_COOLDOWN_SEC - (_time.time() - _last_abertura_trade_ts.get(sym, 0.0))
    return max(0, int(rem / 60))

def _register_abertura_trade(sym: str):
    """Registra que um trade foi executado na janela de abertura."""
    _last_abertura_trade_ts[sym] = _time.time()

# ── Confirmação de fechamento — evita fechar por leitura instável do MT5 ──
# Exige N leituras consecutivas sem posição antes de registrar o fechamento.
_no_position_count   = {}   # tv_symbol -> int
_NO_POSITION_CONFIRM = 5    # leituras consecutivas sem posição para confirmar fechamento
                            # (aumentado de 3→5: MT5 pode levar 1-2 ciclos para registrar nova posição)
                            # 3 = ~9s com ticker rápido (XP/B3 estável) — era 8 só pra MetaQuotes instável

@app.before_request
def _startup_once():
    global _app_initialized
    if _app_initialized:
        return
    _app_initialized = True
    try:
        init_db()
        init_mt5_signals()
        init_auto_trades()
        logger.info("DB inicializado com sucesso.")
    except Exception as e:
        logger.warning("Erro ao inicializar DB: %s", e)
    try:
        from services.telegram_notifier import start_periodic_summary
        start_periodic_summary("BMFBOVESPA:WIN1!")
    except Exception as e:
        logger.warning("Erro ao iniciar Telegram scheduler: %s", e)
    try:
        from services.telegram_commander import start_commander
        start_commander()
    except Exception as e:
        logger.warning("Erro ao iniciar Telegram commander: %s", e)
    try:
        from services.risk_settings_service import init_cpe_tables
        init_cpe_tables()
    except Exception as e:
        logger.warning("CPE tables init falhou (nao bloqueante): %s", e)
    try:
        from services.market_close_scheduler import start_scheduler as _start_mcs
        _start_mcs()
    except Exception as e:
        logger.warning("Market Close Scheduler nao iniciou (nao bloqueante): %s", e)
    try:
        # v6.2 — Supervisor do Scalper (avalia a cada 20min; pausa se detectar sangria)
        from services.scalper_supervisor import start_supervisor_scheduler as _start_sup
        _start_sup()
    except Exception as e:
        logger.warning("Scalper Supervisor nao iniciou (nao bloqueante): %s", e)
    try:
        # v6.3 — Relatorio diario consolidado (Monitor + Scalper) via Telegram as 18:05 BRT
        from services.daily_report import start_daily_report_scheduler as _start_dr
        _start_dr()
    except Exception as e:
        logger.warning("Daily Report Scheduler nao iniciou (nao bloqueante): %s", e)


def allowed_file(filename: str) -> bool:
    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower() in Config.ALLOWED_EXTENSIONS
    )


def _safe_stored_name(original: str) -> str:
    base = secure_filename(original) or "chart"
    ext = Path(base).suffix.lower()
    if ext not in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
        ext = ".png"
    return f"{uuid4().hex}{ext}"


@app.route("/")
def home():
    return jsonify(
        {
            "status": "ok",
            "service": "Trade AI — B3",
            "llm_mode": Config.LLM_MODE,
            "dashboard": url_for("dashboard", _external=False),
        }
    )


@app.route("/dashboard")
def dashboard():
    trades = list_analyses(200)
    for t in trades:
        t["image_url"] = url_for("serve_upload", name=t["stored_filename"])
        t["ativo_label"] = trader_ativo_label(t.get("trader_json"))
        t["ativo_hint"] = trader_ativo_hint(t.get("trader_json"))
    ia_ok = bool(Config.OPENAI_API_KEY) or (
        Config.use_azure_openai()
        and bool(Config.AZURE_OPENAI_API_KEY)
        and bool(Config.AZURE_OPENAI_ENDPOINT)
    )
    return render_template(
        "dashboard.html",
        trades=trades,
        llm_mode=Config.LLM_MODE,
        ia_real_pronta=ia_ok,
        use_azure=Config.use_azure_openai(),
    )


@app.route("/uploads/<path:name>")
def serve_upload(name: str):
    if not re.match(r"^[a-f0-9]{32}\.[a-z0-9]+$", name, re.I):
        return jsonify({"error": "Arquivo inválido."}), 404
    return send_from_directory(Config.UPLOAD_FOLDER, name)


@app.route("/api/history")
def api_history():
    rows = list_analyses(200)
    for t in rows:
        t["image_url"] = url_for("serve_upload", name=t["stored_filename"])
        t["exec_recorded"] = bool(t.get("exec_recorded"))
        t["ativo_label"] = trader_ativo_label(t.get("trader_json"))
        t["ativo_hint"] = trader_ativo_hint(t.get("trader_json"))
    return jsonify({"items": rows})


@app.route("/api/stats")
def api_stats():
    period = request.args.get("period", "all")
    ref = request.args.get("ref")
    return jsonify(journal_stats(period=period, ref=ref))


@app.route("/api/analysis/<int:analysis_id>")
def api_analysis_detail(analysis_id: int):
    row = get_analysis(analysis_id)
    if not row:
        return jsonify({"error": "Análise não encontrada."}), 404
    row["image_url"] = url_for("serve_upload", name=row["stored_filename"])
    row["exec_recorded"] = bool(row.get("exec_recorded"))
    return jsonify(row)


@app.route("/api/analysis/<int:analysis_id>/exec", methods=["PATCH"])
def api_analysis_exec(analysis_id: int):
    row = get_analysis(analysis_id)
    if not row:
        return jsonify({"error": "Análise não encontrada."}), 404
    body = request.get_json(silent=True) or {}
    recorded = body.get("recorded", True)
    if isinstance(recorded, str):
        recorded = recorded.lower() in ("1", "true", "sim", "yes")
    entry = body.get("entry")
    exit_ = body.get("exit")
    pnl_raw = body.get("pnl")
    pnl = parse_pnl_value(pnl_raw) if pnl_raw not in (None, "") else None
    updated = update_execution(
        analysis_id,
        recorded=bool(recorded),
        entry=entry if isinstance(entry, str) else None,
        exit_=exit_ if isinstance(exit_, str) else None,
        pnl=pnl,
    )
    if not updated:
        return jsonify({"error": "Falha ao atualizar."}), 500
    updated["image_url"] = url_for("serve_upload", name=updated["stored_filename"])
    updated["exec_recorded"] = bool(updated.get("exec_recorded"))
    return jsonify(updated)


@app.route("/analyze", methods=["POST"])
def analyze():
    if "image" not in request.files:
        return jsonify({"error": "Envie o campo multipart 'image' com o arquivo do gráfico."}), 400
    file = request.files["image"]
    if not file or file.filename == "":
        return jsonify({"error": "Nenhum arquivo selecionado."}), 400
    if not allowed_file(file.filename):
        return (
            jsonify(
                {
                    "error": "Formato não permitido. Use: "
                    + ", ".join(sorted(Config.ALLOWED_EXTENSIONS))
                }
            ),
            400,
        )

    stored = _safe_stored_name(file.filename)
    path = os.path.join(Config.UPLOAD_FOLDER, stored)
    file.save(path)

    try:
        trader_output = run_trader(path)
        validator_output = run_validator(path, trader_output)
        risk_output = run_risk_manager(path, trader_output, validator_output)
    except RuntimeError as e:
        logger.warning("LLM indisponível: %s", e)
        return jsonify({"error": str(e)}), 503
    except Exception:
        logger.exception("Falha no pipeline de agentes")
        return jsonify({"error": "Erro ao executar a análise. Tente novamente."}), 500

    risk_parsed = extract_json_object(risk_output)
    decisao, score_final, permitir_trade = risk_summary(risk_parsed)

    row_id, created_at = insert_analysis(
        stored_filename=stored,
        trader_json=trader_output,
        validator_json=validator_output,
        risk_json=risk_output,
        decisao=decisao,
        score_final=score_final,
        permitir_trade=permitir_trade,
    )

    return jsonify(
        {
            "id": row_id,
            "created_at": created_at,
            "trader": trader_output,
            "validator": validator_output,
            "risk_manager": risk_output,
            "resumo": {
                "decisao": decisao,
                "score_final": score_final,
                "permitir_trade": permitir_trade,
            },
            "image_url": url_for("serve_upload", name=stored),
            "exec_recorded": False,
            "exec_entry": None,
            "exec_exit": None,
            "exec_pnl": None,
            "exec_logged_at": None,
        }
    )


# ---------------------------------------------------------------------------
# Monitor — Sinais de trading em tempo real (MT5 primário + Yahoo Finance fallback)
# ---------------------------------------------------------------------------

@app.route("/api/monitor/signals")
def api_monitor_signals():
    """
    GET /api/monitor/signals?tv_symbol=BMFBOVESPA:WIN1!&interval=15
    Busca candles via MetaTrader5 (WINM26 real) ou Yahoo Finance (fallback),
    calcula indicadores técnicos e retorna sinal COMPRA/VENDA/NEUTRO.
    """
    tv_symbol = request.args.get("tv_symbol", "BMFBOVESPA:WIN1!")
    interval  = request.args.get("interval", "15")   # minutos ou D/W (TradingView format)

    # Período/intervalo Yahoo Finance — usado apenas no fallback
    period_yf, interval_yf = TV_INTERVAL_TO_YF.get(interval, ("5d", "15m"))

    # Busca candles — tenta MT5 primeiro (WIN/WDO), cai para Yahoo Finance se necessário
    df, error = get_candles(
        tv_symbol,
        period=period_yf,
        interval=interval_yf,
        tv_interval=interval,   # ativa roteamento MT5
    )
    if error or df is None:
        return jsonify({
            "ok": False,
            "error": error or "Sem dados.",
            "tv_symbol": tv_symbol,
        }), 200

    # Busca timeframe maior (1h) para confirmação multi-TF
    # Não busca quando o próprio TF já é ≥ 1h
    htf_df = None
    if interval_yf not in ("60m", "1h", "1d", "1wk"):
        htf_df, _ = get_candles(
            tv_symbol,
            period="1mo",
            interval="1h",
            tv_interval="60",   # 1h no MT5
        )

    signal = generate_signal(df, htf_df=htf_df)
    if signal is None:
        return jsonify({
            "ok": False,
            "error": f"Dados insuficientes para análise técnica ({len(df)} candles).",
            "tv_symbol": tv_symbol,
        }), 200

    # Indica a fonte de dados usada na resposta
    from services.market_service import MT5_AVAILABLE, TV_TO_MT5
    fonte = "mt5" if (MT5_AVAILABLE and tv_symbol.upper() in TV_TO_MT5) else "yfinance"

    # Detecta posicao orfã: MT5 tem posicao aberta mas DB nao tem registro aberto.
    # Isso ocorre apos falso fechamento — o frontend pode usar esse flag para forcar MANAGE.
    orphan_position = False
    try:
        from services.trade_executor import get_open_positions
        from services.trade_log import get_open_auto_trade
        positions, _ = get_open_positions(tv_symbol)
        if positions and not get_open_auto_trade(tv_symbol):
            orphan_position = True
    except Exception:
        pass

    return jsonify({
        "ok":              True,
        "tv_symbol":       tv_symbol,
        "fonte":           fonte,
        "interval":        interval,
        "period":          period_yf,
        "orphan_position": orphan_position,
        **signal,
    })


@app.route("/api/market/news")
def api_market_news():
    """
    GET /api/market/news?finnhub_symbol=AAPL&category=general
    Retorna notícias do Finnhub.
    """
    finnhub_symbol = request.args.get("finnhub_symbol") or None
    category = request.args.get("category", "general")
    news, error = get_news(finnhub_symbol=finnhub_symbol, category=category)
    return jsonify({
        "ok": error is None,
        "items": news,
        "error": error,
        "finnhub_configured": bool(os.getenv("FINNHUB_API_KEY", "")),
    })


@app.route("/api/market/sentiment")
def api_market_sentiment():
    """GET /api/market/sentiment?finnhub_symbol=AAPL"""
    finnhub_symbol = request.args.get("finnhub_symbol", "")
    if not finnhub_symbol:
        return jsonify({"ok": False, "error": "Parâmetro finnhub_symbol obrigatório."}), 400
    sentiment, error = get_sentiment(finnhub_symbol)
    return jsonify({"ok": error is None, "data": sentiment, "error": error})


@app.route("/api/monitor/instruments")
def api_monitor_instruments():
    """Lista instrumentos disponíveis com seus símbolos TradingView e Yahoo Finance."""
    instruments = [
        {"label": "Mini Índice (WIN1!)",   "tv": "BMFBOVESPA:WIN1!", "yf": "^BVSP",     "finnhub": None},
        {"label": "Mini Dólar (WDO1!)",    "tv": "BMFBOVESPA:WDO1!", "yf": "BRL=X",     "finnhub": None},
        {"label": "Ibovespa",              "tv": "BMFBOVESPA:IBOV",  "yf": "^BVSP",     "finnhub": None},
        {"label": "Petrobras (PETR4)",     "tv": "BMFBOVESPA:PETR4", "yf": "PETR4.SA",  "finnhub": "PBR"},
        {"label": "Raiadrogasil (RADL3)",  "tv": "BMFBOVESPA:RADL3", "yf": "RADL3.SA",  "finnhub": None},
        {"label": "Vale (VALE3)",          "tv": "BMFBOVESPA:VALE3", "yf": "VALE3.SA",  "finnhub": "VALE"},
        {"label": "Itaú (ITUB4)",          "tv": "BMFBOVESPA:ITUB4", "yf": "ITUB4.SA",  "finnhub": "ITUB"},
        {"label": "Bradesco (BBDC4)",      "tv": "BMFBOVESPA:BBDC4", "yf": "BBDC4.SA",  "finnhub": None},
        {"label": "Banco do Brasil (BBAS3)","tv": "BMFBOVESPA:BBAS3","yf": "BBAS3.SA",  "finnhub": None},
        {"label": "Ambev (ABEV3)",         "tv": "BMFBOVESPA:ABEV3", "yf": "ABEV3.SA",  "finnhub": "ABEV"},
        {"label": "WEG (WEGE3)",           "tv": "BMFBOVESPA:WEGE3", "yf": "WEGE3.SA",  "finnhub": None},
        {"label": "EUR/USD (Forex 24/5)",    "tv": "FX:EURUSD",           "yf": "EURUSD=X",   "finnhub": None},
        {"label": "GBP/USD (Forex 24/5)",    "tv": "FX:GBPUSD",           "yf": "GBPUSD=X",   "finnhub": None},
        {"label": "Ouro XAU/USD (24/5)",     "tv": "FX:XAUUSD",           "yf": "GC=F",       "finnhub": None},
        {"label": "Bitcoin (BTC/USD) 24h",   "tv": "CRYPTO:BTCUSD",       "yf": "BTC-USD",    "finnhub": "BINANCE:BTCUSDT"},
    ]
    return jsonify({"items": instruments})


# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# Monitor MT5 ao Vivo -- candles + indicadores + sinal em um endpoint so
# ---------------------------------------------------------------------------

@app.route("/api/monitor/mt5")
def api_monitor_mt5():
    """
    GET /api/monitor/mt5?tv_symbol=BMFBOVESPA:WIN1!&interval=15
    Retorna candles OHLCV + series de indicadores (EMA/VWAP/BB) + sinal de trading.
    Usado pelo grafico Lightweight Charts do Monitor MT5 ao Vivo.
    """
    import pandas as pd
    from services.market_service import MT5_AVAILABLE, TV_TO_MT5
    from services.technical_analysis import compute_indicators

    tv_symbol = request.args.get("tv_symbol", "BMFBOVESPA:WIN1!")
    interval  = request.args.get("interval", "15")

    period_yf, interval_yf = TV_INTERVAL_TO_YF.get(interval, ("5d", "15m"))

    df, error = get_candles(tv_symbol, period=period_yf, interval=interval_yf, tv_interval=interval)
    if error or df is None:
        return jsonify({"ok": False, "error": error or "Sem dados."}), 200

    htf_df = None
    if interval_yf not in ("60m", "1h", "1d", "1wk"):
        htf_df, _ = get_candles(tv_symbol, period="1mo", interval="1h", tv_interval="60")

    signal = generate_signal(df, htf_df=htf_df)
    if signal is None:
        return jsonify({"ok": False, "error": "Dados insuficientes para analise ({} candles).".format(len(df))}), 200

    df_ind = compute_indicators(df)

    def ts(idx):
        if hasattr(idx, "timestamp"):
            return int(idx.timestamp())
        return int(pd.Timestamp(idx).timestamp())

    def series_to_list(s):
        return [{"time": ts(i), "value": round(float(v), 3)}
                for i, v in s.items() if pd.notna(v)]

    candles = []
    for idx, row in df.iterrows():
        candles.append({
            "time":   ts(idx),
            "open":   round(float(row["Open"]),  2),
            "high":   round(float(row["High"]),  2),
            "low":    round(float(row["Low"]),   2),
            "close":  round(float(row["Close"]), 2),
            "volume": int(row["Volume"]) if pd.notna(row["Volume"]) else 0,
        })

    fonte = "mt5" if (MT5_AVAILABLE and tv_symbol.upper() in TV_TO_MT5) else "yfinance"

    return jsonify({
        "ok":       True,
        "fonte":    fonte,
        "tv_symbol": tv_symbol,
        "interval": interval,
        "candles":  candles,
        "ema9":     series_to_list(df_ind["ema9"]),
        "ema21":    series_to_list(df_ind["ema21"]),
        "ema50":    series_to_list(df_ind["ema50"]),
        "vwap":     series_to_list(df_ind["vwap"].dropna()),
        "bb_upper": series_to_list(df_ind["bb_upper"]),
        "bb_lower": series_to_list(df_ind["bb_lower"]),
        "bb_mid":   series_to_list(df_ind["bb_mid"]),
        "signal":   signal,
    })


# ---------------------------------------------------------------------------
# Verificação de alvos / stop (outcome)
# ---------------------------------------------------------------------------

@app.route("/api/stats/outcome")
def api_outcome_summary():
    """Resumo global de TP/Stop para os cards do dashboard."""
    return jsonify(outcome_summary())


@app.route("/api/analysis/<int:analysis_id>/verify", methods=["POST"])
def api_verify_outcome(analysis_id: int):
    """
    Busca candles 1m via MT5 a partir do momento do sinal e verifica
    se TP1/TP2/TP3 e stop foram atingidos. Salva resultado no banco.
    """
    from services.outcome_checker import check_outcome

    row = get_analysis(analysis_id)
    if not row:
        return jsonify({"error": "Análise não encontrada."}), 404

    trader_raw = row.get("trader_json") or ""
    lvl = parse_trade_levels(trader_raw)

    trader_parsed = extract_json_object(trader_raw) or {}
    ativo = trader_parsed.get("ativo", "") or ""

    outcome = check_outcome(
        ativo=ativo,
        acao=lvl["acao"] or "",
        created_at_iso=row["created_at"],
        entrada=lvl["entrada"],
        stop=row.get("stop_price") or lvl["stop"],
        tp1=row.get("tp1_price")  or lvl["tp1"],
        tp2=row.get("tp2_price")  or lvl["tp2"],
        tp3=row.get("tp3_price")  or lvl["tp3"],
    )

    update_outcome(analysis_id, **{k: v for k, v in outcome.items()
                                   if k in ("tp1_hit","tp2_hit","tp3_hit","stop_hit",
                                            "candles_checked","error")})

    updated = get_analysis(analysis_id)
    updated["image_url"] = url_for("serve_upload", name=updated["stored_filename"])
    updated["exec_recorded"] = bool(updated.get("exec_recorded"))
    return jsonify({"ok": True, "outcome": outcome, "analysis": updated})


@app.route("/api/analyses/verify-batch", methods=["POST"])
def api_verify_batch():
    """
    Dispara verificação para os N trades mais recentes ainda não verificados.
    Retorna lista com resultados individuais.
    """
    from services.outcome_checker import check_outcome

    body = request.get_json(silent=True) or {}
    limit = min(int(body.get("limit", 20)), 50)

    trades = list_analyses(limit)
    results = []
    for t in trades:
        if t.get("outcome_checked_at"):
            results.append({"id": t["id"], "skipped": True, "reason": "já verificado"})
            continue

        trader_raw = t.get("trader_json") or ""
        lvl = parse_trade_levels(trader_raw)
        trader_parsed = extract_json_object(trader_raw) or {}
        ativo = trader_parsed.get("ativo", "") or ""

        outcome = check_outcome(
            ativo=ativo,
            acao=lvl["acao"] or "",
            created_at_iso=t["created_at"],
            entrada=lvl["entrada"],
            stop=t.get("stop_price") or lvl["stop"],
            tp1=t.get("tp1_price")  or lvl["tp1"],
            tp2=t.get("tp2_price")  or lvl["tp2"],
            tp3=t.get("tp3_price")  or lvl["tp3"],
        )

        update_outcome(t["id"], **{k: v for k, v in outcome.items()
                                   if k in ("tp1_hit","tp2_hit","tp3_hit","stop_hit",
                                            "candles_checked","error")})
        results.append({"id": t["id"], "skipped": False, **outcome})

    return jsonify({"ok": True, "results": results})





# ---------------------------------------------------------------------------
# Relatório Mensal de Sinais MT5
# ---------------------------------------------------------------------------

@app.route("/api/monitor/signals/report")
def api_mt5_signals_report():
    """
    GET /api/monitor/signals/report?month=2026-05
    Retorna resumo + detalhe de todos os sinais do mês.
    """
    month = request.args.get("month", "")          # ex: "2026-05"
    if not month or len(month) < 7:
        from datetime import datetime, timezone
        month = datetime.now(timezone.utc).strftime("%Y-%m")

    year, mon = int(month[:4]), int(month[5:7])

    with __import__("sqlite3").connect(__import__("services.config", fromlist=["Config"]).Config.DATABASE_PATH) as conn:
        conn.row_factory = __import__("sqlite3").Row
        rows = conn.execute("""
            SELECT * FROM mt5_signals
            WHERE acao IN ('COMPRA','VENDA')
              AND strftime('%Y-%m', created_at) = ?
            ORDER BY id DESC
        """, (month,)).fetchall()

    items = [dict(r) for r in rows]

    # Métricas
    total   = len(items)
    verif   = sum(1 for r in items if r["outcome_checked_at"])
    tp1_hit = sum(1 for r in items if r["tp1_hit"])
    tp2_hit = sum(1 for r in items if r["tp2_hit"])
    tp3_hit = sum(1 for r in items if r["tp3_hit"])
    stop_h  = sum(1 for r in items if r["stop_hit"])
    sem_res = sum(1 for r in items if r["outcome_checked_at"] and not r["tp1_hit"] and not r["stop_hit"])
    sucesso = sum(1 for r in items if r["tp1_hit"])   # pelo menos TP1

    # Agrupa por dia
    from collections import defaultdict
    by_day = defaultdict(list)
    for r in items:
        day = r["created_at"][:10]
        by_day[day].append(r)

    daily = []
    for day in sorted(by_day.keys(), reverse=True):
        day_rows = by_day[day]
        daily.append({
            "data":    day,
            "total":   len(day_rows),
            "tp1_hit": sum(1 for x in day_rows if x["tp1_hit"]),
            "tp2_hit": sum(1 for x in day_rows if x["tp2_hit"]),
            "tp3_hit": sum(1 for x in day_rows if x["tp3_hit"]),
            "stop_hit":sum(1 for x in day_rows if x["stop_hit"]),
            "sem_res": sum(1 for x in day_rows if x["outcome_checked_at"] and not x["tp1_hit"] and not x["stop_hit"]),
        })

    return jsonify({
        "ok": True,
        "month": month,
        "summary": {
            "total": total, "verificados": verif,
            "tp1_hit": tp1_hit, "tp2_hit": tp2_hit, "tp3_hit": tp3_hit,
            "stop_hit": stop_h, "sem_resultado": sem_res,
            "sucesso": sucesso,
            "assertividade_pct": round(sucesso / verif * 100) if verif else 0,
            "stop_pct":          round(stop_h  / verif * 100) if verif else 0,
        },
        "by_day": daily,
        "items":  items,
    })


# ---------------------------------------------------------------------------
# Auto-Trade (DEMO) — execução automática via MT5
# ---------------------------------------------------------------------------

@app.route("/api/autotrade/remote-state", methods=["GET", "POST"])
def api_autotrade_remote_state():
    """GET: retorna se auto-trade está habilitado. POST: atualiza o estado."""
    from services.telegram_commander import is_autotrade_enabled, set_autotrade_enabled
    if request.method == "POST":
        body = request.get_json(silent=True) or {}
        enabled = bool(body.get("enabled", True))
        set_autotrade_enabled(enabled)
        return jsonify({"ok": True, "enabled": enabled})
    return jsonify({"ok": True, "enabled": is_autotrade_enabled()})


@app.route("/api/autotrade/symbol-gate", methods=["GET", "POST"])
def api_autotrade_symbol_gate():
    """Trava de trades por ativo (token). POST {token, enabled}. Só bloqueia execuções."""
    from services.trade_gate import set_token, disabled_tokens
    if request.method == "POST":
        body = request.get_json(silent=True) or {}
        set_token(body.get("token", ""), bool(body.get("enabled", True)))
        return jsonify({"ok": True, "disabled": disabled_tokens()})
    return jsonify({"ok": True, "disabled": disabled_tokens()})


@app.route("/api/autotrade/status")
def api_autotrade_status():
    """Retorna posições abertas do bot."""
    from services.trade_executor import get_open_positions
    tv_symbol = request.args.get("tv_symbol", "BMFBOVESPA:WIN1!")
    positions, error = get_open_positions(tv_symbol)
    return jsonify({"ok": error is None, "positions": positions, "error": error})


# Dedup de bloqueios da IA: evita gravar/notificar o MESMO bloqueio em loop
# quando o feed fica congelado (ex.: app ligada antes da abertura, sinal parado).
# Chave: símbolo → (assinatura acao:score, timestamp do último registro).
_ia_block_dedup: dict = {}
_IA_BLOCK_COOLDOWN = 900   # 15 min: só repete o aviso do mesmo sinal após isso


@app.route("/api/autotrade/execute", methods=["POST"])
def api_autotrade_execute():
    """
    Executa uma ordem de compra ou venda via MT5 com validacao AI pre-trade.
    Salva o trade no log para rastreamento completo ate o fechamento.
    """
    # Verifica se auto-trade está habilitado (pode ter sido pausado via Telegram)
    from services.telegram_commander import is_autotrade_enabled
    if not is_autotrade_enabled():
        return jsonify({
            "ok": False, "result": None,
            "error": "Auto-trade pausado remotamente via Telegram. Use /ativar para reativar.",
        })

    # Verifica se meta diária já foi atingida (proteção de lucro)
    try:
        from services.telegram_commander import get_meta_diaria
        from services.trade_log import auto_trades_stats
        _meta = get_meta_diaria()
        if _meta is not None:
            _stats  = auto_trades_stats(today_only=True)
            _pnl_hj = float(_stats.get("pnl_total_brl") or 0.0)
            if _pnl_hj >= _meta:
                from services.telegram_commander import set_autotrade_enabled
                set_autotrade_enabled(False)   # garante que está pausado
                return jsonify({
                    "ok": False, "result": None,
                    "error": (
                        f"Meta diária de R${_meta:.0f} já atingida "
                        f"(P&L hoje: R${_pnl_hj:+.2f}). "
                        "Auto-trade pausado para proteger o lucro."
                    ),
                })
    except Exception as _meta_exc:
        logger.warning("Verificação de meta falhou (não bloqueia): %s", _meta_exc)

    # Lê o símbolo antecipadamente para aplicar cooldowns por ativo
    _early_body  = request.get_json(silent=True, force=True) or {}
    _sym_key     = (_early_body.get("tv_symbol") or "").strip().upper() or "DEFAULT"

    # ── TRAVA POR ATIVO (safety switch server-side) ───────────────────────────
    # Recusa abrir trade de um ativo marcado OFF, venha de onde vier (painel novo,
    # dashboard clássico, outra aba). Só bloqueia — não toca no motor.
    try:
        from services.trade_gate import symbol_enabled
        if not symbol_enabled(_sym_key):
            logger.warning("Execute BLOQUEADO: trades OFF para %s (trava por ativo).", _sym_key)
            return jsonify({
                "ok": False, "result": None,
                "error": f"🔒 Trades DESATIVADOS para {_sym_key} (trava por ativo). "
                         f"Reative o botão TRADES do ativo no painel.",
            })
    except Exception as _tg_exc:
        logger.warning("trade_gate check falhou (não bloqueia): %s", _tg_exc)

    # ── TRAVA DE HORÁRIO DE PREGÃO ────────────────────────────────────────────
    # WIN/WDO (índice/dólar) só operam 09:00–18:30 BRT, seg–sex. Fora disso o
    # feed pode ficar congelado no último sinal; não faz sentido processar. Retorna
    # cedo, ANTES da IA/notificação — evita ordens e spam fora de hora.
    if any(k in _sym_key for k in ("WIN", "WDO", "IND", "DOL")):
        from datetime import datetime as _dtn, timezone as _tzn, timedelta as _tdn
        _now_brt = _dtn.now(_tzn(_tdn(hours=-3)))
        _mins = _now_brt.hour * 60 + _now_brt.minute
        if _now_brt.weekday() >= 5 or _mins < 9 * 60 or _mins >= 18 * 60 + 30:
            return jsonify({
                "ok": False, "result": None,
                "error": f"⏰ Fora do horário de pregão (09:00–18:30, seg–sex) — {_sym_key} não opera agora.",
            })

    # Cooldown no servidor — evita re-entrada mesmo se o JS resetar o debounce
    if _autotrade_in_cooldown(_sym_key):
        rem = _cooldown_remaining_min(_sym_key)
        return jsonify({
            "ok": False, "result": None,
            "error": f"Cooldown ativo no servidor — próxima entrada em ~{rem} min.",
        })

    # ── Bloqueio de abertura do pregão: WIN e WDO não operam entre 09:00-09:15 ──
    # Primeiros 15 min têm ruído excessivo que gera stops desnecessários.
    _is_win_wdo = any(k in _sym_key for k in ("WIN", "WDO", "IND", "DOL"))
    if _is_win_wdo:
        from datetime import datetime, timezone, timedelta
        _brt = datetime.now(timezone(timedelta(hours=-3)))
        if _brt.hour == 9 and _brt.minute < 15:
            _mins_left = 15 - _brt.minute
            return jsonify({
                "ok": False, "result": None,
                "error": (
                    f"Bloqueio de abertura: {_sym_key} não opera entre 09:00-09:15 "
                    f"({_mins_left}min restantes — libera às 09:15)."
                ),
            })

    # Proteção pós-abertura: após trade executado entre 09:00-09:30 BRT, aguarda 20 min
    if _abertura_cooldown_active(_sym_key):
        rem = _abertura_cooldown_remaining_min(_sym_key)
        return jsonify({
            "ok": False, "result": None,
            "error": f"Cooldown pós-abertura ativo — proteção de 20 min após trade na janela 09:00-09:30. Próxima entrada em ~{rem} min.",
        })

    from services.trade_executor import execute_trade, DEFAULT_VOLUME, SCORE_MIN
    from services.signal_validator_ai import validate_signal_with_ai
    from services.trade_log import save_auto_trade
    body = _early_body  # reutiliza o body já lido

    tv_symbol = body.get("tv_symbol", "")
    acao      = body.get("acao", "")
    score     = int(body.get("score") or 0)
    entrada   = body.get("entrada")
    sl        = body.get("stop")
    tp1       = body.get("tp1")
    volume    = float(body.get("volume") or DEFAULT_VOLUME)
    interval  = str(body.get("interval", "15"))
    signal    = body.get("signal") or {}   # sinal tecnico completo (opcional)

    # Validacao via Azure OpenAI (nao bloqueia se IA indisponivel)
    ai_result = None
    if signal and acao in ("COMPRA", "VENDA"):
        try:
            # Hard timeout via thread — funciona no Windows (signal.alarm nao funciona)
            from concurrent.futures import ThreadPoolExecutor, TimeoutError as _FutTimeout
            import functools as _ft
            _ai_fn = _ft.partial(
                validate_signal_with_ai,
                signal=signal,
                tv_symbol=tv_symbol,
                interval=interval,
                score_threshold=SCORE_MIN,
            )
            with ThreadPoolExecutor(max_workers=1) as _pool:
                _fut = _pool.submit(_ai_fn)
                try:
                    ai_result = _fut.result(timeout=12)
                except _FutTimeout:
                    # v6 ASSERTIVIDADE: timeout da IA agora FALHA FECHADO (aguarda,
                    # nao executa). Antes aprovava automaticamente no timeout.
                    logger.warning("AI validator timeout (12s) — fail-closed: AGUARDAR")
                    ai_result = {
                        "aprovado": False, "confianca": 0, "veredito": "AGUARDAR",
                        "motivo": "Timeout IA (12s) — gate fail-closed: trade nao executado.",
                        "alertas": [], "tp1_sugerido": None, "ia_usada": True,
                    }
            # AGUARDAR e BLOQUEAR ambos impedem execução automática.
            # BLOQUEAR = IA identificou contradição real.
            # AGUARDAR = IA identificou incerteza / setup duvidoso.
            # Em auto-trade, qualquer dúvida da IA deve paralisar a entrada.
            _veredito_ia = ai_result.get("veredito", "")
            if _veredito_ia in ("BLOQUEAR", "AGUARDAR"):
                _close_reason = "BLOQUEADO_IA" if _veredito_ia == "BLOQUEAR" else "AGUARDADO_IA"
                # Dedup: só grava/notifica se o bloqueio MUDOU (nova assinatura) ou
                # se passou o cooldown. Evita loop de gravação/spam com feed parado.
                import time as _tt
                _sig = f"{_veredito_ia}:{acao}:{score}"
                _prev = _ia_block_dedup.get(_sym_key)
                _fresh = not (_prev and _prev[0] == _sig and (_tt.time() - _prev[1]) < _IA_BLOCK_COOLDOWN)
                if _fresh:
                    _ia_block_dedup[_sym_key] = (_sig, _tt.time())
                    # Registra no histórico para rastreabilidade
                    try:
                        from services.trade_log import save_auto_trade, close_auto_trade
                        blocked_id = save_auto_trade(
                            tv_symbol    = tv_symbol,
                            interval     = interval,
                            acao         = acao,
                            entry_price  = entrada,
                            volume       = volume,
                            sl_initial   = sl,
                            tp1_initial  = tp1,
                            score        = score,
                            ai_validated = True,
                            ai_confidence= ai_result.get("confianca"),
                            ai_veredito  = _veredito_ia,
                            ai_motivo    = ai_result.get("motivo", "")[:200],
                        )
                        close_auto_trade(
                            trade_id     = blocked_id,
                            exit_price   = entrada or 0,
                            close_reason = _close_reason,
                            pnl_pts      = 0,
                            pnl_brl      = 0.0,
                        )
                    except Exception as _e:
                        logger.warning("Falha ao registrar bloqueio IA: %s", _e)
                    # Notifica Telegram (só no bloqueio novo)
                    try:
                        from services.telegram_notifier import notify_ia_blocked
                        notify_ia_blocked(tv_symbol, acao, score, ai_result.get("motivo", ""))
                    except Exception:
                        pass
                _msg_prefix = "IA bloqueou" if _veredito_ia == "BLOQUEAR" else "IA pediu aguardar"
                return jsonify({
                    "ok":    False,
                    "result": None,
                    "error": f"{_msg_prefix} o trade: {ai_result.get('motivo', '')}",
                    "ai": ai_result,
                })
        except Exception as ai_exc:
            # v6 ASSERTIVIDADE: falha no bloco de validacao IA agora BLOQUEIA a
            # entrada (fail-closed), em vez de seguir e executar sem validacao.
            logger.warning("AI validator falhou — fail-closed, trade bloqueado: %s", ai_exc)
            return jsonify({
                "ok": False, "result": None,
                "error": f"Validacao IA falhou (fail-closed) — trade nao executado: {ai_exc}",
            })

    # ── Partial-close mode: 3 contratos, TPs calculados pelo R/R do SL ──────
    # TPs derivados do risco real (distância entrada→SL), nunca do sinal.
    # MT5 TP = TP1 (safety net): se o monitor bg não disparar a tempo,
    # MT5 fecha os 3 no TP1 — lucro assegurado.
    _pcm_tp1 = None    # TP1 (1:1 R/R sobre SL) — safety net no MT5
    _pcm_tp2 = None    # TP2 (1.5:1 R/R sobre SL) — alvo do contrato restante
    try:
        from services import partial_close_manager as pcm
        if pcm.is_enabled(tv_symbol) and acao in ("COMPRA", "VENDA") and sl:
            _entrada_f = float(entrada) if entrada else 0.0
            _sl_f      = float(sl)
            volume     = float(pcm.ENTRY_VOLUME)   # 3 contratos
            if _entrada_f:
                # TPs calculados puramente pelo R/R — ignora TP do sinal
                _pcm_tp1, _pcm_tp2 = pcm.calc_tp_from_rr(_entrada_f, _sl_f, acao)
            else:
                # Ordem a mercado: sem entrada → adota TP do sinal como TP1 provisório
                _pcm_tp1 = float(tp1) if tp1 else None
                _pcm_tp2 = None
            # MT5 TP aponta para TP1 calculado (safety net para todos os 3 contratos)
            if _pcm_tp1:
                tp1 = _pcm_tp1
            logger.info(
                "Partial-mode ON: vol=%d  entry=%.0f  sl=%.0f  "
                "→ TP1(1:1)=%.0f  TP2(1.5:1)=%s",
                pcm.ENTRY_VOLUME, _entrada_f, _sl_f, _pcm_tp1 or 0,
                f"{_pcm_tp2:.0f}" if _pcm_tp2 else "recalc pós-execução",
            )
    except Exception as _pcm_init_err:
        logger.warning("Partial-config init falhou: %s", _pcm_init_err)

    # ── Capital Protection Engine — veto antes de executar ────────────────
    try:
        from services.capital_protection_engine import cpe_evaluate
        _sl_pts = None
        try:
            if sl and entrada:
                _sl_pts = abs(float(sl) - float(entrada))
        except Exception:
            pass
        _cpe = cpe_evaluate(
            tv_symbol=tv_symbol,
            acao=acao,
            sl_pts=_sl_pts,
            score=score,
        )
        if not _cpe.get("allowed", True):
            logger.warning("CPE bloqueou trade: %s | %s", _cpe.get("block_code"), _cpe.get("reason"))
            return jsonify({"ok": False, "result": None, "error": f"CPE: {_cpe['reason']}", "cpe": _cpe})
        # Soft Target: ajusta limiares se CPE retornar overrides
        if _cpe.get("score_min_override"):
            score_min_eff = max(SCORE_MIN, _cpe["score_min_override"])
            if score < score_min_eff:
                return jsonify({"ok": False, "result": None,
                                "error": f"CPE Soft-Target: score {score} < mínimo conservador {score_min_eff}", "cpe": _cpe})
    except Exception as _cpe_err:
        logger.warning("CPE check falhou (não bloqueante): %s", _cpe_err)

    # ── Order flow (v6.1): book/DOM + agressão + volume real do MT5 ───────────
    # Confirmação final de microestrutura. Só bloqueia quando o fluxo contradiz
    # FORTEMENTE a direção (book E tape contra). Fail-safe: sem dados → não veta.
    _of = None
    try:
        from services.orderflow_mt5 import confirm_direction as _of_confirm
        _of = _of_confirm(tv_symbol, acao)
        if not _of.get("allow", True):
            logger.warning("Order flow bloqueou %s %s: %s", acao, tv_symbol, _of.get("reason"))
            return jsonify({"ok": False, "result": None,
                            "error": f"Order flow: {_of.get('reason')}", "orderflow": _of})
    except Exception as _of_err:
        logger.warning("Order flow check falhou (não bloqueante): %s", _of_err)

    result, error = execute_trade(
        tv_symbol=tv_symbol,
        acao=acao,
        score=score,
        entrada=entrada,
        sl=sl,
        tp1=tp1,
        volume=volume,
    )

    # Salva no log de trades
    trade_log_id = None
    if result and not error:
        try:
            trade_log_id = save_auto_trade(
                tv_symbol    = tv_symbol,
                interval     = interval,
                acao         = acao,
                entry_price  = result.get("price") or entrada,
                volume       = volume,
                sl_initial   = sl,
                tp1_initial  = tp1,
                score        = score,
                mt5_ticket   = result.get("order"),
                mt5_deal     = result.get("deal"),
                ai_validated = bool(ai_result and ai_result.get("ia_usada")),
                ai_confidence= ai_result.get("confianca") if ai_result else None,
                ai_veredito  = ai_result.get("veredito")  if ai_result else None,
                ai_motivo    = ai_result.get("motivo")    if ai_result else None,
            )
        except Exception as log_exc:
            logger.warning("Falha ao salvar trade no log: %s", log_exc)

    # Registra timestamp para cooldown no servidor (por símbolo)
    if result and not error:
        _register_autotrade_ts(_sym_key)
        # Se o trade ocorreu na janela de abertura (09:00–09:30 BRT), registra proteção especial
        if _is_abertura_window():
            _register_abertura_trade(_sym_key)
            logger.info("Cooldown pós-abertura ativado para %s: trade executado na janela 09:00-09:30 BRT", _sym_key)
        # CRÍTICO: reseta o contador de confirmação de fechamento para o símbolo.
        # Sem isso, se _no_position_count estiver em 2 no momento do execute e o
        # fetchManage disparar logo após (antes de o MT5 registrar a nova posição),
        # a contagem chega a 3 e o trade recém-aberto é falsamente fechado no DB.
        _no_position_count[tv_symbol] = 0
        logger.info("_no_position_count[%s] resetado após execute bem-sucedido.", tv_symbol)

    # Notifica Telegram se trade executado
    if result and not error:
        try:
            from services.telegram_notifier import notify_trade_executed
            notify_trade_executed(
                tv_symbol    = tv_symbol,
                acao         = acao,
                score        = score,
                entry_price  = result.get("price") or entrada,
                sl           = sl,
                tp1          = tp1,
                order_id     = result.get("order"),
                ai_veredito  = ai_result.get("veredito")  if ai_result else None,
                ai_confianca = ai_result.get("confianca") if ai_result else None,
            )
        except Exception:
            pass

    return jsonify({
        "ok":          error is None,
        "result":      result,
        "error":       error,
        "ai":          ai_result,
        "trade_log_id": trade_log_id,
    })


@app.route("/api/monitor/tick")
def api_monitor_tick():
    """
    Retorna o preço atual (tick) de um símbolo via MT5 — endpoint leve para
    atualização em tempo real do último candle no gráfico (polling a cada 3s).

    GET /api/monitor/tick?tv_symbol=BMFBOVESPA:WIN1!
    """
    from services.market_service import TV_TO_MT5, MT5_AVAILABLE

    tv_symbol  = request.args.get("tv_symbol", "BMFBOVESPA:WIN1!")
    symbol_key = tv_symbol.upper().strip()

    if not MT5_AVAILABLE:
        return jsonify({"ok": False, "error": "MetaTrader5 não instalado."})

    mt5_symbol = TV_TO_MT5.get(symbol_key)
    if not mt5_symbol:
        return jsonify({"ok": False, "error": f"Símbolo '{tv_symbol}' não mapeado."})

    try:
        import MetaTrader5 as mt5

        # Usa credenciais do .env se configuradas
        _login    = int(os.getenv("MT5_LOGIN", "0") or 0)
        _password = os.getenv("MT5_PASSWORD", "")
        _server   = os.getenv("MT5_SERVER", "")
        _path     = os.getenv("MT5_PATH", "")

        kwargs = {}
        if _path and os.path.exists(_path):
            kwargs["path"] = _path
        _mq_servers = {"metaquotes-demo", "metaquotes-demo2"}
        if _login and _password and _server and _server.lower() not in _mq_servers:
            kwargs.update({"login": _login, "password": _password, "server": _server})

        if not mt5.initialize(**kwargs):
            return jsonify({"ok": False, "error": f"MT5: {mt5.last_error()}"})

        tick = mt5.symbol_info_tick(mt5_symbol)
        mt5.shutdown()

        if tick is None:
            return jsonify({"ok": False, "error": "Tick não disponível."})

        # Para futuros B3 em conta demo, `last` pode ser 0 — usa mid bid/ask
        price = tick.last if tick.last and tick.last > 0 else (tick.bid + tick.ask) / 2

        return jsonify({
            "ok":     True,
            "price":  round(price, 2),
            "bid":    round(tick.bid,  2),
            "ask":    round(tick.ask,  2),
            "time":   tick.time,
            "volume": tick.volume,
        })

    except Exception as exc:
        try:
            import MetaTrader5 as mt5; mt5.shutdown()
        except Exception:
            pass
        return jsonify({"ok": False, "error": str(exc)})


@app.route("/api/autotrade/position-pnl")
def api_position_pnl():
    """
    Endpoint leve para atualização em tempo real do P&L no modo Manager.
    Retorna o profit atual direto do MT5 (já calculado pelo broker na moeda da conta).
    GET /api/autotrade/position-pnl?tv_symbol=FX:XAUUSD
    """
    from services.trade_executor import get_open_positions
    tv_symbol = request.args.get("tv_symbol", "")
    positions, err = get_open_positions(tv_symbol or None)
    if err:
        return jsonify({"ok": False, "error": err})
    if not positions:
        return jsonify({"ok": True, "found": False})
    pos = positions[0]
    profit       = pos.get("profit", 0)
    current_price = pos.get("price_current", 0)
    volume        = pos.get("volume", 1)
    entry_price   = pos.get("price_open", 0)
    pos_type      = pos.get("type", 0)  # 0=BUY, 1=SELL
    return jsonify({
        "ok":           True,
        "found":        True,
        "profit":       round(profit, 2),
        "current_price": round(current_price, 2),
        "entry_price":  round(entry_price, 2),
        "volume":       volume,
        "type":         pos_type,
    })


@app.route("/api/autotrade/manage")
def api_autotrade_manage():
    """
    Retorna o modo atual: SCAN (sem posicao) ou MANAGE (com posicao aberta).
    Detecta fechamento de posicao e registra resultado no log de trades.

    GET /api/autotrade/manage?tv_symbol=BMFBOVESPA:WIN1!&interval=15
    """
    from services.trade_executor import get_open_positions
    from services.market_service import get_candles
    from services.technical_analysis import generate_signal
    from services.position_manager import analyze_position
    from services.trade_log import get_open_auto_trade, close_auto_trade

    tv_symbol    = request.args.get("tv_symbol", "BMFBOVESPA:WIN1!")
    interval     = request.args.get("interval", "15")
    interval_min = int(interval) if str(interval).isdigit() else 15

    # Verifica posicoes abertas pelo bot
    positions, err = get_open_positions(tv_symbol)
    if err:
        # MT5 com erro de conexao — verifica DB antes de retornar falha.
        # Se ha trade aberto no DB, retorna MANAGE (confiar no DB > erro MT5).
        # Isso evita que instabilidade de conexao MT5 prenda a UI em SCAN.
        logger.warning("Manage: get_open_positions erro: %s", err)
        _open_log_fallback = get_open_auto_trade(tv_symbol)
        if _open_log_fallback:
            logger.warning("Manage: MT5 erro mas ha trade aberto no DB (id=%d) — retornando MANAGE.",
                           _open_log_fallback["id"])
            return jsonify({"ok": True, "mode": "MANAGE",
                            "position": None, "analysis": None,
                            "trade_log": _open_log_fallback, "signal": None})
        return jsonify({"ok": False, "error": err})

    def _detect_close_reason(exit_price, open_log):
        """
        Detecta motivo de fechamento comparando exit com SL/TP e direção do trade.
        - STOP     : exit dentro da tolerância do SL, ou preço foi contra a posição
        - TP1      : exit dentro da tolerância do TP1
        - FECHADO_MT5_GAIN  : fechado pelo MT5 com lucro (sem bater TP1)
        - FECHADO_MT5_STOP  : fechado pelo MT5 com perda (sem bater SL exato)
        - FECHADO_MT5       : não foi possível determinar
        """
        if not exit_price or not open_log:
            return "FECHADO_MT5"

        sl    = open_log.get("sl_initial")
        tp1   = open_log.get("tp1_initial")
        entry = open_log.get("entry_price")
        acao  = open_log.get("acao", "")
        # Tolerância por símbolo: WIN=50pts, WDO=5pts, outros=0.5% da entrada
        sym_up = (open_log.get("tv_symbol") or tv_symbol or "").upper()
        if "WIN" in sym_up:
            tol = 50
        elif "WDO" in sym_up:
            tol = 5
        else:
            tol = float(entry) * 0.005 if entry else 0.5

        if sl  and abs(exit_price - float(sl))  <= tol:
            return "STOP"
        if tp1 and abs(exit_price - float(tp1)) <= tol:
            return "TP1"

        # Sem bater exatamente SL ou TP — infere pela direção
        if entry:
            if acao == "COMPRA":
                lucro = float(exit_price) > float(entry)
            elif acao == "VENDA":
                lucro = float(exit_price) < float(entry)
            else:
                lucro = None
            if lucro is True:
                return "FECHADO_MT5_GAIN"
            elif lucro is False:
                return "FECHADO_MT5_STOP"

        return "FECHADO_MT5"

    def _calc_pnl(exit_price, open_log):
        """
        P&L por símbolo:
          WIN  → R$0,20 por ponto por contrato
          WDO  → R$10,00 por ponto por contrato
          Outros (ações) → diferença de preço × volume em BRL
        """
        pnl_pts, pnl_brl = None, None
        if exit_price and open_log.get("entry_price"):
            entry  = float(open_log["entry_price"])
            acao   = open_log.get("acao", "COMPRA")
            vol    = float(open_log.get("volume") or 1.0)
            diff   = (exit_price - entry) if acao == "COMPRA" else (entry - exit_price)
            sym_up = (open_log.get("tv_symbol") or tv_symbol or "").upper()
            if "WIN" in sym_up:
                pnl_pts = round(diff)
                pnl_brl = round(pnl_pts * 0.20 * vol, 2)
            elif "WDO" in sym_up:
                pnl_pts = round(diff)
                pnl_brl = round(pnl_pts * 10.0 * vol, 2)
            else:
                # Ações: BRL direto (diferença de preço × volume)
                pnl_pts = None
                pnl_brl = round(diff * vol, 2)
        return pnl_pts, pnl_brl

    def _get_exit_price(tv_symbol):
        """Busca preço atual via MT5 para usar como exit price."""
        try:
            from services.market_service import TV_TO_MT5, MT5_AVAILABLE
            if MT5_AVAILABLE:
                mt5_sym = TV_TO_MT5.get(tv_symbol.upper().strip())
                if mt5_sym:
                    import MetaTrader5 as mt5
                    if mt5.initialize():
                        tick = mt5.symbol_info_tick(mt5_sym)
                        price = (tick.last or (tick.bid + tick.ask) / 2) if tick else None
                        mt5.shutdown()
                        return price
        except Exception as ex:
            logger.warning("Erro ao buscar exit price: %s", ex)
        return None

    if not positions:
        # ── Confirmação de fechamento ────────────────────────────────────────
        # MT5 às vezes retorna vazio por instabilidade de rede/consulta.
        # Só registra fechamento após N leituras consecutivas sem posição.
        _no_position_count[tv_symbol] = _no_position_count.get(tv_symbol, 0) + 1
        if _no_position_count[tv_symbol] < _NO_POSITION_CONFIRM:
            logger.info("Manage: sem posicao (leitura %d/%d) — aguardando confirmacao.",
                        _no_position_count[tv_symbol], _NO_POSITION_CONFIRM)
            open_log = get_open_auto_trade(tv_symbol)
            if open_log:
                # Há trade aberto no DB — MT5 pode estar com lag. Mantém MANAGE.
                return jsonify({"ok": True, "mode": "MANAGE",
                                "position": None, "analysis": None,
                                "trade_log": open_log, "signal": None})
            # Sem trade no DB: retorna SCAN normalmente.
            # O JS tem um período de graça pós-execute que ignora este SCAN
            # se o execute ocorreu nos últimos 30s (evita flip race-condition).
            return jsonify({"ok": True, "mode": "SCAN",
                            "position": None, "analysis": None, "signal": None,
                            "closed_trade": None})

        # N leituras confirmam ausência de posição — registra fechamento
        _no_position_count[tv_symbol] = 0

        from services.trade_log import save_auto_trade
        open_log = get_open_auto_trade(tv_symbol)
        closed_trade = None
        if open_log:
            try:
                exit_price   = _get_exit_price(tv_symbol)
                close_reason = _detect_close_reason(exit_price, open_log)
                pnl_pts, pnl_brl = _calc_pnl(exit_price, open_log)

                close_auto_trade(
                    trade_id     = open_log["id"],
                    exit_price   = exit_price or 0,
                    close_reason = close_reason,
                    pnl_pts      = pnl_pts,
                    pnl_brl      = pnl_brl,
                )
                closed_trade = {
                    "id":           open_log["id"],
                    "close_reason": close_reason,
                    "exit_price":   exit_price,
                    "pnl_pts":      pnl_pts,
                    "pnl_brl":      pnl_brl,
                }
                logger.info("Trade fechado: id=%d motivo=%s pnl=%s", open_log["id"], close_reason, pnl_pts)
                # Notifica Telegram
                try:
                    from services.telegram_notifier import notify_trade_closed
                    notify_trade_closed(
                        tv_symbol    = tv_symbol,
                        acao         = open_log.get("acao", "—"),
                        entry_price  = open_log.get("entry_price"),
                        exit_price   = exit_price,
                        close_reason = close_reason,
                        pnl_pts      = pnl_pts,
                        pnl_brl      = pnl_brl,
                    )
                except Exception:
                    pass
                # Verifica se meta diária foi atingida
                try:
                    from services.telegram_commander import check_meta_atingida
                    from services.config import Config
                    check_meta_atingida(Config.TELEGRAM_BOT_TOKEN, Config.TELEGRAM_CHAT_ID)
                except Exception:
                    pass
            except Exception as log_exc:
                logger.warning("Erro ao registrar fechamento no log: %s", log_exc)

        return jsonify({
            "ok": True, "mode": "SCAN",
            "position": None, "analysis": None, "signal": None,
            "closed_trade": closed_trade,
        })

    # Posição existe — reseta o contador de confirmação de fechamento
    _no_position_count[tv_symbol] = 0

    position = positions[0]

    # Busca dados de mercado para gerar sinal tecnico atual
    current_signal = None
    atr = None
    try:
        df, data_err = get_candles(tv_symbol, tv_interval=interval)
        if df is not None and not df.empty:
            current_signal = generate_signal(df)
            if current_signal:
                atr = current_signal.get("atr")
    except Exception as _sig_exc:
        logger.warning("Manage: erro ao buscar/gerar sinal para %s: %s", tv_symbol, _sig_exc)

    # Analisa a posicao
    analysis = None
    try:
        analysis = analyze_position(
            position=position,
            current_signal=current_signal,
            atr=atr,
            interval_min=interval_min,
        )
    except Exception as _ana_exc:
        logger.warning("Manage: erro ao analisar posição %s: %s", tv_symbol, _ana_exc)

    # Recupera o log do trade aberto para contexto
    from services.trade_log import save_auto_trade
    open_log = get_open_auto_trade(tv_symbol)

    # ── Recovery: posição aberta no MT5 mas sem registro no DB ────────────
    # Ocorre quando o app foi reiniciado ou o trade foi aberto manualmente/externamente.
    # IMPORTANTE: antes de criar um novo registro, verifica se o último trade fechado
    # tem o mesmo ticket MT5 — se sim, foi um falso fechamento e reabrimos o registro.
    if open_log is None:
        try:
            mt5_ticket_atual = int(position.get("ticket") or 0)
            is_buy   = position.get("type") == 0   # 0=BUY, 1=SELL
            acao_rec = "COMPRA" if is_buy else "VENDA"
            entry_rec  = float(position.get("price_open") or 0) or None
            sl_rec     = float(position.get("sl") or 0) or None
            tp_rec     = float(position.get("tp") or 0) or None
            vol_rec    = float(position.get("volume") or 1.0)

            # Verifica se o último trade fechado tem o mesmo ticket (falso fechamento)
            reaberto = False
            if mt5_ticket_atual:
                from services.db import _conn as _db_conn
                with _db_conn() as _conn_rec:
                    last_closed = _conn_rec.execute("""
                        SELECT id, mt5_ticket FROM auto_trades
                        WHERE tv_symbol=? AND closed_at IS NOT NULL
                        ORDER BY id DESC LIMIT 1
                    """, (tv_symbol,)).fetchone()
                if last_closed and int(last_closed["mt5_ticket"] or 0) == mt5_ticket_atual:
                    # Mesmo ticket — desfaz o fechamento falso
                    with _db_conn() as _conn_rec:
                        _conn_rec.execute("""
                            UPDATE auto_trades
                            SET closed_at=NULL, exit_price=NULL,
                                close_reason=NULL, pnl_pts=NULL, pnl_brl=NULL
                            WHERE id=?
                        """, (last_closed["id"],))
                    open_log = get_open_auto_trade(tv_symbol)
                    reaberto = True
                    logger.warning(
                        "Trade id=%d reaberto: falso fechamento detectado (ticket=%d ainda ativo no MT5).",
                        last_closed["id"], mt5_ticket_atual,
                    )

            if not reaberto:
                rec_id = save_auto_trade(
                    tv_symbol   = tv_symbol,
                    interval    = interval,
                    acao        = acao_rec,
                    entry_price = entry_rec,
                    volume      = vol_rec,
                    sl_initial  = sl_rec,
                    tp1_initial = tp_rec,
                    score       = None,
                    mt5_ticket  = mt5_ticket_atual or None,
                    ai_validated= False,
                    ai_veredito = "RECUPERADO",
                    ai_motivo   = "Trade detectado no MT5 sem registro — recuperado automaticamente.",
                )
                open_log = get_open_auto_trade(tv_symbol)
                logger.info("Trade recuperado do MT5: id=%d %s entry=%.0f", rec_id, acao_rec, entry_rec or 0)
        except Exception as rec_exc:
            logger.warning("Erro ao recuperar trade do MT5: %s", rec_exc)

    return jsonify({
        "ok":         True,
        "mode":       "MANAGE",
        "position":   position,
        "analysis":   analysis,
        "signal":     current_signal,
        "trade_log":  open_log,
    })


@app.route("/api/autotrade/fix-corrupt-pnl", methods=["POST"])
def api_fix_corrupt_pnl():
    """
    Corrige registros com P&L obviamente errado (resultado de bug do fallback multi-símbolo).
    Zera pnl_pts e pnl_brl de trades onde pnl_pts > 10000 (impossível em day trade real).
    """
    from services.db import _conn as _db_conn
    try:
        with _db_conn() as conn:
            rows = conn.execute(
                "SELECT id, tv_symbol, entry_price, exit_price, pnl_pts, pnl_brl "
                "FROM auto_trades WHERE pnl_pts > 10000 OR pnl_pts < -10000"
            ).fetchall()
            ids_corrigidos = []
            for row in rows:
                conn.execute(
                    "UPDATE auto_trades SET pnl_pts=NULL, pnl_brl=NULL, close_reason='DADOS_CORROMPIDOS' WHERE id=?",
                    (row[0],)
                )
                ids_corrigidos.append({
                    "id": row[0], "tv_symbol": row[1],
                    "entry_price": row[2], "exit_price": row[3],
                    "pnl_pts_anterior": row[4], "pnl_brl_anterior": row[5],
                })
                logger.info("Registro corrompido zerado: id=%d tv=%s pnl_pts_era=%s", row[0], row[1], row[4])
        return jsonify({"ok": True, "corrigidos": ids_corrigidos, "total": len(ids_corrigidos)})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)})


@app.route("/api/autotrade/fix-wdo-pnl", methods=["POST"])
def api_fix_wdo_pnl():
    """
    Recalcula pnl_brl dos trades WDO fechados usando a fórmula correta (R$10/ponto).
    A fórmula antiga usava R$0,20/ponto (fórmula do WIN), gerando valores 50x menores.
    Também recalcula trades com pnl_brl absurdo (> 10.000) zerando-os como corrompidos.
    """
    from services.db import _conn as _db_conn
    try:
        with _db_conn() as conn:
            rows = conn.execute("""
                SELECT id, tv_symbol, acao, entry_price, exit_price, pnl_pts, pnl_brl, volume
                FROM auto_trades
                WHERE (tv_symbol LIKE '%WDO%' OR tv_symbol LIKE '%wdo%')
                  AND pnl_pts IS NOT NULL
                  AND closed_at IS NOT NULL
                  AND close_reason NOT IN ('BLOQUEADO_IA','AGUARDADO_IA','DADOS_CORROMPIDOS')
            """).fetchall()

            corrigidos = []
            for row in rows:
                rid, sym, acao, entry, exit_p, pts, brl, vol = (
                    row[0], row[1], row[2], row[3], row[4], row[5], row[6], row[7]
                )
                pts_f  = float(pts  or 0)
                vol_f  = float(vol  or 1.0)

                # Ignorar registros corrompidos (pts absurdo — tratados pelo fix-corrupt-pnl)
                if abs(pts_f) > 10000:
                    continue

                # Recalcular com fórmula correta: R$10/ponto × volume
                novo_brl = round(pts_f * 10.0 * vol_f, 2)
                brl_atual = float(brl or 0)

                if abs(novo_brl - brl_atual) < 0.01:
                    continue  # já está correto

                conn.execute(
                    "UPDATE auto_trades SET pnl_brl=? WHERE id=?",
                    (novo_brl, rid)
                )
                corrigidos.append({
                    "id": rid, "tv_symbol": sym,
                    "pnl_pts": pts_f,
                    "pnl_brl_antigo": brl_atual,
                    "pnl_brl_novo": novo_brl,
                })
                logger.info(
                    "WDO P&L recalculado: id=%d pnl_pts=%.0f brl_antigo=%.2f brl_novo=%.2f",
                    rid, pts_f, brl_atual, novo_brl,
                )

        return jsonify({"ok": True, "corrigidos": corrigidos, "total": len(corrigidos)})
    except Exception as exc:
        logger.exception("Erro ao corrigir P&L WDO")
        return jsonify({"ok": False, "error": str(exc)})

@app.route("/api/autotrade/meta-config", methods=["GET", "POST"])
def api_autotrade_meta_config():
    """
    Configura e consulta a meta diária de P&L.

    GET  → retorna meta, P&L do dia, se foi atingida, se auto-trade está ativo
    POST → {"meta": 300.0}  — define meta em R$ (0 ou null = desativa)
    """
    from services.telegram_commander import (
        get_meta_diaria, set_meta_diaria,
        is_autotrade_enabled, set_autotrade_enabled,
    )
    from services.trade_log import auto_trades_stats

    if request.method == "POST":
        body  = request.get_json(silent=True) or {}
        valor = body.get("meta")
        if valor is None or float(valor) <= 0:
            set_meta_diaria(None)
        else:
            set_meta_diaria(float(valor))
        # Se quiserem reativar o auto-trade junto (reset após pausa por meta)
        if body.get("reativar"):
            set_autotrade_enabled(True)

    try:
        stats   = auto_trades_stats(today_only=True)
        pnl_brl = float(stats.get("pnl_total_brl") or 0.0)
        total   = int(stats.get("total") or 0)
    except Exception:
        pnl_brl = 0.0
        total   = 0

    meta     = get_meta_diaria()
    atingida = meta is not None and pnl_brl >= meta

    return jsonify({
        "ok":              True,
        "meta":            meta,
        "pnl_brl_hoje":    round(pnl_brl, 2),
        "trades_hoje":     total,
        "meta_atingida":   atingida,
        "autotrade_ativo": is_autotrade_enabled(),
    })


@app.route("/api/autotrade/partial-config", methods=["GET", "POST"])
def api_autotrade_partial_config():
    """
    GET  → retorna estado atual do modo 3-contratos (enabled, tp1_rr, tp2_rr)
    POST → {"enabled": true|false}  — ativa ou desativa o modo partial-close
    """
    from services import partial_close_manager as pcm

    # tv_symbol: vem do body (POST) ou query string (GET)
    tv_symbol_cfg = None
    if request.method == "POST":
        body          = request.get_json(silent=True) or {}
        enabled       = bool(body.get("enabled", False))
        tv_symbol_cfg = body.get("tv_symbol") or None
        pcm.set_enabled(enabled, tv_symbol_cfg)
        logger.info("Partial-close 3-contratos via dashboard [%s]: %s",
                    tv_symbol_cfg or "ALL", "ON" if enabled else "OFF")
    else:
        tv_symbol_cfg = request.args.get("tv_symbol") or None

    return jsonify({
        "ok":             True,
        "enabled":        pcm.is_enabled(tv_symbol_cfg),
        "tv_symbol":      tv_symbol_cfg,
        "tp1_rr":         pcm.TP1_RR,
        "tp2_rr":         pcm.TP2_RR,
        "entry_volume":   pcm.ENTRY_VOLUME,
        "partial_volume": pcm.PARTIAL_CLOSE_VOLUME,
        "state":          {k: pcm.get_state(k) for k in list(pcm._state.keys())},
    })


@app.route("/api/autotrade/close", methods=["POST"])
def api_autotrade_close():
    """Fecha todas as posições abertas pelo bot para o símbolo dado."""
    from services.trade_executor import close_all_positions
    body = request.get_json(silent=True) or {}
    tv_symbol = body.get("tv_symbol", "BMFBOVESPA:WIN1!")
    results, error = close_all_positions(tv_symbol)
    return jsonify({"ok": error is None, "results": results, "error": error})




@app.route("/api/trade/manual", methods=["POST"])
def api_trade_manual():
    """
    Executa uma ordem MANUAL de compra ou venda via MT5.
    Diferente do auto-trade: bypassa verificacao de score minimo.
    O usuario define entrada, SL e TP manualmente.

    Body JSON:
      tv_symbol, acao, sl, tp1, volume, check_hours (bool)
    """
    from services.trade_executor import execute_trade, close_all_positions, DEFAULT_VOLUME, SCORE_MIN
    from services.trade_log import save_auto_trade

    body      = request.get_json(silent=True) or {}
    tv_symbol = body.get("tv_symbol", "BMFBOVESPA:WIN1!")
    acao      = body.get("acao", "")
    sl        = body.get("sl") or body.get("stop")
    tp1       = body.get("tp1")
    volume    = float(body.get("volume") or DEFAULT_VOLUME)
    check_hrs = bool(body.get("check_hours", True))

    if acao == "FECHAR":
        results, error = close_all_positions(tv_symbol)
        return jsonify({"ok": error is None, "results": results, "error": error})

    # Usa score=SCORE_MIN para bypasarr a verificacao de score minimo
    result, error = execute_trade(
        tv_symbol=tv_symbol,
        acao=acao,
        score=SCORE_MIN,        # bypass: ordem manual sempre passa
        entrada=None,           # a mercado
        sl=sl,
        tp1=tp1,
        volume=volume,
        check_market_hours=check_hrs,
    )

    trade_log_id = None
    if result and not error:
        try:
            trade_log_id = save_auto_trade(
                tv_symbol   = tv_symbol,
                interval    = "manual",
                acao        = acao,
                entry_price = result.get("price"),
                volume      = volume,
                sl_initial  = sl,
                tp1_initial = tp1,
                score       = 0,
                mt5_ticket  = result.get("order"),
                mt5_deal    = result.get("deal"),
                ai_validated= False,
            )
        except Exception as e:
            logger.warning("Falha ao salvar trade manual no log: %s", e)

    return jsonify({
        "ok":           error is None,
        "result":       result,
        "error":        error,
        "trade_log_id": trade_log_id,
    })

# ---------------------------------------------------------------------------
# Auto-Trades — Historico e gestao
# ---------------------------------------------------------------------------

@app.route("/api/autotrade/trades")
def api_autotrade_trades():
    """Lista historico de trades automaticos."""
    from services.trade_log import list_auto_trades, auto_trades_stats
    limit      = min(int(request.args.get("limit", 50)), 200)
    show_all   = request.args.get("all", "0") == "1"   # ?all=1 mostra todos os dias
    today_only = not show_all
    return jsonify({
        "ok":         True,
        "items":      list_auto_trades(limit, today_only=today_only),
        "stats":      auto_trades_stats(today_only=today_only),
        "today_only": today_only,
    })


@app.route("/api/autotrade/report")
def api_autotrade_report():
    """
    Relatorio de trades por periodo.
    ?period=day|week|month  (default: day)
    ?ref=YYYY-MM-DD         (data de referencia, default: hoje)
    """
    from services.db import _conn as _db_conn
    from datetime import datetime, timedelta, date as date_cls
    import json as _json

    period = request.args.get("period", "day")
    ref_str = request.args.get("ref", "")
    try:
        ref = datetime.strptime(ref_str, "%Y-%m-%d").date() if ref_str else date_cls.today()
    except ValueError:
        ref = date_cls.today()

    # Define intervalo de datas
    if period == "day":
        date_from = ref
        date_to   = ref
    elif period == "week":
        date_from = ref - timedelta(days=ref.weekday())   # segunda-feira
        date_to   = date_from + timedelta(days=6)
    else:  # month
        date_from = ref.replace(day=1)
        # ultimo dia do mes
        if date_from.month == 12:
            date_to = date_from.replace(year=date_from.year+1, month=1, day=1) - timedelta(days=1)
        else:
            date_to = date_from.replace(month=date_from.month+1, day=1) - timedelta(days=1)

    with _db_conn() as conn:
        rows = conn.execute("""
            SELECT * FROM auto_trades
            WHERE date(opened_at, 'localtime') BETWEEN ? AND ?
            ORDER BY opened_at ASC
        """, (date_from.isoformat(), date_to.isoformat())).fetchall()

    trades = [dict(r) for r in rows]
    # Exclui BLOQUEADO_IA e AGUARDADO_IA — nenhum desses foi executado de verdade
    _excluidos = {"BLOQUEADO_IA", "AGUARDADO_IA"}
    reais  = [t for t in trades if t.get("close_reason") not in _excluidos]
    fechados = [t for t in reais if t.get("closed_at")]
    abertos  = [t for t in reais if not t.get("closed_at")]

    gains  = [t for t in fechados if (t.get("pnl_pts") or 0) > 0]
    losses = [t for t in fechados if (t.get("pnl_pts") or 0) < 0]
    pnl_total_pts = sum(t.get("pnl_pts") or 0 for t in fechados)
    pnl_total_brl = sum(t.get("pnl_brl") or 0 for t in fechados)
    avg_gain_pts  = (sum(t["pnl_pts"] for t in gains)  / len(gains))  if gains  else 0
    avg_loss_pts  = (sum(t["pnl_pts"] for t in losses) / len(losses)) if losses else 0
    win_rate      = round(len(gains) / len(fechados) * 100) if fechados else 0
    expectativa   = round(pnl_total_pts / len(fechados), 1) if fechados else 0

    # Breakdown diario
    from collections import defaultdict
    daily = defaultdict(lambda: {"trades": 0, "wins": 0, "losses": 0,
                                 "pnl_pts": 0.0, "pnl_brl": 0.0, "abertos": 0})
    for t in reais:
        day_key = (t.get("opened_at") or "")[:10]
        d = daily[day_key]
        if not t.get("closed_at"):
            d["abertos"] += 1
            d["trades"]  += 1
            continue
        d["trades"] += 1
        pts = t.get("pnl_pts") or 0
        brl = t.get("pnl_brl") or 0
        d["pnl_pts"] += pts
        d["pnl_brl"] += brl
        if pts > 0: d["wins"]   += 1
        elif pts < 0: d["losses"] += 1

    daily_list = sorted([{"date": k, **v} for k, v in daily.items()], key=lambda x: x["date"])

    # Melhor e pior dia
    dias_com_pnl = [d for d in daily_list if d["trades"] - d["abertos"] > 0]
    best_day  = max(dias_com_pnl, key=lambda x: x["pnl_pts"], default=None)
    worst_day = min(dias_com_pnl, key=lambda x: x["pnl_pts"], default=None)

    # Score medio
    scores = [abs(t.get("score") or 0) for t in fechados if t.get("score")]
    avg_score = round(sum(scores) / len(scores), 1) if scores else 0

    # Por motivo de fechamento
    by_reason = defaultdict(int)
    for t in fechados:
        by_reason[t.get("close_reason") or "outro"] += 1

    return jsonify({
        "ok":           True,
        "period":       period,
        "date_from":    date_from.isoformat(),
        "date_to":      date_to.isoformat(),
        "summary": {
            "total":        len(reais),
            "fechados":     len(fechados),
            "abertos":      len(abertos),
            "wins":         len(gains),
            "losses":       len(losses),
            "win_rate":     win_rate,
            "pnl_total_pts": round(pnl_total_pts),
            "pnl_total_brl": round(pnl_total_brl, 2),
            "avg_gain_pts":  round(avg_gain_pts),
            "avg_loss_pts":  round(avg_loss_pts),
            "expectativa":   expectativa,
            "avg_score":     avg_score,
            "best_day":      best_day,
            "worst_day":     worst_day,
            "by_reason":     dict(by_reason),
        },
        "daily":  daily_list,
        "trades": trades,
    })


def api_autotrade_apply_recommendation():
    """
    Aplica automaticamente a recomendacao de gestao do MANAGE mode.
    Acoes suportadas: BREAKEVEN (move SL para entrada), TRAILING (move SL),
    FECHAR (fecha posicao antecipada), CLOSE (alias para FECHAR).
    """
    from services.trade_executor import (
        get_open_positions, close_all_positions,
        execute_trade,
    )
    from services.trade_log import get_open_auto_trade, close_auto_trade
    import MetaTrader5 as mt5

    body         = request.get_json(silent=True) or {}
    tv_symbol    = body.get("tv_symbol", "BMFBOVESPA:WIN1!")
    action       = (body.get("action") or "").upper()
    new_sl       = body.get("new_sl")           # preco do novo SL (BREAKEVEN / TRAILING)
    close_reason = body.get("close_reason")     # motivo do fechamento (TP1, REVERSAO, MANUAL…)

    if action in ("FECHAR", "CLOSE"):
        results, err = close_all_positions(tv_symbol)
        pnl_pts = None
        pnl_brl = None

        # Registra no log
        open_log = get_open_auto_trade(tv_symbol)
        if open_log and not err:
            try:
                from services.market_service import TV_TO_MT5, MT5_AVAILABLE
                exit_price = None
                if MT5_AVAILABLE:
                    mt5_sym = TV_TO_MT5.get(tv_symbol.upper().strip())
                    if mt5_sym and mt5.initialize():
                        tick = mt5.symbol_info_tick(mt5_sym)
                        if tick:
                            exit_price = tick.last or (tick.bid + tick.ask) / 2
                        mt5.shutdown()
                pnl_pts = None
                pnl_brl = None
                if exit_price and open_log.get("entry_price"):
                    e = float(open_log["entry_price"])
                    pnl_pts = round(exit_price - e) if open_log["acao"] == "COMPRA" else round(e - exit_price)
                    vol = float(open_log.get("volume") or 1.0)
                    pnl_brl = round(pnl_pts * 0.20 * vol, 2)
                close_auto_trade(
                    open_log["id"], exit_price or 0,
                    close_reason or "MANUAL", pnl_pts, pnl_brl,
                )
            except Exception as ex:
                logger.warning("Erro ao fechar log: %s", ex)

        return jsonify({"ok": not err, "results": results, "error": err,
                        "pnl_pts": pnl_pts, "pnl_brl": pnl_brl})

    if action in ("BREAKEVEN", "TRAILING") and new_sl is not None:
        # Move o stop loss via MT5 position modify
        from services.market_service import TV_TO_MT5, MT5_AVAILABLE
        from services.trade_executor import MAGIC_NUMBER

        if not MT5_AVAILABLE:
            return jsonify({"ok": False, "error": "MT5 nao disponivel."})

        mt5_sym = TV_TO_MT5.get(tv_symbol.upper().strip())
        if not mt5_sym:
            return jsonify({"ok": False, "error": f"Simbolo {tv_symbol} nao mapeado."})

        try:
            if not mt5.initialize():
                return jsonify({"ok": False, "error": f"MT5: {mt5.last_error()}"})

            positions = mt5.positions_get(symbol=mt5_sym)
            if not positions:
                mt5.shutdown()
                return jsonify({"ok": False, "error": "Nenhuma posicao aberta."})

            pos = next((p for p in positions if p.magic == MAGIC_NUMBER), None)
            if not pos:
                mt5.shutdown()
                return jsonify({"ok": False, "error": "Posicao do bot nao encontrada."})

            # Snap para tick_size
            sym_info = mt5.symbol_info(mt5_sym)
            tick_size = float(sym_info.trade_tick_size) if sym_info and sym_info.trade_tick_size else 1.0
            new_sl_snapped = round(round(float(new_sl) / tick_size) * tick_size, 10)

            req = {
                "action":   mt5.TRADE_ACTION_SLTP,
                "position": pos.ticket,
                "symbol":   mt5_sym,
                "sl":       new_sl_snapped,
                "tp":       pos.tp,
            }
            result = mt5.order_send(req)
            mt5.shutdown()

            if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                return jsonify({
                    "ok": True,
                    "action": action,
                    "new_sl": new_sl_snapped,
                    "ticket": pos.ticket,
                })
            retcode = result.retcode if result else None
            comment = result.comment if result else "None"
            return jsonify({
                "ok":    False,
                "error": f"MT5 retcode {retcode}: {comment}",
            })
        except Exception as exc:
            try:
                mt5.shutdown()
            except Exception:
                pass
            return jsonify({"ok": False, "error": str(exc)})

    return jsonify({"ok": False, "error": f"Acao desconhecida: {action}"})


# ---------------------------------------------------------------------------
# Monitor MT5 — Persistência e verificação de sinais
# ---------------------------------------------------------------------------

@app.route("/api/monitor/signal/save", methods=["POST"])
def api_save_mt5_signal():
    """Salva um sinal do Monitor MT5 no banco."""
    body = request.get_json(silent=True) or {}
    required = ("tv_symbol", "interval", "acao")
    if not all(body.get(k) for k in required):
        return jsonify({"error": "tv_symbol, interval e acao são obrigatórios."}), 400
    row_id, created_at = insert_mt5_signal(
        tv_symbol=body["tv_symbol"],
        interval=str(body["interval"]),
        acao=body["acao"],
        score=body.get("score"),
        preco=body.get("preco"),
        entrada=body.get("entrada"),
        stop=body.get("stop"),
        tp1=body.get("tp1"),
        tp2=body.get("tp2"),
        tp3=body.get("tp3"),
        vwap=body.get("vwap"),
    )
    return jsonify({"ok": True, "id": row_id, "created_at": created_at})


@app.route("/api/monitor/signals/history")
def api_mt5_signals_history():
    limit = min(int(request.args.get("limit", 100)), 200)
    return jsonify({"ok": True, "items": list_mt5_signals(limit)})

@app.route("/api/monitor/signals/stats")
def api_mt5_signals_stats():
    return jsonify(mt5_signals_stats())


@app.route("/api/monitor/signal/<int:signal_id>/verify", methods=["POST"])
def api_verify_mt5_signal(signal_id: int):
    """Verifica via MT5 se alvos/stop de um sinal foram atingidos."""
    from services.outcome_checker import check_outcome
    row = get_mt5_signal(signal_id)
    if not row:
        return jsonify({"error": "Sinal não encontrado."}), 404
    tv_sym = row["tv_symbol"]
    ativo  = tv_sym.split(":")[-1] if ":" in tv_sym else tv_sym
    outcome = check_outcome(
        ativo=ativo,
        acao=row["acao"],
        created_at_iso=row["created_at"],
        entrada=row.get("entrada"),
        stop=row.get("stop"),
        tp1=row.get("tp1"),
        tp2=row.get("tp2"),
        tp3=row.get("tp3"),
    )
    update_mt5_outcome(signal_id, **outcome)
    updated = get_mt5_signal(signal_id)
    return jsonify({"ok": True, "outcome": outcome, "signal": dict(updated)})
@app.route("/api/monitor/signals/verify-batch", methods=["POST"])
def api_verify_mt5_batch():
    """Verifica os N sinais mais recentes ainda nao verificados."""
    from services.outcome_checker import check_outcome
    body  = request.get_json(silent=True) or {}
    limit = min(int(body.get("limit", 30)), 100)
    rows  = list_mt5_signals(limit)
    results = []
    for r in rows:
        if r.get("outcome_checked_at"):
            results.append({"id": r["id"], "skipped": True})
            continue
        tv_sym = r["tv_symbol"]
        ativo  = tv_sym.split(":")[-1] if ":" in tv_sym else tv_sym
        outcome = check_outcome(
            ativo=ativo,
            acao=r["acao"],
            created_at_iso=r["created_at"],
            entrada=r.get("entrada"),
            stop=r.get("stop"),
            tp1=r.get("tp1"),
            tp2=r.get("tp2"),
            tp3=r.get("tp3"),
        )
        update_mt5_outcome(r["id"], **outcome)
        results.append({"id": r["id"], "outcome": outcome})
    return jsonify({"ok": True, "results": results})


init_db()
init_mt5_signals()
init_auto_trades()

# ── Inicia monitor de fechamento parcial (thread daemon bg a cada 2s) ─────────────
try:
    from services.partial_close_monitor import start_monitor as _start_pcm
    _start_pcm()
except Exception as _pcm_start_err:
    logger.warning("partial_close_monitor nao pode ser iniciado: %s", _pcm_start_err)

if __name__ == "__main__":
    app.run(debug=True, use_reloader=False, host="0.0.0.0", port=int(os.getenv("PORT", "5000")))
