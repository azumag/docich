"""OS層: frame-level phase classification (title, battle, menus, field, ...).

Screen features are measured in the native 256x224 SNES coordinate system.
``classify`` looks only at pixels; text and cursors are parsed afterwards by
``docich.hanjuku_screen.parse`` for the phase it returns.
"""
from __future__ import annotations

from ...hanjuku_pixels import Frame


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


def is_squaresoft_splash(frame: Frame) -> bool:
    """Recognize the centered SQUARESOFT logo on its plain gray startup card.

    The old lower-screen paper heuristic also matched this non-game screen.
    Requiring the neutral field, dark wordmark, and its red accent keeps this
    exception specific to the observed splash instead of changing battle or
    generic-event classification.
    """
    neutral = lambda r,g,b: max(r,g,b) - min(r,g,b) <= 2 and min(r,g,b) > 170
    wordmark = lambda r,g,b: max(r,g,b) < 100
    red_accent = lambda r,g,b: r > 180 and g < 100 and b < 80
    if frame.fraction((108,108,124,120), red_accent) <= .10:
        return False
    if frame.fraction((76,102,180,120), wordmark) <= .20:
        return False
    return frame.fraction((0,0,256,224), neutral) > .985


def classify(frame: Frame) -> str:
    f=frame
    if is_title(f):
        return 'title'
    dark=f.fraction((0,0,256,224),lambda r,g,b:max(r,g,b)<25)
    if dark > .97:
        return 'transition'
    if is_squaresoft_splash(f):
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
    curtain=lambda r,g,b:r>140 and r>g*3 and r>b*1.5
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
