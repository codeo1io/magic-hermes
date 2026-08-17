"""A scripted in-process subc daemon for wire-level tests.

Implements just enough of the protocol: the auth handshake, channel-0
catalog.list / route.open control ops, and an echo responder on opened
routes. Scriptable faults (bad key, mid-handshake close, silent drop)
drive the client's error-path tests.
"""

from __future__ import annotations

import hmac
import json
import os
import secrets
import socket
import struct
import threading
import hashlib
from typing import Any, Optional

from magic_hermes.subc.envelope import (
    FrameType,
    Priority,
    build_flags,
    build_frame,
    decode_header,
    encode_frame,
)
from magic_hermes.subc.auth import (
    CLIENT_AUTH_DOMAIN,
    SERVER_PROOF_DOMAIN,
    compute_proof,
)


class FakeSubcDaemon:
    def __init__(
        self,
        *,
        bad_key: bool = False,
        close_after_hello: bool = False,
        drop_requests: bool = False,
        daemon_ver: str = "fake-1.0",
        modules: list[dict] | None = None,
    ):
        self.key = secrets.token_bytes(32)
        self.daemon_id = secrets.token_bytes(16)
        self.daemon_ver = daemon_ver
        self._bad_key = bad_key
        self._close_after_hello = close_after_hello
        self._drop_requests = drop_requests
        self._modules = (
            modules
            if modules is not None
            else [
                {"module_id": "mc.core", "ops": ["compact", "search", "memory"]},
            ]
        )
        self._scripts: dict[str, dict] = {}
        self._script_errors: dict[str, str] = {}
        self._next_channel = 1
        self._lock = threading.Lock()
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind(("127.0.0.1", 0))
        self._server.listen(4)
        self.port = self._server.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        self.requests_seen: list[dict] = []

    @property
    def calls(self) -> list[dict]:
        """Route payloads seen so far (method/params dicts)."""
        with self._lock:
            return list(self.requests_seen)

    def script(self, method: str, result: dict) -> None:
        """Serve ``result`` as the reply for route calls to ``method``."""
        self._scripts[method] = result

    def script_error(self, method: str, message: str) -> None:
        """Fail route calls to ``method`` with an error reply."""
        self._script_errors[method] = message

    # -- test plumbing -----------------------------------------------------

    def connection_file(self, tmp_path, *, insecure_perms: bool = False) -> str:
        path = str(tmp_path / "subc-connection.json")
        payload = {
            "schema": 1,
            "endpoints": [{"host": "127.0.0.1", "port": self.port}],
            "key": list(self.key),
            "daemon_id": list(self.daemon_id),
            "pid": os.getpid(),
            "daemon_ver": self.daemon_ver,
        }
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        os.chmod(path, 0o644 if insecure_perms else 0o600)
        return path

    def start(self) -> None:
        """No-op: the serve thread starts in the constructor."""

    def stop(self) -> None:
        try:
            self._server.close()
        except OSError:
            pass
        self._thread.join(timeout=2)

    # -- daemon ------------------------------------------------------------

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self._server.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn: socket.socket) -> None:
        try:
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            key = secrets.token_bytes(32) if self._bad_key else self.key
            if not self._handshake(conn, key):
                return
            self._frame_loop(conn)
        except OSError:
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def _recv_exact(self, conn: socket.socket, n: int) -> bytes:
        buf = bytearray()
        while len(buf) < n:
            chunk = conn.recv(n - len(buf))
            if not chunk:
                raise OSError("client closed")
            buf.extend(chunk)
        return bytes(buf)

    def _read_msg(self, conn: socket.socket) -> dict:
        length = struct.unpack("<I", self._recv_exact(conn, 4))[0]
        return json.loads(self._recv_exact(conn, length))

    def _write_msg(self, conn: socket.socket, value: dict) -> None:
        payload = json.dumps(value, separators=(",", ":")).encode("utf-8")
        conn.sendall(struct.pack("<I", len(payload)) + payload)

    def _handshake(self, conn: socket.socket, key: bytes) -> bool:
        conn.settimeout(10)
        hello = self._read_msg(conn)
        client_nonce = bytes(hello["client_nonce"])
        if self._close_after_hello:
            conn.close()
            return False
        server_nonce = secrets.token_bytes(32)
        proof = compute_proof(
            key, SERVER_PROOF_DOMAIN, client_nonce, server_nonce, self.daemon_id
        )
        self._write_msg(
            conn,
            {
                "daemon_id": list(self.daemon_id),
                "server_nonce": list(server_nonce),
                "daemon_ver": self.daemon_ver,
                "server_proof": list(proof),
            },
        )
        auth = self._read_msg(conn)
        expected = compute_proof(
            key, CLIENT_AUTH_DOMAIN, client_nonce, server_nonce, self.daemon_id
        )
        if not hmac.compare_digest(expected, bytes(auth["client_auth"])):
            conn.close()
            return False
        conn.settimeout(None)  # handshake deadline must not leak into the frame loop
        return True

    def _frame_loop(self, conn: socket.socket) -> None:
        while True:
            header = decode_header(self._recv_exact(conn, 21))
            body = self._recv_exact(conn, header.len) if header.len else b""
            if header.ty != FrameType.Request:
                continue
            payload = json.loads(body) if body else {}
            with self._lock:
                self.requests_seen.append(payload)
            if self._drop_requests:
                continue
            if header.channel == 0:
                self._control(conn, header.corr, payload)
            else:
                method = payload.get("method", "")
                if method in self._script_errors:
                    self._respond(
                        conn,
                        header.channel,
                        header.epoch,
                        header.corr,
                        {
                            "error": {
                                "code": "internal",
                                "message": self._script_errors[method],
                            }
                        },
                    )
                elif method in self._scripts:
                    self._respond(
                        conn,
                        header.channel,
                        header.epoch,
                        header.corr,
                        {"result": self._scripts[method]},
                    )
                else:
                    self._respond(
                        conn,
                        header.channel,
                        header.epoch,
                        header.corr,
                        {"result": {"echo": payload}},
                    )

    def _control(self, conn: socket.socket, corr: int, payload: dict) -> None:
        op = payload.get("op")
        if op == "catalog.list":
            self._respond(
                conn,
                0,
                0,
                corr,
                {
                    "op": "catalog.list",
                    "modules": self._modules,
                },
            )
        elif op == "route.open":
            with self._lock:
                channel = self._next_channel
                self._next_channel += 1
            self._respond(
                conn, 0, 0, corr, {"route_channel": channel, "route_epoch": 1}
            )
        else:
            self._error(conn, 0, 0, corr, "unknown_op", f"unsupported op {op!r}")

    def _respond(
        self, conn: socket.socket, channel: int, epoch: int, corr: int, value: dict
    ) -> None:
        frame = build_frame(
            FrameType.Response,
            build_flags(False, Priority.Interactive, False),
            channel,
            epoch,
            corr,
            json.dumps(value, separators=(",", ":")).encode("utf-8"),
        )
        conn.sendall(encode_frame(frame))

    def _error(
        self,
        conn: socket.socket,
        channel: int,
        epoch: int,
        corr: int,
        code: str,
        message: str,
    ) -> None:
        frame = build_frame(
            FrameType.Error,
            build_flags(False, Priority.Interactive, False),
            channel,
            epoch,
            corr,
            json.dumps({"code": code, "message": message}).encode("utf-8"),
        )
        conn.sendall(encode_frame(frame))
