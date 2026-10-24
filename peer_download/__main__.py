"""Standalone download/resume entry point for verified cached tracker files."""

import argparse
from dataclasses import fields
import json
import logging
from pathlib import Path
import signal
import threading

from . import DownloadConfig, DownloadError, Downloader, FileInfo


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path, help="downloader JSON configuration")
    parser.add_argument("--tracker", type=Path, help="one verified raw .track file in cache_dir")
    parser.add_argument("--resume-all", action="store_true", help="download/resume all cached .track files")
    args = parser.parse_args()
    if bool(args.tracker) == args.resume_all:
        parser.error("choose exactly one of --tracker or --resume-all")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cancelled = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: cancelled.set())
    signal.signal(signal.SIGTERM, lambda *_: cancelled.set())
    try:
        settings = json.loads(args.config.read_text(encoding="utf-8"))
        allowed = {f.name for f in fields(DownloadConfig)} | {"shared_dir", "cache_dir"}
        if settings.keys() - allowed:
            raise ValueError("unknown config keys: " + ", ".join(sorted(settings.keys() - allowed)))
        shared = Path(settings.pop("shared_dir"))
        cache = Path(settings.pop("cache_dir"))
        # Relative directories are relative to the config file, not the shell.
        shared = shared if shared.is_absolute() else args.config.parent / shared
        cache = cache if cache.is_absolute() else args.config.parent / cache
        config = DownloadConfig(**settings)
        trackers = sorted(cache.glob("*.track")) if args.resume_all else [args.tracker]
        if not trackers:
            logging.info("no cached tracker files to resume")
        failed = False
        for tracker in trackers:
            if cancelled.is_set():
                return 130
            try:
                info = FileInfo.from_cache(tracker)
                expected = cache / (info.filename + ".track")
                if tracker.resolve() != expected.resolve():
                    raise ValueError("tracker must be cache_dir/<filename>.track")
                Downloader(info, shared, cache, config=config, cancel=cancelled).download()
            except (DownloadError, OSError, ValueError) as exc:
                logging.error("%s: %s", tracker, exc)
                failed = True
        return 130 if cancelled.is_set() else int(failed)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        logging.error("configuration error: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
