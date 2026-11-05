"""Owner 2026-11-05: forget marches that can no longer arrive.

g1133 kept 46 overdue records, so every general stayed "out" forever and the
plan had no spare to send at the boss. A record past the bindable window is
dropped and its general goes back onto the source roster; the next castle
menu read replaces that guess with the measured list.
"""
from docich.hanjuku.strategy import orders as O


def memory(age, status='overdue', garrison=None):
    return {'chapter': 6, 'tick': 50000, '_records': [], 'orders': {},
            'garrison': {'アルマムーン': [], 'アブダレ': []} if garrison is None else garrison,
            'sorties': {'A:x:M1': {'general': 'ヘルメス', 'source': 'アルマムーン',
                                   'target': 'ボス', 'status': status,
                                   'tick': 50000 - age}},
            'launched_orders': {}}


def test_a_spent_record_is_dropped_and_its_general_returns():
    mem = memory(age=O.SPENT_SORTIE_TICKS + 1)
    O._expire_spent_sorties(mem)
    assert 'A:x:M1' not in mem['sorties']
    assert 'ヘルメス' in mem['garrison']['アルマムーン']
    assert [r for r in mem['_records'] if r['decision'] == 'sortie_record_expired']


def test_a_recent_overdue_record_is_kept():
    mem = memory(age=O.SPENT_SORTIE_TICKS - 1)
    O._expire_spent_sorties(mem)
    assert 'A:x:M1' in mem['sorties']
    assert mem['garrison']['アルマムーン'] == []


def test_a_general_already_placed_elsewhere_is_not_duplicated():
    mem = memory(age=O.SPENT_SORTIE_TICKS + 1)
    mem['garrison']['アブダレ'] = ['ヘルメス']
    O._expire_spent_sorties(mem)
    assert 'A:x:M1' not in mem['sorties']
    assert mem['garrison']['アルマムーン'] == []
    assert mem['garrison']['アブダレ'] == ['ヘルメス']


def test_an_unread_source_gets_the_general_back():
    # A castle whose list was never read is stored as None; the returned
    # general is a guess the next castle menu read replaces.
    mem = memory(age=O.SPENT_SORTIE_TICKS + 1, garrison={'アルマムーン': None, 'アブダレ': []})
    O._expire_spent_sorties(mem)
    assert mem['garrison']['アルマムーン'] == ['ヘルメス']


def test_a_zombie_march_is_expired_with_the_overdue_ones():
    # g1133 2026-11-05: グレナデン stayed launched_unconfirmed for 5115
    # observations; a march cannot still be running past the window.
    mem = memory(age=O.SPENT_SORTIE_TICKS + 1, status='en_route')
    O._expire_spent_sorties(mem)
    assert 'A:x:M1' not in mem['sorties']
    assert 'ヘルメス' in mem['garrison']['アルマムーン']


def test_a_recent_march_is_kept():
    mem = memory(age=O.SPENT_SORTIE_TICKS - 1, status='en_route')
    O._expire_spent_sorties(mem)
    assert 'A:x:M1' in mem['sorties']


def test_a_planned_zombie_expires_past_twice_the_window():
    # g1133 2026-11-05: グレナデン stayed launched_unconfirmed for 9689
    # observations while every plan kept the step.
    mem = memory(age=2 * O.SPENT_SORTIE_TICKS + 1, status='launched_unconfirmed')
    mem['chart_plan'] = {'orders': [{'step': 'A:x:M1', 'general': 'ヘルメス',
                                     'source': 'アルマムーン', 'target': 'ボス'}]}
    O._expire_spent_sorties(mem)
    assert 'A:x:M1' not in mem['sorties']


def test_a_planned_record_inside_twice_the_window_is_kept():
    mem = memory(age=O.SPENT_SORTIE_TICKS + 1, status='launched_unconfirmed')
    mem['chart_plan'] = {'orders': [{'step': 'A:x:M1', 'general': 'ヘルメス',
                                     'source': 'アルマムーン', 'target': 'ボス'}]}
    O._expire_spent_sorties(mem)
    assert 'A:x:M1' in mem['sorties']


