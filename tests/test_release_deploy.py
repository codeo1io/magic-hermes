"""Regression tests for the release-lane deploy step (findings d59758598379
and c7d63424daa3).

The deploy step closes the land→deploy delivery gap: after ``commit_and_tag``
and before ``publish_release`` the freshly built wheel is installed into the
interpreter maestro probes (``DEFAULT_DEPLOY_VENV``, overridable through
``MAGIC_HERMES_DEPLOY_VENV``) and verified in THAT interpreter — never the
development one. It then closes the deploy→operate gap: the deployed console
script converges the host wiring (``magic-hermes wire``) and must pass a
doctor smoke — DEFAULT-ON, with ``MAGIC_HERMES_DEPLOY_NO_SMOKE=1`` as the
escape for foreign/throwaway targets — before the release may publish. The
legacy ``MAGIC_HERMES_DEPLOY_SMOKE`` opt-in is a tolerated no-op.

These tests never touch the real probe venv: every unit test fakes the
subprocess layer (``release.run``) and ``deployed_version`` while building a
throwaway target-venv layout under ``tmp_path``. The integration test at the
bottom is gated behind ``MAGIC_HERMES_DEPLOY_INTEGRATION=1`` and deploys a
real built wheel into a throwaway venv (established repo precedent:
``TestDeploySmoke`` in tests/test_doctor_contract.py).
"""

from __future__ import annotations

import json
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


