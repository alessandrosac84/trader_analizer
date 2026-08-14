"""
b3_trade_hub.py — hub de acompanhamento WIN/WDO na tela /v7.

Agrega motores paralelos (V7 GAP_FADE/ORB, WinGo NR7/INSIDE, Win EOD) em
um único status + histórico etiquetado por SETUP — sem misturar a execução
(cada um mantém magic próprio).
"""
import csv
import os
from datetime import datetime, timedelta
from pathlib import Path

# Path absoluto — evita KPI zerado se o CWD do processo não for a raiz do repo
LOG = Path(__file__).resolve().parent.parent / "logs"

# Magics conhecidos (para posições abertas) — hub V7/WinGo/EOD (sem Monitor MT5)
ENGINES = {
    "v7": {"magic": 20260714, "label": "V7 GAP_FADE+ORB", "symbol": "WIN"},
    "nr5_h4_mt": {"magic": 20260730, "label": "NR5_H4_MT", "symbol": "WIN"},
    "nr5_h4": {"magic": 20260731, "label": "NR5_H4", "symbol": "WIN"},
    "nr7_h4_mt": {"magic": 20260726, "label": "NR7_H4_MT", "symbol": "WIN"},
    "nr7_trend": {"magic": 20260725, "label": "NR7_TREND_H4", "symbol": "WIN"},
    "nr7": {"magic": 20260723, "label": "NR7_BREAK", "symbol": "WIN"},
    "inside_v18": {"magic": 20260728, "label": "INSIDE_V18_H4", "symbol": "WIN"},
    "inside_vol15": {"magic": 20260732, "label": "INSIDE_VOL15_H4", "symbol": "WIN"},
    "inside_v13": {"magic": 20260733, "label": "INSIDE_V13_H4", "symbol": "WIN"},
    "inside_1015": {"magic": 20260734, "label": "INSIDE_1015_H4", "symbol": "WIN"},
    "inside_h4": {"magic": 20260727, "label": "INSIDE_H4", "symbol": "WIN"},
    "inside_am": {"magic": 20260729, "label": "INSIDE_AM", "symbol": "WIN"},
    "inside": {"magic": 20260724, "label": "INSIDE_BAR_BRK", "symbol": "WIN"},
    "hl_h4": {"magic": 20260735, "label": "HL_H4", "symbol": "WIN"},
    "win_pdh_1115": {"magic": 20260740, "label": "WIN_PDH_1115", "symbol": "WIN"},
    "win_imp_cont": {"magic": 20260741, "label": "WIN_IMP_CONT", "symbol": "WIN"},
    "win_pdh_h4": {"magic": 20260743, "label": "WIN_PDH_H4", "symbol": "WIN"},
    "win_nr5_1115": {"magic": 20260746, "label": "WIN_NR5_1115", "symbol": "WIN"},
    "win_nr4_1115": {"magic": 20260747, "label": "WIN_NR4_1115", "symbol": "WIN"},
    "win_ins_pm": {"magic": 20260749, "label": "WIN_INS_PM", "symbol": "WIN"},
    "win_volspike_am": {"magic": 20260759, "label": "WIN_VOLSPIKE_AM", "symbol": "WIN"},
    "win_ib_brk_v15": {"magic": 20260762, "label": "WIN_IB_BRK_V15", "symbol": "WIN"},
    "win_hl_mid": {"magic": 20260763, "label": "WIN_HL_MID", "symbol": "WIN"},
    "wdo_out_gap_day": {"magic": 20260745, "label": "WDO_OUT_GAP_DAY", "symbol": "WDO"},
    "wdo_out_power": {"magic": 20260748, "label": "WDO_OUT_POWER", "symbol": "WDO"},
    "wdo_imp_1014_mt": {"magic": 20260758, "label": "WDO_IMP_1014_MT", "symbol": "WDO"},
    "wdo_eng_1014": {"magic": 20260760, "label": "WDO_ENG_1014", "symbol": "WDO"},
    "wdo_gapc_a60": {"magic": 20260761, "label": "WDO_GAPC_A60", "symbol": "WDO"},
    "wdo_nr7_1014": {"magic": 20260738, "label": "WDO_NR7_1014_H4", "symbol": "WDO"},
    "wdo_pdh_h4": {"magic": 20260739, "label": "WDO_PDH_H4", "symbol": "WDO"},
    "wdo_hl_1014": {"magic": 20260742, "label": "WDO_HL_1014", "symbol": "WDO"},
    "wdo_out_1015_mt": {"magic": 20260744, "label": "WDO_OUT_1015_MT", "symbol": "WDO"},
    "wdo_nr5_h4": {"magic": 20260736, "label": "WDO_NR5_H4", "symbol": "WDO"},
    "wdo_nr4_h4": {"magic": 20260737, "label": "WDO_NR4_H4", "symbol": "WDO"},
    "wind_s_01": {"magic": 20260750, "label": "WIND_S_01", "symbol": "WIN"},
    "wind_s_08": {"magic": 20260751, "label": "WIND_S_08", "symbol": "WIN"},
    "wind_l_11": {"magic": 20260752, "label": "WIND_L_11", "symbol": "WIN"},
    "wind_s_12": {"magic": 20260753, "label": "WIND_S_12", "symbol": "WIN"},
    "wind_s_33": {"magic": 20260754, "label": "WIND_S_33", "symbol": "WIN"},
    "wdod_s_18": {"magic": 20260755, "label": "WDOD_S_18", "symbol": "WDO"},
    "wdod_s_25": {"magic": 20260756, "label": "WDOD_S_25", "symbol": "WDO"},
    "wdod_s_34": {"magic": 20260757, "label": "WDOD_S_34", "symbol": "WDO"},
    "eod": {"magic": 20260722, "label": "WIN_EOD_REV", "symbol": "WIN"},
}

