"""Same-run/month complete status surveys survive a plain field return."""
from copy import deepcopy

import pytest

from docich import hanjuku_house as house, hanjuku_policy as p, hanjuku_roster as r
from docich.hanjuku_bot import decide
from docich.hanjuku_font import TextLine
from docich.hanjuku_screen import Battle, Screen, parse
from test_hanjuku_chart_bot import month_canvas
from test_hanjuku_house import roster, status

IDENTITY = {'game': 'hanjuku-hero', 'runtime_id': 'g1-synthetic',
            'generation': 1, 'lease_id': 'synthetic-lease'}
NAMES = ['どうし', 'ゼウス']
MAP = Screen([], None, '', kind='map')


def completed(*, broken=False, hp=85):
    mem = {'chapter': 1, 'month': '1-6', 'tick': 500, 'gold': 250,
        '_run_identity': dict(IDENTITY), 'survey_run_identity': dict(IDENTITY),
        'garrison': {'アルマムーン': list(NAMES)},
        'castle_income': {'アルマムーン': {'chapter': 1, 'month': '1-6', 'tick': 500, 'income': 22}},
        'house': {'phase': 'status', 'chapter': 1, 'month_scan': True,
                  'seen': [], 'pending': [], 'age': 0, 'total': 0}}
    state = mem['house']
    r.begin(mem, state)
    r.page(mem, house.roster(parse(roster(NAMES))))
    for name in NAMES:
        state['selected'] = name
        info = house._observe_status(parse(status(name,
            egg='こわれている' if broken and name == 'ゼウス' else 'エラベルエッグ4', hp=hp)), mem)
        assert info is not None and info['general'] == name
        state['seen'].append(name)
        r.wage(mem, info)
    assert r.complete(mem, state)
    house._finish(mem)
    assert r.completed_survey(mem) is not None
    return mem


def test_a_plain_field_return_does_not_reopen_any_general_status():
    mem = completed()
    assert house.step(MAP, mem, None) is None
    assert 'house' not in mem
    assert not [rec for rec in mem.get('_records', []) if rec['decision'] == 'house_scan_started']


def walked_large_roster(*, broken=False):
    from test_hanjuku_recruit_roster import NAMES as large_names
    names = large_names[:12]
    mem = {'chapter': 1, 'month': '1-6', 'tick': 500, 'gold': 250,
           '_run_identity': dict(IDENTITY), 'survey_run_identity': dict(IDENTITY),
           'garrison': {'アルマムーン': list(names)},
           'house': {'phase': 'status', 'chapter': 1, 'month_scan': True,
                     'seen': [], 'pending': [], 'age': 0, 'total': 0}}
    state = mem['house']
    r.begin(mem, state)
    for index, name in enumerate(names):
        r.page(mem, names[:8] if index < 8 else names[4:])
        state['selected'] = name
        info = house._observe_status(parse(status(name,
            egg='こわれている' if broken and name == 'ゼウス' else 'エラベルエッグ4')), mem)
        assert info['general'] == name
        state['seen'].append(name)
        r.wage(mem, info)
    assert not r.complete(mem, state)  # tail cursor did not prove the global total
    house._finish(mem)
    return mem


def test_large_nonwrapping_roster_does_not_repeat_the_status_walk():
    mem = walked_large_roster()
    assert r.completed_survey(mem) is None
    assert mem['recruit_roster']['complete'] is False
    assert r.economics(mem, ['アルマムーン']) is None
    assert r.reusable_survey(mem) is not None
    assert house.step(MAP, mem, None) is None
    assert 'house' not in mem


def test_known_broken_general_in_partial_membership_skips_a_second_full_status_walk():
    mem = walked_large_roster(broken=True)
    assert house.step(MAP, mem, None) == []
    assert mem['house']['phase'] == 'find_castle'
    assert mem['house']['general'] == 'ゼウス'
    assert mem['recruit_roster']['complete'] is False


def test_changed_general_in_a_large_roster_is_the_only_status_target():
    mem = walked_large_roster()
    r.dirty_status(mem, ['ゼウス'], fields=('egg',))
    assert house.step(MAP, mem, None) == [p.pad('x')]
    assert mem['house']['status_targets'] == ['ゼウス']
    mem['house']['phase'] = 'roster'
    assert house.step(parse(roster(NAMES, 0)), mem, None) == [p.pad('down')]
    assert house.step(parse(roster(NAMES, 1)), mem, None) == [p.pad('a')]
    assert house.step(parse(status('ゼウス', egg='エラベルエッグ1')), mem, None) == [p.pad('b')]
    assert r.recheck_targets(mem) == []
    assert mem['recruit_roster']['complete'] is False


