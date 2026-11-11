# Person 3 - Peer Downloading

## Ownership and files

Person 3 owns `peer_download/`, its downloader tests, and this guide. Python 3.9+
and the standard library are sufficient on Linux/macOS. `fcntl.flock` prevents
two processes with the same peer cache from downloading the same file.

| File | Responsibility |
| --- | --- |
| `peer_download/downloader.py` | Scheduling, retries, checkpoints, refresh/report hooks, completion |
| `peer_download/models.py` | Validated downloader metadata, cached tracker loader, object adapter |
| `peer_download/transport.py` | Replaceable reference chunk client |
| `peer_download/__main__.py` | Download one cached tracker or resume the peer's cache |
| `examples/download_config.json` | All downloader tuning values and storage paths |

Person 1 implements the tracker server and storage. Person 2 implements peer
sockets and upload serving. Person 4 supplies the tracker message formats and
configuration readers. The integrated peer's tracker client handles TCP,
verifies responses, and caches tracker bodies. Person 5 wires the modules
together and owns the full timed demo script. The local test uploaders are
fixtures for exercising the downloader over TCP.

## Byte ranges and peer choice

All ranges in this module are **start inclusive, end exclusive**, so `0:1024`
means bytes 0 through 1023. This matches the assignment example in which a
complete file advertises `0:filesize`. Convert at an adapter boundary if another
module uses inclusive end offsets. Chunks never exceed 1024 bytes; segments
default to 65536 bytes. A segment can contain many chunk requests and can span
multiple peers' advertised ranges. A short last chunk/segment is supported.

Segments are submitted in increasing order to a bounded worker pool; workers
can finish out of order. Their ranges never overlap. For each next chunk,
eligible peers must actually advertise its start byte. The newest timestamp
wins among equally busy eligible peers; workers prefer idle peers to spread
concurrent work across different sources. Requests stop at the selected peer's
advertised end. A failed peer is excluded for that chunk until other candidates
have been tried. Retries are bounded by `attempts_per_chunk`.

Workers return complete segments as byte strings. The coordinating thread
alone writes to the partial file, saves checkpoints, and calls the tracker
adapter. That lets workers fetch concurrently without racing to change a
shared file position or marking a partially written segment complete.

## Team integration API

Call `download()` immediately after receiving and verifying a tracker file.
It blocks until completion, cancellation, or a recoverable error, and returns
the final `Path`. Run it on the peer's download coordinator thread so that the
peer can continue accepting uploads and user commands. Each downloader owns
its worker pool. The default chunk client matches the team's Python uploader;
any replacement client must be safe to call concurrently.

Use the `TrackerClient` and `PeerUploader` from the team's Python peer code.
`TrackerClient.get_tracker()` verifies the MD5 against the exact `GET` body
bytes, parses the metadata, and atomically caches the body. The MD5 inside
that body describes the shared file and is checked separately after download.

```python
from peer_download import DownloadConfig, Downloader, FileInfo

def download_file(filename, uploader):
    """Download on a peer with an already running PeerUploader."""
    tracker_client = uploader.tracker
    info = FileInfo.from_tracker(tracker_client.get_tracker(filename))
    downloader = Downloader(
        info,
        shared_dir=uploader.shared_dir,
        cache_dir=tracker_client.cache_dir,
        config=DownloadConfig(refresh_interval=uploader.refresh_seconds),
        refresh_tracker=uploader.refresh_tracker,
        report_segment=uploader.report_segment,
    )
    uploader.attach_downloader(downloader)
    path = downloader.download()
    uploader.heartbeat_once()
    return path
```

Start the uploader before invoking this example, and leave it running while
the peer is online. The uploader supplies its IP and listening port for
tracker updates and recomputes the contiguous completed range from byte zero.
This matters because the tracker stores one range per peer: reporting each
segment separately would overwrite the earlier range. The uploader heartbeat
also keeps complete downloads advertised after `download()` returns.

| Callback | Contract |
| --- | --- |
| `fetch_chunk(peer, filename, start, end) -> bytes` | Worker thread; return exactly `end-start` bytes or raise `OSError`. Enforce a timeout. |
| `refresh_tracker(filename) -> FileInfo` | Coordinator thread; return verified current metadata. File identity must remain the same. |
| `report_segment(filename, start, end) -> None` | Coordinator thread; called after a segment is durably recorded. Aggregate the completed prefix before updating a tracker that stores one range per peer. Raise on failure. |

Completed segments are reported on restoration, after each segment, and at the
refresh interval while the file is incomplete. Failed reports are logged and
retried at the next interval. Tracker refresh failures retain the last peer
snapshot. When no work can progress, failed segments are tried again after
refresh; `max_stalled_refreshes` bounds waiting without progress. Defaults are
900 seconds and 3 stalled intervals. Inject shorter values for tests.

**After download completion**, the surrounding peer program must continue
serving the full file and periodically advertising `0:filesize` while it is
online. This includes retrying an update that failed immediately before
completion; `download()` does not keep a background heartbeat alive after it
returns. The tracker server owns dead-peer expiry.

## Reference peer-to-peer socket format

The assignment specifies the tracker messages but leaves successful
peer-to-peer chunk framing unspecified. The bundled `TCPChunkClient` uses this
contract, which the team's Python uploader implements:

```text
request: <GET filename start end>\n
success: <REP GET length>\n followed immediately by exactly length raw bytes
failure: <GET invalid>\n
```