_WINGO_UI = {
    "NR5_H4_MT": "nr5_h4_mt", "NR5_H4": "nr5_h4",
    "NR7_H4_MT": "nr7_h4_mt", "NR7_TREND_H4": "nr7_trend", "NR7_BREAK": "nr7",
    "INSIDE_V18_H4": "inside_v18", "INSIDE_VOL15_H4": "inside_vol15",
    "INSIDE_V13_H4": "inside_v13", "INSIDE_1015_H4": "inside_1015",
    "INSIDE_H4": "inside_h4", "INSIDE_AM": "inside_am", "INSIDE_BAR_BRK": "inside",
    "HL_H4": "hl_h4",
    "WIN_PDH_1115": "win_pdh_1115", "WIN_IMP_CONT": "win_imp_cont", "WIN_PDH_H4": "win_pdh_h4",
    "WIN_NR5_1115": "win_nr5_1115", "WIN_NR4_1115": "win_nr4_1115",
    "WIN_INS_PM": "win_ins_pm", "WIN_VOLSPIKE_AM": "win_volspike_am",
    "WIN_IB_BRK_V15": "win_ib_brk_v15", "WIN_HL_MID": "win_hl_mid",
    "WDO_OUT_GAP_DAY": "wdo_out_gap_day", "WDO_OUT_POWER": "wdo_out_power",
    "WDO_IMP_1014_MT": "wdo_imp_1014_mt", "WDO_ENG_1014": "wdo_eng_1014",
    "WDO_GAPC_A60": "wdo_gapc_a60",
    "WDO_NR7_1014_H4": "wdo_nr7_1014", "WDO_PDH_H4": "wdo_pdh_h4",
    "WDO_HL_1014": "wdo_hl_1014", "WDO_OUT_1015_MT": "wdo_out_1015_mt",
    "WDO_NR5_H4": "wdo_nr5_h4", "WDO_NR4_H4": "wdo_nr4_h4",
    "WIND_S_01": "wind_s_01", "WIND_S_08": "wind_s_08", "WIND_L_11": "wind_l_11",
    "WIND_S_12": "wind_s_12", "WIND_S_33": "wind_s_33",
    "WDOD_S_18": "wdod_s_18", "WDOD_S_25": "wdod_s_25", "WDOD_S_34": "wdod_s_34",
}


def _sym():
    return os.getenv("WIN_MT5_SYMBOL", "WINV26").strip()


def _ensure_mt5():
    """Garante terminal MT5 (Monitor às vezes chama shutdown após ler posição)."""
    import MetaTrader5 as mt5
    want_login = int(os.getenv("MT5_LOGIN", "0") or 0)
    want_path = os.getenv("MT5_PATH", "")
    info = mt5.terminal_info()
    acct = mt5.account_info() if info is not None else None
    if info is not None and acct is not None:
        if not want_login or int(acct.login) == want_login:
            return mt5
        # terminal errado (outro broker) — reinicia com credenciais do .env
        try:
            mt5.shutdown()
        except Exception:
            pass
    kw = {}
    if want_path and os.path.exists(want_path):
        kw["path"] = want_path
    pw, srv = os.getenv("MT5_PASSWORD", ""), os.getenv("MT5_SERVER", "")
    if want_login and pw and srv and srv.lower() not in ("metaquotes-demo", "metaquotes-demo2"):
        kw.update(login=want_login, password=pw, server=srv)
    mt5.initialize(**kw)
    return mt5


def _asset_of_sym(sym: str) -> str:
    return "WDO" if "WDO" in (sym or "").upper() else "WIN"


def _read_csv(path: Path):
    if not path.exists():
        return []
    try:
        with open(path, encoding="utf-8") as f:
            return list(csv.DictReader(f))
    except Exception:
        return []


def _since(rng: str) -> str:
    now = datetime.now()
    if rng == "day":
        return now.strftime("%Y-%m-%d")
    if rng == "week":
        return (now - timedelta(days=now.weekday())).strftime("%Y-%m-%d")
    if rng == "month":
        return now.strftime("%Y-%m-01")
    return "0000-00-00"


