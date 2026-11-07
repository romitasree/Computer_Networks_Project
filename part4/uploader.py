"""Threaded chunk serving and tracker registration/heartbeats (Part 4)."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import hashlib
import logging
from pathlib import Path
import re
import socket
import threading
import time

from .protocol import _filename, _number

LOG = logging.getLogger(__name__)
MAX_CHUNK_SIZE = 1024


def safe_name(name):
    _filename(name)
    if len(name.encode()) > 200 or ".." in name or ":" in name or name.startswith("."):
        raise ValueError("File name is not compatible with tracker/downloader")
    return name


def digest(path):
    md5 = hashlib.md5()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1048576), b""):
            md5.update(block)
    return md5.hexdigest()


def signature(path):
    s = Path(path).stat()
    return s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns


def contiguous_end(ranges):
    """The tracker replaces one range: advertise only the durable prefix [0,end)."""
    end = 0
    for start, stop in sorted(ranges):
        if start > end:
            break
        if stop > end:
            end = stop
    return end


def covers(ranges, start, end):
    """Allow a chunk across adjacent completed segments, but never a hole."""
    cursor = start
    for first, last in sorted(ranges):
        if first > cursor:
            return False
        if last > cursor:
            cursor = last
        if cursor >= end:
            return True
    return False


@dataclass(frozen=True)
class Seed:
    filename: str
    filesize: int
    md5: str
    description: str
    path: Path
    signature: tuple


class PeerUploader:
    """Socket server compatible with Vikas's TCPChunkClient.

    GET start/end use exclusive ends. Each connection is one request/reply.
    Full seeds are explicitly registered; incomplete files are served only
    through a Downloader object exposing its verified completed_ranges().
    No sparse-file-size guesses or arbitrary shared-directory reads occur.
    """

    def __init__(self, shared_dir, tracker, listen_port, refresh_seconds=900,
                 bind_host="0.0.0.0", timeout=5.0, workers=16, backlog=64):
        self.shared_dir = Path(shared_dir).resolve()
        self.tracker = tracker
        self.listen_port = _number(listen_port, 0, 65535)
        if (not 0 < refresh_seconds <= 4294967295 or not 0 < timeout <= 300
                or not isinstance(workers, int) or not 1 <= workers <= 128
                or not isinstance(backlog, int) or not 1 <= backlog <= 1024):
            raise ValueError("Invalid uploader limits")
        self.refresh_seconds, self.timeout = refresh_seconds, timeout
        self.bind_host, self.workers, self.backlog = bind_host, workers, backlog
        self.ip = None
        self._lock, self._heartbeat_lock = threading.RLock(), threading.Lock()
        self._seeds, self._downloads, self._registered = {}, {}, set()
        self._stop = threading.Event()
        self._slots = threading.BoundedSemaphore(workers)
        self._socket = self._pool = None
        self._threads = []

    def add_seed(self, filename, description="-"):
        """Only a verified complete local file may send createtracker."""
        safe_name(filename)
        path = self.shared_dir / filename
        if path.is_symlink() or path.resolve().parent != self.shared_dir or not path.is_file():
            raise ValueError("Seed must be a regular file directly in the shared folder")
        before = signature(path)
        md5 = digest(path)
        if before != signature(path):
            raise ValueError("Seed changed while computing MD5")
        # Use the existing formatter for description/endpoint validation later.
        from .protocol import _token
        _token(description)
        seed = Seed(filename, before[2], md5, description, path, before)
        with self._lock:
            if filename in self._downloads:
                raise ValueError("File already belongs to an attached downloader")
            self._seeds[filename] = seed
            self._registered.discard(filename)
        return seed

    def attach_downloader(self, downloader):
        """Call before download(); use report_segment/refresh_tracker as callbacks."""
        name = safe_name(downloader.info.filename)
        if Path(downloader.shared_dir).resolve() != self.shared_dir:
            raise ValueError("Downloader must use this peer's shared directory")
        with self._lock:
            if name in self._seeds or name in self._downloads:
                raise ValueError("File already attached")
            self._downloads[name] = downloader

    def _source(self, name):
        safe_name(name)
        with self._lock:
            seed = self._seeds.get(name)
            downloader = self._downloads.get(name)
        if seed is not None:
            if seed.path.is_symlink() or signature(seed.path) != seed.signature:
                raise ValueError("Seed changed; remove/re-register it before serving")
            return seed.path, ((0, seed.filesize),), seed
        if downloader is None:
            raise ValueError("File is not shared")
        ranges = tuple(downloader.completed_ranges())
        # Rename may happen between snapshot and open. Fall back to the final
        # file only when the downloader has already made every byte available.
        part = Path(downloader.part_path)
        final = self.shared_dir / name
        expected_part = self.shared_dir / ".downloads" / (name + ".part")
        if (part.resolve() != expected_part.resolve()
                or part.resolve().parent != self.shared_dir / ".downloads"):
            raise ValueError("Unexpected downloader partial path")
        path = part if part.exists() else final
        if path.is_symlink() or (path == final and
                contiguous_end(ranges) != downloader.info.filesize):
            raise ValueError("Unavailable file")
        return path, ranges, downloader.info

    def read_chunk(self, name, start, end):
        start, end = _number(start), _number(end)
        if not 1 <= end - start <= MAX_CHUNK_SIZE:
            raise ValueError("Chunk must contain 1 through 1024 bytes")
        path, ranges, info = self._source(name)
        if end > info.filesize or not covers(ranges, start, end):
            raise ValueError("Requested bytes are not completed")
        try:
            stream = path.open("rb")
        except FileNotFoundError:
            # Covers the downloader's atomic partial-to-final rename.
            path, ranges, info = self._source(name)
            stream = path.open("rb")
        with stream:
            stream.seek(start)
            data = stream.read(end - start)
        _, current_ranges, _ = self._source(name)
        if len(data) != end - start or not covers(current_ranges, start, end):
            raise ValueError("File availability changed during read")
        return data

    def _handle(self, connection, address):
        try:
            with connection:
                try:
                    deadline = time.monotonic() + self.timeout
                    line = bytearray()
                    while not line.endswith(b"\n"):
                        remaining = deadline - time.monotonic()
                        if remaining <= 0 or len(line) >= 2048:
                            raise ValueError("Request deadline or size limit reached")
                        connection.settimeout(remaining)
                        byte = connection.recv(1)
                        if not byte:
                            raise ValueError("Incomplete request")
                        line.extend(byte)
                    text = line.decode("utf-8")
                    match = re.fullmatch(r"<GET ([^\s<>]+) ([0-9]+) ([0-9]+)>\r?\n", text)
                    if match is None:
                        raise ValueError("Invalid chunk request")
                    name, first, last = match.groups()
                    start, end = _number(first), _number(last)
                    data = self.read_chunk(name, start, end)
                except (OSError, ValueError) as exc:
                    LOG.warning("Upload rejected from %s: %s", address, exc)
                    connection.settimeout(self.timeout)
                    connection.sendall(b"<GET invalid>\n")
                    return
                connection.settimeout(self.timeout)
                connection.sendall(f"<REP GET {len(data)}>\n".encode("ascii") + data)
                LOG.info("Served %s bytes %d:%d to %s", name, start, end, address)
        except OSError as exc:
            LOG.warning("Upload connection failed: %s", exc)
        finally:
            self._slots.release()

    def _accept(self):
        while not self._stop.is_set():
            try:
                conn, address = self._socket.accept()
            except socket.timeout:
                continue
            except OSError:
                if not self._stop.is_set():
                    LOG.exception("Listener failed")
                break
            if not self._slots.acquire(blocking=False):
                # Bound active workers and pending requests under load.
                conn.close()
                continue
            try:
                self._pool.submit(self._handle, conn, address)
            except RuntimeError:
                conn.close()
                self._slots.release()
                break

    def _update(self, name):
        _, ranges, info = self._source(name)
        end = contiguous_end(ranges)
        # No completed prefix yet: do not advertise sparse holes or send create.
        if end == 0 and info.filesize != 0:
            return
        self.tracker.update_tracker(name, 0, end, self.ip, self.listen_port)
        LOG.info("updatetracker %s range 0:%d at %s:%d", name, end, self.ip, self.listen_port)

    def report_segment(self, filename, start, end):
        """Downloader callback: ignore one segment's isolated range; recompute prefix."""
        if self.ip is None:
            raise RuntimeError("Start the uploader before downloading")
        self._update(filename)

    def refresh_tracker(self, filename):
        info = self.tracker.get_tracker(filename)
        from peer_download.models import FileInfo
        return FileInfo.from_tracker(info)

    def heartbeat_once(self):
        """Register pending seeds and refresh all advertised ranges, retrying failures."""
        if self.ip is None:
            raise RuntimeError("Uploader is not started")
        with self._heartbeat_lock:
            with self._lock:
                names = tuple(self._seeds) + tuple(self._downloads)
            for name in names:
                try:
                    _, _, info = self._source(name)
                    with self._lock:
                        seed = self._seeds.get(name)
                        registered = name in self._registered
                    if seed is not None and not registered:
                        status = self.tracker.create_tracker(name, seed.filesize,
                            seed.description, seed.md5, self.ip, self.listen_port)
                        if status == "ferr":
                            existing = self.tracker.get_tracker(name)
                            if existing.identity != (name, seed.filesize, seed.md5):
                                raise ValueError("Existing tracker describes a different file")
                        with self._lock:
                            self._registered.add(name)
                        LOG.info("createtracker %s -> %s", name, status)
                    self._update(name)
                except (OSError, ValueError, RuntimeError) as exc:
                    LOG.warning("Tracker operation for %s failed; retry next interval: %s", name, exc)

    def _heartbeat(self):
        self.heartbeat_once()
        while not self._stop.wait(self.refresh_seconds):
            self.heartbeat_once()

    def start(self):
        if self._socket is not None:
            raise RuntimeError("Uploader already started")
        self.shared_dir.mkdir(parents=True, exist_ok=True)
        self.ip = self.tracker.local_ip()
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind((self.bind_host, self.listen_port))
            listener.listen(self.backlog)
            listener.settimeout(0.2)
        except BaseException:
            listener.close()
            raise
        self._socket = listener
        self.listen_port = listener.getsockname()[1]
        self._stop.clear()
        self._pool = ThreadPoolExecutor(max_workers=self.workers, thread_name_prefix="upload")
        self._threads = [threading.Thread(target=self._accept, name="peer-listener", daemon=True),
                         threading.Thread(target=self._heartbeat, name="tracker-heartbeat", daemon=True)]
        for thread in self._threads:
            thread.start()
        LOG.info("Peer listening at %s:%d", self.ip, self.listen_port)
        return self

    def close(self):
        if self._socket is None:
            return
        self._stop.set()
        self._socket.close()
        for thread in self._threads:
            thread.join()
        self._pool.shutdown(wait=True)
        self._socket = None

    def __enter__(self):
        return self.start()

    def __exit__(self, *args):
        self.close()
