"""Card damage contracts from SFC chart examples; no emulator input."""
import pytest

from docich import hanjuku_reference as reference


def estimate(card='ゼンマイン', **changes):
    args = dict(target_kind='general', enemy_name='キール', enemy_hp=32,
                ally_hp=90, ally_soldiers=6, enemy_soldiers=0)
    args.update(changes)
    return reference.card_damage_estimate(card, **args)


@pytest.mark.parametrize('hp,lethal,remaining', [(31, True, 0), (32, True, 0), (33, False, 1)])
def test_zenmine_ordinary_general_boundary(hp, lethal, remaining):
    out = estimate(enemy_hp=hp)
    assert out['raw_damage_min'] == 32
    assert out['damage_lower_bound'] == 32
    assert out['lethal'] is lethal
    assert out['remaining_hp_upper'] == remaining


def test_zenmine_does_not_ignore_the_enemys_six_soldiers():
    out = estimate(enemy_name='クミン', enemy_hp=27, ally_soldiers=0, enemy_soldiers=6)
    assert out['enemy_soldier_hp_upper'] == 60
    assert out['damage_lower_bound'] == 0
    assert out['remaining_hp_upper'] == 27
    assert out['lethal'] is False


def test_primary_chart_kiel_sequence_matches_soldier_absorption():
    # RTA: Cocotte with 3 soldiers vs Kiel HP65 with 6 soldiers.
    # Mickmee clears the soldiers and leaves HP61; a fresh observation of
    # zero soldiers lets Grinbo's 32+3 reach the general, leaving HP26.
    first = estimate('ミックミー', enemy_hp=65, ally_soldiers=3, enemy_soldiers=6)
    assert first['damage_lower_bound'] == 4 and first['remaining_hp_upper'] == 61
    second = estimate('グリンボー', enemy_hp=61, ally_soldiers=3, enemy_soldiers=0)
    assert second['damage_lower_bound'] == 35 and second['remaining_hp_upper'] == 26


@pytest.mark.parametrize('card,full,empty', [('グリンボー', 38, 32), ('マグネガキン', 72, 48)])
def test_soldier_bonus_uses_current_count_not_starting_count(card, full, empty):
    assert estimate(card, ally_soldiers=6)['raw_damage_min'] == full
    assert estimate(card, ally_soldiers=0)['raw_damage_min'] == empty
    assert estimate(card, ally_soldiers=None)['raw_damage_min'] == empty


@pytest.mark.parametrize('unread', [None, -1, 7, True, '0'])
def test_invalid_counts_never_prove_a_kill_by_assuming_no_enemy_soldiers(unread):
    out = estimate(ally_soldiers=unread, enemy_soldiers=unread)
    assert out['ally_soldiers_lower'] == 0
    assert out['enemy_soldiers_upper'] == 6
    assert out['lethal'] is False


def test_general_boss_column_is_enforced_even_if_caller_uses_general_kind():
    out = estimate('マグネガキン', enemy_name='クイーン', enemy_hp=70, enemy_soldiers=0)
    assert out['target_kind'] == 'boss_general'
    assert out['raw_damage_min'] == 80  # no 6*4 bonus, no monster endurance
    assert out['lethal'] is True


@pytest.mark.parametrize('enemy', ['プリンス', 'せいめいたい'])
def test_high_hp_boss_soldiers_do_not_become_ordinary_or_half_hp_soldiers(enemy):
    out = estimate(enemy_name=enemy, enemy_hp=25, enemy_soldiers=1)
    assert out['raw_damage_min'] == 50
    assert out['enemy_soldier_hp_upper'] == 100
    assert out['damage_lower_bound'] == 0 and out['lethal'] is False
    # Selecting two クースカン is not an input to this prediction.  Only
    # a new, direct zero-soldier observation removes their protection here.
    assert estimate(enemy_name=enemy, enemy_hp=25, enemy_soldiers=0)['lethal'] is True


@pytest.mark.parametrize('enemy,kind', [('未確認の将軍', 'general'), ('キール', 'boss_general'),
                                      (None, 'general'), ('キール', 'unexpected')])
