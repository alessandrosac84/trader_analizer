"""
label_generator.py — ETAPA 2: labels de resultado (estes SIM olham o futuro).

Para cada candle, simula entrada no fechamento com risco = 1×ATR e responde:
  hit_{k}R_long / hit_{k}R_short — atingiu +kR ANTES de -1R (dentro de LOOKAHEAD barras)?
  bars_to_2R_long — em quantas barras (NaN se não atingiu)
  newhigh_{n} / newlow_{n} — fez nova máxima/mínima de n barras no futuro?
"""
import numpy as np
import pandas as pd

from edge_discovery.config import TARGETS_R, LOOKAHEAD, NEWHILO_NS


def build_labels(df: pd.DataFrame) -> pd.DataFrame:
    h, l, c = df.High.values, df.Low.values, df.Close.values
    tr = np.maximum(df.High - df.Low,
                    np.maximum((df.High - df.Close.shift()).abs(),
                               (df.Low - df.Close.shift()).abs()))
    atr = tr.ewm(alpha=1/14, adjust=False).mean().values
    n = len(df)
    L = {f"hit_{k:g}R_long": np.zeros(n, dtype=np.int8) for k in TARGETS_R}
    L.update({f"hit_{k:g}R_short": np.zeros(n, dtype=np.int8) for k in TARGETS_R})
    bars2 = np.full(n, np.nan)

    kmax = max(TARGETS_R)
    for i in range(n - 1):
        a = atr[i]
        if not a or np.isnan(a) or a <= 0:
            continue
        entry = c[i]
        sl_long, sl_short = entry - a, entry + a
        tg_long = {k: entry + k*a for k in TARGETS_R}
        tg_short = {k: entry - k*a for k in TARGETS_R}
        best_long = best_short = 0.0
        end = min(n, i + 1 + LOOKAHEAD)
        long_dead = short_dead = False
        for j in range(i + 1, end):
            if not long_dead:
                if l[j] <= sl_long:                 # stop antes do alvo restante
                    long_dead = True
                else:
                    while best_long < kmax and h[j] >= tg_long[_next(best_long)]:
                        best_long = _next(best_long)
                        if best_long == 2.0 and np.isnan(bars2[i]):
                            bars2[i] = j - i
                    if best_long >= kmax:
                        long_dead = True
            if not short_dead:
                if h[j] >= sl_short:
                    short_dead = True
                else:
                    while best_short < kmax and l[j] <= tg_short[_next(best_short)]:
                        best_short = _next(best_short)
                    if best_short >= kmax:
                        short_dead = True
            if long_dead and short_dead:
                break
        for k in TARGETS_R:
            if best_long >= k:
                L[f"hit_{k:g}R_long"][i] = 1
            if best_short >= k:
                L[f"hit_{k:g}R_short"][i] = 1

    out = pd.DataFrame(L, index=df.index)
    out["bars_to_2R_long"] = bars2
    # novas máximas/mínimas futuras (rolling máximo dos próximos n)
    hs, ls = pd.Series(h, index=df.index), pd.Series(l, index=df.index)
    for nn in NEWHILO_NS:
        fut_max = hs.shift(-nn).rolling(nn).max()   # máx. dos próximos nn candles
        fut_min = ls.shift(-nn).rolling(nn).min()
        out[f"newhigh_{nn}"] = (fut_max > hs).astype(int)
        out[f"newlow_{nn}"] = (fut_min < ls).astype(int)
    return out


def _next(cur):
    """Próximo alvo em R acima de cur."""
    for k in TARGETS_R:
        if k > cur:
            return k
    return cur
