"""Per-segment summary of a finished hanjuku-speedrun attempt.

Reads the attempt's split timer and observation trace and writes
``segments.json`` next to the spool.  The summary answers "where did the time
go": time per phase inside each chapter, battle count and the in-game months
seen, confirmed split verdicts, and the bot/chart versions used.

Fields that the trace cannot support are ``None``.  In particular there is no
measured sortie count, hold duration, stall duration or LLM wait in the data
the bot exposes; those stay ``None`` rather than being estimated.
"""
from __future__ import annotations

import gzip
import json
import os
from pathlib import Path
import time

from . import spool, store

SCHEMA = 1
#: A week-long attempt at the normal 1.5 s cadence is about 400k lines; the
#: streaming reader keeps memory constant, so this is only a CPU guard.
MAX_TRACE_LINES = 1_000_000


def segments_path(runtime, attempt_id):
    return spool.attempt_dir(runtime, attempt_id) / 'segments.json'


def _attempt_source(runtime, attempt_id):
    """The timer document for the attempt, or the JSONL row as a fallback."""
    timer = store.load_timer(runtime)
    if isinstance(timer, dict) and timer.get('attempt_id') == attempt_id:
        return timer
    for row in reversed(store.load_attempts(runtime)):
        if isinstance(row, dict) and row.get('attempt_id') == attempt_id:
            return row
    return {}


def _trace_files(directory):
    files = []
    try:
        with os.scandir(directory) as entries:
            for entry in entries:
                name = entry.name
                if not name.startswith('trace'):
                    continue
                if not name.endswith(('.jsonl', '.jsonl.gz')):
                    continue
                if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                    continue
                files.append(Path(entry.path))
    except OSError:
        return []
    files.sort(key=lambda path: path.name)
    return files


def _iter_trace(directory):
    seen = 0
    for path in _trace_files(directory):
        try:
            opener = gzip.open if path.name.endswith('.gz') else open
            with opener(path, 'rt', encoding='utf-8') as stream:
                for line in stream:
                    seen += 1
                    if seen > MAX_TRACE_LINES:
                        return
                    if not line.strip():
                        continue
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(row, dict):
                        yield row
        except (OSError, EOFError, UnicodeError):
            continue


def _int(value):
    return value if type(value) is int else None


def _splits(source):
    splits = source.get('splits') if isinstance(source, dict) else None
    return [item for item in splits if isinstance(item, dict)] if isinstance(splits, list) else []


def _segment_skeletons(splits):
    skeletons = []
    previous = 0
    for position, split in enumerate(splits):
        start = _int(split.get('index'))
        if start is None:
            continue
        end = _int(split.get('end_index'))
        if end is None or end < start:
            end = start
        segment_ms = _int(split.get('segment_ms'))
        cumulative = _int(split.get('cumulative_ms'))
        if cumulative is None and segment_ms is not None:
            cumulative = previous + segment_ms
        skeletons.append({
            'index': start,
            'end_index': end,
            'merged': bool(end > start or split.get('merged') is True),
            'segment_ms': segment_ms,
            'cumulative_ms': cumulative,
            'start_cumulative_ms': previous,
            'verdict': split.get('verdict') if isinstance(split.get('verdict'), str) else None,
            'source': split.get('source') if isinstance(split.get('source'), str) else None,
            'phase_ms': {},
            'battles': None,
            'sorties': None,
            'months': [],
            'hold_ms': None,
            'stall_ms': None,
            'llm_wait_ms': None,
            'bot_versions': [],
            'observations': 0,
        })
        previous = cumulative if cumulative is not None else previous
    for position, skeleton in enumerate(skeletons):
        if skeleton['cumulative_ms'] is None and position + 1 < len(skeletons):
            skeleton['cumulative_ms'] = skeletons[position + 1]['start_cumulative_ms']
    return skeletons


def _segment_for(elapsed, skeletons):
    if elapsed is None:
        return None
    for skeleton in skeletons:
        end = skeleton.get('cumulative_ms')
        if end is not None and elapsed <= end:
            return skeleton
    return skeletons[-1] if skeletons else None


def _add_phase(skeleton, phase, duration):
    if skeleton is None or duration <= 0:
        return
    key = phase if isinstance(phase, str) and phase else 'unknown'
    skeleton['phase_ms'][key] = skeleton['phase_ms'].get(key, 0) + duration