def engines_status():
    """Status de cada motor WIN/WDO (ligado? última msg? posição?)."""
    out = []

    # V7 — leitura leve (sem reentrar em snapshot completo)
    try:
        from services.v7_engine_runtime import runtime
        runtime.ensure_started()
        sig = runtime.last_eval or {}
        out.append({
            "id": "v7", "label": "V7 · GAP_FADE + ORB", "magic": 20260714,
            "enabled": bool(runtime.enabled),
            "status": runtime.status or "—",
            "last_msg": runtime.last_exec_msg or "",
            "setup": sig.get("setup") or "—",
            "acao": sig.get("acao") or "—",
            "symbol": "WIN",
            "position": runtime.position,
        })
    except Exception as exc:
        out.append({"id": "v7", "label": "V7 · GAP_FADE + ORB", "magic": 20260714,
                    "enabled": False, "status": f"erro: {exc}", "last_msg": "",
                    "setup": "—", "acao": "—", "symbol": "WIN", "position": None})

    # WinGo
    try:
        from services import win_go_runtime as wg
        for name, cfg in wg.SETUPS.items():
            eid = cfg.get("ui_id") or _WINGO_UI.get(name) or name.lower()
            asset = (cfg.get("asset") or "WIN").upper()
            out.append({
                "id": eid, "label": f"WinGo · {name}", "magic": cfg["magic"],
                "enabled": wg._state.get("enabled", True) and wg._enabled(name),
                "status": "ativo" if (wg._state.get("enabled") and wg._enabled(name)) else "desligado",
                "last_msg": f"trades hoje: {wg._state.get('trades_today', {}).get(name, 0)}",
                "setup": name, "acao": "—", "symbol": asset, "position": None,
            })
    except Exception as exc:
        out.append({"id": "wingo", "label": "WinGo", "magic": 0,
                    "enabled": False, "status": f"erro: {exc}", "last_msg": "",
                    "setup": "—", "acao": "—", "symbol": "WIN", "position": None})

    # Win EOD
    try:
        from services import win_eod_runtime as we
        out.append({
            "id": "eod", "label": "WIN_EOD_REV", "magic": we.MAGIC,
            "enabled": bool(we._state.get("enabled")),
            "status": "ativo" if we._state.get("enabled") else "desligado",
            "last_msg": f"trades hoje: {we._state.get('trades_today', 0)}",
            "setup": "WIN_EOD_REV", "acao": "—", "symbol": "WIN", "position": None,
        })
    except Exception as exc:
        out.append({"id": "eod", "label": "WIN_EOD_REV", "magic": 20260722,
                    "enabled": False, "status": f"erro: {exc}", "last_msg": "",
                    "setup": "—", "acao": "—", "symbol": "WIN", "position": None})

    # Anexa posições abertas por magic + ativo (Monitor usa o mesmo magic em WIN e WDO)
    try:
        mt5 = _ensure_mt5()
        if mt5.terminal_info() is not None:
            win_sym = _sym()
            wdo_sym = os.getenv("WDO_MT5_SYMBOL", "WDOU26").strip()
            open_pos = []
            price_by_sym = {}
            for sym in (win_sym, wdo_sym):
                tick = mt5.symbol_info_tick(sym)
                price_by_sym[sym] = float(tick.last or tick.bid or 0) if tick else 0.0
                for p in (mt5.positions_get(symbol=sym) or []):
                    open_pos.append((p, sym))
            # fallback por prefixo se contrato no .env estiver desatualizado
            for want_prefix, want_asset in (("WDO", "WDO"), ("WIN", "WIN")):
                if any(_asset_of_sym(s) == want_asset for _, s in open_pos):
                    continue
                for p in (mt5.positions_get() or []):
                    if str(p.symbol).upper().startswith(want_prefix):
                        open_pos.append((p, p.symbol))
                        if p.symbol not in price_by_sym:
                            tick = mt5.symbol_info_tick(p.symbol)
                            price_by_sym[p.symbol] = (
                                float(tick.last or tick.bid or 0) if tick else 0.0)

            for e in out:
                magic = int(e.get("magic") or 0)
                want = (e.get("symbol") or "").upper()
                if not want:
                    want = "WDO" if (
                        str(e.get("id") or "").startswith("wdo")
                        or str(e.get("setup") or "").startswith("WDO")
                    ) else "WIN"
                hit = None
                for p, sym in open_pos:
                    if int(p.magic) != magic:
                        continue
                    if _asset_of_sym(sym) != want:
                        continue
                    hit = (p, sym)
                    break
                if not hit:
                    continue
                p, sym = hit
                price = price_by_sym.get(sym) or float(p.price_open)
                entry = float(p.price_open)
                sl = float(p.sl or 0)
                tp_mt5 = float(p.tp or 0)
                prev = e.get("position") if isinstance(e.get("position"), dict) else {}
                tp1 = prev.get("tp1")
                tp2 = prev.get("tp2")
                partial = bool(prev.get("partial_done"))
                if tp_mt5:
                    tp = tp_mt5
                elif partial and tp2:
                    tp = float(tp2)
                elif tp1:
                    tp = float(tp1)
                elif prev.get("tp"):
                    tp = float(prev["tp"])
                else:
                    risk = abs(entry - sl) if sl else 0
                    if risk > 0 and magic == 20260714:
                        buy = p.type == 0
                        sign = 1 if buy else -1
                        tp1 = entry + sign * 1.0 * risk
                        tp2 = entry + sign * 2.5 * risk
                        tp = tp2 if partial else tp1
                    else:
                        tp = 0
                buy = p.type == 0
                cur = price or entry
                pts = (cur - entry) if buy else (entry - cur)
                setup_name = (prev.get("setup") if prev.get("setup") and prev.get("setup") != "?"
                              else (e.get("setup") or e.get("label")))
                e["position"] = {
                    "dir": "COMPRA" if buy else "VENDA",
                    "volume": float(p.volume),
                    "entry": entry,
                    "sl": sl,
                    "tp": tp if tp else None,
                    "tp1": round(float(tp1), 1) if tp1 else None,
                    "tp2": round(float(tp2), 1) if tp2 else None,
                    "tp_label": prev.get("tp_label"),
                    "price": cur,
                    "profit_brl": float(p.profit),
                    "pnl_pts": round(pts, 1),
                    "dist_sl_pts": round(abs(cur - sl), 1) if sl else None,
                    "dist_tp_pts": round(abs(tp - cur), 1) if tp else None,
                    "ticket": int(p.ticket),
                    "setup": setup_name,
                    "magic": int(p.magic),
                    "engine_id": e.get("id"),
                    "partial_done": partial,
                    "r_now": prev.get("r_now"),
                    "symbol": sym,
                }
    except Exception:
        pass
    return out


def close_by_magic(magic: int, asset: str = None):
    """Fecha a mercado a posição do magic informado (qualquer motor do hub)."""
    try:
        mt5 = _ensure_mt5()
        if mt5.terminal_info() is None:
            return False, "MT5 offline"
        win_sym = _sym()
        wdo_sym = os.getenv("WDO_MT5_SYMBOL", "WDOU26").strip()
        want = (asset or "").upper().strip() or None
        p = None
        sym = win_sym
        candidates = []
        for s in (win_sym, wdo_sym):
            for x in (mt5.positions_get(symbol=s) or []):
                if int(x.magic) == int(magic):
                    candidates.append((x, s))
        if not candidates:
            for x in (mt5.positions_get() or []):
                if int(x.magic) == int(magic):
                    candidates.append((x, x.symbol))
        for x, s in candidates:
            if want and _asset_of_sym(s) != want:
                continue
            p, sym = x, s
            break
        if not p and candidates:
            p, sym = candidates[0]
        if not p:
            return False, "sem posição"
        tick = mt5.symbol_info_tick(sym)
        if not tick:
            return False, "sem tick"
        buy = p.type == 0
        req = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": sym,
            "volume": float(p.volume),
            "type": mt5.ORDER_TYPE_SELL if buy else mt5.ORDER_TYPE_BUY,
            "position": p.ticket,
            "price": float(tick.bid if buy else tick.ask),
            "deviation": 50,
            "magic": int(magic),
            "comment": "hub-close",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        r = mt5.order_send(req)
        if r is None or r.retcode != mt5.TRADE_RETCODE_DONE:
            return False, f"retcode {getattr(r, 'retcode', None)}"
        return True, None
    except Exception as exc:
        return False, str(exc)


