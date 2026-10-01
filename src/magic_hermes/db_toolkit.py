"""Shared-store migration toolkit for `magic-hermes db`.

The 2026-10-01 v0.3.8 deployment showed the recurring failure mode this
module exists to absorb: upstream releases bump the schema fence, the
store migrates lazily on the next harness boot, and that boot is refused
while any live Pi/OMP/OpenCode harness still runs the OLD plugin build
(``storage fatal: refusing to migrate ... while confirmed Pi harness
PID N still uses the old plugin build``). The manual recovery — lsof
archaeology, per-holder build detection, guessing which sidecars are
stale — is exactly what ``magic-hermes db`` automates:

  status    read-only: store path, persisted vs package fence version,
            pending migration distance, and the live old-build holders
  blockers  read-only: every process holding the store or the rpc
            discovery tree, classified (Pi harness / OpenCode server /
            bridge / other), with age and its resolved package build
  migrate   drive the sanctioned upstream migration path via a one-shot
            Node driver (bridge/migrate.mjs) that calls upstream's
            ``openDatabase()`` — the same fence + holder guard +
            ``runMigrations()`` a real harness boot runs — draining
            old-build holders between attempts when they block.

Design rules:
  * Never touch the store with raw SQL from here (the historian guard
    module owns trigger DDL; migrations belong to upstream).
  * Holder classification mirrors upstream's ``commandHasPiHarnessArc``
    markers (``commandLooksLikePiImage``) so the utility names the same
    blockers upstream's guard would.
  * Every mutating action is fail-loud with receipts: the one-shot
    driver's JSON is always surfaced, exit codes never hide failure.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import signal
import sqlite3
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .runtime import _package_version, find_magic_context_package

# Mirrors PI_HARNESS_ARC_MARKERS in upstream's storage core. Keep in
# sync when adopting a release whose harness-detection markers change.
PI_HARNESS_ARC_MARKERS = (
    "pi-coding-agent",
    "oh-my-pi",
    "@oh-my-pi",
    "cljs/dist",
    "dist/bundle/cli",
)

# Executables whose argv[0] qualifies as a Pi-family harness image when
# combined with an arc marker (upstream: commandHasPiHarnessArc).
_PI_FIRST_TOKENS = frozenset({"pi", "omp", "oh-my-pi", "node", "bun", "deno", "cmd"})
_PI_SCRIPT_NAMES = frozenset({"pi", "pi.js", "pi.mjs", "pi.cjs"})
# Upstream PI_IMAGE_NAMES: a bare argv[0] match is a Pi-family image
# outright (commandLooksLikePiImage) — display-level classification.
_PI_IMAGE_NAMES = frozenset({"pi", "pi.cmd", "omp", "oh-my-pi"})

# Exit contract of bridge/migrate.mjs.
_MIGRATE_EXIT_OPENED = 0  # store opened; migration ran or was a no-op
_MIGRATE_EXIT_REFUSED = 3  # holder guard or fence refused (blockers named)
_MIGRATE_EXIT_ERROR = 1  # driver/package failure


def _tokens(command: str) -> list[str]:
    # Lightweight shell-ish tokenizer: upstream uses a real shell parser
    # but marker detection only needs token boundaries and quotes.
    tokens: list[str] = []
    current = ""
    quote: str | None = None
    for char in command:
        if quote is not None:
            if char == quote:
                quote = None
            else:
                current += char
        elif char in {'"', "'"}:
            quote = char
        elif char.isspace():
            if current:
                tokens.append(current)
                current = ""
        else:
            current += char
    if current:
        tokens.append(current)
    return tokens


def _executable_name(token: str) -> str:
    return token.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]


def command_looks_like_pi_harness(command: str) -> bool:
    """Mirror upstream's ``commandHasPiHarnessArc`` classification."""

    normalized = command.strip().lower().replace("\\", "/").replace("\0", " ")
    if not normalized:
        return False
    tokens = _tokens(command)
    if not tokens:
        return False
    has_arc = any(marker in normalized for marker in PI_HARNESS_ARC_MARKERS)
    first = _executable_name(tokens[0])
    if "/" in tokens[0] or "\\" in tokens[0]:
        first = _executable_name(tokens[0])
    first = re.sub(r"\.(?:exe|cmd)$", "", first)
    if has_arc and first in _PI_FIRST_TOKENS:
        return True
    if has_arc and any(marker in tokens[0] for marker in PI_HARNESS_ARC_MARKERS):
        return True
    if first in {"node", "bun", "deno"} and len(tokens) > 1:
        script = re.sub(r"\.(?:exe|cmd)$", "", _executable_name(tokens[1]))
        if script in _PI_SCRIPT_NAMES:
            return True
        if has_arc:
            return True
    return False


