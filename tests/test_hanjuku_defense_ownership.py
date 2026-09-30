"""A newer measured castle flag must survive an older defender HP result."""
import pytest
from docich import hanjuku_policy as policy
from docich.hanjuku_screen import Battle, Screen


def memory():
    return {'chapter': 1, 'captured': ['スペンソニア'],
            'garrison': {'スペンソニア': ['シェーブル', 'ヴィーナス']},
            'battle': {'ally': 'シェーブル', 'enemy': 'クミン', 'ally_hp': 0,
                       'enemy_hp': 3, 'start_ally_hp': 27, 'start_enemy_hp': 24,
                       'side': 'defense', 'castle': 'スペンソニア', 'cards_used': []}}


def finish(mem):
    policy.battle_end(mem, 'map')
    policy.battle_end(mem, 'map')
    return next(r for r in reversed(mem['_records']) if r['decision'] == 'battle_result')


def test_post_defeat_own_flag_preserves_castle_and_other_defenders_but_not_loser_location():
    mem = memory()
    # g508: the world-map flag was read twice after old defender HP0,
    # then the later normal map finalized that same battle.
    policy._apply_world_flags(mem, {'スペンソニア': 'own'})
    policy._apply_world_flags(mem, {'スペンソニア': 'own'})
    result = finish(mem)
    assert mem['captured'] == ['スペンソニア'] and not mem.get('lost')
    assert mem['garrison']['スペンソニア'] == ['ヴィーナス']
    assert mem['general_location_unknown'] == ['シェーブル']
    assert result['outcome'] == 'loss'
    assert result['resulting_event'] == 'defense_retained:スペンソニア'
    assert result['observed_metric']['castle_owner_after_defeat'] == 'own'
    assert result['observed_metric']['general_loss'] == 'unclassified'
    assert mem['stats']['losses'] == 1 and mem['stats']['generals_lost'] is None


@pytest.mark.parametrize('initial_owner', [None, 'own'])
def test_latest_post_defeat_enemy_flag_confirms_castle_loss(initial_owner):
    mem = memory()
    if initial_owner:
        policy._apply_world_flags(mem, {'スペンソニア': initial_owner})
    policy._apply_world_flags(mem, {'スペンソニア': 'enemy'})
    result = finish(mem)
    assert mem['captured'] == [] and mem['lost'] == ['スペンソニア']
    assert 'スペンソニア' not in mem['garrison']
    assert result['resulting_event'] == 'lost:スペンソニア'
    assert result['observed_metric']['castle_owner_after_defeat'] == 'enemy'


def test_flag_read_before_zero_hp_cannot_preserve_a_later_defeat():
    mem = memory();mem['battle']['ally_hp'] = 27
    policy._apply_world_flags(mem, {'スペンソニア': 'own'})
    assert 'defeat_owner' not in mem['battle']
    mem['battle']['ally_hp'] = 0
    assert finish(mem)['resulting_event'] == 'lost:スペンソニア'


@pytest.mark.parametrize('change,flags', [({'side': 'attack'}, {'スペンソニア': 'own'}),
    ({'side': None}, {'スペンソニア': 'own'}),
    ({'castle': None}, {'スペンソニア': 'own'}),
    ({'castle': '前章の城'}, {'前章の城': 'own'}),
    ({'ally_hp': False}, {'スペンソニア': 'own'}),
    ({'enemy_hp': None}, {'スペンソニア': 'own'}),
    ({'enemy_hp': 0}, {'スペンソニア': 'own'}),
    ({}, {'スペンソニア': 'unknown'}), ({}, {'ゴーメン': 'own'})])
def test_only_known_same_castle_defense_after_actual_zero_binds_receipt(change,flags):
    mem = memory();mem['battle'].update(change)
    policy._apply_world_flags(mem, flags)
    assert 'defeat_owner' not in mem['battle']


@pytest.mark.parametrize('receipt', [
    {'castle': 'ゴーメン', 'chapter': 1, 'owner': 'own'},
    {'castle': 'スペンソニア', 'chapter': 2, 'owner': 'own'},
    {'castle': 'スペンソニア', 'chapter': 1, 'owner': 'unknown'}])
def test_foreign_or_unknown_receipt_cannot_override_defeat(receipt):
    mem = memory();mem['battle']['defeat_owner'] = receipt
    assert finish(mem)['resulting_event'] == 'lost:スペンソニア'


def test_new_complete_combat_panel_invalidates_old_post_defeat_flag():
    mem = memory()
    policy._apply_world_flags(mem, {'スペンソニア': 'own'})
    screen = Screen(lines=[], hand=None, text='', kind='battle',
                    battle=Battle('クミン', 3, 'シェーブル', 1))
    policy.battle_step(screen, mem)
    assert 'defeat_owner' not in mem['battle']
    mem['battle']['ally_hp'] = 0
    assert finish(mem)['resulting_event'] == 'lost:スペンソニア'


def test_ownership_receipt_is_not_carried_into_the_next_defender_battle():
    mem = memory()
    policy._apply_world_flags(mem, {'スペンソニア': 'own'})
    finish(mem)
    mem['attack'] = {'side': 'defense', 'castle': 'スペンソニア', 'step': None}
    screen = Screen(lines=[], hand=None, text='', kind='battle',
                    battle=Battle('クミン', 3, 'ヴィーナス', 82))
    policy.battle_step(screen, mem);policy.battle_step(screen, mem)
    assert 'defeat_owner' not in mem['battle']
    mem['battle']['ally_hp'] = 0
    assert finish(mem)['resulting_event'] == 'lost:スペンソニア'


@pytest.mark.parametrize('change', [{'castle': '前章の城'}, {'ally_hp': False}, {'enemy_hp': True}])
def test_saved_receipt_is_revalidated_against_known_castle_and_integer_hp(change):
    mem = memory();mem['battle'].update(change)
    mem['battle']['defeat_owner'] = {'castle': mem['battle']['castle'], 'chapter': 1, 'owner': 'own'}
    result = finish(mem)
    assert result['resulting_event'].startswith('lost:')
    assert 'castle_owner_after_defeat' not in result['observed_metric']


def test_measured_all_owned_survives_defender_loss_and_keeps_real_boss_gate_ready():
    from copy import deepcopy
    from docich import hanjuku_chart as chart
    mem = memory()
    mem['captured'] = [c for c in chart.castles(1)
                       if c not in (chart.home_castle(1), chart.boss_castle(1))]
    order = next(o for o in chart.orders(1) if o['step'] == '1-B1')
    without_flag = deepcopy(mem)
    finish(without_flag)
    assert not policy._ready(order, without_flag)
    policy._apply_world_flags(mem, {c: 'own' for c in mem['captured']})
    finish(mem)
    assert policy._ready(order, mem)
