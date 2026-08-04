"""
win_go_runtime.py — setups 🟢 GO B3 ao vivo (paper/demo).

v3–v6 WIN + WDO v6/v7 (NR5/NR4/NR7_1014/PDH) + mega B3 (PDH/IMP/HL).
Magics próprios, thread B3.
Ordem = prioridade (1 entrada/barra/ativo): mais seletivo primeiro.
NÃO altera Monitor MT5 (v6), Monitor V7 nem WIN_EOD_REV.

Controles: WIN_GO_ENABLED, WIN_GO_VOLUME, WIN_GO_<SETUP>=1|0
Zeragem EOD 17:55.
"""
import csv
import logging
import os
import threading
import time
from datetime import datetime

import pandas as pd

from services.win_go_setups import (
    nr7_break_signal, inside_bar_break_signal, nr7_trend_h4_signal,
    nr7_h4_mt_signal, inside_h4_signal, inside_v18_h4_signal, inside_am_signal,
    nr5_h4_signal, nr5_h4_mt_signal, inside_1015_h4_signal,
    inside_v13_h4_signal, inside_vol15_h4_signal, hl_h4_signal,
    nr4_h4_signal, nr7_1014_h4_signal, pdh_h4_signal,
    win_pdh_1115_signal, win_pdh_h4_signal, win_imp_cont_signal,
    wdo_hl_1014_signal, wdo_out_1015_mt_signal,
    win_nr5_1115_signal, win_nr4_1115_signal, win_ins_pm_signal,
    win_volspike_am_signal, win_ib_brk_v15_signal, win_hl_mid_signal,
    wdo_out_gap_day_signal, wdo_out_power_signal, wdo_imp_1014_mt_signal,
    wdo_eng_1014_signal, wdo_gapc_a60_signal,
)
from services.b3_discovery_paths import make_wingo_signal, win_wdo_disc_paths

logger = logging.getLogger(__name__)

# Discovery GOs WIN/WDO (harness 27/07) — magics 20260750+
_DISC_MAGIC = {
    "WIND_S_01": 20260750, "WIND_S_08": 20260751, "WIND_L_11": 20260752,
    "WIND_S_12": 20260753, "WIND_S_33": 20260754,
    "WDOD_S_18": 20260755, "WDOD_S_25": 20260756, "WDOD_S_34": 20260757,
}

