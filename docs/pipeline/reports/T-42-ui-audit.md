Status: done

# T-42 — stage-8 audit of `src/cs2trader/ui/` (2026-11-13, branch `claude/awesome-feynman-52b9f3`, head 2647a5c)

Method: `nice -n 19 uv run pytest tests/ -q -k ui` → `35 passed, 238 deselected in 19.83s`; then `cs2 ui --port 18765`
in a scratch dir (12 synthetic items × 420 days, a fixture report, 26 collector hours, a `.env` with four dummy
secrets) and `--port 18766` in an empty dir, probed with curl and the built-in browser. Both servers stopped, no
process left. No source file, `tasks.md` or `state.md` was edited; nothing was pushed.

| AC | Verdict | Command → verdict line |
|----|---------|------------------------|
| AC-15 | pass (synthetic data) | browser walk of 9 routes at 1440 and 1920 px → `overflowEls 0`, no console errors, footer `Price history up to Feb 23, 2025 · 12 items · data version 97f63a6bcfcc7247` on every page; `GET /api/verdict` → `hash_matches_report: true`; empty dir → every route `200 {"state": "empty", …}`, no red banner |
| AC-16 | **fail** (one clause) | `POST /api/settings {"changes":{"capitals":[1e999]}}` → `200 ok`, file now has `capitals = [inf]` (`[NaN]` → `capitals = [nan]`). The rest holds: `verdict.cost_stress`, `STEAM_LOGIN_SECURE`, `data_dir`, `mc.data_repo` → `422 not editable here`; out-of-range → 422 with `cs2.toml` sha unchanged; valid save writes the file and `.bak`; `.env` sha unchanged; holdout warning with a confirm box blocks Save |
| AC-17 | **fail** (one clause) | 6 simultaneous `POST /api/jobs` → `202 202 202 202 202 202`, `ps` shows 6 backtest processes. The rest holds: sequential second POST → `409 busy: a job is already running`; external writer on the DuckDB file → `409 busy: the database is in use`, pages show the amber "database is busy" banner; UI run and `cs2 backtest` both give `run 8951875c896a2767`, cash 1000.00 → 930.17; job outlives the UI process (PPID 1) |
| AC-18 | pass, with a hole in the safety net (M2) | `lsof` → `TCP 127.0.0.1:18765 (LISTEN)`, LAN IP and `[::1]` refuse; POST without / with wrong token on all 3 POST routes → 403; `Host: evil.example`, `127.0.0.1:18765.evil.example`, `[::1]:18765` → 403; 18 traversal paths (`/static/..%2f..%2f.env`, `/reports/../.env`, `/api/run/../../etc/passwd`, …) → 404/400; 23 GET responses grepped for the four dummy values → 0 unsolicited hits; `/api/order|buy|sell|trade|cancel|place|withdraw` GET+POST → 404 |

T-43's unverified states, now checked in the browser: **busy** (banner on Home/Try/Market, footer text, form answers
"Busy."), **failed job** (`role="alert"` card "There is no price data for those dates" + log), **no collector data**
("No collector data yet"), **keyboard only** (30 Tabs: every control reached, `outline: solid 2px`; backtest started
with Enter and landed on the result), **widths** 1024 / 1440 / 1920 (content capped at 1080 px, no overflow).

## Findings

Critical: none.

Major
- M1 `ui/jobs.py:59-71` — the "is a job running" check and the `Popen` are not under one lock, so parallel starts all
  pass (AC-17 "only one job at a time"). Repro: six background `curl -X POST … /api/jobs` with the token. Two tabs or
  a double submit can hit it; `tests/test_ui.py:272` accepts `202` for the second job, so no test guards it.
- M2 `ui/security.py:7` — the response filter knows 2 of the 4 secrets in `config.py:12`; `CF_WORKER_URL` and
  `CF_WORKER_TOKEN` are never redacted. Repro: `GET /api/market/item?name=<CF token value>` echoes it, the same call
  with the Steam value returns `[redacted]`. No route was found that emits them unprompted, but error bodies echo
  exception text (`server.py:80,151`), and `tests/test_ui.py:101` only sets the two old names.
