from __future__ import annotations

import contextvars
import io
import json
import threading

import pytest

import magic_hermes.runtime as runtime


def _supported_version(patch: int = 0) -> str:
    major, minor = runtime.supported_magic_context_series()
    return f"{major}.{minor}.{patch}"


def _next_series_version() -> str:
    major, minor = runtime.supported_magic_context_series()
    return f"{major}.{minor + 1}.0"


def _supported_series_text() -> str:
    major, minor = runtime.supported_magic_context_series()
    return f"{major}.{minor}.x"


def _local_runtime(monkeypatch, tmp_path, version):
    script = tmp_path / "runtime.mjs"
    script.write_text("", encoding="utf-8")
    package_root = tmp_path / "pi-magic-context"
    package_root.mkdir()
    (package_root / "package.json").write_text(
        json.dumps({"version": version}),
        encoding="utf-8",
    )
    monkeypatch.setattr(runtime.shutil, "which", lambda _name: "/usr/bin/node")
    monkeypatch.setattr(runtime, "runtime_script_path", lambda: script)
    monkeypatch.setattr(
        runtime,
        "find_magic_context_package",
        lambda: package_root,
    )
    return package_root


def test_runtime_available_for_supported_upstream_series(monkeypatch, tmp_path):
    _local_runtime(monkeypatch, tmp_path, _supported_version(7))

    assert runtime.runtime_available() is True
    assert runtime.runtime_unavailable_reason() == ""


def test_runtime_accepts_supported_prerelease(monkeypatch, tmp_path):
    _local_runtime(
        monkeypatch,
        tmp_path,
        runtime.tested_magic_context_version().split("+")[0].split("-")[0]
        + "-beta.1+build.2",
    )

    assert runtime.runtime_available() is True


def test_runtime_rejects_unreviewed_upstream_series(monkeypatch, tmp_path):
    _local_runtime(monkeypatch, tmp_path, _next_series_version())

    assert runtime.runtime_available() is False
    reason = runtime.runtime_unavailable_reason()
    assert f"requires the {_supported_series_text()} series" in reason


def test_runtime_rejects_malformed_supported_series(monkeypatch, tmp_path):
    major, minor = runtime.supported_magic_context_series()
    _local_runtime(monkeypatch, tmp_path, f"{major}.{minor}.bad")

    assert runtime.runtime_available() is False
    reason = runtime.runtime_unavailable_reason()
    assert f"requires the {_supported_series_text()} series" in reason


def test_runtime_rejects_unreadable_package_version(monkeypatch, tmp_path):
    package_root = _local_runtime(
        monkeypatch, tmp_path, runtime.tested_magic_context_version()
    )
    (package_root / "package.json").write_text("{", encoding="utf-8")

    assert runtime.runtime_available() is False
    assert "unreadable version" in runtime.runtime_unavailable_reason()


def test_explicit_package_root_is_preflighted_before_spawn(monkeypatch, tmp_path):
    package_root = _local_runtime(monkeypatch, tmp_path, _next_series_version())
    dist = package_root / "dist"
    dist.mkdir()
    (dist / "index.js").write_text("", encoding="utf-8")

    def unexpected_spawn(*_args, **_kwargs):
        raise AssertionError(
            "unsupported package must be rejected before spawning Node"
        )

    monkeypatch.setattr(runtime.subprocess, "Popen", unexpected_spawn)
    client = runtime.RuntimeClient(package_root=package_root)

    with pytest.raises(runtime.RuntimeUnavailable) as exc_info:
        client._start()
    assert f"requires the {_supported_series_text()} series" in str(exc_info.value)


class _FakeProcess:
    def __init__(self, response_line: str):
        self.stdin = io.StringIO()
        self.stdout = io.StringIO(response_line)
        self.stderr = io.StringIO()

    def poll(self):
        return 0


def _client_with_response(monkeypatch, response_line: str):
    client = runtime.RuntimeClient()
    process = _FakeProcess(response_line)
    monkeypatch.setattr(client, "_ensure_process", lambda: process)
    monkeypatch.setattr(
        runtime.select,
        "select",
        lambda readable, _writable, _errors, _timeout: (readable, [], []),
    )
    return client, process


def test_runtime_rejects_non_object_json_response(monkeypatch):
    client, process = _client_with_response(monkeypatch, "[]\n")

    with pytest.raises(runtime.RuntimeProtocolError, match="non-object JSON response"):
        client.call("hello")

    assert process.stdout.closed is True


def test_runtime_normalizes_non_object_error_payload(monkeypatch):
    client, _process = _client_with_response(
        monkeypatch,
        json.dumps({"id": 1, "error": "bridge exploded"}) + "\n",
    )

    with pytest.raises(runtime.RuntimeProtocolError, match="hello: bridge exploded"):
        client.call("hello")


def test_runtime_dispatches_abort_callback_while_prompt_callback_is_running(
    monkeypatch, tmp_path
):
    """A second host callback must not wait behind a long first callback."""

    script = tmp_path / "runtime.mjs"
    script.write_text(
        r'''
import readline from "node:readline";
const rl = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
let requestId = null;
let slow = null;
let abort = null;
function emit(value) { process.stdout.write(JSON.stringify(value) + "\n"); }
function maybeFinish() {
  if (requestId !== null && slow !== null && abort !== null) {
    emit({ id: requestId, result: { slow, abort } });
  }
}
rl.on("line", (line) => {
  const msg = JSON.parse(line);
  if (msg.type === "host_callback_result") {
    if (msg.callback_id === "slow") slow = msg.result;
    if (msg.callback_id === "abort") abort = msg.result;
    maybeFinish();
    return;
  }
  requestId = msg.id;
  emit({ type: "host_callback", callback_id: "slow", method: "slow", params: {} });
  setTimeout(() => emit({
    type: "host_callback", callback_id: "abort", method: "abort", params: {}
  }), 20);
});
''',
        encoding="utf-8",
    )
    package_root = tmp_path / "pi-magic-context"
    (package_root / "dist").mkdir(parents=True)
    (package_root / "package.json").write_text(
        json.dumps({"version": runtime.tested_magic_context_version()}),
        encoding="utf-8",
    )
    (package_root / "dist" / "index.js").write_text("", encoding="utf-8")
    monkeypatch.setattr(runtime, "runtime_script_path", lambda: script)
    monkeypatch.setattr(runtime.shutil, "which", lambda _name: "/usr/bin/node")

    abort_seen = threading.Event()
    active_parent = contextvars.ContextVar(
        "test_runtime_active_parent", default="missing"
    )

    def callback(method, _params):
        marker = active_parent.get()
        if method == "abort":
            abort_seen.set()
            return {"accepted": True, "parent": marker}
        if method == "slow":
            return {"saw_abort": abort_seen.wait(1.0), "parent": marker}
        raise AssertionError(method)

    with runtime.RuntimeClient(
        package_root=package_root, timeout=3, callback_handler=callback
    ) as client:
        token = active_parent.set("bound-parent")
        try:
            result = client.call("probe", timeout=3)
        finally:
            active_parent.reset(token)

    assert result["abort"] == {"accepted": True, "parent": "bound-parent"}
    assert result["slow"] == {"saw_abort": True, "parent": "bound-parent"}
