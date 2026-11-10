"""The monthly walk reuses a same-month read unless a battle dirtied it (v577).

g1163 2026-11-09 18:21-18:26: the monthly status walk re-read all 27 generals
even though only a handful had taken damage, spending five minutes a month.
"""
from docich.hanjuku.flows import post_month_status as P


def memory(month='14-1', dirty=None):
    return {'chapter': 7, 'month': month, 'tick': 90_000,
            'house_eggs': {
                'アテナ': {'month': month, 'location': 'castle', 'location_observed': True,
                          'observed_tick': 80_000},
                'キール': {'month': month, 'location': 'castle', 'location_observed': True,
                          'observed_tick': 80_000},
                'ラスト': {'month': '13-11', 'location': 'castle', 'location_observed': True,
                          'observed_tick': 80_000}},
            'general_statuses': {'dirty': dirty or {}}}


def test_a_same_month_clean_read_is_reused():
    assert P._house_freshly_read(memory()) == {'アテナ', 'キール'}


def test_a_general_a_battle_marked_dirty_is_read_again():
    mem = memory(dirty={'キール': ['hp', 'location']})
    assert P._house_freshly_read(mem) == {'アテナ'}


def test_a_previous_month_read_is_not_reused():
    mem = memory()
    mem['house_eggs']['アテナ']['month'] = '13-12'
    assert P._house_freshly_read(mem) == {'キール'}


def test_the_finished_record_reports_the_walk_length():
    """Without elapsed_ticks a reuse change cannot be compared with the next run."""
    import inspect
    from docich.hanjuku.flows import post_month_status as P
    source = inspect.getsource(P)
    assert "'elapsed_ticks'" in source


def test_a_full_roster_walk_is_assumed_enumerated():
    """g1163 2026-11-09: 32 names collected, then roster_end_unconfirmed."""
    from docich.hanjuku.flows import post_month_status as P
    state = {'top_confirmed': True, 'names': [f'g{i}' for i in range(P.MAX_SCAN)]}
    assert P._roster_end_assumable(state) is True
    state['names'] = state['names'][:-1]
    assert P._roster_end_assumable(state) is False
    state = {'top_confirmed': False, 'names': [f'g{i}' for i in range(P.MAX_SCAN)]}
    assert P._roster_end_assumable(state) is False
