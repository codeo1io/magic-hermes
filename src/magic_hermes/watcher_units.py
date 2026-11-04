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
- ONE shared slot (7e69362a P1): the systemd user units are a single
  host-wide last-writer-wins slot. ``install`` must never steal a
  watcher armed at another LIVE venv — the conflict guard raises
  ``WatcherConflictError`` unless the armed console is the same one or
  has died; only a caller that owns the slot (the deploy lane, which
  quiesces and re-verifies) passes ``takeover=True``.

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

import contextlib
import os
import re
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


def default_unit_dir() -> Path:
    """Where systemd user units are read from on this host.

    Resolved at CALL time (not import time) and honors
    ``$XDG_CONFIG_HOME`` (systemd.user(5): user units load from
    ``$XDG_CONFIG_HOME/systemd/user`` when the variable is set, else
    ``~/.config/systemd/user``) — on hosts that relocate the config
    home, hard-coding ``~/.config`` would land units where systemd
    never reads them while ``daemon-reload`` still exits 0 (review
    finding 11: silently dead watcher). A RELATIVE ``$XDG_CONFIG_HOME``
    is invalid per the XDG Base Directory spec ("as if unset") and is
    ignored the same way — systemd would never read a relative unit
    dir, so honoring it would arm a silently dead watcher (7e69362a
    finding 8).
    """
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = (
        Path(xdg).expanduser()
        if xdg and Path(xdg).is_absolute()
        else Path.home() / ".config"
    )
    return base / "systemd" / "user"

#: Injectable command runner. Mirrors ``scripts.release.run``'s contract:
#: positional argv, raises on failure. Callers may pass capture/env
#: keywords; the default runner accepts and honors both.
Runner = Callable[..., object]


def _escape_execstart_argument(path: str) -> str:
    """Escape a path for one double-quoted ``ExecStart`` argv word.

    systemd.service(5) command-line syntax: inside a quoted word,
    backslash and double quote must be backslash-escaped (a raw ``"``
    would close the word early and hand systemd a split argv), ``$``
    doubled (``$$`` is the literal — a raw ``$`` expands as a variable
    reference), ``%`` doubled (systemd.unit(5) specifier syntax).
    Backslash is escaped FIRST so the backslashes introduced here and
    by the quote escape stay single (7e69362a finding 6).
    """
    return (
        path.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("$", "$$")
        .replace("%", "%%")
    )


