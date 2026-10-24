"""Download scheduling and durable segment records, independent of tracker sockets."""

from collections import deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import tempfile
import threading
import time
from typing import Callable, Dict, Optional, Tuple

from .models import FileInfo, Peer
from .transport import MAX_CHUNK_SIZE, TCPChunkClient

LOG = logging.getLogger(__name__)


class DownloadError(RuntimeError):
    """Download is incomplete; valid checkpoints remain available for resume."""


class IntegrityError(DownloadError):
    """Complete file does not match its tracker MD5."""


@dataclass(frozen=True)
class DownloadConfig:
    segment_size: int = 65536
    chunk_size: int = MAX_CHUNK_SIZE
    workers: int = 4
    attempts_per_chunk: int = 3
    socket_timeout: float = 5.0
    refresh_interval: float = 900.0
    max_stalled_refreshes: int = 3

    def __post_init__(self) -> None:
        for name in ("segment_size", "chunk_size", "workers", "attempts_per_chunk", "max_stalled_refreshes"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(name + " must be a positive integer")
        if self.chunk_size > MAX_CHUNK_SIZE:
            raise ValueError("chunk_size cannot exceed 1024")
        if self.segment_size > 16 * 1024 * 1024:
            raise ValueError("segment_size cannot exceed 16 MiB")
        if self.workers > 128:
            raise ValueError("workers cannot exceed 128")
        if any(not math.isfinite(v) or v <= 0 for v in (self.socket_timeout, self.refresh_interval)):
            raise ValueError("timeouts and refresh interval must be finite and positive")


def _digest(path: Path, algorithm: str = "md5") -> str:
    checksum = hashlib.new(algorithm)
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            checksum.update(block)
    return checksum.hexdigest()


class Downloader:
    """One file per instance; separate instances can download different files.

    fetch_chunk(peer, filename, start, end) -> bytes runs on worker threads.
    refresh_tracker(filename) -> FileInfo runs on the coordinating thread.
    report_segment(filename, start, end) runs after a durable checkpoint and
    again every refresh interval. A failed report is retried at that interval.
    Callbacks must enforce their own network deadlines. Ranges are exclusive.
    """

    def __init__(
        self, info: FileInfo, shared_dir: Path, cache_dir: Path,
        config: Optional[DownloadConfig] = None,
        fetch_chunk: Optional[Callable[[Peer, str, int, int], bytes]] = None,
        refresh_tracker: Optional[Callable[[str], FileInfo]] = None,
        report_segment: Optional[Callable[[str, int, int], None]] = None,
        cancel: Optional[threading.Event] = None,
    ) -> None:
        self.info = info
        self.config = config or DownloadConfig()
        self.shared_dir = Path(shared_dir)
        self.cache_dir = Path(cache_dir)
        self.fetch_chunk = fetch_chunk or TCPChunkClient(self.config.socket_timeout)
        self.refresh_tracker = refresh_tracker
        self.report_segment = report_segment
        self.cancel = cancel or threading.Event()
        self._lock = threading.RLock()
        self._active_peers: Dict[Tuple[str, int], int] = {}
        self._completed: Dict[int, str] = {}
        self._running = threading.Lock()
        self._stop_workers = threading.Event()
        self.path = self.shared_dir / info.filename
        self.part_path = self.shared_dir / ".downloads" / (info.filename + ".part")
        self.state_path = self.cache_dir / (info.filename + ".download.json")
        self.tracker_path = self.cache_dir / (info.filename + ".track")
        self.segment_count = (info.filesize + self.config.segment_size - 1) // self.config.segment_size

    def _bounds(self, index: int) -> Tuple[int, int]:
        start = index * self.config.segment_size
        return start, min(start + self.config.segment_size, self.info.filesize)

    def completed_ranges(self) -> Tuple[Tuple[int, int], ...]:
        """Only fully downloaded, durable segments may be served/advertised."""
        with self._lock:
            return tuple(self._bounds(i) for i in sorted(self._completed))

    @contextmanager
    def _file_lock(self):
        # Keep the lock file: unlinking it can let concurrent processes lock
        # different inodes. flock automatically releases on process failure.
        with (self.cache_dir / (self.info.filename + ".download.lock")).open("a") as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise DownloadError("another process is downloading this file") from exc
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)

    def _save_state(self) -> None:
        state = {"version": 1, "filename": self.info.filename, "filesize": self.info.filesize,
                 "md5": self.info.md5, "segment_size": self.config.segment_size,
                 "completed": {str(k): v for k, v in self._completed.items()}}
        temporary = None
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.cache_dir,
                                             prefix=".download-", delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(state, stream, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.state_path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _restore(self) -> None:
        self._completed = {}
        if self.state_path.exists():
            try:
                state = json.loads(self.state_path.read_text(encoding="utf-8"))
                expected = (1, self.info.filename, self.info.filesize, self.info.md5, self.config.segment_size)
                actual = tuple(state.get(k) for k in ("version", "filename", "filesize", "md5", "segment_size"))
                if actual != expected:
                    raise DownloadError("checkpoint metadata changed; use the original segment size or move the old checkpoint")
                records = state["completed"]
                if not isinstance(records, dict):
                    raise ValueError("invalid completed records")
                if self.part_path.exists():
                    with self.part_path.open("rb") as stream:
                        for key, checksum in records.items():
                            index = int(key)
                            if not 0 <= index < self.segment_count:
                                raise ValueError("invalid segment index")
                            start, end = self._bounds(index)
                            stream.seek(start)
                            data = stream.read(end - start)
                            if len(data) == end - start and hashlib.sha256(data).hexdigest() == checksum:
                                self._completed[index] = checksum
            except (ValueError, KeyError, TypeError, AttributeError) as exc:
                # Malformed state must never cause missing bytes to be skipped.
                raise DownloadError("unreadable checkpoint: " + str(exc)) from exc
        # A crash before the very first checkpoint can leave an orphan .part.
        # No bytes from an unrecorded segment are trusted; download them again.
        # Truncate only after the state has been validated. Sparse gaps are not
        # treated as downloaded bytes; only SHA-256-verified segments are.
        with self.part_path.open("r+b" if self.part_path.exists() else "w+b") as stream:
            stream.truncate(self.info.filesize)
            stream.flush()
            os.fsync(stream.fileno())
        self._save_state()

    def _report(self, start: int, end: int) -> None:
        if self.report_segment is not None:
            try:
                self.report_segment(self.info.filename, start, end)
            except Exception:
                LOG.warning("tracker update failed for %s bytes %d:%d; retry at next interval",
                            self.info.filename, start, end, exc_info=True)

    def _advertise_completed(self) -> None:
        for start, end in self.completed_ranges():
            self._report(start, end)

    def _refresh(self) -> None:
        if self.refresh_tracker is not None:
            try:
                updated = self.refresh_tracker(self.info.filename)
            except Exception:
                LOG.warning("tracker refresh failed; retaining current peers", exc_info=True)
            else:
                if not isinstance(updated, FileInfo) or updated.identity != self.info.identity:
                    raise DownloadError("tracker refresh changed file identity")
                with self._lock:
                    self.info = updated
        self._advertise_completed()

    def _choose_peer(self, position: int, excluded: set) -> Optional[Peer]:
        # Prefer the newest peer that is not already busy. This lets concurrent
        # workers use distinct peers where possible without waiting on a seed.
        with self._lock:
            candidates = [p for p in self.info.peers if p.start_byte <= position < p.end_byte
                          and p.endpoint not in excluded]
            if not candidates:
                return None
            peer = min(candidates, key=lambda p: (self._active_peers.get(p.endpoint, 0),
                                                  -p.timestamp, p.ip, p.port))
            self._active_peers[peer.endpoint] = self._active_peers.get(peer.endpoint, 0) + 1
            return peer

    def _fetch_segment(self, index: int) -> bytes:
        start, end = self._bounds(index)
        result = bytearray()
        while start + len(result) < end:
            if self.cancel.is_set() or self._stop_workers.is_set():
                raise DownloadError("download cancelled")
            position = start + len(result)
            excluded = set()
            last_error = None
            for _ in range(self.config.attempts_per_chunk):
                if self.cancel.is_set() or self._stop_workers.is_set():
                    raise DownloadError("download cancelled")
                peer = self._choose_peer(position, excluded)
                if peer is None:
                    # Retry a temporarily failed peer if every candidate has
                    # been tried, but never exceed attempts_per_chunk.
                    excluded.clear()
                    peer = self._choose_peer(position, excluded)
                if peer is None:
                    raise DownloadError(f"no peer has byte {position}")
                chunk_end = min(position + self.config.chunk_size, end, peer.end_byte)
                try:
                    data = self.fetch_chunk(peer, self.info.filename, position, chunk_end)
                    if not isinstance(data, bytes) or len(data) != chunk_end - position:
                        raise OSError("peer returned an incomplete or invalid chunk")
                    LOG.info("%s: downloaded bytes %d:%d from %s:%d", self.info.filename,
                             position, chunk_end, peer.ip, peer.port)
                    result.extend(data)
                    break
                except OSError as exc:
                    last_error = exc
                    excluded.add(peer.endpoint)
                    LOG.warning("%s: failed bytes %d:%d from %s:%d: %s", self.info.filename,
                                position, chunk_end, peer.ip, peer.port, exc)
                finally:
                    with self._lock:
                        self._active_peers[peer.endpoint] -= 1
            else:
                raise DownloadError(f"chunk at byte {position} failed after retries: {last_error}")
        return bytes(result)

    def _commit_segment(self, stream, index: int, data: bytes) -> None:
        start, end = self._bounds(index)
        if len(data) != end - start:
            raise DownloadError("worker returned an incomplete segment")
        stream.seek(start)
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
        with self._lock:
            self._completed[index] = hashlib.sha256(data).hexdigest()
            self._save_state()
        # Both data and checkpoint are durable before exposing the range.
        self._report(start, end)

    def _download_missing(self) -> None:
        pending = deque(i for i in range(self.segment_count) if i not in self._completed)
        futures = {}
        failures = {}
        next_refresh = time.monotonic() + self.config.refresh_interval
        last_progress = len(self._completed)
        stalled = 0
        with self.part_path.open("r+b") as stream, ThreadPoolExecutor(max_workers=self.config.workers) as pool:
            try:
                while pending or futures or len(self._completed) < self.segment_count:
                    if self.cancel.is_set():
                        raise DownloadError("download cancelled; completed segments saved")
                    now = time.monotonic()
                    if now >= next_refresh:
                        self._refresh()
                        stalled = stalled + 1 if len(self._completed) == last_progress else 0
                        last_progress = len(self._completed)
                        next_refresh = time.monotonic() + self.config.refresh_interval
                        if not pending and not futures:
                            if stalled >= self.config.max_stalled_refreshes:
                                raise DownloadError("no download progress after tracker refreshes")
                            pending.extend(i for i in range(self.segment_count) if i not in self._completed)
                            failures.clear()
                    # Submission order is sequential; completion can be out of order.
                    while pending and len(futures) < self.config.workers:
                        index = pending.popleft()
                        futures[pool.submit(self._fetch_segment, index)] = index
                    if not futures:
                        if self.refresh_tracker is None:
                            raise DownloadError("download incomplete: " + "; ".join(failures.values()))
                        self.cancel.wait(min(0.1, max(0, next_refresh - time.monotonic())))
                        continue
                    done, _ = wait(futures, timeout=min(0.1, max(0, next_refresh - time.monotonic())),
                                   return_when=FIRST_COMPLETED)
                    for future in done:
                        index = futures.pop(future)
                        try:
                            data = future.result()
                        except (DownloadError, OSError) as exc:
                            failures[index] = str(exc)
                        else:
                            self._commit_segment(stream, index, data)
            finally:
                self._stop_workers.set()
                for future in futures:
                    future.cancel()

    def _cleanup(self) -> None:
        self.state_path.unlink(missing_ok=True)
        self.tracker_path.unlink(missing_ok=True)

    def download(self) -> Path:
        """Download/resume, verify MD5, publish final file and clear its cache."""
        if not self._running.acquire(blocking=False):
            raise DownloadError("this downloader is already running")
        try:
            self._stop_workers.clear()
            self.shared_dir.mkdir(parents=True, exist_ok=True)
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            self.part_path.parent.mkdir(parents=True, exist_ok=True)
            if self.shared_dir.resolve() == self.cache_dir.resolve():
                raise ValueError("shared and cache directories must be different")
            with self._file_lock():
                # Covers a crash after final rename but before cache cleanup.
                if self.path.exists():
                    if self.path.stat().st_size != self.info.filesize or _digest(self.path) != self.info.md5:
                        raise DownloadError("destination exists with different contents; refusing to overwrite")
                    with self._lock:
                        self._completed = {i: "" for i in range(self.segment_count)}
                    self._advertise_completed()
                    self._cleanup()
                    return self.path
                self._restore()
                self._advertise_completed()
                self._download_missing()
                if _digest(self.part_path) != self.info.md5:
                    # The tracker only supplies a whole-file digest. There is no
                    # trusted per-segment checksum to identify the corrupt peer.
                    with self._lock:
                        self._completed.clear()
                        self._save_state()
                    raise IntegrityError("MD5 mismatch; all segments marked missing for the next attempt")
                os.replace(self.part_path, self.path)
                self._cleanup()
                LOG.info("%s: download complete (MD5 verified)", self.info.filename)
                return self.path
        finally:
            self._running.release()
