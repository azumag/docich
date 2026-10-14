#!/usr/bin/env python3
"""Bounded library reload of an existing poll worker; never start or restart it."""

import argparse
import json
import os
from pathlib import Path
import re
import signal
import stat
import time


PRODUCTION_ROOT = Path('/home/ubuntu/soren')
PROC_ROOT = Path('/proc')
WAIT_SECONDS = 60
POLL_SECONDS = 0.25
MAX_LOG_BYTES = 1024 * 1024
RELOAD_MARKER = re.compile(rb'^\[poll_worker [0-9]{2}:[0-9]{2}:[0-9]{2}\] reload complete \(', re.M)


class Refused(Exception):
    """Reasons are fixed codes, never runtime text or file contents."""


def read_regular(path, limit):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise Refused('not_regular')
        data = stream.read(limit + 1)
        if len(data) > limit:
            raise Refused('oversized_state')
        return data


def read_pid(root):
    try:
        raw = read_regular(root / 'tmp/state/poll_worker.pid', 32).strip()
    except FileNotFoundError:
        return None
    if not re.fullmatch(rb'[1-9][0-9]{0,9}', raw) or int(raw) <= 1:
        raise Refused('invalid_pid')
    return int(raw)


def paused(root):
    # A dangling marker is also a stop request; never remove any marker.
    return os.path.lexists(root / 'tmp/state/poll_worker.paused') or os.path.lexists(root / 'tmp/stop')


def process_identity(root, pid):
    proc = PROC_ROOT / str(pid)
    if proc.stat().st_uid != os.geteuid():
        raise Refused('wrong_owner')
    argv = (proc / 'cmdline').read_bytes().split(b'\0')
    if argv and argv[-1] == b'':
        argv.pop()
    allowed_scripts = {os.fsencode(root / 'workers/poll_worker.sh'), b'./workers/poll_worker.sh', b'workers/poll_worker.sh'}
    # Accept only bash executing the exact script, not bash -c, substrings,
    # similarly named scripts, or a script mentioned in another command's args.
    if (len(argv) != 2 or argv[0] not in (b'bash', b'/bin/bash', b'/usr/bin/bash')
            or argv[1] not in allowed_scripts
            or (proc / 'cwd').resolve(strict=True) != root
            or os.readlink(proc / 'exe') not in ('/bin/bash', '/usr/bin/bash')):
        raise Refused('wrong_command')
    fields = (proc / 'stat').read_text().rsplit(')', 1)[1].split()
    if fields[0] in ('Z', 'X', 'x') or not fields[19].isdigit():
        raise Refused('not_alive')
    caught = re.search(r'^SigCgt:\s*([0-9a-fA-F]+)$', (proc / 'status').read_text(), re.M)
    if caught is None or not int(caught[1], 16) & (1 << (signal.SIGUSR1 - 1)):
        raise Refused('reload_trap_missing')
    return fields[19]  # Linux /proc stat field 22: process start ticks.


class ReloadLog:
    def __init__(self, root):
        self.path = root / 'logs/poll_worker.log'
        fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        self.stream = os.fdopen(fd, 'rb')
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            self.stream.close()
            raise Refused('log_not_regular')
        self.identity = (info.st_dev, info.st_ino)
        self.offset = info.st_size
        self.stream.seek(self.offset)
        self.data = b''

    def confirmed(self):
        info = self.path.lstat()
        if (info.st_dev, info.st_ino) != self.identity or info.st_size < self.offset:
            raise Refused('log_rotated')
        chunk = self.stream.read(MAX_LOG_BYTES - len(self.data) + 1)
        self.data += chunk
        self.offset += len(chunk)
        if len(self.data) > MAX_LOG_BYTES:
            raise Refused('log_limit')
        return RELOAD_MARKER.search(self.data) is not None

    def close(self):
        self.stream.close()


def reload_worker(root):
    root = root.resolve(strict=True)
    if paused(root):
        return 'skipped', 'paused'
    pid = read_pid(root)
    if pid is None:
        return 'skipped', 'absent'
    try:
        start = process_identity(root, pid)
    except FileNotFoundError:
        # Stale PID file, but never delete it or ask the supervisor to start.
        if not (PROC_ROOT / str(pid)).exists():
            return 'skipped', 'absent'
        raise Refused('identity_unavailable')
    if not hasattr(os, 'pidfd_open') or not hasattr(signal, 'pidfd_send_signal'):
        raise Refused('pidfd_unavailable')
    # Pin this process before the final identity checks. pidfd_send_signal
    # cannot accidentally signal a replacement which reuses the numeric PID.
    fd = os.pidfd_open(pid, 0)
    log = None
    try:
        log = ReloadLog(root)
        if paused(root):
            return 'skipped', 'paused'
        if read_pid(root) != pid or process_identity(root, pid) != start:
            raise Refused('identity_changed')
        signal.pidfd_send_signal(fd, signal.SIGUSR1, None, 0)
        deadline = time.monotonic() + WAIT_SECONDS
        while True:
            signal.pidfd_send_signal(fd, 0, None, 0)
            if read_pid(root) != pid or process_identity(root, pid) != start:
                raise Refused('identity_changed')
            if log.confirmed():
                # Confirm identity once more after observing the new marker.
                signal.pidfd_send_signal(fd, 0, None, 0)
                if read_pid(root) != pid or process_identity(root, pid) != start:
                    raise Refused('identity_changed')
                return 'reloaded', 'worker_log_marker'
            if time.monotonic() >= deadline:
                raise Refused('reload_unconfirmed')
            time.sleep(POLL_SECONDS)
    finally:
        if log is not None:
            log.close()
        os.close(fd)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    # Repository tests only: never populated from workflow input or environment.
    parser.add_argument('--root', type=Path, default=PRODUCTION_ROOT)
    args = parser.parse_args(argv)
    try:
        status, reason = reload_worker(args.root)
    except Refused as exc:
        status, reason = 'failed', exc.args[0]
    except (OSError, ValueError, IndexError):
        status, reason = 'failed', 'runtime_unavailable'
    # Gateway retains this static receipt in its private log. No log/command/
    # environment/prompt/generated content is ever echoed or persisted here.
    print(json.dumps({'worker': 'poll_worker', 'status': status, 'reason': reason}, sort_keys=True))
    return 1 if status == 'failed' else 0


if __name__ == '__main__':
    raise SystemExit(main())
