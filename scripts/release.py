#!/usr/bin/env python3
"""Build, validate, tag, push, and publish a Magic-Hermes GitHub release."""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEMVER = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
VERSION_FILES = {
    "pyproject.toml",
    "plugin.yaml",
    "src/magic_hermes/__init__.py",
}
MAGIC_CONTEXT_SYNC_FILES = {
    "package.json",
    "package-lock.json",
    "src/magic_hermes/magic_context_compat.json",
    "README.md",
}

#: The interpreter operational probes resolve through PATH — the maestro
#: phase-2 observer runs ``magic-hermes doctor`` from this venv, so landing
#: a release on master does nothing for the finding until the wheel is
#: installed HERE (finding d59758598379: release lane ended at publish).
DEFAULT_DEPLOY_VENV = Path("/home/agent/.hermes/hermes-agent/venv")
#: Overrides DEFAULT_DEPLOY_VENV (tests, other hosts).
DEPLOY_VENV_ENV = "MAGIC_HERMES_DEPLOY_VENV"
#: When set by the deploy/release lane, the deploy step additionally runs
#: ``<venv>/bin/magic-hermes doctor`` and requires a clean exit.
DEPLOY_SMOKE_ENV = "MAGIC_HERMES_DEPLOY_SMOKE"


class ReleaseError(RuntimeError):
    """Raised when a release precondition or command fails."""


