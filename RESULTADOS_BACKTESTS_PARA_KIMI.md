# Resultados consolidados dos backtests — para análise do Kimi

**Projeto:** robô de day trade / swing sobre MetaTrader 5 (Python), operando B3 (WIN, WDO) via XP e crypto/metais (BTCUSD, ETHUSD, XAUUSD) via IC Markets.
**Objetivo deste documento:** mostrar tudo o que já testamos, com números reais, para que você (Kimi) veja o que já falhou e sugira **novos setups/cenários** que ainda valha a pena testar — de preferência coisas estruturalmente diferentes das que já reprovaram.

---

## 1. Metodologia (importante — leia antes de propor)

Todo backtest abaixo segue o **mesmo rigor**, senão o resultado não conta:

- **Barra a barra (walk-forward)**, sem olhar o futuro. Entrada avaliada no fechamento da barra; SL/TP checados barra a barra.
- **Custos reais aplicados** em toda operação (ver seção 2). Resultado é sempre **líquido**, medido em **R** (múltiplos do risco inicial).
- **Consistência por bimestre**: % de bimestres com expectância positiva. Um setup que só ganha em 1 ou 2 janelas não presta.
- **Histórico longo**: WIN/WDO têm ~7 anos (M15). Crypto/ouro na corretora só entregam ~1,4–2 anos de M15 (limite do broker) — isso é uma limitação real, não preguiça.
- **Critério de GO** (só entra em produção quem passa nos três):
  `expectância líquida ≥ +0,10 R` **e** `Profit Factor ≥ 1,25` **e** `consistência ≥ 55–60%`.

O que **não** aceitamos como prova: resultado bom só em período recente e curto (já nos enganou uma vez — ver seção 6), PF alto com pouquíssimos trades (< 40), ou setup que depende de gap/overnight que o simulador infla.

---

## 2. Modelo de custos usado

| Ativo | Custo round-trip aplicado |
|---|---|
| WIN | R$ 0,50/contrato (taxas) + 1 tick de spread na entrada a mercado + 1 tick na saída no stop (tick = 5 pts, ponto = R$ 0,20) |
| WDO | R$ 1,15/contrato + spread análogo (tick = 0,5 pt, ponto = R$ 10,00) |
| BTCUSD | spread cheio ~15,0 (medido ao vivo quando possível) |
| ETHUSD | spread cheio ~2,9 |
| XAUUSD | spread cheio ~0,30 |

Entrada a LIMITE economiza o spread da entrada; stop **sempre** cruza o spread. Guarda anti-armadilha: risco mínimo por trade ≥ 2× spread (senão o custo come tudo — lição do ETH).

---

## 3. WIN (mini índice B3) — ~7,1 anos, M15 (46.535 candles, 2021-07 → 2026-07)

Aqui está **o único edge comprovado do projeto**. Testamos várias gerações de motor:

| Motor | Trades | Win% | LÍQ (R) | PF | Consist. | Veredito |
|---|---|---|---|---|---|---|
| v6 saída fixa (produção antiga) | 2116 | 41,6% | **+0,159** | — | 44% | reprova (consist.) |
| v6 gerido (parcial+BE+trailing) | 2524 | 51,1% | +0,036 | — | 59% | reprova |
| v7.1 (regime + ORB + fade) | 307 | 54,4% | +0,088 | — | 52% | reprova |
| **v7.4.1 (GAP_FADE + ORB) — OFICIAL** | **330** | **55,2%** | **+0,182** | **1,44** | **65%** | **🟢 GO** |

Detalhe do motor oficial v7.4.1:
- **GAP_FADE** (fade do gap de abertura 09:15 em direção ao fechamento anterior): n=128, +0,172 R, PF 1,38
- **ORB** (rompimento do range de abertura 10:00–10:45): n=202, +0,189 R, PF 1,48
- Por regime: TREND_DOWN +0,240 R · FORMING +0,172 R · TREND_UP +0,134 R (robusto nos três)
- Total +60,2 R em 7 anos, maxDD apenas −7,6 R.

**Conclusão WIN:** temos edge de abertura (gap fade + ORB). Nenhum outro setto testado no WIN bateu isso.

---

## 4. WDO (mini dólar B3) — ~7,1 anos, M15

Tudo **reprovou**. O WDO é mais eficiente/ruidoso e o custo relativo é maior.

| Motor | Trades | LÍQ (R) | PF | Consist. | Veredito |
|---|---|---|---|---|---|
| v6 saída fixa | 2160 | +0,076 | — | 39% | 🔴 |
| v6 gerido | 2568 | −0,033 | — | 33% | 🔴 |
| v7.1 | 303 | −0,063 | — | 35% | 🔴 |
| v7.3 gerido (GAP_FADE + ORB_RETEST) | 234 | −0,014 | 0,98 | — | 🔴 |

Só o GAP_FADE no WDO fica levemente positivo (+0,031 R, PF 1,06) — insuficiente. **WDO está reprovado; não operamos.**

---

