"""Process-safe client for the packaged upstream Magic Context adapter."""

from __future__ import annotations

import contextvars
import importlib.resources
import json
import logging
import os
import re
import select
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_SUPPORTED_MAGIC_CONTEXT_SERIES = (0, 38)
_SEMVER = re.compile(
    r"^(0|[1-9][0-9]*)[.](0|[1-9][0-9]*)[.](0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:[+][0-9A-Za-z.-]+)?$"
)


def _package_version(package_root: Path) -> str | None:
    try:
        payload = json.loads(
            (package_root / "package.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return None
    version = payload.get("version") if isinstance(payload, dict) else None
    return str(version) if version else None


def _is_supported_version(version: str | None) -> bool:
    if not version:
        return False
    match = _SEMVER.fullmatch(version)
    if match is None:
        return False
    return tuple(map(int, match.groups()[:2])) == _SUPPORTED_MAGIC_CONTEXT_SERIES


class RuntimeErrorBase(RuntimeError):
    """Base error raised by the Magic Context runtime client."""


class RuntimeUnavailable(RuntimeErrorBase):
    """The packaged Node runtime or upstream Magic Context is unavailable."""


class RuntimeProtocolError(RuntimeErrorBase):
    """The runtime returned an invalid or failed response."""


def runtime_script_path() -> Path:
    """Return the installed path to the packaged Node sidecar."""

    resource = importlib.resources.files("magic_hermes.bridge").joinpath("runtime.mjs")
    return Path(str(resource))


def magic_context_package_candidates() -> list[Path]:
    """Return local upstream package locations without probing the network."""

    candidates: list[Path] = []
    configured = os.environ.get("MAGIC_CONTEXT_PACKAGE_ROOT")
    if configured:
        candidates.append(Path(configured).expanduser())

    home = Path.home()
    candidates.extend(
        [
            home
            / ".pi"
            / "agent"
            / "npm"
            / "node_modules"
            / "@cortexkit"
            / "pi-magic-context",
            home
            / ".config"
            / "opencode"
            / "node_modules"
            / "@cortexkit"
            / "pi-magic-context",
        ]
    )

    for base in (Path.cwd(), runtime_script_path().parent):
        for parent in (base, *base.parents):
            candidates.append(
                parent / "node_modules" / "@cortexkit" / "pi-magic-context"
            )

    unique: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve(strict=False)
        if resolved not in seen:
            seen.add(resolved)
            unique.append(resolved)
    return unique


def find_magic_context_package() -> Path | None:
    """Locate an installed upstream Pi package using filesystem checks only."""

    for candidate in magic_context_package_candidates():
        if (candidate / "package.json").is_file() and (
            candidate / "dist" / "index.js"
        ).is_file():
            return candidate
    return None


def runtime_available(
    package_root: str | os.PathLike[str] | None = None,
) -> bool:
    """Return whether Node and the requested upstream package are available."""

    if package_root is None:
        resolved_root = find_magic_context_package()
    else:
        resolved_root = Path(package_root).expanduser().resolve(strict=False)
        if not (
            (resolved_root / "package.json").is_file()
            and (resolved_root / "dist" / "index.js").is_file()
        ):
            resolved_root = None
    return (
        shutil.which("node") is not None
        and runtime_script_path().is_file()
        and resolved_root is not None
        and _is_supported_version(_package_version(resolved_root))
    )


def runtime_unavailable_reason(
    package_root: str | os.PathLike[str] | None = None,
) -> str:
    """Return an actionable local-only availability diagnostic."""

    if shutil.which("node") is None:
        return "Node.js is not installed or not on PATH."
    if not runtime_script_path().is_file():
        return "The magic-hermes Node runtime is missing from the installation."
    if package_root is None:
        resolved_root = find_magic_context_package()
        if resolved_root is None:
            return (
                "@cortexkit/pi-magic-context is not installed. Install the Pi Magic "
                "Context package or set MAGIC_CONTEXT_PACKAGE_ROOT."
            )
    else:
        resolved_root = Path(package_root).expanduser().resolve(strict=False)
        if not (
            (resolved_root / "package.json").is_file()
            and (resolved_root / "dist" / "index.js").is_file()
        ):
            return f"@cortexkit/pi-magic-context was not found at {resolved_root}."
    version = _package_version(resolved_root)
    if not _is_supported_version(version):
        found = version or "an unreadable version"
        return (
            f"@cortexkit/pi-magic-context {found} is unsupported; "
            "magic-hermes requires the 0.38.x series."
        )
    return ""


HostCallback = Callable[[str, dict[str, Any]], Any]


class RuntimeClient:
    """Serialize JSON-line calls to one lazily-started Node process.

    Calls are never replayed. If a process fails after a request is written,
    the caller receives an error instead of risking duplicate mutation.
    """

    def __init__(
        self,
        *,
        package_root: str | os.PathLike[str] | None = None,
        db_path: str | os.PathLike[str] | None = None,
        timeout: float = 130.0,
        callback_handler: HostCallback | None = None,
    ) -> None:
        self.package_root = Path(package_root).resolve() if package_root else None
        self.db_path = Path(db_path).resolve() if db_path else None
        self.timeout = timeout
        self.callback_handler = callback_handler
        self._lock = threading.Lock()
        # Host callbacks may execute for minutes (Dreamer child agents).  Keep
        # stdin writes independently serialized so the main reader can accept a
        # second callback (for example abort) while the first callback is still
        # running on another thread.
        self._stdin_lock = threading.Lock()
        self._callback_threads_lock = threading.Lock()
        self._callback_threads: set[threading.Thread] = set()
        self._process: subprocess.Popen[str] | None = None
        self._next_id = 1
        self._stderr_thread: threading.Thread | None = None

    def __deepcopy__(self, memo: dict[int, Any]) -> RuntimeClient:
        """Return a disconnected client; process handles and locks are not copied."""

        copied = type(self)(
            package_root=self.package_root,
            db_path=self.db_path,
            timeout=self.timeout,
            callback_handler=self.callback_handler,
        )
        memo[id(self)] = copied
        return copied

    def _start(self) -> subprocess.Popen[str]:
        reason = runtime_unavailable_reason(self.package_root)
        if reason:
            raise RuntimeUnavailable(reason)

        script = runtime_script_path()
        if not script.is_file():
            raise RuntimeUnavailable(f"Runtime script is missing: {script}")

        environment = os.environ.copy()
        if self.package_root is not None:
            environment["MAGIC_CONTEXT_PACKAGE_ROOT"] = str(self.package_root)
        if self.db_path is not None:
            environment["MAGIC_CONTEXT_DB_PATH"] = str(self.db_path)

        try:
            process = subprocess.Popen(
                ["node", "--no-warnings", str(script)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                bufsize=1,
                env=environment,
            )
        except OSError as exc:
            message = f"Could not start Magic Context runtime: {exc}"
            raise RuntimeUnavailable(message) from exc

        self._process = process
        self._stderr_thread = threading.Thread(
            target=self._drain_stderr,
            args=(process,),
            name="magic-hermes-runtime-stderr",
            daemon=True,
        )
        self._stderr_thread.start()
        return process

    @staticmethod
    def _drain_stderr(process: subprocess.Popen[str]) -> None:
        stream = process.stderr
        if stream is None:
            return
        for line in stream:
            message = line.rstrip()
            if message:
                log.debug("Magic Context runtime: %s", message)

    def _ensure_process(self) -> subprocess.Popen[str]:
        process = self._process
        if process is None or process.poll() is not None:
            if process is not None:
                self._dispose(process)
            process = self._start()
        return process

    def _write_payload(
        self,
        process: subprocess.Popen[str],
        payload: dict[str, Any],
        *,
        context: str,
    ) -> None:
        if process.stdin is None:
            raise RuntimeProtocolError("Runtime stdin is unavailable")
        try:
            with self._stdin_lock:
                process.stdin.write(json.dumps(payload, separators=(",", ":")) + "\n")
                process.stdin.flush()
        except (BrokenPipeError, OSError, ValueError) as exc:
            self._dispose(process)
            raise RuntimeProtocolError(
                f"Runtime failed while {context}; request was not replayed"
            ) from exc

    def _dispatch_host_callback(
        self,
        process: subprocess.Popen[str],
        callback_id: Any,
        callback_method: str,
        callback_params: dict[str, Any],
    ) -> None:
        current = threading.current_thread()
        reply: dict[str, Any] = {
            "type": "host_callback_result",
            "callback_id": callback_id,
        }
        try:
            if self.callback_handler is None:
                raise RuntimeError(f"No host callback handler for {callback_method}")
            reply["result"] = self.callback_handler(callback_method, callback_params)
        except Exception as exc:  # callback errors are returned to MC
            reply["error"] = {
                "message": str(exc),
                "type": type(exc).__name__,
            }
        try:
            self._write_payload(
                process,
                reply,
                context=f"replying to host callback {callback_method}",
            )
        except RuntimeProtocolError:
            log.debug(
                "Magic Context host callback reply failed for %s",
                callback_method,
                exc_info=True,
            )
        finally:
            with self._callback_threads_lock:
                self._callback_threads.discard(current)

    def _start_host_callback(
        self,
        process: subprocess.Popen[str],
        callback_id: Any,
        callback_method: str,
        callback_params: dict[str, Any],
    ) -> None:
        # Hermes binds public parent-agent and profile/session state through
        # ContextVars for the duration of an active turn. New Python threads do
        # not inherit that state automatically, so preserve the current context
        # when dispatching a concurrent host callback. This keeps Dreamer child
        # launches attached to the real active Hermes parent while still allowing
        # an abort callback to run concurrently with a long prompt callback.
        execution_context = contextvars.copy_context()
        worker = threading.Thread(
            target=execution_context.run,
            args=(
                self._dispatch_host_callback,
                process,
                callback_id,
                callback_method,
                callback_params,
            ),
            name=f"magic-hermes-host-callback-{callback_method[:24]}",
            daemon=True,
        )
        with self._callback_threads_lock:
            self._callback_threads.add(worker)
        worker.start()

    def call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> Any:
        """Call one runtime method without mutation-replaying retries."""

        with self._lock:
            process = self._ensure_process()
            if process.stdin is None or process.stdout is None:
                raise RuntimeProtocolError("Runtime pipes are unavailable")

            request_id = self._next_id
            self._next_id += 1
            payload = {
                "id": request_id,
                "method": method,
                "params": params or {},
            }

            self._write_payload(
                process,
                payload,
                context=f"sending {method}",
            )

            wait_for = self.timeout if timeout is None else timeout
            deadline = time.monotonic() + wait_for
            while True:
                remaining = max(0.0, deadline - time.monotonic())
                ready, _, _ = select.select([process.stdout], [], [], remaining)
                if not ready:
                    self._dispose(process)
                    raise RuntimeProtocolError(
                        f"Runtime timed out after {wait_for:.1f}s during {method}; "
                        "request was not replayed"
                    )

                line = process.stdout.readline()
                if not line:
                    code = process.poll()
                    self._dispose(process)
                    raise RuntimeProtocolError(
                        f"Runtime exited during {method} with status {code}; "
                        "request was not replayed"
                    )

                try:
                    response = json.loads(line)
                except json.JSONDecodeError as exc:
                    self._dispose(process)
                    raise RuntimeProtocolError(
                        f"Runtime returned invalid JSON during {method}"
                    ) from exc

                if not isinstance(response, dict):
                    self._dispose(process)
                    raise RuntimeProtocolError(
                        f"Runtime returned a non-object JSON response during {method}"
                    )

                if response.get("type") == "host_callback":
                    callback_id = response.get("callback_id")
                    callback_method = str(response.get("method") or "")
                    callback_params = response.get("params")
                    if not isinstance(callback_params, dict):
                        callback_params = {}
                    # Do not block the stdout reader on a long host callback.
                    # Upstream Dreamer may emit a second callback (abort) while
                    # the child launch callback is still executing.
                    self._start_host_callback(
                        process,
                        callback_id,
                        callback_method,
                        callback_params,
                    )
                    continue

                if response.get("id") != request_id:
                    self._dispose(process)
                    raise RuntimeProtocolError(
                        f"Runtime response id mismatch during {method}"
                    )
                error = response.get("error")
                if error:
                    if isinstance(error, dict):
                        message = error.get("message") or "unknown runtime error"
                    else:
                        message = str(error)
                    raise RuntimeProtocolError(f"{method}: {message}")
                return response.get("result")

    def close(self) -> None:
        """Terminate this client's private runtime process."""

        with self._lock:
            if self._process is not None:
                self._dispose(self._process)

    def _dispose(self, process: subprocess.Popen[str]) -> None:
        if self._process is process:
            self._process = None
        for stream in (process.stdin, process.stdout):
            try:
                if stream is not None:
                    stream.close()
            except OSError:
                pass
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        try:
            if process.stderr is not None:
                process.stderr.close()
        except OSError:
            pass

    def __enter__(self) -> RuntimeClient:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


__all__ = [
    "RuntimeClient",
    "RuntimeErrorBase",
    "RuntimeProtocolError",
    "RuntimeUnavailable",
    "find_magic_context_package",
    "runtime_available",
    "runtime_unavailable_reason",
]
