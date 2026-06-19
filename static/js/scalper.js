/**
 * scalper.js — Frontend do módulo Scalper (TradeAI)
 * Totalmente isolado do dashboard principal.
 *
 * Fluxo principal:
 *  1. Poll /api/scalper/data  a cada 500ms  → book, agressão, tape, tick
 *  2. Poll /api/scalper/position a cada 800ms → P&L da posição aberta
 *  3. Quando auto habilitado: envia /api/scalper/auto-check a cada ciclo de dados
 *  4. Funções globais: toggleAuto(), manualTrade(), manualClose(), resetSession()
 */

/* ─────────────────────────────────────────────────────────────────────────────
   Estado interno
───────────────────────────────────────────────────────────────────────────── */
var _symbol       = "WDON26";
var _lastPrice    = null;
var _autoEnabled  = false;
var _confirmCount = 0;
var _confirmMax   = 3;
var _hasPosition  = false;
var _posEntry     = null;
var _posProfit    = 0;
var _posTicket    = null;
var _dataTimer    = null;
var _posTimer     = null;
var _sessionTimer = null;

// Último sinal calculado no frontend (para envio ao auto-check)
var _currentSignal   = "NEUTRO";
var _currentBuyPct   = 50;
var _currentSellPct  = 50;

// Contexto multi-fator (atualizado a cada poll de dados)
var _ctx = {
  velocity:   {trades_3s: 0, trades_10s: 0, vol_3s: 0, fast: false,
               tape_trend: "NEUTRO", last5_buy_pct: 50, accelerating: false},
  aggr_5s:    {buy_pct: 50, sell_pct: 50},
  aggr_10s:   {buy_pct: 50, sell_pct: 50},
  book:       {bids_vol: 0, asks_vol: 0},
  score:      0,       // 0-5: quantos filtros passaram
  reasons:    [],      // motivos de bloqueio
};

// Para detectar fechamento via TP/SL
var _lastPosTicket = null;

// Modo simulação
var _simMode = false;

// Volatilidade do mercado
var _volatilityOk   = true;   // false = mercado lateral, bloqueia auto-trade

// Break-even e saída por tempo
var _posOpenTime        = null;   // timestamp (ms) de quando a posição foi aberta
var _breakEvenTriggered = false;  // flag: break-even ja foi aplicado nesta posição

/* ─────────────────────────────────────────────────────────────────────────────
   Utilitários
───────────────────────────────────────────────────────────────────────────── */
function _fmt(n, dec) {
  if (n == null || isNaN(n)) return "—";
  return n.toFixed(dec != null ? dec : 2);
}
function _fmtBRL(n) {
  if (n == null || isNaN(n)) return "R$—";
  var abs = Math.abs(n).toFixed(2).replace(".", ",");
  return (n < 0 ? "-" : "") + "R$" + abs;
}
function _fmtTime(isoOrUnix) {
  try {
    var d = (typeof isoOrUnix === "number")
      ? new Date(isoOrUnix * 1000)
      : new Date(isoOrUnix);
    return d.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  } catch (e) { return "—"; }
}
function _setStatus(msg) {
  var el = document.getElementById("status-msg");
  if (el) el.textContent = msg;
}
function _cfgNum(id) {
  var el = document.getElementById(id);
  return el ? parseFloat(el.value) || 0 : 0;
}

/* ─────────────────────────────────────────────────────────────────────────────
   Gauge — agulha semicircular
   buy_pct: 0% → rotate(-90°, 120,120) = aponta esq (VENDA)
            50% → rotate(0°)           = aponta cima (NEUTRO)
           100% → rotate(+90°)         = aponta dir  (COMPRA)
───────────────────────────────────────────────────────────────────────────── */
function _updateGauge(buyPct) {
  var deg = (buyPct - 50) * 1.8;  // -90 a +90
  var needle = document.getElementById("gauge-needle");
  if (needle) needle.setAttribute("transform", "rotate(" + deg + ", 120, 120)");

  // Preenche o arco: total = 314 (comprimento do semiarco π*100)
  // dashoffset vai de 314 (vazio) a 0 (cheio). buyPct=0 → offset=314, buyPct=100 → 0
  var arc = document.getElementById("gauge-arc");
  if (arc) {
    var offset = 314 - (buyPct / 100) * 314;
    arc.setAttribute("stroke-dashoffset", offset.toFixed(1));
  }

  var label = document.getElementById("gauge-label");
  if (label) label.textContent = "Agressão compradora: " + buyPct.toFixed(1) + "%";
}

/* ─────────────────────────────────────────────────────────────────────────────
   Signal badge + confirm bar
───────────────────────────────────────────────────────────────────────────── */
function _updateSignalBadge(signal) {
  var el = document.getElementById("signal-badge");
  if (!el) return;
  el.className = "NEUTRO";
  el.textContent = "AGUARDANDO";
  if (signal === "COMPRA") { el.className = "COMPRA"; el.textContent = "▲ COMPRA"; }
  else if (signal === "VENDA") { el.className = "VENDA"; el.textContent = "▼ VENDA"; }
}

function _updateConfirmBar(count, maxN) {
  var label = document.getElementById("confirm-label");
  var bar   = document.getElementById("confirm-bar");
  if (label) label.textContent = "Confirmação: " + count + "/" + maxN;
  if (bar)   bar.style.width   = (maxN > 0 ? (count / maxN) * 100 : 0) + "%";
}

/* ─────────────────────────────────────────────────────────────────────────────
   Book de ordens
───────────────────────────────────────────────────────────────────────────── */
function _renderBook(book) {
  if (!book) return;

  var asksEl  = document.getElementById("book-asks");
  var bidsEl  = document.getElementById("book-bids");
  var spreadEl = document.getElementById("book-spread");
  if (!asksEl || !bidsEl) return;

  // Spread
  if (spreadEl) {
    var sp = book.spread != null ? _fmt(book.spread, 2) : "—";
    spreadEl.textContent = "Spread: " + sp;
  }

  // ASKs (inverter para maior ask no topo → menor ask perto do spread)
  var asks = (book.asks || []).slice().reverse();
  asksEl.innerHTML = asks.map(function (r) {
    var w = Math.min(100, r.bar_pct != null ? r.bar_pct : 0);
    return '<div class="book-row ask">'
      + '<div class="book-bar" style="width:' + w + '%"></div>'
      + '<span class="book-price">' + _fmt(r.price, 2) + '</span>'
      + '<span class="book-vol">' + (r.vol || 0) + '</span>'
      + '</div>';
  }).join("");

  // BIDs (maior bid no topo, mais próximo do spread)
  var bids = book.bids || [];
  bidsEl.innerHTML = bids.map(function (r) {
    var w = Math.min(100, r.bar_pct != null ? r.bar_pct : 0);
    return '<div class="book-row bid">'
      + '<div class="book-bar" style="width:' + w + '%"></div>'
      + '<span class="book-price">' + _fmt(r.price, 2) + '</span>'
      + '<span class="book-vol">' + (r.vol || 0) + '</span>'
      + '</div>';
  }).join("");
}

