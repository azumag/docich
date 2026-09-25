"""Deterministic Hanjuku screen policy. No model, provider or network calls.

Screen features are measured in the native 256x224 SNES coordinate system.
The rules deliberately remain separate from terminal evidence and input I/O.
"""
from __future__ import annotations

from .hanjuku_pixels import Frame

BOT_VERSION = 'hanjuku-chart-v2'


# Native title copyright rows, measured from the owner's ROM. A strict match
# keeps dark cutscenes and the animated attract demo from ending a run.
_TITLE_ROWS = tuple(int(row, 16) for row in (
    '03c7c3e2000fe00000000307c7c7c0', '0420422fc00020000000070c6c6c60',
    '09904228400120000000030c6c6c60', '0a10842047c1400000000307e7e0c0',
    '099180404101000000000300606300', '04224040810100000000030c6c6600',
    '03c4218707c2000000000787c7cfe0', '000000000000000000000000000000',
    '03c082108080070001000307c7c7c0', '042fefa4e7e780044fc0070c6c6c60',
    '099081031080c7842200030c6c6c60', '0a1387c617e1084424000307e7e0c0',
    '099480816080004427800300606300', '0423818103c000440840030c6c6600',
    '03c0880084a8008480400787c7cfe0', '000107808307c30303800000000000',
))

# The merchant's specific "これでもうみせじまいしますが" prompt. The price
# list remains visible behind this confirmation, so B would reopen shopping.
# Match its text, not a generic yes/no panel which could confirm a purchase.
_SHOP_EXIT_ROWS = tuple(int(row, 16) for row in (
    '00000a000000000a00000000000a0000', '00000a000000000a00000000000a0000',
    '00000000000000000000000000000000', '00200040707844400800400808200000',
    '7820fe40001044407e44407efe240000', '0cec08f07810fe400842400808f20000',
    '10321040841444407e42407e382a0000', '002220f0047c44400842400848480000',
    '0064204004a648403c40403c38480000', '80a4204408a440444a48444a08a80000',
    '7c221c3830483e383030383010100000',
))


def shop_exit_confirmation(frame: Frame) -> bool:
    errors = 0
    for y, expected in enumerate(_SHOP_EXIT_ROWS, 180):
        actual = 0
        for x in range(24, 152):
            actual = (actual << 1) | int(min(frame.pixel(x, y)) > 180)
        errors += (actual ^ expected).bit_count()
    return errors < 20


def is_title(frame: Frame) -> bool:
    errors = 0
    for y, expected in enumerate(_TITLE_ROWS, 192):
        actual = 0
        for x in range(68, 188):
            actual = (actual << 1) | int(min(frame.pixel(x, y)) > 180)
        errors += (actual ^ expected).bit_count()
    return errors < 40


def green(r,g,b):
    return 8 <= r <= 40 and 48 <= g <= 100 and 40 <= b <= 95 and g > r*1.5


