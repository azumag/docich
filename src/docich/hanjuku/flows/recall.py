"""手順層: camp recall and the hero's return to a castle."""
from __future__ import annotations

from ..os_layer.pixels import Frame
from ..os_layer.screen import Screen, castle_roofs, own_camps
from ... import hanjuku_chart as chart
from ..decision_log import _record
from ..os_layer.commands import menu_to, pad
from ..os_layer.navigation import _start_y_jump
from ..os_layer.readers import _current, _cursor, _options, fort_list_open, is_camp_menu, is_fort_menu
from ..os_layer.world import NAME
from ..strategy.orders import _fort_recall_goal, _recall_goal
from ..state.regions import flow as _flow, journal as _journal


MENU_HOLD_LIMIT = 10           # arrived-but-unanchored holds after a failed castle menu

RECALL_LIMIT = 90              # observations for one camp recall
RECALL_ARRIVE_PX = 4           # cursor cell onto the tent
RECALL_CONFIRM_PX = 4          # marker onto the own castle's selecting cell
RECALL_SKIP_TICKS = 400        # observations before retrying the same failed camp
RECALL_SKIP_RADIUS = 48        # screen pixels: cover the measured camera shift for the same camp
RECALL_SKIP_HISTORY = 6        # other failed camps whose hold is still running
LOST_CAMP_RETRY_TICKS = 60     # first wait after a tent only went out of sight, doubling up to the skip
FORT_RETRY_TICKS = 20          # first wait before the next general leaves the same 砦, doubling likewise
FORT_STATUS_PRESS_EVERY = 4    # observations of the status panel between two A presses
MENU_OPEN_WAIT = 3             # bare map observations after A on the tent before pressing again
MENU_REOPEN_LIMIT = 3          # presses again per recall
DOME_SCREEN_WAIT = 6           # other-screen observations after A on a dome before B
BOSS_RETRY_TICKS = 120         # first wait before a camp is sent at the moving boss again
BOSS_JUMP_LIMIT = 2            # Y jumps to the boss per redirect


def boss_phase(mem) -> bool:
    """Chapter 9 with every road castle ours: the moving boss is the only target.

    Before that, a camped general is an ordinary straggler (a lost battle's
    retreat, say) and is recalled home as usual.
    """
    chapter = mem.get('chapter')
    if chapter != chart.MOVING_BOSS_CHAPTER or not chart.corridor(chapter):
        return False
    from ..os_layer.world import _owned
    return chart.corridor_next(chapter, _owned(mem)) is None


def boss_redirect_goal(mem):
    """The boss castle a camped general is re-sent at, or None (owner 2026-11-09).

    Chapter 9's boss moves, so an army sent at the skull's old cell ends up
    camped on the road. Such a camp is not recalled home: it is ordered at the
    boss's current Y-view cell instead. Only a fresh skull measurement
    (``chart.moving_boss_cell``) counts, never a stale cell, and only in the
    boss phase.
    """
    chapter = mem.get('chapter')
    if not boss_phase(mem) or not chart.moving_boss_cell(chapter):
        return None
    return chart.boss_castle(chapter)


def camp_stays(mem) -> bool:
    """A camp is never recalled home in the moving boss's phase (owner 2026-11-09)."""
    return boss_phase(mem)


def _camp_target(value):
    return (isinstance(value, (list, tuple)) and len(value) == 2
            and all(type(axis) is int for axis in value))


def _remember_recall_target(mem, state):
    """Remember which visible camp owns a future retry cooldown.

    ``recall_skip`` remains the old tick-only shape for compatibility.  This
    companion scope prevents one troublesome camp from suppressing every other
    visible camp for the whole cooldown.
    """
    target = state.get('target') if isinstance(state, dict) else None
    chapter = mem.get('chapter')
    if not _camp_target(target) or type(chapter) is not int or chapter <= 0:
        _flow(mem).pop('recall_skip_target', None)
        return
    scoped = {'chapter': chapter, 'target': list(target)}
    if isinstance(state.get('camp_kind'), str):
        scoped['kind'] = state['camp_kind']
    _flow(mem)['recall_skip_target'] = scoped


