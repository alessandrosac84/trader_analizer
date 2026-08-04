# Inventário COMPLETO de setups testados — atualizado 21/07/2026

Legenda: ✅ **APROVADO e EM USO** · 🟡 promissor/observação · 🔴 reprovado · ⚪ sem amostra
Critério de GO: líq ≥ +0,10 R · PF ≥ 1,25 · consistência ≥ 55% · out-of-sample segura.

---

## 1. Motores WIN / WDO (M15, ~7,1 anos, custos reais)

| Motor / setup | WIN | WDO | Status |
|---|---|---|---|
| **v7.4.1 — GAP_FADE + ORB** | **+0,182 R · PF 1,44 · cons 65%** | −0,014 R | ✅ **EM USO no WIN (Monitor V7)** · 🔴 WDO |
| ├─ GAP_FADE (fade do gap 09:15) | +0,172 R · PF 1,38 | +0,031 R (PF 1,06) | ✅ WIN · 🔴 WDO |
| └─ ORB (rompimento 10:00–10:45) | +0,189 R · PF 1,48 | −0,055 R (retest) | ✅ WIN · 🔴 WDO |
| v6 saída fixa (regras do Monitor MT5) | +0,159 R · cons 44% | +0,076 R · cons 39% | 🟡 roda no **Monitor MT5** (legado; não passa o GO por consistência) |
| v6 com exits geridos | +0,036 R | −0,033 R | 🔴 |
| v7.1 (regime + ORB + fade) | +0,088 R | −0,063 R | 🔴 |

## 2. Crypto / Ouro — motor PRO (score + impulso + pullback, M15)

| Caminho | BTC | ETH | XAU | Status |
|---|---|---|---|---|
| SCORE | +0,002 | −0,151 | +0,004 | 🔴 desligado |
| IMPULSO | −0,064 | −0,159 | −0,086 | 🔴 desligado |
| PULLBACK | — | — | +0,208 R · PF 1,64 (n=47) | 🟡 **EM USO no XAU** (amostra fraca, cascade) |

## 3. Lab de setups estruturais 24h (crypto/ouro/forex)

ASIA_BREAK 🔴 · ASIA_RETEST 🔴 · LONDON_ORB 🔴 · NY_ORB 🔴 · PDH_PDL 🔴 · MONDAY_GAP 🔴 · H1_PULLBACK 🔴 · DAILY_MOMO 🔴 (validação profunda H1 5,7 anos: −0,069 R).

## 4. Scalper

Sem edge comprovado em backtest — 🟡 em observação (não é motor validado).

## 5. Kimi — rodada 1 (12 setups, todos os ativos, M5 e M15)

| Setup | Resultado | Status |
|---|---|---|
| **ORDER_FLOW** (delta + área de valor) | XAU M5: +0,179 R · PF 1,31 · **OOS segurou** · ETH M15: +0,260 R · PF 1,51 · OOS segurou | ✅ **EM USO: XAU M5 e ETH M15 (Monitor Crypto)** · 🔴 BTC (OOS caiu) · 🔴 WIN |
| LIQ_SWEEP | GO em 2,5 anos M5… caiu nos 7 anos (−0,098 R) | 🔴 **overfit confirmado** |
| ICT_SWEEP | negativo em tudo no longo prazo | 🔴 |
| ORB / VWAP_PB / MA_CROSS / BOLLINGER / TREND_PB / SCALP | negativos ou ~zero com custo | 🔴 (MA_CROSS XAU 🟡 +0,151, não passou) |
| PIVOT / MARKET_PROFILE / FVG | sem amostra utilizável | ⚪ |

## 6. Kimi — rodada 2 (6 setups)

| Setup | Ativo | Resultado | Status |
|---|---|---|---|
| **LONDON_HANDOFF** (rompe range asiático estreito + delta, 09–11 servidor) | XAU M15 | +0,208 R · PF 1,56 · **OOS +0,329 R · PF 2,07 · cons 82%** | ✅ **EM USO no XAU (Monitor Crypto)** |
| XAU_PULLBACK | XAU | −0,031 R (n=2391) | 🔴 |
| ETH_FRONTRUN | ETH | −0,114 R (OOS piorou) | 🔴 |
| BTC_EXPANSION | BTC | −0,073 R | 🔴 |
| REVERSAO_TARDE | WIN | −0,075 R | 🔴 |
| SPIKE_FADE | WIN | 0 trades em 7 anos | ⚪ filtro raro demais |

## 7. Kimi — rodada 3 (4 setups de abertura WIN, M15 7,1 anos)

| Setup | Completo | Out-of-sample | Status |
|---|---|---|---|
| ORB_DIR (ORB só no lado do gap) | +0,089 R · PF 1,32 · cons 62% | +0,001 R · PF 1,0 | 🟡 OOS fraco — não liga |
| SEGUNDA_TENT (a favor do gap quando fade falha) | +0,091 R · PF 1,16 | −0,337 R | 🟡→🔴 OOS caiu |
| PRE_ORB | −0,001 R | (in-sample negativo) | 🔴 |
| FADE_IMPULSO | −0,055 R | +0,051 R (fraco) | 🔴 |

## 8. Ações B3 — VALE3, PETR4, ITUB4, ABEV3 (21/07/2026)

Mega-backtest: 23 setups × M5 e M15 × 4 ações (~5,2 anos em M15) + motores reais.
**Resultado: ZERO 🟢 GO.** Em ~150 combinações, nada positivo com amostra válida (n≥40).
Os "menos ruins" (VALE3 M15 FADE_IMPULSO −0,026 R, REVERSAO_TARDE −0,023 R) ainda perdem.
Os poucos positivos tinham n=9–36 (ruído) e caíram no out-of-sample.

Motores da produção nessas ações (M15, 5,2 anos):

| | VALE3 | PETR4 | ITUB4 | ABEV3 |
|---|---|---|---|---|
| V6_FIXED (regras do Monitor MT5) | −0,106 R · PF 0,84 · cons 34% | **−0,184 R · PF 0,74 · cons 21%** | −0,224 R · PF 0,69 · cons 13% | −0,459 R · PF 0,48 · cons 7% |
| V6_MANAGED | −0,136 R | −0,242 R | −0,292 R | (não rodou — reboot) |

**Conclusão: não incluir essas ações nos motores.** O custo de ação (0,055% + spread)
consome ~10% do R por trade e o bruto já era negativo. Padrão consistente demais
(23 conceitos, 2 timeframes, 5 anos, 4 ações) para ser azar.

⚠️ **PONTO EM ABERTO — PETR4:** o PETR4 **está hoje na lista do Monitor MT5**
(`_MONITORED_SYMBOLS`) e o motor v6 nele deu **−0,184 R, PF 0,74, consistência 21%**
em 5,2 anos. Registrado para decisão futura — nada foi alterado no Monitor MT5.

