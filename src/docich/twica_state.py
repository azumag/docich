"""Private, bounded cross-process ownership contract for the common foreground.

No URL, event contents, environment dump, or credentials belong in these files.
A stale/malformed record never grants a second display owner.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import socket
import stat
import tempfile
import time
from urllib.request import Request, urlopen
from uuid import uuid4

from .twica_overlay import private_directory

PROTOCOL = 1
ZERO_GENERATION = '0' * 32
MODES = {'legacy', 'draining', 'common'}
HEALTH_TTL_NS = 5_000_000_000
MAX_RECORD = 8192


def process_birth(pid: int) -> str:
    try:
        text = Path(f'/proc/{pid}/stat').read_text()
        return text[text.rfind(')') + 2:].split()[19]
    except (OSError, IndexError, ValueError):
        return ''


def boot_id() -> str:
    try:
        return Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    except OSError:
        return ''


def identity() -> dict:
    return {'pid': os.getpid(), 'birth': process_birth(os.getpid()), 'boot': boot_id()}


def alive(record: dict) -> bool:
    pid = record.get('pid')
    return (type(pid) is int and pid > 0 and record.get('boot') == boot_id()
            and bool(record.get('birth')) and record['birth'] == process_birth(pid))


def fresh(record: dict, *, now_ns=None) -> bool:
    now = time.monotonic_ns() if now_ns is None else now_ns
    stamp = record.get('updated_ns')
    return (alive(record) and type(stamp) is int
            and 0 <= now - stamp <= HEALTH_TTL_NS)


def read_json(path: Path, *, missing=None) -> dict | None:
    """Reject symlinks, FIFOs, oversized JSON and records owned by another user."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return missing
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) & 0o077
                or not 1 <= info.st_size <= MAX_RECORD):
            raise ValueError('invalid private state')
        with os.fdopen(os.dup(fd), 'rb') as stream:
            data = stream.read(MAX_RECORD + 1)
        value = json.loads(data)
        if not isinstance(value, dict):
            raise ValueError('invalid state type')
        return value
    finally:
        os.close(fd)


