"""Real local peers and the standalone CLI; no tracker server is substituted into production."""

import hashlib
import json
import os
from pathlib import Path
import socketserver
import subprocess
import sys
import tempfile
import threading
import unittest

from peer_download import DownloadConfig, Downloader, FileInfo, Peer


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = b"\x00\xff<GET invalid>\n" * 700 + b"tail"
        self.requests = []
        self.request_lock = threading.Lock()

    def seed(self, first, last, timestamp):
        data, requests, lock = self.data, self.requests, self.request_lock

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                request = self.rfile.readline(512).decode("utf-8").strip()
                command, filename, start, end = request[1:-1].split()
                start, end = int(start), int(end)
                if command != "GET" or filename != "file.bin" or not first <= start < end <= last or end - start > 1024:
                    self.wfile.write(b"<GET invalid>\n")
                    return
                with lock:
                    requests.append((self.server.server_address[1], start, end))
                self.wfile.write(f"<REP GET {end - start}>\n".encode() + data[start:end])

        server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return Peer("127.0.0.1", server.server_address[1], first, last, timestamp)

    def test_download_over_tcp_from_two_partial_peers(self):
        peers = (self.seed(0, 1700, 100), self.seed(1700, len(self.data), 200))
        info = FileInfo("file.bin", len(self.data), hashlib.md5(self.data).hexdigest(), peers)
        output = Downloader(info, self.root / "shared", self.root / "cache",
                            config=DownloadConfig(segment_size=2048, workers=3)).download()
        self.assertEqual(output.read_bytes(), self.data)
        self.assertEqual({port for port, _, _ in self.requests}, {p.port for p in peers})
        self.assertEqual(sum(end - start for _, start, end in self.requests), len(self.data))

    def test_cli_resumes_cached_trackers(self):
        peer = self.seed(0, len(self.data), 100)
        cache = self.root / "cache"
        cache.mkdir()
        tracker = cache / "file.bin.track"
        tracker.write_text(f"Filename: file.bin\nFilesize: {len(self.data)}\n"
                           f"MD5: {hashlib.md5(self.data).hexdigest()}\n"
                           f"{peer.ip}:{peer.port}:0:{len(self.data)}:100\n")
        config = self.root / "config.json"
        config.write_text(json.dumps({"shared_dir": "shared", "cache_dir": "cache", "segment_size": 2048}))
        env = dict(os.environ, PYTHONPYCACHEPREFIX=str(self.root / "pycache"))
        result = subprocess.run([sys.executable, "-m", "peer_download", "--config", str(config), "--resume-all"],
                                capture_output=True, text=True, timeout=10, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / "shared" / "file.bin").read_bytes(), self.data)
        self.assertFalse(tracker.exists())
        again = subprocess.run([sys.executable, "-m", "peer_download", "--config", str(config), "--resume-all"],
                               capture_output=True, text=True, timeout=10, env=env)
        self.assertEqual(again.returncode, 0, again.stderr)


if __name__ == "__main__":
    unittest.main()
