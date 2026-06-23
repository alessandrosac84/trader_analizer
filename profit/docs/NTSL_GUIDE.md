# Guia NTSL — Nelogica Script Language
## Referência para Desenvolvimento de Indicadores no Profit Chart

> Documento interno — Trade AI Platform  
> Sprint Profit 2 — Mapeamento da linguagem NTSL  
> Objetivo: servir de referência para sprints futuras (Profit 3, 4, ...)

---

## 1. O Que é NTSL?

**NTSL** (Nelogica Script Language) é a linguagem de programação utilizada pelo **Profit Chart** e **Profit Pro** para criar:

- **Indicadores** (Indicador, Sobreposição, Indicador de Ponto)
- **Colorações** de barras (Coloração)
- **Estratégias** de trade automatizado (Estratégia)
- **Screeners** de ativos

A linguagem é **Pascal-like** (estrutura `Begin...End`, `:=` para atribuição, `{*comentários*}`).

---

## 2. Estrutura Básica de um Indicador

```pascal
{* Comentário de cabeçalho *}
{Tipo: Sobreposição}   {* opcional — define o tipo *}

Input:
  Periodo : Integer(14);
  Cor     : Integer(clGreen);

Var:
  valor : Float;
  texto : String;

Begin
  valor := Media(Periodo, Close);
  Plot1(valor);
End;
```

### Tipos de Indicador (`{Tipo: ...}`)

| Tipo | Descrição | Uso típico |
|------|-----------|------------|
| `Indicador` | Exibe em sub-janela abaixo do gráfico | RSI, MACD, Volume |
| `Sobreposição` | Exibe sobre o gráfico de preços | Médias móveis, Bollinger |
| `Coloração` | Pinta as barras OHLCV | Barras de tendência |
| `Ponto` | Exibe pontos/setas no gráfico | Sinais de entrada |

---

## 3. Declaração de Inputs

```pascal
Input:
  NomeInput : Tipo(ValorPadrao);
```

### Tipos de Input

```pascal
Input:
  Periodo    : Integer(14);        {* Inteiro *}
  Fator      : Float(1.5);         {* Decimal *}
  Ativo      : String("PETR4");    {* Texto *}
  Usar       : Boolean(True);      {* Verdadeiro/Falso *}
  Cor        : Integer(clGreen);   {* Cor (constante) *}
```

### Sintaxe alternativa (uma por linha)

```pascal
Input: Periodo(14);
Input: Fator(1.5);
Input: Nome("texto");
```

---

## 4. Declaração de Variáveis

```pascal
Var:
  contador : Integer;
  preco    : Float;
  mensagem : String;
  ativo    : Boolean;
```

### Inicialização

```pascal
Var:
  contador : Integer(0);     {* Inicializa em 0 *}
  preco    : Float(0.0);
```

---

## 5. Operadores

### Atribuição e Comparação

```pascal
valor := 100;           {* Atribuição *}
If valor = 100 Then     {* Igual *}
If valor <> 100 Then    {* Diferente *}
If valor > 100 Then     {* Maior *}
If valor >= 100 Then    {* Maior ou igual *}
If valor < 100 Then     {* Menor *}
If valor <= 100 Then    {* Menor ou igual *}
```

### Operadores Lógicos

```pascal
If (A > 0) And (B > 0) Then ...
If (A > 0) Or  (B > 0) Then ...
If Not (A > 0)          Then ...
```

### Operadores Matemáticos

```pascal
soma  := A + B;
diff  := A - B;
mult  := A * B;
div   := A / B;
resto := A Mod B;
```

---

## 6. Estruturas de Controle

### If / Then / Else

```pascal
If condicao Then
  acao_simples
Else
  outra_acao;

{* Com bloco Begin...End *}
If condicao Then
Begin
  acao_1;
  acao_2;
End
Else
Begin
  acao_3;
End;
```

### For (loop)

```pascal
For i := 1 To 10 Do
Begin
  soma := soma + Close[i];   {* Close[i] = barra i atrás *}
End;
```

### While

```pascal
While (contador < 10) Do
Begin
  contador := contador + 1;
End;
```

---

