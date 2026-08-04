import re, pathlib
t = pathlib.Path("services/win_go_runtime.py").read_text(encoding="utf-8")
for block in re.finditer(r'"WDO_[A-Z0-9_]+"\s*:\s*\{.*?\n    \}', t, re.S):
    b = block.group(0)
    name = re.search(r'"(WDO_[^"]+)"', b).group(1)
    magic = re.search(r'"magic"\s*:\s*(\d+)', b)
    print(name, magic.group(1) if magic else "?")