def test_unknown_or_inconsistent_target_never_gets_an_ordinary_general_guarantee(enemy, kind):
    out = estimate('キャトルミュー', enemy_name=enemy, target_kind=kind, enemy_hp=1)
    assert out['target_kind'] == 'unknown' and out['lethal'] is None


@pytest.mark.parametrize('hp', [None, 0, -1, True, '12'])
def test_current_enemy_hp_must_be_a_live_measured_number(hp):
    assert estimate('キャトルミュー', enemy_hp=hp)['lethal'] is None


def test_half_hp_card_is_control_and_does_not_promise_a_kill():
    out = estimate('クースカン', enemy_hp=69, enemy_soldiers=6)
    assert out['effect_kind'] == 'half_enemy_hp'
    assert out['damage_lower_bound'] == 34 and out['remaining_hp_upper'] == 35
    assert out['raw_damage_min'] is None and out['lethal'] is False
    assert out['enemy_soldier_hp_upper'] == 60  # not mutated to a used-card result
    assert estimate('クースカン', enemy_hp=1)['remaining_hp_upper'] == 1


def test_hp_scaled_card_uses_the_users_current_hp_and_target_kind():
    normal = estimate('ノリウツール', ally_hp=90, enemy_hp=35, enemy_soldiers=1)
    assert normal['raw_damage_min'] == 45 and normal['damage_lower_bound'] == 35
    boss = estimate('ノリウツール', enemy_name='クイーン', enemy_hp=30, ally_hp=90)
    assert boss['raw_damage_min'] == 30 and boss['lethal'] is True
    assert estimate('ノリウツール', ally_hp=20)['lethal'] is False
    assert estimate('ノリウツール', ally_hp=None)['lethal'] is None


def test_soldier_clear_order_is_not_assumed_to_increase_general_damage():
    out = estimate('ファイアーボイス', enemy_hp=16, enemy_soldiers=2)
    assert out['effect_kind'] == 'soldier_clear'
    assert out['raw_damage_min'] == 16 and out['damage_lower_bound'] == 0
    assert out['lethal'] is False


def test_nori_does_not_guarantee_a_monster_boss_kill_from_unverified_hp_scaling():
    out = estimate('ノリウツール', target_kind='boss_monster', enemy_name='だいまおう',
                   enemy_hp=13, ally_hp=90, ally_soldiers=0)
    assert out['raw_damage_min'] == 8
    assert out['remaining_hp_upper'] == 5 and out['lethal'] is False


@pytest.mark.parametrize('kind', ['egg_monster', 'boss_monster'])
def test_monster_endure_requires_the_extra_seventeen_damage(kind):
    # Zenmine does 96 to an egg monster, 50 to a boss monster.
    raw = 96 if kind == 'egg_monster' else 50
    safe = estimate(target_kind=kind, enemy_name='monster', enemy_hp=raw - 17)
    assert safe['lethal'] is True
    survives = estimate(target_kind=kind, enemy_name='monster', enemy_hp=raw - 16)
    assert survives['lethal'] is False and survives['remaining_hp_upper'] == 1


def test_instant_egg_kill_does_not_invent_an_instant_boss_kill():
    egg = estimate('バルムンク', target_kind='egg_monster', enemy_name='monster', enemy_hp=9999)
    assert egg['effect_kind'] == 'instant_kill' and egg['lethal'] is True
    boss = estimate('バルムンク', target_kind='boss_monster', enemy_name='monster', enemy_hp=9999)
    assert boss['effect_kind'] == 'fixed' and boss['lethal'] is False


def test_healing_and_self_harm_are_separate_from_safe_attack_selection():
    heal = estimate('エンジェリン', enemy_hp=1)
    assert heal['effect_kind'] == 'heal' and heal['lethal'] is False
    mutual = estimate('デッドガン', enemy_hp=1)
    assert mutual['effect_kind'] == 'mutual_annihilation'
    assert mutual['self_harm'] is True and mutual['lethal'] is None
    assert estimate('ファバード')['self_harm'] is True
    assert estimate('unknown')['lethal'] is None