def observed(age, location, delta, *, observed=True):
    mem = memory(age=age, status='en_route')
    mem['general_statuses'] = {'statuses': {'ヘルメス': {
        'location': location, 'location_observed': observed,
        'tick': mem['sorties']['A:x:M1']['tick'] + delta}}}
    return mem


def test_a_camp_read_after_departure_ends_the_march_early():
    # g1133 2026-11-06: デュオニソス camped after a retreat while his boss
    # sortie stayed en_route until the 3200-tick window.
    mem = observed(age=200, location='camp', delta=50)
    O._expire_spent_sorties(mem)
    assert 'A:x:M1' not in mem['sorties']
    assert 'ヘルメス' in mem['garrison']['アルマムーン']
    assert any(r['decision'] == 'sortie_record_observed_ended' for r in mem['_records'])


def test_a_castle_read_after_departure_ends_the_march_early():
    mem = observed(age=200, location='castle', delta=50)
    O._expire_spent_sorties(mem)
    assert 'A:x:M1' not in mem['sorties']
    assert 'ヘルメス' in mem['garrison']['アルマムーン']


def test_a_status_read_before_departure_does_not_end_the_march():
    mem = observed(age=200, location='camp', delta=-50)
    O._expire_spent_sorties(mem)
    assert 'A:x:M1' in mem['sorties']
    assert mem['garrison']['アルマムーン'] == []


def test_a_march_reading_does_not_end_the_march():
    mem = observed(age=200, location='march', delta=50)
    O._expire_spent_sorties(mem)
    assert 'A:x:M1' in mem['sorties']


def test_an_unobserved_location_does_not_end_the_march():
    mem = observed(age=200, location='camp', delta=50, observed=False)
    O._expire_spent_sorties(mem)
    assert 'A:x:M1' in mem['sorties']


def test_a_march_read_after_departure_restarts_the_window():
    # g1133 2026-11-06: boss marchers read しんぐんちゅうです at 111501-111545
    # near the 2400-tick relaunch window, still walking.
    mem = observed(age=O.SPENT_SORTIE_TICKS + 1, location='march', delta=200)
    sortie = mem['sorties']['A:x:M1']
    refreshed_tick = sortie['tick'] + 200
    O._refresh_live_marches(mem)
    assert mem['sorties']['A:x:M1']['tick'] == refreshed_tick
    assert any(r['decision'] == 'sortie_march_refreshed' for r in mem['_records'])
    # The expiry window now counts from the measured reading, not the launch.
    O._expire_spent_sorties(mem)
    assert 'A:x:M1' in mem['sorties']


def test_a_march_read_before_departure_does_not_restart_the_window():
    mem = observed(age=200, location='march', delta=-50)
    original = mem['sorties']['A:x:M1']['tick']
    O._refresh_live_marches(mem)
    assert mem['sorties']['A:x:M1']['tick'] == original
    assert not any(r['decision'] == 'sortie_march_refreshed' for r in mem['_records'])


def test_a_camp_read_does_not_restart_the_window():
    mem = observed(age=200, location='camp', delta=50)
    original = mem['sorties']['A:x:M1']['tick']
    O._refresh_live_marches(mem)
    assert mem['sorties']['A:x:M1']['tick'] == original
    O._expire_spent_sorties(mem)
    assert 'A:x:M1' not in mem['sorties']


def test_the_march_refresh_is_recorded_once():
    mem = observed(age=O.SPENT_SORTIE_TICKS + 1, location='march', delta=200)
    O._refresh_live_marches(mem)
    O._refresh_live_marches(mem)
    records = [r for r in mem['_records'] if r['decision'] == 'sortie_march_refreshed']
    assert len(records) == 1
