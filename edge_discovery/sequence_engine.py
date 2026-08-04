"""
sequence_engine.py — V2 item 2: mineração de SEQUÊNCIAS (n-grams de contexto).

Cada barra vira um token compacto (direção + volatilidade + volume), ex.:
  "Uc" = subiu em compressão · "DXv" = caiu com range largo e volume alto
Minera n-grams (3 a 5 barras) e mede o que acontece DEPOIS da sequência
(labels +2R), com lift, suporte e validação out-of-sample temporal.
"""
import numpy as np
import pandas as pd

NGRAM_SIZES = (3, 4, 5)
MIN_SUPPORT = 200
MIN_LIFT = 1.20


def tokenize(F: pd.DataFrame) -> pd.Series:
    d = np.where(F["dir_up"] == 1, "U", "D")
    vol = np.where(F["bb_width_pctl"] < 0.2, "c",           # compressão
                   np.where(F["rng_vs_atr"] > 1.3, "X", "-"))  # expansão / normal
    vspike = np.where(F["vol_rel10"] > 1.7, "v", "")
    return pd.Series([f"{a}{b}{c}" for a, b, c in zip(d, vol, vspike)], index=F.index)


def mine_sequences(F: pd.DataFrame, y: pd.Series, oos_frac: float = 0.30) -> pd.DataFrame:
    tok = tokenize(F).values
    yv = y.values.astype(float)
    ok = ~np.isnan(yv)
    n = len(tok)
    cut = int(n*(1-oos_frac))
    base_in = np.nanmean(yv[:cut]); base_oos = np.nanmean(yv[cut:])
    rows = []
    for L in NGRAM_SIZES:
        # sequência termina na barra i → outcome é o label da barra i
        grams = ["|".join(tok[i-L+1:i+1]) for i in range(L-1, n)]
        gs = pd.Series(grams, index=np.arange(L-1, n))
        for gram, idxs in gs.groupby(gs).groups.items():
            idx = np.fromiter(idxs, dtype=int)
            idx = idx[ok[idx]]
            if len(idx) < MIN_SUPPORT:
                continue
            i_in, i_oos = idx[idx < cut], idx[idx >= cut]
            if len(i_in) < MIN_SUPPORT*0.6 or len(i_oos) < 40:
                continue
            p_in = yv[i_in].mean(); p_oos = yv[i_oos].mean()
            lift_in = p_in/base_in if base_in > 0 else 0
            lift_oos = p_oos/base_oos if base_oos > 0 else 0
            if lift_in < MIN_LIFT:
                continue
            z = (p_in - base_in)/np.sqrt(base_in*(1-base_in)/len(i_in))
            rows.append({"seq": gram, "len": L, "n_in": len(i_in), "p_in": round(p_in, 3),
                         "lift_in": round(lift_in, 3), "z": round(z, 2),
                         "n_oos": len(i_oos), "p_oos": round(p_oos, 3),
                         "lift_oos": round(lift_oos, 3),
                         "oos_ok": lift_oos >= 1.08})
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    return out[out.z >= 2.5].sort_values(["oos_ok", "lift_oos"], ascending=False).reset_index(drop=True)
