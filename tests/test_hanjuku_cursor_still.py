"""A frozen position estimate requests the castle-list re-anchor.

g1133 2026-11-05: `cursor` stayed [392,144] with `nav_last` null and `uncertain`
true for hours, so no order could be driven. The code already has an inland
search that moves to where roofs are visible, but it was only started from the
arrive-but-no-roof path, so a frozen estimate never reached it.
"""
from docich.hanjuku.flows import map as M


def memory(cursor=(392, 144)):
    return {'chapter': 6, 'tick': 100, '_records': [], 'cursor': list(cursor),
            'active': 'navigation-order'}


def test_a_moving_estimate_never_starts_the_search():
    mem = memory()
    for i in range(M.CURSOR_STILL_LIMIT * 2):
        mem['cursor'] = [i, 0]
        assert M._watch_cursor_still(mem) is False


def test_a_frozen_estimate_starts_the_search_once():
    mem = memory()
    for _ in range(M.CURSOR_STILL_LIMIT - 1):
        assert M._watch_cursor_still(mem) is False
    assert M._watch_cursor_still(mem) is True
    assert mem['nav_search'] is True
    assert mem['_records'][-1]['decision'] == 'cursor_still_search'
    mem['cursor'] = [1, 1]
    assert M._watch_cursor_still(mem) is False


def test_a_confirmed_anchor_is_not_mistaken_for_a_lost_cursor():
    mem = memory()
    mem['anchor'] = 'アルマムーン'
    mem['uncertain'] = False
    for _ in range(M.CURSOR_STILL_LIMIT + 2):
        assert M._watch_cursor_still(mem) is False
    assert 'nav_search' not in mem


def test_idle_map_does_not_treat_waiting_as_frozen_navigation():
    mem = memory()
    mem.update(active=None, uncertain=True, anchor=None,
               cursor_still={'cell': list(mem['cursor']), 'n': M.CURSOR_STILL_LIMIT})
    for _ in range(M.CURSOR_STILL_LIMIT * 3):
        assert M._watch_cursor_still(mem) is False
    assert 'cursor_still' not in mem
    assert 'nav_search' not in mem
    assert mem['_records'] == []
