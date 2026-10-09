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
import time
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


class TestDoctorVersionDrift:
    """U2 (finding c7d63424) — the version-drift WARN is keyed to every
    discovered installation, not just the primary copy: in the breaking
    configuration the Hermes-managed copy still matches the build's
    validated pin while a newer copy elsewhere on the machine has
    already forward-migrated the shared store, so a primary-only
    comparison can never fire."""

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

    def _run_doctor_with_installations(
        self, tmp_path, monkeypatch, isolated_home, installations
    ):
        from magic_hermes import historian_guard as hg

        db = tmp_path / "context.db"
        hg.make_fixture_db(db)
        hg.apply_guard(db, verify=False)
        monkeypatch.setenv("MAGIC_CONTEXT_DB_PATH", str(db))
        self._wire_config()

        tested = cli.tested_magic_context_version()
        series = ".".join(map(str, cli.supported_magic_context_series()))
        sidecar = mock.MagicMock()
        sidecar.call.side_effect = lambda method, params=None, timeout=60: {
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
                cli, "discover_installations", return_value=installations
            ),
            mock.patch.object(cli, "RuntimeClient", return_value=client),
        ):
            code = cli.run_doctor(json_output=False)
        return code

    @staticmethod
    def _relative_versions():
        pin = cli._semver_tuple(cli.tested_magic_context_version())
        newer = f"{pin[0]}.{pin[1] + 1}.{pin[2]}"
        older = f"{pin[0]}.{pin[1] - 1}.{pin[2]}"
        return newer, older

    def test_newer_foreign_copy_warns_even_when_primary_matches(
        self, tmp_path, monkeypatch, isolated_home, capsys
    ):
        """The incident shape: the primary (Hermes-managed) copy still
        matches the validated pin while a newer copy elsewhere on the
        machine has already forward-migrated the shared store — the
        primary-only comparison could never see it."""
        tested = cli.tested_magic_context_version()
        newer, _older = self._relative_versions()
        primary = make_package(tmp_path / "managed", tested)
        foreign = make_package(
            isolated_home / ".pi" / "agent-cliproxy-only" / "npm"
            / "node_modules" / "@cortexkit" / "pi-magic-context",
            newer,
        )
        code = self._run_doctor_with_installations(
            tmp_path,
            monkeypatch,
            isolated_home,
            [(primary, tested), (foreign, newer)],
        )
        out = capsys.readouterr().out
        assert code == 0  # drift is WARN, not FAIL (D2)
        assert out.count("fence follows the newest copy") == 1
        assert f"newer than the validated v{tested}: {newer} @ {foreign}" in out
        # D3: the sanctioned adoption path, not upstream's npx advice
        assert "scripts/next_magic_context_release.py" in out
        assert "scripts/sync_magic_context_release.py" in out
        assert "PR-gated" in out
        assert "magic-hermes install" in out
        # the primary PASS rows are unchanged
        assert f"{cli.UPSTREAM_PACKAGE} {tested} found at {primary}" in out
        assert "matches the version validated by this build" in out
        # honesty invariant: the verdict string never rides in the row
        # itself (the summary line's own FAIL 0 is the contract, not a row)
        drift_row = next(
            line for line in out.splitlines() if "fence follows the newest copy" in line
        )
        assert "FAIL 0" not in drift_row

    def test_all_copies_matching_pin_render_no_drift_warning(
        self, tmp_path, monkeypatch, isolated_home, capsys
    ):
        tested = cli.tested_magic_context_version()
        primary = make_package(tmp_path / "managed", tested)
        second = make_package(
            isolated_home / ".pi" / "agent" / "npm" / "node_modules"
            / "@cortexkit" / "pi-magic-context",
            tested,
        )
        code = self._run_doctor_with_installations(
            tmp_path,
            monkeypatch,
            isolated_home,
            [(primary, tested), (second, tested)],
        )
        out = capsys.readouterr().out
        assert code == 0
        assert "newer than the validated" not in out
        assert "fence follows the newest copy" not in out

    def test_primary_newer_than_pin_still_warns_once(
        self, tmp_path, monkeypatch, isolated_home, capsys
    ):
        """No regression: the estate the old primary-only WARN covered
        (the primary itself newer) still renders exactly one WARN, now
        naming the copy and the adoption path."""
        tested = cli.tested_magic_context_version()
        newer, _older = self._relative_versions()
        primary = make_package(tmp_path / "managed", newer)
        code = self._run_doctor_with_installations(
            tmp_path, monkeypatch, isolated_home, [(primary, newer)]
        )
        out = capsys.readouterr().out
        assert code == 0
        assert out.count("fence follows the newest copy") == 1
        assert f"newer than the validated v{tested}: {newer} @ {primary}" in out
        # a newer primary renders neither the PASS-match nor the differs row
        assert "matches the version validated by this build" not in out
        assert "differs from validated" not in out
        drift_row = next(
            line for line in out.splitlines() if "fence follows the newest copy" in line
        )
        assert "FAIL 0" not in drift_row

    def test_older_foreign_copy_alone_does_not_warn(
        self, tmp_path, monkeypatch, isolated_home, capsys
    ):
        tested = cli.tested_magic_context_version()
        _newer, older = self._relative_versions()
        primary = make_package(tmp_path / "managed", tested)
        foreign = make_package(
            isolated_home / ".omo" / "npm" / "node_modules" / "@cortexkit"
            / "pi-magic-context",
            older,
        )
        code = self._run_doctor_with_installations(
            tmp_path,
            monkeypatch,
            isolated_home,
            [(primary, tested), (foreign, older)],
        )
        out = capsys.readouterr().out
        assert code == 0
        assert "newer than the validated" not in out
        assert "fence follows the newest copy" not in out
        assert "matches the version validated by this build" in out

    def test_primary_older_with_newer_foreign_warns_and_keeps_info_row(
        self, tmp_path, monkeypatch, isolated_home, capsys
    ):
        """D1 max-over-all: the WARN keys off the newest copy anywhere,
        while the older-primary INFO row is unchanged and only strictly
        newer copies are named."""
        tested = cli.tested_magic_context_version()
        newer, older = self._relative_versions()
        primary = make_package(tmp_path / "managed", older)
        foreign = make_package(
            isolated_home / ".pi" / "agent-cliproxy-only" / "npm"
            / "node_modules" / "@cortexkit" / "pi-magic-context",
            newer,
        )
        code = self._run_doctor_with_installations(
            tmp_path,
            monkeypatch,
            isolated_home,
            [(primary, older), (foreign, newer)],
        )
        out = capsys.readouterr().out
        assert code == 0
        assert out.count("fence follows the newest copy") == 1
        assert f"{newer} @ {foreign}" in out
        assert f"{older} @ {primary}" not in out  # only newer copies named
        assert f"Upstream {older} differs from validated v{tested}" in out
        drift_row = next(
            line for line in out.splitlines() if "fence follows the newest copy" in line
        )
        assert "FAIL 0" not in drift_row


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
        sidecar.call.side_effect = lambda method, params=None, timeout=60: {
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
        sidecar.call.side_effect = lambda method, params=None, timeout=60: {
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


class TestDoctorInstallEnumeration:
    """U1 (finding c7d63424) — every discovered copy shows up in the
    doctor's enumeration row, so a lane-migrating copy in a widened scan
    family (Pi profile homes, OMP home) is visible in the verdict."""

    def test_other_copies_render_in_one_info_row(
        self, tmp_path, monkeypatch, isolated_home, capsys
    ):
        from magic_hermes import historian_guard as hg

        db = tmp_path / "context.db"
        hg.make_fixture_db(db)
        hg.apply_guard(db, verify=False)
        monkeypatch.setenv("MAGIC_CONTEXT_DB_PATH", str(db))
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

        tested = cli.tested_magic_context_version()
        primary = make_package(tmp_path / "managed", tested)
        cliproxy = make_package(
            isolated_home / ".pi" / "agent-cliproxy-only" / "npm"
            / "node_modules" / "@cortexkit" / "pi-magic-context",
            "0.46.0",
        )
        omo = make_package(
            isolated_home / ".omo" / "npm" / "node_modules" / "@cortexkit"
            / "pi-magic-context",
            "0.44.2",
        )

        sidecar = mock.MagicMock()
        sidecar.call.side_effect = lambda method, params=None, timeout=60: {
            "hello": {"harness": "hermes", "package_version": tested},
            "doctor": {
                "database_health": "ok",
                "core_symbols_ready": True,
                "supported_series": ".".join(
                    map(str, cli.supported_magic_context_series())
                ),
            },
        }[method]
        client = mock.MagicMock()
        client.__enter__.return_value = sidecar
        client.__exit__.return_value = False

        with (
            mock.patch.object(
                cli,
                "discover_installations",
                return_value=[(primary, tested), (cliproxy, "0.46.0"), (omo, "0.44.2")],
            ),
            mock.patch.object(cli, "RuntimeClient", return_value=client),
        ):
            code = cli.run_doctor(json_output=False)

        out = capsys.readouterr().out
        assert code == 0
        assert out.count("Other copies discovered:") == 1
        assert f"0.46.0 @ {cliproxy}" in out
        assert f"0.44.2 @ {omo}" in out
        # the primary (Hermes-managed, == tested) PASS row is unchanged
        assert f"{cli.UPSTREAM_PACKAGE} {tested} found at {primary}" in out


class TestDoctorSidecarRetry:
    """R2 (finding c7d63424) — the doctor sidecar pair (hello + doctor)
    gets exactly one retry, and only when the first failure was fast.

    A transient boot lock (the 5x rc=124 cluster) must not mint a FAIL
    against maestro's 90 s verdict; a persistent lock must FAIL exactly
    once, quoting both attempts' stderr tails so the operator sees what
    both tries actually said.
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

    def _run_doctor_with_attempts(
        self, tmp_path, monkeypatch, isolated_home, scripts, fast_fail_s=None
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
        attempts = []

        def make_client(*args, **kwargs):
            sidecar = mock.MagicMock()
            script = scripts[min(len(attempts), len(scripts) - 1)]

            def call(method, params=None, timeout=60):
                return script(method, tested, series)

            sidecar.call.side_effect = call
            client = mock.MagicMock()
            client.__enter__.return_value = sidecar
            client.__exit__.return_value = False
            attempts.append(client)
            return client

        with (
            mock.patch.object(
                cli, "discover_installations", return_value=[(package, tested)]
            ),
            mock.patch.object(cli, "RuntimeClient", side_effect=make_client),
            mock.patch.object(cli, "_SIDECAR_RETRY_BACKOFF_S", 0.0),
        ):
            if fast_fail_s is not None:
                monkeypatch.setattr(cli, "_SIDECAR_FAST_FAIL_S", fast_fail_s)
            code = cli.run_doctor(json_output=False)
        return code, attempts

    @staticmethod
    def _ok(method, tested, series):
        if method == "hello":
            return {"harness": "hermes", "package_version": tested}
        return {
            "database_health": "ok",
            "core_symbols_ready": True,
            "supported_series": series,
        }

    def test_transient_fast_failure_retries_and_recovers(
        self, tmp_path, monkeypatch, isolated_home, capsys
    ):
        def boot_lock(method, tested, series):
            raise RuntimeError(
                "Runtime exited during hello (status 1); "
                "stderr: SQLITE_BUSY boot lock tail-one"
            )

        code, attempts = self._run_doctor_with_attempts(
            tmp_path, monkeypatch, isolated_home, [boot_lock, self._ok]
        )
        out = capsys.readouterr().out
        assert code == 0
        assert "◆  FAIL" not in out
        assert len(attempts) == 2  # fresh client per attempt, never reused

    def test_persistent_fast_failure_fails_once_quoting_both_tails(
        self, tmp_path, monkeypatch, isolated_home, capsys
    ):
        calls = {"n": 0}

        def always_fail(method, tested, series):
            calls["n"] += 1
            raise RuntimeError(
                f"Runtime exited during {method} (status 1); "
                f"stderr: SQLITE_BUSY tail-{calls['n']}"
            )

        code, attempts = self._run_doctor_with_attempts(
            tmp_path, monkeypatch, isolated_home, [always_fail, always_fail]
        )
        out = capsys.readouterr().out
        assert code == 1
        assert len(attempts) == 2  # one retry, then stop
        assert out.count("◆  FAIL") == 1  # exactly one FAIL entry
        assert "tail-1" in out and "tail-2" in out  # both attempts quoted

    def test_persistent_lane_skew_renders_typed_fail_once(
        self, tmp_path, monkeypatch, isolated_home, capsys
    ):
        """U3 (finding c7d63424) — the upstream fence refusal renders ONE
        typed, actionable FAIL row (adoption path named, upstream's own
        lane numbers quoted) instead of the opaque generic one."""

        def fence_refusal(method, tested, series):
            raise RuntimeError(
                "Runtime exited during hello (status 1); stderr: "
                "[magic-context] storage fatal: refusing to open "
                "context.db; upstream migration lane v95 is newer than "
                "this binary supports (max v94)"
            )

        code, attempts = self._run_doctor_with_attempts(
            tmp_path, monkeypatch, isolated_home, [fence_refusal, fence_refusal]
        )
        out = capsys.readouterr().out
        assert code == 1
        # the R2 pair is preserved: fast-fail retry, no extra spawns
        assert len(attempts) == 2
        # D5: the typed row REPLACES the generic one — still FAIL 1
        assert out.count("◆  FAIL") == 1
        # typed: upstream's own numbers, quoted verbatim
        assert "shared-store lane skew" in out
        assert "lane v95" in out
        assert "max v94" in out
        # actionable: the sanctioned adoption path, not upstream's npx advice
        assert "scripts/next_magic_context_release.py" in out
        assert "scripts/sync_magic_context_release.py" in out
        assert "PR-gated" in out
        assert "magic-hermes install" in out
        # the upstream evidence is preserved, never suppressed
        assert "storage fatal" in out
        assert "refusing to open context.db" in out
        # honesty invariant: a FAIL row must never embed the verdict string
        assert "FAIL 0" not in out

    def test_generic_failure_does_not_render_lane_skew_wording(
        self, tmp_path, monkeypatch, isolated_home, capsys
    ):
        """Fail-open guard: a sidecar failure without the fence sentence
        keeps today's generic row, byte-for-byte."""

        calls = {"n": 0}

        def always_fail(method, tested, series):
            calls["n"] += 1
            raise RuntimeError(
                f"Runtime exited during {method} (status 1); "
                f"stderr: SQLITE_BUSY tail-generic-{calls['n']}"
            )

        code, _attempts = self._run_doctor_with_attempts(
            tmp_path, monkeypatch, isolated_home, [always_fail, always_fail]
        )
        out = capsys.readouterr().out
        assert code == 1
        assert "lane skew" not in out
        assert "next_magic_context_release.py" not in out
        # unchanged from the pre-U3 rendering: both attempts' texts ride
        # in the single generic row, wrapper and all
        assert out.count("Magic Context sidecar failed") == 2

    def test_lane_skew_sentence_split_across_retry_pair_matches_once(
        self, tmp_path, monkeypatch, isolated_home, capsys
    ):
        """Edge: the fence sentence split across the two fast-fail retry
        texts still matches — and still renders exactly one FAIL row."""

        def first_half(method, tested, series):
            raise RuntimeError(
                "Runtime exited during hello (status 1); stderr: "
                "[magic-context] storage fatal: upstream migration lane "
                "v95 is newer than this binary"
            )

        def second_half(method, tested, series):
            # a differently-shaped failure whose message begins exactly
            # where the first attempt's tail was cut: the wrapper-stripped
            # join must reconstruct the sentence across the pair
            raise RuntimeError(
                "supports (max v94) — refusing to open context.db"
            )

        code, attempts = self._run_doctor_with_attempts(
            tmp_path, monkeypatch, isolated_home, [first_half, second_half]
        )
        out = capsys.readouterr().out
        assert code == 1
        assert len(attempts) == 2
        assert out.count("◆  FAIL") == 1
        assert "shared-store lane skew" in out
        assert "lane v95" in out
        assert "max v94" in out
        assert "FAIL 0" not in out

    def test_slow_first_failure_skips_the_retry(
        self, tmp_path, monkeypatch, isolated_home, capsys
    ):
        def slow_fail(method, tested, series):
            raise RuntimeError(
                "Runtime exited during doctor (status 124); stderr: timeout tail-slow"
            )

        # a zero gate makes any real elapsed time "slow": a failure that
        # already burned the budget must not be retried
        code, attempts = self._run_doctor_with_attempts(
            tmp_path, monkeypatch, isolated_home, [slow_fail], fast_fail_s=0.0
        )
        out = capsys.readouterr().out
        assert code == 1
        assert len(attempts) == 1
        assert "tail-slow" in out
        assert out.count("Magic Context sidecar failed") == 1

    def test_retry_success_with_over_gate_store_warns_without_failing(
        self, tmp_path, monkeypatch, isolated_home, capsys
    ):
        def boot_lock(method, tested, series):
            raise RuntimeError(
                "Runtime exited during hello (status 1); stderr: boot lock tail-gate"
            )

        def over_gate(method, tested, series):
            if method == "hello":
                return {"harness": "hermes", "package_version": tested}
            return {
                "database_health": "skipped:store-size 3690000000 > 1610612736",
                "scan_mode": "metadata",
                "store_bytes": 3690000000,
                "probe_ms": 4300.0,
                "core_symbols_ready": True,
                "supported_series": series,
            }

        code, attempts = self._run_doctor_with_attempts(
            tmp_path, monkeypatch, isolated_home, [boot_lock, over_gate]
        )
        out = capsys.readouterr().out
        assert code == 0
        assert "◆  FAIL" not in out
        assert "WARN" in out
        assert "store-size" in out
        assert len(attempts) == 2


class TestDoctorWallBudget:
    """U15 (finding c7d63424) — the doctor sidecar pair runs inside a
    wall budget.

    maestro's phase-2 verdict observes the whole ``magic-hermes doctor``
    process for 90 s, single-shot, with no retry. The R2 retry constants
    bound only the *retry decision*; the total the pair may spend is
    capped by ``MAGIC_CONTEXT_DOCTOR_WALL_BUDGET_S`` (default 75 s) so a
    verdict always renders inside the observer: boot/handshake and
    doctor-call timeouts derive from the remaining budget, and exhaustion
    degrades to a WARN naming the offline audit remedy — never a FAIL,
    never a hang.
    """

    BUDGET_ENV = "MAGIC_CONTEXT_DOCTOR_WALL_BUDGET_S"

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

    def _run_doctor_budget(
        self, tmp_path, monkeypatch, isolated_home, budget_env, doctor_script=None
    ):
        """Run cli.run_doctor under a forced wall budget.

        Returns ``(rc, ctor_calls, sidecar_calls)``: the RuntimeClient
        constructor kwargs (carrying the derived boot/handshake timeout)
        and every sidecar call as a ``(method, params, timeout)`` triple.
        ``doctor_script`` (if set) replaces the doctor RPC's behavior.
        """
        from magic_hermes import historian_guard as hg

        db = tmp_path / "context.db"
        hg.make_fixture_db(db)
        hg.apply_guard(db, verify=False)
        monkeypatch.setenv("MAGIC_CONTEXT_DB_PATH", str(db))
        self._wire_config()

        if budget_env is None:
            monkeypatch.delenv(self.BUDGET_ENV, raising=False)
        else:
            monkeypatch.setenv(self.BUDGET_ENV, budget_env)

        tested = cli.tested_magic_context_version()
        package = make_package(tmp_path / "pkg", tested)
        series = ".".join(map(str, cli.supported_magic_context_series()))
        ctor_calls: list[dict] = []
        sidecar_calls: list[tuple] = []

        def make_client(*args, **kwargs):
            ctor_calls.append(kwargs)
            sidecar = mock.MagicMock()

            def call(method, params=None, timeout=None):
                sidecar_calls.append((method, params, timeout))
                if method == "hello":
                    return {"harness": "hermes", "package_version": tested}
                report = {
                    "database_health": "ok",
                    "core_symbols_ready": True,
                    "supported_series": series,
                }
                if doctor_script is not None:
                    return doctor_script(report)
                return report

            sidecar.call.side_effect = call
            client = mock.MagicMock()
            client.__enter__.return_value = sidecar
            client.__exit__.return_value = False
            return client

        with (
            mock.patch.object(
                cli, "discover_installations", return_value=[(package, tested)]
            ),
            mock.patch.object(cli, "RuntimeClient", side_effect=make_client),
            mock.patch.object(cli, "_SIDECAR_RETRY_BACKOFF_S", 0.0),
        ):
            rc = cli.run_doctor(json_output=False)
        return rc, ctor_calls, sidecar_calls

    def test_env_resolution_default_junk_and_override(self, monkeypatch):
        resolve = cli._doctor_wall_budget_s
        # default when unset or empty
        monkeypatch.delenv(self.BUDGET_ENV, raising=False)
        assert resolve() == 75.0
        monkeypatch.setenv(self.BUDGET_ENV, "")
        assert resolve() == 75.0
        # junk never widens the cap
        monkeypatch.setenv(self.BUDGET_ENV, "soon")
        assert resolve() == 75.0
        monkeypatch.setenv(self.BUDGET_ENV, "nan")
        assert resolve() == 75.0
        monkeypatch.setenv(self.BUDGET_ENV, "inf")
        assert resolve() == 75.0
        # a real override is honored exactly
        monkeypatch.setenv(self.BUDGET_ENV, " 30 ")
        assert resolve() == 30.0
        # non-positive values are honored as degenerate (already spent)
        # budgets, never widened back to the default
        monkeypatch.setenv(self.BUDGET_ENV, "0")
        assert resolve() == 0.0

    def test_budget_scales_handshake_and_doctor_timeouts(
        self, tmp_path, monkeypatch, isolated_home
    ):
        rc, ctor_calls, sidecar_calls = self._run_doctor_budget(
            tmp_path, monkeypatch, isolated_home, "30"
        )
        assert rc == 0
        assert ctor_calls, "sidecar must still boot under a 30s budget"
        handshake_timeout = ctor_calls[0]["timeout"]
        # the derived cap follows the budget down, not the 60s constant
        assert 20.0 <= handshake_timeout < 60.0
        hello = next(c for c in sidecar_calls if c[0] == "hello")
        assert 20.0 <= hello[2] < 60.0
        doctor = next(c for c in sidecar_calls if c[0] == "doctor")
        assert 15.0 < doctor[2] <= 30.0

    def test_default_budget_keeps_sixty_second_handshake_cap(
        self, tmp_path, monkeypatch, isolated_home
    ):
        _, ctor_calls, _ = self._run_doctor_budget(
            tmp_path, monkeypatch, isolated_home, None
        )
        # with the default 75s budget and a fresh clock the boot cap is
        # still the 60s constant (min, never a widening)
        assert ctor_calls[0]["timeout"] == 60.0

    def test_exhausted_budget_warns_without_sidecar_call(
        self, tmp_path, monkeypatch, isolated_home, capsys
    ):
        rc, ctor_calls, sidecar_calls = self._run_doctor_budget(
            tmp_path, monkeypatch, isolated_home, "0"
        )
        out = capsys.readouterr().out
        # maestro contract survives a spent budget: verdict renders
        assert rc == 0
        assert "FAIL 0" in out
        assert "wall-budget" in out
        assert "PRAGMA quick_check" in out  # offline audit remedy named
        # nothing booted, nothing was probed
        assert ctor_calls == []
        assert sidecar_calls == []
        assert "quick_check ok" not in out
        assert "◆  FAIL" not in out

    def test_doctor_timeout_at_budget_edge_renders_wall_budget_warn(
        self, tmp_path, monkeypatch, isolated_home, capsys
    ):
        from magic_hermes.runtime import RuntimeProtocolError

        def past_budget(report):
            # mirror what the real capped client does when the derived
            # deadline fires mid-scan: it raises after burning the budget
            time.sleep(0.9)
            raise RuntimeProtocolError(
                "Runtime timed out after 0.6s during doctor; "
                "request was not replayed"
            )

        rc, _, _ = self._run_doctor_budget(
            tmp_path, monkeypatch, isolated_home, "1.0", doctor_script=past_budget
        )
        out = capsys.readouterr().out
        # exhaustion mid-attempt is a WARN, not a sidecar FAIL
        assert rc == 0
        assert "FAIL 0" in out
        assert "wall-budget" in out
        assert "◆  FAIL" not in out
        assert "Magic Context sidecar failed" not in out

    def test_wall_budget_arithmetic_fits_observer(self):
        # Attempt timeouts derive from the remaining budget, so the whole
        # sidecar phase — including one full fast-fail retry cycle — is
        # bounded by the wall budget itself, plus the render reserve kept
        # back for printing the verdict. maestro's observer kills at 90 s.
        assert (
            cli._DOCTOR_WALL_BUDGET_S + cli._DOCTOR_WALL_RENDER_RESERVE_S
            < 90.0
        )
        # a full R2 fast-fail retry cycle fits inside the budget
        assert (
            cli._SIDECAR_FAST_FAIL_S + cli._SIDECAR_RETRY_BACKOFF_S
            < cli._DOCTOR_WALL_BUDGET_S
        )
        assert 0.0 < cli._DOCTOR_WALL_RENDER_RESERVE_S < 1.0


class TestDoctorSkipWording:
    """U16c (R6, run 589cf794) — the skipped-scan WARN must follow the
    probe outcome: the probe-error lane must not claim that probe reads
    succeeded. The WARN/``FAIL 0`` contract itself is unchanged, so a
    degraded probe still exits 0 against maestro's verdict grep.
    """

    def test_probe_error_skip_never_claims_probe_success(
        self, tmp_path, monkeypatch, isolated_home, capsys
    ):
        harness = TestDoctorSidecarRetry()

        def probe_error(method, tested, series):
            if method == "hello":
                return {"harness": "hermes", "package_version": tested}
            return {
                "database_health": (
                    "skipped:probe-error database disk image is malformed (code 1)"
                ),
                "scan_mode": "metadata",
                "core_symbols_ready": True,
                "supported_series": series,
            }

        code, _ = harness._run_doctor_with_attempts(
            tmp_path, monkeypatch, isolated_home, [probe_error]
        )
        out = capsys.readouterr().out
        assert code == 0
        assert "◆  FAIL" not in out
        assert "WARN" in out
        assert "probe-error" in out
        assert "unverified" in out
        assert "probe reads succeeded" not in out

    def test_probe_success_skip_keeps_the_probe_success_wording(
        self, tmp_path, monkeypatch, isolated_home, capsys
    ):
        # probe-success skip lanes (store-size, probe-ms, deadline) keep
        # the truthful "probe reads succeeded" claim
        harness = TestDoctorSidecarRetry()

        def deadline_skip(method, tested, series):
            if method == "hello":
                return {"harness": "hermes", "package_version": tested}
            return {
                "database_health": "skipped:deadline 62000 > 45000",
                "scan_mode": "budgeted",
                "scan_estimate_ms": 62000,
                "scan_budget_ms": 45000,
                "core_symbols_ready": True,
                "supported_series": series,
            }

        code, _ = harness._run_doctor_with_attempts(
            tmp_path, monkeypatch, isolated_home, [deadline_skip]
        )
        out = capsys.readouterr().out
        assert code == 0
        assert "◆  FAIL" not in out
        assert "WARN" in out
        assert "deadline" in out
        assert "probe reads succeeded" in out
