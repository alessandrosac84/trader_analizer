"""
services/trade_gate.py — Trava de trades por ativo (safety switch server-side).

Motivo: o botão "TRADES: OFF" do painel era só client-side; o backend aceitava
ordem de qualquer fonte. Aqui o servidor passa a RECUSAR abrir trade de um ativo
marcado OFF — não importa a origem (painel novo, dashboard clássico, outra aba).

⚠️ SOMENTE PROTEÇÃO: nunca abre nem altera trade. Só pode BLOQUEAR. Não toca no
motor de análise/execução do Monitor MT5.

Modelo por TOKEN (WIN, WDO, ...): um símbolo é bloqueado se contém um token
desativado. Monitor MT5 sobe com WIN/WDO DESLIGADOS — auto no boot é só
V7 + Crypto; o painel liga o Monitor sob demanda.
"""
import threading

_lock = threading.Lock()
# Boot seguro: Monitor clássico OFF até o usuário ligar TRADES no painel.
_disabled_tokens: set = {"WIN", "WDO"}


def set_token(token: str, enabled: bool) -> None:
    """Liga/desliga trades para um token de ativo (ex.: 'WIN')."""
    t = (token or "").strip().upper()
    if not t:
        return
    with _lock:
        if enabled:
            _disabled_tokens.discard(t)
        else:
            _disabled_tokens.add(t)


def symbol_enabled(symbol: str) -> bool:
    """True se o símbolo pode operar. False se casar com algum token desativado."""
    s = (symbol or "").upper()
    with _lock:
        return not any(tok in s for tok in _disabled_tokens)


def disabled_tokens() -> list:
    with _lock:
        return sorted(_disabled_tokens)
