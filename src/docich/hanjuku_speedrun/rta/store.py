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
import tempfile
import time

from . import timer as timer_mod

GAME = timer_mod.GAME
DATA_PARTS = ('speedrun', 'hanjuku-speedrun')
TIMER_FILE = 'timer.json'
RECORDS_FILE = 'records.json'
ATTEMPTS_FILE = 'attempts.jsonl'
RUN_FILE = 'hanjuku_run.json'

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


def data_dir(runtime):
    """``<runtime parent>/speedrun/hanjuku-speedrun`` for a runtime dir."""
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
        pass
    return target


def _read_json(path, limit, *, quarantine=True):
    """Read one JSON document with a size/regular-file gate."""
    try:
        meta = os.lstat(path)
    except FileNotFoundError:
        return None
    except OSError:
        return None
    if not os.path.isfile(path) or os.path.islink(path) or meta.st_size > limit:
        if quarantine:
            _quarantine(path)
        return None
    try:
        raw = path.read_text(encoding='utf-8')
        document = json.loads(raw)
    except (OSError, ValueError, UnicodeError):
        if quarantine:
            _quarantine(path)
        return None
    return document


def _atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f'.{path.name}.', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
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
    _atomic_write(timer_path(runtime), data)


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
    raw = _read_json(records_path(runtime), MAX_RECORDS_BYTES)
    if raw is None:
        if records_path(runtime).exists():
            _quarantine(records_path(runtime))
        return timer_mod.default_records()
    if not isinstance(raw, dict) or raw.get('game') not in (None, GAME):
        _quarantine(records_path(runtime))
        return timer_mod.default_records()
    return timer_mod.normalize_records(raw)


def load_run(runtime):
    """Read ``hanjuku_run.json``; never quarantine or modify it."""
    raw = _read_json(Path(runtime) / RUN_FILE, MAX_RUN_BYTES, quarantine=False)
    return raw if isinstance(raw, dict) else {}


def load_attempts(runtime):
    path = attempts_path(runtime)
    try:
        meta = os.lstat(path)
    except FileNotFoundError:
        return []
    except OSError:
        return []
    if not os.path.isfile(path) or os.path.islink(path) or meta.st_size > MAX_ATTEMPTS_BYTES:
        _quarantine(path)
        return []
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
    except (OSError, UnicodeError):
        _quarantine(path)
        return []
    if damaged:
        # Keep the original bytes and rewrite only the readable rows.
        _quarantine(path)
        _write_attempts(path, rows)
    return rows


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
    an interrupted write leaves the previous complete file.  The newest rows
    win when the byte bound is reached.
    """
    if not isinstance(row, dict):
        raise StoreError('invalid_attempt')
    attempt_id = row.get('attempt_id')
    if not isinstance(attempt_id, str) or not attempt_id:
        raise StoreError('invalid_attempt_id')
    path = attempts_path(runtime)
    rows = [existing for existing in load_attempts(runtime)
            if isinstance(existing, dict)
            and existing.get('attempt_id') != attempt_id]
    rows.append(row)
    _write_attempts(path, rows)
    return True


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
        'attempt_id': timer.get('attempt_id'),
        'attempt_no': timer.get('attempt_no'),
        'status': status,
        'result': status,
        'started_at_ms': timer.get('started_at_ms'),
        'ended_at_ms': timer.get('ended_at_ms'),
        'total_ms': timer.get('total_ms'),
        'current_index': timer.get('current_index'),
        'segments_ms': summary['segments_ms'],
        'cumulative_ms': summary['cumulative_ms'],
        'merged_ranges': summary['merged_ranges'],
        'splits': timer.get('splits') or [],
        'total_verdict': timer.get('total_verdict'),
        'pb_updated': bool(timer.get('pb_updated')),
        'dnf_reason': timer.get('dnf_reason'),
        'reason': timer.get('reason'),
        'bot_version': timer.get('bot_version'),
        'chart_edition': timer.get('chart_edition'),
        'at_ms': at_ms if type(at_ms) is int else timer.get('ended_at_ms'),
    }
    return row


def _observed_ms(run, timer, now_ms):
    if type(now_ms) is int and now_ms >= 0:
        return now_ms
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
    if not isinstance(timer, dict) or timer.get('status') != 'running':
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
    save_records(runtime, records)
    append_attempt(runtime, attempt_row(timer, at_ms=now))
    save_timer(runtime, timer)
    return timer