## 7. Referências de Preço (OHLCV)

```pascal
Close       {* Fechamento barra atual *}
Open        {* Abertura barra atual *}
High        {* Máxima barra atual *}
Low         {* Mínima barra atual *}
Volume      {* Volume barra atual *}

Close[1]    {* Fechamento 1 barra atrás *}
Close[2]    {* Fechamento 2 barras atrás *}
High[5]     {* Máxima 5 barras atrás *}
```

---

## 8. Funções de Barra

```pascal
CurrentBar      {* Número da barra atual (começa em 1) *}
BarCount        {* Total de barras no histórico *}
LastBarOnChart  {* Boolean: True na última barra visível *}
Date            {* Data da barra (YYYYMMDD) *}
Time            {* Hora da barra (HHMM) *}
```

### Verificar última barra (padrão para painéis estáticos)

```pascal
If LastBarOnChart Then
Begin
  {* código executado uma única vez na barra mais recente *}
End;
```

---

## 9. Funções Matemáticas e de String

### Matemáticas

```pascal
Abs(valor)          {* Valor absoluto *}
Sqrt(valor)         {* Raiz quadrada *}
Power(base, exp)    {* Potência *}
Max(a, b)           {* Maior valor *}
Min(a, b)           {* Menor valor *}
Round(valor)        {* Arredonda para inteiro *}
Trunc(valor)        {* Trunca para inteiro *}
```

### Conversão de tipos

```pascal
IntToStr(n)         {* Inteiro para String *}
FloatToStr(f)       {* Float para String *}
StrToInt("9")       {* String para Inteiro *}
StrToFloat("9.5")   {* String para Float *}
Format("%.2f", f)   {* Formata decimal (2 casas) *}
```

### String

```pascal
texto := "Olá" + " " + "Mundo";   {* Concatenação com + *}
Length(texto)                      {* Comprimento *}
UpperCase(texto)                   {* Maiúsculas *}
LowerCase(texto)                   {* Minúsculas *}
Copy(texto, inicio, tamanho)       {* Substring *}
```

---

## 10. Indicadores Técnicos Nativos

```pascal
{* Médias Móveis *}
Media(Periodo, Close)                    {* MMA - Média Móvel Aritmética *}
MediaExp(Periodo, Close)                 {* MME - Média Móvel Exponencial *}
MediaPonderada(Periodo, Close)           {* Média Ponderada *}

{* Momentum e Tendência *}
RSI(Periodo, Close)                      {* Índice de Força Relativa *}
MACD(FastPer, SlowPer, SigPer, Close)   {* MACD *}
ADX(Periodo)                             {* Average Directional Index *}
ATR(Periodo)                             {* Average True Range *}
Momentum(Periodo, Close)                 {* Momentum *}

{* Volatilidade *}
BollingerUpper(Periodo, Desvios, Close) {* Banda Superior Bollinger *}
BollingerLower(Periodo, Desvios, Close) {* Banda Inferior Bollinger *}
StdDev(Periodo, Close)                  {* Desvio Padrão *}

{* Volume *}
OBV(Close, Volume)                      {* On Balance Volume *}

{* Candles *}
CandleColor   {* 1 = Alta (verde), 0 = Baixa (vermelha) *}
```

---

## 11. Cores

### Constantes de cor predefinidas

```pascal
clBlack       {* Preto *}
clWhite       {* Branco *}
clRed         {* Vermelho *}
clGreen       {* Verde *}
clBlue        {* Azul *}
clYellow      {* Amarelo *}
clGray        {* Cinza *}
clAqua        {* Ciano *}
clFuchsia     {* Magenta *}
clPurple      {* Roxo *}
clOrange      {* Laranja *}
clLime        {* Verde lima *}
clNavy        {* Azul marinho *}
clTeal        {* Verde-azulado *}
clMaroon      {* Bordô *}
clTransparent {* Transparente *}
```

### RGB personalizado (versões recentes)

```pascal
RGB(255, 0, 0)     {* Vermelho puro *}
RGB(0, 200, 83)    {* Verde Trade AI *}
RGB(124, 77, 255)  {* Roxo Trade AI *}
RGB(255, 214, 0)   {* Amarelo alerta *}
```

