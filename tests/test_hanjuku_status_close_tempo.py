"""An early B-return must prove the old map, never act on its background list."""
from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from docich.hanjuku.flows import post_month_status as post
from docich.hanjuku.os_layer.commands import pad, status_panel_feedback
from docich.hanjuku.os_layer.pixels import Frame, read_png
from docich.hanjuku.os_layer import status_return
from docich.hanjuku.os_layer.screen import Screen, parse
from docich.hanjuku_bot import decide
from test_hanjuku_house import Canvas
from test_hanjuku_menu_cursor_tempo import entry
from test_hanjuku_post_month_status import memory


FIXTURE = Path(__file__).parent / 'fixtures/hanjuku/g1163-roster-marker-shirakawa.png'


def status_frame(name='ヴィーナス', *, background_roster=False):
    c = Canvas()
    c.text(56, 7, '8わ14ねん11のつき410G')
    for x, y, text in [(24, 39, name), (88, 39, 'しょうぐん'), (24, 55, 'HP'),
                       (64, 55, '85/85P'), (24, 119, 'たまご'),
                       (56, 119, 'エラベルエッグ4'), (24, 151, 'しろのなかにいます')]:
        c.text(x, y, text)
    if background_roster:
        for index, general in enumerate(('どうし', 'ヴィーナス', 'フェデリーニ', 'エピィ')):
            c.text(168, 39 + 16 * index, general)
        c.hand(146, 49)
        c.text(24, 183, 'Aボタンでステータスひょうじ')
        c.text(24, 199, 'コマンドにもどりますじゃ!')
    return c.frame()


def close_status():
    frame = read_png(FIXTURE).resized()
    mem = memory(chapter=8, month='14-11', gold=410)
    post.arm(mem)
    mem['post_month_status'].update(stage='roster', top_confirmed=True, highlight='ヴィーナス')
    actions, state = decide(frame, {'policy': mem})
    assert actions == [pad('a')]
    panel = status_frame()
    actions, state = decide(panel, state)
    assert actions == [pad('b')]
    return frame, panel, state, actions


def test_owned_status_close_gets_one_early_look_then_requires_the_actual_roster_map():
    frame, _, state, actions = close_status()
    assert state['status_panel_feedback']['transition'] == 'general_status_close'
    assert entry.observation_interval_ms(state, actions) == 500
    before = deepcopy(state)
    assert status_panel_feedback(state, actions)
    assert state == before
    actions, state = decide(frame, state)
    assert actions == [pad('down')]
    assert state['status_panel_feedback'] is None
    owner = state['policy']['post_month_status']
    assert owner['stage'] == 'roster'
    assert not owner.get('close_probe_pending') and not owner.get('status_return')


@pytest.mark.parametrize('early', ['old_status', 'background_roster', 'fade', 'other_general', 'map_change'])
def test_an_early_unclosed_or_different_screen_never_moves_or_resends_b(early):
    frame, panel, state, _ = close_status()
    if early == 'background_roster':
        panel = status_frame(background_roster=True)
        assert post.house.roster(parse(panel))
    elif early == 'fade':
        panel = Canvas((0, 0, 0)).frame()
    elif early == 'other_general':
        panel = read_png(FIXTURE.with_name('g1163-roster-scroll-arrow-and-blended-marker.png')).resized()
    elif early == 'map_change':
        data = bytearray(frame.rgb)
        data[(30 * 256 + 20) * 3] ^= 255
        panel = Frame(256, 224, bytes(data))
    actions, state = decide(panel, state)
    assert actions == []
    assert state['status_panel_feedback'] is None
    assert entry.observation_interval_ms(state, actions) == 1500
    assert not state['policy']['post_month_status'].get('close_probe_pending')
    # A failed early look is a single hold, not an unbounded new owner.
    actions, state = decide(frame, state)
    assert actions == [pad('down')]


def test_changes_outside_the_left_map_do_not_needlessly_delay_the_return():
    frame, _, state, _ = close_status()
    data = bytearray(frame.rgb)
    data[-1] ^= 255  # outer border; the map and actual selected row are unchanged
    actions, _ = decide(Frame(256, 224, bytes(data)), state)
    assert actions == [pad('down')]


def test_a_background_list_and_blank_map_never_supply_a_return_proof():
    frame = status_frame(background_roster=True)
    assert post.house.roster(parse(frame))
    assert status_return.capture(parse(frame), frame) is None
    blank = Canvas((0, 0, 0)).frame()
    assert status_return.capture(parse(blank), blank) is None


@pytest.mark.parametrize('frame', [None, Frame(1, 1, bytes(3)), Frame(256, 224, b''),
                                  Frame(256.0, 224, bytes(256 * 224 * 3)),
                                  Frame(256, 224, 'x' * (256 * 224 * 3))])
def test_missing_or_malformed_frame_cannot_supply_a_return_proof(frame):
    screen = parse(read_png(FIXTURE).resized())
    assert status_return.capture(screen, frame) is None


def test_a_failed_probe_counts_one_observation_and_does_not_extend_the_owner_budget():
    _, _, state, _ = close_status()
    before = state['policy']['post_month_status']['steps']
    actions, state = decide(Canvas((0, 0, 0)).frame(), state)
    owner = state['policy']['post_month_status']
    assert actions == [] and owner['steps'] == before + 1
    assert owner['phase_reads'] == 1 and not owner.get('close_probe_pending')


@pytest.mark.parametrize('proof', [None, {}, {'general': 'ヴィーナス', 'sha256': True},
                                  {'general': 'ゼウス', 'sha256': 'f' * 64}])
