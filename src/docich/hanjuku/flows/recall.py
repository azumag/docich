"""手順層: camp recall and the hero's return to a castle."""
from __future__ import annotations

from ..os_layer.pixels import Frame
from ..os_layer.screen import Screen, castle_roofs, own_camps
from ... import hanjuku_chart as chart
from ..decision_log import _record
from ..os_layer.commands import menu_to, pad
from ..os_layer.navigation import _start_y_jump
from ..os_layer.readers import _current, _cursor, is_camp_menu
from ..os_layer.world import NAME
from ..strategy.orders import _recall_goal


MENU_HOLD_LIMIT = 10           # arrived-but-unanchored holds after a failed castle menu

RECALL_LIMIT = 90              # observations for one camp recall
RECALL_ARRIVE_PX = 4           # cursor cell onto the tent
RECALL_CONFIRM_PX = 4          # marker onto the own castle's selecting cell
RECALL_SKIP_TICKS = 400        # observations without recalls after a skipped picker


def camp_recall_step(screen: Screen, mem, frame):
    """Observe named retreaters before reusing the existing camp recall route."""
    from . import camp_recheck as hanjuku_camp_recheck
    return hanjuku_camp_recheck.step(screen, mem, frame, _camp_recall_step)


def _camp_recall_step(screen: Screen, mem, frame, *, observed_camps=None):
    """Owner rule (2026-09-28): a camp (野営) seen on screen is recalled.

    Measured in the isolated probe: A on our tent opens
    いどう/ステータス/キャンプ/きかん; its observed cursor must reach きかん before A
    opens the destination marker (kind map_target), and A over an own castle's
    roof cell sends the general home. The destination is the nearest visible
    own castle (補給できる城). ``None`` means "no recall in flight": the
    caller keeps its ordinary map steering.
    """
    if screen.kind in {'battle', 'battle_menu', 'egg_battle_menu', 'monster_menu',
                       'egg_choice_menu', 'attack_started', 'defense_started', 'boss_attack_started'}:
        return None  # combat owns this screen; keep the recall intention pending
    if mem.get('battle'):
        return [] if screen.kind == 'map' else None  # first result frame is not a finished battle
    state = mem.get('recall')
    if state is None:
        if screen.kind != 'map' or frame is None:
            return None
        camps = own_camps(frame) if observed_camps is None else observed_camps
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
        # A stuck recall must leave the screen it is on, not only a camp menu.
        # map_target/world_map are the actual "return" screens; B closes them.
        # A camp menu is closed with B as before; other screens keep the old
        # no-input behaviour to avoid inventing inputs on text/battle frames.
        if is_camp_menu(screen) or screen.kind in ('map_target', 'world_map'):
            return [pad('b')]
        return []
    stage = state.get('stage')
    if stage == 'hero_focus':
        if screen.kind != 'map':
            return []                  # battle results and messages fade out first
        state['stage'], state['steps'] = 'hero_open', 0
        return [pad('select')]         # measured: SELECT centres the cursor on the hero
    if stage == 'hero_open':
        if screen.kind != 'map':
            return []
        _observe_recall_origin(screen, mem, frame, state)
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
        camps = (own_camps(frame) if frame is not None else []) if observed_camps is None else observed_camps
        if not camps:
            missing = state['camp_missing_reads'] = int(state.get('camp_missing_reads') or 0) + 1
            if missing == 1:
                _record(mem, 'camp_position_unconfirmed', observed_metric={'previous_target': state.get('target')},
                        reason='現在の画面に自軍tentを確認できず、保存した画面座標では決定しない')
            if missing >= 3:
                mem['recall_verification'] = {**state, 'status': 'camp_unobserved'}
                mem.pop('recall', None)
                mem['uncertain'] = True
            return []  # an old screen coordinate never licenses a camp-opening A
        state.pop('camp_missing_reads', None)
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
            _observe_recall_origin(screen, mem, frame, state)
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


def _observe_recall_origin(screen, mem, frame, state):
    """Bind distance to the selected unit's current map, never the R home cursor.

    Two or more roofs must agree on one camera translation. A tied
    constellation or an old integrated cursor cannot locate this unit.
    """
    cursor = _cursor(screen)
    if screen.kind != 'map' or not isinstance(frame, Frame) or cursor is None:
        return
    from ..os_layer.world import observed_cursor_cell
    origin = observed_cursor_cell(screen, mem, frame)
    if origin is None:
        state.pop('origin', None)
    else:
        state['origin'] = origin


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
            from ..os_layer.roster import dirty_status
            general = state.get('general') or (NAME if state.get('hero') else None)
            dirty_status(mem, [general] if general else None, fields=('hp', 'location'))
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