/* ─────────────────────────────────────────────────────────────────────────────
   Agressão
───────────────────────────────────────────────────────────────────────────── */
function _renderAggr(aggr, aggrSec) {
  if (!aggr) return;
  var buyPct  = aggr.buy_pct  != null ? aggr.buy_pct  : 50;
  var sellPct = aggr.sell_pct != null ? aggr.sell_pct : 50;
  var buyVol  = aggr.buy_vol  || 0;
  var sellVol = aggr.sell_vol || 0;
  var total   = aggr.total    || 0;

  _set("buy-pct-txt",  buyPct.toFixed(1)  + "%");
  _set("sell-pct-txt", sellPct.toFixed(1) + "%");
  _set("buy-vol-txt",  buyVol);
  _set("sell-vol-txt", sellVol);
  _set("aggr-sec",     aggrSec || 30);

  // Mostrar velocidade do tape no contador de negócios
  var vel     = _ctx.velocity.trades_3s || 0;
  var velInfo = vel > 0 ? (" | ⚡" + vel + "/3s") : "";
  _set("aggr-total", "Negócios: " + total + velInfo);

  var buyBar  = document.getElementById("buy-bar");
  var sellBar = document.getElementById("sell-bar");
  if (buyBar)  buyBar.style.width  = buyPct  + "%";
  if (sellBar) sellBar.style.width = sellPct + "%";

  _currentBuyPct  = buyPct;
  _currentSellPct = sellPct;

  // ── Estratégia: Spike de Aceleração Multi-fator ─────────────────────────
  // Lógica: detectar INÍCIO do movimento (aggr5s acelerando > aggr30s)
  // não mais o nível sustentado (que indica fim do movimento)
  var threshold   = _cfgNum("cfg-threshold") || 65;
  var velMin      = _cfgNum("cfg-vel-min")   || 0;
  var accelDelta  = 8;   // aggr5s precisa ser >= aggr30s + 8% (aceleração)

  var signal   = "NEUTRO";
  var reasons  = [];
  var score    = 0;

  // Dados dos três janelas temporais
  var buy5    = _ctx.aggr_5s.buy_pct   || 50;
  var sell5   = _ctx.aggr_5s.sell_pct  || 50;
  var buy10   = _ctx.aggr_10s.buy_pct  || 50;
  var sell10  = _ctx.aggr_10s.sell_pct || 50;

  // Determinar direção pelo spike RECENTE (5s), não pela média longa
  var direction = "NEUTRO";
  if (buy5 >= threshold && buy5 > sell5) direction = "COMPRA";
  else if (sell5 >= threshold && sell5 > buy5) direction = "VENDA";

  if (direction !== "NEUTRO") {

    // ── Filtro 1: Spike imediato — aggr5s >= threshold ──────────────────
    score++;  // já passou (é condição para entrar no if)

    // ── Filtro 2: Aceleração — aggr5s supera aggr30s por margem ─────────
    var dir5    = direction === "COMPRA" ? buy5   : sell5;
    var dir30   = direction === "COMPRA" ? buyPct : sellPct;
    var dir10   = direction === "COMPRA" ? buy10  : sell10;
    // Verifica que o momentum está subindo (5s > 10s)
    if (dir5 >= dir30 + accelDelta && dir5 >= dir10) {
      score++;  // Aceleração confirmada: fluxo crescendo, não esfriando
    } else {
      reasons.push("sem aceleração (5s=" + dir5.toFixed(0) + "% vs 30s=" + dir30.toFixed(0) + "%)");
    }

    // ── Filtro 3: Tape trend — maioria dos últimos 5 negócios alinhada ──
    var tapeTrend   = _ctx.velocity.tape_trend   || "NEUTRO";
    var l5BuyPct    = _ctx.velocity.last5_buy_pct || 50;
    var tapeAligned = (direction === "COMPRA" && l5BuyPct >= 60) ||
                      (direction === "VENDA"  && l5BuyPct <= 40);
    if (tapeAligned) {
      score++;  // Tape recente confirma direção
    } else {
      reasons.push("tape divergente (" + l5BuyPct.toFixed(0) + "% C nos últimos 5)");
    }

    // ── Filtro 4: Velocidade — tape ativo (fluxo institucional) ─────────
    if (velMin <= 0 || _ctx.velocity.trades_3s >= velMin) {
      score++;  // Tape com volume suficiente
    } else {
      reasons.push("tape lento (" + _ctx.velocity.trades_3s + "/" + velMin + " neg/3s)");
    }

    // ── Filtro 5: Book — não oposto à direção ────────────────────────────
    var bidsVol   = _ctx.book.bids_vol || 0;
    var asksVol   = _ctx.book.asks_vol || 0;
    var bookTotal = bidsVol + asksVol;
    var bookOk    = true;
    if (bookTotal > 0) {
      var bookBuyPct = bidsVol / bookTotal * 100;
      // Apenas bloqueia se o book estiver fortemente contra (> 60% oposto)
      if (direction === "COMPRA" && bookBuyPct < 40) {
        bookOk = false;
        reasons.push("book contra compra (" + bookBuyPct.toFixed(0) + "% bid)");
      } else if (direction === "VENDA" && bookBuyPct > 60) {
        bookOk = false;
        reasons.push("book contra venda (" + bookBuyPct.toFixed(0) + "% bid)");
      }
    }
    if (bookOk) score++;

    // ── Sinal válido: score >= 70/100 (ou fallback para filtros locais) ──
    var minScore = (bookTotal > 0) ? 5 : 4;
    var scoreMin = _cfgNum("cfg-score-min") || 70;
  var scoreOk  = (_macroScore > 0) ? (_macroScore >= scoreMin) : (score >= minScore);
    if (scoreOk) signal = direction;
  }

  _ctx.score   = score;
  _ctx.reasons = reasons;
  _currentSignal = signal;

  _updateGauge(buyPct);
  _updateSignalBadge(signal);

  // Feedback visual
  if (direction !== "NEUTRO" && signal === "NEUTRO" && reasons.length > 0) {
    var confEl = document.getElementById("confirm-label");
    if (confEl) {
      var scoreTxt = _macroScore > 0 ? ("Score " + Math.round(_macroScore) + "/100 — ") : "";
      confEl.textContent = scoreTxt + reasons[0];
    }
  }
}

function _set(id, val) {
  var el = document.getElementById(id);
  if (el) el.textContent = val;
}

/* ─────────────────────────────────────────────────────────────────────────────
   Tape
───────────────────────────────────────────────────────────────────────────── */
var _tapeEntries = [];
var _tapeMax     = 80;

function _renderTape(tape) {
  if (!Array.isArray(tape) || tape.length === 0) return;
  var tapeEl = document.getElementById("tape-list");
  if (!tapeEl) return;

  // Prepend novos (tape já vem ordenado mais recente primeiro do service)
  tape.forEach(function (t) {
    var side = (t.side || "").toUpperCase();
    _tapeEntries.unshift({
      time:  t.time  ? _fmtTime(t.time)  : "—",
      price: _fmt(t.price, 2),
      vol:   t.vol   || 0,
      side:  side === "BUY" ? "C" : "V",
      cls:   side === "BUY" ? "BUY" : "SELL",
    });
  });

  // Manter tamanho máximo
  if (_tapeEntries.length > _tapeMax) _tapeEntries = _tapeEntries.slice(0, _tapeMax);

  tapeEl.innerHTML = _tapeEntries.map(function (e) {
    return '<div class="tape-row ' + e.cls + '">'
      + '<span class="tape-time">'  + e.time  + '</span>'
      + '<span class="tape-price">' + e.price + '</span>'
      + '<span class="tape-vol">'   + e.vol   + '</span>'
      + '<span class="tape-side">'  + e.side  + '</span>'
      + '</div>';
  }).join("");
}

/* ─────────────────────────────────────────────────────────────────────────────
   Preço principal
───────────────────────────────────────────────────────────────────────────── */
function _updatePrice(tick) {
  if (!tick) return;
  var priceEl = document.getElementById("price-display");
  var last    = tick.last != null ? tick.last : null;

  if (last != null && priceEl) {
    var cls = "flat";
    if (_lastPrice != null) {
      if (last > _lastPrice) cls = "up";
      else if (last < _lastPrice) cls = "down";
    }
    _lastPrice     = last;
    priceEl.className = cls;
    priceEl.textContent = _fmt(last, 2);
  }

  _set("bid-val", _fmt(tick.bid, 2));
  _set("ask-val", _fmt(tick.ask, 2));
}

