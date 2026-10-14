"""Bounded, exact screen/actual-input cycle evidence; no runtime side effects.

A phase or an intended action alone is never proof of a loop. Full RGB hashes
retain visible progress (HP, money, text, etc.). Timing/dwell differences do not
create progress. Irregular or visually nonidentical cycles are deliberately not
inferred from similar phases. The caller binds this state to its runtime lease.
"""
from __future__ import annotations

import hashlib
import json
import math

MAX_PERIOD = 24
MIN_CYCLES = 4
MAX_HISTORY = MAX_PERIOD * MIN_CYCLES
MAX_INPUTS = 32
EVIDENCE = 'repeated_screen_input_cycle'


def _number(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def _hash(value):
    return (isinstance(value, str) and len(value) == 64
            and all(c in '0123456789abcdef' for c in value))


def _token(value):
    return (isinstance(value, str) and len(value) == 65
            and value[0] in 'io' and _hash(value[1:]))


def add_input(pending, action_type, buttons):
    """Hash successful inputs in order. Hold duration is not game progress."""
    if not isinstance(pending, dict):
        pending = {}
    count = pending.get('count', 0)
    digest = pending.get('sha256', '0' * 64)
    if type(count) is not int or not 0 <= count <= MAX_INPUTS or not _hash(digest):
        return {'count': MAX_INPUTS + 1}
    if count == MAX_INPUTS:
        return {'count': MAX_INPUTS + 1}
    payload = json.dumps([digest, action_type, list(buttons)],
                         ensure_ascii=True, separators=(',', ':'))
    return {'count': count + 1, 'sha256': hashlib.sha256(payload.encode()).hexdigest()}


def _valid(record):
    if (not isinstance(record, dict) or type(record.get('schema')) is not int
            or record['schema'] != 1):
        return False
    history = record.get('history')
    last = record.get('last_observed')
    if (not _number(last) or not isinstance(history, list)
            or not 1 <= len(history) <= MAX_HISTORY):
        return False
    previous = -1
    for entry in history:
        if (not isinstance(entry, list) or len(entry) != 2 or not _number(entry[0])
                or not previous <= entry[0] <= last or not _token(entry[1])):
            return False
        previous = entry[0]
    candidate = record.get('candidate')
    if candidate is None:
        return True
    if not isinstance(candidate, dict):
        return False
    pattern = candidate.get('pattern')
    offset = candidate.get('offset')
    cycles = candidate.get('cycles')
    since = candidate.get('since')
    completed = candidate.get('completed_at')
    if (not isinstance(pattern, list) or not 2 <= len(pattern) <= MAX_PERIOD
            or not all(_token(t) for t in pattern) or len(set(pattern)) < 2
            or not any(t[0] == 'i' for t in pattern)
            or type(offset) is not int or not 0 <= offset < len(pattern)
            or type(cycles) is not int or not MIN_CYCLES <= cycles <= 1_000_000_000
            or not _number(since) or not _number(completed)
            or not since <= completed <= last
            or history[-1][1] != pattern[(offset - 1) % len(pattern)]):
        return False
    # Check the retained suffix too, not just a free-standing period counter.
    for distance, entry in enumerate(reversed(history)):
        if entry[0] < since:
            break
        if entry[1] != pattern[(offset - 1 - distance) % len(pattern)]:
            return False
    return True


def observe(record, frame_sha256, phase, pending, *, now, continuous, max_gap=15):
    """Consume one observed frame and inputs sent since the preceding frame.

    Consecutive equal tokens are compressed. A candidate is established only
    after four complete matching cycles; every later transition must match.
    Pauses, capture gaps, clock rewinds and caller-proven progress reset it.
    """
    if not _number(now) or not _hash(frame_sha256) or not isinstance(phase, str):
        return {}
    if not continuous:
        record, pending = {}, {}
    elif not _valid(record) or not 0 <= now - record['last_observed'] <= max_gap:
        record = {}
    pending = {} if pending is None else pending
    if not isinstance(pending, dict):
        return {}
    count = pending.get('count', 0)
    input_hash = pending.get('sha256', '0' * 64)
    if (type(count) is not int or not 0 <= count <= MAX_INPUTS or not _hash(input_hash)
            or (count == 0 and input_hash != '0' * 64)):
        return {}
    raw = json.dumps([frame_sha256, phase, input_hash, count], separators=(',', ':'))
    token = ('i' if count else 'o') + hashlib.sha256(raw.encode()).hexdigest()
    history = [list(entry) for entry in record.get('history', [])]
    candidate = record.get('candidate')
    if candidate is not None:
        candidate = dict(candidate)
    # Re-observation of one step never adds a cycle or advances completed_at.
    if history and history[-1][1] == token:
        return {'schema': 1, 'last_observed': now, 'history': history,
                'candidate': candidate}
    history = (history + [[now, token]])[-MAX_HISTORY:]
    if candidate:
        pattern, offset = candidate['pattern'], candidate['offset']
        if token == pattern[offset]:
            offset = (offset + 1) % len(pattern)
            candidate['offset'] = offset
            if offset == 0:
                candidate['cycles'] = min(1_000_000_000, candidate['cycles'] + 1)
                candidate['completed_at'] = now
        else:
            candidate = None
    if candidate is None:
        tokens = [entry[1] for entry in history]
        for period in range(2, min(MAX_PERIOD, len(tokens) // MIN_CYCLES) + 1):
            pattern = tokens[-period:]
            if (len(set(pattern)) >= 2 and any(t[0] == 'i' for t in pattern)
                    and tokens[-period * MIN_CYCLES:] == pattern * MIN_CYCLES):
                candidate = {'pattern': pattern, 'offset': 0, 'cycles': MIN_CYCLES,
                             'since': history[-period * MIN_CYCLES][0],
                             'completed_at': now}
                break
    return {'schema': 1, 'last_observed': now, 'history': history,
            'candidate': candidate}


def confirmed(record, *, now, seconds=300):
    """Return a small revalidated proof only on a newly completed cycle."""
    if (not _number(now) or not _number(seconds) or not _valid(record)
            or record['last_observed'] != now):
        return None
    candidate = record.get('candidate')
    if (not candidate or candidate['offset'] != 0 or candidate['completed_at'] != now
            or now - candidate['since'] < seconds):
        return None
    return {'kind': EVIDENCE, 'period': len(candidate['pattern']),
            'cycles': candidate['cycles'], 'since': candidate['since'],
            'seconds': now - candidate['since'],
            'pattern_sha256': hashlib.sha256(
                ''.join(candidate['pattern']).encode()).hexdigest()}
