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
  // X-Plumbline marks requests from the app's own pages (other sites can't add it).
  const res = await fetch(path, { headers: { "Content-Type": "application/json", "X-Plumbline": "1" }, ...opts });
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail || msg; } catch (_) { /* not JSON */ }
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return res.json();
}

// Company logos (served by this app from its own cache) and names, next to tickers everywhere.
const Names = {
  map: {}, pending: new Set(), timer: null,
  want(syms) {
    syms.forEach((s) => { if (s && !(s in this.map)) this.pending.add(s); });
    if (this.pending.size && !this.timer) this.timer = setTimeout(() => this.flush(), 60);
  },
  async flush() {
    const list = [...this.pending]; this.pending.clear(); this.timer = null;
    try { Object.assign(this.map, await api("/api/names?symbols=" + encodeURIComponent(list.join(",")))); } catch { /* names are a nicety */ }
    list.forEach((s) => { if (!(s in this.map)) this.map[s] = ""; });
    document.querySelectorAll("[data-name]").forEach((el) => { const n = this.map[el.dataset.name]; if (n && el.textContent !== n) el.textContent = n; });
  },
};
const logoImg = (sym, size = 28) => `<img class="logo" src="/api/logo/${encodeURIComponent(sym)}" width="${size}" height="${size}" alt="" loading="lazy" decoding="async">`;
const nameOf = (sym) => { Names.want([sym]); return `<span class="tk-name" data-name="${esc(sym)}">${esc(Names.map[sym] || "")}</span>`; };
const tick = (sym, size = 18) => `<span class="tk">${logoImg(sym, size)}<b>${esc(sym.replace(/-USD$/, ""))}</b></span>`;

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
// Five sections; Plan, Discover and News hold several pages, shown as a second row.
// ---------------------------------------------------------------- which pages you use (counted on this app only)
const usageState = { hidden: [], last: "" };
function recordUsage(page) {
  if (usageState.last === page) return;
  usageState.last = page;
  api("/api/usage", { method: "POST", body: JSON.stringify({ page }) }).catch(() => {});
}
api("/api/usage").then((u) => { usageState.hidden = u.hidden || []; }).catch(() => {});
const PAGE_NAMES = { home: "Home", decisions: "Decisions", ask: "Ask", hold: "Hold plan", income: "Income", plan: "Strategy", review: "Review", ideas: "What to buy", sleepers: "Sleepers",
  chatter: "Chatter", moneyflow: "Money flow", economy: "Economy", pulse: "Market pulse", early: "Early wire", people: "People", radar: "Filing radar",
  smart: "Smart money", analyze: "Analyze", research: "Research", dashboard: "Dashboard", journal: "Track record", mynews: "My news",
  reading: "Reading room", portfolio: "Portfolio", accounts: "Accounts", taxes: "Taxes" };
async function loadUsage() {
  let u;
  try { u = await api("/api/usage"); } catch { return; }
  usageState.hidden = u.hidden;
  $("#us-meta").textContent = u.since ? `counting since ${u.since}` : "";
  $("#us-text").textContent = u.text;
  $("#us-out").innerHTML = `<div class="table-scroll"><table class="data"><thead><tr><th>Page</th><th>Days used, last 30</th><th>Last opened</th><th></th></tr></thead><tbody>
    ${u.pages.map((p) => `<tr><td>${esc(PAGE_NAMES[p.page] || p.page)}${p.suggest_hide ? ` <span class="chip-warn">rarely used</span>` : ""}</td><td>${p.days_last_30}</td>
      <td class="muted">${esc(p.last || "never")}</td><td>${p.core ? `<span class="muted">always on</span>` : `<button type="button" class="secondary small" data-hide="${esc(p.page)}" data-on="${p.hidden ? 0 : 1}">${p.hidden ? "Show" : "Hide"}</button>`}</td></tr>`).join("")}
    </tbody></table></div>`;
  document.querySelectorAll("#us-out [data-hide]").forEach((b) => b.addEventListener("click", async () => {
    try { await api("/api/usage/hide", { method: "POST", body: JSON.stringify({ page: b.dataset.hide, hide: b.dataset.on === "1" }) }); loadUsage(); } catch { /* ignore */ }
  }));
}

const GROUP_PAGES = { home: ["home", "decisions", "ask"], plan: ["hold", "income", "plan", "review"], ideas: ["ideas", "sleepers", "chatter", "moneyflow", "economy"], discover: ["pulse", "early", "people", "radar", "smart", "analyze", "research", "dashboard", "journal"],
  news: ["mynews", "reading"], portfolio: ["portfolio", "accounts", "taxes"] };
const groupOf = (name) => Object.keys(GROUP_PAGES).find((g) => GROUP_PAGES[g].includes(name));
const lastPage = {};
document.querySelectorAll("#tabs button").forEach((btn) => btn.addEventListener("click", () => selectTab(lastPage[btn.dataset.group] || GROUP_PAGES[btn.dataset.group][0])));
document.querySelectorAll("#subtabs button").forEach((btn) => btn.addEventListener("click", () => selectTab(btn.dataset.tab)));
function selectTab(name) {
  const group = groupOf(name);
  if (group) {
    lastPage[group] = name;
    document.querySelectorAll("#tabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.group === group)));
    const subs = document.querySelectorAll("#subtabs button");
    subs.forEach((b) => { b.hidden = b.dataset.group !== group || (usageState.hidden.includes(b.dataset.tab) && b.dataset.tab !== name); b.setAttribute("aria-current", String(b.dataset.tab === name)); });
    $("#subtabs").hidden = GROUP_PAGES[group].length < 2;
    $("#subtabs").dataset.page = name;
    const cur = document.querySelector(`#subtabs button[data-tab="${name}"]`);
    if (cur && !cur.hidden) cur.scrollIntoView({ block: "nearest", inline: "nearest" });
  }
  document.querySelectorAll(".tab").forEach((s) => { s.hidden = s.id !== "tab-" + name; });
  recordUsage(name);
  if (name === "portfolio") { loadPortfolio(); loadSchedules(); }
  if (name === "accounts") { loadUsage(); loadSetup(); loadConnections(); loadTransfers(); loadCashAccounts(); loadBrokers(); loadOffsite(); loadHealth(); loadLive(); }
  if (name === "taxes") loadTaxes();
  if (name === "review") loadPerformance();
  if (name === "smart" && !loaded.smart) { loaded.smart = true; loadInvestors(); }
  if (name === "journal") { loadJournal(); loadAdvice(); }
  if (name === "pulse") loadPulse();
  if (name === "plan") loadPlan();
  if (name === "home") loadHome();
  if (name === "decisions") { loadDecisions(); loadLetter(); }
  if (name === "ask") setTimeout(() => $("#ask-q").focus(), 50);
  if (name === "early") loadEarly();
  if (name === "people") { loadPeople(); loadPickers(); }
  if (name === "hold") { loadHold(); loadTargets(); loadGoal(); }
  if (name === "income") loadIncome();
  if (name === "mynews") { loadNewsDesk(); loadMyNews(); loadHeadsup(true); }
  if (name === "reading") loadReading();
  if (name === "radar") { loadRadar(); loadCryptoRadar(); }
  if (name === "ideas") { loadScreen(); loadScreenBacktest(); loadIdeas(); loadEvents(); loadSignalBacktests(); loadPaper(); }
  if (name === "sleepers") { loadSleepers(); loadSignalBacktests(); }
  if (name === "chatter") loadChatter();
  if (name === "moneyflow") loadMoneyFlow();
  if (name === "economy") loadEconomy();
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
      ${logoImg(s, 16)}<span class="t-sym">${esc(s.replace(/-USD$/, ""))}</span><span class="t-price">—</span><span class="t-chg"></span></button>`).join("")
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
  $("#sym-name").innerHTML = `${logoImg(sym, 36)}<span>${esc(sym)}</span> ${nameOf(sym)}`;
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
  loadEarnings(sym);
  loadPricedIn(sym);
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
    ${Object.keys(h.by_account || {}).length > 1 || Object.keys(h.by_account || {})[0] ? `<tr><td>Where</td><td class="num">${Object.entries(h.by_account).map(([a, q]) =>
      `${esc(a || "Unlabeled")} ${q.toLocaleString(undefined, { maximumFractionDigits: 6 })}`).join(" · ")}</td></tr>` : ""}
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
  $(".imp-file").hidden = impSource === "holdings" || impSource === "snaptrade";
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
api("/api/sync/snaptrade").then((r) => {
  $("#st-setup").hidden = r.configured; $("#st-buttons").hidden = !r.configured;
  $("#st-last").textContent = r.last_sync ? `Last synced ${r.last_sync}` : "";
}).catch(() => {});
$("#st-connect").addEventListener("click", async () => {
  const tab = window.open("", "_blank");        // opened now, so pop-up blockers allow it
  try {
    const r = await api("/api/sync/snaptrade/connect", { method: "POST" });
    if (tab) tab.location = r.url; else location.href = r.url;
    $("#rh-result").innerHTML = `<p class="muted small">Sign in to your broker in the SnapTrade tab, then come back and press Sync now.</p>`;
  } catch (err) { if (tab) tab.close(); $("#rh-result").innerHTML = `<p class="muted">${esc(err.message)}</p>`; }
});
$("#st-sync").addEventListener("click", async () => {
  const out = $("#rh-result");
  out.innerHTML = `<p class="muted">Reading your connected accounts…</p>`;
  try {
    const r = await api("/api/sync/snaptrade", { method: "POST" });
    const skipped = Object.entries(r.skipped || {}).map(([k, n]) => `${esc(k)} ×${n}`).join(", ");
    out.innerHTML = r.note ? `<p class="muted">${esc(r.note)}</p>` :
      `<p><b>${r.new} new trade${r.new === 1 ? "" : "s"}</b>${r.duplicates ? `, ${r.duplicates} already there` : ""}${r.income_new ? `, ${r.income_new} dividend and interest payments` : ""}
        <span class="muted small">(${r.accounts.map((a) => `${esc(a.name)} ${a.new}`).join(", ")})</span></p>
      ${skipped ? `<p class="muted small">Not trades: ${skipped}</p>` : ""}` +
      (r.differences.length ? `<p class="small">Positions your ledger doesn't explain (transfers in, or trades older than the broker's history; add them with Stash / other):</p>
        <ul class="small">${r.differences.map((d) => `<li>${esc(d.account)} ${esc(d.symbol)}: broker ${d.broker} vs ledger ${d.ledger}</li>`).join("")}</ul>`
        : `<p class="small muted">Every position matches your broker.</p>`);
    $("#st-last").textContent = "Last synced today";
    loadPortfolio(); loadHoldings();
  } catch (err) { out.innerHTML = `<p class="muted">${esc(err.message)}</p>`; }
});
// ---------------------------------------------------------------- vs the market (Home)
async function loadBenchmark() {
  let d;
  try { d = await api("/api/benchmark"); } catch (err) { $("#bm-body").textContent = err.message; return; }
  const o = d.overall;
  if (!o) { $("#bm-body").innerHTML = `<p class="muted">Import your accounts to compare.</p>`; return; }
  const line = (name, r) => `<div class="bm-row"><span>${esc(name)}</span><span class="${cls(r.gain)}">${fmtMoney(r.gain, 0)}</span>
    <span class="muted">${esc(d.benchmark)} ${fmtMoney(r.bench_gain, 0)}</span><b class="${cls(r.ahead)}">${r.ahead >= 0 ? "ahead" : "behind"} ${fmtMoney(Math.abs(r.ahead), 0)}</b></div>`;
  $("#bm-meta").textContent = `since ${o.since}`;
  $("#bm-body").innerHTML = `<p>${o.ahead >= 0 ? "You're ahead of" : "You're behind"} putting the same money into ${esc(d.benchmark)} on the same days by
      <b class="${cls(o.ahead)}">${fmtMoney(Math.abs(o.ahead), 0)}</b>.</p>
    <div class="bm-rows"><div class="bm-row muted small"><span></span><span>You</span><span>Same money in ${esc(d.benchmark)}</span><span></span></div>
    ${Object.entries(d.accounts).map(([a, r]) => line(a, r)).join("")}${line("All", o)}</div>
    ${o.unpriced.length ? `<p class="muted small">No price for ${o.unpriced.map(esc).join(", ")}: counted as $0.</p>` : ""}`;
}

// ---------------------------------------------------------------- buy-the-dip list and goal (Hold plan)
async function loadTargets() {
  try {
    const rows = await api("/api/buy-targets");
    $("#bt-list").innerHTML = rows.map((r) => `<li>${tick(r.symbol)} at <b>${fmtMoney(r.price)}</b>
      <span class="small ${r.now != null && r.now <= r.price ? "up" : "muted"}">${r.now != null ? `now ${fmtMoney(r.now)} (${r.gap_pct > 0 ? r.gap_pct + "% above" : "at your price"})` : ""}</span>
      ${r.note ? `<span class="muted small">${esc(r.note)}</span>` : ""} <button type="button" class="ghost small" data-bt-del="${esc(r.symbol)}">Remove</button></li>`).join("")
      || `<li class="muted">Nothing yet. Add a stock you'd like to own at a lower price.</li>`;
    document.querySelectorAll("[data-bt-del]").forEach((b) => b.addEventListener("click", async () => { await api("/api/buy-targets/" + encodeURIComponent(b.dataset.btDel), { method: "DELETE" }); loadTargets(); }));
  } catch (err) { $("#bt-list").innerHTML = `<li class="muted">${esc(err.message)}</li>`; }
}
$("#bt-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try { await api("/api/buy-targets", { method: "POST", body: JSON.stringify({ symbol: $("#bt-sym").value.trim(), price: +$("#bt-price").value, note: $("#bt-note").value.trim() }) });
    $("#bt-sym").value = ""; $("#bt-price").value = ""; $("#bt-note").value = ""; loadTargets(); }
  catch (err) { alert(err.message); }
});
function paintGoal(d) {
  if (!d.goal) { $("#gl-out").innerHTML = `<p class="muted">Set a goal to see the range of outcomes.</p>`; $("#gl-year").value = new Date().getFullYear() + 20; return; }
  $("#gl-target").value = d.goal.target; $("#gl-year").value = d.goal.year; $("#gl-monthly").value = d.goal.monthly;
  const p = d.projection;
  $("#gl-meta").textContent = `${Math.round(p.chance * 100)}% chance`;
  $("#gl-out").innerHTML = `<p>From ${fmtMoney(d.now_value, 0)} today, adding ${fmtMoney(d.goal.monthly, 0)} a month for ${p.years} years:</p>
    <div class="tax-sums"><div><span class="muted small">Bad markets</span><b>${fmtMoney(p.bad, 0)}</b></div>
      <div><span class="muted small">Typical</span><b>${fmtMoney(p.typical, 0)}</b></div><div><span class="muted small">Good markets</span><b>${fmtMoney(p.good, 0)}</b></div></div>
    <p><b>${Math.round(p.chance * 100)}%</b> chance of reaching ${fmtMoney(p.target, 0)}.
      ${p.monthly_for_even_odds != null ? `About ${fmtMoney(p.monthly_for_even_odds, 0)} a month gives even odds in a typical market.` : ""}</p>
    <p class="muted small">Simulated with ${Math.round(p.assumptions.mean * 100)}% average growth and ${Math.round(p.assumptions.vol * 100)}% yearly swings, before inflation. A range, not a promise.</p>`;
}
async function loadGoal() { try { paintGoal(await api("/api/goal")); } catch (err) { $("#gl-out").textContent = err.message; } }
$("#gl-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try { paintGoal(await api("/api/goal", { method: "POST", body: JSON.stringify({ target: +$("#gl-target").value, year: +$("#gl-year").value, monthly: +$("#gl-monthly").value || 0 }) })); }
  catch (err) { $("#gl-out").textContent = err.message; }
});