# asset: WIN|WDO — 1 entrada por barra por ativo
SETUPS = {
    # ── WIN: seletivos primeiro ──
    "NR5_H4_MT": {
        "magic": 20260730, "comment": "WinNR5MT", "signal": nr5_h4_mt_signal,
        "env_flag": "WIN_GO_NR5_H4_MT", "ui_id": "nr5_h4_mt", "go": "v6", "asset": "WIN",
    },
    "NR5_H4": {
        "magic": 20260731, "comment": "WinNR5H4", "signal": nr5_h4_signal,
        "env_flag": "WIN_GO_NR5_H4", "ui_id": "nr5_h4", "go": "v6", "asset": "WIN",
    },
    "NR7_H4_MT": {
        "magic": 20260726, "comment": "WinNR7MT", "signal": nr7_h4_mt_signal,
        "env_flag": "WIN_GO_NR7_H4_MT", "ui_id": "nr7_h4_mt", "go": "v5", "asset": "WIN",
    },
    "NR7_TREND_H4": {
        "magic": 20260725, "comment": "WinNR7H4", "signal": nr7_trend_h4_signal,
        "env_flag": "WIN_GO_NR7_TREND", "ui_id": "nr7_trend", "go": "v4", "asset": "WIN",
    },
    "NR7_BREAK": {
        "magic": 20260723, "comment": "WinNR7", "signal": nr7_break_signal,
        "env_flag": "WIN_GO_NR7", "ui_id": "nr7", "go": "v3", "asset": "WIN",
    },
    "INSIDE_V18_H4": {
        "magic": 20260728, "comment": "WinInsV18", "signal": inside_v18_h4_signal,
        "env_flag": "WIN_GO_INSIDE_V18", "ui_id": "inside_v18", "go": "v5", "asset": "WIN",
    },
    "INSIDE_VOL15_H4": {
        "magic": 20260732, "comment": "WinInsV15", "signal": inside_vol15_h4_signal,
        "env_flag": "WIN_GO_INSIDE_VOL15", "ui_id": "inside_vol15", "go": "v6", "asset": "WIN",
    },
    "INSIDE_V13_H4": {
        "magic": 20260733, "comment": "WinInsV13", "signal": inside_v13_h4_signal,
        "env_flag": "WIN_GO_INSIDE_V13", "ui_id": "inside_v13", "go": "v6", "asset": "WIN",
    },
    "INSIDE_1015_H4": {
        "magic": 20260734, "comment": "WinIns1015", "signal": inside_1015_h4_signal,
        "env_flag": "WIN_GO_INSIDE_1015", "ui_id": "inside_1015", "go": "v6", "asset": "WIN",
    },
    "INSIDE_H4": {
        "magic": 20260727, "comment": "WinInsH4", "signal": inside_h4_signal,
        "env_flag": "WIN_GO_INSIDE_H4", "ui_id": "inside_h4", "go": "v5", "asset": "WIN",
    },
    "INSIDE_AM": {
        "magic": 20260729, "comment": "WinInsAM", "signal": inside_am_signal,
        "env_flag": "WIN_GO_INSIDE_AM", "ui_id": "inside_am", "go": "v5", "asset": "WIN",
    },
    "INSIDE_BAR_BRK": {
        "magic": 20260724, "comment": "WinInside", "signal": inside_bar_break_signal,
        "env_flag": "WIN_GO_INSIDE", "ui_id": "inside", "go": "v3", "asset": "WIN",
    },
    "WIN_PDH_1115": {
        "magic": 20260740, "comment": "WinPDH1115", "signal": win_pdh_1115_signal,
        "env_flag": "WIN_GO_PDH_1115", "ui_id": "win_pdh_1115", "go": "mega", "asset": "WIN",
    },
    "WIN_IMP_CONT": {
        "magic": 20260741, "comment": "WinImpCont", "signal": win_imp_cont_signal,
        "env_flag": "WIN_GO_IMP_CONT", "ui_id": "win_imp_cont", "go": "mega", "asset": "WIN",
    },
    "WIN_PDH_H4": {
        "magic": 20260743, "comment": "WinPDHH4", "signal": win_pdh_h4_signal,
        "env_flag": "WIN_GO_PDH_H4", "ui_id": "win_pdh_h4", "go": "mega", "asset": "WIN",
    },
    "HL_H4": {
        "magic": 20260735, "comment": "WinHLH4", "signal": hl_h4_signal,
        "env_flag": "WIN_GO_HL_H4", "ui_id": "hl_h4", "go": "v6", "asset": "WIN",
    },
    # ── WIN v19 mega (novos — seletivos primeiro) ──
    "WIN_NR5_1115": {
        "magic": 20260746, "comment": "WinNR51115", "signal": win_nr5_1115_signal,
        "env_flag": "WIN_GO_NR5_1115", "ui_id": "win_nr5_1115", "go": "v19", "asset": "WIN",
    },
    "WIN_NR4_1115": {
        "magic": 20260747, "comment": "WinNR41115", "signal": win_nr4_1115_signal,
        "env_flag": "WIN_GO_NR4_1115", "ui_id": "win_nr4_1115", "go": "v19", "asset": "WIN",
    },
    "WIN_INS_PM": {
        "magic": 20260749, "comment": "WinInsPM", "signal": win_ins_pm_signal,
        "env_flag": "WIN_GO_INS_PM", "ui_id": "win_ins_pm", "go": "v19", "asset": "WIN",
    },
    "WIN_VOLSPIKE_AM": {
        "magic": 20260759, "comment": "WinVolAM", "signal": win_volspike_am_signal,
        "env_flag": "WIN_GO_VOLSPIKE_AM", "ui_id": "win_volspike_am", "go": "v19", "asset": "WIN",
    },
    "WIN_IB_BRK_V15": {
        "magic": 20260762, "comment": "WinIBV15", "signal": win_ib_brk_v15_signal,
        "env_flag": "WIN_GO_IB_BRK_V15", "ui_id": "win_ib_brk_v15", "go": "v19", "asset": "WIN",
    },
    "WIN_HL_MID": {
        "magic": 20260763, "comment": "WinHLMid", "signal": win_hl_mid_signal,
        "env_flag": "WIN_GO_HL_MID", "ui_id": "win_hl_mid", "go": "v19", "asset": "WIN",
    },
    # ── WDO (v7 + mega + v19: seletivos primeiro) ──
    "WDO_OUT_GAP_DAY": {
        "magic": 20260745, "comment": "WdoOutGap", "signal": wdo_out_gap_day_signal,
        "env_flag": "WIN_GO_WDO_OUT_GAP_DAY", "ui_id": "wdo_out_gap_day", "go": "v19", "asset": "WDO",
    },
    "WDO_OUT_POWER": {
        "magic": 20260748, "comment": "WdoOutPwr", "signal": wdo_out_power_signal,
        "env_flag": "WIN_GO_WDO_OUT_POWER", "ui_id": "wdo_out_power", "go": "v19", "asset": "WDO",
    },
    "WDO_IMP_1014_MT": {
        "magic": 20260758, "comment": "WdoImpMT", "signal": wdo_imp_1014_mt_signal,
        "env_flag": "WIN_GO_WDO_IMP_1014_MT", "ui_id": "wdo_imp_1014_mt", "go": "v19", "asset": "WDO",
    },
    "WDO_ENG_1014": {
        "magic": 20260760, "comment": "WdoEng1014", "signal": wdo_eng_1014_signal,
        "env_flag": "WIN_GO_WDO_ENG_1014", "ui_id": "wdo_eng_1014", "go": "v19", "asset": "WDO",
    },
    "WDO_GAPC_A60": {
        "magic": 20260761, "comment": "WdoGapc60", "signal": wdo_gapc_a60_signal,
        "env_flag": "WIN_GO_WDO_GAPC_A60", "ui_id": "wdo_gapc_a60", "go": "v19", "asset": "WDO",
    },
    "WDO_NR7_1014_H4": {
        "magic": 20260738, "comment": "WdoNR71014", "signal": nr7_1014_h4_signal,
        "env_flag": "WIN_GO_WDO_NR7_1014", "ui_id": "wdo_nr7_1014", "go": "v7", "asset": "WDO",
    },
    "WDO_PDH_H4": {
        "magic": 20260739, "comment": "WdoPDHH4", "signal": pdh_h4_signal,
        "env_flag": "WIN_GO_WDO_PDH_H4", "ui_id": "wdo_pdh_h4", "go": "v7", "asset": "WDO",
    },
    "WDO_HL_1014": {
        "magic": 20260742, "comment": "WdoHL1014", "signal": wdo_hl_1014_signal,
        "env_flag": "WIN_GO_WDO_HL_1014", "ui_id": "wdo_hl_1014", "go": "mega", "asset": "WDO",
    },
    "WDO_OUT_1015_MT": {
        "magic": 20260744, "comment": "WdoOutMT", "signal": wdo_out_1015_mt_signal,
        "env_flag": "WIN_GO_WDO_OUT_1015_MT", "ui_id": "wdo_out_1015_mt", "go": "yellow", "asset": "WDO",
    },
    "WDO_NR5_H4": {
        "magic": 20260736, "comment": "WdoNR5H4", "signal": nr5_h4_signal,
        "env_flag": "WIN_GO_WDO_NR5_H4", "ui_id": "wdo_nr5_h4", "go": "v6", "asset": "WDO",
    },
    "WDO_NR4_H4": {
        "magic": 20260737, "comment": "WdoNR4H4", "signal": nr4_h4_signal,
        "env_flag": "WIN_GO_WDO_NR4_H4", "ui_id": "wdo_nr4_h4", "go": "v7", "asset": "WDO",
    },
}

