"""Record complete in-game months with no enemy-directed sortie.

The bot can spend a whole game month in the monthly scan, battles, camp recalls
or a failed plan and never launch a sortie.  month_sorties (docich#4642)
already counts confirmed departures toward enemy castles, but it resets on the
next month scope, so the zero month leaves no durable record.  This module
watches the policy state around decide (the caller passes the previous
observation and the new one), closes the month at the boundary and writes a
bounded, secret-free incident to the runtime directory.

The audit is evidence only.  It never sends input, never edits the plan and
never changes a decision.  Writes are best-effort: a failure is reported as a
fixed code by the caller and the observation continues.

Metric (owner decision 2026-11-20):

- confirmed: month_sorties.launches for the closed month.
- departures: distinct chart steps with order_launched,
  sortie_departed_observed or sortie_launch_confirmed in the month.
- zero_sortie: both are 0 and the month was observed from its start.
- move (own-castle transfer) and house repairs are reported separately and
  never make a month non-zero.

Artifacts:

- hanjuku_month_audit.json: open month + pending incident (small, atomic).
- hanjuku_month_audit.jsonl: sealed incidents (append-only, rotate 4 MB).
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter

SCHEMA = 1
AUDIT_FILE = 'hanjuku_month_audit.json'
LOG_NAME = 'hanjuku_month_audit'
TITLE_PREFIX = '[半熟英雄] 月内出撃ゼロ'
AFTER_WINDOW_SECONDS = 300
MIN_MONTH_SECONDS = 120
MAX_GAP_SECONDS = 60
MIN_OBSERVATIONS = 20
MAX_READ_BYTES = 2 * 1024 * 1024
MAX_RECORD_BYTES = 32 * 1024
MAX_STATE_BYTES = 128 * 1024
MAX_TEXT = 160
MAX_LIST = 20
COMMENT_COOLDOWN_SECONDS = 3600
MAX_ISSUES = 200
DEFAULT_REPO = 'azumag/docich'
BT = chr(96)
LAUNCH_DECISIONS = frozenset({
    'order_launched', 'sortie_departed_observed', 'sortie_launch_confirmed',
})
MONTH_RE = re.compile(r'^([0-9]{1,2})-([0-9]{1,2})$')
SIGNAL_LABELS = {
    'order_start_without_launch': '出撃開始後に発射されない',
    'launch_confirmation_missing': '出撃成立を確認できない',
    'departure_cancelled': '出撃が取り消される',
    'monthly_scan_hold': '月次処理がマップを占有',
    'monthly_scan_yield': '月次処理を出撃へ譲ったが発射なし',
    'camp_recall_loop': '野営帰還を繰り返す',
    'battle_grind': '戦闘が長引く',
    'unread_hold': '未読画面で保留',
    'plan_all_settled': '計画の指示が尽きる',
    'no_orders': '開始可能な指示がない',
    'chart_adjust_stale': '調整チャート待ち',
    'no_source': '出撃元の将軍がいない',
    'stuck_march': '行軍が停滞',
    'ledger_unknown': '月内出撃数を確認できない',
    'unknown': '原因未分類',
}
SIGNAL_ORDER = tuple(SIGNAL_LABELS)
# One stable issue signature per month: the first matching primary cause.
# The full signal list stays in the record for the timeline and evidence.
PRIMARY_LABELS = {
    'plan_starved': '計画の指示が尽きる',
    'departure_failure': '出撃開始後に発射されない',
    'monthly_scan': '月次処理がマップを占有',
    'camp_recall': '野営帰還を繰り返す',
    'battle': '戦闘が長引く',
    'unread': '未読画面で保留',
    'no_source': '出撃元の将軍がいない',
    'stuck_march': '行軍が停滞',
    'ledger_unknown': '月内出撃数を確認できない',
    'unknown': '原因未分類',
}
PRIMARY_SIGNALS = (
    ('plan_starved', ('plan_all_settled', 'no_orders')),
    ('departure_failure', ('order_start_without_launch', 'launch_confirmation_missing',
                           'departure_cancelled')),
    ('monthly_scan', ('monthly_scan_hold', 'monthly_scan_yield')),
    ('camp_recall', ('camp_recall_loop',)),
    ('battle', ('battle_grind',)),
    ('unread', ('unread_hold',)),
    ('no_source', ('no_source',)),
    ('stuck_march', ('stuck_march',)),
    ('ledger_unknown', ('ledger_unknown',)),
)


class AuditError(Exception):
    """Fixed-code audit failure; never carries paths or raw exceptions."""


def _bounded_text(value, limit=MAX_TEXT):
    if not isinstance(value, str):
        return None
    text = ''.join(ch for ch in value if ch >= ' ' and ch != '\x7f').strip()
    return text[:limit] if text else None


def _bounded_int(value, minimum=0, maximum=10 ** 9):
    return value if type(value) is int and minimum <= value <= maximum else None


def _names(value, limit=12):
    if not isinstance(value, list):
        return None
    names = [_bounded_text(item, 48) for item in value[:limit]]
    return [name for name in names if name]


def _text_map(value, limit=12):
    if not isinstance(value, dict):
        return None
    out = {}
    for key, item in list(value.items())[:limit]:
        key = _bounded_text(key, 48) if isinstance(key, str) else None
        item = _bounded_text(item, 48)
        if key and item:
            out[key] = item
    return out or None


def _next_month(month):
    match = MONTH_RE.fullmatch(month) if isinstance(month, str) else None
    if match is None:
        return None
    year, number = int(match.group(1)), int(match.group(2))
    if not 1 <= number <= 12:
        return None
    return f'{year + 1}-1' if number == 12 else f'{year}-{number + 1}'


def _scope(state):
    mem = state.get('policy') if isinstance(state, dict) else None
    if not isinstance(mem, dict):
        return None
    chapter, month = mem.get('chapter'), mem.get('month')
    if type(chapter) is not int or not isinstance(month, str) or not month:
        return None
    return chapter, month


def _identity(state, mem=None):
    mem = mem if isinstance(mem, dict) else {}
    trace = state.get('decision_trace') if isinstance(state, dict) else None
    source = trace if isinstance(trace, dict) else mem.get('_run_identity')
    keys = ('game', 'runtime_id', 'generation', 'lease_id')
    if not isinstance(source, dict):
        return {}
    out = {name: source.get(name) for name in keys}
    if not isinstance(out.get('runtime_id'), str):
        out['runtime_id'] = None
    if type(out.get('generation')) is not int:
        out['generation'] = None
    if not isinstance(out.get('lease_id'), str):
        out['lease_id'] = None
    return out


def _identity_key(identity):
    identity = identity if isinstance(identity, dict) else {}
    return (identity.get('runtime_id'), identity.get('generation'), identity.get('lease_id'))


def _summarize_plan(plan):
    plan = plan if isinstance(plan, dict) else {}
    orders = plan.get('orders') if isinstance(plan.get('orders'), dict) else {}
    chart = plan.get('chart_plan') if isinstance(plan.get('chart_plan'), dict) else {}
    chart_orders = chart.get('orders') if isinstance(chart.get('orders'), list) else []
    return {
        'active': _bounded_text(plan.get('active'), 64),
        'orders': {_bounded_text(step, 64) or '?': _bounded_text(status, 24) or '?'
                   for step, status in list(orders.items())[:60]
                   if isinstance(step, str) and isinstance(status, str)},
        'chart_plan': {
            'source': _bounded_text(chart.get('source'), 32),
            'request_id': _bounded_text(chart.get('request_id'), 32),
            'orders': [{
                'step': _bounded_text(order.get('step'), 64),
                'purpose': _bounded_text(order.get('purpose'), 32),
                'source': _bounded_text(order.get('source'), 48),
                'target': _bounded_text(order.get('target'), 48),
                'general': _bounded_text(order.get('general'), 48),
                'note': _bounded_text(order.get('note'), 120),
                'after': _bounded_text(order.get('after'), 48),
            } for order in chart_orders[:MAX_LIST] if isinstance(order, dict)],
        },
    }


def _summarize_world(world, tick):
    world = world if isinstance(world, dict) else {}
    tick = tick if type(tick) is int else None
    garrison = world.get('garrison') if isinstance(world.get('garrison'), dict) else {}
    sorties = world.get('sorties') if isinstance(world.get('sorties'), dict) else {}
    launched = world.get('launched_orders') if isinstance(world.get('launched_orders'), dict) else {}
    history = world.get('sortie_history') if isinstance(world.get('sortie_history'), list) else []
    ownership = world.get('castle_ownership') if isinstance(world.get('castle_ownership'), dict) else {}

    def age(value):
        started = value.get('tick') if isinstance(value, dict) else None
        if tick is None or type(started) is not int or started < 0 or tick < started:
            return None
        return min(tick - started, 10 ** 7)

    return {
        'garrison': {_bounded_text(castle, 48) or '?': _names(names, 12)
                     for castle, names in list(garrison.items())[:12]
                     if isinstance(castle, str)},
        'sorties': {_bounded_text(step, 64) or '?': {
            'status': _bounded_text(value.get('status'), 32),
            'general': _bounded_text(value.get('general'), 48),
            'source': _bounded_text(value.get('source'), 48),
            'target': _bounded_text(value.get('target') or value.get('planned_target'), 48),
            'purpose': _bounded_text(value.get('purpose'), 32),
            'age_ticks': age(value),
        } for step, value in list(sorties.items())[:MAX_LIST] if isinstance(value, dict)},
        'launched_orders': {_bounded_text(step, 64) or '?': {
            'target': _bounded_text(value.get('target'), 48),
            'source': _bounded_text(value.get('source'), 48),
            'general': _bounded_text(value.get('general'), 48),
            'purpose': _bounded_text(value.get('purpose'), 32),
            'age_ticks': age(value),
        } for step, value in list(launched.items())[:MAX_LIST] if isinstance(value, dict)},
        'captured': _names(world.get('captured'), 20),
        'lost': _names(world.get('lost'), 20),
        'castle_ownership': _text_map(ownership, 16),
        'gold': _bounded_int(world.get('gold'), 0, 10 ** 7),
        'sortie_history': [{
            'outcome': _bounded_text(row.get('outcome'), 24),
            'general': _bounded_text(row.get('general'), 48),
            'target': _bounded_text(row.get('target'), 48),
            'tick': _bounded_int(row.get('tick'), 0, 10 ** 9),
            'age_ticks': age(row),
        } for row in history[-8:] if isinstance(row, dict)],
    }


def _summarize_flow(flow):
    flow = flow if isinstance(flow, dict) else {}
    keys = {
        'month_sub': ('stage', 'kind', 'step', 'attempts', 'reads'),
        'post_month_status': ('stage', 'kind', 'phase_reads', 'floor_yields', 'attempts'),
        'house': ('stage', 'goal', 'kind'),
        'recall': ('stage', 'steps', 'target', 'kind'),
        'departure_request': ('status', 'step', 'general', 'target'),
        'scene_hold': ('stage', 'kind'),
        'yes_no_unread': ('stage', 'kind'),
        'army_count_check': ('stage', 'result'),
    }
    out = {}
    for name, fields in keys.items():
        value = flow.get(name)
        if not isinstance(value, dict):
            continue
        row = {}
        for field in fields:
            item = value.get(field)
            if isinstance(item, str):
                row[field] = _bounded_text(item, 48)
            elif type(item) is int:
                row[field] = _bounded_int(item, 0, 10 ** 7)
            elif type(item) is bool:
                row[field] = item
        if row:
            out[name] = row
    for name, value in list(flow.items())[:24]:
        if name in out or not isinstance(name, str) or not isinstance(value, dict):
            continue
        row = {}
        for field in ('stage', 'kind', 'status', 'result', 'attempts', 'reads', 'steps'):
            item = value.get(field)
            if isinstance(item, str):
                row[field] = _bounded_text(item, 48)
            elif type(item) is int:
                row[field] = _bounded_int(item, 0, 10 ** 7)
        if row:
            out[name] = row
    return out


def _summarize_stats(mem):
    stats = mem.get('stats') if isinstance(mem.get('stats'), dict) else {}
    tally = mem.get('tally') if isinstance(mem.get('tally'), dict) else {}
    return {
        'wins': _bounded_int(stats.get('wins'), 0, 10 ** 6),
        'losses': _bounded_int(stats.get('losses'), 0, 10 ** 6),
        'unclassified': _bounded_int(stats.get('unclassified'), 0, 10 ** 6),
        'cards_confirmed': _bounded_int(stats.get('cards_confirmed'), 0, 10 ** 6),
        'battles_started': _bounded_int(tally.get('battles_started'), 0, 10 ** 6),
        'battles_judged': _bounded_int(tally.get('battles_judged'), 0, 10 ** 6),
        'castle_losses': _bounded_int(tally.get('castle_losses'), 0, 10 ** 6),
    }


def _world_snapshot(mem):
    """WORLD region plus un-migrated top-level observation keys.

    gold, captured, lost, castle_ownership and sortie_history still live at
    the policy top level in long runs (the region migration is gradual).  A
    record that read only mem['world'] showed gold=None and no captures even
    though the state had them.
    """
    from .state.regions import WORLD, region
    data = dict(region(mem, WORLD))
    for key in ('gold', 'captured', 'lost', 'castle_ownership', 'sortie_history'):
        if data.get(key) is None:
            data[key] = mem.get(key)
    return data


def _context(state):
    """Bounded game context from one observation; never copies the whole mem."""
    if not isinstance(state, dict):
        return None
    mem = state.get('policy')
    if not isinstance(mem, dict):
        return None
    scope = _scope(state)
    if scope is None:
        return None
    from .state.regions import FLOW, PLAN, region
    world = _world_snapshot(mem)
    plan = region(mem, PLAN)
    flow = region(mem, FLOW)
    tick = _bounded_int(mem.get('tick'), 0, 10 ** 9)
    durations = mem.get('month_durations')
    durations = durations if isinstance(durations, list) else []
    durations = [value for value in durations[-6:]
                 if type(value) is int and 0 <= value <= 10 ** 7]
    return {
        'chapter': scope[0],
        'month': scope[1],
        'tick': tick,
        'month_start_tick': _bounded_int(mem.get('month_start_tick'), 0, 10 ** 9),
        'month_durations': durations,
        'bot_version': _bounded_text(state.get('bot_version'), 80),
        'screen_kind': _bounded_text(state.get('screen_kind'), 32),
        'phase': _bounded_text(state.get('phase'), 32),
        'plan': _summarize_plan(plan),
        'world': _summarize_world(world, tick),
        'flow': _summarize_flow(flow),
        'stats': _summarize_stats(mem),
        'month_sorties': _copy_month_sorties(mem.get('month_sorties')),
    }


def _copy_month_sorties(value):
    if not isinstance(value, dict):
        return None
    scope = value.get('scope')
    scope = [scope[0], scope[1]] if (isinstance(scope, list) and len(scope) == 2
                                     and type(scope[0]) is int and isinstance(scope[1], str)) else None
    launches = _bounded_int(value.get('launches'), 0, 10 ** 6)
    return {'scope': scope, 'launches': launches}


def capture(state):
    """Bounded snapshot of the pre-decide state, or None when not ready."""
    try:
        return _context(state)
    except (AttributeError, KeyError, TypeError, ValueError, RecursionError):
        return None


def _new_state(identity):
    return {
        'schema': SCHEMA,
        'identity': identity or {},
        'observed': None,
        'pending': None,
        'last_close': None,
        'last_sealed': None,
        'write_tick': 0,
    }


def _read_state(runtime):
    path = Path(runtime) / AUDIT_FILE
    try:
        if path.is_symlink() or not path.is_file():
            return None
        if path.stat().st_size > MAX_STATE_BYTES:
            return None
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get('schema') != SCHEMA:
        return None
    return data


def _write_state(runtime, state):
    path = Path(runtime) / AUDIT_FILE
    if path.is_symlink():
        raise AuditError('unsafe_audit_state')
    from ..game_switch import atomic_write_json
    atomic_write_json(path, state)


def _open_month(before, after, now, *, started_clean):
    mem = after.get('policy') or {}
    tick = _bounded_int(mem.get('tick'), 0, 10 ** 9)
    month_sorties = _copy_month_sorties(mem.get('month_sorties'))
    scope = [mem.get('chapter'), mem.get('month')]
    ledger_ok = (month_sorties is not None and month_sorties.get('scope') == scope
                 and month_sorties.get('launches') is not None)
    return {
        'chapter': mem.get('chapter'),
        'month': mem.get('month'),
        'first_at': float(now),
        'first_tick': tick,
        'last_at': float(now),
        'last_tick': tick,
        'observations': 1,
        'max_gap_s': 0.0,
        'started_clean': bool(started_clean),
        'ledger_scope': month_sorties.get('scope') if ledger_ok else None,
        'ledger_launches': month_sorties.get('launches') if ledger_ok else None,
        'bot_version': _bounded_text(after.get('bot_version'), 80),
    }


def _touch_month(observed, after, now):
    mem = after.get('policy') or {}
    gap = max(0.0, float(now) - float(observed.get('last_at') or now))
    observed['max_gap_s'] = max(float(observed.get('max_gap_s') or 0.0), gap)
    observed['last_at'] = float(now)
    tick = _bounded_int(mem.get('tick'), 0, 10 ** 9)
    if tick is not None:
        observed['last_tick'] = tick
    observed['observations'] = int(observed.get('observations') or 0) + 1
    version = _bounded_text(after.get('bot_version'), 80)
    if version:
        observed['bot_version'] = version
    month_sorties = _copy_month_sorties(mem.get('month_sorties'))
    scope = [observed.get('chapter'), observed.get('month')]
    if (month_sorties is not None and month_sorties.get('scope') == scope
            and month_sorties.get('launches') is not None):
        observed['ledger_scope'] = month_sorties['scope']
        observed['ledger_launches'] = month_sorties['launches']


def _read_decisions(runtime, since, until, identity):
    runtime = Path(runtime)
    rows = []
    for name in ('hanjuku_decisions.previous.jsonl', 'hanjuku_decisions.jsonl'):
        path = runtime / name
        try:
            if path.is_symlink() or not path.is_file():
                continue
            size = path.stat().st_size
            offset = max(0, size - MAX_READ_BYTES)
            with path.open('rb') as stream:
                stream.seek(offset)
                raw = stream.read(MAX_READ_BYTES + 1)
            if offset:
                raw = raw.partition(b'\n')[2]
            lines = raw.decode('utf-8', errors='replace').splitlines()
        except (OSError, ValueError):
            continue
        for line in lines:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict) or row.get('event') != 'decision':
                continue
            at = row.get('at')
            if type(at) not in (int, float) or not since <= at <= until:
                continue
            if identity and any(row.get(name) not in (None, identity.get(name))
                                for name in ('runtime_id', 'generation', 'lease_id')):
                continue
            rows.append(row)
    rows.sort(key=lambda row: row.get('at') or 0)
    return rows


def _coverage(rows, since, until):
    if not rows:
        return 0.0
    first = rows[0].get('at')
    last = rows[-1].get('at')
    if type(first) not in (int, float) or type(last) not in (int, float):
        return 0.0
    span = max(1.0, float(until) - float(since))
    covered = max(0.0, min(float(until), float(last)) - max(float(since), float(first)))
    return min(1.0, covered / span)


def _departures(rows):
    keys = set()
    events = 0
    for row in rows:
        decision = row.get('decision')
        if decision not in LAUNCH_DECISIONS:
            continue
        # A move into our own castle is a transfer, not an enemy sortie.
        if row.get('purpose') == 'move':
            continue
        events += 1
        step = row.get('chart_step')
        if isinstance(step, str) and step:
            keys.add(step)
        else:
            keys.add(f"{decision}:{row.get('at')}")
    return len(keys), events


def _decision_counts(rows):
    counts = Counter(row.get('decision') for row in rows
                     if isinstance(row.get('decision'), str))
    return {name: count for name, count in counts.most_common(30)}


def _decision_rows(rows, limit):
    out = []
    for row in rows[-limit:]:
        out.append({
            'at': row.get('at') if type(row.get('at')) in (int, float) else None,
            'decision': _bounded_text(row.get('decision'), 64),
            'chart_step': _bounded_text(row.get('chart_step'), 64),
            'screen_kind': _bounded_text(row.get('screen_kind'), 32),
            'general': _bounded_text(row.get('general'), 48),
            'source': _bounded_text(row.get('source'), 48),
            'target': _bounded_text(row.get('target'), 48),
            'purpose': _bounded_text(row.get('purpose'), 32),
            'reason': _bounded_text(row.get('reason'), 120),
        })
    return out


def _outcomes(rows):
    outcomes = Counter()
    for row in rows:
        if row.get('decision') != 'sortie_outcome':
            continue
        metric = row.get('observed_metric')
        outcome = metric.get('outcome') if isinstance(metric, dict) else None
        if isinstance(outcome, str):
            outcomes[outcome] += 1
    return dict(outcomes)


def _signals(context, counts, confirmed, departures, battle_count):
    out = []
    flow = (context or {}).get('flow') or {}
    world = (context or {}).get('world') or {}
    plan = (context or {}).get('plan') or {}
    orders = plan.get('orders') if isinstance(plan.get('orders'), dict) else {}
    if counts.get('order_start') and not departures:
        out.append('order_start_without_launch')
    if counts.get('sortie_launch_unverified') and not departures:
        out.append('launch_confirmation_missing')
    if counts.get('sortie_cancelled_observed'):
        out.append('departure_cancelled')
    if counts.get('post_month_sortie_floor'):
        out.append('monthly_scan_yield')
    if counts.get('post_month_status') or counts.get('post_month_status_close_probe') \
            or counts.get('month_sub_step') or flow.get('post_month_status') or flow.get('month_sub'):
        out.append('monthly_scan_hold')
    camp = sum(counts.get(name, 0) for name in counts if 'camp' in name or name.startswith('recall'))
    if camp >= 10:
        out.append('camp_recall_loop')
    if battle_count >= 20 or counts.get('battle_draw_hold', 0) >= 10:
        out.append('battle_grind')
    unread = counts.get('unknown_hold_advance', 0) + counts.get('situation_held', 0)
    if unread >= 30 or flow.get('scene_hold') or flow.get('yes_no_unread'):
        out.append('unread_hold')
    if orders:
        if all(status in ('failed', 'done') for status in orders.values()):
            out.append('plan_all_settled')
    else:
        out.append('no_orders')
    if counts.get('chart_adjust_request'):
        out.append('chart_adjust_stale')
    garrison = world.get('garrison') if isinstance(world.get('garrison'), dict) else {}
    staffed = [len(names) for names in garrison.values() if isinstance(names, list)]
    if not staffed or max(staffed) <= 1:
        out.append('no_source')
    sorties = world.get('sorties') if isinstance(world.get('sorties'), dict) else {}
    if any(isinstance(row, dict) and (row.get('age_ticks') or 0) > 3000
           for row in sorties.values()):
        out.append('stuck_march')
    if confirmed is None:
        out.append('ledger_unknown')
    seen, result = set(), []
    for name in out:
        if name in seen:
            continue
        seen.add(name)
        result.append(name)
    result.sort(key=lambda name: SIGNAL_ORDER.index(name) if name in SIGNAL_ORDER else len(SIGNAL_ORDER))
    return result[:8]


def _confidence(observed, context, ledger_ok, chapter_changed, irregular, coverage, duration_s):
    reasons = []
    if not observed.get('started_clean'):
        reasons.append('first_partial_month')
    if chapter_changed:
        reasons.append('chapter_change')
    if irregular:
        reasons.append('month_jump')
    if not ledger_ok:
        reasons.append('ledger_scope_mismatch')
    if float(observed.get('max_gap_s') or 0) > MAX_GAP_SECONDS:
        reasons.append('observation_gap')
    if duration_s < MIN_MONTH_SECONDS:
        reasons.append('short_month')
    if int(observed.get('observations') or 0) < MIN_OBSERVATIONS:
        reasons.append('few_observations')
    if coverage < 0.9:
        reasons.append('decision_log_coverage')
    if context is None:
        reasons.append('context_missing')
    soft = {'observation_gap', 'decision_log_coverage'}
    if not reasons:
        return 'high', reasons
    if len(reasons) == 1 and reasons[0] in soft:
        return 'medium', reasons
    return 'low', reasons


def _month_summary(observed, close_at):
    first_at = float(observed.get('first_at') or close_at)
    last_at = float(observed.get('last_at') or close_at)
    first_tick = observed.get('first_tick')
    last_tick = observed.get('last_tick')
    return {
        'chapter': observed.get('chapter'),
        'month': observed.get('month'),
        'first_at': first_at,
        'last_at': last_at,
        'closed_at': float(close_at),
        'duration_s': max(0.0, last_at - first_at),
        'first_tick': first_tick,
        'last_tick': last_tick,
        'duration_ticks': (last_tick - first_tick
                           if type(first_tick) is int and type(last_tick) is int
                           and last_tick >= first_tick else None),
        'observations': int(observed.get('observations') or 0),
        'max_gap_s': float(observed.get('max_gap_s') or 0.0),
        'started_clean': bool(observed.get('started_clean')),
    }


def _primary_signal(signals):
    for name, members in PRIMARY_SIGNALS:
        if any(signal in members for signal in signals):
            return name
    return 'unknown'


def _incident_id(identity, chapter, month):
    raw = f"{identity.get('runtime_id')}|{identity.get('generation')}|{chapter}|{month}"
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:20]


def _fingerprint(chapter, kind, signals):
    raw = f"{chapter}|{kind}|{'+'.join(signals)}"
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]


def _shrink(record):
    """Drop the largest optional evidence until the record fits the bound."""
    while len(json.dumps(record, ensure_ascii=False).encode('utf-8')) > MAX_RECORD_BYTES:
        node = None
        path = None
        for candidate in (('decisions', 'before'), ('after', 'rows'),
                          ('plan', 'chart_plan', 'orders'), ('world', 'sortie_history'),
                          ('world', 'garrison')):
            node = record
            for key in candidate:
                node = node.get(key) if isinstance(node, dict) else None
            if isinstance(node, (list, dict)) and node:
                path = candidate
                break
        if path is None:
            raise AuditError('incident_exceeds_record_bound')
        node = record
        for key in path[:-1]:
            node = node[key]
        current = node[path[-1]]
        if isinstance(current, list):
            node[path[-1]] = current[:len(current) // 2]
        else:
            node[path[-1]] = dict(list(current.items())[:len(current) // 2])
        record['truncated'] = True
    return record


def _build_incident(runtime, observed, context, rows, close_at, identity, *, kind, confirmed,
                    departures, launch_events, coverage, confidence, reasons,
                    chapter_changed, irregular):
    counts = _decision_counts(rows)
    battle_count = sum(counts.get(name, 0) for name in counts
                       if name.startswith('battle_') or name == 'monster_menu_choice')
    signals = _signals(context, counts, confirmed, departures, battle_count)
    move_launches = sum(1 for row in rows if row.get('decision') == 'order_launched'
                        and row.get('purpose') == 'move')
    record = {
        'schema': SCHEMA,
        'event': {'zero_sortie': 'month_zero_sortie',
                  'zero_unverified': 'month_zero_unverified',
                  'unconfirmed_launches': 'month_unconfirmed_launches'}.get(
                      kind, 'month_unconfirmed_launches'),
        'kind': kind,
        'confidence': confidence,
        'confidence_reasons': reasons,
        'chapter': observed.get('chapter'),
        'month': observed.get('month'),
        'month_summary': _month_summary(observed, close_at),
        'sorties': {
            'confirmed': confirmed,
            'departures': departures,
            'launch_events': launch_events,
            'order_start': counts.get('order_start', 0),
            'order_launched': counts.get('order_launched', 0),
            'sortie_launch_confirmed': counts.get('sortie_launch_confirmed', 0),
            'sortie_launch_unverified': counts.get('sortie_launch_unverified', 0),
            'sortie_launch_failed': counts.get('sortie_launch_failed', 0),
            'sortie_departure_requested': counts.get('sortie_departure_requested', 0),
            'sortie_cancelled_observed': counts.get('sortie_cancelled_observed', 0),
            'sortie_outcomes': _outcomes(rows),
            'moves': move_launches,
            'house_dispatches': counts.get('goal_dispatch', 0),
        },
        'plan': copy.deepcopy((context or {}).get('plan') or {}),
        'world': copy.deepcopy((context or {}).get('world') or {}),
        'flow': copy.deepcopy((context or {}).get('flow') or {}),
        'stats': copy.deepcopy((context or {}).get('stats') or {}),
        'battles': {'decisions': battle_count},
        'signals': signals,
        'decisions': {
            'total': len(rows),
            'counts': counts,
            'coverage': round(coverage, 3),
            'before': _decision_rows(rows, 30),
            'after': None,
        },
        'evidence': {
            'runtime_dir': str(Path(runtime).name),
            'decisions': 'hanjuku_decisions.jsonl',
            'events': 'hanjuku_events.jsonl',
            'frames': 'hanjuku_frames/',
            'bot_version': observed.get('bot_version'),
            'identity': identity,
        },
        'context_missing': context is None,
        'chapter_changed': bool(chapter_changed),
        'irregular_month': bool(irregular),
        'created_at': float(close_at),
    }
    record['incident_id'] = _incident_id(identity, observed.get('chapter'), observed.get('month'))
    primary = _primary_signal(signals)
    record['signature'] = primary
    record['fingerprint'] = _fingerprint(observed.get('chapter'), kind, [primary])
    record['auto_file'] = kind == 'zero_sortie' and confidence == 'high'
    return _shrink(record)


def _after_summary(rows, close_at, window_end):
    departures, events = _departures(rows)
    first = min((row.get('at') for row in rows
                 if row.get('decision') in LAUNCH_DECISIONS
                 and type(row.get('at')) in (int, float)), default=None)
    return {
        'window_start_at': float(close_at),
        'window_end_at': float(window_end),
        'complete': float(window_end) >= float(close_at) + AFTER_WINDOW_SECONDS,
        'decisions': len(rows),
        'counts': _decision_counts(rows),
        'departures': departures,
        'launch_events': events,
        'recovered': departures > 0,
        'first_launch_seconds': (round(float(first) - float(close_at), 1)
                                 if first is not None else None),
        'outcomes': _outcomes(rows),
        'rows': _decision_rows(rows, 20),
    }


def _append_incident(runtime, record):
    from ..hanjuku_run import append_log
    append_log(Path(runtime), LOG_NAME, record)


def _seal(runtime, state, now):
    pending = state.get('pending')
    if not isinstance(pending, dict):
        return False
    record = pending.get('incident')
    if not isinstance(record, dict):
        state['pending'] = None
        return False
    window_end = min(float(now), float(pending.get('window_end_at') or now))
    rows = _read_decisions(runtime, pending.get('window_start_at') or now,
                           window_end, state.get('identity'))
    record['after'] = _after_summary(rows, pending.get('window_start_at') or now, window_end)
    record['sealed_at'] = float(now)
    record['after']['early'] = float(now) < float(pending.get('window_end_at') or now)
    record = _shrink(record)
    _append_incident(runtime, record)
    state['pending'] = None
    state['last_sealed'] = {
        'incident_id': record.get('incident_id'),
        'kind': record.get('kind'),
        'month': record.get('month'),
        'at': float(now),
    }
    _write_state(runtime, state)
    return True


def _seal_if_due(runtime, state, now):
    pending = state.get('pending')
    if not isinstance(pending, dict):
        return False
    if float(now) < float(pending.get('window_end_at') or 0):
        return False
    return _seal(runtime, state, now)


def _close_month(runtime, state, before, after, now):
    observed = state.get('observed')
    if not isinstance(observed, dict):
        return
    chapter, month = observed.get('chapter'), observed.get('month')
    close_at = float(now)
    context = before if (isinstance(before, dict)
                         and (before.get('chapter'), before.get('month')) == (chapter, month)) else None
    ledger_scope = observed.get('ledger_scope')
    confirmed = observed.get('ledger_launches')
    if context is not None:
        month_sorties = context.get('month_sorties')
        if isinstance(month_sorties, dict):
            ledger_scope = month_sorties.get('scope')
            confirmed = month_sorties.get('launches')
    ledger_ok = ledger_scope == [chapter, month] and type(confirmed) is int
    if not ledger_ok:
        confirmed = None
    after_mem = after.get('policy') or {}
    after_chapter = after_mem.get('chapter')
    after_month = after_mem.get('month')
    chapter_changed = type(after_chapter) is int and after_chapter != chapter
    if type(after_chapter) is not int:
        chapter_changed = True
    next_month = _next_month(month)
    irregular = bool(next_month is not None and isinstance(after_month, str)
                     and after_month != next_month)
    if next_month is None:
        irregular = True
    observed = dict(observed)
    # The gap between the last in-month observation and the boundary counts:
    # a launch or capture can hide in it, so the month is not fully observed.
    observed['max_gap_s'] = max(float(observed.get('max_gap_s') or 0.0),
                                max(0.0, close_at - float(observed.get('last_at') or close_at)))
    since = float(observed.get('first_at') or close_at)
    until = float(observed.get('last_at') or close_at)
    rows = _read_decisions(runtime, since, until, state.get('identity'))
    coverage = _coverage(rows, since, until)
    departures, launch_events = _departures(rows)
    duration_s = max(0.0, until - since)
    confidence, reasons = _confidence(observed, context, ledger_ok, chapter_changed,
                                      irregular, coverage, duration_s)
    if not observed.get('started_clean'):
        kind = 'skipped'
    elif chapter_changed:
        kind = 'skipped'
    elif irregular:
        kind = 'skipped'
    elif departures == 0 and confirmed == 0 and confidence == 'high':
        kind = 'zero_sortie'
    elif departures == 0 and (confirmed is None or confidence != 'high'):
        kind = 'zero_unverified'
    elif departures > 0 and confirmed == 0:
        kind = 'unconfirmed_launches'
    else:
        kind = 'ok'
    state['last_close'] = {
        'chapter': chapter,
        'month': month,
        'kind': kind,
        'confidence': confidence,
        'confirmed': confirmed,
        'departures': departures,
        'at': close_at,
    }
    if kind in ('zero_sortie', 'zero_unverified', 'unconfirmed_launches'):
        if state.get('pending'):
            _seal(runtime, state, close_at)
        record = _build_incident(
            runtime, observed, context, rows, close_at, state.get('identity'), kind=kind,
            confirmed=confirmed, departures=departures, launch_events=launch_events,
            coverage=coverage, confidence=confidence, reasons=reasons,
            chapter_changed=chapter_changed, irregular=irregular)
        state['pending'] = {
            'incident': record,
            'window_start_at': close_at,
            'window_end_at': close_at + AFTER_WINDOW_SECONDS,
        }
    # The boundary we just saw is the next month's start, so it is fully
    # observed even when the month that just closed was the first (partial)
    # month of this run. A chapter change or a month jump is different.
    state['_next_started_clean'] = not chapter_changed and not irregular


def observe(runtime, before, after, records=None, *, now=None):
    """Update the audit for one observation; never raises into the game loop."""
    runtime = Path(runtime)
    now = time.time() if now is None else float(now)
    if not isinstance(after, dict):
        return None
    mem = after.get('policy')
    if not isinstance(mem, dict):
        return None
    chapter, month = mem.get('chapter'), mem.get('month')
    if type(chapter) is not int or not isinstance(month, str) or not month:
        return None
    identity = _identity(after, mem)
    state = _read_state(runtime)
    if (not isinstance(state, dict) or state.get('schema') != SCHEMA
            or _identity_key(state.get('identity')) != _identity_key(identity)):
        state = _new_state(identity)
    force = False
    if _seal_if_due(runtime, state, now):
        force = True
    observed = state.get('observed')
    if not isinstance(observed, dict):
        state['observed'] = _open_month(before, after, now, started_clean=False)
        force = True
    elif (observed.get('chapter'), observed.get('month')) != (chapter, month):
        _close_month(runtime, state, before, after, now)
        started = bool(state.pop('_next_started_clean', False))
        state['observed'] = _open_month(before, after, now, started_clean=started)
        force = True
    else:
        _touch_month(observed, after, now)
    state['write_tick'] = int(state.get('write_tick') or 0) + 1
    # The open-month counters only advance if every observation persists them:
    # a write every tenth observation restarted from the last file and could
    # never accumulate past one. The record is about one kilobyte, far below
    # the bot state the same process already writes.
    _write_state(runtime, state)
    return state


def load_incidents(runtime):
    """Sealed incidents, oldest first; never raises on a malformed line."""
    runtime = Path(runtime)
    rows = []
    for name in (f'{LOG_NAME}.jsonl', f'{LOG_NAME}.previous.jsonl'):
        path = runtime / name
        try:
            if path.is_symlink() or not path.is_file():
                continue
            with path.open(encoding='utf-8', errors='ignore') as stream:
                for line in stream:
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(row, dict) and row.get('schema') == SCHEMA:
                        rows.append(row)
        except OSError:
            continue
    rows.sort(key=lambda row: row.get('created_at') or 0)
    return rows


def _md(value):
    if value is None:
        return ''
    return str(value).replace('|', '\\|').replace('\n', ' ')[:160]


def _clock(value):
    if type(value) not in (int, float):
        return ''
    return dt.datetime.fromtimestamp(value, dt.timezone.utc).strftime('%H:%M:%SZ')


def _cell(value):
    text = _md(value)
    return f'{BT}{text}{BT}' if text else ''


def _timeline(record):
    rows = (record.get('decisions') or {}).get('before') or []
    noisy = {'situation_held', 'unknown_hold_advance', 'battle_draw_hold'}
    useful = [row for row in rows if row.get('decision') not in noisy]
    if useful:
        return useful[-20:], len(useful) != len(rows)
    return rows[-3:], True


def render(record):
    """Deterministic Markdown for one incident."""
    if not isinstance(record, dict):
        raise AuditError('invalid_incident')
    month = record.get('month_summary') or {}
    sorties = record.get('sorties') or {}
    signals = record.get('signals') or []
    labels = [SIGNAL_LABELS.get(name, name) for name in signals]
    labels_text = ' / '.join(labels) if labels else '未分類'
    identity = (record.get('evidence') or {}).get('identity') or {}
    kind = record.get('kind')
    confidence = record.get('confidence')
    body = [
        f"<!-- hanjuku-month-audit fingerprint={record.get('fingerprint')} -->",
        f"<!-- hanjuku-month-audit occurrence={record.get('incident_id')} -->",
        f"## {TITLE_PREFIX}: ch{record.get('chapter')} {record.get('month')}",
        '',
        f"- 判定: **{kind}** (confidence: {BT}{confidence}{BT})",
        f"- シグネチャ: {labels_text}",
        f"- ラン: {BT}{identity.get('runtime_id')}{BT} / generation {BT}{identity.get('generation')}{BT} / {BT}{(record.get('evidence') or {}).get('bot_version')}{BT}",
        f"- 月: ch{month.get('chapter')} {month.get('month')}（観測 {month.get('observations')}回 / {round(float(month.get('duration_s') or 0), 1)}秒 / max gap {round(float(month.get('max_gap_s') or 0), 1)}秒）",
        f"- 出撃: 確定 {sorties.get('confirmed')} / 発射 {sorties.get('departures')} / order_start {sorties.get('order_start')} / order_launched {sorties.get('order_launched')}",
        f"- 判定理由: {', '.join(record.get('confidence_reasons') or []) or 'なし'}",
        '',
        '### 月内の決定',
        '',
    ]
    rows, filtered = _timeline(record)
    if filtered:
        body.append('定常的な保留（situation_held など）は決定内訳に集計し、ここでは省略する。')
        body.append('')
    body += [
        '| 時刻 | 決定 | step | 将軍 | 対象 | 理由 |',
        '| --- | --- | --- | --- | --- | --- |',
    ]
    for row in rows:
        body.append(f"| {_clock(row.get('at'))} | {_cell(row.get('decision'))} | {_cell(row.get('chart_step'))} | {_md(row.get('general'))} | {_md(row.get('target'))} | {_md(row.get('reason'))} |")
    body += ['', '### 前月の計画', '',
             '| step | 状態 | 目的 | 出撃元 | 対象 | 将軍 |',
             '| --- | --- | --- | --- | --- | --- |']
    plan = record.get('plan') or {}
    orders = plan.get('orders') if isinstance(plan.get('orders'), dict) else {}
    chart_orders = ((plan.get('chart_plan') or {}).get('orders') or [])
    detail = {row.get('step'): row for row in chart_orders if isinstance(row, dict)}
    for step, status in list(orders.items())[:30]:
        row = detail.get(step) or {}
        body.append(f"| {_cell(step)} | {_md(status)} | {_md(row.get('purpose'))} | {_md(row.get('source'))} | {_md(row.get('target'))} | {_md(row.get('general'))} |")
    body += ['', '### 世界状態', '']
    world = record.get('world') or {}
    body.append(f"- 所持金: {world.get('gold')}")
    body.append(f"- 奪取: {', '.join(world.get('captured') or []) or 'なし'}")
    body.append(f"- 失城: {', '.join(world.get('lost') or []) or 'なし'}")
    garrison = world.get('garrison') or {}
    body.append('- 在城: ' + (' / '.join(f"{castle}({len(names)})"
                                         for castle, names in garrison.items()) or '未確認'))
    in_flight = world.get('sorties') or {}
    if in_flight:
        body.append('- 行軍中: ' + ', '.join(
            f"{step}:{row.get('status')}({row.get('age_ticks')}t)"
            for step, row in list(in_flight.items())[:8]))
    body += ['', '### 決定内訳', '']
    counts = (record.get('decisions') or {}).get('counts') or {}
    body.append(' / '.join(f"{BT}{name}{BT}:{count}" for name, count in list(counts.items())[:20]))
    after = record.get('after') or {}
    if after:
        body += ['', '### 翌5分', '',
                 f"- 決定 {after.get('decisions')}件 / 出撃 {after.get('departures')}件 / 回復: {'yes' if after.get('recovered') else 'no'} / 初出撃まで {after.get('first_launch_seconds')}秒{'（early seal）' if after.get('early') else ''}"]
    body += ['', '### 証拠', '',
             f"- runtime: {BT}{(record.get('evidence') or {}).get('runtime_dir')}{BT}",
             f"- decisions: {BT}{(record.get('evidence') or {}).get('decisions')}{BT} / events: {BT}{(record.get('evidence') or {}).get('events')}{BT}",
             f"- frames: {BT}{(record.get('evidence') or {}).get('frames')}{BT}",
             f"- incident_id: {BT}{record.get('incident_id')}{BT}",
             '', '<details><summary>JSON</summary>', '',
             f"{BT * 3}json",
             json.dumps(record, ensure_ascii=False, sort_keys=True, indent=1)[:24000],
             f"{BT * 3}", '', '</details>']
    return '\n'.join(body)


def render_comment(record):
    """Compact recurrence comment for an existing open issue."""
    month = record.get('month_summary') or {}
    sorties = record.get('sorties') or {}
    labels = [SIGNAL_LABELS.get(name, name) for name in record.get('signals') or []]
    after = record.get('after') or {}
    return '\n'.join([
        f"<!-- hanjuku-month-audit fingerprint={record.get('fingerprint')} -->",
        f"<!-- hanjuku-month-audit occurrence={record.get('incident_id')} -->",
        f"### 再発: ch{record.get('chapter')} {record.get('month')}",
        '',
        f"- 判定: **{record.get('kind')}** ({BT}{record.get('confidence')}{BT})",
        f"- 出撃: 確定 {sorties.get('confirmed')} / 発射 {sorties.get('departures')}"
        f" / order_start {sorties.get('order_start')}",
        f"- シグナル: {' / '.join(labels) if labels else '未分類'}",
        f"- 月: 観測 {month.get('observations')}回 / {round(float(month.get('duration_s') or 0), 1)}秒"
        f" / max gap {round(float(month.get('max_gap_s') or 0), 1)}秒",
        f"- 翌5分: 出撃 {after.get('departures')} / 回復 {'yes' if after.get('recovered') else 'no'}",
        f"- incident_id: {BT}{record.get('incident_id')}{BT}",
    ])


def _issue_title(record):
    label = PRIMARY_LABELS.get(record.get('signature'), '原因未分類')
    return f"{TITLE_PREFIX}: ch{record.get('chapter')} {label}"[:100]


class GhError(AuditError):
    pass


def _run_gh(args, *, gh=None):
    if gh is not None:
        result = gh(args)
        if isinstance(result, tuple):
            code, out, err = result
        else:
            code, out, err = 0, result or '', ''
        if code != 0:
            raise GhError((err or 'gh failed')[:200])
        return out
    if shutil.which('gh') is None:
        raise GhError('gh_not_found')
    proc = subprocess.run(['gh', *args], text=True, capture_output=True)
    if proc.returncode != 0:
        raise GhError((proc.stderr or '').strip()[:200] or 'gh_failed')
    return proc.stdout


def _gh_json(args, *, gh=None):
    raw = _run_gh(args, gh=gh)
    try:
        return json.loads(raw)
    except ValueError as exc:
        raise GhError('gh_bad_json') from exc


def _secret_scanner():
    env = os.environ.get('AGENT_GUARDS')
    candidates = []
    if env:
        candidates.append(Path(env))
    try:
        root = Path(__file__).resolve().parents[3]
        candidates += [root.parent / 'agent-guards',
                       root.parent.parent / 'agent-guards',
                       root.parent.parent.parent / 'agent-guards',
                       Path.cwd().parent / 'agent-guards',
                       Path.cwd() / 'agent-guards']
    except (IndexError, OSError):
        pass
    for directory in candidates:
        path = directory / 'secret_scan.py'
        if path.is_file():
            return path
    return None


def _scan_clean(path, *, scanner=None):
    if scanner is not None:
        return bool(scanner(path))
    script = _secret_scanner()
    if script is None:
        raise AuditError('secret_scan_unavailable')
    proc = subprocess.run([sys.executable, str(script), '--quiet', str(path)],
                          text=True, capture_output=True)
    if proc.returncode == 0:
        return True
    if proc.returncode == 1:
        return False
    raise AuditError('secret_scan_failed')


def _gh_auth(gh=None):
    _run_gh(['auth', 'status'], gh=gh)


def _issues(repo, *, gh=None):
    data = _gh_json(['issue', 'list', '--repo', repo, '--state', 'open',
                     '--limit', str(MAX_ISSUES), '--json', 'number,title,body'], gh=gh)
    return data if isinstance(data, list) else []


def _issue_comments(repo, number, *, gh=None):
    data = _gh_json(['issue', 'view', str(number), '--repo', repo,
                     '--json', 'comments'], gh=gh)
    comments = data.get('comments') if isinstance(data, dict) else None
    return comments if isinstance(comments, list) else []


def _find_issue(issues, fingerprint):
    marker = f'hanjuku-month-audit fingerprint={fingerprint}'
    for issue in issues:
        if marker in (issue.get('body') or ''):
            return issue
    return None


def _occurrence_present(comments, incident_id):
    marker = f'hanjuku-month-audit occurrence={incident_id}'
    return any(marker in (comment.get('body') or '') for comment in comments)


def file_issue(runtime, *, repo=DEFAULT_REPO, incident_id=None, dry_run=False,
               min_confidence='high', gh=None, scanner=None, now=None):
    """File or comment one sealed incident through gh; returns a result dict."""
    now = time.time() if now is None else float(now)
    records = load_incidents(runtime)
    if incident_id:
        records = [row for row in records if row.get('incident_id') == incident_id]
    else:
        records = [row for row in records if row.get('auto_file') is True]
    order = {'high': 3, 'medium': 2, 'low': 1}
    records = [row for row in records
               if order.get(row.get('confidence'), 0) >= order.get(min_confidence, 3)]
    if not records:
        return {'status': 'no_incident'}
    record = records[-1]
    fingerprint = record.get('fingerprint')
    title = _issue_title(record)
    result = {'status': 'dry_run' if dry_run else 'unknown',
              'incident_id': record.get('incident_id'), 'fingerprint': fingerprint}
    if dry_run:
        result.update(title=title, body=render(record))
        return result
    _gh_auth(gh=gh)
    issues = _issues(repo, gh=gh)
    existing = _find_issue(issues, fingerprint)
    number = existing.get('number') if existing else None
    text = render_comment(record) if existing else render(record)
    if existing:
        comments = _issue_comments(repo, number, gh=gh)
        if _occurrence_present([existing, *comments], record.get('incident_id')):
            return {**result, 'status': 'already_commented', 'issue': number}
        latest = max((comment.get('createdAt') or '' for comment in comments), default='')
        if latest:
            try:
                age = now - dt.datetime.fromisoformat(latest.replace('Z', '+00:00')).timestamp()
            except (TypeError, ValueError):
                age = None
            if age is not None and 0 <= age < COMMENT_COOLDOWN_SECONDS:
                return {**result, 'status': 'cooldown', 'issue': number}
    with tempfile.NamedTemporaryFile('w', suffix='.md', delete=False,
                                     encoding='utf-8') as stream:
        stream.write(text)
        body_path = Path(stream.name)
    try:
        if not _scan_clean(body_path, scanner=scanner):
            raise AuditError('secret_scan_detected')
        if existing:
            out = _run_gh(['issue', 'comment', str(number), '--repo', repo,
                           '--body-file', str(body_path)], gh=gh)
            return {**result, 'status': 'commented', 'issue': number, 'url': out.strip()}
        out = _run_gh(['issue', 'create', '--repo', repo, '--title', title,
                       '--body-file', str(body_path), '--label', 'bug',
                       '--label', 'auto-generated'], gh=gh)
        return {**result, 'status': 'created', 'url': out.strip()}
    finally:
        try:
            body_path.unlink()
        except OSError:
            pass


def watch_once(state_dir, *, repo=DEFAULT_REPO, gh=None, scanner=None, now=None,
               dry_run=False, seen=None):
    """File new sealed incidents for every runtime under one state directory."""
    root = Path(state_dir) / 'runtimes'
    seen = seen if isinstance(seen, dict) else {}
    results = []
    try:
        if root.is_symlink() or not root.is_dir():
            return results
        entries = sorted(root.iterdir())
    except OSError:
        return results
    for entry in entries:
        try:
            if entry.is_symlink() or not entry.is_dir():
                continue
            log = entry / f'{LOG_NAME}.jsonl'
            if log.is_symlink() or not log.is_file():
                continue
            stat = log.stat()
            signature = (stat.st_mtime_ns, stat.st_size)
            if seen.get(str(entry)) == signature:
                continue
        except OSError:
            continue
        try:
            result = file_issue(entry, repo=repo, gh=gh, scanner=scanner,
                                now=now, dry_run=dry_run)
        except (AuditError, GhError, OSError) as exc:
            result = {'status': 'error', 'error': str(exc)}
        if result.get('status') not in ('error', 'cooldown'):
            seen[str(entry)] = signature
        if result.get('status') != 'no_incident':
            results.append({'runtime_id': entry.name, **result})
    return results


def watch(state_dir, *, repo=DEFAULT_REPO, interval=60.0, once=False, gh=None,
          scanner=None, dry_run=False):
    """Poll runtime audit logs and file new incidents; never starts a service."""
    seen = {}
    while True:
        now = time.time()
        for result in watch_once(state_dir, repo=repo, gh=gh, scanner=scanner,
                                 now=now, dry_run=dry_run, seen=seen):
            print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
        if once:
            return 0
        time.sleep(max(5.0, float(interval)))


def _latest_pending(state):
    pending = state.get('pending') if isinstance(state, dict) else None
    return pending.get('incident') if isinstance(pending, dict) else None


def main(argv=None):
    parser = argparse.ArgumentParser(prog='python3 -m docich.hanjuku.month_audit')
    parser.add_argument('--runtime', default=os.environ.get('HANJUKU_RUNTIME'),
                        help='Hanjuku runtime directory (default: HANJUKU_RUNTIME)')
    sub = parser.add_subparsers(dest='command', required=True)
    show = sub.add_parser('show')
    show.add_argument('--pending', action='store_true')
    sub.add_parser('list')
    render_cmd = sub.add_parser('render')
    render_cmd.add_argument('--incident')
    render_cmd.add_argument('--out')
    file_cmd = sub.add_parser('file')
    file_cmd.add_argument('--repo', default=DEFAULT_REPO)
    file_cmd.add_argument('--incident')
    file_cmd.add_argument('--dry-run', action='store_true')
    file_cmd.add_argument('--min-confidence', default='high', choices=('high', 'medium', 'low'))
    sub.add_parser('finalize')
    watch_cmd = sub.add_parser('watch')
    watch_cmd.add_argument('--state-dir')
    watch_cmd.add_argument('--repo', default=DEFAULT_REPO)
    watch_cmd.add_argument('--interval', type=float, default=60.0)
    watch_cmd.add_argument('--once', action='store_true')
    watch_cmd.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)
    if args.command == 'watch':
        state_dir = args.state_dir
        if not state_dir and args.runtime:
            state_dir = str(Path(args.runtime).parent.parent)
        if not state_dir:
            state_dir = os.environ.get('HANJUKU_STATE_DIR')
        if not state_dir:
            parser.error('--state-dir, --runtime or HANJUKU_STATE_DIR is required')
        try:
            return watch(state_dir, repo=args.repo, interval=args.interval,
                         once=args.once, dry_run=args.dry_run)
        except (AuditError, GhError) as exc:
            print(json.dumps({'status': 'error', 'error': str(exc)}, ensure_ascii=False))
            return 2
    if not args.runtime:
        parser.error('--runtime or HANJUKU_RUNTIME is required')
    runtime = Path(args.runtime)
    try:
        if args.command == 'show':
            state = _read_state(runtime)
            record = _latest_pending(state) if args.pending else None
            if record is None:
                records = load_incidents(runtime)
                record = records[-1] if records else None
            if record is None:
                print('no incident')
                return 1
            print(json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2))
            return 0
        if args.command == 'list':
            for record in load_incidents(runtime):
                print(f"{record.get('incident_id')}\t{record.get('kind')}\t"
                      f"{record.get('confidence')}\tch{record.get('chapter')} {record.get('month')}\t"
                      f"confirmed={((record.get('sorties') or {}).get('confirmed'))}\t"
                      f"departures={((record.get('sorties') or {}).get('departures'))}")
            return 0
        if args.command == 'render':
            records = load_incidents(runtime)
            if args.incident:
                records = [row for row in records if row.get('incident_id') == args.incident]
            if not records:
                print('no incident', file=sys.stderr)
                return 1
            body = render(records[-1])
            if args.out:
                Path(args.out).write_text(body, encoding='utf-8')
            else:
                print(body)
            return 0
        if args.command == 'file':
            result = file_issue(runtime, repo=args.repo, incident_id=args.incident,
                                dry_run=args.dry_run, min_confidence=args.min_confidence)
            print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
            return 0
        if args.command == 'finalize':
            state = _read_state(runtime)
            if not isinstance(state, dict) or not state.get('pending'):
                print('no pending')
                return 1
            _seal(runtime, state, time.time())
            print('sealed')
            return 0
    except (AuditError, GhError) as exc:
        print(json.dumps({'status': 'error', 'error': str(exc)}, ensure_ascii=False))
        return 2
    return 2


if __name__ == '__main__':
    raise SystemExit(main())