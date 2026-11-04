"""Regression tests for the systemd user watcher units (c7d63424).

``magic_hermes.watcher_units`` renders and installs the out-of-band
convergence actor: a systemd user timer running ``magic-hermes converge``
every 60 s, lease-gated (KTD 3 — the units never run ``wire``). These
tests pin the rendered unit contracts (absolute ``ExecStart`` running
``converge``, the 60 s cadence, ``timers.target``), the install contract
(0o644 unit files, daemon-reload + enable --now for the timer,
idempotent re-install), the loud failure contract (runner failure raises
so the deploy lane can abort and the install verb can warn), and the
module's stdlib-only import surface (deployment constraint — the module
must import on hosts where only the stdlib is guaranteed). Nothing here
touches systemctl: the runner is always injected, and the unit files land
under ``tmp_path``.
"""

from __future__ import annotations

import ast
import stat
import sys
from pathlib import Path

import pytest

from magic_hermes import watcher_units
from magic_hermes.watcher_units import (
    CONVERGE_SERVICE_UNIT,
    CONVERGE_TIMER_UNIT,
    RECHECK_INTERVAL_S,
    install_watcher_units,
    render_service,
    render_timer,
)


class FakeRunner:
    """Records argv; optionally fails chosen commands (loud contract)."""

    def __init__(self, fail=None):
        self.calls: list[tuple[str, ...]] = []
        self.fail = fail

    def __call__(self, *args, capture: bool = False, env=None):
        key = tuple(str(arg) for arg in args)
        self.calls.append(key)
        if self.fail is not None and self.fail(key):
            raise RuntimeError(f"command failed: {' '.join(key)}")
        return ""


def make_console(tmp_path: Path) -> Path:
    """Write a fake absolute console script under ``tmp_path``."""
    console = tmp_path / "bin" / "magic-hermes"
    console.parent.mkdir(parents=True, exist_ok=True)
    console.write_text("", encoding="utf-8")
    return console


