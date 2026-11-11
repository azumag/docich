"""Same-run/month complete status surveys survive a plain field return."""
from copy import deepcopy

import pytest

from docich import hanjuku_house as house, hanjuku_policy as p, hanjuku_roster as r
from docich.hanjuku_bot import decide
from docich.hanjuku_font import TextLine
from docich.hanjuku_screen import Battle, Screen, parse
from test_hanjuku_chart_bot import month_canvas
from test_hanjuku_house import roster, status
from docich.hanjuku.state.regions import flow as _flow, journal as _journal

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
    _journal(mem)['_recall_inputs'] = {'request_trace': {'decision_id': 'another'}, 'a_inputs': 1}
    p._recall_dispatch_step(Screen([], None, '', kind='text'), mem, state)
    assert not mem['roster_survey']['dirty']
    _journal(mem)['_recall_inputs']['request_trace'] = dict(state['request_trace'])
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


def test_partial_survey_roster_next_hop_acts_on_the_same_frame():
    """Issue #3194 (18): after the status screen closes, the next target is
    walked toward on the frame that already shows the list, not one idle
    observation later."""
    mem = completed()
    _defeat(mem)
    mem['house'] = {'phase': 'roster_next', 'chapter': 1, 'roster_month': '1-6', 'month_scan': False,
                    'age': 0, 'total': 0, 'seen': [], 'pending': [], 'from_survey': True,
                    'status_targets': ['ゼウス'], 'selected': 'どうし'}
    assert house.step(parse(roster(NAMES)), mem, None) == [p.pad('down')]
    assert mem['house']['phase'] == 'roster'
    assert any(rec['decision'] == 'house_input_free_hop' for rec in mem['_records'])


def test_input_free_hop_never_chains_a_second_pad_in_one_observation():
    mem = completed()
    _defeat(mem)
    mem['house'] = {'phase': 'roster_next', 'chapter': 1, 'roster_month': '1-6', 'month_scan': False,
                    'age': 0, 'total': 0, 'seen': [], 'pending': [], 'from_survey': True,
                    'status_targets': ['ゼウス'], 'selected': 'どうし'}
    actions = house.step(parse(roster(NAMES)), mem, None)
    assert len(actions) == 1
    # A non-roster frame must not hop: the phase stays until a list is seen.
    mem['house']['phase'] = 'roster_next'
    assert house.step(parse(status('ゼウス')), mem, None) == []
    assert mem['house']['phase'] == 'roster_next'
def test_observation_cost_tally_counts_status_opens_and_stays_bounded():
    """Issue #3194 (36): counts only, capped in keys and generals."""
    mem = {}
    for i in range(house.TALLY_GENERALS + 10):
        house._tally(mem, 'status_open_full', f'g{i}')
    assert mem['obs_cost']['status_open_full']['n'] == house.TALLY_GENERALS + 10
    assert len(mem['obs_cost']['status_open_full']['by_general']) == house.TALLY_GENERALS
    for i in range(house.TALLY_KEYS + 5):
        house._tally(mem, f'k{i}')
    assert len(mem['obs_cost']) == house.TALLY_KEYS


def test_partial_status_read_is_tallied_per_general():
    mem = completed()
    _defeat(mem)
    mem['house'] = {'phase': 'status', 'chapter': 1, 'roster_month': '1-6', 'month_scan': False,
                    'age': 0, 'total': 0, 'seen': [], 'pending': [], 'from_survey': True,
                    'status_targets': ['ゼウス'], 'selected': 'ゼウス'}
    house.step(parse(status('ゼウス', 'エラベルエッグ4')), mem, None)
    assert mem['obs_cost']['status_open_partial']['by_general'] == {'ゼウス': 1}


