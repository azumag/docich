"""Pure split-timer state machine for the hanjuku-speedrun RTA records.

The module never reads a file, never reads the clock and never logs.  Every
function takes the current timer and records dictionaries, an observation
time in epoch milliseconds and (where needed) the event that was observed,
and returns new dictionaries.  ``store.py`` owns persistence and
``hook.py`` supplies the observed time.

Terminology
-----------
* A *segment* is one of the 12 chapters (index 0 is 第1話).
* The *running segment* starts at ``timer['current_index']``.
* A *confirmed* segment is one whose end was measured.  When the observed
  chapter skipped over the running segment, the skipped segments and the
  observed one become a single ``merged`` split: the total is valid but the
  individual times are unknown, so the merged split never updates a segment
  best.
* PB (personal best) is only updated by a finished attempt.  Segment bests
  (``gold``) are updated by every confirmed single segment, including an
  attempt that later ends as DNF.
"""
from __future__ import annotations

import copy

SCHEMA = 1
GAME = 'hanjuku-speedrun'
SEGMENT_COUNT = 12
STATUSES = frozenset({'pending', 'running', 'finished', 'dnf'})

#: ``terminal_reason`` -> the public DNF reason used by the records.
DNF_REASONS = {
    'game_over': 'game_over',
    'screen_stalled': 'stalled',
    'input_stalled': 'stalled',
}


# ---------------------------------------------------------------------------
# small validation helpers
# ---------------------------------------------------------------------------


def _int(value, *, minimum=0, maximum=None):
    """A non-bool int in range, or ``None``.  Never raises."""
    if type(value) is not int:
        return None
    if value < minimum:
        return None
    if maximum is not None and value > maximum:
        return None
    return value


def _text(value, limit=160):
    if not isinstance(value, str):
        return None
    return value[:limit]


def _copy(value):
    return copy.deepcopy(value)


def _empty_gold():
    return [None] * SEGMENT_COUNT


def default_records(now_ms=0):
    """An empty records document (also the fallback after a corrupt file)."""
    return {
        'schema': SCHEMA,
        'game': GAME,
        'next_attempt_no': 1,
        'pb': None,
        'gold_segments': _empty_gold(),
        'gold_segments_ms': _empty_gold(),
        'sum_of_best_ms': None,
        'updated_at_ms': _int(now_ms) or 0,
    }


def _normalize_gold(value):
    gold = _empty_gold()
    ms = _empty_gold()
    if isinstance(value, list):
        for index, item in enumerate(value[:SEGMENT_COUNT]):
            if isinstance(item, dict):
                segment = _int(item.get('segment_ms'))
                attempt = _text(item.get('attempt_id'))
                at = _int(item.get('at_ms'))
                if segment is not None:
                    gold[index] = {'segment_ms': segment,
                                   'attempt_id': attempt,
                                   'at_ms': at}
                    ms[index] = segment
            elif _int(item) is not None:
                ms[index] = item
                gold[index] = {'segment_ms': item, 'attempt_id': None,
                               'at_ms': None}
    return gold, ms


def _normalize_ranges(value):
    ranges = []
    if not isinstance(value, list):
        return ranges
    for item in value:
        if (isinstance(item, (list, tuple)) and len(item) == 2
                and _int(item[0], maximum=SEGMENT_COUNT - 1) is not None
                and _int(item[1], maximum=SEGMENT_COUNT - 1) is not None
                and item[0] <= item[1]):
            ranges.append([int(item[0]), int(item[1])])
    return ranges


def _normalize_array(value, *, allow_none=True):
    result = [None] * SEGMENT_COUNT
    if not isinstance(value, list):
        return result
    for index, item in enumerate(value[:SEGMENT_COUNT]):
        if _int(item) is not None:
            result[index] = item
        elif not allow_none:
            result[index] = 0
    return result


