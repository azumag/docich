"""Orchestrator: which layer owns this observation, and the no-input fallback.

``decide`` classifies the frame (``os_layer.phase``), parses it, lets the
foreground owners (resume, trade guard, monthly/house/recall transactions)
claim it in priority order and otherwise dispatches on the screen kind to a
``flows`` procedure. It never writes or sends: the caller persists the
returned records and sends the bounded pad actions.
"""
from __future__ import annotations

from .os_layer.pixels import Frame
from . import decision_log
from .flows import (battle as battle_flow, battle_menus as battle_menus_flow,
                    deploy as deploy_flow, egg_recovery as egg_recovery_flow, map as map_flow,
                    month as month_flow, name as name_flow, prompts as prompts_flow,
                    recall as recall_flow, shop as shop_flow)
from .judgment import battle as battle_judgment
from .strategy import campaign, edition, goals
from .os_layer import readers
from .os_layer.navigation import observe_jump_foreground
from .os_layer.phase import classify, shop_exit_confirmation
from .version import BOT_VERSION


# Owner directive (2026-10-03): 保留 is not an end state. When one screen stays
# frozen and the bot has planned no input for more than this many observations,
# decide() sends the phase's default input instead of waiting forever.
#
# The bound sits above every finite wait the policy already resolves itself
# (RECALL_LIMIT=90, EGG_RITUAL_LIMIT=64, RARE_SCAN_LIMIT=32,
# RECRUIT_CANDIDATE_LIMIT=32, MONSTER_MENU_HOLD_LIMIT=30,
# house.STEP_LIMIT=24), so no bounded step is cut short. At the normal 1.5 s
# observation interval 100 observations is 150 s, well inside the 300 s
# screen-stall terminal (hanjuku_run.STALL_SECONDS) that ends the run while
# nothing is being pressed.
NO_INPUT_HOLD_MAX = 100
# A battle command menu owns the screen: it is a decision point, not a scene.
# The g1190 chapter-9 attacks froze on one for 101 observations (minutes) before
# the generic fallback pressed one key. Bound the battle menu's no-input wait
# sooner; the orphan-menu hold and every bounded battle flow stay excluded by
# the same gate.
BATTLE_COMMAND_MENU_HOLD_MAX = 30
# A message box over the field that no flow claims is acknowledged sooner:
# the fallback presses A there anyway (g1067 11:13: a castle-progress notice).
FIELD_TEXT_HOLD_MAX = 4

# The title screen's つづき (battery-save resume) gets this many tries before a
# blind Start; a new game from the title must never happen while a save exists.
TITLE_CONTINUE_TRIES = 3

def _chapter9_field_recall(mem):
    """docich#3958: let chapter 9's mechanical march see field camps again.

    The monthly pass is off in mechanical mode, so the only live mechanism
    for a general left camping on the field is the map-observed camp recall
    in flows/recall.py. It is not chapter-gated itself: before the boss phase
    ``camp_stays`` is False, so a visible camp is recalled home as in other
    chapters; in the boss phase the same call already re-sends the camp at
    the moving boss. This predicate only decides whether the orchestrator
    hands an idle map frame to that existing flow.
    """
    from .strategy import corridor
    if corridor.mechanical(mem):
        return True
    # Owner 2026-11-09: a camped general is never recalled home in the moving
    # boss's phase. The 2026-11-15 gather experiment (the field camp いどう to
    # イデ！) only looped on this runtime: the camp menu recall never dispatched
    # (the blocked table poisoned every castle, the whole-island picker could
    # not confirm), and each month re-found the same tent. The monthly pass now
    # owns the gather through the castle-to-castle forward moves, and the army
    # is back in the castles; keep the field camps where they are.
    return False


# Owner evidence #2076 (g713 UID16229): between a complete battle panel and the
# command box the game draws the menu over several frames. The panel reads
# 'unknown' while the coarse phase can still be 'event', so the generic
# scene-confirm A fired on every one of those frames (UID16229 sent the default
# A on a mid-draw panel). A press from that window can be accepted as soon as
# the box starts taking input and confirm the top row (たまごをつかう) the bot
# never chose, before any cursor evidence exists. Hold this draw window a
# bounded number of observations instead: a box that finishes drawing reads as
# battle_menu and its own owner takes over, and a frame that stays unreadable
# still falls through to the phase default after the hold.
BATTLE_DRAW_HOLD_MAX = 3
# Kinds that prove the previous observation was inside an actual battle
# foreground. A 'text' conversation or an unreadable frame never qualifies.
BATTLE_OWNED_KINDS = frozenset({'battle', 'battle_menu', 'battle_menu_pending',
                                'okunote_menu', 'egg_battle_menu', 'egg_choice_menu',
                                'monster_menu', 'monster_menu_pending'})
# The concert's optional music picker is closed with B, but a stale gold-
# cursor match can keep the picker test true on a screen B never changes. On
# 2026-10-10 17:45 UTC exactly that sent B 128 times until the 300 s
# screen-stalled terminal ended the corner (#1996). After this many unchanged
# observations with the same concert input, press A instead so the policy
# itself leaves the screen rather than waiting for the stall watchdog.
CONCERT_PICKER_REPEAT_MAX = 8
# A scripted event dialogue (phase 'concert'/'event'/'dialogue', kind 'text')
# can sit on the same narration line while another owner (status scan, map
# registration, house flow) holds the frame and returns []: the ordinary A
# handler then never runs, so the line advances only every 10-15 s or not at
# all (g1139 2026-11-06: 12:06/12:48 ~30 min stalls, 13:35 multi-second line
# gaps). Count unchanged narration observations in policy memory (persisted
# every decision) and press A, the dialogue advance key, promptly.
EVENT_TEXT_ADVANCE_PHASES = frozenset({'concert', 'event', 'dialogue'})
EVENT_TEXT_ADVANCE_MAX = 3

# Menu and banner panels drawn over the map: their identity for the no-input
# hold is the text and cursor the panel itself reads, not the pixels behind
# it. Water and other map animations give these kinds a fresh frame digest on
# every observation, so a digest-keyed streak never passed 2 and the corner
# ended input_stalled while the same menu sat open (#3599: a castle_menu held
# ~100 s with zero input). Kinds outside this set keep the digest key: on a
# bare map, a battle or an unreadable frame the animation *is* the content
# and must not be counted as a frozen screen. Every listed kind carries
# readable menu/banner text; an empty read falls back to the digest anyway.
MENU_HOLD_KINDS = frozenset({
    'month_menu', 'main_menu', 'castle_menu', 'castle_info', 'general_list',
    'card_select', 'sortie_confirm', 'discharge_menu', 'shop_exit_confirm',
    'shop_quantity_prompt', 'shop_quantity', 'shop_list', 'gift_request',
    'summer_bonus', 'yes_no', 'name_entry',
    'sealed_castle', 'boss_attack_started', 'field_encounter_started',
    'attack_started', 'defense_started', 'barrier_removed',
})


def _same_event_line(watch, key):
    """Whether a changed narration key is still the same line.

    Dialogue text is typed out character-by-character, so each observation
    extends the previous text and a strict key equality restarts the wait on
    every typed frame: g1270 48-7 logged situation_held alternating
    concert/event with a fresh digest on every observation while the month
    dialogue typed. A prefix extension is the typewriter reveal of the same
    line, not a new line.
    """
    prev = watch.get('key') if isinstance(watch, dict) else None
    if prev == key:
        return True
    if not (isinstance(prev, str) and isinstance(key, str)
            and prev.startswith('text:') and key.startswith('text:')):
        return False
    old, new = prev[5:], key[5:]
    return len(new) > len(old) and new.startswith(old)


def event_text_advance(mem, phase, kind, key, actions, guarded=False):
    """True when an unchanged scripted event narration line should advance.

    The counter lives in the policy memory, which the brain persists on every
    decision, so it survives the fresh per-observation subprocess. Any action,
    a phase/kind change, or a changed line resets it. A line still being
    typed out keeps the count and overrides the no-action requirement: the
    new text only extends the previous one.
    """
    watch = mem.get('event_text_watch') or {}
    raw = watch.get('count') if isinstance(watch, dict) else None
    # The dialogue typewriter extends the line every frame; another rule's
    # generic confirm does not make it a different screen (g1270 48-7: the
    # alternating concert/event holds each planned a pad, so the count never
    # even started). Keep the last text key so a line still typing counts
    # while a planned action exists.
    last = mem.get('event_text_last')
    typing = (isinstance(last, str) and isinstance(key, str)
              and last.startswith('text:') and key.startswith('text:')
              and len(key) > len(last) and key.startswith(last))
    if kind == 'text' and isinstance(key, str) and key.startswith('text:'):
        mem['event_text_last'] = key
    else:
        mem.pop('event_text_last', None)
    if guarded:
        count = 0
    elif phase in EVENT_TEXT_ADVANCE_PHASES and kind == 'text' and (
            not actions or typing):
        count = (raw + 1 if _same_event_line(watch, key)
                 and type(raw) is int and raw >= 0 else 1)
    else:
        count = 0
    advance = count >= EVENT_TEXT_ADVANCE_MAX
    if advance:
        count = 0
    if count:
        mem['event_text_watch'] = {'key': key, 'count': count}
    else:
        mem.pop('event_text_watch', None)
    return advance


PHASE_A_ESCAPE_MAX = 10   # identical readable screens holding the default A


def phase_a_escape_step(mem, phase, kind, text, actions, *, step=None):
    """Break a phase-default A that the same readable screen keeps ignoring.

    g1190 2026-11-18 10:40 (#4543): the ちくじょう castle list
    「どのしろを そうたく なさいますか?」/「これいじょうの ぎょうらくは…」 sat
    in phase dialogue while the default A was sent on every observation; the
    frame never changed and the run ended screen_stalled. One B closes that
    list (probe on the saved state). Count identical (phase, kind, text)
    observations that planned the default A; after the bound send B once and
    restart the count. A changed line, another phase/kind, or any other
    planned action resets it, so typed dialogue and real flows never match.
    """
    confirm = (isinstance(actions, list) and len(actions) == 1
               and isinstance(actions[0], dict) and actions[0].get('type') == 'pad'
               and actions[0].get('buttons') == ['a'])
    if (not confirm or kind != 'text' or mem.get('battle')
            or phase not in ('dialogue', 'month_menu', 'event')):
        mem.pop('phase_a_escape', None)
        return None
    key = f'{phase}:{kind}:{text or ""}'
    watch = mem.get('phase_a_escape') or {}
    n = (int(watch.get('n') or 0) + 1
         if isinstance(watch, dict) and watch.get('key') == key
         and (step is None or watch.get('step') == step - 1) else 1)
    if n >= PHASE_A_ESCAPE_MAX:
        mem['phase_a_escape'] = {'key': key, 'n': 0, 'step': step}
        decision_log._record(mem, 'phase_a_escape', screen=kind, phase=phase,
                             observed_metric={'identical_observations': n,
                                              'limit': PHASE_A_ESCAPE_MAX,
                                              'text': (text or '')[:40]},
                             reason='同じ文面に既定のAを送り続けても進まないため、Bを1回だけ送って閉じる')
        return [pad('b')]
    mem['phase_a_escape'] = {'key': key, 'n': n, 'step': step}
    return None


