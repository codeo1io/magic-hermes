"""Reader for the subc daemon's published connection file.

Port of ``connection-file.ts``: permission-checked rendezvous record whose
``key`` is the shared transport secret. We refuse to trust a key from a
file other local users can read, exactly as the Rust reader does.
"""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from typing import List

SCHEMA_VERSION = 1
MIN_KEY_LEN = 32
DAEMON_ID_LEN = 16


class ConnectionFileError(Exception):
    pass


@dataclass(frozen=True)
class Endpoint:
    host: str
    port: int


@dataclass(frozen=True)
class ConnectionInfo:
    schema: int
    endpoints: List[Endpoint]
    key: bytes
    daemon_id: bytes
    pid: int
    daemon_ver: str


def _to_bytes(value: object, field: str) -> bytes:
    if not isinstance(value, list) or any(
        not isinstance(n, int) or n < 0 or n > 255 for n in value
    ):
        raise ConnectionFileError(
            f"connection file field '{field}' must be a JSON array of bytes"
        )
    return bytes(value)


def _validate(info: ConnectionInfo) -> None:
    if info.schema != SCHEMA_VERSION:
        raise ConnectionFileError(
            f"unsupported connection file schema {info.schema}; expected {SCHEMA_VERSION}"
        )
    if not info.endpoints:
        raise ConnectionFileError("connection file must include at least one endpoint")
    if len(info.key) < MIN_KEY_LEN:
        raise ConnectionFileError(
            f"connection file key is too short: {len(info.key)} bytes, "
            f"need at least {MIN_KEY_LEN}"
        )
    if len(info.daemon_id) != DAEMON_ID_LEN:
        raise ConnectionFileError(
            f"connection file daemon_id must be {DAEMON_ID_LEN} bytes, "
            f"got {len(info.daemon_id)}"
        )


def _verify_owner_only(path: str) -> None:
    mode = stat.S_IMODE(os.stat(path).st_mode)
    if mode & 0o077:
        raise ConnectionFileError(
            f"connection file {path} has insecure permissions "
            f"0o{mode:o}; expected owner-only 0600"
        )


def read_connection_file(path: str) -> ConnectionInfo:
    """Read, permission-check, and validate a connection file."""
    _verify_owner_only(path)
    with open(path, "r", encoding="utf-8") as fh:
        raw = fh.read()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as err:
        raise ConnectionFileError(
            f"connection file JSON read failed for {path}: {err}"
        ) from err

    endpoints_raw = parsed.get("endpoints")
    if not isinstance(endpoints_raw, list):
        raise ConnectionFileError("connection file 'endpoints' must be an array")
    endpoints = []
    for e in endpoints_raw:
        if (
            not isinstance(e, dict)
            or not isinstance(e.get("host"), str)
            or not isinstance(e.get("port"), int)
        ):
            raise ConnectionFileError(
                "connection file endpoint must be { host: string, port: number }"
            )
        endpoints.append(Endpoint(host=e["host"], port=e["port"]))

    info = ConnectionInfo(
        schema=parsed.get("schema", 0),
        endpoints=endpoints,
        key=_to_bytes(parsed.get("key"), "key"),
        daemon_id=_to_bytes(parsed.get("daemon_id"), "daemon_id"),
        pid=parsed.get("pid", 0),
        daemon_ver=parsed.get("daemon_ver", ""),
    )
    _validate(info)
    return info
