"""Post-month roster-first status / recovery sequence.

The monthly command finishes before this owner opens the global X menu.  A
complete individual reading precedes any recall, house dispatch or map order.
All commands are bounded; an unverified location is never treated as a castle.
"""
from __future__ import annotations

from .. import decision_log
from ..os_layer import roster as receipts
from ..os_layer.commands import menu_to, pad
from ..os_layer.readers import _roof_under_cursor, world_markers
from ..os_layer.screen import castle_roofs, own_camps
from ..state.regions import flow as _flow
from . import house, recall as recall_flow, map_registration
from copy import deepcopy

MAX_STEPS = 900
MAX_SCAN = receipts.MAX_GENERALS
MENU_RETRIES = 4
STATUS_RETRIES = 12
MARKER_RETRIES = 4  # bounded roster frames before status A; avoids transient sprite misses
LOCATE_RETRIES = 2  # retry the named SELECT -> actual castle status, never infer a castle
# Menus this owner never opens while it waits for the X menu or the map. A
# castle menu left over from a swallowed A used to answer [] forever (live
# 2026-11-06 g1139 48-10: しゅつげき/ステータス open, overlay 「task_menu」, no
# input), and the generic no-input fallback never fired because the animated
# map changes the frame digest on every observation.
STRAY_MENU_KINDS = frozenset({'castle_menu', 'castle_info', 'general_list'})
STRAY_MENU_BACKOUTS = 12
MARCH = frozenset({'いまこちらにむかっています', 'しんぐんちゅうです',
                   'いどうしています'})
CAMP = frozenset({'キャンプしています', 'キャンプのなかにいます'})
CASTLE = frozenset({'しろのなかにいます'})


def _log(mem, event, **details):
    decision_log._record(mem, event, **details)


def _tasks(statuses, names):
    from ... import hanjuku_chart as chart
    queue = []
    for name in names:
        info = statuses[name]
        if info.get('location') == 'camp':
            # #3505: a camp whose roster triangle maps onto the boss castle is a
            # siege in progress, not a straggler. ``marker_castle`` already binds
            # castle_candidate only within MAP_CASTLE_TOLERANCE, so "boss-adjacent"
            # reuses that measured tolerance instead of a new arbitrary threshold
            # (g1133: a general camped at field [824,176] against the boss at
            # (818,184) and the monthly recall dragged him back, cancelling the
            # boss fight).
            marker = info.get('marker')
            candidate = marker.get('castle_candidate') if isinstance(marker, dict) else None
            if isinstance(candidate, str) and chart.is_boss_castle(candidate):
                continue
            queue.append({'kind': 'recall', 'name': name})
        elif info.get('location') == 'castle':
            if not info.get('castle'):
                queue.append({'kind': 'locate', 'name': name})
            if info.get('broken'):
                queue.append({'kind': 'repair', 'name': name})
    return queue


