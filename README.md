# Trade AI — MVP (mini índice WIN)

Aplicação web em **Flask** que analisa **prints de gráficos** do mini índice (WIN) com um pipeline de **três agentes** (trader → validador → gestor de risco), usando **visão + texto** quando o modo LLM real está ativo, ou **respostas mock** para desenvolvimento sem custo de API.

---

## Funcionalidades

- **Dashboard** (`/dashboard`): upload de imagem (arrastar, selecionar ou **colar com Cmd/Ctrl+V**), visualização estilo “Print & Trade” com sinal, confiança, entrada, stop, alvos, suportes/resistências e **timeframe (TF)** detectado no print.
- **Pipeline de agentes**: cada etapa lê o prompt em `prompts/` e devolve JSON; o risco agrega decisão e score.
- **Histórico**: análises persistidas em **SQLite** (`data/trade_ai.db`); tabela com thumbnail, decisão, score e botão **Detalhes** (JSON dos três agentes).
- **API JSON**: health na raiz, histórico e detalhe por ID para integração ou debug.

---

## Arquitetura

```
Imagem (upload) → Trader (leitura do gráfico + plano)
                → Validator (confluência / erros)
                → Risk Manager (permitir trade, score, decisão)
                → Persistência SQLite + resposta ao cliente
```

O **timeframe visível na imagem** (1m, 5m, 15m, etc.) é tratado como **obrigatório** no prompt do trader: a análise deve ser coerente com esse intervalo; o dashboard exibe um chip **TF** e observações quando o modelo as envia.

---

## Requisitos

- Python **3.10+** (recomendado)
- Navegador moderno

---

## Instalação

```bash
cd trade_ai_mvp
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Copie as variáveis de ambiente:

```bash
cp .env.example .env
```

Edite `.env` conforme a seção abaixo.

---

## Configuração (`.env`)

| Variável | Descrição |
|----------|-----------|
| `LLM_MODE` | `mock` — respostas fixas por variante (sem API). `openai` — chama OpenAI ou Azure. |
| `OPENAI_API_KEY` | Chave da API pública OpenAI (quando `LLM_MODE=openai` e provedor OpenAI). |
| `OPENAI_MODEL` | Modelo multimodal (padrão `gpt-4o`). |
| `OPENAI_PROVIDER` | Vazio ou `openai`: API pública. `azure`: Azure OpenAI. |
| `AZURE_OPENAI_ENDPOINT` | URL do recurso (`https://<recurso>.openai.azure.com`). |
| `AZURE_OPENAI_API_KEY` | Chave do portal Azure (sem texto extra ou prefixos de exemplo). |
| `AZURE_OPENAI_DEPLOYMENT` | Nome do **deployment** com visão (ex.: `gpt-4o`). |
| `AZURE_OPENAI_API_VERSION` | Versão da API REST (ex.: `2024-08-01-preview`). |
| `SECRET_KEY` | Chave secreta Flask (produção). |
| `PORT` | Porta do servidor (padrão `5000`). |

Pastas criadas automaticamente: `uploads/` (imagens), `data/` (banco SQLite).

---

## Como executar

```bash
python app.py
```

Abra [http://127.0.0.1:5000/dashboard](http://127.0.0.1:5000/dashboard) (ou a porta definida em `PORT`).

Health check: [http://127.0.0.1:5000/](http://127.0.0.1:5000/) retorna `llm_mode` e link do dashboard.

---

## API HTTP

| Método | Rota | Descrição |
|--------|------|-----------|
| `GET` | `/` | Status do serviço e `llm_mode`. |
| `GET` | `/dashboard` | Interface principal (HTML). |
| `POST` | `/analyze` | `multipart/form-data` com campo **`image`** (arquivo PNG/JPEG/WebP/GIF). Resposta JSON com `trader`, `validator`, `risk_manager`, `resumo`, `image_url`, `id`. |
| `GET` | `/uploads/<nome>` | Imagem armazenada (nome seguro `uuid.ext`). |
| `GET` | `/api/history` | Lista até 200 análises com `image_url`. |
| `GET` | `/api/analysis/<id>` | Uma análise completa (inclui JSONs dos agentes). |

Limite de upload: **6 MB** por requisição (`MAX_CONTENT_LENGTH` em `services/config.py`).

---

## Estrutura do repositório

```
trade_ai_mvp/
├── app.py                 # Rotas Flask e orquestração do pipeline
├── agents/                # Chamadas ao LLM por papel (trader, validator, risk_manager)
├── prompts/               # Prompts em texto (.txt) por agente
├── services/
│   ├── config.py          # Configuração e variáveis de ambiente
│   ├── db.py              # SQLite — histórico
│   ├── llm.py             # Mock, OpenAI e Azure OpenAI
│   └── json_utils.py      # Extração de JSON e resumo de risco
├── templates/             # dashboard.html
├── static/                # CSS e JS do dashboard
├── data/                  # trade_ai.db (criado ao rodar)
└── uploads/               # Imagens enviadas (nomes uuid)
```

---

## Modo mock

Com `LLM_MODE=mock`, não é necessária chave de API. As respostas alternam por variante (hash do tamanho do arquivo) e incluem campos de exemplo como `timeframe`, `timeframe_como_detectado` e `timeframe_observacao` para testar a UI.

---

## Segurança e produção

- Altere `SECRET_KEY` em produção.
- O servidor de desenvolvimento (`debug=True`) não é adequado para ambiente público; use um WSGI (gunicorn, uwsgi, etc.) atrás de HTTPS.
- Não commite `.env` nem chaves.

---

## Licença e aviso

Projeto de **MVP / demonstração**. Nada aqui constitui recomendação financeira ou de investimento. Use por sua conta e risco.
