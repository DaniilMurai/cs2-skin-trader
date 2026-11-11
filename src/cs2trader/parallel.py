import multiprocessing
import tempfile
from concurrent.futures import Future, ProcessPoolExecutor
from datetime import date
from pathlib import Path
from typing import Any

from cs2trader.backtest.engine import run
from cs2trader.backtest.view import MarketData
from cs2trader.config import Settings
from cs2trader.models import RunResult, RunSpec


class LocalRunner:
    """Runs everything in this process, one call at a time; the reference behaviour."""

    def __init__(self, store: Any, settings: Settings, data: MarketData) -> None:
        self.store, self.settings, self.data = store, settings, data

    def submit_tune(self, entry: Any, capital: float, start: date, end: date, fold: int) -> Future:
        from cs2trader.evaluate import tune

        return _done(tune(entry, capital, start, end, fold, self.store, self.settings, self.data))

    def submit_run(self, spec: RunSpec) -> Future:
        return _done(run(spec, self.store, self.settings, self.data))


def _done(value: Any) -> Future:
    f: Future = Future()
    f.set_result(value)
    return f


class _VersionOnly:
    """The only thing `run` asks the store for; workers get it from the parent so none of them
    opens the database."""

    def __init__(self, version: str) -> None:
        self._version = version

    def data_version(self) -> str:
        return self._version


_worker: dict[str, Any] = {}


def _init_worker(directory: str, settings: Settings, version: str) -> None:
    _worker["data"] = MarketData.attach(Path(directory))
    _worker["settings"] = settings
    _worker["store"] = _VersionOnly(version)


def _tune(name: str, capital: float, start: date, end: date, fold: int):
    from cs2trader.evaluate import tune
    from cs2trader.strategies.registry import REGISTRY

    return tune(REGISTRY[name], capital, start, end, fold, _worker["store"], _worker["settings"], _worker["data"])


def _run(spec: RunSpec) -> RunResult:
    return run(spec, _worker["store"], _worker["settings"], _worker["data"])


class PoolRunner:
    """Worker processes that share one memory-mapped copy of the prepared market. Processes, not
    threads: the strategies are Python code, and 12 threads gave only ~1.5x on the GIL."""

    def __init__(self, store: Any, settings: Settings, data: MarketData, jobs: int) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="cs2-market-")
        data.export(Path(self._tmp.name))
        self._pool = ProcessPoolExecutor(
            jobs, mp_context=multiprocessing.get_context("spawn"), initializer=_init_worker,
            initargs=(self._tmp.name, settings, store.data_version()))

    def submit_tune(self, entry: Any, capital: float, start: date, end: date, fold: int) -> Future:
        return self._pool.submit(_tune, entry.name, capital, start, end, fold)

    def submit_run(self, spec: RunSpec) -> Future:
        return self._pool.submit(_run, spec)

    def close(self) -> None:
        self._pool.shutdown()
        self._tmp.cleanup()

    def __enter__(self) -> PoolRunner:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
