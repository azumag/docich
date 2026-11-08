"""Regression contracts for issue #2835 opponent-aware summon attacks."""
from types import SimpleNamespace

from docich import hanjuku_reference as reference
from docich.hanjuku.judgment.events import _monster_attack_policy_default


def monster(name, hp=100):
    return SimpleNamespace(name=name, hp=hp)


def choose(ally, enemy, first, second, *, enemy_hp=100, battle=None, evidence=None):
    return _monster_attack_policy_default(
        'ally', monster(ally), monster(enemy, enemy_hp), [first, second],
        first, second, 100, enemy_hp, {'battle': battle or {}}, evidence=evidence,
    )


def test_warawara_reverses_choice_for_monster_and_general_opponents():
    skills = ('９９かいパンチ', '９９かいキック')
    assert choose('ワラワラ', 'エッグスライム', *skills) == 'skill2'
    assert choose('ワラワラ', 'あたし', *skills) == 'skill1'


def test_warawara_uses_monster_damage_column_for_boss():
    skills = ('９９かいパンチ', '９９かいキック')
    assert choose('ワラワラ', 'クイーン', *skills) == 'skill2'


def test_self_damage_skill_is_excluded_from_normal_selection():
    assert choose('ウッドボール', 'エッグスライム', 'たいあたり', 'ばくはつする') == 'skill1'


def test_general_battle_prefers_the_multi_hit_skill():
    # ラビットサタン: デスのカマ 11x1 vs ラビットキック 6x4 = 24.
    # Production g1153 kept デスのカマ against a general because the general
    # branch scored the per-hit power and ignored 攻撃回数.
    assert choose('ラビットサタン', 'キャンディー', 'デスのカマ', 'ラビットキック') == 'skill2'
    # フランソワーズ: にげまどう 14x1 vs すねる 8x8 = 64.
    assert choose('フランソワーズ', 'キャンディー', 'にげまどう', 'すねる') == 'skill2'


def test_effect_only_skill_never_beats_a_damaging_skill():
    # アモン: きゅうしゅう (HP吸収, no damage) vs あんこく 12x2.
    assert choose('アモン', 'キャンディー', 'きゅうしゅう', 'あんこく') == 'skill2'
    # デス: しにがみのカマ 8 vs タマシイヌキ (即死) against a monster.
    assert choose('デス', 'エッグスライム', 'しにがみのカマ', 'タマシイヌキ') == 'skill1'


def test_instant_death_wins_on_a_lone_general_turn():
    # 即死 is guaranteed against a general but a low chance against a monster.
    assert choose('エクスカリバー', 'カシュー', 'エクスカリバる', 'マサムネる') == 'skill1'
    assert choose('ユニコーン', 'カシュー', 'つのでつく', 'うしろあしキック') == 'skill1'
    assert choose('デス', 'カシュー', 'しにがみのカマ', 'タマシイヌキ') == 'skill2'
    # A general's soldiers keep the existing ALL/damage ranking instead.
    assert choose('エクスカリバー', 'カシュー', 'エクスカリバる', 'マサムネる',
                  battle={'enemy_soldiers': 3}) == 'skill2'


def test_unregistered_monster_and_unverified_menu_fail_closed():
    assert choose('エッグスライム', 'クイーン', 'たいあたり', 'ころがる') is None
    assert choose('ワラワラ', 'クイーン', '９９かいパンチ', '読めない技') is None


def test_every_reference_monster_has_a_scored_row():
    """The #2835 table covered only two monsters, so every other summon fell
    back to the first skill.  Keep the whole 121-monster table complete and
    label-compatible with the menu reader."""
    assert set(reference.MONSTER_ATTACK_DATA) == set(reference.MONSTER_SKILLS)
    for name, skills in reference.MONSTER_SKILLS.items():
        rows = reference.MONSTER_ATTACK_DATA[name]
        assert (rows['skill1'][0], rows['skill2'][0]) == tuple(skills)
        for row in (rows['skill1'], rows['skill2']):
            assert isinstance(row[3], int) and row[3] > 0
            assert isinstance(row[4], bool) and isinstance(row[5], bool)


def test_evidence_reports_the_scored_pair_for_the_decision_log():
    evidence = {}
    assert choose('ラビットサタン', 'キャンディー', 'デスのカマ', 'ラビットキック',
                  evidence=evidence) == 'skill2'
    assert evidence['monster'] == 'ラビットサタン'
    assert evidence['opponent_kind'] == 'general'
    assert (evidence['score1'], evidence['score2']) == (11, 24)
    assert evidence['choice'] == 'skill2'
    assert evidence['rule'] == 'damage_rank'
