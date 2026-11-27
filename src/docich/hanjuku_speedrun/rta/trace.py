"""One-line observation trace for a hanjuku-speedrun attempt.

The generated speedrun bot calls :func:`hook.observe` once per observation.
That hook already keeps the split timer; this module records *what was seen*
for later time analysis: elapsed attempt time, chapter, in-game month, phase,
screen kind, the decisions made in this observation, a hold reason when the
bot recorded one, the observation gap, and the run's battle counter.

Only keys that are actually present in the bot's ``records`` / ``state`` /
``meta`` and in ``hanjuku_run.json`` are written.  A key that cannot be
observed is omitted instead of guessed.  Each line is bounded to 400 bytes.
The active file rotates at 8 MiB and the rotated file is compressed by a
detached ``gzip``.  Periodic frames (default every 10 minutes) and the frame
at a confirmed split are copied into the spool.  Nothing here waits for
compression or touches the bot's input.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import time

from . import spool, store

MAX_TRACE_BYTES = 8 * 1024 * 1024
MAX_LINE_BYTES = 400
DEFAULT_FRAME_INTERVAL_SEC = 600
MAX_FRAME_BYTES = spool.MAX_FRAME_BYTES
MAX_DECISIONS = 6
TRACE_FILE = 'trace.jsonl'
STATE_FILE = '.trace-state.json'


def trace_path(runtime, attempt_id):
    return spool.spool_dir(runtime, attempt_id) / TRACE_FILE


# ---------------------------------------------------------------------------
# line construction
# ---------------------------------------------------------------------------


def _text(value, limit):
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value[:limit] if value else None


def _int(value):
    return value if type(value) is int else None


def _chapter(records, state):
    for record in reversed(records if isinstance(records, list) else ()):
        if not isinstance(record, dict):
            continue
        value = _int(record.get('chapter'))
        if value is not None:
            return value
        metric = record.get('observed_metric')
        if isinstance(metric, dict):
            value = _int(metric.get('chapter'))
            if value is not None:
                return value
    if isinstance(state, dict):
        policy = state.get('policy')
        if isinstance(policy, dict):
            value = _int(policy.get('chapter'))
            if value is not None:
                return value
        value = _int(state.get('chapter'))
        if value is not None:
            return value
    return None


def _month(state):
    if not isinstance(state, dict):
        return None
    policy = state.get('policy')
    if isinstance(policy, dict):
        value = _text(policy.get('month'), 16)
        if value is not None:
            return value
    return _text(state.get('month'), 16)


def _chart(state):
    if not isinstance(state, dict):
        return None
    policy = state.get('policy')
    if isinstance(policy, dict):
        value = _text(policy.get('chart_edition'), 32)
        if value is not None:
            return value
    return _text(state.get('chart_edition'), 32)


def _hold(records):
    for record in reversed(records if isinstance(records, list) else ()):
        if not isinstance(record, dict):
            continue
        value = _text(record.get('deviation_reason'), 80)
        if value is not None:
            return value
        value = _text(record.get('reason'), 80)
        if value is not None:
            return value
    return None


def _decisions(records):
    names = []
    for record in records if isinstance(records, list) else ():
        if not isinstance(record, dict):
            continue
        name = _text(record.get('decision'), 40)
        if name is not None and name not in names:
            names.append(name)
        if len(names) >= MAX_DECISIONS:
            break
    return names


def _run_state(runtime, meta):
    run = store.load_run(runtime)
    identity = meta.get('hanjuku') if isinstance(meta, dict) else None
    if isinstance(identity, dict):
        run = {**run, **identity}
    return run


def _record(timer, runtime, records, state, meta, observed_ms):
    started = _int(timer.get('started_at_ms'))
    observed = _int(observed_ms)
    if observed is None:
        observed = _int(timer.get('heartbeat_at_ms'))
    if observed is None:
        observed = int(time.time() * 1000)
    elapsed = None
    if started is not None and observed >= started:
        elapsed = observed - started
    run = _run_state(runtime, meta)
    splits = timer.get('splits') if isinstance(timer.get('splits'), list) else []
    return {
        'at': observed,
        'elapsed_ms': elapsed,
        'chapter': _chapter(records, state),
        'month': _month(state),
        'phase': _text(run.get('phase'), 32),
        'screen_kind': _text(state.get('screen_kind'), 32),
        'decisions': _decisions(records),
        'hold_reason': _hold(records),
        'interval_ms': None,
        'splits': len(splits),
        'battles_started': _int(run.get('battles_started')),
        'bot_version': _text(run.get('bot_version') or timer.get('bot_version'), 40),
        'chart_edition': _chart(state),
    }


def encode_line(record):
    """Serialize one trace record, shrinking it until it fits 400 bytes."""
    record = dict(record)
    if isinstance(record.get('decisions'), list):
        record['decisions'] = record['decisions'][:MAX_DECISIONS]
    for key in ('month', 'phase', 'screen_kind', 'hold_reason', 'bot_version',
                'chart_edition'):
        if isinstance(record.get(key), str) and len(record[key]) > 80:
            record[key] = record[key][:80]

    def encode():
        return json.dumps(record, ensure_ascii=False, sort_keys=True,
                          separators=(',', ':')).encode('utf-8')

    line = encode()
    if len(line) <= MAX_LINE_BYTES:
        return line
    for key in ('chart_edition', 'screen_kind', 'battles_started',
                'bot_version', 'month', 'hold_reason', 'decisions', 'chapter',
                'phase'):
        record.pop(key, None)
        line = encode()
        if len(line) <= MAX_LINE_BYTES:
            return line
    # The minimal record only holds integers; this is a guard for a future
    # caller that adds an unknown field, not a path a normal observation
    # reaches.  Building the dict explicitly keeps the bytes valid UTF-8.
    minimal = {key: record[key] for key in
               ('at', 'elapsed_ms', 'interval_ms', 'splits') if key in record}
    return json.dumps(minimal, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':')).encode('utf-8')


def _last_line(path):
    """Parse the last trace record without reading the whole file."""
    if spool._regular(path) is None:
        return None
    try:
        size = os.path.getsize(path)
        if size <= 0:
            return None
        with open(path, 'rb') as stream:
            stream.seek(max(0, size - 4096))
            chunk = stream.read()
    except OSError:
        return None
    for raw in reversed(chunk.splitlines()):
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except ValueError:
            continue
        return row if isinstance(row, dict) else None
    return None


# ---------------------------------------------------------------------------
# frames
# ---------------------------------------------------------------------------


def _frame_interval_ms():
    raw = os.environ.get('DOCICH_SPEEDRUN_FRAME_INTERVAL_SEC')
    try:
        seconds = int(str(raw).strip())
    except (TypeError, ValueError):
        return DEFAULT_FRAME_INTERVAL_SEC * 1000
    if seconds <= 0:
        return DEFAULT_FRAME_INTERVAL_SEC * 1000
    return seconds * 1000


def _frame_taken(directory, prefix):
    # A bucket whose image did not fit under the cap is marked once; later
    # observations of the same bucket must not retry the copy.
    if os.path.lexists(directory / f'.{prefix}.skip'):
        return True
    for suffix in ('.png', '.jpg', '.jpeg', '.webp'):
        if os.path.lexists(directory / f'{prefix}{suffix}'):
            return True
    return False


def _mark_frame_skipped(directory, prefix):
    try:
        directory = spool._ensure_dir(directory)
        (directory / f'.{prefix}.skip').touch(exist_ok=True)
    except Exception:
        pass


def _copy_periodic_frame(runtime, attempt_id, elapsed, screenshot):
    if elapsed is None or elapsed < 0:
        return False
    interval = _frame_interval_ms()
    bucket = elapsed // interval
    if bucket <= 0:
        # The first periodic frame is one interval after the start, not at
        # the starting observation itself.
        return False
    suffix = spool._image_suffix(screenshot)
    directory = spool.frames_dir(runtime, attempt_id)
    prefix = f'frame-{bucket:05d}'
    if _frame_taken(directory, prefix):
        return False
    meta = spool._regular(screenshot)
    if meta is None or meta.st_size <= 0 or meta.st_size > MAX_FRAME_BYTES:
        return False
    if not spool.would_fit(runtime, attempt_id, meta.st_size):
        # Copying now would only be undone by the next enforce_cap, and the
        # next observation would copy it again.  Mark the bucket instead.
        _mark_frame_skipped(directory, prefix)
        return False
    return spool._copy_to_frames(runtime, attempt_id, screenshot,
                                 f'{prefix}{suffix}', limit=MAX_FRAME_BYTES)


def _copy_split_frame(runtime, attempt_id, timer, previous_splits, elapsed,
                      screenshot):
    splits = timer.get('splits') if isinstance(timer.get('splits'), list) else []
    if len(splits) <= previous_splits:
        return False
    if elapsed is None or elapsed < 0:
        elapsed = 0
    last = splits[-1] if isinstance(splits[-1], dict) else {}
    index = _int(last.get('index'))
    if index is None:
        index = max(0, len(splits) - 1)
    suffix = spool._image_suffix(screenshot)
    prefix = f'split-{index:02d}-{elapsed:012d}'
    directory = spool.frames_dir(runtime, attempt_id)
    if _frame_taken(directory, prefix):
        return False
    return spool._copy_to_frames(runtime, attempt_id, screenshot,
                                 f'{prefix}{suffix}', limit=MAX_FRAME_BYTES)


# ---------------------------------------------------------------------------
# append / rotate
# ---------------------------------------------------------------------------


def _rotate(runtime, attempt_id, path):
    directory = path.parent
    target = spool.reserve_path(directory, 'trace')
    try:
        os.replace(path, target)
    except OSError:
        spool._unlink_regular(target)
        return None
    spool.compress_detached(target)
    return target


def append(runtime, timer, records=None, state=None, meta=None,
           screenshot=None, observed_ms=None):
    """Append one observation line, rotate at the bound and copy frames.

    The split timer is already committed when this runs, so any failure here
    is only a lost trace line.  Never raises.
    """
    try:
        return _append(runtime, timer, records=records, state=state, meta=meta,
                       screenshot=screenshot, observed_ms=observed_ms)
    except Exception as exc:
        print(f'rta: trace skipped: {type(exc).__name__}: {str(exc)[:160]}',
              file=sys.stderr)
        return None


def _append(runtime, timer, *, records, state, meta, screenshot, observed_ms):
    if not isinstance(timer, dict):
        return None
    if timer.get('status') not in {'running', 'finished', 'dnf'}:
        return None
    attempt_id = timer.get('attempt_id')
    if not isinstance(attempt_id, str) or not attempt_id:
        return None
    runtime = Path(runtime)
    if timer.get('runtime_id') != runtime.name:
        return None
    records = records if isinstance(records, list) else []
    state = state if isinstance(state, dict) else {}
    meta = meta if isinstance(meta, dict) else {}
    directory = spool._ensure_dir(spool.spool_dir(runtime, attempt_id))
    path = directory / TRACE_FILE
    if os.path.lexists(path) and spool._regular(path) is None:
        # A symlink or a directory in place of the trace file is never
        # followed or replaced by this hook.
        return None

    # Read the previous line before a rotation: after ``_rotate`` the active
    # file is empty, and using that empty file as "no split yet" would copy a
    # split frame on the first observation after every rotation.
    previous = _last_line(path)
    prior_state = spool.read_json(directory / STATE_FILE)
    if not isinstance(prior_state, dict) or prior_state.get('attempt_id') != attempt_id:
        prior_state = {}

    try:
        if path.exists() and path.stat().st_size >= MAX_TRACE_BYTES:
            _rotate(runtime, attempt_id, path)
    except OSError:
        pass

    record = _record(timer, runtime, records, state, meta, observed_ms)
    previous_splits = _int(prior_state.get('splits'))
    if previous_splits is None and isinstance(previous, dict):
        previous_splits = _int(previous.get('splits'))
    if previous_splits is None:
        previous_splits = 0
    if isinstance(previous, dict):
        previous_at = _int(previous.get('at'))
        if previous_at is not None and record['at'] >= previous_at:
            record['interval_ms'] = record['at'] - previous_at
    line = encode_line(record)
    with path.open('a', encoding='utf-8') as stream:
        stream.write(line.decode('utf-8') + '\n')

    splits_now = len(timer.get('splits') or ())
    if not prior_state or previous_splits != splits_now:
        try:
            spool.write_json(directory / STATE_FILE, {
                'schema': 1,
                'attempt_id': attempt_id,
                'splits': splits_now,
                'at': record.get('at'),
            }, fsync=False)
        except Exception:
            pass

    copied = False
    if screenshot is not None:
        copied = _copy_periodic_frame(
            runtime, attempt_id, record.get('elapsed_ms'), screenshot)
        copied = _copy_split_frame(
            runtime, attempt_id, timer, previous_splits,
            record.get('elapsed_ms'), screenshot) or copied
    if copied:
        spool.enforce_cap(runtime, attempt_id)
    return record
