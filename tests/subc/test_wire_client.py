"""U1 wire-level tests: envelope, connection file, handshake, request flow."""

import json
import socket
import threading
import time

import pytest

from magic_hermes.subc import (
    AuthError,
    BindIdentity,
    ConnectionFileError,
    DecodeError,
    FrameType,
    SocketClosedError,
    SocketTimeout,
    SubcClient,
    build_flags,
    build_frame,
    decode_header,
    encode_frame,
    encode_header,
)
from magic_hermes.subc.envelope import EnvelopeHeader, HEADER_LEN, MAX_FRAME_BODY_LEN
from tests.subc.fake_daemon import FakeSubcDaemon


def make_identity() -> BindIdentity:
    return BindIdentity(
        project_root="/tmp/proj", harness="hermes", session="test-session"
    )


# -- envelope round-trips ---------------------------------------------------


class TestEnvelope:
    def test_header_is_21_bytes_round_trip(self):
        frame = build_frame(
            FrameType.Request, build_flags(False, 1, False), 3, 5, 42, b"hello"
        )
        raw = encode_frame(frame)
        assert len(raw) == HEADER_LEN + 5
        header = decode_header(raw[:HEADER_LEN])
        assert (header.len, header.ty, header.channel, header.epoch, header.corr) == (
            5,
            FrameType.Request,
            3,
            5,
            42,
        )

    def test_empty_body_and_large_lengths(self):
        empty = build_frame(
            FrameType.Goodbye, build_flags(False, 1, False), 3, 5, 0, b""
        )
        assert len(encode_frame(empty)) == HEADER_LEN
        body = b"x" * 1024
        big = build_frame(
            FrameType.Request, build_flags(False, 1, False), 1, 1, 7, body
        )
        assert decode_header(encode_frame(big)[:HEADER_LEN]).len == 1024

    def test_frame_body_too_large_rejected(self):
        with pytest.raises(DecodeError) as exc:
            build_frame(
                FrameType.Request,
                build_flags(False, 1, False),
                1,
                1,
                1,
                b"x" * (MAX_FRAME_BODY_LEN + 1),
            )
        assert exc.value.code == "frame_body_too_large"

    def test_unsupported_version_rejected(self):
        raw = encode_header(
            EnvelopeHeader(len=0, ver=99, ty=0, flags=0, channel=0, epoch=0, corr=0)
        )
        with pytest.raises(DecodeError) as exc:
            decode_header(raw)
        assert exc.value.code == "unsupported_version"

    def test_reserved_flag_bits_rejected(self):
        raw = bytearray(
            encode_header(
                EnvelopeHeader(
                    len=0, ver=2, ty=0, flags=0b1100_0000, channel=0, epoch=0, corr=0
                )
            )
        )
        with pytest.raises(DecodeError) as exc:
            decode_header(bytes(raw))
        assert exc.value.code == "reserved_flag_bits"

    def test_control_channel_nonzero_epoch_rejected(self):
        raw = encode_header(
            EnvelopeHeader(len=0, ver=2, ty=0, flags=0, channel=0, epoch=9, corr=0)
        )
        with pytest.raises(DecodeError) as exc:
            decode_header(raw)
        assert exc.value.code == "nonzero_epoch_on_control_channel"

    def test_pure_header_frame_with_body_rejected(self):
        raw = encode_header(
            EnvelopeHeader(
                len=4, ver=2, ty=FrameType.Ping, flags=0, channel=0, epoch=0, corr=0
            )
        )
        with pytest.raises(DecodeError) as exc:
            decode_header(raw)
        assert exc.value.code == "pure_header_frame_with_body"

    def test_length_mismatch_rejected_on_encode(self):
        frame = build_frame(
            FrameType.Request, build_flags(False, 1, False), 1, 1, 1, b"abc"
        )
        with pytest.raises(DecodeError):
            encode_frame(
                type(frame)(
                    header=type(frame.header)(**{**vars(frame.header), "len": 99}),
                    body=frame.body,
                )
            )


