"""Byte-for-byte port of subc-protocol's fixed envelope header.

Source of truth: the upstream TypeScript client's ``envelope.ts``, itself a
port of ``crates/subc-protocol/src/lib.rs``. Field offsets, little-endian
encoding, and frame/flag numbering must stay in lock-step with Rust.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

PROTOCOL_VERSION = 2
HEADER_LEN = 21
FROZEN_PREFIX_LEN = 5
MAX_FRAME_BODY_LEN = 64 * 1024 * 1024

# Header layout: len:u32 | ver:u8 | ty:u8 | flags:u8 | channel:u16 |
# epoch:u32 | corr:u64 — all little-endian.
_HEADER = struct.Struct("<IBBBHIQ")
assert _HEADER.size == HEADER_LEN


class FrameType:
    Request = 0
    Response = 1
    Push = 2
    StreamData = 3
    StreamEnd = 4
    Error = 5
    Cancel = 6
    Ping = 7
    Pong = 8
    Hello = 9
    HelloAck = 10
    Goodbye = 11


FRAME_TYPE_MAX = FrameType.Goodbye

_PURE_HEADER_TYPES = {
    FrameType.Cancel,
    FrameType.Ping,
    FrameType.Pong,
    FrameType.Goodbye,
}


def is_pure_header(ty: int) -> bool:
    return ty in _PURE_HEADER_TYPES


class Priority:
    Passive = 0
    Interactive = 1
    Background = 2


class AdmissionClass:
    Normal = 0
    Expedite = 1
    Sheddable = 2


FLAG_BINARY = 0b0000_0001
FLAG_PRIORITY_MASK = 0b0000_0110
FLAG_PRIORITY_SHIFT = 1
FLAG_LAST = 0b0000_1000
FLAG_ADMISSION_MASK = 0b0011_0000
FLAG_ADMISSION_SHIFT = 4
FLAG_RESERVED_MASK = 0b1100_0000


class DecodeError(Exception):
    """Typed envelope decode failure mirroring the Rust wire taxonomy."""

    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code


def build_flags(
    binary: bool,
    priority: int,
    last: bool,
    admission_class: int = AdmissionClass.Normal,
) -> int:
    flags = 0
    if binary:
        flags |= FLAG_BINARY
    flags |= priority << FLAG_PRIORITY_SHIFT
    if last:
        flags |= FLAG_LAST
    flags |= admission_class << FLAG_ADMISSION_SHIFT
    return flags


@dataclass(frozen=True)
class EnvelopeHeader:
    len: int
    ver: int
    ty: int
    flags: int
    channel: int
    epoch: int
    corr: int

    def frame_type_name(self) -> str:
        return _FRAME_TYPE_NAMES.get(self.ty, f"unknown({self.ty})")


_FRAME_TYPE_NAMES = {v: k for k, v in vars(FrameType).items() if not k.startswith("_")}


@dataclass(frozen=True)
class Frame:
    header: EnvelopeHeader
    body: bytes


def encode_header(header: EnvelopeHeader) -> bytes:
    return _HEADER.pack(
        header.len,
        header.ver,
        header.ty,
        header.flags,
        header.channel,
        header.epoch,
        header.corr,
    )


def decode_header(raw: bytes) -> EnvelopeHeader:
    if len(raw) < FROZEN_PREFIX_LEN:
        raise DecodeError(
            f"header shorter than frozen prefix: have {len(raw)} bytes",
            "too_short_for_prefix",
        )
    ver = raw[4]
    if ver != PROTOCOL_VERSION:
        raise DecodeError(f"unsupported envelope version {ver}", "unsupported_version")
    if len(raw) < HEADER_LEN:
        raise DecodeError(
            f"header too short for version: have {len(raw)} bytes, need {HEADER_LEN}",
            "too_short_for_header",
        )
    length, _, ty, flags, channel, epoch, corr = _HEADER.unpack(raw[:HEADER_LEN])
    if ty > FRAME_TYPE_MAX:
        raise DecodeError(f"unknown frame type byte {ty}", "unknown_frame_type")
    if flags & FLAG_RESERVED_MASK:
        raise DecodeError(
            f"reserved flag bits set in flags {flags:08b}", "reserved_flag_bits"
        )
    if (flags & FLAG_PRIORITY_MASK) >> FLAG_PRIORITY_SHIFT == 0b11:
        raise DecodeError(
            f"reserved priority bits set in flags {flags:08b}", "reserved_priority_bits"
        )
    admission = (flags & FLAG_ADMISSION_MASK) >> FLAG_ADMISSION_SHIFT
    if admission == 0b11:
        raise DecodeError(
            f"reserved admission class set in flags {flags:08b}",
            "reserved_admission_class",
        )
    if (
        admission == AdmissionClass.Sheddable
        and ty != FrameType.Push
        and ty != FrameType.StreamData
    ):
        raise DecodeError(
            f"SHEDDABLE admission class is illegal on {_FRAME_TYPE_NAMES.get(ty, ty)} "
            f"in flags {flags:08b}",
            "sheddable_illegal_frame_type",
        )
    if channel == 0 and epoch != 0:
        raise DecodeError(
            f"control channel carried nonzero epoch {epoch}",
            "nonzero_epoch_on_control_channel",
        )
    if is_pure_header(ty) and length != 0:
        raise DecodeError(
            f"pure-header frame {_FRAME_TYPE_NAMES.get(ty, ty)} declared non-zero body "
            f"length {length}",
            "pure_header_frame_with_body",
        )
    return EnvelopeHeader(
        len=length, ver=ver, ty=ty, flags=flags, channel=channel, epoch=epoch, corr=corr
    )


def build_frame(
    ty: int,
    flags: int,
    channel: int,
    epoch: int,
    corr: int,
    body: bytes,
    ver: int = PROTOCOL_VERSION,
) -> Frame:
    if len(body) > MAX_FRAME_BODY_LEN:
        raise DecodeError(
            f"frame body {len(body)} exceeds max {MAX_FRAME_BODY_LEN}",
            "frame_body_too_large",
        )
    header = EnvelopeHeader(
        len=len(body),
        ver=ver,
        ty=ty,
        flags=flags,
        channel=channel,
        epoch=epoch,
        corr=corr,
    )
    # Validate the complete header exactly as the TS client does.
    decode_header(encode_header(header))
    return Frame(header=header, body=body)


def encode_frame(frame: Frame) -> bytes:
    if frame.header.len != len(frame.body):
        raise DecodeError(
            f"frame header length {frame.header.len} does not match body length "
            f"{len(frame.body)}",
            "frame_length_mismatch",
        )
    return encode_header(frame.header) + frame.body
