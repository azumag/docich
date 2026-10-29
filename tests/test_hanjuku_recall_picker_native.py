"""#3001: the recall picker ring that does not move is given up (v300).

g1075-1d5f65e9 2026-10-29 JST, chapter 3. After きかん the R ring stayed on
アルマムーン through 54 left presses toward パタゴーヌ (15:10:55-15:14:46).
At 15:14:46 パタゴーヌ showed an enemy flag, the goal became メガパタ and one
down press moved the ring to グリン (g1075-ch3-recall-picker-almamoon.png at
15:14:46, g1075-ch3-recall-picker-grin.png at 15:15:24; live: down, then left).
"""
from pathlib import Path

from docich import hanjuku_policy as policy
from docich.hanjuku.os_layer.phase import classify
from docich.hanjuku.os_layer.pixels import read_png
from docich.hanjuku.os_layer.screen import parse

FIXTURES = Path(__file__).resolve().parent / 'fixtures' / 'hanjuku'


def load(name):
    frame = read_png(FIXTURES / f'g1075-ch3-recall-picker-{name}.png')
    return parse(frame, phase=classify(frame)), frame


def memory():
    return {'chapter': 3, 'tick': 100, '_records': [],
            'captured': [c for c in policy.chart.castles(3) if c not in ('ボス', 'アルマムーン')],
            'recall': {'stage': 'dest', 'steps': 0, 'origin': [414, 504], 'origin_source': 'picker'}}


def steps(mem):
    return [r['observed_metric'] for r in mem['_records'] if r['decision'] == 'camp_recall_picker_step']


def test_native_picker_frames_replay_the_live_presses():
    mem = memory()
    screen, frame = load('almamoon')
    assert screen.kind == 'world_map'
    assert policy.world_map_step(screen, mem, frame) == [policy.pad('down')]
    assert mem['recall']['goal'] == 'メガパタ'
    screen, frame = load('grin')
    assert policy.world_map_step(screen, mem, frame) == [policy.pad('left')]
    assert [(s['cursor'], s['press'], s['still']) for s in steps(mem)] == [
        ([151.0, 64.5], 'down', 0), ([169.0, 84.5], 'left', 0)]


def test_native_ring_that_stays_on_home_tries_left_then_gives_up():
    mem = memory()
    screen, frame = load('almamoon')
    presses = [policy.world_map_step(screen, mem, frame) for _ in range(policy.PICKER_STILL_LIMIT)]
    assert presses == ([[policy.pad('down')]] * policy.PICKER_AXIS_SWITCH
                       + [[policy.pad('left')]] * (policy.PICKER_STILL_LIMIT - policy.PICKER_AXIS_SWITCH))
    assert policy.world_map_step(screen, mem, frame) == [policy.pad('b')]
    assert not mem.get('recall') and mem['recall_skip'] == {'tick': 100}
    aborted = [r for r in mem['_records'] if r['decision'] == 'camp_recall_aborted']
    assert [r['resulting_event'] for r in aborted] == ['picker_cursor_stationary']
    assert [s['still'] for s in steps(mem)] == list(range(policy.PICKER_STILL_LIMIT + 1))