// ---------------------------------------------------------------- schedules, statement check, look-through, fees (Portfolio)
const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
function paintScDays() {
  const monthly = $("#sc-every").value === "month";
  $("#sc-day").innerHTML = monthly ? Array.from({ length: 28 }, (_, i) => `<option value="${i + 1}">on day ${i + 1}</option>`).join("")
    : WEEKDAYS.slice(0, 5).map((w, i) => `<option value="${i}">on ${w}</option>`).join("");
}
$("#sc-every").addEventListener("change", paintScDays); paintScDays();
async function loadSchedules() {
  if (!$("#sc-start").value) $("#sc-start").value = localDate();
  try {
    const rows = await api("/api/schedules");
    $("#sc-list").innerHTML = rows.map((r) => `<li>${esc(r.account)}: <b>${fmtMoney(r.amount, 2)}</b> into ${tick(r.symbol)}
      ${r.every === "month" ? `every month on day ${r.day}` : `every ${r.every === "week" ? "" : "2 "}week${r.every === "week" ? "" : "s"} on ${WEEKDAYS[r.day]}`} since ${esc(r.start)}
      <button type="button" class="ghost small" data-sc-del="${r.id}">Remove</button></li>`).join("") || `<li class="muted">No schedules.</li>`;
    document.querySelectorAll("[data-sc-del]").forEach((b) => b.addEventListener("click", async () => { await api("/api/schedules/" + b.dataset.scDel, { method: "DELETE" }); loadSchedules(); }));
  } catch { /* optional */ }
}
$("#sc-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    const r = await api("/api/schedules", { method: "POST", body: JSON.stringify({ account: $("#sc-acct").value, symbol: $("#sc-sym").value.trim(), amount: +$("#sc-amt").value,
      every: $("#sc-every").value, day: +$("#sc-day").value, start: $("#sc-start").value }) });
    $("#sc-sym").value = ""; $("#sc-amt").value = "";
    loadSchedules(); if (r.recorded.length) { loadHoldings(); alert(`Recorded ${r.recorded.length} past buy(s) from this schedule.`); }
  } catch (err) { alert(err.message); }
});
$("#st-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = $("#st-file").files[0]; if (!f) return;
  $("#st-out").innerHTML = `<p class="muted">Reading the statement…</p>`;
  const b64 = await new Promise((res) => { const r = new FileReader(); r.onload = () => res(String(r.result).split(",")[1]); r.readAsDataURL(f); });
  try {
    const r = await api("/api/statement-check", { method: "POST", body: JSON.stringify({ account: $("#st-acct").value, pdf_base64: b64 }) });
    const li = (x, t) => `<li>${tick(x.symbol)} ${t}</li>`;
    $("#st-out").innerHTML = `<p>Read ${r.read} holdings from the statement. ${r.match.length} match.</p>
      ${r.differences.length ? `<p><b>Different</b> (fix with Stash / other):</p><ul class="hp-lines">${r.differences.map((x) => li(x, `statement ${x.statement} · ledger ${x.ledger}`)).join("")}</ul>` : ""}
      ${r.not_in_ledger.length ? `<p><b>On the statement, not in the ledger</b>:</p><ul class="hp-lines">${r.not_in_ledger.map((x) => li(x, `${x.statement}`)).join("")}</ul>` : ""}
      ${r.not_on_statement.length ? `<p><b>In the ledger, not found on the statement</b> (sold, or not read):</p><ul class="hp-lines">${r.not_on_statement.map((x) => li(x, `${x.ledger}`)).join("")}</ul>` : ""}
      ${!r.differences.length && !r.not_in_ledger.length && !r.not_on_statement.length ? `<p class="up">Everything matches.</p>` : ""}`;
  } catch (err) { $("#st-out").innerHTML = `<p class="down">${esc(err.message)}</p>`; }
});
$("#lt-load").addEventListener("click", async () => {
  $("#lt-out").innerHTML = `<p class="muted">Reading your funds' holdings reports…</p>`;
  try {
    const d = await api("/api/lookthrough");
    const cap = holdState.data ? holdState.data.cap : 0.2;
    $("#lt-out").innerHTML = `${d.funds.length ? `<p class="muted">${d.funds.map((f) => `${esc(f.symbol)}${f.source !== f.symbol ? ` (via ${esc(f.source)})` : ""} as of ${esc(f.period)}`).join(" · ")}</p>` : `<p class="muted">You don't hold any funds, so this is just your stocks.</p>`}
      <table class="data"><thead><tr><th>Company</th><th class="num">Your money</th><th class="num">Share</th><th>How</th></tr></thead><tbody>
      ${d.top.slice(0, 20).map((r) => `<tr class="${r.weight > cap ? "warn-row" : ""}"><td>${r.symbol.length <= 6 ? tick(r.symbol) : esc(r.name)}</td><td class="num">${fmtMoney(r.value, 0)}</td>
        <td class="num">${(r.weight * 100).toFixed(1)}%</td><td class="muted">${Object.entries(r.via).map(([k, v]) => `${esc(k)} ${fmtMoney(v, 0)}`).join(" + ")}</td></tr>`).join("")}</tbody></table>
      ${d.errors.length ? `<p class="muted small">${d.errors.map(esc).join("; ")}</p>` : ""}`;
  } catch (err) { $("#lt-out").innerHTML = `<p class="down">${esc(err.message)}</p>`; }
});
$("#fe-load").addEventListener("click", async () => {
  $("#fe-out").innerHTML = `<p class="muted">Looking up expense ratios…</p>`;
  try {
    const d = await api("/api/fees");
    $("#fe-out").innerHTML = d.funds.length ? `<p>Your funds cost about <b>${fmtMoney(d.per_year, 0)} a year</b>.</p><ul class="hp-lines">${d.funds.map((f) => `<li>${tick(f.symbol)}
        ${(f.expense_ratio * 100).toFixed(2)}% = <b>${fmtMoney(f.per_year, 2)}</b> a year <span class="muted small">(${fmtMoney(f.drag_30y, 0)} over 30 years at 7%)</span>
        ${f.cheaper.map((c) => `<div class="small">Same index: <b>${esc(c.symbol)}</b> at ${(c.expense_ratio * 100).toFixed(2)}% saves ${fmtMoney(c.saves_per_year, 2)} a year, about ${fmtMoney(c.saves_30y, 0)} over 30 years
          <span class="muted">(switching means selling: check the tax first)</span></div>`).join("")}</li>`).join("")}</ul>`
      : `<p class="muted">No funds with an expense ratio among your holdings.</p>`;
  } catch (err) { $("#fe-out").innerHTML = `<p class="down">${esc(err.message)}</p>`; }
});

// ---------------------------------------------------------------- connections and trading settings
function lastLine(l) {
  if (!l) return "not run yet";
  const when = new Date(l.at).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
  return l.ok ? `last checked ${when}${l.new ? `, ${l.new} new` : ""}` : `failed ${when}: ${l.error}`;
}
async function loadConnections() {
  let c;
  try { c = await api("/api/connections"); } catch { return; }
  document.querySelectorAll(".conn-state").forEach((el) => {
    const x = c[el.dataset.state];
    el.className = "conn-state " + (x && x.configured ? (x.last && !x.last.ok ? "down" : "up") : "muted");
    el.textContent = x && x.configured ? `● connected · ${lastLine(x.last)}` : "○ not connected";
  });
  if (c.email.user) $("#cx-mail").value = c.email.user;
  $("#cx-unread").innerHTML = (c.email.unread || []).slice(0, 8).map((u) => `<li class="muted">Couldn't read: ${esc(u.account)} · ${esc(u.subject)} (${esc(u.date)})</li>`).join("");
  if (c.robinhood_crypto.public_key) $("#cx-rh-pub").textContent = c.robinhood_crypto.public_key;
  try {
    const t = await api("/api/trade/log");
    $("#tr-on").checked = t.settings.enabled; $("#tr-max").value = t.settings.max_order; $("#tr-day").value = t.settings.daily_limit;
    $("#tr-log").innerHTML = t.orders.slice(0, 10).map((o) => `<li><b>${esc(o.at.slice(0, 16).replace("T", " "))}</b> ${esc(o.side)} ${esc(o.symbol)} ${fmtMoney(o.usd, 2)} on ${esc(o.venue)}
      <span class="${o.status === "placed" ? "up" : "down"} small">${esc(o.status)}</span></li>`).join("") || `<li class="muted">No orders sent from the app yet.</li>`;
  } catch { /* trading log is optional */ }
}
const connSubmit = (form, msg, path, body, done) => $(form).addEventListener("submit", async (e) => {
  e.preventDefault();
  $(msg).className = "small muted"; $(msg).textContent = "Connecting and running the first sync…";
  try {
    const r = await api(path, { method: "POST", body: JSON.stringify(body()) });
    const f = r.first_sync || {};
    $(msg).className = "small up";
    $(msg).textContent = f.error ? `Connected, but the first sync said: ${f.error}` : `Connected. ${f.new || 0} new trades${f.unread ? `, ${f.unread} emails it couldn't read (listed below)` : ""}.`;
    if (done) done();
    loadConnections(); loadHoldings(); if (typeof loadAccounts === "function") loadAccounts();
  } catch (err) { $(msg).className = "small down"; $(msg).textContent = err.message; }
});
connSubmit("#cx-email-form", "#cx-email-msg", "/api/connections/email", () => ({ user: $("#cx-mail").value.trim(), app_password: $("#cx-mail-pw").value.replace(/\s/g, "") }), () => { $("#cx-mail-pw").value = ""; });
connSubmit("#cx-cb-form", "#cx-cb-msg", "/api/connections/coinbase", () => ({ key_name: $("#cx-cb-name").value.trim(), private_key: $("#cx-cb-key").value.trim() }), () => { $("#cx-cb-key").value = ""; });
connSubmit("#cx-rh-form", "#cx-rh-msg", "/api/connections/robinhood", () => ({ api_key: $("#cx-rh-key").value.trim() }));
$("#cx-rh-pair").addEventListener("click", async () => {
  if ($("#cx-rh-pub").textContent && !confirm("Make a new key pair? A credential made with the old public key stops working.")) return;
  try { const r = await api("/api/connections/robinhood/keypair", { method: "POST" }); $("#cx-rh-pub").textContent = r.public_key; }
  catch (err) { $("#cx-rh-msg").textContent = err.message; }
});
$("#tr-settings").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    await api("/api/trade/settings", { method: "POST", body: JSON.stringify({ enabled: $("#tr-on").checked, max_order: +$("#tr-max").value || 250, daily_limit: +$("#tr-day").value || 500 }) });
    $("#tr-msg").textContent = "Saved";
  } catch (err) { $("#tr-msg").textContent = err.message; }
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
const currentTab = () => [...document.querySelectorAll(".tab")].find((t) => !t.hidden)?.id.replace(/^tab-/, "");
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
  loadConfidence();
  loadMoved();
  renderHomeLists();
  loadHomeFeeds();
  if (!quiet || Date.now() - briefState.loadedAt > 600000) { loadBrief(); loadWeekly(); loadHomeIdeas(); loadHomeDecisions(); }
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
  if (!homeTimer) homeTimer = setTimeout(() => { homeTimer = null; renderAccounts(); if (homeState.range === "1d") drawHome(); else if (homeState.data) paintHomeHeader(Book.totals().value || homeState.data.end); }, 700);
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
  return `<button type="button" class="rh-row" data-open="${s}">${logoImg(sym, 32)}
    <span class="rh-sym"><span class="rh-top"><b>${esc(sym.replace(/-USD$/, ""))}</b> ${nameOf(sym)}</span><span class="muted small">${sub}</span></span>
    <span class="rh-spark" data-spark="${s}">${sparkSvg(sym)}</span>
    <span class="rh-pill" data-live="${s}" data-lf="pill">${Live.prices[sym] ? fmtMoney(Live.prices[sym].price) : "—"}</span></button>`;
}
// ---------------------------------------------------------------- accounts
let acctData = null;
async function loadAccounts() {
  try { acctData = await api("/api/accounts"); } catch (err) { $("#home-accounts").innerHTML = `<p class="muted small">${esc(err.message)}</p>`; return; }
  renderAccounts();
}
function renderAccounts() {
  if (!acctData) return;
  const ICON = { Robinhood: "robinhood", Coinbase: "coinbase", Stash: "stash" };
  const rows = acctData.accounts.map((a) => {
    let value = 0, priced = true;
    a.positions.forEach((p) => { const t = Live.prices[p.symbol]; if (t) value += p.quantity * t.price; else { priced = false; value += p.cost; } });
    const how = a.auto ? a.auto.how : Object.keys(a.sources).join(" + ");
    const last = a.auto && a.auto.last ? `synced ${new Date(a.auto.last.at).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}`
      : a.last_trade ? `latest trade ${a.last_trade}` : "";
    const action = a.name === "Coinbase" && acctData.connections.coinbase_api ? `<button type="button" class="ghost small" data-acct-sync="coinbase">Sync now</button>`
      : a.auto && a.auto.how.startsWith("SnapTrade") ? `<button type="button" class="ghost small" data-acct-sync="snaptrade">Sync now</button>`
      : `<button type="button" class="ghost small" data-acct-import="${esc(a.name === "Robinhood" ? "robinhood" : a.name === "Coinbase" ? "coinbase" : "holdings")}">Connect</button>`;
    return `<div class="acct-row">
      <div class="acct-top"><span class="acct-badge acct-${ICON[a.name] || "other"}">${esc(a.name.slice(0, 1))}</span><b>${esc(a.name)}</b>
        <span class="acct-value">${fmtMoney(value, 0)}${priced ? "" : "*"}</span></div>
      <div class="muted small">${a.positions.length} holding${a.positions.length === 1 ? "" : "s"} · ${esc(how)}${last ? " · " + esc(last) : ""}</div>
      <div class="acct-logos">${a.positions.slice(0, 8).map((p) => `<span title="${esc(p.symbol)} ${fmtShares(p.quantity)}">${logoImg(p.symbol, 20)}</span>`).join("")}${a.positions.length > 8 ? `<span class="muted small">+${a.positions.length - 8}</span>` : ""}</div>
      <div class="small ${a.auto && a.auto.last && !a.auto.last.ok ? "down" : "muted"}">${esc(a.advice)}</div>
      <div>${action}</div></div>`;
  });
  $("#home-accounts").innerHTML = rows.join("") || `<p class="muted small">No accounts yet: Portfolio → Import your accounts.</p>`;
  $("#home-acct-meta").textContent = acctData.connections.snaptrade ? "SnapTrade connected" : "";
  document.querySelectorAll("[data-acct-import]").forEach((b) => b.addEventListener("click", () => {
    // Connecting beats re-importing: open the account's connection first.
    const cx = { robinhood: "#cx-email", coinbase: "#cx-coinbase", holdings: "#cx-email" }[b.dataset.acctImport];
    const el = cx && $(cx);
    selectTab(el ? "accounts" : "portfolio");
    if (el) { el.open = true; setTimeout(() => el.scrollIntoView({ block: "center" }), 50); return; }
    const seg = document.querySelector(`#imp-seg button[data-src="${b.dataset.acctImport}"]`);
    if (seg) { seg.click(); seg.scrollIntoView({ block: "center" }); }
  }));
  document.querySelectorAll("[data-acct-sync]").forEach((b) => b.addEventListener("click", async () => {
    b.disabled = true; b.textContent = "Syncing…";
    try {
      const r = await api(`/api/sync/${b.dataset.acctSync}`, { method: "POST" });
      b.textContent = `${r.new} new`;
      loadHoldings(); loadAccounts();
    } catch (err) { b.textContent = "Failed"; b.title = err.message; }
  }));
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
  if (!acctData || Date.now() - (acctData._at || 0) > 60000) loadAccounts().then(() => { if (acctData) acctData._at = Date.now(); });
  else renderAccounts();
  if (!window._bmAt || Date.now() - window._bmAt > 600000) { window._bmAt = Date.now(); loadBenchmark(); }
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
    <div class="tt-live" id="tt-live" hidden></div>
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
  setupSending(sym, $$, shares, price);
}