**Duas falhas do 1º run, já corrigidas (21/07):**
1. *H1 nunca rodou* — o `fetch` só mapeava M5/M15 e caía no fallback M5; os blocos
   "M60" eram cópia do M5. Corrigido (agora mapeia M30/H1/H4).
2. *V7 deu 0 trades* — artefato: o v7 tem janelas de relógio fixas do WIN
   (OR 09:00–10:00, Janela A 09:30–11:30, GAP_FADE 09:00–09:30) e ações abrem 10:00.
   Corrigido com `align_session()`, que desloca o relógio da ação para abrir às 09:00
   (preserva o "tempo desde a abertura") sem tocar na produção. Validado: 0 → 23 trades.

---

## RESUMO — o que está rodando AO VIVO hoje

| Módulo | Ativo | Setup(s) | Validação |
|---|---|---|---|
| **Monitor V7** | WIN M15 | GAP_FADE + ORB (v7.4.1) | ✅ 7 anos, PF 1,44, cons 65% |
| **Monitor MT5** (v6, legado) | WIN / WDO / PETR4 | score técnico v6 saída fixa | 🟡 +0,159 R WIN mas cons 44% — mantido por decisão |
| **WinGo runtime** | WIN/WDO M15 | NR5/NR7/INSIDE*/HL_H4 + **WDO ×4** (NR5/NR4/NR7_1014/PDH) | ✅ GO v3–v7 |
| **WIN_EOD_REV** | WIN M15 | reversão fim de pregão | ✅ paper (edge discovery) |
| **Monitor Crypto** | XAUUSD | OF + LH_* + RND_FADE_* + INSIDE_H4 + XAU_INS_AM + US_DRIFT | ✅ |
| **Monitor Crypto** | ETHUSD | ORDER_FLOW + **ETH_INSIDE_H4** | ✅ |
| **Monitor Crypto** | BTCUSD | — (vetado) | 🔴 nada passou |
| **Scalper** | BTC | — | 🟡 observação |

---

## 9. Rodada Auto v3 (23/07/2026) — setups novos

78 setups testados (31 B3 + 47 crypto). Critério GO inalterado.

| Setup | Ativo | Completo | OOS | Status |
|---|---|---|---|---|
| **NR7_BREAK** | WIN M15 | +0,262 R · PF 1,52 · cons 59% · n=183 | +0,139 · PF 1,27 ✅ | ✅ **EM USO** (WinGo runtime) |
| **INSIDE_BAR_BRK** | WIN M15 | +0,159 R · PF 1,29 · cons 68% · n=325 | +0,365 · PF 1,76 ✅ | ✅ **EM USO** (WinGo runtime) |
| **RND_FADE_TIGHT** | XAU M15 | +0,178 R · PF 1,32 · cons 58% · n=105 | +0,106 · PF 1,17 ✅ | ✅ **EM USO** (Monitor Crypto) |
| OVN_PDC_035_EXT / OVN_ATR_* / LH_* / etc. | — | vários 🟡 | OOS fraco ou amostra | 🔴 não ligar |

## 10. Rodada Auto v4 (23/07/2026) — variações + novos

54 setups (25 B3 + 29 crypto). Só 🟢 GO integrados.

| Setup | Ativo | Completo | OOS | Status |
|---|---|---|---|---|
| **NR7_TREND_H4** | WIN M15 | +0,374 R · PF 1,81 · cons 67% · n=102 | +0,387 · PF 1,96 ✅ | ✅ **EM USO** (WinGo · magic 20260725) |
| **RND_FADE_07** | XAU M15 | +0,273 R · PF 1,51 · cons 70% · n=86 | +0,379 · PF 1,73 ✅ | ✅ **EM USO** (Monitor Crypto) |
| **LH_047_D18** | XAU M15 | +0,185 R · PF 1,45 · cons 68% · n=113 | +0,135 · PF 1,31 ✅ | ✅ **EM USO** (Monitor Crypto · 1/dia) |
| OVN_* / INSIDE_VOL15 / RND_FADE_T_* / etc. | — | 🟡 ou 🔴 | OOS fraco / n baixo | 🔴 não ligar |

## 11. Rodada Auto v5 (23/07/2026) — refinos + novos

64 setups (36 B3 + 28 crypto). `RND_FADE_T_RR18` = duplicata do TIGHT → não religado.

| Setup | Ativo | Completo | OOS | Status |
|---|---|---|---|---|
| **NR7_H4_MT** | WIN M15 | +0,325 R · PF 1,69 · cons 67% · n=87 | +0,264 · PF 1,62 ✅ | ✅ **EM USO** (magic 20260726) |
| **INSIDE_H4** | WIN M15 | +0,308 R · PF 1,64 · cons 64% · n=178 | +0,474 · PF 2,12 ✅ | ✅ **EM USO** (magic 20260727) |
| **INSIDE_V18_H4** | WIN M15 | +0,267 R · PF 1,53 · cons 59% · n=78 | +0,514 · PF 2,19 ✅ | ✅ **EM USO** (magic 20260728) |
| **INSIDE_AM** | WIN M15 | +0,146 R · PF 1,26 · cons 62% · n=297 | +0,332 · PF 1,68 ✅ | ✅ **EM USO** (magic 20260729) |
| **XAU_INSIDE_H4** | XAU M15 | +0,315 R · PF 1,62 · cons 67% · n=75 | +0,492 · PF 2,10 ✅ | ✅ **EM USO** |
| **RND_FADE_MT** | XAU M15 | +0,262 R · PF 1,50 · cons 61% · n=69 | +0,429 · PF 1,87 ✅ | ✅ **EM USO** |
| **LH_049_D19** | XAU M15 | +0,158 R · PF 1,39 · cons 64% · n=110 | +0,172 · PF 1,42 ✅ | ✅ **EM USO** (1/dia) |
| **ETH_INSIDE_H4** | ETH M15 | +0,169 R · PF 1,30 · cons 65% · n=84 | +0,248 · PF 1,50 ✅ | ✅ **EM USO** |
| RND_FADE_T_RR18 | XAU | = RND_FADE_TIGHT | — | ⚪ já ao vivo |

## 12. Rodada Auto v6 (23/07/2026) — refino 🟡 + frequência

55 setups (31 B3 + 24 crypto). OVN_* seguem 🟡 (OOS n curto).

