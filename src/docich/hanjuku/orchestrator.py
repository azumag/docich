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
from .strategy import campaign, edition
from .os_layer import readers
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
# A message box over the field that no flow claims is acknowledged sooner:
# the fallback presses A there anyway (g1067 11:13: a castle-progress notice).
FIELD_TEXT_HOLD_MAX = 4

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


def legacy_actions(frame: Frame, phase: str, state: dict) -> list[dict]:
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
    if phase=='title':
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
           overtaken_frames: list | None = None,
           run_identity: dict | None = None,
           chart_edition: str | None = None,
           approx_map: bool = False) -> tuple[list[dict], dict]:
    """Decide with only this observation's verified/optional seed map exposed."""
    from .. import hanjuku_chart
    hanjuku_chart.migrate_legacy_labels(state)
    memory = dict(state.get('policy') or {})
    memory['approx_map'] = bool(approx_map)
    with hanjuku_chart.map_context(memory, approx=approx_map):
        return _decide_impl(frame, state, adjusted=adjusted, interim=interim,
                            experience=experience, recall_inputs=recall_inputs,
                            card_scroll_input=card_scroll_input, overtaken_frames=overtaken_frames,
                            run_identity=run_identity, chart_edition=chart_edition,
                            _memory=memory)


def _decide_impl(frame: Frame, state: dict, *, adjusted=None, interim=None,
                 experience=None, recall_inputs=None, card_scroll_input=None, overtaken_frames=None,
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
            for key in ('house', 'house_field_scan', 'house_scan_tick', 'house_scan_month',
                        'recruit_field_scan_attempts', 'recruit_month_scan_attempts', 'recruit_roster_attempts',
                        'house_eggs', 'hero_repair_deferral', 'egg_uses', 'egg_zero_owners', 'egg_types', 'egg_recheck', 'castle_income', 'castle_ownership',
                        'starter_party', 'map_registration', 'map_discovery', 'boss_victories'):
                mem.pop(key, None)
        # A matching old trace may retain observed zero counts, but never
        # upgrades an old unbound roster into a complete reusable snapshot.
        mem['survey_run_identity'] = identity
    mem['_records']=[]
    if reset_native_map_scope and type(mem.get('chapter')) is int and mem['chapter'] in hanjuku_chart.CASTLE_NAMES:
        campaign._enter_chapter(mem, mem['chapter'], evidence='native_map_run_identity_changed',
                                reason='ランIDが変わったため旧ランの実測地図と出撃状態を破棄')
    mem['_adjusted']=adjusted
    mem['_interim']=interim
    mem['_recall_inputs']=recall_inputs
    mem['_card_scroll_input']=card_scroll_input
    mem['_experience']=experience if isinstance(experience, dict) else experience_module.empty()
    updated={**state,'phase':phase,'step':step,'phase_step':phase_step,'bot_version':BOT_VERSION}
    updated.pop('_records',None)
    updated.pop('_experience',None)
    screen=parse(frame,phase=phase)
    for sent in overtaken_frames or ():
        # (sha256, Frame): an earlier A reached this frame, not the one it was decided on.
        battle_menus_flow.count_overtaken_monster_turn(parse(sent[1], phase=classify(sent[1])), mem, sent[0])
    if prompts_flow.summer_bonus_continue(screen,mem):
        screen.kind='summer_bonus'
    prompts_flow.observe_events(screen,mem)
    map_flow.observe_sortie_transition(screen,mem,state.get('screen_kind'))
    kind=screen.kind
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
        for key in ('_adjusted', '_interim', '_recall_inputs', '_card_scroll_input'):
            mem.pop(key, None)
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
        for key in ('_adjusted', '_interim', '_recall_inputs', '_card_scroll_input'):
            mem.pop(key, None)
        updated['_records'] = mem.pop('_records')
        updated['_experience'] = mem.pop('_experience', None)
        updated['policy'] = mem
        return starter_actions, updated
    from .flows import camp_recheck as hanjuku_camp_recheck
    hanjuku_camp_recheck.interrupt(screen, mem)
    hanjuku_camp_recheck.observe_camp_status(screen, mem)
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
    after_battle = kind in readers.AFTER_BATTLE_KINDS or kind == 'barrier_removed'
    if mem.get('battle') and after_battle:
        battle_flow.battle_end(mem,kind)
    if after_battle:
        battle_judgment.reset_battle_controls(mem)
        mem.pop('monster_unchosen', None)
        mem.pop('monster_own_choice', None)
    battle_flow.observe_entry_return(screen, mem)
    if kind != 'egg_choice_menu':
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
    recall = mem.get('recall') or {}
    if (recall and phase == 'concert' and recall.get('stage') != 'await_dispatch'
            and not recall.get('retreat_recheck') and not recall.get('anonymous_recheck')):
        # g1075 2026-10-29 13:02: the month rolled over while the camp recall
        # was choosing its destination. The month-start scene (「こんげつは……」,
        # phase 'concert') was handed to the recall, which waits on text, so
        # nothing was pressed for 91 observations until the recall gave up;
        # meanwhile ポセイドン (HP 1, no soldiers) marched back into リグア and
        # fell. The scene closes the picker anyway: drop the recall and let the
        # scene advance. The camp is found again on the map afterwards.
        mem.pop('recall', None)
        mem['uncertain'] = True
        decision_log._record(mem, 'camp_recall_interrupted', screen=kind, phase=phase,
                             observed_metric={'stage': recall.get('stage'), 'steps': recall.get('steps')},
                             reason='舞台の場面（月初など）が始まり帰還先の選択が閉じたため、帰還指示を中断して場面を進める')
        recall = {}
    recall_dialog = recall and phase != 'concert' and (hanjuku_camp_recheck.owns_dialog(screen, mem) or (
        not recall.get('retreat_recheck') and not recall.get('anonymous_recheck')
        and not mem.get('month_sub') and (kind in ('map', 'map_target', 'text') or
        (recall.get('stage') == 'await_dispatch' and kind in ('unknown', 'yes_no')))))
    # Cancel an outstanding repair before an emergency recall can move its
    # general; the old house route must not resume afterwards.
    if mem.get('house') and mem.get('recall'):
        hanjuku_house.step(screen, mem, frame)
    # A planned summon-preemption must not become a blind A on an unfinished
    # command menu, or be replaced by an old monthly/house transaction.
    battle_flow.note_duel_event(screen, mem)
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
        trade_guard_hold = (phase == 'dialogue' and actions == []
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
        actions = (recall_flow.camp_recall_step(screen, mem, frame) if recall_dialog
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
    elif kind == 'text' and month_flow.chikujou_leftover(screen, mem):
        actions = [pad('b')]
    elif kind=='text' and readers.is_camp_menu(screen):
        # g436 22:04: an A after closing the Y view landed on the hero's tent
        # and opened いどう/ステータス/キャンプ/きかん with no recall in flight;
        # nothing handled it and the bot pressed nothing for over an hour.
        decision_log._record(mem,'close_panel',screen='camp_menu',reason='帰還指示中でない野営メニューを閉じる')
        actions=[pad('b')]
    if actions is None:
        actions=legacy_actions(frame,phase,updated)
        draw_hold = battle_draw_hold_step(screen, mem, state.get('screen_kind'), actions)
        if draw_hold is not None:
            actions = draw_hold
        jump_hold = y_jump_unknown_hold_step(screen, mem, state.get('screen_kind'), actions)
        if jump_hold is not None:
            actions = jump_hold
        survey_hold = world_survey_unknown_hold_step(screen, mem, actions)
        if survey_hold is not None:
            actions = survey_hold
        if kind in {'unknown'} and phase not in {'transition','title','title_or_intro'} and mem.get('chapter'):
            if mem.get('held_phase')!=phase:
                mem['held_phase']=phase
                decision_log._record(mem,'situation_held',screen=kind,phase=phase,
                               reason='状況判定保留: 文字・カーソル・戦闘表示を読めない画面')
        else:
            mem['held_phase']=None
    # A concert picker that B cannot close must not consume the whole stall
    # window. Count unchanged observations that produced the same picker B;
    # after CONCERT_PICKER_REPEAT_MAX, choose A once and start over. A real
    # picker changes the frame on B, so its count never reaches the bound.
    digest=frame.digest()
    picker_watch=mem.get('concert_picker_watch') or {}
    if phase=='concert' and actions==[pad('b')]:
        repeat=(int(picker_watch.get('repeat') or 0)+1
                if picker_watch.get('digest')==digest else 1)
    else:
        repeat=0
    if repeat>=CONCERT_PICKER_REPEAT_MAX:
        decision_log._record(mem,'concert_picker_escape',screen=kind,phase=phase,
                       observed_metric={'unchanged_repeats':repeat,'frame_sha256':digest},
                       reason='曲選択の金色カーソル検出がBで変化しないまま繰り返すため、Aへ切り替える')
        actions=[pad('a')]
        repeat=0
    if repeat:
        mem['concert_picker_watch']={'digest':digest,'repeat':repeat}
    else:
        mem.pop('concert_picker_watch',None)
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
    hold_key=f'text:{screen.text}' if kind=='text' and screen.text else digest
    frozen=[d for d in (updated.get('no_input_frames') or []) if isinstance(d,str)][:2]
    if actions:
        streak=0
    elif hold_key in frozen:
        streak=int(updated.get('no_input_streak') or 0)+1
    else:
        streak=1
    bounded_wait=any(mem.get(k) for k in ('recall','house','month_sub','sortie_attempt','y_jump',
                                           'resume_egg_ritual'))
    hold_max=(FIELD_TEXT_HOLD_MAX if kind=='text' and phase=='field' and screen.text and not bounded_wait
              else NO_INPUT_HOLD_MAX)
    if (not actions and streak>hold_max and not trade_guard_hold and not bijin_guard_hold
            and not (kind == 'gift_request' and mem.get('bijin_gift_hold') is True)
            and not (sortie_cancel_hold or (kind == 'sortie_confirm'
                     and isinstance(mem.get('boss_stock_cancelled'), dict)))
            and not mem.get('_map_registration_hold')
            and not (kind == 'card_select' and isinstance(mem.get('boss_card_hold'), dict)
                     and mem['boss_card_hold'].get(mem.get('active')))
            and not isinstance(mem.get('resume_egg_ritual'), dict)
            and not isinstance(mem.get('orphan_battle_menu'), dict)
            and not (kind == 'egg_battle_menu'
                     and (battle_menus_flow._enemy_general_command(screen) is not None
                          or battle_menus_flow._partial_enemy_general_command(screen) is not None))
            and not month_flow.army_count_owned(mem)
            and month_flow.recruit_input_guard(screen, mem) is None):
        # Even this last-resort path must not confirm an unread summer option:
        # the cursor may be on the mass-dismissal row. Reobserve after one move.
        # These recognized events must never borrow a scripted scene's blind A.
        actions=([pad('up')] if kind=='summer_bonus'
                 else [pad('b')] if kind=='month_menu' or event_confirmation
                 else legacy_actions(frame,phase,updated))
        if kind == 'month_menu':
            # The legacy monthly A repeatedly opened the merchant from a
            # held background row. Cancel and re-read the menu instead; only
            # an observed target selection may confirm or spend money.
            mem.pop('month_nav', None)
            mem.pop('house_nav', None)
        if not actions:
            if phase=='field':
                if kind in {'unknown','map'}:
                    # A bare map the chart policy has no plan for: SELECT
                    # recentres the cursor on the hero (measured), which moves
                    # the screen and lets nav_step re-anchor. Mirrors the
                    # bookkeeping map_step.select_focus does after that jump.
                    actions=[pad('select')]
                    mem['nav_last']=None
                    mem['uncertain']=True
                    mem['select_focus_tick']=int(mem.get('tick') or 0)
                elif kind in {'map_target','world_map'}:
                    actions=[pad('b')]      # leave or cancel an open map view
                else:
                    actions=[pad('a')]      # a message over the field: acknowledge
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
    mem.pop('_adjusted',None)
    mem.pop('_interim',None)
    mem.pop('_recall_inputs',None)
    mem.pop('_card_scroll_input',None)
    updated['_records']=mem.pop('_records')
    updated['_experience']=mem.pop('_experience',None)
    updated['policy']=mem
    return actions,updated
