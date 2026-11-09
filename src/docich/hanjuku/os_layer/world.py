"""OS層: the observed world model kept in policy memory.

Map localisation (camera/cursor anchoring on castle roofs), castle ownership
from roofs and world-map flags, and the facts derived from them (owned
castles, marching generals, the hero's castle). These are observations, not
plans: the strategy layer reads them to choose orders.
"""
from __future__ import annotations
from ..state.regions import world as _world

from .screen import Screen, castle_roofs
from .pixels import Frame
from ... import hanjuku_chart as chart
from ..decision_log import _record, _tally
from .readers import STATUS_NAMES, _cursor


NAME = chart.HERO
VENUS_NAME = 'ヴィーナス'
EDGE_X = (8, 232)             # cursor cell clamps; the camera scrolls beyond
EDGE_Y = (8, 200)
ANCHOR_PX = 24                # roof-to-castle association radius (castles >=100 px apart)


def _at_edge(value, bounds):
    return value <= bounds[0] + 1 or value >= bounds[1] - 1


def _localize(roofs, castles, predicted_cam):
    """Camera offset from visible roofs by constellation voting.

    Each (roof, castle) pairing implies a camera offset; the offset that
    places the most roofs on known castles wins, ties broken by closeness to
    the prediction. Returns (camera, castle name of the anchor roof) or None.
    """
    best = None
    for roof in roofs:
        for name, (cx, cy) in castles.items():
            cam = (cx - roof['target'][0], cy - roof['target'][1])
            support = 0
            for other in roofs:
                wx, wy = other['target'][0] + cam[0], other['target'][1] + cam[1]
                if any(abs(wx - x) + abs(wy - y) <= 12 for x, y in castles.values()):
                    support += 1
            drift = (abs(cam[0] - predicted_cam[0]) + abs(cam[1] - predicted_cam[1])
                     if predicted_cam else 0)
            key = (support, -drift)
            if best is None or key > best[0]:
                best = (key, cam, name)
    if best is None:
        return None
    (support, neg_drift), cam, name = best
    # A single roof far from the prediction is ambiguous: do not trust it.
    if support < 2 and predicted_cam and -neg_drift > 80:
        return None
    return cam, name


def observed_cursor_cell(screen, mem, frame, *, max_error=12, ambiguity=24):
    """A current two-roof constellation, without cached motion or Y intent."""
    if screen.kind not in ('map', 'map_target') or not isinstance(frame, Frame):
        return None
    cursor = _cursor(screen)
    if cursor is None:
        return None
    cells = chart.castles(mem.get('chapter') or 0)
    roofs = [r for r in castle_roofs(frame) if not r.get('clipped')]
    candidates = {}
    for roof in roofs:
        for x, y in cells.values():
            cam = (x - roof['target'][0], y - roof['target'][1])
            support = sum(any(abs(r['target'][0] + cam[0] - cx)
                              + abs(r['target'][1] + cam[1] - cy) <= max_error
                              for cx, cy in cells.values()) for r in roofs)
            candidates[cam] = support
    if not candidates or max(candidates.values()) < 2:
        return None
    best = [cam for cam, support in candidates.items() if support == max(candidates.values())]
    if any(abs(cam[0] - best[0][0]) + abs(cam[1] - best[0][1]) > ambiguity for cam in best):
        return None
    return [cursor[0] + best[0][0], cursor[1] + best[0][1]]


NAV_CONTINUITY_TICKS = 30      # ticks between map frames that still integrate