| Setup | Ativo | Completo | OOS | Status |
|---|---|---|---|---|
| **NR5_H4** | WIN M15 | +0,245 R · PF 1,48 · cons 56% · n=182 | +0,498 · PF 2,25 ✅ | ✅ **EM USO** (20260731) |
| **NR5_H4_MT** | WIN M15 | +0,225 R · PF 1,44 · cons 57% · n=150 | +0,493 · PF 2,33 ✅ | ✅ **EM USO** (20260730) |
| **INSIDE_1015_H4** | WIN M15 | +0,272 R · PF 1,55 · cons 67% · n=221 | +0,371 · PF 1,81 ✅ | ✅ **EM USO** (20260734) |
| **INSIDE_V13_H4** | WIN M15 | +0,263 R · PF 1,53 · cons 61% · n=155 | +0,486 · PF 2,15 ✅ | ✅ **EM USO** (20260733) |
| **INSIDE_VOL15_H4** | WIN M15 | +0,242 R · PF 1,48 · cons 59% · n=120 | +0,534 · PF 2,24 ✅ | ✅ **EM USO** (20260732) |
| **HL_H4** | WIN M15 | +0,221 R · PF 1,56 · cons 57% · n=139 | +0,157 · PF 1,35 ✅ | ✅ **EM USO** (20260735) |
| **WDO_NR5_H4** | WDO M15 | +0,136 R · PF 1,27 · cons 56% · n=190 | +0,315 · PF 1,67 ✅ | ✅ **EM USO** (20260736) |
| **WDO_NR4_H4** | WDO M15 | +0,188 R · PF 1,39 · cons 58% · n=245 | OOS segurou ✅ | ✅ **EM USO** v7 (20260737) |
| **WDO_NR7_1014_H4** | WDO M15 | +0,132 R · PF 1,28 · cons 55% · n=83 | OOS segurou ✅ | ✅ **EM USO** v7 (20260738) |
| **WDO_PDH_H4** | WDO M15 | +0,209 R · PF 1,38 · cons 57% · n=99 | +0,588 · PF 2,39 ✅ | ✅ **EM USO** v7 (20260739) |
| **XAU_INS_AM** | XAU M15 | +0,295 R · PF 1,57 · cons 58% · n=70 | +0,555 · PF 2,26 ✅ | ✅ **EM USO** |
| **RND_FADE_065** | XAU M15 | +0,278 R · PF 1,53 · cons 68% · n=77 | +0,257 · PF 1,46 ✅ | ✅ **EM USO** |
| **RND_FADE_075_MT** | XAU M15 | +0,149 R · PF 1,26 · cons 58% · n=73 | +0,227 · PF 1,40 ✅ | ✅ **EM USO** |
| **LH_048_D19** | XAU M15 | +0,142 R · PF 1,34 · cons 64% · n=107 | +0,172 · PF 1,42 ✅ | ✅ **EM USO** (1/dia) |

## 13. Ações B3 V2 — setups específicos (24/07/2026)

Script: `backtest_acoes_b3_v2.py`. Hipóteses **só de ação** (não port WIN/crypto).
Universo: PETR4, VALE3, ITUB4, BBDC4, ABEV3, WEGE3, BBAS3 · ~5 anos.
Log: `logs/backtest_acoes_v2_20260724_1316.txt`.

| Setup | TF | Pool (7 papers) | OOS pool | Status |
|---|---|---|---|---|
| ACOES_GAP_CONT_H1 | H1 | n=804 · net −0,100 R · PF 0,78 · cons 39% | −0,077 | 🔴 |
| ACOES_PB_D1 | D1 | n=200 · net −0,185 R · PF 0,75 · cons 33% | −0,088 | 🔴 |
| ACOES_ORB30_DIR | M15 | n=1694 · net −0,104 R · PF 0,69 · cons 20% | −0,084 | 🔴 |

**Resultado: ZERO 🟢 GO** (por paper e no pool cross-symbol).
Melhor ponto isolado: WEGE3 GAP_CONT_H1 +0,017 R (ainda 🔴 — PF/cons insuficientes).
ITUB4 PB_D1 +0,121 R mas n=31 (amostra fraca).

**Conclusão (v2):** ZERO 🟢. Campanha day-trade/swing específica não passou a régua.

## 13b. Ações B3 V3 MEGA — blue chips (01/08/2026)

Script: `backtest_acoes_b3_v3_mega.py` · **637 setups** · 7 papers · ~5,2a MT5 XP ·
custos % B3 + tick · EOD 16:54 · pregão 10–17 · sem FDS.
Log: `logs/backtest_acoes_v3_mega_20260801_2111.txt` ·
ranking: `logs/backtest_acoes_v3_mega_ranking_20260801_2111.csv`.

Régua intacta: net ≥ +0,10 R · PF ≥ 1,25 · cons ≥ 55% · OOS+ · n ≥ 40.

| Ativo | testados | 🟢 GO | 🟡 |
|---|---|---|---|
| VALE3 | 91 | **9** | 15 |
| WEGE3 | 91 | **3** | 4 |
| BBAS3 | 91 | **2** | 5 |
| PETR4 | 91 | 0 | 4 |
| ITUB4 | 91 | 0 | 2 |
| BBDC4 | 91 | 0 | 0 |
| ABEV3 | 91 | 0 | 0 |

**14 🟢 GO wired** em `services/acoes_go_paths.py` (magics **20260810–20260823**):

| Setup | TF | Completo | OOS | Magic | Status |
|---|---|---|---|---|---|
| **VALE3_ORB30_V15** | M15 | +0,274 R · PF 2,69 · cons 68% · n=71 | +0,263 ✅ | 20260810 | ✅ WIRED |
| **VALE3_ORB30_GAP_V13** | M15 | +0,216 R · PF 2,00 · cons 57% · n=85 | +0,210 ✅ | 20260811 | ✅ WIRED |
| **VALE3_ORB30_GAP_V11** | M15 | +0,191 R · PF 1,90 · cons 55% · n=122 | +0,216 ✅ | 20260812 | ✅ WIRED |
| **VALE3_IMP_DAY_M5** | M5 | +0,180 R · PF 1,51 · cons 59% · n=102 | +0,435 ✅ | 20260813 | ✅ WIRED |
| **VALE3_VWAPC_AM** | M15 | +0,139 R · PF 1,32 · cons 65% · n=489 | +0,246 ✅ | 20260814 | ✅ WIRED |
| **VALE3_GAPC_G30** | M15 | +0,133 R · PF 1,40 · cons 67% · n=280 | +0,301 ✅ | 20260815 | ✅ WIRED |
| **VALE3_GAPC_G30_MT** | M15 | +0,120 R · PF 1,35 · cons 63% · n=246 | +0,256 ✅ | 20260816 | ✅ WIRED |
| **VALE3_GAPC_G45** | M15 | +0,112 R · PF 1,33 · cons 60% · n=251 | +0,245 ✅ | 20260817 | ✅ WIRED |
| **VALE3_GAPC_G60** | M15 | +0,111 R · PF 1,33 · cons 59% · n=219 | +0,263 ✅ | 20260818 | ✅ WIRED |
| **BBAS3_ORB30_V13** | M15 | +0,145 R · PF 1,73 · cons 62% · n=91 | +0,087 ✅ | 20260819 | ✅ WIRED |
| **BBAS3_ORB30_TP15** | M15 | +0,141 R · PF 1,71 · cons 62% · n=91 | +0,089 ✅ | 20260820 | ✅ WIRED |
| **WEGE3_INS_AM** | M15 | +0,178 R · PF 1,40 · cons 58% · n=90 | +0,084 ✅ | 20260821 | ✅ WIRED |
| **WEGE3_GAPC_G30_MT** | M15 | +0,144 R · PF 1,41 · cons 57% · n=121 | +0,165 ✅ | 20260822 | ✅ WIRED |
| **WEGE3_GAPC_G30** | M15 | +0,112 R · PF 1,31 · cons 59% · n=115 | +0,182 ✅ | 20260823 | ✅ WIRED |

