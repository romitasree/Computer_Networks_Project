"""Localhost network tests; sockets are production code, not mock uploads."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import logging
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace

from part4.uploader import PeerUploader, contiguous_end
from part4.tracker_client import TrackerClient, parse_tracker


class RecordingTracker:
    def __init__(self):
        self.creates, self.updates = [], []
        self.fail = False

    def local_ip(self):
        return "127.0.0.1"

    def create_tracker(self, *args):
        if self.fail:
            raise OSError("temporary tracker outage")
        self.creates.append(args)
        return "succ"

    def update_tracker(self, *args):
        self.updates.append(args)


def raw_request(peer, request, fragmented=False):
    with socket.create_connection(("127.0.0.1", peer.listen_port), 2) as sock:
        if fragmented:
            for byte in request:
                sock.sendall(bytes([byte]))
        else:
            sock.sendall(request)
        sock.shutdown(socket.SHUT_WR)
        result = bytearray()
        while True:
            block = sock.recv(4096)
            if not block:
                return bytes(result)
            result.extend(block)


class UploadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.shared = Path(self.temp.name)
        self.data = b"<GET invalid>\n" + bytes(range(256)) * 16
        (self.shared / "sample.bin").write_bytes(self.data)
        self.tracker = RecordingTracker()
        self.peer = PeerUploader(self.shared, self.tracker, 0, .05,
                                 bind_host="127.0.0.1", timeout=.4, workers=4)
        self.peer.add_seed("sample.bin")
        self.peer.start()
        self.addCleanup(self.peer.close)

    def test_binary_fragmented_exact_and_short_chunks(self):
        for start, end in ((0, 1024), (1024, 2048), (4096, len(self.data))):
            response = raw_request(self.peer, f"<GET sample.bin {start} {end}>\n".encode(), True)
            self.assertEqual(response, f"<REP GET {end-start}>\n".encode() + self.data[start:end])

    def test_bad_requests(self):
        for line in (b"<GET sample.bin 0 1025>\n", b"<GET sample.bin 0 0>\n",
                     b"<GET sample.bin -1 2>\n", b"<GET missing 0 1>\n",
                     b"<GET ../sample.bin 0 1>\n", b"<GET sample.bin 0 999999>\n",
                     b"junk\n"):
            with self.subTest(line=line):
                self.assertEqual(raw_request(self.peer, line), b"<GET invalid>\n")

    def test_concurrent_and_heartbeat(self):
        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(lambda _: raw_request(self.peer, b"<GET sample.bin 0 20>\n"), range(3)))
        self.assertTrue(all(x.endswith(self.data[:20]) for x in results))
        deadline = time.monotonic() + 2
        while len(self.tracker.updates) < 3 and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertEqual(len(self.tracker.creates), 1)
        self.assertGreaterEqual(len(self.tracker.updates), 3)
        self.assertTrue(all(x[1:3] == (0, len(self.data)) for x in self.tracker.updates))

    def test_partial_ranges_never_serve_holes(self):
        part = self.shared / ".downloads" / "partial.bin.part"
        part.parent.mkdir()
        part.write_bytes(b"a" * 4096)
        ranges = [(0, 1024), (2048, 3072)]
        downloader = SimpleNamespace(info=SimpleNamespace(filename="partial.bin", filesize=4096),
            shared_dir=self.shared, part_path=part, completed_ranges=lambda: tuple(ranges))
        self.peer.attach_downloader(downloader)
        self.peer.report_segment("partial.bin", 2048, 3072)
        self.assertEqual(self.tracker.updates[-1][1:3], (0, 1024))
        self.assertEqual(raw_request(self.peer, b"<GET partial.bin 1024 2048>\n"), b"<GET invalid>\n")
        self.assertTrue(raw_request(self.peer, b"<GET partial.bin 2048 2050>\n").endswith(b"aa"))
        ranges.append((1024, 2048))
        self.peer.report_segment("partial.bin", 1024, 2048)
        self.assertEqual(self.tracker.updates[-1][1:3], (0, 3072))
        self.assertNotIn("partial.bin", [x[0] for x in self.tracker.creates])

    def test_changed_seed_and_symlink_rejected(self):
        (self.shared / "sample.bin").write_bytes(b"changed")
        self.assertEqual(raw_request(self.peer, b"<GET sample.bin 0 1>\n"), b"<GET invalid>\n")
        (self.shared / "link").symlink_to(self.shared / "sample.bin")
        with self.assertRaises(ValueError):
            self.peer.add_seed("link")

    def test_tracker_failure_retries(self):
        self.tracker.fail = True
        (self.shared / "another").write_bytes(b"ok")
        self.peer.add_seed("another")
        self.peer.heartbeat_once()
        self.tracker.fail = False
        self.peer.heartbeat_once()
        self.assertIn("another", [x[0] for x in self.tracker.creates])

    def test_shutdown_releases_listener(self):
        port = self.peer.listen_port
        self.peer.close()
        with socket.socket() as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("127.0.0.1", port))


class TrackerClientTests(unittest.TestCase):
    def response(self, data, operation):
        with socket.socket() as listener, tempfile.TemporaryDirectory() as d:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            def serve():
                conn, _ = listener.accept()
                with conn:
                    conn.recv(4096)
                    conn.sendall(data)
            thread = threading.Thread(target=serve)
            thread.start()
            try:
                return operation(TrackerClient("127.0.0.1", listener.getsockname()[1], d), Path(d))
            finally:
                thread.join(timeout=3)

    def test_get_checksum_and_cache(self):
        body = b"Filename: f\nFilesize: 2\nMD5: 444bcb3a3fcf8389296c49467f27e1d6\n# comment\n127.0.0.1:5001:0:2:1\n"
        wire = b"<REP GET BEGIN>\n" + body + f"<REP GET END {hashlib.md5(body).hexdigest()}>\n".encode()
        def check(client, cache):
            self.assertEqual(client.get_tracker("f").filesize, 2)
            self.assertEqual((cache / "f.track").read_bytes(), body)
        self.response(wire, check)
        def reject(client, cache):
            (cache / "f.track").write_bytes(b"old verified cache")
            with self.assertRaises(ValueError):
                client.get_tracker("f")
            self.assertEqual((cache / "f.track").read_bytes(), b"old verified cache")
        self.response(wire.replace(b"Filesize: 2", b"Filesize: 3"), reject)

    def test_list_count_and_indexes(self):
        good = b"<REP LIST 1>\n<1 f 0 d41d8cd98f00b204e9800998ecf8427e>\n<REP LIST END>\n"
        self.response(good, lambda c, _: self.assertEqual(c.list_files()[0]["filename"], "f"))
        def reject(c, _):
            with self.assertRaises(ValueError):
                c.list_files()
        self.response(good.replace(b"<REP LIST 1>", b"<REP LIST 2>"), reject)
        self.response(good.replace(b"<1 f", b"<2 f"), reject)

    def test_contiguous_prefix(self):
        self.assertEqual(contiguous_end([(2048, 4096)]), 0)
        self.assertEqual(contiguous_end([(0, 1024), (2048, 4096)]), 1024)
        self.assertEqual(contiguous_end([(1024, 2048), (0, 1024)]), 2048)


if __name__ == "__main__":
    logging.disable(logging.CRITICAL)
    unittest.main()
