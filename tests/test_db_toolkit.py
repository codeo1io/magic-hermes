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
import sqlite3
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
