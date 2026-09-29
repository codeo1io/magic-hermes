"""Regression tests for the release-lane deploy step (finding d59758598379).

The deploy step closes the land→deploy delivery gap: after ``commit_and_tag``
and before ``publish_release`` the freshly built wheel is installed into the
interpreter maestro probes (``DEFAULT_DEPLOY_VENV``, overridable through
``MAGIC_HERMES_DEPLOY_VENV``) and verified in THAT interpreter — never the
development one.

These tests never touch the real probe venv: every unit test fakes the
subprocess layer (``release.run``) and ``deployed_version`` while building a
throwaway target-venv layout under ``tmp_path``. The integration test at the
bottom is gated behind ``MAGIC_HERMES_DEPLOY_INTEGRATION=1`` and deploys a
real built wheel into a throwaway venv (established repo precedent:
``TestDeploySmoke`` in tests/test_doctor_contract.py).
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

import scripts.release as release

WHEEL_NAME = "magic_hermes-0.3.4-py3-none-any.whl"
VERSION = "0.3.4"


def make_target(tmp_path: Path, *, python: bool = True, console_script: bool = True):
    """Build a fake deploy-target venv layout; return the venv path."""
    venv = tmp_path / "fake-venv"
    bin_dir = venv / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    if python:
        (bin_dir / "python").write_text("", encoding="utf-8")
    if console_script:
        (bin_dir / "magic-hermes").write_text("", encoding="utf-8")
    return venv


def make_artifacts(tmp_path: Path, version: str = VERSION, *, with_wheel: bool = True):
    """Build a fake dist/ artifact list (real empty files, exact names)."""
    dist = tmp_path / "dist"
    dist.mkdir(exist_ok=True)
    artifacts: list[Path] = []
    if with_wheel:
        wheel = dist / f"magic_hermes-{version}-py3-none-any.whl"
        wheel.write_bytes(b"")
        artifacts.append(wheel)
    sdist = dist / f"magic_hermes-{version}.tar.gz"
    sdist.write_bytes(b"")
    artifacts.append(sdist)
    return artifacts


class FakeRun:
    """Records run() calls; answers canned queries, fails chosen prefixes."""

    def __init__(self, answers: dict[tuple[str, ...], str] | None = None, fail=None):
        self.calls: list[tuple[str, ...]] = []
        self.answers = {tuple(key): value for key, value in (answers or {}).items()}
        self.fail = fail

    def __call__(self, *args, capture: bool = False, env=None) -> str:
        key = tuple(str(arg) for arg in args)
        self.calls.append(key)
        if self.fail is not None and self.fail(key):
            raise release.ReleaseError(f"command failed: {' '.join(key)}")
        return self.answers.get(key, "")

    def install_calls(self) -> list[tuple[str, ...]]:
        return [call for call in self.calls if "install" in call]

    def doctor_calls(self) -> list[tuple[str, ...]]:
        return [call for call in self.calls if "doctor" in call]


class FakeVersion:
    """Stateful deployed_version() fake: yields the sequence, repeats last."""

    def __init__(self, sequence: list[str | None]):
        self.sequence = list(sequence)
        self.calls: list[object] = []

    def __call__(self, target):
        self.calls.append(target)
        if not self.sequence:
            raise AssertionError("deployed_version fake ran out of answers")
        if len(self.sequence) == 1:
            return self.sequence[0]
        return self.sequence.pop(0)


@pytest.fixture(autouse=True)
def _no_smoke_gate(monkeypatch):
    """Unit tests pin the smoke gate OFF unless a test sets it explicitly."""
    monkeypatch.delenv("MAGIC_HERMES_DEPLOY_SMOKE", raising=False)


class TestDeployRelease:
    """deploy_release(): install contract, verification contract, smoke gate."""

    def test_not_installed_target_gets_forced_no_deps_install(
        self, tmp_path, monkeypatch
    ):
        target = make_target(tmp_path)
        artifacts = make_artifacts(tmp_path)
        fake = FakeRun()
        monkeypatch.setattr(release, "run", fake)
        monkeypatch.setattr(release, "deployed_version", FakeVersion([None, VERSION]))

        release.deploy_release(artifacts, VERSION, target)

        wheel = tmp_path / "dist" / WHEEL_NAME
        expected = (
            str(target / "bin" / "python"),
            "-m",
            "pip",
            "install",
            "--force-reinstall",
            "--no-deps",
            str(wheel),
        )
        assert fake.install_calls() == [expected]
        assert fake.doctor_calls() == []

    def test_already_deployed_version_skips_pip(self, tmp_path, monkeypatch):
        target = make_target(tmp_path)
        artifacts = make_artifacts(tmp_path)
        fake = FakeRun()
        version = FakeVersion([VERSION])
        monkeypatch.setattr(release, "run", fake)
        monkeypatch.setattr(release, "deployed_version", version)

        release.deploy_release(artifacts, VERSION, target)

        assert fake.calls == []
        assert version.calls  # verification consulted the target interpreter

    def test_missing_target_python_is_a_loud_error(self, tmp_path, monkeypatch):
        target = make_target(tmp_path, python=False)
        artifacts = make_artifacts(tmp_path)
        fake = FakeRun()
        monkeypatch.setattr(release, "run", fake)
        monkeypatch.setattr(release, "deployed_version", FakeVersion([None]))

        with pytest.raises(release.ReleaseError) as excinfo:
            release.deploy_release(artifacts, VERSION, target)

        assert str(target / "bin" / "python") in str(excinfo.value)
        assert fake.calls == []

    def test_broken_pip_fails_the_precheck_without_installing(
        self, tmp_path, monkeypatch
    ):
        target = make_target(tmp_path)
        artifacts = make_artifacts(tmp_path)

        def pip_probe_fails(key):
            return "-m" in key and "pip" in key and "install" not in key

        fake = FakeRun(fail=pip_probe_fails)
        monkeypatch.setattr(release, "run", fake)
        monkeypatch.setattr(release, "deployed_version", FakeVersion([None]))

        with pytest.raises(release.ReleaseError, match="pip"):
            release.deploy_release(artifacts, VERSION, target)

        assert fake.install_calls() == []

    def test_version_mismatch_after_install_is_a_loud_error(
        self, tmp_path, monkeypatch
    ):
        target = make_target(tmp_path)
        artifacts = make_artifacts(tmp_path)
        fake = FakeRun()
        monkeypatch.setattr(release, "run", fake)
        monkeypatch.setattr(release, "deployed_version", FakeVersion([None, "0.3.3"]))

        with pytest.raises(release.ReleaseError, match=VERSION):
            release.deploy_release(artifacts, VERSION, target)

    def test_missing_wheel_for_release_version_is_a_loud_error(
        self, tmp_path, monkeypatch
    ):
        target = make_target(tmp_path)
        artifacts = make_artifacts(tmp_path, version="0.3.3")
        fake = FakeRun()
        monkeypatch.setattr(release, "run", fake)
        monkeypatch.setattr(release, "deployed_version", FakeVersion([None]))

        with pytest.raises(release.ReleaseError) as excinfo:
            release.deploy_release(artifacts, VERSION, target)

        assert WHEEL_NAME in str(excinfo.value)
        assert fake.calls == []

    def test_missing_console_script_after_install_is_a_loud_error(
        self, tmp_path, monkeypatch
    ):
        target = make_target(tmp_path, console_script=False)
        artifacts = make_artifacts(tmp_path)
        fake = FakeRun()
        monkeypatch.setattr(release, "run", fake)
        monkeypatch.setattr(release, "deployed_version", FakeVersion([None, VERSION]))

        with pytest.raises(release.ReleaseError, match="magic-hermes"):
            release.deploy_release(artifacts, VERSION, target)

    def test_smoke_gate_runs_target_doctor_and_requires_success(
        self, tmp_path, monkeypatch
    ):
        target = make_target(tmp_path)
        artifacts = make_artifacts(tmp_path)
        monkeypatch.setenv("MAGIC_HERMES_DEPLOY_SMOKE", "1")

        def doctor_fails(key):
            return "doctor" in key

        fake = FakeRun(fail=doctor_fails)
        monkeypatch.setattr(release, "run", fake)
        monkeypatch.setattr(release, "deployed_version", FakeVersion([None, VERSION]))

        with pytest.raises(release.ReleaseError, match="doctor"):
            release.deploy_release(artifacts, VERSION, target)

        assert fake.doctor_calls() == [
            (str(target / "bin" / "magic-hermes"), "doctor")
        ]

    def test_smoke_gate_unset_never_invokes_doctor(self, tmp_path, monkeypatch):
        target = make_target(tmp_path)
        artifacts = make_artifacts(tmp_path)
        fake = FakeRun()
        monkeypatch.setattr(release, "run", fake)
        monkeypatch.setattr(release, "deployed_version", FakeVersion([None, VERSION]))

        release.deploy_release(artifacts, VERSION, target)

        assert fake.doctor_calls() == []


class TestMainDeployWiring:
    """main(): ordering, redeploy path, and target selection."""

    @staticmethod
    def _patch_lane(monkeypatch, order: list[str], artifacts: list[Path], target: Path):
        wheel = artifacts[0]
        monkeypatch.setattr(release, "ensure_tools", lambda: None)
        monkeypatch.setattr(release, "ensure_clean_or_release_version", lambda v: None)
        monkeypatch.setattr(release, "ensure_default_branch", lambda: "master")
        monkeypatch.setattr(release, "release_exists", lambda tag: False)
        monkeypatch.setattr(release, "current_version", lambda: VERSION)
        monkeypatch.setattr(release, "assert_versions", lambda v: None)
        monkeypatch.setattr(
            release, "validate_and_build", lambda: order.append("build") or artifacts
        )
        monkeypatch.setattr(
            release,
            "write_checksums",
            lambda a: order.append("checksums") or wheel,
        )
        monkeypatch.setattr(
            release,
            "commit_and_tag",
            lambda version, tag, branch: order.append("commit_and_tag"),
        )
        monkeypatch.setattr(
            release,
            "deploy_release",
            lambda artifacts_, version_, target_: order.append(
                ("deploy", tuple(str(a) for a in artifacts_), version_, target_)
            ),
        )
        monkeypatch.setattr(
            release,
            "publish_release",
            lambda version, tag, artifacts_, checksum: order.append("publish"),
        )
        return target

    def test_deploy_runs_between_commit_and_publish(self, tmp_path, monkeypatch):
        order: list[str] = []
        target = make_target(tmp_path)
        artifacts = make_artifacts(tmp_path)
        self._patch_lane(monkeypatch, order, artifacts, target)
        monkeypatch.setenv("MAGIC_HERMES_DEPLOY_VENV", str(target))
        monkeypatch.setattr(sys, "argv", ["release.py", VERSION, "--deploy"])

        assert release.main() == 0

        names = [entry if isinstance(entry, str) else entry[0] for entry in order]
        assert names.index("commit_and_tag") < names.index("deploy")
        assert names.index("deploy") < names.index("publish")

    def test_deploy_target_defaults_to_constant_when_env_unset(
        self, tmp_path, monkeypatch
    ):
        order: list[str] = []
        target = make_target(tmp_path)
        artifacts = make_artifacts(tmp_path)
        self._patch_lane(monkeypatch, order, artifacts, target)
        monkeypatch.delenv("MAGIC_HERMES_DEPLOY_VENV", raising=False)
        monkeypatch.setattr(release, "DEFAULT_DEPLOY_VENV", target)
        monkeypatch.setattr(sys, "argv", ["release.py", VERSION, "--deploy"])

        assert release.main() == 0

        deploy_entries = [entry for entry in order if entry[0] == "deploy"]
        assert len(deploy_entries) == 1
        assert Path(deploy_entries[0][3]) == target

    def test_existing_release_without_deploy_still_early_raises(
        self, tmp_path, monkeypatch
    ):
        order: list[str] = []
        target = make_target(tmp_path)
        artifacts = make_artifacts(tmp_path)
        self._patch_lane(monkeypatch, order, artifacts, target)
        monkeypatch.setattr(release, "release_exists", lambda tag: True)
        monkeypatch.setattr(sys, "argv", ["release.py", VERSION])

        with pytest.raises(release.ReleaseError, match="already exists"):
            release.main()

        assert order == []

    def test_existing_release_with_deploy_redeploys_when_tag_points_at_head(
        self, tmp_path, monkeypatch
    ):
        order: list[str] = []
        target = make_target(tmp_path)
        artifacts = make_artifacts(tmp_path)
        self._patch_lane(monkeypatch, order, artifacts, target)
        monkeypatch.setattr(release, "release_exists", lambda tag: True)
        monkeypatch.setattr(release, "tag_exists", lambda tag: True)
        head = "aaaa1111aaaa1111aaaa1111aaaa1111aaaa1111"
        fake_git = FakeRun(
            answers={
                ("git", "rev-parse", "HEAD"): head,
                ("git", "rev-list", "-n", "1", f"v{VERSION}"): head,
            }
        )
        monkeypatch.setattr(release, "run", fake_git)
        monkeypatch.setenv("MAGIC_HERMES_DEPLOY_VENV", str(target))
        monkeypatch.setattr(sys, "argv", ["release.py", VERSION, "--deploy"])

        assert release.main() == 0

        names = [entry if isinstance(entry, str) else entry[0] for entry in order]
        assert "build" in names
        assert names.count("deploy") == 1
        deploy_entries = [entry for entry in order if entry[0] == "deploy"]
        assert deploy_entries[0][2] == VERSION
        assert Path(deploy_entries[0][3]) == target
        assert "commit_and_tag" not in names
        assert "publish" not in names

    def test_existing_release_with_deploy_refuses_foreign_tag(
        self, tmp_path, monkeypatch
    ):
        order: list[str] = []
        target = make_target(tmp_path)
        artifacts = make_artifacts(tmp_path)
        self._patch_lane(monkeypatch, order, artifacts, target)
        monkeypatch.setattr(release, "release_exists", lambda tag: True)
        monkeypatch.setattr(release, "tag_exists", lambda tag: True)
        fake_git = FakeRun(
            answers={
                ("git", "rev-parse", "HEAD"): "aaaa1111aaaa1111aaaa1111aaaa1111",
                ("git", "rev-list", "-n", "1", f"v{VERSION}"): "bbbb2222",
            }
        )
        monkeypatch.setattr(release, "run", fake_git)
        monkeypatch.setenv("MAGIC_HERMES_DEPLOY_VENV", str(target))
        monkeypatch.setattr(sys, "argv", ["release.py", VERSION, "--deploy"])

        with pytest.raises(release.ReleaseError, match="HEAD"):
            release.main()

        names = [entry if isinstance(entry, str) else entry[0] for entry in order]
        assert "deploy" not in names
        assert "commit_and_tag" not in names
        assert "publish" not in names


@pytest.mark.skipif(
    not os.environ.get("MAGIC_HERMES_DEPLOY_INTEGRATION"),
    reason=(
        "set MAGIC_HERMES_DEPLOY_INTEGRATION=1 (with a built dist/ wheel) to "
        "exercise the real pip install into a throwaway venv"
    ),
)
def test_deploy_release_into_throwaway_venv(tmp_path, monkeypatch):
    monkeypatch.delenv("MAGIC_HERMES_DEPLOY_SMOKE", raising=False)
    wheels = sorted((release.ROOT / "dist").glob("magic_hermes-*.whl"))
    if not wheels:
        pytest.skip(
            "no magic_hermes-*.whl in dist/ — build first (release.py --build-only)"
        )
    wheel = wheels[-1]
    match = re.fullmatch(r"magic_hermes-(\d+\.\d+\.\d+)-py3-none-any\.whl", wheel.name)
    assert match is not None, f"unrecognized wheel name: {wheel.name}"
    version = match.group(1)

    venv = tmp_path / "deploy-target-venv"
    subprocess.run(
        [sys.executable, "-m", "venv", str(venv)],
        check=True,
        capture_output=True,
    )

    release.deploy_release([wheel], version, venv)

    probe = subprocess.run(
        [
            str(venv / "bin" / "python"),
            "-c",
            "import importlib.metadata as m; print(m.version('magic-hermes'))",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert probe.stdout.strip() == version
    assert (venv / "bin" / "magic-hermes").is_file()
