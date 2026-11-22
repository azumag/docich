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


def test_general_missing_from_the_fort_scrolls_then_closes_without_a_loss():
    screen, frame = real('g1190-ch9-riesling-fort-list.png')
    mem = memory(general='ミント', fort_stage='list')
    assert bp.step(screen, mem, frame) == [bp.pad('down')]
    assert mem['boss_pursuit']['fort_seeks'] == 1
    for _ in range(bp.FORT_LIST_SEEK_LIMIT - 1):
        assert bp.step(screen, mem, frame) == [bp.pad('down')]
    assert bp.step(screen, mem, frame) == [bp.pad('b')]
    assert mem['boss_pursuit']['outcome'] == 'general_not_in_fort'
    assert 'ミント' not in (mem.get('generals_lost') or [])


def _fort_list_screen(names, hand_y=39):
    from docich.hanjuku.os_layer.screen import TextLine
    lines = [_row(31, 64, 'しゅつげき'), _row(47, 64, 'こうかん')]
    for i, name in enumerate(names):
        lines.append(_row(39 + 16 * i, 144, name))
    return Screen(lines=lines, hand=(122, hand_y - 6, 140, hand_y + 7),
                  text='しゅつげき' + ''.join(names) + 'こうかん', kind='text')


def test_the_fort_list_scrolls_to_a_target_on_the_next_page():
    # Live g1288 2026-11-20 06:13: the 砦 showed four members in its window
    # (アポロン/マーマレード/ヘスティア/ナストリーニ) and カシス was not among
    # them. The fort can hold more than the window draws, so a missing target
    # must scroll before it is called absent.
    mem = memory(general='カシス', fort_stage='list')
    first = _fort_list_screen(['アポロン', 'マーマレード', 'ヘスティア', 'ナストリーニ'])
    assert bp.step(first, mem, None) == [bp.pad('down')]
    second = _fort_list_screen(['マーマレード', 'ヘスティア', 'ナストリーニ', 'カシス'],
                               hand_y=55)
    actions = bp.step(second, mem, None)
    assert actions != [bp.pad('b')]
    assert mem['boss_pursuit'].get('outcome') != 'general_not_in_fort'


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


def test_an_unreadable_roster_closes_and_retries_after_a_bound():
    # The global roster can fail the wage/hand checks for a frame or two; the
    # probe must not wait forever inside the owner guard.
    screen = Screen([], None, '', kind='text')
    mem = memory(general='ギー', phase='roster', reads=1, roster_top=True)
    for _ in range(bp.ROSTER_WAIT_LIMIT):
        assert bp.step(screen, mem, None) == []
    assert bp.step(screen, mem, None) == [bp.pad('b')]
    assert mem['boss_pursuit']['phase'] == 'close'
    assert any(r.get('resulting_event') == 'roster_unreadable' for r in mem['_records'])


def test_a_shop_phase_does_not_spend_the_pursuit_reads():
    # The monthly shop's text screens were ACTIONABLE, so a probe kept
    # reading through them and hit read_limit before the field returned
    # (live g1288 2026-11-20 12:2x: ギー read_limit during the shop).
    screen = Screen([], None, '', kind='text')
    mem = memory(general='ギー', phase='roster', reads=1)
    mem['shop'] = {'closed': False, 'key': '63-1'}
    assert bp.step(screen, mem, None) is None
    assert mem['boss_pursuit']['reads'] == 1


def test_a_closed_shop_kept_for_reuse_does_not_block_the_probe():
    # month end keeps the shop dict with closed=True so the purchase plan can
    # be reused; the field is back then, so the probe must keep reading.
    screen = Screen([], None, '', kind='map')
    mem = memory(general='ギー', phase='roster', reads=1)
    mem['shop'] = {'closed': True, 'key': '63-1'}
    bp.step(screen, mem, None)
    assert mem['boss_pursuit']['reads'] == 2


def test_two_missing_generals_drop_the_wave_record():
    # The X menu lacks しょうぐん for this general; once the absence repeats
    # the stale boss_wave_generals entry must not keep consuming pursuits
    # (live g1288 2026-11-20 13:0x, ギー/コリアンダー).
    screen = Screen([], None, '', kind='main_menu')
    mem = memory(general='ギー', phase='roster')
    assert bp.step(screen, mem, None) is not None
    mem['boss_pursuit']['phase'] = 'roster'
    assert bp.step(screen, mem, None) is not None
    assert 'ギー' not in mem[bp.LIST_KEY]
    assert any(r.get('decision') == 'boss_wave_general_missing'
               for r in mem.get('_records') or [])
