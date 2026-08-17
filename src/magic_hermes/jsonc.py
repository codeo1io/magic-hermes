"""JSONC parsing for the shared CortexKit config.

`~/.config/cortexkit/magic-context.jsonc` is JSON-with-comments (// line
comments, /* block comments */, trailing commas). We strip those and delegate
to the stdlib JSON parser — no third-party dependency, same leniency as the
JSONC handling in the upstream TS plugins.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_DEFAULT_PATH = Path.home() / ".config" / "cortexkit" / "magic-context.jsonc"

_LINE_COMMENT = re.compile(r"//[^\n]*")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_TRAILING_COMMA = re.compile(r",(\s*[}\]])")


def strip_jsonc(text: str) -> str:
    """Remove comments and trailing commas from JSONC text.

    String literals are preserved: comment markers inside strings are left
    alone by temporarily masking string contents.
    """
    masked: list[str] = []

    def _mask(match: re.Match[str]) -> str:
        masked.append(match.group(0))
        return f"\x00{len(masked) - 1}\x00"

    # Mask string literals first so comment syntax inside them survives.
    out = re.sub(r'"(?:[^"\\]|\\.)*"', _mask, text)
    out = _BLOCK_COMMENT.sub(" ", out)
    out = _LINE_COMMENT.sub("", out)
    out = _TRAILING_COMMA.sub(r"\1", out)
    return re.sub(r"\x00(\d+)\x00", lambda m: masked[int(m.group(1))], out)


def load_jsonc(path: Path | str | None = None) -> dict:
    """Load a JSONC file as a plain dict. Missing file returns {}."""
    p = Path(path) if path is not None else _DEFAULT_PATH
    if not p.exists():
        return {}
    return json.loads(strip_jsonc(p.read_text(encoding="utf-8")))


def default_config_path() -> Path:
    return _DEFAULT_PATH
