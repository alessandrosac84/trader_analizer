"""
Limpa todos os trades automáticos registrados hoje no banco de dados.
Execute com: python limpar_trades_hoje.py
"""
import sqlite3
from pathlib import Path

DB = Path(__file__).parent / "data" / "trade_ai.db"

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

# Mostra o que será deletado
rows = conn.execute("""
    SELECT id, opened_at, acao, entry_price, close_reason, pnl_pts, pnl_brl
    FROM auto_trades
    WHERE date(opened_at) = date('now', 'localtime')
    ORDER BY id
""").fetchall()

if not rows:
    print("Nenhum trade encontrado para hoje.")
    input("Pressione Enter para sair...")
    exit()

print(f"\n{'='*70}")
print(f"Trades de HOJE que serão DELETADOS ({len(rows)} registros):")
print(f"{'='*70}")
for r in rows:
    pnl = f"{r['pnl_pts']:+.0f} pts / R${r['pnl_brl']:+.2f}" if r['pnl_pts'] is not None else "aberto"
    print(f"  #{r['id']} | {r['opened_at']} | {r['acao']} @ {r['entry_price']} | {r['close_reason'] or 'aberto'} | {pnl}")

print(f"{'='*70}")
# Avisa se há trade aberto
abertos = [r for r in rows if r['close_reason'] is None and r['pnl_pts'] is None]
if abertos:
    print(f"\n⚠️  ATENÇÃO: {len(abertos)} trade(s) ainda aberto(s)!")
    print("   Feche o trade no MT5 antes de limpar. Se prosseguir, o registro ficará órfão.")

confirm = input(f"\nConfirma a exclusão de {len(rows)} trade(s)? (s/N): ").strip().lower()

if confirm == "s":
    conn.execute("""
        DELETE FROM auto_trades
        WHERE date(opened_at) = date('now', 'localtime')
    """)
    conn.commit()
    print(f"\n✅ {len(rows)} trade(s) deletado(s) com sucesso.")
else:
    print("\n❌ Operação cancelada.")

conn.close()
input("Pressione Enter para sair...")