def test_visible_dirty_target_is_read_before_walking_to_an_offscreen_head():
    """Issue #3194 (12): registration order carries no urgency."""
    names = ['どうし', 'ゼウス', 'ヴィーナス', 'ヘルメス']
    pick = house._nearest_visible_target
    assert pick(['アポロ', 'ヘルメス', 'ゼウス'], names, 'ヴィーナス') in {'ヘルメス', 'ゼウス'}
    assert pick(['アポロ', 'ヘルメス', 'どうし'], names, 'ヴィーナス') == 'ヘルメス'
    # A visible head is never displaced, so the oldest target cannot starve.
    assert pick(['どうし', 'ヘルメス'], names, 'ヘルメス') == 'どうし'
    # Nothing visible (or an unreadable cursor) keeps the existing head search.
    assert pick(['アポロ', 'ゼウス'], ['どうし'], 'どうし') == 'アポロ'
    assert pick(['アポロ', 'ゼウス'], names, '??') == 'アポロ'


def _walk_cost(names):
    """Observations and inputs of one full status walk on synthetic frames."""
    from test_hanjuku_house import memory
    mem = memory('roster')
    r.begin(mem, mem['house'])
    steps = []

    def feed(frame):
        actions = house.step(parse(frame), mem, None)
        steps.append(actions)
        return actions
    feed(roster(names, 0))
    for i, name in enumerate(names):
        feed(status(name, 'エラベルエッグ4'))
        feed(roster(names, i))
        feed(roster(names, (i + 1) % len(names)))
    return steps


@pytest.mark.parametrize('count', [2, 3, 4])
def test_full_status_walk_never_spends_an_observation_without_input(count):
    """Issue #3194 (39): operation-count regression guard.

    The walk reads each general in three observations (A, B, Down) and the
    wrapped list closes with one B; an observation that issues nothing means a
    state hop is waiting for a frame it does not need (cf. #3194 item 18).
    """
    names = ['どうし', 'ゼウス', 'ヴィーナス', 'ヘルメス'][:count]
    steps = _walk_cost(names)
    assert len(steps) == 3 * count + 1
    assert all(len(actions) == 1 for actions in steps)


def _partial_walk_memory(targets):
    mem = completed()
    r.dirty_status(mem, targets)
    mem['house'] = {'phase': 'roster', 'chapter': 1, 'roster_month': '1-6', 'month_scan': False,
                    'age': 0, 'total': 0, 'seen': [], 'pending': [], 'from_survey': True,
                    'status_targets': list(r.recheck_targets(mem))}
    return mem


def test_target_read_on_another_screen_is_dropped_from_the_partial_walk():
    """Issue #3194 (05/16): do not open a status that was already re-read."""
    mem = _partial_walk_memory(['ゼウス'])
    assert mem['house']['status_targets'] == ['ゼウス']
    # A different flow reads ゼウス completely while this walk is waiting.
    assert house._observe_status(parse(status('ゼウス', 'エラベルエッグ4')), mem)['general'] == 'ゼウス'
    assert r.recheck_targets(mem) == []
    assert house.step(parse(roster(NAMES)), mem, None) == [p.pad('b')]
    assert mem['house']['phase'] == 'leave_roster'
    assert mem['obs_cost']['target_satisfied_elsewhere']['n'] == 1


def test_unsatisfied_targets_are_kept_and_walked_as_before():
    mem = _partial_walk_memory(['ゼウス'])
    assert house.step(parse(roster(NAMES)), mem, None) == [p.pad('down')]
    assert mem['house']['status_targets'] == ['ゼウス']
    assert 'target_satisfied_elsewhere' not in mem.get('obs_cost', {})


def test_only_the_satisfied_target_is_dropped_when_two_are_pending():
    names = ['どうし', 'ゼウス']
    mem = _partial_walk_memory(names)
    house._observe_status(parse(status('ゼウス', 'エラベルエッグ4')), mem)
    house.step(parse(roster(NAMES)), mem, None)
    assert mem['house']['status_targets'] == ['どうし']


# ---- Issue #3194 (06/10): month-boundary carry-over (flag, default off) ----

