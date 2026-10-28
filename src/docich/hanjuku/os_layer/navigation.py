"""OS層: map cursor navigation toward a goal chosen by a higher layer.

Bounded cursor holds, stuck detection, ring search, the Y world-map jump and
roof alignment. Given a goal cell this module produces the next pad input; it
does not pick the goal.
"""
from __future__ import annotations

from .screen import Screen, castle_roofs
from ... import hanjuku_chart as chart
from ..decision_log import _record
from .commands import pad
from .readers import _cursor, world_cursor, world_flags
from .world import _apply_world_flags, _hero_castle, update_world


MAX_HOLD_FRAMES = 28          # 1 px/frame below 30 frames; beyond it accelerates
ARRIVE_PX = 4


UNVERIFIED_LIMIT = 3                  # unanchored arrivals held before confirming anyway
NAV_STILL_LIMIT = 3                   # pressed frames with no motion before distrusting the cell


def _nav_stuck(screen, mem, world, s):
    """Distrust a cell that pressing no longer changes.

    g401 21:31: one roof at the top-left (the home castle) was voted as ジョンリギ, so
    the cell said アルマムーン was below; the cursor sat pinned at the map's
    bottom-right corner on open sea and "down" was pressed for 16 minutes.
    When neither the screen cursor nor the cell moves across pressed frames,
    drop to the inland search, which only re-anchors on two or more roofs.
    """
    key = [list(s), list(world)]
    pressed = bool(mem.get('nav_pressed'))
    still = pressed and mem.get('nav_prev') == key
    mem['nav_prev'] = key
    mem['nav_still'] = int(mem.get('nav_still') or 0) + 1 if still else 0
    mem['nav_pressed'] = False
    if mem['nav_still'] < NAV_STILL_LIMIT:
        return False
    mem['nav_still'] = 0
    mem['uncertain'] = True
    mem['nav_search'] = True
    mem.pop('nav_search_leg', None)
    mem.pop('anchor', None)
    _record(mem, 'nav_stuck', screen=screen.kind,
            observed_metric={'cursor': list(world), 'screen_cursor': list(s),
                             'roofs': mem.get('roofs_seen'), 'pressed_frames': NAV_STILL_LIMIT},
            reason='押してもカーソルも推定位置も動かないため位置推定を捨て、内陸を探索して複数の屋根で再特定する')
    return True


SEARCH_RING_PX = 120                  # spiral step around the castles' centroid
SEARCH_RINGS = 4


def _search_goal(mem):
    """Search waypoint: the castles' centroid (inland), then a widening
    square spiral around it. g389 16:41: reaching the centroid by dead
    reckoning alone still showed only water, and the old up/down nudge there
    never uncovered a roof."""
    cells = list(chart.castles(mem.get('chapter') or 1).values())
    cx, cy = sum(x for x, _ in cells) // len(cells), sum(y for _, y in cells) // len(cells)
    leg = int(mem.get('nav_search_leg') or 0)
    if leg <= 0:
        return (cx, cy)
    ring = (leg - 1) // 4 % SEARCH_RINGS + 1
    ux, uy = ((0, -1), (1, 0), (0, 1), (-1, 0))[(leg - 1) % 4]
    return (cx + ux * ring * SEARCH_RING_PX, cy + uy * ring * SEARCH_RING_PX)


