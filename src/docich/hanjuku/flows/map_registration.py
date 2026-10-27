"""Bounded free-cursor survey: source hints never become movement facts.

The first field view with two roofs defines a chapter-local origin. Camera
translation is measured from at least two unchanged roof anchors, including
scrolls; sent D-pad durations are not position evidence. A location enters
the run's map only after a stationary cursor/roof inspection and two exact
castle-status name readings. Only the regular sortie flow can dispatch.
One-roof startup may pan the free cursor under a separate finite budget;
it cannot record coordinates or confirm a menu before calibration.
"""
from __future__ import annotations

import re

from ... import hanjuku_chart as chart, hanjuku_map_sources as sources
from ..decision_log import _record
from ..os_layer.commands import menu_to, pad
from ..os_layer.font import UNKNOWN
from ..os_layer.readers import CASTLE_STATUS, STATUS_NAMES
from ..os_layer.roster import run_identity
from ..os_layer.screen import castle_roofs

SCHEMA = chart.MAP_SCHEMA
verified_castles = chart.verified_castles
MAX_PROBE = 96
MAX_PANEL = 12
MAX_SCROLL = 64
MAX_HOLD = 16
MAX_BOOTSTRAP = 32
MAX_BOOTSTRAP_SWEEP = 320
REANCHOR_REJECT_LIMIT = 2
SINGLE_ROOF_SCROLL = MAX_HOLD+2   # camera shift one sent hold can produce


def translation(previous, current, *, limit=None):
    """Unique integer translation supported by two static roof positions."""
    if len(previous) < 2 or len(current) < 2:
        return None
    before, after = set(map(tuple, previous)), set(map(tuple, current))
    votes = {}
    for a in before:
        for b in after:
            delta = a[0]-b[0], a[1]-b[1]
            if limit is not None and max(map(abs, delta)) > limit:
                continue
            votes[delta] = sum((p[0]+delta[0], p[1]+delta[1]) in before for p in after)
    best = max(votes.values(), default=0)
    winners = [delta for delta, count in votes.items() if count == best]
    return winners[0] if best >= 2 and len(winners) == 1 else None


def _label(screen, chapter):
    if screen.kind == 'castle_info' and CASTLE_STATUS.search(screen.text) is None:
        # Native enemy info has no ステータス word. Match its foreground
        # rows and positions, not a castle name in background dialogue.
        rows = {line.y: line for line in screen.lines if line.y in (31, 47, 63, 79)}
        if len(rows) != 4 or any(UNKNOWN in rows[y].span(120, 216) for y in rows):
            return None
        header = rows[31].spans()
        if (len(header) != 2 or header[0][0] != 120 or header[1] != (184, 'じょう')
                or not re.fullmatch(r'しゅうにゅう\s*\d+\s*G', rows[47].span(120, 216))
                or not re.fullmatch(r'レベル\s*\d+', rows[63].span(120, 216))
                or not re.fullmatch(r'しょうぐん\s*\d+\s*めい', rows[79].span(120, 216))):
            return None
        label = header[0][1]
        label = next((n for n in sources.SOURCE_CASTLES.get(chapter, {})
                      if STATUS_NAMES.get(n, n) == label), label)
        return label if label in sources.SOURCE_CASTLES.get(chapter, {}) else None
    if screen.kind not in {'castle_menu', 'castle_info'} or CASTLE_STATUS.search(screen.text) is None:
        return None
    names = []
    for row in screen.lines:
        # Unknown glyphs at the row ends are map pixels outside the windows
        # (g1059 07:36 snowfield); any inside the text still reject the row.
        text = row.span(16, 240).replace(' ', '').strip(UNKNOWN)
        if UNKNOWN in text:
            continue
        match = re.fullmatch(r'(?:しゅつげき)?(.+?)じょう(?:ステータス)?', text)
        if match:
            label = match[1]
            label = next((n for n in sources.SOURCE_CASTLES.get(chapter, {})
                          if STATUS_NAMES.get(n, n) == label), label)
            if label in sources.SOURCE_CASTLES.get(chapter, {}):
                names.append(label)
    return names[0] if len(names) == 1 else None


