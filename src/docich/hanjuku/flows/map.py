"""手順層: field map procedures (map, target selection, world map).

Drives the cursor toward the strategy layer's next order, confirms the
target under the sortie marker and reads world-map ownership surveys.
"""
from __future__ import annotations
from copy import deepcopy

from ..os_layer import menu_feedback
from ..os_layer.pixels import Frame
from ..os_layer.screen import Screen, castle_roofs, own_camps
from ... import hanjuku_chart as chart
from ... import hanjuku_chart_adjust as chart_adjust
from ... import hanjuku_map_sources as sources
from ..decision_log import _record, _tally
from ..version import BOT_VERSION
from .deploy import (_cancel_broken_hero_sortie, _deploy_context, _deploy_input, _hold_deploy,
                            _hold_guarded_sortie, _hold_locked_boss, _hold_unrouted_sortie)
from ..strategy.bargains import forget_picked
from .recall import (MENU_HOLD_LIMIT, MOVE_MODE_STAGES, RECALL_LIMIT, _hold_after_picker, _recall_dispatch_step, _request_recall,
                    RELAY_MENU_WAIT, _relay_target, _y_route_step,
                    relay_from_picker,
                            camp_recall_step)
from ..os_layer.commands import (VISIBLE_LIST_RELEASE_MS, castle_list_batch, menu_to, pad,
                                 roster_list_batch)
from ..os_layer.navigation import (Y_JUMP_LIMIT, Y_JUMP_OFFSET, Y_JUMP_TOL, _align_on_roof, _drop_stale_y_jump, _start_y_jump,
                                   _forget_failed_jump, _want_y_jump, _y_jump_step, boss_probe_cell,
                                   forget_target_placement, nav_step, y_jump_unreachable)
from ..os_layer.readers import (UNDER_CURSOR_PX, WORLD_MAP_OFFSET, _canonical_general, _cursor, _options,
                                _roof_under_cursor, season_sites, world_cursor, world_flags,
                                world_markers)
from ..os_layer.world import (NAME, _apply_season_sites, _apply_skull_boss, _apply_world_flags,
                              _hero_castle, _owned, update_world)
from ..strategy import goals
from ..strategy.kit import _deploy_cards, _is_boss_order
from ..strategy.execution import departure_snapshot
from ..state.regions import world as _world, flow, journal, flow as _flow, journal as _journal, sorties as _sorties, plan as _plan_region
from ..strategy.orders import (BOSS_COMPANION_WAIT_TICKS, BOSS_TARGET_CANCEL_LIMIT, SOURCE_MISS_LIMIT,
                               _boss_companion_wait_expired, _broken_hero_order,
                               drop_retired_step_context, _finish_order,
                               _garrison_move, _give_up_source, _near_goal, _order, _order_for_step, _ready,
                               _recall_goal, _retire_unentered_sortie, _sortie_general, _source,
                               next_order, order_dispatch_starved, parked_boss_steps,
                               arm_sortie_launch_priority)
from ..strategy.replan import _off_chart
from .result import FlowResult


# docich#5020: a defense/boss battle that interrupts the destination picker
# closes it before the target is committed. Record that cause apart from every
# other picker close so the monthly audit can count interruptions and their
# same-month relaunch rate (4h live g1288 ch10: 6 of 9 sorties unconfirmed).
SORTIE_INTERRUPTING_BATTLE_KINDS = frozenset({
    'attack_started', 'defense_started', 'field_encounter_started',
    'boss_attack_started', 'battle', 'battle_intro'})


def observe_sortie_transition(screen, mem, previous_kind=None):
    """An interrupted target cursor is a possible departure, never a retry.

    A hotloaded old policy has no attempt marker; its verified confirmation
    context and previous map_target observation provide the same evidence.
    """
    from .camp_recheck import observe_departure_return
    observe_departure_return(screen, mem)
    # A recall destination picker belongs to the recall, not to an active
    # chart sortie's interrupted target selection.
    if (_flow(mem).get('recall') or {}).get('stage') in ('dest', 'await_dispatch',
                                                        'await_dispatch_stale'):
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
    own_target_jump = ((mem.get('y_jump') or {}).get('mode') == 'target'
                       or mem.get('y_jump_return') == 'target')
    if (screen.kind in SORTIE_INTERRUPTING_BATTLE_KINDS and order
            and order.get('step') == attempt.get('step')):
        # A fight cut the destination selection. The marker the picker restores
        # afterwards sits wherever the game left it, not the cell our Y jump
        # put it on, and a pre-battle arrival receipt still names the old cell.
        # Mark the attempt so the resumed picker re-does the target jump
        # (``target_step``) instead of confirming the restored marker, and give
        # that jump a fresh attempt budget: the interrupted one must not eat it.
        step = attempt.get('step')
        if not attempt.get('battle_interrupt'):
            attempt['battle_interrupt'] = True
            tick = mem.get('tick')
            attempt['battle_interrupt_tick'] = tick if type(tick) is int else None
            (mem.get('y_jumps') or {}).pop(f'{step}:target', None)
            _forget_failed_jump(mem, step, 'target')
        forget_target_placement(mem, step)
    if own_target_jump:
        # Our own Y jump for the target (the view and the fade back), not an
        # interruption. Captured before the battle reset above dropped
        # ``y_jump_return``: the reset is about placement, not about treating
        # our own view as a closed picker.
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
                # The remembered marker cell and the Y-view receipt are from
                # before the interruption too. g1163 2026-11-11 12:24:03 kept
                # オシロガ's cell after the month menu, read the resumed
                # marker as arrived, aligned it on a roof nearby and sent
                # シュゼット to a cliff where she camped. Forget both so the
                # target is found again by a fresh jump.
                forget_target_placement(mem, step)
                _record(mem, 'sortie_target_resumed', chart_step=step,
                        reason='割り込み後に同じ出撃先マーカーが戻ったため目標選択を再開')
        attempt['target_seen'] = True
        attempt.pop('interrupted', None)
        return
    if (attempt.get('interrupted') or not attempt.get('target_seen')
            or not order or order['step'] != attempt['step']):
        return
    step = order['step']
    # The target picker closed (a battle, event or a plain return): its cursor
    # and Y-jump receipt no longer describe any marker on screen. A re-armed
    # attempt must re-place the target instead of confirming the restored
    # marker at the old cell.
    forget_target_placement(mem, step)
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
        'aimed_cell': (list((chart.castles(mem.get('chapter') or 0) or {}).get(order['target']) or ()) or None),
        'evidence': deepcopy({k: v for k, v in context.items() if k != 'departure_order'}),
        'tick': int(mem.get('tick') or 0),
        **({'confirmation_tick': snapshot['departure_binding']['confirm_tick']}
           if 'departure_binding' in snapshot else {})}
    from .house import _sortie_state_sig, _tally_sortie_attempt
    # Issue #3326 (34): no journaled send before the picker closed = a cancel.
    _tally_sortie_attempt(mem, order['target'], order.get('purpose'),
                          'cancelled_unsent',
                          _sortie_state_sig(mem, snapshot['source'], general))
    if screen.kind in SORTIE_INTERRUPTING_BATTLE_KINDS:
        # The picker closed into a battle, not into a plain field frame: the
        # generic unconfirmed record below cannot tell the two apart, so the
        # monthly decision breakdown counts this cause on its own (docich#5020).
        _record(mem, 'launch_interrupted_by_battle', chart_step=step,
                general=general, target=order['target'], screen=screen.kind,
                observed_metric={'planned_target': order['target'],
                                 'target': order['target'],
                                 'source': snapshot['source'],
                                 'screen': screen.kind},
                reason='出撃先ピッカーの最中に戦闘が割り込み目標決定前に閉じたため、割り込みからの再開率を測る')
    _finish_order(mem, 'launched_unconfirmed', general=general, target=None,
                  planned_target=order['target'], screen=screen.kind,
                  reason='目標決定前に出撃先選択を離れたため出撃の成否と行先を未確定として記録')
    attempt['interrupted'] = True
    mem.pop('expect_menu', None)
WORLD_SURVEY_TICKS = 200       # observations between surveys (~5 min at 1.5 s)
# Owner 2026-11-21: the ch9 boss runs a fixed track; to place generals on it
# the trace has to be dense. While a boss wave is out, survey the whole-island
# view far more often so boss_cell_track records the lap instead of a handful
# of points. The ordinary 200-tick survey stays for everything else.
BOSS_TRACK_SURVEY_TICKS = 40
WORLD_MAP_SKULL_HOLD_LIMIT = 4  # observations a chapter-9 survey keeps the skull view open
WORLD_MAP_ANCHOR_SETTLED_PX = 64  # Y-view world position this close to the field cursor = settled frame
WORLD_MAP_WAIT = 4             # fade frames before giving up a reading
WORLD_MAP_CLOSE_LIMIT = 3      # Y presses before the view is known not to close with Y
PICKER_AXIS_SWITCH = 2         # unchanged recall-picker readings before pressing the other axis
PICKER_STILL_LIMIT = 8         # recall-picker presses with an unchanged cursor before giving up
PICKER_VISIT_SLOTS = 12        # distinct cursor positions remembered per recall
PICKER_LOOP_LIMIT = 3          # visits of one position before giving up
PICKER_LOOP_MIN_STEP = 8       # px a step must move before it counts as a visit
PICKER_NO_GOAL_LIMIT = 30      # aimless picker observations before confirming where it stands
SELECT_FOCUS_INTERVAL = 300    # observations between hero re-focuses (~7.5 min)
MAP_BLIND_ADVANCE = 12         # cursorless/HUD-less map reads before pressing A (#3721)


def survey_interval(mem) -> int:
    """Observations between whole-island surveys.

    Chapter 9's moving boss needs a dense skull trace while a wave is out;
    every other chapter keeps the ordinary cadence.
    """
    if (mem.get('chapter') or 0) == 9 and mem.get('boss_wave_generals'):
        return BOSS_TRACK_SURVEY_TICKS
    return WORLD_SURVEY_TICKS


def _world_map_wanted(mem) -> bool:
    if (mem.get('chapter') or 0) not in WORLD_MAP_OFFSET:
        return False
    last = mem.get('world_map_tick')
    tick = int(mem.get('tick') or 0)
    return (last is None or bool(mem.get('world_map_due'))
            or tick - int(last) >= survey_interval(mem))


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
    if (not parked or _plan_region(mem).get('active') or mem.get('battle') or _plan_region(mem).get('attack')
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
            or mem.get('battle') or _plan_region(mem).get('attack') or _flow(mem).get('recall')):
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


RECALL_GOAL_FAILURE_LIMIT = 4   # repeats at one camp before the month is not spent


def _recall_exhausted(mem, recall):
    """Every own castle has already refused this camp's recall several times.

    g1190 2026-11-17: camp (134,120) sent 117 recalls at アルマムーン and never
    arrived; the general stood in the back half with no own castle there, so no
    destination could work. Retrying every month only spends the month's action.
    """
    camp = (recall or {}).get('target') if isinstance(recall, dict) else None
    if not (isinstance(camp, (list, tuple)) and len(camp) == 2):
        return False
    failures = (_flow(mem).get('recall_goal_failures') or {})
    pair = failures.get(f'{int(camp[0])},{int(camp[1])}') if isinstance(failures, dict) else None
    if not isinstance(pair, dict) or not pair:
        return False
    owned = [c for c in _owned(mem) if c in (chart.castles(mem.get('chapter') or 0) or {})]
    if not owned:
        return False
    return all(int(pair.get(c) or 0) >= RECALL_GOAL_FAILURE_LIMIT for c in owned)


def _goal_after_failures(mem, flags, recall, goal):
    """A destination that never accepted this camp's recall is replaced.

    ``camp_recall_goal_failed`` counts (camp, castle) pairs whose A was sent and
    never arrived. Keep the nearest castle that has not failed yet, so a silent
    failure stops repeating every month (g1190 2026-11-17: リーキ -> アルマムーン
    three times, the general still camped).
    """
    if not isinstance(goal, str):
        return goal
    camp = (recall or {}).get('target') if isinstance(recall, dict) else None
    if not (isinstance(camp, (list, tuple)) and len(camp) == 2):
        return goal
    failures = (_flow(mem).get('recall_goal_failures') or {})
    pair = failures.get(f'{int(camp[0])},{int(camp[1])}') if isinstance(failures, dict) else None
    if not isinstance(pair, dict) or goal not in pair:
        return goal
    chapter = mem.get('chapter') or 0
    cells = chart.castles(chapter) or {}
    # Reuse the same reachable-side decision as the initial recall goal.
    # Flag readings can omit owned castles; the strategy helper includes
    # capture history and preserves the shared gateway hub.
    from ..strategy.orders import _recall_owned
    # Use every captured castle, not only this frame's flag readings: g1190
    # 2026-11-17 22:0x flagged just アルマムーン while マランマ/ゴンダル/チリベット
    # were owned, so the replacement list was empty and the same silent
    # failure repeated five times.
    owned = _recall_owned(
        mem, chapter,
        sorted(c for c in _owned(mem) if c in cells),
        origin=recall.get('origin') if isinstance(recall, dict) else None)
    fresh = [c for c in owned if c not in pair]
    if not fresh:
        return goal
    return min(fresh, key=lambda c: (
        (cells[c][0] - cells[goal][0]) ** 2 + (cells[c][1] - cells[goal][1]) ** 2, c))


def _picker_goal(mem, flags, recall):
    """The destination chosen for THIS picker episode, or a fresh choice.

    ``_recall_goal`` answers from the recall's origin, its sorties and the
    hero's measured castle, and any of those can move while the ring is being
    steered. Re-deriving it every observation made the ring chase a goal that
    flipped between two own castles (g1190 2026-11-17 20:14-20:16: タタール →
    ジャンセニア → タタール within five observations, so every press looked
    aimless). Once this episode selected a destination it keeps it while the
    castle is still ours.
    """
    state = recall if isinstance(recall, dict) else {}
    bound = state.get('picker_goal')
    if (isinstance(bound, str) and state.get('destination_selected')
            and (not isinstance(flags, dict) or flags.get(bound) == 'own')):
        return bound
    return _recall_goal(mem, flags)


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