def normalize_records(raw):
    """Coerce a loaded document into the current schema without losing PB.

    JSON that parsed but has an old/short shape is repaired here rather than
    thrown away; only unparseable JSON is quarantined by ``store``.
    """
    records = default_records()
    if not isinstance(raw, dict):
        return records
    next_no = _int(raw.get('next_attempt_no'), minimum=1)
    records['next_attempt_no'] = next_no if next_no is not None else 1
    if _int(raw.get('updated_at_ms')) is not None:
        records['updated_at_ms'] = raw['updated_at_ms']

    pb = raw.get('pb')
    if isinstance(pb, dict):
        total = _int(pb.get('total_ms'))
        attempt_id = _text(pb.get('attempt_id'))
        if total is not None and attempt_id:
            records['pb'] = {
                'attempt_id': attempt_id,
                'attempt_no': _int(pb.get('attempt_no'), minimum=1),
                'total_ms': total,
                'segments_ms': _normalize_array(pb.get('segments_ms')),
                'cumulative_ms': _normalize_array(pb.get('cumulative_ms')),
                'merged_ranges': _normalize_ranges(pb.get('merged_ranges')),
                'bot_version': _text(pb.get('bot_version')),
                'chart_edition': _text(pb.get('chart_edition')),
                'at_ms': _int(pb.get('at_ms')),
            }

    gold, ms = _normalize_gold(raw.get('gold_segments'))
    merged_ms = raw.get('gold_segments_ms')
    if isinstance(merged_ms, list):
        for index, item in enumerate(merged_ms[:SEGMENT_COUNT]):
            if _int(item) is not None and gold[index] is None:
                gold[index] = {'segment_ms': item, 'attempt_id': None,
                               'at_ms': None}
                ms[index] = item
    records['gold_segments'] = gold
    records['gold_segments_ms'] = ms
    records['sum_of_best_ms'] = sum_of_best_ms(ms)
    return records


def sum_of_best_ms(gold_ms):
    """Sum of the 12 segment bests, or ``None`` until all are known."""
    if not isinstance(gold_ms, list) or len(gold_ms) != SEGMENT_COUNT:
        return None
    values = [_int(item) for item in gold_ms]
    if any(item is None for item in values):
        return None
    return sum(values)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# timer documents
# ---------------------------------------------------------------------------


def comparison(records):
    """The display comparison block derived from the stored records."""
    records = normalize_records(records)
    pb = records.get('pb')
    return {
        'pb_total_ms': pb.get('total_ms') if pb else None,
        'pb_segments_ms': list(pb.get('segments_ms') or []) if pb else [],
        'pb_cumulative_ms': list(pb.get('cumulative_ms') or []) if pb else [],
        'gold_segments_ms': list(records.get('gold_segments_ms') or _empty_gold()),
        'sum_of_best_ms': records.get('sum_of_best_ms'),
    }


def new_timer(runtime_id, records, now_ms):
    """A pending timer: the first observation happened, no attempt started."""
    now = _int(now_ms) or 0
    return {
        'schema': SCHEMA,
        'game': GAME,
        'runtime_id': _text(runtime_id) or '',
        'attempt_id': None,
        'attempt_no': None,
        'status': 'pending',
        'started_at_ms': None,
        'ended_at_ms': None,
        'current_index': 0,
        'splits': [],
        'comparison': comparison(records),
        'heartbeat_at_ms': now,
    }


def heartbeat(timer, now_ms):
    """Advance only the liveness stamp; used on every observation."""
    result = _copy(timer)
    now = _int(now_ms) or 0
    previous = _int(result.get('heartbeat_at_ms')) or 0
    started = _int(result.get('started_at_ms')) or 0
    result['heartbeat_at_ms'] = max(now, previous, started)
    return result