---

## 12. Desenhar Texto no Gráfico

### PlotText — texto em posição absoluta (mais comum)

```pascal
{* Assinatura: PlotText(Barra, Preco, "Texto", Cor, Tamanho) *}
PlotText(CurrentBar, Close * 1.01, "Meu texto", clWhite, 9);

{* Com variáveis *}
PlotText(vBarra, vPreco, "AÇÃO: " + i_Acao, clGreen, i_FontSize);
```

### DrawText — anotação alternativa

```pascal
{* Assinatura: DrawText(Barra, Preco, "Texto") *}
DrawText(CurrentBar, High, "SINAL");

{* Configurar cor antes *}
SetFontColor(clYellow);
DrawText(CurrentBar, High, "SCORE: 9");
```

### SetAnnotation — anotação no ponto de sinal

```pascal
SetAnnotation(CurrentBar, High, "Trade AI: COMPRA");
```

> **Nota:** Nem toda função está disponível em todas as versões do Profit.
> Se `PlotText` não compilar, use `DrawText`. Se `DrawText` não compilar,
> use `Plot1` com `SetPlotText`.

---

## 13. Desenhar Linhas no Gráfico

### Linha horizontal (nível de preço)

```pascal
{* Linha horizontal na média móvel *}
Plot1(Media(20, Close));
SetPlotColor(1, clYellow);
SetPlotStyle(1, psSolid);
SetPlotWidth(1, 2);
```

### Linha entre dois pontos

```pascal
{* DrawLine(BarraInicio, PrecoInicio, BarraFim, PrecoFim, Cor, Espessura) *}
DrawLine(CurrentBar - 10, Low[10], CurrentBar, Low, clGreen, 2);
```

### Linha vertical (barra específica)

```pascal
{* DrawVertLine(Barra, Cor, Estilo, Espessura) *}
DrawVertLine(CurrentBar, clGray, psDot, 1);
```

### Estilos de linha

```pascal
psSolid     {* Linha sólida contínua *}
psDash      {* Tracejada *}
psDot       {* Pontilhada *}
psDashDot   {* Traço-ponto *}
```

---

## 14. Desenhar Caixas / Retângulos

```pascal
{* DrawBox(BarraEsq, PrecoSuperior, BarraDir, PrecoInferior, CorBorda, CorFundo) *}
DrawBox(
  CurrentBar - 10,    {* Barra início *}
  High * 1.002,       {* Preço topo *}
  CurrentBar,         {* Barra fim *}
  Low * 0.998,        {* Preço base *}
  clWhite,            {* Cor da borda *}
  RGB(20, 20, 40)     {* Cor do fundo *}
);
```

### Retângulo transparente (zona de interesse)

```pascal
DrawBox(
  barraInicio, precoTopo,
  barraFim, precoBase,
  clYellow, clTransparent
);
```

---

## 15. Desenhar Zonas (Áreas Preenchidas)

### Zona de preço (faixa horizontal)

```pascal
{* Plot1 e Plot2 definem as bordas; SetPlotBetween preenche *}
Plot1(BollingerUpper(20, 2, Close));
Plot2(BollingerLower(20, 2, Close));
SetPlotColor(1, clBlue);
SetPlotColor(2, clBlue);
SetPlotStyle(1, psSolid);
SetPlotStyle(2, psSolid);
{* Nota: preenchimento entre plots varia por versão do Profit *}
```

### PaintBar — colorir toda a barra

```pascal
{* Pinta a barra quando condição é verdadeira *}
If Close > Media(20, Close) Then
  PaintBar(clLime)
Else
  PaintBar(clRed);
```

---

## 16. Desenhar Painéis (Múltiplos Elementos)

Um painel visual completo combina: caixa de fundo + múltiplas linhas de texto.

