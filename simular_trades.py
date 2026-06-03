"""
simular_trades.py — Simulador de 500 trades usando o motor real de análise técnica (v5).

Novidades v5:
  - 500 cenários (5x mais)
  - Gera htf_df (1h) para ativar o filtro direcional EMA200 + HTF
  - Rastreia trades bloqueados pelo filtro direcional
  - R:R melhorado (stop 1.2x / TP1 2.0x ATR)

Execute: python simular_trades.py
"""
import sys
import random
import numpy as np
import pandas as pd
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from services.technical_analysis import generate_signal

random.seed(42)
np.random.seed(42)

# ── Parâmetros WIN mini ────────────────────────────────────────────────────
TICK_SIZE   = 5       # WIN mini: múltiplo de 5 pts
PTS_POR_BRL = 0.20   # R$ por ponto por contrato
VOLUME      = 1       # 1 contrato

# ── Definição dos cenários ─────────────────────────────────────────────────
SCENARIOS = [
    # (nome, n_repets, params_15m, params_1h)
    # params_1h define a tendência macro (HTF) — pode alinhar ou divergir do 15m
    ("Alta forte alinhada",        30, dict(trend=+1.0, vol=0.6, noise=0.3),  dict(trend=+1.0, vol=0.4, noise=0.2)),
    ("Alta moderada alinhada",     25, dict(trend=+0.4, vol=0.5, noise=0.5),  dict(trend=+0.5, vol=0.3, noise=0.3)),
    ("Alta c/ macro baixista",     20, dict(trend=+0.5, vol=0.6, noise=0.4),  dict(trend=-0.6, vol=0.4, noise=0.3)),
    ("Baixa forte alinhada",       30, dict(trend=-1.0, vol=0.6, noise=0.3),  dict(trend=-1.0, vol=0.4, noise=0.2)),
    ("Baixa moderada alinhada",    25, dict(trend=-0.4, vol=0.5, noise=0.5),  dict(trend=-0.5, vol=0.3, noise=0.3)),
    ("Baixa c/ macro altista",     20, dict(trend=-0.5, vol=0.6, noise=0.4),  dict(trend=+0.6, vol=0.4, noise=0.3)),
    ("Lateralização",              20, dict(trend=0,    vol=0.2, noise=0.9),  dict(trend=0,    vol=0.2, noise=0.8)),
    ("Alta volatilidade",          20, dict(trend=+0.1, vol=1.5, noise=0.6),  dict(trend=+0.2, vol=1.0, noise=0.5)),
    ("Reversão de alta alinhada",  15, dict(trend=-0.3, vol=0.8, noise=0.5, reversal=True, rev_dir=+1),
                                       dict(trend=+0.2, vol=0.4, noise=0.3)),
    ("Reversão de baixa alinhada", 15, dict(trend=+0.3, vol=0.8, noise=0.5, reversal=True, rev_dir=-1),
                                       dict(trend=-0.2, vol=0.4, noise=0.3)),
    ("Reversão contra macro",      15, dict(trend=-0.3, vol=0.8, noise=0.5, reversal=True, rev_dir=+1),
                                       dict(trend=-0.5, vol=0.4, noise=0.3)),
    ("Spike e recuperação",        15, dict(trend=+0.2, vol=1.2, noise=0.4, spike=True),
                                       dict(trend=+0.3, vol=0.6, noise=0.3)),
    ("Breakout de alta",           15, dict(trend=+0.8, vol=0.4, noise=0.2, breakout=True, bo_dir=+1),
                                       dict(trend=+0.7, vol=0.3, noise=0.2)),
    ("Breakout de baixa",          15, dict(trend=-0.8, vol=0.4, noise=0.2, breakout=True, bo_dir=-1),
                                       dict(trend=-0.7, vol=0.3, noise=0.2)),
    ("Consolidação pré-rompimento",15, dict(trend=+0.1, vol=0.3, noise=0.7),  dict(trend=+0.4, vol=0.3, noise=0.4)),
    ("Contra-tendência forte",     15, dict(trend=+1.0, vol=0.6, noise=0.2),  dict(trend=-1.0, vol=0.4, noise=0.2)),
]

total_target = sum(n for _, n, _, _ in SCENARIOS)

