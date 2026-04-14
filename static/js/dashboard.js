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

  function esc(s) {
    if (s == null || s === "") return "—";
    const d = document.createElement("div");
    d.textContent = String(s);
    return d.innerHTML;
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

  function fmtPnlDisplay(v) {
    if (v == null || v === "") return "";
    var n = Number(v);
    if (isNaN(n)) return String(v);
    return n.toLocaleString("pt-BR", { minimumFractionDigits: 0, maximumFractionDigits: 2 });
  }

  function renderJournalPanel(prefix, data, isBuy, isSell) {
    var wrap = document.getElementById(prefix + "trade-journal-wrap");
    if (!wrap) return;
    if (!isBuy && !isSell) {
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
        tfNote.textContent = obs;
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
    if (padraoPill) padraoPill.textContent = T && T.padrao ? T.padrao : "—";

    const entradaEl = $("trade-entrada");
    if (entradaEl) entradaEl.textContent = T && T.entrada ? T.entrada : "—";
    const stopTxt = T && (T.stop_em_pontos || T.stop) ? T.stop_em_pontos || T.stop : "—";
    const stopEl = $("trade-stop");
    if (stopEl) stopEl.textContent = stopTxt;
    const alvos = getAlvos(T);
    const tp1 = alvos[0];
    const tp1El = $("trade-tp1");
    if (tp1El) tp1El.textContent = tp1 ? tp1.distancia : T && T.alvo ? T.alvo : "—";
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
          '<span class="trade-tp-name">—</span><span>—</span><div class="trade-tp-barwrap"><div class="trade-tp-bar" style="width:0%"></div></div><span>—</span><span>—</span>';
        tpBody.appendChild(tr);
      } else {
        showAlvos.forEach(function (a) {
          const prob = Math.max(0, Math.min(100, Number(a.probabilidade) || 0));
          const row = document.createElement("div");
          row.className = "trade-tp-row";
          row.innerHTML =
            '<span class="trade-tp-name">' +
            esc(a.nome || "TP") +
            '</span><span>' +
            esc(a.distancia) +
            '</span><div class="trade-tp-barwrap"><div class="trade-tp-bar" style="width:' +
            prob +
            '%"></div></div><span>' +
            prob +
            '%</span><span style="color:#7dd3fc">' +
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
          li.textContent = x;
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
          li.textContent = x;
          ulR.appendChild(li);
        });
      } else {
        ulR.innerHTML = "<li>—</li>";
      }
    }

    const parts = [];
    if (T && T.justificativa) parts.push(T.justificativa);
    if (R && R.motivo) parts.push("Risk manager: " + R.motivo);
    const narr = $("trade-narrative-text");
    if (narr) narr.textContent = parts.length ? parts.join("\n\n") : "—";

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
    return {
      recorded: true,
      entry: en && en.value ? en.value.trim() : "",
      exit: ex && ex.value ? ex.value.trim() : "",
      pnl: pv === "" ? null : pv,
    };
  }

  function saveJournal(prefix) {
    var idEl = document.getElementById(prefix + "trade-journal-analysis-id");
    var statusEl = document.getElementById(prefix + "trade-journal-status");
    if (!idEl || !idEl.value) return;
    var id = Number(idEl.value);
    var payload = readJournalPayload(prefix);
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
    tr.innerHTML =
      "<td>" +
      escapeHtml((row.created_at || "").slice(0, 19).replace("T", " ")) +
      '</td><td class="thumb"><img class="thumb" src="' +
      escapeHtml(row.image_url || "") +
      '" alt="" width="56" height="56" loading="lazy" /></td><td>' +
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
})();
