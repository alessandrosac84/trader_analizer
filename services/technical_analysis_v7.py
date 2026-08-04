"""
technical_analysis_v7.py — Camada 2 do Motor v7 (WIN/WDO): CONTEXTO + NÍVEL.

Diferenças estruturais vs o motor v6 (technical_analysis.py, INTOCADO):
  1. REGIME decide o playbook (tendência = pullback/rompimento; range = fade).
  2. NÍVEL institucional define ONDE entrar (VWAP e bandas σ, Opening Range,
     máx/mín de ontem) — entrada por ordem LIMITE no nível sempre que possível
     (elimina o custo de cruzar o spread — decisivo no WDO).
  3. Score é CONFLUÊNCIA em blocos com teto (não soma de 15 indicadores
     colineares): TENDÊNCIA(±3) + MOMENTUM(±3) + NÍVEL/GATILHO(±3) + CONTEXTO 1h(±2).
  4. Janela horária embutida (só Janela A e B; nada 11h30-14h nem após 16h30).
  5. Stop por estrutura (além do nível), com teto de sanidade em ATR.

Setups:
  PULLBACK_VWAP  (dia de tendência)  — limite na VWAP/banda 1σ a favor
  ORB            (dia de tendência, Janela A) — rompimento do Opening Range
  FADE_2SIGMA    (dia de range)      — limite na banda 2σ, alvo VWAP

Módulo NOVO e paralelo — o Monitor MT5 em produção continua no motor v6.
Interface: generate_signal_v7(df15, htf_df) → dict compatível com o formato
do motor v6 + campos novos (setup, modo_entrada, nivel_ref, regime, janela).
"""
import logging

import numpy as np
import pandas as pd

from services.technical_analysis import compute_indicators, _f, _r
from services.market_regime import build_daily_refs, classify, _minute_of_day, JANELA_A

logger = logging.getLogger(__name__)

# ── Parâmetros v7 ──────────────────────────────────────────────────────────
MIN_CONFLUENCE   = 5      # score mínimo (máx teórico 11) para armar sinal
ADX_MIN          = 18
STOP_ATR_CAP    = 1.2    # teto do stop em ATRs (sanidade)
STOP_BEYOND_LVL = 0.6    # stop = nível -/+ 0.6x ATR (estrutura)
TARGET_RR       = 1.5    # invariante v6.2 preservado
FADE_MIN_RR     = 1.5    # fade só se a VWAP estiver a >= 1.5R
LEVEL_TOL_ATR   = 0.35   # preço a até 0.35 ATR do nível = "no nível"
VOL_CONFIRM     = 1.3    # volume >= 1.3x média confirma rompimento

# ── v7.1 (calibragem com o 1º backtest real, 17/07/2026) ───────────────────
# Resultados (WINQ26 fev-jul + WDOQ26 abr-jul, com custos):
#   ORB:           WIN +0.056 R (n=15) | WDO +0.550 R, PF 5.0 (n=8)  → MANTIDO
#   PULLBACK_VWAP: WIN -0.197 R (n=9)  | WDO -1.178 R (n=3)          → DESLIGADO
# Diagnóstico do pullback: a ordem limite na VWAP é preenchida justamente
# quando o momentum está contra, e o stop de 0.6 ATR além do nível é varrido
# pelo próprio ruído do teste do nível. Precisa de redesenho (esperar o
# candle de rejeição do nível, não comprar a queda) — até lá, fica off.
# Amostras ainda pequenas: revalidar com histórico longo (contrato contínuo).
SETUP_PULLBACK_ENABLED = False
SETUP_ORB_ENABLED      = True
# v7.2 (backtest 6,6 anos, 18/07/2026): FADE_2SIGMA desligado —
# WIN -0.413 R (n=33) e WDO -0.261 R (n=29). Comprar/vender extremo de banda
# em 15m sem confirmação de fluxo não funciona nesses ativos.
SETUP_FADE_ENABLED     = False

