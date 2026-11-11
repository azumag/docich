"""Keep the brain's own memory record readable and bounded.

The agent hands the brain its previous state through ``hanjuku_bot.json`` and
refuses a record above ``BOT_STATE_LIMIT`` as an unbounded file. A record that
grows past that limit therefore fails every observation, and because the brain
only rewrites the file *after* a successful read it can never shrink itself
again: g1163 2026-11-11 20:19-20:26 hit 265 143 bytes against the 262 144-byte
limit, the brain exited with ``policy_error`` 200 times, no input was sent, the
screen froze and the run latched ``screen_stalled``.

Two things keep that impossible:

* ``load_state`` still reads an oversized record once (regular file, hard cap),
  so a run that already drifted can be brought back instead of dying;
* ``compact_state`` trims caches, unreferenced retired orders and old history
  before every write, down to a target below the limit, so the record stays
  readable. Only measured caches and retirement bookkeeping are dropped; live
  orders, sorties and the current month are never touched.
"""
from __future__ import annotations

from ..adapters.base import AdapterError
from ..retroarch_boundary import read_record

OVERSIZED_READ_LIMIT = 4 * 1024 * 1024   # one recovery read, never a habit
TARGET_FRACTION = 3                      # compact down to 3/4 of the read limit
HISTORY_KEEP = 20                        # newest entries of an append-only list

# Observation caches: every one of these is rebuilt by the next observation.
CACHE_KEYS = ('obs_cost', 'post_month_status_last', 'general_statuses')
# Append-only history that only needs its recent tail.
HISTORY_KEYS = ('sortie_history',)
# Retirement bookkeeping; the live statuses are kept.
RETIRED = ('failed', 'done')
# Every place a step id is still referenced after the order map forgets it.
REFERENCE_KEYS = ('order_context', 'sortie_general')


def size(state) -> int:
    import json
    return len(json.dumps(state, ensure_ascii=False))


def target(limit: int) -> int:
    return limit * TARGET_FRACTION // 4


def load_state(path, limit: int) -> dict:
    """Read the record, tolerating one oversized file so it can be compacted."""
    try:
        return read_record(path, limit=limit)
    except AdapterError:
        return _read_oversized(path)


def _read_oversized(path) -> dict:
    from pathlib import Path
    import json
    path = Path(path)
    try:
        seen = path.stat()
        if path.is_symlink() or not path.is_file() or seen.st_size > OVERSIZED_READ_LIMIT:
            raise AdapterError('Hanjuku state record cannot be recovered')
        data = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise AdapterError('Hanjuku state record is unreadable') from exc
    if not isinstance(data, dict):
        raise AdapterError('Hanjuku state record must be an object')
    return data


def _references(state) -> set:
    """Every step id some other region still mentions."""
    policy = state.get('policy') or {}
    plan = policy.get('plan') or {}
    world = policy.get('world') or {}
    live = set()
    for key in REFERENCE_KEYS:
        section = plan.get(key)
        if isinstance(section, dict):
            live.update(section)
    for key in ('sorties', 'launched_orders', 'launched'):
        section = world.get(key)
        if isinstance(section, dict):
            live.update(section)
    flow = policy.get('flow')
    if isinstance(flow, dict):
        live.update(k for k in flow if isinstance(k, str))
    return live


def compact_state(state, limit: int) -> dict:
    """Return a copy trimmed to ``target(limit)``; the input is never mutated."""
    import copy
    if size(state) <= target(limit):
        return state
    out = copy.deepcopy(state)
    policy = out.get('policy')
    if not isinstance(policy, dict):
        return out
    goal = target(limit)
    for key in CACHE_KEYS:
        if size(out) <= goal:
            return out
        policy.pop(key, None)
    plan = policy.get('plan')
    if isinstance(plan, dict):
        orders = plan.get('orders')
        if isinstance(orders, dict):
            live = _references(out)
            for step, status in list(orders.items()):
                if size(out) <= goal:
                    return out
                if status in RETIRED and step not in live:
                    orders.pop(step, None)
            if not orders:
                plan.pop('orders', None)
    for key in HISTORY_KEYS:
        if size(out) <= goal:
            return out
        history = policy.get(key)
        if isinstance(history, list) and len(history) > HISTORY_KEEP:
            policy[key] = history[-HISTORY_KEEP:]
    return out
