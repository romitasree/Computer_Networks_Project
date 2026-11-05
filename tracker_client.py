#!/usr/bin/env python3
"""
tracker_client.py -- send protocol commands to a tracker by hand.

The project spec asks for the option to test the commands manually
(REQ LIST, GET, createtracker, updatetracker).  This tiny tool does that and
is what you run on the *second machine* for the midterm demo.

One-shot:
    python3 tracker_client.py 192.168.1.20 3490 REQ LIST
    python3 tracker_client.py 192.168.1.20 3490 GET movie1.avi.track
    python3 tracker_client.py 192.168.1.20 3490 createtracker movie1.avi 1000 \
            A_short_description c68c2ee8bfca4e898b396e7a935a1d92 192.168.1.30 2097
    python3 tracker_client.py 192.168.1.20 3490 updatetracker movie1.avi 0 500 192.168.1.31 2098

Interactive (type one command per line, empty line to quit):
    python3 tracker_client.py 192.168.1.20 3490
"""
import socket
import sys

TIMEOUT = 5.0


def send_command(host, port, command):
    """Open a connection, send one command, return the full reply text."""
    command = command.strip()
    if not command.startswith("<"):
        command = f"<{command}>"            # frame it like the spec shows
    with socket.create_connection((host, port), timeout=TIMEOUT) as sock:
        sock.sendall((command + "\n").encode("utf-8"))
        chunks = []
        while True:                         # tracker closes after replying
            try:
                data = sock.recv(4096)
            except socket.timeout:
                break
            if not data:
                break
            chunks.append(data)
    return b"".join(chunks).decode("utf-8", errors="replace")


def run(host, port, command):
    print(f"--> {command}")
    try:
        reply = send_command(host, port, command)
    except OSError as exc:
        print(f"!! cannot talk to tracker {host}:{port}: {exc}")
        return 1
    print("<-- " + (reply.rstrip("\n").replace("\n", "\n    ") or "(no reply)"))
    return 0


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 2
    host = argv[1]
    if not argv[2].isdigit():
        print("port must be a number")
        return 2
    port = int(argv[2])

    if len(argv) > 3:                       # one-shot mode
        return run(host, port, " ".join(argv[3:]))

    print(f"Connected target {host}:{port}.  Type a command, empty line quits.")
    try:
        while True:
            line = input("tracker> ").strip()
            if not line:
                return 0
            run(host, port, line)
    except (EOFError, KeyboardInterrupt):
        print()
        return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
