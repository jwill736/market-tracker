"use strict";

// ---------------------------------------------------------------- helpers
const $ = (sel, root = document) => root.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const safeUrl = (u) => (/^https?:\/\//i.test(u || "") ? u : "#");
const fmtMoney = (x, d) => x == null ? "—" : (x < 0 ? "-$" : "$") + Math.abs(Number(x)).toLocaleString(undefined, {
  minimumFractionDigits: d ?? (Math.abs(x) >= 1000 ? 0 : Math.abs(x) >= 1 ? 2 : 4),
  maximumFractionDigits: d ?? (Math.abs(x) >= 1000 ? 0 : Math.abs(x) >= 1 ? 2 : 6) });
const fmtBig = (x) => x == null ? "—" : Math.abs(x) >= 1e9 ? (x < 0 ? "-$" : "$") + (Math.abs(x) / 1e9).toFixed(1) + "B" : Math.abs(x) >= 1e6 ? (x < 0 ? "-$" : "$") + (Math.abs(x) / 1e6).toFixed(1) + "M" : fmtMoney(x, 0);
const fmtPct = (x, d = 1) => x == null ? "—" : (x > 0 ? "+" : "") + Number(x).toFixed(d) + "%";
const cls = (x) => x == null ? "" : x > 0 ? "up" : x < 0 ? "down" : "";
const arrow = (x) => x == null ? "" : x > 0 ? "▲ " : x < 0 ? "▼ " : "";

async function api(path, opts = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail || msg; } catch (_) { /* not JSON */ }
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return res.json();
}

const tooltip = $("#tooltip");
function showTip(evt, html) {
  tooltip.innerHTML = html;
  tooltip.hidden = false;
  const pad = 14, w = tooltip.offsetWidth, h = tooltip.offsetHeight;
  let x = evt.clientX + pad, y = evt.clientY + pad;
  if (x + w > window.innerWidth - 8) x = evt.clientX - w - pad;
  if (y + h > window.innerHeight - 8) y = evt.clientY - h - pad;
  tooltip.style.left = x + "px"; tooltip.style.top = y + "px";
}
const hideTip = () => { tooltip.hidden = true; };

// ---------------------------------------------------------------- tabs
const loaded = {};
let watchlist = [], holdingsList = [];   // symbols shown in the ticker tape
document.querySelectorAll("#tabs button").forEach((btn) => btn.addEventListener("click", () => selectTab(btn.dataset.tab)));
function selectTab(name) {
  document.querySelectorAll("#tabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === name)));
  document.querySelectorAll(".tab").forEach((s) => { s.hidden = s.id !== "tab-" + name; });
  if (name === "portfolio") loadPortfolio();
  if (name === "smart" && !loaded.smart) { loaded.smart = true; loadInvestors(); }
  if (name === "journal") loadJournal();
  if (name === "pulse") loadPulse();
  if (name === "plan") loadPlan();
  if (name === "home") loadHome();
  if (name === "early") loadEarly();
  if (name === "people") { loadPeople(); loadPickers(); }
  if (name === "hold") loadHold();
  if (name === "income") loadIncome();
  if (name === "mynews") { loadMyNews(); loadHeadsup(true); }
  if (name === "reading") loadReading();
  if (name === "radar") { loadRadar(); loadCryptoRadar(); }
  if (!["mynews", "reading"].includes(name) && typeof Live !== "undefined") Live.drop("mynews");
  if (name !== "early" && typeof Live !== "undefined") Live.drop("early");
  if (name !== "people" && typeof Live !== "undefined") Live.drop("people");
  if (name !== "pulse" && typeof Live !== "undefined") Live.drop("pulse");
  if (name !== "plan" && typeof Live !== "undefined") Live.drop("plan");
  if (name !== "hold" && typeof Live !== "undefined") Live.drop("hold");
  if (name !== "symbol" && typeof Live !== "undefined") { Live.drop("symbol"); symState.sym = null; history.replaceState(null, "", location.pathname); }
}

// ---------------------------------------------------------------- dashboard
async function loadWatchlist() {
  let list = await api("/api/watchlist");
  if (!list.length) {
    for (const s of ["SPY", "QQQ", "BTC-USD", "ETH-USD", "NVDA", "AAPL"]) await api("/api/watchlist/" + s, { method: "POST" });
    list = await api("/api/watchlist");
  }
  watchlist = list;
  const tbody = $("#watch-table tbody");
  tbody.innerHTML = list.map((s) => `<tr class="clickable" data-sym="${esc(s)}"><td><b>${esc(s)}</b></td>
    <td class="num" data-f="price">…</td><td class="num" data-f="chg"></td><td class="muted small" data-f="src"></td>
    <td class="num"><button class="ghost" data-remove="${esc(s)}" title="Remove" aria-label="Remove ${esc(s)}">✕</button></td></tr>`).join("");
  tbody.querySelectorAll("tr").forEach((tr) => tr.addEventListener("click", (e) => {
    if (e.target.dataset.remove) return;
    openSymbol(tr.dataset.sym);
  }));
  tbody.querySelectorAll("[data-remove]").forEach((b) => b.addEventListener("click", async () => {
    await api("/api/watchlist/" + encodeURIComponent(b.dataset.remove), { method: "DELETE" }); loadWatchlist();
  }));
  for (const sym of list) if (Live.prices[sym]) paintWatchRow(Live.prices[sym]);
  refreshTape();
}
function paintWatchRow(t, prev) {
  const tr = document.querySelector(`#watch-table tr[data-sym="${CSS.escape(t.symbol)}"]`);
  if (!tr) return;
  const cell = tr.querySelector('[data-f="price"]');
  cell.textContent = fmtMoney(t.price);
  flash(cell, t, prev);
  const chg = tr.querySelector('[data-f="chg"]');
  chg.textContent = arrow(t.change_pct) + fmtPct(t.change_pct, 2);
  chg.className = "num " + cls(t.change_pct);
  tr.querySelector('[data-f="src"]').textContent = t.source + (isCryptoSym(t.symbol) ? " · 24h" : "");
}
$("#watch-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const v = $("#watch-input").value.trim();
  if (!v) return;
  for (const s of v.split(/[,\s]+/).filter(Boolean)) await api("/api/watchlist/" + encodeURIComponent(s), { method: "POST" });
  $("#watch-input").value = "";
  loadWatchlist();
});

function renderNews(listEl, termsEl, pillEl, data) {
  pillEl.textContent = `${data.count} headlines · mood ${data.avg_sentiment >= 0 ? "+" : ""}${data.avg_sentiment.toFixed(2)}`;
  termsEl.innerHTML = (data.top_terms || []).map(([t, n]) => `<span>${esc(t)} ${n}</span>`).join("");
  listEl.innerHTML = data.articles.map((a) => `<li>
      <span class="sent ${cls(a.sentiment)}" title="Headline sentiment">${a.sentiment ? (a.sentiment > 0 ? "+" : "") + a.sentiment.toFixed(2) : "·"}</span>
      <div><a href="${esc(safeUrl(a.url))}" target="_blank" rel="noopener noreferrer">${esc(a.title)}</a>
      <div class="meta">${esc(a.source)} · ${esc((a.published || "").slice(0, 16).replace("T", " "))}</div></div></li>`).join("")
    || `<li class="muted">No headlines${data.errors?.length ? " (" + esc(data.errors[0]) + ")" : ""}</li>`;
}
let newsLoadedAt = 0;
async function loadMarketNews() {
  newsLoadedAt = Date.now();
  try { renderNews($("#mkt-news"), $("#mkt-terms"), $("#mkt-sentiment"), await api("/api/news/market")); }
  catch (err) { $("#mkt-news").innerHTML = `<li class="muted">${esc(err.message)}</li>`; }
}

// ---------------------------------------------------------------- charts
const SVGNS = "http://www.w3.org/2000/svg";

function priceChart(el, history, ind, forecast) {
  const W = Math.max(320, el.clientWidth || 800), H = 300, m = { t: 10, r: 64, b: 24, l: 8 };
  const closes = history.map((d) => d.close);
  const sma = (n) => closes.map((_, i) => i < n - 1 ? null : closes.slice(i - n + 1, i + 1).reduce((a, b) => a + b, 0) / n);
  const s50 = sma(50), s200 = sma(200);
  const cone = (forecast?.lognormal || []);
  const lastDate = new Date(history[history.length - 1].date);
  const horizonCal = (d) => Math.round(d * (history.length > 1 && isCrypto(history) ? 1 : 7 / 5));
  const future = cone.map((c) => ({ ...c, t: new Date(lastDate.getTime() + horizonCal(c.horizon_days) * 864e5) }));
  const xs = history.map((d) => new Date(d.date).getTime());
  const xMin = xs[0], xMax = future.length ? future[future.length - 1].t.getTime() : xs[xs.length - 1];
  const allY = closes.concat(future.flatMap((f) => [f.p5, f.p95]));
  let yMin = Math.min(...allY), yMax = Math.max(...allY);
  const padY = (yMax - yMin) * 0.05; yMin -= padY; yMax += padY;
  const X = (t) => m.l + (t - xMin) / (xMax - xMin) * (W - m.l - m.r);
  const Y = (v) => m.t + (1 - (v - yMin) / (yMax - yMin)) * (H - m.t - m.b);
  const path = (vals) => vals.map((v, i) => v == null ? null : [X(xs[i]), Y(v)]).filter(Boolean)
    .map((p, i) => (i ? "L" : "M") + p[0].toFixed(1) + "," + p[1].toFixed(1)).join("");

  const ticks = niceTicks(yMin, yMax, 5);
  let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Price history with 50 and 200 day averages and forecast range">`;
  svg += ticks.map((t) => `<line x1="${m.l}" x2="${W - m.r}" y1="${Y(t)}" y2="${Y(t)}" stroke="var(--grid)" stroke-width="1"/>
    <text x="${W - m.r + 6}" y="${Y(t) + 4}" font-size="11" fill="var(--muted)">${fmtAxis(t)}</text>`).join("");
  // Month ticks
  const seen = new Set();
  history.forEach((d, i) => {
    const key = d.date.slice(0, 7);
    if (d.date.slice(8) <= "07" && !seen.has(key) && ["01", "04", "07", "10"].includes(d.date.slice(5, 7))) {
      seen.add(key);
      svg += `<text x="${X(xs[i])}" y="${H - 6}" font-size="11" fill="var(--muted)" text-anchor="middle">${d.date.slice(0, 7)}</text>`;
    }
  });
  if (future.length) {
    const x0 = X(xs[xs.length - 1]), y0 = Y(closes[closes.length - 1]);
    const outer = `M${x0},${y0} ` + future.map((f) => `L${X(f.t)},${Y(f.p95)}`).join(" ") + " " +
      future.slice().reverse().map((f) => `L${X(f.t)},${Y(f.p5)}`).join(" ") + " Z";
    const inner = `M${x0},${y0} ` + future.map((f) => `L${X(f.t)},${Y(f.p75)}`).join(" ") + " " +
      future.slice().reverse().map((f) => `L${X(f.t)},${Y(f.p25)}`).join(" ") + " Z";
    svg += `<path d="${outer}" fill="var(--band)"/><path d="${inner}" fill="var(--band-2)"/>`;
    svg += `<path d="M${x0},${y0} ` + future.map((f) => `L${X(f.t)},${Y(f.p50)}`).join(" ") + `" fill="none" stroke="var(--s1)" stroke-width="1.5" stroke-dasharray="4 4"/>`;
  }
  svg += `<line x1="${m.l}" x2="${W - m.r}" y1="${H - m.b}" y2="${H - m.b}" stroke="var(--axis)"/>`;
  svg += `<path d="${path(s200)}" fill="none" stroke="var(--s3)" stroke-width="2"/>`;
  svg += `<path d="${path(s50)}" fill="none" stroke="var(--s2)" stroke-width="2"/>`;
  svg += `<path d="${path(closes)}" fill="none" stroke="var(--s1)" stroke-width="2"/>`;
  svg += `<line id="xhair" y1="${m.t}" y2="${H - m.b}" stroke="var(--axis)" stroke-width="1" visibility="hidden"/>`;
  svg += `<circle id="xdot" r="4" fill="var(--s1)" stroke="var(--surface)" stroke-width="2" visibility="hidden"/>`;
  svg += `<rect x="${m.l}" y="${m.t}" width="${W - m.l - m.r}" height="${H - m.t - m.b}" fill="transparent" id="hit"/>`;
  svg += `</svg>`;
  el.innerHTML = svg;

  const svgEl = el.querySelector("svg"), hit = el.querySelector("#hit"), xh = el.querySelector("#xhair"), dot = el.querySelector("#xdot");
  hit.addEventListener("mousemove", (evt) => {
    const pt = svgEl.createSVGPoint(); pt.x = evt.clientX; pt.y = evt.clientY;
    const loc = pt.matrixTransform(svgEl.getScreenCTM().inverse());
    const t = xMin + (loc.x - m.l) / (W - m.l - m.r) * (xMax - xMin);
    if (t > xs[xs.length - 1] && future.length) {
      const f = future.reduce((a, b) => Math.abs(b.t - t) < Math.abs(a.t - t) ? b : a);
      xh.setAttribute("x1", X(f.t)); xh.setAttribute("x2", X(f.t)); xh.setAttribute("visibility", "visible");
      dot.setAttribute("visibility", "hidden");
      showTip(evt, `<b>+${f.horizon_days} ${isCrypto(history) ? "days" : "trading days"}</b><br>90% range ${fmtMoney(f.p5)} – ${fmtMoney(f.p95)}<br>50% range ${fmtMoney(f.p25)} – ${fmtMoney(f.p75)}<br>median ${fmtMoney(f.p50)} · P(up) ${(f.prob_up * 100).toFixed(0)}%`);
      return;
    }
    let i = 0, best = Infinity;
    xs.forEach((x, j) => { const d = Math.abs(x - t); if (d < best) { best = d; i = j; } });
    xh.setAttribute("x1", X(xs[i])); xh.setAttribute("x2", X(xs[i])); xh.setAttribute("visibility", "visible");
    dot.setAttribute("cx", X(xs[i])); dot.setAttribute("cy", Y(closes[i])); dot.setAttribute("visibility", "visible");
    showTip(evt, `<b>${history[i].date}</b><br>Close ${fmtMoney(closes[i])}` +
      (s50[i] ? `<br>50-day ${fmtMoney(s50[i])}` : "") + (s200[i] ? `<br>200-day ${fmtMoney(s200[i])}` : ""));
  });
  hit.addEventListener("mouseleave", () => { hideTip(); xh.setAttribute("visibility", "hidden"); dot.setAttribute("visibility", "hidden"); });

  $("#price-legend").innerHTML = `<span><i style="background:var(--s1)"></i>Price</span>
    <span><i style="background:var(--s2)"></i>50-day avg</span><span><i style="background:var(--s3)"></i>200-day avg</span>
    <span><i class="band" style="background:var(--band)"></i>90% range</span><span><i class="band" style="background:var(--band-2)"></i>50% range</span>`;
}
function isCrypto(history) {
  // Crypto trades weekends: consecutive daily bars.
  const a = new Date(history[history.length - 2].date), b = new Date(history[history.length - 1].date);
  return (b - a) / 864e5 === 1 && history.some((d) => new Date(d.date).getUTCDay() === 6);
}
function niceTicks(min, max, n) {
  const span = max - min, step0 = span / n, mag = Math.pow(10, Math.floor(Math.log10(step0)));
  const step = [1, 2, 2.5, 5, 10].map((s) => s * mag).find((s) => span / s <= n) || 10 * mag;
  const out = [];
  for (let v = Math.ceil(min / step) * step; v <= max; v += step) out.push(v);
  return out;
}
const fmtAxis = (v) => Math.abs(v) >= 1000 ? (v / 1000).toFixed(v >= 10000 ? 0 : 1) + "k" : Math.abs(v) >= 1 ? v.toFixed(v % 1 ? 1 : 0) : v.toPrecision(2);

function divergingBars(el, rows) {
  // rows: [{label, value (-100..100), note, weight}]
  const W = Math.max(320, el.clientWidth || 500), rowH = 34, labelW = 110, H = rows.length * rowH + 8;
  const mid = labelW + (W - labelW - 50) / 2, half = (W - labelW - 50) / 2;
  let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Signal components from bearish to bullish">`;
  svg += `<line x1="${mid}" x2="${mid}" y1="0" y2="${H}" stroke="var(--axis)"/>`;
  rows.forEach((r, i) => {
    const y = i * rowH + 8, v = r.value;
    svg += `<text x="0" y="${y + 15}" font-size="12" fill="var(--ink-2)">${esc(r.label)}</text>`;
    if (v == null) {
      svg += `<text x="${mid + 6}" y="${y + 15}" font-size="12" fill="var(--muted)">no data</text>`;
      return;
    }
    const w = Math.abs(v) / 100 * half, x = v >= 0 ? mid : mid - w;
    svg += `<rect x="${x}" y="${y + 3}" width="${Math.max(w, 1)}" height="16" rx="2" fill="${v >= 0 ? "var(--pos)" : "var(--neg)"}" data-i="${i}"/>`;
    svg += `<text x="${v >= 0 ? mid + w + 6 : mid - w - 6}" y="${y + 15}" font-size="12" fill="var(--ink)" text-anchor="${v >= 0 ? "start" : "end"}" font-variant-numeric="tabular-nums">${v > 0 ? "+" : ""}${v.toFixed(0)}</text>`;
    svg += `<rect x="${labelW}" y="${y}" width="${W - labelW}" height="${rowH - 4}" fill="transparent" data-hit="${i}"/>`;
  });
  svg += `<text x="${mid - half}" y="${H}" font-size="10" fill="var(--muted)">bearish</text><text x="${mid + half}" y="${H}" font-size="10" fill="var(--muted)" text-anchor="end">bullish</text></svg>`;
  el.innerHTML = svg;
  el.querySelectorAll("[data-hit]").forEach((h) => {
    const r = rows[+h.dataset.hit];
    h.addEventListener("mousemove", (e) => showTip(e, `<b>${esc(r.label)}</b> (weight ${(r.weight * 100).toFixed(0)}%)<br>${esc(r.note || "no data")}`));
    h.addEventListener("mouseleave", hideTip);
  });
}

// ---------------------------------------------------------------- analyze
$("#analyze-form").addEventListener("submit", (e) => { e.preventDefault(); runAnalyze(); });
async function runAnalyze() {
  const sym = $("#analyze-input").value.trim();
  if (!sym) return;
  const withSm = $("#analyze-sm").checked;
  $("#analyze-status").textContent = withSm ? "Loading… (first 13F scan takes ~1 minute)" : "Loading…";
  try {
    const a = await api(`/api/analyze/${encodeURIComponent(sym)}?smart_money=${withSm}&insiders=${withSm}`);
    renderAnalysis(a);
    $("#analyze-status").textContent = "";
  } catch (err) { $("#analyze-status").textContent = err.message; }
}

function tile(label, value, sub, klass = "") {
  return `<div class="card tile"><div class="label">${esc(label)}</div><div class="value ${klass}">${value}</div><div class="sub">${sub || ""}</div></div>`;
}

