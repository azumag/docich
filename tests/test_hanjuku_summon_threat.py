"""g684's native summon panel must outrank the enemy general's old HP11."""
from copy import deepcopy
from pathlib import Path

import pytest

from docich import hanjuku_experience as experience, hanjuku_policy as policy
from docich.hanjuku_pixels import read_png
from docich.hanjuku_screen import EggRow, parse

FRAME = Path(__file__).parent / 'fixtures/hanjuku/g684-artemis-dark-elf.png'


def encounter():
    frame = read_png(FRAME)
    assert frame.digest() == 'a4ebca049f8ddb86fa0aac44c38e65a77bbc24fe20ab37b3fa3ab2171f9d8bc6'
    screen = parse(frame)
    assert screen.kind == 'egg_battle_menu'
    assert [(r.name, r.hp, r.side) for r in screen.egg_rows] == [
        ('アルテミス', 49, 'ally'), ('ダークエルフ', 240, 'enemy')]
    mem = {'chapter': 1, 'egg_uses': {'アルテミス': 4}, 'battle': {
        'ally': 'アルテミス', 'enemy': 'ヘーゼル', 'ally_hp': 49, 'enemy_hp': 11,
        'start_ally_hp': 49, 'start_enemy_hp': 33, 'side': 'defense',
        'planned_cards': [], 'cards_used': [], 'card_evidence_version': 1}}
    return screen, mem


def test_real_panel_uses_egg_instead_of_attacking_240hp_monster():
    screen, mem = encounter()
    # The previous general-only heuristic incorrectly said to preserve it.
    assert policy._own_egg_needed(mem, mem['battle'], (49, 49))[0] is False
    assert policy.egg_battle_step(screen, mem) == [policy.pad('down')]
    assert mem['egg_action'] == 'use_egg'
    evidence = next(d for d in mem['_records'] if d['decision'] == 'egg_battle')['observed_metric']['egg_need']
    assert evidence['rule'] == 'enemy_monster_threat'
    assert evidence['enemy_hp'] == 11 and evidence['enemy_monster_hp'] == 240
    assert mem['battle']['enemy_hp'] == 11  # monster HP never rewrites the general's panel


def test_learned_wins_never_overrule_the_current_summon_threat():
    screen, mem = encounter()
    key = experience.situation_key('egg_summon', mem)
    mem['_experience'] = {key: {'attack': {'win': 9, 'loss': 0}}}
    policy.egg_battle_step(screen, mem)
    assert mem['egg_action'] == 'use_egg'


def test_hotload_escalates_even_a_previously_required_but_learned_attack():
    screen, mem = encounter()
    mem.update(egg_battle=True, egg_action='attack', egg_needed=True)
    mem['battle']['independent'] = {'kind': 'egg_summon', 'action': 'attack'}
    assert policy.egg_battle_step(screen, mem) == [policy.pad('down')]
    assert mem['battle']['independent']['action'] == 'use_egg'
    assert mem['egg_action'] == 'use_egg'


@pytest.mark.parametrize('monster,hp', [('ダークエルフ', 1), ('プチデビル', 240)])
def test_instant_death_or_actual_hp_disadvantage_each_need_the_egg(monster, hp):
    screen, mem = encounter()
    screen.egg_rows[1] = EggRow(monster, hp, 'enemy', 80)
    policy.egg_battle_step(screen, mem)
    assert mem['egg_action'] == 'use_egg'


def test_a_weak_nonlethal_monster_preserves_the_existing_attack_choice():
    screen, mem = encounter()
    screen.egg_rows[1] = EggRow('エッグスライム', 10, 'enemy', 80)
    assert policy.egg_battle_step(screen, mem) == [policy.pad('a')]
    assert mem['egg_action'] == 'attack'


def test_an_empty_egg_does_not_create_a_summon_or_defense_retreat():
    screen, mem = encounter()
    mem['egg_uses']['アルテミス'] = 0
    assert policy.egg_battle_step(screen, mem) == [policy.pad('a')]
    assert mem['egg_action'] == 'attack'
    assert not mem['battle'].get('egg_retreat_attempts')


@pytest.mark.parametrize('mutation', ['wrong_ally', 'foreign_enemy', 'duplicate_enemy', 'invalid_hp'])
def test_foreign_ambiguous_or_unread_panel_does_not_claim_a_measured_threat(mutation):
    screen, mem = encounter()
    if mutation == 'wrong_ally':
        screen.egg_rows[0].name = 'ヴィーナス'
    elif mutation == 'foreign_enemy':
        screen.egg_rows[1].name = 'ヘーゼル'
    elif mutation == 'duplicate_enemy':
        screen.egg_rows.append(deepcopy(screen.egg_rows[1]))
    else:
        screen.egg_rows[1].hp = True
    policy.egg_battle_step(screen, mem)
    evidence = next(d for d in mem['_records'] if d['decision'] == 'egg_battle')['observed_metric']['egg_need']
    assert evidence['rule'] != 'enemy_monster_threat'