- M3 `ui/jobs.py:66` — a UI job runs at normal priority (`ps` → `NI 0`), in its own session, and the server has no stop
  route; after the UI exits it keeps running with PPID 1. A long-range heavy strategy started from the form can only
  be stopped with `kill` — against the "do not make the Mac lag" rule. Repro: scratch probe with a sleeping job.
- M4 `ui/settings_api.py:88-90` — `capitals` accepts `inf`/`NaN` and writes them (AC-16 "invalid value … nothing is
  written"); the next `cs2 report` would run on them. Repro in the table.

Minor
- m1 `ui/settings_api.py:77` — `"changes": [1]` or `"x"` → `500 AttributeError` instead of 400/422.
- m2 `ui/settings_api.py:141-142` — a save with no changes still rewrites `cs2.toml` and overwrites `.bak` with the
  current file, so the real previous version is lost; every save also drops hand-written comments.
- m3 `ui/server.py:124` — `Content-Length: -1` (with a valid token) blocks a handler thread forever; `abc` drops the
  connection. `server.py:103,106` — `?days=abc` → `500 ValueError: invalid literal…`.
- m4 `ui/static/pages.js:271` — opened while the database is busy, the Run form says "Price history is available up
  to <today>" and pre-fills dates up to today (seen: "Nov 13, 2026" on data ending Feb 23, 2025).
- m5 After submitting the Run form by keyboard, focus falls to `<body>` (the button is disabled and re-enabled).
- m6 `ui/security.py:19,24` — secrets shorter than 8 characters, or ones JSON would escape, pass the filter.
- m7 `cs2 ui` prints its URL only when stdout is a terminal (empty log under `nohup`); a GET in an empty folder
  creates `data/`. No `Origin` check — harmless today because the token is required.

## Tasks marked `[x]` whose DoD does not hold as written
- T-36, T-37, T-38, T-40, T-41: the DoD names `tests/test_ui_security.py`, `_api.py`, `_pages.py`, `_settings.py`,
  `_jobs.py`; none exists, everything is in `tests/test_ui.py` (the T-43 rewrite). Cosmetic, but the DoD commands fail.
- T-41: "a second job while one runs → busy" is not asserted (`test_ui.py:272`), and it is false under concurrency
  (M1); "a killed browser connection does not stop it" is tested without any connection (`test_ui.py:284`).
- T-36: "with `.env` values set no response body contains them" holds only for two of four names (M2); the route test
  (`test_ui.py:116`) greps `server.py` text rather than asking the running server.
- T-38/T-39: "`tests/test_ui_pages.py` green on a fixture run" / "a fixture test covers the empty state" — there are
  no page-render tests, only API payload tests and "static files are served".

## Not checked
Real data (the worktree has no `data/`): page speed and memory on the full DuckDB file, `/api/market/categories` on
the real universe, the real `report.html` under the UI's `Content-Security-Policy`, a real collector/backfill holding
the lock (simulated with a plain DuckDB writer), a real Ctrl-C of `cs2 ui` mid-job, a screen reader, widths 800–1023.

## Proposed tasks
- Fix M1: take the slot and spawn under one lock. DoD: a test fires 6 parallel POSTs → exactly one 202, five 409.
- Fix M2: build the filter from every `SecretName` in `config.py`. DoD: the secrets test sets all four and also checks
  POST and 4xx/5xx bodies.
- Fix M3: start jobs with `nice 19`, add `POST /api/jobs/stop` (token) and a Stop button. DoD: test shows the child's
  niceness is 19 and that stop ends it with state `stopped`.
- Fix M4 + m1: reject non-finite numbers and non-object `changes`. DoD: tests for `inf`, `NaN`, `[1]` → 422, file unchanged.
- Fix m2/m3: skip the write when nothing changed; validate `Content-Length` and query ints → 400. DoD: tests for each.
- Fix m4/m5: no date claim while busy; return focus to the Run button. DoD: checked in the browser with the lock held.
- Tidy group H DoD lines to `tests/test_ui.py` and tighten `test_ui.py:272` to `== 409`. DoD: every DoD command runs.