function renderAnalysis(a) {
  $("#analyze-out").hidden = false;
  const q = a.quote || {}, ind = a.indicators || {}, sig = a.signal;
  $("#analyze-tiles").innerHTML = [
    tile(a.symbol, fmtMoney(q.price), `<span class="${cls(q.change_pct)}">${arrow(q.change_pct)}${fmtPct(q.change_pct, 2)}</span> · ${esc(q.source || "")}`),
    tile("Signal", sig ? `${sig.score > 0 ? "+" : ""}${sig.score}` : "—", sig ? `${esc(sig.label)} · coverage ${(sig.coverage * 100).toFixed(0)}%<br>` +
      `<button type="button" class="chip-warn" data-goto="journal" title="The score has not yet been shown to predict returns">Unproven · see track record</button>` : ""),
    tile("Volatility (1y)", ind.vol_annual != null ? (ind.vol_annual * 100).toFixed(0) + "%" : "—", `RSI ${ind.rsi14 != null ? ind.rsi14.toFixed(0) : "—"}`),
    tile("12-1 momentum", fmtPct(ind.momentum_12_1 != null ? ind.momentum_12_1 * 100 : null), `1y max drawdown ${ind.max_drawdown_1y != null ? (ind.max_drawdown_1y * 100).toFixed(0) + "%" : "—"}`),
    tile("Max position", sig?.suggested_max_weight ? (sig.suggested_max_weight * 100).toFixed(1) + "%" : "—", "1σ monthly move ≈ 2% of portfolio"),
  ].join("");

  $("#analyze-tiles").querySelectorAll("[data-goto]").forEach((b) => b.addEventListener("click", () => selectTab(b.dataset.goto)));
  if (a.history?.length > 2) priceChart($("#price-chart"), a.history, ind, a.forecast);
  $("#forecast-note").textContent = a.forecast?.note || "";

  if (sig) {
    const names = { trend: "Trend", momentum: "Momentum", smart_money: "Smart money", insider: "Insiders", news: "News mood" };
    divergingBars($("#signal-chart"), Object.entries(sig.components).map(([k, c]) => ({
      label: names[k], value: c ? c.score : null, note: c?.why, weight: c ? c.weight : 0 })));
    $("#signal-pill").textContent = sig.label;
    $("#risk-flags").innerHTML = sig.risk_flags.map((f) => `<li>${esc(f)}</li>`).join("");
    $("#signal-disclaimer").textContent = sig.disclaimer;
  }

  const fc = a.forecast;
  $("#forecast-table").innerHTML = fc ? `<thead><tr><th>Horizon</th><th class="hide-sm">Model</th><th class="num">5%</th><th class="num">Median</th><th class="num">95%</th><th class="num">P(up)</th></tr></thead><tbody>` +
    fc.lognormal.concat(fc.bootstrap).sort((x, y) => x.horizon_days - y.horizon_days).map((r) =>
      `<tr><td>${r.horizon_days}d</td><td class="hide-sm">${r.method}</td><td class="num">${fmtMoney(r.p5)}</td><td class="num">${fmtMoney(r.p50)}</td><td class="num">${fmtMoney(r.p95)}</td><td class="num">${(r.prob_up * 100).toFixed(0)}%</td></tr>`).join("") + "</tbody>"
    : "<tr><td class='muted'>Not enough history</td></tr>";

  const bt = a.backtest;
  const btNames = { buy_hold: "Buy & hold", trend_sma200: "Above 200-day", momentum_12_1: "12-1 momentum" };
  $("#backtest-table").innerHTML = bt ? `<thead><tr><th>Rule</th><th class="num">CAGR</th><th class="num">Sharpe</th><th class="num">Max DD</th><th class="num">Trades</th></tr></thead><tbody>` +
    Object.entries(bt.results).filter(([, r]) => r).map(([k, r]) =>
      `<tr><td>${btNames[k] || k}</td><td class="num">${fmtPct(r.cagr != null ? r.cagr * 100 : null)}</td><td class="num">${r.sharpe != null ? r.sharpe.toFixed(2) : "—"}</td><td class="num">${(r.max_drawdown * 100).toFixed(0)}%</td><td class="num">${r.trades}</td></tr>`).join("") + "</tbody>"
    : "<tr><td class='muted'>Needs 300+ days of history</td></tr>";
  $("#backtest-note").textContent = bt?.note || "";

  const sm = a.smart_money;
  if (a.asset_class === "crypto") $("#sm-out").innerHTML = `<p class="muted">13F filings don't cover crypto directly. Use Deep dive for ETF-flow and on-chain research.</p>`;
  else if (!sm) $("#sm-out").innerHTML = `<p class="muted">Not loaded.</p>`;
  else {
    const rows = [...sm.buyers.map((b) => ({ ...b, side: "buy" })), ...sm.sellers.map((s) => ({ ...s, side: "sell" }))];
    $("#sm-out").innerHTML = `<p class="small muted">${sm.investors_scanned} investors scanned · data ${a.smart_money_staleness_days ?? "?"}+ days old</p>` +
      (rows.length ? `<table class="data"><thead><tr><th>Investor</th><th>Move</th><th class="num">Weight</th><th>Quarter</th></tr></thead><tbody>` +
        rows.map((r) => `<tr><td>${esc(r.investor)}</td><td class="${r.side === "buy" ? "up" : "down"}">${esc(r.action)}</td><td class="num">${r.weight_pct.toFixed(1)}%</td><td>${esc(r.period)}</td></tr>`).join("") + "</tbody></table>"
        : "<p class='muted'>No tracked investor changed this position last quarter.</p>") +
      (sm.holders.length ? `<p class="small">Held by: ${sm.holders.map((h) => `${esc(h.investor)} (${h.weight_pct.toFixed(1)}%)`).join(", ")}</p>` : "");
  }

  const ins = a.insiders;
  if (!ins) $("#ins-out").innerHTML = `<p class="muted">${a.asset_class === "crypto" ? "Not applicable to crypto." : "Not loaded."}</p>`;
  else {
    $("#ins-out").innerHTML = `<p class="small">${ins.window_days}d: <b>${ins.open_market_buys}</b> open-market buys (${fmtBig(ins.buy_value_usd)}, ${ins.distinct_buyers} insiders${ins.cluster_buy ? " — <b>cluster buy</b>" : ""}),
      <b>${ins.open_market_sells}</b> sells (${ins.planned_10b5_1_sells} under 10b5-1 plans)</p>` +
      (ins.trades.length ? `<table class="data"><thead><tr><th>Date</th><th>Insider</th><th class="hide-sm">Role</th><th>Type</th><th class="num">Value</th></tr></thead><tbody>` +
        ins.trades.slice(0, 15).map((t) => `<tr><td>${esc(t.date)}</td><td>${esc(t.insider)}</td><td class="hide-sm">${esc(t.role)}</td><td class="${t.code === "P" ? "up" : t.code === "S" ? "down" : ""}">${esc(t.label)}</td><td class="num">${fmtBig(t.value)}</td></tr>`).join("") + "</tbody></table>" : "");
  }

  if (a.news) renderNews($("#news-list"), $("#news-terms"), $("#news-pill"), a.news);
  $("#analyze-errors").textContent = a.errors.length ? "Partial data:\n" + a.errors.join("\n") : "";
}

// ---------------------------------------------------------------- smart money
async function loadInvestors() {
  const invs = await api("/api/investors");
  $("#investor-list").innerHTML = invs.map((i) => `<button data-key="${esc(i.key)}" title="${esc(i.style)}">${esc(i.person)}</button>`).join("");
  $("#investor-list").querySelectorAll("button").forEach((b) => b.addEventListener("click", () => loadInvestor(b)));
}
async function loadInvestor(btn) {
  document.querySelectorAll("#investor-list button").forEach((b) => b.classList.toggle("active", b === btn));
  const out = $("#investor-out");
  out.innerHTML = `<p class="muted">Loading 13F filings from SEC EDGAR…</p>`;
  try {
    const r = await api("/api/investors/" + btn.dataset.key);
    const moves = (k, label, klass) => r.moves[k].length ? `<p><b class="${klass}">${label}</b> ${r.moves[k].slice(0, 20).map((m) => esc(m.ticker || m.issuer)).join(", ")}</p>` : "";
    out.innerHTML = `<h2>${esc(r.investor.person)} — ${esc(r.filer_name)}</h2>
      <p class="small muted">${esc(r.investor.style)}. Quarter ending ${esc(r.period)}, filed ${esc(r.filed)} (${r.staleness_days} days old).
      ${fmtBig(r.total_value_usd)} across ${r.positions} positions.${r.cik_verified ? "" : " <b>Filer name doesn't match — verify CIK.</b>"}</p>
      ${moves("new", "New:", "up")}${moves("added", "Added:", "up")}${moves("reduced", "Reduced:", "down")}${moves("exited", "Exited:", "down")}
      <table class="data"><thead><tr><th>Ticker</th><th>Issuer</th><th class="num">Weight</th><th class="num">Value</th><th class="num">Share Δ</th><th>Move</th></tr></thead><tbody>` +
      r.top_holdings.map((p) => `<tr class="clickable" data-sym="${esc(p.ticker || "")}"><td><b>${esc(p.ticker || "?")}</b></td><td>${esc(p.issuer)}</td><td class="num">${p.weight_now.toFixed(1)}%</td>
        <td class="num">${fmtBig(p.value_now)}</td><td class="num ${cls(p.share_change_pct)}">${fmtPct(p.share_change_pct)}</td><td>${esc(p.action)}</td></tr>`).join("") + "</tbody></table>";
    out.querySelectorAll("tr[data-sym]").forEach((tr) => tr.dataset.sym && tr.addEventListener("click", () => {
      $("#analyze-input").value = tr.dataset.sym; selectTab("analyze"); runAnalyze();
    }));
  } catch (err) { out.innerHTML = `<p class="muted">${esc(err.message)}</p>`; }
}
$("#consensus-load").addEventListener("click", async () => {
  const out = $("#consensus-out");
  out.innerHTML = `<p class="muted">Scanning ${"every tracked"} investor's last two 13F filings…</p>`;
  try {
    const c = await api("/api/smart-money/consensus");
    const table = (rows, side, title) => `<div><h2>${title}</h2><table class="data"><thead><tr><th>Ticker</th><th class="num">Score</th><th>Who</th></tr></thead><tbody>` +
      rows.map((r) => `<tr><td><b>${esc(r.ticker || r.issuer)}</b></td><td class="num">${r.score.toFixed(2)}</td><td class="small">${r[side].map((x) => `${esc(x.investor)} <span class="muted">${esc(x.action)}</span>`).join(", ")}</td></tr>`).join("") + "</tbody></table></div>";
    out.innerHTML = table(c.most_bought, "buyers", "Most bought") + table(c.most_sold, "sellers", "Most sold") +
      (c.errors.length ? `<p class="errors">${esc(c.errors.join("\n"))}</p>` : "");
  } catch (err) { out.innerHTML = `<p class="muted">${esc(err.message)}</p>`; }
});

// ---------------------------------------------------------------- portfolio
async function loadPortfolio() {
  try {
    const [p, txs] = await Promise.all([api("/api/portfolio"), api("/api/transactions")]);
    $("#pf-tiles").innerHTML = [
      tile("Portfolio value", `<span data-book="value">${fmtMoney(p.total_value, 2)}</span>`, `cost ${fmtMoney(p.total_cost, 0)} · live`),
      tile("Unrealized P&L", `<span data-book="unreal">${fmtMoney(p.unrealized_pnl, 0)}</span>`, `<span data-book="unrealpct">${fmtPct(p.unrealized_pct)}</span>`),
      tile("Today", `<span data-book="today-value">${fmtMoney(p.day_change_value, 0)}</span>`, `<span data-book="today-pct"></span> · crypto: 24h`),
      tile("Realized P&L", fmtMoney(p.realized_pnl, 0), ""),
    ].join("");
    $("#pf-table").innerHTML = p.positions.length ? `<thead><tr><th>Symbol</th><th class="num">Qty</th><th class="num">Avg cost</th><th class="num">Price</th><th class="num">Today</th><th class="num">Value</th><th class="num">P&L</th><th class="num">Weight</th></tr></thead><tbody>` +
      p.positions.map((x) => { const s = esc(x.symbol), q = x.quantity, c = x.cost_basis; return `<tr class="clickable" data-open="${s}"><td><b>${s}</b>${(holdingsList.find((h) => h.symbol === x.symbol)?.accounts || []).length ? `<div class="muted small">${esc(holdingsList.find((h) => h.symbol === x.symbol).accounts.join(" · "))}</div>` : ""}</td><td class="num">${q.toLocaleString(undefined, { maximumFractionDigits: 6 })}</td><td class="num">${fmtMoney(x.avg_cost)}</td>
        <td class="num" data-live="${s}" data-lf="price">${fmtMoney(x.price)}</td><td class="num ${cls(x.day_change_pct)}" data-live="${s}" data-lf="chg">${fmtPct(x.day_change_pct, 2)}</td>
        <td class="num" data-live="${s}" data-lf="value" data-qty="${q}">${fmtMoney(x.market_value)}</td>
        <td class="num"><span class="${cls(x.unrealized_pnl)}" data-live="${s}" data-lf="pnl" data-qty="${q}" data-cost="${c}">${fmtMoney(x.unrealized_pnl, 0)}</span> <span class="small ${cls(x.unrealized_pct)}" data-live="${s}" data-lf="pnlpct" data-qty="${q}" data-cost="${c}">${fmtPct(x.unrealized_pct)}</span></td>
        <td class="num" data-book="weight:${s}">${x.weight != null ? x.weight.toFixed(1) + "%" : "—"}</td></tr>`; }).join("") + "</tbody>"
      : "<tr><td class='muted'>No positions yet — record a trade.</td></tr>";
    bindLive("portfolio", $("#tab-portfolio"));
    Book.paint();
    $("#pf-table").querySelectorAll("[data-open]").forEach((tr) => tr.addEventListener("click", () => openSymbol(tr.dataset.open)));
    renderAlloc(p.rebalance_hint || []);
    const r = p.risk || {};
    $("#pf-risk").innerHTML = r.annual_vol != null ? `<table class="data"><tbody>
      <tr><td>Annualized volatility</td><td class="num">${(r.annual_vol * 100).toFixed(1)}%</td></tr>
      <tr><td>Sharpe (trailing)</td><td class="num">${r.sharpe != null ? r.sharpe.toFixed(2) : "—"}</td></tr>
      <tr><td>Max drawdown (trailing, current weights)</td><td class="num">${(r.max_drawdown * 100).toFixed(1)}%</td></tr>
      <tr><td>1-day 95% VaR</td><td class="num">${(r.var_95_1d * 100).toFixed(2)}% · ${fmtMoney(r.var_95_1d * p.total_value, 0)}</td></tr></tbody></table>`
      : `<p class="muted">${esc(r.note || "Add positions to see risk statistics.")}</p>`;
    $("#pf-warnings").innerHTML = (r.warnings || []).concat(p.errors || []).map((w) => `<li>${esc(w)}</li>`).join("");
    $("#tx-table").innerHTML = txs.length ? `<thead><tr><th>Date</th><th>Symbol</th><th>Side</th><th class="num">Qty</th><th class="num">Price</th><th></th></tr></thead><tbody>` +
      txs.slice().reverse().map((t) => `<tr><td>${esc(t.date)}</td><td>${esc(t.symbol)}</td><td>${esc(t.side)}</td><td class="num">${t.quantity}</td><td class="num">${fmtMoney(t.price)}</td>
        <td class="num"><button class="ghost" data-del="${t.id}" title="Delete">✕</button></td></tr>`).join("") + "</tbody>" : "";
    $("#tx-table").querySelectorAll("[data-del]").forEach((b) => b.addEventListener("click", async () => {
      await api("/api/transactions/" + b.dataset.del, { method: "DELETE" }); loadPortfolio();
    }));
  } catch (err) { $("#pf-tiles").innerHTML = `<p class="muted">${esc(err.message)}</p>`; }
}
function renderAlloc(rows) {
  const el = $("#pf-alloc");
  if (!rows.length) { el.innerHTML = ""; return; }
  const W = Math.max(320, el.clientWidth || 500), rowH = 30, labelW = 80, H = rows.length * rowH + 20;
  const max = Math.max(...rows.flatMap((r) => [r.current_pct, r.risk_balanced_pct]));
  // Reserve room right of the longest bar for its "34% → 28%" label (~80px at 12px).
  const X = (v) => labelW + v / max * (W - labelW - 100);
  let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Current weight versus risk-balanced weight">`;
  rows.forEach((r, i) => {
    const y = i * rowH;
    svg += `<text x="0" y="${y + 14}" font-size="12" fill="var(--ink-2)">${esc(r.symbol)}</text>`;
    svg += `<rect x="${labelW}" y="${y + 3}" width="${X(r.current_pct) - labelW}" height="14" rx="2" fill="var(--s1)"/>`;
    svg += `<line x1="${X(r.risk_balanced_pct)}" x2="${X(r.risk_balanced_pct)}" y1="${y}" y2="${y + 20}" stroke="var(--s2)" stroke-width="3"/>`;
    svg += `<text x="${X(Math.max(r.current_pct, r.risk_balanced_pct)) + 6}" y="${y + 14}" font-size="12" fill="var(--ink)">${r.current_pct.toFixed(0)}% → ${r.risk_balanced_pct.toFixed(0)}%</text>`;
  });
  svg += `</svg>`;
  el.innerHTML = svg + `<div class="legend"><span><i style="background:var(--s1);height:10px"></i>Current weight</span><span><i style="background:var(--s2);width:3px;height:12px"></i>Risk-balanced (inverse-volatility) weight</span></div>`;
}
$("#tx-form").date.value = new Date().toISOString().slice(0, 10);
$("#tx-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  const body = { symbol: f.get("symbol"), side: f.get("side"), quantity: +f.get("quantity"), price: +f.get("price"), fees: +(f.get("fees") || 0), date: f.get("date"), account: f.get("account") || "" };
  try {
    await api("/api/transactions", { method: "POST", body: JSON.stringify(body) });
    $("#tx-status").textContent = "Saved.";
    e.target.symbol.value = ""; e.target.quantity.value = ""; e.target.price.value = "";
    loadPortfolio();
  } catch (err) { $("#tx-status").textContent = err.message; }
});

// ---------------------------------------------------------------- research
let researchSource;
$("#research-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const sym = $("#research-symbol").value.trim();
  if (!sym) return;
  const q = $("#research-question").value.trim();
  if (researchSource) researchSource.close();
  $("#research-memo").textContent = "";
  $("#research-verdict").innerHTML = `<span class="muted">Waiting for memo…</span>`;
  researchSource = new EventSource(`/api/research/${encodeURIComponent(sym)}` + (q ? `?question=${encodeURIComponent(q)}` : ""));
  researchSource.onmessage = (ev) => {
    const d = JSON.parse(ev.data);
    if (d.type === "status") $("#research-status").textContent = d.text;
    else if (d.type === "text") $("#research-memo").textContent += d.text;
    else if (d.type === "error") { $("#research-status").textContent = d.text; }
    else if (d.type === "verdict") renderVerdict(d.verdict);
    else if (d.type === "end") { researchSource.close(); if (!/error|declined/i.test($("#research-status").textContent)) $("#research-status").textContent = "Done."; }
  };
  researchSource.onerror = () => { researchSource.close(); $("#research-status").textContent = "Connection closed."; };
});
function renderVerdict(v) {
  if (!v) { $("#research-verdict").textContent = "No structured verdict available."; return; }
  $("#research-verdict").innerHTML = `<div class="verdict-rating">${esc(v.rating)}</div>
    <p>${esc(v.conviction)} conviction · ${esc(v.horizon)} · max position ${Number(v.max_position_pct).toFixed(1)}%</p>
    <p>${esc(v.thesis)}</p>
    <h2 class="mt">Catalysts</h2><ul>${v.catalysts.map((c) => `<li>${esc(c)}</li>`).join("")}</ul>
    <h2 class="mt">Risks</h2><ul>${v.risks.map((c) => `<li>${esc(c)}</li>`).join("")}</ul>
    <h2 class="mt">Thesis is wrong if…</h2><p>${esc(v.invalidation)}</p>`;
}

// ---------------------------------------------------------------- track record
async function loadJournal() {
  $("#journal-status").textContent = "Scoring past entries against actual returns…";
  try {
    const r = await api("/api/journal/report");
    $("#journal-status").textContent = r.entries
      ? `${r.entries} entries · ${r.symbols} symbols · ${r.first_date} → ${r.last_date}` +
        (r.excluded_other_versions ? ` · ${r.excluded_other_versions} older-formula entries excluded` : "") : "";
    $("#journal-verdict").textContent = r.verdict;
    $("#journal-out").innerHTML = r.horizons.map((h) => `<div class="card">
      <h2>${h.horizon_days}-day outcomes</h2>
      <p class="small">${h.n} observations (~${h.effective_n} independent) · IC <b>${h.ic == null ? "—" : (h.ic > 0 ? "+" : "") + h.ic.toFixed(3)}</b>${h.ic_se ? " ± " + h.ic_se.toFixed(3) : ""}</p>
      ${h.buckets.length ? `<table class="data"><thead><tr><th>Label</th><th class="num">n</th><th class="num">Avg return</th><th class="num">Hit rate</th></tr></thead><tbody>` +
        h.buckets.map((b) => `<tr><td>${esc(b.label)}</td><td class="num">${b.n}</td><td class="num ${cls(b.avg_return)}">${fmtPct(b.avg_return * 100, 2)}</td><td class="num">${(b.hit_rate * 100).toFixed(0)}%</td></tr>`).join("") + "</tbody></table>"
        : `<p class="muted small">No outcomes yet — entries need ${h.horizon_days} trading days to mature.</p>`}
      <p class="small muted">Component IC: ${Object.entries(h.component_ic).map(([k, v]) => `${esc(k)} ${v == null ? "—" : v.toFixed(2)}`).join(" · ")}</p>
    </div>`).join("");
    if (r.errors?.length) $("#journal-status").textContent += " · " + r.errors.join("; ");
  } catch (err) { $("#journal-status").textContent = err.message; }
}
$("#journal-record").addEventListener("click", async () => {
  $("#journal-status").textContent = "Recording today's scores for your watchlist (includes SEC data; first run takes about a minute)…";
  try {
    const r = await api("/api/journal/record", { method: "POST" });
    $("#journal-status").textContent = `Recorded ${r.recorded} entries` + (r.skipped.length ? ` (skipped ${r.skipped.join(", ")})` : "");
    loadJournal();
  } catch (err) { $("#journal-status").textContent = err.message; }
});

// ---------------------------------------------------------------- boot
loadWatchlist().catch((err) => { $("#watch-table tbody").innerHTML = `<tr><td class="muted">${esc(err.message)}</td></tr>`; });
loadMarketNews();


// ---------------------------------------------------------------- live prices
// One EventSource for every symbol on screen (tape, watchlist, open symbol page). The server
// keeps the upstream Coinbase/Finnhub connections; this only listens.
const KNOWN_CRYPTO = new Set(["BTC", "ETH", "SOL", "DOGE", "ADA", "XRP", "LTC", "AVAX", "DOT", "LINK", "MATIC", "SHIB", "BCH", "XLM", "UNI", "ATOM", "ETC", "AAVE"]);
const normSym = (s) => { const u = String(s || "").trim().toUpperCase(); return KNOWN_CRYPTO.has(u) ? u + "-USD" : u; };
const isCryptoSym = (s) => /-(USD|USDT|USDC|EUR|GBP)$/.test(s);