def nav_step(screen: Screen, mem, frame, goal, goal_name=None):
    """Holds toward ``goal`` (map cell); 'arrived' within ARRIVE_PX.

    After a failed castle menu (``nav_search``) the estimate is known to be
    wrong, typically pushed past the coast where no roof is visible (g358:
    the camera froze on open water for 140 s). Until roofs re-anchor, steer
    toward the castles' centroid instead of the goal so land comes into view.
    """
    world = update_world(screen, mem, frame, goal_name)
    s = _cursor(screen)
    if not world or not s:
        return None
    if _nav_stuck(screen, mem, world, s):
        return None
    if mem.get('uncertain') and not mem.get('anchor'):
        mem['unanchored'] = int(mem.get('unanchored') or 0) + 1
    else:
        mem.pop('unanchored', None)
    hero_at = _hero_castle(mem)
    tick = int(mem.get('tick') or 0)
    if (hero_at and mem.get('uncertain') and int(mem.get('unanchored') or 0) >= SELECT_AFTER
            and tick - int(mem.get('select_tick') if mem.get('select_tick') is not None else -SELECT_EVERY)
            >= SELECT_EVERY
            and screen.cursor and not screen.marker):
        # Owner hint: SELECT to find the cursor again. SELECT centres the
        # camera on the hero; with the hero's castle known, the cursor is on
        # that castle's cell, so the lost cell is re-placed at once instead
        # of wandering (g421: 11 minutes of cursor walking in 2 hours).
        mem['select_used'] = True
        mem['select_tick'] = tick
        mem['nav_last'] = None
        mem['cursor'] = list(chart.castles(mem.get('chapter') or 0)[hero_at])
        mem.pop('nav_search', None)
        mem.pop('nav_search_leg', None)
        _record(mem, 'select_to_hero', screen=screen.kind, target=hero_at,
                observed_metric={'cursor_before': list(world), 'screen_cursor': list(s),
                                 'hero_castle': hero_at, 'roofs': mem.get('roofs_seen')},
                reason='位置を見失ったためSELECTで主人公の城へカーソルを戻し、その城の座標から再開する')
        return [pad('select')]
    if (mem.get('uncertain') and mem.get('nav_search') and not mem.get('select_used')
            and screen.cursor and not screen.marker):
        # SELECT centres the camera on the hero, who is usually at or near a
        # castle: roofs come into view far sooner than an inland spiral from
        # a wrong cell (owner hint 2026-09-28, measured in the isolated
        # emulator: the cursor lands at the screen centre). The jump is not
        # our measured motion, so nothing is integrated for it.
        mem['select_used'] = True
        mem['nav_last'] = None
        _record(mem, 'select_to_hero', screen=screen.kind,
                observed_metric={'cursor': list(world), 'screen_cursor': list(s),
                                 'roofs': mem.get('roofs_seen')},
                reason='位置を見失ったためSELECTで主人公の周辺を映し、屋根で再特定する')
        return [pad('select')]
    search = bool(mem.get('uncertain') and mem.get('nav_search'))
    if search:
        goal = _search_goal(mem)
    dx, dy = goal[0] - world[0], goal[1] - world[1]
    if search and abs(dx) <= ARRIVE_PX and abs(dy) <= ARRIVE_PX:
        # Waypoint reached with no anchor yet: go on to the next spiral leg.
        # The record keeps a snapshot of what the camera shows (evidence).
        mem['nav_search_leg'] = int(mem.get('nav_search_leg') or 0) + 1
        _record(mem, 'nav_search_leg', screen=screen.kind,
                observed_metric={'leg': mem['nav_search_leg'], 'cursor': list(world),
                                 'screen_cursor': list(s), 'roofs': mem.get('roofs_seen')},
                reason='屋根が見つからないため探索点を内陸の渦巻きの次の点へ進める')
        goal = _search_goal(mem)
        dx, dy = goal[0] - world[0], goal[1] - world[1]
    if abs(dx) <= ARRIVE_PX and abs(dy) <= ARRIVE_PX:
        held = int(mem.get('unverified') or 0)
        if mem.get('anchor') and not mem.get('uncertain'):
            mem.pop('unverified', None)
            return 'arrived'
        if held < UNVERIFIED_LIMIT and (mem.get('uncertain') or (mem.get('roofs_seen') or 0) >= 2):
            # Dead reckoning alone reached the cell. Camera scrolls at the
            # screen edge are only estimated, so the error builds up (g403
            # 23:04: ココット was sent ~60 px north of ジョンリギ) and a roof
            # anchor more than 48 px away is then refused. Distrust the cell
            # so the next roof reading re-anchors it before confirming.
            # Bounded: with no roof in view (a clipped goal at the screen
            # edge) nudging looped for 70 s (g403 23:24, v12).
            mem['uncertain'] = True
            mem['unverified'] = held + 1
            _record(mem, 'arrival_unverified', screen=screen.kind,
                    observed_metric={'cursor': list(world), 'screen_cursor': list(s),
                                     'roofs': mem.get('roofs_seen'), 'held': held + 1},
                    reason='屋根で位置を確認できないまま到着と推定したため、決定せず屋根で再特定する')
            mem['nav_last'] = {'screen': list(s), 'expected': [0, 0], 'search': search,
                               'tick': int(mem.get('tick') or 0), 'kind': screen.kind}
            return [pad('up', 12)] if s[1] > 100 else [pad('down', 12)]
        mem.pop('unverified', None)
        mem['uncertain'] = False
        return 'arrived'
    actions, expected = [], [0, 0]
    for axis, d, neg, pos in ((0, dx, 'left', 'right'), (1, dy, 'up', 'down')):
        if abs(d) > ARRIVE_PX:
            frames = min(abs(d), MAX_HOLD_FRAMES)
            actions.append(pad(pos if d > 0 else neg, frames))
            expected[axis] = frames if d > 0 else -frames
    mem['nav_last'] = {'screen': list(s), 'expected': expected, 'search': search,
                       'tick': int(mem.get('tick') or 0), 'kind': screen.kind}
    mem['nav_pressed'] = bool(actions)
    return actions


