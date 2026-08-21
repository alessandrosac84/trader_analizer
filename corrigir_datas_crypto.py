"""
corrigir_datas_crypto.py — corrige (uma vez) trades do crypto que foram gravados
com data no FUTURO por causa do bug de timezone (hora do servidor da corretora).
Faz backup antes. Roda no seu PC:  python corrigir_datas_crypto.py
"""
import csv, os, shutil
from datetime import datetime, timezone, timedelta
from math import ceil

BRT = timezone(timedelta(hours=-3))
PATH = os.path.join("logs", "crypto_trades.csv")

def parse(s):
    try: return datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=BRT)
    except Exception: return None

now = datetime.now(BRT)
rows = list(csv.DictReader(open(PATH, encoding="utf-8")))
cols = rows[0].keys() if rows else []
future = [r for r in rows if (dt:=parse(r.get("datetime_brt",""))) and dt > now + timedelta(minutes=5)]
if not future:
    print("Nenhuma linha datada no futuro — nada a corrigir."); raise SystemExit
maxdt = max(parse(r["datetime_brt"]) for r in future)
shift_h = ceil((maxdt - now).total_seconds()/3600)
print(f"{len(future)} linhas no futuro (bug de TZ). Deslocando {shift_h}h para trás.")
shutil.copy(PATH, PATH + ".bak_tz")
for r in rows:
    dt = parse(r.get("datetime_brt",""))
    if dt and dt > now + timedelta(minutes=5):
        novo = dt - timedelta(hours=shift_h)
        print(f"   {r['datetime_brt']} -> {novo.strftime('%Y-%m-%d %H:%M:%S')}  ({r.get('symbol')} {r.get('profit')})")
        r["datetime_brt"] = novo.strftime("%Y-%m-%d %H:%M:%S")
with open(PATH, "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=list(cols)); w.writeheader(); w.writerows(rows)
print(f"Pronto. Backup em {PATH}.bak_tz")