def update_world(screen: Screen, mem, frame, goal_name=None):
    """Track the cursor's map cell from measured screen motion and roof anchors.

    Leaving the map (battles, events, month menus) can move the cursor, so the
    estimate becomes uncertain until roofs re-anchor it; ``nav_step`` never
    confirms a cell while uncertain.
    """
    s = _cursor(screen)
    if not s:
        return None
    castles = chart.castles(mem.get('chapter') or 1)
    world = mem.get('cursor')
    last = mem.get('nav_last')
    # Screen displacement is measured, so a continuous run of map frames
    # integrates even while unanchored; the estimate only freezes when a
    # non-measured move (SELECT/Y) or an off-map screen breaks the run. g1075
    # 05:52: an unanchored walk to リグア kept the estimate at [571,282] while
    # the real cursor reached the west edge, so pressing left never ended.
    tick = mem.get('tick')
    last_tick = last.get('tick') if isinstance(last, dict) else None
    if not isinstance(last, dict):
        trusted = False
    elif last_tick is None or type(tick) is not int:
        # Legacy/hot-loaded memory without continuity fields: keep the old
        # rule (trust only anchored frames or the inland search).
        trusted = not mem.get('uncertain') or last.get('search')
    else:
        # Map frames are sparse while the chapter throws battles (g1133
        # 2026-11-05: two map frames in 8.4 minutes), so requiring the very
        # next tick froze the estimate for hours. A short gap still integrates;
        # a long one does not.
        gap = tick - last_tick if type(last_tick) is int and type(tick) is int else None
        trusted = (gap is not None and 0 <= gap <= NAV_CONTINUITY_TICKS
                   and last.get('kind') == screen.kind)
    if world and last and last.get('screen') and trusted:
        moved = []
        for axis, bounds in ((0, EDGE_X), (1, EDGE_Y)):
            if _at_edge(s[axis], bounds) or _at_edge(last['screen'][axis], bounds):
                moved.append(last['expected'][axis])      # camera scrolled: unmeasured
            else:
                moved.append(s[axis] - last['screen'][axis])
        world = [world[0] + moved[0], world[1] + moved[1]]
    # Seed the continuity record from this frame when there is none: `nav_step`
    # only writes it after it computes a direction, so one early return left it
    # None for good and no later frame could integrate (g1133 2026-11-05 froze
    # at [392,144] for hours: every order was stuck at its source).
    if not isinstance(mem.get('nav_last'), dict):
        mem['nav_last'] = {'screen': list(s), 'expected': [0, 0],
                           'tick': int(tick) if type(tick) is int else None,
                           'kind': screen.kind, 'search': bool(mem.get('nav_search'))}
    box = (s[0] - 2, s[1] - 2, s[0] + 18, s[1] + 18)
    roofs = [r for r in castle_roofs(frame, exclude=box) if not r['clipped']]
    predicted = (world[0] - s[0], world[1] - s[1]) if world else None
    mem['roofs_seen'] = len(roofs)
    # While searching after a wrong cell, a lone roof is exactly what was
    # mis-voted before (g401 21:31): anchor only on a constellation.
    found = (None if mem.get('nav_search') and len(roofs) < 2
             else _localize(roofs, castles, predicted))
    anchored = None
    # Once the goal's own roof located a nearby free cursor, do not alternate
    # that correction with another castle's vote as the cursor covers the
    # roof (g403: x=216 -> 205 -> right 10 -> left 10). Away from screen
    # edges, screen displacement is measured; at an edge the camera scrolls
    # and dead reckoning alone is insufficient, so re-localize normally.
    locked = (mem.get('goal_anchor_lock') == goal_name and goal_name and world and last
              and last.get('screen') and not mem.get('uncertain') and not screen.marker
              and goal_name in castles
              and all(not _at_edge(s[a], bounds) and not _at_edge(last['screen'][a], bounds)
                      for a, bounds in ((0, EDGE_X), (1, EDGE_Y)))
              and sum(abs(world[a] - castles[goal_name][a]) for a in (0, 1)) <= 48)
    if locked:
        mem['cursor'] = world
        mem['anchor'] = goal_name
        return world
    mem.pop('goal_anchor_lock', None)
    if found:
        cam, anchored = found
        cam, anchored = _goal_anchor(roofs, castles, cam, anchored, goal_name)
        new = [cam[0] + s[0], cam[1] + s[1]]
        if mem.get('uncertain') or not world or abs(new[0] - world[0]) + abs(new[1] - world[1]) <= 48:
            world = new
            if (goal_name and anchored == goal_name and not screen.marker
                    and sum(abs(new[a] - castles[goal_name][a]) for a in (0, 1)) <= 48):
                mem['goal_anchor_lock'] = goal_name
            mem['uncertain'] = False
            mem.pop('nav_search', None)
            mem.pop('nav_search_leg', None)
            mem.pop('select_used', None)
            if not screen.marker:
                observe_owners(mem, roofs, cam)
        else:
            anchored = None
    mem['cursor'] = world
    mem['anchor'] = anchored
    return world


