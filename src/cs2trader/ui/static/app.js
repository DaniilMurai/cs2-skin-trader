"use strict";
const TOKEN = document.querySelector('meta[name="cs2-token"]').content;
const page = document.getElementById("page");
const COLORS = ["--s1", "--s2", "--good", "--warn", "--bad", "--accent"];
const css = (v) => (v.startsWith("--") ? getComputedStyle(document.documentElement).getPropertyValue(v).trim() : v);
// 14 category lines need more than six theme colours; hue steps keep neighbours distinguishable.
const hue = (i) => `hsl(${(i * 137) % 360} 65% 50%)`;

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const pct = (v) => (v == null ? "–" : (v * 100).toFixed(1) + "%");
const num = (v, d = 2) => (v == null ? "–" : Number(v).toLocaleString("en-US", {maximumFractionDigits: d}));
const money = (v) => "$" + num(v, 0);

async function get(url) {
  const r = await fetch(url);
  return r.json();
}
async function post(url, body) {
  const r = await fetch(url, {method: "POST", headers: {"Content-Type": "application/json", "X-CS2-Token": TOKEN},
    body: JSON.stringify(body)});
  return {status: r.status, body: await r.json()};
}
const notice = (p) => (p && p.state && p.state !== "ok" ? `<div class="msg">${esc(p.message || p.state)}</div>` : "");

