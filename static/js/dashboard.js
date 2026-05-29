(function () {
  const cfg = window.__TRADE_AI__ || {};

  function todayIsoUtc() {
    return new Date().toISOString().slice(0, 10);
  }

  let statsPeriod = "all";
  let statsRefDate = todayIsoUtc();

  function parseJson(str) {
    if (str == null) return null;
    if (typeof str === "object") return str;
    try {
      return JSON.parse(str);
    } catch (e) {
      return null;
    }
  }

  function ativoLabelFromRow(row) {
    if (row.ativo_label != null && String(row.ativo_label).trim() !== "") {
      return String(row.ativo_label).trim();
    }
    var T = parseJson(row.trader_json);
    var s = T && T.ativo != null ? String(T.ativo).trim() : "";
    return s || "—";
  }

  function ativoHintFromRow(row) {
    if (row.ativo_hint != null && String(row.ativo_hint).trim() !== "") {
      return String(row.ativo_hint).trim();
    }
    var T = parseJson(row.trader_json);
    return T && T.ativo_como_detectado ? String(T.ativo_como_detectado).trim() : "";
  }

  function esc(s) {
    if (s == null || s === "") return "—";
    const d = document.createElement("div");
    d.textContent = String(s);
    return d.innerHTML;
  }

  /**
   * Completa preços tipo 202.35 → 202.350 para bater com a escala do gráfico.
   * Evita alterar razões após ":" (ex.: 1:2.5) via lookbehind.
   */
  function padPrecosTresDecimais(s) {
    if (s == null || s === "") return s;
    return String(s).replace(/(?<![:\/])(\d+)\.(\d{1,2})(?!\d)/g, function (_m, intPart, dec) {
      if (dec.length >= 3) return intPart + "." + dec;
      return intPart + "." + dec.padEnd(3, "0");
    });
  }

  function prettifyJson(str) {
    const o = parseJson(str);
    if (o) return JSON.stringify(o, null, 2);
    return String(str || "");
  }

  function execPatchUrl(id) {
    const base = (cfg.apiAnalysisPrefix || "/api/analysis").replace(/\/$/, "");
    return base + "/" + id + "/exec";
  }

  /** Número pt-BR / US: vírgula decimal, milhar com ponto. */
  function parseBrazilianNumber(raw) {
    if (raw == null) return null;
    var s = String(raw).trim();
    if (s === "") return null;
    var neg = false;
    if (/^-/.test(s)) {
      neg = true;
      s = s.replace(/^-/, "").trim();
    }
    if (s.indexOf(",") >= 0 && s.indexOf(".") >= 0) {
      if (s.lastIndexOf(",") > s.lastIndexOf(".")) {
        s = s.replace(/\./g, "").replace(",", ".");
      } else {
        s = s.replace(/,/g, "");
      }
    } else if (s.indexOf(",") >= 0) {
      s = s.replace(",", ".");
    }
    var n = parseFloat(s);
    if (isNaN(n)) return null;
    return neg ? -n : n;
  }

  function assetClassFromTicker(sym) {
    var s = String(sym || "").trim().toUpperCase();
    if (s.indexOf("WIN") === 0) return "WIN";
    if (s.indexOf("WDO") === 0) return "WDO";
    return "OTHER";
  }

  /**
   * Pontos de índice WIN a partir de dois preços no eixo (ex.: 201,635 vs 201,590 → 45).
   * Só aplica fator ×1000 quando a diferença é pequena (< 1), típico de cotação xxx.xxx (0,045 ≈ 45 pts).
   * Se a diferença for grande (ex.: erro digitando 21,590 em vez de 201,590), não multiplica — evita centenas de mil pontos fantasmas.
   */
  function winIndexPointsFromPrices(e, x) {
    var abs = Math.abs(e - x);
    var maxP = Math.max(Math.abs(e), Math.abs(x));
    if (maxP >= 50000) return Math.round(abs);
    if (maxP < 10000 && abs < 1) return Math.round(abs * 1000);
    return Math.round(abs);
  }

  /** Lucro/prejuízo em R$ para mini WIN (R$ 0,20 por ponto) e mini WDO (≈ R$ 50 por ponto de cotação). */
  function computeExecPnlBrl(entryStr, exitStr, isBuy, ativo) {
    var e = parseBrazilianNumber(entryStr);
    var x = parseBrazilianNumber(exitStr);
    if (e == null || x == null) return null;
    var ac = assetClassFromTicker(ativo);
    var signed = isBuy ? x - e : e - x;
    if (ac === "WIN") {
      var pts = winIndexPointsFromPrices(e, x);
      var absPx = Math.abs(e - x);
      var maxP = Math.max(Math.abs(e), Math.abs(x));
      var brl = (signed >= 0 ? 1 : -1) * pts * 0.2;
      var sgn = signed >= 0 ? "+" : "−";
      var hintExtra =
        absPx >= 1 && maxP < 500 && maxP > 50
          ? " — Confira entradas/saídas (diferença grande para WIN nesta faixa)."
          : "";
      return {
        brl: brl,
        hint:
          "WIN: " +
          pts +
          " pts × R$ 0,20 = " +
          sgn +
          "R$ " +
          fmtPnlDisplay(Math.abs(brl)) +
          hintExtra,
      };
    }
    if (ac === "WDO") {
      var ptsW = winIndexPointsFromPrices(e, x);
      var brlW = (signed >= 0 ? 1 : -1) * ptsW * 50;
      var sgnW = signed >= 0 ? "+" : "−";
      return {
        brl: brlW,
        hint:
          "WDO (aprox.): " +
          ptsW +
          " pts × R$ 50,00 = " +
          sgnW +
          "R$ " +
          fmtPnlDisplay(Math.abs(brlW)),
      };
    }
    return {
      brl: signed,
      hint:
        "Δ cotação: " +
        padPrecosTresDecimais(signed.toFixed(3)) +
        " (R$ por unidade — ajuste pelo lote se ação/futuro)",
    };
  }

  /**
   * Quando o trader não disse COMPRA/VENDA, infere pelo movimento: saída > entrada → lucro de long;
   * saída < entrada → lucro de short (venda). Empate usa COMPRA. Assim o P/L em R$ ainda é sugerido.
   */
  function resolveJournalDirection(ctx, entryStr, exitStr) {
    ctx = ctx || {};
    if (ctx.isBuy) return { isBuy: true, inferred: false };
    if (ctx.isSell) return { isBuy: false, inferred: false };
    var e = parseBrazilianNumber(entryStr);
    var x = parseBrazilianNumber(exitStr);
    if (e == null || x == null) return null;
    if (x > e) return { isBuy: true, inferred: true };
    if (e > x) return { isBuy: false, inferred: true };
    return { isBuy: true, inferred: true };
  }

  function extractEntradaPrice(T) {
    if (!T || T.entrada == null) return null;
    var s = String(T.entrada);
    var m = s.match(/\b(\d{1,6}[.,]\d{1,4})\b/);
    if (m) return parseBrazilianNumber(m[1]);
    m = s.match(/\b(\d{5,9})\b/);
    if (m) return parseBrazilianNumber(m[1]);
    return null;
  }

  function parsePontosAlvo(dist) {
    if (dist == null) return null;
    var s = String(dist).toLowerCase();
    var m = s.match(/(\d+(?:[.,]\d+)?)\s*pts?/);
    if (m) return parseBrazilianNumber(m[1].replace(",", "."));
    return null;
  }

  function looksLikePriceToken(dist) {
    return /\d{2,4}[.,]\d{3}/.test(String(dist || ""));
  }

  /** Preço alvo a partir da entrada + distância em pontos (WIN/WDO/outros). */
  function targetExitPrice(base, pts, ac, isBuy) {
    if (base == null || pts == null || pts <= 0) return null;
    var delta;
    if (ac === "WIN") delta = pts * 0.001;
    else if (ac === "WDO") delta = pts * 0.0005;
    else delta = pts * 0.01;
    var p = isBuy ? base + delta : base - delta;
    return p;
  }

  function formatPrice3Num(n) {
    if (n == null || isNaN(n)) return "—";
    return padPrecosTresDecimais(Number(n).toFixed(3));
  }

  function buildAlvoExitLine(T, a, isBuy, isSell) {
    if (!isBuy && !isSell) return "";
    var base = extractEntradaPrice(T);
    if (base == null) return "";
    var dist = a && a.distancia != null ? a.distancia : "";
    if (looksLikePriceToken(dist)) {
      return "Saída em " + padPrecosTresDecimais(String(dist).trim());
    }
    var pts = parsePontosAlvo(dist);
    if (pts == null || pts <= 0) return "";
    var ac = assetClassFromTicker(T && T.ativo ? T.ativo : "");
    var px = targetExitPrice(base, pts, ac, isBuy);
    if (px == null) return "";
    return "Saída em " + formatPrice3Num(px);
  }

  var journalPnlBound = { main: null, modal: null };

  function attachJournalPnlAuto(prefix, ctx) {
    ctx = ctx || {};
    var scope = prefix.indexOf("modal") >= 0 ? "modal" : "main";
    var entryEl = document.getElementById(prefix + "trade-exec-entry");
    var exitEl = document.getElementById(prefix + "trade-exec-exit");
    var pnlEl = document.getElementById(prefix + "trade-exec-pnl");
    var hintEl = document.getElementById(prefix + "trade-exec-pnl-hint");
    if (!entryEl || !exitEl || !pnlEl) return;

    var prev = journalPnlBound[scope];
    if (prev && prev.recalc) {
      ["input", "change", "blur"].forEach(function (ev) {
        prev.entry.removeEventListener(ev, prev.recalc);
        prev.exit.removeEventListener(ev, prev.recalc);
      });
    }

    function recalc() {
      var en = entryEl.value;
      var ex = exitEl.value;
      if (!String(en).trim() || !String(ex).trim()) {
        if (hintEl) hintEl.textContent = "";
        return;
      }
      var dir = resolveJournalDirection(ctx, en, ex);
      if (!dir) {
        if (hintEl) hintEl.textContent = "";
        return;
      }
      var out = computeExecPnlBrl(en, ex, dir.isBuy, ctx.ativo);
      if (!out) {
        if (hintEl) hintEl.textContent = "";
        return;
      }
      pnlEl.value = fmtPnlDisplay(out.brl);
      if (hintEl) {
        var base = out.hint || "";
        hintEl.textContent = dir.inferred
          ? base +
            " — Direção inferida pelo preço (saída vs entrada); ajuste se operou o lado oposto."
          : base;
      }
    }

    ["input", "change", "blur"].forEach(function (ev) {
      entryEl.addEventListener(ev, recalc);
      exitEl.addEventListener(ev, recalc);
    });
    journalPnlBound[scope] = { entry: entryEl, exit: exitEl, recalc: recalc };
    recalc();
  }

  function fillDecisionWhy(getEl, T, R, V, resumo) {
    var whyWrap = getEl("trade-decision-why");
    var whyText = getEl("trade-decision-why-text");
    if (!whyWrap || !whyText) return;

    var rawDec = resumo.decisao != null ? resumo.decisao : R && R.decisao ? R.decisao : "";
    var decisaoNorm = String(rawDec)
      .toUpperCase()
      .replace(/\s/g, "_");
    var naoOperar =
      decisaoNorm.indexOf("NAO_OPERAR") >= 0 || decisaoNorm.indexOf("NÃO_OPERAR") >= 0;
    var permitir = resumo.permitir_trade;
    var mostrar = naoOperar || permitir === false;

    if (!mostrar) {
      whyWrap.hidden = true;
      whyWrap.classList.remove("trade-decision-why--conflict");
      return;
    }

    var acaoRaw = T && T.acao ? String(T.acao).toUpperCase() : "";
    var sinalCV = acaoRaw.indexOf("COMPRA") >= 0 || acaoRaw.indexOf("VENDA") >= 0;
    whyWrap.classList.toggle("trade-decision-why--conflict", sinalCV);

    var chunks = [];
    if (R && R.motivo) chunks.push(String(R.motivo).trim());
    if (V && V.aprovado === false && V.erro_encontrado) {
      var err = String(V.erro_encontrado).trim();
      var rm = R && R.motivo ? String(R.motivo) : "";
      if (err && rm.indexOf(err) < 0) {
        chunks.push("Validação: " + err);
      }
    }
    whyText.textContent = padPrecosTresDecimais(
      chunks.length > 0
        ? chunks.join(" ")
        : "O gestor de risco não autorizou a execução. Consulte score, confluência e regras no JSON dos agentes."
    );
    whyWrap.hidden = false;
  }

  function fmtPnlDisplay(v) {
    if (v == null || v === "") return "";
    var n = Number(v);
    if (isNaN(n)) return String(v);
    return n.toLocaleString("pt-BR", { minimumFractionDigits: 0, maximumFractionDigits: 2 });
  }

  function renderJournalPanel(prefix, data, isBuy, isSell) {
    var wrap = document.getElementById(prefix + "trade-journal-wrap");
    if (!wrap) return;
    var isModal = prefix.indexOf("modal") >= 0;
    // Na tela principal, só mostra o journal para COMPRA/VENDA. No modal de detalhe, sempre (execução real).
    if (!isBuy && !isSell && !isModal) {
      wrap.hidden = true;
      return;
    }
    wrap.hidden = false;
    var idEl = document.getElementById(prefix + "trade-journal-analysis-id");
    if (idEl && data.id != null) idEl.value = String(data.id);
    var entry = document.getElementById(prefix + "trade-exec-entry");
    var ex = document.getElementById(prefix + "trade-exec-exit");
    var pnlIn = document.getElementById(prefix + "trade-exec-pnl");
    if (entry) entry.value = data.exec_entry != null ? String(data.exec_entry) : "";
    if (ex) ex.value = data.exec_exit != null ? String(data.exec_exit) : "";
    if (pnlIn) {
      if (data.exec_pnl != null && data.exec_pnl !== "") {
        pnlIn.value = fmtPnlDisplay(data.exec_pnl);
      } else {
        pnlIn.value = "";
      }
    }
    var st = document.getElementById(prefix + "trade-journal-status");
    if (st) {
      st.hidden = true;
      st.textContent = "";
      st.className = "trade-journal-status";
    }
  }

  function getAlvos(trader) {
    if (!trader) return [];
    if (Array.isArray(trader.alvos) && trader.alvos.length) {
      return trader.alvos.filter(function (a) {
        return a && String(a.distancia || "").trim() && String(a.distancia) !== "—";
      });
    }
    if (trader.alvo && String(trader.alvo).trim() && trader.alvo !== "—") {
      return [
        {
          nome: "TP1",
          distancia: String(trader.alvo),
          probabilidade: Number(trader.confianca) || 0,
          rr: trader.rr || "—",
        },
      ];
    }
    return [];
  }

  function setProgressOpen(open) {
    const el = document.getElementById("progress-overlay");
    if (!el) return;
    el.classList.toggle("is-open", open);
    el.setAttribute("aria-hidden", open ? "false" : "true");
  }

  function setProgressStep(activeIndex) {
    const steps = document.querySelectorAll(".progress-step");
    steps.forEach(function (step, i) {
      step.classList.remove("is-active", "is-done", "is-pending");
      if (i < activeIndex) step.classList.add("is-done");
      else if (i === activeIndex) step.classList.add("is-active");
      else step.classList.add("is-pending");
    });
  }

  /**
   * @param {object} data - trader, validator, risk_manager, image_url, resumo
   * @param {object} opts - target: 'main' | 'modal', elapsedSec, createdAt (ISO, para histórico)
   */
  function renderTradeView(data, opts) {
    opts = opts || {};
    const isModal = opts.target === "modal";
    const P = isModal ? "modal-" : "";

    function $(id) {
      return document.getElementById(P + id);
    }

    const T = parseJson(data.trader);
    const V = parseJson(data.validator);
    const R = parseJson(data.risk_manager);
    const resumo = data.resumo || {};

    const card = $("trade-signal-card");
    if (!card) return;

    const acaoRaw = T && T.acao ? String(T.acao).toUpperCase() : "";
    const isBuy = acaoRaw.indexOf("COMPRA") >= 0;
    const isSell = acaoRaw.indexOf("VENDA") >= 0;
    const isHold =
      acaoRaw.indexOf("NÃO OPERAR") >= 0 ||
      acaoRaw.indexOf("NAO OPERAR") >= 0 ||
      (!isBuy && !isSell);

    card.classList.remove("trade-signal-card--buy", "trade-signal-card--sell", "trade-signal-card--neutral");
    if (isBuy) card.classList.add("trade-signal-card--buy");
    else if (isSell) card.classList.add("trade-signal-card--sell");
    else card.classList.add("trade-signal-card--neutral");

    const ico = $("trade-signal-ico");
    if (ico) ico.textContent = isBuy ? "↑" : isSell ? "↓" : "◌";

    const labelTop = $("trade-signal-title");
    if (labelTop) {
      if (isHold) {
        labelTop.textContent = "Aguardar — sem setup claro";
      } else {
        labelTop.textContent = "Sinal identificado";
      }
    }

    const badge = $("trade-acao-badge");
    if (badge) {
      badge.classList.remove("trade-acao-badge--buy", "trade-acao-badge--sell", "trade-acao-badge--hold");
      if (isBuy) {
        badge.textContent = "COMPRA";
        badge.classList.add("trade-acao-badge--buy");
      } else if (isSell) {
        badge.textContent = "VENDA";
        badge.classList.add("trade-acao-badge--sell");
      } else {
        badge.textContent = acaoRaw.replace(/_/g, " ") || "NÃO OPERAR";
        badge.classList.add("trade-acao-badge--hold");
      }
    }

    const conf = T && T.confianca != null ? Number(T.confianca) : 0;
    const c = Math.max(0, Math.min(100, conf));
    const confFill = $("trade-conf-fill");
    const confPct = $("trade-conf-pct");
    if (confFill) confFill.style.width = c + "%";
    if (confPct) confPct.textContent = Math.round(c) + "%";

    const ativoEl = $("trade-ativo");
    if (ativoEl) {
      const sym = T && T.ativo ? String(T.ativo).trim() : "";
      const symHow = T && T.ativo_como_detectado ? String(T.ativo_como_detectado).trim() : "";
      ativoEl.textContent = sym || "—";
      ativoEl.title = symHow || "Ticker não informado — o modelo deve ler o símbolo visível no print.";
    }

    const tfEl = $("trade-timeframe");
    if (tfEl) {
      const tfRaw = T && T.timeframe ? String(T.timeframe).trim() : "";
      const tfDetect = T && T.timeframe_como_detectado ? String(T.timeframe_como_detectado).trim() : "";
      tfEl.textContent = tfRaw ? "TF " + tfRaw : "TF ?";
      tfEl.title = tfDetect || "Timeframe não informado pelo modelo — inclua o intervalo visível no print.";
    }
    const tfNote = $("trade-tf-note");
    if (tfNote) {
      const obs = T && T.timeframe_observacao ? String(T.timeframe_observacao).trim() : "";
      if (obs) {
        tfNote.hidden = false;
        tfNote.textContent = padPrecosTresDecimais(obs);
      } else {
        tfNote.hidden = true;
        tfNote.textContent = "";
      }
    }

    const img = $("trade-chart-img");
    if (img) {
      img.src = data.image_url || "";
      img.alt = "Gráfico analisado";
    }

    const padraoPill = $("trade-padrao-pill");
    if (padraoPill) {
      padraoPill.textContent = T && T.padrao ? padPrecosTresDecimais(T.padrao) : "—";
    }

    const entradaEl = $("trade-entrada");
    if (entradaEl) {
      entradaEl.textContent = T && T.entrada ? padPrecosTresDecimais(T.entrada) : "—";
    }
    const stopTxt = T && (T.stop_em_pontos || T.stop) ? T.stop_em_pontos || T.stop : "—";
    const stopEl = $("trade-stop");
    if (stopEl) stopEl.textContent = padPrecosTresDecimais(stopTxt);
    const alvos = getAlvos(T);
    const tp1 = alvos[0];
    const tp1El = $("trade-tp1");
    if (tp1El) {
      const tp1raw = tp1 ? tp1.distancia : T && T.alvo ? T.alvo : "—";
      tp1El.textContent = padPrecosTresDecimais(tp1raw);
    }
    const rrEl = $("trade-rr-main");
    if (rrEl) rrEl.textContent = T && T.rr ? T.rr : "—";

    const confLu = $("trade-confluencia");
    if (confLu) confLu.innerHTML = esc(V && V.nota_confluencia ? V.nota_confluencia : "—");
    const tend = T && T.tendencia ? String(T.tendencia).toUpperCase() : "—";
    const tendEl = $("trade-tendencia");
    if (tendEl) {
      tendEl.textContent = tend;
      tendEl.style.color = tend.indexOf("BAIXA") >= 0 ? "#f87171" : tend.indexOf("ALTA") >= 0 ? "#4ade80" : "";
    }

    const tecEl = $("trade-tecnico");
    if (tecEl) tecEl.textContent = T && T.acao ? T.acao : "—";

    const tpBody = $("trade-tp-body");
    if (tpBody) {
      tpBody.innerHTML = "";
      const showAlvos = alvos.length ? alvos.slice(0, 3) : [];
      if (showAlvos.length === 0) {
        const tr = document.createElement("div");
        tr.className = "trade-tp-row";
        tr.innerHTML =
          '<span class="trade-tp-name">—</span><div class="trade-tp-col-metric"><span class="trade-tp-pts">—</span></div><div class="trade-tp-barwrap"><div class="trade-tp-bar" style="width:0%"></div></div><span>—</span><span class="trade-tp-rr">—</span>';
        tpBody.appendChild(tr);
      } else {
        showAlvos.forEach(function (a) {
          const prob = Math.max(0, Math.min(100, Number(a.probabilidade) || 0));
          const row = document.createElement("div");
          row.className = "trade-tp-row";
          var exitLn = buildAlvoExitLine(T, a, isBuy, isSell);
          row.innerHTML =
            '<span class="trade-tp-name">' +
            esc(a.nome || "TP") +
            '</span><div class="trade-tp-col-metric"><span class="trade-tp-pts">' +
            esc(padPrecosTresDecimais(a.distancia || "")) +
            "</span>" +
            (exitLn ? '<span class="trade-tp-exit">' + esc(exitLn) + "</span>" : "") +
            '</div><div class="trade-tp-barwrap"><div class="trade-tp-bar" style="width:' +
            prob +
            '%"></div></div><span>' +
            prob +
            '%</span><span class="trade-tp-rr">' +
            esc(a.rr || "—") +
            "</span>";
          tpBody.appendChild(row);
        });
      }
    }

    const ulS = $("trade-suporte-list");
    const ulR = $("trade-resistencia-list");
    if (ulS) {
      ulS.innerHTML = "";
      if (T && Array.isArray(T.suporte) && T.suporte.length) {
        T.suporte.forEach(function (x) {
          const li = document.createElement("li");
          li.textContent = padPrecosTresDecimais(x);
          ulS.appendChild(li);
        });
      } else {
        ulS.innerHTML = "<li>—</li>";
      }
    }
    if (ulR) {
      ulR.innerHTML = "";
      if (T && Array.isArray(T.resistencia) && T.resistencia.length) {
        T.resistencia.forEach(function (x) {
          const li = document.createElement("li");
          li.textContent = padPrecosTresDecimais(x);
          ulR.appendChild(li);
        });
      } else {
        ulR.innerHTML = "<li>—</li>";
      }
    }

    const parts = [];
    if (T && T.justificativa) parts.push(T.justificativa);
    const narr = $("trade-narrative-text");
    if (narr) {
      narr.textContent = parts.length ? padPrecosTresDecimais(parts.join("\n\n")) : "—";
    }

    fillDecisionWhy($, T, R, V, resumo);

    const rs = $("trade-risk-strip");
    if (rs) {
      const d = resumo.decisao || (R && R.decisao) || "—";
      const sc = resumo.score_final != null ? resumo.score_final : "—";
      const pt =
        resumo.permitir_trade === true ? "sim" : resumo.permitir_trade === false ? "não" : "—";
      rs.innerHTML =
        "<strong>Decisão final:</strong> " +
        esc(d) +
        " &nbsp;|&nbsp; <strong>Score:</strong> " +
        esc(sc) +
        " &nbsp;|&nbsp; <strong>Permitir trade:</strong> " +
        esc(pt);
    }

    const rawT = $("raw-trader");
    const rawV = $("raw-validator");
    const rawR = $("raw-risk");
    if (rawT) rawT.textContent = prettifyJson(data.trader);
    if (rawV) rawV.textContent = prettifyJson(data.validator);
    if (rawR) rawR.textContent = prettifyJson(data.risk_manager);

    const procEl = $("trade-proc-time");
    if (procEl) {
      if (isModal && opts.createdAt) {
        procEl.textContent = String(opts.createdAt).slice(0, 19).replace("T", " ") + " UTC";
      } else if (opts.elapsedSec != null) {
        procEl.textContent = opts.elapsedSec.toFixed(1) + "s";
      } else {
        procEl.textContent = "—";
      }
    }

    renderJournalPanel(isModal ? "modal-" : "", data, isBuy, isSell);
    (function bindJournalMeta() {
      var jw = document.getElementById(P + "trade-journal-wrap");
      if (!jw) return;
      jw.dataset.journalAtivo = T && T.ativo ? String(T.ativo).trim() : "";
      jw.dataset.journalBuy = isBuy ? "1" : "";
      jw.dataset.journalSell = isSell ? "1" : "";
    })();
    if (isModal) {
      attachJournalPnlAuto("modal-", {
        ativo: T && T.ativo ? String(T.ativo) : "",
        isBuy: isBuy,
        isSell: isSell,
      });
    } else if (isBuy || isSell) {
      attachJournalPnlAuto("", {
        ativo: T && T.ativo ? String(T.ativo) : "",
        isBuy: isBuy,
        isSell: isSell,
      });
    }

    if (!isModal) {
      document.getElementById("trade-result").classList.add("is-visible");
      document.getElementById("trade-result").scrollIntoView({ behavior: "smooth", block: "start" });
    } else {
      const loading = document.getElementById("modal-loading");
      const root = document.getElementById("modal-detail-root");
      if (loading) loading.hidden = true;
      if (root) root.hidden = false;
    }
  }

  const form = document.getElementById("analyze-form");
  const input = document.getElementById("file-input");
  const dropzone = document.getElementById("dropzone");
  const btn = document.getElementById("submit-btn");
  const fileStatus = document.getElementById("file-status");
  if (!form || !input || !dropzone || !btn) {
    console.error("Trade AI: formulário incompleto no DOM.");
    return;
  }

  let trades = [];

  function loadHistoryFromApi() {
    const url = cfg.historyUrl || "/api/history";
    fetch(url, { credentials: "same-origin" })
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        trades = (data && data.items) || [];
      })
      .catch(function () {
        trades = [];
      });
  }
  loadHistoryFromApi();

  function updateHistoryJournalCell(analysisId, recorded) {
    var tr = document.querySelector('#history-body tr[data-id="' + analysisId + '"]');
    if (!tr) return;
    var td = tr.querySelector(".td-journal");
    if (!td) return;
    td.innerHTML = recorded
      ? '<span class="badge ok" title="Registrado">✓</span>'
      : '<span class="muted">—</span>';
  }

  function readJournalPayload(prefix) {
    var en = document.getElementById(prefix + "trade-exec-entry");
    var ex = document.getElementById(prefix + "trade-exec-exit");
    var pnlRaw = document.getElementById(prefix + "trade-exec-pnl");
    var pv = pnlRaw && pnlRaw.value ? pnlRaw.value.trim() : "";
    var pnlNum = null;
    if (pv !== "") {
      pnlNum = parseBrazilianNumber(pv);
      if (pnlNum == null || isNaN(pnlNum)) pnlNum = null;
    }
    return {
      recorded: true,
      entry: en && en.value ? en.value.trim() : "",
      exit: ex && ex.value ? ex.value.trim() : "",
      pnl: pnlNum,
    };
  }

  function enrichJournalPayloadFromPrices(prefix, payload) {
    if (!payload || !payload.recorded) return payload;
    var entryOk = payload.entry && String(payload.entry).trim().length > 0;
    var exitOk = payload.exit && String(payload.exit).trim().length > 0;
    if (!entryOk || !exitOk || payload.pnl != null) return payload;
    var wrap = document.getElementById(prefix + "trade-journal-wrap");
    if (!wrap || !wrap.dataset) return payload;
    var jctx = {
      isBuy: wrap.dataset.journalBuy === "1",
      isSell: wrap.dataset.journalSell === "1",
      ativo: wrap.dataset.journalAtivo || "",
    };
    var dir = resolveJournalDirection(jctx, payload.entry, payload.exit);
    if (!dir) return payload;
    var auto = computeExecPnlBrl(payload.entry, payload.exit, dir.isBuy, jctx.ativo);
    if (auto && auto.brl != null && !isNaN(auto.brl)) payload.pnl = auto.brl;
    return payload;
  }

  function saveJournal(prefix) {
    var idEl = document.getElementById(prefix + "trade-journal-analysis-id");
    var statusEl = document.getElementById(prefix + "trade-journal-status");
    if (!idEl || !idEl.value) return;
    var id = Number(idEl.value);
    var payload = enrichJournalPayloadFromPrices(prefix, readJournalPayload(prefix));
    fetch(execPatchUrl(id), {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify(payload),
    })
      .then(function (r) {
        return r.json().then(function (j) {
          if (!r.ok) throw new Error(j.error || "Erro ao salvar");
          return j;
        });
      })
      .then(function (out) {
        var T = parseJson(out.trader_json);
        var acaoRaw = T && T.acao ? String(T.acao).toUpperCase() : "";
        var isBuy = acaoRaw.indexOf("COMPRA") >= 0;
        var isSell = acaoRaw.indexOf("VENDA") >= 0;
        renderJournalPanel(prefix, out, isBuy, isSell);
        (function () {
          var jw = document.getElementById(prefix + "trade-journal-wrap");
          if (jw) {
            jw.dataset.journalAtivo = T && T.ativo ? String(T.ativo).trim() : "";
            jw.dataset.journalBuy = isBuy ? "1" : "";
            jw.dataset.journalSell = isSell ? "1" : "";
          }
        })();
        if (prefix.indexOf("modal") >= 0 || isBuy || isSell) {
          attachJournalPnlAuto(prefix, {
            ativo: T && T.ativo ? String(T.ativo) : "",
            isBuy: isBuy,
            isSell: isSell,
          });
        }
        if (statusEl) {
          statusEl.hidden = false;
          statusEl.className = "trade-journal-status";
          statusEl.textContent = "Registro salvo.";
        }
        loadStats();
        updateHistoryJournalCell(id, !!out.exec_recorded);
      })
      .catch(function (err) {
        if (statusEl) {
          statusEl.hidden = false;
          statusEl.className = "trade-journal-status trade-journal-status--err";
          statusEl.textContent = err.message || "Falha ao salvar.";
        }
      });
  }

  function clearJournal(prefix) {
    var idEl = document.getElementById(prefix + "trade-journal-analysis-id");
    var statusEl = document.getElementById(prefix + "trade-journal-status");
    if (!idEl || !idEl.value) return;
    var id = Number(idEl.value);
    fetch(execPatchUrl(id), {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ recorded: false }),
    })
      .then(function (r) {
        return r.json().then(function (j) {
          if (!r.ok) throw new Error(j.error || "Erro");
          return j;
        });
      })
      .then(function (out) {
        var T = parseJson(out.trader_json);
        var acaoRaw = T && T.acao ? String(T.acao).toUpperCase() : "";
        var isBuy = acaoRaw.indexOf("COMPRA") >= 0;
        var isSell = acaoRaw.indexOf("VENDA") >= 0;
        renderJournalPanel(prefix, out, isBuy, isSell);
        (function () {
          var jw = document.getElementById(prefix + "trade-journal-wrap");
          if (jw) {
            jw.dataset.journalAtivo = T && T.ativo ? String(T.ativo).trim() : "";
            jw.dataset.journalBuy = isBuy ? "1" : "";
            jw.dataset.journalSell = isSell ? "1" : "";
          }
        })();
        if (prefix.indexOf("modal") >= 0 || isBuy || isSell) {
          attachJournalPnlAuto(prefix, {
            ativo: T && T.ativo ? String(T.ativo) : "",
            isBuy: isBuy,
            isSell: isSell,
          });
        }
        if (statusEl) {
          statusEl.hidden = false;
          statusEl.className = "trade-journal-status";
          statusEl.textContent = "Registro removido.";
        }
        loadStats();
        updateHistoryJournalCell(id, false);
      })
      .catch(function (err) {
        if (statusEl) {
          statusEl.hidden = false;
          statusEl.className = "trade-journal-status trade-journal-status--err";
          statusEl.textContent = err.message || "Falha.";
        }
      });
  }

  function loadStats() {
    var base = cfg.statsUrl || "/api/stats";
    var q = ["period=" + encodeURIComponent(statsPeriod)];
    if (statsPeriod !== "all") {
      q.push("ref=" + encodeURIComponent(statsRefDate));
    }
    var full = base + (base.indexOf("?") >= 0 ? "&" : "?") + q.join("&");
    fetch(full, { credentials: "same-origin" })
      .then(function (r) {
        return r.json();
      })
      .then(function (s) {
        function el(id, v) {
          var n = document.getElementById(id);
          if (n) n.textContent = v != null ? String(v) : "—";
        }
        el("stat-registrados", s.total_registrados);
        el("stat-com-pnl", s.com_pnl_informado);
        el("stat-wins", s.wins);
        el("stat-losses", s.losses);
        el("stat-breakeven", s.breakeven);
        el("stat-rec-compra", s.recomendacoes_compra);
        el("stat-rec-venda", s.recomendacoes_venda);
        var pnl = s.pnl_total;
        var pnlEl = document.getElementById("stat-pnl-total");
        if (pnlEl) {
          pnlEl.textContent = pnl != null ? fmtPnlDisplay(pnl) : "—";
          pnlEl.classList.remove("is-pos", "is-neg");
          if (pnl > 0) pnlEl.classList.add("is-pos");
          else if (pnl < 0) pnlEl.classList.add("is-neg");
        }
        var ctxEl = document.getElementById("stats-contexto");
        if (ctxEl) ctxEl.textContent = s.contexto || "";
        var hj = document.getElementById("stats-hoje");
        if (hj) {
          if (s.period === "all") {
            var h = s.hoje_utc || {};
            var st = h.status || "neutro";
            var label =
              st === "lucro"
                ? "Lucro no dia (UTC)"
                : st === "prejuizo"
                ? "Prejuízo no dia (UTC)"
                : "Dia neutro (soma zero ou sem P/L)";
            hj.innerHTML =
              "<strong>Hoje (UTC)</strong> — " +
              esc(h.data || "") +
              " · " +
              esc(label) +
              ": <strong>" +
              esc(h.pnl != null ? String(h.pnl) : "—") +
              "</strong> · " +
              esc(String(h.trades_registrados_hoje != null ? h.trades_registrados_hoje : "—")) +
              " registro(s).";
          } else {
            var inv = s.intervalo;
            var invTxt = inv ? esc(inv.inicio) + " → " + esc(inv.fim) : "—";
            hj.innerHTML =
              "<strong>Resumo do filtro</strong> — " +
              invTxt +
              " · " +
              esc(String(s.total_registrados != null ? s.total_registrados : "—")) +
              " registro(s) · P/L: <strong>" +
              esc(s.pnl_total != null ? String(s.pnl_total) : "—") +
              "</strong>";
          }
        }
        document.querySelectorAll(".stats-pill").forEach(function (p) {
          p.classList.toggle("is-active", p.getAttribute("data-period") === (s.period || statsPeriod));
        });
      })
      .catch(function () {});
  }

  var statsRefInput = document.getElementById("stats-ref-date");
  if (statsRefInput && !statsRefInput.value) {
    statsRefInput.value = todayIsoUtc();
    statsRefDate = statsRefInput.value;
  }

  document.querySelectorAll(".stats-pill").forEach(function (pill) {
    pill.addEventListener("click", function () {
      statsPeriod = pill.getAttribute("data-period") || "all";
      var wrap = document.getElementById("stats-ref-wrap");
      var hint = document.getElementById("stats-ref-hint");
      if (wrap) wrap.hidden = statsPeriod === "all";
      if (hint) {
        hint.textContent =
          statsPeriod === "day"
            ? "Dia (UTC)"
            : statsPeriod === "month"
            ? "Referência do mês (UTC)"
            : "";
      }
      loadStats();
    });
  });
  statsRefInput?.addEventListener("change", function () {
    if (this.value) statsRefDate = this.value;
    loadStats();
  });

  loadStats();

  document.getElementById("trade-journal-form")?.addEventListener("submit", function (e) {
    e.preventDefault();
    saveJournal("");
  });
  document.getElementById("modal-trade-journal-form")?.addEventListener("submit", function (e) {
    e.preventDefault();
    saveJournal("modal-");
  });
  document.getElementById("trade-journal-clear")?.addEventListener("click", function () {
    clearJournal("");
  });
  document.getElementById("modal-trade-journal-clear")?.addEventListener("click", function () {
    clearJournal("modal-");
  });

  /** Arquivo escolhido/colido (Safari nem sempre aceita só input.files = DataTransfer). */
  let stagedFile = null;

  let progressTimer = null;
  let abortCtl = null;

  function setLoading(loading) {
    btn.disabled = loading;
    btn.innerHTML = loading
      ? '<span class="loader" aria-hidden="true"></span> Analisando…'
      : "Analisar com agentes";
  }

  function updateFileStatus() {
    if (!fileStatus) return;
    const f = stagedFile || (input.files && input.files[0]);
    if (!f) {
      fileStatus.textContent = "";
      return;
    }
    const kb = (f.size / 1024).toFixed(f.size > 10240 ? 0 : 1);
    fileStatus.textContent = "Imagem anexada: " + f.name + " (" + kb + " KB)";
  }

  function syncInputFromStaged() {
    if (!stagedFile) return;
    try {
      const dt = new DataTransfer();
      dt.items.add(stagedFile);
      input.files = dt.files;
    } catch (err) {
      /* WebKit pode ignorar; o envio usa stagedFile diretamente. */
    }
  }

  function isProbablyImageFile(file) {
    if (!file) return false;
    if (file.type && file.type.indexOf("image") === 0) return true;
    const n = (file.name || "").toLowerCase();
    return /\.(png|jpe?g|gif|webp)$/i.test(n);
  }

  function setStagedFile(file) {
    if (!file || !isProbablyImageFile(file)) return;
    stagedFile = file;
    syncInputFromStaged();
    updateFileStatus();
  }

  /**
   * Converte TIFF/WebP exótico etc. para PNG quando o backend não aceita o MIME.
   */
  function ensureUploadableFile(file) {
    return new Promise(function (resolve) {
      const t = (file.type || "").toLowerCase();
      if (
        t === "image/png" ||
        t === "image/jpeg" ||
        t === "image/jpg" ||
        t === "image/webp" ||
        t === "image/gif" ||
        t === "image/pjpeg"
      ) {
        resolve(file);
        return;
      }
      const url = URL.createObjectURL(file);
      const img = new Image();
      img.onload = function () {
        URL.revokeObjectURL(url);
        try {
          const w = img.naturalWidth || img.width;
          const h = img.naturalHeight || img.height;
          if (!w || !h) {
            resolve(file);
            return;
          }
          const c = document.createElement("canvas");
          c.width = w;
          c.height = h;
          c.getContext("2d").drawImage(img, 0, 0);
          c.toBlob(
            function (blob) {
              if (!blob) {
                resolve(file);
                return;
              }
              resolve(new File([blob], "captura.png", { type: "image/png" }));
            },
            "image/png",
            0.92
          );
        } catch (e) {
          resolve(file);
        }
      };
      img.onerror = function () {
        URL.revokeObjectURL(url);
        resolve(file);
      };
      img.src = url;
    });
  }

  document.getElementById("btn-new-analysis")?.addEventListener("click", function () {
    document.getElementById("trade-result")?.classList.remove("is-visible");
    stagedFile = null;
    input.value = "";
    updateFileStatus();
    document.getElementById("upload-section")?.scrollIntoView({ behavior: "smooth" });
  });

  input.addEventListener("change", function () {
    const f = input.files && input.files[0];
    if (f && !isProbablyImageFile(f)) {
      alert("Use uma imagem PNG, JPG, WebP ou GIF.");
      input.value = "";
      stagedFile = null;
      updateFileStatus();
      return;
    }
    stagedFile = f || null;
    updateFileStatus();
  });

  document.getElementById("copy-entrada")?.addEventListener("click", function () {
    const t = document.getElementById("trade-entrada").textContent;
    if (t && t !== "—") navigator.clipboard.writeText(t);
  });

  document.getElementById("modal")?.addEventListener("click", function (e) {
    const bt = e.target.closest("#modal-copy-entrada");
    if (!bt) return;
    const t = document.getElementById("modal-trade-entrada");
    if (t && t.textContent && t.textContent !== "—") navigator.clipboard.writeText(t.textContent);
  });

  form.addEventListener("submit", async function (e) {
    e.preventDefault();
    e.stopPropagation();
    let raw = stagedFile || (input.files && input.files[0]);
    if (!raw) {
      alert("Selecione uma imagem (clique na área) ou cole um print (Cmd+V no Mac, Ctrl+V no Windows).");
      return;
    }

    let fileToSend;
    try {
      fileToSend = await ensureUploadableFile(raw);
    } catch (e) {
      fileToSend = raw;
    }

    abortCtl = new AbortController();
    const fd = new FormData();
    fd.append("image", fileToSend, fileToSend.name || "chart.png");

    setProgressOpen(true);
    setProgressStep(0);
    let stepIdx = 0;
    progressTimer = setInterval(function () {
      if (stepIdx < 3) {
        stepIdx += 1;
        setProgressStep(stepIdx);
      }
    }, 520);

    const t0 = performance.now();
    setLoading(true);

    try {
      const r = await fetch(cfg.analyzeUrl || "/analyze", {
        method: "POST",
        body: fd,
        signal: abortCtl.signal,
      });
      const data = await r.json();
      if (!r.ok) {
        clearInterval(progressTimer);
        progressTimer = null;
        setProgressOpen(false);
        alert(data.error || "Erro na análise.");
        setLoading(false);
        return;
      }
      clearInterval(progressTimer);
      progressTimer = null;
      setProgressStep(4);
      const elapsed = (performance.now() - t0) / 1000;
      setTimeout(function () {
        setProgressOpen(false);
        renderTradeView(data, { target: "main", elapsedSec: elapsed });
        setLoading(false);
      }, 480);

      var T0 = parseJson(data.trader);
      var al0 =
        T0 && T0.ativo != null && String(T0.ativo).trim()
          ? String(T0.ativo).trim()
          : "—";
      var ah0 =
        T0 && T0.ativo_como_detectado ? String(T0.ativo_como_detectado).trim() : "";
      const rowPayload = {
        id: data.id,
        created_at: data.created_at,
        stored_filename: null,
        trader_json: data.trader,
        validator_json: data.validator,
        risk_json: data.risk_manager,
        decisao: data.resumo && data.resumo.decisao,
        score_final: data.resumo ? data.resumo.score_final : null,
        permitir_trade:
          data.resumo && data.resumo.permitir_trade === true
            ? 1
            : data.resumo && data.resumo.permitir_trade === false
            ? 0
            : null,
        image_url: data.image_url,
        exec_recorded: data.exec_recorded ? 1 : 0,
        exec_entry: data.exec_entry,
        exec_exit: data.exec_exit,
        exec_pnl: data.exec_pnl,
        exec_logged_at: data.exec_logged_at,
        ativo_label: al0,
        ativo_hint: ah0,
      };
      trades.unshift(rowPayload);
      prependHistoryRow(rowPayload);
      loadHistoryFromApi();
    } catch (err) {
      clearInterval(progressTimer);
      progressTimer = null;
      setProgressOpen(false);
      setLoading(false);
      if (err.name === "AbortError") return;
      alert("Falha de rede ou servidor.");
    }
  });

  document.getElementById("progress-close")?.addEventListener("click", function () {
    if (abortCtl) abortCtl.abort();
    clearInterval(progressTimer);
    progressTimer = null;
    setProgressOpen(false);
    setLoading(false);
  });

  ["dragenter", "dragover", "dragleave", "drop"].forEach(function (ev) {
    dropzone.addEventListener(ev, function (e) {
      e.preventDefault();
      e.stopPropagation();
    });
  });
  dropzone.addEventListener("dragover", function () {
    dropzone.classList.add("dragover");
  });
  dropzone.addEventListener("dragleave", function () {
    dropzone.classList.remove("dragover");
  });
  dropzone.addEventListener("drop", function (e) {
    dropzone.classList.remove("dragover");
    const f = e.dataTransfer.files[0];
    if (f && isProbablyImageFile(f)) {
      setStagedFile(f);
      e.preventDefault();
    }
  });

  document.addEventListener(
    "paste",
    function (e) {
      const items = e.clipboardData && e.clipboardData.items;
      if (!items || !items.length) return;

      for (let i = 0; i < items.length; i++) {
        const item = items[i];
        let blob = null;

        if (item.kind === "file") {
          blob = item.getAsFile();
        } else if (item.type && item.type.indexOf("image") === 0) {
          blob = item.getAsFile();
        }

        if (blob && blob.size > 0) {
          const name =
            blob.name && blob.name !== "image.png"
              ? blob.name
              : "print-" + (blob.type && blob.type.indexOf("jpeg") >= 0 ? "jpg" : "png");
          const type = blob.type || "image/png";
          const file = blob instanceof File ? blob : new File([blob], name, { type: type });
          if (!isProbablyImageFile(file)) continue;
          setStagedFile(file);
          e.preventDefault();
          e.stopPropagation();
          dropzone.classList.add("dragover");
          setTimeout(function () {
            dropzone.classList.remove("dragover");
          }, 400);
          document.getElementById("upload-section")?.scrollIntoView({ behavior: "smooth", block: "nearest" });
          return;
        }
      }
    },
    true
  );

  function escapeHtml(s) {
    const d = document.createElement("div");
    d.textContent = s;
    return d.innerHTML;
  }

  function prependHistoryRow(row) {
    const tbody = document.getElementById("history-body");
    const empty = document.getElementById("history-empty");
    if (!tbody) {
      if (empty) window.location.reload();
      return;
    }
    if (empty) empty.remove();
    const decisao = (row.decisao || "").toUpperCase();
    let decHtml;
    if (decisao.indexOf("EXECUTAR") >= 0) {
      decHtml = '<span class="badge ok">' + escapeHtml(row.decisao || "") + "</span>";
    } else if (row.decisao) {
      decHtml = '<span class="badge no">' + escapeHtml(row.decisao) + "</span>";
    } else {
      decHtml = '<span class="badge neutral">&mdash;</span>';
    }
    let permHtml;
    if (row.permitir_trade === 1) {
      permHtml = '<span class="badge ok">Sim</span>';
    } else if (row.permitir_trade === 0) {
      permHtml = '<span class="badge no">Não</span>';
    } else {
      permHtml = '<span class="badge neutral">&mdash;</span>';
    }
    const regHtml =
      row.exec_recorded === true || row.exec_recorded === 1
        ? '<span class="badge ok" title="Registrado">✓</span>'
        : '<span class="muted">—</span>';
    const tr = document.createElement("tr");
    tr.setAttribute("data-id", String(row.id));
    var atv = ativoLabelFromRow(row);
    var atvHint = ativoHintFromRow(row);
    var atvTd =
      '<td class="td-ativo"' +
      (atvHint ? ' title="' + escapeHtml(atvHint) + '"' : "") +
      ">" +
      escapeHtml(atv) +
      "</td>";
    tr.innerHTML =
      "<td>" +
      escapeHtml((row.created_at || "").slice(0, 19).replace("T", " ")) +
      '</td><td class="thumb"><img class="thumb" src="' +
      escapeHtml(row.image_url || "") +
      '" alt="" width="56" height="56" loading="lazy" /></td>' +
      atvTd +
      "<td>" +
      decHtml +
      "</td><td>" +
      escapeHtml(row.score_final != null ? String(row.score_final) : "—") +
      "</td><td>" +
      permHtml +
      '</td><td class="td-journal">' +
      regHtml +
      '</td><td><button type="button" class="btn btn-ghost js-detail" data-id="' +
      row.id +
      '">Detalhes</button></td>';
    tbody.insertBefore(tr, tbody.firstChild);
  }

  const modal = document.getElementById("modal");
  const modalClose = document.getElementById("modal-close");

  function analysisDetailUrl(id) {
    const base = (cfg.apiAnalysisPrefix || "/api/analysis").replace(/\/$/, "");
    return base + "/" + id;
  }

  function openModalDetailLoading() {
    const loading = document.getElementById("modal-loading");
    const root = document.getElementById("modal-detail-root");
    if (loading) {
      loading.hidden = false;
      loading.textContent = "Carregando…";
    }
    if (root) root.hidden = true;
  }

  if (modalClose && modal) {
    modalClose.addEventListener("click", function () {
      modal.classList.remove("open");
    });
    modal.addEventListener("click", function (e) {
      if (e.target === modal) modal.classList.remove("open");
    });
  }

  document.getElementById("history-section")?.addEventListener("click", function (e) {
    const bt = e.target.closest(".js-detail");
    if (!bt) return;
    e.preventDefault();
    e.stopPropagation();
    const id = Number(bt.getAttribute("data-id"));
    if (!id) return;

    modal.classList.add("open");
    openModalDetailLoading();

    fetch(analysisDetailUrl(id), { credentials: "same-origin" })
      .then(function (r) {
        if (!r.ok) throw new Error("fetch");
        return r.json();
      })
      .then(function (row) {
        const permitir =
          row.permitir_trade === 1 ? true : row.permitir_trade === 0 ? false : null;
        const viewData = {
          id: row.id,
          trader: row.trader_json,
          validator: row.validator_json,
          risk_manager: row.risk_json,
          image_url: row.image_url,
          resumo: {
            decisao: row.decisao,
            score_final: row.score_final,
            permitir_trade: permitir,
          },
          exec_recorded: row.exec_recorded,
          exec_entry: row.exec_entry,
          exec_exit: row.exec_exit,
          exec_pnl: row.exec_pnl,
          exec_logged_at: row.exec_logged_at,
        };
        renderTradeView(viewData, { target: "modal", createdAt: row.created_at });
      })
      .catch(function () {
        const loading = document.getElementById("modal-loading");
        if (loading) {
          loading.hidden = false;
          loading.textContent = "Não foi possível carregar os detalhes. Tente atualizar a página.";
        }
      });
  });

  // =========================================================================
  // MONITOR AO VIVO — Tab switching + TradingView + Sinais + Notícias
  // =========================================================================

  // ---- Tab switching ----
  var tvWidget = null;   // instância TradingView

  function showTab(tabName) {
    document.querySelectorAll(".tab-pane").forEach(function (pane) {
      pane.classList.toggle("tab-active", pane.id === "tab-" + tabName);
    });
    document.querySelectorAll(".main-tab-btn").forEach(function (btn) {
      var active = btn.getAttribute("data-tab") === tabName;
      btn.classList.toggle("is-active", active);
      btn.setAttribute("aria-selected", active ? "true" : "false");
    });
    if (tabName === "monitor" && !tvWidget) {
      initTradingViewWidget();
    }
  }

  document.querySelectorAll(".main-tab-btn").forEach(function (btn) {
    btn.addEventListener("click", function () {
      showTab(btn.getAttribute("data-tab") || "dashboard");
    });
  });

  // ---- TradingView Widget ----
  function getTvSymbol() {
    var sel = document.getElementById("mon-instrument");
    if (!sel) return "BMFBOVESPA:WIN1!";
    var val = sel.value;
    if (val === "__custom__") {
      var custom = document.getElementById("mon-custom-tv");
      return (custom && custom.value.trim()) || "BMFBOVESPA:WIN1!";
    }
    return val;
  }

  function getTvInterval() {
    var sel = document.getElementById("mon-interval");
    return (sel && sel.value) || "15";
  }

  function initTradingViewWidget(symbol, interval) {
    symbol   = symbol   || getTvSymbol();
    interval = interval || getTvInterval();

    var container = document.getElementById("tradingview_chart");
    if (!container) return;
    container.innerHTML = "";

    if (typeof TradingView === "undefined") {
      container.innerHTML = '<p style="padding:1rem;color:var(--text-muted)">TradingView não carregou. Verifique sua conexão.</p>';
      return;
    }

    try {
      tvWidget = new TradingView.widget({
        width:  "100%",
        height: 520,
        symbol: symbol,
        interval: interval,
        timezone: "America/Sao_Paulo",
        theme: "dark",
        style: "1",
        locale: "br",
        toolbar_bg: "#151d28",
        enable_publishing: false,
        allow_symbol_change: true,
        hide_top_toolbar: false,
        hide_legend: false,
        save_image: false,
        container_id: "tradingview_chart",
        studies: ["MAExp@tv-basicstudies", "RSI@tv-basicstudies", "MACD@tv-basicstudies"],
      });
    } catch (e) {
      console.warn("TradingView widget error:", e);
      container.innerHTML = '<p style="padding:2rem;color:var(--danger);text-align:center">Erro ao carregar TradingView: ' + e.message + '</p>';
    }
  }

  // ---- Instrument selector ----
  var monInstrumentSel = document.getElementById("mon-instrument");
  var monCustomWrap    = document.getElementById("mon-custom-wrap");

  if (monInstrumentSel) {
    monInstrumentSel.addEventListener("change", function () {
      if (monCustomWrap) monCustomWrap.hidden = this.value !== "__custom__";
    });
  }

  // ---- Notificações do browser ----
  var notifEnabled      = false;
  var lastNotifiedAcao  = null;   // rastreia último sinal notificado para evitar spam

  function updateNotifStatus() {
    var el      = document.getElementById("mon-notif-status");
    var testBtn = document.getElementById("mon-notif-test-btn");
    if (!el) return;
    if (!("Notification" in window)) {
      el.textContent = "🔕 Notificações não suportadas";
      el.className = "mon-notif-status mon-notif-status--off";
      return;
    }
    if (Notification.permission === "granted" && notifEnabled) {
      el.textContent = "🔔 Notificações ativas";
      el.className = "mon-notif-status mon-notif-status--on";
      if (testBtn) testBtn.style.display = "";
    } else if (Notification.permission === "denied") {
      el.textContent = "🔕 Bloqueadas pelo browser";
      el.className = "mon-notif-status mon-notif-status--off";
      if (testBtn) testBtn.style.display = "none";
    } else {
      el.textContent = "🔕 Clique em 🔔 Notificações para ativar";
      el.className = "mon-notif-status mon-notif-status--off";
      if (testBtn) testBtn.style.display = "none";
    }
  }

  function showNotifHelp(show) {
    var el = document.getElementById("mon-notif-help");
    if (el) el.style.display = show ? "" : "none";
  }

  async function requestNotifications() {
    if (!("Notification" in window)) {
      alert("Seu browser não suporta notificações.");
      return;
    }
    if (Notification.permission === "denied") {
      alert("Notificações bloqueadas. Clique no cadeado (🔒) na barra de endereços e permita notificações para este site.");
      return;
    }
    var perm = Notification.permission === "granted"
      ? "granted"
      : await Notification.requestPermission();

    notifEnabled = perm === "granted";
    updateNotifStatus();
    if (notifEnabled) {
      // Notificação de confirmação — se aparecer, o macOS está configurado corretamente
      fireOsNotification(
        "🔔 Trade AI — Notificações ativas!",
        "Você verá alertas de COMPRA e VENDA mesmo com o browser minimizado.",
        "trade-ai-test",
        false   // requireInteraction = false para a de teste
      );
      // Mostra painel de ajuda caso o usuário não veja nada
      showNotifHelp(true);
    }
  }

  function fireOsNotification(title, body, tag, sticky) {
    // Envia notificação nativa do SO. sticky=true → fica na tela até dispensar.
    try {
      var n = new Notification(title, {
        body:               body,
        tag:                tag || "trade-ai",
        requireInteraction: !!sticky,
        silent:             false,
      });
      n.onclick = function () { window.focus(); n.close(); };
      return true;
    } catch (e) {
      console.warn("Notification error:", e);
      return false;
    }
  }

  function sendTestNotification() {
    if (!notifEnabled || Notification.permission !== "granted") {
      updateNotifStatus();
      return;
    }
    fireOsNotification(
      "🟢 COMPRA — Teste Trade AI",
      "Entrada: 128.350 | Stop: 127.900 | TP1: 129.200 | Score: +5",
      "trade-ai-test-signal",
      true   // sticky — fica até você dispensar
    );
    showNotifHelp(true);
  }

  function sendTradeNotification(data) {
    if (!notifEnabled || Notification.permission !== "granted") return false;
    var acao = (data.acao || "").toUpperCase();
    if (acao === "NEUTRO" || !acao) return false;

    var emoji = acao === "COMPRA" ? "🟢" : "🔴";
    var sym   = data.tv_symbol || data.yf_symbol || "—";
    var title = emoji + " " + acao + " — " + sym;
    var body  = [
      "Entrada: " + (data.entrada != null ? fmtPreco(data.entrada) : "—"),
      "Stop: "    + (data.stop    != null ? fmtPreco(data.stop)    : "—"),
      "TP1: "     + (data.tp1     != null ? fmtPreco(data.tp1)     : "—"),
      "Score: "   + (data.score   != null ? (data.score > 0 ? "+" + data.score : data.score) : "—"),
    ].join(" | ");

    // requireInteraction: true → notificação fica na tela até o usuário dispensar
    return fireOsNotification(title, body, "trade-signal-" + acao, true);
  }

  // ---- In-page toast notification (sempre visível, sem depender do SO) ----
  function showInPageToast(acao, data) {
    // Garante container
    var container = document.getElementById("trade-toast-container");
    if (!container) {
      container = document.createElement("div");
      container.id = "trade-toast-container";
      document.body.appendChild(container);
    }

    var isCompra = acao === "COMPRA";
    var emoji = isCompra ? "🟢" : "🔴";
    var sym = data.tv_symbol || data.yf_symbol || "";
    var entradaStr = data.entrada != null ? fmtPreco(data.entrada) : "—";
    var stopStr    = data.stop    != null ? fmtPreco(data.stop)    : "—";
    var tp1Str     = data.tp1     != null ? fmtPreco(data.tp1)     : "—";
    var scoreStr   = data.score   != null ? (data.score > 0 ? "+" + data.score : data.score) : "—";

    var toast = document.createElement("div");
    toast.className = "trade-toast trade-toast--" + acao.toLowerCase();
    toast.innerHTML =
      '<div class="trade-toast__icon">' + emoji + '</div>' +
      '<div class="trade-toast__body">' +
        '<div class="trade-toast__title">' + acao + (sym ? " — " + sym : "") + '</div>' +
        '<div class="trade-toast__details">' +
          "Entrada: " + entradaStr + " &nbsp;|&nbsp; Stop: " + stopStr + "<br>" +
          "TP1: " + tp1Str + " &nbsp;|&nbsp; Score: " + scoreStr +
        '</div>' +
      '</div>' +
      '<button class="trade-toast__close" title="Fechar">✕</button>';

    // Fechar ao clicar
    toast.addEventListener("click", function () { removeToast(toast); });

    container.appendChild(toast);

    // Auto-remove após 12 segundos
    var autoTimer = setTimeout(function () { removeToast(toast); }, 12000);
    toast._autoTimer = autoTimer;
  }

  function removeToast(toast) {
    if (toast._autoTimer) clearTimeout(toast._autoTimer);
    toast.classList.add("trade-toast--hiding");
    setTimeout(function () {
      if (toast.parentNode) toast.parentNode.removeChild(toast);
    }, 450);
  }

  // ---- Alerta visual: pisca o título da aba ----
  var _titleTimer   = null;
  var _originalTitle = document.title;

  function flashPageTitle(acao, symbol) {
    if (_titleTimer) { clearInterval(_titleTimer); _titleTimer = null; }
    var emoji  = acao === "COMPRA" ? "🟢" : "🔴";
    var alert  = emoji + " " + acao + " " + (symbol || "") + " — Trade AI";
    var toggle = false;
    var count  = 0;
    _titleTimer = setInterval(function () {
      document.title = toggle ? alert : _originalTitle;
      toggle = !toggle;
      count++;
      if (count >= 20) {           // pisca 10x (20 trocas) e para
        clearInterval(_titleTimer);
        _titleTimer = null;
        document.title = _originalTitle;
      }
    }, 700);
  }

  // Restaura título ao mudar de aba ou fechar monitor
  document.addEventListener("visibilitychange", function () {
    if (!document.hidden && _titleTimer) {
      clearInterval(_titleTimer);
      _titleTimer = null;
      document.title = _originalTitle;
    }
  });

  // ---- Alerta sonoro via Web Audio API ----
  function playAlertBeep(acao) {
    try {
      var ctx  = new (window.AudioContext || window.webkitAudioContext)();
      // COMPRA: dois beeps subindo; VENDA: dois beeps descendo
      var freqs = acao === "COMPRA" ? [440, 660] : [660, 440];
      freqs.forEach(function (freq, i) {
        var osc  = ctx.createOscillator();
        var gain = ctx.createGain();
        osc.connect(gain);
        gain.connect(ctx.destination);
        osc.type      = "sine";
        osc.frequency.value = freq;
        gain.gain.setValueAtTime(0.35, ctx.currentTime + i * 0.22);
        gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + i * 0.22 + 0.35);
        osc.start(ctx.currentTime + i * 0.22);
        osc.stop(ctx.currentTime  + i * 0.22 + 0.35);
      });
      setTimeout(function () { ctx.close(); }, 1500);
    } catch (e) {
      // Web Audio não disponível — ignora silenciosamente
    }
  }

  document.getElementById("mon-notif-btn")?.addEventListener("click", requestNotifications);
  document.getElementById("mon-notif-test-btn")?.addEventListener("click", sendTestNotification);
  document.getElementById("mon-notif-help-close")?.addEventListener("click", function () { showNotifHelp(false); });
  updateNotifStatus();

  // ---- Monitor state ----
  var monitorActive    = false;
  var monitorTimer     = null;
  var monitorAlerts    = [];

  function monSetStatus(text) {
    var el = document.getElementById("mon-status-text");
    if (el) el.textContent = text;
  }

  function monSetLoading(loading) {
    var loadEl  = document.getElementById("mon-loading");
    var errEl   = document.getElementById("mon-error");
    var sigEl   = document.getElementById("mon-signal-content");
    var idleEl  = document.getElementById("mon-signal-idle");
    if (loadEl) loadEl.hidden = !loading;
    if (loading) {
      if (errEl) errEl.hidden = true;
      if (idleEl) idleEl.hidden = true;
    }
  }

  function monShowError(msg) {
    var loadEl    = document.getElementById("mon-loading");
    var errEl     = document.getElementById("mon-error");
    var errText   = document.getElementById("mon-error-text");
    var errHint   = document.getElementById("mon-error-hint");
    var idleEl    = document.getElementById("mon-signal-idle");
    var sigEl     = document.getElementById("mon-signal-content");
    if (loadEl) loadEl.hidden = true;
    if (idleEl) idleEl.hidden = true;
    if (sigEl)  sigEl.hidden  = true;
    if (errEl)  errEl.hidden  = false;

    var displayMsg = msg || "Erro desconhecido.";
    var hint       = "";

    // Detecta causas comuns e sugere solução
    if (/yfinance|No module named/i.test(displayMsg)) {
      displayMsg = "Biblioteca yfinance não instalada.";
      hint = "Execute no terminal: pip install yfinance pandas numpy requests";
    } else if (/ConnectionError|timeout|HTTPSConnection|RemoteDisconnected/i.test(displayMsg)) {
      displayMsg = "Sem conexão com Yahoo Finance.";
      hint = "Verifique sua conexão com a internet e tente novamente.";
    } else if (/empty|no data|Dados não disponíveis/i.test(displayMsg)) {
      displayMsg = "Dados não disponíveis para este instrumento/intervalo.";
      hint = "Tente outro intervalo de tempo ou aguarde — Yahoo Finance pode ter delay.";
    }

    if (errText) errText.textContent = displayMsg;
    if (errHint) {
      errHint.textContent = hint;
      errHint.style.display = hint ? "" : "none";
    }
  }

  function monShowSignal(data) {
    var loadEl  = document.getElementById("mon-loading");
    var errEl   = document.getElementById("mon-error");
    var sigEl   = document.getElementById("mon-signal-content");
    var idleEl  = document.getElementById("mon-signal-idle");
    if (loadEl) loadEl.hidden = true;
    if (errEl)  errEl.hidden  = true;
    if (idleEl) idleEl.hidden = true;
    if (sigEl)  sigEl.hidden  = false;

    function setText(id, val) {
      var el = document.getElementById(id);
      if (el) el.textContent = (val != null && val !== "") ? String(val) : "—";
    }

    var acao = (data.acao || "NEUTRO").toUpperCase();

    // Badge de sinal
    var badge = document.getElementById("mon-signal-badge");
    if (badge) {
      badge.textContent = acao;
      badge.className = "mon-signal-badge";
      if (acao === "COMPRA") badge.classList.add("mon-signal-badge--buy");
      else if (acao === "VENDA") badge.classList.add("mon-signal-badge--sell");
      else badge.classList.add("mon-signal-badge--neutral");
    }

    setText("mon-score",       data.score != null ? (data.score > 0 ? "+" + data.score : data.score) : "—");
    setText("mon-forca",       data.forca  || "—");
    setText("mon-preco-atual", data.preco_atual != null ? fmtPreco(data.preco_atual) : "—");
    setText("mon-entrada",     data.entrada    != null ? fmtPreco(data.entrada) : "—");
    setText("mon-stop",        data.stop       != null ? fmtPreco(data.stop) : "—");
    setText("mon-tp1",         data.tp1        != null ? fmtPreco(data.tp1) : "—");
    setText("mon-tp2",         data.tp2        != null ? fmtPreco(data.tp2) : "—");
    setText("mon-tp3",         data.tp3        != null ? fmtPreco(data.tp3) : "—");
    setText("mon-rsi",         data.rsi       != null ? data.rsi + " / 100" : "—");
    setText("mon-adx",         data.adx       != null ? data.adx + (data.adx_filtered ? " ⚠️" : "") : "—");
    setText("mon-vwap",        data.vwap      != null ? fmtPreco(data.vwap) : "—");
    setText("mon-atr",         data.atr       != null ? fmtPreco(data.atr) : "—");
    setText("mon-ema9",        data.ema9      != null ? fmtPreco(data.ema9) : "—");
    setText("mon-ema21",       data.ema21     != null ? fmtPreco(data.ema21) : "—");
    setText("mon-ema50",       data.ema50     != null ? fmtPreco(data.ema50) : "—");
    setText("mon-macd-hist",   data.macd_hist != null ? data.macd_hist : "—");
    setText("mon-suporte",     data.suporte   != null ? fmtPreco(data.suporte) : "—");
    setText("mon-resistencia", data.resistencia != null ? fmtPreco(data.resistencia) : "—");

    // +DI / -DI
    if (data.plus_di != null && data.minus_di != null) {
      setText("mon-di", "+" + data.plus_di + " / −" + data.minus_di);
    } else {
      setText("mon-di", "—");
    }

    // Higher timeframe
    var htfEl = document.getElementById("mon-htf");
    if (htfEl) {
      if (data.htf_trend === "alta") {
        htfEl.textContent = "🟢 ALTA";
        htfEl.style.color = "var(--success)";
      } else if (data.htf_trend === "baixa") {
        htfEl.textContent = "🔴 BAIXA";
        htfEl.style.color = "var(--danger)";
      } else {
        htfEl.textContent = "—";
        htfEl.style.color = "";
      }
    }

    // Padrões de candle
    var candleList = document.getElementById("mon-candles-list");
    if (candleList) {
      var patterns = Array.isArray(data.candle_patterns) ? data.candle_patterns : [];
      if (patterns.length === 0) {
        candleList.innerHTML = "<li class='muted'>Nenhum padrão identificado no candle atual.</li>";
      } else {
        candleList.innerHTML = "";
        patterns.forEach(function (p) {
          var li = document.createElement("li");
          li.textContent = p;
          candleList.appendChild(li);
        });
      }
    }

    // Lista de confluências — com colorização por score anotado
    var sinaisList = document.getElementById("mon-sinais-list");
    if (sinaisList) {
      sinaisList.innerHTML = "";
      var sinais = Array.isArray(data.sinais) ? data.sinais : [];
      if (sinais.length === 0) {
        sinaisList.innerHTML = "<li class='muted'>Nenhuma confluência detectada.</li>";
      } else {
        sinais.forEach(function (s) {
          var li = document.createElement("li");
          li.textContent = s;
          // Coloriza baseado na anotação de score ou em palavras-chave
          var hasBull = /\[\+\d/.test(s) || /🟢/.test(s);
          var hasBear = /\[-\d/.test(s)  || /🔴/.test(s);
          var isFilter = /\[filtro\]/.test(s) || /ADX/.test(s) || /⚠️/.test(s);
          if (hasBull && !hasBear)   li.style.color = "var(--success)";
          else if (hasBear && !hasBull) li.style.color = "var(--danger)";
          else if (isFilter)         li.style.color = "var(--warning)";
          else                       li.style.color = "var(--text-muted)";
          sinaisList.appendChild(li);
        });
      }
    }

    // Timestamp
    var ts = data.timestamp ? new Date(data.timestamp).toLocaleTimeString("pt-BR") : "—";
    setText("mon-last-update", ts);
    setText("mon-yf-symbol",   data.yf_symbol || "—");
  }

  function fmtPreco(v) {
    if (v == null) return "—";
    var n = parseFloat(v);
    if (isNaN(n)) return String(v);
    // Detecta se é preço grande (ex: Ibovespa ~128000) ou pequeno (ex: câmbio ~5.8)
    if (n >= 1000) return n.toLocaleString("pt-BR", { minimumFractionDigits: 0, maximumFractionDigits: 0 });
    return n.toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 3 });
  }

  function fetchSignals() {
    var tvSym  = getTvSymbol();
    var ivl    = getTvInterval();
    var url    = (cfg.monitorSignalsUrl || "/api/monitor/signals")
                 + "?tv_symbol=" + encodeURIComponent(tvSym)
                 + "&interval=" + encodeURIComponent(ivl);

    monSetLoading(true);
    monSetStatus("Buscando dados para " + tvSym + "…");

    fetch(url, { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        monSetLoading(false);
        if (!data.ok) {
          monShowError(data.error || "Erro desconhecido.");
          monSetStatus("⚠ " + (data.error || "Erro ao buscar sinal."));
          return;
        }
        monShowSignal(data);
        var now = new Date().toLocaleTimeString("pt-BR");
        var adxNote = data.adx_filtered ? " · ADX fraco (range)" : "";
        var htfNote = data.htf_trend ? " · 1h: " + data.htf_trend : "";
        monSetStatus("✓ Atualizado às " + now + " · " + tvSym + adxNote + htfNote);
        // Notificação: dispara quando o sinal MUDA para COMPRA ou VENDA.
        // NEUTRO reseta o rastreador → próximo COMPRA/VENDA dispara novo alerta.
        var acao = (data.acao || "").toUpperCase();
        var isNotified = false;
        if (acao === "NEUTRO" || !acao) {
          lastNotifiedAcao = null;
        } else if ((acao === "COMPRA" || acao === "VENDA") && acao !== lastNotifiedAcao) {
          lastNotifiedAcao = acao;
          isNotified = true;
          showInPageToast(acao, data);
          sendTradeNotification(data);
          flashPageTitle(acao, data.tv_symbol || "");
          playAlertBeep(acao);
        }
        addMonitorAlert(data, isNotified);
      })
      .catch(function (err) {
        monSetLoading(false);
        monShowError("Falha de rede: " + (err.message || "erro desconhecido."));
        monSetStatus("⚠ Falha de rede.");
      });
  }

  function addMonitorAlert(data, notified) {
    var acao = (data.acao || "NEUTRO").toUpperCase();
    if (acao === "NEUTRO") return;

    var now = new Date().toLocaleTimeString("pt-BR");
    var alert = {
      hora:      now,
      simbolo:   data.tv_symbol || "—",
      sinal:     acao,
      score:     data.score,
      preco:     data.preco_atual,
      entrada:   data.entrada,
      stop:      data.stop,
      tp1:       data.tp1,
      notified:  !!notified,   // flag: este alerta gerou notificação
    };
    monitorAlerts.unshift(alert);
    renderMonitorAlerts();
  }

  function renderMonitorAlerts() {
    var empty = document.getElementById("mon-alerts-empty");
    var wrap  = document.getElementById("mon-alerts-wrap");
    var tbody = document.getElementById("mon-alerts-body");
    if (!tbody) return;

    if (monitorAlerts.length === 0) {
      if (empty) empty.hidden = false;
      if (wrap)  wrap.hidden  = true;
      return;
    }

    if (empty) empty.hidden = true;
    if (wrap)  wrap.hidden  = false;

    tbody.innerHTML = "";
    monitorAlerts.slice(0, 50).forEach(function (a) {
      var tr = document.createElement("tr");
      var badgeClass = a.sinal === "COMPRA" ? "badge ok" : "badge no";

      // Linha destacada para alertas que geraram notificação
      if (a.notified) {
        tr.className = "mon-alert-row--notified";
      }

      // Coluna de hora: se notificou, adiciona ícone 🔔
      var horaCell = escapeHtml(a.hora) + (a.notified ? " <span class='mon-alert-notif-badge' title='Gerou notificação'>🔔</span>" : "");

      tr.innerHTML =
        "<td>" + horaCell + "</td>" +
        "<td>" + escapeHtml(a.simbolo) + "</td>" +
        "<td><span class='" + badgeClass + "'>" + escapeHtml(a.sinal) + "</span></td>" +
        "<td>" + (a.score != null ? (a.score > 0 ? "+" + a.score : a.score) : "—") + "</td>" +
        "<td>" + (a.preco   != null ? fmtPreco(a.preco)   : "—") + "</td>" +
        "<td>" + (a.entrada != null ? fmtPreco(a.entrada) : "—") + "</td>" +
        "<td>" + (a.stop    != null ? fmtPreco(a.stop)    : "—") + "</td>" +
        "<td>" + (a.tp1     != null ? fmtPreco(a.tp1)     : "—") + "</td>";
      tbody.appendChild(tr);
    });
  }

  function startMonitor() {
    if (monitorActive) stopMonitor();

    // Solicita permissão de notificação automaticamente ao iniciar
    if ("Notification" in window && Notification.permission === "default") {
      Notification.requestPermission().then(function (perm) {
        notifEnabled = perm === "granted";
        updateNotifStatus();
      });
    } else if (Notification.permission === "granted") {
      notifEnabled = true;
      updateNotifStatus();
    }

    // Reinicia o widget TradingView com o novo símbolo
    initTradingViewWidget();

    // Atualiza label do gráfico
    var tvSym  = getTvSymbol();
    var label  = document.getElementById("mon-chart-label");
    var selOpt = document.getElementById("mon-instrument");
    var instrLabel = selOpt ? (selOpt.options[selOpt.selectedIndex]?.text || tvSym) : tvSym;
    if (label) label.textContent = "Gráfico — " + instrLabel;

    monitorActive = true;
    fetchSignals();     // imediato

    var refreshSec = parseInt(
      (document.getElementById("mon-refresh") || {}).value || "300", 10
    );
    if (refreshSec > 0) {
      monitorTimer = setInterval(fetchSignals, refreshSec * 1000);
    }

    var btn = document.getElementById("mon-start-btn");
    if (btn) {
      btn.textContent = "⏹ Parar";
      btn.classList.add("btn--active");
    }

    // Busca notícias ao iniciar
    fetchNews();
  }

  function stopMonitor() {
    monitorActive    = false;
    lastNotifiedAcao = null;   // reseta para notificar novamente ao reiniciar
    if (monitorTimer) {
      clearInterval(monitorTimer);
      monitorTimer = null;
    }
    var btn = document.getElementById("mon-start-btn");
    if (btn) {
      btn.textContent = "▶ Iniciar";
      btn.classList.remove("btn--active");
    }
    monSetStatus("Monitoramento pausado.");
  }

  document.getElementById("mon-start-btn")?.addEventListener("click", function () {
    if (monitorActive) {
      stopMonitor();
    } else {
      startMonitor();
    }
  });

  document.getElementById("mon-clear-alerts")?.addEventListener("click", function () {
    monitorAlerts = [];
    renderMonitorAlerts();
  });

  // ---- Notícias Finnhub ----
  function fetchNews() {
    var newsUrl = (cfg.marketNewsUrl || "/api/market/news") + "?category=general";
    fetch(newsUrl, { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var statusEl = document.getElementById("mon-finnhub-status");
        if (statusEl) {
          if (data.finnhub_configured) {
            statusEl.textContent = "Finnhub: ✓ ativo";
            statusEl.className = "mon-finnhub-badge mon-finnhub-badge--ok";
          } else {
            statusEl.textContent = "Finnhub: sem chave de API";
            statusEl.className = "mon-finnhub-badge mon-finnhub-badge--warn";
          }
        }
        renderNews(data.items || [], data.error);
      })
      .catch(function () {});
  }

  function renderNews(items, error) {
    var container = document.getElementById("mon-news-list");
    if (!container) return;

    if (!items || items.length === 0) {
      container.innerHTML =
        '<p class="muted" style="padding:1rem">' +
        (error
          ? "⚠ " + escapeHtml(error)
          : "Nenhuma notícia disponível. Configure <code>FINNHUB_API_KEY</code> no <code>.env</code> para ativar.") +
        "</p>";
      return;
    }

    var html = "";
    items.forEach(function (item) {
      var ts    = item.datetime ? new Date(item.datetime * 1000).toLocaleString("pt-BR") : "";
      var title = item.headline || item.title || "Sem título";
      var src   = item.source || "";
      var url   = item.url || "#";
      html +=
        '<div class="mon-news-item">' +
          '<div class="mon-news-meta">' +
            '<span class="mon-news-source">' + escapeHtml(src) + "</span>" +
            '<span class="mon-news-time">' + escapeHtml(ts) + "</span>" +
          "</div>" +
          '<a href="' + escapeHtml(url) + '" target="_blank" rel="noopener" class="mon-news-title">' +
            escapeHtml(title) +
          "</a>" +
        "</div>";
    });
    container.innerHTML = html;
  }

  document.getElementById("mon-news-refresh-btn")?.addEventListener("click", fetchNews);

})();


