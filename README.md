# Computer Networks Project

## Vikas - Person 3: Peer Downloading

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
snapshots. Periodic tracker retrieval and updates are enabled by wiring the
documented callbacks into the team's peer program.

The `vikas` work is based on `main`. Lohita's `lohita` branch was inspected for
compatibility; her source files are not copied or changed here. See the guide
for the adapter and the remaining integration points.
