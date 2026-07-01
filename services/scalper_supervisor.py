"""
scalper_supervisor.py — Agente-SUPERVISOR do Scalper (v6.1).

Por que NAO e um validador por-trade: o scalp decide em fracao de segundo; uma
chamada de LLM (1-15s) inviabilizaria a operacao. Entao este agente atua como
SUPERVISOR PERIODICO — roda a cada N minutos (agendado ou por endpoint), le o
desempenho recente e o regime atual, e recomenda uma ACAO de alto nivel:

    CONTINUAR  — desempenho ok, seguir operando
    ENDURECER  — subir score_min / reduzir frequencia (mercado ruim)
    PAUSAR     — parar o auto-scalper por um periodo (sangria / regime hostil)
    REVISAR    — padrao anomalo, pedir olhar humano

O agente NAO reconfigura o robo sozinho de forma destrutiva: ele recomenda,
registra e (opcional) alerta no Telegram. PAUSAR e a unica acao que ele pode
acionar diretamente, porque parar e sempre seguro.

Uso:
  python -c "from services.scalper_supervisor import run_supervision as r; print(r('BITN26'))"
  # ou agende:  python -m services.scalper_supervisor BITN26 --telegram
"""
import csv
import json
import logging
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

_CSV = Path(__file__).resolve().parent.parent / "logs" / "scalper_trades.csv"
_STATE = Path(__file__).resolve().parent.parent / "data" / "scalper_supervisor.json"

# Quantos trades recentes considerar na avaliacao
_WINDOW = 20


def _num(x, default=0.0):
    try:
        return float(x)
    except Exception:
        return default


def _recent_trades(symbol: str, limit: int = _WINDOW) -> list[dict]:
    if not _CSV.exists():
        return []
    try:
        rows = [r for r in csv.DictReader(open(_CSV, encoding="utf-8"))
                if (r.get("symbol") or "").upper() == symbol.upper()]
        return rows[-limit:]
    except Exception as exc:
        logger.warning("supervisor: falha lendo CSV: %s", exc)
        return []


def _compute_stats(trades: list[dict]) -> dict:
    n = len(trades)
    if n == 0:
        return {"n": 0}
    wins = sum(1 for r in trades if r.get("resultado") == "WIN")
    losses = sum(1 for r in trades if r.get("resultado") == "LOSS")
    be = sum(1 for r in trades if r.get("resultado") == "BE")
    pnl = round(sum(_num(r.get("profit")) for r in trades), 2)
    dec = wins + losses
    # perdas consecutivas (do fim para tras)
    consec = 0
    for r in reversed(trades):
        if r.get("resultado") == "LOSS":
            consec += 1
        elif r.get("resultado") == "WIN":
            break
    scores = [_num(r.get("score")) for r in trades if r.get("score")]
    exhaustion_hits = sum(1 for s in scores if s >= 90)
    dirs = [r.get("direcao") for r in trades]
    return {
        "n": n, "wins": wins, "losses": losses, "be": be,
        "win_rate": round(wins / dec * 100, 1) if dec else 0.0,
        "pnl": pnl, "consecutive_losses": consec,
        "avg_score": round(sum(scores) / len(scores), 1) if scores else None,
        "exhaustion_hits": exhaustion_hits,
        "buys": dirs.count("COMPRA"), "sells": dirs.count("VENDA"),
    }


def _heuristic(stats: dict) -> dict:
    """Recomendacao base por regras (funciona mesmo sem IA)."""
    if stats.get("n", 0) < 5:
        return {"acao": "CONTINUAR", "motivo": "Amostra pequena — sem base para intervir.",
                "confianca": 40}
    wr = stats["win_rate"]; pnl = stats["pnl"]; consec = stats["consecutive_losses"]
    if consec >= 4 or pnl <= -300:
        return {"acao": "PAUSAR", "motivo": f"Sangria: {consec} perdas seguidas / P&L {pnl}.",
                "confianca": 80}
    if wr < 30 or pnl < -120:
        return {"acao": "ENDURECER", "motivo": f"Win rate {wr}% / P&L {pnl} fracos — subir score_min.",
                "confianca": 65}
    if stats.get("exhaustion_hits", 0) >= 3:
        return {"acao": "REVISAR", "motivo": "Muitas entradas em zona de exaustao (score>=90).",
                "confianca": 55}
    return {"acao": "CONTINUAR", "motivo": f"Desempenho aceitavel (WR {wr}%, P&L {pnl}).",
            "confianca": 60}


_SYS = (
    "Voce e um supervisor quantitativo de um robo de SCALPING no futuro de Bitcoin da B3 "
    "(BITN26). Voce NAO valida trades individuais — avalia o DESEMPENHO RECENTE e o REGIME "
    "e recomenda UMA acao de alto nivel para proteger o capital. Seja conservador: na duvida, "
    "prefira ENDURECER ou PAUSAR. Responda SOMENTE JSON valido."
)