_WINGO_LOG_FIELDS = [
    "ts", "evento", "dir", "preco", "sl", "tp", "vol", "pnl_brl", "magic", "pos_id",
]

_WINGO_CSV_SETUPS = (
    ("win_go_nr5_h4_mt.csv", "NR5_H4_MT"),
    ("win_go_nr5_h4.csv", "NR5_H4"),
    ("win_go_nr7_h4_mt.csv", "NR7_H4_MT"),
    ("win_go_nr7_trend_h4.csv", "NR7_TREND_H4"),
    ("win_go_nr7_break.csv", "NR7_BREAK"),
    ("win_go_inside_v18_h4.csv", "INSIDE_V18_H4"),
    ("win_go_inside_vol15_h4.csv", "INSIDE_VOL15_H4"),
    ("win_go_inside_v13_h4.csv", "INSIDE_V13_H4"),
    ("win_go_inside_1015_h4.csv", "INSIDE_1015_H4"),
    ("win_go_inside_h4.csv", "INSIDE_H4"),
    ("win_go_inside_am.csv", "INSIDE_AM"),
    ("win_go_inside_bar_brk.csv", "INSIDE_BAR_BRK"),
    ("win_go_hl_h4.csv", "HL_H4"),
    ("win_go_win_pdh_1115.csv", "WIN_PDH_1115"),
    ("win_go_win_imp_cont.csv", "WIN_IMP_CONT"),
    ("win_go_win_pdh_h4.csv", "WIN_PDH_H4"),
    ("win_go_win_nr5_1115.csv", "WIN_NR5_1115"),
    ("win_go_win_nr4_1115.csv", "WIN_NR4_1115"),
    ("win_go_win_ins_pm.csv", "WIN_INS_PM"),
    ("win_go_win_volspike_am.csv", "WIN_VOLSPIKE_AM"),
    ("win_go_win_ib_brk_v15.csv", "WIN_IB_BRK_V15"),
    ("win_go_win_hl_mid.csv", "WIN_HL_MID"),
    ("win_go_wdo_out_gap_day.csv", "WDO_OUT_GAP_DAY"),
    ("win_go_wdo_out_power.csv", "WDO_OUT_POWER"),
    ("win_go_wdo_imp_1014_mt.csv", "WDO_IMP_1014_MT"),
    ("win_go_wdo_eng_1014.csv", "WDO_ENG_1014"),
    ("win_go_wdo_gapc_a60.csv", "WDO_GAPC_A60"),
    ("win_go_wdo_nr7_1014_h4.csv", "WDO_NR7_1014_H4"),
    ("win_go_wdo_pdh_h4.csv", "WDO_PDH_H4"),
    ("win_go_wdo_hl_1014.csv", "WDO_HL_1014"),
    ("win_go_wdo_out_1015_mt.csv", "WDO_OUT_1015_MT"),
    ("win_go_wdo_nr5_h4.csv", "WDO_NR5_H4"),
    ("win_go_wdo_nr4_h4.csv", "WDO_NR4_H4"),
    ("win_go_wind_s_01.csv", "WIND_S_01"),
    ("win_go_wind_s_08.csv", "WIND_S_08"),
    ("win_go_wind_l_11.csv", "WIND_L_11"),
    ("win_go_wind_s_12.csv", "WIND_S_12"),
    ("win_go_wind_s_33.csv", "WIND_S_33"),
    ("win_go_wdod_s_18.csv", "WDOD_S_18"),
    ("win_go_wdod_s_25.csv", "WDOD_S_25"),
    ("win_go_wdod_s_34.csv", "WDOD_S_34"),
)


def _fnum(v, default=None):
    if v is None or v == "":
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _round_px(v, symbol="WIN"):
    n = _fnum(v)
    if n is None:
        return ""
    if str(symbol).upper().startswith("WDO"):
        return round(n, 1)
    return round(n, 0)


def _round_level(v, symbol="WIN"):
    n = _fnum(v)
    if n is None:
        return ""
    if str(symbol).upper().startswith("WDO"):
        return round(n, 1)
    return round(n, 1)


def _compute_r(entry, stop, exit_px, direction, profit_brl=None):
    """R em preço (move/risco). Fallback: sinal do P&L se não houver stop."""
    e, s, x = _fnum(entry), _fnum(stop), _fnum(exit_px)
    risk = abs(e - s) if e is not None and s is not None else 0.0
    if risk > 0 and x is not None and e is not None:
        d = (direction or "").upper()
        if d.startswith("V"):
            move = e - x
        elif d.startswith("C"):
            move = x - e
        else:
            move = abs(e - x) * (1 if (_fnum(profit_brl) or 0) >= 0 else -1)
        return round(move / risk, 2)
    p = _fnum(profit_brl)
    if p is None or p == 0:
        return 0.0
    return round(1.0 if p > 0 else -1.0, 2)


def _read_wingo_csv(path: Path):
    """Lê CSV WinGo por posição nas colunas (_LOG_FIELDS), mesmo com header antigo."""
    if not path.exists():
        return []
    try:
        with open(path, encoding="utf-8", newline="") as f:
            raw = list(csv.reader(f))
    except Exception:
        return []
    if not raw:
        return []
    start = 1 if raw[0] and str(raw[0][0]).strip().lower() == "ts" else 0
    out = []
    for parts in raw[start:]:
        if not parts or all(not str(c).strip() for c in parts):
            continue
        padded = list(parts) + [""] * max(0, len(_WINGO_LOG_FIELDS) - len(parts))
        row = {k: padded[i] for i, k in enumerate(_WINGO_LOG_FIELDS)}
        out.append(row)
    return out


