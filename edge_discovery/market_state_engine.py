"""
market_state_engine.py — V2 itens 1 e 7: ESTADOS DE MERCADO por clustering.

Em vez de analisar candle isolado, classifica cada momento do mercado num
ESTADO descoberto automaticamente (KMeans — sklearn se existir, senão
implementação própria em numpy; GMM/HDBSCAN entram como upgrade opcional).

O nome de cada estado é derivado do centróide (ex.: vol alta + tendência de
alta + volume forte → "TREND_UP_HOT"), e o motor responde: quais estados têm
expectativa positiva, quais antecedem +2R e quais nunca operar.
"""
import numpy as np
import pandas as pd

STATE_FEATURES = ["atr_pctl", "bb_width_pctl", "ema20_slope", "ema50_dist",
                  "vwap_dist", "vol_rel10", "compress", "rng_vs_atr",
                  "day_range_pos", "rsi"]
K_STATES = 8


def _kmeans_np(X, k, iters=30, seed=7):
    rng = np.random.RandomState(seed)
    cent = X[rng.choice(len(X), k, replace=False)]
    for _ in range(iters):
        d = ((X[:, None, :] - cent[None, :, :])**2).sum(-1)
        lab = d.argmin(1)
        new = np.array([X[lab == j].mean(0) if (lab == j).any() else cent[j] for j in range(k)])
        if np.allclose(new, cent):
            break
        cent = new
    return lab, cent


def fit_states(F: pd.DataFrame, k: int = K_STATES):
    """Retorna (Series de estado por barra, DataFrame de centróides em z-score)."""
    cols = [c for c in STATE_FEATURES if c in F.columns]
    X = F[cols].copy()
    mu, sd = X.mean(), X.std().replace(0, 1)
    Xz = ((X - mu) / sd).clip(-4, 4).fillna(0).values
    # subamostra p/ ajustar (velocidade) e depois atribui tudo
    idx = np.arange(len(Xz))
    fit_idx = idx if len(idx) <= 40000 else np.random.RandomState(1).choice(idx, 40000, replace=False)
    try:
        from sklearn.cluster import KMeans
        km = KMeans(n_clusters=k, n_init=4, random_state=7).fit(Xz[fit_idx])
        cent = km.cluster_centers_
        lab = km.predict(Xz)
    except Exception:
        lab_fit, cent = _kmeans_np(Xz[fit_idx], k)
        d = ((Xz[:, None, :] - cent[None, :, :])**2).sum(-1)
        lab = d.argmin(1)
    centroids = pd.DataFrame(cent, columns=cols)
    names = {j: _name_state(centroids.iloc[j]) for j in range(k)}
    states = pd.Series([names[j] for j in lab], index=F.index, name="state")
    centroids.index = [names[j] for j in range(k)]
    return states, centroids


def _name_state(z) -> str:
    """Nome legível a partir do centróide (em z-scores)."""
    parts = []
    trend = z.get("ema50_dist", 0) + z.get("vwap_dist", 0) + z.get("ema20_slope", 0)
    if trend > 0.8:
        parts.append("TREND_UP")
    elif trend < -0.8:
        parts.append("TREND_DN")
    else:
        parts.append("RANGE")
    if z.get("bb_width_pctl", 0) < -0.6 or z.get("compress", 0) < -0.5:
        parts.append("SQUEEZE")
    elif z.get("atr_pctl", 0) > 0.6 or z.get("rng_vs_atr", 0) > 0.6:
        parts.append("HOT")
    elif z.get("atr_pctl", 0) < -0.6:
        parts.append("QUIET")
    if z.get("vol_rel10", 0) > 0.7:
        parts.append("VOLSPIKE")
    return "_".join(parts)


def state_expectancy(states: pd.Series, Y: pd.DataFrame, E: pd.DataFrame,
                     oos_frac: float = 0.30) -> pd.DataFrame:
    """Tabela por estado: n, p(+2R long/short), expectância, MFE/MAE, e se segura no OOS."""
    from edge_discovery.expectancy_engine import expectancy_of
    cut = int(len(states)*(1-oos_frac))
    ins = np.zeros(len(states), dtype=bool); ins[:cut] = True
    rows = []
    for st in states.dropna().unique():
        m = (states == st).values
        n = int(m.sum())
        if n < 200:
            continue
        pl = float(Y["hit_2R_long"][m].mean())
        ps = float(Y["hit_2R_short"][m].mean())
        pl_oos = float(Y["hit_2R_long"][m & ~ins].mean()) if (m & ~ins).sum() > 50 else np.nan
        rows.append({"estado": st, "n": n, "pct_tempo": round(100*n/len(states), 1),
                     "p2R_long": round(pl, 3), "p2R_short": round(ps, 3),
                     "exp_long_R": expectancy_of(pl), "exp_short_R": expectancy_of(ps),
                     "p2R_long_oos": round(pl_oos, 3) if pl_oos == pl_oos else None,
                     "mfe_p50": round(float(E["mfe_long"][m].median()), 2),
                     "mae_p50": round(float(E["mae_long"][m].median()), 2)})
    out = pd.DataFrame(rows)
    return out.sort_values("exp_long_R", ascending=False) if not out.empty else out
