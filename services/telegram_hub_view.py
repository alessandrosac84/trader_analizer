"""
telegram_hub_view.py — visão unificada para comandos genéricos do Telegram.

/status e /trade devem mostrar TODOS os motores (Monitor MT5, Scalper, Hub B3
V7/WinGo/EOD, Crypto), não só o Monitor legado.
Cada coletor é best-effort e isolado (falha de um não zera os outros).
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)
_LOG = Path(__file__).resolve().parent.parent / "logs"
_BRT_FMT = "%d/%m %H:%M"


def _f(v, default=0.0):
    try:
        return float(v)
    except Exception:
        return default


def _brl(v) -> str:
    try:
        return f"R${float(v):+.2f}"
    except Exception:
        return "—"


def _money_usd(v, rate=5.2) -> str:
    u = _f(v)
    return f"$ {u:+.2f} · R$ {u * rate:+.2f}"


# ── Coletores de P&L do dia ─────────────────────────────────────────────────

def _monitor_day() -> dict:
    try:
        from services.trade_log import auto_trades_stats
        s = auto_trades_stats(today_only=True) or {}
        return {
            "label": "🖥 Monitor MT5",
            "on": None,
            "trades": int(s.get("total") or 0),
            "wins": int(s.get("wins") or 0),
            "losses": int(s.get("losses") or 0),
            "pnl_brl": _f(s.get("pnl_total_brl")),
            "extra": f"IA bloqueou {s['bloqueados_ia']}" if s.get("bloqueados_ia") else "",
        }
    except Exception as exc:
        return {"label": "🖥 Monitor MT5", "error": str(exc)}


def _scalper_day() -> dict:
    try:
        from blueprints.scalper_bp import _session, _auto
        trades = int(_session.get("trades") or 0)
        wins = int(_session.get("wins") or 0)
        losses = int(_session.get("losses") or 0)
        return {
            "label": "⚡ Scalper",
            "on": bool(_auto.get("enabled")),
            "trades": trades,
            "wins": wins,
            "losses": losses,
            "pnl_brl": _f(_session.get("pnl")),
        }
    except Exception as exc:
        return {"label": "⚡ Scalper", "error": str(exc)}


def _hub_b3_day() -> dict:
    try:
        from services.b3_trade_hub import unified_period
        p = unified_period("day") or {}
        return {
            "label": "🏗 Hub B3 (V7+WinGo+EOD)",
            "on": None,
            "trades": int(p.get("trades") or 0),
            "wins": int(p.get("wins") or 0),
            "losses": int(p.get("losses") or 0),
            "pnl_brl": _f(p.get("pnl_brl")),
            "extra": f"WR {p.get('win_rate', 0)}%",
        }
    except Exception as exc:
        return {"label": "🏗 Hub B3 (V7+WinGo+EOD)", "error": str(exc)}


def _v7_flag() -> str:
    try:
        from services.v7_engine_runtime import runtime
        runtime.ensure_started()
        return "ON" if runtime.enabled else "OFF"
    except Exception:
        return "?"


def _wingo_flag() -> str:
    try:
        en = os.getenv("WIN_GO_ENABLED", "1").strip() != "0"
        return "ON" if en else "OFF"
    except Exception:
        return "?"


def _crypto_day() -> dict:
    try:
        from services.crypto_telegram import _rows_period, _usdbrl, _live_positions
        rows = _rows_period("day")
        wins = sum(1 for r in rows if _f(r.get("profit")) > 0)
        losses = sum(1 for r in rows if _f(r.get("profit")) < 0)
        pnl_usd = round(sum(_f(r.get("profit")) for r in rows), 2)
        rate = _usdbrl()
        open_n = len(_live_positions() or [])
        on = None
        hb = _load_hb("crypto_runtime")
        if hb and isinstance(hb.get("motivos"), dict):
            offs = [v for v in hb["motivos"].values() if "OFF" in str(v).upper()]
            on = len(offs) < len(hb["motivos"]) if hb["motivos"] else None
        extra = _money_usd(pnl_usd, rate) if rows else ""
        if open_n:
            extra = (extra + " · " if extra else "") + f"{open_n} aberta(s)"
        return {
            "label": "🪙 Crypto",
            "on": on,
            "trades": len(rows),
            "wins": wins,
            "losses": losses,
            "pnl_brl": round(pnl_usd * rate, 2),
            "extra": extra,
            "open_n": open_n,
        }
    except Exception as exc:
        return {"label": "🪙 Crypto", "error": str(exc)}


def _load_hb(name: str) -> dict | None:
    try:
        p = _LOG / f"hb_{name}.json"
        if not p.exists():
            return None
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def format_status_message(monitor_auto_on: bool) -> str:
    """Texto completo para /status."""
    blocks = [_monitor_day(), _scalper_day(), _hub_b3_day(), _crypto_day()]
    mon_on = "✅ ATIVO" if monitor_auto_on else "🛑 PAUSADO"
    lines = [
        "📊 <b>STATUS GERAL</b>",
        "━━━━━━━━━━━━━━━━━━",
        f"🖥 Monitor MT5 auto: <b>{mon_on}</b>",
        f"⚡ V7: <b>{_v7_flag()}</b> · WinGo: <b>{_wingo_flag()}</b>",
        "",
        "<b>Resultado hoje por motor</b>",
    ]
    total_pnl = 0.0
    any_ok = False
    for b in blocks:
        if b.get("error"):
            lines.append(f"{b['label']}: ⚠️ {b['error'][:80]}")
            continue
        any_ok = True
        n = b.get("trades") or 0
        pnl = _f(b.get("pnl_brl"))
        total_pnl += pnl
        on = b.get("on")
        on_txt = "" if on is None else (" · auto ON" if on else " · auto OFF")
        if n == 0:
            lines.append(f"{b['label']}: sem trades{on_txt}")
        else:
            icon = "📈" if pnl >= 0 else "📉"
            extra = f" · {b['extra']}" if b.get("extra") else ""
            lines.append(
                f"{b['label']}: {n} trades · ✅{b.get('wins', 0)} ❌{b.get('losses', 0)} · "
                f"{icon} {_brl(pnl)}{extra}{on_txt}"
            )
    if any_ok:
        icon = "🟢" if total_pnl > 0 else ("🔴" if total_pnl < 0 else "⚪")
        lines += ["", "━━━━━━━━━━━━━━━━━━",
                  f"{icon} <b>CONSOLIDADO:</b> {_brl(total_pnl)}"]
    lines.append("\nDetalhes: /sc_status · /v7 · /cy_status · /trade")
    return "\n".join(lines)


# ── Posições abertas ────────────────────────────────────────────────────────

def _collect_monitor_open() -> list[dict]:
    out = []
    try:
        from services.trade_log import get_open_auto_trade
        from services.trade_executor import get_open_positions
        syms = [
            "BMFBOVESPA:WIN1!", "BMFBOVESPA:WDO1!", "BMFBOVESPA:PETR4",
        ]
        labels = {
            "BMFBOVESPA:WIN1!": "WIN", "BMFBOVESPA:WDO1!": "WDO",
            "BMFBOVESPA:PETR4": "PETR4",
        }
        for sym in syms:
            log = get_open_auto_trade(sym)
            positions, _ = get_open_positions(sym)
            if not log and not positions:
                continue
            pos = (positions or [{}])[0]
            out.append({
                "engine": "Monitor MT5",
                "symbol": labels.get(sym, sym),
                "setup": (log or {}).get("setup") or "monitor",
                "dir": (log or {}).get("acao") or pos.get("type_desc") or "—",
                "entry": (log or {}).get("entry_price") or pos.get("price_open"),
                "price": pos.get("price_current"),
                "sl": (log or {}).get("sl_initial"),
                "tp": (log or {}).get("tp1_initial"),
                "profit": pos.get("profit"),
                "opened_at": (log or {}).get("opened_at"),
            })
    except Exception as exc:
        logger.debug("monitor open: %s", exc)
    return out


def _collect_scalper_open() -> list[dict]:
    out = []
    try:
        from services.scalper_telegram import _scalper_symbol
        from services.scalper_service import get_scalper_position
        sym = _scalper_symbol()
        pos, _ = get_scalper_position(sym)
        if not pos:
            return out
        out.append({
            "engine": "Scalper",
            "symbol": sym,
            "setup": "scalper",
            "dir": "COMPRA" if pos.get("type") == 0 else "VENDA",
            "entry": pos.get("price_open"),
            "price": pos.get("price_current"),
            "sl": pos.get("sl"),
            "tp": pos.get("tp"),
            "profit": pos.get("profit"),
            "opened_at": None,
        })
    except Exception as exc:
        logger.debug("scalper open: %s", exc)
    return out


def _collect_hub_b3_open() -> list[dict]:
    out = []
    try:
        from services.b3_trade_hub import engines_status
        for e in engines_status() or []:
            p = e.get("position")
            if not p:
                continue
            out.append({
                "engine": f"Hub B3/{e.get('id') or e.get('label')}",
                "symbol": p.get("symbol") or e.get("symbol") or "—",
                "setup": p.get("setup") or e.get("label") or e.get("id"),
                "dir": p.get("dir") or "—",
                "entry": p.get("entry"),
                "price": p.get("price"),
                "sl": p.get("sl"),
                "tp": p.get("tp"),
                "profit": p.get("profit_brl"),
                "opened_at": None,
                "magic": p.get("magic") or e.get("magic"),
            })
    except Exception as exc:
        logger.debug("hub open: %s", exc)
    return out


def _collect_crypto_open() -> list[dict]:
    """Posições crypto: API :5001 (fonte viva), fallback heartbeat."""
    out = []
    try:
        from services.crypto_telegram import _live_positions
        for p in _live_positions() or []:
            out.append({
                "engine": "Crypto",
                "symbol": p.get("symbol") or "—",
                "setup": p.get("setup") or "crypto",
                "dir": p.get("dir") or "?",
                "entry": p.get("price_open") or p.get("entry"),
                "price": p.get("price_current") or p.get("price"),
                "sl": p.get("sl"),
                "tp": p.get("tp"),
                "profit": p.get("profit"),
                "opened_at": p.get("opened_at"),
                "profit_usd": p.get("profit"),
            })
        if out:
            return out
    except Exception as exc:
        logger.debug("crypto open api: %s", exc)
    try:
        hb = _load_hb("crypto_runtime") or {}
        for p in hb.get("positions") or []:
            typ = p.get("type")
            if typ in (0, "0") or str(p.get("dir") or "").upper().startswith("C"):
                dir_ = "COMPRA"
            elif typ in (1, "1") or str(p.get("dir") or "").upper().startswith("V"):
                dir_ = "VENDA"
            else:
                dir_ = p.get("dir") or "?"
            out.append({
                "engine": "Crypto",
                "symbol": p.get("symbol") or "—",
                "setup": p.get("comment") or p.get("setup") or "crypto",
                "dir": dir_,
                "entry": p.get("price_open") or p.get("entry"),
                "price": p.get("price_current") or p.get("price"),
                "sl": p.get("sl"),
                "tp": p.get("tp"),
                "profit": p.get("profit"),
                "opened_at": p.get("opened_at"),
                "profit_usd": p.get("profit"),
            })
    except Exception as exc:
        logger.debug("crypto open: %s", exc)
    return out


def collect_open_positions() -> list[dict]:
    rows = []
    rows.extend(_collect_monitor_open())
    rows.extend(_collect_scalper_open())
    rows.extend(_collect_hub_b3_open())
    rows.extend(_collect_crypto_open())
    return rows


def format_open_trades_message() -> str:
    rows = collect_open_positions()
    if not rows:
        return (
            "📭 <b>Nenhum trade aberto</b> nos motores:\n"
            "Monitor MT5 · Scalper · Hub B3 (V7/WinGo/EOD) · Crypto"
        )
    lines = [f"📈 <b>TRADES ABERTOS</b> ({len(rows)})", "━━━━━━━━━━━━━━━━━━"]
    for r in rows:
        dir_ = r.get("dir") or "—"
        icon = "📈" if str(dir_).upper().startswith("C") else "📉"
        profit = r.get("profit")
        if profit is None:
            pnl_txt = "—"
        elif r.get("engine") == "Crypto":
            try:
                from services.crypto_telegram import _usdbrl
                pnl_txt = _money_usd(profit, _usdbrl())
            except Exception:
                pnl_txt = f"$ {_f(profit):+.2f}"
        else:
            pnl_txt = _brl(profit)
        picon = "📈" if _f(profit) >= 0 else "📉"
        entry = r.get("entry")
        price = r.get("price")
        try:
            entry_s = f"{float(entry):.0f}" if entry is not None and float(entry) > 100 else (
                f"{float(entry):.2f}" if entry is not None else "—")
        except Exception:
            entry_s = str(entry or "—")
        try:
            price_s = f"{float(price):.0f}" if price is not None and float(price) > 100 else (
                f"{float(price):.2f}" if price is not None else "—")
        except Exception:
            price_s = str(price or "—")
        lines.append(
            f"\n{icon} <b>[{r.get('engine')}] {r.get('symbol')}</b> · {r.get('setup')}\n"
            f"🔀 {dir_} · 💰 {entry_s} → {price_s}\n"
            f"{picon} P&L: <b>{pnl_txt}</b>"
        )
    lines.append(
        "\nFechar: /fechar · /sc_fechar · /v7 (hub) · crypto no painel :5001"
    )
    return "\n".join(lines)
