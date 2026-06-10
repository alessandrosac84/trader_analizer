import base64
import mimetypes
import os
from typing import Literal

from services.config import Config

Role = Literal["trader", "validator", "risk_manager"]

_SYSTEM_BY_ROLE: dict[Role, str] = {
    "trader": (
        "Você é um especialista em análise técnica de ativos da B3. "
        "Leia ticker, timeframe e o preço visível no print; proponha trades acionáveis nesse instante, "
        "sem gatilhos já ultrapassados na própria imagem."
    ),
    "validator": (
        "Você valida leituras de gráfico de ativos B3: coerência de ticker, timeframe e plano de trade."
    ),
    "risk_manager": (
        "Você é gestor de risco para operações em ativos listados na B3."
    ),
}


def _variant_index(image_path: str) -> int:
    try:
        return os.path.getsize(image_path) % 3
    except OSError:
        return 0


def _mock_trader(v: int) -> str:
    samples = [
        """{
  "preco_referencia_print": "128.450",
  "preco_referencia_como_detectado": "Última cotação na régua do eixo direito",
  "gatilho_ja_ocorreu_no_print": false,
  "ativo": "WINJ26",
  "ativo_como_detectado": "Legenda superior: símbolo WINJ26 visível ao lado do preço",
  "timeframe": "15m",
  "timeframe_como_detectado": "Legenda superior do gráfico mostra intervalo 15m",
  "tendencia": "alta",
  "contexto": "Pullback em tendência de alta após rompimento; região de compra na média.",
  "padrao": "Pullback com vela de confirmação",
  "acao": "COMPRA",
  "entrada": "128.450",
  "stop": "128.200",
  "stop_em_pontos": "25 pts",
  "alvo": "128.950",
  "alvos": [
    { "nome": "TP1", "distancia": "85 pts", "probabilidade": 82, "rr": "1:3.4" },
    { "nome": "TP2", "distancia": "170 pts", "probabilidade": 58, "rr": "1:6.8" },
    { "nome": "TP3", "distancia": "255 pts", "probabilidade": 38, "rr": "1:10.2" }
  ],
  "suporte": ["128.320", "128.180"],
  "resistencia": ["128.620", "128.780"],
  "rr": "1:2.2",
  "risco": "médio",
  "confianca": 74,
  "justificativa": "Sequência de fundos ascendentes; pullback até zona de demanda com absorção de venda.",
  "timeframe_observacao": "Em 15m o ruído é menor que em 5m; stops podem ser um pouco mais largos em pontos."
}""",
        """{
  "preco_referencia_print": "24.260",
  "preco_referencia_como_detectado": "Eixo de preços à direita",
  "gatilho_ja_ocorreu_no_print": false,
  "ativo": "PETR4",
  "ativo_como_detectado": "Cabeçalho do gráfico mostra ticker PETR4 (ação)",
  "timeframe": "5m",
  "timeframe_como_detectado": "Seletor de tempo visível: 5 minutos",
  "tendencia": "lateral",
  "contexto": "Faixa estreita entre suportes e resistências próximos; baixa expansão.",
  "padrao": "Lateralização",
  "acao": "NÃO OPERAR",
  "entrada": "—",
  "stop": "—",
  "stop_em_pontos": "",
  "alvo": "—",
  "alvos": [
    { "nome": "TP1", "distancia": "—", "probabilidade": 0, "rr": "—" }
  ],
  "suporte": [],
  "resistencia": [],
  "rr": "1:1.2",
  "risco": "alto",
  "confianca": 46,
  "justificativa": "Ausência de rompimento claro; ruído do timeframe consome o RR mínimo exigido.",
  "timeframe_observacao": "Em 5m a consolidação gera muitos falsos rompimentos; preferir aguardar."
}""",
        """{
  "preco_referencia_print": "198.135",
  "preco_referencia_como_detectado": "Último preço visível no eixo",
  "gatilho_ja_ocorreu_no_print": false,
  "ativo": "WINJ26",
  "ativo_como_detectado": "Mesmo símbolo WINJ26 na barra de título do gráfico",
  "timeframe": "1m",
  "timeframe_como_detectado": "Canto superior: 1m (scalp)",
  "tendencia": "baixa",
  "contexto": "Repique em zona de resistência após movimento corretivo; oferta ainda dominante.",
  "padrao": "Engolfo de baixa",
  "acao": "VENDA",
  "entrada": "198.135",
  "stop": "198.285",
  "stop_em_pontos": "30 pts",
  "alvo": "197.985",
  "alvos": [
    { "nome": "TP1", "distancia": "115 pts", "probabilidade": 80, "rr": "1:3.6" },
    { "nome": "TP2", "distancia": "225 pts", "probabilidade": 60, "rr": "1:7.2" },
    { "nome": "TP3", "distancia": "335 pts", "probabilidade": 40, "rr": "1:10.8" }
  ],
  "suporte": ["198.020", "197.910"],
  "resistencia": ["198.165", "198.245"],
  "rr": "1:3.6",
  "risco": "médio",
  "confianca": 70,
  "justificativa": "Padrão de engolfo de baixa em região de resistência após repique; topos e fundos descendentes reforçam continuidade vendedora.",
  "timeframe_observacao": "1m exige stops curtos e gestão rápida; alvos menores em pontos."
}""",
    ]
    return samples[v]


