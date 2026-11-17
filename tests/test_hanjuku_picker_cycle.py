"""Chapter 11's real recall selector cycles own castles with Up/Down."""
from pathlib import Path
import hashlib
import json

import pytest

from docich.hanjuku.flows import map as flow
from docich.hanjuku.os_layer.commands import pad
from docich.hanjuku.os_layer.pixels import read_png
from docich.hanjuku.os_layer.phase import classify
from docich.hanjuku.os_layer.screen import parse
from docich.hanjuku.state.regions import flow as flow_state

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / 'tests/fixtures/hanjuku'



def only_press(actions):
    """The single pad button a picker step pressed (the hold varies by distance)."""
    assert len(actions) == 1 and actions[0].get('type') == 'pad', actions
    return actions[0]['buttons'][0]

def image(name):
    frame = read_png(FIX / (name + '.png')).resized()
    screen = parse(frame, phase=classify(frame))
    assert screen.kind == 'world_map'
    return screen, frame


def memory(goal='マランマ'):
    return {'chapter': 11, 'tick': 100, '_records': [],
            'captured': ['ゴンダル', 'マランマ'],
            'recall': {'stage': 'dest', 'steps': 0, 'target': [130, 113],
                       'gather': goal}}


def test_saved_frames_match_the_measured_cursor_and_current_ownership():
    evidence = json.loads((FIX / 'g1190-ch11-recall-cycle-evidence.json').read_text())
    for row in evidence['frames']:
        path = FIX / row['file']
        assert hashlib.sha256(path.read_bytes()).hexdigest() == row['png_sha256']
        frame = read_png(path).resized()
        assert flow.world_cursor(frame) == tuple(row['cursor'])
        assert sorted(name for name, owner in flow.world_flags(frame, 11).items()
                      if owner == 'own') == sorted(evidence['owned'])


def test_real_two_hop_sequence_confirms_only_on_the_observed_goal():
    mem = memory()
    for name in ('g1190-ch11-recall-home', 'g1190-ch11-recall-gondal'):
        screen, frame = image(name)
        assert only_press(flow.world_map_step(screen, mem, frame)) == 'down'
        assert flow_state(mem)['recall']['stage'] == 'dest'
        assert not any(r['decision'] == 'camp_recall_requested' for r in mem['_records'])
    screen, frame = image('g1190-ch11-recall-maranma')
    assert flow.world_map_step(screen, mem, frame) == [pad('a'), {'type': 'wait', 'ms': 700}, pad('a')]
    assert flow_state(mem)['recall']['stage'] == 'await_dispatch'
    assert flow_state(mem)['recall']['goal'] == 'マランマ'
    assert not flow_state(mem)['recall'].get('input_sent')


def test_a_horizontal_goal_uses_the_real_down_selection_in_one_press():
    mem = memory('ゴンダル')
    screen, frame = image('g1190-ch11-recall-home')
    assert only_press(flow.world_map_step(screen, mem, frame)) == 'down'
    screen, frame = image('g1190-ch11-recall-gondal')
    assert flow.world_map_step(screen, mem, frame) == [pad('a'), {'type': 'wait', 'ms': 700}, pad('a')]


def test_ignored_down_never_switches_to_an_ineffective_horizontal_axis():
    mem = memory()
    screen, frame = image('g1190-ch11-recall-home')
    for _ in range(flow.PICKER_STILL_LIMIT):
        assert only_press(flow.world_map_step(screen, mem, frame)) == 'down'
    assert flow.world_map_step(screen, mem, frame) == [pad('a'), {'type': 'wait', 'ms': 700}, pad('a')]
    recall = flow_state(mem)['recall']
    assert recall['goal'] == 'アルマムーン' and recall['picker_forced']
    assert not recall.get('input_sent')


@pytest.mark.parametrize('change', ['missing_flag', 'unknown_owner', 'current_enemy', 'goal_enemy', 'unknown_cursor'])
def test_unverified_cycle_context_keeps_the_existing_navigation(monkeypatch, change):
    mem = memory()
    screen, frame = image('g1190-ch11-recall-home')
    flags = flow.world_flags(frame, 11)
    if change == 'missing_flag':
        flags.pop('ネペンテ')
    elif change == 'unknown_owner':
        flags['ネペンテ'] = 'unknown'
    elif change == 'current_enemy':
        flags['アルマムーン'] = 'enemy'
    elif change == 'goal_enemy':
        flags['マランマ'] = 'enemy'
    else:
        monkeypatch.setattr(flow, 'world_cursor', lambda *_: (130, 130))
    monkeypatch.setattr(flow, 'world_flags', lambda *_: flags)
    assert only_press(flow.world_map_step(screen, mem, frame)) == 'left'


def test_an_unmeasured_chapter_keeps_native_geometric_navigation():
    frame = read_png(ROOT / 'tests/fixtures/hanjuku/g1075-ch3-recall-picker-almamoon.png')
    screen = parse(frame, phase=classify(frame))
    mem = {'chapter': 3, 'tick': 100, '_records': [],
           'captured': [c for c in flow.chart.castles(3) if c not in ('ボス', 'アルマムーン')],
           'recall': {'stage': 'dest', 'steps': 0, 'origin': [414, 504], 'origin_source': 'picker'}}
    assert only_press(flow.world_map_step(screen, mem, frame)) == 'down'
    assert flow_state(mem)['recall']['goal'] == 'メガパタ'