def _next_month(mem, month='1-7', names=NAMES, tick=900):
    """A new month with its own complete names-only roster count."""
    mem.update(month=month, tick=tick)
    mem['house'] = {'phase': 'roster', 'chapter': 1, 'month_scan': True, 'seen': [], 'pending': [],
                    'age': 0, 'total': 0}
    r.begin(mem, mem['house'])
    r.page(mem, list(names))
    mem['recruit_roster']['wages'] = {n: r.fixed_wage(n) or 0 for n in names}
    mem['recruit_roster']['complete'] = True
    mem.pop('house')
    for castle in list(mem.get('castle_income', {})):
        mem['castle_income'][castle].update(month=month, tick=tick)
    return mem


@pytest.fixture
def carry_on(monkeypatch):
    monkeypatch.delenv(r.MONTH_CARRY_ENV, raising=False)  # on by default


def test_month_carry_is_on_by_default_and_the_kill_switch_turns_it_off(monkeypatch):
    monkeypatch.delenv(r.MONTH_CARRY_ENV, raising=False)
    assert r.month_carry_enabled() is True
    assert r.carry_forward(_next_month(completed())) is not None
    monkeypatch.setenv(r.MONTH_CARRY_ENV, '0')
    mem = _next_month(completed())
    assert r.carry_forward(mem) is None
    assert r.reusable_survey(mem) is None


def test_healthy_castle_residents_are_carried_and_only_the_new_member_is_opened(carry_on):
    mem = completed()
    mem = _next_month(mem, names=NAMES + ['ヴィーナス'])
    out = r.carry_forward(mem)
    assert out == {'carried': NAMES, 'reopen': ['ヴィーナス']}
    assert r.recheck_targets(mem) == ['ヴィーナス']
    assert r.completed_survey(mem) is not None
    row = mem['house_eggs']['ゼウス']
    assert row['month'] == '1-7' and row['carried_from'] == '1-6' and row['carry_depth'] == 1
    assert row['location'] == 'castle' and row['location_observed'] is True


def test_everyone_carried_means_no_status_screen_is_opened(carry_on):
    mem = _next_month(completed())
    assert r.carry_forward(mem)['reopen'] == []
    assert r.recheck_targets(mem) == []
    mem['obs_cost'] = {}
    assert house.step(MAP, mem, None) is None  # back to the field, no survey walk
    assert 'house' not in mem or not mem.get('house')


def test_a_general_with_an_event_since_the_last_read_is_reopened(carry_on):
    mem = completed()
    r.dirty_status(mem, ['ゼウス'], fields=('egg',))
    mem = _next_month(mem)
    out = r.carry_forward(mem)
    assert out == {'carried': ['どうし'], 'reopen': ['ゼウス']}
    assert r.recheck_targets(mem) == ['ゼウス']


@pytest.mark.parametrize('change', ['hp', 'field', 'broken', 'unobserved'])
def test_only_unknown_location_or_unobserved_castle_requires_a_fresh_status(carry_on, change):
    mem = completed()
    row = mem['roster_survey']['statuses']['ゼウス']
    if change == 'hp':
        row['hp'] = row['max_hp'] - 1
    elif change == 'field':
        row['location'] = 'field'
    elif change == 'broken':
        row['broken'] = True
    else:
        row['location_observed'] = False
    mem = _next_month(mem)
    if change in ('field', 'unobserved'):
        assert r.carry_forward(mem) == {'carried': ['どうし'], 'reopen': ['ゼウス']}
    else:
        assert r.carry_forward(mem) == {'carried': NAMES, 'reopen': []}


def test_a_row_is_carried_after_any_number_of_unchanged_months(carry_on):
    mem = completed()
    for month in ('1-7', '1-8', '1-9', '1-10', '1-11'):
        mem = _next_month(mem, month=month, tick=mem['tick'] + 400)
        out = r.carry_forward(mem)
        assert out == {'carried': NAMES, 'reopen': []}, month
    assert mem['house_eggs']['ゼウス']['carry_depth'] == 5


def test_a_gap_in_months_or_another_run_or_chapter_is_not_carried(carry_on):
    mem = _next_month(completed(), month='1-8')       # 1-7 was never surveyed
    assert r.carry_forward(mem) is None
    mem = completed(); mem['roster_survey']['identity'] = {**IDENTITY, 'runtime_id': 'other'}
    assert r.carry_forward(_next_month(mem)) is None
    mem = completed()
    mem = _next_month(mem); mem['chapter'] = 2
    assert r.carry_forward(mem) is None


