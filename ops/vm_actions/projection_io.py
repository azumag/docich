"""Descriptor-bound, no-clobber projection updates.

POSIX rename is not compare-and-swap. Move the live inode into private escrow,
validate THAT inode, then publish with linkat (EEXIST never overwrites). Keep
escrow on both success and failure: an uncooperative writer may still hold an
open fd to the old inode. Recovery must never erase that writer's bytes.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import uuid
from pathlib import Path, PurePosixPath

DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
READ = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
CAPABILITY = "projection_no_clobber_v1"


def identity(st):
    return st.st_dev, st.st_ino


def revision(st):
    return identity(st), st.st_mode, st.st_size, st.st_mtime_ns, st.st_ctime_ns


def metadata(directory_fd, name):
    try:
        fd = os.open(name, READ, dir_fd=directory_fd)
    except FileNotFoundError:
        # A dangling symlink must not masquerade as an absent regular file.
        try:
            os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            return None
        raise ValueError("concurrent projection drift")
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("projection path is not a regular file")
        digest = hashlib.sha256()
        while chunk := os.read(fd, 65536):
            digest.update(chunk)
        after = os.fstat(fd)
        named = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if revision(before) != revision(after) or revision(after) != revision(named):
            raise ValueError("concurrent projection drift")
        return {"sha256": digest.hexdigest(), "mode": stat.S_IMODE(after.st_mode)}
    finally:
        os.close(fd)


def publish(source_fd, source_name, destination_fd, destination_name):
    # Atomic no-clobber publication, also used for recovery. Never os.replace.
    os.link(source_name, destination_name, src_dir_fd=source_fd,
            dst_dir_fd=destination_fd, follow_symlinks=False)
    os.fsync(destination_fd)


class ProjectionPath:
    def __init__(self, root, relative):
        self.fds = []
        rel = PurePosixPath(relative)
        if rel.is_absolute() or not rel.parts or any(p in {".", ".."} for p in rel.parts):
            raise ValueError("unsafe projection path")
        self.root = Path(root)
        self.parts = rel.parts
        self.escrows = []
        self.mutated = False
        try:
            self.fds.append(os.open(self.root, DIRECTORY))
            self._walk(create=False)
        except BaseException:
            self.close()
            raise

    def close(self):
        for fd in reversed(self.fds):
            os.close(fd)
        self.fds.clear()

    def __del__(self):
        self.close()

    def attached(self):
        if identity(os.stat(self.root, follow_symlinks=False)) != identity(os.fstat(self.fds[0])):
            raise ValueError("concurrent projection drift")
        for index in range(1, len(self.fds)):
            named = os.stat(self.parts[index - 1], dir_fd=self.fds[index - 1], follow_symlinks=False)
            if identity(named) != identity(os.fstat(self.fds[index])):
                raise ValueError("concurrent projection drift")

    def _walk(self, create):
        self.attached()
        while len(self.fds) < len(self.parts):
            name = self.parts[len(self.fds) - 1]
            try:
                fd = os.open(name, DIRECTORY, dir_fd=self.fds[-1])
            except FileNotFoundError:
                if not create:
                    return False
                # mkdirat doesn't replace a concurrently-created entry. The
                # subsequent O_NOFOLLOW open rejects symlinks, including races.
                try:
                    os.mkdir(name, mode=0o755, dir_fd=self.fds[-1])
                except FileExistsError:
                    pass
                fd = os.open(name, DIRECTORY, dir_fd=self.fds[-1])
            self.fds.append(fd)
        self.attached()
        return True

    def current(self):
        if not self._walk(create=False):
            return None
        return metadata(self.fds[-1], self.parts[-1])

    def verify_escrows(self):
        self.attached()
        for record in self.escrows:
            parent_fd, name, expected = record
            escrow_fd = os.open(name, DIRECTORY, dir_fd=parent_fd)
            try:
                if metadata(escrow_fd, "before") != expected:
                    raise ValueError("concurrent projection drift")
            finally:
                os.close(escrow_fd)

    def replace(self, expected, data, mode):
        if self.current() != expected:
            raise ValueError("concurrent projection drift")
        desired = None if data is None else {"sha256": hashlib.sha256(data).hexdigest(), "mode": mode}
        if expected == desired:
            return
        self._walk(create=True)
        parent_fd, leaf = self.fds[-1], self.parts[-1]
        escrow = ".vmops-projection-" + uuid.uuid4().hex
        os.mkdir(escrow, mode=0o700, dir_fd=parent_fd)
        escrow_fd = os.open(escrow, DIRECTORY, dir_fd=parent_fd)
        captured = False
        try:
            # Fixed recovery metadata stays inside the private escrow. No
            # contents or paths are returned to Actions.
            self._write(escrow_fd, "record.json", json.dumps({
                "target": leaf, "before": expected, "after": desired,
            }, sort_keys=True).encode(), 0o600)
            if data is not None:
                self._write(escrow_fd, "after", data, mode)
            self.attached()
            if expected is not None:
                # Empty private escrow => no destination can be clobbered.
                # A race replacing/editing/chmod'ing leaf is captured intact.
                os.rename(leaf, "before", src_dir_fd=parent_fd, dst_dir_fd=escrow_fd)
                captured = True
                self.mutated = True
                os.fsync(parent_fd)
                os.fsync(escrow_fd)
                if metadata(escrow_fd, "before") != expected:
                    raise ValueError("concurrent projection drift")
                self.escrows.append((parent_fd, escrow, expected))
            self.attached()
            if data is not None:
                publish(escrow_fd, "after", parent_fd, leaf)
                self.mutated = True
            elif self.current() is not None:
                raise ValueError("concurrent projection drift")
            self.verify_escrows()
            if self.current() != desired:
                raise ValueError("concurrent projection drift")
        except BaseException:
            if captured:
                # Restore the captured inode only into an absent name; if a
                # competing writer created leaf, retain BOTH inodes for review.
                try:
                    self.attached()
                    publish(escrow_fd, "before", parent_fd, leaf)
                except (OSError, ValueError):
                    pass
            raise
        finally:
            os.close(escrow_fd)
            # Deliberately never unlink the captured inode or temp hardlink:
            # retained open-fd writes must remain recoverable after failure.

    @staticmethod
    def _write(directory_fd, name, data, mode):
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                     0o600, dir_fd=directory_fd)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fchmod(stream.fileno(), mode)
            os.fsync(stream.fileno())
        os.fsync(directory_fd)
