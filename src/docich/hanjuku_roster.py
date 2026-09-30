"""Fresh, bounded roster receipts. No historical egg cache or paid head counts."""
from __future__ import annotations

MAX_GENERALS = 128
FRESH_TICKS = 400


def roles(raw):
    if not isinstance(raw, dict) or set(raw) != {'per_castle', 'attack'}:
        raise ValueError('invalid recruitment roles')
    if (type(raw['per_castle']) is not int or not 1 <= raw['per_castle'] <= 4
            or type(raw['attack']) is not int or not 1 <= raw['attack'] <= 8):
        raise ValueError('invalid recruitment roles')
    return dict(raw)


def invalidate(mem):
    mem.pop('recruit_roster', None)
    mem['recruit_roster_recheck'] = True
    if mem.get('house'):
        mem['house']['roster_invalidated'] = True


def begin(mem, state):
    state['roster_started'] = int(mem.get('tick') or 0)
    state['roster_month'] = mem.get('month')
    mem['recruit_roster'] = {'chapter': mem.get('chapter'), 'month': mem.get('month'),
                             'tick': state['roster_started'], 'names': [], 'complete': False}


def fresh(mem):
    receipt = mem.get('recruit_roster')
    if not isinstance(receipt, dict):
        return None
    names, tick, now = receipt.get('names'), receipt.get('tick'), mem.get('tick')
    if (receipt.get('chapter') != mem.get('chapter') or receipt.get('month') != mem.get('month')
            or type(tick) is not int or type(now) is not int or not 0 <= now - tick < FRESH_TICKS
            or not isinstance(names, list) or not 1 <= len(names) <= MAX_GENERALS
            or any(not isinstance(n, str) or not n for n in names)
            or len(set(names)) != len(names)):
        return None
    return receipt


def page(mem, names):
    if names is None or not names or len(set(names)) != len(names):
        return
    state = mem.get('house') or {}
    scanning = state.get('phase') in {'roster', 'roster_next', 'roster_advance', 'status'}
    receipt = mem.get('recruit_roster')
    if scanning:
        if (state.get('roster_invalidated') or 'roster_started' not in state
                or state.get('chapter') != mem.get('chapter')
                or state.get('roster_month') != mem.get('month')
                or not isinstance(receipt, dict) or receipt.get('tick') != state['roster_started']):
            return
        joined = list(dict.fromkeys([*receipt['names'], *names]))
        if len(joined) <= MAX_GENERALS:
            receipt['names'] = joined
        return
    # Outside a scan, only this one page is evidence. Do not merge pages from
    # separate menu visits or turn a small page into a claimed total.
    if not state:
        mem['recruit_roster'] = {'chapter': mem.get('chapter'), 'month': mem.get('month'),
                                 'tick': int(mem.get('tick') or 0), 'names': list(names),
                                 'complete': False}


def complete(mem, state):
    receipt = fresh(mem)
    if (receipt and not state.get('roster_invalidated') and state.get('roster_wrapped')
            and receipt['tick'] == state.get('roster_started')
            and set(receipt['names']) == set(state.get('seen') or ())):
        receipt['complete'] = True
        mem['recruit_roster_recheck'] = False
        return True
    mem['recruit_roster_recheck'] = True
    return False
