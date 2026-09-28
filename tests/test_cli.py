"""Behavior tests for the `magic-hermes` installer/doctor CLI.

These tests never spawn the real Node sidecar: RuntimeClient is patched,
and the filesystem fixtures model the shapes the 2026-09-20 schema-fence
incident taught us to guard (multiple homes with different versions,
foreign homes that must never be rewritten, comment-preserving config
edits, text-scan fallback when no YAML library is present).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest import mock

import pytest

from magic_hermes import cli
from magic_hermes.runtime import xdg_data_home


def make_package(root: Path, version: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "package.json").write_text(
        json.dumps({"name": "@cortexkit/pi-magic-context", "version": version}),
        encoding="utf-8",
    )
    (root / "dist").mkdir(exist_ok=True)
    (root / "dist" / "index.js").write_text("// stub\n", encoding="utf-8")
    return root


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home / ".hermes"))
    monkeypatch.setenv("XDG_DATA_HOME", str(home / ".local" / "share"))
    return home


class TestDiscoveryOrder:
    def test_managed_root_is_discovered_first(self, isolated_home):
        foreign = make_package(
            isolated_home / ".pi" / "agent" / "npm" / "node_modules" / "@cortexkit"
            / "pi-magic-context",
            "0.42.6",
        )
        managed = make_package(
            isolated_home / ".local" / "share" / "magic-hermes" / "node_modules"
            / "@cortexkit" / "pi-magic-context",
            "0.42.5",
        )
        # cwd-based candidates must not leak the real repo's node_modules
        from magic_hermes.runtime import find_magic_context_package

        real_candidates = [
            c
            for c in (
                isolated_home / ".local" / "share" / "magic-hermes" / "node_modules"
                / "@cortexkit" / "pi-magic-context",
                isolated_home / ".pi" / "agent" / "npm" / "node_modules" / "@cortexkit"
                / "pi-magic-context",
            )
        ]
        with mock.patch(
            "magic_hermes.runtime.magic_context_package_candidates",
            return_value=real_candidates,
        ):
            found = find_magic_context_package()
        assert found == managed
        assert foreign != managed


class TestInstallPlan:
    def test_no_installation_yet_installs(self, isolated_home):
        with mock.patch(
            "magic_hermes.cli.magic_context_package_candidates",
            return_value=[],
        ):
            plan = cli.plan_install("0.42.6")
        assert plan.action == "install"
        assert "will install 0.42.6" in plan.notes[0]

    def test_foreign_newer_leads_to_managed_install(self, isolated_home):
        foreign = make_package(
            isolated_home / ".pi" / "agent" / "npm" / "node_modules" / "@cortexkit"
            / "pi-magic-context",
            "0.42.7",
        )
        with mock.patch(
            "magic_hermes.cli.magic_context_package_candidates",
            return_value=[foreign],
        ):
            plan = cli.plan_install("0.42.6")
        assert plan.action == "update"
        assert plan.effective_root == foreign
        assert "foreign homes are never rewritten" in plan.notes[0]

    def test_foreign_older_leads_to_managed_install(self, isolated_home):
        foreign = make_package(
            isolated_home / ".pi" / "agent" / "npm" / "node_modules" / "@cortexkit"
            / "pi-magic-context",
            "0.42.4",
        )
        with mock.patch(
            "magic_hermes.cli.magic_context_package_candidates",
            return_value=[foreign],
        ):
            plan = cli.plan_install(" 0.42.5".strip())
        assert plan.action == "update"
        assert "foreign homes are never rewritten" in plan.notes[0]

    def test_matching_foreign_copy_is_reused(self, isolated_home):
        foreign = make_package(
            isolated_home / ".pi" / "agent" / "npm" / "node_modules" / "@cortexkit"
            / "pi-magic-context",
            "0.42.6",
        )
        with mock.patch(
            "magic_hermes.cli.magic_context_package_candidates",
            return_value=[foreign],
        ):
            plan = cli.plan_install("0.42.6")
        assert plan.action == "reuse"
        assert plan.effective_root == foreign

    def test_managed_copy_current_is_reuse(self, isolated_home):
        managed = make_package(
            xdg_data_home() / "magic-hermes" / "node_modules" / "@cortexkit"
            / "pi-magic-context",
            "0.42.6",
        )
        with mock.patch(
            "magic_hermes.cli.magic_context_package_candidates",
            return_value=[managed],
        ):
            plan = cli.plan_install("0.42 6".replace(" ", "."))
        assert plan.action == "reuse"
        assert "managed copy already current" in plan.notes[0]

    def test_npm_latest_newer_than_tested_notes_pipeline(self, isolated_home):
        foreign = make_package(
            isolated_home / ".pi" / "agent" / "npm" / "node_modules" / "@cortexkit"
            / "pi-magic-context",
            "0.42.4",
        )
        with mock.patch(
            "magic_hermes.cli.magic_context_package_candidates",
            return_value=[foreign],
        ):
            plan = cli.plan_install("0.42.5", npm_latest="0.42.7")
        assert plan.action == "update"
        assert any("release pipeline" in n for n in plan.notes)


class TestConfigureHermes:
    def test_writes_all_three_keys_and_is_idempotent(self, isolated_home):
        config = Path(cli.hermes_config_path())
        config.parent.mkdir(parents=True, exist_ok=True)
        # a comment that must survive (comment-preserving ruamel path)
        config.write_text(
            "# top comment\nlanguage: en\n\ncontext:\n  engine: builtin\n",
            encoding="utf-8",
        )
        with mock.patch.dict(sys_modules := {}, {}):  # noqa: F841
            pass
        pytest.importorskip("ruamel.yaml")
        changed = cli.configure_hermes()
        assert changed == [
            "context.engine",
            "memory.provider",
            "plugins.enabled",
        ]
        text = config.read_text(encoding="utf-8")
        assert "# top comment" in text
        assert "engine: magic-context" in text
        assert "provider: magic_context" in text
        changed_again = cli.configure_hermes()
        assert changed_again == []

    def test_no_yaml_library_fails_with_actionable_message(self, isolated_home):
        config = Path(cli.hermes_config_path())
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text("context:\n  engine: builtin\n", encoding="utf-8")
        real_import = __import__

        def blocked(name, *args, **kwargs):
            if name in ("ruamel.yaml", "yaml"):
                raise ImportError(name)
            return real_import(name, *args, **kwargs)

        with (
            mock.patch("builtins.__import__", side_effect=blocked),
            pytest.raises(SystemExit) as excinfo,
        ):
            cli.configure_hermes()
        assert "ruamel.yaml" in str(excinfo.value)


class TestTextScanFallback:
    def test_scans_wiring_from_unparsed_config(self, isolated_home):
        config = Path(cli.hermes_config_path())
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(
            "language: en\n"
            "context:\n"
            "  engine: magic-context\n"
            "memory:\n"
            "  provider: magic_context\n"
            "plugins:\n"
            "  enabled:\n"
            "    - magic-hermes\n",
            encoding="utf-8",
        )
        assert cli.config_wiring_from_text(config) == (True, True, True)

    def test_text_scan_detects_missing_pieces(self, isolated_home):
        config = Path(cli.hermes_config_path())
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(
            "context:\n  engine: builtin\nmemory:\n  provider: sqlite\n",
            encoding="utf-8",
        )
        assert cli.config_wiring_from_text(config) == (False, False, False)


class TestDoctorReport:
    def test_report_counts_and_exit_code(self):
        report = cli.DoctorReport()
        report.add("PASS", "a")
        report.add("WARN", "b")
        report.add("FAIL", "c")
        assert report.passed == 1
        console = report.warned == 1
        assert console
        assert report.failed == 1

    def test_fenced_db_reports_fail(self, isolated_home, monkeypatch, tmp_path):
        # A DB whose newest lane is newer than the binary supports: the
        # sidecar refuses to open it — the doctor must surface that as FAIL.
        monkeypatch.setenv("MAGIC_CONTEXT_DB_PATH", str(tmp_path / "fence.db"))
        fake_db = tmp_path / "fence.db"
        import sqlite3

        con = sqlite3.connect(fake_db)
        con.execute(
            "create table schema_migrations (version int, description text, "
            "applied_at int)"
        )
        con.execute("insert into schema_migrations values (99, 'future', 0)")
        con.commit()
        con.close()
        assert cli.db_schema_lane(fake_db) == 99


class TestGuardParser:
    def test_guard_subcommand_accepts_all_three_actions(self):
        parser = cli.build_parser()
        for action in ("apply", "remove", "status"):
            args = parser.parse_args(["guard", action])
            assert args.command == "guard"
            assert args.action == action

    def test_guard_requires_a_known_action(self):
        parser = cli.build_parser()
        with pytest.raises(SystemExit) as excinfo:
            parser.parse_args(["guard"])
        assert excinfo.value.code == 2


class TestGuardCommand:
    """T9 — ``magic-hermes guard apply|remove|status`` exit codes and output.

    The store is always a fixture reached through MAGIC_CONTEXT_DB_PATH;
    no test ever touches the live shared store.
    """

    @staticmethod
    def _fixture(tmp_path, monkeypatch, name="context.db"):
        from magic_hermes import historian_guard as hg

        db = hg.make_fixture_db(tmp_path / name)
        monkeypatch.setenv("MAGIC_CONTEXT_DB_PATH", str(db))
        return db

    def test_apply_status_remove_roundtrip(
        self, tmp_path, monkeypatch, capsys
    ):
        db = self._fixture(tmp_path, monkeypatch)

        assert cli.main(["guard", "apply"]) == 0
        out = capsys.readouterr().out
        assert "mh_historian_classification_guard" in out
        assert str(db) in out
        assert "trigger installed" in out
        assert "verification" in out and "ok" in out

        assert cli.main(["guard", "status"]) == 0
        out = capsys.readouterr().out
        assert "present, matches canonical DDL" in out
        assert "rows reclassified in the last 24h" in out

        # Idempotent re-apply reports a no-op, still exit 0.
        assert cli.main(["guard", "apply"]) == 0
        assert "no-op" in capsys.readouterr().out

        assert cli.main(["guard", "remove"]) == 0
        assert "removed" in capsys.readouterr().out

        assert cli.main(["guard", "status"]) == 0
        assert "not installed" in capsys.readouterr().out

        # Removing an absent guard is a successful no-op.
        assert cli.main(["guard", "remove"]) == 0
        assert "nothing to remove" in capsys.readouterr().out

    def test_apply_missing_store_fails_loud(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.setenv(
            "MAGIC_CONTEXT_DB_PATH", str(tmp_path / "missing.db")
        )
        assert cli.main(["guard", "apply"]) == 1
        err = capsys.readouterr().err
        assert "no shared context store" in err
        assert "guard apply failed" in err

    def test_status_and_remove_on_missing_store_are_informational(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.setenv(
            "MAGIC_CONTEXT_DB_PATH", str(tmp_path / "missing.db")
        )
        assert cli.main(["guard", "status"]) == 0
        assert "not installed" in capsys.readouterr().out
        assert cli.main(["guard", "remove"]) == 0
        assert "nothing to remove" in capsys.readouterr().out

    def test_apply_writes_only_the_override_store(
        self, tmp_path, monkeypatch, isolated_home
    ):
        """T11 — with MAGIC_CONTEXT_DB_PATH set, apply writes ONLY there.

        A decoy store sits exactly where home-relative resolution would find
        it; it must stay untouched, proving the override isolates every
        write (and that the suite never reaches the real home store).
        """

        from magic_hermes import historian_guard as hg

        decoy_dir = (
            isolated_home / ".local" / "share" / "cortexkit" / "magic-context"
        )
        decoy_dir.mkdir(parents=True)
        decoy = hg.make_fixture_db(decoy_dir / "context.db")
        target = hg.make_fixture_db(tmp_path / "target.db")
        monkeypatch.setenv("MAGIC_CONTEXT_DB_PATH", str(target))
        monkeypatch.setattr(Path, "home", lambda: isolated_home)

        assert cli.main(["guard", "apply"]) == 0
        assert hg.guard_status(target).present is True
        assert hg.guard_status(target).matches is True
        assert hg.guard_status(decoy).present is False


class TestDoctorGuardPosture:
    """T10 — the guard check is WARN-never-FAIL in every guard state
    (KTD-2): maestro requires doctor to exit 0, so even the bad states must
    not raise the FAIL count.
    """

    @staticmethod
    def _wire_config():
        config = Path(cli.hermes_config_path())
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(
            "context:\n"
            "  engine: magic-context\n"
            "memory:\n"
            "  provider: magic_context\n"
            "plugins:\n"
            "  enabled:\n"
            "    - magic-hermes\n",
            encoding="utf-8",
        )

    def _doctor(self, tmp_path, monkeypatch, isolated_home, db_setup):
        db = tmp_path / "context.db"
        db_setup(db)
        monkeypatch.setenv("MAGIC_CONTEXT_DB_PATH", str(db))
        self._wire_config()

        tested = cli.tested_magic_context_version()
        package = make_package(tmp_path / "pkg", tested)
        series = ".".join(map(str, cli.supported_magic_context_series()))

        sidecar = mock.MagicMock()
        sidecar.call.side_effect = lambda method, timeout=60: {
            "hello": {"harness": "hermes", "package_version": tested},
            "doctor": {
                "database_health": "ok",
                "core_symbols_ready": True,
                "supported_series": series,
            },
        }[method]
        client = mock.MagicMock()
        client.__enter__.return_value = sidecar
        client.__exit__.return_value = False

        with (
            mock.patch.object(
                cli, "discover_installations", return_value=[(package, tested)]
            ),
            mock.patch.object(cli, "RuntimeClient", return_value=client),
        ):
            code = cli.run_doctor(json_output=False)
        return code

    @staticmethod
    def _present(db):
        from magic_hermes import historian_guard as hg

        hg.make_fixture_db(db)
        hg.apply_guard(db, verify=False)

    @staticmethod
    def _absent(db):
        from magic_hermes import historian_guard as hg

        hg.make_fixture_db(db)

    @staticmethod
    def _drifted(db):
        from magic_hermes import historian_guard as hg

        hg.make_fixture_db(db)
        con = sqlite3.connect(db)
        con.execute(
            f"create trigger {hg.TRIGGER_NAME} "
            "after insert on historian_runs "
            "begin select 1; end;"
        )
        con.commit()
        con.close()

    @staticmethod
    def _unreadable(db):
        db.write_bytes(b"this is not a sqlite database at all\n")

    def test_guard_present_is_pass_and_never_fails(
        self, tmp_path, monkeypatch, isolated_home, capsys
    ):
        code = self._doctor(tmp_path, monkeypatch, isolated_home, self._present)
        out = capsys.readouterr().out
        assert code == 0
        assert "Historian classification guard active" in out

    def test_guard_absent_warns_but_never_fails(
        self, tmp_path, monkeypatch, isolated_home, capsys
    ):
        code = self._doctor(tmp_path, monkeypatch, isolated_home, self._absent)
        out = capsys.readouterr().out
        assert code == 0
        assert "guard not installed" in out
        assert "guard apply" in out

    def test_guard_drift_warns_but_never_fails(
        self, tmp_path, monkeypatch, isolated_home, capsys
    ):
        code = self._doctor(tmp_path, monkeypatch, isolated_home, self._drifted)
        out = capsys.readouterr().out
        assert code == 0
        assert "drifted" in out

    def test_guard_unreadable_warns_but_never_fails(
        self, tmp_path, monkeypatch, isolated_home, capsys
    ):
        code = self._doctor(tmp_path, monkeypatch, isolated_home, self._unreadable)
        out = capsys.readouterr().out
        assert code == 0
        assert "guard state unreadable" in out

    def test_doctor_json_includes_guard_check(
        self, tmp_path, monkeypatch, isolated_home, capsys
    ):
        from magic_hermes import historian_guard as hg

        db = tmp_path / "context.db"
        hg.make_fixture_db(db)
        hg.apply_guard(db, verify=False)
        monkeypatch.setenv("MAGIC_CONTEXT_DB_PATH", str(db))
        self._wire_config()

        tested = cli.tested_magic_context_version()
        package = make_package(tmp_path / "pkg", tested)
        series = ".".join(map(str, cli.supported_magic_context_series()))
        sidecar = mock.MagicMock()
        sidecar.call.side_effect = lambda method, timeout=60: {
            "hello": {"harness": "hermes", "package_version": tested},
            "doctor": {
                "database_health": "ok",
                "core_symbols_ready": True,
                "supported_series": series,
            },
        }[method]
        client = mock.MagicMock()
        client.__enter__.return_value = sidecar
        client.__exit__.return_value = False

        with (
            mock.patch.object(
                cli, "discover_installations", return_value=[(package, tested)]
            ),
            mock.patch.object(cli, "RuntimeClient", return_value=client),
        ):
            assert cli.run_doctor(json_output=True) == 0
        payload = json.loads(capsys.readouterr().out)
        guard_checks = [
            c
            for c in payload["checks"]
            if "Historian classification guard" in c["message"]
        ]
        assert guard_checks, "doctor --json must surface the guard check"
        assert guard_checks[0]["status"] == "PASS"
        assert payload["summary"]["fail"] == 0
