import hashlib
import json
import logging
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import time
import unittest

from peer_download import DownloadConfig, DownloadError, Downloader, FileInfo, IntegrityError, Peer

logging.getLogger("peer_download").addHandler(logging.NullHandler())
logging.getLogger("peer_download").propagate = False


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.shared, self.cache = self.root / "shared", self.root / "cache"
        self.cache.mkdir()
        self.data = bytes(range(256)) * 33 + b"tail\x00\xff"
        self.peer = Peer("127.0.0.1", 3001, 0, len(self.data), 100)
        self.info = FileInfo("sample.bin", len(self.data), hashlib.md5(self.data).hexdigest(), (self.peer,))
        self.calls = []

    def fetch(self, peer, filename, start, end):
        self.calls.append((peer.port, start, end))
        return self.data[start:end]

    def downloader(self, info=None, fetch=None, **kwargs):
        config = kwargs.pop("config", DownloadConfig(segment_size=2048, workers=2))
        return Downloader(info or self.info, self.shared, self.cache, config,
                          fetch_chunk=fetch or self.fetch, **kwargs)

    def test_binary_download_chunk_limit_tail_and_cache_cleanup(self):
        tracker = self.cache / "sample.bin.track"
        tracker.write_text("cached tracker")
        other = self.cache / "other.track"
        other.write_text("keep")
        downloader = self.downloader()
        path = downloader.download()
        self.assertEqual(path.read_bytes(), self.data)
        self.assertTrue(all(0 < end - start <= 1024 for _, start, end in self.calls))
        self.assertFalse(downloader.state_path.exists())
        self.assertFalse(tracker.exists())
        self.assertTrue(other.exists())
        self.assertEqual(sum(end - start for _, start, end in self.calls), len(self.data))

    def test_newest_peer_and_failure_fallback(self):
        newest = Peer("127.0.0.1", 3002, 0, len(self.data), 200)
        info = FileInfo(self.info.filename, self.info.filesize, self.info.md5, (self.peer, newest))
        calls = []

        def fetch(peer, filename, start, end):
            calls.append(peer.port)
            if peer == newest:
                raise ConnectionRefusedError("peer went offline")
            return self.data[start:end]

        self.downloader(info, fetch, config=DownloadConfig(segment_size=2048, workers=1)).download()
        self.assertEqual(calls[:2], [3002, 3001])

    def test_workers_use_distinct_peers_concurrently(self):
        info = FileInfo(self.info.filename, self.info.filesize, self.info.md5,
                        (self.peer, Peer("127.0.0.1", 3002, 0, len(self.data), 101)))
        barrier = threading.Barrier(2)
        lock = threading.Lock()
        first = []

        def fetch(peer, filename, start, end):
            with lock:
                first.append(peer.port)
                synchronize = len(first) <= 2
            if synchronize:
                barrier.wait(timeout=3)
            return self.data[start:end]

        self.downloader(info, fetch).download()
        self.assertEqual(set(first[:2]), {3001, 3002})

    def test_chunks_split_at_partial_peer_boundaries(self):
        info = FileInfo(self.info.filename, self.info.filesize, self.info.md5,
                        (Peer("127.0.0.1", 3001, 0, 700, 100),
                         Peer("127.0.0.1", 3002, 700, len(self.data), 101)))

        def fetch(peer, filename, start, end):
            self.assertLessEqual(peer.start_byte, start)
            self.assertLessEqual(end, peer.end_byte)
            return self.fetch(peer, filename, start, end)

        self.downloader(info, fetch).download()
        self.assertIn((3001, 0, 700), self.calls)

    def test_resume_skips_completed_segments_after_failure(self):
        def failing(peer, filename, start, end):
            if start >= 2048:
                raise OSError("offline")
            return self.data[start:end]

        config = DownloadConfig(segment_size=2048, workers=1, attempts_per_chunk=1)
        first = self.downloader(fetch=failing, config=config)
        with self.assertRaises(DownloadError):
            first.download()
        self.assertEqual(first.completed_ranges(), ((0, 2048),))
        self.assertFalse(first.path.exists())
        second = self.downloader(config=config)
        self.assertEqual(second.download().read_bytes(), self.data)
        self.assertTrue(all(start >= 2048 for _, start, _ in self.calls))

    def test_resume_rechecks_corrupted_segment(self):
        def failing(peer, filename, start, end):
            if start >= 4096:
                raise OSError("offline")
            return self.data[start:end]

        config = DownloadConfig(segment_size=2048, workers=1, attempts_per_chunk=1)
        first = self.downloader(fetch=failing, config=config)
        with self.assertRaises(DownloadError):
            first.download()
        with first.part_path.open("r+b") as stream:
            stream.write(b"X")
        self.downloader(config=config).download()
        self.assertTrue(any(start == 0 for _, start, _ in self.calls))
        self.assertFalse(any(2048 <= start < 4096 for _, start, _ in self.calls))

    def test_report_only_after_durable_complete_segment(self):
        observed = []
        downloader = self.downloader()

        def report(filename, start, end):
            state = json.loads(downloader.state_path.read_text())
            self.assertIn(str(start // 2048), state["completed"])
            with downloader.part_path.open("rb") as stream:
                stream.seek(start)
                self.assertEqual(stream.read(end - start), self.data[start:end])
            observed.append((start, end))

        downloader.report_segment = report
        downloader.download()
        self.assertEqual(len(observed), downloader.segment_count)

    def test_refresh_discovers_peer_and_retries_failed_report(self):
        empty = FileInfo(self.info.filename, self.info.filesize, self.info.md5)
        refreshes = []
        reports = []

        def refresh(filename):
            refreshes.append(filename)
            return self.info

        def slow_fetch(peer, filename, start, end):
            time.sleep(0.006)
            return self.data[start:end]

        def report(filename, start, end):
            reports.append((start, end))
            if len(reports) == 1:
                raise OSError("tracker temporarily offline")

        config = DownloadConfig(segment_size=1024, workers=1, refresh_interval=0.02,
                                max_stalled_refreshes=5)
        self.downloader(empty, slow_fetch, config=config, refresh_tracker=refresh,
                        report_segment=report).download()
        self.assertGreaterEqual(len(refreshes), 2)
        self.assertGreater(reports.count((0, 1024)), 1)

    def test_stalled_refreshes_are_bounded(self):
        info = FileInfo(self.info.filename, self.info.filesize, self.info.md5)
        refreshes = []

        def refresh(filename):
            refreshes.append(filename)
            return info

        config = DownloadConfig(refresh_interval=0.01, max_stalled_refreshes=2)
        with self.assertRaisesRegex(DownloadError, "no download progress"):
            self.downloader(info, config=config, refresh_tracker=refresh).download()
        self.assertEqual(len(refreshes), 2)

    def test_refresh_identity_change_rejected(self):
        info = FileInfo(self.info.filename, self.info.filesize, self.info.md5)
        changed = FileInfo("different.bin", self.info.filesize, self.info.md5)
        with self.assertRaisesRegex(DownloadError, "changed file identity"):
            self.downloader(info, config=DownloadConfig(refresh_interval=0.01),
                            refresh_tracker=lambda _: changed).download()

    def test_md5_mismatch_never_publishes_and_invalidates_checkpoints(self):
        downloader = self.downloader(fetch=lambda p, f, s, e: b"x" * (e - s))
        with self.assertRaises(IntegrityError):
            downloader.download()
        self.assertFalse(downloader.path.exists())
        self.assertEqual(json.loads(downloader.state_path.read_text())["completed"], {})
        self.assertEqual(downloader.completed_ranges(), ())
        self.downloader().download()

    def test_incomplete_chunks_are_not_recorded(self):
        downloader = self.downloader(fetch=lambda p, f, s, e: b"")
        with self.assertRaises(DownloadError):
            downloader.download()
        self.assertEqual(downloader.completed_ranges(), ())

    def test_cancel_then_resume(self):
        cancel = threading.Event()

        def fetch(peer, filename, start, end):
            if start >= 2048:
                cancel.set()
                raise OSError("cancelled")
            return self.data[start:end]

        config = DownloadConfig(segment_size=2048, workers=1, attempts_per_chunk=1)
        with self.assertRaisesRegex(DownloadError, "cancelled"):
            self.downloader(fetch=fetch, config=config, cancel=cancel).download()
        self.downloader(config=config).download()
        self.assertTrue(all(start >= 2048 for _, start, _ in self.calls))

    def test_empty_file(self):
        info = FileInfo("empty.bin", 0, hashlib.md5(b"").hexdigest())
        self.assertEqual(self.downloader(info).download().read_bytes(), b"")
        self.assertEqual(self.calls, [])

    def test_existing_final_file_is_verified_and_not_redownloaded(self):
        self.shared.mkdir()
        (self.shared / self.info.filename).write_bytes(self.data)
        self.downloader().download()
        self.assertEqual(self.calls, [])
        (self.shared / self.info.filename).write_bytes(b"different")
        with self.assertRaisesRegex(DownloadError, "refusing to overwrite"):
            self.downloader().download()

    def test_checkpoint_identity_and_corrupt_json_rejected(self):
        first = self.downloader(fetch=lambda *args: b"")
        with self.assertRaises(DownloadError):
            first.download()
        with self.assertRaisesRegex(DownloadError, "metadata changed"):
            self.downloader(config=DownloadConfig(segment_size=512)).download()
        first.state_path.write_text("not json")
        with self.assertRaisesRegex(DownloadError, "unreadable checkpoint"):
            self.downloader().download()

    def test_live_file_lock_blocks_second_downloader(self):
        first = self.downloader()
        with first._file_lock():
            with self.assertRaisesRegex(DownloadError, "another process"):
                self.downloader().download()

    def test_orphan_partial_is_not_trusted(self):
        downloader = self.downloader()
        downloader.part_path.parent.mkdir(parents=True)
        downloader.part_path.write_bytes(b"wrong")
        self.assertEqual(downloader.download().read_bytes(), self.data)


class MetadataTests(unittest.TestCase):
    def test_lohita_object_adapter(self):
        peer = SimpleNamespace(ip="127.0.0.1", port="3000", start_byte=0, end_byte=10, timestamp=100)
        tracker = SimpleNamespace(filename="file.bin", filesize=10, md5="A" * 32, peers=[peer])
        info = FileInfo.from_tracker(tracker)
        self.assertEqual(info.peers[0].port, 3000)
        self.assertEqual(info.md5, "a" * 32)

    def test_cache_parser_comments_and_optional_description(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "file.track"
            path.write_text("# comment\nFilename: file\nFilesize: 10\nMD5: " + "a" * 32
                            + "\n127.0.0.1:3000:0:10:100\n")
            self.assertEqual(FileInfo.from_cache(path).peers[0].end_byte, 10)
            path.write_text("Filename: file\nFilesize: 10\n")
            with self.assertRaises(ValueError):
                FileInfo.from_cache(path)

    def test_reject_unsafe_metadata_and_configuration(self):
        for name in ("../evil", "a/b", "a\\b", "a\nb", "a>b", ".", ""):
            with self.subTest(name=name), self.assertRaises(ValueError):
                FileInfo(name, 0, "a" * 32)
        for options in ({"chunk_size": 1025}, {"workers": 0}, {"segment_size": 0},
                        {"refresh_interval": float("nan")}, {"workers": 1.5},
                        {"socket_timeout": -1}, {"attempts_per_chunk": 0}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                DownloadConfig(**options)
        with self.assertRaises(ValueError):
            FileInfo("file", 5, "a" * 32, (Peer("localhost", 1234, 0, 6, 1),))


if __name__ == "__main__":
    unittest.main()
