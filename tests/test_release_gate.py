"""Regression tests for the release gate (finding c7d63424daa3, plan U1).

The gate answers one question for the release-on-unreleased-master lane:
does the checkout carry an unreleased FUNCTIONAL release? Docs-only master
must be a clean no-op (release false), any functional change since the
latest tag must release (release true, pending version honored), and the
predicate must stay total — tag == HEAD releases nothing even when the
next version is resolvable. The CLI prints a single-line JSON verdict;
internal failures exit nonzero with a tidy message, never a traceback.
"""

from __future__ import annotations

import json
import runpy
import subprocess
import sys
from pathlib import Path

import pytest

import scripts.release as release
import scripts.release_gate as gate


def _git(work: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(work), *args],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _write_repo(work: Path, version: str) -> None:
    (work / "src" / "magic_hermes").mkdir(parents=True, exist_ok=True)
    (work / "pyproject.toml").write_text(
        f'[project]\nname = "magic-hermes"\nversion = "{version}"\n',
        encoding="utf-8",
    )
    (work / "README.md").write_text("# magic-hermes\n", encoding="utf-8")


class TestFunctionalFence:
    """is_functional_path(): the functional-surface fence, table-driven."""

    @pytest.mark.parametrize(
        ("path", "expected"),
        [
            # functional prefixes
            ("src/magic_hermes/cli.py", True),
            ("src/magic_hermes/magic_context_compat.json", True),
            ("scripts/release.py", True),
            ("scripts/release_gate.py", True),
            ("tests/test_release.py", True),
            (".github/workflows/ci.yml", True),
            # functional standalone files
            ("pyproject.toml", True),
            ("plugin.yaml", True),
            ("package.json", True),
            ("package-lock.json", True),
            # non-functional surfaces
            ("README.md", False),
            ("docs/architecture.md", False),
            ("ROADMAP.md", False),
            ("ONBOARDING.md", False),
            (".github/CODEOWNERS", False),
            ("src_legacy/foo.py", False),
            ("tests", False),
            ("", False),
        ],
    )
    def test_fence_classification(self, path: str, expected: bool) -> None:
        assert gate.is_functional_path(path) is expected


class TestReleaseVerdict:
    """release_verdict(): pure decision, no git involved."""

    def test_functional_changes_since_tag_release(self) -> None:
        verdict = gate.release_verdict(
            "v0.3.34", ["src/magic_hermes/cli.py"], "0.3.35"
        )
        assert verdict["release"] is True
        assert verdict["version"] == "0.3.35"
        assert "v0.3.34" in verdict["reason"]

    def test_docs_only_master_is_a_no_op(self) -> None:
        verdict = gate.release_verdict(
            "v0.3.34", ["README.md", "docs/foo.md", "ROADMAP.md"], "0.3.35"
        )
        assert verdict["release"] is False
        assert verdict["version"] is None
        assert "docs-only" in verdict["reason"]

    def test_mixed_docs_and_functional_releases(self) -> None:
        verdict = gate.release_verdict(
            "v0.3.34", ["README.md", "src/magic_hermes/cli.py"], "0.3.35"
        )
        assert verdict["release"] is True
        assert verdict["version"] == "0.3.35"

    def test_tag_at_head_releases_nothing_even_with_resolvable_version(self) -> None:
        verdict = gate.release_verdict("v0.3.34", [], "0.3.35")
        assert verdict["release"] is False
        assert verdict["version"] is None
        assert "v0.3.34" in verdict["reason"]

    def test_no_tag_with_functional_files_releases_against_empty_tree(self) -> None:
        verdict = gate.release_verdict(
            None, ["pyproject.toml", "src/magic_hermes/x.py"], "0.1.0"
        )
        assert verdict["release"] is True
        assert "empty tree" in verdict["reason"]

    def test_no_tag_docs_only_is_a_no_op(self) -> None:
        verdict = gate.release_verdict(None, ["README.md"], "0.1.0")
        assert verdict["release"] is False
        assert verdict["version"] is None
        assert "docs-only" in verdict["reason"]


