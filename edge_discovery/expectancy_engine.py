"""
expectancy_engine.py — V2 itens 8-9: MFE, MAE, tempo-até-alvo e EXPECTÂNCIA.

Para cada candle (entrada = fechamento, R = 1×ATR, horizonte = HORIZON barras):
  mfe_long / mae_long   — máxima excursão favorável/adversa em R (sem stop)
  tt_2R_long / tt_stop_long — barras até +2R / até -1R (NaN se não ocorreu)
  (espelhado para short)
Expectância de uma política fixa (alvo kR, stop 1R) é derivável:
  E = p(alvo antes do stop)·k − (1−p)·1   [labels do label_generator dão o p]
MFE/MAE alimentam a descoberta de stops/alvos ideais (quantis por padrão).
Tudo vetorizado (HORIZON passadas de numpy, sem loop por barra).
"""
import numpy as np
import pandas as pd

HORIZON = 40


def build_expectancy(df: pd.DataFrame, horizon: int = HORIZON) -> pd.DataFrame:
    h, l, c = df.High.values, df.Low.values, df.Close.values
    tr = np.maximum(df.High - df.Low,
                    np.maximum((df.High - df.Close.shift()).abs(),
                               (df.Low - df.Close.shift()).abs()))
    atr = tr.ewm(alpha=1/14, adjust=False).mean().values
    n = len(df)
    atr_safe = np.where((atr > 0) & np.isfinite(atr), atr, np.nan)

    mfe_l = np.full(n, np.nan); mae_l = np.full(n, np.nan)
    tt2_l = np.full(n, np.nan); tts_l = np.full(n, np.nan)
    tt2_s = np.full(n, np.nan); tts_s = np.full(n, np.nan)

    run_max = np.full(n, -np.inf)
    run_min = np.full(n, np.inf)
    for k in range(1, horizon + 1):
        hk = np.roll(h, -k); hk[-k:] = np.nan
        lk = np.roll(l, -k); lk[-k:] = np.nan
        run_max = np.fmax(run_max, hk)
        run_min = np.fmin(run_min, lk)
        # tempo até +2R (long): 1ª barra em que high >= entry+2R
        hit2l = (hk >= c + 2*atr_safe) & np.isnan(tt2_l)
        tt2_l[hit2l] = k
        hitsl = (lk <= c - 1*atr_safe) & np.isnan(tts_l)
        tts_l[hitsl] = k
        hit2s = (lk <= c - 2*atr_safe) & np.isnan(tt2_s)
        tt2_s[hit2s] = k
        hitss = (hk >= c + 1*atr_safe) & np.isnan(tts_s)
        tts_s[hitss] = k

    mfe_l = (run_max - c) / atr_safe
    mae_l = (c - run_min) / atr_safe

    out = pd.DataFrame({
        "mfe_long": mfe_l, "mae_long": mae_l,
        "mfe_short": mae_l, "mae_short": mfe_l,          # espelho
        "tt_2R_long": tt2_l, "tt_stop_long": tts_l,
        "tt_2R_short": tt2_s, "tt_stop_short": tts_s,
    }, index=df.index)
    return out


def expectancy_of(p_hit: float, target_r: float = 2.0, stop_r: float = 1.0) -> float:
    """Expectância BRUTA (sem custos) da política alvo kR / stop 1R."""
    return round(p_hit*target_r - (1-p_hit)*stop_r, 3)


def mfe_mae_summary(E: pd.DataFrame, mask=None) -> dict:
    """Quantis de MFE/MAE (base p/ stop/alvo ideais) no subconjunto `mask`."""
    sub = E if mask is None else E[mask]
    if len(sub) < 50:
        return {}
    q = lambda s, x: round(float(s.quantile(x)), 2)
    return {"mfe_p50": q(sub.mfe_long, .5), "mfe_p75": q(sub.mfe_long, .75),
            "mae_p50": q(sub.mae_long, .5), "mae_p75": q(sub.mae_long, .75),
            "tt2R_p50": q(sub.tt_2R_long.dropna(), .5) if sub.tt_2R_long.notna().any() else None}