def _mock_validator(v: int) -> str:
    samples = [
        """{
  "aprovado": true,
  "ativo_ok": true,
  "timeframe_ok": true,
  "nota_confluencia": "4/5",
  "confianca_validacao": 78,
  "erro_encontrado": "",
  "motivo_reprovacao_critico": "",
  "ajustes_sugeridos": "Considerar entrada escalonada se o preço retestar a região.",
  "veredito_final": "APROVADO",
  "justificativa": "RR coerente com mínimo 1:2; stop abaixo da estrutura; contexto não lateral."
}""",
        """{
  "aprovado": false,
  "ativo_ok": true,
  "timeframe_ok": true,
  "nota_confluencia": "2/5",
  "confianca_validacao": 42,
  "erro_encontrado": "Mercado lateral com RR insuficiente para o ruído observado.",
  "motivo_reprovacao_critico": "Lateralização + RR abaixo do mínimo operável para o TF.",
  "ajustes_sugeridos": "Aguardar rompimento ou redução de volatilidade.",
  "veredito_final": "REPROVADO",
  "justificativa": "Critérios de reprovação: lateralização e RR abaixo de 1:2."
}""",
        """{
  "aprovado": true,
  "ativo_ok": true,
  "timeframe_ok": true,
  "nota_confluencia": "4/5",
  "confianca_validacao": 73,
  "erro_encontrado": "",
  "motivo_reprovacao_critico": "",
  "ajustes_sugeridos": "Monitorar spread e horário de baixa liquidez.",
  "veredito_final": "APROVADO",
  "justificativa": "Entrada alinhada ao fluxo; stop posicionado além da microestrutura."
}""",
    ]
    return samples[v]


def _mock_risk(v: int) -> str:
    samples = [
        """{
  "permitir_trade": true,
  "score_final": 76,
  "nivel_risco": "médio",
  "decisao": "EXECUTAR",
  "motivo": "Confluência entre trader e validador; RR >= 1:2; confiança acima do mínimo; mercado não lateral.",
  "alertas": ["Manter gestão de tamanho de posição conservadora."]
}""",
        """{
  "permitir_trade": false,
  "score_final": 44,
  "nivel_risco": "alto",
  "decisao": "NAO_OPERAR",
  "motivo": "Mercado lateral e baixa confiança agregada; RR inadequado ao cenário.",
  "alertas": ["Evitar forçar entrada até haver rompimento claro."]
}""",
        """{
  "permitir_trade": true,
  "score_final": 58,
  "nivel_risco": "médio",
  "decisao": "EXECUTAR",
  "motivo": "Ativo/TF ok; score no limiar mínimo (≥58); RR aceitável; validador aprovou com ressalvas gerenciáveis.",
  "alertas": ["Reavaliar após próximo fechamento de 15m."]
}""",
    ]
    return samples[v]


