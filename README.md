# Computer Networks Project

## Person 3: Peer Downloading

Python 3.9+ on Linux/macOS. No third-party packages are needed.

The `peer_download` package implements sequential segment scheduling with
concurrent workers, requests of at most 1024 bytes, timestamp-based peer
selection, failover, partial-range assembly, persistent resume records, and
whole-file MD5 verification. It exposes callbacks for the team's networking
and tracker code. Existing C starter files are retained as reference material.

```sh
make check
make test
```

Tests include actual localhost TCP peers, multi-peer binary transfers,
recovery, failures, tracker callbacks, and the command-line entry point.

For a cached tracker already obtained and verified by the tracker client:

```sh
python3 -m peer_download --config examples/download_config.json \
  --tracker peer1/cache/example.bin.track

# At peer startup, resume all files still represented in its tracker cache:
python3 -m peer_download --config examples/download_config.json --resume-all
```

The CLI uses the reference chunk transport described in
[the integration guide](docs/DOWNLOADING.md). It operates from cached tracker
snapshots. For live tracker refreshes, progress updates, and serving downloaded
bytes, connect the downloader to the team's Python peer uploader using the
[integration example](docs/DOWNLOADING.md#team-integration-api).

The [midterm demo checklist](docs/DOWNLOADING.md#midterm-demo-checklist) shows
how to capture evidence of a transfer between two machines and how to run the
focused concurrency test. The localhost tests provide repeatable checks;
the demo checklist covers a transfer using two separate machines.
