"""
setup_generator.py — ETAPA 15: transforma padrões validados em SETUPS CANDIDATOS.

Um candidato NÃO é um motor: é uma especificação objetiva (condições + direção +
alvo) pronta para ser codificada no harness de backtest com custos
(backtest_setups_kimi2 / backtest_acoes_b3), que é quem dá o veredito final.
"""
import json
import os


def make_candidates(asset, tf, label, base, patterns, max_out=10):
    """patterns: DataFrame do discovery. Retorna lista de dicts candidatos."""
    if patterns is None or patterns.empty:
        return []
    direction = "COMPRA" if "long" in label or "high" in label else "VENDA"
    target_r = 2.0 if "2R" in label else (1.0 if "1R" in label else 3.0)
    out = []
    for r in patterns[patterns.oos_ok].head(max_out).itertuples():
        p = r.p_oos
        # expectância BRUTA estimada do label (+kR com prob p, -1R com 1-p)
        exp_bruta = round(p*target_r - (1-p)*1.0, 3)
        out.append({
            "asset": asset, "tf": f"M{tf}", "label": label, "direcao": direction,
            "condicoes": r.pattern, "alvo_R": target_r, "stop_R": 1.0,
            "p_in": r.p_in, "p_oos": r.p_oos, "lift_in": r.lift_in,
            "lift_oos": r.lift_oos, "n_in": r.n_in, "n_oos": r.n_oos,
            "anos_pos": r.anos_pos, "exp_bruta_R": exp_bruta,
            "obs": "expectância SEM custos — validar no harness com custos antes de qualquer uso",
        })
    return out


def save_candidates(cands, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cands, f, ensure_ascii=False, indent=2)