def test_same_unread_location_is_not_immediately_reopened_after_one_recheck():
    mem = completed()
    r.dirty_status(mem, ['ゼウス'])
    screen = parse(status('ゼウス', egg='エラベルエッグ4'))
    screen.text = screen.text.replace('しろのなかにいます', '')
    house._observe_status(screen, mem)
    assert mem['roster_survey']['dirty'] == {'ゼウス': ['location']}
    assert r.recheck_targets(mem) == []  # preserve unknown location, no blind repeat
    assert house.step(MAP, mem, None) is None
    r.dirty_status(mem, ['ゼウス'], fields=('location',))
    assert r.recheck_targets(mem) == ['ゼウス']  # a new actual change permits a new read


@pytest.mark.parametrize('change', ['run', 'month', 'chapter', 'join', 'unknown_page'])
def test_partial_status_walk_never_survives_an_invalidated_scope_or_membership(change):
    mem = walked_large_roster()
    if change == 'run':
        mem['_run_identity']['generation'] = 2
    elif change == 'month':
        mem['month'] = '1-7'
    elif change == 'chapter':
        mem['chapter'] = 2
    elif change == 'join':
        r.invalidate(mem)
    else:
        r.page(mem, ['エシャロット'])
    assert r.reusable_survey(mem) is None


def test_known_broken_egg_goes_directly_to_the_existing_repair_guards():
    mem = completed(broken=True)
    assert house.step(MAP, mem, None) == []
    assert mem['house']['from_survey'] is True
    assert mem['house']['phase'] == 'find_castle'
    assert mem['house']['general'] == 'ゼウス'
    assert mem['recruit_field_scan_attempts']['count'] == 1


def test_only_missing_castle_income_is_read_without_a_new_roster_walk():
    mem = completed()
    mem['castle_income'].clear()
    assert house.step(MAP, mem, None) == []
    assert mem['house']['phase'] == 'income_next'
    assert mem['house']['income_castles'] == ['アルマムーン']
    assert 'roster_started' not in mem['house']


def test_complete_names_and_fixed_wages_survive_400_ticks_but_live_hp_is_not_a_permission():
    mem = completed()
    mem['tick'] += 500
    mem['castle_income']['アルマムーン']['tick'] = mem['tick']
    assert r.fresh(mem)['names'] == NAMES
    assert r.economics(mem, ['アルマムーン'])['wages'] == 4
    assert house.step(MAP, mem, None) is None
    # The physical dispatch still requires its own current full-HP screen.
    assert house._repair_hp_ready({'hp': None, 'max_hp': 85}) is False


def test_partial_pages_are_separate_evidence_and_never_overwrite_the_complete_snapshot():
    mem = completed()
    snapshot = deepcopy(mem['roster_survey'])
    r.page(mem, ['どうし'])
    assert mem['recruit_roster']['complete'] is False
    assert mem['roster_survey'] == snapshot
    assert r.fresh(mem)['complete'] is True and r.fresh(mem)['names'] == NAMES


def test_a_new_actual_roster_name_invalidates_membership_instead_of_unioning_visits():
    mem = completed()
    r.page(mem, ['どうし', 'ココット'])
    assert r.completed_survey(mem) is None
    assert mem['recruit_roster']['names'] == ['どうし', 'ココット']
    assert mem['recruit_roster']['complete'] is False


@pytest.mark.parametrize('key,value', [('game', 'another-game'), ('runtime_id', 'g2-synthetic'),
    ('generation', 2), ('generation', True), ('generation', 0), ('lease_id', 'another-lease')])
def test_old_or_invalid_run_identity_never_reuses_the_snapshot(key, value):
    mem = completed()
    mem['_run_identity'][key] = value
    assert r.completed_survey(mem) is None
    assert r.fresh(mem) is None


@pytest.mark.parametrize('change', ['month', 'chapter', 'identity_missing', 'snapshot_identity_missing'])
def test_unknown_or_old_scope_is_not_promoted_to_a_current_complete_survey(change):
    mem = completed()
    if change in ('month', 'chapter'):
        mem[change] = '1-7' if change == 'month' else 2
    elif change == 'identity_missing':
        mem.pop('_run_identity')
    else:
        mem['roster_survey'].pop('identity')
    assert r.completed_survey(mem) is None


