#!/usr/bin/env python3
"""
demo_multithread.py -- shows that the tracker is multi-threaded.

Run it against a *running* tracker (start ``python3 tracker.py`` first); it
can run on the same machine or on a second machine:

    python3 demo_multithread.py                      # tracker on this machine
    python3 demo_multithread.py 192.168.1.20 3490    # tracker on another machine

What it does (watch the tracker's console while it runs -- every event is
logged with the name of the worker thread that handled it):

  1. A "slow peer" connects and says nothing.  A single-threaded server would
     now be stuck waiting for it.
  2. Meanwhile N peers all send createtracker for N different files at the
     same instant.  They are all answered at once by different workers.
  3. N peers race to create the SAME file: exactly one may succeed, the rest
     must get ferr (duplicate).
  4. N peers send updatetracker for the same file at the same instant: no
     update may be lost.
"""
import socket
import sys
import threading
import time

from tracker_client import send_command

N = 10
MD5 = "c68c2ee8bfca4e898b396e7a935a1d92"


def burst(label, jobs):
    """Run jobs (callables) at exactly the same moment, return their results."""
    gate = threading.Barrier(len(jobs))
    results = [None] * len(jobs)

    def go(i):
        gate.wait()                         # release every thread together
        results[i] = jobs[i]()
    threads = [threading.Thread(target=go, args=(i,)) for i in range(len(jobs))]
    began = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    print(f"    {label}: {len(jobs)} requests finished in "
          f"{(time.time() - began) * 1000:.0f} ms")
    return results


def check(ok, text):
    print(f"    [{'PASS' if ok else 'FAIL'}] {text}")
    return ok


def main(argv):
    host = argv[1] if len(argv) > 1 else "127.0.0.1"
    port = int(argv[2]) if len(argv) > 2 else 3490
    run_id = int(time.time()) % 100000
    ask = lambda msg: send_command(host, port, msg)      # noqa: E731
    all_ok = True

    print(f"\nMulti-threading demo against tracker {host}:{port}\n")

    print("1. A slow peer connects and stays silent (holds one worker thread)")
    slow = socket.create_connection((host, port), timeout=5)
    time.sleep(0.3)

    print(f"2. {N} peers create {N} different files at the same instant")
    jobs = [lambda i=i: ask(f"createtracker demo{run_id}_{i}.bin 1000 demo {MD5} "
                            f"10.0.0.{i + 1} {5000 + i}") for i in range(N)]
    replies = burst("different files", jobs)
    all_ok &= check(all(r.strip() == "<createtracker succ>" for r in replies),
                    "every peer got <createtracker succ> while the slow peer was connected")

    print(f"3. {N} peers race to create the SAME file")
    jobs = [lambda i=i: ask(f"createtracker race{run_id}.bin 1000 demo {MD5} "
                            f"10.0.1.{i + 1} {5000 + i}") for i in range(N)]
    replies = [r.strip() for r in burst("same file", jobs)]
    all_ok &= check(replies.count("<createtracker succ>") == 1
                    and replies.count("<createtracker ferr>") == N - 1,
                    f"exactly 1 succ and {N - 1} ferr (got "
                    f"{replies.count('<createtracker succ>')} succ, "
                    f"{replies.count('<createtracker ferr>')} ferr)")

    print(f"4. {N} peers send updatetracker for the same file at the same instant")
    jobs = [lambda i=i: ask(f"updatetracker race{run_id}.bin 0 500 10.0.2.{i + 1} "
                            f"{6000 + i}") for i in range(N)]
    replies = [r.strip() for r in burst("updates", jobs)]
    all_ok &= check(all(r == f"<updatetracker race{run_id}.bin succ>" for r in replies),
                    "every update answered succ")
    body = ask(f"GET race{run_id}.bin.track")
    if body.startswith("<REP GET BEGIN>"):
        seen = body.count("10.0.2.")
        all_ok &= check(seen == N, f"tracker file lists all {N} new peers (found {seen}): "
                                   "no update was lost")
    else:
        print("    [skip] GET is not available, cannot read the file back")

    slow.close()
    print("\nResult:", "ALL CHECKS PASSED" if all_ok else "SOME CHECKS FAILED")
    print("Look at the tracker console: the events above were handled by "
          "different worker-N threads.\n")
    return 0 if all_ok else 1


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except OSError as exc:
        sys.exit(f"cannot reach the tracker: {exc}")