def test_carry_over_needs_this_months_complete_names_only_count(carry_on):
    mem = completed()
    mem.update(month='1-7', tick=900)                  # no new roster count yet
    assert r.carry_forward(mem) is None
    assert r.reusable_survey(mem) is None


# ---- Issue #3194 (07): a join keeps the rows that were already read ----

def _recount(mem, names):
    """The names-only count a join forces again inside the same month."""
    mem['house'] = {'phase': 'roster', 'chapter': 1, 'month_scan': True, 'seen': [], 'pending': [],
                    'age': 0, 'total': 0}
    r.begin(mem, mem['house'])
    r.page(mem, house.roster(parse(roster(list(names)))))
    state = mem['house']
    state['seen'] = list(names)
    state['roster_end_observed'] = True
    complete = r.complete(mem, state)
    mem['recruit_roster']['wages'] = {n: r.fixed_wage(n) or 0 for n in names}
    mem.pop('house')
    return complete


def test_a_join_after_the_carry_still_carries_the_others_on_the_next_count(carry_on):
    mem = _next_month(completed())
    assert r.carry_forward(mem)['reopen'] == []
    joined = NAMES + ['ヴィーナス']
    r.observed_member(mem, joined)
    assert r.reusable_survey(mem) is None            # membership is unknown again
    assert _recount(mem, joined) is True              # and the count can finish (g1097 18:35)
    assert r.carry_forward(mem) == {'carried': NAMES, 'reopen': ['ヴィーナス']}
    assert r.recheck_targets(mem) == ['ヴィーナス']
    row = mem['house_eggs']['ゼウス']
    assert row['carried_from'] == '1-6' and row['carry_depth'] == 1   # not counted twice
    assert 'roster_carry_source' not in mem


def test_a_join_after_a_full_read_reuses_this_months_rows(carry_on):
    mem = completed()                                 # read in full this month
    joined = NAMES + ['ヴィーナス']
    r.observed_member(mem, joined)
    assert _recount(mem, joined) is True
    assert r.carry_forward(mem) == {'carried': NAMES, 'reopen': ['ヴィーナス']}
    assert 'carried_from' not in mem['house_eggs']['ゼウス']


def test_an_event_between_the_join_and_the_count_reopens_that_general(carry_on):
    mem = completed()
    joined = NAMES + ['ヴィーナス']
    r.observed_member(mem, joined)
    r.dirty_status(mem, ['ゼウス'], fields=('egg',))   # a battle or recovery in between
    _recount(mem, joined)
    assert r.carry_forward(mem) == {'carried': ['どうし'], 'reopen': ['ゼウス', 'ヴィーナス']}


def test_the_waiting_rows_are_dropped_by_other_invalidations_and_by_later_months(carry_on):
    joined = NAMES + ['ヴィーナス']
    mem = completed(); r.observed_member(mem, joined)
    assert 'roster_carry_source' in mem
    r.defeated(mem, 'ゼウス')                           # no survey to keep: forget everything
    assert 'roster_carry_source' not in mem
    mem = completed(); r.observed_member(mem, joined)
    mem = _next_month(mem, month='1-8')               # a month later: too old to reuse
    assert r.carry_forward(mem) is None
    assert 'roster_carry_source' not in mem


def test_names_already_on_the_retained_bill_do_not_invalidate_again(carry_on):
    mem = completed()
    joined = NAMES + ['ヴィーナス']
    r.observed_member(mem, joined)
    hold = deepcopy(mem['payroll_membership_hold'])
    r.observed_member(mem, joined)                    # a later page of the same count
    assert mem['payroll_membership_hold'] == hold
    assert mem['roster_carry_source']['scope'] == [1, '1-6']
    r.observed_member(mem, joined + ['ミント'])        # a different new name is a new join
    assert 'ミント' in mem['payroll_membership_hold']['wages']