## 5. Crypto / Ouro — motor "PRO" (score + impulso + pullback), M15

Nosso motor estrutural para 24h. Reprovou em tudo:

| Ativo | Período | Trades | Win% | LÍQ (R) | PF | Consist. | Veredito |
|---|---|---|---|---|---|---|---|
| BTCUSD | ~1,4 ano | 1719 | 55,7% | −0,008 | 0,98 | 33% | 🔴 |
| ETHUSD | ~1,4 ano | 2189 | 48,8% | −0,151 | 0,69 | 11% | 🔴 |
| XAUUSD | ~2,1 anos | 2009 | 53,9% | −0,008 | 0,98 | 42% | 🔴 |

Observação útil: no XAU, o **caminho de PULLBACK** foi o único positivo (+0,208 R, PF 1,64) mas com só 47 trades — pista de que pullback em tendência no ouro pode ter algo, mas amostra fraca.

**Validação profunda extra — DAILY_MOMO no ouro (H1, ~5,7 anos, 2018–2026):** 286 trades, LÍQ −0,069 R, PF 0,85, consistência 41%. **Reprovado** mesmo com histórico longo.

Lab de setups estruturais 24h (ASIA_BREAK, ASIA_RETEST, LONDON_ORB, NY_ORB, PDH_PDL, MONDAY_GAP): nenhum passou o critério de GO.

**Conclusão crypto/ouro:** ainda NÃO temos edge comprovado. Tudo em observação.

---

## 6. Seus 12 setups (Kimi) — testados com nosso rigor

Rodamos seus 12 setups em **M5** (como você projetou, mas o broker só dá ~2,5 anos B3 / ~0,5 ano crypto) e em **M15** (para alcançar os ~7 anos no WIN / ~1,4 ano no crypto). Custos reais e modelo de saída seu (SL / TP1 2R → breakeven / TP2 3R).

### 6a. WIN — M15, 7,1 anos (o teste que importa)

| Setup | Trades | Win% | LÍQ (R) | PF | Consist. | Veredito |
|---|---|---|---|---|---|---|
| ORDER_FLOW ⭐novo | 514 | 46,3% | +0,031 | 1,08 | 47% | 🔴 |
| ORB | 146 | 49,3% | −0,006 | 0,98 | 51% | 🔴 |
| MA_CROSS | 659 | 39,6% | −0,032 | 0,94 | 49% | 🔴 |
| LIQ_SWEEP ⭐novo | 335 | 30,7% | −0,098 | 0,85 | 44% | 🔴 |
| ICT_SWEEP ⭐novo | 730 | 26,0% | −0,145 | 0,78 | 38% | 🔴 |
| VWAP_PB / BOLLINGER / TREND_PB | — | — | todos negativos | — | — | 🔴 |
| SCALP / PIVOT / MARKET_PROFILE | 0 | — | (sem trades no M15) | — | — | — |

> ⚠️ **Alerta anti-overfitting:** o LIQ_SWEEP parecia 🟢 GO nos 2,5 anos de M5 (+0,160 R, PF 1,26, cons 68%). No histórico completo de 7 anos ele **reprovou** (−0,098 R). Foi ilusão de período curto e recente. Por isso exigimos os 6+ anos.

**No WIN nenhum dos seus setups bate o v7.4.1. O motor de abertura continua imbatível ali.**

### 6b. Crypto / Ouro — M15, ~1,4–2,1 anos

| Setup | Ativo | Trades | LÍQ (R) | PF | Consist. | Veredito |
|---|---|---|---|---|---|---|
| **ORDER_FLOW ⭐novo** | **XAUUSD** | 190 | **+0,267** | **1,46** | **69%** | **🟢 GO** |
| **ORDER_FLOW ⭐novo** | **ETHUSD** | 77 | **+0,317** | **1,50** | **61%** | **🟢 GO** (amostra fina) |
| ORDER_FLOW ⭐novo | BTCUSD | 119 | +0,158 | 1,24 | 59% | 🟡 quase |
| MA_CROSS | XAUUSD | 406 | +0,151 | 1,23 | 64% | 🟡 |
| ICT_SWEEP / LIQ_SWEEP / ORB | todos | — | negativos ou ~zero | — | — | 🔴 |

**O grande achado seu foi o ORDER_FLOW** (entra a favor do delta de volume acumulado quando o preço rompe a área de valor via VWAP, com confirmação de volume 1,5×). É o único conceito **novo** que se repetiu em duas amostras independentes (M5 0,5 ano **e** M15 1,4 ano) e passou/quase passou em XAU, BTC e ETH — justamente ativos onde ainda não temos edge.

Status atual: montamos um validador dedicado (`validar_orderflow.py`) que submete o ORDER_FLOW a **teste out-of-sample** (treino 70% × teste 30%), consistência por bimestre e sensibilidade de parâmetro, para confirmar se não é overfit antes de qualquer uso ao vivo. Ainda não é "pode operar" — é "promissor e repetível".

---

## 7. Resumo do que está PROVADO × REPROVADO

