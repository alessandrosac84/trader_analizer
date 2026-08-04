"""
saude_bp.py — tela SAÚDE DO PORTFÓLIO (/saude).

Acompanhamento da fase de paper trading: realizado × backtest por estratégia,
alertas de degradação e de silêncio anômalo. Cálculo em relatorio_saude.py
(mesma fonte do CLI). Aditivo — não altera nenhum módulo existente.
"""
from flask import Blueprint, jsonify, render_template, request

saude_bp = Blueprint("saude", __name__)


@saude_bp.route("/saude")
def saude_page():
    return render_template("saude.html")


@saude_bp.route("/api/saude")
def api_saude():
    try:
        dias = int(request.args.get("dias", 21))
        from relatorio_saude import health_data
        data = health_data(dias)
        # ── pulso dos motores (vivo/morto + último motivo por ativo) ──
        motores = {}
        try:
            from services.engine_heartbeat import load_all
            motores = load_all()
        except Exception:
            pass
        try:
            # V7 roda neste processo na instância B3 — snapshot direto
            from services.v7_engine_runtime import runtime as _v7
            snap = _v7.snapshot()
            motores["v7_win"] = {"idade_s": 0 if snap.get("last_eval_ts") else 99999,
                                 "motivos": {snap.get("symbol", "WIN"):
                                             (f"AUTO {'ON' if snap.get('enabled') else 'OFF'} · "
                                              f"{snap.get('status') or '—'} · últ. aval. "
                                              f"{snap.get('last_eval_ts') or '—'}")}}
        except Exception:
            pass
        return jsonify({"ok": True, "motores": motores, **data})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)})
