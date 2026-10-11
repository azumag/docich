"""Starter parse/decide/persist/send evidence; synthetic glyphs, no arrivals."""
import json
from types import SimpleNamespace

import pytest

from docich import hanjuku_run
from docich.game_switch import atomic_write_json
from docich.hanjuku_bot import decide
from docich.hanjuku_pixels import read_png
from test_hanjuku_camp_receipts import command_bot, ID
from test_hanjuku_starter import picture, state


def records(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


@pytest.mark.parametrize('selected,confirm,on,expected', [
    ((), False, 'ゼウス', 'starter_select'),
    (('ゼウス', 'ヴィーナス', 'ココット'), True, None, 'starter_confirm'),
    (('ゼウス',), True, 'いかんッ!', 'starter_underfilled_rejected'),
])
def test_actual_starter_decisions_link_png_plan_and_sender_without_arrival(tmp_path, selected, confirm, on, expected):
    frame = picture(selected=selected, confirm=confirm, on=on).frame()
    actions, got = decide(frame, state(active='old-sortie', battle={'step': 'old-battle'}))
    raw = got.pop('_records')
    assert raw[-1]['decision'] == expected
    entry = command_bot()
    entry.persist(tmp_path, got, raw, {'hanjuku': ID}, actions=actions,
                  frame_sha256=frame.digest(), frame=frame)
    journal = records(tmp_path/'hanjuku_decisions.jsonl'); plan = journal[0]
    assert plan['chart_step'] == 'starter'
    assert plan['dispatch_status'] == 'planned_not_yet_sent'
    assert plan['planned_actions'] == actions
    assert plan['snapshot']
    assert read_png(tmp_path/'hanjuku_frames'/plan['snapshot']).digest() == frame.digest()
    assert all(row['snapshot'] == plan['snapshot'] and row['decision_id'] == plan['decision_id']
               and row['frame_sha256'] == frame.digest() for row in journal)
    assert not (tmp_path/'hanjuku_events.jsonl').exists()
    atomic_write_json(tmp_path/'hanjuku_run.json', {**ID, 'frame_sha256': frame.digest()})
    atomic_write_json(tmp_path/'hanjuku_bot.json', got)
    hanjuku_run.action_sent(tmp_path, ID, SimpleNamespace(type='pad', buttons=['a'], hold_ms=100))
    sent = records(tmp_path/'hanjuku_events.jsonl')[-1]
    assert sent['decision_id'] == plan['decision_id']
    assert sent['decision_frame_sha256'] == frame.digest()
    assert not got['policy'].get('garrison') and not got['policy'].get('sorties')
    assert not any(row.get('decision') == 'starter_departed_observed' for row in journal)


def test_starter_unread_hold_keeps_exact_png_without_dispatch(tmp_path):
    frame = picture(hand=False).frame()
    actions, got = decide(frame, state())
    assert actions == []
    # Force a partial foreground after a real selection episode, not a fake chosen count.
    partial = picture(prompt=False, hand=False).frame()
    actions, got = decide(partial, json.loads(json.dumps(got)))
    assert actions == []
    raw = got.pop('_records'); assert raw[-1]['decision'] == 'starter_unread'
    command_bot().persist(tmp_path, got, raw, {'hanjuku': ID}, actions=actions,
                          frame_sha256=partial.digest(), frame=partial)
    plan = records(tmp_path/'hanjuku_decisions.jsonl')[0]
    assert plan['chart_step'] == 'starter' and plan['snapshot']
    assert read_png(tmp_path/'hanjuku_frames'/plan['snapshot']).digest() == partial.digest()
    assert not (tmp_path/'hanjuku_events.jsonl').exists()


@pytest.mark.parametrize('available,confirm,button', [
    (('ココット', 'ゼウス', 'ヴィーナス'), False, 'down'),
    (('ヴィーナス', 'ココット', 'ゼウス'), False, 'down'),
    (('ゼウス', 'ヴィーナス', 'ココット'), True, 'down'),
    (('ココット', 'ヴィーナス', 'ゼウス'), True, 'down'),
])
def test_navigation_receipt_uses_starter_context_on_every_json_roundtrip(tmp_path, monkeypatch,
                                                                        available, confirm, button):
    from unittest.mock import Mock
    from docich.adapters.retroarch import RetroArchAdapter, DEFAULT_BUTTONS
    from docich.actions import parse_actions
    frame = picture(available=available, confirm=confirm).frame()
    got = state(active='old-sortie', battle={'step': 'old-battle', 'ally': 'old-ally',
                                          'ally_hp': 80, 'enemy_hp': 0, 'strategy_expected': {'win': True}})
    entry = command_bot()
    for i in range(2):
        got = json.loads(json.dumps(got))
        actions, got = decide(frame, got)
        raw = got.pop('_records')
        entry.persist(tmp_path, got, raw, {'hanjuku': ID}, actions=actions,
                      frame_sha256=frame.digest(), frame=frame)
        plan = [r for r in records(tmp_path/'hanjuku_decisions.jsonl') if r['event']=='action_plan'][-1]
        assert plan['chart_step'] == 'starter'
        assert plan['snapshot'] and read_png(tmp_path/'hanjuku_frames'/plan['snapshot']).digest() == frame.digest()
        assert plan['expected_metric'] is None
        assert raw[-1]['decision'] == 'starter_navigation'
        assert raw[-1]['observed_metric'] == {'selected': []}
        assert plan['planned_actions'][0]['buttons'] == [button]
        assert plan['dispatch_status'] == 'planned_not_yet_sent'
        if i:
            assert len(records(tmp_path/'hanjuku_events.jsonl')) == i
        else:
            assert not (tmp_path/'hanjuku_events.jsonl').exists()
        atomic_write_json(tmp_path/'hanjuku_run.json', {**ID, 'frame_sha256': frame.digest()})
        atomic_write_json(tmp_path/'hanjuku_bot.json', got)
        kit = Mock(); kit.find_window.return_value = '42'
        ctx = SimpleNamespace(game=SimpleNamespace(name='hanjuku-hero', raw={'hanjuku':{'script_bot':True}}),
                              fence=SimpleNamespace(**ID), xkit=kit)
        adapter = RetroArchAdapter(ctx)
        monkeypatch.setattr(adapter, '_check_fence', lambda: None)
        monkeypatch.setattr(adapter, '_runtime_dir', lambda: tmp_path)
        monkeypatch.setattr(adapter, '_source', lambda: (kit, None))
        monkeypatch.setattr(adapter, '_resolved_buttons', lambda: DEFAULT_BUTTONS)
        adapter.act(parse_actions(actions)[0])
        kit.tap.assert_called_once()
        sent = records(tmp_path/'hanjuku_events.jsonl')[-1]
        assert sent['decision_id'] == plan['decision_id'] and sent['decision_frame_sha256'] == frame.digest()
        assert not got['policy'].get('garrison') and not got['policy'].get('sorties')
