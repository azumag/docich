"""The boss pursuit leaves a 砦 by しゅつげき instead of waiting on a tent menu.

ch9 2026-11-17 (local driver, v986, seeded wave): the selected general
(リースリング) sat in a 砦 with カペリーニ. A on his unit opened
しゅつげき/こうかん, which the pursuit's unit_move never read (it wants
いどう/ステータス/キャンプ/きかん), so six pursuits each used 90 reads and ended
read_limit. Frames are the isolated probe of that state (256 wide):

- g1190-ch9-riesling-fort-menu.png    A on the 砦 unit: しゅつげき/こうかん
- g1190-ch9-riesling-fort-list.png    A on しゅつげき: カペリーニ / リースリング
- g1190-ch9-riesling-fort-card.png    A on the name: リースリング's status card
- g1190-ch9-riesling-fort-picker.png  the next A: the destination picker (map_target)
"""
from pathlib import Path

from docich.hanjuku.flows import boss_pursuit as bp
from docich.hanjuku.os_layer import phase
from docich.hanjuku.os_layer.pixels import read_png
from docich.hanjuku.os_layer.screen import parse
from docich.hanjuku_screen import Screen

FIXTURES = Path(__file__).resolve().parents[1] / 'tests' / 'fixtures' / 'hanjuku'


def real(name):
    frame = read_png(FIXTURES / name)
    return parse(frame, phase=phase.classify(frame)), frame


def memory(general='リースリング', **probe):
    entry = {'general': general, 'step': 'A', 'phase': 'unit_move',
             'reads': 10, 'seeks': 3, 'x_retries': 0}
    entry.update(probe)
    return {'chapter': 9, 'tick': 5000, bp.LIST_KEY: {general: {'step': 'A', 'launched': 3000}},
            'boss_pursuit': entry}


def test_fort_menu_picks_shutsugeki_and_opens_the_list():
    screen, frame = real('g1190-ch9-riesling-fort-menu.png')
    mem = memory()
    assert bp.step(screen, mem, frame) == [bp.pad('a')]
    assert mem['boss_pursuit']['fort_stage'] == 'list'
    assert mem['boss_pursuit']['phase'] == 'unit_move'


def test_fort_list_moves_the_hand_to_the_named_general():
    screen, frame = real('g1190-ch9-riesling-fort-list.png')
    mem = memory(fort_stage='list')
    actions = bp.step(screen, mem, frame)
    assert actions and actions != [bp.pad('a')]
    assert mem['boss_pursuit']['fort_stage'] == 'list'


def test_fort_list_selects_the_general_under_the_hand():
    screen, frame = real('g1190-ch9-riesling-fort-list.png')
    mem = memory(general='カペリーニ', fort_stage='list')
    assert bp.step(screen, mem, frame) == [bp.pad('a')]
    assert mem['boss_pursuit']['fort_stage'] == 'status'


def test_general_missing_from_the_fort_closes_without_a_loss():
    screen, frame = real('g1190-ch9-riesling-fort-list.png')
    mem = memory(general='ミント', fort_stage='list')
    assert bp.step(screen, mem, frame) == [bp.pad('b')]
    assert mem['boss_pursuit']['outcome'] == 'general_not_in_fort'
    assert 'ミント' not in (mem.get('generals_lost') or [])


def test_status_card_gets_one_a_then_waits():
    screen, frame = real('g1190-ch9-riesling-fort-card.png')
    mem = memory(fort_stage='status', fort_reads=0)
    assert bp.step(screen, mem, frame) == [bp.pad('a')]
    assert bp.step(screen, mem, frame) == []


def test_the_destination_picker_hands_over_to_dest():
    screen, frame = real('g1190-ch9-riesling-fort-picker.png')
    assert screen.kind == 'map_target'
    mem = memory(fort_stage='status')
    bp.step(screen, mem, frame)
    assert mem['boss_pursuit']['phase'] == 'dest'


def test_a_bare_map_never_spends_the_whole_read_budget_in_unit_move():
    mem = memory()
    bare = Screen([], None, '', kind='map')
    for _ in range(bp.UNIT_MENU_WAIT):
        assert bp.step(bare, mem, None) == []
    assert bp.step(bare, mem, None) == [bp.pad('b')]
    assert mem['boss_pursuit']['outcome'] == 'unit_menu_unreadable'
    assert mem['boss_pursuit']['reads'] < bp.PURSUIT_READ_LIMIT


def test_a_fort_closed_back_to_the_map_closes_the_probe():
    mem = memory(fort_stage='list')
    bare = Screen([], None, '', kind='map')
    for _ in range(bp.UNIT_MENU_WAIT):
        bp.step(bare, mem, None)
    assert bp.step(bare, mem, None) == [bp.pad('b')]
    assert mem['boss_pursuit']['outcome'] == 'fort_menu_closed'


def _row(y, x, word):
    from docich.hanjuku.os_layer.screen import TextLine
    return TextLine(y, tuple((x + 8 * i, ch) for i, ch in enumerate(word)))


def test_a_fort_menu_drawn_right_of_x100_is_not_read_as_its_list():
    # Live g1288 2026-11-20 05:46: the 砦 menu sat around x 93..133, so the
    # old hand-x>100 test called it a list. _fort_step then read the menu's
    # own しゅつげき/こうかん as the members and closed general_not_in_fort
    # without ever opening the list.
    from docich.hanjuku.os_layer.readers import fort_list_open, is_fort_menu
    screen = Screen(lines=[_row(31, 104, 'しゅつげき'), _row(47, 104, 'こうかん')],
                    hand=(96, 25, 116, 38), text='しゅつげきこうかん', kind='text')
    assert is_fort_menu(screen)
    assert fort_list_open(screen) is False


def test_the_list_beside_a_shifted_menu_is_still_open():
    from docich.hanjuku.os_layer.readers import fort_list_open
    screen = Screen(lines=[_row(31, 104, 'しゅつげき'), _row(47, 104, 'こうかん'),
                           _row(39, 144, 'カシス'), _row(55, 144, 'ピオーネ')],
                    hand=(136, 33, 154, 46),
                    text='しゅつげきカシスこうかんピオーネ', kind='text')
    assert fort_list_open(screen) is True
