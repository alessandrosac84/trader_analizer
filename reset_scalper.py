"""
reset_scalper.py — Zera todos os dados do módulo Scalper para nova fase de métricas.

O que é zerado:
  - logs/scalper_trades.csv        → histórico de trades (CSV)
  - data/scalper_rejections.db     → rejeições de sinal
  - data/trade_ai.db               → tabela auto_trades (trades executados)

O que NÃO é alterado:
  - Módulos Monitor MT5 e Scalper (código intocado)
  - Configurações / settings
  - Outros módulos do sistema

Uso:
  1. PARE o servidor Flask antes de rodar.
  2. python reset_scalper.py
  3. Reinicie o Flask.

Backup gerado automaticamente em data/backup_<data>/ e logs/backup_<data>/
"""

import csv
import os
import shutil
import sqlite3
from datetime import datetime

BASE     = os.path.dirname(__file__)
TODAY    = datetime.now().strftime("%Y-%m-%d")
DB_DIR   = os.path.join(BASE, "data")
LOG_DIR  = os.path.join(BASE, "logs")
BK_DB    = os.path.join(DB_DIR, f"backup_{TODAY}")
BK_LOG   = os.path.join(LOG_DIR, f"backup_{TODAY}")

CSV_FILE = os.path.join(LOG_DIR, "scalper_trades.csv")
REJ_DB   = os.path.join(DB_DIR, "scalper_rejections.db")
TRADE_DB = os.path.join(DB_DIR, "trade_ai.db")

CSV_COLUMNS = [
    "id", "datetime_brt", "symbol", "mode",
    "direcao", "entry_price", "tp_price", "sl_price", "volume", "auto",
    "score", "score_delta", "score_acel", "score_vwap",
    "score_book", "score_vel", "score_tape", "score_horario", "score_absorcao",
    "vwap_price", "vwap_context", "atr_1m",
    "cum_delta", "cum_delta_bias",
    "session", "flow_signal", "book_imbalance_pct",
    "aggr_5s_dir", "aggr_30s_dir", "velocity_3s",
    "exit_price", "profit", "exit_reason", "duration_s",
    "resultado", "score_reasons",
]


def backup_file(src: str, dst_dir: str) -> str:
    if not os.path.exists(src):
        return f"  ⚠️  Não encontrado: {src}"
    os.makedirs(dst_dir, exist_ok=True)
    dst = os.path.join(dst_dir, os.path.basename(src))
    shutil.copy2(src, dst)
    size = os.path.getsize(dst)
    return f"  ✅  Backup → {dst} ({size:,} bytes)"


def reset_csv() -> None:
    print(f"\n📄  scalper_trades.csv")

    # Conta registros atuais
    count = 0
    if os.path.exists(CSV_FILE):
        with open(CSV_FILE, "r", encoding="utf-8") as f:
            count = sum(1 for _ in csv.DictReader(f))

    print(backup_file(CSV_FILE, BK_LOG))

    # Recria o arquivo com apenas o cabeçalho
    with open(CSV_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()

    print(f"  🗑️   {count} trades removidos — arquivo recriado com cabeçalho")


def reset_db_table(db_path: str, table: str) -> None:
    if not os.path.exists(db_path):
        print(f"  ⚠️  Banco não encontrado: {db_path}")
        return
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        cnt = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        conn.execute(f'DELETE FROM "{table}"')
        try:
            conn.execute(f'DELETE FROM sqlite_sequence WHERE name="{table}"')
        except Exception:
            pass
        conn.commit()
        conn.isolation_level = None
        conn.execute("VACUUM")
        print(f"  🗑️   {table}: {cnt} registros removidos")
    except sqlite3.OperationalError as e:
        print(f"  ⚠️   {table}: {e}")
    finally:
        conn.close()


def main():
    print("=" * 60)
    print(f"  RESET SCALPER — {TODAY}")
    print("=" * 60)

    # ── CSV de trades ──────────────────────────────────────────────
    reset_csv()

    # ── scalper_rejections.db ──────────────────────────────────────
    print(f"\n📁  scalper_rejections.db")
    print(backup_file(REJ_DB, BK_DB))
    reset_db_table(REJ_DB, "scalper_rejections")

    # ── trade_ai.db → apenas auto_trades ──────────────────────────
    print(f"\n📁  trade_ai.db → auto_trades")
    print(backup_file(TRADE_DB, BK_DB))
    reset_db_table(TRADE_DB, "auto_trades")

    print("\n" + "=" * 60)
    print("  ✅  Reset do Scalper concluído.")
    print(f"  📦  Backups em:")
    print(f"      {BK_DB}")
    print(f"      {BK_LOG}")
    print("\n  ➡️   Reinicie o Flask para começar com métricas zeradas.")
    print("=" * 60)
    input("\nPressione ENTER para fechar...")


if __name__ == "__main__":
    main()
