"""
discovery_engine.py — ETAPAS 4-8: estatísticas, importância e mineração de padrões.

Método (sem caixa-preta, reproduzível):
  1. Cada feature contínua vira quintis (q1..q5); binárias ficam 0/1.
  2. UNIVARIADO: P(label | bucket) vs taxa-base → lift + z binomial + suporte.
  3. BIVARIADO: cruza as TOP_FEATURES_BI melhores 2 a 2 (regras de associação).
  4. VALIDAÇÃO: split temporal 70/30 — o padrão precisa segurar no out-of-sample —
     e walk-forward por ano (% de anos em que o lift > 1).
Se scikit-learn estiver instalado, adiciona feature importance por RandomForest
(opcional; o núcleo não depende disso).
"""
import numpy as np
import pandas as pd

from edge_discovery.config import (MIN_SUPPORT_UNI, MIN_SUPPORT_BI, MIN_LIFT,
                                   MIN_Z, TOP_FEATURES_BI, OOS_FRAC, MIN_LIFT_OOS)

_BINARY_MAX_UNIQUE = 3


def bucketize(F: pd.DataFrame) -> pd.DataFrame:
    """Features contínuas → quintis 'q1'..'q5'; binárias → '0'/'1'."""
    B = pd.DataFrame(index=F.index)
    for col in F.columns:
        s = F[col]
        nun = s.nunique(dropna=True)
        if nun <= 1:
            continue
        if nun <= _BINARY_MAX_UNIQUE:
            # rótulo textual do valor ("0", "1", "0.5"...) — robusto a floats
            B[col] = s.map(lambda x: f"{float(x):g}" if pd.notna(x) else np.nan)
        else:
            try:
                B[col] = pd.qcut(s, 5, labels=[f"q{i}" for i in range(1, 6)], duplicates="drop").astype(str)
            except Exception:
                continue
    return B


def _z_binom(p_hat, p0, n):
    if n <= 0 or p0 <= 0 or p0 >= 1:
        return 0.0
    return (p_hat - p0) / np.sqrt(p0*(1-p0)/n)


def _stats_for_mask(mask, y, base):
    n = int(mask.sum())
    if n == 0:
        return None
    p = float(y[mask].mean())
    return {"n": n, "p": round(p, 4), "lift": round(p/base, 3) if base > 0 else 0,
            "z": round(_z_binom(p, base, n), 2)}