```pascal
{* Exemplo: painel completo Trade AI *}
If LastBarOnChart Then
Begin
  vBarra := CurrentBar - 3;
  vTopo  := High * 1.002;
  vBase  := High * 0.990;

  {* Fundo do painel *}
  DrawBox(vBarra - 1, vTopo * 1.001,
          vBarra + 1, vBase * 0.999,
          RGB(40, 40, 60), RGB(15, 15, 30));

  {* Textos *}
  PlotText(vBarra, vTopo,             "═ TRADE AI ═",  clWhite,   10);
  PlotText(vBarra, vTopo - vStep * 1, "AÇÃO: COMPRA",  clGreen,    9);
  PlotText(vBarra, vTopo - vStep * 2, "SCORE: 9",      clYellow,   9);
  PlotText(vBarra, vTopo - vStep * 3, "REGIME: TREND", clWhite,    9);
End;
```

---

## 17. Configuração dos Plots (Plot1...Plot9)

```pascal
Plot1(valor);                      {* Plot principal *}
Plot2(outrovalor);                 {* Plot secundário *}

SetPlotColor(1, clYellow);         {* Cor do Plot1 *}
SetPlotColor(2, clBlue);           {* Cor do Plot2 *}
SetPlotStyle(1, psSolid);          {* Estilo do Plot1 *}
SetPlotWidth(1, 2);                {* Espessura do Plot1 *}
SetPlotName(1, "Minha Média");     {* Nome na legenda *}
```

---

## 18. Alertas

```pascal
{* Emite alerta sonoro e visual quando condição for verdadeira *}
If Close > Media(20, Close) Then
  Alert("Preço cruzou acima da MM20!");

{* Alerta com nível de preço *}
If High > 135000 Then
  Alert("WIN atingiu 135.000 pts!");
```

---

## 19. Funções de Debug

```pascal
{* Escreve no Log do Profit (Ferramentas → Log) *}
WriteToLog("Valor atual: " + FloatToStr(Close));
WriteToLog("Score: " + IntToStr(i_Score));
```

---

## 20. Padrões para Sprints Futuras

### Sprint Profit 3 — Integração com arquivo JSON

Na Sprint 3, o indicador lerá o arquivo `trade_ai_profit.json` gerado pelo Trade AI. Possíveis abordagens no NTSL:

**Opção A: DLL customizada**
```pascal
{* Uma DLL em C++/Delphi expõe uma função que lê o JSON *}
Var: score: Integer;
Begin
  score := TradeAIDLL_GetScore("WIN");
  Plot1(score);
End;
```

**Opção B: Arquivo CSV intermediário**
```pascal
{* O Trade AI gera um CSV além do JSON — mais simples de ler em NTSL *}
{* NTSL pode ler arquivos com GetFileContent ou ReadFile em algumas versões *}
```

**Opção C: Named Pipe / Socket**
```pascal
{* Comunicação via socket local — avançado, requer pesquisa na versão específica *}
```

> A Sprint Profit 3 definirá a abordagem após validação do ambiente na Sprint 2.

---

## 21. Referências e Recursos

| Recurso | URL |
|---------|-----|
| Ajuda Nelogica | https://ajuda.nelogica.com.br |
| Fórum Nelogica | https://forum.nelogica.com.br |
| Documentação NTSL | Menu Profit → Ajuda → Editor de Estratégias → Ajuda |
| Exemplos no Profit | Editor de Estratégias → Arquivo → Exemplos |

> **Dica:** O próprio Profit Chart vem com exemplos NTSL acessíveis pelo Editor.
> Menu **Arquivo → Abrir Exemplo** mostra dezenas de indicadores prontos que
> servem como referência de sintaxe para a versão instalada.

---

## 22. Checklist de Compatibilidade

Antes de escrever um indicador novo, verifique no Profit instalado:

- [ ] `PlotText` compila? → Se não, use `DrawText`
- [ ] `RGB(r,g,b)` compila? → Se não, use constantes `clXxx`
- [ ] `DrawBox` compila? → Versão suporta desenho de caixas
- [ ] `ATR(14)` compila? → Verificar nome exato da função
- [ ] `LastBarOnChart` compila? → Alternativa: `CurrentBar = BarCount`
- [ ] `{Tipo: Sobreposição}` aceito? → Testar sem o header se der erro

---

*Documento interno Trade AI — Sprint Profit 2*  
*Atualizar conforme descobertas durante validação do ambiente*
