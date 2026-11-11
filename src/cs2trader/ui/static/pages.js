"use strict";
/* One entry per route: `html(arg)` returns the page markup, `after(root, arg)` wires its controls. */

const pages = {};
const state = {capital: null, started: false};
const stratByName = (st, name) => st.strategies.find((s) => s.name === name);
const settingValue = (s, key) => {
  for (const items of Object.values(s.groups)) for (const i of items) if (i.key === key) return i.value;
  return null;
};
const on = (root, type, sel, fn) => root.addEventListener(type, (e) => {
  const t = e.target.closest ? e.target.closest(sel) : null;
  if (t && root.contains(t)) fn(e, t);
});

/* =============================== Home =============================== */
pages.home = {
  title: "Where are we?",
  async html() {
    const h = await get("/api/home");
    const v = h.verdict, m = h.market;
    const ico = {some: ["yes", "✓"], none: ["no", "✕"], unknown: ["unknown", "?"]}[v.status] || ["unknown", "?"];
    const stale = v.status !== "unknown" && v.freshness && v.freshness.stale
      ? `<p class="note warn"><b>This answer may be out of date.</b> ${v.freshness.reasons.map((r) => esc(r.text)).join(" ")}</p>` : "";
    const steps = h.next.map((s) => `<li class="${s.optional ? "optional" : ""}">${rich(s.text)}${s.link ? ` <a href="${esc(s.link)}">${esc(s.link_text)} →</a>` : ""}</li>`).join("");
    return `${pageHead("Where are we?", `This project tests one question: do simple ${term("strategy", "trading strategies")} make money on CS2 skins after the market's real fees? It replays them on past prices (a ${term("backtest")}); it never trades real money.`)}
    <div class="grid2">
      <section class="card qa" aria-labelledby="q1">
        <h2 id="q1">Is any strategy good enough to use?</h2>
        <div class="answer"><span class="mark ${ico[0]}" aria-hidden="true">${ico[1]}</span>
          <div><p class="big">${esc(v.headline)}</p><p>${esc(v.detail)}</p></div></div>
        ${v.lines && v.lines.length ? `<ul class="plain">${v.lines.map((l) => `<li>${esc(l)}</li>`).join("")}</ul>` : ""}
        ${v.day ? `<p class="muted small">From the report of ${esc(fmtDate(v.day))}.</p>` : ""}
        ${stale}
        <a class="more" href="#/verdict">See how each strategy did →</a>
      </section>
      <section class="card qa" aria-labelledby="q2">
        <h2 id="q2">Is the data fresh and complete?</h2>
        <div class="answer">${light(h.data.status)}<div><p class="big">${esc(h.data.headline)}</p>
          <p>${esc(h.data.detail)} <span class="muted">This long history is what strategies are tested on.</span></p></div></div>
        <div class="answer">${light(h.collector.status)}<div><p class="big">${esc(h.collector.headline)}</p>
          <p>${esc(h.collector.detail)} <span class="muted">These are our own hourly ${term("snapshot", "snapshots")} of market.csgo.</span></p></div></div>
        <a class="more" href="#/collector">See collector health →</a>
      </section>
      <section class="card qa" aria-labelledby="q3">
        <h2 id="q3">What is the market doing right now?</h2>
        ${m.regime ? `<div class="answer">${regimeBadge(m.regime)}<div><p class="big">${esc(m.headline)}</p>
          <p>${esc(m.detail)} ${m.year ? esc(m.year) : ""}</p>
          <p class="muted small">${esc(m.rule)} As of ${esc(fmtDate(m.as_of))}; ${esc(m.regime)} since ${esc(fmtDate(m.since))}.</p></div></div>
          <div id="spark" class="spark" aria-hidden="true"></div>`
        : `<div class="empty"><b>${esc(m.headline)}</b><p>${rich(m.detail)}</p></div>`}
        <a class="more" href="#/market">Open the market page →</a>
      </section>
      <section class="card qa" aria-labelledby="q4">
        <h2 id="q4">What should I do next?</h2>
        <ol class="steps">${steps}</ol>
        <p class="muted small">Commands shown like <code>cs2 report</code> are typed in a terminal, in the project folder.</p>
      </section>
    </div>`;
  },
  after(root) {
    const el = $("#spark", root);
    if (!el) return;
    get("/api/market/index").then((d) => {
      if (d.state !== "ok" || !el.isConnected) return;
      const from = isoDay(parseT(d.points[d.points.length - 1].t) - 365 * 864e5);
      const pts = d.points.filter((p) => p.t >= from);
      chart(el, {height: 120, label: "Market index over the last year", series: [{name: "index", color: "--s1", points: pts.map((p) => ({x: p.t, y: p.index_level}))}],
        bands: regimeBands(pts), fmt: (v) => fmtNum(v, 1), hint: "Market index, last 12 months. Green = rising, red = falling."});
    });
  },
};

/* =============================== Verdict =============================== */
const PERIOD_WORD = {walk_forward: "Walk-forward test (2019 to the tuning end)", holdout: "Final test period (holdout)", holdout_stress: "Final test period with higher costs"};
const REGIME_FILTER = {all: "All days", rising: "Only rising days", falling: "Only falling days", flat: "Only flat days"};
const UNIT_WORD = {cash: "Cash-out (main)", steam: "Steam wallet"};

function barRow(label, value, capital, max, kind) {
  const w = Math.max(0, Math.min(100, (value / max) * 100));
  return `<div class="bar-row"><span class="bar-label">${esc(label)}</span>
    <span class="track"><span class="fill ${kind}" style="width:${w.toFixed(1)}%"></span><span class="start" style="left:${((capital / max) * 100).toFixed(1)}%"></span></span>
    <span class="bar-value ${signClass(value - capital)}">${fmtMoney(value)} <small>${fmtPct(value / capital - 1)}</small></span></div>`;
}

function verdictCard(c, board, max) {
  const pill = c.status === "holds_up" ? `<span class="pill pass">✓ Holds up</span>`
    : c.status === "fails" ? `<span class="pill fail">✕ Fails</span>` : `<span class="pill unknown">Not judged</span>`;
  const b = board.benchmarks;
  const bars = c.end_value == null ? "" : `<div class="bars" role="img" aria-label="${esc(`End value: this strategy ${fmtMoney(c.end_value)}, hold cash ${b.cash ? fmtMoney(b.cash.end_value) : "unknown"}, buy and hold ${b.bh ? fmtMoney(b.bh.end_value) : "unknown"}`)}">
      ${barRow("This strategy", c.end_value, c.capital, max, "main")}
      ${b.cash ? barRow("Hold cash", b.cash.end_value, c.capital, max, "bench") : ""}
      ${b.bh ? barRow("Buy and hold", b.bh.end_value, c.capital, max, "bench") : ""}</div>`;
  const checks = c.checks.length ? `<ol class="checks">${c.checks.map((k) => `<li class="${k.ok === true ? "ok" : k.ok === false ? "ko" : "na"}">
      <span class="ck" aria-hidden="true">${k.ok === true ? "✓" : k.ok === false ? "✕" : "–"}</span>
      <span><b>${esc(k.question)}</b><br>${esc(k.text)}${k.code === c.first_broken_rule ? ` <span class="tag">first failed check</span>` : ""}</span></li>`).join("")}</ol>` : "";
  const regimes = Object.keys(c.regimes).length
    ? `<p><b>By ${term("regime", "market mood")}:</b> ${["rising", "falling", "flat"].filter((r) => r in c.regimes).map((r) => `on ${r} days <span class="${signClass(c.regimes[r])}">${fmtPct(c.regimes[r])}</span>`).join(", ")}.</p>` : "";
  const facts = [
    c.n_trades != null ? `${fmtNum(c.n_trades)} trades` : null,
    c.fees_paid != null ? `${fmtMoney(c.fees_paid)} paid in fees` : null,
    c.n_variants != null ? `${fmtNum(c.n_variants)} ${c.n_variants === 1 ? "variant" : "variants"} tried` : null,
    c.deflated_sharpe != null ? `luck-check score ${fmtNum(c.deflated_sharpe, 2)} (must be above 0)` : null,
  ].filter(Boolean).join(" · ");
  return `<article class="card vcard ${esc(c.status || "unknown")}">
    <header><div><h3>${esc(c.label)}</h3><code class="muted">${esc(c.strategy)}</code></div>${pill}</header>
    ${c.reason ? `<p class="reason">${esc(c.reason)}</p>` : ""}
    <p>${esc(c.result)}</p>
    ${bars}
    <details><summary>Show the five checks and details</summary>
      ${checks}${c.status === "fails" ? `<p class="muted small">The verdict stops at the first failed check; the later ones are worked out from the same report numbers for information.</p>` : ""}${regimes}${facts ? `<p class="muted">${facts}</p>` : ""}
      <p><a href="#/runs/new/${encodeURIComponent(c.strategy)}">Try this strategy on other dates →</a></p>
    </details></article>`;
}

function boardHtml(v) {
  const board = v.scoreboard.find((b) => b.capital === state.capital) || v.scoreboard[0];
  if (!board) return `<div class="empty"><b>No results in this report.</b></div>`;
  const b = board.benchmarks;
  const ends = board.cards.map((c) => c.end_value || 0).concat([board.capital, b.cash ? b.cash.end_value : 0, b.bh ? b.bh.end_value : 0]);
  const max = Math.max(...ends) * 1.02;
  return `${board.n_judged ? `<p class="lead">${esc(board.headline)}</p>` : ""}
    <div class="card yard"><h3>The two yardsticks <span class="muted">in the final test period, starting with ${fmtMoney(board.capital)}</span></h3>
      <div class="bars">${b.cash ? barRow("Hold cash", b.cash.end_value, board.capital, max, "bench") : ""}
      ${b.bh ? barRow("Buy and hold", b.bh.end_value, board.capital, max, "bench") : ""}</div>
      <p class="muted small">A strategy has to beat both. The thin vertical line marks the starting ${fmtMoney(board.capital)}: a bar that ends left of it lost money. ${term("cash", "Hold cash")} is below the line because withdrawing from market.csgo costs a fee.</p></div>
    <div class="cards">${board.cards.map((c) => verdictCard(c, board, max)).join("") || `<div class="empty"><b>This report has no strategies for ${fmtMoney(board.capital)}.</b></div>`}</div>`;
}

pages.verdict = {
  title: "Verdict",
  async html(day) {
    const v = await get("/api/verdict" + (day ? `?day=${encodeURIComponent(day)}` : ""));
    const head = pageHead("Verdict", `Each strategy is judged on the ${term("holdout", "final test period")}, a stretch of history it was never tuned on, in ${term("cash_out", "cash you could withdraw")}, against two ${term("benchmark", "yardsticks")}.`);
    if (v.state !== "ok") return head + notice(v, "No report yet");
    pages.verdict.data = v;
    const caps = v.scoreboard.map((b) => b.capital);
    if (!caps.includes(state.capital)) state.capital = caps.includes(1000) ? 1000 : caps[0];
    const cls = {some: "green", none: "neutral", unknown: "amber"}[v.headline.status];
    const noVerdicts = v.verdicts === null;
    const reasons = v.freshness.reasons.filter((r) => !(noVerdicts && r.code === "no_verdicts"));
    const older = v.is_latest ? "" : `<div class="banner amber" role="status"><b>You are looking at an older report.</b> The newest one is from ${esc(fmtDate(v.reports[0]))}.</div>`;
    const stale = reasons.length ? `<div class="banner amber" role="status"><b>This report is out of date.</b>
      <ul>${reasons.map((r) => `<li>${esc(r.text)}</li>`).join("")}</ul>${rich(v.freshness.advice)}</div>` : "";
    const headline = noVerdicts
      ? `<div class="banner amber headline"><p class="big">No verdict in this report</p><p>It was made by an older version of the tool: it has each strategy's results, shown below, but no pass / fail decision. ${rich("Run `cs2 report` in a terminal to build a current report with verdicts (this takes a while).")}</p></div>`
      : `<div class="banner ${cls} headline"><p class="big">${esc(v.headline.headline)}</p><p>${esc(v.headline.detail)}</p></div>`;
    const periods = [...new Set(v.results.map((r) => r.period))], regimes = [...new Set(v.results.map((r) => r.regime))];
    const opt = (list, words) => list.map((p) => `<option value="${esc(p)}">${esc(words[p] || p)}</option>`).join("");
    return `${head}
    ${headline}
    ${older}${stale}
    <div class="toolbar">
      <div class="seg" role="group" aria-label="Starting amount">${caps.map((c) => `<button type="button" data-cap="${c}" aria-pressed="${c === state.capital}">Start with ${fmtMoney(c)}</button>`).join("")}</div>
      <label class="inline">Report from <select id="vday">${v.reports.map((d) => `<option value="${esc(d)}" ${d === v.day ? "selected" : ""}>${esc(fmtDate(d))}</option>`).join("")}</select></label>
    </div>
    <details class="explain"><summary>How is a strategy judged?</summary>
      <p>The rule was fixed before any result was seen. A strategy <b>holds up</b> only if it passes all five checks, in this order, in the final test period (from ${esc(fmtDate(v.holdout_start))}):</p>
      <ol><li>It ends with more money than ${term("cash", "holding cash")}.</li>
      <li>It ends with more money than ${term("bh", "buying and holding")} a basket of skins.</li>
      <li>Its ${term("drawdown", "worst dip")} is no deeper than buy-and-hold's.</li>
      <li>It still beats both when costs are raised (${term("cost_stress")}).</li>
      <li>Its result is more than luck (${term("deflated_sharpe", "luck check")}).</li></ol>
      <p>Otherwise it <b>fails</b>, and the first check it failed is shown. “No strategy holds up” is a real, useful answer: it means none of these ideas survives real costs.</p></details>
    <div id="board">${boardHtml(v)}</div>
    <details class="advanced"><summary>Advanced: full numbers</summary>
      <p class="muted">Every row of the report's results table. Pick what to show:</p>
      <div class="toolbar">
        <label class="inline">Tested on <select id="fper">${opt(periods, PERIOD_WORD)}</select></label>
        <label class="inline">Days counted <select id="freg">${opt(regimes, REGIME_FILTER)}</select></label>
        <label class="inline">Profit counted as <select id="funit">${opt(["cash", "steam"], UNIT_WORD)}</select> ${help("units")}</label>
      </div>
      <div class="scroll" id="rtable"></div>
      <p class="muted small">${term("results_hash", "Results fingerprint")} <code>${esc(v.results_hash)}</code>${v.hash_matches_report === true ? " (matches the saved report)" : v.hash_matches_report === false ? ` <span class="bad">(differs from the saved report)</span>` : ""}
        · ${term("data_version")} <code>${esc(v.data_version || "–")}</code> · price history up to ${esc(fmtDate(v.last_day))}
        · <a href="${esc(v.report_html)}" target="_blank" rel="noopener">Open the original HTML report</a></p>
    </details>`;
  },
  after(root) {
    const v = pages.verdict.data;
    if (!v || !$("#board", root)) return;
    const draw = () => {
      const f = (id) => $("#" + id, root).value;
      const rows = v.results.filter((r) => r.capital === state.capital && r.period === f("fper") && r.regime === f("freg") && r.unit === f("funit"));
      $("#rtable", root).innerHTML = rows.length
        ? `<table><caption class="sr">Results for ${fmtMoney(state.capital)}</caption><thead><tr><th scope="col">Strategy</th><th scope="col">Result after fees ${help("net_return")}</th><th scope="col">Worst dip ${help("drawdown")}</th><th scope="col">Trades</th><th scope="col">Fees paid</th><th scope="col">Sharpe ${help("sharpe")}</th></tr></thead><tbody>` +
          rows.map((r) => `<tr class="${["cash", "bh_universe", "bh_knives_gloves"].includes(r.strategy) ? "bench" : ""}"><th scope="row"><code>${esc(r.strategy)}</code></th>
          <td class="${signClass(r.net_return)}">${fmtPct(r.net_return)}</td><td>${fmtPct(-r.max_drawdown)}</td><td>${fmtNum(r.n_trades)}</td>
          <td>${fmtMoney(r.fees_paid, 2)}</td><td>${r.sharpe == null ? "–" : fmtNum(r.sharpe, 2)}</td></tr>`).join("") + `</tbody></table><p class="muted small">Grey italic rows are the yardsticks. Showing ${fmtMoney(state.capital)}; switch the amount at the top of the page.</p>`
        : `<p class="muted">The report has no rows for this combination.</p>`;
    };
    if ($("#fper option[value=holdout]", root)) $("#fper", root).value = "holdout";
    ["fper", "freg", "funit"].forEach((id) => $("#" + id, root).addEventListener("change", draw));
    $("#vday", root).addEventListener("change", (e) => { location.hash = "#/verdict/" + e.target.value; });
    on(root, "click", "[data-cap]", (e, btn) => {
      state.capital = Number(btn.dataset.cap);
      $$("[data-cap]", root).forEach((b) => b.setAttribute("aria-pressed", String(b === btn)));
      $("#board", root).innerHTML = boardHtml(v);
      draw();
    });
    draw();
  },
};

