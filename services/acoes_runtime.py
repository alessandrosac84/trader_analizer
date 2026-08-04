"""
services/acoes_runtime.py — runtime SCAN/EXECUTE de ações B3 (módulo isolado).

- Magics 20260810+ (não toca WIN/WDO).
- Avalia última barra FECHADA dos paths 🟢 GO.
- Auto-fill só se ACOES_AUTO_ON=1/ALL (default OFF — só scan).
- Heartbeat: logs/hb_acoes_runtime.json
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

_BRT = timezone(timedelta(hours=-3))
_ROOT = Path(__file__).resolve().parents[1]
_HB = _ROOT / "logs" / "hb_acoes_runtime.json"
_CSV = _ROOT / "logs" / "acoes_trades.csv"

_state = {
    "running": False,
    "motivos": {},
    "scans": {},
    "positions": [],
    "pnl_day": {},
    "pnl_realized": {},
    "pnl_floating": {},
    "executed_today": {},
    "trades_today": [],
    "day_stats": {},
    "last_bar": {},
    "ts": None,
}
_lock = threading.Lock()
_thread = None
_STOP = threading.Event()
_CSV_COLS = ["datetime_brt", "symbol", "direcao", "setup", "magic",
             "entry_price", "sl", "tp1", "volume", "resultado", "profit",
             "exit_price", "exit_brt", "position_id"]


def _auto_on() -> bool:
    raw = (os.getenv("ACOES_AUTO_ON", "0") or "").strip().upper()
    return raw in ("1", "TRUE", "ON", "ALL", "*")


def _today_brt() -> str:
    return datetime.now(_BRT).date().isoformat()


def _deal_dt_brt(epoch: int) -> datetime:
    """Converte deal.time MT5 → BRT.

    Na XP, o campo time costuma gravar o wall-clock do pregão (BRT) como se fosse
    UTC. Interpretação UTC→BRT clássica atrasa ~3h vs datetime_brt do CSV.
    """
    return datetime.fromtimestamp(int(epoch), tz=timezone.utc).replace(tzinfo=_BRT)


def get_runtime_snapshot(symbol: str | None = None) -> dict:
    """Snapshot do board. Se HB em memória estiver vazio (pós-restart / fora do pregão),
    tenta refrescar do MT5+CSV — não depende só do loop de scan."""
    with _lock:
        snap = json.loads(json.dumps(_state, default=str))
    snap["auto_on"] = _auto_on()
    needs = (
        not snap.get("positions")
        and not (snap.get("pnl_day") or {})
        and not (snap.get("trades_today") or [])
        and not (snap.get("executed_today") or {})
    )
    if needs:
        try:
            book = refresh_live_book()
            if book:
                with _lock:
                    for k in ("positions", "pnl_day", "pnl_realized", "pnl_floating",
                              "executed_today", "trades_today", "day_stats"):
                        if k in book:
                            _state[k] = book[k]
                    _state["ts"] = datetime.now(_BRT).isoformat(timespec="seconds")
                with _lock:
                    snap = json.loads(json.dumps(_state, default=str))
                snap["auto_on"] = _auto_on()
        except Exception as exc:
            logger.debug("acoes live refresh: %s", exc)
    sym = (symbol or "").upper()
    if sym:
        snap["motivo"] = (snap.get("motivos") or {}).get(sym, "—")
        snap["scan"] = (snap.get("scans") or {}).get(sym, "IDLE")
        snap["pnl_day_sym"] = float((snap.get("pnl_day") or {}).get(sym, 0.0) or 0.0)
        snap["pnl_realized_sym"] = float((snap.get("pnl_realized") or {}).get(sym, 0.0) or 0.0)
        snap["pnl_floating_sym"] = float((snap.get("pnl_floating") or {}).get(sym, 0.0) or 0.0)
        ex = snap.get("executed_today") or {}
        snap["executed_sym"] = int(ex.get(sym, 0) or 0)
        snap["positions_sym"] = [
            p for p in (snap.get("positions") or [])
            if (p.get("symbol") or "").upper() == sym
        ]
        snap["trades_sym"] = [
            t for t in (snap.get("trades_today") or [])
            if (t.get("symbol") or "").upper() == sym
        ]
    return snap


def _write_hb():
    try:
        _HB.parent.mkdir(parents=True, exist_ok=True)
        with _lock:
            payload = dict(_state)
            payload["ts"] = time.time()
            payload["ts_brt"] = datetime.now(_BRT).isoformat(timespec="seconds")
            payload["auto_on"] = _auto_on()
        _HB.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.debug("hb acoes: %s", exc)


def _mt5_ready():
    import MetaTrader5 as mt5
    if mt5.terminal_info() is not None:
        return mt5
    kw = {}
    path = os.getenv("MT5_PATH", "")
    if path and os.path.exists(path):
        kw["path"] = path
    login = int(os.getenv("MT5_LOGIN", "0") or 0)
    pw, srv = os.getenv("MT5_PASSWORD", ""), os.getenv("MT5_SERVER", "")
    if login and pw and srv:
        kw.update(login=login, password=pw, server=srv)
    if not mt5.initialize(**kw):
        return None
    return mt5


def _fetch_df(mt5, symbol: str, tf: int, bars: int = 400):
    from backtest_kimi_real import add_indicators
    from backtest_setups_novos_v2 import add_extra_v2
    import pandas as pd

    tf_map = {
        5: mt5.TIMEFRAME_M5, 15: mt5.TIMEFRAME_M15,
        30: mt5.TIMEFRAME_M30, 60: mt5.TIMEFRAME_H1,
    }
    rates = mt5.copy_rates_from_pos(symbol, tf_map.get(tf, mt5.TIMEFRAME_M15), 0, bars)
    if rates is None or len(rates) < 80:
        return None
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df = df.set_index("time")
    vol = df["real_volume"] if df["real_volume"].sum() > 0 else df["tick_volume"]
    raw = pd.DataFrame({
        "Open": df["open"], "High": df["high"], "Low": df["low"],
        "Close": df["close"], "Volume": vol,
    }, index=df.index)
    df = add_extra_v2(add_indicators(raw), symbol, ctx=None)
    # v5: ORB15/ORB60/PDM precisam de colunas extras
    try:
        from backtest_acoes_b3_v5_mega import _enrich_v5
        df = _enrich_v5(df)
    except Exception:
        pass
    return df


def _build_signal_fn(setup: str):
    """Reconstrói a fn do setup a partir do registry v3/v4/v5/v6."""
    papers = ["PETR4", "VALE3", "ITUB4", "BBDC4", "BBAS3", "ABEV3", "WEGE3"]
    import backtest_acoes_b3_v3_mega as mega
    mega._build_registry(papers)
    fn = mega.STRATS.get(setup)
    if fn is not None:
        return fn
    import backtest_acoes_b3_v4_mega as v4
    v4._build_registry(papers)
    fn = v4.STRATS.get(setup)
    if fn is not None:
        return fn
    import backtest_acoes_b3_v5_mega as v5
    v5._build_registry(papers)
    fn = v5.STRATS.get(setup)
    if fn is not None:
        return fn
    import backtest_acoes_b3_v6_mega as v6
    v6._build_registry(papers)
    return v6.STRATS.get(setup)


_FN_CACHE: dict = {}


def _signal_for(setup: str):
    if setup not in _FN_CACHE:
        _FN_CACHE[setup] = _build_signal_fn(setup)
    return _FN_CACHE[setup]


def _has_position(mt5, symbol: str, magic: int) -> bool:
    for p in (mt5.positions_get(symbol=symbol) or []):
        if int(getattr(p, "magic", 0) or 0) == int(magic):
            return True
    return False


def _sym_base(sym: str) -> str:
    return (sym or "").upper().split(".")[0]


def _exit_reason(comment: str) -> str:
    c = (comment or "").lower()
    if "tp" in c:
        return "TP"
    if "sl" in c:
        return "SL"
    if "so:" in c or "stop" in c:
        return "SL"
    return "CLOSE"


def _read_csv_rows() -> list[dict]:
    if not _CSV.exists():
        return []
    import csv
    try:
        with open(_CSV, encoding="utf-8") as f:
            return list(csv.DictReader(f))
    except Exception as exc:
        logger.warning("acoes csv read: %s", exc)
        return []


def _write_csv_rows(rows: list[dict]):
    import csv
    _CSV.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(_CSV, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=_CSV_COLS, extrasaction="ignore")
            w.writeheader()
            for r in rows:
                w.writerow({k: r.get(k, "") for k in _CSV_COLS})
    except Exception as exc:
        logger.warning("acoes csv write: %s", exc)


def _fetch_positions(mt5) -> list[dict]:
    from services.acoes_go_paths import magics as _magics, setup_by_magic
    mset = _magics()
    out = []
    for p in (mt5.positions_get() or []):
        mag = int(getattr(p, "magic", 0) or 0)
        if mag not in mset:
            continue
        sym = _sym_base(p.symbol)
        out.append({
            "symbol": sym,
            "magic": mag,
            "volume": float(p.volume),
            "profit": round(float(p.profit) + float(getattr(p, "swap", 0) or 0), 2),
            "type": "COMPRA" if p.type == 0 else "VENDA",
            "price_open": float(p.price_open),
            "price_current": float(getattr(p, "price_current", 0) or 0),
            "sl": float(p.sl or 0),
            "tp": float(p.tp or 0),
            "comment": p.comment or "",
            "setup": setup_by_magic(mag) or (p.comment or ""),
            "ticket": int(p.ticket),
            "time": int(getattr(p, "time", 0) or 0),
        })
    return out


def _mt5_closed_today(mt5) -> list[dict]:
    """Trades fechados hoje (BRT) com magics de ações — agrupa IN+OUT por position_id."""
    from services.acoes_go_paths import magics as _magics, setup_by_magic
    mset = _magics()
    today = datetime.now(_BRT).date()
    # +1 dia: broker pode estar adiantado vs local
    to = datetime.now() + timedelta(days=1)
    frm = datetime.now() - timedelta(days=2)
    deals = mt5.history_deals_get(frm, to) or []
    by_pos: dict = {}
    for d in deals:
        mag = int(getattr(d, "magic", 0) or 0)
        if mag not in mset:
            continue
        pid = int(getattr(d, "position_id", 0) or 0)
        if not pid:
            continue
        by_pos.setdefault(pid, []).append(d)

    closed = []
    for pid, ds in by_pos.items():
        ins = [d for d in ds if int(getattr(d, "entry", -1)) == 0]
        outs = [d for d in ds if int(getattr(d, "entry", -1)) == 1]
        if not outs:
            continue
        outs.sort(key=lambda x: x.time)
        od = outs[-1]
        close_brt = _deal_dt_brt(od.time)
        if close_brt.date() != today:
            continue
        idl = sorted(ins, key=lambda x: x.time)[0] if ins else None
        mag = int(getattr(od, "magic", 0) or (getattr(idl, "magic", 0) if idl else 0) or 0)
        if idl is not None:
            direcao = "COMPRA" if idl.type == 0 else "VENDA"
            entry_price = float(idl.price)
            volume = float(idl.volume)
            entry_brt = _deal_dt_brt(idl.time).isoformat(timespec="seconds")
        else:
            direcao = "VENDA" if od.type == 0 else "COMPRA"
            entry_price = None
            volume = float(od.volume)
            entry_brt = close_brt.isoformat(timespec="seconds")
        profit = (
            sum(float(d.profit) for d in outs)
            + sum(float(getattr(d, "swap", 0) or 0) for d in ds)
            + sum(float(getattr(d, "commission", 0) or 0) for d in ds)
        )
        setup = setup_by_magic(mag) or (getattr(idl, "comment", None) if idl else None) or od.comment or ""
        closed.append({
            "datetime_brt": entry_brt,
            "exit_brt": close_brt.isoformat(timespec="seconds"),
            "symbol": _sym_base(od.symbol),
            "direcao": direcao,
            "setup": setup,
            "magic": mag,
            "entry_price": entry_price,
            "exit_price": float(od.price),
            "volume": volume,
            "resultado": _exit_reason(od.comment or ""),
            "profit": round(profit, 2),
            "position_id": pid,
            "status": "CLOSED",
            "source": "mt5",
        })
    closed.sort(key=lambda r: r.get("exit_brt") or "")
    return closed


def _mt5_entries_today(mt5) -> dict:
    """Contagem de entradas (DEAL_ENTRY_IN) hoje por símbolo."""
    from services.acoes_go_paths import magics as _magics
    mset = _magics()
    today = datetime.now(_BRT).date()
    to = datetime.now() + timedelta(days=1)
    frm = datetime.now() - timedelta(days=2)
    deals = mt5.history_deals_get(frm, to) or []
    counts: dict = {}
    day_keys: dict = {}
    for d in deals:
        mag = int(getattr(d, "magic", 0) or 0)
        if mag not in mset:
            continue
        if int(getattr(d, "entry", -1)) != 0:
            continue
        t = _deal_dt_brt(d.time)
        if t.date() != today:
            continue
        sym = _sym_base(d.symbol)
        counts[sym] = int(counts.get(sym, 0)) + 1
        from services.acoes_go_paths import setup_by_magic
        setup = setup_by_magic(mag) or ""
        if setup:
            day_keys[f"{setup}_{today.isoformat()}"] = 1
    return {"by_sym": counts, "day_keys": day_keys}


def _sync_csv_with_closes(closed: list[dict], open_pos: list[dict], mt5=None):
    """Atualiza OPEN→CLOSED no CSV quando o MT5 já fechou (ex.: PETR TP).
    Em conta netting XP, magics diferentes no mesmo papel podem fundir numa posição —
    OPEN órfão (sem magic na posição atual) vira MERGED."""
    rows = _read_csv_rows()
    if not rows and not closed:
        return
    open_keys = {
        (p["symbol"], int(p["magic"])) for p in open_pos
    }
    open_syms = {p["symbol"] for p in open_pos}
    open_tickets = {int(p.get("ticket") or 0) for p in open_pos}
    by_key = {}
    for c in closed:
        by_key[(c["symbol"], int(c["magic"]))] = c
        by_key[("pid", int(c.get("position_id") or 0))] = c

    # position_ids vivos (netting): entries cujo pos ainda está aberta
    live_pids = set()
    if mt5 is not None:
        try:
            to = datetime.now() + timedelta(days=1)
            frm = datetime.now() - timedelta(days=2)
            for d in (mt5.history_deals_get(frm, to) or []):
                pid = int(getattr(d, "position_id", 0) or 0)
                if pid and pid in open_tickets:
                    live_pids.add(pid)
        except Exception:
            pass
    for p in open_pos:
        live_pids.add(int(p.get("ticket") or 0))

    changed = False
    for r in rows:
        res = (r.get("resultado") or "").upper()
        sym = _sym_base(r.get("symbol") or "")
        try:
            mag = int(float(r.get("magic") or 0))
        except Exception:
            mag = 0
        key = (sym, mag)
        hit = by_key.get(key)
        # Atualiza horário/profit de linhas já fechadas se o MT5 trouxe dado melhor
        if res in ("TP", "SL", "CLOSE") and hit is not None:
            new_exit = str(hit.get("exit_brt") or "")
            old_exit = str(r.get("exit_brt") or "")
            if new_exit and new_exit != old_exit:
                r["exit_brt"] = new_exit
                r["exit_price"] = hit.get("exit_price", r.get("exit_price", ""))
                r["profit"] = hit.get("profit", r.get("profit", ""))
                r["position_id"] = hit.get("position_id", r.get("position_id", ""))
                changed = True
            continue
        if res and res not in ("OPEN", ""):
            continue
        if key in open_keys:
            r["resultado"] = "OPEN"
            continue
        if hit is not None:
            r["resultado"] = hit.get("resultado") or "CLOSE"
            r["profit"] = hit.get("profit", "")
            r["exit_price"] = hit.get("exit_price", "")
            r["exit_brt"] = hit.get("exit_brt", "")
            r["position_id"] = hit.get("position_id", r.get("position_id", ""))
            changed = True
            continue
        # Netting: ainda há posição no símbolo, mas magic sumiu → absorvido
        if sym in open_syms:
            r["resultado"] = "MERGED"
            r["profit"] = r.get("profit") or 0
            changed = True
            continue

    # Insere fechamentos MT5 que não estão no CSV
    existing = set()
    for r in rows:
        try:
            existing.add((_sym_base(r.get("symbol") or ""), int(float(r.get("magic") or 0)),
                          str(r.get("resultado") or "").upper()))
        except Exception:
            pass
        if r.get("position_id"):
            existing.add(("pid", int(float(r.get("position_id") or 0))))
    for c in closed:
        pid = int(c.get("position_id") or 0)
        if pid and ("pid", pid) in existing:
            continue
        key3 = (c["symbol"], int(c["magic"]), (c.get("resultado") or "CLOSE").upper())
        # se já tem linha OPEN atualizada acima, não duplica
        already_open_upd = any(
            _sym_base(r.get("symbol") or "") == c["symbol"]
            and int(float(r.get("magic") or 0)) == int(c["magic"])
            and str(r.get("resultado") or "").upper() in ("TP", "SL", "CLOSE")
            and str(r.get("exit_brt") or "") == str(c.get("exit_brt") or "")
            for r in rows
        )
        if already_open_upd:
            continue
        rows.append({
            "datetime_brt": c.get("datetime_brt", ""),
            "symbol": c["symbol"],
            "direcao": c.get("direcao", ""),
            "setup": c.get("setup", ""),
            "magic": c.get("magic", ""),
            "entry_price": c.get("entry_price", ""),
            "sl": "",
            "tp1": "",
            "volume": c.get("volume", ""),
            "resultado": c.get("resultado", "CLOSE"),
            "profit": c.get("profit", ""),
            "exit_price": c.get("exit_price", ""),
            "exit_brt": c.get("exit_brt", ""),
            "position_id": c.get("position_id", ""),
        })
        changed = True

    if changed or (rows and set(_CSV_COLS) - set(rows[0].keys())):
        _write_csv_rows(rows)


def _build_trades_today(closed: list[dict], open_pos: list[dict], csv_rows: list[dict] | None = None) -> list[dict]:
    """Unifica CSV + MT5: fechados do dia + abertos ainda vivos."""
    today = _today_brt()
    csv_rows = csv_rows if csv_rows is not None else _read_csv_rows()
    trades: list[dict] = []
    seen_closed = set()

    for c in closed:
        k = ("c", c.get("position_id") or f"{c['symbol']}_{c['magic']}_{c.get('exit_brt')}")
        seen_closed.add((c["symbol"], int(c["magic"])))
        trades.append(c)

    for r in csv_rows:
        dt = str(r.get("datetime_brt") or r.get("exit_brt") or "")
        if not dt.startswith(today) and not str(r.get("exit_brt") or "").startswith(today):
            continue
        res = (r.get("resultado") or "").upper()
        sym = _sym_base(r.get("symbol") or "")
        try:
            mag = int(float(r.get("magic") or 0))
        except Exception:
            mag = 0
        if res == "MERGED":
            # netting: entrada absorvida — não conta como trade fechado
            continue
        if res in ("TP", "SL", "CLOSE") or (
            r.get("profit") not in ("", None) and res not in ("OPEN", "MERGED", "")
        ):
            if (sym, mag) in seen_closed:
                continue
            try:
                profit = float(r.get("profit") or 0)
            except Exception:
                profit = 0.0
            trades.append({
                "datetime_brt": r.get("datetime_brt", ""),
                "exit_brt": r.get("exit_brt", ""),
                "symbol": sym,
                "direcao": r.get("direcao", ""),
                "setup": r.get("setup", ""),
                "magic": mag,
                "entry_price": r.get("entry_price", ""),
                "exit_price": r.get("exit_price", ""),
                "volume": r.get("volume", ""),
                "resultado": res or "CLOSE",
                "profit": profit,
                "status": "CLOSED",
                "source": "csv",
            })
            seen_closed.add((sym, mag))

    for p in open_pos:
        trades.append({
            "datetime_brt": (
                _deal_dt_brt(p["time"]).isoformat(timespec="seconds")
                if p.get("time") else ""
            ),
            "exit_brt": "",
            "symbol": p["symbol"],
            "direcao": p.get("type", ""),
            "setup": p.get("setup", ""),
            "magic": p.get("magic"),
            "entry_price": p.get("price_open"),
            "exit_price": p.get("price_current"),
            "volume": p.get("volume"),
            "resultado": "OPEN",
            "profit": p.get("profit", 0),
            "sl": p.get("sl"),
            "tp": p.get("tp"),
            "status": "OPEN",
            "source": "mt5",
            "ticket": p.get("ticket"),
        })

    def _sort_key(t):
        return t.get("exit_brt") or t.get("datetime_brt") or ""

    trades.sort(key=_sort_key, reverse=True)
    return trades


def refresh_live_book(mt5=None) -> dict:
    """Fonte da verdade para o board: posições MT5 + deals do dia + CSV.
    Funciona fora do pregão e após restart (não depende só do HB em memória)."""
    own = mt5 is None
    if mt5 is None:
        mt5 = _mt5_ready()
    if mt5 is None:
        # fallback: só CSV
        return _book_from_csv_only()

    try:
        from services.acoes_config import get_symbols
        poss = _fetch_positions(mt5)
        closed = _mt5_closed_today(mt5)
        entries = _mt5_entries_today(mt5)
        _sync_csv_with_closes(closed, poss, mt5=mt5)
        csv_rows = _read_csv_rows()
        trades = _build_trades_today(closed, poss, csv_rows)

        pnl_real: dict = {s: 0.0 for s in get_symbols()}
        pnl_float: dict = {s: 0.0 for s in get_symbols()}
        # P&L a partir do book unificado (não só da lista crua do MT5)
        for t in trades:
            s = _sym_base(t.get("symbol") or "")
            if not s:
                continue
            try:
                pr = float(t.get("profit") or 0)
            except Exception:
                pr = 0.0
            if (t.get("status") or "").upper() == "OPEN" or (t.get("resultado") or "").upper() == "OPEN":
                pnl_float[s] = round(float(pnl_float.get(s, 0)) + pr, 2)
            elif (t.get("resultado") or "").upper() in ("TP", "SL", "CLOSE") or (t.get("status") or "").upper() == "CLOSED":
                pnl_real[s] = round(float(pnl_real.get(s, 0)) + pr, 2)
        # floating da posição MT5 manda (mais fresco que trade snapshot)
        if poss:
            pnl_float = {s: 0.0 for s in get_symbols()}
            for p in poss:
                s = p["symbol"]
                pnl_float[s] = round(float(pnl_float.get(s, 0)) + float(p.get("profit") or 0), 2)

        pnl_day = {
            s: round(float(pnl_real.get(s, 0)) + float(pnl_float.get(s, 0)), 2)
            for s in sorted(set(list(pnl_real) + list(pnl_float)))
        }

        executed = dict(entries.get("day_keys") or {})
        for s, n in (entries.get("by_sym") or {}).items():
            executed[s] = int(n)

        # CSV OPEN/entries do dia também contam se MT5 history falhou
        today = _today_brt()
        for r in csv_rows:
            if not str(r.get("datetime_brt") or "").startswith(today):
                continue
            s = _sym_base(r.get("symbol") or "")
            if s and s not in (entries.get("by_sym") or {}):
                executed[s] = int(executed.get(s, 0) or 0) + 1

        closed_n = len([t for t in trades if t.get("status") == "CLOSED"])
        wins = len([t for t in trades if t.get("status") == "CLOSED" and float(t.get("profit") or 0) > 0])
        total_pnl = round(sum(pnl_day.values()), 2)
        day_stats = {
            "date": today,
            "pnl_total": total_pnl,
            "pnl_realized": round(sum(pnl_real.values()), 2),
            "pnl_floating": round(sum(pnl_float.values()), 2),
            "n_closed": closed_n,
            "n_open": len(poss),
            "n_exec": sum(int(executed.get(s, 0) or 0) for s in get_symbols()),
            "wins": wins,
            "winrate": round(100.0 * wins / closed_n, 1) if closed_n else None,
        }

        return {
            "positions": poss,
            "pnl_day": pnl_day,
            "pnl_realized": pnl_real,
            "pnl_floating": pnl_float,
            "executed_today": executed,
            "trades_today": trades,
            "day_stats": day_stats,
            "ok": True,
        }
    except Exception as exc:
        logger.warning("acoes refresh_live_book: %s", exc)
        return _book_from_csv_only()
    finally:
        # não dá shutdown — app compartilha terminal MT5
        pass


def _book_from_csv_only() -> dict:
    from services.acoes_config import get_symbols
    today = _today_brt()
    rows = _read_csv_rows()
    pnl_real = {s: 0.0 for s in get_symbols()}
    executed = {}
    trades = []
    for r in rows:
        sym = _sym_base(r.get("symbol") or "")
        dt = str(r.get("datetime_brt") or "")
        ex = str(r.get("exit_brt") or "")
        if not (dt.startswith(today) or ex.startswith(today)):
            continue
        res = (r.get("resultado") or "").upper()
        try:
            profit = float(r["profit"]) if r.get("profit") not in ("", None) else 0.0
        except Exception:
            profit = 0.0
        if dt.startswith(today):
            executed[sym] = int(executed.get(sym, 0)) + 1
        if res in ("TP", "SL", "CLOSE"):
            pnl_real[sym] = round(pnl_real.get(sym, 0) + profit, 2)
            trades.append({
                "datetime_brt": r.get("datetime_brt", ""),
                "exit_brt": r.get("exit_brt", ""),
                "symbol": sym,
                "direcao": r.get("direcao", ""),
                "setup": r.get("setup", ""),
                "magic": r.get("magic", ""),
                "entry_price": r.get("entry_price", ""),
                "exit_price": r.get("exit_price", ""),
                "volume": r.get("volume", ""),
                "resultado": res,
                "profit": profit,
                "status": "CLOSED",
                "source": "csv",
            })
        elif res == "OPEN":
            trades.append({
                "datetime_brt": r.get("datetime_brt", ""),
                "exit_brt": "",
                "symbol": sym,
                "direcao": r.get("direcao", ""),
                "setup": r.get("setup", ""),
                "magic": r.get("magic", ""),
                "entry_price": r.get("entry_price", ""),
                "exit_price": "",
                "volume": r.get("volume", ""),
                "resultado": "OPEN",
                "profit": profit,
                "status": "OPEN",
                "source": "csv",
            })
    closed_n = len([t for t in trades if t.get("status") == "CLOSED"])
    wins = len([t for t in trades if t.get("status") == "CLOSED" and float(t.get("profit") or 0) > 0])
    return {
        "positions": [],
        "pnl_day": dict(pnl_real),
        "pnl_realized": dict(pnl_real),
        "pnl_floating": {s: 0.0 for s in get_symbols()},
        "executed_today": executed,
        "trades_today": trades,
        "day_stats": {
            "date": today,
            "pnl_total": round(sum(pnl_real.values()), 2),
            "pnl_realized": round(sum(pnl_real.values()), 2),
            "pnl_floating": 0.0,
            "n_closed": closed_n,
            "n_open": len([t for t in trades if t.get("status") == "OPEN"]),
            "n_exec": sum(executed.values()),
            "wins": wins,
            "winrate": round(100.0 * wins / closed_n, 1) if closed_n else None,
        },
        "ok": True,
        "source": "csv_only",
    }


def _apply_book_to_state(book: dict):
    if not book:
        return
    with _lock:
        for k in ("positions", "pnl_day", "pnl_realized", "pnl_floating",
                  "executed_today", "trades_today", "day_stats"):
            if k in book:
                _state[k] = book[k]


def _place(mt5, symbol: str, direction: str, sl: float, tp: float, magic: int, volume: float, comment: str):
    info = mt5.symbol_info(symbol)
    if info is None:
        return False, "symbol_info None"
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        return False, "no tick"
    buy = direction == "COMPRA"
    price = tick.ask if buy else tick.bid
    order_type = mt5.ORDER_TYPE_BUY if buy else mt5.ORDER_TYPE_SELL
    req = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": float(volume),
        "type": order_type,
        "price": float(price),
        "sl": float(sl),
        "tp": float(tp),
        "deviation": 40,
        "magic": int(magic),
        "comment": comment[:31],
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_RETURN,
    }
    # filling fallback
    for fill in (mt5.ORDER_FILLING_RETURN, mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_FOK):
        req["type_filling"] = fill
        r = mt5.order_send(req)
        if r is not None and r.retcode == mt5.TRADE_RETCODE_DONE:
            return True, f"ticket={r.order}"
        if r is not None and r.retcode not in (
            mt5.TRADE_RETCODE_INVALID_FILL, getattr(mt5, "TRADE_RETCODE_INVALID_FILL", -1)
        ):
            return False, f"retcode={getattr(r, 'retcode', None)} {getattr(r, 'comment', '')}"
    return False, f"fill fail {getattr(r, 'retcode', None)}"


def _scan_once():
    from services.acoes_go_paths import GO_PATHS
    from services.acoes_config import cfg_for, get_symbols

    mt5 = _mt5_ready()
    if mt5 is None:
        book = _book_from_csv_only()
        _apply_book_to_state(book)
        with _lock:
            _state["motivos"] = {s: "MT5 offline" for s in get_symbols()}
            _state["scans"] = {s: "OFFLINE" for s in get_symbols()}
            _state["running"] = True
            _state["ts"] = datetime.now(_BRT).isoformat(timespec="seconds")
        _write_hb()
        return

    # Sempre atualiza posições / P&L / trades (mesmo fora do pregão)
    book = refresh_live_book(mt5)
    _apply_book_to_state(book)

    now = datetime.now(_BRT)
    if now.weekday() >= 5 or not (10 <= now.hour < 17):
        with _lock:
            for s in get_symbols():
                npos = len([p for p in (_state.get("positions") or [])
                            if (p.get("symbol") or "").upper() == s])
                pnl = float((_state.get("pnl_day") or {}).get(s, 0) or 0)
                extra = ""
                if npos:
                    extra = f" · {npos} pos aberta(s)"
                if pnl:
                    extra += f" · P&L dia {pnl:+.2f}"
                _state["motivos"][s] = f"fora do pregão B3{extra}"
                _state["scans"][s] = "CLOSED"
            _state["running"] = True
            _state["ts"] = now.isoformat(timespec="seconds")
        _write_hb()
        return

    df_cache = {}
    motivos = {}
    scans = {}
    executed = dict(_state.get("executed_today") or {})

    # agrupa paths por (sym, tf)
    by_key = {}
    for path in GO_PATHS:
        if not path.get("auto", True):
            continue
        key = (path["symbol"], int(path["tf"]))
        by_key.setdefault(key, []).append(path)

    for (sym, tf), paths in by_key.items():
        key = f"{sym}_M{tf}"
        if key not in df_cache:
            if not mt5.symbol_select(sym, True):
                motivos[sym] = f"{sym} indisponível no Market Watch"
                scans[sym] = "NO_SYMBOL"
                continue
            df_cache[key] = _fetch_df(mt5, sym, tf)
        df = df_cache.get(key)
        if df is None or len(df) < 80:
            motivos[sym] = f"{sym} M{tf}: sem candles"
            scans[sym] = "NO_DATA"
            continue

        # última barra FECHADA
        i = len(df) - 2
        if i < 60:
            continue
        bar_ts = str(df.index[i])
        hits = []
        for path in paths:
            setup = path["setup"]
            fn = _signal_for(setup)
            if fn is None:
                continue
            try:
                sig = fn(df, i, sym)
            except Exception as exc:
                logger.debug("sig %s: %s", setup, exc)
                continue
            if not sig:
                continue
            hits.append((path, sig))

        if not hits:
            motivos[sym] = f"{sym}: SCAN OK — sem gatilho GO"
            scans[sym] = "SCAN"
            with _lock:
                _state["last_bar"][sym] = bar_ts
            continue

        # um path por vez (melhor net da lista de hits)
        hits.sort(key=lambda x: -(x[0].get("net") or 0))
        path, sig = hits[0]
        magic = int(path["magic"])
        scans[sym] = "TRIGGER"
        motivos[sym] = f"{sym}: TRIGGER {path['setup']} {sig['dir']}"

        if not _auto_on():
            motivos[sym] += " · auto OFF (ACOES_AUTO_ON)"
            continue
        if _has_position(mt5, sym, magic):
            motivos[sym] += " · já em posição"
            continue
        # one_per_day: se já executou hoje este magic
        day_key = f"{path['setup']}_{now.date()}"
        if path.get("one_per_day") and executed.get(day_key):
            motivos[sym] += " · 1/dia já ok"
            continue

        vol = float(cfg_for(sym).get("volume", 100))
        entry = float(df.iloc[i].Close)
        sl = float(sig["sl"])
        # TP1 como alvo
        tp = float(sig.get("tp1") or entry)
        ok, detail = _place(
            mt5, sym, sig["dir"], sl, tp, magic, vol,
            comment=path["setup"][:31],
        )
        if ok:
            executed[day_key] = 1
            executed[sym] = int(executed.get(sym, 0)) + 1
            motivos[sym] = f"{sym}: EXECUTED {path['setup']} {sig['dir']} {detail}"
            scans[sym] = "EXECUTED"
            _append_trade_csv({
                "datetime_brt": now.isoformat(timespec="seconds"),
                "symbol": sym, "direcao": sig["dir"], "setup": path["setup"],
                "magic": magic, "entry_price": entry, "sl": sl, "tp1": tp,
                "volume": vol, "resultado": "OPEN", "profit": "",
            })
        else:
            motivos[sym] = f"{sym}: FILL FAIL {path['setup']} {detail}"
            scans[sym] = "REJECT"

    # garante motivo p/ símbolos sem path
    for s in get_symbols():
        motivos.setdefault(s, "sem path GO wired")
        scans.setdefault(s, "IDLE")

    # re-sync book após possíveis fills
    book = refresh_live_book(mt5)
    _apply_book_to_state(book)

    with _lock:
        _state["motivos"] = motivos
        _state["scans"] = scans
        # preserva executed day_keys do scan + contagens do book
        merged = dict(book.get("executed_today") or {})
        merged.update(executed)
        _state["executed_today"] = merged
        _state["running"] = True
        _state["ts"] = datetime.now(_BRT).isoformat(timespec="seconds")
    _write_hb()


def _append_trade_csv(row: dict):
    import csv
    _CSV.parent.mkdir(parents=True, exist_ok=True)
    new = not _CSV.exists()
    try:
        with open(_CSV, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=_CSV_COLS, extrasaction="ignore")
            if new:
                w.writeheader()
            w.writerow({k: row.get(k, "") for k in _CSV_COLS})
    except Exception as exc:
        logger.warning("acoes csv: %s", exc)


def _loop():
    logger.info("AcoesRuntime iniciado — %d GOs · auto=%s",
                len(__import__("services.acoes_go_paths", fromlist=["GO_PATHS"]).GO_PATHS),
                _auto_on())
    # warmup registry uma vez
    try:
        _signal_for("VALE3_ORB30_V15")
    except Exception as exc:
        logger.warning("acoes warmup registry: %s", exc)
    while not _STOP.is_set():
        try:
            _scan_once()
        except Exception as exc:
            logger.exception("acoes scan: %s", exc)
            with _lock:
                _state["motivos"]["_err"] = str(exc)
            _write_hb()
        _STOP.wait(45.0)
    logger.info("AcoesRuntime parado")


def start_acoes_runtime():
    global _thread
    if _thread and _thread.is_alive():
        return
    _STOP.clear()
    _thread = threading.Thread(target=_loop, name="AcoesRuntime", daemon=True)
    _thread.start()


def stop_acoes_runtime():
    _STOP.set()
