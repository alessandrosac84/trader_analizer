"""
profit_hud.py
-------------
Trade AI Cockpit -- Painel profissional de decisao de trading (READ-ONLY)
Layout de DUAS COLUNAS: WIN (esquerda) | WDO (direita)

Execucao standalone:  python profit_hud.py

REGRAS FUNDAMENTAIS:
  - APENAS leitura do JSON. Nao executa ordens. Nao integra com broker.
  - Nunca importa nem altera Monitor MT5, Scalper ou qualquer modulo Flask.
  - Requer apenas Python built-in (tkinter). Sem dependencias externas.
"""

import json
import os
import sys
import tkinter as tk

_BASE_DIR  = os.path.dirname(os.path.abspath(__file__))
_JSON_PATH = os.path.join(_BASE_DIR, "storage", "profit", "trade_ai_profit.json")
UPDATE_MS  = 500

C = {
    "bg":        "#0a0e17", "bg2":       "#0f1520", "bg3":      "#141c2e",
    "border":    "#1e2d45", "dim":       "#4a5568", "muted":    "#718096",
    "text":      "#a0aec0", "bright":    "#e2e8f0", "white":    "#f7fafc",
    "green":     "#00e676", "green_dk":  "#00c853", "green_bg": "#0d2318",
    "yellow":    "#ffd740", "yellow_bg": "#1f1a00",
    "red":       "#ff5252", "red_bg":    "#200a0a",
    "purple":    "#bb86fc", "blue":      "#4fc3f7", "cyan":     "#00e5ff",
    "orange":    "#ff9800",
}


def _cor_acao(v):
    v = str(v).upper()
    if "COMPRA" in v or "BUY"  in v: return C["green"]
    if "VENDA"  in v or "SELL" in v: return C["red"]
    return C["yellow"]

def _cor_score(v):
    try:
        n = float(v)
        if n > 5:  return C["green"]
        if n < -5: return C["red"]
        return C["yellow"]
    except Exception: return C["muted"]

def _cor_forca(v):
    try:
        n = float(v)
        if n >= 60: return C["green"]
        if n >= 35: return C["yellow"]
        return C["red"]
    except Exception: return C["muted"]

def _cor_risco(v):
    try:
        n = float(v)
        if n <= 25: return C["green"]
        if n <= 60: return C["yellow"]
        return C["red"]
    except Exception: return C["muted"]

def _cor_pnl(v):
    try: return C["green"] if float(v) >= 0 else C["red"]
    except Exception: return C["muted"]

def _fmt_preco(v):
    if v is None: return "--"
    try: return "{:.3f}".format(float(v))
    except Exception: return str(v)


def evaluate_trade_state(data, engine):
    """READY | WAIT | NO_TRADE  --  bidirecional (COMPRA e VENDA)."""
    try:
        can_trade   = bool(engine.get("can_trade", True))
        stop_hit    = bool(engine.get("daily_stop_hit", False))
        score       = float(data.get("score", 0) or 0)
        confluences = int(data.get("confluence_count", 0) or 0)
        risk        = float(data.get("risk_level", 0) or 0)
        if stop_hit or not can_trade: return "NO_TRADE"
        if abs(score) > 5 and confluences >= 7 and risk <= 60: return "READY"
        if risk > 75: return "NO_TRADE"
        return "WAIT"
    except Exception: return "WAIT"


def generate_alerts(win, wdo, engine):
    """Retorna lista de (mensagem, cor) em ordem de prioridade."""
    alerts = []
    can_t = bool(engine.get("can_trade", True))
    stop  = bool(engine.get("daily_stop_hit", False))

    if stop: alerts.append(("STOP DO DIA ATIVO", C["red"]))
    dd = str(engine.get("drawdown_status", "")).upper()
    if "STOP" in dd or "CRITICO" in dd:
        alerts.append(("DRAWDOWN CRITICO -- NAO OPERAR", C["red"]))
    elif "ALERTA" in dd:
        alerts.append(("DRAWDOWN EM ALERTA", C["yellow"]))
    if not can_t:
        alerts.append(("ROBO BLOQUEADO -- AGUARDAR", C["red"]))

    ws = float(win.get("score", 0) or 0)
    wc = int(win.get("confluence_count", 0) or 0)
    wr = float(win.get("risk_level", 0) or 0)
    ds = float(wdo.get("score", 0) or 0)
    dc = int(wdo.get("confluence_count", 0) or 0)
    dr = float(wdo.get("risk_level", 0) or 0)

    if abs(ws) >= 7 and wc >= 8 and wr <= 40:
        alerts.append(("QUALIDADE ALTA WIN " + ("COMPRA" if ws > 0 else "VENDA"), C["green"]))
    elif abs(ws) <= 2 and wc <= 4:
        alerts.append(("EVITAR TRADES WIN -- CONDICOES FRACAS", C["red"]))
    elif wr > 70:
        alerts.append(("RISCO ELEVADO WIN", C["yellow"]))

    regime = str(win.get("market_regime", "")).upper()
    if "LOW_VOLUME" in regime or "FRACO" in regime:
        alerts.append(("BAIXO VOLUME -- CAUTELA", C["yellow"]))

    if not alerts:
        alerts.append(("SISTEMA OPERACIONAL -- MONITORANDO", C["cyan"]))
    return alerts


