"""Post-game-over chart collation (#1085 L3/L4).

After ``terminal_reason == 'game_over'`` the corner collates the permanent base
chart, every adjusted chart saved during the run and the per-order outcomes in
``hanjuku_decisions.jsonl``. The result, ``hanjuku_chart_review.json``, lists
proposals for ``hanjuku_chart.py`` (promote an adjusted order that captured
its target, review a base order that failed, add orders for off-chart
situations). It is evidence for a human/PR follow-up; it never edits the chart.
Deterministic, bounded and offline.
"""
from __future__ import annotations

import json
from pathlib import Path
import time

from . import hanjuku_chart as chart
from . import hanjuku_chart_adjust as adjust
from .game_switch import atomic_write_json
from .hanjuku_run import append_log

REVIEW_FILE = 'hanjuku_chart_review.json'
SCHEMA = 1
MAX_LINES = 200000


def _read_jsonl(runtime_dir: Path, name: str):
    for path in (runtime_dir / f'{name}.previous.jsonl', runtime_dir / f'{name}.jsonl'):
        if not path.exists() or path.is_symlink():
            continue
        with path.open(encoding='utf-8', errors='ignore') as stream:
            for index, line in enumerate(stream):
                if index >= MAX_LINES:
                    break
                try:
                    item = json.loads(line)
                except ValueError:
                    continue
                if isinstance(item, dict):
                    yield item


def _kind(step, base_steps):
    if step in base_steps:
        return 'base'
    if isinstance(step, str) and step.startswith(adjust.INTERIM_PREFIX):
        return 'interim'
    return 'adjusted'


def collate(runtime_dir: Path) -> dict:
    runtime_dir = Path(runtime_dir)
    chapters, steps, off_chart = set(), {}, []
    adjusted_orders = {}
    for item in _read_jsonl(runtime_dir, adjust.HISTORY_LOG):
        if item.get('event') == 'adjusted_chart_saved':
            try:
                doc = adjust.validate(item)
            except ValueError:
                continue
            for order in doc['orders']:
                adjusted_orders[order['step']] = {**order, 'cards': list(order['cards']),
                                                  'after': list(order['after'] or ()) or None,
                                                  'chapter': doc['chapter'],
                                                  'request_id': doc['request_id']}
    for item in _read_jsonl(runtime_dir, 'hanjuku_decisions'):
        if item.get('event') != 'decision':
            continue
        decision, step = item.get('decision'), item.get('chart_step')
        if isinstance(item.get('chapter'), int):
            chapters.add(item['chapter'])
        if decision == 'chart_adjust_request':
            off_chart.append({k: item.get(k) for k in ('request_id', 'chapter', 'off_chart_reason',
                                                        'blocked', 'captured')})
            continue
        if not isinstance(step, str):
            continue
        row = steps.setdefault(step, {'starts': 0, 'launched': 0, 'failed': 0, 'retries': 0,
                                      'wins': 0, 'losses': 0, 'unclassified': 0,
                                      'captured': [], 'chapter': item.get('chapter')})
        if decision == 'order_start':
            row['starts'] += 1
            row['target'] = item.get('target')
            row['general'] = item.get('general')
        elif decision == 'order_launched':
            row['launched'] += 1
        elif decision == 'order_failed':
            row['failed'] += 1
        elif decision == 'order_retry':
            row['retries'] += 1
        elif decision == 'battle_result':
            outcome = item.get('outcome')
            key = {'win': 'wins', 'loss': 'losses'}.get(outcome, 'unclassified')
            row[key] += 1
            if (outcome == 'win' and item.get('side') == 'attack'
                    and item.get('castle') and item['castle'] not in row['captured']):
                row['captured'].append(item['castle'])
    base_steps = {o['step'] for c in (chapters or {1}) for o in chart.orders(c)}
    proposals = []
    for step, row in sorted(steps.items()):
        row['kind'] = _kind(step, base_steps)
        target = row.get('target')
        took_target = bool(target) and target in row['captured']
        row['captured_target'] = took_target
        if row['kind'] == 'base' and (row['failed'] or row['losses']) and not took_target:
            proposals.append({'type': 'review_base_step', 'step': step, 'target': target,
                              'failed': row['failed'], 'losses': row['losses'],
                              'retries': row['retries']})
        elif row['kind'] == 'adjusted' and took_target and step in adjusted_orders:
            proposals.append({'type': 'promote_adjusted_step', 'step': step,
                              'order': adjusted_orders[step]})
        elif row['kind'] == 'interim' and took_target:
            proposals.append({'type': 'promote_interim_attack', 'step': step, 'target': target,
                              'general': row.get('general')})
    seen = set()
    for item in off_chart:
        key = (item.get('off_chart_reason'), json.dumps(item.get('blocked'), ensure_ascii=False))
        if key in seen:
            continue
        seen.add(key)
        proposals.append({'type': 'cover_off_chart', **item})
    return {'schema': SCHEMA, 'generated_at': time.time(), 'chapters': sorted(chapters),
            'steps': steps, 'adjusted_orders': adjusted_orders,
            'off_chart_requests': len(off_chart), 'proposals': proposals}


def review(runtime_dir: Path, identity: dict | None = None) -> dict:
    """Collate once per runtime and persist the review; returns a bounded summary."""
    runtime_dir = Path(runtime_dir)
    path = runtime_dir / REVIEW_FILE
    if path.is_symlink():
        raise ValueError('chart review may not be a symlink')
    report = {**collate(runtime_dir), **{k: v for k, v in (identity or {}).items()}}
    atomic_write_json(path, report)
    summary = {'proposals': len(report['proposals']),
               'by_type': {t: sum(1 for p in report['proposals'] if p['type'] == t)
                           for t in sorted({p['type'] for p in report['proposals']})},
               'off_chart_requests': report['off_chart_requests'],
               'adjusted_orders': len(report['adjusted_orders'])}
    append_log(runtime_dir, adjust.HISTORY_LOG, {'event': 'chart_review', 'at': time.time(),
                                                 **(identity or {}), **summary})
    return {'file': REVIEW_FILE, **summary}
