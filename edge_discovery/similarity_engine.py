"""
similarity_engine.py — V2 item 3: busca de MOMENTOS ANÁLOGOS no histórico.

"Memória de mercado": para um candle-consulta, encontra os K candles mais
parecidos (distância euclidiana em z-score sobre todas as features numéricas)
e responde o que aconteceu depois deles (p(+2R), expectância, MFE/MAE).
Numpy puro (KDTree/FAISS ficam como upgrade — desnecessários até ~200k barras).
"""
import numpy as np
import pandas as pd

from edge_discovery.expectancy_engine import expectancy_of

K_ANALOGS = 500
EXCLUDE_NEAR = 50            # não conta vizinhos temporais (autocorrelação)


def _matrix(F: pd.DataFrame):
    X = F.select_dtypes(include=[np.number])
    mu, sd = X.mean(), X.std().replace(0, 1)
    return ((X - mu)/sd).clip(-4, 4).fillna(0).values


def analog_stats(F: pd.DataFrame, Y: pd.DataFrame, E: pd.DataFrame,
                 query_idx: int = -1, k: int = K_ANALOGS) -> dict:
    """O que aconteceu após os k momentos mais parecidos com `query_idx`."""
    X = _matrix(F)
    qi = query_idx % len(X)
    d = np.sqrt(((X - X[qi])**2).sum(1))
    d[max(0, qi-EXCLUDE_NEAR):qi+EXCLUDE_NEAR+1] = np.inf     # exclui o entorno
    kk = min(k, (np.isfinite(d)).sum())
    idx = np.argpartition(d, kk)[:kk]
    yl = Y["hit_2R_long"].values[idx]; ys = Y["hit_2R_short"].values[idx]
    pl, ps = float(np.nanmean(yl)), float(np.nanmean(ys))
    return {"ts": str(F.index[qi]), "k": int(kk),
            "p2R_long": round(pl, 3), "p2R_short": round(ps, 3),
            "exp_long_R": expectancy_of(pl), "exp_short_R": expectancy_of(ps),
            "mfe_p50": round(float(np.nanmedian(E["mfe_long"].values[idx])), 2),
            "mae_p50": round(float(np.nanmedian(E["mae_long"].values[idx])), 2)}


def analog_predictiveness(F: pd.DataFrame, Y: pd.DataFrame, n_queries=150, k=300,
                          seed=5) -> dict:
    """Sanidade: os análogos PREVEEM algo? Compara p(analogos) × resultado real
    em consultas aleatórias da metade final (só passado como base de busca)."""
    X = _matrix(F)
    y = Y["hit_2R_long"].values.astype(float)
    n = len(X)
    rng = np.random.RandomState(seed)
    qs = rng.choice(np.arange(int(n*0.6), n-1), min(n_queries, max(10, n//300)), replace=False)
    preds, reals = [], []
    for qi in qs:
        if np.isnan(y[qi]):
            continue
        d = np.sqrt(((X[:qi-EXCLUDE_NEAR] - X[qi])**2).sum(1))   # só o passado
        kk = min(k, len(d))
        idx = np.argpartition(d, kk-1)[:kk]
        preds.append(np.nanmean(y[idx])); reals.append(y[qi])
    if len(preds) < 20:
        return {}
    preds, reals = np.array(preds), np.array(reals)
    hi = preds >= np.median(preds)
    return {"consultas": len(preds),
            "p_real_quando_analogos_otimistas": round(float(reals[hi].mean()), 3),
            "p_real_quando_analogos_pessimistas": round(float(reals[~hi].mean()), 3),
            "discrimina": bool(reals[hi].mean() > reals[~hi].mean() + 0.02)}
