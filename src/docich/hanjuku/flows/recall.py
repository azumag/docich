"""手順層: camp recall and the hero's return to a castle."""
from __future__ import annotations

from ..os_layer.pixels import Frame
from ..os_layer.screen import Screen, camp_body_target, castle_roofs, own_camps
from ... import hanjuku_chart as chart
from ..decision_log import _record
from ..os_layer.commands import fort_list_batch, menu_to, pad
from ..os_layer.navigation import _start_y_jump
from ..os_layer.readers import (_canonical_general, _current, _cursor, _options,
                                fort_list_open, is_camp_menu, is_fort_menu)
from ..os_layer.world import NAME, _owned
from ..strategy.orders import (_fort_recall_goal, _recall_goal, _recall_owned,
                               order_dispatch_starved)
from ..state.regions import flow as _flow, journal as _journal, sorties as _sorties


MENU_HOLD_LIMIT = 10           # arrived-but-unanchored holds after a failed castle menu

RECALL_LIMIT = 90              # observations for one camp recall
RECALL_ARRIVE_PX = 4           # cursor cell onto the tent
RECALL_OCCLUDED_PX = 8         # cursor sprite covering the last confirmed tent
RECALL_CONFIRM_PX = 4          # marker onto the own castle's selecting cell
RECALL_SKIP_TICKS = 400        # observations before retrying the same failed camp
RECALL_READ_DELAY = 180        # ticks before the destination roster is read (march time)
RECALL_SKIP_RADIUS = 48        # screen pixels: cover the measured camera shift for the same camp
RECALL_SKIP_HISTORY = 6        # other failed camps whose hold is still running
LOST_CAMP_RETRY_TICKS = 60     # first wait after a tent only went out of sight, doubling up to the skip
FORT_RETRY_TICKS = 20          # first wait before the next general leaves the same 砦, doubling likewise
FORT_STATUS_PRESS_EVERY = 4    # observations of the status panel between two A presses
FORT_STAGE_OBSERVATION_LIMIT = 12  # a fort stage must progress or release its owner
MENU_OPEN_WAIT = 3             # bare map observations after A on the tent before pressing again
MENU_REOPEN_LIMIT = 3          # presses again per recall
DOME_SCREEN_WAIT = 6           # other-screen observations after A on a dome before B
BOSS_RETRY_TICKS = 120         # first wait before a camp is sent at the moving boss again
BOSS_JUMP_LIMIT = 2            # Y jumps to the boss per redirect
RELAY_ARRIVE_PX = 32           # a camp this close to the back-half relay cell has finished leg one
RELAY_MENU_WAIT = 4             # observations for the picker B to settle back on the camp menu
# docich#5108: a screen the recall cannot operate (battle kinds, an unrelated
# text panel) must not spend the 90-observation operation budget — it used to
# burn every step until the whole return aborted. The recall intent is held
# instead; only this many ticks without a resumable screen take the ordinary
# abort path.
RECALL_HOLD_TICK_LIMIT = 600   # ticks a non-operable screen may hold one recall
# docich#5134: combat that takes the foreground mid-recall returns the field
# cursor to the battle site, so the observations the recall burned before it
# are debt under a cursor position that no longer exists — the walk to the
# tent restarts from scratch. The first 'map' frame afterwards restarts the
# budget, bounded by count and total ticks so a battle-heavy month cannot
# stretch one recall indefinitely.
RECALL_BATTLE_RESET_LIMIT = 3    # budget restarts battle returns may grant one recall
RECALL_BATTLE_RESET_TICKS = 1800 # ticks those restarts may stretch one recall
# docich#5665: when the bounded dispatch-proof window (6 owned-screen
# observations) runs out without a send receipt, the wait demotes to a long
# tail stage ('await_dispatch_stale') instead of ending as a permanent
# unconfirmed hold. The stale wait keeps yielding screens to their normal
# owners and retries the proof on every owned frame: a late receipt resumes
# the normal check, sender-proven unsent gets one bounded resend into the
# still-open picker, and only these bounds file the unconfirmed incident.
DISPATCH_STALE_STEP_LIMIT = 12   # owned-screen observations the stale wait may spend
DISPATCH_STALE_TICK_LIMIT = 400  # ticks one stale wait may outlive its request
DISPATCH_RESEND_MAX = 1          # sender-proven unsent backfills per request

# docich#5452: the stages that steer the いどう move mode — the destination
# picker/walk, its whole-map Y route, the relay leg and the hero's own unit
# focus. Combat kills that mode along with the foreground, so a battle that
# interrupted one of them needs the mode-loss handling in
# ``_recall_move_mode_lost`` rather than the cursor-budget restart the
# field-navigation stages ('to_camp'/'menu') get.
MOVE_MODE_STAGES = ('dest', 'y_route', 'relay', 'hero_focus', 'hero_open')

# #4468: a field general's blue G badge is the very same square as the real
# いどう destination marker. On g1190 2026-11-17 the recall read a bare field
# as 'map_target' (the G on an army sprite) and sent its A with no picker open;
# the following `camp_recall_unconfirmed` was the silent failure. The badge
# carries a red flag on a pole right above the square; the genuine marker has
# a yellow/black border and no flag. Measured on all eight marker fixtures:
# 16 flag-red pixels above the badge, 0 above every genuine marker. Requiring
# this negative evidence before A keeps the confirmation on a real picker.
BADGE_FLAG_MIN = 6


def _picker_marker(screen, frame):
    """The destination marker, or None when it is a field general's G badge."""
    marker = screen.marker
    if not marker or not isinstance(frame, Frame):
        return marker
    x, y = marker
    reds = 0
    for yy in range(max(0, y - 10), y):
        for xx in range(max(0, x - 3), min(frame.width, x + 18)):
            r, g, b = frame.pixel(xx, yy)
            if r >= 190 and g <= 110 and b <= 110:
                reds += 1
                if reds >= BADGE_FLAG_MIN:
                    return None
    return marker


def boss_phase(mem) -> bool:
    """Chapter 9 with every road castle ours: the moving boss is the only target.

    Before that, a camped general is an ordinary straggler (a lost battle's
    retreat, say) and is recalled home as usual.
    """
    chapter = mem.get('chapter')
    # A rebound policy chart (tests, hot-load layers) may expose only the
    # attributes its case needs; treat a missing one as "not the boss phase"
    # instead of raising into the caller (docich#5170 follow-up).
    moving = getattr(chart, 'MOVING_BOSS_CHAPTER', None)
    corridor = getattr(chart, 'corridor', None)
    if moving is None or corridor is None or chapter != moving or not corridor(chapter):
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
    # A wave that has not produced an entry within the stale bound is not a
    # march to re-arm: sending another camp at the boss would repeat the same
    # miss forever (live g1288 2026-11-20). Skip the boss goal so this camp
    # takes the ordinary recall to a castle; the fresh sortie from there is
    # the next wave.
    from .boss_pursuit import wave_stale
    if wave_stale(mem):
        return None
    return chart.boss_castle(chapter)


def camp_stays(mem) -> bool:
    """A camp is never recalled to an ordinary home in the moving boss's phase.

    Owner 2026-11-15: cliffs block every castle but the last one from reaching
    the moving boss, so the only useful home move in this phase is the
    gathering move at the last corridor castle (see ``gather_castle``).
    """
    return boss_phase(mem)


def gather_castle(mem):
    """The last corridor castle: every camped general gathers there first.

    Measured owner rule (2026-11-15): only a march that starts from the last
    castle reaches the moving boss; from any other castle the cliffs stop the
    army. Chapter 9 therefore gathers at イデ！ before the boss sortie.
    """
    if not camp_stays(mem):
        return None
    road = chart.corridor(mem.get('chapter') or 0) or ()
    return road[-1] if road else None


