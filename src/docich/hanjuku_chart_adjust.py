"""Runtime-adjusted Hanjuku chart (issue #1085, layers L1/L2).

The base chart (``hanjuku_chart``) is permanent and never mutated. When the
policy runs out of charted orders it records ``chart_adjust_request`` and the
bot writes a request file into the runtime directory. An asynchronous worker
(outside the input path) answers with a complete, independent order list plus
optional month purchases. This module is the only boundary that turns that
answer into policy data: every field is validated against measured chart facts
(castle names, card names), so model output never reaches the pad directly.

No model, provider or network calls happen here.
"""
from __future__ import annotations

import hashlib
import math
import json
from pathlib import Path

from . import hanjuku_chart as chart

SCHEMA = 1
REQUEST_FILE = 'hanjuku_chart_adjust_request.json'
ADJUSTED_FILE = 'hanjuku_chart_adjusted.json'
HISTORY_LOG = 'hanjuku_chart_history'
INTERIM_PREFIX = 'I:'
MAX_ORDERS = 16
MAX_CARDS_PER_ORDER = 3
MAX_NOTE = 80
CARD_NAMES = frozenset({
    'イッテツーン', 'ダイチスイム', 'ブラッキー', 'フットバース', 'グリンボー', 'ピッグローラー',
    'カンケリン', 'ノリウツール', 'クースカン', 'ゼンマイン', 'ミックミー', 'デッドガン',
    'ブレイコウ', 'ブンシーン', 'ファイアーボイス', 'ファバード', 'エンジェリン', 'マグネガキン',
    'ハリケーン'})


def settings(raw) -> dict:
    """``[hanjuku.chart_adjust]``: a non-empty ``agents`` chain is consent to
    billed LLM chart generation; ``interim_jev`` enables the JEV interim choice."""
    raw = raw if isinstance(raw, dict) else {}
    enabled = raw.get('enabled', False)
    agents = raw.get('agents', '')
    timeout = raw.get('timeout_s', 180)
    attempts = raw.get('max_attempts', 2)
    interim = raw.get('interim_jev', False)
    interim_timeout = raw.get('interim_timeout_ms', 1500)
    if type(enabled) is not bool or type(interim) is not bool:
        raise ValueError('hanjuku.chart_adjust.enabled/interim_jev must be boolean')
    if not isinstance(agents, str) or len(agents) > 1024:
        raise ValueError('invalid hanjuku.chart_adjust.agents')
    if type(timeout) is not int or not 30 <= timeout <= 600:
        raise ValueError('hanjuku.chart_adjust.timeout_s must be 30..600')
    if type(attempts) is not int or not 1 <= attempts <= 3:
        raise ValueError('hanjuku.chart_adjust.max_attempts must be 1..3')
    if type(interim_timeout) is not int or not 50 <= interim_timeout <= 5000:
        raise ValueError('hanjuku.chart_adjust.interim_timeout_ms must be 50..5000')
    return {'enabled': enabled and bool(agents.strip()), 'agents': agents.strip(),
            'timeout_s': timeout, 'max_attempts': attempts,
            'interim_jev': interim, 'interim_timeout_ms': interim_timeout}


def game_settings(game) -> dict:
    hanjuku = game.raw.get('hanjuku') if isinstance(game.raw.get('hanjuku'), dict) else {}
    return settings(hanjuku.get('chart_adjust'))


