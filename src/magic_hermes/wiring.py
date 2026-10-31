"""Lease-guarded wiring convergence for the Hermes config (Mode 2).

Host-side rewrites of ``~/.hermes/config.yaml`` legitimately serialize
``context.engine``/``memory.provider`` back to Hermes factory defaults
(``compressor``/``null``) while ``plugins.enabled`` keeps ``magic-hermes``
— unplugging the plugin from the host and flagging the doctor's unwired
FAIL row on every evaluation (maestro finding c7d63424, Mode 2). This
module repairs exactly that defect class, and nothing else:

- **The lease is the write authorization.** ``magic-hermes install``
  records operator intent as a JSON lease under the Hermes-owned data
  root; ``converge()`` writes only when the lease exists. No lease means
  zero writes even from a factory-default config — a user who
  deliberately unwired (or never ran install, or ran ``install
  --skip-config``, which arms nothing) is never re-wired.
- **Factory-default-only repair domain.** Repairable ⇔ BOTH keys sit in
  their factory domains (engine ∈ {absent, ``compressor``} AND provider
  ∈ {absent, ``null``}) — the exact host-clobber signature. Any other
  explicit value, including partial states, is a deliberate operator
  choice: no write, and the doctor names the override.
- **Fail-open everywhere.** Every convergence miss (no lease, missing
  config file, no YAML library importable, a torn read, a writer
  failure) is a logged no-op that never raises. The gateway must never
  fail to load because of convergence (the
  ``_register_dreamer_tools`` containment precedent).
- **Idempotent, atomic, comment-preserving writes** through the
  ``configure_hermes`` machinery moved here from ``cli.py`` (``cli``
  re-exports so every existing import keeps working), with timestamped
  backups pruned to the newest ten. Convergence invokes it scoped to
  exactly ``WIRED_KEYS`` — the lease's declared wires — so a background
  recheck never re-appends the plugin to ``plugins.enabled`` (W2:
  ``install`` wires everything; convergence repairs only what the lease
  authorizes).
- **Rate-limited recheck.** The pure ``should_recheck`` decision plus
  lock-guarded module state; the interval comes from
  ``MAGIC_HERMES_WIRING_RECHECK_S`` (default 900 s, clamped to
  [0, 86400]). Plugin-load convergence is not rate-limited — load is the
  natural cadence (the call sites are plan unit U5, plugin.py).

Like ``historian_guard.py``, this module is stdlib-only at import and
imports nothing from its own package: it is imported by ``plugin.py``
inside the host gateway, which must run with zero installed
dependencies (``pyproject.toml`` has ``dependencies = []``); the YAML
library imports stay lazy inside functions.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

PLUGIN_NAME = "magic-hermes"

# The wiring lease: the operator-intent record written by
# `magic-hermes install` (plan W2). `--skip-config` writes no lease.
WIRING_LEASE_NAME = "wiring-lease.json"
LEASE_SCHEMA = 1
#: The keys convergence repairs. `plugins.enabled` is deliberately NOT in
#: this list: when the plugin is no longer enabled it never loads to run
#: convergence at all, so the lease cannot restore its own enablement —
#: the doctor names that gap instead (W4).
WIRED_KEYS = ("context.engine", "memory.provider")

# Factory-default repair domains (plan W2). engine: {absent, compressor};
# provider: {absent, explicit null} — the observed host-clobber signature.
WIRED_ENGINE = "magic-context"
WIRED_PROVIDER = "magic_context"
_FACTORY_ENGINES = frozenset({"compressor"})

# Recheck rate limit: 900 s monotonic default, env-overridable (plan W1),
# mirroring the `_doctor_wall_budget_s` budget-env convention: junk never
# widens, out-of-range values clamp.
RECHECK_ENV = "MAGIC_HERMES_WIRING_RECHECK_S"
DEFAULT_RECHECK_S = 900.0
RECHECK_MIN_S = 0.0
RECHECK_MAX_S = 86400.0

# Timestamped backup retention (plan W3): each real repair keeps a
# backup; convergence prunes to the newest ten so clobber/repair
# flapping cannot grow the profile directory without bound.
BACKUP_KEEP = 10

_MISSING = object()


# ---------------------------------------------------------------------------
# path helpers (single source of truth; `cli` re-exports these)
# ---------------------------------------------------------------------------


def hermes_home() -> Path:
    """Return the Hermes home directory (profile-aware)."""

    override = os.environ.get("HERMES_HOME")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".hermes"


def hermes_config_path() -> Path:
    return hermes_home() / "config.yaml"


def _xdg_data_home() -> Path:
    configured = os.environ.get("XDG_DATA_HOME")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".local" / "share"


def managed_npm_root() -> Path:
    """Return the Hermes-owned npm prefix managed by this installer."""

    return _xdg_data_home() / PLUGIN_NAME


# ---------------------------------------------------------------------------
# Hermes config wiring (moved verbatim from cli.py; behaviour unchanged)
# ---------------------------------------------------------------------------


def load_yaml(path: Path) -> tuple[Any, Any]:
    """Return (document, saver). Prefers ruamel.yaml for comment retention.

    The saver preserves the target's existing file mode across the
    tmp+replace rewrite (a fresh target gets 0600) — the Hermes config
    can carry provider credentials and must not widen to the umask
    default just because convergence rewrote it.
    """

    def copy_mode(tmp: Path, target: Path) -> None:
        try:
            mode = os.stat(target).st_mode & 0o777
        except OSError:
            mode = 0o600
        os.chmod(tmp, mode)

    try:
        from ruamel.yaml import YAML  # type: ignore[import-not-found]

        yaml = YAML()
        yaml.preserve_quotes = True
        with path.open(encoding="utf-8") as handle:
            document = yaml.load(handle)

        def saver(target: Path, doc: Any) -> None:
            tmp = target.with_suffix(target.suffix + ".tmp")
            with tmp.open("w", encoding="utf-8") as handle:
                yaml.dump(doc, handle)
            copy_mode(tmp, target)
            tmp.replace(target)

        return document, saver
    except ImportError:
        import yaml  # type: ignore[import-not-found]

        with path.open(encoding="utf-8") as handle:
            document = yaml.safe_load(handle)

        def saver(target: Path, doc: Any) -> None:
            tmp = target.with_suffix(target.suffix + ".tmp")
            with tmp.open("w", encoding="utf-8") as handle:
                yaml.safe_dump(doc, handle, sort_keys=False)
            copy_mode(tmp, target)
            tmp.replace(target)

        return document, saver


# Back-compat alias for the private name this helper carried when it
# lived in cli.py (the rename made it a public cross-module API).
_load_yaml = load_yaml


_ALL_REPAIR_KEYS = ("context.engine", "memory.provider", "plugins.enabled")


def configure_hermes(
    config_path: Path | None = None, *, keys: Iterable[str] | None = None
) -> list[str]:
    """Point Hermes at the plugin. Idempotent; returns the changed keys.

    ``keys`` restricts the repair to a subset of the three wiring keys
    (d-sec0-cor1): convergence passes exactly ``WIRED_KEYS`` — the
    lease's declared wires — so a deliberately unplugged plugin is never
    re-appended to ``plugins.enabled`` by a background recheck;
    ``install`` keeps the default (all three) because it is the explicit
    operator wiring act.
    """

    path = config_path or hermes_config_path()
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
        os.chmod(path, 0o600)
    try:
        document, saver = load_yaml(path)
    except ImportError:
        raise SystemExit(
            "configuring Hermes requires a YAML library (ruamel.yaml keeps "
            "comments; PyYAML also works). Install one into the same "
            "environment as magic-hermes, e.g. "
            "`pip install ruamel.yaml`, then re-run `magic-hermes install`. "
            "The upstream package install itself is unaffected."
        ) from None
    if not isinstance(document, dict):
        document = {}

    wanted = frozenset(_ALL_REPAIR_KEYS if keys is None else keys)
    changed: list[str] = []

    if "context.engine" in wanted:
        context = document.setdefault("context", {})
        if not isinstance(context, dict):
            context = document["context"] = {}
        if context.get("engine") != WIRED_ENGINE:
            context["engine"] = WIRED_ENGINE
            changed.append("context.engine")

    if "memory.provider" in wanted:
        memory = document.setdefault("memory", {})
        if not isinstance(memory, dict):
            memory = document["memory"] = {}
        if memory.get("provider") != WIRED_PROVIDER:
            memory["provider"] = WIRED_PROVIDER
            changed.append("memory.provider")

    if "plugins.enabled" in wanted:
        plugins = document.setdefault("plugins", {})
        if not isinstance(plugins, dict):
            plugins = document["plugins"] = {}
        enabled = plugins.setdefault("enabled", [])
        if not isinstance(enabled, list):
            enabled = plugins["enabled"] = []
        if PLUGIN_NAME not in enabled:
            enabled.append(PLUGIN_NAME)
            changed.append("plugins.enabled")

    if changed:
        backup = path.with_name(path.name + f".bak.{int(time.time())}")
        shutil.copy2(path, backup)
        saver(path, document)
    return changed


# ---------------------------------------------------------------------------
# the wiring lease
# ---------------------------------------------------------------------------


def lease_path() -> Path:
    """The machine-wide operator-intent lease (per-profile configs converge
    their own config files under this one lease — deliberate, documented in
    the README install section)."""

    return managed_npm_root() / WIRING_LEASE_NAME


def read_lease() -> dict[str, Any] | None:
    """Return the parsed lease, or None when absent or not ours (fail-open).

    A corrupt or foreign-schema lease file is treated like a missing one —
    convergence writes nothing on doubt; `install` re-mints a fresh lease.
    """

    try:
        payload = json.loads(lease_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("schema") != LEASE_SCHEMA or payload.get("plugin") != PLUGIN_NAME:
        return None
    wires = payload.get("wires")
    if not isinstance(wires, list) or not set(WIRED_KEYS) <= set(map(str, wires)):
        return None
    return payload


def write_lease(now: float | None = None) -> Path:
    """Create or refresh the lease; refresh preserves ``created_at``."""

    path = lease_path()
    epoch = time.time() if now is None else float(now)
    existing = read_lease()
    created_at = epoch
    if existing is not None:
        prior_created = existing.get("created_at")
        if isinstance(prior_created, (int, float)):
            created_at = float(prior_created)
    payload = {
        "schema": LEASE_SCHEMA,
        "plugin": PLUGIN_NAME,
        "wires": list(WIRED_KEYS),
        "created_at": created_at,
        "updated_at": epoch,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    try:
        mode = os.stat(path).st_mode & 0o777
    except OSError:
        mode = 0o600
    os.chmod(tmp, mode)
    tmp.replace(path)
    return path


# ---------------------------------------------------------------------------
# drift classification (pure)
# ---------------------------------------------------------------------------


def _section_value(document: Any, section: str, key: str) -> Any:
    """Read ``section.key``, returning the ``_MISSING`` sentinel when the
    section or key is absent (distinguishing absent from explicit null)."""

    if not isinstance(document, dict):
        return _MISSING
    section_doc = document.get(section, _MISSING)
    if not isinstance(section_doc, dict):
        return _MISSING
    return section_doc.get(key, _MISSING)


def classify_document(document: Any) -> str:
    """Classify a parsed config document (plan W2).

    ``converged`` — both keys already carry the target values.
    ``repairable`` — both keys in their factory domains (the host-clobber
    signature). ``deliberate`` — anything else, including partial states
    and explicit nulls outside the factory domains: no write.
    """

    engine = _section_value(document, "context", "engine")
    provider = _section_value(document, "memory", "provider")
    if engine == WIRED_ENGINE and provider == WIRED_PROVIDER:
        return "converged"
    engine_factory = engine is _MISSING or engine in _FACTORY_ENGINES
    provider_factory = provider is _MISSING or provider is None
    if engine_factory and provider_factory:
        return "repairable"
    return "deliberate"


# ---------------------------------------------------------------------------
# convergence
# ---------------------------------------------------------------------------


@dataclass
class ConvergeResult:
    """Outcome of one convergence attempt (for callers and tests).

    ``status`` ∈ {"no-lease", "no-config", "no-yaml-lib", "unparseable",
    "deliberate", "error", "converged", "repaired", "not-due"}.
    """

    status: str
    changed: list[str] = field(default_factory=list)
    lease_path: Path | None = None
    detail: str = ""


def _prune_backups(config_path: Path, keep: int = BACKUP_KEEP) -> None:
    """Keep the newest ``keep`` ``config.yaml.bak.<epoch>`` siblings.

    Only siblings whose suffix parses as a numeric epoch are ours to
    delete (d-adv2): operator-made files that merely match the glob —
    ``config.yaml.bak.manual``, ``.bak.pre-experiment`` — are left
    untouched no matter how crowded the directory gets.
    """

    def stamp(backup: Path) -> float | None:
        try:
            return float(backup.name.rsplit(".", 1)[1])
        except (ValueError, IndexError):
            return None

    stamped = []
    for candidate in config_path.parent.glob(config_path.name + ".bak.*"):
        if not candidate.is_file():
            continue
        candidate_stamp = stamp(candidate)
        if candidate_stamp is not None:
            stamped.append((candidate_stamp, candidate))
    backups = [p for _, p in sorted(stamped, key=lambda pair: pair[0])]
    for stale in backups[:-keep] if keep > 0 else backups:
        try:
            stale.unlink()
        except OSError:
            log.debug("wiring: could not remove stale backup %s", stale)


def converge(config_path: Path | None = None) -> ConvergeResult:
    """Repair factory-default wiring drift under the lease; fail-open.

    Never raises: every miss or failure is a logged no-op returning a
    small result object, because convergence runs inside the gateway
    process (plugin load, plan U5) and must never break it.
    """

    path = config_path or hermes_config_path()
    lease = lease_path()
    if read_lease() is None:
        log.debug("wiring convergence: no lease at %s — not converging", lease)
        return ConvergeResult("no-lease")
    if not path.is_file():
        log.debug("wiring convergence: no config file at %s", path)
        return ConvergeResult("no-config", lease_path=lease)
    try:
        document, _saver = load_yaml(path)
    except ImportError:
        return ConvergeResult("no-yaml-lib", lease_path=lease)
    except Exception as exc:  # torn read / parse error / unreadable
        log.debug("wiring convergence: config unreadable at %s: %s", path, exc)
        return ConvergeResult("unparseable", lease_path=lease, detail=str(exc))
    classification = classify_document(document)
    if classification != "repairable":
        return ConvergeResult(classification, lease_path=lease)
    # Plan U4 key decision — re-read + reclassify immediately before the
    # write: an operator value (or host clobber) landing between the
    # classification above and this fresh read WINS and is returned,
    # never overwritten — last-writer-wins, converged by idempotence on
    # the next recheck. The residual window is configure_hermes's own
    # read-to-replace gap (atomic tmp+replace, no locking).
    try:
        fresh_document, _saver = load_yaml(path)
    except ImportError:
        return ConvergeResult("no-yaml-lib", lease_path=lease)
    except Exception as exc:  # torn read / parse error / unreadable
        log.debug("wiring convergence: config unreadable at %s: %s", path, exc)
        return ConvergeResult("unparseable", lease_path=lease, detail=str(exc))
    fresh = classify_document(fresh_document)
    if fresh != "repairable":
        return ConvergeResult(fresh, lease_path=lease)
    # Scoped to the lease's declared wires (d-sec0-cor1): convergence
    # repairs exactly WIRED_KEYS and never re-appends the plugin to
    # plugins.enabled — enablement is install's explicit act (W2/W4).
    try:
        changed = configure_hermes(path, keys=WIRED_KEYS)
        _prune_backups(path)
    except (Exception, SystemExit) as exc:  # containment is the contract
        log.warning("wiring convergence failed at %s: %s", path, exc)
        return ConvergeResult("error", lease_path=lease, detail=str(exc))
    return ConvergeResult(
        "repaired" if changed else "converged",
        changed=changed,
        lease_path=lease,
    )


# ---------------------------------------------------------------------------
# rate-limited recheck (the pre_tool_call call site is plan unit U5)
# ---------------------------------------------------------------------------

_state_lock = threading.Lock()
_last_recheck: float | None = None


def should_recheck(now: float, last_check: float | None, interval: float) -> bool:
    """Pure rate-limit decision: due when never checked or after ``interval``."""

    if last_check is None:
        return True
    return (now - last_check) >= interval


def recheck_interval_s() -> float:
    """Resolve the recheck interval (env override, junk-proof, clamped)."""

    raw = os.environ.get(RECHECK_ENV, "")
    try:
        value = float(raw) if raw.strip() else DEFAULT_RECHECK_S
    except ValueError:
        return DEFAULT_RECHECK_S
    if value != value or value in (float("inf"), float("-inf")):
        return DEFAULT_RECHECK_S
    return min(RECHECK_MAX_S, max(RECHECK_MIN_S, value))


def recheck(config_path: Path | None = None) -> ConvergeResult:
    """Converge only when the interval has elapsed; cheap when not due.

    The interval is read at call time so an env change takes effect
    without a process restart; the config is read at most once per
    interval per process.
    """

    global _last_recheck
    with _state_lock:
        now = time.monotonic()
        if not should_recheck(now, _last_recheck, recheck_interval_s()):
            return ConvergeResult("not-due")
        _last_recheck = now
    return converge(config_path)


def _reset_recheck_state() -> None:
    """Test helper: clear the process-local last-recheck stamp."""

    global _last_recheck
    with _state_lock:
        _last_recheck = None


# ---------------------------------------------------------------------------
# read-only state summary (the doctor's lease-aware rows are plan unit U6)
# ---------------------------------------------------------------------------

_ABSENT = "<absent>"
_UNREADABLE = "<unreadable>"


def describe(config_path: Path | None = None) -> dict[str, Any]:
    """Read-only wiring state for the doctor's lease-aware rows (W4).

    Values render as ``<absent>``/``<unreadable>`` rather than raising or
    inventing observations; ``plugin_enabled`` reports whether
    ``plugins.enabled`` still lists the plugin (None only when the
    document itself is unreadable).
    """

    path = config_path or hermes_config_path()
    state: dict[str, Any] = {
        "has_lease": read_lease() is not None,
        "lease_path": str(lease_path()),
        "classification": "unknown",
        "engine": _UNREADABLE,
        "provider": _UNREADABLE,
        "plugin_enabled": None,
    }
    if not path.is_file():
        state["classification"] = "no-config"
        return state
    try:
        document, _saver = load_yaml(path)
    except ImportError:
        state["classification"] = "no-yaml-lib"
        return state
    except Exception:  # torn read / parse error
        return state
    state["classification"] = classify_document(document)
    state["engine"] = _render(_section_value(document, "context", "engine"))
    state["provider"] = _render(_section_value(document, "memory", "provider"))
    plugins = (
        document.get("plugins") if isinstance(document, dict) else None
    )
    enabled = plugins.get("enabled") if isinstance(plugins, dict) else None
    state["plugin_enabled"] = (
        isinstance(enabled, list) and PLUGIN_NAME in enabled
    )
    return state


def _render(value: Any) -> Any:
    if value is _MISSING:
        return _ABSENT
    return value
