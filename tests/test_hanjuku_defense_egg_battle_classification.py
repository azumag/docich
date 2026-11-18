"""A defence summon battle's command box must route to the egg battle owner.

g1190 2026-11-18 04:55-07:25: the box もうこうげき / たまごをつかう / grey
きりふだ sat over the live monster panel. The pixel read set mixed_item_box,
which classify_text returned as battle_menu *before* the egg rules below could
run, so a generic A pressed the grey row for 2.5 h (#4528).
"""
from docich.hanjuku.os_layer.font import TextLine
from docich.hanjuku.os_layer.screen import EggRow, Screen, classify_text


def box(*, mixed=True, egg=True):
    rows = ((176, 'もうこうげき'), (192, 'たまごをつかう'))
    screen = Screen([], None, ''.join(label for _, label in rows))
    screen.menu_rows = [
        TextLine(y, tuple((176 + 8 * i, ch) for i, ch in enumerate(label)))
        for y, label in rows]
    screen.menu_cursor = 208
    screen.mixed_item_box = mixed
    screen.egg_rows = ([EggRow('ブラウニー', 5, 'ally', 180),
                        EggRow('テュポーン', 8, 'enemy', 200)] if egg else [])
    return screen


def test_defence_summon_box_reads_as_egg_battle_menu():
    assert classify_text(box()) == 'egg_battle_menu'


def test_kit_read_box_without_a_monster_stays_battle_menu():
    # #4456: the same foreground rows over no monster panel are the read-only
    # defence kit flow, which battle_menu_step owns. Do not steal that box.
    assert classify_text(box(egg=False)) == 'battle_menu'


def test_a_box_without_the_pixel_flag_keeps_the_existing_egg_rule():
    assert classify_text(box(mixed=False)) == 'egg_battle_menu'
