"""手順層: field map procedures (map, target selection, world map).

Drives the cursor toward the strategy layer's next order, confirms the
target under the sortie marker and reads world-map ownership surveys.
"""
from __future__ import annotations
from copy import deepcopy

from ..os_layer.screen import Screen, castle_roofs
from ... import hanjuku_chart as chart
from ... import hanjuku_chart_adjust as chart_adjust
from ..decision_log import _record
from .deploy import (_cancel_broken_hero_sortie, _deploy_context, _deploy_input, _hold_deploy,
                            _hold_guarded_sortie, _hold_locked_boss, _hold_unrouted_sortie)
from .recall import (MENU_HOLD_LIMIT, RECALL_LIMIT, _hold_after_picker, _recall_dispatch_step, _request_recall,
                            camp_recall_step)
from ..os_layer.commands import pad
from ..os_layer.navigation import (Y_JUMP_LIMIT, Y_JUMP_OFFSET, _align_on_roof, _drop_stale_y_jump, _start_y_jump,
                                   _forget_failed_jump, _want_y_jump, _y_jump_step, nav_step)
from ..os_layer.readers import (UNDER_CURSOR_PX, WORLD_MAP_OFFSET, _cursor, _roof_under_cursor, world_cursor,
                                world_flags, world_markers)
from ..os_layer.world import NAME, _apply_world_flags, _hero_castle, _owned, update_world
from ..strategy.kit import _deploy_cards, _is_boss_order
from ..strategy.execution import departure_snapshot
from ..strategy.orders import (BOSS_TARGET_CANCEL_LIMIT, SOURCE_MISS_LIMIT, _broken_hero_order, _finish_order,
                               _garrison_move, _give_up_source, _near_goal, _order, _order_for_step, _ready,
                               _recall_goal, _retire_unentered_sortie, _sortie_general, _source,
                               next_order, parked_boss_steps)
from ..strategy.replan import _off_chart


def observe_sortie_transition(screen, mem, previous_kind=None):
    """An interrupted target cursor is a possible departure, never a retry.

    A hotloaded old policy has no attempt marker; its verified confirmation
    context and previous map_target observation provide the same evidence.
    """
    from .camp_recheck import observe_departure_return
    observe_departure_return(screen, mem)
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
    context = (mem.get('order_context') or {}).get(step) or {}
    general = context.get('actual_general')
    if not general:
        return
    snapshot = departure_snapshot(order, mem, general, _source(order, mem))
    if snapshot is None:
        return  # a foreign confirmation never becomes departure ownership
    general = snapshot['general']
    mem.setdefault('launched_orders', {})[step] = snapshot
    previous = (mem.get('sorties') or {}).get(step)
    if previous:
        _retire_unentered_sortie(mem, step, previous)
    mem.setdefault('sorties', {})[step] = {
        'general': general, 'source': snapshot['source'], 'target': None,
        'planned_target': order['target'], 'status': 'launched_unconfirmed',
        'evidence': deepcopy({k: v for k, v in context.items() if k != 'departure_order'}),
        'tick': int(mem.get('tick') or 0),
        **({'confirmation_tick': snapshot['departure_binding']['confirm_tick']}
           if 'departure_binding' in snapshot else {})}
    _finish_order(mem, 'launched_unconfirmed', general=general, target=None,
                  planned_target=order['target'], screen=screen.kind,
                  reason='目標決定前に出撃先選択を離れたため出撃の成否と行先を未確定として記録')
    attempt['interrupted'] = True
    mem.pop('expect_menu', None)
