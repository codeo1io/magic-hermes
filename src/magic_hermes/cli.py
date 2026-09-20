"""First-class installer and doctor for the magic-hermes plugin.

`magic-hermes install` auto-detects the upstream Magic Context package,
installs or updates it into a Hermes-owned npm root, wires the Hermes
config, and verifies the runtime end to end. `magic-hermes doctor`
reports installation health in the style of the upstream
`@cortexkit/magic-context doctor`.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .runtime import (
    RuntimeClient,
    _package_version,
    magic_context_package_candidates,
    supported_magic_context_series,
    tested_magic_context_version,
    xdg_data_home,
)

PLUGIN_NAME = "magic-hermes"
UPSTREAM_PACKAGE = "@cortexkit/pi-magic-context"
HERMES_CONFIG_RELPATH = Path(".hermes") / "config.yaml"
SHARED_DB_RELPATH = (
    Path(".local") / "share" / "cortexkit" / "magic-context" / "context.db"
)

_SEMVER = re.compile(
    r"^(0|[1-9][0-9]*)[.](0|[1-9][0-9]*)[.](0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:[+][0-9A-Za-z.-]+)?$"
)


def _semver_tuple(version: str) -> tuple[int, int, int] | None:
    match = _SEMVER.fullmatch(version.strip())
    if match is None:
        return None
    return tuple(int(part) for part in match.groups()[:3])  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# detection helpers
# ---------------------------------------------------------------------------


def hermes_home() -> Path:
    """Return the Hermes home directory (profile-aware)."""

    override = os.environ.get("HERMES_HOME")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".hermes"


def hermes_config_path() -> Path:
    return hermes_home() / "config.yaml"


def managed_npm_root() -> Path:
    """Return the Hermes-owned npm prefix managed by this installer."""

    return xdg_data_home() / PLUGIN_NAME


def discover_installations() -> list[tuple[Path, str | None]]:
    """Return every discoverable upstream package root with its version."""

    found: list[tuple[Path, str | None]] = []
    for candidate in magic_context_package_candidates():
        if (candidate / "package.json").is_file() and (
            candidate / "dist" / "index.js"
        ).is_file():
            found.append((candidate, _package_version(candidate)))
    return found


def npm_latest_version(package: str = UPSTREAM_PACKAGE) -> str | None:
    """Return the npm `latest` dist-tag for the package, or None offline."""

    try:
        result = subprocess.run(
            ["npm", "view", package, "version", "--json"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    payload = json.loads(result.stdout or "null")
    if isinstance(payload, list):
        payload = payload[-1] if payload else None
    version = str(payload or "").strip().strip('"')
    return version if _semver_tuple(version) else None


# ---------------------------------------------------------------------------
# install planning
# ---------------------------------------------------------------------------


@dataclass
class InstallPlan:
    action: str  # "install" | "update" | "reuse" | "hold"
    target_version: str
    effective_root: Path | None
    effective_version: str | None
    notes: list[str] = field(default_factory=list)


def plan_install(
    target_version: str,
    package_root: Path | None = None,
    npm_latest: str | None = None,
) -> InstallPlan:
    """Decide how to reach `target_version` without mutating foreign homes.

    Rules learned from the 2026-09-20 schema-fence incident: never downgrade
    or rewrite npm state owned by other tools' homes (pi / opencode); when a
    change is needed, install the validated version into the Hermes-owned
    root, which sits first in the runtime's candidate order.
    """

    installations = discover_installations()
    effective = None
    if package_root is not None:
        if _package_version(package_root):
            effective = (package_root, _package_version(package_root))
        else:
            raise SystemExit(f"--package-root is not a usable package: {package_root}")
    elif installations:
        effective = installations[0]

    managed_root = (
        managed_npm_root() / "node_modules" / "@cortexkit" / "pi-magic-context"
    )

    if effective is None:
        return InstallPlan(
            action="install",
            target_version=target_version,
            effective_root=None,
            effective_version=None,
            notes=[f"no upstream package found; will install {target_version}"],
        )

    root, version = effective
    current = _semver_tuple(version or "")
    wanted = _semver_tuple(target_version)
    if current is None or wanted is None:
        return InstallPlan(
            action="hold",
            target_version=target_version,
            effective_root=root,
            effective_version=version,
            notes=[f"cannot parse installed version {version!r}; leaving it alone"],
        )

    if root == managed_root.resolve():
        if current == wanted:
            return InstallPlan(
                "reuse", target_version, root, version, ["managed copy already current"]
            )
        return InstallPlan(
            "update", target_version, root, version, ["updating the managed copy"]
        )

    if current == wanted:
        return InstallPlan(
            "reuse",
            target_version,
            root,
            version,
            [f"using existing installation at {root}"],
        )

    if current > wanted:
        note = (
            f"found newer {version} at {root} than this build validates "
            f"({target_version}); installing the validated version into the "
            "Hermes-owned root (foreign homes are never rewritten), which "
            "takes precedence"
        )
    else:
        note = (
            f"found older {version} at {root}; installing {target_version} "
            "into the Hermes-owned root (foreign homes are never rewritten)"
        )
    notes = [note]
    latest_tuple = _semver_tuple(npm_latest) if npm_latest else None
    if latest_tuple and latest_tuple > wanted:
        notes.append(
            f"npm latest is {npm_latest}; run the release pipeline before "
            "adopting it (the shared DB schema fence follows the newest copy)"
        )
    return InstallPlan("update", target_version, root, version, notes)


def run_npm_install(root: Path, version: str) -> None:
    """Create the managed npm root pinned to the exact upstream version."""

    root.mkdir(parents=True, exist_ok=True)
    manifest = {
        "name": "magic-hermes-managed",
        "private": True,
        "description": (
            "Hermes-owned Magic Context install managed by `magic-hermes install`"
        ),
        "dependencies": {UPSTREAM_PACKAGE: version},
    }
    (root / "package.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    command = [
        "npm",
        "install",
        "--prefix",
        str(root),
        "--no-audit",
        "--no-fund",
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        raise SystemExit(
            "npm install failed in the managed root:\n"
            f"{result.stdout}\n{result.stderr}"
        )


# ---------------------------------------------------------------------------
# Hermes config wiring
# ---------------------------------------------------------------------------


def _load_yaml(path: Path) -> tuple[Any, Any]:
    """Return (document, saver). Prefers ruamel.yaml for comment retention."""

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
            tmp.replace(target)

        return document, saver


def configure_hermes(config_path: Path | None = None) -> list[str]:
    """Point Hermes at the plugin. Idempotent; returns the changed keys."""

    path = config_path or hermes_config_path()
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
    try:
        document, saver = _load_yaml(path)
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

    changed: list[str] = []

    context = document.setdefault("context", {})
    if not isinstance(context, dict):
        context = document["context"] = {}
    if context.get("engine") != "magic-context":
        context["engine"] = "magic-context"
        changed.append("context.engine")

    memory = document.setdefault("memory", {})
    if not isinstance(memory, dict):
        memory = document["memory"] = {}
    if memory.get("provider") != "magic_context":
        memory["provider"] = "magic_context"
        changed.append("memory.provider")

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
# doctor checks
# ---------------------------------------------------------------------------


@dataclass
class Check:
    status: str  # "PASS" | "WARN" | "FAIL" | "INFO"
    message: str


class DoctorReport:
    def __init__(self) -> None:
        self.checks: list[Check] = []

    def add(self, status: str, message: str) -> None:
        self.checks.append(Check(status, message))

    @property
    def failed(self) -> int:
        return sum(1 for c in self.checks if c.status == "FAIL")

    @property
    def warned(self) -> int:
        return sum(1 for c in self.checks if c.status == "WARN")

    @property
    def passed(self) -> int:
        return sum(1 for c in self.checks if c.status == "PASS")


def _config_wiring_from_document(document: dict[str, Any]) -> tuple[bool, bool, bool]:
    engine = document.get("context") or {}
    memory = document.get("memory") or {}
    plugins = document.get("plugins") or {}
    enabled = plugins.get("enabled") or []
    return (
        engine.get("engine") == "magic-context",
        memory.get("provider") == "magic_context",
        PLUGIN_NAME in enabled,
    )


def _missing_wiring_labels(
    engine_ok: bool, provider_ok: bool, plugin_ok: bool
) -> list[str]:
    return [
        label
        for label, ok in (
            ("context.engine=magic-context", engine_ok),
            ("memory.provider=magic_context", provider_ok),
            ("plugins.enabled+=magic-hermes", plugin_ok),
        )
        if not ok
    ]


def shared_db_path() -> Path | None:
    override = os.environ.get("MAGIC_CONTEXT_DB_PATH")
    if override:
        return Path(override)
    candidate = Path.home() / SHARED_DB_RELPATH
    return candidate if candidate.exists() else None


def db_schema_lane(db_path: Path) -> int | None:
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            row = con.execute(
                "select max(version) from schema_migrations"
            ).fetchone()
        finally:
            con.close()
    except sqlite3.Error:
        return None
    return int(row[0]) if row and row[0] is not None else None


def run_doctor(json_output: bool = False) -> int:
    report = DoctorReport()
    tested = tested_magic_context_version()
    series = ".".join(map(str, supported_magic_context_series()))

    node = shutil.which("node")
    if node:
        try:
            version_out = subprocess.run(
                [node, "--version"], capture_output=True, text=True, timeout=15
            )
            node_version = version_out.stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            node_version = "unknown"
        report.add("PASS", f"Node {node_version} detected at {node}")
    else:
        report.add("FAIL", "Node.js runtime not found on PATH")

    from importlib import metadata

    try:
        own_version = metadata.version(PLUGIN_NAME)
        report.add("PASS", f"magic-hermes {own_version} is installed")
    except metadata.PackageNotFoundError:
        report.add(
            "INFO", "magic-hermes is running from source (no distribution metadata)"
        )
    except Exception:
        report.add(
            "WARN", "magic-hermes is running from source (no distribution metadata)"
        )

    installations = discover_installations()
    if installations:
        root, version = installations[0]
        where = "Hermes-managed" if managed_npm_root() in root.parents else "external"
        report.add(
            "PASS",
            f"{UPSTREAM_PACKAGE} {version} found at {root} ({where})",
        )
        if len(installations) > 1:
            others = ", ".join(f"{v or '?'} @ {r}" for r, v in installations[1:])
            report.add("INFO", f"Other copies discovered: {others}")
        if version == tested:
            report.add(
                "PASS",
                f"Upstream version matches the version validated by this build "
                f"(v{tested})",
            )
        else:
            current = _semver_tuple(version or "")
            wanted = _semver_tuple(tested)
            if current and wanted and current > wanted:
                report.add(
                    "WARN",
                    f"Upstream {version} is newer than the validated v{tested}; "
                    "the shared-DB schema fence follows the newest copy — "
                    "update magic-hermes if sessions fail to open the store",
                )
            else:
                report.add(
                    "INFO",
                    f"Upstream {version} differs from validated v{tested} "
                    f"(supported series {series}.x)",
                )
    else:
        report.add(
            "FAIL",
            f"No {UPSTREAM_PACKAGE} installation discovered — run "
            "`magic-hermes install`",
        )

    config_path = hermes_config_path()
    if config_path.is_file():
        document = parse_hermes_config(config_path)
        if document is None:
            # No YAML library available (source checkout without ruamel/PyYAML):
            # fall back to a conservative text scan rather than warning.
            wiring = config_wiring_from_text(config_path)
            if wiring == (True, True, True):
                report.add("PASS", f"Hermes config wired at {config_path}")
            elif wiring is None:
                report.add("FAIL", f"Hermes config unreadable at {config_path}")
            else:
                report.add(
                    "FAIL",
                    "Hermes config missing wiring: "
                    + ", ".join(_missing_wiring_labels(*wiring))
                    + " — run `magic-hermes install`",
                )
        else:
            wiring = _config_wiring_from_document(document)
            if all(wiring):
                report.add("PASS", f"Hermes config wired at {config_path}")
            else:
                report.add(
                    "FAIL",
                    "Hermes config missing wiring: "
                    + ", ".join(_missing_wiring_labels(*wiring))
                    + " — run `magic-hermes install`",
                )
    else:
        report.add(
            "FAIL", f"Hermes config not found at {config_path} — is Hermes set up?"
        )

    db_path = shared_db_path()
    if db_path:
        report.add("PASS", f"Shared context DB exists at {db_path}")
        lane = db_schema_lane(db_path)
        if lane is not None:
            report.add("INFO", f"Shared DB schema migration lane: v{lane}")
    else:
        report.add(
            "INFO",
            "No shared context DB yet — it is created on first successful "
            "runtime bind",
        )

    handshake: dict[str, Any] | None = None
    bridge: dict[str, Any] | None = None
    if node and installations:
        try:
            with RuntimeClient(timeout=60) as client:
                handshake = client.call("hello", timeout=60)
                # quick_check scans the entire shared DB; on a large store
                # with a cold page cache this can take minutes.
                bridge = client.call("doctor", timeout=300)
        except Exception as exc:
            report.add("FAIL", f"Magic Context sidecar failed: {exc}")
        if isinstance(bridge, dict):
            health = str(bridge.get("database_health", "unknown"))
            if health == "ok":
                report.add("PASS", "Sidecar opened the shared DB (quick_check ok)")
            elif health.startswith("error:"):
                report.add("FAIL", f"Shared DB health: {health}")
            else:
                report.add("WARN", f"Shared DB health reported as {health!r}")
            if bridge.get("core_symbols_ready"):
                series_value = bridge.get("supported_series")
                report.add(
                    "PASS",
                    f"Upstream core symbols present (series {series_value})",
                )
            else:
                report.add(
                    "FAIL",
                    "Upstream is missing core symbols: "
                    + ", ".join(bridge.get("missing_core_symbols") or []),
                )
        if isinstance(handshake, dict):
            report.add(
                "PASS",
                f"Sidecar handshake ok (harness={handshake.get('harness')}, "
                f"v{handshake.get('package_version')})",
            )

    if json_output:
        print(
            json.dumps(
                {
                    "checks": [c.__dict__ for c in report.checks],
                    "summary": {
                        "pass": report.passed,
                        "warn": report.warned,
                        "fail": report.failed,
                    },
                },
                indent=2,
            )
        )
    else:
        print("┌  magic-hermes doctor")
        for check in report.checks:
            print(f"│\n◆  {check.status} {check.message}")
        print(
            f"│\n│  Summary: PASS {report.passed} / WARN {report.warned} "
            f"/ FAIL {report.failed}\n│"
        )
        print("└  Doctor complete")
    return 1 if report.failed else 0


# ---------------------------------------------------------------------------
# install command
# ---------------------------------------------------------------------------


def parse_hermes_config(path: Path) -> dict[str, Any] | None:
    """Parse the Hermes config, returning None when no YAML lib is present.

    Doctor treats None as "cannot structurally parse" and falls back to a
    conservative text scan; install hard-fails on the same condition because
    it must rewrite the file.
    """

    try:
        document, _ = _load_yaml(path)
    except ImportError:
        return None
    return document if isinstance(document, dict) else None


def config_wiring_from_text(path: Path) -> tuple[bool, bool, bool] | None:
    """Scan config text for the three wiring keys without a YAML parser.

    Handles the common shapes (nested key under a section, list item under
    plugins.enabled). Returns None when the file cannot be read.
    """

    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None

    def section_value(section: str, key: str) -> str | None:
        pattern = re.compile(
            rf"^\s*{re.escape(section)}\s*:\s*\n((?:[ \t]+.*\n?)*)", re.MULTILINE
        )
        block = pattern.search(text)
        if block is None:
            return None
        for line in block.group(1).splitlines():
            match = re.match(rf"^\s+{re.escape(key)}\s*:\s*['\"]?([^'\"\n#]+)", line)
            if match:
                return match.group(1).strip()
        return None

    engine = section_value("context", "engine")
    provider = section_value("memory", "provider")
    plugin_line = re.compile(r"^\s*-\s*['\"]?magic-hermes['\"]?\s*$")
    in_plugins = any(plugin_line.match(line) for line in text.splitlines())
    return (
        engine == "magic-context",
        provider == "magic_context",
        in_plugins,
    )


def run_install(
    version: str | None = None,
    package_root: Path | None = None,
    dry_run: bool = False,
    skip_config: bool = False,
) -> int:
    target = version or tested_magic_context_version()
    latest = npm_latest_version()
    plan = plan_install(target, package_root=package_root, npm_latest=latest)
    print(f"┌  magic-hermes install (target {UPSTREAM_PACKAGE}@{target})")
    for note in plan.notes:
        print(f"│  {note}")
    if latest:
        print(f"│  npm latest: {latest}")
    else:
        print("│  npm latest: unknown (offline or npm unavailable)")

    if dry_run:
        print(f"│\n│  Dry run — action: {plan.action}")
        print("└  install (dry run)")
        return 0

    if plan.action in {"install", "update"} and package_root is None:
        root = managed_npm_root()
        if root.exists() and not any(root.glob("node_modules")):
            pass  # fresh root, nothing to preserve
        run_npm_install(root, target)
        installed = (
            root / "node_modules" / "@cortexkit" / "pi-magic-context"
        )
        got = _package_version(installed)
        if got != target:
            print(f"│  npm installed {got!r}, expected {target!r}", file=sys.stderr)
            return 1
        print(f"│  installed {UPSTREAM_PACKAGE}@{got} into {root}")

    if not skip_config:
        changed = configure_hermes()
        if changed:
            print(f"│  Hermes config updated: {', '.join(changed)} (backup written)")
        else:
            print("│  Hermes config already wired")

    # End-to-end verification: the sidecar must boot and open the shared DB.
    try:
        with RuntimeClient(timeout=90) as client:
            hello = client.call("hello", timeout=90)
            print(
                f"│  verified sidecar: {UPSTREAM_PACKAGE} "
                f"v{hello.get('package_version')} (harness={hello.get('harness')})"
            )
    except Exception as exc:
        print(f"│  sidecar verification FAILED: {exc}", file=sys.stderr)
        print("└  install failed verification")
        return 1

    print("│")
    print("│  Restart Hermes (gateway or CLI) to activate the plugin.")
    print("└  install complete")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="magic-hermes",
        description="Installer and doctor for the Hermes Magic Context plugin",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    install = sub.add_parser(
        "install", help="install/update Magic Context and wire Hermes config"
    )
    install.add_argument("--version", help="upstream version to install")
    install.add_argument(
        "--package-root",
        type=Path,
        help="use an existing package root instead of managing npm",
    )
    install.add_argument(
        "--dry-run", action="store_true", help="show the plan without changing anything"
    )
    install.add_argument(
        "--skip-config", action="store_true", help="leave Hermes config untouched"
    )

    doctor = sub.add_parser("doctor", help="check installation health")
    doctor.add_argument("--json", action="store_true", help="machine-readable output")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "install":
        return run_install(
            version=args.version,
            package_root=args.package_root,
            dry_run=args.dry_run,
            skip_config=args.skip_config,
        )
    return run_doctor(json_output=args.json)


if __name__ == "__main__":
    raise SystemExit(main())
