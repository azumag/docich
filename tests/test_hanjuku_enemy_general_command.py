"""Own monster vs enemy general commands; original inputs are not new outcomes."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from docich import hanjuku_policy as policy
from docich.hanjuku.flows.battle_menus import _enemy_general_command, egg_battle_step, monster_menu_step
from docich.hanjuku_pixels import read_png
from docich.hanjuku_screen import parse
from docich.hanjuku.os_layer.font import TextLine

ROOT = Path(__file__).parent / 'fixtures/hanjuku'
EVIDENCE = json.loads((ROOT / 'g747-enemy-command-evidence.json').read_text())


def native(uid):
    row = next(r for r in EVIDENCE['frames'] if r['uid'] == uid)
    frame = read_png(ROOT / row['fixture'])
    assert frame.digest() == row['rgb_sha256'] == row['decision']['frame_sha256']
    assert any(r['decision_id'] == row['decision']['decision_id'] and
               r['frame_sha256'] == r['decision_frame_sha256'] == frame.digest()
               for r in row['input_receipts'])
    return parse(frame)


def memory():
    return {'chapter': 1, 'hero_max_hp': 90,
            'battle': {'ally': 'どうし', 'enemy': 'カシュー', 'ally_hp': 90,
                       'enemy_hp': 39, 'side': 'defense', 'planned_cards': []},
            'egg_menu_stage': 2, 'egg_action': 'use_egg', 'egg_battle': True,
            'egg_key': 'old', 'egg_needed': True, 'egg_battle_row_dead': True}


@pytest.mark.parametrize('uid', [1310, 17492])
def test_native_enemy_general_waits_without_own_egg_or_attack_action(uid):
    s = native(uid); m = memory(); old_battle = deepcopy(m['battle'])
    assert _enemy_general_command(s)
    assert egg_battle_step(s, m) == []
    assert m['battle'] == old_battle  # no stale general assessment/learning claim
    assert m['_records'][-1]['decision'] == 'enemy_general_menu_wait'
    assert m['_records'][-1]['observed_metric']['owner'] == 'enemy'
    assert all(key not in m for key in ('egg_action', 'egg_key', 'egg_needed',
                                       'egg_battle', 'egg_menu_stage', 'egg_battle_row_dead'))


def test_repeated_native_image_and_json_roundtrip_saturate_without_blind_confirmation():
    # Repetition is a synthetic stuck-screen case, not 100 natural observations.
    s = native(17492); m = memory()
    for _ in range(100):
        m = json.loads(json.dumps(m))
        assert egg_battle_step(s, m) == []
    assert m['enemy_general_menu']['observations'] == 31
    records = [r for r in m['_records'] if r['decision'] == 'enemy_general_menu_wait']
    assert len(records) == 2
    assert records[-1]['deviation_reason'] == 'enemy_command_unconfirmed'


@pytest.mark.parametrize('bad_count', [-1, 32, True, '1', None])
def test_corrupt_saved_count_cannot_authorize_an_input(bad_count):
    s = native(17492); m = memory()
    m['enemy_general_menu'] = {'scope': ['ヒュドラ', 396, 'カシュー', 39],
                              'observations': bad_count}
    assert egg_battle_step(s, m) == []
    assert m['enemy_general_menu']['observations'] == 31


@pytest.mark.parametrize('change', ['missing_ally', 'duplicate_ally', 'duplicate_enemy',
                                   'no_color', 'unknown_monster', 'unknown_general',
                                   'general_as_ally', 'monster_as_enemy', 'ally_zero',
                                   'enemy_zero', 'enemy_over_max', 'bool_hp',
                                   'missing_command', 'foreign_command', 'wrong_kind'])
def test_incomplete_or_different_owner_is_not_claimed_as_enemy(change):
    s = deepcopy(native(17492)); a, e = s.egg_rows
    if change == 'missing_ally': s.egg_rows.remove(a)
    elif change == 'duplicate_ally': s.egg_rows.append(deepcopy(a))
    elif change == 'duplicate_enemy': s.egg_rows.append(deepcopy(e))
    elif change == 'no_color': a.side = None
    elif change == 'unknown_monster': a.name = 'ヒュトラ'
    elif change == 'unknown_general': e.name = 'カシュ'
    elif change == 'general_as_ally': a.name = 'アルテミス'; a.hp = 49
    elif change == 'monster_as_enemy': e.name = 'ダークエルフ'
    elif change == 'ally_zero': a.hp = 0
    elif change == 'enemy_zero': e.hp = 0
    elif change == 'enemy_over_max': e.hp = 999
    elif change == 'bool_hp': a.hp = True
    elif change == 'missing_command': s.menu_rows.pop(0)
    elif change == 'foreign_command': s.menu_rows = native(17507).menu_rows
    elif change == 'wrong_kind': s.kind = 'monster_menu'
    assert _enemy_general_command(s) is None


def test_current_own_general_gray_egg_and_monster_skills_keep_their_own_flows():
    s = native(17700); m = memory(); m['battle']['enemy'] = 'シェーブル'
    assert _enemy_general_command(s) is None
    assert egg_battle_step(s, m) == [policy.pad('a')]
    assert m['battle']['ally_hp'] == 90
    assert m['_records'][-1]['decision'] == 'battle_egg_general_attack'
    assert not m.get('enemy_general_menu')
    skill = native(17507); assert _enemy_general_command(skill) is None
    own = memory()
    assert monster_menu_step(skill, own)
    assert own['monster_menu_choice'] != 'retreat'


def test_new_own_turn_after_enemy_wait_reassesses_without_old_egg_navigation():
    m = memory(); assert egg_battle_step(native(17492), m) == []
    m['battle']['enemy'] = 'シェーブル'
    assert egg_battle_step(native(17700), m) == [policy.pad('a')]
    assert not m.get('enemy_general_menu')
    assert m['battle']['ally_hp'] == 90


@pytest.mark.parametrize('offset', [8, 136])
def test_changed_or_opposite_command_column_is_not_enemy_evidence(offset):
    s = native(17492)
    s.menu_rows = [TextLine(r.y, tuple((x + offset, c) for x, c in r.cells))
                   for r in s.menu_rows]
    assert _enemy_general_command(s) is None


def test_own_general_threat_still_summons_after_enemy_menu_wait():
    m = memory(); assert egg_battle_step(native(17492), m) == []
    # Original g684 own general panel; the preceding memory is synthetic.
    s = parse(read_png(ROOT / 'g684-artemis-dark-elf.png'))
    m.update(egg_uses={'アルテミス': 4}, battle={
        'ally': 'アルテミス', 'enemy': 'ヘーゼル', 'ally_hp': 49,
        'enemy_hp': 11, 'start_ally_hp': 49, 'start_enemy_hp': 33,
        'side': 'defense', 'planned_cards': []})
    assert egg_battle_step(s, m) == [policy.pad('down')]
    assert m['egg_action'] == 'use_egg'
    assert not m.get('enemy_general_menu')


def test_orchestrator_does_not_turn_enemy_wait_into_blind_a_after_no_input_limit():
    from docich.hanjuku_bot import decide, NO_INPUT_HOLD_MAX
    f = read_png(ROOT / 'g747-enemy-command-17492.png')
    state = {'policy': memory(), 'name_entered': True,
             'no_input_streak': NO_INPUT_HOLD_MAX, 'no_input_frames': [f.digest()]}
    for _ in range(4):
        actions, state = decide(f, json.loads(json.dumps(state)))
        assert actions == []
        assert not any(r['decision'] == 'no_input_fallback' for r in state['_records'])
    # A stale persisted guard does not suppress the new own general turn.
    own = read_png(ROOT / 'g747-enemy-command-17700.png')
    state['policy']['battle']['enemy'] = 'シェーブル'
    actions, state = decide(own, state)
    assert actions
    assert not state['policy'].get('enemy_general_menu')
