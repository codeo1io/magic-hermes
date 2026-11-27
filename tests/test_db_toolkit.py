"""Behavior tests for the `magic-hermes db` shared-store migration toolkit.

These tests never touch the real shared store: process discovery is
exercised against fixture /proc-style inputs and a scratch store, and
the migrate loop is driven against a stubbed one-shot driver. They lock
the contract the 2026-10-01 v91 migration incident taught us:

  * holder classification must mirror upstream's arc-marker semantics
    (a bare ``pi`` image, a marker-carrying node/bun/deno command) so
    the utility names the same blockers upstream's guard would;
  * ``status`` is read-only and names the fence gap when one exists;
  * ``migrate`` is fail-loud: refusal names blockers, error paths exit
    non-zero, the no-op fence is exit 0;
  * the drain loop terminates blockers only when --kill-blockers is
    given and only PIDs it can classify.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from unittest import mock

import pytest

from magic_hermes import db_toolkit
from magic_hermes.db_toolkit import (
    _rpc_port_pids,
    _tokens,
    command_looks_like_pi_harness,
    command_looks_like_pi_image,
    main_db,
)


class TestTokenizer:
    def test_plain_tokens(self):
        assert _tokens("pi --session x") == ["pi", "--session", "x"]

    def test_quoted_tokens(self):
        assert _tokens('node -e "code with spaces" arg') == [
            "node",
            "-e",
            "code with spaces",
            "arg",
        ]

    def test_empty(self):
        assert _tokens("") == []
        assert _tokens("   ") == []


class TestPiHarnessClassification:
    """Parity with upstream commandHasPiHarnessArc / PI_IMAGE_NAMES."""

    @pytest.mark.parametrize(
        ("command", "expected"),
        [
            ("pi", True),  # bare image
            ("pi --session abc", True),  # image with args
            ("omp serve", True),
            ("node pi.js", True),  # script image
            ("node /opt/pi/dist/bundle/cli.js run", True),  # arc marker
            ("bun run /x/oh-my-pi/server.js", True),  # arc marker
            ("node server.js", False),
            ("python3 -m pytest tests/", False),
            ("sqlite3 /x/context.db check", False),
            (
                "node --no-warnings /site/magic_hermes/bridge/runtime.mjs",
                False,
            ),
        ],
    )
    def test_classification(self, command, expected):
        got = command_looks_like_pi_harness(command) or command_looks_like_pi_image(
            command
        )
        assert got is expected, command

    def test_image_rule_is_argv0_only(self):
        # A marker in a later token with a non-harness argv[0] must not
        # classify (upstream: hasArc requires first in the allowed set).
        assert command_looks_like_pi_harness("bash /x/pi-coding-agent/foo") is False
        # ...but node/bun/deno + marker anywhere does classify.
        assert command_looks_like_pi_harness("node /x/pi-coding-agent/cli.js") is True


class TestRpcPortParsing:
    def test_parses_live_pids(self, tmp_path):
        rpc = tmp_path / "rpc"
        project = rpc / "abc123"
        project.mkdir(parents=True)
        import os

        (project / "port").write_text(
            json.dumps({"port": 8123, "pid": os.getpid(), "started_at": 1}),
            encoding="utf-8",
        )
        assert _rpc_port_pids(rpc) == [os.getpid()]

    def test_ignores_dead_and_malformed(self, tmp_path):
        rpc = tmp_path / "rpc"
        project = rpc / "abc123"
        project.mkdir(parents=True)
        (project / "port").write_text(
            json.dumps({"port": 8123, "pid": 999999999}), encoding="utf-8"
        )
        (project / "port-424242.json").write_text("not json", encoding="utf-8")
        (project / "unrelated.txt").write_text("x", encoding="utf-8")
        assert _rpc_port_pids(rpc) == []

    def test_missing_root(self, tmp_path):
        assert _rpc_port_pids(tmp_path / "rpc") == []


class TestScanStore:
    def test_scan_reports_fence_gap(self, tmp_path, monkeypatch):
        db = tmp_path / "context.db"
        con = sqlite3.connect(db)
        con.execute(
            "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY,"
            " description TEXT NOT NULL, applied_at INTEGER NOT NULL)"
        )
        con.execute("INSERT INTO schema_migrations VALUES (90, 'old', 0)")
        con.commit()
        con.close()
        monkeypatch.setenv("MAGIC_CONTEXT_DB_PATH", str(db))
        # package with fence 91
        package = tmp_path / "pkg"
        dist = package / "dist"
        dist.mkdir(parents=True)
        (package / "package.json").write_text(
            json.dumps({"name": "@cortexkit/pi-magic-context", "version": "0.44.4"}),
            encoding="utf-8",
        )
        (dist / "index-x.js").write_text(
            "function openDatabase(){}\nvar LATEST_SUPPORTED_VERSION = 91;\n",
            encoding="utf-8",
        )
        scan = db_toolkit.scan_store(package)
        assert scan.persisted_version == 90
        assert scan.latest_supported_version == 91
        assert scan.pending_migrations == 1
        assert scan.fence_ok is False

    def test_scan_missing_store(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MAGIC_CONTEXT_DB_PATH", str(tmp_path / "none.db"))
        scan = db_toolkit.scan_store(tmp_path)
        assert scan.exists is False


class TestMigrateFlow:
    def _stub_driver(self, exit_code, payload):
        def fake(db, root, timeout=300.0):
            return exit_code, payload

        return fake

    def test_migrate_noop_when_fence_satisfied(self, tmp_path, monkeypatch, capsys):
        db = tmp_path / "context.db"
        db.write_bytes(b"")
        monkeypatch.setenv("MAGIC_CONTEXT_DB_PATH", str(db))
        monkeypatch.setattr(db_toolkit, "find_magic_context_package", lambda: tmp_path)
        monkeypatch.setattr(
            db_toolkit, "_package_version", lambda root: "0.44.4"
        )
        scan = mock.Mock(
            persisted_version=91,
            latest_supported_version=91,
            pending_migrations=0,
        )
        monkeypatch.setattr(db_toolkit, "scan_store", lambda root: scan)
        rc = main_db(["migrate"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "fence already satisfied" in out

    def test_migrate_refusal_names_blockers_and_fails_loud(
        self, tmp_path, monkeypatch, capsys
    ):
        db = tmp_path / "context.db"
        db.write_bytes(b"")
        monkeypatch.setenv("MAGIC_CONTEXT_DB_PATH", str(db))
        monkeypatch.setattr(db_toolkit, "find_magic_context_package", lambda: tmp_path)
        monkeypatch.setattr(db_toolkit, "_package_version", lambda root: "0.44.4")
        scan = mock.Mock(
            persisted_version=90,
            latest_supported_version=91,
            pending_migrations=1,
        )
        monkeypatch.setattr(db_toolkit, "scan_store", lambda root: scan)
        monkeypatch.setattr(
            db_toolkit,
            "_run_migrate_driver",
            self._stub_driver(
                3,
                {
                    "action": "refused",
                    "persisted_version": 90,
                    "supported_version": 91,
                    "blockers": [4242],
                    "blocking_processes": [{"kind": "Pi", "pid": 4242}],
                },
            ),
        )
        rc = main_db(["migrate"])
        out = capsys.readouterr().out
        assert rc == 2
        assert "4242" in out
        assert "refused" in out

    def test_migrate_kill_blockers_drains_named_pids(
        self, tmp_path, monkeypatch, capsys
    ):
        db = tmp_path / "context.db"
        db.write_bytes(b"")
        monkeypatch.setenv("MAGIC_CONTEXT_DB_PATH", str(db))
        monkeypatch.setattr(db_toolkit, "find_magic_context_package", lambda: tmp_path)
        monkeypatch.setattr(db_toolkit, "_package_version", lambda root: "0.44.4")
        scan = mock.Mock(
            persisted_version=90,
            latest_supported_version=91,
            pending_migrations=1,
        )
        monkeypatch.setattr(db_toolkit, "scan_store", lambda root: scan)
        # First attempt refused naming PID 4242 (a classified holder),
        # second attempt succeeds.
        responses = iter(
            [
                (
                    3,
                    {
                        "action": "refused",
                        "blockers": [4242],
                        "persisted_version": 90,
                        "supported_version": 91,
                    },
                ),
                (
                    0,
                    {
                        "action": "opened",
                        "persisted_version": 91,
                        "latest_supported_version": 91,
                    },
                ),
            ]
        )
        monkeypatch.setattr(
            db_toolkit, "_run_migrate_driver", lambda *a, **k: next(responses)
        )
        holder = db_toolkit.HolderInfo(
            pid=4242, command="pi --session x", kind="pi_harness"
        )
        monkeypatch.setattr(db_toolkit, "list_holders", lambda db: [holder])
        terminated = []
        monkeypatch.setattr(
            db_toolkit,
            "_terminate_holder",
            lambda holder, grace_s=10.0: terminated.append(holder.pid) or True,
        )
        rc = main_db(["migrate", "--kill-blockers"])
        out = capsys.readouterr().out
        assert rc == 0
        assert terminated == [4242]
        assert "migration ran" in out

    def test_migrate_driver_error_fails_loud(self, tmp_path, monkeypatch, capsys):
        db = tmp_path / "context.db"
        db.write_bytes(b"")
        monkeypatch.setenv("MAGIC_CONTEXT_DB_PATH", str(db))
        monkeypatch.setattr(db_toolkit, "find_magic_context_package", lambda: tmp_path)
        monkeypatch.setattr(db_toolkit, "_package_version", lambda root: "0.44.4")
        scan = mock.Mock(
            persisted_version=90,
            latest_supported_version=91,
            pending_migrations=1,
        )
        monkeypatch.setattr(db_toolkit, "scan_store", lambda root: scan)
        monkeypatch.setattr(
            db_toolkit,
            "_run_migrate_driver",
            self._stub_driver(1, {"action": "error", "error": "boom"}),
        )
        rc = main_db(["migrate"])
        out = capsys.readouterr().out
        assert rc == 1
        assert "boom" in out


class TestDriverContract:
    """The one-shot driver's argument/JSON contract from the Python side."""

    def test_driver_invocation_shape(self, tmp_path, monkeypatch):
        calls = {}

        def fake_run(cmd, **kwargs):
            calls["cmd"] = cmd
            calls["env"] = kwargs.get("env")
            calls["timeout"] = kwargs.get("timeout")

            class R:
                returncode = 0
                stdout = '{"action": "none"}'
                stderr = ""

            return R()

        monkeypatch.setattr(db_toolkit.subprocess, "run", fake_run)
        rc, payload = db_toolkit._run_migrate_driver(tmp_path / "db", tmp_path)
        assert rc == 0
        assert payload["action"] == "none"
        assert calls["cmd"][:2] == ["node", "--no-warnings"]
        assert str(tmp_path / "db") in calls["cmd"]
        # explicit package root must reach the driver and win over env
        assert str(tmp_path) in calls["cmd"]
        assert "MAGIC_CONTEXT_PACKAGE_ROOT" not in calls["env"]