// ============================================================
// MONITOR MT5 AO VIVO
// ============================================================
(function () {
  "use strict";

  // ---- State ----
  var chart        = null;
  var candleSeries = null;
  var ema9S        = null;
  var ema21S       = null;
  var ema50S       = null;
  var vwapS        = null;
  var bbUpS        = null;
  var bbLoS        = null;
  var priceLines   = [];  // keep refs for cleanup

  var active     = false;
  var timer      = null;
  var tickTimer  = null;   // timer de tick (3s) para atualização em tempo real
  var alerts     = [];
  var lastSignal = null;  // track signal changes for audio
  var lastCandle = null;  // último candle completo — atualizado pelo tick timer

  // Audio context for beep
  var audioCtx = null;
  function playBeep(freq, dur, vol) {
    try {
      if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      var osc  = audioCtx.createOscillator();
      var gain = audioCtx.createGain();
      osc.connect(gain);
      gain.connect(audioCtx.destination);
      osc.frequency.value = freq || 880;
      gain.gain.value     = vol  || 0.3;
      osc.start();
      osc.stop(audioCtx.currentTime + (dur || 0.25));
    } catch (e) {}
  }
  function beepBuy()  { playBeep(880, 0.2, 0.3); setTimeout(function(){ playBeep(1100, 0.3, 0.3); }, 250); }
  function beepSell() { playBeep(440, 0.2, 0.3); setTimeout(function(){ playBeep(330,  0.3, 0.3); }, 250); }

  // ---- Helpers ----
  function el(id) { return document.getElementById(id); }
  function fmt(v) { return v != null ? Number(v).toLocaleString("pt-BR") : "-"; }
  function fmtPts(v) { return v != null ? Number(v).toLocaleString("pt-BR", {minimumFractionDigits:0, maximumFractionDigits:0}) : "-"; }

  function getSym()  { var s = el("mt5-instrument"); return s ? s.value : "BMFBOVESPA:WIN1!"; }
  function getIvl()  { var s = el("mt5-interval");   return s ? s.value : "15"; }
  function getRefr() { var s = el("mt5-refresh");    return s ? parseInt(s.value, 10) : 60; }

  function setStatus(msg) { var e = el("mt5-status-text"); if (e) e.textContent = msg; }

  function showLoading(on) {
    var ld = el("mt5-loading");
    var er = el("mt5-error");
    var ct = el("mt5-signal-content");
    var id = el("mt5-signal-idle");
    if (ld) ld.hidden = !on;
    if (on) {
      if (er) er.hidden = true;
    }
  }

  function showError(msg) {
    var er = el("mt5-error"); var et = el("mt5-error-text");
    var ld = el("mt5-loading"); var ct = el("mt5-signal-content");
    if (ld) ld.hidden = true;
    if (ct) ct.hidden = true;
    if (er) er.hidden = false;
    if (et) et.textContent = msg || "Erro ao buscar dados.";
  }

  // ---- Chart init ----
  function initChart() {
    var container = el("mt5-chart");
    if (!container) return;

    // Remove placeholder
    var ph = el("mt5-chart-placeholder");
    if (ph) ph.style.display = "none";

    // Destroy existing
    if (chart) { try { chart.remove(); } catch(e){} chart = null; }

    if (typeof LightweightCharts === "undefined") {
      container.innerHTML = '<p style="padding:2rem;color:var(--danger);text-align:center">LightweightCharts nao carregou. Verifique conexao.</p>';
      return;
    }

    chart = LightweightCharts.createChart(container, {
      width:  container.clientWidth,
      height: 480,
      layout: { background: { type: "solid", color: "#0f1923" }, textColor: "#c8d4e0" },
      grid:   { vertLines: { color: "#1e2a35" }, horzLines: { color: "#1e2a35" } },
      crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
      rightPriceScale: { borderColor: "#2a3a4a" },
      timeScale: { borderColor: "#2a3a4a", timeVisible: true, secondsVisible: false, rightOffset: 8 },
    });

    candleSeries = chart.addCandlestickSeries({
      upColor: "#26a69a", downColor: "#ef5350",
      borderVisible: false,
      wickUpColor: "#26a69a", wickDownColor: "#ef5350",
    });

    bbUpS  = chart.addLineSeries({ color: "#455a64", lineWidth: 1, lineStyle: 2, priceLineVisible: false, lastValueVisible: false });
    bbLoS  = chart.addLineSeries({ color: "#455a64", lineWidth: 1, lineStyle: 2, priceLineVisible: false, lastValueVisible: false });
    ema50S = chart.addLineSeries({ color: "#9c27b0", lineWidth: 1, priceLineVisible: false, lastValueVisible: false, title: "EMA50" });
    ema21S = chart.addLineSeries({ color: "#2196F3", lineWidth: 1, priceLineVisible: false, lastValueVisible: false, title: "EMA21" });
    ema9S  = chart.addLineSeries({ color: "#f0c420", lineWidth: 2, priceLineVisible: false, lastValueVisible: false, title: "EMA9"  });
    vwapS  = chart.addLineSeries({ color: "#ff9800", lineWidth: 2, lineStyle: 1, priceLineVisible: false, lastValueVisible: false, title: "VWAP" });

    // Resize
    var ro = new ResizeObserver(function () {
      if (chart && container) chart.applyOptions({ width: container.clientWidth });
    });
    ro.observe(container);
  }

  // ---- Linhas de trade manual no grafico ----
  // Marca o alert mais recente da mesma direção como bloqueado pela IA e re-renderiza
  window._markAlertIABlocked = function (acao, motivo) {
    for (var i = 0; i < alerts.length; i++) {
      if (alerts[i].sinal === acao && !alerts[i].ia_blocked) {
        alerts[i].ia_blocked = true;
        alerts[i].ia_motivo  = motivo || "";
        break;
      }
    }
    renderAlerts();
    // Mostra banner no painel de sinal
    var banner = document.getElementById("mt5-ia-block-banner");
    if (banner) {
      banner.textContent = "🚫 IA bloqueou — " + (motivo ? String(motivo).slice(0, 120) : "trade não autorizado");
      banner.hidden = false;
    }
  };

  window._drawManualTradeLines = function (acao, execPrice, sl, tp1) {
    if (!candleSeries) return;
    clearPriceLines();
    var isBuy    = acao === "COMPRA";
    var entColor = isBuy ? "#26a69a" : "#ef5350";
    var stpColor = isBuy ? "#ef5350" : "#26a69a";
    var tpColor  = "#43a047";

    function addLine(price, color, title, style) {
      if (price == null || isNaN(price)) return;
      try {
        var pl = candleSeries.createPriceLine({
          price: parseFloat(price), color: color, lineWidth: 2,
          lineStyle: style || 0, axisLabelVisible: true, title: title
        });
        priceLines.push(pl);
      } catch(e) {}
    }

    addLine(execPrice, entColor, "Entrada", 0);
    addLine(sl,        stpColor, "Stop",    2);
    addLine(tp1,       tpColor,  "TP1",     2);
  };

  // ---- Clear signal price lines ----
  function clearPriceLines() {
    if (!candleSeries) return;
    priceLines.forEach(function (pl) { try { candleSeries.removePriceLine(pl); } catch(e){} });
    priceLines = [];
  }

  // ---- Tick em tempo real (atualiza só o último candle, a cada 1s) ----
  function fetchTick() {
    if (!active || !candleSeries || !lastCandle) return;
    var sym = getSym();
    fetch("/api/monitor/tick?tv_symbol=" + encodeURIComponent(sym))
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d.ok || !d.price) return;
        var price = d.price;
        // Atualiza close do último candle; expande high/low se necessário
        var updated = {
          time:  lastCandle.time,
          open:  lastCandle.open,
          high:  Math.max(lastCandle.high, price),
          low:   Math.min(lastCandle.low,  price),
          close: price,
        };
        lastCandle = updated;
        try { candleSeries.update(updated); } catch (e) {}
        // Atualiza label de preço no painel de sinal
        var precoEl = el("mt5-preco");
        if (precoEl) precoEl.textContent = price.toLocaleString("pt-BR");
      })
      .catch(function () {});
  }

  function startTickTimer() {
    if (tickTimer) clearInterval(tickTimer);
    tickTimer = setInterval(fetchTick, 1000);
  }

  function stopTickTimer() {
    if (tickTimer) { clearInterval(tickTimer); tickTimer = null; }
  }

  // ---- Render chart data ----
  // Verifica se o último candle é do dia atual (mercado aberto hoje)
  function isSignalFromToday(candles) {
    if (!candles || candles.length === 0) return false;
    var lastTs   = candles[candles.length - 1].time;  // unix timestamp (segundos)
    var lastDate = new Date(lastTs * 1000);
    var today    = new Date();
    return lastDate.getFullYear() === today.getFullYear() &&
           lastDate.getMonth()    === today.getMonth()    &&
           lastDate.getDate()     === today.getDate();
  }

  // Linhas do trade ativo — mantidas durante MANAGE mode
  var _activeTradeLine = null;  // { entry, sl, tp1, acao }

  // Expõe setter para que o IIFE de auto-trade possa setar _activeTradeLine
  window._setActiveTradeLine = function (tl) { _activeTradeLine = tl; };

  // Expõe função para redesenhar imediatamente as linhas do trade ativo
  window._redrawTradeLines = function () {
    clearPriceLines();
    if (!_activeTradeLine || !candleSeries) return;
    var tl = _activeTradeLine;
    var isBuyT = tl.acao === "COMPRA";
    var entC = isBuyT ? "#26a69a" : "#ef5350";
    var stpC = isBuyT ? "#ef5350" : "#26a69a";
    var tpC  = isBuyT ? "#43a047" : "#e53935";
    function addTLW(price, color, title, style) {
      if (price == null) return;
      var pl = candleSeries.createPriceLine({ price: price, color: color, lineWidth: 2, lineStyle: style || 0, axisLabelVisible: true, title: title });
      priceLines.push(pl);
    }
    addTLW(tl.entry, entC, "Entrada", 0);
    addTLW(tl.sl,    stpC, "Stop",    2);
    addTLW(tl.tp1,   tpC,  "TP1",     2);
  };

  function renderChart(data, skipSignalLines) {
    if (!chart) initChart();
    if (!candleSeries) return;

    var candles = data.candles || [];
    candleSeries.setData(candles);

    // Guarda último candle para o tick timer atualizar em tempo real
    if (candles.length > 0) lastCandle = Object.assign({}, candles[candles.length - 1]);

    if (ema9S)  ema9S.setData(data.ema9   || []);
    if (ema21S) ema21S.setData(data.ema21 || []);
    if (ema50S) ema50S.setData(data.ema50 || []);
    if (vwapS)  vwapS.setData(data.vwap   || []);
    if (bbUpS)  bbUpS.setData(data.bb_upper || []);
    if (bbLoS)  bbLoS.setData(data.bb_lower || []);

    clearPriceLines();

    // Em MANAGE mode: redesenha linhas do trade ativo (não do novo sinal)
    if (skipSignalLines) {
      if (_activeTradeLine) {
        var tl = _activeTradeLine;
        var isBuyT = tl.acao === "COMPRA";
        var entC = isBuyT ? "#26a69a" : "#ef5350";
        var stpC = isBuyT ? "#ef5350" : "#26a69a";
        var tpC  = isBuyT ? "#43a047" : "#e53935";
        function addTL(price, color, title, style) {
          if (price == null) return;
          var pl = candleSeries.createPriceLine({ price: price, color: color, lineWidth: 2, lineStyle: style || 0, axisLabelVisible: true, title: title });
          priceLines.push(pl);
        }
        addTL(tl.entry, entC, "Entrada", 0);
        addTL(tl.sl,    stpC, "Stop",    2);
        addTL(tl.tp1,   tpC,  "TP1",     2);
      }
      return;
    }

    // Só desenha linhas de preço se o último candle for do dia atual
    var sig = data.signal;
    if (sig && sig.acao !== "NEUTRO" && isSignalFromToday(candles)) {
      var isBuy = sig.acao === "COMPRA";
      var entColor = isBuy ? "#26a69a" : "#ef5350";
      var stpColor = isBuy ? "#ef5350" : "#26a69a";
      var tpColor  = isBuy ? "#43a047" : "#e53935";

      function addLine(price, color, title, style) {
        if (price == null) return;
        var pl = candleSeries.createPriceLine({ price: price, color: color, lineWidth: 2, lineStyle: style || 0, axisLabelVisible: true, title: title });
        priceLines.push(pl);
      }

      addLine(sig.entrada, entColor, "Entrada", 0);
      addLine(sig.stop,    stpColor, "Stop",    2);
      addLine(sig.tp1,     tpColor,  "TP1",     2);
      addLine(sig.tp2,     tpColor,  "TP2",     2);
    }

    // scrollToRealTime removido — rightOffset mantém espaço fixo à direita
  }

  // ---- Update signal panel ----
  function renderSignal(sig, fonte) {
    var idle    = el("mt5-signal-idle");
    var content = el("mt5-signal-content");
    var loading = el("mt5-loading");
    var errDiv  = el("mt5-error");

    if (idle)    idle.hidden    = true;
    if (loading) loading.hidden = true;
    // Esconde banner IA quando novo sinal chega
    var iaBanner = el("mt5-ia-block-banner");
    if (iaBanner) iaBanner.hidden = true;
    if (errDiv)  errDiv.hidden  = true;
    if (content) content.hidden = false;

    var acao = sig.acao || "NEUTRO";
    var badge = el("mt5-signal-badge");
    if (badge) {
      badge.textContent = acao;
      badge.className = "mon-signal-badge mon-signal-badge--" + acao.toLowerCase();
    }

    function setTxt(id, v) { var e = el(id); if (e) e.textContent = v; }

    setTxt("mt5-score",   sig.score != null ? sig.score : "-");
    setTxt("mt5-forca",   sig.forca  || "-");
    setTxt("mt5-preco",   fmtPts(sig.preco_atual));
    setTxt("mt5-entrada", sig.entrada ? fmtPts(sig.entrada) : "-");
    setTxt("mt5-stop",    sig.stop    ? fmtPts(sig.stop)    : "-");
    setTxt("mt5-tp1",     sig.tp1     ? fmtPts(sig.tp1)     : "-");
    setTxt("mt5-tp2",     sig.tp2     ? fmtPts(sig.tp2)     : "-");
    setTxt("mt5-tp3",     sig.tp3     ? fmtPts(sig.tp3)     : "-");

    setTxt("mt5-rsi",     sig.rsi     != null ? sig.rsi.toFixed(1) : "-");
    setTxt("mt5-adx",     sig.adx     != null ? sig.adx.toFixed(1) : "-");
    setTxt("mt5-vwap",    sig.vwap    ? fmtPts(sig.vwap)  : "-");
    setTxt("mt5-atr",     sig.atr     ? fmtPts(sig.atr)   : "-");
    setTxt("mt5-ema9",    sig.ema9    ? fmtPts(sig.ema9)  : "-");
    setTxt("mt5-ema21",   sig.ema21   ? fmtPts(sig.ema21) : "-");
    setTxt("mt5-ema50",   sig.ema50   ? fmtPts(sig.ema50) : "-");
    setTxt("mt5-macd",    sig.macd_hist != null ? sig.macd_hist.toFixed(1) : "-");
    setTxt("mt5-htf",     sig.htf_trend || "N/A");
    setTxt("mt5-suporte", sig.suporte    ? fmtPts(sig.suporte)    : "-");
    setTxt("mt5-resist",  sig.resistencia ? fmtPts(sig.resistencia) : "-");

    // DI
    var diEl = el("mt5-di");
    if (diEl) diEl.textContent = (sig.plus_di != null && sig.minus_di != null)
      ? "+" + sig.plus_di.toFixed(1) + " / -" + sig.minus_di.toFixed(1) : "-";

    // Candle patterns
    var candleList = el("mt5-candles-list");
    var candleWrap = el("mt5-candles-wrap");
    if (candleList) {
      var patterns = sig.candle_patterns || [];
      candleList.innerHTML = patterns.map(function(p){ return "<li>" + p + "</li>"; }).join("");
      if (candleWrap) candleWrap.hidden = patterns.length === 0;
    }

    // Confluences
    var sinaisList = el("mt5-sinais-list");
    if (sinaisList) {
      var sinais = sig.sinais || [];
      sinaisList.innerHTML = sinais.map(function(s){ return "<li>" + s + "</li>"; }).join("");
    }

    // Fonte badge
    var fonteBadge = el("mt5-fonte-badge");
    if (fonteBadge) {
      fonteBadge.style.display = "";
      if (fonte === "mt5") {
        fonteBadge.textContent = "MT5 | Tempo Real";
        fonteBadge.style.background = "var(--success, #1a7a3a)";
        fonteBadge.style.color = "#fff";
        fonteBadge.style.border = "none";
        fonteBadge.style.padding = "2px 8px";
        fonteBadge.style.borderRadius = "4px";
      } else {
        fonteBadge.textContent = "Yahoo Finance (delay)";
        fonteBadge.style.background = "";
        fonteBadge.style.color = "";
      }
    }

    // Last update
    var lu = el("mt5-last-update");
    if (lu) lu.textContent = "Atualizado: " + new Date().toLocaleTimeString("pt-BR");
  }

  // ---- Audio alert on signal change ----
  function checkAudioAlert(sig) {
    if (!sig) return;
    var acao = sig.acao;
    if (acao === "NEUTRO") return;
    if (lastSignal && lastSignal === acao) return;
    lastSignal = acao;
    if (acao === "COMPRA") beepBuy();
    else if (acao === "VENDA") beepSell();
  }

  // ---- Tentativas de Auto-Trade (histórico simplificado) ----
  // Só registra quando o bot realmente tentou agir: executou, bloqueou ou falhou.

  /**
   * Cria uma entrada de tentativa de auto-trade e retorna o objeto entry
   * para ser atualizado após o resultado da requisição.
   */
  function addTradeAttempt(data) {
    var sig = data.signal || {};
    var now = new Date();
    var entry = {
      hora:      now.toLocaleTimeString("pt-BR"),
      simbolo:   (data.tv_symbol || "-").split(":").pop(),
      sinal:     sig.acao || "-",
      score:     sig.score != null ? sig.score : "-",
      entrada:   sig.entrada ? fmtPts(sig.entrada) : "-",
      stop:      sig.stop    ? fmtPts(sig.stop)    : "-",
      tp1:       sig.tp1     ? fmtPts(sig.tp1)     : "-",
      status:    "pending",   // "pending" | "executado" | "bloqueado" | "erro"
      statusMsg: "⏳ Enviando...",
      iaMotivoTip: "",
    };
    alerts.unshift(entry);
    renderAlerts();
    return entry;
  }

  /** Atualiza o status de uma entrada e re-renderiza. */
  function updateTradeAttempt(entry, status, statusMsg, iaMotivoTip) {
    entry.status    = status;
    entry.statusMsg = statusMsg;
    entry.iaMotivoTip = iaMotivoTip || "";
    renderAlerts();
  }

  function renderAlerts() {
    var empty = el("mt5-alerts-empty");
    var wrap  = el("mt5-alerts-wrap");
    var tbody = el("mt5-alerts-body");
    if (!tbody) return;

    if (alerts.length === 0) {
      if (empty) empty.hidden = false;
      if (wrap)  wrap.hidden  = true;
      return;
    }
    if (empty) empty.hidden = true;
    if (wrap)  wrap.hidden  = false;

    var statusColors = {
      "executado": "#22c55e",
      "bloqueado": "#f87171",
      "erro":      "#f59e0b",
      "pending":   "var(--text-muted)",
    };
    var rowBg = {
      "bloqueado": "background:rgba(127,29,29,.18)",
      "erro":      "background:rgba(120,53,15,.18)",
    };

    tbody.innerHTML = alerts.slice(0, 50).map(function (a) {
      var cls    = a.sinal === "COMPRA" ? "rec-buy" : a.sinal === "VENDA" ? "rec-sell" : "";
      var color  = statusColors[a.status] || "var(--text-muted)";
      var bg     = rowBg[a.status] ? ' style="' + rowBg[a.status] + '"' : '';
      var tip    = a.iaMotivoTip ? ' title="' + a.iaMotivoTip.replace(/"/g, "'") + '"' : '';
      var scoreAbs = a.score != null && a.score !== "-" ? "|" + Math.abs(a.score) + "|" : "—";
      return "<tr" + bg + ">" +
        "<td>" + a.hora + "</td>" +
        "<td style='font-size:.78rem'>" + a.simbolo + "</td>" +
        "<td><span class='" + cls + "'>" + a.sinal + "</span></td>" +
        "<td style='text-align:center'>" + scoreAbs + "</td>" +
        "<td>" + a.entrada + "</td>" +
        "<td>" + a.stop + "</td>" +
        "<td>" + a.tp1 + "</td>" +
        "<td style='color:" + color + ";font-weight:600'" + tip + ">" + a.statusMsg + "</td>" +
        "<td style='font-size:.72rem;color:var(--text-muted);max-width:180px;white-space:normal'>" +
          (a.iaMotivoTip ? a.iaMotivoTip.slice(0, 100) : "—") + "</td>" +
        "</tr>";
    }).join("");
  }

  // Expõe funções para o IIFE de auto-trade registrar tentativas
  window._addTradeAttempt    = addTradeAttempt;
  window._updateTradeAttempt = updateTradeAttempt;

  // Expõe markAlertIABlocked (agora só usado para banner no painel de sinal)
  window._markAlertIABlocked = function (acao, motivo) {
    var banner = document.getElementById("mt5-ia-block-banner");
    if (banner) {
      banner.textContent = "🚫 IA bloqueou — " + (motivo ? String(motivo).slice(0, 120) : "trade não autorizado");
      banner.hidden = false;
    }
  };

  // ---- Fetch ----
  function fetchMt5() {
    var sym = getSym();
    var ivl = getIvl();
    var url = "/api/monitor/mt5?tv_symbol=" + encodeURIComponent(sym) + "&interval=" + encodeURIComponent(ivl);

    showLoading(true);
    setStatus("Buscando dados do MT5...");

    fetch(url)
      .then(function (r) { return r.json(); })
      .then(function (data) {
        showLoading(false);
        if (!data.ok) {
          showError(data.error || "Sem dados.");
          setStatus("Erro: " + (data.error || "sem dados."));
          return;
        }
        // Render chart (em MANAGE mode: só candles/indicadores, sem linhas de sinal)
        var _tm = window._getTradeMode ? window._getTradeMode() : "SCAN";
        renderChart(data, _tm === "MANAGE");

        // Se o último candle não é de hoje (mercado fechado / virada de dia),
        // exibe sinal neutro no painel e não processa auto-trade
        var dadosHoje = isSignalFromToday(data.candles || []);
        if (!dadosHoje && data.signal) {
          if (tradeMode !== "MANAGE") {
            var sigNeutro = Object.assign({}, data.signal, { acao: "NEUTRO" });
            renderSignal(sigNeutro, data.fonte);
            setStatus("Mercado fechado — aguardando abertura de hoje.");
          }
          return;
        }

        // Em MANAGE mode: não atualiza o painel de sinal nem as linhas do gráfico
        // O painel de gestão é atualizado exclusivamente pelo fetchManage()
        if (_tm === "MANAGE") {
          // Só atualiza o sinal técnico dentro do painel MANAGE
          var mgmtSigEl = el("mgmt-sig-acao");
          var mgmtScEl  = el("mgmt-sig-score");
          if (data.signal && mgmtSigEl) mgmtSigEl.textContent = data.signal.acao || "—";
          if (data.signal && mgmtScEl)  mgmtScEl.textContent  = data.signal.score != null ? (data.signal.score > 0 ? "+" : "") + data.signal.score : "—";
          return;
        }

        // Render signal panel
        renderSignal(data.signal, data.fonte);
        // Audio alert
        checkAudioAlert(data.signal);
        if (window._onNewMT5Signal && data.signal) window._onNewMT5Signal(data.signal);
        // Auto-trade: dispara se sinal ativo + debounce de 15min (evita re-entradas imediatas)
        if (data.signal && data.signal.acao !== "NEUTRO") {
          var agora = Date.now();
          var lastMs     = window._getLastTradeMs  ? window._getLastTradeMs()  : 0;
          var debounceMs = window._getDebounceMs   ? window._getDebounceMs()   : 900000;
          var dentroDebounce = (agora - lastMs) < debounceMs;
          if (!dentroDebounce && window.autoTradeOnSignal) {
            window.autoTradeOnSignal(data);
          } else if (dentroDebounce && window._getAutoEnabled && window._getAutoEnabled()) {
            var restante = Math.ceil((debounceMs - (agora - lastMs)) / 60000);
            setStatus("⏳ Sinal " + data.signal.acao + " detectado — próxima entrada em ~" + restante + "min (debounce).");
          }
        }
        // Update label
        var lbl = el("mt5-chart-label");
        if (lbl) lbl.textContent = "Grafico MT5 - " + (data.tv_symbol || sym) + " (" + ivl + "m)";
        setStatus("Ativo. MT5 conectado. Proximo update em " + getRefr() + "s.");
      })
      .catch(function (err) {
        showLoading(false);
        showError("Erro de rede: " + err.message);
        setStatus("Falha na requisicao.");
      });
  }

  // ---- Start / Stop ----
  function startMonitor() {
    if (active) stopMonitor();
    active = true;
    initChart();
    fetchMt5();
    var refr = getRefr();
    if (refr > 0) timer = setInterval(fetchMt5, refr * 1000);
    startTickTimer();  // tick a cada 3s para o último candle em tempo real

    var startBtn = el("mt5-start-btn");
    var stopBtn  = el("mt5-stop-btn");
    if (startBtn) startBtn.style.display = "none";
    if (stopBtn)  stopBtn.style.display  = "";
  }

  function stopMonitor() {
    active = false;
    if (timer) { clearInterval(timer); timer = null; }
    stopTickTimer();
    lastCandle = null;
    var startBtn = el("mt5-start-btn");
    var stopBtn  = el("mt5-stop-btn");
    if (startBtn) startBtn.style.display = "";
    if (stopBtn)  stopBtn.style.display  = "none";
    setStatus("Monitoramento pausado.");
  }

  // ---- Event listeners ----
  var startBtn = el("mt5-start-btn");
  if (startBtn) startBtn.addEventListener("click", startMonitor);

  var stopBtn = el("mt5-stop-btn");
  if (stopBtn) stopBtn.addEventListener("click", stopMonitor);

  var clearBtn = el("mt5-clear-alerts");
  if (clearBtn) clearBtn.addEventListener("click", function () {
    alerts = [];
    renderAlerts();
  });

  // Auto-start when tab is clicked
  document.querySelectorAll(".main-tab-btn").forEach(function (btn) {
    if (btn.getAttribute("data-tab") === "mt5") {
      btn.addEventListener("click", function () {
        if (!active) {
          setTimeout(startMonitor, 200);
        }
      });
    }
  });

}());