**Provado (em produção):**
- WIN — motor v7.4.1 (GAP_FADE + ORB de abertura): +0,182 R, PF 1,44, 65% consistência, 7 anos.

**Candidato forte (em validação OOS):**
- ORDER_FLOW no XAUUSD (e BTC): seu conceito de delta de fluxo. Melhor achado fora do WIN até hoje.

**Reprovado (não operar):**
- WDO — todos os motores.
- Crypto/ouro motor PRO (score+impulso+pullback) — BTC, ETH, XAU.
- DAILY_MOMO ouro (H1, 5,7 anos).
- Setups clássicos de varejo (SCALP, MA_CROSS, Bollinger, TREND_PB, VWAP_PB) — sangram no custo. SCALP no WDO chegou a −1027 R.
- LIQ_SWEEP / ICT_SWEEP — bons em janela curta, caem no histórico longo.

---

## 8. O que pedimos a você (Kimi)

Dado tudo acima, sugira **novos setups para testar**, com estas restrições:

1. **Não repita** o que já reprovou (scalp genérico, cruzamento de médias, Bollinger, ICT/liquidity sweep puro, momo diário no ouro).
2. Foque em **conceitos estruturalmente diferentes** — pode ser evolução do **ORDER_FLOW** (que funcionou): variações de filtro de delta, confluência com sessão/horário, gestão de saída diferente, seleção de regime.
3. Para **crypto/ouro**, lembre que só temos ~1,4–2 anos de M15 — proponha coisas que façam sentido nesse horizonte e que não dependam de amostra gigante.
4. Para **WIN**, o desafio é achar algo que **conviva** com o v7.4.1 (não precisa vencê-lo; precisa ser positivo e descorrelacionado — ex.: um setup de tarde/fechamento, já que o nosso só opera a abertura 09:15–10:45).
5. Cada setup proposto deve vir com: **condição de entrada objetiva, SL, TP, e em que ativo/horário/regime você espera que funcione** — para eu codificar direto no nosso motor de teste.

Envie as ideias e eu rodo todas com este mesmo rigor e te devolvo os números.

---

## 9. RODADA 2 — resultados dos 6 setups que você (Kimi) propôs

Testados com o mesmo rigor (M15, custos reais, walk-forward, **out-of-sample** 70%×30%). WIN teve 7,1 anos; crypto/ouro ~1,4–2 anos. Placar: **1 GO em 6** — mas o GO é forte e é a evolução do ORDER_FLOW, exatamente sua prioridade nº1.

| # | Setup | Ativo | Completo | Out-of-sample | Veredito |
|---|---|---|---|---|---|
| 2 | **LONDON_HANDOFF** | XAU | +0,208 R · PF 1,56 · cons 62% | **+0,329 R · PF 2,07 · cons 82%** | 🟢 **GO** |
| 5 | XAU_PULLBACK | XAU | −0,031 R · PF 0,95 (n=2391) | +0,042 R (fraco) | 🔴 |
| 4 | BTC_EXPANSION | BTC | −0,073 R · PF 0,89 | +0,035 R (fraco) | 🔴 |
| 3 | ETH_FRONTRUN | ETH | −0,114 R · PF 0,85 | −0,244 R (piorou) | 🔴 |
| 1 | REVERSAO_TARDE | WIN | −0,075 R · PF 0,87 | −0,002 R (zerado) | 🔴 |
| 6 | SPIKE_FADE | WIN | **0 trades em 7 anos** | — | filtro raro demais |

**O acerto:** o **LONDON_HANDOFF** foi seu melhor palpite. Especializar o ORDER_FLOW para a transição Ásia→Londres funcionou — o out-of-sample ficou **melhor** que o in-sample (PF 2,07 > 1,39), o que indica edge estrutural, não overfit. Já está rodando ao vivo em demo no XAU, junto do ORDER_FLOW.

**Aprendizados p/ a próxima rodada:**
- Filtrar o ORDER_FLOW por **sessão/horário** é o caminho que dá edge no ouro. Vale explorar mais variações nessa linha (ex.: handoff Londres→NY, ou filtro por dia da semana).
- Pullback puro no ouro (setup 5) NÃO tem edge mesmo com 2391 trades — abandonar essa pista do motor PRO.
- ETH e BTC continuam difíceis: front-run em nível redondo e squeeze/expansão reprovaram. Crypto puro segue sem edge comprovado.
- No WIN, nem reversão de tarde nem gap de continuação vingaram. O SPIKE_FADE precisa de limiares mais frouxos (gap >1% + volume 2× quase nunca ocorre no WIN$D contínuo) para gerar amostra — se quiser, proponha limiares e eu re-testo.

**Novo pedido:** dado que só o conceito "delta filtrado por sessão" no ouro deu certo (duas vezes seguidas), sugira 2–3 variações **dentro dessa família** (delta/fluxo + janela de sessão específica), em XAU e talvez BTC, com entrada/SL/TP objetivos. É onde temos a maior chance de achar o próximo GO.
