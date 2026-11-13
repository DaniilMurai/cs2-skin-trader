"""One background `cs2 backtest` at a time, run as a subprocess so the UI never holds the database."""
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from datetime import date
from pathlib import Path
from typing import Any

import duckdb

from cs2trader.cli import parse_params
from cs2trader.errors import ConfigError

# `python -c` because the cs2 console script may not sit next to this interpreter
LAUNCH = "import sys; sys.argv[0] = 'cs2'; from cs2trader.cli import main; main()"
# The Mac must stay responsive while a backtest runs. Absolute, not an increment: a UI that is itself
# niced would otherwise push the child past 19, and going back below the current value needs root.
NICE = "import os; os.setpriority(os.PRIO_PROCESS, 0, max(19, os.getpriority(os.PRIO_PROCESS, 0))); "
STOP_GRACE_S = 5
RUN_LINE = re.compile(r"^run ([0-9a-f]{16})\b")
KEEP_LINES = 300


class JobRunner:
    def __init__(self, data_dir: Path, cwd: Path | None = None) -> None:
        self.data_dir = data_dir
        self.cwd = cwd or Path.cwd()
        self._lock = threading.Lock()
        self.job: dict[str, Any] | None = None

    def snapshot(self) -> dict:
        with self._lock:
            if self.job is None:
                return {"state": "idle"}
            j = dict(self.job)
            j["lines"] = list(j["lines"])[-60:]
            del j["proc"], j["stopping"]
            return j

    def _db_locked(self) -> bool:
        db = self.data_dir / "cs2.duckdb"
        if not db.exists():
            return False
        try:
            duckdb.connect(str(db), read_only=True).close()
            return False
        except duckdb.Error:
            return True

    def start_backtest(self, form: dict) -> tuple[int, dict]:
        try:
            strategy = str(form.get("strategy", ""))
            params = parse_params(strategy, json.dumps(form.get("params") or {}))
            capital = float(form["capital"])
            start, end = date.fromisoformat(form["start"]), date.fromisoformat(form["end"])
        except (ConfigError, KeyError, ValueError, TypeError) as e:
            return 400, {"error": f"invalid form: {e}"}
        if capital <= 0 or start >= end:
            return 400, {"error": "capital must be positive and start before end"}
        cmd = [sys.executable, "-c", NICE + LAUNCH, "backtest", "--strategy", strategy, "--capital", str(capital),
               "--from", start.isoformat(), "--to", end.isoformat(), "--params", json.dumps(params)]
        # Check and spawn under one lock: parallel requests would otherwise all see a free slot.
        with self._lock:
            if self.job is not None and self.job["state"] == "running":
                return 409, {"error": "busy: a job is already running", "state": "busy"}
            if self._db_locked():
                return 409, {"error": "busy: the database is in use by another process", "state": "busy"}
            proc = subprocess.Popen(cmd, cwd=self.cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, start_new_session=True)
            job = {"state": "running", "started": time.time(), "strategy": strategy, "capital": capital,
                   "start": start.isoformat(), "end": end.isoformat(), "params": params,
                   "lines": [], "run_id": None, "exit_code": None, "proc": proc, "stopping": False}
            self.job = job
        threading.Thread(target=self._pump, args=(job,), daemon=True).start()
        return 202, {"state": "running"}

    def stop(self) -> tuple[int, dict]:
        with self._lock:
            job = self.job
            if job is None or job["state"] != "running":
                return 409, {"error": "no job is running"}
            job["stopping"] = True
            proc = job["proc"]
        # The job leads its own session, so the group signal also reaches anything it spawned.
        self._signal(proc, signal.SIGTERM)
        try:
            proc.wait(timeout=STOP_GRACE_S)
        except subprocess.TimeoutExpired:
            self._signal(proc, signal.SIGKILL)
            proc.wait()
        # The pump thread records the final state; give it a moment so the answer already says "stopped".
        for _ in range(50):
            if self.snapshot()["state"] != "running":
                break
            time.sleep(0.02)
        return 200, self.snapshot()

    @staticmethod
    def _signal(proc: subprocess.Popen, sig: signal.Signals) -> None:
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            pass

    def _pump(self, job: dict[str, Any]) -> None:
        proc = job["proc"]
        for line in proc.stdout:
            line = line.rstrip("\n")
            with self._lock:
                job["lines"].append(line)
                del job["lines"][:-KEEP_LINES]
                if (m := RUN_LINE.match(line)):
                    job["run_id"] = m.group(1)
        code = proc.wait()
        with self._lock:
            job["exit_code"] = code
            job["state"] = "stopped" if job["stopping"] else "done" if code == 0 else "failed"
            job["finished"] = time.time()
