"""
fix_db.py — Corrige registros corrompidos e P&L errado do WDO no banco de dados.

Execute este script com o servidor PARADO:
    python fix_db.py

O que faz:
  1. Zera pnl_pts e pnl_brl de registros com P&L absurdo (> 10.000 pts)
  2. Recalcula pnl_brl dos trades WDO usando formula correta: R$10,00 / ponto
     (a formula antiga usava R$0,20/ponto — formula do WIN, gerando valores 50x menores)
  3. Fecha graciosamente trades "abertos" fantasmas (sem entrada no MT5)
     que foram criados pelo bug de recuperacao automatica
"""
import sqlite3
import os
import sys
from pathlib import Path

# Caminho do banco — ajustar se necessario
SCRIPT_DIR = Path(__file__).resolve().parent
DB_PATH = SCRIPT_DIR / "data" / "trade_ai.db"

if not DB_PATH.exists():
    print(f"ERRO: banco nao encontrado em {DB_PATH}")
    sys.exit(1)

print(f"Banco: {DB_PATH}")
conn = sqlite3.connect(str(DB_PATH))
conn.row_factory = sqlite3.Row
conn.execute("PRAGMA journal_mode=WAL")

# ─────────────────────────────────────────────────────────────────────────────
print("\n=== SITUACAO ATUAL ===")
todos = conn.execute("""
    SELECT id, tv_symbol, acao, entry_price, exit_price,
           pnl_pts, pnl_brl, volume, close_reason, closed_at, score, ai_veredito
    FROM auto_trades
    ORDER BY id
""").fetchall()

for r in todos:
    status = "FECHADO" if r["closed_at"] else "ABERTO"
    print(f"  id={r['id']:3d} [{status}] {r['tv_symbol']:<30} {str(r['acao']):<6} "
          f"entry={r['entry_price']} exit={r['exit_price']} "
          f"pnl_pts={r['pnl_pts']} pnl_brl={r['pnl_brl']} "
          f"vol={r['volume']} motivo={r['close_reason']} score={r['score']}")

# ─────────────────────────────────────────────────────────────────────────────
print("\n=== FIX 1: Zerando registros com P&L absurdo (|pnl_pts| > 10.000) ===")
corrompidos = conn.execute("""
    SELECT id, tv_symbol, pnl_pts, pnl_brl, entry_price, exit_price
    FROM auto_trades
    WHERE pnl_pts > 10000 OR pnl_pts < -10000
""").fetchall()

if not corrompidos:
    print("  Nenhum registro corrompido encontrado.")
else:
    for r in corrompidos:
        conn.execute("""
            UPDATE auto_trades
            SET pnl_pts = NULL,
                pnl_brl = NULL,
                close_reason = 'DADOS_CORROMPIDOS'
            WHERE id = ?
        """, (r["id"],))
        print(f"  ZERADO id={r['id']} {r['tv_symbol']} "
              f"pnl_pts_era={r['pnl_pts']} pnl_brl_era={r['pnl_brl']}")

conn.commit()
print(f"  Total zerados: {len(corrompidos)}")

# ─────────────────────────────────────────────────────────────────────────────
print("\n=== FIX 2: Recalculando P&L dos trades WDO (R$10/ponto) ===")
wdo_rows = conn.execute("""
    SELECT id, tv_symbol, acao, pnl_pts, pnl_brl, volume
    FROM auto_trades
    WHERE tv_symbol LIKE '%WDO%'
      AND pnl_pts IS NOT NULL
      AND ABS(CAST(pnl_pts AS REAL)) <= 10000
      AND closed_at IS NOT NULL
      AND close_reason NOT IN ('BLOQUEADO_IA', 'AGUARDADO_IA', 'DADOS_CORROMPIDOS')
""").fetchall()

if not wdo_rows:
    print("  Nenhum trade WDO fechado para recalcular.")
else:
    corrigidos_wdo = 0
    for r in wdo_rows:
        pts   = float(r["pnl_pts"] or 0)
        vol   = float(r["volume"]  or 1.0)
        brl_atual = float(r["pnl_brl"] or 0)
        brl_correto = round(pts * 10.0 * vol, 2)

        if abs(brl_correto - brl_atual) < 0.01:
            print(f"  id={r['id']} ja correto: pnl_pts={pts:.0f} pnl_brl={brl_atual:.2f}")
            continue

        conn.execute("UPDATE auto_trades SET pnl_brl = ? WHERE id = ?", (brl_correto, r["id"]))
        print(f"  CORRIGIDO id={r['id']} {r['tv_symbol']} "
              f"pnl_pts={pts:.0f} "
              f"pnl_brl_antigo=R${brl_atual:.2f} "
              f"pnl_brl_novo=R${brl_correto:.2f}")
        corrigidos_wdo += 1

    conn.commit()
    print(f"  Total recalculados: {corrigidos_wdo}")