# Append discovery feature-condition GOs (WIN/WDO only; VALE3 = inventário)
for _p in win_wdo_disc_paths():
    _name = _p.get("path") or _p.get("name")
    if not _name or _name in SETUPS:
        continue
    _sym = (_p.get("symbol") or "").upper()
    _asset = "WDO" if "WDO" in _sym else "WIN"
    _magic = _DISC_MAGIC.get(_name)
    if _magic is None:
        continue
    SETUPS[_name] = {
        "magic": _magic,
        "comment": _name[:10],
        "signal": make_wingo_signal(_name),
        "env_flag": f"WIN_GO_DISC_{_name}",
        "ui_id": _name.lower(),
        "go": "disc",
        "asset": _asset,
        "tf": int(_p.get("tf") or 15),
    }

CHECK_SEC = 20
BARS = 2500  # discovery feature-cond precisa ≥600 barras fechadas
EOD_HHMM = (17, 55)
MAX_TRADES_DAY = 4

_state = {
    "enabled": os.getenv("WIN_GO_ENABLED", "1").strip() != "0",
    "day": None,
    "trades_today": {k: 0 for k in SETUPS},
    "last_bar": {},  # asset -> timestamp
}
_started = False
_DEALS_SEEN = set()


def _mt5():
    import MetaTrader5 as mt5
    if mt5.terminal_info() is None:
        kw = {}
        path = os.getenv("MT5_PATH", "")
        if path and os.path.exists(path):
            kw["path"] = path
        login = int(os.getenv("MT5_LOGIN", "0") or 0)
        pw, srv = os.getenv("MT5_PASSWORD", ""), os.getenv("MT5_SERVER", "")
        if login and pw and srv and srv.lower() not in ("metaquotes-demo", "metaquotes-demo2"):
            kw.update(login=login, password=pw, server=srv)
        mt5.initialize(**kw)
    return mt5


