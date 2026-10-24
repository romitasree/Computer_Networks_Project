"""Validated download metadata; byte ranges everywhere are [start, end)."""

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any, Tuple


def safe_filename(name: str) -> str:
    """Only one protocol-safe path component may come from a tracker."""
    if (not name or name in {".", ".."} or any(c in name for c in '/\\<>:')
            or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in name)):
        raise ValueError("filename must be a single name without whitespace or protocol delimiters")
    return name


@dataclass(frozen=True)
class Peer:
    ip: str
    port: int
    start_byte: int
    end_byte: int
    timestamp: int

    def __post_init__(self) -> None:
        if not self.ip or not 1 <= self.port <= 65535:
            raise ValueError("invalid peer address or port")
        if self.start_byte < 0 or self.end_byte < self.start_byte or self.timestamp < 0:
            raise ValueError("invalid peer range or timestamp")

    @property
    def endpoint(self) -> Tuple[str, int]:
        return self.ip, self.port


@dataclass(frozen=True)
class FileInfo:
    filename: str
    filesize: int
    md5: str
    peers: Tuple[Peer, ...] = ()

    def __post_init__(self) -> None:
        safe_filename(self.filename)
        if self.filesize < 0 or not re.fullmatch(r"[0-9a-fA-F]{32}", self.md5):
            raise ValueError("invalid file size or MD5")
        object.__setattr__(self, "md5", self.md5.lower())
        object.__setattr__(self, "peers", tuple(self.peers))
        if any(p.end_byte > self.filesize for p in self.peers):
            raise ValueError("peer range exceeds file size")

    @classmethod
    def from_tracker(cls, tracker: Any) -> "FileInfo":
        """Adapt Person 4's parsed object (including lohita's TrackerFile)."""
        return cls(str(tracker.filename), int(tracker.filesize), str(tracker.md5), tuple(
            Peer(str(p.ip), int(p.port), int(p.start_byte), int(p.end_byte), int(p.timestamp))
            for p in tracker.peers
        ))

    @classmethod
    def from_cache(cls, path: Path) -> "FileInfo":
        """Read a previously verified, raw .track file for CLI/restart recovery.

        Tracker response framing/checksum verification belongs to Person 4.
        This loader never accepts <REP GET ...> wrappers as tracker contents.
        """
        fields = {}
        peers = []
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key, sep, value = line.partition(":")
            if sep and key in {"Filename", "Filesize", "Description", "MD5"}:
                if key in fields:
                    raise ValueError("duplicate tracker field: " + key)
                fields[key] = value.strip()
            else:
                parts = line.split(":")
                if len(parts) != 5:
                    raise ValueError("invalid tracker peer line")
                peers.append(Peer(parts[0], *(int(v) for v in parts[1:])))
        try:
            return cls(fields["Filename"], int(fields["Filesize"]), fields["MD5"], tuple(peers))
        except KeyError as exc:
            raise ValueError("missing tracker field: " + str(exc)) from exc

    @property
    def identity(self) -> Tuple[str, int, str]:
        return self.filename, self.filesize, self.md5
