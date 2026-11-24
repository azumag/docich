"""Chapter-three castle relays using the existing chart's castle-to-castle legs.

This is not a terrain detector. A nearby castle is not proof that a straight
march can cross the cliffs. Keep the chart's legs, stop inside owned castles,
and read the actual roster before forwarding a reinforcement. A chart leg may
be retraced to an owned castle to bring a rear garrison back toward the front.
"""
from ..state.regions import world as _world, sorties as _sorties
from collections import deque

from ... import hanjuku_chart as chart
from ..os_layer.world import SORTIE_BUSY_TICKS, _owned

# Owner 2026-11-23: confirmation-only sortie-window probes were opening too
# often and blocking progress.  Back off to ~5 minutes between probes of the
# same castle (200 observations at 1.5 s).
PROBE_INTERVAL = 200
ROAD_ARRIVAL_PROBE_DELAY = 200   # ch9 road hop arrives well before SORTIE_BUSY_TICKS (800)


ROAD_CHAPTERS = frozenset({3, 9})     # marches follow a road table, not a straight line


def is_road(chapter) -> bool:
    return chapter in ROAD_CHAPTERS


def legs(chapter):
    if chapter == 9:
        # One winding road: a march goes to the adjacent castle, both ways
        # (a lost castle is retaken from either neighbour).
        road = chart.corridor(9)
        return {pair for a, b in zip(road, road[1:]) for pair in ((a, b), (b, a))}
    return {(o['source'], o['target']) for o in chart.measured_orders(chapter)
            if not chart.is_boss_castle(o['target'])} if chapter == 3 else set()


def allowed(mem, source, target):
    chapter = mem.get('chapter')
    if chapter == 9:
        return (chart.is_boss_castle(target) or (source, target) in legs(9))
    return (chapter != 3 or chart.is_boss_castle(target)
            or (source, target) in legs(3)
            or (target in _owned(mem) and (target, source) in legs(3)))


def move_leg(mem, source, target, owned=None):
    """A chapter 9 transfer may ride the owned road past relay castles.

    docich#3952 (measured 2026-11-11 22:2x-23:0x): relaying every rear general
    one hop at a time delivered ~1 general per 5 minutes to the front, so the
    front never held a squad and オオキク's defender recovered between attacks
    (18 -> 34 -> 44). A direct march along owned road castles is a legal move
    leg on this chapter (the road is the only traversable path anyway), so a
    transfer's leg is the adjacent hop ``allowed`` already permits *or* any
    route whose intermediate castles are all ours (``path`` proves that); one
    enemy castle on the way falls back to the plain one-hop leg.
    """
    if allowed(mem, source, target):
        return True
    if mem.get('chapter') != 9:
        return False
    if owned is None:
        owned = _owned(mem) & set(chart.castles(mem.get('chapter') or 0))
    if source not in owned or target not in owned:
        return False
    return path(mem, source, target, owned) is not None


