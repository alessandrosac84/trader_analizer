"""
signal_validator_ai.py — Validacao de sinais tecnicos via Azure OpenAI (texto).

Fluxo:
  1. Sistema programatico detecta sinal com |score| >= threshold
  2. ANTES de executar o trade, chama validate_signal_with_ai()
  3. Se aprovado (veredito != "BLOQUEAR") -> executa
  4. Se bloqueado -> registra motivo e nao executa

Custo estimado: ~$0.005 por chamada (GPT-4o via Azure em modo texto).
Sem custo de imagem — usa apenas dados numericos dos indicadores.
"""
import json
import logging
import re

logger = logging.getLogger(__name__)

# Confianca minima da IA para aprovar (abaixo disso = AGUARDAR)
AI_MIN_CONFIDENCE = 55

_SYSTEM_PROMPT = (
    "Voce e um trader quantitativo especializado em mercados futuros B3 (WIN e WDO). "
    "Avalie setups de trading com rigor tecnico. "
    "Considere horario do pregao, volatilidade (ATR), posicao relativa dos indicadores "
    "e qualidade do risco/retorno. "
    "Responda SEMPRE com JSON valido no formato exato solicitado — sem texto extra."
)

_USER_PROMPT = """Analise os dados abaixo e avalie se o setup tem qualidade para execucao.

=== SINAL DETECTADO ===
Ativo:          {tv_symbol}
Acao:           {acao}
Score tecnico:  {score} (threshold minimo: {threshold})
Preco atual:    {preco}
Entrada:        {entrada}
Stop Loss:      {stop} (risco: {stop_pts} pts)
TP1:            {tp1} (alvo: {tp1_pts} pts | RR ~{rr})
TP2:            {tp2}
ATR(14):        {atr}
Timeframe:      {interval}min
Tendencia 1h:   {htf_trend}

=== INDICADORES ===
RSI(14):        {rsi}
ADX(14):        {adx}  |  +DI: {plus_di}  |  -DI: {minus_di}
EMA9:           {ema9}  |  EMA21: {ema21}  |  EMA50: {ema50}
VWAP:           {vwap}
MACD Hist:      {macd_hist}
BB Upper/Lower: {bb_upper} / {bb_lower}
Suporte:        {suporte}  |  Resistencia: {resistencia}

=== SINAIS IDENTIFICADOS PELO SISTEMA ===
{sinais_text}

Avalie com foco em:
1. Confluencia dos indicadores com a acao {acao}
2. Qualidade do stop (nao muito perto do preco, nao muito longe)
3. TP1 realizavel (entre suporte e resistencia para {acao})
4. Contexto ADX/tendencia (evitar operar contra tendencia forte)

Responda SOMENTE com este JSON:
{{
  "aprovado": true,
  "confianca": 75,
  "veredito": "EXECUTAR",
  "motivo": "Explicacao concisa (max 120 chars)",
  "alertas": ["alerta opcional"],
  "tp1_sugerido": null
}}

veredito pode ser: "EXECUTAR" | "AGUARDAR" | "BLOQUEAR"
tp1_sugerido: numero alternativo para TP1 se achar melhor, ou null"""


def _format_val(v, fmt=".1f") -> str:
    if v is None:
        return "N/A"
    try:
        return format(float(v), fmt)
    except Exception:
        return str(v)


def _pts_dist(a, b) -> str:
    try:
        return str(round(abs(float(a) - float(b))))
    except Exception:
        return "N/A"


def _rr(entry, tp1, stop) -> str:
    try:
        risk   = abs(float(entry) - float(stop))
        reward = abs(float(tp1) - float(entry))
        if risk == 0:
            return "N/A"
        return f"1:{reward/risk:.1f}"
    except Exception:
        return "N/A"


