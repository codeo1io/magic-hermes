#!/usr/bin/env python3
"""Resolve the next unseen published Magic Context core release."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

SEMVER_TAG = re.compile(
    r"^v[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)


def is_series_jump(current_version: str, next_version: str) -> bool:
    """True when the upgrade crosses a minor or major boundary.

    Patch-only updates within the same series keep the fully automated sync
    path; anything wider requires an adoption PR because it can move the
    compatibility fence and migrate the shared store.
    """
    current = current_version.removeprefix("v").split(".")
    nxt = next_version.removeprefix("v").split(".")
    return current[:2] != nxt[:2]


def _semver_key(tag: str) -> tuple[int, int, int, int]:
    """Order key: semver tuple, final releases rank above same-version prereleases."""
    core = tag.removeprefix("v")
    suffix = core.split("-", 1)[1] if "-" in core else ""
    numbers = [int(part) for part in core.split("-", 1)[0].split(".")]
    return (*numbers, 0 if suffix else 1)  # type: ignore[return-value]


def next_release_tag(releases: list[dict], current_version: str) -> str | None:
    """Return the newest stable release strictly above the current pin.

    Upstream can publish several series and patches between nightly syncs
    (observed: v0.44.0 through v0.44.4 within two days). Stepping one
    release at a time in publish order made every sync adopt an already
    superseded version, so the pin always trailed upstream. Taking the
    highest stable release above the pin catches up in one sync; drafts
    and prerelease-flagged releases are never adopted.
    """
    current_tag = f"v{current_version.removeprefix('v')}"
    published = [
        item
        for item in releases
        if isinstance(item, dict)
        and not item.get("draft")
        and not item.get("prerelease")
        and SEMVER_TAG.fullmatch(str(item.get("tag_name", "")))
    ]
    keys = {
        str(item["tag_name"]): _semver_key(str(item["tag_name"]))
        for item in published
    }

    if current_tag not in keys:
        missing = f"tracked Magic Context release {current_tag}"
        raise ValueError(f"{missing} was not found in the release set")

    newer = [tag for tag, key in keys.items() if key > keys[current_tag]]
    if not newer:
        return None
    return max(newer, key=lambda tag: keys[tag])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--current", required=True, help="currently tested core version"
    )
    parser.add_argument("--releases-json", required=True, type=Path)
    args = parser.parse_args()

    releases = json.loads(args.releases_json.read_text(encoding="utf-8"))
    if not isinstance(releases, list):
        parser.error("releases JSON must contain a list")

    try:
        tag = next_release_tag(releases, args.current)
    except ValueError as exc:
        parser.error(str(exc))
    if tag:
        print(tag)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
