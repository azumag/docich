"""SELECT gives a known position without a roof anchor (v429).

g1133 2026-11-05: the policy sat with `uncertain: True`, `anchor: None` and
`nav_last: None` for hours. SELECT centres the camera on the hero, so the cursor
stands on the hero castle right after it - a position the bot can use without
finding a roof.
"""
from docich import hanjuku_chart as chart
from docich.hanjuku.flows import map as M
from docich.hanjuku.os_layer.commands import pad
from docich.hanjuku_screen import Screen

HOME = chart.home_castle(6)


def memory():
    return {'chapter': 6, 'tick': 100, '_records': [], 'select_centered': 99,
            'garrison': {HOME: [chart.HERO]}, 'captured': [HOME]}


def test_a_centred_select_sets_the_hero_castle():
    mem = memory()
    M._apply_select_centered(mem)
    assert mem['anchor'] == HOME
    assert mem['cursor'] == list(chart.castles(6)[HOME])
    assert mem['uncertain'] is False
    assert mem['_records'][-1]['decision'] == 'select_centered'


def test_without_a_centred_select_nothing_changes():
    mem = {'chapter': 6, 'tick': 100, '_records': [], 'cursor': [1, 2], 'uncertain': True}
    M._apply_select_centered(mem)
    assert mem['cursor'] == [1, 2] and mem['uncertain'] is True


def _map_frame():
    return Screen(lines=[], hand=None, text='', kind='map', cursor=(80, 90))


def test_a_recentered_frame_skips_the_periodic_refocus():
    # g1133 2026-11-05: map frames were sparser than SELECT_FOCUS_INTERVAL
    # while chapter 6 threw battles, so every cluster re-sent SELECT and no
    # order ever drove. A frame that just re-established a known position
    # must drive instead of refocusing again.
    mem = {'chapter': 6, 'tick': 500, '_records': [], 'select_centered': 100,
           'select_focus_tick': 100,
           'garrison': {HOME: [chart.HERO]}, 'captured': [HOME]}
    actions = M.map_step(_map_frame(), mem, None)
    assert actions != [pad('select')]
    assert 'select_focus' not in [r['decision'] for r in mem['_records']]
    assert mem['anchor'] == HOME and mem['uncertain'] is False


def test_without_a_recenter_the_periodic_refocus_still_fires():
    mem = {'chapter': 6, 'tick': 500, '_records': [],
           'select_focus_tick': 100,
           'garrison': {HOME: [chart.HERO]}, 'captured': [HOME]}
    actions = M.map_step(_map_frame(), mem, None)
    assert actions == [pad('select')]
    assert 'select_focus' in [r['decision'] for r in mem['_records']]
