"""
hypothesis_engine.py — V2 itens 4, 6, 10, 13, 14, 15 + MOTOR DE HIPÓTESES.

O "pesquisador automático": gera MILHARES de hipóteses combinando condições
atômicas (2 a 4 condições), testa todas vetorizado e só aprova o que sobrevive
à bateria completa:
  1. suporte e lift mínimos + significância (z binomial)
  2. FALSE DISCOVERY CONTROL — Benjamini-Hochberg sobre TODAS as hipóteses
     testadas (milhares de testes ⇒ controle de falso positivo obrigatório)
  3. OUT-OF-SAMPLE temporal (70/30)
  4. WALK-FORWARD por ano (% de anos com lift > 1)
  5. MONTE CARLO — deslocamento circular do label (200 réplicas): o lift real
     precisa superar o percentil 95 do acaso
  6. DEDUP por Jaccard (condições diferentes com a MESMA carteira de barras
     são o mesmo padrão — corrige o alias hour=q5 ≡ sess_ny=1 da V1)
Cada aprovado sai com PATTERN SCORE composto e EXPLICAÇÃO de por que passou.
Árvore de decisão rasa (sklearn, opcional) entra como gerador extra de atoms.
"""
import numpy as np
import pandas as pd

from edge_discovery.expectancy_engine import expectancy_of

TOP_ATOMS      = 40        # átomos (condições) que entram nas combinações
MAX_COMBO      = 4
N_RANDOM_QUADS = 4000      # amostra de combinações de 4 (explosão combinatória)
MIN_SUPPORT    = 150
MIN_LIFT       = 1.20
MIN_Z          = 2.5
FDR_ALPHA      = 0.05
MC_REPS        = 200
OOS_FRAC       = 0.30
JACCARD_DUP    = 0.90


def _z(p, p0, n):
    return (p - p0)/np.sqrt(p0*(1-p0)/n) if n > 0 and 0 < p0 < 1 else 0.0


def _pval(z):
    """p-valor unilateral aproximado da normal (sem scipy)."""
    return 0.5*np.exp(-0.717*z - 0.416*z*z) if z > 0 else 0.5


def build_atoms(B: pd.DataFrame, y: np.ndarray, base: float):
    """Condições atômicas ranqueadas por |lift| univariado."""
    atoms = []
    for col in B.columns:
        for val in B[col].dropna().unique():
            m = (B[col] == val).values
            n = int((m & ~np.isnan(y)).sum())
            if n < MIN_SUPPORT:
                continue
            p = np.nanmean(y[m])
            lift = p/base if base > 0 else 0
            atoms.append((abs(lift-1), f"{col}={val}", m))
    atoms.sort(key=lambda t: -t[0])
    return [(name, m) for _, name, m in atoms[:TOP_ATOMS]]


def tree_atoms(F: pd.DataFrame, y: pd.Series, max_atoms=10):
    """Item 4 (opcional): regras de árvore rasa viram átomos extras."""
    try:
        from sklearn.tree import DecisionTreeClassifier
    except Exception:
        return []
    ok = y.notna()
    X = F[ok].select_dtypes(include=[np.number]).fillna(0)
    if len(X) < 2000:
        return []
    dt = DecisionTreeClassifier(max_depth=2, min_samples_leaf=300, random_state=1)
    dt.fit(X, y[ok].astype(int))
    out = []
    t = dt.tree_
    for feat, thr in zip(t.feature, t.threshold):
        if feat >= 0:
            col = X.columns[feat]
            full = pd.Series(False, index=F.index)
            full[ok] = (X[col] <= thr)
            out.append((f"{col}<={thr:.3g}", full.values))
            out.append((f"{col}>{thr:.3g}", (~full & pd.Series(ok, index=F.index)).values))
    return out[:max_atoms]


def _bh_fdr(pvals, alpha=FDR_ALPHA):
    """Benjamini-Hochberg: retorna máscara de aprovados."""
    p = np.asarray(pvals)
    order = np.argsort(p)
    m = len(p)
    passed = np.zeros(m, dtype=bool)
    thresh = alpha*(np.arange(1, m+1)/m)
    ok = p[order] <= thresh
    if ok.any():
        kmax = np.max(np.where(ok))
        passed[order[:kmax+1]] = True
    return passed