def _symbol_for(asset: str) -> str:
    if (asset or "WIN").upper() == "WDO":
        return os.getenv("WDO_MT5_SYMBOL", "WDOU26").strip()
    return os.getenv("WIN_MT5_SYMBOL", "WINQ26").strip()


def _notify(txt):
    try:
        from services.telegram_notifier import send_async
        send_async(txt)
    except Exception:
        pass


# Schema estável — ENTRADA e SAÍDA no mesmo CSV (evita PnL na coluna errada)
_LOG_FIELDS = ["ts", "evento", "dir", "preco", "sl", "tp", "vol", "pnl_brl", "magic", "pos_id"]


def _log(name, row):
    try:
        os.makedirs("logs", exist_ok=True)
        path = os.path.join("logs", f"win_go_{name.lower()}.csv")
        new = not os.path.exists(path)
        clean = {k: row.get(k, "") for k in _LOG_FIELDS}
        with open(path, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=_LOG_FIELDS, extrasaction="ignore")
            if new:
                w.writeheader()
            w.writerow(clean)
    except Exception as exc:
        logger.warning("win_go log: %s", exc)


def _enabled(name):
    cfg = SETUPS[name]
    return os.getenv(cfg["env_flag"], "1").strip() != "0"


def _magics_for(asset: str):
    return {int(cfg["magic"]) for cfg in SETUPS.values()
            if cfg.get("asset", "WIN") == asset}


def _positions(mt5, sym, magic):
    raw = mt5.positions_get(symbol=sym) or []
    return [p for p in raw if int(p.magic) == int(magic)]


def _asset_positions(mt5, sym, asset: str):
    """Qualquer posição WinGo neste ativo ( magics do grupo ).

    Conta XP netting: deals com magics diferentes viram 1 position_id —
    checar só o magic do setup atual permite empilhar volume (bug 27/07).
    """
    magics = _magics_for(asset)
    raw = mt5.positions_get(symbol=sym) or []
    return [p for p in raw if int(p.magic) in magics]


def _order(mt5, sym, action, volume, price, sl, tp, magic, comment, closing_ticket=None):
    info = mt5.symbol_info(sym)
    ts_ = float((info.trade_tick_size if info else 0) or (info.point if info else 0) or 5.0)
    snap = lambda x: round(round(float(x) / ts_) * ts_, 8)
    min_d = max(float(getattr(info, "trade_stops_level", 0) or 0) * float(info.point or ts_),
                2 * ts_) if info else 2 * ts_
    price = snap(price)
    buy = action == mt5.ORDER_TYPE_BUY
    if sl:
        sl = snap(sl)
        if buy and sl >= price - min_d:
            sl = snap(price - min_d - ts_)
        if (not buy) and sl <= price + min_d:
            sl = snap(price + min_d + ts_)
    if tp:
        tp = snap(tp)
        if buy and tp <= price + min_d:
            tp = snap(price + min_d + ts_)
        if (not buy) and tp >= price - min_d:
            tp = snap(price - min_d - ts_)
    req = {"action": mt5.TRADE_ACTION_DEAL, "symbol": sym, "volume": float(volume),
           "type": action, "price": float(price), "deviation": 30, "magic": int(magic),
           "comment": comment, "type_time": mt5.ORDER_TIME_GTC,
           "type_filling": mt5.ORDER_FILLING_RETURN}
    if sl:
        req["sl"] = float(sl)
    if tp:
        req["tp"] = float(tp)
    if closing_ticket:
        req["position"] = int(closing_ticket)
    for _ in range(3):
        r = mt5.order_send(req)
        if r is not None and r.retcode == mt5.TRADE_RETCODE_DONE:
            return r, None
        time.sleep(1)
    return None, f"retcode={getattr(r, 'retcode', '?')}" if r else "sem resposta"