# ── v7.3 (lab de setups estruturais, 18/07/2026) ───────────────────────────
# GAP_FADE: melhor setup do lab no WIN (+0.158 R, PF 1.36, DD -8.4 R, n=153).
# Gap moderado contra o fechamento de ontem → opera o fechamento do gap.
SETUP_GAPFADE_ENABLED = True
GAP_MIN_ATR   = 0.4    # |gap| mínimo em ATRs diários
GAP_MAX_ATR   = 1.2    # gap gigante = notícia — não faded
GAP_STOP_FRAC = 0.7    # stop = 0.7x |gap| além da entrada
GAP_MIN_RR    = 1.2    # alvo é o fechamento do gap (magnete natural) — RR mínimo
# ORB com entrada no RETESTE (limite) para WDO: o lab provou que o custo de
# cruzar spread é o que mata o WDO (-0.031 a mercado → +0.044 no reteste).
ORB_RETEST_SYMBOLS = ("WDO",)   # prefixos que usam entrada limite no nível do OR
# v7.4.1: rompimento TARDIO falha mais — hora 11 deu negativo em TODAS as
# rodadas (v2: -0.047 | v7.3: -0.019 | v7.4: -0.059). Último sinal de ORB às
# 10:30 (entrada até ~10:45); depois disso o movimento não tem combustível
# até a janela morta do almoço.
ORB_LAST_SIGNAL_MIN = 10 * 60 + 45   # sinal < 10:45


def _vwap_bands(day_df: pd.DataFrame):
    """VWAP intraday + desvio ponderado por volume (bandas 1σ e 2σ)."""
    tp  = (day_df["High"] + day_df["Low"] + day_df["Close"]) / 3
    vol = day_df["Volume"].clip(lower=1)
    cum_v   = vol.cumsum()
    vwap    = (tp * vol).cumsum() / cum_v
    var     = ((tp - vwap) ** 2 * vol).cumsum() / cum_v
    sigma   = np.sqrt(var)
    return vwap, sigma


