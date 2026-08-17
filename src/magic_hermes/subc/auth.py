"""Port of subc-transport's auth.rs client handshake.

The proof construction, domain strings, message framing, and verification
order must match the Rust byte-for-byte: a single byte of drift fails
authentication outright.

Handshake (client side):
  1. send ClientHello { client_nonce, role }
  2. receive ServerProof { daemon_id, server_nonce, daemon_ver, server_proof }
  3. verify server_proof == HMAC(key, "subc-server-v1" || cn || sn || did)
     (constant-time) and daemon_id == the id from the connection file
  4. send ClientAuth { client_auth = HMAC(key, "subc-client-v1" || cn || sn || did) }

Each message on the wire is a 4-byte little-endian length prefix followed by
the JSON body. Byte arrays serialize as JSON arrays of numbers, matching
serde's default for [u8; N].
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import socket
import struct

from .connection_file import ConnectionInfo

NONCE_LEN = 32
PROOF_LEN = 32
MAX_AUTH_MESSAGE_LEN = 4096
SERVER_PROOF_DOMAIN = "subc-server-v1"
CLIENT_AUTH_DOMAIN = "subc-client-v1"
DEFAULT_CLIENT_ROLE = "client"


class AuthError(Exception):
    pass


def compute_proof(
    key: bytes, domain: str, client_nonce: bytes, server_nonce: bytes, daemon_id: bytes
) -> bytes:
    mac = hmac.new(key, digestmod=hashlib.sha256)
    mac.update(domain.encode("utf-8"))
    mac.update(client_nonce)
    mac.update(server_nonce)
    mac.update(daemon_id)
    return mac.digest()


def _constant_time_eq(a: bytes, b: bytes) -> bool:
    return hmac.compare_digest(a, b)


def _write_message(sock: socket.socket, value: object) -> None:
    payload = json.dumps(value, separators=(",", ":")).encode("utf-8")
    if len(payload) > MAX_AUTH_MESSAGE_LEN:
        raise AuthError(
            f"auth message too large: {len(payload)} > {MAX_AUTH_MESSAGE_LEN}"
        )
    sock.sendall(struct.pack("<I", len(payload)) + payload)


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise AuthError("connection closed mid-handshake")
        buf.extend(chunk)
    return bytes(buf)


def _read_message(sock: socket.socket) -> dict:
    length = struct.unpack("<I", _recv_exact(sock, 4))[0]
    if length > MAX_AUTH_MESSAGE_LEN:
        raise AuthError(f"auth message too large: {length} > {MAX_AUTH_MESSAGE_LEN}")
    body = _recv_exact(sock, length) if length else b""
    try:
        parsed = json.loads(body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as err:
        raise AuthError(f"auth message JSON decode failed: {err}") from err
    if not isinstance(parsed, dict):
        raise AuthError("auth message must be a JSON object")
    return parsed


def authenticate_client(sock: socket.socket, conn: ConnectionInfo) -> str:
    """Run the client handshake over an already-connected socket.

    Returns the daemon version string on success; raises AuthError on any
    proof/identity mismatch or framing fault. Socket timeouts bound the
    exchange (caller sets them before connecting).
    """
    client_nonce = os.urandom(NONCE_LEN)

    _write_message(
        sock, {"client_nonce": list(client_nonce), "role": DEFAULT_CLIENT_ROLE}
    )

    proof = _read_message(sock)
    try:
        server_nonce = bytes(proof["server_nonce"])
        daemon_id = bytes(proof["daemon_id"])
        server_proof = bytes(proof["server_proof"])
        daemon_ver = proof.get("daemon_ver", "")
    except (KeyError, TypeError, ValueError) as err:
        raise AuthError(f"malformed ServerProof message: {err}") from err

    expected = compute_proof(
        conn.key, SERVER_PROOF_DOMAIN, client_nonce, server_nonce, daemon_id
    )
    if not _constant_time_eq(expected, server_proof):
        raise AuthError("server proof mismatch — wrong key or impostor daemon")
    if not _constant_time_eq(daemon_id, conn.daemon_id):
        raise AuthError(
            "daemon id mismatch — connection file points at a different daemon"
        )

    client_auth = compute_proof(
        conn.key, CLIENT_AUTH_DOMAIN, client_nonce, server_nonce, daemon_id
    )
    _write_message(sock, {"client_auth": list(client_auth)})
    return daemon_ver