def render_service(console_path: Path) -> str:
    """Render the oneshot service running one ``converge`` pass.

    ``ExecStart`` requires an ABSOLUTE path (systemd rejects relative
    paths), so the console script is resolved before embedding — callers
    may hand over a relative or symlinked path. The path is QUOTED and
    escaped (``\\`` ``"`` ``$`` ``%`` — systemd.unit(5)/systemd.service(5)
    syntax): an unquoted venv path containing whitespace would split
    into multiple argv words, a ``%`` would be silently rewritten as a
    specifier, and a ``$`` would be expanded as a variable — all leave
    a loadable-but-dead unit whose ``enable`` stays green (review
    finding 2: healing silently absent).
    """
    executable = Path(console_path).resolve()
    escaped = _escape_execstart_argument(str(executable))
    return (
        "[Unit]\n"
        "Description=Magic-Hermes lease-gated wiring convergence"
        " (out of band)\n"
        "\n"
        "[Service]\n"
        "Type=oneshot\n"
        f'ExecStart="{escaped}" converge\n'
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
        "AccuracySec=1s\n"
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
    #: The console script the rendered service executes — carried so
    #: callers can print WHICH venv was armed (review finding 1:
    #: armed-venv visibility on the shared, last-writer-wins unit).
    console: Path


_EXECSTART_RE = re.compile(r'^ExecStart="([^"]+)"\s+converge\s*$', re.MULTILINE)


def armed_console_path(unit_dir: Path | None = None) -> Path | None:
    """The console the INSTALLED service unit currently fires, if any.

    Reads the on-disk service unit (``unit_dir``, or the resolved
    default unit dir) and extracts the quoted ``ExecStart`` path.
    Returns ``None`` when the unit is absent or does not match the shape
    this module renders — a foreign or malformed unit is never treated
    as a conflict, because overwriting it is a repair (7e69362a P1#2:
    the conflict guard needs to know WHO is armed, and only a unit we
    can attribute counts).
    """
    destination = Path(unit_dir) if unit_dir is not None else default_unit_dir()
    try:
        text = (destination / CONVERGE_SERVICE_UNIT).read_text(encoding="utf-8")
    except OSError:
        return None
    match = _EXECSTART_RE.search(text)
    return Path(match.group(1)) if match else None


class WatcherConflictError(RuntimeError):
    """Install refused: a DIFFERENT, still-live console is already armed.

    The systemd user units are ONE shared slot (last-writer-wins): an
    unconditional overwrite from any venv re-points the host's only
    healer at whatever venv happened to run ``install`` — live-
    demonstrated 2026-11-04, the host's timer pointed at an ephemeral
    conductor worktree ``.venv`` that dies at cleanup, which is exactly
    the c7d63424 recurrence shape. Callers that OWN the slot pass
    ``takeover=True`` (the deploy lane: it quiesces the timer first and
    re-verifies the new ``ExecStart`` with a service start).
    """

    def __init__(self, existing_console: Path, requested_console: Path) -> None:
        self.existing_console = existing_console
        self.requested_console = requested_console
        super().__init__(
            f"the converge watcher is already armed at {existing_console} "
            f"(still present) — refusing to re-point it at "
            f"{requested_console}; a deploy owns the re-point (it "
            "re-verifies), or disable the timer first: systemctl --user "
            f"disable --now {CONVERGE_TIMER_UNIT}"
        )


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


def _best_effort_disable(runner: Runner) -> None:
    """Rollback for a failed arming: disarm the timer we enabled.

    A failure after ``enable --now`` means the armed ``ExecStart`` is
    unverified or dead — an ENABLED timer on a dead console is green
    enable state failing every 60 s, the exact failure class the
    post-arm verification exists to prevent (7e69362a finding 5:
    enable-before-verify with no rollback). Failures of the rollback
    itself are swallowed — the ORIGINAL exception is the signal the
    caller needs.
    """
    with contextlib.suppress(Exception):
        runner("systemctl", "--user", "disable", "--now", CONVERGE_TIMER_UNIT)


def install_watcher_units(
    console_path: Path,
    unit_dir: Path | None = None,
    runner: Runner = _default_runner,
    takeover: bool = False,
) -> WatcherInstall:
    """Write, reload, enable, and VERIFY the convergence timer.

    Writes both unit files (mode 0o644) into ``unit_dir`` (default
    ``default_unit_dir()``), then issues ``systemctl --user
    daemon-reload``, ``systemctl --user enable --now`` for the timer, and
    — the post-arm verification (review finding 1) — ``systemctl --user
    start`` for the SERVICE through ``runner``. ``enable --now`` starts
    the TIMER, but systemd never executes the service at arm time, so a
    stale/removed venv or a build without the ``converge`` verb would
    leave an active-looking timer failing every 60 s and passing the
    deploy lane silently (live-demonstrated on this host 2026-11-04:
    a unit rewritten to a venv whose magic-hermes lacked ``converge``
    failed with ``invalid choice`` while ``enable`` stayed green). The
    explicit ``start`` runs one converge pass NOW: the oneshot either
    completes rc 0 or the start job fails and the runner raises —
    deploy aborts loudly, ``install`` prints its warn-only note. As a
    bonus the pass fires an immediate heal instead of waiting for the
    first timer tick. A failure AT ``enable`` or the verification
    ``start`` first disarms the timer again (``_best_effort_disable``)
    so no enabled timer is left on an unverified ``ExecStart``.

    Shared-slot conflict guard (7e69362a P1#2): with the default
    ``takeover=False`` the install is REFUSED — ``WatcherConflictError``
    — when the on-disk service unit already fires a DIFFERENT console
    that still EXISTS: the units are one host-wide last-writer-wins
    slot, and an unconditional overwrite lets any ephemeral venv steal
    the host's only healer and strand it at a path that dies at cleanup.
    Re-arming the SAME console stays allowed (idempotent), and a unit
    pointing at a console that no longer exists is repaired, not
    protected (a dead ``ExecStart`` is never worth preserving).
    ``takeover=True`` (deploy lane: it quiesces the timer and
    re-verifies the new ``ExecStart``) keeps the unconditional
    overwrite-and-re-enable behavior. Raises whatever the runner raises
    on failure — callers translate (the ``install`` verb warns and
    continues; the deploy lane aborts loudly).
    """
    console = Path(console_path).resolve()
    destination = Path(unit_dir) if unit_dir is not None else default_unit_dir()
    if not takeover:
        existing = armed_console_path(destination)
        if (
            existing is not None
            and existing != console
            and existing.exists()
        ):
            raise WatcherConflictError(existing, console)
    destination.mkdir(parents=True, exist_ok=True)
    service_path = destination / CONVERGE_SERVICE_UNIT
    timer_path = destination / CONVERGE_TIMER_UNIT
    service_path.write_text(render_service(console), encoding="utf-8")
    timer_path.write_text(render_timer(), encoding="utf-8")
    for unit in (service_path, timer_path):
        unit.chmod(0o644)
    commands = (
        ("systemctl", "--user", "daemon-reload"),
        ("systemctl", "--user", "enable", "--now", CONVERGE_TIMER_UNIT),
        ("systemctl", "--user", "start", CONVERGE_SERVICE_UNIT),
    )
    runner(*commands[0])
    try:
        runner(*commands[1])
    except Exception:
        _best_effort_disable(runner)
        raise
    try:
        runner(*commands[2])
    except Exception:
        _best_effort_disable(runner)
        raise
    return WatcherInstall(
        service_path=service_path,
        timer_path=timer_path,
        commands=commands,
        console=console,
    )