# ─────────────────────────────────────────────────────────────────────────────
print("\n=== FIX 3: Fechando trades 'ABERTOS' sem preco de saida (registros fantasma) ===")
# Trades recuperados automaticamente (score=None, veredito=RECUPERADO) que
# nao sao o trade mais recente podem ser registros fantasma do bug de recuperacao
fantasmas = conn.execute("""
    SELECT id, tv_symbol, acao, entry_price, score, ai_veredito
    FROM auto_trades
    WHERE closed_at IS NULL
      AND ai_veredito = 'RECUPERADO'
      AND score IS NULL
    ORDER BY id
""").fetchall()

if not fantasmas:
    print("  Nenhum registro fantasma encontrado.")
else:
    # Manter apenas o mais recente por symbol; fechar os mais antigos como FANTASMA
    by_symbol = {}
    for r in fantasmas:
        sym = r["tv_symbol"]
        if sym not in by_symbol:
            by_symbol[sym] = []
        by_symbol[sym].append(r)

    for sym, recs in by_symbol.items():
        recs_sorted = sorted(recs, key=lambda x: x["id"])
        # Se houver mais de 1, fechar os antigos
        for old in recs_sorted[:-1]:
            conn.execute("""
                UPDATE auto_trades
                SET closed_at = datetime('now'),
                    exit_price = entry_price,
                    close_reason = 'FANTASMA_REMOVIDO',
                    pnl_pts = 0,
                    pnl_brl = 0.0
                WHERE id = ?
            """, (old["id"],))
            print(f"  FECHADO fantasma: id={old['id']} {sym} entry={old['entry_price']}")

    conn.commit()

# ─────────────────────────────────────────────────────────────────────────────
print("\n=== FIX 4: Fechando registros WDO orfaos (bug cross-symbol) ===")
# Quando o bug cross-symbol estava ativo, o sistema criava dois registros para uma
# posicao WDO: um correto (WDO1!) e um errado (WIN1! com preco WDO).
# O WIN1! foi fechado (gerando P&L absurdo), deixando o WDO1! preso como ABERTO.
# Como o servidor rastreou o fechamento no registro errado, estes WDO ficaram orfaos.
# Confirmacao: id=125 WIN1! entry=5156 (preco WDO) e id=124 WDO1! entry=5156.0 ABERTO.
wdo_orfaos = conn.execute("""
    SELECT id, tv_symbol, acao, entry_price, score
    FROM auto_trades
    WHERE closed_at IS NULL
      AND tv_symbol LIKE '%WDO%'
    ORDER BY id
""").fetchall()

if not wdo_orfaos:
    print("  Nenhum WDO orfao encontrado.")
else:
    for r in wdo_orfaos:
        conn.execute("""
            UPDATE auto_trades
            SET closed_at = datetime('now'),
                exit_price = entry_price,
                close_reason = 'ORFAO_BUG_CROSS_SYMBOL',
                pnl_pts = 0,
                pnl_brl = 0.0
            WHERE id = ?
        """, (r["id"],))
        print(f"  FECHADO orfao WDO: id={r['id']} entry={r['entry_price']} score={r['score']}")
    conn.commit()
    print(f"  Total WDO orfaos fechados: {len(wdo_orfaos)}")

# ─────────────────────────────────────────────────────────────────────────────
print("\n=== SITUACAO FINAL ===")
final = conn.execute("""
    SELECT id, tv_symbol, acao, entry_price, exit_price,
           pnl_pts, pnl_brl, volume, close_reason, closed_at
    FROM auto_trades
    ORDER BY id
""").fetchall()

total_pts = 0
total_brl = 0.0
for r in final:
    status = "FECHADO" if r["closed_at"] else "ABERTO"
    pts = r["pnl_pts"] or 0
    brl = float(r["pnl_brl"] or 0)
    if r["closed_at"] and r["close_reason"] not in ("BLOQUEADO_IA", "AGUARDADO_IA", "DADOS_CORROMPIDOS", "FANTASMA_REMOVIDO", "ORFAO_BUG_CROSS_SYMBOL"):
        total_pts += pts
        total_brl += brl
    print(f"  id={r['id']:3d} [{status}] {r['tv_symbol']:<30} {str(r['acao']):<6} "
          f"entry={r['entry_price']} pnl_pts={pts} pnl_brl=R${brl:.2f} motivo={r['close_reason']}")

print(f"\n  TOTAL P&L: {total_pts:+.0f} pts | R${total_brl:+.2f}")

conn.close()
print("\nConcluido! Pode iniciar o servidor agora.")
