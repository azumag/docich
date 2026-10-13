"""Ended-run evidence plus a bounded, counterfactual critical-menu retry."""
from copy import deepcopy
import json
from pathlib import Path

from docich import hanjuku_policy as p
from docich.hanjuku_pixels import read_png
from docich.hanjuku_screen import parse

ROOT = Path(__file__).parent / 'fixtures/hanjuku'
EVIDENCE = json.loads((ROOT / 'g749-hero-athena-game-over-evidence.json').read_text())


def memory():
    return {'chapter': 2, '_run_identity': deepcopy(EVIDENCE['active']), 'battle': {
        'ally': p.NAME, 'enemy': 'アテナ', 'ally_hp': 6, 'enemy_hp': 52,
        'start_ally_hp': 90, 'start_enemy_hp': 66, 'side': 'defense', 'step': None,
        'planned_cards': [], 'cards_used': [], 'card_evidence_version': 1,
        'clashed': True, 'melee_forced': True,
        'survival': {'opens': 3, 'pending_opens': 3, 'menu_ticks': 0,
                     'egg_attempted': False, 'cards_checked': 0, 'cards_attempted': []}}}


def panel(number):
    return parse(read_png(ROOT / f'g749-hero-athena-{number}.png'))


def test_original_hp6_frame_sent_melee_not_a_confirmed_resource_command():
    row = next(e for e in EVIDENCE['evidence'] if e['fixture'].endswith('30339.png'))
    s = panel(30339)
    assert (s.battle.ally, s.battle.enemy, s.battle.ally_hp, s.battle.enemy_hp) == (
        p.NAME, 'アテナ', 6, 52)
    assert read_png(ROOT / row['fixture']).digest() == row['rgb_sha256']
    assert [r['buttons'] for r in row['input_receipts']] == [['a']] * 4
    assert all(r['decision_id'] == row['decision']['decision_id']
               and r['decision_frame_sha256'] == row['rgb_sha256'] for r in row['input_receipts'])
    assert panel(30340).battle.ally_hp == 0


def test_three_unconfirmed_openings_allow_the_existing_single_critical_retry():
    mem = memory()
    assert p.battle_step(panel(30339), mem) == [p.pad('b')]
    rescue = mem['battle']['survival']
    assert rescue['opens'] == 4 and rescue['critical_open_recheck'] is True
    assert rescue['egg_attempted'] is False
    assert mem['battle']['command_open_pending']['owner'] == 'survival'
    for _ in range(8):
        assert p.battle_step(panel(30339), mem) != [p.pad('b')]
    assert rescue['opens'] == 4
    assert any(r['decision'] == 'battle_survival_critical_recheck' for r in mem['_records'])


def test_zero_hp_or_already_selected_egg_does_not_get_a_further_menu_retry():
    mem = memory()
    assert p.battle_step(panel(30340), mem) == []
    assert mem['battle']['survival']['opens'] == 3
    mem = memory(); mem['battle']['survival']['egg_attempted'] = True
    assert p.battle_step(panel(30339), mem) != [p.pad('b')]
    assert mem['battle']['survival']['opens'] == 3