def command_looks_like_pi_image(command: str) -> bool:
    """Mirror upstream's ``commandLooksLikePiImage`` classification."""

    tokens = _tokens(command)
    if not tokens:
        return False
    first = re.sub(r"\.(?:exe|cmd)$", "", _executable_name(tokens[0]))
    return first in _PI_IMAGE_NAMES


@dataclass
class HolderInfo:
    pid: int
    command: str
    kind: str  # "pi_harness" | "opencode_server" | "bridge" | "other"
    status: str = "live"
    age_s: float | None = None
    package_version: str | None = None
    package_root: str | None = None
    hints: list[str] = field(default_factory=list)


@dataclass
class StoreScan:
    db_path: Path
    exists: bool
    persisted_version: int | None
    package_root: Path | None
    package_version: str | None
    latest_supported_version: int | None
    pending_migrations: int | None
    fence_ok: bool | None
    error: str | None = None


def store_path() -> Path:
    """Resolve the shared store path (env override honoured)."""

    override = os.environ.get("MAGIC_CONTEXT_DB_PATH")
    if override:
        return Path(override)
    data_home = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(data_home) / "cortexkit" / "magic-context" / "context.db"


def scan_store(package_root: Path | None = None) -> StoreScan:
    """Read-only scan of the store's fence state vs the resolved package."""

    root = package_root or find_magic_context_package()
    db = store_path()
    scan = StoreScan(
        db_path=db,
        exists=db.exists(),
        persisted_version=None,
        package_root=root,
        package_version=_package_version(root) if root else None,
        latest_supported_version=None,
        pending_migrations=None,
        fence_ok=None,
    )
    if not db.exists():
        return scan
    if root is None:
        scan.error = "no upstream package found"
        return scan

    # Fence version without booting the bridge: the one-shot driver in
    # --probe mode reports latest_supported_version cheaply. But we can
    # also read it directly: LATEST_SUPPORTED_VERSION is a constant in
    # the chunk source; scanning for it is release-stable.
    latest = _read_latest_supported_version(root)
    scan.latest_supported_version = latest
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5.0)
        try:
            row = con.execute("select max(version) from schema_migrations").fetchone()
        finally:
            con.close()
    except sqlite3.Error as exc:
        scan.error = f"store unreadable: {exc}"
        return scan
    scan.persisted_version = int(row[0]) if row and row[0] is not None else None
    if latest is not None and scan.persisted_version is not None:
        scan.pending_migrations = latest - scan.persisted_version
        scan.fence_ok = scan.pending_migrations <= 0
    return scan


def _read_latest_supported_version(package_root: Path) -> int | None:
    """Read LATEST_SUPPORTED_VERSION from the storage chunk source.

    The constant is declared once (``var LATEST_SUPPORTED_VERSION = N;``)
    in the chunk that owns openDatabase(). Reading it from source keeps
    ``status`` side-effect-free (no node process, no guard evaluation).
    """

    dist = package_root / "dist"
    try:
        names = sorted(dist.iterdir())
    except OSError:
        return None
    pattern = re.compile(
        r"var\s+LATEST_SUPPORTED_VERSION\s*=\s*(\d+)\s*;"
    )
    for entry in names:
        if not (entry.name.startswith("index-") and entry.name.endswith(".js")):
            continue
        try:
            text = entry.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "function openDatabase(" not in text:
            continue
        match = pattern.search(text)
        if match:
            return int(match.group(1))
    return None


def _proc_age_s(pid: int) -> float | None:
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().split()
        starttime = int(fields[21])
    except (OSError, ValueError, IndexError):
        return None
    hz = os.sysconf("SC_CLK_TCK")
    boot = time.clock_gettime(time.CLOCK_BOOTTIME)
    return max(0.0, boot - starttime / hz)


def _holder_command(pid: int) -> str | None:
    try:
        return Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(
            "utf-8", "replace"
        ).strip()
    except OSError:
        return None


