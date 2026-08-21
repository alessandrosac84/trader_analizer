"""
coletar_profit_completo.py — COLETA ÚNICA e COMPLETA do fluxo do Profit (WIN+WDO).

Faz TUDO numa rodada só:
  • Varre a cadeia de contratos anteriores do WIN (V,Q,M,J,G,Z… meses pares) e do
    WDO (todos os meses), cada um na sua janela ativa → máximo de história/regimes.
  • Agrega em BARRAS DE 1 MINUTO com breakdown de agressão (buy_vol/sell_vol) —
    arquivos pequenos (um dia ≈ 500 linhas), não os milhões de ticks crus.
  • Pula o que já foi coletado (logs/flow_bars/).

SAÍDA: logs/flow_bars/<TICKER>_<AAAA-MM-DD>.csv
  colunas: minute, open, high, low, close, buy_vol, sell_vol, ntrades
  (buy_vol/sell_vol = volume com agressor de compra/venda; OHLC de todos os trades)

USO:  python coletar_profit_completo.py            # WIN + WDO, ~18 meses p/ trás
      python coletar_profit_completo.py --win 12 --wdo 18   # nº de contratos
Não envia ordem. Só leitura de histórico.
"""
import argparse
import csv
import os
import sys
import threading
import time
from ctypes import byref, c_int32, c_wchar_p, WINFUNCTYPE
from datetime import date, datetime, timedelta

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "profit_sdk"))
try:
    from profit_dll import initializeDll
    from profitTypes import TConnectorTrade, TConnectorTradeCallback
except Exception as exc:
    print("Falta profit_sdk/ (profitTypes.py + profit_dll.py + ProfitDLL.dll):", exc)
    sys.exit(1)

DLL_PATH = os.getenv("PROFIT_DLL_PATH", os.path.join(_HERE, "profit_sdk", "ProfitDLL.dll"))
ACTIVATION = os.getenv("PROFIT_ACTIVATION", "")
USER = os.getenv("PROFIT_USER", "")
PASSW = os.getenv("PROFIT_PASS", "")
EXCHANGE = os.getenv("PROFIT_EXCHANGE", "F")
OUT_DIR = os.path.join("logs", "flow_bars")
NL_OK = 0
TC_LAST_PACKET = 2

try:
    os.add_dll_directory(os.path.dirname(DLL_PATH))
except Exception:
    pass

profit_dll = initializeDll(DLL_PATH)

# ── códigos de contrato B3 ─────────────────────────────────────────────────
WIN_MONTHS = {2: "G", 4: "J", 6: "M", 8: "Q", 10: "V", 12: "Z"}   # índice: meses pares
WDO_MONTHS = {1: "F", 2: "G", 3: "H", 4: "J", 5: "K", 6: "M",
              7: "N", 8: "Q", 9: "U", 10: "V", 11: "X", 12: "Z"}   # dólar: todos


def _win_expiry(y, m):
    """Vencimento WIN: quarta-feira mais próxima do dia 15 do mês par."""
    d15 = date(y, m, 15)
    wd = d15.weekday()            # 0=seg … 2=qua
    delta = (2 - wd)              # diferença p/ quarta
    if delta > 3:
        delta -= 7
    if delta < -3:
        delta += 7
    return d15 + timedelta(days=delta)


