// Plumbline public site. Renders data.json (a snapshot rebuilt by GitHub Actions) and streams
// crypto prices straight from Coinbase's public WebSocket, so crypto is live even between builds.
"use strict";

const $ = (sel) => document.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const LIVE_CARDS = ["BTC-USD", "ETH-USD", "SOL-USD", "SPY", "QQQ"];
const COMPONENT_NAMES = { trend: "Trend", momentum: "Momentum", smart_money: "Smart money", insider: "Insiders", news: "News" };
const REFRESH_MS = 5 * 60 * 1000;

let data = null;
const live = {}; // symbol -> {price, change_pct, time}

function fmtPrice(p) {
  if (p == null) return "—";
  const digits = p >= 1000 ? 0 : p >= 1 ? 2 : 4;
  return "$" + p.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}
function fmtPct(p) {
  if (p == null) return "—";
  return `${p > 0 ? "▲ +" : p < 0 ? "▼ " : ""}${p.toFixed(2)}%`;
}
function money(x) {
  return x >= 1e6 ? `$${(x / 1e6).toFixed(1)}M` : `$${Math.round(x).toLocaleString("en-US")}`;
}
function fmtTime(iso) {
  const d = new Date(iso);
  return isNaN(d) ? "" : d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", timeZoneName: "short" });
}
function fmtDate(s) {
  const d = new Date(s + "T12:00:00Z");
  return isNaN(d) ? s : d.toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
}
function row(sym) { return data?.universe.find((r) => r.symbol === sym); }
function current(sym) {
  const r = row(sym) || {};
  const l = live[sym];
  return l ? { price: l.price, change_pct: l.change_pct, live: true, time: l.time }
           : { price: r.price, change_pct: r.change_pct, live: false, time: r.quote_as_of };
}

// ------------------------------------------------------------------ rendering
function renderCards() {
  $("#live-cards").innerHTML = LIVE_CARDS.map((sym) => {
    const c = current(sym);
    const crypto = sym.endsWith("-USD");
    const when = c.live ? "live" : crypto ? "waiting for stream" : c.time ? `snapshot ${fmtTime(c.time)}` : "snapshot";
    return `<div class="card" id="card-${esc(sym)}">
      <div class="sym"><span>${esc(sym.replace("-USD", ""))}</span><span class="${c.change_pct > 0 ? "up" : c.change_pct < 0 ? "down" : ""}">${fmtPct(c.change_pct)}</span></div>
      <div class="price">${fmtPrice(c.price)}</div>
      <div class="meta">${esc(when)}${crypto ? " · 24h change" : " · day change"}</div>
    </div>`;
  }).join("");
}

function scoreBar(score) {
  if (score == null) return `<span class="muted">—</span>`;
  const w = Math.min(Math.abs(score), 100) / 2; // percent of the full width
  const left = score >= 0 ? 50 : 50 - w;
  const color = score >= 0 ? "var(--pos)" : "var(--neg)";
  const labelPos = score >= 0 ? `left:calc(${50 + w}% + 4px)` : `right:calc(${50 + w}% + 4px)`;
  return `<div class="bar" role="img" aria-label="Score ${score}"><span style="left:${left}%;width:${w}%;background:${color}"></span><b style="${labelPos}">${score > 0 ? "+" : ""}${score.toFixed(0)}</b></div>`;
}

function drivers(components, sign) {
  if (!components) return "";
  const list = Object.entries(components).filter(([, v]) => v != null && Math.sign(v) === sign && Math.abs(v) >= 10)
    .sort((a, b) => sign * (b[1] - a[1])).slice(0, 2)
    .map(([k, v]) => `${COMPONENT_NAMES[k] || k} ${v > 0 ? "+" : ""}${v.toFixed(0)}`);
  return list.join(", ") || `<span class="muted">—</span>`;
}

function renderScores() {
  $("#score-rows").innerHTML = data.universe.map((r) => {
    const c = current(r.symbol);
    return `<tr>
      <td class="sym-cell">${esc(r.symbol)}</td>
      <td class="num" data-price="${esc(r.symbol)}">${fmtPrice(c.price)}</td>
      <td class="num ${c.change_pct > 0 ? "up" : c.change_pct < 0 ? "down" : ""}" data-change="${esc(r.symbol)}">${fmtPct(c.change_pct)}</td>
      <td class="bar-col">${scoreBar(r.score)}</td>
      <td class="nowrap">${esc(r.label || "No score yet")}</td>
      <td class="drivers">${drivers(r.components, 1)}</td>
      <td class="drivers">${drivers(r.components, -1)}</td>
      <td class="num">${r.coverage != null ? Math.round(r.coverage * 100) + "%" : "—"}</td>
    </tr>`;
  }).join("");
}