def test_an_old_trace_does_not_substitute_for_the_current_observation_identity():
    mem = completed()
    state = {'policy': mem, 'decision_trace': dict(IDENTITY)}
    other = {**IDENTITY, 'generation': 2, 'runtime_id': 'g2-synthetic'}
    _, updated = decide(month_canvas(250, month=6), state, run_identity=other)
    assert updated['policy'].get('roster_survey') is None
    assert updated['policy']['survey_run_identity'] == other


def test_same_run_legacy_zero_counts_are_preserved_without_promoting_its_old_survey():
    mem = completed()
    mem.pop('survey_run_identity')
    mem['egg_uses']['ゼウス'] = 0
    state = {'policy': mem, 'decision_trace': dict(IDENTITY)}
    _, updated = decide(month_canvas(250, month=6), state, run_identity=IDENTITY)
    assert updated['policy']['egg_uses']['ゼウス'] == 0
    assert updated['policy'].get('roster_survey') is None


def test_one_moving_general_invalidates_only_hp_and_location():
    mem = completed()
    hero = deepcopy(mem['roster_survey']['statuses']['どうし'])
    p._garrison_move(mem, 'ゼウス', source='アルマムーン', target='ナキューメラ')
    assert mem['roster_survey']['dirty'] == {'ゼウス': ['hp', 'location']}
    assert mem['roster_survey']['statuses']['どうし'] == hero
    assert r.fresh(mem)['wages'] == {'どうし': 0, 'ゼウス': 4}


def test_an_egg_attempt_invalidates_only_that_generals_egg_without_decrementing():
    mem = completed()
    mem['battle'] = {'ally': 'ゼウス'}
    p._egg_recheck(mem)
    assert mem['roster_survey']['dirty'] == {'ゼウス': ['egg']}
    assert mem['egg_uses']['ゼウス'] == 4
    assert p._egg_recovery_targets(mem) == ['ゼウス']


def test_battle_participation_invalidates_only_the_participant():
    mem = completed()
    mem['battle'] = {'ally': 'ゼウス', 'enemy': 'バジル', 'ally_hp': 3, 'enemy_hp': 2,
                    'start_ally_hp': 85, 'start_enemy_hp': 29, 'side': 'defense', 'cards_used': []}
    screen = Screen([], None, '', kind='battle', battle=Battle('バジル', 2, 'ゼウス', 3))
    p.battle_step(screen, mem)
    assert mem['roster_survey']['dirty'] == {'ゼウス': ['egg', 'hp', 'location']}
    assert r.fresh(mem)['names'] == NAMES


def test_only_the_dirty_general_is_opened_on_the_status_list():
    mem = completed()
    r.dirty_status(mem, ['ゼウス'], fields=('egg',))
    assert house.step(MAP, mem, None) == [p.pad('x')]
    mem['house']['phase'] = 'roster'
    assert house.step(parse(roster(NAMES, 0)), mem, None) == [p.pad('down')]
    assert house.step(parse(roster(NAMES, 1)), mem, None) == [p.pad('a')]
    assert mem['house']['selected'] == 'ゼウス'
    assert house.step(parse(status('ゼウス', egg='エラベルエッグ1')), mem, None) == [p.pad('b')]
    assert mem['roster_survey']['dirty'] == {}
    assert mem['house']['seen'] == ['ゼウス']
    assert mem['egg_uses']['ゼウス'] == 1


@pytest.mark.parametrize('paid', [True, False])
def test_recovery_changes_only_target_egg_facts_and_never_invents_the_new_count(paid):
    mem = completed()
    mem['egg_uses']['ゼウス'] = 0
    mem['shop'] = {'key': '1-6', 'egg': 'opened', 'egg_priority': True,
                   'reserve': 50, 'soldiers_done': True}
    mem['month_sub'] = {'kind': 'egg', 'key': '1-6', 'gold_before': 250,
                       'quoted_cost': 50, 'full_selected': True, 'left_menu': True}
    assert p._finish_month_sub(parse(month_canvas(200 if paid else 250, month=6)), mem, mem['shop'])
    assert mem['roster_survey']['dirty'] == {'ゼウス': ['egg']}
    assert mem['shop']['egg'] == ('done' if paid else 'unverified')
    assert mem['egg_uses'] == ({} if paid else {'どうし': 4, 'ゼウス': 0})


