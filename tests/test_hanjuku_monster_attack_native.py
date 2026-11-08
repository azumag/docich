"""Native-frame integration regressions for issue #2835 summon attack policy."""
from dataclasses import replace
import json
from pathlib import Path

import pytest

from docich.hanjuku.flows.battle_menus import monster_menu_step
from docich.hanjuku.os_layer.commands import pad
from docich.hanjuku_pixels import read_png
from docich.hanjuku_screen import parse

ROOT = Path(__file__).parent / 'fixtures/hanjuku'
EVIDENCE = json.loads((ROOT / 'g749-naruto-evidence.json').read_text())


def native(uid=8549):
    row = next(r for r in EVIDENCE['evidence']
               if r['decision']['decision_id'].endswith(':' + str(uid)))
    frame = read_png(ROOT / f'g749-naruto-{uid}.png')
    assert frame.digest() == row['rgb_sha256'] == row['decision']['frame_sha256']
    return parse(frame)


def policy_case(ally, enemy, skills, *, enemy_hp=100, enemy_soldiers=0):
    """Reuse a verified native menu geometry while counterfactually replaying labels/panel."""
    screen = native()
    skill_indexes = [i for i, line in enumerate(screen.menu_rows)
                     if 'もどれ' not in ''.join(line.known.split())]
    assert len(skill_indexes) == 2
    for index, skill in zip(skill_indexes, skills):
        # TextLine.known is derived from cells rather than a dataclass field.
        # Preserve the native row anchor while replacing only the counterfactual label.
        row = screen.menu_rows[index]
        start_x = min(x for x, _ in row.cells)
        screen.menu_rows[index] = replace(
            row, cells=tuple((start_x + 8 * i, ch) for i, ch in enumerate(skill)))
    screen.egg_rows = [
        replace(row, name=ally, hp=100) if row.side == 'ally'
        else replace(row, name=enemy, hp=enemy_hp) if row.side == 'enemy'
        else row
        for row in screen.egg_rows
    ]
    mem = {
        'chapter': 1,
        '_run_identity': EVIDENCE['active'],
        'battle': {
            'ally': ally,
            'enemy': enemy,
            'ally_hp': 100,
            'enemy_hp': enemy_hp,
            'enemy_soldiers': enemy_soldiers,
            'side': 'attack',
        },
    }
    return screen, mem


def test_native_menu_button_changes_between_monster_and_general_opponents():
    skills = ('９９かいパンチ', '９９かいキック')
    monster_screen, monster_mem = policy_case('ワラワラ', 'エッグスライム', skills)
    general_screen, general_mem = policy_case('ワラワラ', 'あたし', skills)

    assert monster_menu_step(monster_screen, monster_mem) == [pad('down')]
    assert monster_mem['monster_menu_choice'] == 'skill2'
    assert monster_menu_step(general_screen, general_mem) == [pad('a')]
    assert general_mem['monster_menu_choice'] == 'skill1'


def test_native_menu_low_hp_finisher_keeps_first_skill():
    screen, mem = policy_case(
        'ワラワラ', 'エッグスライム', ('９９かいパンチ', '９９かいキック'), enemy_hp=10)

    assert monster_menu_step(screen, mem) == [pad('a')]
    assert mem['monster_menu_choice'] == 'skill1'


def test_native_menu_general_count_switches_single_vs_all_target():
    # Wiki attack-data row: ハードアタック 9/5 x1; ハードバズーカ 2/2 ALL.
    # The production table now carries this row itself.
    skills = ('ハードアタック', 'ハードバズーカ')

    single_screen, single_mem = policy_case('ハードマン', 'あたし', skills, enemy_soldiers=0)
    group_screen, group_mem = policy_case('ハードマン', 'あたし', skills, enemy_soldiers=3)

    assert monster_menu_step(single_screen, single_mem) == [pad('a')]
    assert single_mem['monster_menu_choice'] == 'skill1'
    assert monster_menu_step(group_screen, group_mem) == [pad('down')]
    assert group_mem['monster_menu_choice'] == 'skill2'


def test_native_menu_general_prefers_multi_hit_skill_and_logs_the_scores():
    # ラビットサタン: デスのカマ 11x1 vs ラビットキック 6x4. g1153 repeated
    # デスのカマ against a general because the general branch ignored 回数.
    screen, mem = policy_case(
        'ラビットサタン', 'キャンディー', ('デスのカマ', 'ラビットキック'), enemy_hp=26)

    assert monster_menu_step(screen, mem) == [pad('down')]
    assert mem['monster_menu_choice'] == 'skill2'
    record = mem['_records'][-1]
    assert record['decision'] == 'monster_menu_choice'
    evidence = record['observed_metric']['monster_attack_policy']
    assert evidence['opponent_kind'] == 'general'
    assert (evidence['score1'], evidence['score2']) == (11, 24)
    assert evidence['choice'] == 'skill2'
    assert evidence['rule'] == 'damage_rank'