Tela: `/acoes` · runtime: `services/acoes_runtime.py` (scan sempre; fill se `ACOES_AUTO_ON=1`).
WIN/WDO **não cortados**. 30 🟡 em observação (sem live).

## 13c. Ações B3 V4 MEGA — complemento + expansão (01–02/08/2026)

Script: `backtest_acoes_b3_v4_mega.py` · **1379 setups NOVOS** (0 overlap com v3) ·
7 papers · ~214 min · workers=6 · custos % B3 + tick · EOD 16:54.
Cumulativo v3+v4: **~2016** setups (plano ~1274 coberto com folga).
Log: `logs/backtest_acoes_v4_mega_20260801_2347.txt` ·
ranking: `logs/backtest_acoes_v4_mega_ranking_20260801_2347.csv`.

O que a v3 deixou de fora e a v4 cobriu:
- sessões AM/MID/PM/POWER completas (NR4/7, OUT, EMA, SQZ, VWAPF, MT/TP)
- ORB45 / ORB late-early / ORB TP·RR · GAP G25/G35/G75 · GAP AM/TP/RR
- VWAPC MT/V14 · gap-aligned extras · M5/M30/H1 ampliados

Régua intacta: net ≥ +0,10 R · PF ≥ 1,25 · cons ≥ 55% · OOS+ · n ≥ 40.

| Ativo | testados v4 | 🟢 GO novos | 🟡 |
|---|---|---|---|
| VALE3 | 197 | **20** | 43 |
| WEGE3 | 197 | **5** | 22 |
| BBAS3 | 197 | **4** | 20 |
| PETR4 | 197 | **2** | 17 |
| ITUB4 | 197 | 0 | 1 |
| BBDC4 | 197 | 0 | 1 |
| ABEV3 | 197 | 0 | 0 |

**31 🟢 GO novos** wired em `services/acoes_go_paths.py` (magics **20260824–20260854**).
Total ações wired: **45** (14 v3 + 31 v4). Destaques:

| Setup | TF | Completo | OOS | Magic |
|---|---|---|---|---|
| **PETR4_OUT_DAY_H1** | H1 | +0,170 R · PF 1,58 · cons 55% · n=67 | +0,236 ✅ | 20260824 |
| **PETR4_VWAP_OPEN_AM** | M15 | +0,117 R · PF 1,25 · cons 58% · n=134 | +0,107 ✅ | 20260825 |
| **VALE3_ORB30_V15_TP20** | M15 | +0,274 R · PF 2,69 · cons 68% · n=71 | +0,263 ✅ | 20260826 |
| **VALE3_IMP_AM_M5** | M5 | +0,265 R · PF 1,83 · cons 62% · n=69 | +0,447 ✅ | 20260827 |
| **VALE3_OUT_AM** | M15 | +0,245 R · PF 1,57 · cons 56% · n=75 | +0,447 ✅ | 20260828 |
| **VALE3_IMP_DAY_MT_M5** | M5 | +0,237 R · PF 1,75 · cons 65% · n=102 | +0,532 ✅ | 20260829 |
| **VALE3_ORB45_V15** | M15 | +0,234 R · PF 2,30 · cons 64% · n=81 | +0,165 ✅ | 20260830 |
| **VALE3_VWAPC_AM_V14** | M15 | +0,186 R · PF 1,46 · cons 65% · n=359 | +0,367 ✅ | 20260833 |
| **BBAS3_ORB30_LATE** | M15 | +0,171 R · PF 1,93 · cons 60% · n=101 | +0,203 ✅ | 20260846 |
| **BBAS3_ORB45_V13** | M15 | +0,141 R · PF 1,70 · cons 67% · n=118 | +0,146 ✅ | 20260848 |
| **WEGE3_NR5_AM_MT** | M15 | +0,151 R · PF 1,33 · cons 65% · n=80 | +0,108 ✅ | 20260850 |
| **WEGE3_GAPC_G30_AM** | M15 | +0,149 R · PF 1,41 · cons 56% · n=94 | +0,226 ✅ | 20260851 |
| **WEGE3_GAPC_G30_MT_H1** | H1 | +0,103 R · PF 1,44 · cons 57% · n=94 | +0,128 ✅ | 20260854 |

(+ outros ORB/GAP refinamentos VALE/BBAS/WEGE — ver `acoes_go_paths.py`).

ITUB4/BBDC4/ABEV3: ainda **zero GO** (melhor ITUB4 perto na v3: INS_DAY_H1 🟡).
Runtime: registry v3+v4 · fill gated por `ACOES_AUTO_ON`. WIN/WDO intactos.

## 13d. Ações B3 V5 MEGA — foco fracos PETR/ITUB/ABEV/BBDC (02–03/08/2026)

Script: `backtest_acoes_b3_v5_mega.py` · **7276 setups NOVOS** (0 overlap v3+v4) ·
4 papers fracos · ~11,5h · workers=8 · custos % B3 + tick · EOD 16:54.
Log: `logs/backtest_acoes_v5_mega_20260802_0946.txt` ·
ranking: `logs/backtest_acoes_v5_mega_ranking_20260802_0946.csv`.

Famílias novas: ORB15/ORB60 · ORB retest/fail/PB · open-drive · GAP soft/trend/ATR ·
VWAP_OPEN expand · OUT V5 · INS/NR cons-boost · VOLSPIKE thresholds · IMP soft ·
PDM · lunch fade · sessões OPEN1H/LATEAM/LUNCH/AFT.

Régua intacta: net ≥ +0,10 R · PF ≥ 1,25 · cons ≥ 55% · OOS+ · n ≥ 40.

| Ativo | testados v5 | 🟢 GO novos | 🟡 |
|---|---|---|---|
| PETR4 | 1819 | **15** | 182 |
| BBDC4 | 1819 | **2** | 15 |
| ITUB4 | 1819 | 0 | 17 |
| ABEV3 | 1819 | 0 | 8 |

**17 🟢 GO novos** wired em `services/acoes_go_paths.py` (magics **20260856–20260872**).
Total ações wired: **62** (14 v3 + 31 v4 + 17 v5). Destaques:

