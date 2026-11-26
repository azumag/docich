"""Spool the speedrun's decision/event logs and images inside its attempt.

During an attempt the generated speedrun bot keeps writing the same
``hanjuku_decisions.jsonl`` / ``hanjuku_events.jsonl`` files as the original
corner, rotating each at 4 MiB with one ``.previous`` generation.  A long
speedrun (days) would therefore lose the beginning of the attempt.  This
module copies every rotated ``.previous`` into the attempt's spool before the
next rotation overwrites it, compresses it off the bot's hot path, and keeps
the spool under a hard byte cap.

Layout (``runtime`` is ``run-soren-live/runtimes/<runtime_id>``)::

    run-soren-live/speedrun/hanjuku-speedrun/attempts/<attempt_id>/
        spool/
            decisions-0001.jsonl[.gz]
            events-0001.jsonl[.gz]
            trace.jsonl
            trace-0001.jsonl[.gz]
            frames/frame-00001.png, split-03-000012345678.png
            dropped.json
        segments.json
        manifest.json

Only ``hanjuku_decisions`` and ``hanjuku_events`` rotations are collected;
the other rotated logs (narration, commentary, chart history, month audit,
...) keep the original overwrite behaviour.

A periodic image bucket that cannot fit under the cap is marked once with a
hidden ``frames/.frame-NNNNN.skip`` file, so later observations of the same
bucket do not copy and immediately delete the image again.  Hidden files are
not part of the inventory or the byte total.

Known limitation: the runtime's ``hanjuku_decisions`` / ``hanjuku_events``
logs are shared by every attempt that runs in that runtime.  If a second
attempt starts in the same runtime before the first attempt's teardown, the
collected runtime logs may contain lines from both attempts.  Finalization
never guesses which lines belong to which attempt.

Every failure is swallowed: logging must never hold up an observation or a
game input.  At most one short line goes to stderr.  Nothing here uses the
network or adds a dependency outside the standard library.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time

from . import store, timer as timer_mod

GAME = store.GAME
ATTEMPTS_DIR = 'attempts'
SPOOL_DIR = 'spool'
FRAMES_DIR = 'frames'
DROPPED_FILE = 'dropped.json'
TRACE_FILE = 'trace.jsonl'

#: Default hard cap for one attempt's spool (1 GiB).  Overridable with
#: ``DOCICH_SPEEDRUN_SPOOL_MAX_BYTES`` (a positive integer).
DEFAULT_MAX_BYTES = 1024 * 1024 * 1024
MAX_COMPONENT_BYTES = 128
MAX_JSON_BYTES = 4 * 1024 * 1024
MAX_RING_FRAMES = 24
MAX_RING_BYTES = 8 * 1024 * 1024
MAX_FRAME_BYTES = 512 * 1024
GZIP_LOCK_SUFFIX = '.gz.lock'
#: Rotated logs that belong to the attempt spool.  Every other rotated log
#: keeps the original overwrite behaviour.
ROTATED_LOG_NAMES = frozenset({'hanjuku_decisions', 'hanjuku_events'})
_IMAGE_SUFFIXES = frozenset({'.png', '.jpg', '.jpeg', '.webp'})
_SEQ_PATTERN = '^{base}-(\\d{{4,}})\\.jsonl(?:\\.gz)?$'
_SEQUENCE_RE = re.compile(
    r'(?:^|/)(?P<base>decisions|events|trace)-(?P<seq>\d{4,})'
    r'\.jsonl(?:\.gz)?$')


class SpoolError(Exception):
    """A fixed-code spool failure; callers treat it as 'skip this run'."""


# ---------------------------------------------------------------------------
# paths and validation
# ---------------------------------------------------------------------------


def _component(value, label):
    """A single safe path component (no traversal, separators or NUL)."""
    if not isinstance(value, str):
        raise SpoolError(f'invalid_{label}')
    if not value or len(value.encode('utf-8', 'replace')) > MAX_COMPONENT_BYTES:
        raise SpoolError(f'invalid_{label}')
    if value in {'.', '..'} or '\x00' in value:
        raise SpoolError(f'invalid_{label}')
    if '/' in value or '\\' in value:
        raise SpoolError(f'invalid_{label}')
    try:
        if Path(value).name != value:
            raise SpoolError(f'invalid_{label}')
    except (TypeError, ValueError):
        raise SpoolError(f'invalid_{label}') from None
    return value


def attempt_dir(runtime, attempt_id):
    """``<data_dir>/attempts/<attempt_id>`` for a runtime directory."""
    return attempt_dir_in(store.data_dir(runtime), attempt_id)


def attempt_dir_in(directory, attempt_id):
    """``<speedrun data dir>/attempts/<attempt_id>``.

    The archive job addresses the attempt tree through the data directory so
    it can still work after the runtime directory is gone.
    """
    attempt_id = _component(attempt_id, 'attempt_id')
    return Path(directory) / ATTEMPTS_DIR / attempt_id


def spool_dir(runtime, attempt_id):
    """The per-attempt spool directory."""
    return spool_dir_in(store.data_dir(runtime), attempt_id)


def spool_dir_in(directory, attempt_id):
    return attempt_dir_in(directory, attempt_id) / SPOOL_DIR


def frames_dir(runtime, attempt_id):
    return frames_dir_in(store.data_dir(runtime), attempt_id)


def frames_dir_in(directory, attempt_id):
    return spool_dir_in(directory, attempt_id) / FRAMES_DIR


def dropped_path(runtime, attempt_id):
    return dropped_path_in(store.data_dir(runtime), attempt_id)


def dropped_path_in(directory, attempt_id):
    return spool_dir_in(directory, attempt_id) / DROPPED_FILE


def _ensure_dir(path):
    """Create a directory without ever following a symlink in its place.

    The attempt tree (``attempts/``, ``<attempt_id>/`` and everything below
    it) is rejected component by component.  A symlinked ``attempts`` or
    attempt directory must not redirect a write outside the attempt.
    """
    path = Path(path)
    parts = path.parts
    if ATTEMPTS_DIR in parts:
        index = len(parts) - 1 - parts[::-1].index(ATTEMPTS_DIR)
        current = Path(*parts[:index + 1])
        if current.is_symlink():
            raise SpoolError('unsafe_directory')
        for part in parts[index + 1:]:
            current = current / part
            if current.is_symlink():
                raise SpoolError('unsafe_directory')
    if path.is_symlink():
        raise SpoolError('unsafe_directory')
    path.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or not path.is_dir():
        raise SpoolError('unsafe_directory')
    return path


def _regular(path):
    """``lstat`` result for a regular file, or ``None`` (never follows links)."""
    try:
        meta = os.lstat(path)
    except (OSError, TypeError, ValueError):
        return None
    if not stat.S_ISREG(meta.st_mode):
        return None
    return meta


def _unlink_regular(path):
    """Remove one regular file; symlinks/directories are left alone."""
    if _regular(path) is None:
        return None
    try:
        meta = os.lstat(path)
        os.unlink(path)
    except OSError:
        return None
    return meta.st_size


def read_json(path, *, limit=MAX_JSON_BYTES):
    """Read a bounded, regular JSON document or return ``None``."""
    path = Path(path)
    meta = _regular(path)
    if meta is None or meta.st_size > limit:
        return None
    try:
        raw = path.read_text(encoding='utf-8')
    except (OSError, UnicodeError):
        return None
    try:
        document = json.loads(raw)
    except ValueError:
        return None
    return document if isinstance(document, dict) else None


def write_json(path, document, *, fsync=True):
    """Atomically replace ``path`` with one compact JSON document."""
    path = Path(path)
    _ensure_dir(path.parent)
    data = json.dumps(document, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':')).encode('utf-8')
    fd, temporary = tempfile.mkstemp(prefix=f'.{path.name}.', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            if fsync:
                os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# sequence naming
# ---------------------------------------------------------------------------


def _log_base(name):
    base = Path(str(name)).name
    if base.endswith('.jsonl'):
        base = base[:-len('.jsonl')]
    if base.startswith('hanjuku_'):
        base = base[len('hanjuku_'):]
    return base or 'log'


def _stored_max_sequence(directory, base):
    """Highest sequence for ``base`` recorded in ``dropped.json``."""
    document = read_json(Path(directory) / DROPPED_FILE)
    values = document.get('max_sequence') if isinstance(document, dict) else None
    value = values.get(base) if isinstance(values, dict) else None
    return value if type(value) is int and value > 0 else 0


def _next_sequence(directory, base):
    pattern = re.compile(_SEQ_PATTERN.format(base=re.escape(base)))
    highest = _stored_max_sequence(directory, base)
    try:
        with os.scandir(directory) as entries:
            for entry in entries:
                match = pattern.match(entry.name)
                if match is not None:
                    highest = max(highest, int(match.group(1)))
    except OSError:
        pass
    return highest + 1


def reserve_path(directory, base):
    """Reserve the next ``<base>-NNNN.jsonl`` name without overwriting one.

    The empty reservation file is created with ``O_EXCL``; the caller renames
    the real file over it.  A crash before the rename leaves a harmless empty
    file at the end of the sequence.
    """
    directory = Path(directory)
    sequence = _next_sequence(directory, base)
    for _attempt in range(10000):
        target = directory / f'{base}-{sequence:04d}.jsonl'
        try:
            fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            sequence += 1
            continue
        os.close(fd)
        return target
    raise SpoolError('sequence_exhausted')


def _move_into_spool(source, directory, base):
    """Atomically move one rotated log to its next spool name."""
    target = reserve_path(directory, base)
    try:
        os.replace(source, target)
    except OSError:
        _unlink_regular(target)
        raise
    return target


# ---------------------------------------------------------------------------
# detached compression
# ---------------------------------------------------------------------------


def _read_lock_pid(lock):
    try:
        raw = lock.read_text(encoding='utf-8').strip()
        return int(raw)
    except (OSError, ValueError):
        return None


def _pid_is_zombie(pid):
    """True when ``/proc/<pid>/stat`` reports a zombie/dead process.

    A detached ``gzip`` is often reaped only when its Python parent gets
    around to it, so ``kill(pid, 0)`` succeeds for a zombie.  Treating that
    as alive makes a stale lock wait forever; ``Z`` means the work is over.
    """
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        raw = Path(f'/proc/{pid}/stat').read_text(
            encoding='utf-8', errors='replace')
    except OSError:
        return False
    try:
        state = raw.rsplit(')', 1)[1].strip()[:1]
    except IndexError:
        return False
    return state in {'Z', 'X', 'x'}


def _pid_alive(pid):
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return not _pid_is_zombie(pid)
    except OSError:
        return False
    return not _pid_is_zombie(pid)


def _release_lock(lock):
    try:
        os.unlink(lock)
    except OSError:
        pass


def _acquire_lock(lock):
    """``O_EXCL`` lock; a lock whose process is gone is taken over once."""
    for _attempt in range(2):
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pid = _read_lock_pid(lock)
            if _pid_alive(pid):
                return False
            _release_lock(lock)
            continue
        except OSError:
            return False
        try:
            os.write(fd, str(os.getpid()).encode('ascii'))
        finally:
            os.close(fd)
        return True
    return False


def compress_detached(path):
    """Start ``nice -n 19 gzip -f <path>`` in its own session; do not wait.

    Returns ``True`` only when a process was started.  A missing ``nice`` or
    ``gzip``, an existing ``.gz``/lock, or any launch error is a silent no-op.
    """
    try:
        path = Path(path)
        if path.suffix == '.gz':
            return False
        if _regular(path) is None:
            return False
        compressed = path.with_name(path.name + '.gz')
        lock = path.with_name(path.name + GZIP_LOCK_SUFFIX)
        if os.path.lexists(compressed):
            # A finished compression leaves its now-stale lock behind.
            _release_lock(lock)
            return False
        nice = shutil.which('nice')
        gzip = shutil.which('gzip')
        if not nice or not gzip:
            return False
        if not _acquire_lock(lock):
            return False
        try:
            process = subprocess.Popen(
                ['nice', '-n', '19', 'gzip', '-f', str(path)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                close_fds=True,
            )
        except (OSError, ValueError):
            _release_lock(lock)
            return False
        # The lock names the process that actually owns the file.  A guard or
        # an operator can then tell "gzip is still running" from "the launcher
        # died" without touching the compressed data.
        try:
            lock.write_text(str(process.pid), encoding='ascii')
        except (OSError, AttributeError):
            _release_lock(lock)
            return False
        return True
    except Exception as exc:  # never reach the bot's input path
        print(f'rta: spool compress skipped: {type(exc).__name__}', file=sys.stderr)
        return False


def _reap_stale_locks(directory):
    """Drop locks whose input is gone and whose ``.gz`` already exists.

    A gzip process that finished leaves ``<name>.jsonl.gz`` plus its lock.
    The source name in the lock is ``<name>.jsonl``; when that source no
    longer exists the lock is stale and is removed here.
    """
    try:
        with os.scandir(directory) as entries:
            for entry in entries:
                if not entry.name.endswith(GZIP_LOCK_SUFFIX):
                    continue
                if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                    continue
                source_name = entry.name[:-len(GZIP_LOCK_SUFFIX)]
                source = Path(directory) / source_name
                compressed = Path(directory) / f'{source_name}.gz'
                if os.path.lexists(compressed) and not os.path.lexists(source):
                    try:
                        os.unlink(entry.path)
                    except OSError:
                        continue
    except OSError:
        pass


def compress_pending(runtime, attempt_id):
    """Request compression for every uncompressed JSONL log in the spool."""
    started = 0
    try:
        directory = spool_dir(runtime, attempt_id)
        if directory.is_symlink() or not directory.is_dir():
            return 0
        _reap_stale_locks(directory)
        with os.scandir(directory) as entries:
            for entry in entries:
                if not entry.name.endswith('.jsonl'):
                    continue
                if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                    continue
                if compress_detached(directory / entry.name):
                    started += 1
    except Exception as exc:
        print(f'rta: spool compress scan skipped: {type(exc).__name__}',
              file=sys.stderr)
    return started


# ---------------------------------------------------------------------------
# cap enforcement
# ---------------------------------------------------------------------------


def max_bytes():
    raw = os.environ.get('DOCICH_SPEEDRUN_SPOOL_MAX_BYTES')
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return DEFAULT_MAX_BYTES
    return value if value > 0 else DEFAULT_MAX_BYTES


def would_fit(runtime, attempt_id, additional_bytes, *, max_bytes_value=None):
    """True when ``additional_bytes`` still fits under the spool cap.

    Runs one shallow ``os.scandir`` per call; callers use it once per image
    bucket, before copying, so a capped spool never copies-and-drops the same
    periodic image on every observation.
    """
    if type(additional_bytes) is not int or additional_bytes < 0:
        return False
    directory = spool_dir(runtime, attempt_id)
    if directory.is_symlink() or not directory.is_dir():
        return False
    cap = max_bytes_value if type(max_bytes_value) is int and max_bytes_value > 0 else max_bytes()
    total = sum(size for _rel, _path, size in _spool_entries(directory))
    return total + additional_bytes <= cap


def _spool_entries(directory):
    """Bounded recursive listing of regular files (never follows symlinks)."""
    collected = []
    try:
        stack = [Path(directory)]
        while stack:
            current = stack.pop()
            with os.scandir(current) as entries:
                for entry in entries:
                    if entry.name.endswith(GZIP_LOCK_SUFFIX) or entry.name.startswith('.'):
                        continue
                    if entry.is_symlink():
                        continue
                    relative = (current / entry.name).relative_to(directory).as_posix()
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(Path(entry.path))
                    elif entry.is_file(follow_symlinks=False):
                        try:
                            size = entry.stat(follow_symlinks=False).st_size
                        except OSError:
                            continue
                        collected.append((relative, Path(entry.path), size))
    except OSError:
        pass
    return collected


def _category(relative):
    if relative.startswith(f'{FRAMES_DIR}/'):
        return 'frames'
    name = relative.rsplit('/', 1)[-1]
    if name.startswith('trace-') and name.endswith(('.jsonl', '.jsonl.gz')):
        return 'trace'
    if name.startswith(('decisions-', 'events-')) and name.endswith(('.jsonl', '.jsonl.gz')):
        return 'logs'
    return None


def _sequence(relative):
    """``(base, sequence)`` for a rotated log name, else ``(None, 0)``."""
    match = _SEQUENCE_RE.search(relative)
    if match is None:
        return None, 0
    return match.group('base'), int(match.group('seq'))


def _drop_key(entry):
    """Oldest mtime first; ties by the numeric sequence, then by name."""
    relative, path, _size = entry
    meta = _regular(path)
    mtime = meta.st_mtime if meta is not None else 0.0
    _base, sequence = _sequence(relative)
    return (mtime, sequence, relative)


def _drop_order(entries):
    """Periodic images, other images, rotated traces, old logs, unknowns."""
    images = [(rel, path, size) for rel, path, size in entries
              if _category(rel) == 'frames' and rel.lower().endswith(tuple(_IMAGE_SUFFIXES))]
    periodic = sorted((item for item in images if Path(item[0]).name.startswith('frame-')),
                      key=lambda item: item[0])
    splits = sorted((item for item in images if not Path(item[0]).name.startswith('frame-')),
                    key=lambda item: item[0])
    traces = sorted((item for item in entries if _category(item[0]) == 'trace'),
                    key=_drop_key)
    logs = sorted((item for item in entries if _category(item[0]) == 'logs'),
                  key=_drop_key)
    # A file this module cannot classify must not stay in the spool forever.
    # The active trace and the counters file are owned by the spool and are
    # never part of the fallback.
    unknown = sorted((item for item in entries
                      if _category(item[0]) is None
                      and item[0] not in {TRACE_FILE, DROPPED_FILE}),
                     key=lambda item: item[0])
    return periodic + splits + traces + logs + unknown


def _merge_dropped(runtime, attempt_id, cap, additions, total,
                   max_sequences=None):
    path = dropped_path(runtime, attempt_id)
    document = read_json(path) or {}
    types = document.get('types') if isinstance(document.get('types'), dict) else {}
    merged = {}
    for name in ('frames', 'trace', 'logs'):
        old = types.get(name) if isinstance(types.get(name), dict) else {}
        old_files = old.get('files') if type(old.get('files')) is int else 0
        old_bytes = old.get('bytes') if type(old.get('bytes')) is int else 0
        new = additions.get(name, (0, 0))
        merged[name] = {
            'files': max(0, old_files) + new[0],
            'bytes': max(0, old_bytes) + new[1],
        }
    old_max = (document.get('max_sequence')
               if isinstance(document.get('max_sequence'), dict) else {})
    merged_max = {
        name: value for name, value in old_max.items()
        if isinstance(name, str) and type(value) is int and value > 0
    }
    for name, sequence in (max_sequences or {}).items():
        if type(sequence) is int and sequence > 0:
            merged_max[name] = max(merged_max.get(name, 0), sequence)
    document = {
        'schema': 1,
        'updated_at_ms': int(time.time() * 1000),
        'max_bytes': cap,
        'total_bytes': total,
        'over_cap': total > cap,
        'types': merged,
        'max_sequence': merged_max,
    }
    try:
        write_json(path, document)
    except Exception:
        pass
    return document


def enforce_cap(runtime, attempt_id, *, max_bytes_value=None):
    """Keep one attempt's spool under the configured cap.

    Deletion order is periodic images, remaining images, rotated traces, then
    the oldest decision/event logs (mtime first, numeric sequence on ties),
    then any unclassified leftovers.  The active ``trace.jsonl`` and
    ``dropped.json`` are never removed, and a log whose detached gzip is still
    running (its ``.gz.lock`` exists) is skipped.  Returns the number of bytes
    dropped during this call; failures are swallowed.
    """
    dropped_total = 0
    try:
        directory = spool_dir(runtime, attempt_id)
        if directory.is_symlink() or not directory.is_dir():
            return 0
        _reap_stale_locks(directory)
        cap = max_bytes_value if type(max_bytes_value) is int and max_bytes_value > 0 else max_bytes()
        entries = _spool_entries(directory)
        total = sum(size for _rel, _path, size in entries)
        if total <= cap:
            return 0
        additions = {'frames': [0, 0], 'trace': [0, 0], 'logs': [0, 0]}
        max_sequences = {}
        for relative, path, size in _drop_order(entries):
            if total <= cap:
                break
            if os.path.lexists(Path(str(path) + GZIP_LOCK_SUFFIX)):
                # gzip is reading this file right now.  Removing it would
                # leave the compressor with a vanished input and race its
                # output; the next cap pass can delete it.
                continue
            category = _category(relative)
            base, sequence = _sequence(relative)
            removed = _unlink_regular(path)
            if removed is None:
                continue
            total = max(0, total - removed)
            dropped_total += removed
            if category in additions:
                additions[category][0] += 1
                additions[category][1] += removed
            if base in {'decisions', 'events', 'trace'} and sequence > 0:
                max_sequences[base] = max(max_sequences.get(base, 0), sequence)
        _merge_dropped(
            runtime, attempt_id, cap,
            {name: (values[0], values[1]) for name, values in additions.items()},
            total,
            max_sequences,
        )
        return dropped_total
    except Exception as exc:
        print(f'rta: spool cap skipped: {type(exc).__name__}', file=sys.stderr)
        return dropped_total


# ---------------------------------------------------------------------------
# rotation hook
# ---------------------------------------------------------------------------


def before_rotate(runtime_dir, name, previous_path):
    """Move the outgoing ``.previous`` log into the running attempt's spool.

    Called by the generated ``hanjuku_run.append_log`` immediately before it
    overwrites ``.previous``.  Only a currently running attempt whose
    ``runtime_id`` names this runtime directory is collected; otherwise the
    caller keeps its old overwrite behaviour.  Never raises.
    """
    try:
        runtime = Path(runtime_dir)
        if not isinstance(name, str) or not name:
            return False
        if name not in ROTATED_LOG_NAMES:
            # Narration, commentary, chart history, month audit and every
            # other rotated log keep the original overwrite behaviour.
            return False
        # A lenient read: this runs on the hot log-rotation path and must not
        # quarantine a timer.  Only the fields needed to prove "this runtime
        # is timing right now" are required.
        timer = read_json(store.timer_path(runtime), limit=store.MAX_TIMER_BYTES)
        if not isinstance(timer, dict) or timer.get('status') != 'running':
            return False
        if timer.get('schema') not in (None, timer_mod.SCHEMA):
            return False
        if timer.get('game') not in (None, store.GAME):
            return False
        if timer.get('runtime_id') != runtime.name:
            return False
        attempt_id = timer.get('attempt_id')
        if not isinstance(attempt_id, str) or not attempt_id:
            return False
        previous = Path(previous_path)
        meta = _regular(previous)
        if meta is None:
            return False
        directory = _ensure_dir(spool_dir(runtime, attempt_id))
        target = _move_into_spool(previous, directory, _log_base(name))
        compress_detached(target)
        enforce_cap(runtime, attempt_id)
        return True
    except Exception as exc:
        print(f'rta: spool rotate skipped: {type(exc).__name__}', file=sys.stderr)
        return False


# ---------------------------------------------------------------------------
# bounded image copies
# ---------------------------------------------------------------------------


def _image_suffix(screenshot):
    try:
        suffix = Path(screenshot).suffix.lower()
    except (TypeError, ValueError):
        suffix = ''
    return suffix if suffix in _IMAGE_SUFFIXES else '.png'


def copy_image(source, target, *, limit=MAX_FRAME_BYTES):
    """Copy one bounded regular image atomically; symlinks are not followed."""
    source = Path(source)
    meta = _regular(source)
    if meta is None or meta.st_size <= 0 or meta.st_size > limit:
        return False
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
    try:
        fd = os.open(source, flags)
    except OSError:
        return False
    try:
        with os.fdopen(fd, 'rb') as stream:
            data = stream.read(limit + 1)
    except OSError:
        return False
    if len(data) > limit:
        return False
    target = Path(target)
    try:
        _ensure_dir(target.parent)
        fd, temporary = tempfile.mkstemp(prefix=f'.{target.name}.', dir=str(target.parent))
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(data)
                stream.flush()
            os.replace(temporary, target)
        except BaseException:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise
    except Exception:
        return False
    return True


def _copy_to_frames(runtime, attempt_id, source, name, *, limit=MAX_FRAME_BYTES):
    directory = frames_dir(runtime, attempt_id)
    if directory.is_symlink():
        return False
    target = directory / name
    if os.path.lexists(target):
        return False
    return copy_image(source, target, limit=limit)


# ---------------------------------------------------------------------------
# attempt finalization
# ---------------------------------------------------------------------------


def _collect_runtime_logs(runtime, directory):
    """Move the attempt's remaining decision/event logs into the spool."""
    moved = 0
    for name in ('hanjuku_decisions', 'hanjuku_events'):
        for suffix in ('.previous.jsonl', '.jsonl'):
            source = Path(runtime) / f'{name}{suffix}'
            if _regular(source) is None:
                continue
            try:
                _move_into_spool(source, directory, _log_base(name))
                moved += 1
            except OSError:
                continue
    return moved