def request_id(mem) -> str:
    """Stable id of one off-chart situation: chapter, captures and order states."""
    # Interim (JEV) orders do not change the situation the LLM was asked
    # about; otherwise every interim sortie would discard a pending answer.
    basis = {'chapter': mem.get('chapter'), 'captured': sorted(mem.get('captured') or []),
             'orders': dict(sorted((k, v) for k, v in (mem.get('orders') or {}).items()
                                   if not str(k).startswith(INTERIM_PREFIX)))}
    raw = json.dumps(basis, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _text(value, field, *, limit=MAX_NOTE, required=True):
    if value is None and not required:
        return ''
    if not isinstance(value, str) or (required and not value) or len(value) > limit:
        raise ValueError(f'invalid {field}')
    return value


def _after(value, castles):
    if value is None:
        return None
    if not isinstance(value, (list, tuple)) or not value:
        raise ValueError('invalid after')
    if value[0] == 'captured' and len(value) == 2 and value[1] in castles:
        return ('captured', value[1])
    if value[0] == 'all_captured' and len(value) == 1:
        return ('all_captured',)
    raise ValueError('invalid after')


def _order(raw, castles, base_steps):
    if not isinstance(raw, dict):
        raise ValueError('invalid order')
    step = _text(raw.get('step'), 'step', limit=16)
    # Adjusted steps share the policy's order-status map with the base chart:
    # a colliding name would inherit or overwrite a base order's state.
    if step in base_steps or step.startswith(INTERIM_PREFIX):
        raise ValueError('adjusted step collides with base chart or interim orders')
    source, target = raw.get('source'), raw.get('target')
    if source not in castles or target not in castles or source == target:
        raise ValueError('invalid source/target castle')
    cards = raw.get('cards') or ()
    if (not isinstance(cards, (list, tuple)) or len(cards) > MAX_CARDS_PER_ORDER
            or any(card not in CARD_NAMES for card in cards)):
        raise ValueError('invalid cards')
    return {'step': step, 'general': _text(raw.get('general'), 'general', limit=16),
            'source': source, 'target': target, 'cards': tuple(cards),
            'after': _after(raw.get('after'), castles),
            'note': _text(raw.get('note'), 'note', required=False)}


def _purchases(raw):
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError('invalid purchases')
    month = raw.get('month')
    if (not isinstance(month, (list, tuple)) or len(month) != 2
            or any(type(v) is not int or v < 1 for v in month) or month[1] > 12):
        raise ValueError('invalid purchase month')
    cards = []
    for item in raw.get('cards') or ():
        if (not isinstance(item, (list, tuple)) or len(item) != 2 or item[0] not in CARD_NAMES
                or type(item[1]) is not int or not 1 <= item[1] <= 99):
            raise ValueError('invalid purchase card')
        cards.append((item[0], item[1]))
    soldiers = raw.get('soldiers', 0)
    if type(soldiers) is not int or not 0 <= soldiers <= 999:
        raise ValueError('invalid soldiers')
    generals = raw.get('generals', 0)
    if type(generals) is not int or not 0 <= generals <= 9:
        raise ValueError('invalid generals')
    return {'month': tuple(month), 'cards': tuple(cards), 'soldiers': soldiers,
            'generals': generals, 'note': _text(raw.get('note'), 'note', required=False)}


def validate(doc) -> dict:
    """Return the normalized adjusted chart or raise ``ValueError``."""
    if not isinstance(doc, dict) or doc.get('schema') != SCHEMA:
        raise ValueError('invalid schema')
    chapter = doc.get('chapter')
    if type(chapter) is not int or chapter < 1:
        raise ValueError('invalid chapter')
    castles = chart.castles(chapter)
    if not castles:
        # Without measured castle cells the map policy cannot navigate.
        raise ValueError('chapter has no measured castles')
    rid = doc.get('request_id')
    if not isinstance(rid, str) or len(rid) != 16 or any(c not in '0123456789abcdef' for c in rid):
        raise ValueError('invalid request_id')
    raw_orders = doc.get('orders')
    if not isinstance(raw_orders, list) or not 1 <= len(raw_orders) <= MAX_ORDERS:
        raise ValueError('invalid orders')
    base_steps = {o['step'] for o in chart.orders(chapter)}
    orders = tuple(_order(o, castles, base_steps) for o in raw_orders)
    if len({o['step'] for o in orders}) != len(orders):
        raise ValueError('duplicate step')
    return {'schema': SCHEMA, 'chapter': chapter, 'request_id': rid, 'orders': orders,
            'purchases': _purchases(doc.get('purchases')),
            'generated_at': (doc['generated_at'] if type(doc.get('generated_at')) in (int, float)
                             and math.isfinite(doc['generated_at']) else None),
            'source': _text(doc.get('source'), 'source', limit=40, required=False),
            'reason': _text(doc.get('reason'), 'reason', limit=200, required=False)}


def load(runtime_dir: Path):
    """Read the adjusted chart; a missing or invalid file is simply absent."""
    path = Path(runtime_dir) / ADJUSTED_FILE
    if path.is_symlink():
        return None
    try:
        return validate(json.loads(path.read_text(encoding='utf-8')))
    except (OSError, ValueError):
        return None


def save(runtime_dir: Path, doc) -> dict:
    """Validate, write atomically and append the history (worker side)."""
    from .game_switch import atomic_write_json
    from .hanjuku_run import append_log
    normalized = validate(doc)
    runtime_dir = Path(runtime_dir)
    if (runtime_dir / ADJUSTED_FILE).is_symlink():
        raise ValueError('adjusted chart may not be a symlink')
    # Persist only the normalized fields: unknown keys in model output never
    # reach the runtime file or the history.
    stored = json.loads(json.dumps(normalized, ensure_ascii=False))
    atomic_write_json(runtime_dir / ADJUSTED_FILE, stored)
    append_log(runtime_dir, HISTORY_LOG, {'event': 'adjusted_chart_saved', **stored})
    return normalized


def write_request(runtime_dir: Path, record: dict, identity: dict):
    """Publish the latest off-chart request for the asynchronous worker."""
    from .game_switch import atomic_write_json
    from .hanjuku_run import append_log
    runtime_dir = Path(runtime_dir)
    if (runtime_dir / REQUEST_FILE).is_symlink():
        raise ValueError('adjust request may not be a symlink')
    payload = {'schema': SCHEMA, **identity,
               **{k: record.get(k) for k in ('request_id', 'chapter', 'off_chart_reason',
                                               'captured', 'orders', 'blocked', 'gold', 'month')}}
    atomic_write_json(runtime_dir / REQUEST_FILE, payload)
    append_log(runtime_dir, HISTORY_LOG, {'event': 'adjust_requested', **payload})
    return payload
