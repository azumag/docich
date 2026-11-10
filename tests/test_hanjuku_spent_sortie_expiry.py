"""Overdue marches must not invent a source-castle return.

Expiry drops an unbindable record but makes the general's location dirty;
the next monthly named-status scan rechecks their actual whereabouts.
"""
from docich.hanjuku.strategy import orders as O
from docich.hanjuku.flows import post_month_status as post
from docich.hanjuku.state.regions import world as _world, plan as _plan_region, sorties as _sorties


def memory(age, status='overdue', garrison=None):
    return {'chapter': 6, 'tick': 50000, '_records': [], 'orders': {},
            'world': {'garrison': {'アルマムーン': [], 'アブダレ': []} if garrison is None else garrison,
                      'sorties': {'A:x:M1': {'general': 'ヘルメス', 'source': 'アルマムーン',
                                   'target': 'ボス', 'status': status,
                                   'tick': 50000 - age}}},
            'launched_orders': {}}


def test_a_spent_record_requires_monthly_location_verification():
    mem = memory(age=O.SPENT_SORTIE_TICKS + 1)
    O._expire_spent_sorties(mem)
    assert 'A:x:M1' not in _world(mem)['sorties']
    assert _world(mem)['garrison']['アルマムーン'] == []
    assert 'ヘルメス' in _world(mem)['general_location_unknown']
    assert [r for r in mem['_records'] if r['decision'] == 'sortie_record_expired']


def test_a_recent_overdue_record_is_kept():
    mem = memory(age=O.SPENT_SORTIE_TICKS - 1)
    O._expire_spent_sorties(mem)
    assert 'A:x:M1' in _world(mem)['sorties']
    assert _world(mem)['garrison']['アルマムーン'] == []


def test_a_general_already_placed_elsewhere_is_not_duplicated():
    mem = memory(age=O.SPENT_SORTIE_TICKS + 1)
    _world(mem)['garrison']['アブダレ'] = ['ヘルメス']
    O._expire_spent_sorties(mem)
    assert 'A:x:M1' not in _world(mem)['sorties']
    assert _world(mem)['garrison']['アルマムーン'] == []
    assert _world(mem)['garrison']['アブダレ'] == []
    assert _world(mem)['general_location_unknown'] == ['ヘルメス']


def test_an_unread_source_does_not_invent_a_return():
    mem = memory(age=O.SPENT_SORTIE_TICKS + 1, garrison={'アルマムーン': None, 'アブダレ': []})
    O._expire_spent_sorties(mem)
    assert _world(mem)['garrison']['アルマムーン'] is None
    assert _world(mem)['general_location_unknown'] == ['ヘルメス']


def test_a_zombie_march_is_expired_with_the_overdue_ones():
    # Expiry releases the record, but not the unverified general.
    mem = memory(age=O.SPENT_SORTIE_TICKS + 1, status='en_route')
    O._expire_spent_sorties(mem)
    assert 'A:x:M1' not in _world(mem)['sorties']
    assert _world(mem)['garrison']['アルマムーン'] == []
    assert _world(mem)['general_location_unknown'] == ['ヘルメス']


def test_a_recent_march_is_kept():
    mem = memory(age=O.SPENT_SORTIE_TICKS - 1, status='en_route')
    O._expire_spent_sorties(mem)
    assert 'A:x:M1' in _world(mem)['sorties']


def test_a_planned_zombie_expires_past_twice_the_window():
    # g1133 2026-11-05: グレナデン stayed launched_unconfirmed for 9689
    # observations while every plan kept the step.
    mem = memory(age=2 * O.SPENT_SORTIE_TICKS + 1, status='launched_unconfirmed')
    mem['chart_plan'] = {'orders': [{'step': 'A:x:M1', 'general': 'ヘルメス',
                                     'source': 'アルマムーン', 'target': 'ボス'}]}
    O._expire_spent_sorties(mem)
    assert 'A:x:M1' not in _world(mem)['sorties']


