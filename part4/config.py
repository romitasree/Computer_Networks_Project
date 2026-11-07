"""Configuration readers for the tracker and peers."""

from dataclasses import dataclass
from ipaddress import IPv4Address
from pathlib import Path

DEFAULT_REFRESH_SECONDS = 900


@dataclass(frozen=True)
class ClientConfig:
    tracker_port: int
    tracker_ip: str
    refresh_seconds: int


@dataclass(frozen=True)
class PeerServerConfig:
    listen_port: int
    shared_directory: str


@dataclass(frozen=True)
class TrackerConfig:
    listen_port: int
    tracker_directory: str
    dead_peer_timeout: int = DEFAULT_REFRESH_SECONDS


def _fields(path, count):
    """Read required settings; permit trailing blank lines only."""
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"Cannot read configuration {path}: {exc}") from exc

    while lines and not lines[-1].strip():
        lines.pop()

    allowed = (count,) if isinstance(count, int) else count
    if len(lines) not in allowed:
        raise ValueError(f"{path}: expected {count} configuration lines")

    values = [line.strip() for line in lines]
    if any(not value for value in values):
        raise ValueError(f"{path}: required settings cannot be empty")

    return values


def _positive(text, maximum, label):
    if not text.isascii() or not text.isdecimal():
        raise ValueError(f"{label} must contain decimal digits")

    try:
        value = int(text)
    except ValueError as exc:
        raise ValueError(f"{label} is too large") from exc

    if not 1 <= value <= maximum:
        raise ValueError(f"{label} must be between 1 and {maximum}")

    return value


def config_load_client(path):
    port, address, interval = _fields(path, 3)

    try:
        IPv4Address(address)
    except ValueError as exc:
        raise ValueError("Tracker address must be a valid IPv4 address") from exc

    return ClientConfig(
        _positive(port, 65535, "Tracker port"),
        address,
        _positive(interval, 4294967295, "Refresh interval"),
    )


def config_load_peer_server(path):
    port, directory = _fields(path, 2)
    return PeerServerConfig(
        _positive(port, 65535, "Peer port"),
        directory,
    )


def config_load_tracker(path):
    # Match Romita's sconfig: optional third line is dead-peer expiry seconds.
    fields = _fields(path, (2, 3))
    port, directory = fields[:2]
    return TrackerConfig(
        _positive(port, 65535, "Tracker port"),
        directory,
        _positive(fields[2], 4294967295, "Dead-peer timeout")
        if len(fields) == 3 else DEFAULT_REFRESH_SECONDS,
    )
