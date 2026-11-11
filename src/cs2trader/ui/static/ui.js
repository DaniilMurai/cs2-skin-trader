"use strict";
/* Shared helpers: escaping, formatting, fetch, glossary tooltips, charts. Loaded before pages.js. */

const TOKEN = document.querySelector('meta[name="cs2-token"]').content;
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
// Server sentences mark commands with backticks; everything else stays escaped text.
const rich = (s) => esc(s).replace(/`([^`]+)`/g, "<code>$1</code>");

const MINUS = "−";
function fmtNum(v, d = 0) {
  if (v == null || Number.isNaN(Number(v))) return "–";
  const n = Number(v);
  return (n < 0 ? MINUS : "") + Math.abs(n).toLocaleString("en-US", {minimumFractionDigits: d, maximumFractionDigits: d});
}
function fmtMoney(v, cents) {
  if (v == null || Number.isNaN(Number(v))) return "–";
  const n = Number(v), a = Math.abs(n);
  const d = cents ?? (a >= 100 || Number.isInteger(a) ? 0 : 2);
  return (n < 0 ? MINUS : "") + "$" + a.toLocaleString("en-US", {minimumFractionDigits: d, maximumFractionDigits: d});
}
function fmtPct(v, signed = true, d = 1) {
  if (v == null || Number.isNaN(Number(v))) return "–";
  const n = Number((Number(v) * 100).toFixed(d));
  const sign = n < 0 ? MINUS : n > 0 && signed ? "+" : "";
  return sign + Math.abs(n).toLocaleString("en-US", {minimumFractionDigits: d, maximumFractionDigits: d}) + " %";
}
const DATE_FMT = new Intl.DateTimeFormat("en-US", {month: "short", day: "numeric", year: "numeric", timeZone: "UTC"});
const MONTH_FMT = new Intl.DateTimeFormat("en-US", {month: "short", year: "numeric", timeZone: "UTC"});
const TIME_FMT = new Intl.DateTimeFormat("en-US", {month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hourCycle: "h23", timeZone: "UTC"});
// Dates are days ("2026-11-10"); times come from DuckDB as "2026-11-10 14:00:00" (UTC, no zone) or with "+01".
function parseT(s) {
  s = String(s);
  if (s.length <= 10) return Date.parse(s + "T00:00:00Z");
  s = s.replace(" ", "T");
  if (/[+-]\d\d$/.test(s)) s += ":00";
  else if (!/(Z|[+-]\d\d:?\d\d)$/.test(s)) s += "Z";
  return Date.parse(s);
}
const fmtDate = (s) => (s ? DATE_FMT.format(new Date(parseT(s))) : "–");
const fmtTime = (s) => (s ? TIME_FMT.format(new Date(parseT(s))) + " UTC" : "–");
const isoDay = (ms) => new Date(ms).toISOString().slice(0, 10);
const signClass = (v) => (v > 0 ? "good" : v < 0 ? "bad" : "");

async function get(url) {
  const r = await fetch(url);
  return r.json();
}
async function post(url, body) {
  const r = await fetch(url, {method: "POST", headers: {"Content-Type": "application/json", "X-CS2-Token": TOKEN},
    body: JSON.stringify(body)});
  return {status: r.status, body: await r.json()};
}

/* ---------- glossary ---------- */
const GLOSSARY = {
  backtest: ["Backtest", "Replaying a strategy on past prices to see what it would have earned. No real money moves."],
  strategy: ["Strategy", "A fixed set of rules for what to buy and when to sell. The computer follows it mechanically, day by day."],
  verdict: ["Verdict", "The pass / fail decision for one strategy with one starting amount. “Holds up” needs all five checks to pass in the final test period; otherwise it “fails” and the first failed check is named."],
  benchmark: ["Benchmark (yardstick)", "A do-almost-nothing alternative a strategy must beat to be worth the effort. Here there are two: holding cash, and buying a basket of skins once and holding it."],
  cash: ["Hold cash", "The simplest yardstick: keep the money and do nothing. It shows a small loss because withdrawing money from market.csgo costs a fee."],
  bh: ["Buy and hold", "The second yardstick: on day one, spread the money over up to 25 skins and never sell. It shows what the market itself did."],
  holdout: ["Final test period (holdout)", "The most recent stretch of history (from October 1, 2025 by default), set aside and never used to tune any strategy. Because the strategies have never “seen” it, it is the fairest test, and the verdict is judged here."],
  walk_forward: ["Walk-forward test", "A way to test without cheating: pick the best settings using only the past year, test them on the next three unseen months, then move forward and repeat across the whole history."],
  regime: ["Market mood (regime)", "A label for each day: rising, falling or flat, from how much the market index changed over the last 30 days. Results are also shown per mood, because a strategy may work only in one of them."],
  index: ["Market index", "One line for the whole market: each day it moves by the typical (median) price change of all actively traded skins. It starts at 100, so 150 means the typical skin is up 50 % since the start."],
  drawdown: ["Worst dip (max drawdown)", "The largest fall from a high point to the lowest point after it. A worst dip of −40 % means that at some moment you would have been down 40 % from your best value so far."],
  net_return: ["Result after fees (net return)", "How much the starting amount grew or shrank after every fee: +10 % means $100 became $110."],
  cash_out: ["Cash-out value", "What you would actually receive if you sold everything and withdrew the money from market.csgo, after the sale fee and the withdrawal fee. This is the main way profit is counted here."],
  steam_wallet: ["Steam wallet value", "The same result counted as Steam wallet credit instead of withdrawn cash. Steam credit can only be spent on Steam, so it is shown for comparison only."],
  units: ["Two ways to count profit", "Cash-out = money you can withdraw from market.csgo (the main one). Steam wallet = credit on Steam that cannot be withdrawn. The same trades give two different numbers."],
  fees: ["Fees", "market.csgo takes about 5 % of every sale and about 5 % when money is withdrawn. Both are subtracted in every result."],
  spread: ["Spread penalty", "The recorded price is not the price you get: buying costs a bit more, selling a bit less. We assume 3 % each way."],
  cost_stress: ["Cost stress test", "The same test re-run with every fee 50 % higher and the spread penalty doubled. A strategy that only wins under optimistic costs fails here."],
  sharpe: ["Sharpe ratio", "Return divided by how bumpy the ride was. Above 1 is good, around 0 means the gains were not worth the swings, negative means it lost."],
  deflated_sharpe: ["Luck check (deflated Sharpe)", "If you try 100 variants of a strategy, the best one looks good by chance alone. This score subtracts that expected luck from the walk-forward Sharpe ratio; it must stay above zero."],
  variants: ["Variants tried", "How many different settings of the same strategy were tested. The more that are tried, the more likely the best one is just luck."],
  universe: ["Tradable skins (universe)", "The skins a strategy may buy on a given day: those with enough price history, enough daily sales and a high enough price."],
  lock: ["Trade lock", "A skin bought on market.csgo cannot be sold for 7 days. Every strategy has to live with that."],
  participation: ["Volume limit", "We assume we can buy or sell at most 20 % of what was traded of an item that day; the rest of an order is cancelled."],
  run_id: ["Run ID", "A fingerprint of one backtest: strategy, settings, dates, costs and data. The same inputs always give the same ID and the same numbers."],
  data_version: ["Data version", "A fingerprint of the price data. If it changes, the data changed, and old results are no longer comparable with new ones."],
  results_hash: ["Results fingerprint", "A short code computed from the whole results table. If two reports show the same code, their numbers are identical."],
  snapshot: ["Snapshot", "One hourly copy of every item's price on market.csgo. market.csgo publishes no history, so we have to record it ourselves."],
  collector: ["Collector", "The scheduled job that saves a market.csgo snapshot every hour. It runs on GitHub's servers, with this Mac as a second source."],
  gap: ["Gap", "Hours in a row with no snapshot. Missed hours can never be recovered, because market.csgo only shows the current prices."],
  item_share: ["Item coverage", "How many items a snapshot contains compared with the largest snapshot. A much smaller one means the download was cut short."],
};

// Default wording is the glossary title in lower case, so it reads naturally mid-sentence.
const lowerFirst = (t) => (/^[A-Z][a-z]/.test(t) ? t[0].toLowerCase() + t.slice(1) : t);
const term = (key, text) => {
  const t = text || lowerFirst(GLOSSARY[key][0]);
  return `<button type="button" class="term" data-term="${esc(key)}" aria-label="${esc(t + ": what does it mean?")}">${esc(t)}</button>`;
};
const help = (key) =>
  `<button type="button" class="q" data-term="${esc(key)}" aria-label="${esc("What is " + GLOSSARY[key][0] + "?")}">?</button>`;
const helpText = (text) => `<button type="button" class="q" data-tip="${esc(text)}" aria-label="More information">?</button>`;

(function tooltips() {
  const pop = document.createElement("div");
  pop.id = "pop"; pop.setAttribute("role", "tooltip"); pop.hidden = true;
  document.body.appendChild(pop);
  let owner = null, pinned = false;
  const hide = () => { pop.hidden = true; if (owner) owner.removeAttribute("aria-describedby"); owner = null; pinned = false; };
  const show = (el) => {
    const g = GLOSSARY[el.dataset.term];
    if (!g && !el.dataset.tip) return;
    pop.innerHTML = g ? `<b>${esc(g[0])}</b><span>${esc(g[1])}</span>` : `<span>${esc(el.dataset.tip)}</span>`;
    pop.hidden = false; owner = el; el.setAttribute("aria-describedby", "pop");
    const r = el.getBoundingClientRect(), w = pop.offsetWidth, h = pop.offsetHeight, vw = document.documentElement.clientWidth;
    const left = Math.min(Math.max(8, r.left + r.width / 2 - w / 2), vw - w - 8);
    const above = r.top - h - 8;
    pop.style.left = left + window.scrollX + "px";
    pop.style.top = (above > 8 ? above : r.bottom + 8) + window.scrollY + "px";
  };
  const target = (e) => (e.target.closest ? e.target.closest("[data-term],[data-tip]") : null);
  document.addEventListener("mouseover", (e) => { const t = target(e); if (t && !pinned) show(t); });
  document.addEventListener("mouseout", (e) => { if (target(e) && !pinned) hide(); });
  document.addEventListener("focusin", (e) => { const t = target(e); if (t) show(t); });
  document.addEventListener("focusout", (e) => { if (target(e)) hide(); });
  document.addEventListener("click", (e) => {
    const t = target(e);
    if (!t) return hide();
    e.preventDefault();
    if (pinned && owner === t) return hide();
    show(t); pinned = true;
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") hide(); });
  window.addEventListener("hashchange", hide);
})();

/* ---------- small building blocks ---------- */
const LIGHT_WORD = {green: "Good", amber: "Attention", red: "Problem", grey: "Unknown"};
const light = (status) => `<span class="light ${esc(status)}" role="img" aria-label="${LIGHT_WORD[status] || "Unknown"}"></span>`;
const REGIME_WORD = {rising: "Rising", falling: "Falling", flat: "Flat"};
const REGIME_ARROW = {rising: "↗", falling: "↘", flat: "→"};
const regimeBadge = (r) => `<span class="badge regime-${esc(r)}">${REGIME_ARROW[r] || ""} ${esc(REGIME_WORD[r] || r || "Unknown")}</span>`;

function notice(p, what) {
  if (!p || !p.state || p.state === "ok") return "";
  if (p.state === "busy") {
    return `<div class="banner amber" role="status"><b>The database is busy.</b> Another job (a backtest, a report or a download) is using it. Nothing is wrong: this page will work again when that job finishes. <button type="button" class="ghost" data-act="reload">Try again</button></div>`;
  }
  return `<div class="empty"><b>${esc(what || "Nothing to show yet")}</b><p>${rich(p.message || "")}</p></div>`;
}
const skeleton = (n = 3) => `<div class="skel-wrap" aria-busy="true" aria-label="Loading">${'<div class="skel"></div>'.repeat(n)}</div>`;
const pageHead = (title, how) => `<h1 tabindex="-1">${esc(title)}</h1>${how ? `<p class="how">${how}</p>` : ""}`;

function legendHtml(items) {
  return `<div class="legend">${items.map((s) => `<span class="lg"><i class="${s.swatch || "line"}" style="background:${s.color}"></i>${esc(s.name)}</span>`).join("")}</div>`;
}

/* ---------- charts (inline SVG, drawn at the container's real width so text stays readable) ---------- */
const cssVar = (v) => (v && v.startsWith("--") ? getComputedStyle(document.documentElement).getPropertyValue(v).trim() : v);
const resizeWatch = new ResizeObserver((entries) => {
  for (const e of entries) {
    const el = e.target;
    if (el._spec && Math.abs((el._w || 0) - el.clientWidth) > 4) drawChart(el);
  }
});
function chart(el, spec) {
  if (!el) return;
  el._spec = spec;
  drawChart(el);
  resizeWatch.observe(el);
}
function drawChart(el) {
  const spec = el._spec;
  const W = Math.max(el.clientWidth, 260), narrow = W < 520;
  el._w = el.clientWidth;
  const H = spec.height || (narrow ? 210 : 280);
  const m = {l: 58, r: 12, t: spec.yLabel ? 24 : 10, b: 26};
  const T = spec.log ? Math.log : (y) => y, TI = spec.log ? Math.exp : (y) => y;
  const fmt = spec.fmt || ((v) => fmtNum(v, 0));
  const series = spec.series.map((s) => ({...s, pts: s.points.filter((p) => p.y != null && (!spec.log || p.y > 0)).map((p) => ({x: parseT(p.x), y: T(p.y), raw: p.y}))}));
  const all = series.flatMap((s) => s.pts);
  if (!all.length) { el.innerHTML = `<p class="muted">No data to draw.</p>`; return; }
  let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
  for (const p of all) { x0 = Math.min(x0, p.x); x1 = Math.max(x1, p.x); y0 = Math.min(y0, p.y); y1 = Math.max(y1, p.y); }
  if (spec.baseline) { const b = T(spec.baseline.value); y0 = Math.min(y0, b); y1 = Math.max(y1, b); }
  if (spec.zero) { y0 = Math.min(y0, 0); y1 = Math.max(y1, 0); }
  if (y0 === y1) { y0 -= Math.abs(y0) * 0.05 || 1; y1 += Math.abs(y1) * 0.05 || 1; }
  const narrowTicks = narrow ? 3 : 5;
  let ticks;
  if (spec.log) {
    // Round values (1, 2, 5 x a power of ten) that fall inside the data range.
    const pad = (y1 - y0) * 0.05; y0 -= pad; y1 += pad;
    const lo = TI(y0), hi = TI(y1), cand = [];
    for (let e = Math.floor(Math.log10(lo)); e <= Math.ceil(Math.log10(hi)); e++) for (const k of [1, 1.5, 2, 3, 5, 7]) cand.push(k * 10 ** e);
    let inside = cand.filter((v) => v >= lo && v <= hi);
    while (inside.length > narrowTicks + 1) inside = inside.filter((_, i) => i % 2 === 0);
    ticks = inside.length >= 2 ? inside.map(T) : [y0, (y0 + y1) / 2, y1];
  } else {
    const raw = (y1 - y0) / narrowTicks, mag = 10 ** Math.floor(Math.log10(raw));
    const step = [1, 2, 2.5, 5, 10].map((k) => k * mag).find((v) => v >= raw);
    const lo = Math.floor(y0 / step) * step, hi = Math.ceil(y1 / step) * step;
    ticks = [];
    for (let v = lo; v <= hi + step / 2; v += step) ticks.push(Math.abs(v) < step / 1e6 ? 0 : v);
    y0 = lo; y1 = hi;
  }
  const sx = (x) => m.l + ((x - x0) / (x1 - x0 || 1)) * (W - m.l - m.r);
  const sy = (y) => H - m.b - ((y - y0) / (y1 - y0)) * (H - m.t - m.b);
  let svg = `<svg class="chart" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(spec.label || "Chart")}">`;
  for (const b of spec.bands || []) {
    const bx0 = sx(Math.max(parseT(b.from), x0)), bx1 = sx(Math.min(parseT(b.to) + 864e5, x1));
    if (bx1 > bx0) svg += `<rect x="${bx0.toFixed(1)}" y="${m.t}" width="${(bx1 - bx0).toFixed(1)}" height="${H - m.t - m.b}" fill="var(--band-${esc(b.kind)})"/>`;
  }
  if (spec.yLabel) svg += `<text x="0" y="11" class="axis-title">${esc(spec.yLabel)}</text>`;
  for (const y of ticks) {
    svg += `<line x1="${m.l}" x2="${W - m.r}" y1="${sy(y).toFixed(1)}" y2="${sy(y).toFixed(1)}" class="grid"/>` +
      `<text x="${m.l - 6}" y="${(sy(y) + 4).toFixed(1)}" text-anchor="end">${esc(fmt(TI(y)))}</text>`;
  }
  const nx = narrow ? 2 : 5, span = x1 - x0;
  for (let i = 0; i <= nx; i++) {
    const x = x0 + (span * i) / nx;
    const lab = span > 4 * 365 * 864e5 ? String(new Date(x).getUTCFullYear()) : span > 120 * 864e5 ? MONTH_FMT.format(new Date(x)) : DATE_FMT.format(new Date(x)).replace(/, \d{4}$/, "");
    svg += `<text x="${sx(x).toFixed(1)}" y="${H - 8}" text-anchor="${i === 0 ? "start" : i === nx ? "end" : "middle"}">${esc(lab)}</text>`;
  }
  if (spec.baseline) {
    const by = sy(T(spec.baseline.value)).toFixed(1);
    svg += `<line x1="${m.l}" x2="${W - m.r}" y1="${by}" y2="${by}" class="baseline"/>` +
      (spec.baseline.label ? `<text x="${W - m.r - 4}" y="${by - 5}" text-anchor="end" class="baseline-label">${esc(spec.baseline.label)}</text>` : "");
  }
  const hl = spec.highlight;
  const ordered = hl ? [...series.filter((s) => s.name !== hl), ...series.filter((s) => s.name === hl)] : series;
  for (const s of ordered) {
    const d = s.pts.map((p, j) => `${j ? "L" : "M"}${sx(p.x).toFixed(1)},${sy(p.y).toFixed(1)}`).join("");
    const faded = hl && s.name !== hl;
    svg += `<path d="${d}" fill="none" stroke="${esc(cssVar(s.color))}" stroke-width="${hl && !faded ? 2.4 : s.width || 1.6}" opacity="${faded ? 0.18 : s.opacity || 1}" stroke-linejoin="round"/>`;
  }
  svg += `<line class="cursor" y1="${m.t}" y2="${H - m.b}" x1="0" x2="0" visibility="hidden"/>` +
    `<rect class="hit" x="${m.l}" y="${m.t}" width="${W - m.l - m.r}" height="${H - m.t - m.b}" fill="transparent"/></svg>`;
  el.innerHTML = svg + `<div class="readout" aria-hidden="true">${esc(spec.hint || "Move over the chart to read exact values.")}</div>`;

  const cursor = $(".cursor", el), readout = $(".readout", el);
  const shown = hl ? series.filter((s) => s.name === hl) : series.slice(0, spec.maxReadout || 3);
  const nearest = (pts, x) => {
    let lo = 0, hi = pts.length - 1;
    while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (pts[mid].x < x) lo = mid; else hi = mid; }
    return Math.abs(pts[lo].x - x) <= Math.abs(pts[hi].x - x) ? pts[lo] : pts[hi];
  };
  $(".hit", el).addEventListener("pointermove", (e) => {
    const box = $("svg", el).getBoundingClientRect();
    const x = x0 + ((e.clientX - box.left - m.l) / (W - m.l - m.r)) * (x1 - x0);
    const first = shown[0] && shown[0].pts.length ? nearest(shown[0].pts, x) : null;
    if (!first) return;
    cursor.setAttribute("x1", sx(first.x)); cursor.setAttribute("x2", sx(first.x)); cursor.setAttribute("visibility", "visible");
    let text = `<b>${esc(fmtDate(isoDay(first.x)))}</b>`;
    for (const s of shown) if (s.pts.length) text += ` · ${esc(s.name)} ${esc((spec.fmtExact || fmt)(nearest(s.pts, first.x).raw))}`;
    if (spec.bandAt) { const b = spec.bandAt(first.x); if (b) text += ` · ${esc(b)}`; }
    readout.innerHTML = text;
  });
  $(".hit", el).addEventListener("pointerleave", () => cursor.setAttribute("visibility", "hidden"));
}

function regimeBands(points) {
  const bands = [];
  let cur = null;
  for (const p of points) {
    if (cur && cur.kind === p.regime) cur.to = p.t;
    else { cur = {kind: p.regime, from: p.t, to: p.t}; bands.push(cur); }
  }
  return bands.filter((b) => b.kind !== "flat");
}

/* Markdown-lite for the strategies' exact rules: bold, code, paragraphs. Input is escaped first. */
function specHtml(md) {
  return String(md || "").trim().split(/\n\s*\n/).map((p) =>
    `<p>${esc(p).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/`([^`]+)`/g, "<code>$1</code>")}</p>`).join("");
}

function paramValue(v, unit) {
  if (v === null || v === undefined) return "Off";
  if (unit === "share") return fmtPct(v, false, Number.isInteger(v * 100) ? 0 : 1);
  if (unit === "days") return `${v} days`;
  return String(v);
}
function paramsText(strategy, params) {
  const keys = Object.keys(params || {});
  if (!keys.length) return "standard settings";
  return keys.map((k) => {
    const p = strategy && strategy.params.find((x) => x.key === k);
    return `${p ? p.label : k}: ${paramValue(params[k], p ? p.unit : "")}`;
  }).join(" · ");
}