def arm(mem):
    """The month alone never invalidates a verified per-general reading."""
    scope = [mem.get('chapter'), mem.get('month')]
    if not scope[0] or not scope[1]:
        return
    old = mem.get('post_month_status') or {}
    if old.get('scope') == scope:
        return
    mem.pop('post_month_status', None)  # discard an obsolete previous-month owner
    identity = receipts.run_identity(mem.get('_run_identity'))
    cache = receipts._persistent_status(mem)
    statuses = deepcopy(cache['statuses']) if cache is not None else {}
    stale = set((cache or {}).get('dirty') or {})
    # A moving general can reach a castle, camp or battle without an explicit
    # status callback. Stationary castle residents need no periodic re-open.
    from ..os_layer.world import _owned
    owned = _owned(mem)
    for name, row in statuses.items():
        if (row.get('location') in ('march', 'unknown')
                or row.get('location') == 'castle'
                   and (not row.get('castle') or row['castle'] not in owned)):
            stale.add(name)
    pending = list((cache or {}).get('pending_joins') or [])
    pending.extend(name for name in mem.get(receipts.PENDING_JOINS_KEY) or []
                   if name not in statuses and name not in pending)
    count = mem.get('country_general_count') or {}
    count_mismatch = (isinstance(count, dict)
                      and count.get('scope') == scope and count.get('identity') == identity
                      and type(count.get('count')) is int
                      and count['count'] != len(statuses))
    scan = cache is None or bool(stale or pending or count_mismatch)
    if not scan and not _tasks(statuses, list(statuses)):
        # Common case: zero input and zero roster navigation this month.
        mem['post_month_status_last'] = {
            'scope': scope, 'statuses': statuses, 'complete': True,
            'result': 'unchanged_cached', 'tick': int(mem.get('tick') or 0)}
        _log(mem, 'post_month_status_reused',
             observed_metric={'generals': len(statuses), 'reopened': 0},
             reason='月替わりだけでは将軍の確定済み状態を無効化しない')
        return
    mem['post_month_status'] = {
        'scope': scope, 'identity': identity,
        'stage': 'await_map' if scan else 'process', 'statuses': statuses,
        'dirty': sorted(stale), 'names': [] if scan else list(statuses),
        'tasks': [] if scan else _tasks(statuses, list(statuses)),
        'steps': 0, 'phase_reads': 0, 'complete': not scan,
        'started_tick': int(mem.get('tick') or 0)}
    _log(mem, 'post_month_status_armed',
         observed_metric={'scope': scope, 'cached': len(statuses),
                          'reopen': sorted(stale), 'joins': pending,
                          'count_mismatch': count_mismatch},
         reason=('変更があり得る将軍だけ個別ステータスを再取得する'
                 if scan else '既知の帰還・修理タスクのみ実行する'))


def _stage(state, name, **fields):
    state.update(stage=name, phase_reads=0, **fields)


def _location(screen):
    text = house.location_row(screen)
    if text in CAMP:
        return 'camp'
    if text in CASTLE:
        return 'castle'
    if text in MARCH:
        return 'march'
    return 'unknown'


def _marker(mem, frame):
    """Record the roster selection triangle and bind its castle from the map.

    The roster draws a white/gray inverted triangle over the selected general.
    The red army marker of the Y view is a different sprite and sits on the
    hero, so it cannot answer "which castle is this general in". The measured
    roster marker is therefore read first; the older red-triangle/camera
    heuristic stays only as a fallback for frames where that sprite is absent.
    """
    if frame is None:
        return None
    from ..os_layer.readers import marker_castle, selection_marker
    marker = selection_marker(frame) if hasattr(frame, 'pixel') else None
    if marker is not None:
        result = {'box': list(marker['box']), 'source': 'selection_marker'}
        mapped = marker_castle(frame, mem.get('chapter') or 0)
        # A triangle without any castle flag on the same map is not enough to
        # name a castle; leave it to the SELECT/status confirmation task.
        if isinstance(mapped, dict) and int(mapped.get('icons') or 0) >= 1:
            result['castle_candidate'] = mapped['castle']
            result['mapping'] = mapped
            result['icons_seen'] = mapped['icons']
        else:
            # Distinguish a missing flag sprite from a visible flag with an
            # uncalibrated or ambiguous chart match in live diagnostics.
            from ..os_layer.readers import _map_icon_centres
            result['icons_seen'] = len(_map_icon_centres(frame))
        return result
    boxes = [b for b in world_markers(frame)
             if b[2] < 164 and b[3] < 150]
    if len(boxes) != 1:
        return None
    box = boxes[0]
    result = {'box': list(box), 'source': 'roster_triangle_candidate'}
    last = mem.get('nav_last') or {}
    cell = mem.get('cursor')
    if (mem.get('uncertain') or last.get('kind') != 'map'
            or not isinstance(cell, (list, tuple)) or len(cell) != 2
            or not isinstance(last.get('screen'), list)
            or len(last['screen']) != 2
            or type(mem.get('tick')) is not int
            or type(last.get('tick')) is not int
            or not 0 <= mem['tick'] - last['tick'] <= 30):
        return result
    # Main-menu roster draws on top of the last field camera. The camera did
    # not move while the roster is open; retain only a unique near-castle hit.
    from ... import hanjuku_chart as chart
    mx = (box[0] + box[2]) / 2
    my = (box[1] + box[3]) / 2
    camera = [cell[a] - last['screen'][a] for a in (0, 1)]
    near = [name for name, xy in chart.castles(mem.get('chapter') or 0).items()
            if abs(xy[0] - mx - camera[0]) <= 8
            and abs(xy[1] - my - camera[1]) <= 8
            and not chart.is_boss_castle(name)]
    if len(near) == 1:
        result['castle_candidate'] = near[0]
    return result


