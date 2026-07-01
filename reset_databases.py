"""
reset_databases.py — Zera os bancos de dados do Scalper para nova fase de métricas.

Uso:
  1. PARE o servidor Flask antes de rodar este script.
  2. Execute: python reset_databases.py
  3. Reinicie o Flask normalmente.

O backup já foi gerado em: data/backup_2025-06-25/
"""

import sqlite3
import os
import shutil
from datetime import datetime

BASE = os.path.join(os.path.dirname(__file__), "data")
TODAY = datetime.now().strftime("%Y-%m-%d")
BACKUP_DIR = os.path.join(BASE, f"backup_{TODAY}")

DBS = {
    "trade_ai.db": {
        "tables_to_clear": None,   # None = descobrir automaticamente e limpar todas
        "keep_schema": True,
    },
    "scalper_rejections.db": {
        "tables_to_clear": ["scalper_rejections"],
        "keep_schema": True,
    },
}


def backup(db_name: str) -> str:
    os.makedirs(BACKUP_DIR, exist_ok=True)
    src = os.path.join(BASE, db_name)
    dst = os.path.join(BACKUP_DIR, db_name)
    if os.path.exists(src):
        shutil.copy2(src, dst)
        size = os.path.getsize(dst)
        return f"✅  Backup: {dst} ({size:,} bytes)"
    return f"⚠️  Arquivo não encontrado: {src}"


def reset(db_name: str, tables_to_clear, keep_schema: bool) -> None:
    path = os.path.join(BASE, db_name)
    if not os.path.exists(path):
        print(f"  ⚠️  {db_name} não encontrado — pulando")
        return

    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")

    if tables_to_clear is None:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        tables_to_clear = [r[0] for r in rows]

    total_deleted = 0
    for tbl in tables_to_clear:
        try:
            cnt = conn.execute(f'SELECT COUNT(*) FROM "{tbl}"').fetchone()[0]
            conn.execute(f'DELETE FROM "{tbl}"')
            conn.execute(f'DELETE FROM sqlite_sequence WHERE name="{tbl}"')
            total_deleted += cnt
            print(f"  🗑️   {tbl}: {cnt} registros removidos")
        except sqlite3.OperationalError as e:
            print(f"  ⚠️   {tbl}: {e}")

    conn.commit()
    conn.isolation_level = None   # autocommit — necessário para VACUUM
    conn.execute("VACUUM")
    conn.close()
    print(f"  ✅  {db_name} zerado — {total_deleted} registros removidos, VACUUM executado")


def main():
    print("=" * 60)
    print(f"  RESET DE BANCOS — {TODAY}")
    print("=" * 60)

    for db_name, cfg in DBS.items():
        print(f"\n📁  {db_name}")
        print(backup(db_name))
        reset(db_name, cfg["tables_to_clear"], cfg["keep_schema"])

    print("\n" + "=" * 60)
    print("  ✅  Reset concluído. Backups salvos em:")
    print(f"      {BACKUP_DIR}")
    print("\n  ➡️   Reinicie o Flask para começar a nova fase de métricas.")
    print("=" * 60)
    input("\nPressione ENTER para fechar...")


if __name__ == "__main__":
    main()