def _ask_ai(stats: dict) -> "dict | None":
    """Consulta o Azure para uma segunda opiniao. None se indisponivel."""
    try:
        from services.config import Config
        if not Config.use_azure_openai() or not Config.AZURE_OPENAI_API_KEY:
            return None
        import httpx
        from openai import AzureOpenAI
        client = AzureOpenAI(
            api_version=Config.AZURE_OPENAI_API_VERSION,
            azure_endpoint=Config.AZURE_OPENAI_ENDPOINT,
            api_key=Config.AZURE_OPENAI_API_KEY,
            http_client=httpx.Client(verify=False, trust_env=False,
                                     timeout=httpx.Timeout(connect=5.0, read=20.0, write=5.0, pool=5.0)),
        )
        prompt = (
            "Desempenho recente do scalper (ultimos trades):\n"
            f"{json.dumps(stats, ensure_ascii=False)}\n\n"
            "Recomende UMA acao entre: CONTINUAR | ENDURECER | PAUSAR | REVISAR.\n"
            "Responda JSON: {\"acao\":\"...\",\"motivo\":\"<=140 chars\",\"confianca\":0-100,"
            "\"score_min_sugerido\": <int ou null>}"
        )
        resp = client.chat.completions.create(
            model=Config.AZURE_OPENAI_DEPLOYMENT or Config.OPENAI_MODEL,
            messages=[{"role": "system", "content": _SYS}, {"role": "user", "content": prompt}],
            temperature=0.1, max_completion_tokens=200, timeout=20.0,
        )
        import re
        m = re.search(r"\{.*\}", resp.choices[0].message.content or "", re.DOTALL)
        return json.loads(m.group()) if m else None
    except Exception as exc:
        logger.warning("supervisor IA falhou: %s", exc)
        return None


def run_supervision(symbol: str = "BITN26", apply_pause: bool = False,
                    telegram: bool = False) -> dict:
    """
    Avalia o scalper e retorna recomendacao. Combina heuristica + IA (se disponivel).
    apply_pause=True: se a recomendacao for PAUSAR, aciona a pausa do auto-scalper.
    """
    trades = _recent_trades(symbol)
    stats = _compute_stats(trades)
    base = _heuristic(stats)
    ai = _ask_ai(stats) if stats.get("n", 0) >= 5 else None

    # Decisao final: a acao MAIS conservadora entre heuristica e IA vence.
    order = {"CONTINUAR": 0, "REVISAR": 1, "ENDURECER": 2, "PAUSAR": 3}
    final = base
    if ai and ai.get("acao") in order:
        if order.get(ai["acao"], 0) > order.get(base["acao"], 0):
            final = {"acao": ai["acao"], "motivo": ai.get("motivo", ""),
                     "confianca": ai.get("confianca", 60)}

    result = {
        "symbol": symbol,
        "timestamp": datetime.now(timezone(timedelta(hours=-3))).isoformat(),
        "stats": stats,
        "recomendacao": final["acao"],
        "motivo": final["motivo"],
        "confianca": final["confianca"],
        "ia_usada": ai is not None,
        "score_min_sugerido": (ai or {}).get("score_min_sugerido"),
    }

    # Persistir para o dashboard
    try:
        _STATE.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.debug("supervisor: falha salvando estado: %s", exc)

    # Acao segura: pausar
    if apply_pause and final["acao"] == "PAUSAR":
        try:
            from blueprints.scalper_bp import _auto
            import time as _t
            _auto["paused_until"] = _t.time() + 3600  # 1h de pausa
            logger.warning("SUPERVISOR pausou o auto-scalper por 1h: %s", final["motivo"])
            result["pausa_aplicada"] = True
        except Exception as exc:
            logger.warning("supervisor: falha ao aplicar pausa: %s", exc)

    if telegram and final["acao"] in ("PAUSAR", "ENDURECER", "REVISAR"):
        try:
            from services.telegram_notifier import _send as _tg
            _tg(f"🧭 <b>Supervisor Scalper</b> [{symbol}]\n"
                f"Recomendacao: <b>{final['acao']}</b>\n{final['motivo']}\n"
                f"WR {stats.get('win_rate')}% | P&L {stats.get('pnl')} | "
                f"{stats.get('consecutive_losses')} perdas seguidas")
        except Exception as exc:
            logger.debug("supervisor telegram: %s", exc)

    return result


# ── Scheduler in-process (daemon thread) ────────────────────────────────────
# Roda dentro do app (padrao do market_close_scheduler): a cada N minutos avalia
# o scalper e, se recomendar PAUSAR, aciona a pausa de verdade (mesmo processo).
_sched_running = False


def _supervisor_loop(symbol: str, interval_min: int):
    import time as _t
    logger.info("Scalper Supervisor ativo: avaliando %s a cada %dmin", symbol, interval_min)
    while True:
        try:
            # Só age quando o auto-scalper está ligado
            enabled = True
            try:
                from blueprints.scalper_bp import _auto
                enabled = bool(_auto.get("enabled", False))
            except Exception:
                pass
            if enabled:
                r = run_supervision(symbol, apply_pause=True, telegram=True)
                logger.info("Supervisor [%s]: %s — %s", symbol,
                            r.get("recomendacao"), r.get("motivo"))
        except Exception as exc:
            logger.error("supervisor loop error: %s", exc)
        _t.sleep(max(60, interval_min * 60))


def start_supervisor_scheduler(symbol: str = None, interval_min: int = 20):
    """Inicia a thread daemon do supervisor. Idempotente. Chamar no startup do app."""
    global _sched_running
    if _sched_running:
        return
    import threading
    symbol = symbol or os.getenv("SCALPER_SUPERVISOR_SYMBOL", "BITN26")
    _sched_running = True
    t = threading.Thread(target=_supervisor_loop, args=(symbol, interval_min),
                         daemon=True, name="scalper-supervisor")
    t.start()
    logger.info("scalper_supervisor: thread iniciada (%s, %dmin)", symbol, interval_min)


if __name__ == "__main__":
    import sys
    sym = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else "BITN26"
    r = run_supervision(sym, apply_pause=("--apply" in sys.argv), telegram=("--telegram" in sys.argv))
    print(json.dumps(r, ensure_ascii=False, indent=2))
