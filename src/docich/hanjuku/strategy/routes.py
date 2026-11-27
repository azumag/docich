"""Chapter-three castle relays using the existing chart's castle-to-castle legs.

This is not a terrain detector. A nearby castle is not proof that a straight
march can cross the cliffs. Keep the chart's legs, stop inside owned castles,
and read the actual roster before forwarding a reinforcement. A chart leg may
be retraced to an owned castle to bring a rear garrison back toward the front.
"""
from ..state.regions import world as _world, sorties as _sorties
from collections import deque

from ... import hanjuku_chart as chart, hanjuku_chart_adjust as chart_adjust
from ..os_layer.world import SORTIE_BUSY_TICKS, _owned

# Owner 2026-11-23: confirmation-only sortie-window probes were opening too
# often and blocking progress.  Back off to ~5 minutes between probes of the
# same castle (200 observations at 1.5 s).
PROBE_INTERVAL = 200
ROAD_ARRIVAL_PROBE_DELAY = 200   # ch9 road hop arrives well before SORTIE_BUSY_TICKS (800)


ROAD_CHAPTERS = frozenset({3, 9})     # marches follow a road table, not a straight line


def is_road(chapter) -> bool:
    return chapter in ROAD_CHAPTERS


# owner 2026-11-27: 第1話で間に城があるため直接行軍できない組。
CHAPTER1_BLOCKED_LEGS = frozenset({
    ('アルマムーン', 'スペンソニア'),
})
# ボスへ一直線に行けない城（間にジョンリギ/スペンソニアがある）。
CHAPTER1_BOSS_BLOCKED_SOURCES = frozenset({'アルマムーン', 'ジョンリギ'})


def legs(chapter):
    if chapter == 9:
        # One winding road: a march goes to the adjacent castle, both ways
        # (a lost castle is retaken from either neighbour).
        road = chart.corridor(9)
        return {pair for a, b in zip(road, road[1:]) for pair in ((a, b), (b, a))}
    if chapter == 1:
        # owner 2026-11-27: 第1話は城同士が一直線でつながっていない。チャートの実測
        # leg だけが攻撃可能（アルマムーン→スペンソニア は不可、ジョンリギ経由）。
        # ボスも スペンソニア→けっかい のみ（アルマムーン/ジョンリギからは不可）。
        # ボス leg（スペンソニア→けっかい）も含める: 行軍経路の探索（path）が
        # ボス到達を必要とする。ボスの可否は allowed() 側で判定する。
        return {(o['source'], o['target']) for o in chart.measured_orders(1)}
    return {(o['source'], o['target']) for o in chart.measured_orders(chapter)
            if not chart.is_boss_castle(o['target'])} if chapter == 3 else set()


def allowed(mem, source, target):
    chapter = mem.get('chapter')
    if chapter == 9:
        return (chart.is_boss_castle(target) or (source, target) in legs(9))
    if chapter == 1:
        # owner 2026-11-27: 第1話で「間に城があるため直接行けない」leg だけを塞ぐ。
        # 全 leg を実測表に絞ると既存の偵察・補充の計画と衝突するため、
        # まずは実際に問題になった組を明示的に拒否する。
        if (source, target) in CHAPTER1_BLOCKED_LEGS:
            return False
        if chart.is_boss_castle(target):
            return source not in CHAPTER1_BOSS_BLOCKED_SOURCES
        return True
    return (chapter != 3 or chart.is_boss_castle(target)
            or (source, target) in legs(3)
            or (target in _owned(mem) and (target, source) in legs(3)))


def transfer_allowed(mem, source, target):
    """Whether a transfer (move) may march from source to target.

    owner 2026-11-27: 攻撃と移動で第1話の扱いを分ける。攻撃・奪還は実測 leg のみ
    （allowed）だが、自城への将軍移動は所有城を経由する relay を許す。
    """
    if mem.get('chapter') != 1:
        return True
    if allowed(mem, source, target):
        return True
    return bool(path(mem, source, target, _owned(mem)))


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
    if order.get('purpose') == 'attack' and (source not in owned or target in owned):
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


def _move_general_matches(order, sortie) -> bool:
    """True when ``sortie`` carried out ``order`` despite cursor/substitute picks."""
    planned = order.get('general')
    actual = sortie.get('general')
    if planned == actual or planned in (None, chart_adjust.PROBE_GENERAL):
        return True
    binding = order.get('departure_binding')
    if isinstance(binding, dict) and binding.get('general') == actual:
        return True
    evidence = sortie.get('evidence')
    return (isinstance(evidence, dict)
            and evidence.get('actual_general') == actual
            and evidence.get('planned_general') == planned)


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
                     or (order.get('source') == sortie.get('source')
                         and order.get('target') == sortie.get('target')
                         and _move_general_matches(order, sortie)))
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


# A recall's destination read is due only this long; an old entry must not
# keep minting an unbudgeted probe (the read pops only a complete roster).
ROSTER_READ_DUE_TTL = 900


