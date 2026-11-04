"""Regression tests for the release-lane deploy step (findings d59758598379
and c7d63424daa3).

The deploy step closes the land→deploy delivery gap: after ``commit_and_tag``
and before ``publish_release`` the freshly built wheel is installed into the
interpreter maestro probes (``DEFAULT_DEPLOY_VENV``, overridable through
``MAGIC_HERMES_DEPLOY_VENV``) and verified in THAT interpreter — never the
development one. It then closes the deploy→operate gap — twice over: the
deployed console script converges the host wiring (``magic-hermes wire``),
the lane installs the out-of-band convergence timer (systemd user units
running ``magic-hermes converge``, default-on with
``MAGIC_HERMES_DEPLOY_NO_WATCHER=1`` as the escape for targets without a
systemd user session), and the target must pass a doctor smoke —
DEFAULT-ON, with ``MAGIC_HERMES_DEPLOY_NO_SMOKE=1`` as the escape for
foreign/throwaway targets — before the release may publish. A
version-changing install also UNINSTALLS the old package first (stacked
dist-info hygiene — c7d63424 root cause 3). The legacy
``MAGIC_HERMES_DEPLOY_SMOKE`` opt-in is a tolerated no-op.

These tests never touch the real probe venv: every unit test fakes the
subprocess layer (``release.run``) and ``deployed_version`` while building a
throwaway target-venv layout under ``tmp_path``. The watcher step rides the
same ``release.run`` fake (systemctl never executes) and its unit-file
writes are pinned to ``tmp_path`` through the autouse fixture below, so
nothing here reaches the operator's ``~/.config/systemd/user``. The
integration test at the bottom is gated behind
``MAGIC_HERMES_DEPLOY_INTEGRATION=1`` and deploys a
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
from magic_hermes import watcher_units
from magic_hermes.watcher_units import (
    CONVERGE_SERVICE_UNIT,
    CONVERGE_TIMER_UNIT,
)

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

    def wire_calls(self) -> list[tuple[str, ...]]:
        return [call for call in self.calls if "wire" in call]

    def doctor_calls(self) -> list[tuple[str, ...]]:
        return [call for call in self.calls if "doctor" in call]

    def systemctl_calls(self) -> list[tuple[str, ...]]:
        return [call for call in self.calls if call[:1] == ("systemctl",)]


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


@pytest.fixture(autouse=True)
def _watcher_step_isolated(monkeypatch, tmp_path):
    """Keep the watcher step hermetic: env pinned, unit writes in tmp.

    deploy_release arms the systemd user timer between wire and doctor
    (default-on; ``MAGIC_HERMES_DEPLOY_NO_WATCHER=1`` is the escape). The
    runner is ``release.run`` — faked by every unit test — so systemctl
    never executes; this fixture clears the escape by default and pins
    the unit-file destination to ``tmp_path`` so even a default-unit_dir
    install stays far from the operator's real user unit dir (resolved
    by ``watcher_units.default_unit_dir()`` — the XDG-aware successor of
    the old ``USER_UNIT_DIR`` constant).
    """
    monkeypatch.delenv("MAGIC_HERMES_DEPLOY_NO_WATCHER", raising=False)
    monkeypatch.setattr(
        watcher_units,
        "default_unit_dir",
        lambda: tmp_path / "systemd-user",
    )


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
        assert not any("uninstall" in call for call in fake.calls)
        assert version.calls  # verification consulted the target interpreter
        # Already-current wheel: the pip steps are skipped entirely, but
        # convergence, watcher arming, and verification still run
        # (idempotent deploy contract).
        console = str(target / "bin" / "magic-hermes")
        assert fake.wire_calls() == [(console, "wire")]
        assert fake.doctor_calls() == [(console, "doctor")]
        assert (
            "systemctl",
            "--user",
            "enable",
            "--now",
            CONVERGE_TIMER_UNIT,
        ) in fake.calls

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

    def test_version_mismatch_uninstalls_first(self, tmp_path, monkeypatch):
        """Stacked dist-info hygiene (c7d63424 root cause 3): a
        version-changing deploy runs ``pip uninstall -y magic-hermes``
        BEFORE the --force-reinstall install, then wires, arms the
        watcher, and verifies — in that order."""
        target = make_target(tmp_path)
        artifacts = make_artifacts(tmp_path)
        fake = FakeRun(
            answers={
                ("systemctl", "--user", "is-enabled", CONVERGE_TIMER_UNIT): "enabled"
            }
        )
        monkeypatch.setattr(release, "run", fake)
        monkeypatch.setattr(
            release, "deployed_version", FakeVersion([None, VERSION])
        )

        release.deploy_release(artifacts, VERSION, target)

        console = str(target / "bin" / "magic-hermes")
        probe = ("systemctl", "--user", "is-enabled", CONVERGE_TIMER_UNIT)
        disable = (
            "systemctl",
            "--user",
            "disable",
            "--now",
            CONVERGE_TIMER_UNIT,
        )
        uninstall = (
            str(target / "bin" / "python"),
            "-m",
            "pip",
            "uninstall",
            "-y",
            "magic-hermes",
        )
        install = (
            str(target / "bin" / "python"),
            "-m",
            "pip",
            "install",
            "--force-reinstall",
            "--no-deps",
            str(tmp_path / "dist" / WHEEL_NAME),
        )
        reload_cmd = ("systemctl", "--user", "daemon-reload")
        enable_cmd = (
            "systemctl",
            "--user",
            "enable",
            "--now",
            CONVERGE_TIMER_UNIT,
        )
        start_cmd = ("systemctl", "--user", "start", CONVERGE_SERVICE_UNIT)
        ordered = [
            fake.calls.index(probe),
            fake.calls.index(disable),
            fake.calls.index(uninstall),
            fake.calls.index(install),
            fake.calls.index((console, "wire")),
            fake.calls.index(reload_cmd),
            fake.calls.index(enable_cmd),
            fake.calls.index(start_cmd),
            fake.calls.index((console, "doctor")),
        ]
        assert ordered == sorted(ordered)

    def test_failed_uninstall_is_tolerated(self, tmp_path, monkeypatch):
        """Uninstall failure never aborts the lane: a target without the
        package (or a broken uninstall) falls through — the post-install
        version assert is the real gate."""
        target = make_target(tmp_path)
        artifacts = make_artifacts(tmp_path)
        fake = FakeRun(fail=lambda key: "uninstall" in key)
        monkeypatch.setattr(release, "run", fake)
        monkeypatch.setattr(
            release, "deployed_version", FakeVersion([None, VERSION])
        )

        release.deploy_release(artifacts, VERSION, target)

        assert any("uninstall" in call for call in fake.calls)  # attempted
        assert fake.install_calls()  # install still happened
        console = str(target / "bin" / "magic-hermes")
        assert fake.doctor_calls() == [(console, "doctor")]  # lane completed

    def test_watcher_units_written_between_wire_and_doctor(
        self, tmp_path, monkeypatch
    ):
        """The deploy lane leaves the target self-healing: the rendered
        units land on disk carrying the deployed console script's absolute
        path, and systemctl enable --now rides the faked run seam."""
        target = make_target(tmp_path)
        artifacts = make_artifacts(tmp_path)
        fake = FakeRun()
        monkeypatch.setattr(release, "run", fake)
        monkeypatch.setattr(
            release, "deployed_version", FakeVersion([None, VERSION])
        )
        unit_dir = tmp_path / "units"

        release.deploy_release(artifacts, VERSION, target, unit_dir=unit_dir)

        service = unit_dir / "magic-hermes-converge.service"
        timer = unit_dir / CONVERGE_TIMER_UNIT
        assert service.is_file()
        assert timer.is_file()
        console = target / "bin" / "magic-hermes"
        assert (
            f'ExecStart="{console.resolve()}" converge'
            in service.read_text(encoding="utf-8")
        )
        enable_cmd = (
            "systemctl",
            "--user",
            "enable",
            "--now",
            CONVERGE_TIMER_UNIT,
        )
        # Review finding 1: the post-arm start of the service is the
        # verification that the ExecStart actually runs — it fires after
        # the timer is enabled, before the doctor smoke.
        start_cmd = ("systemctl", "--user", "start", CONVERGE_SERVICE_UNIT)
        assert enable_cmd in fake.calls
        assert start_cmd in fake.calls
        console_s = str(console)
        assert (
            fake.calls.index((console_s, "wire")) < fake.calls.index(enable_cmd)
        )
        assert (
            fake.calls.index(enable_cmd) < fake.calls.index(start_cmd)
        )
        assert (
            fake.calls.index(start_cmd) < fake.calls.index((console_s, "doctor"))
        )

    def test_no_watcher_escape_skips_arming_but_quiesces(
        self, tmp_path, monkeypatch
    ):
        """MAGIC_HERMES_DEPLOY_NO_WATCHER=1 is the documented escape for
        deploy targets without a systemd user session — it skips ONLY
        the watcher ARMING. The pre-uninstall QUIESCE still runs when a
        timer is actually enabled (7e69362a finding 9: the uninstall
        deletes the console script under an armed timer either way —
        the README contract "skips only the watcher installation" is
        what the code implements); wire and the doctor smoke still run.
        """
        target = make_target(tmp_path)
        artifacts = make_artifacts(tmp_path)
        monkeypatch.setenv("MAGIC_HERMES_DEPLOY_NO_WATCHER", "1")
        fake = FakeRun(
            answers={
                ("systemctl", "--user", "is-enabled", CONVERGE_TIMER_UNIT): "enabled"
            }
        )
        monkeypatch.setattr(release, "run", fake)
        monkeypatch.setattr(
            release, "deployed_version", FakeVersion([None, VERSION])
        )

        release.deploy_release(artifacts, VERSION, target)

        systemctl = fake.systemctl_calls()
        assert (
            "systemctl",
            "--user",
            "is-enabled",
            CONVERGE_TIMER_UNIT,
        ) in systemctl
        assert (
            "systemctl",
            "--user",
            "disable",
            "--now",
            CONVERGE_TIMER_UNIT,
        ) in systemctl
        # the ARMING is what the escape skips — no enable, no service start
        assert not any("enable" in call for call in systemctl)
        assert not any(
            "start" in call and CONVERGE_SERVICE_UNIT in call
            for call in systemctl
        )
        console = str(target / "bin" / "magic-hermes")
        assert fake.wire_calls() == [(console, "wire")]
        assert fake.doctor_calls() == [(console, "doctor")]

    def test_operator_paused_timer_is_never_quiesced_or_re_enabled(
        self, tmp_path, monkeypatch
    ):
        """The quiesce is probe-gated: a timer the OPERATOR deliberately
        paused (the documented pause semantic) stays untouched — no
        disable, and nothing to restore on abort."""
        target = make_target(tmp_path)
        artifacts = make_artifacts(tmp_path)
        fake = FakeRun(
            answers={
                ("systemctl", "--user", "is-enabled", CONVERGE_TIMER_UNIT): "disabled"
            },
            fail=lambda key: "doctor" in key,
        )
        monkeypatch.setattr(release, "run", fake)
        monkeypatch.setattr(
            release, "deployed_version", FakeVersion([None, VERSION])
        )

        with pytest.raises(
            release.ReleaseError, match="deploy verification"
        ) as excinfo:
            release.deploy_release(artifacts, VERSION, target)

        systemctl = fake.systemctl_calls()
        assert not any("disable" in call for call in systemctl)
        # the watcher ARMING still ran (only the quiesce was probe-gated)
        assert any("enable" in call for call in systemctl)
        # nothing was quiesced, so the abort owes no stranded-timer note
        assert "DISABLED" not in str(excinfo.value)

    def test_aborted_deploy_after_quiesce_re_arms_the_timer(
        self, tmp_path, monkeypatch
    ):
        """7e69362a finding 3: an abort AFTER a successful quiesce must
        not strand the healer silently disabled. Here the deploy arms
        the new watcher (units point at the freshly deployed console)
        and then the doctor smoke fails — the recovery re-enables the
        timer the deploy itself disabled."""
        target = make_target(tmp_path)
        artifacts = make_artifacts(tmp_path)
        fake = FakeRun(
            answers={
                ("systemctl", "--user", "is-enabled", CONVERGE_TIMER_UNIT): "enabled"
            },
            fail=lambda key: "doctor" in key,
        )
        monkeypatch.setattr(release, "run", fake)
        monkeypatch.setattr(
            release, "deployed_version", FakeVersion([None, VERSION])
        )
        unit_dir = tmp_path / "units"

        with pytest.raises(release.ReleaseError) as excinfo:
            release.deploy_release(artifacts, VERSION, target, unit_dir=unit_dir)

        assert "deploy verification failed" in str(excinfo.value)
        assert "re-armed" in str(excinfo.value)
        enables = [
            call
            for call in fake.systemctl_calls()
            if "enable" in call
        ]
        # one from the arming step, one from the abort recovery
        assert len(enables) == 2

    def test_aborted_deploy_with_dead_execstart_notes_instead_of_rearming(
        self, tmp_path, monkeypatch
    ):
        """7e69362a finding 3, dead half: when the abort happens BEFORE
        the watcher step (version assert here) and the on-disk unit
        still points at the just-uninstalled venv, re-enabling would
        arm a 203/EXEC loop — the abort message must instead SAY the
        healer is left disabled and why."""
        target = make_target(tmp_path)
        artifacts = make_artifacts(tmp_path)
        fake = FakeRun(
            answers={
                ("systemctl", "--user", "is-enabled", CONVERGE_TIMER_UNIT): "enabled"
            }
        )
        monkeypatch.setattr(release, "run", fake)
        # post-install version assert fails: None -> install -> "wrong"
        monkeypatch.setattr(
            release, "deployed_version", FakeVersion([None, "0.0.0-wrong"])
        )
        unit_dir = tmp_path / "units"
        unit_dir.mkdir()
        dead_console = tmp_path / "old-venv" / "bin" / "magic-hermes"
        (unit_dir / CONVERGE_SERVICE_UNIT).write_text(
            f'[Service]\nExecStart="{dead_console}" converge\n',
            encoding="utf-8",
        )

        with pytest.raises(release.ReleaseError) as excinfo:
            release.deploy_release(artifacts, VERSION, target, unit_dir=unit_dir)

        message = str(excinfo.value)
        assert "expected" in message  # the version assert is the abort cause
        assert "left DISABLED" in message
        assert "no longer exists" in message
        # the loop-arm is NOT issued
        assert not any(
            "enable" in call for call in fake.systemctl_calls()
        )

    def test_missing_systemctl_binary_is_tolerated(
        self, tmp_path, monkeypatch, capsys
    ):
        """7e69362a finding 7: a target without a systemctl BINARY raises
        FileNotFoundError (an OSError), not ReleaseError — the quiesce
        probe must tolerate both, or the deploy crashes instead of the
        documented tolerated fall-through. The arming step still fails
        loudly without the NO_WATCHER escape (KTD 4), so this target
        takes the escape — exactly the documented shape for hosts
        without systemd."""
        target = make_target(tmp_path)
        artifacts = make_artifacts(tmp_path)
        monkeypatch.setenv("MAGIC_HERMES_DEPLOY_NO_WATCHER", "1")

        class SystemctlMissing(FakeRun):
            def __call__(self, *args, capture: bool = False, env=None) -> str:
                if args[:1] == ("systemctl",):
                    raise FileNotFoundError(2, "No such file or directory: systemctl")
                return super().__call__(*args, capture=capture, env=env)

        fake = SystemctlMissing()
        monkeypatch.setattr(release, "run", fake)
        monkeypatch.setattr(
            release, "deployed_version", FakeVersion([None, VERSION])
        )

        release.deploy_release(artifacts, VERSION, target)

        console = str(target / "bin" / "magic-hermes")
        assert fake.wire_calls() == [(console, "wire")]  # lane completed
        assert fake.doctor_calls() == [(console, "doctor")]
        _ = capsys

    def test_watcher_failure_aborts_before_doctor(self, tmp_path, monkeypatch):
        """Loud-half contract (KTD 4): a watcher step that cannot install
        or enable aborts the release before verification — the install
        verb warns where deploy fails loudly."""
        target = make_target(tmp_path)
        artifacts = make_artifacts(tmp_path)
        fake = FakeRun(fail=lambda key: key[:1] == ("systemctl",))
        monkeypatch.setattr(release, "run", fake)
        monkeypatch.setattr(
            release, "deployed_version", FakeVersion([None, VERSION])
        )

        with pytest.raises(
            release.ReleaseError, match="watcher installation failed"
        ):
            release.deploy_release(artifacts, VERSION, target)

        assert fake.doctor_calls() == []

    def test_quiesce_failure_before_uninstall_is_tolerated(
        self, tmp_path, monkeypatch, capsys
    ):
        """Review finding 4: the pre-uninstall timer quiesce is
        best-effort — a target whose systemctl fails (or has no systemd
        user session) still deploys; the post-install version assert is
        the real gate."""
        target = make_target(tmp_path)
        artifacts = make_artifacts(tmp_path)

        def quiesce_fails(key):
            return "disable" in key

        fake = FakeRun(
            answers={
                ("systemctl", "--user", "is-enabled", CONVERGE_TIMER_UNIT): "enabled"
            },
            fail=quiesce_fails,
        )
        monkeypatch.setattr(release, "run", fake)
        monkeypatch.setattr(
            release, "deployed_version", FakeVersion([None, VERSION])
        )

        release.deploy_release(artifacts, VERSION, target)

        assert any("disable" in call for call in fake.calls)  # attempted
        console = str(target / "bin" / "magic-hermes")
        assert fake.doctor_calls() == [(console, "doctor")]  # lane done
        assert "quiesce" in capsys.readouterr().err

    def test_foreign_interpreter_cannot_import_watcher_module_loudly(
        self, tmp_path, monkeypatch
    ):
        """Review finding 14: the watcher step needs magic_hermes
        importable by the DRIVING interpreter — a foreign python gets a
        precise, attributable error (not a misattributed "watcher
        installation failed")."""
        target = make_target(tmp_path)
        artifacts = make_artifacts(tmp_path)
        fake = FakeRun()
        monkeypatch.setattr(release, "run", fake)
        monkeypatch.setattr(
            release, "deployed_version", FakeVersion([None, VERSION])
        )
        monkeypatch.setitem(
            sys.modules, "magic_hermes.watcher_units", None
        )

        with pytest.raises(
            release.ReleaseError, match="driving interpreter"
        ):
            release.deploy_release(artifacts, VERSION, target)

        assert fake.doctor_calls() == []

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
        assert fake.systemctl_calls()  # watcher arming is NOT escaped here

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
        # watcher never ARMED either: the only systemctl command is the
        # tolerated pre-uninstall quiesce PROBE (which found no enabled
        # timer here, so not even a disable was issued) — no
        # daemon-reload, no enable, no post-arm start.
        assert fake.systemctl_calls() == [
            ("systemctl", "--user", "is-enabled", CONVERGE_TIMER_UNIT)
        ]


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
        assert (
            "systemctl",
            "--user",
            "enable",
            "--now",
            CONVERGE_TIMER_UNIT,
        ) in fake_git.calls  # watcher tail inherited through deploy_release

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
    # Throwaway target: take the documented escapes so the default-on
    # doctor step is skipped AND the watcher step does not arm real
    # systemd user units on the host — host operational state (config,
    # lease, units) is the deploy phase's sanctioned act, never a test's.
    monkeypatch.setenv("MAGIC_HERMES_DEPLOY_NO_SMOKE", "1")
    monkeypatch.setenv("MAGIC_HERMES_DEPLOY_NO_WATCHER", "1")
    monkeypatch.delenv("MAGIC_HERMES_DEPLOY_SMOKE", raising=False)
    # 7e69362a findings 3/9: the quiesce probe now runs even under
    # NO_WATCHER — isolate the systemd unit search path so the probe
    # resolves nothing on the host and the test can never disable a
    # REAL armed timer.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
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
