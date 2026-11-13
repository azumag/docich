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
SETTLED_MARCH_TICKS = 3000                # a launch record older than this cannot matter

# Observation caches: every one of these is rebuilt by the next observation.
CACHE_KEYS = ('obs_cost', 'post_month_status_last', 'general_statuses')
# Append-only history that only needs its recent tail.
HISTORY_KEYS = ('sortie_history',)
# Counters and per-cell tallies: they only inform the plan, never the safety
# rules, so a record that is still too wide may lose them entirely.
COUNTER_KEYS = ('tally', 'y_jumps', 'y_jumped', 'y_jumps_total', 'house_eggs')
# Retirement bookkeeping; the live statuses are kept.
RETIRED = ('failed', 'done')
# Every place a step id is still referenced after the order map forgets it.
REFERENCE_KEYS = ('order_context', 'sortie_general')
# plan 内の「step id → その場の文脈/上書き」マップ。step が決着した後は
# 誰も引かない (execution.py / orders.py は必ず order['step'] で引く)。
PLAN_STEP_MAPS = ('order_context', 'card_override', 'general_override',
                  'sortie_general', 'source_override')
LIVE_ORDER_STATES = ('pending', 'launched', 'launched_unconfirmed')


def size(state) -> int:
    """Bytes the record occupies on disk, which is what the read limit counts.

    Japanese castle and general names are three bytes each in UTF-8, so counting
    characters under-reports the file by about a fifth once the memory is full
    of them: the live record measured 165 814 characters while it occupied
    202 953 bytes, and a character-counted budget would let it pass the limit
    again.
    """
    import json
    return len(json.dumps(state, ensure_ascii=False).encode('utf-8'))


def target(limit: int) -> int:
    return limit * TARGET_FRACTION // 4


def load_state(path, limit: int) -> dict:
    """Read the record, tolerating one oversized file so it can be compacted."""
    try:
        state = read_record(path, limit=limit)
    except AdapterError:
        state = _read_oversized(path)
    return sanitize_state(state)


def sanitize_state(state: dict) -> dict:
    """Drop castle-status labels an older read stored as garrison members.

    v678 stops new reads from keeping 城名/じょう/しゅうにゅう/レベル/しょうぐん/めい, but a
    record written before it still carries them and the plan believes that castle
    holds spares. The read only runs when the whole name column matches the
    member count, so a polluted castle can stay polluted; cleaning it here
    repairs the run instead of waiting for a perfect re-read.
    """
    from copy import deepcopy
    from .os_layer.roster import known_generals
    policy = state.get('policy') if isinstance(state, dict) else None
    world = policy.get('world') if isinstance(policy, dict) else None
    garrison = world.get('garrison') if isinstance(world, dict) else None
    if not isinstance(garrison, dict):
        return state
    cleaned, changed = {}, False
    for castle, names in garrison.items():
        if isinstance(names, list):
            kept = known_generals(names)
            if len(kept) != len(names):
                changed = True
            cleaned[castle] = kept
        else:
            cleaned[castle] = names
    if not changed:
        return state
    out = deepcopy(state)
    out['policy']['world']['garrison'] = cleaned
    return out


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
    for key in COUNTER_KEYS:
        if size(out) <= goal:
            return out
        policy.pop(key, None)
    if size(out) > goal:
        _prune_settled_marches(out, goal)
    if size(out) > goal:
        _prune_retired_step_maps(out, goal)
    return out