def _assign(mem, name, castle, *, evidence):
    """Make an observed residence immediately available to the planner."""
    from ..os_layer.world import _owned
    if castle not in _owned(mem):
        return False
    garrison = mem.setdefault('garrison', {})
    for other, names in garrison.items():
        if other != castle and isinstance(names, list) and name in names:
            names.remove(name)
    occupants = garrison.setdefault(castle, [])
    if name not in occupants:
        occupants.append(name)
    row = (mem.get('post_month_status') or {}).get('statuses', {}).get(name)
    if isinstance(row, dict):
        row.update(castle=castle, castle_evidence=evidence)
    unknown = mem.get('general_location_unknown')
    if isinstance(unknown, list) and name in unknown:
        unknown.remove(name)
    _log(mem, 'post_month_location_confirmed', general=name, castle=castle,
         reason='将軍本人の現在位置と城の実表示を対応させて在城リストを更新')
    return True


def _forget_field_residents(mem, name):
    for occupants in (mem.get('garrison') or {}).values():
        if isinstance(occupants, list) and name in occupants:
            occupants.remove(name)


def _complete_scan(mem, state):
    if (not state['names'] or len(set(state['names'])) != len(state['names'])
            or not state.get('top_confirmed')
            or any(name not in state['statuses'] for name in state['names'])
            or any(name in state.get('dirty', ()) for name in state['names'])
            or any(state['statuses'][name].get('location') == 'unknown'
                   for name in state['names'])):
        return False
    country = mem.get('country_general_count') or {}
    if (isinstance(country, dict) and country.get('scope') == state['scope']
            and country.get('identity') == state['identity']
            and type(country.get('count')) is int
            and country['count'] != len(state['names'])):
        # A mismatched national headcount means the roster was not complete
        # (or changed mid-visit). Do not delete a general by extrapolation.
        return False
    state['complete'] = True
    state['steps'] = 0
    # A *complete* roster walk proves that missing names have left. A battle
    # defeat by itself never proves death. Exclude absent names and all their
    # pending/field references only after this membership proof.
    missing = set(state['statuses']) - set(state['names'])
    for name in missing:
        receipts.record_departure(mem, name)
        _forget_field_residents(mem, name)
        for key in ('house_eggs', 'egg_types', 'egg_uses'):
            if isinstance(mem.get(key), dict):
                mem[key].pop(name, None)
        for key in ('general_location_unknown', 'egg_recheck', 'roster_pending_joins'):
            if isinstance(mem.get(key), list):
                mem[key] = [actor for actor in mem[key] if actor != name]
        _log(mem, 'post_month_general_departed', general=name,
             reason='将軍名簿を末尾まで実測したが本人がいないため旧状態を除籍')
    state['statuses'] = {name: state['statuses'][name] for name in state['names']}
    state['tasks'] = _tasks(state['statuses'], state['names'])
    mem['general_statuses'] = {
        'scope': list(state['scope']), 'identity': state['identity'],
        'statuses': state['statuses'], 'dirty': {}, 'pending_joins': [],
        'suspected_departures': [], 'complete': True,
        'tick': int(mem.get('tick') or 0)}
    queue = state['tasks']
    # Commit the same full-survey contract that the existing planner, payroll
    # and egg-repair logic already understand. This is a verified per-general
    # scan, unlike the earlier month-menu names-only count.
    mem['recruit_roster'] = {
        'chapter': state['scope'][0], 'month': state['scope'][1],
        'tick': state['started_tick'], 'identity': state['identity'],
        'names': list(state['names']),
        'wages': {name: receipts.fixed_wage(name) for name in state['names']},
        'complete': False}
    receipts.complete(mem, {
        'roster_started': state['started_tick'], 'seen': list(state['names']),
        'roster_end_observed': True})
    _stage(state, 'process')
    _log(mem, 'post_month_status_complete',
         observed_metric={'generals': list(state['names']),
                          'tasks': list(queue)},
         reason='全将軍の個別卵・所在地を確認したため帰還・修理・所在確定を開始')
    return True


