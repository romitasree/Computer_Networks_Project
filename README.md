# Computer_Networks_Project

## Part 4: Tracker Protocol and Configuration

Contributor: Risha Mothukuru
NetID: rxm220101
Course: CS 4390, Computer Networks

### Contribution

This module provides:

- Readers for tracker and peer configuration files
- Parsing and formatting of tracker requests
- Parsing and formatting of individual tracker reply lines
- Input validation and error messages
- Tests for configuration, requests, and replies
- A Makefile that builds a reusable static library

This module must be connected to the group's tracker and peer
implementations. It does not implement the tracker server, peer
file-transfer engine, or periodic update thread.

### Build and test

Requirements: a C11 compiler, make, and ar.

From the repository root, first run `cd part4`. Run the following commands inside `part4/`:

    make
    make test

The library is generated at:

    build/libpart4.a

Tests are generated in build/.
Saved test output is in results/part4-tests.txt.

To remove generated files:

    make clean

### Configuration files

config/tracker/sconfig:
1. Tracker listening port
2. Tracker storage directory

The sconfig line order is a project implementation choice and
must be used consistently by the tracker implementation.

config/peerN/clientThreadConfig.cfg:
1. Tracker port
2. Tracker IPv4 address
3. Update interval in seconds

config/peerN/serverThreadConfig.cfg:
1. Peer listening port
2. Peer shared directory

Examples are provided for peer1, peer2, and peer3.
The example refresh interval is 900 seconds, or 15 minutes.

127.0.0.1 is suitable only when connecting on the same machine.
For a two-machine demo, use the tracker machine's reachable IPv4
address in the peer client configuration files.

Relative directory settings are relative to the application's
working directory. The configuration reader does not change
directories or create storage folders.

All listed settings are required. Invalid or missing settings
produce an error rather than silently selecting defaults.

### Configuration API

Include config.h and call:

- config_load_client
- config_load_peer_server
- config_load_tracker

Each returns 0 on success or -1 on failure.
Pass an error buffer to receive an explanation.

### Request API

Include protocol.h.

protocol_parse_request:
Converts a complete command line into TrackerRequest.

protocol_format_request:
Converts TrackerRequest into a framed wire message.
Returns the byte count excluding the terminating NUL, or -1.

Supported requests:

    <createtracker filename filesize description md5 ip port>
    <updatetracker filename start end ip port>
    <REQ LIST>
    <GET filename.track>

Formatted messages end with LF.
Unframed requests are accepted for manual command input.
Descriptions must be one token; use underscores for spaces.
Use "-" to represent an absent description.

### Reply API

Include protocol_reply.h.

protocol_parse_reply:
Converts one framed reply line into TrackerReply.

protocol_format_reply:
Formats one reply line and returns its byte count, or -1.

Supported reply lines:

    <createtracker succ|fail|ferr>
    <updatetracker filename succ|fail|ferr>
    <REP LIST count>
    <index filename filesize md5>
    <REP LIST END>
    <REP GET BEGIN>
    <REP GET END md5>

The status alternatives above represent separate possible values.

### Integration responsibilities

The tracker teammate should:
- Load sconfig.
- Assemble each complete incoming request line.
- Parse it using protocol_parse_request.
- Perform the requested tracker operation.
- Format and send the appropriate reply.
- Maintain tracker storage, timestamps, and synchronization.

The peer teammate should:
- Load both peer configuration files.
- Format requests using protocol_format_request.
- Send all returned message bytes, excluding the terminating NUL.
- Assemble complete incoming reply lines.
- Parse control lines using protocol_parse_reply.

TCP can split one message across several reads or combine multiple
messages in one read. The networking code must handle this.

For LIST, the receiver must validate the sequence of item indexes
and the number of entries against the header count.

For GET, the receiver must collect the tracker-file body and verify
its MD5 against the GET END checksum before caching it.
The reply parser checks checksum syntax; it does not compute MD5.

Byte offsets are preserved as supplied. The tracker and downloader
must agree on whether the ending offset is inclusive or exclusive.

This module does not run automatic updates or downloads. The peer
implementation must schedule updates using refresh_seconds.

### Linking

Run make to build build/libpart4.a.
Add include/ to the group's compiler header search path.
Link build/libpart4.a after the source or object files that use it.

The provided Makefile builds this contribution only. Its rules
should be merged into the group's Makefile rather than replacing
a working group build.

### Dependencies

Uses the C standard library and the POSIX IPv4 conversion interface
from arpa/inet.h. No third-party runtime library is required.

### Tests

Configuration tests cover the example files, invalid settings,
missing files, and CRLF line endings.

Request and reply tests cover exact wire formats, parsed values,
malformed inputs, numeric limits, buffer sizes, and failure behavior.

These are module tests. Full network integration and the required
two-machine demo still need to be tested with the group.

