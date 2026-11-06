#!/usr/bin/env python3
"""
test_tracker.py -- automated tests for the tracker server core (Part 1).

Each test talks to a real tracker over real TCP sockets (on a random free
port, in a temporary folder), exactly as a peer would.

Run:  python3 test_tracker.py          (or: make test)
"""
import hashlib
import os
import shutil
import socket
import tempfile
import threading
import time
import unittest

import tracker

MD5 = "c68c2ee8bfca4e898b396e7a935a1d92"


def ask(port, message, raw=False, timeout=5):
    """Send one request, return the full reply (until the tracker closes)."""
    data = message if raw else message + "\n"
    with socket.create_connection(("127.0.0.1", port), timeout=timeout) as s:
        s.sendall(data.encode())
        out = b""
        while True:
            chunk = s.recv(4096)
            if not chunk:
                return out.decode()
            out += chunk


class ServerCase(unittest.TestCase):
    """Starts a fresh tracker (random port, temp folder) for every test."""
    dead_timeout = 900
    client_timeout = 3.0

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        cfg = tracker.Config(port=0, torrents_dir=self.dir, quiet=True,
                             dead_peer_timeout=self.dead_timeout,
                             client_timeout=self.client_timeout)
        self.server = tracker.TrackerServer(cfg)
        self.server.start()
        self.port = self.server.port
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        shutil.rmtree(self.dir, ignore_errors=True)

    def track(self, name):
        with open(os.path.join(self.dir, name + ".track"), encoding="utf-8") as f:
            return f.read()

    def rewrite(self, name, text):
        """Replace a tracker file's content (to fake old timestamps)."""
        with open(os.path.join(self.dir, name + ".track"), "w", encoding="utf-8") as f:
            f.write(text)

    def create(self, name="a.bin", size=100, ip="10.0.0.1", port=5000):
        return ask(self.port, f"<createtracker {name} {size} desc {MD5} {ip} {port}>")


