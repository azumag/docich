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
