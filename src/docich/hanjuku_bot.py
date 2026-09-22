"""Deterministic Hanjuku screen policy. No model, provider or network calls.

Screen features are measured in the native 256x224 SNES coordinate system.
The rules deliberately remain separate from terminal evidence and input I/O.
"""
from __future__ import annotations

from .hanjuku_pixels import Frame

BOT_VERSION = 'hanjuku-script-v1'


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
    # The red-curtain concert offers an optional looping music picker. B
    # closes it; repeatedly confirming track 00 would never resume the game.
    curtain=lambda r,g,b:r>140 and r>g*3 and r>b*3
    if (f.fraction((0,0,32,128),curtain)>.25
            and f.fraction((224,0,256,128),curtain)>.25
            and f.fraction((18,150,236,207),green)>.5):
        return 'concert'
    # Castle dialogue is a dark green full-width panel at the bottom.
    if (f.fraction((132,40,225,126),green)>.6
            and f.fraction((18,176,230,206),green)>.5):
        return 'shop'
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


def decide(frame: Frame, state: dict) -> tuple[list[dict], dict]:
    """Return bounded pad actions and new policy memory; never write or send."""
    phase=classify(frame)
    step=int(state.get('step',0))+1
    phase_step=int(state.get('phase_step',0))+1 if state.get('phase')==phase else 1
    updated={**state,'phase':phase,'step':step,'phase_step':phase_step,'bot_version':BOT_VERSION}
    if phase=='transition':
        actions=[]
    elif phase=='name':
        actions=[pad('a')] if phase_step==1 else [pad('start')]
    elif phase=='month_menu':
        actions=[pad('b' if phase_step==1 else 'a')]
    elif phase=='concert':
        gold=lambda r,g,b:r>150 and 80<g<180 and 30<b<120
        picker=any(frame.fraction((200,y,239,y+1),gold)>.8 for y in range(178,196))
        actions=[pad('b' if picker else 'a')]
    elif phase=='shop':
        actions=[pad('b')]
    elif phase in {'dialogue','field_menu','battle_intro','battle'}:
        if phase=='field_menu' and state.get('deployment_opened'):
            updated['deployment_menu_seen']=True
        actions=[pad('a')]
    elif phase=='title':
        actions=[pad('start')]
    elif phase=='title_or_intro':
        # Start can pause cutscenes. Only the verified title and name screen
        # may receive it; dark in-game scenes advance with confirm.
        actions=[pad('a')]
    elif phase=='field':
        # Sweep from the starting castle toward the first enemy holdings.
        # Confirm at each waypoint, allowing the game's real-time troops to
        # travel between observations. Menu recognition supersedes this path.
        route=(('left',1800),('right',300),('up',700),('a',100))
        cursor=int(state.get('field_step',0))
        if not state.get('deployment_opened'):
            updated['deployment_opened']=True
            return [pad('a')],updated
        if not state.get('deployment_menu_seen'):
            return [pad('a')],updated
        if cursor<len(route):
            button,duration=route[cursor]
            updated['field_step']=cursor+1
            actions=[pad(button,duration)]
        else:
            # The real-time unit follows its assigned target. Further confirm
            # would open castle statistics and stop the march, so wait until
            # an observed battle prompt. Stasis is owned by the run monitor.
            actions=[]
    else:
        # Scripted scenes and battle prompts use confirm. No reset, emulator
        # shortcuts, arbitrary keys, or LLM-produced actions are permitted.
        actions=[pad('a')]
    return actions,updated