def _mock_response(role: Role, image_path: str) -> str:
    v = _variant_index(image_path)
    if role == "trader":
        return _mock_trader(v)
    if role == "validator":
        return _mock_validator(v)
    return _mock_risk(v)


def _image_mime(image_path: str) -> str:
    mime, _ = mimetypes.guess_type(image_path)
    if mime and mime.startswith("image/"):
        return mime
    ext = os.path.splitext(image_path)[1].lower()
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }.get(ext, "image/png")


def _chat_messages_payload(prompt: str, data_url: str, *, role: Role) -> list:
    return [
        {
            "role": "system",
            "content": _SYSTEM_BY_ROLE[role],
        },
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        },
    ]


def _call_azure_openai(prompt: str, image_path: str, *, role: Role) -> str:
    if not Config.AZURE_OPENAI_ENDPOINT or not Config.AZURE_OPENAI_API_KEY:
        raise RuntimeError(
            "Azure OpenAI: defina AZURE_OPENAI_ENDPOINT e AZURE_OPENAI_API_KEY no .env "
            "(a chave da Azure não funciona como OPENAI_API_KEY na API pública)."
        )
    deployment = Config.AZURE_OPENAI_DEPLOYMENT or Config.OPENAI_MODEL
    if not deployment:
        raise RuntimeError(
            "Azure OpenAI: defina AZURE_OPENAI_DEPLOYMENT com o nome do deployment "
            "(Studio Azure → Deployments)."
        )
    try:
        from openai import AzureOpenAI

        client = AzureOpenAI(
            api_version=Config.AZURE_OPENAI_API_VERSION,
            azure_endpoint=Config.AZURE_OPENAI_ENDPOINT,
            api_key=Config.AZURE_OPENAI_API_KEY,
        )
        with open(image_path, "rb") as img:
            b64 = base64.b64encode(img.read()).decode()
        mime = _image_mime(image_path)
        data_url = f"data:{mime};base64,{b64}"

        response = client.chat.completions.create(
            model=deployment,
            messages=_chat_messages_payload(prompt, data_url, role=role),
            temperature=0.3,
        )
        content = response.choices[0].message.content
        return content if content else ""
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(
            "Falha ao chamar Azure OpenAI. Verifique endpoint, AZURE_OPENAI_API_KEY, "
            "AZURE_OPENAI_DEPLOYMENT (nome exato no portal) e AZURE_OPENAI_API_VERSION. "
            f"Erro: {e!s}"
        ) from e


def _call_openai_public(prompt: str, image_path: str, *, role: Role) -> str:
    api_key = Config.OPENAI_API_KEY
    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY não configurada. Defina no arquivo .env ou use LLM_MODE=mock."
        )
    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key)
        with open(image_path, "rb") as img:
            b64 = base64.b64encode(img.read()).decode()
        mime = _image_mime(image_path)
        data_url = f"data:{mime};base64,{b64}"

        response = client.chat.completions.create(
            model=Config.OPENAI_MODEL,
            messages=_chat_messages_payload(prompt, data_url, role=role),
            temperature=0.3,
        )
        content = response.choices[0].message.content
        return content if content else ""
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(
            "Falha ao chamar a API OpenAI (api.openai.com). Verifique OPENAI_API_KEY, "
            f"OPENAI_MODEL e conexão. Erro: {e!s}"
        ) from e


def _call_openai(prompt: str, image_path: str, *, role: Role) -> str:
    if Config.use_azure_openai():
        return _call_azure_openai(prompt, image_path, role=role)
    return _call_openai_public(prompt, image_path, role=role)


def call_llm(prompt: str, image_path: str, *, role: Role) -> str:
    mode = Config.LLM_MODE
    if mode == "openai":
        return _call_openai(prompt, image_path, role=role)
    if mode == "mock":
        return _mock_response(role, image_path)
    raise ValueError(f"LLM_MODE inválido: {mode!r}. Use 'mock' ou 'openai'.")