def test_the_kill_switch_keeps_no_waiting_rows(monkeypatch):
    monkeypatch.setenv(r.MONTH_CARRY_ENV, '0')
    mem = completed()
    r.observed_member(mem, NAMES + ['ヴィーナス'])
    assert 'roster_carry_source' not in mem


# ---- Issue #3194 (36): which observations send nothing, and while on what ----
def test_idle_observations_are_counted_by_screen_text_and_stay_bounded():
    from docich.hanjuku import orchestrator as o
    mem = {}
    screen = Screen([], None, 'ロックフォールしょうぐんがてきとそうぐうしました!!', kind='text')
    for _ in range(3):
        o._tally_observation(mem, 'text', screen, [])
    o._tally_observation(mem, 'text', screen, [p.pad('a')])        # sent something: not idle
    o._tally_observation(mem, 'map', Screen([], None, '', kind='map'), [])   # idle, but not text
    cost = mem['obs_cost']
    assert cost['observations']['n'] == 5 and cost['idle_text']['n'] == 3
    assert cost['idle_text']['by_text'] == {'text:ロックフォールしょうぐんがてきと': 3}
    for i in range(o.IDLE_TEXT_KEYS + 10):
        o._tally_observation(mem, 'unknown', Screen([], None, f'もじ{i}', kind='unknown'), [])
    assert len(cost['idle_text']['by_text']) <= o.IDLE_TEXT_KEYS
    assert all(len(k) <= len('unknown:') + o.IDLE_TEXT_HEAD for k in cost['idle_text']['by_text'])


# ---- Issue #3194 (36 の残り): purpose split, fields, cache hit/miss, ticks ----
def test_tally_status_open_counts_purpose_fields_and_miss():
    """Every deliberate open is a cache_miss and records the fields it read."""
    mem = {}
    info = {'general': 'ゼウス', 'hp': 40, 'max_hp': 80, 'uses': 3,
            'location_observed': False}
    house._tally_status_open(mem, 'dispatch', info, ticks=7)
    cost = mem['obs_cost']
    assert cost['status_open_dispatch'] == {'n': 1, 'ticks': 7, 'by_general': {'ゼウス': 1}}
    assert cost['cache_miss']['by_general'] == {'ゼウス': 1}
    # egg is mandatory for a parse; location was not observed on this panel.
    assert cost['status_fields_read']['by_general'] == {'egg': 1, 'hp': 1}


def test_partial_status_read_tallies_purpose_ticks_fields_and_miss():
    mem = completed()
    _defeat(mem)
    mem['house'] = {'phase': 'status', 'chapter': 1, 'roster_month': '1-6', 'month_scan': False,
                    'age': 4, 'total': 0, 'seen': [], 'pending': [], 'from_survey': True,
                    'status_targets': ['ゼウス'], 'selected': 'ゼウス'}
    house.step(parse(status('ゼウス', 'エラベルエッグ4')), mem, None)
    cost = mem['obs_cost']
    assert cost['status_open_partial']['n'] == 1
    # step() bumps age before the phase handler: 4 waiting + 1 read observation.
    assert cost['status_open_partial']['ticks'] == 5
    assert cost['cache_miss']['by_general'] == {'ゼウス': 1}
    assert cost['status_fields_read']['by_general'] == {'egg': 1, 'hp': 1, 'location': 1}


def test_a_satisfied_target_is_a_cache_hit():
    mem = _partial_walk_memory(['ゼウス'])
    house._observe_status(parse(status('ゼウス', 'エラベルエッグ4')), mem)
    house.step(parse(roster(NAMES)), mem, None)
    assert mem['obs_cost']['target_satisfied_elsewhere']['n'] == 1
    assert mem['obs_cost']['cache_hit']['by_general'] == {'ゼウス': 1}


def test_leave_roster_tallies_roster_completeness_and_observations():
    mem = completed()
    mem['house'] = {'phase': 'leave_roster', 'chapter': 1, 'roster_month': '1-6',
                    'month_scan': False, 'age': 0, 'total': 17,
                    'seen': list(NAMES), 'pending': [], 'from_survey': True}
    house.step(MAP, mem, None)
    assert mem['obs_cost']['roster_complete']['n'] == 1
    rec = next(r for r in mem['_records'] if r['decision'] == 'house_scan_complete')
    # total counts this closing observation too: 17 earlier + the map frame.
    assert rec['observed_metric']['observations'] == 18


