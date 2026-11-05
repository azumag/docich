"""Source-castle jumps use the X menu's しろ list, not the Y world map.

Owner instruction 2026-11-06: while planning a sortie from castle A to B, do
not fly to A through the Y world map. Open X, choose しろ, choose the castle
name and press SELECT (manual p.15: 将軍と同じく、セレクトボタンでその城へ飛べる).
The measured list is the right-hand panel of the main menu: names at x=168 on
16 px rows from y=39 with the orange hand, and the help line
"Aボタンでステータスひょうじ Bボタンでコマンドにもどりますじゃ!".
"""

from docich.hanjuku.flows import map as M
from docich.hanjuku.os_layer.font import TextLine
from docich.hanjuku.os_layer.screen import Screen


def _line(y, x, text):
    return TextLine(y, tuple((x + 8 * i, ch) for i, ch in enumerate(text)))


def _screen(kind, rows, *, hand=None, text='', cursor=None):
    lines = [_line(y, x, word) for y, x, word in rows]
    return Screen(lines=lines, hand=hand, text=text or ' '.join(w for _, _, w in rows),
                  kind=kind, cursor=cursor)


def _main_menu(hand_y=39):
    rows = [(39, 168, 'ステータス'), (55, 168, 'しょうぐん'), (71, 168, 'しろ'),
            (87, 168, 'きりふだ'), (103, 168, 'システム')]
    return _screen('main_menu', rows, hand=(146, hand_y - 6, 166, hand_y + 7),
                   text='ステータス しょうぐん しろ きりふだ システム')


def _castle_list(names, hand_y=39):
    rows = [(y, 168, name) for y, name in zip(range(39, 39 + 16 * len(names), 16), names)]
    rows += [(183, 24, 'Aボタンでステータスひょうじ'),
             (199, 24, 'コマンドにもどりますじゃ!')]
    text = 'Aボタンでステータスひょうじ Bボタンで コマンドにもどりますじゃ!'
    return _screen('text', rows, hand=(146, hand_y - 6, 166, hand_y + 7), text=text)


def _order(source='アルマムーン', step='J1'):
    return {'step': step, 'source': source, 'target': 'ナキューメラ',
            'cards': [], 'note': 'test', 'after': ()}


def _memory(order=None):
    order = order or _order()
    return {'chapter': 1, 'active': order['step'], 'tick': 10, '_records': [],
            'orders': {order['step']: 'pending'},
            'chart_plan': {'orders': [order]}, 'cursor': [400, 400]}


def test_list_reads_source_name_across_the_long_vowel_mark():
    screen = _castle_list(['アルマームン', 'ナキューメラ'])
    assert M._castle_list_rows(screen)
    assert M._castle_list_target(screen, 'アルマムーン') == 'アルマームン'


def test_list_is_not_a_general_roster():
    screen = _castle_list(['ゼウス', 'ヴィーナス'])
    assert M._castle_list_target(screen, 'アルマムーン') is None


def test_source_far_away_starts_the_x_menu():
    mem = _memory()
    order = mem['chart_plan']['orders'][0]
    goal = M.chart.castles(1)[order['source']]
    assert M._want_castle_jump(mem, order, order['source'], goal)
    actions = M._start_castle_jump(mem, order, order['source'])
    assert actions == [M.pad('x')]
    assert mem['castle_jump']['phase'] == 'menu'


def test_main_menu_moves_to_shiro_and_confirms_it():
    mem = _memory()
    mem['castle_jump'] = {'step': 'J1', 'source': 'アルマムーン', 'phase': 'menu',
                          'age': 0, 'total': 0, 'tick': 0, 'menu_presses': 1}
    # hand on ステータス: one Down reaches しょうぐん, then しろ
    screen = _main_menu(hand_y=39)
    assert M.castle_jump_step(screen, mem, None) == [M.pad('down')]
    screen = _main_menu(hand_y=71)
    assert M.castle_jump_step(screen, mem, None) == [M.pad('a')]
    assert mem['castle_jump']['phase'] == 'list'


def test_castle_row_sends_select():
    mem = _memory()
    mem['castle_jump'] = {'step': 'J1', 'source': 'アルマムーン', 'phase': 'list',
                          'age': 0, 'total': 0, 'tick': 0, 'menu_presses': 2}
    screen = _castle_list(['アルマームン', 'ナキューメラ'])
    assert M.castle_jump_step(screen, mem, None) == [M.pad('select')]
    assert mem['castle_jump']['phase'] == 'confirm'


def test_map_after_select_opens_the_source_castle():
    mem = _memory()
    mem['castle_jump'] = {'step': 'J1', 'source': 'アルマムーン', 'phase': 'confirm',
                          'age': 0, 'total': 0, 'tick': 0}
    screen = _screen('map', [], cursor=(80, 90))
    assert M.castle_jump_step(screen, mem, None) == [M.pad('a')]
    assert 'castle_jump' not in mem
    assert mem['expect_menu'] is True


def test_map_after_select_opens_castle_when_roof_is_occluded(monkeypatch):
    # A live frame can hide the roof underneath the cursor; the previous
    # implementation waited three times and cancelled instead of entering.
    mem = _memory()
    mem['castle_jump'] = {'step': 'J1', 'source': 'アルマムーン', 'phase': 'confirm',
                          'age': 0, 'total': 0, 'tick': 0}
    screen = _screen('map', [], cursor=(80, 90))
    monkeypatch.setattr(M, '_roof_under_cursor', lambda _screen, _frame: False)
    assert M.castle_jump_step(screen, mem, object()) == [M.pad('a')]
    assert 'castle_jump' not in mem
    assert mem['expect_menu'] is True
    assert 'castle_jump_skip' not in mem


def test_status_opened_by_select_backs_out_once():
    mem = _memory()
    mem['castle_jump'] = {'step': 'J1', 'source': 'アルマムーン', 'phase': 'confirm',
                          'age': 0, 'total': 0, 'tick': 0}
    screen = _screen('castle_info', [])
    assert M.castle_jump_step(screen, mem, None) == [M.pad('b')]
    assert mem['castle_jump']['phase'] == 'list'


def test_missing_castle_scrolls_then_gives_up():
    mem = _memory()
    mem['castle_jump'] = {'step': 'J1', 'source': 'アルマムーン', 'phase': 'list',
                          'age': 0, 'total': 0, 'tick': 0, 'menu_presses': 2,
                          'scrolls': M.CASTLE_JUMP_SCROLL_LIMIT}
    screen = _castle_list(['ナキューメラ'])
    actions = M.castle_jump_step(screen, mem, None)
    assert actions == [M.pad('b')]
    assert 'castle_jump' not in mem
    assert mem['castle_jump_skip']['step'] == 'J1'