# ── Gerador de candles sintéticos ─────────────────────────────────────────
def gen_candles(n: int, base: float, trend: float, vol: float, noise: float,
                reversal: bool = False, rev_dir: int = 1,
                spike: bool = False, breakout: bool = False,
                bo_dir: int = 1,
                freq: str = "15min") -> pd.DataFrame:
    closes = [base]
    atr_base = base * 0.002 * vol

    for i in range(1, n):
        drift = trend * atr_base * 0.15
        noise_val = np.random.normal(0, atr_base * noise * 0.5)

        if reversal and i > n * 0.55:
            drift = rev_dir * atr_base * 0.25
        if spike and i == n // 2:
            noise_val = atr_base * 2.5 * np.random.choice([-1, 1])
        if breakout and i > n * 0.7:
            drift *= 3.0

        new_close = closes[-1] + drift + noise_val
        closes.append(max(new_close, base * 0.95))

    rows = []
    for i, c in enumerate(closes):
        o = closes[i-1] if i > 0 else c
        candle_range = abs(c - o) + atr_base * 0.4 * abs(np.random.randn())
        h = max(o, c) + candle_range * abs(np.random.uniform(0.1, 0.5))
        l = min(o, c) - candle_range * abs(np.random.uniform(0.1, 0.5))
        vol_candle = int(np.random.uniform(500, 3000) * (1 + abs(c - o) / max(atr_base, 1)))
        rows.append({"Open": round(o), "High": round(h), "Low": round(l),
                     "Close": round(c), "Volume": vol_candle})

    df = pd.DataFrame(rows)
    for col in ["Open", "High", "Low", "Close"]:
        df[col] = (df[col] / TICK_SIZE).round() * TICK_SIZE
        df[col] = df[col].clip(lower=TICK_SIZE)

    df.index = pd.date_range("2026-06-02 09:00", periods=n, freq=freq)
    return df


def simulate_outcome(signal: dict, n_future: int = 40) -> dict:
    acao  = signal.get("acao")
    entry = signal.get("entrada")
    sl    = signal.get("stop")
    tp1   = signal.get("tp1")

    if not all([acao in ("COMPRA", "VENDA"), entry, sl, tp1]):
        return {"outcome": "SEM_SINAL", "pnl_pts": 0, "pnl_brl": 0, "candles_ate_saida": 0}

    score     = signal.get("score", 0)
    acerto_prob = min(0.45 + (abs(score) / 20.0), 0.85)
    trend_dir = +1 if acao == "COMPRA" else -1
    acertou   = np.random.random() < acerto_prob

    base    = entry
    atr_est = abs(tp1 - entry) / 2.0  # v5: TP1 é 2.0x ATR
    trend   = (trend_dir if acertou else -trend_dir) * 0.4
    vol     = atr_est / (base * 0.002) if base else 1.0

    for i in range(n_future):
        drift     = trend * atr_est * 0.15
        noise_val = np.random.normal(0, atr_est * 0.5)
        new_price = base + drift + noise_val

        if acao == "COMPRA" and new_price >= tp1:
            pnl = round(tp1 - entry)
            return {"outcome": "TP1", "pnl_pts": pnl,
                    "pnl_brl": round(pnl * PTS_POR_BRL * VOLUME, 2),
                    "candles_ate_saida": i + 1}
        elif acao == "VENDA" and new_price <= tp1:
            pnl = round(entry - tp1)
            return {"outcome": "TP1", "pnl_pts": pnl,
                    "pnl_brl": round(pnl * PTS_POR_BRL * VOLUME, 2),
                    "candles_ate_saida": i + 1}

        if acao == "COMPRA" and new_price <= sl:
            pnl = round(sl - entry)
            return {"outcome": "STOP", "pnl_pts": pnl,
                    "pnl_brl": round(pnl * PTS_POR_BRL * VOLUME, 2),
                    "candles_ate_saida": i + 1}
        elif acao == "VENDA" and new_price >= sl:
            pnl = round(entry - sl)
            return {"outcome": "STOP", "pnl_pts": pnl,
                    "pnl_brl": round(pnl * PTS_POR_BRL * VOLUME, 2),
                    "candles_ate_saida": i + 1}

        base = new_price

    pnl = round((base - entry) if acao == "COMPRA" else (entry - base))
    return {"outcome": "TIMEOUT", "pnl_pts": pnl,
            "pnl_brl": round(pnl * PTS_POR_BRL * VOLUME, 2),
            "candles_ate_saida": n_future}