// Sending the order from the app (Coinbase, Robinhood crypto): preview, then confirm.
const VENUE_NAME = { coinbase: "Coinbase", robinhood: "Robinhood (crypto)", paper: "Paper account (pretend money)", alpaca: "Alpaca", public: "Public.com", ticket: "your broker app" };
async function setupSending(sym, $$, shares, price) {
  let info;
  try { info = await api("/api/trade/venues/" + encodeURIComponent(sym)); } catch { return; }
  const apiVenues = info.venues.filter((v) => v !== "ticket");
  const box = $$("tt-live");
  if (!apiVenues.length) {
    box.hidden = false;
    box.innerHTML = `<p class="muted small">${isCryptoSym(sym) ? "Connect Coinbase or Robinhood crypto (Portfolio → Accounts) to send orders from here."
      : "Robinhood and Stash have no stock API: open it there, and the confirmation email brings the trade in. To trade stocks from here, connect Alpaca or Public.com (Portfolio → Accounts → Brokers)."}</p>`;
    return;
  }
  box.hidden = false;
  box.innerHTML = `<div class="tt-send"><label>Send with <select id="tt-venue">${apiVenues.map((v) => `<option value="${v}">${VENUE_NAME[v]}</option>`).join("")}<option value="ticket">Open in the app myself</option></select></label>
      <button type="button" id="tt-preview">Preview order</button></div>
    <div id="tt-pv"></div>
    ${info.settings.enabled ? "" : `<p class="small muted">Real-money trading from the app is off (Portfolio → Accounts → Trading); the paper account works without it.</p>`}`;
  let timer = null;
  $$("tt-preview").addEventListener("click", async () => {
    const venue = $$("tt-venue").value, out = $$("tt-pv");
    if (venue === "ticket") { out.innerHTML = `<p class="muted small">Use Open in ${esc(brokerFor(sym, []).acct)} above.</p>`; return; }
    const q = shares(), dollars = tradeState.unit === "dollars" ? (+$$("tt-qty").value || 0) : null;
    if (!(q > 0)) { out.innerHTML = `<p class="small down">Enter an amount first.</p>`; return; }
    const body = { venue, symbol: sym, side: tradeState.side, dollars: dollars || null, quantity: dollars ? null : q,
      limit_price: tradeState.type === "limit" ? price() : null };
    out.innerHTML = `<p class="muted small">Asking ${esc(VENUE_NAME[venue])} for a quote…</p>`;
    clearInterval(timer);
    try {
      const pv = await api("/api/trade/preview", { method: "POST", body: JSON.stringify(body) });
      const e = pv.estimate;
      out.innerHTML = `<div class="tt-pv-card">
        <p><b>${tradeState.side === "buy" ? "Buy" : "Sell"} ${(+e.quantity).toLocaleString(undefined, { maximumFractionDigits: 8 })} ${esc(sym.replace(/-USD$/, ""))}</b>
          for about <b>${fmtMoney(e.usd, 2)}</b> at ${fmtMoney(e.price)}${e.fees ? ` + ${fmtMoney(e.fees, 2)} fees` : ""} on ${esc(e.broker)}</p>
        ${e.note ? `<p class="muted small">${esc(e.note)}</p>` : ""}
        ${pv.warnings.map((w) => `<p class="small warn-line">⚠ ${esc(w)}</p>`).join("")}
        ${pv.blockers.map((w) => `<p class="small down">✕ ${esc(w)}</p>`).join("")}
        ${venue === "paper" ? "" : `<p class="muted small">Today: ${fmtMoney(pv.spent_today, 2)} of your ${fmtMoney(pv.limits.daily_limit, 0)} daily limit.</p>`}
        ${pv.token ? `<button type="button" id="tt-place" class="tt-place">Confirm: ${tradeState.side} on ${esc(e.broker)} <span id="tt-count"></span></button>` : ""}</div>`;
      if (!pv.token) return;
      let left = pv.expires_in;
      timer = setInterval(() => { left -= 1; const c = $$("tt-count"); if (c) c.textContent = `(${left}s)`; if (left <= 0) { clearInterval(timer); const b = $$("tt-place"); if (b) { b.disabled = true; b.textContent = "Expired: preview again"; } } }, 1000);
      $$("tt-place").addEventListener("click", async (ev) => {
        ev.target.disabled = true; ev.target.textContent = "Sending…"; clearInterval(timer);
        try {
          const r = await api("/api/trade/place", { method: "POST", body: JSON.stringify({ ...body, token: pv.token }) });
          out.innerHTML = `<p class="up"><b>Sent to ${esc(VENUE_NAME[r.venue])}.</b> Order ${esc(r.broker_order_id || "")}</p><p class="muted small">${esc(r.note)}</p>`;
          setTimeout(() => { loadHoldings().then(renderPosition); }, 12000);
        } catch (err) { out.innerHTML = `<p class="down small">Not sent: ${esc(err.message)}</p>`; }
      });
    } catch (err) { out.innerHTML = `<p class="down small">${esc(err.message)}</p>`; }
  });
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
      ${logoImg(h.symbol, 28)}<button type="button" class="linkish pc-sym" data-open="${s}">${esc(h.symbol.replace(/-USD$/, ""))}</button> ${nameOf(h.symbol)}
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
  $("#hp-reinvest").innerHTML = d.reinvest.map((q) => `<li><button type="button" class="linkish" data-open="${esc(q.symbol)}">${tick(q.symbol)}</button>
      ${q.amount ? `<b>${fmtMoney(q.amount, 0)}</b> ` : ""}<span class="muted small">${esc(q.why)}</span></li>`).join("");
  const ev = d.events || { earnings: [], macro: [] };
  $("#hp-events").innerHTML = [...ev.earnings.map((e) => `<li><b>${esc(e.date)}</b> <button type="button" class="linkish" data-open="${esc(e.symbol)}">${esc(e.symbol)}</button> earnings${e.estimated ? " (estimated)" : ""}
      ${e.move_pct != null ? `· options ±${e.move_pct.toFixed(1)}% ≈ <b>±${fmtMoney(e.move_dollars, 0)}</b> on yours <span class="muted small">(to ${esc(e.expiry)})</span>` : ""}</li>`),
    ...ev.macro.slice(0, 12).map((m) => `<li><b>${esc(m.date)}</b> ${esc(m.kind)} <span class="muted small">${esc(m.name)}${m.consensus ? ` · forecast ${esc(m.consensus)}` : ""}</span></li>`)].join("")
    || `<li class="muted">No earnings for your stocks in the next 100 days, and no big releases in two weeks.</li>`;
  renderTax(d.tax);
  loadYearEnd();
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
async function loadYearEnd() {
  let y;
  try { y = await api("/api/taxes/yearend"); } catch (err) { $("#ye-meta").textContent = err.message; return; }
  $("#ye-meta").textContent = y.days_left ? `${y.days_left} days until ${y.last_trading_day}, the last trading day` : `Last trading day was ${y.last_trading_day}`;
  $("#ye-filing").value = y.settings.filing; $("#ye-income").value = y.settings.taxable_income ?? "";
  $("#ye-carry").value = y.settings.carryover || ""; $("#ye-zero").value = y.settings.zero_limit ?? ""; $("#ye-zero").placeholder = y.default_zero_limit;
  const owed = (x) => x < 0 ? `<b class="up">−${fmtMoney(-x, 0)}</b><span class="muted small">a net loss: lowers tax on your other income</span>` : `<b>${fmtMoney(x, 0)}</b>`;
  $("#ye-sums").innerHTML = `<div><span class="muted small">Tax on this year's gains now</span>${owed(y.tax_before)}</div>
    <div><span class="muted small">After the losses below</span>${owed(y.tax_after)}<span class="muted small">saves ${fmtMoney(y.saves, 0)}</span></div>
    <div><span class="muted small">Loss carried to next year</span><b>${fmtMoney(y.carry_forward, 0)}</b></div>`;
  const parts = [];
  if (y.harvest.length) parts.push(`<h3>Losses to take</h3><ul class="hp-lines">${y.harvest.map((h) => `<li><b>${esc(h.symbol)}</b>${h.account ? ` <span class="muted small">${esc(h.account)}</span>` : ""}
    sell ${fmtShares(h.quantity)} for a ${fmtMoney(h.loss, 0)} ${h.long_term ? "long" : "short"}-term loss: saves about <b>${fmtMoney(h.saves, 0)}</b>; hold <b>${esc(h.replacement)}</b> for 31 days
    ${h.warnings.map((w) => `<div class="down small">${esc(w)}</div>`).join("")}</li>`).join("")}</ul>`);
  else parts.push(`<p class="muted small">No loss worth taking${y.tax_before > 0 ? " among your holdings" : ": there's nothing to offset yet"}.</p>`);
  if (y.blocked.length) parts.push(`<h3>Losses you can't take cleanly yet</h3><ul class="hp-lines">${y.blocked.map((b) => `<li><b>${esc(b.symbol)}</b> ${fmtMoney(b.loss, 0)}: <span class="muted small">${esc(b.why)}</span></li>`).join("")}</ul>`);
  const z = y.zero_bracket;
  if (!z) parts.push(`<p class="muted small">Add your taxable income above to see whether you could take long-term gains at 0%.</p>`);
  else if (z.room <= 0) parts.push(`<p class="muted small">No room in the 0% long-term bracket this year (limit ${fmtMoney(z.limit, 0)}).</p>`);
  else parts.push(`<h3>Gains you could take at 0%</h3><p class="small">Up to <b>${fmtMoney(z.room, 0)}</b> of long-term gains fit under the ${fmtMoney(z.limit, 0)} limit.
      Sell and buy straight back to raise your cost basis, tax-free:</p>
    <ul class="hp-lines">${z.lots.map((l) => `<li><b>${esc(l.symbol)}</b> ${fmtShares(l.quantity)} sh bought ${esc(l.bought)}${l.account ? ` in ${esc(l.account)}` : ""}:
      ${fmtMoney(l.gain, 0)} gain <span class="muted small">(about ${fmtMoney(l.future_tax_avoided, 0)} of future tax avoided)</span></li>`).join("") || `<li class="muted">No long-term lots with gains.</li>`}</ul>
    <p class="muted small">${esc(z.note)}</p>`);
  $("#ye-body").innerHTML = parts.join("");
}
$("#ye-settings").addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = { filing: $("#ye-filing").value, taxable_income: numOrNull($("#ye-income").value), carryover: numOrNull($("#ye-carry").value),
    zero_limit: numOrNull($("#ye-zero").value) };
  try { await api("/api/taxes/yearend/settings", { method: "POST", body: JSON.stringify(body) }); $("#ye-msg").textContent = "Saved"; loadYearEnd(); }
  catch (err) { $("#ye-msg").textContent = err.message; }
});
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
    <span class="in-bar"><span style="width:${(m.amount / max * 100).toFixed(1)}%"></span></span><b>${fmtMoney(m.amount, m.amount && m.amount < 10 ? 2 : 0)}</b></div>`).join("");
  $("#in-upcoming").innerHTML = d.upcoming.map((u) => `<li><b>${esc(u.ex_date)}</b> <button type="button" class="linkish" data-open="${esc(u.symbol)}">${tick(u.symbol)}</button>
    about <b>${fmtMoney(u.amount, 2)}</b> <span class="muted small">(${fmtMoney(u.per_share, 4)} a share${u.pay_date ? `, paid ${esc(u.pay_date)}` : ""}${u.estimated ? ", date estimated from past spacing" : ", declared"})</span></li>`).join("")
    || `<li class="muted">None of your holdings pays a regular dividend.</li>`;
  $("#in-rows").innerHTML = d.holdings.map((h) => `<tr><td><button type="button" class="linkish" data-open="${esc(h.symbol)}">${tick(h.symbol)}</button></td>
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
    out.innerHTML = d.buys.map((b) => `<li><b>${fmtMoney(b.amount, 2)}</b> → <button type="button" class="linkish" data-open="${esc(b.symbol)}">${tick(b.symbol)}</button>
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
// Installable on a phone (Add to Home Screen). Browsers only allow this on https or localhost.
if ("serviceWorker" in navigator && (location.protocol === "https:" || ["localhost", "127.0.0.1"].includes(location.hostname))) {
  navigator.serviceWorker.register("/sw.js").catch(() => {});
}

// ---------------------------------------------------------------- transfers between your accounts
async function loadTransfers() {
  let v;
  try { v = await api("/api/transfers"); } catch (err) { $("#tf-open").textContent = err.message; return; }
  $("#tf-accts").innerHTML = v.accounts.map((a) => `<option value="${esc(a)}">`).join("");
  if (!$("#tf-day").value) $("#tf-day").value = localDate();
  const others = (leg) => v.open.filter((o) => o.symbol === leg.symbol && o.direction !== leg.direction);
  $("#tf-open").innerHTML = v.open.length ? `<h3 class="small">Needs a decision</h3>` + v.open.map((g) => {
    const pairs = others(g).map((o) => `<option value="${o.id}">${esc(o.direction === "in" ? "arrived in" : "left")} ${esc(o.account)} ${esc(o.day)} (${fmtShares(o.quantity)})</option>`).join("");
    const act = g.direction === "in"
      ? `<form class="inline-form tf-act" data-leg="${g.id}" data-how="bought"><label>paid $<input name="cost" type="number" step="any" min="0" required></label>
          <label>on <input name="acquired" type="date" required></label><button type="submit" class="small">Save cost</button></form>`
      : `<form class="inline-form tf-act" data-leg="${g.id}" data-how="wallet"><input name="account" placeholder="My wallet's name" required maxlength="60">
          <button type="submit" class="small">Moved to my wallet</button></form>
         <form class="inline-form tf-act" data-leg="${g.id}" data-how="sold"><label>spent at $<input name="price" type="number" step="any" min="0" required></label>
          <button type="submit" class="small secondary">Spent / sold</button></form>`;
    return `<div class="tf-item"><p>${logoImg(g.symbol, 16)} ${esc(g.ask)}</p>
      ${pairs ? `<form class="inline-form tf-act" data-leg="${g.id}" data-how="pair"><select name="other">${pairs}</select><button type="submit" class="small">Same move</button></form>` : ""}
      ${act}<button type="button" class="link small tf-del" data-leg="${g.id}">Delete</button></div>`;
  }).join("") : `<p class="muted">Nothing waiting: every send and receive is accounted for.</p>`;
  $("#tf-suggest").innerHTML = v.suggested.length ? `<h3 class="small mt">Looks like a move</h3>` + v.suggested.map((m, i) =>
    `<p>${logoImg(m.symbol, 16)} ${esc(m.from)} has ${fmtShares(m.sent)} ${esc(m.symbol)} less than the ledger says and ${esc(m.to)} has ${fmtShares(m.received)} more.
     <button type="button" class="small tf-accept" data-i="${i}">Record the move</button></p>`).join("") : "";
  $("#tf-moves").innerHTML = v.moves.map((m) => `<li>${logoImg(m.symbol, 16)} <b>${esc(m.symbol)}</b> ${fmtShares(m.sent)} from ${esc(m.from)} → ${esc(m.to)}
      ${m.received !== m.sent ? `(${fmtShares(m.received)} arrived)` : ""} · ${esc(m.sent_on)} <button type="button" class="link small tf-del" data-leg="${m.id}">Undo</button></li>`).join("")
    || `<li class="muted">No moves recorded.</li>`;
  document.querySelectorAll(".tf-act").forEach((f) => f.addEventListener("submit", async (e) => {
    e.preventDefault();
    const d = Object.fromEntries(new FormData(f));
    const body = { how: f.dataset.how };
    if (d.other) body.other = +d.other;
    if (d.cost) body.cost = +d.cost;
    if (d.acquired) body.acquired = d.acquired;
    if (d.account) body.account = d.account.trim();
    if (d.price) body.price = +d.price;
    try { await api(`/api/transfers/${f.dataset.leg}/resolve`, { method: "POST", body: JSON.stringify(body) }); loadTransfers(); loadHoldings(); }
    catch (err) { alert(err.message); }
  }));
  document.querySelectorAll(".tf-del").forEach((b) => b.addEventListener("click", async () => {
    if (!confirm("Delete this? A cost or sale it added is removed too.")) return;
    await api(`/api/transfers/${b.dataset.leg}`, { method: "DELETE" }); loadTransfers(); loadHoldings();
  }));
  document.querySelectorAll(".tf-accept").forEach((b) => b.addEventListener("click", async () => {
    const m = v.suggested[+b.dataset.i];
    try {
      await api("/api/transfers", { method: "POST", body: JSON.stringify({ symbol: m.symbol, from_account: m.from, to_account: m.to,
        sent: m.sent, received: m.received, day: $("#tf-day").value || localDate() }) });
      loadTransfers(); loadHoldings();
    } catch (err) { alert(err.message); }
  }));
}
$("#tf-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const recv = $("#tf-recv").value;
  try {
    await api("/api/transfers", { method: "POST", body: JSON.stringify({ symbol: $("#tf-sym").value.trim(), from_account: $("#tf-from").value.trim(),
      to_account: $("#tf-to").value.trim(), sent: +$("#tf-sent").value, received: recv ? +recv : null, day: $("#tf-day").value }) });
    $("#tf-msg").className = "small up"; $("#tf-msg").textContent = "Recorded: the lots moved with their original cost and dates.";
    e.target.reset(); loadTransfers(); loadHoldings();
  } catch (err) { $("#tf-msg").className = "small down"; $("#tf-msg").textContent = err.message; }
});

// ---------------------------------------------------------------- taxes: which shares to sell, and the export
async function loadTaxes() {
  try {
    const m = await api("/api/lots/methods");
    $("#lp-acct").innerHTML = `<option value="">any account</option>` + m.accounts.map((a) => `<option>${esc(a)}</option>`).join("");
    $("#lp-methods").innerHTML = m.accounts.map((a) => `<div class="row small">${esc(a)}
      <select data-lot-acct="${esc(a)}">${Object.entries(m.choices).map(([k, v]) => `<option value="${k}"${(m.methods[a] || "fifo") === k ? " selected" : ""}>${esc(v)}</option>`).join("")}</select></div>`).join("")
      || `<p class="muted">No accounts yet.</p>`;
    document.querySelectorAll("[data-lot-acct]").forEach((sel) => sel.addEventListener("change", async () => {
      await api("/api/lots/methods", { method: "POST", body: JSON.stringify({ account: sel.dataset.lotAcct, method: sel.value }) });
    }));
  } catch (err) { $("#lp-methods").textContent = err.message; }
  const yr = new Date().getFullYear();
  if (!$("#te-year").options.length) {
    try {
      const s = await api(`/api/taxes/export?year=${yr}`);
      $("#te-year").innerHTML = s.years.map((y) => `<option>${esc(y)}</option>`).join("");
    } catch { $("#te-year").innerHTML = `<option>${yr}</option>`; }
  }
  loadExportSummary();
}
async function loadExportSummary() {
  const y = $("#te-year").value;
  try {
    const s = await api(`/api/taxes/export?year=${y}`);
    const inc = Object.entries(s.income).map(([k, v]) => `${esc(k.replace("_", " "))} ${fmtMoney(v, 2)}`).join(" · ") || "none";
    $("#te-out").innerHTML = `<table class="data method-table"><tr><th>${esc(y)}</th><th>Proceeds</th><th>Cost</th><th>Wash adj.</th><th>Gain</th></tr>
      ${["short", "long"].map((t) => `<tr><td>${t === "short" ? "Short-term" : "Long-term"}</td><td>${fmtMoney(s[t].proceeds, 2)}</td><td>${fmtMoney(s[t].cost, 2)}</td>
        <td>${fmtMoney(s[t].adjustment, 2)}</td><td class="${cls(s[t].gain)}">${fmtMoney(s[t].gain, 2)}</td></tr>`).join("")}</table>
      <p class="muted">${s.sales} lot${s.sales === 1 ? "" : "s"} sold${s.wash_rows ? `, ${s.wash_rows} with a wash-sale adjustment (code W)` : ""}. Income: ${inc}.</p>`;
  } catch (err) { $("#te-out").textContent = err.message; }
}
$("#te-year").addEventListener("change", loadExportSummary);
$("#te-form").addEventListener("submit", (e) => { e.preventDefault(); location.href = `/api/taxes/export/sales.csv?year=${$("#te-year").value}`; });
$("#te-income").addEventListener("click", () => { location.href = `/api/taxes/export/income.csv?year=${$("#te-year").value}`; });
$("#lp-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const q = new URLSearchParams({ symbol: $("#lp-sym").value.trim(), quantity: $("#lp-qty").value, account: $("#lp-acct").value });
  if ($("#lp-price").value) q.set("price", $("#lp-price").value);
  $("#lp-out").innerHTML = `<p class="muted">Working it out…</p>`;
  try {
    const r = await api(`/api/lots/compare?${q}`);
    const best = r.methods.find((m) => m.method === r.best);
    $("#lp-out").innerHTML = `${r.short ? `<p class="down">You hold ${fmtShares(r.held)} ${esc(r.symbol)}${r.account ? " in " + esc(r.account) : ""}; this sells ${fmtShares(r.quantity)}.</p>` : ""}
      <table class="data method-table"><tr><th>Method</th><th>Short-term</th><th>Long-term</th><th>Tax</th></tr>
      ${r.methods.map((m) => `<tr class="${m.method === r.best ? "best" : ""}"><td>${esc(m.label)}</td><td class="${cls(m.short_term)}">${fmtMoney(m.short_term, 0)}</td>
        <td class="${cls(m.long_term)}">${fmtMoney(m.long_term, 0)}</td><td>${fmtMoney(m.tax, 0)}</td></tr>`).join("")}</table>
      <p>${r.saves_vs_fifo > 0 ? `<b>${esc(best.label)}</b> saves about <b>${fmtMoney(r.saves_vs_fifo, 0)}</b> against first in, first out. Before selling, set that at your broker: most let you choose a
        "cost basis method" in settings or pick specific lots on the sell order (check yours; the default is first in, first out).` : "First in, first out is already the cheapest here."}</p>
      <details><summary class="small">Lots each method sells</summary>${r.methods.map((m) => `<p><b>${esc(m.label)}</b>: ${m.lots.map((l) => `${fmtShares(l.quantity)} bought ${esc(l.bought)} at ${fmtMoney(l.cost, 2)}${l.long_term ? " (long)" : ""}`).join("; ") || "none"}</p>`).join("")}</details>`;
  } catch (err) { $("#lp-out").innerHTML = `<p class="down">${esc(err.message)}</p>`; }
});

// ---------------------------------------------------------------- cash waiting in your accounts
async function loadCashAccounts() {
  let v;
  try { v = await api("/api/cash/accounts"); } catch (err) { $("#cash-out").textContent = err.message; return; }
  $("#cash-acct").innerHTML = v.names.map((n) => `<option>${esc(n)}</option>`).join("");
  $("#cash-out").innerHTML = (v.accounts.length ? `<table class="data method-table"><tr><th>Account</th><th>Cash</th><th>Since</th><th>Earning</th><th>Missed a year</th></tr>
    ${v.accounts.map((a) => `<tr><td>${esc(a.account)}<div class="muted">${esc(a.source)}</div></td><td>${fmtMoney(a.amount, 2)}</td>
      <td>${a.days ? `${a.days} days` : "today"}</td><td>${a.apy ? a.apy.toFixed(2) + "%" : "0%"}</td>
      <td class="${a.idle ? "down" : ""}">${a.idle ? fmtMoney(a.missed_per_year, 0) : "—"}</td></tr>`).join("")}</table>` : `<p class="muted">No cash recorded. Coinbase fills in from its sync; add the others below.</p>`)
    + `<p class="muted">A Treasury-bill money-market fund pays about ${v.yield.toFixed(2)}% now${v.yield_live ? " (13-week T-bill yield)" : " (couldn't fetch today's rate; a typical figure)"}.</p>`
    + (v.note ? `<p><b>${esc(v.note)}</b> <button type="button" class="link" id="cash-plan">Open the planner</button></p>` : "");
  const b = $("#cash-plan");
  if (b) b.addEventListener("click", () => { selectTab("hold"); setTimeout(() => { const f = $("#nm-form"); if (f) f.scrollIntoView({ block: "center" }); }, 100); });
}
$("#cash-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const apy = $("#cash-apy").value;
  try {
    await api("/api/cash/accounts", { method: "POST", body: JSON.stringify({ account: $("#cash-acct").value, amount: +$("#cash-amt").value, apy: apy === "" ? null : +apy }) });
    e.target.reset(); loadCashAccounts();
  } catch (err) { alert(err.message); }
});

// ---------------------------------------------------------------- off-site backup and connection health
async function loadOffsite() {
  try {
    const o = await api("/api/offsite");
    $("#os-dir").value = o.dir || ""; $("#os-repo").value = o.repo || "";
    $("#os-token").placeholder = o.has_token ? "GitHub token saved (enter a new one to replace it)" : "GitHub token (fine-grained, Contents: write on that repo only)";
    $("#os-pass").placeholder = o.has_key ? "Passphrase set (enter a new one to change it)" : "";
    const l = o.last;
    $("#os-status").innerHTML = !o.configured ? `<p class="muted">Not set up yet.</p>`
      : l ? `<p class="${l.ok ? "up" : "down"}">${l.ok ? "Last copy" : "Last try failed"} ${esc(l.at.slice(0, 16).replace("T", " "))} UTC${l.ok ? " → " + esc(l.to.join(", ")) : ": " + esc(l.error)}</p>`
      : `<p class="muted">Set up: the first copy runs tonight after 2am (or press the button).</p>`;
  } catch (err) { $("#os-status").textContent = err.message; }
}
$("#os-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = { dir: $("#os-dir").value, repo: $("#os-repo").value };
  if ($("#os-pass").value) body.passphrase = $("#os-pass").value;
  if ($("#os-token").value) body.token = $("#os-token").value;
  $("#os-msg").className = "small muted"; $("#os-msg").textContent = "Saving and backing up…";
  try {
    const r = await api("/api/offsite", { method: "POST", body: JSON.stringify(body) });
    $("#os-pass").value = ""; $("#os-token").value = "";
    const b = r.backup;
    $("#os-msg").className = "small " + (b && !b.ok ? "down" : "up");
    $("#os-msg").textContent = !b ? "Saved. Add a passphrase and a folder or repository to start backing up." : b.ok ? `Backed up to ${b.to.join(" and ")}.${b.errors.length ? " " + b.errors.join(" ") : ""}` : b.error;
    loadOffsite();
  } catch (err) { $("#os-msg").className = "small down"; $("#os-msg").textContent = err.message; }
});
$("#os-restore-btn").addEventListener("click", () => $("#os-file").click());
$("#os-file").addEventListener("change", async () => {
  const f = $("#os-file").files[0];
  if (!f) return;
  const pass = prompt("The passphrase for this backup:");
  if (!pass) return;
  if (!confirm("Replace everything in this app with the backup's contents? (Today's data is kept beside it as a file.)")) return;
  const b64 = await new Promise((res) => { const r = new FileReader(); r.onload = () => res(String(r.result).split(",")[1]); r.readAsDataURL(f); });
  try {
    const r = await api("/api/offsite/restore", { method: "POST", body: JSON.stringify({ file_base64: b64, passphrase: pass }) });
    alert(`Restored. The previous data was kept as ${r.previous_kept_as}.`); location.reload();
  } catch (err) { alert(err.message); }
  $("#os-file").value = "";
});
async function loadHealth() {
  try {
    const h = await api("/api/health");
    $("#hl-list").innerHTML = h.checks.map((c) => `<li class="${c.ok ? "" : "down"}">${c.ok ? "●" : "▲"} ${esc(c.text)}</li>`).join("")
      || `<li class="muted">Nothing connected yet: connect an account above.</li>`;
  } catch (err) { $("#hl-list").textContent = err.message; }
}
async function loadLive() {
  try {
    const s = await api("/api/live/status");
    const ago = s.last_stock_tick ? Math.round((Date.now() - Date.parse(s.last_stock_tick)) / 1000) : null;
    $("#lv-status").innerHTML = s.mode === "finnhub"
      ? `<p class="up">Stocks: live trade stream (Finnhub)${ago != null ? `, last trade ${ago < 120 ? ago + "s" : Math.round(ago / 60) + " min"} ago` : ""}. Crypto: live from Coinbase.</p>`
      : `<p>Stocks: checked every few seconds (Yahoo)${ago != null ? `, last price ${ago < 120 ? ago + "s" : Math.round(ago / 60) + " min"} ago` : ""}. Crypto: live from Coinbase, tick by tick.</p>
         <p class="muted">For every stock trade as it happens, add a free Finnhub key: sign up at
         <a href="https://finnhub.io/register" target="_blank" rel="noopener noreferrer">finnhub.io</a>, copy the API key from the dashboard, paste it here.</p>`;
    $("#lv-form").hidden = s.mode === "finnhub";
  } catch (err) { $("#lv-status").textContent = err.message; }
}
$("#lv-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("#lv-msg").className = "small muted"; $("#lv-msg").textContent = "Checking the key…";
  try { await api("/api/live/finnhub", { method: "POST", body: JSON.stringify({ key: $("#lv-key").value.trim() }) });
    $("#lv-key").value = ""; $("#lv-msg").className = "small up"; $("#lv-msg").textContent = "Switched: stock prices now stream live."; loadLive(); }
  catch (err) { $("#lv-msg").className = "small down"; $("#lv-msg").textContent = err.message; }
});

// ---------------------------------------------------------------- brokers for the order engine
async function loadBrokers() {
  let b;
  try { b = await api("/api/brokers"); } catch (err) { $("#bk-brokers").textContent = err.message; return; }
  const pos = Object.entries(b.paper.positions);
  $("#bk-brokers").innerHTML = b.brokers.map((x) => `<div class="broker-row"><b>${esc(x.name)}</b>
      <span class="${x.ready ? "up" : "muted"}">${x.ready ? "● ready" : "○ not connected"}</span>
      <span class="grow muted">${esc(x.text)}</span>
      ${x.key === "alpaca" && !x.ready ? `<form class="inline-form" id="bk-alp"><input name="key_id" placeholder="API key ID" required>
        <input name="secret" type="password" placeholder="Secret key" required autocomplete="off">
        <label class="check"><input type="checkbox" name="live"> live (real money)</label><button type="submit" class="small">Connect</button></form>` : ""}
      ${x.key === "public" && !x.ready ? `<form class="inline-form" id="bk-pub"><input name="secret" type="password" placeholder="Public API secret" required autocomplete="off">
        <button type="submit" class="small">Connect</button></form>` : ""}
      ${x.key === "paper" ? `<button type="button" class="link small" id="bk-paper-reset">Reset to $10,000</button>` : ""}</div>`).join("")
    + (pos.length ? `<p class="muted">Paper holdings: ${pos.map(([s, p]) => `${esc(s)} ${fmtShares(p.quantity)}`).join(", ")}</p>` : "")
    + (b.alpaca && !b.alpaca.error ? `<p class="muted">Alpaca: ${fmtMoney(b.alpaca.cash, 2)} cash, ${fmtMoney(b.alpaca.value, 2)} total.</p>` : b.alpaca ? `<p class="down">${esc(b.alpaca.error)}</p>` : "")
    + `<p class="muted">Orders go from any stock's page: Trade → Send with. Each one is previewed and needs your confirm.</p><p class="small" id="bk-msg"></p>`;
  const send = (id, path, body) => { const f = $(id); if (!f) return; f.addEventListener("submit", async (e) => {
    e.preventDefault();
    try { await api(path, { method: "POST", body: JSON.stringify(body(Object.fromEntries(new FormData(f)), f)) }); loadBrokers(); }
    catch (err) { $("#bk-msg").className = "small down"; $("#bk-msg").textContent = err.message; }
  }); };
  send("#bk-alp", "/api/brokers/alpaca", (d, f) => ({ key_id: d.key_id.trim(), secret: d.secret.trim(), live: f.live.checked }));
  send("#bk-pub", "/api/brokers/public", (d) => ({ secret: d.secret.trim() }));
  const r = $("#bk-paper-reset");
  if (r) r.addEventListener("click", async () => { if (confirm("Start the paper account over with $10,000?")) { await api("/api/brokers/paper/reset", { method: "POST", body: "{}" }); loadBrokers(); } });
}