GOAL_ANCHOR_PX = 16           # voted camera error seen between castles (g401: 11 px)


def _goal_anchor(roofs, castles, cam, anchored, goal_name):
    """Re-anchor the camera on the goal castle's own roof when it is visible.

    The measured castle cells disagree with each other by several pixels, so
    a camera voted from another roof can put the cursor one cell off the
    goal while the estimate says it arrived (g401 2026-09-27: the cursor sat
    11 px below キカンドン and A opened nothing 900 times). The goal roof
    itself is the ground truth for the cell that opens its menu.
    """
    if not goal_name or goal_name not in castles:
        return cam, anchored
    gx, gy = castles[goal_name]
    near = [(abs(r['target'][0] + cam[0] - gx) + abs(r['target'][1] + cam[1] - gy), r)
            for r in roofs]
    near = [(d, r) for d, r in near if d <= GOAL_ANCHOR_PX]
    if len(near) != 1:
        return cam, anchored
    roof = near[0][1]
    return (gx - roof['target'][0], gy - roof['target'][1]), goal_name


OWNER_CONFIRM = 2             # consecutive anchored map readings before a change


def observe_owners(mem, roofs, cam):
    """Track castle ownership from roof colours on anchored map frames.

    A castle taken while nobody defends it shows only 「せめこまれました」 and no
    battle, so ``battle_end`` never revokes it (g401: ジョンリギ turned blue at
    20:12 but stayed "captured", and its chart order kept waiting on it).
    Two consecutive readings are needed; home and boss castles are ignored.
    """
    chapter = mem.get('chapter') or 0
    castles = chart.castles(chapter)
    fixed = {chart.home_castle(chapter), chart.boss_castle(chapter), *chart.boss_sites(chapter)}
    if len(roofs) < 2:
        return                      # one lone roof: camera association not trusted
    seen = {}
    for roof in roofs:
        wx, wy = roof['target'][0] + cam[0], roof['target'][1] + cam[1]
        hits = [name for name, (x, y) in castles.items() if abs(wx - x) + abs(wy - y) <= 12]
        if len(hits) == 1 and roof.get('kind') in ('own', 'enemy'):
            seen[hits[0]] = roof['kind']
    streak = mem.setdefault('owner_streak', {})
    for castle, kind in seen.items():
        prev = streak.get(castle) or {}
        count = prev.get('count', 0) + 1 if prev.get('kind') == kind else 1
        streak[castle] = {'kind': kind, 'count': count,
                          **({'world_flag_vetoed': True} if prev.get('world_flag_vetoed')
                             and prev.get('kind') == kind else {})}
        if count < OWNER_CONFIRM or castle in fixed:
            continue
        from .roster import owner
        owner(mem, STATUS_NAMES.get(castle, castle), kind)
        captured = mem.setdefault('captured', [])
        if kind == 'enemy' and castle in captured:
            (mem.get('castle_income') or {}).pop(STATUS_NAMES.get(castle, castle), None)
            mem['captured'] = [c for c in captured if c != castle]
            lost = mem.setdefault('lost', [])
            if castle not in lost:
                lost.append(castle)
            (_world(mem).get('garrison') or {}).pop(castle, None)
            # Ownership changed hands: the remembered defender is stale.
            (mem.get('castle_defenders') or {}).pop(castle, None)
            _record(mem, 'castle_lost_observed', castle=castle,
                    observed_metric={'roof': kind, 'readings': count},
                    resulting_event=f'lost:{castle}',
                    reason='占領していた城の屋根が敵の色になったため失陥として奪還対象にする')
            _tally(mem, 'castle_losses')
        elif kind == 'own' and castle not in captured:
            flag = (mem.get('world_flags') or {}).get(castle) or {}
            if (flag.get('kind') == 'enemy' and flag.get('chapter') == chapter
                    and 0 <= int(mem.get('tick') or 0) - int(flag.get('tick') or 0) <= WORLD_FLAG_TRUST):
                # g684 2-8 15:12:45: four roof reads called スペンソニア ours
                # while every world map flew its enemy flag; the false capture
                # met all_captured and sent the hero to the boss castle.
                if not streak[castle].get('world_flag_vetoed'):
                    streak[castle]['world_flag_vetoed'] = True
                    _record(mem, 'castle_owned_vetoed', castle=castle,
                            observed_metric={'roof': kind, 'readings': count,
                                             'world_flag_tick': flag.get('tick')},
                            reason='屋根は自軍色だが直近の全体マップの旗が敵のため占領と扱わない')
                continue
            (mem.get('castle_income') or {}).pop(STATUS_NAMES.get(castle, castle), None)
            captured.append(castle)
            if castle in (mem.get('lost') or []):
                mem['lost'] = [c for c in mem['lost'] if c != castle]
            _record(mem, 'castle_owned_observed', castle=castle,
                    observed_metric={'roof': kind, 'readings': count},
                    resulting_event=f'captured:{castle}',
                    reason='城の屋根が自軍の色のため占領として扱う')


