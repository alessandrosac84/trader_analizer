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
import time

logger = logging.getLogger(__name__)


def check_ai_health(timeout: float = 10.0) -> dict:
    """
    v6.1 — Health-check do gate de IA (Azure OpenAI).

    Faz um ping minimo e barato ao endpoint para confirmar que a validacao esta
    operante. Como o gate agora FALHA FECHADO, se o Azure estiver fora o
    auto-trade do Monitor MT5 fica parado — este check serve para VOCE saber
    disso ativamente (na inicializacao, num endpoint /api/ai/health, num agendamento
    ou no dashboard), em vez de descobrir pelo silencio.

    Retorna dict:
      { ok: bool, latency_ms: int|None, model: str|None, error: str|None,
        configured: bool }
    """
    from services.config import Config

    if not Config.use_azure_openai() or not Config.AZURE_OPENAI_API_KEY:
        return {"ok": False, "configured": False, "latency_ms": None,
                "model": None, "error": "Azure OpenAI nao configurado (endpoint/api_key ausentes)."}

    t0 = time.time()
    try:
        import httpx
        from openai import AzureOpenAI

        _http_client = httpx.Client(
            verify=False, trust_env=False,
            timeout=httpx.Timeout(connect=5.0, read=timeout, write=5.0, pool=5.0),
        )
        client = AzureOpenAI(
            api_version    = Config.AZURE_OPENAI_API_VERSION,
            azure_endpoint = Config.AZURE_OPENAI_ENDPOINT,
            api_key        = Config.AZURE_OPENAI_API_KEY,
            http_client    = _http_client,
        )
        deployment = Config.AZURE_OPENAI_DEPLOYMENT or Config.OPENAI_MODEL
        resp = client.chat.completions.create(
            model    = deployment,
            messages = [{"role": "user", "content": "ping — responda apenas: ok"}],
            temperature           = 0.0,
            max_completion_tokens = 5,
            timeout               = timeout,
        )
        _ = (resp.choices[0].message.content or "").strip()
        latency = int((time.time() - t0) * 1000)
        logger.info("AI health OK (%dms, deployment=%s)", latency, deployment)
        return {"ok": True, "configured": True, "latency_ms": latency,
                "model": deployment, "error": None}
    except Exception as exc:
        latency = int((time.time() - t0) * 1000)
        logger.warning("AI health FALHOU (%dms): %s — %s",
                       latency, type(exc).__name__, str(exc)[:300])
        return {"ok": False, "configured": True, "latency_ms": latency,
                "model": None, "error": f"{type(exc).__name__}: {str(exc)[:200]}"}

# Confianca minima da IA para aprovar (abaixo disso = AGUARDAR)
AI_MIN_CONFIDENCE = 65   # v6: era 55. Setup borderline vira AGUARDAR (mais qualidade)

# v6 ASSERTIVIDADE: comportamento em falha do validador.
# ANTES o validador "falhava aberto" — qualquer erro, timeout, ausencia de
# Azure ou resposta sem JSON resultava em veredito EXECUTAR, ou seja, o gate
# de risco aprovava o trade sem realmente validar. Um filtro de risco deve
# FALHAR FECHADO: na duvida, NAO opera. Com FAIL_CLOSED=True, a falha vira
# AGUARDAR (impede a execucao automatica no app.py).
# ⚠️ Consequencia: se o Azure OpenAI nao estiver configurado/disponivel, o
#    auto-trade do Monitor MT5 fica PARADO ate a IA voltar. Isso e intencional.
FAIL_CLOSED = True