def make_layered_target(
    tmp_path: Path, deployed: str, stale: list[str]
) -> tuple[Path, Path]:
    """Fake deploy-target venv carrying layered magic_hermes dist-infos.

    Mirrors the live probe venv at the c7d63424 investigation: stale
    ``magic_hermes-*.dist-info`` directories stacked under the deployed
    version's, plus one foreign package's metadata that must survive.
    """
    target = make_target(tmp_path)
    site = target / "lib" / "python3.11" / "site-packages"
    for version in (*stale, deployed):
        dist = site / f"magic_hermes-{version}.dist-info"
        dist.mkdir(parents=True, exist_ok=True)
        (dist / "METADATA").write_text("", encoding="utf-8")
    foreign = site / "other_pkg-1.0.dist-info"
    foreign.mkdir(parents=True, exist_ok=True)
    (foreign / "METADATA").write_text("", encoding="utf-8")
    return target, site


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

    def wire_calls(self) -> list[tuple[str, ...]]:
        return [call for call in self.calls if "wire" in call]

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
def _clean_smoke_env(monkeypatch):
    """Pin the default-on smoke contract unless a test opts out explicitly.

    Both the legacy opt-in (``MAGIC_HERMES_DEPLOY_SMOKE``) and the escape
    hatch (``MAGIC_HERMES_DEPLOY_NO_SMOKE``) are cleared, so ``wire`` +
    ``doctor`` run in every deploy unless a test sets the escape itself.
    """
    monkeypatch.delenv("MAGIC_HERMES_DEPLOY_SMOKE", raising=False)
    monkeypatch.delenv("MAGIC_HERMES_DEPLOY_NO_SMOKE", raising=False)


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
        # Default-on contract: after the install, converge wiring, then
        # verify with the doctor — in that order, through the deployed
        # console script.
        console = str(target / "bin" / "magic-hermes")
        assert fake.calls.index(expected) < fake.calls.index((console, "wire"))
        assert fake.calls.index((console, "wire")) < fake.calls.index(
            (console, "doctor")
        )

    def test_already_deployed_version_skips_pip(self, tmp_path, monkeypatch):
        target = make_target(tmp_path)
        artifacts = make_artifacts(tmp_path)
        fake = FakeRun()
        version = FakeVersion([VERSION])
        monkeypatch.setattr(release, "run", fake)
        monkeypatch.setattr(release, "deployed_version", version)

        release.deploy_release(artifacts, VERSION, target)

        assert fake.install_calls() == []
        assert version.calls  # verification consulted the target interpreter
        # Already-current wheel: the install is skipped, but convergence and
        # verification still run (idempotent deploy contract).
        console = str(target / "bin" / "magic-hermes")
        assert fake.wire_calls() == [(console, "wire")]
        assert fake.doctor_calls() == [(console, "doctor")]

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

    def test_doctor_smoke_runs_by_default_and_requires_success(
        self, tmp_path, monkeypatch
    ):
        """Default-on verification (c7d63424): no env is set at all, yet the
deployed console script's doctor must run and exit clean — the same probe
maestro executes — or the release aborts."""
        target = make_target(tmp_path)
        artifacts = make_artifacts(tmp_path)

        def doctor_fails(key):
            return "doctor" in key

        fake = FakeRun(fail=doctor_fails)
        monkeypatch.setattr(release, "run", fake)
        monkeypatch.setattr(release, "deployed_version", FakeVersion([None, VERSION]))

        with pytest.raises(release.ReleaseError, match="doctor"):
            release.deploy_release(artifacts, VERSION, target)

        console = str(target / "bin" / "magic-hermes")
        assert fake.wire_calls() == [(console, "wire")]
        assert fake.doctor_calls() == [(console, "doctor")]

    def test_no_smoke_escape_skips_doctor_but_not_wire(self, tmp_path, monkeypatch):
        """MAGIC_HERMES_DEPLOY_NO_SMOKE=1 is the documented escape for
        foreign/throwaway deploy targets — it skips verification only;
        convergence (``wire``) still runs."""
        target = make_target(tmp_path)
        artifacts = make_artifacts(tmp_path)
        monkeypatch.setenv("MAGIC_HERMES_DEPLOY_NO_SMOKE", "1")
        fake = FakeRun()
        monkeypatch.setattr(release, "run", fake)
        monkeypatch.setattr(release, "deployed_version", FakeVersion([None, VERSION]))

        release.deploy_release(artifacts, VERSION, target)

        console = str(target / "bin" / "magic-hermes")
        assert fake.wire_calls() == [(console, "wire")]
        assert fake.doctor_calls() == []

    def test_legacy_smoke_env_is_a_tolerated_no_op(self, tmp_path, monkeypatch):
        """MAGIC_HERMES_DEPLOY_SMOKE (the pre-c7d63424 opt-in) no longer
        gates anything; setting it must behave exactly like the default."""
        target = make_target(tmp_path)
        artifacts = make_artifacts(tmp_path)
        monkeypatch.setenv("MAGIC_HERMES_DEPLOY_SMOKE", "1")
        fake = FakeRun()
        monkeypatch.setattr(release, "run", fake)
        monkeypatch.setattr(release, "deployed_version", FakeVersion([None, VERSION]))

        release.deploy_release(artifacts, VERSION, target)

        console = str(target / "bin" / "magic-hermes")
        assert fake.wire_calls() == [(console, "wire")]
        assert fake.doctor_calls() == [(console, "doctor")]

    def test_wire_failure_aborts_before_doctor(self, tmp_path, monkeypatch):
        """The tonight-class regression (c7d63424): a deployed wheel plus
        inert wiring must abort the release loudly — verification never
        runs and publish is never reached."""
        target = make_target(tmp_path)
        artifacts = make_artifacts(tmp_path)

        def wire_fails(key):
            return "wire" in key

        fake = FakeRun(fail=wire_fails)
        monkeypatch.setattr(release, "run", fake)
        monkeypatch.setattr(release, "deployed_version", FakeVersion([None, VERSION]))

        with pytest.raises(release.ReleaseError, match="wire"):
            release.deploy_release(artifacts, VERSION, target)

        assert fake.wire_calls()  # convergence was attempted ...
        assert fake.doctor_calls() == []  # ... and verification never ran


