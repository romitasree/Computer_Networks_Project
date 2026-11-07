# Computer_Networks_Project

## Part 4: Peer Uploader, Tracker Client and Configuration

Contributor: Risha Mothukuru (rxm220101). Python 3.9+, standard library only.

### Run and test

From the repository root:

    make -C part4 test
    python3 -m part4.check_team

The second command reads committed Python files from origin/vikas,
origin/romita-tracker and origin/lohita into a temporary directory and tests
compatibility without merging or modifying teammates' files. Run git fetch
origin first. The test prints the exact branch commits used.

To share a complete file, place it in part4/config/peer1/shared/ and run:

    python3 -m part4.peer --peer-dir part4/config/peer1 --seed example.bin

Start the group's tracker first. Ctrl+C stops the listener and heartbeat.
The first registration happens automatically; tracker failures are logged
and retried at the next configured update interval. The uploader keeps
serving while the main peer downloads or waits for user commands.

### Configuration

clientThreadConfig.cfg has tracker port, IPv4 address, and update interval
in seconds. serverThreadConfig.cfg has listening port and shared folder.
Defaults in the example files are 900 seconds, localhost, and peer ports
5001/5002/5003. For two machines, use the tracker's reachable IP. The peer's
own IP is detected from the route to that tracker; it is not in a config.

peerRuntimeConfig.json supplies bind address, socket timeout, worker count,
listen backlog, tracker response size limit, and cache directory. Storage
paths in the CLI are resolved relative to --peer-dir.

sconfig accepts port and tracker directory, plus an optional third line for
dead-peer expiry seconds (900 by default), matching Romita's configuration.
The peer does not read sconfig; it reads its own client/server files.

### Implementation and wire contract

- config.py: validated configuration readers (ValueError on invalid input).
- protocol.py: pure tracker request/reply-line parsing and formatting.
- tracker_client.py: real TCP registration, updates, LIST, and verified GET.
- uploader.py: bounded threaded listener, seed catalog, downloader adapters,
  automatic registration, and periodic heartbeat.
- peer.py: runnable uploader/seed command-line program.
- tests/: configuration, protocol, socket and team-integration tests.

Chunk protocol matches Vikas's TCPChunkClient exactly:

    <GET filename start end>\n
    <REP GET length>\n followed by exactly length raw file bytes
    <GET invalid>\n on malformed, oversized or unavailable requests

End offsets are exclusive. Each connection handles one request and closes.
The server enforces 1..1024 bytes independently, handles fragmented request
lines, and rejects path traversal, unregistered files, symlinks and sparse
holes. Bounded workers and socket deadlines prevent unbounded waiting/queues.

Only complete local seeds use createtracker. Duplicate tracker entries are
verified against the seed's identity before joining. updatetracker advertises
only the contiguous completed prefix [0,end), because the tracker replaces
the previous range for that peer. Isolated completed segments are never
misrepresented as a complete prefix. Incomplete files are never registered
as seeds. A zero-length seed registers 0:0 and cannot serve a nonempty chunk.

GET verifies the MD5 of the exact received tracker bytes before parsing and
atomic cache replacement; it does not confuse this digest with the shared
file's checksum. LIST validates boundaries, item counts, and indexes.

### Connecting Vikas's downloader

When peer_download has been merged by the group:

    from peer_download import Downloader, DownloadConfig, FileInfo
    from part4.tracker_client import TrackerClient
    from part4.uploader import PeerUploader

Create TrackerClient and PeerUploader from the peer's configuration, start
the uploader, and register any initial complete local files with add_seed.
Use shared_dir consistently for both modules. For a requested shared file:

    info = FileInfo.from_tracker(tracker_client.get_tracker(filename))
    downloader = Downloader(
        info, shared_dir, cache_dir,
        config=DownloadConfig(refresh_interval=update_interval),
        refresh_tracker=uploader.refresh_tracker,
        report_segment=uploader.report_segment,
    )
    uploader.attach_downloader(downloader)
    path = downloader.download()

Run download() on a coordinator thread; leave the uploader running after it
returns. Its heartbeat continues advertising complete files. For partial
files the uploader consults completed_ranges() and serves .downloads/*.part
only where the downloader has durably committed the bytes. Do not run the
standalone uploader in a separate process from an attached Downloader.

The group still owns the final combined peer command loop, downloader
startup/resume orchestration and timed final-demo script. This contribution
provides the required uploader and tracker calls and tests their interfaces.

### Validation and sources

make test runs localhost tests and skips the team integration test unless
invoked through check_team. check_team runs the actual branch tracker,
Lohita's query handlers and Vikas's downloader together: registration,
verified tracker fetch, full download, periodic advertising and serving
from the downloading peer after the initial seed stops. This is localhost
integration; the required two-machine demonstration still needs testing.

Behavior follows the supplied project-CS4390-501.pdf, the team's exclusive
range guidance and peer_download/transport.py from origin/vikas. No
third-party libraries or copied external implementations are required.

