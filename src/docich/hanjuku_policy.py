"""Chart-driven deterministic Hanjuku policy (no model, provider or network).

``step`` maps one parsed frame and the persisted policy memory to bounded pad
actions plus structured decision records. Every non-trivial choice records the
situation, the chart step, the strategy variant and, when the chart is not
followed exactly, the deviation reason with the expected and observed metrics.
Unknown screens and unreadable values are recorded as ``unclassified``; the
policy never invents a battle result, stage or amount.
"""
from __future__ import annotations

import re
from dataclasses import asdict

from . import hanjuku_chart as chart
from . import hanjuku_chart_adjust as chart_adjust
from . import hanjuku_experience as experience
from . import hanjuku_reference as reference
from .hanjuku_egg_reference import enemy_egg_triggers, general_debut_chapter, general_max_hp
from .hanjuku_font import UNKNOWN, TextLine
from .hanjuku_screen import HEADER as HEADER_RE, OKUNOTE_CHOICES, Screen, castle_roofs, own_camps

NAME = chart.HERO
FPS = 60
MAX_HOLD_FRAMES = 28          # 1 px/frame below 30 frames; beyond it accelerates
EDGE_X = (8, 232)             # cursor cell clamps; the camera scrolls beyond
EDGE_Y = (8, 200)
ARRIVE_PX = 4
ANCHOR_PX = 24                # roof-to-castle association radius (castles >=100 px apart)
BATTLE_MENU = ('たまごをつかう', 'きりふだ', 'たいきゃく')
AFTER_BATTLE_KINDS = frozenset({'map', 'map_target', 'castle_menu', 'general_list', 'month_menu',
                                'attack_started', 'defense_started', 'castle_info', 'main_menu',
                                'yes_no', 'shop_list', 'boss_attack_started'})
CARD_NAMES = frozenset({
    'イッテツーン', 'ダイチスイム', 'ブラッキー', 'フットバース', 'グリンボー', 'ピッグローラー',
    'カンケリン', 'ノリウツール', 'クースカン', 'ゼンマイン', 'ミックミー', 'デッドガン',
    'ブレイコウ', 'ブンシーン', 'ファイアーボイス', 'ファバード', 'エンジェリン', 'マグネガキン',
    'ハリケーン', 'キャトルミュー'})
# Every real card name (the 32-card gcgx table). The panel reader accepts
# these as rows; only CARD_NAMES can be planned or selected.
ALL_CARD_NAMES = frozenset(reference.ALL_CARD_IDS)


def pad(button, frames=6):
    return {'type': 'pad', 'buttons': [button], 'hold_ms': max(16, round(frames * 1000 / FPS))}


def _record(mem, kind, **fields):
    rec = {'decision': kind, 'chart_step': fields.pop('chart_step', mem.get('active')),
           'strategy_variant': fields.pop('strategy_variant', mem.get('variant', 'chart')),
           'chapter': mem.get('chapter'), 'deviation_reason': None,
           'expected_metric': None, 'observed_metric': None,
           'resulting_stage': None, 'resulting_event': None}
    rec.update(fields)
    mem.setdefault('_records', []).append(rec)
    return rec


def _tally(mem, key, amount=1):
    """Bounded, monotonically increasing evidence counter kept beside stats.

    `stats` only advances on a battle whose final HP panel was decisive, so a
    defeat seen on the world map (or a battle that ended before the panel was
    read) left no trace there. The corner status read "0 losses" while castles
    were visibly falling, so these counters give the status panel a complete,
    non-strategy-facing account. Nothing here feeds retries or experience.
    """
    counters = mem.setdefault('tally', {})
    value = counters.get(key)
    if type(value) is not int or value < 0:
        value = 0
    value = min(value + amount, 10 ** 6)
    counters[key] = value
    return value


# ---------------------------------------------------------------- menus
def _options(screen: Screen) -> list[tuple[int, int, str]]:
    return [(x, line.y, word) for line in screen.lines for x, word in line.spans()]


def _current(screen: Screen):
    if not screen.hand:
        return None
    x0, y0, x1, y1 = screen.hand
    row = y0 + 6
    cands = [(x, y, w) for x, y, w in _options(screen) if abs(y - row) <= 2 and x >= x1 - 4]
    # A blank cell (e.g. right of ょ in the kana grid) still has a position.
    return min(cands, default=None) or (((x1 - 4 + 7) // 8) * 8, row, '')


def menu_to(screen: Screen, label: str, *, exact=True):
    """One press toward ``label`` (menu with a hand cursor); 'here' when on it."""
    cur = _current(screen)
    target = [(x, y, w) for x, y, w in _options(screen)
              if (w == label if exact else w.startswith(label))]
    if not cur or not target:
        return None
    if cur[2] == label or (not exact and cur[2].startswith(label)):
        return 'here'
    tx, ty, _ = min(target, key=lambda t: abs(t[1] - cur[1]) + abs(t[0] - cur[0]))
    if abs(ty - cur[1]) > 2:
        return pad('down' if ty > cur[1] else 'up')
    return pad('right' if tx > cur[0] else 'left')


# ---------------------------------------------------------------- name entry
# ひらがな page of the name grid (measured). The hand can hide the glyph it
# passes over, so the fixed layout supplies target cells OCR cannot see.
KANA_GRID = ('あいうえおらりるれろ', 'かきくけこわをん  ', 'さしすせそっゃゅょ ',
             'たちつてとがぎぐげご', 'なにぬねのざじずぜぞ', 'はひふへほだぢづでど',
             'まみむめもばびぶべぼ', 'やゆよ  ぱぴぷぺぽ')


def kana_cell(ch):
    for r, row in enumerate(KANA_GRID):
        c = row.find(ch)
        if c >= 0:
            return (80 + 16 * c if c < 5 else 168 + 16 * (c - 5)), 87 + 16 * r
    return None


def name_step(screen: Screen, mem):
    box = next((line for line in screen.lines if 48 <= line.y <= 62), None)
    typed = ''.join(box.span(64, 160).split()) if box else ''
    name = mem.setdefault('name', {'target': NAME, 'done': False, 'inputs': 0})
    name['typed'] = typed
    if UNKNOWN in typed:
        _record(mem, 'name_wait', chart_step='name', typed=typed,
                reason='状況判定保留: 名前欄に未分類の文字があるため入力を保留')
        return []
    if typed == NAME:
        _record(mem, 'name_confirm', chart_step='name', typed=typed,
                reason='名前欄の実表示がどうしに一致したためSTARTで確定')
        name['done'] = True
        return [pad('start')]
    if not NAME.startswith(typed):
        _record(mem, 'name_delete', chart_step='name', typed=typed, reason='名前欄が目標の前方一致でないため1文字削除')
        return [pad('b')]
    want = NAME[len(typed)]
    if screen.hand and screen.hand[0] < 40:
        # Hand on the page menu (ひらがな/カタカナ/...; x0 about 17), not the
        # grid's first column (x0 about 58): enter the kana grid.
        return [pad('a')]
    cur = _current(screen)
    cells = [(x, y, w) for x, y, w in _options(screen) if w == want and x >= 80 and y >= 80]
    if not cells and kana_cell(want) and 'ひらがな' in screen.text:
        cells = [(*kana_cell(want), want)]
    if not cur or not cells:
        _record(mem, 'name_wait', chart_step='name', typed=typed, reason='カーソルまたは目標文字を判定保留')
        return []
    if cur[2] == want:
        _record(mem, 'name_type', chart_step='name', typed=typed, char=want,
                reason=f'カーソルが「{want}」上にあるためAで入力')
        return [pad('a')]
    tx, ty, _ = cells[0]
    if abs(ty - cur[1]) > 2:
        return [pad('down' if ty > cur[1] else 'up')]
    return [pad('right' if tx > cur[0] else 'left')]


# ---------------------------------------------------------------- map cursor
def _cursor(screen: Screen):
    return screen.marker or screen.cursor


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
    # A search step (see ``nav_step``) is our own measured motion, so it
    # integrates even while uncertain; motion from before an off-map screen
    # never does.
    if world and last and last.get('screen') and (not mem.get('uncertain') or last.get('search')):
        moved = []
        for axis, bounds in ((0, EDGE_X), (1, EDGE_Y)):
            if _at_edge(s[axis], bounds) or _at_edge(last['screen'][axis], bounds):
                moved.append(last['expected'][axis])      # camera scrolled: unmeasured
            else:
                moved.append(s[axis] - last['screen'][axis])
        world = [world[0] + moved[0], world[1] + moved[1]]
    mem['nav_last'] = None
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
    fixed = {chart.home_castle(chapter), chart.boss_castle(chapter)}
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
        streak[castle] = {'kind': kind, 'count': count}
        if count < OWNER_CONFIRM:
            continue
        from .hanjuku_roster import owner
        owner(mem, STATUS_NAMES.get(castle, castle), kind)
        if castle in fixed:
            continue
        captured = mem.setdefault('captured', [])
        if kind == 'enemy' and castle in captured:
            (mem.get('castle_income') or {}).pop(STATUS_NAMES.get(castle, castle), None)
            mem['captured'] = [c for c in captured if c != castle]
            lost = mem.setdefault('lost', [])
            if castle not in lost:
                lost.append(castle)
            (mem.get('garrison') or {}).pop(castle, None)
            _record(mem, 'castle_lost_observed', castle=castle,
                    observed_metric={'roof': kind, 'readings': count},
                    resulting_event=f'lost:{castle}',
                    reason='占領していた城の屋根が敵の色になったため失陥として奪還対象にする')
            _tally(mem, 'castle_losses')
        elif kind == 'own' and castle not in captured:
            (mem.get('castle_income') or {}).pop(STATUS_NAMES.get(castle, castle), None)
            captured.append(castle)
            if castle in (mem.get('lost') or []):
                mem['lost'] = [c for c in mem['lost'] if c != castle]
            _record(mem, 'castle_owned_observed', castle=castle,
                    observed_metric={'roof': kind, 'readings': count},
                    resulting_event=f'captured:{castle}',
                    reason='城の屋根が自軍の色のため占領として扱う')


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
            mem['nav_last'] = {'screen': list(s), 'expected': [0, 0], 'search': search}
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
    mem['nav_last'] = {'screen': list(s), 'expected': expected, 'search': search}
    mem['nav_pressed'] = bool(actions)
    return actions


# ---------------------------------------------------------------- orders
def _sortie_general(order, mem):
    """Keep a confirmed departure's actor even if a plan/override changes."""
    step = order.get('step')
    context = (mem.get('order_context') or {}).get(step) or {}
    if mem.get('active') == step:
        selected = context.get('actual_general') or (mem.get('sortie_general') or {}).get(step)
        if selected:
            return selected
    return (mem.get('general_override') or {}).get(step, order.get('general'))


def _hero_alternatives(mem, names):
    """Recognized idle companions; actual list selection must confirm each name.

    Garrison memory only permits opening the list, never a selection receipt.
    List order is retained; this is no prediction that a companion will win.
    """
    if not names or len(names) != len(set(names)):
        return []
    unavailable = _en_route(mem)[0] | set(mem.get('general_location_unknown') or ())
    return [g for g in names if g != NAME and general_max_hp(g) is not None and g not in unavailable]


def _hero_source_alternative(order, mem):
    return (order.get('general') == NAME
            and bool(_hero_alternatives(mem, (mem.get('garrison') or {}).get(_source(order, mem)))))


def _boss_egg_depleted(order, mem) -> bool:
    # g486: どうし left with 3 uses and lost to the Queen's Hydra after
    # the second card missed its input window. Chapter 1's source recommends
    # a fully recovered egg as the backup; do not invent a missing count.
    uses = (mem.get('egg_uses') or {}).get(NAME)
    return (mem.get('chapter') == 1 and _sortie_general(order, mem) == NAME
            and _is_boss_order(order, mem) and type(uses) is int and 0 <= uses < 4)


def _hero_egg_broken(mem):
    # Only a read status is authoritative. A house dispatch/timeout is not
    # repair; only a later real non-broken status releases this guard.
    return (mem.get('house_eggs') or {}).get(NAME, {}).get('broken') is True


def _broken_hero_order(order, mem):
    general = _sortie_general(order, mem)
    return (general == NAME and _hero_egg_broken(mem)
            and order.get('purpose') != 'move'
            and order.get('target') in chart.castles(mem.get('chapter') or 0))


def _cancel_broken_hero_sortie(mem, order):
    _record(mem, 'hero_broken_egg_sortie_held', chart_step=order['step'],
            observed_metric={'target': order.get('target'), 'repair': 'unconfirmed'},
            reason='主人公の卵破損を観測済みで修復未確認のため攻撃出撃を保留する')
    mem.pop('sortie_attempt', None)
    _finish_order(mem, 'pending', reason='主人公の卵修復確認を待つ')
    return [pad('b')]


def _ready(order, mem) -> bool:
    if ((_broken_hero_order(order, mem) or _boss_egg_depleted(order, mem))
            and not _hero_source_alternative(order, mem)):
        return False
    after = order['after']
    captured = set(mem.get('captured', []))
    if after is None:
        return True
    if after[0] == 'captured':
        return after[1] in captured
    if after[0] == 'all_captured':
        chapter = mem.get('chapter') or 1
        chapter_castles = set(chart.castles(chapter)) - {
            chart.home_castle(chapter), chart.boss_castle(chapter)}
        return chapter_castles <= captured
    return False


def _orders(mem):
    """The order list in force: an adopted adjusted plan, else the base chart.

    The adopted plan (``chart_plan``) is separate from the request state
    (``chart_adjust``): a new off-chart request never discards a plan that is
    still waiting on a capture; only adopting a newer validated plan does.
    While no plan is waiting, one JEV-chosen interim order may run.
    """
    plan = mem.get('chart_plan') or {}
    interim = (mem.get('chart_adjust') or {}).get('interim_order')
    # An interim order only exists when no plan order is waiting (see
    # ``_off_chart``), so it may follow an exhausted plan as well as the base.
    base = plan['orders'] if plan.get('orders') else chart.orders(mem.get('chapter') or 0)
    return (*base, *((interim,) if interim else ()))


def _order_for_step(mem, step):
    """The order behind an execution id, even after its plan was replaced.

    A launched order's snapshot (``launched_orders``) keeps its general, cards,
    target and tactics until its battle and retries are done.
    """
    if not step:
        return None
    return (next((o for o in (*_orders(mem), *chart.orders(mem.get('chapter') or 0))
                  if o['step'] == step), None)
            or (mem.get('launched_orders') or {}).get(step))


def _is_boss_order(order, mem) -> bool:
    chapter = mem.get('chapter') or 0
    return bool(order) and order.get('target') == chart.boss_castle(chapter)


def _plan_pending(mem) -> bool:
    """An adopted plan still has an order that can run, or one waiting on a
    capture that a marching unit is making.

    Orders that can never run (source lost, capture nobody is making) kept
    off-chart sorties (retake, staffing) off for an hour in g407.
    """
    status = mem.get('orders') or {}
    owned = _owned(mem)
    sorties = mem.get('sorties') or {}
    plan = (mem.get('chart_plan') or {}).get('orders') or ()
    # A recent sortie, or a launched plan order with no sortie record to age.
    heading = _en_route(mem)[1] | {o['target'] for o in plan
                                   if status.get(o['step']) == 'launched' and o['step'] not in sorties}

    garrison = mem.get('garrison') or {}

    def live(o):
        after = o.get('after') or ()
        if after and after[0] == 'captured' and after[1] not in owned:
            return after[1] in heading          # waits on a capture somebody is making
        # A source read empty cannot start the order (g438: dead ココット's J1).
        return (_ready(o, mem) and _source(o, mem) in owned
                and garrison.get(_source(o, mem)) != []
                and not _retake_reserved(o, mem) and not _source_spare_missing(o, mem))

    return any(status.get(o['step']) in (None, 'pending') and live(o) for o in plan)


def _boss_retry_due(order, mem) -> bool:
    """A boss sortie failed only by cancelled targets gets its remaining retries
    (g421 13:48: the tower-roof check cancelled どうし twice before #1251)."""
    return ((mem.get('orders') or {}).get(order['step']) == 'failed'
            and order.get('target') == chart.boss_castle(mem.get('chapter') or 0)
            and 0 < (mem.get('target_cancel') or {}).get(order['step'], 0) < BOSS_TARGET_CANCEL_LIMIT)


def next_order(mem):
    status = mem.setdefault('orders', {})
    current = list(_orders(mem))
    steps = {o['step'] for o in current}
    # A launched order put back to pending by a lost battle keeps its retry
    # even if a newer plan replaced the one it came from.
    retries = [o for step, o in (mem.get('launched_orders') or {}).items()
               if step not in steps and status.get(step) == 'pending']
    owned = _owned(mem)
    garrison = mem.get('garrison') or {}
    # The base chart's remaining orders follow an adopted plan that cannot run
    # (g438 03:48: the plan's only order sent the dead ココット from an empty
    # ジョンリギ, so the bot idled with every castle but the boss's taken while
    # the base 1-B1 boss sortie was never looked at).
    base = [o for o in chart.orders(mem.get('chapter') or 0)
            if o['step'] not in steps and o['target'] not in owned] if mem.get('chart_plan') else []
    chart_steps = {o['step'] for o in chart.orders(mem.get('chapter') or 0)}
    for order in (*current, *retries, *base):
        if (order['step'] in chart_steps
                and (status.get(order['step']) in (None, 'pending') or _boss_retry_due(order, mem))
                and _ready(order, mem)):
            _follow_general(mem, order, garrison, owned)
        if ((status.get(order['step']) in (None, 'pending') or _boss_retry_due(order, mem))
                and _ready(order, mem)
                and _source(order, mem) in owned
                # A source last read empty would open an empty list and fail
                # the order (g407: the plan's どうし/ヴィーナス from an empty home).
                and garrison.get(_source(order, mem)) != []
                and not _last_castle_held(mem, order)      # its castle must keep its last general
                and not _retake_reserved(order, mem)
                and not _source_spare_missing(order, mem)
                and (mem.get('general_override', {}).get(order['step'], order['general']) not in _en_route(mem)[0]
                     or _hero_source_alternative(order, mem))):
            return order
    return None


def _follow_general(mem, order, garrison, owned):
    """Send the order from the castle its general is recorded in (g438: 1-B1
    assumes どうし at スペンソニア, but he held ゴーメン)."""
    general = order.get('general')
    source = _source(order, mem)
    if general == NAME and _hero_alternatives(mem, garrison.get(source)):
        return  # inspect the chart source's companion rather than chase the hero
    if not general or general in (garrison.get(source) or ()):
        return
    where = next((c for c, names in garrison.items() if general in (names or ()) and c in owned), None)
    if where and where != source and not mem.get('source_override', {}).get(order['step']):
        mem.setdefault('source_override', {})[order['step']] = where
        _record(mem, 'order_source_changed', chart_step=order['step'], strategy_variant='follow_general',
                observed_metric={'source': source, 'general_at': where},
                reason=f'{general}は{where}にいると記録されているため、そこから出撃させる')


def _order(mem):
    return _order_for_step(mem, mem.get('active'))


def _tactics(mem, step):
    """Battle tactics for a step: the base chart's, plus derived ones for adjusted
    and interim orders (their steps never match a base tactic's step).

    A carried card reuses every verified base tactic for that card (its enemy
    and timing), re-keyed to this step. A card with no verified tactic uses the
    explicit default: once at the battle opening, the same mechanism and
    evidence guards as a retry's opening cards. An added strong-card kit is
    handled only by its strong-only tactic below. Only cards the sortie actually
    carried count (``_deploy_cards``): a card left behind at card select must
    never be planned or announced in battle (g438 04:18: the bot opened the
    card menu for ミックミー after the sortie had dropped it). A boss-castle
    order's unverified card opens only in the measured boss entry, so the boss
    kit never fires in the road/guard fights on the way there.
    """
    base = chart.tactics(mem.get('chapter') or 0)
    order = _order_for_step(mem, step)
    rare_kit = (mem.get('rare_card_kit') or {}).get(step)
    strong_kit = None if rare_kit is not None else (mem.get('strong_card_kit') or {}).get(step)
    rare = ()
    carried = None
    if rare_kit is not None and order:
        carried = list(((mem.get('order_context') or {}).get(step) or {}).get('observed_metric', {}).get('cards') or [])
        for card in (mem.get('kit_spent') or {}).get(step) or ():
            if card in carried:
                carried.remove(card)
        base = tuple(t for t in base if t.get('step') not in (None, step)
                     or t['card'] in carried)
        if 'キャトルミュー' in carried:
            rare = ({'enemy': None, 'card': 'キャトルミュー', 'open': True, 'step': step,
                     'tactic_id': f'rare:{step}:キャトルミュー',
                     'boss_only': _is_boss_order(order, mem),
                     'note': 'レアイベント札を活用: 通常将軍を一撃、EMへ224と石化、ボスへ90ダメージ'},)
    if order is None or any(o['step'] == step for o in chart.orders(mem.get('chapter') or 0)):
        return (*rare, *base)
    override = set() if rare_kit is not None else set((mem.get('card_override') or {}).get(step) or ())
    if carried is None:
        carried = _deploy_cards(order, mem)
    carried = [card for card in carried
               if card not in override and card != strong_kit
               and not (rare and card == 'キャトルミュー')]
    derived, seen = [], set()
    occurrences = {}
    spent = (mem.get('kit_spent') or {}).get(step, [])
    for card in carried:
        verified = [t for t in base if t['card'] == card]
        if verified:
            if card in seen:
                continue
            seen.add(card)
            derived += [{**t, 'step': step, 'note': f"調整: {t['note']}"} for t in verified]
        else:
            ordinal = spent.count(card) + occurrences.get(card, 0)
            occurrences[card] = occurrences.get(card, 0) + 1
            # Removing the first spent card must not renumber the second
            # into an already-done tactic (two carried イッテツーン).
            derived.append({'enemy': None, 'card': card, 'open': True, 'step': step,
                            'tactic_id': f'd:{step}:{card}:{ordinal}',
                            'boss_only': _is_boss_order(order, mem),
                            'note': '調整チャート既定: 検証済み戦術のない携行切り札を開幕使用'})
    return (*rare, *base, *derived)


INTERIM_LIMIT = 2             # JEV answers per off-chart situation
INTERIM_MIN_CONFIDENCE = 0.7


def _distance(chapter, a, b) -> int:
    castles = chart.castles(chapter)
    (ax, ay), (bx, by) = castles[a], castles[b]
    return abs(ax - bx) + abs(ay - by)


SORTIE_BUSY_TICKS = 400       # observations (~10 min at 1.5 s) a marching general stays busy


def _en_route(mem):
    """(generals, targets) of sorties launched but not yet seen arriving.

    Only recent ones: an arrival that was never read kept どうし and ココット
    "marching" for over an hour in g407 (01:38-02:39), which blocked every
    order of the adopted plan and, through it, every off-chart sortie. A
    sortie without a tick predates this bound and no longer counts.
    """
    now = int(mem.get('tick') or 0)
    sorties = [s for s in (mem.get('sorties') or {}).values()
               if s.get('status') in ('en_route', 'launched_unconfirmed')
               and s.get('tick') is not None and now - int(s['tick']) < SORTIE_BUSY_TICKS]
    return {s.get('general') for s in sorties}, {s['target'] for s in sorties if s.get('target')}


def _sortie_reserved_target(mem, step, sortie):
    if sortie.get('target'):
        return sortie['target']
    if sortie.get('status') == 'launched_unconfirmed':
        # Reserve the intended destination without claiming observed arrival.
        # An interrupted target selection must not dispatch reinforcements.
        return ((mem.get('launched_orders') or {}).get(step) or {}).get('target')
    return None


def _reserved_targets(mem):
    now = int(mem.get('tick') or 0)
    return {_sortie_reserved_target(mem, step, s)
            for step, s in (mem.get('sorties') or {}).items()
            if s.get('status') in ('en_route', 'launched_unconfirmed')
            and s.get('tick') is not None
            and 0 <= now - int(s['tick']) < SORTIE_BUSY_TICKS} - {None}


def _retake_order(order, mem):
    return (not _is_boss_order(order, mem)
            and (order.get('purpose') == 'retake'
                 or order.get('target') in (mem.get('lost') or ())))


def _reserve_source_guard(order, mem):
    # Original chart offensives and boss waves retain their sequences. A
    # recapture or an off-chart attack must leave a measured idle defender.
    return (_retake_order(order, mem)
            or (order.get('purpose') == 'attack' and order['step'].startswith('I:')))


def _retake_reserved(order, mem):
    if (not _retake_order(order, mem)
            or (mem.get('orders') or {}).get(order['step']) == 'launched_unconfirmed'):
        return False
    now = int(mem.get('tick') or 0)
    return any(step != order['step'] and _sortie_reserved_target(mem, step, s) == order.get('target')
               and s.get('status') in ('en_route', 'launched_unconfirmed')
               and s.get('tick') is not None
               and 0 <= now - int(s['tick']) < SORTIE_BUSY_TICKS
               for step, s in (mem.get('sorties') or {}).items())


def _source_spare_missing(order, mem):
    if (mem.get('orders') or {}).get(order['step']) == 'launched_unconfirmed':
        return False  # reconcile an issued sortie; do not cancel its carried-kit receipt
    present = (mem.get('garrison') or {}).get(_source(order, mem))
    unavailable = _en_route(mem)[0] | set(mem.get('general_location_unknown') or ())
    return (_reserve_source_guard(order, mem) and present is not None
            and len(set(present) - unavailable) < 2)


def _interim_source(mem, target, chart_order, owned, busy):
    """(source, general) for an interim sortie to ``target``, or None.

    A castle whose general list we last read is used as measured: an empty one
    is never a source (g401: the chart general had already left キカンドン and
    the bot pressed A there for 14 minutes). Without a reading, fall back to
    the chart's source, then the home castle, as before.
    """
    chapter = mem.get('chapter') or 0
    home = chart.home_castle(chapter)
    garrison = mem.get('garrison') or {}
    busy = busy | set(mem.get('general_location_unknown') or ())
    unavailable_for_attack = {NAME} if _hero_egg_broken(mem) else set()
    free = {c: [g for g in garrison.get(c) or () if g not in busy] for c in owned}
    # Emptying a castle is how the undefended ジョンリギ fell (g401), so a
    # castle that keeps somebody behind is required, then the nearest goes.
    staffed = sorted((c for c in owned if len(set(free[c])) >= 2
                      and set(free[c]) - unavailable_for_attack),
                     key=lambda c: _distance(chapter, c, target))
    if staffed:
        present = [g for g in free[staffed[0]] if g not in unavailable_for_attack]
        present.sort(key=lambda g: g == NAME)          # risk the hero last
        return staffed[0], present[0]
    general = chart_order['general'] if chart_order else NAME
    if general in busy or general in unavailable_for_attack:
        return None
    for source in ((chart_order or {}).get('source'), home):
        if source in owned and garrison.get(source) is None:
            return source, general
    return None


MOVE_ANY_GENERAL = 'しょうぐん'    # move from an unread list: the spare is picked on the list


INTERIM_CARDS = ('イッテツーン', 'イッテツーン')   # same opener as a lost melee's retry


def interim_candidates(mem) -> dict:
    """Deterministic off-chart sorties JEV may choose from while a chart is pending.

    Retake castles we lost, then staff owned castles last seen empty (the home
    castle first) with a spare general from a castle that keeps somebody,
    then attack every other uncaptured non-boss castle (chart targets first).
    Moves come before attacks because the first candidate is also the
    fallback: listed last they never ran (g403-g407), while undefended
    castles fell without a battle and g407 left the home castle empty.
    Sources and generals come from measured general lists when known
    (``garrison``), so an idle general at any castle is used instead of only
    the chart's general. Attacks carry cards (INTERIM_CARDS). There is no hold
    label: JEV must pick one, and an unusable answer falls back to the first
    candidate. JEV never produces keys or orders itself.
    """
    chapter = mem.get('chapter') or 0
    castles = chart.castles(chapter)
    home = chart.home_castle(chapter)
    boss = chart.boss_castle(chapter)
    owned = _owned(mem) & set(castles)
    busy, _ = _en_route(mem)
    heading = _reserved_targets(mem)
    chart_orders = {}
    for order in chart.orders(chapter):
        chart_orders.setdefault(order['target'], order)
    lost = [c for c in mem.get('lost') or () if c in castles]
    lost.sort(key=lambda c: c != home)          # a lost home castle is retaken first
    targets = [c for c in (*lost, *chart_orders, *castles)
               if c in castles and c not in owned and c != boss]
    targets = list(dict.fromkeys(targets))
    # One recent expedition per target. Unobserved old arrivals stop reserving
    # after SORTIE_BUSY_TICKS; they must not empty every castle in the meantime.
    targets = [c for c in targets if c not in heading]
    sorties = {'retake': [], 'attack': []}
    for target in targets:
        picked = _interim_source(mem, target, chart_orders.get(target), owned, busy)
        if picked is None:
            continue
        source, general = picked
        purpose = 'retake' if target in lost else 'attack'
        note = (f"暫定: {general}で奪われた{target}を奪還" if purpose == 'retake'
                else f"暫定: {general}で{target}を攻撃")
        # Owner (2026-09-28): 切り札は持たせたほうが良い. The chart's cards for
        # this castle, else the retry opener (two イッテツーン). Cards out of
        # stock are dropped at the card list (card_drop), never waited for.
        base = chart_orders.get(target)
        cards = list(base['cards']) if base and base.get('cards') else list(INTERIM_CARDS)
        sorties[purpose].append({
            'general': general, 'target': target, 'cards': cards, 'source': source,
            'after': None, 'purpose': purpose, 'note': note})
    garrison = mem.get('garrison') or {}
    moves = []
    empty = [c for c in sorted(owned, key=lambda c: _distance(chapter, home, c))
             if garrison.get(c) == [] and c not in heading]
    for target in empty:
        # A castle whose list was never read may hold several generals: the
        # bot reads lists only when it sorties, so g407 knew one castle while
        # the stream showed crowded ones. Opening its list for the move is
        # the reading; with fewer than two there the move is cancelled
        # (deploy_step), which records the list and drops the donor.
        known = [c for c in owned if c != target
                 and len([g for g in garrison.get(c) or () if g not in busy]) >= 2]
        unread = [c for c in owned if c != target and garrison.get(c) is None]
        donors = (sorted(known, key=lambda c: _distance(chapter, c, target))
                  + sorted(unread, key=lambda c: _distance(chapter, c, target)))
        if not donors:
            continue
        spare = [g for g in garrison.get(donors[0]) or () if g not in busy]
        spare.sort(key=lambda g: g == NAME)
        general = spare[0] if spare else MOVE_ANY_GENERAL
        moves.append({
            'general': general, 'target': target, 'cards': [], 'source': donors[0],
            'after': None, 'purpose': 'move',
            'note': f"暫定: {general}を{donors[0]}から空の{target}へ移動"})
    out = {}
    for order in (*sorties['retake'], *moves, *sorties['attack']):
        out[f"{order['purpose']}_{len(out) + 1}"] = order
    return out


def _pick_interim_order(candidates: dict, answer: dict):
    """Return (label, order, confidence, fallback) — always a sortie order."""
    choice, confidence = answer.get('choice'), answer.get('confidence')
    order = candidates.get(choice)
    confident = type(confidence) in (int, float) and confidence >= INTERIM_MIN_CONFIDENCE
    if answer.get('status') == 'ok' and order is not None and confident:
        return choice, order, confidence, False
    # No hold: an unusable JEV answer still takes the first candidate.
    label = next(iter(candidates))
    return label, candidates[label], None, True


def _adopt_interim(mem, state, rid):
    answer = mem.get('_interim')
    if (not isinstance(answer, dict) or answer.get('request_id') != rid
            or answer.get('seq') != state.get('interim_count', 0)):
        return
    state['interim_count'] = state.get('interim_count', 0) + 1
    state['interim_wanted'] = False
    candidates = interim_candidates(mem)
    if not candidates:
        # No castle to retake, attack or staff with a known general: the only wait.
        _record(mem, 'chart_interim_hold', chart_step=None, strategy_variant='chart_adjust_pending',
                request_id=rid, choice=None, confidence=None, jev_status=answer.get('status') or 'no_candidates',
                deviation_reason='interim_no_candidates',
                reason='奪還・攻撃・移動できる候補が無いため暫定出撃を作らず調整チャートを待つ')
        return
    choice, order, confidence, fallback = _pick_interim_order(candidates, answer)
    step = f"{chart_adjust.INTERIM_PREFIX}{rid[:6]}:{state['interim_count']}"
    state['interim_order'] = {**order, 'step': step}
    if fallback:
        _record(mem, 'chart_interim_order', chart_step=step, strategy_variant='chart_interim_fallback',
                request_id=rid, choice=choice, confidence=confidence, general=order['general'],
                source=order['source'], target=order['target'], purpose=order.get('purpose'),
                jev_status=answer.get('status'),
                deviation_reason='interim_fallback',
                reason='JEV暫定判断が保留・低確信・候補外のため最初の候補で必ず出撃')
    else:
        _record(mem, 'chart_interim_order', chart_step=step, strategy_variant='chart_interim_jev',
                request_id=rid, choice=choice, confidence=confidence, general=order['general'],
                source=order['source'], target=order['target'], purpose=order.get('purpose'),
                reason='調整チャート待ちの間、JEVが決定的候補から暫定出撃を選択')


def _adjust_situation(mem):
    """Where the generals are, for the adjusted-chart request.

    g421 (e7df88f1): the LLM chart sent F2/F3/F5 with generals that were not
    in their source castles (it was never told who stood where) and each
    order failed at the castle menu. The request now carries the recorded
    garrisons, marching generals and lost castles so the worker can prompt
    with them and drop orders that cannot start.
    """
    now = int(mem.get('tick') or 0)
    # Same bound as _en_route: an unread arrival stops counting as marching.
    marching = sorted({(s['general'], s.get('target')) for s in (mem.get('sorties') or {}).values()
                       if s.get('status') in ('en_route', 'launched_unconfirmed') and s.get('general')
                       and s.get('tick') is not None and now - int(s['tick']) < SORTIE_BUSY_TICKS},
                      key=str)
    return {'garrison': {castle: sorted(names) for castle, names in sorted((mem.get('garrison') or {}).items())},
            'lost': sorted(mem.get('lost') or []), 'home_lost': bool(mem.get('home_lost')),
            'en_route': [{'general': g, 'target': t} for g, t in marching],
            'card_stock': dict(sorted((mem.get('card_stock') or {}).items()))}


def _adopt_plan(mem, doc, rid):
    """Adopt a validated adjusted chart as the plan, with per-generation step ids."""
    orders = [{**o, 'step': chart_adjust.execution_step(rid, o['step']), 'local_step': o['step'],
               'cards': list(o['cards']), 'after': list(o['after']) if o['after'] else None}
              for o in doc['orders']]
    purchases = doc.get('purchases')
    mem['chart_plan'] = {
        'request_id': rid, 'source': doc.get('source'), 'orders': orders,
        'recruitment': doc.get('recruitment'),
        'purchases': ({**purchases, 'month': list(purchases['month']),
                       'cards': [list(c) for c in purchases['cards']]} if purchases else None)}
    state = mem.setdefault('chart_adjust', {})
    state['interim_wanted'] = False
    state.pop('interim_order', None)
    mem['variant'] = 'chart_adjusted'
    # Digest for commentary: grounded order content, not the full plan blob.
    order_digest = [{'general': o['general'], 'source': o['source'],
                     'target': o['target'], 'cards': list(o['cards'])} for o in orders]
    _record(mem, 'chart_adjust_applied', chart_step=None, strategy_variant='chart_adjusted',
            request_id=rid, source=doc.get('source'), steps=[o['step'] for o in orders],
            local_steps=[o['local_step'] for o in orders], order_digest=order_digest,
            reason='調整チャートを受信したため独自の指示列で出撃を再開')


def _off_chart(mem):
    """No order is ready: request an adjusted chart, or adopt one that answered.

    The base chart is never mutated. A request is recorded once per distinct
    situation (chapter, captures, order states); an adjusted chart is adopted
    only when it answers exactly that request, as a complete order list that
    replaces the previous plan. Until then the previous plan keeps waiting, or,
    with no plan waiting, a bounded number of JEV-chosen interim orders may run.
    """
    # A deliberate recovery wait is not an exhausted chart. Do not ask the
    # planner to replace the boss order or send an interim sortie while the
    # next normal month advances. Other ready orders were considered first.
    waiting = next((o for o in _orders(mem) if _boss_egg_depleted(o, mem)
                    and not _hero_source_alternative(o, mem)
                    and _ready(o, {**mem, 'egg_uses': {}})
                    and (mem.get('orders') or {}).get(o['step']) in (None, 'pending')), None)
    if waiting:
        key = (waiting['step'], mem.get('month'), (mem.get('egg_uses') or {}).get(NAME))
        if mem.get('boss_egg_wait') != list(key):
            mem['boss_egg_wait'] = list(key)
            _record(mem, 'boss_egg_recovery_wait', chart_step=waiting['step'],
                    observed_metric={'general': NAME, 'egg_uses': key[2], 'month': key[1]},
                    expected_metric={'egg_uses': 4},
                    reason='第1話ボス出撃前に主人公の卵が消耗しているため、通常の月次回復を待つ')
        return
    mem.pop('boss_egg_wait', None)
    state = mem.setdefault('chart_adjust', {})
    rid = chart_adjust.request_id(mem)
    orders = [o for o in _orders(mem) if not o['step'].startswith(chart_adjust.INTERIM_PREFIX)]
    status = mem.get('orders') or {}
    blocked = [{'step': o['step'], 'after': list(o['after'] or ())} for o in orders
               if status.get(o['step']) in (None, 'pending')]
    reason = ('chart_unavailable' if not orders
              else 'orders_locked' if blocked else 'orders_exhausted')
    fields = {'request_id': rid, 'off_chart_reason': reason, 'blocked': blocked,
              'captured': sorted(mem.get('captured') or []), 'orders': dict(status),
              'gold': mem.get('gold'), 'month': mem.get('month'), **_adjust_situation(mem)}
    digest = chart_adjust.request_digest(fields)
    prior_digest = state.get('request_digest')
    if state.get('request_id') != rid:
        state.clear()
        state['request_id'] = rid
        state['request_digest'] = digest
        state['interim_wanted'] = not _plan_pending(mem) and bool(interim_candidates(mem))
        _record(mem, 'chart_adjust_request', chart_step=None, strategy_variant='chart_adjust_pending',
                **fields,
                reason='チャート外: 出撃可能な指示がないため調整チャートを非同期に要求し入力を保留')
        return
    revision_changed = prior_digest is not None and prior_digest != digest
    if prior_digest != digest:
        # Same situation, newer payload (stock, gold, lost castles): republish
        # so the worker answers the current revision, then keep going so the
        # interim fallback still progresses this observation.
        state['request_digest'] = digest
        _record(mem, 'chart_adjust_request', chart_step=None, strategy_variant='chart_adjust_pending',
                **fields,
                reason='チャート外: 状況が更新されたため同じ要求を最新の在庫・配置で再要求')
    doc = mem.get('_adjusted')
    plan = mem.get('chart_plan') or {}
    doc_digest = doc.get('request_digest') if doc else None
    if (doc and doc.get('request_id') == rid and doc.get('chapter') == mem.get('chapter')
            and plan.get('request_id') != rid
            and ((doc_digest == state.get('request_digest'))
                 # A digestless answer predates the revision field (old worker
                 # during a hot-load): accepted until a revision actually
                 # changed under it, then only a bound answer counts.
                 or (doc_digest is None and not revision_changed))):
        _adopt_plan(mem, doc, rid)
        return
    # An adopted answer may be locked or exhausted without changing its
    # request id (g514: retake Nakyume waited for Nakyume to be captured).
    # Adoption identity prevents replay above; only a live plan may block
    # the interim candidates from being evaluated again.
    if _plan_pending(mem):
        state['interim_wanted'] = False
        return
    _adopt_interim(mem, state, rid)
    interim = state.get('interim_order')
    status = mem.get('orders') or {}
    # An interim order whose source castle was lost can never run: it must
    # not block the next one.
    busy = (interim and status.get(interim['step']) in (None, 'pending')
            and _source(interim, mem) in _owned(mem))
    candidates = interim_candidates(mem)
    if busy or not candidates:
        if not candidates and state.get('interim_hold_digest') != digest:
            # A hold nobody can act on used to be silent (g530: sortie
            # stopped with zero evidence); record the starvation once per
            # request revision so diagnostics can see it.
            state['interim_hold_digest'] = digest
            _record(mem, 'chart_interim_hold', chart_step=None,
                    strategy_variant='chart_adjust_pending',
                    request_id=rid, choice=None, confidence=None,
                    jev_status='no_candidates',
                    deviation_reason='interim_no_candidates',
                    reason='奪還・攻撃・移動のいずれも作れないため暫定出撃を持てず調整チャートを待つ')
        state['interim_wanted'] = False
    elif state.get('interim_count', 0) < INTERIM_LIMIT:
        state['interim_wanted'] = True
    else:
        # JEV budget spent: still sortie with the first candidate (no hold).
        state['interim_wanted'] = False
        if not busy:
            state['interim_count'] = state.get('interim_count', 0) + 1
            label, order = next(iter(candidates.items()))
            step = f"{chart_adjust.INTERIM_PREFIX}{rid[:6]}:{state['interim_count']}"
            state['interim_order'] = {**order, 'step': step}
            _record(mem, 'chart_interim_order', chart_step=step,
                    strategy_variant='chart_interim_fallback',
                    request_id=rid, choice=label, confidence=None,
                    general=order['general'], source=order['source'], target=order['target'],
                    purpose=order.get('purpose'),
                    deviation_reason='interim_fallback',
                    reason='JEV暫定回数の上限に達したため最初の候補で必ず出撃')


def _finish_order(mem, state, **fields):
    step = mem.get('active')
    if step:
        mem.setdefault('orders', {})[step] = state
        if state == 'launched':
            # A new sortie carries a fresh kit: attempted uses from the
            # previous one must not shrink it (``kit_spent`` is per sortie).
            (mem.get('kit_spent') or {}).pop(step, None)
            (mem.get('sortie_confirm_miss') or {}).pop(step, None)
        _record(mem, 'order_' + state, chart_step=step, **fields)
    mem['active'] = None
    mem['picked'] = []


def observe_sortie_transition(screen, mem, previous_kind=None):
    """An interrupted target cursor is a possible departure, never a retry.

    A hotloaded old policy has no attempt marker; its verified confirmation
    context and previous map_target observation provide the same evidence.
    """
    # A recall destination picker belongs to the recall, not to an active
    # chart sortie's interrupted target selection.
    if (mem.get('recall') or {}).get('stage') in ('dest', 'await_dispatch'):
        return
    order = _order(mem)
    attempt = mem.get('sortie_attempt')
    if not attempt and order and previous_kind == 'map_target':
        context = (mem.get('order_context') or {}).get(order['step']) or {}
        if context.get('actual_general'):
            attempt = {'step': order['step'], 'target_seen': True}
            mem['sortie_attempt'] = attempt
    if not attempt:
        return
    if (mem.get('y_jump') or {}).get('mode') == 'target' or mem.get('y_jump_return') == 'target':
        # Our own Y jump for the target (the view and the fade back), not an
        # interruption.
        if screen.kind in ('map_target', 'map'):
            mem.pop('y_jump_return', None)      # back on a map: normal tracking resumes
        else:
            return
    if screen.kind == 'map_target':
        if not order or order['step'] == attempt['step']:
            step = attempt['step']
            if (mem.get('orders') or {}).get(step) == 'launched_unconfirmed':
                mem['active'] = step
                mem['orders'][step] = 'pending'
                (mem.get('sorties') or {}).pop(step, None)
                # The interruption moved the camera: the resumed selection gets
                # fresh target jumps (g421 18:24: six were spent before a defense
                # battle, the marker came back unplaced and the bot idled).
                (mem.get('y_jumps') or {}).pop(f'{step}:target', None)
                (mem.get('y_jumped') or {}).pop(step, None)
                _record(mem, 'sortie_target_resumed', chart_step=step,
                        reason='割り込み後に同じ出撃先マーカーが戻ったため目標選択を再開')
        attempt['target_seen'] = True
        attempt.pop('interrupted', None)
        return
    if (attempt.get('interrupted') or not attempt.get('target_seen')
            or not order or order['step'] != attempt['step']):
        return
    step = order['step']
    context = (mem.get('order_context') or {}).get(step) or {}
    general = context.get('actual_general')
    if not general:
        return
    mem.setdefault('launched_orders', {})[step] = dict(order)
    mem.setdefault('sorties', {})[step] = {
        'general': general, 'source': _source(order, mem), 'target': None,
        'planned_target': order['target'], 'status': 'launched_unconfirmed',
        'evidence': context, 'tick': int(mem.get('tick') or 0)}
    _finish_order(mem, 'launched_unconfirmed', general=general, target=None,
                  planned_target=order['target'], screen=screen.kind,
                  reason='目標決定前に出撃先選択を離れたため出撃の成否と行先を未確定として記録')
    attempt['interrupted'] = True
    mem.pop('expect_menu', None)


def _general_visible(screen, general):
    """Exact name with no adjacent unknown glyph; ignore hand/border pixels."""
    for line in screen.lines:
        for x, word in line.spans():
            if x <= 100 or word != general:
                continue
            neighbors = (x - 8, x + 8 * len(word))
            if not any(ch == UNKNOWN and cx in neighbors
                       and not (screen.hand and screen.hand[0] <= cx <= screen.hand[2])
                       for cx, ch in line.cells):
                return True
    return False


def _verify_sortie_source(screen, mem, order):
    """Never select a replacement while checking an interrupted departure.

    Presence proves cancellation. Only the explicit empty-list message proves
    absence here: a nonempty panel can be partial or scrolled, so missing a
    name on it is not evidence of departure.
    """
    step = order['step']
    sortie = mem['sorties'][step]
    present = _present_generals(screen)
    if present and sortie['general'] in present and not _general_visible(screen, sortie['general']):
        present = None
    if present is None or (present and sortie['general'] not in present):
        sortie['verification_reads'] = sortie.get('verification_reads', 0) + 1
        if sortie['verification_reads'] >= 3:
            sortie['verification_unavailable'] = True
            mem['active'] = None
            _record(mem, 'sortie_verification_held', chart_step=step,
                    reason='一覧で在城・不在を確定できないため出撃未確定を維持して別の指示へ進む')
            return [pad('b'), pad('b')]
        return _hold_deploy(screen, mem, order, '出撃未確定の将軍の在城・不在を一覧で確定できないため保留')
    cancelled = sortie['general'] in present
    sortie['status'] = 'cancelled' if cancelled else 'en_route'
    mem.setdefault('orders', {})[step] = 'pending' if cancelled else 'launched'
    if not cancelled:
        _garrison_move(mem, sortie['general'], source=sortie['source'])
    _record(mem, 'sortie_cancelled_observed' if cancelled else 'sortie_departed_observed',
            chart_step=step, general=sortie['general'], source=sortie['source'], target=None,
            observed_metric=present,
            reason='将軍一覧で本人の在城を確認したため再試行可能' if cancelled
            else '出撃元が無人と確認できたため出撃を確定、行先は未確認')
    mem.pop('sortie_attempt', None)
    mem['active'] = None
    mem['picked'] = []
    return [pad('b'), pad('b')]


# Y shows the whole island at 1/8 scale; each castle's flag sits at the
# castle cell / 8 + offset (chapter 1 measured in an isolated emulator on
# 2026-09-28, all seven flags within 1 px). Blue fill = enemy, red = ours.
WORLD_MAP_OFFSET = {1: (64.5, 44.4), 2: (64.2, 45.0)}   # chapter 2: 3 flags vs measured cells
WORLD_FLAG_ENEMY = (0, 64, 189)
WORLD_FLAG_OWN = (230, 56, 90)
WORLD_MARKER = (255, 24, 0)     # ▲ army marker on the Y view (probe-measured)
WORLD_SURVEY_TICKS = 200       # observations between surveys (~5 min at 1.5 s)
WORLD_MAP_WAIT = 4             # fade frames before giving up a reading
SELECT_FOCUS_INTERVAL = 300    # observations between hero re-focuses (~7.5 min)


WORLD_COLOUR_TOL = 8           # live capture shifts colours by ~1 (g419: gold 255,181,0)


def _near_colour(pixel, colour, tol=WORLD_COLOUR_TOL):
    return all(abs(a - b) <= tol for a, b in zip(pixel, colour))


def world_flags(frame, chapter, cursor=None):
    """{castle: 'own'|'enemy'} read from the whole-island view, unread ones left out.

    A flag under the Y cursor is not read: the sortie cursor's G is the same
    blue as an enemy flag (isolated probe: the home flag read as enemy).
    """
    offset = WORLD_MAP_OFFSET.get(chapter)
    if not offset or frame is None:
        return {}
    out = {}
    for name, (wx, wy) in chart.castles(chapter).items():
        mx, my = round(wx / 8 + offset[0]), round(wy / 8 + offset[1])
        if cursor and abs(mx + 2 - cursor[0]) <= 10 and abs(my + 1 - cursor[1]) <= 10:
            continue
        box = {frame.pixel(x, y) for x in range(mx - 2, mx + 6) for y in range(my - 2, my + 5)
               if 0 <= x < frame.width and 0 <= y < frame.height}
        enemy = any(_near_colour(px, WORLD_FLAG_ENEMY) for px in box)
        own = any(_near_colour(px, WORLD_FLAG_OWN) for px in box)
        if enemy != own:
            out[name] = 'enemy' if enemy else 'own'
    return out


def world_markers(frame) -> list:
    """▲ army markers on the whole-island view, as (x0, y0, x1, y1) boxes.

    Owner rule (2026-09-28): the Y view also shows ▲ marks for units, and
    they must be checked on every survey. Measured in the isolated probe:
    an own marker is a ~7x6 bright red-orange triangle (255,24,0); castle
    flags (230,56,90)/(0,64,189) sit far outside that colour's tolerance.
    """
    if frame is None:
        return []
    pts = [(x, y) for y in range(frame.height) for x in range(frame.width)
           if _near_colour(frame.pixel(x, y), WORLD_MARKER)]
    seen, out = set(), []
    for p in sorted(pts):
        if p in seen:
            continue
        stack, comp = [p], []
        seen.add(p)
        while stack:
            x, y = stack.pop()
            comp.append((x, y))
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    n = (x + dx, y + dy)
                    if (n not in seen and 0 <= n[0] < frame.width and 0 <= n[1] < frame.height
                            and _near_colour(frame.pixel(*n), WORLD_MARKER)):
                        seen.add(n)
                        stack.append(n)
        if not 6 <= len(comp) <= 60:
            continue
        xs = [c[0] for c in comp]
        ys = [c[1] for c in comp]
        if max(xs) - min(xs) > 12 or max(ys) - min(ys) > 12:
            continue
        out.append((min(xs), min(ys), max(xs), max(ys)))
    return out


def _world_map_wanted(mem) -> bool:
    if (mem.get('chapter') or 0) not in WORLD_MAP_OFFSET:
        return False
    last = mem.get('world_map_tick')
    tick = int(mem.get('tick') or 0)
    return (last is None or bool(mem.get('world_map_due'))
            or tick - int(last) >= WORLD_SURVEY_TICKS)


def world_map_step(screen, mem, frame):
    """Read every castle's owner from the Y view, then close it with Y
    (or, while a Y jump is running, steer its cursor to the goal castle)."""
    if mem.get('y_jump'):
        return _y_jump_step(mem, frame)
    recall = mem.get('recall')
    if recall and recall.get('stage') == 'await_dispatch':
        return _recall_dispatch_step(screen, mem, recall)
    if recall and recall.get('stage') == 'dest':
        # きかん opens a whole-island picker (chapter 1 and 2, isolated probe
        # 2026-09-29): the R ring starts on the home castle, A selects it and a
        # second A confirms; the general then walks back home (measured: the
        # hero turned from キカンドン towards アルマムーン). Y does not close it.
        chapter = mem.get('chapter') or 0
        cursor = world_cursor(frame) if frame is not None else None
        flags = world_flags(frame, chapter)
        if flags:
            _apply_world_flags(mem, flags)
        offset = WORLD_MAP_OFFSET.get(chapter)
        nearby = [name for name, (x, y) in chart.castles(chapter).items()
                  if cursor and offset and abs(x / 8 + offset[0] - cursor[0]) <= 6
                  and abs(y / 8 + offset[1] - cursor[1]) <= 6]
        recall['picker_observations'] = int(recall.get('picker_observations', 0)) + 1
        if len(nearby) != 1 or flags.get(nearby[0]) != 'own':
            if recall['picker_observations'] >= RECALL_LIMIT:
                _record(mem, 'camp_recall_aborted',
                        observed_metric={'cursor': cursor, 'flags': flags},
                        reason='帰還先の自軍旗を確認できないため敵城へ確定せず帰還操作を取り消す')
                mem.pop('recall', None)
                mem['uncertain'] = True
                return [pad('b')]
            return [pad('right')] if cursor else []
        home = nearby[0]  # the measured R picker, never an assumed home castle
        return _request_recall(mem, recall, home,
                               [pad('a'), {'type': 'wait', 'ms': 700}, pad('a')])
    flags = world_flags(frame, mem.get('chapter') or 0)
    if not flags:
        waited = int(mem.get('world_map_wait') or 0) + 1
        mem['world_map_wait'] = waited
        return [] if waited < WORLD_MAP_WAIT else [pad('y')]
    mem.pop('world_map_wait', None)
    _apply_world_flags(mem, flags)
    markers = world_markers(frame)
    if markers:
        _record(mem, 'world_map_units', observed_metric={'count': len(markers),
                'boxes': [list(m) for m in markers]},
                reason='全体マップの▲（部隊マーカー）を定期確認')
    return [pad('y')]


def _apply_world_flags(mem, flags):
    chapter = mem.get('chapter') or 0
    from .hanjuku_roster import owner
    for name, kind in flags.items():
        if name in chart.castles(chapter):
            owner(mem, STATUS_NAMES.get(name, name), kind)
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
    if home_owner == 'enemy' and not mem.get('home_lost'):
        # g407 03:53: the home castle flew an enemy flag while every sortie
        # still treated it as ours and fell back to it.
        mem['home_lost'] = True
        lost = mem.setdefault('lost', [])
        if home not in lost:
            lost.insert(0, home)
        (mem.get('garrison') or {}).pop(home, None)
        changed.append(('lost', home))
    elif home_owner == 'own' and mem.get('home_lost'):
        mem['home_lost'] = False
        mem['lost'] = [c for c in mem.get('lost') or [] if c != home]
        changed.append(('captured', home))
    for castle, owner in flags.items():
        if castle in (home, chart.boss_castle(chapter)):
            continue
        if owner == 'enemy' and castle in captured:
            mem['captured'] = captured = [c for c in captured if c != castle]
            lost = mem.setdefault('lost', [])
            if castle not in lost:
                lost.append(castle)
            (mem.get('garrison') or {}).pop(castle, None)
            changed.append(('lost', castle))
        elif owner == 'own' and castle not in captured:
            captured.append(castle)
            mem['lost'] = [c for c in mem.get('lost') or [] if c != castle]
            changed.append(('captured', castle))
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


# Y jump (owner hint 2026-09-28: "select the castle on the Y map instead of
# moving the cursor"). Measured in the isolated emulator: the Y view shows a
# cursor - a white dashed ring on the map, gold-cornered G while choosing a
# sortie target - centred at cell/8 + offset; the D-pad moves it 0.5 px per
# frame and A returns with the map cursor (or target marker) on that cell.
# Jumps to キカンドン, ジョンリギ, スペンソニア and アルマムーン landed 4-9 px from
# each roof, and the target marker 5 px from キカンドン's roof. Roof-based
# cursor motion was the source of most mis-sorties (lone-roof mix-ups,
# edge-scroll drift of ~60 px), so far goals go through Y instead.
Y_JUMP_OFFSET = {1: (63.0, 47.5), 2: (63.0, 47.5)}
Y_JUMP_FAR = 48                # world px from the goal before a jump is worth it
Y_JUMP_LIMIT = 6               # jumps per order and screen mode (reset when the order starts).
                               # 2 ran out mid-order and left roof walking (g421 15:25: SELECT loops)
Y_JUMP_MOVES = 8               # D-pad steps inside one jump before confirming anyway
Y_JUMP_WAIT = 6                # frames without a readable cursor before closing Y
Y_JUMP_FINAL_TOL = 3.0         # view px: beyond this at the move limit the jump is abandoned
Y_JUMP_OPEN_GRACE = 6          # observations the old screen may still show after Y
Y_JUMP_TOL = 1.0               # view px (8 world px); roofs close the rest. 0.5 oscillated
                               # around a flag in g419 (ring and flag overlap: +-1 px jitter)
_Y_RING = [(dx, dy) for dx in range(-13, 14) for dy in range(-13, 14)
           if 100 <= dx * dx + dy * dy <= 169]          # radius 5-6.5 px, half-pixel units


def world_cursor(frame):
    """Centre of the Y view's cursor: gold G corners, else the white dashed ring."""
    gold = [(x, y) for x in range(40, 220) for y in range(40, 200)
            if _near_colour(frame.pixel(x, y), (255, 182, 0))]
    # Only the compact G corners (~15 px box): the map's gold edge arrows are
    # far apart and their joint box read as a cursor (g419 10:49), and chapter
    # 2's gold camp triangle beside フーリック widened the box so no target
    # jump there could read the G (isolated probe, g421 state). Take the
    # G-sized window holding the most gold instead of the whole box.
    best = None
    for x0 in sorted({x for x, _ in gold}):
        for y0 in sorted({y for _, y in gold}):
            inside = [(x, y) for x, y in gold if x0 <= x <= x0 + 18 and y0 <= y <= y0 + 18]
            if best is None or len(inside) > len(best):
                best = inside
    if best and len(best) >= 12:
        xs = [x for x, _ in best]; ys = [y for _, y in best]
        if max(xs) - min(xs) >= 10 and max(ys) - min(ys) >= 10:
            return ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2)
    votes = {}
    for x in range(40, 220):
        for y in range(40, 200):
            if _near_colour(frame.pixel(x, y), (255, 255, 255)):
                for dx, dy in _Y_RING:
                    key = (2 * x + dx, 2 * y + dy)
                    votes[key] = votes.get(key, 0) + 1
    # The ring only appears inside the island view (sea 64-191 x 47-174);
    # the ornate frame's highlights just outside voted as strongly (g419 10:49,
    # a frame where the blinking ring was off).
    votes = {k: v for k, v in votes.items() if 140 <= k[0] <= 372 and 106 <= k[1] <= 338}
    if not votes:
        return None
    key, count = max(votes.items(), key=lambda kv: kv[1])
    return (key[0] / 2, key[1] / 2) if count >= 18 else None


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
        _record(mem, 'y_jump_failed', chart_step=jump.get('step'), target=jump.get('goal'),
                screen=screen.kind, observed_metric={'moves': jump.get('moves')},
                reason='全体マップが決定前に閉じたためジャンプを中断として扱う')
    return False


def _want_y_jump(mem, order, name, mode) -> bool:
    chapter = mem.get('chapter') or 0
    if chapter not in Y_JUMP_OFFSET or mem.get('y_jump'):
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
        jump['wait'] += 1
        if jump['wait'] < Y_JUMP_WAIT:
            return []
        mem.pop('y_jump', None)
        _record(mem, 'y_jump_failed', chart_step=jump['step'], target=jump['goal'],
                reason='全体マップのカーソルを読めないため閉じて通常の移動に戻る')
        return [pad('y')]
    gx, gy = chart.castles(chapter)[jump['goal']]
    ox, oy = Y_JUMP_OFFSET[chapter]
    dx, dy = gx / 8 + ox - cursor[0], gy / 8 + oy - cursor[1]
    if jump['moves'] >= Y_JUMP_MOVES and not (abs(dx) <= Y_JUMP_FINAL_TOL and abs(dy) <= Y_JUMP_FINAL_TOL):
        # Never confirm far from the goal (g419 10:49: 50 px off after a misread).
        mem.pop('y_jump', None)
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


SOURCE_MISS_LIMIT = 3         # failed castle menus per order before giving the source up


def _source(order, mem):
    return mem.get('source_override', {}).get(order['step'], order['source'])


def _owned(mem):
    """Captured castles plus the home castle, unless the Y map showed it taken (g407 03:53)."""
    home = set() if mem.get('home_lost') else {chart.home_castle(mem.get('chapter') or 0)}
    return set(mem.get('captured') or []) | home


def _give_up_source(mem, order):
    """Bound A on a castle that never opens its menu (g401: 900 presses).

    The chart's own source falls back to the home castle once, as for an
    empty general list; after that the order fails with evidence so the next
    order (or an off-chart sortie) runs instead of a permanent loop.
    """
    step = order['step']
    source = _source(order, mem)
    if (mem.get('orders') or {}).get(step) == 'launched_unconfirmed':
        mem['sorties'][step]['verification_unavailable'] = True
        mem['active'] = None
        _record(mem, 'sortie_verification_held', chart_step=step,
                reason='出撃元の一覧を開けないため未確定の出撃を維持し再指示を抑止')
        return
    home = chart.home_castle(mem.get('chapter') or 0)
    # Navigation state is left to the nav_reset of this same miss: dropping
    # the cell would freeze the camera on open sea again (g358).
    misses = mem.get('source_miss', {}).pop(step, 0)
    garrison = mem.get('garrison') or {}
    if source != home and home in _owned(mem) and not mem.get('source_override', {}).get(step):
        mem.setdefault('source_override', {})[step] = home
        garrison.pop(source, None)
        _record(mem, 'order_source_changed', chart_step=step, strategy_variant='source_fallback',
                deviation_reason=f'{source}で決定しても城メニューが{misses}回開かない',
                observed_metric={'source': source, 'menu_miss': misses},
                reason='本城から出撃し直す')
        return
    _finish_order(mem, 'failed', deviation_reason=f'{source}で決定しても城メニューが{misses}回開かない',
                  observed_metric={'source': source, 'menu_miss': misses},
                  reason='出撃元の城を選べないため指示を諦めて次の指示へ進む')


UNDER_CURSOR_PX = 8            # roof target vs cursor cell when standing on a castle
OFF_CASTLE_LIMIT = 3           # refusals before pressing A anyway (bounded)


def _roof_under_cursor(screen, frame) -> bool:
    """A castle roof whose selecting cell is the cursor's cell, read directly.

    No exclusion box: the cursor brackets overlap the roof they select.
    """
    s = _cursor(screen)
    if not s or frame is None:
        return True                  # nothing to check against: keep old behaviour
    return any(abs(r['target'][0] - s[0]) <= UNDER_CURSOR_PX
               and abs(r['target'][1] - s[1]) <= UNDER_CURSOR_PX
               for r in castle_roofs(frame))


def _hold_off_castle(screen, mem, order) -> bool:
    """Refuse A when the estimate says "on the source" but no roof is there.

    A lone roof is ambiguous: g401 21:31 and g405 00:26 voted アルマムーン's roof
    as another castle, so the cell said "on アルマムーン" while the cursor was on
    open sea; three A presses failed the chart's 1-C1 for good. Distrust the
    cell and search inland (anchoring on 2+ roofs). Bounded per order.
    """
    held = mem.setdefault('off_castle', {})
    count = held.get(order['step'], 0)
    if count >= OFF_CASTLE_LIMIT:
        return False
    held[order['step']] = count + 1
    mem['uncertain'] = True
    if ((mem.get('chapter') or 0) in Y_JUMP_OFFSET
            and (mem.get('y_jumps') or {}).get(f"{order['step']}:map", 0) < Y_JUMP_LIMIT):
        # A Y jump re-places the cursor directly; the search wandered for
        # minutes (g421). Dropping the cell makes the next map frame jump.
        mem.pop('cursor', None)
        mem.pop('anchor', None)
        _record(mem, 'source_not_under_cursor', chart_step=order['step'], screen=screen.kind,
                observed_metric={'screen_cursor': list(_cursor(screen)), 'held': count + 1},
                reason='出撃元に着いたと推定したがカーソル位置に城の屋根が無いため全体マップで選び直す')
        return True
    mem['nav_search'] = True
    mem.pop('nav_search_leg', None)
    mem.pop('anchor', None)
    _record(mem, 'source_not_under_cursor', chart_step=order['step'], screen=screen.kind,
            observed_metric={'cursor': mem.get('cursor'), 'screen_cursor': list(_cursor(screen)),
                             'roofs': mem.get('roofs_seen'), 'held': count + 1},
            reason='出撃元に着いたと推定したがカーソル位置に城の屋根が無いため、決定せず内陸で再特定する')
    return True


SELECT_AFTER = 2               # unanchored map frames before SELECT re-places the cursor
SELECT_EVERY = 40              # observations between hero-castle SELECTs


def _hero_castle(mem):
    """The owned castle the hero is last known to stand in, or None.

    Known from a read general list, or the castle the hero last took. A
    recent sortie of the hero means he is out in the field.
    """
    if NAME in _en_route(mem)[0]:
        return None
    owned = _owned(mem)
    castles = chart.castles(mem.get('chapter') or 0)
    for castle, present in (mem.get('garrison') or {}).items():
        if castle in owned and castle in castles and NAME in (present or ()):
            return castle
    return None


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


def _near_goal(mem, order, name, mode):
    near = mem.get('near_goal') or {}
    return near.get('step') == order['step'] and near.get('goal') == name and near.get('mode') == mode


MENU_HOLD_LIMIT = 10           # arrived-but-unanchored holds after a failed castle menu

RECALL_LIMIT = 90              # observations for one camp recall
RECALL_ARRIVE_PX = 4           # cursor cell onto the tent
RECALL_CONFIRM_PX = 4          # marker onto the own castle's selecting cell
RECALL_SKIP_TICKS = 400        # observations without recalls after a skipped picker


def is_camp_menu(screen) -> bool:
    """Our tent's menu: いどう/ステータス/キャンプ/きかん (isolated probe, g436 22:04)."""
    return all(word in screen.text for word in ('いどう', 'ステータス', 'キャンプ', 'きかん'))


def camp_recall_step(screen: Screen, mem, frame):
    """Owner rule (2026-09-28): a camp (野営) seen on screen is recalled.

    Measured in the isolated probe: A on our tent opens
    いどう/ステータス/キャンプ/きかん; its observed cursor must reach きかん before A
    opens the destination marker (kind map_target), and A over an own castle's
    roof cell sends the general home. The destination is the nearest visible
    own castle (補給できる城). ``None`` means "no recall in flight": the
    caller keeps its ordinary map steering.
    """
    state = mem.get('recall')
    if state is None:
        if screen.kind != 'map' or frame is None:
            return None
        camps = own_camps(frame)
        skip = mem.get('recall_skip')
        if skip and int(mem.get('tick') or 0) - int(skip.get('tick') or 0) < RECALL_SKIP_TICKS:
            # A skipped camp would reopen the same picker on every map frame.
            # The cursor moves with the camera, so skip every camp meanwhile.
            camps = []
        if not camps:
            return None
        cursor = _cursor(screen)
        camp = min(camps, key=lambda c: (abs(c['target'][0] - cursor[0])
                                         + abs(c['target'][1] - cursor[1])) if cursor else 0)
        state = mem['recall'] = {'stage': 'to_camp', 'target': list(camp['target']), 'steps': 0}
        _record(mem, 'camp_found', observed_metric={'camp': list(camp['target'])},
                reason='画面に自軍の野営を見つけたため補給できる自軍城への帰還を指示する')
    if state.get('stage') == 'await_dispatch':
        return _recall_dispatch_step(screen, mem, state)
    state['steps'] = int(state.get('steps') or 0) + 1
    if state['steps'] > RECALL_LIMIT:
        _record(mem, 'camp_recall_aborted',
                observed_metric={'stage': state.get('stage'), 'steps': state['steps']},
                reason='野営の帰還指示が上限内に完了しないため断念し位置を再測定する')
        mem.pop('recall', None)
        mem['uncertain'] = True
        mem['recall_skip'] = {'tick': int(mem.get('tick') or 0)}
        return [pad('b')] if is_camp_menu(screen) else []
    stage = state.get('stage')
    if stage == 'hero_focus':
        if screen.kind != 'map':
            return []                  # battle results and messages fade out first
        state['stage'], state['steps'] = 'hero_open', 0
        return [pad('select')]         # measured: SELECT centres the cursor on the hero
    if stage == 'hero_open':
        if screen.kind != 'map':
            return []
        state['stage'], state['steps'] = 'menu', 0
        _record(mem, 'camp_enter', observed_metric={'hero': True, 'cursor': list(_cursor(screen) or ())},
                reason='主人公にカーソルを合わせて決定し、きかんを選ぶ')
        return [pad('a')]
    if stage == 'to_camp':
        if screen.kind != 'map':
            mem.pop('recall', None)
            mem['uncertain'] = True
            return []
        # Re-detect every step: a tent clipped by the top edge is targeted
        # above the screen, and the cursor servo there scrolls the camera
        # until the flag shows and the real selecting cell is known.
        camps = own_camps(frame) if frame is not None else []
        if camps:
            cursor = _cursor(screen)
            camp = min(camps, key=lambda c: (abs(c['target'][0] - cursor[0])
                                             + abs(c['target'][1] - cursor[1])) if cursor else 0)
            state['target'] = list(camp['target'])
        cursor = _cursor(screen)
        if not cursor:
            return []
        dx = state['target'][0] - cursor[0]
        dy = state['target'][1] - cursor[1]
        if abs(dx) <= RECALL_ARRIVE_PX and abs(dy) <= RECALL_ARRIVE_PX:
            state['stage'] = 'menu'
            state['steps'] = 0        # each stage gets the full observation budget
            _record(mem, 'camp_enter', observed_metric={'camp': list(state['target'])},
                    reason='野営にカーソルを合わせて決定し、きかんを選ぶ')
            return [pad('a')]
        if abs(dx) >= abs(dy):
            return [pad('right' if dx > 0 else 'left', min(8, abs(dx)))]
        return [pad('down' if dy > 0 else 'up', min(8, abs(dy)))]
    if stage == 'menu':
        if state.get('hero') and (screen.kind in ('castle_menu', 'general_list')
                                  or (screen.kind == 'map' and state['steps'] > 4)):
            # SELECT put the cursor on a castle (the hero is inside one) or on
            # nothing: he is not marching, so there is nothing to call off.
            mem.pop('recall', None)
            _record(mem, 'hero_recall_skipped', observed_metric={'screen': screen.kind},
                    reason='主人公の部隊メニューが開かない（城内など）ため帰還指示を取り消す')
            return [pad('b')] if screen.kind != 'map' else []
        if not is_camp_menu(screen):
            return []              # the window is still fading in
        move = menu_to(screen, 'きかん')
        if move is None:
            misses = state['cursor_misses'] = int(state.get('cursor_misses', 0)) + 1
            if misses == 1:
                _record(mem, 'camp_menu_unread', chart_step=None, resulting_event='cursor_unconfirmed',
                        reason='野営メニューの実カーソルを読めないため帰還の確定を保留')
            if misses >= 3:
                mem.pop('recall', None)
                mem['uncertain'] = True
                mem['recall_skip'] = {'tick': int(mem.get('tick') or 0)}
                _record(mem, 'camp_recall_aborted', resulting_event='cursor_unconfirmed',
                        reason='実カーソルを3回で確認できず野営メニューを閉じる')
                return [pad('b')]
            return []
        state.pop('cursor_misses', None)
        _record(mem, 'camp_recall_cursor', chart_step=None, observed_metric={'current': _current(screen),
                'choice': 'きかん', 'move': move}, reason='野営の実手カーソルを帰還項目へ合わせる')
        if move != 'here':
            return [move]
        state['stage'] = 'dest'
        state['steps'] = 0            # each stage gets the full observation budget
        return [pad('a')]
    if stage == 'dest':
        if screen.kind != 'map_target':
            if screen.kind == 'map':
                mem.pop('recall', None)
                mem['uncertain'] = True
            return []
        marker = screen.marker
        roofs = ([r for r in castle_roofs(frame) if r['kind'] == 'own' and not r['clipped']]
                 if frame is not None else [])
        if state.get('hero') and marker:
            goal = state.get('goal') or _recall_goal(mem)
            state['goal'] = goal
            jumped = (mem.get('y_jumped') or {}).get('RECALL') == goal
            if goal and jumped and not castle_roofs(frame):
                return _finish_hero_recall(mem, state, goal)      # roofs hidden: trust the jump
            if goal and not roofs and not jumped and (mem.get('y_jumps') or {}).get('RECALL:target', 0) < 2:
                return _start_y_jump(mem, {'step': 'RECALL'}, goal, 'target')
        if not marker or not roofs:
            mem.pop('recall', None)
            mem['uncertain'] = True
            _record(mem, 'camp_recall_skipped',
                    observed_metric={'marker': list(marker) if marker else None, 'roofs': len(roofs)},
                    reason='可視範囲に自軍城の屋根がなく帰還先を選べないため取り消す')
            return [pad('b')]
        roof = min(roofs, key=lambda r: abs(r['target'][0] - marker[0]) + abs(r['target'][1] - marker[1]))
        dx = roof['target'][0] - marker[0]
        dy = roof['target'][1] - marker[1]
        if abs(dx) <= RECALL_CONFIRM_PX and abs(dy) <= RECALL_CONFIRM_PX:
            if state.get('hero'):
                return _finish_hero_recall(mem, state, state.get('goal'))
            return _request_recall(mem, state, None, [pad('a')],
                                   target_cell=list(roof['target']))
        if abs(dx) >= abs(dy):
            return [pad('right' if dx > 0 else 'left', min(6, abs(dx)))]
        return [pad('down' if dy > 0 else 'up', min(6, abs(dy)))]
    return []


def _recall_goal(mem):
    """The owned castle the hero returns to: where he set out from, else the nearest to home."""
    chapter = mem.get('chapter') or 0
    cells = chart.castles(chapter)
    owned = [c for c in _owned(mem) if c in cells]
    for step in (mem.get('recall') or {}).get('sorties') or ():
        source = ((mem.get('sorties') or {}).get(step) or {}).get('source')
        if source in owned:
            return source
    home = chart.home_castle(chapter)
    if home in owned:
        return home
    return owned[0] if owned else None


def _finish_hero_recall(mem, state, goal):
    return _request_recall(mem, state, goal, [pad('a')])


def _request_recall(mem, state, goal, actions, **fields):
    """A proposal remains pending; neither an input nor an arrival receipt."""
    state.update(stage='await_dispatch', steps=0, goal=goal,
                 a_inputs=sum(a.get('buttons') == ['a'] for a in actions),
                 requested_tick=int(mem.get('tick') or 0), **fields)
    state.pop('request_trace', None)
    state.pop('input_sent', None)
    state.pop('picker_closed', None)
    _record(mem, 'camp_recall_requested', chart_step=None,
            observed_metric={'castle': goal, 'camp': state.get('target'),
                             'hero_intended': bool(state.get('hero')), **fields},
            resulting_event='planned_not_yet_sent',
            reason='実選択した自軍城への帰還入力を予定し、送信と到着は未確認として保持')
    return actions


def _recall_dispatch_step(screen, mem, state):
    state['steps'] = int(state.get('steps') or 0) + 1
    receipt = mem.get('_recall_inputs') or {}
    trace = state.get('request_trace') or {}
    if (trace and receipt.get('request_trace') == trace
            and receipt.get('a_inputs', 0) >= state.get('a_inputs', 1)):
        if not state.get('input_sent'):
            state['input_sent'] = True
            _record(mem, 'camp_recall_input_sent', observed_metric={'castle': state.get('goal'),
                    'request_trace': trace}, resulting_event='sent_not_yet_accepted',
                    reason='同じラン・判断ID・実画面の帰還A送信記録を確認、受理と到着は未確認')
    if state.get('input_sent') and screen.kind == 'map':
        state['picker_closed'] = True
    if state.get('picker_closed') or state['steps'] >= 6:
        status = 'arrival_unconfirmed' if state.get('input_sent') else 'dispatch_unconfirmed'
        mem['recall_verification'] = {**state, 'status': status}
        mem.pop('recall', None)
        mem['recall_skip'] = {'tick': int(mem.get('tick') or 0)}
        mem['uncertain'] = True
        _record(mem, 'camp_recall_unconfirmed', chart_step=None, observed_metric={'castle': state.get('goal'),
                'input_sent': bool(state.get('input_sent')), 'picker_closed': bool(state.get('picker_closed')),
                'general': None}, resulting_event=status,
                reason='帰還の本人・移動・到着を確認できず未確認として保持し、有限な操作を終了')
        return [pad('b')] if screen.kind in ('world_map', 'map_target') else []
    return []


def map_step(screen: Screen, mem, frame):
    if _drop_stale_y_jump(screen, mem):
        return []
    if mem.pop('expect_menu', False):
        mem['menu_miss'] = int(mem.get('menu_miss', 0)) + 1
        missed = _order(mem)
        if missed is not None:
            misses = mem.setdefault('source_miss', {})
            misses[missed['step']] = misses.get(missed['step'], 0) + 1
        mem['uncertain'] = True
        _record(mem, 'localize', reason='城で決定したがメニューが出ないため位置を再測定',
                observed_metric=mem.get('cursor'),
                expected_metric={'menu_miss': mem['menu_miss']})
        # Integrated motion put the cursor on a non-castle cell (g340: A on
        # open water forever). Distrust the estimate; only a fresh roof
        # anchor may re-enable confirming a cell. Keep the cell itself as a
        # dead-reckoning origin: without it navigation returned None and the
        # camera froze on open water with no roof to re-anchor (g358).
        for key in ('anchor', 'nav_last'):
            mem.pop(key, None)
        mem['nav_search'] = True
        mem.pop('nav_search_leg', None)
        _record(mem, 'nav_reset',
                reason='城で決定してもメニューが出ないため位置推定を信用せず、城の多い内陸へ動かして屋根アンカーで再特定する',
                observed_metric={'menu_miss': mem['menu_miss'], 'cursor': mem.get('cursor'),
                                 'screen_cursor': list(_cursor(screen) or ()),
                                 'roofs': mem.get('roofs_seen')})
    # Owner rule (2026-09-28): a camp (野営) seen on screen is recalled, even
    # while an order is being driven (g421: a visible camp produced no
    # camp_found because the recall only started on chartless idle maps).
    # A Y jump in flight has already returned above (_drop_stale_y_jump), so
    # a jump can neither be hijacked nor block a later recall.
    recall = camp_recall_step(screen, mem, frame)
    if recall is not None:
        return recall
    # Owner rule (2026-09-28): periodically SELECT to the hero's position to
    # re-focus the view on where we are. The jump is camera motion we did not
    # measure: drop nav_last and demand a re-anchor (mirrors the search's
    # select_to_hero). Seeded on the first map frame, then every interval.
    if (_cursor(screen) and not screen.marker and not mem.get('y_jump')
            and not mem.get('select_used') and not mem.get('near_goal')):
        tick = int(mem.get('tick') or 0)
        last = mem.get('select_focus_tick')
        if last is None:
            mem['select_focus_tick'] = tick
        elif tick - int(last) >= SELECT_FOCUS_INTERVAL:
            mem['select_focus_tick'] = tick
            mem['nav_last'] = None
            mem['uncertain'] = True
            _record(mem, 'select_focus', observed_metric={'screen_cursor': list(_cursor(screen))},
                    reason='定期的にSELECTで主人公の位置へフォーカスして現在地を確認')
            return [pad('select')]
    order = _order(mem)
    if order is not None and (mem.get('source_miss') or {}).get(order['step'], 0) >= SOURCE_MISS_LIMIT:
        _give_up_source(mem, order)
        order = _order(mem)
    verifying = order is not None and (mem.get('orders') or {}).get(order['step']) == 'launched_unconfirmed'
    if order is not None and ((not verifying and not _ready(order, mem)) or _source(order, mem) not in _owned(mem)):
        # A defense loss revokes the capture an order was picked on. Holding
        # ``active`` past that keeps steering the cursor at the now-foreign
        # castle and pressing A there forever (g350, 2026-09-25: ジョンリギを
        # 失ったあとも J3 の出撃元へ戻って城情報だけを開き続けた)。前提が
        # 戻るまで次に選べる指示へ切り替える。
        _record(mem, 'order_precondition_lost', chart_step=order['step'],
                observed_metric={'captured': sorted(mem.get('captured') or []),
                                 'after': list(order.get('after') or ()),
                                 'source': _source(order, mem)},
                reason='実行中の指示の前提が失われたため指示を選び直す')
        mem['active'] = None
        mem['picked'] = []
        order = None
    if order is None and _cursor(screen) and not screen.marker and _world_map_wanted(mem):
        # Before choosing what to do next, read every castle's owner at once.
        mem['world_map_tick'] = int(mem.get('tick') or 0)   # bounded even if Y shows nothing
        mem.pop('world_map_due', None)
        _record(mem, 'world_map_open', screen=screen.kind,
                reason='全城の所有を全体マップで確認するためYを押す')
        return [pad('y')]
    if order is None:
        order = next((_order_for_step(mem, step) for step, sortie in (mem.get('sorties') or {}).items()
                      if sortie.get('status') == 'launched_unconfirmed'
                      and not sortie.get('verification_unavailable')
                      and sortie.get('source') in _owned(mem)), None)
        order = order or next_order(mem)
        if order is None:
            _off_chart(mem)
            order = next_order(mem)
        if order is None:
            update_world(screen, mem, frame)
            return []           # nothing charted: let real time advance
        mem['active'] = order['step']
        mem['picked'] = []
        mem.setdefault('rare_card_kit', {}).pop(order['step'], None)
        mem.setdefault('strong_card_kit', {}).pop(order['step'], None)
        mem.pop('rare_scan', None)
        mem['y_jumps'] = {k: v for k, v in (mem.get('y_jumps') or {}).items()
                          if not k.startswith(f"{order['step']}:")}
        mem.pop('castle_verified', None)
        _record(mem, 'order_start', chart_step=order['step'], **_deploy_context(order, mem),
                source=order['source'], target=order['target'], purpose=order.get('purpose'),
                cards=list(order['cards']),
                reason=order['note'])
    source = _source(order, mem)
    goal = chart.castles(mem['chapter'])[source]
    if screen.cursor and not screen.marker and _near_goal(mem, order, source, 'map'):
        aligned = _align_on_roof(screen, mem, frame, ('own',), f"{order['step']}:map")
        if aligned == 'on':
            mem.pop('near_goal', None)
            mem['cursor'], mem['uncertain'] = list(goal), False
            mem['expect_menu'] = True
            return _deploy_input(screen, mem, order, [pad('a')], '全体マップで選んだ出撃元の屋根に合わせて城を選択')
        if aligned:
            return _deploy_input(screen, mem, order, aligned, '全体マップで選んだ出撃元の屋根へカーソルを合わせる')
        mem.pop('near_goal', None)
        mem.pop('cursor', None)                  # no roof near: jump again rather than search
        _record(mem, 'align_failed', chart_step=order['step'], target=source, screen=screen.kind,
                reason='全体マップで選んだ出撃元の近くに屋根が無いため全体マップを開き直す')
    if screen.cursor and not screen.marker and _want_y_jump(mem, order, source, 'map'):
        return _start_y_jump(mem, order, source, 'map')
    result = nav_step(screen, mem, frame, goal, source)
    if mem.get('menu_miss'):
        if mem.get('anchor') and not mem.get('uncertain') and mem.get('cursor'):
            mem['menu_miss'] = 0   # roofs re-anchored: confirming is allowed again
            mem.pop('menu_hold', None)
        elif result == 'arrived' and int(mem.get('menu_hold') or 0) < MENU_HOLD_LIMIT:
            # Bounded (g419 09:06: 221 holds, ~10 min, after a Y jump had
            # cleared the anchor). Past the bound, the roof-under-cursor
            # check below still guards the press.
            mem['menu_hold'] = int(mem.get('menu_hold') or 0) + 1
            _record(mem, 'situation_held', screen=screen.kind,
                    observed_metric={'cursor': mem.get('cursor'),
                                     'screen_cursor': list(_cursor(screen) or ()),
                                     'held': mem['menu_hold']},
                    reason='城メニュー未確認のため位置を信用せず入力を保留して再アンカーを待つ')
            return []
        elif result == 'arrived':
            mem['menu_miss'] = 0
            mem.pop('menu_hold', None)
        elif result is None:
            if _cursor(screen):
                _record(mem, 'situation_held', screen=screen.kind,
                        observed_metric={'screen_cursor': list(_cursor(screen))},
                        reason='マップ位置を屋根アンカーで再特定できないため入力を保留')
            return []
    if result == 'arrived':
        if not _roof_under_cursor(screen, frame) and _hold_off_castle(screen, mem, order):
            return []
        mem.pop('off_castle', None)
        # If no castle menu follows, the cell was wrong: re-localize.
        mem['expect_menu'] = True
        return _deploy_input(screen, mem, order, [pad('a')], '出撃元の城を選択')
    if result is None:
        if _cursor(screen):
            _record(mem, 'situation_held', screen=screen.kind,
                    observed_metric={'screen_cursor': list(_cursor(screen))},
                    reason='マップ位置を屋根アンカーで再特定できないため入力を保留')
        return []
    return _deploy_input(screen, mem, order, result, '出撃元の城へカーソルを移動')


def target_step(screen: Screen, mem, frame):
    if _drop_stale_y_jump(screen, mem):
        return []
    order = _order(mem)
    if order is None:
        # A marker we did not request: cancel instead of sending a general.
        _record(mem, 'unexpected_target', reason='指示中でない出撃先選択画面のためBで取消')
        return [pad('b')]
    if _broken_hero_order(order, mem):
        return _cancel_broken_hero_sortie(mem, order)
    if _is_boss_order(order, mem):
        context = (mem.get('order_context') or {}).get(order['step']) or {}
        if (not context.get('actual_general')
                or context.get('actual_general') != _sortie_general(order, mem)
                or (mem.get('sortie_general') or {}).get(order['step']) != context.get('actual_general')
                or (context.get('observed_metric') or {}).get('cards') != sorted(_deploy_cards(order, mem))):
            return _hold_deploy(screen, mem, order, 'ボス出撃の選択本人と携行品の確認証拠がないため目標確定を保留')
    goal = chart.castles(mem['chapter'])[order['target']]
    result = None
    if screen.marker and _near_goal(mem, order, order['target'], 'target'):
        if order['target'] == chart.boss_castle(mem.get('chapter') or 0):
            aligned = 'on'                        # the tower has no own/enemy roof
        else:
            kinds = ('own',) if order['target'] in _owned(mem) else ('enemy',)
            aligned = _align_on_roof(screen, mem, frame, kinds, f"{order['step']}:target")
        if aligned == 'on':
            mem.pop('near_goal', None)
            mem['cursor'], mem['uncertain'] = list(goal), False
            result = 'arrived'
        elif aligned:
            return _deploy_input(screen, mem, order, aligned, '全体マップで選んだ出撃先の屋根へマーカーを合わせる')
        else:
            mem.pop('near_goal', None)
            mem.pop('cursor', None)
            _record(mem, 'align_failed', chart_step=order['step'], target=order['target'],
                    screen=screen.kind, reason='全体マップで選んだ出撃先の近くに屋根が無いため全体マップを開き直す')
    if result is None and screen.marker and _want_y_jump(mem, order, order['target'], 'target'):
        return _start_y_jump(mem, order, order['target'], 'target')
    if result is None:
        result = nav_step(screen, mem, frame, goal)
        if not result and screen.marker and not mem.get('cursor'):
            # Jumps spent and the marker's place unknown: nothing steers it
            # (g421 18:24: 70 idle observations). Count it as an unverified
            # arrival so the sortie is cancelled and retried within its bounds.
            return _unverified_target(screen, mem, order)
    if result == 'arrived' and not _target_roof_under_marker(screen, mem, frame, order):
        return _unverified_target(screen, mem, order)
    if result == 'arrived':
        mem.pop('sortie_attempt', None)
        (mem.get('target_miss') or {}).pop(order['step'], None)
        context = _deploy_context(order, mem, expected_metric='のりこんだ表示で目標城を確認')
        _finish_order(mem, 'launched', **context, target=order['target'],
                      cursor=mem.get('cursor'), anchor=mem.get('anchor'),
                      reason=f"{context['general']}を{order['target']}へ出撃")
        general = context['general']
        _garrison_move(mem, general, source=_source(order, mem))
        mem.setdefault('launched', {})[order['target']] = {'general': general, 'step': order['step']}
        # Snapshot: the battle, boss entry and retries of this sortie must not
        # depend on the plan still containing it.
        mem.setdefault('launched_orders', {})[order['step']] = {
            **order, 'cards': list(order['cards']),
            'after': list(order['after']) if order['after'] else None}
        # Per execution id: ``launched`` keeps one sortie per castle, so a later
        # sortie to the same castle must not unbind an earlier unit en route.
        mem.setdefault('sorties', {})[order['step']] = {
            'general': general, 'target': order['target'], 'status': 'en_route',
            'evidence': (mem.get('order_context') or {}).get(order['step']),
            'tick': int(mem.get('tick') or 0)}
        return [pad('a')]
    return _deploy_input(screen, mem, order, result or [], '出撃先へ目標カーソルを移動')


TARGET_MISS_LIMIT = 3          # unverified target arrivals before cancelling the sortie
TARGET_CANCEL_LIMIT = 2        # cancelled sorties per order before failing it
BOSS_TARGET_CANCEL_LIMIT = 4   # the boss sortie is worth more retries
BOSS_ABSENT_LIMIT = 10         # general-list readings without the boss general before giving up


def _target_roof_under_marker(screen, mem, frame, order) -> bool:
    """The marker stands on a castle roof of the expected owner.

    g419 08:34/08:44: after failed Y jumps the capped unverified arrival
    confirmed a target by dead reckoning alone, and the hero and ココット
    marched to open fields and camped. A target is confirmed only on a roof:
    enemy for an attack, ours for a move.
    """
    s = _cursor(screen)
    if not s or frame is None:
        return True
    if order['target'] == chart.boss_castle(mem.get('chapter') or 0):
        # The boss tower has no own/enemy roof (g421 13:48: two cancels).
        # Accept it only when a Y jump placed the marker on it.
        return (mem.get('y_jumped') or {}).get(order['step']) == order['target']
    roofs = castle_roofs(frame)
    if not roofs and (mem.get('y_jumped') or {}).get(order['step']) == order['target']:
        return True                           # winter palette: trust the Y jump (g421 18:13)
    want = 'own' if order['target'] in _owned(mem) else 'enemy'
    return any(r['kind'] == want
               and abs(r['target'][0] - s[0]) <= UNDER_CURSOR_PX
               and abs(r['target'][1] - s[1]) <= UNDER_CURSOR_PX
               for r in roofs)


def _unverified_target(screen, mem, order):
    step = order['step']
    misses = mem.setdefault('target_miss', {})
    misses[step] = misses.get(step, 0) + 1
    if misses[step] < TARGET_MISS_LIMIT:
        mem['uncertain'] = True               # re-anchor on roofs before confirming
        mem.pop('anchor', None)
        _record(mem, 'target_not_under_marker', chart_step=step, target=order['target'],
                observed_metric={'cursor': mem.get('cursor'), 'marker': list(_cursor(screen) or ()),
                                 'misses': misses[step]},
                reason='出撃先マーカーの位置に目的の城の屋根が無いため決定せず位置を取り直す')
        return []
    misses.pop(step, None)
    # A retry of this order starts with fresh Y jumps (g421 13:48: the limit
    # carried over from the first attempt, so the boss target was walked to).
    mem['y_jumps'] = {k: v for k, v in (mem.get('y_jumps') or {}).items()
                      if not k.startswith(f'{step}:')}
    (mem.get('y_jumped') or {}).pop(step, None)
    cancels = mem.setdefault('target_cancel', {})
    cancels[step] = cancels.get(step, 0) + 1
    mem.pop('sortie_attempt', None)
    limit = (BOSS_TARGET_CANCEL_LIMIT if order['target'] == chart.boss_castle(mem.get('chapter') or 0)
             else TARGET_CANCEL_LIMIT)
    state = 'failed' if cancels[step] >= limit else 'pending'
    _finish_order(mem, state, target=order['target'],
                  deviation_reason='出撃先を屋根で確認できない',
                  observed_metric={'marker': list(_cursor(screen) or ()), 'cancels': cancels[step]},
                  reason='誤った場所へ出撃させないよう出撃を取り消す（将軍は城に残る）')
    return [pad('b')]


# Owner (2026-09-29): 敵にエッグを使わせない. gcgx ai.html: an enemy general
# uses its egg when the battle's card IDs total 48 or more (patterns 1-3).
CARD_IDS = reference.ALL_CARD_IDS
ENEMY_EGG_CARD_ID_SUM = reference.ENEMY_EGG_CARD_ID_SUM


def _cap_card_ids(cards):
    """Keep cards in plan order while their ID total stays under the enemy egg threshold."""
    kept, total = [], 0
    for card in cards:
        card_id = CARD_IDS.get(card)
        if card_id is None or total + card_id >= ENEMY_EGG_CARD_ID_SUM:
            continue
        kept.append(card)
        total += card_id
    return kept


def _strict_boss_cards(order, mem) -> bool:
    """The base chart's boss sortie carries exactly its charted cards; an
    adjusted/interim boss order may leave an unavailable card behind (g438
    04:03: J1 wanted ミックミー, none was owned, and card_select held for good)."""
    return _is_boss_order(order, mem) and any(
        o['step'] == order['step'] for o in chart.orders(mem.get('chapter') or 0))


def _deploy_cards(order, mem):
    rare_kit = (mem.get('rare_card_kit') or {}).get(order['step'])
    strong = (mem.get('strong_card_kit') or {}).get(order['step']) if rare_kit is None else None
    if _strict_boss_cards(order, mem) and rare_kit is None:
        return _with_strong_card(list(order['cards']), strong, mem, order['step'])
    cards = list(rare_kit if rare_kit is not None else
                 mem.get('card_override', {}).get(order['step'], order['cards']))
    # One planned copy leaves the kit per drop entry. g454 12:22: a planned
    # イッテツーン x2 had one copy already picked and the second recorded as
    # dropped; removing every copy of the card shrank the plan below what the
    # sortie actually carried and the confirmation held forever.
    for card in (mem.get('card_drop') or {}).get(order['step']) or ():
        if card in cards:
            cards.remove(card)
    # One carried copy leaves the kit per attempted use in this sortie (g452:
    # ヴィーナス spent both イッテツーン in one battle and the next battle
    # re-planned them, opened an empty きりふだ list and stalled).
    for card in (mem.get('kit_spent') or {}).get(order['step']) or ():
        if card in cards:
            cards.remove(card)
    capped = _cap_card_ids(cards)
    if capped != cards and (mem.get('cards_capped') or {}).get(order['step']) != capped:
        mem.setdefault('cards_capped', {})[order['step']] = capped
        _record(mem, 'cards_capped', chart_step=order['step'],
                observed_metric={'planned': cards, 'carried': capped,
                                 'id_sum': sum(CARD_IDS[c] for c in capped)},
                reason='切り札IDの合計が48以上だと敵がエッグを使うため、47以下になるよう携行札を絞る')
    return _with_strong_card(capped, strong, mem, order['step'])


def _with_strong_card(cards, strong, mem, step):
    """A carried strong card only when a slot, a budget and the kit allow it.

    予定札(チャート/調整/レア札)は外さない。gcgx: 携行札のID合計が48以上だと敵が
    エッグを使うため、追加後も ``< 48`` を満たす時だけ1枚足す。同じ札がもう1枚
    携行済みなら足さない(在庫を無駄にしない)。この出撃で消費・破棄した札は
    再び足さない: 一覧に無い札を開くと空の一覧で止まる(g438 04:18)。
    """
    if not strong or strong in cards or len(cards) >= CARRY_SLOTS:
        return cards
    if strong in ((mem.get('kit_spent') or {}).get(step) or ()) or strong in ((mem.get('card_drop') or {}).get(step) or ()):
        return cards
    if sum(CARD_IDS[c] for c in cards) + CARD_IDS.get(strong, ENEMY_EGG_CARD_ID_SUM) >= ENEMY_EGG_CARD_ID_SUM:
        return cards
    return [*cards, strong]


RARE_SCAN_LIMIT = 32          # the entire 32-card catalogue, once per observed month
CARRY_SLOTS = 3               # card_select「あとNこ」の携行枠(実測 header は 0..3)
CARD_SCROLL_LIMIT = 8         # downward presses looking for a card below a full panel
CARD_MISS_LIMIT = 5           # card_select readings before a missing card is left behind
CARD_UNREADABLE_LIMIT = 6     # unreadable card_select readings before cancelling the sortie
SORTIE_CONFIRM_LIMIT = 8      # readings before a readable but mismatched kit is approved
CARD_STOCK_LIMIT = 24         # observed card names kept for the adjusted-chart request


def _observe_card_stock(mem, inventory):
    """Remember the stocks a sortie card panel showed (chart-adjust grounding).

    Fewer than four rows is the whole inventory (depleted items disappear), so
    a known card absent from it is out of stock now and recorded as 0; a
    four-row panel is a window and only its rows are updated. g438 04:04: the
    adjusted chart planned ミックミー and エンジェリン for chapter 1, where
    neither was ever in the panel -- the model was never told what the player
    actually holds. The request now carries this so it plans from observed
    stock.
    """
    stock = mem.setdefault('card_stock', {})
    shown = {row['card']: row['stock'] for row in inventory['rows']}
    stock.update(shown)
    if len(inventory['rows']) < 4:
        for card in stock:
            if card not in shown:
                stock[card] = 0
    while len(stock) > CARD_STOCK_LIMIT:
        stock.pop(next(iter(stock)))



def _rare_card_inventory(screen, mem, order, inventory):
    """Discover event stock before picking; freeze a positive, measured kit.

    One bounded sweep per month reaches hidden event cards without declaring
    the first four rows a complete inventory. Rewind before normal selection.
    Never change an already picked/in-flight sortie or invent event stock.
    """
    if (mem.get('picked') or inventory['remaining'] == 0
            or (mem.get('rare_card_kit') or {}).get(order['step']) is not None):
        return None
    month = mem.get('month') or f"chapter-{mem.get('chapter')}:unknown"
    scan = mem.get('rare_scan') or {}
    if scan.get('rewind', 0) > 0:
        scan['rewind'] -= 1
        if not scan['rewind']:
            mem.pop('rare_scan', None)
        _record(mem, 'sortie_input', **_deploy_context(order, mem),
                screen='card_select', observed_metric={'rare_scan': 'rewind', 'remaining': scan['rewind']},
                reason='レア札在庫の有限探索後に選択カーソルを戻す')
        return [pad('up')]
    row = next((r for r in inventory['rows'] if r['card'] == 'キャトルミュー'), None)
    if row and row['stock'] > 0 and inventory['remaining'] > 0:
        original = _deploy_cards(order, mem)
        kit = _cap_card_ids(['キャトルミュー', *[c for c in original if c != 'キャトルミュー']])[:3]
        mem.setdefault('rare_card_kit', {})[order['step']] = kit
        mem['rare_scan_month'] = month
        mem.pop('rare_scan', None)
        _record(mem, 'rare_card_kit', **_deploy_context(order, mem), chart_step=order['step'],
                observed_metric={'card': row['card'], 'stock': row['stock'], 'cards': kit,
                                 'original_cards': original, 'id_sum': sum(CARD_IDS[c] for c in kit)},
                reason='実在庫と携行枠を確認し、イベント札を1枚携行して活用する')
        return []
    if not isinstance(month, str) or mem.get('rare_scan_month') == month:
        return None
    scan = mem.setdefault('rare_scan', {'step': order['step'], 'presses': 0, 'rows': None})
    if scan['step'] != order['step']:
        mem.pop('rare_scan', None)
        return None
    rows = [r['card'] for r in inventory['rows']]
    at_bottom = inventory['selected_y'] == inventory['rows'][-1]['y']
    if (len(rows) < 4 or scan['presses'] >= RARE_SCAN_LIMIT
            or (at_bottom and scan['rows'] == rows and scan.get('was_bottom'))):
        mem['rare_scan_month'] = month
        if scan['presses']:
            scan['rewind'] = scan['presses'] - 1
            if not scan['rewind']:
                mem.pop('rare_scan', None)
            _record(mem, 'sortie_input', **_deploy_context(order, mem),
                    screen='card_select', observed_metric={'rare_scan': 'rewind', 'remaining': scan['rewind']},
                    reason='レア札探索の末尾または上限に達したため選択カーソルを戻す')
            return [pad('up')]
        mem.pop('rare_scan', None)
        return None
    scan.update(presses=scan['presses'] + 1, rows=rows, was_bottom=at_bottom)
    _record(mem, 'sortie_input', **_deploy_context(order, mem), screen='card_select',
            observed_metric={'rare_scan': 'discover', 'presses': scan['presses'], 'rows': rows},
            reason='未選択の実在庫を有限回探索し、隠れたイベント札の有無を確認する')
    return [pad('down')]

def _strong_card_inventory(mem, order, inventory, wanted) -> bool:
    """Add one held strong card to a sortie that still has a carry slot free.

    owner 2026-10-03: 「強い切り札を偶然手に入れている時などは、強い将軍とたたかう
    ときに積極的に利用するようにして下さい」→ 携行はここで決めるが、使うのは
    戦闘で敵が強い将軍と判定されたときだけ (``reference.strong_general``)。

    予定札(チャート/調整)は外さず、追加後も携行札のID合計が48未満を維持する
    (gcgx: 48以上だと敵がエッグを使う)。レア札キットの出撃と残枠なしの出撃には
    追加しない。決定は一度だけ(order_start で破棄して次の出撃で選び直す)。
    在庫の根拠は直前に更新された ``card_stock`` の正数だけにする。
    追加した時だけ True を返し、呼び出し側が同じ観測の選択対象を並べ直す
    (予定札が先頭なので、強い切り札は残り枠が空いた時に選ばれる)。
    """
    step = order['step']
    if (mem.get('strong_card_kit') or {}).get(step) is not None:
        return False
    if (mem.get('rare_card_kit') or {}).get(step) is not None:
        return False                     # レア札キットが携行札の主導権を持つ
    # 1枚でも選ぶ前 (予定札の内訳がまだ揃っている時点) だけ決める。
    if mem.get('picked') or inventory['remaining'] - len(wanted) < 1:
        return False                     # 残り枠は予定札で埋まる
    stock = mem.get('card_stock') or {}
    used = sum(CARD_IDS[c] for c in wanted)
    card = next((name for name in reference.STRONG_CARDS
                 if name not in wanted and int(stock.get(name, 0) or 0) > 0
                 and used + CARD_IDS.get(name, ENEMY_EGG_CARD_ID_SUM) < ENEMY_EGG_CARD_ID_SUM
                 and len(wanted) + 1 <= CARRY_SLOTS), None)
    if card is None:
        return False
    mem.setdefault('strong_card_kit', {})[step] = card
    carried = _deploy_cards(order, mem)
    _record(mem, 'strong_card_kit', **_deploy_context(order, mem), chart_step=step, card=card,
            observed_metric={'stock': int(stock.get(card, 0) or 0), 'planned': list(wanted),
                             'cards': carried, 'id_sum': sum(CARD_IDS[c] for c in carried),
                             'remaining': inventory['remaining']},
            reason='実在庫の強い切り札を携行枠に加え、強い将軍との戦闘で開幕に使う')
    return True


def _drop_card(screen, mem, order, card, inventory):
    """Leave a planned card behind after it stays unselectable (non-boss only).

    A measured panel shows at most four rows, so a card absent from a full
    panel is not proven to be out of stock (it may be scrolled off), but
    holding forever is worse: g401 21:16 an adjusted-chart order wanted
    ダイチスイム and the sortie screen stayed open for minutes. After
    ``CARD_MISS_LIMIT`` readings the card is dropped with evidence; with no
    carry slot left every remaining card is dropped.
    """
    misses = mem.setdefault('card_miss', {})
    key = f"{order['step']}:{card}"
    misses[key] = misses.get(key, 0) + 1
    if misses[key] < CARD_MISS_LIMIT:
        return None
    misses.pop(key, None)
    wanted = _deploy_cards(order, mem)
    for picked in mem.get('picked') or ():
        if picked in wanted:
            wanted.remove(picked)
    # One entry per unselectable copy: with no carry slot left every remaining
    # planned copy goes, otherwise every copy of the missing card does.
    dropped = (list(wanted) if inventory['remaining'] == 0
               else [c for c in wanted if c == card])
    mem.setdefault('card_drop', {}).setdefault(order['step'], []).extend(dropped)
    context = {**_deploy_context(order, mem),
               'deviation_reason': f'{card}を選べないため携行せずに出撃する'}
    _record(mem, 'card_dropped', **context, card=card, dropped=dropped,
            observed_metric={'rows': [[r['card'], r['stock']] for r in inventory['rows']],
                             'remaining': inventory['remaining'],
                             # fewer than 4 rows is the whole stock; 4 may hide more
                             'complete_list': len(inventory['rows']) < 4,
                             'readings': CARD_MISS_LIMIT},
            reason='予定切り札の在庫・携行枠を確認できない状態が続いたため、その札を外して出撃を続ける')
    return []


def _selected_actor_guard(screen, mem, order):
    """The priority substitute must be the actual named sortie-panel actor.

    x16/80 y31 is the existing measured field/sortie status layout. A planned
    A on the list can miss; its stored name alone never authorizes card or
    departure confirmation for the new substitute path.
    """
    selected = (mem.get('sortie_general') or {}).get(order['step'])
    if order['general'] != NAME or not selected or selected == NAME:
        return None
    row = next((line for line in screen.lines if line.y == 31), None)
    name = row.span(16, 80).strip() if row else None
    if (not row or row.span(80, 128) != 'しょうぐん' or not name
            or UNKNOWN in name or general_max_hp('しゅじんこう' if name == NAME else name) is None):
        misses = mem.setdefault('sortie_actor_miss', {})
        misses[order['step']] = misses.get(order['step'], 0) + 1
        if misses[order['step']] < 3:
            return _hold_deploy(screen, mem, order, '代役の出撃画面の本人名を読めないため保留')
        reason = '代役の出撃画面の本人名を3回で確認できず出撃を取り消す'
    elif name != selected:
        reason = f'選択予定の{selected}と実画面の{name}が違うため出撃を取り消す'
    else:
        (mem.get('sortie_actor_miss') or {}).pop(order['step'], None)
        return None
    (mem.get('general_override') or {}).pop(order['step'], None)
    (mem.get('sortie_actor_miss') or {}).pop(order['step'], None)
    return _hold_guarded_sortie(mem, order, 'sortie_actor_unconfirmed', reason,
                               {'selected': selected, 'observed': name, 'screen': screen.kind})


def _deploy_context(order, mem, *, expected_metric=None):
    general = _sortie_general(order, mem)
    context = {'general': general, 'planned_general': order['general'],
            'expected_metric': expected_metric,
            'strategy_variant': 'substitute_general' if general != order['general'] else mem.get('variant', 'chart'),
            'deviation_reason': (f"計画の{order['general']}に代わり{general}を出撃させる"
                                 if general != order['general'] else None)}
    retry = (mem.get('retry_context') or {}).get(order['step'])
    if retry:
        context.update(strategy_variant=retry.get('strategy_variant', 'retry_with_opening_cards'),
                       deviation_reason=retry.get('deviation_reason'),
                       expected_metric=retry.get('expected_metric'))
    if (mem.get('rare_card_kit') or {}).get(order['step']):
        context.update(strategy_variant='rare_cattlemyu',
                       deviation_reason='実在庫のキャトルミューを活用するため携行札を変更')
    return context


def _deploy_input(screen, mem, order, actions, reason):
    # Retry navigation used to have no decision record, losing its context in
    # action_plan. Ordinary first-attempt records retain their existing shape.
    if (mem.get('retry_context') or {}).get(order['step']):
        _record(mem, 'sortie_input', **_deploy_context(order, mem), screen=screen.kind,
                observed_metric={'planned_buttons': [a.get('buttons') for a in actions]},
                reason=reason)
    return actions


def _hold_deploy(screen, mem, order, reason, *, card=None, carried=None):
    context = _deploy_context(order, mem, expected_metric={'cards': _deploy_cards(order, mem)})
    if not (mem.get('retry_context') or {}).get(order['step']):
        context.update(strategy_variant='sortie_cards_unclassified' if screen.kind == 'sortie_confirm'
                       else 'sortie_menu_unclassified', deviation_reason=reason)
    _record(mem, 'situation_held', **context, screen=screen.kind, card=card,
            observed_metric={'hand': screen.hand,
                             'candidates': [{'x': x, 'y': y, 'text': w[:80]} for x, y, w in _options(screen)[:32]],
                             'carried_cards_read': carried, 'confirmation': 'unclassified'},
            reason=reason)
    return []


def _empty_sortie_inventory(screen):
    """Measured g328 empty inventory, using exact text cells in its panels.

    Only these text baselines establish the receipt. Left-side stats, mark
    rows above glyphs and the hand left of the choices are outside them.
    No quantity or nonempty-inventory format is inferred here.
    """
    if screen.kind != 'sortie_confirm':
        return False
    rows = ((47, 136, ((136, 'きりふだ'),)),
            (63, 136, ((176, 'きりふだは'),)),
            (79, 136, ((176, 'ありません……'),)),
            (95, 136, ((136, 'ーしゅつげき'), (192, 'しますか?ー'))),
            (111, 160, ((160, 'うむッ!'),)),
            (127, 160, ((160, 'いかんッ!'),)))
    for y, left, spans in rows:
        expected = tuple((x + 8 * index, ch) for x, text in spans for index, ch in enumerate(text))
        observed = tuple((x, ch) for line in screen.lines if line.y == y
                         for x, ch in line.cells if left <= x < 240)
        if observed != expected:
            return False
    return not any(card in word for _, _, word in _options(screen) for card in CARD_NAMES)


def _sortie_inventory(screen):
    """Read complete card names from the fixed sortie panel slots.

    The one-card placement is live-measured. Additional names are classified
    only when each occupies a complete known slot and the whole panel matches;
    this recognizes text, not quantities or partial names.
    """
    if screen.kind != 'sortie_confirm' or screen.hand not in ((138, 105, 156, 118), (138, 105, 158, 118)):
        return None
    def cells(y, left=136):
        return tuple((x, ch) for line in screen.lines if line.y == y
                     for x, ch in line.cells if left <= x < 256)
    def text_cells(x, text):
        return tuple((x + 8*i, ch) for i, ch in enumerate(text))
    first = cells(47)
    heading = text_cells(136, 'きりふだ')
    first_spans = TextLine(47, tuple((x, ch) for x, ch in first if x >= 176)).spans()
    if len(first_spans) != 1 or first_spans[0][0] != 176 or first_spans[0][1] not in CARD_NAMES:
        return None
    cards = [first_spans[0][1]]
    if first != heading + text_cells(176, cards[0]):
        return None
    expected = {47: first, 63: (), 79: (),
                95: text_cells(136, 'ーしゅつげき') + text_cells(192, 'しますか?ー'),
                111: text_cells(160, 'うむッ!'), 127: text_cells(160, 'いかんッ!')}
    empty_tail = False
    for y in (63, 79):
        row = cells(y)
        if not row:
            empty_tail = True
            continue
        if empty_tail:
            return None
        spans = TextLine(y, tuple((x, ch) for x, ch in row if x >= 176)).spans()
        if len(spans) != 1 or spans[0][0] != 176 or spans[0][1] not in CARD_NAMES:
            return None
        if row != text_cells(176, spans[0][1]):
            return None
        cards.append(spans[0][1])
        expected[y] = row
    for y, wanted in expected.items():
        if cells(y, 160 if y in (111, 127) else 136) != wanted:
            return None
    # Background and dakuten/cursor UNKNOWNs are not body text. Additional
    # known text anywhere else inside this right panel is uncalibrated.
    if any(ch != UNKNOWN and 136 <= x < 256
           and (line.y not in expected or (line.y in (111, 127) and x < 160))
           for line in screen.lines if 32 <= line.y < 135 for x, ch in line.cells):
        return None
    if [word for _, _, word in _options(screen) if word in CARD_NAMES] != cards:
        return None
    return cards


def _measured_card_select(screen):
    """Read contiguous rows in the measured g328 panel, never infer stock.

    Stock is right-aligned at x=232; live tens for 10-99 sit at x=224.
    Depleted final items disappear. Empty baselines must form a trailing
    suffix; UNKNOWN cells are unreadable rows, not evidence of emptiness.
    """
    if screen.kind != 'card_select':
        return None
    def cells(y, left):
        return tuple((x, ch) for line in screen.lines if line.y == y
                     for x, ch in line.cells if left <= x < 256)
    def text_cells(x, text):
        return tuple((x + 8 * i, ch) for i, ch in enumerate(text))
    header = cells(31, 136)
    remaining = dict(header).get(224)
    if remaining not in ('0', '1', '2', '3'):
        return None
    if header != text_cells(136, 'きりふだセレクト') + text_cells(208, f'あと{remaining}こ'):
        return None
    if cells(127, 136) != text_cells(136, 'バトルようのきりふだです'):
        return None
    rows = []
    empty_tail = False
    for y in (55, 71, 87, 103):
        row = cells(y, 160)
        if not row:
            empty_tail = True
            continue
        if empty_tail:
            return None
        digits = dict(row)
        ones = digits.get(232)
        if ones not in tuple('0123456789'):
            return None
        tens = digits.get(224)
        # Live g328 stock is right-aligned at x=232; tens sit at x=224 (10-99).
        if tens in tuple('123456789'):
            stock_text = tens + ones
            name_limit = 224
            stock_cells = ((224, tens), (232, ones))
        else:
            stock_text = ones
            name_limit = 232
            stock_cells = ((232, ones),)
        names = TextLine(y, tuple((x, ch) for x, ch in row if x < name_limit)).spans()
        # Every real card name (ALL_CARD_NAMES, the 32-card gcgx table) is a
        # valid row, not just the selectable subset: g454 10:02 the panel
        # showed バルムンク (an event card outside CARD_NAMES) and every
        # reading was rejected, so the sortie held forever. Unknown cards are
        # read as rows but can never be selected by name.
        if len(names) != 1 or names[0][0] != 160 or names[0][1] not in ALL_CARD_NAMES:
            return None
        name = names[0][1]
        if row != text_cells(160, name) + stock_cells:
            return None
        rows.append({'y': y, 'card': name, 'stock': int(stock_text)})
    if not rows or len({row['card'] for row in rows}) != len(rows):
        return None
    # Extra text rows would be an uncalibrated inventory/scroll layout. Mark
    # rows above the known text can contain UNKNOWN and are not inventory.
    if any(ch != UNKNOWN and 136 <= x < 256
           and (line.y not in (55, 71, 87, 103) or x < 160)
           for line in screen.lines if 32 <= line.y < 127
           for x, ch in line.cells):
        return None
    if not screen.hand:
        return None
    x0, y0, x1, y1 = screen.hand
    selected_y = y0 + 6
    if (x0 != 138 or x1 not in (156, 158) or y1 != selected_y + 7
            or selected_y not in {row['y'] for row in rows}):
        return None
    return {'rows': rows, 'selected_y': selected_y, 'remaining': int(remaining),
            'inventory_evidence': ('measured_four_row_card_select' if len(rows) == 4
                                   else 'structured_card_select'), 'observed_rows': len(rows)}


def _move_general_pick(screen, mem, order):
    """Pick the general a move sends: never the donor's last one."""
    present = _present_generals(screen)
    if present is None:
        return _hold_deploy(screen, mem, order, '移動元の将軍一覧を読めないため保留')
    busy = _en_route(mem)[0]
    spare = sorted((g for g in present if g not in busy), key=lambda g: g == NAME)
    if len(present) < 2 or not spare:
        _finish_order(mem, 'failed', deviation_reason='移動元に残す将軍がいない',
                      observed_metric=present[:8], source=_source(order, mem),
                      reason='移動元の将軍が1人以下のため移動を取り消す（一覧は駐留として記録）')
        return [pad('b'), pad('b')]
    general = mem.get('general_override', {}).get(order['step'])
    if general not in spare:
        general = spare[0]
        mem.setdefault('general_override', {})[order['step']] = general
        _record(mem, 'move_general_picked', chart_step=order['step'], general=general,
                source=_source(order, mem), target=order['target'], observed_metric=present[:8],
                reason='移動元に1人以上残して主人公以外の将軍を空の城へ送る')
    move = menu_to(screen, general)
    if move is None:
        return _hold_deploy(screen, mem, order, '移動させる将軍へカーソルを合わせられないため保留')
    return _deploy_input(screen, mem, order, [pad('a')] if move == 'here' else [move],
                         '移動させる将軍を選択')


GENERAL_LIST_UI = frozenset({'しゅつげき', 'ステータス', 'しょうぐんは', 'おりません', 'おりません……'})


def _present_generals(screen):
    """Names on a general list, [] for the empty-list message, None if unreadable."""
    if 'おりません' in screen.text:
        return []
    if not screen.hand:
        return None
    names = [w for x, y, w in _options(screen) if x > 100 and w not in GENERAL_LIST_UI
             and not re.search(r'\d', w) and 'おりません' not in w]
    if not names or any(UNKNOWN in w for w in names):
        return None               # a partial reading is not evidence of who is absent
    return names


def _observe_garrison(screen, mem, order):
    """Remember who a castle's general list showed (``interim_candidates``)."""
    present = _present_generals(screen)
    castle = _source(order, mem)
    if present is None or not castle:
        return
    garrison = mem.setdefault('garrison', {})
    mem['general_location_unknown'] = [g for g in mem.get('general_location_unknown') or ()
                                       if g not in present or not _general_visible(screen, g)]
    verification = mem.get('recruit_verification') or {}
    joined = (set(present) & set(verification.get('candidates') or ())) - set(
        verification.get('generals_before') or ())
    if joined:
        _record(mem, 'recruit_join_observed', castle=castle, month=verification.get('month'),
                observed_metric={'generals': sorted(joined)},
                reason='募集で紹介された将軍を在城一覧で確認し、採用後の配置を実測した')
        mem.pop('recruit_verification', None)
    if garrison.get(castle) != present:
        garrison[castle] = present
        _record(mem, 'garrison_seen', **_deploy_context(order, mem), castle=castle,
                observed_metric=present[:8], reason='出撃元の将軍一覧から駐留将軍を記録')


def _garrison_move(mem, general, source=None, target=None):
    """A general left ``source`` and/or now holds ``target`` (known lists only)."""
    garrison = mem.setdefault('garrison', {})
    if source and garrison.get(source) is not None:
        garrison[source] = [g for g in garrison[source] if g != general]
    if target and general:
        mem['general_location_unknown'] = [g for g in mem.get('general_location_unknown') or () if g != general]
        here = [g for g in garrison.get(target) or () if g != general]
        garrison[target] = [*here, general]


def _name_read_cleanly(line, name) -> bool:
    """The name is one whole word with no unread tile touching it.

    The hand cursor and the row's icon are unread tiles on the same row but
    one cell away from the name; counting them held the boss sortie for over
    80 minutes (g421 11:48: どうし read cleanly, hand on its row).
    """
    span = next((x for x, word in line.spans() if word == name), None)
    if span is None:
        return False
    cells = dict(line.cells)
    return (cells.get(span - 8) != UNKNOWN
            and cells.get(span + 8 * len(name)) != UNKNOWN)


# Chart label -> the name ステータス shows. Chapter 1's home castle is
# アルマムーン in the game and in this chart; it used to be labelled ほんじょう
# here, which sent every home sortie through a name check and switched the
# chapter detector (g436 21:19: every home sortie refused as "the wrong
# castle", 1-A1/1-V1 failed; g436 21:33: a defense of アルマムーン advanced a
# chapter 1 game to chapter 2). The labels now equal the on-screen names, so
# this map is empty; it stays for a genuine future alias.
STATUS_NAMES: dict[str, str] = {}
CASTLE_STATUS = re.compile(r'(?:しゅつげき)?([^\ufffd\s]+?)じょうステータスしゅうにゅう')


def _check_source_castle(screen, mem, order):
    """Read the castle's name (ステータス) before sending anyone from it.

    g421 18:13: a Y jump to アルマムーン closed early, A opened the menu of
    the castle under the cursor - フーリック, where the hero stood - and the
    bot recorded どうし at アルマムーン and sent him to フーリック itself; the
    sortie went nowhere. Measured (isolated probe): ステータス shows
    「<name>じょう しゅうにゅう… しょうぐんNめい …」 beside the menu and B
    returns to it. ``None`` means verified: continue with しゅつげき.
    """
    step, source = order['step'], _source(order, mem)
    m = CASTLE_STATUS.search(screen.text)
    if m is None:
        if mem.get('castle_verified') == step:
            return None
        move = menu_to(screen, 'ステータス')
        if move is None:
            return None                   # unreadable menu: the old path holds with evidence
        return _deploy_input(screen, mem, order, [pad('a')] if move == 'here' else [move],
                             '出撃前に城のステータスで城名を確認')
    name = m.group(1)
    if name == source or STATUS_NAMES.get(source) == name:
        mem['castle_verified'] = step
        # Verification does not prove the status panel has closed. Its hand
        # is hidden while open, so attempting a sortie here can wait forever.
        # Keep closing only the positively identified panel until it is gone.
        return [pad('b')]
    mem.pop('castle_verified', None)
    cells = chart.castles(mem.get('chapter') or 0)
    label = next((k for k, v in STATUS_NAMES.items() if v == name and k in cells), name)
    if label in cells:
        mem['cursor'], mem['uncertain'] = list(cells[label]), False    # we know where we are now
    misses = mem.setdefault('source_miss', {})
    misses[step] = misses.get(step, 0) + 1
    _record(mem, 'source_castle_mismatch', chart_step=step, source=source,
            observed_metric={'castle': name, 'misses': misses[step]},
            reason=f'出撃元{source}のつもりで開いた城が{name}だったため出撃せず現在地を直して向かい直す')
    return [pad('b'), {'type': 'wait', 'ms': 500}, pad('b')]


LAST_CASTLE_HOLD_TICKS = 300   # observations before the last castle's list is read again


def _staffed_elsewhere(mem, source) -> bool:
    """Another castle of ours is known to hold a general who is not marching.

    A castle whose list was never read (or was cleared by a recruit) is not
    counted: the guard below errs towards keeping someone home.
    """
    busy, _ = _en_route(mem)
    garrison = mem.get('garrison') or {}
    owned = _owned(mem) & set(chart.castles(mem.get('chapter') or 0))
    return any(c != source and any(g not in busy for g in garrison.get(c) or ()) for c in owned)


def _keep_last_castle(screen, mem, order):
    """Never send out the last general standing in any castle of ours.

    g421 18:55: フーリック had fallen, アルマムーン was our only castle and どうし
    its only general; he was sent to ドミノーラ, the empty castle was taken at
    18:57 and with no castle left the game ended (title, game_over). The
    isolated probe then showed the same with two castles: エシャロット left
    アルマムーン and ゼウス left フーリック, both empty at once. A sortie whose
    list shows one general, while no other castle of ours is known to keep
    one, is cancelled and waits (bounded) for another general. ``None`` lets
    the sortie go on.
    """
    if _is_boss_order(order, mem) or order.get('purpose') == 'move':
        return None                        # the boss battle ends the chapter; a move keeps one (_move_general_pick)
    source = _source(order, mem)
    present = _present_generals(screen)
    if (present is None or len(present) != 1 or source not in _owned(mem)
            or not _few_castles(mem) or _staffed_elsewhere(mem, source)):
        return None
    mem['last_castle_hold'] = {'castle': source, 'tick': int(mem.get('tick') or 0)}
    mem['orders'][order['step']] = 'pending'
    mem['active'] = None
    mem['picked'] = []
    _record(mem, 'sortie_held_last_castle', chart_step=order['step'], source=source,
            observed_metric=present[:4],
            reason=f'{source}の将軍が{len(present)}人で他の自軍城に待機将軍がいないため、全城を空にして落城・ゲームオーバーにならないよう出撃しない')
    return [pad('b'), {'type': 'wait', 'ms': 300}, pad('b')]


LAST_CASTLE_GUARD_OWNED = 2   # the guard only matters while so few castles remain


def _few_castles(mem) -> bool:
    """Owner (2026-09-29): with six castles the guard only cancelled the chart's
    hero sortie to スペンソニア on stream; a total loss (game over) is a risk
    only when one or two castles are left (g436 18:57: the last one fell)."""
    owned = _owned(mem) & set(chart.castles(mem.get('chapter') or 0))
    return len(owned) <= LAST_CASTLE_GUARD_OWNED


def _last_castle_held(mem, order) -> bool:
    hold = mem.get('last_castle_hold') or {}
    source = _source(order, mem)
    return (not _is_boss_order(order, mem) and order.get('purpose') != 'move' and hold.get('castle') == source
            and _few_castles(mem) and not _staffed_elsewhere(mem, source)
            and int(mem.get('tick') or 0) - int(hold.get('tick') or 0) < LAST_CASTLE_HOLD_TICKS)


def _hold_guarded_sortie(mem, order, decision, reason, observed):
    mem.setdefault('orders', {})[order['step']] = (
        'failed' if order['step'].startswith('I:') else 'pending')
    mem['active'] = None
    mem['picked'] = []
    mem.setdefault('sortie_general', {}).pop(order['step'], None)
    mem.setdefault('order_context', {}).pop(order['step'], None)
    _record(mem, decision, chart_step=order['step'], source=_source(order, mem),
            target=order['target'], observed_metric=observed, reason=reason)
    return [pad('b'), pad('b')]


def _keep_sortie_defender(screen, mem, order):
    if not _reserve_source_guard(order, mem):
        return None
    present = _present_generals(screen)
    if present is None:
        return _hold_deploy(screen, mem, order, '守備を残せる将軍一覧を読めないため出撃を保留')
    unavailable = _en_route(mem)[0] | set(mem.get('general_location_unknown') or ())
    available = set(present) - unavailable
    if len(available) >= 2:
        return None
    return _hold_guarded_sortie(mem, order, 'sortie_held_source_defender',
        '奪還・暫定攻撃の出撃元に待機将軍を1人残せないため出撃を取り消す',
        {'present': present[:8], 'available': sorted(available)})


def deploy_step(screen: Screen, mem):
    order = _order(mem)
    kind = screen.kind
    if order is None:
        # Menus we did not open (e.g. confirm pressed by an earlier fallback).
        return [pad('b')]
    if kind == 'general_list' and (mem.get('orders') or {}).get(order['step']) != 'launched_unconfirmed':
        # A newly selected sortie may use a different actor. An interrupted
        # departure instead retains its evidence for source reconciliation.
        (mem.get('order_context') or {}).pop(order['step'], None)
        (mem.get('sortie_general') or {}).pop(order['step'], None)
    if (_broken_hero_order(order, mem)
            and kind != 'general_list'
            and not (kind == 'castle_menu' and _hero_source_alternative(order, mem))):
        return _cancel_broken_hero_sortie(mem, order)
    if _retake_reserved(order, mem):
        return _hold_guarded_sortie(mem, order, 'sortie_held_target_reserved',
            '同じ奪還先へ向かう部隊が既にいるため重複出撃を取り消す',
            {'target': order['target']})
    if kind != 'general_list' and _source_spare_missing(order, mem):
        return _hold_guarded_sortie(mem, order, 'sortie_held_source_defender',
            '出撃元の記録で待機将軍を1人残せないため出撃を取り消す',
            {'present': (mem.get('garrison') or {}).get(_source(order, mem))})
    if kind == 'castle_menu':
        checked = _check_source_castle(screen, mem, order)
        if checked is not None:
            return checked
        (mem.get('source_miss') or {}).pop(order['step'], None)
        move = menu_to(screen, 'しゅつげき')
        if move is None:
            # No hand (or unreadable menu): hold with evidence instead of a
            # silent empty plan that never advances and never explains itself.
            return _hold_deploy(screen, mem, order, '出撃メニューのしゅつげきをカーソルで判定できないため保留')
        return _deploy_input(screen, mem, order, [pad('a')] if move == 'here' else [move],
                             '出撃メニューを選択')
    if kind == 'general_list':
        _observe_garrison(screen, mem, order)
        if (mem.get('orders') or {}).get(order['step']) == 'launched_unconfirmed':
            return _verify_sortie_source(screen, mem, order)
        guard = _keep_sortie_defender(screen, mem, order)
        if guard is not None:
            return guard
        guard = _keep_last_castle(screen, mem, order)
        if guard is not None:
            return guard
        if order.get('purpose') == 'move':
            return _move_general_pick(screen, mem, order)
        # A readable list is the selection evidence. Boss orders used to
        # force the chart hero and discard the actual substitute identity.
        # Prefer an idle companion when the planned attacker is the hero;
        # preserve a chart's explicit non-hero role.
        if order['general'] == NAME or _is_boss_order(order, mem):
            mem.setdefault('sortie_general', {}).pop(order['step'], None)
            mem.setdefault('order_context', {}).pop(order['step'], None)
            (mem.get('sortie_actor_miss') or {}).pop(order['step'], None)
            present = _present_generals(screen)
            if (present is None or len(present) != len(set(present))
                    or any(not _general_visible(screen, g) or general_max_hp(
                        'しゅじんこう' if g == NAME else g) is None for g in present)):
                return _hold_deploy(screen, mem, order, '出撃一覧の本人・カーソルを確認できないため保留')
            candidates = _hero_alternatives(mem, present) if order['general'] == NAME else []
            general = candidates[0] if candidates else order['general']
            # A stale override is not a receipt, and a companion selected in
            # this list must not inherit the hero's depleted-egg guard.
            mem.setdefault('general_override', {}).pop(order['step'], None)
            if general != order['general']:
                mem['general_override'][order['step']] = general
            if general not in present or general in _en_route(mem)[0]:
                holds = mem.setdefault('boss_absent', {})
                holds[order['step']] = holds.get(order['step'], 0) + 1
                if holds[order['step']] >= BOSS_ABSENT_LIMIT:
                    holds.pop(order['step'], None)
                    (mem.get('target_cancel') or {}).pop(order['step'], None)
                    _finish_order(mem, 'failed', deviation_reason=f"{general}が出撃元で待機していない",
                                  observed_metric=present[:8], source=_source(order, mem),
                                  reason='出撃できる本人を有限回の一覧読取で確認できず指示を取り消す')
                    return [pad('b'), pad('b')]
                return _hold_deploy(screen, mem, order, '出撃できる本人が一覧にいないため保留')
            if _broken_hero_order(order, mem):
                return _cancel_broken_hero_sortie(mem, order)
            if _boss_egg_depleted(order, mem):
                return _hold_guarded_sortie(mem, order, 'boss_sortie_cancelled_for_egg',
                    '代役がいないため主人公の卵回復を待つ', {'general': NAME, 'egg_uses': mem['egg_uses'][NAME]})
            (mem.get('boss_absent') or {}).pop(order['step'], None)
            move = menu_to(screen, general)
            if move is None:
                return _hold_deploy(screen, mem, order, '選択本人へカーソルを合わせられないため保留')
            if move == 'here':
                mem['sortie_general'][order['step']] = general
                if general != order['general']:
                    _record(mem, 'hero_priority_selected', **_deploy_context(order, mem),
                            observed_metric={'present': present[:8], 'selected': general},
                            reason='主人公の敗北でゲームオーバーになる危険を避け、実在する待機将軍を先発にする')
                return _deploy_input(screen, mem, order, [pad('a')], '実一覧の出撃本人を選択')
            return _deploy_input(screen, mem, order, [move], '主人公以外の待機将軍を優先して選択カーソルを移動')
        empty_list = 'おりません' in screen.text
        if not screen.hand and not empty_list:
            return _hold_deploy(screen, mem, order, '将軍選択カーソルを判定できないため入力を保留')
        general = mem.get('general_override', {}).get(order['step'], order['general'])
        move = menu_to(screen, general) if screen.hand else None
        if move is None:
            # The chart's general is not at this castle (routed or lost).
            # Whoever is actually here goes instead, so the castle, later
            # steps and the boss condition stay reachable.
            # An empty list message is not a general name.
            ui = {'しゅつげき', 'ステータス', 'しょうぐんは', 'おりません', 'おりません……'}
            present = [] if empty_list else [w for x, y, w in _options(screen) if x > 100 and w not in ui
                                             and not re.search(r'\d', w) and 'おりません' not in w]
            present = [g for g in present if g not in _en_route(mem)[0]]
            present.sort(key=lambda w: w == NAME)      # risk the hero last
            if present and not mem.get('general_override', {}).get(order['step']):
                mem.setdefault('general_override', {})[order['step']] = present[0]
                _record(mem, 'order_substitute', strategy_variant='substitute_general',
                        deviation_reason=f"{order['general']}が出撃元にいないため{present[0]}が代わりに出撃",
                        observed_metric=present[:8], expected_metric=f"{order['target']}の占領",
                        reason='後続手順とボス条件を満たすため')
                move = menu_to(screen, present[0])
                return [pad('a')] if move == 'here' else [move] if move else []
            if (not mem.get('source_override', {}).get(order['step'])
                    and chart.home_castle(mem.get('chapter') or 1) in _owned(mem)):
                mem.setdefault('source_override', {})[order['step']] = chart.home_castle(
                    mem.get('chapter') or 1)
                mem.setdefault('general_override', {}).pop(order['step'], None)
                mem['orders'][order['step']] = 'pending'
                _record(mem, 'order_source_changed', strategy_variant='source_fallback',
                        deviation_reason=f"{order['source']}に出撃できる将軍がいない",
                        observed_metric=present[:8], reason='本城から出撃し直す')
                mem['active'] = None
                return [pad('b'), pad('b')]
        if move is None:
            _finish_order(mem, 'failed', deviation_reason=f"{order['general']}が出撃一覧に見えない",
                          observed_metric=[w for _, _, w in _options(screen)][:12],
                          reason='チャートの将軍が出撃元の城にいない')
            return [pad('b')]
        return _deploy_input(screen, mem, order, [pad('a')] if move == 'here' else [move], '出撃将軍を選択')
    if kind in {'card_select', 'sortie_confirm'}:
        guard = _selected_actor_guard(screen, mem, order)
        if guard is not None:
            return guard
    if kind in {'card_select', 'sortie_confirm'} and _boss_egg_depleted(order, mem):
        # observe_events reads the egg row before this decision. Cancel a
        # sortie that was chosen while its quantity was still unknown.
        mem.setdefault('orders', {})[order['step']] = 'pending'
        mem['active'] = None
        mem['picked'] = []
        mem.setdefault('order_context', {}).pop(order['step'], None)
        _record(mem, 'boss_sortie_cancelled_for_egg', chart_step=order['step'],
                observed_metric={'general': NAME, 'egg_uses': mem['egg_uses'][NAME],
                                 'screen': kind}, expected_metric={'egg_uses': 4},
                reason='出撃画面で主人公の卵の消耗を確認したため、ボス出撃を取り消して回復を待つ')
        return [pad('b'), pad('b')]
    if kind in {'card_select', 'sortie_confirm'} and _is_boss_order(order, mem):
        mem.setdefault('order_context', {}).pop(order['step'], None)
        if not (mem.get('sortie_general') or {}).get(order['step']):
            return _hold_deploy(screen, mem, order, 'ボス出撃の選択本人を確認できないため保留')
    if kind == 'card_select':
        mem.setdefault('order_context', {}).pop(order['step'], None)
        picked = mem.setdefault('picked', [])
        wanted = _deploy_cards(order, mem)
        for card in picked:
            if card in wanted:
                wanted.remove(card)
        card = wanted[0] if wanted else None
        inventory = _measured_card_select(screen)
        if inventory is None:
            if not _strict_boss_cards(order, mem):
                # An unreadable panel must not hold the sortie forever (g454
                # 10:02: an unknown card name stalled シェーブル's sortie). The
                # base boss kit keeps its strict hold.
                misses = mem.setdefault('card_unreadable', {})
                misses[order['step']] = misses.get(order['step'], 0) + 1
                if misses[order['step']] >= CARD_UNREADABLE_LIMIT:
                    misses.pop(order['step'], None)
                    _finish_order(mem, 'failed', deviation_reason='切り札一覧を実測構造として読めない',
                                  observed_metric={'screen': screen.kind,
                                                   'readings': CARD_UNREADABLE_LIMIT},
                                  reason='切り札一覧を読み取れないため出撃を取り消して次の指示へ進む')
                    return [pad('b'), pad('b')]
            return _hold_deploy(screen, mem, order,
                                '切り札一覧の名前・数量・配置またはカーソルが実測構造と一致しないため保留', card=card)
        mem.get('card_unreadable', {}).pop(order['step'], None)
        _observe_card_stock(mem, inventory)
        rare_actions = _rare_card_inventory(screen, mem, order, inventory)
        if rare_actions is not None:
            return rare_actions
        if _strong_card_inventory(mem, order, inventory, wanted):
            # 携行枠に1枚追加した: この観測の選択対象も並べ直す(予定札が先頭)。
            wanted = _deploy_cards(order, mem)
            for card in picked:
                if card in wanted:
                    wanted.remove(card)
        strong = (mem.get('strong_card_kit') or {}).get(order['step'])
        if not wanted:
            return _deploy_input(screen, mem, order, [pad('b')], '予定切り札の選択入力後に携行確認へ進む')
        # Pick what is on screen first: a full 4-row panel scrolls, and a card
        # below it (g421 13:27: クースカン, bought x4) is reached by scrolling.
        # 予定札(チャート/調整)が残っている間は、追加携行の強い切り札を先に選ばない:
        # 空き枠に足した札が予定札より先に消費されるのを避ける。
        planned = [c for c in wanted if c != strong] or wanted
        shown = {r['card'] for r in inventory['rows'] if r['stock'] > 0}
        card = next((c for c in planned if c in shown), planned[0])
        row = next((row for row in inventory['rows'] if row['card'] == card), None)
        if (row is None and len(inventory['rows']) == 4 and inventory['remaining'] > 0
                and int((mem.get('card_scroll') or {}).get(order['step'], 0)) < (RARE_SCAN_LIMIT if (mem.get('rare_card_kit') or {}).get(order['step'])
                   else CARD_SCROLL_LIMIT)):
            scrolls = mem.setdefault('card_scroll', {})
            scrolls[order['step']] = scrolls.get(order['step'], 0) + 1
            _record(mem, 'card_scroll', **_deploy_context(order, mem), card=card,
                    observed_metric={'rows': [[r['card'], r['stock']] for r in inventory['rows']],
                                     'selected_y': inventory['selected_y'], 'scrolls': scrolls[order['step']]},
                    reason='予定切り札が一覧に見えないため下へ送って隠れた行を表示する')
            direction = ('up' if CARD_IDS.get(card, 99) <
                         min(CARD_IDS[r['card']] for r in inventory['rows']) else 'down')
            return [pad(direction)]
        if row is None or row['stock'] == 0 or inventory['remaining'] == 0:
            if not _strict_boss_cards(order, mem):
                dropped = _drop_card(screen, mem, order, card, inventory)
                if dropped is not None:
                    return dropped
            return _hold_deploy(screen, mem, order,
                                '予定切り札の正数在庫と携行余枠を確認できないため選択せず保留', card=card)
        if inventory['selected_y'] == row['y']:
            picked.append(card)
            _record(mem, 'card_pick', **_deploy_context(order, mem), card=card,
                    observed_metric={'stock': row['stock'], 'remaining': inventory['remaining'],
                                     'inventory_evidence': inventory['inventory_evidence'],
                                 'observed_rows': inventory['observed_rows']},
                    resulting_event='selection_planned_not_yet_confirmed', reason=order['note'])
            return [pad('a')]
        move = pad('down' if row['y'] > inventory['selected_y'] else 'up')
        selected = next(item for item in inventory['rows'] if item['y'] == inventory['selected_y'])
        _record(mem, 'sortie_input',
                **_deploy_context(order, mem, expected_metric={'card': card, 'goal': '予定切り札へカーソルを合わせる'}),
                screen=screen.kind, card=card,
                observed_metric={'desired_card': card, 'selected_y': selected['y'],
                                 'selected_card': selected['card'], 'target_y': row['y'],
                                 'target_stock': row['stock'], 'remaining': inventory['remaining'],
                                 'inventory_evidence': inventory['inventory_evidence'],
                                 'observed_rows': inventory['observed_rows']},
                reason='実測配置の正数在庫と携行余枠を確認し、予定切り札へカーソルを移動')
        return [move]
    if kind == 'sortie_confirm':
        mem.setdefault('order_context', {}).pop(order['step'], None)
        empty_receipt = _empty_sortie_inventory(screen)
        slot_receipt = _sortie_inventory(screen)
        carried = [] if empty_receipt else slot_receipt
        inventory_evidence = ('measured_empty_sortie' if empty_receipt else
                              'measured_single_card_sortie' if slot_receipt and len(slot_receipt) == 1 else
                              'structured_card_slots')
        ambiguous = carried is None
        want = sorted(_deploy_cards(order, mem))
        got = sorted(carried) if carried is not None else None
        if ambiguous or got != want:
            misses = mem.setdefault('sortie_confirm_miss', {})
            misses[order['step']] = misses.get(order['step'], 0) + 1
            if (not _strict_boss_cards(order, mem)
                    and misses[order['step']] >= SORTIE_CONFIRM_LIMIT):
                if carried is None:
                    misses.pop(order['step'], None)
                    _finish_order(mem, 'failed', deviation_reason='出撃確認の携行札を読めない',
                                  observed_metric={'carried': None},
                                  reason='出撃確認を読み取れないため出撃を取り消して次の指示へ進む')
                    return [pad('b'), pad('b')]
                if misses[order['step']] == SORTIE_CONFIRM_LIMIT:
                    # A readable panel is the truth: approve the shown kit.
                    _record(mem, 'sortie_kit_mismatch',
                            **_deploy_context(order, mem, expected_metric=want),
                            carried=carried,
                            observed_metric={'carried': carried, 'planned': want},
                            reason='読み取れた携行札を実際の携行として出撃を承認（計画と不一致）')
            else:
                return _hold_deploy(screen, mem, order,
                                    '携行切り札の読取が不確実または計画と不一致のため出撃承認を保留', carried=carried)
        else:
            mem.get('sortie_confirm_miss', {}).pop(order['step'], None)
        move = menu_to(screen, 'うむッ!')
        if move is None:
            return _hold_deploy(screen, mem, order, '出撃確認カーソルまたは承認項目を読めないため保留', carried=carried)
        if move == 'here':
            context = _deploy_context(order, mem, expected_metric=want)
            mem['order_context'][order['step']] = {
                'strategy_variant': context['strategy_variant'],
                'deviation_reason': context['deviation_reason'],
                'actual_general': context['general'], 'planned_general': order['general'],
                'expected_metric': (context['expected_metric'] if (mem.get('retry_context') or {}).get(order['step'])
                                    else {'general': order['general'], 'cards': want}),
                'observed_metric': {'general': context['general'], 'cards': got}}
            _record(mem, 'sortie_confirm', **context, cards=carried,
                    inventory_evidence=inventory_evidence,
                    observed_metric=got,
                    reason='出撃確認で読み取った切り札が計画と一致したため承認を予定')
            source = mem.get('source_override', {}).get(order['step'], order['source'])
            mem['sortie_attempt'] = {'step': order['step'], 'target_seen': False}
            mem['cursor'] = list(chart.castles(mem['chapter'])[source])
            mem['uncertain'] = False      # the target marker starts on the source castle
            mem.pop('nav_search', None)
            mem.pop('nav_search_leg', None)
            return [pad('a')]
        return _deploy_input(screen, mem, order, [move], '携行品一致を確認したため出撃承認項目へ移動')
    return []


# ---------------------------------------------------------------- battles
def _match_sortie(mem, castle, general):
    """The execution id of the sortie a measured entry message belongs to.

    Returns ``(step, status)``: exactly one en-route sortie of this general to
    this castle is ``matched``; several are ``ambiguous`` (never guessed);
    none falls back to the legacy per-castle record.
    """
    sorties = mem.get('sorties') or {}
    candidates = [step for step, sortie in sorties.items()
                  if sortie.get('status') in ('en_route', 'launched_unconfirmed')
                  and sortie.get('target') in (None, castle)
                  and sortie.get('general') == general]
    if len(candidates) == 1:
        return candidates[0], 'matched'
    if candidates:
        return None, 'ambiguous'
    launched = (mem.get('launched') or {}).get(castle) or {}
    if launched.get('general') == general and launched.get('step') not in sorties:
        return launched.get('step'), 'legacy'
    return None, 'none'


def _bind_sortie(mem, step, castle):
    sortie = (mem.get('sorties') or {}).get(step)
    if sortie:
        if sortie.get('target') is None:
            _record(mem, 'sortie_arrival_confirmed', chart_step=step, general=sortie['general'],
                    castle=castle, reason='将軍名の一致する入城表示で未確定だった出撃と行先を確定')
            _garrison_move(mem, sortie['general'], source=sortie.get('source'))
            mem.setdefault('orders', {})[step] = 'launched'
            if mem.get('active') == step:
                mem['active'] = None
            if (mem.get('sortie_attempt') or {}).get('step') == step:
                mem.pop('sortie_attempt', None)
        sortie.update(status='arrived', target=castle)


def _sortie_step_for(mem, general, castle):
    """The newest sortie of this general already bound to this castle.

    An ambiguous boss entry (g460 17:32: the base 1-B1 and the adjusted
    A:bd2304e3:K1 were both still marching on けっかい) must not bind an order
    id that was never read. The battle still has to fight with the kit of the
    sortie that is actually out there: with an unknown step every charted
    tactic is filtered out and the hero swings bare-handed (g460 17:32:
    planned_cards=[] -> ally HP 88..0 -> 17:35 game over)."""
    if not general or not castle:
        return None
    march = [(sortie.get('tick') or 0, step)
             for step, sortie in (mem.get('sorties') or {}).items()
             if sortie.get('general') == general
             and sortie.get('target') == castle
             and sortie.get('status') in ('en_route', 'arrived')]
    return max(march)[1] if march else None


ENTRY_RETURN_KINDS = frozenset({'main_menu', 'castle_info', 'castle_menu',
    'general_list', 'card_select', 'sortie_confirm', 'month_menu', 'shop_list',
    'shop_quantity_prompt', 'shop_quantity', 'shop_exit_confirm', 'discharge_menu'})


def observe_entry_return(screen, mem):
    """A measured management menu ends an entry that never reached combat.

    A field/map frame can flash before combat, so it is not a receipt. Keep
    active battles and their pending first panel until battle_end finishes.
    Never manufacture a loss, death, or castle owner from a skipped entry.
    """
    entry = mem.get('attack')
    if not isinstance(entry, dict) or not entry or mem.get('battle'):
        return
    if screen.kind not in ENTRY_RETURN_KINDS:
        return
    _record(mem, 'battle_entry_expired', castle=entry.get('castle'),
            side=entry.get('side'), general=entry.get('general'), screen=screen.kind,
            resulting_event='unclassified_entry_closed',
            reason='戦闘未開始のまま管理画面へ戻ったため古い城情報を解除')
    mem['attack'] = None
    mem.pop('battle_seen', None)


def _battle_context(mem, ally):
    """Only a matching attack message establishes a battle location and side."""
    attack = mem.get('attack') or {}
    captured = set(mem.get('captured', []))
    if attack.get('side') == 'defense' or (attack.get('general') and attack.get('general') == ally):
        side = attack.get('side')
        if side == 'attack' and attack.get('castle') in captured:
            side = 'defense'          # a battle at a castle we hold is not a capture attempt
        step = attack.get('step')
        if step is None and side == 'attack':
            step = _sortie_step_for(mem, ally, attack.get('castle'))
        return {'castle': attack.get('castle'), 'side': side, 'step': step,
                'entry_evidence': attack.get('entry_evidence')}
    captured = set(mem.get('captured', []))
    en_route = [step for step, sortie in (mem.get('sorties') or {}).items()
                if sortie.get('general') == ally
                and sortie.get('status') in ('en_route', 'arrived', 'launched_unconfirmed')
                and sortie.get('target') not in captured]
    if len(en_route) == 1:
        return {'castle': None, 'side': None, 'step': en_route[0],
                'context': 'unclassified_location'}
    if en_route:
        return {'castle': None, 'side': None, 'step': None, 'context': 'ambiguous_sortie'}
    for castle, info in (mem.get('launched') or {}).items():
        if info.get('general') == ally and castle not in captured:
            return {'castle': None, 'side': None, 'step': info.get('step'),
                    'context': 'unclassified_location'}
    return {'castle': None, 'side': None, 'step': None, 'context': 'unclassified'}


def _boss_tactics_allowed(mem, cur):
    """Gate carried boss cards on entry text or a measured chapter-boss name.

    A boss dialogue may hide/skip the entry message (g484: Venus vs Queen).
    Reading the boss in the battle panel authorizes its carried tactics, but
    does not establish castle ownership, arrival, or a retry context.
    """
    if cur.get('entry_evidence') == 'measured_boss_entry':
        return True
    chapter = mem.get('chapter')
    boss = chart.BOSSES.get(chapter)
    return bool(boss and cur.get('enemy') == boss
                and _is_boss_order(_order_for_step(mem, cur.get('step')), mem))


def _battle_labels(cur):
    return {'chart_step': cur.get('step'),
            'strategy_variant': cur.get('strategy_variant', 'chart'),
            'deviation_reason': cur.get('deviation_reason')}


def _bind_battle_strategy(mem, cur):
    if 'strategy_variant' in cur:
        return
    step = cur.get('step')
    retry = (mem.get('retry_context') or {}).get(step)
    sortie = (mem.get('order_context') or {}).get(step) or {}
    if sortie.get('actual_general') != cur.get('ally'):
        sortie = {}
    if retry or (mem.get('card_override') or {}).get(step):
        cur['strategy_variant'] = (retry or {}).get('strategy_variant', 'retry_with_opening_cards')
        cur['deviation_reason'] = (retry or {}).get('deviation_reason') or '保存済みの再攻撃用切り札計画を適用（元の敗北詳細は未分類）'
        cur['strategy_expected'] = (retry or {}).get('expected_metric') or {'goal': '敵HP減少と戦闘勝利'}
    elif sortie:
        cur['strategy_variant'] = sortie.get('strategy_variant', 'chart')
        cur['deviation_reason'] = sortie.get('deviation_reason')
        cur['strategy_expected'] = sortie.get('expected_metric')
    else:
        cur['strategy_variant'] = mem.get('variant', 'chart')
        cur['deviation_reason'] = None
        cur['strategy_expected'] = {'goal': 'チャート戦術による戦闘勝利'}


def _migrate_card_evidence(mem):
    """Legacy cards_used mixed selections/missing cards with actual use."""
    for scope in ('stats', 'previous_stats'):
        stats = mem.get(scope)
        if not isinstance(stats, dict) or stats.get('card_evidence_version') == 1:
            continue
        had_previous = 'cards_used' in stats
        previous = stats.get('cards_used')
        stats.update(cards_used=None, cards_confirmed=0, card_evidence_version=1)
        if had_previous:
            _record(mem, 'metric_invalidated', metric='cards_used', metric_scope=scope,
                    observed_metric={'previous_inferred_count': previous if type(previous) is int else None,
                                     'cards_used': None},
                    reason='旧切り札集計は予定・欠品を含むため未分類化。以後は実使用証拠のみ別途加算')
    cur = mem.get('battle')
    if not isinstance(cur, dict):
        return
    _bind_battle_strategy(mem, cur)
    if cur.get('card_evidence_version') == 1:
        return
    legacy = list(cur.get('cards_used') or [])
    cur.update(cards_used=[], cards_selected=[], cards_missing=[],
               cards_unclassified=legacy, card_consumption_complete=False, card_evidence_version=1)
    flow = cur.get('card_flow')
    if flow and flow.get('stage') == 'announce':
        pending = flow.get('card')
        if pending and pending not in legacy:
            cur['cards_unclassified'].append(pending)
        cur['card_flow'] = None
    _record(mem, 'battle_card_evidence_migrated', **_battle_labels(cur),
            observed_metric={'legacy_cards_unclassified': legacy},
            reason='旧戦闘の選択済みリストは実使用証拠でないためafter_cardを解禁しない')


def _card_use_unclassified(mem, cur, reason):
    flow = cur.get('card_flow') or {}
    card = flow.get('card')
    selected = (flow.get('selection_planned') is True
                or (flow.get('stage') == 'announce'
                    and card in cur.get('cards_selected', [])))
    if not selected:
        # g496: the B/menu opening expired before any list selection, but the
        # old path consumed an イッテツーン and counted it for after_card.
        # Preserve the carried kit and retry only this tactic, boundedly.
        tid = flow.get('tactic_id')
        done = cur.get('tactics_done', [])
        if tid is None and card:
            matches = [item for item in done if item.endswith(':' + card)]
            if len(matches) == 1:
                tid = matches[0]  # a flow opened by the previous bot version
        failures = cur.setdefault('card_open_failures', {})
        count = int(failures.get(tid, 0)) + 1 if tid else CARD_OPEN_ATTEMPTS
        if tid:
            failures[tid] = count
            if count < CARD_OPEN_ATTEMPTS and tid in done:
                done.remove(tid)
        _record(mem, 'battle_card_open_unclassified', **_battle_labels(cur), card=card,
                expected_metric='実メニューと札一覧の選択',
                observed_metric={'selection_planned': False, 'attempts': count,
                                 'retry_allowed': bool(tid and count < CARD_OPEN_ATTEMPTS)},
                reason='札の選択前に操作が戻ったため携行札を差し引かず、有限回だけ再試行')
        cur['card_flow'] = None
        return
    if card:
        cur.setdefault('cards_unclassified', []).append(card)
        # A selected card with no calibrated receipt still leaves the kit for
        # this sortie: re-planning it would open an empty list next battle.
        step = cur.get('step')
        if step:
            spent = mem.setdefault('kit_spent', {}).setdefault(step, [])
            spent.append(card)
    cur['card_consumption_complete'] = False
    if not cur.get('deviation_reason'):
        cur['strategy_variant'] = 'card_use_unclassified'
        cur['deviation_reason'] = reason
    _record(mem, 'battle_card_unclassified', **_battle_labels(cur), card=card,
            expected_metric='選択した切り札の実使用告知',
            observed_metric={'confirmation': 'unclassified'}, reason=reason)
    cur['card_flow'] = None


def reset_battle_controls(mem):
    """Discard controls belonging to the previous combatant."""
    mem['egg_battle'] = False
    mem.pop('battle_menu_pending_ticks', None)
    for key in ('egg_action', 'egg_key', 'egg_menu_stage', 'egg_battle_row_dead',
                'egg_retreat_tried', 'egg_retreat_flow', 'egg_choice', 'egg_row_dead',
                'indep_menu', 'indep_menu_key', 'indep_menu_action',
                'monster_menu_key', 'monster_menu_cursor', 'monster_menu_hold',
                'monster_menu_choice', 'monster_menu_choice_key', 'monster_panel'):
        mem.pop(key, None)


def _defender_successor(mem, cur, b):
    """Two stable living-general panels can continue a known castle defense."""
    maximum = general_max_hp('しゅじんこう' if b.ally == NAME else b.ally)
    eligible = (cur.get('side') == 'defense' and bool(cur.get('castle'))
                and cur.get('ally_hp') == 0 and (cur.get('enemy_hp') or 0) > 0
                and b.enemy == cur.get('enemy') and b.ally != cur.get('ally')
                and maximum is not None and 0 < b.ally_hp <= maximum and b.enemy_hp > 0)
    reading = [b.enemy, b.ally]
    if not eligible:
        cur.pop('successor_seen', None)
        return False
    if cur.get('successor_seen') != reading:
        cur['successor_seen'] = reading
        return False
    context = {'castle': cur['castle'], 'side': 'defense', 'step': None,
               'entry_evidence': cur.get('entry_evidence')}
    predecessor = cur['ally']
    battle_end(mem, 'defender_successor', defense_continues=True)
    reset_battle_controls(mem)
    mem['attack'] = context
    mem['battle_seen'] = reading
    _record(mem, 'defender_successor', ally=b.ally, enemy=b.enemy,
            castle=context['castle'], previous_ally=predecessor,
            observed_metric={'ally_hp': b.ally_hp, 'enemy_hp': b.enemy_hp},
            reason='同じ敵と戦う次の守備将軍を連続した完全パネルで確認')
    return True


def _entered_battle_successor(mem, cur, b):
    """A measured new entry and two complete panels release a finished win.

    g510: only one attack-entry frame separated Gasupacho/Cinnamon from
    the hero/Venus. The old zero-enemy-HP record then blocked every input.
    Never infer a transition from changed names alone or from a living enemy.
    """
    entry = mem.get('attack')
    maximum = general_max_hp('しゅじんこう' if b.ally == NAME else b.ally)
    eligible = (isinstance(entry, dict) and entry.get('side') == 'attack'
                and entry.get('general') == b.ally and b.ally != cur.get('ally')
                and entry.get('castle') in chart.castles(mem.get('chapter') or 0)
                and type(cur.get('enemy_hp')) is int and cur['enemy_hp'] == 0
                and type(cur.get('ally_hp')) is int and cur['ally_hp'] > 0
                and type(cur.get('away')) is int and cur['away'] >= 1
                and maximum is not None and 0 < b.ally_hp <= maximum and b.enemy_hp > 0)
    reading = [b.enemy, b.ally]
    if not eligible:
        cur.pop('entry_successor_seen', None)
        return False
    if cur.get('entry_successor_seen') != reading:
        cur['entry_successor_seen'] = reading
        return False
    context = dict(entry)
    previous_ally, previous_enemy = cur['ally'], cur['enemy']
    battle_end(mem, 'confirmed_new_entry')
    reset_battle_controls(mem)
    mem['attack'] = context
    mem['battle_seen'] = reading
    _record(mem, 'battle_entry_successor', ally=b.ally, enemy=b.enemy,
            castle=context['castle'], previous_ally=previous_ally, previous_enemy=previous_enemy,
            observed_metric={'ally_hp': b.ally_hp, 'enemy_hp': b.enemy_hp},
            reason='前戦の敵HP0・新しい突入記録・連続した完全パネルから次の戦闘へ移行')
    return True


def _strong_cards_carried(mem, cur):
    """この戦闘で実際に携行している強い切り札(消費・破棄済みは除く)。

    計画は携行実績ではない。出撃確認の読めた札一覧だけを正本にし、
    この出撃で消費済みのコピーを差し引いてから戦術にする。
    """
    step = cur.get('step')
    kit = (mem.get('strong_card_kit') or {}).get(step)
    if not kit or not step:
        return []
    context = (mem.get('order_context') or {}).get(step) or {}
    observed = (context.get('observed_metric') or {}).get('cards')
    if not isinstance(observed, list):
        return []
    carried = list(observed)
    for card in (mem.get('kit_spent') or {}).get(step) or ():
        if card in carried:
            carried.remove(card)
    return [kit] if kit in carried else []


def _strong_enemy(mem, cur):
    """この敵が「強い将軍」か (gcgx shogun.html の戦闘・最大HPを主判定)。

    返り値は (verdict, evidence)。ボスは後期ボスが表 (SFC ID 0..127) に載らない
    ため、表の値に関わらず強い将軍とみなす。判定不能 (表に無い) は False に
    正規化し、積極利用は True の時だけ許可する。
    """
    enemy, ally = cur.get('enemy'), cur.get('ally')
    if ally == NAME:
        ally = 'しゅじんこう'
    verdict, evidence = reference.strong_general(enemy, ally)
    if enemy and enemy in chart.BOSSES.values():
        return True, {**evidence, 'rule': 'boss'}
    return verdict is True, evidence


def _strong_card_tactics(mem, cur, enemy):
    """携行した強い切り札を開幕に使う戦術 (実行は強い将軍と判定された時だけ)。"""
    return [{'enemy': enemy, 'card': card, 'open': True, 'step': cur.get('step'),
             'tactic_id': f"strong:{cur.get('step')}:{card}", 'strong_only': True,
             'note': '出撃時に携行した強い切り札を強い将軍へ開幕で使う'}
            for card in _strong_cards_carried(mem, cur)]


def battle_step(screen: Screen, mem):
    b = screen.battle
    if (b is None or not b.enemy or not b.ally or UNKNOWN in b.enemy or UNKNOWN in b.ally
            or any(type(hp) is not int or hp < 0 for hp in (b.enemy_hp, b.ally_hp))):
        mem['battle_seen'] = None
        if mem.get('battle'):
            mem['battle'].pop('successor_seen', None)
            mem['battle'].pop('entry_successor_seen', None)
        return []  # partial panel must not replace the last clear HP/context
    _migrate_card_evidence(mem)
    cur = mem.get('battle')
    if cur:
        cur.pop('defeat_owner', None)  # a later complete combat panel invalidates the post-combat flag
    if cur and (b.enemy, b.ally) != (cur['enemy'], cur['ally']):
        if not (_entered_battle_successor(mem, cur, b) or _defender_successor(mem, cur, b)):
            return []
        cur = None
    elif cur:
        cur.pop('successor_seen', None)
        cur.pop('entry_successor_seen', None)
    if cur is None:
        # Fades dim the panel and can drop dakuten; open a battle record only
        # after two consecutive identical readings of both names.
        reading = [b.enemy, b.ally]
        if mem.get('battle_seen') != reading:
            mem['battle_seen'] = reading
            return []
        mem['battle_seen'] = None
        # The panel's opponent is a chapter fact: a general whose debut
        # chapter is later than ours proves the game advanced (g454: ピオーネ/
        # ヘラ after the chapter 1 boss). This resets the chapter state, so it
        # runs before the new battle record is built.
        observe_chapter_general(mem, b.enemy)
        context = _battle_context(mem, b.ally)
        cur = mem['battle'] = {'enemy': b.enemy, 'ally': b.ally, 'start_enemy_hp': b.enemy_hp,
                               'start_ally_hp': b.ally_hp, 'cards_used': [], 'plan': None,
                               'cards_selected': [], 'cards_missing': [], 'cards_unclassified': [],
                               'card_consumption_complete': True, 'card_evidence_version': 1,
                               **context}
        if b.ally == NAME and type(b.ally_hp) is int:
            # The hero's full strength, remembered across battles: a hero who
            # starts a fight already weak never looks "far down" against his
            # own start HP (g436 23:17: 14 HP after a road battle, then died).
            mem['hero_max_hp'] = max(int(mem.get('hero_max_hp') or 0), b.ally_hp)
            cur['ref_ally_hp'] = mem['hero_max_hp']
        _bind_battle_strategy(mem, cur)
        if cur.get('step') and not (mem.get('attack') or {}).get('step'):
            # The order id stayed unknown (ambiguous entry): only the marching
            # sortie's kit is bound, and the record says so.
            _record(mem, 'battle_step_resolved', **_battle_labels(cur), enemy=b.enemy, ally=b.ally,
                    castle=cur.get('castle'), side=cur.get('side'),
                    observed_metric={'sortie_step': cur['step']},
                    reason='出撃注文を特定できない突入のため、同じ将軍の進軍中出撃の携行札だけを結び付け')
        planned = [t['card'] for t in _tactics(mem, cur['step'])
                   if t['enemy'] in (None, b.enemy) and t.get('step') in (None, cur['step'])
                   and not (t.get('boss_only')
                            and not _boss_tactics_allowed(mem, cur))]
        planned += list(mem.get('card_override', {}).get(cur['step']) or [])
        cur['planned_cards'] = list(planned)
        _record(mem, 'battle_start', **_battle_labels(cur), enemy=b.enemy, ally=b.ally,
                expected_metric=cur['strategy_expected'],
                observed_metric={'enemy_hp': b.enemy_hp, 'ally_hp': b.ally_hp},
                planned_cards=planned, context=cur.get('context', 'message'),
                enemy_hp=b.enemy_hp, ally_hp=b.ally_hp, castle=cur['castle'],
                reason='戦闘パネルの将軍名とHPを確認')
        _tally(mem, 'battles_started')
        # 将軍 (最大HP) と携行切り札 (卵落・ID合計) の組み合わせで、この戦闘の
        # 「卵を使わせない／落とさせる」方針を先に決めて記録する。卵を使える能力が
        # ない・不明な敵では方針もないので記録しない (卵の有無自体は battle_melee の
        # egg_risk_flags が毎回残す)。
        egg_plan = _egg_plan(mem, cur)
        if egg_plan['threat']:
            _record(mem, 'battle_egg_plan', **_battle_labels(cur), enemy=b.enemy, ally=b.ally,
                    expected_metric='携行ID47以下による開幕卵の抑止と、卵落札での卵の落下',
                    observed_metric=egg_plan,
                    reason='将軍の最大HPと携行切り札の卵落・ID合計から卵ディニアル方針を決定')
    if (b.enemy, b.ally) != (cur['enemy'], cur['ally']):
        return []            # faded/partial panel: keep the last clear reading
    cur['away'] = 0
    cur.pop('okunote_flow', None)  # the command finished; a later use is a new attempt
    cur['enemy_hp'], cur['ally_hp'] = b.enemy_hp, b.ally_hp
    # フィールドの兵士スプライト (味方が左・敵が右)。召喚戦の独立判断が
    # HPだけでなく兵士数も見るため、直近の白兵フレームの読み取りを保持する。
    # 読めない側は None のまま渡し、判定側で HP のみへ退ける。
    if getattr(screen, 'field_soldiers', None) is not None:
        cur['ally_soldiers'], cur['enemy_soldiers'] = screen.field_soldiers
    if b.enemy_hp is not None and cur.get('start_enemy_hp') is not None and b.enemy_hp < cur['start_enemy_hp']:
        cur['clashed'] = True
    if (_boss_tactics_allowed(mem, cur) and type(b.ally_hp) is int
            and type(cur.get('start_ally_hp')) is int and b.ally_hp < cur['start_ally_hp']):
        # The first clash can hurt only our general (g506 Queen 70 stays
        # unchanged while hero 66->55). Do not wait for an enemy HP drop
        # before starting the carried post-clash boss sequence.
        cur['clashed'] = True
    if b.enemy_hp == 0 or b.ally_hp == 0:
        if (cur.get('card_flow') or {}).get('stage') == 'menu':
            # A panel at zero is the end of the fight: drop the opening card
            # that has not reached the command menu yet instead of pushing B
            # after a resolution the chart cannot follow up on.
            cur['card_flow'] = None
        return []
    # 卵落札の選択後に敵HPが下がり、まだ生き残っているなら敵は卵を落とした。
    # 以後の召喚不能として扱ってもよい（落としていない卵は無いと言い切らない）。
    _egg_drop_confirm(mem, cur)
    retreat = _hero_retreat_open(mem, cur)
    if retreat is not None:
        return retreat
    if cur.get('card_flow'):
        flow = cur['card_flow']
        opening_hp = flow.get('enemy_hp_at_open')
        # g484: Queen 68->34 after selected クースカン, but two idle
        # observations before reopening B let the Queen summon. A measured
        # HP drop after selection is enough to continue an existing chain;
        # the uncalibrated consumption receipt remains unclassified.
        fast_chain = (flow.get('stage') == 'announce'
                      and flow.get('card') in cur.get('cards_selected', [])
                      and type(opening_hp) is int and type(b.enemy_hp) is int
                      and b.enemy_hp < opening_hp
                      and _boss_tactics_allowed(mem, cur)
                      and any(t.get('after_card') == flow.get('card')
                              and t.get('enemy') in (None, b.enemy)
                              and t.get('step') in (None, cur.get('step'))
                              for t in _tactics(mem, cur.get('step'))))
        if fast_chain:
            _card_use_unclassified(mem, cur, '選択後の敵HP低下と白兵復帰を実測したため、使用告知は未確認のまま後続札へ進む')
        else:
            flow['battle_frames_without_receipt'] = flow.get('battle_frames_without_receipt', 0) + 1
            waited = flow['battle_frames_without_receipt']
            opening = flow.get('stage') == 'menu'
            if opening and waited <= CARD_MENU_OPEN_RETRIES:
                return [pad('b')]
            if waited >= (CARD_MENU_OPEN_RETRIES + 1 if opening else 2):
                _card_use_unclassified(mem, cur, '実使用告知を確認できないまま白兵戦へ復帰')
            return []
    extra = [{'enemy': b.enemy, 'card': card, 'open': True, 'step': cur.get('step'),
              'note': '再攻撃の開幕切り札(チャート逸脱)'}
             for card in ([] if (mem.get('rare_card_kit') or {}).get(cur.get('step')) is not None
                          else mem.get('card_override', {}).get(cur.get('step')) or [])]
    # 卵ディニアル札はチャートの戦術より後ろに置く（チャートが指す札・タイミングを
    # 優先し、チャートが指していない携行札だけを開幕の卵落に使う）。
    egg_plan = _egg_plan(mem, cur)
    tactics = [*extra, *_tactics(mem, cur.get('step')),
               *_egg_drop_tactics(mem, cur, b.enemy, egg_plan),
               *_strong_card_tactics(mem, cur, b.enemy)]
    done = cur.setdefault('tactics_done', [])
    for index, tactic in enumerate(tactics):
        tid = tactic.get('tactic_id') or f"{'x' if index < len(extra) else 'c'}{index}:{tactic['card']}"
        if tactic['enemy'] not in (None, b.enemy) or tid in done:
            continue
        if tactic.get('step') and tactic['step'] != cur.get('step'):
            continue
        if (tactic.get('boss_only')
                and not _boss_tactics_allowed(mem, cur)):
            continue          # do not spend the boss kit in an unmeasured road fight
        strong_evidence = None
        if tactic.get('strong_only'):
            strong, strong_evidence = _strong_enemy(mem, cur)
            if not strong:
                # 追加携行した切り札は、強い将軍と判定された戦闘以外では温存する。
                continue
        # No calibrated use receipt exists, so a selected-then-unclassified card
        # is the best evidence there is. Without chaining on it the charted boss
        # strategy never fires its second card (1-B1: クースカン→ノリウツール)
        # and the queen summons. The chain is recorded as a deviation.
        attempted = [*cur['cards_used'], *[card for card in (cur.get('cards_unclassified') or [])
                                          if card in cur.get('cards_selected', [])]]
        due = (tactic.get('open')
               or (tactic.get('when_hp_at_most') is not None and b.enemy_hp is not None
                   and b.enemy_hp <= tactic['when_hp_at_most'])
               or (tactic.get('after_clash') and cur.get('clashed'))
               or (tactic.get('after_card') and tactic['after_card'] in attempted))
        if due:
            done.append(tid)
            chained_unconfirmed = (tactic.get('after_card')
                                   and tactic['after_card'] not in cur['cards_used']
                                   and tactic['after_card'] in (cur.get('cards_unclassified') or []))
            note = tactic['note']
            if chained_unconfirmed:
                note = f"{note}（前札の実使用告知は未校正のため選択記録で連続使用）"
                if not cur.get('deviation_reason'):
                    cur['strategy_variant'] = 'after_card_unconfirmed'
                    cur['deviation_reason'] = '前の切り札の実使用告知が未校正のため、選択記録を根拠に連続使用を継続'
            cur['card_flow'] = {'card': tactic['card'], 'stage': 'menu', 'note': note,
                                'enemy_hp_at_open': b.enemy_hp, 'tactic_id': tid}
            # 卵落が最大HP合計の余りを超える札は、命中すれば敵の卵を落とす。
            # どの札が落ちさせても同じなので、チャート札も同じ根拠で監視する。
            drop_evidence = _egg_drop_evidence(cur, tactic['card'])
            if drop_evidence and not drop_evidence['drops']:
                drop_evidence = None
            if drop_evidence:
                _watch_egg_drop(cur, tactic['card'], b.enemy_hp, drop_evidence)
            _record(mem, 'battle_card', **_battle_labels(cur), card=tactic['card'], enemy=b.enemy,
                    enemy_hp=b.enemy_hp, ally_hp=b.ally_hp, reason=note,
                    expected_metric='選択後の実使用告知と敵HP減少',
                    observed_metric={'enemy_hp': b.enemy_hp, 'ally_hp': b.ally_hp},
                    **({'strong_enemy': strong_evidence} if strong_evidence is not None else {}),
                    **({'egg_drop': drop_evidence,
                        'egg_plan': {key: egg_plan[key] for key in
                                     ('threat', 'deny_opening_egg', 'id_sum', 'droppers')}}
                       if drop_evidence else {}),
                    resulting_event='card_planned')
            return [pad('b')]
    if (_defender_last_resort(mem, cur)
            and not cur.get('okunote_last_resort_reopened')
            and not cur.get('okunote_last_resort_recorded')
            and (cur.get('survival') or {}).get('exhausted')):
        # An earlier healthy menu may have rejected the lottery. Recheck the
        # live menu once when later complete HP panels prove losing melee.
        cur['okunote_last_resort_reopened'] = True
        rescue = _survival_state(mem, cur)
        rescue['exhausted'] = False
        rescue['pending_opens'] = 0
        rescue['menu_ticks'] = 0
        _record(mem, 'battle_okunote_recheck', **_battle_labels(cur),
                observed_metric={'ally_hp': cur['ally_hp'], 'enemy_hp': cur['enemy_hp']},
                reason='先の使用不能メニューを確認後、押し負けが実測されたため一度だけ再確認')
        return [pad('b')]
    if _survival_needed(cur):
        rescue = _survival_state(mem, cur)
        if (not rescue.get('exhausted') and rescue['opens'] < 12
                and rescue.get('pending_opens', 0) < 3):
            rescue['opens'] += 1
            rescue['pending_opens'] = rescue.get('pending_opens', 0) + 1
            rescue['menu_ticks'] = 0
            return [pad('b')]
    return _melee_step(mem, cur)


CARD_OPEN_ATTEMPTS = 2        # initial chart opening plus one bounded retry
CARD_MENU_OPEN_RETRIES = 4    # B repeats while the command menu has not opened yet
MELEE_HOLD_LIMIT = 8          # egg-safe holds in one fight before melee proceeds
# A hold that bleeds the general past this share of the fight's opening HP
# stops before the count bound (g530 09:52 defense: 90 -> 81 by the 7th hold,
# the count alone let it reach 73, and the forced push that followed fought
# the summoned モーグリ at 73 and died with it at 4 HP; entering with the 8 HP
# the budget saves wins that trade, by a thin margin). The floor keeps a
# small opening HP from tripping on the first scratch.
MELEE_HP_HOLD_DIVISOR = 10
MELEE_HP_HOLD_FLOOR = 3


def _charted_melee(mem, cur) -> bool:
    """The chart needs this fight's melee to progress toward a card.

    Holding input under the clash egg risk never reaches ``after_clash`` or
    the HP gate of a ``when_hp_at_most`` card (g454 08:19: the queen battle
    held, どうし fell to 43 and the rescue used its own egg -> ヒュドラ; g456
    14:13: どうし 90 vs ガルバンゾー 30 held until the フットバース gate at
    enemy HP 24 was unreachable, and the hero died).
    """
    return any((t.get('after_clash') or t.get('when_hp_at_most') is not None)
               and t.get('enemy') in (None, cur.get('enemy'))
               and t.get('step') in (None, cur.get('step'))
               for t in _tactics(mem, cur.get('step')))


def _melee_step(mem, cur):
    side = cur.get('side')
    defense = True if side == 'defense' else False if side == 'attack' else None
    triggers = enemy_egg_triggers(cur.get('enemy'), player_castle_defense=defense)
    charted = _charted_melee(mem, cur)
    holds = int(cur.get('melee_holds') or 0)
    # The count bound cannot see the bleed: hold reads that stay under
    # MELEE_HOLD_LIMIT can still spend most of the fight's opening HP.
    start_hp, hp = cur.get('start_ally_hp'), cur.get('ally_hp')
    if type(start_hp) is int and type(hp) is int and hp > 0:
        hp_budget = max(MELEE_HP_HOLD_FLOOR, start_hp // MELEE_HP_HOLD_DIVISOR)
        hp_bled = start_hp - hp
    else:
        hp_budget = hp_bled = None      # fail closed to the count bound alone
    # Only a hold that is actually in charge can be cut by the budget; an
    # eggless, clash-free or charted melee already mashes regardless.
    hold_cut = (hp_bled is not None and hp_bled >= hp_budget
                and triggers.has_egg is not False and triggers.clash_position is not False
                and not charted)
    forced = (bool(cur.get('melee_forced')) or holds >= MELEE_HOLD_LIMIT
              or hold_cut)
    # An unbounded hold is a passive death: after the bound the melee proceeds
    # for the rest of the fight even at the clash egg risk (the rescue already
    # had its chances).
    # 敵の卵が落ちた（召喚不能）戦闘だけ、激突判定があっても青ゲージの A 連打を
    # 消費して押し込む。卵が落ちていない卵持ち敵では相変わらず保持する。
    egg_dropped = cur.get('enemy_egg_dropped') is True
    safe = (egg_dropped or triggers.has_egg is False or triggers.clash_position is False
            or charted or forced)
    cur['melee_forced'] = forced
    cur['melee_holds'] = 0 if safe else holds + 1
    mode = 'power_mash' if safe else 'egg_safe_hold'
    actions = _power_mash(mem, cur) if safe else []
    # Per returned action batch, not just the first use in a fight. These are
    # requested A frames; the executor's fence/delivery result remains separate.
    _record(mem, 'battle_melee', **_battle_labels(cur),
            enemy=cur.get('enemy'), enemy_hp=cur.get('enemy_hp'), ally_hp=cur.get('ally_hp'),
            egg_risk_flags=asdict(triggers), melee_control_mode=mode,
            enemy_egg_dropped=egg_dropped,
            a_frames_sent=POWER_TAPS * 3 if safe else 0,
            hold_hp_budget=hp_budget, hold_hp_bled=hp_bled,
            reason=('チャートのぶつかり合いに向けて押し込む' if charted
                    else '敵の卵を落として召喚を封じたため、青ゲージをA連打で消費して押し込む'
                    if egg_dropped
                    else '保持中のHP劣化が予算に達したため押し込む' if hold_cut
                    else '卵の激突リスクの保留上限に達したため押し込む' if holds >= MELEE_HOLD_LIMIT
                    else '保持の打ち切り後はこの戦闘を通しで押し込む' if forced
                    else '卵の激突判定なし' if safe
                    else '卵の激突リスクあり・戦線位置未校正のため入力保留'))
    return actions


# Short, released A bursts cover the POWER window without relying on an old
# screenshot to time its leading edge. Reobserve after 400 ms of planned input;
# card/egg menus still use their own policies. Six taps without release waits
# lost the isolated RetroArch tutorial, despite winning a frame-stepped replay.
POWER_TAPS = 4


def _power_mash(mem, cur):
    if not cur.get('power_mash'):
        cur['power_mash'] = True
        _record(mem, 'battle_power', **_battle_labels(cur),
                expected_metric={'taps': POWER_TAPS, 'hold_ms': 50, 'release_ms': 50},
                observed_metric={'enemy_hp': cur.get('enemy_hp'), 'ally_hp': cur.get('ally_hp')},
                reason='白兵のぶつかり合いで押し負けないようA連打で踏ん張る')
    # xdotool's keyup/next keydown can fall between emulator polls. An
    # explicit release is necessary for four distinct presses, not a hold.
    return [action for _ in range(POWER_TAPS)
            for action in (pad('a', 3), {'type': 'wait', 'ms': 50})]


def _behind(cur: dict) -> bool:
    enemy_hp, ally_hp = cur.get('enemy_hp'), cur.get('ally_hp')
    if type(enemy_hp) is not int or type(ally_hp) is not int:
        return False
    return ally_hp < enemy_hp


# Only non-sacrificial cards are eligible for automatic survival use. Rank
# healing first, then control/damage; this is not a guaranteed damage model.
# Effects: https://gcgx.games/hanjuku/kirihuda.html
SURVIVAL_CARDS = ('エンジェリン', 'キャトルミュー', 'ミックミー', 'マグネガキン', 'クースカン',
                  'グリンボー', 'ゼンマイン', 'ブラッキー', 'ファイアーボイス',
                  'ハリケーン', 'ブンシーン', 'ピッグローラー', 'イッテツーン',
                  'カンケリン', 'フットバース', 'ダイチスイム', 'ノリウツール')
# 実在庫の札が順位表に無くても ValueError にしない (全32札を表に持つようになったため)。
SURVIVAL_RANK = {card: index for index, card in enumerate(SURVIVAL_CARDS)}


def _egg_drop_evidence(cur, card):
    """This battle's 卵落 fit for ``card``, or None when max HP is unknown.

    gcgx: a card drops the enemy egg when its 卵落 exceeds the two generals'
    *max* HP sum mod 16. A wounded battle reading is not max HP, so the
    comparison uses the fixed char.csv HP (hero: the remembered full-strength
    ``ref_ally_hp``) and fails closed to the fixed priority when the general
    is unknown.
    """
    ally_max = cur.get('ref_ally_hp')
    if type(ally_max) is not int or ally_max <= 0:
        ally_name = 'しゅじんこう' if cur.get('ally') == NAME else cur.get('ally')
        ally_max = general_max_hp(ally_name)
    enemy_max = general_max_hp(cur.get('enemy'))
    value = reference.egg_drop_value(card)
    if value is None or any(type(n) is not int or n <= 0 for n in (enemy_max, ally_max)):
        return None
    total = enemy_max + ally_max
    return {'value': value, 'threshold': reference.egg_drop_threshold(total),
            'max_hp_sum': total, 'drops': reference.can_drop_egg(card, total)}


def _egg_threat(triggers):
    """敵がこの戦闘で卵を使える能力か。未読は None (安全と推定しない)。

    開幕の「携行ID合計 >= 48」判定は当方の携行が47以下なので起きない
    (``_cap_card_ids``)。残るのは激突・壁の判定で、思考タイプ0の敵には無い。
    """
    if triggers.has_egg is None:
        return None
    if triggers.has_egg is False:
        return False
    return any(flag is True for flag in (triggers.clash_position,
                                         triggers.wall_critical, triggers.wall_mod4))


def _carried_kit(mem, cur):
    """この戦闘で実際に携行している切り札。出撃注文が特定できない戦闘は None。"""
    order = _order_for_step(mem, cur.get('step'))
    return None if order is None else _deploy_cards(order, mem)


def _egg_plan(mem, cur):
    """将軍の最大HPと携行切り札の卵落を組み合わせた、この戦闘の卵ディニアル計画。

    owner 2026-10-03: 「全ての将軍と切り札のデータをちゃんと内部でデータとして
    持って、その組み合わせで卵を使わせないか、落とさせるようにたたかう」。
    gcgx: 携行札のID合計が48以上だと敵が先に卵を使い、「卵落 > 敵・味方将軍の
    最大HP合計 mod 16」で命中した札は敵の卵を落として以後の召喚を封じる。
    将軍名・最大HP・携行が判明しない項目は fail-closed で None のまま残す。
    """
    enemy = cur.get('enemy')
    side = cur.get('side')
    defense = True if side == 'defense' else False if side == 'attack' else None
    triggers = enemy_egg_triggers(enemy, player_castle_defense=defense)
    kit = _carried_kit(mem, cur)
    ids = None if kit is None else [CARD_IDS.get(card) for card in kit]
    plan = {'threat': _egg_threat(triggers), 'has_egg': triggers.has_egg,
            'kit_known': kit is not None, 'carried': list(kit or []),
            'id_sum': None if ids is None or any(i is None for i in ids) else sum(ids),
            'deny_opening_egg': None,
            'max_hp_sum': None, 'threshold': None, 'droppers': [], 'dropper': None,
            'egg_drop': None, 'triggers': asdict(triggers)}
    if kit is None:
        return plan
    # 携行IDの合計が48以上だと敵が開幕で卵を使う (gcgx ai.html)。不明IDは失敗扱い。
    plan['deny_opening_egg'] = plan['id_sum'] is not None and plan['id_sum'] < ENEMY_EGG_CARD_ID_SUM
    evidence = {card: _egg_drop_evidence(cur, card) for card in kit}
    known = next((evidence[card] for card in kit if evidence.get(card)), None)
    if not known:
        return plan
    plan['max_hp_sum'] = known['max_hp_sum']
    plan['threshold'] = known['threshold']
    plan['droppers'] = reference.egg_droppers(kit, known['max_hp_sum'])
    if not (plan['threat'] and plan['droppers']):
        return plan
    # チャートがこの札を指す戦闘はチャートのタイミングを優先し、指していない札だけを
    # 開幕の卵ディニアルに回す (HPゲートや after_clash を壊さないため)。
    charted = {t['card'] for t in _tactics(mem, cur.get('step'))
               if t.get('step') in (None, cur.get('step')) and t['enemy'] in (None, enemy)}
    free = [card for card in plan['droppers'] if card not in charted]
    if not free:
        return plan
    # 卵落の強い札を先に、同値は将軍へのダメージが小さい札を (主力を温存)。
    plan['dropper'] = min(free, key=lambda card: (
        -reference.egg_drop_value(card),
        reference.CARDS.get(card, {}).get('general_damage') or 0,
        SURVIVAL_RANK.get(card, len(SURVIVAL_CARDS)), CARD_IDS.get(card, 99)))
    plan['egg_drop'] = evidence.get(plan['dropper'])
    return plan


def _egg_drop_tactics(mem, cur, enemy, plan=None):
    """卵を落とせる携行札を開幕に使う戦術 (チャートがこの札を指していない時だけ)。"""
    plan = _egg_plan(mem, cur) if plan is None else plan
    card = plan.get('dropper')
    if not card or cur.get('enemy_egg_dropped'):
        return []
    return [{'enemy': enemy, 'card': card, 'open': True, 'step': cur.get('step'),
             'tactic_id': f"eggdrop:{cur.get('step')}:{card}", 'egg_drop_only': True,
             'note': ('携行札の卵落が敵味方将軍の最大HP合計の余りを超えるため開幕に使い、'
                      '敵の卵を落として召喚を封じる')}]


def _watch_egg_drop(cur, card, hp, evidence):
    """この札が命中すれば敵が卵を落とす、と判定できる時だけ監視を始める。"""
    if evidence and evidence.get('drops'):
        cur['egg_drop_watch'] = {'card': card, 'hp': hp, 'value': evidence['value'],
                                 'threshold': evidence['threshold'],
                                 'max_hp_sum': evidence['max_hp_sum']}


def _egg_drop_confirm(mem, cur):
    """選択後の敵HP低下を、卵を落とした根拠にする。

    実使用告知は未校正のまま (``fast_chain`` と同じ水準の証拠)。卵が落ちたと言える
    のは、卵落札が選択されたあとに敵HPが下がり、まだ生き残っている時だけ。
    HP 0 (倒れた) や根拠がない戦闘では、落ちていない卵を無いと言い切らない。
    """
    watch = cur.get('egg_drop_watch')
    if not watch or cur.get('enemy_egg_dropped'):
        return
    hp, at_open = cur.get('enemy_hp'), watch.get('hp')
    if type(hp) is not int or hp <= 0 or type(at_open) is not int or hp >= at_open:
        return
    if watch['card'] not in (cur.get('cards_selected') or []):
        return                      # 札一覧の選択に達していない＝使用の根拠がない
    cur['enemy_egg_dropped'] = True
    cur.pop('egg_drop_watch', None)
    _record(mem, 'battle_egg_dropped', **_battle_labels(cur), card=watch['card'],
            enemy=cur.get('enemy'), enemy_hp=hp, ally_hp=cur.get('ally_hp'),
            egg_drop={'value': watch['value'], 'threshold': watch['threshold'],
                      'max_hp_sum': watch['max_hp_sum']},
            expected_metric='卵落札の命中後、敵は卵を落として以後召喚を使えなくなる',
            observed_metric={'enemy_hp': hp, 'hp_at_card': at_open,
                             'selected': watch['card'] in (cur.get('cards_selected') or [])},
            reason='卵落が最大HP合計の余りを超える切り札の選択後、敵HPの低下を実測')


def _rescue_card(candidates, cur):
    """The rescue card: a visible heal first, else the best egg dropper.

    ``SURVIVAL_CARDS`` stays the damage/control fallback. A card that would
    drop this battle's egg goes before it: the enemy summon is what a
    low-HP rescue is usually racing. HPs are the battle's start readings
    (the hero's remembered full strength); without them the fixed order stands.
    """
    if not candidates:
        return None
    if 'エンジェリン' in candidates:
        return 'エンジェリン'                     # heal always goes first
    if 'キャトルミュー' in candidates:
        return 'キャトルミュー'                   # instant general kill / 224 EM / 90 boss
    if enemy_egg_triggers(cur.get('enemy')).has_egg is False:
        return candidates[0]                      # no egg to drop
    droppers = [card for card in candidates
                if (_egg_drop_evidence(cur, card) or {}).get('drops')]
    if not droppers:
        return candidates[0]
    return min(droppers, key=lambda card: (-reference.egg_drop_value(card),
                                           SURVIVAL_RANK.get(card, len(SURVIVAL_CARDS))))


BEHIND_EGG_RATIO_TENTHS = 7    # ally HP at or below 70% of the enemy's: rescue (egg) now
GENERAL_CRITICAL_RETREAT_HP = 12


def _unarmed_clash_risk(cur):
    """Check resources before a non-boss egg clash, rather than idle into it."""
    if (cur.get('planned_cards')
            or cur.get('enemy_egg_dropped')       # 卵は既に落ち、召喚の心配はない
            or cur.get('enemy') in chart.BOSSES.values()):
        return False
    triggers = enemy_egg_triggers(cur.get('enemy'))
    return triggers.has_egg is True and triggers.clash_position is True


def _survival_needed(cur):
    hp, enemy, start = (cur.get(k) for k in ('ally_hp', 'enemy_hp', 'start_ally_hp'))
    if any(type(n) is not int or n <= 0 for n in (hp, enemy, start)):
        return False
    # Sword practice has no resource menu. Preserve its released A bursts.
    if (cur.get('enemy'), cur.get('ally'), start, cur.get('start_enemy_hp'), cur.get('step')) == (
            'だいじん', 'どうし', 90, 90, None):
        return False
    # Far behind the enemy from the start: the old 40%-of-start rule fired
    # only at HP ~10, after the melee had already decided the fight, and
    # generals died with an unused egg (g421 15:09 26 vs 48, 15:13 27 vs 38;
    # owner: eggs unused while dying).
    # Behind from the very start (g438 03:31: ココット 22 vs キッシュ 26 went
    # 22 -> 10 in one observation of melee before any card was used, then
    # died). The rescue (cards first, then the egg) opens before the melee.
    # Not for boss fights or battles with charted cards: their plan runs.
    behind_start = (type(cur.get('start_enemy_hp')) is int and start < cur['start_enemy_hp']
                    and not cur.get('planned_cards') and cur.get('enemy') not in chart.BOSSES.values())
    return (hp <= GENERAL_CRITICAL_RETREAT_HP or (hp < enemy and hp * 5 <= start * 2)
            or hp * 10 <= enemy * BEHIND_EGG_RATIO_TENTHS or behind_start
            or _unarmed_clash_risk(cur))


def _survival_state(mem, cur):
    if 'survival' not in cur:
        cur['survival'] = {'opens': 0, 'menu_ticks': 0, 'cards_checked': 0,
                           'cards_attempted': [], 'egg_attempted': False}
        _record(mem, 'battle_survival', **_battle_labels(cur),
                observed_metric={'ally_hp': cur.get('ally_hp'), 'enemy_hp': cur.get('enemy_hp')},
                expected_metric='使用可能な切り札・たまごを確認して選択',
                reason=('携行戦術のない卵持ち敵との衝突前に、戦闘メニューで救済手段を確認'
                        if _unarmed_clash_risk(cur)
                        else 'HP低下のため温存を中止し、戦闘メニューで救済手段を確認'))
    return cur['survival']


def _hero_retreat_needed(cur):
    """Any general's retreat threshold in an attack battle.

    The hero keeps his remembered full-strength reference; another general
    uses this battle's start HP (g460 16:11: ココット 24 vs タピオカ 50 spent
    the whole fight in card menus, fell 24->12->0 and died with no retreat).
    """
    if (cur.get('ally') and cur.get('egg_battle') and cur.get('egg_retreat_needed')
            and all(type(cur.get(k)) is int and cur[k] > 0
                    for k in ('ally_hp', 'enemy_hp', 'start_ally_hp'))):
        return True
    if not cur.get('ally') or not _survival_needed(cur):
        return False
    hp, enemy, start = (cur.get(k) for k in ('ally_hp', 'enemy_hp', 'start_ally_hp'))
    ref = (max(start, int(cur.get('ref_ally_hp') or 0)) if cur.get('ally') == NAME else start)
    rescue = cur.get('survival') or {}
    if (_unarmed_clash_risk(cur) and rescue.get('exhausted')
            and not rescue.get('cards_uncertain')):
        return True  # no observed rescue remains; retreat before the summon
    if cur.get('egg_battle'):
        # An enemy summon we cannot answer (no egg left, no cards) is not a
        # winnable melee: retreat before the general dies (owner 2026-09-29;
        # g460 16:46: ヴィーナス 82 vs アルファルファ 38 summoned and killed
        # her while the bot chose こうげき and the retreat came too late).
        return hp <= GENERAL_CRITICAL_RETREAT_HP or hp * 2 <= enemy or hp * 2 <= ref
    return hp <= GENERAL_CRITICAL_RETREAT_HP or (hp < enemy and hp * 4 <= ref)


def _hero_retreat_open(mem, cur):
    if not _hero_retreat_needed(cur) or cur.get('side') == 'defense':
        return None
    flow = cur.get('hero_retreat') or {}
    if flow.get('unavailable') or flow.get('exhausted') or flow.get('opens', 0) >= 3:
        return None
    card_flow = cur.get('card_flow') or {}
    if card_flow.get('stage') == 'announce':
        # Preserve a selected card's receipt while HP is above the critical
        # threshold. At critical HP, waiting for an uncalibrated receipt can
        # cost the general before the retreat menu opens (g460: ココット
        # 24 -> 12 -> 0 while stuck in card menus). Unknown card use stays
        # unknown; survival takes precedence over continuing that selection.
        hp = cur.get('ally_hp')
        if type(hp) is not int or hp > GENERAL_CRITICAL_RETREAT_HP:
            return None
        pending_card = card_flow.get('card')
        _card_use_unclassified(mem, cur, '危険HPに達したため未確認の切り札を保留し、退却を優先')
        _record(mem, 'battle_retreat_preempted_card', **_battle_labels(cur),
                general=cur.get('ally'), enemy=cur.get('enemy'), card=pending_card,
                observed_metric={'ally_hp': hp, 'enemy_hp': cur.get('enemy_hp'),
                                 'card_confirmation': 'unclassified'},
                resulting_event='retreat_preempted_card_use',
                reason='切り札の使用確認を待つ間に将軍を失わず、主人公の敗北も避けるため退却を優先')
    flow = cur.setdefault('hero_retreat', {})
    flow['opens'] = flow.get('opens', 0) + 1
    cur['card_flow'] = None  # supersede an unselected chart card intention
    _record(mem, 'battle_hero_retreat_open', **_battle_labels(cur),
            general=cur.get('ally'), enemy=cur.get('enemy'),
            castle=cur.get('castle'), side=cur.get('side'),
            observed_metric={'ally_hp': cur['ally_hp'], 'enemy_hp': cur['enemy_hp'],
                             'start_ally_hp': cur['start_ally_hp']},
            reason='主人公の敗北によるゲームオーバーまたは一般将軍の喪失を避けるため退却の可否を確認')
    return [pad('b')]


def _hero_retreat_menu(screen, mem, cur):
    # A castle defense cannot retreat (owner 2026-09-29; the menu has no
    # たいきゃく row there), so never try to select it.
    if not _hero_retreat_needed(cur) or cur.get('side') == 'defense':
        return None
    flow = cur.setdefault('hero_retreat', {})
    if flow.get('unavailable') or flow.get('exhausted'):
        return None
    if 'たいきゃく' not in {w for _,_,w in _options(screen)}:
        flow['unavailable'] = True
        _record(mem, 'battle_hero_retreat_unavailable', **_battle_labels(cur),
                general=cur.get('ally'), enemy=cur.get('enemy'),
                castle=cur.get('castle'), side=cur.get('side'),
                reason='退却が有効な項目として読めないため卵・切り札・奥の手で対処')
        return None
    if flow.get('selected'):
        flow['wait'] = flow.get('wait', 0) + 1
        if flow['wait'] <= 3:
            return []
        if flow['selected'] >= 2:
            flow['exhausted'] = True
            return [pad('b')]
    flow['menu_ticks'] = flow.get('menu_ticks', 0) + 1
    if flow['menu_ticks'] > 8:
        flow['exhausted'] = True
        return [pad('b')]
    move = _battle_menu_to(screen, 'たいきゃく')
    if move != 'here':
        return [move] if move else []
    if (cur.get('card_flow') or {}).get('selection_planned'):
        _card_use_unclassified(mem, cur, '退却を優先し切り札の実使用は未確認')
    else:
        cur['card_flow'] = None
    flow['selected'] = flow.get('selected', 0) + 1
    flow['wait'] = 0
    _record(mem, 'battle_hero_retreat_select', **_battle_labels(cur),
            general=cur.get('ally'), enemy=cur.get('enemy'),
            castle=cur.get('castle'), side=cur.get('side'), choice='たいきゃく',
            observed_metric={'ally_hp': cur['ally_hp'], 'enemy_hp': cur['enemy_hp']},
            resulting_event='retreat_selected_not_yet_confirmed',
            reason='主人公または将軍の危険な敗北を避けるため有効な退却行とカーソルを確認して決定')
    return [pad('a')]


def _battle_menu_to(screen, label):
    if screen.hand:
        return menu_to(screen, label)
    target = [y for x, y, word in _options(screen) if word == label]
    if screen.menu_cursor is None or not target:
        return None
    y = min(target, key=lambda y: abs(y - screen.menu_cursor))
    if y == screen.menu_cursor:
        return 'here'
    return pad('down' if y > screen.menu_cursor else 'up')


def _survival_menu(screen, mem, cur):
    rescue = _survival_state(mem, cur)
    rescue['pending_opens'] = 0
    rescue['menu_ticks'] += 1
    if rescue.get('egg_pending'):
        rescue['egg_wait'] = rescue.get('egg_wait', 0) + 1
        if rescue['egg_wait'] <= 3:
            return []  # allow the accepted summon to leave its menu
        rescue['egg_pending'] = False
    if rescue['menu_ticks'] > 12 or rescue.get('exhausted'):
        rescue['exhausted'] = True
        return [pad('b')]
    # A visible list is authoritative. Do not infer carried cards from the
    # chart, a different general's sortie, or a planned inventory.
    labels = {word for _, _, word in _options(screen)}
    # Cards first, then the egg (owner: 切り札が先 - the chart's order).
    if ('きりふだ' in labels and not rescue.get('cards_exhausted')
            and rescue['cards_checked'] < 3):
        label = 'きりふだ'
    elif 'たまごをつかう' in labels and not rescue['egg_attempted']:
        label = 'たまごをつかう'
    else:
        rescue['exhausted'] = True
        _record(mem, 'battle_survival_unavailable', **_battle_labels(cur),
                observed_metric={'labels': sorted(labels)},
                reason='読み取れる未試行の救済手段がないため白兵へ戻る')
        return [pad('b')]
    move = _battle_menu_to(screen, label)
    if move != 'here':
        return [move] if move else []  # no blind A on a missing cursor
    if label == 'きりふだ':
        rescue['cards_checked'] += 1
        cur['card_flow'] = {'card': None, 'stage': 'list', 'survival': True, 'list_ticks': 0}
    else:
        _egg_recheck(mem)
        rescue['egg_attempted'] = True
        rescue['egg_pending'] = True
    _record(mem, 'battle_survival_select', **_battle_labels(cur),
            observed_metric={'label': label}, resulting_event='selection_planned_not_yet_confirmed',
            reason='カーソルと有効な項目を確認して救済行動を選択')
    return [pad('a')]


def _survival_card_list(screen, mem, cur, flow, names):
    rescue = cur['survival']
    flow['list_ticks'] += 1
    # Names can disappear during a fade. Bound the wait and return to the
    # parent menu once, so an empty/unsupported list can still lead to an egg.
    # A name is not an inventory count (g514: two イッテツーン, one
    # unclassified selection, then healthy バジル40 vs27 retreated). A
    # second copy is eligible only after two agreeing live lists show fewer
    # copies than at the previous selection. No consumption receipt is
    # inferred, and an unchanged/unreadable list cannot cause repeated A.
    counts = {c: names.count(c) for c in SURVIVAL_CARDS if c in names}
    same = counts == flow.get('counts')
    flow['count_readings'] = flow.get('count_readings', 0) + 1 if same else 1
    flow['counts'] = counts
    previous = rescue.setdefault('card_counts_at_selection', {})
    attempted = rescue['cards_attempted']
    candidates = [c for c in SURVIVAL_CARDS if c in counts and (
        c not in attempted or (type(previous.get(c)) is int
                              and counts[c] < previous[c]
                              and flow['count_readings'] >= 2))]
    uncertain = [c for c in counts if c in attempted and c not in candidates]
    rescue['cards_uncertain'] = bool(uncertain)
    if (not candidates and uncertain and flow['count_readings'] < 2
            and flow['list_ticks'] <= 10):
        return []
    if not candidates or flow['list_ticks'] > 10:
        if not names and flow['list_ticks'] <= 3:
            return []
        rescue['cards_exhausted'] = True
        cur['card_flow'] = None
        if flow['list_ticks'] > 10 and counts:
            rescue['cards_uncertain'] = True
        if uncertain:
            _record(mem, 'battle_card_remaining_unconfirmed', **_battle_labels(cur),
                    observed_metric={'listed_counts': counts,
                                     'counts_at_selection': dict(previous)},
                    reason='同名札が実一覧に残るが使用・枚数減少を確認できないため再決定を保留。救済手段なしとは断定しない')
        return [pad('b')]
    card = _rescue_card(candidates, cur)
    move = _battle_menu_to(screen, card)
    if move != 'here':
        return [move] if move else []
    flow.update(card=card, stage='announce', selection_planned=True)
    rescue['cards_attempted'].append(card)
    previous[card] = counts[card]
    rescue['cards_uncertain'] = False
    cur['card_consumption_complete'] = False
    cur.setdefault('cards_selected', []).append(card)
    egg_drop = _egg_drop_evidence(cur, card)
    if egg_drop and egg_drop['drops']:
        # 救済で選んだ卵落札も、命中すれば敵の卵は落ちる。同じ根拠で監視を始める。
        _watch_egg_drop(cur, card, cur.get('enemy_hp'), egg_drop)
    _record(mem, 'battle_card_selected', **_battle_labels(cur), card=card,
            expected_metric='実使用告知と卵落' if egg_drop and egg_drop['drops'] else '実使用告知',
            observed_metric={'listed_cards': names, 'listed_count': counts[card],
                             'survival': True, 'egg_drop': egg_drop},
            resulting_event='selection_planned_not_yet_confirmed',
            reason=('HP低下で卵を落とせる切り札を選択。消費は未確定' if egg_drop and egg_drop['drops']
                    else 'HP低下に対処する切り札を選択。消費は未確定'))
    return [pad('a')]



def _available_rare_tactic(mem, cur):
    return next((t for t in _tactics(mem, cur.get('step'))
                 if t['card'] == 'キャトルミュー' and t.get('step') == cur.get('step')
                 and t.get('tactic_id') not in cur.get('tactics_done', [])
                 and (not t.get('boss_only') or _boss_tactics_allowed(mem, cur))), None)

def battle_menu_pending_step(mem):
    if not mem.get('battle'):
        return []
    ticks = mem['battle_menu_pending_ticks'] = mem.get('battle_menu_pending_ticks', 0) + 1
    if ticks <= 8:
        return []
    mem.pop('battle_menu_pending_ticks', None)
    _record(mem, 'battle_command_incomplete',
            reason='表示途中の戦闘メニューが観測上限に達したため決定せず戻る')
    return [pad('b')]


def battle_menu_step(screen: Screen, mem):
    _migrate_card_evidence(mem)
    cur = mem.get('battle') or {}
    flow = cur.get('card_flow')
    if screen.kind != 'battle_menu':
        return []
    if screen.hidden_battle_commands or screen.has('おくのて'):
        return okunote_step(screen, mem)
    retreat = _hero_retreat_menu(screen, mem, cur)
    if retreat is not None:
        return retreat
    if flow and flow.get('survival'):
        flow['menu_returns'] = flow.get('menu_returns', 0) + 1
        if flow['stage'] == 'list' and flow['menu_returns'] <= 2:
            return [pad('a')] if _battle_menu_to(screen, 'きりふだ') == 'here' else []
        if flow['stage'] == 'announce':
            _card_use_unclassified(mem, cur, '実使用告知を確認できないまま戦闘メニューへ復帰')
        else:
            cur['survival']['cards_exhausted'] = True
            cur['card_flow'] = None
        return _survival_menu(screen, mem, cur)
    if flow:
        flow['command_menu_ticks'] = flow.get('command_menu_ticks', 0) + 1
        if flow['command_menu_ticks'] > 8:
            _card_use_unclassified(mem, cur, '切り札行とカーソルの確認が観測上限に達したため保留')
            return [pad('b')]
        move = _battle_menu_to(screen, 'きりふだ')
        if move != 'here':
            if move:
                flow['stage'] = 'down'
            return [move] if move else []
        flow['stage'] = 'list'
        return [pad('a')]
    if not flow and cur.get('egg_battle') and 'きりふだ' in screen.text:
        rare = _available_rare_tactic(mem, cur)
        move = _battle_menu_to(screen, 'きりふだ') if rare else None
        if move is not None:
            tid = rare['tactic_id']
            cur.setdefault('tactics_done', []).append(tid)
            cur['card_flow'] = {'card': 'キャトルミュー', 'stage': 'list' if move == 'here' else 'down',
                                'tactic_id': tid, 'enemy_hp_at_open': cur.get('enemy_hp')}
            _record(mem, 'battle_card', **_battle_labels(cur), card='キャトルミュー',
                    observed_metric={'enemy_hp': cur.get('enemy_hp'), 'egg_battle': True},
                    reason=('実携行のキャトルミューをボスへ使用し90ダメージを狙う'
                            if cur.get('enemy') in chart.BOSSES.values() else
                            '実携行のキャトルミューを敵EMへ使用し224ダメージと石化を狙う'))
            return [pad('a')] if move == 'here' else [move]
    if cur.get('survival') or _survival_needed(cur):
        return _survival_menu(screen, mem, cur)
    # Chart has no card due this frame: independent judgment under the chart,
    # mapped from the original six melee patterns (①③ pass / ⑥ egg; ②④⑤ are
    # chart-directed cards and never chosen blindly here).
    exp = mem.get('_experience')
    key = experience.situation_key('battle_menu', mem)
    if not mem.get('indep_menu'):
        mem['indep_menu'] = True
        default = 'use_egg' if _behind(cur) else 'pass'
        action = experience.preferred(exp, key, default=default, kind='battle_menu')
        mem['indep_menu_key'] = key
        mem['indep_menu_action'] = action
        # Original patterns: ①/③ continue melee when not behind; ⑥ use egg
        # only when behind (melee+chart cards insufficient / none due).
        pattern = '⑥' if action == 'use_egg' else ('③' if cur.get('clashed') else '①')
        battle = mem.get('battle')
        if isinstance(battle, dict):
            battle['independent'] = {'kind': 'battle_menu', 'key': key, 'action': action,
                                     'pattern': pattern}
        _record(mem, 'independent_menu',
                strategy_variant=f'independent_{action}',
                deviation_reason='チャートに戦術なし: 状況判断',
                expected_metric='戦闘結果による判断の検証',
                observed_metric={'enemy_hp': cur.get('enemy_hp'), 'ally_hp': cur.get('ally_hp'),
                                 'experience_key': key, 'source_pattern': pattern},
                source_pattern=pattern,
                reason='チャートが当該フレームに切り札を指示していないための独自判断（原典戦術'
                       + pattern + 'に相当）')
    action = mem.get('indep_menu_action', 'pass')
    if action == 'use_egg' and 'たまごをつかう' not in screen.text:
        # A spent egg greys its row out (unreadable): A there does nothing.
        if not mem.get('egg_row_dead'):
            mem['egg_row_dead'] = True
            _record(mem, 'situation_held', screen=screen.kind, strategy_variant='egg_unavailable',
                    reason='たまごをつかうが使えない表示のため卵を諦めて白兵へ戻る')
        return [pad('b')]
    if action == 'use_egg':
        _egg_recheck(mem)
        if screen.hand:
            move = menu_to(screen, 'たまごをつかう')
            if move == 'here':
                return [pad('a')]
            if move:
                return [move]
            # Hand present but the label is unreadable: top item is たまご.
        return [pad('a')]
    return [pad('b')]


OKUNOTE_MAX_SELF_DAMAGE = 88  # gcgx: ヤケクソ at castle Lv1; higher levels reduce it


def _defender_last_resort(mem, cur):
    """A losing non-hero human defender may take the irreversible last chance.

    HP defeat is not certain death; neither is the lottery safe. This only
    replaces repeated losing melee after *measured* resource unavailability.
    Boss kits, the hero, uncertain locations and healthy fights stay excluded.
    """
    if (cur.get('ally') in (None, NAME, 'しゅじんこう', 'だいじん')
            or cur.get('side') != 'defense'
            or cur.get('castle') not in chart.castles(mem.get('chapter') or 0)
            or cur.get('enemy') in chart.BOSSES.values()
            or cur.get('planned_cards') or cur.get('card_flow') or cur.get('egg_battle')
            or cur.get('okunote_last_resort_selected')
            or not cur.get('okunote_only_observed')):
        return False
    hp, enemy, start, enemy_start = (cur.get(k) for k in
        ('ally_hp', 'enemy_hp', 'start_ally_hp', 'start_enemy_hp'))
    ally_max = general_max_hp(cur.get('ally'))
    enemy_max = general_max_hp(cur.get('enemy'))
    if any(type(v) is not int or v <= 0 for v in
           (hp, enemy, start, enemy_start, ally_max, enemy_max)):
        return False
    return (hp <= start <= ally_max and enemy <= enemy_start <= enemy_max
            and hp * 2 <= start and enemy * 5 >= enemy_start * 4)


def okunote_step(screen, mem):
    cur = mem.get('battle')
    if not cur:
        return []
    flow = cur.setdefault('okunote_flow', {'ticks': 0})
    flow['ticks'] += 1
    if screen.kind == 'battle_menu':
        hp = cur.get('ally_hp')
        # g482: パプリカ34 entered the irreversible random choices and chose
        # しんだフリ (28..48 self damage), then lost at HP0. Before opening
        # the candidates, require enough *current* HP to survive even the
        # worst Lv1 result, except the measured losing non-hero defense below.
        # An already-open list cannot be cancelled: below
        # we still choose the strongest visible candidate there.
        labels = {w for _, _, w in _options(screen)}
        # The gray parent rows or a sole active おくのて are direct evidence;
        # chart inventory/house state cannot prove the currently usable menu.
        cur['okunote_only_observed'] = ((screen.hidden_battle_commands or 'おくのて' in labels)
            and not labels.intersection({'たまごをつかう', 'きりふだ', 'たいきゃく'}))
        last_resort = _defender_last_resort(mem, cur)
        if last_resort and not cur.get('okunote_last_resort_recorded'):
            cur['okunote_last_resort_recorded'] = True
            _record(mem, 'battle_okunote_last_resort', **_battle_labels(cur),
                    observed_metric={'ally_hp': hp, 'start_ally_hp': cur['start_ally_hp'],
                                     'enemy_hp': cur['enemy_hp'],
                                     'start_enemy_hp': cur['start_enemy_hp'],
                                     'max_self_damage': OKUNOTE_MAX_SELF_DAMAGE},
                    resulting_event='irreversible_risk_accepted',
                    reason='他の手段が使用不能で押し負ける一般守備将軍の最後の救済。自傷リスクは残る')
        if (type(hp) is not int or hp <= OKUNOTE_MAX_SELF_DAMAGE) and not last_resort:
            rescue = _survival_state(mem, cur)
            if not flow.get('risk_declined'):
                flow['risk_declined'] = True
                _record(mem, 'battle_okunote_risk_declined', **_battle_labels(cur),
                        observed_metric={'ally_hp': hp, 'max_self_damage': OKUNOTE_MAX_SELF_DAMAGE},
                        reason='奥の手は候補確認後にキャンセルできず自傷もあるため、低HPでは開かず白兵へ戻る')
            rescue['exhausted'] = True
            return [pad('b')]
    if flow['ticks'] > 16:
        return [pad('b')] if screen.kind == 'battle_menu' else []
    if screen.hidden_battle_commands:
        _record(mem, 'battle_okunote_scroll', **_battle_labels(cur),
                observed_metric={'cursor_y': screen.menu_cursor},
                reason='灰色のたまご・切り札・退却の下にあるおくのてへスクロール')
        return [pad('down')]
    names = {w for _,_,w in _options(screen)}
    if screen.kind == 'battle_menu' and 'おくのて' in names:
        label = 'おくのて'
    else:
        candidates = [w for w in OKUNOTE_CHOICES if w in names]
        if not candidates:
            return []
        label = candidates[-1]
    move = _battle_menu_to(screen, label)
    if move != 'here':
        return [move] if move else []
    if label == 'おくのて' and cur.get('okunote_last_resort_recorded'):
        cur['okunote_last_resort_selected'] = True
    _record(mem, 'battle_okunote_select', **_battle_labels(cur), choice=label,
            observed_metric={'options': sorted(names)},
            reason='奥の手のカーソルを確認して選択。候補は効果が高いものを優先')
    return [pad('a')]


def card_list_step(screen: Screen, mem):
    """Keep card intention/selection separate from observed consumption."""
    _migrate_card_evidence(mem)
    cur = mem.get('battle') or {}
    flow = cur.get('card_flow')
    if (_hero_retreat_needed(cur) and flow and flow.get('stage') == 'list'
            and not (cur.get('hero_retreat') or {}).get('unavailable')
            and not (cur.get('hero_retreat') or {}).get('exhausted')):
        cur['card_flow'] = None
        _record(mem, 'battle_hero_retreat_cancel_card', **_battle_labels(cur),
                reason='未選択の切り札一覧を閉じ、どうしの緊急退却を優先')
        return [pad('b')]
    names = [w for _, _, w in _options(screen) if w in CARD_NAMES]
    if not flow:
        return [pad('b')]
    if flow['stage'] == 'list' and flow.get('survival'):
        return _survival_card_list(screen, mem, cur, flow, names)
    if flow['stage'] == 'list':
        if flow['card'] not in names:
            # A 500-ms observation can catch the opening text before the
            # carried names draw (g490: two confirmed イッテツーン, then an
            # empty OCR list). An unreadable frame is not a missing-card
            # receipt. Explicit absence stays immediate; other readable
            # lists must agree twice, and unreadable lists back out boundedly.
            absent = any(text in ''.join(screen.text.split()) for text in
                         ('きりふだはありません', 'きりふだなし'))
            flow['list_observations'] = int(flow.get('list_observations', 0)) + 1
            previous = flow.get('missing_list_names')
            flow['missing_list_names'] = names
            if not absent and (not names or previous != names):
                if flow['list_observations'] < 4:
                    return []
                _record(mem, 'battle_card_list_unclassified', **_battle_labels(cur),
                        card=flow['card'], observed_metric={'listed_cards': names,
                                                           'observations': flow['list_observations']},
                        expected_metric='読める切り札一覧または明示的な切り札なし表示',
                        reason='札一覧を確定できないため、携行不足と断定せず入力を戻す')
                cur['card_flow'] = None
                return [pad('b'), pad('b')]
            cur.setdefault('cards_missing', []).append(flow['card'])
            if not cur.get('deviation_reason'):
                cur['strategy_variant'] = 'chart_card_unavailable'
                cur['deviation_reason'] = 'チャートで予定した切り札を携行していない'
            _record(mem, 'battle_card_missing', **_battle_labels(cur), card=flow['card'],
                    expected_metric={'carried_card': flow['card']}, observed_metric={'listed_cards': names},
                    resulting_event='card_not_selected', reason='切り札を携行していないため白兵を継続')
            cur['card_flow'] = None
            return [pad('b'), pad('b')]
        index = names.index(flow['card'])
        flow['stage'] = 'announce'
        flow['selection_planned'] = True
        cur['card_consumption_complete'] = False
        cur.setdefault('cards_selected', []).append(flow['card'])
        _record(mem, 'battle_card_selected', **_battle_labels(cur), card=flow['card'],
                expected_metric='実使用告知', observed_metric={'listed_cards': names},
                resulting_event='selection_planned_not_yet_confirmed', reason='切り札選択入力を予定。消費は未確定')
        return [pad('down')] * index + [pad('a')]
    if flow['stage'] == 'announce' and flow.get('selection_planned'):
        if flow.get('survival'):
            flow['announce_ticks'] = flow.get('announce_ticks', 0) + 1
            if flow['announce_ticks'] > 6:
                _card_use_unclassified(mem, cur, '切り札の告知待ち上限に達したため選択画面から戻る')
                return [pad('b')]
        # No live frame/parser signature has calibrated a use receipt yet.
        # Keep capturing this flow, but never unlock after_card from guessed text.
        cur['card_consumption_complete'] = False
        if not flow.get('uncalibrated_candidate_recorded'):
            flow['uncalibrated_candidate_recorded'] = True
            _record(mem, 'battle_card_candidate', **_battle_labels(cur), card=flow['card'],
                    expected_metric='実画面とparserで校正済みの切り札使用証拠',
                    observed_metric={'confirmation': 'unclassified', 'screen_kind': screen.kind},
                    resulting_event='card_use_unclassified', reason='告知署名が未校正のため消費を確定せず画像収集')
    return []


def _hold_general_loss_metric(mem):
    """HP defeat is not proof of death; invalidate old inferred counters once.

    No death/roster-loss observer exists yet. Preserve HP battle counters and
    original JSONL history, but mark both current and archived in-memory loss
    totals unknown on the next observation, even if the old total was zero.
    """
    for scope in ('stats', 'previous_stats'):
        old_stats = mem.get(scope)
        if not isinstance(old_stats, dict):
            continue
        previous = old_stats.get('generals_lost')
        mem[scope] = {**old_stats, 'generals_lost': None}
        if previous is not None:
            bounded_previous = previous if type(previous) is int and 0 <= previous <= 10**6 else None
            _record(mem, 'metric_invalidated', metric='generals_lost', metric_scope=scope,
                    expected_metric='将軍の死亡または喪失を直接確認する証拠',
                    observed_metric={'previous_inferred_count': bounded_previous,
                                     'generals_lost': None, 'status': 'unclassified'},
                    reason='HP敗北から将軍喪失は確定できず、喪失観測器がないため旧集計を未分類化')


def battle_end(mem, next_kind, *, defense_continues=False):
    _hold_general_loss_metric(mem)
    _migrate_card_evidence(mem)
    cur = mem.get('battle')
    if not cur:
        return
    # A panel can blink during the melee; end only on the second
    # consecutive observation without it.
    cur['away'] = cur.get('away', 0) + 1
    if cur['away'] < 2 and not defense_continues:
        return
    if (cur.get('card_flow') or {}).get('stage') == 'announce':
        _card_use_unclassified(mem, cur, '実使用告知がないまま戦闘が終了')
    mem['battle_seen'] = None
    mem.pop('battle', None)
    mem['attack'] = None
    enemy_hp, ally_hp = cur.get('enemy_hp'), cur.get('ally_hp')
    if ally_hp == 0:
        from .hanjuku_roster import invalidate
        invalidate(mem)  # HP defeat makes membership uncertain; it is not proof of death.
    if enemy_hp == 0 and ally_hp not in (None, 0):
        outcome = 'win'
    elif ally_hp == 0 and enemy_hp not in (None, 0):
        outcome = 'loss'
    else:
        outcome = 'unclassified'
    castle = cur.get('castle')
    receipt = cur.get('defeat_owner') or {}
    observed_owner = (receipt.get('owner') if isinstance(receipt, dict)
                      and receipt.get('castle') == castle
                      and receipt.get('chapter') == (mem.get('chapter') or 0)
                      and castle in chart.castles(mem.get('chapter') or 0)
                      and receipt.get('owner') in ('own', 'enemy')
                      and type(ally_hp) is int and type(enemy_hp) is int
                      and cur.get('side') == 'defense' and outcome == 'loss' else None)
    defense_retained = observed_owner == 'own'
    if outcome == 'win' and castle and cur.get('side') == 'attack':
        (mem.get('castle_income') or {}).pop(STATUS_NAMES.get(castle, castle), None)
        captured = mem.setdefault('captured', [])
        if castle not in captured:
            captured.append(castle)
        mem['lost'] = [c for c in mem.get('lost') or [] if c != castle]
        # The winner occupies the castle it took.
        mem.setdefault('garrison', {})[castle] = []
        _garrison_move(mem, cur.get('ally'), target=castle)
    if outcome == 'win' and castle and cur.get('side') == 'defense':
        _garrison_move(mem, cur.get('ally'), target=castle)
    if outcome == 'loss' and castle and cur.get('side') == 'defense' and not (defense_continues or defense_retained):
        (mem.get('castle_income') or {}).pop(STATUS_NAMES.get(castle, castle), None)
        mem['captured'] = [c for c in mem.get('captured', []) if c != castle]
        (mem.get('garrison') or {}).pop(castle, None)
        lost = mem.setdefault('lost', [])
        if castle not in lost:
            lost.append(castle)
    if outcome == 'loss' and (cur.get('side') == 'attack' or defense_continues or defense_retained) and cur.get('ally'):
        general = cur['ally']
        for source in list(mem.get('garrison') or {}):
            _garrison_move(mem, general, source=source)
        unknown = mem.setdefault('general_location_unknown', [])
        if general not in unknown:
            unknown.append(general)
        _record(mem, 'general_location_unclassified', general=general, castle=castle,
                reason='敗北した将軍の所在は未確認のため古い駐留情報を破棄し、一覧での再確認を待つ')
    stats = mem.setdefault('stats', {'wins': 0, 'losses': 0, 'unclassified': 0, 'cards_used': 0,
                                     'generals_lost': None, 'cards_confirmed': 0, 'card_evidence_version': 1})
    stats[{'win': 'wins', 'loss': 'losses'}.get(outcome, 'unclassified')] += 1
    confirmed = len(cur.get('cards_used', []))
    stats['cards_confirmed'] = stats.get('cards_confirmed', 0) + confirmed
    if stats.get('cards_used') is not None and cur.get('card_consumption_complete') is True:
        stats['cards_used'] += confirmed
    else:
        stats['cards_used'] = None
    indep = cur.get('independent')
    if isinstance(indep, dict) and outcome in ('win', 'loss'):
        experience.record(mem, indep.get('key'), indep.get('action'), outcome)
        _record(mem, 'experience_result', experience_key=indep.get('key'),
                experience_action=indep.get('action'), outcome=outcome,
                reason='独自判断の結果を経験記憶へ反映')
    step = cur.get('step')
    boss_order = _order_for_step(mem, step)
    chapter = mem.get('chapter') or 0
    boss_name = chart.BOSSES.get(chapter)
    boss_cell = chart.boss_castle(chapter)
    boss_attempt = (_is_boss_order(boss_order, mem)
                    and cur.get('enemy') == boss_name
                    and cur.get('castle') == boss_cell and cur.get('side') == 'attack'
                    and cur.get('entry_evidence') == 'measured_boss_entry')
    if outcome == 'loss' and boss_attempt:
        retries = mem.setdefault('retries', {})
        retries[step] = retries.get(step, 0) + 1
        for key in ('general_override', 'card_override', 'rare_card_kit', 'strong_card_kit', 'order_context', 'sortie_general', 'sortie_actor_miss'):
            mem.setdefault(key, {}).pop(step, None)
        if retries[step] <= 3:
            mem.setdefault('orders', {})[step] = 'pending'
            context = {'strategy_variant': 'retry_chart_boss_kit',
                       'deviation_reason': 'ボス戦のHP敗北を確認。出撃将軍と既定切り札を再確認して再試行',
                       'expected_metric': {'general': boss_order['general'],
                                           'cards': list(boss_order['cards']),
                                           'goal': f'{boss_name or "ボス"}戦勝利'}}
            mem.setdefault('retry_context', {})[step] = context
            _record(mem, 'order_retry', chart_step=step, **context,
                    observed_metric={'outcome': outcome, 'enemy_hp': enemy_hp, 'ally_hp': ally_hp},
                    resulting_event=f'retry:{retries[step]}', reason='既定のチャート装備を確認してボスへ再挑戦')
        else:
            mem.setdefault('orders', {})[step] = 'failed'
            _record(mem, 'situation_held', chart_step=step, strategy_variant='boss_retry_exhausted',
                    observed_metric={'retries': retries[step]}, reason='ボス再試行の上限に到達したため保留')
    elif outcome == 'loss' and (_is_boss_order(boss_order, mem) or cur.get('enemy') == boss_name):
        _record(mem, 'situation_held', chart_step=step, strategy_variant='boss_entry_unclassified',
                observed_metric={'castle': castle, 'side': cur.get('side'),
                                 'entry_evidence': cur.get('entry_evidence')},
                reason='実測ボス突入の証拠がないため再出撃を保留')
    elif (outcome == 'loss' and cur.get('side') == 'attack' and step
            and castle not in mem.get('captured', [])):
        retries = mem.setdefault('retries', {})
        retries[step] = retries.get(step, 0) + 1
        if retries[step] <= 2:
            mem.setdefault('orders', {})[step] = 'pending'
            # Better than repeating a lost melee: open with two イッテツーン
            # (10 general damage each per the chart's card table).
            extra = ['イッテツーン', 'イッテツーン']
            mem.setdefault('card_override', {})[step] = extra
            mem.setdefault('retry_context', {})[step] = {
                'deviation_reason': 'チャートの白兵で敗北したため、開幕イッテツーン2枚で再攻撃',
                'expected_metric': {'enemy_hp_after_open': max(0, (enemy_hp or 0) - 20)}}
            _record(mem, 'order_retry', chart_step=step, strategy_variant='retry_with_opening_cards',
                    deviation_reason='チャートの白兵で敗北したため、開幕イッテツーン2枚で再攻撃',
                    expected_metric={'enemy_hp_after_open': max(0, (enemy_hp or 0) - 20)},
                    observed_metric={'outcome': outcome, 'enemy_hp': enemy_hp, 'ally_hp': ally_hp},
                    resulting_event=f'retry:{retries[step]}', reason='城を取らないと後続手順とボスへ進めない')
    boss = chart.BOSSES.get(mem.get('chapter') or 0)
    resulting = None
    if outcome == 'win' and boss and cur.get('enemy') == boss:
        resulting = f"chapter_{mem['chapter']}_boss_defeated"
        # A boss HP reading proves this battle result, not the next chapter.
        # Only a visible chapter header or a next-chapter fact (castle name,
        # enemy general debut) may advance and reset route state; the defeat
        # is the context a further-jump general needs (observe_chapter_general).
        mem['boss_defeated'] = mem.get('chapter')
    elif outcome == 'win' and castle and cur.get('side') == 'attack':
        resulting = f'captured:{castle}'
    elif outcome == 'loss' and castle and cur.get('side') == 'defense' and not (defense_continues or defense_retained):
        resulting = f'lost:{castle}'
    if defense_continues:
        resulting = f'defense_continues:{castle}'
    elif defense_retained:
        resulting = f'defense_retained:{castle}'
    _record(mem, 'battle_result', **_battle_labels(cur), enemy=cur.get('enemy'),
            expected_metric=cur.get('strategy_expected'),
            ally=cur.get('ally'), castle=castle, side=cur.get('side'), outcome=outcome,
            observed_metric={'enemy_hp': enemy_hp, 'ally_hp': ally_hp,
                             'cards_used': cur.get('cards_used', []),
                             'cards_selected': cur.get('cards_selected', []),
                             'cards_missing': cur.get('cards_missing', []),
                             'cards_unclassified': cur.get('cards_unclassified', []),
                             'card_consumption_complete': cur.get('card_consumption_complete', False),
                             'hero_retreat_selected': bool((cur.get('hero_retreat') or {}).get('selected')),
                             'general_loss': 'unclassified',
                             **({'castle_owner_after_defeat': observed_owner} if observed_owner else {})},
            resulting_event=resulting or outcome, resulting_stage=None, next_screen=next_kind,
            reason='戦闘終了時のHP表示から判定' if outcome != 'unclassified'
            else '最終HPが0/非0で確定しないため未分類')
    # One judged battle per started battle, whatever the verdict. The gap
    # against battles_started is the set the status panel must disclose.
    _tally(mem, 'battles_judged')
    _maybe_recall_weak_hero(mem, cur, outcome, ally_hp)


HERO_RECALL_RATIO_TENTHS = 4   # hero HP at or below 40% of his full strength after a win
HERO_RECALL_MIN_HP = 20        # and never below this when his full strength is unknown


def _hero_marching(mem):
    now = int(mem.get('tick') or 0)
    return [step for step, s in (mem.get('sorties') or {}).items()
            if s.get('general') == NAME and s.get('status') in ('en_route', 'launched_unconfirmed')
            and s.get('tick') is not None and now - int(s['tick']) < SORTIE_BUSY_TICKS]


def _maybe_recall_weak_hero(mem, cur, outcome, ally_hp):
    """Pull the hero back to a castle after a win that left him weak.

    g436 23:15-23:18: the hero, marching on ゴーメン (1-A2), won a road battle
    90 -> 14 HP with no soldiers left, marched on, and fell there within two
    seconds of the next battle opening (14 -> 9 -> 0): too fast for any
    in-battle retreat. The march itself has to be called off. The unit menu
    SELECT + A opens is the camp menu (g436 22:04), so the camp recall's
    きかん path is reused.
    """
    if cur.get('ally') != NAME or outcome != 'win' or type(ally_hp) is not int or mem.get('recall'):
        return
    full = int(mem.get('hero_max_hp') or 0)
    limit = max(HERO_RECALL_MIN_HP, full * HERO_RECALL_RATIO_TENTHS // 10)
    marching = _hero_marching(mem)
    if ally_hp > limit or not marching:
        return
    mem['recall'] = {'stage': 'hero_focus', 'hero': True, 'steps': 0, 'sorties': marching}
    (mem.get('y_jumps') or {}).pop('RECALL:target', None)
    (mem.get('y_jumped') or {}).pop('RECALL', None)
    _record(mem, 'hero_recall_start', observed_metric={'ally_hp': ally_hp, 'full_hp': full,
                                                        'sorties': marching},
            reason=f'主人公がHP{ally_hp}まで減って進軍中のため、次の戦闘で倒れる前に自軍城へ帰還させる')


ATTACK = re.compile(r'(\S+?)しょうぐんが(\S+?)じょうにのりこんだ')
DEFENSE = re.compile(r'(\S+?)じょうがてきにせめこまれ')
BOSS_ENTRY_HOLD_LIMIT = 8   # unmatched boss entry messages before the screen is closed anyway


def message_step(screen: Screen, mem):
    text = screen.text
    boss_entry = re.fullmatch(r'([^\ufffd\s]+)しょうぐんがボスじょうにせめこんだ!!', text)
    if boss_entry:
        general = boss_entry.group(1)
        boss_cell = chart.boss_castle(mem.get('chapter') or 0)
        current = mem.get('attack') or {}
        step, match = _match_sortie(mem, boss_cell, general)
        if (match != 'matched' and current.get('castle') == boss_cell
                and current.get('general') == general
                and current.get('entry_evidence') == 'measured_boss_entry'):
            return [pad('a')]              # same entry text still on screen
        # Base boss order or an adjusted/interim boss order (generation-scoped id):
        # the launched order itself must target this chapter's boss castle.
        if not _is_boss_order(_order_for_step(mem, step), mem):
            if match == 'ambiguous':
                # Two sorties of this general are still heading for the boss
                # castle (g460 17:10: the base order and its adjusted wave), so
                # the order id is unknown. Never bind either of them - but the
                # message is measured and the screen must keep moving.
                _record(mem, 'attack_observed', chart_step=None, general=general, castle=boss_cell,
                        expected_metric=None,
                        observed_metric={'general': general, 'message': text, 'sortie_match': match},
                        deviation_reason='ambiguous_sortie',
                        reason='ボス城への出撃が複数在途のため注文は特定せず、実測文のみで進行')
                mem['attack'] = {'general': general, 'castle': boss_cell, 'side': 'attack',
                                 'step': None, 'entry_evidence': 'measured_boss_entry'}
                return [pad('a')]
            held = mem.get('boss_entry_hold') or {}
            misses = held.get('n') + 1 if held.get('text') == text else 1
            if misses < BOSS_ENTRY_HOLD_LIMIT:
                mem['boss_entry_hold'] = {'text': text, 'n': misses}
                _record(mem, 'situation_held', screen='boss_attack_started',
                        observed_metric={'message': text, 'sortie_match': match,
                                         'hold_count': misses},
                        reason='実測ボス突入文を読んだが出撃注文と一致しないため保留')
                return []
            # Unmatched for the whole limit: the game already committed this
            # attack, so holding the message freezes the screen forever
            # (g460 16:43-). Close it and let the battle be measured.
            mem.pop('boss_entry_hold', None)
            _record(mem, 'attack_observed', chart_step=step, general=general, castle=boss_cell,
                    expected_metric=None,
                    observed_metric={'general': general, 'message': text, 'sortie_match': match},
                    deviation_reason='boss_entry_hold_released',
                    reason=f'出撃注文と一致しない突入文を{BOSS_ENTRY_HOLD_LIMIT}回保留したため表示を閉じる')
            mem['attack'] = {'general': general, 'castle': boss_cell, 'side': 'attack',
                             'step': step, 'entry_evidence': 'measured_boss_entry'}
            return [pad('a')]
        _bind_sortie(mem, step, boss_cell)
        mem['attack'] = {'general': general, 'castle': boss_cell, 'side': 'attack', 'step': step,
                         'entry_evidence': 'measured_boss_entry'}
        _record(mem, 'attack_observed', chart_step=step, general=general, castle=boss_cell,
                expected_metric={'general': general}, observed_metric={'general': general, 'message': text},
                reason='実測済みのボス城突入文と出撃将軍が一致')
        return [pad('a')]
    m = ATTACK.search(text)
    if m:
        general, castle = m.groups()
        observe_chapter_castle(mem, castle)
        castle = _castle_label(mem, castle)
        launched = (mem.get('launched') or {}).get(castle) or {}
        current = mem.get('attack') or {}
        step, match = _match_sortie(mem, castle, general)
        if match != 'matched' and current.get('castle') == castle and current.get('general') == general:
            return [pad('a')]              # same entry text still on screen
        ours = step is not None or match == 'ambiguous' or general in (NAME, 'ヴィーナス', 'ココット', 'ゼウス')
        side = 'attack' if ours else 'enemy'
        _bind_sortie(mem, step, castle)
        _record(mem, 'attack_observed', chart_step=step, general=general, castle=castle,
                expected_metric=launched.get('general'), observed_metric=general,
                deviation_reason=(None if step or not ours
                                  else 'ambiguous_sortie' if match == 'ambiguous' else 'unplanned_attack'),
                reason='のりこんだ表示')
        mem['attack'] = {'general': general, 'castle': castle, 'side': side, 'step': step}
        return [pad('a')]
    m = DEFENSE.search(text)
    if m:
        castle = m.group(1)
        observe_chapter_castle(mem, castle)
        castle = _castle_label(mem, castle)
        if (mem.get('attack') or {}).get('castle') != castle or (mem.get('attack') or {}).get('side') != 'defense':
            _record(mem, 'defense_observed', castle=castle, reason='せめこまれました表示')
            mem['world_map_due'] = True      # the castle may have fallen without a battle
        mem['attack'] = {'general': None, 'castle': castle, 'side': 'defense', 'step': None}
        return [pad('a')]
    return None


def _enter_chapter(mem, chapter, *, reason, evidence=None):
    previous = mem.get('chapter')
    from .hanjuku_roster import fresh
    previous_roster = fresh(mem)
    if previous_roster and previous_roster.get('complete') is True:
        mem['recruit_payroll_pending'] = list(previous_roster['names'])
    # Route state belongs to the measured map of one chapter. Keep
    # run-wide counters/name evidence, never carry coordinates/orders.
    for key in ('active', 'anchor', 'goal_anchor_lock', 'attack', 'battle', 'battle_seen',
                'captured', 'card_override', 'rare_card_kit', 'strong_card_kit', 'rare_scan', 'rare_scan_month', 'cursor', 'egg_battle',
                'expect_menu', 'general_override', 'launched', 'menu_miss', 'month_exit', 'month_sub',
                'nav_last', 'nav_search', 'nav_search_leg', 'orders', 'picked', 'retries', 'retry_context', 'shop',
                'source_override', 'uncertain', 'month', 'order_context', 'sortie_general', 'sortie_actor_miss',
                'boss_defeated', 'boss_entry_hold',
                'chart_adjust', 'chart_plan', 'launched_orders', 'sorties', 'sortie_attempt',
                'garrison', 'general_location_unknown', 'recruit_verification', 'lost', 'owner_streak', 'source_miss', 'card_drop', 'card_miss',
                'nav_prev', 'nav_still', 'nav_pressed', 'unverified', 'off_castle',
                'target_miss', 'target_cancel', 'menu_hold', 'card_scroll', 'card_unreadable',
                'sortie_confirm_miss',
                'world_map_tick', 'world_map_due', 'world_map_wait', 'home_lost',
                'y_jump', 'y_jumps', 'y_jump_return', 'y_jumped', 'boss_absent', 'recall', 'recall_skip', 'recall_verification',
                'near_goal', 'align_steps', 'unanchored', 'select_tick',
                'house', 'house_scan_tick', 'house_scan_month', 'house_field_scan', 'house_eggs',
                'recruit_roster', 'recruit_roster_floor', 'recruit_roster_recheck', 'recruit_roster_attempts', 'recruit_field_scan_attempts', 'recruit_month_scan_attempts', 'castle_income', 'castle_ownership',
                'select_used', 'castle_verified', 'last_castle_hold',
                'egg_action', 'egg_key', 'egg_menu_stage', 'indep_menu',
                'indep_menu_key', 'indep_menu_action',
                'monster_menu_key', 'monster_menu_cursor', 'monster_menu_hold',
                'monster_menu_choice', 'monster_menu_choice_key', 'monster_panel'):
        mem.pop(key, None)
    mem['chapter'] = chapter
    mem['chapter_evidence'] = evidence or 'header'
    mem['variant'] = 'chart' if chart.orders(chapter) else 'chart_unavailable'
    _record(mem, 'chapter_seen', previous_stage=previous,
            observed_metric={'chapter': chapter, **({'evidence': evidence} if evidence else {})},
            resulting_stage=chapter,
            reason=reason)


def observe_chapter_castle(mem, castle):
    """Advance the chapter on a castle name that only the next chapter has.

    The month header reads 「2ねん5のつき」 (year, month) and never shows the
    chapter, so the header branch never fired: g421 beat クイーン at 14:41,
    fought at アルマムーン from 14:47 and navigated chapter 2 with chapter 1
    coordinates for 80 minutes (every Y jump and roof check missed).
    A name absent from this chapter and present in the next one is proof.
    """
    chapter = mem.get('chapter') or 0
    if not castle or not chapter:
        return
    here = set(chart.CASTLE_NAMES.get(chapter, ())) | {chart.home_castle(chapter), chart.boss_castle(chapter)}
    # The home castle is アルマムーン in every chapter (あるまむーん in 8), and the
    # chart now labels it the same way, so it can never read as "only the next
    # chapter has it" (g436 21:33: a defense of アルマムーン in chapter 1 advanced
    # the bot to chapter 2 cells and parked the cursor at sea).
    here |= {STATUS_NAMES.get(label, label) for label in here}
    if castle in here:
        return
    nxt = chapter + 1
    if castle in chart.CASTLE_NAMES.get(nxt, ()) or castle == chart.home_castle(nxt):
        _enter_chapter(mem, nxt, evidence=castle,
                       reason='次章にしかない城名を確認したため章を進め、前章の座標・出撃・購入状態を初期化')


def observe_chapter_general(mem, general):
    """Advance the chapter on an enemy general that only later chapters have.

    g454 08:24: どうし defeated クイーン, but the bot stayed on chapter 1 while
    ピオーネ and ヘラ (char.csv 話=2) attacked its castles. No chapter-2 castle
    name ever appeared, so ``observe_chapter_castle`` had no evidence and the
    bot navigated chapter 1 coordinates for 30+ minutes. The battle panel's
    enemy general is a chapter fact; a debut chapter later than the bot's
    current one proves the game advanced.
    """
    chapter = mem.get('chapter') or 0
    debut = general_debut_chapter(general)
    if not chapter or debut is None or debut <= chapter:
        return
    # The immediate next chapter's roster is proof by itself; a further jump
    # (a missed transition) needs this chapter's boss defeated as context.
    if debut != chapter + 1 and mem.get('boss_defeated') != chapter:
        return
    _enter_chapter(mem, debut, evidence=general,
                   reason='次章以降に初登場する敵将軍を確認したため章を進め、前章の座標・出撃・購入状態を初期化')


def _castle_label(mem, name):
    """The chart label for a castle name read from a message.

    The chart uses the game's own names, so this is the identity unless
    ``STATUS_NAMES`` holds a genuine alias; it existed because chapter 1's
    home castle was read as アルマムーン while the chart said ほんじょう.
    """
    cells = chart.castles(mem.get('chapter') or 0)
    if name in cells:
        return name
    return next((label for label, real in STATUS_NAMES.items() if real == name and label in cells), name)


def _repair_home_name_chapter(mem):
    """Undo the chapter 2 switch that only a defense of アルマムーン caused (v44-v60).

    Chapter entries now keep their evidence; a chapter 2 memory without it
    predates this fix, and the only such live game (g436, 21:33) was still
    in chapter 1. Its route state is reset for chapter 1 once.
    """
    # Only a game that v60 already saw in chapter 1 (its one-time repair flag)
    # and that then entered chapter 2 without recorded evidence.
    if ((mem.get('chapter') or 0) != 2 or 'chapter_evidence' in mem
            or not mem.get('home_alias_repaired')):
        return
    mem['chapter_evidence'] = 'reverted_home_name'
    _enter_chapter(mem, 1, evidence='reverted_home_name',
                   reason='本城の実名アルマムーンを次章の証拠と誤認して第2章にしていたため第1章へ戻す')
    mem['chapter_evidence'] = 'reverted_home_name'



# ---------------------------------------------------------------- month
def _month_key(header):
    return f"{header['year']}-{header['month']}" if header else None


# Measured cart prices. グリンボー/ミックミー/ブラッキー come from the base
# charts (2.md -66G/11個・-80G/2個, 3.md -63G/21個); the rest are measured.
KNOWN_PRICES = {'イッテツーン': 1, 'ノリウツール': 18, 'クースカン': 24, 'ゼンマイン': 32,
                'グリンボー': 6, 'ミックミー': 40, 'ブラッキー': 3}

# 兵士は1G=1人で2桁入力が上限。チャート計画の無い月の残金はここまでの補充に使う。
SOLDIER_CAP = 99
# The month boundary pays 収入 − 総賃金; ending a month at 0G forced a
# dismissal in g358 (-1G) and g407 (-3G, 6 generals lost). Keep this much
# gold unspent so a shortfall month cannot push the balance negative
# (owner 2026-09-28: そもそも将軍解雇はしないで欲しい).
WAGE_RESERVE = 30
# 月一の たまごのかいふく / しょうぐんぼしゅう (gcgx: どちらも50G)。
EGG_RECOVER_COST = 50
RECRUIT_COST = 50
# 月イチイベント「ゴニンジャー」(gcgx event.html: 50Gで敵将軍の暗殺を依頼できる。
# 成功率は低い)。隠密戦隊ごにんじゃーへの依頼料。
GONINJA_COST = 50
MONTH_SUB_LIMIT = 8              # A presses through an unmeasured sub-screen
# The month menu stays on screen for a few observations after the A press that
# opens a sub (g462 18:06:02-05); only that stale frame may not end the sub.
MONTH_SUB_MENU_WAIT = 4
RECRUIT_CANDIDATE_LIMIT = 32     # paid candidate introductions outlast the generic 8 observations
EGG_RITUAL_LIMIT = 64            # measured paid recovery includes a long chant


def _egg_row(screen):
    """(general, egg, uses) from card_select/sortie_confirm (measured rows:
    general at x=16 before しょうぐん, egg row「たまご エラベルエッグ 4」).
    たまごなし and unreadable counts return None."""
    general = egg = uses = None
    for line in screen.lines:
        cells = dict(line.spans())
        if cells.get(80) == 'しょうぐん' and cells.get(16):
            general = cells[16]
        if cells.get(16) == 'たまご' and cells.get(48) and (cells.get(112) or '').isdigit():
            egg, uses = cells[48], int(cells[112])
    if not general or UNKNOWN in general or egg is None:
        return None
    return general, egg, uses


def _egg_recovery_targets(mem):
    # Ordinary eggs recover to four, while one-shot/king eggs hold only one.
    types = mem.get('egg_types') or {}
    counts = mem.get('egg_uses') or {}
    targets = {name for name, uses in counts.items()
               if type(uses) is int and 0 <= uses < (
                   1 if types.get(name) in {'いっぱつエッグ', 'キングエッグ'} else 4)}
    # An attempted summon makes the sortie count stale. Check the recovery
    # screen; this flag is not proof of consumption and never decrements stock.
    targets.update(mem.get('egg_recheck') or [])
    return sorted(targets)


def _extras_reserve(mem, header):
    """Reserve the cost of all observed depleted eggs before soldiers.

    Owner rule (2026-09-28): with 50+ soldiers on hand (read from the
    「へいしすうはNめい」 line), egg recovery wins even when the gold is
    short of the full cost: hold ALL of it for the egg and buy no soldiers,
    so the balance grows until the recovery can run.

    The hero's depleted egg reserves first even without the soldier count:
    a castle defense cannot be retreated from and an eggless hero cannot
    answer a summon (g458 15:45: どうし 90 vs シェーブル 27, the enemy's egg
    summon came, the hero's egg was spent and he died -> game over).
    """
    targets = _egg_recovery_targets(mem)
    cost = EGG_RECOVER_COST * len(targets)
    gold = (header or {}).get('gold')
    # Owner (2026-09-29): 兵士の数を確認せず卵回復した - the army is read on the
    # soldier screen, so without this month's count the soldiers go first and
    # the eggs are recovered from what is left (_plan_extras: egg 'check').
    counted = (mem.get('soldiers_seen_key') == _month_key(header) if header else False)
    big_army = counted and (mem.get('soldiers_seen') or 0) >= 50
    if type(gold) is not int or gold < 0 or not cost:
        reserve = 0
    elif NAME in targets or big_army:
        # 卵へ温存（兵士は今月見送り）。賃金リザーブは守る。
        reserve = cost if gold >= cost else max(0, gold - WAGE_RESERVE)
    else:
        reserve = 0
    return reserve, not _charted_purchase_ahead(mem, header)


def _egg_recheck(mem):
    ally = (mem.get('battle') or {}).get('ally')
    if ally:
        pending = mem.setdefault('egg_recheck', [])
        if ally not in pending:
            pending.append(ally)


def _recruit_target(mem):
    from .hanjuku_roster import roles
    chapter = mem.get('chapter')
    expected = set(chart.CASTLE_NAMES.get(chapter, ())) - {chart.boss_castle(chapter)}
    owned = _owned(mem)
    # Unknown ownership/chapters are not zero castles; aliases count once.
    owned = {STATUS_NAMES.get(c, c) for c in owned}
    expected = {STATUS_NAMES.get(c, c) for c in expected}
    if not owned or not owned <= expected or type(chapter) is not int:
        return None
    raw = (mem.get('chart_plan') or {}).get('recruitment')
    if raw is None:
        raw = chart.RECRUITMENT_BY_CHAPTER.get(chapter, {'per_castle': 2, 'attack': 3})
    try:
        spec = roles(raw)
    except ValueError:
        return None
    target = spec['per_castle'] * len(owned) + spec['attack']
    from .hanjuku_roster import MAX_GENERALS
    return min(target, MAX_GENERALS) if target > 0 else None


def _recruit_sufficient(mem):
    from .hanjuku_roster import fresh
    seen, target = fresh(mem), _recruit_target(mem)
    return seen is not None and target is not None and len(seen['names']) >= target


def _stop_unneeded_recruit(mem, shop):
    from .hanjuku_roster import fresh
    if shop.get('recruit') in ('opened', 'done', 'unverified'):
        return True  # finish the existing transaction; do not invent a refund
    seen, target = fresh(mem), _recruit_target(mem)
    if seen is not None and target is not None and len(seen['names']) < target and seen.get('complete') is True:
        return False
    sufficient = _recruit_sufficient(mem)
    status = 'not_needed' if sufficient else 'deferred_roster'
    if shop.get('recruit') != status:
        _record(mem, 'recruit_not_needed' if sufficient else 'recruit_roster_unknown',
                month=shop.get('key'),
                observed_metric={'roster_lower_bound': len(seen['names']) if seen else None,
                                 'target': target, 'complete': seen.get('complete') if seen else False},
                reason=('新鮮な同一scanの実一覧で必要人数以上を確認したため募集を見送る' if sufficient
                        else '実在人数または必要人数が不明のため募集を保留し、実一覧を再確認する'))
    shop['recruit'] = status
    shop['recruit_priority'] = False
    mem['recruit_hold'] = {'chapter': mem.get('chapter'), 'month': shop.get('key'),
                          'reason': ('enough_observed' if sufficient
                                     else 'roster_incomplete' if seen and target is not None
                                     else 'roster_or_demand_unknown'), 'status': status}
    if sufficient:
        shop['recruit_reserve'] = 0
    else:
        mem['recruit_roster_recheck'] = True
    return True


def _recruit_shortage(mem):
    from .hanjuku_roster import fresh
    seen, target = fresh(mem), _recruit_target(mem)
    if (seen is None or target is None or seen.get('complete') is not True
            or len(seen['names']) >= target):
        return None
    return {'available_observed': list(seen['names']), 'count_lower_bound': len(seen['names']),
            'target': target, 'total_roster': len(seen['names']),
            'garrison': mem.get('garrison') or {}, 'placement': 'observed_only'}


def _stop_uneconomic_recruit(mem, shop):
    from .hanjuku_roster import economics
    if shop.get('recruit') in ('opened', 'done', 'unverified'):
        return False  # finish the existing conversation without guessing refunds
    owned = {STATUS_NAMES.get(c, c) for c in _owned(mem)}
    economy = economics(mem, owned)
    # Owner approved ordinary observed income plus the existing cash reserves.
    # Do not silently apply an unapproved permanent quarter-income factor.
    sustainable = economy and economy['income'] >= economy['wages'] + economy['additional_wage_max']
    if sustainable:
        mem.pop('recruit_hold', None)
        return False
    status = 'deferred_economy' if economy is None else 'not_affordable'
    if shop.get('recruit') != status:
        _record(mem, 'recruit_economy_held', month=shop.get('key'),
                observed_metric={'economy': economy,
                                 'pending_employees': mem.get('recruit_payroll_pending') or []},
                expected_metric='募集後も賃金を収入で支払えることを確認',
                reason=('実収入・同一scan総賃金が不明のため募集を保留し確認する' if economy is None
                        else '募集後の賃金を安全側の収入で賄えないため募集を見送る'))
    shop['recruit'] = status
    shop['recruit_priority'] = False
    mem['recruit_hold'] = {'chapter': mem.get('chapter'), 'month': shop.get('key'),
                          'reason': ('pending_employees' if mem.get('recruit_payroll_pending')
                                     else 'income_or_payroll_unknown' if economy is None else 'wages_exceed_income'),
                          'status': status}
    mem['recruit_roster_recheck'] = economy is None
    return True


def _month_held_reserve(shop):
    return shop.get('reserve', 0) + shop.get('hero_repair_reserve', 0)


def _reserve_hero_repair(mem, shop, gold):
    if not _hero_egg_broken(mem) or shop.get('hero_repair_reserve'):
        return
    shop['hero_repair_reserve'] = 50  # cheapest measured house gift; wage is separate
    items = [] if shop.get('merchant_done') else shop.get('items') or []
    card_cost = (sum(KNOWN_PRICES[n] * qty for n, qty in items)
                 if all(n in KNOWN_PRICES for n, _ in items) else None)
    if not shop.get('soldiers_done') and type(gold) is int and card_cost is not None:
        shop['soldiers'] = min(shop.get('soldiers', 0), max(0, gold - card_cost
                               - _month_held_reserve(shop) - WAGE_RESERVE
                               - shop.get('recruit_reserve', 0)))
        shop['soldiers_done'] = shop['soldiers'] == 0
    _record(mem, 'hero_repair_funds_reserved', month=shop.get('key'),
            observed_metric={'gold': gold, 'reserve': 50, 'soldiers': shop.get('soldiers')},
            reason='主人公の壊れた卵の家修復費を募集・兵士・築城へ使い切らず温存する')


def _prioritise_recruit(mem, shop, gold):
    """Once per month, reserve a recruitment fee before optional soldiers.

    Includes existing cached shops on hotload. Original card purchases and
    the existing egg/wage reserves are kept; unconfirmed deaths are not used.
    """
    if _stop_unneeded_recruit(mem, shop):
        return
    if shop.get('recruit_budget_version') == 2 and shop.get('recruit') == 'check':
        return
    shortage = _recruit_shortage(mem)
    if not shortage or _stop_uneconomic_recruit(mem, shop):
        return
    shop['recruit_budget_version'] = 2
    shop['recruit_priority'] = True
    if shop.get('recruit') not in ('opened', 'done', 'unverified'):
        shop['recruit'] = 'check'
    items = shop.get('items') or []
    card_cost = (sum(KNOWN_PRICES[n] * qty for n, qty in items)
                 if all(n in KNOWN_PRICES for n, _ in items) else None)
    # Only subtract pending card purchases. A cached post-merchant menu
    # already shows the remaining balance.
    if shop.get('merchant_done'):
        card_cost = 0
    available = (max(0, gold - card_cost - _month_held_reserve(shop) - WAGE_RESERVE)
                 if type(gold) is int and card_cost is not None else 0)
    budget = 0 if shop.get('recruit') == 'done' else min(RECRUIT_COST, available)
    shop['recruit_reserve'] = budget
    if not shop.get('soldiers_done'):
        if card_cost is not None:
            shop['soldiers'] = min(shop.get('soldiers', 0), max(0, available - budget))
        if shop['soldiers'] == 0:
            shop['soldiers_done'] = True
    _record(mem, 'recruit_priority_plan', month=shop.get('key'),
            observed_metric={**shortage, 'gold': gold, 'reserved': budget,
                             'soldiers': shop.get('soldiers')},
            reason='確認できる将軍が不足しているため募集費を兵士補充より先に確保する。総人数や死亡は未確定')


def _plan_extras(mem, shop, reserve, recruit):
    shop['reserve'] = reserve
    egg_cost = EGG_RECOVER_COST * len(_egg_recovery_targets(mem))
    shop['egg'] = 'pending' if reserve else ('check' if egg_cost else None)
    shop['egg_cost'] = egg_cost
    shop['recruit'] = 'check' if recruit else None
    shop['chikujou'] = 'check'
    _reserve_hero_repair(mem, shop, shop.get('gold_start'))
    _prioritise_recruit(mem, shop, shop.get('gold_start'))
    if reserve or recruit:
        _record(mem, 'month_extras_plan', chart_step='1-month', month=shop['key'],
                strategy_variant=shop.get('variant', 'chart'),
                observed_metric={'egg_uses': dict(mem.get('egg_uses') or {})},
                plan={'egg_recover': bool(reserve), 'egg_recover_budget': reserve,
                      'egg_recover_targets': _egg_recovery_targets(mem),
                      'recruit_if_left': RECRUIT_COST if recruit else None,
                      'recruit_before_soldiers': bool(shop.get('recruit_priority')),
                      'recruit_reserve': shop.get('recruit_reserve', 0)},
                reason=('将軍不足時は募集費を兵士より先に確保し、卵回復・賃金予備も維持する'
                        if shop.get('recruit_priority') else
                        '使用回数が減った卵の全回復費を兵士より先に確保し、兵士99人分の後に余りがあれば将軍を募集'))


def _adjusted_plan(mem, header, key):
    """Month purchases from an adopted adjusted chart (#1085 L2).

    Cards reuse the merchant flow (unlisted/unaffordable items are skipped
    and recorded there). An adjusted chart may buy cards whose price was never
    measured, so the soldier count here is only a provisional estimate: after
    the merchant, ``month_step`` recomputes it from the gold read on screen
    (``_recalc_soldiers``) before opening the refill. General recruitment has
    no measured menu yet, so it is recorded as a deviation and not attempted.
    """
    spec = (mem.get('chart_plan') or {}).get('purchases')
    if not spec or key != f'{spec["month"][0]}-{spec["month"][1]}':
        return None
    gold = header['gold']
    items = [list(i) for i in spec['cards']]
    target = min(spec['soldiers'], 99)
    unpriced = sorted({name for name, _ in items if name not in KNOWN_PRICES})
    reserve, recruit = _extras_reserve(mem, header)
    left = gold - sum(KNOWN_PRICES.get(name, 0) * qty for name, qty in items) - reserve
    soldiers = max(0, min(target, left - WAGE_RESERVE))
    shop = mem['shop'] = {'key': key, 'items': items, 'soldiers': soldiers, 'merchant_done': False,
                          'variant': 'chart_adjusted', 'soldiers_done': target == 0,
                          'soldiers_target': target, 'soldiers_from_gold': True,
                          'gold_start': gold}
    _plan_extras(mem, shop, reserve, recruit)
    _record(mem, 'month_plan', chart_step='adjusted-month', strategy_variant='chart_adjusted',
            month=key, gold=gold,
            plan={'cards': items, 'soldiers_provisional': soldiers, 'unpriced_cards': unpriced},
            deviation_reason=('recruit_owner_rule' if spec.get('generals') else None),
            expected_metric={'adjusted_cards': [list(i) for i in spec['cards']],
                             'adjusted_soldiers': spec['soldiers'],
                             'adjusted_generals': spec.get('generals', 0)},
            reason=spec.get('note') or '調整チャートの月次購入')
    return shop


def _recalc_soldiers(screen, mem, shop):
    """Adjusted plans: size the refill from the gold actually left after the
    merchant (1G per soldier, as in the base budget plan). Unreadable gold
    holds input rather than guessing."""
    gold = (screen.header or {}).get('gold')
    if type(gold) is not int:
        return False
    shop['soldiers'] = max(0, min(shop.get('soldiers_target', 0),
                                  gold - _month_held_reserve(shop) - shop.get('recruit_reserve', 0) - WAGE_RESERVE))
    shop['soldiers_done'] = shop['soldiers'] == 0
    shop['soldiers_recalculated'] = True
    _record(mem, 'soldier_plan_recalc', chart_step='adjusted-month',
            strategy_variant=shop.get('variant', 'chart_adjusted'), month=shop.get('key'),
            expected_metric={'soldiers_target': shop.get('soldiers_target')},
            observed_metric={'gold_after_merchant': gold, 'soldiers': shop['soldiers']},
            reason='商人での実購入後の所持金から兵士補充数を再計算')
    return True


def _charted_purchase_ahead(mem, header):
    """True while a charted month purchase still lies ahead.

    Its budget stays reserved: the chart's own soldier count caps the refill
    there, and a month the chart does not cover buys nothing at all.
    """
    if not header:
        return False
    here = (header['year'], header['month'])
    planned = [tuple(p.get('month') or ()) for p in chart.purchases(mem.get('chapter') or 0)]
    adjusted = ((mem.get('chart_plan') or {}).get('purchases') or {}).get('month')
    if adjusted:
        planned.append(tuple(adjusted))
    return any(plan > here for plan in planned)


def _plan(mem, header):
    key = _month_key(header)
    shop = mem.get('shop')
    if shop and shop.get('key') == key:
        _reserve_hero_repair(mem, shop, (header or {}).get('gold'))
        return shop
    adjusted = _adjusted_plan(mem, header, key) if header else None
    if adjusted:
        return adjusted
    spec = None
    if header:
        spec = chart.purchase_for(mem.get('chapter') or 0, header['year'], header['month'])
    ahead = _charted_purchase_ahead(mem, header)
    if not spec:
        if ahead:
            reserve, _ = _extras_reserve(mem, header)
            if not reserve and not _recruit_shortage(mem):
                return None
            shop = mem['shop'] = {'key': key, 'items': [], 'soldiers': 0,
                'merchant_done': True, 'soldiers_done': True, 'gold_start': header['gold'],
                'variant': 'egg_recovery_only'}
            _plan_extras(mem, shop, reserve, False)
            return shop
        return _soldier_refill_plan(mem, header, key)
    gold = header['gold']
    if gold >= spec['chart_gold']:
        items = [list(i) for i in spec['cards']]
        variant, deviation = 'chart', None
        # Unpriced cards would overstate the gold left for soldiers.
        leftover = (gold - sum(KNOWN_PRICES[name] * qty for name, qty in items)
                    if all(name in KNOWN_PRICES for name, _ in items) else None)
    else:
        items, left = [], gold
        for name, qty in spec['priority']:
            if name not in KNOWN_PRICES:
                break
            n = min(qty, left // KNOWN_PRICES[name])
            if n:
                items.append([name, n])
                left -= n * KNOWN_PRICES[name]
        leftover = left
        variant = 'budget_boss_kit_first'
        deviation = f"所持金{gold}Gがチャート想定{spec['chart_gold']}G未満"
    # With a later charted purchase ahead, its budget wins: the chart's own
    # soldier count is the ceiling. Otherwise the gold left goes to soldiers.
    limit = spec['soldiers'] if ahead else SOLDIER_CAP
    reserve, recruit = _extras_reserve(mem, header)
    if leftover is None:
        reserve = 0          # unpriced cards: no measured room for the egg
    soldiers = (spec['soldiers'] if leftover is None
                else max(0, min(limit, leftover - reserve - WAGE_RESERVE)))
    shop = mem['shop'] = {'key': key, 'items': items, 'soldiers': soldiers, 'merchant_done': False,
                          'variant': variant,
                          'soldiers_done': soldiers == 0, 'gold_start': gold}
    _plan_extras(mem, shop, reserve, recruit)
    _record(mem, 'month_plan', chart_step='1-month', strategy_variant=variant, month=key, gold=gold,
            plan={'cards': items, 'soldiers': soldiers}, deviation_reason=deviation,
            expected_metric={'chart_cards': [list(i) for i in spec['cards']],
                             'chart_soldiers': spec['soldiers'], 'chart_gold': spec['chart_gold']},
            reason=spec['note'])
    return shop


def _soldier_refill_plan(mem, header, key):
    """Months the chart has no purchase for: spend the gold left on soldiers.

    The owner policy (2026-09-25) is 毎月・残金で99人まで: without this the
    month menu exits before へいしほじゅう and the army empties.
    """
    if not header:
        return None
    gold = header['gold']
    reserve, recruit = _extras_reserve(mem, header)
    soldiers = min(SOLDIER_CAP, max(0, gold - reserve - WAGE_RESERVE))
    shop = mem['shop'] = {'key': key, 'items': [], 'soldiers': soldiers, 'merchant_done': False,
                          'variant': 'soldier_refill_only', 'soldiers_done': soldiers == 0,
                          'gold_start': gold}
    _plan_extras(mem, shop, reserve, recruit)
    _record(mem, 'month_plan', chart_step='1-month', strategy_variant='soldier_refill_only',
            month=key, gold=gold, plan={'cards': [], 'soldiers': soldiers},
            deviation_reason='chart_month_uncovered',
            expected_metric={'soldier_cap': SOLDIER_CAP},
            reason='チャートに当月の購入計画がないため残金で兵士を補充')
    return shop


RECRUIT_INTRO = 'ども!しょうぐんえんごかいのものです。しょうぐんのぼしゅうでございますね?'
RECRUIT_GOODBYE = 'それではまたのきかいに。ごようのさいはいつでもおまかせを。'


def _month_dialog_body(screen):
    return ''.join(line.known.replace(' ', '') for line in screen.lines if line.y >= 175)


def month_menu_ready(screen):
    """The recruitment overlay can retain the background menu's upper hand."""
    return (screen.kind == 'month_menu' and bool(screen.hand and screen.hand[1] < 120)
            and _month_dialog_body(screen) not in (RECRUIT_INTRO, RECRUIT_GOODBYE))


def month_step(screen: Screen, mem):
    shop = _plan(mem, screen.header)
    body = _month_dialog_body(screen)
    if shop:
        _prioritise_recruit(mem, shop, (screen.header or {}).get('gold'))
    if body == RECRUIT_GOODBYE:
        _record(mem, 'month_recruit_goodbye', reason='実測した募集終了文を閉じて本メニューへ戻る')
        return [pad('a')]
    if (body == RECRUIT_INTRO
            and all(UNKNOWN not in line.span(8, 248) for line in screen.lines if line.y in (183, 199))):
        # g496: the real background hand remained visible, so a hand alone
        # could neither prove menu return nor rule out this measured dialogue.
        gold = (screen.header or {}).get('gold')
        if (shop and shop.get('recruit') == 'unverified' and type(gold) is int
                and gold >= RECRUIT_COST and (shop.get('recruit_priority')
                                             or shop.get('soldiers', 0) >= SOLDIER_CAP)):
            mem['month_sub'] = {'kind': 'recruit', 'gold_before': gold, 'presses': 0,
                                'key': shop['key'], 'left_menu': False}
            shop['recruit'] = 'opened'
            _record(mem, 'month_sub_resumed', choice='しょうぐんぼしゅう',
                    reason='実測した募集導入文と費用条件が一致したため失われた会話追跡を再開')
            return month_sub_step(screen, mem)
        return []
    if screen.has('じゅうじキー'):
        return quantity_step(screen, mem, soldiers=True)
    if screen.has('うむッ') and screen.has('いかんッ'):
        # "よろしいですかな?" after も〜おしまい!: confirm only our own exit.
        choice = 'うむッ!' if mem.get('month_exit') or mem.get('month_sub') else 'いかんッ!'
        move = menu_to(screen, choice)
        if move == 'here':
            if choice == 'うむッ!':
                mem['month_exit'] = False
            _record(mem, 'month_confirm', choice=choice, reason='月初メニュー終了の確認')
            return [pad('a')]
        return [move] if move else [pad('b')]
    if shop and not shop['merchant_done'] and shop['items']:
        move = menu_to(screen, 'しょうにん')
        return [pad('a')] if move == 'here' else [move] if move else []
    if (mem.get('month_sub', {}).get('kind') == 'egg'
            and screen.has('おはらいのひつような') and screen.has('たまごはありませんぞ')):
        mem.pop('month_sub')
        mem.pop('egg_recheck', None)
        mem['egg_uses'] = {}  # the game says none need recovery; reobserve quantities
        if shop:
            shop['egg'] = 'not_needed'
        _record(mem, 'egg_recover_not_needed', reason='ゲームが回復不要と表示したため支払わず説明を閉じる')
        return [pad('a')]
    # Finish the tracked egg/recruit flow before opening another one;
    # after an egg-cost deferral recruitment is still pending here.
    if mem.get('month_sub') and not _finish_month_sub(screen, mem, shop):
        return []
    if (shop and shop.get('recruit_priority') and not shop.get('recruit_measured_budget')
            and not mem.get('month_sub')):
        gold = (screen.header or {}).get('gold')
        if type(gold) is not int:
            return []
        shop['recruit_reserve'] = (0 if shop.get('recruit') == 'done' else
                                   min(RECRUIT_COST, max(0, gold - _month_held_reserve(shop) - WAGE_RESERVE)))
        if not shop.get('soldiers_done'):
            shop['soldiers'] = min(shop.get('soldiers', 0),
                                   max(0, gold - _month_held_reserve(shop)
                                       - shop['recruit_reserve'] - WAGE_RESERVE))
            shop['soldiers_done'] = shop['soldiers'] == 0
        shop['recruit_measured_budget'] = True
    if (shop and shop.get('soldiers_from_gold') and not shop.get('soldiers_recalculated')
            and not shop['soldiers_done']):
        if not _recalc_soldiers(screen, mem, shop):
            return []
    if (shop and shop.get('recruit_priority')
            and shop.get('recruit') in ('check', 'pending')):
        extra = _month_extra(screen, mem, shop, recruit_only=True)
        if extra is not None:
            return extra
    if mem.get('month_sub') and mem['month_sub'].get('kind') == 'recruit':
        if not _finish_month_sub(screen, mem, shop):
            return []
    if shop and not shop['soldiers_done']:
        move = menu_to(screen, 'へいしほじゅう')
        return [pad('a')] if move == 'here' else [move] if move else []
    if mem.get('month_sub') and not _finish_month_sub(screen, mem, shop):
        # g462 18:06:04: the menu frame still predates the game's reaction to
        # the A press that opened the sub. Pressing anything here (も〜おしまい!
        # navigation) races the flow that is starting, so hold instead.
        return []
    extra = _month_extra(screen, mem, shop)
    if extra is not None:
        return extra
    if shop and not shop.get('closed'):
        shop['closed'] = True
        _record(mem, 'month_done', month=shop['key'], gold_after=(screen.header or {}).get('gold'),
                observed_metric={'bought': shop.get('bought', []),
                                 'gold_start': shop['gold_start'],
                                 'gold_end': (screen.header or {}).get('gold')},
                reason='月一の購入を終了')
    move = menu_to(screen, 'も〜おしまい!')
    if move == 'here':
        mem['month_exit'] = True
        return [pad('a')]
    return [move] if move else []


def _month_extra(screen, mem, shop, *, recruit_only=False):
    """Recruit before soldiers when short; otherwise keep the old extras order."""
    if not shop:
        return None
    gold = (screen.header or {}).get('gold')
    if not _stop_unneeded_recruit(mem, shop):
        _stop_uneconomic_recruit(mem, shop)
    for sub, label, cost in (('egg', 'たまごのかいふく', shop.get('reserve') or EGG_RECOVER_COST),
                             ('recruit', 'しょうぐんぼしゅう', RECRUIT_COST)):
        if recruit_only and sub != 'recruit':
            continue
        status = shop.get(sub)
        if status not in ('pending', 'check'):
            continue
        if sub == 'egg' and status == 'check':
            # After the soldiers: recover the eggs only from what is left.
            cost = shop.get('egg_cost') or EGG_RECOVER_COST
            if type(gold) is not int or gold < cost + WAGE_RESERVE + shop.get('hero_repair_reserve', 0):
                shop[sub] = 'skipped'
                _record(mem, 'egg_recover_skip', month=shop.get('key'), gold=gold,
                        observed_metric={'cost': cost, 'gold': gold},
                        reason='兵士補充の後の残金が卵の回復費と賃金リザーブに足りないため見送る')
                continue
        elif status == 'check':
            # Owner rule (2026-09-27): recruit only when the soldiers got
            # their full 99 and the fee plus the wage reserve is still left.
            if (type(gold) is not int or (not shop.get('recruit_priority') and shop.get('soldiers', 0) < SOLDIER_CAP)
                    or gold < cost + WAGE_RESERVE + shop.get('hero_repair_reserve', 0)
                    + (shop.get('reserve', 0) if shop.get('recruit_priority')
                       and shop.get('egg') in ('pending', 'check') else 0)):
                if (shop.get('recruit_priority') and type(gold) is int
                        and gold >= RECRUIT_COST + WAGE_RESERVE + shop.get('hero_repair_reserve', 0)
                        and shop.get('egg') in ('pending', 'check')):
                    # g498: estimate 150G, actual recovery only 50G; the
                    # early check must not permanently skip recruitment.
                    if not shop.get('recruit_deferred_egg'):
                        shop['recruit_deferred_egg'] = True
                        _record(mem, 'recruit_deferred_egg', month=shop.get('key'),
                                observed_metric={'gold': gold, 'egg_reserve': shop.get('reserve')},
                                reason='卵回復費は見積もりのため、回復後の実残金で募集を再判定する')
                    continue
                shop[sub] = 'skipped'
                _record(mem, 'recruit_skip', month=shop.get('key'), gold=gold,
                        observed_metric={'soldiers': shop.get('soldiers'), 'gold': gold},
                        reason=('募集費と卵回復・賃金リザーブが足りないため資金を温存して次月へ待つ'
                                if shop.get('recruit_priority') else
                                '兵士99人分と賃金リザーブの後に募集費が残っていないため将軍を募集しない'))
                continue
        elif (type(gold) is not int or gold < cost + shop.get('hero_repair_reserve', 0)
              + (WAGE_RESERVE if shop.get('hero_repair_reserve') else 0)):
            shop[sub] = 'skipped'
            _record(mem, 'egg_recover_skip', month=shop.get('key'), gold=gold,
                    reason='所持金が卵の回復費に足りないため見送る')
            continue
        move = menu_to(screen, label)
        if move == 'here':
            shop[sub] = 'opened'
            mem['month_sub'] = {'kind': sub, 'gold_before': gold, 'presses': 0, 'key': shop.get('key'),
                                'left_menu': False}
            if sub == 'recruit':
                mem['month_sub']['generals_before'] = sorted({
                    g for gs in (mem.get('garrison') or {}).values() for g in gs or ()})
            _record(mem, 'month_sub_open', month=shop.get('key'), gold=gold, choice=label,
                    reason=f'{label}を選択')
            return [pad('a')]
        if move is None:
            if sub == 'recruit' and shop.get('recruit_priority'):
                # g508 1-8: the first menu/cursor reading was incomplete;
                # skipping immediately sent all optional money to soldiers.
                waits = int(shop.get('recruit_menu_wait') or 0) + 1
                shop['recruit_menu_wait'] = waits
                if waits <= 6:
                    if waits == 1:
                        _record(mem, 'recruit_menu_wait', month=shop.get('key'),
                                reason='将軍不足時の募集欄・カーソルを有限回待ち、兵士補充を先に始めない')
                    return []
                shop[sub] = 'unverified'
                _record(mem, 'recruit_menu_unconfirmed', month=shop.get('key'),
                        observed_metric={'waits': waits},
                        reason='募集欄を有限回待っても読めないため実行を未確認とし、募集費は温存する')
                continue
            shop[sub] = 'skipped'
            _record(mem, 'situation_held', screen=screen.kind, choice=label,
                    reason=f'月一メニューに{label}が読めないため見送る')
            continue
        if sub == 'recruit':
            shop.pop('recruit_menu_wait', None)
        return [move]
    return None if recruit_only else _month_chikujou(screen, mem, shop)


CHIKUJOU_SPARE = 40            # gold beyond the wage reserve before a castle is upgraded


def _month_chikujou(screen, mem, shop):
    """Owner (2026-09-29): with money to spare, ちくじょう raises a castle's level.

    wikiwiki 城: a defender's egg monster gains +level defense and speed and a
    defending general +level charge speed. Measured (isolated probe, chapter
    2): ちくじょう lists our castles (the first row is the home castle),
    「NGかかりますがよろしいですかな」 asks うむッ!/いかんッ!, and one level per
    month is allowed (「これいじょうのぞうちくはできませんぞ!!」).
    """
    if shop.get('chikujou') != 'check':
        return None
    gold = (screen.header or {}).get('gold')
    if type(gold) is not int or gold < WAGE_RESERVE + CHIKUJOU_SPARE + shop.get('recruit_reserve', 0) + shop.get('hero_repair_reserve', 0):
        shop['chikujou'] = 'skipped'
        return None
    move = menu_to(screen, 'ちくじょう')
    if move is None:
        shop['chikujou'] = 'skipped'
        return None
    if move != 'here':
        return [move]
    shop['chikujou'] = 'opened'
    mem['month_sub'] = {'kind': 'chikujou', 'gold_before': gold, 'presses': 0, 'key': shop.get('key'),
                        'left_menu': False}
    _record(mem, 'month_sub_open', month=shop.get('key'), gold=gold, choice='ちくじょう',
            reason='所持金に余裕があるため、ちくじょうで城の防衛力を上げる')
    return [pad('a')]


def _chikujou_step(screen, mem, sub):
    text = screen.text
    if screen.has('うむッ') and screen.has('いかんッ'):
        quote = re.search(r'(\d+)Gかかりますが', text)
        gold = (screen.header or {}).get('gold')
        cost = int(quote[1]) if quote else None
        if (cost is None or type(gold) is not int or gold - cost < WAGE_RESERVE + (mem.get('shop') or {}).get('hero_repair_reserve', 0)
                + (mem.get('shop') or {}).get('recruit_reserve', 0)):
            sub['declined'] = True
            move = menu_to(screen, 'いかんッ!')
            _record(mem, 'chikujou_declined', observed_metric={'cost': cost, 'gold': gold},
                    reason='ちくじょう費用を払うと賃金リザーブを割るか費用が読めないため見送る')
            return [pad('a')] if move == 'here' else [move] if move else [pad('b')]
        move = menu_to(screen, 'うむッ!')
        if move == 'here':
            sub['quoted_cost'] = cost
            _record(mem, 'chikujou_confirm', observed_metric={'cost': cost, 'gold': gold},
                    reason=f'{cost}Gで城のレベルを上げる')
            return [pad('a')]
        return [move] if move else []
    if 'になりましたぞ' in text:
        sub['upgraded'] = True
        return [pad('a')]
    if sub.get('upgraded') or sub.get('declined'):
        return [pad('b')]              # keep the existing one-success spending budget
    if 'これいじょう' in text:
        # A refusal applies to the chosen castle, not every castle. Never
        # infer a permanent level cap (it can also mean upgraded this month).
        chosen = sub.get('chosen')
        if sub.get('rejected_frame') == text:
            if chosen:
                # Input may not have taken effect yet. Do not attribute the
                # previous castle's unchanged refusal to our next candidate.
                sub['rejected_wait'] = sub.get('rejected_wait', 0) + 1
                if sub['rejected_wait'] >= 6:
                    sub['declined'] = True
                    return [pad('b')]
                return []
        elif chosen:
            tried = sub.setdefault('rows_tried', [])
            if chosen not in tried:
                tried.append(chosen)
            sub['rejected_frame'] = text
            sub.pop('chosen', None)
            sub.pop('quoted_cost', None)
            sub['rejected_wait'] = 0
            _record(mem, 'chikujou_castle_unavailable',
                    observed_metric={'castle': chosen},
                    reason='選択した城が増築不可のため、その城を今回の候補から外して次を探す')
        else:
            # Lost selection context cannot identify which castle was refused.
            sub['declined'] = True
            return [pad('b')]
    else:
        sub.pop('rejected_frame', None)
        sub.pop('rejected_wait', None)
    if 'ぞうちく' in text:
        # g438 03:37: the first row was ジョンリギ with nobody inside
        # (「しょうぐんがおりませなんだ」) and the upgrade never happened. Pick the
        # home castle, else a castle a general is known to hold.
        chapter = mem.get('chapter') or 0
        home = chart.home_castle(chapter)
        names = [STATUS_NAMES.get(home, home)] + [
            STATUS_NAMES.get(c, c) for c, gs in (mem.get('garrison') or {}).items() if gs and c != home]
        tried = sub.setdefault('rows_tried', [])
        for name in names:
            if name in tried:
                continue
            move = menu_to(screen, name)
            if move is None:
                tried.append(name)
                continue
            if move == 'here':
                if 'おりませなんだ' in text and sub.get('chosen') == name:
                    tried.append(name)     # nobody there: try the next castle
                    continue
                sub['chosen'] = name
                return [pad('a')]
            return [move]
        sub['declined'] = True
        _record(mem, 'chikujou_declined', observed_metric={'tried': tried},
                reason='将軍のいる城を一覧で選べないため、ちくじょうを見送る')
        return [pad('b')]
    return []


def _finish_month_sub(screen, mem, shop) -> bool:
    """Back on the month menu: judge the sub-action by the gold it cost.

    Returns False while the sub must stay tracked. g462 18:06:02-18:11:11: the
    menu frame was still on screen when ちくじょう opened, so the sub ended
    before the game reacted; the castle list and its confirm then ran through
    the generic paths and the 「これいじょうのぞうちく」 exit screen repeated A
    for 300 s until the run watchdog ended the corner. Defer while the menu is
    unchanged (nothing judged yet): finish once the flow left the menu, the
    payment is readable, or MONTH_SUB_MENU_WAIT stale menu frames passed.
    """
    sub = mem.get('month_sub')
    if sub is None:
        return True
    gold = (screen.header or {}).get('gold')
    before = sub.get('gold_before')
    cost = sub.get('quoted_cost') if sub['kind'] in ('egg', 'chikujou') else RECRUIT_COST
    paid = (type(cost) is int and cost > 0 and type(gold) is int and type(before) is int
            and before - gold == cost
            and (sub['kind'] != 'egg' or sub.get('full_selected') is True))
    if sub['kind'] == 'recruit':
        receipt = sub.get('recruit_paid_gold')
        # g498: the 235 ->185 fee was observed in the candidate dialogue;
        # the final menu was 183, so later deductions cannot erase that fee.
        paid = paid or (type(receipt) is int and type(before) is int
                        and before - receipt == RECRUIT_COST)
    if sub.get('left_menu') is False and not paid and not sub.get('declined'):
        wait = int(sub.get('menu_wait', 0)) + 1
        sub['menu_wait'] = wait
        if wait < MONTH_SUB_MENU_WAIT:
            return False
    sub = mem.pop('month_sub')
    if shop and shop.get(sub['kind']) == 'opened':
        shop[sub['kind']] = 'done' if paid else 'unverified'
    if sub['kind'] == 'recruit':
        from .hanjuku_roster import invalidate
        invalidate(mem)  # payment/return never proves an extra general
        if shop and paid:
            shop['recruit_reserve'] = 0
        # A recruit joins the hero's castle, which need not be home.
        # Invalidate its old list; observe the candidate there before claiming
        # admission. Payment alone is not a roster increase.
        garrison = mem.get('garrison') or {}
        placements = {c for c, gs in garrison.items() if NAME in (gs or ())}
        placements.add(chart.home_castle(mem.get('chapter') or 0))
        for c in placements:
            garrison.pop(c, None)
        if paid:
            mem['recruit_verification'] = {
                'month': sub.get('key'), 'candidates': sorted(set(sub.get('candidate_names', []))
                                                           | set(sub.get('joined_names', []))),
                'generals_before': sub.get('generals_before', []), 'placement': 'unclassified'}
    if (shop and type(gold) is int and not shop.get('soldiers_done')
            and (shop.get('hero_repair_reserve') or (paid and sub['kind'] == 'recruit'))):
        # g508: a confirmed 50G fee plus a further 12G deduction left 94G;
        # the original 76 soldiers then spent the wage reserve down to 18G.
        # All paid recruits need the actual balance, not only broken heroes.
        previous_soldiers = shop.get('soldiers', 0)
        shop['soldiers'] = min(previous_soldiers, max(0, gold
                               - _month_held_reserve(shop) - WAGE_RESERVE
                               - shop.get('recruit_reserve', 0)))
        shop['soldiers_done'] = shop['soldiers'] == 0
        if paid and sub['kind'] == 'recruit':
            _record(mem, 'soldier_budget_after_recruit', month=sub.get('key'),
                    observed_metric={'gold_after': gold, 'soldiers_before': previous_soldiers,
                                     'soldiers': shop['soldiers'], 'wage_reserve': WAGE_RESERVE},
                    reason='募集後の実残金から兵士予算を再計算し、追加出費で賃金予備を使い込まない')
    if paid and sub['kind'] == 'egg':
        mem['egg_uses'] = {}      # counts are re-read at the next sorties
        mem.pop('egg_recheck', None)
    if sub['kind'] == 'chikujou':
        _record(mem, 'chikujou', month=sub.get('key'),
                observed_metric={'gold_before': before, 'gold_after': gold, 'quoted_cost': cost,
                                 'declined': sub.get('declined', False)},
                deviation_reason=None if paid or sub.get('declined') else 'cost_not_observed',
                reason='月一メニュー復帰時の所持金でちくじょうの支払いを確認' if paid
                else 'ちくじょうを見送った／支払いを確認できない')
        return True
    _record(mem, 'egg_recover' if sub['kind'] == 'egg' else 'recruit', month=sub.get('key'),
            strategy_variant='recruit_default_cursor' if sub['kind'] == 'recruit' else 'egg_recover',
            observed_metric={'gold_before': before, 'gold_after': gold, 'presses': sub.get('presses'),
                             'aborted': sub.get('aborted', False),
                             'full_selected': sub.get('full_selected'), 'quoted_cost': cost,
                             'fee_receipt_gold': sub.get('recruit_paid_gold'),
                             'joined_announced': sub.get('joined_names', [])},
            deviation_reason=None if paid else 'cost_not_observed',
            reason='月一メニュー復帰時の所持金で実行を確認' if paid
            else '月一メニューに戻ったが所持金の減少を確認できない')
    return True


CHIKUJOU_LEFTOVER = ('これいじょうのぞうちく', 'ぞうちくなさいます')


def chikujou_leftover(screen, mem) -> bool:
    """An untracked ちくじょう overlay: only B leaves it.

    g462 18:06:10-18:11: the sub had been closed early, so the castle list
    (「これいじょうのぞうちくはできませんぞ!! どのしろをぞうちくなさいますか?」)
    fell through to legacy A presses that changed nothing for 300 s and the run
    watchdog ended the corner. Measured screens: kind text, phrase above.
    """
    if mem.get('month_sub'):
        return False
    if not any(phrase in screen.text for phrase in CHIKUJOU_LEFTOVER):
        mem.pop('chikujou_leftover', None)
        return False
    if not mem.get('chikujou_leftover'):
        mem['chikujou_leftover'] = True
        _record(mem, 'chikujou_leftover', screen=screen.kind,
                observed_metric={'text': screen.text[-60:]},
                deviation_reason='chikujou_state_lost',
                reason='ちくじょうの画面を方策状態なしで確認したためBで閉じる')
    return True


def _paid_recruit_candidate(screen, sub):
    """Measured candidate biography + paid recruitment receipt, not a blank fade."""
    gold = (screen.header or {}).get('gold')
    before = sub.get('gold_before')
    if sub.get('kind') != 'recruit' or type(gold) is not int or type(before) is not int or before - gold != RECRUIT_COST:
        return None
    body = ''.join(line.span(8, 248).replace(' ', '') for line in screen.lines if line.y == 151)
    match = re.fullmatch(r'「わたしのなは([^\ufffd]+)ともうします。', body)
    words = ''.join(line.known.replace(' ', '') for line in screen.lines if line.y >= 167)
    if (screen.kind != 'text' or not match or not all(w in words for w in ('HP', 'たまご', 'せんとう', 'ないせい', 'ちんぎん'))):
        return None
    return match[1]


def month_sub_step(screen: Screen, mem):
    """Screens inside measured egg recovery / unmeasured general recruitment.

    Recruitment advances with A (first audition candidate, owner
    rule) and うむッ! on confirmations, at most MONTH_SUB_LIMIT presses; then
    B out and record. A map or battle means the month is over: drop it.
    """
    sub = mem['month_sub']
    kind = screen.kind
    if kind != 'month_menu':
        sub['left_menu'] = True     # the flow really left the month menu
    if kind in MONTH_SUB_EXIT_KINDS:
        mem.pop('month_sub')
        _record(mem, 'month_sub_lost', screen=kind, observed_metric=sub,
                reason='月一の実行中に月一メニューへ戻らず別画面になったため追跡をやめる')
        return None
    candidate = _paid_recruit_candidate(screen, sub)
    if candidate:
        sub['recruit_paid_gold'] = screen.header['gold']
    if sub['kind'] == 'recruit' and sub.get('recruit_paid_gold') is not None:
        for line in screen.lines:
            joined = re.fullmatch(r'([^�]+)がはいかにくわわった!', line.known.replace(' ', '').replace('！', '!'))
            if joined and joined[1] not in sub.setdefault('joined_names', []):
                sub['joined_names'].append(joined[1])
                from .hanjuku_roster import invalidate
                invalidate(mem)
                _record(mem, 'recruit_join_announced', general=joined[1], month=sub.get('key'),
                        observed_metric={'placement': 'unclassified'},
                        reason='募集の実加入告知を確認。配置は次の在城一覧で照合する')
    if candidate and candidate not in sub.setdefault('candidate_names', []):
        sub['candidate_names'].append(candidate)
    if candidate and not sub.get('paid_candidates'):
        # v16 may already have entered the old 8-observation abort. The
        # measured paid candidate screen is new progress, so recover once;
        # repeated/unknown screens can never reset this second bound again.
        sub['paid_candidates'] = True
        sub['presses'] = 0
        sub.pop('aborted', None)
        _record(mem, 'recruit_candidates_seen', general=candidate,
                reason='募集費50G支払い後の候補紹介を実測し、上限付き候補選択へ進む')
    sub['presses'] = int(sub.get('presses', 0)) + 1
    limit = (EGG_RITUAL_LIMIT if sub.get('stage') == 'recovering' else
             RECRUIT_CANDIDATE_LIMIT if sub.get('paid_candidates') else MONTH_SUB_LIMIT)
    if sub.get('aborted') or sub['presses'] > limit:
        if not sub.get('aborted'):
            sub['aborted'] = True
            _record(mem, 'month_sub_abort', screen=kind, observed_metric={'text': screen.text[-40:]},
                    reason='月一の未測定画面が上限回数で終わらないためBで離脱')
        return [pad('b')]
    if sub['kind'] == 'egg':
        return _egg_recovery_step(screen, mem, sub)
    if sub['kind'] == 'chikujou':
        return _chikujou_step(screen, mem, sub)
    if kind == 'yes_no' or (screen.has('うむッ') and screen.has('いかんッ')):
        move = menu_to(screen, 'うむッ!')
        if move and move != 'here':
            return [move]
    _record(mem, 'month_sub_step', screen=kind, choice=screen.selected,
            observed_metric={'kind': sub['kind'], 'presses': sub['presses'], 'text': screen.text[-60:]},
            reason='月一の実行画面を既定カーソルのまま決定')
    return [pad('a')]


def _egg_recovery_step(screen, mem, sub):
    """Measured full-recovery list and NこでNG confirmation; never guess a row."""
    if not screen.text:
        # The paid ritual fades back into the month menu. A on that blank
        # frame can be buffered and open the newly focused merchant row.
        return []
    if screen.has('うむッ') and screen.has('いかんッ'):
        body = ''.join(line.known.replace(' ', '') for line in screen.lines if line.y >= 140)
        quote = re.search(r'(\d+)こで(\d+)Gになりまんな', body)
        gold = (screen.header or {}).get('gold')
        if (not sub.get('full_selected') or not quote
                or int(quote[1]) <= 0 or int(quote[2]) != int(quote[1]) * EGG_RECOVER_COST
                or type(gold) is not int or gold < int(quote[2])
                + (mem.get('shop') or {}).get('hero_repair_reserve', 0)
                + (WAGE_RESERVE if (mem.get('shop') or {}).get('hero_repair_reserve') else 0)):
            sub['aborted'] = True
            _record(mem, 'egg_recover_skip', observed_metric={'gold': gold, 'quote_read': bool(quote)},
                    reason='全回復の選択・費用・所持金を確認できないか、費用が足りないため戻る')
            return [pad('b')]
        move = menu_to(screen, 'うむッ!')
        if move == 'here':
            sub.update(quoted_cost=int(quote[2]), stage='recovering')
            _record(mem, 'egg_recover_confirm', choice='ぜんかいふく',
                    observed_metric={'eggs': int(quote[1]), 'cost': int(quote[2]), 'gold': gold},
                    reason='全回復の表示費用が所持金以内なので決定。回復完了は未確定')
            return [pad('a')]
        return [move] if move else []
    if sub.get('stage') != 'recovering' and any(
            x == 32 and y == 47 and word == 'ぜんかいふく' for x, y, word in _options(screen)):
        move = menu_to(screen, 'ぜんかいふく')
        if move == 'here':
            sub['full_selected'] = True
            _record(mem, 'egg_recover_select', choice='ぜんかいふく',
                    reason='残数が0でない卵も含めて全回復を選択し、次画面で費用を確認')
            return [pad('a')]
        return [move] if move else []
    if screen.hand and sub.get('stage') != 'recovering':
        return []  # unknown option: no default A on an individual egg
    return [pad('a')]  # bounded introduction/paid ritual dialogue


MONTH_SUB_EXIT_KINDS = frozenset({'map', 'map_target', 'battle', 'battle_menu', 'egg_battle_menu', 'egg_choice_menu',
                                  'monster_menu', 'attack_started', 'defense_started',
                                  'boss_attack_started', 'name_entry', 'castle_menu'})


def shop_step(screen: Screen, mem):
    shop = mem.get('shop')
    kind = screen.kind
    if kind == 'shop_exit_confirm':
        move = menu_to(screen, 'うむッ!')
        if shop:
            shop['merchant_done'] = True
        # Without a readable hand, the measured default cursor is うむッ (#960).
        return [move] if move and move != 'here' else [pad('a')]
    if kind == 'shop_quantity_prompt':
        return [pad('a')]
    if kind == 'shop_quantity':
        return quantity_step(screen, mem)
    # shop_list
    gold = (screen.header or {}).get('gold')
    if not shop or shop.get('key') != _month_key(screen.header) or not shop['items']:
        return [pad('b')]
    listed = {}
    for line in screen.lines:
        joined = ''.join(line.words(120, 256))
        m = re.match(r'^(\S+?)(\d+)G$', joined)
        if m:
            listed[m.group(1)] = int(m.group(2))
    while shop['items']:
        name, qty = shop['items'][0]
        price = listed.get(name)
        if price is None or gold is None or price > gold:
            _record(mem, 'buy_skip', card=name, qty=qty, price=price, gold=gold,
                    deviation_reason='not_listed' if price is None else 'insufficient_gold',
                    reason='商人の品揃えまたは所持金の制約')
            shop['items'].pop(0)
            continue
        n = min(qty, gold // price)
        shop['items'][0] = [name, n]
        shop['want'] = [name, n, price]
        move = menu_to(screen, name)
        return [pad('a')] if move == 'here' else [move] if move else []
    return [pad('b')]


def quantity_step(screen: Screen, mem, soldiers=False):
    shop = mem.get('shop') or {}
    if soldiers:
        # Digits sit at x=216 (tens) and x=224 (ones). The hand on the ones
        # digit hides the tens digit, so keep the last tens digit seen.
        line = next((l for l in screen.lines if 'かわりますぞ' in l.known.replace(' ', '')), None)
        cells = dict(line.cells) if line else {}
        ones_ch, tens_ch = cells.get(224), cells.get(216)
        if tens_ch and tens_ch.isdigit() and screen.hand and screen.hand[0] < 200:
            shop['soldier_tens'] = int(tens_ch)
        tens_seen = shop.get('soldier_tens')
        if ones_ch and ones_ch.isdigit() and tens_seen is not None:
            value = tens_seen * 10 + int(ones_ch)
        else:
            value = None
            if screen.hand and screen.hand[0] >= 200:
                return [pad('left')]     # reveal the tens digit first
        target = shop.get('soldiers', 0)
    else:
        m = re.search(r'(\d+)こで', screen.text)
        value = int(m.group(1)) if m else None
        want = shop.get('want') or [None, 0, 0]
        target = want[1]
        if screen.hand and 'うむッ' in screen.text and screen.selected in ('うむッ!', 'いかんッ!'):
            if value == target and target:
                move = menu_to(screen, 'うむッ!')
                if move == 'here':
                    bought = shop.setdefault('bought', [])
                    bought.append([want[0], value, want[2]])
                    _record(mem, 'buy', card=want[0], qty=value, price=want[2],
                            chart_step='1-month', strategy_variant=shop.get('variant', 'chart'),
                            reason='チャート/予算計画どおりの個数を確認して購入')
                    if shop.get('items') and shop['items'][0][0] == want[0]:
                        shop['items'].pop(0)
                    shop['want'] = None
                    return [pad('a')]
                return [move] if move else []
            return [pad('b')]
    if value is None or not screen.hand:
        return []
    if not target:
        return [pad('b')]
    # Digit editor: hand x>=200 is the ones digit, a smaller x the tens digit.
    ones_selected = screen.hand[0] >= 200
    tens, ones = divmod(value % 100, 10)
    want_tens, want_ones = divmod(min(target, 99), 10)
    if tens != want_tens:
        if ones_selected:
            return [pad('left')]
        if soldiers:
            shop['soldier_tens'] = None      # re-read after the change
        return [pad('up' if (want_tens - tens) % 10 <= 5 else 'down')]
    if ones != want_ones:
        if not ones_selected:
            return [pad('right')]
        return [pad('up' if (want_ones - ones) % 10 <= 5 else 'down')]
    if soldiers:
        shop['soldiers_done'] = True
        year, month = (int(p) for p in shop.get('key', '0-0').split('-'))
        expected = (chart.purchase_for(mem.get('chapter') or 0, year, month) or {}).get('soldiers')
        _record(mem, 'soldier_refill', qty=value, chart_step='1-month',
                strategy_variant=shop.get('variant', 'chart'),
                expected_metric=expected,
                observed_metric=value, reason='兵士補充数を確認して確定')
    return [pad('a')]


# ---------------------------------------------------------------- prompts
DISCHARGE_LIMIT = 6              # A presses per month while the balance is negative
DISCHARGE_EXIT_LIMIT = 4         # B presses per month once the balance is paid up


def discharge_step(screen: Screen, mem):
    """Forced discharge list: dismiss generals only while the balance is negative.

    Wages come out at the month boundary; a negative balance forces
    dismissing generals (their 賃金 becomes cash) until it is back to at
    least zero (gcgx 収入). While the balance is negative the header gold is
    unreadable (the game prints ー1G and the header only matches digits) and
    B is refused. Once the balance is non-negative the header gold parses:
    leave with B instead of dismissing more generals (owner 2026-09-28:
    そもそも将軍解雇はしないで欲しい). A list that refuses every bounded B
    press holds for the screen-stall terminal.
    """
    month = re.search(r'(\d+)ねん(\d+)のつき', screen.text)
    key = f'{month[1]}-{month[2]}' if month else None
    gold = (screen.header or {}).get('gold')
    paid_up = type(gold) is int
    state = mem.get('discharge') or {}
    if state.get('key') != key or type(state.get('presses')) is not int:
        state = {'key': key, 'presses': 0, 'exits': 0}
    mem['discharge'] = state
    if paid_up:
        exits = int(state.get('exits') or 0)
        if exits >= DISCHARGE_EXIT_LIMIT:
            if not state.get('held'):
                state['held'] = True
                _record(mem, 'situation_held', screen=screen.kind, strategy_variant='discharge_exit_failed',
                        observed_metric={'month': key, 'gold': gold, 'exits': exits},
                        reason='所持金が0以上でも解雇画面がBで抜けないため入力を保留')
            return []
        state['exits'] = exits + 1
        _record(mem, 'discharge_exit', strategy_variant='paid_up_leave_with_b',
                observed_metric={'month': key, 'gold': gold, 'exits': state['exits'],
                                 'selected': screen.selected},
                reason='所持金が0以上になったため解雇を続けずBで画面を出る')
        return [pad('b')]
    if state['presses'] >= DISCHARGE_LIMIT:
        if not state.get('held'):
            state['held'] = True
            _record(mem, 'situation_held', screen=screen.kind, strategy_variant='discharge_limit',
                    observed_metric={'month': key, 'presses': state['presses']},
                    reason='解雇画面で上限回数まで決定しても抜けないため入力を保留')
        return []
    state['presses'] += 1
    _record(mem, 'discharge_general', general=screen.selected,
            strategy_variant='forced_discharge_default_cursor',
            observed_metric={'month': key, 'presses': state['presses'],
                             'hand': list(screen.hand) if screen.hand else None},
            reason='所持金不足で将軍の解雇を強制されたため既定カーソルの将軍を解雇')
    return [pad('a')]


# The general-trade event (花いちもんめ) must always be declined (owner rule
# 2026-09-28). 'トレード' is the measured question word; the song title is a
# belt-and-braces match for a variant scene.
TRADE_DECLINE_TOKENS = ('トレード', 'はないちもんめ', 'いちもんめ')


def _goninja_budget(mem, header):
    """{gold, planned, spare} behind the monthly ゴニンジャー offer, else None.

    Owner rule (2026-10-02): 隠密戦隊ごにんじゃーへの依頼はお金に余裕がある時のみ。

    余裕 = 所持金 − 当月の購入予定（チャート or 採用済み調整チャート） − 卵回復の
    予約 − 賃金リザーブ。この残りで依頼料50Gが払えて初めて受ける。所持金もしくは
    当月の予定が読めない時（調整チャートの価格未測定カードを含む）は None で、
    None の時は払わない。残金での兵士補充と「余りがあれば」の募集費は元々余り金の
    使い道なので、ここでは予約しない。
    """
    gold = (header or {}).get('gold')
    if type(gold) is not int or gold < 0 or not header:
        return None
    here = (header['year'], header['month'])
    adjusted = ((mem.get('chart_plan') or {}).get('purchases') or {})
    if tuple(adjusted.get('month') or ()) == here:
        cards = tuple(adjusted.get('cards') or ())
        if any(name not in KNOWN_PRICES for name, _ in cards):
            return None      # 価格未測定では当月の予算が読めない
        planned = (sum(KNOWN_PRICES[name] * qty for name, qty in cards)
                   + max(0, int(adjusted.get('soldiers') or 0)))   # 兵士は1G=1人
    else:
        spec = chart.purchase_for(mem.get('chapter') or 0, *here)
        planned = int(spec.get('chart_gold') or 0) if spec else 0
    egg, _ = _extras_reserve(mem, header)
    return {'gold': gold, 'planned': planned, 'egg_reserve': egg,
            'spare': gold - planned - egg - WAGE_RESERVE}


def yes_no_step(screen: Screen, mem):
    text = screen.text
    metric = None
    if re.search(r'\d+Gでいい', text):
        choice, reason, variant = 'いかんッ!', '追加のおねだりは所持金を月一購入に残すため断る', 'decline_extra_gift'
    elif 'うちとおす' in text:
        # 月イチイベント「ゴニンジャー」: 50Gで敵将軍の暗殺を依頼する。成功率は低い。
        # Owner rule (2026-10-02): お金に余裕がある時だけ依頼する。
        budget = metric = _goninja_budget(mem, screen.header)
        if budget is None:
            choice, reason, variant = ('いかんッ!', '所持金か当月の購入予定が読めないためゴニンジャーへの依頼を見送る',
                                       'decline_goninja_unreadable')
        elif budget['spare'] >= GONINJA_COST:
            choice, reason, variant = ('うむッ!', '当月の購入予定と卵回復・賃金リザーブを差し引いても依頼料50Gが残るため依頼する',
                                       'accept_goninja')
        else:
            choice, reason, variant = ('いかんッ!', '当月の購入予定とリザーブを差し引くと依頼料50Gに足りないため依頼しない',
                                       'decline_goninja_no_spare')
    elif 'はたしあい' in text or 'ごあいて' in text:
        # Owner decision (2026-09-25): accept. A duel fought with the blue
        # gauge spent properly is a near-certain win, so the hero no longer
        # needs shielding from the offer.
        choice, reason = 'うむッ!', '一騎打ちは青ゲージを消費する前提で受ける'
        variant = 'accept_duel'
    elif any(tok in text for tok in TRADE_DECLINE_TOKENS):
        # Owner rule (2026-09-28): the general trade (花いちもんめ) almost
        # always offers an unfair deal (odoru7094: ろくでもないのしか手に
        # 入らないのでやってはいけない), so decline it. Measured question
        # (倒転王国 月イチイベント): 「ここで将軍同士のトレードをしようじゃ
        # ないか。そちらの◯◯将軍と我が軍のイキのいいのとではどーだ?」
        choice, reason = 'いかんッ!', '将軍トレード（花いちもんめ）は不平等な提案が多いため断る'
        variant = 'decline_general_trade'
    else:
        choice, reason, variant = 'うむッ!', '未分類の確認は既定で進行', 'unclassified_prompt'
    move = menu_to(screen, choice)
    if move == 'here':
        _record(mem, 'prompt', choice=choice, prompt=text[-40:], reason=reason,
                strategy_variant=variant, observed_metric=metric)
        return [pad('a')]
    if move is None:
        _record(mem, 'situation_held', screen=screen.kind,
                observed_metric={'choice': choice, 'hand_visible': screen.hand is not None,
                                 **({'goninja': metric} if metric else {})},
                reason='確認画面の選択位置を読めないため決定せず再観測')
    return [move] if move else []


def _repair_home_alias_failures(mem):
    """Undo order failures caused by the home castle chart/screen name check (v53-v59).

    g436 (21:19): the status check refused chapter 1's home castle as a
    mismatch three times per order and failed 1-A1, 1-V1, 1-C1... before any
    of them left. Orders from the home castle that failed without ever
    launching go back to pending once.
    """
    if mem.get('home_alias_repaired') or (mem.get('chapter') or 0) != 1:
        return
    mem['home_alias_repaired'] = True
    status = mem.get('orders') or {}
    launched = mem.get('launched_orders') or {}
    home = chart.home_castle(1)
    restored = []
    for order in chart.orders(1):
        step = order['step']
        if status.get(step) == 'failed' and order['source'] == home and step not in launched:
            status.pop(step, None)
            for key in ('source_miss', 'source_override', 'general_override'):
                (mem.get(key) or {}).pop(step, None)
            restored.append(step)
    if restored:
        mem['active'] = None
        _record(mem, 'orders_restored', observed_metric=restored,
                reason='本城の城名確認の誤判定（旧チャートラベルと画面実名の不一致）で失敗扱いになった指示を未実行に戻す')


def observe_events(screen: Screen, mem):
    """Record chart-relevant facts that need no input (month header, harvest)."""
    _repair_home_name_chapter(mem)
    _repair_home_alias_failures(mem)
    mem['tick'] = int(mem.get('tick') or 0) + 1      # observations: ages sorties (_en_route)
    _hold_general_loss_metric(mem)
    _migrate_card_evidence(mem)
    if screen.kind in ('card_select', 'sortie_confirm'):
        row = _egg_row(screen)
        eggs = mem.setdefault('egg_uses', {})
        if row:
            mem.setdefault('egg_types', {})[row[0]] = row[1]
            if row[0] in (mem.get('egg_recheck') or []):
                mem['egg_recheck'].remove(row[0])
        if row and eggs.get(row[0]) != row[2]:
            eggs[row[0]] = row[2]
            _record(mem, 'egg_uses_seen', general=row[0], egg=row[1], observed_metric=row[2],
                    reason='出撃画面のたまご行から卵の残り使用回数を記録')
    header = screen.header
    if header:
        chapter = header.get('chapter')
        if chapter and chapter != mem.get('chapter'):
            _enter_chapter(mem, chapter, reason='画面の章表示を確認し、前章の座標・出撃・購入状態を初期化')
        key = _month_key(header)
        if mem.get('month') != key:
            mem['month'] = key
            mem['gold'] = header['gold']
            _record(mem, 'month_seen', month=key, gold=header['gold'], reason='月の表示')
        mem['gold'] = header['gold']
    castle = CASTLE_STATUS.search(screen.text)
    income = re.search(r'しゅうにゅう(\d{1,3})Gレベル\d+しょうぐん', screen.text)
    if castle and income and mem.get('chapter') and mem.get('month'):
        label = STATUS_NAMES.get(castle[1], castle[1])
        if label in {STATUS_NAMES.get(c, c) for c in _owned(mem)}:
            mem.setdefault('castle_income', {})[label] = {
                'chapter': mem['chapter'], 'month': mem['month'], 'tick': mem['tick'], 'income': int(income[1])}
    if 'がはいかにくわわった!' in screen.text.replace('！', '!'):
        from .hanjuku_roster import invalidate
        invalidate(mem)
    from .hanjuku_house import roster
    from .hanjuku_roster import page
    page(mem, roster(screen))
    if 'きょうさく' in screen.text and not mem.get('poor_harvest_' + str(mem.get('month'))):
        mem['poor_harvest_' + str(mem.get('month'))] = True
        _record(mem, 'poor_harvest', deviation_reason='reset_forbidden',
                reason='チャートは凶作でリセット指示だがbotはリセットしない',
                expected_metric='収入減')
    # Owner rule (2026-09-28): with 50+ soldiers on hand, egg recovery wins
    # over buying more soldiers. The army total is read wherever the game
    # states it (e.g. 「げんざい わがぐんの へいしすうは Nめいです」).
    m = re.search(r'へいしすうは([0-9０-９]+)めい', screen.text.replace(' ', ''))
    if m:
        seen = int(m.group(1).translate(str.maketrans('０１２３４５６７８９', '0123456789')))
        mem['soldiers_seen_key'] = mem.get('month')
        if seen != mem.get('soldiers_seen'):
            mem['soldiers_seen'] = seen
            _record(mem, 'soldiers_seen', observed_metric={'soldiers': seen},
                    reason='兵士の総数を画面の文言から読み取った')


def summary(mem: dict | None) -> dict:
    """Bounded, secret-free chart progress for corner state and diagnostics."""
    mem = mem if isinstance(mem, dict) else {}
    stats = mem.get('stats') if isinstance(mem.get('stats'), dict) else {}
    tally = mem.get('tally') if isinstance(mem.get('tally'), dict) else {}
    orders = mem.get('orders') if isinstance(mem.get('orders'), dict) else {}
    battle = mem.get('battle') if isinstance(mem.get('battle'), dict) else {}
    chart_step = battle.get('step') if battle else mem.get('active')
    strategy_variant = battle.get('strategy_variant') if battle else mem.get('variant')
    as_int = lambda v: v if type(v) is int and 0 <= v <= 10**6 else None
    battles_started = as_int(tally.get('battles_started'))
    battles_judged = as_int(tally.get('battles_judged'))
    # A started battle without a verdict is not a win and not a loss: the
    # status must say so instead of reporting the judged subset as the whole.
    battles_unjudged = (max(0, battles_started - battles_judged)
                        if battles_started is not None and battles_judged is not None else None)
    return {
        'chapter': as_int(mem.get('chapter')),
        'chart_step': chart_step if isinstance(chart_step, str) else None,
        'strategy_variant': strategy_variant if isinstance(strategy_variant, str) else None,
        'orders_launched': sum(1 for v in orders.values() if v == 'launched'),
        'orders_failed': sum(1 for v in orders.values() if v == 'failed'),
        'captured': len(mem.get('captured') or []),
        'wins': as_int(stats.get('wins')), 'losses': as_int(stats.get('losses')),
        'unclassified': as_int(stats.get('unclassified')),
        'battles_started': battles_started,
        'battles_judged': battles_judged,
        'battles_unjudged': battles_unjudged,
        'castle_losses': as_int(tally.get('castle_losses')),
        'cards_used': (as_int(stats.get('cards_used')) if stats.get('card_evidence_version') == 1
                       and not battle.get('card_flow')
                       and battle.get('card_consumption_complete', True) is True else None),
        # Lower bound confirmed under the new evidence contract, not total use.
        'cards_confirmed': as_int(stats.get('cards_confirmed')),
        # No direct loss observer exists: even a legacy zero is not evidence.
        'generals_lost': None,
        'gold': as_int(mem.get('gold')),
        'month': mem.get('month') if isinstance(mem.get('month'), str) else None,
        'name_entered': bool((mem.get('name') or {}).get('done')),
        'name_matches': (mem.get('name') or {}).get('typed') == NAME,
    }


def egg_choice_step(screen: Screen, mem):
    """Choose a real offered summon; never type a name or infer an egg result."""
    from .hanjuku_screen import egg_choice_names
    names = egg_choice_names(screen)
    if not names or screen.menu_cursor not in (176, 192, 208):
        return []
    key = tuple(names)
    flow = mem.get('egg_choice')
    if not flow or tuple(flow.get('names') or ()) != key:
        flow = mem['egg_choice'] = {'names': names, 'selected': 0, 'wait': 0}
    if flow['selected']:
        flow['wait'] += 1
        if flow['wait'] <= 3:
            return []
        if flow['selected'] >= 2:
            if not flow.get('held'):
                flow['held'] = True
                _record(mem, 'egg_choice_unconfirmed', observed_metric={'candidates': names},
                        reason='有限回の召喚選択後も同じ三択のため、召喚成功とせず保留')
            return []
    # The first actual candidate is a valid summon. No unverified power ranking
    # or arbitrary entry is introduced; later skill/HP policy handles its fight.
    choice = names[0]
    move = _battle_menu_to(screen, choice)
    if move != 'here':
        return [move] if move else []
    flow['selected'] += 1
    flow['wait'] = 0
    _record(mem, 'egg_choice_select', observed_metric={'candidates': names, 'choice': choice},
            resulting_event='summon_selected_not_yet_confirmed',
            reason='実表示の三候補と騎士カーソルを確認し、先頭の召喚獣を選択')
    return [pad('a')]


def _egg_general_reading(screen, mem, battle):
    """Read this battle's general, never substitute a summoned monster's HP."""
    rows = [row for row in screen.egg_rows
            if row.side == 'ally' and row.name == battle.get('ally')
            and type(row.hp) is int and row.hp >= 0]
    if len(rows) != 1:
        return None
    ally = battle.get('ally')
    full = (mem.get('hero_max_hp') if ally == NAME else general_max_hp(ally))
    if type(full) is not int or full <= 0 or rows[0].hp > full:
        return None
    battle['ally_hp'] = rows[0].hp
    return rows[0].hp, full


def _own_egg_needed(mem, battle, general_reading=None):
    """自軍のたまごを使うべきかを、HPと兵士数で判定する。返り値は (要るか, 証拠)。

    正典 (docs/hanjuku_script_bot.md ⑥) は「HP不利かつチャートに切り札指示
    なし → use_egg」。合戦力は gcgx battle.html「兵士は1人ずつHP10を持っている」
    に従い HP + 10×兵士で比べ、兵士が読めない戦闘では HP だけで判定する。
    加えて自軍のHPが最大の7割を超えていること（`general_reading`）も要る。
    オーナー確認 (2026-10-03): 30/82 のように自軍が大破していれば、敵将軍の
    絶対値HPが低くても勝てそうにはならず卵を使う。読み取れない時も温存しない。
    強い将軍 (shogun.html の評・ボス) は常に要る。HP不明でも要る (従来どおり)。
    """
    evidence = {'strong_general': None, 'strong_rule': None, 'ally_hp': None,
                'ally_max_hp': None,
                'enemy_hp': None, 'ally_soldiers': None, 'enemy_soldiers': None,
                'ally_force': None, 'enemy_force': None, 'healthy': None,
                'ratio_tenths': BEHIND_EGG_RATIO_TENTHS, 'rule': 'unknown_battle'}
    if not isinstance(battle, dict):
        return True, evidence
    strong, strong_evidence = _strong_enemy(mem, battle)
    reading = general_reading if isinstance(general_reading, (tuple, list)) else None
    healthy = (bool(reading) and len(reading) == 2
               and type(reading[0]) is int and type(reading[1]) is int and reading[1] > 0
               and reading[0] * 10 > reading[1] * BEHIND_EGG_RATIO_TENTHS)
    ally_hp, enemy_hp = battle.get('ally_hp'), battle.get('enemy_hp')
    ally_soldiers, enemy_soldiers = battle.get('ally_soldiers'), battle.get('enemy_soldiers')
    # 片側だけ読めない戦闘では HP 比較へ退ける (推測で片方だけ加点しない)。
    measured = type(ally_soldiers) is int and type(enemy_soldiers) is int
    if type(ally_hp) is int and type(enemy_hp) is int and measured:
        ally_force = ally_hp + 10 * max(0, ally_soldiers)
        enemy_force = enemy_hp + 10 * max(0, enemy_soldiers)
    else:
        ally_force, enemy_force = ally_hp, enemy_hp
    comfortable = (healthy and type(ally_force) is int and type(enemy_force) is int
                   and ally_force * 10 > enemy_force * BEHIND_EGG_RATIO_TENTHS)
    if not healthy:
        rule = 'ally_wounded'    # 大破中は敵より上でも温存しない
    elif measured:
        rule = 'soldier_force'
    else:
        rule = 'hp_only'
    return (strong or not comfortable), {
        **strong_evidence,
        'strong_rule': strong_evidence.get('rule'),
        'strong_general': strong, 'ally_hp': ally_hp,
        'ally_max_hp': reading[1] if isinstance(reading, (tuple, list)) and len(reading) == 2
        and type(reading[1]) is int else None,
        'enemy_hp': enemy_hp,
        'ally_soldiers': ally_soldiers if measured else None,
        'enemy_soldiers': enemy_soldiers if measured else None,
        'ally_force': ally_force, 'enemy_force': enemy_force, 'healthy': healthy,
        'ratio_tenths': BEHIND_EGG_RATIO_TENTHS, 'rule': rule,
    }


def egg_battle_step(screen: Screen, mem):
    """Summoned-monster battle: a turn menu that waits for a command.

    The chart avoids provoking enemy eggs but gives no command for this
    battle. Independent judgment defaults to たまごをつかう so an enemy
    summon is answered with an egg rather than a stall or blind attack.
    This is original pattern ⑥ (own egg when melee is not enough); patterns
    ②④⑤ stay chart-directed. Messages inside it advance with A.
    """
    if screen.kind != 'egg_battle_menu':
        mem.pop('egg_menu_stage', None)
        return [pad('a')]
    # 将軍HPをこの召喚戦のメニュー行から最新化してから、勝てそうかを判定する。
    battle = mem.get('battle') or {}
    general_reading = _egg_general_reading(screen, mem, battle)
    wants_egg, egg_evidence = _own_egg_needed(mem, battle, general_reading)
    if not mem.get('egg_battle'):
        mem['egg_battle'] = True
        exp = mem.get('_experience')
        key = experience.situation_key('egg_summon', mem)
        # ⑥の写像: HP不利ならたまご、勝てそうなら温存 (attack) から始める。
        action = experience.preferred(exp, key, default='use_egg' if wants_egg else 'attack',
                                      kind='egg_summon')
        mem['egg_action'] = action
        mem['egg_key'] = key
        mem['egg_needed'] = wants_egg
        if mem.get('battle'):
            battle['independent'] = {'kind': 'egg_summon', 'key': key, 'action': action,
                                     'pattern': '⑥'}
            battle['egg_battle'] = True     # earlier retreat threshold (see _hero_retreat_needed)
        _record(mem, 'egg_battle', strategy_variant=f'egg_battle_{action}',
                deviation_reason='チャート外: 敵の卵召喚戦',
                expected_metric='召喚獣への対処と戦闘結果',
                observed_metric={'experience_key': key, 'egg_need': egg_evidence},
                source_pattern='⑥',
                reason=('チャートに召喚戦の指示がないための独自判断（原典戦術⑥、'
                        '自軍が健在でHPと兵士が勝てそうならたまごを温存）'
                        if not wants_egg else
                        'チャートに召喚戦の指示がないための独自判断（原典戦術⑥、既定はたまご）'))
    attack = mem.get('attack') or {}
    # g514: the hero was 34/90 against the boss's Hydra, with both planned
    # cards unused. Available eggs/cards must not bypass his retreat check.
    # A current named general panel and the same attack receipt are required;
    # monster HP, another general, or a previous battle cannot authorize B.
    if (general_reading and battle.get('ally') == NAME
            and battle.get('side') == attack.get('side') == 'attack'
            and attack.get('general') == NAME and battle.get('step')
            and attack.get('step') == battle.get('step')
            and attack.get('castle') == battle.get('castle')
            and not battle.get('away')
            and type(battle.get('start_ally_hp')) is int
            and 0 < battle['start_ally_hp'] <= general_reading[1]):
        retreat = _hero_retreat_open(mem, battle)
        if retreat is not None:
            return retreat
    if (_available_rare_tactic(mem, battle) and not battle.get('rare_egg_command_return')):
        battle['rare_egg_command_return'] = True
        _record(mem, 'battle_rare_egg_command', **_battle_labels(battle),
                observed_metric={'card': 'キャトルミュー'},
                reason='実携行のレア札で召喚敵へ対処するため、通常コマンドへ一度戻る')
        return [pad('b')]
    # 途中昇格: 温存していた (前回まで勝てていた) のに劣勢になったら、たまごを
    # 使う判断へ切り替える。回復しても温存へは戻さない (一方向だけ)。
    if mem.get('egg_needed') is False and wants_egg and mem.get('egg_action') == 'attack':
        mem['egg_action'] = 'use_egg'
        if isinstance(battle.get('independent'), dict):
            battle['independent']['action'] = 'use_egg'
        _record(mem, 'egg_battle', strategy_variant='egg_battle_use_egg',
                deviation_reason='チャート外: 敵の卵召喚戦',
                expected_metric='召喚獣への対処と戦闘結果',
                observed_metric={'egg_need': egg_evidence}, source_pattern='⑥',
                reason=('温存していたが、自軍のHPが最大の7割以下になるか、'
                        'HPと兵士の合戦力が敵の7割を下回ったため途中でたまごを'
                        '使う判断へ切り替える'))
    mem['egg_needed'] = wants_egg
    action = mem.get('egg_action', 'use_egg')
    if action == 'use_egg' and 'たまごをつかう' not in screen.text:
        # A spent egg greys the row out and drops it from OCR (mirrors
        # battle_menu_step's きりふだ/たいきゃく fallback); chasing a label
        # that never appears held the bot here forever (viewer report
        # 2026-09-28: 持ってないタマゴを使おうとして止まっている). The
        # attack fallback still checks the observed command cursor.
        if not mem.get('egg_battle_row_dead'):
            mem['egg_battle_row_dead'] = True
            _record(mem, 'situation_held', screen=screen.kind, strategy_variant='egg_unavailable',
                    reason='たまごをつかうが使えない表示のため卵を諦めてこうげきで応戦する')
        action = 'attack'
    if action == 'attack':
        battle = (mem.get('battle') if isinstance(mem.get('battle'), dict)
                  else mem.setdefault('egg_retreat_flow', {}))
        attempted = {*(battle.get('cards_selected') or []), *(battle.get('cards_used') or []),
                     *(battle.get('cards_unclassified') or [])}
        cards_left = [c for c in (battle.get('planned_cards') or []) if c not in attempted]
        # g498: B did not leave the turn menu, yet the next observation
        # immediately chose A and Venus lost. Require a bounded return flow,
        # and actually request retreat if the normal command menu is reached.
        # g460 安全弁は「卵か札か退却のどれもない」局面だけ。勝てそうなので
        # たまごを温存している間は、まず白兵で戦う (退却しない)。
        if (not cards_left and battle.get('side') != 'defense'
                and (wants_egg or 'たまごをつかう' not in screen.text)):
            attempts = int(battle.get('egg_retreat_attempts') or 0)
            battle['egg_retreat_needed'] = True
            battle['egg_retreat_tried'] = True  # legacy hotloaded state
            if attempts < 3:
                battle['egg_retreat_attempts'] = attempts + 1
                _record(mem, 'battle_egg_retreat_attempt', **_battle_labels(battle),
                        observed_metric={'enemy': battle.get('enemy'), 'ally_hp': battle.get('ally_hp'),
                                         'enemy_hp': battle.get('enemy_hp'), 'attempt': attempts + 1},
                        reason='卵も札もない召喚戦のため、通常の退却メニューへの復帰を再確認する')
                return [pad('b')]
            if not battle.get('egg_retreat_unavailable_recorded'):
                battle['egg_retreat_unavailable_recorded'] = True
                _record(mem, 'battle_egg_retreat_unavailable', **_battle_labels(battle),
                        observed_metric={'attempts': attempts, 'screen': screen.kind},
                        reason='有限回の復帰入力でも退却メニューを確認できないため、退却成功とせず応戦する')
        # g508: after Excalibur fell, stale general HP82 concealed the
        # wounded Venus while blind A kept choosing the weakest attack.
        # Fierce attack adds lost HP to damage, but can miss/lose initiative;
        # use it only as a bounded-resource last resort with actual panels.
        enemy_rows = [r for r in screen.egg_rows if r.side == 'enemy'
                      and r.name in _MONSTER_SKILLS and type(r.hp) is int and r.hp > 0]
        fierce = (general_reading is not None and 0 < general_reading[0] * 2 <= general_reading[1]
                  and len(enemy_rows) == 1 and not cards_left
                  and 'たまごをつかう' not in screen.text)
        label = 'もうこうげき' if fierce else 'こうげき'
        move = _battle_menu_to(screen, label)
        if move is None:
            # Never confirm a previously selected fierce row blindly.
            return [] if fierce or battle.get('egg_attack_label') == 'もうこうげき' else [pad('a')]
        if battle.get('egg_attack_label') != label:
            battle['egg_attack_label'] = label
            _record(mem, 'battle_egg_general_attack', **_battle_labels(battle),
                    observed_metric={'command': label, 'general_hp': battle.get('ally_hp'),
                                     'max_hp': general_reading[1] if general_reading else None,
                                     'enemy_monster': enemy_rows[0].name if len(enemy_rows) == 1 else None},
                    reason=('卵・予定札がなく退却もできない負傷将軍は、実HPに基づきもうこうげきで応戦する'
                            if fierce else '実コマンド位置を確認して通常攻撃へ戻す'))
        return [pad('a')] if move == 'here' else [move]
    _egg_recheck(mem)
    if screen.hand:
        move = menu_to(screen, 'たまごをつかう')
        if move == 'here':
            return [pad('a')]
        if move:
            return [move]
        # Hand present but the label is unreadable: keep moving like no-hand.
    # No hand (or unreadable hand): walk down twice from こうげき to たまご.
    stage = int(mem.get('egg_menu_stage') or 0)
    if stage < 2:
        mem['egg_menu_stage'] = stage + 1
        return [pad('down')]
    return [pad('a')]


# The skill table is matched kana-blind: the tile reader can drop a dakuten
# mark (live とけこむそー vs the transcribed とけこむぞー).
_KANA_FOLD = str.maketrans(
    'がぎぐげござじずぜぞだぢづでどばびぶべぼ'
    'ガギグゲゴザジズゼゾダヂヅデドバビブベボヴ'
    'ぱぴぷぺぽパピプペポ',
    'かきくけこさしすせそたちつてとはひふへほ'
    'カキクケコサシスセソタチツテトハヒフヘホウ'
    'はひふへほハヒフヘホ')
# Enemy-owned menu: hold this many observations before acting anyway. The
# agent observes every 1500 ms (config/games/hanjuku-hero.toml interval_ms),
# so 30 observations ≈ 45 s - long enough for the observed enemy selection
# window (g344 kept the same menu for 31 s) and far below the 300 s stasis
# limit, yet bounded so a stuck screen cannot freeze the corner.
MONSTER_MENU_HOLD_LIMIT = 30


def _fold_skill(text: str) -> str:
    return text.replace(' ', '').replace('！', '!').replace('？', '?').translate(_KANA_FOLD)


_MONSTER_SKILLS = {name: frozenset(_fold_skill(s) for s in skills)
                   for name, skills in reference.MONSTER_SKILLS.items()}
_MONSTER_EFFECTS = frozenset(_fold_skill(s) for s in reference.MONSTER_EFFECT_SKILLS)
_MONSTER_HEALS = frozenset(_fold_skill(s) for s in reference.MONSTER_HEAL_SKILLS)


def _monster_owner(skill_lines, ally, enemy) -> str | None:
    """Which side the visible skill rows belong to, or None when ambiguous."""
    shown = {_fold_skill(''.join(line.known.split())) for line in skill_lines}
    ally_set = _MONSTER_SKILLS.get(ally.name) if ally else None
    enemy_set = _MONSTER_SKILLS.get(enemy.name) if enemy else None
    hit_ally = bool(shown & ally_set) if ally_set else False
    hit_enemy = bool(shown & enemy_set) if enemy_set else False
    if hit_ally and not hit_enemy:
        return 'ally'
    if hit_enemy and not hit_ally:
        return 'enemy'
    return None


def _monster_effectful(skill: str) -> bool:
    return _fold_skill(skill) in _MONSTER_EFFECTS


def _monster_heal(skill: str) -> bool:
    return _fold_skill(skill) in _MONSTER_HEALS


def monster_menu_step(screen: Screen, mem):
    """Our summoned monster's own turn: a skill menu with independent judgment.

    The chart has no command for this menu. The owner is decided from the
    skill table: an enemy-owned menu waits for the enemy AI, bounded by
    MONSTER_MENU_HOLD_LIMIT so a stuck screen cannot freeze the bot. An
    allied menu retreats at half HP or less, keeps the special second skill
    while behind, skips a heal-first skill while ahead so damage still
    happens, otherwise uses the first skill, refined by measured
    experience. Movement follows the knight cursor when visible and a
    row-step stage when it is not.
    """
    rows = screen.menu_rows
    if not rows:
        return []
    row_texts = [''.join(line.known.split()) for line in rows]
    menu_key = tuple(row_texts)
    if mem.get('monster_menu_key') != menu_key:
        mem['monster_menu_key'] = menu_key
        for key in ('monster_menu_cursor', 'monster_menu_choice', 'monster_menu_choice_key',
                    'monster_menu_choice_hp', 'monster_ally_max_hp'):
            mem.pop(key, None)
        mem['monster_menu_hold'] = 0
    skill_lines = [line for line, text in zip(rows, row_texts) if 'もどれ' not in text]
    if not skill_lines:
        # The menu is still drawing: acting now could land on the retreat row.
        return []
    return_index = next((i for i, text in enumerate(row_texts) if 'もどれ' in text), None)
    panel = {row.side: row for row in screen.egg_rows if row.side}
    ally, enemy = panel.get('ally'), panel.get('enemy')
    mem['monster_panel'] = {'ally': ally.name if ally else None,
                            'ally_hp': ally.hp if ally else None,
                            'enemy': enemy.name if enemy else None,
                            'enemy_hp': enemy.hp if enemy else None}
    owner = _monster_owner(skill_lines, ally, enemy)
    if owner == 'enemy':
        hold = int(mem.get('monster_menu_hold') or 0) + 1
        mem['monster_menu_hold'] = hold
        if hold <= MONSTER_MENU_HOLD_LIMIT:
            if hold == 1:
                _record(mem, 'monster_menu_wait',
                        observed_metric={'owner': owner,
                                         'enemy': enemy.name if enemy else None,
                                         'enemy_hp': enemy.hp if enemy else None,
                                         'menu': list(menu_key)},
                        reason='技選択は敵側の表示（敵AIが選ぶ）ため入力を保留')
            return []
        if hold == MONSTER_MENU_HOLD_LIMIT + 1:
            _record(mem, 'monster_menu_wait', deviation_reason='enemy_menu_stuck',
                    observed_metric={'hold_observations': hold, 'menu': list(menu_key)},
                    reason='敵側表示のまま停留が上限を超えたため、画面停止を避けて選択へ移行')
    else:
        mem['monster_menu_hold'] = 0
    # Re-decide when the panel HP changed: a cached choice repeated the same
    # skill every turn (g407: ふくらむ x71) and never let a heal-first monster
    # alternate ふくらむ→シャウト (owner 2026-09-29).
    hp_state = (ally.hp if ally else None, enemy.hp if enemy else None)
    # GCGX: エクスカリバる is instant death (11/current HP against
    # monsters), マサムネる hits four times. g498 used the former at
    # 282HP against a 240HP Dark Elf, then lost the summon and Venus.
    damage_second = (owner == 'ally' and ally.name == 'エクスカリバー'
                     and enemy is not None and enemy.name in _MONSTER_SKILLS
                     and len(skill_lines) == 2
                     and [_fold_skill(t) for t in row_texts[:2]]
                     == [_fold_skill(t) for t in reference.MONSTER_SKILLS['エクスカリバー']])
    if (not mem.get('monster_menu_choice') or mem.get('monster_menu_choice_hp') != hp_state
            or (damage_second and mem.get('monster_menu_choice') not in ('skill2', 'retreat'))):
        ally_hp = ally.hp if ally else None
        enemy_hp = enemy.hp if enemy else None
        behind = _behind({'ally_hp': ally_hp, 'enemy_hp': enemy_hp})
        retreat = type(ally_hp) is int and type(enemy_hp) is int and ally_hp * 2 <= enemy_hp
        first = ''.join(skill_lines[0].known.split())
        second = ''.join(skill_lines[1].known.split()) if len(skill_lines) >= 2 else ''
        # g496: ウゴカザル's two commands do nothing; spending turns on
        # them cost 120 -> 46 -> 11 HP without reducing the defender.
        powerless = (owner == 'ally' and ally.name == 'ウゴカザル'
                     and len(skill_lines) == 2
                     and {_fold_skill(first), _fold_skill(second)}
                     == _MONSTER_SKILLS['ウゴカザル'])
        heal_first = bool(second) and _monster_heal(first) and not _monster_heal(second)
        default = 'skill1'
        if damage_second:
            default = 'skill2'
        elif heal_first:
            # バルーンフィンチ: ふくらむ→シャウト (owner 2026-09-29). Inflate to
            # the tracked max first (the first turn heals so a damaged summon
            # reaches it), then shout while at max; damage re-enables the heal.
            # The old "heal at full forever" loop (g407: ふくらむ x71) stays
            # impossible because a full HP attacks instead.
            seen = mem.get('monster_ally_max_hp')
            if type(ally_hp) is not int:
                default = 'skill1'
            elif behind or (type(seen) is int and ally_hp < seen):
                default = 'skill1'      # hurt: inflate back to the tracked max
            else:
                default = 'skill2'      # at max (or ahead): shout
            if type(ally_hp) is int:
                mem['monster_ally_max_hp'] = max(int(seen or 0), ally_hp)
        elif behind and len(skill_lines) >= 2 and _monster_effectful(second):
            default = 'skill2'
        exp = mem.get('_experience')
        key = experience.situation_key('monster_menu', mem)
        action = ('retreat' if retreat or powerless else 'skill2' if damage_second
                  else experience.preferred(exp, key, default=default, kind='monster_menu'))
        mem['monster_menu_choice'] = action
        mem['monster_menu_choice_key'] = key
        mem['monster_menu_choice_hp'] = hp_state
        battle = mem.get('battle')
        if isinstance(battle, dict):
            battle['independent'] = {'kind': 'monster_menu', 'key': key, 'action': action}
        if action == 'retreat':
            label = 'たまごに もどれ'
            why = ('両技に攻撃性能がない召喚獣のため、無効な攻撃を繰り返さず戻す'
                   if powerless else '味方HPが敵の半分以下なので撤退して見守る')
        elif action == 'skill2' and len(skill_lines) >= 2:
            label = second
            why = ('敵召喚獣には低確率の即死より4回攻撃を優先する' if damage_second else
                   '1技目が回復技のため敵を減らす2技目を選ぶ'
                   if heal_first and not behind else '味方が劣勢で効果付きの2技目')
        else:
            label, why = first, '先手を取れる1技目を続ける'
        if action != default and action != 'retreat':
            why = f'過去の結果に基づく経験の選択（既定 {default}）'
        _record(mem, 'monster_menu_choice',
                strategy_variant=f'monster_menu_{action}',
                deviation_reason='チャートに召喚獣ターンの指示がない',
                expected_metric='召喚獣ターンの選択と戦闘結果',
                observed_metric={'action': action, 'owner': owner, 'menu': list(menu_key),
                                 'ally_hp': ally_hp, 'enemy_hp': enemy_hp,
                                 'experience_key': key},
                reason=f'召喚獣の技メニューで「{label}」を選択: {why}')
    action = mem.get('monster_menu_choice')
    if action == 'retreat':
        if return_index is None:
            return []
        target_index = return_index
    elif action == 'skill2' and len(skill_lines) >= 2:
        target_index = rows.index(skill_lines[1])
    else:
        target_index = rows.index(skill_lines[0])
    target = rows[target_index].y
    cur = screen.menu_cursor
    if cur is None:
        cur = mem.get('monster_menu_cursor')
    if cur is None:
        cur = rows[0].y
    if target > cur + 4:
        mem['monster_menu_cursor'] = cur + 16
        return [pad('down')]
    if target < cur - 4:
        mem['monster_menu_cursor'] = cur - 16
        return [pad('up')]
    return [pad('a')]


def gift_step(screen: Screen, mem):
    """「なにを かいあたえますか?」: the chart resets here; the bot cannot, so
    it buys the cheapest listed item and records the deviation."""
    items = []
    for line in screen.lines:
        # The live gift prompt shares the first price row. Restrict the
        # item/price to the right menu, not the dialogue at its left.
        joined = ''.join(word for x, word in line.spans() if x >= 144)
        m = re.match(r'^(\S+?)(\d+)G$', joined)
        if m and not HEADER_RE.search(line.known.replace(' ', '')):
            items.append((int(m.group(2)), m.group(1)))
    if not items:
        return []
    price, name = min(items)
    move = menu_to(screen, name, exact=False)    # the price can abut the name
    if move == 'here':
        _record(mem, 'gift', strategy_variant='cheapest_gift', item=name, price=price,
                gold=(screen.header or {}).get('gold'), deviation_reason='reset_forbidden',
                expected_metric={'chart': 'おねだりはリセット'},
                observed_metric={'price': price, 'affordable': (screen.header or {}).get('gold', 0) >= price},
                reason='リセットできないため最安の品を選ぶ')
        return [pad('a')]
    return [move] if move else []