def _ler_json():
    try:
        if not os.path.exists(_JSON_PATH): return None
        with open(_JSON_PATH, "r", encoding="utf-8") as f: return json.load(f)
    except Exception: return None


def _hrow(parent, label, larg=9):
    """Linha label:valor para uso dentro de colunas."""
    frm = tk.Frame(parent, bg=C["bg2"])
    frm.pack(fill="x", pady=1)
    tk.Label(frm, text=label, bg=C["bg2"], fg=C["muted"],
             font=("Consolas", 7), width=larg, anchor="w").pack(side="left")
    val = tk.Label(frm, text="--", bg=C["bg2"], fg=C["text"],
                   font=("Consolas", 8, "bold"), anchor="e")
    val.pack(side="right")
    return val


class TradingCockpit:

    WIDTH  = 680
    HEIGHT = 650

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Trade AI Cockpit")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.96)
        self.root.configure(bg=C["bg"])
        self.root.resizable(False, False)

        sw = self.root.winfo_screenwidth()
        geo = "{}x{}+{}+30".format(self.WIDTH, self.HEIGHT, sw - self.WIDTH - 20)
        self.root.geometry(geo)

        self._dx = self._dy = 0
        self._alert_idx  = 0
        self._alert_tick = 0

        self._build()
        self._loop()

    # ------------------------------------------------------------------
    # BUILD
    # ------------------------------------------------------------------

    def _build(self):
        root = self.root

        # ── HEADER ──────────────────────────────────────────────────────
        hdr = tk.Frame(root, bg=C["bg3"], pady=4)
        hdr.pack(fill="x")
        hdr.bind("<ButtonPress-1>", self._drag_start)
        hdr.bind("<B1-Motion>",     self._drag_move)
        root.bind("<ButtonPress-1>", self._drag_start)
        root.bind("<B1-Motion>",     self._drag_move)

        tk.Label(hdr, text="  TRADE AI COCKPIT", bg=C["bg3"],
                 fg=C["purple"], font=("Consolas", 10, "bold")).pack(side="left")
        self._lbl_ts = tk.Label(hdr, text="", bg=C["bg3"],
                                 fg=C["dim"], font=("Consolas", 7))
        self._lbl_ts.pack(side="left", padx=4)
        btn = tk.Label(hdr, text=" X ", bg=C["bg3"], fg=C["dim"],
                       font=("Consolas", 9), cursor="hand2")
        btn.pack(side="right", padx=2)
        btn.bind("<Button-1>", lambda e: root.destroy())

        # ── ALERTAS ─────────────────────────────────────────────────────
        self._alert_outer = tk.Frame(root, bg=C["red_bg"],
                                      highlightbackground=C["red"],
                                      highlightthickness=1)
        self._alert_outer.pack(fill="x", padx=5, pady=(3, 2))
        self._lbl_alert = tk.Label(
            self._alert_outer, text="AGUARDANDO DADOS...",
            bg=C["red_bg"], fg=C["red"],
            font=("Consolas", 9, "bold"), pady=4,
            anchor="center", wraplength=self.WIDTH - 30)
        self._lbl_alert.pack(fill="x")

        # ── ENGINE / RISCO GLOBAL ────────────────────────────────────────
        eng_out = tk.Frame(root, bg=C["bg2"],
                           highlightbackground=C["border"], highlightthickness=1)
        eng_out.pack(fill="x", padx=5, pady=(0, 3))
        eh = tk.Frame(eng_out, bg=C["bg3"])
        eh.pack(fill="x")
        tk.Label(eh, text="  ENGINE  /  RISCO GLOBAL", bg=C["bg3"], fg=C["cyan"],
                 font=("Consolas", 8, "bold"), pady=3, anchor="w").pack(side="left")
        tk.Frame(eng_out, bg=C["border"], height=1).pack(fill="x")
        ei = tk.Frame(eng_out, bg=C["bg2"])
        ei.pack(fill="x", padx=6, pady=3)

        row_top = tk.Frame(ei, bg=C["bg2"])
        row_top.pack(fill="x")
        fs = tk.Frame(row_top, bg=C["bg2"])
        fs.pack(side="left", fill="x", expand=True)
        tk.Label(fs, text="STATUS", bg=C["bg2"], fg=C["muted"],
                 font=("Consolas", 7)).pack(anchor="w")
        self._eng_status = tk.Label(fs, text="--", bg=C["bg2"],
                                     fg=C["text"], font=("Consolas", 12, "bold"))
        self._eng_status.pack(anchor="w")

        fp = tk.Frame(row_top, bg=C["bg2"])
        fp.pack(side="right")
        tk.Label(fp, text="P&L DIA", bg=C["bg2"], fg=C["muted"],
                 font=("Consolas", 7), anchor="e").pack(anchor="e")
        self._eng_pnl = tk.Label(fp, text="R$ --", bg=C["bg2"],
                                  fg=C["text"], font=("Consolas", 12, "bold"), anchor="e")
        self._eng_pnl.pack(anchor="e")

        tk.Frame(ei, bg=C["border"], height=1).pack(fill="x", pady=2)

        row_mid = tk.Frame(ei, bg=C["bg2"])
        row_mid.pack(fill="x")
        fl = tk.Frame(row_mid, bg=C["bg2"])
        fl.pack(side="left", fill="x", expand=True)
        tk.Label(fl, text="DRAWDOWN", bg=C["bg2"], fg=C["muted"],
                 font=("Consolas", 7), width=12, anchor="w").pack(side="left")
        self._eng_dd = tk.Label(fl, text="--", bg=C["bg2"],
                                 fg=C["text"], font=("Consolas", 8, "bold"))
        self._eng_dd.pack(side="left", padx=4)

        fr = tk.Frame(row_mid, bg=C["bg2"])
        fr.pack(side="right")
        tk.Label(fr, text="STOP DO DIA", bg=C["bg2"], fg=C["muted"],
                 font=("Consolas", 7), anchor="e").pack(side="left")
        self._eng_stop = tk.Label(fr, text="--", bg=C["bg2"],
                                   fg=C["text"], font=("Consolas", 8, "bold"))
        self._eng_stop.pack(side="left", padx=6)

        # ── DUAS COLUNAS ─────────────────────────────────────────────────
        two = tk.Frame(root, bg=C["bg"])
        two.pack(fill="x", padx=4, pady=(0, 2))

        COL_W = (self.WIDTH - 20) // 2

        left = tk.Frame(two, bg=C["bg"])
        left.pack(side="left", fill="both", expand=True, padx=(0, 2))

        sep = tk.Frame(two, bg=C["border"], width=1)
        sep.pack(side="left", fill="y")

        right = tk.Frame(two, bg=C["bg"])
        right.pack(side="left", fill="both", expand=True, padx=(2, 0))

        self._build_col(left,  "WIN", C["green"])
        self._build_col(right, "WDO", C["blue"])

        # ── TRADES DO SISTEMA ────────────────────────────────────────────
        tr_out = tk.Frame(root, bg=C["bg2"],
                          highlightbackground=C["border"], highlightthickness=1)
        tr_out.pack(fill="x", padx=5, pady=(2, 2))
        th = tk.Frame(tr_out, bg=C["bg3"])
        th.pack(fill="x")
        tk.Label(th, text="  TRADES DO SISTEMA  (ultimos 5)", bg=C["bg3"],
                 fg=C["purple"], font=("Consolas", 8, "bold"), pady=3,
                 anchor="w").pack(side="left")
        tk.Frame(tr_out, bg=C["border"], height=1).pack(fill="x")
        tr_in = tk.Frame(tr_out, bg=C["bg2"])
        tr_in.pack(fill="x", padx=6, pady=3)

        self._trade_rows = []
        for _ in range(5):
            frm = tk.Frame(tr_in, bg=C["bg2"])
            frm.pack(fill="x", pady=1)
            ls = tk.Label(frm, text="", bg=C["bg2"], fg=C["muted"],
                          font=("Consolas", 7), width=4, anchor="w")
            ls.pack(side="left")
            ld = tk.Label(frm, text="", bg=C["bg2"], fg=C["text"],
                          font=("Consolas", 7, "bold"), width=6, anchor="w")
            ld.pack(side="left")
            le = tk.Label(frm, text="", bg=C["bg2"], fg=C["muted"],
                          font=("Consolas", 7), width=8, anchor="w")
            le.pack(side="left")
            lr = tk.Label(frm, text="", bg=C["bg2"], fg=C["blue"],
                          font=("Consolas", 7), width=8, anchor="w")
            lr.pack(side="left")
            lres = tk.Label(frm, text="", bg=C["bg2"], fg=C["muted"],
                            font=("Consolas", 7, "bold"), anchor="e")
            lres.pack(side="right")
            self._trade_rows.append((ls, ld, le, lr, lres))

        # ── FOOTER ───────────────────────────────────────────────────────
        tk.Label(root, text="READ-ONLY  |  NO ORDERS  |  APENAS LEITURA",
                 bg=C["bg"], fg=C["dim"],
                 font=("Consolas", 7), pady=3).pack(fill="x", anchor="center")

    # ------------------------------------------------------------------
    # BUILD COLUNA (WIN ou WDO)
    # ------------------------------------------------------------------

    def _build_col(self, parent, instr, hdr_cor):
        """Constroi uma coluna completa para WIN ou WDO."""
        p = instr.lower()   # prefixo: "win" ou "wdo"

        # Cabecalho da coluna
        ch = tk.Frame(parent, bg=C["bg3"],
                      highlightbackground=hdr_cor, highlightthickness=1)
        ch.pack(fill="x", pady=(0, 2))
        tk.Label(ch, text="  " + instr, bg=C["bg3"], fg=hdr_cor,
                 font=("Consolas", 9, "bold"), pady=3).pack(side="left")

        # Decision badge
        dec_out = tk.Frame(parent, bg=C["bg2"],
                           highlightbackground=C["border"], highlightthickness=1)
        dec_out.pack(fill="x", pady=(0, 2))
        dec_in = tk.Frame(dec_out, bg=C["bg2"])
        dec_in.pack(fill="x", padx=4, pady=3)
        badge = tk.Label(dec_in, text=" WAIT ", bg=C["yellow_bg"],
                         fg=C["yellow"], font=("Consolas", 9, "bold"),
                         padx=4, pady=2)
        badge.pack(fill="x")
        detail = tk.Label(dec_in, text="", bg=C["bg2"], fg=C["muted"],
                          font=("Consolas", 7), anchor="w",
                          wraplength=(self.WIDTH // 2) - 20)
        detail.pack(fill="x", pady=(1, 0))
        setattr(self, "_" + p + "_badge",  badge)
        setattr(self, "_" + p + "_detail", detail)

        # Bloco oportunidade / dados
        opp_out = tk.Frame(parent, bg=C["bg2"],
                           highlightbackground=C["border"], highlightthickness=1)
        opp_out.pack(fill="x", pady=(0, 2))

        opp_hdr_frm = tk.Frame(opp_out, bg=C["bg3"])
        opp_hdr_frm.pack(fill="x")
        opp_title = tk.Label(opp_hdr_frm, text="  " + instr + " -- MONITORANDO",
                             bg=C["bg3"], fg=C["dim"],
                             font=("Consolas", 7, "bold"), pady=2, anchor="w")
        opp_title.pack(side="left")
        tk.Frame(opp_out, bg=C["border"], height=1).pack(fill="x")
        setattr(self, "_" + p + "_opp_outer", opp_out)
        setattr(self, "_" + p + "_opp_hdr",   opp_hdr_frm)
        setattr(self, "_" + p + "_opp_title", opp_title)

        opp_body = tk.Frame(opp_out, bg=C["bg2"])
        opp_body.pack(fill="x", padx=4, pady=2)
        setattr(self, "_" + p + "_opp_body", opp_body)

        # Acao grande + score
        row_as = tk.Frame(opp_body, bg=C["bg2"])
        row_as.pack(fill="x")
        lbl_acao = tk.Label(row_as, text="--", bg=C["bg2"],
                            fg=C["text"], font=("Consolas", 13, "bold"))
        lbl_acao.pack(side="left")
        lbl_score = tk.Label(row_as, text="", bg=C["bg2"],
                             fg=C["muted"], font=("Consolas", 11, "bold"), anchor="e")
        lbl_score.pack(side="right")
        setattr(self, "_" + p + "_acao",  lbl_acao)
        setattr(self, "_" + p + "_score", lbl_score)

        tk.Frame(opp_body, bg=C["border"], height=1).pack(fill="x", pady=2)

        # Linhas de dados: label esquerda, valor direita
        def crow(lbl_txt):
            frm = tk.Frame(opp_body, bg=C["bg2"])
            frm.pack(fill="x", pady=1)
            tk.Label(frm, text=lbl_txt, bg=C["bg2"], fg=C["muted"],
                     font=("Consolas", 7), width=7, anchor="w").pack(side="left")
            val = tk.Label(frm, text="--", bg=C["bg2"],
                           fg=C["dim"], font=("Consolas", 9, "bold"), anchor="w")
            val.pack(side="left", padx=2)
            side = tk.Label(frm, text="", bg=C["bg2"],
                            fg=C["muted"], font=("Consolas", 7), anchor="e")
            side.pack(side="right")
            return val, side, frm

        e_val, e_side, e_frm = crow("ENT")
        s_val, s_side, s_frm = crow("STOP")
        t1_val, t1_side, t1_frm = crow("TP1")
        t2_val, t2_side, t2_frm = crow("TP2")
        setattr(self, "_" + p + "_ent",      e_val)
        setattr(self, "_" + p + "_ent_side", e_side)
        setattr(self, "_" + p + "_sl",       s_val)
        setattr(self, "_" + p + "_sl_side",  s_side)
        setattr(self, "_" + p + "_tp1",      t1_val)
        setattr(self, "_" + p + "_tp1_side", t1_side)
        setattr(self, "_" + p + "_tp2",      t2_val)

        tk.Frame(opp_body, bg=C["border"], height=1).pack(fill="x", pady=2)

        # Linhas de analise (diferentes para WIN e WDO)
        if instr == "WIN":
            self._win_forca  = _hrow(opp_body, "STR %",  7)
            self._win_risco  = _hrow(opp_body, "RISK %", 7)
            self._win_regime = _hrow(opp_body, "REGIME", 7)
            self._win_conf   = _hrow(opp_body, "CONF",   7)
            self._win_pos    = _hrow(opp_body, "POS",    7)
            self._win_sinal  = _hrow(opp_body, "SINAL",  7)
        else:
            self._wdo_interp = _hrow(opp_body, "INTERP", 7)
            self._wdo_rsi    = _hrow(opp_body, "RSI",    7)
            self._wdo_adx    = _hrow(opp_body, "ADX",    7)
            self._wdo_trend  = _hrow(opp_body, "TREND",  7)
            self._wdo_vwap   = _hrow(opp_body, "VWAP",   7)
            self._wdo_conf   = _hrow(opp_body, "CONF",   7)

        # Separador + linha de acao piscante
        opp_sep = tk.Frame(opp_out, bg=C["border"], height=1)
        opp_sep.pack(fill="x")
        opp_act = tk.Label(opp_out, text="",
                           bg=C["bg2"], fg=C["bg2"],
                           font=("Consolas", 8, "bold"), pady=3, anchor="center")
        opp_act.pack(fill="x")
        setattr(self, "_" + p + "_opp_sep", opp_sep)
        setattr(self, "_" + p + "_opp_act", opp_act)


    # ------------------------------------------------------------------
    # LOOP
    # ------------------------------------------------------------------

    def _loop(self):
        data = _ler_json()
        if data: self._render(data)
        else:    self._render_sem_dados()
        self.root.after(UPDATE_MS, self._loop)

    # ------------------------------------------------------------------
    # RENDER
    # ------------------------------------------------------------------

    def _render(self, data):
        win = data.get("win", {})
        wdo = data.get("wdo", {})
        eng = data.get("engine", {})

        ts = data.get("generated_at", "")
        self._lbl_ts.config(text=ts[11:19] if len(ts) >= 19 else ts)
        self._generated_at = ts

        # ENGINE
        can_trade = eng.get("can_trade", True)
        stop_hit  = eng.get("daily_stop_hit", False)
        pnl       = float(eng.get("current_pnl", 0) or 0)
        dd_status = str(eng.get("drawdown_status", "NORMAL")).upper()

        s_txt = "BLOQUEADO" if (stop_hit or not can_trade) else "LIBERADO"
        s_cor = C["red"]   if (stop_hit or not can_trade) else C["green"]
        self._eng_status.config(text=s_txt, fg=s_cor)
        self._eng_pnl.config(text="R$ {:+.0f}".format(pnl), fg=_cor_pnl(pnl))
        dd_cor = (C["red"] if ("STOP" in dd_status or "CRITICO" in dd_status)
                  else C["yellow"] if "ALERTA" in dd_status else C["green"])
        self._eng_dd.config(text=dd_status, fg=dd_cor)
        self._eng_stop.config(text="ATIVO" if stop_hit else "INATIVO",
                               fg=C["red"] if stop_hit else C["green"])

        # Estados WIN e WDO
        win_state = evaluate_trade_state(win, eng)
        wdo_state = evaluate_trade_state(wdo, eng)

        self._render_col("WIN", win, eng, win_state)
        self._render_col("WDO", wdo, eng, wdo_state)

        # ALERTAS
        alerts = generate_alerts(win, wdo, eng)
        self._alert_tick += 1
        if self._alert_tick >= 6:
            self._alert_tick = 0
            self._alert_idx  = (self._alert_idx + 1) % len(alerts)
        if self._alert_idx >= len(alerts):
            self._alert_idx = 0
        msg, cor = alerts[self._alert_idx]
        if cor == C["red"]:
            abg, aborder = C["red_bg"],    C["red"]
        elif cor == C["yellow"]:
            abg, aborder = C["yellow_bg"], C["yellow"]
        elif cor == C["green"]:
            abg, aborder = C["green_bg"],  C["green_dk"]
        else:
            abg, aborder = C["bg3"],       C["border"]
        self._alert_outer.config(bg=abg, highlightbackground=aborder)
        self._lbl_alert.config(text="  " + msg + "  ", bg=abg, fg=cor)

        # TRADES
        trades = data.get("trades", [])
        for i, (ls, ld, le, lr, lres) in enumerate(self._trade_rows):
            if i < len(trades):
                t = trades[i]
                sym  = str(t.get("instrument", "--"))
                acao = str(t.get("acao", "--"))[:6]
                ent  = t.get("entry_price")
                rr   = t.get("rr_ratio")
                isop = t.get("is_open", False)
                pnlt = t.get("pnl_brl")
                rsn  = str(t.get("close_reason", "") or "")
                etxt = "{:.0f}".format(float(ent)) if ent else "--"
                rtxt = "R/R 1:{:.1f}".format(rr)   if rr  else ""
                if isop:
                    res_txt, res_cor = "ABERTO", C["cyan"]
                elif pnlt is not None:
                    res_txt = "R${:+.0f}".format(float(pnlt))
                    res_cor = C["green"] if float(pnlt) >= 0 else C["red"]
                else:
                    res_txt, res_cor = rsn[:8] or "--", C["muted"]
                ls.config(text=sym,    fg=C["purple"])
                ld.config(text=acao,   fg=_cor_acao(acao))
                le.config(text=etxt,   fg=C["muted"])
                lr.config(text=rtxt,   fg=C["blue"])
                lres.config(text=res_txt, fg=res_cor)
            else:
                for lbl in (ls, ld, le, lr, lres):
                    lbl.config(text="", fg=C["dim"])

    # ------------------------------------------------------------------
    # RENDER COLUNA
    # ------------------------------------------------------------------

    def _render_col(self, instr, data, eng, state):
        p = instr.lower()

        # Acao + Score
        acao  = str(data.get("action", "--")).upper()
        score = float(data.get("score", 0) or 0)
        getattr(self, "_" + p + "_acao").config(text=acao,  fg=_cor_acao(acao))
        getattr(self, "_" + p + "_score").config(
            text="{:+.0f}".format(score), fg=_cor_score(score))

        # Badge decisao
        opp_dir   = acao if acao not in ("--", "NEUTRO") else ("COMPRA" if score > 0 else "VENDA")
        is_compra = ("COMPRA" in opp_dir or score > 0)
        conf      = int(data.get("confluence_count", 0) or 0)
        risk      = float(data.get("risk_level", 0) or 0)

        if state == "READY":
            if is_compra: b_fg, b_bg = C["green"],  C["green_bg"]
            else:         b_fg, b_bg = C["orange"], C["red_bg"]
            b_txt = "READY  " + opp_dir
        elif state == "NO_TRADE":
            b_fg, b_bg, b_txt = C["red"], C["red_bg"], "NO TRADE"
        else:
            b_fg, b_bg, b_txt = C["yellow"], C["yellow_bg"], "WAIT"

        getattr(self, "_" + p + "_badge").config(
            text="  " + b_txt + "  ", fg=b_fg, bg=b_bg)

        det = ""
        if state == "READY":
            det = "|{}|>5  Conf {}  Risk {}%".format(
                int(abs(score)), conf, int(risk))
        elif state == "WAIT":
            det = "Score |{}|  Conf {}  Risk {}%".format(
                int(abs(score)), conf, int(risk))
        getattr(self, "_" + p + "_detail").config(text="  " + det)

        # Niveis MT5
        entry = data.get("entry_price")
        sl    = data.get("sl")
        tp1   = data.get("tp1")
        tp2   = data.get("tp2")

        # R/R e stop pts
        rr_v = spts_v = 0.0
        try:
            ef = float(entry) if entry else None
            sf = float(sl)    if sl    else None
            t1 = float(tp1)   if tp1   else None
            if ef and sf and t1:
                spts_v = abs(ef - sf)
                rr_v   = abs(t1 - ef) / spts_v if spts_v > 0 else 0
        except Exception:
            pass

        rr_cor = C["green"] if rr_v >= 2 else C["yellow"] if rr_v >= 1 else C["red"]

        getattr(self, "_" + p + "_ent").config(
            text=_fmt_preco(entry) if entry else "--", fg=C["cyan"])
        getattr(self, "_" + p + "_ent_side").config(
            text="{:.0f}pts".format(spts_v) if spts_v else "", fg=C["muted"])
        getattr(self, "_" + p + "_sl").config(
            text=_fmt_preco(sl) if sl else "--", fg=C["red"])
        getattr(self, "_" + p + "_sl_side").config(
            text="R/R 1:{:.1f}".format(rr_v) if rr_v else "", fg=rr_cor)
        getattr(self, "_" + p + "_tp1").config(
            text=_fmt_preco(tp1) if tp1 else "--", fg=C["green"])
        getattr(self, "_" + p + "_tp1_side").config(
            text=str(conf) + " conf" if conf else "", fg=C["blue"])
        getattr(self, "_" + p + "_tp2").config(
            text=_fmt_preco(tp2) if tp2 else "--", fg=C["green_dk"])

        # Linhas de analise especificas
        if instr == "WIN":
            forca = float(data.get("signal_strength", 0) or 0)
            pos   = data.get("position_open", False)
            pdir  = str(data.get("position_dir", "") or "").upper()
            ppnl  = float(data.get("position_pnl", 0) or 0)
            regime = str(data.get("market_regime", "--")).upper()
            sinal  = str(data.get("last_signal_at", "") or "")

            self._win_forca.config(text="{:.0f}%".format(forca),
                                    fg=_cor_forca(forca))
            self._win_risco.config(text="{:.0f}%".format(risk),
                                    fg=_cor_risco(risk))
            self._win_regime.config(text=regime,  fg=C["text"])
            w_conf_cor = C["blue"] if conf >= 7 else C["yellow"] if conf >= 4 else C["red"]
            self._win_conf.config(text=str(conf), fg=w_conf_cor)
            if pos:
                self._win_pos.config(
                    text=pdir + " R${:+.0f}".format(ppnl), fg=_cor_pnl(ppnl))
            else:
                self._win_pos.config(text="SEM POSICAO", fg=C["dim"])
            # Usa generated_at (tempo real do Monitor MT5) em vez de last_signal_at (DB potencialmente antigo)
            gen_ts = getattr(self, "_generated_at", "")
            sinal_txt = gen_ts[11:19] if len(gen_ts) >= 19 else (gen_ts[11:16] if len(gen_ts) >= 16 else "--")
            self._win_sinal.config(text=sinal_txt, fg=C["cyan"])

        else:  # WDO
            rsi   = data.get("rsi")
            adx   = data.get("adx")
            trend = str(data.get("trend", "--")).upper()
            vwap  = data.get("vwap_bull")

            if score > 5:   interp, icor = "FORTE",  C["green"]
            elif score < -5: interp, icor = "FRACO",  C["red"]
            else:            interp, icor = "NEUTRO", C["yellow"]
            self._wdo_interp.config(text=interp, fg=icor)

            rsi_txt = "{:.1f}".format(rsi) if rsi is not None else "--"
            rsi_cor = (C["red"]  if (rsi and rsi > 70) else
                       C["green"] if (rsi and rsi < 30) else C["text"])
            self._wdo_rsi.config(text=rsi_txt, fg=rsi_cor)

            adx_txt = "{:.1f}".format(adx) if adx is not None else "--"
            self._wdo_adx.config(text=adx_txt,
                                  fg=C["green"] if (adx and adx > 25) else C["muted"])
            self._wdo_trend.config(text=trend, fg=C["text"])

            if vwap is True:
                self._wdo_vwap.config(text="BULL", fg=C["green"])
            elif vwap is False:
                self._wdo_vwap.config(text="BEAR", fg=C["red"])
            else:
                self._wdo_vwap.config(text="--", fg=C["muted"])

            d_cc = C["blue"] if conf >= 7 else C["yellow"] if conf >= 4 else C["red"]
            self._wdo_conf.config(text=str(conf), fg=d_cc)

        # Bloco OPP -- visual e pulso
        opp_out   = getattr(self, "_" + p + "_opp_outer")
        opp_hdr   = getattr(self, "_" + p + "_opp_hdr")
        opp_title = getattr(self, "_" + p + "_opp_title")
        opp_body  = getattr(self, "_" + p + "_opp_body")
        opp_sep = getattr(self, "_" + p + "_opp_sep")
        opp_act = getattr(self, "_" + p + "_opp_act")

        if state == "READY":
            opp_cor = C["green"]    if is_compra else C["orange"]
            opp_bg  = C["green_bg"] if is_compra else C["red_bg"]
            opp_hbg = "#0d1f0d"     if is_compra else "#1a0a00"
            opp_out.config(bg=opp_bg, highlightbackground=opp_cor, highlightthickness=2)
            opp_hdr.config(bg=opp_hbg)
            opp_title.config(text="  * " + instr + "  --  " + opp_dir + "  *",
                             bg=opp_hbg, fg=opp_cor)
            opp_body.config(bg=opp_bg)
            for frm in opp_body.winfo_children():
                frm.config(bg=opp_bg)
                for child in frm.winfo_children():
                    child.config(bg=opp_bg)
            opp_sep.config(bg=opp_cor)
            opp_act.config(
                text="  EXECUTE MANUALMENTE SE DESEJAR  ",
                bg=opp_hbg, fg=opp_cor)
        else:
            w_cor = C["yellow"] if state == "WAIT" else C["red"]
            opp_out.config(bg=C["bg2"], highlightbackground=C["border"], highlightthickness=1)
            opp_hdr.config(bg=C["bg3"])
            opp_title.config(text="  " + instr + "  --  " +
                             ("WAIT" if state == "WAIT" else "NO TRADE"),
                             bg=C["bg3"], fg=w_cor)
            opp_body.config(bg=C["bg2"])
            for frm in opp_body.winfo_children():
                frm.config(bg=C["bg2"])
                for child in frm.winfo_children():
                    child.config(bg=C["bg2"])
            opp_sep.config(bg=C["border"])
            opp_act.config(text="", bg=C["bg2"], fg=C["bg2"])

    def _render_sem_dados(self):
        self._lbl_ts.config(text="sem dados")
        self._alert_outer.config(bg=C["red_bg"], highlightbackground=C["red"])
        self._lbl_alert.config(
            text="JSON NAO ENCONTRADO -- Bridge ativa?",
            bg=C["red_bg"], fg=C["red"])
        self._eng_status.config(text="OFFLINE", fg=C["dim"])

    def _drag_start(self, event):
        self._dx = event.x_root - self.root.winfo_x()
        self._dy = event.y_root - self.root.winfo_y()

    def _drag_move(self, event):
        x = event.x_root - self._dx
        y = event.y_root - self._dy
        self.root.geometry("+{}+{}".format(x, y))

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    if _BASE_DIR not in sys.path:
        sys.path.insert(0, _BASE_DIR)
    print("=" * 52)
    print("  Trade AI Cockpit -- READ-ONLY  (2 colunas)")
    print("  Sem execucao de ordens. Apenas leitura do JSON.")
    print("  JSON: " + _JSON_PATH)
    print("  Feche a janela ou Ctrl+C para encerrar.")
    print("=" * 52)
    app = TradingCockpit()
    app.run()
