import http.client
import json
import os
import sys
import threading
import time
import typing
import urllib.parse
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import polars as pl
import pytest
from test_benchmarks import D0, FILLS, FakeStore, bars

from cs2trader.backtest.engine import run
from cs2trader.backtest.runs import save_run
from cs2trader.config import load_settings
from cs2trader.models import FxRate, RunSpec, SteamPoint
from cs2trader.store import Store
from cs2trader.ui import jobs, settings_api
from cs2trader.ui.jobs import JobRunner
from cs2trader.ui.security import TOKEN_HEADER, scrub, secret_values
from cs2trader.ui.server import App, start_in_thread

SECRET = "SECRET-cookie-value-123456"
SECRETS = {"STEAM_LOGIN_SECURE": SECRET, "MARKETCSGO_API_KEY": "abcdefgh-key-98765",
           "CF_WORKER_URL": "https://buffer-worker.example.workers.dev", "CF_WORKER_TOKEN": "cf-token-value-24680"}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for name in SECRETS:
        monkeypatch.delenv(name, raising=False)
    (tmp_path / ".env").write_text("".join(f"{k}={v}\n" for k, v in SECRETS.items()))
    (tmp_path / "cs2.toml").write_text('data_dir = "data"\nreports_dir = "reports"\n\n[mc]\ninterval_s = 2.5\n')
    data = tmp_path / "data"
    store = Store(data)
    fx = FxRate(date(2024, 1, 1), "UAH", 40.0, "test", False)
    pts = [SteamPoint("AK-47 | Redline (Field-Tested)", datetime(2024, 1, 1, tzinfo=UTC) + timedelta(days=d),
                      400.0 + d, "UAH", 5, "day") for d in range(40)]
    store.upsert_steam(pts, fx)
    store.db.execute("INSERT INTO items (name, category) VALUES ('AK-47 | Redline (Field-Tested)', 'weapon_skin')")
    store.db.execute("INSERT INTO regimes VALUES (DATE '2024-01-10', 100.0, 0.0, 'flat'), "
                     "(DATE '2024-01-11', 90.0, -0.1, 'falling')")
    store.close()
    res = run(RunSpec("bh_universe", {}, 100.0, D0, D0 + timedelta(30), fills=FILLS), FakeStore(bars()))
    save_run(res, data / "runs")
    day = tmp_path / "reports" / "2026-01-01"
    day.mkdir(parents=True)
    results = pl.DataFrame([{"strategy": "cash", "capital": 100.0, "period": "holdout", "regime": "all",
                             "unit": "cash", "net_return": -0.05, "max_drawdown": 0.0, "n_trades": 0,
                             "fees_paid": 0.0, "sharpe": 0.0}])
    results.write_parquet(day / "results.parquet")
    from cs2trader.report import results_hash
    (day / "verdicts.json").write_text(json.dumps({
        "summary": "No strategy holds up", "results_hash": results_hash(results), "capitals": [100.0],
        "verdicts": [{"strategy": "x", "capital": 100.0, "status": "fails", "first_broken_rule": "beats_cash",
                      "deflated_sharpe": -1.0, "n_variants": 3}]}))
    (day / "report.html").write_text("<html>report</html>")
    app = App(load_settings(tmp_path / "cs2.toml"), tmp_path / "cs2.toml", tmp_path / ".env")
    server, _ = start_in_thread(app)
    yield app, res
    server.shutdown()
    server.server_close()


def call(app, method, path, body=None, token=True, host=None):
    conn = http.client.HTTPConnection("127.0.0.1", app.port, timeout=10)
    headers = {"Host": host or f"127.0.0.1:{app.port}"}
    if token:
        headers[TOKEN_HEADER] = app.token
    if body is not None:
        headers["Content-Type"] = "application/json"
    conn.request(method, path, json.dumps(body) if body is not None else None, headers)
    r = conn.getresponse()
    raw = r.read()
    conn.close()
    return r.status, raw


# ---- AC-18: security -------------------------------------------------------------------------