def pad(button, ms=100):
    return {'type':'pad','buttons':[button],'hold_ms':ms}


def _battle_draw_scope(mem):
    """Identity of the live battle whose panel/menu transition is unreadable."""
    cur = mem.get('battle') or {}
    return [mem.get('chapter'), cur.get('ally'), cur.get('enemy'),
            cur.get('start_ally_hp'), cur.get('start_enemy_hp')]


# A Y jump still open while the frame reads 'unknown' (the fade into the view,
# or a view misread): the scene-confirm A there decides the jump wherever the
# cursor happens to be. g1075 2026-10-29: 5 of 26 jumps in 2 h were confirmed
# that way (4 on the first frame after Y, one mid-move), the field cursor
# landed on the sea and the order walked for minutes before cancelling the
# sortie. Hold the A a bounded number of observations; after that the jump is
# dropped and the ordinary default applies again.
Y_JUMP_UNKNOWN_HOLD_MAX = 6
# The fade into the view and a misread mid-move come straight after the field
# map or the view itself; an unreadable run that starts there is one episode.
# A battle or scene that interrupts the view is not the view: g1075 2026-10-29
# 13:12:29-13:13:08, an arrival battle opened mid-jump and holds 2-4 landed on
# its draws, using up the bound before the view came back at 13:13:09.
Y_JUMP_VIEW_KINDS = frozenset({'map', 'map_target', 'world_map'})


CAMP_RECALL_SCENE_RESUMES = 2   # scenes may cut a recall twice before the camp is skipped
UNKNOWN_HOLD_ADVANCE = 6      # identical unreadable frames before one A is sent
UNKNOWN_BATTLE_ADVANCE = 10   # any unreadable frames inside a battle before one A
# g1253 2026-11-16: an unclassified concert/dance screen sat on `unknown` and
# took 23 A presses in one generation. One unchanged-digest episode gets at most
# this many presses; after that stop pressing and leave the frozen screen to the
# existing screen_stalled watch. A new digest starts a fresh episode.
UNKNOWN_HOLD_BURST_MAX = 3


# A sortie in flight keeps the field for this many ticks before the camp
# recall may re-open its own picker.
RECALL_YIELD_TO_SORTIE = 300


def _sortie_owns_the_field(mem) -> bool:
    """A march/departure is being steered right now.

    g1190 2026-11-17 20:27: `y_jump_open/confirm` (the sortie flow) and
    `camp_recall_picker_step` alternated every observation, so the ring was
    steered at two different castles and looked aimless on screen. One owner
    at a time: the sortie is the committed action, the recall waits.
    """
    from .state.regions import flow as _flow
    attempt = _flow(mem).get('sortie_attempt')
    if not isinstance(attempt, dict):
        return False
    tick = attempt.get('confirmation_tick')
    if type(tick) is not int:
        tick = attempt.get('tick')
    now = mem.get('tick')
    return type(tick) is int and type(now) is int and 0 <= now - tick <= RECALL_YIELD_TO_SORTIE


def _recall_dialog_wanted(recall, screen, phase, mem) -> bool:
    """Whether the in-flight camp recall owns this screen.

    The month routine owns the menu screens, not the field picker: a recall
    that already opened the destination must not idle for the routine's whole
    length (g1163 2026-11-14 02:49-02:55: the dest sat at steps 0 for five
    minutes and the month scene closed it).
    """
    if not (recall and phase != 'concert'):
        return False
    if _sortie_owns_the_field(mem):
        return False
    from .flows import camp_recheck as hanjuku_camp_recheck
    if hanjuku_camp_recheck.owns_dialog(screen, mem):
        return True
    kind = screen.kind
    return (not recall.get('retreat_recheck') and not recall.get('anonymous_recheck')
            and (not mem.get('month_sub')
                 or (recall.get('stage') == 'dest' and kind == 'map_target'))
            and (kind in ('map', 'map_target', 'text')
                 or (recall.get('stage') == 'await_dispatch' and kind in ('unknown', 'yes_no'))
                 # Between a 砦's screens the default A would pick the list's
                 # top row or confirm the marker on the 砦 itself, which
                 # cancels the sortie.
                 or (kind == 'unknown'
                     and (recall.get('fort') or recall_flow.in_fort_route(recall)))))


def _recall_after_scene(mem, recall, kind, phase):
    """Keep a scene-cut recall pending instead of starting it over.

    The scene closes the destination picker but not the intent: the camped
    general still stands where he was. Dropping the recall made every attempt
    begin from finding the camp again, and the retry landed near the next
    month's end just as often (g1163 2026-11-12 03:5x-04:1x: six attempts an
    hour, none completed). The destination is chosen again, so a picker that
    really closed cannot be confirmed by a stale marker.
    """
    from .state.regions import flow as _flow
    _flow(mem).pop('recall_interrupted', None)
    for key in ('dest', 'dest_target', 'marker', 'picker_closed', 'input_sent',
                'request_trace', 'a_inputs', 'requested_tick'):
        recall.pop(key, None)
    resumes = int(recall.get('scene_resumes') or 0) + 1
    if resumes > CAMP_RECALL_SCENE_RESUMES:
        # A recall that keeps being cut by scenes is not transient: give the
        # camp the usual failed skip instead of spending every month on it.
        _flow(mem).pop('recall', None)
        # #4468: a scene cut is an interruption, not the recall's own failure.
        # The marker is cleared at the head of every new request (so it cannot
        # shorten an unrelated recall), which left the monthly task reading
        # `interrupted=False` and spending RECALL_TASK_RETRIES on a recall whose
        # A was never sent — unlike a battle cut, which gets a free retry via
        # recall._mark_recall_interrupted. Stamp it at the abandonment point so
        # post_month_status.wait_recall requeues without charging the budget.
        _flow(mem)['recall_interrupted'] = int(mem.get('tick') or 0)
        from .flows.recall import _arm_recall_skip as _arm_skip
        _arm_skip(mem, recall)
        decision_log._record(mem, 'camp_recall_scene_abandoned', screen=kind, phase=phase,
                             observed_metric={'resumes': resumes,
                                              'stage': recall.get('stage'),
                                              'goal': recall.get('goal'),
                                              'target': recall.get('target')},
                             reason='場面の割り込みで帰還が繰り返し中断されるため、この野営を一定期間見送る')
        return None
    recall.update(stage='to_camp', steps=0, resumed_after_scene=True,
                  scene_pending=True, scene_resumes=resumes)
    _flow(mem)['recall'] = recall
    return recall


def plain_text_hold(screen) -> bool:
    """A text screen with no hand cursor has nothing to choose.

    Only an A can move such a screen on, so it gets the same bounded press an
    unreadable screen does (g1163 2026-11-12 21:3x: 101 no-input `text`
    frames in half an hour).
    """
    return getattr(screen, 'kind', None) == 'text' and getattr(screen, 'hand', None) is None


# A map with no cursor sprite cannot be steered: nav_step has no cell to read
# and map_step plans no input, so the generic no-input gate only moves the
# screen after NO_INPUT_HOLD_MAX observations (~150 s at the 1.5 s interval).
# g1153 2026-11-07 22:53-22:55 (issue #3721): the next chapter's map sat with
# no HUD, text or cursor for 174 s. SELECT recentres the camera on the hero --
# the measured recovery nav_step already uses for a lost cursor -- and, when
# no order is waiting on the map, one A advances a scene drawn over it. Each
# frozen episode gets one of each; a screen neither moves stays with the gate.
MAP_UNSTEERABLE_HOLD_MAX = 8


def map_unsteerable_nudge(screen, mem, kind, phase, streak, digest):
    """Send one bounded recovery for a frozen map that shows no cursor."""
    if phase != 'field' or kind != 'map':
        return None
    if screen.cursor or screen.marker or screen.text:
        return None
    if streak <= MAP_UNSTEERABLE_HOLD_MAX:
        return None
    from .state.regions import plan as _plan_region
    watch = mem.get('map_unsteerable')
    if not isinstance(watch, dict) or watch.get('digest') != digest:
        watch = {'digest': digest, 'steps': 0}
    steps = watch.get('steps')
    steps = steps if type(steps) is int and 0 <= steps < 2 else 2
    if steps == 1 and _plan_region(mem).get('orders'):
        # A plan still owns this map: a blind A could open a menu at the
        # invisible cell. The SELECT already re-anchored the camera.
        mem['map_unsteerable'] = {'digest': digest, 'steps': steps}
        return None
    if steps >= 2:
        mem['map_unsteerable'] = {'digest': digest, 'steps': steps}
        return None
    actions = [pad('select')] if steps == 0 else [pad('a')]
    mem['map_unsteerable'] = {'digest': digest, 'steps': steps + 1}
    if steps == 0:
        mem['nav_last'] = None
        mem['uncertain'] = True
        mem['select_focus_tick'] = int(mem.get('tick') or 0)
    decision_log._record(mem, 'map_unsteerable_nudge', screen=kind, phase=phase,
                         observed_metric={'streak': streak, 'steps': steps + 1,
                                          'digest': digest[:16]},
                         reason='カーソルの無い地図が同じままなので、SELECT/Aで画面を動かして再観測する')
    return actions


