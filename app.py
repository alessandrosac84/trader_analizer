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
    init_db,
    insert_analysis,
    journal_stats,
    list_analyses,
    parse_pnl_value,
    update_execution,
)
from services.json_utils import extract_json_object, risk_summary, trader_ativo_hint, trader_ativo_label
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
# Monitor — Sinais de trading em tempo real (via Yahoo Finance + TA)
# ---------------------------------------------------------------------------

@app.route("/api/monitor/signals")
def api_monitor_signals():
    """
    GET /api/monitor/signals?tv_symbol=BMFBOVESPA:WIN1!&interval=15
    Busca candles via Yahoo Finance, calcula indicadores técnicos e retorna
    sinal de trading (COMPRA/VENDA/NEUTRO) com entrada, stop e alvos.
    """
    tv_symbol = request.args.get("tv_symbol", "BMFBOVESPA:WIN1!")
    interval  = request.args.get("interval", "15")   # minutos ou D/W (TradingView format)

    # Converte intervalo TradingView → Yahoo Finance (period, interval)
    period_yf, interval_yf = TV_INTERVAL_TO_YF.get(interval, ("5d", "15m"))

    # Converte símbolo TradingView → Yahoo Finance
    yf_symbol = tv_to_yf_symbol(tv_symbol)

    df, error = get_candles(yf_symbol, period=period_yf, interval=interval_yf)
    if error or df is None:
        return jsonify({
            "ok": False,
            "error": error or "Sem dados.",
            "tv_symbol": tv_symbol,
            "yf_symbol": yf_symbol,
        }), 200   # 200 para o frontend exibir o erro sem quebrar

    # Busca timeframe maior (1h) para confirmação multi-TF
    # Não busca quando o próprio TF já é ≥ 1h
    htf_df = None
    if interval_yf not in ("60m", "1h", "1d", "1wk"):
        htf_df, _ = get_candles(yf_symbol, period="1mo", interval="1h")

    signal = generate_signal(df, htf_df=htf_df)
    if signal is None:
        return jsonify({
            "ok": False,
            "error": f"Dados insuficientes para análise técnica ({len(df)} candles).",
            "tv_symbol": tv_symbol,
            "yf_symbol": yf_symbol,
        }), 200

    return jsonify({
        "ok": True,
        "tv_symbol": tv_symbol,
        "yf_symbol": yf_symbol,
        "interval": interval,
        "period": period_yf,
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
        {"label": "Vale (VALE3)",          "tv": "BMFBOVESPA:VALE3", "yf": "VALE3.SA",  "finnhub": "VALE"},
        {"label": "Itaú (ITUB4)",          "tv": "BMFBOVESPA:ITUB4", "yf": "ITUB4.SA",  "finnhub": "ITUB"},
        {"label": "Bradesco (BBDC4)",      "tv": "BMFBOVESPA:BBDC4", "yf": "BBDC4.SA",  "finnhub": None},
        {"label": "Banco do Brasil (BBAS3)","tv": "BMFBOVESPA:BBAS3","yf": "BBAS3.SA",  "finnhub": None},
        {"label": "Ambev (ABEV3)",         "tv": "BMFBOVESPA:ABEV3", "yf": "ABEV3.SA",  "finnhub": "ABEV"},
        {"label": "WEG (WEGE3)",           "tv": "BMFBOVESPA:WEGE3", "yf": "WEGE3.SA",  "finnhub": None},
    ]
    return jsonify({"items": instruments})


# ---------------------------------------------------------------------------

init_db()

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=int(os.getenv("PORT", "5000")))