def _collect_frames(runtime, attempt_id, directory):
    """Copy the newest bounded slice of the runtime's frame ring."""
    source_dir = Path(runtime) / 'hanjuku_frames'
    if source_dir.is_symlink() or not source_dir.is_dir():
        return 0
    try:
        with os.scandir(source_dir) as entries:
            candidates = []
            for entry in entries:
                if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                    continue
                if Path(entry.name).suffix.lower() not in _IMAGE_SUFFIXES:
                    continue
                if not entry.name.startswith('frame-'):
                    continue
                try:
                    mtime = entry.stat(follow_symlinks=False).st_mtime
                except OSError:
                    continue
                candidates.append((mtime, entry.name, Path(entry.path)))
    except OSError:
        return 0
    # The frame ring wraps its names, so recency comes from the mtime.
    candidates.sort(key=lambda item: (item[0], item[1]))
    directory = Path(directory)
    copied = 0
    total = 0
    for _mtime, _name, source in candidates[-MAX_RING_FRAMES:]:
        meta = _regular(source)
        if meta is None or meta.st_size > MAX_FRAME_BYTES:
            continue
        if total + meta.st_size > MAX_RING_BYTES:
            continue
        target = directory / f'ring-{source.name}'
        if os.path.lexists(target):
            continue
        if not copy_image(source, target):
            continue
        total += meta.st_size
        copied += 1
    return copied


