"""
profit_flow.py — Coletor de AGRESSÃO (Times&Trades) do WIN via ProfitDLL.

POR QUÊ: o feed do MT5 da XP não entrega Times&Trades nem book. A ProfitDLL
(Nelogica) entrega. Este coletor conecta na ProfitDLL, assina o WIN, calcula o
saldo de agressão comprador × vendedor dos últimos N segundos e grava em
data/profit_aggression.json. O Opening Engine LÊ esse arquivo (não carrega a DLL),
então um eventual crash da DLL mata só ESTE processo, nunca os motores de trade.

⚠️ RODA EM PROCESSO SEPARADO (não é importado pelo app). Inicie com:
      python profit_flow.py
   (ou o atalho iniciar_PROFIT_FLOW.bat)

PRÉ-REQUISITOS (o que só a Nelogica fornece):
  1. ProfitDLL.dll  → pasta Win64. Como obter: artigo "Como obter acesso à
     ProfitDLL" (ajuda.nelogica.com.br). Aponte o caminho em PROFIT_DLL_PATH.
  2. profitTypes.py → structs OFICIAIS (TConnectorTrade / TConnectorAssetIdentifier).
     Vem no exemplo GRATUITO "dashboard-players.zip" (7 kB) do artigo
     "Do tick ao dashboard…". Coloque em profit_sdk/profitTypes.py.
     Uso as structs oficiais de propósito: o layout de bytes do TConnectorTrade é
     crítico e chutá-lo corromperia a memória. Por isso EXIJO o arquivo oficial.
  3. Credenciais Nelogica (as mesmas do Profit) no .env:
       PROFIT_ACTIVATION=  (chave de ativação da DLL)
       PROFIT_USER=        (usuário Nelogica)
       PROFIT_PASS=        (senha de login — NÃO a de roteamento)
       PROFIT_DLL_PATH=C:\\ProfitDLL\\Win64\\ProfitDLL.dll
       PROFIT_TICKER=WINV26   (contrato atual; default = WIN_MT5_SYMBOL)
"""
import json
import os
import sys
import threading
import time
from collections import deque
from ctypes import (WINFUNCTYPE, WinDLL, POINTER, byref,
                    c_int, c_uint, c_size_t, c_wchar_p)
from datetime import datetime

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

OUT_PATH = os.path.join("data", "profit_aggression.json")
WINDOW_SEC = 120          # janela do saldo de agressão
WRITE_EVERY = 1.0         # grava o JSON a cada 1s

# ── Structs OFICIAIS (do exemplo gratuito da Nelogica) ─────────────────────
try:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "profit_sdk"))
    from profitTypes import TConnectorTrade, TConnectorAssetIdentifier  # type: ignore
except Exception:
    print("=" * 68)
    print("  FALTA o profitTypes.py OFICIAL (structs da ProfitDLL).")
    print("  1) Baixe o exemplo gratuito 'dashboard-players.zip' (7 kB) do artigo")
    print("     'Do tick ao dashboard' em ajuda.nelogica.com.br")
    print("  2) Copie o profitTypes.py para  profit_sdk/profitTypes.py")
    print("  (uso o oficial de propósito — o layout do struct é crítico)")
    print("=" * 68)
    sys.exit(1)

# ── Config ─────────────────────────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
DLL_PATH = os.getenv("PROFIT_DLL_PATH",
                     os.path.join(_HERE, "profit_sdk", "ProfitDLL.dll"))
# Windows/Python 3.8+: garante que dependências da DLL sejam encontradas na pasta dela.
try:
    os.add_dll_directory(os.path.dirname(DLL_PATH))
except Exception:
    pass
ACTIVATION = os.getenv("PROFIT_ACTIVATION", "")
USER = os.getenv("PROFIT_USER", "")
PASSW = os.getenv("PROFIT_PASS", "")
TICKER = os.getenv("PROFIT_TICKER", os.getenv("WIN_MT5_SYMBOL", "WINV26")).strip()
EXCHANGE = os.getenv("PROFIT_EXCHANGE", "F")   # F = BM&F/B3 futuros

if not (ACTIVATION and USER and PASSW):
    print("Faltam credenciais no .env: PROFIT_ACTIVATION / PROFIT_USER / PROFIT_PASS")
    sys.exit(1)

