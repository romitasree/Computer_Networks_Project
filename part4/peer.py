"""Run the Part 4 peer uploader and tracker heartbeat from assignment configs."""

import argparse
import json
import logging
from pathlib import Path
import signal
import threading

from .config import config_load_client, config_load_peer_server
from .tracker_client import TrackerClient
from .uploader import PeerUploader


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--peer-dir", type=Path, required=True)
    parser.add_argument("--seed", action="append", default=[], metavar="FILENAME")
    parser.add_argument("--description", default="-")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(threadName)s %(message)s")
    try:
        peer_dir = args.peer_dir.resolve()
        client = config_load_client(peer_dir / "clientThreadConfig.cfg")
        server = config_load_peer_server(peer_dir / "serverThreadConfig.cfg")
        settings = {"bind_host": "0.0.0.0", "timeout": 5.0, "workers": 16,
                    "backlog": 64, "max_tracker_response": 4194304, "cache_directory": "cache"}
        path = peer_dir / "peerRuntimeConfig.json"
        if path.exists():
            custom = json.loads(path.read_text())
            if not isinstance(custom, dict) or set(custom) - set(settings):
                raise ValueError("Unknown runtime configuration settings")
            settings.update(custom)
        cache = peer_dir / settings.pop("cache_directory")
        tracker = TrackerClient(client.tracker_ip, client.tracker_port, cache,
                                settings["timeout"], settings.pop("max_tracker_response"))
        peer = PeerUploader(peer_dir / server.shared_directory, tracker,
                            server.listen_port, client.refresh_seconds, **settings)
        for filename in args.seed:
            peer.add_seed(filename, args.description)
        stop = threading.Event()
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: stop.set())
        with peer:
            stop.wait()
        logging.info("Peer terminated")
        return 0
    except (OSError, ValueError, TypeError) as exc:
        parser.exit(1, f"peer: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