function renderClusters() {
  const rules = data.alert_rules;
  $("#ins-rules").textContent = `Three or more officers or directors buying their own company's shares on the open market ` +
    `(at least ${money(rules.min_buy)} each, ${money(rules.min_total)} in total) within ${rules.window_days} days. ` +
    `"New" means the newest filing is under ${rules.max_age_days} days old and an alert issue was opened. ` +
    `Research finds this is the insider signal that carries information, mostly at smaller companies, which are also more volatile.`;
  const labels = { new: "New", alerted: "Alerted", "one-day": "One day only", older: "Older" };
  const tips = { "one-day": "Every purchase on the same day: often a compensation program, not independent decisions",
                 older: "Newest filing is more than a week old (two weeks if it already alerted)", alerted: "An alert issue was opened in the last 30 days" };
  const card = (c) => `<article class="cluster">
      <header><span class="tick">${esc(c.symbol || "—")}</span><span class="co" title="${esc(c.company)}">${esc(c.company)}</span>
        <span class="chip ${c.status === "new" ? "new" : ""}" title="${esc(tips[c.status] || "")}">${labels[c.status]}</span></header>
      <div class="facts"><b>${c.insiders} insiders</b> · ${money(c.total_value)} · ${fmtDate(c.first_trade)} → ${fmtDate(c.last_trade)} · ${c.trade_days} trading day${c.trade_days === 1 ? "" : "s"}</div>
      <details><summary>Purchases and filings</summary>
        <div class="scroll"><table><thead><tr><th>Date</th><th>Insider</th><th>Role</th><th class="num">Value</th><th></th></tr></thead><tbody>
        ${c.buys.map((b) => `<tr><td>${fmtDate(b.date)}</td><td>${esc(b.insider)}</td><td>${esc(b.role)}</td><td class="num">${money(b.value)}</td><td><a href="${esc(b.url)}" rel="noopener">filing</a></td></tr>`).join("")}
        </tbody></table></div>
      </details>
    </article>`;
  // New and alerted clusters get cards; one-day batches and older buying go in a compact list.
  const main = data.clusters.filter((c) => c.status === "new" || c.status === "alerted");
  const rest = data.clusters.filter((c) => !main.includes(c));
  $("#clusters").innerHTML = main.map(card).join("") || `<p class="empty">No clusters have alerted in the last 30 days.</p>`;
  $("#clusters-rest").innerHTML = rest.length ? `<details><summary>${rest.length} more active clusters that didn't alert</summary>
      <div class="panel scroll"><table class="plain"><thead><tr><th>Ticker</th><th>Company</th><th>Why not</th><th class="num">Insiders</th><th class="num">Total</th><th>Buys</th></tr></thead><tbody>
      ${rest.map((c) => `<tr><td>${esc(c.symbol)}</td><td>${esc(c.company)}</td><td>${labels[c.status]}</td><td class="num">${c.insiders}</td><td class="num">${money(c.total_value)}</td><td>${fmtDate(c.first_trade)} → ${fmtDate(c.last_trade)}</td></tr>`).join("")}
      </tbody></table></div></details>` : "";
}

function renderMoves() {
  const big = data.big_buys || [], stakes = data.stakes || [];
  $("#big-buys").innerHTML = big.length ? `<ul class="move-list">${big.map((b) => `<li>
      <span class="what">${esc(b.symbol)} <span class="muted">${esc(b.company)}</span></span>
      <span class="amt">${money(b.value)}</span>
      <span class="sub">${esc(b.insider)}, ${esc(b.role)} · ${b.trade_dates.map(fmtDate).join(", ")} · <a href="${esc(b.url)}" rel="noopener">filing</a></span>
    </li>`).join("")}</ul>` : `<p class="empty">None in the last 14 days.</p>`;
  $("#stakes").innerHTML = stakes.length ? `<ul class="move-list">${stakes.map((st) => `<li>
      <span class="what">${esc(st.company)}</span>
      <span class="amt">${fmtDate(st.filed)}</span>
      <span class="sub">${esc(st.filer)}${st.tracked ? `<span class="chip tracked">${esc(st.tracked)}</span>` : ""} · ${esc(st.form)} · <a href="${esc(st.url)}" rel="noopener">filing</a></span>
    </li>`).join("")}</ul>` : `<p class="empty">No new 13D filings seen yet. The watcher records them as they arrive.</p>`;
}

function renderRecord() {
  const t = data.track_record;
  if (!t) { $("#verdict").textContent = "No journal entries yet."; return; }
  $("#verdict").textContent = t.verdict;
  const ic = (h) => h.ic == null ? "—" : `${h.ic >= 0 ? "+" : ""}${h.ic.toFixed(3)}${h.ic_se ? ` ± ${(2 * h.ic_se).toFixed(3)}` : ""}`;
  $("#record-table").innerHTML = `<thead><tr><th>Horizon</th><th class="num">Scored observations</th><th class="num">Independent</th><th class="num">IC (± 2 errors)</th></tr></thead><tbody>` +
    t.horizons.map((h) => `<tr><td>${h.horizon_days} trading days</td><td class="num">${h.n}</td><td class="num">~${h.effective_n}</td><td class="num">${ic(h)}</td></tr>`).join("") +
    `</tbody>`;
}