// ---------------------------------------------------------------- the app's own advice, scored
async function loadAdvice() {
  $("#adv-verdict").textContent = "Scoring…";
  try {
    const r = await api("/api/advice/record");
    $("#adv-verdict").textContent = r.verdict;
    const hz = { 30: "1 month", 91: "3 months", 182: "6 months" };
    $("#adv-out").innerHTML = `<table class="data method-table"><tr><th>After</th><th>Scored</th><th>Helped</th><th>Average vs VOO</th></tr>
      ${Object.entries(r.horizons).map(([h, x]) => `<tr><td>${hz[h]}</td><td>${x.resolved}${x.enough ? "" : ` <span class="muted">(needs ${r.min_resolved})</span>`}</td>
        <td>${x.hit_rate == null ? "—" : x.hit_rate + "%"}</td><td class="${cls(x.avg_edge)}">${x.avg_edge == null ? "—" : fmtPct(x.avg_edge)}</td></tr>`).join("")}</table>
      <h3 class="small mt">Latest advice</h3>
      <ul class="hp-lines">${r.items.slice(0, 15).map((i) => { const x = i.results[91] || i.results[30];
        return `<li><b>${esc(i.day)}</b> ${esc(i.action)} ${logoImg(i.symbol, 16)} ${esc(i.symbol)} at ${fmtMoney(i.price, 2)} <span class="muted">(${esc(i.source)}${i.reason ? ": " + esc(i.reason) : ""})</span>
          ${x ? ` → <span class="${x.helped ? "up" : "down"}">${x.helped ? "helped" : "hurt"} ${fmtPct(x.edge)} vs VOO</span>` : ` <span class="muted">· waiting</span>`}</li>`; }).join("")
        || `<li class="muted">Nothing logged yet: the first entries appear the day after you have holdings.</li>`}</ul>`;
  } catch (err) { $("#adv-verdict").textContent = err.message; }
}