# v6.1 — AGENTE ESPECIALISTA WIN/WDO (Monitor MT5).
# Prompt reescrito para ser especialista nos DOIS ativos do Monitor (mini indice
# WIN e mini dolar WDO), calibrado pelo que os dados reais mostraram:
#   - o maior gerador de perda foi operar CONTRA a tendencia de 1h;
#   - os unicos vencedores tinham |score| alto (>=9);
#   - TP1 aqui e um ALVO PARCIAL curto (existe TP2/TP3 + saida parcial), logo
#     RR do TP1 ~1.0 e NORMAL e NAO deve ser motivo de reprovacao.
_SYSTEM_PROMPT = (
    "Voce e um trader institucional especialista em DAY TRADE de mini indice (WIN) e "
    "mini dolar (WDO) na B3, operando via order flow e analise tecnica em 15m/5m. "
    "Sua funcao e ser o ULTIMO filtro antes de um robo executar a ordem: aprove so o que "
    "um trader profissional de WIN/WDO realmente operaria. Priorize PRESERVAR CAPITAL.\n"
    "CONHECIMENTO DO ATIVO:\n"
    "- WIN: cotado em PONTOS (tick 5 pts, R$1/pt no mini). Stops tipicos de 100-400 pts em 15m.\n"
    "- WDO: cotado em PONTOS (tick 0,5, R$10/pt no mini). Mais rapido e ruidoso que o WIN.\n"
    "- Ambos tem forte componente de horario: 09:00-09:15 e ruido de abertura; 12:00-14:00 "
    "e a janela morta (liquidez baixa, mais stops); 09:15-11:00 e 14:00-16:30 sao as boas.\n"
    "REGRAS DE DECISAO (rigor de profissional):\n"
    "- ALINHAMENTO COM 1h e prioridade maxima: NUNCA aprovar COMPRA com 1h em baixa nem "
    "VENDA com 1h em alta. Operar contra o 1h foi o maior gerador de perdas — BLOQUEAR.\n"
    "- FORCA DO SINAL: |score| >= 9 com 1h alinhada = confluencia forte, favorecer EXECUTAR; "
    "|score| entre 7 e 8 = aceitavel so se ADX>=23 e a favor do 1h; |score| < 7 = AGUARDAR.\n"
    "- RSI extremo alinhado a direcao e a favor da tendencia CONFIRMA (nao bloqueia).\n"
    "- TP1 e um alvo PARCIAL curto: RR do TP1 proximo de 1.0 e NORMAL — NAO reprove por isso. "
    "So desconfie se o STOP for absurdamente maior que o 1o alvo (stop 2x+ o TP1 = setup quebrado).\n"
    "- ADX: >=25 confirma tendencia; entre 20-25 exige cautela; <20 = range = tende a AGUARDAR.\n"
    "- HORARIO: se o setup cai na janela morta (12-14h) sem confluencia forte, prefira AGUARDAR.\n"
    "- BLOQUEAR: contra-tendencia de 1h, stop ilogico, ou mercado claramente lateral.\n"
    "- Na duvida entre EXECUTAR e AGUARDAR, escolha AGUARDAR (menos trades, mais qualidade).\n"
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

Avalie com foco em (ordem de prioridade):
1. ALINHAMENTO COM 1h (decisivo): COMPRA exige 1h != baixa; VENDA exige 1h != alta. Contra o 1h = BLOQUEAR.
2. Forca do sinal: |score|>=9 alinhado = EXECUTAR; 7-8 so com ADX>=23 a favor do 1h; <7 = AGUARDAR.
3. Stop coerente com a estrutura (0.5-2x ATR). TP1 e alvo PARCIAL: RR~1.0 e normal, NAO reprove por isso; so desconfie se stop for 2x+ o TP1.
4. ADX: >=25 confirma; 20-25 cautela; <20 range = AGUARDAR.
5. Coerencia direcional: para VENDA, TP1 < entrada; para COMPRA, TP1 > entrada.

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

    # v6: fallback FAIL-CLOSED (na duvida, nao opera). Ver FAIL_CLOSED acima.
    _fallback = {
        "aprovado":      not FAIL_CLOSED,
        "confianca":     60 if not FAIL_CLOSED else 0,
        "veredito":      "EXECUTAR" if not FAIL_CLOSED else "AGUARDAR",
        "motivo":        "Validacao IA indisponivel — "
                         + ("aprovacao pelo score tecnico." if not FAIL_CLOSED
                            else "gate fail-closed: aguardando IA."),
        "alertas":       [],
        "tp1_sugerido":  None,
        "ia_usada":      False,
    }

    # Se Azure nao esta configurado: com FAIL_CLOSED, NAO executa (aguarda).
    if not Config.use_azure_openai() or not Config.AZURE_OPENAI_API_KEY:
        logger.warning("AI validator: Azure nao configurado — veredito %s (fail-closed=%s).",
                       _fallback["veredito"], FAIL_CLOSED)
        return _fallback

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
        import httpx
        from openai import AzureOpenAI

        # Windows: desativa verificacao SSL (mesmo fix aplicado no Telegram)
        # Timeout de 20s: connect 5s + read 15s — evita travar o endpoint de execucao
        _http_client = httpx.Client(
            verify=False,
            trust_env=False,
            timeout=httpx.Timeout(connect=5.0, read=15.0, write=5.0, pool=5.0),
        )

        client = AzureOpenAI(
            api_version    = Config.AZURE_OPENAI_API_VERSION,
            azure_endpoint = Config.AZURE_OPENAI_ENDPOINT,
            api_key        = Config.AZURE_OPENAI_API_KEY,
            http_client    = _http_client,
        )
        deployment = Config.AZURE_OPENAI_DEPLOYMENT or Config.OPENAI_MODEL

        response = client.chat.completions.create(
            model    = deployment,
            messages = [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user",   "content": prompt},
            ],
            temperature             = 0.15,
            max_completion_tokens   = 350,
            timeout                 = 18.0,
        )

        content = (response.choices[0].message.content or "").strip()
        logger.info("AI validator resposta bruta: %s", content[:300])

        # Extrai JSON da resposta (pode vir com markdown ```json ...```)
        match = re.search(r"\{.*\}", content, re.DOTALL)
        if not match:
            logger.warning("AI validator: sem JSON na resposta.")
            return {**_fallback, "motivo": "Resposta IA sem JSON — fail-closed (aguardar).", "ia_usada": True}

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
        import traceback
        logger.warning("AI validator erro COMPLETO: %s", traceback.format_exc())
        logger.warning("AI validator erro resumido: %s \u2014 %s", type(exc).__name__, str(exc)[:500])
        return {
            **_fallback,
            "motivo":  f"Erro na validacao IA \u2014 fail-closed (aguardar). ({type(exc).__name__})",
            "ia_usada": True,
        }