def start_attempt(timer, records, now_ms, *, attempt_no=None,
                  bot_version=None, chart_edition=None):
    """Start a new attempt and reserve its number in ``records``.

    ``next_attempt_no`` is the durable counter.  It is advanced here, before
    any attempt file is written, so a truncated JSONL history can never make
    two attempts share an id.
    """
    source = normalize_records(records)
    timer = _copy(timer)
    now = _int(now_ms) or 0
    reserved = _int(source.get('next_attempt_no'), minimum=1) or 1
    number = _int(attempt_no, minimum=1) or reserved
    number = max(number, reserved)
    runtime_id = _text(timer.get('runtime_id')) or ''
    attempt_id = f'a{number:04d}-{runtime_id}'
    started = {
        'schema': SCHEMA,
        'game': GAME,
        'runtime_id': runtime_id,
        'attempt_id': attempt_id,
        'attempt_no': number,
        'status': 'running',
        'started_at_ms': now,
        'ended_at_ms': None,
        'current_index': 0,
        'splits': [],
        'comparison': comparison(source),
        'heartbeat_at_ms': now,
        'bot_version': _text(bot_version),
        'chart_edition': _text(chart_edition),
    }
    source = _copy(source)
    source['next_attempt_no'] = number + 1
    return started, source


# ---------------------------------------------------------------------------
# PB / segment-best lookup
# ---------------------------------------------------------------------------


def _pb(records):
    pb = records.get('pb') if isinstance(records, dict) else None
    return pb if isinstance(pb, dict) else None


def _cumulative_at(pb, index):
    """PB cumulative time at the end of ``index`` (``-1`` means start)."""
    if index < 0:
        return 0
    values = pb.get('cumulative_ms') if isinstance(pb, dict) else None
    if not isinstance(values, list) or index >= len(values):
        return None
    return _int(values[index])


def pb_range_ms(records, start, end):
    """PB time for the inclusive segment range, or ``None`` when unknown.

    A PB attempt may itself contain a merged range.  Its intermediate
    cumulative times are unknown, so only a range with a known start/end
    boundary (or all individual segments) is comparable.
    """
    pb = _pb(records)
    if pb is None:
        return None
    if start == end:
        segments = pb.get('segments_ms')
        if isinstance(segments, list) and start < len(segments):
            value = _int(segments[start])
            if value is not None:
                return value
        return None
    ranges = pb.get('merged_ranges') or []
    if [start, end] not in ranges:
        segments = pb.get('segments_ms')
        if not isinstance(segments, list):
            return None
        values = []
        for index in range(start, min(end + 1, len(segments))):
            value = _int(segments[index])
            if value is None:
                return None
            values.append(value)
        return sum(values) if len(values) == end - start + 1 else None
    before = _cumulative_at(pb, start - 1)
    after = _cumulative_at(pb, end)
    if before is None or after is None or after < before:
        return None
    return after - before


def _gold_at(records, index):
    gold = records.get('gold_segments') if isinstance(records, dict) else None
    if isinstance(gold, list) and 0 <= index < len(gold):
        item = gold[index]
        if isinstance(item, dict):
            return item
    return None


def _update_gold(records, index, segment_ms, attempt_id, at_ms):
    records = _copy(records)
    gold = records.get('gold_segments')
    if not isinstance(gold, list) or len(gold) != SEGMENT_COUNT:
        gold = _empty_gold()
    gold[index] = {'segment_ms': segment_ms, 'attempt_id': attempt_id,
                   'at_ms': at_ms}
    records['gold_segments'] = gold
    ms = [_int(item.get('segment_ms')) if isinstance(item, dict) else _int(item)
          for item in gold]
    records['gold_segments_ms'] = ms
    records['sum_of_best_ms'] = sum_of_best_ms(ms)
    records['updated_at_ms'] = at_ms
    return records


# ---------------------------------------------------------------------------
# split confirmation
# ---------------------------------------------------------------------------


def _split_start(timer):
    splits = timer.get('splits')
    if isinstance(splits, list) and splits and isinstance(splits[-1], dict):
        return _int(splits[-1].get('cumulative_ms')) or 0
    return 0


