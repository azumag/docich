"""Private browser transport checkpoint; never diagnostics or source control."""
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile

from .twica_overlay import private_directory

LIMIT = 256 * 1024


def read_checkpoint(directory: Path, url: str) -> dict:
    path = directory / 'browser-state.json'
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or info.st_nlink != 1 or info.st_size > LIMIT or info.st_mode & 0o077):
                return {}
            value = json.loads(os.read(fd, LIMIT + 1))
        finally:
            os.close(fd)
        if not isinstance(value, dict) or value.get('target') != hashlib.sha256(url.encode()).hexdigest():
            return {}
        if not isinstance(value.get('storage'), dict) or not isinstance(value.get('session'), dict):
            return {}
        if any(not isinstance(k, str) or not isinstance(v, str) for k, v in value['session'].items()):
            return {}
        return value
    except (OSError, ValueError, TypeError, UnicodeError):
        return {}


async def save_checkpoint(directory: Path, url: str, page) -> None:
    private_directory(directory)
    # Read only the dedicated overlay's browser context; never host credentials.
    storage = await page.context.storage_state()
    session = await page.evaluate('() => Object.fromEntries(Object.entries(sessionStorage))')
    value = {'target': hashlib.sha256(url.encode()).hexdigest(), 'storage': storage, 'session': session}
    data = json.dumps(value).encode()
    if len(data) > LIMIT:
        raise ValueError('checkpoint too large')
    fd, temporary = tempfile.mkstemp(prefix='.browser-', dir=directory)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
        os.replace(temporary, directory / 'browser-state.json')
    finally:
        Path(temporary).unlink(missing_ok=True)