def roster_read_due(mem):
    """Castles whose roster read has come due, keyed by their due tick.

    ``roster_read_due[castle]`` stores the read's *due time* — a recall lands
    after its walking delay — not the request tick, so an entry counts only
    once ``tick <= now``. An entry past ``ROSTER_READ_DUE_TTL`` is stale: the
    read pops a due only on a complete roster, so an old one would keep an
    unbudgeted probe in front of every plan. It is dropped here for every
    chapter, not just chapter 9's corridor (docich#5127).
    """
    now = int(mem.get('tick') or 0)
    raw = mem.get('roster_read_due') or {}
    for castle, tick in list(raw.items()):
        if type(tick) is not int or now - tick > ROSTER_READ_DUE_TTL:
            raw.pop(castle, None)          # stale past due: drop, never re-read
    return {castle: tick for castle, tick in raw.items() if tick <= now}


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


# ---------------------------------------------------------------------------
# Chapter 12 boss staging (owner 2026-11-24).
#
# A boss march straight from the home castle is absorbed by the castle on the
# line (measured: the hero sent home -> ディランダ stopped inside ヘリオポリス)
# or camps on the way, and every wave from home ended ``boss_sortie_overdue``.
# The chart's own boss source (12-B1, also the target of the hero's last relay
# 12-A3) is where the assault starts: the hero walks the chart's relay first
# (a castle never sends its last general, so each hop needs a companion), the
# other generals follow, and only then do boss waves leave from that castle.
# Every phase is bounded: a staging that makes no progress for
# ``STAGING_MAX_TICKS`` is abandoned and the old behaviour returns.
# ---------------------------------------------------------------------------
STAGING_MAX_TICKS = 6000             # one relay hop measured 560..5000 ticks launch->roster (battles on the road)
STAGING_MAX_TOTAL = 24000            # hard cap from the start, whatever progress is claimed
STAGING_FOLLOWERS = 3          # generals besides the hero to gather at the staging castle
STAGING_MAX_REVERTS = 3
STAGING_PHASES = ('gathering', 'ready')


def _staging_book(mem, chapter):
    book = mem.get('boss_staging')
    if not isinstance(book, dict) or book.get('chapter') != chapter:
        book = mem['boss_staging'] = {'chapter': chapter, 'phase': 'idle', 'since': None,
                                      'reverts': 0}
    return book


def staging_phase(mem):
    """'gathering' | 'ready' while a chapter's boss staging governs boss sorties."""
    chapter = mem.get('chapter') or 0
    if chart.boss_staging(chapter) is None:
        return None
    book = mem.get('boss_staging')
    if not isinstance(book, dict) or book.get('chapter') != chapter:
        return None
    return book.get('phase') if book.get('phase') in STAGING_PHASES else None


def _staging_note(mem, kind, reason, **observed):
    from ..decision_log import _record
    _record(mem, kind, chart_step=None, strategy_variant='boss_staging',
            observed_metric=observed or None, reason=reason)


def staging_arm(mem):
    """Start the staging once the boss is open and the relay castles are ours."""
    chapter = mem.get('chapter') or 0
    spec = chart.boss_staging(chapter)
    if spec is None:
        return None
    book = _staging_book(mem, chapter)
    if book['phase'] != 'idle':
        return book['phase']
    owned = _owned(mem)
    if any(c not in owned for c in spec['path']):
        return 'idle'
    now = int(mem.get('tick') or 0)
    book.update(phase='gathering', since=now, started=now)
    _staging_note(mem, 'boss_staging_started',
                  'ボス出撃を拠点へ集結してから出す（主人公が先に中継城を進み、他の将軍が追従する）',
                  castle=spec['castle'], path=list(spec['path']))
    return 'gathering'


def _staging_abandon(mem, book, reason, **observed):
    book['phase'] = 'abandoned'
    _staging_note(mem, 'boss_staging_abandoned',
                  f'{reason}。従来のボス出撃に戻す', **observed)
    return 'abandoned'