// ---------------------------------------------------------------- news desk
const TIER_LABEL = { A: "Serious", B: "Worth a look", C: "Noise" };
const ACTION_LABEL = { review: "Review", watch: "Unconfirmed", note: "Note", ignore: "No action" };
async function loadNewsDesk(refresh = false) {
  $("#nd-out").innerHTML = `<p class="muted">Reading the sources…</p>`;
  try {
    const r = await api("/api/newsdesk" + (refresh ? "?refresh=true" : ""));
    const syms = Object.keys(r.desk);
    if (!syms.length) { $("#nd-out").innerHTML = `<p class="muted">No holdings yet.</p>`; return; }
    $("#nd-meta").textContent = r.at ? `checked ${new Date(r.at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}` : "";
    const rank = (d) => Math.min(...d.stories.map((s) => ({ review: 0, watch: 1, note: 2, ignore: 3 }[s.action])), 4);
    syms.sort((a, b) => rank(r.desk[a]) - rank(r.desk[b]));
    $("#nd-out").innerHTML = syms.map((sym) => {
      const d = r.desk[sym];
      const shown = d.stories.filter((s) => s.action !== "ignore").slice(0, 4);
      const quiet = d.stories.length - shown.length;
      return `<div class="nd-sym"><div class="row">${logoImg(sym, 20)} <b>${esc(sym)}</b> <span class="muted">${d.items} headlines${d.outlets_3d ? ` · ${d.outlets_3d} outlets in 3 days (GDELT)` : ""}</span></div>
        ${shown.map((st) => `<div class="nd-story nd-${st.action}"><span class="nd-badge">${ACTION_LABEL[st.action]}</span>
          <b>${esc(st.title)}</b>
          <div class="muted">${esc(TIER_LABEL[st.tier])} · ${esc(st.event.replace(/_/g, " "))} · ${st.sources} independent source${st.sources === 1 ? "" : "s"}
            (${esc(st.outlets.slice(0, 5).join(", "))}) · confidence ${Math.round(st.confidence * 100)}%${st.rumor ? " · rumor wording" : ""}${st.stale ? " · seen before (stale)" : ""}</div>
          <div>${esc(st.action_text)}</div>
          <div class="muted">${st.items.slice(0, 3).map((i) => `<a href="${esc(i.url)}" target="_blank" rel="noopener noreferrer">${esc(i.outlet)}</a>`).join(" · ")}</div></div>`).join("")}
        ${quiet > 0 ? `<p class="muted">${quiet} more stor${quiet === 1 ? "y" : "ies"} with no bearing on the plan.</p>` : ""}</div>`;
    }).join("");
    $("#nd-feeds").innerHTML = `<ul class="hp-lines">${Object.entries(r.feeds).sort().map(([k, v]) => `<li class="${v.ok ? "" : "down"}">${v.ok ? "●" : "▲"} ${esc(k)}: ${v.ok ? v.items + " items" : esc(v.error || "down")}</li>`).join("")}</ul>`;
  } catch (err) { $("#nd-out").innerHTML = `<p class="down">${esc(err.message)}</p>`; }
}
$("#nd-refresh").addEventListener("click", () => loadNewsDesk(true));

// ---------------------------------------------------------------- setup and what's been checked
const GO = { ask: ["ask", "#ask-q"], decisions: ["decisions", "#dc-list"], offsite: ["accounts", "#offsite"], "setup-alerts": ["accounts", "#su-alerts"], "cx-coinbase": ["accounts", "#cx-coinbase"],
  "cx-rh": ["accounts", "#cx-rh"], "cx-email": ["accounts", "#cx-email"], statement: ["portfolio", "#st-form"], live: ["accounts", "#live"],
  brokers: ["accounts", "#brokers"], transfers: ["accounts", "#transfers"], health: ["accounts", "#health"], connections: ["accounts", "#cx-email"] };
function goTo(where) {
  const [page, sel] = GO[where] || ["accounts", "#setup"];
  selectTab(page);
  setTimeout(() => { const el = $(sel); if (!el) return; if (el.tagName === "DETAILS") el.open = true; el.scrollIntoView({ block: "center" }); }, 150);
}
async function loadSetup() {
  let s, c;
  try { [s, c] = await Promise.all([api("/api/setup"), api("/api/confidence")]); } catch (err) { $("#su-steps").textContent = err.message; return; }
  $("#su-count").textContent = `${s.done} of ${s.total} done`;
  $("#su-steps").innerHTML = s.steps.map((st) => `<li class="${st.done ? "done" : ""}">
      <div class="su-head"><span class="su-mark">${st.done ? "✓" : "○"}</span><b>${esc(st.title)}</b></div>
      <div class="muted small">${esc(st.why)}</div>
      <div class="small ${st.done ? "up" : ""}">${esc(st.detail)}</div>
      ${st.key === "alerts" ? `<form class="inline-form" id="su-alerts"><input id="su-topic" value="${esc(s.ntfy_topic || s.suggested_topic)}" minlength="12" maxlength="64" pattern="[A-Za-z0-9_-]+" aria-label="ntfy topic">
        <button type="submit" class="small">Save and send a test</button></form>
        <p class="muted small">Install the free ntfy app, add this topic (keep it secret: anyone with the name can read your alerts), then press the button.</p>` : ""}
      <div class="row">${st.done ? "" : `<button type="button" class="small secondary su-go" data-go="${esc(st.go)}">Open</button>`}
        ${st.test ? `<button type="button" class="small su-test" data-key="${esc(st.key)}">Test now</button>` : ""}<span class="small su-out"></span></div></li>`).join("");
  document.querySelectorAll(".su-go").forEach((b) => b.addEventListener("click", () => goTo(b.dataset.go)));
  document.querySelectorAll(".su-test").forEach((b) => b.addEventListener("click", async () => {
    const out = b.parentElement.querySelector(".su-out");
    b.disabled = true; out.className = "small muted su-out"; out.textContent = "Testing…";
    try { const r = await api(`/api/setup/test/${encodeURIComponent(b.dataset.key)}`, { method: "POST" }); out.className = "small up su-out"; out.textContent = r.text; setTimeout(loadSetup, 1500); }
    catch (err) { out.className = "small down su-out"; out.textContent = err.message; }
    b.disabled = false;
  }));
  const f = $("#su-alerts");
  if (f) f.addEventListener("submit", async (e) => {
    e.preventDefault();
    const out = f.parentElement.querySelector(".su-out");
    try { await api("/api/setup/alerts", { method: "POST", body: JSON.stringify({ topic: $("#su-topic").value.trim() }) }); out.className = "small up su-out"; out.textContent = "Test sent: check your phone"; setTimeout(loadSetup, 800); }
    catch (err) { out.className = "small down su-out"; out.textContent = err.message; }
  });
  $("#su-fixes").innerHTML = c.fixes.length ? `<h3 class="small mt">To fix (${c.fixes.length})</h3><ul class="hp-lines">${c.fixes.map((x) =>
    `<li class="${x.level >= 2 ? "down" : ""}">${x.level >= 2 ? "▲" : "●"} ${esc(x.text)} <button type="button" class="link small su-fix" data-go="${esc(x.where)}">Fix</button></li>`).join("")}</ul>` : "";
  document.querySelectorAll(".su-fix").forEach((b) => b.addEventListener("click", () => goTo(b.dataset.go)));
}
async function loadConfidence() {
  const box = $("#home-conf");
  if (!box) return;
  try {
    const c = await api("/api/confidence");
    if (c.score == null) { box.innerHTML = `<p class="muted small">No holdings yet. <button type="button" class="link" id="conf-start">Start the setup</button></p>`; }
    else {
      box.innerHTML = `<div class="conf-score"><span class="conf-num ${c.score >= 80 ? "up" : c.score >= 40 ? "" : "down"}">${c.score}%</span>
        <span class="muted small">of your money (${fmtMoney(c.checked_value, 0)} of ${fmtMoney(c.total_value, 0)}) matched a broker balance today or a statement this month</span></div>
        ${c.accounts.map((a) => `<div class="conf-row"><span>${esc(a.account)}</span><span class="conf-bar"><i style="width:${a.pct || 0}%"></i></span><span class="small">${a.pct == null ? "—" : a.pct + "%"}</span></div>`).join("")}
        ${c.fixes.length ? `<p class="small ${c.fixes.some((x) => x.level >= 2) ? "down" : "muted"}">${c.fixes.length} thing${c.fixes.length === 1 ? "" : "s"} to fix: ${esc(c.fixes[0].text)}
          <button type="button" class="link" id="conf-fix">See all</button></p>` : ""}`;
    }
    [["#conf-start", "setup"], ["#conf-fix", "setup"]].forEach(([id]) => { const b = $(id); if (b) b.addEventListener("click", () => goTo("setup-top")); });
  } catch (err) { box.innerHTML = `<p class="muted small">${esc(err.message)}</p>`; }
}
$("#conf-setup").addEventListener("click", () => goTo("setup-top"));
GO["setup-top"] = ["accounts", "#setup"];

