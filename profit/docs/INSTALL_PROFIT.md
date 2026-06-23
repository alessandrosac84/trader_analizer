# Guia de Instalação — TradeAI Commander no Profit Chart

> **Sprint Profit 2** — Validação do ambiente NTSL  
> Plataforma: Profit Chart / Profit Pro (Nelogica)  
> Indicador: `TradeAI_Commander.ntsl`

---

## Pré-requisitos

- Profit Chart instalado e logado (versão Pro ou superior recomendada)
- Acesso ao Editor de Estratégias (Menu → Ferramentas → Editor de Estratégias)
- Gráfico aberto em qualquer ativo (WIN, WDO, PETR4, etc.)

---

## Passo 1 — Abrir o Editor de Estratégias

1. No Profit Chart, clique no menu **Ferramentas** na barra superior
2. Selecione **Editor de Estratégias** (ou pressione `F8`)
3. O Editor abrirá em uma nova janela separada

**Alternativa:** Clique com o botão direito sobre qualquer gráfico → **Inserir Estudo** → **Novo Indicador**

---

## Passo 2 — Criar um Novo Indicador

1. No Editor de Estratégias, clique em **Arquivo → Novo** (ou `Ctrl+N`)
2. Na janela que aparece, selecione o tipo:
   - **Tipo:** `Indicador`
   - **Subtipo:** `Sobreposição` (se disponível)
3. Dê o nome: `TradeAI_Commander`
4. Clique em **OK**

O editor abrirá com um template básico. **Apague todo o conteúdo** do template.

---

## Passo 3 — Colar o Código

1. Abra o arquivo `profit/indicators/TradeAI_Commander.ntsl` em qualquer editor de texto (Bloco de Notas, VS Code, etc.)
2. Selecione todo o conteúdo (`Ctrl+A`) e copie (`Ctrl+C`)
3. No Editor de Estratégias do Profit, clique na área de código e cole (`Ctrl+V`)

O código deve aparecer completo com inputs, variáveis e bloco principal.

---

## Passo 4 — Compilar

1. No Editor de Estratégias, clique em **Compilar** (ícone de engrenagem ▶, ou `F9`, ou Menu → Compilar)
2. Aguarde a compilação

**Resultado esperado:** Mensagem **"Compilação bem-sucedida"** na barra inferior.

### Se ocorrerem erros de compilação:

| Erro | Causa | Solução |
|------|-------|---------|
| `PlotText não identificado` | Versão antiga do Profit | Substituir por `DrawText` (ver notas no .ntsl) |
| `RGB não identificado` | Versão sem RGB | Substituir por `clGreen`, `clRed`, etc. |
| `ATR não identificado` | Nome diferente na versão | Substituir por `MediaMovel(14, High - Low, 0)` |
| `Tipo: Sobreposição inválido` | Header diferente | Remover a linha `{Tipo: Sobreposição}` |
| Erro de ponto e vírgula | Falta `;` | Verificar linha indicada no erro |

---

## Passo 5 — Inserir no Gráfico

### Opção A — Arrastar do Painel de Estudos
1. Abra o **Painel de Estudos** (Menu → Exibir → Painel de Estudos)
2. Procure por `TradeAI_Commander` na lista
3. Arraste para o gráfico desejado

### Opção B — Inserir pelo Menu
1. Clique com o botão direito no gráfico
2. Selecione **Inserir Estudo**
3. Na aba **Indicadores**, procure `TradeAI_Commander`
4. Selecione e clique em **OK**

### Opção C — Menu Inserir
1. Menu **Inserir → Estudos → Indicadores**
2. Localize `TradeAI_Commander`
3. Confirme com **OK**

---

## Passo 6 — Configurar os Inputs

Após inserir o indicador, uma janela de configuração aparecerá automaticamente.

### Inputs disponíveis:

