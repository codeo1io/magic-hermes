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


def test_release_notes_use_authenticated_download_for_private_repo(monkeypatch):
    monkeypatch.setattr(release, "git_output", lambda *args: "")
    monkeypatch.setattr(release, "changes_bullets", lambda tag: ["- fix: something"])

    notes = release.release_notes("0.2.0", "v0.2.0", "PRIVATE")

    assert "gh release download v0.2.0" in notes
    assert "pip install magic_hermes-0.2.0-py3-none-any.whl" in notes
    assert "## Changes" in notes
    assert "- fix: something" in notes


def test_release_notes_use_direct_url_for_public_repo(monkeypatch):
    monkeypatch.setattr(release, "git_output", lambda *args: "")
    monkeypatch.setattr(release, "changes_bullets", lambda tag: [])

    notes = release.release_notes("0.2.0", "v0.2.0", "PUBLIC")

    assert (
        "pip install https://github.com/codeo1io/magic-hermes/releases/download/"
        in notes
    )
    assert "## Changes" not in notes


def test_release_notes_exclude_release_and_merge_commits(monkeypatch):
    monkeypatch.setattr(
        release,
        "git_output",
        lambda *args: "release: v0.2.1\nfix: real change\n",
    )

    bullets = release.changes_bullets("v0.2.1")

    assert bullets == ["- fix: real change"]


def test_replace_once_requires_matching_version_line(tmp_path):
    path = tmp_path / "sample.txt"
    path.write_text("name = nope\n", encoding="utf-8")
    with pytest.raises(release.ReleaseError, match="could not update version"):
        release.replace_once(path, re.escape('version = "old"'), 'version = "new"')


def test_pending_release_version_returns_pre_bumped_metadata():
    # master at 0.3.4 while the newest tag is v0.3.3: --next-patch must
    # release 0.3.4, not silently skip to 0.3.5.
    assert release.pending_release_version("0.3.4", "v0.3.3") == "0.3.4"


def test_pending_release_version_none_when_metadata_matches_latest_tag():
    assert release.pending_release_version("0.3.3", "v0.3.3") is None


def test_pending_release_version_none_when_metadata_is_older_than_tag():
    assert release.pending_release_version("0.3.3", "v0.3.4") is None


def test_pending_release_version_ignores_non_release_tags():
    assert release.pending_release_version("0.3.4", "dashboard-v0.18.0") is None
    assert release.pending_release_version("0.3.4", None) is None


def test_latest_tag_returns_highest_semver_ignoring_non_release_tags(monkeypatch):
    # git --sort=v:refname is version-aware, so the stub mirrors its order.
    monkeypatch.setattr(
        release,
        "git_output",
        lambda *args: "dashboard-v0.18.0\nv0.3.3\nv0.3.4\nv0.3.10\n",
    )
    assert release.latest_tag() == "v0.3.10"


def test_next_release_version_releases_pending_version(monkeypatch, capsys):
    monkeypatch.setattr(release, "current_version", lambda: "0.3.4")
    monkeypatch.setattr(release, "git_output", lambda *args: "v0.3.2\nv0.3.3\n")

    assert release.next_release_version() == "0.3.4"
    assert "releasing the pending version" in capsys.readouterr().out


def test_next_release_version_bumps_when_nothing_pending(monkeypatch, capsys):
    monkeypatch.setattr(release, "current_version", lambda: "0.3.3")
    monkeypatch.setattr(release, "git_output", lambda *args: "v0.3.2\nv0.3.3\n")

    assert release.next_release_version() == "0.3.4"
    assert "releasing the pending version" not in capsys.readouterr().out


