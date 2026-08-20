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


def ensure_clean_or_release_version(version: str) -> None:
    status = git_output("status", "--porcelain")
    if not status:
        return
    allowed = {
        "pyproject.toml",
        "plugin.yaml",
        "src/magic_hermes/__init__.py",
    }
    dirty = set()
    for line in status.splitlines():
        path = line[3:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        dirty.add(path)
    if not dirty.issubset(allowed) or current_version() != version:
        raise ReleaseError(
            "working tree must be clean before starting a release; "
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


def commit_and_tag(version: str, tag: str, default_branch: str) -> None:
    if not tag_exists(tag):
        if current_version() != version:
            set_version(version)
        assert_versions(version)
        run(
            "git",
            "add",
            "pyproject.toml",
            "plugin.yaml",
            "src/magic_hermes/__init__.py",
        )
        staged = git_output("diff", "--cached", "--name-only")
        if staged:
            run("git", "commit", "-m", f"release: {tag}")
        elif not git_output("log", "-1", "--format=%s").startswith(
            f"release: {tag}"
        ):
            raise ReleaseError(
                "release version is set but no matching release commit exists"
            )
        run("git", "tag", "-a", tag, "-m", f"Magic-Hermes {tag}")

    tagged_commit = git_output("rev-list", "-n", "1", tag)
    head = git_output("rev-parse", "HEAD")
    if tagged_commit != head:
        raise ReleaseError(f"tag {tag} does not point at current HEAD")
    run("git", "push", "origin", default_branch)
    run("git", "push", "origin", tag)


def publish_release(
    version: str,
    tag: str,
    artifacts: list[Path],
    checksum: Path,
) -> None:
    if release_exists(tag):
        print(f"GitHub release {tag} already exists; nothing to publish")
        return
    install_command = (
        f"pip install https://github.com/codeo1io/magic-hermes/releases/download/{tag}/"
        f"magic_hermes-{version}-py3-none-any.whl"
    )
    notes = (
        f"## Install\n\n```bash\n{install_command}\n```\n\n"
        "Magic Context must also be installed in a supported Pi/OpenCode location "
        "or exposed with `MAGIC_CONTEXT_PACKAGE_ROOT`.\n"
    )
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
    parser.add_argument("version", help="release version in X.Y.Z form")
    parser.add_argument(
        "--build-only",
        action="store_true",
        help="validate and build artifacts without committing, tagging, or publishing",
    )
    args = parser.parse_args()
    version = args.version.removeprefix("v")
    if not SEMVER.fullmatch(version):
        parser.error("version must use X.Y.Z form")
    tag = f"v{version}"

    ensure_tools()
    ensure_clean_or_release_version(version)
    default_branch = ensure_default_branch()
    if release_exists(tag):
        raise ReleaseError(f"GitHub release {tag} already exists")

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
    publish_release(version, tag, artifacts, checksum)
    print(f"Published Magic-Hermes {tag}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ReleaseError as exc:
        print(f"release failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