function renderBacktest() {
  const b = data.backtest;
  if (!b) { $("#backtest").hidden = true; return; }
  $("#bt-method").textContent = `${b.universe}, ${b.period}. ${b.method}`;
  const n = (x, d = 3) => x == null ? "≈ 0" : `${x > 0 ? "+" : x < 0 ? "−" : ""}${Math.abs(x).toFixed(d)}`;
  $("#bt-table").innerHTML = `<thead><tr><th>Component</th><th class="num">IC, 1 month</th><th class="num">IC, 3 months</th><th class="num">t, 3 months</th><th>Reading</th></tr></thead><tbody>` +
    b.components.map((c) => `<tr><td>${esc(c.name)}</td><td class="num">${n(c.ic_1m)}</td><td class="num">${n(c.ic_3m)}</td><td class="num">${Math.abs(c.t_3m) >= 2 ? "<b>" : ""}${n(c.t_3m, 2)}${Math.abs(c.t_3m) >= 2 ? "</b>" : ""}</td><td>${esc(c.reading)}</td></tr>`).join("") +
    `</tbody>`;
  $("#bt-note").textContent = `${b.copy_billionaires} Run of ${fmtDate(b.run_date)}.`;
}

function render() {
  $("#snapshot-note").textContent = `Snapshot built ${fmtTime(data.generated_at)}.`;
  $("#generated").textContent = `Data built ${fmtTime(data.generated_at)}`;
  renderCards(); renderScores(); renderClusters(); renderMoves(); renderRecord(); renderBacktest();
}

async function load() {
  try {
    const res = await fetch(`data.json?t=${Date.now()}`, { cache: "no-store" });
    if (!res.ok) throw new Error(res.status);
    data = await res.json();
    render();
  } catch (e) {
    if (!data) $("#snapshot-note").textContent = "The data snapshot could not be loaded.";
  }
}

// ------------------------------------------------------------------ live crypto
const CRYPTO = ["BTC-USD", "ETH-USD", "SOL-USD"];
let ws, retry = 0, retryTimer = null, lastPaint = 0, pending = false;

function setStatus(state, text) {
  $("#live-dot").className = "dot " + (state === "live" ? "live" : state === "off" ? "off" : "");
  $("#live-text").textContent = text;
}

function flash(el) {
  if (!el || matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  el.classList.add("flash");
  setTimeout(() => el.classList.remove("flash"), 300);
}

function paintLive() {
  pending = false; lastPaint = Date.now();
  for (const sym of CRYPTO) {
    const l = live[sym];
    if (!l || !data) continue;
    const card = document.getElementById("card-" + sym);
    if (card && card.querySelector(".price").textContent !== fmtPrice(l.price)) {
      card.querySelector(".price").textContent = fmtPrice(l.price);
      const ch = card.querySelector(".sym span:last-child");
      ch.textContent = fmtPct(l.change_pct);
      ch.className = l.change_pct > 0 ? "up" : l.change_pct < 0 ? "down" : "";
      card.querySelector(".meta").textContent = "live · 24h change";
      flash(card);
    }
    const cell = document.querySelector(`[data-price="${sym}"]`);
    if (cell && cell.textContent !== fmtPrice(l.price)) { cell.textContent = fmtPrice(l.price); flash(cell); }
    const chg = document.querySelector(`[data-change="${sym}"]`);
    if (chg) { chg.textContent = fmtPct(l.change_pct); chg.className = "num " + (l.change_pct > 0 ? "up" : l.change_pct < 0 ? "down" : ""); }
  }
}

function connect() {
  clearTimeout(retryTimer);
  setStatus("connecting", "Connecting…");
  try { ws = new WebSocket("wss://ws-feed.exchange.coinbase.com"); } catch { return scheduleRetry(); }
  ws.onopen = () => {
    retry = 0;
    ws.send(JSON.stringify({ type: "subscribe", product_ids: CRYPTO, channels: ["ticker"] }));
  };
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.type !== "ticker" || !m.price) return;
    const price = +m.price, open = +m.open_24h;
    live[m.product_id] = { price, change_pct: open ? (price / open - 1) * 100 : null, time: m.time };
    setStatus("live", "Crypto live");
    // Paint at most ~3 times a second: BTC can tick dozens of times per second.
    if (!pending) { pending = true; setTimeout(paintLive, Math.max(0, 330 - (Date.now() - lastPaint))); }
  };
  ws.onclose = scheduleRetry;
  ws.onerror = () => ws.close();
}

function scheduleRetry() {
  setStatus("off", "Crypto stream offline · showing snapshot");
  clearTimeout(retryTimer);
  retryTimer = setTimeout(connect, Math.min(30000, 1000 * 2 ** retry++));
}

document.addEventListener("visibilitychange", () => {
  // Reconnect promptly when the tab comes back; browsers throttle background sockets.
  if (!document.hidden && (!ws || ws.readyState > 1)) { retry = 0; connect(); }
});

load().then(connect);
setInterval(load, REFRESH_MS);