def unknown_hold_advance(screen, mem, actions, digest, phase=None):
    """Break a frozen unreadable screen with a bounded burst of A presses.

    g1133 2026-11-05 logged 35 `situation_held` in half an hour: the bot sent no
    input while the screen stayed unknown. Many game screens need one button to
    move on, so an identical frame repeated UNKNOWN_HOLD_ADVANCE times gets a
    single A. A screen that reacts never reaches the bound.

    Inside a battle the frame animates (the boss breathes, the panels flash), so
    no two frames carry the same digest and the identical-frame bound is never
    reached: g1163 2026-11-09 13:12-13:16 held on the chapter-6 boss entry for
    six minutes and the fight expired without a single input. Count consecutive
    unreadable battle frames instead and send the same bounded A.

    A blinking cursor alternates two images forever, which the run's stasis
    clock already reads as one unchanged screen (`recent_frame_sha256`). This
    counter must agree: g1163 2026-11-11 19:09-19:15 froze an unreadable
    'event' frame that flipped between exactly two digests, so every
    observation looked new, no A was ever sent, and the 5-minute stasis latched
    screen_stalled. Keep the last two distinct digests and treat a repeat in
    that pair as the same screen; a genuinely changing screen still misses it.

    g1253 2026-11-16: pressing A every five frames forever on an unclassified
    concert/dance screen is blind play. An unchanged-digest episode now gets at
    most UNKNOWN_HOLD_BURST_MAX presses; a new digest resets the budget and the
    run's screen_stalled watch remains the final backstop.
    """
    watch = mem.get('unknown_hold_watch') or {}
    recent = [d for d in (watch.get('recent') or []) if isinstance(d, str)][:2]
    same = digest in recent
    if not same:
        # A digest outside the two-image blink pair starts a new episode: the
        # A burst budget and its one-shot stop record both reset.
        watch['presses'] = 0
        watch['capped'] = False
    watch['n'] = (int(watch.get('n') or 0) + 1) if same else 1
    watch['recent'] = ([digest] + [d for d in recent if d != digest])[:2]
    watch['digest'] = digest
    watch['total'] = int(watch.get('total') or 0) + 1
    mem['unknown_hold_watch'] = watch
    in_battle = bool(mem.get('battle'))
    # After one press this screen already proved it does not clear itself:
    # waiting the full six frames again just costs frames (g1163 2026-11-12
    # 22:1x: 66 no-input `unknown` frames in ten minutes, mostly three-frame
    # episodes). The first episode is unchanged.
    limit = UNKNOWN_HOLD_ADVANCE - 1 if watch.get('pressed') else UNKNOWN_HOLD_ADVANCE
    if watch['n'] < limit and not (
            in_battle and watch['total'] >= UNKNOWN_BATTLE_ADVANCE):
        return actions
    presses = int(watch.get('presses') or 0)
    if presses >= UNKNOWN_HOLD_BURST_MAX:
        # Further A presses would be blind input on a screen that never reacts.
        # Record the stop once per episode so hold diagnostics can see it.
        if not watch.get('capped'):
            watch['capped'] = True
            decision_log._record(
                mem, 'situation_held', screen=screen.kind, phase=phase,
                observed_metric={'kind': screen.kind, 'phase': phase,
                                 'variant': mem.get('variant'),
                                 'digest': digest[:16], 'presses': presses},
                reason='読めない画面にAを複数回送っても変化しないため、これ以上の盲目的な入力を止める')
        return actions
    watch['presses'] = presses + 1
    watch['pressed'] = True
    watch['n'] = 0
    watch['total'] = 0
    decision_log._record(mem, 'unknown_hold_advance', screen=screen.kind,
                         observed_metric={'digest': digest[:16],
                                          'held': UNKNOWN_HOLD_ADVANCE,
                                          'battle': in_battle,
                                          'kind': screen.kind, 'phase': phase,
                                          'variant': mem.get('variant')},
                         reason='読めない画面が同じままなので、Aを1回だけ送って進める')
    return [pad('a')]


def y_jump_unknown_hold_step(screen, mem, previous_kind, generic):
    """Withhold the generic confirm while a Y jump is open, boundedly."""
    confirms = (isinstance(generic, list) and len(generic) == 1
                and isinstance(generic[0], dict) and generic[0].get('type') == 'pad'
                and generic[0].get('buttons') == ['a'])
    jump = mem.get('y_jump')
    if screen.kind != 'unknown' or not isinstance(jump, dict):
        return None
    tick = mem.get('tick')
    episode = (previous_kind == 'unknown' and type(tick) is int
               and jump.get('unknown_tick') == tick - 1)
    if previous_kind not in Y_JUMP_VIEW_KINDS and not episode:
        return None
    jump['unknown_tick'] = tick
    if not confirms:
        return None
    held = jump.get('unknown_holds')
    held = held if type(held) is int and held >= 0 else 0
    if held >= Y_JUMP_UNKNOWN_HOLD_MAX:
        from .os_layer.navigation import _note_failed_jump
        mem.pop('y_jump', None)
        _note_failed_jump(mem, jump, 'view_unreadable')
        decision_log._record(mem, 'y_jump_failed', chart_step=jump.get('step'), target=jump.get('goal'),
                             screen=screen.kind,
                             observed_metric={'moves': jump.get('moves'), 'unknown_holds': held},
                             reason='全体マップのジャンプ中に読めない画面が続いたため、ジャンプを中断して既定の入力に戻す')
        return None
    jump['unknown_holds'] = held + 1
    decision_log._record(mem, 'y_jump_unknown_hold', chart_step=jump.get('step'), target=jump.get('goal'),
                         screen=screen.kind,
                         observed_metric={'held': held + 1, 'limit': Y_JUMP_UNKNOWN_HOLD_MAX,
                                          'moves': jump.get('moves')},
                         reason='全体マップのジャンプ中に読めない画面で既定のAを押すと現在地で決定してしまうため保留する')
    return []


# The owner survey's Y opens the same view with no jump in flight. g1075
# 2026-10-29: 7 of 46 surveys met an unreadable first frame (the fade in) and
# the generic A closed the view unread; at 15:42:44 that hid パタゴーヌ's new
# enemy flag. Hold that A only for the first observations after the survey Y.
WORLD_SURVEY_UNKNOWN_HOLD_TICKS = 3


def world_survey_unknown_hold_step(screen, mem, generic):
    """Withhold the generic confirm on the fade right after the survey Y."""
    confirms = (isinstance(generic, list) and len(generic) == 1
                and isinstance(generic[0], dict) and generic[0].get('type') == 'pad'
                and generic[0].get('buttons') == ['a'])
    opened, tick = mem.get('world_map_tick'), mem.get('tick')
    if (screen.kind != 'unknown' or not confirms or isinstance(mem.get('y_jump'), dict)
            or type(opened) is not int or type(tick) is not int
            or not 0 < tick - opened <= WORLD_SURVEY_UNKNOWN_HOLD_TICKS):
        return None
    decision_log._record(mem, 'world_map_unknown_hold', screen=screen.kind,
                         observed_metric={'after_y': tick - opened,
                                          'limit': WORLD_SURVEY_UNKNOWN_HOLD_TICKS},
                         reason='全城確認のYの直後の読めない画面で既定のAを押すと全体マップを読まずに閉じるため保留する')
    return []


# A target jump's confirm returns to the sortie map with the marker on the
# target; the frames between are unreadable. g1075 2026-10-30 05:43:09: the
# generic A on that fade decided the target unseen, the order left the target
# picker unobserved and was recorded launched_unconfirmed (4 of 5 interim
# sorties 04:30-06:19), then re-read from its source list.
Y_JUMP_TARGET_SETTLE_TICKS = 3


def y_jump_target_settle_step(screen, mem, generic):
    """Withhold the generic confirm on the fade right after a target confirm."""
    confirmed = mem.get('y_jump_target_confirmed')
    if not isinstance(confirmed, dict):
        return None
    tick, at = mem.get('tick'), confirmed.get('tick')
    if (screen.kind != 'unknown' or type(tick) is not int or type(at) is not int
            or not 0 < tick - at <= Y_JUMP_TARGET_SETTLE_TICKS):
        mem.pop('y_jump_target_confirmed', None)
        return None
    confirms = (isinstance(generic, list) and len(generic) == 1
                and isinstance(generic[0], dict) and generic[0].get('type') == 'pad'
                and generic[0].get('buttons') == ['a'])
    if not confirms:
        return None
    decision_log._record(mem, 'y_jump_target_settle_hold', chart_step=confirmed.get('step'),
                         screen=screen.kind,
                         observed_metric={'after_confirm': tick - at, 'limit': Y_JUMP_TARGET_SETTLE_TICKS},
                         reason='出撃先ジャンプの決定直後の読めない画面で既定のAを押すと出撃先を見ずに決定するため保留する')
    return []


def y_jump_close_settle_step(screen, mem, generic):
    """Withhold the generic confirm on the fade after a target jump we closed.

    The failed jump's Y returns to the target picker with the marker wherever
    the world cursor stopped; an A there decides that spot (g1163 2026-11-11
    07:59:47, see ``navigation._close_target_jump``). The picker itself is
    readable and the sortie flow walks the marker from there.
    """
    closed = mem.get('y_jump_target_closed')
    if not isinstance(closed, dict):
        return None
    tick, at = mem.get('tick'), closed.get('tick')
    if (screen.kind != 'unknown' or type(tick) is not int or type(at) is not int
            or not 0 < tick - at <= Y_JUMP_TARGET_SETTLE_TICKS):
        mem.pop('y_jump_target_closed', None)
        return None
    confirms = (isinstance(generic, list) and len(generic) == 1
                and isinstance(generic[0], dict) and generic[0].get('type') == 'pad'
                and generic[0].get('buttons') == ['a'])
    if not confirms:
        return None
    decision_log._record(mem, 'y_jump_close_settle_hold', chart_step=closed.get('step'),
                         screen=screen.kind,
                         observed_metric={'after_close': tick - at, 'limit': Y_JUMP_TARGET_SETTLE_TICKS},
                         reason='出撃先ジャンプを決定せず閉じた直後の読めない画面で既定のAを押すと、止まった位置を出撃先に決定するため保留する')
    return []


# The same freeze reaches the egg and monster command boxes, which ignore one
# button exactly like the ordinary たまごをつかう／きりふだ／たいきゃく box.
BATTLE_COMMAND_MENU_KINDS = frozenset({'battle_menu', 'egg_battle_menu', 'monster_menu'})


def field_fallback_actions(kind):
    """The no-input fallback's input for a frozen ``field``-phase screen."""
    if kind in {'unknown', 'map'}:
        # A bare map the chart policy has no plan for: SELECT recentres the
        # cursor on the hero (measured) and lets nav_step re-anchor.
        return [pad('select')]
    if kind in {'map_target', 'world_map'}:
        return [pad('b')]                   # leave or cancel an open map view
    if kind in BATTLE_COMMAND_MENU_KINDS:
        # g1190 2026-11-16: A on a stale rescue menu changed nothing and the
        # run ended screen_stalled; B closes it and the melee resumes.
        return [pad('b')]
    return [pad('a')]                       # a message over the field: acknowledge
BATTLE_MENU_HOLD_ADVANCE = 5   # identical readable command frames before one A
# The command box's idle sprite and scroll arrows keep animating, so one
# unchanged screen cycles through several full-frame digests (g1190
# 2026-11-18 04:55-07:25 measured four). Keep a ring instead of the last two
# or the bound restarts on every frame and the menu is never escaped.
BATTLE_MENU_HOLD_RECENT = 6
# A cursor parked on a grey (disabled) row ignores A. Move it up at most this
# many times per unchanged battle-menu episode before the opposite button.
BATTLE_MENU_CURSOR_NUDGES = 2


def battle_menu_cursor_off_rows(screen):
    """The command cursor sits beside a row the parser could not read.

    g1190 2026-11-18 04:55-07:25: a castle-defence egg-battle box showed
    もうこうげき / たまごをつかう with the knight cursor down by the grey
    きりふだ row, and every A was a no-op for 2.5 h while the idle sprite kept
    the digest changing. On the live saved state one ``up`` put the cursor on
    the readable もうこうげき row and the next A opened the summoned monster's
    skill box, so the battle resumed. Only the measured knight cursor and the
    readable rows are evidence; an unreadable cursor (None) is not.
    """
    cursor = screen.menu_cursor
    rows = [line for line in (screen.menu_rows or []) if type(line.y) is int]
    if type(cursor) is not int or not rows:
        return False
    return not any(abs(cursor - line.y) <= 4 for line in rows)


