"""Castle defense continues under the next observed general, not a lost castle."""
from copy import deepcopy

import pytest

from docich import hanjuku_policy as policy
from docich.hanjuku_screen import Battle, Screen


def memory():
    return {'chapter': 1, 'captured': ['ジョンリギ'],
            'garrison': {'ジョンリギ': ['クミン', 'ヴィーナス', 'ミント']},
            'battle': {'enemy': 'シナモン', 'ally': 'クミン', 'enemy_hp': 39,
                       'ally_hp': 0, 'start_enemy_hp': 39, 'start_ally_hp': 27,
                       'castle': 'ジョンリギ', 'side': 'defense', 'step': 'old',
                       'cards_used': [], 'planned_cards': ['キャトルミュ'],
                       'survival': {'exhausted': True}, 'egg_retreat_attempts': 3},
            'egg_battle': True, 'egg_key': 'old', 'egg_choice': {'old': True},
            'indep_menu': 'old', 'monster_panel': 'old'}


def panel(enemy='シナモン', ally='ヴィーナス', enemy_hp=39, ally_hp=82):
    return Screen(lines=[], hand=None, text='', kind='battle',
                  battle=Battle(enemy, enemy_hp, ally, ally_hp))


def test_successor_restarts_inputs_and_preserves_castle_without_inheriting_controls():
    mem = memory()
    assert policy.battle_step(panel(), mem) == []
    assert mem['battle']['ally'] == 'クミン'
    assert policy.battle_step(panel(), mem)
    cur = mem['battle']
    assert (cur['ally'], cur['enemy'], cur['ally_hp'], cur['side'], cur['castle']) == (
        'ヴィーナス', 'シナモン', 82, 'defense', 'ジョンリギ')
    assert cur['step'] is None and cur['planned_cards'] == []
    assert 'survival' not in cur and 'egg_retreat_attempts' not in cur
    assert mem['egg_battle'] is False
    assert all(key not in mem for key in ('egg_key', 'egg_choice', 'indep_menu', 'monster_panel'))
    assert mem['captured'] == ['ジョンリギ'] and not mem.get('lost')
    assert mem['garrison']['ジョンリギ'] == ['ヴィーナス', 'ミント']
    assert mem['general_location_unknown'] == ['クミン']
    result = next(r for r in mem['_records'] if r['decision'] == 'battle_result')
    assert result['ally'] == 'クミン' and result['outcome'] == 'loss'
    assert result['resulting_event'] == 'defense_continues:ジョンリギ'
    assert result['observed_metric']['general_loss'] == 'unclassified'
    assert mem['stats']['generals_lost'] is None
    assert mem['stats']['losses'] == 1


@pytest.mark.parametrize('changes,observation', [
    ({'side': 'attack'}, panel()), ({'side': None}, panel()),
    ({'castle': None}, panel()), ({'ally_hp': 1}, panel()),
    ({'enemy_hp': 0}, panel()), ({}, panel(enemy='ミント')),
    ({}, panel(ally='ダークエルフ', ally_hp=216)),
    ({}, panel(ally='未確認', ally_hp=30)), ({}, panel(ally_hp=83)),
    ({}, panel(ally_hp=0)), ({}, panel(enemy_hp=0)),
])
def test_unsafe_name_changes_do_not_end_previous_battle(changes, observation):
    mem = memory()
    mem['battle'].update(changes)
    policy._migrate_card_evidence(mem)
    before = deepcopy(mem['battle'])
    assert policy.battle_step(observation, mem) == []
    assert policy.battle_step(observation, mem) == []
    assert mem['battle'] == before
    assert mem['captured'] == ['ジョンリギ'] and 'stats' not in mem


@pytest.mark.parametrize('interruption', [panel(ally='ヴィーナス'+policy.UNKNOWN),
    panel(ally_hp=None), panel(ally_hp=-1), panel(ally_hp=True),
    panel(ally='クミン', ally_hp=0), panel(ally='ミント', ally_hp=32)])
def test_partial_or_changed_panel_breaks_consecutive_confirmation(interruption):
    mem = memory()
    policy.battle_step(panel(), mem)
    policy.battle_step(interruption, mem)
    assert policy.battle_step(panel(), mem) == []
    assert mem['battle']['ally'] == 'クミン'
    assert policy.battle_step(panel(), mem)
    assert mem['battle']['ally'] == 'ヴィーナス'


@pytest.mark.parametrize('won', [True, False])
def test_successor_final_result_alone_updates_castle_ownership(won):
    mem = memory()
    policy.battle_step(panel(), mem)
    policy.battle_step(panel(), mem)
    policy.battle_step(panel(enemy_hp=0 if won else 39, ally_hp=82 if won else 0), mem)
    policy.battle_end(mem, 'map')
    policy.battle_end(mem, 'map')
    assert ('ジョンリギ' in mem['captured']) is won
    assert ('ジョンリギ' in mem.get('lost', [])) is (not won)
    assert mem['stats']['losses'] == (1 if won else 2)
    assert mem['stats']['wins'] == (1 if won else 0)


def test_intervening_nonbattle_screen_breaks_confirmation_in_bot(monkeypatch):
    from docich.hanjuku_bot import decide
    from docich.hanjuku_pixels import Frame
    from docich import hanjuku_screen
    mem = memory()
    policy.battle_step(panel(), mem)
    monkeypatch.setattr(hanjuku_screen, 'parse',
                        lambda *args, **kwargs: Screen(lines=[], hand=None, text='', kind='text'))
    _, updated = decide(Frame(256, 224, bytes(256 * 224 * 3)), {'policy': mem})
    mem = updated['policy']
    assert 'successor_seen' not in mem['battle']
    assert policy.battle_step(panel(), mem) == []
    assert mem['battle']['ally'] == 'クミン'
    assert policy.battle_step(panel(), mem)
    assert mem['battle']['ally'] == 'ヴィーナス'