def _reconcile(mt5):
    try:
        from datetime import timedelta
        deals = mt5.history_deals_get(datetime.now() - timedelta(days=3), datetime.now()) or []
        magics = {int(v["magic"]): k for k, v in SETUPS.items()}
        for d in deals:
            if int(d.magic) not in magics or d.entry != 1 or d.ticket in _DEALS_SEEN:
                continue
            _DEALS_SEEN.add(d.ticket)
            name = magics[int(d.magic)]
            asset = SETUPS[name].get("asset", "WIN")
            cmt = (d.comment or "").lower()
            reason = "TP" if "tp" in cmt else ("SL" if "sl" in cmt else "SAIDA")
            _log(name, {
                "ts": str(datetime.fromtimestamp(d.time)), "evento": reason,
                "dir": "", "preco": d.price, "sl": "", "tp": "",
                "vol": d.volume, "pnl_brl": d.profit,
                "magic": int(d.magic), "pos_id": int(getattr(d, "position_id", 0) or 0),
            })
            vol_note = f" · vol {d.volume:g}" if float(d.volume or 0) > 1.01 else ""
            _notify(f"{'🎯' if reason == 'TP' else '🛑' if reason == 'SL' else '✋'} "
                    f"<b>{asset} {name} — {reason}</b>\n"
                    f"Saída {d.price:.0f} · P&L R$ {d.profit:+.2f}{vol_note}")
    except Exception as exc:
        logger.debug("win_go reconcile: %s", exc)


def _eod_close_all(mt5) -> bool:
    """Fecha todas as posições WinGo. Retorna True se ainda houver algo aberto."""
    still = False
    for asset in sorted({cfg.get("asset", "WIN") for cfg in SETUPS.values()}):
        sym = _symbol_for(asset)
        for p in _asset_positions(mt5, sym, asset):
            still = True
            tick = mt5.symbol_info_tick(sym)
            if not tick:
                continue
            px = tick.bid if p.type == 0 else tick.ask
            otype = mt5.ORDER_TYPE_SELL if p.type == 0 else mt5.ORDER_TYPE_BUY
            # usa o magic da posição (netting) + comment genérico
            name = next((n for n, c in SETUPS.items()
                         if int(c["magic"]) == int(p.magic)), None)
            cfg = SETUPS.get(name) if name else {
                "magic": int(p.magic), "comment": "WinGoEOD", "asset": asset}
            r, err = _order(mt5, sym, otype, p.volume, px, None, None,
                            cfg["magic"], cfg.get("comment", "WinGoEOD"), p.ticket)
            if not err and name:
                _notify(f"🌆 <b>{asset} {name} — ZERAGEM "
                        f"{EOD_HHMM[0]:02d}:{EOD_HHMM[1]:02d}</b>\n"
                        f"Saída {px:.0f} · P&L R$ {p.profit:+.2f} · vol {p.volume:g}")
                _log(name, {
                    "ts": str(datetime.now()), "evento": "EOD_CLOSE",
                    "dir": "", "preco": px, "sl": "", "tp": "",
                    "vol": p.volume, "pnl_brl": p.profit,
                    "magic": int(p.magic), "pos_id": int(p.ticket),
                })
            elif err:
                logger.warning("WinGo EOD close falhou magic=%s: %s", p.magic, err)
    return still or bool(any(
        _asset_positions(mt5, _symbol_for(a), a)
        for a in {cfg.get("asset", "WIN") for cfg in SETUPS.values()}
    ))


def _fetch_df(mt5, sym):
    rates = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M15, 0, BARS)
    if rates is None or len(rates) < 50:
        return None
    df = pd.DataFrame(rates)
    df["dt"] = pd.to_datetime(df["time"], unit="s")
    df = df.set_index("dt").rename(columns={
        "open": "Open", "high": "High", "low": "Low", "close": "Close"})
    df["Volume"] = df["real_volume"].where(df["real_volume"] > 0, df["tick_volume"])
    return df.iloc[:-1][["Open", "High", "Low", "Close", "Volume"]]