def _recall_picker_cycles(chapter, flags, nearby, goal):
    """Measured chapter-11 R picker: Up/Down cycle the owned castles.

    Left/Right do nothing here, even when the next castle is horizontally
    beside the ring. A one-way scan avoids assuming a spatial neighbour or
    an internal castle ordering; every next frame still proves the selection.
    Require the complete current flag reading, not remembered ownership.
    """
    if chapter != 11 or not isinstance(flags, dict) or len(nearby) != 1:
        return False
    castles = {name for name in chart.castles(chapter) if not chart.is_boss_castle(name)}
    return (goal in castles and nearby[0] in castles
            and flags.get(goal) == flags.get(nearby[0]) == 'own'
            and all(flags.get(name) in ('own', 'enemy') for name in castles))


def _picker_y_route(screen, mem, recall, goal):
    """Delegate the open recall picker to the whole-map Y route (docich#4755).

    DSH 方針(1): when the destination goal and the camp's own world cell are
    both measured, ``_y_route_step`` — the same step the hero_focus y_route
    uses — jumps this picker's cursor onto the goal instead of the ring being
    walked castle by castle.  The layer stays additive: it spends the
    ``RECALL:target`` jump budget the map_target route uses (recall.py:1516),
    never starts while another jump is in flight, and returns ``None`` so the
    ordinary picker walk keeps the screen whenever it cannot act.
    """
    # The bare map_target marker may use the measured origin; the actual
    # recall picker (world_map) delegates only with a measured camp_world.
    # The native picker/receipt tests have origin but no camp_world and must
    # keep their ring walk.
    if screen.kind == 'map_target':
        camp_world = recall.get('camp_world') or recall.get('origin')
    else:
        camp_world = recall.get('camp_world')
        if not camp_world:
            return None
    if not (isinstance(goal, str) and goal
            and isinstance(camp_world, (list, tuple)) and len(camp_world) == 2
            and all(type(axis) in (int, float) for axis in camp_world)):
        return None
    if (recall.get('boss') or recall.get('boss_move') or recall.get('gather')
            or recall.get('relay') or recall.get('move_visible')):
        return None                     # dedicated routes own these recalls
    if _relay_target(mem, recall) is not None:
        # The camp sits across the cliff: jumping the marker at the goal would
        # send the march into the wall the relay leg exists to avoid.
        return None
    if ((mem.get('y_jumps') or {}).get('RECALL:target', 0) >= 2
            or mem.get('y_jump') or mem.get('castle_jump')):
        return None
    recall['goal'] = goal
    stepped = _y_route_step(screen, mem, recall)
    if not stepped:
        return None
    _record(mem, 'camp_recall_picker_y_route',
            observed_metric={'goal': goal, 'camp_world': list(camp_world)},
            reason='帰還先と野営の世界座標が計測済みのため、ピッカー歩行ではなく全体マップYジャンプ経路へ委譲する')
    return stepped


UNVERIFIED_LAUNCH_LIMIT = 3   # Y-view observations without the unit before rearm


def _settle_unverified_launches(mem, markers):
    """Return a launch with no visible unit and no march record to pending.

    A sent A plus a closed picker is not proof the unit left (g1097), and the
    per-sortie ``launch_probe`` lives in the procedure region, so it is gone
    once the deploy ends (g1190 2026-11-19 run 5: three ``launched`` castles
    for five months while the Y view showed a single unit — #4626). The world
    map is the measured ground truth: when fewer units are drawn than there
    are launches, a launch without a march record misses again, and after
    UNVERIFIED_LAUNCH_LIMIT such views it goes back to pending so the plan can
    pick it again next month.
    """
    launched = _world(mem).get('launched_orders') or {}
    if not launched:
        mem.pop('launch_unverified', None)
        return
    if not markers:
        return          # an unread world map proves nothing
    sorties = _sorties(mem)
    watch = mem.get('launch_unverified')
    watch = watch if isinstance(watch, dict) else {}
    if len(markers) >= len(launched):
        mem['launch_unverified'] = {}
        return
    changed = False
    for step in list(launched):
        if step in sorties:
            watch.pop(step, None)
            continue
        watch[step] = int(watch.get(step) or 0) + 1
        if watch[step] < UNVERIFIED_LAUNCH_LIMIT:
            continue
        watch.pop(step, None)
        launched.pop(step, None)
        status = _plan_region(mem).setdefault('orders', {})
        if status.get(step) not in ('failed',):
            status[step] = 'pending'
        _record(mem, 'sortie_unverified_rearmed', chart_step=step,
                observed_metric={'units': len(markers), 'launched': len(launched) + 1,
                                 'limit': UNVERIFIED_LAUNCH_LIMIT},
                reason='全城マップにユニットが見えず行軍記録も無いままの出撃を指示待ちへ戻し、次月に選び直す')
        changed = True
    if watch:
        mem['launch_unverified'] = watch
    else:
        mem.pop('launch_unverified', None)

def _map_y_route_give_up(mem, recall, chapter, nearby, reason):
    """docich#5105: a blind いどう→Y view that cannot name its destination.

    The owner policy (2026-11-19: 「きかん禁止・いどう→Y」) never falls back to
    the ring picker's confirm: reserve a roster read of the capital and back
    out so the camp can be re-measured.
    """
    home = chart.home_castle(chapter)
    if home and home not in (mem.get('roster_read_due') or {}):
        mem.setdefault('roster_read_due', {})[home] = (
            int(mem.get('tick') or 0) + 180)
    _record(mem, 'camp_recall_goal_unresolved',
            observed_metric={'camp': list(recall.get('target') or ()),
                             'origin': recall.get('origin'),
                             'nearby': nearby},
            reason=reason)
    _flow(mem).pop('recall', None)
    mem['uncertain'] = True
    from .recall import _arm_recall_skip as _arm_skip
    _arm_skip(mem, recall)
    return [pad('b')]


