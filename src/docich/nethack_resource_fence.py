"""One permanent, NetHack-only spawn fence; never signals or stops resources.

Producers hold a shared lock through spawning and child cleanup. The separate
administrative transaction holds it exclusively through census and commit.
An older, non-participating producer must still fail the closed UID census.
"""
from contextlib import contextmanager
import fcntl
from functools import wraps
import os
from pathlib import Path
import stat

FENCE_FILE = 'locks/nethack-resource-fence.lock'


class FenceUnproven(RuntimeError):
    pass


@contextmanager
def resource_fence(state_dir, *, exclusive=False):
    configured = Path(state_dir).absolute()
    if configured.is_symlink():
        raise FenceUnproven('NetHack resource fence unavailable')
    # Normalize the configured directory once, including platform/temp-root
    # aliases, so every producer locks the same canonical dev/inode. State and
    # lock inputs never come from operator requests or container arena data.
    root = configured.resolve()
    directory = root / 'locks'
    if any(p.is_symlink() for p in (directory, *directory.parents)):
        raise FenceUnproven('NetHack resource fence unavailable')
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    parent = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    descriptor = None
    try:
        if os.fstat(parent).st_uid != os.getuid():
            raise FenceUnproven('NetHack resource fence unavailable')
        descriptor = os.open('nethack-resource-fence.lock',
                             os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC,
                             0o600, dir_fd=parent)
        identity = os.fstat(descriptor)
        if (not stat.S_ISREG(identity.st_mode) or identity.st_uid != os.getuid()
                or identity.st_nlink != 1 or identity.st_mode & 0o022):
            raise FenceUnproven('NetHack resource fence unavailable')
        try:
            fcntl.flock(descriptor, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise FenceUnproven('NetHack resource fence busy') from exc
        current = os.stat('nethack-resource-fence.lock', dir_fd=parent, follow_symlinks=False)
        if (current.st_dev, current.st_ino) != (identity.st_dev, identity.st_ino):
            raise FenceUnproven('NetHack resource fence unavailable')
        yield
    except OSError as exc:
        raise FenceUnproven('NetHack resource fence unavailable') from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(parent)


def fenced_configuration(function):
    @wraps(function)
    def wrapped(g, *args, **kwargs):
        with resource_fence(g.state_dir):
            return function(g, *args, **kwargs)
    return wrapped
