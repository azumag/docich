"""Single writer lease: flock for same-host exclusion + a DB epoch for fencing."""
from __future__ import annotations

import fcntl
import os
import uuid
from dataclasses import dataclass
from pathlib import Path


class LeaseLost(RuntimeError):
    pass


class LeaseHeld(RuntimeError):
    pass


@dataclass(frozen=True)
class WriterLease:
    holder_id: str
    epoch: int
    lock_path: Path
    fd: int
    inode: int


def acquire_writer_lease(ledger, layout, *, now: float) -> WriterLease:
    fd = os.open(layout.lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise LeaseHeld("another process holds the LIVE writer lease") from exc
        holder = uuid.uuid4().hex
        epoch = ledger.claim_lease_row(holder_id=holder, pid=os.getpid(), now=now)
        return WriterLease(holder, epoch, Path(layout.lock_path), fd, os.fstat(fd).st_ino)
    except BaseException:
        os.close(fd)
        raise


def verify_in_tx(cur, lease: WriterLease) -> None:
    """Called inside a write transaction: lock file and DB row must both still match."""
    try:
        same_file = os.stat(lease.lock_path).st_ino == lease.inode == os.fstat(lease.fd).st_ino
    except OSError:
        same_file = False
    row = cur.execute("SELECT holder_id, epoch, released_at FROM writer_lease WHERE singleton=1").fetchone()
    if not same_file or row is None or row[0] != lease.holder_id or row[1] != lease.epoch or row[2] is not None:
        raise LeaseLost("writer lease lost")


def release_writer_lease(ledger, lease: WriterLease, *, now: float) -> None:
    try:
        ledger.release_lease_row(lease, now=now)
    finally:
        try:
            os.close(lease.fd)
        except OSError:
            pass
