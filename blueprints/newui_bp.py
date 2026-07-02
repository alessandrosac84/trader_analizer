"""
newui_bp.py — Nova interface (STARK HUD) em PARALELO à atual.

⚠️ SOMENTE LEITURA / SOMENTE FRONTEND.
Este blueprint apenas SERVE um novo template (newdashboard.html). Ele NÃO
implementa nem altera nenhuma regra de negócio: o frontend novo consome os
MESMOS endpoints /api/* que a interface atual já usa. Nada nos módulos
(scalper, monitor MT5, risco, inteligência, execução) é tocado.

Objetivo: permitir testar o visual novo em /newdashboard sem risco, mantendo
o /dashboard atual 100% intacto. Quando o novo estiver validado, o antigo pode
ser removido.
"""
from flask import Blueprint, render_template

newui_bp = Blueprint("newui", __name__)


@newui_bp.route("/newdashboard")
def newdashboard():
    """Serve a nova UI (HUD). Todo o consumo de dados é via /api/* já existentes."""
    return render_template("newdashboard.html")
