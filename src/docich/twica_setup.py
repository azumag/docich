"""Prepare only the opt-in environment flag; never restart a live service.

Called by the fixed owner operation after renderer dependencies are installed.
Existing secret-bearing environment content is preserved and never printed.
"""
from __future__ import annotations
import argparse
import hashlib
import os
from pathlib import Path
import re
import stat
import tempfile
from .twica_state import lease, owner
from .twica_overlay import private_directory


def prepare_environment(soren_root: Path) -> None:
    envfile = soren_root / '.env'
    directory = private_directory(soren_root / 'tmp/state/twica-common')
    with lease(directory, 'control'):
        if owner(directory)['mode'] != 'legacy':
            raise ValueError('ownership must be legacy during preparation')
        fd = os.open(envfile, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                    or info.st_nlink != 1 or info.st_size > 1024 * 1024):
                raise ValueError('invalid environment file')
            with os.fdopen(os.dup(fd), 'rb') as stream:
                original = stream.read(1024 * 1024 + 1)
        finally:
            os.close(fd)
        text = original.decode('utf-8')
        lines = [line for line in text.splitlines(keepends=True)
                 if not re.match(r'^\s*(?:export\s+)?DOCICH_TWICA_COMMON_ENABLED\s*=', line)]
        updated = ''.join(lines).rstrip('\n') + '\nDOCICH_TWICA_COMMON_ENABLED=1\n'
        digest = hashlib.sha256(original).hexdigest()
        backup = directory / f'environment-before-{digest}.bak'
        try:
            fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(original)
                stream.flush()
                os.fsync(stream.fileno())
        fd, temporary = tempfile.mkstemp(prefix='.twica-env-', dir=soren_root)
        try:
            with os.fdopen(fd, 'w') as stream:
                stream.write(updated)
                stream.flush()
                os.fsync(stream.fileno())
            current = envfile.lstat()
            if (current.st_ino, current.st_mtime_ns, current.st_size) != (
                    info.st_ino, info.st_mtime_ns, info.st_size):
                raise ValueError('environment changed concurrently')
            os.replace(temporary, envfile)
        finally:
            Path(temporary).unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--soren-root', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        prepare_environment(args.soren_root)
    except Exception:
        print('common overlay preparation failed')
        return 2
    print('common overlay prepared; existing processes were not restarted')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