def _pose(state, roofs, cursor):
    positions = [list(r['target']) for r in roofs]
    prior = state.get('roof_world')
    if prior:
        camera = translation(prior, positions)
        last_camera = state.get('camera')
        if camera is None and last_camera is not None and positions and min(len(prior), len(positions)) == 1:
            # g1059 07:29 (snowfield): only one roof stays in view, so the
            # two-anchor vote never forms. Accept the single pairing only
            # when it is the one shift a single hold can reach.
            shifts = {(a[0]-b[0], a[1]-b[1]) for a in prior for b in positions
                      if max(abs(a[i]-b[i]-last_camera[i]) for i in (0, 1)) <= SINGLE_ROOF_SCROLL}
            camera = next(iter(shifts)) if len(shifts) == 1 else None
        if camera is not None and last_camera is not None:
            if max(abs(a-b) for a,b in zip(camera, last_camera)) > MAX_SCROLL:
                camera = None
    elif state.get('origin_frame'):
        camera = None  # a named known-castle inspection must re-anchor first
    else:
        camera = (0, 0) if len(positions) >= 2 else None
    if camera is None:
        return None
    point = [cursor[0]+camera[0], cursor[1]+camera[1]]
    previous = state.get('cursor_world')
    motion = state.pop('last_input', None)
    if previous:
        delta = [point[i]-previous[i] for i in (0, 1)]
        if motion:
            axis, direction, budget = motion
            if (not -1 <= delta[axis]*direction <= budget+2
                    or abs(delta[1-axis]) > 1):
                return None
        elif max(map(abs, delta)) > 1:
            return None
    state['camera'] = list(camera)
    state['cursor_world'] = point
    state['roof_world'] = [[x+camera[0], y+camera[1]] for x,y in positions]
    return camera


def _ownership(mem, name, kind):
    """A twice-observed roof and named status prove this current ownership."""
    from ..os_layer.roster import owner
    owner(mem, STATUS_NAMES.get(name, name), kind)
    home = chart.home_castle(mem['chapter'])
    captured = mem.setdefault('captured', [])
    lost = mem.setdefault('lost', [])
    if name == home:
        mem['home_lost'] = kind == 'enemy'
    if kind == 'own':
        if name != home and name not in captured:
            captured.append(name)
        mem['lost'] = [n for n in lost if n != name]
    else:
        was_owned = name in captured
        mem['captured'] = [n for n in captured if n != name]
        if (name == home or was_owned) and name not in lost:
            lost.append(name)
        if was_owned:
            (mem.get('garrison') or {}).pop(name, None)
            (mem.get('castle_defenders') or {}).pop(name, None)


def _candidate(mem, state, known):
    chapter = mem['chapter']
    names = sources.SOURCE_CASTLES[chapter]
    preferred = [chart.home_castle(chapter)]
    for order in chart.all_orders(chapter):
        preferred.extend([order['source'], order['target']])
    preferred.extend(names)
    failed = state.get('failed') or {}
    tick = int(mem.get('tick') or 0)
    wanted = next((n for n in dict.fromkeys(preferred) if n in names and n not in known
                   and tick-failed.get(n, -1000) >= 200), None)
    if wanted is None or not known:
        return wanted, None
    anchor = next(iter(known))
    left, top = names[anchor]
    x, y = known[anchor]
    point = names[wanted]
    # Hypothesis used to steer a bounded inspection search only. It is never
    # returned by verified_castles or used to select a sortie destination.
    return wanted, (x+8*(point[0]-left), y+8*(point[1]-top))


