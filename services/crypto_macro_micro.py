"""
services/crypto_macro_micro.py — Monitor MACRO/MICRO do MONITOR CRYPTO.

⚠️ MÓDULO NOVO E INDEPENDENTE. NÃO toca no motor de score, no impulso, nem no
Monitor MT5. É uma camada de leitura de momentum em DOIS timeframes:

  MACRO  = contexto/força do timeframe maior (ex.: H1)
  MICRO  = momentum imediato do timeframe curto (ex.: M5)

Cada um vira um "medidor" (valor de pressão + direção + força %). A combinação
dos dois dá a leitura: CONTINUAÇÃO, PULLBACK (repique a favor da tendência),
REVERSÃO ou NEUTRO — no espírito daquele painel macro/micro.

Só cálculo (candles + tick_volume). Quem busca os candles e usa é o crypto_bp.
"""
import logging

logger = logging.getLogger(__name__)

DIR_THR   = 0.15   # |norm| acima disso → direcional; abaixo → neutro
NEUTRO_ADX = 18    # ADX abaixo disso (e norm fraco) → NEUTRO (lateral)
PCT_SCALE = 130    # escala do "força %" a partir do momentum normalizado


def _ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def _atr(df, n=14):
    import pandas as pd
    h, l, c = df["High"], df["Low"], df["Close"]
    tr = pd.concat([(h - l), (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()


def _adx(df, n=14):
    import pandas as pd
    h, l, c = df["High"], df["Low"], df["Close"]
    up, dn = h.diff(), -l.diff()
    plus_dm = ((up > dn) & (up > 0)) * up
    minus_dm = ((dn > up) & (dn > 0)) * dn
    tr = pd.concat([(h - l), (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(n).mean().replace(0, 1e-9)
    plus_di = 100 * (plus_dm.rolling(n).mean() / atr)
    minus_di = 100 * (minus_dm.rolling(n).mean() / atr)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, 1e-9)
    return dx.rolling(n).mean()


def tf_momentum(candles: list, window: int = 20, ema_f: int = 9, ema_s: int = 21) -> dict:
    """Momentum de UM timeframe.
    'value' = pressão acumulada (Σ (close-open)×tick_volume) na janela → nº grande
              (positivo=compra, negativo=venda), no estilo do medidor da imagem.
    window/ema pequenos = MICRO (responsivo); grandes = MACRO (contexto).
    Retorna {value, dir(ALTA/BAIXA/NEUTRO), pct(0-100), adx, norm}."""
    out = {"value": 0, "dir": "NEUTRO", "pct": 0, "adx": 0.0, "norm": 0.0}
    try:
        import pandas as pd
        if not candles or len(candles) < max(20, ema_s + 3):
            return out
        df = pd.DataFrame(candles).rename(
            columns={"open": "Open", "high": "High", "low": "Low", "close": "Close"})
        n = min(window, len(df) - 1)
        recent = df.iloc[-n:]
        vol = recent["volume"] if "volume" in recent else pd.Series([1.0] * n)
        pressure = float(((recent["Close"] - recent["Open"]) * vol).sum())
        atr_s = _atr(df).iloc[-1]
        atr_v = float(atr_s) if not pd.isna(atr_s) else 0.0
        vol_avg = float(vol.mean() or 0.0)
        base = atr_v * vol_avg * n
        norm = pressure / base if base else 0.0
        adx_s = _adx(df).iloc[-1]
        adx_v = float(adx_s) if not pd.isna(adx_s) else 0.0

        # confirma direção pela pilha de EMAs (evita 'value' dominado por 1 candle)
        ef, es = float(_ema(df["Close"], ema_f).iloc[-1]), float(_ema(df["Close"], ema_s).iloc[-1])
        ema_bias = 1 if ef > es else (-1 if ef < es else 0)

        direction = "NEUTRO"
        if norm > DIR_THR and ema_bias >= 0:
            direction = "ALTA"
        elif norm < -DIR_THR and ema_bias <= 0:
            direction = "BAIXA"
        if adx_v < NEUTRO_ADX and abs(norm) < 0.25:
            direction = "NEUTRO"

        pct = min(100, int(abs(norm) * PCT_SCALE))
        return {"value": int(round(pressure)), "dir": direction, "pct": pct,
                "adx": round(adx_v, 1), "norm": round(norm, 3)}
    except Exception as exc:
        logger.warning("crypto tf_momentum erro: %s", exc)
        return out


def read(macro: dict, micro: dict) -> dict:
    """Combina MACRO×MICRO numa leitura + ação sugerida (para alerta/auto-trade).
    kind ∈ {CONTINUACAO, PULLBACK, REVERSAO, PAUSA, NEUTRO}.
    action (só quando há gatilho a favor da tendência) ∈ {COMPRA, VENDA, None}."""
    md, xd = macro.get("dir", "NEUTRO"), micro.get("dir", "NEUTRO")
    mstrong = macro.get("pct", 0) >= 45
    neutro_pct = max(0, 100 - macro.get("pct", 0))
    kind, reading, action = "NEUTRO", "Sem contexto definido — mercado lateral", None

    if md == "NEUTRO":
        kind, reading = "NEUTRO", "Macro lateral — sem tendência de contexto (esperar)"
    elif md == "ALTA" and xd == "ALTA":
        kind, reading = "CONTINUACAO", "Continuação de ALTA (macro e micro alinhados)"
    elif md == "ALTA" and xd == "BAIXA":
        kind, reading = "PULLBACK", "Pullback na ALTA — repique de baixa dentro da tendência de alta"
    elif md == "BAIXA" and xd == "BAIXA":
        kind, reading = "CONTINUACAO", "Continuação de BAIXA (macro e micro alinhados)"
    elif md == "BAIXA" and xd == "ALTA":
        kind, reading = "PULLBACK", "Pullback na BAIXA — repique de alta dentro da tendência de baixa"
    else:
        kind, reading = "PAUSA", f"{md} pausando (micro neutro)"

    return {"reading": reading, "kind": kind, "action": action,
            "macro_dir": md, "micro_dir": xd, "macro_strong": mstrong,
            "neutro_pct": neutro_pct}
