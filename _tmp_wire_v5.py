"""Gera entradas GO v5 para acoes_go_paths.py (magics 20260855+)."""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

df = pd.read_csv("logs/backtest_acoes_v5_mega_ranking_20260802_0946.csv")
go = df[df.veredito.astype(str).str.contains("🟢", na=False)].sort_values(
    "net", ascending=False
)

# magics já usados no repo
used = set()
for p in Path(".").rglob("*.py"):
    if "venv" in str(p) or "__pycache__" in str(p):
        continue
    try:
        t = p.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        continue
    for m in re.findall(r"20260\d{3,5}", t):
        used.add(int(m))

magic = 20260855
while magic in used:
    magic += 1

lines = []
print(f"# {len(go)} GOs v5 · first magic {magic}")
for _, r in go.iterrows():
    name = str(r["setup"])
    fam = "GAP"
    if "ORB15" in name or "ORB60" in name or "ORB_" in name:
        fam = "ORB"
    elif "OUT" in name:
        fam = "OUT"
    elif "VSPIKE" in name or "VOLSPIKE" in name:
        fam = "VSPIKE"
    elif "VWAP" in name:
        fam = "VWAP"
    elif "INS" in name:
        fam = "INS"
    elif "IMP" in name:
        fam = "IMP"
    opd = fam in ("GAP", "ORB", "PDH")
    while magic in used:
        magic += 1
    used.add(magic)
    entry = (
        f'    {{"setup": "{name}", "symbol": "{r["sym"]}", "tf": {int(r["tf"])}, '
        f'"magic": {magic},\n'
        f'     "net": {float(r["net"]):.3f}, "pf": {float(r["PF"]):.2f}, '
        f'"cons": {int(r["cons%"])}, "n": {int(r["n"])}, '
        f'"oos_net": {float(r["oos_net"]):.3f},\n'
        f'     "family": "{fam}", "one_per_day": {opd}, "auto": True}},'
    )
    lines.append(entry)
    print(
        f"{magic} {r['sym']} M{int(r['tf'])} {name} "
        f"net={r['net']:.3f} PF={r['PF']} cons={r['cons%']} n={r['n']} oos={r['oos_net']:.3f}"
    )
    magic += 1

Path("_tmp_v5_go_entries.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
print("-> _tmp_v5_go_entries.txt")
print(f"last magic used: {magic - 1}")
print(f"total wired will be: 45 + {len(go)} = {45 + len(go)}")
