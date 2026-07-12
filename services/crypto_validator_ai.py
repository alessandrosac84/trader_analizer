"""
services/crypto_validator_ai.py — Validador de IA EXCLUSIVO do Monitor Crypto.

⚠️ MÓDULO NOVO E INDEPENDENTE. Espelha o gate de IA do Monitor MT5 (Azure OpenAI,
fail-closed, confiança mínima) mas com PROMPT e regras próprios de crypto/forex.
NÃO importa nem altera o validador da B3 (signal_validator_ai).

Retorna: {aprovado, confianca, veredito(EXECUTAR|AGUARDAR|BLOQUEAR), motivo}.
"""
import re
import json
import logging

logger = logging.getLogger(__name__)

FAIL_CLOSED = True        # na dúvida/falha, NÃO opera (igual ao Monitor MT5)
AI_MIN_CONFIDENCE = 65    # abaixo disso → AGUARDAR

_SYSTEM = (
    "Você é um trader quantitativo sênior especializado em CRYPTO (BTCUSD, ETHUSD) "
    "e FOREX (EURUSD, GBPUSD, etc.), operando 24h. Recebe um sinal técnico já "
    "calculado e decide se vale EXECUTAR agora. Considere: alinhamento de tendência "
    "(EMA50/EMA200 e 1h), força do movimento (ADX), momentum (RSI/MACD), volatilidade "
    "(ATR) e a relação risco:retorno do alvo. Crypto tem volatilidade alta e movimentos "
    "24h; forex é mais lateral e sensível a sessão. Seja conservador: na dúvida, AGUARDAR. "
    "Bloqueie entradas contra a tendência de 1h, em exaustão (RSI extremo) ou com R:R ruim. "
    "Responda SOMENTE JSON válido."
)

_USER = (
    "Sinal para {symbol} ({interval}m):\n"
    "- Ação: {acao}  | score: {score}  | força: {forca}\n"
    "- Entrada: {entrada} | Stop: {stop} | TP1: {tp1} | R:R: {rr}\n"
    "- RSI: {rsi} | ADX: {adx} | ATR: {atr} | tendência 1h: {htf}\n"
    "- Confluências: {conf}\n\n"
    "Decida se vale operar AGORA. Responda JSON:\n"
    '{{"veredito":"EXECUTAR|AGUARDAR|BLOQUEAR","confianca":0-100,"motivo":"<=160 chars",'
    '"aprovado":true|false}}'
)


def _rr(entry, tp1, stop):
    try:
        if entry and tp1 and stop and abs(entry - stop) > 0:
            return round(abs(tp1 - entry) / abs(entry - stop), 2)
    except Exception:
        pass
    return "—"


def validate(sig: dict, symbol: str, interval: str = "15") -> dict:
    """Valida o sinal do Monitor Crypto. Retorna dict com veredito/confiança/motivo."""
    from services.config import Config

    fallback = {
        "aprovado":  not FAIL_CLOSED,
        "confianca": 0 if FAIL_CLOSED else 60,
        "veredito":  "AGUARDAR" if FAIL_CLOSED else "EXECUTAR",
        "motivo":    "IA indisponível — gate fail-closed (aguardando)." if FAIL_CLOSED
                     else "IA indisponível — aprovado pelo score técnico.",
        "ia_usada":  False,
    }

    if not Config.use_azure_openai() or not Config.AZURE_OPENAI_API_KEY:
        logger.warning("crypto AI: Azure não configurado — %s (fail-closed=%s)",
                       fallback["veredito"], FAIL_CLOSED)
        return fallback

    try:
        import httpx
        from openai import AzureOpenAI
        http = httpx.Client(verify=False, trust_env=False,
                            timeout=httpx.Timeout(connect=5.0, read=15.0, write=5.0, pool=5.0))
        client = AzureOpenAI(
            api_version=Config.AZURE_OPENAI_API_VERSION,
            azure_endpoint=Config.AZURE_OPENAI_ENDPOINT,
            api_key=Config.AZURE_OPENAI_API_KEY, http_client=http,
        )
        prompt = _USER.format(
            symbol=symbol, interval=interval, acao=sig.get("acao"),
            score=sig.get("score"), forca=sig.get("forca"),
            entrada=sig.get("entrada"), stop=sig.get("stop"), tp1=sig.get("tp1"),
            rr=_rr(sig.get("entrada"), sig.get("tp1"), sig.get("stop")),
            rsi=sig.get("rsi"), adx=sig.get("adx"), atr=sig.get("atr"),
            htf=sig.get("htf_trend"), conf="; ".join(sig.get("confluences", [])[:6]),
        )
        resp = client.chat.completions.create(
            model=Config.AZURE_OPENAI_DEPLOYMENT or Config.OPENAI_MODEL,
            messages=[{"role": "system", "content": _SYSTEM},
                      {"role": "user", "content": prompt}],
            temperature=0.15, max_completion_tokens=250, timeout=18.0,
        )
        content = (resp.choices[0].message.content or "").strip()
        m = re.search(r"\{.*\}", content, re.DOTALL)
        if not m:
            return {**fallback, "motivo": "Resposta IA sem JSON — fail-closed.", "ia_usada": True}
        data = json.loads(m.group())
        veredito = str(data.get("veredito", "EXECUTAR")).upper()
        confianca = int(data.get("confianca", 60))
        aprovado = bool(data.get("aprovado", veredito == "EXECUTAR"))
        motivo = str(data.get("motivo", ""))[:200]
        if aprovado and veredito == "EXECUTAR" and confianca < AI_MIN_CONFIDENCE:
            veredito = "AGUARDAR"
            motivo = (motivo + f" · confiança {confianca}% < {AI_MIN_CONFIDENCE}").strip()
        return {
            "aprovado": aprovado and veredito == "EXECUTAR",
            "confianca": confianca, "veredito": veredito, "motivo": motivo, "ia_usada": True,
        }
    except Exception as exc:
        logger.warning("crypto AI erro: %s", exc)
        return {**fallback, "motivo": f"Falha IA — fail-closed ({str(exc)[:60]}).", "ia_usada": True}