def confirm_split(timer, records, now_ms, end_index, source):
    """Confirm the end of chapter ``end_index + 1``.

    The caller passes the *last* segment covered by the measurement.  When it
    is greater than the running segment, everything between is recorded as a
    single merged split.  Returns ``(timer, records)``; a stale/repeated or
    out-of-range measurement returns unchanged copies.
    """
    timer = _copy(timer)
    records = normalize_records(records)
    if timer.get('status') != 'running':
        return timer, records
    end = _int(end_index, maximum=SEGMENT_COUNT - 1)
    if end is None:
        return timer, records
    start = _int(timer.get('current_index'), maximum=SEGMENT_COUNT)
    if start is None or start >= SEGMENT_COUNT or end < start:
        return timer, records
    started = _int(timer.get('started_at_ms')) or 0
    now = _int(now_ms) or 0
    previous_heartbeat = _int(timer.get('heartbeat_at_ms')) or 0
    now = max(now, previous_heartbeat, started)
    before = _split_start(timer)
    segment_ms = max(0, now - started - before)
    cumulative = before + segment_ms
    merged = end > start
    source = _text(source, limit=32) or 'chapter_end_save'
    attempt_id = _text(timer.get('attempt_id'))

    pb_ms = pb_range_ms(records, start, end)
    delta = None if pb_ms is None else segment_ms - pb_ms

    if merged:
        # A merged split has no individual times: never gold, never a best.
        if pb_ms is None:
            verdict = 'white'
        elif segment_ms < pb_ms:
            verdict = 'blue'
        elif segment_ms > pb_ms:
            verdict = 'red'
        else:
            verdict = 'white'
    else:
        prior = _gold_at(records, start)
        prior_ms = _int(prior.get('segment_ms')) if prior else None
        already_applied = bool(
            prior
            and attempt_id
            and _text(prior.get('attempt_id')) == attempt_id
            and prior_ms == segment_ms
        )
        if prior_ms is None or segment_ms < prior_ms or already_applied:
            verdict = 'gold'
        elif pb_ms is None:
            verdict = 'white'
        elif segment_ms < pb_ms:
            verdict = 'blue'
        elif segment_ms > pb_ms:
            verdict = 'red'
        else:
            verdict = 'white'
        if prior_ms is None or segment_ms < prior_ms:
            records = _update_gold(records, start, segment_ms, attempt_id, now)

    split = {
        'index': start,
        'segment_ms': segment_ms,
        'cumulative_ms': cumulative,
        'verdict': verdict,
        'delta_ms': delta,
        'source': source,
        'merged': merged,
    }
    timer.setdefault('splits', []).append(split)
    timer['current_index'] = end + 1
    timer['heartbeat_at_ms'] = now
    timer['comparison'] = comparison(records)
    return timer, records


# ---------------------------------------------------------------------------
# attempt summary and end
# ---------------------------------------------------------------------------


def attempt_summary(timer):
    """Per-segment arrays for the confirmed splits of an attempt.

    ``segments_ms`` is ``None`` inside a merged range (the individual times
    were never measured).  ``cumulative_ms`` is known at every boundary of a
    confirmed split, which is enough to compare a merged range later.
    """
    segments = _empty_gold()
    cumulative = _empty_gold()
    merged_ranges = []
    splits = timer.get('splits') if isinstance(timer, dict) else None
    splits = [item for item in splits if isinstance(item, dict)] if isinstance(splits, list) else []
    current = _int(timer.get('current_index'), maximum=SEGMENT_COUNT)
    limit = (current - 1) if current is not None and current > 0 else -1
    for position, split in enumerate(splits):
        start = _int(split.get('index'), maximum=SEGMENT_COUNT - 1)
        if start is None:
            continue
        if position + 1 < len(splits):
            following = _int(splits[position + 1].get('index'),
                             maximum=SEGMENT_COUNT - 1)
            end = (following - 1) if following is not None else limit
        else:
            end = limit
        if end is None or end < start:
            end = start
        if split.get('merged') is True or end > start:
            merged_ranges.append([start, end])
        else:
            segments[start] = _int(split.get('segment_ms'))
        cumulative[end] = _int(split.get('cumulative_ms'))
    return {
        'segments_ms': segments,
        'cumulative_ms': cumulative,
        'merged_ranges': merged_ranges,
    }