# ---------------------------------------------------------------------------
class CreateTrackerTests(ServerCase):
    def test_spec_example_produces_spec_file(self):
        """The exact example from the project description."""
        r = ask(self.port, "<createtracker movie1.avi 109283519 "
                           "Ghost_and_the_Darkness,_DVD_Rip "
                           "c68c2ee8bfca4e898b396e7a935a1d92 202.141.60.10 2097>")
        self.assertEqual(r, "<createtracker succ>\n")
        lines = self.track("movie1.avi").splitlines()
        self.assertEqual(lines[:5], [
            "Filename: movie1.avi",
            "Filesize: 109283519",
            "Description: Ghost and the Darkness, DVD Rip",
            "MD5: c68c2ee8bfca4e898b396e7a935a1d92",
            "#list of peers follows next"])
        self.assertRegex(lines[5], r"^202\.141\.60\.10:2097:0:109283519:\d{10}$")
        self.assertEqual(len(lines), 6)

    def test_timestamp_is_current_time(self):
        before = int(time.time())
        self.create()
        stamp = int(self.track("a.bin").splitlines()[-1].split(":")[-1])
        self.assertTrue(before <= stamp <= int(time.time()))

    def test_duplicate_gives_ferr_and_keeps_original(self):
        self.assertEqual(self.create(ip="1.1.1.1"), "<createtracker succ>\n")
        self.assertEqual(self.create(ip="2.2.2.2"), "<createtracker ferr>\n")
        self.assertIn("1.1.1.1:5000", self.track("a.bin"))
        self.assertNotIn("2.2.2.2", self.track("a.bin"))

    def test_description_optional_and_with_spaces(self):
        self.assertEqual(ask(self.port, f"createtracker n1 10 {MD5} 1.1.1.1 99"),
                         "<createtracker succ>\n")
        self.assertIn("Description: \n", self.track("n1"))
        self.assertEqual(ask(self.port, f"createtracker n2 10 two words here {MD5} 1.1.1.1 99"),
                         "<createtracker succ>\n")
        self.assertIn("Description: two words here\n", self.track("n2"))

    def test_invalid_requests_fail_and_create_nothing(self):
        bad = [
            f"<createtracker ../evil 5 d {MD5} 1.1.1.1 1>",     # path traversal
            f"<createtracker a/b 5 d {MD5} 1.1.1.1 1>",
            f"<createtracker x -5 d {MD5} 1.1.1.1 1>",          # negative size
            f"<createtracker x abc d {MD5} 1.1.1.1 1>",         # size not a number
            f"<createtracker x 5 d {MD5} 1.1.1.1 0>",           # port 0
            f"<createtracker x 5 d {MD5} 1.1.1.1 70000>",       # port too big
            f"<createtracker x 5 d {MD5} 1.1.1.1 http>",        # port not a number
            "<createtracker x 5 1.1.1.1 80>",                   # missing md5
            "<createtracker>",
            "<createtracker x 5 d not-a-hash 1.1.1.1 80>",      # md5 is not a hash
            f"<createtracker x 5 d {MD5[:-1]} 1.1.1.1 80>",     # 31 hex digits
            f"<createtracker x 5 d {MD5}0 1.1.1.1 80>",         # 33 hex digits
            f"<createtracker x 5 d {'g' * 32} 1.1.1.1 80>",     # 32 chars, not hex
            "<createtracker x 5 d md5 1.1.1.1 80>",
        ]
        for msg in bad:
            self.assertEqual(ask(self.port, msg), "<createtracker fail>\n", msg)
        self.assertEqual(os.listdir(self.dir), [])

    def test_md5_must_be_32_hex_digits(self):
        """A bad MD5 is refused (peers could not use the tracker file)."""
        for bad in ("not-a-hash", "abc", MD5[:-1], MD5 + "0", "g" * 32, "0x" + MD5[2:]):
            r = ask(self.port, f"<createtracker h.bin 10 desc {bad} 1.1.1.1 80>")
            self.assertEqual(r, "<createtracker fail>\n", bad)
        self.assertEqual(os.listdir(self.dir), [])            # nothing was created
        # ...and a bad MD5 with a multi-word description is refused too
        self.assertEqual(ask(self.port, "<createtracker h.bin 10 two words oops 1.1.1.1 80>"),
                         "<createtracker fail>\n")

    def test_uppercase_md5_accepted_and_stored_lowercase(self):
        r = ask(self.port, f"<createtracker h.bin 10 d {MD5.upper()} 1.1.1.1 80>")
        self.assertEqual(r, "<createtracker succ>\n")
        self.assertIn(f"MD5: {MD5}\n", self.track("h.bin"))

    def test_manual_forms_are_accepted(self):
        """Un-framed, upper-case, CRLF and no-newline commands (manual testing)."""
        self.assertEqual(ask(self.port, f"createtracker m1 5 d {MD5} 1.1.1.1 80"),
                         "<createtracker succ>\n")
        self.assertEqual(ask(self.port, f"CREATETRACKER m2 5 d {MD5} 1.1.1.1 80"),
                         "<createtracker succ>\n")
        self.assertEqual(ask(self.port, f"<createtracker m3 5 d {MD5} 1.1.1.1 80>\r\n",
                             raw=True), "<createtracker succ>\n")
        self.assertEqual(ask(self.port, f"<createtracker m4 5 d {MD5} 1.1.1.1 80>",
                             raw=True), "<createtracker succ>\n")   # no newline


