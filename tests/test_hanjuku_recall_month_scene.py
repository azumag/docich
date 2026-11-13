"""#2971: a camp recall must not own the month-start scene.

Live frame from g1075-1d5f65e9 (2026-10-29 JST):
- g1075-month-scene-during-recall.png  13:02:25, 「21ねん 10のつき」「こんげつは……」
  (phase 'concert'). The recall had just chosen パタゴーヌ at 13:02:19
  (stage 'dest'); before v286 it owned this text and pressed nothing for 91
  observations, until camp_recall_aborted at 13:04:39.
"""
from pathlib import Path

import pytest

from docich import hanjuku_policy as p
from docich.hanjuku.orchestrator import decide, pad
from docich.hanjuku.flows import recall as recall_flow
from docich.hanjuku.os_layer.pixels import read_png
from docich.hanjuku.os_layer.phase import classify
from docich.hanjuku.os_layer.screen import Screen
from docich.hanjuku.state.regions import flow as _flow, journal as _journal
from test_hanjuku_chart_bot import camp_menu

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'tests' / 'fixtures' / 'hanjuku'
SCENE = FIXTURES / 'g1075-month-scene-during-recall.png'
MONTH_MENU = FIXTURES / 'g1067-chapterless-month-menu.png'
# Live map frame with one own tent in view; measured parse: kind 'map',
# cursor (134, 115), camp target (141, 128).
FIELD_CAMP = FIXTURES / 'g1075-ch4-camp-thin-flag.png'


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
    assert record['observed_metric'] == {'stage': stage, 'steps': 7, 'goal': None,
                                         'target': [414, 496], 'dest_target': None}
    # v693: the intent survives the scene (it walks back to the camp once the
    # scene is over) instead of being dropped and re-found from scratch.
    kept = state['policy']['flow']['recall']
    assert kept['stage'] == 'to_camp' and kept['scene_pending'] is True
    assert state['policy']['uncertain'] is True


def test_the_dropped_recall_leaves_no_interruption_marker():
    # #3544: a stale recall_interrupted marker would shorten the retry hold of
    # an unrelated later recall's unconfirmed terminal.
    state = recall_state(stage='dest')
    state['policy']['flow'] = {'recall_interrupted': 70400}
    _, state = decide(read_png(SCENE), state)
    assert 'camp_recall_interrupted' in decisions(state)
    assert 'recall_interrupted' not in (state['policy'].get('flow') or {})


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
    assert state['policy']['flow']['recall']['stage'] == 'await_dispatch'
    _, state = decide(read_png(MONTH_MENU), state)
    assert 'camp_recall_unconfirmed' in decisions(state)
    assert 'recall' not in state['policy']


@pytest.mark.parametrize('flag', ['retreat_recheck', 'anonymous_recheck'])
def test_a_recheck_recall_is_left_to_its_own_flow(flag):
    # The recheck validates its own identity and scope (this minimal state is
    # discarded there); the month-scene interruption must not claim it.
    _, state = decide(read_png(SCENE), recall_state(stage='dest', **{flag: True}))
    assert 'camp_recall_interrupted' not in decisions(state)


def test_the_interrupted_recall_resumes_and_reaches_a_destination(monkeypatch):
    """#3978 acceptance (1): the full post-scene transition.

    月初場面（concert）→ 通常画面（map）→ 野営再到達（to_camp → menu）→
    帰還先決定（dest → await_dispatch）をひと続きで検証する。v693 では中断
    直後の状態保持だけを見ていた; g1163 の障害（1時間6回試行・0回成功）は
    「中断後に帰還が成立しない」ことが問題だったため、帰還先決定（await_dispatch 到達）までを通す。
    """
    # 1. 月初場面が帰還先選択中（dest 段）の帰還を切る: 入力せず場面をAで進め、
    #    帰還意図は to_camp + scene_pending として保持される（v693 の既存検証）。
    actions, state = decide(read_png(SCENE), recall_state(stage='dest'))
    assert actions == [pad('a')]
    mem = state['policy']
    kept = _flow(mem)['recall']
    assert kept['stage'] == 'to_camp' and kept['scene_pending'] is True
    assert 'camp_recall_interrupted' in decisions(state)

    # 2. 通常画面へ戻ると保留は1回分だけ解け、to_camp 段がマップ上の野営を毎
    #    フレーム再検出して実カーソルから歩き直す。実フィクスチャの実測値は
    #    cursor (134,115) / tent (141,128) で、|dy|=13 が主軸のため down の1押し。
    frame = read_png(FIELD_CAMP)
    actions, state = decide(frame, state)
    mem = state['policy']
    recall = _flow(mem)['recall']
    assert 'scene_pending' not in recall           # 場面は重ねて数えない
    assert recall['stage'] == 'to_camp' and recall['last_seen'] == [141, 128]
    assert actions[0]['buttons'] == ['down']    # 実カーソルとの差分の1押し
    assert 'camp_recall_interrupted' not in decisions(state)

    # 3. 野営再到達: 実カーソルが野営に乗った map で決定し、きかんメニュー段へ。
    #    以降は実機画面の無い段を合成 Screen で遷移させる（chapter9 redirect
    #    テストと同じ手法: 野営検出を observed_camps 引数で pin する）。
    camp = {'target': (141, 128), 'clipped': False}
    on_camp = Screen([], None, '', kind='map', cursor=(140, 127))
    assert recall_flow._camp_recall_step(on_camp, mem, frame, observed_camps=[camp]) == [pad('a')]
    assert _flow(mem)['recall']['stage'] == 'menu'
    assert mem['_records'][-1]['decision'] == 'camp_enter'

    # 4. 野営メニュー: 実カーソル位置を毎回観測して「きかん」（最下行）へ1押し
    #    ずつ合わせ、乗った所でAして帰還先ピッカーを開く。
    for selected in range(3):
        assert recall_flow._camp_recall_step(camp_menu(selected), mem, frame) == [pad('down')]
    assert recall_flow._camp_recall_step(camp_menu(3), mem, frame) == [pad('a')]
    assert _flow(mem)['recall']['stage'] == 'dest'

    # 5. 帰還先決定: ピッカーのマーカーが自軍城の屋根に乗った map_target でAし、
    #    帰還要求が未送信のまま await_dispatch へ進む（送信・到着は別契約）。
    monkeypatch.setattr(p, 'castle_roofs',
                        lambda _f, exclude=None: [{'kind': 'own', 'target': (163, 115),
                                                   'clipped': False}])
    target = Screen([], None, '', kind='map_target', marker=(163, 115))
    assert recall_flow._camp_recall_step(target, mem, frame) == [pad('a')]
    done = _flow(mem)['recall']
    assert done['stage'] == 'await_dispatch' and done['target_cell'] == [163, 115]
    assert mem['_records'][-1]['decision'] == 'camp_recall_requested'