| Setup | TF | Completo | OOS | Magic |
|---|---|---|---|---|
| **PETR4_ORB15_GAP_V14_LATE** | M15 | +0,283 R · PF 1,96 · cons 65% · n=77 | +0,086 ✅ | 20260856 |
| **PETR4_ORB15_GAP_V12_LATE** | M15 | +0,231 R · PF 1,73 · cons 65% · n=106 | +0,182 ✅ | 20260857 |
| **PETR4_GAPC_G90_LATE_SOFT** | M15 | +0,158 R · PF 1,38 · cons 60% · n=83 | +0,268 ✅ | 20260858 |
| **PETR4_ORB15_V12_LATE** | M15 | +0,151 R · PF 1,44 · cons 59% · n=151 | +0,195 ✅ | 20260859 |
| **PETR4_OUT_DAY_V5_LOATR_H1** | H1 | +0,144 R · PF 1,40 · cons 63% · n=65 | +0,109 ✅ | 20260860 |
| **BBDC4_VSPIKE_T20_DAY_MT** | M15 | +0,142 R · PF 1,56 · cons 56% · n=81 | +0,203 ✅ | 20260862 |
| **PETR4_OUT_AM_V5_TP15_H1** | H1 | +0,141 R · PF 1,47 · cons 59% · n=65 | +0,212 ✅ | 20260863 |
| **BBDC4_VSPIKE_T22_DAY_MT** | M15 | +0,129 R · PF 1,52 · cons 55% · n=64 | +0,198 ✅ | 20260864 |
| **PETR4_GAPC_G40_SOFT** | M15 | +0,119 R · PF 1,29 · cons 58% · n=239 | +0,130 ✅ | 20260867 |

(+ outros GAPC soft / OUT AM H1 / ORB15 LATE — ver `acoes_go_paths.py`).

ITUB4/ABEV3: ainda **zero GO** (melhores 🟡: ITUB4_NR4_AM_V13_M30 · ABEV3_PDM_V15_H1).
BBDC4: **primeiro GO** do paper (VOLSPIKE MT).
Runtime: registry v3+v4+v5 · fill gated por `ACOES_AUTO_ON` (default OFF). WIN/WDO intactos.

## 13e. Ações B3 V6 MEGA — foco zeros ITUB/ABEV (+BBDC) (02–03/08/2026)

Script: `backtest_acoes_b3_v6_mega.py` · **4767 setups NOVOS** (0 overlap v3+v4+v5) ·
3 papers · ~6,1h · workers=8 · custos % B3 + tick · EOD 16:54.
Log: `logs/backtest_acoes_v6_mega_20260802_2337.txt` ·
ranking: `logs/backtest_acoes_v6_mega_ranking_20260802_2337.csv`.

Hipótese: near-misses v5 falharam por OOS n<20 (amostra total <~67).
v6 amplifica n com vol soft (0,85–1,15), janelas mais largas e TFs M15/M30/H1.

Régua intacta: net ≥ +0,10 R · PF ≥ 1,25 · cons ≥ 55% · OOS+ · n ≥ 40.

| Ativo | testados v6 | 🟢 GO novos | 🟡 |
|---|---|---|---|
| BBDC4 | 1589 | **2** | 13 |
| ITUB4 | 1589 | 0 | 36 |
| ABEV3 | 1589 | 0 | 22 |

**2 🟢 GO novos** wired em `services/acoes_go_paths.py` (magics **20260873–20260874**).
Total ações wired: **64** (14 v3 + 31 v4 + 17 v5 + 2 v6):

| Setup | TF | Completo | OOS | Magic |
|---|---|---|---|---|
| **BBDC4_VSPIKE_T24_A12_DAY_V6_MT** | M15 | +0,119 R · PF 1,39 · cons 56% · n=81 | +0,228 ✅ | 20260873 |
| **BBDC4_VSPIKE_T20_A14_DAY_V6_MT** | M15 | +0,108 R · PF 1,37 · cons 57% · n=90 | +0,096 ✅ | 20260874 |

ITUB4/ABEV3: ainda **zero GO** (melhores 🟡: ITUB4_NR5_AM_V10_V6_RR18_M30 · ABEV3_EMA_OPEN1H_V10_V6_MT_M15).
Runtime: registry v3+v4+v5+v6 · fill gated por `ACOES_AUTO_ON`. WIN/WDO intactos.

## 14. V6 atoms — dissecção do score Monitor MT5 (24/07/2026)

Script: `backtest_v6_atoms.py`. WIN$D + WDO$D · M15 · ~6,6 anos · ADX≥23 + HTF ·
SL 1,2×ATR · TP 1,5R. Log: `logs/backtest_v6_atoms_20260724_1446.txt`.

| Setup | WIN | WDO | Status |
|---|---|---|---|
| V6_EMA_CROSS | −0,226 R · PF 0,65 | +0,066 R · PF 1,13 | 🔴 |
| V6_MACD_CROSS | −0,075 R | −0,041 R | 🔴 |
| V6_RSI_EXT | +0,005 R | −0,015 R | 🔴 |
| V6_BB_FADE | −0,013 R | −0,009 R | 🔴 |
| V6_VOL_SPIKE | −0,032 R | −0,019 R | 🔴 |
| V6_CANDLE | −0,007 R | −0,049 R | 🔴 |
| V6_EMA200_RECLAIM | +0,006 R | **+0,105 R · PF 1,22 · cons 56%** (OOS 0,041) | WIN 🔴 · WDO 🟡 OOS fraco |
| V6_VWAP_RECLAIM | −0,094 R | −0,001 R | 🔴 |
| V6_FULL_SCORE (controle) | −0,014 R · cons 40% | −0,026 R · cons 37% | 🔴 |

**Resultado: ZERO 🟢 GO.** Nenhum átomo reutilizável no V7/WinGo nesta rodada.
Melhor ponto: WDO `V6_EMA200_RECLAIM` 🟡 (net passa, PF/OOS não fecham a régua).
Produção Monitor MT5 / V7 **não alterada**.

## 15. BTC weekend mega v12 (24/07/2026)

Script: `backtest_setups_novos_v12_btc_wknd.py` · 33 hipóteses · BTCUSD M15 (~1,4a IC).
Log: `logs/backtest_setups_novos_v12_btc_wknd_20260724_1540.txt`.

**8 🟢 GO integrados no Monitor Crypto** (além dos 4 já live: INS_1015, INS_1015_V13,
INS_0918_V13, NR5_1016):

| Setup | Completo | OOS | Status |
|---|---|---|---|
| **BTC_INS_1218_V13** | +0,443 R · PF 1,95 · cons 61% · n=100 | +0,172 · PF 1,29 ✅ | ✅ EM USO |
| **BTC_INS_1117_V13** | +0,412 R · PF 1,86 · cons 83% · n=102 | +0,169 · PF 1,29 ✅ | ✅ EM USO |
| **BTC_NR5_0915** | +0,365 R · PF 1,66 · cons 61% · n=71 | +0,226 · PF 1,34 ✅ | ✅ EM USO |
| **BTC_INS_1017_V13** | +0,314 R · PF 1,60 · cons 78% · n=114 | +0,112 · PF 1,18 ✅ | ✅ EM USO |
| **BTC_INS_V135_0916** | +0,309 R · PF 1,58 · cons 83% · n=79 | +0,382 · PF 1,73 ✅ | ✅ EM USO |
| **BTC_INS_0816_V13** | +0,273 R · PF 1,51 · cons 83% · n=103 | +0,325 ✅ | ✅ EM USO |
| **BTC_NR4_1016** | +0,220 R · PF 1,38 · cons 67% · n=107 | +0,159 ✅ | ✅ EM USO |
| **BTC_H4_PB_EMA** | +0,154 R · PF 1,28 · cons 83% · n=1024 | +0,339 · PF 1,69 ✅ | ✅ EM USO |