def spool_files(runtime, attempt_id):
    """Relative path/size inventory of the spool (manifest input)."""
    return spool_files_in(store.data_dir(runtime), attempt_id)


def spool_files_in(directory, attempt_id):
    """Relative path/size inventory of a spool addressed by data directory."""
    path = spool_dir_in(directory, attempt_id)
    if path.is_symlink() or not path.is_dir():
        return [], 0
    files = []
    total = 0
    for relative, _path, size in _spool_entries(path):
        files.append({'path': relative, 'bytes': size})
        total += size
    files.sort(key=lambda item: item['path'])
    return files, total


def finalize_logs(runtime, attempt_id):
    """Collect the finished attempt's logs and write its summary files.

    Runs after ``store.finalize_attempt`` from the common corner teardown.
    Moving the runtime's own logs (rather than copying) avoids leaving a
    second copy behind; the runtime directory itself is never deleted here.
    A failure is reported once on stderr and never stops the corner teardown.
    """
    try:
        runtime = Path(runtime)
        attempt_id = _component(attempt_id, 'attempt_id')
        directory = _ensure_dir(spool_dir(runtime, attempt_id))
        _collect_runtime_logs(runtime, directory)
        _collect_frames(runtime, attempt_id, frames_dir(runtime, attempt_id))
        # Enforce the cap before the summary so the manifest describes what
        # actually remains, and before compression so a dropped file cannot
        # race a running gzip.
        enforce_cap(runtime, attempt_id)
        from . import report_segments
        report_segments.build_segments(runtime, attempt_id)
        from . import manifest
        manifest.build_manifest(runtime, attempt_id)
        compress_pending(runtime, attempt_id)
        return True
    except Exception as exc:
        print(f'rta: spool finalize skipped: {type(exc).__name__}: {str(exc)[:160]}',
              file=sys.stderr)
        return False
