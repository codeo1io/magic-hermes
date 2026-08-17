"""Python subc wire client — byte-compatible port of @cortexkit/subc-client."""

from .auth import AuthError, authenticate_client
from .client import (
    BindIdentity,
    CatalogEntry,
    RouteClosedError,
    RouteHandle,
    SubcClient,
    SubcError,
)
from .connection_file import (
    ConnectionFileError,
    ConnectionInfo,
    Endpoint,
    read_connection_file,
)
from .envelope import (
    AdmissionClass,
    DecodeError,
    EnvelopeHeader,
    Frame,
    FrameType,
    Priority,
    build_flags,
    build_frame,
    decode_header,
    encode_frame,
    encode_header,
)
from .socket import SocketClosedError, SocketTimeout, SubcSocket

__all__ = [
    "AuthError",
    "authenticate_client",
    "BindIdentity",
    "CatalogEntry",
    "RouteClosedError",
    "RouteHandle",
    "SubcClient",
    "SubcError",
    "ConnectionFileError",
    "ConnectionInfo",
    "Endpoint",
    "read_connection_file",
    "AdmissionClass",
    "DecodeError",
    "EnvelopeHeader",
    "Frame",
    "FrameType",
    "Priority",
    "build_flags",
    "build_frame",
    "decode_header",
    "encode_frame",
    "encode_header",
    "SocketClosedError",
    "SocketTimeout",
    "SubcSocket",
]
