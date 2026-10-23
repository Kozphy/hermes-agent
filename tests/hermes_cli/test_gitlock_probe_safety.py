"""Preserve old locks if the process inventory is incomplete or git is alive."""

import os
import subprocess
import time

import pytest

from hermes_cli.gitlock import STALE_LOCK_MIN_AGE_SECONDS, clear_stale_git_locks


@pytest.mark.parametrize("probe", ["error", "missing", "timeout"])
def test_failed_probe_preserves_old_lock(tmp_path, monkeypatch, probe):
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    lock = git_dir / "index.lock"
    lock.write_text("in-flight writer")
    old = time.time() - STALE_LOCK_MIN_AGE_SECONDS - 60
    os.utime(lock, (old, old))

    def unavailable(*args, **kwargs):
        if probe == "missing":
            raise FileNotFoundError("process probe missing")
        if probe == "timeout":
            raise subprocess.TimeoutExpired(args[0], 10)
        return subprocess.CompletedProcess(args[0], 2, stdout="", stderr="unavailable")

    monkeypatch.setattr("hermes_cli.gitlock.subprocess.run", unavailable)
    assert clear_stale_git_locks(tmp_path) == []
    assert lock.read_text() == "in-flight writer"


@pytest.mark.linux_only
def test_live_git_preserves_old_lock(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    lock = tmp_path / ".git" / "index.lock"
    lock.write_text("in-flight writer")
    old = time.time() - STALE_LOCK_MIN_AGE_SECONDS - 60
    os.utime(lock, (old, old))
    # hash-object waits for EOF, giving the sweep a real live git process to observe.
    with subprocess.Popen(["git", "hash-object", "--stdin"], stdin=subprocess.PIPE,
                          stdout=subprocess.PIPE, cwd=tmp_path) as writer:
        try:
            assert writer.poll() is None
            assert clear_stale_git_locks(tmp_path) == []
            assert lock.exists()
        finally:
            writer.communicate(timeout=10)