13 🟡 (NR5 NY/1116, INS_V14/V15, NR7, IMP_*, US_OPEN, …) — observação, sem live.
Reiniciar Crypto `:5001` para carregar os novos paths.

## 16. BTC/ETH 24h / overnight / Asia v13 (26/07/2026)

Script: `backtest_setups_novos_v13_btc_24h.py` · 32 hipóteses · BTCUSD+ETHUSD M15 (~1,4a IC).
Log: `logs/backtest_setups_novos_v13_btc_24h_20260726_2011.txt`.

**SKIP:** ORDER_FLOW BTC — já 🔴 (OOS caiu, §3); sem params/dados novos.

**4 🟢 GO** (2 integrados no live; 2 redundantes com o 24h principal):

| Setup | Completo | OOS | Status |
|---|---|---|---|
| **BTC_INS_24H_V13** | +0,181 R · PF 1,33 · cons 78% · n=266 | +0,118 · PF 1,20 ✅ | ✅ **EM USO** (cascade após diurnos) |
| **ETH_IMP_CONT_24H** | +0,230 R · PF 1,68 · cons 76% · n=67 | +0,328 · PF 2,20 ✅ | ✅ **EM USO** |
| BTC_INS_24H_V14 | +0,163 R · PF 1,29 · cons 61% · n=174 | +0,305 · PF 1,57 ✅ | 🟢 GO · **não wired** (subset vol≥1,4× do V13) |
| BTC_INS_24H_MT | +0,227 R · PF 1,43 · cons 67% · n=145 | +0,114 · PF 1,20 ✅ | 🟢 GO · **não wired** (V13 + seg–qui; V13 já cobre) |

**Top 🟡 near-miss** (sem live):

| Setup | Completo | Por que não GO |
|---|---|---|
| BTC_IMP_ASIA_0008 | +0,435 R · PF 2,40 · n=46 | OOS fraco (régua OOS) |
| BTC_IMP_OVN_1808 | +0,402 R · PF 2,36 · n=41 | OOS fraco |
| BTC_NR4_24H | +0,138 R · PF 1,24 · n=357 | PF &lt; 1,25 |
| BTC_INS_24H_V135 | +0,138 R · PF 1,24 · n=212 | PF &lt; 1,25 |
| BTC_H4_PB_EMA_24H | +0,099 R · PF 1,17 · n=1998 | net &lt; 0,10 / PF fraco |

Asia-only (00–08 / 22–06) e overnight INS/NR **não** passaram — edge 24h veio do inside full-session, não do filtro Asia.
Score legado BTC permanece OFF.
Reiniciar Crypto `:5001` para carregar `BTC_INS_24H_V13` / `ETH_IMP_CONT_24H`.

## 17. Mega-sweep gaps crypto (26/07/2026)

Script: `backtest_mega_sweep_gaps.py` · 89 variantes · 39 células abertas · crypto M15 (~1,4a IC).
Log: `logs/backtest_mega_sweep_gaps_20260726_2118.txt`.

**6 🟢 GO integrados no Monitor Crypto:**

| Setup | Completo | OOS | Status |
|---|---|---|---|
| **XAU_NR4_1016_H4** | +0,224 R · PF 1,39 · cons 60% · n=77 | +0,500 ✅ | ✅ **EM USO** |
| **XAU_PDH_H4** | +0,205 R · PF 1,39 · cons 65% · n=158 | +0,329 ✅ | ✅ **EM USO** |
| **EUR_PDH_H4** | +0,191 R · PF 1,41 · cons 72% · n=159 | +0,178 ✅ | ✅ **EM USO** |
| **BTC_PDH_H4** | +0,181 R · PF 1,34 · cons 67% · n=95 | +0,352 ✅ | ✅ **EM USO** |
| **XAU_PDH_1115** | +0,175 R · PF 1,32 · cons 60% · n=77 | +0,234 ✅ | ✅ **EM USO** |
| **XAU_HL_24H** | +0,170 R · PF 1,31 · cons 85% · n=927 | +0,165 ✅ | ✅ **EM USO** |

**🟡 amarelos** (sem live — OOS fraco / cons baixa): GBP_NR5_0915_H4, XAU_HL_1014, BTC_OUT_1015, BTC_IMP_CONT_24H, BTC_OUT_24H, XAU_NR5_1016_H4, GBP_NR5_1016_H4, EUR_INS_0918_V13, BTC_HL_24H, BTC_PDH_1115, GBP_HL_1014, XAU_H4_PB_EMA_24H, XAU_OUT_24H, EUR_IMP_CONT_20, ETH_LH_047, GBP_NR4_1016_H4, EUR_IMP_CONT_24H.

Próximo: discovery candidates COM CUSTOS + mega-sweep B3 (WIN/WDO).
Reiniciar Crypto `:5001` para carregar os 6 paths mega.

## 18. Mega-sweep gaps B3 (26/07/2026)

Script: `backtest_mega_sweep_gaps.py` · 11 células abertas · WIN$D / WDO$D M15 (~1,3a).
Log: `logs/backtest_mega_sweep_gaps_20260726_2156.txt`.

**5 🟢 GO → 4 paths WinGo live** (`WIN_IMP_CONT_20` + `_24H` = mesma amostra no pregão → 1 path `WIN_IMP_CONT`):

| Setup | Completo | OOS | Status |
|---|---|---|---|
| **WIN_PDH_1115** | +0,326 R · PF 1,84 · cons 59% · n=139 | +0,563 ✅ | ✅ **EM USO** · magic 20260740 |
| **WIN_IMP_CONT** | +0,164 R · PF 1,48 · cons 65% · n=227 | +0,250 ✅ | ✅ **EM USO** · magic 20260741 · (`_20`; `_24H` duplicata) |
| **WDO_HL_1014** | +0,132 R · PF 1,28 · cons 68% · n=490 | +0,205 ✅ | ✅ **EM USO** · magic 20260742 |
| **WIN_PDH_H4** | +0,122 R · PF 1,27 · cons 58% · n=372 | +0,357 ✅ | ✅ **EM USO** · magic 20260743 |

**🟡 amarelos** (sem live): WDO_OUT_1015, WDO_IMP_CONT_20, WDO_IMP_CONT_24H.

Reiniciar app B3/V7 para carregar os 4 paths mega.

## 19. Discovery candidates GO crypto (26/07/2026)

