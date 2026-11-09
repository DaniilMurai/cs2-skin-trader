import subprocess
from pathlib import Path

import pytest

from cs2trader.collect import pull_remote
from cs2trader.errors import ConfigError
from cs2trader.store import Store


def git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def remote(tmp_path):
    src = tmp_path / "src"
    (src / "asks" / "day=2026-11-09").mkdir(parents=True)
    (src / "asks" / "day=2026-11-09" / "a-gha.parquet").write_bytes(b"x")
    git("init", "-q", "-b", "main", cwd=src)
    git("-c", "user.name=t", "-c", "user.email=t@t", "add", "-A", cwd=src)
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "1", cwd=src)
    return src


def add(remote: Path, name: str) -> None:
    (remote / "asks" / "day=2026-11-09" / name).write_bytes(b"y")
    git("add", "-A", cwd=remote)
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", name, cwd=remote)


def test_clone_then_pull(tmp_path, remote):
    store = Store(tmp_path / "data")
    assert pull_remote(store, str(remote)) == 1
    add(remote, "b-gha.parquet")
    assert pull_remote(store, str(remote)) == 1


def test_clone_over_local_mac_files(tmp_path, remote):
    store = Store(tmp_path / "data")
    mine = tmp_path / "data" / "collector" / "asks" / "day=2026-11-09" / "m-mac.parquet"
    mine.parent.mkdir(parents=True)
    mine.write_bytes(b"m")
    assert pull_remote(store, str(remote)) == 1
    assert mine.exists()
    add(remote, "b-gha.parquet")
    assert pull_remote(store, str(remote)) == 1


def test_missing_repo_setting(tmp_path):
    with pytest.raises(ConfigError):
        pull_remote(Store(tmp_path), "")
