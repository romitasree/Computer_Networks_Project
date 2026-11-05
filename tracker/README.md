# Tracker Server — Part 1 (Server Core)

CS 4390 P2P file-sharing project. This folder is the **tracker server core**:
a multi-threaded TCP server that creates tracker files (`createtracker`),
keeps them up to date (`updatetracker`) and hands `LIST` / `GET` to Part 2.

Written in Python 3 using **only the standard library** (no installation of
packages needed).

## 1. Installation

| Requirement | Details |
|---|---|
| Python | 3.8 or newer (`python3 --version`) |
| OS | Linux, macOS or Windows |
| Packages | none |

```bash
cd tracker
make            # checks Python, creates the ./tracker launcher
```
Without `make`, nothing needs building: run `python3 tracker.py`.

## 2. Configuration — `sconfig`

Plain text, one value per line (blank lines and `#` comments are ignored):

```
3490         <- line 1: port the tracker listens on
torrents     <- line 2: shared folder where the .track files are stored
900          <- line 3 (optional): dead-peer timeout in seconds (default 900 = 15 min)
```
A relative folder is resolved next to the `sconfig` file. Bad values stop the
tracker with a clear message. Each machine keeps its own `sconfig`.

## 3. Running

```bash
./tracker               # or: python3 tracker.py      (reads ./sconfig)
./tracker --fresh       # delete old *.track files first (final demo needs an empty server)
./tracker -v            # also print every raw request
./tracker -c other/sconfig -p 4000
```

| `make` target | Does |
|---|---|
| `make` | check setup, create `./tracker` |
| `make run` / `make fresh` | start the tracker (`fresh` = empty torrents folder) |
| `make test` | run the 38 automated tests |
| `make demo` | multi-threading demo (tracker must already be running) |
| `make clean` | remove generated files |

On start-up the tracker prints the port, the machine's LAN address (give this
to the peers' `clientThreadConfig.cfg`), the shared folder and which code
answers LIST/GET. Stop it with **Ctrl+C** (a summary is printed).

### Reading the console

One line per event: `[time] thread  TAG  details`

```
[14:02:11] worker-1  CREATE  movie1.avi (109283519 bytes) by 202.141.60.10:2097 -> succ
[14:02:15] worker-2  UPDATE  movie1.avi by 10.126.155.17:20182 range 1500-123821 -> succ (new peer)
[14:02:16] worker-3  LIST    10.0.0.7 asked for the list of tracker files
[14:02:17] worker-4  GET     movie1.avi.track sent to 10.0.0.7
[14:09:30] worker-9  PURGE   10.0.0.9:2099 removed from movie1.avi (no update for over 900s)
```
`TAG` is CREATE, UPDATE, LIST, GET, PURGE (dead peer removed), ERROR or IGNORE.
The `worker-N` column shows which thread served the request — different
numbers at the same second prove requests run in parallel.

## 4. Manual testing

`tracker_client.py` sends one command and prints the reply (spec: "option for
manual testing"). Use it from any machine:

```bash
python3 tracker_client.py <tracker-ip> 3490 REQ LIST
python3 tracker_client.py <tracker-ip> 3490 createtracker movie1.avi 1000 My_description c68c2ee8bfca4e898b396e7a935a1d92 192.168.1.30 2097
python3 tracker_client.py <tracker-ip> 3490 updatetracker movie1.avi 0 500 192.168.1.31 2098
python3 tracker_client.py <tracker-ip> 3490 GET movie1.avi.track
python3 tracker_client.py <tracker-ip> 3490          # interactive mode
```
`nc <ip> 3490` / `telnet` also work; commands may be typed with or without `<...>`.

## 5. Protocol handled by this part

One request per connection; the tracker replies and closes.

| Request | Reply |
|---|---|
| `<createtracker filename filesize description md5 ip port>` | `<createtracker succ>` created · `<createtracker ferr>` tracker file already exists · `<createtracker fail>` bad request / cannot write |
| `<updatetracker filename start_byte end_byte ip port>` | `<updatetracker filename succ>` · `<updatetracker filename ferr>` no such tracker file · `<updatetracker filename fail>` any other error |
| `<REQ LIST>` , `<GET x.track>` | passed to Part 2 |
| anything else | `<error unknown command>` |

Tracker file `torrents/<filename>.track` (exactly as in the project text):

```
Filename: movie1.avi
Filesize: 109283519
Description: Ghost and the Darkness, DVD Rip
MD5: c68c2ee8bfca4e898b396e7a935a1d92
#list of peers follows next
202.141.60.10:2097:0:109283519:1174259923
10.126.155.17:20182:1500:123821:1174263923
```
Peer line = `ip:port:start_byte:end_byte:timestamp` (Unix seconds).

## 6. Code design

### Files

| File | Role |
|---|---|
| `tracker.py` | the server (this part) |
| `tracker_client.py` | manual test client |
| `demo_multithread.py` | demo + self-check of the threading |
| `test_tracker.py` | 38 automated tests (real sockets) |
| `sconfig`, `Makefile` | configuration, build/run helpers |

### Threading model

```
main thread: accept() ──► for each connection ──► new Thread "worker-N"
                                                      │ read one request line
                                                      │ parse + validate
                                                      │ createtracker / updatetracker / LIST / GET
                                                      │ send reply
                                                      └ close connection, thread ends
```
* Every connection is handed to its own thread **immediately**; a worker serves
  one peer, sends the reply and closes (spec: tracker section).
* A silent client only ties up *its own* worker and is dropped after 10 s, so it
  cannot block anybody else.
* An exception in a worker is caught and logged; the tracker keeps running.

### Keeping tracker files correct under concurrency

* `file_lock` makes every create / update a single critical section, so two
  simultaneous `createtracker` for one file give exactly one `succ` and the
  rest `ferr`, and simultaneous updates never overwrite each other.
* Files are written to `name.track.tmp`, flushed, then swapped in with
  `os.replace` (atomic). A reader (Part 2's LIST/GET) therefore sees the old
  file or the new file, never a half-written one, and a crash cannot corrupt
  a tracker file. Stale `.tmp` files are removed at start-up.

### updatetracker rules (spec section "Tracker Server", item 4)

1. File missing → `ferr` (nothing is created).
2. Peer `ip:port` already listed → its range and timestamp are refreshed in place.
3. New peer → new entry, timestamp = server's current time.
4. Every *other* peer whose last update is older than the dead-peer timeout is removed.

### Input validation ("handle all errors")

File names may not contain `/`, `\`, `..`, `<`, `>` or control characters (no
escaping the torrents folder); sizes, bytes and ports must be integers; port
1–65535; `start ≤ end ≤ filesize`; request ≤ 4096 bytes. Invalid requests get
`fail` and change nothing.

### Decisions where the spec is silent (documented assumptions)

* **Message forms** — framed `<...>` and un-framed commands, any letter case, `\n`, `\r\n`
  or no newline are all accepted (the project asks for manual testing).
* **Description** — optional; may contain spaces (the last three fields are always
  md5, ip, port). `_` is stored as a space, matching the project example.
* **MD5 field** — stored as given (not required to be 32 hex digits) so manual tests work.
* **Dead peer** — no update within the timeout (default 900 s = the spec's default refresh
  interval). Purging happens during `updatetracker`.
* **Seed range** — `createtracker` stores `0:filesize` (as in the spec example).
* **Fallback LIST/GET** — see below.

## 7. Interface with Part 2

`tracker.py` automatically imports `tracker_queries.py` from the same folder and calls

```python
handle_list_req(sock, torrents_dir)
handle_get_req(sock, filename, torrents_dir)      # filename as sent, e.g. movie1.avi.track
```
The start-up banner says whether `tracker_queries.py` or the built-in fallback is
answering. The fallback (`fallback_list_req` / `fallback_get_req` in `tracker.py`)
follows the spec formats so Part 1 can be demonstrated alone. Leave it in place: it
is only used when Part 2's module is absent or fails to import. If a Part 2 handler
raises, the worker logs it, replies `<error internal>` and the tracker keeps serving.

`<REP GET END md5>` carries the MD5 of the tracker-file data that was sent
(spec: "compare the MD5 checksum of the received data"), not the MD5 of the shared file.

## 8. Tests

```bash
make test        # 38 tests, ~10 s
```
Covers: spec example byte-for-byte, duplicates, validation, manual command forms,
update add/refresh/purge, `ferr`/`fail` replies, 30-way create race, 40 simultaneous
updates, slow client not blocking others, timeouts, oversize/garbage input, a crashing
Part 2 handler, config parsing, `--fresh`, LIST/GET formats and MD5.

## 9. Sources and citations

* Python standard library only: [`socket`](https://docs.python.org/3/library/socket.html),
  [`threading`](https://docs.python.org/3/library/threading.html),
  [`os.replace`](https://docs.python.org/3/library/os.html#os.replace),
  [`hashlib`](https://docs.python.org/3/library/hashlib.html),
  [`argparse`](https://docs.python.org/3/library/argparse.html),
  [`unittest`](https://docs.python.org/3/library/unittest.html).
* Course material: project description CS 4390-501 and the instructor's
  `skeleton_tracker` (command dispatch, accepted message forms).
* Concepts: Beej's Guide to Network Programming, <https://beej.us/guide/bgnet/>.
* No third-party code or packages are used.
