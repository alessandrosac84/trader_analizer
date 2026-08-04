"""
services/v7_ai_advisor.py — Consultor de IA do Monitor V7 (MODO CONSULTIVO).

⚠️ FILOSOFIA: o motor v7.4.1 foi validado em 6,6 anos SEM IA. Ligar a IA para
BLOQUEAR trades mudaria o sistema provado. Então, por padrão, a IA aqui é
CONSULTIVA: dá o veredito (EXECUTAR / AGUARDAR / BLOQUEAR) e o motivo, tudo é
REGISTRADO, mas NÃO impede a execução. Depois de semanas de dados, comparamos
os vereditos com o resultado real:
  - se os "BLOQUEAR" da IA deram prejuízo → promovemos a IA a filtro real (gate);
  - se não → provamos que ela não ajuda, sem ter arriscado o edge.

O modo é controlado pelo runtime (ai_mode = 'advisory' | 'gate' | 'off').
Este módulo só CALCULA o parecer — nunca decide sozinho.

Fail-safe: se o Azure não estiver disponível, retorna veredito 'N/D' (não
atrapalha a operação em modo consultivo).
"""
import json
import logging
import re
import time

logger = logging.getLogger(__name__)

_SYSTEM = (
    "Voce e um trader institucional especialista em mini indice (WIN) na B3, "
    "operando setups ESTRUTURAIS de abertura: fade de gap (volta ao fechamento "
    "de ontem) e rompimento do range da 1a hora (ORB). Sua funcao NAO e recalcular "
    "o setup — ele ja foi validado em 6,6 anos de backtest. Sua funcao e dar uma "
    "SEGUNDA OPINIAO de contexto: existe algo ANORMAL hoje que um trader humano "
    "experiente notaria e que aumentaria o risco desta entrada especifica? "
    "Ex.: noticia/dado macro iminente, volatilidade explosiva sem direcao, "
    "gap gigante por evento, mercado travado. Na duvida, EXECUTAR (o setup tem edge "
    "comprovado; so peca AGUARDAR/BLOQUEAR com razao concreta). "
    "Responda SEMPRE em JSON valido, sem texto extra."
)

_USER = """Avalie o contexto desta entrada do robo (setup ja validado — de sua 2a opiniao):

Ativo:        {symbol}
Setup:        {setup}
Acao:         {acao}
Regime do dia:{regime}   | Janela: {janela}   | Tendencia 1h: {htf}
Entrada:      {entrada}
Stop:         {stop}   (risco {risco} pts)
Alvo (TP1):   {tp1}
Score confluencia: {score}
Sinais do motor:
{sinais}

Responda SOMENTE com este JSON:
{{"veredito":"EXECUTAR","confianca":80,"motivo":"<=100 chars"}}
veredito: "EXECUTAR" (contexto normal) | "AGUARDAR" (incerteza concreta) | "BLOQUEAR" (risco anormal claro)"""


def advise(signal: dict, symbol: str, timeout: float = 10.0) -> dict:
    """Retorna {veredito, confianca, motivo, ia_usada}. Nunca lanca excecao."""
    fallback = {"veredito": "N/D", "confianca": None,
                "motivo": "IA indisponivel (consultivo)", "ia_usada": False}
    try:
        from services.config import Config
        if not Config.use_azure_openai() or not getattr(Config, "AZURE_OPENAI_API_KEY", ""):
            return fallback
        import httpx
        from openai import AzureOpenAI

        client = AzureOpenAI(
            api_version=Config.AZURE_OPENAI_API_VERSION,
            azure_endpoint=Config.AZURE_OPENAI_ENDPOINT,
            api_key=Config.AZURE_OPENAI_API_KEY,
            http_client=httpx.Client(verify=False, trust_env=False,
                                     timeout=httpx.Timeout(connect=5.0, read=timeout,
                                                           write=5.0, pool=5.0)),
        )
        prompt = _USER.format(
            symbol=symbol, setup=signal.get("setup", "?"),
            acao=signal.get("acao", "?"), regime=signal.get("regime", "?"),
            janela=signal.get("janela", "?"), htf=signal.get("htf_trend", "?"),
            entrada=signal.get("entrada"), stop=signal.get("stop"),
            risco=abs(float(signal.get("entrada", 0)) - float(signal.get("stop", 0) or 0)),
            tp1=signal.get("tp1"), score=signal.get("score"),
            sinais="\n".join(f"- {s}" for s in (signal.get("sinais") or [])[-6:]) or "-",
        )
        resp = client.chat.completions.create(
            model=Config.AZURE_OPENAI_DEPLOYMENT or Config.OPENAI_MODEL,
            messages=[{"role": "system", "content": _SYSTEM},
                      {"role": "user", "content": prompt}],
            temperature=0.0, max_completion_tokens=120, timeout=timeout,
        )
        content = (resp.choices[0].message.content or "").strip()
        m = re.search(r"\{.*\}", content, re.DOTALL)
        if not m:
            return {**fallback, "motivo": "resposta IA sem JSON", "ia_usada": True}
        data = json.loads(m.group())
        return {"veredito": str(data.get("veredito", "EXECUTAR")).upper(),
                "confianca": int(data.get("confianca", 0) or 0),
                "motivo": str(data.get("motivo", ""))[:140], "ia_usada": True}
    except Exception as exc:
        logger.warning("v7_ai_advisor: %s", exc)
        return {**fallback, "motivo": f"erro IA: {type(exc).__name__}", "ia_usada": True}
