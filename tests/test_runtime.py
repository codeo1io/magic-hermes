from __future__ import annotations

import io
import json

import pytest

import magic_hermes.runtime as runtime


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
    _local_runtime(monkeypatch, tmp_path, "0.38.7")

    assert runtime.runtime_available() is True
    assert runtime.runtime_unavailable_reason() == ""


def test_runtime_accepts_supported_prerelease(monkeypatch, tmp_path):
    _local_runtime(monkeypatch, tmp_path, "0.38.0-beta.1+build.2")

    assert runtime.runtime_available() is True


def test_runtime_rejects_unreviewed_upstream_series(monkeypatch, tmp_path):
    _local_runtime(monkeypatch, tmp_path, "0.39.0")

    assert runtime.runtime_available() is False
    assert "requires the 0.38.x series" in runtime.runtime_unavailable_reason()


def test_runtime_rejects_malformed_supported_series(monkeypatch, tmp_path):
    _local_runtime(monkeypatch, tmp_path, "0.38.bad")

    assert runtime.runtime_available() is False
    assert "requires the 0.38.x series" in runtime.runtime_unavailable_reason()


def test_runtime_rejects_unreadable_package_version(monkeypatch, tmp_path):
    package_root = _local_runtime(monkeypatch, tmp_path, "0.38.0")
    (package_root / "package.json").write_text("{", encoding="utf-8")

    assert runtime.runtime_available() is False
    assert "unreadable version" in runtime.runtime_unavailable_reason()


def test_explicit_package_root_is_preflighted_before_spawn(monkeypatch, tmp_path):
    package_root = _local_runtime(monkeypatch, tmp_path, "0.39.0")
    dist = package_root / "dist"
    dist.mkdir()
    (dist / "index.js").write_text("", encoding="utf-8")

    def unexpected_spawn(*_args, **_kwargs):
        raise AssertionError(
            "unsupported package must be rejected before spawning Node"
        )

    monkeypatch.setattr(runtime.subprocess, "Popen", unexpected_spawn)
    client = runtime.RuntimeClient(package_root=package_root)

    with pytest.raises(
        runtime.RuntimeUnavailable,
        match=r"requires the 0\.38\.x series",
    ):
        client._start()


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
