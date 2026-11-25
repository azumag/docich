"""Archive finished hanjuku-speedrun attempts to Cloudflare R2.

The speedrun corner spools its detailed logs and images inside each attempt
(see :mod:`docich.hanjuku_speedrun.rta.spool`).  Once an attempt has ended,
this module uploads that spool to the owner's R2 bucket, verifies every object
with ``HEAD``, and only then deletes the local files.  The upload runs in a
detached process so the game, the broadcast and the next corner never wait for
it (:func:`launch_pending`).

Layout in the bucket::

    hanjuku-speedrun/attempts/<attempt_no:04d>-<attempt_id>/spool/<file>
    hanjuku-speedrun/attempts/<attempt_no:04d>-<attempt_id>/spool/frames.tar
    hanjuku-speedrun/attempts/<attempt_no:04d>-<attempt_id>/manifest.json
    hanjuku-speedrun/keep/<attempt_no:04d>-<attempt_id>/...

``frames/`` is uploaded as one uncompressed tar.  A file whose detached gzip
is still running is deferred to the next run; an uncompressed file is sent as
it is (never wait for compression).  Uploads are idempotent: a retry PUTs the
same keys again.

Credentials come from ``~/.config/docich/r2.env`` (override with
``DOCICH_R2_ENV_FILE``).  The parser accepts ``KEY=VALUE`` lines only and never
evaluates the file as a shell script.  A missing/unreadable file means
"unconfigured": nothing is sent and the timer/spool keep working.  A file with
group/other permission bits is refused (fixed code only; its values are never
printed).  No credential value is written to a log, an exception, a manifest
or ``archive_state.json``.

Only the standard library is used: ``fcntl`` for the process lock, ``tarfile``
for the image bundle, ``hashlib``/``hmac``/``http.client`` for the signed
requests.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tarfile
import tempfile
import time

try:  # pragma: no cover - Windows has no fcntl; the VM (Linux) does.
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

from . import manifest, report_segments, sigv4, spool, store, timer

GAME = store.GAME
ATTEMPTS_DIR = spool.ATTEMPTS_DIR
SPOOL_DIR = spool.SPOOL_DIR
FRAMES_DIR = spool.FRAMES_DIR
LOCK_FILE = 'archive.lock'
ARCHIVE_STATE_FILE = 'archive_state.json'
MANIFEST_FILE = 'manifest.json'
SEGMENTS_FILE = 'segments.json'
FRAMES_TAR = 'frames.tar'
ENV_FILE_ENV = 'DOCICH_R2_ENV_FILE'
DATA_DIR_ENV = 'DOCICH_SPEEDRUN_DATA_DIR'
STATE_DIR_ENV = 'DOCICH_STATE_DIR'
ATTEMPTS_MAX_ENV = 'DOCICH_SPEEDRUN_ATTEMPTS_MAX_BYTES'
DEFAULT_ENV_PATH = Path('.config') / 'docich' / 'r2.env'
MAX_ENV_BYTES = 64 * 1024
DEFAULT_ATTEMPTS_MAX_BYTES = 4 * 1024 * 1024 * 1024
BACKOFF_BASE_SECONDS = 60
BACKOFF_MAX_SECONDS = 24 * 60 * 60
RECENT_LOCAL_ATTEMPTS = 3
#: A just-finished attempt often still has a detached gzip running.  The
#: uploader waits for those locks instead of failing the attempt and backing
#: off for a minute.
GZIP_WAIT_MAX_SECONDS = 10 * 60
GZIP_WAIT_INTERVAL_SECONDS = 3.0

REQUIRED_KEYS = (
    'R2_ACCOUNT_ID',
    'R2_ACCESS_KEY_ID',
    'R2_SECRET_ACCESS_KEY',
    'R2_BUCKET',
)
_KEY_CHARS = frozenset('ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_')
_ENDED = frozenset({'finished', 'dnf'})


class ArchiveError(Exception):
    """A fixed-code archive failure (never contains credentials)."""

    def __init__(self, code):
        self.code = str(code)
        super().__init__(self.code)


class ConfigError(Exception):
    """A fixed-code configuration failure (never contains values)."""

    def __init__(self, code):
        self.code = str(code)
        super().__init__(self.code)


# ---------------------------------------------------------------------------
# paths
# ---------------------------------------------------------------------------


def env_path():
    """The credentials file path (the contents are read only on upload)."""
    raw = os.environ.get(ENV_FILE_ENV)
    if raw:
        return Path(raw).expanduser()
    return Path.home() / DEFAULT_ENV_PATH


def _looks_like_data_dir(path):
    return path.name == 'hanjuku-speedrun' and path.parent.name == 'speedrun'


def data_dir(value=None):
    """Normalise a runtime dir, data dir or state dir to the speedrun data dir.

    ``value`` may be ``None`` (the default location), a runtime directory
    (``.../run-soren-live/runtimes/<id>``), the data directory itself, or a
    state directory (``.../run-soren-live``).
    """
    if value is None:
        return default_data_dir()
    path = Path(value).expanduser()
    if path.parent.name == 'runtimes':
        return store.data_dir(path)
    if _looks_like_data_dir(path):
        return path
    nested = path / 'speedrun' / 'hanjuku-speedrun'
    if nested.is_dir():
        return nested
    return path


def default_data_dir():
    """The data directory for the detached CLI when no ``--data-dir`` given."""
    raw = os.environ.get(DATA_DIR_ENV)
    if raw:
        return Path(raw).expanduser()
    state = os.environ.get(STATE_DIR_ENV)
    if state:
        return Path(state).expanduser() / 'speedrun' / 'hanjuku-speedrun'
    root = Path(__file__).resolve().parents[4]
    for name in ('run-soren-live', 'run'):
        candidate = root / name / 'speedrun' / 'hanjuku-speedrun'
        if candidate.is_dir():
            return candidate
    return root / 'run-soren-live' / 'speedrun' / 'hanjuku-speedrun'


def attempt_dir(directory, attempt_id):
    try:
        return spool.attempt_dir_in(directory, attempt_id)
    except spool.SpoolError:
        raise ArchiveError('invalid_attempt_id') from None


def spool_dir(directory, attempt_id):
    try:
        return spool.spool_dir_in(directory, attempt_id)
    except spool.SpoolError:
        raise ArchiveError('invalid_attempt_id') from None


def manifest_path(directory, attempt_id):
    return attempt_dir(directory, attempt_id) / MANIFEST_FILE


def segments_path(directory, attempt_id):
    return attempt_dir(directory, attempt_id) / SEGMENTS_FILE


def archive_state_path(directory, attempt_id):
    return attempt_dir(directory, attempt_id) / ARCHIVE_STATE_FILE


# ---------------------------------------------------------------------------
# configuration
# ---------------------------------------------------------------------------


class R2Config:
    """The values needed to talk to one R2 bucket."""

    def __init__(self, account_id, access_key_id, secret_access_key, bucket,
                 endpoint, *, endpoint_explicit=False):
        self.account_id = account_id
        self.access_key_id = access_key_id
        self.secret_access_key = secret_access_key
        self.bucket = bucket
        self.endpoint = endpoint
        self.endpoint_explicit = endpoint_explicit

    def __repr__(self):  # never show credentials or the account id
        return f'R2Config(bucket={self.bucket!r})'

    @property
    def credentials(self):
        return sigv4.Credentials(self.access_key_id, self.secret_access_key)


def _parse_env_text(text):
    values = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        if '=' not in line:
            raise ConfigError('invalid_env')
        name, value = line.split('=', 1)
        name = name.strip()
        if not name or any(char not in _KEY_CHARS for char in name):
            raise ConfigError('invalid_env')
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values[name] = value
    return values


def _read_env_file(path):
    try:
        meta = os.lstat(path)
    except FileNotFoundError:
        raise ConfigError('unconfigured') from None
    except OSError:
        raise ConfigError('unconfigured') from None
    if stat.S_ISLNK(meta.st_mode) or not stat.S_ISREG(meta.st_mode):
        raise ConfigError('unreadable_env')
    if meta.st_size > MAX_ENV_BYTES:
        raise ConfigError('invalid_env')
    if meta.st_mode & 0o077:
        # Group/other can read the credentials.  Refusing is the safe choice;
        # the caller sees one fixed code and no values.  The owner fixes the
        # mode and the next run retries.
        raise ConfigError('insecure_permissions')
    try:
        text = path.read_text(encoding='utf-8')
    except (OSError, UnicodeError):
        raise ConfigError('unreadable_env') from None
    return _parse_env_text(text)


def load_config(path=None):
    """Return ``(R2Config, 'ok')`` or ``(None, fixed_code)``."""
    path = Path(path) if path is not None else env_path()
    try:
        values = _read_env_file(path)
    except ConfigError as exc:
        # An unreadable file is treated like a missing one: nothing is sent
        # and the timer/spool keep working (fixed code only).
        code = 'unconfigured' if exc.code in {'unconfigured', 'unreadable_env'} else exc.code
        return None, code
    missing = [name for name in REQUIRED_KEYS
               if not str(values.get(name) or '').strip()]
    if missing:
        return None, 'unconfigured'
    account = str(values['R2_ACCOUNT_ID']).strip()
    bucket = str(values['R2_BUCKET']).strip()
    if '/' in bucket or any(char.isspace() for char in bucket):
        return None, 'invalid_config'
    endpoint_value = str(values.get('R2_ENDPOINT') or '').strip()
    explicit = bool(endpoint_value)
    if not endpoint_value:
        endpoint_value = f'https://{account}.r2.cloudflarestorage.com'
    try:
        endpoint = sigv4.parse_endpoint(endpoint_value, explicit=explicit)
    except sigv4.SigV4Error as exc:
        return None, exc.code
    config = R2Config(
        account, str(values['R2_ACCESS_KEY_ID']).strip(),
        str(values['R2_SECRET_ACCESS_KEY']).strip(), bucket, endpoint,
        endpoint_explicit=explicit)
    return config, 'ok'


def configuration_status(path=None):
    """``(configured, fixed_code)`` without sending anything."""
    config, code = load_config(path)
    return config is not None, code


# ---------------------------------------------------------------------------
# small bounded readers
# ---------------------------------------------------------------------------


def _read_json(path):
    return spool.read_json(path)


def _write_json(path, document):
    spool.write_json(path, document)


def _now_ms(now_ms=None):
    return now_ms if type(now_ms) is int and now_ms >= 0 else int(time.time() * 1000)


def _positive_int(value, default):
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def attempts_max_bytes():
    raw = os.environ.get(ATTEMPTS_MAX_ENV)
    return _positive_int(raw, DEFAULT_ATTEMPTS_MAX_BYTES)


def _iter_files(directory):
    """All regular files below ``directory`` (symlinks are never followed)."""
    directory = Path(directory)
    if directory.is_symlink() or not directory.is_dir():
        return []
    collected = []
    stack = [directory]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            size = entry.stat(follow_symlinks=False).st_size
                            relative = Path(entry.path).relative_to(directory).as_posix()
                            collected.append((relative, Path(entry.path), size))
                    except OSError:
                        continue
        except OSError:
            continue
    collected.sort(key=lambda item: item[0])
    return collected


def _prune_empty_dirs(root):
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        return
    for current, _dirs, _files in os.walk(root, topdown=False):
        try:
            os.rmdir(current)
        except OSError:
            pass


def _remove_files(directory):
    """Delete every regular file below ``directory``; returns freed bytes."""
    removed = 0
    for _relative, path, size in _iter_files(directory):
        try:
            os.unlink(path)
            removed += size
        except OSError:
            continue
    _prune_empty_dirs(directory)
    return removed


def _spool_bytes(directory):
    return sum(size for _rel, _path, size in _iter_files(directory))


def _has_files(directory):
    return bool(_iter_files(directory))


# ---------------------------------------------------------------------------
# attempt metadata
# ---------------------------------------------------------------------------


def _valid_attempt_id(value):
    try:
        spool._component(value, 'attempt_id')
    except Exception:
        return False
    return True


def _rows(directory):
    return [row for row in store.load_attempts_in(directory)
            if isinstance(row, dict)]


def _row_for(rows, attempt_id):
    for row in reversed(rows):
        if row.get('attempt_id') == attempt_id:
            return row
    return {}


def _timer(directory):
    timer = _read_json(Path(directory) / store.TIMER_FILE)
    return timer if isinstance(timer, dict) else {}


def running_attempt_id(directory):
    timer = _timer(directory)
    if timer.get('status') != 'running':
        return None
    attempt_id = timer.get('attempt_id')
    return attempt_id if isinstance(attempt_id, str) and attempt_id else None


def _attempt_info(directory, attempt_id, *, rows=None):
    rows = _rows(directory) if rows is None else rows
    row = _row_for(rows, attempt_id)
    document = _read_json(manifest_path(directory, attempt_id))
    manifest_doc = document if isinstance(document, dict) else {}
    status = row.get('status')
    if not (isinstance(status, str) and status in {'pending', 'running', 'finished', 'dnf'}):
        candidate = manifest_doc.get('status') or manifest_doc.get('result')
        status = candidate if isinstance(candidate, str) else None
    timer = _timer(directory)
    if (timer.get('status') == 'running'
            and timer.get('attempt_id') == attempt_id):
        status = 'running'
    attempt_no = row.get('attempt_no')
    if type(attempt_no) is not int:
        attempt_no = manifest_doc.get('attempt_no')
    if type(attempt_no) is not int or attempt_no < 0:
        attempt_no = 0
    runtime_id = row.get('runtime_id')
    if not isinstance(runtime_id, str) or not runtime_id:
        candidate = manifest_doc.get('runtime_id')
        runtime_id = candidate if isinstance(candidate, str) else None
    started = row.get('started_at_ms')
    if type(started) is not int:
        candidate = manifest_doc.get('started_at_ms')
        started = candidate if type(candidate) is int else None
    return {
        'attempt_id': attempt_id,
        'attempt_no': attempt_no,
        'status': status,
        'runtime_id': runtime_id,
        'archived': (row.get('archived') is True
                     or manifest_doc.get('archived') is True),
        'started_at_ms': started,
        'manifest': manifest_doc,
    }


def _attempt_ids(directory):
    ids = []
    for row in _rows(directory):
        value = row.get('attempt_id')
        if isinstance(value, str) and value and _valid_attempt_id(value) and value not in ids:
            ids.append(value)
    attempts = Path(directory) / ATTEMPTS_DIR
    if attempts.is_dir() and not attempts.is_symlink():
        try:
            with os.scandir(attempts) as entries:
                for entry in entries:
                    if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
                        continue
                    name = entry.name
                    if name.startswith('.') or name in ids:
                        continue
                    if not _valid_attempt_id(name):
                        continue
                    # Orphan directories have neither a row nor a manifest;
                    # the total-attempt cap still has to account for and
                    # eventually reclaim their spool.
                    ids.append(name)
        except OSError:
            pass
    return ids


def _attempt_dir_mtime(directory, attempt_id):
    """``attempts/<id>`` mtime; unknown ids sort last rather than first."""
    try:
        return os.lstat(attempt_dir(directory, attempt_id)).st_mtime
    except OSError:
        return float('inf')


def _sort_key(info):
    started = info.get('started_at_ms')
    return (info.get('attempt_no') or 0,
            started if type(started) is int else 0,
            info.get('attempt_id') or '')


def _archive_state(directory, attempt_id):
    document = _read_json(archive_state_path(directory, attempt_id))
    return document if isinstance(document, dict) else {}


def _record_failure(directory, attempt_id, code, now_ms):
    path = archive_state_path(directory, attempt_id)
    state = _archive_state(directory, attempt_id)
    attempts = state.get('attempts') if type(state.get('attempts')) is int else 0
    attempts = max(0, attempts) + 1
    delay = min(BACKOFF_MAX_SECONDS, BACKOFF_BASE_SECONDS * (2 ** min(attempts - 1, 20)))
    state.update({
        'schema': 1,
        'attempt_id': attempt_id,
        'attempts': attempts,
        'last_error': code,
        'last_error_at_ms': now_ms,
        'next_retry_at_ms': now_ms + delay * 1000,
    })
    try:
        _write_json(path, state)
    except Exception:
        pass
    return state


def _clear_failure(directory, attempt_id):
    path = archive_state_path(directory, attempt_id)
    try:
        meta = os.lstat(path)
    except OSError:
        return
    if stat.S_ISREG(meta.st_mode):
        try:
            os.unlink(path)
        except OSError:
            pass


def _record_dropped_for_cap(directory, attempt_id, dropped, now_ms):
    if dropped <= 0:
        return
    state = _archive_state(directory, attempt_id)
    previous = state.get('dropped_for_cap')
    previous = previous if type(previous) is int and previous > 0 else 0
    state.update({
        'schema': 1,
        'attempt_id': attempt_id,
        'dropped_for_cap': previous + dropped,
        'dropped_for_cap_at_ms': now_ms,
    })
    try:
        _write_json(archive_state_path(directory, attempt_id), state)
    except Exception:
        pass
    document = _read_json(manifest_path(directory, attempt_id))
    if isinstance(document, dict):
        previous = document.get('dropped_for_cap')
        previous = previous if type(previous) is int and previous > 0 else 0
        document['dropped_for_cap'] = previous + dropped
        document['dropped_for_cap_at_ms'] = now_ms
        try:
            _write_json(manifest_path(directory, attempt_id), document)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# total attempt cap (PR4a review [M4])
# ---------------------------------------------------------------------------


def enforce_attempts_cap(directory, *, max_bytes_value=None, now_ms=None):
    """Keep all spools under ``DOCICH_SPEEDRUN_ATTEMPTS_MAX_BYTES``.

    The oldest non-archived attempts lose their spool first; ``manifest.json``
    and ``segments.json`` stay.  The attempt named by a ``running`` timer is
    never touched.  The number of bytes dropped is recorded as
    ``dropped_for_cap`` in ``archive_state.json`` and ``manifest.json``.
    """
    directory = Path(directory)
    cap = (max_bytes_value if type(max_bytes_value) is int and max_bytes_value > 0
           else attempts_max_bytes())
    now = _now_ms(now_ms)
    attempts = Path(directory) / ATTEMPTS_DIR
    if attempts.is_symlink() or not attempts.is_dir():
        return 0
    running = running_attempt_id(directory)
    total = 0
    candidates = []
    rows = _rows(directory)
    for info in (_attempt_info(directory, attempt_id, rows=rows)
                 for attempt_id in _attempt_ids(directory)):
        attempt_id = info['attempt_id']
        spool_path = spool_dir(directory, attempt_id)
        size = _spool_bytes(spool_path)
        if size <= 0:
            continue
        total += size
        if attempt_id == running or info['archived']:
            continue
        candidates.append(info)
    if total <= cap:
        return 0
    dropped_total = 0
    def cap_order(info):
        # Oldest attempt directory first.  ``attempt_no`` 0 means "unknown"
        # and must not jump ahead of known attempts; it only breaks ties.
        unknown = 0 if (info.get('attempt_no') or 0) > 0 else 1
        return (_attempt_dir_mtime(directory, info['attempt_id']), unknown,
                info.get('attempt_no') or 0, info['attempt_id'])
    for info in sorted(candidates, key=cap_order):
        if total <= cap:
            break
        attempt_id = info['attempt_id']
        removed = _remove_files(spool_dir(directory, attempt_id))
        if removed <= 0:
            continue
        total = max(0, total - removed)
        dropped_total += removed
        _record_dropped_for_cap(directory, attempt_id, removed, now)
    return dropped_total


def enforce_cap(runtime_data_dir=None, *, now_ms=None):
    """Apply the total-attempt cap without sending anything.

    ``launch_pending`` and the teardown call this even when R2 is not
    configured, so an unconfigured (or offline) VM cannot grow the attempts
    tree without bound.  It takes the same process lock as an uploader and
    never raises.  Returns the number of bytes reclaimed.
    """
    try:
        directory = data_dir(runtime_data_dir)
        if not directory.is_dir():
            return 0
        fd = _acquire_lock(directory)
        if fd is None:
            return 0
        try:
            return enforce_attempts_cap(directory, now_ms=now_ms)
        finally:
            _release_lock(fd)
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# local retention
# ---------------------------------------------------------------------------


def enforce_local_retention(directory, *, keep=RECENT_LOCAL_ATTEMPTS):
    """Keep only the newest ``keep`` manifests/segment summaries locally.

    Archived attempts older than that lose ``manifest.json``,
    ``segments.json`` and ``archive_state.json``.  A non-archived attempt is
    never pruned, because its spool still needs its retry state.
    """
    directory = Path(directory)
    rows = _rows(directory)
    infos = [_attempt_info(directory, attempt_id, rows=rows)
             for attempt_id in _attempt_ids(directory)]
    newest = sorted(infos, key=_sort_key, reverse=True)[:max(0, keep)]
    keep_ids = {info['attempt_id'] for info in newest}
    for info in infos:
        attempt_id = info['attempt_id']
        if attempt_id in keep_ids or not info['archived']:
            continue
        if _has_files(spool_dir(directory, attempt_id)):
            # Never report an attempt as fully archived while its spool is
            # still present; a later upload pass will finish it.
            continue
        base = attempt_dir(directory, attempt_id)
        for name in (MANIFEST_FILE, SEGMENTS_FILE, ARCHIVE_STATE_FILE):
            path = base / name
            try:
                meta = os.lstat(path)
            except OSError:
                continue
            if stat.S_ISREG(meta.st_mode):
                try:
                    os.unlink(path)
                except OSError:
                    pass
        _prune_empty_dirs(base)


# ---------------------------------------------------------------------------
# upload
# ---------------------------------------------------------------------------


class ArchiveResult:
    """The outcome of one attempt upload (only fixed codes and numbers)."""

    def __init__(self, ok, code, *, uploaded=0, bytes_uploaded=0):
        self.ok = bool(ok)
        self.code = str(code)
        self.uploaded = uploaded
        self.bytes_uploaded = bytes_uploaded

    def __repr__(self):
        return (f'ArchiveResult(ok={self.ok!r}, code={self.code!r}, '
                f'uploaded={self.uploaded!r}, bytes={self.bytes_uploaded!r})')

    def __bool__(self):
        return self.ok

    def as_dict(self):
        return {
            'ok': self.ok,
            'code': self.code,
            'uploaded': self.uploaded,
            'bytes': self.bytes_uploaded,
        }


def _gzip_lock_live(path):
    """True when ``<path>.gz.lock`` names a live compressor."""
    lock = Path(str(path) + spool.GZIP_LOCK_SUFFIX)
    try:
        meta = os.lstat(lock)
    except OSError:
        return False
    if not stat.S_ISREG(meta.st_mode):
        return False
    pid = spool._read_lock_pid(lock)
    if isinstance(pid, int) and spool._pid_alive(pid):
        return True
    # The launcher died (or the lock is stale).  Drop it and send the
    # uncompressed file rather than waiting forever.
    try:
        os.unlink(lock)
    except OSError:
        return True
    return False


def _await_gzip(directory, attempt_id, *, max_seconds=None, interval=None):
    """Wait (bounded) for detached gzip locks on this attempt to clear.

    Called only from the detached ``upload --pending`` process, where a
    just-finished attempt's compressor is usually seconds away from done.
    Dead/zombie locks are taken over by :func:`_gzip_lock_live` immediately.
    A live lock that outlives the bound raises ``gzip_pending``; that outcome
    is not a retryable failure and is not fed into the backoff state.
    """
    spool_path = spool_dir(directory, attempt_id)
    if spool_path.is_symlink() or not spool_path.is_dir():
        return
    wait_max = (GZIP_WAIT_MAX_SECONDS if max_seconds is None
                else max(0.0, float(max_seconds)))
    wait_interval = (GZIP_WAIT_INTERVAL_SECONDS if interval is None
                     else max(0.01, float(interval)))
    deadline = time.monotonic() + wait_max
    while True:
        pending = False
        for relative, path, _size in _iter_files(spool_path):
            if relative.startswith(f'{FRAMES_DIR}/'):
                continue
            name = Path(relative).name
            if name.startswith('.') or name.endswith(spool.GZIP_LOCK_SUFFIX):
                continue
            if _gzip_lock_live(path):
                pending = True
                break
        if not pending:
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ArchiveError('gzip_pending')
        time.sleep(min(wait_interval, remaining))


def _build_frames_tar(directory, attempt_id):
    """Bundle ``spool/frames/`` into one uncompressed tar.

    Returns ``(tar_path, included)`` where ``included`` lists the frame files
    actually written to the tar; ``(None, [])`` when there are no frames.  The
    caller deletes only ``included`` (an unreadable or late frame is kept).
    """
    frames = spool.frames_dir_in(directory, attempt_id)
    entries = [(relative, path, size) for relative, path, size in _iter_files(frames)
               if not Path(relative).name.startswith('.')]
    if not entries:
        return None, []
    base = attempt_dir(directory, attempt_id)
    try:
        base.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix='.frames-', suffix='.tar',
                                         dir=str(base))
    except OSError:
        raise ArchiveError('frames_tar_failed') from None
    os.close(fd)
    target = Path(temporary)
    included = []
    try:
        added = 0
        with tarfile.open(target, 'w') as archive:
            for relative, path, size in entries:
                flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
                try:
                    handle = os.open(path, flags)
                except OSError:
                    continue
                with os.fdopen(handle, 'rb') as stream:
                    if size <= 0:
                        continue
                    info = tarfile.TarInfo(name=f'{FRAMES_DIR}/{relative}')
                    info.size = size
                    info.mode = 0o600
                    try:
                        info.mtime = int(os.lstat(path).st_mtime)
                    except OSError:
                        info.mtime = 0
                    archive.addfile(info, stream)
                    added += 1
                    included.append((path, size))
        if added == 0:
            raise OSError('empty tar')
        return target, included
    except (OSError, tarfile.TarError):
        try:
            target.unlink()
        except OSError:
            pass
        raise ArchiveError('frames_tar_failed') from None


def _remote_prefix(info):
    return f'{GAME}/attempts/{info["attempt_no"]:04d}-{info["attempt_id"]}'


def _keep_prefix(info):
    return f'{GAME}/keep/{info["attempt_no"]:04d}-{info["attempt_id"]}'


def _ensure_manifest(directory, attempt_id, info):
    """Try to create a missing manifest/segments before uploading.

    The common teardown normally writes them.  If it stopped between the two
    steps the archive job rebuilds them from the data directory; when a
    runtime directory is still available its remaining logs and frame ring
    are collected first.  A failure is not fatal: the spool alone is sent.
    """
    if manifest_path(directory, attempt_id).is_file():
        return True
    runtime_id = info.get('runtime_id')
    if isinstance(runtime_id, str) and runtime_id:
        try:
            runtime_id = spool._component(runtime_id, 'runtime_id')
            runtime = Path(directory).parent.parent / 'runtimes' / runtime_id
            timer = _timer(directory)
            live_other = (timer.get('status') == 'running'
                          and timer.get('runtime_id') == runtime_id
                          and timer.get('attempt_id') not in {None, attempt_id})
            # Never move a live attempt's runtime logs into an older attempt.
            if runtime.is_dir() and not live_other:
                spool.finalize_logs(runtime, attempt_id)
        except Exception:
            pass
        if manifest_path(directory, attempt_id).is_file():
            return True
    try:
        report_segments.build_segments_at(directory, attempt_id)
    except Exception:
        pass
    try:
        manifest.build_manifest_at(directory, attempt_id)
    except Exception:
        return False
    return manifest_path(directory, attempt_id).is_file()


def _keep_attempt(info, records):
    """True when records name this attempt as PB or a segment best."""
    attempt_id = info['attempt_id']
    pb = records.get('pb')
    if isinstance(pb, dict) and pb.get('attempt_id') == attempt_id:
        return True
    gold = records.get('gold_segments')
    for item in gold if isinstance(gold, list) else ():
        if isinstance(item, dict) and item.get('attempt_id') == attempt_id:
            return True
    return False


def _load_records(directory):
    raw = _read_json(Path(directory) / store.RECORDS_FILE)
    if not isinstance(raw, dict) or raw.get('game') not in (None, GAME):
        return {}
    try:
        return timer.normalize_records(raw)
    except Exception:
        return {}


def _client_put(client, key, path):
    """PUT through either our name or the S3-style alias."""
    method = getattr(client, 'put_file', None) or getattr(
        client, 'put_object', None)
    if method is None:
        raise ArchiveError('internal_error')
    return method(key, path)


def _client_head(client, key):
    method = getattr(client, 'head_size', None) or getattr(
        client, 'head_object', None)
    if method is None:
        raise ArchiveError('internal_error')
    return method(key)


def _client_copy(client, source_key, destination_key):
    method = getattr(client, 'copy_object', None)
    if method is None:
        return False
    return bool(method(source_key, destination_key))


def _verify(client, key, expected):
    try:
        remote = _client_head(client, key)
    except sigv4.SigV4Error:
        raise ArchiveError('head_failed') from None
    if isinstance(remote, dict):
        remote = remote.get('ContentLength', remote.get('content_length'))
    if remote is None:
        raise ArchiveError('head_missing')
    try:
        remote = int(remote)
    except (TypeError, ValueError):
        raise ArchiveError('head_failed') from None
    if remote != expected:
        raise ArchiveError('size_mismatch')


def _copy_to_keep(client, keep_base, entries):
    """Best-effort keep copies; returns ``(ok, failed)``."""
    keep_ok = True
    failed = 0
    for key, path, _size, relative in entries:
        destination = f'{keep_base}/{relative}'
        copied = False
        try:
            copied = _client_copy(client, key, destination)
        except Exception:
            copied = False
        if not copied:
            try:
                _client_put(client, destination, path)
                copied = True
            except Exception:
                copied = False
        if not copied:
            keep_ok = False
            failed += 1
    return keep_ok, failed


def _delete_verified_local(spool_path, uploaded, tar_included, tar_path,
                           segments_local):
    """Delete only files whose bytes are known to be in the bucket.

    ``uploaded`` covers the spool files, the frames tar and ``segments.json``;
    the individual frame files are covered by the tar that contains them.
    Anything not in either list (a late frame, a dotfile, a live/stale lock
    that was not reaped) stays for the next pass.
    """
    to_delete = []
    for _key, path, _size, _relative in uploaded:
        if path == tar_path or path == segments_local:
            continue
        try:
            inside = Path(path).is_relative_to(spool_path)
        except (AttributeError, ValueError):
            inside = False
        if inside:
            to_delete.append(Path(path))
    for path, _size in tar_included:
        to_delete.append(Path(path))
    # A finished gzip leaves its lock next to the compressed file.  The lock
    # is not uploaded, but reaping it before the delete keeps a fully
    # archived attempt from looking "pending" forever.
    spool._reap_stale_locks(spool_path)
    for path in to_delete:
        try:
            os.unlink(path)
        except OSError:
            continue
    _prune_empty_dirs(spool_path)


def _do_upload(directory, info, config, *, now_ms, client=None,
               wait_gzip=False):
    attempt_id = info['attempt_id']
    base = attempt_dir(directory, attempt_id)
    if not base.is_dir():
        raise ArchiveError('missing_attempt')
    if base.is_symlink():
        raise ArchiveError('unsafe_directory')
    if spool_dir(directory, attempt_id).is_symlink():
        raise ArchiveError('unsafe_directory')
    manifest_ready = _ensure_manifest(directory, attempt_id, info)
    info = _attempt_info(directory, attempt_id)

    if wait_gzip:
        _await_gzip(directory, attempt_id)

    files = []
    for relative, path, size in _iter_files(spool_dir(directory, attempt_id)):
        if relative.startswith(f'{FRAMES_DIR}/'):
            continue
        name = Path(relative).name
        if name.startswith('.') or name.endswith(spool.GZIP_LOCK_SUFFIX):
            continue
        if _gzip_lock_live(path):
            raise ArchiveError('gzip_pending')
        files.append((f'{SPOOL_DIR}/{relative}', path, size))

    tar_path, tar_included = _build_frames_tar(directory, attempt_id)
    tar_uploaded = False
    temp_manifest = None
    try:
        if tar_path is not None:
            files.append((f'{SPOOL_DIR}/{FRAMES_TAR}', tar_path,
                          tar_path.stat().st_size))
        client = client or sigv4.R2Client(
            config.endpoint, config.credentials, config.bucket)
        prefix = _remote_prefix(info)
        uploaded = []
        total_bytes = 0
        for relative, path, size in files:
            key = f'{prefix}/{relative}'
            try:
                sent = _client_put(client, key, path)
            except sigv4.SigV4Error as exc:
                if exc.code == 'local_changed':
                    raise ArchiveError('local_changed') from None
                raise ArchiveError('put_failed') from None
            _verify(client, key, sent)
            uploaded.append((key, path, sent, relative))
            total_bytes += sent
            if tar_path is not None and path == tar_path:
                tar_uploaded = True

        # Nothing may change between the upload and the local delete.
        for _key, path, size, _relative in uploaded:
            try:
                current = os.lstat(path).st_size
            except OSError:
                current = None
            if current != size:
                raise ArchiveError('local_changed')
        for path, size in tar_included:
            try:
                current = os.lstat(path).st_size
            except OSError:
                current = None
            if current != size:
                raise ArchiveError('local_changed')

        segments_local = segments_path(directory, attempt_id)
        segments_meta = spool._regular(segments_local)
        if segments_meta is not None:
            key = f'{prefix}/{SEGMENTS_FILE}'
            try:
                sent = _client_put(client, key, segments_local)
            except sigv4.SigV4Error:
                raise ArchiveError('put_failed') from None
            _verify(client, key, sent)
            uploaded.append((key, segments_local, sent, SEGMENTS_FILE))
            total_bytes += sent

        # Keep copies are best effort and must not undo the archive.  The
        # manifest itself is copied after its final (archived) bytes exist.
        keep_ok = True
        keep_failed = 0
        keep_wanted = _keep_attempt(info, _load_records(directory))
        if keep_wanted:
            keep_ok, keep_failed = _copy_to_keep(
                client, _keep_prefix(info), uploaded)

        total_uploaded = len(uploaded)
        if manifest_ready:
            document = _read_json(manifest_path(directory, attempt_id))
            document = dict(document) if isinstance(document, dict) else {}
            document.update({
                'schema': document.get('schema', manifest.SCHEMA),
                'game': GAME,
                'attempt_id': attempt_id,
                'attempt_no': info['attempt_no'],
                'archived': True,
                'archived_at_ms': now_ms,
                'archived_files': [{'key': key, 'bytes': size}
                                   for key, _path, size, _relative in uploaded],
                'archived_bytes': total_bytes,
                'manifest_archived': True,
                'keep_copied': keep_ok,
                'keep_failed': keep_failed,
            })
            fd, temporary = tempfile.mkstemp(
                prefix='.manifest-', suffix='.json',
                dir=str(attempt_dir(directory, attempt_id)))
            os.close(fd)
            temp_manifest = Path(temporary)
            _write_json(temp_manifest, document)
            manifest_key = f'{prefix}/{MANIFEST_FILE}'
            try:
                sent = _client_put(client, manifest_key, temp_manifest)
            except (OSError, sigv4.SigV4Error):
                raise ArchiveError('put_failed') from None
            _verify(client, manifest_key, sent)
            if keep_wanted:
                destination = f'{_keep_prefix(info)}/{MANIFEST_FILE}'
                copied = False
                try:
                    copied = _client_copy(client, manifest_key, destination)
                except Exception:
                    copied = False
                if not copied:
                    try:
                        _client_put(client, destination, temp_manifest)
                        copied = True
                    except Exception:
                        copied = False
                if not copied:
                    keep_ok = False
                    keep_failed += 1
                    document['keep_copied'] = keep_ok
                    document['keep_failed'] = keep_failed
                    # A failed metadata refresh must not turn a successful
                    # spool upload into a failure; the next pass retries the
                    # keep copy and the manifest bytes.
                    try:
                        _write_json(temp_manifest, document)
                        refreshed = _client_put(
                            client, manifest_key, temp_manifest)
                        _verify(client, manifest_key, refreshed)
                    except Exception:
                        pass
            total_bytes += sent
            total_uploaded += 1
            os.replace(temp_manifest, manifest_path(directory, attempt_id))
            temp_manifest = None

        if tar_path is None or tar_uploaded:
            _delete_verified_local(
                spool_dir(directory, attempt_id), uploaded, tar_included,
                tar_path, segments_local)
        try:
            store.update_attempt_in(directory, attempt_id, {
                'archived': True,
                'archived_at_ms': now_ms,
                'keep_copied': keep_ok,
                'keep_failed': keep_failed,
            })
        except Exception:
            pass
        _clear_failure(directory, attempt_id)
        enforce_local_retention(directory)
        return ArchiveResult(True, 'ok', uploaded=total_uploaded,
                             bytes_uploaded=total_bytes)
    finally:
        if tar_path is not None:
            try:
                tar_path.unlink()
            except OSError:
                pass
        if temp_manifest is not None:
            try:
                temp_manifest.unlink()
            except OSError:
                pass


def upload_attempt(runtime_data_dir, attempt_id, *, config=None, client=None,
                   now_ms=None, wait_gzip=False):
    """Upload one ended attempt, verify it, then delete verified local files.

    ``runtime_data_dir`` may be the runtime directory or the speedrun data
    directory.  The function is idempotent; the caller owns the process lock
    (the CLI and :func:`upload_pending` take it).  It never raises for a
    normal failure: the returned result carries a fixed code.  ``wait_gzip``
    is set only by the detached pending uploader (bounded wait for a
    just-finished attempt's compressor).
    """
    now = _now_ms(now_ms)
    try:
        directory = data_dir(runtime_data_dir)
        attempt_id = spool._component(attempt_id, 'attempt_id')
        info = _attempt_info(directory, attempt_id)
        if info['status'] is None and not manifest_path(directory, attempt_id).exists():
            raise ArchiveError('missing_attempt')
        if info['status'] == 'running':
            raise ArchiveError('not_ended')
        if info['status'] not in _ENDED:
            raise ArchiveError('not_ended')
    except ArchiveError as exc:
        return ArchiveResult(False, exc.code)
    except Exception:
        return ArchiveResult(False, 'invalid_attempt')

    if client is None and config is None:
        config, code = load_config()
        if config is None:
            if code == 'unconfigured':
                return ArchiveResult(True, 'unconfigured')
            _record_failure(directory, attempt_id, code, now)
            return ArchiveResult(False, code)
    try:
        return _do_upload(directory, info, config, now_ms=now, client=client,
                          wait_gzip=wait_gzip)
    except ArchiveError as exc:
        if exc.code != 'gzip_pending':
            # A compressor that is still running is a normal transient at
            # teardown, not a retryable failure: do not advance the backoff.
            _record_failure(directory, attempt_id, exc.code, now)
        return ArchiveResult(False, exc.code)
    except Exception:
        _record_failure(directory, attempt_id, 'internal_error', now)
        return ArchiveResult(False, 'internal_error')


# ---------------------------------------------------------------------------
# process lock helpers
# ---------------------------------------------------------------------------


def _lock_path(directory):
    return Path(directory) / LOCK_FILE


def _acquire_lock(directory):
    """Non-blocking ``flock``; returns the fd or ``None`` when already held."""
    if fcntl is None:  # pragma: no cover - the VM is Linux
        return 0
    path = _lock_path(directory)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    except OSError:
        return None
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    return fd


def _release_lock(fd):
    if fcntl is None:  # pragma: no cover
        return
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass
    try:
        os.close(fd)
    except OSError:
        pass


def lock_is_free(directory):
    if fcntl is None:  # pragma: no cover
        return True
    path = _lock_path(directory)
    if not path.exists():
        return True
    fd = _acquire_lock(directory)
    if fd is None:
        return False
    _release_lock(fd)
    return True


# ---------------------------------------------------------------------------
# pending queue
# ---------------------------------------------------------------------------


def _pending(directory, *, now_ms):
    pending = []
    rows = _rows(directory)
    for attempt_id in _attempt_ids(directory):
        info = _attempt_info(directory, attempt_id, rows=rows)
        if info['status'] not in _ENDED:
            continue
        if info['archived'] and not _has_files(spool_dir(directory, attempt_id)):
            continue
        state = _archive_state(directory, attempt_id)
        next_retry = state.get('next_retry_at_ms')
        if type(next_retry) is int and next_retry > now_ms:
            continue
        pending.append(info)
    pending.sort(key=_sort_key)
    return pending


def upload_pending(runtime_data_dir=None, *, now_ms=None, client=None):
    """Upload every finished/DNF attempt that is not archived yet.

    Takes the process lock.  Enforces the total-attempt cap at the start and
    after the pass, so a long backlog cannot grow without bound even while
    the network is unavailable.
    """
    now = _now_ms(now_ms)
    directory = data_dir(runtime_data_dir)
    if not directory.is_dir():
        return {'ok': True, 'code': 'no_data', 'attempted': 0,
                'uploaded': 0, 'failed': 0, 'bytes': 0}
    fd = _acquire_lock(directory)
    if fd is None:
        return {'ok': True, 'code': 'locked', 'attempted': 0,
                'uploaded': 0, 'failed': 0, 'bytes': 0}
    attempted = uploaded = failed = total_bytes = 0
    code = 'ok'
    try:
        enforce_attempts_cap(directory, now_ms=now)
        config = None
        if client is None:
            config, config_code = load_config()
            if config is None:
                if config_code == 'unconfigured':
                    return {'ok': True, 'code': 'unconfigured',
                            'attempted': 0, 'uploaded': 0, 'failed': 0,
                            'bytes': 0}
                return {'ok': False, 'code': config_code, 'attempted': 0,
                        'uploaded': 0, 'failed': 0, 'bytes': 0}
        for info in _pending(directory, now_ms=now):
            attempted += 1
            result = upload_attempt(
                directory, info['attempt_id'], config=config,
                client=client, now_ms=now, wait_gzip=True)
            if result.ok:
                uploaded += 1
                total_bytes += result.bytes_uploaded
            elif result.code != 'gzip_pending':
                # A live compressor that outlived the bounded wait is a
                # deferred attempt, not a failure; no backoff is recorded.
                failed += 1
        enforce_attempts_cap(directory, now_ms=now)
        enforce_local_retention(directory)
    finally:
        _release_lock(fd)
    if failed:
        code = 'partial_failure'
    return {'ok': failed == 0, 'code': code, 'attempted': attempted,
            'uploaded': uploaded, 'failed': failed, 'bytes': total_bytes}


# ---------------------------------------------------------------------------
# status / CLI
# ---------------------------------------------------------------------------


def status(runtime_data_dir=None):
    """A small JSON-able status document (fixed keys, numbers and codes)."""
    directory = data_dir(runtime_data_dir)
    configured, code = configuration_status()
    rows = _rows(directory) if directory.is_dir() else []
    pending = archived = failed = 0
    if directory.is_dir():
        for attempt_id in _attempt_ids(directory):
            info = _attempt_info(directory, attempt_id, rows=rows)
            if info['status'] in _ENDED:
                if info['archived'] and not _has_files(spool_dir(directory, attempt_id)):
                    archived += 1
                else:
                    pending += 1
            if _archive_state(directory, attempt_id).get('last_error'):
                failed += 1
    return {
        'configured': configured,
        'config': code,
        'status': code,
        'pending': pending,
        'archived': archived,
        'failed': failed,
        'locked': not lock_is_free(directory) if directory.is_dir() else False,
    }


def launch_pending(runtime_data_dir=None):
    """Start the detached pending uploader; never raises.

    R2 is unconfigured when the env file does not exist: only its existence is
    checked here, its contents are neither read nor echoed.  A missing file
    means no process is started, so the timer and the corner are unaffected.

    The total-attempt cap is applied first, even when R2 is unconfigured or
    the env file is absent.  That part does not send anything; it only
    reclaims the spool of the oldest unsent attempts.
    """
    try:
        directory = data_dir(runtime_data_dir)
        enforce_cap(directory)
        path = env_path()
        if not path.is_file():
            return False
        args = [
            sys.executable, '-m', 'docich.hanjuku_speedrun.rta.archive',
            '--data-dir', str(directory), 'upload', '--pending',
        ]
        env = dict(os.environ)
        env[DATA_DIR_ENV] = str(directory)
        source_root = Path(__file__).resolve().parents[3]
        existing = env.get('PYTHONPATH')
        env['PYTHONPATH'] = (f'{source_root}{os.pathsep}{existing}'
                             if existing else str(source_root))
        subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
            env=env,
        )
        return True
    except Exception:
        # The bot's observation path must never fail because of a launcher.
        return False


def _cli_upload(args):
    directory = data_dir(args.data_dir)
    if args.pending == bool(args.attempt):
        print('archive: error invalid_arguments', file=sys.stderr)
        return 2
    if args.attempt:
        if not directory.is_dir():
            print('archive: no_data')
            return 0
        fd = _acquire_lock(directory)
        if fd is None:
            print('archive: locked')
            return 0
        try:
            config, code = load_config()
            if config is None:
                print(f'archive: {code}')
                return 0 if code == 'unconfigured' else 1
            result = upload_attempt(directory, args.attempt, config=config)
        finally:
            _release_lock(fd)
        print(f'archive: {result.code} {result.uploaded} {result.bytes_uploaded}')
        return 0 if result.ok else 1
    summary = upload_pending(directory)
    print(f'archive: {summary["code"]} {summary["attempted"]} '
          f'{summary["uploaded"]} {summary["failed"]}')
    if summary['code'] in {'no_data', 'locked', 'unconfigured'}:
        return 0
    return 0 if summary['ok'] else 1


def main(argv=None):
    parser = argparse.ArgumentParser(prog='archive')
    parser.add_argument('--data-dir', default=None,
                        help='speedrun data directory or runtime directory')
    commands = parser.add_subparsers(dest='command', required=True)
    upload = commands.add_parser('upload')
    upload.add_argument('--attempt', default=None)
    upload.add_argument('--pending', action='store_true')
    commands.add_parser('status')
    args = parser.parse_args(argv)
    try:
        if args.command == 'upload':
            return _cli_upload(args)
        if args.command == 'status':
            print(json.dumps(status(args.data_dir), ensure_ascii=False,
                             sort_keys=True, separators=(',', ':')))
            return 0
    except Exception:
        # Never expose a traceback or a value; the log gets one fixed code.
        print('archive: internal_error', file=sys.stderr)
        return 1
    return 2


if __name__ == '__main__':  # pragma: no cover
    raise SystemExit(main())
