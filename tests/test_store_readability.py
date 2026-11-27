"""Reader-shape fence test suite — the R3 anchor (plan 2026-11-28-001 U3).

maestro's bounded-operational-scan reader is a FROZEN contract (maestro
``src/maestro/context.py`` ``_ro``): a ``file:<db>?mode=ro`` URI open with
``timeout=8``, a Row factory, ``PRAGMA query_only=ON``, then
``PRAGMA busy_timeout=8000``, followed by the exact query sequence in
order. This suite replicates that reader byte-for-byte — importing the
repo's own frozen sequence constant so any drift is impossible — and
asserts, per the adversarial-review-corrected matrix:

* fixtures (a)-(d) — clean WAL, grown WAL, live concurrent writer,
  stale-shm — all read CLEAN. Fixture (d) pins the empirically verified
  truth that falsified the original hypothesis: a stale wal-index is
  NOT the blocker; a killed holder's committed rows read clean.
* fixture (e) — a live ``PRAGMA locking_mode=EXCLUSIVE`` holder — is
  THE deterministic WAL-mode blocker on this host (SQLite 3.53.1): the
  reader fails bounded within its 8 s busy budget with the observed
  ``OperationalError: database is locked`` signature, including at the
  production-observed mid-scan position (between the pending counts and
  the ``historian_runs`` read). Pinned forever.
* a missing store reports the distinct not-found shape, proving the
  suite discriminates failure modes.

The keepalive remedy (U2, even lane) is deliberately NOT exercised
here: its own contract tests live in ``tests/test_store_keepalive.py``
per the plan's file assignments.
"""

from __future__ import annotations

import signal
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

from magic_hermes.db_toolkit import (
    MAESTRO_QUERY_SEQUENCE,
    _MAESTRO_BUSY_TIMEOUT_MS,
    _MAESTRO_READER_TIMEOUT_S,
)

FENCE_SCHEMA = (
    "CREATE TABLE pending_ops (id INTEGER PRIMARY KEY)",
    "CREATE TABLE migration_pending (id INTEGER PRIMARY KEY)",
    "CREATE TABLE pending_session_cleanup (id INTEGER PRIMARY KEY)",
    (
        "CREATE TABLE historian_runs (id INTEGER PRIMARY KEY, status TEXT,"
        " failure_reason TEXT, created_at TEXT)"
    ),
    (
        "CREATE TABLE dream_runs (id INTEGER PRIMARY KEY, started_at TEXT,"
        " finished_at TEXT, tasks_succeeded INTEGER, tasks_failed INTEGER)"
    ),
    (
        "CREATE TABLE task_schedule_state (task TEXT PRIMARY KEY, last_run_at TEXT,"
        " next_due_at TEXT, last_status TEXT, retry_count INTEGER)"
    ),
    (
        "CREATE TABLE mirror_cursors (domain TEXT PRIMARY KEY, cursor TEXT,"
        " updated_at TEXT)"
    ),
    "CREATE TABLE memory_verifications (id INTEGER PRIMARY KEY, verified_at TEXT)",
)


def build_fence_store(path: Path, grow_wal: bool = False) -> Path:
    """Create a WAL-mode fixture store carrying maestro's query surface.

    ``grow_wal=True`` disables autocheckpoint and returns with a second
    connection still OPEN so the -wal keeps committed frames (a clean
    close would checkpoint and remove it). The caller owns closing that
    connection via the returned handle in ``_lingering`` — see
    ``build_fence_store_lingering``.
    """

    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=WAL")
    for ddl in FENCE_SCHEMA:
        con.execute(ddl)
    con.commit()
    con.close()
    return path


def _grow_wal(path: Path) -> sqlite3.Connection:
    """Grow the fixture's WAL with committed frames; keep a live owner open."""

    con = sqlite3.connect(path)
    con.execute("PRAGMA wal_autocheckpoint=0")
    for i in range(500):
        con.execute("BEGIN IMMEDIATE")
        con.execute(f"INSERT INTO pending_ops VALUES ({i + 1})")
        con.commit()
    return con