def battle_menu_hold_advance(screen, mem, actions, digest):
    """Break a battle menu that answers the same no-op press forever.

    g1163 2026-11-12 04:44-05:40: a battle command menu stayed on screen while
    the plan kept pressing B, which that box ignores, so the digest never
    changed, `unchanged_seconds` reached 300 s and screen_stalled ended the
    corner — five times in one hour. Repeating the same pad on an unchanged
    battle menu is not progress: after BATTLE_MENU_HOLD_ADVANCE identical
    frames the opposite confirm is sent once, and the count restarts.

    A repeated A on a cursor parked off the readable rows (g1190 above) is a
    different no-op: A is ignored, so the measured escape is one ``up`` to a
    readable row. That nudge is bounded and only taken for A.

    An empty plan keeps its meaning (an orphan parent menu deliberately holds;
    see test_hanjuku_orphan_battle_menu), so only a single repeated pad is
    escalated here.
    """
    if screen.kind not in BATTLE_COMMAND_MENU_KINDS:
        return None
    if (not isinstance(actions, list) or len(actions) != 1
            or not isinstance(actions[0], dict) or actions[0].get('type') != 'pad'):
        mem.pop('battle_menu_hold', None)
        return None
    buttons = tuple(actions[0].get('buttons') or ())
    if buttons not in (('b',), ('a',)):
        mem.pop('battle_menu_hold', None)
        return None
    watch = mem.get('battle_menu_hold') or {}
    # A blinking cursor cycles two or more frames, exactly like the
    # unreadable-screen escape learned in v673: comparing one digest would
    # reset the bound on every frame and the menu would never be escaped. Any
    # digest of the recent ring is the same unchanged screen.
    recent = [d for d in (watch.get('recent') or [])
              if isinstance(d, str)][:BATTLE_MENU_HOLD_RECENT]
    same = (digest in recent) and watch.get('buttons') == list(buttons)
    watch['n'] = (int(watch.get('n') or 0) + 1) if same else 1
    watch['recent'] = ([digest] + [d for d in recent
                                   if d != digest])[:BATTLE_MENU_HOLD_RECENT]
    watch['digest'] = digest
    watch['buttons'] = list(buttons)
    mem['battle_menu_hold'] = watch
    if watch['n'] < BATTLE_MENU_HOLD_ADVANCE:
        return None
    watch['n'] = 0
    nudges = watch.get('nudges')
    nudges = nudges if type(nudges) is int and 0 <= nudges <= BATTLE_MENU_CURSOR_NUDGES else 0
    if (buttons == ('a',) and nudges < BATTLE_MENU_CURSOR_NUDGES
            and battle_menu_cursor_off_rows(screen)):
        watch['nudges'] = nudges + 1
        decision_log._record(mem, 'battle_menu_cursor_nudge', screen=screen.kind,
                             observed_metric={'digest': digest[:16],
                                              'cursor': screen.menu_cursor,
                                              'rows': [line.y for line in screen.menu_rows],
                                              'nudge': nudges + 1,
                                              'limit': BATTLE_MENU_CURSOR_NUDGES},
                             reason='カーソルが読めない行にあるためAが効かない。カーソルを上の行へ1回だけ動かす')
        return [pad('up')]
    other = 'a' if buttons == ('b',) else 'b'
    decision_log._record(mem, 'battle_menu_hold_advance', screen=screen.kind,
                         observed_metric={'digest': digest[:16],
                                          'held': BATTLE_MENU_HOLD_ADVANCE,
                                          'from': list(buttons), 'to': other},
                         reason='同じボタンが効かないまま戦闘メニューが変わらないため、逆のボタンを1回だけ送る')
    return [pad(other)]


def battle_draw_hold_step(screen, mem, previous_kind, generic):
    """Withhold a generic confirm on an unreadable battle draw, boundedly.

    Only a phase default that would confirm (a single A) is replaced. A default
    that already plans no input keeps its reviewed meaning untouched.
    """
    confirms = (isinstance(generic, list) and len(generic) == 1
                and isinstance(generic[0], dict) and generic[0].get('type') == 'pad'
                and generic[0].get('buttons') == ['a'])
    scope = _battle_draw_scope(mem)
    cur = mem.get('battle')
    held = mem.get('battle_draw_hold')
    live = isinstance(held, dict) and held.get('scope') == scope
    if not confirms or screen.kind != 'unknown':
        mem.pop('battle_draw_hold', None)
        return None
    # A hold episode owns its own continuation: after the first held frame the
    # previous observation is 'unknown', not the battle foreground that opened
    # the window. The bounded counter, not that kind, ends the episode.
    if (not isinstance(cur, dict) or not cur.get('ally') or not cur.get('enemy')
            or (previous_kind not in BATTLE_OWNED_KINDS and not live)):
        mem.pop('battle_draw_hold', None)
        return None
    if (live and type(held.get('held')) is int and held['held'] >= BATTLE_DRAW_HOLD_MAX):
        # The window is over without a readable box. Hand the frame back to the
        # ordinary phase default instead of holding a real scene forever.
        mem.pop('battle_draw_hold', None)
        return None
    if (not live or type(held.get('held')) is not int
            or not 0 <= held['held'] < BATTLE_DRAW_HOLD_MAX):
        held = mem['battle_draw_hold'] = {'scope': scope, 'held': 0}
    held['held'] += 1
    decision_log._record(mem, 'battle_draw_hold', screen=screen.kind,
                         observed_metric={'held': held['held'], 'limit': BATTLE_DRAW_HOLD_MAX,
                                          'previous_screen': previous_kind,
                                          'ally': cur.get('ally'), 'enemy': cur.get('enemy')},
                         reason='戦闘パネルとメニューの描画途中を読めないため、未確認の最上段を確定しないよう既定Aを保留する')
    return [{'type': 'wait', 'ms': 50}]


def legacy_actions(frame: Frame, phase: str, state: dict, mem: dict | None = None) -> list[dict]:
    """Reviewed v1 rules for screens without readable text or cursor.

    The v1 blind field route (confirm/left/right/up) is removed: map input
    now comes only from the chart policy with a measured cursor. A map
    without a detected cursor waits instead of confirming at an unknown cell.
    """
    phase_step=int(state.get('phase_step',1))
    if phase=='transition':
        return []
    if phase=='name':
        return []  # Only the readable name-entry policy may type or confirm.
    if phase=='month_menu':
        return [pad('b' if phase_step==1 else 'a')]
    if phase=='concert':
        gold=lambda r,g,b:r>150 and 80<g<180 and 30<b<120
        picker=any(frame.fraction((200,y,239,y+1),gold)>.8 for y in range(178,196))
        return [pad('b' if picker else 'a')]
    if phase=='shop':
        return [pad('a' if shop_exit_confirmation(frame) else 'b')]
    if phase=='title' or (phase=='title_or_intro'
                          and isinstance(state.get('title_tries'), dict)):
        # Never press Start blind: a state that boots to the title must resume
        # the battery save (つづき) instead. g1190 2026-11-16 booted a saved
        # state that showed the title, Start began a new game and the whole
        # chapter-9 progress was lost. Only a bounded set of continue attempts,
        # then Start for a cartridge with no save at all.
        tries = state.get('title_tries')
        if not isinstance(tries, dict):
            tries = state['title_tries'] = {'n': 0}
        tries['n'] = int(tries.get('n') or 0) + 1
        if tries['n'] <= TITLE_CONTINUE_TRIES:
            return [pad('down'), {'type': 'wait', 'ms': 200}, pad('a')]
        # A run that already identified a chapter has progress worth keeping.
        # Start on the title menu confirms はじめから, so falling through to it
        # after the continue attempts fail would replace the battery save
        # (g1190 2026-11-16). Hold and let the host restore instead; only a
        # chapter-less cartridge (no save) may start a game here.
        if mem is not None and type(mem.get('chapter')) is int:
            if not tries.get('held'):
                tries['held'] = True
                decision_log._record(
                    mem, 'title_continue_exhausted',
                    observed_metric={'tries': tries['n'] - 1, 'chapter': mem['chapter']},
                    reason='進行中の章があるためタイトルでつづきを再試行し尽くしてもSTARTを送らず、新規ゲームによる上書きを避ける')
            return []
        return [pad('start')]
    if phase in {'field','field_menu','battle'}:
        # A readable battle panel goes through the risk-aware battle_flow.battle_step;
        # without one the target is unknown, and A on the map would open a
        # sortie menu.
        return []
    # Scripted scenes, battle prompts and dark cutscenes use confirm. No
    # reset, emulator shortcuts, arbitrary keys or LLM-produced actions.
    return [pad('a')]


def decide(frame: Frame, state: dict, *, adjusted: dict | None = None,
           interim: dict | None = None, experience: dict | None = None,
           recall_inputs: dict | None = None, card_scroll_input: dict | None = None,
           departure_input: dict | None = None,
           overtaken_frames: list | None = None,
           run_identity: dict | None = None,
           chart_edition: str | None = None,
           approx_map: bool = False) -> tuple[list[dict], dict]:
    """Decide with only this observation's verified/optional seed map exposed."""
    from .. import hanjuku_chart
    hanjuku_chart.migrate_legacy_labels(state)
    memory = dict(state.get('policy') or {})
    memory['approx_map'] = bool(approx_map)
    previous_version = state.get('bot_version')
    if previous_version and previous_version != BOT_VERSION:
        # A new bot version re-evaluates the transient battle-menu holds. A
        # legacy orphan flow persisted by an older version otherwise keeps
        # holding a battle fade after resume (g1190 ch9 2026-11-15, input_stalled).
        for key in ('orphan_battle_menu', 'resume_battle_menu',
                    'battle_menu_hold', 'battle_draw_hold'):
            memory.pop(key, None)
    # A location mark from an overdue march expires here rather than in the
    # corridor planner: g1163 2026-11-11 23:15-00:05 spent an hour in battles
    # and the month menu without ever planning a sortie, so the planner-only
    # call never ran and 29 of 32 living generals stayed unusable.
    from .os_layer.world import expire_unknown_locations
    expire_unknown_locations(memory)
    # Chapter 9's boss moves: only a fresh Y-view measurement may stand in
    # for its missing static cell (see chart.MOVING_BOSS_CHAPTER).
    reading = memory.get('boss_cell')
    tick = memory.get('tick')
    chapter = memory.get('chapter')
    fresh = (isinstance(reading, dict) and reading.get('chapter') == chapter
             and isinstance(reading.get('cell'), (list, tuple))
             and type(reading.get('tick')) is int and type(tick) is int)
    if fresh and chapter == hanjuku_chart.MOVING_BOSS_CHAPTER:
        # A moving boss's position ages out; a static boss keeps its cell.
        fresh = 0 <= tick - reading['tick'] <= hanjuku_chart.MOVING_BOSS_TICKS
    hanjuku_chart.set_boss_cell(chapter, reading.get('cell') if fresh else None)
    with hanjuku_chart.map_context(memory, approx=approx_map):
        return _decide_impl(frame, state, adjusted=adjusted, interim=interim,
                            experience=experience, recall_inputs=recall_inputs,
                            card_scroll_input=card_scroll_input, departure_input=departure_input,
                            overtaken_frames=overtaken_frames,
                            run_identity=run_identity, chart_edition=chart_edition,
                            _memory=memory)