def order_allowed(mem, order, source):
    owned = _owned(mem)
    target = order.get('target')
    # An adopted attack must still target an enemy castle at the moment it
    # commits, even in chapters without a measured road graph. A stale plan
    # cannot turn a newly captured castle into an attack.
    if order.get('purpose') == 'attack' and target in owned:
        return False
    if not is_road(mem.get('chapter')):
        return True
    if source not in owned:
        return False
    if chart.is_boss_castle(target):
        return True
    if order.get('purpose') == 'move':
        # Owner 2026-11-16: chapter 9's road only carries a march to the
        # castle before it — a transfer aimed past a neighbour is absorbed by
        # the castle between and never arrives (タオ→オシロガ over サヌト
        # settled as move_overdue after 1400+ ticks). A move's leg is the
        # adjacent hop only; the N-2→N-1→N relay rides successive orders.
        return (target in owned and allowed(mem, source, target))
    return allowed(mem, source, target)


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
    """Unconfirmed transfers sent into ``castle`` along a chart leg.

    An 'overdue' move is still a dispatch that was sent here and whose
    arrival was never read: g1097 2026-11-02 drained five generals into
    アイアイエー because the overdue march relaunch popped
    ``launched_orders`` and the sortie stopped counting as an unread
    dispatch, so the next plan moved somebody else into the same castle.
    The purpose lives on the sortie itself (written at launch) so the
    check survives the snapshot's removal.
    """
    now = int(mem.get('tick') or 0)
    for step, sortie in _sorties(mem).items():
        if not isinstance(sortie, dict):
            continue
        order = (_world(mem).get('launched_orders') or {}).get(step) or {}
        purpose = order.get('purpose') or sortie.get('purpose')
        tick = sortie.get('tick')
        if (purpose == 'move' and sortie.get('status') in ('en_route', 'overdue')
                and (not order
                     or all(order.get(k) == sortie.get(k)
                            for k in ('source', 'target', 'general')))
                and sortie.get('target') == castle and sortie.get('source') != castle
                and move_leg(mem, sortie.get('source'), castle)
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


def arrival_probe_due(mem, castle, *, cooldown=False):
    """Transfers sent into ``castle`` have left the busy window and its list was
    not read since: that list is the only receipt of their arrival.

    g1097 2026-11-03 (chapter 5): アイアイエー was recorded empty from a reading
    26k ticks old while 14 moves had gone there. No list was ever opened, so the
    generals who stood there never sortied. ``cooldown`` spaces the probe
    *orders* a candidate builder mints; the order already running is not
    affected (``_source_list_open`` calls without it).
    """
    now = int(mem.get('tick') or 0)
    unread = unread_dispatches(mem, castle)
    if not unread:
        return False
    if incoming(mem, castle):
        # Non-road chapters wait until every in-flight move leaves the busy
        # window (#3515 / #2841 contract). In a road chapter (ch9), hops are
        # short and follow-up moves run back-to-back, so waiting for incoming()
        # to drain hides already-arrived units at the front castle (#4851).
        last_read = (mem.get('route_probe_read_tick') or {}).get(castle)
        road_ready = (
            is_road(mem.get('chapter'))
            and (last_read is None or now - int(last_read) >= PROBE_INTERVAL)
            and any(now - sortie['tick'] >= ROAD_ARRIVAL_PROBE_DELAY
                    or sortie.get('status') == 'overdue'
                    for _, sortie in unread)
        )
        if not road_ready:
            return False
    if cooldown:
        asked = (mem.get('arrival_probe_tick') or {}).get(castle)
        return asked is None or now - int(asked) >= PROBE_INTERVAL
    return True


def probe_due(mem, castle):
    """A dispatched transfer permits opening a list, never guessing arrival."""
    if not is_road(mem.get('chapter')):
        return arrival_probe_due(mem, castle)
    now = int(mem.get('tick') or 0)
    last = (mem.get('route_probe_read_tick') or {}).get(castle)
    return (bool(incoming(mem, castle) or unread_dispatches(mem, castle))
            and (last is None or now - int(last) >= PROBE_INTERVAL))


def _remember_residents(mem, castle, names):
    """Mark the generals standing in a read roster as resident in that castle.

    The monthly recall queue and the sortie planner read each general's
    individual status row, not the castle roster. A recalled camper who walks
    home appears in the destination list (the recall's arrival receipt) while
    his row still says 'camp', so the next month recalled him again
    (g1190 2026-11-17: オレガノ/ユイートル had been camp-recall targets for
    many months). The roster is authoritative for residency.
    """
    statuses = (mem.get('general_statuses') or {}).get('statuses')
    if not isinstance(statuses, dict) or not isinstance(names, (list, tuple)):
        return
    from ..decision_log import _record
    now = int(mem.get('tick') or 0)
    moved = []
    for name in names:
        row = statuses.get(name)
        if not isinstance(row, dict):
            continue
        if row.get('location') == 'castle' and row.get('castle') == castle:
            continue
        row.update(location='castle', castle=castle, location_observed=True, tick=now)
        moved.append(name)
    if moved:
        _record(mem, 'roster_resident_confirmed', castle=castle,
                observed_metric={'residents': list(names)[:12], 'moved': moved[:12]},
                reason='実一覧に居た将軍を在城として更新し、帰還済みの野営を再対象にしない')


def observed_roster(mem, castle, names):
    # Every chapter: chapter 4 recorded ビクトリア empty, never read it, and
    # sent five moves there (g1075 2026-10-29, #3037).
    mem.setdefault('route_probe_read_tick', {})[castle] = int(mem.get('tick') or 0)
    # The busy window bounds "still marching", not arrival evidence: an
    # overdue move's general standing in this list is arrival observed.
    overdue = [(step, sortie) for step, sortie in _moves_into(mem, castle)
               if sortie.get('status') == 'overdue']
    for step, sortie in [*incoming(mem, castle), *overdue]:
        if sortie.get('general') in names:
            sortie['status'] = 'arrived'
            from ..decision_log import _record
            _record(mem, 'sortie_move_arrival_observed', chart_step=step,
                    general=sortie['general'], castle=castle,
                    reason='中継城の実一覧で移動した本人を確認し、次の出撃の前提を更新')
    _remember_residents(mem, castle, names)