class UpdateTrackerTests(ServerCase):
    def test_new_peer_is_added(self):
        self.create("u.bin", 50, "1.1.1.1", 1000)
        r = ask(self.port, "<updatetracker u.bin 0 25 2.2.2.2 2000>")
        self.assertEqual(r, "<updatetracker u.bin succ>\n")
        self.assertRegex(self.track("u.bin"), r"\n2\.2\.2\.2:2000:0:25:\d+\n$")

    def test_existing_peer_is_refreshed_not_duplicated(self):
        self.create("u.bin", 50, "1.1.1.1", 1000)
        ask(self.port, "<updatetracker u.bin 0 25 2.2.2.2 2000>")
        # age the entry, then update again: timestamp must move to "now"
        text = self.track("u.bin").replace(
            [l for l in self.track("u.bin").splitlines() if l.startswith("2.2.2.2")][0],
            "2.2.2.2:2000:0:25:1000000000")
        self.rewrite("u.bin", text)
        ask(self.port, "<updatetracker u.bin 0 50 2.2.2.2 2000>")
        t = self.track("u.bin")
        self.assertEqual(t.count("2.2.2.2:2000:"), 1)
        entry = [l for l in t.splitlines() if l.startswith("2.2.2.2")][0].split(":")
        self.assertEqual(entry[2:4], ["0", "50"])
        self.assertGreater(int(entry[4]), int(time.time()) - 5)

    def test_header_and_comments_preserved(self):
        self.create("u.bin", 50)
        ask(self.port, "<updatetracker u.bin 0 10 2.2.2.2 2000>")
        t = self.track("u.bin")
        self.assertTrue(t.startswith("Filename: u.bin\nFilesize: 50\n"))
        self.assertIn("#list of peers follows next\n", t)

    def test_missing_tracker_file_gives_ferr(self):
        self.assertEqual(ask(self.port, "<updatetracker nope 0 1 1.1.1.1 1>"),
                         "<updatetracker nope ferr>\n")
        self.assertEqual(os.listdir(self.dir), [])      # must not create it

    def test_invalid_updates_fail(self):
        self.create("u.bin", 50)
        for msg in ("<updatetracker u.bin 0 1 1.1.1.1>",          # missing port
                    "<updatetracker u.bin 9 1 1.1.1.1 80>",       # start > end
                    "<updatetracker u.bin 0 51 1.1.1.1 80>",      # beyond file size
                    "<updatetracker u.bin a b 1.1.1.1 80>",
                    "<updatetracker ../x 0 1 1.1.1.1 80>"):
            r = ask(self.port, msg)
            self.assertTrue(r.startswith("<updatetracker ") and r.endswith(" fail>\n"), (msg, r))

    def test_end_byte_equal_to_filesize_is_allowed(self):
        self.create("u.bin", 50)
        self.assertEqual(ask(self.port, "<updatetracker u.bin 0 50 2.2.2.2 80>"),
                         "<updatetracker u.bin succ>\n")

    def test_dead_peers_removed_live_peers_kept(self):
        self.create("d.bin", 10, "1.1.1.1", 1000)
        old = int(time.time()) - 5000
        with open(os.path.join(self.dir, "d.bin.track"), "a") as f:
            f.write(f"9.9.9.9:9999:0:10:{old}\n")          # dead
            f.write(f" 8.8.8.8:8888:0:5:{int(time.time())}\n")   # alive, spec-style leading space
        ask(self.port, "<updatetracker d.bin 0 10 3.3.3.3 3000>")
        t = self.track("d.bin")
        self.assertNotIn("9.9.9.9", t)
        self.assertIn("1.1.1.1:1000", t)
        self.assertIn("\n8.8.8.8:8888:0:5:", t)            # normalised, no leading space
        self.assertIn("3.3.3.3:3000", t)

    def test_updating_peer_is_never_purged_by_itself(self):
        """A peer whose own old entry is expired is refreshed, not removed."""
        self.create("p.bin", 10, "1.1.1.1", 1000)
        t = self.track("p.bin")
        self.rewrite("p.bin", t.rsplit("\n", 2)[0] + "\n1.1.1.1:1000:0:10:1000000000\n")
        ask(self.port, "<updatetracker p.bin 0 10 1.1.1.1 1000>")
        self.assertEqual(self.track("p.bin").count("1.1.1.1:1000:"), 1)


class ShortTimeoutTests(ServerCase):
    dead_timeout = 1                    # peers die after 1 second

    def test_dead_peer_timeout_comes_from_config(self):
        self.create("t.bin", 10, "1.1.1.1", 1000)
        time.sleep(2.2)
        ask(self.port, "<updatetracker t.bin 0 10 2.2.2.2 2000>")
        t = self.track("t.bin")
        self.assertNotIn("1.1.1.1", t)
        self.assertIn("2.2.2.2:2000", t)


