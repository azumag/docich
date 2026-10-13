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


def pad(button, ms=100):
    return {'type':'pad','buttons':[button],'hold_ms':ms}


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
           recall_inputs: dict | None = None, run_identity: dict | None = None,
           chart_edition: str | None = None) -> tuple[list[dict], dict]:
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
    # State written before the castle-label rename still names chapter 1's home
    # castle ほんじょう. Rename it here, before anything reads or writes it, so
    # garrison keys, order sources and free-text notes all match the chart.
    hanjuku_chart.migrate_legacy_labels(state)
    phase=classify(frame)
    step=int(state.get('step',0))+1
    phase_step=int(state.get('phase_step',0))+1 if state.get('phase')==phase else 1
    mem=dict(state.get('policy') or {})
    identity = hanjuku_roster.run_identity(run_identity)
    mem['_run_identity'] = identity
    if identity is not None and mem.get('survey_run_identity') != identity:
        previous_identity = (hanjuku_roster.run_identity(mem.get('survey_run_identity'))
                             or hanjuku_roster.run_identity(state.get('decision_trace')))
        hanjuku_roster.invalidate(mem)
        if previous_identity != identity:
            for key in ('house', 'house_field_scan', 'house_scan_tick', 'house_scan_month',
                        'recruit_field_scan_attempts', 'recruit_month_scan_attempts', 'recruit_roster_attempts',
                        'house_eggs', 'egg_uses', 'egg_zero_owners', 'egg_types', 'egg_recheck', 'castle_income', 'castle_ownership',
                        'starter_party'):
                mem.pop(key, None)
        # A matching old trace may retain observed zero counts, but never
        # upgrades an old unbound roster into a complete reusable snapshot.
        mem['survey_run_identity'] = identity
    mem['_records']=[]
    mem['_adjusted']=adjusted
    mem['_interim']=interim
    mem['_recall_inputs']=recall_inputs
    mem['_experience']=experience if isinstance(experience, dict) else experience_module.empty()
    updated={**state,'phase':phase,'step':step,'phase_step':phase_step,'bot_version':BOT_VERSION}
    updated.pop('_records',None)
    updated.pop('_experience',None)
    screen=parse(frame,phase=phase)
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
        for key in ('_adjusted', '_interim', '_recall_inputs'):
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
        for key in ('_adjusted', '_interim', '_recall_inputs'):
            mem.pop(key, None)
        updated['_records'] = mem.pop('_records')
        updated['_experience'] = mem.pop('_experience', None)
        updated['policy'] = mem
        return starter_actions, updated
    from .flows import camp_recheck as hanjuku_camp_recheck
    hanjuku_camp_recheck.interrupt(screen, mem)
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
    battle_flow.observe_entry_return(screen, mem)
    if kind != 'egg_choice_menu':
        mem.pop('egg_choice', None)
    if kind != 'egg_battle_menu':
        mem.pop('egg_menu_stage',None)
        mem.pop('egg_battle_row_dead',None)
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
    recall_dialog = recall and (hanjuku_camp_recheck.owns_dialog(screen, mem) or (
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
    if actions is None:
        actions = deploy_flow.boss_stock_restart_step(screen, mem)
    if actions is None:
        actions = battle_menus_flow.orphan_battle_menu_step(screen, mem)
    if actions is None:
        actions = battle_menus_flow.defender_egg_pending_step(screen, mem)
    if actions is None:
        actions = battle_menus_flow.command_open_pending_step(screen, mem)
    if actions is None:
        actions = prompts_flow.trade_guard_step(screen, mem)
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
            mem={'_records':mem['_records'],'previous_stats':stats,'_experience':kept_experience}
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
        actions=shop_flow.discharge_step(screen,mem)
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
        if kind in {'unknown'} and phase not in {'transition','title','title_or_intro'} and mem.get('chapter'):
            if mem.get('held_phase')!=phase:
                mem['held_phase']=phase
                decision_log._record(mem,'situation_held',screen=kind,phase=phase,
                               reason='状況判定保留: 文字・カーソル・戦闘表示を読めない画面')
        else:
            mem['held_phase']=None
    # 保留の廃止: a screen that froze while we planned no input must not stay
    # that way. The streak counts only observations whose frame digest is one of
    # the two most recent distinct images -- the same evidence
    # hanjuku_run.screen_stalled treats as one unchanged screen -- so a fade, an
    # animation or marching units are never cut short, and a real policy input
    # starts the count over. Past NO_INPUT_HOLD_MAX the phase's own default
    # input is sent; the five phases whose reviewed default is deliberately
    # empty get the key a player would reach for instead of another wait.
    digest=frame.digest()
    frozen=[d for d in (updated.get('no_input_frames') or []) if isinstance(d,str)][:2]
    if actions:
        streak=0
    elif digest in frozen:
        streak=int(updated.get('no_input_streak') or 0)+1
    else:
        streak=1
    if (not actions and streak>NO_INPUT_HOLD_MAX
            and not isinstance(mem.get('resume_egg_ritual'), dict)
            and not isinstance(mem.get('orphan_battle_menu'), dict)
            and not month_flow.army_count_owned(mem)):
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
    updated['no_input_frames']=[digest]+[d for d in frozen if d!=digest][:1]
    updated['screen_kind']=kind
    mem.pop('_adjusted',None)
    mem.pop('_interim',None)
    mem.pop('_recall_inputs',None)
    updated['_records']=mem.pop('_records')
    updated['_experience']=mem.pop('_experience',None)
    updated['policy']=mem
    return actions,updated
