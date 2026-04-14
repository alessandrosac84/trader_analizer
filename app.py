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
from services.json_utils import extract_json_object, risk_summary

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


init_db()

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=int(os.getenv("PORT", "5000")))
