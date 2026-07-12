"""
services/crypto_momentum.py — Detector de IMPULSO/ROMPIMENTO do MONITOR CRYPTO.

⚠️ MÓDULO NOVO E INDEPENDENTE. NÃO toca no motor de score (crypto_analysis) nem no
Monitor MT5. É uma camada ADICIONAL: pega movimentos bruscos (thrust) que o motor
seguidor de tendência perde, porque EMA/ADX são atrasados e travam entrada contra
o 1h justamente no início do movimento.

Perfil CONSERVADOR/CONFIRMADO: só dispara quando há ROMPIMENTO DE ESTRUTURA
(fecha além da mín/máx recente) + candle de EXPANSÃO (range ≥ k×ATR) + VOLUME
acima da média + corpo dominante. Com ANTI-EXAUSTÃO: se o movimento já andou
demais (entrada tardia), não persegue — retorna sem sinal.

detect() é só cálculo (não envia ordem). Quem executa é o crypto_bp, usando o
mesmo gate anti-overtrading (lock + cooldown + checagem de posição).
"""
import logging

logger = logging.getLogger(__name__)

# Parâmetros (perfil confirmado/conservador)
LOOKBACK      = 15     # janela p/ estrutura (mín/máx) e média de volume
ATR_MULT      = 1.5    # candle de rompimento precisa ter range ≥ 1.5×ATR
VOL_MULT      = 1.3    # volume do candle ≥ 1.3× a média recente
BODY_MIN      = 0.55   # corpo ≥ 55% do range do candle (candle "de força", não pavio)
SEQ_LOOK      = 3      # confirma direção nos últimos N candles
SEQ_MIN       = 2      # ao menos 2 dos últimos 3 candles na mesma direção
MAX_EXT_ATR   = 4.5    # se o movimento acumulado (4 candles) já passou disso → exaustão
RR            = 1.5    # alvo = 1.5× o risco (stop além do candle de rompimento)
STOP_ATR_BUF  = 0.25   # folga do stop além da máx/mín do candle de rompimento (×ATR)


def _atr(df, n=14):
    import pandas as pd
    h, l, c = df["High"], df["Low"], df["Close"]
    tr = pd.concat([(h - l), (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()


def detect(candles: list, cfg: dict = None) -> dict:
    """Detecta impulso confirmado. Retorna dict:
    {impulso: bool, acao, entrada, stop, tp1, forca, atr, ext_atr, confluences[]}.
    impulso=False quando não há rompimento válido (ou exaustão)."""
    out = {"impulso": False, "acao": "NEUTRO", "confluences": []}
    try:
        import pandas as pd
        if not candles or len(candles) < LOOKBACK + 5:
            return out
        df = pd.DataFrame(candles).rename(
            columns={"open": "Open", "high": "High", "low": "Low", "close": "Close"})
        atr = _atr(df)
        atr_v = float(atr.iloc[-1]) if not pd.isna(atr.iloc[-1]) else 0.0
        if atr_v <= 0:
            return out

        last = df.iloc[-1]
        o, h, l, c = float(last.Open), float(last.High), float(last.Low), float(last.Close)
        rng = max(h - l, 1e-9)
        body = abs(c - o)
        vol = float(last.get("volume", 0) or 0)

        # estrutura recente (exclui o candle atual)
        win = df.iloc[-(LOOKBACK + 1):-1]
        prev_low  = float(win["Low"].min())
        prev_high = float(win["High"].max())
        vol_avg   = float(win["volume"].mean()) if "volume" in win else 0.0

        # sequência direcional dos últimos SEQ_LOOK candles
        seq = df.iloc[-SEQ_LOOK:]
        downs = int((seq["Close"] < seq["Open"]).sum())
        ups   = int((seq["Close"] > seq["Open"]).sum())

        # candle de expansão + corpo dominante
        big     = rng >= ATR_MULT * atr_v
        strong  = (body / rng) >= BODY_MIN
        vol_ok  = (vol_avg <= 0) or (vol >= VOL_MULT * vol_avg)

        conf = []
        acao = "NEUTRO"
        # VENDA: fecha ABAIXO da mínima recente (rompimento), candle grande de baixa, volume
        if c < prev_low and c < o and big and strong and vol_ok and downs >= SEQ_MIN:
            acao = "VENDA"
            conf = [f"Rompeu mínima de {LOOKBACK} velas ({prev_low:.0f})",
                    f"Candle {rng/atr_v:.1f}×ATR (expansão)",
                    f"Corpo {body/rng*100:.0f}% do range",
                    (f"Volume {vol/vol_avg:.1f}× a média" if vol_avg else "volume n/d"),
                    f"{downs}/{SEQ_LOOK} candles de baixa"]
        # COMPRA: fecha ACIMA da máxima recente
        elif c > prev_high and c > o and big and strong and vol_ok and ups >= SEQ_MIN:
            acao = "COMPRA"
            conf = [f"Rompeu máxima de {LOOKBACK} velas ({prev_high:.0f})",
                    f"Candle {rng/atr_v:.1f}×ATR (expansão)",
                    f"Corpo {body/rng*100:.0f}% do range",
                    (f"Volume {vol/vol_avg:.1f}× a média" if vol_avg else "volume n/d"),
                    f"{ups}/{SEQ_LOOK} candles de alta"]
        else:
            return out

        # ANTI-EXAUSTÃO: conta candles GRANDES e direcionais SEGUIDOS terminando no atual.
        # 1-2 = rompimento fresco (entra). 4+ = movimento já maduro (tarde, não persegue).
        want_down = (acao == "VENDA")
        big_streak = 0
        for i in range(len(df) - 1, -1, -1):
            ci = df.iloc[i]
            rng_i = float(ci.High - ci.Low)
            same_dir = (float(ci.Close) < float(ci.Open)) if want_down else (float(ci.Close) > float(ci.Open))
            if rng_i >= ATR_MULT * atr_v and same_dir:
                big_streak += 1
            else:
                break
        if big_streak >= 4:
            out["confluences"] = [f"⚠️ {big_streak} candles grandes seguidos — movimento maduro, entrada tardia"]
            out["big_streak"] = big_streak
            return out   # impulso=False: não persegue o fim do movimento

        # stop além do extremo do candle de rompimento; alvo por R:R
        if acao == "VENDA":
            stop = h + STOP_ATR_BUF * atr_v
            entrada = c
            tp1 = entrada - RR * (stop - entrada)
        else:
            stop = l - STOP_ATR_BUF * atr_v
            entrada = c
            tp1 = entrada + RR * (entrada - stop)

        forca = "FORTE" if (rng / atr_v) >= 2.2 else "MODERADA"
        rnd = 2 if entrada < 100 else (1 if entrada < 5000 else 0)
        return {
            "impulso": True, "acao": acao, "forca": forca,
            "entrada": round(entrada, rnd), "stop": round(stop, rnd), "tp1": round(tp1, rnd),
            "atr": round(atr_v, 5), "big_streak": big_streak,
            "range_atr": round(rng / atr_v, 2), "confluences": conf,
        }
    except Exception as exc:
        logger.warning("crypto momentum detect erro: %s", exc)
        return out
