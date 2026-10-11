"""g684 12:01: 1-C2 ココット (24) carried ダイチスイム×2・ブラッキー for the
chart's opening against タピオカ (50); ソーピニヨン (48) defended instead and
the enemy-keyed tactic never fired. ココット died without using a card."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from docich import hanjuku_policy as policy
from docich.hanjuku_screen import Battle, Screen


def start(enemy, enemy_hp, ally='ココット', ally_hp=24, step='1-C2'):
    mem = {'chapter': 1, 'attack': {'general': ally, 'castle': None, 'side': 'attack', 'step': step}}
    screen = Screen(lines=[], hand=None, text='', battle=Battle(enemy, enemy_hp, ally, ally_hp),
                    kind='battle')
    assert policy.battle_step(screen, mem) == []           # battle_seen
    return mem, policy.battle_step(screen, mem)            # battle_start


def test_a_stronger_substitute_defender_gets_the_steps_opening_cards():
    mem, actions = start('ソーピニヨン', 48)
    assert mem['battle']['tactic_substitute'] == {'expected': 'タピオカ', 'actual': 'ソーピニヨン'}
    assert mem['battle']['planned_cards'] == ['ダイチスイム', 'ブラッキー']
    assert actions and actions[0]['buttons'] == ['b']
    assert mem['battle']['card_flow']['card'] == 'ダイチスイム'
    assert any(r['decision'] == 'tactic_substitute_defender' for r in mem['_records'])


@pytest.mark.parametrize('enemy_hp', [20, 35])   # 35 < 1.5 x 24
def test_a_weaker_or_near_even_substitute_keeps_the_melee(enemy_hp):
    mem, _ = start('ソーピニヨン', enemy_hp)
    assert 'tactic_substitute' not in mem['battle']
    assert mem['battle']['planned_cards'] == []
    assert not mem['battle'].get('card_flow')


def test_the_named_enemy_and_hp_gated_branches_are_unchanged():
    mem, _ = start('タピオカ', 50)
    assert 'tactic_substitute' not in mem['battle']
    mem, _ = start('ガルバンゾー', 60)        # 1-C2 has its own HP-13 branch
    assert 'tactic_substitute' not in mem['battle']
    assert mem['battle']['planned_cards'] == ['ダイチスイム']


@pytest.mark.parametrize('step,ally', [('1-A2', 'どうし'), ('1-B1', 'どうし')])
def test_steps_without_a_single_opening_enemy_do_not_substitute(step, ally):
    mem, _ = start('ソーピニヨン', 99, ally=ally, ally_hp=90, step=step)
    assert 'tactic_substitute' not in mem['battle']