def test_actual_recovery_not_needed_also_rechecks_the_previous_target_egg_fact():
    mem = completed()
    mem['egg_uses']['ゼウス'] = 0
    mem['shop'] = {'key': '1-6', 'egg': 'opened', 'egg_priority': True,
                   'reserve': 50, 'items': [], 'merchant_done': True, 'soldiers_done': True}
    mem['month_sub'] = {'kind': 'egg', 'key': '1-6', 'gold_before': 250, 'left_menu': True}
    screen = parse(month_canvas(250, month=6))
    screen.text += 'おはらいのひつようなたまごはありませんぞ'
    p.month_step(screen, mem)
    assert mem['roster_survey']['dirty'] == {'ゼウス': ['egg']}
    assert mem['shop']['egg'] == 'not_needed'
    assert mem['egg_uses'] == {}


def _defeat(mem, ally='ゼウス'):
    mem['battle'] = {'ally': ally, 'enemy': 'バジル', 'ally_hp': 0, 'enemy_hp': 2,
                     'away': 1, 'cards_used': []}
    p.battle_end(mem, 'map')


def test_a_join_invalidates_membership_without_inventing_a_headcount():
    mem = completed()
    p.observe_events(Screen([], None, 'ココットがはいかにくわわった!', kind='text'), mem)
    assert 'roster_survey' not in mem and mem['recruit_roster_recheck'] is True


def test_hp_defeat_rereads_only_that_general_without_inventing_a_death():
    mem = completed()
    _defeat(mem)
    assert mem['roster_survey']['defeated'] == ['ゼウス']
    assert r.recheck_targets(mem) == ['ゼウス']
    assert not mem.get('recruit_roster_recheck')
    assert p.summary(mem)['generals_lost'] is None


def test_hp_defeat_without_a_survey_entry_still_invalidates_membership():
    mem = completed()
    _defeat(mem, ally='ヴィーナス')
    assert 'roster_survey' not in mem and mem['recruit_roster_recheck'] is True


def test_a_defeated_general_still_listed_stays_in_the_survey():
    mem = completed()
    _defeat(mem)
    mem['house'] = {'phase': 'roster', 'chapter': 1, 'roster_month': '1-6', 'month_scan': False,
                    'age': 0, 'total': 0, 'seen': [], 'pending': [], 'from_survey': True,
                    'status_targets': ['ゼウス']}
    page = parse(roster(NAMES))
    assert house.step(page, mem, None) == [p.pad('down')]
    assert mem['roster_survey']['defeated'] == ['ゼウス']
    assert 'ゼウス' in mem['roster_survey']['statuses']


def test_a_defeated_general_missing_from_the_whole_list_leaves_only_that_name():
    mem = completed()
    survey_names = list(mem['roster_survey']['receipt']['names'])
    _defeat(mem)
    mem['house'] = {'phase': 'roster', 'chapter': 1, 'roster_month': '1-6', 'month_scan': False,
                    'age': 0, 'total': 0, 'seen': [], 'pending': [], 'from_survey': True,
                    'status_targets': ['ゼウス']}
    page = parse(roster(['どうし']))
    assert house.step(page, mem, None) == []
    assert mem['house']['status_targets'] == []
    assert any(rec['decision'] == 'house_roster_defeated_gone' for rec in mem['_records'])
    survey = r.completed_survey(mem)
    assert survey is not None and survey['receipt']['names'] == ['どうし']
    assert set(survey['statuses']) == {'どうし'} and not survey['dirty']
    assert survey_names == NAMES and not mem.get('recruit_roster_recheck')
    assert r.fresh(mem)['names'] == ['どうし']


def test_an_undefeated_target_is_never_dropped_from_the_survey():
    mem = completed()
    r.dirty_status(mem, ['ゼウス'])
    mem['house'] = {'phase': 'roster', 'chapter': 1, 'roster_month': '1-6', 'month_scan': False,
                    'age': 0, 'total': 0, 'seen': [], 'pending': [], 'from_survey': True,
                    'status_targets': ['ゼウス']}
    assert house.step(parse(roster(['どうし'])), mem, None) == [p.pad('down')]
    assert 'ゼウス' in mem['roster_survey']['statuses']


def test_a_walk_not_started_at_the_top_cannot_prove_a_defeated_general_gone():
    mem = completed()
    _defeat(mem)
    mem['house'] = {'phase': 'roster', 'chapter': 1, 'roster_month': '1-6', 'month_scan': False,
                    'age': 0, 'total': 0, 'seen': [], 'pending': [], 'from_survey': True,
                    'status_targets': ['ゼウス'], 'walk_names': ['どうし'], 'walk_from_top': False}
    assert house.step(parse(roster(['どうし'])), mem, None) == [p.pad('down')]
    assert 'ゼウス' in mem['roster_survey']['statuses']


