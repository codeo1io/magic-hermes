"""Behavior tests for the `magic-hermes` installer/doctor CLI.

These tests never spawn the real Node sidecar: RuntimeClient is patched,
and the filesystem fixtures model the shapes the 2026-09-20 schema-fence
incident taught us to guard (multiple homes with different versions,
foreign homes that must never be rewritten, comment-preserving config
edits, text-scan fallback when no YAML library is present).
"""

from __future__ import annotations

import json
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
            "magic_hermes.cli.magic_context_package_candidates",
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
