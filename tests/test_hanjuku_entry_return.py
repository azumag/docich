"""Entry text is not a location receipt for every later battle."""
from copy import deepcopy
import pytest
from docich import hanjuku_policy as policy, hanjuku_screen
from docich.hanjuku_bot import decide
from docich.hanjuku_pixels import Frame
from docich.hanjuku_screen import Screen


def memory(side='defense'):
    return {'chapter': 1, 'captured': ['ジョンリギ'],
            'garrison': {'ジョンリギ': ['ゼウス']},
            'attack': {'general': None if side == 'defense' else 'ヴィーナス',
                       'castle': 'ジョンリギ', 'side': side, 'step': None},
            'battle_seen': ('ヴィーナス', 'カシュー')}


@pytest.mark.parametrize('side', ['defense', 'attack', 'enemy'])
@pytest.mark.parametrize('kind', sorted(policy.ENTRY_RETURN_KINDS))
def test_return_to_management_expires_unstarted_entry_without_inventing_loss(side, kind):
    mem = memory(side); captured = deepcopy(mem['captured']); garrison = deepcopy(mem['garrison'])
    policy.observe_entry_return(Screen(lines=[], hand=None, text="", kind=kind), mem)
    assert mem['attack'] is None and 'battle_seen' not in mem
    assert mem['captured'] == captured and mem['garrison'] == garrison
    assert not mem.get('lost') and not mem.get('stats')
    assert mem['_records'][0]['decision'] == 'battle_entry_expired'
    assert policy._battle_context(mem, 'ヴィーナス')['castle'] is None


@pytest.mark.parametrize('kind', ['map', 'map_target', 'world_map', 'unknown', 'text',
    'battle', 'battle_menu', 'battle_menu_pending', 'egg_battle_menu',
    'egg_choice_menu', 'monster_menu', 'okunote_menu', 'defense_started', 'attack_started'])
def test_transient_field_or_combat_screen_does_not_expire_entry(kind):
    mem = memory(); expected = deepcopy(mem)
    policy.observe_entry_return(Screen(lines=[], hand=None, text="", kind=kind), mem)
    assert mem == expected


def test_first_away_management_observation_preserves_active_battle_result():
    mem = memory(); mem['battle'] = {'ally': 'ゼウス', 'enemy': 'カシュー',
        'ally_hp': 0, 'enemy_hp': 39, 'castle': 'ジョンリギ', 'side': 'defense'}
    policy.battle_end(mem, 'castle_menu')
    policy.observe_entry_return(Screen(lines=[], hand=None, text="", kind='castle_menu'), mem)
    assert mem['attack']['castle'] == 'ジョンリギ' and mem['battle']['away'] == 1
    policy.battle_end(mem, 'castle_menu')
    policy.observe_entry_return(Screen(lines=[], hand=None, text="", kind='castle_menu'), mem)
    assert any(r['decision'] == 'battle_result' and r['outcome'] == 'loss' for r in mem['_records'])
    assert not any(r['decision'] == 'battle_entry_expired' for r in mem['_records'])


def test_g510_main_menu_return_is_wired_into_decide(monkeypatch):
    # Actual sequence: Jonrigi warning5038, main_menu5054, roster completion5115,
    # Venus/Cashew starts5130. The old warning must not label this new fight.
    monkeypatch.setattr(hanjuku_screen, 'parse', lambda *a, **k:
                        Screen(lines=[], hand=None, text="", kind='main_menu'))
    _, state = decide(Frame(256, 224, bytes(256 * 224 * 3)), {'policy': memory()})
    assert state['policy']['attack'] is None
    assert policy._battle_context(state['policy'], 'ヴィーナス')['castle'] is None
    assert any(r['decision'] == 'battle_entry_expired' for r in state['_records'])