def _stash_previous_skip(mem):
    """Keep the hold of the camp that failed before this one.

    The single ``recall_skip`` slot held only the latest failure, so two stuck
    camps released each other: A failed, B was picked, B failed, A (no longer
    held) was picked again. g1133 2026-11-05 14:16 retried ``[52,148]`` and
    ``[11,46]`` alternately every few seconds and spent the short map windows
    that the boss order needed.
    """
    flow = _flow(mem)
    skip, scoped = flow.get('recall_skip'), flow.get('recall_skip_target')
    chapter, now = mem.get('chapter'), int(mem.get('tick') or 0)
    history = [h for h in flow.get('recall_skip_history') or [] if _history_alive(h, chapter, now)]
    if (isinstance(skip, dict) and isinstance(scoped, dict) and _camp_target(scoped.get('target'))
            and scoped.get('chapter') == chapter and type(skip.get('tick')) is int):
        ticks = int(flow.get('recall_skip_ticks') or RECALL_SKIP_TICKS)
        entry = {'chapter': chapter, 'target': list(scoped['target']), 'tick': skip['tick'],
                 'ticks': ticks, **({'kind': scoped['kind']} if isinstance(scoped.get('kind'), str) else {})}
        history = [h for h in history if not _same_skipped_camp(entry, h)] + [entry]
    if history:
        flow['recall_skip_history'] = history[-RECALL_SKIP_HISTORY:]
    else:
        flow.pop('recall_skip_history', None)


def _history_alive(entry, chapter, now):
    return (isinstance(entry, dict) and entry.get('chapter') == chapter
            and _camp_target(entry.get('target')) and type(entry.get('tick')) is int
            and type(entry.get('ticks')) is int and 0 <= now - entry['tick'] < entry['ticks'])


def _arm_recall_skip(mem, state, base=None):
    """Hold the failed camp back; ``base`` starts a doubling wait for a mere retry.

    A tent hidden by a passing unit for three observations is not a failed
    camp. g1097 2026-11-01 19:24 lost one so, and the 400-observation hold then
    left its 砦 idle on screen for ten minutes. Repeats of the same camp double
    the wait up to the full hold, which keeps the bound #3040 set after the
    same tent was lost eight times in eight minutes.
    """
    _stash_previous_skip(mem)
    _flow(mem)['recall_skip'] = {'tick': int(mem.get('tick') or 0)}
    _remember_recall_target(mem, state)
    _flow(mem).pop('recall_skip_ticks', None)
    scoped = _flow(mem).get('recall_skip_target')
    if base is None or scoped is None:
        _flow(mem).pop('recall_retry', None)
        return
    old = _flow(mem).get('recall_retry')
    repeat = isinstance(old, dict) and old.get('chapter') == scoped['chapter'] and _same_skipped_camp(
        {'target': tuple(scoped['target']), 'kind': scoped.get('kind')}, old)
    count = int(old.get('count') or 0) + 1 if repeat else 1
    _flow(mem)['recall_retry'] = {**scoped, 'count': count}
    _flow(mem)['recall_skip_ticks'] = min(RECALL_SKIP_TICKS, base * 2 ** (count - 1))


def _same_skipped_camp(camp, scoped):
    if not isinstance(camp, dict) or not isinstance(scoped, dict):
        return False
    target, old = camp.get('target'), scoped.get('target')
    if not _camp_target(target) or not _camp_target(old):
        return False
    # A tent that became a 砦 is a changed camp with a different menu, so a
    # failure on the tent says nothing about it (g1097 19:24 lost the tent as a
    # second general walked in, and the dome stayed on hold).
    if camp.get('kind') != scoped.get('kind'):
        return False
    return (abs(target[0] - old[0]) <= RECALL_SKIP_RADIUS
            and abs(target[1] - old[1]) <= RECALL_SKIP_RADIUS)


