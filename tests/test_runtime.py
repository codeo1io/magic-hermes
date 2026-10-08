from __future__ import annotations

import contextvars
import io
import json
import signal
import threading
import time

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


def test_runtime_accepts_version_outside_validated_series(monkeypatch, tmp_path):
    _local_runtime(monkeypatch, tmp_path, _next_series_version())

    assert runtime.runtime_available() is True
    assert runtime.runtime_unavailable_reason() == ""
    notice = runtime.magic_context_version_notice()
    assert _next_series_version() in notice
    assert "validated" in notice


def test_runtime_notice_is_informational_for_malformed_version(monkeypatch, tmp_path):
    major, minor = runtime.supported_magic_context_series()
    _local_runtime(monkeypatch, tmp_path, f"{major}.{minor}.bad")

    assert runtime.runtime_available() is True
    assert runtime.runtime_unavailable_reason() == ""
    assert f"{major}.{minor}.bad" in runtime.magic_context_version_notice()
    assert "outside the" in runtime.magic_context_version_notice()


def test_runtime_rejects_unreadable_package_version(monkeypatch, tmp_path):
    package_root = _local_runtime(
        monkeypatch, tmp_path, runtime.tested_magic_context_version()
    )
    (package_root / "package.json").write_text("{", encoding="utf-8")
    (package_root / "dist").mkdir()
    (package_root / "dist" / "index.js").write_text("", encoding="utf-8")

    assert runtime.runtime_available() is False
    assert "unreadable" in runtime.runtime_unavailable_reason()


def test_explicit_package_root_is_preflighted_before_spawn(monkeypatch, tmp_path):
    missing_root = tmp_path / "no-such-package"
    monkeypatch.setattr(runtime.shutil, "which", lambda _name: "/usr/bin/node")
    monkeypatch.setattr(
        runtime, "runtime_script_path", lambda: tmp_path / "runtime.mjs"
    )
    (tmp_path / "runtime.mjs").write_text("", encoding="utf-8")

    def unexpected_spawn(*_args, **_kwargs):
        raise AssertionError(
            "broken package root must be rejected before spawning Node"
        )

    monkeypatch.setattr(runtime.subprocess, "Popen", unexpected_spawn)
    client = runtime.RuntimeClient(package_root=missing_root)

    with pytest.raises(runtime.RuntimeUnavailable) as exc_info:
        client._start()
    assert "was not found at" in str(exc_info.value)


def test_version_mismatch_proceeds_to_spawn(monkeypatch, tmp_path):
    package_root = _local_runtime(monkeypatch, tmp_path, _next_series_version())
    dist = package_root / "dist"
    dist.mkdir()
    (dist / "index.js").write_text("", encoding="utf-8")

    spawned = []

    def record_spawn(*args, **_kwargs):
        spawned.append(args)
        raise RuntimeError("record_spawn sentinel")

    monkeypatch.setattr(runtime.subprocess, "Popen", record_spawn)
    client = runtime.RuntimeClient(package_root=package_root)

    with pytest.raises(RuntimeError, match="record_spawn"):
        client._start()
    assert len(spawned) == 1


def test_runtime_finalizer_closes_unreleased_client(monkeypatch):
    client = runtime.RuntimeClient()
    closed = []
    monkeypatch.setattr(client, "close", lambda: closed.append(True))

    client.__del__()

    assert closed == [True]


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


def test_runtime_exit_error_includes_stderr_tail(monkeypatch):
    client = runtime.RuntimeClient()
    process = _FakeProcess("")
    # Simulate stderr captured by the drain thread before the exit error.
    client._stderr_tail.extend(
        [
            "[magic-context] storage fatal: refusing to open context.db; "
            "upstream migration lane v85 is newer (max v84)",
        ]
    )
    monkeypatch.setattr(client, "_ensure_process", lambda: process)
    monkeypatch.setattr(
        runtime.select,
        "select",
        lambda readable, _writable, _errors, _timeout: (readable, [], []),
    )

    with pytest.raises(
        runtime.RuntimeProtocolError, match=r"migration lane v85"
    ):
        client.call("bind")


