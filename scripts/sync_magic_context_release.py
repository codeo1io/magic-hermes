#!/usr/bin/env python3
"""Synchronize magic-hermes with one published Magic Context core release."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

PACKAGE = "@cortexkit/pi-magic-context"
SEMVER = re.compile(
    r"^(0|[1-9][0-9]*)[.](0|[1-9][0-9]*)[.](0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:[+][0-9A-Za-z.-]+)?$"
)


def _root() -> Path:
    return Path(__file__).resolve().parents[1]


def _load_json(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _series(version: str) -> list[int]:
    match = SEMVER.fullmatch(version)
    if match is None:
        raise ValueError(f"invalid semantic version: {version}")
    return [int(match.group(1)), int(match.group(2))]


def sync(root: Path, version: str, release_tag: str | None = None) -> bool:
    series = _series(version)
    tag = release_tag or f"v{version}"
    if tag != f"v{version}":
        raise ValueError(
            f"core release tag {tag!r} does not match package version {version!r}"
        )

    compat_path = root / "src" / "magic_hermes" / "magic_context_compat.json"
    package_path = root / "package.json"
    compat = _load_json(compat_path)
    package = _load_json(package_path)

    dependencies = package.setdefault("dependencies", {})
    if not isinstance(dependencies, dict):
        raise ValueError("package.json dependencies must be an object")

    changed = False
    desired_compat = {
        "package": PACKAGE,
        "repository": "cortexkit/magic-context",
        "release_tag": tag,
        "tested_version": version,
        "supported_series": series,
    }
    if compat != desired_compat:
        _write_json(compat_path, desired_compat)
        changed = True

    if dependencies.get(PACKAGE) != version:
        dependencies[PACKAGE] = version
        _write_json(package_path, package)
        changed = True

    return changed


def check(root: Path) -> None:
    compat = _load_json(
        root / "src" / "magic_hermes" / "magic_context_compat.json"
    )
    package = _load_json(root / "package.json")
    version = compat.get("tested_version")
    if not isinstance(version, str):
        raise ValueError("compat manifest tested_version must be a string")
    series = _series(version)
    if compat.get("supported_series") != series:
        raise ValueError(
            "compat manifest supported_series does not match tested_version"
        )
    if compat.get("release_tag") != f"v{version}":
        raise ValueError("compat manifest release_tag does not match tested_version")
    dependencies = package.get("dependencies")
    if not isinstance(dependencies, dict) or dependencies.get(PACKAGE) != version:
        raise ValueError("package.json Magic Context dependency is out of sync")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=_root())
    parser.add_argument("--version")
    parser.add_argument("--release-tag")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    root = args.root.resolve()
    if args.check:
        check(root)
        print("Magic Context compatibility metadata is consistent")
        return 0
    if not args.version:
        parser.error("--version is required unless --check is used")
    changed = sync(root, args.version, args.release_tag)
    print("updated" if changed else "already synchronized")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