def _filter_single_recall_skip(mem, camps):
    """Apply a retry cooldown to only the camp that actually failed.

    This is deliberately reused both when choosing a camp and while tracking it:
    otherwise the next ``to_camp`` re-detection can immediately switch back to
    the nearer failed camp and defeat the scoped cooldown.
    """
    camps = list(camps or [])
    skip = _flow(mem).get('recall_skip')
    window = int(_flow(mem).get('recall_skip_ticks') or RECALL_SKIP_TICKS)
    age = (int(mem.get('tick') or 0) - int(skip.get('tick') or 0)
           if isinstance(skip, dict) else window)
    if not 0 <= age < window:
        _flow(mem).pop('recall_skip', None)
        _flow(mem).pop('recall_skip_target', None)
        _flow(mem).pop('recall_skip_ticks', None)
        return camps
    scoped = _flow(mem).get('recall_skip_target')
    if not isinstance(scoped, dict) or not _camp_target(scoped.get('target')):
        # Hotloaded/legacy state has no safe camp identity. Keep the historical
        # fail-closed global cooldown for that one window.
        return []
    if scoped.get('chapter') != mem.get('chapter'):
        # Screen coordinates are chapter-local. Never carry a scoped cooldown
        # into a new chapter.
        _flow(mem).pop('recall_skip', None)
        _flow(mem).pop('recall_skip_target', None)
        return camps
    return [camp for camp in camps if not _same_skipped_camp(camp, scoped)]


def _filter_recall_skip(mem, camps):
    """Camps not held back by the latest failure nor by any earlier still-running hold."""
    camps = _filter_single_recall_skip(mem, camps)
    flow = _flow(mem)
    history = flow.get('recall_skip_history') or []
    if not history:
        return camps
    chapter, now = mem.get('chapter'), int(mem.get('tick') or 0)
    alive = [h for h in history if _history_alive(h, chapter, now)]
    if alive:
        flow['recall_skip_history'] = alive
    else:
        flow.pop('recall_skip_history', None)
    return [camp for camp in camps if not any(_same_skipped_camp(camp, h) for h in alive)]

def camp_recall_step(screen: Screen, mem, frame):
    """Observe named retreaters before reusing the existing camp recall route."""
    from . import camp_recheck as hanjuku_camp_recheck
    return hanjuku_camp_recheck.step(screen, mem, frame, _camp_recall_step)