def classify(frame: Frame) -> str:
    f=frame
    if is_title(f):
        return 'title'
    dark=f.fraction((0,0,256,224),lambda r,g,b:max(r,g,b)<25)
    if dark > .97:
        return 'transition'
    paper=lambda r,g,b:r>185 and g>185 and b>155
    if (f.fraction((16,174,113,191),paper)>.65
            and f.fraction((146,174,234,191),paper)>.55):
        return 'battle'
    # Name grid and two independent bordered top panels: never type endlessly.
    if (f.fraction((80,85,235,210),green)>.55
            and f.fraction((24,18,176,30),green)>.4
            and f.fraction((80,48,140,65),green)>.3
            and f.fraction((0,80,8,216),lambda r,g,b:max(r,g,b)<25)>.85):
        return 'name'
    # Merchants share the red-curtain backdrop with the concert, but have a
    # tall price list at the right. Check that specific panel first.
    if (f.fraction((132,40,225,126),green)>.6
            and f.fraction((18,176,230,206),green)>.5):
        return 'shop'
    # The red-curtain concert offers an optional looping music picker. B
    # closes it; repeatedly confirming track 00 would never resume the game.
    curtain=lambda r,g,b:r>140 and r>g*3 and r>b*3
    if (f.fraction((0,0,32,128),curtain)>.25
            and f.fraction((224,0,256,128),curtain)>.25
            and f.fraction((18,150,236,207),green)>.5):
        return 'concert'
    # Castle dialogue is a dark green full-width panel at the bottom.
    if (f.fraction((48,40,210,104),green)>.65
            and f.fraction((18,176,230,206),green)>.5):
        return 'month_menu'
    if f.fraction((18,150,236,207),green)>.60:
        return 'dialogue'
    # Map menus are translucent; their stable red border identifies them.
    border=lambda r,g,b:r>235 and 35<g<80 and 35<b<85
    if any(f.fraction((35,y,110,y+1),border)>.85 for y in (16,17)):
        return 'field_menu'
    # Outdoor maps are grass/forest/water, unlike the plain battle arena.
    grass=f.fraction((0,0,256,135),lambda r,g,b:g>70 and g>r*1.35 and g>b*1.3)
    water=f.fraction((0,135,256,224),lambda r,g,b:b>100 and b>r*1.7)
    if grass>.22 or water>.25:
        # Encounter/result prompts use a green, white or blue bright border.
        # Both edges are required; animated water/grass is not a prompt.
        bright=lambda r,g,b:max(r,g,b)>220 and r>60
        if (any(f.fraction((20,y,152,y+1),bright)>.8 for y in (16,17))
                and any(f.fraction((x,20,x+1,70),bright)>.8 for x in (16,17))):
            return 'battle_intro'
        panel=f.fraction((35,22,222,128),green)
        return 'field_menu' if panel>.015 else 'field'
    if dark>.55:
        return 'title_or_intro'
    return 'event'


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
        # Battles are fought by the game's own melee; A in a battle has no
        # measured benefit and A on the map would open a sortie menu.
        return []
    # Scripted scenes, battle prompts and dark cutscenes use confirm. No
    # reset, emulator shortcuts, arbitrary keys or LLM-produced actions.
    return [pad('a')]