def _resolved_package(pid: int) -> tuple[str | None, str | None]:
    """Best-effort: which pi-magic-context build does the PID run?"""

    # 1) Env override (bridges spawned with an explicit package root).
    try:
        env = Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
    except OSError:
        env = []
    for entry in env:
        if entry.startswith(b"MAGIC_CONTEXT_PACKAGE_ROOT="):
            root = entry.split(b"=", 1)[1].decode("utf-8", "replace")
            pkg = Path(root)
            return str(pkg), _package_version(pkg) if pkg.exists() else None
    # 2) Loaded-module attribution: node mmaps the package's dist chunks,
    # so /proc/<pid>/maps names the build the process actually loaded.
    try:
        maps = Path(f"/proc/{pid}/maps").read_text(encoding="utf-8", errors="replace")
    except OSError:
        maps = ""
    match = re.search(r"/([^\s]+pi-magic-context)/dist/", maps)
    if match:
        pkg = Path("/" + match.group(1))
        version = _package_version(pkg) if pkg.exists() else None
        return str(pkg), version
    # 3) Command-line path attribution (node <script> inside a package).
    command = _holder_command(pid) or ""
    cmdline_match = re.search(r"(/[^\s]+pi-magic-context)/", command)
    if cmdline_match:
        pkg = Path(cmdline_match.group(1))
        return str(pkg), _package_version(pkg) if pkg.exists() else None
    return None, None


