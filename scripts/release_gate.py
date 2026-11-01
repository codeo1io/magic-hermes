#!/usr/bin/env python3
"""Decide whether master carries an unreleased FUNCTIONAL release.

Gate for the release-on-unreleased-master lane (finding c7d63424daa3):
the only automated release path fired on upstream Magic Context drift and
stopped at publish, so repo-side remediation landing on master never
triggered a release and never reached the probe venv. The gate answers,
from ``git diff --name-only <latest-tag>..HEAD`` plus a functional-surface
fence, whether the checkout has something worth releasing, and prints a
JSON verdict the workflow consumes.

Fence (the repo's own release vocabulary): functional surfaces are the
``src/``, ``scripts/``, ``tests/`` and ``.github/workflows/`` prefixes
plus the repo metadata files (``pyproject.toml``, ``plugin.yaml``,
``package.json``, ``package-lock.json``,
``src/magic_hermes/magic_context_compat.json``). Everything else — README,
docs/, ROADMAP, other markdown — is non-functional: docs-only master must
never cut a release. Version numbering stays single-sourced in
``release.py`` (``--next-patch`` resolution, pending bumps honored); the
verdict's version field is informational — the lane still decides the
number.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
from pathlib import Path

# Direct-file invocation (``python scripts/release_gate.py --check``, the
# lane's shape) puts scripts/ — not the repo root — on sys.path; bootstrap
# the root so the ``scripts`` package resolves, exactly like pytest does.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import scripts.release as release  # noqa: E402

#: Git's well-known empty-tree hash — the diff base when no release tag
#: exists yet (every tracked file then counts as changed).
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"

#: Directory prefixes whose changes are release-bearing (functional).
FUNCTIONAL_PREFIXES = ("src/", "scripts/", "tests/", ".github/workflows/")

#: Standalone functional files: repo/package metadata the lane consumes.
#: ``src/magic_hermes/magic_context_compat.json`` is redundant with the
#: ``src/`` prefix on purpose — belt and braces, mirroring the sync-file
#: vocabulary in ``release.MAGIC_CONTEXT_SYNC_FILES``.
FUNCTIONAL_FILES = frozenset(
    {
        "pyproject.toml",
        "plugin.yaml",
        "package.json",
        "package-lock.json",
        "src/magic_hermes/magic_context_compat.json",
    }
)


def is_functional_path(path: str) -> bool:
    """True when a changed path sits on a functional (release-bearing) surface."""
    return path in FUNCTIONAL_FILES or path.startswith(FUNCTIONAL_PREFIXES)


def changed_paths_since(latest: str | None) -> list[str]:
    """Repo-relative paths changed between the diff base and HEAD.

    ``latest`` is the newest semantic-version tag (``release.latest_tag``);
    ``None`` diffs against the empty tree so an untagged repo still gets a
    total verdict. HEAD is the explicit endpoint — uncommitted dirt never
    counts; the lane releases committed state.
    """
    base = latest if latest is not None else EMPTY_TREE
    output = release.git_output("diff", "--name-only", base, "HEAD")
    return [line for line in output.splitlines() if line]


def release_verdict(
    latest_tag: str | None,
    changed_paths: list[str] | set[str],
    next_version: str | None,
) -> dict[str, object]:
    """Pure should-release decision over fence-classified changed paths."""
    if latest_tag is not None:
        where = f"tag {latest_tag}"
    else:
        where = "the empty tree (no release tag exists)"
    functional = sorted({path for path in changed_paths if is_functional_path(path)})
    if functional:
        reason = (
            f"{len(functional)} functional path(s) changed since {where} "
            f"(e.g. {functional[0]})"
        )
        return {"release": True, "version": next_version, "reason": reason}
    if changed_paths:
        reason = (
            f"docs-only: {len(set(changed_paths))} changed path(s) since {where} "
            "all sit outside the functional fence (src/, scripts/, tests/, "
            ".github/workflows/, repo metadata) — nothing to release"
        )
    else:
        reason = f"no changes since {where} — nothing to release"
    return {"release": False, "version": None, "reason": reason}


def check_repository() -> dict[str, object]:
    """Compose the release verdict for the current checkout (``release.ROOT``)."""
    latest = release.latest_tag()
    changed = changed_paths_since(latest)
    # next_release_version() prints its pending-version notice; the CLI's
    # stdout must carry the JSON verdict alone.
    with contextlib.redirect_stdout(io.StringIO()):
        next_version = release.next_release_version()
    return release_verdict(latest, changed, next_version)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="print the JSON release verdict for the current checkout",
    )
    args = parser.parse_args()
    if not args.check:
        parser.error("--check is required (the gate's only mode)")
    print(json.dumps(check_repository()))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except release.ReleaseError as exc:
        print(f"release gate failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
