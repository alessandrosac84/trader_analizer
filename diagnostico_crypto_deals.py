"""
diagnostico_crypto_deals.py — MOSTRA A VERDADE do que o MT5 devolve para o
Monitor Crypto, para descobrir por que um fechamento não entra na lista.

Roda NA SUA MÁQUINA, com o MetaTrader 5 (ICMarkets) aberto e logado.
  python diagnostico_crypto_deals.py
  python diagnostico_crypto_deals.py --hours 48

Ele NÃO altera nada. Só lê o histórico e imprime:
  - Cada deal de SAÍDA (fechamento) das últimas N horas: símbolo EXATO, magic,
    lucro, horário e position_id.
  - Se o filtro atual do reconciliador capturaria aquele deal (e por quê não).
  - O que o get_symbols() do crypto retorna.
  - As últimas linhas do crypto_trades.csv, pra comparar com o que já foi gravado.
"""
import argparse
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

try:
    import MetaTrader5 as mt5
except ImportError:
    print("MetaTrader5 não instalado. Rode na máquina Windows com o MT5 aberto.")
    sys.exit(1)

MAGIC_CRYPTO = 770077
SCALPER_MAGIC = 20260506


def _init():
    if mt5.terminal_info() is not None:
        return True

    def _g(k, d=""):
        return os.getenv("MT5_CRYPTO_" + k) or os.getenv("MT5_" + k, d)
    login = int(_g("LOGIN", "0") or 0)
    password = _g("PASSWORD", "")
    server = _g("SERVER", "")
    path = _g("PATH", "")
    kw = {}
    if path and os.path.exists(path):
        kw["path"] = path
    if login and password and server:
        kw.update(login=login, password=password, server=server)
    return mt5.initialize(**kw)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=int, default=24)
    args = ap.parse_args()

    if not _init():
        print("❌ MT5 não inicializou:", mt5.last_error())
        print("   Abra o MetaTrader 5 (ICMarkets), logue e confira MT5_CRYPTO_* no .env")
        sys.exit(1)

    acc = mt5.account_info()
    print("═" * 70)
    print(f"  CONTA CONECTADA: {getattr(acc,'login','?')} · {getattr(acc,'server','?')} · {getattr(acc,'currency','?')}")
    print("═" * 70)

    # o que o crypto considera "seus" símbolos
    try:
        from services.crypto_config import get_symbols
        syms = set(s.upper() for s in (get_symbols() or []))
    except Exception as e:
        syms = set()
        print("  (não consegui ler get_symbols():", e, ")")
    print(f"  get_symbols() do Monitor Crypto: {sorted(syms)}")

    # deals do período
    frm = datetime.now() - timedelta(hours=args.hours)
    to = datetime.now() + timedelta(days=1)   # folga p/ fuso do servidor (ver crypto_service)
    deals = mt5.history_deals_get(frm, to) or []
    outs = [d for d in deals if getattr(d, "entry", None) == mt5.DEAL_ENTRY_OUT]
    print(f"\n  Deals de SAÍDA (fechamentos) nas últimas {args.hours}h: {len(outs)}")
    print("  " + "-" * 66)
    print(f"  {'hora':<20}{'símbolo':<12}{'magic':<10}{'lucro':>10}  captura?")
    print("  " + "-" * 66)

    for d in sorted(outs, key=lambda x: x.time):
        t = datetime.fromtimestamp(d.time).strftime("%Y-%m-%d %H:%M:%S")
        sym = (d.symbol or "").upper()
        base = sym.split(".")[0]
        mag = getattr(d, "magic", 0)
        prof = float(d.profit)
        # replica o filtro ATUAL do reconciliador
        if mag == SCALPER_MAGIC:
            cap = "NÃO (é do Scalper)"
        else:
            sym_ok = bool(syms) and (sym in syms or base in syms)
            cap = "SIM" if (mag == MAGIC_CRYPTO or sym_ok) else f"NÃO (símbolo '{sym}' fora da lista, magic {mag})"
        print(f"  {t:<20}{sym:<12}{mag:<10}{prof:>10.2f}  {cap}")

    print("  " + "-" * 66)
    print("\n  → Se um fechamento que você viu no MT5 aparece aqui com 'NÃO', a causa")
    print("    está no MOTIVO ao lado (símbolo com sufixo, magic diferente, etc.).")
    print("    Se ele NEM aparece na lista acima, o MT5 não devolveu esse deal na")
    print("    janela — aí é fuso/tempo. Me manda este print e eu ajusto certeiro.")

    # comparação com o CSV já gravado
    csvp = Path(__file__).parent / "logs" / "crypto_trades.csv"
    if csvp.exists():
        linhas = csvp.read_text(encoding="utf-8").strip().splitlines()
        print(f"\n  crypto_trades.csv — últimas 6 linhas (de {len(linhas)-1} trades):")
        for ln in linhas[-6:]:
            print("    ", ln)

    mt5.shutdown()


if __name__ == "__main__":
    main()
