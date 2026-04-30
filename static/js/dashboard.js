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
