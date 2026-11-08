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
from ..state.regions import flow as _flow, world as _world
from . import house, recall as recall_flow, map_registration
from copy import deepcopy

MAX_STEPS = 900
MAX_SCAN = receipts.MAX_GENERALS
MENU_RETRIES = 4
STATUS_RETRIES = 12
MARKER_RETRIES = 4  # bounded roster frames before status A; avoids transient sprite misses
LOCATE_RETRIES = 2  # retry the named SELECT -> actual castle status, never infer a castle
# g1147 2026-11-07 1-8: an 8-name roster fills the visible page, so the
# short-page end proof never applies. The pixel end proof needs a 256x224
# frame, but live frames are 299x224, and this list does not wrap: down on
# the last row is a no-op (13 identical frames then roster_end_unconfirmed).
# After this many consecutive already-visited observations stalled on the
# visible last row of a full walk, a passing _complete_scan is the end proof.
END_STALL_READS = 3
# Menus this owner never opens while it waits for the X menu or the map. A
# castle menu left over from a swallowed A used to answer [] forever (live
# 2026-11-06 g1139 48-10: しゅつげき/ステータス open, overlay 「task_menu」, no
# input), and the generic no-input fallback never fired because the animated
# map changes the frame digest on every observation.
STRAY_MENU_KINDS = frozenset({'castle_menu', 'castle_info', 'general_list'})
STRAY_MENU_BACKOUTS = 12
# docich#3659: the #3505 boss-adjacent camp exemption was indefinite, so a
# general camped on the boss castle could neither be recalled nor re-ordered
# toward it (no camp->boss route exists). Bound the exemption: after this many
# consecutive months the monthly pass queues the recall again, returning him
# to the planner's garrison pool so a later boss wave can send him.
BOSS_CAMP_EXEMPT_MONTHS = 3
MARCH = frozenset({'いまこちらにむかっています', 'しんぐんちゅうです',
                   'いどうしています'})
CAMP = frozenset({'キャンプしています', 'キャンプのなかにいます'})
CASTLE = frozenset({'しろのなかにいます'})


def _log(mem, event, **details):
    decision_log._record(mem, event, **details)


def _boss_camp_holds_siege(info):
    """Whether a boss-adjacent camp still justifies keeping the general there.

    #3505/#3512: a general camped just outside the boss castle is part of an
    in-progress boss attack; a monthly recall would cancel it. A broken egg is
    the signal for the repair task below, but that task only starts from a
    verified castle, so a boss-adjacent camp can never satisfy it. Leaving him
    camped pins him indefinitely (live g1153 2026-11-07: ヴィーナス HP3/82,
    broken, camped at けっかい and never recalled). Recall him so the next
    monthly pass can repair him. An unread broken flag keeps the old exemption
    (never recall on unknown evidence).
    """
    from ... import hanjuku_chart as chart
    marker = info.get('marker')
    candidate = marker.get('castle_candidate') if isinstance(marker, dict) else None
    return (isinstance(candidate, str) and chart.is_boss_castle(candidate)
            and info.get('broken') is not True)