def _wingo_csv_events(since: str):
    """Eventos brutos dos CSVs WinGo (+ EOD), já normalizados."""
    events = []
    for fname, setup in _WINGO_CSV_SETUPS:
        for r in _read_wingo_csv(LOG / fname):
            ts = r.get("ts") or ""
            if ts[:10] < since:
                continue
            ev = (r.get("evento") or "").upper().strip()
            if not ev:
                continue
            sym = "WDO" if str(setup).startswith("WDO") else "WIN"
            events.append({
                "ts": ts, "evento": ev, "setup": setup, "symbol": sym,
                "dir": (r.get("dir") or "").upper(),
                "preco": _fnum(r.get("preco")),
                "sl": _fnum(r.get("sl")), "tp": _fnum(r.get("tp")),
                "vol": _fnum(r.get("vol"), 0) or "",
                "pnl_brl": _fnum(r.get("pnl_brl")),
                "magic": r.get("magic") or "",
                "pos_id": str(r.get("pos_id") or "").strip(),
                "engine": "wingo",
            })
    for r in _read_csv(LOG / "win_eod_trades.csv"):
        ts = r.get("ts") or ""
        if ts[:10] < since:
            continue
        ev = (r.get("evento") or "").upper().strip()
        if not ev:
            continue
        # schema EOD legado: ts,evento,preco,sl,tp,... — saída guarda PnL em sl
        is_in = ev == "ENTRADA"
        pnl = _fnum(r.get("pnl_brl"))
        if not is_in and pnl is None:
            pnl = _fnum(r.get("sl"))
        events.append({
            "ts": ts, "evento": ev, "setup": "WIN_EOD_REV", "symbol": "WIN",
            "dir": (r.get("dir") or "").upper() or ("COMPRA" if is_in else ""),
            "preco": _fnum(r.get("preco")),
            "sl": _fnum(r.get("sl")) if is_in else None,
            "tp": _fnum(r.get("tp")) if is_in else None,
            "vol": _fnum(r.get("vol"), 0) or "",
            "pnl_brl": None if is_in else pnl,
            "magic": r.get("magic") or "20260722",
            "pos_id": str(r.get("pos_id") or "").strip(),
            "engine": "eod",
        })
    return events


def _mt5_hub_positions(since: str):
    """Trades fechados do hub via deals MT5, 1 por position_id (fonte de P&L)."""
    out = []
    open_rows = []
    try:
        mt5 = _ensure_mt5()
        if mt5.terminal_info() is None:
            return out, open_rows
        magic_to_setup = {int(v["magic"]): v["label"] for v in ENGINES.values()}
        magic_to_setup.setdefault(20260722, "WIN_EOD_REV")
        start = datetime.strptime(since, "%Y-%m-%d")
        # margem: ENTRADA CSV pode ser D-1 no fuso local vs deal
        deals = mt5.history_deals_get(start - timedelta(days=2), datetime.now()) or []
        by_pos = {}
        for d in deals:
            magic = int(d.magic or 0)
            if magic not in magic_to_setup:
                continue
            pos_id = int(getattr(d, "position_id", 0) or 0)
            if not pos_id:
                continue
            bucket = by_pos.setdefault(pos_id, {
                "magic": magic, "setup": magic_to_setup[magic],
                "symbol_mt5": d.symbol, "entries": [], "exits": [],
            })
            entry_flag = getattr(d, "entry", None)
            try:
                entry_flag = int(entry_flag)
            except (TypeError, ValueError):
                entry_flag = -1
            item = {
                "ts": datetime.fromtimestamp(d.time).strftime("%Y-%m-%d %H:%M:%S"),
                "price": float(d.price), "volume": float(d.volume or 0),
                "profit": float(d.profit or 0), "comment": d.comment or "",
                "ticket": int(d.ticket), "type": int(d.type),
            }
            if entry_flag == 0:
                bucket["entries"].append(item)
            elif entry_flag == 1:
                bucket["exits"].append(item)

        for pos_id, b in by_pos.items():
            if not b["exits"]:
                continue
            exits = sorted(b["exits"], key=lambda x: x["ts"])
            entries = sorted(b["entries"], key=lambda x: x["ts"])
            ts_close = exits[-1]["ts"]
            if ts_close[:10] < since:
                continue
            profit = sum(x["profit"] for x in exits)
            exit_px = exits[-1]["price"]
            vol = sum(x["volume"] for x in exits) or (entries[0]["volume"] if entries else 0)
            entry_px = entries[0]["price"] if entries else ""
            ts_open = entries[0]["ts"] if entries else ""
            # direção pelo tipo do deal de entrada (0=buy)
            direction = ""
            if entries:
                direction = "COMPRA" if entries[0]["type"] == 0 else "VENDA"
            cmt = " ".join((x["comment"] or "").lower() for x in exits)
            if "tp" in cmt:
                reason = "TP"
            elif "sl" in cmt:
                reason = "SL"
            else:
                reason = "SAIDA"
            setup = b["setup"]
            sym = "WDO" if str(setup).startswith("WDO") or str(b["symbol_mt5"]).upper().startswith("WDO") else "WIN"
            out.append({
                "pos_id": str(pos_id),
                "ts": ts_close, "ts_open": ts_open, "ts_close": ts_close,
                "setup": setup, "dir": direction, "volume": vol,
                "entry": entry_px, "stop": "", "tp": "",
                "exit": exit_px, "exit_reason": reason,
                "ai_veredito": "", "profit_brl": round(profit, 2),
                "r_multiple": "", "engine": "mt5",
                "symbol": sym, "evento": reason, "magic": b["magic"],
            })

        # abertas agora
        for p in (mt5.positions_get() or []):
            magic = int(p.magic or 0)
            if magic not in magic_to_setup:
                continue
            setup = magic_to_setup[magic]
            sym = "WDO" if str(p.symbol).upper().startswith("WDO") else "WIN"
            ts_open = datetime.fromtimestamp(p.time).strftime("%Y-%m-%d %H:%M:%S")
            open_rows.append({
                "pos_id": str(int(p.ticket)),
                "ts": ts_open, "ts_open": ts_open, "ts_close": "",
                "setup": setup,
                "dir": "COMPRA" if p.type == 0 else "VENDA",
                "volume": float(p.volume),
                "entry": float(p.price_open),
                "stop": float(p.sl or 0) or "",
                "tp": float(p.tp or 0) or "",
                "exit": "", "exit_reason": "ABERTO",
                "ai_veredito": "",
                "profit_brl": round(float(p.profit), 2),
                "r_multiple": "", "engine": "mt5",
                "symbol": sym, "evento": "ABERTO", "magic": magic,
            })
    except Exception:
        pass
    return out, open_rows