class TestChangedPathsSince:
    """changed_paths_since(): diff-base selection and output shaping."""

    def test_diff_runs_from_tag_to_head(self, monkeypatch) -> None:
        seen: dict[tuple[str, ...], str] = {}

        def fake_git(*args: str) -> str:
            seen[args] = ""
            return "src/a.py\nREADME.md\n"

        monkeypatch.setattr(release, "git_output", fake_git)

        assert gate.changed_paths_since("v0.3.34") == ["src/a.py", "README.md"]
        assert ("diff", "--name-only", "v0.3.34", "HEAD") in seen

    def test_missing_tag_diffs_against_the_empty_tree(self, monkeypatch) -> None:
        seen: dict[tuple[str, ...], str] = {}

        def fake_git(*args: str) -> str:
            seen[args] = ""
            return "src/a.py\n"

        monkeypatch.setattr(release, "git_output", fake_git)

        assert gate.changed_paths_since(None) == ["src/a.py"]
        assert ("diff", "--name-only", gate.EMPTY_TREE, "HEAD") in seen


class TestGateOnRealRepo:
    """check_repository() against real git fixtures (no fakes)."""

    def test_pending_release_state_releases_the_pending_version(
        self, monkeypatch, tmp_path
    ) -> None:
        """Mirrors live master at the c7d63424 investigation: tag v0.3.34,
        then a `release: v0.3.35` bump commit plus a docs+src follow-up —
        the gate must say release 0.3.35, not bump past it."""
        work = tmp_path / "work"
        _write_repo(work, "0.3.34")
        _git(work, "init", "-q", "-b", "master")
        _git(work, "config", "user.name", "test")
        _git(work, "config", "user.email", "test@example.com")
        _git(work, "add", "-A")
        _git(work, "commit", "-q", "-m", "chore: baseline 0.3.34")
        _git(work, "tag", "-a", "v0.3.34", "-m", "Magic-Hermes v0.3.34")
        (work / "pyproject.toml").write_text(
            '[project]\nname = "magic-hermes"\nversion = "0.3.35"\n',
            encoding="utf-8",
        )
        _git(work, "add", "-A")
        _git(work, "commit", "-q", "-m", "release: v0.3.35")
        (work / "README.md").write_text(
            "# magic-hermes (updated)\n", encoding="utf-8"
        )
        (work / "src" / "magic_hermes" / "fix.py").write_text("", encoding="utf-8")
        _git(work, "add", "-A")
        _git(work, "commit", "-q", "-m", "merge: integrate run")
        monkeypatch.setattr(release, "ROOT", work)

        verdict = gate.check_repository()

        assert verdict["release"] is True
        assert verdict["version"] == "0.3.35"
        assert "v0.3.34" in verdict["reason"]

    def test_docs_only_master_since_tag_is_a_no_op(self, monkeypatch, tmp_path) -> None:
        work = tmp_path / "work"
        _write_repo(work, "0.3.34")
        _git(work, "init", "-q", "-b", "master")
        _git(work, "config", "user.name", "test")
        _git(work, "config", "user.email", "test@example.com")
        _git(work, "add", "-A")
        _git(work, "commit", "-q", "-m", "chore: baseline 0.3.34")
        _git(work, "tag", "-a", "v0.3.34", "-m", "Magic-Hermes v0.3.34")
        (work / "docs").mkdir()
        (work / "docs" / "notes.md").write_text("docs only\n", encoding="utf-8")
        (work / "ROADMAP.md").write_text("roadmap\n", encoding="utf-8")
        _git(work, "add", "-A")
        _git(work, "commit", "-q", "-m", "docs: notes")
        monkeypatch.setattr(release, "ROOT", work)

        verdict = gate.check_repository()

        assert verdict["release"] is False
        assert verdict["version"] is None
        assert "docs-only" in verdict["reason"]

    def test_tag_at_head_releases_nothing(self, monkeypatch, tmp_path) -> None:
        work = tmp_path / "work"
        _write_repo(work, "0.3.34")
        _git(work, "init", "-q", "-b", "master")
        _git(work, "config", "user.name", "test")
        _git(work, "config", "user.email", "test@example.com")
        _git(work, "add", "-A")
        _git(work, "commit", "-q", "-m", "chore: baseline 0.3.34")
        _git(work, "tag", "-a", "v0.3.34", "-m", "Magic-Hermes v0.3.34")
        monkeypatch.setattr(release, "ROOT", work)

        verdict = gate.check_repository()

        assert verdict["release"] is False
        assert verdict["version"] is None

    def test_malformed_tags_are_ignored_not_fatal(self, monkeypatch, tmp_path) -> None:
        """Foreign/non-semver tags must not crash the gate: latest_tag()
        ignores them (established lane semantics), the verdict falls back
        to a diff against the empty tree and stays tidy."""
        work = tmp_path / "work"
        _write_repo(work, "0.1.0")
        _git(work, "init", "-q", "-b", "master")
        _git(work, "config", "user.name", "test")
        _git(work, "config", "user.email", "test@example.com")
        _git(work, "add", "-A")
        _git(work, "commit", "-q", "-m", "chore: baseline")
        _git(work, "tag", "-a", "dashboard-v1.0", "-m", "foreign")
        _git(work, "tag", "-a", "v0.3.x", "-m", "malformed")
        monkeypatch.setattr(release, "ROOT", work)

        verdict = gate.check_repository()

        assert verdict["release"] is True  # untagged functional files ship
        assert verdict["version"] == "0.1.1"