def test_a_planned_record_inside_twice_the_window_is_kept():
    mem = memory(age=O.SPENT_SORTIE_TICKS + 1, status='launched_unconfirmed')
    mem['chart_plan'] = {'orders': [{'step': 'A:x:M1', 'general': 'ヘルメス',
                                     'source': 'アルマムーン', 'target': 'ボス'}]}
    O._expire_spent_sorties(mem)
    assert 'A:x:M1' in _world(mem)['sorties']


def observed(age, location, delta, *, observed=True):
    mem = memory(age=age, status='en_route')
    mem['general_statuses'] = {'statuses': {'ヘルメス': {
        'location': location, 'location_observed': observed,
        'tick': _world(mem)['sorties']['A:x:M1']['tick'] + delta}}}
    return mem


def test_a_camp_read_after_departure_ends_the_march_early():
    # g1133 2026-11-06: デュオニソス camped after a retreat while his boss
    # sortie stayed en_route until the 3200-tick window.
    mem = observed(age=200, location='camp', delta=50)
    O._expire_spent_sorties(mem)
    assert 'A:x:M1' not in _world(mem)['sorties']
    assert 'ヘルメス' in _world(mem)['garrison']['アルマムーン']
    assert any(r['decision'] == 'sortie_record_observed_ended' for r in mem['_records'])


def test_a_castle_read_after_departure_ends_the_march_early():
    mem = observed(age=200, location='castle', delta=50)
    O._expire_spent_sorties(mem)
    assert 'A:x:M1' not in _world(mem)['sorties']
    assert 'ヘルメス' in _world(mem)['garrison']['アルマムーン']


def test_a_status_read_before_departure_does_not_end_the_march():
    mem = observed(age=200, location='camp', delta=-50)
    O._expire_spent_sorties(mem)
    assert 'A:x:M1' in _world(mem)['sorties']
    assert _world(mem)['garrison']['アルマムーン'] == []


def test_a_march_reading_does_not_end_the_march():
    mem = observed(age=200, location='march', delta=50)
    O._expire_spent_sorties(mem)
    assert 'A:x:M1' in _world(mem)['sorties']


def test_an_unobserved_location_does_not_end_the_march():
    mem = observed(age=200, location='camp', delta=50, observed=False)
    O._expire_spent_sorties(mem)
    assert 'A:x:M1' in _world(mem)['sorties']


def test_a_march_read_after_departure_restarts_the_window():
    # g1133 2026-11-06: boss marchers read しんぐんちゅうです at 111501-111545
    # near the 2400-tick relaunch window, still walking.
    mem = observed(age=O.SPENT_SORTIE_TICKS + 1, location='march', delta=200)
    sortie = _world(mem)['sorties']['A:x:M1']
    refreshed_tick = sortie['tick'] + 200
    O._refresh_live_marches(mem)
    assert _world(mem)['sorties']['A:x:M1']['tick'] == refreshed_tick
    assert any(r['decision'] == 'sortie_march_refreshed' for r in mem['_records'])
    # The expiry window now counts from the measured reading, not the launch.
    O._expire_spent_sorties(mem)
    assert 'A:x:M1' in _world(mem)['sorties']


def test_a_march_read_before_departure_does_not_restart_the_window():
    mem = observed(age=200, location='march', delta=-50)
    original = _world(mem)['sorties']['A:x:M1']['tick']
    O._refresh_live_marches(mem)
    assert _world(mem)['sorties']['A:x:M1']['tick'] == original
    assert not any(r['decision'] == 'sortie_march_refreshed' for r in mem['_records'])


def test_a_camp_read_does_not_restart_the_window():
    mem = observed(age=200, location='camp', delta=50)
    original = _world(mem)['sorties']['A:x:M1']['tick']
    O._refresh_live_marches(mem)
    assert _world(mem)['sorties']['A:x:M1']['tick'] == original
    O._expire_spent_sorties(mem)
    assert 'A:x:M1' not in _world(mem)['sorties']


