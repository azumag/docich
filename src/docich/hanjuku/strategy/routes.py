"""Chapter-three castle relays using the existing chart's directed legs.

This is not a terrain detector. A nearby castle is not proof that a straight
march can cross the cliffs. Keep the chart's legs, stop inside owned castles,
and read the actual roster before forwarding a reinforcement.
"""
from collections import deque

from ... import hanjuku_chart as chart
from ..os_layer.world import SORTIE_BUSY_TICKS

PROBE_INTERVAL = 40


def legs(chapter):
    return {(o['source'], o['target']) for o in chart.orders(chapter)
            if not chart.is_boss_castle(o['target'])} if chapter == 3 else set()


def allowed(mem, source, target):
    return (mem.get('chapter') != 3 or chart.is_boss_castle(target)
            or (source, target) in legs(3))


def path(mem, source, target, owned):
    """Intermediate castles must be ours; never march through an enemy town."""
    graph = legs(mem.get('chapter'))
    cells = chart.castles(mem.get('chapter'))
    queue = deque([(source,)])
    seen = {source}
    while queue:
        route = queue.popleft()
        for a, b in sorted(graph):
            if a != route[-1] or b in seen or b not in cells:
                continue
            if b == target:
                return (*route, b)
            if b in owned:
                seen.add(b)
                queue.append((*route, b))
    return None


def incoming(mem, castle):
    now = int(mem.get('tick') or 0)
    result = []
    for step, sortie in (mem.get('sorties') or {}).items():
        order = (mem.get('launched_orders') or {}).get(step) or {}
        tick = sortie.get('tick')
        if (order.get('purpose') == 'move' and sortie.get('status') == 'en_route'
                and all(order.get(k) == sortie.get(k) for k in ('source', 'target', 'general'))
                and sortie.get('target') == castle and sortie.get('source') != castle
                and allowed(mem, sortie.get('source'), castle)
                and type(tick) is int and 0 <= now - tick < SORTIE_BUSY_TICKS):
            result.append((step, sortie))
    return result


def probe_due(mem, castle):
    """A dispatched transfer permits opening a list, never guessing arrival."""
    now = int(mem.get('tick') or 0)
    last = (mem.get('route_probe_read_tick') or {}).get(castle)
    return (mem.get('chapter') == 3 and bool(incoming(mem, castle))
            and (last is None or now - int(last) >= PROBE_INTERVAL))


def observed_roster(mem, castle, names):
    if mem.get('chapter') != 3:
        return
    mem.setdefault('route_probe_read_tick', {})[castle] = int(mem.get('tick') or 0)
    for step, sortie in incoming(mem, castle):
        if sortie.get('general') in names:
            sortie['status'] = 'arrived'
            from ..decision_log import _record
            _record(mem, 'sortie_move_arrival_observed', chart_step=step,
                    general=sortie['general'], castle=castle,
                    reason='中継城の実一覧で移動した本人を確認し、次の出撃の前提を更新')