/* =============================== Try a strategy / runs =============================== */
function paramFields(s, saved) {
  if (!s.params.length) return `<p class="muted">This one has no settings to choose.</p>`;
  return `<div class="fields">${s.params.map((p) => {
    const has = saved && p.key in saved;
    const options = p.options.slice();
    if (has && !options.some((o) => o === saved[p.key])) options.push(saved[p.key]);
    return `<label class="field"><span class="flabel">${esc(p.label)}</span>
      <select data-param="${esc(p.key)}">
        <option value="">Standard (${esc(paramValue(p.default, p.unit))})</option>
        ${options.map((o) => `<option value="${esc(JSON.stringify(o))}" ${has && saved[p.key] === o ? "selected" : ""}>${esc(paramValue(o, p.unit))}</option>`).join("")}
      </select><span class="fhelp">${esc(p.help)} <code>${esc(p.key)}</code></span></label>`;
  }).join("")}</div>`;
}
function readParams(root) {
  const out = {};
  $$("select[data-param]", root).forEach((sel) => { if (sel.value !== "") out[sel.dataset.param] = JSON.parse(sel.value); });
  return out;
}

function runResultText(r) {
  if (r.end_cash == null) return "";
  const ret = r.end_cash / r.capital - 1;
  return `${fmtMoney(r.capital)} → <b class="${signClass(ret)}">${fmtMoney(r.end_cash)}</b> <span class="${signClass(ret)}">(${fmtPct(ret)})</span>`;
}

