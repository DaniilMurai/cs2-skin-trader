"""One background `cs2 backtest` at a time, run as a subprocess so the UI never holds the database."""
import json
import re
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
            j.pop("proc", None)
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
        with self._lock:
            if self.job is not None and self.job["state"] == "running":
                return 409, {"error": "busy: a job is already running", "state": "busy"}
        if self._db_locked():
            return 409, {"error": "busy: the database is in use by another process", "state": "busy"}
        cmd = [sys.executable, "-c", LAUNCH, "backtest", "--strategy", strategy, "--capital", str(capital),
               "--from", start.isoformat(), "--to", end.isoformat(), "--params", json.dumps(params)]
        proc = subprocess.Popen(cmd, cwd=self.cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, start_new_session=True)
        with self._lock:
            self.job = {"state": "running", "started": time.time(), "strategy": strategy, "capital": capital,
                        "start": start.isoformat(), "end": end.isoformat(), "params": params,
                        "lines": [], "run_id": None, "exit_code": None, "proc": proc}
        threading.Thread(target=self._pump, args=(proc,), daemon=True).start()
        return 202, {"state": "running"}

    def _pump(self, proc: subprocess.Popen) -> None:
        for line in proc.stdout:
            line = line.rstrip("\n")
            with self._lock:
                self.job["lines"].append(line)
                del self.job["lines"][:-KEEP_LINES]
                if (m := RUN_LINE.match(line)):
                    self.job["run_id"] = m.group(1)
        code = proc.wait()
        with self._lock:
            self.job["exit_code"] = code
            self.job["state"] = "done" if code == 0 else "failed"
            self.job["finished"] = time.time()