def _bootstrap(screen, mem, state, roofs):
    """Bounded free-cursor pan until two roofs enter view; no pose evidence.

    Source deltas choose a search direction/budget only. In particular,
    neither those deltas nor sent durations ever seed a camera or castle.
    """
    search = state.get('bootstrap')
    if search is None:
        if len(roofs) != 1:
            return None
        chapter = mem['chapter']
        names = sources.SOURCE_CASTLES[chapter]
        home = chart.home_castle(chapter)
        target = next((o['target'] for o in chart.all_orders(chapter)
                       if o['target'] in names and o['target'] != home), None)
        if target is None:
            return None
        delta = [8*(names[target][i]-names[home][i]) for i in (0, 1)]
        legs = [[i, 1 if d > 0 else -1, min(256, abs(d)+MAX_HOLD)]
                for i, d in enumerate(delta) if d]
        search = state['bootstrap'] = {'legs': legs, 'attempts': 0, 'still': 0}
        _record(mem, 'map_bootstrap_started', castle=target,
                observed_metric={'source_delta_hint': delta, 'native_coordinate_verified': False},
                reason='屋根が一つなので座標は作らず、自由カーソルだけで二屋根が見える範囲を有界探索')
    mem['_map_registration_hold'] = True
    if search.get('done'):
        # The hint can miss the second roof even while movement succeeds.
        # Try one bounded sweep using directional inputs only; no sent
        # distance becomes a coordinate or authorizes a confirmation.
        if (not search.get('sweep_started') and search.get('attempts', 0) >= 8
                and search.get('still') == 0 and not search.get('legs')):
            search.update(done=False, sweep_started=True, attempts=0, still=0, view=None,
                          legs=[[0, -1, 512], [1, -1, 512], [0, 1, 1024],
                                [1, 1, 160], [0, -1, 1024], [1, 1, 160],
                                [0, 1, 1024]])
            _record(mem, 'map_bootstrap_sweep',
                    reason='候補方向で二屋根を確認できないため、座標を推測せず追加の範囲を有界探索')
        else:
            return []
    view = [list(screen.cursor), sorted(list(r['target']) for r in roofs)]
    search['still'] = search['still']+1 if search.get('view') == view else 0
    search['view'] = view
    if search.get('sweep_started') and search['still'] >= 3 and search['legs']:
        search['legs'].pop(0)  # a map edge ends this leg, not the whole sweep
        search['still'] = 0
    limit = MAX_BOOTSTRAP_SWEEP if search.get('sweep_started') else MAX_BOOTSTRAP
    if search['still'] >= 3 or search['attempts'] >= limit or not search['legs']:
        search['done'] = True
        _record(mem, 'map_bootstrap_exhausted',
                observed_metric={'attempts': search['attempts'], 'still': search['still'],
                                 'native_coordinate_verified': False},
                reason='二屋根を確認できないため初期探索を停止し、座標登録と確認入力は行わない')
        return []
    axis, direction, remaining = search['legs'][0]
    frames = min(MAX_HOLD, remaining)
    search['legs'][0][2] -= frames
    if search['legs'][0][2] == 0:
        search['legs'].pop(0)
    search['attempts'] += 1
    return [pad((('left', 'right') if axis == 0 else ('up', 'down'))[direction > 0], frames)]