SORTIE_BUSY_TICKS = 400       # legacy observation window (~10 min), NOT proof of arrival
BOSS_ARRIVAL_LIMIT = 2 * SORTIE_BUSY_TICKS
# g1133 chapter 6: 603 chart cells took 23-32 min and 740 cells took 34 min.
# The rate must cover the slow end (32 min / 603 cells = 3.18 ticks/cell): at
# 2.4 the limit was 1447 and a march measured at 1701 ticks was declared
# overdue while it could still arrive, re-sending the same order.
MARCH_TICKS_PER_CELL = {6: 3.2}
LONG_MARCH_TICKS = 6 * SORTIE_BUSY_TICKS   # window for late arrival binding
MARCH_ARRIVAL_CAP = LONG_MARCH_TICKS
BOSS_DETOUR_FACTOR = 2.0    # forest detours double the straight-line march


def march_arrival_limit(mem, sortie):
    """Bound an unconfirmed march by the chapter's measured chart distance.

    Keep this pure estimate in the observed-world layer so both strategy and
    busy-general bookkeeping use the same bound without an upwards import.
    An exceeded bound is not evidence that a general has arrived.
    """
    rate = MARCH_TICKS_PER_CELL.get(mem.get('chapter') or 0)
    if not rate:
        return BOSS_ARRIVAL_LIMIT
    cells = chart.castles(mem.get('chapter'))
    source = cells.get(sortie.get('source'))
    target_name = sortie.get('target') or sortie.get('planned_target')
    target = cells.get(target_name)
    if not source or not target:
        return BOSS_ARRIVAL_LIMIT
    distance = ((source[0] - target[0]) ** 2 + (source[1] - target[1]) ** 2) ** 0.5
    limit = max(BOSS_ARRIVAL_LIMIT, min(MARCH_ARRIVAL_CAP, round(distance * rate)))
    if chart.is_boss_castle(target_name):
        # Boss paths can detour through forest, so the Euclidean distance
        # underestimates. A full 2400-tick window is too slow for a probe
        # rotation, though: every wrong candidate cost ~40 minutes (g1163
        # chapter 6: thirteen candidates). A 2x detour factor still covers the
        # measured forest routes and rotates the candidates twice as fast.
        limit = max(BOSS_ARRIVAL_LIMIT,
                    min(MARCH_ARRIVAL_CAP, round(distance * rate * BOSS_DETOUR_FACTOR)))
    return limit