def test_drain_stderr_escalates_storage_fatal_to_warning(caplog):
    import io
    import logging

    client = runtime.RuntimeClient()
    process = _FakeProcess("")
    process.stderr = io.StringIO(
        "[magic-context] storage fatal: refusing to open context.db\n"
        "noise line\n"
    )
    with caplog.at_level(logging.DEBUG, logger="magic_hermes.runtime"):
        client._drain_stderr(process)

    records = [r for r in caplog.records if "Magic Context runtime" in r.message]
    assert any(r.levelname == "WARNING" for r in records)
    assert any(r.levelname == "DEBUG" for r in records)


class _FakeSidecar:
    """Minimal Popen stand-in for lifecycle tests (no real process)."""

    def __init__(self):
        self.stdin = io.StringIO()
        self.stdout = io.StringIO()
        self.stderr = io.StringIO()
        self.pid = 424242
        self.terminated = []
        self.killed = False

    def poll(self):
        return None if not self.killed else -9

    def wait(self, timeout=None):
        return self.poll()

    def terminate(self):
        self.terminated.append("SIGTERM")

    def kill(self):
        self.killed = True


def test_reap_if_idle_requires_a_live_process():
    client = runtime.RuntimeClient(idle_ttl_s=0.0)
    client._process = None

    assert client._reap_if_idle() is False


def test_reap_if_idle_never_reaps_a_closed_client():
    client = runtime.RuntimeClient(idle_ttl_s=0.0)
    process = _FakeSidecar()
    client._process = process
    client._closed = True

    assert client._reap_if_idle() is False
    assert client._process is process


def test_reap_if_idle_spares_recent_activity():
    client = runtime.RuntimeClient(idle_ttl_s=300.0)
    client._process = _FakeSidecar()
    client._last_activity = time.monotonic()  # busy right now

    assert client._reap_if_idle() is False
    assert client._process is not None


def test_reap_if_idle_disposes_a_stale_sidecar():
    client = runtime.RuntimeClient(idle_ttl_s=1.0)
    process = _FakeSidecar()
    client._process = process
    client._last_activity = time.monotonic() - 3600.0  # idle an hour

    disposed = []
    client._dispose = disposed.append

    assert client._reap_if_idle() is True
    assert disposed == [process]


def test_call_refreshes_idle_activity(monkeypatch):
    client, _process = _client_with_response(
        monkeypatch, json.dumps({"id": 1, "result": {}}) + "\n"
    )
    stale = time.monotonic() - 3600.0
    client._last_activity = stale

    client.call("bind")

    assert client._last_activity > stale


def test_failure_cooldown_blocks_until_window_passes():
    client = runtime.RuntimeClient()
    client._failure_cooldown_until = time.monotonic() + 30.0

    with pytest.raises(runtime.RuntimeUnavailable, match="failure cooldown"):
        client._ensure_process()


def test_dispose_kills_the_process_group_on_posix(monkeypatch):
    client = runtime.RuntimeClient()
    process = _FakeSidecar()
    signals = []
    monkeypatch.setattr(runtime.os, "getpgid", lambda pid: 999111)
    monkeypatch.setattr(
        runtime.os, "killpg", lambda pgid, sig: signals.append((pgid, sig))
    )

    client._dispose(process)

    assert signals == [(999111, signal.SIGTERM)]
    assert "SIGTERM" not in process.terminated  # group kill replaced bare terminate


def _make_package(root, version):
    root.mkdir(parents=True, exist_ok=True)
    (root / "package.json").write_text(
        json.dumps({"name": "@cortexkit/pi-magic-context", "version": version}),
        encoding="utf-8",
    )
    (root / "dist").mkdir(exist_ok=True)
    (root / "dist" / "index.js").write_text("// stub\n", encoding="utf-8")
    return root


