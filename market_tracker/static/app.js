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
async function loadMarketNews() {
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
      tile("Portfolio value", fmtMoney(p.total_value, 0), `cost ${fmtMoney(p.total_cost, 0)}`),
      tile("Unrealized P&L", `<span class="${cls(p.unrealized_pnl)}">${fmtMoney(p.unrealized_pnl, 0)}</span>`, fmtPct(p.unrealized_pct)),
      tile("Today", `<span class="${cls(p.day_change_value)}">${fmtMoney(p.day_change_value, 0)}</span>`, "crypto: 24h"),
      tile("Realized P&L", fmtMoney(p.realized_pnl, 0), ""),
    ].join("");
    $("#pf-table").innerHTML = p.positions.length ? `<thead><tr><th>Symbol</th><th class="num">Qty</th><th class="num">Avg cost</th><th class="num">Price</th><th class="num">P&L</th><th class="num">Weight</th></tr></thead><tbody>` +
      p.positions.map((x) => `<tr class="clickable" data-open="${esc(x.symbol)}"><td><b>${esc(x.symbol)}</b></td><td class="num">${x.quantity.toLocaleString()}</td><td class="num">${fmtMoney(x.avg_cost)}</td><td class="num">${fmtMoney(x.price)}</td>
        <td class="num ${cls(x.unrealized_pnl)}">${fmtMoney(x.unrealized_pnl, 0)} <span class="small">${fmtPct(x.unrealized_pct)}</span></td><td class="num">${x.weight != null ? x.weight.toFixed(1) + "%" : "—"}</td></tr>`).join("") + "</tbody>"
      : "<tr><td class='muted'>No positions yet — record a trade.</td></tr>";
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
  const body = { symbol: f.get("symbol"), side: f.get("side"), quantity: +f.get("quantity"), price: +f.get("price"), fees: +(f.get("fees") || 0), date: f.get("date") };
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
  apply(t) {
    const prev = this.prices[t.symbol];
    this.prices[t.symbol] = t;
    this.listeners.forEach((fn) => fn(t, prev));
  },
};
function setLiveState(state) {
  const dot = $("#live-dot");
  if (dot) { dot.classList.toggle("on", state === "on"); dot.title = { on: "Live", off: "Reconnecting…", connecting: "Connecting…" }[state]; }
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
  try {
    const d = await api(`/api/intraday/${encodeURIComponent(sym)}?range=${range}`);
    if (sym !== symState.sym || range !== symState.range) return;
    Object.assign(symState, { points: d.points, reference: d.reference, refLabel: d.reference_label });
    drawChart(true);
  } catch (err) { $("#sym-chart").innerHTML = `<p class="muted small">Chart unavailable: ${esc(err.message)}</p>`; }
}
document.querySelectorAll("#tab-symbol .range button").forEach((b) => b.addEventListener("click", () => {
  symState.range = b.dataset.range;
  document.querySelectorAll("#tab-symbol .range button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
  loadChart();
}));

function paintSymbol(t, prev) {
  const el = $("#sym-price");
  el.textContent = fmtMoney(t.price);
  flash(el, t, prev);
  // The day's change for the 1D view (vs previous close; crypto: 24h); for longer ranges, vs the range start.
  let chg = t.change_pct, label = isCryptoSym(t.symbol) ? "past 24 hours" : "today";
  if (symState.range !== "1d" && symState.reference) { chg = (t.price / symState.reference - 1) * 100; label = "since " + symState.refLabel; }
  const abs = symState.range !== "1d" && symState.reference ? t.price - symState.reference
    : chg != null ? t.price - t.price / (1 + chg / 100) : null;
  const c = $("#sym-change");
  c.textContent = `${abs != null ? (abs >= 0 ? "+" : "-") + fmtMoney(Math.abs(abs), 2) + " " : ""}(${fmtPct(chg, 2)}) ${label}`;
  c.className = "sym-change " + cls(chg);
  $("#sym-src").textContent = `${t.source} · ${new Date(t.ts).toLocaleTimeString()}`;
  if (symState.range === "1d" && symState.points.length) {
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
  const color = up ? "var(--good)" : "var(--bad)";
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
    showTip(e, `<b>${fmtMoney(best.p)}</b> <span class="${cls(best.p - ref)}">${fmtPct((best.p / ref - 1) * 100, 2)}</span><br>${esc(fmtT(best.t))}`);
  });
  svg.addEventListener("pointerleave", () => { cross.setAttribute("visibility", "hidden"); hideTip(); });
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

// ---------------------------------------------------------------- Robinhood import
let rhText = "";
$("#rh-preview").addEventListener("click", async () => {
  const file = $("#rh-file").files[0];
  if (!file) { $("#rh-result").innerHTML = `<p class="muted">Choose the CSV file first.</p>`; return; }
  rhText = await file.text();
  await runImport(false);
});
async function runImport(commit) {
  const out = $("#rh-result");
  out.innerHTML = `<p class="muted">${commit ? "Importing…" : "Reading…"}</p>`;
  try {
    const r = await api("/api/import/robinhood", { method: "POST", body: JSON.stringify({ csv: rhText, commit }) });
    const skipped = Object.entries(r.skipped).map(([k, n]) => `${esc(k)} ×${n}`).join(", ");
    out.innerHTML = `<p>${commit ? `<b>Imported ${r.new} trades.</b>` : `<b>${r.new} new trades</b> to import`}${r.duplicates ? `, ${r.duplicates} already imported` : ""}.
      ${skipped ? `<br><span class="muted small">Skipped (not share trades): ${skipped}</span>` : ""}
      ${r.errors.length ? `<br><span class="muted small">${r.errors.map(esc).join("<br>")}</span>` : ""}</p>
      <p class="muted small">Positions after import: ${r.positions.map((p) => `${esc(p.symbol)} ${p.quantity.toLocaleString(undefined, { maximumFractionDigits: 4 })}`).join(", ") || "none"}</p>
      ${!commit && r.new ? `<button id="rh-commit" type="button">Import ${r.new} trades</button>` : ""}`;
    const btn = $("#rh-commit");
    if (btn) btn.addEventListener("click", () => runImport(true));
    if (commit) { loadPortfolio(); loadHoldings(); }
  } catch (err) { out.innerHTML = `<p class="muted">${esc(err.message)}</p>`; }
}

// ---------------------------------------------------------------- start
api("/api/session").then((s) => { $("#signout").hidden = !s.auth; }).catch(() => {});
loadHoldings();
if (location.hash.length > 1) openSymbol(decodeURIComponent(location.hash.slice(1)));
