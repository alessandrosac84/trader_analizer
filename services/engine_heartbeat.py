"""
engine_heartbeat.py — pulso dos motores (transparência do "por que não operou").

Cada motor grava um JSON por ciclo (logs/hb_<nome>.json) com timestamp, estado
e o ÚLTIMO MOTIVO por ativo de não ter aberto trade. A tela /saude lê tudo e
mostra: motor vivo/morto (idade do pulso) + motivos ao vivo.
Arquivo por motor = sem corrida entre os dois processos (B3 e CRYPTO).
"""
import json
import os
import time
from pathlib import Path

_LOG = Path(__file__).resolve().parent.parent / "logs"


def beat(name: str, data: dict) -> None:
    try:
        _LOG.mkdir(exist_ok=True)
        payload = {"ts": time.time(), **(data or {})}
        tmp = _LOG / f"hb_{name}.json.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, default=str)
        os.replace(tmp, _LOG / f"hb_{name}.json")
    except Exception:
        pass


def load_all() -> dict:
    out = {}
    try:
        for p in _LOG.glob("hb_*.json"):
            try:
                d = json.load(open(p, encoding="utf-8"))
                d["idade_s"] = round(time.time() - float(d.get("ts", 0)))
                out[p.stem[3:]] = d
            except Exception:
                continue
    except Exception:
        pass
    return out