# ---- Issue #3326 (34 ①): sortie attempts per (target, purpose) ----
def test_sortie_attempt_tally_counts_outcomes_and_state_change():
    mem = {'gold': 250, 'garrison': {'城A': ['ゼウス', 'どうし']},
           'house_eggs': {'ゼウス': {'hp': 80, 'location': 'castle'}}}
    sig = house._sortie_state_sig(mem, '城A', 'ゼウス')
    assert sig == [80, 'castle', 250, 2]
    house._tally_sortie_attempt(mem, '砦B', 'attack', 'departed', sig)
    # Same key, identical signature: an unchanged retry (what #34 hunts).
    house._tally_sortie_attempt(mem, '砦B', 'attack', 'cancelled_unsent', sig)
    # Changed signature: legitimate re-confirmation, not a blind retry.
    mem['gold'] = 100
    house._tally_sortie_attempt(mem, '砦B', 'attack', 'cancelled_unsent',
                                house._sortie_state_sig(mem, '城A', 'ゼウス'))
    # A different purpose at the same fort is a different key.
    house._tally_sortie_attempt(mem, '砦B', 'move', 'released',
                                house._sortie_state_sig(mem, '城A', 'ゼウス'))
    rows = mem['obs_cost']['sortie_retry']
    assert rows['n'] == 4
    attack = rows['by_key']['砦B|attack']
    assert attack['n'] == 3
    assert attack['departed'] == 1 and attack['cancelled_unsent'] == 2
    assert attack['unchanged'] == 1 and attack['changed'] == 1
    assert rows['by_key']['砦B|move']['released'] == 1


def test_sortie_attempt_keys_stay_bounded():
    mem = {}
    for i in range(house.SORTIE_ATTEMPT_KEYS + 5):
        house._tally_sortie_attempt(mem, f'砦{i}', 'attack', 'departed', [1, 'castle', 0, 0])
    entry = mem['obs_cost']['sortie_retry']
    assert entry['n'] == house.SORTIE_ATTEMPT_KEYS + 5
    assert len(entry['by_key']) == house.SORTIE_ATTEMPT_KEYS
    # Rejected inserts are not silent: saturation is distinguishable from ≈0.
    assert mem['obs_cost_dropped']['sortie_retry_key'] == 5


def test_sortie_attempt_outer_key_saturation_is_counted():
    # Fill every obs_cost slot without sortie_retry: attempts still happen,
    # but no row can be created. The drop counter must not return them as 0.
    mem = {'obs_cost': {f'k{i}': {'n': 1} for i in range(house.TALLY_KEYS)}}
    house._tally_sortie_attempt(mem, '砦B', 'attack', 'cancelled_unsent', [1, 'castle', 0, 0])
    house._tally_sortie_attempt(mem, '砦B', 'attack', 'departed', [1, 'castle', 0, 0])
    assert 'sortie_retry' not in mem['obs_cost']
    assert mem['obs_cost_dropped']['sortie_retry'] == 2

# ---- Issue #3326 (34 stage 3): cancel→retry round-trip pairs ----
def test_sortie_cancel_retry_pair_counts_gap_and_signature():
    mem = {'tick': 100, 'gold': 250,
           'garrison': {'城A': ['ゼウス', 'どうし']},
           'house_eggs': {'ゼウス': {'hp': 80, 'location': 'castle'}}}
    sig = house._sortie_state_sig(mem, '城A', 'ゼウス')
    house._tally_sortie_attempt(mem, '砦B', 'attack', 'cancelled_unsent', sig)
    # An attempt before any cancel is not a round trip.
    mem['tick'] = 108
    house._tally_sortie_attempt(mem, '砦B', 'attack', 'departed', sig)
    row = mem['obs_cost']['sortie_retry']['by_key']['砦B|attack']
    pairs = row['pairs']
    assert pairs['n'] == 1
    assert pairs['same_sig'] == 1 and 'diff_sig' not in pairs
    assert pairs['departed'] == 1
    assert pairs['gap_sum'] == 8 and pairs['gap_max'] == 8
    assert pairs['gap_le_10'] == 1
    # The departed retry closed the pair: nothing is pending, so the next
    # attempt starts a new window instead of double-counting the old cancel.
    assert 'cancel_pending' not in row