def _mark_recall_interrupted(mem):
    """Remember that a battle/event scene took the foreground from a recall.

    The recall is planned one observation and sent on the next. When a
    defense/attack message starts in between (g1139 2026-11-06 15:18:18 JST:
    カルーン じょうが てきに せめこまれました right after the recall plan), the
    A advances that scene instead of confirming the destination. The pending
    recall state stays untouched (a combat foreground must not rewrite it),
    but the dispatch step uses this marker to retry soon after the battle
    instead of serving the long failed-camp skip.
    """
    state = _flow(mem).get('recall')
    if not isinstance(state, dict):
        return
    if state.get('stage') != 'await_dispatch' or state.get('input_sent'):
        return
    _flow(mem)['recall_interrupted'] = int(mem.get('tick') or 0)


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
        _mark_recall_interrupted(mem)
        return None  # combat owns this screen; keep the recall intention pending
    if mem.get('battle'):
        _mark_recall_interrupted(mem)
        return [] if screen.kind == 'map' else None  # first result frame is not a finished battle
    state = _flow(mem).get('recall')
    if state is None:
        if screen.kind != 'map' or frame is None:
            return None
        boss_goal = boss_redirect_goal(mem)
        if camp_stays(mem) and boss_goal is None:
            return None     # moving boss unseen: the camp waits where it is, never goes home
        camps = own_camps(frame) if observed_camps is None else observed_camps
        camps = _filter_recall_skip(mem, camps)
        if not camps:
            return None
        cursor = _cursor(screen)
        camp = min(camps, key=lambda c: (abs(c['target'][0] - cursor[0])
                                         + abs(c['target'][1] - cursor[1])) if cursor else 0)
        state = _flow(mem)['recall'] = {'stage': 'to_camp', 'target': list(camp['target']),
                                 'last_seen': list(camp['target']), 'steps': 0,
                                 **({'camp_kind': camp['kind']} if camp.get('kind') else {}),
                                 **({'boss': True, 'goal': boss_goal} if boss_goal else {})}
        if boss_goal:
            _reset_boss_jump(mem)
        _record(mem, 'camp_found', observed_metric={'camp': list(camp['target']),
                                                    **({'kind': camp['kind']} if camp.get('kind') else {}),
                                                    **({'boss_redirect': boss_goal} if boss_goal else {})},
                reason=('画面に自軍の野営を見つけたため、帰還させずボスの現在位置へ再出撃させる'
                        if boss_goal else
                        '画面に自軍の野営を見つけたため補給できる自軍城への帰還を指示する'))
    if state.get('stage') == 'await_dispatch':
        return _recall_dispatch_step(screen, mem, state)
    state['steps'] = int(state.get('steps') or 0) + 1
    if state['steps'] > RECALL_LIMIT:
        _record(mem, 'camp_recall_aborted',
                observed_metric={'stage': state.get('stage'), 'steps': state['steps']},
                reason='野営の帰還指示が上限内に完了しないため断念し位置を再測定する')
        _flow(mem).pop('recall', None)
        mem['uncertain'] = True
        _arm_recall_skip(mem, state)
        # A stuck recall must leave the screen it is on, not only a camp menu.
        # map_target/world_map are the actual "return" screens; B closes them.
        # A camp menu is closed with B as before; other screens keep the old
        # no-input behaviour to avoid inventing inputs on text/battle frames.
        if (is_camp_menu(screen) or is_fort_menu(screen) or screen.kind in ('map_target', 'world_map')
                or str(state.get('stage')).startswith('fort_')):
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
            _flow(mem).pop('recall', None)
            mem['uncertain'] = True
            return [pad('b')] if screen.kind in ('world_map', 'map_target') else []
        # Re-detect every step: a tent clipped by the top edge is targeted
        # above the screen, and the cursor servo there scrolls the camera
        # until the flag shows and the real selecting cell is known. Keep the
        # failed-camp cooldown applied here too, not only at initial selection.
        camps = (own_camps(frame) if frame is not None else []) if observed_camps is None else observed_camps
        camps = _filter_recall_skip(mem, camps)
        if not camps:
            # Owner 2026-11-04: キャンプにカーソルが重なると野営画像を失い、
            # 帰還が中断していた。直前の実測でカーソル位置にあると確認できた
            # targetなら、一度だけAでメニューを開く（メニュー段階が空振りを
            # 有界に処理する）。未観測の古い座標は従来どおり信頼しない。
            cursor = _cursor(screen)
            target = state.get('target')
            last = state.get('last_seen')
            if (not state.get('occluded_enter') and cursor
                    and _camp_target(last) and _camp_target(target)
                    and abs(last[0] - target[0]) <= RECALL_ARRIVE_PX
                    and abs(last[1] - target[1]) <= RECALL_ARRIVE_PX
                    and abs(cursor[0] - target[0]) <= RECALL_ARRIVE_PX
                    and abs(cursor[1] - target[1]) <= RECALL_ARRIVE_PX):
                state['occluded_enter'] = True
                _observe_recall_origin(screen, mem, frame, state)
                state['stage'], state['steps'] = 'menu', 0
                _record(mem, 'camp_enter', observed_metric={'camp': list(target), 'occluded': True},
                        reason='カーソルが野営画像を覆っているため、直前に実測したtargetを信頼してきかんメニューを開く')
                return [pad('a')]
            missing = state['camp_missing_reads'] = int(state.get('camp_missing_reads') or 0) + 1
            if missing == 1:
                _record(mem, 'camp_position_unconfirmed', observed_metric={'previous_target': state.get('target')},
                        reason='現在の画面に自軍tentを確認できず、保存した画面座標では決定しない')
            if missing >= 3:
                _flow(mem)['recall_verification'] = {**state, 'status': 'camp_unobserved'}
                _flow(mem).pop('recall', None)
                mem['uncertain'] = True
                # g1075 2026-10-29 23:55-00:03: a tent at the top edge beside
                # the order's source castle was found and lost 8 times in 8
                # minutes, and the retake from that castle never left. Wait
                # as after an aborted recall before chasing it again.
                _arm_recall_skip(mem, state, LOST_CAMP_RETRY_TICKS)
            return []  # an old screen coordinate never licenses a camp-opening A
        state.pop('camp_missing_reads', None)
        cursor = _cursor(screen)
        camp = min(camps, key=lambda c: (abs(c['target'][0] - cursor[0])
                                         + abs(c['target'][1] - cursor[1])) if cursor else 0)
        state['target'] = list(camp['target'])
        # The cursor sprite can cover the tent once the servo is on it; keep
        # the last real detection so the arrival step can trust it.
        state['last_seen'] = list(camp['target'])
        if camp.get('kind'):
            state['camp_kind'] = camp['kind']
        else:
            state.pop('camp_kind', None)
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
    if stage == 'menu' and is_fort_menu(screen):
        enter_fort(mem, state)
        stage = 'fort_menu'
    if stage in FORT_STAGES:
        actions = _fort_step(screen, mem, state)
        if actions is not None:
            return actions
        stage = state.get('stage')
    if stage == 'menu':
        if state.get('hero') and (screen.kind in ('castle_menu', 'general_list')
                                  or (screen.kind == 'map' and state['steps'] > 4)):
            # SELECT put the cursor on a castle (the hero is inside one) or on
            # nothing: he is not marching, so there is nothing to call off.
            _flow(mem).pop('recall', None)
            _record(mem, 'hero_recall_skipped', observed_metric={'screen': screen.kind},
                    reason='主人公の部隊メニューが開かない（城内など）ため帰還指示を取り消す')
            return [pad('b')] if screen.kind != 'map' else []
        if not is_camp_menu(screen) and screen.kind != 'map' and state.get('camp_kind') == 'dome':
            # The dome opens the measured 砦 menu above; anything else on it
            # is unmeasured. Keep its frame and leave it.
            others = state['menu_other'] = int(state.get('menu_other') or 0) + 1
            if others < DOME_SCREEN_WAIT:
                return []
            _flow(mem).pop('recall', None)
            mem['uncertain'] = True
            _arm_recall_skip(mem, state)
            _record(mem, 'camp_group_screen', resulting_event='unclassified_screen_closed',
                    observed_metric={'camp': state.get('target'), 'screen': screen.kind,
                                     'text': (screen.text or '')[:80]},
                    reason='ドーム型の野営で決定した後が実測済みのメニューでないため、画面を記録してBで閉じる')
            return [pad('b')]
        if not is_camp_menu(screen):
            if screen.kind == 'map' and not state.get('hero'):
                # Back on the bare map: the A on the tent opened nothing or
                # an event swallowed it (g1075 2026-10-29 14:57:27: a defense
                # message right after the A, then 61 silent map observations
                # with the tent in view until 15:07). Go back onto the tent.
                waits = state['menu_map_waits'] = int(state.get('menu_map_waits') or 0) + 1
                if waits >= MENU_OPEN_WAIT:
                    reopens = state['menu_reopens'] = int(state.get('menu_reopens') or 0) + 1
                    state.pop('menu_map_waits', None)
                    if reopens > MENU_REOPEN_LIMIT:
                        _flow(mem).pop('recall', None)
                        mem['uncertain'] = True
                        _arm_recall_skip(mem, state)
                        _record(mem, 'camp_recall_aborted', resulting_event='camp_menu_not_opened',
                                observed_metric={'camp': state.get('target'), 'reopens': reopens - 1},
                                reason='野営で決定し直しても野営メニューが開かないため帰還指示を断念する')
                        return []
                    state['stage'] = 'to_camp'
                    _record(mem, 'camp_menu_not_opened', observed_metric={
                                'camp': state.get('target'), 'map_observations': waits, 'reopen': reopens},
                            reason='野営で決定したが野営メニューが開かずマップに戻ったため、野営へ合わせ直して決定し直す')
            return []              # the window is still fading in
        state.pop('menu_map_waits', None)
        choice = 'いどう' if state.get('boss') else 'きかん'
        move = menu_to(screen, choice)
        if move is None:
            misses = state['cursor_misses'] = int(state.get('cursor_misses', 0)) + 1
            if misses == 1:
                _record(mem, 'camp_menu_unread', chart_step=None, resulting_event='cursor_unconfirmed',
                        reason='野営メニューの実カーソルを読めないため帰還の確定を保留')
            if misses >= 3:
                _flow(mem).pop('recall', None)
                mem['uncertain'] = True
                _arm_recall_skip(mem, state)
                _record(mem, 'camp_recall_aborted', resulting_event='cursor_unconfirmed',
                        reason='実カーソルを3回で確認できず野営メニューを閉じる')
                return [pad('b')]
            return []
        state.pop('cursor_misses', None)
        _record(mem, 'camp_recall_cursor', chart_step=None, observed_metric={'current': _current(screen),
                'choice': choice, 'move': move},
                reason=('野営の実手カーソルをいどう項目へ合わせる（ボスへ再出撃）' if state.get('boss')
                        else '野営の実手カーソルを帰還項目へ合わせる'))
        if move != 'here':
            return [move]
        state['stage'] = 'dest'
        state['steps'] = 0            # each stage gets the full observation budget
        return [pad('a')]
    if stage == 'dest':
        if screen.kind != 'map_target':
            if screen.kind == 'map':
                _flow(mem).pop('recall', None)
                mem['uncertain'] = True
                _hold_after_picker(mem, state)
            return [pad('b')] if screen.kind == 'world_map' else []
        marker = screen.marker
        if state.get('boss'):
            return _boss_redirect_dest_step(screen, mem, frame, state, marker)
        roofs = ([r for r in castle_roofs(frame) if r['kind'] == 'own' and not r['clipped']]
                 if frame is not None else [])
        if (state.get('hero') or state.get('fort')) and marker:
            # A 砦 asks for the nearest own castle (owner 2026-11-04). The hero's
            # castle is only _recall_goal's last resort, so a fort must not
            # consult it first: with no bound origin it answered アルマムーン
            # and the Y jump left the fort's neighbourhood.
            goal = state.get('goal') or (_fort_recall_goal(mem, state) if state.get('fort')
                                         else _recall_goal(mem))
            state['goal'] = goal
            jumped = (mem.get('y_jumped') or {}).get('RECALL') == goal
            if goal and jumped and not castle_roofs(frame):
                return _finish_hero_recall(mem, state, goal)      # roofs hidden: trust the jump
            if goal and not roofs and not jumped and (mem.get('y_jumps') or {}).get('RECALL:target', 0) < 2:
                return _start_y_jump(mem, {'step': 'RECALL'}, goal, 'target')
        enemy_under_marker = marker and frame is not None and any(
            r['kind'] == 'enemy' and abs(r['target'][0] - marker[0]) <= 12
            and abs(r['target'][1] - marker[1]) <= 12 for r in castle_roofs(frame))
        if marker and not roofs and not enemy_under_marker and not state.get('picker_forced'):
            # Owner 2026-11-05: 最悪キャンセルにせず、どこでも良いから指定する。
            # No own roof is in view, but the picker's marker stands somewhere:
            # confirm it once. The dispatch step bounds what follows.
            state['picker_forced'] = True
            _record(mem, 'camp_recall_picker_fallback',
                    observed_metric={'marker': list(marker), 'roofs': 0, 'goal': state.get('goal')},
                    reason='可視範囲に自軍城の屋根がないため取り消さず、マーカーの位置で確定する')
            return _request_recall(mem, state, state.get('goal'), [pad('a')],
                                   target_cell=list(marker))
        if not marker or not roofs:
            _flow(mem).pop('recall', None)
            mem['uncertain'] = True
            _hold_after_picker(mem, state)
            _record(mem, 'camp_recall_skipped',
                    observed_metric={'marker': list(marker) if marker else None, 'roofs': len(roofs)},
                    reason='可視範囲に自軍城の屋根がなく帰還先を選べないため取り消す')
            return [pad('b')]
        roof = min(roofs, key=lambda r: abs(r['target'][0] - marker[0]) + abs(r['target'][1] - marker[1]))
        dx = roof['target'][0] - marker[0]
        dy = roof['target'][1] - marker[1]
        if abs(dx) <= RECALL_CONFIRM_PX and abs(dy) <= RECALL_CONFIRM_PX:
            if state.get('hero') or state.get('fort'):
                return _finish_hero_recall(mem, state, state.get('goal'))
            return _request_recall(mem, state, None, [pad('a')],
                                   target_cell=list(roof['target']))
        if abs(dx) >= abs(dy):
            return [pad('right' if dx > 0 else 'left', min(6, abs(dx)))]
        return [pad('down' if dy > 0 else 'up', min(6, abs(dy)))]
    return []