const Live = {
  prices: {}, owners: new Map(), listeners: new Set(), es: null, current: "", timer: null,
  want(owner, syms) { this.owners.set(owner, new Set(syms.map(normSym))); this.schedule(); },
  drop(owner) { this.owners.delete(owner); this.schedule(); },
  onTick(fn) { this.listeners.add(fn); },
  schedule() { clearTimeout(this.timer); this.timer = setTimeout(() => this.connect(), 250); },
  connect() {
    const all = [...new Set([...this.owners.values()].flatMap((s) => [...s]))].sort();
    const key = all.join(",");
    if (key === this.current && this.es && this.es.readyState !== 2) return;
    this.current = key;
    if (this.es) this.es.close();
    if (!all.length) { setLiveState("off"); return; }
    this.es = new EventSource("/api/stream/live?symbols=" + encodeURIComponent(key));
    setLiveState("connecting");
    this.es.addEventListener("snapshot", (e) => { JSON.parse(e.data).forEach((t) => this.apply(t)); setLiveState("on"); });
    this.es.onmessage = (e) => { this.apply(JSON.parse(e.data)); setLiveState("on"); };
    this.es.onerror = () => setLiveState("off");   // EventSource reconnects by itself
  },
  lastTick: 0, stockSession: null,
  apply(t) {
    const prev = this.prices[t.symbol];
    this.lastTick = Date.now();
    if (t.session && t.session !== "24h") this.stockSession = { name: t.session, at: Date.now() };
    this.prices[t.symbol] = t;
    this.listeners.forEach((fn) => fn(t, prev));
  },
};
function setLiveState(state) {
  for (const dot of document.querySelectorAll("#live-dot, #live-dot-global")) {
    dot.classList.toggle("on", state === "on"); dot.title = { on: "Live", off: "Reconnecting…", connecting: "Connecting…" }[state];
  }
}
function flash(el, t, prev) {
  if (!prev || prev.price === t.price || matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  el.classList.remove("flash-up", "flash-down");
  void el.offsetWidth;   // restart the animation
  el.classList.add(t.price > prev.price ? "flash-up" : "flash-down");
}

// ---------------------------------------------------------------- ticker tape
function refreshTape() {
  const held = holdingsList.map((h) => h.symbol);
  const syms = [...new Set([...held, ...watchlist])];
  Live.want("tape", syms);
  $("#tape").innerHTML = syms.map((s) => `<button class="tape-item${held.includes(s) ? " held" : ""}" data-sym="${esc(s)}" title="${held.includes(s) ? "You own this" : "Watchlist"}">
      <span class="t-sym">${esc(s.replace(/-USD$/, ""))}</span><span class="t-price">—</span><span class="t-chg"></span></button>`).join("")
    || `<span class="muted small">Add symbols to your watchlist or import your holdings to see them here.</span>`;
  $("#tape").querySelectorAll("[data-sym]").forEach((b) => b.addEventListener("click", () => openSymbol(b.dataset.sym)));
  syms.forEach((s) => Live.prices[s] && paintTape(Live.prices[s]));
}
function paintTape(t, prev) {
  const b = document.querySelector(`#tape [data-sym="${CSS.escape(t.symbol)}"]`);
  if (!b) return;
  const p = b.querySelector(".t-price");
  p.textContent = fmtMoney(t.price);
  flash(p, t, prev);
  const c = b.querySelector(".t-chg");
  c.textContent = fmtPct(t.change_pct, 2);
  c.className = "t-chg " + cls(t.change_pct);
}
async function loadHoldings() {
  try { holdingsList = await api("/api/holdings"); } catch { holdingsList = []; }
  Book.set(holdingsList);
  refreshTape();
}
Live.onTick((t, prev) => { paintTape(t, prev); paintWatchRow(t, prev); if (t.symbol === symState.sym) paintSymbol(t, prev); });

// ---------------------------------------------------------------- symbol page
const symState = { sym: null, range: "1d", points: [], reference: null, refLabel: "", lastDraw: 0, analysis: null };

async function openSymbol(raw) {
  const sym = normSym(raw);
  if (!sym) return;
  Object.assign(symState, { sym, range: "1d", points: [], reference: null, analysis: null });
  selectTab("symbol");
  $("#sym-name").textContent = sym;
  $("#sym-price").textContent = Live.prices[sym] ? fmtMoney(Live.prices[sym].price) : "—";
  $("#sym-change").textContent = ""; $("#sym-src").textContent = "";
  document.querySelectorAll("#tab-symbol .range button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.range === "1d")));
  $("#sym-watch").textContent = watchlist.includes(sym) ? "Watching" : "Watch";
  Live.want("symbol", [sym]);
  history.replaceState(null, "", "#" + encodeURIComponent(sym));
  if (Live.prices[sym]) paintSymbol(Live.prices[sym]);
  renderPosition();
  loadChart();
  loadSymbolDetails(sym);
}
async function loadChart() {
  const { sym, range } = symState;
  $("#sym-chart").innerHTML = `<p class="muted small">Loading chart…</p>`;
  if (symState.candles) {
    try {
      const d = await api(`/api/candles/${encodeURIComponent(sym)}?range=${range === "5d" ? "1w" : range}`);
      if (sym !== symState.sym || range !== symState.range) return;
      symState.ohlc = d.candles; symState.reference = d.reference; symState.refLabel = d.reference_label;
      drawCandles();
    } catch (err) { $("#sym-chart").innerHTML = `<p class="muted small">Candles unavailable: ${esc(err.message)}</p>`; }
    return;
  }
  try {
    const d = await api(`/api/intraday/${encodeURIComponent(sym)}?range=${range}`);
    if (sym !== symState.sym || range !== symState.range) return;
    Object.assign(symState, { points: d.points, reference: d.reference, refLabel: d.reference_label });
    drawChart(true);
  } catch (err) { $("#sym-chart").innerHTML = `<p class="muted small">Chart unavailable: ${esc(err.message)}</p>`; }
}
$("#sym-candles").addEventListener("click", () => {
  symState.candles = !symState.candles;
  $("#sym-candles").setAttribute("aria-pressed", String(symState.candles));
  try { localStorage.setItem("plumbline.candles", symState.candles ? "1" : ""); } catch { /* storage blocked */ }
  loadChart();
});
try { symState.candles = localStorage.getItem("plumbline.candles") === "1"; $("#sym-candles").setAttribute("aria-pressed", String(symState.candles)); } catch { /* storage blocked */ }

function drawCandles() {
  const el = $("#sym-chart"), cs = symState.ohlc || [];
  if (cs.length < 2) { el.innerHTML = `<p class="muted small">Not enough data for candles in this range.</p>`; return; }
  // Keep the forming candle live.
  const t = Live.prices[symState.sym];
  if (t && symState.range === "1d") {
    const last = cs[cs.length - 1];
    last.c = t.price; last.h = Math.max(last.h, t.price); last.l = Math.min(last.l, t.price);
  }
  const W = el.clientWidth || 700, H = Math.max(260, Math.min(380, W * 0.5)), volH = Math.round(H * 0.2), pad = { t: 10, r: 56, b: 20, l: 6 };
  const priceH = H - pad.t - pad.b - volH - 6;
  const lo = Math.min(...cs.map((c) => c.l)), hi = Math.max(...cs.map((c) => c.h)), span = hi - lo || hi * 0.01 || 1;
  const vmax = Math.max(...cs.map((c) => c.v)) || 1;
  const n = cs.length, step = (W - pad.l - pad.r) / n, bw = Math.max(1, Math.min(12, step * 0.7));
  const X = (i) => pad.l + step * (i + 0.5), Y = (p) => pad.t + (hi - p) / span * priceH;
  const vy0 = H - pad.b;
  let body = "";
  cs.forEach((c, i) => {
    const up = c.c >= c.o, col = up ? "var(--gain)" : "var(--loss)", x = X(i);
    body += `<line x1="${x}" x2="${x}" y1="${Y(c.h)}" y2="${Y(c.l)}" stroke="${col}" stroke-width="1"/>`;
    const y1 = Y(Math.max(c.o, c.c)), y2 = Y(Math.min(c.o, c.c));
    body += `<rect x="${x - bw / 2}" y="${y1}" width="${bw}" height="${Math.max(1, y2 - y1)}" fill="${up ? "var(--surface)" : col}" stroke="${col}" stroke-width="1"/>`;
    const vh = c.v / vmax * volH;
    body += `<rect x="${x - bw / 2}" y="${vy0 - vh}" width="${bw}" height="${vh}" fill="${col}" opacity=".35"/>`;
  });
  const ticks = niceTicks(lo, hi, 4).map((v) => `<text x="${W - pad.r + 6}" y="${Y(v) + 4}" font-size="11" fill="var(--muted)">${fmtAxis(v)}</text><line x1="${pad.l}" x2="${W - pad.r}" y1="${Y(v)}" y2="${Y(v)}" stroke="var(--grid)"/>`).join("");
  const last = cs[n - 1];
  el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" width="100%" height="${H}" aria-hidden="true">${ticks}${body}
    <line x1="${pad.l}" x2="${W - pad.r}" y1="${Y(last.c)}" y2="${Y(last.c)}" stroke="var(--accent)" stroke-dasharray="3 3"/>
    <rect x="${W - pad.r + 2}" y="${Y(last.c) - 9}" width="${pad.r - 4}" height="18" rx="3" fill="var(--accent)"/>
    <text x="${W - pad.r + 6}" y="${Y(last.c) + 4}" font-size="11" fill="var(--on-accent)">${fmtAxis(last.c)}</text>
    <g class="cross" visibility="hidden"><line y1="${pad.t}" y2="${H - pad.b}" stroke="var(--axis)"/></g></svg>`;
  const svg = el.querySelector("svg"), cross = svg.querySelector(".cross");
  const daily = !["1d", "5d"].includes(symState.range);
  svg.addEventListener("pointermove", (e) => {
    const r = svg.getBoundingClientRect(), x = (e.clientX - r.left) * (W / r.width);
    const i = Math.max(0, Math.min(n - 1, Math.floor((x - pad.l) / step)));
    const c = cs[i];
    cross.setAttribute("visibility", "visible");
    cross.querySelector("line").setAttribute("x1", X(i)); cross.querySelector("line").setAttribute("x2", X(i));
    const when = new Date(c.t * 1000);
    showTip(e, `<b>${esc(daily ? when.toLocaleDateString([], { month: "short", day: "numeric", year: "numeric" }) : when.toLocaleString([], { weekday: "short", hour: "numeric", minute: "2-digit" }))}</b><br>
      O ${fmtMoney(c.o)} H ${fmtMoney(c.h)}<br>L ${fmtMoney(c.l)} C <span class="${cls(c.c - c.o)}">${fmtMoney(c.c)}</span><br>Vol ${Math.round(c.v).toLocaleString()}`);
  });
  svg.addEventListener("pointerleave", () => { cross.setAttribute("visibility", "hidden"); hideTip(); });
}
document.querySelectorAll("#tab-symbol .range button").forEach((b) => b.addEventListener("click", () => {
  symState.range = b.dataset.range;
  document.querySelectorAll("#tab-symbol .range button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
  loadChart();
}));

function paintSymbol(t, prev) {
  const el = $("#sym-price");
  if (!symState.hovering) { el.textContent = fmtMoney(t.price); flash(el, t, prev); }
  // The day's change for the 1D view (vs previous close; crypto: 24h); for longer ranges, vs the range start.
  let chg = t.change_pct, label = isCryptoSym(t.symbol) ? "past 24 hours" : "today";
  if (symState.range !== "1d" && symState.reference) { chg = (t.price / symState.reference - 1) * 100; label = "since " + symState.refLabel; }
  const abs = symState.range !== "1d" && symState.reference ? t.price - symState.reference
    : chg != null ? t.price - t.price / (1 + chg / 100) : null;
  const c = $("#sym-change"), x = $("#sym-ext");
  const extSession = !isCryptoSym(t.symbol) && ["pre", "post", "closed"].includes(t.session) && t.regular && t.change_pct != null;
  if (symState.range === "1d" && extSession) {
    // Robinhood-style: the regular session's change, then the move since the close.
    const prevClose = t.price / (1 + t.change_pct / 100);
    const today = t.regular - prevClose, ext = t.price - t.regular;
    const line = (v, base, word) => `${v >= 0 ? "▲" : "▼"} ${fmtMoney(Math.abs(v), 2)} (${fmtPct(Math.abs(base ? v / base * 100 : 0), 2).replace("+", "")}) ${word}`;
    c.textContent = line(today, prevClose, t.session === "pre" ? "Previous session" : "Today");
    c.className = "sym-change " + cls(today);
    x.hidden = false;
    x.textContent = line(ext, t.regular, t.session === "pre" ? "Pre-market" : "After-hours");
    x.className = "sym-change sym-ext " + cls(ext);
  } else {
    x.hidden = true;
    c.textContent = `${abs != null ? (abs >= 0 ? "▲ " : "▼ ") + fmtMoney(Math.abs(abs), 2) + " " : ""}(${fmtPct(chg, 2)}) ${label}`;
    c.className = "sym-change " + cls(chg);
  }
  const sess = { pre: "Pre-market", post: "After hours", closed: "Market closed · last trade" }[t.session] || "";
  $("#sym-src").textContent = `${sess ? sess + " · " : ""}${t.source} · ${new Date(t.ts).toLocaleTimeString()}`;
  if (symState.candles && symState.range === "1d") { drawChart(false); }
  else if (symState.range === "1d" && symState.points.length) {
    const now = Math.floor(Date.now() / 1000), last = symState.points[symState.points.length - 1];
    if (now - last.t < 60) last.p = t.price; else symState.points.push({ t: now, p: t.price });
    drawChart(false);
  }
  renderPosition();
}

function drawChart(force) {
  const now = performance.now();
  if (!force && now - symState.lastDraw < 500) return;   // at most twice a second
  symState.lastDraw = now;
  if (symState.candles) return drawCandles();
  const el = $("#sym-chart"), pts = symState.points;
  if (pts.length < 2) { el.innerHTML = `<p class="muted small">Not enough data for this range yet (markets closed?).</p>`; return; }
  const W = el.clientWidth || 700, H = Math.max(220, Math.min(340, W * 0.45)), pad = { t: 12, r: 8, b: 22, l: 8 };
  const ref = symState.reference ?? pts[0].p;
  const ps = pts.map((x) => x.p).concat([ref]);
  const lo = Math.min(...ps), hi = Math.max(...ps), span = hi - lo || hi * 0.01 || 1;
  const t0 = pts[0].t, t1 = pts[pts.length - 1].t || t0 + 1;
  const X = (t) => pad.l + (t - t0) / (t1 - t0 || 1) * (W - pad.l - pad.r);
  const Y = (p) => pad.t + (hi - p) / span * (H - pad.t - pad.b);
  const up = pts[pts.length - 1].p >= ref;
  const color = up ? "var(--gain)" : "var(--loss)";
  const d = pts.map((x, i) => `${i ? "L" : "M"}${X(x.t).toFixed(1)},${Y(x.p).toFixed(1)}`).join("");
  const fmtT = (t) => { const dt = new Date(t * 1000); return symState.range === "1d" ? dt.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }) : dt.toLocaleDateString([], { month: "short", day: "numeric" }); };
  el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" width="100%" height="${H}" aria-hidden="true">
    <line x1="${pad.l}" x2="${W - pad.r}" y1="${Y(ref)}" y2="${Y(ref)}" stroke="var(--axis)" stroke-dasharray="2 4"/>
    <path d="${d}" fill="none" stroke="${color}" stroke-width="2" stroke-linejoin="round"/>
    <circle cx="${X(pts[pts.length - 1].t)}" cy="${Y(pts[pts.length - 1].p)}" r="3.5" fill="${color}"/>
    <text x="${pad.l}" y="${H - 6}" font-size="11" fill="var(--muted)">${esc(fmtT(t0))}</text>
    <text x="${W - pad.r}" y="${H - 6}" font-size="11" fill="var(--muted)" text-anchor="end">${esc(fmtT(t1))}</text>
    <text x="${pad.l}" y="${Y(ref) < 24 ? Y(ref) + 14 : Y(ref) - 5}" font-size="11" fill="var(--muted)">${esc(symState.refLabel)} ${fmtMoney(ref, 2)}</text>
    <g class="cross" visibility="hidden"><line y1="${pad.t}" y2="${H - pad.b}" stroke="var(--axis)"/><circle r="4" fill="${color}"/></g>
  </svg>`;
  const svg = el.querySelector("svg"), cross = svg.querySelector(".cross");
  svg.addEventListener("pointermove", (e) => {
    const r = svg.getBoundingClientRect(), x = (e.clientX - r.left) * (W / r.width);
    const t = t0 + (x - pad.l) / (W - pad.l - pad.r) * (t1 - t0);
    let best = pts[0];
    for (const p of pts) if (Math.abs(p.t - t) < Math.abs(best.t - t)) best = p;
    cross.setAttribute("visibility", "visible");
    cross.querySelector("line").setAttribute("x1", X(best.t)); cross.querySelector("line").setAttribute("x2", X(best.t));
    cross.querySelector("circle").setAttribute("cx", X(best.t)); cross.querySelector("circle").setAttribute("cy", Y(best.p));
    $("#sym-price").textContent = fmtMoney(best.p);
    symState.hovering = true;
    showTip(e, `<b>${fmtMoney(best.p)}</b> <span class="${cls(best.p - ref)}">${fmtPct((best.p / ref - 1) * 100, 2)}</span><br>${esc(fmtT(best.t))}`);
  });
  svg.addEventListener("pointerleave", () => {
    cross.setAttribute("visibility", "hidden"); hideTip(); symState.hovering = false;
    if (Live.prices[symState.sym]) $("#sym-price").textContent = fmtMoney(Live.prices[symState.sym].price);
  });
}

function renderPosition() {
  const h = holdingsList.find((x) => x.symbol === symState.sym), el = $("#sym-position");
  if (!h) { el.innerHTML = `<p class="muted">You don't own ${esc(symState.sym)}. Use Buy to record a purchase, or import your Robinhood history in Portfolio.</p>`; return; }
  const t = Live.prices[symState.sym], value = t ? h.quantity * t.price : null, pnl = value != null ? value - h.cost_basis : null;
  el.innerHTML = `<table class="data"><tbody>
    <tr><td>Shares</td><td class="num">${h.quantity.toLocaleString(undefined, { maximumFractionDigits: 6 })}</td></tr>
    <tr><td>Average cost</td><td class="num">${fmtMoney(h.avg_cost)}</td></tr>
    <tr><td>Market value</td><td class="num">${fmtMoney(value)}</td></tr>
    <tr><td>Total return</td><td class="num ${cls(pnl)}">${fmtMoney(pnl)} ${h.cost_basis ? `(${fmtPct(pnl / h.cost_basis * 100)})` : ""}</td></tr>
  </tbody></table>`;
}

async function loadSymbolDetails(sym) {
  $("#sym-signal").innerHTML = `<p class="muted">Loading signal and news…</p>`;
  try {
    const a = await api(`/api/analyze/${encodeURIComponent(sym)}?smart_money=false&insiders=false`);
    if (sym !== symState.sym) return;
    symState.analysis = a;
    const sig = a.signal;
    const comps = sig ? Object.entries(sig.components || {}).filter(([, v]) => v).map(([k, v]) => `<li><span>${esc(k.replace("_", " "))}</span><span class="${cls(v.score)}">${v.score > 0 ? "+" : ""}${v.score.toFixed(0)}</span></li>`).join("") : "";
    $("#sym-signal").innerHTML = sig ? `<div class="sig-line"><span class="sig-score ${cls(sig.score)}">${sig.score > 0 ? "+" : ""}${sig.score}</span> ${esc(sig.label)}</div>
      <ul class="sig-comps">${comps}</ul>
      <p class="muted small">Trend, momentum and news only here; Full analysis adds 13F and insider data. The score is a ranking aid that hasn't yet been shown to predict returns.</p>`
      : `<p class="muted">No signal available.</p>`;
    if (a.news) renderNews($("#sym-news"), $("#sym-news-terms"), $("#sym-news-pill"), a.news);
  } catch (err) { $("#sym-signal").innerHTML = `<p class="muted">${esc(err.message)}</p>`; }
}

$("#sym-buy").addEventListener("click", () => prefillTrade("buy"));
$("#sym-sell").addEventListener("click", () => prefillTrade("sell"));
function prefillTrade(side) {
  selectTab("portfolio");
  const f = $("#tx-form");
  f.symbol.value = symState.sym; f.side.value = side;
  if (Live.prices[symState.sym]) f.price.value = Live.prices[symState.sym].price;
  f.quantity.focus();
}
$("#sym-watch").addEventListener("click", async () => {
  if (watchlist.includes(symState.sym)) return;
  await api("/api/watchlist/" + encodeURIComponent(symState.sym), { method: "POST" });
  $("#sym-watch").textContent = "Watching";
  loadWatchlist();
});
$("#sym-analyze").addEventListener("click", () => { $("#analyze-input").value = symState.sym; selectTab("analyze"); runAnalyze(); });
$("#sym-research").addEventListener("click", () => { $("#research-symbol").value = symState.sym; selectTab("research"); $("#research-question").focus(); });
$("#quick-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const v = $("#quick-input").value; $("#quick-input").value = "";
  openSymbol(v);
});
document.addEventListener("keydown", (e) => {
  if (e.key === "/" && !["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName)) { e.preventDefault(); $("#quick-input").focus(); }
});

// ---------------------------------------------------------------- imports (Robinhood, Coinbase, Stash / other)
let impSource = "robinhood", impText = "", impAccount = "";
document.querySelectorAll("#imp-seg button").forEach((b) => b.addEventListener("click", () => {
  impSource = b.dataset.src;
  document.querySelectorAll("#imp-seg button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
  document.querySelectorAll(".imp-help").forEach((p) => { p.hidden = p.dataset.for !== impSource; });
  $(".imp-file").hidden = impSource === "holdings";
  $(".imp-list").hidden = impSource !== "holdings";
  $("#rh-result").innerHTML = "";
}));
$("#rh-preview").addEventListener("click", async () => {
  const file = $("#rh-file").files[0];
  if (!file) { $("#rh-result").innerHTML = `<p class="muted">Choose the CSV file first.</p>`; return; }
  impText = await file.text();
  await runImport(false);
});
$("#imp-list-preview").addEventListener("click", () => {
  impText = $("#imp-text").value; impAccount = $("#imp-account").value;
  if (!impText.trim()) { $("#rh-result").innerHTML = `<p class="muted">Type at least one holding.</p>`; return; }
  runImport(false);
});
let cbConfigured = false;
api("/api/sync/coinbase").then((r) => { cbConfigured = r.configured; }).catch(() => {});
function paintSyncButton() { $(".imp-sync").hidden = !(impSource === "coinbase" && cbConfigured); }
document.querySelectorAll("#imp-seg button").forEach((b) => b.addEventListener("click", paintSyncButton));
$("#cb-sync").addEventListener("click", async () => {
  const out = $("#rh-result");
  out.innerHTML = `<p class="muted">Reading your Coinbase fills and balances…</p>`;
  try {
    const r = await api("/api/sync/coinbase", { method: "POST" });
    out.innerHTML = `<p><b>${r.new} new trade${r.new === 1 ? "" : "s"}</b> imported${r.duplicates ? `, ${r.duplicates} already there` : ""}.</p>` +
      (r.differences.length ? `<p class="small">Balances your ledger doesn't explain (rewards, transfers or older trades; add them with Stash / other):</p><ul class="small">${r.differences.map((d) => `<li>${esc(d.coin)}: Coinbase ${d.coinbase} vs ledger ${d.ledger.toFixed(8)}</li>`).join("")}</ul>` : `<p class="small muted">Every coin's balance matches the ledger.</p>`);
    loadPortfolio(); loadHoldings();
  } catch (err) { out.innerHTML = `<p class="muted">${esc(err.message)}</p>`; }
});
$("#bk-download").addEventListener("click", async () => {
  try {
    const data = await api("/api/backup");
    const blob = new Blob([JSON.stringify(data, null, 1)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = `plumbline-backup-${localDate()}.json`;
    document.body.appendChild(a); a.click(); a.remove();
    $("#bk-status").textContent = `Saved ${data.transactions.length} trades.`;
  } catch (err) { $("#bk-status").textContent = err.message; }
});
$("#bk-file").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  try {
    const r = await api("/api/backup/restore", { method: "POST", body: JSON.stringify({ data: JSON.parse(await file.text()) }) });
    $("#bk-status").textContent = `Restored: ${r.transactions_added} trades added.`;
    loadPortfolio(); loadHoldings(); loadWatchlist();
  } catch (err) { $("#bk-status").textContent = err.message; }
  e.target.value = "";
});

async function runImport(commit) {
  const out = $("#rh-result");
  out.innerHTML = `<p class="muted">${commit ? "Importing…" : "Reading…"}</p>`;
  try {
    const r = await api("/api/import/" + impSource, { method: "POST", body: JSON.stringify({ csv: impText, commit, account: impAccount || "Stash" }) });
    const skipped = Object.entries(r.skipped).map(([k, n]) => `${esc(k)} ×${n}`).join(", ");
    const noun = impSource === "holdings" ? "holdings" : "trades";
    out.innerHTML = `<p>${commit ? `<b>Imported ${r.new} ${noun}.</b>` : `<b>${r.new} new ${noun}</b> to import`}${r.duplicates ? `, ${r.duplicates} already imported` : ""}.
      ${r.income_new ? `<br>Plus ${r.income_new} dividend and interest payment${r.income_new === 1 ? "" : "s"} (${fmtMoney(r.income_total, 2)}) for the Income tab.` : ""}
      ${skipped ? `<br><span class="muted small">Skipped (not trades): ${skipped}</span>` : ""}
      ${r.errors.length ? `<br><span class="muted small">${r.errors.map(esc).join("<br>")}</span>` : ""}</p>
      <p class="muted small">Positions after import: ${r.positions.map((p) => `${esc(p.symbol)} ${p.quantity.toLocaleString(undefined, { maximumFractionDigits: 6 })}`).join(", ") || "none"}</p>
      ${!commit && (r.new || r.income_new) ? `<button id="rh-commit" type="button">Import${r.new ? ` ${r.new} ${noun}` : ""}${r.income_new ? `${r.new ? " and" : ""} ${r.income_new} payments` : ""}</button>` : ""}`;
    const btn = $("#rh-commit");
    if (btn) btn.addEventListener("click", () => runImport(true));
    if (commit) { loadPortfolio(); loadHoldings(); }
  } catch (err) { out.innerHTML = `<p class="muted">${esc(err.message)}</p>`; }
}

// ---------------------------------------------------------------- pulse
const pulseState = { data: null, list: "gainers", loadedAt: 0 };
async function loadPulse(force = false) {
  loadSellWatch();
  if (!force && pulseState.data && Date.now() - pulseState.loadedAt < 170000) return renderMovers();
  if (!pulseState.data) $("#pulse-meta").textContent = "Scanning movers and their news… (about 10–20 seconds the first time)";
  try {
    pulseState.data = await api("/api/pulse" + (force ? "?refresh=true" : ""));
    pulseState.loadedAt = Date.now();
    renderPulse();
  } catch (err) { $("#pulse-meta").textContent = err.message; }
}
function moverTags(m) {
  return (m.tags || []).map((t) => `<span class="tag tag-${t.toLowerCase().replace(/[^a-z]+/g, "-")}">${esc(t)}</span>`).join("");
}
function renderMovers() {
  const d = pulseState.data;
  if (!d) return;
  const rows = d.movers[pulseState.list] || [];
  $("#movers-table").innerHTML = rows.length ? `<thead><tr><th>Symbol</th><th class="num">Price</th><th class="num">Today</th><th class="num">Volume vs avg</th><th class="num">News 48h</th><th>Why it's here</th></tr></thead><tbody>` +
    rows.map((m) => `<tr class="clickable" data-open="${esc(m.symbol)}"><td><b>${esc(m.symbol.replace(/-USD$/, ""))}</b><div class="muted small">${esc(m.name)}</div></td>
      <td class="num" data-live="${esc(m.symbol)}" data-lf="price">${fmtMoney(m.price)}</td>
      <td class="num ${cls(m.change_pct)}" data-live="${esc(m.symbol)}" data-lf="chg">${fmtPct(m.change_pct, 2)}</td>
      <td class="num">${m.rel_volume ? m.rel_volume.toFixed(1) + "×" : "—"}</td>
      <td class="num">${m.attention ? m.attention.count_48h : "—"}</td>
      <td>${moverTags(m) || '<span class="muted small">—</span>'}${m.attention?.headline ? `<div class="small muted clip">${esc(m.attention.headline.title)}</div>` : ""}</td></tr>`).join("") + "</tbody>"
    : `<tr><td class="muted">Nothing in this list right now.</td></tr>`;
  $("#movers-table").querySelectorAll("[data-open]").forEach((tr) => tr.addEventListener("click", () => openSymbol(tr.dataset.open)));
  bindLive("pulse", $("#tab-pulse"));
}
function renderPulse() {
  const d = pulseState.data;
  renderMovers();
  $("#pulse-meta").innerHTML = `Scanned <span data-ago="${esc(d.generated_at)}"></span>; rescans every 3 minutes. Prices update live. ${d.errors.length ? `<span class="muted">(${d.errors.map(esc).join("; ")})</span>` : ""} <button class="ghost" id="pulse-refresh">Rescan</button>`;
  $("#pulse-refresh").addEventListener("click", () => loadPulse(true));
  const item = (m, extra) => `<li class="clickable" data-open="${esc(m.symbol)}"><div><b>${esc(m.symbol.replace(/-USD$/, ""))}</b> <span data-live="${esc(m.symbol)}" data-lf="price">${fmtMoney(m.price)}</span> <span class="${cls(m.change_pct)}" data-live="${esc(m.symbol)}" data-lf="chg">${fmtPct(m.change_pct, 2)}</span> <span class="muted small">${esc(m.name)}</span></div>${extra}</li>`;
  const link = (a) => `<a href="${esc(safeUrl(a.url))}" target="_blank" rel="noopener noreferrer">${esc(a.title)}</a> <span class="muted small">${esc(a.source)}</span>`;
  $("#hot-list").innerHTML = d.in_the_news.filter((m) => m.attention.count_48h).map((m) => item(m,
    `<div class="small">${m.attention.count_48h} headlines · mood <span class="${cls(m.attention.sentiment)}">${m.attention.sentiment >= 0 ? "+" : ""}${m.attention.sentiment.toFixed(2)}</span></div>${m.attention.headline ? `<div class="small">${link(m.attention.headline)}</div>` : ""}`)).join("")
    || `<li class="muted">No mover has much news right now.</li>`;
  $("#deep-list").innerHTML = d.deep_coverage.map((m) => item(m, m.attention.deep.map((a) => `<div class="small">${link(a)}</div>`).join(""))).join("")
    || `<li class="muted">No in-depth coverage of today's movers yet.</li>`;
  const r = d.rules;
  $("#sleeper-rule").textContent = `Officers and directors bought in the last 30 days (a cluster, or $1M+ by one of them), yet the stock had ${r.sleeper_max_news_7d} or fewer headlines this week and is up less than ${Math.round(r.sleeper_max_run_1m * 100)}% over the month. Research finds insider buying pays off over months, mostly in smaller, more volatile companies; the scorecard hasn't confirmed it for these alerts yet.`;
  $("#sleeper-list").innerHTML = d.sleepers.map((s) => `<div class="sleeper clickable" data-open="${esc(s.symbol)}">
      <div class="sl-head"><b class="sl-sym">${esc(s.symbol)}</b><span class="muted small clip">${esc(s.company)}</span>${s.dilution ? `<span class="chip dil">${esc(s.dilution)}</span>` : ""}</div>
      <div class="sl-price"><span data-live="${esc(s.symbol)}" data-lf="price">${fmtMoney(s.price)}</span> <span class="${cls(s.change_pct)}" data-live="${esc(s.symbol)}" data-lf="chg">${fmtPct(s.change_pct, 2)}</span></div>
      <ul>${s.reasons.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div>`).join("")
    || `<p class="muted">No sleepers right now: recent insider buying is either already in the news or already priced up.</p>`;
  document.querySelectorAll("#tab-pulse [data-open]").forEach((el) => el.addEventListener("click", (e) => {
    if (e.target.closest("a")) return;
    openSymbol(el.dataset.open);
  }));
  bindLive("pulse", $("#tab-pulse"));
  paintAgo();
}
document.querySelectorAll("#mover-seg button").forEach((b) => b.addEventListener("click", () => {
  pulseState.list = b.dataset.list;
  document.querySelectorAll("#mover-seg button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
  renderMovers();
}));

async function loadSellWatch() {
  if (!holdingsList.length) { $("#sw-card").hidden = true; return; }
  $("#sw-card").hidden = false;
  $("#sw-list").innerHTML = `<p class="muted">Checking ${holdingsList.length} holding${holdingsList.length === 1 ? "" : "s"}…</p>`;
  try {
    const { holdings } = await api("/api/sellwatch");
    $("#sw-list").innerHTML = holdings.map((h) => `<div class="sw-row clickable" data-open="${esc(h.symbol)}">
        <div class="sw-head"><b>${esc(h.symbol)}</b><span data-live="${esc(h.symbol)}" data-lf="price">—</span><span class="small" data-live="${esc(h.symbol)}" data-lf="chg"></span><span class="chip ${h.verdict === "Review" ? "chip-review" : h.verdict === "Watch" ? "dil" : h.verdict === "Couldn't check" ? "chip-muted" : "chip-ok"}">${esc(h.verdict)}</span>
          <span class="muted small">${h.weight != null ? h.weight.toFixed(1) + "% of portfolio" : ""}${h.unrealized_pct != null ? ` · <span class="${cls(h.unrealized_pct)}">${fmtPct(h.unrealized_pct)} vs cost</span>` : ""}${h.score != null ? ` · signal ${h.score > 0 ? "+" : ""}${h.score}` : ""}</span></div>
        ${h.flags.length ? `<ul>${h.flags.map((f) => `<li class="${f.severity > 1 ? "sev2" : ""}">${esc(f.text)}</li>`).join("")}</ul>` : `<p class="muted small">No rule fired.${h.error ? " (" + esc(h.error) + ")" : ""}</p>`}</div>`).join("");
    $("#sw-list").querySelectorAll("[data-open]").forEach((el) => el.addEventListener("click", () => openSymbol(el.dataset.open)));
    bindLive("pulse", $("#tab-pulse"));
  } catch (err) { $("#sw-list").innerHTML = `<p class="muted">${esc(err.message)}</p>`; }
}

// ---------------------------------------------------------------- live bindings
// Any element with data-live="SYM" repaints on every tick for that symbol. data-lf picks what
// it shows: price, chg, value (x data-qty), pnl / pnlpct (vs data-cost), since (vs data-p0),
// verdict (did the move since data-p0 go the data-side way).
function setSign(el, v) { el.classList.remove("up", "down"); const c = cls(v); if (c) el.classList.add(c); }
function paintBound(t, prev, root = document) {
  root.querySelectorAll(`[data-live="${CSS.escape(t.symbol)}"]`).forEach((el) => {
    const qty = +el.dataset.qty || 0, cost = +el.dataset.cost || 0, p0 = +el.dataset.p0 || 0;
    switch (el.dataset.lf) {
      case "price": el.textContent = fmtMoney(t.price); flash(el, t, prev); break;
      case "chg": el.textContent = fmtPct(t.change_pct, 2); setSign(el, t.change_pct); break;
      case "pill": el.textContent = fmtMoney(t.price); el.classList.toggle("pill-down", (t.change_pct ?? 0) < 0); flash(el, t, prev); break;
      case "value": el.textContent = fmtMoney(qty * t.price, 2); flash(el, t, prev); break;
      case "pnl": { const v = qty * t.price - cost; el.textContent = (v >= 0 ? "+" : "") + fmtMoney(v, 2); setSign(el, v); break; }
      case "pnlpct": { const v = cost ? (qty * t.price / cost - 1) * 100 : null; el.textContent = fmtPct(v, 2); setSign(el, v); break; }
      case "since": { const v = p0 ? (t.price / p0 - 1) * 100 : null; el.textContent = fmtPct(v, 2); setSign(el, v); break; }
      case "verdict": {
        if (!p0 || t.price === p0) { el.textContent = "—"; setSign(el, 0); break; }
        const right = (t.price > p0) === (el.dataset.side === "up");
        el.textContent = right ? "Right so far" : "Wrong so far"; setSign(el, right ? 1 : -1); break;
      }
    }
  });
}
function bindLive(owner, root) {
  const syms = [...new Set([...root.querySelectorAll("[data-live]")].map((e) => e.dataset.live))];
  Live.want(owner, syms);
  syms.forEach((s) => Live.prices[s] && paintBound(Live.prices[s], null, root));
}

// Your holdings, valued at the latest tick: header strip, Portfolio tiles and weights.
const Book = {
  pos: [], timer: null, lastValue: null,
  set(list) { this.pos = list || []; Live.want("book", this.pos.map((h) => h.symbol)); $("#sb-book").hidden = !this.pos.length; this.paint(); },
  has(sym) { return this.pos.some((h) => h.symbol === sym); },
  totals() {
    let value = 0, cost = 0, today = 0;
    const w = {};
    for (const h of this.pos) {
      const t = Live.prices[h.symbol];
      if (!t) continue;
      const v = h.quantity * t.price;
      value += v; cost += h.cost_basis; w[h.symbol] = v;
      if (t.change_pct != null) today += v - v / (1 + t.change_pct / 100);
    }
    for (const s in w) w[s] = value ? w[s] / value * 100 : null;
    return { value, cost, today, todayPct: value - today ? today / (value - today) * 100 : null,
             unreal: value - cost, unrealPct: cost ? (value / cost - 1) * 100 : null, weights: w };
  },
  schedule() { if (!this.timer) this.timer = setTimeout(() => { this.timer = null; this.paint(); }, 200); },
  paint() {
    if (!this.pos.length) return;
    const T = this.totals();
    const moved = this.lastValue != null && T.value !== this.lastValue ? { price: T.value } : null;
    const prev = moved && { price: this.lastValue };
    this.lastValue = T.value;
    document.querySelectorAll("[data-book]").forEach((el) => {
      const k = el.dataset.book;
      if (k === "value") { el.textContent = fmtMoney(T.value, 2); if (moved) flash(el, moved, prev); }
      else if (k === "today") { el.textContent = `${T.today >= 0 ? "+" : "-"}${fmtMoney(Math.abs(T.today), 2)} (${fmtPct(T.todayPct, 2)}) today`; setSign(el, T.today); }
      else if (k === "today-value") { el.textContent = (T.today >= 0 ? "+" : "-") + fmtMoney(Math.abs(T.today), 2); setSign(el, T.today); }
      else if (k === "today-pct") { el.textContent = fmtPct(T.todayPct, 2); setSign(el, T.todayPct); }
      else if (k === "unreal") { el.textContent = (T.unreal >= 0 ? "+" : "") + fmtMoney(T.unreal, 2); setSign(el, T.unreal); }
      else if (k === "unrealpct") { el.textContent = fmtPct(T.unrealPct, 2); setSign(el, T.unrealPct); }
      else if (k.startsWith("weight:")) { const v = T.weights[k.slice(7)]; el.textContent = v != null ? v.toFixed(2) + "%" : "—"; }
    });
  },
};
Live.onTick((t, prev) => { paintBound(t, prev); if (Book.has(t.symbol)) Book.schedule(); });

// ---------------------------------------------------------------- clocks
// US session from the New York clock (holidays aside: a recent stock tick's session wins).
const SESSION_EDGES = [[240, "pre"], [570, "regular"], [960, "post"], [1200, "closed"]];
function usSession(now = new Date()) {
  const et = new Date(now.toLocaleString("en-US", { timeZone: "America/New_York" }));
  const day = et.getDay(), mins = et.getHours() * 60 + et.getMinutes() + et.getSeconds() / 60;
  const weekday = (d) => d !== 0 && d !== 6;
  let name = "closed";
  if (weekday(day)) for (const [edge, n] of SESSION_EDGES) if (mins >= edge) name = n;
  for (let off = 0; off < 8; off++) {
    const d = (day + off) % 7;
    if (!weekday(d)) continue;
    for (const [edge, n] of SESSION_EDGES) {
      if (off === 0 && edge <= mins) continue;
      return { name, next: n, minutes: off * 1440 + edge - mins };
    }
  }
  return { name, next: "pre", minutes: 0 };
}
const fmtSpan = (m) => m >= 1440 ? `${Math.floor(m / 1440)}d ${Math.floor(m % 1440 / 60)}h` : m >= 60 ? `${Math.floor(m / 60)}h ${Math.floor(m % 60)}m` : `${Math.max(1, Math.round(m))}m`;
function ago(ts) {
  const s = Math.max(0, (Date.now() - ts) / 1000);
  return s < 5 ? "just now" : s < 60 ? `${Math.floor(s)}s ago` : s < 3600 ? `${Math.floor(s / 60)} min ago` : `${Math.floor(s / 3600)} h ago`;
}
function paintAgo() {
  document.querySelectorAll("[data-ago]").forEach((el) => {
    const v = el.dataset.ago, ts = /^\d+$/.test(v) ? +v : Date.parse(v);
    if (!isNaN(ts)) el.textContent = ago(ts);
  });
}
function paintClock() {
  const s = usSession();
  const server = Live.stockSession && Date.now() - Live.stockSession.at < 120000 ? Live.stockSession.name : null;
  const name = server || s.name;
  const label = { pre: "Pre-market", regular: "US market open", post: "After hours", closed: "US market closed" }[name];
  const nextLabel = { pre: "pre-market in", regular: "opens in", post: "closes in", closed: "after-hours ends in" }[s.next];
  $("#sb-session").textContent = `${label} · ${nextLabel} ${fmtSpan(s.minutes)} · crypto 24/7`;
  $("#sb-session").dataset.session = name;
  $("#sb-tick").textContent = Live.lastTick ? `last tick ${ago(Live.lastTick)}` : "waiting for prices…";
  paintAgo();
}
setInterval(paintClock, 1000);
paintClock();

// Lists that aren't prices refresh themselves while their tab is open.
const currentTab = () => document.querySelector('#tabs [aria-selected="true"]')?.dataset.tab;
setInterval(() => {
  if (document.hidden) return;
  const tab = currentTab(), now = Date.now();
  if (tab === "pulse" && pulseState.data && now - pulseState.loadedAt > 180000) loadPulse();
  if (tab === "dashboard" && now - newsLoadedAt > 120000) loadMarketNews();
  if (tab === "plan" && planState.data && now - planState.loadedAt > 300000) loadPlan();
  if (tab === "mynews" && now - mnState.loadedAt > 300000) loadMyNews();
  if (tab === "reading" && now - rdState.loadedAt > 600000) loadReading();
  if (tab === "radar" && now - rrState.loadedAt > 120000) loadRadar();
  if (tab === "home" && now - homeState.loadedAt > (homeState.range === "1d" ? 60000 : 600000)) loadHome(true);
  if (tab === "early" && typeof earlyState !== "undefined" && now - earlyState.loadedAt > 120000) loadEarly();
  if (tab === "people" && typeof peopleState !== "undefined" && now - peopleState.loadedAt > 1800000) { loadPeople(); loadPickers(); }
  if (tab === "hold" && typeof holdState !== "undefined" && holdState.data && now - holdState.loadedAt > 300000) loadHold();
}, 15000);

// ---------------------------------------------------------------- strategy plan
const planState = { data: null, loadedAt: 0 };
const PLAN_CASH_KEY = "plumbline.plan.cash";
try { $("#plan-cash").value = localStorage.getItem(PLAN_CASH_KEY) || ""; } catch { /* storage blocked */ }
$("#plan-form").addEventListener("submit", (e) => {
  e.preventDefault();
  try { localStorage.setItem(PLAN_CASH_KEY, $("#plan-cash").value); } catch { /* storage blocked */ }
  loadPlan();
});
const SELLS = new Set(["Sell", "Trim"]);
const ACT_CLASS = { Sell: "act-sell", Trim: "act-trim", Add: "act-add", Buy: "act-buy", Hold: "act-hold" };
const fmtShares = (x) => Number(x).toLocaleString(undefined, { maximumFractionDigits: 4 });
const localDate = () => new Date().toLocaleDateString("en-CA");
const rhUrl = (sym) => isCryptoSym(sym) ? `https://robinhood.com/crypto/${encodeURIComponent(sym.replace(/-USD$/, ""))}`
  : `https://robinhood.com/stocks/${encodeURIComponent(sym)}`;

async function loadPlan() {
  const cash = Math.max(0, +($("#plan-cash").value || 0));
  if (!planState.data) $("#plan-meta").textContent = "Checking every holding… (up to a minute the first time)";
  try {
    planState.data = await api("/api/plan?cash=" + cash);
    planState.loadedAt = Date.now();
    renderPlan();
  } catch (err) { $("#plan-meta").textContent = err.message; }
}
function planCard(a) {
  const s = esc(a.symbol), sell = SELLS.has(a.action);
  return `<div class="card plan-card ${ACT_CLASS[a.action]}">
    <div class="pc-head"><span class="act">${esc(a.action)}</span>
      <button type="button" class="linkish pc-sym" data-open="${s}">${esc(a.symbol.replace(/-USD$/, ""))}</button>
      <span class="pc-price" data-live="${s}" data-lf="price">${fmtMoney(a.price)}</span><span class="small" data-live="${s}" data-lf="chg"></span>
      <span class="muted small pc-w">${(a.current_weight * 100).toFixed(1)}% → ${(a.target_weight * 100).toFixed(1)}% of portfolio · limit ${(a.limit * 100).toFixed(0)}%</span></div>
    <p class="pc-order">${sell ? "Sell" : "Buy"} <b>${fmtShares(a.shares)}</b>${isCryptoSym(a.symbol) ? "" : " shares"} ≈
      <b data-live="${s}" data-lf="value" data-qty="${a.shares}">${fmtMoney(a.value, 2)}</b></p>
    <ul class="pc-why">${a.reasons.map((r) => `<li>${esc(r)}</li>`).join("")}</ul>
    ${a.tax ? `<p class="pc-tax small"><b>Tax:</b> ${esc(a.tax)}</p>` : ""}
    <div class="pc-buttons"><button type="button" data-ticket>Order ticket</button>
      <a class="button-secondary" href="${esc(rhUrl(a.symbol))}" target="_blank" rel="noopener noreferrer">Open in Robinhood ↗</a></div>
    <div class="ticket" hidden></div></div>`;
}
function openTicket(card, a) {
  const el = card.querySelector(".ticket");
  if (!el.hidden) { el.hidden = true; return; }
  const side = SELLS.has(a.action) ? "sell" : "buy";
  const t = Live.prices[a.symbol], px = t ? t.price : a.price;
  const limit = +(side === "sell" ? px * 0.998 : px * 1.002).toFixed(px < 1 ? 4 : 2);
  const ext = t && ["pre", "post", "closed"].includes(t.session);
  el.innerHTML = `<div class="ticket-grid">
      <label>Side<output>${side === "sell" ? "Sell" : "Buy"} ${esc(a.symbol)}</output></label>
      <label>Quantity<input type="number" step="any" min="0" name="qty" value="${a.shares}"></label>
      <label>Order type<output>Limit</output></label>
      <label>Limit price<input type="number" step="any" min="0" name="limit" value="${limit}"></label>
      <label>Estimated total<output name="est">${fmtMoney(a.shares * limit, 2)}</output></label>
      <label>Time in force<output>${ext ? "Good for day · extended hours" : "Good for day"}</output></label></div>
    <p class="muted small">The limit sits 0.2% ${side === "sell" ? "below" : "above"} the live price, so it fills without chasing the price.
      ${ext && !isCryptoSym(a.symbol) ? "Outside regular hours only limit orders work and spreads are wider." : ""}</p>
    <div class="pc-buttons"><button type="button" data-copy>Copy order</button>
      <button type="button" class="secondary" data-record>It filled: record the trade</button><span class="muted small" data-status></span></div>`;
  el.hidden = false;
  const q = el.querySelector('[name="qty"]'), l = el.querySelector('[name="limit"]'), est = el.querySelector('[name="est"]');
  const status = (m) => { el.querySelector("[data-status]").textContent = m; };
  const upd = () => { est.textContent = fmtMoney((+q.value || 0) * (+l.value || 0), 2); };
  q.addEventListener("input", upd); l.addEventListener("input", upd);
  el.querySelector("[data-copy]").addEventListener("click", async () => {
    const txt = `${side.toUpperCase()} ${q.value} ${a.symbol} LIMIT ${l.value} DAY`;
    try { await navigator.clipboard.writeText(txt); status("Copied: " + txt); } catch { status(txt); }
  });
  el.querySelector("[data-record]").addEventListener("click", async () => {
    if (!(+q.value > 0 && +l.value > 0)) { status("Enter the quantity and price that filled."); return; }
    if (!confirm(`Record ${side} ${q.value} ${a.symbol} at ${fmtMoney(+l.value)} today? Only once it has filled in Robinhood.`)) return;
    try {
      await api("/api/transactions", { method: "POST", body: JSON.stringify({ symbol: a.symbol, side, quantity: +q.value, price: +l.value, fees: 0, date: localDate() }) });
      status("Recorded. Rebuilding the plan…");
      await loadHoldings();
      loadPlan();
    } catch (err) { status(err.message); }
  });
}
function renderPlan() {
  const d = planState.data, T = d.totals, acts = d.actions.filter((a) => a.action !== "Hold"), holds = d.actions.filter((a) => a.action === "Hold");
  $("#plan-meta").innerHTML = `Built <span data-ago="${planState.loadedAt}"></span>; rebuilt every 5 minutes. Shares and values move with the live price.`;
  $("#plan-tiles").innerHTML = [
    tile("Invested now", `<span data-book="value">${fmtMoney(T.invested, 2)}</span>`, `plus ${fmtMoney(T.cash, 0)} cash`),
    tile("Plan sells", fmtMoney(T.sell_value, 0), `${d.actions.filter((a) => SELLS.has(a.action)).length} sell or trim`),
    tile("Plan buys", fmtMoney(T.buy_value, 0), `${d.actions.filter((a) => a.action === "Add" || a.action === "Buy").length} add or buy`),
    tile("Cash after", fmtMoney(Math.abs(T.cash_after) < 0.5 ? 0 : T.cash_after, 0), "if every order fills"),
  ].join("");
  let html = acts.map(planCard).join("");
  if (!d.actions.length) html = `<div class="card"><p class="muted">No holdings yet. Import your Robinhood history in Portfolio (or record trades), and enter cash above to see buys from your watchlist and the sleepers.</p></div>`;
  else if (!acts.length) html = `<div class="card"><p class="muted">No trades today: nothing is oversized or flagged enough to act on${T.cash ? ", and no candidate cleared the buy rules" : ", and there's no cash to add with"}.</p></div>`;
  if (holds.length) html += `<div class="card"><h2>Hold</h2><div class="hold-list">${holds.map((h) => { const s = esc(h.symbol); return `<div class="hold-row clickable" data-open="${s}">
      <b>${esc(h.symbol.replace(/-USD$/, ""))}</b><span data-live="${s}" data-lf="price">${fmtMoney(h.price)}</span><span class="small" data-live="${s}" data-lf="chg"></span>
      <span class="muted small">${(h.current_weight * 100).toFixed(1)}% · limit ${(h.limit * 100).toFixed(0)}% · ${esc(h.reasons[0])}</span></div>`; }).join("")}</div></div>`;
  $("#plan-actions").innerHTML = html;
  $("#plan-actions").querySelectorAll(".plan-card").forEach((card, i) => card.querySelector("[data-ticket]").addEventListener("click", () => openTicket(card, acts[i])));
  $("#plan-actions").querySelectorAll("[data-open]").forEach((el) => el.addEventListener("click", () => openSymbol(el.dataset.open)));

  const hist = d.history || [];
  $("#plan-history").innerHTML = hist.length ? `<thead><tr><th>Day</th><th>Call</th><th>Symbol</th><th class="num">Price then</th><th class="num">Now</th><th class="num">Since</th><th>So far</th></tr></thead><tbody>` +
    hist.map((h) => { const s = esc(h.symbol), up = h.action === "Add" || h.action === "Buy"; return `<tr class="clickable" data-open="${s}"><td>${esc(h.day)}</td>
      <td><span class="act-chip ${ACT_CLASS[h.action]}">${esc(h.action)}</span></td><td><b>${s}</b></td><td class="num">${fmtMoney(h.price)}</td>
      <td class="num" data-live="${s}" data-lf="price">—</td><td class="num" data-live="${s}" data-lf="since" data-p0="${h.price}">—</td>
      <td data-live="${s}" data-lf="verdict" data-p0="${h.price}" data-side="${up ? "up" : "down"}">—</td></tr>`; }).join("") + "</tbody>"
    : `<tr><td class="muted">Nothing logged yet: today's calls are logged the first time the plan has any.</td></tr>`;
  $("#plan-history").querySelectorAll("[data-open]").forEach((el) => el.addEventListener("click", () => openSymbol(el.dataset.open)));
  const r = d.rules, pct = (x) => Math.round(x * 100) + "%";
  $("#plan-rules").innerHTML = [
    `Size limit per position: the most a normal (1-sigma) month can cost is 2% of the portfolio, capped at ${pct(r.max_weight)}; ${pct(r.default_cap)} when volatility is unknown.`,
    `Sell: warning flags add up to ${r.sell_flags}+ and the composite signal is ${r.sell_score} or worse.`,
    `Trim to the limit: the position is more than ${r.oversize}× its limit.`,
    `Trim by half: the flags add up to ${r.trim_flags}+ (the sell watch's "Review").`,
    `Add (at most ${pct(r.starter_weight)} more of the portfolio per plan, never past the limit): no serious flag, signal +${r.add_score} or better, above the 200-day average, under 60% of its limit. Paid only from cash and this plan's sales.`,
    `Over its limit but under ${r.oversize}×: hold, and say so.`,
    `Buy a starter position (${pct(r.starter_weight)}, or the limit if smaller): your watchlist and the latest sleepers, signal +${r.buy_score} or better, above the 200-day average, no serious flag, best first while money lasts.`,
    "Trades under $25 are skipped. Taxes assume the oldest shares are sold first; over a year held is long-term (US).",
  ].map((x) => `<li>${esc(x)}</li>`).join("");
  bindLive("plan", $("#tab-plan"));
  Book.paint();
  paintAgo();
  paintPlanScore();
}
function paintPlanScore() {
  const cells = [...document.querySelectorAll('#plan-history [data-lf="verdict"]')];
  if (!cells.length) { $("#plan-score").textContent = ""; return; }
  const right = cells.filter((c) => c.classList.contains("up")).length, wrong = cells.filter((c) => c.classList.contains("down")).length;
  const oldest = planState.data.history[planState.data.history.length - 1].day;
  const days = Math.round((Date.now() - Date.parse(oldest)) / 86400000);
  $("#plan-score").textContent = `Right so far on ${right} of ${right + wrong} calls (oldest ${days} day${days === 1 ? "" : "s"} ago).` +
    (days < 90 ? " Far too early to mean anything." : "");
}
Live.onTick(() => { if (currentTab() === "plan" && planState.data) paintPlanScoreSoon(); });
let planScoreTimer = null;
function paintPlanScoreSoon() { if (!planScoreTimer) planScoreTimer = setTimeout(() => { planScoreTimer = null; paintPlanScore(); }, 500); }

// ---------------------------------------------------------------- heads-up bell
const huState = { unread: 0, seen: new Set() };
async function loadHeadsup(markRead = false) {
  let h;
  try { h = await api("/api/headsup"); } catch { return; }
  const n = markRead ? 0 : h.unread;
  $("#sb-bell-n").textContent = n;
  $("#sb-bell").classList.toggle("has", n > 0);
  // Desktop notification for anything new while the page is open (if allowed).
  const fresh = h.items.filter((i) => !i.read && !huState.seen.has(i.key));
  if (huState.seen.size && fresh.length && "Notification" in window && Notification.permission === "granted") {
    fresh.slice(0, 3).forEach((i) => { try { new Notification(i.title, { body: i.body }); } catch { /* blocked */ } });
  }
  h.items.forEach((i) => huState.seen.add(i.key));
  const kinds = { radar: "Filing", news: "Loud news", reading: "Pro mention", topic: "Hot topic", early: "Early wire", people: "Following" };
  $("#hu-list").innerHTML = h.items.length ? h.items.slice(0, 25).map((i) => `<li class="hu lvl${i.level}${i.read ? "" : " unread"}">
      <span class="hu-kind">${esc(kinds[i.kind] || i.kind)}</span>
      <div><a href="${esc(safeUrl(i.url))}" target="_blank" rel="noopener noreferrer">${esc(i.title)}</a>
      <div class="muted small">${esc(i.body)} · <span data-ago="${esc(i.at)}"></span></div></div></li>`).join("")
    : `<li class="muted">Nothing yet. While the app runs it checks SEC filings every 2 minutes and news every 10 for everything you own or watch.</li>`;
  $("#hu-push").innerHTML = h.push ? "Phone push: on" : `Phone push: off (set NTFY_TOPIC in .env)` +
    ("Notification" in window && Notification.permission === "default" ? ` · <button type="button" class="ghost" id="hu-desktop">Enable desktop alerts</button>` : "");
  const d = $("#hu-desktop");
  if (d) d.addEventListener("click", () => Notification.requestPermission().then(() => loadHeadsup()));
  if (markRead && h.unread) api("/api/headsup/read", { method: "POST" }).catch(() => {});
  paintAgo();
}
$("#sb-bell").addEventListener("click", () => selectTab("mynews"));
setInterval(() => { if (!document.hidden) loadHeadsup(currentTab() === "mynews"); }, 60000);

// ---------------------------------------------------------------- news for your holdings
const mnState = { data: null, loadedAt: 0, filter: "" };
const moodTxt = (m) => `<span class="${cls(m)}">${m > 0 ? "+" : ""}${Number(m).toFixed(2)}</span>`;
async function loadMyNews() {
  if (!mnState.data) $("#mn-meta").textContent = "Reading the news for each holding… (up to a minute the first time)";
  try {
    mnState.data = await api("/api/mynews"); mnState.loadedAt = Date.now(); renderMyNews();
  } catch (err) { $("#mn-meta").textContent = err.message; }
}
function renderMyNews() {
  const d = mnState.data;
  if (!d.symbols.length && !d.feed.length) {
    $("#mn-grid").innerHTML = `<p class="muted">Import your Robinhood, Coinbase or Stash holdings (Portfolio tab) or add symbols to your watchlist.</p>`;
    $("#mn-feed").innerHTML = ""; $("#mn-meta").textContent = ""; return;
  }
  $("#mn-meta").innerHTML = `Updated <span data-ago="${esc(d.generated_at)}"></span> · every 5 minutes${d.errors.length ? ` · ${d.errors.length} not found` : ""}`;
  const held = new Set(d.held || []);
  $("#mn-grid").innerHTML = d.symbols.map((x) => { const s = esc(x.symbol); return `<div class="mn-card${x.loud ? " loud" : ""}">
      <div class="mn-head"><button type="button" class="linkish" data-open="${s}">${esc(x.symbol.replace(/-USD$/, ""))}</button>
        <span data-live="${s}" data-lf="price">—</span><span class="small" data-live="${s}" data-lf="chg"></span>
        ${x.loud ? `<span class="chip chip-review">Loud · ${x.heat}×</span>` : ""}${held.has(x.symbol) ? "" : `<span class="chip chip-muted">watching</span>`}</div>
      <div class="small muted">${x.last_24h} today · ${x.daily_pace}/day usual · mood ${moodTxt(x.mood)}${x.terms.length ? " · " + esc(x.terms.join(", ")) : ""}</div>
      <ul>${x.headlines.slice(0, 3).map((a) => `<li><a href="${esc(safeUrl(a.url))}" target="_blank" rel="noopener noreferrer">${esc(a.title)}</a> <span class="muted small">${esc(a.source)} · <span data-ago="${esc(a.published)}"></span></span></li>`).join("")}</ul>
      ${x.deep.length ? `<div class="small"><b>In depth:</b> ${x.deep.map((a) => `<a href="${esc(safeUrl(a.url))}" target="_blank" rel="noopener noreferrer">${esc(a.source)}</a>`).join(", ")}</div>` : ""}
    </div>`; }).join("");
  const syms = [...new Set(d.feed.map((a) => a.symbol))];
  $("#mn-filter").innerHTML = [`<button type="button" class="chipbtn" data-f="" aria-pressed="${!mnState.filter}">All</button>`]
    .concat(syms.map((s) => `<button type="button" class="chipbtn" data-f="${esc(s)}" aria-pressed="${mnState.filter === s}">${esc(s.replace(/-USD$/, ""))}</button>`)).join("");
  $("#mn-filter").querySelectorAll("button").forEach((b) => b.addEventListener("click", () => { mnState.filter = b.dataset.f; renderMyNews(); }));
  $("#mn-feed").innerHTML = d.feed.filter((a) => !mnState.filter || a.symbol === mnState.filter).slice(0, 80).map((a) => `<li>
      <div><span class="tag">${esc(a.symbol.replace(/-USD$/, ""))}</span>${a.deep ? `<span class="tag tag-deep-coverage">In depth</span>` : ""}
      <a href="${esc(safeUrl(a.url))}" target="_blank" rel="noopener noreferrer">${esc(a.title)}</a></div>
      <div class="muted small">${esc(a.source)} · <span data-ago="${esc(a.published)}"></span> · mood ${moodTxt(a.sentiment || 0)}</div></li>`).join("")
    || `<li class="muted">No headlines in the last 48 hours.</li>`;
  document.querySelectorAll("#tab-mynews [data-open]").forEach((el) => el.addEventListener("click", () => openSymbol(el.dataset.open)));
  bindLive("mynews", $("#tab-mynews"));
  paintAgo();
}

// ---------------------------------------------------------------- reading room
const rdState = { data: null, loadedAt: 0, kind: "" };
async function loadReading() {
  if (!rdState.data) $("#rd-meta").textContent = "Reading 17 feeds…";
  try { rdState.data = await api("/api/reading"); rdState.loadedAt = Date.now(); renderReading(); }
  catch (err) { $("#rd-meta").textContent = err.message; }
}
const mentionTags = (m) => (m || []).map((s) => `<span class="tag tag-in-the-news">${esc(s.replace(/-USD$/, ""))}</span>`).join("");
function renderReading() {
  const d = rdState.data;
  $("#rd-meta").innerHTML = `Updated <span data-ago="${esc(d.generated_at)}"></span>`;
  $("#rd-picks").innerHTML = d.picks.map((p) => `<li><div>${p.picked_by.length > 1 ? `<span class="tag tag-unusual-volume">Picked by both</span>` : ""}${mentionTags(p.mentions)}
      <a href="${esc(safeUrl(p.url))}" target="_blank" rel="noopener noreferrer">${esc(p.title)}</a></div>
      <div class="muted small">${esc(p.domain)} · ${esc(p.picked_by.join(" + "))}${p.section && p.section !== "Reads" ? " · " + esc(p.section) : ""} · <span data-ago="${esc(p.published)}"></span></div></li>`).join("")
    || `<li class="muted">No curated links in the last few days.</li>`;
  const multi = d.outlets.filter(([, n]) => n > 1);
  $("#rd-outlets").textContent = multi.length ? "Most-picked outlets: " + multi.slice(0, 8).map(([dm, n]) => `${dm} ×${n}`).join(" · ") : "";
  $("#rd-mentions").innerHTML = d.mentions.map((m) => `<li><div>${mentionTags(m.mentions)}<a href="${esc(safeUrl(m.url))}" target="_blank" rel="noopener noreferrer">${esc(m.title)}</a></div>
      <div class="muted small">${esc(m.source_name || (m.picked_by || []).join(" + "))} · <span data-ago="${esc(m.published)}"></span></div></li>`).join("")
    || `<li class="muted">None of your holdings is in the pro coverage right now.</li>`;
  $("#rd-topics").innerHTML = d.topics.map((t) => `<div class="topic${t.hot ? " hot" : ""}">
      <div class="topic-head"><b>${esc(t.name)}</b>${t.hot ? `<span class="chip chip-review">Heating up</span>` : ""}
        <span class="muted small">${t.last_24h} today · ${t.daily_pace}/day usual</span>
        <button type="button" class="ghost" data-del-topic="${esc(t.name)}" aria-label="Remove ${esc(t.name)}">✕</button></div>
      <ul>${t.latest.slice(0, 3).map((i) => `<li><a href="${esc(safeUrl(i.url))}" target="_blank" rel="noopener noreferrer">${esc(i.title)}</a> <span class="muted small">${esc(i.source_name)}</span></li>`).join("")}</ul></div>`).join("");
  $("#rd-topics").querySelectorAll("[data-del-topic]").forEach((b) => b.addEventListener("click", async () => {
    await api("/api/topics/" + encodeURIComponent(b.dataset.delTopic), { method: "DELETE" }); rdState.data = null; loadReading();
  }));
  renderDesks();
  paintAgo();
}
function renderDesks() {
  const items = rdState.data.items.filter((i) => !rdState.kind || i.kind === rdState.kind).slice(0, 80);
  $("#rd-items").innerHTML = items.map((i) => `<li><div>${mentionTags(i.mentions)}${i.topics.map((t) => `<span class="tag">${esc(t)}</span>`).join("")}
      <a href="${esc(safeUrl(i.url))}" target="_blank" rel="noopener noreferrer">${esc(i.title)}</a></div>
      <div class="muted small">${esc(i.source_name)} · <span data-ago="${esc(i.published)}"></span></div></li>`).join("") || `<li class="muted">Nothing here yet.</li>`;
  paintAgo();
}
document.querySelectorAll("#rd-kind button").forEach((b) => b.addEventListener("click", () => {
  rdState.kind = b.dataset.kind;
  document.querySelectorAll("#rd-kind button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
  if (rdState.data) renderDesks();
}));
$("#topic-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  try {
    await api("/api/topics", { method: "POST", body: JSON.stringify({ name: f.get("name"), terms: f.get("terms") }) });
    e.target.reset(); rdState.data = null; loadReading();
  } catch (err) { alert(err.message); }
});

// ---------------------------------------------------------------- radar
const rrState = { data: null, loadedAt: 0 };
async function loadRadar() {
  if (!rrState.data) $("#rr-meta").textContent = "Checking SEC filings for your companies…";
  try { rrState.data = await api("/api/radar"); rrState.loadedAt = Date.now(); renderRadar(); }
  catch (err) { $("#rr-meta").textContent = err.message; }
}
function radarRow(a, mine) {
  const s = a.symbol ? esc(a.symbol) : "";
  return `<li class="rr lvl${a.level}"><span class="rr-level">${esc(a.level_name)}</span>
    <div><div>${s ? `<button type="button" class="linkish rr-sym" data-open="${s}">${s}</button>` : ""}<b>${esc(a.headline)}</b></div>
    <div class="small">${esc(a.company)} · ${esc(a.form)} · ${esc(a.filed)}${a.when ? ` · <span data-ago="${esc(a.when)}"></span>` : ""} ·
      <a href="${esc(safeUrl(a.url))}" target="_blank" rel="noopener noreferrer">Read the filing ↗</a></div>
    ${mine ? `<div class="muted small">${esc(a.why)}</div>` : ""}
    ${a.items && a.items.length > 1 ? `<div class="muted small">Items: ${a.items.map((i) => esc(i.code + " " + i.label)).join("; ")}</div>` : ""}</div></li>`;
}
function renderRadar() {
  const d = rrState.data;
  $("#rr-meta").innerHTML = `${d.watching} companies watched · feed checked <span data-ago="${esc(d.scanned_at)}"></span>, every 2 minutes${d.errors.length ? ` · ${d.errors.length} source errors` : ""}`;
  $("#rr-mine").innerHTML = d.mine.map((a) => radarRow(a, true)).join("")
    || `<li class="muted">${d.watching ? `No scary filings in the last 90 days for your ${d.watching} companies.` : "No stock holdings or watchlist companies yet (crypto has no SEC filings)."}</li>`;
  paintMarketRadar();
}
function paintMarketRadar() {
  const lvl = +$("#rr-level").value, listed = $("#rr-listed").checked;
  const rows = rrState.data.market.filter((a) => a.level >= lvl && (!listed || a.symbol));
  $("#rr-market").innerHTML = rows.slice(0, 150).map((a) => radarRow(a, false)).join("") || `<li class="muted">Nothing at this level in the last 3 days.</li>`;
  document.querySelectorAll("#tab-radar [data-open]").forEach((el) => el.addEventListener("click", () => openSymbol(el.dataset.open)));
  paintAgo();
}
$("#rr-level").addEventListener("change", () => rrState.data && paintMarketRadar());
$("#rr-listed").addEventListener("change", () => rrState.data && paintMarketRadar());

// ---------------------------------------------------------------- home (Robinhood-style)
const homeState = { range: "1d", data: null, loadedAt: 0, hover: false, sparks: {}, sparksAt: 0 };
const RANGE_WORDS = { "1d": "Today", "1w": "Past week", "1m": "Past month", "3m": "Past 3 months", "1y": "Past year", all: "All time" };
async function loadHome(quiet = false) {
  const hasHoldings = holdingsList.length > 0;
  $("#home-empty").hidden = hasHoldings;
  if (!quiet) $("#home-chart").innerHTML = hasHoldings ? `<p class="muted small">Loading…</p>` : "";
  loadCash();
  renderHomeLists();
  loadHomeFeeds();
  if (!quiet || Date.now() - briefState.loadedAt > 600000) loadBrief();
  if (!hasHoldings) { $("#home-value").textContent = fmtMoney(0, 2); $("#home-gain").innerHTML = "&nbsp;"; return; }
  try {
    const d = await api("/api/portfolio/history?range=" + homeState.range);
    homeState.data = d; homeState.loadedAt = Date.now();
    drawHome();
  } catch (err) { $("#home-chart").innerHTML = `<p class="muted small">${esc(err.message)}</p>`; }
}
async function loadCash() {
  try { const { cash } = await api("/api/cash"); $("#bp-btn").textContent = fmtMoney(cash, 2); $("#bp-input").value = cash || ""; } catch { /* offline */ }
}
$("#bp-btn").addEventListener("click", () => { $("#bp-form").hidden = !$("#bp-form").hidden; if (!$("#bp-form").hidden) $("#bp-input").focus(); });
$("#bp-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try { await api("/api/cash", { method: "POST", body: JSON.stringify({ cash: Math.max(0, +$("#bp-input").value || 0) }) }); $("#bp-form").hidden = true; loadCash(); }
  catch (err) { alert(err.message); }
});
$("#home-import").addEventListener("click", () => selectTab("portfolio"));
document.querySelectorAll("[data-goto]").forEach((b) => b.addEventListener("click", () => selectTab(b.dataset.goto)));
document.querySelectorAll("#home-ranges button").forEach((b) => b.addEventListener("click", () => {
  homeState.range = b.dataset.range;
  document.querySelectorAll("#home-ranges button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
  loadHome();
}));
function homeSeries() {
  const d = homeState.data;
  if (!d) return [];
  const pts = d.points.slice();
  // The 1D line ends at the live value.
  if (homeState.range === "1d" && Book.pos.length) {
    const v = Book.totals().value, now = Math.floor(Date.now() / 1000);
    if (v) { if (pts.length && now - pts[pts.length - 1].t < 300) pts[pts.length - 1] = { t: pts[pts.length - 1].t, v }; else pts.push({ t: now, v }); }
  }
  return pts;
}
function paintHomeHeader(value, at) {
  const d = homeState.data;
  if (!d) return;
  const range = homeState.range;
  let gain, pct;
  if (range === "1d") { const base = d.reference || d.start; gain = value - base; pct = base ? gain / base * 100 : null; }
  else { const flow = range === "all" ? (d.net_deposits || 0) : (d.net_deposits || 0); const start = range === "all" ? 0 : d.start;
    gain = value - start - flow; const basis = start + Math.max(flow, 0); pct = basis ? gain / basis * 100 : null; }
  $("#home-value").textContent = fmtMoney(value, 2);
  const up = gain >= 0;
  $("#home-gain").innerHTML = `<span class="${up ? "gain" : "loss"}">${up ? "▲" : "▼"} ${fmtMoney(Math.abs(gain), 2)} (${fmtPct(Math.abs(pct ?? 0), 2).replace("+", "")})</span> <span class="muted">${at ? esc(at) : RANGE_WORDS[range]}</span>`;
  document.documentElement.style.setProperty("--trend", up ? "var(--gain)" : "var(--loss)");
}
function drawHome() {
  const el = $("#home-chart"), pts = homeSeries(), d = homeState.data;
  if (!d || pts.length < 2) { el.innerHTML = `<p class="muted small">Not enough history for this range yet.</p>`; if (d) paintHomeHeader(Book.totals().value || d.end || 0); return; }
  const W = el.clientWidth || 700, H = Math.max(200, Math.min(300, W * 0.42)), pad = { t: 10, r: 4, b: 8, l: 4 };
  const ref = homeState.range === "1d" ? (d.reference || pts[0].v) : pts[0].v;
  const vs = pts.map((p) => p.v).concat([ref]);
  const lo = Math.min(...vs), hi = Math.max(...vs), span = hi - lo || hi * 0.01 || 1;
  const t0 = pts[0].t, t1 = pts[pts.length - 1].t || t0 + 1;
  const X = (t) => pad.l + (t - t0) / (t1 - t0 || 1) * (W - pad.l - pad.r);
  const Y = (v) => pad.t + (hi - v) / span * (H - pad.t - pad.b);
  const last = pts[pts.length - 1];
  const line = pts.map((p, i) => `${i ? "L" : "M"}${X(p.t).toFixed(1)},${Y(p.v).toFixed(1)}`).join("");
  el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" width="100%" height="${H}" aria-hidden="true">
    ${homeState.range === "1d" ? `<line x1="0" x2="${W}" y1="${Y(ref)}" y2="${Y(ref)}" stroke="var(--axis)" stroke-dasharray="1 5" stroke-linecap="round" stroke-width="2"/>` : ""}
    <path d="${line}" fill="none" stroke="var(--trend)" stroke-width="2.2" stroke-linejoin="round"/>
    <circle class="pulse-dot" cx="${X(last.t)}" cy="${Y(last.v)}" r="4" fill="var(--trend)"/>
    <g class="cross" visibility="hidden"><line y1="0" y2="${H}" stroke="var(--axis)"/><circle r="4.5" fill="var(--trend)" stroke="var(--surface)" stroke-width="2"/></g>
  </svg>`;
  if (!homeState.hover) paintHomeHeader(homeState.range === "1d" ? (Book.totals().value || last.v) : last.v);
  const svg = el.querySelector("svg"), cross = svg.querySelector(".cross");
  const fmtT = (t) => { const dt = new Date(t * 1000); return homeState.range === "1d" || homeState.range === "1w" ? dt.toLocaleString([], { weekday: homeState.range === "1w" ? "short" : undefined, hour: "numeric", minute: "2-digit" }) : dt.toLocaleDateString([], { month: "short", day: "numeric", year: "numeric" }); };
  svg.addEventListener("pointermove", (e) => {
    const r = svg.getBoundingClientRect(), x = (e.clientX - r.left) * (W / r.width);
    const t = t0 + (x - pad.l) / (W - pad.l - pad.r) * (t1 - t0);
    let best = pts[0];
    for (const p of pts) if (Math.abs(p.t - t) < Math.abs(best.t - t)) best = p;
    homeState.hover = true;
    cross.setAttribute("visibility", "visible");
    cross.querySelector("line").setAttribute("x1", X(best.t)); cross.querySelector("line").setAttribute("x2", X(best.t));
    cross.querySelector("circle").setAttribute("cx", X(best.t)); cross.querySelector("circle").setAttribute("cy", Y(best.v));
    paintHomeHeader(best.v, fmtT(best.t));
  });
  svg.addEventListener("pointerleave", () => { homeState.hover = false; cross.setAttribute("visibility", "hidden"); drawHome(); });
}
let homeTimer = null;
Live.onTick((t) => {
  if (currentTab() !== "home" || !Book.has(t.symbol) || homeState.hover) return;
  if (!homeTimer) homeTimer = setTimeout(() => { homeTimer = null; if (homeState.range === "1d") drawHome(); else if (homeState.data) paintHomeHeader(Book.totals().value || homeState.data.end); }, 700);
});

function sparkSvg(sym, w = 72, h = 28) {
  const s = homeState.sparks[sym];
  if (!s || !s.p || s.p.length < 2) return `<svg width="${w}" height="${h}" aria-hidden="true"></svg>`;
  const ps = s.p, ref = s.reference ?? ps[0], lo = Math.min(...ps, ref), hi = Math.max(...ps, ref), span = hi - lo || 1;
  const X = (i) => i / (ps.length - 1) * (w - 2) + 1, Y = (v) => 2 + (hi - v) / span * (h - 4);
  const up = ps[ps.length - 1] >= ref;
  return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" aria-hidden="true"><line x1="0" x2="${w}" y1="${Y(ref)}" y2="${Y(ref)}" stroke="var(--axis)" stroke-dasharray="1 3"/><path d="${ps.map((v, i) => `${i ? "L" : "M"}${X(i).toFixed(1)},${Y(v).toFixed(1)}`).join("")}" fill="none" stroke="${up ? "var(--gain)" : "var(--loss)"}" stroke-width="1.5"/></svg>`;
}
function rhRow(sym, sub) {
  const s = esc(sym);
  return `<button type="button" class="rh-row" data-open="${s}">
    <span class="rh-sym"><b>${esc(sym.replace(/-USD$/, ""))}</b><span class="muted small">${sub}</span></span>
    <span class="rh-spark" data-spark="${s}">${sparkSvg(sym)}</span>
    <span class="rh-pill" data-live="${s}" data-lf="pill">${Live.prices[sym] ? fmtMoney(Live.prices[sym].price) : "—"}</span></button>`;
}
function renderHomeLists() {
  const stocks = holdingsList.filter((h) => !isCryptoSym(h.symbol)), crypto = holdingsList.filter((h) => isCryptoSym(h.symbol));
  const shares = (h) => `${h.quantity.toLocaleString(undefined, { maximumFractionDigits: isCryptoSym(h.symbol) ? 6 : 4 })} ${isCryptoSym(h.symbol) ? "" : "shares"}${(h.accounts || []).length ? " · " + h.accounts.join(", ") : ""}`;
  $("#home-stocks").innerHTML = stocks.map((h) => rhRow(h.symbol, esc(shares(h)))).join("") || `<p class="muted small">No stocks yet.</p>`;
  $("#home-crypto").innerHTML = crypto.map((h) => rhRow(h.symbol, esc(shares(h)))).join("") || `<p class="muted small">No coins yet.</p>`;
  const held = new Set(holdingsList.map((h) => h.symbol));
  $("#home-watch").innerHTML = watchlist.filter((w) => !held.has(w)).map((w) => rhRow(w, "")).join("") || `<p class="muted small">Add symbols from any page with Watch.</p>`;
  document.querySelectorAll("#tab-home .rh-row").forEach((b) => b.addEventListener("click", () => openSymbol(b.dataset.open)));
  bindLive("home", $("#tab-home"));
  const syms = [...new Set([...holdingsList.map((h) => h.symbol), ...watchlist])];
  if (syms.length && Date.now() - homeState.sparksAt > 120000) {
    homeState.sparksAt = Date.now();
    api("/api/sparklines?symbols=" + encodeURIComponent(syms.join(","))).then((sp) => {
      homeState.sparks = sp;
      document.querySelectorAll("#tab-home [data-spark]").forEach((el) => { el.innerHTML = sparkSvg(el.dataset.spark); });
    }).catch(() => {});
  }
}
async function loadHomeFeeds() {
  try {
    const n = await api("/api/mynews");
    $("#home-news").innerHTML = (n.feed || []).slice(0, 6).map((a) => `<li><div><span class="tag">${esc(a.symbol.replace(/-USD$/, ""))}</span>
      <a href="${esc(safeUrl(a.url))}" target="_blank" rel="noopener noreferrer">${esc(a.title)}</a></div>
      <div class="muted small">${esc(a.source)} · <span data-ago="${esc(a.published)}"></span></div></li>`).join("") || `<li class="muted">No news for your holdings yet.</li>`;
  } catch { $("#home-news").innerHTML = `<li class="muted">News unavailable.</li>`; }
  try {
    const e = await api("/api/early?limit=6");
    $("#home-early").innerHTML = (e.signals || []).slice(0, 6).map(earlyItem).join("") || `<li class="muted">Nothing early right now.</li>`;
    document.querySelectorAll("#home-early [data-open]").forEach((el) => el.addEventListener("click", (ev) => { if (!ev.target.closest("a")) openSymbol(el.dataset.open); }));
  } catch { $("#home-early").innerHTML = `<li class="muted">Early wire unavailable.</li>`; }
  paintAgo();
}

// ---------------------------------------------------------------- trade ticket (any symbol)
const tradeState = { sym: null, side: "buy", type: "market", unit: "shares" };
const brokerFor = (sym, accounts) => {
  const acct = (accounts || [])[0] || (isCryptoSym(sym) ? "Coinbase" : "Robinhood");
  const base = sym.replace(/-USD$/, "");
  const url = acct === "Coinbase" ? `https://www.coinbase.com/price/${encodeURIComponent(base.toLowerCase())}`
    : isCryptoSym(sym) ? `https://robinhood.com/crypto/${encodeURIComponent(base)}` : `https://robinhood.com/stocks/${encodeURIComponent(sym)}`;
  return { acct, url };
};
function openTradeTicket(sym, side = "buy") {
  Object.assign(tradeState, { sym, side, type: "market", unit: "shares" });
  const held = holdingsList.find((h) => h.symbol === sym);
  const { acct, url } = brokerFor(sym, held && held.accounts);
  $("#trade-title").textContent = `Trade ${sym.replace(/-USD$/, "")}`;
  $("#trade-body").innerHTML = `
    <div class="seg" id="tt-side"><button type="button" data-v="buy">Buy</button><button type="button" data-v="sell">Sell</button></div>
    <div class="tt-grid">
      <label>Order type<select id="tt-type"><option value="market">Market</option><option value="limit">Limit</option></select></label>
      <label>Amount in<select id="tt-unit"><option value="shares">${isCryptoSym(sym) ? "Coins" : "Shares"}</option><option value="dollars">Dollars</option></select></label>
      <label><span id="tt-qty-label">${isCryptoSym(sym) ? "Coins" : "Shares"}</span><input id="tt-qty" type="number" min="0" step="any" inputmode="decimal" value="${held && side === "sell" ? held.quantity : ""}"></label>
      <label id="tt-limit-wrap" hidden>Limit price<input id="tt-limit" type="number" min="0" step="any" inputmode="decimal"></label>
      <label>Market price<output id="tt-price" data-live="${esc(sym)}" data-lf="price">${Live.prices[sym] ? fmtMoney(Live.prices[sym].price) : "—"}</output></label>
      <label>Estimated <span id="tt-est-word">cost</span><output id="tt-est">—</output></label>
      <label>Record in<select id="tt-acct">${["Robinhood", "Coinbase", "Stash", "Other"].map((a) => `<option ${a === acct ? "selected" : ""}>${a}</option>`).join("")}</select></label>
      <label>You own<output>${held ? held.quantity.toLocaleString(undefined, { maximumFractionDigits: 6 }) : "0"}</output></label>
    </div>
    <p class="muted small" id="tt-note"></p>
    <div class="pc-buttons">
      <button type="button" id="tt-copy">Copy order</button>
      <a class="button-secondary" id="tt-open" href="${esc(url)}" target="_blank" rel="noopener noreferrer">Open in ${esc(acct === "Coinbase" ? "Coinbase" : "Robinhood")} ↗</a>
      <button type="button" class="secondary" id="tt-record">It filled: record it</button>
    </div>
    <div class="tt-confirm" id="tt-confirm" hidden></div>
    <p class="muted small" id="tt-status"></p>`;
  const $$ = (id) => $("#" + id);
  const paintSide = () => {
    document.querySelectorAll("#tt-side button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.v === tradeState.side)));
    $$("tt-est-word").textContent = tradeState.side === "buy" ? "cost" : "credit";
    $("#trade-modal").dataset.side = tradeState.side;
  };
  const price = () => tradeState.type === "limit" && +$$("tt-limit").value > 0 ? +$$("tt-limit").value : (Live.prices[sym]?.price || 0);
  const shares = () => tradeState.unit === "dollars" ? (price() ? (+$$("tt-qty").value || 0) / price() : 0) : (+$$("tt-qty").value || 0);
  const update = () => {
    const p = price(), q = shares();
    $$("tt-est").textContent = p && q ? fmtMoney(q * p, 2) + (tradeState.unit === "dollars" ? ` · ${q.toLocaleString(undefined, { maximumFractionDigits: 6 })} ${isCryptoSym(sym) ? "coins" : "shares"}` : "") : "—";
    const t = Live.prices[sym];
    const ext = t && !isCryptoSym(sym) && ["pre", "post", "closed"].includes(t.session);
    $$("tt-note").textContent = ext && tradeState.type === "market" ? "The market is closed: a market order waits for the open. Use a limit order to trade in extended hours."
      : tradeState.type === "limit" ? "A limit order fills only at your price or better." : "A market order fills at the next available price.";
  };
  tradeState.update = update;
  document.querySelectorAll("#tt-side button").forEach((b) => b.addEventListener("click", () => { tradeState.side = b.dataset.v; paintSide(); update(); }));
  $$("tt-type").addEventListener("change", (e) => {
    tradeState.type = e.target.value; $$("tt-limit-wrap").hidden = tradeState.type !== "limit";
    if (tradeState.type === "limit" && !$$("tt-limit").value && Live.prices[sym]) $$("tt-limit").value = (+Live.prices[sym].price.toFixed(Live.prices[sym].price < 1 ? 4 : 2));
    update();
  });
  $$("tt-unit").addEventListener("change", (e) => { tradeState.unit = e.target.value; $$("tt-qty-label").textContent = tradeState.unit === "dollars" ? "Dollars" : (isCryptoSym(sym) ? "Coins" : "Shares"); update(); });
  ["tt-qty", "tt-limit"].forEach((id) => $$(id).addEventListener("input", update));
  $$("tt-copy").addEventListener("click", async () => {
    const q = shares(), txt = `${tradeState.side.toUpperCase()} ${+q.toFixed(6)} ${sym} ${tradeState.type === "limit" ? "LIMIT " + price() : "MARKET"}`;
    try { await navigator.clipboard.writeText(txt); $$("tt-status").textContent = "Copied: " + txt; } catch { $$("tt-status").textContent = txt; }
  });
  $$("tt-record").addEventListener("click", () => {
    const q = shares(), p = price();
    if (!(q > 0 && p > 0)) { $$("tt-status").textContent = "Enter an amount first."; return; }
    const box = $$("tt-confirm");
    box.hidden = false;
    box.innerHTML = `<p>Record <b>${tradeState.side} ${q.toLocaleString(undefined, { maximumFractionDigits: 6 })} ${esc(sym)}</b> at <b>${fmtMoney(p)}</b> in ${esc($$("tt-acct").value)}, dated today? Only once it has filled.</p>
      <div class="pc-buttons"><button type="button" id="tt-yes">Record it</button><button type="button" class="secondary" id="tt-no">Not yet</button></div>`;
    $$("tt-no").addEventListener("click", () => { box.hidden = true; });
    $$("tt-yes").addEventListener("click", async () => {
      try {
        await api("/api/transactions", { method: "POST", body: JSON.stringify({ symbol: sym, side: tradeState.side, quantity: +q.toFixed(8), price: p, fees: 0, date: localDate(), account: $$("tt-acct").value }) });
        box.hidden = true; $$("tt-status").textContent = "Recorded.";
        await loadHoldings(); renderPosition();
      } catch (err) { $$("tt-status").textContent = err.message; }
    });
  });
  paintSide(); update();
  $("#trade-modal").hidden = false;
  bindLive("trade", $("#trade-modal"));
  $$("tt-qty").focus();
}
function closeTrade() { $("#trade-modal").hidden = true; Live.drop("trade"); tradeState.update = null; }
$("#trade-close").addEventListener("click", closeTrade);
$("#trade-modal").addEventListener("click", (e) => { if (e.target.id === "trade-modal") closeTrade(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("#trade-modal").hidden) closeTrade(); });
Live.onTick((t) => { if (tradeState.update && t.symbol === tradeState.sym) tradeState.update(); });
$("#sym-trade").addEventListener("click", () => symState.sym && openTradeTicket(symState.sym, "buy"));

// ---------------------------------------------------------------- early wire
const earlyState = { data: null, loadedAt: 0, filter: "", earlyOnly: false };
const KIND_LABEL = { social: "Social spike", wire: "Press release", filing: "SEC filing", crypto: "Trending coin", listing: "Listing", depeg: "Depeg" };
function earlyItem(m) {
  const s = esc(m.symbol);
  return `<li class="ew-item${m.yours ? " yours" : ""}" data-open="${s}">
    <div class="ew-top"><b class="ew-sym">${esc(m.symbol.replace(/-USD$/, ""))}</b>
      <span data-live="${s}" data-lf="price"></span><span class="small" data-live="${s}" data-lf="chg"></span>
      ${m.early ? `<span class="chip chip-early">Not in the mainstream yet</span>` : m.mainstream_24h != null ? `<span class="chip chip-muted">${m.mainstream_24h} mainstream today</span>` : ""}
      ${m.yours ? `<span class="chip chip-ok">Yours</span>` : ""}
      ${m.tone < 0 ? `<span class="chip chip-review">Bad news</span>` : ""}
      <span class="ew-bar" title="Signal strength ${m.strength}"><i style="width:${Math.min(100, m.strength)}%"></i></span></div>
    <div class="ew-kinds">${m.kinds.map((k) => `<span class="tag">${esc(KIND_LABEL[k] || k)}</span>`).join("")}${m.name ? `<span class="muted small">${esc(m.name)}</span>` : ""}</div>
    <ul class="ew-why">${m.signals.slice(0, 3).map((x) => `<li>${x.url ? `<a href="${esc(safeUrl(x.url))}" target="_blank" rel="noopener noreferrer">${esc(x.headline)}</a>` : esc(x.headline)} <span class="muted small">${esc(x.source)}${x.at ? ` · <span data-ago="${esc(x.at)}"></span>` : ""}</span></li>`).join("")}</ul></li>`;
}
async function loadEarly(force = false) {
  if (!earlyState.data) $("#ew-meta").textContent = "Checking social, wires, filings and crypto… (up to a minute the first time)";
  try {
    earlyState.data = await api("/api/early" + (force ? "?refresh=true" : ""));
    earlyState.loadedAt = Date.now();
    renderEarly();
    loadProof();
  } catch (err) { $("#ew-meta").textContent = err.message; }
}
const proofState = { horizon: 5 };
async function loadProof() {
  const body = $("#ew-proof-body");
  try {
    const d = await api(`/api/early/scorecard?horizon=${proofState.horizon}`);
    const a = d.all, e = d.early;
    if (!a.count) {
      body.innerHTML = `No sighting is ${d.horizon} closes old yet${d.pending ? ` (${d.pending} waiting)` : ""}. The first scores appear ${d.horizon} trading days after the wire starts logging.`;
      return;
    }
    const line = (s) => `median <b class="${s.median_excess_pct >= 0 ? "up" : "down"}">${fmtPct(s.median_excess_pct, 2)}</b> vs SPY · beat SPY ${Math.round(s.hit_rate * 100)}% of the time · ${s.count} scored`;
    const kinds = Object.entries(d.by_kind).map(([k, s]) => `<span class="chip">${esc(k)}: ${fmtPct(s.median_excess_pct, 1)} (${s.count})</span>`).join(" ");
    const verdict = a.count < 150 ? `Too few to judge: ${a.count} of the 150 needed before this number means anything.`
      : a.median_excess_pct > 0 && a.hit_rate > 0.55 ? "The wire is beating the market so far on this horizon." : "No edge on this horizon yet.";
    body.innerHTML = `<div>All sightings, ${d.horizon} closes later: ${line(a)}</div>` +
      (e.count ? `<div>Marked early: ${line(e)}</div>` : "") +
      (kinds ? `<div class="ew-kinds">${kinds}</div>` : "") +
      `<div class="muted">${verdict}${d.pending ? ` ${d.pending} more waiting for their exit close.` : ""}</div>`;
  } catch (err) { body.textContent = err.message; }
}
document.querySelectorAll("#ew-horizon button").forEach((b) => b.addEventListener("click", () => {
  proofState.horizon = Number(b.dataset.h);
  document.querySelectorAll("#ew-horizon button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
  loadProof();
}));
function renderEarly() {
  const d = earlyState.data;
  $("#ew-meta").innerHTML = `Updated <span data-ago="${esc(d.generated_at)}"></span> · every 2 minutes${d.errors.length ? ` · ${d.errors.length} source${d.errors.length > 1 ? "s" : ""} down` : ""}`;
  const f = earlyState.filter;
  const rows = d.signals.filter((m) => (!f || (f === "yours" ? m.yours : f === "crypto" ? m.asset === "crypto" : m.asset !== "crypto")) && (!earlyState.earlyOnly || m.early));
  $("#ew-list").innerHTML = rows.map(earlyItem).join("") || `<li class="muted">Nothing matches right now.</li>`;
  document.querySelectorAll("#ew-list [data-open]").forEach((el) => el.addEventListener("click", (e) => { if (!e.target.closest("a")) openSymbol(el.dataset.open); }));
  const hist = d.history || [];
  $("#ew-history").innerHTML = hist.length ? `<thead><tr><th>Day</th><th>Symbol</th><th>Why</th><th class="num">Price then</th><th class="num">Now</th><th class="num">Since</th></tr></thead><tbody>` +
    hist.map((h) => { const s = esc(h.symbol); return `<tr class="clickable" data-open="${s}"><td>${esc(h.day)}</td><td><b>${esc(h.symbol.replace(/-USD$/, ""))}</b>${h.early ? ` <span class="chip chip-early">early</span>` : ""}</td>
      <td class="small clip">${esc(h.headline || "")}</td><td class="num">${fmtMoney(h.price)}</td><td class="num" data-live="${s}" data-lf="price">—</td>
      <td class="num" data-live="${s}" data-lf="since" data-p0="${h.price}">—</td></tr>`; }).join("") + "</tbody>"
    : `<tr><td class="muted">The first sightings are logged today; come back tomorrow to see how they did.</td></tr>`;
  $("#ew-history").querySelectorAll("[data-open]").forEach((el) => el.addEventListener("click", () => openSymbol(el.dataset.open)));
  bindLive("early", $("#tab-early"));
  paintAgo(); paintEarlyScore();
}
function paintEarlyScore() {
  const cells = [...document.querySelectorAll('#ew-history [data-lf="since"]')];
  const vals = cells.map((c) => parseFloat(c.textContent)).filter((v) => !isNaN(v));
  if (!vals.length) { $("#ew-score").textContent = ""; return; }
  const up = vals.filter((v) => v > 0).length, avg = vals.reduce((a, b) => a + b, 0) / vals.length;
  $("#ew-score").textContent = `${up} of ${vals.length} logged tickers are up since first seen; average ${avg >= 0 ? "+" : ""}${avg.toFixed(2)}%. A few days prove nothing; compare with the market over months.`;
}
document.querySelectorAll("#ew-filter button").forEach((b) => b.addEventListener("click", () => {
  earlyState.filter = b.dataset.f;
  document.querySelectorAll("#ew-filter button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
  if (earlyState.data) renderEarly();
}));
$("#ew-early").addEventListener("change", (e) => { earlyState.earlyOnly = e.target.checked; if (earlyState.data) renderEarly(); });
let earlyScoreTimer = null;
Live.onTick(() => { if (currentTab() === "early" && !earlyScoreTimer) earlyScoreTimer = setTimeout(() => { earlyScoreTimer = null; paintEarlyScore(); }, 1000); });

// ---------------------------------------------------------------- people
const peopleState = { data: null, loadedAt: 0, copies: {} };
async function loadPeople(force = false) {
  if (!peopleState.data) $("#pp-meta").textContent = "Reading Congress disclosures, ARK's holdings and insider filings… (a minute the first time)";
  try {
    peopleState.data = await api("/api/people" + (force ? "?refresh=true" : ""));
    peopleState.loadedAt = Date.now();
    renderPeople();
  } catch (err) { $("#pp-meta").textContent = err.message; }
}
const actionChip = (a) => `<span class="act-chip ${/Buy|New/.test(a) ? "act-buy" : /Sell|Exit/.test(a) ? "act-sell" : "act-hold"}">${esc(a)}</span>`;
function personCell(m) {
  const f = peopleState.data.follows || {}, on = m.who in f;
  return `<span class="pp-who">${esc(m.who)}</span> <button type="button" class="chipbtn" data-follow="${esc(m.who)}" data-group="${esc(m.group)}" aria-pressed="${on}">${on ? "Following" : "Follow"}</button>
    <button type="button" class="ghost small" data-copy="${esc(m.who)}">If you'd copied</button><div class="pp-copy small" data-copyout="${esc(m.who)}"></div>`;
}
function symCell(m) {
  if (!m.symbol) return `<span class="muted">—</span>`;
  const s = esc(m.symbol);
  return `<button type="button" class="linkish pp-sym" data-open="${s}">${s}</button> <span class="small" data-live="${s}" data-lf="price"></span> <span class="small" data-live="${s}" data-lf="chg"></span>`;
}
function renderPeople() {
  const d = peopleState.data, sec = d.sections || {};
  $("#pp-meta").innerHTML = `Updated ${esc(d.as_of)} · refreshed every 30 minutes${d.errors.length ? ` · ${d.errors.length} source note${d.errors.length > 1 ? "s" : ""}` : ""}`;
  const follows = Object.keys(d.follows || {});
  $("#pp-follows").innerHTML = follows.map((w) => `<button type="button" class="chipbtn" aria-pressed="true" data-unfollow="${esc(w)}">${esc(w)} ✕</button>`).join("") || `<span class="muted small">Nobody yet: use Follow on anyone below.</span>`;
  $("#pp-following").innerHTML = (d.following || []).slice(0, 20).map((m) => `<li><div>${actionChip(m.action)} <b>${esc(m.symbol || "")}</b> ${esc(m.who)} <span class="muted small">${esc(m.amount || "")}</span></div>
      <div class="muted small">${esc(m.detail)} · disclosed ${esc(m.disclosed)}${m.url ? ` · <a href="${esc(safeUrl(m.url))}" target="_blank" rel="noopener noreferrer">filing ↗</a>` : ""}</div></li>`).join("")
    || (follows.length ? `<li class="muted">No new moves by the people you follow.</li>` : "");
  const table = (rows, cols) => rows.length ? `<thead><tr>${cols.map((c) => `<th${c.num ? ' class="num"' : ""}>${c.h}</th>`).join("")}</tr></thead><tbody>` +
    rows.map((m) => `<tr>${cols.map((c) => `<td${c.num ? ' class="num"' : ""}>${c.f(m)}</td>`).join("")}</tr>`).join("") + "</tbody>" : `<tr><td class="muted">Nothing in this window.</td></tr>`;
  const link = (m) => m.url ? `<a href="${esc(safeUrl(m.url))}" target="_blank" rel="noopener noreferrer">↗</a>` : "";
  $("#pp-congress").innerHTML = table((sec.congress || []).slice(0, 80), [
    { h: "Disclosed", f: (m) => `${esc(m.disclosed)}${m.lag_days != null ? `<div class="muted small">${m.lag_days} days late</div>` : ""}` },
    { h: "Who", f: personCell }, { h: "Move", f: (m) => actionChip(m.action) }, { h: "Symbol", f: symCell },
    { h: "Amount", f: (m) => esc(m.amount || "") }, { h: "Detail", f: (m) => `<span class="small">${esc(m.detail)}</span> ${link(m)}` }]);
  $("#pp-ark").innerHTML = table((sec.ark || []).slice(0, 60), [
    { h: "Day", f: (m) => esc(m.disclosed) }, { h: "Fund", f: (m) => personCell(m) }, { h: "Move", f: (m) => actionChip(m.action) },
    { h: "Symbol", f: symCell }, { h: "Detail", f: (m) => `<span class="small">${esc(m.detail)}</span>` }]);
  $("#pp-ark-status").textContent = Object.entries(d.ark_status || {}).map(([f, st]) => `${f}: ${st}`).slice(0, 2).join(" · ");
  $("#pp-insider").innerHTML = table((sec.insider || []).slice(0, 40), [
    { h: "Filed", f: (m) => esc(m.disclosed) }, { h: "Who", f: personCell }, { h: "Symbol", f: symCell },
    { h: "Value", num: true, f: (m) => esc(m.amount) }, { h: "", f: link }]);
  $("#pp-activist").innerHTML = table((sec.activist || []).slice(0, 40), [
    { h: "Filed", f: (m) => esc(m.disclosed) }, { h: "Who", f: personCell }, { h: "Symbol", f: symCell },
    { h: "Filing", f: (m) => `<span class="small">${esc(m.detail)}</span> ${link(m)}` }]);
  document.querySelectorAll("#tab-people [data-open]").forEach((el) => el.addEventListener("click", () => openSymbol(el.dataset.open)));
  document.querySelectorAll("#tab-people [data-follow]").forEach((b) => b.addEventListener("click", async () => {
    const on = b.getAttribute("aria-pressed") === "true";
    try {
      d.follows = on ? await api("/api/people/follow?who=" + encodeURIComponent(b.dataset.follow), { method: "DELETE" })
        : await api("/api/people/follow", { method: "POST", body: JSON.stringify({ who: b.dataset.follow, group: b.dataset.group }) });
      renderPeople();
    } catch (err) { alert(err.message); }
  }));
  document.querySelectorAll("#tab-people [data-unfollow]").forEach((b) => b.addEventListener("click", async () => {
    d.follows = await api("/api/people/follow?who=" + encodeURIComponent(b.dataset.unfollow), { method: "DELETE" }); renderPeople();
  }));
  document.querySelectorAll("#tab-people [data-copy]").forEach((b) => b.addEventListener("click", () => loadCopy(b.dataset.copy)));
  Object.entries(peopleState.copies).forEach(([w, html]) => document.querySelectorAll(`[data-copyout="${CSS.escape(w)}"]`).forEach((el) => { el.innerHTML = html; }));
  bindLive("people", $("#tab-people"));
}
async function loadCopy(who) {
  const out = () => document.querySelectorAll(`[data-copyout="${CSS.escape(who)}"]`);
  out().forEach((el) => { el.textContent = "Simulating…"; });
  try {
    const c = await api("/api/people/copy?who=" + encodeURIComponent(who));
    const html = c.trades ? `Copying ${c.trades} disclosed move${c.trades > 1 ? "s" : ""} since ${esc(c.since)}: <b class="${cls(c.return_pct)}">${fmtPct(c.return_pct, 1)}</b> vs ${esc(c.benchmark)} <b class="${cls(c.benchmark_return_pct)}">${fmtPct(c.benchmark_return_pct, 1)}</b>`
      : "No priced moves to copy yet.";
    peopleState.copies[who] = html;
    out().forEach((el) => { el.innerHTML = html; });
  } catch (err) { out().forEach((el) => { el.textContent = err.message; }); }
}


// ---------------------------------------------------------------- hold plan
const holdState = { data: null, loadedAt: 0 };
const VERDICT_CLASS = { "Sell?": "v-sell", Trim: "v-trim", Review: "v-review", Hold: "v-hold" };
async function loadHold(force = false) {
  if (!holdState.data) $("#hp-meta").textContent = "Checking your holdings, their filings and your tax lots…";
  try {
    holdState.data = await api("/api/holdplan" + (force ? "?refresh=true" : ""));
    holdState.loadedAt = Date.now();
    renderHold();
  } catch (err) { $("#hp-meta").textContent = err.message; }
}
function holdRow(h) {
  const s = esc(h.symbol), t = h.thesis;
  const trig = h.triggers.filter((x) => !(x.kind === "thesis" && x.level === "info"));
  const e = h.earnings;
  return `<div class="card hp-row ${VERDICT_CLASS[h.verdict]}">
    <div class="pc-head"><span class="act">${esc(h.verdict)}</span>
      <button type="button" class="linkish pc-sym" data-open="${s}">${esc(h.symbol.replace(/-USD$/, ""))}</button>
      <span class="pc-price" data-live="${s}" data-lf="price">${fmtMoney(h.price)}</span><span class="small" data-live="${s}" data-lf="chg"></span>
      <span class="muted small pc-w"><b data-live="${s}" data-lf="value" data-qty="${h.quantity}">${fmtMoney(h.value, 0)}</b> · ${(h.weight * 100).toFixed(1)}% of portfolio${h.cost_pct != null ? ` · <span class="${cls(h.cost_pct)}">${fmtPct(h.cost_pct, 0)}</span> from cost` : ""}</span></div>
    ${trig.length ? `<ul class="pc-why">${trig.map((x) => `<li class="t-${esc(x.level)}">${esc(x.text)}</li>`).join("")}</ul>` : `<p class="muted small">Nothing says otherwise: holding is the plan.</p>`}
    ${h.fundamentals ? `<p class="muted small">${esc(h.fundamentals.line)}</p>` : ""}
    ${e ? `<p class="small">Reports ${esc(e.date)}${e.estimated ? " (estimated)" : ""}${e.move_pct != null ? `: options price about ±${e.move_pct.toFixed(1)}%, <b>±${fmtMoney(e.move_dollars, 0)}</b> on yours` : ""}</p>` : ""}
    <div class="hp-thesis small">${t && (t.thesis || t.wrong_if) ? `<b>Why you own it:</b> ${esc(t.thesis || "—")}${t.wrong_if ? ` · <b>Wrong if:</b> ${esc(t.wrong_if)}` : ""}` : `<span class="muted">No reason written down yet.</span>`}
      <button type="button" class="linkish" data-thesis="${s}">${t ? "Edit" : "Write it down"}</button></div>
  </div>`;
}
function renderHold() {
  const d = holdState.data;
  $("#hp-meta").innerHTML = `Updated ${esc(d.as_of)} · cap ${(d.cap * 100).toFixed(0)}%${d.errors && d.errors.length ? ` · ${d.errors.length} source note${d.errors.length > 1 ? "s" : ""}` : ""}`;
  $("#hp-counts").innerHTML = Object.entries(d.counts).filter(([, n]) => n).map(([v, n]) => `<span class="chip ${VERDICT_CLASS[v]}">${n} ${esc(v)}</span>`).join("")
    || `<span class="muted small">No holdings yet: import your accounts on the Portfolio tab.</span>`;
  $("#hp-cap").value = Math.round(d.rules.cap * 100); $("#hp-st").value = Math.round(d.rules.short_term_rate * 100); $("#hp-lt").value = Math.round(d.rules.long_term_rate * 100);
  $("#hp-list").innerHTML = d.holdings.map(holdRow).join("");
  $("#hp-freed").textContent = d.freed ? `${fmtMoney(d.freed, 0)} to place (trims + buying power)` : "";
  $("#hp-reinvest").innerHTML = d.reinvest.map((q) => `<li><button type="button" class="linkish" data-open="${esc(q.symbol)}"><b>${esc(q.symbol)}</b></button>
      ${q.amount ? `<b>${fmtMoney(q.amount, 0)}</b> ` : ""}<span class="muted small">${esc(q.why)}</span></li>`).join("");
  const ev = d.events || { earnings: [], macro: [] };
  $("#hp-events").innerHTML = [...ev.earnings.map((e) => `<li><b>${esc(e.date)}</b> <button type="button" class="linkish" data-open="${esc(e.symbol)}">${esc(e.symbol)}</button> earnings${e.estimated ? " (estimated)" : ""}
      ${e.move_pct != null ? `· options ±${e.move_pct.toFixed(1)}% ≈ <b>±${fmtMoney(e.move_dollars, 0)}</b> on yours <span class="muted small">(to ${esc(e.expiry)})</span>` : ""}</li>`),
    ...ev.macro.slice(0, 12).map((m) => `<li><b>${esc(m.date)}</b> ${esc(m.kind)} <span class="muted small">${esc(m.name)}${m.consensus ? ` · forecast ${esc(m.consensus)}` : ""}</span></li>`)].join("")
    || `<li class="muted">No earnings for your stocks in the next 100 days, and no big releases in two weeks.</li>`;
  renderTax(d.tax);
  document.querySelectorAll("#tab-hold [data-open]").forEach((el) => el.addEventListener("click", () => openSymbol(el.dataset.open)));
  document.querySelectorAll("#tab-hold [data-thesis]").forEach((el) => el.addEventListener("click", () => openThesis(el.dataset.thesis)));
  bindLive("hold", $("#tab-hold"));
}
function renderTax(t) {
  const r = t.realized;
  $("#hp-tax-meta").textContent = `Rates used: ${(t.rates.short_term * 100).toFixed(0)}% short-term, ${(t.rates.long_term * 100).toFixed(0)}% long-term`;
  $("#hp-realized").innerHTML = `<div><span class="muted small">Short-term gains this year</span><b class="${cls(r.short_term)}">${fmtMoney(r.short_term, 0)}</b></div>
    <div><span class="muted small">Long-term gains this year</span><b class="${cls(r.long_term)}">${fmtMoney(r.long_term, 0)}</b></div>
    <div><span class="muted small">Losses disallowed by wash sales</span><b>${fmtMoney(r.wash_disallowed, 0)}</b></div>`;
  const parts = [];
  if (t.blackout.length) parts.push(`<h3>Don't buy yet</h3><ul class="hp-lines">${t.blackout.map((b) => `<li><b>${b.avoid.map(esc).join(" / ")}</b> until <b>${esc(b.until)}</b>
    <span class="muted small">sold at a ${fmtMoney(b.loss, 0)} loss on ${esc(b.sold)}; buying back sooner, in any account, cancels the deduction</span></li>`).join("")}</ul>`);
  if (t.wash_sales.length) parts.push(`<h3>Wash sales found</h3><ul class="hp-lines">${t.wash_sales.map((w) => `<li><b>${esc(w.symbol)}</b> sold ${esc(w.sold)} at a ${fmtMoney(w.loss, 0)} loss
    <span class="muted small">${esc(w.note)} (about ${fmtMoney(w.disallowed, 0)} disallowed)</span></li>`).join("")}</ul>`);
  if (t.clock.length) parts.push(`<h3>Worth waiting for long-term</h3><ul class="hp-lines">${t.clock.map((c) => `<li><b>${esc(c.symbol)}</b> ${fmtShares(c.quantity)} bought ${esc(c.bought)}${c.account ? ` in ${esc(c.account)}` : ""}
    turn long-term <b>${esc(c.long_term_on)}</b> (${c.days} days) <span class="muted small">selling after that saves about ${fmtMoney(c.saving, 0)} on a ${fmtMoney(c.gain, 0)} gain</span></li>`).join("")}</ul>`);
  if (t.harvest.length) parts.push(`<h3>Losses worth harvesting</h3><ul class="hp-lines">${t.harvest.map((h) => `<li><b>${esc(h.symbol)}</b>${h.account ? ` <span class="muted small">${esc(h.account)}</span>` : ""}
    down ${fmtMoney(h.loss, 0)} (${h.loss_pct.toFixed(0)}%): selling saves about <b>${fmtMoney(h.tax_saved, 0)}</b>; hold <b>${esc(h.replacement)}</b> instead <span class="muted small">(${esc(h.replacement_why)})</span>
    ${h.blocked_by.length ? `<div class="down small">You bought ${h.blocked_by.map((b) => `${esc(b.symbol)} on ${esc(b.date)}${b.account ? " in " + esc(b.account) : ""}`).join(", ")}: selling now would wash part of this loss.</div>` : ""}
    <div class="muted small">${esc(h.note)}</div></li>`).join("")}</ul>`);
  $("#hp-tax").innerHTML = parts.join("") || `<p class="muted small">No wash sales, no don't-buy windows, nothing turning long-term within 90 days and no losses worth harvesting.</p>`;
}
$("#hp-settings").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    await api("/api/holdplan/settings", { method: "POST", body: JSON.stringify({ cap: +$("#hp-cap").value / 100, st_rate: +$("#hp-st").value / 100, lt_rate: +$("#hp-lt").value / 100 }) });
    $("#hp-settings-msg").textContent = "Saved"; loadHold(true);
  } catch (err) { $("#hp-settings-msg").textContent = err.message; }
});

// ---------------------------------------------------------------- income
async function loadIncome(force = false) {
  $("#in-meta").textContent = "Reading dividend histories…";
  let d;
  try { d = await api("/api/income" + (force ? "?refresh=true" : "")); } catch (err) { $("#in-meta").textContent = err.message; return; }
  $("#in-meta").textContent = `Updated ${d.as_of}${d.errors.length ? ` · ${d.errors.length} source note${d.errors.length > 1 ? "s" : ""}` : ""}`;
  $("#in-sums").innerHTML = `<div><span class="muted small">A year, at today's rates</span><b>${fmtMoney(d.annual_income, 0)}</b><span class="muted small">${fmtMoney(d.monthly_avg, 0)} a month</span></div>
    <div><span class="muted small">Yield on what you paid</span><b>${d.yield_on_cost != null ? d.yield_on_cost.toFixed(2) + "%" : "—"}</b><span class="muted small">portfolio yield now ${d.portfolio_yield != null ? d.portfolio_yield.toFixed(2) + "%" : "—"}</span></div>
    <div><span class="muted small">Received, last 12 months</span><b>${fmtMoney(d.received_12m, 0)}</b><span class="muted small">${fmtMoney(d.received_ytd, 0)} this year${d.estimated_share ? ` · ${Math.round(d.estimated_share * 100)}% estimated` : ""}</span></div>`;
  const max = Math.max(...d.months.map((m) => m.amount), 1);
  $("#in-months").innerHTML = d.months.map((m) => `<div class="in-month"><span class="muted small">${esc(new Date(m.month + "-15").toLocaleString(undefined, { month: "short" }))}</span>
    <span class="in-bar"><span style="width:${(m.amount / max * 100).toFixed(1)}%"></span></span><b>${fmtMoney(m.amount, 0)}</b></div>`).join("");
  $("#in-upcoming").innerHTML = d.upcoming.map((u) => `<li><b>${esc(u.ex_date)}</b> <button type="button" class="linkish" data-open="${esc(u.symbol)}">${esc(u.symbol)}</button>
    about <b>${fmtMoney(u.amount, 2)}</b> <span class="muted small">(${fmtMoney(u.per_share, 4)} a share${u.pay_date ? `, paid ${esc(u.pay_date)}` : ""}${u.estimated ? ", date estimated from past spacing" : ", declared"})</span></li>`).join("")
    || `<li class="muted">None of your holdings pays a regular dividend.</li>`;
  $("#in-rows").innerHTML = d.holdings.map((h) => `<tr><td><button type="button" class="linkish" data-open="${esc(h.symbol)}">${esc(h.symbol)}</button></td>
    <td>${h.per_share != null ? `${fmtMoney(h.per_share, 4)} ×${h.per_year}` : "—"}</td><td><b>${fmtMoney(h.annual_income, 2)}</b></td>
    <td>${h.yield_on_cost != null ? h.yield_on_cost.toFixed(2) + "%" : "—"}</td><td>${h.current_yield != null ? h.current_yield.toFixed(2) + "%" : "—"}</td>
    <td>${fmtMoney(h.paid_12m, 2)}</td></tr>`).join("") || `<tr><td colspan="6" class="muted">No dividend payers among your holdings.</td></tr>`;
  const KIND = { dividend: "dividend", reinvested: "reinvested dividend", interest: "interest", tax_withheld: "tax withheld" };
  $("#in-received").innerHTML = d.received.map((r) => `<li><b>${esc(r.day)}</b> ${esc(r.symbol || "cash")} <b class="${cls(r.amount)}">${fmtMoney(r.amount, 2)}</b>
    <span class="muted small">${esc(KIND[r.kind] || r.kind)}${r.account ? " · " + esc(r.account) : ""}${r.estimated ? ` · estimated: ${fmtShares(r.shares)} × ${fmtMoney(r.per_share, 4)}` : ""}</span></li>`).join("")
    || `<li class="muted">Nothing yet. Import a Robinhood account activity CSV (Portfolio tab) to bring in your dividends.</li>`;
  document.querySelectorAll("#tab-income [data-open]").forEach((el) => el.addEventListener("click", () => openSymbol(el.dataset.open)));
}

$("#nm-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const out = $("#nm-out");
  out.innerHTML = `<li class="muted">Working it out…</li>`;
  try {
    const d = await api("/api/holdplan/newmoney?amount=" + encodeURIComponent(+$("#nm-amount").value));
    out.innerHTML = d.buys.map((b) => `<li><b>${fmtMoney(b.amount, 2)}</b> → <button type="button" class="linkish" data-open="${esc(b.symbol)}"><b>${esc(b.symbol)}</b></button>
        ${b.shares != null ? `<span class="muted small">≈ ${fmtShares(b.shares)} sh</span>` : ""}
        <span class="muted small">${esc(b.why)}${b.weight_now != null ? ` · ${(b.weight_now * 100).toFixed(1)}% → ${(b.weight_after * 100).toFixed(1)}%` : ""}</span></li>`).join("")
      + d.skipped.map((x) => `<li class="muted small">Not ${esc(x.symbol)}: ${esc(x.why)}</li>`).join("")
      + `<li class="muted small">${esc(d.note)}${d.has_targets ? "" : " No targets set yet, so this keeps your current mix: set one in any holding's \"why you own it\"."}</li>`;
    out.querySelectorAll("[data-open]").forEach((el) => el.addEventListener("click", () => openSymbol(el.dataset.open)));
  } catch (err) { out.innerHTML = `<li class="muted">${esc(err.message)}</li>`; }
});

// the "why you own it" form
let thesisSym = null;
async function openThesis(sym) {
  thesisSym = sym;
  $("#th-title").textContent = `Why you own ${sym.replace(/-USD$/, "")}`;
  $("#th-msg").textContent = "";
  let t = {};
  try { t = (await api("/api/thesis"))[sym] || {}; } catch { /* new */ }
  $("#th-thesis").value = t.thesis || ""; $("#th-wrong").value = t.wrong_if || "";
  $("#th-below").value = t.price_below ?? ""; $("#th-above").value = t.price_above ?? ""; $("#th-loss").value = t.max_loss_pct ?? "";
  $("#th-target").value = t.target_weight != null ? +(t.target_weight * 100).toFixed(2) : ""; $("#th-review").value = t.review_on || "";
  $("#th-rev").value = t.rev_growth_min ?? ""; $("#th-revq").value = t.rev_growth_quarters ?? "";
  $("#th-om").value = t.op_margin_min ?? ""; $("#th-dil").value = t.dilution_max ?? ""; $("#th-fcf").checked = !!t.fcf_positive;
  $("#th-delete").hidden = !t.symbol;
  $("#th-fund").textContent = "";
  $("#thesis-modal").hidden = false; $("#th-thesis").focus();
  api("/api/fundamentals/" + encodeURIComponent(sym)).then((f) => {
    if (thesisSym !== sym) return;
    $("#th-fund").textContent = f.line || f.note || "";
  }).catch(() => {});
}
const closeThesis = () => { $("#thesis-modal").hidden = true; };
$("#th-close").addEventListener("click", closeThesis);
$("#thesis-modal").addEventListener("click", (e) => { if (e.target.id === "thesis-modal") closeThesis(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("#thesis-modal").hidden) closeThesis(); });
const numOrNull = (v) => v === "" ? null : +v;
$("#th-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const target = numOrNull($("#th-target").value);
  const body = { thesis: $("#th-thesis").value.trim(), wrong_if: $("#th-wrong").value.trim(), price_below: numOrNull($("#th-below").value),
    price_above: numOrNull($("#th-above").value), max_loss_pct: numOrNull($("#th-loss").value), review_on: $("#th-review").value || null,
    target_weight: target ? target / 100 : null, rev_growth_min: numOrNull($("#th-rev").value),
    rev_growth_quarters: numOrNull($("#th-revq").value), op_margin_min: numOrNull($("#th-om").value),
    dilution_max: numOrNull($("#th-dil").value), fcf_positive: $("#th-fcf").checked };
  try { await api("/api/thesis/" + encodeURIComponent(thesisSym), { method: "POST", body: JSON.stringify(body) }); closeThesis(); loadHold(true); }
  catch (err) { $("#th-msg").textContent = err.message; }
});
$("#th-delete").addEventListener("click", async () => {
  try { await api("/api/thesis/" + encodeURIComponent(thesisSym), { method: "DELETE" }); closeThesis(); loadHold(true); }
  catch (err) { $("#th-msg").textContent = err.message; }
});

// ---------------------------------------------------------------- morning brief (Home)
const briefState = { data: null, loadedAt: 0, all: false };
async function loadBrief() {
  if (!holdingsList.length) { $("#home-brief").hidden = true; return; }
  try { briefState.data = await api("/api/brief"); briefState.loadedAt = Date.now(); renderBrief(); } catch { /* offline: leave hidden */ }
}
function renderBrief() {
  const b = briefState.data;
  $("#home-brief").hidden = false;
  $("#hb-title").textContent = b.title.replace(/^Morning brief: /, "Today: ").replace(/^./, (c) => c.toUpperCase());
  $("#hb-when").innerHTML = `built <span data-ago="${esc(b.generated_at)}"></span>`;
  const lines = briefState.all ? b.lines : b.lines.slice(0, 5);
  $("#hb-list").innerHTML = lines.map((ln) => `<li class="lvl${ln.level}"><span class="hb-sec">${esc(ln.section)}</span>
    ${ln.symbol && !ln.symbol.includes(",") ? `<button type="button" class="linkish" data-open="${esc(ln.symbol)}">${esc(ln.text)}</button>` : esc(ln.text)}</li>`).join("");
  $("#hb-more").hidden = b.lines.length <= 5;
  $("#hb-more").textContent = briefState.all ? "Show less" : `Show all ${b.lines.length}`;
  document.querySelectorAll("#hb-list [data-open]").forEach((el) => el.addEventListener("click", () => openSymbol(el.dataset.open)));
  paintAgo();
}
$("#hb-more").addEventListener("click", () => { briefState.all = !briefState.all; if (briefState.data) renderBrief(); });

// ---------------------------------------------------------------- stock pickers (People)
const pickState = { data: null, loadedAt: 0 };
async function loadPickers(force = false) {
  try { pickState.data = await api("/api/pickers" + (force ? "?refresh=true" : "")); pickState.loadedAt = Date.now(); renderPickers(); }
  catch (err) { $("#pk-meta").textContent = err.message; }
}
function renderPickers() {
  const d = pickState.data;
  $("#pk-meta").textContent = d.following.length ? `${d.following.length} followed · graded ${d.horizon} trading days after each call` : "";
  $("#pk-suggested").innerHTML = d.suggested.length ? `<span class="muted small">Suggested:</span> ` + d.suggested.slice(0, 12).map((u) =>
    `<button type="button" class="chip" data-pick="${esc(u.username)}">${esc(u.username)} <span class="muted">${(u.followers / 1000).toFixed(0)}k</span></button>`).join("") : "";
  $("#pk-list").innerHTML = d.following.map((g) => {
    const calls = g.calls.slice(0, 8);
    return `<div class="pk-card"><div class="pc-head"><b>${esc(g.username)}</b><span class="muted small">${g.profile && g.profile.followers ? (g.profile.followers).toLocaleString() + " followers · " : ""}${g.bullish} bullish · ${g.bearish} bearish calls</span>
      <button type="button" class="ghost small" data-unpick="${esc(g.username)}">Unfollow</button></div>
      <p>${g.scored ? `Beat SPY on <b>${Math.round(g.hit_rate * 100)}%</b> of ${g.scored} scored calls · median <b class="${cls(g.median_excess_pct)}">${fmtPct(g.median_excess_pct, 2)}</b> vs SPY`
        : "No calls old enough to score yet"}${g.pending ? ` <span class="muted small">· ${g.pending} waiting</span>` : ""}${g.scored && g.scored < 30 ? ` <span class="muted small">· too few to judge</span>` : ""}</p>
      ${calls.length ? `<div class="scroll"><table class="data"><thead><tr><th>Called</th><th>Ticker</th><th>Side</th><th class="num">Price then</th><th class="num">Since</th><th class="num">5-day vs SPY</th></tr></thead><tbody>
      ${calls.map((c) => `<tr><td class="nowrap">${esc(c.day)}</td><td><button type="button" class="linkish" data-open="${esc(c.symbol)}">${esc(c.symbol)}</button></td>
        <td class="${c.side === "bullish" ? "up" : "down"}">${esc(c.side)}</td><td class="num">${fmtMoney(c.price)}</td><td class="num ${cls(c.since_pct)}">${fmtPct(c.since_pct, 1)}</td>
        <td class="num">${c.excess_pct == null ? `<span class="muted">waiting</span>` : `<span class="${c.right ? "up" : "down"}">${fmtPct(c.excess_pct, 1)} ${c.right ? "✓" : "✗"}</span>`}</td></tr>`).join("")}</tbody></table></div>` : ""}
      ${g.errors.length ? `<p class="muted small">${esc(g.errors[0])}</p>` : ""}</div>`;
  }).join("") || `<p class="muted small">Follow someone to start grading their calls.</p>`;
  document.querySelectorAll("#pk-suggested [data-pick]").forEach((b) => b.addEventListener("click", () => followPicker(b.dataset.pick)));
  document.querySelectorAll("#pk-list [data-unpick]").forEach((b) => b.addEventListener("click", async () => {
    await api("/api/pickers/follow?username=" + encodeURIComponent(b.dataset.unpick), { method: "DELETE" }); loadPickers(true);
  }));
  document.querySelectorAll("#pk-list [data-open]").forEach((el) => el.addEventListener("click", () => openSymbol(el.dataset.open)));
}
async function followPicker(user) {
  $("#pk-msg").textContent = `Grading ${user}…`;
  try { await api("/api/pickers/follow", { method: "POST", body: JSON.stringify({ username: user }) }); $("#pk-msg").textContent = ""; $("#pk-user").value = ""; loadPickers(true); }
  catch (err) { $("#pk-msg").textContent = err.message; }
}
$("#pk-form").addEventListener("submit", (e) => { e.preventDefault(); const u = $("#pk-user").value.trim().replace(/^@/, ""); if (u) followPicker(u); });

// ---------------------------------------------------------------- crypto radar (Radar)
async function loadCryptoRadar() {
  try {
    const d = await api("/api/cryptoradar");
    $("#cr-card").hidden = !d.coins.length;
    if (!d.coins.length) return;
    $("#cr-meta").innerHTML = `${d.coins.map(esc).join(", ")} · checked <span data-ago="${esc(d.generated_at)}"></span>${d.errors.length ? ` · ${d.errors.length} source errors` : ""}`;
    const names = { 3: "Act today", 2: "Serious", 1: "Read it" };
    $("#cr-list").innerHTML = d.alerts.map((a) => `<li class="rr lvl${a.level}"><span class="rr-level">${names[a.level]}</span>
      <div><b>${esc(a.text)}</b><div class="small">${a.date ? esc(a.date) + " · " : ""}${esc(a.kind)}${a.coins && a.coins.length ? " · " + a.coins.map(esc).join(", ") : ""}
      ${a.url ? ` · <a href="${esc(safeUrl(a.url))}" target="_blank" rel="noopener noreferrer">Source ↗</a>` : ""}</div></div></li>`).join("")
      || `<li class="muted">Nothing on your coins: no hacks on their chains, no Coinbase incidents, no depegs.</li>`;
    paintAgo();
  } catch (err) { $("#cr-meta").textContent = err.message; }
}

// ---------------------------------------------------------------- start
api("/api/session").then((s) => { $("#signout").hidden = !s.auth; }).catch(() => {});
loadHoldings().then(() => { if (!location.hash || location.hash.length < 2) loadHome(); });
loadHeadsup();
if (location.hash.length > 1) openSymbol(decodeURIComponent(location.hash.slice(1)));