pages.runs = {
  title: "Try a strategy",
  async html(arg, arg2) {
    if (arg && arg !== "new") return runDetail(arg);
    const [list, st, ov, se] = await Promise.all([get("/api/runs"), get("/api/strategies"), get("/api/overview"), get("/api/settings")]);
    const last = ov.last_day || isoDay(Date.now());
    const holdout = settingValue(se, "wf.holdout_start"), wfStart = settingValue(se, "wf.start"), wfEnd = settingValue(se, "wf.end");
    const back = (days) => isoDay(parseT(last) - days * 864e5);
    const presets = [
      {id: "year", label: "Last 12 months", from: back(365), to: last},
      {id: "holdout", label: "Final test period", from: holdout, to: last},
      {id: "tuning", label: `Tuning years (${wfStart.slice(0, 4)}–${wfEnd.slice(0, 4)})`, from: wfStart, to: wfEnd},
      {id: "all", label: `Everything since ${wfStart.slice(0, 4)}`, from: wfStart, to: last},
    ].filter((p) => p.from < p.to);
    pages.runs.ctx = {st, presets, list};
    const pick = stratByName(st, arg2) ? arg2 : "v1_dip_knives";
    // The video's strategies first, the yardsticks last.
    const rank = (x) => (x.family === "video" ? 0 : x.is_benchmark ? 2 : 1);
    const ordered = st.strategies.slice().sort((a, b) => rank(a) - rank(b) || a.label.localeCompare(b.label));
    const rows = (list.runs || []).map((r) => `<li><a class="runrow" href="#/runs/${esc(r.run_id)}">
        <span class="rr-main"><b>${esc(r.label)}</b><span class="muted">${esc(fmtDate(r.start))} → ${esc(fmtDate(r.end))} · ${esc(paramsText(stratByName(st, r.strategy), r.params))}</span></span>
        <span class="rr-result">${runResultText(r)}</span></a></li>`).join("");
    return `${pageHead("Try a strategy", `Replay one strategy on past prices and see what would have happened to your money. This is a ${term("backtest")}: nothing is bought or sold.`)}
    ${notice(ov.state === "busy" ? ov : null)}
    <form id="runform" class="card form" novalidate>
      <fieldset><legend><span class="stepn">1</span> Pick a strategy</legend>
        <div class="choices" role="radiogroup" aria-label="Strategy">${ordered.map((s) => `<label class="choice"><input type="radio" name="strategy" value="${esc(s.name)}" ${s.name === pick ? "checked" : ""}>
          <span><b>${esc(s.label)}</b><small>${esc(s.family_label)}</small></span></label>`).join("")}</div>
        <div id="sdesc" class="desc" aria-live="polite"></div>
      </fieldset>
      <fieldset><legend><span class="stepn">2</span> Starting amount</legend>
        <div class="seg" role="group" aria-label="Starting amount">
          <button type="button" data-amount="100" aria-pressed="false">$100</button><button type="button" data-amount="1000" aria-pressed="true">$1,000</button></div>
        <label class="inline">or another amount: $ <input id="rf-capital" type="number" min="1" step="1" value="1000" inputmode="decimal" aria-label="Starting amount in dollars"></label>
        <p class="fhelp">With little money a strategy can afford fewer (and cheaper) items, so results differ by amount.</p>
      </fieldset>
      <fieldset><legend><span class="stepn">3</span> Which period to replay</legend>
        <div class="chips" role="group" aria-label="Period presets">${presets.map((p, i) => `<button type="button" class="chip" data-preset="${p.id}" aria-pressed="${i === 0}">${esc(p.label)}</button>`).join("")}</div>
        <div class="row"><label class="field">From <input id="rf-start" type="date" value="${esc(presets[0] ? presets[0].from : back(365))}"></label>
        <label class="field">To <input id="rf-end" type="date" value="${esc(presets[0] ? presets[0].to : last)}"></label></div>
        <p class="fhelp">Price history is available up to ${esc(fmtDate(last))}. The ${term("holdout", "final test period")} starts ${esc(fmtDate(holdout))}. Longer periods take longer to run.</p>
      </fieldset>
      <fieldset><legend><span class="stepn">4</span> Strategy settings <span class="muted">(optional)</span></legend>
        <div id="pfields"></div>
        <details id="jsonbox"><summary>Edit as JSON (advanced)</summary>
          <label class="field">Parameters sent to the backtest. Editing here overrides the fields above.
          <textarea id="rf-params" spellcheck="false" rows="3">{}</textarea></label></details>
      </fieldset>
      <div class="actions"><button type="submit" class="primary" id="rf-go">Run the backtest</button>
        <span class="muted small">Roughly a minute per year of history.</span></div>
      <div id="formerr" role="alert"></div>
    </form>
    <div id="job" aria-live="polite"></div>
    <h2>Earlier runs</h2>
    ${rows ? `<ul class="runlist">${rows}</ul>` : `<div class="empty"><b>No runs yet.</b><p>Your first run will appear here. Pick a strategy above and press “Run the backtest”.</p></div>`}`;
  },
  after(root, arg) {
    if (arg && arg !== "new") return runDetailAfter(root);
    const {st, presets} = pages.runs.ctx;
    const form = $("#runform", root), ta = $("#rf-params", root);
    let jsonEdited = false;
    const current = () => stratByName(st, form.elements.strategy.value);
    const syncJson = () => { ta.value = JSON.stringify(readParams(form)); jsonEdited = false; };
    const fill = () => {
      const s = current();
      $("#sdesc", root).innerHTML = `<p>${esc(s.summary)}</p><details><summary>Exact rules (technical)</summary>${specHtml(s.spec)}</details>`;
      $("#pfields", root).innerHTML = paramFields(s, st.defaults[s.name] || {});
      syncJson();
    };
    on(form, "change", "input[name=strategy]", fill);
    on(form, "change", "select[data-param]", syncJson);
    ta.addEventListener("input", () => { jsonEdited = true; });
    const cap = $("#rf-capital", root);
    const markAmount = () => $$("[data-amount]", form).forEach((b) => b.setAttribute("aria-pressed", String(Number(b.dataset.amount) === Number(cap.value))));
    on(form, "click", "[data-amount]", (e, b) => { cap.value = b.dataset.amount; markAmount(); });
    cap.addEventListener("input", markAmount);
    const markPreset = (id) => $$("[data-preset]", form).forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.preset === id)));
    on(form, "click", "[data-preset]", (e, b) => {
      const p = presets.find((x) => x.id === b.dataset.preset);
      $("#rf-start", root).value = p.from; $("#rf-end", root).value = p.to; markPreset(p.id);
    });
    ["rf-start", "rf-end"].forEach((id) => $("#" + id, root).addEventListener("input", () => markPreset(null)));
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const err = $("#formerr", root);
      err.innerHTML = "";
      let params;
      try { params = jsonEdited ? JSON.parse(ta.value || "{}") : readParams(form); }
      catch (ex) { err.innerHTML = `<div class="banner red"><b>The JSON in “Edit as JSON” is not valid.</b> ${esc(ex.message)}. Fix it, or change any field above to reset it.</div>`; return; }
      const start = $("#rf-start", root).value, end = $("#rf-end", root).value;
      if (!start || !end || start >= end) { err.innerHTML = `<div class="banner red"><b>Check the dates.</b> “From” must be earlier than “To”.</div>`; return; }
      if (!(Number(cap.value) > 0)) { err.innerHTML = `<div class="banner red"><b>Check the amount.</b> The starting amount must be more than $0.</div>`; return; }
      $("#rf-go", root).disabled = true;
      const r = await post("/api/jobs", {strategy: current().name, params, capital: cap.value, start, end});
      $("#rf-go", root).disabled = false;
      if (r.status === 409) { err.innerHTML = `<div class="banner amber"><b>Busy.</b> ${r.body.error && r.body.error.includes("already running") ? "Another backtest is still running. Wait for it to finish, then try again." : "Another job is using the database (a report, a download or the collector). Try again when it finishes."}</div>`; return; }
      if (r.status >= 400) { err.innerHTML = `<div class="banner red"><b>Could not start the run.</b> ${esc(r.body.error || "Unknown error")}</div>`; return; }
      state.started = true;
      pollJob();
    });
    fill();
    pollJob(true);
  },
};