def replica_scan(
    db_path: Path,
    *,
    hold_before: str | None = None,
    on_hold=None,
) -> dict:
    """Run the reader replica exactly as maestro's bounded scan does.

    ``hold_before``/``on_hold`` give tests a deterministic mid-scan hook:
    ``on_hold()`` runs inside the scan right before the named query —
    in the reader's own thread, so the test can engage a lock holder
    between statements and reproduce the production failure position.
    """

    result: dict = {
        "ok": True,
        "latency_ms": 0.0,
        "exception": None,
        "failing_query": None,
    }
    started = time.monotonic()
    name = "open"
    try:
        con = sqlite3.connect(
            f"file:{db_path}?mode=ro", uri=True, timeout=_MAESTRO_READER_TIMEOUT_S
        )
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA query_only=ON")
        con.execute(f"PRAGMA busy_timeout={_MAESTRO_BUSY_TIMEOUT_MS}")
        try:
            for name, sql in MAESTRO_QUERY_SEQUENCE:
                if hold_before == name and on_hold is not None:
                    on_hold()
                con.execute(sql).fetchall()
        except sqlite3.Error as exc:
            result["ok"] = False
            result["exception"] = f"{type(exc).__name__}: {exc}"
            result["failing_query"] = name
        finally:
            con.close()
    except sqlite3.Error as exc:
        result["ok"] = False
        result["exception"] = f"{type(exc).__name__}: {exc}"
        result["failing_query"] = name
    result["latency_ms"] = round((time.monotonic() - started) * 1000.0, 1)
    return result