| Input | Tipo | Padrão | Descrição |
|-------|------|--------|-----------|
| `i_Ativo` | Texto | `WDO` | Nome do ativo exibido no painel |
| `i_Acao` | Texto | `COMPRA` | Ação do Trade AI: `COMPRA`, `VENDA` ou `NEUTRO` |
| `i_Score` | Inteiro | `9` | Score do sinal (0-10) |
| `i_Forca` | Inteiro | `73` | Força do sinal (0-100) |
| `i_Risco` | Inteiro | `10` | Nível de risco (0-100) |
| `i_Regime` | Texto | `TRENDING` | Regime de mercado |
| `i_Confluencias` | Inteiro | `9` | Número de confluências |
| `i_Tendencia` | Texto | `ALTA` | Tendência: `ALTA`, `BAIXA` ou `LATERAL` |
| `i_FontSize` | Inteiro | `9` | Tamanho da fonte do painel |
| `i_OffsetBars` | Inteiro | `3` | Deslocamento horizontal (barras) |
| `i_OffsetPrice` | Float | `0.003` | Deslocamento vertical (fração do preço) |

Ajuste os valores conforme o sinal atual e clique em **OK**.

---

## Passo 7 — Verificar Renderização

O painel deve aparecer **no canto superior direito do gráfico**, exibindo:

```
═══ TRADE AI ═══
ATIVO: WDO
AÇÃO:  COMPRA       ← verde
SCORE: 9            ← verde (>= 7)
FORÇA: 73%          ← verde (>= 60%)
RISCO: 10%          ← verde (<= 25%)
REGIME: TRENDING
CONFLUÊNCIAS: 9
TENDÊNCIA: ALTA
─── v1.0 Sprint P2 ───
```

**Cores esperadas:**
- COMPRA → Verde | VENDA → Vermelho | NEUTRO → Cinza
- Score ≥ 7 → Verde | Score 5-6 → Amarelo | Score < 5 → Vermelho
- Risco ≤ 25% → Verde | Risco 26-50% → Amarelo | Risco > 50% → Vermelho

---

## Ajustes de Posição

Se o painel aparecer fora da área visível ou sobreposto ao gráfico:

1. Clique com o botão direito no indicador → **Propriedades**
2. Ajuste `i_OffsetPrice`:
   - **WIN** (mini-índice): tente `0.001` (preço ~130.000)
   - **WDO** (mini-dólar): tente `0.002` (preço ~5.800)
   - **PETR4** (ação): tente `0.01` (preço ~40)
3. Ajuste `i_OffsetBars`: número de barras da borda direita (padrão: 3)

---

## Passo 8 — Atualizar Versões Futuras

Quando o Trade AI publicar uma nova versão do indicador (Sprint Profit 3+):

1. Abra o **Editor de Estratégias** (`F8`)
2. Abra o indicador `TradeAI_Commander` (`Arquivo → Abrir`)
3. Selecione todo o código (`Ctrl+A`) e cole a nova versão (`Ctrl+V`)
4. Compile (`F9`)
5. O indicador já inserido nos gráficos será atualizado automaticamente

> **Nota:** Os valores dos Inputs são preservados ao atualizar o código. Apenas lógica e novos inputs precisam ser reconfigurados.

---

## Troubleshooting Rápido

### Painel não aparece no gráfico
- Verifique se a compilação foi bem-sucedida
- Confirme que o indicador está inserido no gráfico (aba de estudos ativos)
- Tente rolar o gráfico para a direita para ver a última barra

### Texto aparece piscando ou duplicado
- Desative e reative o indicador (checkbox no painel de estudos)
- Isso força o Profit a re-renderizar o overlay

### Indicador não aparece na lista após compilar
- Verifique se salvou o arquivo (`Ctrl+S` no Editor)
- Feche e reabra o painel de estudos

### Erro "Sobreposição não suportada"
- Remova a linha `{Tipo: Sobreposição}` do código
- Recompile — o Profit usará o tipo padrão (Indicador de Linha)

---

## Registro de Versões

| Versão | Sprint | Descrição |
|--------|--------|-----------|
| v1.0 | Profit 2 | Painel visual com Inputs manuais — validação do ambiente |
| v2.0 | Profit 3 | Integração com `trade_ai_profit.json` via DLL ou arquivo |
| v3.0 | Profit 4 | Painel dinâmico com alertas e zonas no gráfico |

---

*Documento gerado pelo Trade AI — Sprint Profit 2*