def _boss_redirect_dest_step(screen, mem, frame, state, marker):
    """Aim the destination marker at the moving boss's measured cell and confirm.

    The marker is put on the cell by a Y jump to the boss (the same route a
    normal boss sortie takes) and confirmed by the boss rule of
    ``map._target_roof_under_marker`` (a Y jump or the marker on the cell).
    Never snaps to an own roof: the goal is the boss, not a castle.
    """
    from . import map as _map
    goal = chart.boss_castle(mem.get('chapter') or 0)
    state['goal'] = goal
    if not marker or frame is None or not chart.moving_boss_cell(mem.get('chapter')):
        # The measurement aged out or the picker shows nothing: never confirm blind.
        state['boss_lost'] = int(state.get('boss_lost') or 0) + 1
        if state['boss_lost'] < 3:
            return []
        return _abort_boss_redirect(mem, state, 'boss_position_lost',
                                    'ボスの現在位置を確認できないため再出撃を取り消す')
    pseudo = {'step': 'RECALL', 'target': goal, 'source': None}
    if _map._target_roof_under_marker(screen, mem, frame, pseudo):
        _reset_boss_jump(mem)       # the receipt is spent by this confirmation
        return _request_recall(mem, state, goal, [pad('a')], target_cell=list(marker), boss=True)
    if (mem.get('y_jumps') or {}).get('RECALL:target', 0) < BOSS_JUMP_LIMIT:
        return _start_y_jump(mem, {'step': 'RECALL'}, goal, 'target')
    return _abort_boss_redirect(mem, state, 'boss_jump_failed',
                                'ボスのセルへマーカーを合わせられないため再出撃を取り消す')