# -- connection file --------------------------------------------------------


class TestConnectionFile:
    def test_valid_file_parses(self, tmp_path):
        daemon = FakeSubcDaemon()
        try:
            path = daemon.connection_file(tmp_path)
            conn = magic_hermes_read(path)
            assert conn.daemon_ver == "fake-1.0"
            assert len(conn.key) == 32
            assert len(conn.daemon_id) == 16
        finally:
            daemon.stop()

    def test_insecure_permissions_rejected(self, tmp_path):
        daemon = FakeSubcDaemon()
        try:
            path = daemon.connection_file(tmp_path, insecure_perms=True)
            with pytest.raises(ConnectionFileError, match="insecure permissions"):
                magic_hermes_read(path)
        finally:
            daemon.stop()

    def test_bad_schema_rejected(self, tmp_path):
        daemon = FakeSubcDaemon()
        try:
            path = daemon.connection_file(tmp_path)
            data = json.load(open(path))
            data["schema"] = 99
            json.dump(data, open(path, "w"))
            with pytest.raises(ConnectionFileError, match="schema"):
                magic_hermes_read(path)
        finally:
            daemon.stop()


def magic_hermes_read(path):
    from magic_hermes.subc import read_connection_file

    return read_connection_file(path)


# -- handshake and request flow ---------------------------------------------


class TestClientFlow:
    def test_connect_auth_catalog_route_echo(self, tmp_path):
        daemon = FakeSubcDaemon()
        try:
            client = SubcClient.connect(daemon.connection_file(tmp_path))
            try:
                modules = client.catalog_list()
                assert modules[0].module_id == "mc.core"

                handle = client.route_open(
                    {"kind": "management_surface", "module_id": "mc.core"},
                    make_identity(),
                )
                result = client.request(handle, {"method": "ping", "params": {"x": 1}})
                assert result == {"echo": {"method": "ping", "params": {"x": 1}}}
                client.route_close(handle)

                managed = client.call(
                    "mc.core", "status", {"a": True}, identity=make_identity()
                )
                assert managed["echo"]["method"] == "status"
            finally:
                client.close()
            assert daemon.requests_seen[0]["op"] == "catalog.list"
        finally:
            daemon.stop()

    def test_wrong_key_fails_auth(self, tmp_path):
        daemon = FakeSubcDaemon(bad_key=True)
        try:
            with pytest.raises(AuthError, match="server proof mismatch"):
                SubcClient.connect(daemon.connection_file(tmp_path))
        finally:
            daemon.stop()

    def test_close_mid_handshake_raises_auth_error(self, tmp_path):
        daemon = FakeSubcDaemon(close_after_hello=True)
        try:
            with pytest.raises((AuthError, SocketClosedError)):
                SubcClient.connect(daemon.connection_file(tmp_path))
        finally:
            daemon.stop()

    def test_unanswered_request_times_out(self, tmp_path):
        daemon = FakeSubcDaemon(drop_requests=True)
        try:
            client = SubcClient.connect(daemon.connection_file(tmp_path))
            try:
                with pytest.raises(SocketTimeout):
                    handle = client.route_open(
                        {"kind": "management_surface", "module_id": "mc.core"},
                        make_identity(),
                        timeout_ms=500,
                    )
                    client.request(handle, {"method": "ping"}, timeout_ms=500)
            finally:
                client.close()
        finally:
            daemon.stop()

    def test_connect_refused_times_out_cleanly(self, tmp_path):
        # Bind then immediately close a port to get a likely-free port.
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()
        daemon = FakeSubcDaemon()
        try:
            path = daemon.connection_file(tmp_path)
            data = json.load(open(path))
            data["endpoints"][0]["port"] = port
            json.dump(data, open(path, "w"))
            with pytest.raises((SocketTimeout, OSError)):
                SubcClient.connect(path, handshake_timeout_ms=800)
        finally:
            daemon.stop()