# Y jump (owner hint 2026-09-28: "select the castle on the Y map instead of
# moving the cursor"). Measured in the isolated emulator: the Y view shows a
# cursor - a white dashed ring on the map, gold-cornered G while choosing a
# sortie target - centred at cell/8 + offset; the D-pad moves it 0.5 px per
# frame and A returns with the map cursor (or target marker) on that cell.
# Jumps to キカンドン, ジョンリギ, スペンソニア and アルマムーン landed 4-9 px from
# each roof, and the target marker 5 px from キカンドン's roof. Roof-based
# cursor motion was the source of most mis-sorties (lone-roof mix-ups,
# edge-scroll drift of ~60 px), so far goals go through Y instead.
Y_JUMP_OFFSET = chart.Y_JUMP_OFFSET
Y_JUMP_FAR = 48                # world px from the goal before a jump is worth it
Y_JUMP_LIMIT = 6               # jumps per order and screen mode (reset when the order starts).
                               # 2 ran out mid-order and left roof walking (g421 15:25: SELECT loops)
Y_JUMP_MOVES = 8               # D-pad steps inside one jump before confirming anyway
Y_JUMP_WAIT = 6                # frames without a readable cursor before closing Y
Y_JUMP_FINAL_TOL = 3.0         # view px: beyond this at the move limit the jump is abandoned
Y_JUMP_OPEN_GRACE = 6          # observations the old screen may still show after Y
Y_JUMP_TOL = 1.0               # view px (8 world px); roofs close the rest. 0.5 oscillated
Y_JUMP_STILL_LIMIT = 3         # readable observations unchanged after directional inputs


def _note_failed_jump(mem, jump, why):
    """Remember the closed jump so a view that never moved is not reopened.

    Y is a toggle: reopening it after a close that moved nothing only toggles
    the view again (g1075 2026-10-28: 69 closes at 0 moves, ~1 minute of Y
    blinking per order while the ring cursor sat still on the sea).
    """
    mem['y_jump_last'] = {'step': jump.get('step'), 'mode': jump.get('mode'),
                          'moves': int(jump.get('moves') or 0), 'why': why}


def _drop_stale_y_jump(screen, mem):
    """Back on a map screen with a jump still open: the Y view was closed by
    something else (g419 08:45: an event ended it at 8 moves and the stale
    jump blocked every later jump for 10 minutes)."""
    jump = mem.get('y_jump')
    if not jump:
        return False
    # Right after Y the old screen is still shown for a frame or two; only a
    # jump that already saw the view, or has waited long, was closed early
    # (g419 09:52: every target jump was dropped 3 s after opening, 0 moves).
    if not jump.get('seen') and int(mem.get('tick') or 0) - int(jump.get('tick') or 0) < Y_JUMP_OPEN_GRACE:
        return True                      # the view is still opening: press nothing
    mem.pop('y_jump', None)
    if jump:
        _note_failed_jump(mem, jump, 'view_closed')
        _record(mem, 'y_jump_failed', chart_step=jump.get('step'), target=jump.get('goal'),
                screen=screen.kind, observed_metric={'moves': jump.get('moves')},
                reason='全体マップが決定前に閉じたためジャンプを中断として扱う')
    return False