Script: `backtest_discovery_candidates_go.py` · JSON `edge_discovery/out/candidatos_v2_crypto_20260726_2104.json` · top 40.
Log: `logs/followup_discovery_then_b3_20260726_2155.txt`.
Live: engine generico `services/crypto_discovery_paths.py` + specs `data/crypto_discovery_go_paths.json` (thresholds IS congelados).

**10 GO integrados (sem dedupe — condicoes distintas):**

| Setup | TF | Side | Completo | OOS | Status |
|---|---|---|---|---|---|
| **XAUUSD_L_35** | M15 | COMPRA | +0,496 R · PF 1,98 · cons 77% · n=155 | +0,478 | EM USO |
| **XAUUSD_L_07** | M15 | COMPRA | +0,449 R · PF 1,85 · cons 78% · n=136 | +0,641 | EM USO |
| **XAUUSD_L_14** | M15 | COMPRA | +0,421 R · PF 1,79 · cons 81% · n=104 | +0,395 | EM USO |
| **XAUUSD_L_31** | M15 | COMPRA | +0,385 R · PF 1,70 · cons 68% · n=170 | +0,477 | EM USO |
| **XAUUSD_L_04** | M15 | COMPRA | +0,340 R · PF 1,61 · cons 73% · n=168 | +0,278 | EM USO |
| **XAUUSD_L_02** | M15 | COMPRA | +0,332 R · PF 1,58 · cons 68% · n=154 | +0,260 | EM USO |
| **XAUUSD_S_40** | M15 | VENDA | +0,232 R · PF 1,38 · cons 73% · n=132 | +0,399 | EM USO |
| **ETHUSD_S_36** | M60 | VENDA | +0,188 R · PF 1,30 · cons 56% · n=243 | +0,131 | EM USO |
| **BTCUSD_L_30** | M15 | COMPRA | +0,183 R · PF 1,29 · cons 61% · n=126 | +0,149 | EM USO |
| **BTCUSD_L_33** | M15 | COMPRA | +0,175 R · PF 1,28 · cons 71% · n=174 | +0,159 | EM USO |

**Amarelos** (sem live): BTCUSD_L_17, BTCUSD_L_25, BTCUSD_L_28, BTCUSD_L_34.

Params live = harness: SL 1xATR · TP 2xATR · quintis IS-only. Barra GO nao abaixada.
Reiniciar Crypto `:5001` para carregar os 10 paths discovery.

## 20. Mega-sweep yellows refine (27/07/2026)

Script: `backtest_mega_sweep_yellows.py` · 122 variantes crypto + 26 B3.
Logs: `logs/backtest_mega_sweep_yellows_crypto_20260727_0047.txt`, `logs/backtest_mega_sweep_yellows_b3_20260727_0536.txt`.

| Setup | Completo | OOS | Status |
|---|---|---|---|
| **EUR_IMP_CONT_24H_MT** | +0,124 R · PF 1,29 · cons 58% · n=109 | +0,094 ✅ | ✅ **EM USO** crypto |
| **WDO_OUT_1015_MT** | +0,347 R · PF 1,84 · cons 61% · n=82 | +0,283 ✅ | ✅ **EM USO** WinGo · magic 20260744 |

Demais bases 🟡 sem GO novo (ainda amarelo ou 🔴). WDO_IMP_CONT continua 🟡.

## 21. Edge Discovery B3 v2 + harness GO (27/07/2026)

Discovery: `rodar_discovery_tudo.py --v2 --grupos b3 --tfs 15,60` → **144 candidatos** (`edge_discovery/out/candidatos_v2_b3_20260727_0047.json`).
Harness: `backtest_discovery_candidates_go.py --top 40` · log `logs/discovery_candidates_go_20260727_0536.txt`.
Live WIN/WDO: `services/b3_discovery_paths.py` + `data/b3_discovery_go_paths.json`.

**14 GO harness → 8 WinGo live (WIN/WDO) + 6 VALE3 inventário (acoes sem auto):**

| Setup | Completo | OOS | Status |
|---|---|---|---|
| **WIND_S_01** | +0,152 R · PF 1,25 · cons 58% | +0,239 | EM USO · 20260750 |
| **WIND_S_08** | +0,390 R · PF 1,75 · cons 61% | +0,167 | EM USO · 20260751 |
| **WIND_L_11** | +0,207 R · PF 1,40 · cons 65% | +0,177 | EM USO · 20260752 |
| **WIND_S_12** | +0,308 R · PF 1,55 · cons 68% | +0,174 | EM USO · 20260753 |
| **WIND_S_33** | +0,198 R · PF 1,33 · cons 60% | +0,171 | EM USO · 20260754 |
| **WDOD_S_18** | +0,190 R · PF 1,32 · cons 56% | +0,263 | EM USO · 20260755 |
| **WDOD_S_25** | +0,372 R · PF 1,71 · cons 56% | +0,647 | EM USO · 20260756 |
| **WDOD_S_34** | +0,238 R · PF 1,41 · cons 59% | +0,649 | EM USO · 20260757 |
| VALE3_L_14/L_17/S_22/S_31/S_32/S_35 | GO harness | — | inventário only (acoes scaffold) |

Reiniciar Crypto `:5001` + app B3/V7.

## 22. BTC/ETH FDS · noite · Asia · refine v14 (31/07/2026)

Script: `backtest_setups_novos_v14_btc_eth.py` · **110 hipóteses** · BTCUSD+ETHUSD M15 (~1,4a IC).
Log: `logs/backtest_setups_novos_v14_btc_eth_20260731_2226.txt` · ranking CSV no mesmo stamp.

Régua mantida: net ≥ +0,10 R · PF ≥ 1,25 · cons ≥ 55% · OOS segura · n ≥ 40.

**13 🟢 GO — todos wired no Monitor Crypto:**

| Setup | Completo | OOS | Status |
|---|---|---|---|
| **BTC_NR5_1622** | +0,273 R · PF 1,56 · cons 59% · n=65 | +0,242 ✅ | ✅ **EM USO** |
| **ETH_IMP_CONT_24H_V18** | +0,260 R · PF 1,80 · cons 82% · n=72 | +0,090 ✅ | ✅ **EM USO** |
| **ETH_INS_24H_MT** | +0,237 R · PF 1,42 · cons 82% · n=135 | +0,404 ✅ | ✅ **EM USO** |
| **BTC_PDH_ASIA_0008** | +0,213 R · PF 1,43 · cons 78% · n=138 | +0,172 ✅ | ✅ **EM USO** |
| **ETH_IMP_TP25_24H** | +0,201 R · PF 1,58 · cons 76% · n=68 | +0,123 ✅ | ✅ **EM USO** |
| **BTC_NR5_24H_V14** | +0,197 R · PF 1,37 · cons 72% · n=167 | +0,137 ✅ | ✅ **EM USO** |
| **ETH_HL_24H_MT** | +0,197 R · PF 1,35 · cons 82% · n=389 | +0,092 ✅ | ✅ **EM USO** |
| **BTC_PDH_24H** | +0,192 R · PF 1,37 · cons 78% · n=342 | +0,244 ✅ | ✅ **EM USO** |
| **BTC_NR4_24H_V14** | +0,190 R · PF 1,35 · cons 72% · n=222 | +0,314 ✅ | ✅ **EM USO** |
| **BTC_PDH_NIGHT_2010** | +0,172 R · PF 1,33 · cons 78% · n=193 | +0,226 ✅ | ✅ **EM USO** |
| **BTC_PDH_24H_V13** | +0,168 R · PF 1,32 · cons 78% · n=283 | +0,165 ✅ | ✅ **EM USO** |
| **BTC_PDH_24H_MT** | +0,155 R · PF 1,29 · cons 65% · n=229 | +0,155 ✅ | ✅ **EM USO** |
| **BTC_H4_PB_EMA_MT24** | +0,139 R · PF 1,25 · cons 83% · n=1203 | +0,115 ✅ | ✅ **EM USO** |