IDLE_TEXT_KEYS = 24      # distinct (screen kind, text head) pairs kept
IDLE_TEXT_HEAD = 16      # leading characters of the screen text kept

# #4422: caches invalidated when a new run's identity is seen. Named so the
# drop log can report exactly which observations were discarded.
IDENTITY_RESET_KEYS = (
    'house', 'post_month_status', 'post_month_status_last', 'boss_camp_exempt',
    'boss_camp_exempt_scope', 'general_statuses', 'house_field_scan',
    'house_scan_tick', 'house_scan_month', 'month_roster_scan',
    'country_income', 'country_wages', 'recruit_field_scan_attempts',
    'recruit_month_scan_attempts', 'recruit_roster_attempts', 'house_eggs',
    'hero_repair_deferral', 'egg_uses', 'egg_zero_owners', 'egg_types',
    'egg_memory',
    'egg_recheck', 'castle_income', 'castle_ownership', 'starter_party',
    'map_registration', 'map_discovery', 'boss_victories', 'obs_cost',
    'generals_lost',
)


def _identity_label(identity):
    """Short, log-safe run id: ``runtime_id:generation``."""
    if not isinstance(identity, dict):
        return '-'
    return f"{identity.get('runtime_id')}:{identity.get('generation')}"


def _tally_observation(mem, kind, screen, actions):
    """Issue #3194 (36): how many observations send nothing, and while on what.

    g1097 2026-11-02: 36% of 4021 observations issued no input, 1009 of the
    idle ones on a text screen, but the log keeps no screen text, so what they
    waited on was unknowable. Counts only, bounded in keys; never read by a
    decision.
    """
    cost = mem.setdefault('obs_cost', {})
    cost.setdefault('observations', {'n': 0})['n'] += 1
    if actions or kind not in ('text', 'unknown'):
        return
    entry = cost.setdefault('idle_text', {'n': 0, 'by_text': {}})
    entry['n'] += 1
    head = ''.join((getattr(screen, 'text', '') or '').split())[:IDLE_TEXT_HEAD] or '(blank)'
    key = f'{kind}:{head}'
    if key in entry['by_text'] or len(entry['by_text']) < IDLE_TEXT_KEYS:
        entry['by_text'][key] = entry['by_text'].get(key, 0) + 1


