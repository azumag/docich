"""Regression for front refill ordering with newer partial roster observations."""

from docich.hanjuku.strategy.corridor import FRONT_REFILL_MIN, _front_names, _front_thin


def test_front_thin_uses_newer_partial_roster():
    mem = {'world': {
        'garrison': {'タオ': ['将軍A']},
        'garrison_tick': {'タオ': 4900},
        'garrison_partial': {'タオ': ['将軍A', '将軍B', '将軍C']},
        'garrison_partial_tick': {'タオ': 4990},
    }, 'tick': 5000}
    assert len(_front_names(mem, 'タオ')) >= FRONT_REFILL_MIN
    assert not _front_thin(mem, 'タオ')


def test_front_thin_uses_newer_complete_roster():
    mem = {'world': {
        'garrison': {'タオ': ['将軍A']},
        'garrison_tick': {'タオ': 4990},
        'garrison_partial': {'タオ': ['将軍A', '将軍B', '将軍C']},
        'garrison_partial_tick': {'タオ': 4900},
    }, 'tick': 5000}
    assert _front_thin(mem, 'タオ')


def test_front_thin_unread_is_not_thin():
    assert not _front_thin({'world': {}}, 'タオ')