def _en_route(mem):
    """(generals, targets) of launches without an observed end.

    The 400-observation display window is shorter than measured 20-40 minute
    marches. Keep generals and their targets busy until the strategy's
    distance-aware arrival limit; then overdue reconciliation determines
    whether to retry. An overdue/arrived receipt is never treated as moving.
    Missing or future ticks are not evidence of a fresh march.
    """
    now = int(mem.get('tick') or 0)
    sorties = [
        s for s in (mem.get('sorties') or {}).values()
        if isinstance(s, dict)
        and s.get('status') in ('en_route', 'launched_unconfirmed')
        and type(s.get('tick')) is int
        and 0 <= now - s['tick'] < max(SORTIE_BUSY_TICKS, march_arrival_limit(mem, s))
    ]
    return ({s.get('general') for s in sorties if s.get('general')},
            {s['target'] for s in sorties if s.get('target')})


WORLD_FLAG_TRUST = 200   # observations a world-map (Y) flag outranks a roof reading


def _apply_world_flags(mem, flags):
    chapter = mem.get('chapter') or 0
    from .roster import owner
    seen = mem.setdefault('world_flags', {})
    for name, kind in flags.items():
        if name in chart.castles(chapter):
            owner(mem, STATUS_NAMES.get(name, name), kind)
            seen[name] = {'kind': kind, 'tick': int(mem.get('tick') or 0), 'chapter': chapter}
    home = chart.home_castle(chapter)
    cur = mem.get('battle') or {}
    castle = cur.get('castle')
    if (cur.get('side') == 'defense' and castle in chart.castles(chapter)
            and type(cur.get('ally_hp')) is int and cur['ally_hp'] == 0
            and type(cur.get('enemy_hp')) is int and cur['enemy_hp'] > 0
            and flags.get(castle) in ('own', 'enemy')):
        # This flag was read after this defender's zero HP, not before entry
        # or in an earlier battle/month. Keep it only inside this battle.
        cur['defeat_owner'] = {'castle': castle, 'chapter': chapter, 'owner': flags[castle]}
    captured = mem.setdefault('captured', [])
    changed = []
    home_owner = flags.get(home)
    if home_owner == 'enemy':
        # g407 03:53: the home castle flew an enemy flag while every sortie
        # still treated it as ours and fell back to it.
        # A successful retake can also put home in captured. Remove that old
        # receipt even when a hotloaded state already has home_lost=True.
        if home in captured or home not in (mem.get('lost') or []):
            # A completed defense loss already recorded the current capturer.
            # Only a new ownership loss invalidates that defender evidence.
            (mem.get('castle_defenders') or {}).pop(home, None)
        mem['captured'] = captured = [c for c in captured if c != home]
        if not mem.get('home_lost'):
            changed.append(('lost', home))
        mem['home_lost'] = True
        lost = mem.setdefault('lost', [])
        if home not in lost:
            lost.insert(0, home)
        (_world(mem).get('garrison') or {}).pop(home, None)
    elif home_owner == 'own' and (mem.get('home_lost') or home in (mem.get('lost') or [])):
        # A defense loss lists home in lost without home_lost; the own flag
        # clears that too (g1075 14:24: アルマムーン own, still lost).
        mem['home_lost'] = False
        mem['lost'] = [c for c in mem.get('lost') or [] if c != home]
        changed.append(('captured', home))
    for castle, owner in flags.items():
        if castle == home or chart.is_boss_castle(castle):
            continue
        if owner == 'enemy' and castle in captured:
            mem['captured'] = captured = [c for c in captured if c != castle]
            lost = mem.setdefault('lost', [])
            if castle not in lost:
                lost.append(castle)
            (_world(mem).get('garrison') or {}).pop(castle, None)
            # Ownership changed hands: the remembered defender is stale.
            (mem.get('castle_defenders') or {}).pop(castle, None)
            changed.append(('lost', castle))
        elif owner == 'own' and castle not in captured:
            captured.append(castle)
            mem['lost'] = [c for c in mem.get('lost') or [] if c != castle]
            changed.append(('captured', castle))
    # The Y view is read once per picker step and once per survey; recording
    # the same flag set again (g1133 2026-11-05: 204 owners records an hour,
    # most of them identical picker steps) hid real changes in the log.
    # The signature has to survive the policy's JSON round trip: a tuple is
    # reloaded as a list, so a tuple never compares equal again and the same
    # flag set was recorded on every observation anyway (measured 2026-11-05:
    # 147 owners records an hour over nine distinct flag sets).
    signature = [[k, v] for k, v in sorted(flags.items())]
    if mem.get('world_map_signature') != signature:
        mem['world_map_signature'] = signature
        _record(mem, 'world_map_owners', observed_metric=flags,
                resulting_event=[f'{k}:{c}' for k, c in changed] or None,
                reason='全体マップの旗の色で全城の所有を確認')
    for kind, castle in changed:
        _record(mem, 'castle_lost_observed' if kind == 'lost' else 'castle_owned_observed',
                castle=castle, observed_metric={'world_map': flags[castle]},
                resulting_event=f'{kind}:{castle}',
                reason=('全体マップで城の旗が敵の色になったため失陥として奪還対象にする'
                        if kind == 'lost' else '全体マップで城の旗が自軍の色のため占領として扱う'))
        if kind == 'lost':
            _tally(mem, 'castle_losses')