def staging_update(mem):
    """Advance gathering -> ready, revert, or abandon; returns the phase."""
    chapter = mem.get('chapter') or 0
    spec = chart.boss_staging(chapter)
    if spec is None or staging_phase(mem) is None:
        return staging_phase(mem)
    book = _staging_book(mem, chapter)
    now = int(mem.get('tick') or 0)
    owned = _owned(mem)
    stage = spec['castle']
    lost = [c for c in spec['path'] if c not in owned]
    if lost:
        return _staging_abandon(mem, book, '拠点または中継城を失った', lost=lost)
    roster = (_world(mem).get('garrison') or {}).get(stage)
    from ..os_layer.world import NAME
    ready = isinstance(roster, list) and NAME in roster
    if ready and book['phase'] == 'gathering':
        book.update(phase='ready', since=now)
        _staging_note(mem, 'boss_staging_ready',
                      '拠点の将軍一覧に主人公を確認したため、拠点からボス出撃を始める（追従の将軍は拠点へ集め続ける）',
                      castle=stage, roster=list(roster)[:10])
    elif not ready and book['phase'] == 'ready':
        book['reverts'] = int(book.get('reverts') or 0) + 1
        if book['reverts'] > STAGING_MAX_REVERTS:
            return _staging_abandon(mem, book, '拠点の将軍一覧に主人公が繰り返し確認できない',
                                    reverts=book['reverts'])
        book.update(phase='gathering', since=now)
    progress = int(book.get('since') or now)
    # Each hop of the hero along the relay is progress (a march takes ~1000
    # ticks and its arrival is only read from the destination's list).
    here = hero_castle(mem, spec['path'])
    if here is not None and here != book.get('hero_at'):
        book.update(hero_at=here, hero_tick=now)
    progress = max(progress, int(book.get('hero_tick') or 0))
    # A march along the relay that left after the last progress counts too:
    # the hero's (or a follower's) launch is the staging working, even while
    # its arrival is still unread.
    for sortie in (_sorties(mem) or {}).values():
        if (isinstance(sortie, dict) and sortie.get('source') in spec['path']
                and sortie.get('target') in spec['path'][1:]
                and type(sortie.get('tick')) is int and sortie['tick'] <= now):
            progress = max(progress, sortie['tick'])
    if now - int(book.get('started') or now) > STAGING_MAX_TOTAL:
        return _staging_abandon(mem, book, f'開始から{STAGING_MAX_TOTAL}tick以内に拠点へ集結できない',
                                phase=book['phase'], waited=now - int(book.get('started') or now))
    if book['phase'] == 'ready':
        for sortie in (_sorties(mem) or {}).values():
            if (isinstance(sortie, dict) and sortie.get('source') == stage
                    and chart.is_boss_castle(sortie.get('target') or sortie.get('planned_target'))
                    and type(sortie.get('tick')) is int):
                progress = max(progress, sortie['tick'])
    if now - progress > STAGING_MAX_TICKS:
        return _staging_abandon(mem, book, f'{book["phase"]}のまま{STAGING_MAX_TICKS}tick進まない',
                                phase=book['phase'], waited=now - progress)
    return book['phase']


def staging_blocks(mem, order, source):
    """True when the staging forbids this boss sortie from ``source``.

    While gathering no boss wave leaves anywhere; once ready only the staging
    castle sends. A read-only roster probe never marches, so it is not blocked.
    """
    if not chart.is_boss_castle(order.get('target')) or order.get('purpose') == 'move':
        return False
    phase = staging_phase(mem)
    if phase is None or order.get('roster_probe') is True:
        return False
    if phase == 'gathering':
        return True
    return source != chart.boss_staging(mem.get('chapter') or 0)['castle']


def hero_castle(mem, path):
    """The relay castle the newest evidence puts the hero in, else None.

    A launched move's target counts as his castle once it is no longer
    marching (arrival is confirmed by the next list read, which replaces this
    estimate); a roster or the monthly status that is newer wins.
    """
    from ..os_layer.world import NAME
    world = _world(mem)
    evidence = []
    garrison = world.get('garrison') or {}
    post = mem.get('hero_post')
    if isinstance(post, dict) and post.get('castle') in path and type(post.get('tick')) is int:
        # The monthly status names his castle as of its own tick; a roster
        # that no longer lists him there supersedes it (e2e 2026-11-25: the
        # arrival was booked in the destination's list while the post still
        # said アルマムーン, so the next hop was never planned).
        listed = garrison.get(post['castle'])
        if not (isinstance(listed, list) and NAME not in listed):
            evidence.append((post['tick'], 1, post['castle']))
    ticks = world.get('garrison_tick') or {}
    for castle in path:
        names = garrison.get(castle)
        if isinstance(names, list) and NAME in names:
            # A list booked without a read has no tick of its own; rank it by
            # the current tick so it competes with the other evidence.
            evidence.append((ticks[castle] if type(ticks.get(castle)) is int
                             else int(mem.get('tick') or 0), 2, castle))
    for sortie in (_sorties(mem) or {}).values():
        if (isinstance(sortie, dict) and sortie.get('general') == NAME
                and sortie.get('target') in path and type(sortie.get('tick')) is int
                and sortie.get('status') in ('arrived', 'overdue')):
            evidence.append((sortie['tick'], 0, sortie['target']))
    best = max(evidence)[2] if evidence else None
    # The staging only ever walks him forward: a partial roster that does not
    # list him, or a stale status post, must not send the plan back (e2e
    # 2026-11-25: hero_at fell from ブラックランド to アルマムーン mid-march).
    book = mem.get('boss_staging')
    prior = book.get('hero_at') if isinstance(book, dict) and book.get('chapter') == mem.get('chapter') else None
    if prior in path and (best is None or path.index(prior) > path.index(best)):
        return prior
    return best