def world_map_step(screen, mem, frame):
    """Read every castle's owner from the Y view, then close it with Y
    (or, while a Y jump is running, steer its cursor to the goal castle)."""
    mem.pop('_menu_navigation_feedback', None)
    if not mem.get('chapter'):
        # g1190 2026-11-16: a chapter transition (or a restored memory) can
        # leave the chapter unset, and the field frames never carry the
        # chapter header again. The Y view names every castle, so a name that
        # exists in one chapter only restores it (the measured recovery path
        # in strategy/campaign.py). Without this the chapter-9 corridor flows
        # never ran and the bot stayed on the generic walk.
        from ..strategy.campaign import observe_chapter_castle
        owners = mem.get('world_map_flags')
        if isinstance(owners, dict):
            for name in list(owners)[:12]:
                observe_chapter_castle(mem, name)
                if mem.get('chapter'):
                    break
    recall = _flow(mem).get('recall')
    if (isinstance(recall, dict) and recall.get('battle_return')
            and recall.get('stage') in MOVE_MODE_STAGES):
        # docich#5452: a whole map after combat cannot be told apart from the
        # view a dead いどう mode would leave — an A there opens the castle
        # menu instead of confirming the destination. Drop the recall's own
        # jump and restart the return from the camp.
        from .recall import _recall_move_mode_lost
        own_jump = (mem.get('y_jump') or {}).get('step') == 'RECALL'
        if own_jump:
            mem.pop('y_jump', None)
        actions = _recall_move_mode_lost(mem, recall, screen)
        # The closing B only belongs on a view the recall itself could own;
        # a foreign flow's jump keeps its screen.
        return actions if own_jump or not mem.get('y_jump') else []
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
    if isinstance(recall, dict):
        recall.pop('hold_since', None)      # docich#5108: the view is operable again
    if (isinstance(recall, dict) and recall.get('relay')
            and recall.get('stage') == 'menu' and screen.kind == 'world_map'):
        # relay_from_picker re-aimed this camp at the relay cell and sent the B
        # that closes the picker. The picker screen stays for an observation or
        # two and this function owns it; without a hold the stage-is-dest
        # branch below is skipped and the generic world_map path drops the
        # recall flow (live 2026-11-18 09:53: stage menu+relay, then task done).
        waits = int(recall.get('relay_menu_waits') or 0) + 1
        recall['relay_menu_waits'] = waits
        if waits <= RELAY_MENU_WAIT:
            return []
        recall['relay_menu_waits'] = 0
    if recall and recall.get('stage') in ('await_dispatch', 'await_dispatch_stale'):
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
        if recall['picker_observations'] % 10 == 0:
            hero_known = sorted(c for c, names in (_world(mem).get('garrison') or {}).items()
                                if NAME in (names or ()))
            hero_row = ((mem.get('general_statuses') or {}).get('statuses') or {}).get(NAME)
            hero_status = ({'location': hero_row.get('location'),
                            'observed': hero_row.get('location_observed') is True}
                           if isinstance(hero_row, dict) else None)
            start = mem.get('month_start_tick')
            elapsed = (int(mem.get('tick') or 0) - start
                       if type(start) is int else None)
            usable = [int(d) for d in (mem.get('month_durations') or [])[-3:]
                      if type(d) is int]
            _record(mem, 'camp_recall_picker_progress',
                    observed_metric={'observations': recall['picker_observations'],
                                     **({'month_elapsed': elapsed}
                                        if elapsed is not None else {}),
                                     **({'month_estimate': max(usable)} if usable else {}),
                                     'cursor': list(cursor) if cursor else None,
                                     'goal': recall.get('goal'),
                                     'nearby': nearby,
                                     'own': sorted(c for c, owner in (flags or {}).items()
                                                   if owner == 'own'),
                                     'hero': hero_known,
                                     **({'hero_status': hero_status} if hero_status else {})},
                    reason='帰還先ピッカーの進捗を10回ごとに記録する')
        if recall['picker_observations'] >= RECALL_LIMIT:
            if recall.get('map_y_route'):
                # A blind いどう→Y view that stayed unreadable for the whole
                # budget must not take the ring fallback's blind confirm.
                return _map_y_route_give_up(
                    mem, recall, chapter, nearby,
                    '全体マップの旗が観測上限まで読めないため、首都在城名簿の再読を予約して帰還を見送る')
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
        if relay_from_picker(mem, recall) is not None:
            # The picker just proved the camp stands in the back half; the
            # きかん it opened would confirm a castle across the cliff.
            return [pad('b')]
        # A boss-phase gather names its castle explicitly (owner 2026-11-15:
        # every general must gather at the last castle before the boss); never
        # replace it with the nearest-castle rule.
        if _recall_exhausted(mem, recall):
            from .recall import _arm_recall_skip as _arm_skip
            _record(mem, 'camp_recall_given_up',
                    observed_metric={'camp': list(recall.get('target') or ()),
                                     'failures': dict((_flow(mem).get('recall_goal_failures') or {})
                                                      .get(','.join(str(int(v))
                                                                    for v in recall['target'])) or {})},
                    reason='全ての自軍城への帰還が繰り返し未確認のため、この野営への帰還を一定期間見送る')
            _flow(mem).pop('recall', None)
            _arm_skip(mem, recall)
            return [pad('b')]
        goal = recall.get('gather') or _picker_goal(mem, flags, recall)
        goal = _goal_after_failures(mem, flags, recall, goal)
        if recall.get('map_y_route'):
            # docich#5105: いどう's field cursor opened this view blind so its
            # own-castle flags could name the destination. Bind it here and
            # hand off to the measured Y route — the きかん ring walk below is
            # never used for this recall.
            if not flags:
                return []                    # the view is still fading in
            if goal:
                recall['goal'] = goal
                recall.pop('map_y_route', None)
                recall['stage'], recall['steps'] = 'y_route', 0
                _record(mem, 'camp_recall_y_route',
                        observed_metric={'goal': goal},
                        reason='全体マップの自軍城旗で帰還先を確定し、いどう→Yジャンプ経路へ委譲する')
                return _y_route_step(screen, mem, recall)
            # No reachable own castle even with the island survey: never fall
            # back to きかん — reserve a roster read of the capital and back
            # out so the camp can be re-measured (owner 2026-11-19).
            return _map_y_route_give_up(
                mem, recall, chapter, nearby,
                '全体マップでも帰還先の自軍城を決められないため、首都在城名簿の再読を予約して帰還を見送る')
        delegated = _picker_y_route(screen, mem, recall, goal)
        if delegated is not None:
            return delegated
        if goal is None:
            # The picker cannot aim and the hero's castle is the missing
            # position (g1163 2026-11-14 08:59/09:01, statuses null in ch.9).
            # Read the capital's own list so the next picker knows it.
            home = chart.home_castle(chapter)
            if home and home not in (mem.get('roster_read_due') or {}):
                mem.setdefault('roster_read_due', {})[home] = (
                    int(mem.get('tick') or 0) + 180)
            # An aimless picker used to press right until the whole budget ran
            # out. The ring starts on the home castle and snaps castle to
            # castle, so an own castle under it is a destination the budget
            # fallback would confirm anyway — naming it now keeps the recall
            # inside the month it started in. g1163 2026-11-14 10:14-10:16:
            # 34 picker steps with goal null were cut by the month scene and
            # the camp's generals stayed in the field; the same stall at
            # 10:47 spent 50 observations before the origin bound.
            under = next((name for name in nearby
                          if (flags or {}).get(name) == 'own'), None)
            if under is not None and recall['picker_observations'] < 2:
                # The ring already stands on the castle: one observation to let
                # the frame settle, then confirm. Pressing right here would walk
                # the ring off it and leave the cursor between castles.
                return []
            if under is not None:
                recall.update(goal=under, destination_selected=True,
                              goal_from_cursor=True)
                _record(mem, 'camp_recall_destination',
                        observed_metric={'castle': under,
                                         'origin': recall.get('origin'),
                                         'origin_source': 'picker_cursor',
                                         'picker_boxes': ([list(b) for b in world_markers(frame)]
                                                          if frame is not None else []),
                                         'hero_castle': _hero_castle(mem)},
                        reason='帰還先を決める根拠がないため、ピッカーのカーソルが指す自軍城で確定する')
                return _request_recall(mem, recall, under,
                                       [pad('a'), {'type': 'wait', 'ms': 700}, pad('a')])
            if (recall['picker_observations'] >= PICKER_NO_GOAL_LIMIT
                    and not _picker_on_enemy(nearby, flags)):
                return _picker_fallback(mem, recall, nearby, cursor,
                                        '帰還先を決める根拠がないまま観測を使い切るため、今いる城で確定する')
        if not cursor or not offset or goal is None:
            return [pad('right')] if cursor else []
        if recall.get('goal') != goal or not recall.get('destination_selected'):
            recall.update(goal=goal, destination_selected=True, picker_goal=goal)
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
            # docich#4700: the cursor can keep moving without getting closer —
            # ch3 15-x logged 62 picker steps whose remaining offset never fell
            # below 76 while 38 of them pressed left, so the recall spent
            # minutes walking the ring and the month scene cut it. Track the
            # best (smallest) offset reached; once several presses fail to
            # improve it, stop steering and let the existing confirm-anywhere
            # fallback settle the recall.
            here_offset = abs(dx) + abs(dy)
            reached = recall.get('picker_best_offset')
            if type(reached) is not int or here_offset < reached:
                recall['picker_best_offset'] = here_offset
                recall['picker_no_progress'] = 0
            else:
                recall['picker_no_progress'] = int(recall.get('picker_no_progress') or 0) + 1
                if (recall['picker_no_progress'] >= PICKER_NO_PROGRESS_LIMIT
                        and not _picker_on_enemy(nearby, flags)):
                    return _picker_fallback(
                        mem, recall, nearby, cursor,
                        'カーソルが目標に近づかないまま押し続けているため、今いる城で確定する')
            across = 'right' if dx > 0 else 'left'
            along = 'down' if dy > 0 else 'up'
            press, other = (across, along) if abs(dx) >= abs(dy) else (along, across)
            cycle_picker = _recall_picker_cycles(chapter, flags, nearby, goal)
            if cycle_picker:
                press = 'down'
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
            if not cycle_picker and still >= PICKER_AXIS_SWITCH:
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
                                     'still': still, 'loops': loops,
                                     'navigation': 'own_castle_cycle' if cycle_picker else 'geometry'},
                    reason=('帰還先の実ピッカーは上下で自軍城を巡回するため、下へ1回動かして次の城を読み直す'
                            if cycle_picker else '帰還先の城へ全体マップのカーソルを1歩動かす'))
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
            # One 100 ms tap per observation walked the ring across the island
            # a pixel at a time (owner 2026-11-18: 帰還のカーソル移動が遅すぎて
            # 進行を阻害している). Hold proportionally to what is left; the next
            # observation re-reads the cursor and corrects any overshoot.
            if cycle_picker:
                # Each tap selects a whole castle; holding can skip the goal.
                # Cadence only: the next actual ring still proves the castle.
                mem['_menu_navigation_feedback'] = {
                    'kind': 'world_map', 'menu': 'recall_castles',
                    'cursor': [round(value) for value in cursor], 'directions': ['down']}
                return [pad(press)]
            span = abs(dx) if press in ('left', 'right') else abs(dy)
            return [pad(press, _picker_hold_frames(span))]
        return _request_recall(mem, recall, goal,
                               [pad('a'), {'type': 'wait', 'ms': 700}, pad('a')])
    flags = world_flags(frame, mem.get('chapter') or 0)
    if not flags:
        waited = int(mem.get('world_map_wait') or 0) + 1
        mem['world_map_wait'] = waited
        return [] if waited < WORLD_MAP_WAIT else _close_world_map(mem)
    mem.pop('world_map_wait', None)
    _apply_world_flags(mem, flags)
    # The Y view holds the player's own position (the gold G cursor). When the
    # field estimate is uncertain (a restored state, a missed roof) this is
    # the authoritative re-anchor: world = (Y cursor - offset) * 8, the same
    # relation the recall picker uses. g1190 2026-11-16: the field roofs could
    # not be read at all, the uncertain flag never cleared and every sortie
    # destination was aimed off-screen until this anchor was taken.
    y_cursor = world_cursor(frame) if hasattr(frame, 'pixel') else None
    y_offset = WORLD_MAP_OFFSET.get(mem.get('chapter') or 0)
    settled = False
    if y_cursor and y_offset:
        previous_cursor = mem.get('cursor')
        world = [int(round((y_cursor[0] - y_offset[0]) * 8)),
                 int(round((y_cursor[1] - y_offset[1]) * 8))]
        # The first Y frame can still be the camera transition: its G cursor
        # is the old field position rather than the settled island view. The
        # field cursor the map flow already holds is the reference that tells
        # the two apart (g1288 2026-11-20: transition [838,568] vs field
        # [518,436], then the next frame matched exactly).
        if (isinstance(previous_cursor, (list, tuple)) and len(previous_cursor) == 2
                and all(type(v) in (int, float) for v in previous_cursor)):
            settled = (abs(world[0] - previous_cursor[0]) <= WORLD_MAP_ANCHOR_SETTLED_PX
                       and abs(world[1] - previous_cursor[1]) <= WORLD_MAP_ANCHOR_SETTLED_PX)
        mem['cursor'] = world
        mem['uncertain'] = False
        mem.pop('anchor', None)
        mem.pop('y_anchor_attempts', None)
        _record(mem, 'world_anchor_from_yview',
                observed_metric={'cursor': list(y_cursor),
                                 'world': list(mem['cursor'])},
                reason='全体マップの自位置カーソルからフィールド位置を再確定する')
    # The same view draws chapter 4's 四季王 sites: a live king keeps the
    # skull, a defeated one draws none. Pass the richer reading so the writer
    # can tell a partial/hidden marker from clean terrain (and from a frame
    # whose other sites no longer read normally).
    if (mem.get('chapter') or 0) == 4 and hasattr(frame, 'pixel'):
        _apply_season_sites(mem, season_sites(frame, 4, world_cursor(frame), True))
    if (mem.get('chapter') or 0) != 4 and hasattr(frame, 'pixel'):
        # A single boss skull on the Y view is the boss's measured cell (the
        # moving boss included; chapter 4's four sites use the scan above).
        _apply_skull_boss(mem, frame)
    markers = world_markers(frame)
    _probe_launch_markers(mem, markers)
    _settle_unverified_launches(mem, markers)
    mem['_world_units_prev'] = [list(b) for b in markers]
    if markers:
        _record(mem, 'world_map_units', observed_metric={'count': len(markers),
                'boxes': [list(m) for m in markers]},
                reason='全体マップの▲（部隊マーカー）を定期確認')
    # A resumed/already-open Y view may have no opening timestamp. Its
    # successful survey must not be immediately repeated on the next map.
    # Keep explicit world_map_due requests and all unread/picker paths intact.
    expected = {name for name in chart.castles(mem.get('chapter') or 0)
                if not chart.is_boss_castle(name)}
    if expected and expected <= flags.keys() and not mem.get('world_map_pending'):
        mem['world_map_tick'] = int(mem.get('tick') or 0)
    # Chapter 9's moving boss draws chapter 3's skull with red pupils. One
    # reading only starts the streak (BOSS_SKULL_READINGS=2), so a survey
    # that closes here can never confirm a boss that moves between Y visits.
    # The first Y frame can still be the camera transition: its skull cell is
    # read from the old view. A settled frame is accepted directly below; when
    # the frame is not settled, keep the view open for a bounded number of
    # readings until two consecutive frames agree. The old one-observation
    # hold closed the view on the changed second cell every time (live g1288
    # 2026-11-20: 12 surveys, every one ended after one hold with count 1, and
    # the last cell aged out; no pursuit could start).
    streak = mem.get('boss_cell_streak') or {}
    hold = mem.get('world_map_skull_hold') or {}
    now = int(mem.get('tick') or 0)
    # A settled frame is already authoritative: the boss moved less than one
    # observation since the skull was drawn, so the cell is as current as the
    # two-reading rule was. The survey cannot always supply a second readable
    # frame (the Y view closes on its own, live g1288 2026-11-20: the first
    # frame was settled but the next observation was already the closing
    # fade), so accept this one rather than losing the whole survey.
    cell = streak.get('cell')
    if ((mem.get('chapter') or 0) == 9 and settled and streak.get('count') == 1
            and isinstance(cell, (list, tuple)) and len(cell) == 2
            and all(type(v) is int for v in cell)):
        mem['boss_cell'] = {'chapter': 9, 'cell': list(cell),
                            'tick': now, 'readings': 1}
        mem.pop('world_map_skull_hold', None)
        _record(mem, 'boss_cell_measured', observed_metric={
                    'chapter': 9, 'cell': list(cell), 'readings': 1, 'settled': True},
                resulting_event=f'boss_cell:{cell[0]},{cell[1]}',
                reason='全島マップの遷移が終わったフレームでドクロ位置を実測した')
        return _close_world_map(mem)
    recent = (isinstance(hold, dict) and 0 <= now - int(hold.get('tick') or 0) <= 3)
    reads = int(hold.get('reads') or 0) if recent else 0
    if ((mem.get('chapter') or 0) == 9 and streak.get('count') == 1
            and reads < WORLD_MAP_SKULL_HOLD_LIMIT):
        mem['world_map_skull_hold'] = {'tick': now, 'reads': reads + 1,
                                       'cell': streak.get('cell')}
        _record(mem, 'world_map_skull_hold',
                observed_metric={'cell': streak.get('cell'), 'count': streak.get('count'),
                                 'reads': reads + 1},
                reason='第9話の移動ボスを同じ全島マップでもう1観測し、遷移フレームを挟んでもドクロ位置の2回一致を成立させる')
        return []
    mem.pop('world_map_skull_hold', None)
    return _close_world_map(mem)


PICKER_NO_PROGRESS_LIMIT = 6   # presses without a smaller offset before settling


