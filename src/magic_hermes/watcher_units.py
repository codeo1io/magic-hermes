"""Systemd user units for out-of-band wiring convergence.

Defect class (maestro finding c7d63424daa3, root-cause chain verified
2026-10-30): the hermes-agent gateway can serialize factory defaults over
the wiring keys (``context.engine`` / ``memory.provider``) when it
rewrites ``~/.hermes/config.yaml`` — an out-of-band clobber that the
in-process convergence inside the plugin cannot heal while the gateway
process predates the convergence code or the plugin is disabled at the
moment of the write. The units rendered here remove the gateway-uptime
dependency entirely: a systemd user TIMER runs ``magic-hermes converge``
— the fail-open, lease-gated verb — every ``RECHECK_INTERVAL_S``
seconds, so any clobber converges back well inside the maestro probe
cadence (4-8 min).

Design pins (plans 2c06dd22 / a39e438e / e1d24534):

- TIMER, not a path unit (KTD 1): config.yaml is rewritten by atomic
  tmp+rename, where inotify-on-inode semantics are subtle; a
  fixed-interval poll is deterministic and trivially testable.
- NO flock (KTD 2): systemd never runs two instances of the same oneshot
  unit concurrently, and a concurrent in-process convergence in the
  gateway is explicitly benign (idempotent, atomic writes).
- The units run ``converge``, NEVER ``wire`` (KTD 3): the lease stays the
  sole intent gate, so a deliberately unwired host (lease deleted) is
  never re-wired by the watcher. Disabling the timer pauses out-of-band
  healing; deleting the lease is the full convergence opt-out.

The module is deliberately stdlib-only and renders the unit TEXTS in code
(single source of truth — no shipped template files to drift, KTD 6).
``install_watcher_units`` performs the side effects (write, daemon-reload,
enable) through an injectable runner, so the test battery never touches
systemctl. Detecting a missing systemd user session is the CALLER's
concern (KTD 4): ``magic-hermes install`` warns and continues, the deploy
lane fails loudly — this module raises on any runner failure and lets the
callers translate.

Ref: maestro finding c7d63424daa3 (phase2.magic-context.operational).
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

#: Oneshot service unit name — also the journal each pass lands in.
CONVERGE_SERVICE_UNIT = "magic-hermes-converge.service"
#: Timer unit name — the unit operators enable/disable to pause healing.
CONVERGE_TIMER_UNIT = "magic-hermes-converge.timer"
#: How often a converged host is re-checked, in seconds. Pinned by the
#: README/cli contract (Unit A fold) at 60 s: ~4-8x headroom against the
#: maestro 4-8 min probe cadence.
RECHECK_INTERVAL_S = 60
#: First check after boot — early enough to catch a boot-time clobber,
#: late enough not to race unit activation on a cold user session.
FIRST_CHECK_DELAY_S = 45
#: Default destination for the rendered unit files (systemd user units).
USER_UNIT_DIR = Path.home() / ".config" / "systemd" / "user"

#: Injectable command runner. Mirrors ``scripts.release.run``'s contract:
#: positional argv, raises on failure. Callers may pass capture/env
#: keywords; the default runner accepts and honors both.
Runner = Callable[..., object]


def render_service(console_path: Path) -> str:
    """Render the oneshot service running one ``converge`` pass.

    ``ExecStart`` requires an ABSOLUTE path (systemd rejects relative
    paths), so the console script is resolved before embedding — callers
    may hand over a relative or symlinked path.
    """
    executable = Path(console_path).resolve()
    return (
        "[Unit]\n"
        "Description=Magic-Hermes lease-gated wiring convergence"
        " (out of band)\n"
        "\n"
        "[Service]\n"
        "Type=oneshot\n"
        f"ExecStart={executable} converge\n"
    )


def render_timer() -> str:
    """Render the periodic timer driving the convergence service."""
    return (
        "[Unit]\n"
        f"Description=Run magic-hermes converge every {RECHECK_INTERVAL_S}s\n"
        "\n"
        "[Timer]\n"
        f"OnBootSec={FIRST_CHECK_DELAY_S}s\n"
        f"OnUnitActiveSec={RECHECK_INTERVAL_S}s\n"
        f"Unit={CONVERGE_SERVICE_UNIT}\n"
        "\n"
        "[Install]\n"
        "WantedBy=timers.target\n"
    )


@dataclass(frozen=True)
class WatcherInstall:
    """Outcome of one install pass: paths written, commands issued."""

    service_path: Path
    timer_path: Path
    commands: tuple[tuple[str, ...], ...]


def _default_runner(*args: str, capture: bool = False, env=None) -> object:
    """subprocess-backed runner: raises RuntimeError on non-zero exit.

    The loud half of the KTD 4 split — a real systemctl failure surfaces
    as an exception (deploy aborts, install prints its warn-only note)
    instead of being swallowed.
    """
    completed = subprocess.run(
        args, check=False, capture_output=capture, env=env
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"command failed (exit {completed.returncode}): "
            f"{' '.join(str(a) for a in args)}"
        )
    return completed


def install_watcher_units(
    console_path: Path,
    unit_dir: Path | None = None,
    runner: Runner = _default_runner,
) -> WatcherInstall:
    """Write, reload, and enable the convergence timer — idempotent.

    Writes both unit files (mode 0o644) into ``unit_dir`` (default
    ``USER_UNIT_DIR``), then issues ``systemctl --user daemon-reload`` and
    ``systemctl --user enable --now`` for the timer through ``runner``.
    Overwrite + re-enable is safe by design: re-running any deploy
    rewrites the units with the CURRENT console-script path, so a venv
    that moved since the last deploy heals on the next one. Raises
    whatever the runner raises on failure — callers translate (the
    ``install`` verb warns and continues; the deploy lane aborts loudly).
    """
    destination = Path(unit_dir) if unit_dir is not None else USER_UNIT_DIR
    destination.mkdir(parents=True, exist_ok=True)
    service_path = destination / CONVERGE_SERVICE_UNIT
    timer_path = destination / CONVERGE_TIMER_UNIT
    service_path.write_text(
        render_service(console_path), encoding="utf-8"
    )
    timer_path.write_text(render_timer(), encoding="utf-8")
    for unit in (service_path, timer_path):
        unit.chmod(0o644)
    commands = (
        ("systemctl", "--user", "daemon-reload"),
        ("systemctl", "--user", "enable", "--now", CONVERGE_TIMER_UNIT),
    )
    for command in commands:
        runner(*command)
    return WatcherInstall(
        service_path=service_path,
        timer_path=timer_path,
        commands=commands,
    )