SEASON_SITE_CONFIRM = 2        # consecutive Y-view readings before a site state changes


def _apply_season_sites(mem, sites):
    """Keep chapter 4's cleared 四季王 sites closed from the Y view's markers.

    g1163 2026-11-09: 北西/北東/南西 had been cleared (owner report; the live
    Y view drew their pennants) but the bot kept sortieing to them, because a
    king fight that ends on the closing dialogue leaves no measured 0 HP and
    so never reached ``boss_victories``. The marker is direct evidence, so it
    also stops the next sortie there. Two consecutive readings are required,
    an unreadable marker keeps the previous state, and the impossible
    "all four closed while the chapter is not over" reading is refused so a
    misread can never park the whole chapter.
    """
    chapter = mem.get('chapter') or 0
    if chapter != 4 or not sites:
        return
    sites_all = set(chart.boss_sites(4))
    if (set(sites) >= sites_all and all(kind == 'closed' for kind in sites.values())
            and (mem.get('boss_defeated') or 0) != 4):
        # The chapter cannot still be running with every site cleared: this is
        # a misread (gold frame/cursor pixels), never a reason to park the
        # whole chapter. One record per reading signature.
        digest = [list(item) for item in sorted(sites.items())]
        if mem.get('season_sites_refused') != digest:
            mem['season_sites_refused'] = digest
            _record(mem, 'season_sites_all_closed_refused',
                    observed_metric={'sites': sorted(sites), 'marker': 'closed'},
                    reason='全4地点が撃破済みの印でも章が終わっていないため、読み違いとして記録しない')
        return
    seen = mem.setdefault('season_sites', {})
    streak = mem.setdefault('season_site_streak', {})
    for name, kind in sorted(sites.items()):
        previous = streak.get(name) or {}
        count = previous.get('count', 0) + 1 if previous.get('kind') == kind else 1
        streak[name] = {'kind': kind, 'count': count, 'chapter': chapter}
        if count < SEASON_SITE_CONFIRM:
            continue
        row = seen.get(name) or {}
        if row.get('state') == kind and row.get('chapter') == chapter:
            continue
        if kind == 'closed' and (mem.get('boss_defeated') or 0) != 4:
            closed = {other for other, value in seen.items()
                      if isinstance(value, dict) and value.get('state') == 'closed'
                      and value.get('chapter') == chapter}
            closed.add(name)
            if len(closed) >= len(chart.boss_sites(4)):
                _record(mem, 'season_sites_all_closed_refused', castle=name,
                        observed_metric={'closed': sorted(closed),
                                         'readings': count, 'marker': kind},
                        reason='全4地点が撃破済みの印でも章が終わっていないため、読み違いとして記録しない')
                continue
        seen[name] = {'state': kind, 'chapter': chapter, 'tick': int(mem.get('tick') or 0),
                      'readings': count}
        _record(mem, 'season_site_closed' if kind == 'closed' else 'season_site_open',
                castle=name,
                observed_metric={'site': name, 'marker': kind, 'readings': count},
                resulting_event=f'{kind}:{name}',
                reason=('全体マップの四季王の印が旗に変わったため、この地点は撃破済みとして出撃しない'
                        if kind == 'closed' else
                        '全体マップの四季王の印がドクロのため、撃破済みの記録を取り消して出撃対象へ戻す'))


