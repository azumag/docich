"""Private single-owner control and bounded, non-secret runtime evidence.

The owner is durable policy, never inferred from renderer liveness. A crashed
common renderer therefore cannot cause the legacy clients to resubscribe.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import stat
import tempfile
import time
import uuid

from .twica_overlay import private_directory

SCHEMA = 1
MAX_JSON = 4096
OWNERS = {'legacy', 'none', 'common'}
ROOT = Path(__file__).resolve().parents[2]


def state_directory() -> Path:
    return Path(os.environ.get('DOCICH_TWICA_STATE_DIR', str(ROOT / 'run/twica-common')))


def frame_directory() -> Path:
    # Frame traffic is tens of MB/s; keep it in RAM, not on the VM system disk.
    return Path(os.environ.get('DOCICH_TWICA_FRAME_DIR', f'/dev/shm/docich-twica-{os.geteuid()}'))


def read_json(path: Path) -> dict:
    """No links, special files, foreign owners or unbounded reads."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                    or info.st_nlink != 1 or info.st_size > MAX_JSON
                    or stat.S_IMODE(info.st_mode) & 0o077):
                return {}
            data = os.read(fd, MAX_JSON + 1)
            value = json.loads(data)
            return value if isinstance(value, dict) else {}
        finally:
            os.close(fd)
    except (OSError, ValueError, UnicodeError):
        return {}


def atomic_json(directory: Path, name: str, value: dict) -> None:
    if Path(name).name != name:
        raise ValueError('invalid state filename')
    directory = private_directory(directory)
    payload = json.dumps(value, separators=(',', ':'), sort_keys=True).encode()
    if len(payload) > MAX_JSON:
        raise ValueError('state is too large')
    fd, temporary = tempfile.mkstemp(prefix='.twica-', dir=directory)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, directory / name)
        dfd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    finally:
        Path(temporary).unlink(missing_ok=True)


def control(directory: Path) -> dict:
    raw = read_json(directory / 'control.json')
    valid = (raw.get('schema') == SCHEMA and isinstance(raw.get('owner'), str)
             and raw['owner'] in OWNERS
             and isinstance(raw.get('generation'), str)
             and len(raw['generation']) == 32
             and all(c in '0123456789abcdef' for c in raw['generation']))
    if valid:
        return raw
    # Absent/malformed managed policy is not permission for fallback.
    return {'schema': SCHEMA, 'owner': 'none', 'generation': '', 'invalid': True}


def new_control(owner: str, **fields) -> dict:
    if owner not in OWNERS:
        raise ValueError('invalid owner')
    return {'schema': SCHEMA, 'owner': owner, 'generation': uuid.uuid4().hex, **fields}


def process_identity(pid: int | None = None) -> str:
    """Boot ID plus process start ticks fences PID reuse across crashes/reboots."""
    pid = os.getpid() if pid is None else pid
    try:
        boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        data = Path(f'/proc/{pid}/stat').read_text()
        ticks = data[data.rindex(')') + 2:].split()[19]
        return f'{boot}:{ticks}'
    except (OSError, ValueError, IndexError):
        return ''


def heartbeat(directory: Path, name: str, **fields) -> None:
    atomic_json(directory, name, {'schema': SCHEMA, 'pid': os.getpid(),
                'identity': process_identity(), 'monotonic_ns': time.monotonic_ns(), **fields})


def fresh(value: dict, *, age_sec: float = 3) -> bool:
    try:
        age = time.monotonic_ns() - value['monotonic_ns']
        pid = value['pid']
        return (type(pid) is int and pid > 0 and type(value['monotonic_ns']) is int
                and value.get('schema') == SCHEMA and 0 <= age < age_sec * 1e9
                and bool(value.get('identity'))
                and process_identity(pid) == value['identity'])
    except (KeyError, TypeError):
        return False


@contextmanager
def exclusive(directory: Path, name: str):
    private_directory(directory)
    if Path(name).name != name:
        raise ValueError('invalid lock name')
    fd = os.open(directory / name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077):
            raise ValueError('invalid lock')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def legacy_clients(directory: Path) -> list[dict]:
    paths = sorted(directory.glob('legacy-*.json'))
    if len(paths) > 64:
        raise RuntimeError('too many legacy clients')
    result = []
    for path in paths:
        data = read_json(path)
        # A live but stale client is a blocker, not an absent subscriber.
        if (type(data.get('pid')) is int and data['pid'] > 0
                and data.get('identity') and process_identity(data['pid']) == data['identity']):
            result.append(data)
    return result


def all_legacy_quiescent(directory: Path, generation: str) -> bool:
    clients = legacy_clients(directory)
    return bool(clients) and all(fresh(c) and c.get('generation') == generation
                                 and c.get('subscribed') is False for c in clients)


def status(directory: Path) -> dict:
    """Deliberately excludes URLs, event text, session storage and file paths."""
    owner = control(directory)
    pipeline = read_json(directory / 'pipeline.json')
    renderer = read_json(directory / 'renderer.json')
    try:
        clients = legacy_clients(directory)
        clients_ok = bool(clients) and all(fresh(c) for c in clients)
    except RuntimeError:
        clients, clients_ok = [], False
    return {'schema': SCHEMA, 'owner': owner['owner'], 'policy_valid': not owner.get('invalid', False),
            'pipeline_ready': fresh(pipeline) and pipeline.get('ready') is True,
            'renderer_alive': fresh(renderer), 'renderer_state': renderer.get('state')
            if renderer.get('state') in {'standby', 'starting', 'active', 'degraded', 'stopped'} else 'unknown',
            'legacy_clients': len(clients), 'legacy_healthy': clients_ok,
            'legacy_subscribers': sum(c.get('subscribed') is True for c in clients),
            'frame_state': pipeline.get('frame_state') if pipeline.get('frame_state') in
            {'fresh', 'stale', 'missing', 'invalid', 'unavailable', 'inactive', 'closed'} else 'unknown'}