async function runDetail(runId) {
  const [d, st] = await Promise.all([get("/api/run/" + encodeURIComponent(runId)), get("/api/strategies")]);
  const back = `<p><a href="#/runs">← Back to Try a strategy</a></p>`;
  if (d.error) return `${back}${pageHead("Run not found")}<div class="empty"><b>This run does not exist (any more).</b><p>${esc(d.error)}. Pick one from the list of earlier runs.</p></div>`;
  const s = d.meta.spec, sum = d.summary, strat = stratByName(st, s.strategy);
  pages.runs.detail = d;
  const kpi = (label, value, sub, cls) => `<div class="kpi"><span class="klabel">${label}</span><b class="${cls || ""}">${value}</b><span class="muted small">${sub || ""}</span></div>`;
  const body = !sum ? `<div class="empty"><b>This run has no daily values.</b></div>` : `
    <div class="card summary ${esc(sum.outcome)}"><p class="big">${esc(sum.text)}</p><p>${esc(sum.dip)} ${esc(sum.trades)}</p>
      ${sum.notes.map((n) => `<p class="muted">${esc(n)}</p>`).join("")}</div>
    <div class="kpis">
      ${kpi(`End value ${help("cash_out")}`, fmtMoney(sum.end_cash), `${fmtPct(sum.return_cash)} · cash you could withdraw`, signClass(sum.return_cash))}
      ${kpi(`As Steam credit ${help("steam_wallet")}`, fmtMoney(sum.end_steam), `${fmtPct(sum.return_steam)} · cannot be withdrawn`, signClass(sum.return_steam))}
      ${kpi(`Worst dip ${help("drawdown")}`, fmtPct(sum.worst_dip), sum.worst_dip < 0 ? `on ${esc(fmtDate(sum.worst_dip_day))}` : "never below an earlier high")}
      ${kpi(`Fees paid ${help("fees")}`, fmtMoney(sum.fees_paid, 2), `${fmtNum(sum.n_filled)} trades`)}
    </div>
    <figure class="card"><h3>How the money changed over time</h3>
      ${legendHtml([{name: "Cash-out value (main)", color: "var(--s1)"}, {name: "Steam wallet value", color: "var(--s2)"}, {name: `Starting amount (${fmtMoney(s.capital)})`, color: "var(--muted)", swatch: "dash"}])}
      <div id="eq"></div>
      <figcaption>What the account would be worth each day if everything were sold and paid out that day. Above the dashed line = ahead of the starting ${fmtMoney(s.capital)}. The line starts a little below it because cashing out costs a fee.</figcaption></figure>
    <figure class="card"><h3>How far below its best value it was</h3>
      ${legendHtml([{name: "Cash-out", color: "var(--s1)"}, {name: "Steam wallet", color: "var(--s2)"}])}
      <div id="dd"></div>
      <figcaption>0 % means a new high. −20 % means the account was a fifth below the best value it had reached so far. The deepest point is the ${term("drawdown", "worst dip")}.</figcaption></figure>
    <details class="card"><summary>Show the trades (${fmtNum(d.n_trades)} orders${d.n_trades > d.trades.length ? `, first ${fmtNum(d.trades.length)} listed` : ""})</summary>
      ${d.trades.length ? `<div class="scroll"><table><thead><tr><th scope="col">Day</th><th scope="col" class="l">Item</th><th scope="col">Action</th><th scope="col">Wanted</th><th scope="col">Got</th><th scope="col">Price each</th><th scope="col">Fee</th><th scope="col" class="l">Outcome</th></tr></thead><tbody>` +
        d.trades.map((t) => `<tr><td>${esc(fmtDate(t.t))}</td><td class="l">${esc(t.market_hash_name)}</td><td>${t.side === "buy" ? "Buy" : t.side === "sell" ? "Sell" : esc(t.side)}</td><td>${fmtNum(t.qty_requested)}</td>
        <td>${fmtNum(t.qty_filled)}</td><td>${fmtMoney(t.price, 2)}</td><td>${fmtMoney(t.fee, 2)}</td><td class="l">${esc(REASON_WORD[t.reason] || t.reason)}</td></tr>`).join("") + `</tbody></table></div>
        <p class="muted small">“Got” can be less than “Wanted”: we assume only a share of an item's daily volume can be traded (${term("participation", "volume limit")}).</p>` : `<p class="muted">This run placed no orders.</p>`}
    </details>`;
  return `${back}${pageHead(d.label, `${fmtMoney(s.capital)} from ${esc(fmtDate(s.start))} to ${esc(fmtDate(s.end))} · ${esc(paramsText(strat, s.params))}`)}
    ${body}
    <details class="card"><summary>Technical details</summary>
      <dl class="tech"><dt>${term("run_id")}</dt><dd><code>${esc(d.meta.run_id)}</code></dd>
      <dt>${term("data_version")}</dt><dd><code>${esc(d.meta.data_version)}</code></dd>
      <dt>Strategy code name</dt><dd><code>${esc(s.strategy)}</code></dd>
      <dt>Parameters</dt><dd><code>${esc(JSON.stringify(s.params))}</code></dd>
      <dt>Costs and fill assumptions used</dt><dd><code>${esc(JSON.stringify({costs: s.costs, fills: s.fills}))}</code></dd></dl>
      <p><a href="#/runs/new/${encodeURIComponent(s.strategy)}">Run this strategy again with other dates →</a></p></details>`;
}
function runDetailAfter(root) {
  const d = pages.runs.detail;
  if (!d || !d.summary || !$("#eq", root)) return;
  const eq = d.equity, cap = d.meta.spec.capital;
  chart($("#eq", root), {label: "Account value over time", yLabel: "Account value, $", fmt: (v) => fmtMoney(v, 0), fmtExact: (v) => fmtMoney(v, 2),
    baseline: {value: cap, label: `start ${fmtMoney(cap)}`},
    series: [{name: "cash-out", color: "--s1", points: eq.map((e) => ({x: e.t, y: e.equity_cash}))},
      {name: "Steam wallet", color: "--s2", points: eq.map((e) => ({x: e.t, y: e.equity_steam}))}]});
  chart($("#dd", root), {label: "Distance below the best value so far", yLabel: "Below the best value so far, %", height: 190, zero: true, fmt: (v) => fmtPct(v, false, 0), fmtExact: (v) => fmtPct(v, false),
    series: [{name: "cash-out", color: "--s1", points: eq.map((e) => ({x: e.t, y: e.drawdown_cash}))},
      {name: "Steam wallet", color: "--s2", points: eq.map((e) => ({x: e.t, y: e.drawdown_steam}))}]});
}

const REASON_WORD = {ok: "filled in full", volume_cap: "limited: too little of it was traded that day", locked: "not allowed yet: still in the 7-day trade lock",
  no_cash: "limited: not enough cash", no_position: "nothing to sell", limit: "price limit not reached", no_data: "no price that day"};

/* ---- job progress ---- */
let jobTimer = null, tickTimer = null;
const clock = (sec) => `${Math.floor(sec / 60)}:${String(Math.floor(sec % 60)).padStart(2, "0")}`;
function jobProblem(lines) {
  const text = (lines || []).join("\n");
  if (/no bars/i.test(text)) return "There is no price data for those dates. Pick a period inside the available history.";
  if (/lock|conflicting/i.test(text)) return "Another job was using the database. Try again when it has finished.";
  return "The log below shows what went wrong.";
}
function showJob(j) {
  const el = $("#job");
  clearInterval(tickTimer);
  if (!el) return;
  if (j.state === "idle") { el.innerHTML = ""; return; }
  const st = pages.runs.ctx && pages.runs.ctx.st, s = st && stratByName(st, j.strategy);
  const what = `${esc(s ? s.label : j.strategy)}, ${fmtMoney(j.capital)}, ${esc(fmtDate(j.start))} → ${esc(fmtDate(j.end))}`;
  const log = `<details><summary>Show the log</summary><pre tabindex="0">${esc((j.lines || []).join("\n") || "(no output yet)")}</pre></details>`;
  if (j.state === "running") {
    el.innerHTML = `<div class="card job" role="status"><div class="jobhead"><span class="spinner" aria-hidden="true"></span><b>Running: ${what}</b></div>
      <p>Elapsed <b id="elapsed">${clock(Date.now() / 1000 - j.started)}</b>. A year takes roughly a minute; the full history takes several minutes. You can leave this page or close the browser: the run continues, and the result will be listed under “Earlier runs”.</p>${log}</div>`;
    tickTimer = setInterval(() => { const e = $("#elapsed"); if (e) e.textContent = clock(Date.now() / 1000 - j.started); else clearInterval(tickTimer); }, 1000);
  } else if (j.state === "done") {
    el.innerHTML = `<div class="card job done" role="status"><b>✓ Finished: ${what}</b>
      <p>Took ${clock((j.finished || j.started) - j.started)}. ${j.run_id ? `<a class="primary btn" href="#/runs/${esc(j.run_id)}">Open the result →</a>` : ""}</p>${log}</div>`;
  } else {
    el.innerHTML = `<div class="card job failed" role="alert"><b>The run did not finish: ${what}</b><p>${esc(jobProblem(j.lines))}</p>${log.replace("<details>", "<details open>")}</div>`;
  }
}
async function pollJob(quiet) {
  clearTimeout(jobTimer);
  let j;
  try { j = await get("/api/jobs"); } catch (e) { return; }
  if (!$("#job")) return;
  if (j.state === "idle" && quiet) return;
  // A job from an earlier visit stays out of the way unless it is still running.
  if (quiet && j.state !== "running" && !state.started) return;
  showJob(j);
  if (j.state === "running") jobTimer = setTimeout(() => pollJob(), 1500);
  else if (j.state === "done" && j.run_id && state.started) { state.started = false; location.hash = "#/runs/" + j.run_id; }
  else state.started = false;
}

/* =============================== Market =============================== */
const CAT_WORD = {weapon_skin: "Weapon skins", knife: "Knives", glove: "Gloves", sticker: "Stickers", case: "Cases", capsule: "Sticker capsules",
  agent: "Agents", charm: "Charms", graffiti: "Graffiti", music_kit: "Music kits", music_kit_box: "Music kit boxes", patch: "Patches",
  souvenir_package: "Souvenir packages", other: "Other", unknown: "Uncategorised"};
const catHue = (i, n) => `hsl(${Math.round((i * 360) / n)} 62% 48%)`;

pages.market = {
  title: "Market",
  async html() {
    return `${pageHead("Market", `What prices have been doing: the whole market first, then by item type, then any single item. All prices are Steam Community Market prices in US dollars.`)}
    <section class="card" aria-labelledby="m1"><h2 id="m1">The whole market <span id="mbadge"></span></h2>
      <p>The ${term("index", "market index")} is one line for all actively traded skins: each day it moves by the typical price change. Coloured stripes show the ${term("regime", "market mood")} on each day.</p>
      <p id="mnow" class="lead"></p>
      <div class="toolbar"><div class="seg" role="group" aria-label="Time range"><button type="button" data-range="730" aria-pressed="true">Last 2 years</button><button type="button" data-range="2018" aria-pressed="false">Since 2018</button><button type="button" data-range="all" aria-pressed="false">Everything</button></div>
      ${legendHtml([{name: "Rising", color: "var(--band-rising-solid)", swatch: "box"}, {name: "Falling", color: "var(--band-falling-solid)", swatch: "box"}, {name: "Flat (no colour)", color: "transparent", swatch: "box outline"}])}</div>
      <div id="idx">${skeleton(1)}</div>
      <p class="muted small" id="idxnote"></p></section>
    <section class="card" aria-labelledby="m2"><h2 id="m2">By item type</h2>
      <p>The same kind of line for each item type over the last two years, all starting at 100 so they can be compared: 120 means that type is up 20&nbsp;%. Click a type to highlight its line.</p>
      <div id="cats">${skeleton(1)}<p class="muted small">Computing this takes a few seconds the first time.</p></div></section>
    <section class="card" aria-labelledby="m3"><h2 id="m3">One item</h2>
      <label class="field" for="isearch">Search by name (at least 3 letters)</label>
      <input id="isearch" type="search" placeholder="e.g. AK-47 | Redline" autocomplete="off" aria-controls="ilist">
      <p class="fhelp">Try: <button type="button" class="chip" data-ex="AK-47 | Redline">AK-47 | Redline</button> <button type="button" class="chip" data-ex="Karambit | Doppler">Karambit | Doppler</button> <button type="button" class="chip" data-ex="Case">Case</button></p>
      <div id="ilist" role="listbox" aria-label="Matching items"></div><div id="item"></div></section>`;
  },
  after(root) {
    get("/api/market/index").then((d) => {
      const el = $("#idx", root);
      if (!el || !el.isConnected) return;
      if (d.state !== "ok") { el.innerHTML = notice(d, "No market index yet"); return; }
      if (d.now) {
        $("#mbadge", root).innerHTML = regimeBadge(d.now.regime);
        $("#mnow", root).innerHTML = `<b>${esc(d.now.headline)}.</b> ${esc(d.now.detail)} ${d.now.year ? esc(d.now.year) : ""}<br><span class="muted small">${esc(d.now.rule)}</span>`;
      }
      const last = d.points[d.points.length - 1].t;
      const draw = (range) => {
        const from = range === "all" ? "0000" : range === "2018" ? "2018-01-01" : isoDay(parseT(last) - Number(range) * 864e5);
        const pts = d.points.filter((p) => p.t >= from);
        const byDay = new Map(pts.map((p) => [p.t.slice(0, 10), p.regime]));
        chart(el, {label: "Market index with rising and falling periods", yLabel: "Index (100 = first day of the history)", log: range !== "730", bands: regimeBands(pts),
          fmt: (v) => fmtNum(v, v < 10 ? 1 : 0), fmtExact: (v) => fmtNum(v, 1),
          series: [{name: "index", color: "--s1", points: pts.map((p) => ({x: p.t, y: p.index_level}))}],
          bandAt: (x) => { const r = byDay.get(isoDay(x)); return r ? `market ${r}` : ""; }});
        $("#idxnote", root).textContent = range === "all" ? "Before 2018 only a few items were traded enough to count, so the early part of the line is unreliable. Logarithmic scale: equal distances are equal percentage changes."
          : range === "2018" ? "Logarithmic scale: equal distances are equal percentage changes." : "";
      };
      on(root, "click", "[data-range]", (e, b) => {
        $$("[data-range]", root).forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
        draw(b.dataset.range);
      });
      draw("730");
    });
    get("/api/market/categories").then((d) => {
      const el = $("#cats", root);
      if (!el || !el.isConnected) return;
      if (d.state !== "ok") { el.innerHTML = notice(d, "No item-type data yet"); return; }
      const names = Object.keys(d.series).filter((n) => d.series[n].length);
      const change = (n) => d.series[n][d.series[n].length - 1].level / 100 - 1;
      names.sort((a, b) => change(b) - change(a));
      const series = names.map((n, i) => ({name: CAT_WORD[n] || n, key: n, color: catHue(i, names.length), points: d.series[n].map((p) => ({x: p.t, y: p.level}))}));
      el.innerHTML = `<div class="catlegend" role="group" aria-label="Item types, best first">${series.map((s) => `<button type="button" class="chip cat" data-cat="${esc(s.name)}" aria-pressed="false"><i style="background:${s.color}"></i>${esc(s.name)} <span class="${signClass(change(s.key))}">${fmtPct(change(s.key), true, 0)}</span></button>`).join("")}</div><div id="catchart"></div>
        <p class="muted small">Percentages are the change over the two years shown, best first. “Uncategorised” and “Other” are items we could not sort into a type.</p>`;
      let pinned = null;
      const draw = (hl) => chart($("#catchart", el), {label: "Price index by item type", yLabel: "Index (100 = two years ago)", log: true, series, highlight: hl,
        baseline: {value: 100, label: ""}, fmt: (v) => fmtNum(v, 0), fmtExact: (v) => fmtNum(v, 1),
        hint: hl ? "" : "Click or hover an item type above to highlight its line."});
      on(el, "click", "[data-cat]", (e, b) => {
        pinned = pinned === b.dataset.cat ? null : b.dataset.cat;
        $$("[data-cat]", el).forEach((x) => x.setAttribute("aria-pressed", String(x.dataset.cat === pinned)));
        draw(pinned);
      });
      on(el, "mouseover", "[data-cat]", (e, b) => { if (!pinned) draw(b.dataset.cat); });
      $(".catlegend", el).addEventListener("mouseleave", () => { if (!pinned) draw(null); });
      draw(null);
    });
    const box = $("#isearch", root), list = $("#ilist", root);
    let t;
    const search = async () => {
      const q = box.value.trim();
      if (q.length < 3) { list.innerHTML = q ? `<p class="muted small">Type at least 3 letters.</p>` : ""; return; }
      const r = await get("/api/market/search?q=" + encodeURIComponent(q));
      if (r.state && r.state !== "ok") { list.innerHTML = notice(r); return; }
      list.innerHTML = (r.names || []).map((n) => `<button type="button" role="option" class="result" data-n="${esc(n)}">${esc(n)}</button>`).join("")
        || `<p class="muted">No item name contains “${esc(q)}”. Names are in English, as on the Steam market, e.g. “AK-47 | Redline (Field-Tested)”.</p>`;
      if ((r.names || []).length === 20) list.insertAdjacentHTML("beforeend", `<p class="muted small">Showing the first 20 matches. Type more to narrow it down.</p>`);
    };
    box.addEventListener("input", () => { clearTimeout(t); t = setTimeout(search, 250); });
    on(root, "click", "[data-ex]", (e, b) => { box.value = b.dataset.ex; search(); box.focus(); });
    on(list, "click", "[data-n]", async (e, b) => {
      const out = $("#item", root);
      out.innerHTML = skeleton(1);
      const d = await get("/api/market/item?name=" + encodeURIComponent(b.dataset.n));
      if (d.state !== "ok") { out.innerHTML = notice(d, "No price history for this item"); return; }
      const first = d.points[0], last = d.points[d.points.length - 1];
      out.innerHTML = `<h3>${esc(d.name)}</h3><p>${fmtMoney(first.price, 2)} on ${esc(fmtDate(first.t))} → <b>${fmtMoney(last.price, 2)}</b> on ${esc(fmtDate(last.t))} (<span class="${signClass(last.price - first.price)}">${fmtPct(last.price / first.price - 1)}</span>).</p><div id="itemchart"></div>
        <p class="muted small">Average sale price per day on the Steam Community Market, in US dollars.</p>`;
      chart($("#itemchart", out), {label: `Daily price of ${d.name}`, yLabel: "Price, $", fmt: (v) => fmtMoney(v, v < 10 ? 2 : 0), fmtExact: (v) => fmtMoney(v, 2),
        series: [{name: "price", color: "--s1", points: d.points.map((p) => ({x: p.t, y: p.price}))}]});
      out.scrollIntoView({block: "nearest", behavior: "smooth"});
    });
  },
};

/* =============================== Collector =============================== */
pages.collector = {
  title: "Collector",
  async html() {
    const c = await get("/api/collector");
    const head = pageHead("Collector health", `market.csgo publishes only current prices, no history. So a ${term("collector")} saves a ${term("snapshot")} of every price once an hour. This page shows whether it is keeping up.`);
    const why = `<div class="card note-card"><h2>Why holes matter</h2><p>A missed hour is gone for good: there is nowhere to download yesterday's market.csgo prices from. The hourly tests (“would this strategy work on market.csgo itself?”) can only use hours we actually recorded, and need about four weeks of them without long holes.</p></div>`;
    if (c.state !== "ok") return `${head}<div class="banner amber"><p class="big">${light("grey")} ${esc(c.summary ? c.summary.headline : "No collector data yet")}</p><p>${rich("No hourly snapshots are stored on this computer yet. Run `cs2 collect pull` in a terminal to download what the collector has gathered so far.")}</p></div>${why}`;
    const s = c.summary;
    const byHour = new Map(c.per_hour.map((h) => [parseT(h.hour), h]));
    // Same window as the summary above (the last N hours up to now), unless the data is older than that.
    const newest = Math.max(...byHour.keys()), nowHour = Math.floor(Date.now() / 36e5) * 36e5;
    const live = nowHour - newest < c.window_h * 36e5;
    const t1 = live ? nowHour : newest, t0 = t1 - (c.window_h - 1) * 36e5;
    const max = Math.max(...c.per_hour.map((h) => h.items));
    let bars = "", n = 0, missing = 0;
    for (let t = t0; t <= t1; t += 36e5, n++) {
      const h = byHour.get(t);
      if (h) bars += `<rect x="${n * 10 + 1}" y="${(100 - (h.items / max) * 96).toFixed(1)}" width="8" height="${((h.items / max) * 96).toFixed(1)}" fill="var(${h.host === "gha" ? "--s1" : "--s2"})"><title>${esc(fmtTime(h.hour))}: ${fmtNum(h.items)} items, from ${h.host === "gha" ? "GitHub" : "this Mac"}</title></rect>`;
      else { missing++; bars += `<rect x="${n * 10 + 1}" y="0" width="8" height="100" fill="var(--band-falling)"><title>${esc(fmtTime(new Date(t).toISOString()))}: no snapshot</title></rect><rect x="${n * 10 + 1}" y="96" width="8" height="4" fill="var(--bad)"/>`; }
    }
    const fresh = c.age_hours == null ? "–" : c.age_hours < 1 ? `${Math.max(1, Math.round(c.age_hours * 60))} min ago` : c.age_hours < 48 ? `${fmtNum(c.age_hours, 0)} h ago` : `${fmtNum(c.age_hours / 24, 0)} days ago`;
    return `${head}
    <div class="banner ${esc(s.status)} headline"><p class="big">${light(s.status)} ${esc(s.headline)}</p>
      <ul>${s.points.map((p) => `<li>${esc(p)}</li>`).join("")}</ul>${s.advice ? `<p>${rich(s.advice)}</p>` : ""}</div>
    <div class="kpis">
      <div class="kpi"><span class="klabel">Newest snapshot</span><b>${esc(fresh)}</b><span class="muted small">${esc(fmtTime(c.latest_feed_time))}</span></div>
      <div class="kpi"><span class="klabel">Snapshots, last ${c.window_h} hours</span><b class="${c.snapshots >= c.target ? "good" : "warn"}">${fmtNum(c.snapshots)} of ${c.window_h}</b><span class="muted small">at least ${c.target} expected</span></div>
      <div class="kpi"><span class="klabel">Smallest snapshot ${help("item_share")}</span><b>${fmtPct(c.min_item_share, false, 0)}</b><span class="muted small">of the largest one's items</span></div>
    </div>
    <figure class="card"><h3>Items recorded each hour</h3>
      ${legendHtml([{name: "Collected by GitHub", color: "var(--s1)", swatch: "box"}, {name: "Collected by this Mac", color: "var(--s2)", swatch: "box"}, {name: "Hour missing", color: "var(--bad)", swatch: "box"}])}
      <svg class="hours" viewBox="0 0 ${n * 10} 100" preserveAspectRatio="none" role="img" aria-label="${esc(`Items per hour for the last ${n} hours; ${missing} hours missing`)}">${bars}</svg>
      <div class="axis"><span>${esc(fmtTime(new Date(t0).toISOString()))}</span><span>${esc(fmtTime(new Date(t1).toISOString()))}</span></div>
      <figcaption>One bar per hour for ${live ? `the last ${n} hours` : `the ${n} hours up to the newest snapshot`}, oldest on the left. A full-height bar is a complete snapshot (${fmtNum(max)} items); a red stripe is an hour with no snapshot${missing ? ` (${missing} here)` : ""}. Hover a bar for details.${live && missing !== s.missing_hours ? ` The summary at the top is slightly stricter: an hour counts there only when both the sell-price list and the buy-order list arrived, so its number of missing hours (${s.missing_hours}) can differ.` : ""}</figcaption></figure>
    ${why}
    <details class="card"><summary>Exact times of holes and thin snapshots</summary>
      <h3>${term("gap", "Gaps")} longer than 2 hours (last ${c.window_h} hours)</h3>${c.gaps.length ? "<ul>" + c.gaps.map((g) => `<li>${esc(fmtTime(g[0]))} → ${esc(fmtTime(g[1]))}</li>`).join("") + "</ul>" : `<p class="muted">None.</p>`}
      <h3>Snapshots with under 90 % of the usual items</h3>${c.low_share_hours.length ? "<ul>" + c.low_share_hours.map((h) => `<li>${esc(fmtTime(h))}</li>`).join("") + "</ul>" : `<p class="muted">None.</p>`}</details>`;
  },
};

