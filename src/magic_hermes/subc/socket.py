"""Synchronous buffered socket wrapper over subc's envelope framing.

Python port of ``socket.ts``: deadline-bounded reads of exact byte counts,
frame reads that validate the frozen 5-byte prefix before waiting for the
rest of the header, and TCP_NODELAY on connect (matching the TS client).
"""

from __future__ import annotations

import socket
import time

from .envelope import (
    FROZEN_PREFIX_LEN,
    HEADER_LEN,
    MAX_FRAME_BODY_LEN,
    DecodeError,
    EnvelopeHeader,
    Frame,
    decode_header,
)


class SocketClosedError(Exception):
    pass


class SocketTimeout(Exception):
    pass


class SubcSocket:
    def __init__(self, sock: socket.socket):
        self._sock = sock

    @property
    def local_port(self) -> int | None:
        addr = self._sock.getsockname()
        return addr[1] if isinstance(addr, tuple) else None

    @classmethod
    def connect(cls, host: str, port: int, timeout_ms: int) -> SubcSocket:
        deadline = time.monotonic() + timeout_ms / 1000.0
        try:
            sock = socket.create_connection(
                (host, port), timeout=max(0.001, timeout_ms / 1000.0)
            )
        except OSError as err:
            raise SocketTimeout(
                f"timed out connecting to {host}:{port}: {err}"
            ) from err
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        if time.monotonic() > deadline:
            sock.close()
            raise SocketTimeout(f"timed out connecting to {host}:{port}")
        return cls(sock)

    def read_exact(self, n: int, deadline: float) -> bytes:
        """Read exactly ``n`` bytes before ``deadline`` (monotonic seconds)."""
        buf = bytearray()
        while len(buf) < n:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SocketTimeout(f"timed out waiting for {n} bytes")
            self._sock.settimeout(remaining)
            try:
                chunk = self._sock.recv(n - len(buf))
            except socket.timeout as err:
                raise SocketTimeout(f"timed out waiting for {n} bytes") from err
            except OSError as err:
                raise SocketClosedError(f"subc connection read failed: {err}") from err
            if not chunk:
                raise SocketClosedError("subc closed the connection")
            buf.extend(chunk)
        return bytes(buf)

    def read_frame(
        self,
        header_deadline: float | None = None,
        body_timeout_s: float = 30.0,
    ) -> Frame:
        """Read one envelope frame.

        ``header_deadline`` of ``None`` waits indefinitely for the header
        (background use); the body budget starts from header arrival.
        """
        if header_deadline is None:
            # One recv with no deadline, then bounded body reads.
            prefix = self._recv_prefix_blocking(FROZEN_PREFIX_LEN)
        else:
            prefix = self.read_exact(FROZEN_PREFIX_LEN, header_deadline)
        if prefix[4] != 2:  # PROTOCOL_VERSION
            raise DecodeError(
                f"unsupported envelope version {prefix[4]}", "unsupported_version"
            )
        deadline = (
            header_deadline
            if header_deadline is not None
            else time.monotonic() + body_timeout_s
        )
        remainder = self.read_exact(HEADER_LEN - FROZEN_PREFIX_LEN, deadline)
        header = decode_header(prefix + remainder)
        if header.len > MAX_FRAME_BODY_LEN:
            raise DecodeError(
                f"frame body {header.len} exceeds max {MAX_FRAME_BODY_LEN}",
                "frame_body_too_large",
            )
        body_deadline = time.monotonic() + body_timeout_s
        body = self.read_exact(header.len, body_deadline) if header.len else b""
        return Frame(header=header, body=body)

    def _recv_prefix_blocking(self, n: int) -> bytes:
        buf = bytearray()
        while len(buf) < n:
            self._sock.settimeout(None)
            try:
                chunk = self._sock.recv(n - len(buf))
            except OSError as err:
                raise SocketClosedError(f"subc connection read failed: {err}") from err
            if not chunk:
                raise SocketClosedError("subc closed the connection")
            buf.extend(chunk)
        return bytes(buf)

    def write(self, data: bytes, timeout_ms: int = 30_000) -> None:
        deadline = time.monotonic() + timeout_ms / 1000.0
        self._sock.settimeout(max(0.001, timeout_ms / 1000.0))
        try:
            self._sock.sendall(data)
        except OSError as err:
            if time.monotonic() > deadline:
                raise SocketTimeout("timed out writing to subc") from err
            raise SocketClosedError(f"subc connection write failed: {err}") from err

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass
