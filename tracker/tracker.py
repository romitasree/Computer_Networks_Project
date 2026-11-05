#!/usr/bin/env python3
"""
tracker.py -- P2P tracker server (CS 4390 project, Part 1: server core)

Responsibilities of this file
-----------------------------
1. Read the configuration file ``sconfig`` (port, shared "torrents" folder).
2. Listen for TCP connections.  Every connection is handed *immediately* to
   its own worker thread.  A worker serves exactly one request, sends the
   protocol reply and closes the connection.
3. Handle ``createtracker``  -> create <filename>.track (refuse duplicates).
   Handle ``updatetracker``  -> add / refresh a peer entry, purge dead peers.
4. Pass ``REQ LIST`` and ``GET <file>.track`` to Part 2's handlers
   (``tracker_queries.py``).  A small built-in fallback is used when that
   module is not present, so this file also runs on its own.

Usage
-----
    python3 tracker.py                 # reads ./sconfig
    python3 tracker.py -c path/sconfig # explicit config file
    python3 tracker.py --fresh         # delete old *.track files first
    python3 tracker.py -v              # also print every raw request

Only the Python standard library is used (Python 3.8 or newer).

Interface expected from Part 2 (module ``tracker_queries``)
-----------------------------------------------------------
    handle_list_req(sock, torrents_dir)
    handle_get_req(sock, filename, torrents_dir)

Tracker files are only ever replaced atomically (write temp file, then
``os.replace``), so a Part 2 reader never sees a half-written file.
"""
import argparse
import hashlib
import itertools
import os
import re
import signal
import socket
import sys
import threading
import time
from typing import NamedTuple

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------
DEFAULT_PORT = 3490
DEFAULT_DEAD_PEER_SECS = 900      # spec: refresh interval defaults to 15 min
DEFAULT_CLIENT_TIMEOUT = 10.0     # drop a client that stays silent this long
MAX_REQUEST_BYTES = 4096          # longest protocol message we accept
LISTEN_BACKLOG = 128
MAX_NAME_LEN = 200                # leaves room for ".track" in a 255 limit

# One peer line inside a tracker file:  ip:port:start:end:timestamp
PEER_LINE = re.compile(r"^\s*([A-Za-z0-9._-]+):(\d+):(\d+):(\d+):(\d+)\s*$")
HOST_RE = re.compile(r"^[A-Za-z0-9._-]+$")


class BadRequest(Exception):
    """A request that is syntactically or semantically invalid."""


class RequestTooLong(Exception):
    """The client sent more than MAX_REQUEST_BYTES without a newline."""


class Config:
    """Settings read from sconfig (never changed after start-up)."""

    def __init__(self, port=DEFAULT_PORT, torrents_dir="torrents",
                 dead_peer_timeout=DEFAULT_DEAD_PEER_SECS,
                 client_timeout=DEFAULT_CLIENT_TIMEOUT,
                 verbose=False, quiet=False):
        self.port = port
        self.torrents_dir = torrents_dir
        self.dead_peer_timeout = dead_peer_timeout
        self.client_timeout = client_timeout
        self.verbose = verbose      # print each raw request
        self.quiet = quiet          # print nothing (used by the unit tests)


class CreateRequest(NamedTuple):
    filename: str
    filesize: int
    description: str
    md5: str
    ip: str
    port: int


class UpdateRequest(NamedTuple):
    filename: str
    start: int
    end: int
    ip: str
    port: int


# --------------------------------------------------------------------------
# Request parsing and validation (pure functions, no I/O)
# --------------------------------------------------------------------------
def tokenize(line):
    """'<cmd a b c>' or 'cmd a b c'  ->  ['cmd', 'a', 'b', 'c'].

    The spec shows framed messages (<...>) but also asks for manual testing
    of un-framed commands, so both forms are accepted.
    """
    line = line.strip()
    if line.startswith("<"):
        line = line[1:]
    if line.endswith(">"):
        line = line[:-1]
    return line.split()


def check_filename(name):
    """Reject names that could escape the torrents folder or break framing."""
    if (not name or len(name) > MAX_NAME_LEN or ".." in name
            or any(c in name for c in "/\\<>")
            or any(ord(c) < 32 for c in name)):
        raise BadRequest("invalid file name")
    return name


def check_number(text, what):
    """Parse a non-negative decimal integer."""
    if not (text.isascii() and text.isdigit()):
        raise BadRequest(f"{what} must be a non-negative integer")
    return int(text)


def check_endpoint(ip, port_text):
    """Validate an 'ip port' pair.  The ':' separator may not appear in ip."""
    if not HOST_RE.fullmatch(ip):
        raise BadRequest("invalid IP address")
    port = check_number(port_text, "port")
    if not 1 <= port <= 65535:
        raise BadRequest("port out of range")
    return ip, port


def parse_create(tok):
    """<createtracker filename filesize description md5 ip port>

    The description is optional and may contain spaces, so the last three
    tokens are always md5, ip, port and everything between size and md5 is
    the description.  Underscores become spaces (spec example: the message
    carries Ghost_and_the_Darkness,_DVD_Rip, the stored file shows spaces).
    """
    if len(tok) < 6:
        raise BadRequest("expected: filename filesize [description] md5 ip port")
    filename = check_filename(tok[1])
    filesize = check_number(tok[2], "file size")
    md5 = tok[-3]
    if len(md5) > 64:
        raise BadRequest("md5 too long")
    ip, port = check_endpoint(tok[-2], tok[-1])
    description = " ".join(tok[3:-3]).replace("_", " ")
    return CreateRequest(filename, filesize, description, md5, ip, port)


def parse_update(tok):
    """<updatetracker filename start_byte end_byte ip port>"""
    if len(tok) != 6:
        raise BadRequest("expected: filename start_byte end_byte ip port")
    filename = check_filename(tok[1])
    start = check_number(tok[2], "start byte")
    end = check_number(tok[3], "end byte")
    if start > end:
        raise BadRequest("start byte is greater than end byte")
    ip, port = check_endpoint(tok[4], tok[5])
    return UpdateRequest(filename, start, end, ip, port)


def header_value(lines, key):
    """Return the value of a 'Key: value' header line, or None."""
    for line in lines:
        if line.startswith(key + ":"):
            return line.split(":", 1)[1].strip()
    return None


# --------------------------------------------------------------------------
# Socket helpers
# --------------------------------------------------------------------------
def send_text(conn, text):
    """Send a whole string; a client that has vanished is not an error."""
    try:
        conn.sendall(text.encode("utf-8"))
    except OSError:
        pass


def read_request(conn):
    """Read one request line.  Returns the stripped text, or None if empty.

    Reads until a newline, end-of-stream or timeout.  A request that arrives
    without a trailing newline (manual test tools) is still processed.
    """
    buf = b""
    while b"\n" not in buf:
        try:
            chunk = conn.recv(1024)
        except socket.timeout:
            break                       # client went quiet: use what we have
        except OSError:
            return None
        if not chunk:
            break                       # client closed its sending side
        buf += chunk
        if len(buf) > MAX_REQUEST_BYTES and b"\n" not in buf:
            raise RequestTooLong
    line = buf.split(b"\n", 1)[0]
    if len(line) > MAX_REQUEST_BYTES:
        raise RequestTooLong
    return line.decode("utf-8", errors="replace").strip() or None


# --------------------------------------------------------------------------
# Built-in LIST / GET fallback (used only if Part 2's module is missing)
# --------------------------------------------------------------------------
def fallback_list_req(sock, torrents_dir):
    """Reply to <REQ LIST> in the format required by the spec."""
    names = sorted(n for n in os.listdir(torrents_dir) if n.endswith(".track"))
    rows = []
    for name in names:
        try:
            with open(os.path.join(torrents_dir, name), encoding="utf-8") as f:
                lines = f.read().splitlines()
        except (OSError, UnicodeDecodeError):
            continue                    # vanished or unreadable: skip it
        rows.append((header_value(lines, "Filename") or name[:-6],
                     header_value(lines, "Filesize") or "0",
                     header_value(lines, "MD5") or "-"))
    out = f"<REP LIST {len(rows)}>\n"
    for index, (fname, size, md5) in enumerate(rows, start=1):
        out += f"<{index} {fname} {size} {md5}>\n"
    send_text(sock, out + "<REP LIST END>\n")


def fallback_get_req(sock, filename, torrents_dir):
    """Reply to <GET x.track> with the file body and its MD5 checksum."""
    if not filename.endswith(".track"):
        filename += ".track"
    try:
        check_filename(filename)
        with open(os.path.join(torrents_dir, filename), "rb") as f:
            body = f.read()
    except (BadRequest, OSError):
        send_text(sock, "<GET file not found>\n")
        return
    if not body.endswith(b"\n"):
        body += b"\n"
    digest = hashlib.md5(body).hexdigest()   # md5 of the tracker data sent
    sock.sendall(b"<REP GET BEGIN>\n" + body
                 + f"<REP GET END {digest}>\n".encode())


def load_query_handlers():
    """Use Part 2's tracker_queries module if present, else the fallback."""
    try:
        import tracker_queries
        return (tracker_queries.handle_list_req,
                tracker_queries.handle_get_req, "tracker_queries.py (Part 2)")
    except ModuleNotFoundError as exc:
        if exc.name != "tracker_queries":
            print(f"warning: tracker_queries.py needs a missing module: "
                  f"{exc.name}", file=sys.stderr)
    except Exception as exc:            # broken Part 2 file: keep running
        print(f"warning: could not load tracker_queries.py: {exc!r}",
              file=sys.stderr)
    return fallback_list_req, fallback_get_req, "built-in fallback"


# --------------------------------------------------------------------------
# The server
# --------------------------------------------------------------------------
class TrackerServer:
    """Multi-threaded tracker: one worker thread per incoming connection."""

    def __init__(self, cfg, list_handler=None, get_handler=None):
        self.cfg = cfg
        if list_handler and get_handler:
            self.list_handler, self.get_handler = list_handler, get_handler
            self.handler_source = "custom handlers"
        else:
            (self.list_handler, self.get_handler,
             self.handler_source) = load_query_handlers()

        self.file_lock = threading.Lock()   # guards every .track read-modify-write
        self._log_lock = threading.Lock()   # keeps log lines from interleaving
        self._stats_lock = threading.Lock()
        self.stats = {"create": 0, "update": 0, "list": 0, "get": 0,
                      "error": 0, "peak_workers": 0}
        self.worker_names = set()           # names of every worker ever used
        self._active = 0
        self._ids = itertools.count(1)
        self._stop = threading.Event()
        self._threads = []
        self.sock = None
        self.port = cfg.port

    # ---- logging --------------------------------------------------------
    def log(self, tag, message):
        """One tidy line per event:  [time] thread  TAG  message."""
        if self.cfg.quiet:
            return
        stamp = time.strftime("%H:%M:%S")
        name = threading.current_thread().name
        with self._log_lock:
            print(f"[{stamp}] {name:<9} {tag:<7} {message}", flush=True)

    def _count(self, key):
        with self._stats_lock:
            self.stats[key] += 1

    # ---- start-up / shut-down ------------------------------------------
    def prepare_directory(self, fresh=False):
        """Create the torrents folder; drop leftovers.  Returns #files removed.

        Stale ``*.track.tmp`` files (from a crash) are always removed.  With
        ``fresh`` every ``*.track`` file is removed as well, which the final
        demo requires ("no tracker files present prior to its start").
        """
        os.makedirs(self.cfg.torrents_dir, exist_ok=True)
        removed = 0
        for name in os.listdir(self.cfg.torrents_dir):
            if name.endswith(".track.tmp") or (fresh and name.endswith(".track")):
                try:
                    os.remove(os.path.join(self.cfg.torrents_dir, name))
                    removed += name.endswith(".track")
                except OSError:
                    pass
        return removed

    def start(self):
        """Create, bind and listen on the server socket."""
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("", self.cfg.port))
        self.sock.listen(LISTEN_BACKLOG)
        self.sock.settimeout(0.5)           # lets the loop notice shutdown
        self.port = self.sock.getsockname()[1]

    def serve_forever(self):
        """Accept loop: hand every connection to a brand-new worker thread."""
        while not self._stop.is_set():
            try:
                conn, addr = self.sock.accept()
            except socket.timeout:
                continue
            except OSError:
                if self._stop.is_set():
                    break
                self.log("ERROR", "accept() failed; retrying")
                time.sleep(0.1)             # e.g. out of file descriptors
                continue
            name = f"worker-{next(self._ids)}"
            thread = threading.Thread(target=self._worker, name=name,
                                      args=(conn, addr), daemon=True)
            self.worker_names.add(name)
            self._threads = [t for t in self._threads if t.is_alive()]
            self._threads.append(thread)
            thread.start()

    def shutdown(self):
        """Stop accepting and give running workers a moment to finish."""
        self._stop.set()
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
        for thread in list(self._threads):
            thread.join(timeout=2)

    # ---- worker thread --------------------------------------------------
    def _worker(self, conn, addr):
        """Serve ONE request from ONE peer, reply, close."""
        peer_ip = addr[0]
        with self._stats_lock:
            self._active += 1
            self.stats["peak_workers"] = max(self.stats["peak_workers"],
                                             self._active)
        try:
            conn.settimeout(self.cfg.client_timeout)
            try:
                line = read_request(conn)
            except RequestTooLong:
                send_text(conn, "<error request too long>\n")
                self._count("error")
                self.log("ERROR", f"{peer_ip}: request longer than "
                                  f"{MAX_REQUEST_BYTES} bytes")
                return
            if line is None:
                self.log("IGNORE", f"{peer_ip}: connection with no request")
                return
            if self.cfg.verbose:
                self.log("RECV", f"{peer_ip}: {line}")
            self._dispatch(conn, peer_ip, tokenize(line))
        except Exception as exc:            # one bad client must never kill us
            self._count("error")
            self.log("ERROR", f"{peer_ip}: {exc!r}")
            send_text(conn, "<error internal>\n")
        finally:
            try:
                conn.shutdown(socket.SHUT_WR)   # flush reply before closing
            except OSError:
                pass
            conn.close()                    # one request per connection
            with self._stats_lock:
                self._active -= 1

    def _dispatch(self, conn, peer_ip, tok):
        """Route a tokenised request to the right handler."""
        cmd = tok[0].lower() if tok else ""
        if cmd == "req" and len(tok) == 2 and tok[1].lower() == "list":
            self.list_handler(conn, self.cfg.torrents_dir)
            self._count("list")
            self.log("LIST", f"{peer_ip} asked for the list of tracker files")
        elif cmd == "get" and len(tok) == 2:
            self.get_handler(conn, tok[1], self.cfg.torrents_dir)
            self._count("get")
            self.log("GET", f"{tok[1]} sent to {peer_ip}")
        elif cmd == "createtracker":
            self._do_create(conn, peer_ip, tok)
        elif cmd == "updatetracker":
            self._do_update(conn, peer_ip, tok)
        else:
            send_text(conn, "<error unknown command>\n")
            self._count("error")
            self.log("ERROR", f"{peer_ip}: unknown command")

    # ---- file helpers ---------------------------------------------------
    def _track_path(self, filename):
        return os.path.join(self.cfg.torrents_dir, filename + ".track")

    @staticmethod
    def _write_atomic(path, text):
        """Write text so readers see the old file or the new one, never half."""
        tmp = path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except OSError:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise

    # ---- createtracker --------------------------------------------------
    def _do_create(self, conn, peer_ip, tok):
        """<createtracker filename filesize description md5 ip port>"""
        self._count("create")
        try:
            req = parse_create(tok)
        except BadRequest as exc:
            send_text(conn, "<createtracker fail>\n")
            self.log("CREATE", f"{peer_ip}: rejected ({exc}) -> fail")
            return

        path = self._track_path(req.filename)
        # The creator is a seed: it owns bytes 0..filesize.  Timestamp = now.
        text = (f"Filename: {req.filename}\n"
                f"Filesize: {req.filesize}\n"
                f"Description: {req.description}\n"
                f"MD5: {req.md5}\n"
                f"#list of peers follows next\n"
                f"{req.ip}:{req.port}:0:{req.filesize}:{int(time.time())}\n")
        status = "fail"
        with self.file_lock:                # check + create is one atomic step
            if os.path.exists(path):
                status = "ferr"
            else:
                try:
                    self._write_atomic(path, text)
                    status = "succ"
                except OSError:
                    status = "fail"
        send_text(conn, f"<createtracker {status}>\n")
        self.log("CREATE", f"{req.filename} ({req.filesize} bytes) "
                           f"by {req.ip}:{req.port} -> {status}")

    # ---- updatetracker --------------------------------------------------
    def _do_update(self, conn, peer_ip, tok):
        """<updatetracker filename start_byte end_byte ip port>"""
        self._count("update")
        name = tok[1] if len(tok) > 1 else "?"
        try:
            req = parse_update(tok)
        except BadRequest as exc:
            send_text(conn, f"<updatetracker {name} fail>\n")
            self.log("UPDATE", f"{peer_ip}: {name} rejected ({exc}) -> fail")
            return

        status, note, purged = self._apply_update(req)
        send_text(conn, f"<updatetracker {req.filename} {status}>\n")
        self.log("UPDATE", f"{req.filename} by {req.ip}:{req.port} "
                           f"range {req.start}-{req.end} -> {status}"
                           + (f" ({note})" if note else ""))
        for dead in purged:
            self.log("PURGE", f"{dead} removed from {req.filename} (no update "
                              f"for over {self.cfg.dead_peer_timeout}s)")

    def _apply_update(self, req):
        """Add/refresh the peer, drop dead peers.  Returns (status, note, purged)."""
        path = self._track_path(req.filename)
        now = int(time.time())
        with self.file_lock:
            try:
                with open(path, encoding="utf-8") as f:
                    lines = f.read().splitlines()
            except FileNotFoundError:
                return "ferr", "no such tracker file", []
            except (OSError, UnicodeDecodeError):
                return "fail", "tracker file unreadable", []

            size = header_value(lines, "Filesize")
            if size and size.isdigit() and req.end > int(size):
                return "fail", f"end byte beyond file size {size}", []

            out, purged, placed = [], [], False
            new_line = f"{req.ip}:{req.port}:{req.start}:{req.end}:{now}"
            for line in lines:
                m = PEER_LINE.match(line)
                if not m:                       # header or comment: keep as is
                    out.append(line)
                    continue
                ip, port, start, end, stamp = m.groups()
                if ip == req.ip and int(port) == req.port:
                    if not placed:              # same peer: refresh in place
                        out.append(new_line)
                        placed = True           # (duplicate lines are dropped)
                elif now - int(stamp) > self.cfg.dead_peer_timeout:
                    purged.append(f"{ip}:{port}")   # update interval elapsed
                else:
                    out.append(f"{ip}:{int(port)}:{start}:{end}:{stamp}")
            note = "refreshed"
            if not placed:
                out.append(new_line)            # new peer: timestamp = now
                note = "new peer"
            try:
                self._write_atomic(path, "\n".join(out) + "\n")
            except OSError:
                return "fail", "could not write tracker file", []
        return "succ", note, purged


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------
def read_config(path):
    """sconfig: line 1 = port, line 2 = torrents folder,
    optional line 3 = dead-peer timeout in seconds.  Blank lines and lines
    starting with '#' are ignored."""
    try:
        with open(path, encoding="utf-8") as f:
            lines = [ln.strip() for ln in f.read().splitlines()]
    except OSError as exc:
        sys.exit(f"tracker: cannot read config file '{path}': {exc}")
    lines = [ln for ln in lines if ln and not ln.startswith("#")]
    if len(lines) < 2:
        sys.exit("tracker: sconfig needs at least 2 lines: port, shared folder")

    cfg = Config()
    if not (lines[0].isascii() and lines[0].isdigit()
            and 1 <= int(lines[0]) <= 65535):
        sys.exit("tracker: sconfig line 1 must be a port number (1-65535)")
    cfg.port = int(lines[0])

    folder = lines[1]
    if not os.path.isabs(folder):           # relative to the config file
        folder = os.path.join(os.path.dirname(os.path.abspath(path)), folder)
    cfg.torrents_dir = folder

    if len(lines) >= 3:
        if not (lines[2].isascii() and lines[2].isdigit() and int(lines[2]) > 0):
            sys.exit("tracker: sconfig line 3 must be a positive number of "
                     "seconds (dead-peer timeout)")
        cfg.dead_peer_timeout = int(lines[2])
    return cfg


def local_ip():
    """Best guess of this machine's LAN address (peers need it to connect)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))    # no packet is actually sent
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def print_banner(server, removed):
    cfg = server.cfg
    count = len([n for n in os.listdir(cfg.torrents_dir) if n.endswith(".track")])
    bar = "=" * 66
    print(bar)
    print(" P2P Tracker Server - CS 4390 project, Part 1")
    print(f" Listening on  : port {server.port}   (this machine: {local_ip()})")
    print(f" Shared folder : {cfg.torrents_dir}   ({count} tracker file(s))")
    print(f" Dead peer     : no update for more than {cfg.dead_peer_timeout} s")
    print(f" LIST / GET by : {server.handler_source}")
    if removed:
        print(f" Fresh start   : removed {removed} old tracker file(s)")
    print(" Press Ctrl+C to stop")
    print(bar, flush=True)


def _terminate(signum, frame):
    """SIGTERM handler: behave exactly like Ctrl+C."""
    raise KeyboardInterrupt


def default_config_path():
    """./sconfig if it exists, otherwise the sconfig next to this script."""
    if os.path.exists("sconfig"):
        return "sconfig"
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "sconfig")


def main(argv=None):
    parser = argparse.ArgumentParser(description="P2P tracker server")
    parser.add_argument("-c", "--config", help="path to sconfig")
    parser.add_argument("-p", "--port", type=int, help="override the port")
    parser.add_argument("--fresh", action="store_true",
                        help="delete existing *.track files at start-up")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="print every raw request")
    args = parser.parse_args(argv)

    threading.current_thread().name = "main"   # tidy log column
    cfg = read_config(args.config or default_config_path())
    if args.port:
        cfg.port = args.port
    cfg.verbose = args.verbose

    server = TrackerServer(cfg)
    try:
        removed = server.prepare_directory(fresh=args.fresh)
        server.start()
    except OSError as exc:
        sys.exit(f"tracker: cannot start on port {cfg.port}: {exc}")

    try:                                    # let `kill` shut down cleanly too
        signal.signal(signal.SIGTERM, _terminate)
    except (ValueError, AttributeError, OSError):
        pass

    print_banner(server, removed)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        s = server.stats
        server.log("STOP", f"served {s['create'] + s['update'] + s['list'] + s['get']} "
                           f"requests (createtracker {s['create']}, updatetracker "
                           f"{s['update']}, list {s['list']}, get {s['get']}); "
                           f"errors {s['error']}; most workers at once: "
                           f"{s['peak_workers']}")


if __name__ == "__main__":
    main()
