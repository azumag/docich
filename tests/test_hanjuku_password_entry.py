"""The chapter-clear password screen is recognised and held, never B-spammed.

g1190 2026-11-18 62-2: after the chapter-11 boss the screen 「パスワードヲ
ジュンバンニ イレテクダサイ。」 sat while the shop phase pressed B for twenty
minutes (static frame → every observation held or B'd).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from docich.hanjuku.os_layer.screen import Screen, classify_text

TEXT = 'パスワードヲジュンバンニイレテクダサイ。かんじゅくわれらがははいだいなる'


def test_the_password_screen_has_its_own_kind():
    screen = Screen([], None, TEXT, kind='text')
    assert classify_text(screen) == 'password_entry'


def test_another_text_is_not_the_password_screen():
    screen = Screen([], None, 'パスワードのニュウリョクガヒツヨウデス', kind='text')
    assert classify_text(screen) != 'password_entry'


ROWS = [(23, 'われらが'), (39, 'せいめいたいの'), (55, 'エッグママの'),
        (71, 'きゅうでん'), (87, 'われを'), (103, 'オヴァリウムよ'),
        (119, 'みちびけ')]


def _screen(rows, hand_y):
    from docich.hanjuku.flows import password
    screen = Screen([], (146, hand_y - 4, 166, hand_y + 8), '', kind='password_entry')
    screen.password_list = list(rows)
    return screen


def test_dynamic_confirms_the_phrase_word_under_the_hand():
    from docich.hanjuku.flows import password
    mem = {}
    screen = _screen(ROWS, 23)          # hand on われらが = phrase[0]
    assert password.password_step(screen, mem) == [password.pad('a', 30)]
    assert mem['password_entry']['dyn_step'] == 1


def test_dynamic_moves_toward_a_visible_phrase_word():
    from docich.hanjuku.flows import password
    mem = {}
    screen = _screen(ROWS, 71)          # きゅうでん, phrase[0] われらが is above
    assert password.password_step(screen, mem) == [password.pad('up')]


def test_dynamic_scroll_progress_does_not_look_stuck_when_rows_change():
    from docich.hanjuku.flows import password
    mem = {}
    views = [
        [(23, 'かんじゅく'), (39, 'せいめいたいの'), (55, 'いだいなる'),
         (71, 'はは'), (87, 'エッグママの'), (103, 'きゅうでん'), (119, 'われを')],
        [(23, 'せいめいたいの'), (39, 'いだいなる'), (55, 'はは'),
         (71, 'エッグママの'), (87, 'きゅうでん'), (103, 'われを'), (119, 'みちびけ')],
        [(23, 'いだいなる'), (39, 'はは'), (55, 'エッグママの'),
         (71, 'きゅうでん'), (87, 'われを'), (103, 'みちびけ'), (119, 'オヴァリウムよ')],
    ]
    for rows in views:
        assert password.password_step(_screen(rows, 23), mem) == [password.pad('down')]
        assert mem['password_entry']['dyn_search']['stuck'] == 0


def test_dynamic_finishes_a_partial_attempt_in_phrase_order():
    from docich.hanjuku.flows import password
    mem = {}
    screen = _screen(ROWS, 23)
    # われらが was already consumed by the earlier fixed-index attempt;
    # the next remaining phrase word is かんじゅく, even though it is offscreen.
    screen.text = (
        'パスワードヲジュンバンニイレテクダサイ。'
        'かんじゅくせいめいたいのいだいなるははエッグママの'
        'きゅうでんオヴァリウムよわれをみちびけ'
    )
    assert password.password_step(screen, mem) == [password.pad('down')]


def test_dynamic_scrolls_when_the_word_is_off_screen():
    from docich.hanjuku.flows import password
    mem = {'password_entry': {'dyn_step': 1}}   # want かんじゅく, not visible
    screen = _screen(ROWS, 23)
    assert password.password_step(screen, mem) == [password.pad('down')]
