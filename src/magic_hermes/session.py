"""Session client: one hermes process's managed connection to the MC daemon.

Wraps SubcClient with the pieces a hermes plugin needs:
- lazy connect with retry policy and a liveness probe
- catalog inspection and an open route to the MC management surface
- managed JSON-RPC-style calls over that route (compaction, search, memory ops)
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from .discovery import discover_connection_file
from .subc.client import BindIdentity, SubcClient, SubcError
from .subc.connection_file import ConnectionFileError

log = logging.getLogger(__name__)

DEFAULT_REQUEST_TIMEOUT_MS = 120_000
CONNECT_RETRY_DELAYS = (0.25, 0.5, 1.0, 2.0)


class SessionUnavailable(RuntimeError):
    """The MC daemon is not reachable — callers degrade gracefully."""


class MagicContextSession:
    """Owns the daemon connection and the route to the MC module."""

    def __init__(
        self,
        project_root: str,
        session_id: str,
        request_timeout_ms: int = DEFAULT_REQUEST_TIMEOUT_MS,
    ) -> None:
        self._project_root = project_root
        self._session_id = session_id
        self._client: SubcClient | None = None
        self._route: Any = None
        self._request_timeout_ms = request_timeout_ms

    # -- lifecycle ---------------------------------------------------------

    def connect(self, retries: int = len(CONNECT_RETRY_DELAYS)) -> None:
        """Discover, connect, and open the MC route. Raises SessionUnavailable."""
        last_err: Exception | None = None
        attempts = max(1, retries)
        for attempt in range(attempts):
            try:
                self._connect_once()
                return
            except (SessionUnavailable, SubcError, ConnectionFileError, OSError) as err:
                last_err = err
                self._teardown()
                if attempt < attempts - 1:
                    delay = CONNECT_RETRY_DELAYS[
                        min(attempt, len(CONNECT_RETRY_DELAYS) - 1)
                    ]
                    log.debug(
                        "mc connect attempt %d failed (%s); retry in %.2fs",
                        attempt + 1,
                        err,
                        delay,
                    )
                    time.sleep(delay)
        raise SessionUnavailable(f"cannot reach magic-context daemon: {last_err}")

    def _connect_once(self) -> None:
        found = discover_connection_file()
        if found is None:
            raise SessionUnavailable(
                "no subc connection file found — is the daemon running? "
                "(set SUBC_CONNECTION_FILE to override)"
            )
        path, info = found
        client = SubcClient.connect(str(path))
        try:
            # Find the MC module in the catalog and open a route to it.
            catalog = client.catalog_list()
            mc = next(
                (e for e in catalog if "mc" in e.module_id or "magic" in e.module_id),
                None,
            )
            if mc is None:
                raise SessionUnavailable(
                    f"no magic-context module in daemon catalog: "
                    f"{[e.module_id for e in catalog]}"
                )
            self._route = client.route_open(
                target={"module_id": mc.module_id},
                identity=BindIdentity(
                    project_root=self._project_root,
                    harness="hermes",
                    session=self._session_id,
                ),
            )
        except Exception:
            client.close()
            raise
        self._client = client

    def close(self) -> None:
        self._teardown()

    def _teardown(self) -> None:
        if self._route is not None:
            try:
                if self._client is not None:
                    self._client.route_close(self._route)
            except Exception:  # noqa: BLE001 - teardown must never raise
                pass
            self._route = None
        if self._client is not None:
            try:
                self._client.close()
            except Exception:  # noqa: BLE001
                pass
            self._client = None

    @property
    def connected(self) -> bool:
        return self._client is not None and self._route is not None

    # -- managed calls -----------------------------------------------------

    def call(
        self, method: str, params: dict | None = None, timeout_ms: int | None = None
    ) -> Any:
        """Invoke an MC management operation over the open route.

        Lazily connects on first use (and reconnects after a teardown) so
        surfaces registered by the plugin work without an explicit
        ``connect()`` call. Lazy (re)connects fail fast — a single attempt —
        because fail-closed callers degrade every call and must not pay the
        full retry backoff each time.
        """
        if not self.connected:
            self.connect(retries=1)
        assert self._client is not None and self._route is not None
        body = json.dumps({"method": method, "params": params or {}}).encode("utf-8")
        raw = self._client.request(
            self._route,
            body,
            timeout_ms=timeout_ms or self._request_timeout_ms,
        )
        reply = raw if isinstance(raw, dict) else json.loads(raw)
        if isinstance(reply, dict) and reply.get("error"):
            raise SubcError(str(reply["error"]))
        return reply.get("result") if isinstance(reply, dict) else reply
