"""手順層: field map procedures (map, target selection, world map).

Drives the cursor toward the strategy layer's next order, confirms the
target under the sortie marker and reads world-map ownership surveys.
"""
from __future__ import annotations
from copy import deepcopy

from ..os_layer.screen import Screen, castle_roofs
from ... import hanjuku_chart as chart
from ... import hanjuku_chart_adjust as chart_adjust
from ... import hanjuku_map_sources as sources
from ..decision_log import _record, _tally
from ..version import BOT_VERSION
from .deploy import (_cancel_broken_hero_sortie, _deploy_context, _deploy_input, _hold_deploy,
                            _hold_guarded_sortie, _hold_locked_boss, _hold_unrouted_sortie)
from .recall import (MENU_HOLD_LIMIT, RECALL_LIMIT, _hold_after_picker, _recall_dispatch_step, _request_recall,
                            camp_recall_step)
from ..os_layer.commands import castle_list_batch, menu_to, pad
from ..os_layer.navigation import (Y_JUMP_LIMIT, Y_JUMP_OFFSET, _align_on_roof, _drop_stale_y_jump, _start_y_jump,
                                   _forget_failed_jump, _want_y_jump, _y_jump_step, nav_step)
from ..os_layer.readers import (UNDER_CURSOR_PX, WORLD_MAP_OFFSET, _cursor, _roof_under_cursor, season_sites,
                                world_cursor, world_flags, world_markers)
from ..os_layer.world import (NAME, _apply_season_sites, _apply_skull_boss, _apply_world_flags,
                              _hero_castle, _owned, update_world)
from ..strategy import goals
from ..strategy.kit import _deploy_cards, _is_boss_order
from ..strategy.execution import departure_snapshot
from ..state.regions import world as _world, flow, journal, flow as _flow, journal as _journal, sorties as _sorties, plan as _plan_region
from ..strategy.orders import (BOSS_TARGET_CANCEL_LIMIT, SOURCE_MISS_LIMIT, _broken_hero_order, _finish_order,
                               _garrison_move, _give_up_source, _near_goal, _order, _order_for_step, _ready,
                               _recall_goal, _retire_unentered_sortie, _sortie_general, _source,
                               next_order, parked_boss_steps)
from ..strategy.replan import _off_chart
from .result import FlowResult


def observe_sortie_transition(screen, mem, previous_kind=None):
    """An interrupted target cursor is a possible departure, never a retry.

    A hotloaded old policy has no attempt marker; its verified confirmation
    context and previous map_target observation provide the same evidence.
    """
    from .camp_recheck import observe_departure_return
    observe_departure_return(screen, mem)
    # A recall destination picker belongs to the recall, not to an active
    # chart sortie's interrupted target selection.
    if (_flow(mem).get('recall') or {}).get('stage') in ('dest', 'await_dispatch'):
        return
    order = _order(mem)
    attempt = _flow(mem).get('sortie_attempt')
    if not attempt and order and previous_kind == 'map_target':
        context = (_plan_region(mem).get('order_context') or {}).get(order['step']) or {}
        if context.get('actual_general'):
            attempt = {'step': order['step'], 'target_seen': True}
            _flow(mem)['sortie_attempt'] = attempt
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
            if (_plan_region(mem).get('orders') or {}).get(step) == 'launched_unconfirmed':
                _plan_region(mem)['active'] = step
                _plan_region(mem)['orders'][step] = 'pending'
                _sorties(mem).pop(step, None)
                # The interruption moved the camera: the resumed selection gets
                # fresh target jumps (g421 18:24: six were spent before a defense
                # battle, the marker came back unplaced and the bot idled).
                (mem.get('y_jumps') or {}).pop(f'{step}:target', None)
                _forget_failed_jump(mem, step, 'target')
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
    request = flow(mem).get('departure_request')
    if isinstance(request, dict) and request.get('step') == step:
        if (_departure_receipt_sent(mem, request) and isinstance(request.get('snapshot'), dict)
                and isinstance(request.get('context'), dict)
                and screen.kind in ('map', 'unknown')):
            if screen.kind == 'unknown' and int(request.get('departure_fades') or 0) < 3:
                # An ambiguous fade never proves departure; keep observing.
                request['departure_fades'] = int(request.get('departure_fades') or 0) + 1
                return
            flow(mem).pop('departure_request', None)
            # A journaled A plus the field map returning is the departure
            # observation: only now may the reservation become world state,
            # never at decision time (docich#3193 A).
            from .house import _sortie_state_sig, _tally_sortie_attempt
            # Issue #3326 (34): one attempt closes as 'departed' here.
            _tally_sortie_attempt(mem, request.get('target') or order['target'],
                (request.get('snapshot') or {}).get('purpose') or order.get('purpose'),
                'departed',
                _sortie_state_sig(mem, request.get('source'), request.get('general')))
            _commit_departure(mem, order, request['context'], request['snapshot'])
            attempt['interrupted'] = True
            mem.pop('expect_menu', None)
            return
        flow(mem).pop('departure_request', None)
        # The picker closed without a recorded send, or into a screen that
        # is not the field: fall through to the unconfirmed-departure
        # record. The observed garrison fact stays.
    context = (_plan_region(mem).get('order_context') or {}).get(step) or {}
    general = context.get('actual_general')
    if not general:
        return
    snapshot = departure_snapshot(order, mem, general, _source(order, mem))
    if snapshot is None:
        return  # a foreign confirmation never becomes departure ownership
    general = snapshot['general']
    _world(mem).setdefault('launched_orders', {})[step] = snapshot
    previous = _sorties(mem).get(step)
    if previous:
        _retire_unentered_sortie(mem, step, previous)
    _world(mem).setdefault('sorties', {})[step] = {
        'general': general, 'source': snapshot['source'], 'target': None,
        'planned_target': order['target'], 'status': 'launched_unconfirmed',
        'evidence': deepcopy({k: v for k, v in context.items() if k != 'departure_order'}),
        'tick': int(mem.get('tick') or 0),
        **({'confirmation_tick': snapshot['departure_binding']['confirm_tick']}
           if 'departure_binding' in snapshot else {})}
    from .house import _sortie_state_sig, _tally_sortie_attempt
    # Issue #3326 (34): no journaled send before the picker closed = a cancel.
    _tally_sortie_attempt(mem, order['target'], order.get('purpose'),
                          'cancelled_unsent',
                          _sortie_state_sig(mem, snapshot['source'], general))
    _finish_order(mem, 'launched_unconfirmed', general=general, target=None,
                  planned_target=order['target'], screen=screen.kind,
                  reason='目標決定前に出撃先選択を離れたため出撃の成否と行先を未確定として記録')
    attempt['interrupted'] = True
    mem.pop('expect_menu', None)
WORLD_SURVEY_TICKS = 200       # observations between surveys (~5 min at 1.5 s)
WORLD_MAP_WAIT = 4             # fade frames before giving up a reading
WORLD_MAP_CLOSE_LIMIT = 3      # Y presses before the view is known not to close with Y
PICKER_AXIS_SWITCH = 2         # unchanged recall-picker readings before pressing the other axis
PICKER_STILL_LIMIT = 8         # recall-picker presses with an unchanged cursor before giving up
PICKER_VISIT_SLOTS = 12        # distinct cursor positions remembered per recall
PICKER_LOOP_LIMIT = 3          # visits of one position before giving up
PICKER_LOOP_MIN_STEP = 8       # px a step must move before it counts as a visit
SELECT_FOCUS_INTERVAL = 300    # observations between hero re-focuses (~7.5 min)


def _world_map_wanted(mem) -> bool:
    if (mem.get('chapter') or 0) not in WORLD_MAP_OFFSET:
        return False
    last = mem.get('world_map_tick')
    tick = int(mem.get('tick') or 0)
    return (last is None or bool(mem.get('world_map_due'))
            or tick - int(last) >= WORLD_SURVEY_TICKS)


BOSS_STALL_ESCAPE_TICKS = 400   # one bounded back-out per ~10 min while parked
BOSS_STALL_ESCAPE_KINDS = frozenset({
    'month_menu', 'shop_list', 'shop_quantity_prompt', 'shop_quantity',
    'shop_exit_confirm', 'castle_menu', 'general_list', 'main_menu', 'castle_info'})


def boss_stall_escape_step(mem, kind, phase):
    """Back out of a menu when a boss march is parked on the field map.

    g1075 kept four site steps overdue with boss_relaunch=2 for 11-16
    hours. The sorties were correct; the bot never returned to the field map
    where next_order can re-arm them, because month/shop text kept owning
    the observation. This is a bounded escape, not a new order: one B per
    cooldown, only with no active order/battle and only on menu/text screens.
    """
    parked = parked_boss_steps(mem)
    if (not parked or _plan_region(mem).get('active') or mem.get('battle') or mem.get('attack')
            or _flow(mem).get('recall')):
        return None
    if kind == 'text':
        if phase not in ('shop', 'month'):
            return None
    elif kind not in BOSS_STALL_ESCAPE_KINDS:
        return None
    tick = int(mem.get('tick') or 0)
    last = mem.get('boss_stall_escape_tick')
    if type(last) is int and 0 <= tick - last < BOSS_STALL_ESCAPE_TICKS:
        return None
    mem['boss_stall_escape_tick'] = tick
    mem['world_map_due'] = True
    mem['uncertain'] = True
    _record(mem, 'boss_stall_escape',
            observed_metric={'steps': parked[:4], 'kind': kind, 'phase': phase},
            reason='ボス出撃が長期間入城しないまま駐車しているため、メニューを閉じて全体マップへ戻る')
    return [pad('b')]


BOSS_SORTIE_MENU_HOLD_TICKS = 60   # ~90 s at 1.5 s, safely inside the 300 s stall terminal
BOSS_SORTIE_MENU_KINDS = frozenset({'general_list', 'castle_menu', 'card_select', 'sortie_confirm'})


def boss_sortie_menu_escape_step(mem, kind, actions):
    """Cancel a boss sortie menu that produced no input for too long.

    g1075 2026-10-31 08:27-08:33: 4-B1 was active but every observation in
    general_list returned [] (situation_held), so the corner hit
    input_stalled after 300 s and the run ended. The menu owner has no
    unlimited hold: after BOSS_SORTIE_MENU_HOLD_TICKS with no action for the
    same step/screen, press B once and let the order be retried. Normal
    castle orders and screens with an action are untouched.
    """
    active = _plan_region(mem).get('active')
    if (not active or kind not in BOSS_SORTIE_MENU_KINDS or actions != []
            or mem.get('battle') or mem.get('attack') or _flow(mem).get('recall')):
        mem.pop('boss_sortie_menu_hold', None)
        return None
    order = _order_for_step(mem, active)
    if not _is_boss_order(order, mem):
        mem.pop('boss_sortie_menu_hold', None)
        return None
    tick = int(mem.get('tick') or 0)
    hold = mem.get('boss_sortie_menu_hold') or {}
    if hold.get('step') != active or hold.get('kind') != kind:
        mem['boss_sortie_menu_hold'] = {'step': active, 'kind': kind, 'tick': tick}
        return None
    since = hold.get('tick')
    if type(since) is not int or tick - since < BOSS_SORTIE_MENU_HOLD_TICKS:
        return None
    mem.pop('boss_sortie_menu_hold', None)
    mem['world_map_due'] = True
    mem['uncertain'] = True
    _record(mem, 'boss_sortie_menu_escape', chart_step=active, screen=kind,
            observed_metric={'held_ticks': tick - since},
            reason='ボス出撃メニューで有限時間入力が決まらないため、Bで取り消して出撃をやり直す')
    return [pad('b')]