# ---------------------------------------------------------------------------
class MultithreadingTests(ServerCase):
    client_timeout = 4.0

    def test_many_simultaneous_creates_exactly_one_wins(self):
        """30 peers race to create the same tracker file."""
        results, gate = [], threading.Barrier(30)

        def go(i):
            gate.wait()
            results.append(self.create("race.bin", 10, f"10.0.0.{i}", 4000 + i))
        ts = [threading.Thread(target=go, args=(i,)) for i in range(30)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(results.count("<createtracker succ>\n"), 1)
        self.assertEqual(results.count("<createtracker ferr>\n"), 29)

    def test_simultaneous_updates_lose_nothing(self):
        self.create("z.bin", 10)
        gate = threading.Barrier(40)

        def go(i):
            gate.wait()
            ask(self.port, f"<updatetracker z.bin 0 10 5.5.5.{i} {6000 + i}>")
        ts = [threading.Thread(target=go, args=(i,)) for i in range(40)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(self.track("z.bin").count("5.5.5."), 40)

    def test_each_connection_gets_its_own_worker_thread(self):
        for i in range(5):
            ask(self.port, "<REQ LIST>")
        self.assertEqual(len(self.server.worker_names), 5)

    def test_slow_client_does_not_block_others(self):
        """A stalled connection occupies one worker; the rest keep being served."""
        slow = socket.create_connection(("127.0.0.1", self.port))   # sends nothing
        try:
            start = time.time()
            self.assertEqual(self.create("fast.bin"), "<createtracker succ>\n")
            self.assertEqual(ask(self.port, "<REQ LIST>").splitlines()[0], "<REP LIST 1>")
            self.assertLess(time.time() - start, 1.5)
        finally:
            slow.close()

    def test_stalled_client_is_dropped_after_timeout(self):
        slow = socket.create_connection(("127.0.0.1", self.port))
        slow.settimeout(self.client_timeout + 3)
        self.assertEqual(slow.recv(10), b"")            # tracker closed on us
        slow.close()
        self.assertEqual(self.create(), "<createtracker succ>\n")   # still alive


# ---------------------------------------------------------------------------
class RobustnessTests(ServerCase):
    def test_unknown_command(self):
        self.assertEqual(ask(self.port, "<hello world>"), "<error unknown command>\n")

    def test_oversized_request_gets_error_reply_not_a_reset(self):
        """The error reply must reach the client even if it keeps sending."""
        for size in (5000, 50_000, 500_000):       # just over the limit ... flood
            for _ in range(3):
                with socket.create_connection(("127.0.0.1", self.port), timeout=5) as s:
                    s.sendall(b"x" * size)          # no newline at all
                    self.assertEqual(s.recv(200), b"<error request too long>\n", size)
        self.assertEqual(self.create(), "<createtracker succ>\n")   # server survives

    def test_oversized_line_with_newline_also_rejected(self):
        r = ask(self.port, "createtracker " + "a" * 6000 + "\n", raw=True)
        self.assertEqual(r, "<error request too long>\n")

    def test_extra_data_after_the_request_does_not_break_the_reply(self):
        """Pipelined junk after the newline is absorbed; the reply still arrives."""
        with socket.create_connection(("127.0.0.1", self.port), timeout=5) as s:
            s.sendall(b"<REQ LIST>\n" + b"junk" * 20000)
            data = b""
            while True:
                chunk = s.recv(4096)
                if not chunk:
                    break
                data += chunk
        self.assertEqual(data, b"<REP LIST 0>\n<REP LIST END>\n")

    def test_drain_discards_pending_input(self):
        """drain() empties the receive buffer so close() cannot send a reset."""
        a, b = socket.socketpair()
        self.addCleanup(a.close)
        self.addCleanup(b.close)
        a.sendall(b"z" * 100_000)
        a.shutdown(socket.SHUT_WR)
        self.assertEqual(tracker.drain(b, 1_000_000, 2.0), 100_000)

    def test_drain_is_bounded(self):
        """A client that never stops cannot hold the worker forever."""
        a, b = socket.socketpair()
        self.addCleanup(a.close)
        self.addCleanup(b.close)
        stop = threading.Event()

        def flood():
            try:
                while not stop.is_set():
                    a.sendall(b"z" * 65536)
            except OSError:
                pass
        t = threading.Thread(target=flood, daemon=True)
        t.start()
        began = time.time()
        tracker.drain(b, 10_000_000_000, 0.5)       # limited by time
        self.assertLess(time.time() - began, 2.0)
        stop.set()
        b.close()
        t.join(timeout=2)

    def test_empty_connection_ignored(self):
        socket.create_connection(("127.0.0.1", self.port)).close()
        self.assertEqual(self.create(), "<createtracker succ>\n")

    def test_binary_garbage_does_not_crash(self):
        with socket.create_connection(("127.0.0.1", self.port)) as s:
            s.sendall(b"\xff\xfe\x00\x01garbage\n")
            s.recv(100)
        self.assertEqual(self.create(), "<createtracker succ>\n")

    def test_no_temp_files_left_behind(self):
        self.create("a.bin")
        ask(self.port, "<updatetracker a.bin 0 5 2.2.2.2 22>")
        self.assertEqual(os.listdir(self.dir), ["a.bin.track"])


class ListGetFallbackTests(ServerCase):
    """The built-in LIST/GET must follow the spec's reply formats."""

    def setUp(self):
        super().setUp()
        self.server.list_handler = tracker.fallback_list_req
        self.server.get_handler = tracker.fallback_get_req

    def test_list_format(self):
        self.assertEqual(ask(self.port, "<REQ LIST>"), "<REP LIST 0>\n<REP LIST END>\n")
        self.create("b.bin", 20)
        self.create("a.bin", 10)
        self.assertEqual(ask(self.port, "REQ LIST"),
                         f"<REP LIST 2>\n<1 a.bin 10 {MD5}>\n<2 b.bin 20 {MD5}>\n<REP LIST END>\n")

    def test_get_format_and_md5_of_content(self):
        self.create("g.bin", 10)
        r = ask(self.port, "<GET g.bin.track>")
        lines = r.split("\n")
        self.assertEqual(lines[0], "<REP GET BEGIN>")
        body = r[len("<REP GET BEGIN>\n"):r.rindex("<REP GET END")]
        self.assertEqual(body, self.track("g.bin"))
        self.assertEqual(lines[-2], f"<REP GET END {hashlib.md5(body.encode()).hexdigest()}>")

    def test_get_missing_and_unsafe(self):
        self.assertEqual(ask(self.port, "<GET nope.track>"), "<GET file not found>\n")
        self.assertEqual(ask(self.port, "<GET ../../etc/passwd>"), "<GET file not found>\n")


class Part2InterfaceTests(ServerCase):
    """tracker.py must call Part 2's handlers with (sock, dir) / (sock, name, dir)."""

    def test_handlers_are_called_with_agreed_signatures(self):
        calls = []

        def fake_list(sock, torrents_dir):
            calls.append(("list", torrents_dir))
            sock.sendall(b"<REP LIST 0>\n<REP LIST END>\n")

        def fake_get(sock, filename, torrents_dir):
            calls.append(("get", filename, torrents_dir))
            sock.sendall(b"<GET file not found>\n")
        self.server.list_handler, self.server.get_handler = fake_list, fake_get
        ask(self.port, "<REQ LIST>")
        ask(self.port, "<GET movie.avi.track>")
        self.assertEqual(calls, [("list", self.dir), ("get", "movie.avi.track", self.dir)])

    def test_crashing_handler_does_not_kill_server(self):
        def boom(*a):
            raise RuntimeError("bug in part 2")
        self.server.list_handler = boom
        self.assertEqual(ask(self.port, "<REQ LIST>"), "<error internal>\n")
        self.assertEqual(self.create(), "<createtracker succ>\n")


# ---------------------------------------------------------------------------
class ConfigTests(unittest.TestCase):
    def write(self, text):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        p = os.path.join(d, "sconfig")
        with open(p, "w", newline="") as f:
            f.write(text)
        return p, d

    def test_two_line_config(self):
        p, d = self.write("3490\ntorrents\n")
        cfg = tracker.read_config(p)
        self.assertEqual((cfg.port, cfg.dead_peer_timeout), (3490, 900))
        self.assertEqual(cfg.torrents_dir, os.path.join(d, "torrents"))

    def test_three_lines_crlf_comments_blank_lines(self):
        p, _ = self.write("# tracker config\r\n\r\n4000\r\nshared\r\n60\r\n")
        cfg = tracker.read_config(p)
        self.assertEqual((cfg.port, cfg.dead_peer_timeout), (4000, 60))

    def test_absolute_folder_kept(self):
        p, d = self.write(f"4000\n{d_abs()}\n")
        self.assertEqual(tracker.read_config(p).torrents_dir, d_abs())

    def test_bad_configs_exit_with_message(self):
        for text in ("", "3490\n", "abc\ntorrents\n", "0\ntorrents\n",
                     "70000\ntorrents\n", "3490\ntorrents\nsoon\n", "3490\ntorrents\n0\n"):
            p, _ = self.write(text)
            with self.assertRaises(SystemExit, msg=repr(text)):
                tracker.read_config(p)

    def test_missing_file_exits(self):
        with self.assertRaises(SystemExit):
            tracker.read_config("/no/such/sconfig")


def d_abs():
    return os.path.abspath(os.path.join(tempfile.gettempdir(), "abs_torrents"))


class StartupTests(unittest.TestCase):
    def test_fresh_removes_tracker_files_and_stale_tmp(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        for n in ("a.track", "b.track", "c.track.tmp", "keep.txt"):
            open(os.path.join(d, n), "w").close()
        srv = tracker.TrackerServer(tracker.Config(torrents_dir=d, quiet=True))
        self.assertEqual(srv.prepare_directory(fresh=True), 2)
        self.assertEqual(os.listdir(d), ["keep.txt"])

    def test_normal_start_keeps_tracker_files_but_clears_tmp(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        for n in ("a.track", "c.track.tmp"):
            open(os.path.join(d, n), "w").close()
        srv = tracker.TrackerServer(tracker.Config(torrents_dir=d, quiet=True))
        self.assertEqual(srv.prepare_directory(), 0)
        self.assertEqual(os.listdir(d), ["a.track"])

    def test_creates_missing_folder(self):
        d = os.path.join(tempfile.mkdtemp(), "new", "torrents")
        srv = tracker.TrackerServer(tracker.Config(torrents_dir=d, quiet=True))
        srv.prepare_directory()
        self.assertTrue(os.path.isdir(d))


if __name__ == "__main__":
    unittest.main(verbosity=2)