def _lsof_pids(targets: list[Path]) -> list[int]:
    """PIDs holding any target file, via lsof -t (empty on failure)."""

    existing = [t for t in targets if t.exists()]
    if not existing:
        return []
    try:
        result = subprocess.run(
            ["lsof", "-t", *map(str, existing)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    pids: list[int] = []
    for token in (result.stdout or "").split():
        try:
            pids.append(int(token))
        except ValueError:
            continue
    return pids


def _rpc_port_pids(rpc_root: Path) -> list[int]:
    """Live PIDs named by rpc port files (upstream's discovery format).

    Port files live at ``rpc/<project>/port[-<pid>].json`` holding
    ``{"port": N, "pid": M, ...}``; upstream's guard treats those PIDs
    as confirmed OpenCode servers when live. We mirror the JSON shape
    (and the port-<pid> filename fallback) without binding sockets.
    """

    if not rpc_root.is_dir():
        return []
    pids: list[int] = []
    for port_file in sorted(rpc_root.glob("*/port*")):
        if port_file.name != "port" and not (
            port_file.name.startswith("port-") and port_file.name.endswith(".json")
        ):
            continue
        raw: str | None = None
        try:
            raw = port_file.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            continue
        if not raw or not raw.startswith("{"):
            continue
        try:
            record = json.loads(raw)
        except json.JSONDecodeError:
            continue
        pid = record.get("pid")
        if isinstance(pid, int) and pid > 0 and Path(f"/proc/{pid}").exists():
            pids.append(pid)
    return pids


def _holder_invoker(pid: int) -> str:
    """Deprecated alias kept for one release; unused."""

    return _holder_command(pid) or ""


def _bridge_build(script_path: str) -> tuple[str | None, str | None]:
    """Attribute a hermes bridge to its magic-hermes wheel + tested upstream.

    The bridge command names the installed runtime.mjs; the surrounding
    package carries magic_context_compat.json (tested upstream version)
    and a *.dist-info dir (wheel version).
    """

    pkg_dir = Path(script_path).resolve().parents[1]  # .../magic_hermes
    compat = pkg_dir / "magic_context_compat.json"
    upstream: str | None = None
    with contextlib.suppress(OSError, ValueError, KeyError):
        upstream = str(json.loads(compat.read_text(encoding="utf-8"))["tested_version"])
    wheel: str | None = None
    site = pkg_dir.parent
    try:
        dist_info = sorted(site.glob("magic_hermes-*.dist-info"))
        wheel = (
            dist_info[0].name.removesuffix(".dist-info").split("-")[1]
            if dist_info
            else None
        )
    except OSError:
        pass
    label = f"magic-hermes {wheel}" if wheel else "magic-hermes (wheel unknown)"
    return label, upstream


def _pi_standard_root_version() -> str | None:
    """Version at pi's standard extension root, if installed."""

    root = Path.home() / ".pi" / "agent" / "npm" / "node_modules"
    pkg = root / "@cortexkit" / "pi-magic-context"
    try:
        raw = json.loads((pkg / "package.json").read_text(encoding="utf-8"))
        return str(raw["version"])
    except (OSError, ValueError, KeyError):
        return None


def list_holders(db_path: Path) -> list[HolderInfo]:
    """Every live process holding the store or the rpc discovery tree.

    Mirrors the union upstream's guard considers: lsof on the db (plus
    WAL/SHM siblings) and the rpc/port-file server pids. Classified with
    the same arc-marker logic so the utility names the same blockers.
    """

    holders: list[HolderInfo] = []
    seen: set[int] = set()
    targets = [db_path, Path(f"{db_path}-wal"), Path(f"{db_path}-shm")]
    pids: list[int] = []
    # Primary: lsof on the store (+ WAL/SHM siblings). Secondary: the
    # rpc discovery tree — upstream's guard names port-file PIDs as
    # blockers, and lsof may miss processes that only hold those.
    lsof_pids = _lsof_pids(targets)
    port_pids = _rpc_port_pids(db_path.parent / "rpc")
    for pid in [*lsof_pids, *port_pids]:
        if pid not in seen:
            seen.add(pid)
            pids.append(pid)

    for pid in pids:
        command = _holder_command(pid)
        if command is None:
            continue
        kind = "other"
        hints: list[str] = []
        package_version: str | None = None
        package_root: str | None = None
        if command_looks_like_pi_harness(command) or command_looks_like_pi_image(
            command
        ):
            kind = "pi_harness"
            # A bare `pi` binary resolves its extension at runtime from
            # the standard root; attribute that root's build.
            package_version = _pi_standard_root_version()
            package_root = str(
                Path.home() / ".pi" / "agent" / "npm" / "node_modules"
                / "@cortexkit" / "pi-magic-context"
            )
        elif "opencode" in command.lower():
            kind = "opencode_server"
        elif "runtime.mjs" in command:
            script = command.split()[-1] if command.split() else ""
            kind = "bridge"
            label, upstream = _bridge_build(script)
            if label:
                hints.append(label)
            package_version = upstream
            package_root = script
        root, version = _resolved_package(pid)
        if root is not None:
            package_root = root
            package_version = package_version or version
        if package_version:
            hints.append(f"runs pi-magic-context {package_version}")
        holders.append(
            HolderInfo(
                pid=pid,
                command=command,
                kind=kind,
                status="live",
                age_s=_proc_age_s(pid),
                package_version=package_version,
                package_root=package_root,
                hints=hints,
            )
        )
    return holders


def format_status(scan: StoreScan, holders: list[HolderInfo]) -> str:
    lines: list[str] = []
    lines.append("┌  magic-hermes db status")
    lines.append(f"│  store: {scan.db_path}")
    if not scan.exists:
        lines.append("│  store does not exist yet (created on first bind)")
        lines.append("└  nothing to migrate")
        return "\n".join(lines)
    lines.append(f"│  persisted schema version: v{scan.persisted_version}")
    if scan.package_root is not None:
        lines.append(
            f"│  package: {scan.package_root} "
            f"({scan.package_version or 'version unknown'})"
        )
    else:
        lines.append("│  package: none found — set MAGIC_CONTEXT_PACKAGE_ROOT")
    if scan.latest_supported_version is not None:
        lines.append(
            f"│  package fence: v{scan.latest_supported_version}"
        )
    if scan.pending_migrations is not None:
        if scan.pending_migrations <= 0:
            lines.append(f"│  fence: OK (at v{scan.persisted_version})")
        else:
            lines.append(
                f"│  PENDING: {scan.pending_migrations} migration(s) "
                f"(v{scan.persisted_version} → v{scan.latest_supported_version})"
            )
    if scan.error:
        lines.append(f"│  warning: {scan.error}")
    if holders:
        lines.append(f"│  live holders: {len(holders)}")
        for holder in holders:
            age = f" age {int(holder.age_s)}s" if holder.age_s is not None else ""
            build = f" [{holder.hints[0]}]" if holder.hints else ""
            lines.append(
                f"│    pid {holder.pid}: {holder.kind}{age}{build}"
            )
    else:
        lines.append("│  live holders: none")
    lines.append("└  db status complete")
    return "\n".join(lines)


def run_db_status(json_output: bool = False) -> int:
    scan = scan_store()
    holders = list_holders(scan.db_path) if scan.exists else []
    if json_output:
        print(
            json.dumps(
                {
                    "store": str(scan.db_path),
                    "exists": scan.exists,
                    "persisted_version": scan.persisted_version,
                    "package_root": str(scan.package_root)
                    if scan.package_root
                    else None,
                    "package_version": scan.package_version,
                    "latest_supported_version": scan.latest_supported_version,
                    "pending_migrations": scan.pending_migrations,
                    "fence_ok": scan.fence_ok,
                    "error": scan.error,
                    "holders": [
                        {
                            "pid": h.pid,
                            "kind": h.kind,
                            "command": h.command,
                            "age_s": h.age_s,
                            "package_version": h.package_version,
                        }
                        for h in holders
                    ],
                },
                indent=2,
            )
        )
        return 0
    print(format_status(scan, holders))
    return 0


def run_db_blockers(json_output: bool = False) -> int:
    db = store_path()
    if not db.exists():
        if json_output:
            print(json.dumps({"store": str(db), "exists": False, "holders": []}))
        else:
            print(f"No store at {db} — nothing holds it")
        return 0
    holders = list_holders(db)
    if json_output:
        print(
            json.dumps(
                {
                    "mode": "blockers",
                    "store": str(db),
                    "holders": [
                        {
                            "pid": h.pid,
                            "kind": h.kind,
                            "command": h.command,
                            "age_s": h.age_s,
                            "package_version": h.package_version,
                            "package_root": h.package_root,
                        }
                        for h in holders
                    ],
                },
                indent=2,
            )
        )
        return 0
    print("┌  magic-hermes db blockers")
    print(f"│  store: {db}")
    if not holders:
        print("│  no live holders — migration is unobstructed")
        print("└  db blockers complete")
        return 0
    for holder in holders:
        age = f"  age {int(holder.age_s)}s" if holder.age_s is not None else ""
        print(f"│  pid {holder.pid}  {holder.kind}{age}")
        print(f"│    command: {holder.command}")
        if holder.package_version:
            print(f"│    runs pi-magic-context {holder.package_version}")
        if holder.package_root:
            print(f"│    package: {holder.package_root}")
    print("└  db blockers complete")
    return 0


_MIGRATE_USAGE = """\
usage: magic-hermes db migrate [--wait SECONDS] [--kill-blockers] [--json]

Drives the sanctioned upstream migration path (openDatabase) with a
one-shot Node driver. Default: single attempt, fail-loud on refusal
with the blocker PIDs named. --wait loops with drain checks until the
store reaches the package fence or the budget expires. --kill-blockers
adds old-build holders to the drain set (SIGTERM, then SIGKILL after a
grace period) — never hermes bridges on the current build, never PIDs
we cannot classify.
"""


def _run_migrate_driver(
    db: Path,
    package_root: Path | None,
    timeout: float = 300.0,
) -> tuple[int, dict[str, Any]]:
    """Run bridge/migrate.mjs once; return (exit_code, parsed_json)."""

    script = Path(__file__).parent / "bridge" / "migrate.mjs"
    cmd = ["node", "--no-warnings", str(script), "--db", str(db)]
    root = package_root or find_magic_context_package()
    if root is not None:
        cmd += ["--package-root", str(root)]
    env = dict(os.environ)
    env.pop("MAGIC_CONTEXT_PACKAGE_ROOT", None)  # explicit root wins
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return 1, {"action": "error", "error": f"driver timed out after {timeout}s"}
    except OSError as exc:
        return 1, {"action": "error", "error": f"could not run driver: {exc}"}
    payload: dict[str, Any] = {}
    for line in (result.stdout or "").splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
    return result.returncode, payload


def _terminate_holder(holder: HolderInfo, grace_s: float = 10.0) -> bool:
    """SIGTERM a blocking holder; SIGKILL after grace. Returns efficacy."""

    try:
        os.kill(holder.pid, signal.SIGTERM)
    except OSError:
        return False
    deadline = time.monotonic() + grace_s
    while time.monotonic() < deadline:
        if not Path(f"/proc/{holder.pid}").exists():
            return True
        time.sleep(0.5)
    with contextlib.suppress(OSError):
        os.kill(holder.pid, signal.SIGKILL)
    return not Path(f"/proc/{holder.pid}").exists()


def run_db_migrate(
    wait_s: float | None = None,
    kill_blockers: bool = False,
    json_output: bool = False,
) -> int:
    db = store_path()
    root = find_magic_context_package()
    log_lines: list[str] = []

    def log(message: str) -> None:
        log_lines.append(message)
        if not json_output:
            print(message)

    log("┌  magic-hermes db migrate")
    log(f"│  store: {db}")
    if root is None:
        log("└  no upstream package found — set MAGIC_CONTEXT_PACKAGE_ROOT")
        return 1
    log(f"│  package: {root} ({_package_version(root)})")
    if not db.exists():
        log("└  store does not exist — nothing to migrate")
        return 0

    scan = scan_store(root)
    if scan.pending_migrations is not None and scan.pending_migrations <= 0:
        log(
            f"│  fence already satisfied (v{scan.persisted_version} ≥ "
            f"v{scan.latest_supported_version}) — no-op"
        )
        log("└  db migrate complete")
        return 0

    deadline = None if wait_s is None else time.monotonic() + wait_s
    attempt = 0
    while True:
        attempt += 1
        code, payload = _run_migrate_driver(db, root)
        if code == _MIGRATE_EXIT_OPENED:
            persisted = payload.get("persisted_version")
            latest = payload.get("latest_supported_version")
            log(
                f"│  attempt {attempt}: migration ran — store now at "
                f"v{persisted} (fence v{latest})"
            )
            log("└  db migrate complete")
            return 0
        if code == _MIGRATE_EXIT_ERROR:
            log(f"│  attempt {attempt}: driver error: {payload.get('error')}")
            log("└  db migrate FAILED")
            return 1
        # refused: name the blockers
        blockers = payload.get("blockers") or []
        persisted = payload.get("persisted_version")
        supported = payload.get("supported_version")
        log(
            f"│  attempt {attempt}: refused — store at v{persisted}, fence "
            f"v{supported}, {len(blockers)} blocking holder(s): "
            f"{', '.join(str(pid) for pid in blockers) or 'none named'}"
        )
        if kill_blockers and blockers:
            holders = list_holders(db)
            by_pid = {h.pid: h for h in holders}
            drained_any = False
            for pid in blockers:
                holder = by_pid.get(pid)
                if holder is None:
                    log(f"│    pid {pid}: not re-found in holder scan — skip")
                    continue
                if holder.kind == "bridge":
                    log(
                        f"│    pid {pid}: magic-hermes bridge — SIGTERM "
                        "(respawns lazily on next runtime bind)"
                    )
                if not _terminate_holder(holder):
                    log(f"│    pid {pid}: termination FAILED")
                    continue
                drained_any = True
                log(f"│    pid {pid}: terminated")
            if drained_any:
                # A drain only helps if we retry the migration on the
                # next loop pass (with or without --wait).
                continue
            log("└  db migrate refused (no blocker could be drained)")
            return 2
        elif blockers:
            log("│  next: restart/stop the blocking harness(es), then retry")
            log("│        or re-run with --kill-blockers --wait 300")
            log("└  db migrate refused (blockers live)")
            return 2
        if deadline is None:
            log("└  db migrate refused (blockers live, no --wait)")
            return 2
        if time.monotonic() >= deadline:
            log(f"└  db migrate timed out after {wait_s}s — blockers still live")
            return 2
        time.sleep(5.0)


def main_db(argv: list[str]) -> int:
    """Entry for `magic-hermes db <action> [options]`."""

    if not argv:
        print(_MIGRATE_USAGE)
        return 1
    action = argv[0]
    rest = argv[1:]
    json_output = "--json" in rest
    if action == "status":
        return run_db_status(json_output=json_output)
    wait_s: float | None = None
    for i, token in enumerate(rest):
        if token == "--wait" and i + 1 < len(rest):
            with contextlib.suppress(ValueError):
                wait_s = float(rest[i + 1])
    kill_blockers = "--kill-blockers" in rest
    if action == "blockers":
        return run_db_blockers(json_output=json_output)
    if action == "migrate":
        return run_db_migrate(
            wait_s=wait_s,
            kill_blockers=kill_blockers,
            json_output=json_output,
        )
    print(f"unknown db action: {action}")
    print(_MIGRATE_USAGE)
    return 1