def test_corrupt_or_foreign_return_proof_cannot_authorize_an_early_move(proof):
    frame, _, state, _ = close_status()
    owner = state['policy']['post_month_status']
    owner['status_return'] = proof
    owner['close_probe_pending'] = True
    actions, state = decide(frame, state)
    assert actions == []
    assert entry.observation_interval_ms(state, actions) == 1500


def test_battle_preemption_is_immediate_and_resume_discards_the_old_return_proof():
    _, _, state, _ = close_status()
    mem = state['policy']
    assert post.step(Screen([], None, '', kind='battle'), mem, None) is None
    post.resume_after_battle(mem, {'ally': 'ヴィーナス'})
    owner = mem['post_month_status']
    assert owner['stage'] == 'await_map'
    assert not owner.get('status_return') and not owner.get('close_probe_pending')


@pytest.mark.parametrize('change', ['month', 'identity'])
def test_a_different_owner_scope_does_not_reuse_the_return_hash(change):
    frame, _, state, _ = close_status()
    mem = state['policy']
    if change == 'month':
        mem['month'] = '14-12'
    else:
        mem['_run_identity'] = {'game': 'hanjuku-hero', 'runtime_id': 'other-test',
                                'generation': 2, 'lease_id': 'other-test-lease'}
    assert post.step(parse(frame), mem, frame) is None
    assert 'post_month_status' not in mem


@pytest.mark.parametrize('actions', [
    [pad('a')], [pad('b'), pad('b')], [pad('b'), {'type': 'wait', 'ms': 50}],
    [{**pad('b'), 'hold_ms': 200}], [{**pad('b'), 'hold_ms': 100.0}],
])
def test_status_close_feedback_cannot_speed_other_inputs(actions):
    _, _, state, _ = close_status()
    assert not status_panel_feedback(state, actions)


@pytest.mark.parametrize('field,value', [('step', True), ('step', -1), ('kind', 'unknown'),
                                        ('transition', 'purchase')])
def test_close_feedback_cannot_reuse_invalid_step_screen_or_transition(field, value):
    _, _, state, actions = close_status()
    state['status_panel_feedback'][field] = value
    assert not status_panel_feedback(state, actions)


def test_status_close_action_plan_records_the_actual_panel_and_fixed_transition(tmp_path):
    _, panel, state, actions = close_status()
    with patch('docich.hanjuku_scene.observe'), patch('docich.hanjuku_progress.record'):
        entry.persist(tmp_path, state, [], {'hanjuku': {
            'game': 'hanjuku-hero', 'runtime_id': 'test-close-tempo',
            'generation': 1, 'lease_id': 'test-lease'}},
            actions=actions, frame_sha256=panel.digest(), frame=panel)
    rows = [json.loads(row) for row in (tmp_path / 'hanjuku_decisions.jsonl').read_text().splitlines()]
    plan = next(row for row in rows if row['event'] == 'action_plan')
    assert plan['status_panel_feedback']['transition'] == 'general_status_close'
    assert plan['planned_actions'] == [pad('b')]
    assert plan['observation_interval_ms'] == 500
    assert plan['dispatch_status'] == 'planned_not_yet_sent'
    assert read_png(tmp_path / 'hanjuku_frames' / plan['snapshot']).digest() == panel.digest()


@pytest.mark.parametrize('matched', [True, False])
def test_probe_outcome_and_its_frame_are_saved_even_without_an_input(tmp_path, matched):
    frame, _, state, _ = close_status()
    if not matched:
        frame = status_frame(background_roster=True)
    actions, state = decide(frame, state)
    records = state.pop('_records', [])
    probe = next(row for row in records if row['decision'] == 'post_month_status_close_probe')
    assert probe['observed_metric']['map_matched'] is matched
    assert set(probe['observed_metric']) == {'map_matched', 'screen_kind', 'observation_tick'}
    with patch('docich.hanjuku_scene.observe'), patch('docich.hanjuku_progress.record'):
        entry.persist(tmp_path, state, records, {'hanjuku': {
            'game': 'hanjuku-hero', 'runtime_id': 'test-probe-result',
            'generation': 1, 'lease_id': 'test-lease'}},
            actions=actions, frame_sha256=frame.digest(), frame=frame)
    rows = [json.loads(row) for row in (tmp_path / 'hanjuku_decisions.jsonl').read_text().splitlines()]
    plan = next(row for row in rows if row['event'] == 'action_plan')
    assert 'post_month_status_close_probe' in plan['reason_decisions']
    assert read_png(tmp_path / 'hanjuku_frames' / plan['snapshot']).digest() == frame.digest()


def test_repeated_same_person_probe_results_are_not_deduplicated_across_observations(tmp_path):
    frame = read_png(FIXTURE).resized()
    screen = parse(frame)
    for tick in (42, 43):
        mem = memory(chapter=8, month='14-11', tick=tick)
        post.arm(mem)
        mem['post_month_status'].update(stage='roster_next', selected='ヴィーナス',
                                       status_return=status_return.capture(screen, frame),
                                       close_probe_pending=True)
        actions = post.step(screen, mem, frame)
        state = {'policy': mem, 'step': tick, 'screen_kind': 'text'}
        with patch('docich.hanjuku_scene.observe'), patch('docich.hanjuku_progress.record'):
            entry.persist(tmp_path, state, mem.pop('_records', []), {'hanjuku': {
                'game': 'hanjuku-hero', 'runtime_id': 'test-repeated-probe',
                'generation': 1, 'lease_id': 'test-lease'}},
                actions=actions, frame_sha256=frame.digest(), frame=frame)
    rows = [json.loads(row) for row in (tmp_path / 'hanjuku_decisions.jsonl').read_text().splitlines()]
    probes = [row for row in rows if row.get('decision') == 'post_month_status_close_probe']
    assert len(probes) == 2
    assert [row['observed_metric']['observation_tick'] for row in probes] == [42, 43]
