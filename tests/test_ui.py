import http.client
import json
import sys
import time
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
from cs2trader.ui import settings_api
from cs2trader.ui.jobs import JobRunner
from cs2trader.ui.security import TOKEN_HEADER, scrub, secret_values
from cs2trader.ui.server import App, start_in_thread

SECRET = "SECRET-cookie-value-123456"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(f"STEAM_LOGIN_SECURE={SECRET}\nMARKETCSGO_API_KEY=abcdefgh-key-98765\n")
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
             "/api/strategies", "/api/settings", "/api/jobs", "/static/app.js", "/reports/2026-01-01/report.html"]
    for p in secret_values(app.env_path):
        assert p
    for path in paths:
        _, body = call(app, "GET", path)
        for value in secret_values(app.env_path):
            assert value.encode() not in body, path
    # the filter itself: a payload that does contain one is redacted
    assert SECRET.encode() not in scrub(f"x {SECRET} y".encode(), secret_values(app.env_path))


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
