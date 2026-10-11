"""A defense must not consume an uncommitted repair trip for the month."""
from copy import deepcopy

import pytest

from docich import hanjuku_house as house
from docich.hanjuku_screen import Screen


def memory(phase='castle_view'):
    return {
        'chapter': 2, 'month': '3-5', 'gold': 196, 'tick': 1700,
        'captured': ['スペランザ', 'アウスパジア'],
        'garrison': {'スペランザ': ['ココット'], 'アウスパジア': ['ゼウス']},
        'house_eggs': {'ココット': {
            'general': 'ココット', 'broken': True, 'location': 'castle',
            'location_observed': True, 'hp': 24, 'max_hp': 24,
            'month': '3-5', 'observed_chapter': 2, 'observed_tick': 1631}},
        'house': {'phase': phase, 'chapter': 2, 'roster_month': '3-5',
                  'month_scan': False, 'general': 'ココット', 'source': 'スペランザ',
                  'pending': [], 'castles': ['アウスパジア'],
                  'purchase': None, 'purchased': False, 'returning': False,
                  'age': 2, 'total': 69},
    }


def step(mem, kind):
    return house.step(Screen([], None, '', kind=kind), mem, None)


@pytest.mark.parametrize('phase', [
    'find_castle', 'castle_view', 'castle_open', 'castle_verify',
    'castle_sortie', 'castle_pick', 'castle_kit', 'castle_confirm'])
def test_defense_rechecks_uncommitted_named_repair_without_another_roster_walk(phase):
    mem = memory(phase)
    assert step(mem, 'defense_started') is None
    assert step(mem, 'map') == []
    assert mem['house']['phase'] == 'repair_recheck'
    assert mem['house']['interruption_retries'] == 1
    assert step(mem, 'map') == []
    assert mem['house']['phase'] == 'find_castle'
    assert mem['house']['general'] == 'ココット'
    assert mem['house']['source'] is None
    assert mem['house']['castles'][0] == 'スペランザ'
    assert mem['house']['total'] == 71
    assert mem['gold'] == 196


def test_recheck_uses_changed_castle_ownership_and_placement_after_defense():
    mem = memory()
    step(mem, 'defense_started')
    mem['lost'] = ['スペランザ']
    mem['captured'].remove('スペランザ')
    mem['garrison'] = {'アウスパジア': ['ココット', 'ゼウス']}
    step(mem, 'map')
    step(mem, 'map')
    assert mem['house']['castles'] == ['アウスパジア', 'アルマムーン']
    assert 'sole_defender' not in mem['house']


def test_repeated_defenses_have_a_finite_retry_budget_and_keep_session_budget():
    mem = memory()
    for count in (1, 2):
        step(mem, 'defense_started')
        step(mem, 'map')
        assert mem['house']['interruption_retries'] == count
        step(mem, 'map')
    step(mem, 'defense_started')
    step(mem, 'map')
    assert mem['house']['phase'] == 'close'
    assert mem['house']['total'] == 74


@pytest.mark.parametrize('phase', ['out_target', 'house_view', 'house_confirm',
                                  'buy', 'receipt', 'return_pick'])
def test_committed_or_paid_trip_is_never_replayed(phase):
    mem = memory(phase)
    step(mem, 'defense_started')
    assert step(mem, 'map') == []
    assert mem['house']['phase'] == 'close'
    assert not mem['house'].get('interruption_retries')


@pytest.mark.parametrize('change', ['month', 'chapter', 'missing_status', 'field',
                                   'wrong_name', 'paid', 'wounded', 'future_status'])
def test_invalidated_target_cannot_restart_a_trip(change):
    mem = memory()
    step(mem, 'defense_started')
    if change == 'month': mem['month'] = '3-6'
    elif change == 'chapter': mem['chapter'] = 3
    elif change == 'missing_status': mem['house_eggs'] = {}
    elif change == 'field': mem['house_eggs']['ココット']['location'] = 'field'
    elif change == 'wrong_name': mem['house_eggs']['ココット']['general'] = 'ゼウス'
    elif change == 'paid': mem['house']['purchased'] = True
    elif change == 'wounded': mem['house_eggs']['ココット']['hp'] = 23
    elif change == 'future_status': mem['house_eggs']['ココット']['observed_tick'] = 1701
    step(mem, 'map')
    assert mem.get('house', {}).get('phase') != 'repair_recheck'


def test_retry_wait_does_not_consume_another_battle_or_unrelated_dialogue():
    mem = memory()
    step(mem, 'defense_started')
    step(mem, 'map')
    before = deepcopy(mem['house'])
    for _ in range(100):
        assert step(mem, 'battle_menu') is None
    assert mem['house'] == before
    assert step(mem, 'text') is None
    assert mem['house']['phase'] == 'repair_recheck'


def test_scope_change_while_waiting_discards_the_retry():
    mem = memory()
    step(mem, 'defense_started')
    step(mem, 'map')
    mem['month'] = '3-6'
    step(mem, 'map')
    assert mem['house']['phase'] == 'close'


def test_retry_cannot_reset_the_original_session_limit():
    mem = memory()
    mem['house']['total'] = house.SESSION_LIMIT
    step(mem, 'defense_started')
    step(mem, 'map')
    step(mem, 'map')
    assert mem.get('house', {}).get('phase') != 'find_castle'


def test_recheck_wait_is_bounded_even_when_no_map_returns():
    mem = memory()
    step(mem, 'defense_started')
    step(mem, 'map')
    for _ in range(house.STEP_LIMIT + 1):
        step(mem, 'text')
    assert mem['house']['phase'] == 'close'


def test_new_marching_evidence_is_rechecked_before_restarting_the_castle_route():
    mem = memory()
    step(mem, 'defense_started')
    mem['sorties'] = {'A': {'general': 'ココット', 'status': 'en_route',
                           'target': 'アウスパジア', 'tick': 1650}}
    step(mem, 'map')
    step(mem, 'map')
    assert mem['house']['phase'] == 'close'


def test_restarted_route_still_protects_the_last_castles_sole_defender():
    from test_hanjuku_sole_defender_repair import castle_status
    mem = memory()
    step(mem, 'defense_started')
    mem['captured'] = []
    mem['garrison'] = {'アルマムーン': ['ココット']}
    step(mem, 'map')
    step(mem, 'map')
    assert mem['house']['castles'] == ['アルマムーン']
    mem['house'].update(phase='castle_verify', source='アルマムーン')
    house.step(castle_status('アルマムーン', ['ココット']), mem, None)
    assert mem['house']['phase'] == 'close'


def test_restarted_route_checks_new_hp_before_confirming_dispatch():
    from test_hanjuku_house import status
    from docich.hanjuku_screen import parse
    mem = memory()
    step(mem, 'defense_started')
    step(mem, 'map')
    step(mem, 'map')
    mem['house'].update(phase='castle_kit', source='スペランザ')
    frame = status('ココット', main=False, hp=23, max_hp=24)
    screen = parse(frame)
    screen.kind = 'card_select'
    assert house.step(screen, mem, frame) == []
    assert mem['house']['phase'] == 'close'