def _prune_retired_step_maps(state, goal: int) -> None:
    """Drop per-step plan context that no live order or march can read again.

    g1163 2026-11-13 09:5x: the record reached 356 274 bytes against the
    262 144-byte read limit although every cache, history and settled-march
    trim had already run. All of the remaining growth was inside ``plan``:
    ``order_context`` 185 KB / 228 entries, ``card_override`` 30 KB,
    ``general_override`` 16 KB, ``sortie_general`` 15 KB. Only 49/61 of those
    steps were pending or launched; the rest belonged to 226 ``failed`` orders.
    Because of that the shared status card read the policy as unavailable
    ("状態: 未確認") and the left observation card disappeared from the
    broadcast.

    A step is kept when it is still a live order, or when a march/flow region
    can still resolve it (``world.sorties`` / ``world.launched_orders`` /
    ``flow``). Everything else is unreachable: the readers
    (``strategy.execution`` / ``strategy.orders`` / ``flows.deploy``) always
    look up ``order['step']``.
    """
    policy = state.get('policy') or {}
    plan = policy.get('plan') if isinstance(policy, dict) else None
    if not isinstance(plan, dict):
        return
    orders = plan.get('orders') if isinstance(plan.get('orders'), dict) else {}
    referenced = {step for step, status in orders.items() if status in LIVE_ORDER_STATES}
    world = policy.get('world') if isinstance(policy.get('world'), dict) else {}
    for key in ('sorties', 'launched_orders'):
        section = world.get(key)
        if isinstance(section, dict):
            referenced.update(section)
    flow = policy.get('flow')
    if isinstance(flow, dict):
        referenced.update(key for key in flow if isinstance(key, str))
    # 現在動いている step と再武装の待ちは、orders が一時的に欠けていても残す。
    for key in ('active', 'attack'):
        step = plan.get(key)
        if isinstance(step, str) and step:
            referenced.add(step)
    rearm = plan.get('plan_step_rearm')
    if isinstance(rearm, dict):
        referenced.update(rearm)
    retry = policy.get('retry_context')
    if isinstance(retry, dict):
        referenced.update(key for key in retry if isinstance(key, str))
    for key in PLAN_STEP_MAPS:
        section = plan.get(key)
        if not isinstance(section, dict):
            continue
        for step in list(section):
            if size(state) <= goal:
                return
            if step not in referenced:
                section.pop(step, None)
        if not section:
            plan.pop(key, None)


def _entry_tick(entry):
    """When this launch/sortie record was written, or None."""
    if not isinstance(entry, dict):
        return None
    for key in ('tick', 'confirm_tick'):
        if type(entry.get(key)) is int:
            return entry[key]
    binding = entry.get('departure_binding')
    if isinstance(binding, dict) and type(binding.get('confirm_tick')) is int:
        return binding['confirm_tick']
    return None


def _prune_settled_marches(state, goal: int) -> None:
    """Drop launch bookkeeping for marches that ended long ago.

    `world.launched_orders` and `world.sorties` keep one record per launch with
    its confirmation context. Nothing settles them, so they grew to 120 KB and
    50 KB by chapter 9 month 23-12 (g1163 2026-11-12 02:2x: 270 051 bytes, over
    the 256 KiB read limit — the agent failed every observation on it). A record
    whose step is no longer a live order and whose tick is older than
    SETTLED_MARCH_TICKS cannot affect the current plan.
    """
    policy = state.get('policy') or {}
    world = policy.get('world') if isinstance(policy, dict) else None
    if not isinstance(world, dict):
        return
    orders = ((policy.get('plan') or {}).get('orders') or {})
    now = int(policy.get('tick') or 0)
    marches = world.get('sorties') if isinstance(world.get('sorties'), dict) else {}
    # A 'launched' order whose march ended long ago never retires: chapter 9
    # carried 68 of them (g1163 2026-11-12 02:5x) and together with its launch
    # context each one is ~1 KB. Nothing waits on an order that old, so it is
    # retired to 'failed' (the state docich itself uses for a settled order)
    # and its records go with it.
    for step, status in list(orders.items()):
        if status not in ('launched', 'launched_unconfirmed'):
            continue
        march = marches.get(step) if isinstance(marches.get(step), dict) else {}
        if march.get('status') in ('en_route', 'launched_unconfirmed'):
            continue
        tick = _entry_tick(world.get('launched_orders', {}).get(step) if isinstance(world.get('launched_orders'), dict) else None)
        tick = tick if tick is not None else _entry_tick(march)
        if tick is not None and now - tick > SETTLED_MARCH_TICKS:
            orders[step] = 'failed'
    live = {step for step, status in orders.items()
            if status in ('pending', 'launched', 'launched_unconfirmed')}
    for key in ('launched_orders', 'sorties'):
        section = world.get(key)
        if not isinstance(section, dict):
            continue
        for step in list(section):
            if step in live:
                continue
            tick = _entry_tick(section[step])
            if tick is None or now - tick > SETTLED_MARCH_TICKS:
                section.pop(step, None)