/* ─────────────────────────────────────────────────────────────────────────────
   Connection indicator
───────────────────────────────────────────────────────────────────────────── */
function _setConn(ok, msg) {
  var dot = document.getElementById("conn-dot");
  var txt = document.getElementById("conn-txt");
  if (dot) { dot.className = "conn-dot " + (ok ? "ok" : "err"); }
  if (txt) txt.textContent = msg || (ok ? "Conectado" : "Erro");
}

/* ─────────────────────────────────────────────────────────────────────────────
   B3 / Forex status badge
───────────────────────────────────────────────────────────────────────────── */
var _FOREX_SYMBOLS = { "XAUUSD": 1, "EURUSD": 1, "USDBRL": 1, "GBPUSD": 1, "BTCUSD": 1 };

function _setB3Badge(open) {
  var el = document.getElementById("b3-badge");
  if (!el) return;
  if (_simMode) {
    el.className   = "top-badge open";
    el.textContent = "SIMULAÇÃO";
    return;
  }
  el.className = "top-badge " + (open ? "open" : "close");
  var isForex = !!_FOREX_SYMBOLS[_symbol];
  if (open) {
    el.textContent = isForex ? "FOREX ABERTO" : "B3 ABERTA";
  } else {
    el.textContent = isForex ? "FOREX FECHADO" : "FECHADO";
  }
}

/* ─────────────────────────────────────────────────────────────────────────────
   Auto-check: envia sinal ao servidor para avaliação de entrada
───────────────────────────────────────────────────────────────────────────── */
function _sendAutoCheck(b3Open) {
  if (!_autoEnabled) return;

  // Hard-block: não opera em horário PERIGOSO (12-14h)
  if (_sessionBlock) {
    var reasonEl = document.getElementById("auto-reason");
    if (reasonEl) reasonEl.textContent = "⚠ Mercado fechado — fora do horário B3";
    return;
  }

  var payload = {
    symbol:       _symbol,
    signal:       _currentSignal,
    buy_pct:      _currentBuyPct,
    sell_pct:     _currentSellPct,
    volume:       _cfgNum("cfg-volume")    || 100,
    tp_ticks:       _cfgNum("cfg-tp")           || 5,
    sl_ticks:       _cfgNum("cfg-sl")           || 2,
    use_atr_sizing: document.getElementById("cfg-atr-sizing")
                      ? document.getElementById("cfg-atr-sizing").checked
                      : true,
    threshold_pct:_cfgNum("cfg-threshold") || 65,
    cooldown_sec: _cfgNum("cfg-cooldown")  || 30,
    confirm_n:    _cfgNum("cfg-confirm")   || 3,
    b3_open:      b3Open,
    score_min:    _cfgNum("cfg-score-min") || 75,
    max_daily:    _cfgNum("cfg-max-daily")  || 15,
    use_vwap_filter: document.getElementById("cfg-vwap-filter")
                       ? document.getElementById("cfg-vwap-filter").checked
                       : true,
  };

  fetch("/api/scalper/auto-check", {
    method:  "POST",
    headers: { "Content-Type": "application/json" },
    body:    JSON.stringify(payload),
  })
  .then(function (r) { return r.json(); })
  .then(function (d) {
    _confirmMax   = parseInt(_cfgNum("cfg-confirm")) || 3;
    _confirmCount = d.confirm != null ? parseInt(d.confirm) : 0;
    _updateConfirmBar(_confirmCount, _confirmMax);

    var reasonEl = document.getElementById("auto-reason");
    if (reasonEl) reasonEl.textContent = d.reason || "";

    if (d.action === "EXECUTED") {
      _setStatus("✅ Auto-trade executado: " + (d.acao || "") + " " + _symbol);
      // Marcar posição imediatamente — não esperar o poll de 800ms
      if (d.result && d.result.order) {
        _hasPosition        = true;
        _lastPosTicket      = d.result.order;
        _posProfit          = 0;
        _posOpenTime        = Date.now();
        _breakEvenTriggered = false;
      }
    } else if (d.action === "FAILED") {
      _setStatus("⚠️ Auto-trade falhou: " + (d.error || "erro desconhecido"));
    }
  })
  .catch(function (e) { /* falha silenciosa */ });
}

/* ─────────────────────────────────────────────────────────────────────────────
   Poll de dados de mercado (500ms)
───────────────────────────────────────────────────────────────────────────── */
/* ─────────────────────────────────────────────────────────────────────────────
   Filtro de volatilidade — atualiza badge e flag global
───────────────────────────────────────────────────────────────────────────── */
function _updateVolatility(vol) {
  if (!vol) return;
  var minTicks = parseInt((_cfgNum("cfg-vol-min") || 2));
  var badge    = document.getElementById("volatility-badge");

  if (minTicks <= 0) {
    // Filtro desativado
    _volatilityOk = true;
    if (badge) badge.style.display = "none";
    return;
  }

  var rangeTicks = vol.range_ticks || 0;
  _volatilityOk  = rangeTicks >= minTicks;

  if (badge) {
    if (_volatilityOk) {
      badge.style.display = "none";
    } else {
      badge.style.display = "block";
      badge.textContent   = "⚠ MERCADO LATERAL (" + rangeTicks.toFixed(1) + " tick) — auto-trade bloqueado";
    }
  }
}

