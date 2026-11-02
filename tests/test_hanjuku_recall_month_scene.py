"""#2971: a camp recall must not own the month-start scene.

Live frame from g1075-1d5f65e9 (2026-10-29 JST):
- g1075-month-scene-during-recall.png  13:02:25, 「21ねん 10のつき」「こんげつは……」
  (phase 'concert'). The recall had just chosen パタゴーヌ at 13:02:19
  (stage 'dest'); before v286 it owned this text and pressed nothing for 91
  observations, until camp_recall_aborted at 13:04:39.
"""
from pathlib import Path

import pytest

from docich.hanjuku.orchestrator import decide, pad
from docich.hanjuku.os_layer.pixels import read_png
from docich.hanjuku.os_layer.phase import classify

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'tests' / 'fixtures' / 'hanjuku'
SCENE = FIXTURES / 'g1075-month-scene-during-recall.png'
MONTH_MENU = FIXTURES / 'g1067-chapterless-month-menu.png'


def recall_state(**recall):
    return {'screen_kind': 'unknown',
            'policy': {'chapter': 3, 'tick': 70400,
                       'recall': {'target': [414, 496], 'steps': 7, **recall}}}


def decisions(state):
    return [r.get('decision') for r in state.get('_records', [])]


def test_the_saved_frame_is_the_month_scene():
    assert classify(read_png(SCENE)) == 'concert'


@pytest.mark.parametrize('stage', ['to_camp', 'menu', 'dest'])
def test_a_recall_before_dispatch_yields_the_scene(stage):
    actions, state = decide(read_png(SCENE), recall_state(stage=stage))
    assert actions == [pad('a')]
    assert 'camp_recall_interrupted' in decisions(state)
    record = next(r for r in state['_records'] if r.get('decision') == 'camp_recall_interrupted')
    assert record['observed_metric'] == {'stage': stage, 'steps': 7}
    assert 'recall' not in state['policy']
    assert state['policy']['uncertain'] is True


def test_the_scene_advances_on_every_later_frame():
    frame = read_png(SCENE)
    actions, state = decide(frame, recall_state(stage='dest'))
    for _ in range(3):
        actions, state = decide(frame, state)
        assert actions == [pad('a')]
        assert 'camp_recall_interrupted' not in decisions(state)


def test_a_dispatched_recall_waits_out_the_scene_and_ends_on_the_month_screen():
    # The recall A was already sent: the scene advances with A and the dispatch
    # wait is settled (unconfirmed, finitely) on the next screen, as before v286
    # it was settled on the scene itself.
    actions, state = decide(read_png(SCENE), recall_state(stage='await_dispatch'))
    assert actions == [pad('a')]
    assert 'camp_recall_interrupted' not in decisions(state)
    assert state['policy']['recall']['stage'] == 'await_dispatch'
    _, state = decide(read_png(MONTH_MENU), state)
    assert 'camp_recall_unconfirmed' in decisions(state)
    assert 'recall' not in state['policy']


@pytest.mark.parametrize('flag', ['retreat_recheck', 'anonymous_recheck'])
def test_a_recheck_recall_is_left_to_its_own_flow(flag):
    # The recheck validates its own identity and scope (this minimal state is
    # discarded there); the month-scene interruption must not claim it.
    _, state = decide(read_png(SCENE), recall_state(stage='dest', **{flag: True}))
    assert 'camp_recall_interrupted' not in decisions(state)
