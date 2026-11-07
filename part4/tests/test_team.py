"""Compatibility with actual tracker/query/downloader branches; see check_team."""
import hashlib
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import unittest

from part4.uploader import PeerUploader
from part4.tracker_client import TrackerClient

TEAM = os.environ.get("PART4_TEAM_CODE")
if TEAM:
    sys.path.insert(0, TEAM)
    sys.path.insert(0, str(Path(TEAM) / "tracker"))
    from tracker import Config, TrackerServer
    from tracker_queries import handle_list_req, handle_get_req
    from peer_download import Downloader, DownloadConfig, FileInfo
    from peer_download.models import Peer
    from peer_download.transport import TCPChunkClient


@unittest.skipUnless(TEAM, "Run python3 -m part4.check_team to test teammate branches")
class TeamIntegration(unittest.TestCase):
    def test_actual_tracker_downloader_and_partial_upload(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tracker = TrackerServer(Config(port=0, torrents_dir=str(root / "torrents"), quiet=True),
                                    handle_list_req, handle_get_req)
            tracker.prepare_directory()
            tracker.start()
            thread = threading.Thread(target=tracker.serve_forever)
            thread.start()
            peers = []
            try:
                payload = b"<GET invalid>\n" + bytes(range(256)) * 100
                seed_dir = root / "seed"
                seed_dir.mkdir()
                (seed_dir / "movie.bin").write_bytes(payload)
                client = TrackerClient("127.0.0.1", tracker.port, root / "seed-cache", timeout=2)
                seed = PeerUploader(seed_dir, client, 0, .1, bind_host="127.0.0.1")
                seed.add_seed("movie.bin")
                seed.start()
                peers.append(seed)
                # Explicitly synchronize the first registration for deterministic tests.
                seed.heartbeat_once()
                self.assertEqual(client.list_files()[0]["filename"], "movie.bin")
                snapshot = client.get_tracker("movie.bin")
                self.assertEqual(snapshot.md5, hashlib.md5(payload).hexdigest())

                downloader_client = TrackerClient("127.0.0.1", tracker.port, root / "cache", timeout=2)
                uploader = PeerUploader(root / "download", downloader_client, 0, .1, bind_host="127.0.0.1")
                uploader.start()
                peers.append(uploader)
                info = FileInfo.from_tracker(snapshot)
                downloaded = Downloader(info, root / "download", root / "cache",
                    config=DownloadConfig(segment_size=4096, workers=3, refresh_interval=.1),
                    refresh_tracker=uploader.refresh_tracker,
                    report_segment=uploader.report_segment)
                uploader.attach_downloader(downloaded)
                self.assertEqual(downloaded.download().read_bytes(), payload)
                uploader.heartbeat_once()
                after = downloader_client.get_tracker("movie.bin")
                mine = next(p for p in after.peers if p.port == uploader.listen_port)
                self.assertEqual((mine.start_byte, mine.end_byte), (0, len(payload)))

                # Seed can disappear; the completed downloading peer keeps serving.
                seed.close()
                peer = Peer("127.0.0.1", uploader.listen_port, 0, len(payload), 0)
                self.assertEqual(TCPChunkClient()(peer, "movie.bin", 0, 1024), payload[:1024])
                self.assertEqual(TCPChunkClient()(peer, "movie.bin", len(payload)-7, len(payload)), payload[-7:])
                # Duplicate seed registration must verify tracker identity first.
                extra_dir = root / "extra"
                extra_dir.mkdir()
                (extra_dir / "movie.bin").write_bytes(payload)
                extra = PeerUploader(extra_dir, downloader_client, 0, .1, bind_host="127.0.0.1")
                extra.add_seed("movie.bin")
                extra.start()
                peers.append(extra)
                extra.heartbeat_once()
                self.assertIn(extra.listen_port, [p.port for p in client.get_tracker("movie.bin").peers])
            finally:
                for peer in reversed(peers):
                    peer.close()
                tracker.shutdown()
                thread.join(timeout=3)
                self.assertFalse(thread.is_alive())
