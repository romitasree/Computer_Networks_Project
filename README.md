# Computer_Networks_Project

## Part 4: Tracker Protocol and Configuration

Contributor: Risha Mothukuru (rxm220101)

Implemented in Python 3 using only the standard library.

### Files

- `part4/config.py`: validated tracker and peer configuration readers
- `part4/protocol.py`: tracker request and reply-line parsing/formatting
- `part4/config/`: configuration examples for the tracker and three peers
- `part4/tests/test_python.py`: automated tests
- `part4/results/part4-tests.txt`: Python test results

### Run tests

From the repository root:

    python3 -m unittest discover -s part4/tests -p 'test_python.py' -v

Or:

    make -C part4 test

### Configuration

`clientThreadConfig.cfg`: tracker port, tracker IPv4 address, refresh
interval in seconds, one setting per line.

`serverThreadConfig.cfg`: peer listening port and shared directory,
one setting per line.

`sconfig`: tracker listening port and tracker directory, one per line.
This sconfig ordering is an implementation choice.

Examples use a 900-second refresh interval and localhost.
For separate machines, configure the reachable tracker IPv4 address.
Relative storage paths are relative to the application's working
directory. Required settings must be present.

### Python API

Configuration functions return frozen dataclass objects:

    from part4.config import (
        config_load_client,
        config_load_peer_server,
        config_load_tracker,
    )

Protocol parsers return dictionaries; formatters accept dictionaries
and return framed strings ending with LF:

    from part4.protocol import (
        protocol_parse_request,
        protocol_format_request,
        protocol_parse_reply,
        protocol_format_reply,
    )

Invalid input raises ValueError.

Example:

    message = protocol_format_request({"type": "list"})
    wire_bytes = message.encode("utf-8")

Supports createtracker, updatetracker, REQ LIST, GET, and their
individual reply control lines.

### Integration

This module must be called from the group's tracker and peer code.
It does not implement socket I/O, periodic update scheduling, or
peer file transfers.

Networking code must handle partial TCP reads/writes and assemble
complete lines before parsing. LIST receivers must verify item
indexes and counts. GET receivers must collect tracker-file bytes
and calculate/verify MD5 before caching; this module validates MD5
text format only. The group must agree on byte-range end semantics.

### Validation and dependencies

All 10 Python test methods passed, including multiple valid and
invalid cases. Only Python standard-library modules are used.
Full-system network integration remains to be tested.