/* ─────────────────────────────────────────────────────────────────────────────
   Painel Fluxo do Dia — agressão acumulada + probabilidade + OHLC
───────────────────────────────────────────────────────────────────────────── */
function _updateFlowDay(macro) {
  if (!macro) return;

  // ── 1. Agressão acumulada do dia (cum_delta) ────────────────────────────
  var cum     = macro.cum_delta || {};
  var buyPct  = typeof cum.buy_pct  === "number" ? cum.buy_pct  : 50;
  var sellPct = 100 - buyPct;
  var delta   = typeof cum.delta    === "number" ? cum.delta    : 0;
  var bias    = cum.bias || "NEUTRO";

  var fdBuyBar  = document.getElementById("fd-buy-bar");
  var fdSellBar = document.getElementById("fd-sell-bar");
  var fdBuyPct  = document.getElementById("fd-buy-pct");
  var fdSellPct = document.getElementById("fd-sell-pct");
  var fdSaldo   = document.getElementById("fd-saldo");

  if (fdBuyBar)  fdBuyBar.style.width  = buyPct.toFixed(1)  + "%";
  if (fdSellBar) fdSellBar.style.width = sellPct.toFixed(1) + "%";
  if (fdBuyPct)  fdBuyPct.textContent  = buyPct.toFixed(1)  + "%";
  if (fdSellPct) fdSellPct.textContent = sellPct.toFixed(1) + "%";
  if (fdSaldo) {
    var saldoSign = delta >= 0 ? "▲" : "▼";
    fdSaldo.textContent = "Saldo: " + (delta >= 0 ? "+" : "") + Math.round(delta) + " lots " + saldoSign;
    fdSaldo.className = "fd-saldo " + (bias === "BULLISH" ? "bull" : bias === "BEARISH" ? "bear" : "neutro");
  }

  // ── 2. Probabilidade direcional (score_buy vs score_sell) ──────────────
  var sb = (macro.score_buy  || {}).score || 0;
  var ss = (macro.score_sell || {}).score || 0;
  // Combina score com cum_delta (70% score, 30% delta)
  var scoreBuyNorm  = sb / Math.max(sb + ss, 1) * 100;
  var deltaAdj      = (buyPct - 50) * 0.3;   // max ±15%
  var buyProb       = Math.max(5, Math.min(95, Math.round(scoreBuyNorm * 0.7 + 50 * 0.3 + deltaAdj)));
  var sellProb      = 100 - buyProb;

  var fdProbFill  = document.getElementById("fd-prob-fill");
  var fdProbBuy   = document.getElementById("fd-prob-buy");
  var fdProbSell  = document.getElementById("fd-prob-sell");
  var fdProbLabel = document.getElementById("fd-prob-label");

  if (fdProbFill) fdProbFill.style.width = buyProb + "%";
  if (fdProbBuy)  fdProbBuy.textContent  = buyProb  + "%";
  if (fdProbSell) fdProbSell.textContent = sellProb + "%";
  if (fdProbLabel) {
    if      (buyProb >= 55) { fdProbLabel.textContent = "COMPRA"; fdProbLabel.className = "fd-prob-label buy";  }
    else if (buyProb <= 45) { fdProbLabel.textContent = "VENDA";  fdProbLabel.className = "fd-prob-label sell"; }
    else                    { fdProbLabel.textContent = "NEUTRO"; fdProbLabel.className = "fd-prob-label neutro"; }
  }

  // ── 3. Valores do Dia (via vwap data que agora inclui day_open etc.) ───
  var vwap = macro.vwap || {};
  var fmtPts = function(v) {
    if (v == null || isNaN(v)) return "—";
    return (v >= 0 ? "+" : "") + Math.round(v) + " pts";
  };

  var elOpen    = document.getElementById("fd-day-open");
  var elHigh    = document.getElementById("fd-day-high");
  var elLow     = document.getElementById("fd-day-low");
  var elAmp     = document.getElementById("fd-day-amp");
  var elDistO   = document.getElementById("fd-day-dist-open");
  var elDistM   = document.getElementById("fd-day-dist-min");

  if (elOpen) elOpen.textContent = vwap.day_open  != null ? vwap.day_open.toFixed(2)  : "—";
  if (elHigh) elHigh.textContent = vwap.day_high  != null ? vwap.day_high.toFixed(2)  : "—";
  if (elLow)  elLow.textContent  = vwap.day_low   != null ? vwap.day_low.toFixed(2)   : "—";
  if (elAmp)  elAmp.textContent  = vwap.day_amplitude != null
    ? Math.round(vwap.day_amplitude) + " pts" : "—";

  if (elDistO) {
    var dOpen = vwap.day_dist_open;
    elDistO.textContent = fmtPts(dOpen);
    elDistO.className   = "fd-day-val " + (dOpen != null && dOpen >= 0 ? "buy" : "sell");
  }
  if (elDistM) {
    var dMin = vwap.day_dist_min;
    elDistM.textContent = dMin != null ? "+" + Math.round(dMin) + " pts" : "—";
    elDistM.className   = "fd-day-val buy";
  }
}

function _pollData() {
  var aggrSec = _cfgNum("cfg-cooldown");  // reutiliza cooldown como window? Não —
  // O aggr_seconds é separado; vamos fixar 30s ou ler de um campo futuro
  var aggrSeconds = 30;

  fetch("/api/scalper/data?symbol=" + encodeURIComponent(_symbol) + "&aggr_seconds=" + aggrSeconds)
  .then(function (r) { return r.json(); })
  .then(function (d) {
    if (!d.ok) {
      _setConn(false, "MT5 off");
      _setStatus("⚠️ " + (d.error || "Erro de dados"));
      return;
    }
    _setConn(true, "Conectado");
    _setB3Badge(!!d.b3_open);
    _updatePrice(d.tick);
    _renderBook(d.book);

    // ── Atualizar _ctx ANTES de _renderAggr para filtros usarem dados frescos ──
    if (d.context) {
      if (d.context.velocity) _ctx.velocity = d.context.velocity;
      if (d.context.aggr_5s)  _ctx.aggr_5s  = d.context.aggr_5s;
      if (d.context.aggr_10s) _ctx.aggr_10s = d.context.aggr_10s;
    }
    if (d.book) {
      var bidsVol = 0, asksVol = 0;
      (d.book.bids || []).slice(0, 3).forEach(function(b){ bidsVol += b.vol || 0; });
      (d.book.asks || []).slice(0, 3).forEach(function(a){ asksVol += a.vol || 0; });
      _ctx.book = {bids_vol: bidsVol, asks_vol: asksVol};
    }

    _renderAggr(d.aggr, aggrSeconds);
    _renderTape(d.tape);
    _updateVolatility(d.volatility);

    // ── Macro: VWAP, Delta, Score, Absorção, Horário ─────────────────────
    if (d.macro) _updateMacroBar(d.macro);

    // ── Fluxo do Dia: agressão acumulada + probabilidade + valores diários ──
    if (d.macro) _updateFlowDay(d.macro);

    if (_autoEnabled && !_hasPosition && _volatilityOk) {
      _sendAutoCheck(!!d.b3_open);
    } else if (!_autoEnabled) {
      _updateConfirmBar(0, parseInt(_cfgNum("cfg-confirm")) || 3);
    }
  })
  .catch(function (e) {
    _setConn(false, "Sem resposta");
    _setStatus("⚠️ Falha de rede");
  });
}

/* ─────────────────────────────────────────────────────────────────────────────
   Macro bar — VWAP, ATR, Cumulative Delta, Absorção, Score
───────────────────────────────────────────────────────────────────────────── */
var _macroScore    = 0;   // score atual (0-100) para uso no auto-check
/* ─────────────────────────────────────────────────────────────────────────────
   Auto-params por sessão — ajuste automático de filtros conforme horário
   PRIME    9-10:30  → filtros exigentes (mercado forte, institutional)
   BOM manhã10:30-12 → filtros médios
   BOM tarde14-16:30 → abertura EUA às 14:30 → moves fortes → mais aberto
   FECHAMENTO16:30-17→ conservador
───────────────────────────────────────────────────────────────────────────── */
// ── BURST DETECTION presets ─────────────────────────────────────────────────
// Nova filosofia: confirm_n=1 (burst é instantâneo, não espera confirmação),
// cooldown=60s (menos trades, mais qualidade), score_min calibrado para burst.
//
// Score burst interpretation:
//   <50  = sem burst (não opera)
//   50-65= burst fraco (opera apenas em PRIME)
//   65-80= burst moderado (opera em BOM e PRIME)
//   80+  = burst forte (opera em qualquer sessão)
var _SESSION_PRESETS = {
  // PRIME 9-10:30: máxima liquidez — melhor janela do dia
  PRIME: {
    "cfg-score-min": 62, "cfg-threshold": 63,
    "cfg-vel-min": 2,    "cfg-vol-min": 1,  "cfg-cooldown": 55, "cfg-confirm": 1
  },
  // BOM: bom momentum — cooldown maior evita churn em cluster de trades
  BOM: function () {
    var h = new Date().getHours();
    return h >= 14
      ? { "cfg-score-min": 68, "cfg-threshold": 63, "cfg-vel-min": 2, "cfg-vol-min": 1, "cfg-cooldown": 60, "cfg-confirm": 1 }
      : { "cfg-score-min": 68, "cfg-threshold": 63, "cfg-vel-min": 2, "cfg-vol-min": 1, "cfg-cooldown": 60, "cfg-confirm": 1 };
  },
  // PERIGOSO 12-14h: lateralização típica — exige sinal muito claro, cooldown longo
  PERIGOSO: {
    "cfg-score-min": 73, "cfg-threshold": 67,
    "cfg-vel-min": 3,    "cfg-vol-min": 2,  "cfg-cooldown": 80, "cfg-confirm": 1
  },
  // FECHAMENTO 16:30-17h: bursts de encerramento — cuidado com reversões rápidas
  FECHAMENTO: {
    "cfg-score-min": 70, "cfg-threshold": 65,
    "cfg-vel-min": 3,    "cfg-vol-min": 1,  "cfg-cooldown": 60, "cfg-confirm": 1
  }
  // FECHADO: fora do horario B3 — _sessionBlock bloqueia
};
var _autoParamsEnabled = true;  // auto-params ligado por padrao
var _lastAutoSession   = null;  // sessao em que os params foram aplicados

