# Regression: 2026-10-12 provenance discovery-escape (fix-queue item 259).
#
# On 2026-10-12 ~/.hermes became a git repo (config-versioning baseline)
# while pytest's tmp trees live under it. git's upward .git traversal then
# answered for probe dirs that are NOT repos, so read_state(notarepo)
# reported origin_match/branch_tracking drift instead of just
# repo_readable — reddening the provenance trio and, through the nightly
# updater's full-suite gate, holding the Magic Context 0.48.0 adoption.
#
# Fix under test: _git ceiling-bounds discovery at the target's parent,
# so an ENCLOSING repo can no longer answer for the target; a real repo
# at the target path still resolves every facet.

from __future__ import annotations

import subprocess
from pathlib import Path

from magic_hermes import provenance


def _git(*args: str, cwd: Path) -> tuple[int, str]:
    proc = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode, proc.stdout.strip()


def test_enclosing_repo_does_not_answer_for_nonrepo_target(tmp_path):
    """The 2026-10-12 live shape: probe dir nested inside a real repo.

    OLD code: git discovers the enclosing repo, read_state reports
    origin_match/branch_tracking (and possibly drift) for a path that is
    not a repository at all. NEW code: discovery is ceiling-bounded and
    only repo_readable drifts.
    """
    outer = tmp_path / "outer-repo"
    outer.mkdir()
    assert _git("init", "-q", cwd=outer)[0] == 0
    assert _git("remote", "add", "origin", "git@github.com:example/outer.git", cwd=outer)[0] == 0
    probe = outer / "notarepo"
    probe.mkdir()

    state = provenance.read_state(probe)
    report = provenance.check_state(state)

    assert state.repo_readable is False
    assert report.drifted_facets == ["repo_readable"]


def test_real_repo_at_target_still_resolves(tmp_path):
    """A genuine repo AT the target path keeps every facet (ceiling only
    blocks escape above the target's parent, never resolution inside)."""
    repo = tmp_path / "real"
    repo.mkdir()
    assert _git("init", "-q", cwd=repo)[0] == 0
    assert _git("remote", "add", "origin", provenance.EXPECTED_ORIGIN, cwd=repo)[0] == 0
    assert _git("checkout", "-q", "-b", "master", cwd=repo)[0] == 0

    state = provenance.read_state(repo)

    assert state.repo_readable is True
    assert state.fetch_url == provenance.EXPECTED_ORIGIN


def test_repo_in_nested_dir_inside_repo_still_resolves(tmp_path):
    """A repo nested BELOW an enclosing repo resolves via its own .git —
    ceiling clamps only escape above the target's own parent."""
    outer = tmp_path / "outer"
    outer.mkdir()
    assert _git("init", "-q", cwd=outer)[0] == 0
    inner = outer / "sub" / "real"
    inner.mkdir(parents=True)
    assert _git("init", "-q", cwd=inner)[0] == 0
    assert _git("remote", "add", "origin", provenance.EXPECTED_ORIGIN, cwd=inner)[0] == 0

    state = provenance.read_state(inner)

    assert state.repo_readable is True
    assert state.fetch_url == provenance.EXPECTED_ORIGIN