def _picker_origin(chapter, frame):
    """The recalling unit's chart cell, only when the picker shows exactly one ▲.

    DSH 2026-10-28: the picker cursor starts on home, so only a unique army
    marker is bound. Several markers are ambiguous and leave origin unknown.
    """
    offset = WORLD_MAP_OFFSET.get(chapter)
    markers = world_markers(frame) if frame is not None and offset else []
    if len(markers) != 1:
        return None
    x0, y0, x1, y1 = markers[0]
    return [round(((x0 + x1) / 2 - offset[0]) * 8), round(((y0 + y1) / 2 - offset[1]) * 8)]


def world_map_step(screen, mem, frame):
    """Read every castle's owner from the Y view, then close it with Y
    (or, while a Y jump is running, steer its cursor to the goal castle)."""
    if mem.get('y_jump'):
        order = _order(mem)
        if order is not None and mem['y_jump'].get('step') == order['step']:
            if ('_run_identity' in mem
                    and (_measured_goal(mem, order, _source(order, mem)) is None
                         or _measured_goal(mem, order, order['target']) is None)):
                mem.pop('y_jump', None)
                return []
            routed = _hold_unrouted_sortie(mem, order)
            if routed is not None:
                return routed
        return _y_jump_step(mem, frame)
    recall = _flow(mem).get('recall')
    if recall and recall.get('stage') == 'await_dispatch':
        return _recall_dispatch_step(screen, mem, recall)
    if recall and recall.get('boss') and recall.get('stage') == 'dest':
        # いどう opened a whole-island picker, whose confirmation below sends
        # the unit to an own castle. A boss redirect must never do that: close
        # it and keep the camp where it is. Unmeasured: the event names the screen.
        from .recall import _abort_boss_redirect
        return _abort_boss_redirect(mem, recall, 'boss_picker_is_world_map',
                                    'いどうが全島ピッカーを開いたため、自軍城へ帰還させず取り消す')
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
        if recall['picker_observations'] >= RECALL_LIMIT:
            if not recall.get('picker_forced') and not _picker_on_enemy(nearby, flags):
                return _picker_fallback(mem, recall, nearby, cursor,
                                        '帰還先カーソルが有限回で合わないため、今いる城で確定する')
            _record(mem, 'camp_recall_aborted',
                    observed_metric={'goal': recall.get('goal'), 'cursor': cursor},
                    reason='帰還先カーソルが有限回で合わず確定せず取り消す')
            _flow(mem).pop('recall', None)
            mem['uncertain'] = True
            _hold_after_picker(mem, recall)
            return [pad('b')]
        if recall.get('origin') is None:
            picked = _picker_origin(chapter, frame)
            if picked is not None:
                recall.update(origin=picked, origin_source='picker')
        goal = _recall_goal(mem, flags)
        if not cursor or not offset or goal is None:
            return [pad('right')] if cursor else []
        if recall.get('goal') != goal or not recall.get('destination_selected'):
            recall.update(goal=goal, destination_selected=True)
            origin = recall.get('origin')
            _record(mem, 'camp_recall_destination',
                    observed_metric={'castle': goal, 'origin': origin,
                                     'origin_source': recall.get('origin_source', 'fallback') if origin else 'fallback',
                                     'picker_boxes': [list(b) for b in world_markers(frame)],
                                     'hero_castle': _hero_castle(mem)},
                    reason='現在地から近い自軍城を選び、同距離ならどうしの在城先を優先する')
        if nearby != [goal]:
            gx, gy = chart.castles(chapter)[goal]
            dx, dy = gx / 8 + offset[0] - cursor[0], gy / 8 + offset[1] - cursor[1]
            across = 'right' if dx > 0 else 'left'
            along = 'down' if dy > 0 else 'up'
            press, other = (across, along) if abs(dx) >= abs(dy) else (along, across)
            # g1075 15:10:55-15:14:46: the ring stayed on アルマムーン through
            # 54 left presses (only the enemy ボス lies to its left), and one
            # down press moved it to グリン. Record every step, so its frame is
            # kept, try the other axis when the reading does not move, and stop
            # once it has not changed for a while.
            prev = recall.get('picker_cursor')
            still = (int(recall.get('picker_still') or 0) + 1
                     if prev == list(cursor) else 0)
            recall.update(picker_cursor=list(cursor), picker_still=still)
            # g1133 02:13-02:43: the ring moves between バビラリー and オケアナ
            # with two unchanged readings at each stop, so a sliding window of
            # changed positions never accumulated (the unchanged steps cleared
            # it) and the loop budget never fired. Count how often each distant
            # position is visited instead; local jitter below
            # PICKER_LOOP_MIN_STEP is not a visit.
            here = [round(cursor[0], 1), round(cursor[1], 1)]
            moved = (abs(here[0] - prev[0]) + abs(here[1] - prev[1])) if prev else None
            visits = dict(recall.get('picker_visits') or {})
            loops = int(recall.get('picker_loops') or 0)
            if moved is None or moved >= PICKER_LOOP_MIN_STEP:
                key = f'{here[0]},{here[1]}'
                visits[key] = int(visits.get(key) or 0) + 1
                loops = max(int(v) for v in visits.values())
                if len(visits) > PICKER_VISIT_SLOTS:
                    visits = dict(sorted(visits.items(), key=lambda kv: kv[1])[-PICKER_VISIT_SLOTS:])
            recall.update(picker_visits=visits, picker_loops=loops)
            if still >= PICKER_AXIS_SWITCH:
                if min(abs(dx), abs(dy)) >= 3:
                    press = other
                else:
                    # The other offset is small, so the old guard never tried
                    # it: g1133 01:06-01:33 バビラリー→ダーラント (dx 38,
                    # dy -1.5) pressed right 8 times without moving and fell
                    # back to the wrong castle. Alternate the two axes so a
                    # blocked one is not held forever.
                    press = other if (still // PICKER_AXIS_SWITCH) % 2 else press
            _record(mem, 'camp_recall_picker_step',
                    observed_metric={'cursor': list(cursor), 'goal': goal,
                                     'offset': [round(dx, 1), round(dy, 1)], 'press': press,
                                     'still': still, 'loops': loops},
                    reason='帰還先の城へ全体マップのカーソルを1歩動かす')
            if (still >= PICKER_STILL_LIMIT or loops >= PICKER_LOOP_LIMIT) and _picker_on_enemy(nearby, flags):
                _record(mem, 'camp_recall_aborted', resulting_event='picker_cursor_stationary',
                        observed_metric={'goal': goal, 'cursor': list(cursor), 'still': still},
                        reason='帰還先カーソルが敵城の上で動かないため、敵城へは確定せず取り消す')
                _flow(mem).pop('recall', None)
                mem['uncertain'] = True
                _flow(mem)['recall_skip'] = {'tick': int(mem.get('tick') or 0)}
                return [pad('b')]
            if still >= PICKER_STILL_LIMIT or loops >= PICKER_LOOP_LIMIT:
                # Owner 2026-11-05: 最悪キャンセルにせず、どこでも良いから指定する。
                # 動かないカーソルを取り消すと、撤退した将軍が野営のまま帰れない
                # (g1133 00:14:49, ダーラントへ向かうカーソルが止まり取消)。
                return _picker_fallback(mem, recall, nearby, cursor,
                                        '帰還先カーソルが入力しても動かないため、取り消さず今いる城で確定する')
            return [pad(press)]
        return _request_recall(mem, recall, goal,
                               [pad('a'), {'type': 'wait', 'ms': 700}, pad('a')])
    flags = world_flags(frame, mem.get('chapter') or 0)
    if not flags:
        waited = int(mem.get('world_map_wait') or 0) + 1
        mem['world_map_wait'] = waited
        return [] if waited < WORLD_MAP_WAIT else _close_world_map(mem)
    mem.pop('world_map_wait', None)
    _apply_world_flags(mem, flags)
    # The same view draws chapter 4's 四季王 sites: a cleared one keeps a
    # pennant, a live king keeps the skull.
    if (mem.get('chapter') or 0) == 4 and hasattr(frame, 'pixel'):
        _apply_season_sites(mem, season_sites(frame, 4, world_cursor(frame)))
    if (mem.get('chapter') or 0) != 4 and hasattr(frame, 'pixel'):
        # A single boss skull on the Y view is the boss's measured cell (the
        # moving boss included; chapter 4's four sites use the scan above).
        _apply_skull_boss(mem, frame)
    markers = world_markers(frame)
    _probe_launch_markers(mem, markers)
    mem['_world_units_prev'] = [list(b) for b in markers]
    if markers:
        _record(mem, 'world_map_units', observed_metric={'count': len(markers),
                'boxes': [list(m) for m in markers]},
                reason='全体マップの▲（部隊マーカー）を定期確認')
    return _close_world_map(mem)


def _picker_on_enemy(nearby, flags):
    """The cursor stands on a castle the Y view flags as the enemy's: never confirm it."""
    return bool(nearby) and (flags or {}).get(nearby[0]) == 'enemy'


def _picker_fallback(mem, recall, nearby, cursor, reason):
    """Confirm the recall where the picker cursor stands instead of cancelling.

    The picker's ring starts on the home castle and snaps from castle to castle;
    a goal it cannot reach (the reading stops moving, the observation budget is
    spent) used to cancel the recall and leave the general camping. A castle the
    cursor stands on is a destination too, so A confirms it; with no castle under
    the cursor A is still sent once. ``_recall_dispatch_step`` then bounds what
    follows, and a refused A is closed by its own limits.
    """
    castle = nearby[0] if nearby else None
    recall['picker_forced'] = True
    _record(mem, 'camp_recall_picker_fallback',
            observed_metric={'goal': recall.get('goal'), 'castle': castle,
                             'cursor': list(cursor) if cursor else None,
                             'still': recall.get('picker_still'),
                             'observations': recall.get('picker_observations')},
            reason=reason)
    return _request_recall(mem, recall, castle or recall.get('goal'),
                           [pad('a'), {'type': 'wait', 'ms': 700}, pad('a')])


def _close_world_map(mem):
    """Y closes the owner survey, but not a destination picker left open.

    A battle can take the screen while きかん's picker is up: the recall state
    ends and the picker comes back afterwards. g1097 07:36:47 pressed Y 33 times
    on it, until a monthly scene took the screen.
    """
    tick = int(mem.get('tick') or 0)
    tries = mem.get('world_map_close') or {}
    count = int(tries.get('count') or 0) + 1 if 0 <= tick - int(tries.get('tick') or 0) <= 2 else 1
    if count > WORLD_MAP_CLOSE_LIMIT:
        mem.pop('world_map_close', None)
        _record(mem, 'world_map_picker_escape', observed_metric={'presses': count - 1},
                reason='Yで閉じない全体マップは取り残された帰還先の選択なのでBで閉じる')
        return [pad('b')]
    mem['world_map_close'] = {'tick': tick, 'count': count}
    return [pad('y')]


OFF_CASTLE_LIMIT = 3           # refusals before pressing A anyway (bounded)
APPROX_LOCALIZE_LIMIT = 4      # observations without a roof anchor before native survey


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


def _measured_goal(mem, order, castle):
    """Last coordinate boundary, including already-issued reconciliation."""
    goal = chart.castles(mem.get('chapter') or 0).get(castle)
    if goal is None:
        _plan_region(mem)['active'] = None
        mem['picked'] = []
        mem['_map_registration_hold'] = True
        _record(mem, 'map_coordinates_unavailable', chart_step=order['step'], castle=castle,
                reason='現在のランで有効な座標がないため入力せず、既存注文の実行を停止')
    return goal


def _abandon_stuck_step(screen, mem, order):
    """Give up an order whose map navigation keeps needing SELECT resets.

    g1075 2026-10-29 (chapter 3): the cursor sat on open sea at the screen's
    west edge; every ~6 s a SELECT re-placed the estimate and the same hold
    walked it back, for 266 ``nav_stuck`` bursts and 95 minutes without a
    sortie. ``navigation.NAV_RECOVER_LIMIT`` bounds that recovery per step, so
    fail the step and let the picker choose another candidate instead.
    """
    step = order['step']
    mem['uncertain'] = True
    for key in ('anchor', 'nav_last', 'nav_prev', 'nav_still', 'unverified',
                'nav_search', 'nav_search_leg', 'select_used', 'near_goal',
                'menu_miss', 'menu_hold', 'off_castle'):
        mem.pop(key, None)
    observed = {'cursor': mem.get('cursor'),
                'screen_cursor': list(_cursor(screen) or ()),
                'roofs': mem.get('roofs_seen'),
                'recoveries': (mem.get('nav_recovers') or {}).get(step)}
    _finish_order(mem, 'failed', source=_source(order, mem), target=order['target'],
                  observed_metric=observed,
                  reason='画面端で位置を再特定できずSELECT復帰を上限まで繰り返したため、この移動orderを打ち切って次の候補へ進む')
    _record(mem, 'nav_infeasible', chart_step=step, screen=screen.kind,
            source=_source(order, mem), target=order['target'],
            observed_metric=observed,
            reason='画面端で位置を再特定できずSELECT復帰を上限まで繰り返したため、この移動orderを打ち切って次の候補へ進む')
    return []


ORDER_START_BACKOFF = 12      # observations (~18 s) before the same order restarts


def _order_start_due(mem, step):
    """The same order is not restarted on every observation.

    A start that an interruption (defence, event) cut short used to restart at
    once: g1133 2026-11-05 started the same move 10 times in five minutes, each
    attempt opening the world map and aborting. The backoff keeps the order
    pending without paying that cost again.
    """
    now = int(mem.get('tick') or 0)
    started = mem.setdefault('order_start_tick', {})
    last = started.get(step)
    if last is not None and now - int(last) < ORDER_START_BACKOFF:
        return False
    started[step] = now
    for stale in list(started)[:-40]:      # del on the temporary list is a no-op
        started.pop(stale, None)
    return True


def _drop_jump_state(mem, step):
    """Forget the Y-jump bookkeeping of one step.

    g1133 2026-11-05 opened the Y view for the abandoned step
    ``A:bf8c3dee:M97e81e`` 72 times in 2.5 minutes (always attempt 1) while the
    active order was another step: the per-step jump memory outlived the plan.
    """
    mem['y_jumps'] = {k: v for k, v in (mem.get('y_jumps') or {}).items()
                      if not k.startswith(f'{step}:')}
    _forget_failed_jump(mem, step)
    (mem.get('y_jumped') or {}).pop(step, None)
    last = mem.get('y_jump_last') or {}
    if last.get('step') == step:
        mem.pop('y_jump_last', None)
    jump = mem.get('y_jump') or {}
    if jump.get('step') == step:
        mem.pop('y_jump', None)


def _release_stale_active(mem):
    """Drop an active order that no plan or launch record owns.

    g1133 2026-11-05: the bot kept opening the Y view for ``A:bf8c3dee:M97e81e``
    (588 records, always attempt 1) long after the plan had replaced it, so the
    step consumed map observations while nothing could start it. An order that
    is neither in the plan nor in the launched-order records and never got a
    status was abandoned by the plan; releasing it lets ``next_order`` pick the
    current plan.
    """
    step = _plan_region(mem).get('active')
    if not step:
        return False
    plan = mem.get('chart_plan') or {}
    plan_steps = {o.get('step') for o in plan.get('orders') or ()}
    launched = (mem.get('world') or {}).get('launched_orders') or mem.get('launched_orders') or {}
    status = (_plan_region(mem).get('orders') or {}).get(step)
    if not plan_steps or step in plan_steps or step in launched or status is not None:
        return False
    _plan_region(mem)['active'] = None
    mem['picked'] = []
    _drop_jump_state(mem, step)
    _record(mem, 'stale_active_released', chart_step=step,
            observed_metric={'plan_steps': sorted(plan_steps)[:8]},
            reason='計画にも出撃記録にも無い古い指示が残っていたため解放し、現在の計画から選び直す')
    return True


def _home_can_send(mem):
    """The fallback source (the home castle) has at least one general.

    g1133 2026-11-05 had `home_garrison: []` while `source_miss` stayed at 5
    for an hour: every observation gave the source up to a castle that could
    never send anyone, so the order never even walked to the chart's source
    (issue #3417).
    """
    home = chart.home_castle(mem.get('chapter') or 0)
    return bool((_world(mem).get('garrison') or {}).get(home))


def _apply_select_centered(mem):
    """Use a just-sent SELECT as a known position.

    SELECT centres the camera on the hero, so the cursor stands on the hero's
    castle right after it: a position that needs no roof anchor (g1133
    2026-11-05 sat `uncertain` with `anchor` None for hours).
    """
    if mem.pop('select_centered', None) is None or mem.get('y_jump'):
        return False
    hero = _hero_castle(mem)
    cell = chart.castles(mem.get('chapter') or 0).get(hero) if hero else None
    if not cell:
        return False
    mem['cursor'] = list(cell)
    mem['uncertain'] = False
    mem['anchor'] = hero
    _record(mem, 'select_centered', observed_metric={'castle': hero, 'cell': list(cell)},
            reason='SELECTで主人公の城へフォーカスしたので現在地を確定する')
    return True


# Identical estimates before the inland search starts. Map observations are
# rare while the chapter throws battles (g1133 2026-11-05: two map frames in
# 8.4 minutes), so 40 would never be reached in a useful time.
CURSOR_STILL_LIMIT = 6


def _watch_cursor_still(mem):
    """Mark a frozen position estimate for recovery via the own-castle list.

    Unlike the former inland roof search, X -> しろ -> アルマムーン ->
    SELECT provides a named, deterministic cursor cell even with no visible
    roofs. The legacy nav_search path remains a bounded fallback if the menu
    jump cannot be completed.
    """
    # With no order the map deliberately waits for armies/events. A missing
    # roof anchor during that wait is not evidence of failed navigation.
    # Clear old counts as well, including those left by a hotloaded policy.
    if not _plan_region(mem).get('active'):
        mem.pop('cursor_still', None)
        return False
    cursor = mem.get('cursor')
    watch = mem.get('cursor_still') or {}
    if not cursor:
        mem.pop('cursor_still', None)
        return False
    if watch.get('cell') != list(cursor):
        mem['cursor_still'] = {'cell': list(cursor), 'n': 1}
        return False
    watch['n'] = int(watch.get('n') or 0) + 1
    mem['cursor_still'] = watch
    if (watch['n'] < CURSOR_STILL_LIMIT or mem.get('nav_search')
            or mem.get('castle_jump') or (mem.get('anchor') and not mem.get('uncertain'))):
        return False
    mem['nav_search'] = True
    mem.pop('nav_search_leg', None)
    mem.pop('anchor', None)
    _record(mem, 'cursor_still_search', observed_metric={'cursor': list(cursor), 'still': watch['n']},
            reason='現在地の推定が動かないため、Xメニューの「しろ」からアルマムーンへ飛び座標を再設定する')
    return True


# Owner instruction (2026-11-06): reach the sortie's source castle through the
# X menu's しろ list and SELECT, never through the Y world map. The list is the
# same right-hand panel layout as the general roster: names at x=168 on 16 px
# rows from y=39, with the orange hand left of the selected row. SELECT flies
# the cursor to that castle (manual p.15: 将軍と同じく、セレクトボタンでその城へ飛べる).
CASTLE_JUMP_HINT = 'Aボタンでステータスひょうじ'
CASTLE_JUMP_LIMIT = 48          # observations across the whole menu trip
# The しろ list is an eight-row window (y=39..151). In the observed failures
# it opened on the home castle at row 0, so reaching the last row of a
# 12-castle chapter needs 11 Downs. The old bound of 8 stopped at row 8 and
# left every source from ユージア onward (chapter 6 indices 9+) unreachable:
# g1133 recorded five ユージア castle_missing failures on 2026-11-06 and
# g1163 seven アブダレ failures on 2026-11-09, each with the eight-scroll
# bound exhausted. Keep the bound above the longest chapter roster; the loop
# still stops as soon as the source is visible.
CASTLE_JUMP_SCROLL_LIMIT = 16   # list scrolls while looking for the source
CASTLE_JUMP_COOLDOWN = 40       # observations before retrying a failed source
CASTLE_JUMP_FAR = 48            # same distance gate as the Y jump (map px)
# The orange hand of the main menu and of the しろ list blinks, so whole frames
# are captured with no cursor sprite drawn. g1139 2026-11-07 lost five jumps
# because the first main-menu observation fell in the hidden phase and
# `shiro_missing` aborted the trip before any input. Wait for a frame that
# shows the hand instead: both panels are static, so holding without input
# cannot change what they display.
CASTLE_JUMP_CURSOR_BLIND = 4    # consecutive cursor-less frames before giving up
# The X press fades the map into the menu, and the frame in between reads as
# nothing at all (g1139 2026-11-07 00:24:33 and 23:58:03: one `unknown`
# observation, then a readable main menu). Re-observe briefly; a real
# mis-pressed panel still ends the trip on the same bound.
CASTLE_JUMP_FADE_BLIND = 2      # consecutive unreadable frames after the X
CASTLE_JUMP_ORPHAN_HOLD = 4     # frames an empty active order may interrupt the trip
HERO_SELECT_SETTLE_AGE = 2      # observations after a field SELECT before a map frame counts
HERO_SELECT_WAIT_LIMIT = 6      # observations without the field return before the しろ list


def _castle_key(name):
    """Compare chart and on-screen castle names without the long-vowel mark."""
    return (name or '').replace('ー', '').replace(' ', '')


def _castle_list_rows(screen):
    """Rows of the own-castle list, or None when this is another panel."""
    text = screen.text or ''
    if not screen.hand or CASTLE_JUMP_HINT not in text or 'コマンドにもどります' not in text:
        return None
    rows = []
    for line in screen.lines:
        if 39 <= line.y <= 151 and (line.y - 39) % 16 == 0:
            word = dict(line.spans()).get(168)
            if word:
                rows.append((line.y, word))
    return rows or None


def _castle_list_target(screen, source):
    """The visible list row naming source (chart spelling may differ)."""
    rows = _castle_list_rows(screen)
    if not rows:
        return None
    key = _castle_key(source)
    exact = [word for _, word in rows if _castle_key(word) == key]
    if exact:
        return exact[0]
    for _, word in rows:
        candidate = _castle_key(word)
        if candidate and (key.startswith(candidate) or candidate.startswith(key)):
            return word
    return None


def _start_reanchor_jump(mem, *, owner=None):
    """Recover a lost cursor from a named castle, without a roof observation.

    The SELECT return from the own-castle list is a stronger reference than
    a guessed camera offset. Reuse the sortie castle-jump state machine, but
    keep this recovery independent of any active order or available garrison.
    """
    home = 'アルマムーン'
    chapter = mem.get('chapter') or 0
    if (not mem.get('nav_search') or home not in chart.castles(chapter)
            or mem.get('castle_jump') or mem.get('y_jump') or mem.get('expect_menu')):
        return None
    skip = mem.get('castle_jump_skip') or {}
    tick = int(mem.get('tick') or 0)
    if (skip.get('mode') == 'reanchor'
            and tick - int(skip.get('tick') or 0) < CASTLE_JUMP_COOLDOWN):
        return None
    # The navigator repairs coordinates without taking ownership of the work.
    # Save the foreground flow and stage before SELECT moves the camera home.
    if owner is None:
        owner = 'sortie_plan' if _plan_region(mem).get('active') else 'map_navigation'
    monthly = mem.get('post_month_status') if owner == 'post_month_status' else None
    return_to = {'owner': owner, 'chapter': chapter, 'month': mem.get('month'),
                 'active_step': _plan_region(mem).get('active'),
                 'stage': monthly.get('stage') if isinstance(monthly, dict) else None,
                 'scope': list(monthly.get('scope') or []) if isinstance(monthly, dict) else None}
    mem['castle_jump'] = {'mode': 'reanchor', 'step': None, 'source': home,
                          'phase': 'menu', 'age': 0, 'total': 0, 'tick': tick,
                          'menu_presses': 1, 'selects': 0, 'scrolls': 0, 'waits': 0,
                          'return_to': return_to}
    mem.pop('select_centered', None)
    _record(mem, 'cursor_reanchor_open', target=home,
            observed_metric={'cursor_before': mem.get('cursor'), 'chapter': chapter,
                             'return_to': return_to},
            reason='中断元を保存しX→しろ→アルマムーン→SELECTでカーソル基準点を復旧する')
    return [pad('x')]


def _want_castle_jump(mem, order, source, goal):
    chapter = mem.get('chapter') or 0
    if chapter not in Y_JUMP_OFFSET:
        return False
    if chapter not in sources.SOURCE_CASTLES or source not in sources.SOURCE_CASTLES[chapter]:
        return False
    if _plan_region(mem).get('active') != order['step']:
        return False
    if mem.get('y_jump') or mem.get('castle_jump') or _source(order, mem) != source:
        return False
    skip = mem.get('castle_jump_skip') or {}
    tick = int(mem.get('tick') or 0)
    if (skip.get('step') == order['step']
            and tick - int(skip.get('tick') or 0) < CASTLE_JUMP_COOLDOWN):
        return False
    world = mem.get('cursor')
    if world and abs(world[0] - goal[0]) + abs(world[1] - goal[1]) <= CASTLE_JUMP_FAR:
        return False
    return True


def _hero_select_jump_allowed(mem, order, source):
    """SELECT alone reaches the source when the hero is verified inside it.

    SELECT centres the field cursor on the hero (owner rule 2026-09-28,
    _apply_select_centered), so a source castle whose read general list holds
    the hero needs no X -> しろ -> row walk. g1163 2026-11-10 23:20: every
    sortie from あるまむーん spent X, three Downs, A and SELECT (about 11 s)
    to reach the castle the hero already stood in. The deploy layer still
    reads the opened castle's status name before しゅつげき, so a stale
    garrison reading cannot send from the wrong castle; any mismatch on this
    step falls back to the named list.
    """
    if _hero_castle(mem) != source:
        return False
    if order['step'] in (mem.get('hero_select_miss') or {}):
        return False
    return not (mem.get('source_miss') or {}).get(order['step'])


HERO_SELECT_MISS_KEEP = 16       # recent steps whose field SELECT failed


def _mark_hero_select_miss(mem, step):
    """Remember a step whose field SELECT failed; bounded, newest kept."""
    misses = mem.get('hero_select_miss') if isinstance(mem.get('hero_select_miss'), dict) else {}
    misses[step] = int(mem.get('tick') or 0)
    mem['hero_select_miss'] = dict(sorted(misses.items(), key=lambda kv: kv[1])[-HERO_SELECT_MISS_KEEP:])


def _start_castle_jump(mem, order, source):
    if _hero_select_jump_allowed(mem, order, source):
        mem['castle_jump'] = {'step': order['step'], 'source': source,
                              'target': order.get('target'), 'phase': 'confirm',
                              'mode': 'hero_select', 'age': 0, 'total': 0,
                              'tick': int(mem.get('tick') or 0),
                              'menu_presses': 0, 'selects': 1, 'scrolls': 0, 'waits': 0}
        _record(mem, 'castle_jump_open', chart_step=order['step'], target=source,
                strategy_variant='hero_select',
                observed_metric={'cursor': mem.get('cursor'), 'source': source,
                                 'hero_castle': source},
                reason='出撃元は将軍一覧で主人公が居ると確認した城なので、Xメニューを経由せずSELECTで主人公の城へ飛ぶ')
        return [pad('select')]
    mem['castle_jump'] = {'step': order['step'], 'source': source,
                          'target': order.get('target'), 'phase': 'menu',
                          'age': 0, 'total': 0, 'tick': int(mem.get('tick') or 0),
                          'menu_presses': 1, 'selects': 0, 'scrolls': 0, 'waits': 0}
    _record(mem, 'castle_jump_open', chart_step=order['step'], target=source,
            observed_metric={'cursor': mem.get('cursor'), 'source': source},
            reason='出撃元の城へXメニューの「しろ」からSELECTで飛ぶ')
    return [pad('x')]


def _end_castle_jump(screen, mem, why):
    jump = mem.pop('castle_jump', None)
    if not isinstance(jump, dict):
        return []
    mode = jump.get('mode')
    mem['castle_jump_skip'] = {'mode': mode, 'step': jump.get('step'),
                               'tick': int(mem.get('tick') or 0)}
    metric = {'why': why, 'age': jump.get('age'), 'phase': jump.get('phase')}
    if mode == 'reanchor':
        _record(mem, 'cursor_reanchor_failed', target=jump.get('source'),
                screen=screen.kind, observed_metric=metric,
                reason='アルマムーンへのSELECT復旧が成立しないため、再試行を制限して従来の探索へ戻る')
    else:
        _record(mem, 'castle_jump_failed', chart_step=jump.get('step'), target=jump.get('source'),
                screen=screen.kind, observed_metric=metric,
                reason='Xメニューの「しろ」から出撃元へ飛べないため、通常の移動に戻る')
    if screen.kind == 'main_menu' or _castle_list_rows(screen):
        return [pad('b')]
    return []


def _jump_successor(mem, order, jump=None):
    """The live order that continues a re-planned jump, or None.

    Same departure castle, same target and the same general (or a substitute
    the plan itself picked) mean the walk to the castle is still the right
    physical action; only the step id changed.
    """
    active = _order_for_step(mem, _plan_region(mem).get('active'))
    if not isinstance(active, dict):
        return None
    if isinstance(order, dict):
        if active.get('step') == (order.get('step') or ''):
            return None
        target, source = order.get('target'), _source(order, mem)
    else:
        # The old step disappeared with the replaced plan; the jump itself
        # still remembers where it was going (g1163 2026-11-10 06:22: the
        # jump died at age 1 every time a re-plan dropped its step).
        jump = jump if isinstance(jump, dict) else {}
        target, source = jump.get('target'), jump.get('source')
        if not target:
            return None
    if (target or None) != (active.get('target') or None):
        return None
    if source != _source(active, mem):
        return None
    # The general may change with the re-plan (the chart worker re-picks the
    # actor each time: シュゼット's jump was replaced by マンゴスチン's at
    # 05:38). The castle trip is still the same physical action, and a
    # different general may depart from there (owner 2026-11-10 fallback).
    return active


def castle_jump_step(screen, mem, frame):
    """Drive X -> しろ -> castle name -> SELECT for the order's source.

    Runs before the ordinary screen dispatch while mem['castle_jump'] is
    live, so the main menu and the castle list cannot be mistaken for a
    generic panel. Returns None when no jump is in flight.
    """
    jump = mem.get('castle_jump')
    if not isinstance(jump, dict):
        return None
    jump['total'] = int(jump.get('total') or 0) + 1
    jump['age'] = int(jump.get('age') or 0) + 1
    if jump['total'] > CASTLE_JUMP_LIMIT:
        return _end_castle_jump(screen, mem, 'limit')
    reanchor = jump.get('mode') == 'reanchor'
    if not reanchor:
        order = _order_for_step(mem, jump['step'])
        if order is None or _plan_region(mem).get('active') != jump['step']:
            return _end_castle_jump(screen, mem, 'order_changed')

    phase = jump.get('phase')
    if phase == 'menu':
        if screen.kind == 'map':
            # Give the first X a few observations to draw the menu; a second
            # X too early would close the menu it just opened.
            if jump['age'] >= 3 and int(jump.get('menu_presses') or 0) < 2:
                jump['menu_presses'] = int(jump.get('menu_presses') or 0) + 1
                return [pad('x')]
            if jump['age'] < 4:
                return []
            return _end_castle_jump(screen, mem, 'menu_not_open')
        if screen.kind == 'unknown':
            # The map-to-menu fade has no readable text; hold for it instead
            # of treating the in-between frame as a wrong panel.
            jump['fade'] = int(jump.get('fade') or 0) + 1
            if jump['fade'] <= CASTLE_JUMP_FADE_BLIND:
                return []
            return _end_castle_jump(screen, mem, 'unexpected_menu')
        if screen.kind == 'main_menu':
            jump['fade'] = 0
            if screen.hand is None:
                # A blind frame says nothing about which row is selected: the
                # hand blinks, so it can be missing while the menu is fully
                # drawn. Hold and re-observe instead of cancelling the jump.
                jump['blind'] = int(jump.get('blind') or 0) + 1
                if jump['blind'] <= CASTLE_JUMP_CURSOR_BLIND:
                    return []
                return _end_castle_jump(screen, mem, 'hand_hidden')
            jump['blind'] = 0
            move = menu_to(screen, 'しろ')
            if move == 'here':
                jump['phase'] = 'list'
                jump['age'] = 0
                return [pad('a')]
            if move:
                return [move]
            return _end_castle_jump(screen, mem, 'shiro_missing')
        return _end_castle_jump(screen, mem, 'unexpected_menu')

    if phase == 'list':
        if screen.kind == 'main_menu':
            if jump['age'] >= 2 and int(jump.get('menu_presses') or 0) < 3:
                jump['menu_presses'] = int(jump.get('menu_presses') or 0) + 1
                return [pad('a')]
            if jump['age'] < 3:
                return []
            return _end_castle_jump(screen, mem, 'list_not_open')
        rows = _castle_list_rows(screen)
        if rows is None:
            jump['blind'] = int(jump.get('blind') or 0) + 1
            if jump['blind'] <= CASTLE_JUMP_CURSOR_BLIND:
                return []
            return _end_castle_jump(screen, mem, 'list_unreadable')
        jump['blind'] = 0
        target = _castle_list_target(screen, jump['source'])
        if target is None:
            if int(jump.get('scrolls') or 0) < CASTLE_JUMP_SCROLL_LIMIT:
                jump['scrolls'] = int(jump.get('scrolls') or 0) + 1
                jump['age'] = 0
                return [pad('down')]
            return _end_castle_jump(screen, mem, 'castle_missing')
        move = menu_to(screen, target)
        if move == 'here':
            jump['phase'] = 'confirm'
            jump['age'] = 0
            return [pad('select')]
        if move:
            return castle_list_batch(screen, target) or [move]
        return _end_castle_jump(screen, mem, 'castle_unreachable')

    if phase == 'confirm':
        if screen.kind == 'map':
            if reanchor:
                # A confirmed SELECT from the named row is the new map origin.
                # Do not inspect roof pixels or press A: this is localization,
                # not a sortie or castle status request.
                cell = chart.castles(mem.get('chapter') or 0).get(jump['source'])
                if cell is None:
                    return _end_castle_jump(screen, mem, 'home_coordinates_missing')
                cursor = _cursor(screen)
                if cursor is None:
                    if jump['age'] < 4:
                        return []
                    return _end_castle_jump(screen, mem, 'map_cursor_missing')
                mem['cursor'] = list(cell)
                mem['uncertain'] = False
                mem['anchor'] = jump['source']
                for key in ('nav_search', 'nav_search_leg', 'nav_last',
                            'nav_prev', 'nav_still', 'nav_pressed', 'select_used',
                            'select_centered', 'cursor_still', 'unanchored',
                            'unverified', 'near_goal', 'expect_menu',
                            'menu_miss', 'menu_hold'):
                    mem.pop(key, None)
                tick = int(mem.get('tick') or 0)
                mem['nav_last'] = {'screen': list(cursor), 'expected': [0, 0],
                                   'tick': tick, 'kind': screen.kind, 'search': False}
                mem['goal_anchor_lock'] = jump['source']
                mem['select_focus_tick'] = tick
                mem['select_tick'] = tick
                mem.pop('castle_jump', None)
                mem.pop('castle_jump_skip', None)
                # Reanchoring is a prerequisite, not the finished action.
                # Release this map frame to the normal dispatcher.
                mem['reanchor_resume'] = {
                    'source': jump['source'], 'tick': tick,
                    'active_before': (jump.get('return_to') or {}).get('active_step',
                                                                         _plan_region(mem).get('active')),
                    **(jump.get('return_to') or {}),
                }
                _record(mem, 'cursor_reanchor_confirm', target=jump['source'],
                        observed_metric={'cell': list(cell), 'screen_cursor': list(cursor)},
                        reason='アルマムーンのSELECTで座標を復旧し中断した処理を再評価する')
                return None
            hero_select = jump.get('mode') == 'hero_select'
            if hero_select and jump['age'] < HERO_SELECT_SETTLE_AGE:
                # The field SELECT starts from a map frame, so the first map
                # reading may still be the pre-SELECT camera (live: SELECT,
                # one fade frame, then the centred map one observation later).
                return []
            # The named own-castle row was selected before SELECT. Its field
            # return is the navigation evidence; the cursor sprite can cover
            # every roof pixel, so an absent detected roof is not a refusal.
            # The deploy layer still reads the *actual* castle's status and
            # checks its name before allowing しゅつげき.
            roof_visible = _roof_under_cursor(screen, frame)
            mem['expect_menu'] = True
            mem.pop('castle_jump', None)
            mem.pop('castle_jump_skip', None)
            # SELECT from the named row (or onto the hero's castle) put the
            # cursor on the source. Keep the estimate there; the deploy layer
            # still corrects it from the castle status if it was not.
            cell = chart.castles(mem.get('chapter') or 0).get(jump['source'])
            if cell:
                mem['cursor'], mem['uncertain'] = list(cell), False
            _record(mem, 'castle_jump_confirm', chart_step=jump['step'], target=jump['source'],
                    **({'strategy_variant': 'hero_select'} if hero_select else {}),
                    observed_metric={'cursor': mem.get('cursor'),
                                     'screen_cursor': list(_cursor(screen) or ()),
                                     'roof_under_cursor': roof_visible},
                    reason=('主人公の城へのSELECT後のフィールドなので、Aで城メニューを開き実ステータスで出撃元を照合する'
                            if hero_select else
                            'しろ一覧で城名を確認したSELECT後のフィールドなので、屋根が隠れてもAで城メニューを開き実ステータスで出撃元を照合する'))
            return [pad('a')]
        if jump.get('mode') == 'hero_select' and screen.kind in ('main_menu', 'castle_info',
                                                                 'castle_menu', 'general_list'):
            # Not the field return SELECT should give: leave this panel and
            # let the next attempt for the step walk the named しろ list.
            _mark_hero_select_miss(mem, jump['step'])
            mem.pop('castle_jump', None)
            _record(mem, 'castle_jump_failed', chart_step=jump['step'], target=jump['source'],
                    screen=screen.kind, strategy_variant='hero_select',
                    observed_metric={'why': 'hero_select_panel', 'age': jump.get('age')},
                    reason='主人公の城へのSELECTでフィールドに戻らなかったため、パネルを閉じてXメニューのしろ一覧で飛び直す')
            return [pad('b')]
        if _castle_list_rows(screen):
            if int(jump.get('selects') or 0) < 2:
                jump['selects'] = int(jump.get('selects') or 0) + 1
                return [pad('select')]
            return _end_castle_jump(screen, mem, 'select_not_accepted')
        if screen.kind in ('castle_info', 'castle_menu', 'general_list'):
            # SELECT opened the castle status instead of flying: back out to
            # the list once and retry with the same target row.
            jump['phase'] = 'list'
            jump['age'] = 0
            jump['status_retries'] = int(jump.get('status_retries') or 0) + 1
            _record(mem, 'castle_jump_status', chart_step=jump['step'], target=jump['source'],
                    screen=screen.kind,
                    reason='SELECTで城一覧の状態画面が開いたため、一覧へ戻って再試行')
            if jump['status_retries'] <= 1:
                return [pad('b')]
            return _end_castle_jump(screen, mem, 'select_opens_status')
        if jump.get('mode') == 'hero_select' and jump['age'] >= HERO_SELECT_WAIT_LIMIT:
            _mark_hero_select_miss(mem, jump['step'])
            mem.pop('castle_jump', None)
            _record(mem, 'castle_jump_failed', chart_step=jump['step'], target=jump['source'],
                    screen=screen.kind, strategy_variant='hero_select',
                    observed_metric={'why': 'hero_select_no_field', 'age': jump.get('age')},
                    reason='主人公の城へのSELECT後にフィールドを確認できないため、Xメニューのしろ一覧で飛び直す')
            return []
        return []
    return _end_castle_jump(screen, mem, 'unknown_phase:' + str(phase))


def resume_reanchored_owner(screen, mem, frame):
    """Resume the interrupted owner before the ordinary dispatch priority.

    A SELECT recovery lands at home, so a monthly status or field-location
    transaction must re-enter through a fresh menu rather than replaying A.
    """
    resume = mem.get('reanchor_resume')
    if not isinstance(resume, dict) or not resume.get('owner'):
        return None
    if (resume.get('chapter') != mem.get('chapter')
            or resume.get('month') != mem.get('month')):
        mem.pop('reanchor_resume', None)
        _record(mem, 'cursor_reanchor_owner_expired',
                observed_metric={'owner': resume.get('owner'),
                                 'stage': resume.get('stage')},
                reason='復旧中に章・月が変わったため古い中断状態を再生しない')
        return None
    if screen.kind != 'map':
        return None  # battle, dialogue and menu events keep their foreground
    owner = resume['owner']
    if owner == 'post_month_status':
        from . import post_month_status
        if not post_month_status.reenter_after_reanchor(mem, resume):
            mem.pop('reanchor_resume', None)
            return None
        actions = post_month_status.step(screen, mem, frame)
        if actions or 'post_month_status' not in mem:
            resume['resumed_owner'] = owner
        return actions
    if owner in {'sortie_plan', 'map_navigation'}:
        if owner == 'sortie_plan' and _plan_region(mem).get('active') != resume.get('active_before'):
            mem.pop('reanchor_resume', None)
            _record(mem, 'cursor_reanchor_owner_expired',
                    observed_metric={'owner': owner,
                                     'active_before': resume.get('active_before'),
                                     'active_now': _plan_region(mem).get('active')},
                    reason='復旧中に出撃指示が変わったため古い指示を再生しない')
            return None
        actions = map_step(screen, mem, frame)
        if actions:
            resume['resumed_owner'] = owner
        return actions
    mem.pop('reanchor_resume', None)
    return None


def note_reanchor_followup(screen, mem, actions):
    """Trace the action after SELECT reanchoring, or why a safe hold remains.

    A foreground flow may legitimately own the map before the sortie planner.
    Keep this receipt until a real policy action is selected.
    """
    resume = mem.get('reanchor_resume')
    if not isinstance(resume, dict):
        return
    if resume.get('source') not in chart.castles(mem.get('chapter') or 0):
        mem.pop('reanchor_resume', None)
        return
    tick = int(mem.get('tick') or 0)
    if resume.get('resumed_owner') or (actions and not resume.get('owner')):
        mem.pop('reanchor_resume', None)
        _record(mem, 'cursor_reanchor_resumed', screen=screen.kind,
                observed_metric={'source': resume.get('source'),
                                 'owner': resume.get('resumed_owner') or resume.get('owner'),
                                 'stage_before': resume.get('stage'),
                                 'stage_after': (mem.get('post_month_status') or {}).get('stage'),
                                 'active_before': resume.get('active_before'),
                                 'active_after': _plan_region(mem).get('active'),
                                 'actions': actions[:3]},
                reason='位置復旧後、中断元の処理に制御を戻した')
        return
    previous = resume.get('wait_report_tick')
    if previous is not None and tick - int(previous) < 20:
        return
    resume['wait_report_tick'] = tick
    status = mem.get('post_month_status') or {}
    if isinstance(status, dict) and status.get('stage') == 'blocked':
        blocked_by = 'post_month_status_blocked'
        why = '月次ステータス確認が失敗状態のため安全に再開できない'
    elif resume.get('owner') == 'post_month_status' and isinstance(status, dict) and status:
        blocked_by = 'post_month_status:' + str(status.get('stage'))
        why = '月次ステータスの中断段階に戻り、画面の再確認を待っている'
    elif mem.get('_map_registration_hold'):
        blocked_by = 'map_registration'
        why = '地図登録・座標確認が操作を保留している'
    elif mem.get('map_idle_tick') == tick and _plan_region(mem).get('active') is None:
        blocked_by = 'no_ready_order'
        why = '中断前の指示と次の計画を再評価したが実行可能な出撃・移動がない'
    elif _plan_region(mem).get('active'):
        blocked_by = 'active_order_wait'
        why = '中断中の指示を再評価したが出撃元の確認などで入力を保留している'
    elif isinstance(status, dict) and status:
        blocked_by = 'post_month_status:' + str(status.get('stage'))
        why = '月次ステータス処理の画面確認が進行中'
    else:
        blocked_by = 'other_flow_hold'
        why = '通常処理を再評価したがこの観測では安全な入力が決まらない'
    _record(mem, 'cursor_reanchor_wait', screen=screen.kind,
            observed_metric={'blocked_by': blocked_by, 'active': _plan_region(mem).get('active'),
                             'return_owner': resume.get('owner'),
                             'return_stage': resume.get('stage'),
                             'active_before': resume.get('active_before'),
                             'plan_steps': [o.get('step') for o in
                                            (mem.get('chart_plan') or {}).get('orders', [])[:8]],
                             'orders': {k: v for k, v in list((_plan_region(mem).get('orders') or {}).items())[:8]},
                             'wait_ticks': tick - int(resume.get('tick') or tick)},
            reason=why)


def map_step(screen: Screen, mem, frame):
    # Did this module really run? The give-up logs 199 records in twenty minutes
    # while the probe added in v419 never appeared, so the running module may be
    # older than the checkout (issue #3417). A tally keeps it out of the
    # decision log while still showing the loaded version in the policy state.
    _tally(mem, f'module:{BOT_VERSION}')
    _tally(mem, 'map_step_seen')
    if not _cursor(screen):
        # The map step ran but the frame shows no cursor sprite: the position
        # cannot be integrated (g1133 2026-11-05 froze at [392,144] for hours
        # with `nav_last` null). Counted, not recorded, so it stays cheap.
        _tally(mem, 'map_no_cursor')
    _watch_cursor_still(mem)
    # Idle maps deliberately wait for armies/events. Keep any interrupted
    # search for the next order instead of repeatedly jumping home before
    # plan selection and ownership surveys can run.
    if _plan_region(mem).get('active') and screen.cursor and not screen.marker:
        reanchor = _start_reanchor_jump(mem)
        if reanchor is not None:
            return reanchor
    _release_stale_active(mem)
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
    # Camp recall is no longer an opportunistic map interrupt. The
    # post-month roster owner verifies each general first and schedules the
    # named recall after the status scan; this path keeps driving orders.
    # Owner rule (2026-09-28): periodically SELECT to the hero's position to
    # re-focus the view on where we are. The jump is camera motion we did not
    # measure: drop nav_last and demand a re-anchor (mirrors the search's
    # select_to_hero). Seeded on the first map frame, then every interval.
    # A frame that just re-established a known position from the previous
    # SELECT must drive orders instead of refocusing again: map frames can be
    # sparser than the interval while a chapter throws battles (g1133
    # 2026-11-05: every cluster re-sent SELECT and no order ever drove).
    recentered = _apply_select_centered(mem)
    if (not recentered
            and _cursor(screen) and not screen.marker and not mem.get('y_jump')
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
            mem['select_centered'] = tick
            return [pad('select')]
    order = _order(mem)
    if order is not None and (mem.get('source_miss') or {}).get(order['step'], 0) >= SOURCE_MISS_LIMIT:
        if _home_can_send(mem):
            _give_up_source(mem, order)
            order = _order(mem)
        # Otherwise the home castle has nobody to send (g1133 2026-11-05:
        # home_garrison [] while source_miss stayed at 5 for an hour), so the
        # fallback could never launch and giving up only blocked the walk to
        # the chart's own source. Keep walking instead (issue #3417).
    verifying = order is not None and (_plan_region(mem).get('orders') or {}).get(order['step']) == 'launched_unconfirmed'
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
        _plan_region(mem)['active'] = None
        mem['picked'] = []
        order = None
    if isinstance(order, dict) and (str(order.get('step', '')).startswith(chart_adjust.INTERIM_PREFIX)
                                    or (mem.get('chart_plan') or {}).get('source') == 'mechanical'):
        if (mem.get('chart_adjust') or {}).get('request_id') == chart_adjust.request_id(mem):
            _off_chart(mem)
            order = _order(mem) or next_order(mem) or order
    if order is None and _cursor(screen) and not screen.marker and _world_map_wanted(mem):
        # Before choosing what to do next, read every castle's owner at once.
        mem['world_map_tick'] = int(mem.get('tick') or 0)   # bounded even if Y shows nothing
        mem.pop('world_map_due', None)
        _record(mem, 'world_map_open', screen=screen.kind,
                reason='全城の所有を全体マップで確認するためYを押す')
        return [pad('y')]
    if order is None:
        order = next((_order_for_step(mem, step) for step, sortie in _sorties(mem).items()
                      if sortie.get('status') == 'launched_unconfirmed'
                      and not sortie.get('verification_unavailable')
                      and sortie.get('source') in _owned(mem)), None)
        order = order or next_order(mem)
        if (order is None or (isinstance(order, dict)
                              and (str(order.get('step', '')).startswith(chart_adjust.INTERIM_PREFIX)
                                   or (mem.get('chart_plan') or {}).get('source') == 'mechanical'))):
            _off_chart(mem)
            order = next_order(mem) or order
        if order is None:
            update_world(screen, mem, frame)
            goals.note_map_idle(mem)
            return []           # nothing charted: let real time advance
        if not _order_start_due(mem, order['step']):
            return []
        _plan_region(mem)['active'] = order['step']
        mem['picked'] = []
        # The bounded inventory search belongs to this attempt, not the
        # order forever. A cancelled attempt must not exhaust every retry.
        (mem.get('card_scroll') or {}).pop(order['step'], None)
        if (_plan_region(mem).get('orders') or {}).get(order['step']) != 'launched_unconfirmed':
            # The unreadable-panel fallback also belongs to this attempt.
            # Keeping its spent B/A budget makes a later sortie with the same
            # step cancel immediately, before it can recover its own panel.
            # A source-roster check of an unconfirmed launch is reconciliation,
            # not permission to rearm the previous departure's blind A.
            for key in ('card_unreadable', 'card_skip_departure',
                        'sortie_confirm_miss', 'sortie_confirm_blind'):
                (mem.get(key) or {}).pop(order['step'], None)
        mem.pop('card_upper_boundary', None)
        mem.pop('card_scroll_last', None)
        stock = mem.get('card_stock') if isinstance(mem.get('card_stock'), dict) else {}
        unseen = mem.get('card_bought_unseen') if isinstance(mem.get('card_bought_unseen'), dict) else {}
        mem['card_zero_at_start'] = {'step': order['step'], 'cards': sorted(
            card for card, count in stock.items() if count == 0 and card not in unseen)}
        mem.setdefault('rare_card_kit', {}).pop(order['step'], None)
        mem.setdefault('strong_card_kit', {}).pop(order['step'], None)
        mem.setdefault('dropper_card_kit', {}).pop(order['step'], None)
        mem.pop('rare_scan', None)
        mem['y_jumps'] = {k: v for k, v in (mem.get('y_jumps') or {}).items()
                          if not k.startswith(f"{order['step']}:")}
        _forget_failed_jump(mem, order['step'])
        mem.pop('castle_verified', None)
        if order.get('source_probe') is True:
            mem.setdefault('arrival_probe_tick', {})[_source(order, mem)] = int(mem.get('tick') or 0)
        _record(mem, 'order_start', chart_step=order['step'], **_deploy_context(order, mem),
                source=order['source'], target=order['target'], purpose=order.get('purpose'),
                cards=list(order['cards']),
                **({'roster_probe': True} if order.get('roster_probe') is True else {}),
                reason=order['note'])
    source = _source(order, mem)
    goal = _measured_goal(mem, order, source)
    if goal is None:
        return []
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
    if screen.cursor and not screen.marker and _want_castle_jump(mem, order, source, goal):
        return _start_castle_jump(mem, order, source)
    result = nav_step(screen, mem, frame, goal, source, step=order['step'])
    if result == 'infeasible':
        return _abandon_stuck_step(screen, mem, order)
    if chart.approximate_map() and chart.approx_chapter(mem.get('chapter') or 0):
        # Only while this chapter navigates on the seed table. A measured
        # chapter gains nothing from the fallback and lost its order to it
        # (g1075 2026-10-29 09:14:55-09:15:56 JST, chapter 3: four roof-less
        # map frames -> ``approx_map_fallback`` -> the same interim order
        # re-picked -> held ~1 min until the periodic SELECT).
        cursor_before = mem.get('cursor')
        if mem.get('anchor') or result == 'arrived':
            mem.pop('approx_localize_miss', None)
        else:
            misses = int(mem.get('approx_localize_miss') or 0) + 1
            mem['approx_localize_miss'] = misses
            if misses >= APPROX_LOCALIZE_LIMIT:
                order_step = (order or {}).get('step')
                mem['approx_fallback'] = True
                mem.pop('approx_localize_miss', None)
                _plan_region(mem)['active'] = None
                mem['picked'] = []
                mem['uncertain'] = True
                mem.pop('cursor', None)
                _record(mem, 'approx_map_fallback', chart_step=order_step,
                        screen=screen.kind,
                        observed_metric={'misses': misses, 'cursor': cursor_before,
                                         'screen_cursor': list(_cursor(screen) or ())},
                        reason='近似座標で屋根に再アンカーできないため近似を止め、実測地図探索へ切り替える')
                return []
    else:
        mem.pop('approx_localize_miss', None)     # a count left from before the chapter was measured
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
        # A named SELECT return is stronger than roof pixels obscured by the
        # cursor. The deploy flow still checks the actual source castle name.
        resume = mem.get('reanchor_resume') or {}
        selected_source = (resume.get('source') == source
                           and mem.get('anchor') == source
                           and not mem.get('uncertain'))
        if not selected_source and not _roof_under_cursor(screen, frame) and _hold_off_castle(screen, mem, order):
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
        flow(mem).pop('departure_request', None)   # a stale reservation owns nothing
        # A marker we did not request: cancel instead of sending a general.
        _record(mem, 'unexpected_target', reason='指示中でない出撃先選択画面のためBで取消')
        return [pad('b')]
    locked = _hold_locked_boss(mem, order)
    if locked is not None:
        return locked
    if (_is_boss_order(order, mem) and _sortie_general(order, mem) == NAME
            and (_plan_region(mem).get('orders') or {}).get(order['step']) not in {'launched', 'launched_unconfirmed'}):
        _flow(mem).pop('sortie_attempt', None)
        return _hold_guarded_sortie(mem, order, 'boss_companion_required',
            '未送信の主人公のボス目標を取り消し、十分な戦力の代役を待つ',
            {'general': NAME, 'screen': screen.kind, 'hero_forbidden': True})
    if _broken_hero_order(order, mem):
        return _cancel_broken_hero_sortie(mem, order)
    if _is_boss_order(order, mem):
        context = (_plan_region(mem).get('order_context') or {}).get(order['step']) or {}
        if (not context.get('actual_general')
                or context.get('actual_general') != _sortie_general(order, mem)
                or (_plan_region(mem).get('sortie_general') or {}).get(order['step']) != context.get('actual_general')
                or (context.get('observed_metric') or {}).get('cards') != sorted(_deploy_cards(order, mem))):
            return _hold_deploy(screen, mem, order, 'ボス出撃の選択本人と携行品の確認証拠がないため目標確定を保留')
    request = flow(mem).get('departure_request')
    if isinstance(request, dict):
        if request.get('step') != order['step']:
            flow(mem).pop('departure_request', None)   # a reservation of another order owns nothing
        else:
            return _departure_request_step(screen, mem, order, request).actions
    goal = _measured_goal(mem, order, order['target'])
    if goal is None:
        return []
    routed = _hold_unrouted_sortie(mem, order)
    if routed is not None:
        return routed
    result = None
    if screen.marker and _near_goal(mem, order, order['target'], 'target'):
        if chart.is_boss_castle(order['target']):
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
        if screen.marker and not chart.is_boss_castle(order['target']):
            # g1067 09:49-10:19: dead reckoning stopped the marker 10 px right
            # of Grin's roof, and the up/down nudges never corrected x; every
            # retake was cancelled twice. A roof of the expected owner beside
            # the marker is the castle the estimate meant: walk onto it.
            kinds = ('own',) if order['target'] in _owned(mem) else ('enemy',)
            aligned = _align_on_roof(screen, mem, frame, kinds, f"{order['step']}:target")
            if aligned and aligned != 'on':
                mem['near_goal'] = {'step': order['step'], 'goal': order['target'], 'mode': 'target'}
                _record(mem, 'target_roof_align', chart_step=order['step'], target=order['target'],
                        observed_metric={'cursor': mem.get('cursor'), 'marker': list(_cursor(screen) or ())},
                        reason='推定到着点の近くに目的と同じ色の屋根があるため、その屋根へマーカーを合わせる')
                return _deploy_input(screen, mem, order, aligned, '推定到着点の近くの屋根へ出撃先マーカーを合わせる')
        return _unverified_target(screen, mem, order)
    if result == 'arrived':
        context = _deploy_context(order, mem, expected_metric='のりこんだ表示で目標城を確認')
        snapshot = departure_snapshot(order, mem, context['general'], _source(order, mem))
        if snapshot is None:
            return []
        (mem.get('target_miss') or {}).pop(order['step'], None)
        # The decided A is a reservation, not a fact: the fixed actor, source
        # and carried kit are held while the observed garrison and the order
        # stay untouched until the journaled send and the closed picker prove
        # the departure (docich#3193 A). ``sortie_attempt`` stays armed so
        # leaving this picker remains the departure observation.
        attempt = _flow(mem).get('sortie_attempt') or {}
        attempt['step'] = order['step']
        attempt['target_seen'] = True
        attempt.pop('interrupted', None)     # a new execution, not the old one
        _flow(mem)['sortie_attempt'] = attempt
        flow(mem)['departure_request'] = {
            'step': order['step'], 'target': order['target'],
            'general': context['general'], 'source': _source(order, mem),
            'context': context, 'snapshot': snapshot,
            'requested_tick': int(mem.get('tick') or 0),
            'status': 'requested', 'resends': 0}
        from .camp_recheck import departure_requested
        departure_requested(mem, context['general'],
                            snapshot.get('departure_binding', {}).get('confirm_tick'))
        _record(mem, 'sortie_departure_requested', chart_step=order['step'],
                **context, target=order['target'],
                resulting_event='planned_not_yet_sent',
                reason=f"{context['general']}を{order['target']}へ出撃するAを要求。送信・受理・出発は未確認のまま在城事実を消さない")
        return [pad('a')]
    return _deploy_input(screen, mem, order, result or [], '出撃先へ目標カーソルを移動')


def _commit_departure(mem, order, context, snapshot):
    """Departure observed: the fixed request becomes world state exactly once.

    Runs only on receipt-backed evidence (the picker closed after a journaled
    A). The garrison fact, the launched order and the en-route sortie it
    creates are identical to what the pre-#3193 code committed eagerly.
    """
    (mem.get('target_miss') or {}).pop(order['step'], None)
    _finish_order(mem, 'launched', **context, target=order['target'],
                  cursor=mem.get('cursor'), anchor=mem.get('anchor'),
                  reason=f"{context['general']}を{order['target']}へ出撃")
    general = context['general']
    _garrison_move(mem, general, source=_source(order, mem))
    _world(mem).setdefault('launched', {})[order['target']] = {'general': general, 'step': order['step']}
    # Snapshot: the battle, boss entry and retries of this sortie must not
    # depend on the plan still containing it.
    _world(mem).setdefault('launched_orders', {})[order['step']] = snapshot
    # Per execution id: ``launched`` keeps one sortie per castle, so a later
    # sortie to the same castle must not unbind an earlier unit en route.
    previous = _sorties(mem).get(order['step'])
    if previous:
        _retire_unentered_sortie(mem, order['step'], previous)
    _world(mem).setdefault('sorties', {})[order['step']] = {
        'general': general, 'source': snapshot['source'],
        'target': order['target'], 'status': 'en_route',
        'purpose': snapshot.get('purpose'),
        'evidence': deepcopy({k: v for k, v in
            ((_plan_region(mem).get('order_context') or {}).get(order['step']) or {}).items()
            if k != 'departure_order'}),
        'tick': int(mem.get('tick') or 0),
        **({'confirmation_tick': snapshot['departure_binding']['confirm_tick']}
           if 'departure_binding' in snapshot else {})}
    ((_plan_region(mem).get('order_context') or {}).get(order['step']) or {}).pop('departure_order', None)
    chapter = mem.get('chapter') or 0
    castles = chart.castles(chapter)
    if (WORLD_MAP_OFFSET.get(chapter) and order['target'] in castles
            and snapshot['source'] in castles):
        # A sent A plus a closed picker is still not proof the unit left.
        # g1097: three committed sorties produced no world-map ▲ at all
        # (the same marker idled for 40 min) and rotted into march_overdue.
        # The next surveys must show a unit the baseline did not have.
        _flow(mem).setdefault('launch_probe', {})[order['step']] = {
            'general': general, 'source': snapshot['source'],
            'target': order['target'], 'reads': 0,
            'baseline': deepcopy(mem.get('_world_units_prev') or []),
            'tick': int(mem.get('tick') or 0), 'roster_read': False}
        mem['world_map_due'] = True


BOSS_MARKER_CELL_PX = 24     # 3 tiles: the marker on the measured boss cell
TARGET_MISS_LIMIT = 3          # unverified target arrivals before cancelling the sortie
TARGET_CANCEL_LIMIT = 2        # cancelled sorties per order before failing it
DEPARTURE_SENT_HOLD_LIMIT = 4     # sent picker readings before treating the A as unaccepted
DEPARTURE_RESEND_LIMIT = 2        # journal-proven unsent resends inside one request
DEPARTURE_REQUEST_LIMIT = 2       # released request cycles per order before failing it
DEPARTURE_UNKNOWN_HOLD_LIMIT = 3  # unverifiable receipt readings; never a blind resend
# g1163 2026-11-09 19:00-19:40: 12 of 15 unverified launches were marker misses that
# fell through to a source-roster trip (a castle jump plus a general list, ~20-30 s
# each) even though the army had left. The departure cutscene can render the new
# triangle after the third survey, so allow two more reads before the expensive
# roster check: an absent marker costs 2 extra frames, a false miss costs a trip.
LAUNCH_PROBE_READS = 5            # world-map surveys without the new unit before the roster check
LAUNCH_PROBE_RADIUS = 10        # world-map px from source/target flag counting as that unit
LAUNCH_PROBE_NEW_MARKER_PX = 4  # centre distance that still counts as the same marker
# A marching unit's triangle is ~7x6; a 3x3 red decoration must not confirm a
# launch (g1163 2026-11-09 05:28: [[149,63,152,66]] confirmed three launches
# while only the hero's own marker was on the view).
LAUNCH_PROBE_MIN_BOX = 20      # bounding-box pixels for a plausible unit marker
LAUNCH_PROBE_ROSTER_TICKS = 200  # blind probe ticks to wait for the source roster before rollback
LAUNCH_MISS_LIMIT = 2           # unverified launches an order may retry before failing


def _read_source_roster(mem, castle):
    """Mark a source castle's general list as due for launch verification.

    docich#3515: a blind boss probe waits for the source roster, but no
    order opens that castle until the march outlives its arrival window
    (up to 2400 ticks). Marking the castle lets the planner mint one
    roster-probe order that opens the list and lets
    settle_launch_probes_from_roster decide. The mark itself reads nothing;
    the deploy step performs the read.
    """
    if not castle:
        return
    due = mem.setdefault('roster_read_due', {})
    if castle not in due:
        due[castle] = int(mem.get('tick') or 0)


def _marker_near_castle(mem, marker, castle):
    offset = WORLD_MAP_OFFSET.get(mem.get('chapter') or 0)
    cell = chart.castles(mem.get('chapter') or 0).get(castle)
    if not offset or not cell:
        return False
    mx, my = (marker[0] + marker[2]) / 2, (marker[1] + marker[3]) / 2
    return (abs(mx - (cell[0] / 8 + offset[0])) <= LAUNCH_PROBE_RADIUS
            and abs(my - (cell[1] / 8 + offset[1])) <= LAUNCH_PROBE_RADIUS)


def _probe_launch_markers(mem, markers):
    """Verify a committed departure left a unit marker on the world map.

    The picker closing after a journaled A commits the sortie, but g1097
    showed that combination firing with no unit ever appearing. A marker the
    launch-time baseline did not have is the launch's first hard evidence:
    near the source or target it always counts, anywhere else it counts once
    a baseline exists. A sortie settled by another path (arrival, battle,
    overdue) drops the probe instead of being rolled back.

    Absence of a marker is not proof the unit stayed home (v389). g1133
    2026-11-05 showed chapter 6 drawing marching units as portraits while the
    probe only knows the (255,24,0) triangle: every survey of a view that
    held nothing but the static house icon rolled a launch back, and the next
    source roster showed the general had left. Once the surveys carry no unit
    evidence the probe waits for that roster
    (settle_launch_probes_from_roster) and only the wait expiring falls back
    to the old rollback.
    """
    probes = _flow(mem).get('launch_probe') or {}
    if not probes:
        return
    now = int(mem.get('tick') or 0)
    for step, probe in list(probes.items()):
        sortie = _sorties(mem).get(step)
        if not sortie or sortie.get('status') not in ('en_route', 'launched_unconfirmed'):
            probes.pop(step, None)
            continue
        if probe.get('blind'):
            if probe.get('kept'):
                # A kept boss march waits for the source roster (or for
                # arrival/overdue to settle it elsewhere). The probe stays
                # so a later roster still rolls it back when it lists the
                # general (docich#3515: popping it on kept made the roster
                # evidence useless and left a phantom en_route).
                continue
            since = probe.get('tick')
            if type(since) is int and now - since < LAUNCH_PROBE_ROSTER_TICKS:
                continue
            if chart.is_boss_castle(probe.get('target')):
                # g1133 2026-11-05: chapter 6 draws marching units as
                # portraits, so the triangle survey can never confirm a boss
                # launch. Twelve boss sorties were rolled back and sent again
                # while the armies were still marching (ライム entered the
                # boss castle at 15:39 after his launch had been rolled back
                # at 15:21). Keep the march: a roster that still lists the
                # general rolls it back (settle_launch_probes_from_roster),
                # and _relaunch_overdue_boss still bounds a march that never
                # entered.
                # docich#3515: keep the probe after kept. Popping it here
                # left a phantom en_route that no roster could ever settle;
                # the next roster of the source castle (actively requested
                # via _read_source_roster) decides instead. The launch is
                # marked kept so an observed camp/castle end reopens the
                # order instead of stranding it launched.
                probe['kept'] = True
                sortie['launch_kept'] = True
                _read_source_roster(mem, probe.get('source'))
                _record(mem, 'sortie_launch_unconfirmed_kept', chart_step=step,
                        general=probe.get('general'),
                        observed_metric={'general': probe.get('general'),
                                         'source': probe.get('source'),
                                         'target': probe.get('target'),
                                         'markers': [list(b) for b in markers],
                                         'reads': probe.get('reads')},
                        resulting_event='boss_march_kept',
                        reason='ボス行軍は部隊表示が肖像で▲を検出できないため、出撃成立の巻き戻しをせず到着・期限の実測に委ねる')
                continue
            probes.pop(step, None)
            _rollback_unverified_launch(mem, step, probe, markers)
            continue
        baseline = probe.get('baseline') or []
        fresh = [b for b in markers
                 if (b[2] - b[0] + 1) * (b[3] - b[1] + 1) >= LAUNCH_PROBE_MIN_BOX
                 and not any(abs((b[0] + b[2]) / 2 - (o[0] + o[2]) / 2) <= LAUNCH_PROBE_NEW_MARKER_PX
                             and abs((b[1] + b[3]) / 2 - (o[1] + o[3]) / 2) <= LAUNCH_PROBE_NEW_MARKER_PX
                             for o in baseline)]
        near = [b for b in fresh
                if _marker_near_castle(mem, b, probe.get('source'))
                or _marker_near_castle(mem, b, probe.get('target'))]
        if near or (baseline and fresh):
            sortie['launch_confirmed'] = True
            probes.pop(step, None)
            _record(mem, 'sortie_launch_confirmed', chart_step=step,
                    general=probe.get('general'),
                    observed_metric={'markers': [list(b) for b in markers],
                                     'new_markers': [list(b) for b in fresh]},
                    reason='出撃コミット後の全体マップに基準に無い部隊▲を確認')
            continue
        probe['reads'] = int(probe.get('reads') or 0) + 1
        if probe['reads'] >= LAUNCH_PROBE_READS:
            probe['blind'] = True
            probe['tick'] = now
            _read_source_roster(mem, probe.get('source'))
            _record(mem, 'sortie_launch_unverified', chart_step=step,
                    general=probe.get('general'),
                    observed_metric={'general': probe.get('general'),
                                     'source': probe.get('source'),
                                     'target': probe.get('target'),
                                     'markers': [list(b) for b in markers],
                                     'reads': probe['reads']},
                    resulting_event='roster_pending',
                    reason='全体マップに部隊▲が現れないため、出撃元の将軍一覧で出撃の成立を確認する')


def settle_launch_probes_from_roster(mem, castle, generals):
    """Settle a launch probe with the source castle's own roster.

    A completed roster read is direct evidence about the general the ledger
    sent out: gone from the list means the sortie left, still listed means the
    committed A never took him. g1133 2026-11-05: the Y view drew marching
    units as portraits, so only the roster could tell a real launch from a
    failed one. Presence is the only case that rolls back; absence confirms.
    The read settles the probe whether or not the marker survey already went
    blind: the roster arrives first on many sorties.
    """
    probes = _flow(mem).get('launch_probe') or {}
    if not probes:
        return
    roster = list(generals)
    for step, probe in list(probes.items()):
        if probe.get('source') != castle:
            continue
        general = probe.get('general')
        if general in roster:
            probes.pop(step, None)
            _rollback_unverified_launch(mem, step, probe, [],
                                        reason='出撃元の将軍一覧にまだ出撃した将軍がいるため、出撃を未発射に戻す')
            continue
        probes.pop(step, None)
        sortie = _sorties(mem).get(step)
        if isinstance(sortie, dict):
            sortie['launch_confirmed'] = True
            sortie.pop('launch_kept', None)
        # The launch is confirmed: the equivalent-order miss budget it may
        # have accrued belongs to a failed attempt, not to this one.
        for key, row in list((mem.get('equivalent_launch_miss') or {}).items()):
            if isinstance(row, dict) and row.get('step') == step:
                (mem.get('equivalent_launch_miss') or {}).pop(key, None)
        _record(mem, 'sortie_launch_confirmed', chart_step=step, general=general,
                observed_metric={'castle': castle, 'general': general, 'roster': roster[:12]},
                reason='出撃元の将軍一覧に出撃した将軍がいないため出撃成立を確認')


def _rollback_unverified_launch(mem, step, probe, markers, reason=None):
    """A committed departure produced no unit marker: return it to pending.

    Mirrors the march-overdue reset so the order re-confirms from scratch,
    but leaves the observed garrison consistent: the general the ledger sent
    out was never seen leaving, so they go back onto the source roster.
    reason carries the roster evidence when the probe was settled by the
    source castle instead of the world map.
    """
    misses = _flow(mem).setdefault('launch_miss', {})
    misses[step] = misses.get(step, 0) + 1
    for key in ('general_override', 'source_override', 'card_override', 'rare_card_kit',
                'strong_card_kit', 'dropper_card_kit', 'sortie_actor_miss'):
        (mem.get(key) or {}).pop(step, None)
    _plan_region(mem).setdefault('order_context', {}).pop(step, None)
    _plan_region(mem).setdefault('sortie_general', {}).pop(step, None)
    (_world(mem).get('launched_orders') or {}).pop(step, None)
    _sorties(mem).pop(step, None)
    for target, entry in list((_world(mem).get('launched') or {}).items()):
        if entry.get('step') == step:
            _world(mem)['launched'].pop(target, None)
    general, source = probe.get('general'), probe.get('source')
    garrison = _world(mem).get('garrison') or {}
    if general and isinstance(garrison.get(source), list) and general not in garrison[source]:
        garrison[source].append(general)
    observed = {'general': general, 'source': source, 'target': probe.get('target'),
                'markers': [list(b) for b in markers], 'reads': probe.get('reads'),
                'misses': misses[step]}
    if misses[step] >= LAUNCH_MISS_LIMIT:
        _plan_region(mem).setdefault('orders', {})[step] = 'failed'
        _record(mem, 'sortie_launch_failed', chart_step=step, general=general,
                deviation_reason='出撃確定後に部隊が現れない',
                observed_metric=observed,
                reason=reason or '出撃確定の入力後も部隊▲が現れず再試行の上限に達したため指示を失敗にする')
        return
    if _plan_region(mem).setdefault('orders', {}).get(step) == 'launched':
        _plan_region(mem)['orders'][step] = 'pending'
    _record(mem, 'sortie_launch_unverified', chart_step=step, general=general,
            observed_metric=observed, resulting_event='order_reopened',
            reason=reason or '出撃確定の入力後に全体マップへ部隊▲が現れないため、出撃を未発射に戻す')


def _departure_receipt_sent(mem, request):
    """The sender journal proves this exact request left as a pad A."""
    trace = (request or {}).get('request_trace') or {}
    receipt = journal(mem).get('_departure_input') or {}
    return bool(trace and receipt.get('request_trace') == trace
                and int(receipt.get('a_inputs') or 0) >= 1)


def _release_departure_request(mem, order, request, reason):
    """Release the reservation without touching observed facts.

    The general was never moved out of the garrison, so the order simply
    stays pending and the selection re-confirms within a bounded count.
    """
    step = order['step']
    flow(mem).pop('departure_request', None)
    _flow(mem).pop('sortie_attempt', None)
    from .house import _sortie_state_sig, _tally_sortie_attempt
    # Issue #3326 (34): a released request is its own outcome, not a cancel.
    _tally_sortie_attempt(mem, request.get('target') or order['target'],
        (request.get('snapshot') or {}).get('purpose') or order.get('purpose'),
        'released',
        _sortie_state_sig(mem, request.get('source'), request.get('general')))
    misses = flow(mem).setdefault('departure_miss', {})
    misses[step] = int(misses.get(step) or 0) + 1
    if misses[step] >= DEPARTURE_REQUEST_LIMIT:
        used = misses.pop(step)
        context = {k: v for k, v in (request.get('context') or {}).items()
                   if k != 'deviation_reason'}
        _finish_order(mem, 'failed', **context, target=order['target'],
                      deviation_reason='出撃要求が送信・受理されない',
                      observed_metric={'request_misses': DEPARTURE_REQUEST_LIMIT},
                      reason='出撃Aが送信・受理されないまま要求の上限に達したため指示を失敗にする')
        return FlowResult('departure_request', 'release', [pad('b')], reason,
                          retry={'used': used, 'limit': DEPARTURE_REQUEST_LIMIT},
                          permitted_recovery='none')
    _record(mem, 'sortie_departure_not_accepted', chart_step=step,
            **(request.get('context') or {}),
            observed_metric={'request_misses': misses[step]},
            resulting_event='request_released_unconfirmed',
            reason=reason)
    return FlowResult('departure_request', 'release', [], reason,
                      retry={'used': misses[step], 'limit': DEPARTURE_REQUEST_LIMIT},
                      permitted_recovery='reconfirm')


def _departure_request_step(screen, mem, order, request):
    """Drive a pending departure request across the send boundary.

    requested -> sent/unknown -> departure_observed -> arrived. While the
    picker stays open the sender journal decides what is known: a matched
    receipt may only confirm the send, an empty match may resend within
    bounds, and an unreadable journal may never resend blindly.
    """
    if screen.kind != 'map_target':
        # Leaving the picker is settled by observe_sortie_transition.
        return FlowResult('departure_request', 'commit_pending', [],
                          'ピッカー外への遷移は観測層が確定する', permitted_recovery='none')
    if _departure_receipt_sent(mem, request):
        request['status'] = 'sent'
        request.pop('unknown_reads', None)
        reads = request['sent_reads'] = int(request.get('sent_reads') or 0) + 1
        if reads == 1:
            _record(mem, 'sortie_departure_input_sent', chart_step=order['step'],
                    **(request.get('context') or {}),
                    observed_metric={'request_trace': request.get('request_trace')},
                    resulting_event='sent_not_yet_accepted',
                    reason='同じラン・判断ID・実画面の出撃A送信記録を確認。受理と出発は未確認')
        if reads >= DEPARTURE_SENT_HOLD_LIMIT:
            return _release_departure_request(mem, order, request,
                '送信記録のある出撃Aの後も出撃先選択が閉じないため、受理を断定せず要求を解放する')
        return FlowResult('departure_request', 'hold', [],
                          '送信済み・受理未確認のため再観測を待つ',
                          retry={'used': reads, 'limit': DEPARTURE_SENT_HOLD_LIMIT},
                          permitted_recovery='reobserve')
    receipt = journal(mem).get('_departure_input')
    trace = request.get('request_trace') or {}
    if trace and isinstance(receipt, dict) and receipt.get('request_trace') == trace:
        # The journal is readable and holds no send for this trace: the A
        # verifiably never left, so a bounded resend is not a blind retry.
        request.pop('sent_reads', None)
        request.pop('unknown_reads', None)
        if int(request.get('resends') or 0) < DEPARTURE_RESEND_LIMIT:
            request['resends'] = int(request.get('resends') or 0) + 1
            request['status'] = 'requested'
            request.pop('request_trace', None)   # persist() stamps the new send
            _record(mem, 'sortie_departure_resend', chart_step=order['step'],
                    **(request.get('context') or {}),
                    observed_metric={'resends': request['resends']},
                    resulting_event='unsent_retry',
                    reason='送信ジャーナルにこの出撃Aが無いことを確認したため同じ要求を再送する')
            return FlowResult('departure_request', 'input', [pad('a')],
                              '未送信が証明された要求の再送',
                              retry={'used': request['resends'], 'limit': DEPARTURE_RESEND_LIMIT},
                              permitted_recovery='resend')
        return _release_departure_request(mem, order, request,
            '出撃Aが送信記録に現れず再送の上限に達したため要求を解放する')
    reads = request['unknown_reads'] = int(request.get('unknown_reads') or 0) + 1
    if reads >= DEPARTURE_UNKNOWN_HOLD_LIMIT:
        return _release_departure_request(mem, order, request,
            '送信receiptを照合できないまま出撃先選択が残るため要求を解放する')
    return FlowResult('departure_request', 'hold', [],
                      'receipt不明のため盲目再送せず保留',
                      retry={'used': reads, 'limit': DEPARTURE_UNKNOWN_HOLD_LIMIT},
                      permitted_recovery='none')



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
    if chart.is_boss_castle(order['target']):
        # The boss tower has no own/enemy roof (g421 13:48: two cancels).
        # Accept it when a Y jump placed the marker on it, or when the marker
        # already stands on the measured cell: g1133 2026-11-05 had three jump
        # attempts end on stationary reads, and a jump-only rule left the base
        # impossible to confirm. The cell itself was verified against a
        # captured frame in v402.
        if (mem.get('y_jumped') or {}).get(order['step']) == order['target']:
            return True
        cell = chart.castles(mem.get('chapter') or 0).get(order['target'])
        world = update_world(screen, mem, frame, order['target'])
        return bool(cell and world
                    and abs(world[0] - cell[0]) <= BOSS_MARKER_CELL_PX
                    and abs(world[1] - cell[1]) <= BOSS_MARKER_CELL_PX)
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
    _forget_failed_jump(mem, step)
    (mem.get('y_jumped') or {}).pop(step, None)
    cancels = mem.setdefault('target_cancel', {})
    boss = chart.is_boss_castle(order['target'])
    if boss:
        # Boss cancels count per cell: the ones at a withdrawn cell say
        # nothing about the new one (g1075 2026-10-29: 3-B1 failed after four
        # cancels at the chapter 3 cave). See _boss_retry_due.
        cell = list(chart.castles(mem.get('chapter') or 0).get(order['target']) or ())
        cells = mem.setdefault('boss_cancel_cell', {})
        if cells.get(step) != cell:
            cancels.pop(step, None)
            cells[step] = cell
    cancels[step] = cancels.get(step, 0) + 1
    _flow(mem).pop('sortie_attempt', None)
    # The target marker was not verified; this does not prove the general
    # arrived or even departed. After cancelling the picker, read the named
    # general's X-menu status instead of repeating roof-only diagnostics.
    from . import arrival_status
    arrival_status.queue(mem, order, _sortie_general(order, mem))
    limit = BOSS_TARGET_CANCEL_LIMIT if boss else TARGET_CANCEL_LIMIT
    state = 'failed' if cancels[step] >= limit else 'pending'
    _finish_order(mem, state, target=order['target'],
                  deviation_reason='出撃先を屋根で確認できない',
                  observed_metric={'marker': list(_cursor(screen) or ()), 'cancels': cancels[step]},
                  reason='誤った場所へ出撃させないよう出撃を取り消す（将軍は城に残る）')
    return [pad('b')]