def _reset_boss_jump(mem):
    """Forget the previous redirect's Y-jump receipt and attempt count.

    The goal is always the boss, so an old ``y_jumped['RECALL']`` would
    confirm the next redirect without aiming the marker at the moved boss.
    """
    (mem.get('y_jumps') or {}).pop('RECALL:target', None)
    (mem.get('y_jumped') or {}).pop('RECALL', None)


def _abort_boss_redirect(mem, state, event, reason):
    _reset_boss_jump(mem)
    _flow(mem).pop('recall', None)
    mem['uncertain'] = True
    _arm_recall_skip(mem, state, BOSS_RETRY_TICKS)
    _record(mem, 'camp_boss_redirect_aborted', resulting_event=event,
            observed_metric={'camp': state.get('target'), 'goal': state.get('goal')},
            reason=reason)
    return [pad('b')]


FORT_STAGES = ('fort_menu', 'fort_list', 'fort_status')


def in_fort_route(state):
    return state.get('stage') in FORT_STAGES


def enter_fort(mem, state):
    """Start the 砦's own route: its generals leave by しゅつげき, not by きかん."""
    state.update(stage='fort_menu', steps=0, camp_kind='dome')
    (mem.get('y_jumps') or {}).pop('RECALL:target', None)
    (mem.get('y_jumped') or {}).pop('RECALL', None)