**Cobertura ganha:** BTC ganha PDH/NR 24h + noite/Asia + late 16–22; ETH ganha inside/HL 24h (MT) + impulso V18/TP2.5 — além do `ETH_IMP_CONT_24H` v13.
Weekend-only (`BTC_WE_*` / `ETH_WE_*`) ficou 🟡/🔴 (amostra ou OOS).
Paths MT cobrem overnight de semana; FDS puro continua via paths 24h sem filtro MT (`BTC_PDH_24H`, `BTC_NR*_24H_V14`, impulsos ETH 24h).

**Não reiniciar `:5001` com posição aberta** — só disco até flat.

## 23. BTC/ETH famílias novas v20 (03/08/2026)

Script: `backtest_setups_novos_v20_btc_eth.py` · **115 hipóteses** · BTC+ETH M15/M60 (~1,4a / ~5,7a IC).
Log: `logs/backtest_setups_novos_v20_btc_eth_20260803_2018.txt`.

Famílias novas (não reteste de INS/NR/HL/PDH/IMP/VWAP-cont clássicos): momo3 · Donchian · BB fade · RSI reclaim · false-break · pin · M60.

**3 🟢 GO — wired:**

| Setup | Completo | OOS | Status |
|---|---|---|---|
| **BTC_RSI_RECL_M60** | +0,200 R · PF 1,38 · cons 56% · n=115 | +0,353 ✅ | ✅ **EM USO** · M60 |
| **ETH_RSI_RECL_M60** | +0,191 R · PF 1,37 · cons 65% · n=125 | +0,174 ✅ | ✅ **EM USO** · M60 |
| **BTC_BBFADE_NY** | +0,175 R · PF 1,34 · cons 65% · n=168 | +0,152 ✅ | ✅ **EM USO** · 13–17h |

🟡 near-miss (sem live): ETH_MOMO3_NY, BTC_DON20_LON, BTC_MOMO4_24H, BTC_BBFADE_24H_M60, BTC_MOMO3_LON/NY/MT…
False-break / pin / BB fade 24h / WE momo → 🔴.
**Giveback MANAGER (todos os símbolos crypto):** pico MFE em R$ — <400 → 50%; 400–999 → 33%; ≥1000 → teto R$250 do pico (`allowed_giveback` em `crypto_position_manager`). Arma com ≥0,6R **ou** peak_brl≥R$200 (SL gigante não silencia). Avalia underwater. Peak persiste em `data/crypto_pos_peaks.json` + seed MFE via candles M5.
**Caps live SL/TP (03/08/2026):** BTC 1000/1200 · ETH 12/20 · EUR/GBP 30/45 pips · XAU TP estrutural 30 — só fill/manage; backtest intacto. Justificativa BTC: trade SL~1191/TP~1827 (~8–13×ATR M15≈143) com MFE real ~380 pts.

## 22. Mega WIN/WDO v19 (01–02/08/2026)

Script: `backtest_setups_novos_v19_win_wdo_mega.py` · **540 setups** (WIN 270 + WDO 270) · TFs M15/M30/H1 · ~6,6a.
Log: `logs/backtest_setups_novos_v19_win_wdo_mega_20260802_0104.txt` · ranking CSV homônimo.
Régua: net≥+0,10 R · PF≥1,25 · cons≥55% · OOS · n≥40 (**não baixada**).

**Resultado:** WIN 🟢80 / 🟡84 · WDO 🟢49 / 🟡48 · total 129 GO (muitos refinamentos de famílias já live).

**11 paths novos wired no WinGo** (não cortam GOs existentes; magics livres 20260745–49 + 20260758+):

| Setup | Completo | OOS | Magic | Nota |
|---|---|---|---|---|
| **WDO_OUT_GAP_DAY** | +0,414 R · PF 2,10 · cons 66% · n=65 | +0,833 ✅ | 20260745 | melhor net WDO v19 |
| **WIN_NR5_1115** | +0,428 R · PF 2,14 · cons 63% · n=87 | +0,220 ✅ | 20260746 | melhor net WIN v19 |
| **WIN_NR4_1115** | +0,372 R · PF 1,98 · cons 66% · n=112 | +0,165 ✅ | 20260747 | NR4 WIN novo |
| **WDO_OUT_POWER** | +0,343 R · PF 1,83 · cons 62% · n=74 | +0,488 ✅ | 20260748 | outside tarde |
| **WIN_INS_PM** | +0,340 R · PF 1,79 · cons 59% · n=69 | +0,232 ✅ | 20260749 | inside PM |
| **WDO_IMP_1014_MT** | +0,247 R · PF 1,95 · cons 62% · n=73 | +0,122 ✅ | 20260758 | preenche célula IMP WDO |
| **WIN_VOLSPIKE_AM** | +0,273 R · PF 1,81 · cons 67% · n=102 | +0,319 ✅ | 20260759 | família nova |
| **WDO_ENG_1014** | +0,206 R · PF 1,45 · cons 63% · n=172 | +0,345 ✅ | 20260760 | engolfo |
| **WDO_GAPC_A60** | +0,212 R · PF 1,53 · cons 57% · n=192 | +0,213 ✅ | 20260761 | gap cont ATR |
| **WIN_IB_BRK_V15** | +0,159 R · PF 1,41 · cons 80% · n=472 | +0,180 ✅ | 20260762 | IB break |
| **WIN_HL_MID** | +0,286 R · PF 1,83 · cons 69% · n=131 | +0,096 ✅ | 20260763 | HL mid |

**Não wired** (overlap com live ou TF sem runtime M15): dezenas de NR/INS/PDH/IMP DAY/1015 equivalentes a paths já WinGo; `WDO_OUT_1015_MT` já live; `WIN_OUT_DAY_H1` (só H1 GO — WinGo é M15).
**M5** ficou fora desta rodada (custo compute); famílias M5 no script via `--tfs 5`.
Crypto `:5001` **não tocado**.
