"""Replaceable chunk transport for Person 2's peer networking implementation."""

import socket

from .models import Peer, safe_filename

MAX_CHUNK_SIZE = 1024


class ChunkError(OSError):
    """A peer could not provide the complete requested chunk."""


class TCPChunkClient:
    """Reference wire contract: <GET filename start end>\n -> <REP GET N>\n + data.

    end is exclusive. Each connection serves one request. An unsuccessful
    server sends <GET invalid>\n and closes. The length header distinguishes
    errors from arbitrary binary data (even data beginning with '<GET').
    Person 2 can instead inject any fetch(peer, filename, start, end) callable.
    """

    def __init__(self, timeout: float = 5.0) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self.timeout = timeout

    def __call__(self, peer: Peer, filename: str, start: int, end: int) -> bytes:
        safe_filename(filename)
        length = end - start
        if start < 0 or not 1 <= length <= MAX_CHUNK_SIZE:
            raise ValueError("chunk length must be between 1 and 1024 bytes")
        if not peer.start_byte <= start < end <= peer.end_byte:
            raise ValueError("peer does not advertise the requested bytes")
        try:
            with socket.create_connection(peer.endpoint, timeout=self.timeout) as sock:
                sock.settimeout(self.timeout)
                sock.sendall(f"<GET {filename} {start} {end}>\n".encode("utf-8"))
                header = bytearray()
                while len(header) < 128 and not header.endswith(b"\n"):
                    byte = sock.recv(1)
                    if not byte:
                        break
                    header.extend(byte)
                if header != f"<REP GET {length}>\n".encode("ascii"):
                    raise ChunkError("peer rejected request or returned an invalid length header")
                # The peer closes after one reply. Read through EOF, bounded
                # to catch oversized replies without unbounded allocation.
                result = bytearray()
                while len(result) <= length:
                    block = sock.recv(length + 1 - len(result))
                    if not block:
                        break
                    result.extend(block)
                if len(result) != length:
                    raise ChunkError(f"peer returned {len(result)} bytes; expected {length}")
                return bytes(result)
        except OSError as exc:
            raise ChunkError(f"chunk {start}:{end} from {peer.ip}:{peer.port}: {exc}") from exc
