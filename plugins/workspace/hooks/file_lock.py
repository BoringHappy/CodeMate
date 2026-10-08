"""Acquire a lock on a shell-owned FD, including on hosts without util-linux."""

import argparse
import fcntl
import sys
import time

parser = argparse.ArgumentParser()
parser.add_argument("-n", action="store_true")
parser.add_argument("-w", type=float, default=0)
parser.add_argument("fd", type=int)
args = parser.parse_args()
deadline = time.monotonic() + args.w
while True:
    try:
        # The shell retains this open file description after this process exits.
        fcntl.flock(args.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        sys.exit(0)
    except BlockingIOError:
        if args.n or time.monotonic() >= deadline:
            sys.exit(1)
        time.sleep(0.05)