var _sessionBlock = false; // true quando sessão é PERIGOSO (bloqueia auto-trade)

function _updateMacroBar(macro) {
  if (!macro) return;

  // VWAP
  var vwap    = macro.vwap    || {};
  var vwapVal = document.getElementById("vwap-val");
  var vwapBdg = document.getElementById("vwap-badge");
  if (vwapVal) vwapVal.textContent = vwap.vwap ? _fmt(vwap.vwap, 2) : "—";
  if (vwapBdg) {
    var ctx = vwap.context || "NEUTRO";
    vwapBdg.className = ctx;
    vwapBdg.textContent = ctx === "BULL" ? "▲ BULL" : ctx === "BEAR" ? "▼ BEAR" : "~ NEUTRO";
  }

  // ATR
  var atrEl = document.getElementById("atr-val");
  if (atrEl) atrEl.textContent = vwap.atr_1m != null ? _fmt(vwap.atr_1m, 1) : "—";

  // Cumulative Delta
  var cd      = macro.cum_delta || {};
  var cdEl    = document.getElementById("cum-delta-val");
  var cdBias  = document.getElementById("cum-delta-bias");
  if (cdEl) {
    var dv = cd.delta || 0;
    cdEl.textContent = (dv >= 0 ? "+" : "") + Math.round(dv);
    cdEl.style.color = dv > 0 ? "var(--buy)" : dv < 0 ? "var(--sell)" : "var(--text)";
  }
  if (cdBias) {
    var bias = cd.bias || "NEUTRO";
    cdBias.textContent = bias;
    cdBias.style.color = bias === "BULLISH" ? "var(--buy)" : bias === "BEARISH" ? "var(--sell)" : "var(--muted)";
  }

  // Fluxo (Absorção/Exaustão)
  var flow   = macro.flow   || {};
  var flowEl = document.getElementById("flow-signal");
  if (flowEl) {
    var parts = [];
    if (flow.absorption_buy)  parts.push("Absorção C");
    if (flow.absorption_sell) parts.push("Absorção V");
    if (flow.exhaustion_buy)  parts.push("Exaustão C");
    if (flow.exhaustion_sell) parts.push("Exaustão V");
    flowEl.textContent = parts.length ? parts.join(" | ") : "—";
    flowEl.style.color = flow.absorption_buy || flow.exhaustion_sell ? "var(--buy)"
                       : flow.absorption_sell || flow.exhaustion_buy ? "var(--sell)"
                       : "var(--muted)";
  }

  // Horário / Sessão
  var timeW   = macro.time  || {};
  var sessBdg = document.getElementById("session-badge");
  if (sessBdg) {
    sessBdg.className   = timeW.session || "FECHADO";
    sessBdg.textContent = timeW.label   || "—";
  }
  // Hard-block: mercado fechado E horário PERIGOSO (12-14h) — lateralização, sem edge
  _sessionBlock = (!timeW.session || timeW.session === "FECHADO" || timeW.session === "PERIGOSO");
  // Auto-params: aplica preset quando a sessao muda
  if (_autoParamsEnabled && timeW.session && timeW.session !== _lastAutoSession) {
    _applySessionParams(timeW.session);
  }

  // Score (usar o maior entre score_buy e score_sell, baseado no sinal atual)
  var scoreBuy  = (macro.score_buy  || {}).score || 0;
  var scoreSell = (macro.score_sell || {}).score || 0;
  var scoreActive = (_currentSignal === "COMPRA") ? scoreBuy
                  : (_currentSignal === "VENDA")  ? scoreSell
                  : Math.max(scoreBuy, scoreSell);
  _macroScore = scoreActive;

  var scoreLbl = document.getElementById("score-label");
  var scoreBar = document.getElementById("score-bar");
  if (scoreLbl) {
    scoreLbl.textContent = Math.round(scoreActive) + "/100";
    scoreLbl.style.color = scoreActive >= 70 ? "var(--buy)"
                         : scoreActive >= 50 ? "var(--gold)"
                         : "var(--sell)";
  }
  if (scoreBar) {
    scoreBar.style.width      = Math.min(100, scoreActive) + "%";
    scoreBar.style.background = scoreActive >= 70 ? "var(--buy)"
                               : scoreActive >= 50 ? "var(--gold)"
                               : "var(--sell)";
  }

  // Atualizar razões no confirm-label se houver bloqueio
  var activeScore = _currentSignal === "COMPRA" ? macro.score_buy
                  : _currentSignal === "VENDA"  ? macro.score_sell : null;
  if (activeScore && activeScore.reasons && activeScore.reasons.length > 0 && _currentSignal === "NEUTRO") {
    var confEl = document.getElementById("confirm-label");
    if (confEl) confEl.textContent = "Score: " + Math.round(scoreActive) + " — " + activeScore.reasons[0];
  }
}
function _applySessionParams(session) {
  if (!_autoParamsEnabled) return;
  var preset = _SESSION_PRESETS[session];
  if (!preset) return;                   // sem preset para esta sessao (PERIGOSO, FECHADO)
  if (typeof preset === "function") preset = preset();
  Object.keys(preset).forEach(function (id) {
    var el = document.getElementById(id);
    if (el) el.value = preset[id];
  });
  _lastAutoSession = session;
  // atualiza badge no statusbar
  var btn = document.getElementById("btn-auto-params");
  if (btn && _autoParamsEnabled) {
    btn.textContent = "\u2699 Auto: " + session;
    btn.className = "ap-btn-active";
  }
}


/* ─────────────────────────────────────────────────────────────────────────────
   Busca P&L real do histórico MT5 (pelo ticket) e registra fechamento
───────────────────────────────────────────────────────────────────────────── */
function _fetchRealProfitAndClose(ticket, fallbackProfit) {
  fetch("/api/scalper/last-deal?ticket=" + ticket)
  .then(function (r) { return r.json(); })
  .then(function (d) {
    var profit = (d.ok && d.found && d.profit != null) ? d.profit : fallbackProfit;
    _onPositionClosed(ticket, profit);
  })
  .catch(function () {
    _onPositionClosed(ticket, fallbackProfit);
  });
}

