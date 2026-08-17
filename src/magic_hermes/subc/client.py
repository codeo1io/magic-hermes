"""Synchronous Python subc client.

Mirrors the canonical pure consumer (subc-probe): authenticate ->
catalog.list (optional) -> route.open -> request on the returned route
channel. There is no client HELLO — HELLO is module-registration only.

Unlike the TS client (single async read loop demuxing many in-flight
requests), this client is synchronous with one request in flight per call:
write the request frame, then read frames until the matching (channel,
corr) terminal arrives, dispatching only frames that belong to other
correlations (none exist while we serialize calls). This matches how the
hermes context engine drives it — one bounded call per lifecycle hook.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

from .auth import AuthError, authenticate_client
from .connection_file import ConnectionFileError, ConnectionInfo, read_connection_file
from .envelope import (
    AdmissionClass,
    Frame,
    FrameType,
    Priority,
    build_flags,
    build_frame,
    encode_frame,
)
from .socket import SocketClosedError, SocketTimeout, SubcSocket

DEFAULT_HANDSHAKE_TIMEOUT_MS = 10_000
DEFAULT_REQUEST_TIMEOUT_MS = 30_000
# Once a header arrives, its body must follow promptly.
BODY_READ_TIMEOUT_S = 30.0


@dataclass(frozen=True)
class BindIdentity:
    project_root: str
    harness: str
    session: str

    def to_dict(self) -> dict:
        return {
            "project_root": self.project_root,
            "harness": self.harness,
            "session": self.session,
        }


@dataclass(frozen=True)
class RouteHandle:
    channel: int
    epoch: int


@dataclass(frozen=True)
class CatalogEntry:
    module_id: str
    roles: list
    control_ops: list


class SubcError(Exception):
    def __init__(self, message: str, code: Optional[str] = None):
        super().__init__(message)
        self.code = code


class RouteClosedError(SubcError):
    def __init__(self, message: str = "route closed"):
        super().__init__(message, "route_closed")


class SubcClient:
    """A connected, authenticated subc client. Not thread-safe: callers
    serialize access (the hermes engine hook path is single-threaded)."""

    def __init__(
        self,
        sock: SubcSocket,
        conn: ConnectionInfo,
        handshake_timeout_ms: int = DEFAULT_HANDSHAKE_TIMEOUT_MS,
        request_timeout_ms: int = DEFAULT_REQUEST_TIMEOUT_MS,
        _skip_auth: bool = False,
    ):
        self._sock = sock
        self.conn = conn
        self._handshake_timeout_ms = handshake_timeout_ms
        self._request_timeout_ms = request_timeout_ms
        self._next_corr = 1
        self._closed = False
        if not _skip_auth:
            authenticate_client(sock._sock, conn)

    # -- lifecycle ---------------------------------------------------------

    @classmethod
    def connect(
        cls,
        connection_file: str,
        handshake_timeout_ms: int = DEFAULT_HANDSHAKE_TIMEOUT_MS,
        request_timeout_ms: int = DEFAULT_REQUEST_TIMEOUT_MS,
    ) -> SubcClient:
        """Read the connection file, connect, authenticate."""
        conn = read_connection_file(connection_file)
        endpoint = conn.endpoints[0]
        sock = SubcSocket.connect(endpoint.host, endpoint.port, handshake_timeout_ms)
        try:
            client = cls(sock, conn, handshake_timeout_ms, request_timeout_ms)
        except Exception:
            sock.close()
            raise
        return client

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._sock.close()

    # -- control plane -------------------------------------------------------

    def catalog_list(self, module_id: Optional[str] = None) -> list[CatalogEntry]:
        op: dict[str, Any] = {"op": "catalog.list"}
        if module_id is not None:
            op["module_id"] = module_id
        reply = self._control_rpc(op)
        return [
            CatalogEntry(
                m.get("module_id", ""), m.get("roles", []), m.get("control_ops", [])
            )
            for m in reply.get("modules", [])
        ]

    def route_open(
        self,
        target: dict,
        identity: BindIdentity,
        timeout_ms: Optional[int] = None,
    ) -> RouteHandle:
        body = {"op": "route.open", "target": target, "identity": identity.to_dict()}
        reply = self._control_rpc(body, timeout_ms=timeout_ms)
        channel = reply.get("route_channel")
        epoch = reply.get("route_epoch")
        if not isinstance(channel, int) or not isinstance(epoch, int):
            raise SubcError(f"route.open returned no route handle: {reply!r}")
        return RouteHandle(channel=channel, epoch=epoch)

    def route_close(self, handle: RouteHandle) -> None:
        """GOODBYE the route channel. Best-effort; failures are ignored —
        the daemon reaps routes when the connection drops."""
        goodbye = build_frame(
            FrameType.Goodbye,
            build_flags(False, Priority.Interactive, False),
            handle.channel,
            handle.epoch,
            0,
            b"",
        )
        try:
            self._sock.write(encode_frame(goodbye))
        except (SocketClosedError, SocketTimeout):
            pass

    # -- data plane ----------------------------------------------------------

    def request(
        self,
        handle: RouteHandle,
        body: Any,
        timeout_ms: Optional[int] = None,
        on_progress: Optional[Callable[[bytes], None]] = None,
    ) -> Any:
        """Send a data-plane request on the route and await its terminal reply."""
        payload = body if isinstance(body, (bytes, bytearray)) else _encode_json(body)
        corr = self._alloc_corr()
        frame = build_frame(
            FrameType.Request,
            build_flags(False, Priority.Interactive, False, AdmissionClass.Normal),
            handle.channel,
            handle.epoch,
            corr,
            bytes(payload),
        )
        self._sock.write(encode_frame(frame))
        reply = self._read_reply(handle, corr, timeout_ms, on_progress)
        if reply.header.ty == FrameType.Error:
            raise _error_from_frame(reply)
        return _decode_json(reply.body)

    def call(
        self,
        module_id: str,
        method: str,
        params: Any = None,
        identity: Optional[BindIdentity] = None,
        target_kind: str = "management_surface",
        timeout_ms: Optional[int] = None,
    ) -> Any:
        """Managed convenience: route.open to the module then request."""
        if identity is None:
            raise SubcError("managed call requires a BindIdentity", "missing_identity")
        target = {"kind": target_kind, "module_id": module_id}
        handle = self.route_open(target, identity, timeout_ms=timeout_ms)
        try:
            body = (
                {"method": method}
                if params is None
                else {"method": method, "params": params}
            )
            return self.request(handle, body, timeout_ms=timeout_ms)
        finally:
            self.route_close(handle)

    # -- internals -----------------------------------------------------------

    def _control_rpc(self, body: dict, timeout_ms: Optional[int] = None) -> dict:
        corr = self._alloc_corr()
        frame = build_frame(
            FrameType.Request,
            build_flags(False, Priority.Interactive, False, AdmissionClass.Normal),
            0,
            0,
            corr,
            _encode_json(body),
        )
        self._sock.write(encode_frame(frame))
        reply = self._read_reply(None, corr, timeout_ms, None)
        if reply.header.ty == FrameType.Error:
            raise _error_from_frame(reply)
        return _decode_json(reply.body)

    def _read_reply(
        self,
        handle: Optional[RouteHandle],
        corr: int,
        timeout_ms: Optional[int],
        on_progress: Optional[Callable[[bytes], None]],
    ) -> Frame:
        ms = timeout_ms if timeout_ms is not None else self._request_timeout_ms
        deadline = time.monotonic() + ms / 1000.0
        channel = handle.channel if handle else 0
        epoch = handle.epoch if handle else 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SocketTimeout(
                    f"request on channel {channel} corr {corr} timed out after {ms}ms"
                )
            frame = self._sock.read_frame(
                header_deadline=deadline, body_timeout_s=remaining
            )
            if frame.header.channel != channel or frame.header.epoch != epoch:
                continue  # not ours (stale route traffic) — keep waiting
            if frame.header.corr != corr:
                continue
            ty = frame.header.ty
            if ty in (FrameType.Push, FrameType.StreamData):
                if on_progress:
                    on_progress(frame.body)
                continue
            if ty in (FrameType.Response, FrameType.StreamEnd, FrameType.Error):
                return frame
            if ty == FrameType.Goodbye and handle is not None:
                raise RouteClosedError("route closed by subc (GOODBYE)")
            # Cancel/Ping/Pong/Hello etc. — ignore

    def _alloc_corr(self) -> int:
        corr = self._next_corr
        self._next_corr += 1
        return corr


def _encode_json(value: Any) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode("utf-8")


def _decode_json(body: bytes) -> Any:
    if not body:
        return None
    try:
        return json.loads(body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return body


def _error_from_frame(frame: Frame) -> SubcError:
    try:
        parsed = json.loads(frame.body.decode("utf-8"))
        return SubcError(parsed.get("message", "subc error"), parsed.get("code"))
    except (json.JSONDecodeError, UnicodeDecodeError, AttributeError):
        return SubcError(frame.body.decode("utf-8", "replace") or "subc error")
