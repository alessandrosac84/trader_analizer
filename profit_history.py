"""
profit_history.py — COLETOR DE HISTÓRICO DE FLUXO (Times&Trades) do WIN/WDO via ProfitDLL.

POR QUÊ (crítico nos 7 dias de licença): o valor único da ProfitDLL é o histórico
tick-a-tick com AGRESSÃO (comprador × vendedor) — dado que o MT5 não tem. Este script
puxa esse histórico dia a dia e salva em CSV, montando o DATASET que vamos usar pra
backtestar setups de FLUXO no WIN/WDO e provar (ou não) que vale assinar a DLL.

USO (rode durante a licença, quantos dias conseguir):
    python profit_history.py --dias 30              # últimos 30 pregões de WIN e WDO
    python profit_history.py --dias 60 --ticker WINV26,WDOV26
    python profit_history.py --de 2026-05-01 --ate 2026-08-15

SAÍDA: logs/flow_hist/<TICKER>_<YYYY-MM-DD>.csv  (colunas: ts,price,qty,tradetype,agg)
  tradetype: 2=agressor COMPRA, 3=agressor VENDA (ver TTradeType). agg: +1/-1/0.

Não envia ordem. Só leitura de histórico. Isolado — não toca em nenhum motor.
"""
import argparse
import csv
import os
import sys
import threading
import time
from ctypes import WINFUNCTYPE, byref, cast, c_int, c_int32, c_size_t, c_uint, c_wchar_p
from datetime import datetime, timedelta

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "profit_sdk"))
try:
    from profit_dll import initializeDll
    from profitTypes import (TConnectorTrade, TConnectorAssetIdentifierSafe,
                             TConnectorTradeCallback)
except Exception as exc:
    print("Falta profit_sdk/ (profitTypes.py + profit_dll.py + ProfitDLL.dll):", exc)
    sys.exit(1)

DLL_PATH = os.getenv("PROFIT_DLL_PATH", os.path.join(_HERE, "profit_sdk", "ProfitDLL.dll"))
ACTIVATION = os.getenv("PROFIT_ACTIVATION", "")
USER = os.getenv("PROFIT_USER", "")
PASSW = os.getenv("PROFIT_PASS", "")
EXCHANGE = os.getenv("PROFIT_EXCHANGE", "F")
OUT_DIR = os.path.join("logs", "flow_hist")

try:
    os.add_dll_directory(os.path.dirname(DLL_PATH))
except Exception:
    pass

NL_OK = 0
TC_LAST_PACKET = 2

profit_dll = initializeDll(DLL_PATH)

# ── estado de conexão ──────────────────────────────────────────────────────
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

# ── captura do dia corrente ────────────────────────────────────────────────
_rows = []                    # trades do dia sendo puxado
_done = threading.Event()     # setado quando chega o último pacote


@TConnectorTradeCallback
def historyTradeCallback(assetSafe, pTrade, flags):
    is_last = bool(flags & TC_LAST_PACKET)
    tr = TConnectorTrade(Version=0)
    try:
        if profit_dll.TranslateTrade(pTrade, byref(tr)) == NL_OK:
            d = tr.TradeDate
            ts = f"{d.wYear:04d}-{d.wMonth:02d}-{d.wDay:02d} {d.wHour:02d}:{d.wMinute:02d}:{d.wSecond:02d}.{d.wMilliseconds:03d}"
            tt = int(tr.TradeType)
            agg = 1 if tt == 2 else (-1 if tt == 3 else 0)
            _rows.append((ts, float(tr.Price), int(tr.Quantity), tt, agg))
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
            print("Conectado (login/market/ativação OK)."); return True
        time.sleep(0.5)
    print("Não conectou em 30s — cheque credenciais/ativação/licença."); return False


def _pull_day(ticker, day, timeout=180):
    """Puxa 1 pregão do ticker. Retorna nº de trades salvos (0 se dia sem dados)."""
    global _rows
    _rows = []
    _done.clear()
    ini = day.strftime("%d/%m/%Y") + " 09:00:00"
    fim = day.strftime("%d/%m/%Y") + " 18:30:00"
    ret = profit_dll.GetHistoryTrades(c_wchar_p(ticker), c_wchar_p(EXCHANGE),
                                      c_wchar_p(ini), c_wchar_p(fim))
    if ret < 0:
        print(f"  {ticker} {day:%Y-%m-%d}: GetHistoryTrades erro {ret}"); return 0
    if not _done.wait(timeout):
        print(f"  {ticker} {day:%Y-%m-%d}: timeout ({len(_rows)} parciais)")
    rows = list(_rows)
    if not rows:
        return 0
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"{ticker}_{day:%Y-%m-%d}.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ts", "price", "qty", "tradetype", "agg"])
        w.writerows(rows)
    return len(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker", default=os.getenv("PROFIT_TICKER", "WINV26") + "," + os.getenv("WDO_MT5_SYMBOL", "WDOU26"))
    ap.add_argument("--dias", type=int, default=30, help="nº de pregões p/ trás a partir de hoje")
    ap.add_argument("--de", default=None, help="AAAA-MM-DD (sobrepõe --dias)")
    ap.add_argument("--ate", default=None, help="AAAA-MM-DD")
    args = ap.parse_args()

    tickers = [t.strip() for t in args.ticker.split(",") if t.strip()]
    if args.de:
        d0 = datetime.strptime(args.de, "%Y-%m-%d").date()
        d1 = datetime.strptime(args.ate, "%Y-%m-%d").date() if args.ate else datetime.now().date()
        days = []
        d = d0
        while d <= d1:
            if d.weekday() < 5:  # só dias úteis
                days.append(d)
            d += timedelta(days=1)
    else:
        days = []
        d = datetime.now().date()
        while len(days) < args.dias:
            if d.weekday() < 5:
                days.append(d)
            d -= timedelta(days=1)
        days = list(reversed(days))

    if not _connect():
        return
    print(f"Coletando {len(days)} pregões × {tickers} → {OUT_DIR}/")
    total = 0
    for ticker in tickers:
        for day in days:
            path = os.path.join(OUT_DIR, f"{ticker}_{day:%Y-%m-%d}.csv")
            if os.path.exists(path):
                print(f"  {ticker} {day:%Y-%m-%d}: já existe, pulando")
                continue
            n = _pull_day(ticker, day)
            total += n
            if n:
                print(f"  {ticker} {day:%Y-%m-%d}: {n} trades salvos")
            time.sleep(0.3)  # respiro entre requisições
    print(f"FIM — {total} trades coletados em {OUT_DIR}/")
    try:
        profit_dll.DLLFinalize()
    except Exception:
        pass


if __name__ == "__main__":
    main()