/* ─────────────────────────────────────────────────────────────────────────────
   Poll de posição aberta (800ms)
───────────────────────────────────────────────────────────────────────────── */
function _pollPosition() {
  fetch("/api/scalper/position?symbol=" + encodeURIComponent(_symbol))
  .then(function (r) { return r.json(); })
  .then(function (d) {
    var pos = d.position;
    if (pos) {
      _hasPosition   = true;
      _posEntry      = pos.price_open;
      _posProfit     = pos.profit;
      var ticket     = pos.ticket;

      // Detectar troca de posição (raro mas possível)
      if (_lastPosTicket !== null && _lastPosTicket !== ticket) {
        _fetchRealProfitAndClose(_lastPosTicket, _posProfit);
      }
      _lastPosTicket = ticket;

      // Inicializar rastreamento de break-even e tempo na primeira detecção
      if (!_posOpenTime) {
        _posOpenTime        = pos.open_time ? (pos.open_time * 1000) : Date.now();
        _breakEvenTriggered = false;
      }

      _checkBreakEvenAndTimeExit(pos);
      _renderPositionBox(pos);
      _setStatus("📊 Posição aberta: " + pos.type + " " + pos.volume + " " + _symbol
        + " | P&L " + _fmtBRL(pos.profit));
    } else {
      // Sem posição — verificar se acabou de fechar
      if (_hasPosition && _lastPosTicket !== null) {
        var closedTicket = _lastPosTicket;
        _lastPosTicket      = null;
        _hasPosition        = false;
        _posEntry           = null;
        _posOpenTime        = null;
        _breakEvenTriggered = false;
        // Buscar P&L real do histórico MT5 (evita gain=0 por fechamento rápido)
        _fetchRealProfitAndClose(closedTicket, _posProfit);
      } else {
        _hasPosition = false;
        _posEntry    = null;
      }

      var posBox = document.getElementById("pos-box");
      if (posBox) posBox.className = "";

      var btnBuy  = document.getElementById("btn-buy");
      var btnSell = document.getElementById("btn-sell");
      if (btnBuy)  btnBuy.disabled  = false;
      if (btnSell) btnSell.disabled = false;
    }
  })
  .catch(function () { /* silencioso */ });
}

/* ─────────────────────────────────────────────────────────────────────────────
   Break-even automático e saída por tempo
───────────────────────────────────────────────────────────────────────────── */
function _checkBreakEvenAndTimeExit(pos) {
  var beTicks  = parseInt((document.getElementById("cfg-be-ticks")  || {value:"2"}).value)  || 0;
  var timeSecs = parseInt((document.getElementById("cfg-time-exit") || {value:"60"}).value) || 0;

  var tpTicks  = parseInt((document.getElementById("cfg-tp") || {value:"5"}).value) || 5;
  var slTicks  = parseInt((document.getElementById("cfg-sl") || {value:"2"}).value) || 2;

  // ── Break-even ──────────────────────────────────────────────────────────
  if (beTicks > 0 && !_breakEvenTriggered && pos.profit != null) {
    // Calcular lucro em ticks: profit / (tick_value * volume)
    // Comparamos diretamente: se profit >= beTicks * tick_value * volume => aplicar
    // Como não temos tick_value no JS, usamos a relação: profit_per_tick = (tp_pnl / tp_ticks)
    // Simplificação: se profit > 0 e preço atual >= entry + beTicks * tick_size
    var ts = pos.tp && pos.price_open
      ? Math.abs(pos.tp - pos.price_open) / tpTicks
      : 0;  // tick_size estimado pelo TP configurado

    if (ts > 0) {
      var profitInTicks = (pos.price_cur - pos.price_open) / ts * (pos.type === "BUY" ? 1 : -1);
      if (profitInTicks >= beTicks) {
        _breakEvenTriggered = true;
        fetch("/api/scalper/move-sl", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({symbol: _symbol})
        })
        .then(function (r) { return r.json(); })
        .then(function (d) {
          if (d.ok) {
            _setStatus("✅ Break-even aplicado: SL movido para " + pos.price_open);
          }
        })
        .catch(function () {});
      }
    }
  }

  // ── Saída por tempo ──────────────────────────────────────────────────────
  if (timeSecs > 0 && _posOpenTime) {
    var elapsed = (Date.now() - _posOpenTime) / 1000;
    if (elapsed >= timeSecs) {
      _setStatus("⏱ Saída por tempo: " + Math.round(elapsed) + "s — fechando posição...");
      window.manualClose();
    }
  }
}

/* ─────────────────────────────────────────────────────────────────────────────
   Renderiza caixa de posição aberta
───────────────────────────────────────────────────────────────────────────── */
function _renderPositionBox(pos) {
  var posBox = document.getElementById("pos-box");
  if (!posBox) return;
  posBox.className = "active";

  var profit  = pos.profit  != null ? pos.profit  : 0;
  var pnlEl   = document.getElementById("pos-pnl");
  if (pnlEl) {
    pnlEl.textContent = _fmtBRL(profit);
    pnlEl.style.color = profit >= 0 ? "var(--buy)" : "var(--sell)";
  }

  var typeStr = (pos.type || "").toUpperCase();
  _set("pos-side",  typeStr === "0" || typeStr === "BUY"  ? "COMPRA" : "VENDA");
  _set("pos-entry", _fmt(pos.price_open, 2));
  _set("pos-tp",    pos.tp  ? _fmt(pos.tp,  2) : "—");
  _set("pos-sl",    pos.sl  ? _fmt(pos.sl,  2) : "—");

  // Desabilitar botões de nova entrada enquanto há posição
  var btnBuy  = document.getElementById("btn-buy");
  var btnSell = document.getElementById("btn-sell");
  if (btnBuy)  btnBuy.disabled  = true;
  if (btnSell) btnSell.disabled = true;
}

/* ─────────────────────────────────────────────────────────────────────────────
   Callback: posição foi fechada (via TP/SL/MANUAL)
───────────────────────────────────────────────────────────────────────────── */
function _onPositionClosed(ticket, profit) {
  var reason = profit >= 0 ? "TP" : "SL";
  fetch("/api/scalper/register-close", {
    method:  "POST",
    headers: { "Content-Type": "application/json" },
    body:    JSON.stringify({ profit: profit, reason: reason, symbol: _symbol }),
  })
  .then(function (r) { return r.json(); })
  .then(function () { _loadSession(); })
  .catch(function () { _loadSession(); });

  _setStatus((profit >= 0 ? "✅ Gain!" : "❌ Loss") + " " + _fmtBRL(profit));

  // Esconder caixa de posição
  var posBox = document.getElementById("pos-box");
  if (posBox) posBox.className = "";
}

/* ─────────────────────────────────────────────────────────────────────────────
   Session stats
───────────────────────────────────────────────────────────────────────────── */
function _loadSession() {
  fetch("/api/scalper/session")
  .then(function (r) { return r.json(); })
  .then(function (d) {
    if (!d.ok) return;
    var s = d.session;
    _set("stat-trades",  s.trades     || 0);
    _set("stat-wins",    s.wins       || 0);
    _set("stat-be",      s.breakevens || 0);
    _set("stat-losses",  s.losses     || 0);

    var pnlEl = document.getElementById("stat-pnl");
    if (pnlEl) {
      var pnl = s.pnl || 0;
      pnlEl.textContent = _fmtBRL(pnl);
      pnlEl.className   = pnl > 0 ? "pos" : pnl < 0 ? "neg" : "zero";
    }
  })
  .catch(function () {});
}

/* ─────────────────────────────────────────────────────────────────────────────
   Funções globais (chamadas pelo HTML via onclick)
───────────────────────────────────────────────────────────────────────────── */

