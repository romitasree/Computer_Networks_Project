# Person 3 - Peer Downloading

## Ownership and files

Vikas owns `peer_download/`, its downloader tests, and this guide. Python 3.9+
and the standard library are sufficient on Linux/macOS. `fcntl.flock` prevents
two processes with the same peer cache from downloading the same file.

| File | Responsibility |
| --- | --- |
| `peer_download/downloader.py` | Scheduling, retries, checkpoints, refresh/report hooks, completion |
| `peer_download/models.py` | Validated downloader metadata, cached tracker loader, object adapter |
| `peer_download/transport.py` | Replaceable reference chunk client |
| `peer_download/__main__.py` | Download one cached tracker or resume the peer's cache |
| `examples/download_config.json` | All downloader tuning values and storage paths |

Person 1 implements tracker serving/storage. Person 2 implements peer sockets
and upload serving. Person 4 implements tracker protocol/configuration and
verifies tracker response data before handing it to the downloader. Person 5
wires the modules together and owns the full timed demo script. The test
servers here are fixtures, not the production uploader or tracker server.

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

## Team integration API

Call `download()` immediately after receiving and verifying a tracker file.
It blocks until completion, cancellation, or a recoverable error, and returns
the final `Path`. Run it on the peer's download coordinator thread so that the
peer can continue accepting uploads and user commands. Each downloader owns
its worker pool; Person 2's `fetch_chunk` must be safe to call concurrently.

```python
from pathlib import Path
from peer_download import DownloadConfig, Downloader, FileInfo

# parsed_tracker is supplied by Person 4. Its fields are filename, filesize,
# md5 and peers; each peer has ip, port, start_byte, end_byte and timestamp.
info = FileInfo.from_tracker(parsed_tracker)

def refresh(filename):
    # Person 4 must issue GET <filename>.track, verify the tracker payload
    # checksum, atomically replace the cached raw .track file, and parse it.
    return FileInfo.from_tracker(tracker_client.get_tracker(filename))

def report(filename, start, end):
    # Person 4 adds this peer's automatically detected IP/listening port and
    # sends the exact assignment updatetracker command; raise on failure.
    tracker_client.update_tracker(filename, start, end)

downloader = Downloader(
    info,
    shared_dir=Path(peer_settings.shared_dir),
    cache_dir=Path(peer_settings.cache_dir),
    config=DownloadConfig(refresh_interval=peer_settings.update_interval),
    fetch_chunk=peer_network.fetch_chunk,
    refresh_tracker=refresh,
    report_segment=report,
)
path = downloader.download()
```

`tracker_client`, `peer_network`, and `peer_settings` above are integration
interfaces to be supplied by teammates, not modules included in this branch.

| Callback | Contract |
| --- | --- |
| `fetch_chunk(peer, filename, start, end) -> bytes` | Worker thread; return exactly `end-start` bytes or raise `OSError`. Enforce a timeout. |
| `refresh_tracker(filename) -> FileInfo` | Coordinator thread; return verified current metadata. File identity must remain the same. |
| `report_segment(filename, start, end) -> None` | Coordinator thread; update tracker only after the segment is durably recorded. Raise on failure. |

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
explicit, replaceable contract; Person 2 must implement it or inject a client
using the team's chosen format:

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

Person 2 should consult `downloader.completed_ranges()` before serving an
incomplete file from `downloader.part_path`. Do not infer availability from
the sparse file's size. Keep a downloader reference per active file, and stop
new requests during the final rename; retry from the final path if the rename
occurs between opening checks. Completed segments can be shared while later
segments download, as required by the demo.

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

## Compatibility notes from lohita

`FileInfo.from_tracker` accepts the field layout in `lohita`'s
`tracker_format.py`. At the inspected commit
`f6b479c9fdc0263a6c3c7d2bb85661212033dbcf`, that branch still needs its own fixes:
`TrackerFile.init` is not `__init__`, `parse_file` consequently lacks an
initialized peer list, query handlers call `TrackerFile.parse` instead of
`parse_file`, and writing calls `peer.to_string()` although only `__str__`
exists. Its tracker GET trailer uses the shared file MD5 where the assignment
requires verification of the received tracker data. These are documented
integration findings; Lohita's files are not modified in this branch.

## Validation and sources

`make test` runs unit tests plus localhost TCP integration tests. Coverage
includes different partial peers, concurrent workers, short final chunks,
newest-peer fallback, interruption/resume, modified local bytes, checksum
failure, refresh/update callbacks, malformed responses, timeout, and the CLI.
`make check` checks package syntax. No third-party code or packages are used.
The local assignment PDF is the behavioral source, and Lohita's existing
object layout is the compatibility reference.