def _finish(mem, state):
    mem['post_month_status_last'] = {
        'scope': list(state['scope']), 'complete': bool(state['complete']),
        'statuses': state['statuses'], 'failed': state.get('failure'),
        'tick': int(mem.get('tick') or 0)}
    mem.pop('post_month_status', None)
    _log(mem, 'post_month_sequence_finished',
         observed_metric={'complete': bool(state['complete']),
                          'remaining': list(state.get('tasks') or [])},
         reason='月次の個別確認と状態別の処理を終えて通常攻略の計画へ制御を返す')


def _task_complete(mem, state, *, result):
    job = state.get('job') or {}
    if job.get('kind') == 'locate' and result != 'castle_verified':
        name = job.get('name')
        if name and name not in mem.setdefault('general_location_unknown', []):
            mem['general_location_unknown'].append(name)
        _forget_field_residents(mem, name)
    _log(mem, 'post_month_task_result',
         general=job.get('name'), resulting_event=result,
         observed_metric={'task': job.get('kind')},
         reason='将軍別の処理を終了し次の将軍の処理へ進む')
    state.pop('job', None)
    state['steps'] = 0
    _stage(state, 'process')


def _retry_location(mem, state, screen, reason):
    """Revisit the same general before discarding a location without proof."""
    general = (state.get('job') or {}).get('name')
    count = int(state.get('locate_retries') or 0)
    _log(mem, 'post_month_castle_location_unverified', general=general,
         resulting_event=reason, observed_metric={'attempt': count + 1},
         reason='SELECTの後に城名が確認できなかったため同じ将軍を再確認する')
    if count < LOCATE_RETRIES:
        state['locate_retries'] = count + 1
        _stage(state, 'locate_reset')
    else:
        _task_complete(mem, state, result=reason)
    return [pad('b')] if screen.kind in {
        'castle_menu', 'castle_info', 'main_menu', 'general_list', 'text'} else []


def reenter_after_reanchor(mem, resume):
    """Reopen this month's workflow from the recovered map, without blind A.

    SELECT reanchor always lands on アルマムーン, not the general whose status
    or castle was being inspected.  Preserve verified statuses, tasks and the
    pending job, but discard only the menu/cursor phase whose screen vanished.
    """
    state = mem.get('post_month_status')
    if (not isinstance(state, dict)
            or state.get('scope') != resume.get('scope')
            or state.get('scope') != [mem.get('chapter'), mem.get('month')]
            or state.get('identity') != receipts.run_identity(mem.get('_run_identity'))):
        return False
    if resume.get('owner_reentered'):
        return True
    previous = state.get('stage')
    if previous in {'roster', 'status', 'roster_next'}:
        # The prior roster highlight disappeared when SELECT moved the camera.
        # A fresh top-to-bottom membership proof is required; cached clean
        # individual statuses remain and dirty entries are reread.
        state['names'] = []
        state['list_moves'] = 0
        state['top_confirmed'] = False
        for key in ('highlight', 'selected', 'marker_reads', 'markers', 'top_seeks'):
            state.pop(key, None)
        _stage(state, 'open_menu')
        state['phase_reads'] = 2  # next map observation opens X immediately
    elif previous in {'task_menu', 'task_select', 'task_jump',
                      'locate_open', 'locate_info', 'locate_reset'}:
        # A task SELECT originally jumped to its general, but the recovery
        # SELECT moved to home.  Re-select the *named* general before A.
        _stage(state, 'task_menu' if state.get('job') else 'process', seeks=0)
        if state['stage'] == 'task_menu':
            state['phase_reads'] = 2
    elif previous == 'open_menu':
        state['phase_reads'] = 2
    elif previous not in {'await_map', 'process', 'blocked', 'wait_repair', 'wait_recall'}:
        return False
    resume['owner_reentered'] = True
    _log(mem, 'post_month_reanchor_reenter',
         observed_metric={'from': previous, 'to': state.get('stage'),
                          'cached': len(state.get('statuses') or {}),
                          'pending': len(state.get('tasks') or [])},
         reason='カーソル復旧後、月次処理を元の段階から安全に開き直す')
    return True


