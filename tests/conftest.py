"""Shared test bootstrap for the magic-hermes suite.

The suite runs on a host where pytest's temporary directory (TMPDIR) can
live INSIDE a git repository — e.g. ``~/.hermes`` is a local-only git repo
(memory: r/hermesagent 1x3soc8, 2026-10-12) and Hermes exports
``TMPDIR=$HOME/.hermes/cache/scratch``.  Git resolves repositories by
walking parent directories, so a "not a repo" fixture directory created
under such a TMPDIR silently inherits the OUTER repository: ``git -C
<nonrepo> rev-parse --git-dir`` succeeds and points at ``~/.hermes/.git``.

That broke four tests the moment the outer repo appeared (2026-10-12,
suite failures holding the Magic Context pin upgrade pipeline hostage):

* ``test_provenance.py::TestDriftFixture::test_not_a_repo_reports_only_repo_readable``
* ``test_provenance.py::TestRepair::test_refuses_non_repo_path``
* ``test_provenance.py::TestDoctorProvenanceSurface::test_unreadable_path_warns_as_skipped``
* ``test_runtime_integration.py::test_upstream_dreamer_maps_verifies_and_classifies_memories``
  (upstream re-derives the project root via ``git rev-parse
  --show-toplevel``; the inherited repo redirected memory mapping to the
  outer checkout, leaving ``memory_verifications.file_path`` empty)

``GIT_CEILING_DIRECTORIES`` stops git's upward walk at the first listed
directory, restoring "a plain directory under TMPDIR is not a repo"
regardless of where the host keeps its scratch space.  Exported here for
every test run — local, CI (where it is a no-op), and the upgrade
pipeline — so the suite stays hermetic to the host's directory layout.
"""

import os

_CEILOMBS = os.environ.get("GIT_CEILING_DIRECTORIES", "")
_CEILING_TARGETS = [p for p in _CEILOMBS.split(os.pathsep) if p]


def _outer_repo_roots() -> list:
    """Git toplevels of cwd and TMPDIR (if any), that ENCLOSE TMPDIR."""

    import subprocess
    import tempfile

    roots = []
    tmp_root = tempfile.gettempdir()
    for probe_dir in {os.getcwd(), tmp_root}:
        try:
            proc = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                cwd=probe_dir,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        toplevel = proc.stdout.strip()
        if proc.returncode == 0 and toplevel:
            # Only a toplevel that strictly encloses the probe dir can
            # leak repo-ness into fixtures created below it.
            common = os.path.commonpath([toplevel, probe_dir])
            if common == toplevel and toplevel != probe_dir:
                roots.append(toplevel)
    return roots


for _root in _outer_repo_roots():
    if _root not in _CEILING_TARGETS:
        _CEILING_TARGETS.append(_root)

if _CEILING_TARGETS:
    os.environ["GIT_CEILING_DIRECTORIES"] = os.pathsep.join(_CEILING_TARGETS)