// ---------------------------------------------------------------- review: timing and contribution
let pfPeriod = "all";
async function loadPerformance() {
  $("#pf-verdict").textContent = "Working it out from your trades and daily prices…";
  try {
    const r = await api(`/api/performance?period=${pfPeriod}`);
    if (r.empty) { $("#pf-verdict").textContent = "No trades yet."; $("#pf-out").innerHTML = ""; $("#pf-contrib").innerHTML = ""; return; }
    $("#pf-verdict").textContent = r.verdict;
    const row = (k, v, c = "") => `<tr><td>${k}</td><td class="${c}">${v}</td></tr>`;
    $("#pf-out").innerHTML = `<table class="data method-table">
      ${row("Put in", fmtMoney(r.put_in, 0))}${row("Taken out", fmtMoney(r.taken_out, 0))}${r.income ? row("Dividends and interest paid to you", fmtMoney(r.income, 0)) : ""}${row("Worth now", fmtMoney(r.value, 0))}
      ${row("Gain", fmtMoney(r.gain, 0), cls(r.gain))}
      ${row("Holdings' return (time-weighted)", fmtPct(r.time_weighted) + (r.time_weighted_annual != null ? ` · ${fmtPct(r.time_weighted_annual)}/yr` : ""), cls(r.time_weighted))}
      ${row("Your dollars' return (money-weighted)", r.money_weighted_annual != null ? fmtPct(r.money_weighted_annual) + "/yr" : "—", cls(r.money_weighted_annual))}
      ${row("If you'd never sold anything", fmtMoney(r.never_sold_value, 0))}
      ${row("What your sales did", fmtMoney(r.sales_effect, 0), cls(r.sales_effect))}</table>
      ${r.unpriced.length ? `<p class="muted">No price history for ${esc(r.unpriced.join(", "))}: valued at trade prices.</p>` : ""}`;
    const max = Math.max(...r.contribution.map((c) => Math.abs(c.gain)), 1);
    $("#pf-contrib").innerHTML = r.contribution.slice(0, 15).map((c) => `<div class="ct-row">${logoImg(c.symbol, 18)}<b>${esc(c.symbol)}</b>
        <span class="ct-bar"><i class="${c.gain >= 0 ? "pos" : "neg"}" style="width:${Math.abs(c.gain) / max * 100}%"></i></span>
        <span class="${cls(c.gain)}">${fmtMoney(c.gain, 0)}</span></div>`).join("") || `<p class="muted">Nothing yet.</p>`;
  } catch (err) { $("#pf-verdict").textContent = err.message; }
}
document.querySelectorAll("#pf-period button").forEach((b) => b.addEventListener("click", () => {
  pfPeriod = b.dataset.p;
  document.querySelectorAll("#pf-period button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
  loadPerformance();
}));

// ---------------------------------------------------------------- review: crises and overlap
$("#cr-load").addEventListener("click", async () => {
  $("#cr-out").innerHTML = `<p class="muted">Fetching prices back to 2007…</p>`;
  try {
    const r = await api("/api/crises");
    $("#cr-out").innerHTML = r.crises.map((c) => `<div class="cr-item"><div class="row"><b>${esc(c.name)}</b>
        <span class="muted">${esc(c.start)} to ${esc(c.end)} · S&P 500 ${c.market_pct == null ? "—" : fmtPct(c.market_pct)} · back to the peak in ${esc(c.recovery)}</span></div>
        <div class="cr-big ${cls(c.change)}">${fmtMoney(c.change, 0)} <span class="small">(${fmtPct(c.change_pct)} of ${fmtMoney(r.total, 0)})</span></div>
        <details><summary class="small">By holding${c.stand_ins ? ` · ${c.stand_ins} use a stand-in` : ""}</summary>
          <ul class="hp-lines">${c.holdings.map((h) => `<li>${logoImg(h.symbol, 16)} <b>${esc(h.symbol)}</b> <span class="${cls(h.change)}">${fmtPct(h.change_pct)} (${fmtMoney(h.change, 0)})</span>
            <span class="muted">${esc(h.how)}</span></li>`).join("")}</ul></details></div>`).join("")
      + `<p class="muted">Write down now what you'll do if this happens: buy more, hold, or trim. Past falls aren't a forecast of the next one.</p>`;
  } catch (err) { $("#cr-out").innerHTML = `<p class="down">${esc(err.message)}</p>`; }
});
$("#ov-load").addEventListener("click", async () => {
  $("#ov-out").innerHTML = `<p class="muted">Comparing a year of daily moves…</p>`;
  try {
    const r = await api("/api/overlap");
    if (r.symbols.length < 2) { $("#ov-out").innerHTML = `<p class="muted">Needs two or more holdings with a year of prices.</p>`; return; }
    const shade = (v) => v == null ? "" : `background: color-mix(in srgb, var(--${v >= 0 ? "neg" : "pos"}) ${Math.round(Math.abs(v) * 70)}%, transparent)`;
    $("#ov-out").innerHTML = `<p>${r.pairs.length ? `<b>${r.pairs.length} pair${r.pairs.length === 1 ? "" : "s"} move almost as one:</b> ${r.pairs.slice(0, 6).map((p) => `${esc(p.a)} & ${esc(p.b)} (${p.rho.toFixed(2)})`).join(", ")}.`
      : "No pair moves almost as one (all below 0.8)."} Average correlation ${r.average == null ? "—" : r.average.toFixed(2)}.</p>
      <div class="table-scroll"><table class="data corr"><tr><th></th>${r.symbols.map((s) => `<th>${esc(s.replace(/-USD$/, ""))}</th>`).join("")}</tr>
      ${r.symbols.map((a) => `<tr><th>${esc(a.replace(/-USD$/, ""))}</th>${r.symbols.map((b) => { const v = (r.matrix[a] || {})[b];
        return `<td style="${a === b ? "" : shade(v)}">${a === b ? "" : v == null ? "—" : v.toFixed(2)}</td>`; }).join("")}</tr>`).join("")}</table></div>`;
  } catch (err) { $("#ov-out").innerHTML = `<p class="down">${esc(err.message)}</p>`; }
});

// ---------------------------------------------------------------- what moved today, and why
async function loadMoved() {
  const box = $("#home-moved");
  if (!box) return;
  try {
    const m = await api("/api/moved");
    if (!m.rows.length) { box.innerHTML = `<p class="muted">Nothing yet: prices and holdings load first.</p>`; $("#mv-head").textContent = ""; return; }
    $("#mv-head").innerHTML = `<span class="${cls(m.total)}">${fmtMoney(m.total, 0)}</span>`;
    box.innerHTML = m.rows.slice(0, 5).map((r) => `<div class="mv-row${r.big ? " big" : ""}">
        <div class="row">${logoImg(r.symbol, 18)} <b>${esc(r.symbol)}</b> <span class="${cls(r.change)}">${fmtMoney(r.change, 0)} (${fmtPct(r.change_pct)})</span>
          ${r.big ? `<span class="mv-flag">${r.typical ? (Math.abs(r.change_pct) / r.typical).toFixed(1) + "× usual" : "big move"}</span>` : ""}</div>
        ${r.why ? `<div class="muted">${r.why.url ? `<a href="${esc(r.why.url)}" target="_blank" rel="noopener noreferrer">${esc(r.why.title)}</a>` : esc(r.why.title)}
          · ${r.why.sources} source${r.why.sources === 1 ? "" : "s"}</div>` : r.big ? `<div class="muted">No news found.</div>` : ""}
        ${r.note ? `<div class="small">${esc(r.note)}</div>` : ""}</div>`).join("");
  } catch (err) { box.innerHTML = `<p class="muted">${esc(err.message)}</p>`; }
}

// ---------------------------------------------------------------- review: what changed in the annual reports
$("#tk-load").addEventListener("click", async () => {
  $("#tk-out").innerHTML = `<p class="muted">Reading each company's last two annual reports from the SEC (a minute the first time)…</p>`;
  try {
    const r = await api("/api/tenk");
    const LV = { big: "Changed a lot: read the new parts", some: "Some new text", little: "Mostly the same as last year" };
    $("#tk-out").innerHTML = (r.companies.map((c) => {
      if (c.error) return `<div class="tk-item"><b>${esc(c.symbol)}</b> <span class="muted">${esc(c.error)}</span></div>`;
      const sec = c.sections.risk, leg = c.sections.legal;
      const pct = (x) => x == null ? "—" : Math.round(x * 100) + "%";
      return `<div class="tk-item tk-${esc(c.level)}"><div class="row">${logoImg(c.symbol, 18)} <b>${esc(c.symbol)}</b> <span class="tk-level">${esc(LV[c.level] || "")}</span>
          <span class="muted">${esc(c.current.form)} filed ${esc(c.current.filed)} vs ${esc(c.previous.filed)} ·
          <a href="${esc(c.current.url)}" target="_blank" rel="noopener noreferrer">this year's</a> · <a href="${esc(c.previous.url)}" target="_blank" rel="noopener noreferrer">last year's</a></span></div>
        <div class="muted">Risk Factors: ${pct(sec.new_share)} new (${sec.new_count || 0} new sentences, ${sec.removed_count || 0} dropped), similarity ${sec.similarity == null ? "—" : sec.similarity.toFixed(2)} ·
          Legal Proceedings: ${pct(leg.new_share)} new</div>
        ${c.rank ? `<div class="small"><b>${esc(c.rank.text)}</b> <span class="muted">(S&amp;P 500 median ${pct(c.rank.median)}, ${c.rank.of} reports)</span></div>` : ""}
        ${sec.new && sec.new.length ? `<details${c.level === "big" ? " open" : ""}><summary class="small">What's new in Risk Factors</summary><ul class="hp-lines">${sec.new.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></details>` : ""}
        ${leg.new && leg.new.length ? `<details><summary class="small">What's new in Legal Proceedings</summary><ul class="hp-lines">${leg.new.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></details>` : ""}</div>`;
    }).join("") || `<p class="muted">No company stocks to compare (funds and coins don't file 10-Ks).</p>`)
      + (r.errors.length ? `<p class="muted">Couldn't read: ${esc(r.errors.join("; "))}</p>` : "");
  } catch (err) { $("#tk-out").innerHTML = `<p class="down">${esc(err.message)}</p>`; }
});

// ---------------------------------------------------------------- ask the company's filings
$("#ask-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const which = [...($("#ask-10k").checked ? ["10-K"] : []), ...($("#ask-10q").checked ? ["10-Q"] : []), ...($("#ask-er").checked ? ["earnings"] : [])];
  if (!which.length) { $("#ask-out").innerHTML = `<p class="down">Pick at least one report.</p>`; return; }
  const sym = symState.sym;
  $("#ask-out").innerHTML = `<p class="muted">Reading ${esc(sym)}'s ${esc(which.join(" and "))} (about a minute)…</p>`;
  try {
    const r = await api("/api/ask-filing", { method: "POST", body: JSON.stringify({ symbol: sym, question: $("#ask-q").value, filings: which }) });
    if (sym !== symState.sym) return;
    const text = r.segments.map((g) => esc(g.text) + g.cites.map((n) => `<sup class="cite">[${n}]</sup>`).join("")).join("");
    $("#ask-out").innerHTML = `<div class="ask-answer">${text.replace(/\n\n/g, "<br><br>")}</div>
      ${r.sources.length ? `<ol class="ask-sources">${r.sources.map((x) => `<li value="${x.n}"><span class="muted">${esc(x.document)}:</span> “${esc(x.quote)}”</li>`).join("")}</ol>` : `<p class="muted">No passages cited: treat this answer with care.</p>`}
      <p class="muted">${r.filings.map((f) => `<a href="${esc(f.url)}" target="_blank" rel="noopener noreferrer">${esc(f.form)} filed ${esc(f.filed)}</a>`).join(" · ")}
        · ${Math.round((r.usage.input + r.usage.cache_read + r.usage.cache_write) / 1000)}k tokens read${r.usage.cache_read ? " (mostly from cache)" : ""}</p>`;
  } catch (err) { $("#ask-out").innerHTML = `<p class="down">${esc(err.message)}</p>`; }
});

// ---------------------------------------------------------------- weekly recap (Home)
const weeklyState = { data: null, all: false };
async function loadWeekly() {
  if (!holdingsList.length) { $("#home-weekly").hidden = true; return; }
  try { weeklyState.data = await api("/api/weekly"); renderWeekly(); } catch { /* offline: leave hidden */ }
}
function renderWeekly() {
  const w = weeklyState.data;
  $("#home-weekly").hidden = false;
  $("#wk-title").textContent = w.title.replace(/^Your week: /, "This week: ");
  $("#wk-cadence").value = w.cadence || "weekly";
  $("#wk-list").innerHTML = w.lines.slice(0, weeklyState.all ? 99 : 8).map((ln) => `<li class="lvl${ln.level}"><span class="hb-sec">${esc(ln.section)}</span>
    ${ln.symbol ? `<button type="button" class="linkish" data-open="${esc(ln.symbol)}">${esc(ln.text)}</button>` : esc(ln.text)}</li>`).join("")
    + (w.lines.length > 8 && !weeklyState.all ? `<li><button type="button" class="linkish" id="wk-more">Show all ${w.lines.length}</button></li>` : "");
  document.querySelectorAll("#wk-list [data-open]").forEach((el) => el.addEventListener("click", () => openSymbol(el.dataset.open)));
  const more = $("#wk-more");
  if (more) more.addEventListener("click", () => { weeklyState.all = true; renderWeekly(); });
}
$("#wk-cadence").addEventListener("change", async (e) => {
  try { await api("/api/push-cadence", { method: "POST", body: JSON.stringify({ cadence: e.target.value }) }); $("#wk-note").textContent = "Saved: your phone gets the " + e.target.selectedOptions[0].textContent + "."; }
  catch (err) { $("#wk-note").textContent = err.message; }
});
$("#wk-send").addEventListener("click", async () => {
  try { await api("/api/weekly/send", { method: "POST" }); $("#wk-note").textContent = "Sent: check your phone."; } catch (err) { $("#wk-note").textContent = err.message; }
});

// ---------------------------------------------------------------- review: hidden style bets
$("#fx-load").addEventListener("click", async () => {
  $("#fx-out").innerHTML = `<p class="muted">Reading two years of daily prices and the factor files…</p>`;
  try {
    const r = await api("/api/factors");
    if (r.empty) { $("#fx-out").innerHTML = `<p class="muted">${esc(r.note || "No stocks or funds with price history to measure.")}</p>`; return; }
    const ref = Object.fromEntries(((r.reference || {}).loadings || []).map((l) => [l.factor, l.beta]));
    $("#fx-out").innerHTML = `<p class="verdict-line">${esc(r.summary)}</p>
      <table class="data fx-table"><thead><tr><th>Factor</th><th>You</th><th>VOO</th><th>What it means</th></tr></thead><tbody>
      ${r.loadings.map((l) => `<tr><td>${esc(l.name)}</td><td><b>${l.beta.toFixed(2)}</b> <span class="muted">t ${l.t.toFixed(1)}</span></td>
        <td class="muted">${ref[l.factor] != null ? ref[l.factor].toFixed(2) : "—"}</td><td>${esc(l.text)}</td></tr>`).join("")}</tbody></table>
      <p class="muted">${esc(r.start)} to ${esc(r.end)} (${r.days} trading days, the factor file's latest), ${r.holdings} holdings${r.crypto_share ? `; coins (${r.crypto_share}% of your money) left out` : ""}${r.missing.length ? `; no prices for ${esc(r.missing.join(", "))}` : ""}.
        Left over after the factors: ${fmtPct(r.alpha_annual)} a year (t ${r.alpha_t.toFixed(1)}), which two years of data can't tell from luck.</p>`;
  } catch (err) { $("#fx-out").innerHTML = `<p class="down">${esc(err.message)}</p>`; }
});

// ---------------------------------------------------------------- review: the S&P 500's most-changed annual reports
$("#tk-most").addEventListener("toggle", async (e) => {
  if (!e.target.open || $("#tk-most-out").dataset.loaded) return;
  $("#tk-most-out").innerHTML = `<p class="muted">Loading…</p>`;
  try {
    const r = await api("/api/tenk/most-changed?n=25");
    $("#tk-most-out").dataset.loaded = "1";
    $("#tk-most-out").innerHTML = `<ul class="hp-lines">${r.companies.map((c) => `<li>${tick(c.symbol)} <b>${Math.round(c.risk_new * 100)}% new</b>
        <span class="muted">${esc(c.name)} · ${esc(c.sector)} · filed ${esc(c.filed)}</span>
        ${(c.sample || []).length ? `<details><summary class="small">New sentences</summary><ul>${c.sample.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></details>` : ""}</li>`).join("")}</ul>
      <p class="muted">Ranking built ${esc((r.as_of || "").slice(0, 10))} from ${r.universe} companies.</p>`;
    $("#tk-most-out").querySelectorAll(".tk").forEach((el) => el.addEventListener("click", () => openSymbol(el.textContent.trim())));
  } catch (err) { $("#tk-most-out").innerHTML = `<p class="muted">${esc(err.message)}</p>`; }
});

// ---------------------------------------------------------------- income: dividend safety
const GRADE_CLS = { A: "up", B: "up", C: "", D: "down", F: "down", "?": "muted" };
$("#ds-load").addEventListener("click", async () => {
  $("#ds-out").innerHTML = `<p class="muted">Reading each company's cash flows from the SEC…</p>`;
  try {
    const r = await api("/api/income/safety");
    $("#ds-meta").textContent = r.grades.length ? `${r.grades.length} graded` : "";
    $("#ds-out").innerHTML = (r.grades.map((g) => `<div class="ds-item"><div class="row"><span class="ds-grade ${GRADE_CLS[g.grade] || ""}">${esc(g.grade)}</span>
        ${tick(g.symbol)} <span class="muted">${g.fcf_payout != null ? `${Math.round(g.fcf_payout)}% of free cash flow` : ""}${g.net_debt_ebitda != null ? ` · net debt ${g.net_debt_ebitda.toFixed(1)}× operating profit` : ""}${g.history && g.history.years_growing ? ` · ${g.history.years_growing} years of raises` : ""}</span></div>
        <ul class="hp-lines">${g.reasons.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div>`).join("")
      || `<p class="muted">None of your stocks pays a dividend.</p>`)
      + (r.errors.length ? `<p class="muted">Couldn't read: ${esc(r.errors.join("; "))}</p>` : "")
      + `<p class="muted">Funds and coins aren't graded. Quarter ends and figures come from the companies' 10-Q and 10-K filings.</p>`;
  } catch (err) { $("#ds-out").innerHTML = `<p class="down">${esc(err.message)}</p>`; }
});

// ---------------------------------------------------------------- symbol page: latest results
async function loadEarnings(sym) {
  const card = $("#sym-earn");
  card.hidden = true;
  if (/-USD$/.test(sym)) return;
  try {
    const r = await api("/api/earnings/" + encodeURIComponent(sym));
    if (sym !== symState.sym) return;
    const DIR = { raised: "Outlook raised", lowered: "Outlook lowered", kept: "Outlook kept", given: "Outlook given", none: "No outlook in the release" };
    const re = r.reaction, au = r.audited;
    $("#se-meta").innerHTML = `<a href="${esc(r.release.url)}" target="_blank" rel="noopener noreferrer">release filed ${esc(r.release.filed)}</a>`;
    $("#se-out").innerHTML = `${re ? `<p>Stock <b class="${cls(re.move_pct)}">${fmtPct(re.move_pct)}</b> on ${esc(re.day)}${re.times_usual ? ` (${re.times_usual}× a normal day)` : ""}</p>` : ""}
      <ul class="hp-lines">${r.highlights.map((x) => `<li>“${esc(x)}”</li>`).join("") || `<li class="muted">No headline numbers found in the release text.</li>`}</ul>
      <p><b>${esc(DIR[r.outlook.direction] || "")}</b></p>${r.outlook.lines.length ? `<ul class="hp-lines">${r.outlook.lines.map((x) => `<li>“${esc(x)}”</li>`).join("")}</ul>` : ""}
      ${au ? `<p class="muted">From the ${esc(au.quarter_end)} quarterly report (audited XBRL): revenue ${fmtPct(au.revenue_yoy)} on a year earlier,
        diluted EPS ${au.eps != null ? "$" + au.eps.toFixed(2) : "—"} (${fmtPct(au.eps_yoy)})${au.operating_margin != null ? `, operating margin ${au.operating_margin}%` : ""}.</p>`
        : `<p class="muted">The quarterly report's audited numbers appear here once the 10-Q is filed. The release leads with the company's own (often adjusted) figures.</p>`}`;
    card.hidden = false;
  } catch { /* funds and companies without a results release: no card */ }
}

// ---------------------------------------------------------------- size it: a dollar amount for an idea
const sizeBtn = (sym, src) => `<button type="button" class="secondary small size-btn" data-size="${esc(sym)}" data-src="${esc(src)}">Size it</button>`;
document.addEventListener("click", async (e) => {
  const b = e.target.closest("[data-size]");
  if (!b) return;
  // Under the item; for a table row, under the table (inside a sideways-scrolling table it would be cut off on a phone).
  const host = b.closest(".table-scroll") || b.closest(".id-item, .sl-item, .ch-item, .mv-row") || b.parentElement;
  const next = host.nextElementSibling;
  if (next && next.classList.contains("size-holder")) {
    next.remove();
    if (next.dataset.sym === b.dataset.size) return;
  }
  const box = document.createElement("div");
  box.className = "size-holder";
  box.dataset.sym = b.dataset.size;
  box.innerHTML = `<div class="size-out muted">Working out the amount for ${esc(b.dataset.size)}…</div>`;
  host.after(box);
  try {
    const r = await api(`/api/size/${encodeURIComponent(b.dataset.size)}?source=${encodeURIComponent(b.dataset.src)}`);
    const head = r.locked_until ? `<b>${esc(r.symbol)}: wait first</b>` : `<b>${esc(r.symbol)}: ${fmtMoney(r.amount, 0)}</b>${r.shares ? ` <span class="muted">≈ ${r.shares} share${r.shares === 1 ? "" : "s"} at ${fmtMoney(r.price, 2)}</span>` : ""}`;
    box.querySelector(".size-out").classList.remove("muted");
    box.querySelector(".size-out").innerHTML = `${head}<ul>${r.lines.map((ln) => `<li>${esc(ln)}</li>`).join("")}</ul>`;
  } catch (err) { box.querySelector(".size-out").innerHTML = `<span class="down">${esc(err.message)}</span>`; }
});

// ---------------------------------------------------------------- ideas: the weekly screen
const screenState = { data: null, which: "top_all" };
const pctTxt = (x) => x == null ? "—" : Math.round(x * 100) + "%";
const gradeCell = (g) => g == null ? `<span class="muted">—</span>` : `<span class="gr ${g >= 70 ? "up" : g < 30 ? "down" : ""}">${g}</span>`;
async function loadScreen() {
  try { screenState.data = await api("/api/screen"); } catch (err) { $("#sc-out").innerHTML = `<p class="muted">${esc(err.message)}</p>`; $("#bl-out").innerHTML = ""; return; }
  renderScreen();
}
function renderScreen() {
  const d = screenState.data;
  $("#sc-meta").textContent = `${d.universe.toLocaleString()} companies · ${d.quarter} · built ${String(d.as_of).slice(0, 10)}`;
  const rows = d[screenState.which] || [];
  screenNote();
  $("#sc-out").innerHTML = `<div class="table-scroll"><table class="data sc-table"><thead><tr><th>Company</th><th>Grade</th><th title="Operating profit on assets">Quality</th>
    <th>Value</th><th>Momentum</th><th title="Not issuing new shares">No dilution</th><th>12 mo</th><th></th><th></th></tr></thead><tbody>
    ${rows.slice(0, 30).map((r) => `<tr><td><button type="button" class="linkish" data-open="${esc(r.symbol)}">${tick(r.symbol)}</button> <span class="muted">${esc((r.name || "").slice(0, 28))}</span></td>
      <td><b>${r.score != null ? r.score.toFixed(0) : "—"}</b>${r.flaws && r.flaws.length ? ` <span class="chip-warn" title="One grade in the bottom 10% of its sector">flaw</span>` : ""}</td>
      <td>${gradeCell(r.grades.quality)}</td><td>${gradeCell(r.grades.value)}</td><td>${gradeCell(r.grades.momentum)}</td><td>${gradeCell(r.grades.low_issuance)}</td>
      <td class="${cls(r.return_12m)}">${r.return_12m != null ? fmtPct(r.return_12m * 100, 0) : "—"}</td>
      <td class="muted">${esc(r.sector || "")}</td><td>${screenState.which === "bottom" ? "" : sizeBtn(r.symbol, "qvm")}</td></tr>`).join("")}</tbody></table></div>
    <p class="muted">Grades are percentiles within the sector (100 = best). Quality is operating profit on assets; banks and insurers are compared on return on equity, and companies that report no operating profit on net income over assets.</p>`;
  $("#bl-out").innerHTML = (d.backlog || []).map((b) => `<div class="mv-row"><div class="row"><button type="button" class="linkish" data-open="${esc(b.symbol)}">${tick(b.symbol)}</button>
      <span class="muted">${esc((b.name || "").slice(0, 32))}</span> <span class="muted">grade ${b.score != null ? b.score.toFixed(0) : "—"}</span>${sizeBtn(b.symbol, "backlog")}</div>
      <div>${esc(b.why)}</div></div>`).join("") || `<p class="muted">None this week.</p>`;
  document.querySelectorAll("#tab-ideas [data-open]").forEach((el) => el.addEventListener("click", () => openSymbol(el.dataset.open)));
}
// What the replay since 2012 says about the list on screen (filled in once the backtest has loaded).
const SC_GROUP = { top_all: "all", small_mid: "small_mid", top_large: "large", bottom: "bottom" };
function screenNote() {
  const bt = screenState.bt, g = bt && bt.summary[SC_GROUP[screenState.which]];
  if (!g || !g.growth || g.growth.cagr == null) { $("#sc-note").textContent = ""; return; }
  const diff = g.growth.cagr - g.growth.cagr_spy, t = g["3m"] && g["3m"].t;
  $("#sc-note").textContent = screenState.which === "bottom"
    ? `In the replay since 2012 this group ${g["3m"].avg_edge < 0 ? "trailed" : "led"} SPY by ${Math.abs(g["3m"].avg_edge).toFixed(1)} points a quarter`
      + (t != null && Math.abs(t) < 2 ? ", within luck: if you hold one, reread why, but don't sell on the grade alone." : ". If you hold one, look hard at why.")
    : `In the replay since 2012, the ${g.label.toLowerCase()} ${diff >= 0 ? "beat" : "trailed"} SPY by ${Math.abs(diff).toFixed(1)} points a year`
      + (t != null && Math.abs(t) < 2 ? ", not enough to rule out luck" : "") + (diff < 0 ? ": for these, VOO has been hard to beat." : ".");
}
document.querySelectorAll("#sc-which button").forEach((b) => b.addEventListener("click", () => {
  screenState.which = b.dataset.w;
  document.querySelectorAll("#sc-which button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
  if (screenState.data) renderScreen();
}));

// ---------------------------------------------------------------- Decisions: what Plumbline thinks you should do
const EV_WORD = { rule: "rule", mixed: "tested", unproven: "unproven" };
function decisionHtml(d, compact) {
  const act = d.action;
  const doLabel = !act ? "" : act.type === "trade" ? (act.side === "buy" ? `Buy${act.dollars ? " " + fmtMoney(act.dollars, 0) : ""}` : `Sell${act.dollars ? " " + fmtMoney(act.dollars, 0) : ""}`) : "Fix it";
  return `<div class="dc-item" data-key="${esc(d.key)}">
    <div class="dc-head">${d.new ? `<span class="dc-new">New</span>` : ""}<span class="dc-title">${esc(d.title)}</span>
      <span class="dc-ev ${esc(d.evidence.level)}" title="${esc(d.evidence.label)}">${esc(EV_WORD[d.evidence.level] || d.evidence.level)}</span></div>
    ${compact ? `<div class="muted small">${esc(d.why[0] || "")}</div>` : `<ul class="dc-why">${d.why.map((w) => `<li>${esc(w)}</li>`).join("")}</ul>`}
    <div class="dc-buttons">${act ? `<button type="button" class="small" data-dc="do">${esc(doLabel)}</button>` : ""}
      ${d.kind === "hold" ? "" : `<button type="button" class="secondary small" data-dc="later">Later</button><button type="button" class="secondary small" data-dc="skipped">Skip</button>`}
      <button type="button" class="linkish small" data-dc="ask">Ask why</button></div></div>`;
}
function wireDecisions(root, items, reload) {
  root.querySelectorAll(".dc-item").forEach((el) => {
    const d = items.find((x) => x.key === el.dataset.key);
    el.querySelectorAll("[data-dc]").forEach((b) => b.addEventListener("click", async () => {
      const what = b.dataset.dc;
      if (what === "ask") { selectTab("ask"); askQuestion(`Why do you recommend: "${d.title}"? What would you do instead if I disagree?`); return; }
      if (what === "do") {
        const a = d.action;
        if (a.type === "open") { selectTab(a.page); return; }
        openTradeTicket(a.symbol, a.side);
        if (a.dollars) {
          const unit = $("#tt-unit"), qty = $("#tt-qty");
          if (unit) { unit.value = "dollars"; unit.dispatchEvent(new Event("change")); }
          if (qty) { qty.value = Math.floor(a.dollars); qty.dispatchEvent(new Event("input")); }
        }
      }
      try { await api("/api/decisions/decide", { method: "POST", body: JSON.stringify({ key: d.key, status: what === "do" ? "approved" : what }) }); }
      catch { /* shown next load */ }
      if (what !== "do") reload();
    }));
  });
}
async function loadHomeDecisions() {
  let r;
  if ($("#home-decide").hidden) { $("#home-decide").hidden = false; $("#hd-list").innerHTML = `<p class="muted">Working out this week's decisions…</p>`; }
  try { r = await api("/api/decisions"); } catch { $("#home-decide").hidden = true; return; }
  const open = r.decisions.filter((d) => d.status === "open");
  $("#hd-meta").textContent = open.length ? `${open.length} open` : "all decided";
  $("#hd-list").innerHTML = open.slice(0, 3).map((d) => decisionHtml(d, true)).join("") || `<p class="muted">Nothing open. Holding is the plan.</p>`;
  wireDecisions($("#hd-list"), r.decisions, loadHomeDecisions);
}
$("#hd-all").addEventListener("click", () => selectTab("decisions"));
async function loadDecisions() {
  let r;
  try { r = await api("/api/decisions"); } catch (err) { $("#dc-list").innerHTML = `<p class="down">${esc(err.message)}</p>`; return; }
  $("#dc-meta").textContent = `updated ${String(r.as_of).replace("T", " ")}:00`;
  $("#dc-list").innerHTML = r.decisions.map((d) => decisionHtml(d, false)).join("") || `<p class="muted">Nothing open.</p>`;
  wireDecisions($("#dc-list"), r.decisions, loadDecisions);
  $("#dc-history").innerHTML = r.history.length ? `<div class="table-scroll"><table class="data"><thead><tr><th>Decision</th><th>You</th><th>When</th></tr></thead><tbody>
    ${r.history.map((h) => `<tr><td>${esc(h.title)}</td><td>${esc(h.status)}${h.until ? ` <span class="muted">until ${esc(h.until)}</span>` : ""}</td><td class="muted">${esc(h.decided)}</td></tr>`).join("")}</tbody></table></div>`
    : `<p class="muted">Nothing decided yet.</p>`;
}
function letterHtml(r) {
  return `<div class="ask-msg pl">${esc(r.answer)}</div>${r.tools && r.tools.length ? `<p class="muted">From: ${esc(r.tools.join(", "))}</p>` : ""}`;
}
async function loadLetter() {
  try {
    const r = await api("/api/letter");
    if (r.answer) { $("#lt-out").innerHTML = letterHtml(r); $("#lt-meta").textContent = `written ${String(r.written || "").replace("T", " ")}:00`; }
  } catch { /* none yet */ }
}
$("#lt-write").addEventListener("click", async () => {
  $("#lt-out").innerHTML = `<p class="muted">Writing (reads your decisions and weekly recap; about half a minute)…</p>`;
  try { const r = await api("/api/letter", { method: "POST" }); $("#lt-out").innerHTML = letterHtml(r); $("#lt-meta").textContent = "just now"; }
  catch (err) { $("#lt-out").innerHTML = `<p class="down">${esc(err.message)}</p>`; }
});

// ---------------------------------------------------------------- Ask Plumbline
const askState = { conversation: null, busy: false };
function askAppend(cls, html) {
  const el = document.createElement("div");
  el.className = "ask-msg " + cls;
  el.innerHTML = html;
  $("#ask-log").appendChild(el);
  el.scrollIntoView({ block: "nearest" });
  return el;
}
async function askQuestion(q) {
  q = (q || "").trim();
  if (!q || askState.busy) return;
  askState.busy = true;
  askAppend("me", esc(q));
  const wait = askAppend("pl", `<span class="muted">Reading your data…</span>`);
  try {
    const r = await api("/api/ask", { method: "POST", body: JSON.stringify({ question: q, conversation: askState.conversation }) });
    askState.conversation = r.conversation;
    wait.innerHTML = esc(r.answer) + (r.tools.length ? `<div class="muted">Looked at: ${esc(r.tools.join(", "))}</div>` : "");
  } catch (err) { wait.innerHTML = `<span class="down">${esc(err.message)}</span>`; }
  askState.busy = false;
}
$("#ask-form").addEventListener("submit", (e) => { e.preventDefault(); const q = $("#ask-q").value; $("#ask-q").value = ""; askQuestion(q); });
$("#ask-q").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("#ask-form").requestSubmit(); } });
document.querySelectorAll("#ask-chips .chip").forEach((b) => b.addEventListener("click", () => askQuestion(b.textContent)));

// ---------------------------------------------------------------- Home: are the ideas beating VOO?
async function loadHomeIdeas() {
  let r;
  try { r = await api("/api/ideas"); } catch { return; }
  if (!r.items.length && !(r.bottom_held || []).length) { $("#home-ideas").hidden = true; return; }
  $("#home-ideas").hidden = false;
  $("#hi-meta").textContent = `${r.items.length} ideas logged${r.logged_by_github ? " · sealed daily on GitHub" : ""}`;
  $("#hi-verdict").textContent = r.verdict;
  const rows = r.leaderboard.filter((b) => b.so_far.n).sort((a, b) => b.so_far.avg_edge - a.so_far.avg_edge);
  $("#hi-list").innerHTML = rows.map((b) => {
    const done = b["6m"].resolved ? ` · 6-month record: ${b["6m"].beat_voo}% beat VOO (${b["6m"].resolved})` : "";
    return `<li><span class="hb-sec">${esc(b.label)}</span> <span class="${cls(b.so_far.avg_edge)}">${fmtPct(b.so_far.avg_edge)}</span> vs VOO so far,
      ${b.so_far.beat_voo}% ahead (${b.so_far.n} open)${done}</li>`;
  }).join("") + (r.bottom_held || []).map((x) => `<li class="lvl2"><span class="hb-sec">You hold a bottom-50 stock</span>
      <button type="button" class="linkish" data-open="${esc(x.symbol)}">${esc(x.symbol)}</button>: ${esc(x.reasons[0])}</li>`).join("")
    + (r.broken && r.broken.length ? r.broken.map((x) => `<li class="lvl2"><span class="hb-sec">Case broken</span>
      <button type="button" class="linkish" data-open="${esc(x.symbol)}">${esc(x.symbol)}</button>: ${esc(x.reasons.join("; "))}</li>`).join("") : "")
    + `<li class="muted">Open ideas move around; only 6-month results with ${r.min_resolved}+ ideas count as a record.</li>`;
  document.querySelectorAll("#hi-list [data-open]").forEach((el) => el.addEventListener("click", () => openSymbol(el.dataset.open)));
}
$("#hi-open").addEventListener("click", () => selectTab("ideas"));

// ---------------------------------------------------------------- ideas: the screen's backtest
async function loadScreenBacktest() {
  let r;
  try { r = await api("/api/screen/backtest"); } catch { $("#sb-card").hidden = true; return; }
  $("#sb-card").hidden = false;
  screenState.bt = r;
  if (screenState.data) screenNote();
  $("#sb-meta").textContent = `${String(r.first).slice(0, 7)} to ${String(r.last).slice(0, 7)} · built ${String(r.as_of).slice(0, 10)}`;
  $("#sb-verdict").textContent = r.verdict;
  const cellH = (x) => x ? `<span class="${cls(x.avg_edge)}">${fmtPct(x.avg_edge)}</span> <span class="muted">· beat ${x.beat_pct}% · worst ${fmtPct(x.worst_edge)}</span>` : "—";
  const tbl = (rows) => `<div class="table-scroll"><table class="data"><thead><tr><th>Picks</th><th>3 months vs SPY</th><th>12 months vs SPY</th><th>$10,000 became</th></tr></thead><tbody>
    ${rows.map((g) => `<tr><td>${esc(g.label)}</td><td>${cellH(g["3m"])}${g["3m"] && g["3m"].t != null ? ` <span class="muted">t ${g["3m"].t}</span>` : ""}</td><td>${cellH(g["12m"])}</td>
      <td>${fmtMoney(g.growth.screen, 0)} <span class="muted">(SPY ${fmtMoney(g.growth.spy, 0)})</span></td></tr>`).join("")}</tbody></table></div>`;
  const all = Object.values(r.summary).filter((g) => g["3m"]);
  $("#sb-out").innerHTML = tbl(all.filter((g) => !g.check))
    + (all.some((g) => g.check) ? `<h3 class="small">Checks on the bottom of the ranking</h3>${tbl(all.filter((g) => g.check))}` : "");
}

// ---------------------------------------------------------------- ideas: events (raised guidance, spin-offs)
async function loadEvents() {
  $("#ev-pead").innerHTML = `<p class="muted">Reading this week's results releases (a minute the first time)…</p>`;
  let r;
  try { r = await api("/api/idea-events"); } catch (err) { $("#ev-pead").innerHTML = `<p class="down">${esc(err.message)}</p>`; $("#ev-spin").innerHTML = ""; return; }
  $("#ev-meta").textContent = r.errors.length ? r.errors.join("; ") : `as of ${r.as_of}`;
  $("#ev-pead").innerHTML = r.pead.map((x) => `<div class="mv-row"><div class="row"><button type="button" class="linkish" data-open="${esc(x.symbol)}">${tick(x.symbol)}</button>
      <span class="muted">${x.cap ? fmtMoney(x.cap / 1e9, 1) + "B" : ""}${x.score != null ? ` · grade ${Math.round(x.score)}` : ""}</span>${sizeBtn(x.symbol, "pead")}</div>
      <div>${esc(x.why)}</div>${x.outlook ? `<div class="muted">“${esc(x.outlook)}”</div>` : ""}</div>`).join("")
    || `<p class="muted">Nobody raised guidance to a big positive reaction in the last ten days.</p>`;
  const trading = r.spinoffs.filter((x) => x.stage === "trading"), coming = r.spinoffs.filter((x) => x.stage !== "trading");
  $("#ev-spin").innerHTML = (trading.map((x) => `<div class="mv-row"><div class="row"><button type="button" class="linkish" data-open="${esc(x.ticker)}">${tick(x.ticker)}</button>
      <span class="muted">${esc(x.name)}</span>${x.loggable ? sizeBtn(x.ticker, "spinoff") : ""}</div>
      <div>Trading since ${esc(x.trading_since)} (${x.days_trading} days): <span class="${cls(x.return_pct)}">${fmtPct(x.return_pct)}</span>${x.spy_pct != null ? ` vs SPY ${fmtPct(x.spy_pct)}` : ""}${x.score != null ? ` · grade ${Math.round(x.score)}` : ""}</div></div>`).join("")
    || `<p class="muted">No spin-off started trading in the last year and a half.</p>`)
    + (coming.length ? `<p class="muted">Registered, not trading yet: ${coming.slice(0, 12).map((x) => `${esc(x.name)}${x.ticker ? ` (${esc(x.ticker)})` : ""}, filed ${esc(x.last_filed)}`).join(" · ")}</p>` : "");
  document.querySelectorAll("#ev-pead [data-open], #ev-spin [data-open]").forEach((el) => el.addEventListener("click", () => openSymbol(el.dataset.open)));
}

// ---------------------------------------------------------------- ideas: paper portfolios (forward test)
async function loadPaper() {
  let r;
  try { r = await api("/api/paper"); } catch (err) { $("#pp-out").innerHTML = `<p class="down">${esc(err.message)}</p>`; return; }
  $("#pp-meta").textContent = r.books ? `${r.books} monthly books` : "";
  if (!r.lists.length) { $("#pp-out").innerHTML = `<p class="muted">${esc(r.note || "No month has finished yet: the first result shows a month after the first books.")}</p>`; return; }
  $("#pp-out").innerHTML = `<div class="table-scroll"><table class="data"><thead><tr><th>List</th><th>Since</th><th>$10,000 became</th><th>VOO</th><th>Months</th></tr></thead><tbody>
    ${r.lists.map((x) => `<tr><td>${esc(x.label)}</td><td>${esc(x.months.length ? x.months[0].from : x.open)}</td>
      <td class="${cls(x.ahead)}">${fmtMoney(x.value, 0)}</td><td>${fmtMoney(x.voo_value, 0)}</td><td>${x.months.length}</td></tr>`).join("")}</tbody></table></div>
    <p class="muted">A few months say nothing either way; the same 20-results rule as the scorecard applies before any list earns your money.</p>`;
}

// ---------------------------------------------------------------- ideas: the scorecard
async function loadIdeas() {
  let r;
  try { r = await api("/api/ideas"); } catch (err) { $("#id-verdict").textContent = err.message; return; }
  $("#id-verdict").textContent = r.verdict;
  $("#id-meta").textContent = `${r.items.length} logged · against ${r.benchmark}`;
  const cell = (h) => h.resolved ? `${h.beat_voo}% beat · ${fmtPct(h.avg_edge)} avg · worst ${fmtPct(h.worst)}${h.enough ? "" : ` <span class="muted">(${h.resolved})</span>`}` : `<span class="muted">none yet</span>`;
  $("#id-board").innerHTML = r.leaderboard.length ? `<div class="table-scroll"><table class="data"><thead><tr><th>Kind of idea</th><th>Logged</th><th>3 months</th><th>6 months</th><th>12 months</th></tr></thead><tbody>
    ${r.leaderboard.map((b) => `<tr><td>${esc(b.label)}</td><td>${b.ideas}</td><td>${cell(b["3m"])}</td><td>${cell(b["6m"])}</td><td>${cell(b["12m"])}</td></tr>`).join("")}</tbody></table></div>
    ${r.decisions.bought.n || r.decisions.passed.n ? `<p class="muted">Ideas you bought: ${r.decisions.bought.n} (${fmtPct(r.decisions.bought.avg_edge)} vs VOO so far) · passed on: ${r.decisions.passed.n} (${fmtPct(r.decisions.passed.avg_edge)}).</p>` : ""}` : "";
  const broken = Object.fromEntries((r.broken || []).map((x) => [x.id, x.reasons]));
  $("#id-list").innerHTML = `<h3 class="small">Latest ideas</h3>` + (r.items.slice(0, 40).map((i) => `<div class="id-item"><div class="row">
      <button type="button" class="linkish" data-open="${esc(i.symbol)}">${tick(i.symbol)}</button> <span class="muted">${esc(r.sources[i.source] || i.source)} · ${esc(i.day)} at ${fmtMoney(i.price, 2)}</span>
      ${i.so_far ? `<span class="${cls(i.so_far.edge)}">${fmtPct(i.so_far.edge)} vs VOO so far</span>` : ""}
      ${i.decision ? `<span class="chip">${esc(i.decision)}</span>` : `<span class="id-dec"><button type="button" class="secondary small" data-dec="bought" data-id="${i.id}">I bought it</button> <button type="button" class="secondary small" data-dec="passed" data-id="${i.id}">Passed</button></span>`}
      ${i.decision !== "passed" ? sizeBtn(i.symbol, i.source) : ""}</div>
      <div class="muted">${esc(i.reason)}${i.wrong_if ? ` · wrong if: ${esc(i.wrong_if)}` : ""}</div>
      ${broken[i.id] ? `<div class="id-broken">Case broken: ${esc(broken[i.id].join("; "))}</div>` : ""}</div>`).join("") || `<p class="muted">Nothing yet: the screens log their ideas once a day.</p>`);
  document.querySelectorAll("#id-list [data-open]").forEach((el) => el.addEventListener("click", () => openSymbol(el.dataset.open)));
  document.querySelectorAll("#id-list [data-dec]").forEach((el) => el.addEventListener("click", async () => {
    try { await api(`/api/ideas/${el.dataset.id}/decision`, { method: "POST", body: JSON.stringify({ decision: el.dataset.dec }) }); loadIdeas(); }
    catch (err) { $("#id-msg").textContent = err.message; }
  }));
}
$("#id-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    const r = await api("/api/ideas", { method: "POST", body: JSON.stringify({ symbol: $("#id-sym").value, reason: $("#id-reason").value, wrong_if: $("#id-wrong").value }) });
    $("#id-msg").textContent = `Logged ${r.logged} at ${fmtMoney(r.price, 2)}. It can't be edited: that's the point.`;
    $("#id-form").reset(); loadIdeas();
  } catch (err) { $("#id-msg").textContent = err.message; }
});