def write_json(path: Path, value: dict) -> None:
    parent = private_directory(path.parent)
    data = json.dumps(value, sort_keys=True, separators=(',', ':')).encode()
    if len(data) > MAX_RECORD:
        raise ValueError('state exceeds limit')
    fd, temporary = tempfile.mkstemp(prefix='.state-', dir=parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def heartbeat(directory: Path, name: str, **fields) -> dict:
    if name not in {'renderer', 'compositor'}:
        raise ValueError('unknown component')
    value = {'protocol': PROTOCOL, **identity(), 'updated_ns': time.monotonic_ns(), **fields}
    write_json(directory / f'{name}.json', value)
    return value


def owner(directory: Path) -> dict:
    try:
        value = read_json(directory / 'owner.json', missing={
            'protocol': PROTOCOL, 'mode': 'legacy', 'generation': ZERO_GENERATION,
        })
        valid_generation = re.fullmatch(r'[a-f0-9]{32}', str(value.get('generation', '')))
        if value.get('protocol') != PROTOCOL or value.get('mode') not in MODES or not valid_generation:
            raise ValueError('invalid owner')
        return value
    except (OSError, ValueError, TypeError):
        return {'protocol': PROTOCOL, 'mode': 'blocked', 'generation': ZERO_GENERATION}


def set_owner(directory: Path, mode: str) -> dict:
    if mode not in MODES:
        raise ValueError('invalid owner mode')
    value = {'protocol': PROTOCOL, 'mode': mode, 'generation': uuid4().hex}
    write_json(directory / 'owner.json', value)
    return value


@contextmanager
def lease(directory: Path, name: str):
    if name not in {'control', 'stream', 'renderer-service'}:
        raise ValueError('invalid lease name')
    private_directory(directory)
    fd = os.open(directory / f'{name}.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077):
            raise ValueError('invalid lease')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def consumers(directory: Path) -> list[dict]:
    root = directory / 'consumers'
    if not root.exists():
        return []
    private_directory(root)
    paths = list(root.iterdir())
    if len(paths) > 100:
        raise ValueError('consumer inventory too large')
    records = []
    for path in paths:
        if path.name.startswith('.state-'):
            continue
        if not re.fullmatch(r'[a-f0-9]{32}\.json', path.name):
            raise ValueError('unknown consumer record')
        record = read_json(path)
        if not isinstance(record, dict) or record.get('protocol') != PROTOCOL:
            raise ValueError('unknown consumer protocol')
        if alive(record):
            records.append(record)
    return records


def retired(directory: Path, generation: str) -> bool:
    try:
        return all(fresh(r) and r.get('generation') == generation
                   and r.get('state') == 'retired' and r.get('frames') == 0
                   for r in consumers(directory))
    except (OSError, ValueError, TypeError):
        return False


def component(directory: Path, name: str) -> dict:
    try:
        return read_json(directory / f'{name}.json', missing={}) or {}
    except (OSError, ValueError, TypeError):
        return {}


def proxy_inventory(ports: tuple[int, ...]) -> bool:
    """An old/unreachable-but-listening proxy blocks cutover, not an empty guess.

    Both legacy proxy instances expose only a fixed version/guard-count route.
    A genuinely closed port is safe; timeout, wrong response and HTTP errors are not.
    """
    for port in set(ports):
        if type(port) is not int or not 1 <= port <= 65535:
            return False
        try:
            connection = socket.create_connection(('127.0.0.1', port), timeout=1)
        except ConnectionRefusedError:
            continue
        except OSError:
            return False
        connection.close()
        try:
            request = Request(f'http://127.0.0.1:{port}/__docich_twica_guard_v1',
                              headers={'Accept': 'application/json'})
            with urlopen(request, timeout=1) as response:
                data = response.read(2049)
                if response.status != 200 or len(data) > 2048:
                    return False
            value = json.loads(data)
            if not isinstance(value, dict) or value.get('protocol') != PROTOCOL or value.get('guard_ready') is not True:
                return False
        except (OSError, ValueError):
            return False
    return True


class TransitionError(RuntimeError):
    """Contains only a fixed public reason code."""


class OwnerControl:
    def __init__(self, directory: Path, *, proxy_ports=(18080, 18081),
                 inventory=proxy_inventory, timeout=12.0):
        self.directory = private_directory(directory)
        self.proxy_ports = tuple(proxy_ports)
        self.inventory = inventory
        self.timeout = timeout

    def _wait(self, predicate) -> bool:
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(.1)
        return False

    def _renderer_standby(self) -> bool:
        r = component(self.directory, 'renderer')
        return fresh(r) and r.get('state') == 'standby' and r.get('browser_active') is False

    def activate(self) -> dict:
        with lease(self.directory, 'control'):
            previous = owner(self.directory)
            if previous['mode'] == 'common':
                return {'mode': 'common', 'changed': False}
            if previous['mode'] not in {'legacy', 'draining'}:
                raise TransitionError('owner_state_invalid')
            compositor = component(self.directory, 'compositor')
            if not (fresh(compositor) and compositor.get('state') == 'running'
                    and type(compositor.get('frames_sent')) is int
                    and compositor['frames_sent'] >= 2):
                raise TransitionError('compositor_not_ready')
            if not self._renderer_standby():
                raise TransitionError('renderer_not_ready')
            if not self.inventory(self.proxy_ports):
                raise TransitionError('legacy_clients_not_upgraded')
            draining = set_owner(self.directory, 'draining')
            if not self._wait(lambda: retired(self.directory, draining['generation'])):
                # No upstream common page has opened. Restore only the old owner;
                # do not start the common renderer while a legacy ACK is unknown.
                if self._renderer_standby():
                    set_owner(self.directory, 'legacy')
                raise TransitionError('legacy_retirement_timeout')
            compositor = component(self.directory, 'compositor')
            if not (fresh(compositor) and compositor.get('state') == 'running'):
                set_owner(self.directory, 'legacy')
                raise TransitionError('compositor_lost_during_cutover')
            current = set_owner(self.directory, 'common')
            return {'mode': current['mode'], 'changed': True}

    def rollback(self) -> dict:
        with lease(self.directory, 'control'):
            previous = owner(self.directory)
            if previous['mode'] == 'legacy':
                return {'mode': 'legacy', 'changed': False}
            if previous['mode'] == 'blocked':
                raise TransitionError('owner_state_invalid')
            draining = set_owner(self.directory, 'draining')
            def stopped():
                r = component(self.directory, 'renderer')
                return (fresh(r) and r.get('state') == 'standby'
                        and r.get('browser_active') is False
                        and r.get('generation') == draining['generation']
                        and retired(self.directory, draining['generation']))
            if not self._wait(stopped):
                # Never revive legacy while an unconfirmed browser might play audio.
                raise TransitionError('renderer_retirement_timeout')
            set_owner(self.directory, 'legacy')
            return {'mode': 'legacy', 'changed': True}


def diagnostics(directory: Path) -> dict:
    """Fixed output only, suitable for inclusion in the existing diagnostics path."""
    current = owner(directory)
    r, c = component(directory, 'renderer'), component(directory, 'compositor')
    try:
        clients = consumers(directory)
        count = len(clients)
        unready = sum(not fresh(x) for x in clients)
    except (OSError, ValueError, TypeError):
        count, unready = 0, 1
    return {'protocol': PROTOCOL, 'owner': current['mode'],
            'renderer_fresh': fresh(r), 'renderer_active': r.get('browser_active') is True,
            'compositor_fresh': fresh(c), 'compositor_running': c.get('state') == 'running',
            'legacy_consumers': count, 'unready_consumers': unready,
            'frame_state': c.get('frame_state') if c.get('frame_state') in
                {'fresh', 'stale', 'missing', 'unavailable', 'invalid', 'inactive', 'closed'} else 'unknown'}