def test_sortie_pair_rearms_on_consecutive_cancel_and_splits_by_outcome():
    mem = {'tick': 10}
    sig = [50, 'castle', 0, 1]
    house._tally_sortie_attempt(mem, '砦C', 'attack', 'cancelled_unsent', sig)
    # A second cancel at the same key IS the retry: it closes one pair and
    # re-arms with the newer cancel.
    mem['tick'] = 30
    house._tally_sortie_attempt(mem, '砦C', 'attack', 'cancelled_observed', sig)
    # State changed before the next retry: a legitimate re-check, diff_sig.
    mem['tick'] = 120
    house._tally_sortie_attempt(mem, '砦C', 'attack', 'released', [60, 'field', 0, 1])
    pairs = mem['obs_cost']['sortie_retry']['by_key']['砦C|attack']['pairs']
    assert pairs['n'] == 2
    assert pairs['same_sig'] == 1 and pairs['diff_sig'] == 1
    assert pairs['cancelled_observed'] == 1 and pairs['released'] == 1
    # Gaps 20 (10→30) and 90 (30→120) both land in the ≤100 bucket.
    assert pairs['gap_le_100'] == 2 and pairs['gap_sum'] == 110 and pairs['gap_max'] == 90
    # 'released' is not a cancel: the chain ends without a pending pair.
    row = mem['obs_cost']['sortie_retry']['by_key']['砦C|attack']
    assert 'cancel_pending' not in row
    # A different (target, purpose) key never inherits this pending cancel.
    house._tally_sortie_attempt(mem, '砦D', 'attack', 'departed', sig)
    assert 'pairs' not in mem['obs_cost']['sortie_retry']['by_key']['砦D|attack']


# ---- Issue #3326 (34 stage 4): deferral of same_sig short-cycle retries ----
def test_sortie_retry_hold_defers_same_sig_short_cycle_once():
    mem = {'tick': 100, 'gold': 250,
           'garrison': {'城A': ['ゼウス', 'どうし']},
           'house_eggs': {'ゼウス': {'hp': 80, 'location': 'castle'}}}
    sig = house._sortie_state_sig(mem, '城A', 'ゼウス')
    house._tally_sortie_attempt(mem, '砦B', 'attack', 'cancelled_unsent', sig)
    # Same signature, inside the gap_le_100 band: the blind retry is held.
    mem['tick'] = 150
    pending, first = house._sortie_retry_hold(mem, '砦B', 'attack', sig)
    assert pending is not None and pending['deferred'] is True
    assert first is True
    # Re-evaluating the same pending cancel does not recount the deferral.
    held, first = house._sortie_retry_hold(mem, '砦B', 'attack', sig)
    assert held is not None and first is False
    row = mem['obs_cost']['sortie_retry']['by_key']['砦B|attack']
    assert row['deferred'] == 1


def test_sortie_retry_hold_passes_diff_sig_long_gap_and_new_run():
    mem = {'tick': 10}
    sig = [50, 'castle', 0, 1]
    house._tally_sortie_attempt(mem, '砦B', 'attack', 'cancelled_unsent', sig)
    # diff_sig: a legitimate re-check with new evidence is never held.
    mem['tick'] = 40
    assert house._sortie_retry_hold(mem, '砦B', 'attack', [60, 'field', 0, 1]) is None
    # gap past the band: a spaced re-try is not the short-cycle loop.
    mem['tick'] = 200
    assert house._sortie_retry_hold(mem, '砦B', 'attack', sig) is None
    # A different (target, purpose) key has no pending cancel.
    assert house._sortie_retry_hold(mem, '砦C', 'attack', sig) is None
    # A departed terminal leaves no pending cancel to match.
    house._tally_sortie_attempt(mem, '砦D', 'attack', 'departed', sig)
    assert house._sortie_retry_hold(mem, '砦D', 'attack', sig) is None
    # obs_cost dies at a run switch: a fresh mem cannot hold anything.
    assert house._sortie_retry_hold({'tick': 5}, '砦B', 'attack', sig) is None
    row = mem['obs_cost']['sortie_retry']['by_key']['砦B|attack']
    assert 'deferred' not in row