// ---------------------------------------------------------------- sleepers
async function loadSleepers() {
  $("#sl-out").innerHTML = `<p class="muted">Checking insider filings and headlines (a minute the first time)…</p>`;
  let r;
  try { r = await api("/api/sleepers"); } catch (err) { $("#sl-out").innerHTML = `<p class="down">${esc(err.message)}</p>`; return; }
  $("#sl-meta").textContent = r.screen_as_of ? `screen ${String(r.screen_as_of).slice(0, 10)}` : "";
  $("#sl-bucket").innerHTML = `<span class="${r.bucket.over ? "down" : ""}">${esc(r.bucket.text)}</span>${r.bucket.over ? " Over the limit: don't add more." : ""}`;
  const LV = { sleeper: "Sleeper: 3+ signals", "strong lead": "Strong lead: 2 signals", lead: "Lead: 1 signal" };
  $("#sl-out").innerHTML = (r.note ? `<p class="muted">${esc(r.note)}</p>` : "") + (r.sleepers.map((x) => `<div class="sl-item sl-${esc(x.level.replace(" ", "-"))}">
      <div class="row"><button type="button" class="linkish" data-open="${esc(x.symbol)}">${tick(x.symbol)}</button> <span class="muted">${esc((x.name || "").slice(0, 32))}</span>
        <span class="tk-level">${esc(LV[x.level])}</span> <span class="muted">${x.cap ? fmtMoney(x.cap / 1e9, 1) + "B" : ""} · ${esc(x.sector || "")}</span>${sizeBtn(x.symbol, "sleeper")}</div>
      <ul class="hp-lines">${x.why.map((w) => `<li>${esc(w)}</li>`).join("")}</ul></div>`).join("") || `<p class="muted">Nothing lines up this week. That's a fine answer.</p>`);
  document.querySelectorAll("#sl-out [data-open]").forEach((el) => el.addEventListener("click", () => openSymbol(el.dataset.open)));
}