One TCP connection serves one request and closes after the reply. A header
separates an error from arbitrary binary file bytes. The client rejects a bad
header, wrong length, premature EOF, excess data, and socket timeouts. The
uploader must independently reject ranges larger than 1024 bytes, unavailable
ranges, invalid names, and out-of-file offsets. This contract **does not
replace** the assignment's tracker `GET filename.track` reply format.

## Storage, restarts, and serving incomplete data

For `example.bin`:

```text
shared/example.bin                        # published only after MD5 succeeds
shared/.downloads/example.bin.part        # sparse, incomplete bytes
cache/example.bin.download.json           # identity and completed segment hashes
cache/example.bin.track                   # raw tracker cached by Person 4
cache/example.bin.download.lock           # advisory lock inode; intentionally kept
```

Each completed segment is flushed and fsynced before an atomic checkpoint
replacement. The record stores a SHA-256 for each completed segment. Restart
rechecks those hashes, skips valid segments, and redownloads damaged or
unrecorded segments. Bytes in an incomplete segment are deliberately not
advertised or retained as completed progress. A changed file identity or
segment size rejects an old checkpoint rather than silently mixing versions.
An invalid JSON checkpoint reports a clear error; preserve it for diagnosis
or move it aside to explicitly start a fresh record.

The uploader consults `downloader.completed_ranges()` before serving an
incomplete file from `downloader.part_path`. Availability comes from the
checkpoint ranges rather than the sparse file's size. The uploader can retry
from the final path if the atomic rename happens before opening the partial
file. Completed segments can be shared while later segments download.

When MD5 succeeds, the partial file is atomically renamed to its final path,
and only that file's `.track` and JSON record are removed. An already existing
matching final file is verified and recovered without a transfer. Different
existing final contents are never overwritten.

An MD5 mismatch leaves the partial file unpublished and marks every segment
missing. The assignment supplies no trusted per-segment hashes, so this module
cannot identify which peer sent bad data. It raises `IntegrityError`; the peer
coordinator can refresh sources and retry, and must stop serving ranges no
longer listed by `completed_ranges()`. MD5 is used for assignment compatibility,
not as a security authentication mechanism.

Pass a `threading.Event` as `cancel` for shutdown. In-flight socket operations
finish or hit their configured deadlines; no new chunks start afterward.
Completed checkpoints remain for the next run. The CLI maps SIGINT/SIGTERM to
this event and exits 130 on cancellation.

## Startup and configuration

The CLI accepts one tracker or scans `cache_dir/*.track` using `--resume-all`.
Each raw tracker must be at `cache_dir/<filename>.track`. Configuration storage
paths are relative to the JSON file; `--tracker` is relative to the working
directory. The CLI is useful before the tracker client has been integrated,
but uses only the peer snapshot in the cache. The integrated peer should pass
both tracker callbacks and apply its `clientThreadConfig.cfg` update interval
to `DownloadConfig.refresh_interval`. Person 4 retains ownership of the
assignment's peer configuration files.

The [example JSON](../examples/download_config.json) shows the standalone
downloader settings:

| Setting | What it controls |
| --- | --- |
| `shared_dir`, `cache_dir` | Separate directories for file data and resume/tracker metadata. Use distinct directories for each peer. |
| `segment_size`, `chunk_size` | Bytes saved per checkpoint and the maximum bytes per request. `chunk_size` cannot exceed 1024. |
| `workers` | Maximum segments fetched concurrently for one file. |
| `attempts_per_chunk`, `socket_timeout` | Attempts per chunk and the reference transport's socket timeout in seconds. |
| `refresh_interval`, `max_stalled_refreshes` | Seconds between tracker callbacks and the limit on refresh intervals with no completed segments. Live refresh requires the tracker callback; the standalone CLI uses its cached snapshot. |

## Midterm demo checklist

1. On machine A, start the team's tracker and uploader. Seed a file larger
   than one configured segment (64 KiB by default), and register it using
   the uploader's reachable LAN address and listening port.
2. On machine B, point the peer at the tracker on machine A and use a fresh
   download directory. Start the download through the integrated peer. For a
   downloader-only check, the standalone CLI can use a verified `.track`
   snapshot as long as it names the real uploader on machine A.
3. Capture both machines' terminal output: uploader address and served byte
   ranges on A; source address, requested ranges, and `MD5 verified` on B.
   Compare the final file's size and checksum with the seed. Include the
   worker count and segment size used; add `%(threadName)s` to the integrated
   peer's logging format if showing multiple workers in the demo logs.
4. For a separate, repeatable check of concurrent workers, run:

   ```sh
   python3 -m unittest discover -s tests -p 'test_downloader.py' \
     -k workers_use_distinct_peers_concurrently -v
   ```

   This test uses a barrier to require two workers to enter the fetch
   callback together. The local TCP tests also download from two simulated
   uploaders. The report should include separate evidence from the actual
   two-machine transfer.

## Validation and sources

`make test` runs unit tests plus localhost TCP integration tests. Coverage
includes different partial peers, concurrent workers, short final chunks,
newest-peer fallback, interruption/resume, modified local bytes, checksum
failure, refresh/update callbacks, malformed responses, timeout, and the CLI.
`make check` checks package syntax. No third-party code or packages are used.
The assignment PDF defines the required behavior. The tracker client and
peer uploader are the integration references.