def _scan_trace(directory, skeletons):
    observations = 0
    all_bots = []
    all_charts = []
    previous = None
    for row in _iter_trace(directory):
        observations += 1
        bot = row.get('bot_version')
        if isinstance(bot, str) and bot and bot not in all_bots:
            all_bots.append(bot)
        chart = row.get('chart_edition')
        if isinstance(chart, str) and chart and chart not in all_charts:
            all_charts.append(chart)
        elapsed = _int(row.get('elapsed_ms'))
        if elapsed is None:
            continue
        skeleton = _segment_for(elapsed, skeletons)
        if skeleton is None:
            continue
        skeleton['observations'] += 1
        phase = row.get('phase') if isinstance(row.get('phase'), str) else None
        if previous is not None:
            previous_elapsed, previous_skeleton, previous_phase = previous
            if previous_skeleton is not None and elapsed >= previous_elapsed:
                if previous_skeleton is skeleton:
                    _add_phase(skeleton, previous_phase, elapsed - previous_elapsed)
                else:
                    end = previous_skeleton.get('cumulative_ms')
                    if end is not None and end > previous_elapsed:
                        _add_phase(previous_skeleton, previous_phase,
                                   end - previous_elapsed)
        previous = (elapsed, skeleton, phase)
        battles = _int(row.get('battles_started'))
        if battles is not None:
            if '_battle_min' not in skeleton:
                skeleton['_battle_min'] = battles
                skeleton['_battle_max'] = battles
            else:
                skeleton['_battle_min'] = min(skeleton['_battle_min'], battles)
                skeleton['_battle_max'] = max(skeleton['_battle_max'], battles)
        month = row.get('month')
        if isinstance(month, str) and month and month not in skeleton['months']:
            skeleton['months'].append(month)
        if isinstance(bot, str) and bot and bot not in skeleton['bot_versions']:
            skeleton['bot_versions'].append(bot)
    if previous is not None:
        previous_elapsed, previous_skeleton, previous_phase = previous
        end = previous_skeleton.get('cumulative_ms') if previous_skeleton else None
        if end is not None and end > previous_elapsed:
            _add_phase(previous_skeleton, previous_phase, end - previous_elapsed)
    return observations, all_bots, all_charts


def _finish(skeletons):
    for skeleton in skeletons:
        low = skeleton.pop('_battle_min', None)
        high = skeleton.pop('_battle_max', None)
        if low is not None and high is not None:
            skeleton['battles'] = max(0, high - low)
        skeleton['phase_ms'] = dict(sorted(skeleton['phase_ms'].items()))
        skeleton['months'] = list(skeleton['months'])
        skeleton['bot_versions'] = list(skeleton['bot_versions'])


def build_segments(runtime, attempt_id):
    """Build and atomically write ``segments.json``; returns the document."""
    runtime = Path(runtime)
    attempt_id = spool._component(attempt_id, 'attempt_id')
    source = _attempt_source(runtime, attempt_id)
    skeletons = _segment_skeletons(_splits(source))
    directory = spool.spool_dir(runtime, attempt_id)
    if directory.is_dir():
        observations, trace_bots, trace_charts = _scan_trace(directory, skeletons)
    else:
        observations, trace_bots, trace_charts = 0, [], []
    _finish(skeletons)

    bot_versions = []
    for value in ([source.get('bot_version')] + trace_bots
                  + [bot for skeleton in skeletons
                     for bot in skeleton['bot_versions']]):
        if isinstance(value, str) and value and value not in bot_versions:
            bot_versions.append(value)
    chart_editions = []
    for value in [source.get('chart_edition')] + trace_charts:
        if isinstance(value, str) and value and value not in chart_editions:
            chart_editions.append(value)
    dropped = spool.read_json(spool.dropped_path(runtime, attempt_id))
    document = {
        'schema': SCHEMA,
        'game': store.GAME,
        'attempt_id': attempt_id,
        'attempt_no': _int(source.get('attempt_no')),
        'runtime_id': source.get('runtime_id') if isinstance(source.get('runtime_id'), str) else None,
        'generation': _int(source.get('generation')),
        'status': source.get('status') if isinstance(source.get('status'), str) else None,
        'result': source.get('result') if isinstance(source.get('result'), str) else None,
        'started_at_ms': _int(source.get('started_at_ms')),
        'ended_at_ms': _int(source.get('ended_at_ms')),
        'total_ms': _int(source.get('total_ms')),
        'bot_version': source.get('bot_version') if isinstance(source.get('bot_version'), str) else None,
        'chart_edition': source.get('chart_edition') if isinstance(source.get('chart_edition'), str) else None,
        'bot_versions': bot_versions,
        'chart_editions': chart_editions,
        'trace_observations': observations,
        'segments': skeletons,
        'dropped': dropped if isinstance(dropped, dict) else None,
        'built_at_ms': int(time.time() * 1000),
    }
    spool.write_json(segments_path(runtime, attempt_id), document)
    return document
