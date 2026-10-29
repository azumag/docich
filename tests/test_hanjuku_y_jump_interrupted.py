"""A Y jump cut by other screens is opened again, not walked (g1075 2026-10-30).

08:51:09 the retake I:084ce6:1 pressed Y for フヌイヌム; the month turnover
(08:51:12-08:54:31) and a defense of アルマムーン (08:54:35-08:55:12) followed.
Back on the target map at 08:55:13 the jump, which never showed the view, was
noted as a 0-move close, so no Y was allowed: the order walked from the cursor
estimate made before the month menu, read no roof under the marker twice and
was cancelled at 08:55:55.
"""
from docich.hanjuku.os_layer import navigation as nav
from docich.hanjuku_screen import Screen

ORDER = {'step': 'I:084ce6:1', 'general': 'セルリアク', 'source': 'アルマムーン', 'target': 'フヌイヌム',
         'cards': [], 'after': None, 'purpose': 'retake'}


def memory(tick):
    return {'chapter': 4, 'tick': tick, 'cursor': [520, 528],
            'y_jump': {'goal': 'フヌイヌム', 'mode': 'target', 'step': ORDER['step'],
                       'moves': 0, 'wait': 0, 'tick': 100}}


def target_map():
    return Screen([], None, '', kind='map_target')


def test_a_jump_cut_by_the_month_and_a_battle_opens_again():
    mem = memory(tick=260)                       # ~4 minutes of other screens
    assert nav._drop_stale_y_jump(target_map(), mem) is False
    assert 'y_jump' not in mem and 'y_jump_last' not in mem
    assert 'cursor' not in mem and mem['uncertain'] is True
    record = mem['_records'][-1]
    assert record['decision'] == 'y_jump_interrupted'
    assert record['observed_metric'] == {'moves': 0, 'waited': 160}
    assert nav._want_y_jump(mem, ORDER, 'フヌイヌム', 'target')


def test_a_view_that_closes_right_away_still_walks():
    mem = memory(tick=100 + nav.Y_JUMP_OPEN_GRACE)
    assert nav._drop_stale_y_jump(target_map(), mem) is False
    assert mem['y_jump_last'] == {'step': ORDER['step'], 'mode': 'target', 'moves': 0, 'why': 'view_closed'}
    assert mem['cursor'] == [520, 528]
    assert mem['_records'][-1]['decision'] == 'y_jump_failed'
    assert not nav._want_y_jump(mem, ORDER, 'フヌイヌム', 'target')


def test_a_jump_that_saw_the_view_is_not_an_interruption():
    mem = memory(tick=260)
    mem['y_jump']['seen'] = True
    nav._drop_stale_y_jump(target_map(), mem)
    assert mem['_records'][-1]['decision'] == 'y_jump_failed'
