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


def test_sync_moves_readme_pin_claims_forward(tmp_path):
    """A pin bump must carry README 'through X.Y.Z' claims with it.

    The docs-consistency battery runs before every auto-release and rejects
    a README claim that lags the manifest — the 2026-09-30 nightly died
    exactly there ("README claims upstream 0.43.2 but the manifest pins
    0.44.0"), leaving the release stuck on the old core.
    """
    _seed(tmp_path)
    (tmp_path / "README.md").write_text(
        "Upstream `@cortexkit/pi-magic-context` (through 0.38.0) seeds historian\n"
        "run telemetry. An unrelated mention of through 0.30.1 stays put.\n",
        encoding="utf-8",
    )

    assert sync(tmp_path, "0.39.2", "v0.39.2") is True

    readme = (tmp_path / "README.md").read_text(encoding="utf-8")
    assert "through 0.39.2" in readme
    assert "through 0.38.0" not in readme
    assert "through 0.30.1" in readme


def test_sync_without_readme_leaves_release_metadata_consistent(tmp_path):
    """No README in the tree (or no matching claim) must not break the sync."""
    _seed(tmp_path)

    assert sync(tmp_path, "0.39.0", "v0.39.0") is True
    check(tmp_path)


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