def test_binds_loopback_only(env):
    app, _ = env
    s = start_in_thread(app)[0]
    try:
        assert s.server_address[0] == "127.0.0.1"
    finally:
        s.shutdown()
        s.server_close()


def test_post_needs_the_token(env):
    app, _ = env
    assert call(app, "POST", "/api/settings", {"changes": {}}, token=False)[0] == 403
    conn = http.client.HTTPConnection("127.0.0.1", app.port)
    conn.request("POST", "/api/settings", "{}", {"X-CS2-Token": "wrong", "Host": f"127.0.0.1:{app.port}"})
    assert conn.getresponse().status == 403
    assert call(app, "POST", "/api/settings", {"changes": {}})[0] == 200


def test_foreign_host_header_is_refused(env):
    app, _ = env
    assert call(app, "GET", "/api/overview", host="evil.example:80")[0] == 403


def test_no_response_contains_a_secret_value(env):
    app, _ = env
    paths = ["/", "/api/overview", "/api/verdict", "/api/runs", "/api/market/index", "/api/collector",
             "/api/strategies", "/api/settings", "/api/jobs", "/api/home", "/static/app.js", "/static/ui.js",
             "/static/pages.js", "/static/style.css", "/reports/2026-01-01/report.html"]
    from cs2trader.config import SecretName
    assert set(SECRETS) == set(typing.get_args(SecretName))
    assert sorted(secret_values(app.env_path)) == sorted(SECRETS.values())
    for path in paths:
        _, body = call(app, "GET", path)
        for value in SECRETS.values():
            assert value.encode() not in body, path
    # the filter itself: a payload that does contain one is redacted
    assert SECRET.encode() not in scrub(f"x {SECRET} y".encode(), secret_values(app.env_path))


def test_post_and_error_bodies_redact_every_secret(env, monkeypatch):
    app, _ = env
    for name, value in SECRETS.items():
        echoed = urllib.parse.quote(value, safe="")
        # 200: the item name comes back in the payload
        status, body = call(app, "GET", f"/api/market/item?name={echoed}")
        assert status == 200 and value.encode() not in body and b"[redacted]" in body, name
        # 4xx on GET and on POST: the error text quotes what was sent
        status, body = call(app, "GET", f"/api/run/{echoed}")
        assert 400 <= status < 500 and value.encode() not in body, name
        status, body = call(app, "POST", "/api/jobs", {"strategy": value, "capital": 1, "start": "2024-01-01",
                                                       "end": "2024-02-01"})
        assert status == 400 and value.encode() not in body and b"[redacted]" in body, name
        status, body = call(app, "POST", "/api/settings", {"changes": {value: 1}})
        assert status == 422 and value.encode() not in body and b"[redacted]" in body, name
        # 5xx: exception text is echoed by both handlers
        status, body = call(app, "GET", f"/api/collector?hours={echoed}")
        assert status == 500 and value.encode() not in body and b"[redacted]" in body, name

    def boom(*a, **k):
        raise RuntimeError(" ".join(SECRETS.values()))
    monkeypatch.setattr(app.jobs, "start_backtest", boom)
    status, body = call(app, "POST", "/api/jobs", {})
    assert status == 500 and body.count(b"[redacted]") == 4
    for value in SECRETS.values():
        assert value.encode() not in body


def test_route_list_has_no_order_endpoint():
    import re
    src = (Path(__file__).parents[1] / "src/cs2trader/ui/server.py").read_text()
    routes = re.findall(r'"(/api/[^"]+)"', src)
    assert routes and not [r for r in routes if re.search(r"order|buy|sell|trade|cancel", r)]


def test_page_embeds_the_token_and_posts_need_it(env):
    app, _ = env
    _, page = call(app, "GET", "/")
    assert app.token.encode() in page


# ---- AC-15: read API -------------------------------------------------------------------------

def test_verdict_payload_matches_report_hash(env):
    app, _ = env
    status, body = call(app, "GET", "/api/verdict")
    v = json.loads(body)
    assert status == 200 and v["state"] == "ok" and v["hash_matches_report"] is True
    assert v["verdicts"][0]["first_broken_rule"] == "beats_cash" and v["last_day"] and v["data_version"]