def _decide_impl(frame: Frame, state: dict, *, adjusted=None, interim=None,
                 experience=None, recall_inputs=None, card_scroll_input=None, departure_input=None, overtaken_frames=None,
                 run_identity=None, chart_edition=None, _memory):
    """Return bounded pad actions and new policy memory; never write or send.

    ``adjusted`` is a validated runtime-adjusted chart (``hanjuku_chart_adjust``)
    offered for adoption when the base chart has no ready order. ``interim``
    is a JEV answer (a candidate label, never keys) for the pending request.
    ``experience`` is the persistent independent-judgment memory
    (``hanjuku_experience``). None of these are persisted in policy memory.

    Decision records produced for this observation are returned in
    ``state['_records']`` for the caller to persist; the updated experience
    is returned in ``state['_experience']``. Neither is memory.
    """
    from .. import hanjuku_experience as experience_module
    from .flows import house as hanjuku_house
    from .os_layer import roster as hanjuku_roster
    from .. import hanjuku_chart
    from .os_layer.screen import fake_hero_announcement_chapter, month_confirmation_positions, parse
    phase=classify(frame)
    step=int(state.get('step',0))+1
    phase_step=int(state.get('phase_step',0))+1 if state.get('phase')==phase else 1
    mem=_memory
    mem.pop('_map_registration_hold', None)
    mem.pop('_map_cursor_feedback', None)
    mem.pop('_nav_hold_feedback', None)
    mem.pop('_menu_navigation_feedback', None)
    if phase != 'dialogue':
        mem.pop('trade_guard', None)  # release before an earlier foreground owner
    identity = hanjuku_roster.run_identity(run_identity)
    mem['_run_identity'] = identity
    reset_native_map_scope = False
    if identity is not None and mem.get('survey_run_identity') != identity:
        previous_identity = (hanjuku_roster.run_identity(mem.get('survey_run_identity'))
                             or hanjuku_roster.run_identity(state.get('decision_trace')))
        hanjuku_roster.invalidate(mem)
        if previous_identity != identity:
            reset_native_map_scope = bool(mem.get('map_registration') or mem.get('map_discovery'))
            removed = [key for key in IDENTITY_RESET_KEYS if key in mem]
            for key in IDENTITY_RESET_KEYS:
                mem.pop(key, None)
            if removed:
                from .state_budget import log_dropped_keys
                log_dropped_keys(source='identity_change', keys=removed,
                                 reason='run_identity_changed',
                                 from_run=_identity_label(previous_identity),
                                 to_run=_identity_label(identity))
        # A matching old trace may retain observed zero counts, but never
        # upgrades an old unbound roster into a complete reusable snapshot.
        mem['survey_run_identity'] = identity
    mem['_records']=[]
    if reset_native_map_scope and type(mem.get('chapter')) is int and mem['chapter'] in hanjuku_chart.CASTLE_NAMES:
        campaign._enter_chapter(mem, mem['chapter'], evidence='native_map_run_identity_changed',
                                reason='ランIDが変わったため旧ランの実測地図と出撃状態を破棄')
    from .state.regions import flow as _flow, journal as _journal, plan as _plan_region
    mem['_adjusted']=adjusted
    mem['_interim']=interim
    _journal(mem)['_recall_inputs']=recall_inputs
    mem['_card_scroll_input']=card_scroll_input
    _journal(mem)['_departure_input']=departure_input
    mem['_experience']=experience if isinstance(experience, dict) else experience_module.empty()
    updated={**state,'phase':phase,'step':step,'phase_step':phase_step,'bot_version':BOT_VERSION}
    if phase in ('field', 'field_menu', 'battle', 'battle_intro'):
        # A title episode ends only when the run is back in the game world.
        # g1190 2026-11-16: the counter survived the episode, so a later title
        # return pressed Start without trying つづき at all. A title fade
        # (title_or_intro) keeps the episode open instead of restarting it.
        updated.pop('title_tries', None)
    updated['status_panel_feedback'] = None
    updated['map_cursor_feedback'] = None
    updated['nav_hold_feedback'] = None
    updated.pop('_records',None)
    updated.pop('_experience',None)
    screen=parse(frame,phase=phase)
    for sent in overtaken_frames or ():
        # (sha256, Frame): an earlier A reached this frame, not the one it was decided on.
        battle_menus_flow.count_overtaken_monster_turn(parse(sent[1], phase=classify(sent[1])), mem, sent[0])
    if prompts_flow.summer_bonus_continue(screen,mem):
        screen.kind='summer_bonus'
    observe_jump_foreground(screen, mem)
    prompts_flow.observe_events(screen,mem)
    map_flow.observe_sortie_transition(screen,mem,state.get('screen_kind'))
    # Reconcile the deferred-goal ledger with the facts this observation just
    # updated, before any owner runs: an interrupted or deferred want must be
    # visible to this observation's idle-time scheduling.
    goals.refresh(mem)
    kind=screen.kind
    if 'ロードなされますかな' in (screen.text or ''):
        # The save/load menu looks like a shop to the pixel classifier, so the
        # shop phase pressed B forever and screen_stalled (g1190 2026-11-15
        # after the local state was replaced by an older save). Load the
        # selected save with A; the prompt itself is the evidence.
        import re as _re
        loaded_chapter = _re.search(r'だい(\d{1,2})わ', screen.text or '')
        if loaded_chapter is not None and 1 <= int(loaded_chapter.group(1)) <= 12:
            # The save slot names its chapter ("だい9わ"). A fresh policy after
            # a load otherwise keeps chapter None and the corridor plan has no
            # orders to run (g1190 2026-11-15).
            mem['chapter'] = int(loaded_chapter.group(1))
        decision_log._record(mem, 'load_screen_confirm', screen=kind,
                             observed_metric={'text': (screen.text or '')[-40:],
                                              **({'chapter': mem.get('chapter')}
                                                 if mem.get('chapter') else {})},
                             reason='セーブのロード選択画面を実画面文言で確認したため、選択中のセーブをAでロードする')
        updated.update(screen_kind=kind, no_input_streak=0, no_input_frames=[])
        for key in ('_adjusted', '_interim', '_card_scroll_input'):
            mem.pop(key, None)
        _journal(mem).pop('_departure_input', None)
        _journal(mem).pop('_recall_inputs', None)
        updated['_records'] = mem.pop('_records')
        updated['_experience'] = mem.pop('_experience', None)
        updated['policy'] = mem
        return [pad('a')], updated
    from .os_layer.commands import menu_navigation_reading
    navigation = menu_navigation_reading(screen)
    updated['menu_navigation_reading'] = {**navigation, 'step': step} if navigation else None
    reveal_chapter = fake_hero_announcement_chapter(screen)
    if reveal_chapter is not None:
        # This explicit field transition owns the foreground even when a
        # repair/recall or battle command was remembered before the reveal.
        if mem.get('battle'):
            battle_flow.battle_end(mem, 'fake_hero_reveal')
        battle_judgment.reset_battle_controls(mem)
        mem['chapter'] = reveal_chapter
        mem.pop('resume_chapter_pending', None)
        decision_log._record(mem, 'fake_hero_reveal',
                       reason='二行の城の正体告知を実画面で確認したため会話を進める',
                       observed_metric={'chapter': reveal_chapter})
        updated.update(screen_kind=kind, no_input_streak=0, no_input_frames=[])
        # '_recall_inputs' is a migrated journal key: popping it flat would
        # leave the journal copy and could drop a legacy value migrate would
        # have folded in (#3923). The other three are still flat keys.
        for key in ('_adjusted', '_interim', '_card_scroll_input'):
            mem.pop(key, None)
        _journal(mem).pop('_departure_input', None)
        _journal(mem).pop('_recall_inputs', None)
        updated['_records'] = mem.pop('_records')
        updated['_experience'] = mem.pop('_experience', None)
        updated['policy'] = mem
        return [pad('a')], updated
    from .flows import starter as hanjuku_starter
    starter_actions = hanjuku_starter.step(screen, mem, frame)
    if starter_actions is not None:
        # Never let the generic shop/event/no-input fallback skip this list,
        # or let an old monthly/repair transaction own the chapter foreground.
        updated.update(screen_kind=kind, no_input_streak=0, no_input_frames=[])
        # '_recall_inputs' is a migrated journal key: popping it flat would
        # leave the journal copy and could drop a legacy value migrate would
        # have folded in (#3923). The other three are still flat keys.
        for key in ('_adjusted', '_interim', '_card_scroll_input'):
            mem.pop(key, None)
        _journal(mem).pop('_departure_input', None)
        _journal(mem).pop('_recall_inputs', None)
        updated['_records'] = mem.pop('_records')
        updated['_experience'] = mem.pop('_experience', None)
        updated['policy'] = mem
        return starter_actions, updated
    from .flows import camp_recheck as hanjuku_camp_recheck
    hanjuku_camp_recheck.interrupt(screen, mem)
    hanjuku_camp_recheck.observe_camp_status(screen, mem)
    stall_actions = map_flow.boss_stall_escape_step(mem, kind, phase)
    if stall_actions is not None:
        # A parked boss march must not stay behind month/shop/house text for
        # hours. One bounded B returns to the field map, where next_order can
        # re-arm the stale step; normal flows resume if no step is parked.
        updated.update(screen_kind=kind, no_input_streak=0, no_input_frames=[])
        # '_recall_inputs' is a migrated journal key: popping it flat would
        # leave the journal copy and could drop a legacy value migrate would
        # have folded in (#3923). The other three are still flat keys.
        for key in ('_adjusted', '_interim', '_card_scroll_input'):
            mem.pop(key, None)
        _journal(mem).pop('_departure_input', None)
        _journal(mem).pop('_recall_inputs', None)
        updated['_records'] = mem.pop('_records')
        updated['_experience'] = mem.pop('_experience', None)
        updated['policy'] = mem
        return stall_actions, updated
    if state.get('screen_kind') != kind:
        # A shop/information/confirmation round trip starts a new navigation
        # episode. No movement failure belongs to a different visible screen.
        mem.pop('month_nav', None)
        mem.pop('house_nav', None)
    map_kinds={'map','map_target','castle_menu','general_list','card_select','sortie_confirm'}
    if kind not in map_kinds and mem.get('cursor'):
        # Battles, events and month menus can move the map cursor.
        mem['uncertain']=True
        for key in ('nav_last','nav_pressed','nav_still'):
            mem.pop(key,None)
    if kind in {'castle_menu','general_list'}:
        mem.pop('expect_menu',None)
        mem.pop('menu_miss',None)   # a real menu proves the cell was correct
    flow=(mem.get('battle') or {}).get('card_flow')
    # A readable card name routes the open list, but a depleted list draws no
    # card name at all (g452 07:14: ヴィーナス had spent both イッテツーン, the
    # きりふだ menu showed nothing, the flow stayed on this text screen and
    # legacy A presses stalled the run). The open list itself is evidence
    # enough to let card_list_step back out with B.
    card_list=bool(flow) and kind in {'text','unknown'} and (
        (flow.get('stage') == 'list' and (kind == 'text' or flow.get('survival')))
        or any(w in readers.CARD_NAMES for line in screen.lines for _,w in line.spans()))
    # Egg/card announcements and fades inside a battle are not its end; only
    # a return to the map or a following event/menu closes the record.
    if kind != 'battle_menu_pending':
        mem.pop('battle_menu_pending_ticks', None)
    if kind != 'monster_menu_pending':
        mem.pop('monster_menu_pending_ticks', None)
    if (mem.get('monster_unchosen') and kind not in ('monster_menu', 'monster_menu_pending', 'unknown')
            and (kind != 'text' or screen.text)):
        # The turn moved on (a message, the enemy's menu, the battle field).
        battle_menus_flow.count_unchosen_monster_turn(mem)
    if kind == 'text' and screen.text:
        mem.pop('monster_own_choice', None)
    if kind != 'battle' and mem.get('battle'):
        mem['battle'].pop('successor_seen', None)
        mem['battle'].pop('entry_successor_seen', None)
        mem['battle'].pop('unlocated_successor_seen', None)
        mem['battle'].pop('unlocated_zero_candidate', None)
    # The 帰還先ピッカー (world_map) can cover a fight that already ended at HP 0;
    # it closes that record, but never an unfinished fight (#3009).
    after_battle = (kind in readers.AFTER_BATTLE_KINDS or kind == 'barrier_removed'
                    or (kind == 'world_map' and battle_flow.battle_finished(mem.get('battle'))))
    if mem.get('battle') and after_battle:
        battle_flow.battle_end(mem,kind)
    if after_battle:
        battle_judgment.reset_battle_controls(mem)
        mem.pop('monster_unchosen', None)
        mem.pop('monster_own_choice', None)
    battle_flow.observe_entry_return(screen, mem)
    battle_flow.observe_season_king_panel(screen, mem)
    # A spinning reel can be unreadable between full glyphs. Such a frame
    # must not refill its bounded stop allowance or lose proven slot mode.
    slot_drawing = ((mem.get('egg_choice') or {}).get('mode') == 'slot'
                    and kind in {'unknown', 'text', 'battle_menu_pending', 'monster_menu_pending'})
    if kind != 'egg_choice_menu' and not slot_drawing:
        mem.pop('egg_choice', None)
    if kind != 'egg_battle_menu':
        mem.pop('egg_menu_stage',None)
        mem.pop('egg_battle_row_dead',None)
        mem.pop('enemy_general_menu', None)
        mem.pop('enemy_general_menu_pending', None)
    if kind != 'monster_menu':
        for key in ('monster_menu_key','monster_menu_cursor','monster_menu_hold',
                    'monster_menu_choice','monster_menu_choice_key','monster_panel'):
            mem.pop(key,None)
    if kind != 'battle_menu':
        for key in ('indep_menu','indep_menu_key','indep_menu_action','egg_row_dead'):
            mem.pop(key,None)
    # Every explicit foreground confirmation keeps its owner's amount/target
    # checks. A missing hand alone is not evidence of such a foreground: after
    # an egg ritual it may simply be a monthly return with a blinking cursor.
    sub = mem.get('month_sub') or {}
    event_confirmation = (bool(month_confirmation_positions(screen))
                          and (readers._is_general_trade_prompt(screen.text)
                               or readers._is_goninja_prompt(screen.text)))
    month_dialog = ((sub.get('kind') == 'recruit' and not readers.month_menu_ready(screen))
                    or (bool(sub) and bool(month_confirmation_positions(screen))))
    recall = _flow(mem).get('recall') or {}
    if (recall and phase == 'concert' and recall.get('stage') != 'await_dispatch'
            and not recall.get('retreat_recheck') and not recall.get('anonymous_recheck')
            and not recall.get('scene_pending')):
        # g1075 2026-10-29 13:02: the month rolled over while the camp recall
        # was choosing its destination. The month-start scene (「こんげつは……」,
        # phase 'concert') was handed to the recall, which waits on text, so
        # nothing was pressed for 91 observations until the recall gave up;
        # meanwhile ポセイドン (HP 1, no soldiers) marched back into リグア and
        # fell. The scene closes the picker anyway: drop the recall and let the
        # scene advance. The camp is found again on the map afterwards.
        # #3544: the interruption marker belongs to this recall; sweep it too
        # so a later unrelated recall cannot inherit the short retry hold.
        usable = [int(d) for d in (mem.get('month_durations') or [])[-3:] if type(d) is int]
        stages = {'stage': recall.get('stage'), 'steps': recall.get('steps'),
                  'goal': recall.get('goal'), 'target': recall.get('target'),
                  'dest_target': recall.get('dest_target'),
                  **({'month_estimate': max(usable)} if usable else {})}
        recall = _recall_after_scene(mem, recall, kind, phase) or {}
        mem['uncertain'] = True
        decision_log._record(mem, 'camp_recall_interrupted', screen=kind, phase=phase,
                             observed_metric=stages,
                             reason='舞台の場面（月初など）が始まり帰還先の選択が閉じたため、帰還指示を中断して場面を進める')
    if isinstance(recall, dict) and recall.get('scene_pending') and phase != 'concert':
        # One interruption per scene: the scene can stay on screen for many
        # frames, and counting each of them would abandon the camp at once.
        recall.pop('scene_pending', None)
    recall_dialog = _recall_dialog_wanted(recall, screen, phase, mem)
    # docich#3958: chapter 9's mechanical march disables the monthly pass
    # (corridor.mechanical: v665/v668), so no recall task is ever queued, no
    # status panel is opened and nothing seeds the recall state — a camp left
    # on the field stays forever. Reopen the existing camp recall's entry
    # point on idle map frames: ``camp_recall_step`` starts a recall from a
    # visible camp when no recall is in flight, and its own gates
    # (``boss_phase``/``camp_stays`` redirect, skip cooldowns, the named
    # recheck queue of #3008) still apply inside.
    ch9_field_recall = (not recall_dialog and phase != 'concert' and kind == 'map'
                        and frame is not None and _chapter9_field_recall(mem))
    # Cancel an outstanding repair before an emergency recall can move its
    # general; the old house route must not resume afterwards.
    if mem.get('house') and _flow(mem).get('recall'):
        hanjuku_house.step(screen, mem, frame)
    # A planned summon-preemption must not become a blind A on an unfinished
    # command menu, or be replaced by an old monthly/house transaction.
    battle_flow.note_duel_event(screen, mem)
    # A camp recall already in flight outranks the sortie plan's castle jump
    # on the field map and on the camp menu. The jump's confirm A otherwise
    # consumed the frame and the recall was dropped before its first approach
    # step (g1190 2026-11-15 75-9: camp_found at [175,96], then
    # castle_jump_confirm, and the camp menu was closed as 'not a recall in
    # flight'). The same jump ended on the camp menu at 77-8 (173415
    # castle_jump_failed text) and closed it before いどう could be chosen.
    recall_in_flight = _flow(mem).get('recall') or {}
    actions = None
    if isinstance(recall_in_flight, dict):
        stage = recall_in_flight.get('stage')
        camp_menu = (stage == 'menu' and recall_flow.is_camp_menu(screen))
        # The menu stage also owns the field frames: when the A on the tent
        # did not open the menu, the recall has to re-centre the tent; the
        # sortie's castle jump otherwise consumed the frame (g1190 77-10
        # 178836 camp_enter -> 178838 castle_jump_confirm).
        field_picker = ((stage in ('to_camp', 'dest', 'menu')
                         and kind in ('map', 'map_target') and phase != 'concert'))
        if camp_menu or field_picker:
            actions = recall_flow.camp_recall_step(screen, mem, frame)
    if actions is None:
        actions = map_flow.castle_jump_step(screen, mem, frame)
    if actions is None:
        # A successful reanchor is a nested interrupt: restore the exact
        # foreground owner before unrelated normal-priority flows run.
        actions = map_flow.resume_reanchored_owner(screen, mem, frame)
    if actions is None:
        actions = egg_recovery_flow.resume_egg_ritual_step(screen, mem)
    if actions is None:
        actions = deploy_flow.resume_sortie_step(screen, mem)
    sortie_cancel_hold = False
    if actions is None:
        actions = deploy_flow.boss_stock_restart_step(screen, mem)
        sortie_cancel_hold = actions == [] and bool(mem.get('boss_stock_restart') or mem.get('boss_stock_cancelled'))
    if actions is None:
        actions = battle_menus_flow.orphan_battle_menu_step(screen, mem)
    if actions is None:
        actions = battle_menus_flow.defender_egg_pending_step(screen, mem)
    if actions is None:
        actions = battle_menus_flow.command_open_pending_step(screen, mem)
    trade_guard_hold = False
    if actions is None:
        actions = prompts_flow.trade_guard_step(screen, mem, phase=phase)
        # The monthly event that carries the trade is phase 'concert' (red
        # curtain) or the classify() fallback 'event', so a held refusal must
        # suppress the no-input fallback there too; otherwise its legacy A
        # confirms the default upper row.
        trade_guard_hold = (phase in ('dialogue', 'concert', 'event') and actions == []
                            and (mem.get('trade_guard') or {}).get('held') is True)
    bijin_guard_hold = False
    if actions is None:
        actions = prompts_flow.bijin_guard_step(screen, mem, phase=phase)
        # The measured monthly-event scene is phase 'concert', so a held beauty
        # request must suppress the no-input fallback there too; the guard only
        # sets `held` on its own request frame.
        bijin_guard_hold = (actions == [] and (
            mem.get('bijin_gift_hold') is True or
            (mem.get('bijin_guard') or {}).get('held') is True))
    if actions is None:
        actions = month_flow.army_count_pending_step(screen, mem)
    if actions is None and event_confirmation:
        # Owner (2026-10-06): trade refusal and the goninja cash floor outrank
        # a remembered monthly recruit/exit that could accept this yes/no.
        # Only both explicit choices plus event wording can take precedence;
        # regular paid prompts and recruitment introductions keep their owner.
        month_flow.interrupt_month_payment(mem)
        actions = prompts_flow.yes_no_step(screen, mem)
    if actions is None:
        actions = (recall_flow.camp_recall_step(screen, mem, frame)
                   if recall_dialog or ch9_field_recall
                   else month_flow.month_sub_step(screen, mem) if mem.get('month_sub') and (kind != 'month_menu' or month_dialog)
                   else None)
    # No house phase expects a red-curtain screen (concert/merchant backdrop):
    # every branch answers such a text frame with [] or B, which advances
    # neither the event nor input. On an outbound repair that starved the trip
    # until STEP_LIMIT (g530 2026-10-01 07:01/07:44/07:52 field_status stalls)
    # and the close phase's repeated B could not clear it either, so the
    # transaction aborted (07:53:43). Let the ordinary concert fallback advance
    # the event without consuming the bounded house budget; the house route
    # resumes on its own screen. This covers return and outbound flows alike.
    concert_house_event = (
        mem.get('house') is not None and phase == 'concert' and kind == 'text')
    if actions is None:
        from .flows import post_month_status
        actions = post_month_status.step(screen, mem, frame)
        monthly = mem.get('post_month_status') or {}
        # Only this owner's freshly entered status stage may accelerate A.
        # A generic confirm, a pending/retried read, or another flow cannot
        # borrow an old marker. The next frame must still prove the panel.
        if (kind == 'text' and actions == [pad('a')]
                and monthly.get('stage') == 'status' and monthly.get('phase_reads') == 0
                and monthly.get('selected') == screen.selected
                and navigation and navigation.get('menu') == 'global_roster'):
            updated['status_panel_feedback'] = {
                'step': step, 'kind': kind, 'transition': 'general_status_open'}
        elif (kind == 'text' and actions == [pad('b')]
                and monthly.get('stage') == 'roster_next' and monthly.get('phase_reads') == 0
                and monthly.get('close_probe_pending') is True):
            updated['status_panel_feedback'] = {
                'step': step, 'kind': kind, 'transition': 'general_status_close'}
    if actions is None:
        from .flows import arrival_status
        actions = arrival_status.step(screen, mem)
    if actions is None:
        from .flows import boss_pursuit
        actions = boss_pursuit.step(screen, mem, frame)
    if actions is None:
        from .flows import map_registration
        actions = map_registration.step(screen, mem, frame)
    if actions is None and not concert_house_event:
        actions = hanjuku_house.step(screen, mem, frame)
    if actions is not None:
        pass
    elif phase=='name' and kind!='name_entry':
        decision_log._record(mem,'name_wait',chart_step='name',
                       reason='状況判定保留: 名前入力画面の文字を読めないため入力を保留')
        actions=[]
    elif kind=='name_entry':
        if (mem.get('name') or {}).get('done'):
            # A second name screen means a new game: never carry the previous
            # game's orders, captures or cursor into it.
            stats=mem.get('stats')
            kept_experience=mem.get('_experience')
            records = mem['_records']
            mem.clear()
            mem.update(_records=records, previous_stats=stats, _experience=kept_experience)
            decision_log._record(mem,'new_game_detected',chart_step='name',
                           reason='名前入力画面を再度確認したため方策状態を初期化')
        actions=name_flow.name_step(screen,mem)
        if mem.get('name',{}).get('done') and not mem.get('chapter'):
            mem['chapter']=1
            # Pin the chart edition for this game only; hot-loaded config
            # changes never switch a game already in progress.
            mem['chart_edition']=edition.resolve(chart_edition,(identity or {}).get('runtime_id'))
            decision_log._record(mem,'chart_edition_pinned',chart_step='name',
                           strategy_variant=mem['chart_edition'],
                           observed_metric={'setting':edition.setting(chart_edition),
                                            'runtime_id':(identity or {}).get('runtime_id')},
                           reason='新しいゲームの開始時にチャート版を固定する')
            mem['variant']='chart' if campaign.chart.orders(1) else 'chart_unavailable'
            home=campaign.chart.home_castle(1)
            castles=campaign.chart.castles(1)
            if home in castles:
                mem['cursor']=list(castles[home])
    elif card_list:
        actions=battle_menus_flow.card_list_step(screen,mem)
    elif kind=='battle':
        actions=battle_flow.battle_step(screen,mem)
    elif kind=='egg_choice_menu':
        actions=battle_menus_flow.egg_choice_step(screen,mem)
    elif kind=='monster_menu':
        actions=battle_menus_flow.monster_menu_step(screen,mem)
    elif kind=='monster_menu_pending':
        actions=battle_menus_flow.monster_menu_pending_step(screen,mem)
    elif kind=='egg_battle_menu' or (mem.get('egg_battle') and kind=='text'):
        actions=battle_menus_flow.egg_battle_step(screen,mem)
    elif kind=='okunote_menu':
        actions=battle_menus_flow.okunote_step(screen,mem)
    elif kind=='battle_menu_pending':
        actions=battle_menus_flow.battle_menu_pending_step(mem)
    elif kind=='battle_menu':
        actions=battle_menus_flow.battle_menu_step(screen,mem)
    elif kind in {'attack_started','defense_started','boss_attack_started','field_encounter_started'}:
        actions=battle_flow.message_step(screen,mem)
    elif kind=='barrier_removed':
        if mem.get('chapter') == 1:
            decision_log._record(mem,'barrier_removed',chart_step='1-barrier',
                           observed_metric={'message':screen.text},
                           resulting_event='barrier_removed',
                           reason='実測済みのいばら消滅確認文を読んだためAで閉じる')
            actions=[pad('a')]
        else:
            decision_log._record(mem,'situation_held',screen=kind,
                           reason='いばら消滅表示を確認したが章を確定できないため保留')
            actions=[]
    elif kind=='month_menu':
        actions=month_flow.month_step(screen,mem)
    elif kind in {'shop_list','shop_quantity_prompt','shop_quantity','shop_exit_confirm'}:
        actions=shop_flow.shop_step(screen,mem)
    elif kind=='castle_menu' and not mem.get('chapter'):
        actions=deploy_flow.chapterless_status_step(screen,mem)
    elif kind in {'castle_menu','general_list','card_select','sortie_confirm'} and mem.get('chapter'):
        actions=deploy_flow.deploy_step(screen,mem)
    elif kind=='map_target' and mem.get('chapter'):
        actions=map_flow.target_step(screen,mem,frame)
    elif kind=='world_map' and mem.get('chapter'):
        actions=map_flow.world_map_step(screen,mem,frame)
    elif kind=='map' and mem.get('chapter'):
        actions=map_flow.map_step(screen,mem,frame)
    elif kind=='discharge_menu':
        actions=shop_flow.discharge_step(screen,mem,frame)
    elif kind=='gift_request':
        actions=prompts_flow.gift_step(screen,mem)
    elif kind=='summer_bonus':
        actions=prompts_flow.summer_bonus_step(screen,mem)
    elif kind=='summer_bonus_message':
        actions=[pad('a')]
    elif kind=='yes_no':
        actions=prompts_flow.yes_no_step(screen,mem)
    elif kind in {'castle_info','sealed_castle','main_menu'}:
        decision_log._record(mem,'close_panel',screen=kind,reason='意図しない情報画面を閉じる')
        actions=[pad('b')]
    elif kind == 'text' and mem.get('shop') and shop_flow.holding_limit_message(screen):
        actions=shop_flow.shop_step(screen,mem)
    elif kind == 'text' and month_flow.chikujou_leftover(screen, mem):
        actions = [pad('b')]
    elif kind=='text' and readers.is_camp_menu(screen):
        # g436 22:04: an A after closing the Y view landed on the hero's tent
        # and opened いどう/ステータス/キャンプ/きかん with no recall in flight;
        # nothing handled it and the bot pressed nothing for over an hour.
        decision_log._record(mem,'close_panel',screen='camp_menu',reason='帰還指示中でない野営メニューを閉じる')
        actions=[pad('b')]
    elif kind=='text' and readers.is_fort_menu(screen):
        # g1097 2026-11-01 19:24 and 19:33: the same stray A landed on a 砦 and
        # its しゅつげき/こうかん stayed open for 101 observations each time.
        decision_log._record(mem,'close_panel',screen='fort_menu',reason='帰還指示中でない砦のメニューを閉じる')
        actions=[pad('b')]
    menu_escape = map_flow.boss_sortie_menu_escape_step(mem, kind, actions)
    if menu_escape is not None:
        # A boss sortie menu that keeps returning [] would otherwise hit the
        # 300 s input_stalled terminal (g1075 08:33). Bounded B, then retry.
        actions = menu_escape
    if actions is None:
        actions=legacy_actions(frame,phase,updated,mem)
        a_escape = phase_a_escape_step(mem, phase, kind, screen.text, actions, step=step)
        if a_escape is not None:
            actions = a_escape
        draw_hold = battle_draw_hold_step(screen, mem, state.get('screen_kind'), actions)
        if draw_hold is not None:
            actions = draw_hold
        jump_hold = y_jump_unknown_hold_step(screen, mem, state.get('screen_kind'), actions)
        if jump_hold is not None:
            actions = jump_hold
        survey_hold = world_survey_unknown_hold_step(screen, mem, actions)
        if survey_hold is not None:
            actions = survey_hold
        settle_hold = y_jump_target_settle_step(screen, mem, actions)
        if settle_hold is not None:
            actions = settle_hold
        close_hold = y_jump_close_settle_step(screen, mem, actions)
        if close_hold is not None:
            actions = close_hold
        plain_text = plain_text_hold(screen)
        if (kind in {'unknown'} or plain_text) \
                and (mem.get('chapter') or mem.get('resume_chapter_pending')) \
                and (phase not in {'transition', 'title', 'title_or_intro'} or mem.get('battle')):
            hold_digest = frame.digest()
            if mem.get('held_phase')!=phase:
                mem['held_phase']=phase
                decision_log._record(mem,'situation_held',screen=kind,phase=phase,
                               observed_metric={'kind': kind, 'phase': phase,
                                                'variant': mem.get('variant'),
                                                'digest': hold_digest[:16]},
                               reason='状況判定保留: 文字・カーソル・戦闘表示を読めない画面')
            actions = unknown_hold_advance(screen, mem, actions, hold_digest, phase)
        else:
            mem['held_phase']=None
            mem.pop('unknown_hold_watch', None)
    actions=battle_menus_flow.summon_dialogue_pace(screen,mem,phase,kind,actions,frame.digest())
    hold_escape = battle_menu_hold_advance(screen, mem, actions, frame.digest())
    if hold_escape is not None:
        actions = hold_escape
    # The reanchor FSM only repairs coordinates; dispatch may now resume a
    # previously active order or explain why no safe action is available.
    map_flow.note_reanchor_followup(screen, mem, actions)
    # Bound B presses for the entire concert phase, regardless of blinking
    # pixels or alternating A/B detections. Once exhausted, keep advancing
    # with A until the phase actually changes; never re-arm on a new digest.
    digest=frame.digest()
    picker_watch=mem.get('concert_picker_watch') or {}
    if phase!='concert':
        mem.pop('concert_picker_watch',None)
    else:
        b_count=int(picker_watch.get('b_count') or 0)
        exhausted=bool(picker_watch.get('exhausted'))
        if not exhausted and actions==[pad('b')]:
            b_count+=1
            if b_count>=CONCERT_PICKER_REPEAT_MAX:
                exhausted=True
                decision_log._record(mem,'concert_picker_escape',screen=kind,phase=phase,
                           observed_metric={'concert_b_presses':b_count,'frame_sha256':digest},
                           reason='コンサート中のB入力が上限に達したため、画面の明滅に関わらずAで進める')
        if exhausted:
            actions=[pad('a')]
        if b_count or exhausted:
            mem['concert_picker_watch']={'b_count':b_count,'exhausted':exhausted}
        else:
            mem.pop('concert_picker_watch',None)
    concert_key = (f'text:{screen.text}' if kind == 'text' and screen.text
                   else digest)
    # A battle's summon/monster dialogue has its own reviewed pacing
    # (battle_menus_flow.summon_dialogue_pace): never shortcut it here, or an
    # A lands on the skill box before the page settles.
    event_guarded = (trade_guard_hold or bijin_guard_hold
                     or mem.get('bijin_gift_hold') is True
                     or (mem.get('trade_guard') or {}).get('held') is True
                     or bool(mem.get('battle')))
    if event_text_advance(mem, phase, kind, concert_key, actions, event_guarded):
        decision_log._record(mem,'event_text_advance',screen=kind,phase=phase,
                       observed_metric={'unchanged_observations':EVENT_TEXT_ADVANCE_MAX,
                                        'frame_sha256':digest},
                       reason='イベント会話が同一文面のまま他担当の待機で止まっているため、Aで会話を進める')
        actions=[pad('a')]
    # 保留の廃止: a screen that froze while we planned no input must not stay
    # that way. The streak counts only observations whose frame digest is one of
    # the two most recent distinct images -- the same evidence
    # hanjuku_run.screen_stalled treats as one unchanged screen -- so a fade, an
    # animation or marching units are never cut short, and a real policy input
    # starts the count over. Past NO_INPUT_HOLD_MAX the phase's own default
    # input is sent; the five phases whose reviewed default is deliberately
    # empty get the key a player would reach for instead of another wait.
    # g1067 11:13-11:23: 「かんじゅくプリンスじょうは いっぽ かんせいに ちかづいた!!」
    # sat over the field for ten minutes; the water and the ▼ prompt changed
    # the digest on every observation, the streak never passed 1, and the run
    # ended input_stalled. A message box is the same screen while it reads the
    # same text.
    # 'text' keeps its historical key shape; menu/banner kinds get a semantic
    # key (see MENU_HOLD_KINDS) so background animation cannot restart the
    # count. Cursor position and hand location are part of the key, so moving
    # the selection still counts as a real change and restarts the streak.
    if kind=='text' and screen.text:
        hold_key=f'text:{screen.text}'
    elif kind in MENU_HOLD_KINDS and screen.text:
        hold_key=(f'menu:{kind}:{screen.text}:{screen.menu_cursor}'
                  f':{screen.selected}:{screen.hand}')
    else:
        hold_key=digest
    frozen=[d for d in (updated.get('no_input_frames') or []) if isinstance(d,str)][:2]
    # A raw digest in the recent list is still evidence of the same screen:
    # it keeps a blinking menu counted and stays compatible with state written
    # before semantic keys existed (the seeded digest key simply ages out).
    if actions:
        streak=0
    elif hold_key in frozen or digest in frozen:
        streak=int(updated.get('no_input_streak') or 0)+1
    else:
        streak=1
    bounded_wait=(any(_flow(mem).get(k) for k in ('recall','sortie_attempt'))
                  or any(mem.get(k) for k in ('house','month_sub','y_jump',
                                           'resume_egg_ritual')))
    if (not actions and not bounded_wait
            and not mem.get('_map_registration_hold')):
        nudge = map_unsteerable_nudge(screen, mem, kind, phase, streak, digest)
        if nudge is not None:
            actions = nudge
    hold_max=(FIELD_TEXT_HOLD_MAX if kind=='text' and phase=='field' and screen.text and not bounded_wait
              else BATTLE_COMMAND_MENU_HOLD_MAX
              if kind in BATTLE_COMMAND_MENU_KINDS and not bounded_wait
              else NO_INPUT_HOLD_MAX)
    army_owned = month_flow.army_count_owned(mem)
    recruit_guard = month_flow.recruit_input_guard(screen, mem)
    if (not actions and streak > hold_max and kind == 'month_menu'
            and (army_owned or recruit_guard is not None)):
        # Owner 2026-11-04: the free army/recruit checks must not own the
        # month menu forever. g1107 46-9 stalled 300 s with no input and the
        # run ended screen_stalled. Close the shop with B (never buy here),
        # release the check, and let the next observation re-read the menu.
        mem.pop('army_count_check', None)
        if isinstance(mem.get('shop'), dict) and not mem['shop'].get('soldier_receipt'):
            mem['shop']['soldiers_done'] = True
        decision_log._record(mem, 'month_menu_stall_release', screen=kind, phase=phase,
                observed_metric={'streak': streak, 'army_owned': army_owned,
                                 'recruit_guard': bool(recruit_guard)},
                reason='月一メニューで兵数・募集の確認が進まず無入力のまま停滞したため、購入せずBで閉じて再観測する')
        army_owned = False
        recruit_guard = None
    if (not actions and streak>hold_max and not trade_guard_hold and not bijin_guard_hold
            and not (kind == 'gift_request' and mem.get('bijin_gift_hold') is True)
            and not (sortie_cancel_hold or (kind == 'sortie_confirm'
                     and isinstance(mem.get('boss_stock_cancelled'), dict)))
            and not mem.get('_map_registration_hold')
            and not (kind == 'card_select' and isinstance(mem.get('boss_card_hold'), dict)
                     and mem['boss_card_hold'].get(_plan_region(mem).get('active')))
            and not isinstance(mem.get('resume_egg_ritual'), dict)
            and not isinstance(mem.get('orphan_battle_menu'), dict)
            and not (kind == 'egg_battle_menu'
                     and (battle_menus_flow._enemy_general_command(screen) is not None
                          or battle_menus_flow._partial_enemy_general_command(screen) is not None))
            and not army_owned
            and recruit_guard is None):
        # Even this last-resort path must not confirm an unread summer option:
        # the cursor may be on the mass-dismissal row. Reobserve after one move.
        # These recognized events must never borrow a scripted scene's blind A.
        actions=([pad('up')] if kind=='summer_bonus'
                 else [pad('b')] if kind=='month_menu' or event_confirmation
                 else legacy_actions(frame,phase,updated,mem))
        if kind == 'month_menu':
            # The legacy monthly A repeatedly opened the merchant from a
            # held background row. Cancel and re-read the menu instead; only
            # an observed target selection may confirm or spend money.
            mem.pop('month_nav', None)
            mem.pop('house_nav', None)
        if not actions:
            if phase=='field':
                actions=field_fallback_actions(kind)
                if kind in {'unknown','map'}:
                    # Mirrors the bookkeeping map_step.select_focus does after
                    # the SELECT jump.
                    mem['nav_last']=None
                    mem['uncertain']=True
                    mem['select_focus_tick']=int(mem.get('tick') or 0)
            elif phase=='transition':
                actions=[pad('a')]          # a dark scene that stopped moving, not a fade
            elif phase=='name':
                actions=[pad('b')]          # only the readable name policy may type or confirm
            else:
                actions=[pad('b')]          # field_menu / battle: close, cancel or back out
        # Deliberately outside INPUT_CONTEXT_DECISIONS: the record that already
        # explains the hold (situation_held, chart_adjust_request, ...) stays the
        # input context, so strategy_variant and deviation_reason are not
        # replaced by this mechanical follow-up. And outside SPOKEN, like every
        # other wait: it is logged, not narrated.
        decision_log._record(mem,'no_input_fallback',screen=kind,phase=phase,
                       observed_metric={'frozen_observations':streak,'frame_sha256':digest},
                       reason='同一画面で入力0件の観測が上限を超えたため、フェーズ別の既定入力で保留を解消する')
        streak=0
    updated['no_input_streak']=streak
    updated['no_input_frames']=[hold_key]+[d for d in frozen if d!=hold_key][:1]
    updated['screen_kind']=kind
    menu_feedback = mem.pop('_menu_navigation_feedback', None)
    if menu_feedback is not None:
        updated['menu_navigation_reading'] = {**menu_feedback, 'step': step}
    map_feedback = mem.pop('_map_cursor_feedback', None)
    if map_feedback is not None:
        updated['map_cursor_feedback'] = {**map_feedback, 'step': step}
    nav_feedback = mem.pop('_nav_hold_feedback', None)
    if nav_feedback is not None:
        updated['nav_hold_feedback'] = {**nav_feedback, 'step': step}
    _tally_observation(mem, kind, screen, actions)
    mem.pop('_adjusted',None)
    mem.pop('_interim',None)
    _journal(mem).pop('_recall_inputs',None)
    mem.pop('_card_scroll_input',None)
    _journal(mem).pop('_departure_input',None)
    updated['_records']=mem.pop('_records')
    updated['_experience']=mem.pop('_experience',None)
    updated['policy']=mem
    return actions,updated
