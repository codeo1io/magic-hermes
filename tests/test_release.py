from __future__ import annotations

import re

import pytest

import scripts.release as release


def test_set_version_updates_all_package_metadata(monkeypatch, tmp_path):
    (tmp_path / "src" / "magic_hermes").mkdir(parents=True)
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "magic-hermes"\nversion = "0.2.0.dev0"\n',
        encoding="utf-8",
    )
    (tmp_path / "plugin.yaml").write_text(
        "name: magic-hermes\nversion: 0.2.0.dev0\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "magic_hermes" / "__init__.py").write_text(
        '__version__ = "0.2.0.dev0"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(release, "ROOT", tmp_path)

    release.set_version("1.2.3")
    release.assert_versions("1.2.3")

    assert 'version = "1.2.3"' in (tmp_path / "pyproject.toml").read_text()
    assert "version: 1.2.3" in (tmp_path / "plugin.yaml").read_text()
    assert '__version__ = "1.2.3"' in (
        tmp_path / "src" / "magic_hermes" / "__init__.py"
    ).read_text()


def test_set_version_rejects_non_release_version():
    with pytest.raises(release.ReleaseError, match=r"X\.Y\.Z"):
        release.set_version("0.2.0.dev0")


def test_release_semver_only_accepts_three_numeric_components():
    assert release.SEMVER.fullmatch("0.2.0")
    assert release.SEMVER.fullmatch("12.34.56")
    assert not release.SEMVER.fullmatch("v0.2.0")
    assert not release.SEMVER.fullmatch("0.2")
    assert not release.SEMVER.fullmatch("0.2.0-rc1")


def test_next_patch_version_increments_only_patch_component():
    assert release.next_patch_version("0.2.0") == "0.2.1"
    assert release.next_patch_version("12.34.56") == "12.34.57"


def test_release_allows_magic_context_sync_changes_before_patch_bump(monkeypatch):
    monkeypatch.setattr(
        release,
        "git_output",
        lambda *args: (
            " M package.json\n M package-lock.json\n"
            " M src/magic_hermes/magic_context_compat.json"
        ),
    )
    monkeypatch.setattr(release, "current_version", lambda: "0.2.0")

    release.ensure_clean_or_release_version("0.2.1")


def test_release_rejects_unrelated_dirty_paths(monkeypatch):
    monkeypatch.setattr(release, "git_output", lambda *args: " M README.md")

    with pytest.raises(release.ReleaseError, match="outside the release transaction"):
        release.ensure_clean_or_release_version("0.2.1")


def test_install_notes_use_authenticated_download_for_private_repo():
    notes = release.install_notes("0.2.0", "v0.2.0", "PRIVATE")
    assert "gh release download v0.2.0" in notes
    assert "pip install magic_hermes-0.2.0-py3-none-any.whl" in notes


def test_install_notes_use_direct_url_for_public_repo():
    notes = release.install_notes("0.2.0", "v0.2.0", "PUBLIC")
    assert (
        "pip install https://github.com/codeo1io/magic-hermes/releases/download/"
        in notes
    )


def test_replace_once_requires_matching_version_line(tmp_path):
    path = tmp_path / "sample.txt"
    path.write_text("name = nope\n", encoding="utf-8")
    with pytest.raises(release.ReleaseError, match="could not update version"):
        release.replace_once(path, re.escape('version = "old"'), 'version = "new"')