def run_hypotheses(B: pd.DataFrame, F: pd.DataFrame, y_ser: pd.Series,
                   years: pd.Series, seed=11):
    """Gera, testa e valida hipóteses. Retorna (DataFrame aprovados, nº testadas)."""
    rng = np.random.RandomState(seed)
    y = y_ser.values.astype(float)
    valid = ~np.isnan(y)
    n_all = int(valid.sum())
    base = np.nanmean(y)
    cut = int(len(y)*(1-OOS_FRAC))
    ins = np.zeros(len(y), dtype=bool); ins[:cut] = True
    base_in = np.nanmean(y[ins]); base_oos = np.nanmean(y[~ins])
    yrs = years.values

    atoms = build_atoms(B, y, base) + tree_atoms(F, y_ser)
    if len(atoms) < 3:
        return pd.DataFrame(), 0
    names = [a[0] for a in atoms]
    masks = [a[1] & valid for a in atoms]
    A = len(atoms)

    # ── gera hipóteses: pares, trincas e amostra de quadras ──
    combos = [(i,) for i in range(A)]
    combos += [(i, j) for i in range(A) for j in range(i+1, A)]
    trip = [(i, j, k) for i in range(A) for j in range(i+1, A) for k in range(j+1, A)]
    combos += trip if len(trip) <= 12000 else [trip[t] for t in rng.choice(len(trip), 12000, replace=False)]
    if MAX_COMBO >= 4 and A >= 4:
        quads = set()
        while len(quads) < N_RANDOM_QUADS:
            quads.add(tuple(sorted(rng.choice(A, 4, replace=False))))
        combos += list(quads)

    # ── testa tudo (vetorizado por máscara) ──
    res = []
    for combo in combos:
        m = masks[combo[0]].copy()
        for ix in combo[1:]:
            m &= masks[ix]
        n = int(m.sum())
        if n < MIN_SUPPORT:
            continue
        p = y[m].mean()
        lift = p/base if base > 0 else 0
        z = _z(p, base, n)
        res.append({"combo": combo, "n": n, "p": p, "lift": lift, "z": z,
                    "pval": _pval(z), "mask": m})
    if not res:
        return pd.DataFrame(), len(combos)
    n_tested = len(res)

    # ── FDR (Benjamini-Hochberg) sobre TODAS as testadas ──
    fdr_pass = _bh_fdr([r["pval"] for r in res])
    res = [r for r, keep in zip(res, fdr_pass)
           if keep and r["lift"] >= MIN_LIFT and r["z"] >= MIN_Z]

    # ── dedup por Jaccard (padrões-alias) ──
    res.sort(key=lambda r: -r["lift"])
    kept = []
    for r in res:
        dup = False
        for k in kept:
            inter = np.logical_and(r["mask"], k["mask"]).sum()
            union = np.logical_or(r["mask"], k["mask"]).sum()
            if union and inter/union >= JACCARD_DUP:
                dup = True; break
        if not dup:
            kept.append(r)
        if len(kept) >= 60:
            break

    # ── OOS + walk-forward + Monte Carlo nos sobreviventes ──
    out = []
    for r in kept:
        m = r["mask"]
        m_in, m_oos = m & ins, m & ~ins
        if m_in.sum() < MIN_SUPPORT*0.5 or m_oos.sum() < 40:
            continue
        p_in, p_oos = y[m_in].mean(), y[m_oos].mean()
        lift_in = p_in/base_in if base_in > 0 else 0
        lift_oos = p_oos/base_oos if base_oos > 0 else 0
        # walk-forward por ano
        yr_ok = yr_tot = 0
        for yr in np.unique(yrs):
            my = m & (yrs == yr)
            if my.sum() < 30:
                continue
            yr_tot += 1
            byr = np.nanmean(y[yrs == yr])
            if byr > 0 and y[my].mean()/byr > 1.0:
                yr_ok += 1
        # Monte Carlo: shift circular do label — mantém autocorrelação do y
        mc_lifts = np.empty(MC_REPS)
        idxm = np.where(m)[0]
        for t in range(MC_REPS):
            sh = rng.randint(50, len(y)-50)
            y_sh = np.roll(y, sh)
            mc_lifts[t] = np.nanmean(y_sh[idxm])/base if base > 0 else 0
        mc_p95 = float(np.nanpercentile(mc_lifts, 95))
        mc_ok = r["lift"] > mc_p95
        oos_ok = lift_oos >= 1.08
        exp_r = expectancy_of(p_oos)
        score = round(min(r["z"], 8)/8*25 + min(np.log10(r["n"])/4, 1)*15
                      + (25 if oos_ok else 0)
                      + (yr_ok/yr_tot*20 if yr_tot else 0)
                      + (15 if mc_ok else 0), 1)
        out.append({"hipotese": " & ".join(names[i] for i in r["combo"]),
                    "n": r["n"], "p": round(r["p"], 3), "lift": round(r["lift"], 3),
                    "z": round(r["z"], 2), "lift_in": round(lift_in, 3),
                    "lift_oos": round(lift_oos, 3), "oos_ok": oos_ok,
                    "anos_pos": f"{yr_ok}/{yr_tot}", "mc_p95": round(mc_p95, 3),
                    "mc_ok": mc_ok, "exp_bruta_R": exp_r, "score": score,
                    "aprovada": bool(oos_ok and mc_ok),
                    "explicacao": (f"suporte n={r['n']} · z={r['z']:.1f} (FDR-BH ok) · "
                                   f"OOS lift {lift_oos:.2f} {'✔' if oos_ok else '✘'} · "
                                   f"anos+ {yr_ok}/{yr_tot} · "
                                   f"MC real {r['lift']:.2f} vs acaso p95 {mc_p95:.2f} "
                                   f"{'✔' if mc_ok else '✘'} · exp bruta {exp_r:+.2f}R")})
    df = pd.DataFrame(out)
    if not df.empty:
        df = df.sort_values(["aprovada", "score"], ascending=False).reset_index(drop=True)
    return df, n_tested
