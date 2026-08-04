# Motor v7.1 — Como rodar (pasta nova C:\Projetos\trader_analizer)

Tudo aqui é **paralelo**: Monitor MT5 (v6) e Monitor Crypto intocados.
Nenhum destes scripts envia ordem ao MT5.

## O que mudou na v7.1 (calibragem com o 1º backtest real de 17/07/2026)

Resultados que motivaram a calibragem (WINQ26 fev-jul + WDOQ26 abr-jul, com custos):

| Item | Resultado | Ação |
|---|---|---|
| v6 + exits geridos (WIN) | **+0,131R líq, PF 1,39, 80% consistência — PASSA no GO** | Candidato a levar os exits geridos à produção |
| v6 + exits geridos (WDO) | +0,019R líq (custo 0,306R comeu o edge) | Confirma parecer: WDO precisa entrada limite/stops maiores |
| v7 ORB | WIN +0,056R (n=15) · WDO **+0,550R, PF 5,0** (n=8) | Mantido |
| v7 PULLBACK_VWAP | WIN -0,197R · WDO -1,178R | **Desligado** (`SETUP_PULLBACK_ENABLED=False`) — precisa redesenho (esperar rejeição do nível, não comprar a queda) |

⚠️ Amostras pequenas (5 meses, contrato único). Antes de qualquer decisão
final, rode com histórico longo:

```bash
python backtest_pro.py --list          # mostra símbolos WIN*/WDO* e quantos candles cada um tem
python backtest_pro.py --symbol WIN$N  # exemplo: contrato contínuo (se a Santander oferecer)
```

## Comandos

```bash
python backtest_pro.py                      # WIN e WDO do .env, v6 vs v7.1
python backtest_pro.py --engine v6 --exits fixed   # produção EXATA + custos (baseline)
python backtest_pro.py --ticks              # v7 + gatilho de fluxo histórico (lento)
python -m services.shadow_monitor           # shadow em pregão → logs/shadow_v7_trades.csv
```

Comparação que importa agora: `--engine v6 --exits fixed` (produção real) vs
`--engine v6 --exits managed`. Se managed vencer com folga também no histórico
longo, os exits geridos são o próximo upgrade de produção do Monitor MT5 —
mudança pequena e de alto impacto (o motor de sinal continua o mesmo).

## Reaplicado após a migração de pasta (tinha se perdido)

A migração OneDrive → C:\Projetos perdeu os ajustes v7 do Scalper aprovados
anteriormente. Foram reaplicados, agora **restritos a ativos B3** (BITN26/WIN/WDO)
para não alterar nada do stack crypto (BTCUSD/forex seguem com comportamento
original):

- Spread guard (BITN26: spread > 2 ticks → não entra)
- SL ancorado na cotação oposta (tolerância real de ruído) + TP 6/SL 3 no BITN26
- Componentes de score: alinhamento fluxo 30s + penalidade de sessão + teto de exaustão 88 (BITN26)
- Bloqueio de abertura 30 min no BITN26
- Logger: breakdown do score corrigido (gravava zeros) + coluna spread_ticks + rotação do CSV legado
- Dedupe do register-close (linhas duplicadas corrompiam stats/supervisor)

## Custos (editar `services/trading_costs.py` se necessário)

- WIN: R$0,50/contrato r/t (nota Santander) + 1 tick por perna a mercado
- WDO: **R$1,15 estimado** — confirmar com nota de WDO
- Entrada LIMITE não paga spread; stop sempre paga