class TestReaderFence:
    """Fixtures (a)-(d): the reader completes clean; only (e) blocks."""

    def test_a_clean_wal_store_reads_clean(self, tmp_path):
        db = build_fence_store(tmp_path / "store.db")
        result = replica_scan(db)
        assert result["ok"] is True, result
        assert result["exception"] is None
        assert result["latency_ms"] < 5_000

    def test_b_grown_wal_store_reads_clean(self, tmp_path):
        db = build_fence_store(tmp_path / "store.db")
        owner = _grow_wal(db)
        try:
            wal = Path(f"{db}-wal")
            assert wal.exists() and wal.stat().st_size > 0
            result = replica_scan(db)
        finally:
            owner.close()
        assert result["ok"] is True, result

    def test_c_live_concurrent_writer_reads_clean(self, tmp_path):
        db = build_fence_store(tmp_path / "store.db")
        stop = threading.Event()

        def _write_loop() -> None:
            con = sqlite3.connect(db, timeout=5.0)
            try:
                while not stop.is_set():
                    try:
                        con.execute("BEGIN IMMEDIATE")
                        con.execute(
                            "INSERT INTO historian_runs (status, created_at)"
                            " VALUES ('ok', '2026-11-28T00:00:00Z')"
                        )
                        con.commit()
                    except sqlite3.Error:
                        con.rollback()
                    time.sleep(0.01)
            finally:
                con.close()

        writer = threading.Thread(target=_write_loop, daemon=True)
        writer.start()
        try:
            result = replica_scan(db)
        finally:
            stop.set()
            writer.join(2.0)
        assert result["ok"] is True, result

    def test_d_stale_shm_reads_clean(self, tmp_path):
        """Pins the falsified hypothesis: a stale wal-index reads CLEAN.

        A killed holder leaves -wal/-shm behind with committed frames; the
        maestro-replica reader recovers the wal-index and reads every
        committed row — staleness is not the blocker (adversarial review,
        2026-11-28). Asserted so the dead hypothesis is never re-claimed.
        """

        db = build_fence_store(tmp_path / "store.db")
        child_code = (
            "import sqlite3, sys, time\n"
            "con = sqlite3.connect(sys.argv[1])\n"
            "con.execute('INSERT INTO pending_ops VALUES (999)')\n"
            "con.commit()\n"
            "print('ready', flush=True)\n"
            "time.sleep(60)\n"
        )
        child = subprocess.Popen(
            [sys.executable, "-c", child_code, str(db)],
            stdout=subprocess.PIPE,
            text=True,
        )
        try:
            assert child.stdout is not None
            assert child.stdout.readline().strip() == "ready"
        finally:
            os_kill = signal.SIGKILL
            import os

            os.kill(child.pid, os_kill)
            child.wait(timeout=10)
        assert child.returncode == -signal.SIGKILL
        assert Path(f"{db}-shm").exists()
        wal = Path(f"{db}-wal")
        assert wal.exists() and wal.stat().st_size > 0
        result = replica_scan(db)
        assert result["ok"] is True, result
        # the killed holder's committed row survived and is readable
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            count = con.execute("SELECT COUNT(*) FROM pending_ops").fetchone()[0]
        finally:
            con.close()
        assert count == 1

    def test_e_exclusive_holder_blocks_reader_bounded(self, tmp_path):
        """THE deterministic blocker (fixture e): bounded failure, not a hang."""

        db = build_fence_store(tmp_path / "store.db")
        holder = sqlite3.connect(db)
        holder.execute("PRAGMA locking_mode=EXCLUSIVE")
        holder.execute("BEGIN IMMEDIATE")
        holder.execute("INSERT INTO pending_ops VALUES (2)")
        try:
            result = replica_scan(db)
        finally:
            holder.rollback()
            holder.close()
        assert result["ok"] is False, result
        assert "database is locked" in (result["exception"] or "")
        # holder engaged before the scan: the first table read fails
        assert result["failing_query"] == "schema_table_count"
        # bounded: the 8 s busy window fired (waited, not instant), no hang
        assert 7_000 <= result["latency_ms"] < 12_000

    def test_e_exclusive_holder_mid_scan_reproduces_observed_signature(self, tmp_path):
        """The production signature, deterministically: the lock window opens
        between the pending counts and the historian_runs read."""

        db = build_fence_store(tmp_path / "store.db")
        holder = sqlite3.connect(db)
        holder.execute("PRAGMA locking_mode=EXCLUSIVE")

        def _engage() -> None:
            holder.execute("BEGIN IMMEDIATE")
            holder.execute("INSERT INTO pending_ops VALUES (3)")

        try:
            result = replica_scan(
                db, hold_before="historian_runs", on_hold=_engage
            )
        finally:
            holder.rollback()
            holder.close()
        assert result["ok"] is False, result
        assert result["failing_query"] == "historian_runs"
        assert "database is locked" in (result["exception"] or "")
        # bounded within the 8 s reader budget (observed ~5 s on this host's
        # SQLite 3.53.1: the wal-index lock retry path fires before the
        # busy timeout); waited rather than instant, never a hang
        assert 1_000 <= result["latency_ms"] < 9_000

    def test_missing_store_reports_distinct_not_found(self, tmp_path):
        """The suite discriminates failure modes: not-found is not a lock."""

        result = replica_scan(tmp_path / "absent.db")
        assert result["ok"] is False, result
        assert "database is locked" not in (result["exception"] or "")
        assert "unable to open" in (result["exception"] or "")
        assert result["failing_query"] == "open"


class TestFrozenReaderContract:
    """The replica cannot drift from maestro's frozen query surface."""

    def test_query_sequence_is_the_frozen_nine(self):
        assert [name for name, _ in MAESTRO_QUERY_SEQUENCE] == [
            "schema_table_count",
            "pending_ops",
            "migration_pending",
            "pending_session_cleanup",
            "historian_runs",
            "dream_runs",
            "task_schedule_state",
            "mirror_cursors",
            "memory_verifications",
        ]
        assert "LIMIT 20" in dict(MAESTRO_QUERY_SEQUENCE)["historian_runs"]

    def test_reader_shape_constants_match_maestro(self):
        assert _MAESTRO_READER_TIMEOUT_S == 8.0
        assert _MAESTRO_BUSY_TIMEOUT_MS == 8000
