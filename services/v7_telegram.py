"""
v7_telegram.py — Comandos do Telegram para o Monitor V7 (WIN).

Comandos:
  /v7              — status completo (auto on/off, regime, sinal, posição, dia)
  /v7 hoje         — resultado do dia
  /v7 semana       — resultado da semana
  /v7 mes          — resultado do mês
  /v7 on / /v7 off — liga/desliga o auto-trade
  /v7 ia consultiva|filtro|off — modo da IA

Lê o estado direto do runtime do V7 (services.v7_engine_runtime).
"""
import logging

logger = logging.getLogger(__name__)


def _reply(token, chat_id, text):
    # mesmo padrão do crypto/scalper: envia para o chat configurado no .env
    try:
        from services.telegram_notifier import send_async
        send_async(text)
    except Exception as exc:
        logger.warning("v7_telegram reply falhou: %s", exc)


def _fmt_status(rt) -> str:
    snap = rt.snapshot()
    sig = snap.get("signal") or {}
    pos = snap.get("position")
    t = snap.get("today") or {}
    ai = snap.get("last_ai") or {}
    auto = "🟢 LIGADO" if snap.get("enabled") else "🔴 desligado"
    demo = ("DEMO " + str(snap.get("account") or "")) if snap.get("is_demo") \
        else ("⚠️ NÃO-DEMO" if snap.get("is_demo") is False else "conta —")

    lines = [
        f"⚡ <b>Monitor V7 · {snap.get('symbol')}</b>",
        f"Auto-trade: {auto}   ({demo})",
        f"Status: {snap.get('status')}",
        f"Contratos: base×{snap.get('vol_mult')}  ·  IA: {snap.get('ai_mode')}",
        "",
        f"<b>Contexto</b> (aval. {snap.get('last_eval_ts') or '—'})",
        f"  Regime: {sig.get('regime') or '—'} · Janela: {sig.get('janela') or '—'} · 1h: {sig.get('htf_trend') or '—'}",
    ]
    if sig.get("acao") and sig.get("acao") != "NEUTRO":
        lines.append(f"  Sinal: {sig.get('setup')} {sig.get('acao')} · "
                     f"entr {sig.get('entrada')} / stop {sig.get('stop')} / alvo {sig.get('tp1')}")
        if ai.get("veredito"):
            lines.append(f"  IA: {ai.get('veredito')} ({ai.get('confianca') or '?'}%)")
    else:
        lines.append("  Sinal: aguardando setup (GAP_FADE 09:15 · ORB 10:00-10:45)")

    lines.append("")
    if pos:
        lines.append(f"<b>Posição ABERTA</b> — {pos.get('setup')} {pos.get('dir')}")
        lines.append(f"  {pos.get('volume')}c @ {pos.get('entry')} · preço {pos.get('price')} · SL {pos.get('sl')}")
        lines.append(f"  P&L: R$ {pos.get('profit_brl'):+.2f} ({pos.get('r_now'):+.2f} R)"
                     + ("  · parcial ✓" if pos.get("partial_done") else ""))
    else:
        lines.append("<b>Posição:</b> nenhuma aberta")

    lines.append("")
    lines.append(f"<b>Hoje:</b> {t.get('trades',0)} trade(s) · "
                 f"{t.get('wins',0)}✅/{t.get('losses',0)}❌ · "
                 f"R$ {t.get('pnl_brl',0):+.2f} ({t.get('pnl_r',0):+.2f} R)")
    lines.append(f"Último evento: {snap.get('last_exec_msg') or '—'}")
    return "\n".join(lines)


def _fmt_period(rng: str) -> str:
    from services.v7_engine_runtime import period_stats
    p = period_stats(rng)
    nome = {"day": "HOJE", "week": "SEMANA", "month": "MÊS", "all": "TOTAL"}.get(rng, rng)
    if not p or p.get("trades", 0) == 0:
        return f"⚡ <b>V7 · {nome}</b>\nNenhum trade no período."
    setups = "\n".join(
        f"  {k}: {v['n']} trade(s) · {v['pnl_r']:+.2f} R"
        for k, v in (p.get("by_setup") or {}).items())
    return (f"⚡ <b>V7 · {nome}</b>\n"
            f"Trades: {p['trades']} · win {p['win_rate']}% · PF {p['pf']}\n"
            f"P&L: R$ {p['pnl_brl']:+.2f} ({p['pnl_r']:+.2f} R) · maxDD {p['max_dd_r']} R\n"
            f"Por setup:\n{setups}")


def handle_v7_command(token: str, chat_id: str, text: str) -> None:
    from services.v7_engine_runtime import runtime
    runtime.ensure_started()
    parts = (text or "").strip().lower().split()
    arg = parts[1] if len(parts) > 1 else ""

    if arg in ("hoje", "dia", "day"):
        _reply(token, chat_id, _fmt_period("day"))
    elif arg in ("semana", "week"):
        _reply(token, chat_id, _fmt_period("week"))
    elif arg in ("mes", "mês", "month"):
        _reply(token, chat_id, _fmt_period("month"))
    elif arg in ("tudo", "all", "total"):
        _reply(token, chat_id, _fmt_period("all"))
    elif arg in ("on", "ligar", "ativar"):
        runtime.set_enabled(True)
        _reply(token, chat_id, "⚡ V7 auto-trade LIGADO.")
    elif arg in ("off", "desligar", "pausar"):
        runtime.set_enabled(False)
        _reply(token, chat_id, "⚡ V7 auto-trade DESLIGADO.")
    elif arg == "ia" and len(parts) > 2:
        modo = {"consultiva": "advisory", "advisory": "advisory",
                "filtro": "gate", "gate": "gate", "off": "off"}.get(parts[2])
        if modo:
            runtime.set_config(ai_mode=modo)
            _reply(token, chat_id, f"⚡ V7 IA agora em modo: {modo}")
        else:
            _reply(token, chat_id, "Use: /v7 ia consultiva | filtro | off")
    elif arg in ("", "status"):
        _reply(token, chat_id, _fmt_status(runtime))
    else:
        _reply(token, chat_id,
               "⚡ <b>Comandos V7</b>\n"
               "/v7 — status completo\n"
               "/v7 hoje | semana | mes | tudo — resultados\n"
               "/v7 on | off — liga/desliga auto-trade\n"
               "/v7 ia consultiva | filtro | off — modo da IA")