def _want_y_jump(mem, order, name, mode) -> bool:
    chapter = mem.get('chapter') or 0
    if chapter not in Y_JUMP_OFFSET or mem.get('y_jump'):
        return False
    last = mem.get('y_jump_last') or {}
    if (last.get('step') == order['step'] and last.get('mode') == mode
            and (not int(last.get('moves') or 0) or last.get('why') == 'cursor_stationary')):
        # The last jump for this order closed without moving the cursor. The
        # view is not opening or readable here, so walk instead of toggling Y.
        return False
    key = f"{order['step']}:{mode}"
    if (mem.get('y_jumps') or {}).get(key, 0) >= Y_JUMP_LIMIT:
        return False
    world, goal = mem.get('cursor'), chart.castles(chapter)[name]
    return not world or abs(world[0] - goal[0]) + abs(world[1] - goal[1]) > Y_JUMP_FAR


def _start_y_jump(mem, order, name, mode):
    key = f"{order['step']}:{mode}"
    jumps = mem.setdefault('y_jumps', {})
    jumps[key] = jumps.get(key, 0) + 1
    mem['y_jump'] = {'goal': name, 'mode': mode, 'step': order['step'], 'moves': 0, 'wait': 0,
                     'tick': int(mem.get('tick') or 0)}
    _record(mem, 'y_jump_open', chart_step=order['step'], target=name,
            observed_metric={'cursor': mem.get('cursor'), 'mode': mode, 'attempt': jumps[key]},
            reason='全体マップ（Y）で目的の城を直接選ぶ')
    return [pad('y')]


def _y_jump_step(mem, frame):
    jump = mem['y_jump']
    jump['seen'] = True
    chapter = mem.get('chapter') or 0
    cursor = world_cursor(frame)
    flags = world_flags(frame, chapter, cursor) if cursor else {}
    if flags and not jump.get('owners'):
        jump['owners'] = True
        _apply_world_flags(mem, flags)          # the same view also shows every owner
    if cursor is None:
        jump.pop('last_cursor', None)
        jump['still'] = 0
        jump['wait'] += 1
        if jump['wait'] < Y_JUMP_WAIT:
            return []
        mem.pop('y_jump', None)
        _note_failed_jump(mem, jump, 'cursor_unreadable')
        _record(mem, 'y_jump_failed', chart_step=jump['step'], target=jump['goal'],
                reason='全体マップのカーソルを読めないため閉じて通常の移動に戻る')
        return [pad('y')]
    gx, gy = chart.castles(chapter)[jump['goal']]
    ox, oy = Y_JUMP_OFFSET[chapter]
    dx, dy = gx / 8 + ox - cursor[0], gy / 8 + oy - cursor[1]
    # A roof misread as the G stays fixed even while the real cursor moves
    # (#2915). Count observations after issued inputs, not the initial read.
    # Never turn that stationary detection into an arrival or a new cell.
    previous = jump.get('last_cursor')
    jump['still'] = (int(jump.get('still') or 0) + 1
                     if previous == list(cursor) and jump['moves'] else 0)
    jump['last_cursor'] = list(cursor)
    if jump['still'] >= Y_JUMP_STILL_LIMIT:
        mem.pop('y_jump', None)
        _note_failed_jump(mem, jump, 'cursor_stationary')
        # Another mode can overwrite y_jump_last. Keep this order/mode's
        # budget exhausted until the normal order-start reset instead.
        mem.setdefault('y_jumps', {})[f"{jump['step']}:{jump['mode']}"] = Y_JUMP_LIMIT
        _record(mem, 'y_jump_failed', chart_step=jump['step'], target=jump['goal'],
                observed_metric={'moves': jump['moves'], 'stationary_reads': jump['still']},
                reason='移動入力後も検出カーソルが動かないため決定せず閉じて通常の移動に戻る')
        return [pad('y')]
    if jump['moves'] >= Y_JUMP_MOVES and not (abs(dx) <= Y_JUMP_FINAL_TOL and abs(dy) <= Y_JUMP_FINAL_TOL):
        # Never confirm far from the goal (g419 10:49: 50 px off after a misread).
        mem.pop('y_jump', None)
        _note_failed_jump(mem, jump, 'residual')
        _record(mem, 'y_jump_failed', chart_step=jump['step'], target=jump['goal'],
                observed_metric={'world_cursor': list(cursor), 'residual': [round(dx, 1), round(dy, 1)],
                                 'moves': jump['moves']},
                reason='全体マップのカーソルが目的の城に合わないため決定せず閉じる')
        return [pad('y')]
    if (abs(dx) <= Y_JUMP_TOL and abs(dy) <= Y_JUMP_TOL) or jump['moves'] >= Y_JUMP_MOVES:
        mem.pop('y_jump', None)
        mem['y_jump_return'] = jump['mode']
        if jump['mode'] == 'target':
            mem.setdefault('y_jumped', {})[jump['step']] = jump['goal']
        mem['near_goal'] = {'step': jump['step'], 'goal': jump['goal'], 'mode': jump['mode']}
        (mem.get('align_steps') or {}).pop(f"{jump['step']}:{jump['mode']}", None)
        mem.pop('menu_miss', None)            # a fresh placement, not the missed cell
        mem.pop('menu_hold', None)
        mem['cursor'] = [gx, gy]
        mem['uncertain'] = False
        for key in ('anchor', 'nav_last', 'nav_search', 'nav_search_leg'):
            mem.pop(key, None)
        _record(mem, 'y_jump_confirm', chart_step=jump['step'], target=jump['goal'],
                observed_metric={'world_cursor': list(cursor), 'residual': [round(dx, 1), round(dy, 1)],
                                 'moves': jump['moves']},
                reason='全体マップのカーソルを目的の城に合わせて決定')
        return [pad('a')]
    jump['moves'] += 1
    actions = []
    for d, neg, pos in ((dx, 'left', 'right'), (dy, 'up', 'down')):
        if abs(d) > Y_JUMP_TOL:
            actions.append(pad(pos if d > 0 else neg, min(56, max(1, round(abs(d) * 2)))))
    return actions