class TestBridgeBuildAttribution:
    def test_labels_wheel_and_upstream(self, tmp_path):
        site = tmp_path / "site-packages"
        pkg = site / "magic_hermes"
        bridge = pkg / "bridge"
        bridge.mkdir(parents=True)
        (pkg / "magic_context_compat.json").write_text(
            json.dumps({"tested_version": "0.44.4"}), encoding="utf-8"
        )
        (site / "magic_hermes-0.3.9.dist-info").mkdir()
        label, upstream = db_toolkit._bridge_build(str(bridge / "runtime.mjs"))
        assert label == "magic-hermes 0.3.9"
        assert upstream == "0.44.4"


class TestDbSubcommandWiring:
    def test_status_json_is_parseable(self, tmp_path, monkeypatch, capsys):
        db = tmp_path / "context.db"
        con = sqlite3.connect(db)
        con.execute(
            "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY,"
            " description TEXT NOT NULL, applied_at INTEGER NOT NULL)"
        )
        con.execute("INSERT INTO schema_migrations VALUES (91, 'now', 0)")
        con.commit()
        con.close()
        monkeypatch.setenv("MAGIC_CONTEXT_DB_PATH", str(db))
        monkeypatch.setattr(db_toolkit, "find_magic_context_package", lambda: tmp_path)
        rc = main_db(["status", "--json"])
        payload = json.loads(capsys.readouterr().out)
        assert rc == 0
        assert payload["persisted_version"] == 91

    def test_unknown_action_fails(self, capsys):
        assert main_db(["explode"]) == 1

    def test_no_action_prints_usage(self, capsys):
        assert main_db([]) == 1
        assert "usage:" in capsys.readouterr().out