class TestPruneStaleDistInfos:
    """prune_stale_dist_infos(): the layered-metadata repair (c7d63424).

    The probe venv arrived at the 2026-11-01 investigation carrying
    0.3.24/0.3.32/0.3.34 dist-infos stacked from historical manual deploys;
    ambiguous metadata is an operational hazard the deploy lane must clean
    at install time without ever touching foreign packages.
    """

    def test_layered_stale_dist_infos_are_pruned_to_the_deployed_version(
        self, tmp_path
    ):
        target, site = make_layered_target(
            tmp_path, "0.3.35", ["0.3.24", "0.3.32"]
        )

        removed = release.prune_stale_dist_infos(target, "0.3.35")

        assert removed == [
            "magic_hermes-0.3.24.dist-info",
            "magic_hermes-0.3.32.dist-info",
        ]
        assert sorted(p.name for p in site.glob("magic_hermes-*.dist-info")) == [
            "magic_hermes-0.3.35.dist-info"
        ]
        # foreign metadata is never touched
        assert (site / "other_pkg-1.0.dist-info").is_dir()

    def test_prunes_across_every_python_slot(self, tmp_path):
        target = make_target(tmp_path)
        for slot in ("python3.11", "python3.12"):
            site = target / "lib" / slot / "site-packages"
            for dist in (
                "magic_hermes-0.3.24.dist-info",
                "magic_hermes-0.3.35.dist-info",
            ):
                (site / dist).mkdir(parents=True)

        removed = release.prune_stale_dist_infos(target, "0.3.35")

        assert removed == [
            "magic_hermes-0.3.24.dist-info",
            "magic_hermes-0.3.24.dist-info",
        ]
        for slot in ("python3.11", "python3.12"):
            remaining = sorted(
                p.name
                for p in (target / "lib" / slot / "site-packages").glob(
                    "magic_hermes-*.dist-info"
                )
            )
            assert remaining == ["magic_hermes-0.3.35.dist-info"]

    def test_missing_site_packages_or_metadata_is_a_silent_no_op(
        self, tmp_path
    ):
        target = make_target(tmp_path)  # no lib/ at all
        assert release.prune_stale_dist_infos(target, "0.3.35") == []

        site = target / "lib" / "python3.11" / "site-packages"
        (site / "other_pkg-1.0.dist-info").mkdir(parents=True)
        assert release.prune_stale_dist_infos(target, "0.3.35") == []
        assert (site / "other_pkg-1.0.dist-info").is_dir()


class TestDeployReleasePrunesStaleLayers:
    """deploy_release(): the prune rides the deploy on every path.

    Ordering contract: pruning happens after the version re-check and
    before wiring/verification — both on the fresh-install path and on
    the already-deployed skip path — so a failed removal aborts the
    release before any wire act, and a successful deploy always ends
    with exactly one magic_hermes dist-info in the target.
    """

    def test_fresh_install_prunes_stale_layers(self, tmp_path, monkeypatch):
        target, site = make_layered_target(
            tmp_path, "0.3.35", ["0.3.24", "0.3.32"]
        )
        artifacts = make_artifacts(tmp_path, version="0.3.35")
        fake = FakeRun()
        monkeypatch.setattr(release, "run", fake)
        monkeypatch.setattr(
            release, "deployed_version", FakeVersion([None, "0.3.35"])
        )

        release.deploy_release(artifacts, "0.3.35", target)

        assert fake.install_calls()  # fresh-install path exercised
        assert sorted(p.name for p in site.glob("magic_hermes-*.dist-info")) == [
            "magic_hermes-0.3.35.dist-info"
        ]
        assert (site / "other_pkg-1.0.dist-info").is_dir()
        # the deploy still finishes its convergence + verification tail
        console = str(target / "bin" / "magic-hermes")
        assert fake.wire_calls() == [(console, "wire")]
        assert fake.doctor_calls() == [(console, "doctor")]

    def test_already_deployed_skip_path_still_prunes(self, tmp_path, monkeypatch):
        target, site = make_layered_target(
            tmp_path, "0.3.35", ["0.3.24", "0.3.32"]
        )
        artifacts = make_artifacts(tmp_path, version="0.3.35")
        fake = FakeRun()
        monkeypatch.setattr(release, "run", fake)
        monkeypatch.setattr(release, "deployed_version", FakeVersion(["0.3.35"]))

        release.deploy_release(artifacts, "0.3.35", target)

        assert fake.install_calls() == []  # skip-pip path
        assert sorted(p.name for p in site.glob("magic_hermes-*.dist-info")) == [
            "magic_hermes-0.3.35.dist-info"
        ]
        console = str(target / "bin" / "magic-hermes")
        assert fake.wire_calls() == [(console, "wire")]

    def test_prune_failure_aborts_deploy_before_wire(self, tmp_path, monkeypatch):
        target = make_target(tmp_path)
        site = target / "lib" / "python3.11" / "site-packages"
        (site / "magic_hermes-0.3.35.dist-info").mkdir(parents=True)
        # a FILE where the stale dist-info directory belongs: rmtree fails
        (site / "magic_hermes-0.3.24.dist-info").write_text(
            "not a directory", encoding="utf-8"
        )
        artifacts = make_artifacts(tmp_path, version="0.3.35")
        fake = FakeRun()
        monkeypatch.setattr(release, "run", fake)
        monkeypatch.setattr(release, "deployed_version", FakeVersion(["0.3.35"]))

        with pytest.raises(release.ReleaseError, match="prune"):
            release.deploy_release(artifacts, "0.3.35", target)

        assert fake.wire_calls() == []
        assert fake.doctor_calls() == []