// ============================================================
// AUTO-TRADE — execução automática + gestão SCAN / MANAGE
// ============================================================
(function () {
  var autoTradeEnabled   = false;
  var tradeMode          = "SCAN";   // "SCAN" | "MANAGE"
  var _lastAutoTradeMs   = 0;        // timestamp da última tentativa de auto-trade
  var _AUTO_DEBOUNCE_MS  = 15 * 60 * 1000; // 15 min — evita re-entrada imediata
  window._getTradeMode    = function () { return tradeMode; };
  window._getAutoEnabled  = function () { return autoTradeEnabled; };
  window._getLastTradeMs  = function () { return _lastAutoTradeMs; };
  window._getDebounceMs   = function () { return _AUTO_DEBOUNCE_MS; };
  var manageTimer      = null;
  var el = function (id) { return document.getElementById(id); };

  var toggleBtn = el("at-toggle");
  var closeBtn  = el("at-close-btn");
  var statusDiv = el("at-status");
  var posDiv    = el("at-positions");

  // ── helpers ────────────────────────────────────────────────────────────
  function setStatus(msg, isError) {
    if (!statusDiv) return;
    statusDiv.textContent = msg;
    statusDiv.style.color = isError ? "#ef4444" : "var(--text-muted)";
  }

  function getSym() {
    return (window.mt5MonitorGetSym && window.mt5MonitorGetSym()) || "BMFBOVESPA:WIN1!";
  }
  function getIvl() {
    var s = el("mt5-interval");
    return s ? s.value : "15";
  }

  function fmtPnl(pts, brl) {
    var color = pts >= 0 ? "#22c55e" : "#ef4444";
    var sign  = pts >= 0 ? "+" : "";
    return '<span style="color:' + color + '">' + sign + pts + ' pts | ' +
           sign + 'R$' + Math.abs(brl).toFixed(2) + '</span>';
  }

  function updateToggleUI() {
    if (!toggleBtn) return;
    if (autoTradeEnabled) {
      toggleBtn.textContent = "🟢 Auto-Trade ON";
      toggleBtn.classList.add("at-on");
    } else {
      toggleBtn.textContent = "🔴 Auto-Trade OFF";
      toggleBtn.classList.remove("at-on");
    }
  }

  // ── Renderiza posições no painel AT (compacto) ─────────────────────────
  function renderPositions(positions) {
    if (!posDiv) return;
    if (!positions || !positions.length) { posDiv.innerHTML = ""; return; }
    posDiv.innerHTML = positions.map(function (p) {
      var side = p.type === 0 ? "COMPRA" : "VENDA";
      var cls  = p.type === 0 ? "rec-buy" : "rec-sell";
      var pnlColor = p.profit >= 0 ? "#22c55e" : "#ef4444";
      var pnlSign  = p.profit >= 0 ? "+" : "-";
      return '<div class="at-pos">' +
        '🎯 <strong>' + p.symbol + '</strong> &nbsp;' +
        '<span class="' + cls + '">' + side + '</span> &nbsp;' +
        'Vol: ' + p.volume + ' | ' +
        'Abertura: ' + (p.price_open || 0).toFixed(0) + ' | ' +
        'P&amp;L: <span style="color:' + pnlColor + '">' + pnlSign + 'R$' + Math.abs(p.profit || 0).toFixed(2) + '</span>' +
        '</div>';
    }).join('');
  }

  // ── Modo SCAN: exibe painel normal de sinal ────────────────────────────
  function enterScanMode() {
    if (tradeMode === "SCAN") return;
    tradeMode = "SCAN";
    if (window._setActiveTradeLine) window._setActiveTradeLine(null);  // limpa linhas do trade
    if (window._redrawTradeLines)   window._redrawTradeLines();        // limpa linhas do gráfico
    var managePanel  = el("mt5-manage-panel");
    var signalContent = el("mt5-signal-content");
    var signalIdle    = el("mt5-signal-idle");
    if (managePanel)   managePanel.hidden   = true;
    if (signalContent) signalContent.hidden = true;
    if (signalIdle)    signalIdle.hidden    = false;   // mostra idle até próxima análise
    if (autoTradeEnabled) {
      setStatus("🔍 SCAN — aguardando próximo sinal (score ≥ " +
        (el("at-score-min") ? el("at-score-min").value : "4") + ")");
    }
  }

  // ── Modo MANAGE: exibe painel de gestão do trade ativo ─────────────────
  window.enterManageMode = function enterManageMode() {
    if (tradeMode === "MANAGE") return;
    tradeMode = "MANAGE";
    var managePanel   = el("mt5-manage-panel");
    var signalContent = el("mt5-signal-content");
    var signalIdle    = el("mt5-signal-idle");
    if (managePanel)   managePanel.hidden   = false;
    if (signalContent) signalContent.hidden = true;
    if (signalIdle)    signalIdle.hidden    = true;
    setStatus("🎯 MANAGE — gerenciando trade ativo...");
  }

  // ── Renderiza painel de gestão ─────────────────────────────────────────
  var REC_COLORS = {
    ok:      { border: "#22c55e", bg: "rgba(34,197,94,.1)"  },
    success: { border: "#22c55e", bg: "rgba(34,197,94,.15)" },
    warning: { border: "#f59e0b", bg: "rgba(245,158,11,.1)" },
    danger:  { border: "#ef4444", bg: "rgba(239,68,68,.1)"  },
  };

  function renderManagePanel(analysis, position, tradeLog) {
    _renderManagePanelInner(analysis, position, tradeLog);
  }

  function _renderManagePanelInner(analysis, position, tradeLog) {
    if (!analysis) return;
    renderManagePanelCore(analysis, position);

    // Badge IA (do trade_log salvo)
    var aiBadge = el("mgmt-ai-badge");
    var aiText  = el("mgmt-ai-text");
    if (tradeLog && tradeLog.ai_validated && aiBadge && aiText) {
      var ver = tradeLog.ai_veredito || "—";
      var conf = tradeLog.ai_confidence != null ? tradeLog.ai_confidence + "%" : "";
      var mot  = tradeLog.ai_motivo || "";
      aiText.textContent = "IA: " + ver + (conf ? " (" + conf + ")" : "") + (mot ? " — " + mot.slice(0, 60) : "");
      aiBadge.hidden = false;
    } else if (aiBadge) {
      aiBadge.hidden = true;
    }
  }

  // Renomeia a funcao interna para que renderManagePanel possa chamar a versao correta
  var _origRenderManagePanel = renderManagePanelCore;
  renderManagePanel = _renderManagePanelInner;
  function renderManagePanelCore(analysis, position) {
    if (!analysis) return;
    function set(id, val) { var e = el(id); if (e) e.textContent = val; }
    function setHtml(id, val) { var e = el(id); if (e) e.innerHTML = val; }

    var sideBadge = el("mgmt-side-badge");
    if (sideBadge) {
      sideBadge.textContent = analysis.position_side || "-";
      sideBadge.style.background = analysis.position_side === "COMPRA" ? "#22c55e" : "#ef4444";
      sideBadge.style.color = "#fff";
    }
    var pnlPts = analysis.pnl_points != null ? analysis.pnl_points : 0;
    var pnlBrl = analysis.pnl_brl    != null ? analysis.pnl_brl    : 0;
    var pnlColor = pnlPts >= 0 ? "#22c55e" : "#ef4444";
    var sign     = pnlPts >= 0 ? "+" : "";
    var pnlPtsEl = el("mgmt-pnl-pts");
    var pnlBrlEl = el("mgmt-pnl-brl");
    if (pnlPtsEl) { pnlPtsEl.textContent = sign + pnlPts + " pts"; pnlPtsEl.style.color = pnlColor; }
    if (pnlBrlEl) { pnlBrlEl.textContent = sign + "R$" + Math.abs(pnlBrl).toFixed(2); pnlBrlEl.style.color = pnlColor; }
    set("mgmt-candles",    analysis.candles_open != null ? analysis.candles_open + " candles" : "—");
    set("mgmt-entry",      analysis.entry_price   != null ? analysis.entry_price.toLocaleString("pt-BR")   : "—");
    set("mgmt-current",    analysis.current_price  != null ? analysis.current_price.toLocaleString("pt-BR")  : "—");
    set("mgmt-sl",         analysis.sl_current     != null ? analysis.sl_current.toLocaleString("pt-BR")     : "—");
    set("mgmt-tp1",        analysis.tp1_current    != null ? analysis.tp1_current.toLocaleString("pt-BR")    : "—");
    set("mgmt-dist-stop",  analysis.dist_stop_pts  != null ? analysis.dist_stop_pts + " pts" : "—");
    set("mgmt-dist-tp1",   analysis.dist_tp1_pts   != null ? analysis.dist_tp1_pts  + " pts" : "—");
    var recBox = el("mgmt-rec-box");
    var style  = REC_COLORS[analysis.alert_level] || REC_COLORS.ok;
    if (recBox) {
      recBox.style.borderLeftColor  = style.border;
      recBox.style.backgroundColor  = style.bg;
    }
    set("mgmt-rec-label",  analysis.recommendation || "—");
    set("mgmt-rec-reason", analysis.reason         || "—");
    var slWrap = el("mgmt-sl-suggestion-wrap");
    if (slWrap) {
      if (analysis.new_sl_suggestion != null) {
        slWrap.hidden = false;
        set("mgmt-sl-val", analysis.new_sl_suggestion.toLocaleString("pt-BR"));
      } else {
        slWrap.hidden = true;
      }
    }
    set("mgmt-sig-acao",  analysis.signal_acao  || "—");
    set("mgmt-sig-score", analysis.signal_score != null ? (analysis.signal_score > 0 ? "+" : "") + analysis.signal_score : "—");
  }

  // ── Polling do modo MANAGE ─────────────────────────────────────────────
  function fetchManage() {
    var sym = getSym();
    var ivl = getIvl();
    fetch("/api/autotrade/manage?tv_symbol=" + encodeURIComponent(sym) + "&interval=" + ivl)
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d.ok) return;
        if (d.mode === "SCAN") {
          // Posicao foi fechada — volta para SCAN
          enterScanMode();
          renderPositions([]);
          if (d.closed_trade) {
            // Registrou fechamento automatico
            var ct = d.closed_trade;
            var pnlMsg = ct.pnl_pts != null
              ? " | P&L: " + (ct.pnl_pts >= 0 ? "+" : "") + ct.pnl_pts + " pts"
              : "";
            setStatus("✅ Trade fechado (" + (ct.close_reason || "MT5") + ")" + pnlMsg + " — 🔍 SCAN.");
            setTimeout(loadAutoTradesHistory, 800);
          } else if (autoTradeEnabled) {
            setStatus("✅ Trade encerrado. 🔍 Voltando ao modo SCAN...");
          }
        } else {
          // Ainda em MANAGE
          enterManageMode();
          // Salva linhas do trade ativo para o gráfico não sobrescrevê-las
          if (d.trade_log && window._setActiveTradeLine) {
            window._setActiveTradeLine({
              acao:  d.trade_log.acao,
              entry: d.trade_log.entry_price,
              sl:    d.trade_log.sl_initial,
              tp1:   d.trade_log.tp1_initial,
            });
            if (window._redrawTradeLines) window._redrawTradeLines();
          }
          renderManagePanel(d.analysis, d.position, d.trade_log);
          if (d.position) renderPositions([d.position]);

          // Auto-executa FECHAR no TP1 (1 contrato → fecha e garante lucro)
          if (autoTradeEnabled && d.analysis && d.analysis.tp1_reached) {
            var sym = getSym();
            setStatus("🎯 TP1 atingido! Fechando trade automaticamente...");
            fetch("/api/autotrade/apply-recommendation", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ tv_symbol: sym, action: "FECHAR", new_sl: null, close_reason: "TP1" }),
            })
              .then(function (r) { return r.json(); })
              .then(function (rd) {
                if (rd.ok) {
                  var pnlMsg = rd.pnl_pts != null ? " | P&L: +" + rd.pnl_pts + " pts" : "";
                  setStatus("✅ TP1 atingido! Trade fechado com lucro" + pnlMsg + " — 🔍 SCAN.");
                  enterScanMode();
                  setTimeout(loadAutoTradesHistory, 800);
                } else {
                  setStatus("⚠️ Falha ao fechar no TP1: " + (rd.error || "erro desconhecido"));
                }
              })
              .catch(function () { setStatus("⚠️ Erro de rede ao tentar fechar no TP1."); });
          }

          // Auto-executa FECHAR se auto-trade ativo e recomendação for FECHAR (reversão de sinal)
          else if (autoTradeEnabled && d.analysis && d.analysis.recommendation === "FECHAR") {
            var sym = getSym();
            setStatus("⚡ Reversão detectada — fechando trade automaticamente...");
            fetch("/api/autotrade/apply-recommendation", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ tv_symbol: sym, action: "FECHAR", new_sl: null, close_reason: "REVERSAO" }),
            })
              .then(function (r) { return r.json(); })
              .then(function (rd) {
                if (rd.ok) {
                  var pnlMsg = rd.pnl_pts != null ? " | P&L: " + (rd.pnl_pts >= 0 ? "+" : "") + rd.pnl_pts + " pts" : "";
                  setStatus("✅ Trade fechado por reversão de sinal" + pnlMsg + " — 🔍 voltando ao SCAN.");
                  enterScanMode();
                  setTimeout(loadAutoTradesHistory, 800);
                } else {
                  setStatus("⚠️ Falha ao fechar automaticamente: " + (rd.error || "erro desconhecido"));
                }
              })
              .catch(function () { setStatus("⚠️ Erro de rede ao tentar fechar por reversão."); });
          }
        }
      })
      .catch(function () {});
  }

  // ── Inicia polling MANAGE ──────────────────────────────────────────────
  window.startManagePolling = function startManagePolling(intervalSec) {
    if (manageTimer) clearInterval(manageTimer);
    fetchManage();
    manageTimer = setInterval(fetchManage, (intervalSec || 30) * 1000);
  }

  function stopManagePolling() {
    if (manageTimer) { clearInterval(manageTimer); manageTimer = null; }
  }

  // ── Toggle Auto-Trade ON/OFF ───────────────────────────────────────────
  if (toggleBtn) {
    toggleBtn.addEventListener("click", function () {
      autoTradeEnabled = !autoTradeEnabled;
      updateToggleUI();
      if (autoTradeEnabled) {
        setStatus("✅ Auto-Trade ATIVO — aguardando próximo sinal (score ≥ " +
          (el("at-score-min") ? el("at-score-min").value : "5") + ")");
        // Verifica se já há posição aberta ao ligar
        fetchManage();
        startManagePolling(30);
      } else {
        setStatus("⏹ Auto-Trade desativado.");
        stopManagePolling();
        enterScanMode();
        renderPositions([]);
      }
    });
  }

  // ── Fecha posições ─────────────────────────────────────────────────────
  if (closeBtn) {
    closeBtn.addEventListener("click", function () {
      if (!confirm("Fechar TODAS as posições abertas pelo bot?")) return;
      var sym = getSym();
      closeBtn.disabled = true;
      fetch("/api/autotrade/close", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tv_symbol: sym }),
      })
        .then(function (r) { return r.json(); })
        .then(function (d) {
          closeBtn.disabled = false;
          if (d.ok) {
            setStatus("✅ Posições fechadas: " + (d.results || []).length);
            renderPositions([]);
            enterScanMode();
          } else {
            setStatus("❌ " + (d.error || "Erro ao fechar posições."), true);
          }
        })
        .catch(function () { closeBtn.disabled = false; });
    });
  }

  // ── Chamado pelo monitor MT5 quando detecta novo sinal ─────────────────
  window.autoTradeOnSignal = function (data) {
    if (!autoTradeEnabled) return;

    // Em MANAGE: já há trade aberto, não abre outro
    if (tradeMode === "MANAGE") {
      setStatus("⏸ Trade ativo — aguardando encerramento antes de nova entrada.");
      return;
    }

    var sig = data.signal || {};
    if (sig.acao !== "COMPRA" && sig.acao !== "VENDA") return;

    var scoreMin  = parseInt((el("at-score-min") && el("at-score-min").value) || "5");
    var volume    = parseFloat((el("at-volume")  && el("at-volume").value)    || "1");
    var scoreRaw  = sig.score || 0;
    var scoreAbs  = Math.abs(scoreRaw);

    // Bloqueia se |score| < mínimo
    if (scoreAbs < scoreMin) {
      setStatus("⏭ " + sig.acao + " ignorado — |score| " + scoreAbs + " < mínimo " + scoreMin + ".");
      return;
    }

    // Marca timestamp da tentativa ANTES de enviar (evita re-entrada durante a request)
    _lastAutoTradeMs = Date.now();

    // Registra a tentativa no histórico imediatamente (será atualizada com o resultado)
    var _attempt = window._addTradeAttempt ? window._addTradeAttempt(data) : null;

    var direcao = sig.acao === "VENDA"
      ? "VENDA (score " + scoreRaw + ", força " + scoreAbs + ")"
      : "COMPRA (score +" + scoreAbs + ")";
    setStatus("⏳ Enviando ordem " + direcao + " para MT5 — validando com IA...");

    fetch("/api/autotrade/execute", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        tv_symbol: data.tv_symbol || "BMFBOVESPA:WIN1!",
        acao:      sig.acao,
        score:     scoreRaw,
        entrada:   sig.entrada || null,
        stop:      sig.stop    || null,
        tp1:       sig.tp1     || null,
        volume:    volume,
        interval:  getIvl(),
        signal:    sig,          // envia sinal completo para validacao IA
      }),
    })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d.ok && d.result) {
          var r = d.result;
          var aiInfo = d.ai || {};
          var aiMsg  = aiInfo.ia_usada
            ? " ✅ IA: " + (aiInfo.veredito || "APROVAR") + " (" + (aiInfo.confianca || 0) + "%)"
            : "";
          setStatus(
            "✅ EXECUTADO! " + sig.acao +
            " | Order #" + r.order +
            " | Preço: " + (r.price ? r.price.toFixed(0) : "-") +
            " | |score|=" + scoreAbs + aiMsg
          );
          // Atualiza tentativa no histórico
          if (_attempt && window._updateTradeAttempt) {
            var iaOk = aiInfo.ia_usada ? " ✅ IA " + (aiInfo.confianca || "") + "%" : "";
            window._updateTradeAttempt(_attempt, "executado",
              "✅ Executado #" + r.order + iaOk,
              aiInfo.motivo || "");
          }
          // Guarda linhas do trade para o gráfico não sobrescrevê-las em MANAGE mode
          if (window._setActiveTradeLine) {
            window._setActiveTradeLine({
              acao:  sig.acao,
              entry: sig.entrada || (r.price || null),
              sl:    sig.stop    || null,
              tp1:   sig.tp1     || null,
            });
            if (window._redrawTradeLines) window._redrawTradeLines();
          }
          // Mostra badge IA no painel manage ao entrar
          var aiBadge = el("mgmt-ai-badge");
          var aiText  = el("mgmt-ai-text");
          if (aiBadge && aiText && aiInfo.ia_usada) {
            aiText.textContent = "IA: " + (aiInfo.veredito || "APROVAR") +
              (aiInfo.confianca ? " (" + aiInfo.confianca + "%)" : "") +
              (aiInfo.motivo ? " — " + String(aiInfo.motivo).slice(0, 80) : "");
            aiBadge.hidden = false;
          }
          enterManageMode();
          startManagePolling(30);
          loadAutoTradesHistory();
        } else {
          // Bloqueado pela IA ou erro — mostra motivo com detalhes
          var bloqMsg   = d.error || "Erro desconhecido.";
          var aiInfo2   = d.ai || {};
          var isIABlock = bloqMsg.toLowerCase().indexOf("ia bloqueou") >= 0 || bloqMsg.toLowerCase().indexOf("bloqueou") >= 0;
          var motivoIA  = aiInfo2.motivo || bloqMsg;
          var motivo    = aiInfo2.motivo ? " — " + String(aiInfo2.motivo).slice(0, 100) : "";
          if (isIABlock) {
            setStatus("🚫 IA bloqueou o trade: |score|=" + scoreAbs + motivo);
            // Atualiza tentativa no histórico como bloqueado
            if (_attempt && window._updateTradeAttempt) {
              window._updateTradeAttempt(_attempt, "bloqueado", "🚫 IA bloqueou", motivoIA);
            }
            if (window._markAlertIABlocked) {
              window._markAlertIABlocked(sig.acao, motivoIA);
            }
          } else {
            setStatus("❌ Trade não executado: " + bloqMsg);
            // Atualiza tentativa no histórico como erro
            if (_attempt && window._updateTradeAttempt) {
              window._updateTradeAttempt(_attempt, "erro", "❌ " + bloqMsg.slice(0, 60), "");
            }
          }
          // Libera debounce imediatamente se foi bloqueio (permite tentar no próximo ciclo)
          _lastAutoTradeMs = 0;
          // Atualiza histórico de trades para mostrar o bloqueio no DB
          setTimeout(loadAutoTradesHistory, 600);
        }
      })
      .catch(function (err) { setStatus("❌ Erro de rede: " + err.message, true); });
  };

  // ── Listener: Aplicar recomendacao (breakeven / trailing) ─────────────
  var applyBtn2 = el("mgmt-apply-btn");
  if (applyBtn2) {
    applyBtn2.addEventListener("click", function () {
      var action = applyBtn2.dataset.action || "BREAKEVEN";
      var newSl  = parseFloat(applyBtn2.dataset.newSl);
      if (isNaN(newSl)) return;
      var sym = getSym();
      applyBtn2.disabled = true;
      applyBtn2.textContent = "⏳ Aplicando...";
      fetch("/api/autotrade/apply-recommendation", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tv_symbol: sym, action: action, new_sl: newSl }),
      })
        .then(function (r) { return r.json(); })
        .then(function (d) {
          applyBtn2.disabled = false;
          if (d.ok) {
            setStatus("✅ " + action + " aplicado! Novo SL: " + newSl.toLocaleString("pt-BR"));
            fetchManage();
          } else {
            setStatus("❌ Erro: " + (d.error || "Falha ao aplicar."), true);
            applyBtn2.textContent = "✅ Aplicar";
          }
        })
        .catch(function () { applyBtn2.disabled = false; });
    });
  }

  // ── Listener: Fechar trade manualmente ─────────────────────────────────
  var closeTradeBtn = el("mgmt-close-trade-btn");
  if (closeTradeBtn) {
    closeTradeBtn.addEventListener("click", function () {
      if (!confirm("Fechar o trade ativo agora?")) return;
      var sym = getSym();
      closeTradeBtn.disabled = true;
      fetch("/api/autotrade/apply-recommendation", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tv_symbol: sym, action: "FECHAR" }),
      })
        .then(function (r) { return r.json(); })
        .then(function (d) {
          closeTradeBtn.disabled = false;
          if (d.ok) {
            setStatus("✅ Trade fechado manualmente.");
            enterScanMode();
            renderPositions([]);
            setTimeout(loadAutoTradesHistory, 800);
          } else {
            setStatus("❌ Erro: " + (d.error || "Falha ao fechar."), true);
          }
        })
        .catch(function () { closeTradeBtn.disabled = false; });
    });
  }

  // ── Listener: Refresh historico ────────────────────────────────────────
  var histRefreshBtn = el("at-history-refresh");
  if (histRefreshBtn) {
    histRefreshBtn.addEventListener("click", loadAutoTradesHistory);
  }

  // ── Listener: Filtro Hoje / Todos ──────────────────────────────────────
  var btnFilterToday = el("at-filter-today");
  var btnFilterAll   = el("at-filter-all");
  if (btnFilterToday) {
    btnFilterToday.addEventListener("click", function () {
      _atShowAll = false;
      loadAutoTradesHistory();
    });
  }
  if (btnFilterAll) {
    btnFilterAll.addEventListener("click", function () {
      _atShowAll = true;
      loadAutoTradesHistory();
    });
  }

  // Carrega historico ao inicializar (só hoje por padrão)
  loadAutoTradesHistory();

  // ── Polling contínuo a cada 30s quando auto-trade está ativo ──────────
  setInterval(function () {
    if (autoTradeEnabled) fetchManage();
  }, 30000);
})();

