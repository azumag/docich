"""Common nonblocking FFmpeg runner lock primitive.

The file path and lifetime remain caller-owned during the staged migration.
This module never spawns a process, opens an output, changes stream settings,
unlocks an FD, or manipulates any live service. The caller retains an already-
opened lock file descriptor until the encoder/supervisor has completely exited.
"""
from __future__ import annotations

import fcntl

SINGLE_ENCODER_LOCK_CONTRACT_VERSION = 1


def acquire_nonblocking_encoder_lock(fd: int) -> None:
    """Acquire an exclusive process-held flock or raise BlockingIOError.

    This intentionally does not close the descriptor or release the lock.
    A second FFmpeg supervisor using the same file must fail immediately.
    """
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


# Fixed host-wide path. It must never be derived from a game repository,
# caller-controlled state_dir, DOCICH_ROOT or a service-specific environment.
# The companion legacy lock remains mandatory until the VM handover completes.
CANONICAL_STREAM_ENCODER_LOCK_CONTRACT_VERSION = 1
CANONICAL_STREAM_ENCODER_LOCK_PATH = "/home/ubuntu/docich/run-soren-live/locks/direct_stream_encoder.lock"


def open_canonical_stream_encoder_lock():
    """Open, validate and lock the single host's canonical publisher lease.

    The parent must be provisioned by the operator as a private real directory.
    Never create a directory or follow a symlink in a live stream start path.
    The returned file remains locked until the owner closes it. No service,
    encoder or relay operation occurs here.
    """
    import os
    from pathlib import Path
    import stat

    path = Path(CANONICAL_STREAM_ENCODER_LOCK_PATH)
    parent_stat = path.parent.lstat()
    if (
        not stat.S_ISDIR(parent_stat.st_mode)
        or parent_stat.st_uid != os.geteuid()
        or stat.S_IMODE(parent_stat.st_mode) & 0o077
    ):
        raise PermissionError("canonical stream lock directory must be private and owned")
    flags = os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    try:
        meta = os.fstat(fd)
        if (
            not stat.S_ISREG(meta.st_mode)
            or meta.st_uid != os.geteuid()
            or stat.S_IMODE(meta.st_mode) & 0o077
            or meta.st_nlink != 1
        ):
            raise PermissionError("canonical stream lock file must be private and owned")
        # Opened without following links, and held on the same inode on disk.
        current = path.lstat()
        if (current.st_dev, current.st_ino) != (meta.st_dev, meta.st_ino):
            raise PermissionError("canonical stream lock path changed")
        acquire_nonblocking_encoder_lock(fd)
        return os.fdopen(fd, "a+")
    except BaseException:
        os.close(fd)
        raise
