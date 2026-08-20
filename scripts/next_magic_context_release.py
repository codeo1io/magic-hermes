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


def next_release_tag(releases: list[dict], current_version: str) -> str | None:
    current_tag = f"v{current_version.removeprefix('v')}"
    published = [
        item
        for item in releases
        if isinstance(item, dict)
        and not item.get("draft")
        and SEMVER_TAG.fullmatch(str(item.get("tag_name", "")))
    ]
    published.sort(
        key=lambda item: item.get("published_at") or item.get("created_at") or ""
    )

    for index, item in enumerate(published):
        if item["tag_name"] == current_tag:
            if index + 1 < len(published):
                return str(published[index + 1]["tag_name"])
            return None

    raise ValueError(
        f"tracked Magic Context release {current_tag} was not found in the release set"
    )


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