def step(screen, mem, frame):
    chapter = mem.get('chapter')
    identity = run_identity(mem.get('_run_identity'))
    if (identity is None or chapter not in sources.SOURCE_CASTLES
            or chart.CASTLES.get(chapter)
            or (chart.approximate_map() and not mem.get('approx_fallback'))
            or frame is None or (frame.width, frame.height) != (256, 224)):
        return None
    state = mem.get('map_discovery')
    if not isinstance(state, dict) or state.get('scope') != [identity, chapter]:
        state = mem['map_discovery'] = {'scope': [identity, chapter], 'failed': {}}
        mem.pop('map_registration', None)
    tick, previous_tick = mem.get('tick'), state.get('last_tick')
    if previous_tick is not None and tick not in (previous_tick, previous_tick+1):
        for key in ('probe', 'roof_world', 'cursor_world', 'last_input', 'stationary',
                    'reanchor_stationary'):
            state.pop(key, None)
    state['last_tick'] = tick
    if any(mem.get(k) for k in ('battle', 'recall', 'month_sub', 'house', 'active',
                               'sortie_attempt', 'y_jump', 'resume_egg_ritual')):
        state.pop('probe', None)
        state.pop('roof_world', None)
        state.pop('cursor_world', None)
        return None
    probe = state.get('probe')
    if probe and screen.kind != 'map':
        if screen.kind not in {'castle_menu', 'castle_info', 'general_list', 'card_select',
                               'sortie_confirm', 'text', 'unknown'}:
            state.pop('probe', None)
            state.pop('roof_world', None)
            state.pop('cursor_world', None)
            return None
        mem['_map_registration_hold'] = True
        probe['age'] += 1
        if probe.get('closing') or probe['age'] > MAX_PANEL:
            probe['closing'] = True
            return [pad('b')] if probe['age'] <= 2*MAX_PANEL else []
        name = _label(screen, chapter)
        if name is None:
            probe['readings'] = 0
            if screen.kind == 'castle_menu':
                move = menu_to(screen, 'ステータス')
                return ([pad('a')] if move == 'here' else [move]) if move else []
            return []
        expected = probe.get('expected')
        if probe.get('name') not in (None, name):
            _record(mem, 'map_registration_rejected', observed_metric={'expected': expected, 'name': name},
                    reason='同じ確認中に実城名が変わったため座標を登録せず閉じる')
            probe['closing'] = True
            return [pad('b')]
        if expected is not None and name != expected:
            known = verified_castles(mem, chapter)
            if name not in known:
                _record(mem, 'map_registration_rejected', observed_metric={'expected': expected, 'name': name},
                        reason='探索候補と実城名が一致せず未確認の城のため座標を登録せず閉じる')
                probe['closing'] = True
                return [pad('b')]
            # The source hint picked the wrong castle, but the screen names a
            # location this run already measured. Re-anchor on that known name
            # instead of closing and reopening the same menu forever.
            probe['reanchor'] = True
            probe['expected'] = name
            _record(mem, 'map_search_candidate_mismatch', castle=name,
                    observed_metric={'expected': expected, 'name': name},
                    reason='探索候補と異なる既知城を確認したため、その城名でカメラを再アンカーする')
        probe['name'] = name
        tick = mem.get('tick')
        if type(tick) is not int or tick <= probe.get('read_tick', -1):
            return []
        probe['read_tick'] = tick
        probe['readings'] = probe.get('readings', 0)+1
        if probe['readings'] < 2:
            return []
        known = verified_castles(mem, chapter)
        if probe.get('reanchor'):
            if name not in known:
                probe['closing'] = True
                _record(mem, 'map_registration_rejected', castle=name,
                        reason='画面の連続性が切れた後は既知の城名で原点を再確認するまで新規地点を登録しない')
                state['reanchor_rejects'] = int(state.get('reanchor_rejects') or 0)+1
                if state['reanchor_rejects'] >= REANCHOR_REJECT_LIMIT:
                    # g1059 07:08: the cursor rested on an unregistered roof,
                    # and the walk only runs when no roof is under it, so the
                    # same status was re-read every ~10 s. Drop the whole run
                    # map; the next field view is a fresh origin, never mixed
                    # with the old coordinates.
                    dropped = sorted(known)
                    mem.pop('map_registration', None)
                    for key in ('origin_frame', 'camera', 'roof_world', 'cursor_world',
                                'candidate', 'stationary', 'last_input', 'bootstrap',
                                'reanchor_stationary', 'reanchor_tick', 'reanchor_walk',
                                'reanchor_rejects'):
                        state.pop(key, None)
                    _record(mem, 'map_registration_restarted', castle=name,
                            observed_metric={'dropped': dropped},
                            reason='既知の城へ戻れず未登録の城名の再確認が続くため、実測地図を破棄して原点から登録し直す')
                return [pad('b')]
            camera = [known[name][i]-probe['screen_cursor'][i] for i in (0, 1)]
            state['camera'] = camera
            state['cursor_world'] = list(known[name])
            state['roof_world'] = [[x+camera[0], y+camera[1]] for x,y in probe['screen_roofs']]
            state.pop('last_input', None)
            state.pop('reanchor_walk', None)
            state.pop('reanchor_rejects', None)
            probe['closing'] = True
            _record(mem, 'map_reanchored', castle=name,
                    reason='既知の実城名と自由カーソル地点を再確認してカメラ位置を復元')
            return [pad('b')]
        point = tuple(probe['position'])
        collision = any(other != name and sum(abs(a-b) for a,b in zip(point, old)) < 24
                        for other, old in known.items())
        changed = name in known and sum(abs(a-b) for a,b in zip(point, known[name])) > 8
        if collision or changed:
            probe['closing'] = True
            _record(mem, 'map_registration_rejected', observed_metric={'name': name},
                    reason='既存の実測地点と矛盾するため座標を登録しない')
            return [pad('b')]
        data = mem.setdefault('map_registration', {
            'schema': SCHEMA, 'identity': identity, 'chapter': chapter,
            'origin_frame': state['origin_frame'], 'castles': {}})
        data['castles'][name] = {'name': name, 'position': list(point), 'readings': 2,
            'field_frame': probe['field_frame'], 'status_frame': frame.digest(),
            'evidence': 'native_castle_status'}
        _ownership(mem, name, probe['roof_kind'])
        _record(mem, 'map_castle_confirmed', castle=name,
                observed_metric={'position': list(point), 'coordinate_space': 'chapter_local_field',
                                 'field_frame': probe['field_frame'], 'status_frame': frame.digest()},
                reason='屋根とスクロールを観測した自由カーソル地点を、二度の実城名読取で確認')
        probe['closing'] = True
        state.pop('candidate', None)
        mem['variant'] = 'chart' if chart.orders(chapter) else 'chart_unavailable'
        return [pad('b')]
    if screen.kind != 'map' or screen.marker or screen.cursor is None:
        state.pop('probe', None)
        state.pop('roof_world', None)
        state.pop('cursor_world', None)
        return None
    if probe:
        if not probe.get('closing'):
            # A disappeared menu is not a name receipt.
            _record(mem, 'map_registration_rejected', reason='城名を確認する前に地図へ戻ったため候補を破棄')
        state.pop('probe', None)
        return []
    known = verified_castles(mem, chapter)
    from ..strategy.orders import next_order
    if next_order(mem) is not None:
        return None
    roofs = [r for r in castle_roofs(frame) if not r['clipped']][:16]
    camera = _pose(state, roofs, tuple(screen.cursor))
    if camera is None:
        # A shape match after a gap does not identify a castle. Re-open a
        # stationary roof and require a known native name to restore origin.
        if state.get('origin_frame'):
            state.pop('roof_world', None)
            state.pop('cursor_world', None)
            mem['_map_registration_hold'] = True
            near = [r for r in roofs if max(abs(r['target'][i]-screen.cursor[i]) for i in (0,1)) <= 2]
            if len(near) == 1:  # the known name, not a second roof, fixes the camera
                stamp = [list(screen.cursor), [list(r['target']) for r in roofs]]
                if (state.get('reanchor_stationary') == stamp
                        and type(tick) is int and tick > state.get('reanchor_tick', tick)):
                    state['probe'] = {'reanchor': True, 'screen_cursor': list(screen.cursor),
                        'screen_roofs': [list(r['target']) for r in roofs],
                        'age': 0, 'expected': None, 'field_frame': frame.digest()}
                    return [pad('a')]
                state['reanchor_stationary'] = stamp
                state['reanchor_tick'] = tick
            elif not near and roofs and known:
                # A gap can leave the cursor away from every roof. Move only
                # toward a visible roof; its identity and the camera remain
                # unknown until the existing two-read reanchor succeeds.
                walk = state.setdefault('reanchor_walk', {'attempts': 0, 'still': 0})
                view = [list(screen.cursor), sorted(list(r['target']) for r in roofs)]
                walk['still'] = walk.get('still', 0) + 1 if walk.get('view') == view else 0
                walk['view'] = view
                if walk['still'] >= 3 or walk.get('attempts', 0) >= MAX_BOOTSTRAP:
                    return []
                target = min(roofs, key=lambda r: sum(abs(r['target'][i] - screen.cursor[i]) for i in (0, 1)))['target']
                dx, dy = (target[i] - screen.cursor[i] for i in (0, 1))
                axis = 0 if abs(dx) >= abs(dy) else 1
                distance = (dx, dy)[axis]
                walk['attempts'] = walk.get('attempts', 0) + 1
                return [pad((('left', 'right') if axis == 0 else ('up', 'down'))[distance > 0],
                            min(MAX_HOLD, abs(distance)))]
            return []
        return _bootstrap(screen, mem, state, roofs)
    state.setdefault('origin_frame', frame.digest())
    expected, hint = _candidate(mem, state, known)
    if expected is None:
        if set(sources.SOURCE_CASTLES[chapter]) <= set(known):
            return None  # exhausted chart/interim selection keeps its owner
        mem['_map_registration_hold'] = True
        return []
    if state.get('candidate') != expected:
        state.update(candidate=expected, attempts=0)
        _record(mem, 'map_search_candidate', castle=expected,
                observed_metric={'source_flag_bbox': list(sources.SOURCE_CASTLES[chapter][expected]),
                                 'search_hint': list(hint) if hint else None,
                                 'native_coordinate_verified': False},
                reason='公開資料の点は探索候補だけに使い、実城名を確認するまで出撃先に使わない')
    state['attempts'] += 1
    if state['attempts'] > MAX_PROBE:
        mem['_map_registration_hold'] = True
        state['failed'][expected] = int(mem.get('tick') or 0)
        state.pop('candidate', None)
        _record(mem, 'map_search_exhausted', castle=expected, reason='候補探索の観測上限に達したため入力を止めて別候補へ戻す')
        return []
    cursor = tuple(screen.cursor)
    visible = [r for r in roofs if not known or all(
        sum(abs(a-b) for a,b in zip((r['target'][0]+camera[0], r['target'][1]+camera[1]), p)) > 20
        for p in known.values())]
    near = sorted(visible, key=lambda r: sum(abs(r['target'][i]+camera[i]-hint[i]) for i in (0,1))
                  if hint else sum(abs(r['target'][i]-cursor[i]) for i in (0,1)))
    roof = near[0] if near and (hint is None or sum(
        abs(near[0]['target'][i]+camera[i]-hint[i]) for i in (0,1)) <= 32) else None
    goal = tuple(roof['target']) if roof else (hint[0]-camera[0], hint[1]-camera[1])
    dx, dy = goal[0]-cursor[0], goal[1]-cursor[1]
    mem['_map_registration_hold'] = True
    if roof and abs(dx) <= 2 and abs(dy) <= 2:
        stamp = [list(cursor), list(camera), list(roof['target']), roof['kind']]
        stable = (state.get('stationary') == stamp and type(tick) is int
                  and tick > state.get('stationary_tick', tick))
        state['stationary'] = stamp
        state['stationary_tick'] = tick
        if not stable:
            return []
        state['probe'] = {'position': [cursor[0]+camera[0], cursor[1]+camera[1]],
                          'field_frame': frame.digest(), 'age': 0,
                          'roof_kind': roof['kind'],
                          'screen_cursor': list(cursor),
                          'screen_roofs': [list(r['target']) for r in roofs],
                          'expected': expected if known else None}
        state.pop('last_input', None)
        return [pad('a')]
    state.pop('stationary', None)
    if not dx and not dy:
        # A source hint without a visible roof never authorizes A.
        return []
    axis = 0 if abs(dx) >= abs(dy) else 1
    state['last_input'] = [axis, 1 if (dx, dy)[axis] > 0 else -1,
                           min(MAX_HOLD, max(1, max(abs(dx), abs(dy))))]
    return [pad(('right' if dx > 0 else 'left') if abs(dx) >= abs(dy)
                else ('down' if dy > 0 else 'up'), min(MAX_HOLD, max(1, max(abs(dx), abs(dy)))))]