class TestRenderService:
    def test_execstart_is_absolute_console_path_running_converge(
        self, tmp_path
    ):
        console = make_console(tmp_path)

        text = render_service(console)

        assert f'ExecStart="{console.resolve()}" converge' in text
        assert "Type=oneshot" in text

    def test_relative_console_path_is_resolved_absolute(
        self, tmp_path, monkeypatch
    ):
        """systemd rejects relative ``ExecStart`` paths — a caller that
        hands over a cwd-relative path still gets an absolute unit."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "magic-hermes").write_text("", encoding="utf-8")

        text = render_service(Path("magic-hermes"))

        assert 'ExecStart="/' in text
        assert f'ExecStart="{tmp_path / "magic-hermes"}"' in text

    def test_whitespace_and_percent_paths_are_quoted_and_escaped(
        self, tmp_path
    ):
        """Review finding 2: a venv path with whitespace splits into
        argv words and a ``%`` is rewritten as a systemd specifier —
        both leave a loadable-but-dead unit. The path must be quoted
        and ``%`` doubled (systemd.unit(5))."""
        console = (
            tmp_path / "venv with space" / "bin%20magic"
        )
        console.parent.mkdir(parents=True)
        console.write_text("", encoding="utf-8")

        text = render_service(console)

        escaped = str(console.resolve()).replace("%", "%%")
        assert f'ExecStart="{escaped}" converge' in text
        # the raw single-% path must NOT appear unescaped
        assert str(console.resolve()) not in text

    def test_service_converges_but_never_wires(self, tmp_path):
        """KTD 3 regression pin: the watcher is the healing actor, never
        the arming one — ``wire`` (which records the lease) is the
        operator act and must never appear in the service unit."""
        text = render_service(tmp_path / "magic-hermes")

        assert "converge" in text
        assert "wire" not in text


class TestRenderTimer:
    def test_timer_pins_cadence_boot_delay_and_target(self):
        text = render_timer()

        assert f"OnUnitActiveSec={RECHECK_INTERVAL_S}s" in text
        assert (
            f"OnBootSec={watcher_units.FIRST_CHECK_DELAY_S}s" in text
        )
        assert "AccuracySec=1s" in text
        assert "WantedBy=timers.target" in text

    def test_timer_drives_the_converge_service_by_name(self):
        text = render_timer()

        assert f"Unit={CONVERGE_SERVICE_UNIT}" in text

    def test_cadence_is_the_pinned_sixty_seconds(self):
        # The 60 s cadence is a cross-surface contract: cli.py's install
        # tail, the README, and the maestro closure window (4-8 min probe
        # cadence) all assume it. Drift is a plan defect, not a tweak.
        assert RECHECK_INTERVAL_S == 60

    def test_unit_names_match_the_documented_contract(self):
        assert CONVERGE_SERVICE_UNIT == "magic-hermes-converge.service"
        assert CONVERGE_TIMER_UNIT == "magic-hermes-converge.timer"


class TestInstallWatcherUnits:
    def test_install_writes_units_and_enables_timer(self, tmp_path):
        console = make_console(tmp_path)
        runner = FakeRunner()
        unit_dir = tmp_path / "units"
        result = install_watcher_units(
            console, unit_dir=unit_dir, runner=runner
        )

        assert result.service_path == unit_dir / CONVERGE_SERVICE_UNIT
        assert result.timer_path == unit_dir / CONVERGE_TIMER_UNIT
        assert result.service_path.is_file()
        assert result.timer_path.is_file()
        assert stat.S_IMODE(result.service_path.stat().st_mode) == 0o644
        assert stat.S_IMODE(result.timer_path.stat().st_mode) == 0o644
        assert (
            f'ExecStart="{console.resolve()}" converge'
            in result.service_path.read_text(encoding="utf-8")
        )
        # Review finding 1: enable --now starts the TIMER only — the
        # explicit service START is the post-arm verification that a
        # dead ExecStart (stale venv, missing verb) fails loudly here
        # instead of silently every 60 s.
        assert runner.calls == [
            ("systemctl", "--user", "daemon-reload"),
            ("systemctl", "--user", "enable", "--now", CONVERGE_TIMER_UNIT),
            ("systemctl", "--user", "start", CONVERGE_SERVICE_UNIT),
        ]
        assert result.commands == tuple(runner.calls)
        assert result.console == console.resolve()

    def test_install_is_idempotent(self, tmp_path):
        """Re-install overwrites cleanly and re-runs enable — the
        moved-venv repair path (any deploy rewrites the units with the
        current console-script path)."""
        console = make_console(tmp_path)
        runner = FakeRunner()
        unit_dir = tmp_path / "units"

        first = install_watcher_units(
            console, unit_dir=unit_dir, runner=runner
        )
        second = install_watcher_units(
            console, unit_dir=unit_dir, runner=runner
        )

        assert second.service_path == first.service_path
        assert second.timer_path == first.timer_path
        assert second.service_path.read_text(encoding="utf-8") == (
            first.service_path.read_text(encoding="utf-8")
        )
        enables = [c for c in runner.calls if "enable" in c]
        reloads = [c for c in runner.calls if "daemon-reload" in c]
        starts = [
            c
            for c in runner.calls
            if c[1:3] == ("--user", "start")
        ]
        assert len(enables) == 2
        assert len(reloads) == 2
        assert len(starts) == 2

    def test_install_rewrites_a_stale_console_path(self, tmp_path):
        """The unit file tracks the console path it was installed WITH —
        a second install with a different venv rewrites ``ExecStart``."""
        unit_dir = tmp_path / "units"
        old_console = tmp_path / "old-venv" / "magic-hermes"
        old_console.parent.mkdir(parents=True)
        old_console.write_text("", encoding="utf-8")
        new_console = tmp_path / "new-venv" / "magic-hermes"
        new_console.parent.mkdir(parents=True)
        new_console.write_text("", encoding="utf-8")
        runner = FakeRunner()

        install_watcher_units(old_console, unit_dir=unit_dir, runner=runner)
        install_watcher_units(new_console, unit_dir=unit_dir, runner=runner)

        service = (unit_dir / CONVERGE_SERVICE_UNIT).read_text(
            encoding="utf-8"
        )
        assert f'ExecStart="{new_console.resolve()}" converge' in service
        assert str(old_console.resolve()) not in service

    def test_default_unit_dir_follows_xdg_config_home(self, monkeypatch):
        """Review finding 11: systemd reads user units from
        ``$XDG_CONFIG_HOME/systemd/user`` when the variable is set —
        hard-coding ``~/.config`` would land units where systemd never
        reads them on relocated-config hosts (resolved at call time,
        not import time)."""
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        assert watcher_units.default_unit_dir() == (
            Path.home() / ".config" / "systemd" / "user"
        )

        monkeypatch.setenv("XDG_CONFIG_HOME", "/srv/xdg-config")
        assert watcher_units.default_unit_dir() == (
            Path("/srv/xdg-config") / "systemd" / "user"
        )

    def test_service_start_verification_failure_raises(self, tmp_path):
        """Review finding 1 loud half: if the post-arm service start
        fails (dead ExecStart — stale venv, build without ``converge``),
        the module raises so deploy aborts and install warns."""
        console = make_console(tmp_path)
        runner = FakeRunner(
            fail=lambda call: call[-1] == CONVERGE_SERVICE_UNIT
            and "start" in call
        )

        with pytest.raises(RuntimeError, match="start"):
            install_watcher_units(
                console, unit_dir=tmp_path / "units", runner=runner
            )

        # enable already ran — the failure aborts at the verification,
        # after the timer was armed.
        assert (
            "systemctl",
            "--user",
            "enable",
            "--now",
            CONVERGE_TIMER_UNIT,
        ) in runner.calls

    def test_runner_failure_raises(self, tmp_path):
        """KTD 4 loud half: a failing runner command raises out of the
        module (deploy aborts on it; install catches it for the note)."""
        console = make_console(tmp_path)
        runner = FakeRunner(fail=lambda call: "daemon-reload" in call)

        with pytest.raises(RuntimeError, match="daemon-reload"):
            install_watcher_units(
                console, unit_dir=tmp_path / "units", runner=runner
            )

    def test_enable_failure_raises_after_reload(self, tmp_path):
        console = make_console(tmp_path)
        runner = FakeRunner(fail=lambda call: "enable" in call)

        with pytest.raises(RuntimeError, match="enable"):
            install_watcher_units(
                console, unit_dir=tmp_path / "units", runner=runner
            )

        # daemon-reload already ran — the failure aborts at the exact
        # command that failed, not the whole batch up front.
        assert ("systemctl", "--user", "daemon-reload") in runner.calls

    def test_default_runner_raises_on_nonzero_exit(self):
        """The subprocess-backed default runner (real systemctl callers)
        converts a non-zero exit into an exception — never a silent
        skip."""
        with pytest.raises(RuntimeError, match=r"exit 3"):
            watcher_units._default_runner(
                sys.executable, "-c", "import sys; sys.exit(3)"
            )


class TestModulePurity:
    def test_module_imports_stdlib_only(self):
        """Deployment constraint (plan U2): the module must import with
        zero non-stdlib dependencies — asserted over the parsed AST so a
        future third-party import fails here instead of on a deploy
        target."""
        source = Path(watcher_units.__file__).read_text(encoding="utf-8")
        allowed = set(sys.stdlib_module_names) | {"__future__"}
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] in allowed
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                assert (node.module or "").split(".")[0] in allowed