def run(*args: str, capture: bool = False, env: dict[str, str] | None = None) -> str:
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    result = subprocess.run(
        args,
        cwd=ROOT,
        check=False,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        env=merged_env,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        suffix = f"\n{detail}" if detail else ""
        raise ReleaseError(f"command failed: {' '.join(args)}{suffix}")
    return (result.stdout or "").rstrip()


def current_version() -> str:
    match = re.search(
        r'^version = "([^"]+)"$',
        (ROOT / "pyproject.toml").read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    if match is None:
        raise ReleaseError("could not read project version from pyproject.toml")
    return match.group(1)


def replace_once(path: Path, pattern: str, replacement: str) -> None:
    text = path.read_text(encoding="utf-8")
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.MULTILINE)
    if count != 1:
        try:
            display_path = path.relative_to(ROOT)
        except ValueError:
            display_path = path
        raise ReleaseError(f"could not update version in {display_path}")
    path.write_text(updated, encoding="utf-8")


def set_version(version: str) -> None:
    if not SEMVER.fullmatch(version):
        raise ReleaseError(f"release version must be X.Y.Z, got {version!r}")
    replace_once(
        ROOT / "pyproject.toml",
        r'^version = "[^"]+"$',
        f'version = "{version}"',
    )
    replace_once(
        ROOT / "plugin.yaml",
        r"^version: .+$",
        f"version: {version}",
    )
    replace_once(
        ROOT / "src" / "magic_hermes" / "__init__.py",
        r'^__version__ = "[^"]+"$',
        f'__version__ = "{version}"',
    )


def assert_versions(version: str) -> None:
    pyproject_version = current_version()
    plugin_version = re.search(
        r"^version: (.+)$",
        (ROOT / "plugin.yaml").read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    init_version = re.search(
        r'^__version__ = "([^"]+)"$',
        (ROOT / "src" / "magic_hermes" / "__init__.py").read_text(
            encoding="utf-8"
        ),
        re.MULTILINE,
    )
    found = {
        pyproject_version,
        plugin_version.group(1) if plugin_version else "<missing>",
        init_version.group(1) if init_version else "<missing>",
    }
    if found != {version}:
        raise ReleaseError(f"version metadata is inconsistent: {sorted(found)}")


def ensure_tools() -> None:
    for tool in ("git", "gh", "npm", "node"):
        if shutil.which(tool) is None:
            raise ReleaseError(f"required tool is not on PATH: {tool}")
    python = ROOT / ".venv" / "bin" / "python"
    if not python.is_file():
        raise ReleaseError(".venv/bin/python is required for release validation")
    run("gh", "auth", "status")


def git_output(*args: str) -> str:
    return run("git", *args, capture=True)


def next_patch_version(version: str) -> str:
    match = SEMVER.fullmatch(version)
    if match is None:
        raise ReleaseError(f"current version must be X.Y.Z, got {version!r}")
    major, minor, patch = (int(part) for part in match.groups())
    return f"{major}.{minor}.{patch + 1}"


def latest_tag() -> str | None:
    """Return the highest semantic-version tag, ignoring non-release tags."""
    tags = [
        item
        for item in git_output("tag", "-l", "v*", "--sort=v:refname").splitlines()
        if SEMVER.fullmatch(item.removeprefix("v"))
    ]
    return tags[-1] if tags else None


def pending_release_version(current: str, latest: str | None) -> str | None:
    """Return the pre-bumped project version when it has not been released yet.

    A squash-merged release chore can bump the version metadata before the
    tag is cut (for example master at 0.3.4 with the newest tag at v0.3.3).
    In that state ``--next-patch`` must release the pending version, not bump
    past it — otherwise the pending version never gets a tag or release.
    """
    if latest is None:
        return None
    released = latest.removeprefix("v")
    if not SEMVER.fullmatch(released):
        return None
    if not SEMVER.fullmatch(current):
        raise ReleaseError(
            f"project version {current!r} is not X.Y.Z; fix pyproject.toml"
        )
    current_parts = [int(part) for part in current.split(".")]
    released_parts = [int(part) for part in released.split(".")]
    if current_parts > released_parts:
        return current
    return None


def next_release_version() -> str:
    """Resolve what ``--next-patch`` should release, honoring pending bumps."""
    current = current_version()
    latest = latest_tag()
    pending = pending_release_version(current, latest)
    if pending is not None:
        print(
            f"Version {current} is set in project metadata but newer than tag "
            f"{latest}; releasing the pending version instead of bumping."
        )
        return pending
    return next_patch_version(current)


def ensure_clean_or_release_version(version: str) -> None:
    status = git_output("status", "--porcelain")
    if not status:
        return

    allowed = VERSION_FILES | MAGIC_CONTEXT_SYNC_FILES
    dirty = set()
    for line in status.splitlines():
        path = line[3:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        dirty.add(path)

    disallowed = dirty - allowed
    if disallowed:
        raise ReleaseError(
            "working tree contains changes outside the release transaction; "
            f"dirty paths: {', '.join(sorted(dirty))}"
        )

    if dirty & VERSION_FILES and current_version() != version:
        raise ReleaseError(
            "release metadata is already modified for a different version; "
            f"dirty paths: {', '.join(sorted(dirty))}"
        )


def ensure_default_branch() -> str:
    branch = git_output("branch", "--show-current")
    default = run(
        "gh",
        "repo",
        "view",
        "--json",
        "defaultBranchRef",
        "--jq",
        ".defaultBranchRef.name",
        capture=True,
    )
    if branch != default:
        raise ReleaseError(
            f"release must run from default branch {default!r}, got {branch!r}"
        )
    run("git", "fetch", "origin", default, "--tags")
    divergence = git_output(
        "rev-list", "--left-right", "--count", f"HEAD...origin/{default}"
    )
    if divergence != "0\t0":
        raise ReleaseError(f"{default} must be synchronized with origin/{default}")
    return default


def validate_and_build() -> list[Path]:
    run("npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund")
    package_root = ROOT / "node_modules" / "@cortexkit" / "pi-magic-context"
    if not package_root.is_dir():
        raise ReleaseError("repo-pinned Magic Context npm package was not installed")

    python = str(ROOT / ".venv" / "bin" / "python")
    ruff = str(ROOT / ".venv" / "bin" / "ruff")
    env = {"MAGIC_CONTEXT_PACKAGE_ROOT": str(package_root)}
    run(python, "scripts/sync_magic_context_release.py", "--check")
    run(python, "-m", "pytest", "-q", env=env)
    run(ruff, "check", "src", "tests", "scripts")
    run("node", "--check", "src/magic_hermes/bridge/loader.mjs")
    run("node", "--check", "src/magic_hermes/bridge/runtime.mjs")

    dist = ROOT / "dist"
    if dist.exists():
        shutil.rmtree(dist)
    run(python, "-m", "build")
    artifacts = sorted(dist.glob("magic_hermes-*.whl")) + sorted(
        dist.glob("magic_hermes-*.tar.gz")
    )
    if len(artifacts) != 2:
        raise ReleaseError(
            f"expected wheel and sdist, found {len(artifacts)} artifacts"
        )
    return artifacts


def write_checksums(artifacts: list[Path]) -> Path:
    checksum_path = ROOT / "dist" / "SHA256SUMS"
    lines = []
    for artifact in artifacts:
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        lines.append(f"{digest}  {artifact.name}")
    checksum_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return checksum_path


def tag_exists(tag: str) -> bool:
    return subprocess.run(
        ["git", "rev-parse", "-q", "--verify", f"refs/tags/{tag}"],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    ).returncode == 0


def release_exists(tag: str) -> bool:
    return subprocess.run(
        ["gh", "release", "view", tag],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    ).returncode == 0


def deploy_target() -> Path:
    """Resolve the deploy target venv: env override beats the default."""
    override = os.environ.get(DEPLOY_VENV_ENV)
    return Path(override) if override else DEFAULT_DEPLOY_VENV


def deployed_version(target: Path) -> str | None:
    """Report the magic-hermes version installed in the TARGET venv.

    Resolution runs in the target interpreter itself — never this one —
    because the development interpreter's own metadata can be stale.
    Returns ``None`` when the venv is absent or magic-hermes is missing.
    """
    python = target / "bin" / "python"
    if not python.is_file():
        return None
    probe = subprocess.run(
        [
            str(python),
            "-c",
            "import importlib.metadata as m; print(m.version('magic-hermes'))",
        ],
        cwd=ROOT,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    if probe.returncode != 0:
        return None
    return probe.stdout.strip() or None


def deploy_release(artifacts: list[Path], version: str, target: Path) -> None:
    """Install and verify the freshly built wheel in the probe venv.

    Ordering (callers): after ``commit_and_tag`` and before
    ``publish_release`` — the tag exists when bits land in the venv, and a
    deploy failure aborts the release before the GitHub release object is
    created. Idempotent: when the target already carries ``version`` the
    install is skipped and verification still runs. A missing venv,
    missing pip, or a failed verification is a loud error, never a skip.
    """
    python = target / "bin" / "python"
    if not python.is_file():
        raise ReleaseError(f"deploy target venv python is missing: {python}")

    wheel_name = f"magic_hermes-{version}-py3-none-any.whl"
    wheel = next((a for a in artifacts if a.name == wheel_name), None)
    if wheel is None:
        raise ReleaseError(
            f"release wheel {wheel_name} not found among built artifacts"
        )

    installed = deployed_version(target)
    if installed != version:
        run(str(python), "-m", "pip", "--version")
        run(
            str(python),
            "-m",
            "pip",
            "install",
            "--force-reinstall",
            "--no-deps",
            str(wheel),
        )
        installed = deployed_version(target)
        if installed != version:
            raise ReleaseError(
                f"target venv {target} reports magic-hermes {installed!r} "
                f"after installing the wheel; expected {version}"
            )

    console_script = target / "bin" / "magic-hermes"
    if not console_script.is_file():
        raise ReleaseError(f"console script missing after deploy: {console_script}")

    if os.environ.get(DEPLOY_SMOKE_ENV):
        run(str(console_script), "doctor")


def redeploy_release(version: str, tag: str) -> None:
    """Re-deliver an already-released version to the deploy target.

    Taken when the GitHub release exists and ``--deploy`` was requested:
    the tag must exist and point at current HEAD, so the wheel is rebuilt
    from exactly the released tree; commit and publish are skipped (both
    already happened for this version).
    """
    if not tag_exists(tag):
        raise ReleaseError(
            f"GitHub release {tag} exists but tag {tag} does not; "
            "refusing to deploy from an untagged tree"
        )
    tagged_commit = git_output("rev-list", "-n", "1", tag)
    head = git_output("rev-parse", "HEAD")
    if tagged_commit != head:
        raise ReleaseError(
            f"tag {tag} does not point at current HEAD "
            f"({tagged_commit[:12]} vs {head[:12]}); refusing to redeploy "
            "a foreign tag"
        )
    artifacts = validate_and_build()
    target = deploy_target()
    deploy_release(artifacts, version, target)
    print(f"Redeployed Magic-Hermes {tag} into {target}")


def commit_and_tag(version: str, tag: str, default_branch: str) -> None:
    if not tag_exists(tag):
        if current_version() != version:
            set_version(version)
        assert_versions(version)
        run("git", "add", *sorted(VERSION_FILES | MAGIC_CONTEXT_SYNC_FILES))
        staged = git_output("diff", "--cached", "--name-only")
        if staged:
            run("git", "commit", "-m", f"release: {tag}")
        elif not any(
            subject.startswith(f"release: {tag}")
            for subject in git_output("log", "--format=%s").splitlines()
        ):
            # Clean tree at the release version but no release commit in
            # history: an unexplained state, so refuse rather than tag it.
            raise ReleaseError(
                "release version is set but no matching release commit exists"
            )
        # Otherwise the release commit already exists in history (for
        # example a squashed "release: v0.3.4 (#16)" with follow-up commits
        # on top) and the tree carries the version metadata: tag HEAD.

        remaining = git_output("status", "--porcelain")
        if remaining:
            raise ReleaseError(
                "release commit left uncommitted changes behind:\n" + remaining
            )
        run("git", "tag", "-a", tag, "-m", f"Magic-Hermes {tag}")

    tagged_commit = git_output("rev-list", "-n", "1", tag)
    head = git_output("rev-parse", "HEAD")
    if tagged_commit != head:
        raise ReleaseError(f"tag {tag} does not point at current HEAD")
    run("git", "push", "origin", default_branch)
    run("git", "push", "origin", tag)


def previous_tag(tag: str) -> str | None:
    tags = git_output("tag", "-l", "--sort=v:refname").splitlines()
    others = [item for item in tags if item != tag]
    return others[-1] if others else None


def magic_context_tested_version() -> str | None:
    match = re.search(
        r'"tested_version":\s*"([^"]+)"',
        (ROOT / "src" / "magic_hermes" / "magic_context_compat.json").read_text(
            encoding="utf-8"
        ),
    )
    return match.group(1) if match else None


def changes_bullets(tag: str) -> list[str]:
    previous = previous_tag(tag)
    args = ["log", "--format=%s"]
    if previous:
        args.append(f"{previous}..HEAD")
    bullets = []
    for subject in git_output(*args).splitlines():
        stripped = subject.strip()
        if not stripped or stripped.startswith("release: "):
            continue
        bullets.append(f"- {stripped}")
    return bullets


def release_notes(version: str, tag: str, visibility: str) -> str:
    wheel = f"magic_hermes-{version}-py3-none-any.whl"
    if visibility.upper() == "PUBLIC":
        install_command = (
            f"pip install https://github.com/codeo1io/magic-hermes/releases/download/"
            f"{tag}/{wheel}"
        )
    else:
        install_command = (
            f"gh release download {tag} --repo codeo1io/magic-hermes "
            f"--pattern '{wheel}'\n"
            f"pip install {wheel}"
        )

    sections = []
    bullets = changes_bullets(tag)
    if bullets:
        sections.append("## Changes\n\n" + "\n".join(bullets))
    tested = magic_context_tested_version()
    if tested:
        sections.append(
            f"Tested against Magic Context core `v{tested}` "
            "(`package.json` pin; runtime discovery still walks Pi/OpenCode "
            "locations or `MAGIC_CONTEXT_PACKAGE_ROOT`)."
        )
    sections.append(f"## Install\n\n```bash\n{install_command}\n```")
    return "\n\n".join(sections) + "\n"


def publish_release(
    version: str,
    tag: str,
    artifacts: list[Path],
    checksum: Path,
) -> None:
    if release_exists(tag):
        print(f"GitHub release {tag} already exists; nothing to publish")
        return
    visibility = run(
        "gh",
        "repo",
        "view",
        "--json",
        "visibility",
        "--jq",
        ".visibility",
        capture=True,
    )
    notes = release_notes(version, tag, visibility)
    run(
        "gh",
        "release",
        "create",
        tag,
        *(str(path) for path in [*artifacts, checksum]),
        "--verify-tag",
        "--title",
        f"Magic-Hermes {tag}",
        "--notes",
        notes,
        "--generate-notes",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", nargs="?", help="release version in X.Y.Z form")
    parser.add_argument(
        "--next-patch",
        action="store_true",
        help="release the next patch version based on current project metadata",
    )
    parser.add_argument(
        "--build-only",
        action="store_true",
        help="validate and build artifacts without committing, tagging, or publishing",
    )
    parser.add_argument(
        "--deploy",
        action="store_true",
        help=(
            "after tagging, and before publishing, install and verify the "
            "built wheel in the deploy target venv (default "
            f"{DEFAULT_DEPLOY_VENV}; override with {DEPLOY_VENV_ENV})"
        ),
    )
    args = parser.parse_args()

    if args.next_patch and args.version:
        parser.error("version and --next-patch are mutually exclusive")
    if args.next_patch:
        version = next_release_version()
    elif args.version:
        version = args.version.removeprefix("v")
    else:
        parser.error("version or --next-patch is required")

    if not SEMVER.fullmatch(version):
        parser.error("version must use X.Y.Z form")
    tag = f"v{version}"

    ensure_tools()
    ensure_clean_or_release_version(version)
    default_branch = ensure_default_branch()
    if release_exists(tag):
        if not args.deploy:
            raise ReleaseError(f"GitHub release {tag} already exists")
        redeploy_release(version, tag)
        return 0

    if current_version() != version:
        set_version(version)
    assert_versions(version)
    artifacts = validate_and_build()
    checksum = write_checksums(artifacts)

    if args.build_only:
        print("Built release artifacts:")
        for path in [*artifacts, checksum]:
            print(path.relative_to(ROOT))
        return 0

    commit_and_tag(version, tag, default_branch)
    if args.deploy:
        deploy_release(artifacts, version, deploy_target())
    publish_release(version, tag, artifacts, checksum)
    print(f"Published Magic-Hermes {tag}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ReleaseError as exc:
        print(f"release failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