def _tasks(statuses, names, boss_camp_months=None):
    months = boss_camp_months or {}
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
            # boss fight). The exemption expires after BOSS_CAMP_EXEMPT_MONTHS
            # consecutive observed months (docich#3659): a siege that never
            # resolves must not pin him to the camp forever. A broken general
            # never holds the siege (docich#3659 follow-up: ヴィーナス).
            if (_boss_camp_holds_siege(info)
                    and int(months.get(name) or 0) <= BOSS_CAMP_EXEMPT_MONTHS):
                continue
            queue.append({'kind': 'recall', 'name': name})
        elif info.get('location') == 'castle':
            # A castle name measured from the roster triangle or the native
            # status panel is already known; only its ownership is pending.
            # Re-running the expensive SELECT->ステータス locate for it would
            # repeat the same failure every month (2026-11-07 g1139 ポワソン).
            if not info.get('castle') and not info.get('castle_pending'):
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
    # ``general_location_unknown`` is itself the request for the next
    # individual status confirmation (an expired march, or a general who lost
    # a battle). Schedule it from that list instead of trusting only the
    # cached field flags: a request recorded while a monthly pass was running
    # is dropped when that pass commits its snapshot, and a cached row that
    # still names an owned castle never turns dirty on its own — the general
    # was then never re-read (2026-11-07: a lost march stayed unavailable
    # while every month reused the cache).
    world = _world(mem)
    unknown = [name for name in (world.get('general_location_unknown') or ())
               if isinstance(name, str) and name]
    members = set(statuses) | set(pending)
    if cache is not None and unknown:
        # A verified roster is the membership proof: a name it does not hold
        # cannot be an employed general whose whereabouts are unknown.
        dropped = [name for name in unknown if name not in members]
        if dropped:
            world['general_location_unknown'] = [
                name for name in unknown if name in members]
            unknown = [name for name in unknown if name in members]
            _log(mem, 'post_month_unknown_location_pruned',
                 observed_metric={'generals': dropped, 'members': len(members)},
                 reason='確定済みの名簿にいない将軍は雇用中の所在不明ではないため再確認対象から外す')
    for name in unknown:
        if name in statuses:
            stale.add(name)
    scan = cache is None or bool(stale or pending or count_mismatch)
    # A castle recorded while its ownership was stale is now usable the moment
    # the Y map shows our flag; do not wait for another roster SELECT.
    promoted = _promote_pending(mem, statuses)
    # Count each general observed camping on the boss castle once per month.
    # ``arm`` can also be re-entered inside the same month after ``_finish``
    # dropped the owner state, so gate the increment on the counted scope —
    # never on the presence of a live owner.
    if mem.get('boss_camp_exempt_scope') != scope:
        mem['boss_camp_exempt_scope'] = list(scope)
        camping = {
            name for name, row in statuses.items()
            if row.get('location') == 'camp' and _boss_camp_holds_siege(row)}
        previous = mem.get('boss_camp_exempt') or {}
        counted = {name: int(previous.get(name) or 0) + 1 for name in camping}
        if counted:
            mem['boss_camp_exempt'] = counted
        else:
            mem.pop('boss_camp_exempt', None)
    if not scan and not _tasks(statuses, list(statuses), mem.get('boss_camp_exempt')):
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
        'tasks': [] if scan else _tasks(statuses, list(statuses),
                                        mem.get('boss_camp_exempt')),
        'steps': 0, 'phase_reads': 0, 'complete': not scan,
        'started_tick': int(mem.get('tick') or 0)}
    _log(mem, 'post_month_status_armed',
         observed_metric={'scope': scope, 'cached': len(statuses),
                          'reopen': sorted(stale), 'joins': pending,
                          'unknown_location': list(unknown),
                          'promoted': promoted,
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
    garrison = _world(mem).setdefault('garrison', {})
    for other, names in garrison.items():
        if other != castle and isinstance(names, list) and name in names:
            names.remove(name)
    occupants = garrison.setdefault(castle, [])
    if name not in occupants:
        occupants.append(name)
    row = (mem.get('post_month_status') or {}).get('statuses', {}).get(name)
    if isinstance(row, dict):
        row.update(castle=castle, castle_evidence=evidence)
    unknown = _world(mem).get('general_location_unknown')
    if isinstance(unknown, list) and name in unknown:
        unknown.remove(name)
    _log(mem, 'post_month_location_confirmed', general=name, castle=castle,
         reason='将軍本人の現在位置と城の実表示を対応させて在城リストを更新')
    return True


def _forget_field_residents(mem, name):
    for occupants in (_world(mem).get('garrison') or {}).values():
        if isinstance(occupants, list) and name in occupants:
            occupants.remove(name)


def _forget_pending(mem, name):
    pending = _world(mem).get('castle_location_pending')
    if isinstance(pending, dict):
        pending.pop(name, None)


def _note_pending_castle(mem, name, castle, *, evidence):
    """Keep a measured castle name until the ownership cache catches up.

    A general's own status can say しろのなかにいます while the roster triangle
    (or the castle status panel opened at his SELECT) names a castle the Y map
    still shows as enemy: the castle was taken moments ago and ``captured`` has
    not been refreshed yet (g1139 2026-11-07: ポワソン took ジュメルズ at 00:03,
    the roster bound him to it at 00:04, and the locate task spent three
    12-read waits because ``_assign`` kept rejecting the name; the Y map only
    recorded the capture at 00:09). The castle name is observed; only
    ownership is missing, so keep it here and never add him to a garrison of a
    castle we do not own yet. ``_promote_pending`` binds it once the Y map
    confirms the flag, without another SELECT round trip.
    """
    if not isinstance(name, str) or not isinstance(castle, str):
        return
    world = _world(mem)
    pending = world.get('castle_location_pending')
    if not isinstance(pending, dict):
        pending = world['castle_location_pending'] = {}
    previous = pending.get(name)
    pending[name] = {'castle': castle, 'chapter': mem.get('chapter'),
                     'tick': int(mem.get('tick') or 0), 'evidence': evidence}
    # He is verifiably in *a* castle, just not one we may place him in yet. An
    # older garrison entry at another castle is no longer his observed station.
    _forget_field_residents(mem, name)
    row = ((mem.get('post_month_status') or {}).get('statuses') or {}).get(name)
    if isinstance(row, dict):
        row.update(castle=castle, castle_evidence=evidence, castle_pending=True)
    cached = ((receipts._persistent_status(mem) or {}).get('statuses') or {}).get(name)
    if isinstance(cached, dict):
        # The full-scan snapshot was written before this locate task ran.
        cached.update(castle=castle, castle_evidence=evidence, castle_pending=True)
    if not isinstance(previous, dict) or previous.get('castle') != castle:
        _log(mem, 'post_month_castle_pending_ownership', general=name, castle=castle,
             observed_metric={'evidence': evidence, 'tick': int(mem.get('tick') or 0)},
             reason='城名は実画面で確認できたが所有一覧が未更新のため、所有確認まで在城一覧へ入れない')


def _promote_pending(mem, statuses):
    """Bind a remembered castle once the Y map proves it is ours."""
    pending = _world(mem).get('castle_location_pending')
    if not isinstance(pending, dict) or not pending:
        return 0
    from ..os_layer.world import _owned
    owned = _owned(mem)
    cache = receipts._persistent_status(mem)
    promoted = 0
    for name in list(pending):
        record = pending.get(name)
        if not isinstance(record, dict) or record.get('chapter') != mem.get('chapter'):
            pending.pop(name, None)
            continue
        castle = record.get('castle')
        if not isinstance(castle, str) or castle not in owned:
            continue
        if not _assign(mem, name, castle, evidence='pending_ownership_confirmed'):
            continue
        pending.pop(name, None)
        promoted += 1
        row = statuses.get(name) if isinstance(statuses, dict) else None
        if isinstance(row, dict):
            row.update(castle=castle, castle_evidence='pending_ownership_confirmed')
            row.pop('castle_pending', None)
        cached = ((cache or {}).get('statuses') or {}).get(name)
        if isinstance(cached, dict):
            cached.update(castle=castle, castle_evidence='pending_ownership_confirmed')
            cached.pop('castle_pending', None)
    return promoted


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
        _forget_pending(mem, name)
        for key in ('house_eggs', 'egg_types', 'egg_uses'):
            if isinstance(mem.get(key), dict):
                mem[key].pop(name, None)
        for key in ('egg_recheck', 'roster_pending_joins'):
            if isinstance(mem.get(key), list):
                mem[key] = [actor for actor in mem[key] if actor != name]
        # ``general_location_unknown`` lives in the world region; the flat
        # read here never matched it, so a departed general stayed queued for
        # a status confirmation he could never receive.
        unknown = _world(mem).get('general_location_unknown')
        if isinstance(unknown, list) and name in unknown:
            unknown.remove(name)
        _log(mem, 'post_month_general_departed', general=name,
             reason='将軍名簿を末尾まで実測したが本人がいないため旧状態を除籍')
    # A name that was never in the cached statuses cannot be purged by the
    # ``missing`` loop above, yet the completed walk is the same membership
    # proof: only names on this list are employed generals.
    world = _world(mem)
    unknown = world.get('general_location_unknown')
    if isinstance(unknown, list):
        known = set(state['names'])
        kept = [name for name in unknown if name in known]
        if len(kept) != len(unknown):
            world['general_location_unknown'] = kept
    state['statuses'] = {name: state['statuses'][name] for name in state['names']}
    state['tasks'] = _tasks(state['statuses'], state['names'],
                            mem.get('boss_camp_exempt'))
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
    failure = state.get('failure')
    mem['post_month_status_last'] = {
        'scope': list(state['scope']), 'complete': bool(state['complete']),
        'statuses': state['statuses'], 'failed': failure,
        'tick': int(mem.get('tick') or 0)}
    mem.pop('post_month_status', None)
    _log(mem, 'post_month_sequence_finished',
         observed_metric={'complete': bool(state['complete']), 'failed': failure,
                          'remaining': list(state.get('tasks') or [])},
         reason=('確認できなかった月次情報を城の所在地として扱わず、通常の出撃計画へ制御を返す'
                 if failure else
                 '月次の個別確認と状態別の処理を終えて通常攻略の計画へ制御を返す'))


def _task_complete(mem, state, *, result):
    job = state.get('job') or {}
    if job.get('kind') == 'locate' and result != 'castle_verified':
        name = job.get('name')
        if name and name not in _world(mem).setdefault('general_location_unknown', []):
            _world(mem)['general_location_unknown'].append(name)
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
         resulting_event=reason,
         observed_metric={'attempt': count + 1, 'screen': screen.kind,
                          'text': (screen.text or '')[:120]},
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
        for key in ('highlight', 'selected', 'marker_reads', 'markers', 'top_seeks',
                    'end_stall', 'end_stall_selected'):
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


# A battle can replace the general list/status panel with a map while this
# monthly owner still expects that now-closed panel. Re-enter the same owner
# rather than letting an exhausted [] handler or the sortie planner take over.
_BATTLE_SCAN_STAGES = frozenset({'roster', 'status', 'roster_next'})
_BATTLE_TASK_STAGES = frozenset({
    'task_menu', 'task_select', 'task_jump', 'locate_open', 'locate_info',
    'locate_reset',
})


def resume_after_battle(mem, battle):
    """Resume an interrupted monthly status operation on the next field map.

    Keep previously observed statuses and queued recovery tasks. A roster
    traversal must start at its verified top again: a partial list after
    combat cannot prove that an absent general died. A SELECT-based task must
    reopen the named general's menu, not act on the combat camera position.
    """
    state = mem.get('post_month_status')
    if not isinstance(state, dict) or (
            state.get('scope') != [mem.get('chapter'), mem.get('month')]):
        return
    stage = state.get('stage')
    actor = battle.get('ally') if isinstance(battle, dict) else None
    statuses = state.get('statuses') or {}
    if actor in statuses:
        # battle_end also dirties the persistent roster, but an already armed
        # monthly scan has its own snapshot of the dirty list.
        dirty = set(state.get('dirty') or ())
        dirty.add(actor)
        state['dirty'] = sorted(dirty)
    if stage in _BATTLE_SCAN_STAGES or (stage == 'blocked' and not state.get('job')):
        state['names'] = []
        state['complete'] = False
        for key in ('top_confirmed', 'top_seeks', 'list_moves', 'highlight',
                    'marker_reads', 'selected', 'markers', 'stray_backouts',
                    'end_stall', 'end_stall_selected',
                    'failure'):
            state.pop(key, None)
        state['steps'] = 0
        _stage(state, 'await_map')
        resumed = 'await_map'
    elif stage in _BATTLE_TASK_STAGES or (stage == 'blocked' and state.get('job')):
        # Keep the exact job and remaining queue. The battle invalidated the
        # old SELECT position, so opening the list again is mandatory.
        state['steps'] = 0
        state.pop('failure', None)
        _stage(state, 'task_menu', seeks=0)
        state['phase_reads'] = 2  # first map frame opens X immediately
        resumed = 'task_menu'
    elif stage == 'open_menu':
        # First map frame after the battle may open X without three idle reads.
        state['phase_reads'] = 2
        resumed = 'open_menu'
    else:
        # await_map/process/wait_repair/wait_recall own their existing
        # continuations; never steal a live repair/recall transaction.
        resumed = stage
    _log(mem, 'post_month_battle_resume',
         general=actor,
         observed_metric={'previous_stage': stage, 'resume_stage': resumed},
         reason='戦闘で中断した月次ステータス処理を優先し、出撃計画ではなく元の確認手順へ戻る')


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
    if state.get('stage') == 'blocked':
        # A failed verification must not hold the field forever. The old
        # state answered [] until a battle or a reanchor happened, so a scan
        # that gave up (observation_budget / status_unreadable / roster end)
        # left the map idle with no input for the rest of the month. Nothing
        # unverified was written to general_statuses, so releasing the screen
        # is safe: record the failure and return control to the field.
        _finish(mem, state)
        if screen.kind in STRAY_MENU_KINDS and not house.roster(screen):
            # Close the leftover menu once before the ordinary handlers run;
            # a map/text frame is released immediately instead.
            return [pad('b')]
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
    if state['stage'] in {'await_map', 'open_menu', 'task_menu'}:
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
    if stage == 'await_map':
        if screen.kind == 'map':
            _stage(state, 'open_menu')
            return [pad('x')]
        if screen.kind == 'main_menu' or house.roster(screen):
            # A battle can leave the global X/roster overlay open while the
            # monthly scan is being restarted. Close it as this owner's
            # recovery, rather than allowing deploy_step to claim the list.
            return [pad('b')]
        return None
    # These hops only relabel an already visible roster. Continue on the
    # same observation, with one budget increment and at most one pad. The
    # stable-highlight/marker gate in 'roster' still needs distinct frames.
    if stage == 'open_menu' and house.roster(screen):
        _stage(state, 'roster')
        stage = 'roster'
    if stage == 'task_menu' and house.roster(screen):
        _stage(state, 'task_select', seeks=0)
        stage = 'task_select'
    if stage == 'open_menu':
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
                state.pop('end_stall', None)
                state.pop('end_stall_selected', None)
                return [pad('b')]
            if (len(state['names']) >= receipts.VISIBLE_NAME_ROWS
                    and selected == names[-1]):
                # Full-page non-wrapping list (live 299x224): down on the last
                # row never moves and never shows scroll-end pixels. Count the
                # stall and let a passing membership proof finish the walk.
                if state.get('end_stall_selected') == selected:
                    stall = int(state.get('end_stall') or 0) + 1
                else:
                    stall = 1
                    state['end_stall_selected'] = selected
                state['end_stall'] = stall
                if stall >= END_STALL_READS and _complete_scan(mem, state):
                    state.pop('end_stall', None)
                    state.pop('end_stall_selected', None)
                    return [pad('b')]
            else:
                state.pop('end_stall', None)
                state.pop('end_stall_selected', None)
            if state['phase_reads'] > STATUS_RETRIES:
                state['failure'] = 'roster_end_unconfirmed'
                _log(mem, 'post_month_status_unverified',
                     observed_metric={'names': len(state['names']),
                                      'selected': selected,
                                      'top_confirmed': bool(state.get('top_confirmed')),
                                      'dirty': list(state.get('dirty') or ()),
                                      'phase_reads': state['phase_reads'],
                                      'end_stall': int(state.get('end_stall') or 0)},
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
            state.pop('end_stall', None)
            state.pop('end_stall_selected', None)
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
        state.pop('end_stall', None)
        state.pop('end_stall_selected', None)
        if name in state.get('dirty', []):
            state['dirty'].remove(name)
        if loc in ('camp', 'march'):
            _forget_field_residents(mem, name)
            _forget_pending(mem, name)
        elif loc == 'castle':
            # Existing named castle rosters are still valid when the observed
            # status says resident. Do not repeat a costly SELECT for them.
            old = [castle for castle, residents in (_world(mem).get('garrison') or {}).items()
                   if isinstance(residents, list) and name in residents]
            candidate = marker.get('castle_candidate') if isinstance(marker, dict) else None
            if candidate:
                if _assign(mem, name, candidate, evidence='roster_triangle'):
                    _forget_pending(mem, name)
                else:
                    # The triangle and the status agree on the castle; only the
                    # Y map still lists it as enemy. Keep the name, skip the
                    # repeated SELECT, and promote it when ownership lands.
                    _note_pending_castle(mem, name, candidate, evidence='roster_triangle')
            elif len(old) == 1:
                from ..os_layer.world import _owned
                if old[0] in _owned(mem):
                    row.update(castle=old[0], castle_evidence='previous_named_roster')
                    _forget_pending(mem, name)
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
        if castle is not None:
            if _assign(mem, name, castle, evidence='native_castle_name'):
                _task_complete(mem, state, result='castle_verified')
                return [pad('b')]
            # The panel named the castle. Waiting out STATUS_RETRIES here only
            # repeated the same three attempts every month (g1139 ポワソン
            # 2026-11-07): the missing fact was ownership, not the name, and the
            # Y map records it minutes later. Remember the name and finish.
            _note_pending_castle(mem, name, castle, evidence='native_castle_name')
            _task_complete(mem, state, result='castle_named_unowned')
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
