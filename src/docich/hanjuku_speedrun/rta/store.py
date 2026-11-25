"""Bounded, atomic file storage for the hanjuku-speedrun RTA records.

Layout (``runtime`` is ``run-soren-live/runtimes/<runtime_id>``)::

    run-soren-live/speedrun/hanjuku-speedrun/
        timer.json
        records.json
        attempts.jsonl

Every write goes to a temporary file in the same directory and is renamed
over the old file, so a crash during a write leaves the previous readable
file in place.  A file that cannot be parsed is moved aside as
``<name>.corrupt-<epoch_ms>`` (never deleted) and the caller gets the empty
document so recording continues.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import time

try:  # pragma: no cover - Windows has no fcntl; the VM (Linux) does.
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

from . import timer as timer_mod

GAME = timer_mod.GAME
DATA_PARTS = ('speedrun', 'hanjuku-speedrun')
#: Optional overrides for a local (non-production) run.  ``DATA_DIR`` makes
#: the bot and the archive CLI agree on one directory even when the runtime
#: directory lives outside the production tree.  ``ENV`` labels the records so
#: a local PB is never mistaken for a production one.
DATA_DIR_ENV = 'DOCICH_SPEEDRUN_DATA_DIR'
ENVIRONMENT_ENV = 'DOCICH_SPEEDRUN_ENV'
ENVIRONMENTS = ('soren', 'local')
DEFAULT_ENVIRONMENT = 'soren'
TIMER_FILE = 'timer.json'
RECORDS_FILE = 'records.json'
ATTEMPTS_FILE = 'attempts.jsonl'
ATTEMPTS_LOCK_FILE = 'attempts.jsonl.lock'
RUN_FILE = 'hanjuku_run.json'

#: The attempts history is read-modify-written by two processes (the bot and
#: the detached archiver).  The lock wait is deliberately short: a contended
#: write is skipped, never allowed to stall the bot's observation loop.
LOCK_WAIT_SECONDS = 5.0
LOCK_POLL_SECONDS = 0.02

#: Every file has a hard read/write bound.  The timer is rewritten on every
#: observation (about 1.5 s) and must stay a few hundred bytes.
MAX_TIMER_BYTES = 64 * 1024
MAX_RECORDS_BYTES = 256 * 1024
MAX_ATTEMPTS_BYTES = 4 * 1024 * 1024
MAX_ATTEMPT_LINE_BYTES = 16 * 1024
MAX_ATTEMPT_ROWS = 2000
MAX_RUN_BYTES = 256 * 1024


class StoreError(Exception):
    """A fixed-code storage failure; the caller must not retry blindly."""


def environment():
    """The record environment: ``soren`` (production) or ``local``.

    The value is written into the timer, the attempt rows and the manifest so
    a local trial can never be compared with (or mistaken for) the production
    PB.  Unknown values fall back to the production label instead of being
    copied into the records.
    """
    raw = os.environ.get(ENVIRONMENT_ENV)
    return raw if raw in ENVIRONMENTS else DEFAULT_ENVIRONMENT


def data_dir(runtime):
    """``<runtime parent>/speedrun/hanjuku-speedrun`` for a runtime dir.

    ``DOCICH_SPEEDRUN_DATA_DIR`` overrides the location (the local driver uses
    it to keep everything under ``run-local/speedrun``).  The default is
    unchanged, so production records keep their existing path.
    """
    raw = os.environ.get(DATA_DIR_ENV)
    if raw:
        return Path(raw).expanduser()
    runtime = Path(runtime)
    return runtime.parent.parent.joinpath(*DATA_PARTS)


def timer_path(runtime):
    return data_dir(runtime) / TIMER_FILE


def records_path(runtime):
    return data_dir(runtime) / RECORDS_FILE


def attempts_path(runtime):
    return data_dir(runtime) / ATTEMPTS_FILE


# ---------------------------------------------------------------------------
# primitive read/write
# ---------------------------------------------------------------------------


def _quarantine(path):
    """Move a bad file aside without deleting its bytes."""
    stamp = int(time.time() * 1000)
    target = path.with_name(f'{path.name}.corrupt-{stamp}')
    counter = 1
    while target.exists():
        target = path.with_name(f'{path.name}.corrupt-{stamp}-{counter}')
        counter += 1
    try:
        os.replace(path, target)
    except OSError:
        return target
    print(f'rta: quarantined {target}', file=sys.stderr)
    return target


def _read_json(path, limit, *, quarantine=True):
    """Read one JSON document with a size/regular-file gate.

    Only out-of-contract or unparseable content is quarantined.  An
    ``OSError`` is a transient or permission problem, not evidence that the
    document is bad: it propagates so the caller can skip this observation
    instead of discarding the file and losing the records it still holds.
    """
    try:
        meta = os.lstat(path)
    except FileNotFoundError:
        return None
    if not os.path.isfile(path) or os.path.islink(path) or meta.st_size > limit:
        if quarantine:
            _quarantine(path)
        return None
    try:
        raw = path.read_text(encoding='utf-8')
    except UnicodeError:
        if quarantine:
            _quarantine(path)
        return None
    try:
        document = json.loads(raw)
    except (ValueError, UnicodeError):
        if quarantine:
            _quarantine(path)
        return None
    return document


def _atomic_write(path, data, *, fsync=True):
    path.parent.mkdir(parents=True, exist_ok=True)
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


def _dump(document):
    return json.dumps(document, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':')).encode('utf-8')


def save_timer(runtime, timer):
    data = _dump(timer)
    if len(data) > MAX_TIMER_BYTES:
        raise StoreError('timer_too_large')
    # The timer is a display cache rewritten every observation.  A crash that
    # loses the newest copy only costs one heartbeat (the next observation
    # rewrites it); durable PB/history records keep their fsync.
    _atomic_write(timer_path(runtime), data, fsync=False)


def save_records(runtime, records):
    data = _dump(records)
    if len(data) > MAX_RECORDS_BYTES:
        raise StoreError('records_too_large')
    _atomic_write(records_path(runtime), data)


# ---------------------------------------------------------------------------
# documents
# ---------------------------------------------------------------------------


def load_timer(runtime):
    raw = _read_json(timer_path(runtime), MAX_TIMER_BYTES)
    if not isinstance(raw, dict):
        # JSON ``null`` and an empty file are not a missing timer.
        if timer_path(runtime).exists():
            _quarantine(timer_path(runtime))
        return None
    if (raw.get('schema') != timer_mod.SCHEMA or raw.get('game') != GAME
            or raw.get('status') not in timer_mod.STATUSES
            or not isinstance(raw.get('runtime_id'), str)):
        _quarantine(timer_path(runtime))
        return None
    if not isinstance(raw.get('splits'), list):
        raw['splits'] = []
    if not isinstance(raw.get('comparison'), dict):
        raw['comparison'] = {}
    return raw


def load_records(runtime):
    path = records_path(runtime)
    existed = path.exists()
    raw = _read_json(path, MAX_RECORDS_BYTES)
    if raw is None:
        if path.exists():
            # Parsed as JSON ``null``: a corrupt document the reader accepted.
            _quarantine(path)
            return _rebuild_records(runtime)
        if existed:
            # The reader quarantined a malformed/oversized file; recover what
            # the append-only history still knows.
            return _rebuild_records(runtime)
        return timer_mod.default_records()
    if not isinstance(raw, dict) or raw.get('game') not in (None, GAME):
        _quarantine(path)
        return _rebuild_records(runtime)
    return timer_mod.normalize_records(raw)


def _rebuild_records(runtime):
    """Best-effort records recovery from ``attempts.jsonl``.

    Only the append-only history survives a corrupt ``records.json``.  The
    rebuilt document is written back with ``rebuilt: true`` so a later reader
    can tell it apart from a normal one.
    """
    records = timer_mod.rebuild_records(
        load_attempts(runtime), now_ms=int(time.time() * 1000))
    records['rebuilt'] = True
    save_records(runtime, records)
    return records


def load_run(runtime):
    """Read ``hanjuku_run.json``; never quarantine or modify it."""
    raw = _read_json(Path(runtime) / RUN_FILE, MAX_RUN_BYTES, quarantine=False)
    return raw if isinstance(raw, dict) else {}


def load_attempts(runtime):
    return load_attempts_in(data_dir(runtime))


def _attempts_lock_path(directory):
    return Path(directory) / ATTEMPTS_LOCK_FILE


def _acquire_attempts_lock(directory):
    """Short, bounded ``flock`` for one attempts-history read-modify-write."""
    if fcntl is None:  # pragma: no cover - the VM is Linux
        return 0
    path = _attempts_lock_path(directory)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    except OSError:
        return None
    deadline = time.monotonic() + LOCK_WAIT_SECONDS
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except OSError:
            if time.monotonic() >= deadline:
                try:
                    os.close(fd)
                except OSError:
                    pass
                return None
            time.sleep(LOCK_POLL_SECONDS)


def _release_attempts_lock(fd):
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


def _read_attempts(path):
    """Return ``(rows, needs_repair)`` without modifying the file."""
    try:
        meta = os.lstat(path)
    except FileNotFoundError:
        return [], False
    if not os.path.isfile(path) or os.path.islink(path) or meta.st_size > MAX_ATTEMPTS_BYTES:
        return [], True
    rows = []
    damaged = False
    try:
        with path.open('r', encoding='utf-8') as stream:
            for line in stream:
                if not line.strip():
                    continue
                if len(line.encode('utf-8')) > MAX_ATTEMPT_LINE_BYTES:
                    damaged = True
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    damaged = True
                    continue
                if not isinstance(row, dict):
                    damaged = True
                    continue
                rows.append(row)
    except UnicodeError:
        return [], True
    return rows, damaged


def _load_attempts_locked(directory):
    """Read (and, if needed, repair) the history while the lock is held."""
    path = Path(directory) / ATTEMPTS_FILE
    rows, needs_repair = _read_attempts(path)
    if needs_repair:
        # Keep the original bytes and rewrite only the readable rows.
        _quarantine(path)
        _write_attempts(path, rows)
    return rows


def load_attempts_in(directory):
    """Read ``attempts.jsonl`` from a speedrun data directory.

    ``directory`` is the ``speedrun/hanjuku-speedrun`` directory (the same
    directory :func:`data_dir` returns for a runtime).  The archive job runs
    from a detached process which may not know a runtime directory (or whose
    runtime directory was already removed), so it addresses the history file
    directly rather than through a synthetic runtime.

    A normal read needs no lock: writes replace the file atomically, so a
    reader always sees a complete generation.  A damaged file is repaired
    under the same lock writers use, and is left untouched (readable rows are
    still returned) when the lock cannot be taken in time.
    """
    path = Path(directory) / ATTEMPTS_FILE
    rows, needs_repair = _read_attempts(path)
    if not needs_repair:
        return rows
    fd = _acquire_attempts_lock(directory)
    if fd is None:
        return rows
    try:
        return _load_attempts_locked(directory)
    finally:
        _release_attempts_lock(fd)


def _row_line(row):
    """Serialize one bounded attempt row; oversized text is truncated."""
    row = dict(row)
    for key in ('bot_version', 'chart_edition', 'reason', 'dnf_reason',
                'total_verdict', 'attempt_id', 'runtime_id'):
        if isinstance(row.get(key), str):
            row[key] = row[key][:160]
    line = json.dumps(row, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':')).encode('utf-8')
    if len(line) <= MAX_ATTEMPT_LINE_BYTES:
        return line
    # The 12 segments and their splits are the only large fields.  Drop the
    # duplicate split list first; the arrays still describe the attempt.
    row.pop('splits', None)
    line = json.dumps(row, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':')).encode('utf-8')
    if len(line) <= MAX_ATTEMPT_LINE_BYTES:
        return line
    # Last resort: keep a valid, minimal row.  A row this large means an
    # upstream contract violation, not a normal attempt.
    minimal = {
        'schema': row.get('schema'),
        'game': row.get('game'),
        'runtime_id': row.get('runtime_id'),
        'attempt_id': row.get('attempt_id'),
        'attempt_no': row.get('attempt_no'),
        'status': row.get('status'),
        'result': row.get('result'),
        'started_at_ms': row.get('started_at_ms'),
        'ended_at_ms': row.get('ended_at_ms'),
        'total_ms': row.get('total_ms'),
        'truncated': True,
    }
    return json.dumps(minimal, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':')).encode('utf-8')


def _bounded_rows(rows):
    """Newest rows that fit both the row count and the byte bound."""
    lines = [_row_line(row) for row in rows if isinstance(row, dict)]
    selected = []
    size = 0
    for line in reversed(lines):
        if len(selected) >= MAX_ATTEMPT_ROWS:
            break
        added = len(line) + 1
        if size + added > MAX_ATTEMPTS_BYTES:
            break
        selected.append(line)
        size += added
    selected.reverse()
    return selected


def _write_attempts(path, rows):
    lines = _bounded_rows(rows)
    data = b''.join(line + b'\n' for line in lines)
    _atomic_write(path, data)


def append_attempt(runtime, row):
    """Append one attempt row; a repeated ``attempt_id`` is a no-op.

    The whole (bounded) JSONL file is rewritten through a temporary file, so
    an interrupted write leaves the previous complete file.  The read and the
    rewrite happen under ``attempts.jsonl.lock``; a lock that cannot be taken
    within :data:`LOCK_WAIT_SECONDS` skips this write instead of blocking the
    bot.  The newest rows win when the byte bound is reached.
    """
    if not isinstance(row, dict):
        raise StoreError('invalid_attempt')
    attempt_id = row.get('attempt_id')
    if not isinstance(attempt_id, str) or not attempt_id:
        raise StoreError('invalid_attempt_id')
    directory = data_dir(runtime)
    fd = _acquire_attempts_lock(directory)
    if fd is None:
        print('rta: attempts lock timeout', file=sys.stderr)
        return False
    try:
        path = Path(directory) / ATTEMPTS_FILE
        rows = [existing for existing in _load_attempts_locked(directory)
                if isinstance(existing, dict)
                and existing.get('attempt_id') != attempt_id]
        rows.append(row)
        _write_attempts(path, rows)
    finally:
        _release_attempts_lock(fd)
    return True


def update_attempt_in(directory, attempt_id, updates):
    """Merge ``updates`` into the row named by ``attempt_id``.

    The whole bounded JSONL file is rewritten atomically, exactly like
    :func:`append_attempt`; row order is preserved.  Returns ``True`` when a
    row changed.  A missing attempt id is a no-op.  The read-modify-write is
    serialised with :func:`append_attempt` through ``attempts.jsonl.lock``;
    a lock timeout skips the update and returns ``False``.
    """
    if not isinstance(attempt_id, str) or not attempt_id:
        raise StoreError('invalid_attempt_id')
    if not isinstance(updates, dict):
        raise StoreError('invalid_attempt')
    fd = _acquire_attempts_lock(directory)
    if fd is None:
        print('rta: attempts lock timeout', file=sys.stderr)
        return False
    try:
        path = Path(directory) / ATTEMPTS_FILE
        rows = _load_attempts_locked(directory)
        changed = False
        updated_rows = []
        for row in rows:
            if not isinstance(row, dict) or row.get('attempt_id') != attempt_id:
                updated_rows.append(row)
                continue
            merged = {**row, **updates}
            if merged != row:
                changed = True
            updated_rows.append(merged)
        if changed:
            _write_attempts(path, updated_rows)
        return changed
    finally:
        _release_attempts_lock(fd)


# ---------------------------------------------------------------------------
# attempt row and finalization
# ---------------------------------------------------------------------------


def attempt_row(timer, *, at_ms=None):
    """The one-line JSONL record for a finished/DNF attempt."""
    status = timer.get('status')
    if status not in {'finished', 'dnf'}:
        raise StoreError('attempt_not_ended')
    summary = timer_mod.attempt_summary(timer)
    row = {
        'schema': timer_mod.SCHEMA,
        'game': GAME,
        'runtime_id': timer.get('runtime_id'),
        'generation': timer.get('generation'),
        'attempt_id': timer.get('attempt_id'),
        'attempt_no': timer.get('attempt_no'),
        'status': status,
        'result': status,
        'started_at_ms': timer.get('started_at_ms'),
        'ended_at_ms': timer.get('ended_at_ms'),
        'start_source': timer.get('start_source'),
        'name_seen_at_ms': timer.get('name_seen_at_ms'),
        'total_ms': timer.get('total_ms'),
        'current_index': timer.get('current_index'),
        'segments_ms': summary['segments_ms'],
        'cumulative_ms': summary['cumulative_ms'],
        'merged_ranges': summary['merged_ranges'],
        'splits': timer.get('splits') or [],
        'total_verdict': timer.get('total_verdict'),
        'pb_updated': bool(timer.get('pb_updated')),
        'pb_attempt_id': timer.get('pb_attempt_id'),
        'pb_eligible': timer.get('pb_eligible'),
        'dnf_reason': timer.get('dnf_reason'),
        'reason': timer.get('reason'),
        'bot_version': timer.get('bot_version'),
        'chart_edition': timer.get('chart_edition'),
        'env': (timer.get('env') if timer.get('env') in ENVIRONMENTS
                else environment()),
        'archived': False,
        'at_ms': at_ms if type(at_ms) is int else timer.get('ended_at_ms'),
    }
    return row


def _observed_ms(run, timer, now_ms):
    if type(now_ms) is int and now_ms >= 0:
        return now_ms
    # A local driver reports game time derived from the emulated frame count.
    # It is the same clock the hook used for the splits, so finalizing an
    # attempt must not replace it with the host wall clock.
    frame_ms = run.get('observed_at_ms') if isinstance(run, dict) else None
    if type(frame_ms) is int and frame_ms >= 0:
        return frame_ms
    observed = run.get('observed_at') if isinstance(run, dict) else None
    if isinstance(observed, (int, float)) and observed > 0:
        candidate = int(observed * 1000)
    else:
        candidate = int(time.time() * 1000)
    previous = timer.get('heartbeat_at_ms') if isinstance(timer, dict) else None
    started = timer.get('started_at_ms') if isinstance(timer, dict) else None
    floor = max(value for value in (previous, started)
                if type(value) is int) if any(
                    type(value) is int for value in (previous, started)) else 0
    return max(candidate, floor)


def finalize_attempt(runtime, terminal_reason=None, *, now_ms=None, reason=None):
    """Finish or DNF the running attempt from the common corner teardown.

    Returns the resulting timer, or the unchanged timer when there is nothing
    to finalize.  Idempotent: a repeated call after a partial write reuses the
    PB that already names this attempt, skips the duplicate JSONL row and
    rewrites the timer.
    """
    timer = load_timer(runtime)
    if not isinstance(timer, dict) or timer.get('runtime_id') != Path(runtime).name:
        # ``timer.json`` is shared by every runtime generation.  A timer that
        # names another runtime is the live attempt of that runtime: never
        # finish it from this runtime's teardown/retry path.
        return timer
    if timer.get('status') != 'running':
        return timer
    records = load_records(runtime)
    run = load_run(runtime)
    # The runtime's own run state is authoritative for the terminal reason;
    # the explicit argument only covers a recovery call whose run state has
    # no terminal yet.
    run_terminal = run.get('terminal_reason')
    if not isinstance(run_terminal, str) or not run_terminal:
        run_terminal = None
    if run_terminal is not None:
        terminal_reason = run_terminal
    if (run_terminal == 'game_clear'
            or (run_terminal is None and run.get('clear_ending_seen') is True
                and terminal_reason in {None, 'game_clear', 'stopped'})):
        terminal_reason = 'game_clear'
    now = _observed_ms(run, timer, now_ms)
    if terminal_reason == 'game_clear':
        timer, records = timer_mod.finish(timer, records, now)
    else:
        detail = reason
        if detail is None and terminal_reason:
            detail = terminal_reason
        timer, records = timer_mod.finish_or_dnf(
            timer, records, now, terminal_reason, reason=detail)
    if timer.get('status') not in {'finished', 'dnf'}:
        return timer

    # PB/gold first, then the append-only history, then the display state.
    # A crash between them is repaired by the next idempotent call.
    timer = {**timer, 'env': environment()}
    save_records(runtime, records)
    append_attempt(runtime, attempt_row(timer, at_ms=now))
    save_timer(runtime, timer)
    return timer
