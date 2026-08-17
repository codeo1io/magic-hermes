"""Daemon discovery: locate the subc daemon's connection file.

The TS client takes the connection-file path from its caller; there is no
hardcoded default. We resolve in this order:

1. ``SUBC_CONNECTION_FILE`` environment variable (explicit override)
2. ``$XDG_RUNTIME_DIR/cortexkit/subc-connection.json``
3. ``~/.local/state/cortexkit/subc-connection.json``
4. ``~/.local/share/cortexkit/subc-connection.json``
"""

from __future__ import annotations

import os
from pathlib import Path

from .subc.connection_file import ConnectionFileError, read_connection_file

ENV_VAR = "SUBC_CONNECTION_FILE"


def candidate_paths() -> list[Path]:
    paths: list[Path] = []
    env = os.environ.get(ENV_VAR)
    if env:
        paths.append(Path(env))
        return paths  # explicit override: no fallbacks
    xdg = os.environ.get("XDG_RUNTIME_DIR")
    if xdg:
        paths.append(Path(xdg) / "cortexkit" / "subc-connection.json")
    home = Path.home()
    paths.append(home / ".local" / "state" / "cortexkit" / "subc-connection.json")
    paths.append(home / ".local" / "share" / "cortexkit" / "subc-connection.json")
    # magic-hermes' own bridge daemon publishes its connection file as
    # subc-hermes.json in the hermes runtime dir (see bridge/daemon.mjs).
    # Without these candidates the shipped daemon is only discoverable via
    # SUBC_CONNECTION_FILE.
    hermes_dir = home / ".hermes"
    paths.append(hermes_dir / "subc-hermes.json")
    paths.append(hermes_dir / "runtime" / "subc-hermes.json")
    return paths


def discover_connection_file() -> tuple[Path, "object"] | None:
    """Return (path, ConnectionInfo) for the first readable candidate, else None.

    A candidate that exists but fails validation raises ConnectionFileError —
    a broken rendezvous record is a loud failure, not a skip-to-next.
    """
    for path in candidate_paths():
        if not path.exists():
            continue
        info = read_connection_file(path)
        return path, info
    return None


def daemon_available() -> bool:
    """True when a valid connection file is discoverable."""
    try:
        return discover_connection_file() is not None
    except ConnectionFileError:
        return False


__all__ = [
    "ENV_VAR",
    "candidate_paths",
    "discover_connection_file",
    "daemon_available",
    "ConnectionFileError",
]
