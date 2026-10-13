"""Run persistence/latching and the existing save boundary, without a live ROM."""
import hashlib
import json
import time
from types import SimpleNamespace

import pytest

from docich import hanjuku_repetition as loop, hanjuku_run as run, game_switch
from docich.adapters.base import AdapterError

IDENTITY = {'game': 'hanjuku-hero', 'runtime_id': 'g1-abcdef',
            'generation': 1, 'lease_id': 'lease'}
ACTION = SimpleNamespace(type='pad', buttons=['a'], hold_ms=100)


class ObservedFrame:
    def __init__(self, value, phase='month_menu'):
        self.value, self.phase = value, phase

    def digest(self):
        return hashlib.sha256(str(self.value).encode()).hexdigest()

    def png_bytes(self):
        # The observer contract is tested here, not the separate PNG codec.
        return b'test-frame'


def induce_loop(path, monkeypatch, identity=IDENTITY, period=3):
    monkeypatch.setattr(run, 'classify', lambda frame: frame.phase)
    for n in range(500):
        now = 100 + n * 2.5
        state = run.observe(path, identity, ObservedFrame(n % period),
                            now=now, wall=10_000 + now)
        if state['terminal_reason']:
            return state
        run.action_sent(path, identity, ACTION, now=now + .1)
    pytest.fail('cycle did not terminate')


@pytest.mark.parametrize('period', [3, 4, 8, 24])
def test_loop_latches_existing_stall_with_distinct_durable_evidence(tmp_path, monkeypatch, period):
    state = induce_loop(tmp_path, monkeypatch, period=period)
    assert state['terminal_reason'] == 'screen_stalled'
    assert state['terminal_evidence'] == loop.EVIDENCE
    assert state['frame_unchanged_seconds'] == 0
    assert state['unchanged_seconds'] >= 300
    assert state['repetition_evidence']['period'] == period
    assert run.terminal(tmp_path, IDENTITY) == state
    assert (tmp_path / run.RUN_FILE).stat().st_size < 16 * 1024
    entries = [json.loads(line) for line in (tmp_path / 'hanjuku_events.jsonl').read_text().splitlines()]
    assert entries[-1]['repetition_evidence'] == state['repetition_evidence']
    assert (tmp_path / 'hanjuku_frames' / entries[-1]['snapshot']).exists()
    # New frames cannot unlatch a terminal run or add another terminal event.
    assert run.observe(tmp_path, IDENTITY, ObservedFrame('new'), now=2000, wall=12000) == state


def test_continuing_visual_progress_and_same_button_do_not_stop(tmp_path, monkeypatch):
    monkeypatch.setattr(run, 'classify', lambda _: 'month_menu')
    for n in range(500):
        now = 100 + n * 2.5
        state = run.observe(tmp_path, IDENTITY, ObservedFrame(n), now=now, wall=10_000 + now)
        assert state['terminal_reason'] is None
        run.action_sent(tmp_path, IDENTITY, ACTION, now=now + .1)


def test_confirmed_battle_boundaries_reset_cycle_history(tmp_path, monkeypatch):
    monkeypatch.setattr(run, 'classify', lambda frame: frame.phase)
    for n in range(500):
        now = 100 + n * 2.5
        phase = 'battle' if n % 5 < 2 else 'month_menu'
        state = run.observe(tmp_path, IDENTITY, ObservedFrame(n % 5, phase), now=now, wall=10_000 + now)
        assert state['terminal_reason'] is None
        run.action_sent(tmp_path, IDENTITY, ACTION, now=now + .1)
    assert state['battles_finished'] > 0


@pytest.mark.parametrize('change', ['missing_history', 'changed_summary', 'wrong_duration', 'paused'])
def test_invalid_loop_proof_never_authorizes_terminal(tmp_path, monkeypatch, change):
    state = induce_loop(tmp_path, monkeypatch)
    if change == 'missing_history':
        state.pop('repetition')
    elif change == 'changed_summary':
        state['repetition_evidence']['cycles'] += 1
    elif change == 'wrong_duration':
        state['unchanged_seconds'] += 1
    else:
        state['playing'] = False
    game_switch.atomic_write_json(tmp_path / run.RUN_FILE, state)
    with pytest.raises(AdapterError, match='terminal evidence'):
        run.terminal(tmp_path, IDENTITY)


def test_cycle_never_crosses_runtime_identity(tmp_path, monkeypatch):
    induce_loop(tmp_path, monkeypatch)
    with pytest.raises(AdapterError, match='identity mismatch'):
        run.terminal(tmp_path, {**IDENTITY, 'lease_id': 'different-lease'})


def test_legacy_static_stall_contract_is_unchanged(tmp_path, monkeypatch):
    monkeypatch.setattr(run, 'classify', lambda _: 'month_menu')
    for now in range(100, 401, 10):
        state = run.observe(tmp_path, IDENTITY, ObservedFrame(0), now=now, wall=10_000 + now)
    assert state['terminal_reason'] == 'screen_stalled'
    assert state['terminal_evidence'] is None
    assert state['unchanged_seconds'] == state['frame_unchanged_seconds'] == 300
    assert run.terminal(tmp_path, IDENTITY) == state