def _loop():
    assets = sorted({cfg.get("asset", "WIN") for cfg in SETUPS.values()})
    logger.info("WinGoRuntime iniciado (%s) — %s",
                ",".join(f"{a}={_symbol_for(a)}" for a in assets),
                " · ".join(f"{k}={v['magic']}" for k, v in SETUPS.items()))
    while True:
        try:
            now = datetime.now()
            today = now.date()
            if _state["day"] != today:
                _state["day"] = today
                _state["trades_today"] = {k: 0 for k in SETUPS}
            hhmm = (now.hour, now.minute)
            past_eod = hhmm >= EOD_HHMM

            try:
                from services.engine_heartbeat import beat
                beat("win_go_setups", {
                    "motivos": {_symbol_for("WIN"): (
                        "desligado (WIN_GO_ENABLED=0)" if not _state["enabled"]
                        else "zeragem/pós-EOD" if past_eod
                        else "avaliando GO B3 na barra fechada")}})
            except Exception:
                pass

            if not _state["enabled"]:
                time.sleep(CHECK_SEC)
                continue

            mt5 = _mt5()
            if mt5.terminal_info() is None:
                time.sleep(CHECK_SEC)
                continue
            _reconcile(mt5)

            # Após EOD: só fecha até zerar (sem janela curta 17:55–18:15)
            if past_eod:
                _eod_close_all(mt5)
                time.sleep(CHECK_SEC)
                continue

            if hhmm < (10, 0):
                time.sleep(CHECK_SEC)
                continue

            vol = float(os.getenv("WIN_GO_VOLUME", "1") or 1)

            for asset in assets:
                sym = _symbol_for(asset)
                df = _fetch_df(mt5, sym)
                if df is None or df.empty:
                    continue
                last_bar = df.index[-1]
                if _state["last_bar"].get(asset) == last_bar:
                    continue
                _state["last_bar"][asset] = last_bar

                # Netting: 1 posição por ativo para TODOS os setups WinGo
                if _asset_positions(mt5, sym, asset):
                    continue

                tick = mt5.symbol_info_tick(sym)
                if not tick:
                    continue

                for name, cfg in SETUPS.items():
                    if cfg.get("asset", "WIN") != asset:
                        continue
                    if not _enabled(name):
                        continue
                    if _state["trades_today"][name] >= MAX_TRADES_DAY:
                        continue
                    sig = cfg["signal"](df)
                    if not sig:
                        continue
                    buy = sig["dir"] == "COMPRA"
                    px = tick.ask if buy else tick.bid
                    otype = mt5.ORDER_TYPE_BUY if buy else mt5.ORDER_TYPE_SELL
                    r, err = _order(mt5, sym, otype, vol, px, sig["sl"], sig["tp"],
                                    cfg["magic"], cfg["comment"])
                    if err:
                        logger.warning("WinGo %s ordem falhou: %s", name, err)
                        _notify(f"⚠️ {asset} {name}: ordem falhou ({err})")
                        continue
                    _state["trades_today"][name] += 1
                    go = cfg.get("go", "?")
                    _notify(f"{'🟢' if buy else '🔴'} <b>{asset} {name} — {sig['dir']}</b> "
                            f"(GO {go} ✅)\n━━━━━━━━━━━━━━━━━━\n"
                            f"💰 ~{px:.0f} · 🛑 SL {sig['sl']:.0f} · 🎯 TP {sig['tp']:.0f}\n"
                            f"⏱ Zeragem {EOD_HHMM[0]:02d}:{EOD_HHMM[1]:02d}")
                    _log(name, {
                        "ts": str(now), "evento": "ENTRADA", "dir": sig["dir"],
                        "preco": px, "sl": sig["sl"], "tp": sig["tp"], "vol": vol,
                        "pnl_brl": "", "magic": cfg["magic"], "pos_id": "",
                    })
                    break  # 1 entrada por barra neste ativo
        except Exception as exc:
            logger.warning("WinGoRuntime loop: %s", exc)
        time.sleep(CHECK_SEC)


def start_win_go_runtime() -> None:
    """Chamar no boot da instância B3. Idempotente."""
    global _started
    if _started:
        return
    if os.getenv("MT5_PROFILE", "b3").strip().lower() == "crypto":
        return
    _started = True
    t = threading.Thread(target=_loop, daemon=True, name="WinGoRuntime")
    t.start()