def discover(B: pd.DataFrame, y: pd.Series, years: pd.Series):
    """
    B: features bucketizadas · y: label 0/1 · years: ano de cada linha.
    Retorna (base_rate, DataFrame de padrões validados, ranking univariado completo).
    """
    ok = y.notna()
    B, y, years = B[ok], y[ok].astype(int), years[ok]
    n_total = len(y)
    base = float(y.mean())
    cut = int(n_total*(1-OOS_FRAC))
    ins = np.zeros(n_total, dtype=bool); ins[:cut] = True
    oos = ~ins
    base_in = float(y[ins].mean()) if ins.any() else base
    base_oos = float(y[oos].mean()) if oos.any() else base

    # ── univariado ──
    uni = []
    for col in B.columns:
        vals = B[col].dropna().unique()
        for val in vals:
            m = (B[col] == val).values
            s_all = _stats_for_mask(m, y.values, base)
            if not s_all or s_all["n"] < MIN_SUPPORT_UNI:
                continue
            uni.append({"cond": f"{col}={val}", "col": col, "val": val, **s_all})
    uni_df = pd.DataFrame(uni)
    if uni_df.empty:
        return base, pd.DataFrame(), pd.DataFrame()
    uni_df = uni_df.sort_values("lift", ascending=False)

    # candidatos univariados fortes + bivariados sobre as melhores colunas
    strong = uni_df[(uni_df.lift >= MIN_LIFT) & (uni_df.z >= MIN_Z)]
    top_cols = list(dict.fromkeys(strong.col))[:TOP_FEATURES_BI]
    cands = [(r.cond, (B[r.col] == r.val).values) for r in strong.itertuples()]
    for i in range(len(top_cols)):
        for j in range(i+1, len(top_cols)):
            c1, c2 = top_cols[i], top_cols[j]
            best1 = strong[strong.col == c1].iloc[0]
            best2 = strong[strong.col == c2].iloc[0]
            m = (B[c1] == best1.val).values & (B[c2] == best2.val).values
            cands.append((f"{best1.cond} & {best2.cond}", m))

    # ── validação in/out-of-sample + walk-forward por ano ──
    rows = []
    yv, yrs = y.values, years.values
    for cond, m in cands:
        s_in = _stats_for_mask(m & ins, yv, base_in)
        s_out = _stats_for_mask(m & oos, yv, base_oos)
        if not s_in or not s_out:
            continue
        min_sup = MIN_SUPPORT_BI if "&" in cond else MIN_SUPPORT_UNI
        if s_in["n"] < min_sup*(1-OOS_FRAC) or s_out["n"] < max(30, min_sup*OOS_FRAC*0.5):
            continue
        if s_in["lift"] < MIN_LIFT or s_in["z"] < MIN_Z:
            continue
        # walk-forward: % de anos com lift > 1
        yr_ok = yr_tot = 0
        for yr in np.unique(yrs):
            my = m & (yrs == yr)
            if my.sum() < 30:
                continue
            yr_tot += 1
            b_yr = yv[yrs == yr].mean()
            if b_yr > 0 and yv[my].mean()/b_yr > 1.0:
                yr_ok += 1
        rows.append({"pattern": cond, "n_in": s_in["n"], "lift_in": s_in["lift"],
                     "z_in": s_in["z"], "n_oos": s_out["n"], "lift_oos": s_out["lift"],
                     "oos_ok": s_out["lift"] >= MIN_LIFT_OOS,
                     "anos_pos": f"{yr_ok}/{yr_tot}",
                     "anos_pct": round(100*yr_ok/yr_tot) if yr_tot else 0,
                     "p_in": s_in["p"], "p_oos": s_out["p"]})
    pat = pd.DataFrame(rows)
    if not pat.empty:
        pat = pat.sort_values(["oos_ok", "lift_oos"], ascending=False).reset_index(drop=True)
    return base, pat, uni_df


def sklearn_importance(F: pd.DataFrame, y: pd.Series, top=25):
    """Feature importance por RandomForest (opcional — só se sklearn existir)."""
    try:
        from sklearn.ensemble import RandomForestClassifier
    except Exception:
        return None
    ok = y.notna()
    X = F[ok].fillna(F[ok].median(numeric_only=True))
    X = X.select_dtypes(include=[np.number])
    yv = y[ok].astype(int)
    if len(X) > 60000:                      # subamostra p/ velocidade
        idx = np.random.RandomState(1).choice(len(X), 60000, replace=False)
        X, yv = X.iloc[idx], yv.iloc[idx]
    rf = RandomForestClassifier(n_estimators=120, max_depth=6, min_samples_leaf=200,
                                n_jobs=-1, random_state=1)
    rf.fit(X, yv)
    imp = pd.Series(rf.feature_importances_, index=X.columns).sort_values(ascending=False)
    return imp.head(top)


def by_dimension(F: pd.DataFrame, y: pd.Series, dim: str):
    """ETAPAS 12-14: expectativa por hora/dia-da-semana/sessão."""
    ok = y.notna() & F[dim].notna()
    g = y[ok].astype(int).groupby(F.loc[ok, dim])
    out = pd.DataFrame({"n": g.size(), "p": g.mean().round(4)})
    base = float(y[ok].astype(int).mean())
    out["lift"] = (out["p"]/base).round(3) if base > 0 else 0
    return out[out.n >= 100].sort_values("lift", ascending=False)
