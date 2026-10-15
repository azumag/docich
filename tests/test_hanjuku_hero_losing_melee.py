"""The hero's losing melee reopens the unopened command menu (g749 08:37).

Synthetic observations only: no ROM, no live input.  The run's last defeat was
どうし 90 vs アテナ66 in a castle defense (no retreat): the three opening B
presses landed on the enemy's battle dialogue, the resource menu never
appeared, and the hold budget latched `power_mash` until the hero died 54 -> 6
while アテナ never left 66.  どうしの戦死はゲームオーバー.
"""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from docich import hanjuku_policy as policy
from docich.hanjuku_screen import Battle, Screen

MASH = [action for _ in range(policy.POWER_TAPS)
        for action in (policy.pad('a', 3), {'type': 'wait', 'ms': 50})]
WAIT = [{'type': 'wait', 'ms': 50}]


def panel(enemy='アテナ', enemy_hp=66, ally_hp=90, ally='どうし'):
    return Screen([], None, '', kind='battle', battle=Battle(enemy, enemy_hp, ally, ally_hp))


def memory(side='defense'):
    return {'chapter': 2, 'attack': {'side': side, 'general': 'どうし',
                                     'castle': 'アルマムーン', 'step': None}}


def start_battle(mem, **kwargs):
    """Battle opening: a stable panel, then the first survival B."""
    assert policy.battle_step(panel(**kwargs), mem) == []
    assert policy.battle_step(panel(**kwargs), mem) == [policy.pad('b')]
    return mem['battle']


def failed_open_budget(mem):
    """Three B presses that never produced a menu (enemy dialogue, g749)."""
    mem['battle']['survival'] = {'exhausted': True, 'opens': 3, 'pending_opens': 3,
                                 'menu_ticks': 0, 'cards_checked': 0, 'egg_attempted': False}
    mem['battle'].pop('command_open_pending', None)


def record_names(mem):
    return [rec.get('decision') for rec in mem.get('_records', [])]


def test_losing_hero_melee_reopens_the_menu_instead_of_mashing_to_death():
    mem = memory()
    start_battle(mem)
    failed_open_budget(mem)
    # The hold keeps its egg-safe budget: an uncut hold is still inputless.
    for hp in (90, 86, 82):
        assert policy.battle_step(panel(ally_hp=hp), mem) == []
    # 90 - 81 reaches the hold budget and latches the push, as before.
    assert policy.battle_step(panel(ally_hp=81), mem) == MASH
    assert mem['_records'][-1]['melee_control_mode'] == 'power_mash'
    # アテナ's panel has not moved, so a trade we keep losing must not be
    # mashed to death: the unopened command menu is reopened instead.
    assert policy.battle_step(panel(ally_hp=54), mem) == [policy.pad('b')]
    rec = mem['_records'][-1]
    assert rec['decision'] == 'battle_hero_survival_recheck'
    assert rec['reason']
    assert rec['observed_metric']['episode'] == 1
    assert rec['observed_metric']['limit'] == policy.HERO_SURVIVAL_REOPEN_LIMIT


def test_hero_recheck_is_bounded_per_fight():
    mem = memory()
    start_battle(mem)
    failed_open_budget(mem)
    assert policy.battle_step(panel(ally_hp=81), mem) == MASH
    actions = [policy.battle_step(panel(ally_hp=54), mem) for _ in range(30)]
    rechecks = [rec for rec in mem['_records'] if rec.get('decision') == 'battle_hero_survival_recheck']
    assert len(rechecks) == policy.HERO_SURVIVAL_REOPEN_LIMIT
    assert rechecks[0]['observed_metric']['episode'] == 1
    assert rechecks[-1]['observed_metric']['episode'] == policy.HERO_SURVIVAL_REOPEN_LIMIT
    # Two failed episodes are enough: the fight returns to the bounded push.
    assert actions[0] == [policy.pad('b')]
    assert actions[-1] == MASH
    assert mem['_records'][-1]['melee_control_mode'] == 'power_mash'


def test_a_menu_that_opened_ends_the_recheck():
    mem = memory()
    start_battle(mem)
    failed_open_budget(mem)
    assert policy.battle_step(panel(ally_hp=81), mem) == MASH
    assert policy.battle_step(panel(ally_hp=54), mem) == [policy.pad('b')]
    # The menu is finally on screen: its own procedure owns the fight now.
    mem['battle']['survival']['menu_ticks'] = 1
    opened_at = len(mem['_records'])
    for _ in range(6):
        policy.battle_step(panel(ally_hp=50), mem)
    assert 'battle_hero_survival_recheck' not in [
        rec.get('decision') for rec in mem['_records'][opened_at:]]


@pytest.mark.parametrize('enemy_hp', [60, 20])
def test_a_trade_the_enemy_also_loses_never_reopens(enemy_hp):
    # アテナ's own panel falling below its opening is not the measured
    # one-sided trade; the existing hold/push budget stays in charge.
    mem = memory()
    start_battle(mem)
    failed_open_budget(mem)
    assert policy.battle_step(panel(enemy_hp=enemy_hp, ally_hp=81), mem) == MASH
    assert policy.battle_step(panel(enemy_hp=enemy_hp, ally_hp=54), mem) == MASH
    assert 'battle_hero_survival_recheck' not in record_names(mem)


def test_an_ordinary_general_keeps_the_existing_push():
    # A general's defeat is a real loss, but not a game over: the bounded
    # hold/push contract of #1180 is unchanged for everyone else.
    mem = memory()
    assert policy.battle_step(panel(ally='アマンディーヌ'), mem) == []
    assert policy.battle_step(panel(ally='アマンディーヌ'), mem) == [policy.pad('b')]
    mem['battle']['survival'] = {'exhausted': True, 'opens': 3, 'pending_opens': 3,
                                 'menu_ticks': 0, 'cards_checked': 0, 'egg_attempted': False}
    mem['battle'].pop('command_open_pending', None)
    assert policy.battle_step(panel(ally='アマンディーヌ', ally_hp=81), mem) == MASH
    assert policy.battle_step(panel(ally='アマンディーヌ', ally_hp=54), mem) == MASH
    assert 'battle_hero_survival_recheck' not in record_names(mem)


def test_charted_hero_fights_keep_their_chart():
    # A charted tactic must still progress: its melee is not this rescue's.
    mem = {'chapter': 2, 'attack': {'side': 'attack', 'general': 'どうし',
                                    'castle': None, 'step': '2-V1'}}
    start_battle(mem)
    mem['battle']['planned_cards'] = ['クースカン']
    for _ in range(6):
        policy.battle_step(panel(enemy_hp=66, ally_hp=54), mem)
    assert 'battle_hero_survival_recheck' not in record_names(mem)