def _wdo_expiry(y, m):
    """Vencimento WDO: 1º dia útil do mês do contrato."""
    d = date(y, m, 1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def _win_chain(n):
    """Últimos n contratos WIN (meses pares) com janela ativa (~65 dias antes do venc.)."""
    out = []
    today = date.today()
    y, m = today.year, today.month
    if m % 2 == 1:
        m += 1
    if m > 12:
        m = 2; y += 1
    if _win_expiry(y, m) < today:      # contrato do mês par atual já venceu → próximo
        m += 2
        if m > 12:
            m = 2; y += 1
    for _ in range(n):
        code = f"WIN{WIN_MONTHS[m]}{str(y)[-2:]}"
        exp = _win_expiry(y, m)
        out.append((code, exp - timedelta(days=65), exp + timedelta(days=1)))
        m -= 2
        if m < 2:
            m = 12; y -= 1
    return out


def _wdo_chain(n):
    """Últimos n contratos WDO (mensais) com janela ativa (~35 dias antes do venc.)."""
    out = []
    today = date.today()
    y, m = today.year, today.month + 1     # contrato ativo = mês seguinte
    if m > 12:
        m = 1; y += 1
    for _ in range(n):
        code = f"WDO{WDO_MONTHS[m]}{str(y)[-2:]}"
        exp = _wdo_expiry(y, m)
        out.append((code, exp - timedelta(days=35), exp + timedelta(days=1)))
        m -= 1
        if m < 1:
            m = 12; y -= 1
    return out


# ── conexão ────────────────────────────────────────────────────────────────
_conn = {"login": False, "market": False, "ativo": False}
_cbs = []


@WINFUNCTYPE(None, c_int32, c_int32)
def stateCallback(nType, nResult):
    if nType == 0:
        _conn["login"] = (nResult == 0)
    elif nType == 2:
        _conn["market"] = (nResult == 4)
    elif nType == 3:
        _conn["ativo"] = (nResult == 0)


_cbs.append(stateCallback)

# ── agregação em barras de 1 min ───────────────────────────────────────────
_bars = {}                    # "HH:MM" -> [o,h,l,c,buy,sell,n]
_done = threading.Event()


@TConnectorTradeCallback
def historyTradeCallback(assetSafe, pTrade, flags):
    is_last = bool(flags & TC_LAST_PACKET)
    tr = TConnectorTrade(Version=0)
    try:
        if profit_dll.TranslateTrade(pTrade, byref(tr)) == NL_OK:
            d = tr.TradeDate
            key = f"{d.wHour:02d}:{d.wMinute:02d}"
            px = float(tr.Price)
            qt = int(tr.Quantity)
            tt = int(tr.TradeType)
            b = _bars.get(key)
            if b is None:
                _bars[key] = [px, px, px, px, 0, 0, 0]
                b = _bars[key]
            b[1] = max(b[1], px)     # high
            b[2] = min(b[2], px)     # low
            b[3] = px                # close
            b[6] += 1                # ntrades
            if tt == 2:
                b[4] += qt           # buy_vol (agressor compra)
            elif tt == 3:
                b[5] += qt           # sell_vol (agressor venda)
    except Exception:
        pass
    if is_last:
        _done.set()


_cbs.append(historyTradeCallback)


def _connect():
    if not (ACTIVATION and USER and PASSW):
        print("Faltam credenciais no .env (PROFIT_ACTIVATION/USER/PASS)."); sys.exit(1)
    profit_dll.DLLInitializeMarketLogin(
        c_wchar_p(ACTIVATION), c_wchar_p(USER), c_wchar_p(PASSW),
        stateCallback, None, None, None, None, None, None, None)
    profit_dll.SetHistoryTradeCallbackV2(historyTradeCallback)
    print("Conectando à ProfitDLL…")
    for _ in range(60):
        if _conn["login"] and _conn["market"] and _conn["ativo"]:
            print("Conectado (login/market/ativação OK).\n"); return True
        time.sleep(0.5)
    print("Não conectou em 30s — cheque credenciais/ativação/licença."); return False


def _pull_day(ticker, day, timeout=300):
    global _bars
    _bars = {}
    _done.clear()
    ini = day.strftime("%d/%m/%Y") + " 09:00:00"
    fim = day.strftime("%d/%m/%Y") + " 18:30:00"
    ret = profit_dll.GetHistoryTrades(c_wchar_p(ticker), c_wchar_p(EXCHANGE),
                                      c_wchar_p(ini), c_wchar_p(fim))
    if ret < 0:
        return None            # sem dados/contrato inativo nessa data
    # Espera INTELIGENTE: não trava 5 min num dia vazio.
    t0 = time.time(); last_n = 0; idle = 0
    while not _done.is_set():
        time.sleep(1)
        cur = len(_bars)
        if cur > last_n:
            last_n = cur; idle = 0
        else:
            idle += 1
        if cur == 0 and (time.time() - t0) > 12:
            break              # nada chegou em 12s → dia vazio/feriado
        if idle >= 15:
            break              # dados pararam de chegar há 15s → pronto
        if (time.time() - t0) > timeout:
            break
    if not _bars:
        return 0
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"{ticker}_{day:%Y-%m-%d}.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["minute", "open", "high", "low", "close", "buy_vol", "sell_vol", "ntrades"])
        for k in sorted(_bars):
            o, h, l, c, bv, sv, n = _bars[k]
            w.writerow([k, o, h, l, c, bv, sv, n])
    return sum(v[6] for v in _bars.values())


def _convert_existing_raw():
    """Converte arquivos crus já coletados (logs/flow_hist/*.csv) em barras, para
    não re-baixar dias que você já tem. Roda no disco local (rápido)."""
    import glob
    raws = glob.glob(os.path.join("logs", "flow_hist", "*.csv"))
    if not raws:
        return
    os.makedirs(OUT_DIR, exist_ok=True)
    conv = 0
    for f in raws:
        name = os.path.basename(f)
        outp = os.path.join(OUT_DIR, name)
        if os.path.exists(outp):
            continue
        bars = {}
        try:
            with open(f, encoding="utf-8") as fh:
                r = csv.DictReader(fh)
                for row in r:
                    try:
                        key = row["ts"][11:16]
                        px = float(row["price"]); qt = int(row["qty"]); tt = int(row["tradetype"])
                    except Exception:
                        continue
                    b = bars.get(key)
                    if b is None:
                        bars[key] = [px, px, px, px, 0, 0, 0]; b = bars[key]
                    b[1] = max(b[1], px); b[2] = min(b[2], px); b[3] = px; b[6] += 1
                    if tt == 2:
                        b[4] += qt
                    elif tt == 3:
                        b[5] += qt
            if bars:
                with open(outp, "w", newline="", encoding="utf-8") as fo:
                    w = csv.writer(fo)
                    w.writerow(["minute", "open", "high", "low", "close", "buy_vol", "sell_vol", "ntrades"])
                    for k in sorted(bars):
                        o, h, l, c, bv, sv, n = bars[k]
                        w.writerow([k, o, h, l, c, bv, sv, n])
                conv += 1
        except Exception as exc:
            print(f"  (conversão {name} falhou: {exc})")
    if conv:
        print(f"Convertidos {conv} arquivos crus já existentes → barras.\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--win", type=int, default=12, help="nº de contratos WIN p/ trás")
    ap.add_argument("--wdo", type=int, default=18, help="nº de contratos WDO p/ trás")
    args = ap.parse_args()

    _convert_existing_raw()

    chain = _win_chain(args.win) + _wdo_chain(args.wdo)
    print("Contratos a coletar (código | janela):")
    for code, d0, d1 in chain:
        print(f"  {code}: {d0} → {d1}")
    print()

    if not _connect():
        return
    total_days = total_trades = 0
    empty_streak = {}
    hoje = date.today()
    for code, d0, d1 in chain:
        # não pede datas futuras (a janela do contrato atual passa de hoje)
        d1 = min(d1, hoje)
        if d0 > d1:
            print(f"── {code}: janela ainda no futuro, pulando ──"); continue
        print(f"── {code} ({d0} → {d1}) ──", flush=True)
        d = d0
        have = pulled = novos = 0
        while d <= d1:
            if d.weekday() < 5:
                path = os.path.join(OUT_DIR, f"{code}_{d:%Y-%m-%d}.csv")
                if os.path.exists(path):
                    have += 1
                else:
                    n = _pull_day(code, d)
                    if n:
                        total_days += 1; total_trades += n; novos += 1
                        print(f"   {d:%Y-%m-%d}: {n:,} trades → barras", flush=True)
                    pulled += 1
                    time.sleep(0.2)
            d += timedelta(days=1)
        print(f"   [{code}] já tinha {have} · baixados {novos} novos", flush=True)
        if have == 0 and novos == 0:
            print(f"   (sem dados p/ {code} — provavelmente além do limite da licença)", flush=True)
    print(f"\nFIM — {total_days} pregões novos, {total_trades:,} trades agregados em {OUT_DIR}/")
    try:
        profit_dll.DLLFinalize()
    except Exception:
        pass


if __name__ == "__main__":
    main()
