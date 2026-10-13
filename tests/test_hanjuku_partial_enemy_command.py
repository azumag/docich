"""Native g749 partial enemy command must not inherit own summon inputs."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from docich import hanjuku_policy as policy
from docich.hanjuku.flows.battle_menus import (
    _partial_enemy_general_command, egg_battle_step, monster_menu_step,
)
from docich.hanjuku.os_layer.font import TextLine
from docich.hanjuku_pixels import read_png
from docich.hanjuku_screen import parse
from test_hanjuku_enemy_general_command import memory, native as old_native

ROOT = Path(__file__).parent / 'fixtures/hanjuku'
EVIDENCE = json.loads((ROOT / 'g749-partial-enemy-command-evidence.json').read_text())


def frame(uid=5561):
    row = next(x for x in EVIDENCE['frames'] if x['uid'] == uid)
    f = read_png(ROOT / row['fixture'])
    assert f.digest() == row['rgb_sha256'] == row['decision']['frame_sha256']
    assert any(i['decision_id'] == row['decision']['decision_id']
               and i['frame_sha256'] == i['decision_frame_sha256'] == f.digest()
               for i in row['input_receipts'])
    return f


def test_original_partial_menu_withholds_a_without_inventing_ally_panel():
    s = parse(frame()); m = memory(); before = deepcopy(m['battle'])
    assert len(s.egg_rows) == 1 and s.egg_rows[0].name == 'クランベリー'
    assert [r.known for r in s.menu_rows] == ['もうこうげき']
    assert egg_battle_step(s, m) == []
    assert m['battle'] == before
    record = m['_records'][-1]
    assert record['decision'] == 'enemy_general_menu_pending'
    assert record['observed_metric']['ally_panel_unread'] is True
    assert 'ally_hp' not in record['observed_metric']
    assert all(k not in m for k in ('egg_menu_stage', 'egg_battle', 'egg_action',
                                    'egg_key', 'egg_needed', 'egg_battle_row_dead'))


def test_json_roundtrip_wait_is_bounded_and_does_not_restore_old_navigation():
    s = parse(frame()); m = memory()
    for _ in range(70):
        m = json.loads(json.dumps(m)); assert egg_battle_step(s, m) == []
    assert m['enemy_general_menu_pending']['observations'] == 31
    assert len(m['_records']) == 2
    assert m['_records'][-1]['deviation_reason'] == 'enemy_command_unconfirmed'


@pytest.mark.parametrize('count', [-1, True, '1', 99, None])
def test_invalid_saved_count_cannot_trigger_input(count):
    m = memory(); m['enemy_general_menu_pending'] = {
        'scope': ['クランベリー', 51], 'observations': count}
    assert egg_battle_step(parse(frame()), m) == []
    assert m['enemy_general_menu_pending']['observations'] == 31


@pytest.mark.parametrize('change', ['own_side', 'no_color', 'duplicate', 'no_hp',
                                   'zero', 'too_high', 'bool_hp', 'unknown', 'monster',
                                   'own_column', 'shifted_column', 'foreign_label',
                                   'wrong_y', 'no_rows', 'wrong_kind'])
def test_different_or_unread_evidence_does_not_claim_partial_enemy_command(change):
    s = parse(frame()); e = s.egg_rows[0]
    if change == 'own_side': e.side = 'ally'
    elif change == 'no_color': e.side = None
    elif change == 'duplicate': s.egg_rows.append(deepcopy(e))
    elif change == 'no_hp': e.hp = None
    elif change == 'zero': e.hp = 0
    elif change == 'too_high': e.hp = 999
    elif change == 'bool_hp': e.hp = True
    elif change == 'unknown': e.name = 'クランベリ'
    elif change == 'monster': e.name = 'ダークエルフ'
    elif change in ('own_column', 'shifted_column'):
        offset = 136 if change == 'own_column' else 8
        s.menu_rows = [TextLine(r.y, tuple((x + offset, c) for x, c in r.cells))
                       for r in s.menu_rows]
    elif change == 'foreign_label': s.menu_rows = old_native(17507).menu_rows
    elif change == 'wrong_y':
        s.menu_rows = [TextLine(32, s.menu_rows[0].cells)]
    elif change == 'no_rows': s.menu_rows = []
    elif change == 'wrong_kind': s.kind = 'monster_menu'
    assert _partial_enemy_general_command(s) is None


def test_new_complete_own_turn_clears_pending_and_keeps_rescue():
    m = memory(); assert egg_battle_step(parse(frame()), m) == []
    s = parse(read_png(ROOT / 'g684-artemis-dark-elf.png'))
    m.update(egg_uses={'アルテミス': 4}, battle={
        'ally': 'アルテミス', 'enemy': 'ヘーゼル', 'ally_hp': 49,
        'enemy_hp': 11, 'start_ally_hp': 49, 'start_enemy_hp': 33,
        'side': 'defense', 'planned_cards': []})
    assert egg_battle_step(s, m) == [policy.pad('down')]
    assert m['egg_action'] == 'use_egg'
    assert 'enemy_general_menu_pending' not in m


def test_native_own_monster_skill_menu_is_not_suppressed():
    s = parse(frame(5560)); m = memory()
    assert _partial_enemy_general_command(s) is None
    assert monster_menu_step(s, m) == [policy.pad('a')]


def test_orchestrator_wait_survives_no_input_limit_then_releases_own_turn():
    from docich.hanjuku_bot import decide, NO_INPUT_HOLD_MAX
    f = frame(); state = {'policy': memory(), 'name_entered': True,
                         'no_input_streak': NO_INPUT_HOLD_MAX,
                         'no_input_frames': [f.digest()]}
    for _ in range(4):
        actions, state = decide(f, json.loads(json.dumps(state)))
        assert actions == []
        assert not any(r['decision'] == 'no_input_fallback' for r in state['_records'])
    actions, state = decide(frame(5560), state)
    assert actions
    assert 'enemy_general_menu_pending' not in state['policy']