def _hold_after_picker(mem, state):
    """A picker that ended without a castle must not restart at once.

    The recall outranks orders and surveys, and re-entering a 砦 refills its Y
    jumps, so an unheld retry would keep every other flow off the map. The
    hero's own return names no camp: a hold there would stop every camp.
    """
    if not state.get('hero'):
        _arm_recall_skip(mem, state, FORT_RETRY_TICKS if state.get('fort') else LOST_CAMP_RETRY_TICKS)


def _leave_fort(mem, state, reason, actions):
    _flow(mem).pop('recall', None)
    mem['uncertain'] = True
    _arm_recall_skip(mem, state, FORT_RETRY_TICKS)
    _record(mem, 'camp_group_screen', resulting_event='fort_route_left',
            observed_metric={'camp': state.get('target'), 'stage': state.get('stage')}, reason=reason)
    return actions


def _fort_step(screen, mem, state):
    """Send one general out of a 砦 to the nearest own castle.

    Measured on the isolated g1097 start state (2026-11-01, ヴィーナス and エピィ
    in one 砦): A on the dome opens しゅつげき/こうかん, A on しゅつげき lists the
    generals inside, A on a name shows his status panel, and the next A opens
    the destination marker on the 砦 itself, where A cancels and stays. The
    picker is a castle sortie's, so the hero's Y-jump destination route applies.
    ``None`` hands the map_target frame on to that route.
    """
    from . import house
    stage = state['stage']
    if stage != 'fort_status' and screen.kind == 'map':
        return _leave_fort(mem, state, '砦のメニューが閉じてマップへ戻ったためこのしゅつげきを見送る', [])
    if stage == 'fort_menu':
        if not is_fort_menu(screen):
            return []                  # the window is still fading in
        if not fort_list_open(screen):
            move = menu_to(screen, 'しゅつげき')
            if move is None:
                return []
            if move != 'here':
                return [move]
            state.update(stage='fort_list', steps=0)
            return [pad('a')]
        state.update(stage='fort_list', steps=0)
        stage = 'fort_list'
    if stage == 'fort_list':
        if not fort_list_open(screen):
            return []                  # the list is still drawing
        members = [word for x, _, word in _options(screen) if x > 100]
        wanted = state.get('fort_actor')
        if wanted in members:
            move = menu_to(screen, wanted)
            if move is None:
                return []
            if move != 'here':
                return [move]
        current = _current(screen)
        if not current or current[2] not in members:
            return []
        # The fort holds these generals together, so any of their departures
        # locates it when the chosen one has no sortie record of his own.
        state['fort_members'] = list(members)
        state.update(stage='fort_status', steps=0, general=current[2])
        _record(mem, 'camp_fort_sortie', chart_step=None, general=current[2],
                observed_metric={'camp': state.get('target'), 'members': members},
                reason='砦の中の将軍を一人選び、しゅつげきで補給できる自軍城へ帰す')
        return [pad('a')]
    if screen.kind == 'map_target':
        state.update(stage='dest', steps=0, fort=True)
        return None
    if screen.kind == 'map':
        return _leave_fort(mem, state, '砦のしゅつげきが閉じてマップへ戻ったため見送る', [])
    if house.general_status(screen):
        reads = state['fort_status_reads'] = int(state.get('fort_status_reads') or 0) + 1
        return [pad('a')] if reads % FORT_STATUS_PRESS_EVERY == 1 else []
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
        state.pop('origin_source', None)
    else:
        state.update(origin=origin, origin_source='roofs')