/* =============================== Settings =============================== */
const UNIT_SUFFIX = {share: "%", days: "days", x: "×", "per day": "per day", count: ""};
const shownValue = (v, unit) => (unit === "share" ? Number((v * 100).toFixed(6)) : Array.isArray(v) ? v.join(", ") : v);
const defaultText = (i) => (i.unit === "share" ? fmtPct(i.default, false, Number.isInteger(Number((i.default * 100).toFixed(6))) ? 0 : 2)
  : i.unit === "date" ? fmtDate(i.default) : Array.isArray(i.default) ? i.default.map((x) => fmtMoney(x)).join(", ")
  : i.unit === "$" ? fmtMoney(i.default, 2) : `${i.default}${UNIT_SUFFIX[i.unit] ? " " + UNIT_SUFFIX[i.unit] : ""}`);

function settingField(i, showEffect) {
  const id = "set-" + i.key.replace(/\W/g, "-");
  const common = `id="${id}" data-key="${esc(i.key)}" data-unit="${esc(i.unit)}" data-kind="${esc(i.kind)}" data-orig="${esc(JSON.stringify(i.value))}" data-default="${esc(JSON.stringify(i.default))}" aria-describedby="${id}-h"`;
  let input;
  if (i.key === "fills.ratio_source") input = `<select ${common}>${["fixed", "measured"].map((o) => `<option ${o === i.value ? "selected" : ""}>${o}</option>`).join("")}</select>`;
  else if (i.unit === "date") input = `<input type="date" ${common} value="${esc(i.value)}">`;
  else if (i.kind === "list") input = `<span class="affix">$</span><input type="text" ${common} value="${esc(shownValue(i.value))}" inputmode="decimal">`;
  else {
    const k = i.unit === "share" ? 100 : 1;
    const range = i.min != null ? `min="${Number((i.min * k).toFixed(6))}" max="${Number((i.max * k).toFixed(6))}"` : "";
    input = `${i.unit === "$" ? `<span class="affix">$</span>` : ""}<input type="number" step="any" ${range} ${common} value="${esc(shownValue(i.value, i.unit))}" inputmode="decimal">${UNIT_SUFFIX[i.unit] ? `<span class="affix">${UNIT_SUFFIX[i.unit]}</span>` : ""}`;
  }
  const range = i.min != null ? ` Allowed: ${shownValue(i.min, i.unit)} to ${shownValue(i.max, i.unit)}${UNIT_SUFFIX[i.unit] ? " " + UNIT_SUFFIX[i.unit] : ""}.` : "";
  return `<div class="setting" data-row="${esc(i.key)}">
    <label for="${id}">${esc(i.label)} <span class="changed" hidden>changed</span></label>
    <div class="control">${input}</div>
    <p class="fhelp" id="${id}-h">${esc(i.help)}${esc(range)}${showEffect ? ` <span class="effect">${rich(i.effect)}</span>` : ""}</p>
    <p class="fmeta">Default: ${esc(defaultText(i))} · <code>${esc(i.key)}</code> <button type="button" class="link" data-reset="${esc(i.key)}" aria-label="${esc("Reset " + i.label + " to its default")}">Reset to default</button></p>
  </div>`;
}
function readSetting(inp) {
  const raw = inp.value.trim(), unit = inp.dataset.unit, kind = inp.dataset.kind;
  if (kind === "list") return raw.split(",").map((x) => Number(x.trim())).filter((x) => !Number.isNaN(x));
  if (unit === "date" || inp.tagName === "SELECT") return raw;
  if (raw === "") return null;
  return unit === "share" ? Number((Number(raw) / 100).toFixed(8)) : Number(raw);
}