SELECT_AFTER = 2               # unanchored map frames before SELECT re-places the cursor
SELECT_EVERY = 40              # observations between hero-castle SELECTs


ALIGN_RADIUS = 28              # screen px: a roof this close to the cursor after a Y jump
ALIGN_TOL = 3                  # cursor-to-roof-target distance that selects the castle
ALIGN_LIMIT = 10               # aligning steps before the jump is redone


def _align_on_roof(screen, mem, frame, kinds, key):
    """Walk the cursor onto the nearest roof of the expected kind, on screen.

    After a Y jump the cursor lands 0-9 px from the castle. Trusting the
    world estimate there dropped into roof-voting and the inland search
    (g421 14:45: source_not_under_cursor -> SELECT -> 20+ minutes of
    wandering). The roof in view is the ground truth: steer by its offset.
    Returns 'on', a list of pad actions, or None (no such roof / too long).
    """
    s = _cursor(screen)
    if not s or frame is None:
        return None
    roofs = castle_roofs(frame)
    if not roofs:
        # Winter repaints the map (g421 18:13: the G stood on フーリック's
        # castle, no roof was read, and the jump was reopened six times).
        # A confirmed jump lands on the castle; nothing on screen contradicts it.
        return 'on'
    near = [(abs(r['target'][0] - s[0]) + abs(r['target'][1] - s[1]), r) for r in roofs
            if r['kind'] in kinds and abs(r['target'][0] - s[0]) <= ALIGN_RADIUS
            and abs(r['target'][1] - s[1]) <= ALIGN_RADIUS]
    if not near:
        return None
    roof = min(near, key=lambda item: item[0])[1]
    dx, dy = roof['target'][0] - s[0], roof['target'][1] - s[1]
    if abs(dx) <= ALIGN_TOL and abs(dy) <= ALIGN_TOL:
        return 'on'
    steps = mem.setdefault('align_steps', {})
    steps[key] = steps.get(key, 0) + 1
    if steps[key] > ALIGN_LIMIT:
        return None
    actions = []
    for d, neg, pos in ((dx, 'left', 'right'), (dy, 'up', 'down')):
        if abs(d) > ALIGN_TOL:
            actions.append(pad(pos if d > 0 else neg, min(abs(d), MAX_HOLD_FRAMES)))
    return actions