# ── Estados de conexão (do artigo Ecossistema) ─────────────────────────────
CONNECTION_STATE_LOGIN, CONNECTION_STATE_MARKET_DATA, CONNECTION_STATE_MARKET_LOGIN = 0, 2, 3
LOGIN_CONNECTED, MARKET_CONNECTED, CONNECTION_ACTIVATE_VALID = 0, 4, 0

_state = {"login": threading.Event(), "market": threading.Event(), "ativacao": threading.Event()}
_trades = deque()            # (epoch, qty, side)  side: +1 compra, -1 venda
_lock = threading.Lock()
_callbacks = []              # mantém refs vivas (anti-GC) — CRÍTICO

dll = WinDLL(DLL_PATH)
dll.TranslateTrade.argtypes = [c_size_t, POINTER(TConnectorTrade)]
dll.TranslateTrade.restype = c_int


# state callback: (nConnStateType, nResult)
@WINFUNCTYPE(None, c_int, c_int)
def state_callback(state_type, result):
    if state_type == CONNECTION_STATE_LOGIN and result == LOGIN_CONNECTED:
        _state["login"].set()
    elif state_type == CONNECTION_STATE_MARKET_DATA and result == MARKET_CONNECTED:
        _state["market"].set()
    elif state_type == CONNECTION_STATE_MARKET_LOGIN and result == CONNECTION_ACTIVATE_VALID:
        _state["ativacao"].set()


# trade V2: (asset por valor, ponteiro opaco do trade, flags)
TradeCallbackV2 = WINFUNCTYPE(None, TConnectorAssetIdentifier, c_size_t, c_uint)


def _on_trade(asset, p_trade, flags):
    trade = TConnectorTrade()
    trade.Version = 0
    try:
        if dll.TranslateTrade(p_trade, byref(trade)) == 0:
            qty = int(trade.Quantity)
            side = 1 if int(trade.TradeType) == 2 else (-1 if int(trade.TradeType) == 3 else 0)
            if side and qty > 0:
                with _lock:
                    _trades.append((time.time(), qty, side))
    except Exception:
        pass


cb_trade = TradeCallbackV2(_on_trade)
_callbacks.append(cb_trade)
_callbacks.append(state_callback)


def _aggregate_and_write():
    while True:
        try:
            now = time.time()
            cutoff = now - WINDOW_SEC
            buy = sell = 0
            with _lock:
                while _trades and _trades[0][0] < cutoff:
                    _trades.popleft()
                for _, qty, side in _trades:
                    if side > 0:
                        buy += qty
                    else:
                        sell += qty
            tot = buy + sell
            ratio = (buy - sell) / tot if tot > 0 else 0.0
            os.makedirs("data", exist_ok=True)
            tmp = OUT_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"ts": now, "ticker": TICKER, "buy": buy, "sell": sell,
                           "ratio": round(ratio, 4), "window_sec": WINDOW_SEC,
                           "hora": datetime.now().strftime("%H:%M:%S")}, f)
            os.replace(tmp, OUT_PATH)
        except Exception as exc:
            print("agg erro:", exc)
        time.sleep(WRITE_EVERY)


def main():
    print(f"ProfitFlow: conectando à ProfitDLL ({DLL_PATH}) — ticker {TICKER}/{EXCHANGE}")
    # Market Data apenas: state + (demais callbacks None) — trades vêm via V2.
    dll.DLLInitializeMarketLogin.restype = c_int
    r = dll.DLLInitializeMarketLogin(
        c_wchar_p(ACTIVATION), c_wchar_p(USER), c_wchar_p(PASSW),
        state_callback, None, None, None, None, None, None, None)
    if r != 0:
        print(f"DLLInitializeMarketLogin retornou {r} (esperado 0). Abortando.")
        sys.exit(1)
    # aguarda login + market + ativação
    for name in ("login", "market", "ativacao"):
        if not _state[name].wait(30):
            print(f"Conexão '{name}' não confirmou em 30s — verifique credenciais/ativação.")
            sys.exit(1)
    print("Conectado. Registrando callback de trade e assinando o WIN…")
    dll.SetTradeCallbackV2(cb_trade)
    dll.SubscribeTicker(c_wchar_p(TICKER), c_wchar_p(EXCHANGE))
    threading.Thread(target=_aggregate_and_write, daemon=True).start()
    print(f"ProfitFlow ATIVO — gravando saldo de agressão em {OUT_PATH} a cada {WRITE_EVERY}s.")
    try:
        while True:
            time.sleep(5)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            dll.DLLFinalize()
        except Exception:
            pass


if __name__ == "__main__":
    main()