class TestDbCensus:
    """`db census` (plan 2026-11-28-001 U1): read-only readability sampler.

    The census replicates maestro's bounded-operational-scan reader
    byte-for-byte (mode=ro + query_only + busy_timeout=8000 + the frozen
    query sequence) and records lock-window evidence to JSONL OUTSIDE
    the repository tree. Exit contract: 0 = every tick read clean;
    2 = lock events observed (the operational signal); 1 = harness error.
    """

    SCHEMA = (
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
            "CREATE TABLE task_schedule_state (task TEXT PRIMARY KEY,"
            " last_run_at TEXT, next_due_at TEXT, last_status TEXT,"
            " retry_count INTEGER)"
        ),
        (
            "CREATE TABLE mirror_cursors (domain TEXT PRIMARY KEY, cursor TEXT,"
            " updated_at TEXT)"
        ),
        "CREATE TABLE memory_verifications (id INTEGER PRIMARY KEY, verified_at TEXT)",
    )

    def _fixture_store(self, tmp_path, monkeypatch) -> Path:
        db = tmp_path / "fixture.db"
        con = sqlite3.connect(db)
        con.execute("PRAGMA journal_mode=WAL")
        for ddl in self.SCHEMA:
            con.execute(ddl)
        con.commit()
        con.close()
        monkeypatch.setenv("MAGIC_CONTEXT_DB_PATH", str(db))
        return db

    def test_single_tick_census_clean(self, tmp_path, monkeypatch, capsys):
        self._fixture_store(tmp_path, monkeypatch)
        out = tmp_path / "census.jsonl"
        rc = main_db(["census", "--out", str(out), "--json"])
        payload = json.loads(capsys.readouterr().out)
        assert rc == 0
        assert payload["census"] == "ok"
        assert payload["ticks"] == 1
        assert payload["events"] == 0
        assert payload["evidence"] == str(out)
        rows = [json.loads(line) for line in out.read_text().splitlines()]
        assert len(rows) == 1
        assert rows[0]["ok"] is True
        assert rows[0]["exception"] is None
        assert rows[0]["failing_query"] is None
        assert rows[0]["latency_ms"] >= 0.0

    def test_census_multi_tick_and_default_interval(self, tmp_path, monkeypatch, capsys):
        import time as _time

        self._fixture_store(tmp_path, monkeypatch)
        out = tmp_path / "census.jsonl"
        started = _time.monotonic()
        rc = main_db(
            [
                "census",
                "--duration",
                "0.5",
                "--interval",
                "0.15",
                "--out",
                str(out),
                "--json",
            ]
        )
        elapsed = _time.monotonic() - started
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["ticks"] >= 2
        rows = [json.loads(line) for line in out.read_text().splitlines()]
        assert len(rows) == payload["ticks"]
        assert all(row["ok"] for row in rows)
        assert 0.3 <= elapsed < 5.0

    def test_census_default_output_lands_in_xdg_state_dir(
        self, tmp_path, monkeypatch, capsys
    ):
        self._fixture_store(tmp_path, monkeypatch)
        state = tmp_path / "xdg-state"
        monkeypatch.setenv("XDG_STATE_HOME", str(state))
        rc = main_db(["census", "--json"])
        payload = json.loads(capsys.readouterr().out)
        assert rc == 0
        evidence = Path(payload["evidence"])
        assert evidence.parent == state / "magic-hermes" / "census"
        assert evidence.exists() and evidence.suffix == ".jsonl"

    def test_census_records_exclusive_holder_event(self, tmp_path, monkeypatch, capsys):
        import os as _os
        import time as _time

        db = self._fixture_store(tmp_path, monkeypatch)
        out = tmp_path / "census.jsonl"
        # THE empirically reproducible reader-blocker on this host
        # (SQLite 3.53.1): a live holder in PRAGMA locking_mode=EXCLUSIVE.
        holder = sqlite3.connect(db)
        holder.execute("PRAGMA locking_mode=EXCLUSIVE")
        holder.execute("BEGIN IMMEDIATE")
        holder.execute("INSERT INTO pending_ops VALUES (1)")
        try:
            started = _time.monotonic()
            rc = main_db(["census", "--out", str(out), "--json"])
            elapsed = _time.monotonic() - started
        finally:
            holder.rollback()
            holder.close()
        payload = json.loads(capsys.readouterr().out)
        assert rc == 2
        assert payload["events"] == 1
        assert payload["census"] == "events"
        rows = [json.loads(line) for line in out.read_text().splitlines()]
        assert len(rows) == 1
        row = rows[0]
        assert row["ok"] is False
        assert "database is locked" in row["exception"]
        assert row["failing_query"] == "schema_table_count"
        # the lsof holder census names the live holder (this pytest process)
        assert any(h["pid"] == _os.getpid() for h in row["holders"])
        # the post-event recovery probe is recorded, bounded and roll-back only
        assert row["recovery"]["recovered"] is False
        assert "database is locked" in row["recovery"]["exception"]
        # 8 s reader busy window + 10 s recovery busy window, bounded
        assert 15.0 <= elapsed < 30.0

    def test_census_store_missing_rc2(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("MAGIC_CONTEXT_DB_PATH", str(tmp_path / "absent.db"))
        rc = main_db(["census", "--json"])
        payload = json.loads(capsys.readouterr().out)
        assert rc == 2
        assert "not found" in payload["error"]
        assert not (tmp_path / "absent.db").exists()
        assert not tmp_path.joinpath("census").exists()

    @pytest.mark.skipif(
        os.geteuid() == 0,
        reason="running as root: directory permissions do not block writes",
    )
    def test_census_output_dir_unwritable_rc1(self, tmp_path, monkeypatch, capsys):
        self._fixture_store(tmp_path, monkeypatch)
        ro_dir = tmp_path / "ro"
        ro_dir.mkdir()
        ro_dir.chmod(0o555)
        try:
            rc = main_db(["census", "--out", str(ro_dir / "c.jsonl"), "--json"])
        finally:
            ro_dir.chmod(0o755)
        assert rc == 1
        out = capsys.readouterr().out
        assert "census refused" in out
        assert "fix permissions or pass --out" in out or "cannot write evidence" in out

    def test_census_invalid_duration_fails_loud(self, tmp_path, monkeypatch, capsys):
        self._fixture_store(tmp_path, monkeypatch)
        rc = main_db(
            ["census", "--duration", "banana", "--out", str(tmp_path / "c.jsonl")]
        )
        assert rc == 1
        assert "invalid --duration value" in capsys.readouterr().out
