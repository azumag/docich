"""A non-lethal card where the enemy's thought type has no egg trigger left.

gcgx EggHero: B/D fire on the enemy general's wall hit, C on a contact at
X <= chapter*4+32. Synthetic panels; the push bound is the code under test.
"""
import pytest

from docich import hanjuku_policy as p
from docich.hanjuku.judgment import battle
from test_hanjuku_card_damage_gate import assert_not_spent, cards, memory


@pytest.fixture(autouse=True)
def refused_card(monkeypatch):
    """These tests pin what follows a refusal, so the hit itself is not the reason to allow.

    The rule for hits that do not finish the enemy is in test_hanjuku_card_wear_down.py.
    """
    monkeypatch.setattr('docich.hanjuku.judgment.battle._card_wears_down_enemy', lambda estimate: False)


def gate(ally, enemy, enemy_hp, *, chapter, soldiers, side='attack', card='ゼンマイン'):
    mem = memory(card, enemy=enemy, enemy_hp=enemy_hp, ally=ally, ally_hp=40,
                 soldiers=soldiers, side=side)
    mem['chapter'] = chapter
    step = p.card_list_step(cards([card]), mem)
    return step, mem


def test_soldierless_weak_attacker_cannot_push_a_thought_two_enemy_to_its_wall():
    step, mem = gate('ピオーネ', 'ブカティーニ', 42, chapter=4, soldiers=(0, 3))
    assert step == [p.pad('a')]
    assessment = mem['battle']['card_assessment']
    assert assessment['lethal'] is False
    assert assessment['reason'] == 'enemy_egg_triggers_unreachable'


@pytest.mark.parametrize('soldiers', [(3, 0), (None, None)])
def test_our_soldiers_or_an_unread_count_push_the_enemy_into_its_wall(soldiers):
    step, mem = gate('ピオーネ', 'ブカティーニ', 42, chapter=4, soldiers=soldiers)
    assert step == [p.pad('b')]
    assert_not_spent(mem)
    assert mem['battle']['card_assessment']['reason'] == 'nonlethal_egg_risk'
    rejected = mem['_records'][0]['observed_metric']
    assert rejected['egg_reach']['unreachable'] is False


def test_hera_against_apollon_backs_apollon_onto_its_wall_from_the_rear_rank():
    # 16:06:55 g1075: the general stands 24 dots behind the front, and our
    # back-off stops at the right wall, so contacts settle near X 105.
    step, mem = gate('ヘラ', 'アポロン', 74, chapter=3, soldiers=(0, 0), card='グリンボー')
    assert step == [p.pad('b')]
    reach = mem['_records'][0]['observed_metric']['egg_reach']
    assert reach['enemy_general_x_min'] == 64.7
    assert reach['enemy_general_x_min'] - reach['enemy_backoff_upper'] <= battle.CLASH_LEFT_WALL


@pytest.mark.parametrize('enemy,thought,allowed', [('ヘスティア', 2, True), ('リーキ', 1, False)])
def test_contact_position_matters_only_for_a_thought_with_the_clash_check(enemy, thought, allowed):
    # Same push either way; at chapter 10 the clash check fires at X <= 72.
    reach = battle._enemy_egg_reach({'chapter': 10}, {'side': 'attack', 'ally': 'ピオーネ', 'enemy': enemy},
                                    {'ally_soldiers': 0}, {'effect_kind': 'fixed', 'self_harm': False})
    assert reach['enemy_general_x_min'] == 70.7
    assert reach['clash_checked'] is (thought != 2)
    assert reach['unreachable'] is allowed


def test_defense_keeps_the_forced_thought_one_refusal():
    step, mem = gate('ピオーネ', 'ブカティーニ', 42, chapter=4, soldiers=(0, 3), side='defense')
    assert step == [p.pad('b')]
    assert mem['battle']['card_assessment']['reason'] == 'nonlethal_egg_risk'
    assert mem['_records'][0]['observed_metric']['egg_reach'] is None


@pytest.mark.parametrize('effect,self_harm,enemy', [
    ('damage_and_stun', False, 'ブカティーニ'), ('fixed', True, 'ブカティーニ'), ('fixed', False, 'プリンス'),
])
def test_moving_or_self_harming_cards_and_bosses_are_not_bounded(effect, self_harm, enemy):
    assert battle._enemy_egg_reach({'chapter': 4}, {'side': 'attack', 'ally': 'ピオーネ', 'enemy': enemy},
                                   {'ally_soldiers': 0},
                                   {'effect_kind': effect, 'self_harm': self_harm}) is None
