"""
Limpa apenas os trades ABERTOS (sem fechamento) registrados hoje.
Os trades ja fechados (com resultado) sao preservados.

Execute com: python limpar_abertos_hoje.py
"""
import sqlite3
from pathlib import Path

DB = Path(__file__).parent / "data" / "trade_ai.db"
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

# Mostra trades fechados (preservados)
fechados = conn.execute("""
    SELECT id, opened_at, acao, entry_price, close_reason, pnl_pts, pnl_brl
    FROM auto_trades
    WHERE date(opened_at) = date('now', 'localtime')
      AND closed_at IS NOT NULL
    ORDER BY id
""").fetchall()

print(f"\n{'='*70}")
print(f"Trades FECHADOS hoje (serao PRESERVADOS - {len(fechados)} registros):")
print(f"{'='*70}")
for r in fechados:
    pnl = f"{r['pnl_pts']:+.0f} pts / R${r['pnl_brl']:+.2f}"
    print(f"  #{r['id']} | {r['opened_at']} | {r['acao']} @ {r['entry_price']} | {r['close_reason']} | {pnl}")

# Mostra trades abertos (phantoms a serem deletados)
abertos = conn.execute("""
    SELECT id, opened_at, acao, entry_price, score
    FROM auto_trades
    WHERE date(opened_at) = date('now', 'localtime')
      AND closed_at IS NULL
    ORDER BY id
""").fetchall()

print(f"\n{'='*70}")
print(f"Trades ABERTOS hoje (phantoms - serao DELETADOS - {len(abertos)} registros):")
print(f"{'='*70}")
if not abertos:
    print("  Nenhum trade aberto encontrado.")
    conn.close()
    input("Pressione Enter para sair...")
    exit()

for r in abertos:
    score_str = f"|{r['score']}|" if r['score'] else "sem score"
    print(f"  #{r['id']} | {r['opened_at']} | {r['acao']} @ {r['entry_price']} | score: {score_str}")

print(f"{'='*70}")
print(f"\nResumo real do dia (trades fechados):")
if fechados:
    total_pts = sum(r['pnl_pts'] for r in fechados)
    total_brl = sum(r['pnl_brl'] for r in fechados)
    gains = [r for r in fechados if r['pnl_pts'] > 0]
    stops = [r for r in fechados if r['pnl_pts'] < 0]
    print(f"  Gains : {len(gains)}  |  Stops : {len(stops)}")
    print(f"  P&L   : {total_pts:+.0f} pts / R${total_brl:+.2f}")
else:
    print("  Nenhum trade fechado hoje.")

confirm = input(f"\nConfirma a exclusao dos {len(abertos)} registro(s) aberto(s)? (s/N): ").strip().lower()

if confirm == "s":
    conn.execute("""
        DELETE FROM auto_trades
        WHERE date(opened_at) = date('now', 'localtime')
          AND closed_at IS NULL
    """)
    conn.commit()
    print(f"\nOK: {len(abertos)} registro(s) fantasma(s) removido(s).")
    print("Resultado real preservado intacto.")
else:
    print("\nOperacao cancelada.")

conn.close()
input("Pressione Enter para sair...")