// ============================================================
// HISTORICO DE TRADES AUTOMATICOS
// ============================================================
var _atShowAll = false;   // false = só hoje, true = todos os dias

function loadAutoTradesHistory() {
  var el = function (id) { return document.getElementById(id); };

  // Sincroniza visual dos botões filtro
  var btnToday = el("at-filter-today");
  var btnAll   = el("at-filter-all");
  if (btnToday && btnAll) {
    if (_atShowAll) {
      btnToday.style.background = ""; btnToday.style.color = ""; btnToday.style.fontWeight = "";
      btnAll.style.background   = "var(--accent)"; btnAll.style.color = "#000"; btnAll.style.fontWeight = "600";
    } else {
      btnToday.style.background = "var(--accent)"; btnToday.style.color = "#000"; btnToday.style.fontWeight = "600";
      btnAll.style.background   = ""; btnAll.style.color = ""; btnAll.style.fontWeight = "";
    }
  }

  var url = "/api/autotrade/trades?limit=100" + (_atShowAll ? "&all=1" : "");
  fetch(url)
    .then(function (r) { return r.json(); })
    .then(function (d) {
      if (!d.ok) return;
      var items = d.items || [];
      var stats = d.stats  || {};

      // Atualiza stats
      function setTxt(id, val) { var e = el(id); if (e) e.textContent = val; }
      setTxt("at-stat-total",      stats.total         || 0);
      setTxt("at-stat-wins",       stats.wins          || 0);
      setTxt("at-stat-losses",     stats.losses        || 0);
      setTxt("at-stat-winrate",    (stats.win_rate_pct || 0) + "%");
      setTxt("at-stat-bloqueados", stats.bloqueados_ia || 0);

      var pnlPts = stats.pnl_total_pts || 0;
      var pnlBrl = stats.pnl_total_brl || 0;
      var ptsEl  = el("at-stat-pnl-pts");
      var brlEl  = el("at-stat-pnl-brl");
      if (ptsEl) {
        ptsEl.textContent = (pnlPts >= 0 ? "+" : "") + pnlPts + " pts";
        ptsEl.style.color = pnlPts >= 0 ? "#22c55e" : "#ef4444";
      }
      if (brlEl) {
        brlEl.textContent = "R$" + (pnlBrl >= 0 ? "+" : "") + pnlBrl.toFixed(2);
        brlEl.style.color = pnlBrl >= 0 ? "#22c55e" : "#ef4444";
      }

      var emptyEl = el("at-history-empty");
      var wrapEl  = el("at-history-wrap");
      var bodyEl  = el("at-history-body");

      if (!items.length) {
        if (emptyEl) emptyEl.hidden = false;
        if (wrapEl)  wrapEl.hidden  = true;
        return;
      }
      if (emptyEl) emptyEl.hidden = true;
      if (wrapEl)  wrapEl.hidden  = false;

      if (!bodyEl) return;
      bodyEl.innerHTML = items.map(function (t) {
        var isOpen     = !t.closed_at;
        var acao       = t.acao || "—";
        var acaoCls    = acao === "COMPRA" ? "rec-buy" : "rec-sell";
        var hora       = t.opened_at ? t.opened_at.slice(11, 19) : "—";
        var data       = t.opened_at ? t.opened_at.slice(0, 10) : "—";
        var pnlPts     = t.pnl_pts;
        var pnlBrl     = t.pnl_brl;
        var pnlPtsStr  = isOpen ? "<em>aberto</em>" :
          pnlPts != null ? '<span style="color:' + (pnlPts >= 0 ? "#22c55e" : "#ef4444") + '">' +
          (pnlPts >= 0 ? "+" : "") + pnlPts + '</span>' : "—";
        var pnlBrlStr  = isOpen ? "" :
          pnlBrl != null ? '<span style="color:' + (pnlBrl >= 0 ? "#22c55e" : "#ef4444") + '">' +
          "R$" + (pnlBrl >= 0 ? "+" : "") + parseFloat(pnlBrl).toFixed(2) + '</span>' : "—";

        var isBloqueado = t.close_reason === "BLOQUEADO_IA";
        var motivo = isBloqueado
          ? '<span style="color:#f87171;font-weight:700" title="' + (t.ai_motivo || "") + '">🚫 IA bloqueou</span>'
          : t.close_reason || (isOpen ? '<em style="color:#f59e0b">aberto</em>' : "—");
        var scoreDisp = t.score != null ? Math.abs(t.score) : null;
        var aiStr  = t.ai_validated
          ? '<span title="' + (t.ai_motivo || "") + '" style="color:' + (isBloqueado ? "#f87171" : "#818cf8") + '">' +
            (isBloqueado ? "🚫 " : "✓ ") +
            (t.ai_veredito || "IA") + (t.ai_confidence ? " " + t.ai_confidence + "%" : "") + '</span>'
          : '<span style="color:var(--text-muted)">—</span>';

        var rowStyle = isBloqueado
          ? ' style="background:rgba(127,29,29,.18);opacity:.85"'
          : '';
        var aiMotivTip = isBloqueado && t.ai_motivo
          ? ' title="Motivo IA: ' + t.ai_motivo.replace(/"/g, "'") + '"'
          : '';
        return '<tr' + rowStyle + aiMotivTip + '>' +
          '<td>' + data + '<br><small style="color:var(--text-muted)">' + hora + '</small></td>' +
          '<td style="font-size:.78rem">' + (t.tv_symbol || "").split(":").pop() + '</td>' +
          '<td><span class="' + acaoCls + '">' + acao + '</span></td>' +
          '<td style="text-align:center">' + (scoreDisp != null ? '|' + scoreDisp + '|' : "—") + '</td>' +
          '<td>' + (t.entry_price ? Math.round(t.entry_price).toLocaleString("pt-BR") : "—") + '</td>' +
          '<td>' + (t.sl_initial  ? Math.round(t.sl_initial).toLocaleString("pt-BR")  : "—") + '</td>' +
          '<td>' + (t.tp1_initial ? Math.round(t.tp1_initial).toLocaleString("pt-BR") : "—") + '</td>' +
          '<td>' + (t.exit_price && !isOpen ? Math.round(t.exit_price).toLocaleString("pt-BR") : "—") + '</td>' +
          '<td style="font-size:.78rem">' + motivo + '</td>' +
          '<td>' + pnlPtsStr + '</td>' +
          '<td>' + pnlBrlStr + '</td>' +
          '<td>' + aiStr + '</td>' +
          '</tr>';
      }).join("");
    })
    .catch(function (e) { console.warn("Auto-trades history erro:", e); });
}

// ============================================================
// RELATÓRIO MENSAL
// ============================================================
(function () {
  "use strict";
  var el = function (id) { return document.getElementById(id); };

  // Inicializa o mês com o mês atual
  var monthInput = el("report-month");
  if (monthInput) {
    var now = new Date();
    monthInput.value = now.getFullYear() + "-" + String(now.getMonth() + 1).padStart(2, "0");
  }

  function fmtDate(iso) {
    if (!iso) return "—";
    return iso.slice(0, 10);
  }

  function pct(n, d) {
    return d ? Math.round(n / d * 100) + "%" : "—";
  }

  // ── Carrega dados do relatório ─────────────────────────────────────────
  function loadReport() {
    var month = monthInput ? monthInput.value : "";
    var url   = "/api/monitor/signals/report" + (month ? "?month=" + encodeURIComponent(month) : "");

    fetch(url)
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d.ok) return;
        renderReport(d);
        // Habilita botão CSV
        var csvBtn = el("report-csv-btn");
        if (csvBtn) csvBtn.disabled = d.total === 0;
      })
      .catch(function (e) { console.error("Relatório erro:", e); });
  }

  // ── Renderiza o relatório ──────────────────────────────────────────────
  function renderReport(d) {
    var items  = d.items  || [];
    var daily  = d.daily  || [];
    var total  = d.total  || 0;
    var verif  = d.verificados || 0;

    var summaryEl = el("report-summary");
    var dailyEl   = el("report-daily-section");
    var detailEl  = el("report-detail-section");
    var emptyEl   = el("report-empty");

    function setText(id, val) { var e = el(id); if (e) e.textContent = val; }

    if (total === 0) {
      if (summaryEl) summaryEl.style.display = "none";
      if (dailyEl)   dailyEl.style.display   = "none";
      if (detailEl)  detailEl.style.display  = "none";
      if (emptyEl)   emptyEl.style.display   = "";
      return;
    }
    if (emptyEl) emptyEl.style.display = "none";

    // Cards de resumo
    if (summaryEl) summaryEl.style.display = "";
    var successCount = (d.tp1_hits || 0) + (d.tp2_hits || 0) + (d.tp3_hits || 0);
    setText("rpt-total",    total);
    setText("rpt-assertiv", verif ? pct(successCount, verif) : "—");
    setText("rpt-sucesso",  successCount + " trades");
    setText("rpt-tp1",      d.tp1_hits || 0);
    setText("rpt-tp2",      d.tp2_hits || 0);
    setText("rpt-tp3",      d.tp3_hits || 0);
    setText("rpt-stop",     d.stp_hits || 0);
    setText("rpt-stop-pct", verif ? pct(d.stp_hits || 0, verif) : "");
    setText("rpt-sem",      total - verif);

    // Tabela diária
    if (dailyEl) dailyEl.style.display = "";
    var dailyBody = el("report-daily-body");
    if (dailyBody) {
      dailyBody.innerHTML = daily.map(function (row) {
        var acertos = (row.tp1 || 0) + (row.tp2 || 0) + (row.tp3 || 0);
        return "<tr>" +
          "<td>" + (row.day || "—") + "</td>" +
          "<td>" + (row.total || 0) + "</td>" +
          "<td style='color:#22c55e'>" + (row.tp1 || 0) + "</td>" +
          "<td style='color:#3b82f6'>" + (row.tp2 || 0) + "</td>" +
          "<td style='color:#a855f7'>" + (row.tp3 || 0) + "</td>" +
          "<td style='color:#ef4444'>" + (row.stop || 0) + "</td>" +
          "<td>" + pct(acertos, row.total) + "</td>" +
          "</tr>";
      }).join("");
    }

    // Tabela de detalhes
    if (detailEl) detailEl.style.display = "";
    var detailBody = el("report-detail-body");
    if (detailBody) {
      detailBody.innerHTML = items.map(function (r) {
        var cls = r.acao === "COMPRA" ? "style='color:#22c55e'" : "style='color:#ef4444'";
        var outcome = "";
        if (r.tp3_hit)       outcome = "<span class='badge tp3'>★TP3</span>";
        else if (r.tp2_hit)  outcome = "<span class='badge tp2'>★TP2</span>";
        else if (r.tp1_hit)  outcome = "<span class='badge tp1'>★TP1</span>";
        else if (r.stop_hit) outcome = "<span class='badge no'>🛑Stop</span>";
        else if (r.outcome_checked_at) outcome = "<span class='badge'>—</span>";
        return "<tr>" +
          "<td>" + fmtDate(r.created_at) + "</td>" +
          "<td>" + (r.tv_symbol || "—").split(":").pop() + "</td>" +
          "<td " + cls + ">" + (r.acao || "—") + "</td>" +
          "<td>" + (r.score != null ? r.score : "—") + "</td>" +
          "<td>" + (r.entrada ? Number(r.entrada).toLocaleString("pt-BR") : "—") + "</td>" +
          "<td>" + (r.stop    ? Number(r.stop).toLocaleString("pt-BR")    : "—") + "</td>" +
          "<td>" + (r.tp1     ? Number(r.tp1).toLocaleString("pt-BR")     : "—") + "</td>" +
          "<td>" + (r.tp2     ? Number(r.tp2).toLocaleString("pt-BR")     : "—") + "</td>" +
          "<td>" + outcome + "</td>" +
          "</tr>";
      }).join("");
    }
  }

  // ── Exportar CSV ───────────────────────────────────────────────────────
  var csvBtn = el("report-csv-btn");
  if (csvBtn) {
    csvBtn.addEventListener("click", function () {
      var month = monthInput ? monthInput.value : "";
      var url   = "/api/monitor/signals/report" + (month ? "?month=" + encodeURIComponent(month) : "");
      fetch(url)
        .then(function (r) { return r.json(); })
        .then(function (d) {
          if (!d.ok || !d.items) return;
          var header = ["Data", "Simbolo", "Sinal", "Score", "Entrada", "Stop", "TP1", "TP2", "TP3", "TP1 hit", "TP2 hit", "TP3 hit", "Stop hit"];
          var rows = [header].concat(d.items.map(function (r) {
            return [
              fmtDate(r.created_at), (r.tv_symbol || "").split(":").pop(),
              r.acao, r.score, r.entrada, r.stop, r.tp1, r.tp2, r.tp3,
              r.tp1_hit ? "S" : "N", r.tp2_hit ? "S" : "N",
              r.tp3_hit ? "S" : "N", r.stop_hit ? "S" : "N"
            ];
          }));
          var csv  = rows.map(function (r) { return r.join(";"); }).join("\n");
          var blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
          var a    = document.createElement("a");
          a.href   = URL.createObjectURL(blob);
          a.download = "relatorio_" + (month || "trades") + ".csv";
          document.body.appendChild(a);
          a.click();
          document.body.removeChild(a);
        })
        .catch(function () {});
    });
  }

  // ── Verificar mês (batch verify) ──────────────────────────────────────
  var verifyBtn = el("report-verify-btn");
  if (verifyBtn) {
    verifyBtn.addEventListener("click", function () {
      verifyBtn.disabled = true;
      verifyBtn.textContent = "🔄 Verificando...";
      fetch("/api/monitor/signals/verify-batch", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ limit: 50 }),
      })
        .then(function (r) { return r.json(); })
        .then(function () {
          verifyBtn.textContent = "✅ Pronto";
          setTimeout(function () {
            verifyBtn.textContent = "🔍 Verificar Mês";
            verifyBtn.disabled = false;
          }, 3000);
          loadReport();
        })
        .catch(function () {
          verifyBtn.textContent = "❌ Erro";
          verifyBtn.disabled = false;
        });
    });
  }

  // Auto-start when report tab is clicked
  document.querySelectorAll(".main-tab-btn").forEach(function (btn) {
    if (btn.getAttribute("data-tab") === "report") {
      btn.addEventListener("click", function () { loadReport(); });
    }
  });

  var loadBtn = el("report-load-btn");
  if (loadBtn) loadBtn.addEventListener("click", loadReport);

}());

