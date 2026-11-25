"""Tests for the shared-store ``historian_runs`` classification guard.

Regression context: maestro finding d59758598379 — upstream
``@cortexkit/pi-magic-context`` commits historian rows with
``status='failed'`` and ``failure_reason IS NULL`` on benign early-return
paths, and maestro's ``phase1.context.operational`` evaluator counts the
leading failure streak of the newest 20 rows, so those reason-less rows
read as operational failures.

All tests run against fixture databases built with the production
22-column ``historian_runs`` shape. No live store is ever touched.
Tasks T9-T11 (CLI exit codes, doctor states, env isolation) live with
the CLI wiring tests in ``tests/test_cli.py``; T12 (WAL second-connection
server-side proof) lives below.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from magic_hermes import historian_guard as hg


def make_db(tmp_path: Path, name: str = "context.db") -> Path:
    return hg.make_fixture_db(tmp_path / name)


def read_row(con: sqlite3.Connection, row_id: int) -> tuple[str, str | None]:
    row = con.execute(
        "select status, failure_reason from historian_runs where id = ?", (row_id,)
    ).fetchone()
    assert row is not None
    return (str(row[0]), row[1])


def leading_failure_streak(con: sqlite3.Connection, window: int = 20) -> int:
    """Maestro-style read (maestro/src/maestro/context.py magic()): count the
    leading consecutive failure-status rows of the newest ``window`` runs."""

    rows = con.execute(
        "select status from historian_runs order by id desc limit ?", (window,)
    ).fetchall()
    streak = 0
    for (status,) in rows:
        if str(status) != "failed":
            break
        streak += 1
    return streak


class TestRegressionD59758598379:
    """T1 — the defect class reads as an operational failure unshimmed and
    as a benign noop once the guard is applied."""

    def test_failed_null_row_is_reclassified_and_streak_breaks(self, tmp_path):
        db = make_db(tmp_path)

        # Unshimmed: the upstream defect. A benign early-return commits
        # failed/NULL and a maestro-style newest-20 read counts it.
        con = sqlite3.connect(db)
        try:
            hg.insert_run(con, session_id="conductor-x", status="failed")
            row_id = hg.insert_run(con, session_id="conductor-x", status="failed")
            assert read_row(con, row_id) == ("failed", None)
            assert leading_failure_streak(con) == 2
        finally:
            con.close()

        result = hg.apply_guard(db)
        assert result.applied is True
        assert result.repaired is False
        assert result.verification_failures == []

        # Shimmed: the same insert commits as noop+marker, streak breaks.
        con = sqlite3.connect(db)
        try:
            row_id = hg.insert_run(con, session_id="conductor-x", status="failed")
            assert read_row(con, row_id) == ("noop", hg.MARKER_REASON)
            assert leading_failure_streak(con) == 0
        finally:
            con.close()

    def test_guard_only_ever_rewrites_the_new_row(self, tmp_path):
        db = make_db(tmp_path)
        con = sqlite3.connect(db)
        try:
            old_id = hg.insert_run(con, status="failed")
        finally:
            con.close()

        hg.apply_guard(db)

        con = sqlite3.connect(db)
        try:
            # Historical rows are never rewritten by the guard.
            assert read_row(con, old_id) == ("failed", None)
            total = con.execute("select count(*) from historian_runs").fetchone()[0]
            assert total == 1
        finally:
            con.close()


class TestPassThroughLanes:
    """T2-T4 — everything outside the defect class passes through."""

    def test_reasoned_failure_untouched(self, tmp_path):
        db = make_db(tmp_path)
        hg.apply_guard(db, verify=False)
        con = sqlite3.connect(db)
        try:
            row_id = hg.insert_run(
                con, status="failed", failure_reason="compaction crashed"
            )
            assert read_row(con, row_id) == ("failed", "compaction crashed")
        finally:
            con.close()

    def test_success_untouched(self, tmp_path):
        db = make_db(tmp_path)
        hg.apply_guard(db, verify=False)
        con = sqlite3.connect(db)
        try:
            row_id = hg.insert_run(con, status="success")
            assert read_row(con, row_id) == ("success", None)
        finally:
            con.close()

    def test_existing_noop_untouched(self, tmp_path):
        db = make_db(tmp_path)
        hg.apply_guard(db, verify=False)
        con = sqlite3.connect(db)
        try:
            row_id = hg.insert_run(con, status="noop",
                                   failure_reason="drain budget exhausted")
            assert read_row(con, row_id) == ("noop", "drain budget exhausted")
        finally:
            con.close()


class TestIdempotencyAndRepair:
    def test_double_apply_leaves_single_canonical_trigger(self, tmp_path):
        db = make_db(tmp_path)
        first = hg.apply_guard(db, verify=False)
        second = hg.apply_guard(db, verify=False)
        assert first.applied and second.applied
        # Second apply found the canonical DDL already in place: no rewrite.
        assert second.repaired is False

        con = sqlite3.connect(db)
        try:
            count = con.execute(
                "select count(*) from sqlite_master where type = 'trigger' "
                "and name = ?",
                (hg.TRIGGER_NAME,),
            ).fetchone()[0]
            assert count == 1
            sql = con.execute(
                "select sql from sqlite_master where type = 'trigger' and name = ?",
                (hg.TRIGGER_NAME,),
            ).fetchone()[0]
        finally:
            con.close()
        assert hg._normalize_ddl(sql) == hg._normalize_ddl(hg.GUARD_DDL)

    def test_tampered_trigger_body_is_repaired(self, tmp_path):
        db = make_db(tmp_path)
        # A trigger with the right name but a different (tampered/drifted)
        # body — e.g. left by an older guard version or hand-editing.
        con = sqlite3.connect(db)
        try:
            con.execute(
                f"create trigger {hg.TRIGGER_NAME} "
                "after insert on historian_runs "
                "when new.status = 'failed' and new.failure_reason is null "
                "begin "
                "update historian_runs set status = 'skip' where id = new.id; "
                "end;"
            )
            con.commit()
        finally:
            con.close()

        result = hg.apply_guard(db, verify=False)
        assert result.applied is True
        assert result.repaired is True

        con = sqlite3.connect(db)
        try:
            status = hg.guard_status(db)
            assert status.present and status.matches
            row_id = hg.insert_run(con, status="failed")
            # The tampered body no longer runs: canonical semantics restored.
            assert read_row(con, row_id) == ("noop", hg.MARKER_REASON)
        finally:
            con.close()


class TestRemove:
    def test_remove_then_status_absent_then_reapply(self, tmp_path):
        db = make_db(tmp_path)
        hg.apply_guard(db, verify=False)
        assert hg.remove_guard(db) is True
        status = hg.guard_status(db)
        assert status.present is False
        assert status.matches is False
        # Removing a guard that is not there is a successful no-op.
        assert hg.remove_guard(db) is False

        # And the store still accepts unguarded inserts (retirement works).
        con = sqlite3.connect(db)
        try:
            row_id = hg.insert_run(con, status="failed")
            assert read_row(con, row_id) == ("failed", None)
        finally:
            con.close()

        hg.apply_guard(db, verify=False)
        assert hg.guard_status(db).present is True


class TestMarkerWindow:
    def test_marker_rows_24h_epoch_ms_boundaries(self, tmp_path):
        db = make_db(tmp_path)
        hg.apply_guard(db, verify=False)
        now_ms = int(time.time() * 1000)
        hour = 60 * 60 * 1000
        con = sqlite3.connect(db)
        try:
            hg.insert_run(con, status="failed", created_at=now_ms)
            hg.insert_run(con, status="failed", created_at=now_ms - 23 * hour)
            hg.insert_run(con, status="failed", created_at=now_ms - 25 * hour)
            # Old reasoned failure: outside the window AND not a marker.
            hg.insert_run(
                con,
                status="failed",
                failure_reason="old reasoned failure",
                created_at=now_ms - 1 * hour,
            )
        finally:
            con.close()

        status = hg.guard_status(db)
        assert status.present is True
        assert status.marker_rows_24h == 2

    def test_status_on_store_without_guard_still_counts_nothing(self, tmp_path):
        db = make_db(tmp_path)
        status = hg.guard_status(db)
        assert status.present is False
        assert status.marker_rows_24h == 0
        assert status.error is None


class TestApplyFailures:
    def test_missing_store_raises_actionable_error(self, tmp_path):
        missing = tmp_path / "nope" / "context.db"
        with pytest.raises(hg.GuardError, match="no shared context store"):
            hg.apply_guard(missing)

    def test_store_without_historian_table_refused(self, tmp_path):
        db = tmp_path / "bare.db"
        con = sqlite3.connect(db)
        con.execute("create table unrelated (id integer)")
        con.commit()
        con.close()
        with pytest.raises(hg.GuardError, match="no historian_runs table"):
            hg.apply_guard(db)


class TestSemanticVerify:
    def test_verify_guard_semantics_passes_on_fresh_apply(self):
        assert hg.verify_guard_semantics() == []


class TestWALSecondConnection:
    """T12 — server-side proof: the trigger lives in the store file, not in
    any client library. A WAL-mode fixture (the live shared store's journal
    mode) reclassifies rows written by a second, independent connection."""

    def test_second_connection_insert_is_reclassified_under_wal(self, tmp_path):
        db = make_db(tmp_path, name="wal.db")
        con = sqlite3.connect(db)
        mode = con.execute("pragma journal_mode=wal").fetchone()[0]
        con.close()
        assert str(mode).lower() == "wal"

        assert hg.apply_guard(db, verify=False).applied
        assert hg.guard_status(db).matches is True

        # A brand-new connection — no shared Python state with the one
        # that applied the guard — writes a defect-class row.
        writer = sqlite3.connect(db)
        try:
            row_id = hg.insert_run(writer, session_id="other-lane", status="failed")
            row = writer.execute(
                "select status, failure_reason from historian_runs where id = ?",
                (row_id,),
            ).fetchone()
        finally:
            writer.close()
        assert row == ("noop", hg.MARKER_REASON)


class TestLivenessStatus:
    """U1 (plan 2026-11-24-001, finding d59758598379) — read-only liveness
    corroboration for the doctor's boot-transient WARN lane. Positive
    evidence only: fresh success/noop rows corroborate; stale, empty,
    or failed-only stores never do; unreadable/missing stores are
    fail-visible and never corroborate. No test here writes through the
    guard's deploy path — the helper is strictly read-only."""

    def _insert(self, db, status, age_s, *, failure_reason=None):
        con = sqlite3.connect(db)
        try:
            now_ms = int(time.time() * 1000)
            return hg.insert_run(
                con,
                session_id="s-liveness",
                status=status,
                failure_reason=failure_reason,
                created_at=now_ms - int(age_s * 1000),
            )
        finally:
            con.close()

    def test_fresh_success_row_corroborates(self, tmp_path):
        db = make_db(tmp_path)
        self._insert(db, "success", 60.0)
        status = hg.liveness_status(db)
        assert status.ok is True
        assert status.recent_rows == 1
        assert status.latest_status == "success"
        assert status.latest_row_age_s is not None
        assert 55.0 <= status.latest_row_age_s <= 65.0
        assert status.error is None

    def test_fresh_noop_row_corroborates(self, tmp_path):
        # Episode-time guard-marker rows are reclassified to noop — they
        # are the live-traffic signal the doctor will actually see (the
        # live store runs the guard, so replicate it here).
        db = make_db(tmp_path)
        assert hg.apply_guard(db, verify=False).applied
        self._insert(db, "failed", 30.0)  # NULL reason -> guard -> noop
        status = hg.liveness_status(db)
        assert status.ok is True
        assert status.recent_rows == 1
        assert status.latest_status == "noop"

    def test_reasoned_failed_row_does_not_corroborate(self, tmp_path):
        # Positive-liveness means upstream's healthy vocabulary: a reasoned
        # failure row proves traffic but not health, so it must not
        # corroborate.
        db = make_db(tmp_path)
        self._insert(db, "failed", 10.0, failure_reason="boom")
        status = hg.liveness_status(db)
        assert status.ok is False
        assert status.recent_rows == 0
        assert status.latest_status == "failed"

    def test_stale_success_row_does_not_corroborate(self, tmp_path):
        # The live store's quiet-estate shape: rows days old. A stale row
        # must never white-wash an outage.
        db = make_db(tmp_path)
        self._insert(db, "success", 7 * 60 * 60.0)  # > 6 h default window
        status = hg.liveness_status(db)
        assert status.ok is False
        assert status.recent_rows == 0
        assert status.latest_status == "success"

    def test_window_is_inclusive_at_the_boundary(self, tmp_path):
        db = make_db(tmp_path)
        self._insert(db, "success", 3599.0)
        self._insert(db, "success", 3601.0)
        status = hg.liveness_status(db, window_s=3600)
        assert status.ok is True
        assert status.recent_rows == 1

    def test_empty_store_never_corroborates(self, tmp_path):
        db = make_db(tmp_path)
        status = hg.liveness_status(db)
        assert status.ok is False
        assert status.recent_rows == 0
        assert status.latest_status is None
        assert status.latest_row_age_s is None
        assert status.error is None

    def test_missing_store_is_fail_visible(self, tmp_path):
        status = hg.liveness_status(tmp_path / "absent.db")
        assert status.ok is False
        assert status.error is not None
        assert status.recent_rows == 0

    def test_unreadable_store_is_fail_visible(self, tmp_path):
        bad = tmp_path / "corrupt.db"
        bad.write_bytes(b"this is not a sqlite database" * 8)
        status = hg.liveness_status(bad)
        assert status.ok is False
        assert status.error is not None
        assert status.recent_rows == 0

    def test_helper_never_writes(self, tmp_path):
        # R3: the doctor path is read-only — after a liveness probe the
        # store's change counter must be untouched.
        db = make_db(tmp_path)
        self._insert(db, "success", 5.0)
        con = sqlite3.connect(db)
        try:
            before = con.execute("pragma data_version").fetchone()[0]
        finally:
            con.close()
        assert hg.liveness_status(db).ok
        con = sqlite3.connect(db)
        try:
            after = con.execute("pragma data_version").fetchone()[0]
        finally:
            con.close()
        assert before == after