/** Liga/desliga modo simulação */
window.toggleSim = function () {
  _simMode = !_simMode;

  var btn = document.getElementById("sim-badge");
  if (btn) {
    btn.textContent = _simMode ? "⚙ SIM: ON" : "⚙ SIM: OFF";
    btn.className   = _simMode ? "active" : "";
  }

  // Se ligar sim, desligar auto-trade para evitar confusão
  if (_simMode && _autoEnabled) {
    _autoEnabled = false;
    var abtn = document.getElementById("auto-toggle");
    if (abtn) { abtn.textContent = "🤖 AUTO-TRADE: OFF"; abtn.className = ""; }
  }

  // Limpar tape e posição ao trocar modo
  _tapeEntries  = [];
  _lastPosTicket = null;
  _hasPosition   = false;
  var tapeEl = document.getElementById("tape-list");
  if (tapeEl) tapeEl.innerHTML = "";
  var posBox = document.getElementById("pos-box");
  if (posBox) posBox.className = "";

  fetch("/api/scalper/sim-mode", {
    method:  "POST",
    headers: { "Content-Type": "application/json" },
    body:    JSON.stringify({ enabled: _simMode }),
  }).catch(function () {});

  _setStatus(_simMode
    ? "⚙ Modo SIMULAÇÃO ativado — ticks sintéticos, sem MT5."
    : "Modo REAL restaurado — conectando ao MT5...");

  _setB3Badge(true);
};

/** Liga/desliga auto-trade */

window.toggleAutoParams = function () {
  _autoParamsEnabled = !_autoParamsEnabled;
  var btn = document.getElementById("btn-auto-params");
  if (_autoParamsEnabled) {
    _lastAutoSession = null;  // forca reaplicacao imediata na proxima atualizacao
    if (btn) { btn.className = "ap-btn-active"; btn.textContent = "\u2699 Auto"; }
  } else {
    if (btn) { btn.className = "ap-btn-off"; btn.textContent = "\u2699 Manual"; }
  }
};

window.toggleAuto = function () {
  _autoEnabled = !_autoEnabled;

  var btn = document.getElementById("auto-toggle");
  if (btn) {
    btn.textContent = "🤖 AUTO-TRADE: " + (_autoEnabled ? "ON" : "OFF");
    btn.className   = _autoEnabled ? "active" : "";
  }

  if (!_autoEnabled) {
    _confirmCount = 0;
    _updateConfirmBar(0, parseInt(_cfgNum("cfg-confirm")) || 3);
    var reasonEl = document.getElementById("auto-reason");
    if (reasonEl) reasonEl.textContent = "";
  }

  fetch("/api/scalper/auto-state", {
    method:  "POST",
    headers: { "Content-Type": "application/json" },
    body:    JSON.stringify({ enabled: _autoEnabled }),
  }).catch(function () {});

  _setStatus("Auto-trade: " + (_autoEnabled ? "LIGADO ✅" : "DESLIGADO ⛔"));
};

/** Abre posição manual */
window.manualTrade = function (acao) {
  if (_hasPosition) {
    _setStatus("⚠️ Já há posição aberta. Feche antes de entrar.");
    return;
  }
  var payload = {
    symbol:   _symbol,
    acao:     acao,
    volume:   _cfgNum("cfg-volume")    || 100,
    tp_ticks:       _cfgNum("cfg-tp") || 5,
    sl_ticks:       _cfgNum("cfg-sl") || 2,
    use_atr_sizing: document.getElementById("cfg-atr-sizing")
                      ? document.getElementById("cfg-atr-sizing").checked
                      : true,
  };

  var btnBuy  = document.getElementById("btn-buy");
  var btnSell = document.getElementById("btn-sell");
  if (btnBuy)  btnBuy.disabled  = true;
  if (btnSell) btnSell.disabled = true;

  _setStatus("⏳ Enviando ordem " + acao + " " + _symbol + "...");

  fetch("/api/scalper/execute", {
    method:  "POST",
    headers: { "Content-Type": "application/json" },
    body:    JSON.stringify(payload),
  })
  .then(function (r) { return r.json(); })
  .then(function (d) {
    if (d.ok) {
      _setStatus("✅ Ordem enviada: " + acao + " " + _symbol);
      // Marcar posição imediatamente — não esperar o poll de 800ms
      if (d.result && d.result.order) {
        _hasPosition        = true;
        _lastPosTicket      = d.result.order;
        _posProfit          = 0;
        _posOpenTime        = Date.now();
        _breakEvenTriggered = false;
      }
    } else {
      _setStatus("⚠️ Falha na ordem: " + (d.error || "erro desconhecido"));
      if (btnBuy)  btnBuy.disabled  = false;
      if (btnSell) btnSell.disabled = false;
    }
  })
  .catch(function (e) {
    _setStatus("⚠️ Erro de rede ao enviar ordem.");
    if (btnBuy)  btnBuy.disabled  = false;
    if (btnSell) btnSell.disabled = false;
  });
};

/** Fecha posição manualmente */
window.manualClose = function () {
  if (!_hasPosition) {
    _setStatus("⚠️ Nenhuma posição aberta.");
    return;
  }
  _setStatus("⏳ Fechando posição...");

  fetch("/api/scalper/close", {
    method:  "POST",
    headers: { "Content-Type": "application/json" },
    body:    JSON.stringify({ symbol: _symbol }),
  })
  .then(function (r) { return r.json(); })
  .then(function (d) {
    if (d.ok) {
      var profit = d.info ? (d.info.profit || 0) : 0;
      _setStatus("✅ Posição fechada | P&L " + _fmtBRL(profit));
      _hasPosition   = false;
      _lastPosTicket = null;
      _loadSession();
      var posBox = document.getElementById("pos-box");
      if (posBox) posBox.className = "";
      var btnBuy  = document.getElementById("btn-buy");
      var btnSell = document.getElementById("btn-sell");
      if (btnBuy)  btnBuy.disabled  = false;
      if (btnSell) btnSell.disabled = false;
    } else {
      _setStatus("⚠️ Falha ao fechar: " + (d.error || "erro desconhecido"));
    }
  })
  .catch(function () { _setStatus("⚠️ Erro de rede ao fechar posição."); });
};

/** Zera estatísticas da sessão */
window.resetSession = function () {
  if (!confirm("Zerar todas as estatísticas da sessão?")) return;
  fetch("/api/scalper/session/reset", { method: "POST" })
  .then(function () { _loadSession(); })
  .catch(function () {});
  _tapeEntries  = [];
  var tapeEl = document.getElementById("tape-list");
  if (tapeEl) tapeEl.innerHTML = "";
  _setStatus("Sessão zerada.");
};

/* ─────────────────────────────────────────────────────────────────────────────
   Troca de símbolo
───────────────────────────────────────────────────────────────────────────── */
function _initSymbolSelect() {
  var sel = document.getElementById("sym-select");
  if (!sel) return;
  sel.addEventListener("change", function () {
    _symbol       = sel.value;
    _lastPrice    = null;
    _tapeEntries  = [];
    _hasPosition  = false;
    _lastPosTicket = null;
    var tapeEl = document.getElementById("tape-list");
    if (tapeEl) tapeEl.innerHTML = "";
    var posBox = document.getElementById("pos-box");
    if (posBox) posBox.className = "";
    _setStatus("Símbolo alterado para " + _symbol);
  });
}

/* ─────────────────────────────────────────────────────────────────────────────
   Inicialização
───────────────────────────────────────────────────────────────────────────── */
document.addEventListener("DOMContentLoaded", function () {
  _initSymbolSelect();

  _symbol      = (document.getElementById("sym-select") || {}).value || "WDON26";
  _autoEnabled = false;
  _simMode     = false;
  _updateGauge(50);
  _updateSignalBadge("NEUTRO");
  _updateConfirmBar(0, 3);
  _setStatus("Iniciando conexão...");
  _loadSession();

  _dataTimer    = setInterval(_pollData,     500);
  _posTimer     = setInterval(_pollPosition, 800);
  _sessionTimer = setInterval(_loadSession, 5000);

  _pollData();
  _pollPosition();
});