def _pending_release_repo(tmp_path, bump_subject):
    """Build a real git repo in the pending-release state.

    Tag v0.3.3 exists, the version metadata was bumped to 0.3.4 by a commit
    titled ``bump_subject``, and a follow-up commit sits on top with a clean
    tree — exactly the state master reaches after ``release: v0.3.4 (#16)``
    plus later work. Returns the worktree path; ``origin`` is a bare repo.
    """
    import subprocess

    origin = tmp_path / "origin.git"
    origin.mkdir()
    subprocess.run(["git", "init", "--bare", "-q", str(origin)], check=True)
    work = tmp_path / "work"
    (work / "src" / "magic_hermes").mkdir(parents=True)
    (work / "pyproject.toml").write_text(
        '[project]\nname = "magic-hermes"\nversion = "0.3.3"\n', encoding="utf-8"
    )
    (work / "plugin.yaml").write_text("version: 0.3.3\n", encoding="utf-8")
    (work / "src" / "magic_hermes" / "__init__.py").write_text(
        '__version__ = "0.3.3"\n', encoding="utf-8"
    )
    # The sync files release.py stages alongside the version metadata.
    (work / "package.json").write_text(
        '{"dependencies": {"@cortexkit/pi-magic-context": "0.43.2"}}\n',
        encoding="utf-8",
    )
    (work / "package-lock.json").write_text(
        '{"lockfileVersion": 3, "packages": {}}\n', encoding="utf-8"
    )
    (work / "src" / "magic_hermes" / "magic_context_compat.json").write_text(
        '{"tested_version": "0.43.2"}\n', encoding="utf-8"
    )

    def git(*args):
        subprocess.run(
            ["git", "-C", str(work), *args],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    git("init", "-q", "-b", "master")
    git("config", "user.name", "test")
    git("config", "user.email", "test@example.com")
    git("add", "-A")
    git("commit", "-q", "-m", "chore: baseline 0.3.3")
    git("tag", "-a", "v0.3.3", "-m", "Magic-Hermes v0.3.3")
    release.set_version("0.3.4")
    git("add", "-A")
    git("commit", "-q", "-m", bump_subject)
    (work / "docs.txt").write_text("follow-up work\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-q", "-m", "docs: follow-up")
    git("remote", "add", "origin", str(origin))
    git("push", "-q", "origin", "master", "--tags")
    return work


def test_commit_and_tag_tags_head_when_release_commit_in_history(
    monkeypatch, tmp_path
):
    """Regression (review P1): the merge-release gate lands on a clean tree
    whose HEAD is not the release commit (it sits an ancestor away). The
    pending-release transaction must tag HEAD, not abort."""
    monkeypatch.setattr(release, "ROOT", tmp_path / "work")
    _pending_release_repo(tmp_path, "release: v0.3.4 (#16)")

    release.commit_and_tag("0.3.4", "v0.3.4", "master")

    head = release.git_output("rev-parse", "HEAD")
    assert release.git_output("rev-list", "-n", "1", "v0.3.4") == head
    # branch and tag reached the remote
    import subprocess

    for ref in ("master", "v0.3.4"):
        remote = subprocess.run(
            [
                "git",
                "--git-dir",
                str(tmp_path / "origin.git"),
                "rev-list",
                "-n",
                "1",
                ref,
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        assert remote == head


def test_commit_and_tag_refuses_release_version_without_release_commit(
    monkeypatch, tmp_path
):
    """A clean tree claiming the release version with no release commit in
    history is unexplained and must still abort (the guard is narrowed, not
    removed)."""
    monkeypatch.setattr(release, "ROOT", tmp_path / "work")
    _pending_release_repo(tmp_path, "chore: bump metadata to 0.3.4")

    with pytest.raises(release.ReleaseError, match="no matching release commit"):
        release.commit_and_tag("0.3.4", "v0.3.4", "master")


def test_pending_release_version_rejects_malformed_current_version():
    """Regression (review P3): a malformed pyproject version must surface as
    a tidy ReleaseError, not a raw ValueError from int()."""
    with pytest.raises(release.ReleaseError, match=r"not X\.Y\.Z"):
        release.pending_release_version("0.3.4-dev", "v0.3.3")