def test_a_target_passed_on_the_way_down_is_reached_going_up():
    mem = completed()
    r.dirty_status(mem, ['ゼウス'])
    mem['house'] = {'phase': 'roster', 'chapter': 1, 'roster_month': '1-6', 'month_scan': False,
                    'age': 0, 'total': 0, 'seen': [], 'pending': [], 'from_survey': True,
                    'status_targets': ['ゼウス'], 'walk_names': ['ゼウス']}
    assert house.step(parse(roster(['どうし'])), mem, None) == [p.pad('up')]


def test_unknown_hp_is_a_dirty_field_even_when_membership_is_complete():
    mem = completed()
    row = deepcopy(mem['house_eggs']['ゼウス'])
    row.update(hp=None, max_hp=None)
    r.status_observed(mem, row)
    assert r.fresh(mem)['complete'] is True
    assert mem['roster_survey']['dirty'] == {'ゼウス': ['hp']}


def test_an_unread_location_refreshes_hp_and_egg_but_keeps_only_location_dirty():
    mem = completed()
    r.dirty_status(mem, ['ゼウス'])
    screen = parse(status('ゼウス', egg='エラベルエッグ4'))
    screen.text = screen.text.replace('しろのなかにいます', '')
    info = house._observe_status(screen, mem)
    assert info['location_observed'] is False
    assert mem['roster_survey']['dirty'] == {'ゼウス': ['location']}
    house._observe_status(parse(status('ゼウス', egg='エラベルエッグ4')), mem)
    assert not mem['roster_survey']['dirty']


def test_recall_proposal_does_not_reread_status_until_matching_input_receipt():
    mem = completed()
    state = {'general': 'ゼウス', 'target': [1, 2]}
    p._request_recall(mem, state, 'アルマムーン', [p.pad('a')])
    assert not mem['roster_survey']['dirty']
    state['request_trace'] = {'decision_id': 'synthetic-1'}
    mem['_recall_inputs'] = {'request_trace': {'decision_id': 'another'}, 'a_inputs': 1}
    p._recall_dispatch_step(Screen([], None, '', kind='text'), mem, state)
    assert not mem['roster_survey']['dirty']
    mem['_recall_inputs']['request_trace'] = dict(state['request_trace'])
    p._recall_dispatch_step(Screen([], None, '', kind='text'), mem, state)
    assert mem['roster_survey']['dirty'] == {'ゼウス': ['hp', 'location']}
    assert state['input_sent'] is True


def test_house_return_to_map_invalidates_only_the_moving_general():
    mem = completed()
    mem['house'] = {'phase': 'return_done', 'general': 'ゼウス',
                    'return_goal': 'アルマムーン', 'pending': [], 'seen': NAMES}
    house._repair_step(MAP, mem, None)
    assert mem['roster_survey']['dirty'] == {'ゼウス': ['hp', 'location']}


def test_unbound_old_month_marker_and_incomplete_statuses_never_create_a_snapshot():
    mem = completed()
    mem.pop('roster_survey')
    mem['roster_survey_scope'] = [1, '1-6']
    assert r.completed_survey(mem) is None and r.surveyed(mem) is False
    mem['house'] = {'phase': 'status', 'chapter': 1, 'seen': ['どうし']}
    r.begin(mem, mem['house'])
    r.page(mem, NAMES)
    assert r.complete(mem, mem['house']) is False
    assert 'roster_survey' not in mem


@pytest.mark.parametrize('value', [None, [], {'どうし': [{}]}, {'どうし': ['invented']}])
def test_malformed_dirty_metadata_is_unknown_instead_of_crashing_or_reusing(value):
    mem = completed()
    mem['roster_survey']['dirty'] = value
    assert r.completed_survey(mem) is None


def test_field_reuse_keeps_the_two_attempt_budget_and_unknown_statuses_stay_dirty():
    mem = completed()
    r.dirty_status(mem, ['ゼウス'], fields=('hp',))
    for expected in (1, 2):
        assert house.step(MAP, mem, None) == [p.pad('x')]
        assert mem['recruit_field_scan_attempts']['count'] == expected
        house._finish(mem)
        mem['tick'] += house.SCAN_INTERVAL
    assert house.step(MAP, mem, None) is None
    assert mem['roster_survey']['dirty'] == {'ゼウス': ['hp']}