def _picker_hold_frames(span):
    """Frames to hold for the recall picker's cursor, by remaining distance.

    The old one-tap step moved a view pixel at a time; a far castle took many
    observations and the month scene cut the recall. Far moves use two frames
    per pixel (the Y-jump's fast scale) and the last stretch uses one, so the
    next read can settle on the castle.
    """
    size = abs(span)
    if size <= 24:
        return max(1, min(16, round(size)))
    return min(56, max(1, round(size * 2)))


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
                             'observations': recall.get('picker_observations'),
                             'nearby': list(nearby)},
            reason=reason)
    # A destination is still required for the arrival read and the dispatch
    # receipt: with no nearby castle and no goal, name the capital rather than
    # leaving the recall unnamed (g1163 2026-11-14 10:12: goal null, nearby []).
    goal = castle or recall.get('goal') or chart.home_castle(mem.get('chapter') or 0)
    return _request_recall(mem, recall, goal,
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
        # This may have been an orphan destination picker, not a survey.
        # Do not let its flag reading defer the next ordinary Y recheck.
        mem['world_map_due'] = True
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
    goal = chart.aim_cell(mem.get('chapter') or 0, _source(order, mem), castle)
    if goal is None:
        _plan_region(mem)['active'] = None
        forget_picked(mem)
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
    """Whether a proven start of this order is still inside its backoff.

    The check itself must not consume the budget: an interruption can cut the
    attempt before the castle menu is ever reached (g1190 2026-11-19 run 5 —
    #4622), and stamping here would hold the first real retry for no reason.
    """
    now = int(mem.get('tick') or 0)
    started = mem.get('order_start_tick') or {}
    last = started.get(step)
    if last is not None and now - int(last) < ORDER_START_BACKOFF:
        # Counted, not recorded: a held frame that never reaches the plan is
        # invisible today (g1190 2026-11-19 run 5: 66 map plans with no
        # decision and no order_start — #4622).
        _tally(mem, 'order_start_backoff')
        return False
    return True


def _mark_order_started(mem, step):
    """Record that the order reached a concrete castle-opening action."""
    if not step:
        return
    started = mem.setdefault('order_start_tick', {})
    started[step] = int(mem.get('tick') or 0)
    for stale in list(started)[:-40]:      # del on the temporary list is a no-op
        started.pop(stale, None)


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
    (mem.get('y_jumped_res') or {}).pop(step, None)
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
    launched = _world(mem).get('launched_orders') or {}
    status = (_plan_region(mem).get('orders') or {}).get(step)
    if step in launched or status is not None:
        return False
    from ..strategy import corridor
    if corridor.mechanical(mem) and corridor._pair(step) is not None:
        # Chapter 9's march rebuilds its order list on every call, so an
        # adopted ``chart_plan`` never owns its steps: the corridor itself is
        # the plan, and a step it still mints is live. g1270 (#4498) released
        # every unlaunched march order on the next map frame, and the release
        # + order-start backoff cycle (~13 obs per map step) was the measured
        # 150-261 obs stall between sorties from different castles.
        live = {o.get('step') for o in corridor.orders(mem)}
        if step in live or _plan_region(mem).get('active') != step:
            return False
        _plan_region(mem)['active'] = None
        forget_picked(mem)
        _drop_jump_state(mem, step)
        _record(mem, 'stale_active_released', chart_step=step,
                observed_metric={'live_steps': sorted(live)[:8]},
                reason='第9話の進軍計画がこの指示をもう発行していないため解放し、現在の計画から選び直す')
        return True
    plan = _plan_region(mem).get('chart_plan') or {}
    plan_steps = {o.get('step') for o in plan.get('orders') or ()}
    if not plan_steps or step in plan_steps:
        return False
    _plan_region(mem)['active'] = None
    forget_picked(mem)
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
    # Prefer the Y view's own-position cursor before the castle-list jump: it
    # anchors the field cell directly, while the X reanchor needs アルマムーン
    # to be on the visible list and looped when it was not (g1190 2026-11-19
    # run 3/4: cursor_still_search repeated and no sortie ever started — #4611).
    # docich#4711: the per-episode budget resets whenever the stillness
    # episode ends, so a cursor the Y view never reads churned 66 re-anchors in
    # ten minutes (ch6 19-x). A chapter-wide total stops that: after a few
    # fruitless opens the field falls back to the castle-list search instead
    # of re-opening the same unreadable view.
    total = int(mem.get('y_anchor_total') or 0)
    attempts = int(mem.get('y_anchor_attempts') or 0)
    if attempts < Y_ANCHOR_ATTEMPTS and total < Y_ANCHOR_CHAPTER_TOTAL:
        mem['y_anchor_attempts'] = attempts + 1
        mem['y_anchor_total'] = total + 1
        mem['y_anchor_due'] = True
        _record(mem, 'cursor_still_yview',
                observed_metric={'cursor': list(cursor), 'still': watch['n'],
                                 'attempt': attempts + 1},
                reason='現在地の推定が動かないため、全体マップ（Y）の自位置カーソルから座標を再確定する')
        return True
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
# docich#4658: menu draw timing for the X jump. The retry must come
# after the first X had time to draw, and the wait must be long
# enough that a slow menu is not declared missing.
CASTLE_JUMP_MENU_RETRY_AGE = 6
CASTLE_JUMP_MENU_WAIT = 12
# The しろ list is an eight-row window (y=39..151). In the observed failures
# it opened on the home castle at row 0, so reaching the last row of a
# 12-castle chapter needs 11 Downs. The old bound of 8 stopped at row 8 and
# left every source from ユージア onward (chapter 6 indices 9+) unreachable:
# g1133 recorded five ユージア castle_missing failures on 2026-11-06 and
# g1163 seven アブダレ failures on 2026-11-09, each with the eight-scroll
# bound exhausted. Keep the bound above the longest chapter roster; the loop
# still stops as soon as the source is visible.
CASTLE_JUMP_SCROLL_LIMIT = 16   # list scrolls while looking for the source
# Issue #4338: the しろ window turns a page only while the hand sits on its
# last row, and a lone 6-frame Down there was dropped before the page moved
# (the monthly roster's タオ…シマウ カラ scroll proved the panel does move).
# Each scroll observation now sends the whole descent plus PROOF taps, so at
# least one press lands after the hand reaches the bottom row.
CASTLE_JUMP_SCROLL_PROOF = 2    # extra taps issued once the hand is on the last row
CASTLE_JUMP_SCROLL_STALL = 2    # unchanged list readings before the Y route takes over
CASTLE_JUMP_COOLDOWN = 40       # observations before retrying a failed source
CASTLE_JUMP_ABANDON = 4         # menu failures before the step is given up
Y_ANCHOR_ATTEMPTS = 2            # Y-view re-anchors before the castle-list jump
# Owner 2026-11-19: six anchors for a whole chapter starved the Y jump — ch6
# spent the budget early, so later jumps had no anchor and the cursor drifted
# on the whole-map view and on the field without ever committing. Keep a bound
# (the v1210 loop guard) but size it for a thirteen-castle chapter.
Y_ANCHOR_CHAPTER_TOTAL = 24      # fruitless Y re-anchors allowed in one chapter
REANCHOR_FAILURE_LIMIT = 3      # consecutive failed reanchors before backing off
REANCHOR_FAILURE_BACKOFF = 600  # observations a tripped reanchor stays quiet
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
    """Compare chart and on-screen castle names without the long-vowel mark.

    The game's しろ list draws イデ！ with a half-width '!' while the chart and
    capture list hold the full-width '！'; the chapter-9 boss order died on
    that difference (g1190 2026-11-15 72-2: castle_missing for イデ！).
    """
    return ((name or '').replace('！', '!').replace('？', '?')
            .replace('ー', '').replace(' ', '').replace('　', ''))


def _castle_list_rows(screen, chapter=None):
    """Rows of the own-castle list, or None when this is another panel.

    The general roster (しょうぐん) draws the same help line and the same
    name column; its rows name generals, so a chapter check rejects it
    instead of scrolling a roster 16 times as if it were the しろ list
    (g1190 2026-11-15 81-1: the roster was saved as a castle-list frame).
    """
    text = screen.text or ''
    if not screen.hand or CASTLE_JUMP_HINT not in text or 'コマンドにもどります' not in text:
        return None
    rows = []
    for line in screen.lines:
        if 39 <= line.y <= 151 and (line.y - 39) % 16 == 0:
            word = dict(line.spans()).get(168)
            if word:
                rows.append((line.y, word))
    if not rows:
        return None
    if chapter:
        cells = chart.castles(chapter)
        names = {_castle_key(name) for name in cells}
        if names and not any(_castle_key(word) in names for _, word in rows):
            return None
    return rows


def _castle_list_target(screen, source, chapter=None):
    """The visible list row naming source (chart spelling may differ)."""
    rows = _castle_list_rows(screen, chapter)
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


def _castle_list_hand_row(screen, rows):
    """The visible row index the orange hand points at, or None."""
    if not screen.hand or not rows:
        return None
    hand_y = screen.hand[1] + 6                      # the same anchor as _current
    index = min(range(len(rows)), key=lambda i: abs(rows[i][0] - hand_y))
    return index if abs(rows[index][0] - hand_y) <= 8 else None


def _castle_list_scroll_batch(rows, hand_row):
    """Released Down taps that reach the last row, then turn the page.

    A single 6-frame Down at the bottom edge was repeatedly dropped before
    the window shifted (#4338). The descent plus CASTLE_JUMP_SCROLL_PROOF
    presses means at least one input lands after the hand reaches the end.
    """
    descent = (len(rows) - 1) - (hand_row if type(hand_row) is int else 0)
    actions = [pad('down')]
    for _ in range(max(0, descent) + CASTLE_JUMP_SCROLL_PROOF - 1):
        actions.extend([{'type': 'wait', 'ms': VISIBLE_LIST_RELEASE_MS}, pad('down')])
    return actions


def _start_reanchor_jump(mem, *, owner=None):
    """Recover a lost cursor from a named castle, without a roof observation.

    The SELECT return from the own-castle list is a stronger reference than
    a guessed camera offset. Reuse the sortie castle-jump state machine, but
    keep this recovery independent of any active order or available garrison.
    """
    home = 'アルマムーン'
    chapter = mem.get('chapter') or 0
    # Anchor at the active order's source castle when it is named: the X
    # list's SELECT to that castle puts the cursor where the sortie's source
    # list opens.  Home is only the fallback (ch4 53-x: the hardcoded home
    # reanchor looped while 4-B1 waited for ミヒラギアン).
    target = home
    active = _plan_region(mem).get('active')
    if active:
        try:
            from ..strategy.orders import _order_for_step
            order = _order_for_step(mem, active)
            source = order.get('source') if isinstance(order, dict) else None
            if isinstance(source, str) and source in chart.castles(chapter):
                target = source
        except Exception:
            target = home
    if (not mem.get('nav_search') or target not in chart.castles(chapter)
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
    mem['castle_jump'] = {'mode': 'reanchor', 'step': None, 'source': target,
                          'phase': 'menu', 'age': 0, 'total': 0, 'tick': tick,
                          'menu_presses': 1, 'selects': 0, 'scrolls': 0, 'waits': 0,
                          'return_to': return_to}
    mem.pop('select_centered', None)
    _record(mem, 'cursor_reanchor_open', target=target,
            observed_metric={'cursor_before': mem.get('cursor'), 'chapter': chapter,
                             'return_to': return_to},
            reason='中断元を保存しX→しろ→出撃元の城→SELECTでカーソル基準点を復旧する')
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
        _mark_order_started(mem, order['step'])
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
    metric = {'why': why, 'age': jump.get('age'), 'phase': jump.get('phase'),
              'target': jump.get('source'),
              'screen': screen.kind,
              'hand': list(screen.hand) if screen.hand else None,
              'selected': screen.selected,
              'menu_presses': jump.get('menu_presses'),
              'total': jump.get('total')}
    rows = _castle_list_rows(screen, mem.get('chapter'))
    if rows is not None:
        metric['rows'] = [word for _, word in rows]
        metric['scrolls'] = jump.get('scrolls')
        # The failed trip's hand position tells a page that never shifted
        # (the hand parked on the last row) from one that did but named the
        # wrong row (g1190 2026-11-16: 16 downs on the same eight rows).
        metric['hand'] = list(screen.hand) if screen.hand else None
    if mode == 'reanchor':
        _record(mem, 'cursor_reanchor_failed', target=jump.get('source'),
                screen=screen.kind, observed_metric=metric,
                reason='アルマムーンへのSELECT復旧が成立しないため、再試行を制限して従来の探索へ戻る')
        # A reanchor that keeps failing is a loop, not a repair: the map flow
        # never leaves it to dispatch the plan (g1190 2026-11-19 run 3:
        # 14 cursor_reanchor_failed and 14 cursor_still_search with no sortie
        # from the chapter start to 58-7). Back off for a long window so the
        # normal navigation can run instead of retrying every cooldown.
        fails = mem.get('reanchor_failures') or {}
        n = int(fails.get('n') or 0) + 1
        if n >= REANCHOR_FAILURE_LIMIT:
            mem['reanchor_failures'] = {'n': 0, 'tick': int(mem.get('tick') or 0)}
            mem['castle_jump_skip'] = {'mode': 'reanchor', 'step': None,
                                       'tick': int(mem.get('tick') or 0) + REANCHOR_FAILURE_BACKOFF}
            _record(mem, 'cursor_reanchor_suppressed',
                    observed_metric={'failures': n, 'backoff': REANCHOR_FAILURE_BACKOFF},
                    reason='再アンカーが続けて失敗するため長時間休止し、通常のナビゲーションへ戻す')
        else:
            mem['reanchor_failures'] = {'n': n, 'tick': int(mem.get('tick') or 0)}
    else:
        _record(mem, 'castle_jump_failed', chart_step=jump.get('step'), target=jump.get('source'),
                screen=screen.kind, observed_metric=metric,
                reason='Xメニューの「しろ」から出撃元へ飛べないため、通常の移動に戻る')
        # docich#4658: when the X menu itself will not open in this state,
        # retrying X forever never launches the order (g1190 ch1 month 4-10:
        # 106 of 106 trips died in 'menu'), so route the same source through
        # the existing Y whole-map jump after a bounded number of failures.
        fails = mem.get('castle_jump_menu_failures')
        fails = fails if isinstance(fails, dict) else {}
        step_key = jump.get('step')
        n = int(fails.get(step_key) or 0) + 1
        fails[step_key] = n
        mem['castle_jump_menu_failures'] = fails
        if why in ('menu_not_open', 'unexpected_menu') and n >= 2:
            mem['castle_jump_y'] = {'step': step_key,
                                    'source': jump.get('source'),
                                    'tick': int(mem.get('tick') or 0)}
            _record(mem, 'castle_jump_y_fallback', chart_step=step_key,
                    target=jump.get('source'), screen=screen.kind,
                    observed_metric={'failures': n, 'why': why},
                    reason='Xメニューが開かない状態が続くため、同じ出撃元へ全体マップ（Y）経由で向かう')
        if why in ('menu_not_open', 'unexpected_menu') and n >= CASTLE_JUMP_ABANDON:
            # docich#4717: own castles never use the Y view (owner 2026-11-19),
            # so a menu that will not open used to retry the same X trip for as
            # long as the order lived (ch6 23-x: 113 failures on ユージア and no
            # sortie ever started). Give the step up instead: the planner picks
            # another source on the next rebuild.
            _plan_region(mem).setdefault('orders', {})[step_key] = 'failed'
            _plan_region(mem)['active'] = None
            fails.pop(step_key, None)
            mem['castle_jump_menu_failures'] = fails
            mem.pop('castle_jump_y', None)
            mem['castle_jump_skip'] = {'step': step_key, 'tick': int(mem.get('tick') or 0)}
            _record(mem, 'castle_jump_abandoned', chart_step=step_key,
                    target=jump.get('source'), screen=screen.kind,
                    observed_metric={'failures': n, 'why': why},
                    reason='Xメニューが開かないまま上限に達したため、この出撃元の注文を諦めて別の城から組み直す')
        # The same bound must cover a list/scroll failure: the interim active
        # order (chart_adjust) can otherwise retry the same castle jump for
        # tens of observations while every planning order stays pending
        # (owner 2026-11-22: 208 castle_jump_scroll / 71 menu failures, no
        # sortie). Give the interim order up and let the plan's pending
        # orders run.
        if (_plan_region(mem).get('active') == step_key
                and n >= CASTLE_JUMP_ABANDON):
            _plan_region(mem).setdefault('orders', {})[step_key] = 'failed'
            _plan_region(mem)['active'] = None
            fails.pop(step_key, None)
            mem['castle_jump_menu_failures'] = fails
            mem.pop('castle_jump_y', None)
            mem['castle_jump_skip'] = {'step': step_key, 'tick': int(mem.get('tick') or 0)}
            adjust = mem.get('chart_adjust')
            if isinstance(adjust, dict):
                interim = adjust.get('interim_order') or {}
                if isinstance(interim, dict) and interim.get('step') == step_key:
                    adjust.pop('interim_order', None)
                    steps = adjust.get('interim_steps')
                    if isinstance(steps, dict):
                        for key, value in list(steps.items()):
                            if value == step_key:
                                steps.pop(key)
                    _record(mem, 'chart_interim_abandoned', chart_step=step_key,
                            observed_metric={'failures': n, 'why': why},
                            reason='暫定注文の城ジャンプが上限まで失敗したため、この暫定注文を破棄して基本計画へ戻す')
            _record(mem, 'castle_jump_abandoned', chart_step=step_key,
                    target=jump.get('source'), screen=screen.kind,
                    observed_metric={'failures': n, 'why': why},
                    reason='城ジャンプが上限まで失敗したため、この出撃元の注文を諦めて別の城から組み直す')
    if screen.kind == 'main_menu' or _castle_list_rows(screen):
        return [pad('b')]
    return []


def _castle_jump_y_fallback(screen, mem, jump, rows):
    """The list never turned a page: close it and route the source through Y.

    Issue #4338: repeated 6-frame Downs sat on the last row without shifting
    the window, so a source below the eighth row could burn the whole trip
    budget. The stall is a property of the panel, not of the order: once the
    menu is closed the ordinary map step retries the same source through the
    whole-map Y jump (mode 'map'), and walks only when Y cannot run.

    Issues #5275 / #5276: that resume bypassed ``_end_castle_jump``, so the
    menu-failure counter never advanced and open → scroll → stall → fallback
    → reopen could repeat without bound. Count stalls per step and give the
    step up at ``CASTLE_JUMP_ABANDON``, the same bound a menu failure uses, so
    the planner can pick another source. A reanchor owns no step and keeps its
    bounded list retries (there is nothing to abandon).
    """
    step_key = jump.get('step')
    source = jump.get('source')
    mem.pop('castle_jump', None)
    mem['castle_jump_skip'] = {'mode': jump.get('mode'), 'step': step_key,
                               'tick': int(mem.get('tick') or 0)}
    mem['castle_jump_y'] = {'step': step_key, 'source': source,
                            'tick': int(mem.get('tick') or 0)}
    stalls = None
    if step_key is not None:
        stalls = mem.get('castle_jump_stall_failures')
        stalls = stalls if isinstance(stalls, dict) else {}
        n = int(stalls.get(step_key) or 0) + 1
        stalls[step_key] = n
        mem['castle_jump_stall_failures'] = stalls
    else:
        n = 0
    metric = {'scrolls': jump.get('scrolls'),
              'stalled': jump.get('scroll_stall'),
              'rows': [word for _, word in rows]}
    if stalls is not None:
        metric['stall_failures'] = n
    _record(mem, 'castle_jump_y_fallback', chart_step=step_key,
            target=source, screen=screen.kind, observed_metric=metric,
            reason='しろ一覧のページ送りが進まないため一覧を閉じ、全体マップ（Y）で出撃元へ飛ぶ')
    if step_key is not None and n >= CASTLE_JUMP_ABANDON:
        # Same retirement as _end_castle_jump's abandon path: without this the
        # same stalled list is re-opened on every resume with no escape.
        _plan_region(mem).setdefault('orders', {})[step_key] = 'failed'
        if (_plan_region(mem).get('active') == step_key):
            _plan_region(mem)['active'] = None
        stalls.pop(step_key, None)
        mem['castle_jump_stall_failures'] = stalls
        mem.pop('castle_jump_y', None)
        mem['castle_jump_skip'] = {'step': step_key, 'tick': int(mem.get('tick') or 0)}
        adjust = mem.get('chart_adjust')
        if isinstance(adjust, dict):
            interim = adjust.get('interim_order') or {}
            if isinstance(interim, dict) and interim.get('step') == step_key:
                adjust.pop('interim_order', None)
                steps = adjust.get('interim_steps')
                if isinstance(steps, dict):
                    for key, value in list(steps.items()):
                        if value == step_key:
                            steps.pop(key)
                _record(mem, 'chart_interim_abandoned', chart_step=step_key,
                        observed_metric={'stall_failures': n, 'why': 'scroll_stalled'},
                        reason='暫定注文の城ジャンプがページ送り停滞で上限に達したため破棄して基本計画へ戻す')
        _record(mem, 'castle_jump_abandoned', chart_step=step_key,
                target=source, screen=screen.kind,
                observed_metric={'stall_failures': n, 'why': 'scroll_stalled'},
                reason='しろ一覧のページ送り停滞が上限に達したため、この出撃元の注文を諦めて別の城から組み直す')
    return [pad('b')]

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
            # The chart worker may re-plan while the jump is in flight, which
            # renumbers every step (g1163 2026-11-10 05:38-05:50: five jumps
            # aborted at age 1 with order_changed, so the sortie never left).
            # The physical trip is still valid when the same general leaves
            # the same castle for the same target, so carry the jump over.
            if (not _plan_region(mem).get('active')
                    or _order_for_step(mem, _plan_region(mem).get('active')) is None):
                # Between plans the active order can be empty, or name a step
                # the replaced plan no longer holds; that is not a change of
                # intent (g1163 2026-11-10 07:01: the jump still died at age 1
                # one second after order_start).
                jump['orphan'] = int(jump.get('orphan') or 0) + 1
                if jump['orphan'] <= CASTLE_JUMP_ORPHAN_HOLD:
                    return []
            adopted = _jump_successor(mem, order, jump)
            if adopted is None:
                return _end_castle_jump(screen, mem, 'order_changed')
            jump['step'] = adopted['step']
            jump['source'] = _source(adopted, mem) or jump.get('source')
            order = adopted

    phase = jump.get('phase')
    # docich#4718: the X menu never opened because the field cursor estimate was
    # already gone when the jump started — the failure metrics carried
    # cursor:null for every one of the 113 attempts on ユージア. X opened from a
    # cell whose position is unknown does nothing on this map. Re-anchor first
    # (SELECT is measured to centre the cursor on the hero) and only then ask
    # for the menu.
    if (phase == 'menu' and screen.kind == 'map' and not mem.get('cursor')
            and not jump.get('centered')):
        jump['centered'] = True
        _record(mem, 'castle_jump_center_cursor', chart_step=jump.get('step'),
                target=jump.get('source'),
                observed_metric={'cursor': mem.get('cursor')},
                reason='カーソル位置が不明のままXを押しても開かないため、SELECTで主人公へカーソルを中央寄せしてから開く')
        return [pad('select')]
    if phase == 'menu':
        if screen.kind == 'map':
            # Give the first X a few observations to draw the menu; a second
            # X too early would close the menu it just opened.
            # docich#4658: the X sent when the jump starts needs a few
            # frames to draw the menu. A second X while the frame still
            # reads 'map' closed the menu it had just opened, and the
            # jump gave up at age 4 (g1190 ch1: 106 of 106 failures were
            # menu_not_open). Retry once late and wait a bounded window.
            if (jump['age'] >= CASTLE_JUMP_MENU_RETRY_AGE
                    and int(jump.get('menu_presses') or 0) < 2):
                jump['menu_presses'] = int(jump.get('menu_presses') or 0) + 1
                return [pad('x')]
            if jump['age'] < CASTLE_JUMP_MENU_WAIT:
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
            if jump.get('step') and jump.get('mode') != 'reanchor':
                _mark_order_started(mem, jump['step'])
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
        rows = _castle_list_rows(screen, mem.get('chapter'))
        if rows is None:
            jump['blind'] = int(jump.get('blind') or 0) + 1
            if jump['blind'] <= CASTLE_JUMP_CURSOR_BLIND:
                return []
            return _end_castle_jump(screen, mem, 'list_unreadable')
        jump['blind'] = 0
        target = _castle_list_target(screen, jump['source'], mem.get('chapter'))
        if target is None:
            hand_row = _castle_list_hand_row(screen, rows)
            signature = ([word for _, word in rows], hand_row)
            jump['scroll_stall'] = (int(jump.get('scroll_stall') or 0) + 1
                                    if signature == jump.get('scroll_signature') else 0)
            jump['scroll_signature'] = signature
            if (jump['scroll_stall'] >= CASTLE_JUMP_SCROLL_STALL
                    and jump.get('mode') != 'reanchor'):
                return _castle_jump_y_fallback(screen, mem, jump, rows)
            if int(jump.get('scrolls') or 0) < CASTLE_JUMP_SCROLL_LIMIT:
                jump['scrolls'] = int(jump.get('scrolls') or 0) + 1
                jump['age'] = 0
                batch = _castle_list_scroll_batch(rows, hand_row)
                _record(mem, 'castle_jump_scroll', chart_step=jump.get('step'),
                        target=jump['source'], screen=screen.kind,
                        observed_metric={'scrolls': jump['scrolls'],
                                         'hand_row': hand_row,
                                         'taps': sum(1 for a in batch if a['type'] == 'pad'),
                                         'rows': [word for _, word in rows]},
                        reason='出撃元の行が見えないため、手を一覧の最下行まで送ってページ送りする')
                return batch
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
            # This field frame is also the recall's entry: a visible own camp
            # must not be spent on the castle menu (g1163 2026-11-14 05:28:
            # camps=1 but the jump's A took the frame). Keep the jump state so
            # the sortie resumes after the recall.
            camp_skipped = False
            if isinstance(frame, Frame) and own_camps(frame):
                from . import recall as recall_flow
                camp_skipped = not recall_flow._filter_recall_skip(mem, own_camps(frame))
                actions = recall_flow.camp_recall_step(screen, mem, frame)
                if actions:
                    return actions
            mem['expect_menu'] = True
            mem.pop('castle_jump', None)
            mem.pop('castle_jump_skip', None)
            # SELECT from the named row (or onto the hero's castle) put the
            # cursor on the source. Keep the estimate there; the deploy layer
            # still corrects it from the castle status if it was not.
            cell = chart.castles(mem.get('chapter') or 0).get(jump['source'])
            if cell:
                mem['cursor'], mem['uncertain'] = list(cell), False
            if not hero_select:
                # The しろ list row was read and named the castle before
                # SELECT, so the deploy step does not have to open ステータス
                # to prove the same name again (g1163 2026-11-12 23:1x: every
                # sortie spent three more frames there).
                mem['castle_verified'] = jump['step']
            _record(mem, 'castle_jump_confirm', chart_step=jump['step'], target=jump['source'],
                    **({'strategy_variant': 'hero_select'} if hero_select else {}),
                    observed_metric={'cursor': mem.get('cursor'),
                                     'screen_cursor': list(_cursor(screen) or ()),
                                     'roof_under_cursor': roof_visible,
                                     # The castle-jump field frame is also a
                                     # field observation: how many own camps
                                     # it showed explains why a recall never
                                     # started from these frames (g1163
                                     # 2026-11-14 04:46-05:12).
                                     'camps': len(own_camps(frame)) if isinstance(frame, Frame) else 0,
                                     **({'camp_skipped': True} if camp_skipped else {})},
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
        if screen.kind == 'main_menu':
            # SELECT closed the list back to the main menu instead of
            # flying. Waiting here spent the whole jump budget (g1190
            # 2026-11-15 77-10: three カラ jumps sat in confirm for 35
            # observations each and failed with why=limit). Re-open しろ
            # and retry the same row, bounded.
            retries = jump['menu_return_retries'] = int(jump.get('menu_return_retries') or 0) + 1
            _record(mem, 'castle_jump_menu_return', chart_step=jump['step'],
                    target=jump['source'],
                    observed_metric={'retries': retries, 'age': jump.get('age')},
                    reason='しろ一覧のSELECTでフィールドに戻らずメインメニューになったため、一覧を開き直す')
            if retries <= 2:
                jump['phase'] = 'menu'
                jump['age'] = 0
                jump['menu_presses'] = 0
                return []
            return _end_castle_jump(screen, mem, 'select_returned_to_menu')
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


GENERAL_JUMP_LIMIT = 96          # observations across the whole roster trip
GENERAL_JUMP_SCROLL_LIMIT = 40   # the all-general roster outgrows the castle list's 16


def _end_general_jump(screen, mem, why):
    jump = mem.pop('general_jump', None)
    if not isinstance(jump, dict):
        return []
    # docich#4752: for general_missing distinguish "target absent from the
    # roster" from "target visible but selected never matched" — record the
    # highlighted name and the visible roster rows (x=168 column).
    rows = sorted({w for x, y, w in _options(screen) if x == 168})[:12] if screen.kind == 'text' else []
    seen = jump.get('seen') or []
    target_seen = any(_canonical_general(w) == jump.get('general') for w in seen)
    # The roster was walked to the end and the target is not in it: the
    # general left the country. The plain 200-tick skip re-armed the same
    # absent target every minute for an hour (live g1288 2026-11-21
    # 04:13-05:11: 50+ general_jump_failed, all target_seen=false), so
    # remember the absence for a long window. The skip is per general: a
    # single slot let two absent generals alternate forever (05:21-05:29:
    # トレビス/タルタル ping-pong).
    target_name = _canonical_general(jump.get('general') or '')
    if target_name:
        skips = mem.get('general_jump_skip')
        if not isinstance(skips, dict) or 'general' in skips:
            skips = {}                       # legacy single-slot shape
        skips[target_name] = {'tick': int(mem.get('tick') or 0),
                              'absent': bool(not target_seen
                                             and jump.get('scrolls', 0) >= GENERAL_JUMP_SCROLL_LIMIT)}
        mem['general_jump_skip'] = skips
    _record(mem, 'general_jump_failed', target=jump.get('general'),
            screen=screen.kind,
            observed_metric={'why': why, 'total': jump.get('total'),
                             'phase': jump.get('phase'),
                             'scrolls': jump.get('scrolls'),
                             'selected': screen.selected,
                             'rows': rows,
                             'seen_count': len(seen),
                             'target_seen': target_seen,
                             'text': (screen.text or '')[:48]},
            reason='しょうぐん一覧から将軍へSELECTで飛べないため、野営探索へ戻る')
    if screen.kind in ('main_menu', 'general_list', 'castle_menu'):
        return [pad('b')]
    return []


def general_jump_step(screen, mem, frame):
    """Drive X -> しょうぐん -> general name -> SELECT onto that general.

    The camp->boss redirect needs the camped general observed, but a stale
    boss march camps in the boss's roam band that the cursor cannot reach
    (``nav_step`` is infeasible over the distance, and the corridor camps
    keep the west view busy). The X-menu roster's SELECT jumps the cursor
    straight to a general (manual p.15), bringing the stale-landed tent
    into view so the redirect can pick it up. Returns None when no jump is
    in flight.
    """
    jump = mem.get('general_jump')
    if not isinstance(jump, dict):
        # The camp recall's corridor-view trigger only runs while its flow
        # owns a camp frame; on a plain held field there is no such owner,
        # so arm the jump here whenever an overdue boss march sits camped
        # out of the cursor's reach. The X menu only opens from the field,
        # so a month/menu frame must not arm it: arming there fires
        # 'unexpected_menu' and retries every observation.
        if screen.kind != 'map' or isinstance(mem.get('boss_pursuit'), dict):
            return None
        if order_dispatch_starved(mem):
            # A starved boss-phase order owns the next free map frame. The
            # jump is useful recovery work, but arming it here repeatedly kept
            # that order behind a multi-menu roster trip (#5155).
            return None
        if (mem.get('chapter') or 0) == 9:
            from . import boss_pursuit as _boss_pursuit
            if _boss_pursuit.wave_active(mem) and _boss_pursuit._candidate(mem) is not None:
                return None
        boss_castle = getattr(chart, 'boss_castle', None)
        boss_target = boss_castle(mem.get('chapter') or 0) if boss_castle else None
        if boss_target is None:
            return None
        tick = int(mem.get('tick') or 0)
        skips = mem.get('general_jump_skip')
        if not isinstance(skips, dict) or 'general' in skips:
            skips = {}                       # legacy single-slot shape

        def _skipped(name):
            row = skips.get(_canonical_general(name))
            if not isinstance(row, dict):
                return False
            limit = 3600 if row.get('absent') else 200
            return tick - int(row.get('tick') or 0) < limit

        general = next((_canonical_general(s['general'])
                        for s in _sorties(mem).values()
                        if isinstance(s, dict) and s.get('target') == boss_target
                        and s.get('status') == 'overdue' and s.get('general')
                        and not _skipped(s['general'])), None)
        if general is None:
            return None
        mem['general_jump'] = jump = {'general': general, 'phase': 'menu',
                                      'age': 0, 'total': 0, 'scrolls': 0,
                                      'menu_presses': 0}
        _record(mem, 'general_jump_open', target=general,
                observed_metric={'cursor': mem.get('cursor')},
                reason='期限切れボス行軍の将軍がカーソル圏外に野営したため、しょうぐん一覧からSELECTでその野営へ飛ぶ')
    jump['total'] = int(jump.get('total') or 0) + 1
    jump['age'] = int(jump.get('age') or 0) + 1
    if jump['total'] > GENERAL_JUMP_LIMIT:
        return _end_general_jump(screen, mem, 'limit')
    target = jump.get('general')
    phase = jump.get('phase')

    if phase == 'menu':
        if screen.kind == 'map':
            # docich#4658: the X sent when the jump starts needs a few
            # frames to draw the menu. A second X while the frame still
            # reads 'map' closed the menu it had just opened, and the
            # jump gave up at age 4 (g1190 ch1: 106 of 106 failures were
            # menu_not_open). Retry once late and wait a bounded window.
            if (jump['age'] >= CASTLE_JUMP_MENU_RETRY_AGE
                    and int(jump.get('menu_presses') or 0) < 2):
                jump['menu_presses'] = int(jump.get('menu_presses') or 0) + 1
                return [pad('x')]
            if jump['age'] < CASTLE_JUMP_MENU_WAIT:
                return []
            return _end_general_jump(screen, mem, 'menu_not_open')
        if screen.kind == 'unknown':
            jump['fade'] = int(jump.get('fade') or 0) + 1
            if jump['fade'] <= CASTLE_JUMP_FADE_BLIND:
                return []
            return _end_general_jump(screen, mem, 'unexpected_menu')
        if screen.kind == 'main_menu':
            jump['fade'] = 0
            if screen.hand is None:
                jump['blind'] = int(jump.get('blind') or 0) + 1
                if jump['blind'] <= CASTLE_JUMP_CURSOR_BLIND:
                    return []
                return _end_general_jump(screen, mem, 'hand_hidden')
            jump['blind'] = 0
            move = menu_to(screen, 'しょうぐん')
            if move == 'here':
                jump['phase'] = 'list'
                jump['age'] = 0
                return [pad('a')]
            if move:
                return [move]
            return _end_general_jump(screen, mem, 'roster_missing')
        return _end_general_jump(screen, mem, 'unexpected_menu')

    if phase == 'list':
        if screen.kind == 'main_menu':
            if jump['age'] >= 2 and int(jump.get('menu_presses') or 0) < 3:
                jump['menu_presses'] = int(jump.get('menu_presses') or 0) + 1
                return [pad('a')]
            if jump['age'] < 3:
                return []
            return _end_general_jump(screen, mem, 'list_not_open')
        roster_text = False
        if screen.kind not in ('general_list', 'castle_menu'):
            # X -> しょうぐん opens the all-generals quick-select panel, which
            # classifies as 'text' with a global_roster menu reading (the same
            # panel the monthly status walk uses). Its SELECT still jumps the
            # map cursor to the named general, so accept it here instead of
            # failing every attempt as unexpected_list (docich#4752).
            reading = menu_feedback.reading(screen)
            roster_text = (screen.kind == 'text'
                           and ((reading is not None
                                 and reading.get('kind') == 'text'
                                 and reading.get('menu') == 'global_roster')
                                # g1241: the same panel sometimes draws without
                                # the 'Aボタンでステータスひょうじ' help line, so
                                # menu_feedback returns None. The roster still
                                # shows a selected name plus x=168 name rows;
                                # accept that shape too (docich#4752).
                                or (screen.selected
                                    and sum(1 for x, _y, _w in _options(screen)
                                            if x == 168) >= 3)))
            if roster_text:
                jump['text_blind'] = 0
            elif screen.kind == 'text':
                # g1242: while the roster scrolls/redraws, intermediate
                # 'text' frames briefly lose selected/rows and fail the
                # structural check, killing the whole jump as
                # unexpected_list. Wait a bounded window for the panel to
                # settle instead of ending on the first transient frame.
                jump['text_blind'] = int(jump.get('text_blind') or 0) + 1
                if jump['text_blind'] <= 6:
                    return []
                return _end_general_jump(screen, mem, 'unexpected_list')
            elif screen.kind == 'unknown':
                return []
            else:
                return _end_general_jump(screen, mem, 'unexpected_list')
        if screen.hand is None:
            jump['blind'] = int(jump.get('blind') or 0) + 1
            if jump['blind'] <= CASTLE_JUMP_CURSOR_BLIND:
                return []
            return _end_general_jump(screen, mem, 'hand_hidden')
        jump['blind'] = 0
        # docich#4752: accumulate every roster name seen while scrolling so a
        # general_missing failure can prove whether the target was absent
        # from the panel or merely never reached (bounded to 240 entries).
        seen = jump.setdefault('seen', [])
        for _x, _y, word in _options(screen):
            if _x == 168 and word not in seen and len(seen) < 240:
                seen.append(word)
        if _canonical_general(screen.selected or '') == target:
            jump['phase'] = 'confirm'
            jump['age'] = 0
            return [pad('select')]
        if int(jump.get('scrolls') or 0) < GENERAL_JUMP_SCROLL_LIMIT:
            jump['scrolls'] = int(jump.get('scrolls') or 0) + 1
            jump['age'] = 0
            batch = roster_list_batch(screen, target)
            if batch:
                return batch
            return [pad('down')]
        return _end_general_jump(screen, mem, 'general_missing')

    if phase == 'confirm':
        if screen.kind == 'map':
            # The SELECT landed the cursor on the named general's field or
            # camp; leave the state so the visible tent can feed the recall's
            # boss-redirect chase on the normal dispatch. Record a normal
            # cooldown so the same overdue sortie does not immediately re-arm
            # general_jump on the next map frame (#4857).
            mem.pop('general_jump', None)
            if target:
                skips = mem.get('general_jump_skip')
                if not isinstance(skips, dict) or 'general' in skips:
                    skips = {}
                skips[target] = {'tick': int(mem.get('tick') or 0), 'absent': False}
                mem['general_jump_skip'] = skips
            _record(mem, 'general_jump_confirm', target=target,
                    observed_metric={'cursor': list(_cursor(screen) or ())},
                    reason='しょうぐん一覧のSELECTで将軍へ飛んだため、その野営を野営→ボス追撃へ回す')
            return None
        if screen.kind in ('general_list', 'castle_menu'):
            return _end_general_jump(screen, mem, 'select_opens_status')
        if screen.kind == 'month_menu':
            # The self-arm on a month frame was removed in docich#4601, but a
            # jump already in flight when the month menu appeared still waited
            # out the whole 48-observation budget with no input (g1270
            # 2026-11-18 22:08: a month_menu froze for 300 s and the run ended
            # screen_stalled). The SELECT cannot land on this screen, so
            # release it now and let the month flow own the frame.
            return _end_general_jump(screen, mem, 'month_menu')
        return []
    return _end_general_jump(screen, mem, 'unknown_phase:' + str(phase))


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
                                            (_plan_region(mem).get('chart_plan') or {}).get('orders', [])[:8]],
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
    if mem.pop('y_anchor_due', False) and not mem.get('menu_miss'):
        # A menu-miss hold already has its own bounded Y-jump escape; only a
        # plain frozen cursor asks for the Y view here.
        return [pad('y')]
    if not _cursor(screen) and not screen.header and not screen.text:
        # #3721: a chapter transition parks on a map with no HUD, text or
        # cursor until A advances the intermission (g1153: 174 s of no
        # input; one A continued the dialogue). A normal field map always
        # shows a cursor, so all three missing means a cinematic: press A
        # once per bounded streak instead of waiting it out.
        blind = int(mem.get('map_blind') or 0) + 1
        mem['map_blind'] = blind
        if blind >= MAP_BLIND_ADVANCE:
            mem['map_blind'] = 0
            _record(mem, 'map_blind_advance', screen=screen.kind,
                    observed_metric={'blind': blind},
                    reason='カーソル・HUD・文字のない地図が続くため章送り等の演出をAで進める')
            return [pad('a')]
    else:
        mem.pop('map_blind', None)
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
        elif (tick - int(last) >= SELECT_FOCUS_INTERVAL
                and not order_dispatch_starved(mem)):
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
        forget_picked(mem)
        order = None
    if isinstance(order, dict) and (str(order.get('step', '')).startswith(chart_adjust.INTERIM_PREFIX)
                                    or (_plan_region(mem).get('chart_plan') or {}).get('source') == 'mechanical'):
        if (mem.get('chart_adjust') or {}).get('request_id') == chart_adjust.request_id(mem):
            _off_chart(mem)
            order = _order(mem) or next_order(mem) or order
    if order is None and _cursor(screen) and not screen.marker and _world_map_wanted(mem):
        # Before choosing what to do next, read every castle's owner at once —
        # unless a boss-phase order is starved for dispatch (docich#5077):
        # the repeated survey then yields one frame to the block below.
        if not order_dispatch_starved(mem):
            mem['world_map_tick'] = int(mem.get('tick') or 0)   # bounded even if Y shows nothing
            mem.pop('world_map_due', None)
            _record(mem, 'world_map_open', screen=screen.kind,
                    reason='全城の所有を全体マップで確認するためYを押す')
            return [pad('y')]
    if order is None:
        from ..strategy import corridor
        # Chapter 9's march does not reopen a source list to settle an
        # unconfirmed launch (owner 2026-11-11: それ以外の操作はdisable); the
        # march deadline settles it.
        order = None if corridor.mechanical(mem) else next(
                     (_order_for_step(mem, step) for step, sortie in _sorties(mem).items()
                      if sortie.get('status') == 'launched_unconfirmed'
                      and not sortie.get('verification_unavailable')
                      and sortie.get('source') in _owned(mem)), None)
        order = order or next_order(mem)
        if (order is None or (isinstance(order, dict)
                              and (str(order.get('step', '')).startswith(chart_adjust.INTERIM_PREFIX)
                                   or (_plan_region(mem).get('chart_plan') or {}).get('source') == 'mechanical'))):
            _off_chart(mem)
            order = next_order(mem) or order
        if order is None:
            # docich#5077: nothing startable ends the dispatch wait; a pending
            # but unstartable order re-arms the bound on the next check.
            mem.pop('order_dispatch_since', None)
            update_world(screen, mem, frame)
            goals.note_map_idle(mem)
            return []           # nothing charted: let real time advance
        mem.setdefault('order_dispatch_since', int(mem.get('tick') or 0))
        if not _order_start_due(mem, order['step']):
            _tally(mem, 'map_hold_order_due')
            return []
        # Issue #3326 (34 stage 4): a same-signature retry inside
        # SORTIE_RETRY_DEFER_GAP of a cancel at the same (target, purpose)
        # is the measured blind round trip. Deferring the start keeps the
        # order pending — the pick re-runs each observation and passes
        # once the gap outgrows the band or the state signature changes —
        # instead of walking the same menus again with no new evidence.
        from .house import _sortie_retry_hold, _sortie_state_sig
        # Chapter 9's march orders are never deferred: each one sends whoever
        # the list cursor is on (owner 2026-11-11: 機械的に次の城にすべての将軍を).
        held = None if order.get('corridor') is True else _sortie_retry_hold(
                mem, order['target'], order.get('purpose'),
                _sortie_state_sig(mem, _source(order, mem), _sortie_general(order, mem)))
        if held is not None:
            pending, first = held
            if first:
                # One record per pending cancel: the hold re-fires every
                # observation in the band, so recording here unconditionally
                # would flood the decision log (~100 rows per cancel).
                _record(mem, 'sortie_retry_deferred', chart_step=order['step'],
                        source=_source(order, mem), target=order['target'],
                        purpose=order.get('purpose'),
                        observed_metric={'cancel_tick': pending.get('tick'),
                                         'gap': int(mem.get('tick') or 0) - int(pending.get('tick') or 0)},
                        reason='同一砦・同一目的の出撃取消し直後で状態署名が変わっていないため再要求を延期する')
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
        # A restarted attempt gets a fresh destination placement. A receipt
        # left by the previous attempt would confirm the new picker's marker
        # on the old cell without a jump (the rearm path after an interrupted
        # sortie does not pass through the resumed-picker cleanup).
        for key in ('y_jumped', 'y_jumped_res'):
            bucket = mem.get(key)
            if isinstance(bucket, dict):
                bucket.pop(order['step'], None)
        if (mem.get('near_goal') or {}).get('step') == order['step']:
            mem.pop('near_goal', None)
        mem.pop('castle_verified', None)
        if order.get('source_probe') is True:
            mem.setdefault('arrival_probe_tick', {})[_source(order, mem)] = int(mem.get('tick') or 0)
        mem.pop('order_dispatch_since', None)   # docich#5077: the wait ends at dispatch
        # docich#5702: the priority window starts at dispatch, not only after
        # the later departure commit. Housekeeping otherwise keeps preempting
        # the long source/cards/target walk before sortie_departure_requested.
        arm_sortie_launch_priority(mem, order)
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
            _mark_order_started(mem, order['step'])
            return _deploy_input(screen, mem, order, [pad('a')], '全体マップで選んだ出撃元の屋根に合わせて城を選択')
        if aligned:
            return _deploy_input(screen, mem, order, aligned, '全体マップで選んだ出撃元の屋根へカーソルを合わせる')
        mem.pop('near_goal', None)
        mem.pop('cursor', None)                  # no roof near: jump again rather than search
        _record(mem, 'align_failed', chart_step=order['step'], target=source, screen=screen.kind,
                reason='全体マップで選んだ出撃元の近くに屋根が無いため全体マップを開き直す')
    if screen.cursor and not screen.marker:
        # Owner 2026-11-19: an own castle is reached with the しろ list's
        # SELECT jump only — never through the whole-map Y view. A list that
        # stalled (or a menu that never opened) re-opens the same menu jump;
        # the existing cooldown bounds the retries.
        y_route = mem.pop('castle_jump_y', None)
        if isinstance(y_route, dict) and y_route.get('source') == source:
            return _start_castle_jump(mem, order, source)
    # docich#4658: a field frame without the month/gold HUD is a camera pan,
    # battle announcement or other mode transition where X does nothing. The
    # jump used to start there, spend both presses and die with
    # 'field_unstable' (or 'menu_not_open' before v1172), then start again on
    # the next frame. Only open the trip once the field is readable.
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
                forget_picked(mem)
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
        _mark_order_started(mem, order['step'])
        return _deploy_input(screen, mem, order, [pad('a')], '出撃元の城を選択')
    if result is None:
        if _cursor(screen):
            _record(mem, 'situation_held', screen=screen.kind,
                    observed_metric={'screen_cursor': list(_cursor(screen))},
                    reason='マップ位置を屋根アンカーで再特定できないため入力を保留')
        return []
    return _deploy_input(screen, mem, order, result, '出撃元の城へカーソルを移動')


def _target_confirm_actions(mem, order):
    """Confirm the sortie target; chapter 9's オシロガ steps up once first.

    Owner 2026-11-24: the オシロガ sortie still caught the cliff even after
    the aim was shifted; pressing UP once immediately before the confirming A
    lands the march on the passable cell above it.
    """
    if ((mem.get('chapter') or 0) == 9 and order.get('target') == 'オシロガ'):
        seen = _flow(mem).setdefault('ch9_oshiroga_confirm', {})
        if not seen.get(order['step']):
            seen[order['step']] = True
            _record(mem, 'ch9_oshiroga_confirm_up', chart_step=order['step'],
                    target=order['target'],
                    reason='オシロガの出撃確定直前に↑を1回押して崖を避ける')
            return [pad('up'), pad('a')]
    return [pad('a')]


def target_step(screen: Screen, mem, frame):
    if _drop_stale_y_jump(screen, mem):
        return []
    order = _order(mem)
    if order is None:
        flow(mem).pop('departure_request', None)   # a stale reservation owns nothing
        # A marker we did not request: cancel instead of sending a general.
        _record(mem, 'unexpected_target', reason='指示中でない出撃先選択画面のためBで取消')
        return [pad('b')]
    attempt = _flow(mem).get('sortie_attempt')
    if (isinstance(attempt, dict) and attempt.get('step') == order['step']
            and attempt.get('battle_interrupt')):
        # The battle that cut the target selection left its marker where the
        # game restored it. A confirmation receipt from before that fight is
        # not evidence about this screen: unless a fresh target jump placed the
        # marker after the interruption, drop the old placement so the jump
        # below runs again. The jump lands on the measured cell and the marker
        # is read from that fresh placement.
        interrupt_tick = attempt.get('battle_interrupt_tick')
        placed_tick = (mem.get('y_jump_target_confirmed') or {}).get('tick')
        freshly_placed = (type(interrupt_tick) is int and type(placed_tick) is int
                          and placed_tick >= interrupt_tick)
        attempt.pop('battle_interrupt', None)
        attempt.pop('battle_interrupt_tick', None)
        if not freshly_placed:
            forget_target_placement(mem, order['step'])
            _record(mem, 'sortie_target_rejump', chart_step=order['step'],
                    target=order['target'], screen=screen.kind,
                    reason='戦闘割り込みで出撃先マーカーが復元されたため、割り込み前の到達記録を捨ててYジャンプをやり直す')
    locked = _hold_locked_boss(mem, order)
    if locked is not None:
        return locked
    if (_is_boss_order(order, mem) and _sortie_general(order, mem) == NAME
            and (_plan_region(mem).get('orders') or {}).get(order['step']) not in {'launched', 'launched_unconfirmed'}):
        _flow(mem).pop('sortie_attempt', None)
        if not _boss_companion_wait_expired(mem, order):
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
    if chart.is_boss_castle(order['target']):
        # Owner 2026-11-16 (chapter 9): the boss order's first leg aims at the
        # waypoint north of イデ！ (the cliffs block the direct line at the
        # skull); a re-armed step aims at the measured boss cell itself. The
        # plain chart cell would aim every attempt straight at the skull.
        goal = (boss_probe_cell(mem, order['target'], order['step'])
                or _measured_goal(mem, order, order['target']))
    else:
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
    if (result is None and screen.marker and not y_jump_unreachable(mem, order['target'])
            and _want_y_jump(mem, order, order['target'], 'target')):
        # Carry the same measured cell the walking path uses (route offsets
        # included) into the Y view. A fresh jump after a battle must not
        # silently fall back to the bare castle cell.
        return _start_y_jump(mem, order, order['target'], 'target', goal_cell=goal)
    if result is None:
        if (chart.is_boss_castle(order['target'])
                and (mem.get('y_jumped') or {}).get(order['step']) == order['target']):
            # Chapter 9 (owner 2026-11-16): the move to the north waypoint is
            # the Y jump itself. Once the view put the target marker on the aim
            # cell, dead-reckoned field walking only drifts it into the cliff
            # ring and the arrival never verifies, so the jumped landing is
            # confirmed directly — the sortie actually marches north.
            result = 'arrived'
        else:
            result = nav_step(screen, mem, frame, goal, order['target'], step=order['step'])
        if not result and screen.marker and (not mem.get('cursor') or mem.get('nav_search')):
            # Jumps spent and the marker's place unknown (g421 18:24: 70 idle
            # observations), or the cursor will not move under the presses at
            # all (_nav_stuck sets nav_search; g1190 ch9 2026-11-15 kept
            # pressing a picker that ignored every input and the front never
            # fell). Count it as an unverified arrival so the sortie is
            # cancelled and retried within its bounds instead of idling.
            mem.pop('nav_search', None)
            return _unverified_target(screen, mem, order, frame)
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
        return _unverified_target(screen, mem, order, frame)
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
        return _target_confirm_actions(mem, order)
    return _deploy_input(screen, mem, order, result or [], '出撃先へ目標カーソルを移動')


def _commit_departure(mem, order, context, snapshot):
    """Departure observed: the fixed request becomes world state exactly once.

    Runs only on receipt-backed evidence (the picker closed after a journaled
    A). The garrison fact, the launched order and the en-route sortie it
    creates are identical to what the pre-#3193 code committed eagerly.
    """
    (mem.get('target_miss') or {}).pop(order['step'], None)
    # A committed departure proves earlier releases premature: a later retry
    # of this order must not inherit the old request-miss budget.
    for key in ('departure_miss', 'departure_sent_miss'):
        misses = flow(mem).get(key)
        if isinstance(misses, dict):
            misses.pop(order['step'], None)
    _finish_order(mem, 'launched', **context, target=order['target'],
                  cursor=mem.get('cursor'), anchor=mem.get('anchor'),
                  reason=f"{context['general']}を{order['target']}へ出撃")
    general = context['general']
    _garrison_move(mem, general, source=_source(order, mem))
    if general == NAME and order.get('purpose') == 'move' and order.get('corridor') is True:
        # The hero marched out of the source roster: remember the castle he is
        # walking into so the next capture's post rule can still move him
        # without waiting for a roster read (owner 2026-11-15).
        from ..strategy import corridor
        corridor.note_hero_post(mem, order['target'])
    _world(mem).setdefault('launched', {})[order['target']] = {'general': general, 'step': order['step']}
    # Snapshot: the battle, boss entry and retries of this sortie must not
    # depend on the plan still containing it.
    _world(mem).setdefault('launched_orders', {})[order['step']] = snapshot
    # Per execution id: ``launched`` keeps one sortie per castle, so a later
    # sortie to the same castle must not unbind an earlier unit en route.
    previous = _sorties(mem).get(order['step'])
    if previous:
        _retire_unentered_sortie(mem, order['step'], previous)
    chapter = mem.get('chapter') or 0
    castles = chart.castles(chapter)
    _world(mem).setdefault('sorties', {})[order['step']] = {
        'general': general, 'source': snapshot['source'],
        'target': order['target'], 'status': 'en_route',
        'purpose': snapshot.get('purpose'),
        'aimed_cell': (list(castles[order['target']]) if order['target'] in castles else None),
        'evidence': deepcopy({k: v for k, v in
            ((_plan_region(mem).get('order_context') or {}).get(order['step']) or {}).items()
            if k != 'departure_order'}),
        'tick': int(mem.get('tick') or 0),
        **({'confirmation_tick': snapshot['departure_binding']['confirm_tick']}
           if 'departure_binding' in snapshot else {})}
    ((_plan_region(mem).get('order_context') or {}).get(order['step']) or {}).pop('departure_order', None)
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
        # owner 2026-11-27 (#5696/#5702): 出撃を開始したが発射を確認できていない
        # 間は、築城一覧・家来確認などの割り込みより発射確認を優先する。
        arm_sortie_launch_priority(mem, order)
        mem['world_map_due'] = True


BOSS_MARKER_CELL_PX = 24     # 3 tiles: the marker on the measured boss cell
TARGET_MISS_LIMIT = 3          # unverified target arrivals before cancelling the sortie
TARGET_CANCEL_LIMIT = 2        # cancelled sorties per order before failing it
DEPARTURE_SENT_HOLD_LIMIT = 4     # sent picker readings before treating the A as unaccepted
DEPARTURE_RESEND_LIMIT = 2        # journal-proven unsent resends inside one request
DEPARTURE_REQUEST_LIMIT = 2       # released request cycles per order before failing it
DEPARTURE_SENT_MISS_LIMIT = 4     # released sent-request cycles before failure evidence suffices
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
# Chapters whose world map draws marching units as portraits, never as the
# (255,24,0) triangle the survey reads (g1190 2026-11-16 chapter 9).
PORTRAIT_MARCH_CHAPTERS = frozenset({9})
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
            portrait = mem.get('chapter') in PORTRAIT_MARCH_CHAPTERS
            if chart.is_boss_castle(probe.get('target')) or portrait:
                # g1190 2026-11-16: chapter 9 draws every marching unit as a
                # portrait too (the two "markers" were the egg-repair hearts),
                # so no transfer was ever confirmed and each was rolled back
                # with the general written back into the source garrison
                # while the destination's menu already listed him.
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
                        resulting_event=('boss_march_kept' if chart.is_boss_castle(probe.get('target'))
                                         else 'portrait_march_kept'),
                        reason='行軍中の部隊表示が肖像で▲を検出できないため、出撃成立の巻き戻しをせず出撃元の一覧・到着・期限の実測に委ねる')
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
            _note_sortie_launch(mem, sortie)
            probes.pop(step, None)
            _record(mem, 'sortie_launch_confirmed', chart_step=step,
                    general=probe.get('general'),
                    observed_metric={'markers': [list(b) for b in markers],
                                     'new_markers': [list(b) for b in fresh]},
                    reason='出撃コミット後の全体マップに基準に無い部隊▲を確認')
            continue
        probe['reads'] = int(probe.get('reads') or 0) + 1
        # A move into our own castle draws no 部隊▲ (the target is friendly);
        # g1163 2026-11-13 12:3x-14:2x spent five surveys per オシロガ>ドンドン
        # move before the source roster receipt. One survey is enough there.
        own_target = probe.get('target') in _owned(mem)
        limit = 1 if own_target else LAUNCH_PROBE_READS
        if probe['reads'] >= limit:
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


def _month_sorties(mem):
    """Launch ledger for the current game month (docich#4642).

    The monthly roster/status scan and its battle resumes can hold the field
    for so long that whole months pass with no sortie. ``launches`` counts
    confirmed departures toward enemy castles within the month so the scan
    can yield the map until the first one is out.
    """
    ms = mem.setdefault('month_sorties', {})
    scope = [mem.get('chapter'), mem.get('month')]
    if ms.get('scope') != scope:
        ms['scope'] = scope
        ms['launches'] = 0
    return ms


def _note_sortie_launch(mem, row):
    # A move into our own castle is a transfer, not a 出撃 toward the enemy.
    if (row or {}).get('purpose') == 'move':
        return
    _month_sorties(mem)['launches'] = int(_month_sorties(mem).get('launches') or 0) + 1


def settle_launch_probes_from_roster(mem, castle, generals, *, complete=True):
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
        if not complete and not probe.get('blind'):
            # One page is not the roster: while the marker survey may still
            # find the unit, a name that merely scrolled off proves nothing.
            continue
        probes.pop(step, None)
        sortie = _sorties(mem).get(step)
        if isinstance(sortie, dict):
            sortie['launch_confirmed'] = True
            sortie.pop('launch_kept', None)
        _note_sortie_launch(mem, sortie if isinstance(sortie, dict) else probe)
        # The launch is confirmed: the equivalent-order miss budget it may
        # have accrued belongs to a failed attempt, not to this one.
        for key, row in list((mem.get('equivalent_launch_miss') or {}).items()):
            if isinstance(row, dict) and row.get('step') == step:
                (mem.get('equivalent_launch_miss') or {}).pop(key, None)
        _record(mem, 'sortie_launch_confirmed', chart_step=step, general=general,
                observed_metric={'castle': castle, 'general': general, 'roster': roster[:12],
                                 'complete': bool(complete)},
                reason=('出撃元の将軍一覧に出撃した将軍がいないため出撃成立を確認' if complete
                        else '出撃元の将軍一覧の1ページに出撃した将軍がいないため出撃成立とみなす'))


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
    for key in ('rare_card_kit', 'strong_card_kit', 'dropper_card_kit', 'sortie_actor_miss'):
        (mem.get(key) or {}).pop(step, None)
    for key in ('general_override', 'source_override', 'card_override'):
        (_plan_region(mem).get(key) or {}).pop(step, None)
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
    # A view that shows only the hero's own marker cannot distinguish a new
    # unit mark, so "no marker" is not evidence that the launch failed
    # (g1190 2026-11-18: タプロベイン→タタール failed twice on the same single
    # marker while 12 generals stood at the source). Give the retry room
    # instead of ending the month's order.
    miss_limit = LAUNCH_MISS_LIMIT
    if len(markers or ()) == 1:
        miss_limit = LAUNCH_MISS_LIMIT * 3
    if misses[step] >= miss_limit:
        _plan_region(mem).setdefault('orders', {})[step] = 'failed'
        drop_retired_step_context(mem)
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


def _sortie_is_current_for_departure_request(mem, step, request):
    """Whether a sortie record belongs to this exact confirmed departure.

    Transit continuation and overdue relaunches reuse an order step, so an
    old arrived/overdue record may remain under that step while a new departure
    request is being retried. Only a receipt-backed execution with the same
    confirmation tick, actor and source can protect the new request. Untagged
    active marches remain fail-closed because their provenance is unknown.
    """
    sortie = _sorties(mem).get(step)
    if not isinstance(sortie, dict):
        return False
    status = sortie.get('status')
    if status not in ('en_route', 'arrived', 'launched_unconfirmed', 'overdue'):
        return False

    snapshot = request.get('snapshot')
    binding = snapshot.get('departure_binding') if isinstance(snapshot, dict) else None
    if isinstance(binding, dict):
        request_tick = binding.get('confirm_tick')
        sortie_tick = sortie.get('confirmation_tick')
        if type(request_tick) is int and type(sortie_tick) is int:
            return (request_tick == sortie_tick
                    and sortie.get('general') == request.get('general') == binding.get('general')
                    and sortie.get('source') == request.get('source') == binding.get('source'))

    # Arrived/overdue records are not live marches; without a matching
    # confirmation binding they may belong to an earlier request.
    if status in ('arrived', 'overdue'):
        return False
    # An untagged active march cannot be attributed safely; preserve the guard.
    return True


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
    orders = _plan_region(mem).get('orders') or {}
    used = int(misses.get(step) or 0)
    # A stale release must not consume the retry budget of a different or
    # already-settled order. Only the request that still owns the active step
    # is allowed to count as a miss.
    if (request.get('step') != step or _plan_region(mem).get('active') != step
            or orders.get(step) in ('launched', 'launched_unconfirmed')):
        _record(mem, 'sortie_departure_not_accepted', chart_step=step,
                **(request.get('context') or {}),
                observed_metric={'request_misses': used,
                                 'request_step': request.get('step'),
                                 'active': _plan_region(mem).get('active'),
                                 'order_state': orders.get(step)},
                resulting_event='request_released_not_owned',
                reason='出撃要求が現在の指示・手順の所有に結び付かないため失敗とは記録せず解放する')
        return FlowResult('departure_request', 'release', [], reason,
                          retry={'used': used, 'limit': DEPARTURE_REQUEST_LIMIT},
                          permitted_recovery='reconfirm')
    misses[step] = used + 1
    if misses[step] >= DEPARTURE_REQUEST_LIMIT:
        # An untagged live march, or a receipt-backed march for this exact
        # confirmation, is still evidence that the departure may have landed.
        if _sortie_is_current_for_departure_request(mem, step, request):
            misses[step] = DEPARTURE_REQUEST_LIMIT - 1
            _record(mem, 'sortie_departure_not_accepted', chart_step=step,
                    **(request.get('context') or {}),
                    observed_metric={'request_misses': misses[step],
                                     'request_step': request.get('step'),
                                     'active': _plan_region(mem).get('active'),
                                     'order_state': orders.get(step)},
                    resulting_event='request_released_not_owned',
                    reason='今回の出撃に結び付く行軍記録が残るため失敗を留保して解放する')
            return FlowResult('departure_request', 'release', [], reason,
                              retry={'used': misses[step], 'limit': DEPARTURE_REQUEST_LIMIT},
                              permitted_recovery='reconfirm')
        if request.get('status') == 'sent':
            # A journaled A can be accepted a beat after the last picker read.
            # Require several released sent cycles before declaring it ignored.
            sent_misses = flow(mem).setdefault('departure_sent_miss', {})
            sent_misses[step] = int(sent_misses.get(step) or 0) + 1
            if sent_misses[step] < DEPARTURE_SENT_MISS_LIMIT:
                misses[step] = DEPARTURE_REQUEST_LIMIT - 1
                _record(mem, 'sortie_departure_not_accepted', chart_step=step,
                        **(request.get('context') or {}),
                        observed_metric={'request_misses': misses[step],
                                         'sent_misses': sent_misses[step]},
                        resulting_event='sent_release_held',
                        reason='送信記録済みの要求がまだ受理の余地を残すため失敗を留保して解放する')
                return FlowResult('departure_request', 'release', [], reason,
                                  retry={'used': sent_misses[step], 'limit': DEPARTURE_SENT_MISS_LIMIT},
                                  permitted_recovery='reconfirm')
        used = misses.pop(step)
        sent_used = (flow(mem).get('departure_sent_miss') or {}).pop(step, None)
        context = {k: v for k, v in (request.get('context') or {}).items()
                   if k != 'deviation_reason'}
        _finish_order(mem, 'failed', **context, target=order['target'],
                      deviation_reason='出撃要求が送信・受理されない',
                      observed_metric={'request_misses': used, 'sent_misses': sent_used},
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



def _precise_y_landing(mem, order) -> bool:
    """A Y jump placed the marker on the measured cell within ``Y_JUMP_TOL``.

    The coarse landing (moves exhausted, residual up to ``Y_JUMP_FINAL_TOL`` ≈
    a cell and a half) still needs a roof to prove it; the TOL landing is on
    the cell itself (owner 2026-11-16: Y jump for the rough placement, fine
    tune after), so a clipped or unreadable roof must not reject it.
    """
    if (mem.get('y_jumped') or {}).get(order['step']) != order['target']:
        return False
    res = (mem.get('y_jumped_res') or {}).get(order['step'])
    return bool(res and abs(res[0]) <= Y_JUMP_TOL and abs(res[1]) <= Y_JUMP_TOL)


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
        # Chapter 9's moving boss (owner 2026-11-16): the first leg lands on
        # the waypoint north of イデ！, not on the skull itself — verifying
        # against the bare boss cell cancelled every first leg. The probe cell
        # is the actual aim point (waypoint while unrelaunched, the boss cell
        # once re-armed); other chapters get their assigned probe candidate.
        cell = (boss_probe_cell(mem, order['target'], order['step'])
                or chart.castles(mem.get('chapter') or 0).get(order['target']))
        world = update_world(screen, mem, frame, order['target'])
        return bool(cell and world
                    and abs(world[0] - cell[0]) <= BOSS_MARKER_CELL_PX
                    and abs(world[1] - cell[1]) <= BOSS_MARKER_CELL_PX)
    if _precise_y_landing(mem, order):
        # The Y view put the marker on the measured cell (owner 2026-11-16:
        # jump for the rough placement, fine-tune after). A clipped roof at
        # the screen edge is no reason to doubt that placement and loop.
        return True
    roofs = castle_roofs(frame)
    if not roofs and (mem.get('y_jumped') or {}).get(order['step']) == order['target']:
        return True                           # winter palette: trust the Y jump (g421 18:13)
    want = 'own' if order['target'] in _owned(mem) else 'enemy'
    return any(r['kind'] == want
               and not r.get('clipped')
               and abs(r['target'][0] - s[0]) <= UNDER_CURSOR_PX
               and abs(r['target'][1] - s[1]) <= UNDER_CURSOR_PX
               for r in roofs)


CELL_EVIDENCE_ROOF_PX = 24      # a castle roof this close to the marker backs a cell match


def _cell_match_evidence(screen, mem, order, frame) -> bool:
    """The screen backs a dead-reckoned cell match for the target.

    g1190 2026-11-16 (chapter 9): the estimate sat on オシロガ's cell while
    the marker stood over the sea north of the island, and the accepted A
    sent the general to the coast where he camped. A Y jump to the target
    lands on its cell; otherwise a castle roof must stand by the marker —
    a roof of the *expected* owner, fully in frame. A clipped roof's box is
    cut by the screen edge, so its bottom-centre anchor does not mark the
    castle's selecting cell: confirming beside it sent アルマムーン→ハヤク
    generals marching 900 ticks to nowhere (g1190 2026-11-16 17:20).
    """
    if (mem.get('y_jumped') or {}).get(order['step']) == order['target']:
        return True
    s = _cursor(screen)
    if not s or frame is None:
        return False
    if (chart.is_boss_castle(order['target'])
            and (mem.get('chapter') or 0) == chart.MOVING_BOSS_CHAPTER):
        # The moving boss's aim points — the north waypoint or the skull's
        # cell — have no roof beside the marker. The anchored estimate is the
        # only evidence either landing can carry.
        return not mem.get('uncertain')
    want = 'own' if order['target'] in _owned(mem) else 'enemy'
    gate = chart.gateway(mem.get('chapter') or 0)
    # The gateway hub is the one castle both halves share and its flag is
    # drawn apart from the ordinary castle rows (#4551: every アルマムーン→
    # タタール move died on target_not_under_marker while the map/y jump
    # estimate was sound). Its roof is still the landmark the cell match
    # stands on, so any owner tone beside the marker backs the hub cell.
    hub_target = bool(gate and order['target'] == gate.get('hub'))
    return any((r['kind'] == want or hub_target) and not r.get('clipped')
               and abs(r['target'][0] - s[0]) <= CELL_EVIDENCE_ROOF_PX
               and abs(r['target'][1] - s[1]) <= CELL_EVIDENCE_ROOF_PX
               for r in castle_roofs(frame))


def _unverified_target(screen, mem, order, frame=None):
    step = order['step']
    misses = mem.setdefault('target_miss', {})
    misses[step] = misses.get(step, 0) + 1
    # The camera estimate can already sit on the chart cell while the roofs are
    # unreadable (g1190 2026-11-16 20-9: 132 arrival_unverified and 50
    # target_not_under_marker in one window while 24 タオ attacks produced no
    # battle). Accept the cell match instead of cancelling the sortie forever,
    # but only with evidence on screen (``_cell_match_evidence``): the bare
    # estimate confirmed a marker over the sea (owner 2026-11-16, ch9).
    cell = chart.castles(mem.get('chapter') or 0).get(order['target'])
    if chart.is_boss_castle(order['target']):
        # A boss order's aim cell is its probe cell (chapter 9's first leg is
        # the waypoint north of イデ！), not the bare chart cell.
        cell = boss_probe_cell(mem, order['target'], order['step']) or cell
    cursor = mem.get('cursor')
    if (cell and isinstance(cursor, (list, tuple)) and len(cursor) == 2
            and abs(cursor[0] - cell[0]) <= BOSS_MARKER_CELL_PX
            and abs(cursor[1] - cell[1]) <= BOSS_MARKER_CELL_PX
            and _cell_match_evidence(screen, mem, order, frame)):
        misses.pop(step, None)
        mem['uncertain'] = False
        _record(mem, 'target_cell_accepted', chart_step=step, target=order['target'],
                observed_metric={'cursor': list(cursor), 'cell': list(cell),
                                 'misses': misses.get(step)},
                reason='屋根が読めないため、カメラ推定がチャートのセルに一致することで出撃先を確定する')
        # The callers use this function's value as the next action list, so the
        # acceptance must confirm the marker here (a bare True stalled the flow
        # right after the record). The A is the measured sortie confirmation.
        return [pad('a')]
    if misses[step] < TARGET_MISS_LIMIT:
        mem['uncertain'] = True               # re-anchor on roofs before confirming
        mem.pop('anchor', None)
        _record(mem, 'target_not_under_marker', chart_step=step, target=order['target'],
                observed_metric={'cursor': mem.get('cursor'), 'marker': list(_cursor(screen) or ()),
                                 'want': ('own' if order['target'] in _owned(mem) else 'enemy'),
                                 'roofs': ([{'kind': r.get('kind'),
                                             'target': list(r.get('target') or ()),
                                             'clipped': bool(r.get('clipped'))}
                                            for r in castle_roofs(frame)][:8]
                                           if frame is not None else None),
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
