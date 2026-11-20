"""An absent general is not retried every minute.

Live g1288 2026-11-21 04:13-05:11: 50+ general_jump_failed in a row, every one
target_seen=false after a full 40-scroll roster walk — the target had left the
country, but the 200-tick skip re-armed the same jump every minute.
"""
from types import SimpleNamespace

from docich.hanjuku.flows import map as M


def memory():
    return {'chapter': 9, 'tick': 1000,
            'general_jump': {'general': 'キッシュ', 'phase': 'list', 'age': 5,
                             'total': 10, 'scrolls': M.GENERAL_JUMP_SCROLL_LIMIT,
                             'seen': ['エストラゴン', 'ギー', 'ココット']}}


def test_an_absent_target_gets_a_long_skip():
    mem = memory()
    screen = SimpleNamespace(kind='general_list', selected='キッシュ', text='',
                             options=[], lines=[], hand=None)
    M._end_general_jump(screen, mem, 'limit')
    skip = mem['general_jump_skip']
    assert skip['general'] == 'キッシュ'
    assert skip['absent'] is True
    assert skip['tick'] == 1000