def total_ms(timer):
    splits = timer.get('splits') if isinstance(timer, dict) else None
    if not isinstance(splits, list):
        return 0
    return sum(_int(item.get('segment_ms')) or 0
               for item in splits if isinstance(item, dict))


def finish(timer, records, now_ms):
    """Finish the attempt at the clear ending and update PB/gold records."""
    timer = _copy(timer)
    records = normalize_records(records)
    if timer.get('status') != 'running':
        return timer, records
    now = _int(now_ms) or 0
    previous_heartbeat = _int(timer.get('heartbeat_at_ms')) or 0
    now = max(now, previous_heartbeat, _int(timer.get('started_at_ms')) or 0)
    current = _int(timer.get('current_index'), maximum=SEGMENT_COUNT) or 0
    if current < SEGMENT_COUNT:
        # The clear ending also closes the final chapter when its own split
        # was not measured.  Everything since the running segment is merged.
        timer, records = confirm_split(
            timer, records, now, SEGMENT_COUNT - 1, 'clear_ending')
    timer['status'] = 'finished'
    timer['ended_at_ms'] = now
    timer['current_index'] = SEGMENT_COUNT
    timer['dnf_reason'] = None
    timer['reason'] = None
    timer['heartbeat_at_ms'] = now

    summary = attempt_summary(timer)
    total = total_ms(timer)
    timer['total_ms'] = total
    timer['segments_ms'] = summary['segments_ms']
    timer['cumulative_ms'] = summary['cumulative_ms']
    timer['merged_ranges'] = summary['merged_ranges']

    pb = _pb(records)
    if pb is not None and _text(pb.get('attempt_id')) == _text(timer.get('attempt_id')):
        total_verdict = 'gold'
        new_pb = False
    elif pb is None or total < (_int(pb.get('total_ms')) or 0):
        total_verdict = 'gold'
        new_pb = True
        records = _copy(records)
        records['pb'] = {
            'attempt_id': _text(timer.get('attempt_id')),
            'attempt_no': _int(timer.get('attempt_no'), minimum=1),
            'total_ms': total,
            'segments_ms': summary['segments_ms'],
            'cumulative_ms': summary['cumulative_ms'],
            'merged_ranges': summary['merged_ranges'],
            'bot_version': _text(timer.get('bot_version')),
            'chart_edition': _text(timer.get('chart_edition')),
            'at_ms': now,
        }
        records['updated_at_ms'] = now
    else:
        total_verdict = 'red'
        new_pb = False
    timer['total_verdict'] = total_verdict
    timer['pb_updated'] = new_pb
    timer['comparison'] = comparison(records)
    return timer, records


def dnf(timer, records, now_ms, dnf_reason, *, reason=None):
    """End the attempt without a finish; confirmed segment bests stay."""
    timer = _copy(timer)
    records = normalize_records(records)
    if timer.get('status') != 'running':
        return timer, records
    now = _int(now_ms) or 0
    now = max(now, _int(timer.get('heartbeat_at_ms')) or 0,
              _int(timer.get('started_at_ms')) or 0)
    timer['status'] = 'dnf'
    timer['ended_at_ms'] = now
    timer['dnf_reason'] = _text(dnf_reason, limit=32) or 'stopped'
    timer['reason'] = _text(reason, limit=64)
    timer['total_verdict'] = None
    timer['pb_updated'] = False
    timer['heartbeat_at_ms'] = now
    summary = attempt_summary(timer)
    timer['total_ms'] = total_ms(timer)
    timer['segments_ms'] = summary['segments_ms']
    timer['cumulative_ms'] = summary['cumulative_ms']
    timer['merged_ranges'] = summary['merged_ranges']
    timer['comparison'] = comparison(records)
    return timer, records


def finish_or_dnf(timer, records, now_ms, terminal_reason, *, reason=None):
    """Apply the common-side terminal vocabulary to an attempt."""
    if terminal_reason == 'game_clear':
        return finish(timer, records, now_ms)
    return dnf(timer, records, now_ms,
               DNF_REASONS.get(terminal_reason, 'stopped'),
               reason=reason if reason is not None else terminal_reason)
