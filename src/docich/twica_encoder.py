"""Own/reap native FFmpeg even when the adapter receives uncatchable SIGKILL.

Only the adapter holds the lifetime pipe's write end. Its EOF is a final stop,
after the runner's existing q/SIGINT grace periods; no timeout is shortened.
The guardian inherits stdio and forwards signals, but never reads FFmpeg stdin.
"""
from __future__ import annotations

import os
import select
import signal
import subprocess
import sys


def supervise(lifetime_fd: int, pid_fd: int, overlay_fd: int, command: list[str]) -> int:
    child = None
    pending = []

    def forward(signum, _frame):
        if child is None:
            pending.append(signum)
        elif child.poll() is None:
            try:
                child.send_signal(signum)
            except ProcessLookupError:
                pass

    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, forward)
    try:
        child = subprocess.Popen(command, pass_fds=(overlay_fd,))
        os.close(overlay_fd)
        os.write(pid_fd, str(child.pid).encode('ascii'))
        os.close(pid_fd)
        for sig in pending:
            forward(sig, None)
        while child.poll() is None:
            if select.select([lifetime_fd], [], [], 0.1)[0]:
                # The adapter alone owns the writer. Neither this process nor
                # native FFmpeg inherits it, including during native startup.
                if not os.read(lifetime_fd, 1):
                    child.kill()
                    break
        return_code = child.wait()
        return return_code if return_code >= 0 else 128 - return_code
    finally:
        if child is not None:
            if child.poll() is None:
                child.kill()
            child.wait()


def main() -> int:
    try:
        lifetime_fd, pid_fd, overlay_fd = map(int, sys.argv[1:4])
        return supervise(lifetime_fd, pid_fd, overlay_fd, sys.argv[4:])
    except Exception:
        # Do not publish FFmpeg arguments or raw exceptions.
        print('twica common: encoder supervision failed', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