def test_the_march_refresh_is_recorded_once():
    mem = observed(age=O.SPENT_SORTIE_TICKS + 1, location='march', delta=200)
    O._refresh_live_marches(mem)
    O._refresh_live_marches(mem)
    records = [r for r in mem['_records'] if r['decision'] == 'sortie_march_refreshed']
    assert len(records) == 1


def test_overdue_transition_marks_only_the_actor_dirty_for_next_month():
    mem = memory(age=300, status='en_route')
    mem['month'] = '3-6'
    mem['general_statuses'] = {
        'scope': [6, '3-5'], 'identity': None, 'complete': True,
        'statuses': {
            'ヘルメス': {'location': 'castle', 'castle': 'アルマムーン'},
            'ゼウス': {'location': 'castle', 'castle': 'アルマムーン'},
        },
        'dirty': {},
    }
    _world(mem)['garrison']['アルマムーン'] = ['ヘルメス', 'ゼウス']
    sortie = _world(mem)['sorties']['A:x:M1']
    sortie['status'] = 'overdue'
    O._queue_overdue_location_recheck(mem, sortie)
    O._queue_overdue_location_recheck(mem, sortie)
    assert mem['general_statuses']['dirty'] == {'ヘルメス': ['location']}
    assert _world(mem)['garrison']['アルマムーン'] == ['ゼウス']
    assert _world(mem)['general_location_unknown'] == ['ヘルメス']
    assert len([r for r in mem['_records']
                if r['decision'] == 'sortie_location_recheck_queued']) == 1
    post.arm(mem)
    assert mem['post_month_status']['dirty'] == ['ヘルメス']
    assert 'ゼウス' not in mem['post_month_status']['dirty']


def test_expiry_cannot_erase_the_next_month_recheck():
    mem = memory(age=O.SPENT_SORTIE_TICKS + 1)
    mem['month'] = '3-6'
    mem['general_statuses'] = {
        'scope': [6, '3-5'], 'identity': None, 'complete': True,
        'statuses': {'ヘルメス': {'location': 'castle', 'castle': 'アルマムーン'}},
        'dirty': {},
    }
    O._expire_spent_sorties(mem)
    assert _world(mem)['sorties'] == {}
    assert mem['general_statuses']['dirty'] == {'ヘルメス': ['location']}
    post.arm(mem)
    assert mem['post_month_status']['dirty'] == ['ヘルメス']


def test_a_kept_boss_camp_reopens_the_order_same_month():
    # docich#3515 (g1133 2026-11-06): アルテミス stayed en_route after kept
    # while camped at home. The observed camp must not strand the order
    # launched with no sortie; it reopens it at once.
    mem = observed(age=200, location='camp', delta=50)
    _plan_region(mem)['orders'] = {'A:x:M1': 'launched'}
    _world(mem)['sorties']['A:x:M1']['launch_kept'] = True
    from docich.hanjuku.state.regions import flow as _flow
    _flow(mem)['launch_probe'] = {'A:x:M1': {'general': 'ヘルメス', 'source': 'アルマムーン',
                   'target': 'ボス', 'reads': 3, 'blind': True, 'kept': True, 'tick': 49900}}
    O._expire_spent_sorties(mem)
    assert 'A:x:M1' not in _world(mem)['sorties']
    assert _plan_region(mem)['orders']['A:x:M1'] == 'pending'
    assert 'ヘルメス' in _world(mem)['garrison']['アルマムーン']
    assert any(r['decision'] == 'sortie_launch_unverified' for r in mem['_records'])


def test_a_confirmed_boss_camp_does_not_reopen():
    # #3500 retreat: a confirmed launch that camped after an encounter
    # discards only; reopening it would double-send the wave.
    mem = observed(age=200, location='camp', delta=50)
    _plan_region(mem)['orders'] = {'A:x:M1': 'launched'}
    _world(mem)['sorties']['A:x:M1']['launch_confirmed'] = True
    O._expire_spent_sorties(mem)
    assert 'A:x:M1' not in _world(mem)['sorties']
    assert _plan_region(mem)['orders']['A:x:M1'] == 'launched'
    assert not any(r['decision'] == 'sortie_launch_unverified' for r in mem['_records'])