// ============================================================
// OPERACAO MANUAL — Compra/Venda direta no MT5
// ============================================================
(function () {
  "use strict";

  var el = function (id) { return document.getElementById(id); };

  // Estado do último sinal recebido (preenchido pelo Monitor MT5)
  window._lastMT5Signal = window._lastMT5Signal || null;

  // Elementos
  var buyBtn     = el("manual-buy-btn");
  var sellBtn    = el("manual-sell-btn");
  var closeBtn   = el("manual-close-btn");
  var statusDiv  = el("manual-status");
  var overlay    = el("manual-confirm-overlay");
  var confirmOk  = el("manual-confirm-ok");
  var confirmCan = el("manual-confirm-cancel");

  if (!buyBtn || !sellBtn) return;  // painel não presente

  // ---- helpers ----
  function getSymbol() {
    var sel = el("mt5-instrument");
    return (sel && sel.value) || "BMFBOVESPA:WIN1!";
  }

  function getVolume() {
    var v = parseFloat((el("manual-volume") || {}).value || "1");
    return isNaN(v) || v < 1 ? 1 : v;
  }

  function getSL() {
    var useSignal = el("manual-use-signal") && el("manual-use-signal").checked;
    var manualVal = parseFloat((el("manual-sl") || {}).value || "");
    if (!isNaN(manualVal) && manualVal > 0) return manualVal;
    if (useSignal && window._lastMT5Signal && window._lastMT5Signal.stop)
      return window._lastMT5Signal.stop;
    return null;
  }

  function getTP1() {
    var useSignal = el("manual-use-signal") && el("manual-use-signal").checked;
    var manualVal = parseFloat((el("manual-tp1") || {}).value || "");
    if (!isNaN(manualVal) && manualVal > 0) return manualVal;
    if (useSignal && window._lastMT5Signal && window._lastMT5Signal.tp1)
      return window._lastMT5Signal.tp1;
    return null;
  }

  function setStatus(msg, color) {
    if (!statusDiv) return;
    statusDiv.textContent = msg;
    statusDiv.style.color = color || "var(--text-muted)";
  }

  function fmt(v) { return v ? parseFloat(v).toFixed(0) : "—"; }

  // ---- Modal de confirmação ----
  var _pendingAction = null;

  function showConfirm(acao, sl, tp1, volume, callback) {
    var title  = el("manual-confirm-title");
    var body   = el("manual-confirm-body");
    if (!overlay || !title || !body) { callback(); return; }

    var cor = acao === "COMPRA" ? "#16a34a" : acao === "VENDA" ? "#dc2626" : "#f59e0b";
    title.innerHTML = '<span style="color:' + cor + ';font-size:1.3rem">' +
      (acao === "COMPRA" ? "▲ COMPRA" : acao === "VENDA" ? "▼ VENDA" : "✕ FECHAR") +
      '</span>';

    if (acao === "FECHAR") {
      body.innerHTML = "Fechar <strong>todas as posições abertas</strong> em " + getSymbol() + "?";
    } else {
      body.innerHTML =
        "Símbolo: <strong>" + getSymbol() + "</strong><br>" +
        "Volume: <strong>" + volume + " mini(s)</strong><br>" +
        "Stop Loss: <strong>" + fmt(sl) + "</strong><br>" +
        "TP1: <strong>" + fmt(tp1) + "</strong><br><br>" +
        "<span style='color:#f59e0b;font-size:.8rem'>⚠️ Ordem executada diretamente no MT5</span>";
    }

    var okBtn = el("manual-confirm-ok");
    if (okBtn) okBtn.style.background = cor;
    overlay.style.display = "flex";
    _pendingAction = callback;
  }

  function hideConfirm() {
    if (overlay) overlay.style.display = "none";
    _pendingAction = null;
  }

  if (confirmOk)  confirmOk.addEventListener("click",  function () { if (_pendingAction) _pendingAction(); hideConfirm(); });
  if (confirmCan) confirmCan.addEventListener("click",  hideConfirm);
  if (overlay)    overlay.addEventListener("click", function (e) { if (e.target === overlay) hideConfirm(); });

  // ---- Execução ----
  function executeManual(acao) {
    var sym    = getSymbol();
    var vol    = getVolume();
    var sl     = getSL();
    var tp1    = getTP1();

    showConfirm(acao, sl, tp1, vol, function () {
      setStatus("Enviando ordem ao MT5...", "#f59e0b");
      [buyBtn, sellBtn, closeBtn].forEach(function (b) { if (b) b.disabled = true; });

      fetch("/api/trade/manual", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          tv_symbol: sym,
          acao:      acao,
          sl:        sl,
          tp1:       tp1,
          volume:    vol,
        }),
      })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.ok) {
            var price = data.result && data.result.price ? data.result.price.toFixed(0) : "—";
            setStatus("✅ " + acao + " executada @ " + price + " | Order #" + (data.result && data.result.order || "—"), "#16a34a");
            // Desenha linhas no gráfico LightweightCharts
            if (window._drawManualTradeLines && data.result) {
              window._drawManualTradeLines(acao, data.result.price, getSL(), getTP1());
            }
            // Ativa modo MANAGE — para análise de novos sinais enquanto trade está ativo
            if (window.enterManageMode)    window.enterManageMode();
            if (window.startManagePolling) window.startManagePolling(30);
            // Atualiza histórico de trades
            if (window.loadAutoTradesHistory) setTimeout(window.loadAutoTradesHistory, 800);
          } else {
            setStatus("❌ " + (data.error || "Erro desconhecido"), "#dc2626");
          }
        })
        .catch(function (err) {
          setStatus("❌ Erro de rede: " + err.message, "#dc2626");
        })
        .finally(function () {
          [buyBtn, sellBtn, closeBtn].forEach(function (b) { if (b) b.disabled = false; });
        });
    });
  }

  buyBtn.addEventListener("click",  function () { executeManual("COMPRA"); });
  sellBtn.addEventListener("click", function () { executeManual("VENDA"); });
  closeBtn.addEventListener("click", function () {
    showConfirm("FECHAR", null, null, null, function () {
      setStatus("Fechando posições...", "#f59e0b");
      [buyBtn, sellBtn, closeBtn].forEach(function (b) { if (b) b.disabled = true; });

      fetch("/api/trade/manual", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tv_symbol: getSymbol(), acao: "FECHAR" }),
      })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          setStatus(data.ok ? "✅ Posição fechada." : "❌ " + (data.error || "Erro"), data.ok ? "#16a34a" : "#dc2626");
          if (data.ok && window.loadAutoTradesHistory) setTimeout(window.loadAutoTradesHistory, 800);
        })
        .catch(function (err) { setStatus("❌ Erro: " + err.message, "#dc2626"); })
        .finally(function () { [buyBtn, sellBtn, closeBtn].forEach(function (b) { if (b) b.disabled = false; }); });
    });
  });

  // Pré-preenche SL/TP quando chega novo sinal do Monitor MT5
  // (hook chamado pelo fetchMt5 após renderizar sinal)
  window._onNewMT5Signal = function (signal) {
    window._lastMT5Signal = signal;
    var useSignal = el("manual-use-signal");
    if (!useSignal || !useSignal.checked) return;
    var slInput  = el("manual-sl");
    var tp1Input = el("manual-tp1");
    if (slInput  && signal && signal.stop) slInput.placeholder  = "≈ " + parseFloat(signal.stop).toFixed(0);
    if (tp1Input && signal && signal.tp1)  tp1Input.placeholder = "≈ " + parseFloat(signal.tp1).toFixed(0);
  };

}());