def _finish_hero_recall(mem, state, goal):
    return _request_recall(mem, state, goal, [pad('a')])


def _request_recall(mem, state, goal, actions, **fields):
    """A proposal remains pending; neither an input nor an arrival receipt."""
    # #3544: a `recall_interrupted` marker can outlive the recall it belonged
    # to (e.g. the month-scene drop pops `recall` without the marker). It is
    # only read by the dispatch wait's unconfirmed terminal, so clearing it at
    # the head of every new request keeps a stale marker from shortening an
    # unrelated recall's retry hold.
    _flow(mem).pop('recall_interrupted', None)
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
    receipt = _journal(mem).get('_recall_inputs') or {}
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
        interrupted = _flow(mem).pop('recall_interrupted', None) is not None
        _flow(mem)['recall_verification'] = {**state, 'status': status}
        _flow(mem).pop('recall', None)
        _arm_recall_skip(mem, state,
                         BOSS_RETRY_TICKS if state.get('boss')
                         else FORT_RETRY_TICKS if (state.get('fort') or interrupted) else None)
        mem['uncertain'] = True
        _record(mem, 'camp_recall_unconfirmed', chart_step=None, observed_metric={'castle': state.get('goal'),
                'input_sent': bool(state.get('input_sent')), 'picker_closed': bool(state.get('picker_closed')),
                'interrupted': interrupted,
                'general': state.get('general') if state.get('fort') else None}, resulting_event=status,
                reason='帰還の本人・移動・到着を確認できず未確認として保持し、有限な操作を終了')
        return [pad('b')] if screen.kind in ('world_map', 'map_target') else []
    return []