class TestCli:
    """main() / __main__: JSON verdict contract and tidy failures."""

    @staticmethod
    def _patch_lane(monkeypatch, changed: str) -> None:
        monkeypatch.setattr(release, "latest_tag", lambda: "v0.3.34")
        monkeypatch.setattr(release, "git_output", lambda *args: changed)

        def fake_next() -> str:
            # Mirrors the real pending-version notice on stdout — the CLI
            # must suppress it so its stdout is the JSON verdict alone.
            print(
                "Version 0.3.35 is set in project metadata but newer than "
                "tag v0.3.34; releasing the pending version instead."
            )
            return "0.3.35"

        monkeypatch.setattr(release, "next_release_version", fake_next)

    def test_check_prints_single_line_json_verdict(self, monkeypatch, capsys) -> None:
        self._patch_lane(monkeypatch, "src/magic_hermes/cli.py\n")
        monkeypatch.setattr(sys, "argv", ["release_gate.py", "--check"])

        assert gate.main() == 0

        out = capsys.readouterr().out
        lines = [line for line in out.splitlines() if line.strip()]
        assert len(lines) == 1
        verdict = json.loads(lines[0])
        assert set(verdict) == {"release", "version", "reason"}
        assert verdict["release"] is True
        assert verdict["version"] == "0.3.35"

    def test_check_exits_zero_on_docs_only_no_op(self, monkeypatch, capsys) -> None:
        self._patch_lane(monkeypatch, "README.md\n")
        monkeypatch.setattr(sys, "argv", ["release_gate.py", "--check"])

        assert gate.main() == 0

        verdict = json.loads(capsys.readouterr().out.strip())
        assert verdict["release"] is False
        assert verdict["version"] is None

    def test_check_flag_is_required(self, monkeypatch, capsys) -> None:
        monkeypatch.setattr(sys, "argv", ["release_gate.py"])

        with pytest.raises(SystemExit) as excinfo:
            gate.main()

        assert excinfo.value.code == 2  # argparse usage error

    def test_git_failure_exits_one_with_tidy_stderr(
        self, monkeypatch, tmp_path, capsys
    ) -> None:
        """A dead git / non-repo ROOT must exit 1 with a one-line stderr
        message — never a traceback (plan U1 error path)."""
        monkeypatch.setattr(release, "ROOT", tmp_path)  # plain dir, not a repo
        monkeypatch.setattr(sys, "argv", ["release_gate.py", "--check"])

        with pytest.raises(SystemExit) as excinfo:
            gate_path = (
                Path(__file__).resolve().parents[1] / "scripts" / "release_gate.py"
            )
            runpy.run_path(
                str(gate_path),
                run_name="__main__",
            )

        assert excinfo.value.code == 1
        err = capsys.readouterr().err
        assert "release gate failed" in err
        assert "Traceback" not in err