def step(screen, mem, frame):
    """Return actions/[] while owned; None when unrelated foreground wins."""
    state = mem.get('post_month_status')
    if not isinstance(state, dict):
        return None
    if (state.get('scope') != [mem.get('chapter'), mem.get('month')]
            or state.get('identity') != receipts.run_identity(mem.get('_run_identity'))):
        mem.pop('post_month_status', None)
        return None
    if (mem.get('battle') or mem.get('month_sub')
            or screen.kind in {'battle', 'battle_menu', 'egg_battle_menu',
                               'egg_choice_menu', 'monster_menu', 'month_menu',
                               'attack_started', 'defense_started',
                               'boss_attack_started', 'field_encounter_started'}):
        return None
    # A scripted dialogue/event is not this owner's screen. The menu stages
    # below would answer [] and starve the ordinary A handler: g1133 stalled on
    # the new-partner ceremony at 2026-11-06 02:56 right after the status scan,
    # with every later observation still reading 'task_menu'. Only 'status'
    # reads a text panel (the individual general status) and the wait_* stages
    # hand the frame to recall/house; everything else releases it.
    if (screen.kind == 'text'
            and state['stage'] not in {'status', 'wait_repair', 'wait_recall'}
            and house.roster(screen) is None):
        return None
    # The monthly workflow owns this map.  A lost cursor is a temporary
    # navigation interrupt, not a reason to let the sortie planner take over.
    if (screen.kind == 'map' and screen.cursor and not screen.marker
            and mem.get('nav_search')
            and state.get('stage') not in {'blocked', 'wait_repair', 'wait_recall'}):
        from . import map as map_flow
        recovery = map_flow._start_reanchor_jump(mem, owner='post_month_status')
        if recovery is not None:
            return recovery
    if state['stage'] in {'open_menu', 'task_menu', 'blocked'}:
        if screen.kind in STRAY_MENU_KINDS and not house.roster(screen):
            backouts = state.get('stray_backouts')
            backouts = backouts if type(backouts) is int and backouts >= 0 else 0
            if backouts < STRAY_MENU_BACKOUTS:
                state['stray_backouts'] = backouts + 1
                if backouts == 0:
                    _log(mem, 'post_month_stray_menu_backout', screen=screen.kind,
                         observed_metric={'stage': state['stage']},
                         reason='月越し後の確認が待つX・マップ画面ではなく城メニュー等が残っているため、Bで戻る')
                return [pad('b')]
        else:
            state.pop('stray_backouts', None)
    state['steps'] += 1
    stage = state['stage']
    if state['steps'] > MAX_STEPS and stage not in {'wait_repair', 'wait_recall'}:
        state['failure'] = 'observation_budget'
        _log(mem, 'post_month_status_unverified',
             observed_metric={'stage': stage, 'names': list(state['statuses'])},
             reason='確認上限に達したため未確認情報を城の所在地として扱わない')
        _stage(state, 'blocked')
        return []
    state['phase_reads'] += 1
    if stage == 'blocked':
        return []
    if stage == 'await_map':
        if screen.kind == 'map':
            _stage(state, 'open_menu')
            return [pad('x')]
        return None
    if stage == 'open_menu':
        if house.roster(screen):
            _stage(state, 'roster')
            return []
        if screen.kind == 'main_menu' and screen.hand:
            direction = menu_to(screen, 'しょうぐん')
            if direction == 'here':
                _stage(state, 'roster')
                return [pad('a')]
            return [direction] if direction else []
        if screen.kind == 'map' and state['phase_reads'] in (3, 6):
            return [pad('x')]
        return []
    if stage == 'roster':
        names = house.roster(screen)
        if not names or screen.selected not in names:
            return []
        # A SELECT or a previous roster visit can leave the list scrolled to
        # the middle. Only a walk beginning at the hero/top can prove a
        # defeated general absent; a partial tail must never evict residents.
        if not state.get('top_confirmed'):
            if names[0] == 'どうし' and screen.selected == 'どうし':
                state['top_confirmed'] = True
            else:
                state['top_seeks'] = state.get('top_seeks', 0) + 1
                if state['top_seeks'] > MAX_SCAN:
                    state['failure'] = 'roster_top_unconfirmed'
                    _stage(state, 'blocked')
                    return [pad('b')]
                return [pad('up')]
        selected = screen.selected
        if selected in state['names']:
            first = state['names'][0] if state['names'] else None
            at_end = (house._roster_scroll_end(screen, frame)
                      or (len(names) < receipts.VISIBLE_NAME_ROWS
                          and selected == names[-1]))
            wrapped = selected == first and state.get('list_moves', 0) > 0
            if (at_end or wrapped) and _complete_scan(mem, state):
                return [pad('b')]
            if state['phase_reads'] > STATUS_RETRIES:
                state['failure'] = 'roster_end_unconfirmed'
                _log(mem, 'post_month_status_unverified',
                     reason='将軍一覧の末尾が確認できず、通常の出撃計画を保留')
                _stage(state, 'blocked')
                return [pad('b')]
            state['list_moves'] = state.get('list_moves', 0) + 1
            return [pad('down')]
        if selected in state['statuses'] and selected not in state.get('dirty', ()):
            # The roster page establishes current membership, but does not
            # require pressing A on a cached, unchanged general.
            state['names'].append(selected)
            state['list_moves'] = state.get('list_moves', 0) + 1
            return [pad('down')]
        # Wait for a stable highlight so that the triangle belongs to this
        # general, not to the preceding row's last rendered frame.
        if state.get('highlight') != selected:
            state['highlight'] = selected
            state['marker_reads'] = 0
            return []
        # The white selection triangle can be absent in one rendered frame.
        # The old path sampled it once then unconditionally entered the status
        # panel, so every failed sample became an expensive SELECT visit.
        marker = _marker(mem, frame)
        reads = int(state.get('marker_reads') or 0) + 1
        state['marker_reads'] = reads
        candidate = marker.get('castle_candidate') if isinstance(marker, dict) else None
        if frame is not None and not candidate and reads < MARKER_RETRIES:
            return []  # stay on the same highlighted general for another frame
        state.setdefault('markers', {})[selected] = marker
        if not candidate:
            _log(mem, 'post_month_roster_marker_unverified', general=selected,
                 observed_metric={'reads': reads, 'marker_seen': isinstance(marker, dict),
                                  'source': marker.get('source') if isinstance(marker, dict) else None,
                                  'icons_seen': (marker.get('icons_seen')
                                                 if isinstance(marker, dict) else None),
                                  'marker_box': (marker.get('box')
                                                 if isinstance(marker, dict) else None)},
                 reason='一覧の三角マークから城を確定できず個別状態確認後にSELECTへ委ねる')
        else:
            _log(mem, 'post_month_roster_marker_mapped', general=selected, castle=candidate,
                 observed_metric={'reads': reads, 'source': marker.get('source')},
                 reason='一覧の選択三角マークから在城候補を観測した')
        state['selected'] = selected
        _stage(state, 'status')
        return [pad('a')]
    if stage == 'status':
        info = house._observe_status(screen, mem)
        if not info or info.get('general') != state.get('selected'):
            if state['phase_reads'] >= STATUS_RETRIES:
                state['failure'] = 'status_unreadable'
                _log(mem, 'post_month_status_unverified',
                     general=state.get('selected'),
                     reason='将軍個別の状態を読めないため通常の出撃計画を保留')
                _stage(state, 'blocked')
                return [pad('b')]
            return []
        name = info['general']
        loc = _location(screen)
        marker = (state.get('markers') or {}).get(name)
        row = {**info, 'location': loc, 'status_text': house.location_row(screen),
               'marker': marker, 'tick': int(mem.get('tick') or 0)}
        state['names'].append(name)
        state['statuses'][name] = row
        if name in state.get('dirty', []):
            state['dirty'].remove(name)
        if loc in ('camp', 'march'):
            _forget_field_residents(mem, name)
        elif loc == 'castle':
            # Existing named castle rosters are still valid when the observed
            # status says resident. Do not repeat a costly SELECT for them.
            old = [castle for castle, residents in (mem.get('garrison') or {}).items()
                   if isinstance(residents, list) and name in residents]
            candidate = marker.get('castle_candidate') if isinstance(marker, dict) else None
            if candidate:
                if not _assign(mem, name, candidate, evidence='roster_triangle'):
                    _log(mem, 'post_month_roster_castle_rejected', general=name, castle=candidate,
                         reason='三角マークの候補城が現在の所有城ではないため在城として確定しない')
            elif len(old) == 1:
                from ..os_layer.world import _owned
                if old[0] in _owned(mem):
                    row.update(castle=old[0], castle_evidence='previous_named_roster')
        _stage(state, 'roster_next')
        return [pad('b')]
    if stage == 'roster_next':
        if house.roster(screen):
            _stage(state, 'roster')
            state['list_moves'] = state.get('list_moves', 0) + 1
            return [pad('down')]
        return []
    if stage == 'process':
        if screen.kind != 'map':
            if screen.kind in {'main_menu', 'castle_menu', 'castle_info', 'general_list'} or house.roster(screen):
                return [pad('b')]
            return None
        if not state['tasks']:
            _finish(mem, state)
            return []
        job = state['tasks'].pop(0)
        state['job'] = job
        if job['kind'] == 'repair' and not state['statuses'][job['name']].get('castle'):
            _task_complete(mem, state, result='location_unverified')
            return []
        _stage(state, 'task_menu', seeks=0, locate_retries=0)
        return [pad('x')]
    if stage == 'task_menu':
        if house.roster(screen):
            _stage(state, 'task_select', seeks=0)
            return []
        if screen.kind == 'main_menu' and screen.hand:
            direction = menu_to(screen, 'しょうぐん')
            if direction == 'here':
                _stage(state, 'task_select', seeks=0)
                return [pad('a')]
            return [direction] if direction else []
        if screen.kind == 'map' and state['phase_reads'] in (3, 6):
            return [pad('x')]
        return []
    if stage == 'task_select':
        names = house.roster(screen)
        if not names:
            return []
        job = state['job']
        if screen.selected == job['name']:
            _stage(state, 'task_jump')
            return [pad('select')]
        if state.get('seeks', 0) >= MAX_SCAN:
            _task_complete(mem, state, result='general_not_found')
            return [pad('b')]
        state['seeks'] += 1
        direction = menu_to(screen, job['name'])
        return [pad('down')] if direction is None else [pad('a')] if direction == 'here' else [direction]
    if stage == 'task_jump':
        job = state['job']
        name = job['name']
        if screen.kind == 'world_map':
            return [pad('y')]
        if screen.kind != 'map':
            if (job['kind'] == 'locate' and house.roster(screen)
                    and screen.selected == name
                    and state['phase_reads'] in (4, 8)):
                # A SELECT can be lost during the roster's closing animation.
                return [pad('select')]
            if state['phase_reads'] >= STATUS_RETRIES:
                if job['kind'] == 'locate':
                    return _retry_location(mem, state, screen, 'select_jump_unconfirmed')
                _task_complete(mem, state, result='jump_unconfirmed')
                return [pad('b')]
            return []
        if job['kind'] == 'locate':
            # SELECT is the observed anchor for *this named general*. The
            # cursor itself can completely cover the castle roof, so the
            # roof detector is not a prerequisite for opening its menu.
            _stage(state, 'locate_open')
            return [pad('a')]
        if job['kind'] == 'recall':
            camps = own_camps(frame) if frame is not None else []
            cursor = screen.cursor
            if not camps and cursor is None:
                _task_complete(mem, state, result='camp_cursor_unobserved')
                return []
            # SELECT in the named roster centres the field cursor on that
            # general. A tent hidden under its cursor can still be opened by
            # recall.py's one-shot occlusion path; never reuse an older map
            # coordinate without this observed SELECT->map transition.
            camp = (min(camps, key=lambda c: sum(abs(c['target'][a] - cursor[a])
                                                 for a in (0, 1)) if cursor else 0)
                    if camps else {'target': list(cursor), 'kind': None})
            if (cursor and sum(abs(camp['target'][a] - cursor[a])
                               for a in (0, 1)) > 12):
                _task_complete(mem, state, result='selected_general_camp_unconfirmed')
                return []
            _flow(mem)['recall'] = {
                'stage': 'to_camp', 'target': list(camp['target']),
                'last_seen': list(camp['target']), 'steps': 0,
                'general': name, 'camp_kind': camp.get('kind')}
            _stage(state, 'wait_recall', recall_started=True)
            recall = recall_flow.camp_recall_step(screen, mem, frame)
            return recall if recall is not None else []
        if job['kind'] == 'repair':
            if mem.get('house'):
                return []
            source = state['statuses'][name].get('castle')
            if not source or not _roof_under_cursor(screen, frame):
                _task_complete(mem, state, result='repair_source_unconfirmed')
                return []
            # SELECT already put the cursor on this general. Read the actual
            # castle status before handing the existing guarded house FSM its
            # castle_sortie / card / destination / receipt transaction.
            mem['house'] = {
                'phase': 'castle_verify', 'chapter': mem['chapter'],
                'roster_month': mem.get('month'), 'age': 0, 'total': 0,
                'seen': [], 'pending': [], 'month_scan': False,
                'general': name, 'source': source, 'castles': []}
            _stage(state, 'wait_repair')
            return [pad('a')]
        _task_complete(mem, state, result='unknown_task')
        return []
    if stage == 'locate_open':
        if screen.kind in ('castle_menu', 'castle_info'):
            _stage(state, 'locate_info')
            return []
        if screen.kind == 'map' and state['phase_reads'] in (3, 6):
            # The first A may have been swallowed while SELECT closed the
            # roster; press again only on a confirmed map, boundedly.
            return [pad('a')]
        if state['phase_reads'] >= STATUS_RETRIES:
            return _retry_location(mem, state, screen, 'castle_menu_not_opened')
        return []
    if stage == 'locate_info':
        name = state['job']['name']
        # The native status panel keeps the castle menu visible behind it, so
        # the frame stays classified 'castle_menu' (the しゅつげき/ステータス box
        # is still drawn) even after ステータス is opened. Never claim a location
        # from the menu header alone: _label answers only when the panel text
        # carries the CASTLE_STATUS rows (name + しゅうにゅう/レベル/しょうぐん).
        castle = map_registration._label(screen, mem.get('chapter'))
        if castle is not None and _assign(mem, name, castle, evidence='native_castle_name'):
            _task_complete(mem, state, result='castle_verified')
            return [pad('b')]
        if screen.kind == 'castle_menu' and screen.hand:
            toward = menu_to(screen, 'ステータス')
            if toward == 'here' and state['phase_reads'] in (1, 4, 8):
                return [pad('a')]
            if toward and toward != 'here':
                return [toward]
        if state['phase_reads'] >= STATUS_RETRIES:
            return _retry_location(mem, state, screen, 'castle_status_not_read')
        return []
    if stage == 'locate_reset':
        if screen.kind == 'map':
            _stage(state, 'task_menu', seeks=0)
            return [pad('x')]
        if screen.kind in ('castle_menu', 'castle_info', 'main_menu', 'general_list', 'text'):
            return [pad('b')]
        return []
    if stage == 'wait_recall':
        if _flow(mem).get('recall'):
            recall = recall_flow.camp_recall_step(screen, mem, frame)
            return recall if recall is not None else []
        _task_complete(mem, state, result='recall_finished_or_deferred')
        return []
    if stage == 'wait_repair':
        if mem.get('house'):
            return house.step(screen, mem, frame) or []
        _task_complete(mem, state, result='house_flow_finished')
        return []
    return []
