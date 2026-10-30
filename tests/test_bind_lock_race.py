"""Regression for Magic Context's cross-process bind mutex.

The real failure was a concurrent waiter deleting the just-mkdir'd lock
before its owner could publish pid, plus scratch reaping of live locks.
Tests use disposable lock directories; never open the production context DB.
"""
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

RUNTIME = Path(__file__).resolve().parents[1] / "src/magic_hermes/bridge/runtime.mjs"
NODE = shutil.which("node")


def _node_script():
    source = RUNTIME.read_text()
    begin = source.index("function acquireBindLock(lockPath)")
    end = source.index("function bindAttempts()", begin)
    helpers = source[begin:end]
    return (
        'import {mkdirSync, readFileSync, rmSync, statSync, '
        'writeFileSync} from "node:fs";\n'
        'import {randomUUID} from "node:crypto";\n'
        'import {join} from "node:path";\n'
        + helpers
        + '\nconst token = acquireBindLock(process.argv[1]);\n'
        + 'process.stdout.write("acquired\\n");\n'
        + 'releaseBindLock(process.argv[1], token);\n'
    )


def _waiter(path):
    return subprocess.Popen(
        [NODE, "--input-type=module", "-e", _node_script(), str(path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


@pytest.mark.skipif(not NODE, reason="node not installed")
def test_unpublished_pid_is_not_mistaken_for_dead_owner(tmp_path):
    lock = tmp_path / "context.db.bindlock"
    lock.mkdir()  # owner has just called mkdir, not yet written pid
    proc = _waiter(lock)
    try:
        time.sleep(0.5)
        assert lock.is_dir(), "waiter removed an in-progress live lock"
        assert not (lock / "pid").exists()
        (lock / "pid").write_text(str(os.getpid()) + "\n")
        time.sleep(0.5)
        assert proc.poll() is None, "waiter stole a live holder's lock"
        shutil.rmtree(lock)  # disposable fixture: simulate owner release
        stdout, stderr = proc.communicate(timeout=8)
        assert proc.returncode == 0, stderr
        assert "acquired" in stdout
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate()


@pytest.mark.skipif(not NODE, reason="node not installed")
def test_old_but_live_holder_is_not_reaped(tmp_path):
    lock = tmp_path / "context.db.bindlock"
    lock.mkdir()
    (lock / "pid").write_text(str(os.getpid()) + "\n")
    old = time.time() - 300
    os.utime(lock, (old, old))
    proc = _waiter(lock)
    try:
        time.sleep(0.6)
        assert lock.is_dir(), "age alone must never evict a live holder"
        assert proc.poll() is None
        shutil.rmtree(lock)
        stdout, stderr = proc.communicate(timeout=8)
        assert proc.returncode == 0, stderr
        assert "acquired" in stdout
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate()


@pytest.mark.skipif(not NODE, reason="node not installed")
def test_dead_holder_after_grace_is_reclaimed(tmp_path):
    lock = tmp_path / "context.db.bindlock"
    lock.mkdir()
    (lock / "pid").write_text("99999999\n")
    old = time.time() - 10
    os.utime(lock, (old, old))
    proc = _waiter(lock)
    try:
        stdout, stderr = proc.communicate(timeout=8)
        assert proc.returncode == 0, stderr
        assert "acquired" in stdout
        assert not lock.exists()
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate()


@pytest.mark.skipif(not NODE, reason="node not installed")
def test_parallel_binders_do_not_steal_each_others_lock(tmp_path):
    lock = tmp_path / "context.db.bindlock"
    script = _node_script().replace(
        'process.stdout.write("acquired\\n");',
        'sleepSync(80); process.stdout.write("acquired\\n");',
    )
    procs = [
        subprocess.Popen(
            [NODE, "--input-type=module", "-e", script, str(lock)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        for _ in range(8)
    ]
    try:
        for proc in procs:
            stdout, stderr = proc.communicate(timeout=12)
            assert proc.returncode == 0, stderr
            assert "acquired" in stdout
        assert not lock.exists()
    finally:
        for proc in procs:
            if proc.poll() is None:
                proc.kill()
                proc.communicate()


def test_lock_is_durable_and_backoff_is_outside_mutex():
    source = RUNTIME.read_text()
    assert 'join(dataHome, "cortexkit", "magic-context", "context.db")' in source
    assert (
        'join(tmpdir(), "hermes", "magic-context", "context.db.bindlock")'
        not in source
    )
    assert source.index("releaseBindLock(lockPath, bindToken);") < source.index(
        "if (retryDelayMs) sleepSync(retryDelayMs);"
    )

@pytest.mark.skipif(not NODE, reason="node not installed")
def test_delayed_release_does_not_delete_replacement_owner(tmp_path):
    """A stale binder must not unlink a lock with a different acquisition."""
    lock = tmp_path / "context.db.bindlock"
    script = _node_script().replace(
        "releaseBindLock(process.argv[1], token);",
        'writeFileSync(join(process.argv[1], "pid"), "99999999 replacement\\n");'
        "releaseBindLock(process.argv[1], token);"
        'if (!readFileSync(join(process.argv[1], "pid"), "utf8")'
        '.includes("replacement")) '
        'process.exit(9);'
        'rmSync(process.argv[1], { recursive: true, force: true });',
    )
    proc = subprocess.run(
        [NODE, "--input-type=module", "-e", script, str(lock)],
        capture_output=True, text=True, timeout=12, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert not lock.exists()
