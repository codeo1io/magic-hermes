from __future__ import annotations

import json

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