// ---------------------------------------------------------------- signal backtests (insider buying, spin-offs)
async function loadSignalBacktests() {
  let r;
  try { r = await api("/api/signal-backtests"); } catch { return; }
  const ins = r.insider;
  if (ins) {
    $("#ib-card").hidden = false;
    $("#ib-meta").textContent = `since ${esc(ins.since)} · built ${String(ins.as_of).slice(0, 10)}`;
    $("#ib-verdict").textContent = ins.verdict;
    const c = (x) => x ? `<span class="${cls(x.avg_edge)}">${fmtPct(x.avg_edge)}</span> <span class="muted">· ${x.beat_pct}% of months · t ${x.t ?? "—"}</span>` : "—";
    $("#ib-out").innerHTML = `<div class="table-scroll"><table class="data"><thead><tr><th>Purchases</th><th>3 months vs SPY</th><th>6 months</th><th>12 months</th></tr></thead><tbody>
      ${Object.values(ins.summary).map((g) => `<tr><td>${esc(g.label)}</td><td>${c(g["3m"])}</td><td>${c(g["6m"])}</td><td>${c(g["12m"])}</td></tr>`).join("")}</tbody></table></div>`;
  }
  if (r.spinoff) $("#ev-spin-bt").textContent = r.spinoff.verdict;
}

// ---------------------------------------------------------------- chatter and themes
async function loadChatter() {
  let r;
  try { r = await api("/api/chatter"); } catch (err) { $("#ch-out").innerHTML = `<p class="down">${esc(err.message)}</p>`; return; }
  $("#ch-meta").textContent = r.errors.length ? r.errors.join("; ") : "Reddit (ApeWisdom) and StockTwits";
  const CAU = { high: "High caution", medium: "Caution", low: "Fewer warnings" };
  $("#ch-out").innerHTML = r.rows.map((x) => `<div class="ch-item ch-${esc(x.caution)}"><div class="row">
      <button type="button" class="linkish" data-open="${esc(x.symbol)}">${tick(x.symbol)}</button> <span class="muted">${esc((x.name || "").slice(0, 30))}</span>
      <span class="tk-level">${esc(CAU[x.caution])}</span>${sizeBtn(x.symbol, "chatter")}</div>
      <div>${x.reddit ? `${x.reddit} Reddit mentions${x.rising ? ` (${x.rising}× yesterday)` : ""}` : ""}${x.stocktwits ? `${x.reddit ? " · " : ""}trending on StockTwits${x.reason ? ": " + esc(x.reason.slice(0, 140)) : ""}` : ""}</div>
      ${x.warnings.length ? `<div class="muted">${esc(x.warnings.join(" · "))}</div>` : ""}</div>`).join("") || `<p class="muted">No chatter data right now.</p>`;
  document.querySelectorAll("#ch-out [data-open]").forEach((el) => el.addEventListener("click", () => openSymbol(el.dataset.open)));
}
$("#th-load").addEventListener("click", async () => {
  $("#th-out").innerHTML = `<p class="muted">Searching SEC fund filings…</p>`;
  try {
    const r = await api("/api/themes");
    const LV = { crowded: "Crowded", busy: "Busy", quiet: "Quiet" };
    $("#th-out").innerHTML = `<div class="table-scroll"><table class="data"><thead><tr><th>Theme</th><th>New fund filings, 6 mo</th><th>6 mo before</th><th></th><th>Well-known names</th></tr></thead><tbody>
      ${r.themes.map((t) => `<tr><td>${esc(t.theme)}</td><td><b>${t.last_6m}</b></td><td>${t.prior_6m}</td><td class="${t.level === "crowded" ? "down" : ""}">${LV[t.level]}${t.ratio ? ` (${t.ratio}×)` : ""}</td>
        <td>${t.tickers.map((x) => `<button type="button" class="linkish" data-open="${esc(x)}">${esc(x)}</button>`).join(" ")}</td></tr>`).join("")}</tbody></table></div>`;
    document.querySelectorAll("#th-out [data-open]").forEach((el) => el.addEventListener("click", () => openSymbol(el.dataset.open)));
  } catch (err) { $("#th-out").innerHTML = `<p class="down">${esc(err.message)}</p>`; }
});

// ---------------------------------------------------------------- money flow
const bn = (x) => x == null ? "—" : "$" + (x / 1e9).toFixed(x >= 1e11 ? 0 : 1) + "B";
async function loadMoneyFlow() {
  let r;
  try { r = await api("/api/moneyflow"); } catch (err) { $("#mf-out").innerHTML = `<p class="down">${esc(err.message)}</p>`; return; }
  $("#mf-out").innerHTML = r.waves.map((w) => `<div class="mf-wave"><div class="row"><b>${esc(w.wave)}</b>
      <span>${bn(w.total)} over the last year${w.growth != null ? ` <span class="${cls(w.growth)}">${fmtPct(w.growth * 100, 0)}</span> on the year before` : ""}</span></div>
      <div class="muted">${w.spenders.map((s) => `${esc(s.symbol)} ${bn(s.capex)}${s.growth != null ? ` (${fmtPct(s.growth * 100, 0)})` : ""}`).join(" · ")}</div>
      <div class="mf-cats">${w.suppliers.map((c) => `<div><span class="muted">${esc(c.category)}:</span> ${c.companies.map((x) => `<button type="button" class="linkish mf-co${x.priced_in === "Already ran hard" ? " hot" : ""}" data-open="${esc(x.symbol)}"
        title="${esc(x.priced_in || "not on the screen")}${x.score != null ? ", grade " + Math.round(x.score) : ""}">${esc(x.symbol)}${x.priced_in === "Already ran hard" ? " ▲" : ""}</button>`).join(" ")}</div>`).join("")}</div></div>`).join("")
    + `<p class="muted">▲ = already ran hard (top 10% of 12-month returns, or 30%+ above its 200-day average). Supplier lists are hand-picked well-known names, not recommendations.
      A spender shown as — doesn't report capital spending under the standard SEC tags (NextEra uses its own), so it isn't in the total.</p>`;
  $("#lag-out").innerHTML = r.lagging.map((x) => `<div class="mv-row"><div class="row"><button type="button" class="linkish" data-open="${esc(x.symbol)}">${tick(x.symbol)}</button>
      <span class="muted">${fmtMoney(x.cap / 1e9, 1)}B${x.score != null ? ` · grade ${Math.round(x.score)}` : ""}</span>${sizeBtn(x.symbol, "supplier")}</div><div>${esc(x.why)}</div></div>`).join("")
    || `<p class="muted">No big customer moved 10%+ this month without its small suppliers following.</p>`;
  document.querySelectorAll("#tab-moneyflow [data-open]").forEach((el) => el.addEventListener("click", () => openSymbol(el.dataset.open)));
}

// ---------------------------------------------------------------- economy
async function loadEconomy() {
  let r;
  try { r = await api("/api/macro"); } catch (err) { $("#ec-out").innerHTML = `<p class="down">${esc(err.message)}</p>`; return; }
  $("#ec-meta").textContent = `as of ${r.as_of}`;
  $("#ec-pace").innerHTML = `<span class="ec-${esc(r.pace.level)}">${esc(r.pace.text)}</span>`;
  const fmt = (g, v) => v == null ? "—" : g.id === "ICSA" ? Math.round(v / 1000) + "k" : g.id === "NEWORDER" ? "$" + (v / 1000).toFixed(1) + "B" : g.unit === "$" ? "$" + v.toFixed(2) : v.toFixed(2) + (g.unit === "%" ? "%" : "");
  $("#ec-out").innerHTML = r.gauges.map((g) => `<div class="ec-g ec-${esc(g.state)}"><div class="muted">${esc(g.name)}</div><b>${fmt(g, g.value)}</b>
      <div class="muted">3 months ago ${fmt(g, g.three_months_ago)} · a year ago ${fmt(g, g.year_ago)}</div>${g.note ? `<div>${esc(g.note)}</div>` : ""}</div>`).join("");
}

// ---------------------------------------------------------------- symbol page: priced in?
async function loadPricedIn(sym) {
  const card = $("#sym-pi");
  card.hidden = true;
  if (/-USD$/.test(sym)) return;
  try {
    const r = await api("/api/priced-in/" + encodeURIComponent(sym));
    if (sym !== symState.sym) return;
    const s = r.screen, v = r.valuation;
    $("#pi-meta").textContent = r.verdict;
    $("#pi-out").innerHTML = (r.flags.length ? `<ul class="hp-lines">${r.flags.map((f) => `<li class="${f.level >= 2 ? "down" : ""}">${esc(f.text)}</li>`).join("")}</ul>` : `<p>No priced-in warnings.</p>`)
      + (s ? `<p class="muted">Screen grade <b>${s.score != null ? Math.round(s.score) : "—"}</b>/100 in ${esc(s.sector)}: quality ${s.quality ?? "—"}, value ${s.value ?? "—"}, momentum ${s.momentum ?? "—"}; 12-month return better than ${s.return_12m_pct ?? "—"}% of listed companies.</p>` : "")
      + (v && v.pe ? `<p class="muted">P/E ${v.pe} against its own last ${v.quarters} quarter-ends: median ${v.median}, range ${v.low}-${v.high}.</p>` : v && v.note ? `<p class="muted">${esc(v.note)}</p>` : "")
      + (r.themes.length ? `<p class="muted">Theme: ${esc(r.themes.join(", "))}.</p>` : "")
      + `<p class="muted" id="pi-gov"></p>`;
    card.hidden = false;
    api("/api/contracts/" + encodeURIComponent(sym)).then((c) => { if (sym === symState.sym && c.amount) $("#pi-gov").textContent = c.text + (c.material ? " A material share of the business." : ""); }).catch(() => {});
  } catch { /* funds and anything the screen doesn't cover: no card */ }
}