class TestDiscoveryWidening:
    """U1 (finding c7d63424) — candidates cover every Pi profile root and
    the OMP home, appended after the fixed candidates, deduped, with the
    env override still first.

    The breaking newer copy lived only in ~/.pi/agent-cliproxy-only/npm;
    before this unit no candidate family scanned it, so the doctor could
    not see the copy that migrated the shared store ahead of the pin.
    """

    @staticmethod
    def _isolate_home(monkeypatch, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        script = tmp_path / "runtime.mjs"
        script.write_text("", encoding="utf-8")
        monkeypatch.setattr(runtime.Path, "home", lambda: home)
        monkeypatch.setattr(runtime, "runtime_script_path", lambda: script)
        monkeypatch.setenv("XDG_DATA_HOME", str(home / ".local" / "share"))
        monkeypatch.delenv("MAGIC_CONTEXT_PACKAGE_ROOT", raising=False)
        # keep the cwd-walk tail inside the fabricated tree
        monkeypatch.chdir(tmp_path)
        return home

    def test_all_profile_and_omo_copies_discovered_in_order(
        self, monkeypatch, tmp_path
    ):
        home = self._isolate_home(monkeypatch, tmp_path)
        managed = _make_package(
            home / ".local" / "share" / "magic-hermes" / "node_modules"
            / "@cortexkit" / "pi-magic-context",
            "0.45.0",
        )
        default_pi = _make_package(
            home / ".pi" / "agent" / "npm" / "node_modules" / "@cortexkit"
            / "pi-magic-context",
            "0.45.0",
        )
        cliproxy = _make_package(
            home / ".pi" / "agent-cliproxy-only" / "npm" / "node_modules"
            / "@cortexkit" / "pi-magic-context",
            "0.46.0",
        )
        omo = _make_package(
            home / ".omo" / "npm" / "node_modules" / "@cortexkit"
            / "pi-magic-context",
            "0.45.0",
        )

        candidates = runtime.magic_context_package_candidates()
        resolved = [str(c) for c in candidates]

        assert resolved[0] == str(managed.resolve()), "managed root stays first"
        for copy in (managed, default_pi, cliproxy, omo):
            key = str(copy.resolve())
            assert resolved.count(key) == 1, f"{key} must appear exactly once"
        # fixed families stay ahead of the widened glob family (D6)
        assert resolved.index(str(default_pi.resolve())) < resolved.index(
            str(cliproxy.resolve())
        )
        assert runtime.find_magic_context_package() == managed.resolve()

    def test_profile_without_package_or_broken_profile_contributes_nothing(
        self, monkeypatch, tmp_path
    ):
        home = self._isolate_home(monkeypatch, tmp_path)
        _make_package(
            home / ".pi" / "agent" / "npm" / "node_modules" / "@cortexkit"
            / "pi-magic-context",
            "0.45.0",
        )
        # a profile dir whose npm root has no upstream package
        (home / ".pi" / "bare-profile" / "npm").mkdir(parents=True)
        # a dangling profile symlink must not crash the glob
        (home / ".pi" / "broken-profile").symlink_to(home / ".no-such-target")

        candidates = runtime.magic_context_package_candidates()

        assert not any("bare-profile" in str(c) for c in candidates)
        assert not any("broken-profile" in str(c) for c in candidates)
        assert any(
            str(c).endswith(".pi/agent/npm/node_modules/@cortexkit/"
                            "pi-magic-context")
            for c in candidates
        )

    def test_override_root_still_wins_and_is_first(self, monkeypatch, tmp_path):
        home = self._isolate_home(monkeypatch, tmp_path)
        override = _make_package(tmp_path / "override-pkg", "0.45.0")
        _make_package(
            home / ".pi" / "agent-cliproxy-only" / "npm" / "node_modules"
            / "@cortexkit" / "pi-magic-context",
            "0.46.0",
        )
        monkeypatch.setenv("MAGIC_CONTEXT_PACKAGE_ROOT", str(override))

        candidates = runtime.magic_context_package_candidates()

        assert candidates[0] == override.resolve()
        assert runtime.find_magic_context_package() == override.resolve()