class TestMainDeployWiring:
    """main(): ordering, redeploy path, and target selection."""

    @staticmethod
    def _patch_lane(
        monkeypatch,
        order: list[str],
        artifacts: list[Path],
        target: Path,
        *,
        patch_deploy: bool = True,
    ):
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
            "publish_release",
            lambda version, tag, artifacts_, checksum: order.append("publish"),
        )
        if patch_deploy:
            monkeypatch.setattr(
                release,
                "deploy_release",
                lambda artifacts_, version_, target_: order.append(
                    ("deploy", tuple(str(a) for a in artifacts_), version_, target_)
                ),
            )
        else:
            # Leave deploy_release real: the test fakes the subprocess layer
            # (release.run / deployed_version) to exercise its body.
            monkeypatch.setattr(
                release, "deployed_version", FakeVersion([VERSION])
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

    def test_doctor_failure_aborts_the_release_before_publish(
        self, tmp_path, monkeypatch
    ):
        """A doctor FAIL at smoke time (any non-wiring row — wire has
        already removed the wiring one) must keep the GitHub release object
        unpublished: the lane refuses to ship an unverified probe."""
        order: list[str] = []
        target = make_target(tmp_path)
        artifacts = make_artifacts(tmp_path)
        self._patch_lane(monkeypatch, order, artifacts, target, patch_deploy=False)

        def doctor_fails(key):
            return "doctor" in key

        monkeypatch.setattr(release, "run", FakeRun(fail=doctor_fails))
        monkeypatch.setenv("MAGIC_HERMES_DEPLOY_VENV", str(target))
        monkeypatch.setattr(sys, "argv", ["release.py", VERSION, "--deploy"])

        with pytest.raises(release.ReleaseError, match="doctor"):
            release.main()

        names = [entry if isinstance(entry, str) else entry[0] for entry in order]
        assert "commit_and_tag" in names
        assert "publish" not in names

    def test_redeploy_route_runs_the_convergence_and_verification_tail(
        self, tmp_path, monkeypatch
    ):
        """redeploy_release routes through deploy_release, so the re-entry
        path inherits the wire + doctor tail — it cannot reintroduce the
        deploy-without-converging hole."""
        order: list[str] = []
        target = make_target(tmp_path)
        artifacts = make_artifacts(tmp_path)
        self._patch_lane(monkeypatch, order, artifacts, target, patch_deploy=False)
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
        assert "commit_and_tag" not in names
        assert "publish" not in names
        console = str(target / "bin" / "magic-hermes")
        assert fake_git.wire_calls() == [(console, "wire")]
        assert fake_git.doctor_calls() == [(console, "doctor")]

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


class TestEnsureDefaultBranchFFRecovery:
    """Behind-only drift no longer hard-fails the release preflight.

    Root cause 2026-10-22 nightly (run 39722457971): the roadmap-sync /
    conductor writers push to master between the workflow checkout and
    release.py's preflight, so the strict synchronized check aborted the
    auto-release lane. Behind-only is always recoverable at that point —
    the release transaction's own dirt is the only dirt allowed through
    ensure_clean_or_release_version — so fold it over the new tip with
    stash → fast-forward → pop. Ahead or diverged states still fail loud.
    """

    DIV = ("git", "rev-list", "--left-right", "--count", "HEAD...origin/master")

    def _patch(self, monkeypatch, *, divergence: str, status: str = "") -> FakeRun:
        fake = FakeRun(
            answers={
                ("git", "branch", "--show-current"): "master",
                (
                    "gh",
                    "repo",
                    "view",
                    "--json",
                    "defaultBranchRef",
                    "--jq",
                    ".defaultBranchRef.name",
                ): "master",
                self.DIV: divergence,
                ("git", "status", "--porcelain"): status,
            }
        )
        monkeypatch.setattr(release, "run", fake)
        return fake

    def test_behind_clean_tree_fast_forwards(self, monkeypatch):
        fake = self._patch(monkeypatch, divergence="0\t2")

        assert release.ensure_default_branch() == "master"

        assert ("git", "merge", "--ff-only", "origin/master") in fake.calls
        assert not any(call[:2] == ("git", "stash") for call in fake.calls)

    def test_behind_dirty_release_tree_stashes_and_pops(self, monkeypatch):
        fake = self._patch(
            monkeypatch,
            divergence="0\t2",
            status="M  pyproject.toml\nM  src/magic_hermes/__init__.py\n",
        )

        assert release.ensure_default_branch() == "master"

        calls = fake.calls
        stash_push = calls.index(
            ("git", "stash", "push", "-m", "release-preflight ff-recovery")
        )
        ff = calls.index(("git", "merge", "--ff-only", "origin/master"))
        stash_pop = calls.index(("git", "stash", "pop"))
        assert stash_push < ff < stash_pop

    def test_ahead_fails_loudly(self, monkeypatch):
        fake = self._patch(monkeypatch, divergence="1\t0")

        with pytest.raises(release.ReleaseError, match="synchronized"):
            release.ensure_default_branch()

        assert not any("merge" in call for call in fake.calls)

    def test_diverged_fails_loudly(self, monkeypatch):
        self._patch(monkeypatch, divergence="1\t2")

        with pytest.raises(release.ReleaseError, match="synchronized"):
            release.ensure_default_branch()

    def test_ff_failure_restores_stash_and_raises(self, monkeypatch):
        fake = self._patch(
            monkeypatch,
            divergence="0\t2",
            status="M  pyproject.toml\n",
        )
        original_call = fake.__call__

        def fail_ff_then_pass(*args, capture: bool = False, env=None):
            if args[:3] == ("git", "merge", "--ff-only"):
                raise release.ReleaseError("command failed: git merge --ff-only")
            return original_call(*args, capture=capture, env=env)

        monkeypatch.setattr(release, "run", fail_ff_then_pass)

        with pytest.raises(release.ReleaseError, match="ff-only"):
            release.ensure_default_branch()

        assert ("git", "stash", "pop") in fake.calls


@pytest.mark.skipif(
    not os.environ.get("MAGIC_HERMES_DEPLOY_INTEGRATION"),
    reason=(
        "set MAGIC_HERMES_DEPLOY_INTEGRATION=1 (with a built dist/ wheel) to "
        "exercise the real pip install into a throwaway venv"
    ),
)
def test_deploy_release_into_throwaway_venv(tmp_path, monkeypatch):
    # Throwaway target: take the documented escape so the default-on doctor
    # step is skipped — this battery asserts the convergence artifacts on
    # disk (wire writes them), not the smoke (unit tests pin that contract).
    monkeypatch.setenv("MAGIC_HERMES_DEPLOY_NO_SMOKE", "1")
    monkeypatch.delenv("MAGIC_HERMES_DEPLOY_SMOKE", raising=False)
    # Isolated Hermes home + data root: the wire step must write config and
    # lease HERE, never in the operator's real home (R6 swim-lane rule).
    hermes_home = tmp_path / "hermes-home"
    data_home = tmp_path / "data-home"
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.setenv("XDG_DATA_HOME", str(data_home))
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

    # Convergence through the deployed code (c7d63424): wiring keys in the
    # isolated config, lease recorded under the isolated data root.
    config_text = (hermes_home / "config.yaml").read_text(encoding="utf-8")
    assert re.search(r"engine:\s*magic-context", config_text)
    assert re.search(r"provider:\s*magic_context", config_text)
    leases = list(data_home.rglob("wiring-lease.json"))
    assert leases, "wiring lease was not recorded under the isolated data root"
    lease = json.loads(leases[0].read_text(encoding="utf-8"))
    assert lease["plugin"] == "magic-hermes"
    assert set(lease["wires"]) >= {"context.engine", "memory.provider"}