def test_sortie_retry_hold_never_defers_on_a_negative_gap():
    # A rewound tick (run state carried across a reset) puts the recorded
    # cancel in the future: it is not evidence for the measured loop, so a
    # stale cancel_pending must not suppress the retry.
    mem = {'tick': 100}
    sig = [50, 'castle', 0, 1]
    house._tally_sortie_attempt(mem, '砦B', 'attack', 'cancelled_unsent', sig)
    mem['tick'] = 50  # rewind below the cancel tick
    assert house._sortie_retry_hold(mem, '砦B', 'attack', sig) is None
    assert 'deferred' not in         mem['obs_cost']['sortie_retry']['by_key']['砦B|attack']


def test_map_step_defers_then_starts_same_sig_retry():
    # Issue #3326 (34 stage 4): the detector sits on the order-start path.
    # A pending cancel with an unchanged signature inside the gap_le_100
    # band keeps the order pending ([] instead of order_start); once the
    # gap outgrows the band the same pick starts the order.
    from docich.hanjuku.flows import map as M
    from docich import hanjuku_chart as chart
    from docich.hanjuku_screen import Screen
    home = chart.home_castle(1)
    target = next(c for c in chart.castles(1) if c != home)
    mem = {'chapter': 1, 'tick': 200, 'gold': 250, '_records': [],
           'captured': [home], 'select_centered': 199,
           'garrison': {home: ['ゼウス', 'どうし']},
           'house_eggs': {'ゼウス': {'hp': 80, 'location': 'castle'}},
           'chart_plan': {'source': 'jev', 'orders': [
               {'step': 'A:test:M1', 'general': 'ゼウス', 'source': home,
                'target': target, 'cards': [], 'after': None, 'note': 'n'}]},
           'plan': {'orders': {'A:test:M1': 'pending'}}}
    sig = house._sortie_state_sig(mem, home, 'ゼウス')
    # The order carries no explicit purpose: the key is 'target|None'.
    house._tally_sortie_attempt(mem, target, None, 'cancelled_unsent', sig)
    screen = Screen(lines=[], hand=None, text='', kind='map', cursor=None)
    # 40 ticks after the cancel: inside the band, signature unchanged.
    mem['tick'] = 240
    assert M.map_step(screen, mem, None) == []
    assert mem['plan']['orders']['A:test:M1'] == 'pending'
    assert mem['plan'].get('active') is None
    assert 'sortie_retry_deferred' in [r['decision'] for r in mem['_records']]
    assert 'order_start' not in [r['decision'] for r in mem['_records']]
    # Re-evaluation while held defers again but emits no second record.
    mem['tick'] = 280
    assert M.map_step(screen, mem, None) == []
    assert [r['decision'] for r in mem['_records']].count(
        'sortie_retry_deferred') == 1
    # Past the band the same pick is allowed to start the order.
    mem['tick'] = 500
    M.map_step(screen, mem, None)
    assert 'order_start' in [r['decision'] for r in mem['_records']]


def test_month_carry_has_no_fixed_maximum_after_observed_unchanged_months():
    from docich.hanjuku.os_layer import roster as records
    row = {'general': 'ゼウス', 'location': 'castle',
           'location_observed': True, 'broken': False,
           'hp': 50, 'max_hp': 50, 'carry_depth': 99}
    assert records._carry_eligible(row, []) is True
    assert records._carry_eligible(row, ['location']) is False
    row['broken'] = True
    assert records._carry_eligible(row, []) is True