/* ---------- inline SVG line chart ---------- */
function lineChart(series, opts = {}) {
  const W = 900, H = opts.height || 260, m = {l: 56, r: 10, t: 8, b: 22};
  const T = opts.log ? Math.log : (y) => y, TI = opts.log ? Math.exp : (y) => y;
  series = series.map((s) => ({...s, points: s.points.map((p) => ({...p, y: p.y == null ? null : T(p.y)}))}));
  const all = series.flatMap((s) => s.points);
  if (!all.length) return "<p class='muted'>No data.</p>";
  const xs = all.map((p) => Date.parse(p.x)), ys = all.map((p) => p.y).filter((y) => y != null);
  let x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
  if (opts.zero) { y0 = Math.min(y0, 0); y1 = Math.max(y1, 0); }
  if (y0 === y1) { y0 -= 1; y1 += 1; }
  const sx = (x) => m.l + ((x - x0) / (x1 - x0 || 1)) * (W - m.l - m.r);
  const sy = (y) => H - m.b - ((y - y0) / (y1 - y0)) * (H - m.t - m.b);
  let svg = `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img">`;
  for (const b of opts.bands || []) {
    const bx0 = sx(Math.max(Date.parse(b.from), x0)), bx1 = sx(Math.min(Date.parse(b.to), x1));
    svg += `<rect x="${bx0}" y="${m.t}" width="${Math.max(bx1 - bx0, 1)}" height="${H - m.t - m.b}" fill="var(--band-${b.kind})"/>`;
  }
  for (let i = 0; i <= 4; i++) {
    const y = y0 + ((y1 - y0) * i) / 4;
    svg += `<line x1="${m.l}" x2="${W - m.r}" y1="${sy(y)}" y2="${sy(y)}" stroke="var(--line)" stroke-width="0.5"/>` +
      `<text x="${m.l - 6}" y="${sy(y) + 4}" text-anchor="end">${esc(opts.fmt ? opts.fmt(TI(y)) : num(TI(y), 1))}</text>`;
  }
  for (let i = 0; i <= 5; i++) {
    const x = x0 + ((x1 - x0) * i) / 5;
    svg += `<text x="${sx(x)}" y="${H - 6}" text-anchor="middle">${new Date(x).toISOString().slice(0, 10)}</text>`;
  }
  series.forEach((s, i) => {
    const d = s.points.filter((p) => p.y != null).map((p, j) => `${j ? "L" : "M"}${sx(Date.parse(p.x)).toFixed(1)},${sy(p.y).toFixed(1)}`).join("");
    svg += `<path d="${d}" fill="none" stroke="${css(s.color || COLORS[i % COLORS.length])}" stroke-width="1.4"/>`;
  });
  return svg + "</svg>";
}
function legend(series) {
  return `<div class="legend">` + series.map((s, i) =>
    `<span><i style="background:${css(s.color || COLORS[i % COLORS.length])}"></i>${esc(s.name)}</span>`).join("") + "</div>";
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

/* ---------- pages ---------- */
const pages = {};

pages.verdict = async (day) => {
  const v = await get("/api/verdict" + (day ? `?day=${day}` : ""));
  if (v.state !== "ok") return `<h1>Verdict</h1>${notice(v)}`;
  const verdicts = v.verdicts
    ? `<div class="scroll"><table><tr><th>strategy</th><th>capital</th><th>status</th><th>first broken rule</th><th>deflated Sharpe</th><th>variants</th></tr>` +
      v.verdicts.map((x) => `<tr><td>${esc(x.strategy)}</td><td>${money(x.capital)}</td>
      <td><span class="badge ${x.status === "holds_up" ? "good" : "bad"}">${x.status === "holds_up" ? "holds up" : "fails"}</span></td>
      <td>${esc(x.first_broken_rule || "–")}</td><td>${num(x.deflated_sharpe, 3)}</td><td>${x.n_variants}</td></tr>`).join("") + "</table></div>"
    : `<div class="msg">${esc(v.verdicts_note)}</div>`;
  const caps = [...new Set(v.results.map((r) => r.capital))].sort((a, b) => a - b);
  const periods = [...new Set(v.results.map((r) => r.period))];
  const regimes = [...new Set(v.results.map((r) => r.regime))];
  window.__verdict = v;
  return `<h1>${esc(v.summary || "Verdict")}</h1>
  <div class="row muted">
    <label>Report <select id="vday">${v.reports.map((d) => `<option ${d === v.day ? "selected" : ""}>${d}</option>`).join("")}</select></label>
    <span>results hash <code>${v.results_hash}</code> ${v.hash_matches_report === true ? '<span class="good">matches the report</span>' :
      v.hash_matches_report === false ? '<span class="bad">DIFFERS from the report</span>' : ""}</span>
    <span>data version <code>${esc(v.data_version || "–")}</code>, last data day ${esc(v.last_day || "–")}</span>
    <a href="${v.report_html}" target="_blank">full HTML report</a></div>
  <h2>Verdicts</h2>${verdicts}
  <h2>Results</h2>
  <div class="row">
    <label>Capital <select id="fcap">${caps.map((c) => `<option value="${c}">${money(c)}</option>`).join("")}</select></label>
    <label>Period <select id="fper">${periods.map((p) => `<option>${p}</option>`).join("")}</select></label>
    <label>Regime <select id="freg">${regimes.map((p) => `<option>${p}</option>`).join("")}</select></label>
    <label>Unit <select id="funit"><option>cash</option><option>steam</option></select></label>
  </div>
  <div class="scroll" id="rtable"></div>
  <p class="muted">cash = money withdrawn from market.csgo after fees (main unit); steam = Steam wallet.
  Italic rows are the benchmarks (cash, buy-and-hold).</p>`;
};
pages.verdict.after = () => {
  const day = document.getElementById("vday");
  if (day) day.onchange = () => { location.hash = "#/verdict/" + day.value; };
  const draw = () => {
    const v = window.__verdict;
    if (!v) return;
    const f = (id) => document.getElementById(id).value;
    const rows = v.results.filter((r) => r.capital == f("fcap") && r.period === f("fper") && r.regime === f("freg") && r.unit === f("funit"));
    document.getElementById("rtable").innerHTML = rows.length
      ? `<table><tr><th>strategy</th><th>net return</th><th>max drawdown</th><th>trades</th><th>fees</th><th>Sharpe</th></tr>` +
        rows.map((r) => `<tr class="${["cash", "bh_universe", "bh_knives_gloves"].includes(r.strategy) ? "bench" : ""}"><td>${esc(r.strategy)}</td>
        <td class="${r.net_return >= 0 ? "good" : "bad"}">${pct(r.net_return)}</td><td>${pct(r.max_drawdown)}</td><td>${r.n_trades}</td>
        <td>${num(r.fees_paid)}</td><td>${num(r.sharpe, 2)}</td></tr>`).join("") + "</table>"
      : "<p class='muted'>No rows for this selection.</p>";
  };
  ["fcap", "fper", "freg", "funit"].forEach((id) => { const e = document.getElementById(id); if (e) e.onchange = draw; });
  draw();
};

pages.runs = async (runId) => {
  const [list, strat] = await Promise.all([get("/api/runs"), get("/api/strategies")]);
  window.__strategies = strat;
  const form = runForm(strat, list.runs && list.runs[0]);
  const rows = (list.runs || []).map((r) => `<tr class="click ${r.run_id === runId ? "sel" : ""}" data-id="${r.run_id}">
    <td>${esc(r.strategy)}</td><td>${money(r.capital)}</td><td>${r.start} → ${r.end}</td><td class="l"><code>${esc(JSON.stringify(r.params))}</code></td><td><code>${r.run_id}</code></td></tr>`).join("");
  let detail = "";
  if (runId) {
    const d = await get("/api/run/" + runId);
    if (d.error) detail = `<div class="msg">${esc(d.error)}</div>`;
    else {
      const s = d.meta.spec, eq = d.equity;
      const ser = [{name: "cash-out equity", color: "--s1", points: eq.map((e) => ({x: e.t, y: e.equity_cash}))},
        {name: "Steam-wallet equity", color: "--s2", points: eq.map((e) => ({x: e.t, y: e.equity_steam}))}];
      const dd = [{name: "cash drawdown", color: "--s1", points: eq.map((e) => ({x: e.t, y: e.drawdown_cash}))},
        {name: "Steam drawdown", color: "--s2", points: eq.map((e) => ({x: e.t, y: e.drawdown_steam}))}];
      const last = eq[eq.length - 1];
      detail = `<h2>${esc(s.strategy)} · ${money(s.capital)} · ${s.start} → ${s.end}</h2>
      <div class="row muted"><span>run <code>${runId}</code></span><span>data <code>${d.meta.data_version}</code></span>
      <span>params <code>${esc(JSON.stringify(s.params))}</code></span>
      <span>cash-out available ${esc(d.meta.cashout_available || "–")}</span></div>
      <div class="row"><div class="kpi">cash-out<b>${num(last.equity_cash)}</b>${pct(last.equity_cash / s.capital - 1)}</div>
      <div class="kpi">Steam wallet<b>${num(last.equity_steam)}</b>${pct(last.equity_steam / s.capital - 1)}</div>
      <div class="kpi">fees paid<b>${num(last.fees_paid)}</b></div><div class="kpi">fills<b>${d.n_filled}</b>of ${d.n_trades} orders</div></div>
      <div class="card"><b>Equity</b>${legend(ser)}${lineChart(ser, {})}</div>
      <div class="card"><b>Drawdown</b>${legend(dd)}${lineChart(dd, {height: 160, zero: true, fmt: pct})}</div>
      <h2>Trades ${d.n_trades > d.trades.length ? `(first ${d.trades.length} of ${d.n_trades})` : ""}</h2>
      <div class="scroll"><table><tr><th>day</th><th class="l">item</th><th>side</th><th>asked</th><th>filled</th><th>price</th><th>fee</th><th>reason</th></tr>` +
        d.trades.map((t) => `<tr><td>${t.t}</td><td class="l">${esc(t.market_hash_name)}</td><td>${t.side}</td><td>${t.qty_requested}</td>
        <td>${t.qty_filled}</td><td>${num(t.price)}</td><td>${num(t.fee)}</td><td>${esc(t.reason)}</td></tr>`).join("") + "</table></div>";
    }
  }
  return `<h1>Runs</h1>${form}<div id="job"></div>
  <h2>Past runs</h2>${notice(list)}<div class="scroll"><table><tr><th>strategy</th><th>capital</th><th>window</th><th>params</th><th>run id</th></tr>${rows}</table></div>${detail}`;
};
function runForm(strat, lastRun) {
  const today = new Date().toISOString().slice(0, 10);
  return `<div class="card"><b>Start a backtest</b> <span class="muted">(same code and settings as <code>cs2 backtest</code>)</span>
  <div class="row"><label>Strategy <select id="rf-strategy">${strat.strategies.map((s) => `<option>${s.name}</option>`).join("")}</select></label>
  <label>Capital <select id="rf-capital"><option>100</option><option selected>1000</option></select></label>
  <label>From <input id="rf-start" type="date" value="2024-01-01"></label><label>To <input id="rf-end" type="date" value="${today}"></label>
  <button id="rf-go">Run</button></div>
  <label>Params (JSON; keys allowed: <span id="rf-keys"></span>)<textarea id="rf-params">{}</textarea></label></div>`;
}
pages.runs.after = (runId) => {
  document.querySelectorAll("tr.click").forEach((tr) => (tr.onclick = () => (location.hash = "#/runs/" + tr.dataset.id)));
  const sel = document.getElementById("rf-strategy");
  if (!sel) return;
  const st = window.__strategies;
  const fill = () => {
    const s = st.strategies.find((x) => x.name === sel.value);
    document.getElementById("rf-keys").textContent = Object.keys(s.grid).join(", ") || "none";
    document.getElementById("rf-params").value = JSON.stringify(st.defaults[s.name] || {});
  };
  sel.onchange = fill; fill();
  document.getElementById("rf-go").onclick = async () => {
    let params;
    try { params = JSON.parse(document.getElementById("rf-params").value || "{}"); } catch (e) { return showJob({state: "failed", lines: ["Params are not valid JSON: " + e.message]}); }
    const r = await post("/api/jobs", {strategy: sel.value, params, capital: document.getElementById("rf-capital").value,
      start: document.getElementById("rf-start").value, end: document.getElementById("rf-end").value});
    if (r.status >= 400) return showJob({state: "failed", lines: [r.body.error || "failed"]});
    pollJob();
  };
  pollJob(true);
};
function showJob(j) {
  const el = document.getElementById("job");
  if (!el) return;
  if (j.state === "idle") { el.innerHTML = ""; return; }
  const cls = j.state === "done" ? "good" : j.state === "failed" ? "bad" : "warn";
  el.innerHTML = `<div class="card"><b class="${cls}">Job ${j.state}</b> ${j.strategy ? `${esc(j.strategy)} ${money(j.capital)}` : ""}
    ${j.state === "done" && j.run_id ? `<a href="#/runs/${j.run_id}">open run ${j.run_id}</a>` : ""}<pre>${esc((j.lines || []).join("\n"))}</pre></div>`;
}
let jobTimer = null;
async function pollJob(quiet) {
  clearTimeout(jobTimer);
  const j = await get("/api/jobs");
  if (j.state === "idle" && quiet) return;
  showJob(j);
  if (j.state === "running") jobTimer = setTimeout(() => pollJob(), 1500);
}

pages.market = async () => `<h1>Market</h1>
  <div class="card"><b>Market index and regimes</b> <span class="muted">(median daily return of liquid items; green = rising, red = falling)</span>
  <div id="idx">Loading…</div></div>
  <div class="card"><b>Category indices</b> <span class="muted">(last 2 years, 100 at the start; first load takes a while)</span>
  <div id="cats">Loading…</div></div>
  <div class="card"><b>One item</b><div class="row"><label>Search <input id="isearch" placeholder="AK-47 | Redline"></label></div>
  <div id="ilist" class="muted"></div><div id="item"></div></div>`;
pages.market.after = async () => {
  get("/api/market/index").then((d) => {
    const el = document.getElementById("idx");
    if (!el) return;
    if (d.state !== "ok") { el.innerHTML = notice(d); return; }
    const draw = (all) => {
      const pts = all ? d.points : d.points.filter((p) => p.t >= "2018-01-01");
      const ser = [{name: "index", color: "--s1", points: pts.map((p) => ({x: p.t, y: p.index_level}))}];
      el.innerHTML = lineChart(ser, {bands: regimeBands(pts), log: true}) +
        `<label style="flex-direction:row;align-items:center;gap:6px"><input type="checkbox" id="early" ${all ? "checked" : ""}> include 2013–2017 (few liquid items, unreliable)</label>
         <span class="muted">${pts.length} of ${d.n_days} days, log scale</span>`;
      document.getElementById("early").onchange = (e) => draw(e.target.checked);
    };
    draw(false);
  });
  get("/api/market/categories").then((d) => {
    const el = document.getElementById("cats");
    if (!el) return;
    if (d.state !== "ok") { el.innerHTML = notice(d); return; }
    const names = Object.keys(d.series).sort();
    const ser = names.map((n, i) => ({name: n, color: hue(i), points: d.series[n].map((p) => ({x: p.t, y: p.level}))}));
    el.innerHTML = legend(ser) + lineChart(ser, {log: true});
  });
  const box = document.getElementById("isearch");
  let t;
  box.oninput = () => { clearTimeout(t); t = setTimeout(async () => {
    if (box.value.length < 3) return;
    const r = await get("/api/market/search?q=" + encodeURIComponent(box.value));
    const list = document.getElementById("ilist");
    list.innerHTML = (r.names || []).map((n) => `<div><a href="#" data-n="${esc(n)}">${esc(n)}</a></div>`).join("") || "no match";
    list.querySelectorAll("a").forEach((a) => (a.onclick = async (e) => {
      e.preventDefault();
      const d = await get("/api/market/item?name=" + encodeURIComponent(a.dataset.n));
      document.getElementById("item").innerHTML = d.state !== "ok" ? notice(d) :
        `<b>${esc(d.name)}</b> (USD, Steam daily)` + lineChart([{name: "price", points: d.points.map((p) => ({x: p.t, y: p.price}))}], {});
    }));
  }, 250); };
};

pages.collector = async () => {
  const c = await get("/api/collector");
  if (c.state !== "ok") return `<h1>Collector</h1>${notice(c)}`;
  const fresh = c.age_hours == null ? "–" : c.age_hours + " h ago";
  const ok = c.snapshots >= c.target;
  const hours = c.per_hour.slice().reverse();
  const max = Math.max(...hours.map((h) => h.items));
  const bars = hours.map((h, i) => `<rect x="${i * (900 / hours.length)}" y="${120 - (h.items / max) * 110}" width="${Math.max(900 / hours.length - 1, 1)}"
    height="${(h.items / max) * 110}" fill="var(${h.host === "gha" ? "--s1" : "--s2"})"><title>${h.hour} · ${h.items} items · ${h.host}</title></rect>`).join("");
  return `<h1>Collector</h1>
  <div class="row"><div class="kpi">snapshots, last ${c.window_h} h<b class="${ok ? "good" : "warn"}">${c.snapshots}</b>target ${c.target}</div>
  <div class="kpi">smallest item share<b>${pct(c.min_item_share)}</b>of the largest snapshot</div>
  <div class="kpi">latest snapshot<b>${fresh}</b>${esc(c.latest_feed_time || "")}</div></div>
  <div class="card"><b>Items per hour</b> <span class="muted">(<span style="color:var(--s1)">■</span> GitHub, <span style="color:var(--s2)">■</span> Mac; newest on the right)</span>
  <svg class="chart" viewBox="0 0 900 130">${bars}</svg></div>
  <h2>Gaps longer than 2 h</h2>${c.gaps.length ? "<ul>" + c.gaps.map((g) => `<li>${g[0]} → ${g[1]}</li>`).join("") + "</ul>" : "<p class='muted'>none</p>"}
  <h2>Hours with a low item share</h2>${c.low_share_hours.length ? "<p>" + c.low_share_hours.map(esc).join(", ") + "</p>" : "<p class='muted'>none</p>"}`;
};

pages.settings = async () => {
  const [s, st] = await Promise.all([get("/api/settings"), get("/api/strategies")]);
  window.__settings = s; window.__strategies = st;
  const groups = Object.entries(s.groups).map(([g, items]) => `<div class="card"><b>${esc(g)}</b><div class="row">` +
    items.map((i) => `<label>${esc(i.key)}${i.min != null ? ` (${i.min}…${i.max})` : ""}
      <input data-key="${i.key}" data-orig="${esc(JSON.stringify(i.value))}" value="${esc(Array.isArray(i.value) ? i.value.join(", ") : i.value)}" size="12"></label>`).join("") + "</div></div>").join("");
  return `<h1>Settings</h1><p class="muted">Saved to <code>cs2.toml</code> (old file kept as <code>cs2.toml.bak</code>). Secrets in <code>.env</code> are never shown or edited here.</p>
  ${groups}
  <div class="card"><b>Read-only</b> ${s.read_only.map((r) => `<div>${esc(r.key)} = ${esc(r.value)} <span class="muted">— ${esc(r.why)}</span></div>`).join("")}</div>
  <div class="row"><button id="save">Save changes</button><span id="smsg"></span></div>
  <div class="card"><b>Strategy parameters</b> <span class="muted">(defaults that pre-fill the Run form; the report keeps using each strategy's grid)</span>
  <div class="row"><label>Strategy <select id="sd-strategy">${st.strategies.map((x) => `<option>${x.name}</option>`).join("")}</select></label><button class="sec" id="sd-save">Save defaults</button><span id="sdmsg"></span></div>
  <label>Params (JSON)<textarea id="sd-params"></textarea></label><div id="sd-grid" class="muted"></div></div>`;
};
pages.settings.after = () => {
  const parse = (inp) => {
    const raw = inp.value.trim(), orig = JSON.parse(inp.dataset.orig);
    if (Array.isArray(orig)) return raw.split(",").map((x) => Number(x.trim()));
    if (typeof orig === "number") return Number(raw);
    return raw;
  };
  document.getElementById("save").onclick = async () => {
    const changes = {};
    document.querySelectorAll("input[data-key]").forEach((inp) => {
      if (inp.value !== inp.defaultValue) changes[inp.dataset.key] = parse(inp);
    });
    const msg = document.getElementById("smsg");
    if (!Object.keys(changes).length) { msg.textContent = "Nothing changed."; return; }
    if ("wf.holdout_start" in changes && !confirm(window.__settings.holdout_warning + "\n\nSave anyway?")) return;
    const r = await post("/api/settings", {changes});
    msg.innerHTML = r.body.ok ? `<span class="good">${esc(r.body.note)}</span>` : `<span class="bad">${(r.body.errors || [r.body.error]).map(esc).join("; ")}</span>`;
    if (r.body.ok) pages.settings().then((h) => { page.innerHTML = h; pages.settings.after(); });
  };
  const st = window.__strategies, sel = document.getElementById("sd-strategy");
  const fill = () => {
    const s = st.strategies.find((x) => x.name === sel.value);
    document.getElementById("sd-params").value = JSON.stringify(st.defaults[s.name] || {});
    document.getElementById("sd-grid").textContent = "Allowed keys and the report's grid: " + JSON.stringify(s.grid);
  };
  sel.onchange = fill; fill();
  document.getElementById("sd-save").onclick = async () => {
    let params;
    try { params = JSON.parse(document.getElementById("sd-params").value || "{}"); } catch (e) { return (document.getElementById("sdmsg").textContent = "Not valid JSON"); }
    const r = await post("/api/strategy-defaults", {strategy: sel.value, params});
    document.getElementById("sdmsg").innerHTML = r.body.ok ? '<span class="good">saved</span>' : `<span class="bad">${(r.body.errors || []).map(esc).join("; ")}</span>`;
    if (r.body.ok) st.defaults[sel.value] = params;
  };
};

/* ---------- router ---------- */
async function route() {
  const [, name = "verdict", arg] = location.hash.split("/");
  document.querySelectorAll("nav a").forEach((a) => a.classList.toggle("on", a.getAttribute("href") === "#/" + name));
  const p = pages[name] || pages.verdict;
  page.innerHTML = "<p class='muted'>Loading…</p>";
  try {
    page.innerHTML = await p(arg);
    if (p.after) p.after(arg);
  } catch (e) {
    page.innerHTML = `<div class="msg">Could not load this page: ${esc(e.message)}</div>`;
  }
}
async function loadOverview() {
  const o = await get("/api/overview");
  document.getElementById("overview").textContent = o.state === "ok"
    ? `data ${o.data_version} · last day ${o.last_day} · ${o.items_with_history} items` : (o.message || "");
}
window.addEventListener("hashchange", route);
route();
loadOverview();