def test_run_detail_and_list(env):
    app, res = env
    runs = json.loads(call(app, "GET", "/api/runs")[1])["runs"]
    assert runs[0]["run_id"] == res.run_id
    d = json.loads(call(app, "GET", f"/api/run/{res.run_id}")[1])
    assert d["equity"] and "drawdown_cash" in d["equity"][0] and d["n_trades"] >= 1
    assert call(app, "GET", "/api/run/../../etc/passwd")[0] == 400
    assert call(app, "GET", "/api/run/" + "0" * 16)[0] == 404


def test_market_item_and_index(env):
    app, _ = env
    idx = json.loads(call(app, "GET", "/api/market/index")[1])
    assert idx["state"] == "ok" and idx["points"][-1]["regime"] == "falling"
    names = json.loads(call(app, "GET", "/api/market/search?q=redline")[1])["names"]
    assert names == ["AK-47 | Redline (Field-Tested)"]
    item = json.loads(call(app, "GET", "/api/market/item?name=" + names[0].replace(" ", "%20"))[1])
    assert item["state"] == "ok" and len(item["points"]) == 40


def test_empty_state_instead_of_error(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = App(load_settings(tmp_path / "cs2.toml"), tmp_path / "cs2.toml", tmp_path / ".env")
    server, _ = start_in_thread(app)
    try:
        for path in ("/api/overview", "/api/verdict", "/api/runs", "/api/market/index", "/api/collector"):
            status, body = call(app, "GET", path)
            assert status == 200 and json.loads(body)["state"] == "empty", path
    finally:
        server.shutdown()
        server.server_close()


def test_busy_flag_while_another_process_writes(env):
    app, _ = env
    import subprocess
    code = ("import duckdb,sys,time; c=duckdb.connect(sys.argv[1]); print('locked', flush=True); time.sleep(30)")
    p = subprocess.Popen([sys.executable, "-c", code, str(app.ctx.data_dir / "cs2.duckdb")],
                         stdout=subprocess.PIPE, text=True)
    try:
        assert p.stdout.readline().strip() == "locked"
        v = json.loads(call(app, "GET", "/api/overview")[1])
        assert v["state"] == "busy"
        status, body = call(app, "POST", "/api/jobs", {"strategy": "cash", "capital": 100, "start": "2024-01-01",
                                                      "end": "2024-02-01"})
        assert status == 409 and "busy" in json.loads(body)["error"]
    finally:
        p.kill()
        p.wait()


# ---- AC-16: settings -------------------------------------------------------------------------

def test_invalid_setting_is_rejected_and_file_unchanged(env):
    app, _ = env
    before = (app.config_path).read_text()
    status, body = call(app, "POST", "/api/settings", {"changes": {"fills.spread_penalty": 5.0}})
    assert status == 422 and "between" in json.loads(body)["errors"][0]
    status, _ = call(app, "POST", "/api/settings", {"changes": {"steam.interval_s": 0.1}})
    assert status == 422
    assert app.config_path.read_text() == before


def test_valid_setting_is_written_with_backup(env):
    app, _ = env
    old = app.config_path.read_text()
    status, body = call(app, "POST", "/api/settings", {"changes": {"fills.spread_penalty": 0.04,
                                                                    "wf.holdout_start": "2025-11-01",
                                                                    "capitals": [100, 500]}})
    assert status == 200 and json.loads(body)["ok"]
    s = load_settings(app.config_path)
    assert s.fills.spread_penalty == 0.04 and s.wf.holdout_start == date(2025, 11, 1) and s.capitals == [100.0, 500.0]
    assert (app.config_path.with_suffix(".toml.bak")).read_text() == old
    assert app.ctx.settings.fills.spread_penalty == 0.04
    assert "[mc]" in app.config_path.read_text()


def test_verdict_thresholds_and_secrets_are_not_writable(env):
    app, _ = env
    for key in ("verdict.cost_stress", "steam.interval_s", "data_dir", "STEAM_LOGIN_SECURE"):
        status, _ = call(app, "POST", "/api/settings", {"changes": {key: 1}})
        assert status == 422, key
    s = json.loads(call(app, "GET", "/api/settings")[1])
    assert {r["key"] for r in s["read_only"]} == {"verdict.cost_stress", "verdict.spread_stress"}
    assert "STEAM" not in json.dumps(s)


def test_dates_must_be_ordered():
    _, errors = settings_api.validate(load_settings(Path("/nonexistent.toml")), {"wf.holdout_start": "2020-01-01"})
    assert errors


def test_strategy_defaults_validate_keys(env):
    app, _ = env
    assert call(app, "POST", "/api/strategy-defaults", {"strategy": "cash", "params": {"nope": 1}})[0] == 422
    assert call(app, "POST", "/api/strategy-defaults", {"strategy": "cash", "params": {}})[0] == 200


# ---- AC-17: jobs -----------------------------------------------------------------------------

def wait_job(app):
    for _ in range(150):
        j = json.loads(call(app, "GET", "/api/jobs")[1])
        if j["state"] != "running":
            return j
        time.sleep(0.2)
    raise AssertionError("job did not finish")


def test_job_gives_the_same_run_id_as_the_cli_path(env):
    from cs2trader.evaluate import make_spec
    app, _ = env
    form = {"strategy": "cash", "capital": 100, "start": "2024-01-05", "end": "2024-02-05"}
    assert call(app, "POST", "/api/jobs", form)[0] == 202
    j = wait_job(app)
    assert j["state"] == "done" and j["run_id"]
    store = Store(app.ctx.data_dir)
    try:
        spec = make_spec("cash", {}, 100.0, date(2024, 1, 5), date(2024, 2, 5), app.ctx.settings)
        assert run(spec, store, app.ctx.settings).run_id == j["run_id"]
    finally:
        store.close()
    d = json.loads(call(app, "GET", f"/api/run/{j['run_id']}")[1])
    assert d["equity"]


def test_failed_job_reports_the_error_and_second_job_is_refused_while_running(env):
    app, _ = env
    form = {"strategy": "cash", "capital": 100, "start": "2010-01-01", "end": "2010-02-01"}
    assert call(app, "POST", "/api/jobs", form)[0] == 202
    status2, _ = call(app, "POST", "/api/jobs", form)
    j = wait_job(app)
    assert j["state"] == "failed" and any("no bars" in line for line in j["lines"])
    assert status2 in (202, 409)


SLEEPER = "import time; print('started', flush=True); time.sleep(120)"


def test_parallel_starts_take_one_slot(env, monkeypatch):
    app, _ = env
    monkeypatch.setattr(jobs, "LAUNCH", SLEEPER)
    form = {"strategy": "cash", "capital": 100, "start": "2024-01-05", "end": "2024-02-05"}
    gate, statuses = threading.Barrier(6), []

    def fire():
        gate.wait()
        statuses.append(call(app, "POST", "/api/jobs", form)[0])
    threads = [threading.Thread(target=fire) for _ in range(6)]
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert sorted(statuses) == [202, 409, 409, 409, 409, 409]
    finally:
        app.jobs.stop()


def test_job_runs_at_nice_19_and_stop_ends_it(env, monkeypatch):
    app, _ = env
    monkeypatch.setattr(jobs, "LAUNCH", SLEEPER)
    assert call(app, "POST", "/api/jobs/stop", {}, token=False)[0] == 403
    assert call(app, "POST", "/api/jobs/stop", {})[0] == 409
    form = {"strategy": "cash", "capital": 100, "start": "2024-01-05", "end": "2024-02-05"}
    assert call(app, "POST", "/api/jobs", form)[0] == 202
    pid = app.jobs.job["proc"].pid
    try:
        # the child lowers its own priority first thing; its first output line proves it got that far
        for _ in range(100):
            if json.loads(call(app, "GET", "/api/jobs")[1])["lines"]:
                break
            time.sleep(0.1)
        assert os.getpriority(os.PRIO_PROCESS, pid) == 19
        status, body = call(app, "POST", "/api/jobs/stop", {})
        assert status == 200 and json.loads(body)["state"] == "stopped"
        assert json.loads(call(app, "GET", "/api/jobs")[1])["state"] == "stopped"
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
        # the slot is free again
        assert call(app, "POST", "/api/jobs", form)[0] == 202
    finally:
        app.jobs.stop()


def test_job_form_validation(env):
    app, _ = env
    for form in ({"strategy": "nope", "capital": 100, "start": "2024-01-01", "end": "2024-02-01"},
                 {"strategy": "cash", "capital": -1, "start": "2024-01-01", "end": "2024-02-01"},
                 {"strategy": "cash", "capital": 100, "start": "2024-02-01", "end": "2024-01-01"},
                 {"strategy": "cash", "params": {"x": 1}, "capital": 100, "start": "2024-01-01", "end": "2024-02-01"}):
        assert call(app, "POST", "/api/jobs", form)[0] == 400


def test_job_survives_without_a_client(tmp_path):
    runner = JobRunner(tmp_path)
    runner.start_backtest({"strategy": "cash", "capital": 100, "start": "2010-01-01", "end": "2010-02-01"})
    for _ in range(100):
        if runner.snapshot()["state"] != "running":
            break
        time.sleep(0.2)
    assert runner.snapshot()["state"] in ("failed", "done")


# ---- plain-language layer (UI redesign): every sentence comes from the data --------------------

def _results(rows):
    base = {"regime": "all", "unit": "cash", "n_trades": 3, "fees_paid": 1.0, "sharpe": 0.1}
    return pl.DataFrame([{**base, "strategy": s, "capital": c, "period": p, "net_return": r, "max_drawdown": d}
                         for s, c, p, r, d in rows])


def test_rule_texts_cover_exactly_the_verdict_rules():
    import re

    from cs2trader.ui import plain
    src = (Path(__file__).parents[1] / "src/cs2trader/evaluate.py").read_text()
    body = src[src.index("def verdict("):]
    assert re.findall(r'\("(\w+)", ', body[:body.index("broken =")]) == list(plain.RULES)


def test_scoreboard_translates_the_first_broken_rule_and_never_flatters():
    from cs2trader.config import Settings
    from cs2trader.ui import plain
    results = _results([("cash", 100.0, "holdout", -0.05, 0.0), ("bh_universe", 100.0, "holdout", 0.10, 0.20),
                        ("a", 100.0, "holdout", -0.466, 0.5), ("b", 100.0, "holdout", 0.30, 0.1),
                        ("b", 100.0, "holdout_stress", 0.20, 0.1), ("c", 100.0, "holdout", 0.05, 0.1)])
    verdicts = [{"strategy": "a", "capital": 100.0, "status": "fails", "first_broken_rule": "beats_cash",
                 "deflated_sharpe": -1.0, "n_variants": 3},
                {"strategy": "b", "capital": 100.0, "status": "holds_up", "first_broken_rule": None,
                 "deflated_sharpe": 0.4, "n_variants": 8},
                {"strategy": "c", "capital": 100.0, "status": "fails", "first_broken_rule": "beats_bh",
                 "deflated_sharpe": 0.2, "n_variants": 2}]
    (board,) = plain.scoreboard(results, verdicts, Settings())
    cards = {c["strategy"]: c for c in board["cards"]}
    assert board["cards"][0]["strategy"] == "b"  # the passing one is listed first
    assert cards["a"]["reason"] == "Did worse than just holding cash."
    assert "$100 became $53.40 (−46.6\u00a0%)" in cards["a"]["result"]
    assert cards["c"]["reason"] == "Did worse than buying and holding a basket of skins."
    assert [k["ok"] for k in cards["c"]["checks"]][:2] == [True, False]
    assert all(k["ok"] for k in cards["b"]["checks"]) and cards["b"]["reason"] == "Passed all five checks."
    assert board["holds"] == ["b"] and board["benchmarks"]["cash"]["end_value"] == pytest.approx(95.0)
    assert plain.verdict_headline([board], True)["status"] == "some"
    # Without verdicts nothing is called passing or failing.
    (bare,) = plain.scoreboard(results, None, Settings())
    assert {c["status"] for c in bare["cards"]} == {None}
    assert plain.verdict_headline([bare], False)["status"] == "unknown"


def test_every_rule_code_has_a_failure_sentence():
    from cs2trader.config import Settings
    from cs2trader.ui import plain
    results = _results([("cash", 100.0, "holdout", -0.05, 0.0), ("bh_universe", 100.0, "holdout", 0.1, 0.2),
                        ("x", 100.0, "holdout", 0.0, 0.3)])
    for code, text in plain.RULES.items():
        v = [{"strategy": "x", "capital": 100.0, "status": "fails", "first_broken_rule": code,
              "deflated_sharpe": -1.0, "n_variants": 3}]
        card = plain.scoreboard(results, v, Settings())[0]["cards"][0]
        assert card["reason"] == text["fail"]
        failed = [k["code"] for k in card["checks"] if k["ok"] is False]
        assert failed[0] == code


def test_report_freshness_reasons():
    from cs2trader.ui import plain
    assert plain.report_freshness("2026-01-01", True, True, "2026-01-01") == {"stale": False, "reasons": [],
                                                                               "advice": None}
    f = plain.report_freshness("2026-01-01", False, False, "2026-01-04")
    assert [r["code"] for r in f["reasons"]] == ["no_verdicts", "hash", "newer_data"]
    assert "3 days past this report" in f["reasons"][2]["text"] and "cs2 report" in f["advice"]


def test_collector_summary_traffic_light():
    from cs2trader.ui import plain
    base = {"state": "ok", "window_h": 48, "target": 44, "snapshots": 48, "gaps": [], "low_share_hours": [],
            "min_item_share": 0.99, "age_hours": 0.4}
    assert plain.collector_summary(base)["status"] == "green"
    holes = plain.collector_summary({**base, "snapshots": 45,
                                     "gaps": [["2026-11-10T13:00:00+00:00", "2026-11-10T16:00:00+00:00"]]})
    assert holes["status"] == "amber" and "3 hours missing in the last 48 hours" in holes["headline"]
    assert "3 hours missing from Nov 10, 13:00 to Nov 10, 16:00 UTC." in holes["points"]
    assert plain.collector_summary({**base, "age_hours": 30.0})["status"] == "red"
    assert plain.collector_summary({"state": "empty"})["status"] == "grey"


def test_market_now_words():
    from cs2trader.ui import plain
    pts = [{"t": "2024-01-09", "index_level": 100.0, "ret": 0.0, "regime": "flat"},
           {"t": "2024-01-10", "index_level": 95.0, "ret": -0.08, "regime": "falling"},
           {"t": "2024-01-11", "index_level": 90.0, "ret": -0.10, "regime": "falling"}]
    now = plain.market_now(pts, 30, 0.05)
    assert now["headline"] == "The market is falling" and now["since"] == "2024-01-10"
    assert "down 10.0\u00a0% over the last 30 days" in now["detail"] and now["year"] is None
    assert plain.market_now([], 30, 0.05) is None


def test_home_payload_answers_the_four_questions(env):
    app, _ = env
    status, body = call(app, "GET", "/api/home")
    h = json.loads(body)
    assert status == 200 and h["state"] == "ok"
    assert h["verdict"]["status"] == "none" and h["verdict"]["headline"] == "No strategy is good enough to use"
    assert h["market"]["regime"] == "falling" and h["market"]["headline"] == "The market is falling"
    # The fixture's history ends in February 2024, so the data block must say it is old.
    assert h["data"]["status"] == "amber" and "cs2 backfill steam" in h["data"]["advice"]
    assert h["collector"]["status"] == "grey"
    texts = " ".join(s["text"] for s in h["next"])
    assert "cs2 backfill steam" in texts and "cs2 collect pull" in texts
    assert h["last_day"] and h["data_version"]


def test_home_on_an_empty_project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = App(load_settings(tmp_path / "cs2.toml"), tmp_path / "cs2.toml", tmp_path / ".env")
    server, _ = start_in_thread(app)
    try:
        h = json.loads(call(app, "GET", "/api/home")[1])
        assert h["verdict"]["state"] == "empty" and h["data"]["status"] == "red" and h["market"]["regime"] is None
        assert any("cs2 report" in s["text"] for s in h["next"])
    finally:
        server.shutdown()
        server.server_close()


def test_verdict_payload_has_a_scoreboard_and_freshness(env):
    app, _ = env
    v = json.loads(call(app, "GET", "/api/verdict")[1])
    card = v["scoreboard"][0]["cards"][0]
    assert card["strategy"] == "x" and card["reason"] == "Did worse than just holding cash."
    assert v["headline"]["status"] == "none" and v["is_latest"] is True
    assert v["freshness"]["stale"] is False  # the report (2026-01-01) is newer than the data (2024)


def test_run_detail_has_a_summary_in_words(env):
    app, res = env
    d = json.loads(call(app, "GET", f"/api/run/{res.run_id}")[1])
    s, last = d["summary"], d["equity"][-1]
    assert s["end_cash"] == pytest.approx(last["equity_cash"]) and s["capital"] == 100.0
    assert s["return_cash"] == pytest.approx(last["equity_cash"] / 100.0 - 1)
    assert s["worst_dip"] == pytest.approx(min(e["drawdown_cash"] for e in d["equity"]))
    assert s["text"].startswith("Started with $100 on ") and "ended with $" in s["text"]
    run = json.loads(call(app, "GET", "/api/runs")[1])["runs"][0]
    assert run["label"] == "Buy and hold (all skins)" and run["end_cash"] == pytest.approx(last["equity_cash"])


def test_strategies_and_settings_carry_plain_labels(env):
    from cs2trader.strategies.registry import REGISTRY
    from cs2trader.ui import plain
    app, _ = env
    st = json.loads(call(app, "GET", "/api/strategies")[1])["strategies"]
    assert {s["name"] for s in st} == set(REGISTRY) == set(plain.STRATEGIES)
    for s in st:
        assert s["label"] != s["name"] and s["summary"]
        assert [p["key"] for p in s["params"]] == list(s["grid"])
        assert all(p["label"] != p["key"] and p["help"] for p in s["params"])
    v1 = next(s for s in st if s["name"] == "v1_dip_knives")
    assert next(p for p in v1["params"] if p["key"] == "drop_7d")["default"] == 0.10
    se = json.loads(call(app, "GET", "/api/settings")[1])
    items = [i for g in se["groups"].values() for i in g]
    assert {i["key"] for i in items} == set(settings_api.EDITABLE) == set(plain.SETTINGS_TEXT)
    assert all(i["label"] != i["key"] and i["help"] and i["effect"] for i in items)
    fee = next(i for i in items if i["key"] == "costs.seller_fee")
    assert fee["default"] == 0.05 and fee["unit"] == "share"
    assert set(se["group_intros"]) == set(se["groups"]) and se["read_only_why"]


def test_static_files_are_served_only_from_the_static_folder(env):
    app, _ = env
    for name in ("ui.js", "pages.js", "app.js", "style.css"):
        assert call(app, "GET", f"/static/{name}")[0] == 200, name
    for bad in ("/static/../server.py", "/static/..%2Fserver.py", "/static/sub/x.js", "/static/nope.js",
                "/static/index.txt"):
        assert call(app, "GET", bad)[0] == 404, bad


def test_page_has_no_inline_script():
    import re
    html = (Path(__file__).parents[1] / "src/cs2trader/ui/static/index.html").read_text()
    assert not re.search(r"<script(?![^>]*\bsrc=)", html) and " onclick=" not in html
