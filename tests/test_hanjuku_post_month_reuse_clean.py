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
    assert P._roster_end_assumable(memory(), state) is True
    state['names'] = state['names'][:-1]
    assert P._roster_end_assumable(memory(), state) is False
    state = {'top_confirmed': False, 'names': [f'g{i}' for i in range(P.MAX_SCAN)]}
    assert P._roster_end_assumable(memory(), state) is False


def test_a_count_matching_walk_is_assumed_enumerated():
    """A sub-32 roster fully enumerated stalls on the same missing end proof:
    the game's own headcount corroborates the enumeration."""
    from docich.hanjuku.flows import post_month_status as P
    from docich.hanjuku.os_layer import roster as receipts
    names = [f'g{i}' for i in range(12)]
    mem = memory()
    mem['country_general_count'] = {
        'scope': [2, '5-3'], 'identity': receipts.run_identity(mem.get('_run_identity')),
        'tick': 10, 'count': 12}
    mem['chapter'], mem['month'], mem['tick'] = 2, '5-3', 20
    state = {'top_confirmed': True, 'names': list(names), 'scope': [2, '5-3'],
             'statuses': {n: {'location': 'unknown'} for n in names}, 'dirty': []}
    # ``location: unknown`` still blocks ``_complete_scan`` but not the assume:
    # enumeration is proven, the tasks handle the unreadable row.
    assert P._roster_end_assumable(mem, state) is True
    # A genuinely truncated walk (fewer names than the country reports), a
    # still-dirty name, a duplicate OCR row or a foreign scope are not proven.
    for changed in (dict(names=names[:-1]),
                    dict(dirty=['g3']),
                    dict(statuses={n: {} for n in names[:-1]}),
                    dict(names=names + ['g0'])):
        assert P._roster_end_assumable(mem, dict(state, **changed)) is False, changed
    mem['country_general_count']['count'] = 13
    assert P._roster_end_assumable(mem, state) is False
    mem['country_general_count']['count'] = 12
    mem['country_general_count']['scope'] = [2, '5-2']
    assert P._roster_end_assumable(mem, state) is False