def decide(frame: Frame, state: dict, *, adjusted: dict | None = None,
           interim: dict | None = None, experience: dict | None = None) -> tuple[list[dict], dict]:
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
    from . import hanjuku_experience as experience_module
    from . import hanjuku_policy as policy
    from .hanjuku_screen import parse
    phase=classify(frame)
    step=int(state.get('step',0))+1
    phase_step=int(state.get('phase_step',0))+1 if state.get('phase')==phase else 1
    mem=dict(state.get('policy') or {})
    mem['_records']=[]
    mem['_adjusted']=adjusted
    mem['_interim']=interim
    mem['_experience']=experience if isinstance(experience, dict) else experience_module.empty()
    updated={**state,'phase':phase,'step':step,'phase_step':phase_step,'bot_version':BOT_VERSION}
    updated.pop('_records',None)
    updated.pop('_experience',None)
    screen=parse(frame,phase=phase)
    policy.observe_events(screen,mem)
    kind=screen.kind
    map_kinds={'map','map_target','castle_menu','general_list','card_select','sortie_confirm'}
    if kind not in map_kinds and mem.get('cursor'):
        # Battles, events and month menus can move the map cursor.
        mem['uncertain']=True
    if kind in {'castle_menu','general_list'}:
        mem.pop('expect_menu',None)
        mem.pop('menu_miss',None)   # a real menu proves the cell was correct
    flow=(mem.get('battle') or {}).get('card_flow')
    card_list=bool(flow) and kind in {'text','unknown'} and any(
        w in policy.CARD_NAMES for line in screen.lines for _,w in line.spans())
    # Egg/card announcements and fades inside a battle are not its end; only
    # a return to the map or a following event/menu closes the record.
    after_battle = kind in policy.AFTER_BATTLE_KINDS or kind == 'barrier_removed'
    if mem.get('battle') and after_battle:
        policy.battle_end(mem,kind)
    if after_battle:
        mem['egg_battle']=False
        for key in ('egg_action','egg_key','egg_menu_stage','indep_menu',
                    'indep_menu_key','indep_menu_action',
                    'monster_menu_key','monster_menu_cursor','monster_menu_hold',
                    'monster_menu_choice','monster_menu_choice_key','monster_panel'):
            mem.pop(key,None)
    if kind != 'egg_battle_menu':
        mem.pop('egg_menu_stage',None)
    if kind != 'monster_menu':
        for key in ('monster_menu_key','monster_menu_cursor','monster_menu_hold',
                    'monster_menu_choice','monster_menu_choice_key','monster_panel'):
            mem.pop(key,None)
    if kind != 'battle_menu':
        for key in ('indep_menu','indep_menu_key','indep_menu_action'):
            mem.pop(key,None)
    actions=None
    if phase=='name' and kind!='name_entry':
        policy._record(mem,'name_wait',chart_step='name',
                       reason='状況判定保留: 名前入力画面の文字を読めないため入力を保留')
        actions=[]
    elif kind=='name_entry':
        if (mem.get('name') or {}).get('done'):
            # A second name screen means a new game: never carry the previous
            # game's orders, captures or cursor into it.
            stats=mem.get('stats')
            kept_experience=mem.get('_experience')
            mem={'_records':mem['_records'],'previous_stats':stats,'_experience':kept_experience}
            policy._record(mem,'new_game_detected',chart_step='name',
                           reason='名前入力画面を再度確認したため方策状態を初期化')
        actions=policy.name_step(screen,mem)
        if mem.get('name',{}).get('done') and not mem.get('chapter'):
            mem['chapter']=1
            mem['variant']='chart' if policy.chart.orders(1) else 'chart_unavailable'
            home=policy.chart.home_castle(1)
            castles=policy.chart.castles(1)
            if home in castles:
                mem['cursor']=list(castles[home])
    elif card_list:
        actions=policy.card_list_step(screen,mem)
    elif kind=='battle':
        actions=policy.battle_step(screen,mem)
    elif kind=='monster_menu':
        actions=policy.monster_menu_step(screen,mem)
    elif kind=='egg_battle_menu' or (mem.get('egg_battle') and kind=='text'):
        actions=policy.egg_battle_step(screen,mem)
    elif kind=='battle_menu':
        actions=policy.battle_menu_step(screen,mem)
    elif kind in {'attack_started','defense_started','boss_attack_started'}:
        actions=policy.message_step(screen,mem)
    elif kind=='barrier_removed':
        if mem.get('chapter') == 1:
            policy._record(mem,'barrier_removed',chart_step='1-barrier',
                           observed_metric={'message':screen.text},
                           resulting_event='barrier_removed',
                           reason='実測済みのいばら消滅確認文を読んだためAで閉じる')
            actions=[pad('a')]
        else:
            policy._record(mem,'situation_held',screen=kind,
                           reason='いばら消滅表示を確認したが章を確定できないため保留')
            actions=[]
    elif kind=='month_menu':
        actions=policy.month_step(screen,mem)
    elif kind in {'shop_list','shop_quantity_prompt','shop_quantity','shop_exit_confirm'}:
        actions=policy.shop_step(screen,mem)
    elif kind in {'castle_menu','general_list','card_select','sortie_confirm'} and mem.get('chapter'):
        actions=policy.deploy_step(screen,mem)
    elif kind=='map_target' and mem.get('chapter'):
        actions=policy.target_step(screen,mem,frame)
    elif kind=='map' and mem.get('chapter'):
        actions=policy.map_step(screen,mem,frame)
    elif kind=='gift_request':
        actions=policy.gift_step(screen,mem)
    elif kind=='yes_no':
        actions=policy.yes_no_step(screen,mem)
    elif kind in {'castle_info','sealed_castle','main_menu'}:
        policy._record(mem,'close_panel',screen=kind,reason='意図しない情報画面を閉じる')
        actions=[pad('b')]
    if actions is None:
        actions=legacy_actions(frame,phase,updated)
        if kind in {'unknown'} and phase not in {'transition','title','title_or_intro'} and mem.get('chapter'):
            if mem.get('held_phase')!=phase:
                mem['held_phase']=phase
                policy._record(mem,'situation_held',screen=kind,phase=phase,
                               reason='状況判定保留: 文字・カーソル・戦闘表示を読めない画面')
        else:
            mem['held_phase']=None
    updated['screen_kind']=kind
    mem.pop('_adjusted',None)
    mem.pop('_interim',None)
    updated['_records']=mem.pop('_records')
    updated['_experience']=mem.pop('_experience',None)
    updated['policy']=mem
    return actions,updated
