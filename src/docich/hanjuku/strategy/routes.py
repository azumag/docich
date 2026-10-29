"""Chapter-three castle relays using the existing chart's castle-to-castle legs.

This is not a terrain detector. A nearby castle is not proof that a straight
march can cross the cliffs. Keep the chart's legs, stop inside owned castles,
and read the actual roster before forwarding a reinforcement. A chart leg may
be retraced to an owned castle to bring a rear garrison back toward the front.
"""
from collections import deque

from ... import hanjuku_chart as chart
from ..os_layer.world import SORTIE_BUSY_TICKS, _owned

PROBE_INTERVAL = 40


def legs(chapter):
    return {(o['source'], o['target']) for o in chart.orders(chapter)
            if not chart.is_boss_castle(o['target'])} if chapter == 3 else set()


def allowed(mem, source, target):
    return (mem.get('chapter') != 3 or chart.is_boss_castle(target)
            or (source, target) in legs(3)
            or (target in _owned(mem) and (target, source) in legs(3)))


def order_allowed(mem, order, source):
    if mem.get('chapter') != 3 or chart.is_boss_castle(order.get('target')):
        return True
    owned = _owned(mem)
    return (source in owned and allowed(mem, source, order.get('target'))
            and (order.get('purpose') != 'move' or order.get('target') in owned))


def path(mem, source, target, owned):
    """Intermediate castles must be ours; never march through an enemy town."""
    graph = legs(mem.get('chapter'))
    # Retrace an existing segment only into our own castle; this is a return
    # along the same terrain, never a new diagonal attack on an enemy castle.
    graph |= {(b, a) for a, b in graph if a in owned}
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


def _moves_into(mem, castle):
    """Unconfirmed transfers sent into ``castle`` along a chart leg."""
    now = int(mem.get('tick') or 0)
    for step, sortie in (mem.get('sorties') or {}).items():
        if not isinstance(sortie, dict):
            continue
        order = (mem.get('launched_orders') or {}).get(step) or {}
        tick = sortie.get('tick')
        if (order.get('purpose') == 'move' and sortie.get('status') == 'en_route'
                and all(order.get(k) == sortie.get(k) for k in ('source', 'target', 'general'))
                and sortie.get('target') == castle and sortie.get('source') != castle
                and allowed(mem, sortie.get('source'), castle)
                and type(tick) is int and 0 <= now - tick):
            yield step, sortie


def incoming(mem, castle):
    """Transfers whose general still counts as marching (the busy window)."""
    now = int(mem.get('tick') or 0)
    return [(step, sortie) for step, sortie in _moves_into(mem, castle)
            if now - sortie['tick'] < SORTIE_BUSY_TICKS]


def unread_dispatches(mem, castle):
    """Transfers sent into ``castle`` after its list was last read, of any age.

    The busy window bounds how long a *general* counts as marching; it must
    not bound whether the castle's list may be opened. No screen reports a
    transfer's arrival, so a castle recorded empty stays empty until its own
    list is read (g1075 2026-10-29: moves kept going to グリン, recorded
    empty and never reread once each move left the busy window, so the only
    leg to the lost リグア stayed closed with the interim retake waiting).
    """
    last = (mem.get('route_probe_read_tick') or {}).get(castle)
    return [(step, sortie) for step, sortie in _moves_into(mem, castle)
            if last is None or sortie['tick'] > int(last)]


def probe_due(mem, castle):
    """A dispatched transfer permits opening a list, never guessing arrival."""
    now = int(mem.get('tick') or 0)
    last = (mem.get('route_probe_read_tick') or {}).get(castle)
    return (mem.get('chapter') == 3
            and bool(incoming(mem, castle) or unread_dispatches(mem, castle))
            and (last is None or now - int(last) >= PROBE_INTERVAL))


def observed_roster(mem, castle, names):
    # Every chapter: chapter 4 recorded ビクトリア empty, never read it, and
    # sent five moves there (g1075 2026-10-29, #3037).
    mem.setdefault('route_probe_read_tick', {})[castle] = int(mem.get('tick') or 0)
    for step, sortie in incoming(mem, castle):
        if sortie.get('general') in names:
            sortie['status'] = 'arrived'
            from ..decision_log import _record
            _record(mem, 'sortie_move_arrival_observed', chart_step=step,
                    general=sortie['general'], castle=castle,
                    reason='中継城の実一覧で移動した本人を確認し、次の出撃の前提を更新')
