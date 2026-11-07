"""Deadline-bounded tracker sockets and verified tracker-file caching."""

from dataclasses import dataclass
import hashlib
from ipaddress import IPv4Address
import os
from pathlib import Path
import socket
import tempfile

from .protocol import (
    _filename, _md5, _number, protocol_format_request, protocol_parse_reply,
)


@dataclass(frozen=True)
class PeerEntry:
    ip: str
    port: int
    start_byte: int
    end_byte: int
    timestamp: int


@dataclass(frozen=True)
class TrackerFile:
    filename: str
    filesize: int
    md5: str
    peers: tuple
    description: str = ""

    @property
    def identity(self):
        return self.filename, self.filesize, self.md5


def parse_tracker(data):
    """Parse raw verified tracker bytes, ignoring comments and blank lines."""
    fields, peers = {}, []
    for line in data.decode("utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition(":")
        if sep and key in {"Filename", "Filesize", "MD5", "Description"}:
            if key in fields:
                raise ValueError("Duplicate tracker field: " + key)
            fields[key] = value.strip()
        else:
            parts = line.split(":")
            if len(parts) != 5:
                raise ValueError("Invalid tracker peer record")
            ip, port, start, end, stamp = parts
            IPv4Address(ip)
            peers.append(PeerEntry(ip, _number(port, 1, 65535),
                                   _number(start), _number(end), _number(stamp)))
    try:
        name = _filename(fields["Filename"])
        size = _number(fields["Filesize"])
        checksum = _md5(fields["MD5"]).lower()
    except KeyError as exc:
        raise ValueError("Missing tracker metadata") from exc
    if any(not 0 <= p.start_byte <= p.end_byte <= size for p in peers):
        raise ValueError("Peer range exceeds file bounds")
    return TrackerFile(name, size, checksum, tuple(peers), fields.get("Description", ""))


class TrackerClient:
    """One TCP connection per operation; instances can be shared by threads."""

    def __init__(self, host, port, cache_dir, timeout=5.0, max_response=4194304):
        self.host, self.port = str(IPv4Address(host)), _number(port, 1, 65535)
        if not 0 < timeout <= 300 or not 1024 <= max_response <= 67108864:
            raise ValueError("Invalid tracker timeout or response size limit")
        self.cache_dir = Path(cache_dir)
        self.timeout, self.max_response = timeout, max_response

    def local_ip(self):
        # UDP connect selects the route without transmitting application data.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect((self.host, self.port))
            return sock.getsockname()[0]

    def _exchange(self, request):
        wire = protocol_format_request(request).encode("utf-8")
        import time
        deadline = time.monotonic() + self.timeout
        with socket.create_connection((self.host, self.port), self.timeout) as sock:
            sock.settimeout(max(0.001, deadline - time.monotonic()))
            sock.sendall(wire)
            data = bytearray()
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Tracker response deadline elapsed")
                sock.settimeout(remaining)
                block = sock.recv(min(65536, self.max_response + 1 - len(data)))
                if not block:
                    return bytes(data)
                data.extend(block)
                if len(data) > self.max_response:
                    raise ValueError("Tracker response exceeds configured size limit")

    def create_tracker(self, filename, filesize, description, md5, ip, port):
        reply = protocol_parse_reply(self._exchange({
            "type": "create", "filename": filename, "filesize": filesize,
            "description": description, "md5": md5, "ip": ip, "port": port,
        }).decode("utf-8"))
        if reply["type"] != "create" or reply["status"] == "fail":
            raise ValueError("Tracker refused file registration")
        return reply["status"]

    def update_tracker(self, filename, start, end, ip, port):
        reply = protocol_parse_reply(self._exchange({
            "type": "update", "filename": filename, "start_byte": start,
            "end_byte": end, "ip": ip, "port": port,
        }).decode("utf-8"))
        if (reply["type"] != "update" or reply["filename"] != filename
                or reply["status"] != "succ"):
            raise ValueError("Tracker refused range update: " + str(reply))

    def list_files(self):
        lines = self._exchange({"type": "list"}).decode("utf-8").splitlines(True)
        if len(lines) < 2:
            raise ValueError("Incomplete LIST response")
        replies = [protocol_parse_reply(line) for line in lines]
        if replies[0]["type"] != "list_begin" or replies[-1]["type"] != "list_end":
            raise ValueError("Invalid LIST boundaries")
        items = replies[1:-1]
        if len(items) != replies[0]["number"]:
            raise ValueError("LIST count mismatch")
        for index, item in enumerate(items, 1):
            if item["type"] != "list_item" or item["number"] != index:
                raise ValueError("LIST index mismatch")
        return items

    def get_tracker(self, filename):
        """Verify exact payload bytes before parsing or atomic cache replacement."""
        _filename(filename)
        # API takes the shared filename; append .track even for a shared .track file.
        data = self._exchange({"type": "get", "filename": filename + ".track"})
        begin, separator, rest = data.partition(b"\n")
        if not separator or protocol_parse_reply((begin + b"\n").decode())["type"] != "get_begin":
            raise ValueError("Invalid GET beginning")
        # Trailer is the final line. Never decode/re-encode the checksum payload.
        if not rest.endswith(b"\n"):
            raise ValueError("Missing GET trailer terminator")
        split = rest.rfind(b"\n", 0, len(rest) - 1)
        payload, trailer = rest[:split + 1], rest[split + 1:]
        reply = protocol_parse_reply(trailer.decode("ascii"))
        if reply["type"] != "get_end" or hashlib.md5(payload).hexdigest() != reply["md5"].lower():
            raise ValueError("Tracker payload MD5 mismatch")
        info = parse_tracker(payload)
        if info.filename != filename:
            raise ValueError("Tracker filename mismatch")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.cache_dir, delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.cache_dir / (filename + ".track"))
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return info