def _enrich_trade_from_csv(trade, csv_events):
    """Completa SL/TP/dir/EOD a partir do CSV ENTRADA / EOD_CLOSE."""
    setup = trade.get("setup")
    entry = _fnum(trade.get("entry"))
    pos_id = str(trade.get("pos_id") or "")
    # EOD_CLOSE / motivo CSV para o mesmo pos_id
    for ev in csv_events:
        if ev["evento"] not in ("EOD_CLOSE", "TP", "SL", "SAIDA"):
            continue
        if pos_id and ev.get("pos_id") == pos_id:
            if ev["evento"] == "EOD_CLOSE":
                trade["exit_reason"] = "EOD_CLOSE"
                trade["evento"] = "EOD_CLOSE"
            break
    # ENTRADA: match por setup + preço
    best = None
    best_dist = 1e18
    for ev in csv_events:
        if ev["evento"] != "ENTRADA" or ev.get("setup") != setup:
            continue
        px = ev.get("preco")
        if entry is None or px is None:
            continue
        dist = abs(px - entry)
        tol = 1.0 if trade.get("symbol") == "WDO" else 50.0
        if dist <= tol and dist < best_dist:
            best, best_dist = ev, dist
    if best:
        if not trade.get("dir") and best.get("dir"):
            trade["dir"] = best["dir"]
        if best.get("sl") is not None:
            trade["stop"] = best["sl"]
        if best.get("tp") is not None:
            trade["tp"] = best["tp"]
        if not trade.get("ts_open"):
            trade["ts_open"] = best["ts"]
    return trade


def _finalize_trade_row(trade):
    sym = trade.get("symbol") or "WIN"
    trade["entry"] = _round_px(trade.get("entry"), sym)
    trade["exit"] = _round_px(trade.get("exit"), sym) if trade.get("exit") not in ("", None) else ""
    trade["stop"] = _round_level(trade.get("stop"), sym) if trade.get("stop") not in ("", None) else ""
    trade["tp"] = _round_level(trade.get("tp"), sym) if trade.get("tp") not in ("", None) else ""
    if trade.get("volume") not in ("", None):
        try:
            trade["volume"] = float(trade["volume"])
        except Exception:
            pass
    pb = trade.get("profit_brl")
    if pb not in ("", None):
        trade["profit_brl"] = round(float(pb), 2)

    if trade.get("exit_reason") == "ABERTO":
        e, s = _fnum(trade.get("entry")), _fnum(trade.get("stop"))
        p = _fnum(trade.get("profit_brl"))
        risk = abs(e - s) if e is not None and s is not None else 0.0
        pv = 10.0 if sym == "WDO" else 0.20
        if risk > 0 and p is not None and pv > 0:
            trade["r_multiple"] = round(p / (risk * pv), 2)
        else:
            trade["r_multiple"] = ""
    else:
        trade["r_multiple"] = _compute_r(
            trade.get("entry"), trade.get("stop"), trade.get("exit"),
            trade.get("dir"), trade.get("profit_brl"),
        )
    trade["ts"] = trade.get("ts_close") or trade.get("ts_open") or trade.get("ts") or ""
    return trade


