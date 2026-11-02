"""#3009: a finished battle must close even when the return picker covers it.

g1075 2026-10-29 15:10-15:14 (ボス出撃の3-B1): ヘパイストス fell to HP 0 with
アポロン at 20, then the 帰還先ピッカー (world_map) covered the return. Nothing
closed the record, so アポロン's next fight against クランベリー never opened a
battle (its panel was judged as the old one), and the old win was recorded at
15:14:37. The g1075 frames used here are the real picker and the real
カシュー/クランベリー panel; the ヘパイストス/アポロン panels are not fixtures,
so the finished record is built from the measured readings instead.
"""
from pathlib import Path

from docich import hanjuku_policy as policy
from docich.hanjuku.os_layer.phase import classify
from docich.hanjuku.os_layer.pixels import read_png
from docich.hanjuku.os_layer.screen import parse
from docich.hanjuku.orchestrator import decide

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'tests' / 'fixtures' / 'hanjuku'
PICKER = FIXTURES / 'g1075-ch3-recall-picker-almamoon.png'
NEXT_PANEL = FIXTURES / 'g1075-defense-handoff-1.png'
IDENTITY = {'game': 'hanjuku-hero', 'generation': 900,
            'runtime_id': 'g900-synthetic', 'lease_id': 'test-battle-carryover'}


def finished_memory(**extra):
    battle = {'enemy': 'ヘパイストス', 'ally': 'アポロン', 'enemy_hp': 0, 'ally_hp': 20,
              'start_enemy_hp': 49, 'start_ally_hp': 30, 'side': 'attack', 'castle': None,
              'step': None, 'cards_used': [], 'planned_cards': []}
    battle.update(extra)
    return {'chapter': 3, 'tick': 100, 'battle': battle, '_run_identity': IDENTITY}


def decisions(records, name):
    return [r for r in records if r['decision'] == name]


def drive(state, path, times=1):
    records = []
    for _ in range(times):
        frame = read_png(path)
        _, state = decide(frame, state, run_identity=IDENTITY)
        records.extend(state.get('_records') or [])
    return state, records


def test_the_picker_closes_a_finished_battle():
    picker = read_png(PICKER)
    assert parse(picker, phase=classify(picker)).kind == 'world_map'
    state, records = drive({'policy': finished_memory()}, PICKER, times=2)
    mem = state['policy']
    [result] = decisions(records, 'battle_result')
    assert (result['outcome'], result['resulting_event']) == ('win', 'win')
    assert 'battle' not in mem


def test_the_picker_keeps_an_unfinished_battle_open():
    state, records = drive({'policy': finished_memory(enemy_hp=34, ally_hp=20)}, PICKER, times=3)
    assert not decisions(records, 'battle_result')
    assert state['policy']['battle']['enemy_hp'] == 34


def test_a_new_opponent_after_a_finished_battle_is_a_new_battle():
    mem = finished_memory()
    state = {'policy': mem}
    state, records = drive(state, NEXT_PANEL, times=1)
    assert not decisions(records, 'battle_result')
    assert state['policy']['battle']['enemy'] == 'ヘパイストス'
    # Two matching panels close the old fight; the new one opens on the next reading.
    state, records = drive(state, NEXT_PANEL, times=2)
    [result] = decisions(records, 'battle_result')
    assert (result['outcome'], result['enemy'], result['ally']) == ('win', 'ヘパイストス', 'アポロン')
    [start] = decisions(records, 'battle_start')
    assert (start['enemy'], start['ally']) == ('カシュー', 'クランベリー')
    assert state['policy']['battle']['enemy'] == 'カシュー'