pages.settings = {
  title: "Settings",
  async html() {
    const [s, st] = await Promise.all([get("/api/settings"), get("/api/strategies")]);
    pages.settings.ctx = {s, st};
    const groups = Object.entries(s.groups).map(([g, items]) => {
      const effects = [...new Set(items.map((i) => i.effect))];
      return `<section class="card" aria-label="${esc(g)}"><h2>${esc({"Costs and fills": "Costs and what can be traded", "Regime and universe": "Market mood and tradable skins", "Report windows": "Test periods and amounts"}[g] || g)}</h2>
        <p>${esc(s.group_intros[g] || "")}</p>
        ${effects.length === 1 ? `<p class="note"><b>If you change these:</b> ${rich(effects[0])}</p>` : ""}
        <div class="settings">${items.map((i) => settingField(i, effects.length > 1)).join("")}</div>
        ${g === "Report windows" ? `<div class="banner amber" id="holdwarn" hidden><b>You changed the start of the final test period.</b> ${esc(s.holdout_warning)}
          <label class="check"><input type="checkbox" id="holdok"> I understand; save it anyway.</label></div>` : ""}
      </section>`;
    }).join("");
    return `${pageHead("Settings", `The assumptions behind every result. They are saved to the file <code>cs2.toml</code> (the previous version is kept as <code>cs2.toml.bak</code>). Passwords and keys live in a separate file and are never shown or edited here.`)}
    <div id="savebar" class="savebar" role="region" aria-label="Save changes"><span id="smsg" aria-live="polite">No unsaved changes.</span>
      <span><button type="button" class="ghost" id="discard" disabled>Discard</button> <button type="button" class="primary" id="save" disabled>Save changes</button></span></div>
    ${groups}
    <section class="card" aria-label="Fixed verdict rule"><h2>The pass / fail rule <span class="badge lock">fixed</span></h2>
      <p>${esc(s.read_only_why)}</p>
      <dl class="tech">${s.read_only.map((r) => `<dt>${esc(r.label)}</dt><dd><b>${esc(r.value)} ${esc(UNIT_SUFFIX[r.unit] || "")}</b> <span class="muted">${esc(r.help)} <code>${esc(r.key)}</code></span></dd>`).join("")}</dl>
      <p class="muted small">Plus the ${term("deflated_sharpe", "luck check")}, which has no adjustable number. See ${term("cost_stress")}.</p></section>
    <section class="card" aria-label="Strategy presets"><h2>Your preferred strategy settings</h2>
      <p>These only pre-fill the “Try a strategy” form. The report ignores them: it always tests every combination listed for a strategy, so it cannot be steered from here.</p>
      <label class="field">Strategy <select id="sd-strategy">${st.strategies.filter((x) => x.params.length).map((x) => `<option value="${esc(x.name)}">${esc(x.label)}</option>`).join("")}</select></label>
      <div id="sd-fields"></div>
      <div class="actions"><button type="button" class="primary" id="sd-save">Save as my defaults</button><span id="sdmsg" aria-live="polite"></span></div></section>`;
  },
  after(root) {
    const {s, st} = pages.settings.ctx;
    const inputs = () => $$("[data-key]", root);
    const changes = () => {
      const out = {};
      inputs().forEach((inp) => { const v = readSetting(inp); if (JSON.stringify(v) !== inp.dataset.orig) out[inp.dataset.key] = v; });
      return out;
    };
    const msg = $("#smsg", root);
    const refresh = (keep) => {
      const ch = changes(), n = Object.keys(ch).length;
      inputs().forEach((inp) => {
        const row = inp.closest(".setting");
        $(".changed", row).hidden = !(inp.dataset.key in ch);
        $("[data-reset]", row).hidden = JSON.stringify(readSetting(inp)) === inp.dataset.default;
      });
      const hold = "wf.holdout_start" in ch;
      $("#holdwarn", root).hidden = !hold;
      $("#save", root).disabled = !n || (hold && !$("#holdok", root).checked);
      $("#discard", root).disabled = !n;
      $("#savebar", root).classList.toggle("dirty", n > 0);
      if (!keep) msg.textContent = n ? `${n} unsaved change${n > 1 ? "s" : ""}.${hold && !$("#holdok", root).checked ? " Confirm the warning below to save." : ""}` : "No unsaved changes.";
    };
    on(root, "input", "[data-key], #holdok", () => refresh());
    on(root, "change", "[data-key], #holdok", () => refresh());
    on(root, "click", "[data-reset]", (e, b) => {
      const inp = $(`[data-key="${b.dataset.reset}"]`, root);
      inp.value = shownValue(JSON.parse(inp.dataset.default), inp.dataset.unit);
      refresh(); inp.focus();
    });
    $("#discard", root).addEventListener("click", () => {
      inputs().forEach((inp) => { inp.value = shownValue(JSON.parse(inp.dataset.orig), inp.dataset.unit); });
      $("#holdok", root).checked = false; refresh();
    });
    $("#save", root).addEventListener("click", async () => {
      const ch = changes();
      if (Object.values(ch).some((v) => v === null)) { msg.innerHTML = `<span class="bad">A field is empty. Fill it in or reset it to its default.</span>`; return; }
      const r = await post("/api/settings", {changes: ch});
      if (r.body.ok) {
        await renderPage(pages.settings);
        window.scrollTo(0, 0);
        $("#smsg").innerHTML = `<span class="good">✓ ${esc(r.body.note)}</span> Results made earlier keep their old ${term("run_id")}; re-run the report to judge strategies under the new settings.`;
        return;
      }
      // Server errors name the config key and raw fractions; show the field's label and its on-screen unit.
      const byKey = Object.fromEntries(Object.values(s.groups).flat().map((i) => [i.key, i]));
      const friendly = (e) => {
        const m = /^([\w.]+): (.*)$/.exec(String(e)), f = m && byKey[m[1]];
        if (!f) return String(e);
        const range = f.min != null && /between/.test(m[2]) ? `must be between ${shownValue(f.min, f.unit)} and ${shownValue(f.max, f.unit)}${UNIT_SUFFIX[f.unit] ? " " + UNIT_SUFFIX[f.unit] : ""}` : m[2];
        return `${f.label}: ${range}`;
      };
      msg.innerHTML = `<span class="bad"><b>Not saved, nothing was changed.</b> ${(r.body.errors || [r.body.error]).map((e) => esc(friendly(e))).join("; ")}</span>`;
    });
    refresh();

    const sel = $("#sd-strategy", root);
    const fill = () => { $("#sd-fields", root).innerHTML = paramFields(stratByName(st, sel.value), st.defaults[sel.value] || {}); $("#sdmsg", root).textContent = ""; };
    sel.addEventListener("change", fill); fill();
    $("#sd-save", root).addEventListener("click", async () => {
      const params = readParams($("#sd-fields", root));
      const r = await post("/api/strategy-defaults", {strategy: sel.value, params});
      $("#sdmsg", root).innerHTML = r.body.ok ? `<span class="good">✓ Saved. The Try a strategy form will start from these.</span>` : `<span class="bad">Not saved: ${(r.body.errors || [r.body.error]).map(esc).join("; ")}</span>`;
      if (r.body.ok) st.defaults[sel.value] = params;
    });
  },
};

/* =============================== Glossary =============================== */
pages.glossary = {
  title: "Glossary",
  async html() {
    const items = Object.entries(GLOSSARY).sort((a, b) => a[1][0].localeCompare(b[1][0]));
    return `${pageHead("Glossary", "Every special word used in this app, in plain language. The same explanations appear when you click an underlined word or a “?” anywhere else.")}
    <label class="field" for="gsearch">Filter</label><input id="gsearch" type="search" placeholder="e.g. holdout, fee, dip" autocomplete="off">
    <dl class="glossary card">${items.map(([k, g]) => `<div class="gitem" id="g-${esc(k)}"><dt>${esc(g[0])}</dt><dd>${esc(g[1])}</dd></div>`).join("")}</dl>
    <p class="muted" id="gnone" hidden>No term matches. Try a shorter word.</p>`;
  },
  after(root) {
    $("#gsearch", root).addEventListener("input", (e) => {
      const q = e.target.value.trim().toLowerCase();
      let n = 0;
      $$(".gitem", root).forEach((el) => { el.hidden = !!q && !el.textContent.toLowerCase().includes(q); if (!el.hidden) n++; });
      $("#gnone", root).hidden = n > 0;
    });
  },
};
