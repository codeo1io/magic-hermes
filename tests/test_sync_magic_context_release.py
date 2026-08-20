from __future__ import annotations

import json

import pytest

from scripts.sync_magic_context_release import PACKAGE, check, sync


def _seed(tmp_path, version="0.38.0"):
    compat_dir = tmp_path / "src" / "magic_hermes"
    compat_dir.mkdir(parents=True)
    (compat_dir / "magic_context_compat.json").write_text(
        json.dumps(
            {
                "package": PACKAGE,
                "repository": "cortexkit/magic-context",
                "release_tag": f"v{version}",
                "tested_version": version,
                "supported_series": [0, 38],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "name": "test",
                "private": True,
                "dependencies": {PACKAGE: version},
            }
        ),
        encoding="utf-8",
    )


def test_sync_updates_exact_pin_and_supported_series(tmp_path):
    _seed(tmp_path)

    assert sync(tmp_path, "0.39.2", "v0.39.2") is True
    check(tmp_path)

    compat = json.loads(
        (tmp_path / "src" / "magic_hermes" / "magic_context_compat.json").read_text()
    )
    package = json.loads((tmp_path / "package.json").read_text())
    assert compat["tested_version"] == "0.39.2"
    assert compat["supported_series"] == [0, 39]
    assert package["dependencies"][PACKAGE] == "0.39.2"


def test_sync_is_idempotent(tmp_path):
    _seed(tmp_path)

    assert sync(tmp_path, "0.38.0", "v0.38.0") is False
    check(tmp_path)


def test_sync_rejects_non_core_release_tag(tmp_path):
    _seed(tmp_path)

    with pytest.raises(ValueError, match="does not match package version"):
        sync(tmp_path, "0.39.0", "dashboard-v0.39.0")


def test_check_rejects_dependency_drift(tmp_path):
    _seed(tmp_path)
    package_path = tmp_path / "package.json"
    package = json.loads(package_path.read_text())
    package["dependencies"][PACKAGE] = "0.37.0"
    package_path.write_text(json.dumps(package), encoding="utf-8")

    with pytest.raises(ValueError, match="out of sync"):
        check(tmp_path)