def generate_signal_v7(df15: pd.DataFrame, htf_df: "pd.DataFrame | None" = None,
                       daily_refs: "pd.DataFrame | None" = None,
                       symbol: "str | None" = None) -> "dict | None":
    """
    Gera sinal v7 sobre o último candle de df15.
    daily_refs pode ser pré-computado (backtest) para performance.
    symbol (opcional): permite regras por ativo (ex.: WDO usa ORB por reteste).
    """
    if df15 is None or len(df15) < 40:
        return None

    df   = compute_indicators(df15)
    last = df.iloc[-1]
    ts   = df.index[-1]
    close = _f(last["Close"])
    atr   = _f(last["atr"]) or (close * 0.005)
    rsi   = _f(last["rsi"])
    adx   = _f(last["adx"])
    ema9, ema21, ema50 = _f(last["ema9"]), _f(last["ema21"]), _f(last["ema50"])
    macd_hist = _f(last["macd_hist"])

    if daily_refs is None:
        daily_refs = build_daily_refs(df15)
    date = ts.date()
    if date not in daily_refs.index:
        return None
    refs = daily_refs.loc[date]

    # máx/mín do dia ATÉ agora (sem look-ahead)
    dsel = df15.index.date == date
    day_df   = df15.loc[dsel]
    day_high = float(day_df["High"].max())
    day_low  = float(day_df["Low"].min())

    reg = classify(ts, close, day_high, day_low, refs)
    regime, janela = reg["regime"], reg["janela"]

    vwap_s, sigma_s = _vwap_bands(day_df)
    vwap  = float(vwap_s.iloc[-1])
    sigma = float(sigma_s.iloc[-1]) if not np.isnan(sigma_s.iloc[-1]) else atr
    b1_up, b1_dn = vwap + sigma, vwap - sigma
    b2_up, b2_dn = vwap + 2 * sigma, vwap - 2 * sigma

    # 1h: direção do timeframe maior (veto herdado da v6 — regra comprovada)
    htf_trend = None
    if htf_df is not None and len(htf_df) >= 26:
        try:
            h = compute_indicators(htf_df).iloc[-1]
            if _f(h["ema9"]) is not None and _f(h["ema21"]) is not None:
                htf_trend = "alta" if h["ema9"] > h["ema21"] else "baixa"
        except Exception:
            pass

    sinais: list[str] = [f"Regime: {regime} (dir {reg['dir_score']:+d}, exp {reg['expansion']}) | janela {janela}"]

    base = {
        "engine": "v7", "acao": "NEUTRO", "setup": None, "score": 0,
        "forca": "FRACA", "regime": regime, "janela": janela,
        "htf_trend": htf_trend, "modo_entrada": None, "nivel_ref": None,
        "entrada": _r(close), "stop": None, "tp1": None, "tp2": None, "tp3": None,
        "preco_atual": _r(close), "atr": _r(atr), "rsi": _r(rsi, 1) if rsi else None,
        "adx": _r(adx, 1) if adx else None, "vwap": _r(vwap),
        "vwap_b1": [_r(b1_dn), _r(b1_up)], "vwap_b2": [_r(b2_dn), _r(b2_up)],
        "or_high": _r(reg.get("or_high")) if not pd.isna(reg.get("or_high", np.nan)) else None,
        "or_low":  _r(reg.get("or_low"))  if not pd.isna(reg.get("or_low",  np.nan)) else None,
        "y_high": _r(reg.get("y_high")) if not pd.isna(reg.get("y_high", np.nan)) else None,
        "y_low":  _r(reg.get("y_low"))  if not pd.isna(reg.get("y_low",  np.nan)) else None,
        "sinais": sinais, "rr": TARGET_RR,
        "timestamp": ts.isoformat() if hasattr(ts, "isoformat") else str(ts),
        "candles": len(df15),
    }

    # ── Setup GAP_FADE (v7.3) — roda ANTES do gate de janela: o gap se opera
    # entre 09:15 e 09:45, enquanto o dia ainda está "FORMING".
    # Lab 6,6 anos (WIN): +0.158 R, PF 1.36, DD -8.4 R. Sem filtros de
    # tendência de propósito: fade é contra-movimento por natureza.
    if SETUP_GAPFADE_ENABLED and regime == "FORMING":
        minute = _minute_of_day(ts)
        y_close_r = reg.get("y_close")
        atr_d_r   = reg.get("atr_d")
        open_d_r  = reg.get("open_d")
        # v7.4: janela do SINAL = candle 09:00-09:30 → entrada 09:15-09:45.
        # (v7.3 exigia sinal >= 09:15 → entrada só às 09:30+, 15 min atrasada
        # vs o lab que validou o setup — o gap fecha cedo, o atraso comia o edge.)
        if (9 * 60 <= minute <= 9 * 60 + 30
                and not any(pd.isna(v) for v in (y_close_r, atr_d_r, open_d_r))
                and atr_d_r):
            gap = open_d_r - y_close_r
            ag  = abs(gap)
            if GAP_MIN_ATR * atr_d_r <= ag <= GAP_MAX_ATR * atr_d_r:
                buy = gap < 0                      # gap de baixa → compra o fechamento
                gap_aberto = (close < y_close_r) if buy else (close > y_close_r)
                if gap_aberto:
                    entry = close
                    stop  = entry - GAP_STOP_FRAC * ag if buy else entry + GAP_STOP_FRAC * ag
                    tp1   = y_close_r              # alvo = fechamento do gap (magnete)
                    risk  = abs(entry - stop)
                    rr    = abs(tp1 - entry) / risk if risk > 0 else 0
                    if rr >= GAP_MIN_RR:
                        out = dict(base)
                        out.update({
                            "acao": "COMPRA" if buy else "VENDA",
                            "setup": "GAP_FADE", "score": 6, "forca": "MODERADA",
                            "modo_entrada": "MERCADO", "nivel_ref": _r(y_close_r),
                            "entrada": _r(entry), "stop": _r(stop),
                            "tp1": _r(tp1), "tp2": None, "tp3": None,
                            # v7.4: o alvo é o fechamento do gap (ímã) — saída FIXA.
                            # Exits geridos (parcial+BE) estopam no ruído antes do
                            # fill e destroem o edge (lab +0.158 → v7.3 -0.040).
                            "exit_policy": "FIXED",
                        })
                        out["sinais"] = sinais + [
                            f"GAP_FADE: gap {gap:+.1f} ({ag/atr_d_r:.2f}x ATR_d) ainda aberto — "
                            f"{'COMPRA' if buy else 'VENDA'} @ {entry:.1f} | stop {stop:.1f} | "
                            f"alvo fech. ontem {tp1:.1f} (RR {rr:.2f})"
                        ]
                        return out

    # Sem janela ou dia não operável → NEUTRO
    if janela == "FECHADA" or regime in ("FORMING", "NO_TRADE"):
        sinais.append("Fora de janela operacional ou regime não operável [filtro]")
        return base

    vol_last = _f(day_df["Volume"].iloc[-1])
    vol_avg  = _f(df15["Volume"].tail(20).mean())
    vol_ok   = bool(vol_last and vol_avg and vol_last >= VOL_CONFIRM * vol_avg)

    def _blocks(direction: str, at_level: bool, level_kind: str) -> "tuple[int, list]":
        """Score de confluência em blocos com teto. direction: COMPRA|VENDA."""
        s = []
        buy = direction == "COMPRA"
        sgn = 1 if buy else -1
        # TENDÊNCIA (teto 3)
        t = 0
        if ema9 and ema21: t += sgn * (1 if ema9 > ema21 else -1)
        if ema50:          t += sgn * (1 if close > ema50 else -1)
        t += sgn * (1 if close > vwap else -1)
        t = max(-3, min(3, t))
        s.append(f"bloco tendência {t:+d}")
        # MOMENTUM (teto 3)
        m = 0
        if macd_hist is not None: m += sgn * (1 if macd_hist > 0 else -1)
        if rsi is not None:
            if buy and 40 <= rsi <= 65:  m += 1     # pullback saudável p/ compra
            if not buy and 35 <= rsi <= 60: m += 1
            if buy and rsi > 75:  m -= 1            # esticado
            if not buy and rsi < 25: m -= 1
        if adx is not None and adx >= 25: m += 1
        m = max(-3, min(3, m))
        s.append(f"bloco momentum {m:+d}")
        # NÍVEL (teto 3)
        n = 0
        if at_level: n += 2
        if vol_ok and level_kind == "ORB": n += 1
        if level_kind in ("VWAP", "B2") and not vol_ok: n += 1  # pullback quer volume BAIXO
        n = max(-3, min(3, n))
        s.append(f"bloco nível {n:+d} ({level_kind})")
        # CONTEXTO 1h (teto 2)
        c = 0
        if htf_trend == ("alta" if buy else "baixa"): c += 2
        elif htf_trend is None: c += 0
        s.append(f"bloco 1h {c:+d}")
        return t + m + n + c, s

    def _stamp_orb_targets(sig):
        """Alvos da gestão ORB ao vivo: parcial +1R · final +2.5R (não o TARGET_RR 1.5)."""
        if not sig:
            return sig
        entry, stop = sig.get("entrada"), sig.get("stop")
        if entry is None or stop is None:
            return sig
        risk = abs(float(entry) - float(stop))
        if risk <= 0:
            return sig
        sign = 1 if sig.get("acao") == "COMPRA" else -1
        sig["tp1"] = _r(float(entry) + sign * 1.0 * risk)
        sig["tp2"] = _r(float(entry) + sign * 2.5 * risk)
        return sig

    def _levels_out(direction, setup, level, entry_mode, stop, tp1, tp2, score, block_notes):
        buy = direction == "COMPRA"
        risk = abs(level - stop)
        if risk <= 0:
            return None
        # invariante: TP1 >= TARGET_RR
        min_tp = level + TARGET_RR * risk * (1 if buy else -1)
        if tp1 is None or (buy and tp1 < min_tp) or (not buy and tp1 > min_tp):
            tp1 = min_tp
        out = dict(base)
        out.update({
            "acao": direction, "setup": setup, "score": score,
            "forca": "FORTE" if score >= MIN_CONFLUENCE + 2 else "MODERADA",
            "modo_entrada": entry_mode, "nivel_ref": _r(level),
            "entrada": _r(level), "stop": _r(stop),
            "tp1": _r(tp1), "tp2": _r(tp2) if tp2 else None,
            "tp3": None,
        })
        out["sinais"] = sinais + block_notes + [
            f"Setup {setup}: {direction} {'LIMITE' if entry_mode=='LIMITE' else 'MERCADO'} "
            f"@ {level:.1f} | stop {stop:.1f} | TP1 {tp1:.1f}"
        ]
        return out

    # ── Playbook por regime ────────────────────────────────────────────────
    if regime in ("TREND_UP", "TREND_DOWN"):
        buy  = regime == "TREND_UP"
        dirn = "COMPRA" if buy else "VENDA"
        # veto 1h (herdado da v6)
        if htf_trend == ("baixa" if buy else "alta"):
            sinais.append("⛔ 1h contra o regime do dia — sem trade [filtro v6]")
            return base
        if adx is not None and adx < ADX_MIN:
            sinais.append(f"ADX {adx:.0f} < {ADX_MIN} — tendência sem força [filtro]")
            return base

        # Setup A: PULLBACK à VWAP/1σ (entrada LIMITE no nível)
        # v7.1: DESLIGADO por padrão — negativo nos 2 ativos no 1º backtest
        # real (ver nota SETUP_PULLBACK_ENABLED no topo do arquivo).
        if SETUP_PULLBACK_ENABLED:
            level = vwap if buy else vwap
            # em tendência forte o pullback para na banda 1σ do lado da tendência
            strong = abs(reg["dir_score"]) >= 3
            if strong:
                level = b1_dn if buy else b1_up
                level = max(level, vwap - 1.5 * sigma) if buy else min(level, vwap + 1.5 * sigma)
            dist = (close - level) if buy else (level - close)
            at_level = 0 <= dist <= LEVEL_TOL_ATR * atr * 2   # se aproximando por cima/baixo
            if at_level and dist >= 0:
                stop = level - STOP_BEYOND_LVL * atr if buy else level + STOP_BEYOND_LVL * atr
                if abs(level - stop) > STOP_ATR_CAP * atr:
                    stop = level - STOP_ATR_CAP * atr if buy else level + STOP_ATR_CAP * atr
                tp2  = (day_high if buy else day_low)
                score, notes = _blocks(dirn, True, "VWAP")
                if score >= MIN_CONFLUENCE:
                    sig = _levels_out(dirn, "PULLBACK_VWAP", level, "LIMITE", stop,
                                      None, tp2, score, notes)
                    if sig: return sig
                else:
                    sinais.append(f"PULLBACK_VWAP: confluência {score} < {MIN_CONFLUENCE} [filtro]")

        # Setup B: ORB — rompimento do Opening Range (Janela A)
        # v7.3: WIN entra a MERCADO no rompimento; WDO entra LIMITE no RETESTE
        # do nível rompido (lab: mesmo setup, -0.031 a mercado vs +0.044 no
        # reteste — o custo de cruzar o spread é o que mata o WDO).
        orh, orl = reg.get("or_high"), reg.get("or_low")
        _retest = bool(symbol) and any(p in symbol.upper() for p in ORB_RETEST_SYMBOLS)
        if (SETUP_ORB_ENABLED and janela == "A"
                and _minute_of_day(ts) < ORB_LAST_SIGNAL_MIN
                and not pd.isna(orh) and not pd.isna(orl)):
            prev_close = _f(df15["Close"].iloc[-2])
            if buy and prev_close is not None and prev_close <= orh < close:
                score, notes = _blocks(dirn, True, "ORB")
                if vol_ok and score >= MIN_CONFLUENCE:
                    if _retest:
                        level = float(orh)
                        stop  = max(orl + (orh - orl) / 2, level - STOP_ATR_CAP * atr)
                        sig = _levels_out(dirn, "ORB_RETEST", level, "LIMITE", stop,
                                          None, None, score, notes)
                        if sig: sig["exit_policy"] = "FIXED"   # v7.4: config validada no lab
                    else:
                        stop = max(orl + (orh - orl) / 2, close - STOP_ATR_CAP * atr)
                        sig = _levels_out(dirn, "ORB", close, "MERCADO", stop,
                                          None, None, score, notes)
                        if sig: return _stamp_orb_targets(sig)
                    if sig: return sig
                sinais.append(f"ORB compra detectado: vol_ok={vol_ok}, score={score} [avaliado]")
            if (not buy) and prev_close is not None and prev_close >= orl > close:
                score, notes = _blocks(dirn, True, "ORB")
                if vol_ok and score >= MIN_CONFLUENCE:
                    if _retest:
                        level = float(orl)
                        stop  = min(orh - (orh - orl) / 2, level + STOP_ATR_CAP * atr)
                        sig = _levels_out(dirn, "ORB_RETEST", level, "LIMITE", stop,
                                          None, None, score, notes)
                        if sig: sig["exit_policy"] = "FIXED"   # v7.4: config validada no lab
                    else:
                        stop = min(orh - (orh - orl) / 2, close + STOP_ATR_CAP * atr)
                        sig = _levels_out(dirn, "ORB", close, "MERCADO", stop,
                                          None, None, score, notes)
                        if sig: return _stamp_orb_targets(sig)
                    if sig: return sig
                sinais.append(f"ORB venda detectado: vol_ok={vol_ok}, score={score} [avaliado]")

    elif regime == "RANGE" and SETUP_FADE_ENABLED:
        # Setup C: FADE na banda 2σ, alvo VWAP (entrada LIMITE)
        # compra na 2σ inferior
        if close <= b2_dn + LEVEL_TOL_ATR * atr and (htf_trend != "baixa"):
            level, stop = b2_dn, b2_dn - STOP_BEYOND_LVL * atr
            risk = level - stop
            if risk > 0 and (vwap - level) >= FADE_MIN_RR * risk and (rsi or 50) <= 40:
                score, notes = _blocks("COMPRA", True, "B2")
                if score >= MIN_CONFLUENCE - 1:   # fade pede 1 a menos (contra-momentum por natureza)
                    sig = _levels_out("COMPRA", "FADE_2SIGMA", level, "LIMITE", stop,
                                      vwap, None, score, notes)
                    if sig: return sig
                sinais.append(f"FADE compra: confluência {score} insuficiente [filtro]")
        # venda na 2σ superior
        if close >= b2_up - LEVEL_TOL_ATR * atr and (htf_trend != "alta"):
            level, stop = b2_up, b2_up + STOP_BEYOND_LVL * atr
            risk = stop - level
            if risk > 0 and (level - vwap) >= FADE_MIN_RR * risk and (rsi or 50) >= 60:
                score, notes = _blocks("VENDA", True, "B2")
                if score >= MIN_CONFLUENCE - 1:
                    sig = _levels_out("VENDA", "FADE_2SIGMA", level, "LIMITE", stop,
                                      vwap, None, score, notes)
                    if sig: return sig
                sinais.append(f"FADE venda: confluência {score} insuficiente [filtro]")

    return base