WORLD_SURVEY_TICKS = 200       # observations between surveys (~5 min at 1.5 s)
WORLD_MAP_WAIT = 4             # fade frames before giving up a reading
PICKER_AXIS_SWITCH = 2         # unchanged recall-picker readings before pressing the other axis
PICKER_STILL_LIMIT = 8         # recall-picker presses with an unchanged cursor before giving up
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
    if (not parked or mem.get('active') or mem.get('battle') or mem.get('attack')
            or mem.get('recall')):
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
    active = mem.get('active')
    if (not active or kind not in BOSS_SORTIE_MENU_KINDS or actions != []
            or mem.get('battle') or mem.get('attack') or mem.get('recall')):
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
        if recall['picker_observations'] >= RECALL_LIMIT:
            _record(mem, 'camp_recall_aborted',
                    observed_metric={'goal': recall.get('goal'), 'cursor': cursor},
                    reason='帰還先カーソルが有限回で合わず確定せず取り消す')
            mem.pop('recall', None)
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
            still = (int(recall.get('picker_still') or 0) + 1
                     if recall.get('picker_cursor') == list(cursor) else 0)
            recall.update(picker_cursor=list(cursor), picker_still=still)
            if still >= PICKER_AXIS_SWITCH and min(abs(dx), abs(dy)) >= 3:
                press = other
            _record(mem, 'camp_recall_picker_step',
                    observed_metric={'cursor': list(cursor), 'goal': goal,
                                     'offset': [round(dx, 1), round(dy, 1)], 'press': press,
                                     'still': still},
                    reason='帰還先の城へ全体マップのカーソルを1歩動かす')
            if still >= PICKER_STILL_LIMIT:
                _record(mem, 'camp_recall_aborted', resulting_event='picker_cursor_stationary',
                        observed_metric={'goal': goal, 'cursor': list(cursor), 'still': still},
                        reason='帰還先カーソルが入力しても動かないため取り消して通常の行動に戻る')
                mem.pop('recall', None)
                mem['uncertain'] = True
                mem['recall_skip'] = {'tick': int(mem.get('tick') or 0)}
                return [pad('b')]
            return [pad(press)]
        return _request_recall(mem, recall, goal,
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
        mem['active'] = None
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
        order = next((_order_for_step(mem, step) for step, sortie in (mem.get('sorties') or {}).items()
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
            return []           # nothing charted: let real time advance
        mem['active'] = order['step']
        mem['picked'] = []
        # The bounded inventory search belongs to this attempt, not the
        # order forever. A cancelled attempt must not exhaust every retry.
        (mem.get('card_scroll') or {}).pop(order['step'], None)
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
        _record(mem, 'order_start', chart_step=order['step'], **_deploy_context(order, mem),
                source=order['source'], target=order['target'], purpose=order.get('purpose'),
                cards=list(order['cards']),
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
    if screen.cursor and not screen.marker and _want_y_jump(mem, order, source, 'map'):
        return _start_y_jump(mem, order, source, 'map')
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
                mem['active'] = None
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
    locked = _hold_locked_boss(mem, order)
    if locked is not None:
        return locked
    if (_is_boss_order(order, mem) and _sortie_general(order, mem) == NAME
            and (mem.get('orders') or {}).get(order['step']) not in {'launched', 'launched_unconfirmed'}):
        mem.pop('sortie_attempt', None)
        return _hold_guarded_sortie(mem, order, 'boss_companion_required',
            '未送信の主人公のボス目標を取り消し、十分な戦力の代役を待つ',
            {'general': NAME, 'screen': screen.kind, 'hero_forbidden': True})
    if _broken_hero_order(order, mem):
        return _cancel_broken_hero_sortie(mem, order)
    if _is_boss_order(order, mem):
        context = (mem.get('order_context') or {}).get(order['step']) or {}
        if (not context.get('actual_general')
                or context.get('actual_general') != _sortie_general(order, mem)
                or (mem.get('sortie_general') or {}).get(order['step']) != context.get('actual_general')
                or (context.get('observed_metric') or {}).get('cards') != sorted(_deploy_cards(order, mem))):
            return _hold_deploy(screen, mem, order, 'ボス出撃の選択本人と携行品の確認証拠がないため目標確定を保留')
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
        mem.pop('sortie_attempt', None)
        (mem.get('target_miss') or {}).pop(order['step'], None)
        _finish_order(mem, 'launched', **context, target=order['target'],
                      cursor=mem.get('cursor'), anchor=mem.get('anchor'),
                      reason=f"{context['general']}を{order['target']}へ出撃")
        general = context['general']
        _garrison_move(mem, general, source=_source(order, mem))
        mem.setdefault('launched', {})[order['target']] = {'general': general, 'step': order['step']}
        # Snapshot: the battle, boss entry and retries of this sortie must not
        # depend on the plan still containing it.
        mem.setdefault('launched_orders', {})[order['step']] = snapshot
        # Per execution id: ``launched`` keeps one sortie per castle, so a later
        # sortie to the same castle must not unbind an earlier unit en route.
        previous = (mem.get('sorties') or {}).get(order['step'])
        if previous:
            _retire_unentered_sortie(mem, order['step'], previous)
        mem.setdefault('sorties', {})[order['step']] = {
            'general': general, 'source': snapshot['source'],
            'target': order['target'], 'status': 'en_route',
            'evidence': deepcopy({k: v for k, v in
                ((mem.get('order_context') or {}).get(order['step']) or {}).items()
                if k != 'departure_order'}),
            'tick': int(mem.get('tick') or 0),
            **({'confirmation_tick': snapshot['departure_binding']['confirm_tick']}
               if 'departure_binding' in snapshot else {})}
        ((mem.get('order_context') or {}).get(order['step']) or {}).pop('departure_order', None)
        from .camp_recheck import departure_requested
        departure_requested(mem, general, snapshot.get('departure_binding', {}).get('confirm_tick'))
        return [pad('a')]
    return _deploy_input(screen, mem, order, result or [], '出撃先へ目標カーソルを移動')


TARGET_MISS_LIMIT = 3          # unverified target arrivals before cancelling the sortie
TARGET_CANCEL_LIMIT = 2        # cancelled sorties per order before failing it


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
    mem.pop('sortie_attempt', None)
    limit = BOSS_TARGET_CANCEL_LIMIT if boss else TARGET_CANCEL_LIMIT
    state = 'failed' if cancels[step] >= limit else 'pending'
    _finish_order(mem, state, target=order['target'],
                  deviation_reason='出撃先を屋根で確認できない',
                  observed_metric={'marker': list(_cursor(screen) or ()), 'cancels': cancels[step]},
                  reason='誤った場所へ出撃させないよう出撃を取り消す（将軍は城に残る）')
    return [pad('b')]