def _consolidate_from_csv_only(since: str, csv_events):
    """Fallback sem MT5: emparelha ENTRADA↔saída por setup (FIFO + pos_id)."""
    entries = [e for e in csv_events if e["evento"] == "ENTRADA"]
    exits = [e for e in csv_events if e["evento"] in ("TP", "SL", "SAIDA", "EOD_CLOSE")]
    # dedupe saídas iguais (EOD+SAIDA mesmo pos_id/pnl)
    dedup_exits, seen = [], set()
    for x in sorted(exits, key=lambda e: e["ts"]):
        pid = x.get("pos_id") or ""
        pnl = round(x["pnl_brl"], 2) if x.get("pnl_brl") is not None else None
        key = (x["setup"], pid or x["ts"][:16], pnl)
        if key in seen:
            # preferir EOD_CLOSE / TP / SL sobre SAIDA genérica
            prev_i = next(i for i, p in enumerate(dedup_exits)
                          if (p["setup"], (p.get("pos_id") or p["ts"][:16]),
                              round(p["pnl_brl"], 2) if p.get("pnl_brl") is not None else None) == key)
            prev = dedup_exits[prev_i]
            rank = {"TP": 3, "SL": 3, "EOD_CLOSE": 2, "SAIDA": 1}
            if rank.get(x["evento"], 0) > rank.get(prev["evento"], 0):
                dedup_exits[prev_i] = x
            continue
        seen.add(key)
        dedup_exits.append(x)

    used_entry = set()
    rows = []
    for x in dedup_exits:
        match = None
        mid = None
        if x.get("pos_id"):
            # entrada não tem pos_id — match por preço/setup próximo no tempo
            pass
        best_i, best_score = None, None
        for i, e in enumerate(entries):
            if i in used_entry or e["setup"] != x["setup"]:
                continue
            # permite entrada "depois" no relógio local (fuso MT5)
            score = abs((_fnum(e["preco"]) or 0) - (_fnum(x["preco"]) or 0))
            # prioriza entradas cujo preço de stop/tp batem com saída
            if x["evento"] in ("SL", "TP") and e.get("sl") is not None and x.get("preco") is not None:
                if abs(e["sl"] - x["preco"]) < (1.0 if e["symbol"] == "WDO" else 30):
                    score -= 1000
            if e.get("tp") is not None and x.get("preco") is not None:
                if abs(e["tp"] - x["preco"]) < (1.0 if e["symbol"] == "WDO" else 30):
                    score -= 1000
            if best_score is None or score < best_score:
                best_score, best_i = score, i
        if best_i is not None:
            used_entry.add(best_i)
            match = entries[best_i]
        entry_px = match["preco"] if match else ""
        direction = (match or {}).get("dir") or ""
        stop = (match or {}).get("sl") or ""
        tp = (match or {}).get("tp") or ""
        ts_open = (match or {}).get("ts") or ""
        pnl = x.get("pnl_brl")
        rows.append({
            "pos_id": x.get("pos_id") or "",
            "ts": x["ts"], "ts_open": ts_open, "ts_close": x["ts"],
            "setup": x["setup"], "dir": direction,
            "volume": x.get("vol") or (match or {}).get("vol") or "",
            "entry": entry_px, "stop": stop, "tp": tp,
            "exit": x.get("preco") or "",
            "exit_reason": x["evento"],
            "ai_veredito": "",
            "profit_brl": round(pnl, 2) if pnl is not None else "",
            "r_multiple": "",
            "engine": x.get("engine") or "wingo",
            "symbol": x["symbol"], "evento": x["evento"],
        })
    # entradas sem saída → só se recentes e sem match (possível aberto fantasma:
    # omitimos se já houver saída do setup no dia)
    closed_setups = {r["setup"] for r in rows}
    for i, e in enumerate(entries):
        if i in used_entry:
            continue
        # se o setup já tem fechamentos no período, entrada órfã é lixo de fuso/netting
        if e["setup"] in closed_setups:
            continue
        rows.append({
            "pos_id": "", "ts": e["ts"], "ts_open": e["ts"], "ts_close": "",
            "setup": e["setup"], "dir": e.get("dir") or "",
            "volume": e.get("vol") or "",
            "entry": e.get("preco") or "", "stop": e.get("sl") or "",
            "tp": e.get("tp") or "", "exit": "",
            "exit_reason": "ABERTO", "ai_veredito": "",
            "profit_brl": "", "r_multiple": "",
            "engine": e.get("engine") or "wingo",
            "symbol": e["symbol"], "evento": "ABERTO",
        })
    return rows


def unified_trades(limit=40, rng="all"):
    """Histórico unificado: 1 linha = 1 trade lógico (entrada+saída+P&L)."""
    since = _since(rng)
    rows = []

    # V7 CSV (já vem consolidado) — período por data de FECHAMENTO (ou abertura se aberto)
    for r in _read_csv(LOG / "v7_trader_trades.csv"):
        ts_close = (r.get("ts_close") or "").strip()
        ts_open = (r.get("ts_open") or "").strip()
        day = (ts_close or ts_open)[:10]
        if not day or day < since:
            continue
        row = {
            "ts": ts_close or ts_open, "ts_open": ts_open or ts_close,
            "ts_close": ts_close,
            "setup": r.get("setup") or "V7", "dir": r.get("dir") or "",
            "volume": r.get("volume") or "", "entry": r.get("entry") or "",
            "stop": r.get("stop") or "", "tp": r.get("tp") or "",
            "exit": r.get("exit") or "",
            "exit_reason": r.get("exit_reason") or "",
            "ai_veredito": r.get("ai_veredito") or "",
            "profit_brl": r.get("profit_brl") or "",
            "r_multiple": r.get("r_multiple") or "",
            "engine": "v7", "symbol": "WIN", "evento": "TRADE",
        }
        if row["r_multiple"] in ("", None) and row["profit_brl"] not in ("", None):
            row["r_multiple"] = _compute_r(
                row["entry"], row["stop"], row["exit"], row["dir"], row["profit_brl"])
        rows.append(_finalize_trade_row(row))

    csv_events = _wingo_csv_events(since)
    mt5_closed, mt5_open = _mt5_hub_positions(since)

    if mt5_closed or mt5_open:
        for t in mt5_closed:
            _enrich_trade_from_csv(t, csv_events)
            rows.append(_finalize_trade_row(t))
        for t in mt5_open:
            _enrich_trade_from_csv(t, csv_events)
            rows.append(_finalize_trade_row(t))
    else:
        for t in _consolidate_from_csv_only(since, csv_events):
            rows.append(_finalize_trade_row(t))

    rows.sort(key=lambda x: x.get("ts") or "", reverse=True)
    return rows[:limit]