def _whitehouse_camp(mem, screen, state, frame):
    """A detected camp that belongs to chapter 10's open White House (#4922).

    The White House admits a march only through the south gate's red carpet,
    so every general who marched straight at its walled boss cell is camped
    somewhere in or beside the ring. While the castle is open — all normal
    castles held, or its cell measured this chapter — such a camp is an
    expired boss march, never an ordinary straggler to recall home.
    """
    if (mem.get('chapter') or 0) != 10:
        return False
    from ..strategy.orders import _whitehouse_open
    if not _whitehouse_open(mem):
        return False
    gen = state.get('general')
    statuses = (mem.get('post_month_status') or {}).get('statuses') or {}
    info = statuses.get(gen) if isinstance(gen, str) and isinstance(statuses, dict) else None
    if isinstance(info, dict):
        hp, mhp = info.get('hp'), info.get('max_hp')
        if info.get('broken') is True or (
                type(hp) is int and type(mhp) is int and mhp > 0 and hp <= mhp // 2):
            return False
    area = chart.CH10_WH_AREA
    # A measured roster marker on the general (or any member of a fort) places
    # a White House camp even when the frame has no castle roofs around it.
    for who in ((gen,) if gen else ()) + tuple(state.get('fort_members') or ()):
        w_info = statuses.get(who) if isinstance(statuses, dict) else None
        if not isinstance(w_info, dict):
            continue
        marker = w_info.get('marker')
        if isinstance(marker, dict):
            if marker.get('castle_candidate') == chart.boss_castle(10):
                return True
            mf = marker.get('marker_field') or (marker.get('mapping') or {}).get('field')
            if (isinstance(mf, (list, tuple)) and len(mf) == 2
                    and area[0] <= mf[0] <= area[2] and area[1] <= mf[1] <= area[3]):
                return True
    camp = state.get('camp_world')
    if not _camp_target(camp):
        # The two-roof origin may not bind inside the ring (the White House
        # is a boss tower, not a castle roof); the maintained world estimate
        # plus the camp's screen delta still places it.
        cursor = _cursor(screen)
        origin, target = mem.get('cursor'), state.get('target')
        if (cursor and not mem.get('uncertain')
                and isinstance(origin, (list, tuple)) and len(origin) == 2
                and isinstance(target, (list, tuple)) and len(target) == 2):
            camp = [int(round(origin[0] + target[0] - cursor[0])),
                    int(round(origin[1] + target[1] - cursor[1]))]
            state['camp_world'] = camp
    if (isinstance(camp, (list, tuple)) and len(camp) == 2
            and area[0] <= camp[0] <= area[2] and area[1] <= camp[1] <= area[3]):
        return True
    # A boss-wave general camping is an expired boss march wherever the tent
    # stands: the White House legs walk him back to the south gate.
    wave = {s.get('general') for s in _sorties(mem).values()
            if isinstance(s, dict) and s.get('target') == chart.boss_castle(10)
            and s.get('status') not in ('arrived', 'cancelled') and s.get('general')}
    if not wave:
        return False
    actors = ((mem.get('camp_status_seen') or {}).get('actors') or {})
    if any(g in actors for g in wave):
        return True
    # By the chapter-9 boss-redirect reasoning a roofless camp is off-road:
    # castle camping parks beside its own roof, so while a wave is in flight
    # an open-field tent is another expired boss march.
    return isinstance(frame, Frame) and not castle_roofs(frame)


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
    if base is None:
        _flow(mem).pop('recall_retry', None)
        return
    if scoped is None:
        # An interrupted dispatch can lack a measured camp coordinate (for
        # example a hero return). Keep the recovery hold short even when it
        # must be global; otherwise the default 400-tick cooldown suppresses
        # every visible camp after a battle that never sent the return A.
        if type(base) is int and base > 0:
            _flow(mem)['recall_skip_ticks'] = min(RECALL_SKIP_TICKS, base)
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

def _recall_hold_or_abort(mem, state, screen):
    """Bound the hold a non-operable screen puts on a camp recall (docich#5108).

    The first held frame stamps ``hold_since``; a resumable screen clears it
    (in ``_camp_recall_step`` / ``map.world_map_step``). Past
    ``RECALL_HOLD_TICK_LIMIT`` ticks the recall takes the same abort as the
    step-limit path: the intention does not bounce between early aborts and
    re-detection. ``None`` means "keep holding"; the caller keeps returning
    the frame to its real owner.
    """
    if not isinstance(state, dict):
        return None
    now = mem.get('tick')
    if type(now) is not int:
        return None                      # no clock: hold without a bound
    since = state.get('hold_since')
    if type(since) is not int:
        state['hold_since'] = now
        return None
    if now - since < RECALL_HOLD_TICK_LIMIT:
        return None
    _record(mem, 'camp_recall_aborted',
            observed_metric={'stage': state.get('stage'), 'steps': state.get('steps'),
                             'held_ticks': now - since, 'screen': screen.kind},
            reason='操作できない画面が上限を超えて続くため帰還を断念し位置を再測定する')
    _flow(mem).pop('recall', None)
    mem['uncertain'] = True
    _arm_recall_skip(mem, state)
    # Mirrors the step-limit abort: only menus and pickers get a closing B; a
    # text or battle frame is left to its real owner.
    if (is_camp_menu(screen) or is_fort_menu(screen) or screen.kind in ('map_target', 'world_map')
            or str(state.get('stage')).startswith('fort_')):
        return [pad('b')]
    return []


def _battle_return_reset(mem, state):
    """Restart the step budget after combat repositioned the field cursor.

    docich#5134: the walk to the tent (and the bounded menu reopen after
    it) was measured from the pre-battle cursor, so the first operable
    'map' frame back gets a fresh budget. ``battle_reset_since`` marks the
    first restart — later ones are allowed up to RECALL_BATTLE_RESET_LIMIT
    while they stay inside RECALL_BATTLE_RESET_TICKS of it, so an
    eight-battle month stretches the recall by a bounded amount only.
    """
    resets = int(state.get('battle_resets') or 0)
    if resets >= RECALL_BATTLE_RESET_LIMIT:
        return
    now = mem.get('tick')
    since = state.get('battle_reset_since')
    if (type(now) is int and type(since) is int
            and now - since >= RECALL_BATTLE_RESET_TICKS):
        return
    if type(now) is int and type(since) is not int:
        state['battle_reset_since'] = now
    spent = int(state.get('steps') or 0)
    state['battle_resets'] = resets + 1
    state['steps'] = 0
    _record(mem, 'camp_recall_battle_reset',
            observed_metric={'stage': state.get('stage'), 'steps_spent': spent,
                             'resets': state['battle_resets']},
            reason='戦闘が終わりカーソル位置が戦闘地点へ戻ったため、帰還操作の観測予算を新しい基準でやり直す')


def camp_recall_step(screen: Screen, mem, frame):
    """Observe named retreaters before reusing the existing camp recall route."""
    from . import camp_recheck as hanjuku_camp_recheck
    return hanjuku_camp_recheck.step(screen, mem, frame, _camp_recall_step)


def _mark_recall_interrupted(mem):
    """Record a foreground scene that covers a pending recall dispatch.

    Before an input receipt exists, the scene is an ordinary interruption and
    can use the existing free retry marker. After a receipt exists, retrying A
    could duplicate an accepted return. Preserve that case separately as
    scene_covered so a later field map is not mistaken for picker closure and
    the destination is not penalized for a scene-owned frame.
    """
    state = _flow(mem).get('recall')
    if (not isinstance(state, dict)
            or state.get('stage') not in ('await_dispatch', 'await_dispatch_stale')):
        return
    receipt = _journal(mem).get('_recall_inputs') or {}
    trace = state.get('request_trace') or {}
    receipt_sent = bool(
        trace and receipt.get('request_trace') == trace
        and receipt.get('a_inputs', 0) >= state.get('a_inputs', 1)
    )
    if state.get('input_sent') or receipt_sent:
        state['scene_covered'] = True
        state.setdefault('scene_covered_tick', int(mem.get('tick') or 0))
        return
    _flow(mem)['recall_interrupted'] = int(mem.get('tick') or 0)


def _mark_recall_battle(mem):
    """A combat foreground interrupted field nav or menu work (docich#5134).

    Combat returns the field cursor to the battle site; the first 'map'
    frame afterwards restarts that work's observation budget on the new
    baseline (see ``_battle_return_reset``). Only the stages that steer the
    field cursor — or wait on the camp menu it opened — take the flag; a
    pending dispatch already has ``recall_interrupted`` and a text-hold is
    not a cursor repositioning.

    docich#5452: the destination-steering stages take the flag too, but for
    a different reason — combat also kills the いどう move mode, so the
    first 'map' after it must not be resumed as the いどう field cursor
    (see ``_recall_move_mode_lost``).
    """
    state = _flow(mem).get('recall')
    if isinstance(state, dict) and state.get('stage') in (
            'to_camp', 'menu') + MOVE_MODE_STAGES:
        state['battle_return'] = True


def _recall_move_mode_lost(mem, state, screen):
    """Combat killed the いどう move mode; restart the recall from scratch.

    docich#5452: a battle that interrupts the destination steering
    (``MOVE_MODE_STAGES``) leaves the flow believing the field cursor is
    still the いどう one. The first post-battle 'map' is a plain field —
    resuming the Y route there opens the whole map in *view* mode, where an
    A on an own castle opens its menu (castle_menu) instead of naming the
    destination, and the follow-up confirm A lands inside that menu so the
    return never launches (g1190 2026-11-25). Drop the in-flight attempt
    like an interrupted dispatch: the next observation re-detects the camp,
    re-walks its menu and confirms いどう again from a state that can be
    verified.
    """
    if (mem.get('y_jump') or {}).get('step') == 'RECALL':
        mem.pop('y_jump', None)          # the jump belonged to the dead mode
    _reset_boss_jump(mem)                # drop the stale RECALL jump receipt/count
    (mem.get('y_jumped_res') or {}).pop('RECALL', None)
    stage = state.get('stage')
    _flow(mem).pop('recall', None)
    mem['uncertain'] = True
    if not state.get('hero'):
        # Same short recovery hold an interrupted dispatch takes — the camp
        # is still there, only its move mode died. A boss redirect keeps its
        # own pacing.
        _arm_recall_skip(mem, state,
                         BOSS_RETRY_TICKS if (state.get('boss') or state.get('boss_move'))
                         else FORT_RETRY_TICKS)
    _record(mem, 'camp_recall_interrupted',
            observed_metric={'stage': stage, 'screen': screen.kind,
                             'goal': state.get('goal'),
                             'camp': list(state.get('target') or ())},
            resulting_event='move_mode_lost',
            reason='戦闘が目的地操舵中のいどうモードを切ったため、viewモードの全体マップや城メニューへ入力せず帰還をやり直す')
    if (is_camp_menu(screen) or is_fort_menu(screen)
            or screen.kind in ('map_target', 'world_map', 'castle_menu',
                               'general_list', 'castle_info', 'main_menu')):
        return [pad('b')]
    return []


MONTH_END_MARGIN = 200         # ticks before the estimated month end: no new recall
MONTH_DEFAULT_TICKS = 320      # first month after a restart: no measured length yet


def _month_end_soon(mem, margin=None):
    """The month's start scene is likely close: do not begin a recall now.

    g1163 2026-11-14 01:35: the camp menu and the きかん selection completed,
    then the destination picker was cut by the 42-8 month scene (steps 1).
    The recall takes about two minutes; starting it inside that margin just
    feeds the scene counter.
    """
    start = mem.get('month_start_tick')
    if type(start) is not int:
        return False
    durations = mem.get('month_durations')
    usable = [int(d) for d in (durations or [])[-3:] if type(d) is int]
    # The first month after a restart has no measured length: a conservative
    # default keeps the scene protection on instead of silently waiting for
    # a second month boundary (g1163 2026-11-14 02:0x: durations None).
    estimate = max(usable) if usable else MONTH_DEFAULT_TICKS
    limit = MONTH_END_MARGIN if margin is None else margin
    return int(mem.get('tick') or 0) - start >= estimate - limit


VISIBLE_MOVE_LIMIT = 40      # field-cursor steps allowed on one いどう
CAMP_STEP_FRAMES = 28        # same 1 px/frame hold as nav_step (was 8)
SCREEN_W, SCREEN_H = 256, 224  # the field map frame
EDGE_SCROLL_LIMIT = 8        # bounded camera scrolls toward an off-screen tent


def _recall_blocked_twice(mem, castle):
    """A castle that already refused this camp twice (a cliff on the way)."""
    blocked = _flow(mem).get('camp_recall_blocked') or {}
    count = blocked.get(castle) if isinstance(blocked, dict) else None
    return type(count) is int and count >= 2


def _visible_own_candidates(mem, frame):
    """Every own roof on this frame with the castle and screen cell it names.

    'screen' is the roof's measured cell; together with the chart cell it
    implies the camera even when only one roof is visible. That one-roof
    camera is what lets the destination picker aim at another castle when
    the constellation is too weak for observed_cursor_cell (live g1190
    camp at [87,19]: four roofs, camera_support 1, origin None).
    """
    if not isinstance(frame, Frame):
        return []
    from ..os_layer.world import _owned, roof_camera_support
    support = roof_camera_support(mem, frame)
    if not support:
        return []
    _, cam = support
    cells = chart.castles(mem.get('chapter') or 0)
    owned = _owned(mem)
    candidates = []
    for roof in castle_roofs(frame):
        if roof.get('kind') != 'own' or roof.get('clipped'):
            continue
        world = (roof['target'][0] + cam[0], roof['target'][1] + cam[1])
        for name, cell in cells.items():
            if name in owned and abs(world[0] - cell[0]) + abs(world[1] - cell[1]) <= 12:
                candidates.append({'castle': name, 'world': list(world),
                                   'screen': list(roof['target'])})
                break
    return candidates


def _visible_own_castle(mem, frame, candidates=None):
    """The own castle whose roof is visible now (field いどう destination).

    A castle that already failed to receive this camp twice (a cliff on the
    way) is not chosen again just because it is on screen. With no unblocked
    candidate the caller falls back to the きかん picker, where
    _recall_goal ranks every blocked castle behind the unblocked ones.
    """
    if candidates is None:
        candidates = _visible_own_candidates(mem, frame)
    for candidate in candidates:
        if not _recall_blocked_twice(mem, candidate['castle']):
            return candidate
    return None


def _bind_camp_origin(mem, state, candidates):
    """Bind the camp's world cell from one visible own roof when unbound.

    The corridor camp menu leaves the roof constellation too weak for
    observed_cursor_cell (camera_support 1), so the island picker had no
    goal and confirmed an empty marker every month. The roof itself is a
    measured anchor: the camp's world cell is one screen subtraction away.
    The value only ranks the recall destination; it never licenses an input.
    """
    if not candidates or state.get('origin'):
        return
    target = state.get('target')
    roof = candidates[0]
    screen, world = roof.get('screen'), roof.get('world')
    if not (_camp_target(target) and _camp_target(screen) and _camp_target(world)):
        return
    state['origin'] = [world[0] + target[0] - screen[0],
                       world[1] + target[1] - screen[1]]
    state['origin_source'] = 'visible_roof'


def _visible_recall_candidates(mem, state, frame):
    """Visible own castles the camp may be recalled to on this chapter.

    Even a blocked roof is a measured camera anchor for the ordinary picker,
    so bind the origin from every candidate before filtering. #4468 extends
    the field shortcut to gateway chapters; v1010/v1011's side judgement must
    still keep a camp on its own half of the impassable cliff (plus the shared
    hub). With no measured origin _recall_owned keeps all owned castles, so a
    weak camera falls back to the whole-island picker rather than guessing a
    side.
    """
    candidates = _visible_own_candidates(mem, frame)
    _bind_camp_origin(mem, state, candidates)
    chapter = mem.get('chapter') or 0
    gate = chart.gateway(chapter)
    if gate:
        from ..os_layer.world import _owned
        cells = chart.castles(chapter) or {}
        allowed = _recall_owned(mem, chapter,
                                sorted(c for c in _owned(mem) if c in cells),
                                origin=state.get('origin'))
        candidates = [c for c in candidates if c['castle'] in allowed]
        # DSH #4480: the shared hub is reachable from both halves only in
        # principle. A back-half camp sits in the hollow below タタール and a
        # direct march walks into the cliff (v1017), so a same-side castle must
        # win regardless of the roof scan order. A hub-only view is left to the
        # ordinary picker instead of forcing that unsafe shortcut.
        side = [c for c in candidates if c['castle'] != gate['hub']]
        candidates = side or []
    return candidates

def _relay_target(mem, state):
    """Back-half camps walk to the middle of their half before recalling to the hub.

    Owner 2026-11-17 (#4473): a general who retreated from a back-half castle
    stands in a hollow below the hub. A direct きかん at タタール walks into the
    cliff and leaves the general camping on the edge month after month (117
    failed recalls at camp (134,120)). When no back-half castle is ours, send
    him there first with the camp menu's いどう (a field-cursor move, so no
    picker geometry is involved), then recall to the hub on the next pass.

    The relay cell is the centroid of the gateway's back castles — field
    (508,264) in chapter 11, north of the hub (504,480) — so it is biased into
    the back half rather than sitting on the direct line to the hub. Only a
    measured back-half camp with no own back-half castle qualifies; a boss,
    a gather or an already-running move keeps the existing route.
    """
    chapter = mem.get('chapter') or 0
    gate = chart.gateway(chapter) if hasattr(chart, 'gateway') else None
    if not gate or not gate.get('back') or not gate.get('hub'):
        return None
    if state.get('boss') or state.get('boss_move') or state.get('gather'):
        return None
    cells = chart.castles(chapter)
    pts = [cells[c] for c in gate['back'] if c in cells]
    if len(pts) != len(gate['back']) or gate['hub'] not in cells:
        return None
    origin = state.get('camp_world') or state.get('origin')
    if not _camp_target(origin) or origin[1] >= cells[gate['hub']][1]:
        # Not measured, or already on the hub/front side: no cliff between.
        return None
    if any(c in _owned(mem) for c in gate['back']):
        # An own castle in this half is a reachable destination; keep きかん.
        return None
    relay = [round(sum(p[0] for p in pts) / len(pts)),
             round(sum(p[1] for p in pts) / len(pts))]
    if abs(origin[0] - relay[0]) + abs(origin[1] - relay[1]) <= RELAY_ARRIVE_PX:
        # Leg one is done. The next recall starts here and きかん aims at the
        # hub (the nearest own castle to the relay cell).
        return None
    return {'cell': relay, 'hub': gate['hub']}


def _visible_move_or_relay(mem, state, visible):
    """A visible camp move, unless the visible castle is across the cliff.

    For a back-half camp with no own back-half castle, every visible own
    castle (home and hub included) is on the far side of the impassable
    cliff, so the いどう would walk into it. Return the relay instead;
    ``None`` visible or an owned back-half castle keeps the ordinary move.
    """
    if visible is None:
        return None, None
    relay = _relay_target(mem, state)
    if relay is None:
        return visible, None
    return None, relay


def relay_from_picker(mem, state):
    """Re-aim a picker-opened recall at the relay once the origin is measured.

    g1190 2026-11-18 08:45-08:56 (#4533): a back-half camp has no own roof on
    screen, so ``_observe_recall_origin`` cannot bind its world cell and the
    relay never starts. The whole-island picker then binds the unique army ▲
    (``origin_source='picker'``) only after きかん was already chosen, and the
    confirm recalled the general at a front-half castle across the cliff —
    ten unconfirmed tries from one camp. When that measured origin turns out
    to need the relay, close the picker and return to the camp menu so the
    ordinary menu step can choose いどう instead.
    """
    if state.get('origin') is None or state.get('relay'):
        return None
    relay = _relay_target(mem, state)
    if relay is None:
        return None
    state['relay'] = relay
    state['move_visible'] = {'cell': list(relay['cell']), 'relay': True}
    state['stage'] = 'menu'
    state['steps'] = 0
    state.pop('goal', None)
    state.pop('destination_selected', None)
    _record(mem, 'camp_recall_relay_from_picker',
            observed_metric={'origin': list(state['origin']),
                             'relay': list(relay['cell']),
                             'camp': list(state.get('target') or ())},
            reason='ピッカーで測った現在地が後半にあるため、崖の向こうの城ではなく中継セルへいどうする')
    return relay


RELAY_ROAD_STEP = 16      # frames per press; the field cursor moves ~1 px/frame
RELAY_ROAD_PRESS_MAX = 40  # bounded presses for one relay leg


def _relay_road(mem, state, cell):
    """Dead-reckon the relay cursor when no own roof can anchor the camera.

    The back half has no own roofs, so ``observed_cursor_cell`` cannot place
    the いどう cursor (g1190 2026-11-18: relay started but leg one aborted as
    cursor_not_found). The picker already measured the camp's world cell and
    the field cursor moves about one pixel per frame, so the camp→relay delta
    is walked directly and the A confirms the cell when the delta is spent.
    Units are the same: ``_camp_world_cell`` keeps screen cells == world cells.
    """
    road = state.get('relay_road')
    if not isinstance(road, dict):
        origin = state.get('camp_world') or state.get('origin')
        if not (_camp_target(origin) and _camp_target(cell)):
            return None
        road = state['relay_road'] = {
            'dx': int(cell[0]) - int(origin[0]),
            'dy': int(cell[1]) - int(origin[1]),
            'presses': 0}
        _record(mem, 'camp_recall_relay_road', observed_metric={
            'camp': list(origin), 'relay': list(cell),
            'dx': road['dx'], 'dy': road['dy']},
            reason='屋根アンカーが無い後半では、ピッカーで測った野営セルと中継セルの差でいどうカーソルを送る')
    presses = int(road.get('presses') or 0)
    dx, dy = int(road.get('dx') or 0), int(road.get('dy') or 0)
    if presses >= RELAY_ROAD_PRESS_MAX or (dx == 0 and dy == 0):
        # #4533 (DSH follow-up): the road is spent. Leaving dx=dy=0 behind
        # would make a later leg with `relay` still set answer a momentary
        # roof-anchor loss with an immediate A instead of re-measuring.
        state.pop('relay_road', None)
        return [pad('a')]
    if abs(dx) >= abs(dy):
        frames = min(RELAY_ROAD_STEP, abs(dx))
        road['dx'] = dx - frames if dx > 0 else dx + frames
        road['presses'] = presses + 1
        return [pad('right' if dx > 0 else 'left', frames)]
    frames = min(RELAY_ROAD_STEP, abs(dy))
    road['dy'] = dy - frames if dy > 0 else dy + frames
    road['presses'] = presses + 1
    return [pad('down' if dy > 0 else 'up', frames)]


def _visible_move_dest_step(screen, mem, frame, state):
    """Steer the field cursor onto the visible own castle, then confirm.

    Owner 2026-11-15: the camp menu's いどう is a field-cursor move, not the
    whole-island picker, so a visible castle can be chosen where it stands
    instead of walking the island ring.
    """
    from ..os_layer.world import observed_cursor_cell
    from ..os_layer.navigation import nav_step
    mv = state.get('move_visible') or {}
    goal = mv.get('castle')
    # A relay destination is a measured field cell, not a castle name; the
    # ordinary visible-castle move still resolves its cell from the chart.
    cell = (list(mv['cell']) if isinstance(mv.get('cell'), (list, tuple))
            and len(mv['cell']) == 2 else None)
    if cell is None and goal:
        cell = chart.castles(mem.get('chapter') or 0).get(goal)
    steps = int(state.get('move_steps') or 0) + 1
    state['move_steps'] = steps
    if cell is None or not isinstance(frame, Frame) or steps > VISIBLE_MOVE_LIMIT:
        _visible_move_abort(mem, state, goal, steps, 'cursor_not_found')
        return [pad('b')]
    if steps == 1 and not state.get('relay'):
        # A relay already starts inside the hollow; a perpendicular escape
        # would divert it away from the measured back-half middle cell.
        _plan_cliff_escape(mem, state, frame)
    escape = None if state.get('relay') else state.get('escape_world')
    here = observed_cursor_cell(screen, mem, frame)
    if here is None and state.get('relay'):
        road = _relay_road(mem, state, cell)
        if road is not None:
            return road
    if escape:
        # Owner 2026-11-16: the cliff-bound camp first steps perpendicular
        # off the wall; the freed camp is recalled home on a later pass.
        if here and abs(here[0] - escape[0]) + abs(here[1] - escape[1]) <= 8:
            return _request_recall(mem, state, None, [pad('a')],
                                   target_cell=list(here), escape_move=True)
        move = nav_step(screen, mem, frame, tuple(escape))
        if move in ('up', 'down', 'left', 'right'):
            return [pad(move)]
        if move == 'infeasible':
            state.pop('escape_world', None)     # fall back to the castle
        return []
    if here and abs(here[0] - cell[0]) + abs(here[1] - cell[1]) <= 12:
        if state.get('relay'):
            # Leg one only: the A confirms the field move, not a castle recall.
            # Drop the flow so the next observation re-detects the camp at the
            # relay cell; from there the ordinary きかん route aims at the hub.
            relay = state['relay']
            _flow(mem)['recall_relay_done'] = {
                'chapter': mem.get('chapter') or 0,
                'cell': list(cell), 'hub': relay.get('hub'),
                'tick': int(mem.get('tick') or 0)}
            _record(mem, 'camp_recall_relay_reached',
                    observed_metric={'cell': list(cell), 'hub': relay.get('hub'),
                                     'cursor': list(here), 'steps': steps},
                    reason='後半中央までいどうで移動して到達したため、次回のきかんでハブへ帰還させる')
            _flow(mem).pop('recall', None)
            mem['uncertain'] = True
            return [pad('a')]
        state['goal'] = goal
        _record(mem, 'camp_recall_visible_move_confirm',
                observed_metric={'castle': goal, 'cursor': list(here), 'steps': steps},
                reason='フィールドカーソルが見えている自軍城の屋根に載ったため、いどう先として決定する')
        return _request_recall(mem, state, goal, [pad('a')], target_cell=list(here))
    move = nav_step(screen, mem, frame, cell)
    if move == 'infeasible':
        _visible_move_abort(mem, state, goal, steps, move)
        return [pad('b')]
    if move in ('up', 'down', 'left', 'right'):
        return [pad(move)]
    return []


CLIFF_ESCAPE_REARM_TICKS = 1800   # one escape per camp area in this many observations
CLIFF_ESCAPE_AREA_PX = 48         # camps this close count as the same area


def _plan_cliff_escape(mem, state, frame):
    """Arm a perpendicular escape cell when the camp stands against a cliff.

    g1190 2026-11-16 (タオ): the tent sat under the end of a diagonal wall
    and every visible-castle いどう marched it into that wall, so it camped
    on the same tile month after month. One escape per camp area is armed;
    the next recall of the freed camp goes straight to the castle.
    """
    origin, target = state.get('origin'), state.get('target')
    if not (_camp_target(origin) and _camp_target(target)):
        return
    now = int(mem.get('tick') or 0)
    done = _flow(mem).setdefault('camp_cliff_escaped', {})
    for key, tick in list(done.items()):
        if type(tick) is not int or now - tick > CLIFF_ESCAPE_REARM_TICKS:
            done.pop(key, None)
    for key in done:
        x, y = (int(v) for v in key.split(','))
        if abs(x - origin[0]) <= CLIFF_ESCAPE_AREA_PX and abs(y - origin[1]) <= CLIFF_ESCAPE_AREA_PX:
            return
    found = _cliff_escape(frame, target)
    if found is None:
        return
    side, (ex, ey) = found
    escape = [origin[0] + ex - target[0], origin[1] + ey - target[1]]
    state['escape_world'] = escape
    state['cliff_detour'] = side
    done[f'{origin[0]},{origin[1]}'] = now
    _record(mem, 'camp_cliff_escape',
            observed_metric={'camp': list(target), 'origin': list(origin),
                             'side': side, 'escape': escape},
            reason='野営が崖に接しているため、城へ向かう前に崖と垂直方向の開けた隣接地点へ移動させる')


def _visible_move_abort(mem, state, goal, steps, why):
    _flow(mem).pop('recall', None)
    mem['uncertain'] = True
    _arm_recall_skip(mem, state)
    _record(mem, 'camp_recall_visible_move_aborted',
            observed_metric={'castle': goal, 'steps': steps, 'why': why},
            reason='見えている城へフィールドカーソルを合わせられないため、いどうを閉じて通常の帰還に戻す')


# Cliff walls are near-black in summer (8, 16, 0) and sand (16, 24, 8) but a
# dark grey (40, 48, 32) in winter (g1190 2026-11-16 タオ): the old < 25 test
# never saw a winter cliff, so the escape never fired there.
# (32, 32, 32) is every sprite's outline (cursor, tent, castle), never a wall
# colour, so it is not a cliff.
CLIFF_DARK = lambda r, g, b: max(r, g, b) <= 50 and (r, g, b) != (32, 32, 32)
CLIFF_REACH = 24            # px from the camp sampled for the black cliff
CLIFF_DETOUR_FRAMES = 16    # one short perpendicular step away from the cliff
CLIFF_DETOUR_MAX = 6        # wall-sliding steps before the goal is unreachable
CLIFF_RADIUS = 28           # px around the tent body searched for cliff pixels
CLIFF_MIN_PIXELS = 40       # dark pixels in that disc that make the tent cliff-bound
CLIFF_MIN_SOLID = 8         # 3x3 solid dark core pixels (filters sparse ch10 ground dots)
CLIFF_OVERSCAN_PX = 8       # SNES border margin ignored for cliff detection
CLIFF_ESCAPE_PX = 24        # distance of the perpendicular escape cell
_DIRS = {'left': (-1, 0), 'right': (1, 0), 'up': (0, -1), 'down': (0, 1)}


def _cliff_escape(frame, cell):
    """(side, (x, y)) of an open perpendicular step off an adjacent cliff.

    Owner 2026-11-15/16: a camp standing against a cliff must first step
    perpendicular away; marching straight home stays pinned to the wall.
    g1190 2026-11-16 (タオ): the tent sat under the end of a diagonal wall
    with the lake below it. The wall's direction is the centroid of the solid
    3x3 dark wall-core pixels around the tent body; requiring solid 3x3 cores
    and skipping the 8px SNES overscan border prevents ch10's dark-speckled
    ground tiles ((32, 40, 40)) and screen-edge black bars from triggering
    false cliff detours (#4907). The escape is the 4-way step most opposite
    to the wall whose landing stands on the tent's own ground colours (not the
    lake, not another wall). ``cell`` is the camp's selecting cell (the
    body is about (8, 12) px further).
    """
    if not isinstance(frame, Frame) or not _camp_target(cell):
        return None
    cx, cy = cell[0] + 8, cell[1] + 12
    w, h = frame.width, frame.height
    margin = CLIFF_OVERSCAN_PX if min(w, h) >= 64 else 1
    body = (cx - 9, cy - 16, cx + 9, cy + 6)     # the tent sprite itself
    dark, solid, ring = [], [], {}
    r2 = CLIFF_RADIUS * CLIFF_RADIUS
    y_lo, y_hi = max(margin, cy - CLIFF_RADIUS), min(h - margin, cy + CLIFF_RADIUS + 1)
    x_lo, x_hi = max(margin, cx - CLIFF_RADIUS), min(w - margin, cx + CLIFF_RADIUS + 1)
    for y in range(y_lo, y_hi):
        for x in range(x_lo, x_hi):
            d2 = (x - cx) ** 2 + (y - cy) ** 2
            if d2 > r2 or (body[0] <= x <= body[2] and body[1] <= y <= body[3]):
                continue
            p = frame.pixel(x, y)
            if CLIFF_DARK(*p[:3]):
                dark.append((x, y))
                if all(CLIFF_DARK(*frame.pixel(x + dx, y + dy)[:3])
                       for dy in (-1, 0, 1) for dx in (-1, 0, 1)):
                    solid.append((x, y))
            elif 100 <= d2 <= 256:
                ring[p] = ring.get(p, 0) + 1
    if len(dark) < CLIFF_MIN_PIXELS or len(solid) < CLIFF_MIN_SOLID or not ring:
        return None
    ax = cx - sum(x for x, _ in solid) / len(solid)
    ay = cy - sum(y for _, y in solid) / len(solid)
    ground = {c for c, _ in sorted(ring.items(), key=lambda kv: -kv[1])[:4]}
    best = None
    for side, (dx, dy) in _DIRS.items():
        # The walkability test below does the real filtering: a step into
        # the wall or the lake is never taken. Among open steps, the one
        # most opposite to the wall's mass wins (a diagonal wall can leave
        # only a sideways step open: g1190 タオ, lake below, wall up-right).
        score = dx * ax + dy * ay
        ex, ey = cx + dx * CLIFF_ESCAPE_PX, cy + dy * CLIFF_ESCAPE_PX
        if not (4 <= ex < w - 4 and 4 <= ey < h - 4):
            continue
        land = [frame.pixel(x, y) for y in range(ey - 5, ey + 6)
                for x in range(ex - 5, ex + 6) if 0 <= x < w and 0 <= y < h]
        path = [frame.pixel(cx + dx * k, cy + dy * k) for k in range(10, CLIFF_ESCAPE_PX + 1, 2)]
        if not land or any(CLIFF_DARK(*p[:3]) for p in path + land):
            continue
        if sum(p in ground for p in land) < 0.5 * len(land):
            continue
        if best is None or score > best[0]:
            best = (score, side, (ex - 8, ey - 12))
    return (best[1], best[2]) if best else None


def _cliff_open_side(frame, cell):
    """The open direction away from an adjacent cliff, or None."""
    found = _cliff_escape(frame, cell)
    return found[0] if found else None


CLIFF_DETOUR_STALL = 8      # observations without progress re-plan the escape
CLIFF_DETOUR_CAMP_AREA = 48 # camps this close share one escape budget


def _detour_camp_key(state):
    """The camp's stable cell for the persistent escape budget.

    ``camp_world``/``origin`` are camera-free world cells; the raw screen
    ``target`` is the last resort (camera pans can alias it, which only makes
    the budget slightly more generous, never less bounded).
    """
    cell = state.get('camp_world') or state.get('origin') or state.get('target')
    return cell if _camp_target(cell) else None


def _detour_count(mem, key):
    """Escapes already ordered near ``key`` across recall cycles.

    A recall pops its state after one escape move, so the per-state
    ``cliff_detours`` never reaches ``CLIFF_DETOUR_MAX`` and a cliff-bound
    camp is escaped again every month without ever being sent home
    (g1190 2026-11-16: every camp recall ended ``arrival_unconfirmed`` after
    a single 24px escape order, same tent cells for hours). Count escapes
    per camp so the budget is bounded across cycles; after it is spent the
    recall proceeds to the castle instead of escaping forever.
    """
    done = _flow(mem).setdefault('camp_detours', {})
    now = int(mem.get('tick') or 0)
    for k, v in list(done.items()):
        if not isinstance(v, dict) or now - int(v.get('tick') or 0) > CLIFF_ESCAPE_REARM_TICKS * 4:
            done.pop(k, None)
            continue
        x, y = (int(p) for p in k.split(','))
        if abs(x - key[0]) <= CLIFF_DETOUR_CAMP_AREA and abs(y - key[1]) <= CLIFF_DETOUR_CAMP_AREA:
            return int(v.get('count') or 0)
    return 0


def _detour_used(mem, key):
    if not key:
        return
    done = _flow(mem).setdefault('camp_detours', {})
    cur = done.get(f'{key[0]},{key[1]}') or {}
    done[f'{key[0]},{key[1]}'] = {'count': int(cur.get('count') or 0) + 1,
                                  'tick': int(mem.get('tick') or 0)}


def _cliff_detour_step(screen, mem, frame, state, marker):
    """Order the unit to a perpendicular cell one step off the cliff.

    Owner 2026-11-16: a cliff-edge camp sent straight home marches into the
    wall. The escape must be a real move order to an adjacent cell on the
    open side — not a nudge of the home-bound marker — so the unit
    physically steps perpendicular; the freed camp is then recalled home.

    The armed escape cell is kept: frames where the cliff pixels read thin
    used to fall through to the roof servo, which walked the marker back
    toward the castle and the escape never landed (g1190 2026-11-16: every
    camp=[12,155] detour stalled at marker=[28,163], six steps, re-planned).
    """
    if state.get('gather'):
        return None
    if (mem.get('y_jumped') or {}).get('RECALL'):
        # #4886: Yジャンプで目的の自城へ移動済みの画面はキャンプ画面ではないため、
        # ジャンプ前のキャンプ座標で崖スキャンして自城屋根から逸らさない。
        return None
    camp_key = _detour_camp_key(state)
    if camp_key and _detour_count(mem, camp_key) >= CLIFF_DETOUR_MAX:
        _record(mem, 'camp_cliff_detour_done', chart_step=None,
                observed_metric={'camp': state.get('target'), 'camp_key': list(camp_key),
                                 'escapes': CLIFF_DETOUR_MAX},
                reason='離脱を繰り返しても野営が崖を離れないため、離脱を打ち切り帰還の確定へ進む')
        return None
    escape = state.get('escape_cell')
    if not _camp_target(escape):
        target = state.get('target')
        base = target if _camp_target(target) else [int(marker[0]), int(marker[1])]
        found = _cliff_escape(frame, base)
        if found is None:
            return None
        side, (ex, ey) = found
        escape = [int(ex), int(ey)]
        state['cliff_detours'] = int(state.get('cliff_detours') or 0) + 1
        state['cliff_detour'] = side
        state['escape_cell'] = escape
        state.pop('detour_best', None)
        state.pop('detour_stall', None)
        _record(mem, 'camp_cliff_detour',
                observed_metric={'camp': state.get('target'), 'marker': list(marker),
                                 'side': side, 'escape': escape,
                                 'step': state['cliff_detours']},
                reason='崖に隣接しているため、まず崖と垂直方向の隣接セルへ将軍を移動させて崖から離す')
    dist = abs(escape[0] - marker[0]) + abs(escape[1] - marker[1])
    best = state.get('detour_best')
    if best is None or dist <= best - 2:
        state['detour_best'] = dist
        state['detour_stall'] = 0
    else:
        state['detour_stall'] = int(state.get('detour_stall') or 0) + 1
        if state['detour_stall'] >= CLIFF_DETOUR_STALL:
            state.pop('escape_cell', None)
            state.pop('detour_best', None)
            state['detour_stall'] = 0
            _record(mem, 'camp_cliff_detour_stalled',
                    observed_metric={'camp': state.get('target'), 'marker': list(marker),
                                     'escape': escape},
                    reason='崖の離脱先へマーカーが近づかないため、離脱先を計り直す')
            return None
    mdx, mdy = escape[0] - marker[0], escape[1] - marker[1]
    if abs(mdx) <= RECALL_CONFIRM_PX and abs(mdy) <= RECALL_CONFIRM_PX:
        _detour_used(mem, camp_key)
        return _request_recall(mem, state, state.get('goal'), [pad('a')],
                               target_cell=escape, escape_move=True)
    if abs(mdx) >= abs(mdy):
        return [pad('right' if mdx > 0 else 'left', min(6, abs(mdx)))]
    return [pad('down' if mdy > 0 else 'up', min(6, abs(mdy)))]


def _camp_inside_roof(camp, roofs):
    """The tent detection fired on part of a castle sprite, not on a camp.

    g1190 2026-11-18 02:24: camp (134,120) was recalled 411 times and never
    arrived; the captured frame shows a castle at box (120,95)-(167,142) and
    the "camp" cell sits inside it. A real tent stands beside a castle, not
    inside its roof box.
    """
    cell = (camp or {}).get('target')
    if not (isinstance(cell, (list, tuple)) and len(cell) == 2):
        return False
    for roof in roofs or ():
        box = roof.get('box') if isinstance(roof, dict) else None
        if not (isinstance(box, (list, tuple)) and len(box) == 4):
            continue
        x0, y0, x1, y1 = box
        if int(x0) <= cell[0] <= int(x1) and int(y0) <= cell[1] <= int(y1):
            return True
    return False


Y_ROUTE_RETRIES = 6


def _y_route_step(screen, mem, state):
    """Camp recall through the whole-map jump (owner 2026-11-19: no きかん)."""
    goal = state.get('goal')
    if not goal:
        return []
    if (mem.get('y_jumped') or {}).get('RECALL') == goal:
        if screen.kind not in ('map', 'map_target', 'world_map'):
            # docich#5452: the jump receipt licenses the confirm A only on the
            # field, its picker or the live move-mode world map. Any other
            # screen means the mode was lost — a view-mode world map's A
            # already opened castle_menu — so the confirm A must never land
            # there. Close the stray menu and restart the recall.
            if (is_camp_menu(screen) or is_fort_menu(screen)
                    or screen.kind in ('castle_menu', 'general_list',
                                       'castle_info', 'main_menu')):
                return _recall_move_mode_lost(mem, state, screen)
            return []
        return _finish_hero_recall(mem, state, goal)
    state['steps'] = int(state.get('steps') or 0) + 1
    if not mem.get('y_jump') and state['steps'] > Y_ROUTE_RETRIES:
        # Owner 2026-11-23: camp recall must never fall back to きかん.
        # Re-arm the Y view instead and keep trying.
        _record(mem, 'camp_recall_y_retry', observed_metric={'goal': goal,
                                                            'steps': state['steps']},
                reason='全体マップ（Y）の受領が得られないため、きかんへ戻さずYジャンプを再試行する')
        state['steps'] = 0
        return []
    if screen.kind in ('map', 'world_map'):
        return _start_y_jump(mem, {'step': 'RECALL'}, goal, 'target')
    return []


# docich#5032: an accepted dispach is anonymous (the camp menu names no
# occupant we read), so arrival was never checked against a general and the
# camp stayed in the last status reading for months. After this many ticks the
# generals who were camped at dispatch must have been re-read; one still shown
# at 'camp' names the failure and sends the next attempt to another castle.
CAMP_ARRIVAL_TIMEOUT_TICKS = 600


def _camped_generals(mem, limit=8):
    rows = ((mem.get('general_statuses') or {}).get('statuses') or {})
    if not isinstance(rows, dict):
        return []
    return [name for name, row in rows.items()
            if isinstance(name, str) and isinstance(row, dict)
            and row.get('location') == 'camp'][:limit]


def _remember_camp_verify(mem, state):
    """Keep who was camped when this return was accepted (bounded, no guesses)."""
    names = _camped_generals(mem)
    if not names:
        _flow(mem).pop('camp_verify', None)
        return
    _flow(mem)['camp_verify'] = {
        'tick': int(mem.get('tick') or 0),
        'castle': state.get('goal'),
        'camp': list(state.get('target') or []),
        'generals': names,
    }


def camp_arrival_check(mem):
    """Name the generals an accepted return never brought home.

    Runs whenever the camp flow is consulted; it never inputs anything. A
    general whose status was not re-read since the dispatch keeps the check
    pending (an unread row is not evidence either way).
    """
    verify = _flow(mem).get('camp_verify')
    if not isinstance(verify, dict):
        return None
    dispatched = verify.get('tick')
    now = int(mem.get('tick') or 0)
    if type(dispatched) is not int or now - dispatched < CAMP_ARRIVAL_TIMEOUT_TICKS:
        return None
    rows = ((mem.get('general_statuses') or {}).get('statuses') or {})
    still, moved, unread = [], [], []
    for name in verify.get('generals') or ():
        row = rows.get(name) if isinstance(rows, dict) else None
        tick = row.get('tick') if isinstance(row, dict) else None
        if type(tick) is not int or tick <= dispatched:
            unread.append(name)
        elif row.get('location') == 'camp':
            still.append(name)
        else:
            moved.append(name)
    if still:
        goal = verify.get('castle')
        _record(mem, 'camp_recall_arrival_timeout',
                observed_metric={'castle': goal, 'camp': verify.get('camp'),
                                 'still_camped': still, 'arrived': moved,
                                 'unread': unread,
                                 'waited_ticks': now - dispatched},
                resulting_event='dispatched_general_still_camped',
                reason='受理した帰還の後も名簿の再読みで野営のままの将軍がいるため、到着しなかった帰還として名前を残す')
        camp = verify.get('camp')
        if (isinstance(camp, (list, tuple)) and len(camp) == 2
                and isinstance(goal, str) and goal):
            failures = _flow(mem).setdefault('recall_goal_failures', {})
            key = f'{int(camp[0])},{int(camp[1])}'
            pair = failures.get(key) if isinstance(failures.get(key), dict) else {}
            pair[goal] = int(pair.get(goal) or 0) + 1
            failures[key] = pair
        mem['uncertain'] = True
        _flow(mem).pop('camp_verify', None)
        return True
    if moved and not unread:
        _record(mem, 'camp_recall_arrival_confirmed',
                observed_metric={'castle': verify.get('castle'), 'arrived': moved,
                                 'waited_ticks': now - dispatched},
                resulting_event='dispatched_general_left_camp',
                reason='受理した帰還の後、野営だった将軍が名簿の再読みで城内・行軍に変わったため到着として記録する')
        _flow(mem).pop('camp_verify', None)
        return True
    return None


def _camp_recall_step(screen: Screen, mem, frame, *, observed_camps=None):
    """Owner rule (2026-09-28): a camp (野営) seen on screen is recalled.

    Measured in the isolated probe: A on our tent opens
    いどう/ステータス/キャンプ/きかん. Since docich#5105 (owner 2026-11-19:
    きかん禁止・いどう→Y) every recall confirms いどう; the field cursor it
    opens then jumps home through the whole map (Y) instead of the retired
    きかん picker. The destination is the nearest own castle (補給できる城).
    ``None`` means "no recall in flight": the caller keeps its ordinary map
    steering.
    """
    state = _flow(mem).get('recall')
    if not state:
        camp_arrival_check(mem)
    if screen.kind in {'battle', 'battle_menu', 'egg_battle_menu', 'monster_menu',
                       'egg_choice_menu', 'attack_started', 'defense_started', 'boss_attack_started'}:
        _mark_recall_interrupted(mem)
        _mark_recall_battle(mem)
        held = _recall_hold_or_abort(mem, state, screen)
        if held is not None:
            return held
        # A combat foreground explains a long dest pause (g1163 2026-11-14
        # 05:57-06:01: the dest sat at steps 1 for four minutes and the
        # month scene closed it). Throttled so a long battle logs once.
        now = int(mem.get('tick') or 0)
        if now - int(mem.get('camp_battle_tick') or 0) >= 60:
            mem['camp_battle_tick'] = now
            _record(mem, 'camp_recall_battle',
                    observed_metric={'stage': (state or {}).get('stage'), 'kind': screen.kind},
                    reason='戦闘が前面に出て帰還の操作が止まったことを記録する')
        return None  # combat owns this screen; keep the recall intention pending
    if mem.get('battle'):
        _mark_recall_interrupted(mem)
        _mark_recall_battle(mem)
        held = _recall_hold_or_abort(mem, state, screen)
        if held is not None:
            return held
        return [] if screen.kind == 'map' else None  # first result frame is not a finished battle
    if state is None:
        if screen.kind != 'map' or frame is None:
            return None
        if _month_end_soon(mem):
            return None     # the month-start scene would cut the destination picker
        if order_dispatch_starved(mem):
            # docich#5077: a starved boss-phase dispatch takes the map first;
            # only a NEW recall/hunt yields — one in flight still owns its menu.
            return None
        camps = own_camps(frame) if observed_camps is None else observed_camps
        camps = _filter_recall_skip(mem, camps)
        if camps and isinstance(frame, Frame):
            # The castle sprite's colours match the tent palette: drop a
            # "camp" that sits inside a castle roof box.
            roofs = castle_roofs(frame)
            kept = [c for c in camps if not _camp_inside_roof(c, roofs)]
            if len(kept) != len(camps):
                now = int(mem.get('tick') or 0)
                if now - int(_flow(mem).get('camp_castle_fp_tick') or 0) >= 100:
                    _flow(mem)['camp_castle_fp_tick'] = now
                    _record(mem, 'camp_sighting_on_castle',
                            observed_metric={'dropped': [list(c.get('target') or ())
                                                         for c in camps if c not in kept][:4],
                                             'roofs': [list(r.get('box') or ()) for r in roofs][:4]},
                            reason='検出したテントが城の屋根ボックス内にあるため、城スプライトの誤検出として除外する')
            camps = kept
        # Owner 2026-11-18 02:2x: the roster is NOT evidence that a visible tent
        # is empty. Four or five tents were on screen while every status row
        # said nobody was camped, so a sighting is never dismissed by the
        # roster (v1029's guard was removed).
        if camps and camp_stays(mem) and isinstance(frame, Frame) and castle_roofs(frame):
            # A corridor view keeps field camps beside their roofs: every one
            # of them is left camped by ``camp_stays`` and none can be the
            # boss-march foothold. The stale-landed boss-march camp is in the
            # boss's roam band, unreachable by cursor nav over the distance,
            # so open the X-menu しょうぐん roster and SELECT the overdue
            # sortie's general: the cursor jumps straight onto their tent.
            boss_target = chart.boss_castle(mem.get('chapter') or 0)
            overdue_general = next((_canonical_general(s['general'])
                                    for s in _sorties(mem).values()
                                    if isinstance(s, dict)
                                    and s.get('target') == boss_target
                                    and s.get('status') == 'overdue'
                                    and s.get('general')), None)
            skips = mem.get('general_jump_skip')
            skip_row = (skips.get(overdue_general)
                        if overdue_general and isinstance(skips, dict) else None)
            skip_ticks = 3600 if isinstance(skip_row, dict) and skip_row.get('absent') else 200
            skipping = (isinstance(skip_row, dict)
                        and int(mem.get('tick') or 0) - int(skip_row.get('tick') or 0) < skip_ticks)
            # Live g1288 2026-11-21 19:47-19:52: the recall started the roster
            # jump for an absent general (アニス) every ~2 minutes, ignoring the
            # general_jump_skip that map's arming honours, so the field stalled.
            if overdue_general and not skipping and not mem.get('general_jump'):
                mem['general_jump'] = {'general': overdue_general, 'phase': 'menu',
                                       'age': 0, 'total': 0, 'scrolls': 0,
                                       'menu_presses': 0}
                _record(mem, 'camp_revisit',
                        observed_metric={'general': overdue_general},
                        reason='回廊ビューの野営は全部野営停止のため、しょうぐん一覧からSELECTで期限切れボス行軍将軍の野営へ飛ぶ')
                return []
        if not camps:
            # The field frames keep coming but no tent is visible: record a
            # throttled sample so the camera/roof situation is measurable
            # (g1163 2026-11-14 03:42-04:36: 54 minutes without a camp_found).
            now = int(mem.get('tick') or 0)
            if now - int(mem.get('camp_hunt_tick') or 0) >= 100:
                mem['camp_hunt_tick'] = now
                start = mem.get('month_start_tick')
                elapsed = (now - start if type(start) is int else None)
                from ..os_layer.world import observed_cursor_cell, roof_camera_support
                origin = (observed_cursor_cell(screen, mem, frame)
                          if isinstance(frame, Frame) else None)
                support = roof_camera_support(mem, frame) if isinstance(frame, Frame) else None
                from ..strategy.orders import _owned as _owned_castles
                chart_own = {name: list(cell) for name, cell in
                             chart.castles(mem.get('chapter') or 0).items()
                             if name in _owned_castles(mem)}
                roofs_seen = ([{'kind': r.get('kind'), 'target': list(r['target'])}
                               for r in castle_roofs(frame)]
                              if isinstance(frame, Frame) else [])
                _record(mem, 'camp_hunt',
                        observed_metric={'cursor': list(_cursor(screen) or ()),
                                         'roofs': len(roofs_seen),
                                         'kind': screen.kind,
                                         **({'roofs_detail': roofs_seen} if roofs_seen else {}),
                                         **({'origin': list(origin)} if origin else {}),
                                         **({'camera_support': support[0],
                                             'camera': list(support[1])}
                                            if support else {}),
                                         **({'chart_own': chart_own} if chart_own else {}),
                                         'chart_all': {name: list(cell) for name, cell in
                                                       chart.castles(mem.get('chapter') or 0).items()},
                                         **({'month_elapsed': elapsed}
                                            if elapsed is not None else {})},
                        reason='野営が見えないまま地図フレームが続いているため、カメラと屋根の状況を記録する')
            # A camp that was located before can be revisited: the tents sit
            # near the castles and the camera only moves with the cursor, so a
            # hunt that waits never brings them back (all 32 generals camped,
            # g1190 94-xx). Bounded by the ARRIVE check inside nav_step.
            last = _flow(mem).get('camp_last_world')
            if not _camp_target(last) and camp_stays(mem):
                # An overdue boss sortie stale-landed at its aimed cell and
                # camped there; ``camp_world`` never resolves in roofless
                # terrain, so revisit the recorded target cell to re-detect
                # the tent and feed the boss-redirect chase.
                chapter = mem.get('chapter') or 0
                boss_target = chart.boss_castle(chapter)
                for s in _sorties(mem).values():
                    if (isinstance(s, dict) and s.get('target') == boss_target
                            and s.get('status') == 'overdue'
                            and _camp_target(s.get('aimed_cell'))):
                        last = list(s['aimed_cell'])
                        break
            if camp_stays(mem) and _camp_target(last):
                from ..os_layer.navigation import nav_step
                move = nav_step(screen, mem, frame, list(last))
                if move == 'arrived':
                    _flow(mem).pop('camp_last_world', None)
                    _record(mem, 'camp_revisit_arrived',
                            observed_metric={'camp_world': list(last)},
                            reason='最後に確認した野営の位置へカーソルを寄せ終えたため、テントの再検出を待つ')
                    return []
                if move in ('up', 'down', 'left', 'right'):
                    _record(mem, 'camp_revisit',
                            observed_metric={'camp_world': list(last), 'move': move},
                            reason='最後に確認した野営の位置へカーソルを寄せてテントを再発見する')
                    return [pad(move)]
                if move == 'infeasible':
                    _flow(mem).pop('camp_last_world', None)
            return None
        cursor = _cursor(screen)
        camp = min(camps, key=lambda c: (abs(c['target'][0] - cursor[0])
                                         + abs(c['target'][1] - cursor[1])) if cursor else 0)
        state = _flow(mem)['recall'] = {'stage': 'to_camp', 'target': list(camp['target']),
                                 'last_seen': list(camp['target']), 'steps': 0,
                                 **({'camp_body': body} if (body := camp_body_target(frame, camp['target'])) else {}),
                                 **({'camp_kind': camp['kind']} if camp.get('kind') else {})}
        # A fresh recall starts a fresh destination-jump budget and receipt:
        # the previous recall's ``y_jumped`` must not confirm this picker's
        # marker, and its spent attempts must not starve this recall's jump
        # (the fort and boss routes already reset the same pair).
        _reset_boss_jump(mem)
        _observe_recall_origin(screen, mem, frame, state)
        state['camp_world'] = _camp_world_cell(cursor, state.get('origin'), camp['target'])
        # Owner 2026-11-16 (fix A): re-wire the lost second leg. A camp goes
        # to the boss when it formed beside the north waypoint OR when a
        # boss-launched general (``boss_wave_generals``) is known to be
        # camping — in the boss phase the field camps are expired boss
        # marches. Other camps keep the ordinary nearest-castle recall.
        boss = boss_redirect_goal(mem)
        if boss:
            chapter = mem.get('chapter') or 0
            boss_target = chart.boss_castle(chapter)
            # A camp holding a general whose boss sortie never entered is an
            # expired boss march regardless of where the camera bound: the
            # empty-cell camp is its rest stop, not a corridor reinforcement.
            wave = {s.get('general') for s in _sorties(mem).values()
                    if isinstance(s, dict) and s.get('target') == boss_target
                    and s.get('status') not in ('arrived', 'cancelled')
                    and s.get('general')}
            actors = ((mem.get('camp_status_seen') or {}).get('actors') or {})
            cw = state.get('camp_world')
            near = (isinstance(cw, (list, tuple)) and len(cw) == 2
                    and abs(cw[0] - chart.CH9_BOSS_WAYPOINT[0]) <= 320
                    and abs(cw[1] - chart.CH9_BOSS_WAYPOINT[1]) <= 320)
            # A camp sighted in open terrain (no castle roof in the frame) is
            # in the boss's roam band: castle camping always parks beside its
            # own roof, so a roofless camp is an expired boss march that ran
            # out of road. When a boss sortie is in flight, redirect it at the
            # boss's fresh cell rather than recall it onto the corridor.
            open_field = isinstance(frame, Frame) and not castle_roofs(frame)
            if near or any(g in actors for g in wave) or (wave and open_field):
                state['boss'], state['goal'] = True, boss
        # Issue #4922: while chapter 10's White House is open, a camp in its
        # wall ring — or any expired boss march — is not recalled home. The
        # camp menu's いどう turns it into a boss_move whose RECALL jump picks
        # the White House leg (gate -> front door, or an outer corner first).
        if not state.get('boss') and _whitehouse_camp(mem, screen, state, frame):
            state['boss_move'], state['goal'] = True, chart.boss_castle(mem.get('chapter') or 0)
        if _camp_target(state.get('camp_world')):
            _flow(mem)['camp_last_world'] = list(state['camp_world'])
        elapsed = (int(mem.get('tick') or 0) - mem['month_start_tick']
                   if type(mem.get('month_start_tick')) is int else None)
        estimate = None
        usable = [int(d) for d in (mem.get('month_durations') or [])[-3:] if type(d) is int]
        if usable:
            estimate = max(usable)
        roofs_seen = len(castle_roofs(frame)) if isinstance(frame, Frame) else 0
        state['move_only'] = True
        _record(mem, 'camp_found', observed_metric={'camp': list(camp['target']),
                                                    **({'month_elapsed': elapsed} if elapsed is not None else {}),
                                                    **({'month_estimate': estimate} if estimate else {}),
                                                    **({'camp_world': state['camp_world']}
                                                       if state.get('camp_world') else {}),
                                                    **({'roofs': roofs_seen} if roofs_seen else {}),
                                                    **({'origin': list(state['origin'])}
                                                       if state.get('origin') else {}),
                                                    **({'cursor_cell': list(mem['cursor'])}
                                                       if isinstance(mem.get('cursor'), (list, tuple))
                                                       and len(mem['cursor']) == 2 else {}),
                                                    **({'kind': camp['kind']} if camp.get('kind') else {})},
                reason='画面に自軍の野営を見つけたため補給できる自軍城への帰還を指示する')
    # Issue #4922: the monthly task's recall state (post_month_status) never
    # passes the creation-time classification above, so a White House camp it
    # queued as an ordinary recall is reclassified here while the destination
    # is still unchosen.
    if (state.get('stage') in ('to_camp', 'hero_focus', 'hero_open', 'y_route', 'menu')
            and not (state.get('boss') or state.get('boss_move') or state.get('fort')
                     or state.get('gather') or state.get('hero') or state.get('move_visible'))
            and _whitehouse_camp(mem, screen, state, frame)):
        state['boss_move'], state['goal'] = True, chart.boss_castle(mem.get('chapter') or 0)
    # docich#5108: a text frame that is not the camp/fort menu or a status
    # panel cannot be operated by this flow. It is held exactly like a battle
    # foreground — the step budget is not spent and the intention survives —
    # and the frame returns to its real owner so the message can advance.
    # A partial camp menu (only one menu word visible, e.g. 'いどう' while the
    # window is still fading in) is operable: the menu stage reads it. The
    # A sent dispatch is not verified by a text/month scene. Keep the scene
    # with its normal owner and do not spend the six-observation receipt window.
    if (screen.kind == 'text'
            and state.get('stage') in ('await_dispatch', 'await_dispatch_stale')):
        _mark_recall_interrupted(mem)
        return None
    # A dome camp is exempt too — the menu stage's own DOME_SCREEN_WAIT owns
    # its non-menu screens. Other text frames are held without spending the
    # field-navigation budget.
    if (screen.kind == 'text' and state.get('stage') != 'await_dispatch'
            and state.get('camp_kind') != 'dome' and not (
            is_camp_menu(screen) or is_fort_menu(screen)
            or any(word in (screen.text or '')
                   for word in ('いどう', 'ステータス', 'キャンプ', 'きかん'))
            or 'Aボタンでステータスひょうじ' in (screen.text or ''))):
        _mark_recall_interrupted(mem)
        held = _recall_hold_or_abort(mem, state, screen)
        if held is not None:
            return held
        return None
    # docich#5134: a 'map' frame without the field cursor cannot be steered —
    # the post-battle return animation and fades look exactly like that, and
    # g1190 ch6's to_camp recall spent all 91 'map' observations on them
    # before camp_recall_limit. Hold it like a battle foreground: the budget
    # is not spent and RECALL_HOLD_TICK_LIMIT bounds the wait. A HUD-less
    # cinematic map (map_step's blind chapter intermission) keeps the old
    # counted path so the recall still releases to the scene's advance
    # instead of holding the field for the whole limit.
    if (screen.kind == 'map' and state.get('stage') == 'to_camp'
            and not _cursor(screen) and (screen.header or screen.text)):
        held = _recall_hold_or_abort(mem, state, screen)
        if held is not None:
            return held
        return []
    state.pop('hold_since', None)
    if state.get('battle_return'):
        stage = state.get('stage')
        if stage in ('to_camp', 'menu') and screen.kind == 'map':
            # Keep the marker through resumable non-map frames; only the first
            # map after combat establishes the new cursor baseline.
            state.pop('battle_return', None)
            _battle_return_reset(mem, state)
        elif stage in MOVE_MODE_STAGES:
            if screen.kind == 'map':
                # docich#5452: a post-battle 'map' is a plain field — the
                # いどう move mode died with the battle and cannot be
                # re-confirmed from this screen. Never resume the Y route
                # here: the map it opens is a view, and its A opens the
                # castle menu instead of dispatching the return.
                state.pop('battle_return', None)
                return _recall_move_mode_lost(mem, state, screen)
            if screen.kind == 'map_target' and stage == 'dest':
                # The destination picker may have survived combat — the game
                # restores it — but every jump receipt names the pre-battle
                # marker. Drop them like observe_sortie_transition does so a
                # fresh jump re-places the marker before any confirm; the
                # stage body below re-drives from this cleared state.
                state.pop('battle_return', None)
                _reset_boss_jump(mem)
                (mem.get('y_jumped_res') or {}).pop('RECALL', None)
                state.pop('picker_confirmed', None)
                state.pop('picker_from_menu', None)
            elif screen.kind in ('map_target', 'world_map'):
                # A whole map after combat is indistinguishable from the
                # view a dead いどう mode would leave, and a picker screen
                # belongs to no other move-mode stage: restart the recall
                # instead of steering or confirming it blind.
                state.pop('battle_return', None)
                return _recall_move_mode_lost(mem, state, screen)
            # Other kinds (menus, panels, fades) keep the marker until a
            # decidable screen appears.
        elif stage not in ('to_camp', 'menu'):
            # The recall left the field-navigation/menu stages before a map
            # returned, so this reset no longer applies to the active route.
            state.pop('battle_return', None)
    if state.get('stage') in MOVE_MODE_STAGES:
        castle_menu_open = screen.kind in ('castle_menu', 'general_list',
                                           'castle_info', 'main_menu')
        # A camp menu can still be fading out for a frame or two right after
        # the いどう select that entered 'dest'; only a persistent one means
        # the move mode never took. A castle menu is never a fade — only the
        # recall's own mis-fired input could open it (a view-mode world
        # map's A on an own castle).
        camp_menu_open = ((is_camp_menu(screen) or is_fort_menu(screen))
                          and int(state.get('steps') or 0) > 2)
        if castle_menu_open or camp_menu_open:
            # docich#5452: mode-loss evidence — never steer or confirm inside
            # these menus; close the stray menu and restart the recall.
            return _recall_move_mode_lost(mem, state, screen)
    if state.get('stage') in ('await_dispatch', 'await_dispatch_stale'):
        return _recall_dispatch_step(screen, mem, state)
    _observe_fort_frame(screen, state)
    state['steps'] = int(state.get('steps') or 0) + 1
    stage = state.get('stage')
    # A map_target following our A is the fort's own sortie picker. Let it
    # reach _fort_step even at the global boundary so another order cannot
    # steer the destination before the recall owner sees it.
    fort_picker_ready = (
        stage == 'fort_status'
        and state.get('fort_picker_requested') is True
        and screen.kind == 'map_target'
    )
    if stage in FORT_STAGES:
        stage_observations = int(state.get(f'{stage}_observations') or 0)
        if stage_observations > FORT_STAGE_OBSERVATION_LIMIT and not fort_picker_ready:
            return _abort_camp_recall(
                mem, state, screen,
                '砦の画面が段階別の観測上限内に進行しないため中断し位置を再測定する')
    if state['steps'] > RECALL_LIMIT and not fort_picker_ready:
        return _abort_camp_recall(
            mem, state, screen,
            '野営の帰還指示が上限内に完了しないため断念し位置を再測定する')
    stage = state.get('stage')
    if stage == 'hero_focus':
        if screen.kind != 'map':
            return []                  # battle results and messages fade out first
        # Owner 2026-11-19: きかん is too slow for every return, not just the
        # camp one — open the move (いどう) view and take the whole-map Y jump
        # home instead. The menu route stays only as the bootstrapping fallback
        # when no goal is measured yet.
        goal = state.get('goal') or (_fort_recall_goal(mem, state) if state.get('fort')
                                     else _recall_goal(mem))
        if goal:
            state['goal'] = goal
            state['stage'], state['steps'] = 'y_route', 0
            _record(mem, 'camp_recall_y_route', observed_metric={'goal': goal},
                    reason='野営からの帰還は「きかん」を使わず、全体マップ（Y）で自城へ直接戻る（オーナー指示）')
            return _y_route_step(screen, mem, state)
        # docich#4755: the picker fallback keeps firing while y_route never
        # engages — record which goal inputs were missing so the next fix
        # targets the actual gap instead of guessing.
        _record(mem, 'camp_recall_goal_unresolved', observed_metric={
            'origin': state.get('origin'), 'camp_world': state.get('camp_world'),
            'target': state.get('target'), 'fort': bool(state.get('fort')),
            'stage_from': state.get('stage')},
            reason='hero_focus で帰還goalが解決できず picker 経路へフォールバック')
        # Owner 2026-11-23: no きかん fallback.  Wait for a measurable goal and
        # retry the Y jump; the picker/menu route is forbidden for camp recall.
        state['steps'] = 0
        return []
    if stage == 'y_route':
        return _y_route_step(screen, mem, state)
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
            # A fresh camera-bound origin corrects the tent's screen cell after
            # a shift; it never licenses opening from an unconfirmed position.
            if (state.get('camp_world') and state.get('origin') and cursor):
                cell, origin = state['camp_world'], state['origin']
                state['target'] = [cell[0] - origin[0] + cursor[0], cell[1] - origin[1] + cursor[1]]
            target = state.get('target')
            last = state.get('last_seen')
            if (not state.get('occluded_enter') and cursor
                    and _camp_target(last) and _camp_target(target)
                    and abs(last[0] - target[0]) <= RECALL_ARRIVE_PX
                    and abs(last[1] - target[1]) <= RECALL_ARRIVE_PX
                    and abs(cursor[0] - target[0]) <= RECALL_OCCLUDED_PX
                    and abs(cursor[1] - target[1]) <= RECALL_OCCLUDED_PX):
                state['occluded_enter'] = True
                _observe_recall_origin(screen, mem, frame, state)
                state['stage'], state['steps'] = 'menu', 0
                _record(mem, 'camp_enter',
                        observed_metric={'camp': list(target), 'occluded': True,
                                         'cursor': list(cursor), 'last_seen': list(last),
                                         'target': list(target)},
                        reason='カーソルが野営画像を覆っているため、直前に実測したtargetを信頼してきかんメニューを開く')
                return [pad('a')]
            # The tent was recorded off the screen (a clipped camp at the
            # edge). The camera only moves with the cursor, so waiting here
            # never brings it back: push the cursor to that edge for a
            # bounded number of observations (g1190 91-11: camp target
            # [-1,9], camp_unobserved after three reads, army stranded).
            if (cursor and _camp_target(target)
                    and int(state.get('edge_scroll') or 0) < EDGE_SCROLL_LIMIT
                    and (target[0] < 0 or target[1] < 0
                         or target[0] >= SCREEN_W or target[1] >= SCREEN_H)):
                state['edge_scroll'] = int(state.get('edge_scroll') or 0) + 1
                if state['edge_scroll'] == 1:
                    _record(mem, 'camp_edge_scroll',
                            observed_metric={'camp': list(target), 'cursor': list(cursor)},
                            reason='保存した野営が画面外にあるため、カーソルを端へ送ってカメラを寄せる')
                dx = target[0] - cursor[0]
                dy = target[1] - cursor[1]
                if abs(dx) >= abs(dy):
                    move = 'left' if dx < 0 else 'right'
                else:
                    move = 'up' if dy < 0 else 'down'
                return [pad(move, CAMP_STEP_FRAMES)]
            missing = state['camp_missing_reads'] = int(state.get('camp_missing_reads') or 0) + 1
            if missing == 1:
                _record(mem, 'camp_position_unconfirmed',
                        observed_metric={'previous_target': state.get('target'),
                                         'cursor': list(_cursor(screen) or ()),
                                         'missing': missing,
                                         'camp_world': state.get('camp_world')},
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
        # Keep the camp this recall started on: re-picking the nearest tent
        # each step flipped between two visible camps and the cursor never
        # arrived (g1190 2026-11-15 74-12: 91 observations aborted). Only a
        # detection that no longer matches the stored cell re-picks.
        stored = state.get('target')
        camp = None
        if _camp_target(stored):
            near = min(camps, key=lambda c: (abs(c['target'][0] - stored[0])
                                             + abs(c['target'][1] - stored[1])))
            if (abs(near['target'][0] - stored[0])
                    + abs(near['target'][1] - stored[1]) <= RECALL_SKIP_RADIUS):
                camp = near
        if camp is None:
            camp = min(camps, key=lambda c: (abs(c['target'][0] - cursor[0])
                                             + abs(c['target'][1] - cursor[1])) if cursor else 0)
        body = camp_body_target(frame, camp['target'])
        if body:
            state['camp_body'] = body
        else:
            state.pop('camp_body', None)
        if (body is not None and _camp_target(stored) and list(stored) == list(body)):
            # #4901: the menu stage's body retry retargeted this same camp.
            # Restoring its flag cell here pressed the dead cell again, so the
            # recall looped camp_menu_not_opened until it gave up (ch10 v1317).
            state['last_seen'] = list(stored)
        else:
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
        # The field cursor moves about one pixel per frame; the old 8-frame
        # cap walked at most 24 px per observation and a camp across the
        # screen needed the whole 90-observation budget (g1190 2026-11-15).
        if abs(dx) >= abs(dy):
            frames = min(CAMP_STEP_FRAMES, max(1, abs(dx) - RECALL_ARRIVE_PX))
            return [pad('right' if dx > 0 else 'left', frames)]
        frames = min(CAMP_STEP_FRAMES, max(1, abs(dy) - RECALL_ARRIVE_PX))
        return [pad('down' if dy > 0 else 'up', frames)]
    if stage == 'menu' and is_fort_menu(screen):
        enter_fort(mem, state)
        stage = 'fort_menu'
    if stage in FORT_STAGES:
        actions = _fort_step(screen, mem, state)
        if actions is not None:
            return actions
        # An unrelated destination picker is not owned by the fort recall.
        # Leave it to the normal map/order flow instead of consuming it as an
        # empty recall action.
        if (screen.kind == 'map_target'
                and state.get('stage') == 'fort_status'
                and state.get('fort_picker_requested') is not True):
            return None
        stage = state.get('stage')
        if (stage == 'dest' and not state.get('boss') and not state.get('boss_move')
                and _whitehouse_camp(mem, screen, state, frame)):
            state['boss_move'], state['goal'] = True, chart.boss_castle(mem.get('chapter') or 0)
    if stage == 'menu' and state.get('relay') and not is_camp_menu(screen):
        # The whole-island picker closes one or more frames after the B that
        # #4534 sends when it re-aims a camp at the relay; the picker screen
        # itself used to pop the recall flow and the aim was lost (live trace
        # 2026-11-18 09:35:43: stage menu + relay, then the flow vanished).
        # Hold briefly so the camp menu can come back and offer いどう.
        waits = int(state.get('relay_menu_waits') or 0) + 1
        state['relay_menu_waits'] = waits
        if waits <= RELAY_MENU_WAIT:
            return []
        state['relay_menu_waits'] = 0
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
                    body = state.get('camp_body')
                    if (isinstance(body, list) and len(body) == 2
                            and reopens >= 2 and state.get('target') != body):
                        # The flag cell did not open the menu twice; the tent
                        # body centre is the other selectable cell.
                        state['target'] = list(body)
                        state['last_seen'] = list(body)
                        _record(mem, 'camp_enter_body_retry',
                                observed_metric={'camp': list(body), 'reopens': reopens},
                                reason='野営の旗セルで2回メニューが開かないため、テント本体の中心セルへ合わせ直す')
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

        chapter = mem.get('chapter') or 0
        visible_route = chapter == 9 or chart.gateway(chapter)
        if visible_route and not (state.get('boss') or state.get('boss_move')):
            # Owner 2026-11-15 measured this on the chapter-9 corridor camp:
            # the field いどう lets the visible castle be picked where it
            # stands. #4468 extends it to gateway chapters (chapter 11): the
            # whole-island picker needed several 1-hop observations to walk the
            # ring to the goal, and a month scene closed it before the A.
            # Other chapters keep the measured きかん picker flow.
            candidates = _visible_recall_candidates(mem, state, frame)
            if state.get('gather'):
                # Gathering at the last castle: never divert to the nearest
                # visible castle, only to the gather target itself.
                target = next((c for c in candidates if c['castle'] == state['gather']
                               and not _recall_blocked_twice(mem, c['castle'])), None)
                if target is not None:
                    state['move_visible'] = {'castle': target['castle'],
                                             'world': list(target['world'])}
                    _record(mem, 'camp_gather_visible',
                            observed_metric={'castle': target['castle']},
                            reason='集結先の最後の城が画面上に見えているため、いどうで集結先へ送る')
            else:
                visible = _visible_own_castle(mem, frame, candidates)
                # A back-half camp with no own back-half castle has nothing it
                # can reach on its screen: every visible own castle (home and
                # hub included) is across the cliff. g1190 2026-11-18 08:27:
                # the camp at (140,112) picked アルマムーン by いどう and could
                # not cross — the same 117-failure hollow #4520 relays away
                # from. Let the relay below own that camp instead of the
                # visible move; an owned back-half castle keeps the move.
                visible, relay = _visible_move_or_relay(mem, state, visible)
                if visible is not None:
                    state['move_visible'] = visible
                    _record(mem, 'camp_recall_visible_move',
                            observed_metric={'castle': visible['castle'], 'world': visible['world']},
                            reason='画面上に自軍城が見えているため、全島ピッカーではなくいどうで可視城へ帰還する')
                elif visible is not None:
                    _record(mem, 'camp_recall_relay_over_visible',
                            observed_metric={'castle': visible['castle'],
                                             'world': visible['world'],
                                             'relay': list(relay['cell']),
                                             'camp': list(state.get('target') or ())},
                            reason='可視城が崖の向こうにあるため、いどう先を後半中央の中継セルへ切り替える')
                elif candidates:
                    _record(mem, 'camp_recall_visible_blocked',
                            observed_metric={'castles': [c['castle'] for c in candidates],
                                             'blocked': dict(_flow(mem).get('camp_recall_blocked') or {}),
                                             'origin': list(state['origin']) if state.get('origin') else None},
                            reason='見えている自軍城はすべて帰還に失敗済みのため、いどうを使わず全島ピッカーで別の城を選ぶ')
        if not (state.get('boss') or state.get('boss_move') or state.get('gather')
                or state.get('move_visible')):
            relay = _relay_target(mem, state)
            if relay is not None:
                state['relay'] = relay
                state['move_visible'] = {'cell': list(relay['cell']), 'relay': True}
                _record(mem, 'camp_recall_relay_start',
                        observed_metric={'cell': list(relay['cell']), 'hub': relay.get('hub'),
                                         'camp': list(state.get('target') or ())},
                        reason='後半の自軍城が無くハブへ直行すると崖に詰まるため、まず後半中央へいどうで移動する')
        # Owner 2026-11-19 (#4736): いどう opens the field cursor (screen.kind=='map').
        # docich#5105 retires the v1362 きかん fallback entirely (owner policy
        # 2026-11-19: 「きかん禁止・いどう→Y」): every recall takes いどう and
        # the 'dest' stage hands the field cursor to the whole-map Y route — a
        # resolved goal jumps straight home, an unresolved one opens the view
        # so its flag survey can name the own castle (world_map_step). The
        # きかん ring picker is never selected again; boss / boss_move /
        # gather / move_visible / relay / fort keep their own dedicated routes.
        choice = 'いどう'
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
        state['stage'] = 'relay' if state.get('relay') else 'dest'
        state['steps'] = 0            # each stage gets the full observation budget
        # #4890: 新しい「いどう」ピッカーを開く瞬間に前回帰還の RECALL Yジャンプ履歴を
        # 必ずクリアする（名前付き帰還で前の将軍の y_jumped/y_jumps が残ると、
        # 未ジャンプのまま現在地キャンプの足元を自城と誤認して確定してしまう）。
        (mem.get('y_jumps') or {}).pop('RECALL:target', None)
        (mem.get('y_jumped') or {}).pop('RECALL', None)
        (mem.get('y_jumped_res') or {}).pop('RECALL', None)
        state.pop('picker_confirmed', None)
        state['picker_from_menu'] = True
        return [pad('a')]
    if stage == 'relay':
        # いどう opened a field-cursor move. Steer it to the relay cell; the
        # whole-island picker geometry (and its chapter-11 offset mismatch)
        # is not involved in this leg.
        if screen.kind in ('map', 'map_target'):
            return _visible_move_dest_step(screen, mem, frame, state)
        _flow(mem).pop('recall', None)
        mem['uncertain'] = True
        return [pad('b')] if screen.kind == 'world_map' else []
    if stage == 'dest':
        if screen.kind != 'map_target':
            if screen.kind == 'map' and state.get('move_visible'):
                return _visible_move_dest_step(screen, mem, frame, state)
            if state.get('move_visible'):
                state.pop('move_visible', None)   # the game opened the island picker
            if screen.kind == 'map':
                if (state.get('boss') or state.get('boss_move') or state.get('fort')
                        or state.get('relay')):
                    _flow(mem).pop('recall', None)
                    mem['uncertain'] = True
                    _hold_after_picker(mem, state)
                    return []
                # docich#5105 (owner 2026-11-19: きかん禁止・いどう→Y): いどう
                # opened the field cursor. A goal that resolves here takes the
                # measured whole-map Y jump straight home (hero_focus uses the
                # same _y_route_step); one that does not opens the view blind
                # so its own-castle flags can name the destination — the
                # world_map dest stage binds it through _picker_goal.
                goal = (state.get('goal') or state.get('gather')
                        or _recall_goal(mem))
                if goal:
                    state['goal'] = goal
                    state['stage'], state['steps'] = 'y_route', 0
                    _record(mem, 'camp_recall_y_route',
                            observed_metric={'goal': goal},
                            reason='いどうのフィールドカーソルから全体マップ（Y）で自軍城へ直接戻る（オーナー指示）')
                    return _y_route_step(screen, mem, state)
                if not state.get('map_y_route'):
                    state['map_y_route'] = True
                    state['map_y_waits'] = 0
                    _record(mem, 'camp_recall_map_y_route',
                            observed_metric={'origin': state.get('origin'),
                                             'camp_world': state.get('camp_world'),
                                             'camp': list(state.get('target') or ())},
                            reason='現在地から帰還先を決められないため、全体マップの自軍城旗で確定する')
                    return [pad('y')]
                # The view takes a frame or two to open; a second Y while it
                # is still on 'map' would land inside the view and close it.
                waits = int(state.get('map_y_waits') or 0) + 1
                state['map_y_waits'] = waits
                if waits < 5:
                    return []
                state['map_y_waits'] = 0
                return [pad('y')]        # the first Y was swallowed; press again
            return [pad('b')] if screen.kind == 'world_map' else []
        # #4885: 一度本物の行き先ピッカーを確認した後は、camp_cliff_detour 等で
        # 赤いテント・砦・自軍城の真下へマーカーを動かしても赤い屋根を将軍旗と
        # 誤認して帰還ピッカーを中断しない（初回フレームの偽G検出 #4468 は維持）。
        # #4917: さらに、キャンプメニューから いどう を確定した直後（steps<=2）に
        # そのキャンプ自身の座標上（<=8px）へ出たピッカーは、キャンプ直上の赤い城屋根や
        # 溶岩・別テント旗が重なっていても本物のピッカーとして確定する。
        if state.get('picker_confirmed'):
            marker = screen.marker
        else:
            marker = _picker_marker(screen, frame)
            if (not marker and screen.marker and state.get('picker_from_menu')
                    and int(state.get('steps') or 0) <= 2):
                camp = state.get('target')
                if (_camp_target(camp)
                        and abs(screen.marker[0] - camp[0]) <= 8
                        and abs(screen.marker[1] - camp[1]) <= 8):
                    marker = screen.marker
            state.pop('picker_from_menu', None)
        if screen.marker and not marker:
            # #4468: the blue G belongs to a field general, not an open picker.
            # Never send the recall A here — there is no destination to confirm
            # — and leave the input to the ordinary field policy (no blind B on
            # a screen whose kind came only from this badge).
            _flow(mem).pop('recall', None)
            mem['uncertain'] = True
            _record(mem, 'camp_recall_picker_not_open', chart_step=None,
                    observed_metric={'marker': list(screen.marker), 'stage': 'dest'},
                    reason='青いGはフィールド将軍のバッジで行き先ピッカーが開いていないため、帰還Aを送らず中断する')
            return []
        if marker:
            state['picker_confirmed'] = True
        if state.get('boss'):
            return _boss_redirect_dest_step(screen, mem, frame, state, marker)
        if state.get('boss_move'):
            return _boss_move_dest_step(screen, mem, frame, state, marker)
        roofs = ([r for r in castle_roofs(frame) if r['kind'] == 'own' and not r['clipped']]
                 if frame is not None else [])
        if marker:
            detour = _cliff_detour_step(screen, mem, frame, state, marker)
            if detour is not None:
                return detour
            # A 砦 asks for the nearest own castle (owner 2026-11-04). The hero's
            # castle is only _recall_goal's last resort, so a fort must not
            # consult it first: with no bound origin it answered アルマムーン
            # and the Y jump left the fort's neighbourhood.
            goal = state.get('goal') or (_fort_recall_goal(mem, state) if state.get('fort')
                                         else _recall_goal(mem))
            state['goal'] = goal
            jumped = (mem.get('y_jumped') or {}).get('RECALL') == goal
            if goal and jumped and not castle_roofs(frame):
                # Roofs hidden after a measured jump: the marker stands on the
                # goal castle, so confirm it. This covers the ordinary camp
                # too — a real own castle beats the confirm-anywhere fallback.
                return _finish_hero_recall(mem, state, goal)
            if (goal and not jumped and not state.get('cliff_detour')
                    and (mem.get('y_jumps') or {}).get('RECALL:target', 0) < 2):
                # Owner 2026-11-19/21: 帰還は見えている城へのカーソル移動ではなく
                # 全体マップ（Y）で自城へ直接戻る。屋根が見えていても先にジャンプし、
                # 受領後に _finish_hero_recall が確定する。崖離脱だけは先に
                # マーカーを動かすためジャンプしない（次観測で確定へ）。
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
                    observed_metric={'marker': list(marker), 'roofs': 0,
                                     'goal': state.get('goal'),
                                     'origin': state.get('origin'),
                                     'camp_world': state.get('camp_world'),
                                     'camp_last_world': _flow(mem).get('camp_last_world')},
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
            # Owner 2026-11-19: the in-place return judgement is very slow on
            # screen, so one failed return switches to the whole-map jump
            # instead of walking the field again. The jump budget keeps its
            # own bound, so this cannot loop.
            goal = state.get('goal') or (_fort_recall_goal(mem, state) if state.get('fort')
                                         else _recall_goal(mem))
            if (marker and goal and (mem.get('y_jumps') or {}).get('RECALL:target', 0) < 2
                    and not mem.get('y_jump') and not mem.get('castle_jump')):
                state['goal'] = goal
                _record(mem, 'camp_recall_y_jump_fallback',
                        observed_metric={'goal': goal, 'roofs': len(roofs)},
                        reason='帰還先の判定が一度失敗したため、全体マップ（Y）で自軍城へ直接戻る')
                return _start_y_jump(mem, {'step': 'RECALL'}, goal, 'target')
            return [pad('b')]
        roof = min(roofs, key=lambda r: abs(r['target'][0] - marker[0]) + abs(r['target'][1] - marker[1]))
        dx = roof['target'][0] - marker[0]
        dy = roof['target'][1] - marker[1]
        if abs(dx) <= RECALL_CONFIRM_PX and abs(dy) <= RECALL_CONFIRM_PX:
            if state.get('hero') or state.get('fort'):
                return _finish_hero_recall(mem, state, state.get('goal'))
            # Name the destination so the arrival roster read is scheduled
            # (the roof only carries pixels; without a goal the recalled
            # generals stayed unaccounted, g1163 2026-11-13 15:5x-16:3x).
            goal = state.get('goal') or _recall_goal(mem)
            return _request_recall(mem, state, goal, [pad('a')],
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
    # #4937: chapter 9's RECALL jump must also measure the unit's position on
    # the Y view so _ch9_cliff_detour can pick the north corridor / east mouth
    # leg when needed instead of confirming directly into the central cliff.
    ch9_need_jump = ((mem.get('chapter') or 0) == 9
                     and (mem.get('y_jumped') or {}).get('RECALL') != goal)
    if not ch9_need_jump and _map._target_roof_under_marker(screen, mem, frame, pseudo):
        _reset_boss_jump(mem)       # the receipt is spent by this confirmation
        return _request_recall(mem, state, goal, [pad('a')], target_cell=list(marker), boss=True)
    if (mem.get('y_jumps') or {}).get('RECALL:target', 0) < BOSS_JUMP_LIMIT:
        return _start_y_jump(mem, {'step': 'RECALL'}, goal, 'target')
    return _abort_boss_redirect(mem, state, 'boss_jump_failed',
                                'ボスのセルへマーカーを合わせられないため再出撃を取り消す')


def _site_king_down(mem, name):
    """A chapter-4 site whose king's defeat is already measured.

    Delegates to the strategy layer's single definition so the Y-view's
    closed marker, the boss victories and the all-four fallback cannot drift
    apart. Only chapter 4's own site names are asked (other chapters' boss
    castle is not one of the four).
    """
    from ..strategy.orders import _site_king_down as king_down
    return ((mem.get('chapter') or 0) == 4 and name in chart.boss_sites(4)
            and king_down(mem, name))


def _boss_move_goal(mem, state, marker=None):
    """The concrete boss site this いどう aims at, or None.

    #3505 assumes one boss castle, but chapter 4 spreads the boss over four
    四季王 sites and ``chart.boss_castle(4)`` answers the label 'ボス' with no
    cell. Live g1342 2026-11-24 22:14: a chapter-4 boss_move recall pressed Y
    with that goal, ``_y_jump_step`` could not unpack a cell and the brain
    raised on every observation (the whole-map G cursor appeared stopped).
    Prefer the goal the monthly task measured from the roster marker, then the
    site nearest the measured camp; never invent a site from nothing.

    Cleared sites never qualify (live g1342 2026-11-25 01:12: the 北西 king
    was down and the camp still aimed a いどう at it). A measured goal that
    is now closed yields None — aborting the redirect is safer than marching
    the camper onto a dead site; the monthly task recalls him home instead.
    A missing goal falls back to the nearest live site only.
    """
    chapter = mem.get('chapter') or 0
    cells = chart.castles(chapter)
    goal = state.get('goal')
    if isinstance(goal, str) and cells.get(goal):
        return None if _site_king_down(mem, goal) else goal
    sites = [name for name in chart.boss_sites(chapter)
             if isinstance(name, str) and cells.get(name)
             and not _site_king_down(mem, name)]
    if len(sites) == 1:
        return sites[0]
    if not sites:
        return None
    anchor = state.get('camp_world') or state.get('origin')
    if (isinstance(anchor, (list, tuple)) and len(anchor) == 2
            and all(type(value) is int for value in anchor)):
        return min(sites, key=lambda name: (abs(cells[name][0] - anchor[0])
                                            + abs(cells[name][1] - anchor[1]), name))
    return None


def _boss_move_dest_step(screen, mem, frame, state, marker):
    """Aim the destination marker at the boss castle and confirm (いどう).

    #3505: the same Y-jump route a boss sortie takes. Unlike the chapter-9
    redirect this boss does not move, so the fixed chart cell is enough —
    ``_target_roof_under_marker`` still requires a jump receipt or the marker
    measured on the cell, never a blind confirm.
    """
    from . import map as _map
    goal = _boss_move_goal(mem, state, marker)
    state['goal'] = goal
    if not goal or not marker or frame is None:
        state['boss_lost'] = int(state.get('boss_lost') or 0) + 1
        if state['boss_lost'] < 3:
            return []
        return _abort_boss_redirect(mem, state, 'boss_position_lost',
                                    'ボスの位置を確認できないため再移動を取り消す')
    pseudo = {'step': 'RECALL', 'target': goal, 'source': None}
    # #4922: chapter 10's White House requires a Y-jump receipt so _y_jump_step
    # measures the camp's actual position on the ring and picks the walkable leg;
    # never short-circuit confirm near the wall without jumping first.
    ch10_need_jump = ((mem.get('chapter') or 0) == 10
                      and (mem.get('y_jumped') or {}).get('RECALL') != goal)
    if not ch10_need_jump and _map._target_roof_under_marker(screen, mem, frame, pseudo):
        _reset_boss_jump(mem)
        return _request_recall(mem, state, goal, [pad('a')], target_cell=list(marker), boss=True)
    if (mem.get('y_jumps') or {}).get('RECALL:target', 0) < BOSS_JUMP_LIMIT:
        return _start_y_jump(mem, {'step': 'RECALL'}, goal, 'target')
    return _abort_boss_redirect(mem, state, 'boss_jump_failed',
                                'ボス城へマーカーを合わせられないため再移動を取り消す')


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


def _observe_fort_frame(screen, state):
    """Keep bounded evidence for an unread 砦 route (#5252).

    Each stage also has a short non-progress bound; a stale fort panel must
    release the foreground before it consumes the full camp-recall budget.
    """
    stage = state.get('stage')
    if stage not in FORT_STAGES:
        return
    key = f'{stage}_observations'
    state[key] = min(RECALL_LIMIT + 1, int(state.get(key) or 0) + 1)
    kind = getattr(screen, 'kind', None)
    state['fort_last_screen_kind'] = kind[:32] if isinstance(kind, str) else 'unknown'
    text = getattr(screen, 'text', None)
    excerpt = ' '.join(text.split())[:120] if isinstance(text, str) else ''
    if excerpt:
        state['fort_last_screen_text'] = excerpt
    else:
        state.pop('fort_last_screen_text', None)
    if stage == 'fort_status':
        from . import house
        info = house.general_status(screen)
        observed = info.get('general') if isinstance(info, dict) else None
        if isinstance(observed, str) and observed:
            state['fort_last_observed_general'] = observed[:48]
        else:
            state.pop('fort_last_observed_general', None)


def _abort_camp_recall(mem, state, screen, reason):
    """Record bounded evidence and release a recall that cannot progress."""
    stage = state.get('stage')
    metric = {'stage': stage, 'steps': int(state.get('steps') or 0)}
    if stage in FORT_STAGES:
        metric['fort'] = True
        camp = state.get('target')
        if (isinstance(camp, (list, tuple)) and len(camp) == 2
                and all(type(value) is int for value in camp)):
            metric['camp'] = list(camp)
        target_cell = state.get('target_cell')
        if (isinstance(target_cell, (list, tuple)) and len(target_cell) == 2
                and all(type(value) is int for value in target_cell)):
            metric['target_cell'] = list(target_cell)
        castle = state.get('goal')
        if isinstance(castle, str):
            metric['castle'] = castle[:48]
        metric.update({
            'general': state.get('general'),
            'fort_status_reads': int(state.get('fort_status_reads') or 0),
            'fort_status_observations': int(state.get('fort_status_observations') or 0),
            'fort_list_observations': int(state.get('fort_list_observations') or 0),
            'fort_menu_observations': int(state.get('fort_menu_observations') or 0),
            'screen_kind': state.get('fort_last_screen_kind'),
            'screen_text': state.get('fort_last_screen_text'),
            'observed_general': state.get('fort_last_observed_general'),
            'input_sent': state.get('input_sent') is True,
            'input_receipt_confirmed': state.get('input_receipt_confirmed') is True,
            'picker_closed': state.get('picker_closed') is True,
            'interrupted': _flow(mem).get('recall_interrupted') is not None,
            'escape_move': state.get('escape_move') is True,
        })
    _record(mem, 'camp_recall_aborted', observed_metric=metric, reason=reason)
    _flow(mem).pop('recall', None)
    mem['uncertain'] = True
    _arm_recall_skip(mem, state)
    if (is_camp_menu(screen) or is_fort_menu(screen)
            or screen.kind in ('map_target', 'world_map')
            or str(stage).startswith('fort_')):
        return [pad('b')]
    return []


def enter_fort(mem, state):
    """Start the 砦's own route: its generals leave by しゅつげき, not by きかん."""
    state.update(stage='fort_menu', steps=0, camp_kind='dome')
    for stage in FORT_STAGES:
        state.pop(f'{stage}_observations', None)
    state.pop('fort_status_reads', None)
    state.pop('fort_picker_requested', None)
    state.pop('fort_picker_requested_tick', None)
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
    # A fort sortie takes longer than the remaining month-end margin. Do not
    # begin a new sortie (or send the status-panel A) when the concert scene
    # is close enough to cover its destination picker. A picker already
    # requested remains owned by its existing bounded wait.
    if _month_end_soon(mem) and not state.get('fort_picker_requested'):
        ready_to_defer = False
        if stage in ('fort_menu', 'fort_list'):
            ready_to_defer = is_fort_menu(screen) or fort_list_open(screen)
        elif stage == 'fort_status':
            info = house.general_status(screen)
            ready_to_defer = bool(info and info.get('general') == state.get('general'))
        if ready_to_defer:
            return _leave_fort(
                mem, state,
                '月末場面までの猶予が短いため、砦しゅつげきを次月へ延期',
                [pad('b')])
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
                return fort_list_batch(screen, wanted) or [move]
        current = _current(screen)
        if not current or current[2] not in members:
            return []
        # The fort holds these generals together, so any of their departures
        # locates it when the chosen one has no sortie record of his own.
        state['fort_members'] = list(members)
        state.update(stage='fort_status', steps=0, general=current[2])
        state.pop('fort_status_observations', None)
        state['fort_status_reads'] = 0
        state.pop('fort_picker_requested', None)
        state.pop('fort_picker_requested_tick', None)
        _record(mem, 'camp_fort_sortie', chart_step=None, general=current[2],
                observed_metric={'camp': state.get('target'), 'members': members},
                reason='砦の中の将軍を一人選び、しゅつげきで補給できる自軍城へ帰す')
        return [pad('a')]
    if screen.kind == 'map_target':
        if state.get('fort_picker_requested') is not True:
            return None
        state.update(stage='dest', steps=0, fort=True, picker_from_menu=True)
        state.pop('fort_picker_requested', None)
        state.pop('fort_picker_requested_tick', None)
        return None
    if screen.kind == 'map':
        return _leave_fort(mem, state, '砦のしゅつげきが閉じてマップへ戻ったため見送る', [])
    info = house.general_status(screen)
    # The status panel must belong to the general selected in the fort list.
    # A stale/adjacent panel is not permission to confirm the sortie picker.
    if info and info.get('general') == state.get('general'):
        reads = state['fort_status_reads'] = int(state.get('fort_status_reads') or 0) + 1
        if state.get('fort_picker_requested') is True:
            return []
        if reads % FORT_STATUS_PRESS_EVERY == 1:
            state['fort_picker_requested'] = True
            state['fort_picker_requested_tick'] = int(mem.get('tick') or 0)
            return [pad('a')]
        return []
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


def _camp_world_cell(cursor, origin, target):
    """The tent's stable world cell from the camera-bound origin.

    ``origin`` is the cursor's world cell (``observed_cursor_cell``); the tent
    is ``target - cursor`` away from it in screen cells, so the world cell
    survives a later camera shift. The owner rule against trusting a stale
    *screen* coordinate stands: this is a fresh world anchor, not a replay.
    """
    if not (isinstance(cursor, (tuple, list)) and isinstance(origin, (tuple, list))
            and isinstance(target, (tuple, list))):
        return None
    return [origin[0] + target[0] - cursor[0], origin[1] + target[1] - cursor[1]]


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
    state.pop('scene_covered', None)
    state.pop('scene_covered_tick', None)
    state.pop('scene_pending', None)
    # docich#5665: a fresh request restarts the send-proof window; the stale
    # demotion bookkeeping of a superseded wait must not bound it.
    state.pop('stale_since_tick', None)
    state.pop('stale_steps', None)
    state.pop('resends', None)
    escape_move = fields.get('escape_move') is True
    if goal and not escape_move:
        # The destination's own list is the arrival receipt for a recalled
        # camp (the field shows no marker for it). Ask the corridor probe to
        # read that castle so the recalled generals become visible instead of
        # staying "unaccounted": g1163 2026-11-13 15:5x-16:3x recalled camps
        # four times and never read where they landed. The due time is the
        # recall's walking time: reading at the request tick finds an empty
        # castle.
        mem.setdefault('roster_read_due', {})[goal] = (
            int(mem.get('tick') or 0) + RECALL_READ_DELAY)
    if escape_move:
        decision = 'camp_escape_move_requested'
        resulting_event = 'field_move_requested'
        reason = '崖離脱のフィールド移動入力を予定し、送信・画面遷移は未確認として保持'
    else:
        decision = 'camp_recall_requested'
        resulting_event = 'planned_not_yet_sent'
        reason = '実選択した自軍城への帰還入力を予定し、送信と到着は未確認として保持'
    _record(mem, decision, chart_step=None,
            observed_metric={'castle': goal, 'camp': state.get('target'),
                             'hero_intended': bool(state.get('hero')),
                             'escape_move': escape_move, **fields},
            resulting_event=resulting_event, reason=reason)
    return actions


def _dispatch_unsent_proven(state, receipt, trace):
    """The sender journaled other inputs after this request but not our A.

    ``later_inputs`` in the receipt means the sender-side journal (the
    ``hanjuku_inputs_sent`` mirror, falling back to the events tail) recorded
    delivered inputs at/after this request's ``planned_at``. With the matching
    press still absent, that is positive proof the batch never left — distinct
    from an unreadable journal (``{}``, unknown) or a still-pending proof.
    """
    return bool(trace and receipt.get('request_trace') == trace
                and receipt.get('later_inputs')
                and receipt.get('a_inputs', 0) < int(state.get('a_inputs') or 1))


def _resend_picker_identity_confirmed(state, screen):
    if screen.kind != 'map_target':
        return False
    target = state.get('target_cell')
    marker = getattr(screen, 'marker', None)
    if not (isinstance(target, (list, tuple)) and len(target) == 2
            and isinstance(marker, (list, tuple)) and len(marker) == 2):
        return False
    values = tuple(target) + tuple(marker)
    if any(type(value) not in (int, float) for value in values):
        return False
    return (abs(target[0] - marker[0]) <= RECALL_CONFIRM_PX
            and abs(target[1] - marker[1]) <= RECALL_CONFIRM_PX)


def _recall_dispatch_step(screen, mem, state):
    # Once a scene covered the sent A, only field/picker observations can
    # advance the bounded check. Text and transition frames belong to their
    # normal owner and cannot prove that the destination picker closed.
    if state.get('scene_covered') and screen.kind not in ('map', 'map_target', 'world_map'):
        return None
    stale = state.get('stage') == 'await_dispatch_stale'
    state['last_dispatch_screen_kind'] = screen.kind
    state['steps'] = int(state.get('steps') or 0) + 1
    if stale:
        state['stale_steps'] = int(state.get('stale_steps') or 0) + 1
    # docich#5452: keep the screens this wait actually saw. A terminal that
    # ends on a menu the recall's own inputs opened by mistake (a view-mode
    # world map's A opens castle_menu) is mode-loss evidence, not a
    # (camp, castle) refusal.
    recent = state.get('dispatch_screens')
    if not isinstance(recent, list):
        recent = []
    recent.append(screen.kind)
    state['dispatch_screens'] = recent[-6:]
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
        if stale:
            # The receipt reached the sender journal after the bounded window
            # had already demoted the wait: the send is proven, so the normal
            # acceptance check resumes on a fresh observation count.
            state.update(stage='await_dispatch', steps=0)
            _record(mem, 'camp_recall_dispatch_resumed', chart_step=None,
                    observed_metric={'castle': state.get('goal'),
                                     'stale_steps': state.get('stale_steps')},
                    resulting_event='dispatch_receipt_confirmed',
                    reason='stale待機中に帰還Aの送信receiptを確認したため受理の確認を新しい観測窓でやり直す')
    # A scene can be observed before its input receipt is projected into the
    # policy state. If the matching receipt arrives later, promote that earlier
    # interruption to scene coverage rather than charging the destination.
    interrupted_tick = _flow(mem).get('recall_interrupted')
    requested_tick = state.get('requested_tick')
    if (state.get('input_sent') and type(interrupted_tick) is int
            and type(requested_tick) is int and interrupted_tick >= requested_tick):
        state['scene_covered'] = True
        state.setdefault('scene_covered_tick', interrupted_tick)
    if (state.get('input_sent') and screen.kind == 'map'
            and not state.get('scene_covered')):
        state['picker_closed'] = True
    # docich#5665: exhausting the six-observation proof window without a send
    # receipt is not yet a defect — the window counts only screens this wait
    # owned, so a foreign sortie or journal eviction can starve it while the
    # press was real, or the press may never have left. Demote to
    # 'await_dispatch_stale' instead of filing a permanent unconfirmed hold:
    # the stale wait keeps the same owned screens, retries the proof there,
    # and is itself bounded. Cliff escapes keep the old terminal — their A is
    # a field move, not a picker confirm, so resending it is unsafe.
    stale_done = False
    unsent_proven = False
    if not state.get('input_sent') and not state.get('picker_closed'):
        unsent_proven = _dispatch_unsent_proven(state, receipt, trace)
        if not stale:
            if (state['steps'] >= 6 and state.get('escape_move') is not True):
                state['stage'] = 'await_dispatch_stale'
                state.setdefault('stale_since_tick', int(mem.get('tick') or 0))
                _record(mem, 'camp_recall_dispatch_stale', chart_step=None,
                        observed_metric={'castle': state.get('goal'),
                                         'camp': state.get('target'),
                                         'steps': state['steps'],
                                         'recent_screens': list(state.get('dispatch_screens') or ())},
                        resulting_event='dispatch_proof_window_exhausted',
                        reason='帰還Aの送信証明が6観測で得られなかったためstaleへ降格し、所有画面が戻り次第receipt照合と未送信証明を再試行する')
                return []
        else:
            resends = int(state.get('resends') or 0)
            resend_rejected = False
            if (unsent_proven and resends < DISPATCH_RESEND_MAX
                    and screen.kind in ('map_target', 'world_map')):
                # The sender journaled other inputs after this request while
                # our A is absent, so the batch provably never left. The
                # destination picker is still on screen: send the confirm
                # once under a fresh request trace (persist re-stamps it for
                # this observation's batch) instead of staying unconfirmed.
                scene_interrupted = ((type(interrupted_tick) is int
                                      and (type(requested_tick) is not int
                                           or interrupted_tick >= requested_tick))
                                     or bool(state.get('scene_covered'))
                                     or bool(state.get('scene_pending')))
                if scene_interrupted and not _resend_picker_identity_confirmed(state, screen):
                    resend_rejected = True
                    marker = getattr(screen, 'marker', None)
                    _record(mem, 'camp_recall_dispatch_resend_rejected', chart_step=None,
                            observed_metric={'castle': state.get('goal'),
                                             'camp': state.get('target'),
                                             'target_cell': state.get('target_cell'),
                                             'marker': list(marker) if marker else None,
                                             'screen': screen.kind},
                            resulting_event='picker_identity_unconfirmed',
                            reason='場面後のピッカーが元の帰還先選択と同一と確認できないため、別のピッカーへAを送らず未確認終端へ進める')
                else:
                    state.update(stage='await_dispatch', steps=0, a_inputs=1,
                                 requested_tick=int(mem.get('tick') or 0),
                                 resends=resends + 1)
                    for key in ('request_trace', 'input_sent', 'picker_closed',
                                'scene_covered', 'scene_covered_tick', 'scene_pending'):
                        state.pop(key, None)
                    _flow(mem).pop('recall_interrupted', None)
                    _record(mem, 'camp_recall_dispatch_resent', chart_step=None,
                            observed_metric={'castle': state.get('goal'),
                                             'camp': state.get('target'),
                                             'resends': state['resends']},
                            resulting_event='dispatch_unsent_proven_resent',
                            reason='帰還Aの送信記録がなく後続入力の記録があるため未送信と証明し、開いたピッカーへAを一度だけ補送する')
                    return [pad('a')]
            stale_since = state.get('stale_since_tick')
            now = mem.get('tick')
            stale_done = (
                resend_rejected
                or int(state.get('stale_steps') or 0) >= DISPATCH_STALE_STEP_LIMIT
                or (unsent_proven and screen.kind == 'map')
                or (type(stale_since) is int and type(now) is int
                    and now - stale_since >= DISPATCH_STALE_TICK_LIMIT))
            if not stale_done:
                return []
    if state.get('picker_closed') or (not stale and state['steps'] >= 6) or stale_done:
        interrupted_at = _flow(mem).pop('recall_interrupted', None)
        interrupted = interrupted_at is not None
        # docich#4978: the destination picker dropping back to the field map is
        # the game accepting the return (the code below already says so). When
        # that acceptance is the *same* observation that saw our A, the old
        # terminal still called it 未確認 and filed a camp_recall_unconfirmed
        # incident per recall (g1190 2026-11-21 16:59:45: input_sent and
        # unconfirmed in one second) while also forcing a map re-anchor
        # (mem['uncertain']) that the accepted dispatch never needed.
        accepted = (bool(state.get('input_sent')) and bool(state.get('picker_closed'))
                    and not interrupted and not state.get('scene_covered'))
        status = ('dispatched' if accepted
                  else 'arrival_unconfirmed' if state.get('input_sent')
                  else 'dispatch_unconfirmed')
        goal = state.get('goal')
        escape_move = state.get('escape_move') is True
        # docich#5452: screens like castle_menu during this wait mean the
        # recall's own inputs left the picker mode — a mode loss, so it gets
        # the same short recovery hold an interruption does, and its terminal
        # must not poison the (camp, castle) ranking below.
        foreign_screens = list(state.get('dispatch_screens') or ())
        mode_lost = any(kind in ('castle_menu', 'general_list', 'castle_info',
                                 'main_menu') for kind in foreign_screens)
        # A missing input receipt is a source/transport/receipt-path uncertainty,
        # not evidence that the selected destination refused this camp. Keep
        # castle ranking unchanged until destination-specific refusal evidence
        # exists (docich#5250).
        verification = {
            **state, 'status': status, 'interrupted': interrupted or mode_lost,
            'input_sent': bool(state.get('input_sent')),
            'picker_closed': bool(state.get('picker_closed')),
            'scene_covered': bool(state.get('scene_covered')),
            **({'interrupted_tick': interrupted_at} if type(interrupted_at) is int else {}),
        }
        # A cliff escape uses the same bounded dispatch receipt, but it must
        # not replace a real recall's arrival proof. camp_recheck consumes only
        # recall_verification for named arrival reads.
        if escape_move:
            _flow(mem)['escape_move_verification'] = {
                'status': status, 'interrupted': interrupted or mode_lost,
                **({'interrupted_tick': interrupted_at} if type(interrupted_at) is int else {}),
                'escape_move': True, 'target': state.get('target'),
                'target_cell': state.get('target_cell'),
                'requested_tick': state.get('requested_tick'),
                'steps': state.get('steps'),
                'input_sent': bool(state.get('input_sent')),
                'picker_closed': bool(state.get('picker_closed')),
                'scene_covered': bool(state.get('scene_covered')),
                'scene_covered_tick': state.get('scene_covered_tick'),
            }
        else:
            _flow(mem)['recall_verification'] = verification
        _flow(mem).pop('recall', None)
        _arm_recall_skip(mem, state,
                         BOSS_RETRY_TICKS if state.get('boss')
                         else FORT_RETRY_TICKS if (state.get('fort') or interrupted
                                                   or state.get('scene_covered')
                                                   or mode_lost) else None)
        camp_target = state.get('target')
        if isinstance(camp_target, tuple):
            camp_target = list(camp_target)
        target_cell = state.get('target_cell')
        if isinstance(target_cell, tuple):
            target_cell = list(target_cell)
        terminal_metric = {
            'castle': goal,
            'camp': camp_target,
            'target_cell': target_cell,
            'stage': state.get('stage'),
            'steps': state.get('steps'),
            'status': status,
            'escape_move': escape_move,
            'input_receipt_confirmed': bool(state.get('input_sent')),
            'input_sent': bool(state.get('input_sent')),
            'unsent_proven': unsent_proven,
            'stale_steps': state.get('stale_steps'),
            'resends': int(state.get('resends') or 0),
            'picker_closed': bool(state.get('picker_closed')),
            'interrupted': interrupted,
            'scene_covered': bool(state.get('scene_covered')),
            'scene_covered_tick': state.get('scene_covered_tick'),
            'last_screen_kind': state.get('last_dispatch_screen_kind'),
            'mode_lost': mode_lost,
            'recent_screens': foreign_screens,
            'general': state.get('general') if state.get('fort') else None,
        }
        if accepted:
            if escape_move:
                # The map return after this A accepts the field move away from
                # the cliff; it is not a camp-return dispatch or arrival proof.
                _record(mem, 'camp_escape_move_dispatched', chart_step=None,
                        observed_metric=terminal_metric,
                        resulting_event='field_move_accepted',
                        reason='崖離脱のフィールド移動が受理されたため、次の観測で野営位置を再確認する')
            else:
                # The dispatch was accepted; only its arrival is still unknown, and
                # camp_recheck's named roster read owns that (arrival_request).
                _record(mem, 'camp_recall_dispatched', chart_step=None,
                        observed_metric={'castle': state.get('goal'),
                                         'camp': state.get('target'),
                                         'general': state.get('general') if state.get('fort') else None},
                        resulting_event='accepted_awaiting_arrival',
                        reason='帰還ピッカーが閉じて受理されたため、到着は名簿の実読みで確認する')
                _remember_camp_verify(mem, state)
        else:
            mem['uncertain'] = True
            if escape_move:
                _record(mem, 'camp_escape_move_unconfirmed', chart_step=None,
                        observed_metric=terminal_metric,
                        resulting_event='field_move_unconfirmed',
                        reason='崖離脱のフィールド移動の送信・画面遷移を確認できず、次回観測へ未確認として保持')
            else:
                reason = ('帰還Aの送信receiptを確認できず、送信段階で未確認として保持'
                          if status == 'dispatch_unconfirmed' else
                          '帰還A送信後の受理・到着を確認できず、未確認として保持')
                _record(mem, 'camp_recall_unconfirmed', chart_step=None,
                        observed_metric=terminal_metric, resulting_event=status,
                        reason=reason)
        if (not escape_move and status == 'arrival_unconfirmed'
                and isinstance(goal, str) and state.get('input_sent')):
            # docich#5127 (owner 2026-11-23): an input that was sent but whose
            # picker did not close inside the window may still be walking. Give
            # it the same bounded destination-roster read an accepted dispatch
            # gets, so arrival can be confirmed instead of ending "unconfirmed"
            # and leaving the general unknown.
            mem.setdefault('roster_read_due', {})[goal] = (
                int(mem.get('tick') or 0) + RECALL_READ_DELAY)
        if (mode_lost and not escape_move and status == 'arrival_unconfirmed'
                and not state.get('picker_closed')):
            # docich#5452: the terminal screens are menus the recall's own
            # inputs opened after the いどう mode was lost (a view-mode world
            # map's A opens castle_menu). Nothing here says the (camp,
            # castle) pair refused the dispatch, so it must not be counted as
            # a refusal — the recall retries this camp and castle.
            _record(mem, 'camp_recall_mode_lost', chart_step=None,
                    observed_metric={'camp': camp_target, 'goal': goal,
                                     'recent_screens': foreign_screens},
                    resulting_event='move_mode_lost',
                    reason='帰還確定の入力が城メニュー系画面へ入った未受理終端のため、目的地拒否としては計上しない')
        elif (not escape_move and status == 'arrival_unconfirmed'
                and not state.get('picker_closed') and not state.get('scene_covered')):
            # The same camp was sent at the same castle month after month
            # (g1190 2026-11-17 21:1x: リーキ -> アルマムーン three times) and the
            # general never moved. Remember the pair so the next attempt picks
            # a different castle instead of repeating a silent failure.
            # picker_closed means the destination picker already dropped back
            # to the field map — the game accepted the dispatch and the
            # general is walking home in game time, which the 6-step window
            # cannot verify. Counting those as failures poisoned every
            # (camp, castle) pair until _recall_exhausted abandoned the camp
            # (docich#4755 follow-up: generals stuck in field camps).
            camp = state.get('target')
            if (isinstance(camp, (list, tuple)) and len(camp) == 2
                    and isinstance(goal, str) and goal):
                failures = _flow(mem).setdefault('recall_goal_failures', {})
                key = f'{int(camp[0])},{int(camp[1])}'
                pair = failures.get(key) if isinstance(failures.get(key), dict) else {}
                pair[goal] = int(pair.get(goal) or 0) + 1
                failures[key] = pair
                _record(mem, 'camp_recall_goal_failed', observed_metric={
                    'camp': list(camp), 'goal': goal, 'count': pair[goal]},
                    reason='同じ野営から同じ城への帰還が未確認のまま繰り返されるため、次回は別の城を試す')
        return [pad('b')] if screen.kind in ('world_map', 'map_target', 'castle_menu',
                                             'general_list', 'castle_info', 'main_menu') else []
    return []
