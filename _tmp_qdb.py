import sqlite3
from pathlib import Path
db = Path("data/trade_ai.db")
if db.exists():
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    tables = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    print("TABLES", tables)
    if "trades" in tables:
        for r in cur.execute("SELECT * FROM trades WHERE id=625").fetchall():
            print("TRADE625", dict(r))
        for r in cur.execute("SELECT * FROM trades ORDER BY id DESC LIMIT 5").fetchall():
            print("RECENT", dict(r))
    for t in tables:
        if "trade" in t.lower() or "log" in t.lower() or "event" in t.lower():
            try:
                sample = cur.execute(f"SELECT * FROM {t} ORDER BY rowid DESC LIMIT 3").fetchall()
                if sample:
                    print("SAMPLE", t, [dict(x) for x in sample])
            except Exception as e:
                print("SAMPLE_ERR", t, e)
    con.close()
else:
    print("NO_DB")