/* ─────────────────────────────────────────────────────────────────────────────
   Modal: Log de Trades — assertividade histórica
───────────────────────────────────────────────────────────   Modal: Log de Trades — assertividade histórica
─────────────────────────────────────────────────────────────────────────────── */
// ── Log state (closure) ───────────────────────────────────────────────────
var _logAllTrades   = [];
var _logContextData = {};

window.showTradeLog = function () {
  var modal = document.getElementById("log-modal");
  if (!modal) return;
  modal.style.display = "flex";

  var sb = document.getElementById("log-summary-bar");
  var cb = document.getElementById("log-context-bars");
  var tb = document.getElementById("log-tbody");
  if (sb) sb.innerHTML = "<span style='color:var(--muted);font-size:12px'>Carregando...</span>";
  if (cb) cb.innerHTML = "";
  if (tb) tb.innerHTML = "";

  fetch("/api/scalper/trade-log")
  .then(function (r) { return r.json(); })
  .then(function (resp) {
    var d = resp.data || resp;
    if (d.error) {
      if (sb) sb.innerHTML = "<span style='color:var(--sell)'>Erro: " + d.error + "</span>";
      return;
    }
    if (!d.trades || d.trades === 0) {
      if (sb) sb.innerHTML = "<span style='color:var(--muted)'>Nenhum trade registrado ainda.</span>";
      return;
    }

    _logAllTrades   = d.last_trades || [];
    _logContextData = d;

    // Popula filtro de dia e renderiza
    _logBuildDayFilter();
    _logRenderView();
  })
  .catch(function (e) {
    var sb2 = document.getElementById("log-summary-bar");
    if (sb2) sb2.innerHTML = "<span style='color:var(--sell)'>Falha ao carregar log: " + e + "</span>";
  });
};

function _logBuildDayFilter() {
  var sel = document.getElementById("log-day-filter");
  if (!sel) return;

  // Datas únicas ordenadas decrescente
  var seen = {};
  _logAllTrades.forEach(function (t) {
    var dt = (t.datetime_brt || "").split(" ")[0];
    if (dt) seen[dt] = true;
  });
  var dates = Object.keys(seen).sort().reverse();

  // Data de hoje para selecionar por padrão
  var today = new Date();
  var todayStr = today.getFullYear() + "-" +
    String(today.getMonth()+1).padStart(2,"0") + "-" +
    String(today.getDate()).padStart(2,"0");

  sel.innerHTML = '<option value="">Todos (' + dates.length + ' dias)</option>' +
    dates.map(function (d) {
      var isToday = d === todayStr;
      var label   = isToday ? d + " — Hoje" : d;
      return '<option value="' + d + '"' + (isToday ? ' selected' : '') + '>' + label + '</option>';
    }).join("");

  sel.onchange = _logRenderView;
}

function _logRenderView() {
  var sel        = document.getElementById("log-day-filter");
  var filterDate = sel ? sel.value : "";
  var trades     = filterDate
    ? _logAllTrades.filter(function (t) { return (t.datetime_brt || "").startsWith(filterDate); })
    : _logAllTrades;

  _logRenderSummary(trades, filterDate);
  _logRenderTable(trades);
}

function _logRenderSummary(trades, filterDate) {
  var sb = document.getElementById("log-summary-bar");
  var cb = document.getElementById("log-context-bars");
  if (!sb) return;

  var wins   = trades.filter(function (t) { return t.resultado === "WIN";  }).length;
  var bes    = trades.filter(function (t) { return t.resultado === "BE";   }).length;
  var losses = trades.filter(function (t) { return t.resultado === "LOSS"; }).length;
  var total  = trades.length;
  var pnl    = trades.reduce(function (s, t) { return s + parseFloat(t.profit || 0); }, 0);
  var wr     = total > 0 ? wins / total * 100 : 0;
  var avg    = total > 0
    ? trades.reduce(function (s, t) { return s + parseFloat(t.score || 0); }, 0) / total
    : 0;

  var pnlColor = pnl >= 0 ? "var(--buy)" : "var(--sell)";
  var wrColor  = wr  >= 55 ? "var(--buy)" : (wr >= 45 ? "var(--gold)" : "var(--sell)");

  sb.innerHTML = [
    _logStat("Trades",    total,                   "var(--text)"),
    _logStat("✅ Wins",   wins,                    "var(--buy)"),
    _logStat("➖ BE",     bes,                     "var(--gold)"),
    _logStat("❌ Losses", losses,                  "var(--sell)"),
    _logStat("Win Rate",  wr.toFixed(1) + "%",     wrColor),
    _logStat("Score Méd", avg.toFixed(0),          "var(--text)"),
    _logStat("P&L Total", _fmtBRL(pnl),            pnlColor),
  ].join("");

  // Context bars — só mostrar na visão geral (sem filtro de dia)
  if (cb) {
    if (!filterDate) {
      var cbHtml = _logContextGroup("VWAP",   _logContextData.by_vwap)
                 + _logContextGroup("Sessão", _logContextData.by_session)
                 + _logContextGroup("Score",  _logContextData.by_score);
      cb.innerHTML = cbHtml;
    } else {
      cb.innerHTML = "";
    }
  }
}

function _logRenderTable(trades) {
  var tb = document.getElementById("log-tbody");
  if (!tb) return;
  if (trades.length === 0) {
    tb.innerHTML = "<tr><td colspan='8' style='color:var(--muted);text-align:center;padding:12px'>Sem trades para este dia.</td></tr>";
    return;
  }
  tb.innerHTML = trades.map(function (t) {
    var resClass = t.resultado === "WIN" ? "log-win" : (t.resultado === "LOSS" ? "log-loss" : "log-be");
    var pnlClr   = parseFloat(t.profit || 0) >= 0 ? "color:var(--buy)" : "color:var(--sell)";
    var dt       = (t.datetime_brt || "—");
    var hora     = dt.split(" ")[1] || "";
    var data     = (dt.split(" ")[0] || "").slice(5);   // MM-DD
    return "<tr>" +
      "<td>" + hora + " " + data + "</td>" +
      "<td>" + (t.direcao === "COMPRA" ? "&#9650;" : "&#9660;") + " " + (t.direcao || "?") + "</td>" +
      "<td class='" + resClass + "'>" + (t.resultado || "—") + "</td>" +
      "<td style='" + pnlClr + "'>" + _fmtBRL(parseFloat(t.profit || 0)) + "</td>" +
      "<td>" + (t.score || "—") + "</td>" +
      "<td>" + (t.vwap_context || "—") + "</td>" +
      "<td>" + (t.session || "—") + "</td>" +
      "<td>" + (t.exit_reason || "—") + "</td>" +
    "</tr>";
  }).join("");
}

function _logStat(label, val, color) {
  return "<div class='ls-item'>" +
    "<span class='ls-label'>" + label + "</span>" +
    "<span class='ls-val' style='color:" + color + "'>" + val + "</span>" +
    "</div>";
}

function _logContextGroup(title, obj) {
  if (!obj || Object.keys(obj).length === 0) return "";
  var rows = Object.entries(obj).map(function (kv) {
    var k = kv[0], v = kv[1];
    if (!v || !v.total) return "";
    var wr = v.total > 0 ? (v.wins / v.total * 100).toFixed(0) + "%" : "—";
    var wrColor = v.total > 0 && (v.wins/v.total) >= 0.55 ? "var(--buy)" :
      v.total > 0 && (v.wins/v.total) >= 0.40 ? "var(--warn)" : "var(--sell)";
    return _logStat(k, wr + " (" + v.total + ")", wrColor);
  }).join("");
  return "<div class='lc-group'><div class='lc-title'>" + title +" </div>" + rows + "</div>";
}