BOSS_SKULL_READINGS = 2      # equal Y-view skull readings before the cell is used


def _apply_skull_boss(mem, frame):
    """Locate a boss from the Y view's boss skull.

    Chapter 3's boss skull is the same sprite for chapter 4's sites and for a
    moving boss, so a single skull on the whole-island view is the boss's
    current cell. Two equal readings are required, an unreadable view keeps
    the previous value, and the orchestrator decides how long it stays valid
    (a moving boss ages out, a static one keeps its measured cell).
    Chapter 4's four sites are read by ``_apply_season_sites`` instead.
    """
    chapter = mem.get('chapter') or 0
    if chapter in (0, 4) or frame is None:
        return
    from .readers import skull_cell_at, y_view_skulls
    skulls = y_view_skulls(frame)
    if len(skulls) != 1:
        mem.pop('boss_cell_streak', None)
        return
    cell = skull_cell_at(skulls[0], chapter)
    if cell is None:
        return
    streak = mem.get('boss_cell_streak') or {}
    count = (streak.get('count', 0) + 1) if streak.get('cell') == list(cell) else 1
    mem['boss_cell_streak'] = {'cell': list(cell), 'count': count}
    if count < BOSS_SKULL_READINGS:
        return
    previous = mem.get('boss_cell') or {}
    mem['boss_cell'] = {'chapter': chapter, 'cell': list(cell),
                        'tick': int(mem.get('tick') or 0), 'readings': count}
    if previous.get('chapter') != chapter or previous.get('cell') != list(cell):
        _record(mem, 'boss_cell_measured',
                observed_metric={'chapter': chapter, 'cell': list(cell), 'readings': count},
                resulting_event=f'boss_cell:{cell[0]},{cell[1]}',
                reason='全体マップのボスのドクロ位置からボスセルを実測した')


# Backwards-compatible name used by the chapter-9 moving-boss call sites.
_apply_moving_boss = _apply_skull_boss


def _owned(mem):
    """Captured castles plus the home castle, unless the Y map showed it taken (g407 03:53)."""
    home = set() if mem.get('home_lost') else {chart.home_castle(mem.get('chapter') or 0)}
    return set(mem.get('captured') or []) | home


def _hero_castle(mem):
    """The owned castle the hero is last known to stand in, or None.

    Known from a read general list, or the castle the hero last took. A
    recent sortie of the hero means he is out in the field.
    """
    if NAME in _en_route(mem)[0]:
        return None
    owned = _owned(mem)
    castles = chart.castles(mem.get('chapter') or 0)
    for castle, present in (_world(mem).get('garrison') or {}).items():
        if castle in owned and castle in castles and NAME in (present or ()):
            return castle
    return None


# ---------------------------------------------------------------- house (あたしの家)
# Measured field view position of the house per chapter (moved from the
# house flow so the economy strategy can read it without importing a flow).
HOUSE_VIEW = chart.HOUSE_VIEW


def _repair_hp_ready(info):
    hp, maximum = info.get('hp'), info.get('max_hp')
    return type(hp) is int and type(maximum) is int and maximum > 0 and hp == maximum


def _castle_status_after_sortie(info, sortie, mem):
    """A fresh named status permits rechecking a castle, not assuming arrival.

    A missing arrival toast must not make an old en_route hint permanent.
    The later castle picker still requires the actual named resident and a
    remaining defender before it permits any dispatch.
    """
    now, observed, launched = mem.get('tick'), info.get('observed_tick'), sortie.get('tick')
    return (info.get('location') == 'castle'
            and info.get('general') == sortie.get('general')
            and type(mem.get('chapter')) is int and mem['chapter'] > 0
            and type(info.get('observed_chapter')) is int
            and info['observed_chapter'] == mem.get('chapter')
            and isinstance(mem.get('month'), str) and bool(mem['month'])
            and info.get('month') == mem.get('month')
            and all(type(value) is int for value in (now, observed, launched))
            and 0 <= launched < observed <= now and now - observed <= 400)