# ── Execução da simulação ──────────────────────────────────────────────────
print("\n" + "="*70)
print(f"  SIMULADOR DE TRADES — Motor TradeAI v5 ({total_target} cenários)")
print("="*70)
print("Gerando candles 15m + 1h e rodando generate_signal() com filtro direcional...\n")

BASE_PRICE = 174_000

results = []
trade_num = 0

for scenario_name, n_reps, params_15m, params_1h in SCENARIOS:
    for rep in range(n_reps):
        trade_num += 1
        base = BASE_PRICE + random.randint(-3000, 3000)
        base = (base // TICK_SIZE) * TICK_SIZE

        # Gera DataFrame 15m (250 candles ~2.5 dias)
        df_15m = gen_candles(n=250, base=base, freq="15min", **params_15m)

        # Gera DataFrame 1h (60 candles ~2.5 dias) para HTF
        df_1h = gen_candles(n=60, base=base, freq="1h", **params_1h)

        try:
            signal = generate_signal(df_15m, htf_df=df_1h)
        except Exception as e:
            signal = None

        d_block = signal.get("directional_block") if signal else None
        is_blocked = d_block is not None

        if not signal or signal.get("acao") == "NEUTRO":
            results.append({
                "trade":    trade_num,
                "cenario":  scenario_name,
                "acao":     "NEUTRO",
                "score":    signal.get("score", 0) if signal else 0,
                "htf":      signal.get("htf_trend", "?") if signal else "?",
                "bloqueado": is_blocked,
                "motivo_block": d_block or "",
                "entrada":  None,
                "stop":     None,
                "tp1":      None,
                "outcome":  "BLOQUEADO" if is_blocked else "SEM_SINAL",
                "pnl_pts":  0,
                "pnl_brl":  0.0,
                "candles":  0,
                "rr":       0,
            })
            continue

        outcome = simulate_outcome(signal)

        entry = signal.get("entrada") or 0
        sl    = signal.get("stop") or 0
        tp1   = signal.get("tp1") or 0
        risk  = abs(entry - sl)
        rwd   = abs(tp1 - entry)
        rr    = round(rwd / risk, 2) if risk > 0 else 0

        results.append({
            "trade":    trade_num,
            "cenario":  scenario_name,
            "acao":     signal.get("acao"),
            "score":    signal.get("score", 0),
            "htf":      signal.get("htf_trend", "?"),
            "bloqueado": False,
            "motivo_block": "",
            "entrada":  entry,
            "stop":     sl,
            "tp1":      tp1,
            "outcome":  outcome["outcome"],
            "pnl_pts":  outcome["pnl_pts"],
            "pnl_brl":  outcome["pnl_brl"],
            "candles":  outcome["candles_ate_saida"],
            "rr":       rr,
        })

# ── Relatório ──────────────────────────────────────────────────────────────
df_res = pd.DataFrame(results)

com_sinal   = df_res[df_res["acao"] != "NEUTRO"]
bloqueados  = df_res[df_res["bloqueado"] == True]
sem_sinal   = df_res[(df_res["acao"] == "NEUTRO") & (df_res["bloqueado"] == False)]
tp1_hits    = com_sinal[com_sinal["outcome"] == "TP1"]
stop_hits   = com_sinal[com_sinal["outcome"] == "STOP"]
timeout     = com_sinal[com_sinal["outcome"] == "TIMEOUT"]
compras     = com_sinal[com_sinal["acao"] == "COMPRA"]
vendas      = com_sinal[com_sinal["acao"] == "VENDA"]

print(f"{'─'*80}")
print(f"  {'#':<5} {'CENÁRIO':<30} {'AÇÃO':<8} {'SCORE':<7} {'HTF':<8} {'RESULTADO':<14} {'PL PTS'}")
print(f"{'─'*80}")

for _, r in df_res.iterrows():
    if r["bloqueado"]:
        block_type = "⛔ COMPRA" if "COMPRA" in r["motivo_block"] else "⛔ VENDA"
        print(f"  #{int(r['trade']):<4} {r['cenario']:<30} {block_type:<8}  |{abs(int(r['score']))}|    "
              f"{str(r['htf']):<8} BLOQUEADO      —")
    elif r["acao"] == "NEUTRO":
        print(f"  #{int(r['trade']):<4} {r['cenario']:<30} {'NEUTRO':<8}  |{abs(int(r['score']))}|    "
              f"{str(r['htf']):<8} SEM SINAL     —")
    else:
        pl_str  = f"{'+' if r['pnl_pts']>=0 else ''}{int(r['pnl_pts'])} pts"
        res_str = {"TP1": "✅ TP1", "STOP": "❌ STOP", "TIMEOUT": "⏱ TIMEOUT"}.get(r["outcome"], r["outcome"])
        print(f"  #{int(r['trade']):<4} {r['cenario']:<30} {r['acao']:<8}  |{abs(int(r['score']))}|    "
              f"{str(r['htf']):<8} {res_str:<14} {pl_str}")

# ── Estatísticas gerais ────────────────────────────────────────────────────
print(f"\n{'='*70}")
print("  ESTATÍSTICAS GERAIS — v5 com Filtro Direcional")
print(f"{'='*70}")
print(f"  Total de cenários simulados  : {len(df_res)}")
print(f"  Sinais aceitos (executados)  : {len(com_sinal)} ({len(com_sinal)/len(df_res)*100:.0f}%)")
print(f"  Bloqueados filtro direcional : {len(bloqueados)} ({len(bloqueados)/len(df_res)*100:.0f}%)")
print(f"  Sem sinal (NEUTRO puro)      : {len(sem_sinal)} ({len(sem_sinal)/len(df_res)*100:.0f}%)")
print(f"  → Compras executadas         : {len(compras)}")
print(f"  → Vendas executadas          : {len(vendas)}")

if len(bloqueados) > 0:
    bc = bloqueados[bloqueados["motivo_block"].str.contains("COMPRA", na=False)]
    bv = bloqueados[bloqueados["motivo_block"].str.contains("VENDA", na=False)]
    print(f"  → Compras bloqueadas         : {len(bc)}")
    print(f"  → Vendas bloqueadas          : {len(bv)}")

print(f"\n{'─'*70}")
print("  RESULTADOS DOS TRADES EXECUTADOS")
print(f"{'─'*70}")
if len(com_sinal) > 0:
    win_rate = len(tp1_hits) / len(com_sinal) * 100
    print(f"  ✅ TP1 atingido              : {len(tp1_hits)} ({win_rate:.1f}%)")
    print(f"  ❌ Stop atingido             : {len(stop_hits)} ({len(stop_hits)/len(com_sinal)*100:.1f}%)")
    print(f"  ⏱ Timeout (aberto)          : {len(timeout)} ({len(timeout)/len(com_sinal)*100:.1f}%)")

    pnl_total = com_sinal["pnl_pts"].sum()
    pnl_brl   = com_sinal["pnl_brl"].sum()
    pnl_gain  = tp1_hits["pnl_pts"].mean() if len(tp1_hits) else 0
    pnl_stop  = stop_hits["pnl_pts"].mean() if len(stop_hits) else 0
    rr_medio  = com_sinal[com_sinal["rr"] > 0]["rr"].mean()

    print(f"\n  P&L TOTAL                    : {'+' if pnl_total>=0 else ''}{int(pnl_total)} pts | R${pnl_brl:+.2f}")
    print(f"  Gain médio (TP1)             : +{int(pnl_gain)} pts | R${pnl_gain*PTS_POR_BRL:+.2f}")
    print(f"  Stop médio                   : {int(pnl_stop)} pts | R${pnl_stop*PTS_POR_BRL:.2f}")
    print(f"  R:R médio dos setups         : 1:{rr_medio:.2f}")
    print(f"  Candles até saída (média)    : {com_sinal['candles'].mean():.1f}")
    print(f"  Score médio dos sinais       : |{com_sinal['score'].abs().mean():.1f}|")

    # Score x Win Rate
    print(f"\n{'─'*70}")
    print("  WIN RATE POR SCORE")
    print(f"{'─'*70}")
    for score_min in [4, 5, 6, 7, 8, 9]:
        filtrado = com_sinal[com_sinal["score"].abs() >= score_min]
        if len(filtrado) > 0:
            wr = filtrado[filtrado["outcome"]=="TP1"].shape[0] / len(filtrado) * 100
            pnl_f = filtrado["pnl_pts"].sum()
            print(f"  Score ≥ |{score_min}|  → {len(filtrado):>3} trades  "
                  f"WinRate: {wr:>5.1f}%  P&L: {'+' if pnl_f>=0 else ''}{int(pnl_f)} pts")

    # Por cenário
    print(f"\n{'─'*70}")
    print("  RESULTADO POR TIPO DE MERCADO")
    print(f"{'─'*70}")
    for cenario in df_res["cenario"].unique():
        sub   = df_res[df_res["cenario"] == cenario]
        com   = sub[sub["acao"] != "NEUTRO"]
        blq   = sub[sub["bloqueado"] == True]
        if len(com) == 0:
            print(f"  {cenario:<35}: {len(sub)} cenarios - {len(blq)} bloq - NEUTRO")
            continue
        wins = com[com["outcome"]=="TP1"]
        wr   = len(wins)/len(com)*100
        pnl  = com["pnl_pts"].sum()
        print(f"  {cenario:<35}: {len(com):>3} exec  Win {wr:>5.1f}%  "
              f"P&L: {'+' if pnl>=0 else ''}{int(pnl):>6} pts"
              + (f"  [{len(blq)} bloq]" if len(blq) else ""))

    # Por direção
    print(f"\n{'─'*70}")
    print("  COMPRA vs VENDA")
    print(f"{'─'*70}")
    for acao, grupo in [("COMPRA", compras), ("VENDA", vendas)]:
        if len(grupo) == 0:
            continue
        wins_g = grupo[grupo["outcome"]=="TP1"]
        wr_g   = len(wins_g)/len(grupo)*100
        pnl_g  = grupo["pnl_pts"].sum()
        print(f"  {acao:<8}: {len(grupo):>3} trades  WinRate: {wr_g:>5.1f}%  "
              f"P&L: {'+' if pnl_g>=0 else ''}{int(pnl_g)} pts | R${pnl_g*PTS_POR_BRL:+.2f}")

    # Por HTF
    print(f"\n{'─'*70}")
    print("  WIN RATE POR TENDÊNCIA 1H (HTF)")
    print(f"{'─'*70}")
    for htf_val in ["alta", "baixa"]:
        sub_htf = com_sinal[com_sinal["htf"] == htf_val]
        if len(sub_htf) == 0:
            continue
        w = sub_htf[sub_htf["outcome"]=="TP1"]
        wr = len(w)/len(sub_htf)*100
        pnl_h = sub_htf["pnl_pts"].sum()
        print(f"  HTF {htf_val:<6}: {len(sub_htf):>3} trades  WinRate: {wr:>5.1f}%  "
              f"P&L: {'+' if pnl_h>=0 else ''}{int(pnl_h)} pts")

    # Expectativa matemática
    print(f"\n{'─'*70}")
    print("  EXPECTATIVA MATEMÁTICA (por trade executado)")
    print(f"{'─'*70}")
    ev = com_sinal["pnl_pts"].mean()
    print(f"  Expectativa por trade        : {'+' if ev>=0 else ''}{ev:.1f} pts | R${ev*PTS_POR_BRL:+.2f}")

    # Impacto do filtro direcional
    if len(bloqueados) > 0:
        print(f"\n{'─'*70}")
        print("  IMPACTO DO FILTRO DIRECIONAL v5")
        print(f"{'─'*70}")
        print(f"  Trades bloqueados            : {len(bloqueados)}")
        pct_saved = len(bloqueados) / (len(com_sinal) + len(bloqueados)) * 100
        print(f"  % do total de sinais raw     : {pct_saved:.1f}% filtrados")
        print(f"  → Evitou entradas contra a tendência macro (EMA200 + 1h)")
        if ev > 0:
            print(f"  Motor v5: POSITIVO com filtro direcional ativo ✅")
        else:
            print(f"  Motor v5: ainda negativo — revisar parâmetros ⚠️")

print(f"\n{'='*70}")
print("  Simulacao v5 concluida.")