def validate_signal_with_ai(
    signal: dict,
    tv_symbol: str,
    interval: str = "15",
    score_threshold: int = 5,
) -> dict:
    """
    Valida um sinal programatico via Azure OpenAI (texto apenas, sem imagem).

    Parametros
    ----------
    signal          : dict retornado por generate_signal()
    tv_symbol       : ex. "BMFBOVESPA:WIN1!"
    interval        : timeframe em minutos
    score_threshold : threshold atual do auto-trade

    Retorna
    -------
    dict com: aprovado (bool), confianca (int), veredito (str),
              motivo (str), alertas (list), tp1_sugerido (float|None)
    """
    from services.config import Config

    _fallback_aprovado = {
        "aprovado":      True,
        "confianca":     60,
        "veredito":      "EXECUTAR",
        "motivo":        "Validacao IA nao disponivel — aprovacao pelo score tecnico.",
        "alertas":       [],
        "tp1_sugerido":  None,
        "ia_usada":      False,
    }

    # Se Azure nao esta configurado, nao bloqueia — usa aprovacao automatica
    if not Config.use_azure_openai() or not Config.AZURE_OPENAI_API_KEY:
        logger.info("AI validator: Azure nao configurado — aprovacao automatica.")
        return _fallback_aprovado

    acao    = signal.get("acao", "NEUTRO")
    preco   = signal.get("preco_atual") or signal.get("entrada")
    stop    = signal.get("stop")
    tp1     = signal.get("tp1")
    tp2     = signal.get("tp2")
    entry   = signal.get("entrada")

    sinais_text = "\n".join(
        f"  - {s}" for s in (signal.get("sinais") or [])
    ) or "  (nenhum)"

    prompt = _USER_PROMPT.format(
        tv_symbol   = tv_symbol,
        acao        = acao,
        score       = signal.get("score", 0),
        threshold   = score_threshold,
        preco       = _format_val(preco, ".2f"),
        entrada     = _format_val(entry, ".2f"),
        stop        = _format_val(stop,  ".2f"),
        stop_pts    = _pts_dist(entry, stop),
        tp1         = _format_val(tp1,  ".2f"),
        tp1_pts     = _pts_dist(entry, tp1),
        rr          = _rr(entry, tp1, stop),
        tp2         = _format_val(tp2,  ".2f"),
        atr         = _format_val(signal.get("atr")),
        interval    = interval,
        htf_trend   = signal.get("htf_trend") or "nao disponivel",
        rsi         = _format_val(signal.get("rsi")),
        adx         = _format_val(signal.get("adx")),
        plus_di     = _format_val(signal.get("plus_di")),
        minus_di    = _format_val(signal.get("minus_di")),
        ema9        = _format_val(signal.get("ema9"), ".0f"),
        ema21       = _format_val(signal.get("ema21"), ".0f"),
        ema50       = _format_val(signal.get("ema50"), ".0f"),
        vwap        = _format_val(signal.get("vwap"), ".0f"),
        macd_hist   = _format_val(signal.get("macd_hist"), ".4f"),
        bb_upper    = _format_val(signal.get("bb_upper"), ".0f"),
        bb_lower    = _format_val(signal.get("bb_lower"), ".0f"),
        suporte     = _format_val(signal.get("suporte"), ".0f"),
        resistencia = _format_val(signal.get("resistencia"), ".0f"),
        sinais_text = sinais_text,
    )

    try:
        from openai import AzureOpenAI

        client = AzureOpenAI(
            api_version    = Config.AZURE_OPENAI_API_VERSION,
            azure_endpoint = Config.AZURE_OPENAI_ENDPOINT,
            api_key        = Config.AZURE_OPENAI_API_KEY,
        )
        deployment = Config.AZURE_OPENAI_DEPLOYMENT or Config.OPENAI_MODEL

        response = client.chat.completions.create(
            model    = deployment,
            messages = [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user",   "content": prompt},
            ],
            temperature = 0.15,
            max_tokens  = 350,
        )

        content = (response.choices[0].message.content or "").strip()
        logger.info("AI validator resposta bruta: %s", content[:300])

        # Extrai JSON da resposta (pode vir com markdown ```json ...```)
        match = re.search(r"\{.*\}", content, re.DOTALL)
        if not match:
            logger.warning("AI validator: sem JSON na resposta.")
            return {**_fallback_aprovado, "motivo": "Resposta IA sem JSON — aprovacao conservadora.", "ia_usada": True}

        data = json.loads(match.group())

        aprovado   = bool(data.get("aprovado", True))
        confianca  = int(data.get("confianca", 60))
        veredito   = str(data.get("veredito", "EXECUTAR")).upper()
        motivo     = str(data.get("motivo", ""))[:200]
        alertas    = [str(a) for a in (data.get("alertas") or [])]
        tp1_sug    = data.get("tp1_sugerido")

        # Regra de seguranca: confianca baixa = AGUARDAR (nao bloqueia, apenas alerta)
        if aprovado and confianca < AI_MIN_CONFIDENCE:
            veredito = "AGUARDAR"
            alertas.append(f"Confianca da IA baixa ({confianca}%) — aguardar confirmacao.")

        return {
            "aprovado":      aprovado and veredito != "BLOQUEAR",
            "confianca":     confianca,
            "veredito":      veredito,
            "motivo":        motivo,
            "alertas":       alertas,
            "tp1_sugerido":  float(tp1_sug) if tp1_sug else None,
            "ia_usada":      True,
        }

    except Exception as exc:
        logger.warning("AI validator erro: %s", exc)
        return {
            **_fallback_aprovado,
            "motivo":  f"Erro na validacao IA — aprovacao pelo score tecnico. ({type(exc).__name__})",
            "ia_usada": True,
        }