def unified_period(rng="day"):
    """KPIs + by_setup a partir de trades lógicos consolidados."""
    rows = unified_trades(limit=5000, rng=rng)
    closed = []
    for r in rows:
        if (r.get("exit_reason") or "") == "ABERTO":
            continue
        pb = r.get("profit_brl")
        if pb is None or pb == "":
            continue
        p = _fnum(pb)
        if p is None:
            continue
        rr = _fnum(r.get("r_multiple"), 0.0) or 0.0
        closed.append({**r, "_p": p, "_r": rr})

    n = wins = losses = 0
    pnl = pr = gw = gl = 0.0
    by_setup = {}
    equity = []
    daily = {}
    cum = 0.0
    peak = 0.0
    dd = 0.0
    for r in sorted(closed, key=lambda x: x.get("ts_close") or x.get("ts") or ""):
        p, rr = r["_p"], r["_r"]
        n += 1
        pnl += p
        pr += rr
        if p > 0:
            wins += 1
            gw += abs(rr) if rr else abs(p)
        elif p < 0:
            losses += 1
            gl += abs(rr) if rr else abs(p)
        s = r.get("setup") or "?"
        d = by_setup.setdefault(s, {"n": 0, "pnl_r": 0.0, "pnl_brl": 0.0, "wins": 0})
        d["n"] += 1
        d["pnl_r"] += rr
        d["pnl_brl"] += p
        d["wins"] += 1 if p > 0 else 0
        day = (r.get("ts_close") or r.get("ts") or "")[:10] or "?"
        ddrow = daily.setdefault(day, {"date": day, "trades": 0, "wins": 0, "losses": 0, "pnl_brl": 0.0, "pnl_r": 0.0})
        ddrow["trades"] += 1
        ddrow["pnl_brl"] += p
        ddrow["pnl_r"] += rr
        if p > 0:
            ddrow["wins"] += 1
        elif p < 0:
            ddrow["losses"] += 1
        # curva em R real (sem inventar ±1 quando R=0 e P&L≠0)
        step = rr if rr else (0.0 if p == 0 else (1.0 if p > 0 else -1.0))
        cum += step
        peak = max(peak, cum)
        dd = min(dd, cum - peak)
        equity.append(round(cum, 2))
    for d in by_setup.values():
        d["pnl_r"] = round(d["pnl_r"], 2)
        d["pnl_brl"] = round(d["pnl_brl"], 2)
    daily_list = []
    for day in sorted(daily.keys()):
        row = daily[day]
        row["pnl_brl"] = round(row["pnl_brl"], 2)
        row["pnl_r"] = round(row["pnl_r"], 2)
        daily_list.append(row)
    dec = wins + losses
    return {
        "range": rng, "trades": n, "wins": wins, "losses": losses,
        "win_rate": round(wins / dec * 100, 1) if dec else 0.0,
        "pnl_brl": round(pnl, 2), "pnl_r": round(pr, 2),
        "pf": round(gw / gl, 2) if gl > 0 else (99.0 if gw > 0 else 0.0),
        "max_dd_r": round(dd, 2), "by_setup": by_setup, "equity": equity,
        "daily": daily_list,
    }


def set_engine_enabled(engine_id: str, enabled: bool):
    """Liga/desliga motor pelo id da UI."""
    eid = (engine_id or "").lower().strip()
    if eid == "v7":
        from services.v7_engine_runtime import runtime
        runtime.ensure_started()
        runtime.set_enabled(bool(enabled))
        return True, "v7"

    _WINGO_FLAGS = {
        "nr7": "WIN_GO_NR7",
        "nr7_trend": "WIN_GO_NR7_TREND",
        "nr7_trend_h4": "WIN_GO_NR7_TREND",
        "nr7_h4_mt": "WIN_GO_NR7_H4_MT",
        "nr5_h4": "WIN_GO_NR5_H4",
        "nr5_h4_mt": "WIN_GO_NR5_H4_MT",
        "inside": "WIN_GO_INSIDE",
        "inside_h4": "WIN_GO_INSIDE_H4",
        "inside_v18": "WIN_GO_INSIDE_V18",
        "inside_vol15": "WIN_GO_INSIDE_VOL15",
        "inside_v13": "WIN_GO_INSIDE_V13",
        "inside_1015": "WIN_GO_INSIDE_1015",
        "inside_am": "WIN_GO_INSIDE_AM",
        "hl_h4": "WIN_GO_HL_H4",
        "win_pdh_1115": "WIN_GO_PDH_1115",
        "win_imp_cont": "WIN_GO_IMP_CONT",
        "win_pdh_h4": "WIN_GO_PDH_H4",
        "win_nr5_1115": "WIN_GO_NR5_1115",
        "win_nr4_1115": "WIN_GO_NR4_1115",
        "win_ins_pm": "WIN_GO_INS_PM",
        "win_volspike_am": "WIN_GO_VOLSPIKE_AM",
        "win_ib_brk_v15": "WIN_GO_IB_BRK_V15",
        "win_hl_mid": "WIN_GO_HL_MID",
        "wdo_out_gap_day": "WIN_GO_WDO_OUT_GAP_DAY",
        "wdo_out_power": "WIN_GO_WDO_OUT_POWER",
        "wdo_imp_1014_mt": "WIN_GO_WDO_IMP_1014_MT",
        "wdo_eng_1014": "WIN_GO_WDO_ENG_1014",
        "wdo_gapc_a60": "WIN_GO_WDO_GAPC_A60",
        "wdo_nr7_1014": "WIN_GO_WDO_NR7_1014",
        "wdo_pdh_h4": "WIN_GO_WDO_PDH_H4",
        "wdo_hl_1014": "WIN_GO_WDO_HL_1014",
        "wdo_out_1015_mt": "WIN_GO_WDO_OUT_1015_MT",
        "wdo_nr5_h4": "WIN_GO_WDO_NR5_H4",
        "wdo_nr4_h4": "WIN_GO_WDO_NR4_H4",
        "wind_s_01": "WIN_GO_DISC_WIND_S_01",
        "wind_s_08": "WIN_GO_DISC_WIND_S_08",
        "wind_l_11": "WIN_GO_DISC_WIND_L_11",
        "wind_s_12": "WIN_GO_DISC_WIND_S_12",
        "wind_s_33": "WIN_GO_DISC_WIND_S_33",
        "wdod_s_18": "WIN_GO_DISC_WDOD_S_18",
        "wdod_s_25": "WIN_GO_DISC_WDOD_S_25",
        "wdod_s_34": "WIN_GO_DISC_WDOD_S_34",
    }
    if eid in _WINGO_FLAGS:
        from services import win_go_runtime as wg
        os.environ[_WINGO_FLAGS[eid]] = "1" if enabled else "0"
        if enabled:
            wg._state["enabled"] = True
            os.environ["WIN_GO_ENABLED"] = "1"
        return True, eid
    if eid == "wingo":
        from services import win_go_runtime as wg
        wg._state["enabled"] = bool(enabled)
        os.environ["WIN_GO_ENABLED"] = "1" if enabled else "0"
        return True, eid
    if eid == "eod":
        from services import win_eod_runtime as we
        we._state["enabled"] = bool(enabled)
        return True, "eod"
    return False, f"engine desconhecido: {engine_id}"
