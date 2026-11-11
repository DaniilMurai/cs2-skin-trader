"use strict";
/* Router and page shell. Routes: #/home, #/verdict[/day], #/runs[/run_id | /new/strategy], #/market,
   #/collector, #/settings, #/glossary. */
const pageEl = document.getElementById("page");
let routeSeq = 0;

async function renderPage(p, arg, arg2) {
  const seq = ++routeSeq;
  try {
    const html = await p.html(arg, arg2);
    if (seq !== routeSeq) return;  // the user already went elsewhere
    pageEl.innerHTML = html;
    if (p.after) p.after(pageEl, arg, arg2);
  } catch (e) {
    if (seq !== routeSeq) return;
    pageEl.innerHTML = `<h1 tabindex="-1">This page could not be loaded</h1>
      <div class="banner red" role="alert"><p>The app could not get its data. Check that <code>cs2 ui</code> is still running in the terminal, then try again.</p>
      <p class="muted small">Detail: ${esc(e.message)}</p><button type="button" class="ghost" data-act="reload">Try again</button></div>`;
  }
}

async function route() {
  const parts = location.hash.split("/").map(decodeURIComponent);
  const name = pages[parts[1]] ? parts[1] : "home";
  document.querySelectorAll("nav a").forEach((a) => {
    if (a.getAttribute("href") === "#/" + name) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  });
  document.title = `${pages[name].title} · CS2 skin research`;
  pageEl.innerHTML = `<div class="skel title"></div>${skeleton(name === "home" ? 4 : 3)}`;
  await renderPage(pages[name], parts[2], parts[3]);
  const h1 = pageEl.querySelector("h1");
  // Move keyboard and screen-reader focus to the new page, but not on the first load.
  if (h1 && route.visited) h1.focus({preventScroll: true});
  if (route.visited) window.scrollTo(0, 0);
  route.visited = true;
}

async function loadFooter() {
  const el = document.getElementById("datafoot");
  try {
    const o = await get("/api/overview");
    el.innerHTML = o.state === "ok"
      ? `Price history up to <b>${esc(fmtDate(o.last_day))}</b> · ${fmtNum(o.items_with_history)} items · ${term("data_version")} <code>${esc(o.data_version)}</code>`
      : esc(o.state === "busy" ? "The database is busy right now; data details will appear when it is free." : (o.message || ""));
  } catch (e) { el.textContent = ""; }
}

document.addEventListener("click", (e) => {
  if (e.target.closest && e.target.closest("[data-act=reload]")) route();
});
window.addEventListener("hashchange", () => { route(); loadFooter(); });
route();
loadFooter();
