import sqlite3
from pathlib import Path
con=sqlite3.connect("data/trade_ai.db")
con.row_factory=sqlite3.Row
cur=con.cursor()
for q in [
    "SELECT * FROM mt5_signals WHERE created_at LIKE '2026-07-24%' OR tv_symbol LIKE '%WDO%' ORDER BY id DESC LIMIT 10",
    "SELECT * FROM risk_events WHERE timestamp LIKE '2026-07-24%' ORDER BY id DESC LIMIT 20",
    "SELECT * FROM analyses WHERE created_at LIKE '2026-07-24%' ORDER BY id DESC LIMIT 5",
]:
    try:
        rows=cur.execute(q).fetchall()
        print('---', q[:70], 'count', len(rows))
        for r in rows[:5]:
            print(dict(r))
    except Exception as e:
        print('ERR', e)
con.close()
